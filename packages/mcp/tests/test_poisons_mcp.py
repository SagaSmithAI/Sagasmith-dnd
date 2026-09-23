from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.breathing import begin_holding_breath
from sagasmith_dnd.character_schema import add_effect, default_character_sheet
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.poisons import HOUR_TICKS, build_poison_effect

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


def _poison_item(poison_id: str) -> dict:
    workspace = Path(__file__).resolve().parents[3]
    _manifest, artifacts = build_srd2014_content(workspace / "skills")
    artifact = next(
        item
        for item in artifacts
        if item.get("id") == f"dnd5e.content.srd2014.item.poison.{poison_id}"
    )
    result = deepcopy(artifact["card"]["inventory_template"])
    result["id"] = f"dose-{poison_id}"
    result["quantity"] = 2
    return result


async def _create_actor(
    server,
    campaign_id: str,
    name: str,
    item: dict,
    extra_items: list[dict] | None = None,
) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["inventory"]["items"] = [item, *(extra_items or [])]
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": name, "sheet": sheet},
            "idempotency_key": f"actor-{name}",
        },
    )


def test_ingested_poison_dose_and_delivery_replay_are_atomic(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Poison replay", "edition": "2014", "idempotency_key": "campaign"},
            )
            source = await _create_actor(
                server, campaign["id"], "source", _poison_item("assassins_blood")
            )
            target = await _create_actor(
                server, campaign["id"], "target", _poison_item("assassins_blood")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "source_actor_id": source["id"],
                "target_ids": [target["id"]],
                "dose_item_id": "dose-assassins_blood",
                "swallowed_entire_dose": True,
                "expected_revision": current["revision"],
                "idempotency_key": "deliver-assassins-blood",
            }
            delivered = await _call(server, "combat_poison_expose", request)
            assert delivered["status"] == "committed"
            assert delivered["dose_consumed"] is True
            assert delivered["targets"][0]["target_actor_id"] == target["id"]
            assert delivered["targets"][0]["rule_receipts"]

            source_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": source["id"]}},
            )
            dose_after = next(
                item
                for item in source_after["sheet"]["inventory"]["items"]
                if item["id"] == "dose-assassins_blood"
            )
            assert dose_after["quantity"] == 1

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "minute",
                        "count": 1,
                        "expected_elapsed_ticks": 10,
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "advance-after-poison",
                },
            )
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "combat_poison_expose", request) == delivered
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_truth_serum_constraint_expires_and_exact_delivery_replays_after_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Truth Serum",
                    "edition": "2014",
                    "random_seed": "truth-serum-constraint",
                    "idempotency_key": "campaign",
                },
            )
            source = await _create_actor(
                server, campaign["id"], "source", _poison_item("truth_serum")
            )
            target = await _create_actor(
                server, campaign["id"], "target", _poison_item("truth_serum")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "source_actor_id": source["id"],
                "target_ids": [target["id"]],
                "dose_item_id": "dose-truth_serum",
                "swallowed_entire_dose": True,
                "expected_revision": current["revision"],
                "idempotency_key": "truth-serum-delivery",
            }
            delivered = await _call(server, "combat_poison_expose", request)
            result = delivered["targets"][0]
            assert result["save"]["success"] is False
            assert result["effect"]["metadata"]["poison_state"][
                "truth_constraint_kind"
            ] == "cannot_knowingly_speak_a_lie"

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "hour",
                        "count": 1,
                        "expected_elapsed_ticks": HOUR_TICKS,
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "expire-truth-serum",
                },
            )
            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert "poisoned" not in target_after["sheet"]["conditions"]
            effect = next(
                item
                for item in target_after["sheet"]["effects"]
                if item["id"] == result["effect"]["id"]
            )
            assert effect["active"] is False

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "combat_poison_expose", request) == delivered
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_basic_poison_coats_three_ammunition_and_expires_at_exact_minute(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Basic poison", "edition": "2014", "idempotency_key": "campaign"},
            )
            arrows = {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 5}
            actor = await _create_actor(
                server,
                campaign["id"],
                "archer",
                _poison_item("basic_poison"),
                [arrows],
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="at most three"):
                await _call(
                    server,
                    "combat_poison_coat",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "dose_item_id": "dose-basic_poison",
                        "object_item_id": "arrows",
                        "ammunition_count": 4,
                        "expected_revision": current["revision"],
                        "idempotency_key": "invalid-four-arrows",
                    },
                )
            result = await _call(
                server,
                "combat_poison_coat",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "dose_item_id": "dose-basic_poison",
                    "object_item_id": "arrows",
                    "ammunition_count": 3,
                    "expected_revision": current["revision"],
                    "idempotency_key": "coat-three-arrows",
                },
            )
            assert result["action_cost"] == "action"
            assert result["action_paid"] is False
            assert result["consumed_dose_item_id"] == "dose-basic_poison"
            assert len(result["coatings"]) == 3
            object_ids = result["object_item_ids"]
            assert len(object_ids) == len(set(object_ids)) == 3
            assert {item["object_item_id"] for item in result["coatings"]} == set(object_ids)
            assert all(item["poison_id"] == "basic_poison" for item in result["coatings"])
            assert all(item["active"] for item in result["coatings"])

            actor_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            inventory = actor_after["sheet"]["inventory"]["items"]
            assert next(
                item for item in inventory if item["id"] == "dose-basic_poison"
            )["quantity"] == 1
            assert next(item for item in inventory if item["id"] == "arrows")["quantity"] == 2
            assert {item["id"] for item in inventory if item["id"] in object_ids} == set(object_ids)

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="no source-defined wash-off"):
                await _call(
                    server,
                    "combat_poison_wash",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "object_item_id": object_ids[0],
                        "expected_revision": current["revision"],
                        "idempotency_key": "basic-poison-wash-rejected",
                    },
                )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            advanced = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "minute", "count": 1, "expected_elapsed_ticks": 10},
                    "expected_revision": current["revision"],
                    "idempotency_key": "expire-basic-poison",
                },
            )
            expired = [
                item
                for item in advanced["poison_events"]
                if item.get("kind") == "coating_expired"
            ]
            assert {item["coating_id"] for item in expired} == {
                item["id"] for item in result["coatings"]
            }
            assert all(item["ended"] for item in expired)
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_basic_poison_application_pays_one_combat_action_atomically(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Basic poison action", "edition": "2014", "idempotency_key": "campaign"},
            )
            arrows = {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 5}
            actor = await _create_actor(
                server,
                campaign["id"],
                "archer",
                _poison_item("basic_poison"),
                [arrows],
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "target",
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
                    "battle_map": {"width_cells": 5, "height_cells": 5},
                    "participant_ids": [actor["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": actor["id"],
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
                    "expected_revision": current["revision"],
                    "idempotency_key": "start",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            applied = await _call(
                server,
                "combat_poison_coat",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "dose_item_id": "dose-basic_poison",
                    "object_item_id": "arrows",
                    "expected_revision": current["revision"],
                    "idempotency_key": "coat-during-combat",
                },
            )
            assert applied["action_paid"] is True
            assert applied["action_cost"] == "action"

            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError, match="action payment"):
                await _call(
                    server,
                    "combat_poison_coat",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "dose_item_id": "dose-basic_poison",
                        "object_item_id": "arrows",
                        "expected_revision": after["revision"],
                        "idempotency_key": "second-coat-same-turn",
                    },
                )
            actor_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert next(
                item
                for item in actor_after["sheet"]["inventory"]["items"]
                if item["id"] == "dose-basic_poison"
            )["quantity"] == 1
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_basic_poison_triggers_on_weapon_hit_with_zero_physical_damage_and_repeats(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))

        async def raw(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result

        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Basic poison hit", "edition": "2014", "idempotency_key": "campaign"},
            )
            attacker_sheet = default_character_sheet()
            attacker_sheet["edition"] = "2014"
            attacker_sheet["abilities"]["strength"]["score"] = 18
            attacker_sheet["inventory"]["items"] = [
                _poison_item("basic_poison"),
                {
                    "id": "sword",
                    "name": "Slashing sword",
                    "kind": "weapon",
                    "equipped": True,
                    "equipped_slot": "main_hand",
                    "mechanics": {
                        "attack_type": "melee",
                        "attack_ability": "strength",
                        "damage_formula": "1d8",
                        "damage_type": "slashing",
                        "properties": [],
                        "proficient": True,
                    },
                },
                {
                    "id": "hammer",
                    "name": "Bludgeoning hammer",
                    "kind": "weapon",
                    "mechanics": {
                        "attack_type": "melee",
                        "attack_ability": "strength",
                        "damage_formula": "1d6",
                        "damage_type": "bludgeoning",
                        "properties": [],
                        "proficient": True,
                    },
                },
            ]
            attacker_sheet["inventory"]["equipment_slots"]["main_hand"] = "sword"
            attacker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "swordsman",
                        "sheet": attacker_sheet,
                    },
                    "idempotency_key": "attacker",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            target_sheet["combat"]["ac"]["override"] = 1
            target_sheet["traits"]["immunities"] = ["slashing"]
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "immune target",
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
            with pytest.raises(ToolError, match="piercing or slashing"):
                await _call(
                    server,
                    "combat_poison_coat",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": attacker["id"],
                        "dose_item_id": "dose-basic_poison",
                        "object_item_id": "hammer",
                        "expected_revision": current["revision"],
                        "idempotency_key": "reject-bludgeoning-weapon",
                    },
                )
            coated = await _call(
                server,
                "combat_poison_coat",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": attacker["id"],
                    "dose_item_id": "dose-basic_poison",
                    "object_item_id": "sword",
                    "expected_revision": current["revision"],
                    "idempotency_key": "coat-sword",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            started = await raw(
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 5, "height_cells": 5},
                    "participant_ids": [attacker["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": attacker["id"],
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
                    "expected_revision": current["revision"],
                    "idempotency_key": "start",
                },
            )
            revision = started["campaign_revision"]
            hit_events = []
            for index in range(4):
                attacked = await raw(
                    "combat_resolve_attack",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": attacker["id"],
                        "target_id": target["id"],
                        "action": {"weapon_id": "sword", "attack_mode": "melee"},
                        "expected_revision": revision,
                        "idempotency_key": f"attack-{index}",
                    },
                )
                revision = attacked["campaign_revision"]
                if attacked["result"]["hit"]:
                    assert attacked["result"]["damage"]["applied_amount"] == 0
                    poison_result = attacked["result"]["poison"]
                    assert poison_result["trigger"] == "weapon_hit"
                    assert poison_result["condition_applied"] is False
                    if poison_result["damage"] is not None:
                        assert poison_result["damage"]["expression"] == "1d4"
                    hit_events.append(poison_result)
                    if len(hit_events) == 2:
                        break
                ended_attack = await raw(
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": attacker["id"],
                        "expected_revision": revision,
                        "idempotency_key": f"end-attacker-{index}",
                    },
                )
                ended_target = await raw(
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": target["id"],
                        "expected_revision": ended_attack["campaign_revision"],
                        "idempotency_key": f"end-target-{index}",
                    },
                )
                revision = ended_target["campaign_revision"]
            assert len(hit_events) == 2
            assert coated["coating"]["object_item_id"] == "sword"
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_basic_poisoned_ammunition_is_consumed_without_delivery_on_miss(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))

        async def raw(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result

        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Poisoned miss", "edition": "2014", "idempotency_key": "campaign"},
            )
            arrows = {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 5}
            bow = {
                "id": "bow",
                "name": "Shortbow",
                "kind": "weapon",
                "equipped": True,
                "equipped_slot": "main_hand",
                "mechanics": {
                    "attack_type": "ranged",
                    "attack_ability": "dexterity",
                    "damage_formula": "1d6",
                    "damage_type": "piercing",
                    "properties": ["ammunition", "two_handed"],
                    "normal_range_ft": 80,
                    "long_range_ft": 320,
                    "ammunition_item_id": "arrows",
                    "proficient": True,
                },
            }
            attacker_sheet = default_character_sheet()
            attacker_sheet["edition"] = "2014"
            attacker_sheet["inventory"]["items"] = [_poison_item("basic_poison"), arrows, bow]
            attacker_sheet["inventory"]["equipment_slots"]["main_hand"] = "bow"
            attacker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "archer",
                        "sheet": attacker_sheet,
                    },
                    "idempotency_key": "archer",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            target_sheet["combat"]["ac"]["override"] = 100
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "distant target",
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
            coated = await _call(
                server,
                "combat_poison_coat",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": attacker["id"],
                    "dose_item_id": "dose-basic_poison",
                    "object_item_id": "arrows",
                    "expected_revision": current["revision"],
                    "idempotency_key": "coat-arrow",
                },
            )
            ammunition_id = coated["object_item_ids"][0]
            current_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": attacker["id"]}},
            )
            updated_sheet = current_actor["sheet"]
            bow_item = next(
                item for item in updated_sheet["inventory"]["items"] if item["id"] == "bow"
            )
            bow_item["mechanics"]["ammunition_item_id"] = ammunition_id
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            updated_actor = await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": attacker["id"],
                    "sheet": updated_sheet,
                    "expected_revision": current_actor["revision"],
                    "idempotency_key": "bind-coated-arrow",
                },
            )
            started = await raw(
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 10, "height_cells": 4},
                    "participant_ids": [attacker["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": attacker["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 5, "y": 0},
                            "disposition": "hostile",
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start",
                },
            )
            attacked = await raw(
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": attacker["id"],
                    "target_id": target["id"],
                    "action": {
                        "weapon_id": "bow",
                        "attack_mode": "ranged",
                        "ammunition_item_id": ammunition_id,
                    },
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "miss-poisoned-arrow",
                },
            )
            assert attacked["result"]["hit"] is False
            assert attacked["result"]["poison"]["status"] == "not_delivered"
            assert attacked["result"]["poison"]["reason"] == "ammunition_consumed_on_miss"
            consumed = attacked["result"]["ammunition"]
            assert consumed["item_id"] == ammunition_id
            assert consumed["remaining"] == 0
            assert updated_actor["revision"] == current_actor["revision"] + 1
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_midnight_tears_self_ingestion_merges_dose_and_effect_write(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Midnight merge", "edition": "2014", "idempotency_key": "campaign"},
            )
            actor = await _create_actor(
                server, campaign["id"], "self", _poison_item("midnight_tears")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            clock = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_set",
                    "payload": {"day": 3, "hour": 23, "minute": 0, "label": "Test"},
                    "expected_revision": current["revision"],
                    "idempotency_key": "set-clock",
                },
            )
            result = await _call(
                server,
                "combat_poison_expose",
                {
                    "campaign_id": campaign["id"],
                    "source_actor_id": actor["id"],
                    "target_ids": [actor["id"]],
                    "dose_item_id": "dose-midnight_tears",
                    "swallowed_entire_dose": True,
                    "expected_revision": clock["campaign_revision"],
                    "idempotency_key": "self-midnight",
                },
            )
            assert result["targets"][0]["status"] == "pending_midnight"

            updated = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert any(
                effect.get("metadata", {}).get("poison_state", {}).get("poison_id")
                == "midnight_tears"
                for effect in updated["sheet"]["effects"]
            )
            assert (
                next(
                    item
                    for item in updated["sheet"]["inventory"]["items"]
                    if item["id"] == "dose-midnight_tears"
                )["quantity"]
                == 1
            )
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_contact_poison_requires_exposed_skin_and_no_exposure_replays(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Contact poison", "edition": "2014", "idempotency_key": "campaign"},
            )
            ring = {
                "id": "ring",
                "name": "Ring",
                "kind": "equipment",
                "source_key": "test:ring",
            }
            actor = await _create_actor(
                server,
                campaign["id"],
                "actor",
                _poison_item("crawler_mucus"),
                [ring],
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            coated = await _call(
                server,
                "combat_poison_coat",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "dose_item_id": "dose-crawler_mucus",
                    "object_item_id": "ring",
                    "expected_revision": current["revision"],
                    "idempotency_key": "coat-ring",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            exposure = {
                "campaign_id": campaign["id"],
                "source_actor_id": actor["id"],
                "target_ids": [actor["id"]],
                "coating_id": coated["coating"]["id"],
                "exposed_skin_touch": False,
                "expected_revision": current["revision"],
                "idempotency_key": "covered-touch",
            }
            no_exposure = await _call(server, "combat_poison_expose", exposure)
            assert no_exposure == {
                "status": "no_exposure",
                "poison_id": "crawler_mucus",
                "dose_consumed": False,
            }

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_poison_wash",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "object_item_id": "ring",
                    "expected_revision": current["revision"],
                    "idempotency_key": "wash-ring",
                },
            )
            assert await _call(server, "combat_poison_expose", exposure) == no_exposure
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_pale_tincture_clock_events_repeat_at_each_anchored_day(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Pale Tincture clock",
                    "edition": "2014",
                    "random_seed": "pale-tincture-clock",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["constitution"]["score"] = 1
            sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            sheet["effects"] = [
                build_poison_effect(
                    "pale_tincture",
                    effect_id="pale-dose",
                    elapsed_ticks=0,
                    save_succeeded=False,
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Pale target",
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
            first_request = {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {
                    "period": "day",
                    "count": 1,
                    "expected_elapsed_ticks": 14400,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "pale-day-one",
            }
            first = await _call(server, "campaign_change", first_request)
            assert first["poison_events"][0]["due_elapsed_ticks"] == 14400
            assert first["poison_events"][0]["damage"]["expression"] == "1d6"
            assert await _call(server, "campaign_change", first_request) == first

            updated = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(item for item in updated["sheet"]["effects"] if item["id"] == "pale-dose")
            poison_state = effect["metadata"]["poison_state"]
            assert poison_state["next_due_elapsed_ticks"] == 28800
            assert poison_state["healing_locked_damage_remaining"] > 0
            assert updated["sheet"]["combat"]["hp"]["value"] < 20

            second = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "day",
                        "count": 1,
                        "expected_elapsed_ticks": 28800,
                    },
                    "expected_revision": first["campaign_revision"],
                    "idempotency_key": "pale-day-two",
                },
            )
            assert second["poison_events"][0]["due_elapsed_ticks"] == 28800
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_midnight_tears_fires_at_anchored_calendar_midnight(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Midnight boundary",
                    "edition": "2014",
                    "random_seed": "midnight-boundary",
                    "idempotency_key": "campaign",
                },
            )
            actor = await _create_actor(
                server, campaign["id"], "target", _poison_item("midnight_tears")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            clock = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_set",
                    "payload": {
                        "day": 3,
                        "hour": 23,
                        "minute": 59,
                        "label": "Boundary",
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "set-clock",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            exposure = await _call(
                server,
                "combat_poison_expose",
                {
                    "campaign_id": campaign["id"],
                    "source_actor_id": actor["id"],
                    "target_ids": [actor["id"]],
                    "dose_item_id": "dose-midnight_tears",
                    "swallowed_entire_dose": True,
                    "expected_revision": current["revision"],
                    "idempotency_key": "ingest-midnight",
                },
            )
            pending = exposure["targets"][0]["effect"]
            assert pending["metadata"]["poison_state"]["midnight_due_elapsed_ticks"] == 10
            assert pending["active"] is True

            after = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "minute",
                        "count": 1,
                        "expected_elapsed_ticks": 10,
                    },
                    "expected_revision": exposure["campaign_revision"],
                    "idempotency_key": "reach-midnight",
                },
            )
            assert after["poison_events"][0]["due_elapsed_ticks"] == 10
            assert after["poison_events"][0]["poison_id"] == "midnight_tears"
            updated = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert not any(
                item.get("metadata", {}).get("poison_state", {}).get("poison_id")
                == "midnight_tears"
                for item in updated["sheet"]["effects"]
            )
            assert clock["world_time"]["calendar_offset_ticks"] is not None
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_burnt_othur_saves_and_damage_settle_at_target_turn_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Burnt Othur turn",
                    "edition": "2014",
                    "random_seed": "burnt-othur-turn",
                    "idempotency_key": "campaign",
                },
            )
            attacker_sheet = default_character_sheet()
            attacker_sheet["edition"] = "2014"
            attacker_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            attacker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "First turn",
                        "sheet": attacker_sheet,
                    },
                    "idempotency_key": "attacker",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["abilities"]["constitution"]["score"] = 1
            target_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            target_sheet["effects"] = [
                build_poison_effect(
                    "burnt_othur_fumes",
                    effect_id="burnt-othur",
                    source_actor_id=attacker["id"],
                    elapsed_ticks=0,
                    save_succeeded=False,
                )
            ]
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Poisoned turn",
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
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "participant_ids": [attacker["id"], target["id"]],
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
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-start",
                },
            )
            ended = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": attacker["id"],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "end-first-turn",
                },
            )
            event = next(
                item for item in ended["poison_events"] if item["poison_id"] == "burnt_othur_fumes"
            )
            assert event["phase"] == "start_of_turn"
            if event["save"]["success"]:
                assert event["damage"] is None
            else:
                assert event["damage"]["expression"] == "1d6"
            assert (
                await _call(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": attacker["id"],
                        "expected_revision": started["campaign_revision"],
                        "idempotency_key": "end-first-turn",
                    },
                )
                == ended
            )
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_crawler_mucus_settles_its_save_at_the_target_turn_end(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Crawler Mucus end turn",
                    "edition": "2014",
                    "random_seed": "crawler-mucus-end-turn",
                    "idempotency_key": "campaign",
                },
            )
            source = await _create_actor(
                server,
                campaign["id"],
                "source",
                _poison_item("crawler_mucus"),
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["abilities"]["constitution"]["score"] = 1
            target_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            target_sheet, _ = add_effect(
                target_sheet,
                build_poison_effect(
                    "crawler_mucus",
                    effect_id="crawler-mucus-instance",
                    source_actor_id=source["id"],
                    elapsed_ticks=0,
                    save_succeeded=False,
                ),
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Crawler target",
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
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "participant_ids": [target["id"], source["id"]],
                    "participant_config": [
                        {
                            "actor_id": target["id"],
                            "initiative": 20,
                            "position": {"x": 1, "y": 0},
                        },
                        {
                            "actor_id": source["id"],
                            "initiative": 10,
                            "position": {"x": 0, "y": 0},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-start",
                },
            )
            ended = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": target["id"],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "end-crawler-target-turn",
                },
            )
            event = next(
                item
                for item in ended["poison_events"]
                if item["poison_id"] == "crawler_mucus"
            )
            assert event["phase"] == "end_of_turn"
            assert event["save"]["success"] is False
            assert event["ended"] is False

            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            active = next(
                effect
                for effect in target_after["sheet"]["effects"]
                if effect["id"] == "crawler-mucus-instance"
            )
            assert active["active"] is True
            assert {"poisoned", "paralyzed"} <= set(target_after["sheet"]["conditions"])
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_inhaled_poison_uses_the_grid_cube_even_while_target_holds_breath(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Inhaled poison cube",
                    "edition": "2014",
                    "random_seed": "inhaled-poison-hold-breath",
                    "idempotency_key": "campaign",
                },
            )
            source = await _create_actor(
                server,
                campaign["id"],
                "source",
                _poison_item("burnt_othur_fumes"),
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["abilities"]["constitution"]["score"] = 1
            target_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            target_sheet = begin_holding_breath(target_sheet)["sheet"]
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Breath holder",
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
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "participant_ids": [source["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": source["id"],
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
            exposure = await _call(
                server,
                "combat_poison_expose",
                {
                    "campaign_id": campaign["id"],
                    "source_actor_id": source["id"],
                    "target_ids": [],
                    "dose_item_id": "dose-burnt_othur_fumes",
                    "cube_origin": [1, 0],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "release-burnt-othur-cube",
                },
            )
            assert exposure["delivery"] == "inhaled"
            assert exposure["dose_consumed"] is True
            assert exposure["cloud_dissipated"] is True
            assert [item["target_actor_id"] for item in exposure["targets"]] == [target["id"]]
            target_result = exposure["targets"][0]
            assert target_result["save"]["dc"] == 13
            if target_result["save"]["success"]:
                assert target_result["damage"] is None
                assert target_result["effect"] is None
            else:
                assert target_result["damage"]["expression"] == "3d6"
                assert target_result["effect"] is not None

            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert any(
                effect.get("name") == "Holding Breath" and effect.get("active")
                for effect in target_after["sheet"]["effects"]
            )
            poison_effects = [
                effect
                for effect in target_after["sheet"]["effects"]
                if effect.get("metadata", {}).get("poison_state", {}).get("poison_id")
                == "burnt_othur_fumes"
            ]
            assert bool(poison_effects) is (not target_result["save"]["success"])
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_action_shake_wakes_poison_rider_without_ending_the_poison(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Poison wake", "edition": "2014", "idempotency_key": "campaign"},
            )
            shaker_sheet = default_character_sheet()
            shaker_sheet["edition"] = "2014"
            shaker_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            shaker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Shaker",
                        "sheet": shaker_sheet,
                    },
                    "idempotency_key": "shaker",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            target_effect = build_poison_effect(
                "essence_of_ether",
                effect_id="ether-dose",
                source_actor_id=shaker["id"],
                elapsed_ticks=0,
                save_succeeded=False,
            )
            target_sheet, _ = add_effect(target_sheet, target_effect)
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Sleeping target",
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
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "participant_ids": [shaker["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": shaker["id"],
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
            shaken = await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": shaker["id"],
                    "action": "shake_poison",
                    "target_id": target["id"],
                    "payload": {"poison_effect_id": "ether-dose"},
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "shake-poison",
                },
            )
            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert "unconscious" not in target_after["sheet"]["conditions"]
            assert "poisoned" in target_after["sheet"]["conditions"]
            active = next(
                item for item in target_after["sheet"]["effects"] if item["id"] == "ether-dose"
            )
            assert active["active"] is True
            assert active["metadata"]["poison_state"]["unconscious_wake_reason"] == "action_shake"
            assert shaken["condition_resolution"]["woken_effect_ids"] == ["ether-dose"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_generic_effect_and_inventory_tools_cannot_forge_or_remove_poison_state(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Poison protections", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            existing = build_poison_effect(
                "pale_tincture",
                effect_id="pale-protected",
                elapsed_ticks=0,
                save_succeeded=False,
            )
            sheet["effects"] = [existing]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Protected target",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            current = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            with pytest.raises(ToolError, match="source-owned poison effects require"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_remove",
                        "payload": {"effect_id": "pale-protected"},
                        "expected_revision": current["revision"],
                        "idempotency_key": "remove-poison",
                    },
                )
            with pytest.raises(ToolError, match="source-bound poison effects require"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_add",
                        "payload": {
                            "effect": build_poison_effect(
                                "essence_of_ether",
                                effect_id="forged-poison",
                                elapsed_ticks=0,
                                save_succeeded=False,
                            )
                        },
                        "expected_revision": current["revision"],
                        "idempotency_key": "add-poison",
                    },
                )
            with pytest.raises(ToolError, match="source-bound poison doses must be added"):
                await _call(
                    server,
                    "inventory_change",
                    {
                        "owner": "character",
                        "action": "add",
                        "owner_id": actor["id"],
                        "payload": {"item": _poison_item("assassins_blood")},
                        "expected_revision": current["revision"],
                        "idempotency_key": "add-dose",
                    },
                )
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert after == current
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_source_ruling_neutralizes_only_the_exact_poison_instance(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Poison neutralization", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            sheet["effects"] = [
                build_poison_effect(
                    "drow_poison",
                    effect_id="drow-dose",
                    source_actor_id="source",
                    elapsed_ticks=0,
                    save_succeeded=False,
                    save_failed_by=5,
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Neutralized target",
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
            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "poison_effect_id": "drow-dose",
                "neutralizer_source_ref": "test:authorized-neutralizer",
                "neutralizer_source_excerpt": (
                    "The approved neutralizer ends this named poison dose."
                ),
                "ruling": {
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "decision": "neutralize_exact_poison_instance",
                    "reason": "The DM adjudicated that this exact source was neutralized.",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "neutralize-drow-dose",
            }
            result = await _call(server, "combat_poison_neutralize", request)
            assert result["neutralized_effect_id"] == "drow-dose"
            assert result["neutralization_source"]["ruling"]["committed"] is True
            assert await _call(server, "combat_poison_neutralize", request) == result

            updated = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert "poisoned" not in updated["sheet"]["conditions"]
            assert "unconscious" not in updated["sheet"]["conditions"]
            assert not any(item["id"] == "drow-dose" for item in updated["sheet"]["effects"])
        finally:
            close_server(server)

    asyncio.run(exercise())
