from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_SPELL_ID
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


def _weapon() -> dict:
    return {
        "id": "held-sword",
        "name": "Held Sword",
        "kind": "weapon",
        "mechanics": {
            "category": "simple",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "1d8",
            "damage_type": "slashing",
            "properties": [],
        },
    }


def _shield() -> dict:
    return {
        "id": "held-shield",
        "name": "Held Shield",
        "kind": "shield",
        "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
    }


@pytest.mark.fresh_database
def test_sleep_drops_held_items_to_ground_without_automatic_pickup(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path / "seed"),
            auto_seed_rules=True,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Ground drop", "edition": "2014", "idempotency_key": "campaign"},
            )
            spells = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {"campaign_id": campaign["id"], "kind": "spell", "query": "Sleep"},
                },
            )
            sleep = next(item for item in spells if item["id"] == CORE_SLEEP_SPELL_ID)
            caster_sheet = default_character_sheet()
            caster_sheet["progression"]["level"] = 3
            caster_sheet["progression"]["classes"] = [
                {"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}
            ]
            caster_sheet["spellcasting"].update(
                ability="charisma",
                spell_slots={
                    "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"}
                },
            )
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Caster",
                        "sheet": caster_sheet,
                    },
                    "idempotency_key": "caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "sleep-card",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            target_sheet, sword_id = add_inventory_item(target_sheet, _weapon())
            target_sheet, shield_id = add_inventory_item(target_sheet, _shield())
            target_sheet = equip_inventory_item(target_sheet, sword_id, "main_hand")
            target_sheet = equip_inventory_item(target_sheet, shield_id, "shield")
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Target",
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
            phase = await _call(
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
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 10, "height_cells": 10},
                    "campaign_id": campaign["id"],
                    "participant_ids": [caster["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": caster["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                            "disposition": "hostile",
                        },
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "combat",
                },
            )
            cast = await _call(
                server,
                "combat_cast_spell",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": caster["id"],
                    "spell_id": sleep["id"],
                    "cast_level": 1,
                    "declaration": {
                        "origin": {"x": 1, "y": 0},
                        "target_contexts": [
                            {"target_id": caster["id"], "cover": "none"},
                            {"target_id": target["id"], "cover": "none"},
                        ],
                    },
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "sleep",
                },
            )
            targets = {item["target_id"]: item for item in cast["targets"]}
            assert targets[target["id"]]["affected"] is True
            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert "unconscious" in target_after["sheet"]["conditions"]
            assert target_after["sheet"]["inventory"]["equipment_slots"]["main_hand"] == sword_id
            assert target_after["sheet"]["inventory"]["equipment_slots"]["shield"] == shield_id
            campaign_after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert campaign_after["state"].get("ground_items")
            ground = campaign_after["state"]["ground_items"][0]
            assert ground["root_item_id"] == sword_id
            with pytest.raises(ToolError):
                await _call(
                    server,
                    "combat_common_action",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": caster["id"],
                        "action": "pickup_ground",
                        "payload": {"ground_id": ground["id"], "slot": "main_hand"},
                        "expected_revision": cast["campaign_revision"],
                        "idempotency_key": "pickup",
                    },
                )
        finally:
            close_server(server)

    asyncio.run(exercise())
