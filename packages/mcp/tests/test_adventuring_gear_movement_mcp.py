from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.adventuring_gear import (
    ADVENTURING_GEAR_ACTIONS,
    ADVENTURING_GEAR_SOURCE_REF,
)
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


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


async def _campaign_and_characters(server, campaign_id: str, actor_ids: list[str]):
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    actors = [
        await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor_id}},
        )
        for actor_id in actor_ids
    ]
    return campaign, actors


def test_ball_bearings_deploy_and_movement_trigger_share_atomic_replay(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Ball bearings movement",
                    "edition": "2014",
                    "random_seed": "ball-bearings-movement-prone",
                    "idempotency_key": "campaign",
                },
            )
            staged_scene = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "ball-bearings-scene.md",
                        "content": "# Crossing\n\nA marked practice grid.",
                        "source_key": "ball-bearings-scene",
                        "title": "Ball Bearings Test Scene",
                    },
                    "idempotency_key": "scene-draft",
                },
            )

            async def authoring_call(runtime, name: str, arguments: dict):
                return await _call(runtime, name, arguments)

            await finalize_and_activate_module(
                authoring_call,
                server,
                campaign["id"],
                staged_scene,
                source_key="ball-bearings-scene",
                title="Ball Bearings Test Scene",
                portable_id="dnd5e.module.ball-bearings-test",
            )
            scene_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign["id"],
                    "query": "marked practice grid",
                    "top_k": 1,
                },
            )
            scene_expansion = await _call(
                server, "module_expand", {"chunk_id": scene_hits[0]["id"]}
            )
            encounter_scene_id = scene_expansion["scene"]["id"]
            bearings = next(
                item
                for item in ADVENTURING_GEAR_ACTIONS.values()
                if item.name == "Ball bearings (bag of 1,000)"
            )
            owner_sheet = default_character_sheet()
            owner_sheet["edition"] = "2014"
            owner_sheet["inventory"]["items"] = [
                {
                    "id": "bearings-1",
                    "name": bearings.name,
                    "source_key": bearings.source_key,
                    "quantity": 1,
                }
            ]
            owner = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Gear user",
                        "sheet": owner_sheet,
                    },
                    "idempotency_key": "owner",
                },
            )

            def target_sheet() -> dict:
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["abilities"]["dexterity"]["score"] = 1
                return sheet

            first = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Fast target",
                        "sheet": target_sheet(),
                    },
                    "idempotency_key": "first-target",
                },
            )
            cautious = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Careful target",
                        "sheet": target_sheet(),
                    },
                    "idempotency_key": "cautious-target",
                },
            )
            actor_ids = [owner["id"], first["id"], cautious["id"]]
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
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
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 4},
                    "scene_id": encounter_scene_id,
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": first["id"], "initiative": 30, "position": {"x": 8, "y": 0}},
                        {"actor_id": owner["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                        {
                            "actor_id": cautious["id"],
                            "initiative": 10,
                            "position": {"x": 3, "y": 1},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )

            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors}
            deploy = {
                "campaign_id": campaign["id"],
                "action_id": "deploy-bearings",
                "item_id": "bearings-1",
                "intent": "spread",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": owner["id"],
                "expected_actor_revision": actor_by_id[owner["id"]]["revision"],
                "expected_revision": current["revision"],
                "action_context": {"area_origin": {"x": 1, "y": 0}},
                "idempotency_key": "deploy-bearings",
            }
            before_wrong_turn = (current, actors)
            with pytest.raises(ToolError, match="not this actor's turn"):
                await _call(server, "adventuring_gear_action", deploy)
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_wrong_turn
            )

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": first["id"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-first-before-deploy",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors}
            invalid_area = {
                **deploy,
                "expected_actor_revision": actor_by_id[owner["id"]]["revision"],
                "expected_revision": current["revision"],
                "action_context": {"area_origin": {"x": 11, "y": 3}},
                "action_id": "invalid-area",
                "idempotency_key": "invalid-area",
            }
            before_invalid_area = (current, actors)
            with pytest.raises(ToolError, match="fit within"):
                await _call(server, "adventuring_gear_action", invalid_area)
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_invalid_area
            )

            deploy.update(
                expected_actor_revision=actor_by_id[owner["id"]]["revision"],
                expected_revision=current["revision"],
            )
            deployed = await _call(server, "adventuring_gear_action", deploy)
            assert deployed["hazard"]["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert deployed["hazard"]["cells"] == ["1,0", "1,1", "2,0", "2,1"]
            assert deployed["receipt"]["quantity"] == 1
            assert deployed["combat"]["combatants"][1]["turn_budget"]["main_action"] == 0
            after_deploy = await _campaign_and_characters(server, campaign["id"], actor_ids)
            deploy_replay = await _call(server, "adventuring_gear_action", deploy)
            assert deploy_replay["receipt"] == deployed["receipt"]
            assert deploy_replay["hazard"] == deployed["hazard"]
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == after_deploy
            )

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": owner["id"],
                    "expected_revision": deployed["campaign_revision"],
                    "idempotency_key": "end-owner-after-deploy",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors}
            cautious_move = {
                "campaign_id": campaign["id"],
                "actor_id": cautious["id"],
                "action": "move",
                "payload": {
                    "distance": 5,
                    "destination": {"x": 2, "y": 1},
                    "path": [{"x": 3, "y": 1}, {"x": 2, "y": 1}],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "cautious-move",
            }
            random_state_before_cautious = current["state"].get("random_stream")
            cautious_result = await _call(server, "combat_movement", cautious_move)
            cautious_resolution = cautious_result["adventuring_gear_area_resolutions"][0]
            assert cautious_resolution["avoided_by"] == "half_speed"
            assert "save" not in cautious_resolution
            after_cautious, _ = await _campaign_and_characters(
                server, campaign["id"], actor_ids
            )
            assert after_cautious["state"].get("random_stream") == random_state_before_cautious

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": cautious["id"],
                    "expected_revision": cautious_result["campaign_revision"],
                    "idempotency_key": "end-cautious-turn",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors}
            path = [{"x": x, "y": 0} for x in range(8, 1, -1)]
            move = {
                "campaign_id": campaign["id"],
                "actor_id": first["id"],
                "action": "move",
                "payload": {"distance": 30, "destination": {"x": 2, "y": 0}, "path": path},
                "expected_revision": current["revision"],
                "idempotency_key": "fast-move",
            }
            missing_path = {
                **move,
                "payload": {"distance": 30, "destination": {"x": 2, "y": 0}},
            }
            before_missing_path = (current, actors)
            missing_result = await _call(server, "combat_movement", missing_path)
            assert missing_result["status"] == "pending_ruling"
            assert missing_result["committed"] is False
            assert missing_result["missing"] == ["adventuring_gear.movement_path"]
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_missing_path
            )

            stale = {
                **move,
                "expected_revision": current["revision"] - 1,
                "idempotency_key": "stale-move",
            }
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(server, "combat_movement", stale)
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_missing_path
            )

            moved = await _call(server, "combat_movement", move)
            resolution = moved["adventuring_gear_area_resolutions"][0]
            assert resolution["save"]["dc"] == 10
            assert resolution["route_index"] == 6
            after_move = await _campaign_and_characters(server, campaign["id"], actor_ids)
            replay = await _call(server, "combat_movement", move)
            assert replay["adventuring_gear_area_resolutions"] == moved[
                "adventuring_gear_area_resolutions"
            ]
            assert replay["random_stream_receipt"] == moved["random_stream_receipt"]
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == after_move
            moved_first = next(actor for actor in after_move[1] if actor["id"] == first["id"])
            combatant = next(
                item for item in moved["combat"]["combatants"] if item["actor_id"] == first["id"]
            )
            assert ("prone" in moved_first["sheet"]["conditions"]) is (
                resolution["outcome"] == "prone"
            )
            assert ("prone" in combatant["conditions"]) is (resolution["outcome"] == "prone")
            assert moved["random_stream_receipt"]["draw_count"] >= 1
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_caltrops_stop_damage_speed_recovery_and_replay_share_cas(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Caltrops movement",
                    "edition": "2014",
                    "random_seed": "caltrops-failure",
                    "idempotency_key": "campaign",
                },
            )
            staged_scene = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "caltrops-scene.md",
                        "content": "# Caltrops\n\nA marked practice grid.",
                        "source_key": "caltrops-scene",
                        "title": "Caltrops Test Scene",
                    },
                    "idempotency_key": "scene-draft",
                },
            )

            async def authoring_call(runtime, name: str, arguments: dict):
                return await _call(runtime, name, arguments)

            await finalize_and_activate_module(
                authoring_call,
                server,
                campaign["id"],
                staged_scene,
                source_key="caltrops-scene",
                title="Caltrops Test Scene",
                portable_id="dnd5e.module.caltrops-test",
            )
            scene_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign["id"],
                    "query": "marked practice grid",
                    "top_k": 1,
                },
            )
            scene_expansion = await _call(
                server, "module_expand", {"chunk_id": scene_hits[0]["id"]}
            )
            scene_id = scene_expansion["scene"]["id"]
            caltrops = next(
                item
                for item in ADVENTURING_GEAR_ACTIONS.values()
                if item.name == "Caltrops (bag of 20)"
            )
            owner_sheet = default_character_sheet()
            owner_sheet["edition"] = "2014"
            owner_sheet["inventory"]["items"] = [
                {
                    "id": "caltrops-1",
                    "name": caltrops.name,
                    "source_key": caltrops.source_key,
                    "quantity": 1,
                }
            ]
            owner = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Caltrops user",
                        "sheet": owner_sheet,
                    },
                    "idempotency_key": "owner",
                },
            )
            cautious_sheet = default_character_sheet()
            cautious_sheet["edition"] = "2014"
            cautious_sheet["abilities"]["dexterity"]["score"] = 1
            cautious = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Careful mover",
                        "sheet": cautious_sheet,
                    },
                    "idempotency_key": "cautious",
                },
            )
            fast_sheet = default_character_sheet()
            fast_sheet["edition"] = "2014"
            fast_sheet["abilities"]["dexterity"]["score"] = 1
            fast_sheet["combat"]["hp"]["value"] = 1
            fast_sheet["combat"]["speed"]["walk"] = 40
            fast = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Fast mover",
                        "sheet": fast_sheet,
                    },
                    "idempotency_key": "fast",
                },
            )
            actor_ids = [owner["id"], cautious["id"], fast["id"]]
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
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
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 3},
                    "scene_id": scene_id,
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": owner["id"], "initiative": 30, "position": {"x": 1, "y": 0}},
                        {
                            "actor_id": cautious["id"],
                            "initiative": 20,
                            "position": {"x": 3, "y": 0},
                        },
                        {"actor_id": fast["id"], "initiative": 10, "position": {"x": 6, "y": 2}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors}
            deploy = {
                "campaign_id": campaign["id"],
                "action_id": "deploy-caltrops",
                "item_id": "caltrops-1",
                "intent": "spread",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": owner["id"],
                "expected_actor_revision": actor_by_id[owner["id"]]["revision"],
                "expected_revision": current["revision"],
                "action_context": {"area_origin": {"x": 2, "y": 0}},
                "idempotency_key": "deploy-caltrops",
            }
            deployed = await _call(server, "adventuring_gear_action", deploy)
            assert deployed["hazard"]["cells"] == ["2,0"]
            assert deployed["hazard"]["width_cells"] == 1
            assert deployed["receipt"]["source_key"] == caltrops.source_key
            assert deployed["combat"]["combatants"][0]["turn_budget"]["main_action"] == 0
            after_deploy = await _campaign_and_characters(server, campaign["id"], actor_ids)
            owner_after_deploy = next(
                actor for actor in after_deploy[1] if actor["id"] == owner["id"]
            )
            assert not any(
                item["id"] == "caltrops-1"
                for item in owner_after_deploy["sheet"]["inventory"]["items"]
            )
            deploy_replay = await _call(server, "adventuring_gear_action", deploy)
            assert deploy_replay["receipt"] == deployed["receipt"]
            assert deploy_replay["hazard"] == deployed["hazard"]
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == after_deploy

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": owner["id"],
                    "expected_revision": deployed["campaign_revision"],
                    "idempotency_key": "end-owner",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            cautious_move = {
                "campaign_id": campaign["id"],
                "actor_id": cautious["id"],
                "action": "move",
                "payload": {
                    "distance": 15,
                    "destination": {"x": 3, "y": 1},
                    "path": [
                        {"x": 3, "y": 0},
                        {"x": 2, "y": 0},
                        {"x": 3, "y": 0},
                        {"x": 3, "y": 1},
                    ],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "cautious-move",
            }
            random_before = current["state"].get("random_stream")
            cautious_result = await _call(server, "combat_movement", cautious_move)
            cautious_resolution = cautious_result["adventuring_gear_area_resolutions"][0]
            assert cautious_resolution["trigger"] == "creature_enters_area"
            assert cautious_resolution["avoided_by"] == "half_speed"
            assert "save" not in cautious_resolution
            after_cautious, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            assert after_cautious["state"].get("random_stream") == random_before

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": cautious["id"],
                    "expected_revision": cautious_result["campaign_revision"],
                    "idempotency_key": "end-cautious",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            fast_turn = next(
                item
                for item in current["state"]["combat"]["combatants"]
                if item["actor_id"] == fast["id"]
            )
            assert fast_turn["turn_budget"]["movement"] == 40, fast_turn["turn_budget"]
            move = {
                "campaign_id": campaign["id"],
                "actor_id": fast["id"],
                "action": "move",
                "payload": {
                    "distance": 30,
                    "destination": {"x": 2, "y": 0},
                    "path": [
                        *[{"x": x, "y": 2} for x in range(6, 1, -1)],
                        {"x": 2, "y": 1},
                        {"x": 2, "y": 0},
                    ],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "fast-move",
            }
            missing_path = {
                **move,
                "payload": {
                    "distance": 0,
                    "destination": {"x": 6, "y": 2},
                },
            }
            before_missing_path = (current, actors)
            pending = await _call(server, "combat_movement", missing_path)
            assert pending["status"] == "pending_ruling"
            assert pending["committed"] is False
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_missing_path
            )
            stale = {
                **move,
                "expected_revision": current["revision"] - 1,
                "idempotency_key": "stale-move",
            }
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(server, "combat_movement", stale)
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_missing_path
            )

            moved = await _call(server, "combat_movement", move)
            resolution = moved["adventuring_gear_area_resolutions"][0]
            assert resolution["save"]["dc"] == 15
            assert resolution["save"]["success"] is False
            assert resolution["damage"]["applied_amount"] == 1
            assert resolution["movement_stopped_this_turn"] is True
            assert resolution["stopped_position"] == {"x": 2, "y": 0}
            after_move = await _campaign_and_characters(server, campaign["id"], actor_ids)
            moved_fast = next(actor for actor in after_move[1] if actor["id"] == fast["id"])
            assert moved_fast["sheet"]["combat"]["hp"]["value"] == 0
            combat = moved["combat"]
            fast_combatant = next(
                item for item in combat["combatants"] if item["actor_id"] == fast["id"]
            )
            assert fast_combatant["position"] == {"x": 2, "y": 0}
            assert fast_combatant["turn_budget"]["speed_modes"]["walk"] == 30
            penalty = next(
                item
                for item in combat["ongoing_effects"]
                if item.get("mechanic_id") == "dnd5e.core.adventuring_gear.caltrops"
            )
            assert penalty["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert penalty["active"] is True
            replay = await _call(server, "combat_movement", move)
            assert replay["adventuring_gear_area_resolutions"] == moved[
                "adventuring_gear_area_resolutions"
            ]
            assert replay["random_stream_receipt"] == moved["random_stream_receipt"]
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == after_move

            blocked_move = {
                **move,
                "payload": {
                    "distance": 5,
                    "destination": {"x": 2, "y": 1},
                    "path": [{"x": 2, "y": 0}, {"x": 2, "y": 1}],
                },
                "expected_revision": after_move[0]["revision"],
                "idempotency_key": "movement-after-caltrops-stop",
            }
            with pytest.raises(ToolError, match="stopped this actor's movement"):
                await _call(server, "combat_movement", blocked_move)
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == after_move

            healed = await _call(
                server,
                "combat_hp_change",
                {
                    "campaign_id": campaign["id"],
                    "target_id": fast["id"],
                    "action": "heal",
                    "payload": {"amount": 1},
                    "expected_revision": after_move[0]["revision"],
                    "idempotency_key": "heal-to-one",
                },
            )
            assert healed["result"]["after_hp"] == 1
            healed_effect = next(
                item
                for item in healed["combat"]["ongoing_effects"]
                if item.get("id") == penalty["id"]
            )
            assert healed_effect["active"] is False
            healed_fast = next(
                item for item in healed["combat"]["combatants"] if item["actor_id"] == fast["id"]
            )
            assert healed_fast["turn_budget"]["speed_modes"]["walk"] == 40
            assert healed_fast["hit_points"] == 1
        finally:
            close_server(server)

    asyncio.run(exercise())
