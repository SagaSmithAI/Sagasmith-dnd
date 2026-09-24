from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


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


def test_holy_water_only_damages_fiends_and_undead_and_settles_source_plan(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Holy water",
                    "edition": "2014",
                    "random_seed": "holy-water-hit",
                    "idempotency_key": "campaign",
                },
            )
            attacker_sheet = default_character_sheet()
            attacker_sheet["edition"] = "2014"
            attacker_sheet["abilities"]["strength"]["score"] = 30
            attacker_sheet["inventory"]["items"] = [
                {
                    "id": "holy-water-1",
                    "name": "Holy Water (flask)",
                    "source_key": "dnd5e.content.srd2014.item.holy-water-flask",
                    "quantity": 2,
                }
            ]
            attacker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Holy water bearer",
                        "sheet": attacker_sheet,
                    },
                    "idempotency_key": "attacker",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["progression"]["species"] = "construct"
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Construct",
                        "sheet": target_sheet,
                    },
                    "idempotency_key": "target",
                },
            )
            fiend_sheet = default_character_sheet()
            fiend_sheet["edition"] = "2014"
            fiend_sheet["progression"]["species"] = "fiend"
            fiend = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Fiend",
                        "sheet": fiend_sheet,
                    },
                    "idempotency_key": "target-fiend",
                },
            )
            undead_sheet = default_character_sheet()
            undead_sheet["edition"] = "2014"
            undead_sheet["progression"]["species"] = "undead"
            undead = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Undead",
                        "sheet": undead_sheet,
                    },
                    "idempotency_key": "target-undead",
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
                    "idempotency_key": "phase",
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
                    "battle_map": {"width_cells": 8, "height_cells": 2},
                    "participant_ids": [
                        attacker["id"],
                        target["id"],
                        fiend["id"],
                        undead["id"],
                    ],
                    "participant_config": [
                        {
                            "actor_id": attacker["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                        },
                        {
                            "actor_id": fiend["id"],
                            "initiative": 5,
                            "position": {"x": 5, "y": 0},
                        },
                        {
                            "actor_id": undead["id"],
                            "initiative": 1,
                            "position": {"x": 2, "y": 0},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            attacker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            non_fiend_request = {
                "campaign_id": campaign["id"],
                "action_id": "holy-water-construct",
                "item_id": "holy-water-1",
                "intent": "throw",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": attacker["id"],
                "target_actor_id": target["id"],
                "expected_actor_revision": attacker["revision"],
                "expected_target_revision": target["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "holy-water-construct",
            }
            fiend = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": fiend["id"]}},
            )
            too_far = {
                **non_fiend_request,
                "action_id": "holy-water-out-of-range",
                "target_actor_id": fiend["id"],
                "expected_target_revision": fiend["revision"],
                "idempotency_key": "holy-water-out-of-range",
            }
            with pytest.raises(ToolError, match="outside weapon range"):
                await _call(server, "adventuring_gear_action", too_far)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            unchanged_attacker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            assert unchanged["revision"] == current["revision"]
            assert unchanged["state"]["combat"] == current["state"]["combat"]
            assert unchanged["state"].get("item_spends", []) == current["state"].get(
                "item_spends", []
            )
            assert unchanged_attacker["revision"] == attacker["revision"]
            assert unchanged_attacker["sheet"]["inventory"]["items"][0]["quantity"] == 2

            target_hp_before = target["sheet"]["combat"]["hp"]["value"]
            non_fiend_result = await _call(
                server, "adventuring_gear_action", non_fiend_request
            )
            assert non_fiend_result["hit"] is True
            assert non_fiend_result.get("damage") is None
            assert non_fiend_result["attack_payment"]
            committed_non_fiend = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            spent_non_fiend_attacker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            spent_non_fiend_target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert spent_non_fiend_target["sheet"]["combat"]["hp"]["value"] == (
                target_hp_before
            )
            actor_combatant = next(
                item
                for item in committed_non_fiend["state"]["combat"]["combatants"]
                if item["actor_id"] == attacker["id"]
            )
            assert actor_combatant["turn_budget"]["main_action"] == 0
            remaining_water = next(
                item
                for item in spent_non_fiend_attacker["sheet"]["inventory"]["items"]
                if item["id"] == "holy-water-1"
            )
            assert remaining_water["quantity"] == 1
            non_fiend_spend = next(
                item
                for item in committed_non_fiend["state"]["item_spends"]
                if item.get("id") == "holy-water-construct"
            )
            assert non_fiend_spend["quantity"] == 1
            assert non_fiend_spend["rule_plan"]["effect"]["damage"] == "2d6"
            assert non_fiend_spend["rule_plan"]["effect"]["damage_type"] == "radiant"
            replayed_non_fiend = await _call(
                server, "adventuring_gear_action", non_fiend_request
            )
            assert replayed_non_fiend == non_fiend_result
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_replay["revision"] == committed_non_fiend["revision"]

            for index, turn_actor_id in enumerate(
                [attacker["id"], target["id"], fiend["id"], undead["id"]]
            ):
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                await _call(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": turn_actor_id,
                        "expected_revision": current["revision"],
                        "idempotency_key": f"advance-holy-water-round-{index}",
                    },
                )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            attacker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            undead = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": undead["id"]}},
            )

            request = {
                **non_fiend_request,
                "action_id": "holy-water-fiend",
                "target_actor_id": undead["id"],
                "expected_actor_revision": attacker["revision"],
                "expected_target_revision": undead["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "holy-water-fiend",
            }
            result = await _call(server, "adventuring_gear_action", request)
            gear_result = result["adventuring_gear"]
            assert gear_result["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert gear_result["rule_plan"]["source_key"] == (
                "dnd5e.content.srd2014.item.holy-water-flask"
            )
            assert gear_result["rule_plan"]["effect"]["damage"] == "2d6"
            assert gear_result["rule_plan"]["effect"]["damage_type"] == "radiant"
            assert result["hit"] is True
            assert result["damage"]["expression"] == "2d6"
            spent_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            spend = next(
                item
                for item in spent_campaign["state"]["item_spends"]
                if item.get("id") == "holy-water-fiend"
            )
            assert spend["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert spend["rule_plan"] == gear_result["rule_plan"]
            final_attacker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            assert all(
                item["id"] != "holy-water-1"
                for item in final_attacker["sheet"]["inventory"]["items"]
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
