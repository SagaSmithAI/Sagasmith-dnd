from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd import madness
from sagasmith_dnd.character_schema import default_character_sheet

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


def test_damage_triggered_confusion_save_commits_with_damage_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Damage-triggered confusion",
                    "edition": "2014",
                    "random_seed": "madness-damage-trigger",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            sheet["abilities"]["wisdom"]["score"] = 1
            effect = madness.resolve_madness("long_term", 86, duration_die=10)["runtime_effect"]
            effect["id"] = "source-damage-confusion"
            effect["name"] = "Damage-triggered Confusion"
            sheet["effects"].append(effect)
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Affected", "sheet": sheet},
                    "idempotency_key": "actor",
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
                        "name": "Other combatant",
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
            request = {
                "campaign_id": campaign["id"],
                "target_id": actor["id"],
                "action": "damage",
                "payload": {"parts": [{"amount": 1, "damage_type": "force"}]},
                "expected_revision": current["revision"],
                "idempotency_key": "damage-1",
            }
            response = await _call(server, "combat_hp_change", request)
            triggers = response["result"]["madness_triggers"]
            assert len(triggers) == 1
            assert triggers[0]["trigger"] == "takes_damage"
            assert triggers[0]["damage_taken"] == 1
            assert triggers[0]["save"]["dc"] == 15
            assert triggers[0]["save"]["ability"] == "wisdom"
            assert response["random_stream_receipt"]["draw_count"] > 0
            if not triggers[0]["save"]["success"]:
                assert triggers[0]["confusion_applied"] is True
            else:
                assert triggers[0]["confusion_applied"] is False

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "combat_hp_change", request) == response

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
                    "positioning_mode": "agent",
                    "participant_ids": [other["id"], actor["id"]],
                    "participant_config": [
                        {"actor_id": other["id"], "initiative": 20},
                        {"actor_id": actor["id"], "initiative": 10},
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
            turn = await _call(server, "combat_end_turn", end_turn_request)
            assert len(turn["madness_events"]) == 1
            madness_turn = turn["madness_events"][0]
            assert madness_turn["actor_id"] == actor["id"]
            assert 1 <= madness_turn["roll"] <= 10
            assert madness_turn["outcome"] in {
                "move_random_direction",
                "no_action_or_movement",
                "attack_random_creature_in_reach",
                "act_normally",
            }
            next_actor = next(
                item
                for item in turn["combat"]["combatants"]
                if item["actor_id"] == actor["id"]
            )
            assert next_actor["turn_flags"]["madness_confusion"] == madness_turn
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "combat_end_turn", end_turn_request) == turn
        finally:
            close_server(server)

    asyncio.run(exercise())
