from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from sagasmith_dnd.statblocks import parse_2014_statblock
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


async def raw(server, name: str, arguments: dict) -> dict:
    _, result = await server.call_tool(name, arguments)
    return result


@pytest.mark.parametrize("visibility", ["visible", "unseen", "missing"])
@pytest.mark.parametrize("check_action", ["search", "stabilize", "legendary"])
def test_frightened_checks_use_recorded_sources_and_atomic_receipts(
    tmp_path: Path, visibility: str, check_action: str
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Frightened checks", "edition": "2014", "idempotency_key": "create",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            source_sheet = default_character_sheet()
            source_sheet["edition"] = "2014"
            source_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            source = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Fear source", "sheet": source_sheet,
                }, "idempotency_key": "source",
            })
            actor_sheet = default_character_sheet()
            legendary_activity_id = None
            if check_action == "legendary":
                source_path = (
                    Path(__file__).resolve().parents[3] / "skills/full/skills/dnd-dm/srd"
                    / "references-2014-en/10_Monsters/Monsters_Each/Aboleth.md"
                )
                actor_sheet = parse_2014_statblock(
                    source_path.read_text(encoding="utf-8"), source_key="srd2014:aboleth",
                ).sheet
                legendary_activity_id = next(
                    item["id"] for item in actor_sheet["content"]["activities"]
                    if item["name"] == "Detect"
                )
            actor_sheet["edition"] = "2014"
            actor_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            actor_sheet["conditions"] = ["frightened"]
            actor_sheet["effects"] = [{
                "id": "source-bound-fear", "name": "Fear", "kind": "timed_conditions",
                "source": source["id"], "active": True,
                "duration": {"period": "source_turn_start", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
            }]
            actor = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Frightened", "sheet": actor_sheet,
                }, "idempotency_key": "actor",
            })
            patient = None
            witness = None
            if check_action == "stabilize":
                patient_sheet = default_character_sheet()
                patient_sheet["edition"] = "2014"
                patient_sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
                patient_sheet["conditions"] = ["unconscious", "prone"]
                patient = await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": "Patient", "sheet": patient_sheet,
                    }, "idempotency_key": "patient",
                })
            if check_action == "legendary":
                witness = await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": "Other turn", "sheet": source_sheet,
                    }, "idempotency_key": "witness",
                })
            current = await snapshot()
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            participants = [{"actor_id": actor["id"], "initiative": 20, "tie_breaker": 0}]
            if visibility != "missing":
                participants.append({
                    "actor_id": source["id"], "initiative": 10, "tie_breaker": 1,
                    "hidden": visibility == "unseen",
                    "visible_to_actor_ids": [] if visibility == "unseen" else None,
                })
            if witness is not None:
                participants.append({
                    "actor_id": witness["id"], "initiative": 30, "tie_breaker": 2,
                })
            spatial_config = {"positioning_mode": "agent"}
            if patient is not None:
                participants[0]["position"] = {"x": 0, "y": 0}
                if visibility != "missing":
                    participants[1]["position"] = {"x": 0, "y": 2}
                participants.append({
                    "actor_id": patient["id"], "initiative": 5, "tie_breaker": 2,
                    "position": {"x": 1, "y": 0},
                })
                spatial_config = {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 12},
                }
            await raw(server, "combat_start", {
                **spatial_config, "campaign_id": campaign_id,
                "participant_ids": [entry["actor_id"] for entry in participants],
                "participant_config": participants,
                "expected_revision": phase["campaign_revision"], "idempotency_key": "start",
            })
            before = await snapshot()
            tool = "combat_check"
            arguments = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "kind": "check",
                "ability": "perception", "action": "search", "dc": 12,
                "rule_facts": {"frightened_source_visibility": False},
                "expected_revision": before["revision"], "idempotency_key": "fear-search",
            }
            if patient is not None:
                arguments.update(kind="stabilize", ability="wisdom", target_id=patient["id"])
                arguments.pop("action")
                arguments.pop("dc")
            if legendary_activity_id is not None:
                tool = "combat_use_activity"
                arguments = {
                    "campaign_id": campaign_id, "actor_id": actor["id"],
                    "activity_id": legendary_activity_id,
                    "expected_revision": before["revision"], "idempotency_key": "fear-search",
                }
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, tool, {
                    **arguments, "expected_revision": before["revision"] - 1,
                })
            assert await snapshot() == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation=tool,
                idempotency_key="fear-search", campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                settled = await raw(server, tool, arguments)
            if visibility == "missing":
                assert settled["status"] == "pending_ruling"
                assert settled["committed"] is False
                assert "frightened_source_visibility" in settled["missing"]
                assert stream.draw_count == 0
                assert await snapshot() == before
            else:
                expected_draws = 2 if visibility == "visible" else 1
                assert settled["status"] == "committed"
                check = settled["result"]
                if check_action == "legendary":
                    check = check["core_effect"]["check"]
                assert len(check["rolls"]) == expected_draws
                assert settled["random_stream_receipt"]["draw_count"] == expected_draws
                assert "dnd5e.core.check.frightened" in {
                    receipt["mechanic_id"] for receipt in check["rule_receipts"]
                }
                after = await snapshot()
                assert after["revision"] == before["revision"] + 1
                acting = next(item for item in after["state"]["combat"]["combatants"]
                              if item["actor_id"] == actor["id"])
                if check_action == "legendary":
                    assert acting["legendary_actions"]["remaining"] == 2
                else:
                    assert acting["turn_budget"]["main_action"] == 0
                if check_action != "stabilize":
                    assert all(item.get("position") is None
                               for item in after["state"]["combat"]["combatants"])
                assert await raw(server, tool, arguments) == settled
                assert await snapshot() == after
            final_snapshot = await snapshot()
            close_server(server)
            server = create_server(config)
            assert await snapshot() == final_snapshot
            repeated = await raw(server, tool, arguments)
            assert repeated == settled
            assert await snapshot() == final_snapshot
        finally:
            close_server(server)

    asyncio.run(exercise())
