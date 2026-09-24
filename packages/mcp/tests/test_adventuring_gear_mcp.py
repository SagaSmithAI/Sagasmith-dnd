from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
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


def _gear_item(name: str, source_key: str, item_id: str) -> dict:
    return {"id": item_id, "name": name, "source_key": source_key, "quantity": 1}


def test_rope_burst_action_persists_source_state_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Rope burst",
                    "edition": "2014",
                    "random_seed": "gear-rope-burst",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 30
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Rope, hempen (50 feet)",
                    "dnd5e.content.srd2014.item.rope-hempen-50-feet",
                    "rope-1",
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Rope user", "sheet": sheet},
                    "idempotency_key": "actor",
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
                "action_id": "burst-rope",
                "item_id": "rope-1",
                "intent": "burst",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "burst-rope",
            }
            result = await _call(server, "adventuring_gear_action", request)
            assert result["rule_plan"]["check"] == {"ability": "strength", "dc": 17}
            assert result["rule_plan"]["object_hit_points"] == 2
            assert result["check"]["rolls"]
            assert result["success"] is True
            assert result["resulting_state"]["state"] == "broken"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            object_key = f"{actor['id']}:rope-1"
            assert current["state"]["adventuring_gear_objects"][object_key]["state"] == "broken"
            assert current["revision"] == request["expected_revision"] + 1
            assert current["state"]["random_stream"]["position"] > 0
            assert any(spend.get("id") == "burst-rope" for spend in current["state"]["item_spends"])
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "adventuring_gear_action", request) == result
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_chain_burst_uses_source_dc_hp_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Chain burst",
                    "edition": "2014",
                    "random_seed": "gear-chain-success-1",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 30
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Chain (10 feet)",
                    "dnd5e.content.srd2014.item.chain-10-feet",
                    "chain-1",
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Chain user",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
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
                "action_id": "burst-chain",
                "item_id": "chain-1",
                "intent": "burst",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "burst-chain",
            }
            with pytest.raises(ToolError, match="do not accept caller rule or outcome context"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {**request, "action_context": {"check": {"dc": 1}, "success": True}},
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == request["expected_revision"]
            assert unchanged["state"].get("item_spends", []) == []

            assert actor["revision"] > 0 and request["expected_revision"] > 0
            for stale_request in (
                {
                    **request,
                    "action_id": "stale-chain-actor",
                    "idempotency_key": "stale-chain-actor",
                    "expected_actor_revision": actor["revision"] - 1,
                    "expected_target_revision": actor["revision"] - 1,
                },
                {
                    **request,
                    "action_id": "stale-chain-campaign",
                    "idempotency_key": "stale-chain-campaign",
                    "expected_revision": request["expected_revision"] - 1,
                },
            ):
                with pytest.raises(ToolError):
                    await _call(server, "adventuring_gear_action", stale_request)
                after_stale = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                assert after_stale["revision"] == request["expected_revision"]
                assert after_stale["state"].get("item_spends", []) == []

            result = await _call(server, "adventuring_gear_action", request)
            assert result["rule_plan"]["check"] == {"ability": "strength", "dc": 20}
            assert result["rule_plan"]["object_hit_points"] == 10
            assert result["check"]["rolls"]
            assert result["success"] is True
            assert result["resulting_state"]["state"] == "broken"
            assert result["resulting_state"]["object_hit_points"] == 0
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert current["state"]["adventuring_gear_objects"][f"{actor['id']}:chain-1"] == result[
                "resulting_state"
            ]
            assert any(
                spend.get("id") == "burst-chain" for spend in current["state"]["item_spends"]
            )
            broken_request = {
                **request,
                "action_id": "burst-broken-chain-again",
                "idempotency_key": "burst-broken-chain-again",
                "expected_actor_revision": result["character"]["revision"],
                "expected_target_revision": result["character"]["revision"],
                "expected_revision": current["revision"],
            }
            with pytest.raises(ToolError, match="chain must be intact"):
                await _call(server, "adventuring_gear_action", broken_request)
            unchanged_after_repeat = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged_after_repeat["revision"] == current["revision"]
            assert unchanged_after_repeat["state"]["item_spends"] == current["state"]["item_spends"]
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "adventuring_gear_action", request) == result
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_chain_burst_failure_preserves_intact_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Chain burst failure",
                    "edition": "2014",
                    "random_seed": "gear-chain-burst-failure",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 1
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Chain (10 feet)",
                    "dnd5e.content.srd2014.item.chain-10-feet",
                    "chain-1",
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Weak chain user",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
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
                "action_id": "burst-chain-fail",
                "item_id": "chain-1",
                "intent": "burst",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "burst-chain-fail",
            }
            result = await _call(server, "adventuring_gear_action", request)
            assert result["success"] is False
            assert result["check"]["total"] < result["rule_plan"]["check"]["dc"]
            assert result["resulting_state"]["state"] == "intact"
            assert result["resulting_state"]["object_hit_points"] == 10
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert current["state"]["adventuring_gear_objects"][
                f"{actor['id']}:chain-1"
            ] == result["resulting_state"]
            assert any(
                spend.get("id") == "burst-chain-fail"
                for spend in current["state"]["item_spends"]
            )
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_lamp_lantern_dynamic_light_fuel_lifecycle_and_replay(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Gear light lifecycle",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            oil = _gear_item(
                "Oil (flask)", "dnd5e.content.srd2014.item.oil-flask", "oil"
            )
            oil["quantity"] = 3
            sheet["inventory"]["items"] = [
                _gear_item("Lamp", "dnd5e.content.srd2014.item.lamp", "lamp"),
                _gear_item(
                    "Lantern, bullseye",
                    "dnd5e.content.srd2014.item.lantern-bullseye",
                    "bullseye",
                ),
                _gear_item(
                    "Lantern, hooded",
                    "dnd5e.content.srd2014.item.lantern-hooded",
                    "hooded",
                ),
                oil,
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Lamp bearer",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Target"},
                    "idempotency_key": "target",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {
                        "width_cells": 8,
                        "height_cells": 4,
                        "ambient_illumination": "dark",
                    },
                    "battle_map_override_reason": "The room is dark for lighting verification.",
                    "campaign_id": campaign["id"],
                    "participant_ids": [actor["id"], target["id"]],
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20, "position": {"x": 1, "y": 1}},
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 2, "y": 1}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )
            scene_id = (
                started["combat"].get("scene_id")
                or started["combat"]["battle_map"]["source"]["scene_id"]
            )
            map_checksum = started["combat"]["battle_map"]["checksum"]
            map_revision = started["combat"]["battle_map"]["map_revision"]
            gear_requests: dict[str, dict] = {}

            async def campaign_state():
                return await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )

            async def character_state():
                return await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )

            async def use_light(
                item_id: str,
                intent: str,
                action_id: str,
                *,
                action_context: dict | None = None,
            ):
                if action_id in gear_requests:
                    return await _call(
                        server, "adventuring_gear_action", gear_requests[action_id]
                    )
                current_state = await campaign_state()
                current_actor = await character_state()
                request = {
                    "campaign_id": campaign["id"],
                    "action_id": action_id,
                    "item_id": item_id,
                    "intent": intent,
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": actor["id"],
                    "expected_actor_revision": current_actor["revision"],
                    "expected_revision": current_state["revision"],
                    "idempotency_key": action_id,
                }
                if action_context is not None:
                    request["action_context"] = action_context
                gear_requests[action_id] = request
                return await _call(server, "adventuring_gear_action", request)

            forged_review_before = await campaign_state()
            with pytest.raises(ToolError):
                await use_light(
                    "bullseye",
                    "light",
                    "bullseye-forged-review",
                    action_context={
                        "direction": "east",
                        "scene_id": scene_id,
                        "map_checksum": map_checksum,
                        "map_revision": map_revision,
                        "reviewer_principal_id": "system:local",
                    },
                )
            unchanged = await campaign_state()
            unchanged_actor = await character_state()
            assert unchanged["revision"] == forged_review_before["revision"]
            remaining_oil = next(
                item
                for item in unchanged_actor["sheet"]["inventory"]["items"]
                if item["id"] == "oil"
            )["quantity"]
            assert remaining_oil == 3
            assert unchanged["state"]["combat"].get("adventuring_gear_lights", []) == []

            lit_lamp = await use_light("lamp", "light", "lamp-light")
            assert lit_lamp["light"]["active"] is True
            assert lit_lamp["fuel"]["quantity_spent"] == 1
            assert lit_lamp["light"]["remaining_fuel_ticks"] == 3600
            assert await use_light("lamp", "light", "lamp-light") == lit_lamp

            async def preflight():
                return await _call(
                    server,
                    "combat_preflight_attack",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "target_id": target["id"],
                        "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
                    },
                )

            lamp_vision = await preflight()
            assert lamp_vision["vision"]["attacker"]["light_level"] == "bright"
            assert any(
                light["source_ref"].endswith("Adventuring_Gear.md#Lamp")
                for light in lamp_vision["vision"]["attacker"]["source_lights"]
            )
            close_server(server)
            server = create_server(config)
            restarted_lamp_vision = await preflight()
            assert restarted_lamp_vision["vision"]["attacker"]["light_level"] == "bright"

            extinguished_lamp = await use_light("lamp", "extinguish", "lamp-off")
            assert extinguished_lamp["light"]["active"] is False
            assert extinguished_lamp["light"]["remaining_fuel_ticks"] == 3600
            relit_lamp = await use_light("lamp", "light", "lamp-relight")
            assert relit_lamp["fuel"]["quantity_spent"] == 0
            assert relit_lamp["light"]["fuel_due_elapsed_ticks"] == 3600

            bullseye = await use_light(
                "bullseye",
                "light",
                "bullseye-light",
                action_context={
                    "direction": "east",
                    "scene_id": scene_id,
                    "map_checksum": map_checksum,
                    "map_revision": map_revision,
                },
            )
            assert bullseye["fuel"]["quantity_spent"] == 1
            cone_vision = await preflight()
            assert any(
                light.get("shape") == "cone" and light.get("direction") == "east"
                for light in cone_vision["vision"]["attacker"]["source_lights"]
            )
            await use_light("lamp", "extinguish", "lamp-off-before-hood")
            await use_light("bullseye", "extinguish", "bullseye-off")

            hooded = await use_light("hooded", "light", "hooded-light")
            assert hooded["fuel"]["quantity_spent"] == 1
            lowered = await use_light("hooded", "lower_hood", "hooded-lower")
            assert lowered["light"]["hood"] == "lowered"
            raised = await use_light("hooded", "raise_hood", "hooded-raise")
            assert raised["light"]["hood"] == "raised"
            await use_light("hooded", "extinguish", "hooded-off")
            final_lamp = await use_light("lamp", "light", "lamp-final-light")
            assert final_lamp["fuel"]["quantity_spent"] == 0

            current = await campaign_state()
            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "victory", "summary": "Lighting lifecycle complete."},
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-combat",
                },
            )
            current = await campaign_state()
            before_due = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "minute", "count": 359, "expected_elapsed_ticks": 3590},
                    "expected_revision": current["revision"],
                    "idempotency_key": "clock-before-light-expiry",
                },
            )
            before_due_state = await campaign_state()
            before_light = next(
                light
                for light in before_due_state["state"]["combat"]["adventuring_gear_lights"]
                if light["item_id"] == "lamp"
            )
            assert before_light["active"] is True
            assert before_light["remaining_fuel_ticks"] == 10
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "minute", "count": 1, "expected_elapsed_ticks": 3600},
                    "expected_revision": before_due["campaign_revision"],
                    "idempotency_key": "clock-at-light-expiry",
                },
            )
            at_due_state = await campaign_state()
            expired_lamp = next(
                light
                for light in at_due_state["state"]["combat"]["adventuring_gear_lights"]
                if light["item_id"] == "lamp"
            )
            assert expired_lamp["active"] is False
            assert expired_lamp["remaining_fuel_ticks"] == 0
            close_server(server)
            server = create_server(config)
            after_restart = await campaign_state()
            persisted_lamp = next(
                light for light in after_restart["state"]["combat"]["adventuring_gear_lights"]
                if light["item_id"] == "lamp"
            )
            assert persisted_lamp["active"] is False
            assert persisted_lamp["remaining_fuel_ticks"] == 0
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_manacles_binding_key_escape_break_and_pick_settle_atomically(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Manacles lifecycle",
                    "edition": "2014",
                    "random_seed": "manacles-lifecycle",
                    "idempotency_key": "campaign",
                },
            )
            source_sheet = default_character_sheet()
            source_sheet["edition"] = "2014"
            source_sheet["traits"]["proficiencies"]["tools"] = ["Thieves' Tools"]
            source_sheet["inventory"]["items"] = [
                _gear_item("Manacles", "dnd5e.content.srd2014.item.manacles", f"manacles-{i}")
                for i in range(1, 6)
            ]
            source = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Manacles holder",
                        "sheet": source_sheet,
                    },
                    "idempotency_key": "source",
                },
            )
            targets = []
            for index, size in enumerate(("medium", "small", "medium", "medium", "large")):
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["traits"]["size"] = size
                sheet["abilities"]["dexterity"]["score"] = 30
                sheet["abilities"]["strength"]["score"] = 30
                if index == 0:
                    sheet["conditions"] = ["restrained"]
                target = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": f"Manacles target {index}",
                            "sheet": sheet,
                        },
                        "idempotency_key": f"target-{index}",
                    },
                )
                targets.append(target)

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
                    "battle_map": {"width_cells": 16, "height_cells": 4},
                    "participant_ids": [source["id"], *(target["id"] for target in targets)],
                    "participant_config": [
                        {
                            "actor_id": actor_id,
                            "initiative": 20 - index,
                            "position": {"x": index * 2, "y": 0},
                        }
                        for index, actor_id in enumerate(
                            [source["id"], *(target["id"] for target in targets)]
                        )
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-start",
                },
            )

            async def get_campaign() -> dict:
                return await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )

            async def get_character(actor_id: str) -> dict:
                return await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor_id}},
                )

            async def act(
                item_number: int,
                target_number: int,
                intent: str,
                action_id: str,
                action_context: dict | None = None,
            ) -> tuple[dict, dict]:
                owner = await get_character(source["id"])
                target = await get_character(targets[target_number]["id"])
                state = await get_campaign()
                request = {
                    "campaign_id": campaign["id"],
                    "action_id": action_id,
                    "item_id": f"manacles-{item_number}",
                    "intent": intent,
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": source["id"],
                    "target_actor_id": target["id"],
                    "expected_actor_revision": owner["revision"],
                    "expected_target_revision": target["revision"],
                    "expected_revision": state["revision"],
                    "idempotency_key": action_id,
                }
                if action_context is not None:
                    request["action_context"] = action_context
                return await _call(server, "adventuring_gear_action", request), request

            async def assert_rejected_without_writes(
                item_number: int,
                target_number: int,
                intent: str,
                action_id: str,
                context: dict | None = None,
                match: str | None = None,
            ) -> None:
                owner_before = await get_character(source["id"])
                target_before = await get_character(targets[target_number]["id"])
                state_before = await get_campaign()
                with pytest.raises(ToolError, match=match):
                    await act(item_number, target_number, intent, action_id, context)
                owner_after = await get_character(source["id"])
                target_after = await get_character(targets[target_number]["id"])
                state_after = await get_campaign()
                assert owner_after["revision"] == owner_before["revision"]
                assert target_after["revision"] == target_before["revision"]
                assert owner_after["sheet"] == owner_before["sheet"]
                assert target_after["sheet"] == target_before["sheet"]
                assert state_after["revision"] == state_before["revision"]
                assert state_after["state"] == state_before["state"]

            bind_context = {
                "binding_possible": True,
                "reason": "The target is held in place for the reviewed binding attempt.",
                "key_available": True,
                "key_reason": "The included key remains available to the holder.",
            }
            no_key_context = {
                **bind_context,
                "key_available": False,
                "key_reason": "The included key is not currently available to the holder.",
            }
            await assert_rejected_without_writes(
                1, 0, "bind", "bind-needs-review", match="bounded review"
            )
            await assert_rejected_without_writes(
                1,
                0,
                "bind",
                "bind-forged-result",
                {**bind_context, "dc": 1, "success": True},
                match="bounded review",
            )
            await assert_rejected_without_writes(
                5, 4, "bind", "bind-large-target", bind_context, match="Small or Medium"
            )

            bound, bind_request = await act(1, 0, "bind", "bind-with-key", bind_context)
            binding = bound["binding"]
            assert bound["success"] is True
            assert binding["status"] == "bound"
            assert binding["owner_actor_id"] == source["id"]
            assert binding["item_id"] == "manacles-1"
            assert binding["target_actor_id"] == targets[0]["id"]
            assert binding["key_holder_actor_id"] == source["id"]
            assert binding["key_available"] is True
            assert binding["key_review"]["reviewed_actor_id"] == source["id"]
            assert await _call(server, "adventuring_gear_action", bind_request) == bound
            holder = await get_character(source["id"])
            assert all(item["quantity"] == 1 for item in holder["sheet"]["inventory"]["items"])
            restrained_target = await get_character(targets[0]["id"])
            assert "restrained" in restrained_target["sheet"]["conditions"]

            await assert_rejected_without_writes(
                1, 0, "pick", "pick-with-key", match="unavailable"
            )
            unlocked, _ = await act(1, 0, "unlock", "unlock-with-key")
            assert unlocked["success"] is True
            assert unlocked["binding"]["status"] == "released"
            preserved_target = await get_character(targets[0]["id"])
            assert "restrained" in preserved_target["sheet"]["conditions"]

            await act(2, 1, "bind", "bind-without-key", no_key_context)
            no_key_target = await get_character(targets[1]["id"])
            assert "restrained" not in no_key_target["sheet"]["conditions"]
            picked, _ = await act(2, 1, "pick", "pick-without-key")
            assert picked["rule_plan"]["check"] == {"ability": "dexterity", "dc": 15}
            assert picked["check"]["proficiency_bonus"] == 2
            assert picked["success"] is True
            assert picked["binding"]["status"] == "released"
            assert picked["binding"]["key_available"] is False

            await act(3, 2, "bind", "bind-for-escape", bind_context)
            for attempt in range(1, 21):
                escaped, _ = await act(3, 2, "escape", f"escape-{attempt}")
                assert escaped["rule_plan"]["check"] == {"ability": "dexterity", "dc": 20}
                if escaped["success"]:
                    break
                assert escaped["binding"]["status"] == "bound"
            else:
                raise AssertionError("Dexterity 30 target did not pass DC 20 escape in 20 attempts")
            assert escaped["binding"]["status"] == "released"
            escaped_target = await get_character(targets[2]["id"])
            assert "restrained" not in escaped_target["sheet"]["conditions"]

            await act(4, 3, "bind", "bind-for-break", bind_context)
            for attempt in range(1, 21):
                broken, _ = await act(4, 3, "break", f"break-{attempt}")
                assert broken["rule_plan"]["check"] == {"ability": "strength", "dc": 20}
                if broken["success"]:
                    break
                assert broken["binding"]["status"] == "bound"
            else:
                raise AssertionError("Strength 30 target did not pass DC 20 break in 20 attempts")
            assert broken["binding"]["status"] == "broken"
            assert broken["binding"]["object_hit_points"] == 0

            await assert_rejected_without_writes(
                4, 3, "bind", "rebind-broken-manacles", bind_context, match="broken Manacles"
            )
            current = await get_campaign()
            action_ids = {entry["id"] for entry in current["state"]["item_spends"]}
            assert {
                "bind-with-key",
                "unlock-with-key",
                "bind-without-key",
                "pick-without-key",
            } <= action_ids
            assert all(entry["quantity"] == 0 for entry in current["state"]["item_spends"])
            assert current["revision"] > started["campaign_revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_manacles_pick_requires_proficiency_and_rejection_is_no_write(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Untrained Manacles holder",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            holder_sheet = default_character_sheet()
            holder_sheet["edition"] = "2014"
            holder_sheet["inventory"]["items"] = [
                _gear_item(
                    "Manacles",
                    "dnd5e.content.srd2014.item.manacles",
                    "manacles-untrained",
                )
            ]
            holder = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Untrained holder",
                        "sheet": holder_sheet,
                    },
                    "idempotency_key": "holder",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["traits"]["size"] = "small"
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Small target",
                        "sheet": target_sheet,
                    },
                    "idempotency_key": "target",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "participant_ids": [holder["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": holder["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-start",
                },
            )
            holder = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": holder["id"]}},
            )
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            bound = await _call(
                server,
                "adventuring_gear_action",
                {
                    "campaign_id": campaign["id"],
                    "action_id": "bind-without-key",
                    "item_id": "manacles-untrained",
                    "intent": "bind",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": holder["id"],
                    "target_actor_id": target["id"],
                    "expected_actor_revision": holder["revision"],
                    "expected_target_revision": target["revision"],
                    "expected_revision": current["revision"],
                    "idempotency_key": "bind-without-key",
                    "action_context": {
                        "binding_possible": True,
                        "reason": "The target is held in place for this reviewed attempt.",
                        "key_available": False,
                        "key_reason": "The included key is currently unavailable.",
                    },
                },
            )
            assert bound["binding"]["key_available"] is False
            holder_before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": holder["id"]}},
            )
            target_before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            state_before = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="thieves' tools proficiency"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        "campaign_id": campaign["id"],
                        "action_id": "untrained-pick",
                        "item_id": "manacles-untrained",
                        "intent": "pick",
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "actor_id": holder["id"],
                        "target_actor_id": target["id"],
                        "expected_actor_revision": holder_before["revision"],
                        "expected_target_revision": target_before["revision"],
                        "expected_revision": state_before["revision"],
                        "idempotency_key": "untrained-pick",
                    },
                )
            holder_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": holder["id"]}},
            )
            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            state_after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert holder_after["revision"] == holder_before["revision"]
            assert target_after["revision"] == target_before["revision"]
            assert "restrained" not in target_after["sheet"]["conditions"]
            assert state_after["revision"] == state_before["revision"]
            assert state_after["state"] == state_before["state"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_lock_pick_uses_sheet_tool_proficiency_and_persists_open_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Lock pick", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["dexterity"]["score"] = 30
            sheet["traits"]["proficiencies"]["tools"] = ["Thieves' Tools"]
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Lock",
                    "dnd5e.content.srd2014.item.lock",
                    "lock-1",
                ),
                _gear_item(
                    "Lock",
                    "dnd5e.content.srd2014.item.lock",
                    "lock-2",
                ),
                _gear_item(
                    "Lock",
                    "dnd5e.content.srd2014.item.lock",
                    "lock-3",
                ),
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Lock picker",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            untrained_sheet = default_character_sheet()
            untrained_sheet["edition"] = "2014"
            untrained_sheet["inventory"]["items"] = [
                _gear_item(
                    "Lock",
                    "dnd5e.content.srd2014.item.lock",
                    "lock-untrained",
                )
            ]
            untrained = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Untrained lock user",
                        "sheet": untrained_sheet,
                    },
                    "idempotency_key": "untrained-actor",
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
                "action_id": "pick-lock",
                "item_id": "lock-1",
                "intent": "pick",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "pick-lock",
            }
            unreviewed = {**request, "action_id": "pick-without-key-review"}
            unreviewed["idempotency_key"] = "pick-without-key-review"
            forged = {
                **request,
                "action_context": {
                    "key_unavailable": True,
                    "reason": "The key is missing.",
                    "check": {"dc": 1},
                    "success": True,
                },
            }
            for invalid in (unreviewed, forged):
                with pytest.raises(ToolError, match="bounded review"):
                    await _call(server, "adventuring_gear_action", invalid)
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged_actor["revision"] == request["expected_actor_revision"]
            assert unchanged_campaign["revision"] == request["expected_revision"]
            assert unchanged_campaign["state"].get("item_spends", []) == []

            untrained_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": untrained["id"]}},
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="thieves' tools proficiency"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        "campaign_id": campaign["id"],
                        "action_id": "untrained-lock-pick",
                        "item_id": "lock-untrained",
                        "intent": "pick",
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "actor_id": untrained["id"],
                        "target_actor_id": untrained["id"],
                        "expected_actor_revision": untrained_actor["revision"],
                        "expected_target_revision": untrained_actor["revision"],
                        "expected_revision": current["revision"],
                        "idempotency_key": "untrained-lock-pick",
                        "action_context": {
                            "key_unavailable": True,
                            "reason": "The key provided with the lock is unavailable.",
                        },
                    },
                )
            after_untrained_pick = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_untrained_pick["revision"] == current["revision"]
            assert after_untrained_pick["state"] == current["state"]

            pick_request = {
                **request,
                "action_context": {
                    "key_unavailable": True,
                    "reason": "The key supplied with this lock is not currently available.",
                },
            }
            result = await _call(server, "adventuring_gear_action", pick_request)
            assert result["rule_plan"]["check"] == {"ability": "dexterity", "dc": 15}
            assert result["check"]["proficiency_bonus"] == 2
            assert result["success"] is True
            assert result["resulting_state"]["state"] == "open"
            assert result["resulting_state"]["key_holder_actor_id"] == actor["id"]
            assert result["resulting_state"]["key_available"] is False
            assert result["resulting_state"]["key_source"] == "provided_with_lock"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            object_key = f"{actor['id']}:lock-1"
            assert current["state"]["adventuring_gear_objects"][object_key]["state"] == "open"
            receipt = next(
                spend for spend in current["state"]["item_spends"] if spend.get("id") == "pick-lock"
            )
            assert receipt["quantity"] == 0
            assert receipt["key_available"] is False
            assert await _call(server, "adventuring_gear_action", pick_request) == result

            actor_after_pick = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            campaign_after_pick = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            unlock_request = {
                "campaign_id": campaign["id"],
                "action_id": "unlock-lock-with-key",
                "item_id": "lock-2",
                "intent": "unlock",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor_after_pick["revision"],
                "expected_target_revision": actor_after_pick["revision"],
                "expected_revision": campaign_after_pick["revision"],
                "idempotency_key": "unlock-lock-with-key",
            }
            before_bad_unlock = (actor_after_pick, campaign_after_pick)
            with pytest.raises(ToolError, match="do not accept caller rule or outcome context"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {**unlock_request, "action_context": {"key_available": True, "success": True}},
                )
            assert (
                await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
            )["revision"] == before_bad_unlock[0]["revision"]
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
            )["state"] == before_bad_unlock[1]["state"]

            unlocked = await _call(server, "adventuring_gear_action", unlock_request)
            assert unlocked["success"] is True
            assert unlocked["check"] is None
            assert unlocked["resulting_state"]["state"] == "open"
            assert unlocked["resulting_state"]["key_holder_actor_id"] == actor["id"]
            assert unlocked["resulting_state"]["key_available"] is True
            assert (
                unlocked["campaign"]["state"]["random_stream"]
                == campaign_after_pick["state"]["random_stream"]
            )
            unlock_receipt = next(
                spend
                for spend in unlocked["campaign"]["state"]["item_spends"]
                if spend.get("id") == "unlock-lock-with-key"
            )
            assert unlock_receipt["quantity"] == 0
            assert await _call(server, "adventuring_gear_action", unlock_request) == unlocked

            before_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        **unlock_request,
                        "item_id": "lock-3",
                        "action_id": "stale-lock-unlock",
                        "idempotency_key": "stale-lock-unlock",
                    },
                )
            after_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_stale["revision"] == before_stale["revision"]
            assert after_stale["state"] == before_stale["state"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_acid_and_alchemists_fire_attack_source_objects_atomically(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Gear object attacks",
                    "edition": "2014",
                    "random_seed": "gear-object-attacks",
                    "idempotency_key": "campaign",
                },
            )
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "gear-target.md",
                        "content": (
                            "# Workshop\n\n## Door\n\n"
                            "The wooden door has AC 5 and 500 hit points."
                        ),
                        "source_key": "gear-target",
                        "title": "Gear target",
                    },
                    "idempotency_key": "stage-module",
                },
            )
            activation = await finalize_and_activate_module(
                _call,
                server,
                campaign["id"],
                staged,
                source_key="gear-target",
                title="Gear target",
                portable_id="dnd5e.module.gear-target-test",
            )
            module_id = activation["activated"]["activation"]["module_id"]
            hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign["id"],
                    "query": "wooden door AC hit points",
                    "top_k": 3,
                },
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            object_source_ref = {
                "module_id": module_id,
                "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"],
                "page_start": expanded["page_start"],
                "page_end": expanded["page_end"],
                "heading_path": expanded["heading_path"],
                "content_sha256": hashlib.sha256(
                    expanded["content"].encode("utf-8")
                ).hexdigest(),
            }
            object_profile = {
                "id": "wooden-door",
                "name": "Wooden door",
                "scene_id": expanded["scene"]["id"],
                "material": "wood",
                "size": "large",
                "resilience": "resilient",
                "armor_class": 5,
                "hit_points": 500,
            }
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 30
            sheet["inventory"]["items"] = [
                {
                    **_gear_item(
                        "Acid (vial)",
                        "dnd5e.content.srd2014.item.acid-vial",
                        "acid-1",
                    ),
                    "quantity": 1,
                },
                {
                    **_gear_item(
                        "Alchemist's fire (flask)",
                        "dnd5e.content.srd2014.item.alchemist-s-fire-flask",
                        "fire-1",
                    ),
                    "quantity": 1,
                },
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Gear user",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )

            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            bad_reference = {**object_source_ref, "content_sha256": "0" * 64}
            invalid_request = {
                "campaign_id": campaign["id"],
                "action_id": "invalid-acid-object",
                "item_id": "acid-1",
                "intent": "throw",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_revision": current_campaign["revision"],
                "target_object": object_profile,
                "object_source_ref": bad_reference,
                "object_reason": "The acid strikes the wooden door.",
                "object_ruling": {
                    "reason": "DM reviewed the door statistics from the module source.",
                    "source_excerpt": "The wooden door has AC 5 and 500 hit points.",
                },
                "idempotency_key": "invalid-acid-object",
            }
            with pytest.raises(ToolError, match="content_sha256"):
                await _call(server, "adventuring_gear_action", invalid_request)
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert unchanged_campaign["revision"] == current_campaign["revision"]
            assert unchanged_campaign["state"].get("scene_objects", {}) == {}
            assert unchanged_campaign["state"].get("item_spends", []) == []
            assert unchanged_campaign["state"].get("random_stream") == current_campaign[
                "state"
            ].get("random_stream")
            assert unchanged_actor["revision"] == actor["revision"]
            assert unchanged_actor["sheet"]["inventory"]["items"][0]["quantity"] == 1

            for item_id, intent, damage_expression in (
                ("acid-1", "throw", "2d6"),
                ("fire-1", "throw", "1d4"),
            ):
                result = None
                last_request = None
                for attempt in range(20):
                    current_campaign = await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    actor = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actor["id"]}},
                    )
                    last_request = {
                        "campaign_id": campaign["id"],
                        "action_id": f"{item_id}-object-attack-{attempt}",
                        "item_id": item_id,
                        "intent": intent,
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "actor_id": actor["id"],
                        "expected_actor_revision": actor["revision"],
                        "expected_revision": current_campaign["revision"],
                        "target_object": object_profile,
                        "object_source_ref": object_source_ref,
                        "object_reason": f"{intent} strikes the wooden door.",
                        "object_ruling": {
                            "reason": "DM reviewed the door statistics from the module source.",
                            "source_excerpt": "The wooden door has AC 5 and 500 hit points.",
                        },
                        "idempotency_key": f"{item_id}-object-attack-{attempt}",
                    }
                    result = await _call(server, "adventuring_gear_action", last_request)
                    if result["attack"]["hit"]:
                        break
                assert result is not None and result["attack"]["hit"] is True
                gear_receipt = result["adventuring_gear"]
                assert gear_receipt["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
                planned_damage = gear_receipt["rule_plan"]["effect"].get(
                    "damage"
                ) or gear_receipt["rule_plan"]["effect"].get("hit_damage")
                assert planned_damage == damage_expression
                assert result["damage"]["expression"] == damage_expression
                assert result["object"]["hit_points"] < object_profile["hit_points"]

                current_campaign = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                spend = next(
                    item
                    for item in current_campaign["state"]["item_spends"]
                    if item.get("id") == last_request["action_id"]
                )
                assert spend["item_id"] == item_id
                assert spend["quantity"] == 1
                assert spend["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
                assert spend["target_object_id"] == object_profile["id"]
                assert spend["rule_plan"] == gear_receipt["rule_plan"]
                actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                remaining = sum(
                    item["quantity"]
                    for item in actor["sheet"]["inventory"]["items"]
                    if item["id"] == item_id
                )
                assert remaining == 0

                expected_result = result
                assert await _call(server, "adventuring_gear_action", last_request) == (
                    expected_result
                )
                close_server(server)
                server = create_server(_config(tmp_path))
                assert await _call(server, "adventuring_gear_action", last_request) == (
                    expected_result
                )

            combat_sheet = default_character_sheet()
            combat_sheet["edition"] = "2014"
            combat_sheet["abilities"]["strength"]["score"] = 30
            combat_sheet["inventory"]["items"] = [
                {
                    **_gear_item(
                        "Acid (vial)",
                        "dnd5e.content.srd2014.item.acid-vial",
                        "combat-acid-1",
                    ),
                    "quantity": 1,
                },
                {
                    **_gear_item(
                        "Alchemist's fire (flask)",
                        "dnd5e.content.srd2014.item.alchemist-s-fire-flask",
                        "combat-fire-1",
                    ),
                    "quantity": 1,
                },
            ]
            combat_actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Combat gear user",
                        "sheet": combat_sheet,
                    },
                    "idempotency_key": "combat-gear-actor",
                },
            )
            combat_target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Combat gear opponent",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "combat-gear-target",
                },
            )
            current_campaign = await _call(
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
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "combat-gear-phase",
                },
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 2},
                    "participant_ids": [combat_actor["id"], combat_target["id"]],
                    "participant_config": [
                        {
                            "actor_id": combat_actor["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": combat_target["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                        },
                    ],
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "combat-gear-start",
                },
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            combat_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": combat_actor["id"]}},
            )
            combat_request = {
                "campaign_id": campaign["id"],
                "action_id": "combat-acid-object",
                "item_id": "combat-acid-1",
                "intent": "throw",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": combat_actor["id"],
                "expected_actor_revision": combat_actor["revision"],
                "expected_revision": current_campaign["revision"],
                "target_object": object_profile,
                "object_source_ref": object_source_ref,
                "object_reason": "The acid strikes the wooden door during combat.",
                "object_ruling": {
                    "reason": "DM reviewed the door statistics from the module source.",
                    "source_excerpt": "The wooden door has AC 5 and 500 hit points.",
                },
                "idempotency_key": "combat-acid-object",
            }
            bad_combat_request = {
                **combat_request,
                "object_source_ref": {
                    **object_source_ref,
                    "content_sha256": "0" * 64,
                },
                "idempotency_key": "combat-acid-object-invalid-source",
            }
            with pytest.raises(ToolError, match="content_sha256"):
                await _call(server, "adventuring_gear_action", bad_combat_request)
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": combat_actor["id"]}},
            )
            assert unchanged_campaign["revision"] == current_campaign["revision"]
            assert unchanged_campaign["state"]["combat"] == current_campaign["state"]["combat"]
            assert unchanged_campaign["state"].get("random_stream") == current_campaign[
                "state"
            ].get("random_stream")
            assert unchanged_campaign["state"].get("item_spends", []) == current_campaign[
                "state"
            ].get("item_spends", [])
            assert unchanged_actor["revision"] == combat_actor["revision"]
            assert all(
                item["quantity"] == 1
                for item in unchanged_actor["sheet"]["inventory"]["items"]
            )

            combat_result = await _call(
                server,
                "adventuring_gear_action",
                combat_request,
            )
            assert combat_result["combat"]["active"] is True
            actor_combatant = next(
                item
                for item in combat_result["combat"]["combatants"]
                if item["actor_id"] == combat_actor["id"]
            )
            assert actor_combatant["turn_budget"]["main_action"] == 0
            assert combat_result["adventuring_gear"]["item_id"] == "combat-acid-1"
            assert await _call(server, "adventuring_gear_action", combat_request) == (
                combat_result
            )

            spent_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            spent_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": combat_actor["id"]}},
            )
            rejected_fire = {
                **combat_request,
                "action_id": "combat-fire-object-no-action",
                "item_id": "combat-fire-1",
                "intent": "throw",
                "expected_actor_revision": spent_actor["revision"],
                "expected_revision": spent_campaign["revision"],
                "idempotency_key": "combat-fire-object-no-action",
            }
            with pytest.raises(ToolError, match="action payment|legal action"):
                await _call(server, "adventuring_gear_action", rejected_fire)
            unchanged_after_rejection = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            actor_after_rejection = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": combat_actor["id"]}},
            )
            assert unchanged_after_rejection["revision"] == spent_campaign["revision"]
            assert unchanged_after_rejection["state"]["combat"] == spent_campaign["state"][
                "combat"
            ]
            assert unchanged_after_rejection["state"]["scene_objects"] == spent_campaign[
                "state"
            ]["scene_objects"]
            assert unchanged_after_rejection["state"]["item_spends"] == spent_campaign[
                "state"
            ]["item_spends"]
            assert unchanged_after_rejection["state"]["random_stream"] == spent_campaign[
                "state"
            ]["random_stream"]
            assert actor_after_rejection["revision"] == spent_actor["revision"]
            assert any(
                item["id"] == "combat-fire-1" and item["quantity"] == 1
                for item in actor_after_rejection["sheet"]["inventory"]["items"]
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
