from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import validate
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.operations import RequestIdentity
from sagasmith_dnd_runtime.result_contracts import tool_output_schema

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


async def call(server, name, arguments):
    result = await server.runtime.execute(
        name, arguments, context=RequestIdentity("system:local"),
    )
    if isinstance(result, dict) and "action" in result:
        return result.get("result", result)
    return result


@pytest.mark.parametrize("conditional_trait", [False, True])
def test_scene_hazard_save_validates_evidence_before_draw_and_replays(
    tmp_path: Path, conditional_trait: bool,
):
    condition = "frightened" if conditional_trait else "restrained"
    clause = f"The snare requires a DC 10 Wisdom saving throw or the target is {condition}."

    async def exercise():
        server = create_server(McpConfig(
            home=tmp_path / "home", database_url=None, chroma_url=None,
            chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
            modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
        ))
        try:
            campaign = await call(server, "campaign_create", {
                "name": "Source-bound hazard", "idempotency_key": "campaign",
            })
            cid = campaign["id"]
            await call(server, "campaign_rules", {
                "campaign_id": cid, "action": "set_profile",
                "payload": {"edition": "2014"},
                "expected_revision": campaign["revision"], "idempotency_key": "edition",
            })
            staged = await call(server, "module_draft", {
                "campaign_id": cid, "action": "start", "payload": {
                    "name": "snare.md", "source_key": "snare", "title": "Snare",
                    "content": "# Trail\n\n## Snare\n\n" + clause,
                }, "idempotency_key": "stage",
            })
            await finalize_and_activate_module(
                call, server, cid, staged, source_key="snare", title="Snare",
                portable_id="dnd5e.module.snare",
            )
            hits = await call(server, "module_search", {
                "campaign_id": cid, "query": "snare Wisdom", "top_k": 3,
            })
            expanded = await call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            if conditional_trait:
                sheet["content"]["features"] = [{
                    "id": "species-halfling-brave", "name": "Brave",
                    "mechanic_refs": ["dnd5e.core.save.halfling_brave"],
                    "choices": {"source_trait": {
                        "kind": "halfling_brave", "automatic": True,
                        "source_excerpt": "Advantage on saving throws against being frightened.",
                    }},
                }]
            actor = await call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": cid, "name": "Hero", "sheet": sheet,
                },
                "idempotency_key": "hero",
            })
            queried = await call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            assert queried["id"] == actor["id"]
            with pytest.raises(PermissionError, match="another campaign"):
                await server.runtime.execute("character_query", {
                    "view": "get", "payload": {"character_id": actor["id"]},
                }, context=RequestIdentity("system:local", "other-campaign"))
            current = await call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": cid},
            })
            phase = await call(server, "game_phase", {
                "campaign_id": cid, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            args = {
                "campaign_id": cid, "action": "scene_save",
                "expected_revision": phase["campaign_revision"], "idempotency_key": "save",
                "payload": {
                    "actor_id": actor["id"], "ability": "wisdom", "dc": 10,
                    "source_ref": expanded["source_ref"], "source_excerpt": clause,
                    "reason": "The PC stepped into the reviewed mechanical snare.",
                    "save_source_kind": "nonmagical_effect",
                    "save_effect_conditions": [condition], "save_against_poison": False,
                },
            }
            before = await call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": cid},
            })
            # A player/DM may inspect combat state after returning to Play.
            # Read-only audit access must not force a new combat to be opened.
            await call(server, "combat_query", {"campaign_id": cid, "view": "status"})
            assert await call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": cid},
            }) == before
            # Read-only source discovery stays available during play; Pack
            # mutations retain their Lobby boundary even with a stable catalog.
            await call(server, "content_pack", {
                "action": "list", "payload": {"campaign_id": cid, "kind": "module"},
            })
            with pytest.raises((ToolError, ValueError), match="(?i)lobby"):
                await call(server, "content_pack", {
                    "action": "remove", "payload": {
                        "campaign_id": cid, "kind": "module", "module_id": "unused",
                    }, "expected_revision": before["revision"], "idempotency_key": "remove",
                })
            for patch in (
                {"source_excerpt": "This invented fear trap has no matching source."},
                {"source_ref": {**expanded["source_ref"], "module_id": "other-module"}},
                {"save_effect_conditions": {"failure": "restrained"}},
                {"save_source_kind": "spell"},
                {"save_against_poison": "false"},
            ):
                forged = deepcopy(args)
                forged["payload"].update(patch)
                with pytest.raises((ToolError, ValueError)):
                    await call(server, "character_check", forged)
                assert await call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": cid},
                }) == before
            result = await call(server, "character_check", args)
            validate(result, tool_output_schema("character_check"))
            assert result["status"] == "committed"
            assert result["random_stream_receipt"]["draw_count"] >= 1
            assert result["scene_save_source"]["save_effect_conditions"] == [condition]
            assert result["result"]["roll_mode"] == (
                "advantage" if conditional_trait else "normal"
            )
            assert await call(server, "character_check", args) == result
            after = await call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": cid},
            })
            assert after["revision"] == before["revision"] + 1
            review = after["state"]["resolution_log"][-1]["scene_save_source"]
            assert review["source_excerpt"] == clause
            await call(server, "combat_start", {
                "campaign_id": cid, "participant_ids": [actor["id"]],
                "positioning_mode": "agent",
                "expected_revision": after["revision"], "idempotency_key": "combat",
            })
            await call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            await call(server, "content_pack", {
                "action": "list", "payload": {"campaign_id": cid, "kind": "module"},
            })
        finally:
            close_server(server)

    asyncio.run(exercise())
