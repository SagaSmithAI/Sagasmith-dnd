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


async def _raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


async def _create_nearest_attack_world(
    server, name: str, *, suppressed: bool, positioning_mode: str = "grid"
):
    campaign = await _call(
        server,
        "campaign_create",
        {"name": name, "edition": "2014", "idempotency_key": f"{name}-campaign"},
    )
    actor_sheet = default_character_sheet()
    actor_sheet["edition"] = "2014"
    effect = madness.resolve_madness("short_term", 55, duration_die=10)["runtime_effect"]
    effect["id"] = f"{name}-nearest-madness"
    actor_sheet, _ = add_effect(actor_sheet, effect)
    actor_sheet["inventory"]["items"] = [
        {
            "id": "whip",
            "name": "Whip",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "category": "simple",
                "attack_type": "melee",
                "attack_ability": "dexterity",
                "damage_formula": "1d4",
                "damage_type": "slashing",
                "reach_ft": 10,
                "properties": ["finesse", "reach"],
            },
        }
    ]
    actor_sheet["inventory"]["equipment_slots"]["main_hand"] = "whip"
    if suppressed:
        actor_sheet = madness.suppress_madness_effect(
            actor_sheet, effect_id=effect["id"], started_elapsed_ticks=0
        )

    actors = []
    for actor_name, sheet in [
        ("Madness", actor_sheet),
        ("Nearest A", default_character_sheet()),
        ("Nearest B", default_character_sheet()),
        ("Far", default_character_sheet()),
    ]:
        sheet["edition"] = "2014"
        actors.append(
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": f"{name} {actor_name}",
                        "sheet": sheet,
                    },
                    "idempotency_key": f"{name}-{actor_name}",
                },
            )
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
            "idempotency_key": f"{name}-phase",
        },
    )
    current = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
    )
    participant_config = [
        {"actor_id": actors[0]["id"], "initiative": 20},
        {"actor_id": actors[1]["id"], "initiative": 30},
        {"actor_id": actors[2]["id"], "initiative": 15},
        {"actor_id": actors[3]["id"], "initiative": 5},
    ]
    if positioning_mode == "grid":
        for item, position in zip(
            participant_config,
            ({"x": 5, "y": 2}, {"x": 4, "y": 2}, {"x": 6, "y": 2}, {"x": 3, "y": 2}),
        ):
            item["position"] = position
    start_args = {
        "campaign_id": campaign["id"],
        "positioning_mode": positioning_mode,
        "participant_ids": [actor["id"] for actor in actors],
        "participant_config": participant_config,
        "expected_revision": current["revision"],
        "idempotency_key": f"{name}-combat",
    }
    if positioning_mode == "grid":
        start_args["battle_map"] = {"width_cells": 20, "height_cells": 5}
    started = await _call(
        server,
        "combat_start",
        start_args,
    )
    turn_started = started
    if positioning_mode == "grid":
        turn_started = await _call(
            server,
            "combat_end_turn",
            {
                "campaign_id": campaign["id"],
                "actor_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": f"{name}-start-madness-turn",
            },
        )
    return campaign, actors, turn_started


def test_nearest_attack_madness_checks_live_grid_targets_before_attack_commit(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign, actors, turn_started = await _create_nearest_attack_world(
                server, "Nearest constraint", suppressed=False
            )
            actor, near_a, near_b, far = actors
            event = turn_started["madness_events"][-1]
            assert event["effect_ids"] == ["Nearest constraint-nearest-madness"]
            assert set(event["nearest_actor_ids"]) == {near_a["id"], near_b["id"]}

            before = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            attack = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "target_id": far["id"],
                "action": {"weapon_id": "whip", "attack_mode": "melee"},
                "expected_revision": before["revision"],
                "idempotency_key": "illegal-far-target",
            }
            with pytest.raises(Exception, match="nearest creature"):
                await server.call_tool("combat_resolve_attack", attack)
            after_rejection = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_rejection["revision"] == before["revision"]
            assert after_rejection["state"]["random_stream"]["position"] == before["state"][
                "random_stream"
            ]["position"]

            legal_tie_attack = {
                **attack,
                "target_id": near_b["id"],
                "idempotency_key": "legal-tied-nearest-target",
            }
            settled = await _raw(server, "combat_resolve_attack", legal_tie_attack)
            assert settled["result"]["target_id"] == near_b["id"]
            after_attack = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            stale_attack = {
                **legal_tie_attack,
                "target_id": near_a["id"],
                "expected_revision": before["revision"],
                "idempotency_key": "stale-nearest-attack",
            }
            with pytest.raises(Exception, match="revision conflict"):
                await server.call_tool("combat_resolve_attack", stale_attack)
            after_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_stale["revision"] == after_attack["revision"]
            assert after_stale["state"]["random_stream"]["position"] == after_attack[
                "state"
            ]["random_stream"]["position"]
            assert await _raw(server, "combat_resolve_attack", legal_tie_attack) == settled

            suppressed_campaign, suppressed_actors, _ = await _create_nearest_attack_world(
                server, "Suppressed nearest constraint", suppressed=True
            )
            suppressed_actor, _, _, suppressed_far = suppressed_actors
            suppressed_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": suppressed_campaign["id"]}},
            )
            allowed_suppressed_attack = await _raw(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": suppressed_campaign["id"],
                    "actor_id": suppressed_actor["id"],
                    "target_id": suppressed_far["id"],
                    "action": {"weapon_id": "whip", "attack_mode": "melee"},
                    "expected_revision": suppressed_state["revision"],
                    "idempotency_key": "suppressed-far-target",
                },
            )
            assert allowed_suppressed_attack["result"]["target_id"] == suppressed_far["id"]

            no_grid_campaign, no_grid_actors, _ = await _create_nearest_attack_world(
                server,
                "No Grid nearest constraint",
                suppressed=False,
                positioning_mode="agent",
            )
            before_ruling = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": no_grid_campaign["id"]}},
            )
            combat_state = before_ruling["state"]["combat"]
            combatants = combat_state["combatants"]
            for turn_number in range(len(combatants)):
                turn_index = combat_state["turn_index"]
                current_actor_id = combatants[turn_index]["actor_id"]
                next_actor_id = combatants[(turn_index + 1) % len(combatants)]["actor_id"]
                turn_request = {
                    "campaign_id": no_grid_campaign["id"],
                    "actor_id": current_actor_id,
                    "expected_revision": before_ruling["revision"],
                    "idempotency_key": f"no-grid-advance-{turn_number}",
                }
                if next_actor_id == no_grid_actors[0]["id"]:
                    ruling = await _call(server, "combat_end_turn", turn_request)
                    assert ruling["status"] == "pending_ruling"
                    assert ruling["committed"] is False
                    break
                await _call(server, "combat_end_turn", turn_request)
                before_ruling = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": no_grid_campaign["id"]}},
                )
                combat_state = before_ruling["state"]["combat"]
                combatants = combat_state["combatants"]
            else:
                pytest.fail("combat turn cycle did not reach the madness actor")
            after_ruling = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": no_grid_campaign["id"]}},
            )
            assert after_ruling["revision"] == before_ruling["revision"]
            assert after_ruling["state"]["random_stream"]["position"] == before_ruling[
                "state"
            ]["random_stream"]["position"]
        finally:
            close_server(server)

    asyncio.run(exercise())
