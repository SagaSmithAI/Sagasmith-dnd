from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_sphere_contact_annihilates_actor_atomically_and_replays(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.sphere_of_annihilation"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    content = (
        "# Crypt\n\n## Sphere of Annihilation\n\n"
        "A black sphere enters the stone mouth and is annihilated.\n"
        f"trap_profile: {marker}\n"
    )
    source = tmp_path / "sphere.md"
    source.write_text(content, encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        module_import_roots=(tmp_path,),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Sphere contact",
                    "edition": "2014",
                    "random_seed": "sphere-contact",
                    "idempotency_key": "campaign",
                },
            )
            campaign_id = campaign["id"]
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "idempotency_key": "draft",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "sphere-source",
                        "title": "Sphere source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="sphere-source",
                title="Sphere source",
                portable_id="dnd5e.module.sphere-contact-test",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Sphere Annihilation", "top_k": 3},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = json.dumps(expanded["source_ref"], sort_keys=True, separators=(",", ":"))
            await _call(
                server,
                "module_set_progress",
                {
                    "campaign_id": campaign_id,
                    "scene_id": expanded["scene"]["id"],
                    "status": "current",
                    "expected_state_version": 0,
                    "idempotency_key": "set-current-scene",
                },
            )

            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["inventory"]["wallet"]["gp"] = 25
            sheet["inventory"]["items"] = [
                {
                    "id": "attuned-ring",
                    "name": "Attuned Ring",
                    "kind": "equipment",
                    "quantity": 1,
                    "weight_oz": 1,
                    "attunement": "attuned",
                }
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Scout", "sheet": sheet},
                    "idempotency_key": "actor",
                },
            )
            before_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            before_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )

            def request(facts: dict, key: str) -> dict:
                return {
                    "campaign_id": campaign_id,
                    "trap_id": "sphere-1",
                    "action": "contact",
                    "source_ref": source_ref,
                    "source_excerpt": expanded["content"],
                    "profile": profile,
                    "actor_id": actor["id"],
                    "contact_facts": facts,
                    "expected_revision": before_campaign["revision"],
                    "idempotency_key": key,
                }

            facts = {
                "decision_id": "sphere-contact-1",
                "reason": "The target entered the stone mouth.",
                "scene_id": expanded["scene"]["id"],
                "trap_id": "sphere-1",
                "target_actor_id": actor["id"],
                "target_actor_revision": before_actor["revision"],
                "source_ref": source_ref,
                "campaign_revision": before_campaign["revision"],
                "reviewed_by": "system:local",
                "enters_mouth": True,
            }
            with pytest.raises(ToolError, match="authenticated campaign DM"):
                await _call(
                    server,
                    "trap_state_transition",
                    request({**facts, "reviewed_by": "player:forged"}, "forged-review"),
                )
            with pytest.raises(ToolError, match="actor revision is stale"):
                await _call(
                    server,
                    "trap_state_transition",
                    request(
                        {**facts, "target_actor_revision": before_actor["revision"] + 1},
                        "stale-actor",
                    ),
                )
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert unchanged_campaign["revision"] == before_campaign["revision"]
            assert unchanged_actor["revision"] == before_actor["revision"]

            args = request(facts, "valid-contact")
            result = await _call(server, "trap_state_transition", args)
            assert result["annihilated"] is True
            assert result["body_state"] == "annihilated"
            assert await _call(server, "trap_state_transition", args) == result
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert after["revision"] == before_actor["revision"] + 1
            assert after["sheet"]["body_state"] == "annihilated"
            assert after["sheet"]["conditions"] == ["dead"]
            assert after["sheet"]["inventory"]["wallet"]["gp"] == 0
            assert after["sheet"]["inventory"]["items"] == []
            assert all(
                value is None for value in after["sheet"]["inventory"]["equipment_slots"].values()
            )

            with pytest.raises(ToolError, match="annihilated character sheet cannot be modified"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": actor["id"],
                        "patch": {
                            "combat": {"hp": {"value": 1}},
                            "inventory": {
                                "items": [
                                    {
                                        "id": "restored-kit",
                                        "name": "Restored Kit",
                                        "kind": "equipment",
                                        "quantity": 1,
                                        "weight_oz": 1,
                                        "attunement": "none",
                                        "equipped": True,
                                        "equipped_slot": "main_hand",
                                    }
                                ],
                                "equipment_slots": {"main_hand": "restored-kit"},
                            },
                        },
                        "expected_revision": after["revision"],
                        "idempotency_key": "annihilated-sheet-restore-attempt",
                    },
                )
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert unchanged_actor["revision"] == after["revision"]
            assert unchanged_actor["sheet"]["body_state"] == "annihilated"
            assert unchanged_actor["sheet"]["combat"]["hp"]["value"] == 0
            assert unchanged_actor["sheet"]["inventory"]["items"] == []
            assert all(
                value is None
                for value in unchanged_actor["sheet"]["inventory"]["equipment_slots"].values()
            )

            close_server(server)
            server = create_server(config)
            replayed = await _call(server, "trap_state_transition", args)
            assert replayed == result
            persisted = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert persisted["revision"] == after["revision"]
            assert persisted["sheet"]["body_state"] == "annihilated"
        finally:
            close_server(server)

    asyncio.run(exercise())
