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


def test_fleeing_madness_requires_dash_and_away_movement_before_turn_end(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Fleeing madness turn",
                    "edition": "2014",
                    "random_seed": "fleeing-madness-turn",
                    "idempotency_key": "flee-campaign",
                },
            )
            source = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Fear Source",
                        "sheet": {**default_character_sheet(), "edition": "2014"},
                    },
                    "idempotency_key": "fear-source",
                },
            )
            affected_sheet = default_character_sheet()
            affected_sheet["edition"] = "2014"
            effect = madness.resolve_madness("short_term", 31, duration_die=10)[
                "runtime_effect"
            ]
            effect["id"] = "fleeing-madness"
            affected_sheet, _ = add_effect(affected_sheet, effect)
            affected = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Fleeing Character",
                        "sheet": affected_sheet,
                    },
                    "idempotency_key": "affected-character",
                },
            )
            current = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": affected["id"]}},
            )
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": affected["id"],
                    "action": "madness_choose",
                    "payload": {
                        "effect_id": effect["id"],
                        "choice": {"kind": "fear_source_actor", "actor_id": source["id"]},
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "choose-fear-source",
                },
            )
            other = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Other Combatant",
                        "sheet": {**default_character_sheet(), "edition": "2014"},
                    },
                    "idempotency_key": "other-combatant",
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
                    "idempotency_key": "play-phase",
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
                    "battle_map": {"width_cells": 20, "height_cells": 10},
                    "participant_ids": [other["id"], affected["id"], source["id"]],
                    "participant_config": [
                        {
                            "actor_id": other["id"],
                            "initiative": 20,
                            "position": {"x": 1, "y": 1},
                        },
                        {
                            "actor_id": affected["id"],
                            "initiative": 10,
                            "position": {"x": 5, "y": 5},
                        },
                        {
                            "actor_id": source["id"],
                            "initiative": 5,
                            "position": {"x": 2, "y": 5},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "flee-combat",
                },
            )
            turn_started = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": other["id"],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "end-other-turn",
                },
            )
            assert any(
                event.get("action") == "dash"
                for event in turn_started["madness_events"]
            )

            before_rejection = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(Exception, match="legal action payment|spending the action to Dash"):
                await server.call_tool(
                    "combat_common_action",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": affected["id"],
                        "action": "dodge",
                        "expected_revision": before_rejection["revision"],
                        "idempotency_key": "forbidden-dodge",
                    },
                )
            with pytest.raises(Exception, match="requires a Dash"):
                await server.call_tool(
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": affected["id"],
                        "expected_revision": before_rejection["revision"],
                        "idempotency_key": "end-without-dash",
                    },
                )
            after_rejection = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_rejection["revision"] == before_rejection["revision"]
            assert after_rejection["state"]["random_stream"]["position"] == before_rejection[
                "state"
            ]["random_stream"]["position"]

            dashed = await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "action": "dash",
                    "expected_revision": after_rejection["revision"],
                    "idempotency_key": "flee-dash",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(Exception, match="farther from every fear source"):
                await server.call_tool(
                    "combat_movement",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": affected["id"],
                        "action": "move",
                        "payload": {"distance": 5, "path": [{"x": 5, "y": 4}]},
                        "expected_revision": current["revision"],
                        "idempotency_key": "move-toward-fear",
                    },
                )
            after_bad_move = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_bad_move["revision"] == current["revision"]

            with pytest.raises(Exception, match="requires movement"):
                await server.call_tool(
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": affected["id"],
                        "expected_revision": after_bad_move["revision"],
                        "idempotency_key": "end-before-moving",
                    },
                )
            moved = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "action": "move",
                    "payload": {"distance": 5, "path": [{"x": 6, "y": 5}]},
                    "expected_revision": after_bad_move["revision"],
                    "idempotency_key": "move-away-from-fear",
                },
            )
            moved_actor = next(
                item for item in moved["combat"]["combatants"] if item["actor_id"] == affected["id"]
            )
            assert moved_actor["position"] == {"x": 6, "y": 5}
            finished = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "expected_revision": moved["campaign_revision"],
                    "idempotency_key": "finish-flee-turn",
                },
            )
            assert finished["status"] == "committed"
            assert dashed["status"] == "committed"

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            source_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": source["id"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-source-turn",
                },
            )
            other_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": other["id"],
                    "expected_revision": source_turn["campaign_revision"],
                    "idempotency_key": "end-other-round-two-turn",
                },
            )
            moved_without_dash = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "action": "move",
                    "payload": {
                        "distance": 30,
                        "path": [{"x": x, "y": 5} for x in range(7, 13)],
                    },
                    "expected_revision": other_turn["campaign_revision"],
                    "idempotency_key": "move-full-speed-before-dash",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(Exception, match="requires a Dash"):
                await server.call_tool(
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": affected["id"],
                        "expected_revision": current["revision"],
                        "idempotency_key": "end-after-normal-speed-movement",
                    },
                )
            after_block = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_block["revision"] == current["revision"]
            dashed_after_move = await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "action": "dash",
                    "expected_revision": after_block["revision"],
                    "idempotency_key": "dash-after-normal-speed-movement",
                },
            )
            finished_after_move = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": affected["id"],
                    "expected_revision": dashed_after_move["campaign_revision"],
                    "idempotency_key": "finish-flee-round-two",
                },
            )
            assert moved_without_dash["status"] == "committed"
            assert finished_after_move["status"] == "committed"
        finally:
            close_server(server)

    asyncio.run(exercise())
