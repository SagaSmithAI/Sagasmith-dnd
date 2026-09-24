"""Source-bound Rolling Sphere activation, movement, contact, and turn settlement."""

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


def test_rolling_sphere_is_source_bound_and_settles_grid_contacts_and_turns(
    tmp_path: Path,
) -> None:
    profile = {"profile_id": "srd5.1.rolling_sphere"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = (
        "A pressure plate releases a 10-foot stone sphere when it bears at least 20 pounds. "
        "All creatures roll initiative; the sphere has +8, moves 60 feet in a straight line, "
        "stops at a wall, and entering its space requires a DC 15 Dexterity save against "
        "10d10 bludgeoning damage and prone. A creature within 5 feet can use an action to "
        "make a DC 20 Strength check and reduce its speed by 15 feet.\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "rolling-sphere.md"
    source.write_text(f"# Trap\n\n## Rolling Sphere\n\n{excerpt}\n", encoding="utf-8")
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
                    "name": "Rolling Sphere settlement",
                    "edition": "2014",
                    "random_seed": "rolling-sphere-grid-settlement",
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
                        "source_key": "rolling-sphere-source",
                        "title": "Rolling Sphere source",
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
                source_key="rolling-sphere-source",
                title="Rolling Sphere source",
                portable_id="dnd5e.module.rolling-sphere-source",
            )
            source_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Rolling Sphere", "top_k": 3},
            )
            source_chunk = await _call(server, "module_expand", {"chunk_id": source_hits[0]["id"]})
            scene_id = source_chunk["scene"]["id"]
            source_ref = json.dumps(
                source_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )

            participants = []
            for name, dexterity, strength in (
                ("Runner", 3, 30),
                ("Target", 1, 3),
                ("Witness", 10, 10),
            ):
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
                sheet["abilities"]["dexterity"]["score"] = dexterity
                sheet["abilities"]["strength"]["score"] = strength
                created = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"campaign_id": campaign_id, "name": name, "sheet": sheet},
                        "idempotency_key": name.casefold(),
                    },
                )
                participants.append(created)
            runner, target, witness = participants

            latest = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign_id,
                    "scene_id": scene_id,
                    "positioning_mode": "grid",
                    "battle_map": {
                        "width_cells": 20,
                        "height_cells": 8,
                        "blocked_cells": [{"x": 12, "y": 2}, {"x": 12, "y": 3}],
                    },
                    "participant_ids": [item["id"] for item in participants],
                    "participant_config": [
                        {
                            "actor_id": runner["id"],
                            "initiative": 20,
                            "position": {"x": 6, "y": 2},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 3, "y": 3},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": witness["id"],
                            "initiative": 5,
                            "position": {"x": 16, "y": 5},
                            "disposition": "friendly",
                        },
                    ],
                    "expected_revision": latest["revision"],
                    "idempotency_key": "start-grid",
                },
            )
            battle_map = started["combat"]["battle_map"]
            latest = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            spatial_facts = {
                "decision_id": "review-sphere-release-path",
                "reason": "The surveyed corridor runs east and terminates at the stone wall.",
                "scene_id": scene_id,
                "trap_id": "sphere-1",
                "encounter_id": started["combat"]["id"],
                "source_ref": source_ref,
                "campaign_revision": latest["revision"],
                "reviewed_by": "system:local",
                "map_id": battle_map["id"],
                "map_revision": battle_map["map_revision"],
                "map_checksum": battle_map["checksum"],
                "origin": {"x": 2, "y": 2},
                "heading": "east",
            }
            transition = {
                "campaign_id": campaign_id,
                "scene_id": scene_id,
                "trap_id": "sphere-1",
                "action": "trigger",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": runner["id"],
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": scene_id,
                    "plate_id": "sphere-1",
                    "weight_lb": 20,
                },
                "spatial_facts": spatial_facts,
                "expected_revision": latest["revision"],
                "idempotency_key": "activate-sphere",
            }
            stale_spatial = {
                **transition,
                "spatial_facts": {
                    **spatial_facts,
                    "campaign_revision": latest["revision"] - 1,
                },
                "idempotency_key": "stale-sphere-path",
            }
            with pytest.raises(ToolError, match="stale or do not match"):
                await _call(server, "trap_state_transition", stale_spatial)
            after_reject = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_reject["revision"] == latest["revision"]

            activated = await _call(server, "trap_state_transition", transition)
            assert activated["sphere"]["active"] is True
            assert activated["sphere"]["initiative"] == activated["sphere"]["initiative_roll"] + 8
            assert activated["contacts"][0]["target_id"] == target["id"]
            assert activated["contacts"][0]["save"]["dc"] == 15
            assert await _call(server, "trap_state_transition", transition) == activated

            move = {
                "campaign_id": campaign_id,
                "actor_id": runner["id"],
                "action": "move",
                "payload": {
                    "distance": 20,
                    "destination": {"x": 2, "y": 2},
                    "path": [{"x": x, "y": 2} for x in range(6, 1, -1)],
                },
                "expected_revision": activated["campaign_revision"],
                "idempotency_key": "runner-enters-sphere-space",
            }
            moved = await _call(server, "combat_movement", move)
            assert moved["rolling_sphere_contacts"][0]["target_id"] == runner["id"]
            assert moved["rolling_sphere_contacts"][0]["save"]["dc"] == 15
            runner_turn = next(
                item for item in moved["combat"]["combatants"] if item["actor_id"] == runner["id"]
            )
            assert runner_turn["turn_budget"]["movement"] == 0
            assert "2,2" not in moved["combat"]["battle_map"]["difficult_cells"]
            assert await _call(server, "combat_movement", move) == moved

            latest = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            slow_args = {
                "campaign_id": campaign_id,
                "scene_id": scene_id,
                "trap_id": "sphere-1",
                "action": "slow",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": runner["id"],
                "expected_revision": latest["revision"],
                "idempotency_key": "slow-sphere",
            }
            slowed = await _call(server, "trap_state_transition", slow_args)
            assert slowed["check"]["dc"] == 20
            assert slowed["action_budget"]["main_action"] == 0
            assert slowed["sphere"]["speed_ft"] == (45 if slowed["check"]["success"] else 60)
            assert await _call(server, "trap_state_transition", slow_args) == slowed

            turn = None
            for index in range(3):
                latest = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                )
                combat = latest["state"]["combat"]
                active_actor = combat["combatants"][combat["turn_index"]]["actor_id"]
                turn = await _call(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": campaign_id,
                        "actor_id": active_actor,
                        "expected_revision": latest["revision"],
                        "idempotency_key": f"end-turn-{index}",
                    },
                )
                if turn.get("rolling_sphere_events"):
                    break
            assert turn is not None and turn["rolling_sphere_events"]
            event = turn["rolling_sphere_events"][0]
            assert event["distance_ft"] <= slowed["sphere"]["speed_ft"]
            assert event["stopped"] is True
            assert event["stop_reason"] == "wall_or_similar_barrier"
        finally:
            close_server(server)

    asyncio.run(exercise())
