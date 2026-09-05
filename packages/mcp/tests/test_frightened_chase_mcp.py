from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_chase_mcp import _call, _config

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


@pytest.mark.parametrize("operation", ["start", "complication", "extra_dash"])
@pytest.mark.parametrize("frightened", [True, False])
def test_public_chase_preserves_fear_rulings_without_partial_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, frightened: bool,
) -> None:
    module_root = tmp_path / "modules"
    module_root.mkdir()
    excerpt = "The runner is 60 feet away when the pursuer begins the street chase."
    source = module_root / "chase.md"
    source.write_text("# Chase Audit\n\n## Street Chase\n\n" + excerpt, encoding="utf-8")
    original_advance = server_module.advance_chase_turn

    def prepared_turn(chase, *args, **kwargs):
        # Exercise the real engine with a deterministic pending complication or
        # the next dash beyond the free allowance, as in existing chase fixtures.
        chase = deepcopy(chase)
        if operation == "complication":
            chase["pending_complication"] = {
                "number": 1, "source_actor_id": chase["quarry_ids"][0], "rolled_round": 1,
            }
        else:
            chase["pending_complication"] = None
            for participant in chase["participants"]:
                if participant["actor_id"] == kwargs["actor_id_value"]:
                    participant["dash_count"] = participant["free_dash_limit"]
        return original_advance(chase, *args, **kwargs)

    if operation != "start":
        monkeypatch.setattr(server_module, "advance_chase_turn", prepared_turn)

    async def exercise() -> None:
        config = _config(tmp_path, module_root)
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Chase fear rulings", "edition": "2014", "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            staged = await _call(server, "module_draft", {
                "campaign_id": campaign_id, "action": "start", "payload": {
                    "source_path": str(source), "source_key": "chase-audit", "title": "Chase Audit",
                }, "idempotency_key": "stage",
            })
            await finalize_and_activate_module(
                _call, server, campaign_id, staged, source_key="chase-audit", title="Chase Audit",
                portable_id="dnd5e.module.chase-audit",
            )
            hits = await _call(server, "module_search", {
                "campaign_id": campaign_id, "query": "runner pursuer street chase",
            })
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = {
                "module_id": expanded["module"]["id"], "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"], "page_start": expanded["page_start"],
                "page_end": expanded["page_end"], "heading_path": expanded["heading_path"],
                "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
            }
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}

            async def create(name: str) -> dict:
                return await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": name, "sheet": sheet,
                    }, "idempotency_key": name,
                })

            fear_source = await create("Absent fear source")
            quarry = await create("Runner")
            if frightened:
                sheet["conditions"] = ["frightened"]
                sheet["effects"] = [{
                    "id": "fear", "name": "Fear", "kind": "timed_conditions", "active": True,
                    "source": fear_source["id"], "duration": {"period": "round", "remaining": 10},
                    "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
                }]
            actor = await create("Pursuer")
            before_phase = await snapshot()
            await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": before_phase["revision"], "idempotency_key": "play",
            })
            before = await snapshot()
            arguments = {
                "campaign_id": campaign_id, "action": "start", "payload": {
                    "participant_ids": [quarry["id"], actor["id"]], "quarry_ids": [quarry["id"]],
                    "initial_distance_ft": 60, "scene_id": expanded["scene"]["id"],
                    "source_ref": source_ref, "source_excerpt": excerpt,
                    "participant_config": [
                        {"actor_id": quarry["id"], "initiative": 20, "tie_breaker": 1},
                        {"actor_id": actor["id"], "tie_breaker": 0,
                         **({"initiative": 30} if operation != "start" else {})},
                    ],
                }, "expected_revision": before["revision"], "idempotency_key": "chase",
            }
            if operation != "start":
                started = await _call(server, "chase", arguments)
                assert started["status"] == "committed"
                before = await snapshot()
                arguments = {
                    "campaign_id": campaign_id, "action": "take_turn", "payload": {
                        "actor_id": actor["id"], "expected_actor_revision": actor["revision"],
                        "turn_action": "move" if operation == "complication" else "dash",
                        "complication_choice": "acrobatics" if operation == "complication" else "",
                        "quarry_visibility": {quarry["id"]: True},
                    }, "expected_revision": before["revision"], "idempotency_key": "turn",
                }
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(server, "chase", {
                    **arguments, "expected_revision": before["revision"] - 1,
                })
            assert await snapshot() == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation="chase",
                idempotency_key=arguments["idempotency_key"], campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                result = await _call(server, "chase", arguments)
            if frightened:
                assert result["status"] == "pending_ruling"
                assert result["committed"] is False
                assert "frightened_source_visibility" in result["missing"]
                assert stream.draw_count == 0
                assert await snapshot() == before
            else:
                assert result["status"] == "committed"
                assert (await snapshot())["revision"] == before["revision"] + 1
                assert stream.draw_count > 0
            after = await snapshot()
            assert not dict(after["state"].get("combat") or {}).get("active", False)
            assert await _call(server, "chase", arguments) == result
            assert await snapshot() == after
            close_server(server)
            server = create_server(config)
            assert await snapshot() == after
            assert await _call(server, "chase", arguments) == result
            assert await snapshot() == after
        finally:
            close_server(server)

    asyncio.run(exercise())
