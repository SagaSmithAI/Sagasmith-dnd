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
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == after_deploy

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
            after_cautious, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
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
            assert (
                replay["adventuring_gear_area_resolutions"]
                == moved["adventuring_gear_area_resolutions"]
            )
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


def test_hunting_trap_deployment_requires_reviewed_anchor_without_writes(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Trap review", "edition": "2014", "idempotency_key": "campaign"},
            )
            trap = next(
                item for item in ADVENTURING_GEAR_ACTIONS.values() if item.name == "Hunting trap"
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["inventory"]["items"] = [
                {"id": "trap-1", "name": trap.name, "source_key": trap.source_key, "quantity": 1}
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Trap user", "sheet": sheet},
                    "idempotency_key": "actor",
                },
            )
            rescuer_sheet = default_character_sheet()
            rescuer_sheet["edition"] = "2014"
            rescuer_sheet["abilities"]["strength"]["score"] = 1
            rescuer = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Rescuer",
                        "sheet": rescuer_sheet,
                    },
                    "idempotency_key": "rescuer",
                },
            )
            actor_ids = [actor["id"], rescuer["id"]]
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 5, "height_cells": 3},
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                        {"actor_id": rescuer["id"], "initiative": 10, "position": {"x": 0, "y": 1}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            before = (current, actors)
            with pytest.raises(ToolError, match="anchor|pressure-plate"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        "campaign_id": campaign["id"],
                        "action_id": "set-trap",
                        "item_id": "trap-1",
                        "intent": "set",
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "actor_id": actor["id"],
                        "expected_actor_revision": actors[0]["revision"],
                        "expected_revision": current["revision"],
                        "action_context": {"area_origin": {"x": 2, "y": 1}},
                        "idempotency_key": "set-trap",
                    },
                )
            assert await _campaign_and_characters(server, campaign["id"], actor_ids) == before
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "random_seed,expected_escape_success",
    [("trap", False), ("trap-success-0", True)],
)
def test_hunting_trap_deploy_trigger_escape_use_source_bound_transactions(
    tmp_path: Path, random_seed: str, expected_escape_success: bool
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Trap transaction",
                    "edition": "2014",
                    "random_seed": random_seed,
                    "idempotency_key": "campaign",
                },
            )
            scene_text = (
                "Anchor-1 is an immobile iron post fixed to bedrock beside a pressure plate."
            )
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "trap-scene.md",
                        "content": f"# Trap\n\n{scene_text}",
                        "source_key": "trap-scene",
                        "title": "Trap Scene",
                    },
                    "idempotency_key": "draft",
                },
            )

            async def authoring_call(runtime, name: str, arguments: dict):
                return await _call(runtime, name, arguments)

            activation = await finalize_and_activate_module(
                authoring_call,
                server,
                campaign["id"],
                staged,
                source_key="trap-scene",
                title="Trap Scene",
                portable_id="dnd5e.module.trap-test",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign["id"], "query": scene_text, "top_k": 1},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = {
                "module_id": activation["activated"]["activation"]["module_id"],
                "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"],
                "page_start": expanded["page_start"],
                "page_end": expanded["page_end"],
                "heading_path": expanded["heading_path"],
                "content_sha256": __import__("hashlib")
                .sha256(expanded["content"].encode("utf-8"))
                .hexdigest(),
            }
            trap_item = next(
                item for item in ADVENTURING_GEAR_ACTIONS.values() if item.name == "Hunting trap"
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["dexterity"]["score"] = 1
            sheet["inventory"]["items"] = [
                {
                    "id": "trap-1",
                    "name": trap_item.name,
                    "source_key": trap_item.source_key,
                    "quantity": 1,
                }
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Trapper", "sheet": sheet},
                    "idempotency_key": "actor",
                },
            )
            rescuer_sheet = default_character_sheet()
            rescuer_sheet["edition"] = "2014"
            rescuer = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Rescuer",
                        "sheet": rescuer_sheet,
                    },
                    "idempotency_key": "rescuer",
                },
            )
            actor_ids = [actor["id"], rescuer["id"]]
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 5, "height_cells": 3},
                    "scene_id": expanded["scene"]["id"],
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                        {"actor_id": rescuer["id"], "initiative": 10, "position": {"x": 0, "y": 1}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {entry["id"]: entry for entry in actors}
            deploy_request = {
                "campaign_id": campaign["id"],
                "action_id": "deploy-trap",
                "item_id": "trap-1",
                "intent": "set",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "expected_actor_revision": actor_by_id[actor["id"]]["revision"],
                "expected_revision": current["revision"],
                "action_context": {
                    "area_origin": {"x": 1, "y": 0},
                    "anchor_object_id": "anchor-1",
                    "anchor_source_ref": source_ref,
                    "anchor_source_excerpt": expanded["content"],
                    "anchor_reason": "DM confirms the iron post is immobile.",
                    "trigger_reason": "Pressure plate is exposed on the traversed cell.",
                },
                "idempotency_key": "deploy-trap",
            }
            deployed = await _call(server, "adventuring_gear_action", deploy_request)
            assert deployed["hazard"]["anchor_review"]["object_id"] == "anchor-1"
            deploy_replay = await _call(server, "adventuring_gear_action", deploy_request)
            assert deploy_replay["hazard"] == deployed["hazard"]
            assert deploy_replay["receipt"] == deployed["receipt"]
            close_server(server)
            server = create_server(_config(tmp_path))
            restart_replay = await _call(server, "adventuring_gear_action", deploy_request)
            assert restart_replay["hazard"] == deployed["hazard"]
            assert restart_replay["receipt"] == deployed["receipt"]
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "expected_revision": deployed["campaign_revision"],
                    "idempotency_key": "end-after-set",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": rescuer["id"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-rescuer",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], actor_ids)
            moved = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "action": "move",
                    "payload": {
                        "distance": 5,
                        "destination": {"x": 1, "y": 0},
                        "path": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "trigger-trap",
                },
            )
            resolution = moved["adventuring_gear_area_resolutions"][0]
            assert resolution["outcome"] == "damaged_and_stopped"
            assert resolution["damage"]["applied_amount"] in {1, 2, 3, 4}
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            assert any(
                hazard.get("trapped_actor_id") == actor["id"]
                and hazard.get("capture_status") == "trapped"
                for hazard in current["state"]["combat"]["adventuring_gear_hazards"]
            )
            before_tether_attempt = (current, actors)
            tether_result = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "action": "move",
                    "payload": {
                        "distance": 5,
                        "destination": {"x": 2, "y": 0},
                        "path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}],
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "blocked-by-tether",
                },
            )
            assert "three-foot chain" in str(tether_result).casefold()
            assert (
                await _campaign_and_characters(server, campaign["id"], actor_ids)
                == before_tether_attempt
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {entry["id"]: entry for entry in actors}
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-after-trigger",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], actor_ids)
            actor_by_id = {entry["id"]: entry for entry in actors}
            escape = await _call(
                server,
                "adventuring_gear_action",
                {
                    "campaign_id": campaign["id"],
                    "action_id": "escape-trap",
                    "item_id": "trap-1",
                    "intent": "escape",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": rescuer["id"],
                    "target_actor_id": actor["id"],
                    "expected_actor_revision": actor_by_id[rescuer["id"]]["revision"],
                    "expected_target_revision": actor_by_id[actor["id"]]["revision"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "escape-trap",
                },
            )
            assert escape["action_paid"] is True
            assert escape["rule_plan"]["check"] == {"ability": "strength", "dc": 13}
            assert escape["success"] is expected_escape_success, (
                escape["check"],
                escape["random_stream_receipt"],
            )
            after_escape = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert (
                after_escape["sheet"]["combat"]["hp"]["value"]
                == escape["target"]["sheet"]["combat"]["hp"]["value"]
            )
            if escape["success"] is False:
                assert escape["damage"]["applied_amount"] == 1
            assert (
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        "campaign_id": campaign["id"],
                        "action_id": "escape-trap",
                        "item_id": "trap-1",
                        "intent": "escape",
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "actor_id": rescuer["id"],
                        "target_actor_id": actor["id"],
                        "expected_actor_revision": actor_by_id[rescuer["id"]]["revision"],
                        "expected_target_revision": actor_by_id[actor["id"]]["revision"],
                        "expected_revision": current["revision"],
                        "idempotency_key": "escape-trap",
                    },
                )
                == escape
            )
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
            assert (
                replay["adventuring_gear_area_resolutions"]
                == moved["adventuring_gear_area_resolutions"]
            )
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


def test_oil_ground_fire_is_source_bound_and_once_per_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Oil fire",
                    "edition": "2014",
                    "random_seed": "oil",
                    "idempotency_key": "campaign",
                },
            )
            scene_text = "A maintained open flame burns in the marked stone cell."
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "oil-scene.md",
                        "content": f"# Oil Scene\n\n{scene_text}",
                        "source_key": "oil-scene",
                        "title": "Oil Scene",
                    },
                    "idempotency_key": "draft",
                },
            )

            async def authoring_call(runtime, name: str, arguments: dict):
                return await _call(runtime, name, arguments)

            activation = await finalize_and_activate_module(
                authoring_call,
                server,
                campaign["id"],
                staged,
                source_key="oil-scene",
                title="Oil Scene",
                portable_id="dnd5e.module.oil-test",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign["id"], "query": scene_text, "top_k": 1},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = {
                "module_id": activation["activated"]["activation"]["module_id"],
                "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"],
                "page_start": expanded["page_start"],
                "page_end": expanded["page_end"],
                "heading_path": expanded["heading_path"],
                "content_sha256": __import__("hashlib")
                .sha256(expanded["content"].encode("utf-8"))
                .hexdigest(),
            }
            oil_plan = next(
                item for item in ADVENTURING_GEAR_ACTIONS.values() if item.name == "Oil (flask)"
            )

            def sheet(*, oil: bool = False, resist_fire: bool = False) -> dict:
                result = default_character_sheet()
                result["edition"] = "2014"
                result["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
                if resist_fire:
                    result["traits"]["resistances"] = ["fire"]
                if oil:
                    result["inventory"]["items"] = [
                        {
                            "id": "oil-1",
                            "name": oil_plan.name,
                            "source_key": oil_plan.source_key,
                            "quantity": 2,
                        }
                    ]
                return result

            owner = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Oil user",
                        "sheet": sheet(oil=True),
                    },
                    "idempotency_key": "owner",
                },
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Resistant target",
                        "sheet": sheet(resist_fire=True),
                    },
                    "idempotency_key": "target",
                },
            )
            already_in_oil = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Already in oil cell",
                        "sheet": sheet(),
                    },
                    "idempotency_key": "already-in-oil",
                },
            )
            ids = [owner["id"], target["id"], already_in_oil["id"]]
            current, _ = await _campaign_and_characters(server, campaign["id"], ids)
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 3},
                    "scene_id": expanded["scene"]["id"],
                    "participant_ids": ids,
                    "participant_config": [
                        {"actor_id": owner["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                        {
                            "actor_id": already_in_oil["id"],
                            "initiative": 15,
                            "position": {"x": 1, "y": 0},
                        },
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 4, "y": 0}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], ids)
            await _call(
                server,
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": campaign["id"],
                    "principal_id": "user:oil-player",
                    "payload": {"role": "player"},
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], ids)
            review_args = {
                "campaign_id": campaign["id"],
                "action": "fire_source_review",
                "payload": {
                    "active": True,
                    "cell": {"x": 1, "y": 0},
                    "level_ground_surface": True,
                    "source_ref": source_ref,
                    "source_excerpt": scene_text,
                    "reason": "DM confirms an active flame.",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "fire-review",
            }
            before_non_dm_review = await _campaign_and_characters(server, campaign["id"], ids)
            with pytest.raises(ToolError, match="role|permission|campaign"):
                await _call(
                    server,
                    "environment_change",
                    {**review_args, "principal_id": "user:oil-player"},
                )
            assert (
                await _campaign_and_characters(server, campaign["id"], ids) == before_non_dm_review
            )
            false_ground_review = {
                **review_args,
                "payload": {**review_args["payload"], "level_ground_surface": False},
                "idempotency_key": "fire-review-not-level",
            }
            with pytest.raises(ToolError, match="level_ground_surface"):
                await _call(server, "environment_change", false_ground_review)
            assert (
                await _campaign_and_characters(server, campaign["id"], ids) == before_non_dm_review
            )
            review = await _call(server, "environment_change", review_args)
            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "expected_revision": review["campaign_revision"],
                    "idempotency_key": "end-before-map-change",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 10, "height_cells": 3},
                    "scene_id": expanded["scene"]["id"],
                    "participant_ids": ids,
                    "participant_config": [
                        {
                            "actor_id": owner["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": already_in_oil["id"],
                            "initiative": 15,
                            "position": {"x": 1, "y": 0},
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 4, "y": 0},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-new-map",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], ids)
            owner_record = next(actor for actor in actors if actor["id"] == owner["id"])
            stale_map_deploy = {
                "campaign_id": campaign["id"],
                "action_id": "stale-map-pour",
                "item_id": "oil-1",
                "intent": "pour_ground",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": owner["id"],
                "expected_actor_revision": owner_record["revision"],
                "expected_revision": current["revision"],
                "action_context": {
                    "area_origin": {"x": 1, "y": 0},
                    "fire_source_id": review["fire_source"]["id"],
                },
                "idempotency_key": "stale-map-pour",
            }
            before_stale_map_deploy = await _campaign_and_characters(server, campaign["id"], ids)
            with pytest.raises(ToolError, match="currently reviewed fire-source cell"):
                await _call(server, "adventuring_gear_action", stale_map_deploy)
            assert (
                await _campaign_and_characters(server, campaign["id"], ids)
                == before_stale_map_deploy
            )
            review = await _call(
                server,
                "environment_change",
                {
                    **review_args,
                    "expected_revision": current["revision"],
                    "idempotency_key": "fire-review-new-map",
                },
            )
            owner_record = next(actor for actor in actors if actor["id"] == owner["id"])
            deploy = {
                "campaign_id": campaign["id"],
                "action_id": "pour-oil",
                "item_id": "oil-1",
                "intent": "pour_ground",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": owner["id"],
                "expected_actor_revision": owner_record["revision"],
                "expected_revision": review["campaign_revision"],
                "action_context": {
                    "area_origin": {"x": 1, "y": 0},
                    "fire_source_id": review["fire_source"]["id"],
                },
                "idempotency_key": "pour-oil",
            }
            before_unreviewed_fire = await _campaign_and_characters(server, campaign["id"], ids)
            with pytest.raises(ToolError, match="currently reviewed fire-source cell"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        **deploy,
                        "action_id": "unreviewed-pour",
                        "action_context": {
                            "area_origin": {"x": 1, "y": 0},
                            "fire_source_id": "caller-invented-id",
                        },
                        "idempotency_key": "unreviewed-pour",
                    },
                )
            assert (
                await _campaign_and_characters(server, campaign["id"], ids)
                == before_unreviewed_fire
            )
            before_stale_deploy = await _campaign_and_characters(server, campaign["id"], ids)
            with pytest.raises(ToolError, match="campaign revision conflict"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        **deploy,
                        "expected_revision": review["campaign_revision"] - 1,
                        "idempotency_key": "stale-pour-oil",
                    },
                )
            assert (
                await _campaign_and_characters(server, campaign["id"], ids) == before_stale_deploy
            )
            deployed = await _call(server, "adventuring_gear_action", deploy)
            assert deployed["hazard"]["oil_lit"] is True
            assert deployed["hazard"]["expires_at_round"] == 3
            assert deployed["receipt"]["quantity"] == 1
            after_deploy = await _campaign_and_characters(server, campaign["id"], ids)
            deployed_owner = next(actor for actor in after_deploy[1] if actor["id"] == owner["id"])
            assert deployed_owner["sheet"]["inventory"]["items"][0]["quantity"] == 1
            assert deployed["combat"]["combatants"][0]["turn_budget"]["main_action"] == 0
            replay = await _call(server, "adventuring_gear_action", deploy)
            assert replay["hazard"] == deployed["hazard"]
            assert await _campaign_and_characters(server, campaign["id"], ids) == after_deploy
            close_server(server)
            server = create_server(_config(tmp_path))
            restart_replay = await _call(server, "adventuring_gear_action", deploy)
            assert restart_replay["hazard"] == deployed["hazard"]
            assert await _campaign_and_characters(server, campaign["id"], ids) == after_deploy

            ended_owner = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": owner["id"],
                    "expected_revision": deployed["campaign_revision"],
                    "idempotency_key": "end-owner-r1",
                },
            )
            standing_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": already_in_oil["id"],
                    "expected_revision": ended_owner["campaign_revision"],
                    "idempotency_key": "end-standing-oil-r1",
                },
            )
            _, actors = await _campaign_and_characters(server, campaign["id"], ids)
            standing_after_end = next(
                actor for actor in actors if actor["id"] == already_in_oil["id"]
            )
            assert standing_turn["oil_hazard_events"][0]["damage"]["applied_amount"] == 5
            assert standing_after_end["sheet"]["combat"]["hp"]["value"] == 15
            assert standing_turn["combat"]["current_turn"]["actor_id"] == target["id"]
            ended_target_r1 = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": target["id"],
                    "expected_revision": standing_turn["campaign_revision"],
                    "idempotency_key": "end-target-r1-outside-oil",
                },
            )
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": owner["id"],
                    "expected_revision": ended_target_r1["campaign_revision"],
                    "idempotency_key": "end-owner-r2",
                },
            )
            current, _ = await _campaign_and_characters(server, campaign["id"], ids)
            standing_move = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": already_in_oil["id"],
                    "action": "move",
                    "payload": {
                        "distance": 5,
                        "destination": {"x": 1, "y": 1},
                        "path": [{"x": 1, "y": 0}, {"x": 1, "y": 1}],
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "standing-creature-leaves-oil",
                },
            )
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": already_in_oil["id"],
                    "expected_revision": standing_move["campaign_revision"],
                    "idempotency_key": "end-standing-oil-r2",
                },
            )
            current, actors = await _campaign_and_characters(server, campaign["id"], ids)
            target_before = next(actor for actor in actors if actor["id"] == target["id"])
            move_args = {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "action": "move",
                "payload": {
                    "distance": 15,
                    "destination": {"x": 1, "y": 0},
                    "path": [
                        {"x": 4, "y": 0},
                        {"x": 3, "y": 0},
                        {"x": 2, "y": 0},
                        {"x": 1, "y": 0},
                    ],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "move-into-oil",
            }
            move = await _call(server, "combat_movement", move_args)
            target_after_move = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": target["id"]},
                },
            )
            assert (
                target_after_move["sheet"]["combat"]["hp"]["value"]
                == target_before["sheet"]["combat"]["hp"]["value"] - 2
            ), (
                move.get("adventuring_gear_area_resolutions"),
                move["combat"].get("adventuring_gear_hazards"),
            )
            assert move["adventuring_gear_area_resolutions"][0]["damage"]["applied_amount"] == 2
            after_move = await _campaign_and_characters(server, campaign["id"], ids)
            move_replay = await _call(server, "combat_movement", move_args)
            assert (
                move_replay["adventuring_gear_area_resolutions"]
                == move["adventuring_gear_area_resolutions"]
            )
            assert await _campaign_and_characters(server, campaign["id"], ids) == after_move
            ended_target = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": target["id"],
                    "expected_revision": move["campaign_revision"],
                    "idempotency_key": "end-target-r2",
                },
            )
            assert ended_target["oil_hazard_events"] == []
            assert (
                target_after_move["sheet"]["combat"]["hp"]["value"]
                == (
                    await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": target["id"]}},
                    )
                )["sheet"]["combat"]["hp"]["value"]
            )
            target_round_two = ended_target
            campaign_after_expiry, _ = await _campaign_and_characters(server, campaign["id"], ids)
            oil_hazard = next(
                hazard
                for hazard in campaign_after_expiry["state"]["combat"]["adventuring_gear_hazards"]
                if hazard["id"] == deployed["hazard"]["id"]
            )
            assert target_round_two["combat"]["round"] == 3
            assert oil_hazard["active"] is False
            actors_after_expiry = await _campaign_and_characters(server, campaign["id"], ids)
            owner_record = next(
                actor for actor in actors_after_expiry[1] if actor["id"] == owner["id"]
            )
            second_deploy = {
                **deploy,
                "action_id": "pour-oil-after-expiry",
                "expected_actor_revision": owner_record["revision"],
                "expected_revision": campaign_after_expiry["revision"],
                "idempotency_key": "pour-oil-after-expiry",
            }
            second_deployed = await _call(server, "adventuring_gear_action", second_deploy)
            assert second_deployed["hazard"]["created_round"] == 3
            revoked = await _call(
                server,
                "environment_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "fire_source_review",
                    "payload": {
                        "active": False,
                        "fire_source_id": review["fire_source"]["id"],
                        "reason": "The DM extinguishes the reviewed flame.",
                    },
                    "expected_revision": second_deployed["campaign_revision"],
                    "idempotency_key": "revoke-fire-source",
                },
            )
            assert revoked["fire_source"]["active"] is False
            after_revoke, actors = await _campaign_and_characters(server, campaign["id"], ids)
            revoked_hazard = next(
                hazard
                for hazard in after_revoke["state"]["combat"]["adventuring_gear_hazards"]
                if hazard["id"] == second_deployed["hazard"]["id"]
            )
            assert revoked_hazard["active"] is False
            assert revoked_hazard["inactive_reason"] == "fire_source_revoked"
            target_hp_before = next(
                actor["sheet"]["combat"]["hp"]["value"]
                for actor in actors
                if actor["id"] == target["id"]
            )
            owner_end_after_revoke = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": owner["id"],
                    "expected_revision": revoked["campaign_revision"],
                    "idempotency_key": "end-owner-after-revoke",
                },
            )
            standing_after_revoke = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": already_in_oil["id"],
                    "expected_revision": owner_end_after_revoke["campaign_revision"],
                    "idempotency_key": "end-standing-after-revoke",
                },
            )
            assert standing_after_revoke["oil_hazard_events"] == []
            current, _ = await _campaign_and_characters(server, campaign["id"], ids)
            after_revoke_move = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": target["id"],
                    "action": "move",
                    "payload": {
                        "distance": 5,
                        "destination": {"x": 2, "y": 0},
                        "path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}],
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "move-after-fire-revoke",
                },
            )
            assert after_revoke_move["status"] == "committed"
            target_after_revoke = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert target_after_revoke["sheet"]["combat"]["hp"]["value"] == target_hp_before
        finally:
            close_server(server)

    asyncio.run(exercise())
