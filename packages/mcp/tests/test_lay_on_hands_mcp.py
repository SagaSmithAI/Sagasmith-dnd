from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import add_effect, default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server

LAY_ON_HANDS_ID = "dnd5e.content.srd2014.feature.paladin-lay-on-hands"


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def _paladin_sheet() -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 5,
        "classes": [{"name": "Paladin", "level": 5, "hit_die": 10}],
    }
    sheet["resources"]["lay_on_hands"] = {
        "label": "Lay on Hands",
        "value": 25,
        "max": 25,
        "recovers_on": "long_rest",
        "source_key": "Paladin",
    }
    sheet["content"]["features"] = [
        {
            "id": LAY_ON_HANDS_ID,
            "name": "Lay on Hands",
            "source_key": "Paladin",
            "resource_key": "lay_on_hands",
            "activation": {"type": "action", "cost": 1, "trigger": ""},
            "mechanic_refs": ["dnd5e.core.activity.lay_on_hands"],
        }
    ]
    return sheet


def _target_sheet() -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 5, "max": 20, "temp": 0}
    sheet, _ = add_effect(
        sheet,
        {
            "id": "poison-a",
            "name": "Poison",
            "kind": "poison",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
        },
    )
    return sheet


def test_noncombat_lay_on_hands_heals_and_cures_with_atomic_revisions(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Lay on Hands", "edition": "2014", "idempotency_key": "campaign"},
        )
        paladin = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Paladin",
                    "sheet": _paladin_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "paladin",
            },
        )
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Target",
                    "sheet": _target_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "play",
            },
        )
        del phase
        paladin_view = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": paladin["id"]},
                "principal_id": "system:local",
            },
        )
        target_view = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": target["id"]},
                "principal_id": "system:local",
            },
        )
        healed = await _call(
            server,
            "character_action",
            {
                "character_id": paladin["id"],
                "action": "use_activity",
                "payload": {
                    "activity_id": LAY_ON_HANDS_ID,
                    "declaration": {
                        "target_id": target["id"],
                        "mode": "heal",
                        "amount": 10,
                        "expected_revision": target_view["revision"],
                        "within_touch": True,
                    },
                },
                "principal_id": "system:local",
                "expected_revision": paladin_view["revision"],
                "idempotency_key": "heal",
            },
        )
        assert healed["result"]["core_effect"]["pool_remaining"] == 15
        assert healed["target"]["sheet"]["combat"]["hp"]["value"] == 15
        paladin_view = healed["character"]
        target_view = healed["target"]
        cured = await _call(
            server,
            "character_action",
            {
                "character_id": paladin["id"],
                "action": "use_activity",
                "payload": {
                    "activity_id": LAY_ON_HANDS_ID,
                    "declaration": {
                        "target_id": target["id"],
                        "mode": "cure",
                        "effect_id": "poison-a",
                        "expected_revision": target_view["revision"],
                        "within_touch": True,
                    },
                },
                "principal_id": "system:local",
                "expected_revision": paladin_view["revision"],
                "idempotency_key": "cure",
            },
        )
        assert cured["result"]["core_effect"]["pool_remaining"] == 10
        assert cured["target"]["sheet"]["conditions"] == []

    asyncio.run(exercise())


def test_combat_lay_on_hands_requires_touch_and_pays_action(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Combat Lay on Hands", "edition": "2014", "idempotency_key": "campaign"},
        )
        paladin = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Paladin",
                    "sheet": _paladin_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "paladin",
            },
        )
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Target",
                    "sheet": _target_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "play",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 8, "height_cells": 8},
                "campaign_id": campaign["id"],
                "participant_ids": [paladin["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": paladin["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                        "disposition": "friendly",
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                        "disposition": "friendly",
                    },
                ],
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "start",
            },
        )
        resolved = await _raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": paladin["id"],
                "activity_id": LAY_ON_HANDS_ID,
                "declaration": {"target_id": target["id"], "mode": "heal", "amount": 10},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "heal",
            },
        )
        assert resolved["result"]["core_effect"]["pool_remaining"] == 15
        assert resolved["result"]["core_effect"]["distance_ft"] == 5
        assert resolved["combat"]["combatants"][0]["turn_budget"]["main_action"] == 0

    asyncio.run(exercise())
