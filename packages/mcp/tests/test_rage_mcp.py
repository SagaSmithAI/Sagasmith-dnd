import asyncio
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd import rage
from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd_runtime.application import create_runtime

from tests.test_bardic_inspiration_mcp import actor
from tests.test_source_object_authority import setup
from tests.test_structured_spell_mcp import _slot, _spell


async def prepare(tmp_path, level=1, heavy=False, concentration=False):
    world = await setup(tmp_path, random_seed="rage")
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    sheet = default_character_sheet()
    sheet["progression"].update(
        level=level, classes=[{"name": "Barbarian", "level": level, "hit_die": 12}]
    )
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 30}
    sheet["abilities"]["strength"]["score"] = 16
    sheet["traits"]["proficiencies"].update(weapons=["simple weapons"], armor=["heavy armor"])
    sheet, wid = add_inventory_item(
        sheet,
        {
            "id": "axe",
            "name": "Axe",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "slashing",
            },
        },
    )
    sheet = equip_inventory_item(sheet, wid, "main_hand")
    if heavy:
        sheet, aid = add_inventory_item(
            sheet,
            {
                "id": "plate",
                "name": "Plate",
                "kind": "armor",
                "mechanics": {"category": "heavy", "base_ac": 18, "dexterity_mode": "none"},
            },
        )
        sheet = equip_inventory_item(sheet, aid, "armor")
    if concentration:
        sheet["spellcasting"].update(ability="wisdom", spell_slots=_slot(1))
        sheet["content"]["spells"] = [
            _spell("Guiding Bolt", 1, casting_time="1 action", range_ft=120)
        ]
        sheet, _ = add_effect(
            sheet, {"id": "spell", "name": "Prior concentration", "concentration": True}
        )
    card = await world.call(
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": world.cid, "name": "Barbarian", "sheet": sheet},
            "idempotency_key": "barbarian",
        },
    )
    world.barbarian = card["id"]
    for identifier in [rage.FEATURE, rage.PERSISTENT] if level >= 15 else [rage.FEATURE]:
        await world.call(
            "character_content_apply",
            {
                "character_id": world.barbarian,
                "artifact_id": identifier,
                "selection": {},
                "idempotency_key": identifier,
            },
        )
    await world.call(
        "game_phase",
        {
            "campaign_id": world.cid,
            "action": "set",
            "tool_profile": "play",
            "idempotency_key": "play",
        },
    )
    await world.call(
        "combat_start",
        {
            "campaign_id": world.cid,
            "participant_ids": [world.barbarian, world.aid],
            "participant_config": [
                {
                    "actor_id": world.barbarian,
                    "initiative": 20,
                    "position": {"x": 0, "y": 0},
                    "disposition": "friendly",
                },
                {
                    "actor_id": world.aid,
                    "initiative": 10,
                    "position": {"x": 1, "y": 0},
                    "disposition": "hostile",
                },
            ],
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 20, "height_cells": 20},
            "idempotency_key": "combat",
        },
    )
    return world


async def use(world, key="rage", **declaration):
    return await world.call(
        "combat_use_activity",
        {
            "campaign_id": world.cid,
            "actor_id": world.barbarian,
            "activity_id": rage.FEATURE,
            "declaration": declaration,
            "idempotency_key": key,
        },
    )


async def turn(world, identifier, key):
    return await world.call(
        "combat_end_turn",
        {"campaign_id": world.cid, "actor_id": identifier, "idempotency_key": key},
    )


async def damage(world, amount, key="damage", kind="slashing"):
    return await world.call(
        "combat_hp_change",
        {
            "campaign_id": world.cid,
            "target_id": world.barbarian,
            "action": "damage",
            "payload": {"parts": [{"amount": amount, "damage_type": kind}]},
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize(
    "level,uses", [(1, 2), (3, 3), (6, 4), (9, 4), (12, 5), (15, 5), (16, 5), (17, 6), (20, 0)]
)
def test_source_creation_activation_early_end_and_long_rest(tmp_path, level, uses):
    async def run():
        world = await prepare(tmp_path, level)
        try:
            initial = rage.feature((await actor(world, world.barbarian))["sheet"])["uses"]
            assert initial["max"] == uses and initial["unlimited"] is (level == 20)
            result = await use(world)
            card = await actor(world, world.barbarian)
            assert rage.active(card["sheet"])
            remaining = rage.feature(card["sheet"])["uses"]["value"]
            assert remaining == (uses if level == 20 else uses - 1)
            before = await world.snapshot(), card
            with pytest.raises(Exception, match="bonus|already"):
                await use(world, "duplicate")
            assert (await world.snapshot(), await actor(world, world.barbarian)) == before
            assert (await use(world))["effect"] == result["effect"]
            await turn(world, world.barbarian, "end-turn")
            assert bool(rage.active((await actor(world, world.barbarian))["sheet"])) is (
                level >= 15
            )
            await world.call(
                "combat_end", {"campaign_id": world.cid, "idempotency_key": "end-combat"}
            )
            for rest, minutes in (("short_rest", 60), ("long_rest", 480)):
                current = await actor(world, world.barbarian)
                await world.call(
                    "campaign_change",
                    {
                        "campaign_id": world.cid,
                        "action": "party_rest",
                        "payload": {
                            "rest_type": rest,
                            "duration_minutes": minutes,
                            "members": [
                                {
                                    "character_id": world.barbarian,
                                    "expected_revision": current["revision"],
                                }
                            ],
                        },
                        "idempotency_key": rest,
                    },
                )
                sheet = (await actor(world, world.barbarian))["sheet"]
                assert rage.feature(sheet)["uses"]["value"] == (
                    uses if rest == "long_rest" else remaining
                )
                assert not rage.active(sheet)
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("heavy", [False, True])
def test_damage_advantage_concentration_and_voluntary_end(tmp_path, heavy):
    async def run():
        world = await prepare(tmp_path, heavy=heavy, concentration=True)
        try:
            activated = await use(world)
            assert activated["ended_concentration_effect_ids"] == ["spell"]
            before_cast = await world.snapshot(), await actor(world, world.barbarian)
            with pytest.raises(Exception, match="Rage"):
                await world.call(
                    "combat_cast_spell",
                    {
                        "campaign_id": world.cid,
                        "actor_id": world.barbarian,
                        "spell_id": "test.spell.guiding-bolt",
                        "cast_level": 1,
                        "idempotency_key": "blocked-cast",
                    },
                )
            assert (await world.snapshot(), await actor(world, world.barbarian)) == before_cast
            await damage(world, 8)
            sheet = (await actor(world, world.barbarian))["sheet"]
            assert sheet["combat"]["hp"]["temp"] == (22 if heavy else 26)
            checked = await world.call(
                "combat_check",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.barbarian,
                    "kind": "save",
                    "ability": "strength",
                    "dc": 10,
                    "idempotency_key": "check",
                },
            )
            assert len(checked["rolls"]) == (1 if heavy else 2)
            await turn(world, world.barbarian, "b1")
            assert rage.active((await actor(world, world.barbarian))["sheet"])
            await turn(world, world.aid, "a1")
            world.close()
            world.runtime = create_runtime(world.config)
            ended = await use(world, "end", end=True)
            assert ended["ended_effect_ids"] == [activated["effect"]["id"]]
            assert not rage.active((await actor(world, world.barbarian))["sheet"])
            assert (await use(world, "end", end=True))["ended_effect_ids"] == ended[
                "ended_effect_ids"
            ]
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("attack_first", [True, False])
def test_actual_attack_retains_rage_then_inactivity_ends(tmp_path, attack_first):
    async def run():
        world = await prepare(tmp_path)
        try:
            if not attack_first:
                await use(world)
            attack_result = await world.call(
                "combat_resolve_attack",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.barbarian,
                    "target_id": world.aid,
                    "action": {"weapon_id": "axe", "attack_mode": "melee"},
                    "idempotency_key": "attack",
                },
            )
            if attack_first:
                await use(world)
            else:
                assert attack_result["rage_damage_bonus"] == 2
            await turn(world, world.barbarian, "b1")
            assert rage.active((await actor(world, world.barbarian))["sheet"])
            await turn(world, world.aid, "a1")
            await turn(world, world.barbarian, "b2")
            sheet = (await actor(world, world.barbarian))["sheet"]
            assert not rage.active(sheet)
            assert (
                next(e for e in sheet["effects"] if e["kind"] == rage.KIND)["ended_reason"]
                == "no_hostile_attack_or_damage"
            )
        finally:
            world.close()

    asyncio.run(run())


def test_unconsciousness_and_ten_round_expiry(tmp_path):
    async def run():
        world = await prepare(tmp_path, level=15)
        try:
            await use(world)
            for n in range(10):
                await turn(world, world.barbarian, f"b{n}")
                await turn(world, world.aid, f"a{n}")
                assert bool(rage.active((await actor(world, world.barbarian))["sheet"])) is (n < 9)
            await use(world, "again")
            await damage(world, 135, kind="fire")
            sheet = (await actor(world, world.barbarian))["sheet"]
            assert "unconscious" in sheet["conditions"]
            assert not rage.active(sheet)
        finally:
            world.close()

    asyncio.run(run())


def test_rage_dissipates_paid_readied_spell_without_refunding_slot(tmp_path):
    async def run():
        world = await prepare(tmp_path, concentration=True)
        try:
            await world.call(
                "combat_ready",
                {
                    "campaign_id": world.cid,
                    "action": "ready_spell",
                    "payload": {
                        "actor_id": world.barbarian,
                        "spell_id": "test.spell.guiding-bolt",
                        "trigger": "the bell rings",
                        "cast_level": 1,
                        "declaration": {"attacks": [{"target_id": world.aid}]},
                    },
                    "idempotency_key": "ready",
                },
            )
            before = (await world.snapshot())[0]["state"]["combat"]["readied"]
            assert len(before) == 1
            activated = await use(world)
            assert activated["dissipated_readied_ids"] == [before[0]["id"]]
            assert not (await world.snapshot())[0]["state"]["combat"]["readied"]
            sheet = (await actor(world, world.barbarian))["sheet"]
            assert sheet["spellcasting"]["spell_slots"]["1"]["value"] == 0
            assert not any(e["active"] and e["concentration"] for e in sheet["effects"])
        finally:
            world.close()

    asyncio.run(run())


def test_activation_cas_and_bad_declarations_are_no_write(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path, concentration=True)
        try:
            before = await world.snapshot(), await actor(world, world.barbarian)
            with pytest.raises(Exception, match="boolean|bool"):
                await use(world, "bad", end="true")
            original = StateMutationService.replace

            def fail(self, *args, **kwargs):
                if kwargs.get("operation") == "class.rage.enter":
                    raise ValueError("injected actor revision conflict")
                return original(self, *args, **kwargs)

            with monkeypatch.context() as scoped:
                scoped.setattr(StateMutationService, "replace", fail)
                with pytest.raises(Exception, match="revision conflict"):
                    await use(world)
            assert (await world.snapshot(), await actor(world, world.barbarian)) == before
            assert (await use(world))["effect"]
        finally:
            world.close()

    asyncio.run(run())
