from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd import madness
from sagasmith_dnd.character_schema import add_effect, default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


def _config(path: Path) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
    )


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def test_confusion_random_direction_is_seeded_durable_and_requires_full_mapped_grid_path(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Confusion direction movement",
                    "edition": "2014",
                    "random_seed": "confusion-direction-16",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            parent = madness.resolve_madness("long_term", 86, duration_die=10)["runtime_effect"]
            parent.update({"id": "damage-madness", "name": "Damage Madness"})
            sheet, _ = add_effect(sheet, parent)
            sheet = madness.apply_damage_triggered_confusion(
                sheet,
                source_effect_id="damage-madness",
                damage_taken=1,
                save={"kind": "save", "ability": "wisdom", "dc": 15, "success": False},
                confusion_effect_id="confusion-effect",
            )["sheet"]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Confused", "sheet": sheet},
                    "idempotency_key": "confused-actor",
                },
            )
            other_sheet = default_character_sheet()
            other_sheet["edition"] = "2014"
            other = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Other",
                        "sheet": other_sheet,
                    },
                    "idempotency_key": "other-actor",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 20, "height_cells": 20},
                    "participant_ids": [other["id"], actor["id"]],
                    "participant_config": [
                        {"actor_id": other["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                        {"actor_id": actor["id"], "initiative": 10, "position": {"x": 5, "y": 5}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )
            end_turn_request = {
                "campaign_id": campaign["id"],
                "actor_id": other["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "end-other-turn",
            }
            before = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            missing_map = await _call(server, "combat_end_turn", end_turn_request)
            assert missing_map["status"] == "pending_ruling"
            assert missing_map["committed"] is False
            assert "combat.confusion.direction_map" in missing_map["missing"]
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == before["revision"]
            assert unchanged["state"]["random_stream"]["position"] == before["state"][
                "random_stream"
            ]["position"]

            direction_map = {
                "1": {"dx": -1, "dy": -1},
                "2": {"dx": -1, "dy": 0},
                "3": {"dx": 1, "dy": 0},
                "4": {"dx": -1, "dy": 1},
                "5": {"dx": 0, "dy": -1},
                "6": {"dx": 0, "dy": 1},
                "7": {"dx": 1, "dy": -1},
                "8": {"dx": 1, "dy": 1},
            }
            started_turn = await _call(
                server,
                "combat_end_turn",
                {**end_turn_request, "confusion_direction_map": direction_map},
            )
            event = started_turn["madness_events"][0]
            assert event["roll"] == 1
            assert event["roll_result"]["expression"] == "1d10"
            assert event["direction_roll"]["expression"] == "1d8"
            assert event["direction_roll"]["total"] == 3
            assert event["direction_vector"] == direction_map["3"]
            constraint = event["movement_constraint"]
            assert constraint["status"] == "pending"
            assert constraint["rule"] == "consume_all_available_movement"
            assert started_turn["random_stream_receipt"]["draw_count"] == 2

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(
                server,
                "combat_end_turn",
                {**end_turn_request, "confusion_direction_map": direction_map},
            ) == started_turn

            after_turn = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            bad_path = [{"x": 4, "y": 5}]
            bad_move_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "action": "move",
                "payload": {"distance": 5, "path": bad_path},
                "expected_revision": after_turn["revision"],
                "idempotency_key": "bad-direction",
            }
            with pytest.raises(Exception, match="mapped direction"):
                await server.call_tool("combat_movement", bad_move_request)
            unchanged_after_bad_path = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged_after_bad_path["revision"] == after_turn["revision"]
            assert unchanged_after_bad_path["state"]["random_stream"]["position"] == after_turn[
                "state"
            ]["random_stream"]["position"]

            full_path = [{"x": x, "y": 5} for x in range(6, 12)]
            move_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "action": "move",
                "payload": {"distance": 30, "path": full_path},
                "expected_revision": after_turn["revision"],
                "idempotency_key": "mapped-direction-move",
            }
            moved = await _call(server, "combat_movement", move_request)
            moved_actor = next(
                item for item in moved["combat"]["combatants"] if item["actor_id"] == actor["id"]
            )
            assert moved_actor["position"] == {"x": 11, "y": 5}
            assert moved_actor["turn_budget"]["movement_spent"] == 30
            assert (
                moved_actor["turn_flags"]["madness_confusion"]["movement_constraint"]["status"]
                == "completed"
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
