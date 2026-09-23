import asyncio
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd import divine_smite as smite
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd_runtime.application import create_runtime

from tests.test_bardic_inspiration_mcp import actor
from tests.test_source_object_authority import setup
from tests.test_structured_spell_mcp import _slot


async def choose(world, offered, slot="1", key="choose"):
    return await world.call(
        "character_state_change",
        {
            "character_id": world.paladin,
            "action": "divine_smite",
            "payload": {"choice_id": offered["choice"]["id"], "accept": True, "slot": slot},
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize(
    "slot,species,defense",
    [
        (1, "undead", "resistances"),
        (4, "fiend (devil)", "immunities"),
        (9, "humanoid", "vulnerabilities"),
    ],
)
def test_public_critical_radiant_parts_follow_source_cap_and_defenses(
    tmp_path, slot, species, defense
):
    async def run():
        world = await prepare(tmp_path, slot=slot, species=species, defense=defense, critical=True)
        try:
            offered = await hit(world)
            assert offered["choice"]["result"]["critical"] is True
            result = await choose(world, offered, str(slot))
            settled = result["operation_result"]
            settled = settled.get("result", settled)
            count = min(5, slot + 1) + int(species in {"undead", "fiend (devil)"})
            assert settled["divine_smite"]["damage_expression"] == f"{count}d8"
            assert settled["critical"]
            parts = settled["damage"]["roll_parts"]
            radiant = next(p for p in parts if p["damage_type"] == "radiant")
            assert len(radiant["rolls"]) == count * 2
            amount = sum(radiant["rolls"])
            adjusted = (
                0
                if defense == "immunities"
                else amount // 2
                if defense == "resistances"
                else amount * 2
            )
            weapon = next(p for p in parts if p["damage_type"] == "slashing")
            assert settled["damage"]["applied_amount"] == weapon["amount"] + adjusted
            after = (await world.snapshot())[1]["sheet"]["combat"]["hp"]["value"]
            assert after == 200 - settled["damage"]["applied_amount"]
        finally:
            world.close()

    asyncio.run(run())


def test_extra_attack_has_two_independent_smite_windows(tmp_path):
    async def run():
        world = await prepare(tmp_path, extra_attack=True, critical=True)
        try:
            ids = []
            for index in range(2):
                offered = await hit(world, f"attack-{index}")
                ids.append(offered["choice"]["id"])
                assert (await choose(world, offered, key=f"choice-{index}"))[
                    "status"
                ] == "committed"
            assert ids[0] != ids[1]
            assert (await actor(world, world.paladin))["sheet"]["spellcasting"]["spell_slots"]["1"][
                "value"
            ] == 0
        finally:
            world.close()

    asyncio.run(run())


def test_readied_reaction_attack_suspends_before_damage_and_spend(tmp_path):
    async def run():
        world = await prepare(tmp_path)
        try:
            await world.call(
                "combat_common_action",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.paladin,
                    "action": "ready",
                    "trigger": "the bell rings",
                    "payload": {
                        "action": "attack",
                        "target_id": world.aid,
                        "attack": {"weapon_id": "sword"},
                    },
                    "idempotency_key": "ready",
                },
            )
            await world.call(
                "combat_end_turn",
                {"campaign_id": world.cid, "actor_id": world.paladin, "idempotency_key": "end"},
            )
            readied = (await world.snapshot())[0]["state"]["combat"]["readied"][0]
            triggered = await world.call(
                "combat_ready",
                {
                    "campaign_id": world.cid,
                    "action": "trigger_action",
                    "payload": {"readied_id": readied["id"], "event": "the bell rings"},
                    "idempotency_key": "trigger",
                },
            )
            window = next(
                w for w in triggered["combat"]["pending"] if w.get("trigger") == "readied_action"
            )
            offered = await world.call(
                "combat_ready",
                {
                    "campaign_id": world.cid,
                    "action": "resolve_action",
                    "payload": {
                        "actor_id": world.paladin,
                        "choice_id": window["id"],
                        "release": True,
                    },
                    "idempotency_key": "release",
                },
            )
            assert offered["status"] == "pending_hit"
            result = await choose(world, offered)
            assert result["status"] == "committed"
            assert (await actor(world, world.paladin))["sheet"]["spellcasting"]["spell_slots"]["1"][
                "value"
            ] == 1
            assert (await world.snapshot())[1]["sheet"]["combat"]["hp"]["value"] < 200
        finally:
            world.close()

    asyncio.run(run())


async def prepare(
    tmp_path, slot=1, species="humanoid", defense=None, critical=False, extra_attack=False
):
    world = await setup(tmp_path, random_seed="smite")
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    sheet = default_character_sheet()
    level = 5 if extra_attack else 2
    sheet["progression"].update(
        level=level, classes=[{"name": "Paladin", "level": level, "hit_die": 10}]
    )
    sheet["spellcasting"].update(ability="charisma", spell_slots=_slot(slot, 2))
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    sheet, wid = add_inventory_item(
        sheet,
        {
            "id": "sword",
            "name": "Sword",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d4",
                "damage_type": "slashing",
                "attack_bonus_override": 30,
            },
        },
    )
    sheet = equip_inventory_item(sheet, wid, "main_hand")
    card = await world.call(
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": world.cid, "name": "Paladin", "sheet": sheet},
            "idempotency_key": "paladin",
        },
    )
    world.paladin = card["id"]
    await world.call(
        "character_content_apply",
        {
            "character_id": world.paladin,
            "artifact_id": smite.FEATURE,
            "selection": {},
            "idempotency_key": "source",
        },
    )
    enemy = default_character_sheet()
    enemy["progression"]["species"] = species
    enemy["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
    if defense:
        enemy["traits"][defense] = ["radiant"]
    if critical:
        enemy["conditions"] = ["paralyzed"]
    if extra_attack:
        await world.call(
            "character_content_apply",
            {
                "character_id": world.paladin,
                "artifact_id": "dnd5e.content.srd2014.feature.paladin-extra-attack",
                "selection": {},
                "idempotency_key": "extra-attack",
            },
        )
    created = await world.call(
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": world.cid, "name": "Enemy", "sheet": enemy},
            "idempotency_key": "enemy",
        },
    )
    world.aid = created["id"]
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
            "participant_ids": [world.paladin, world.aid],
            "participant_config": [
                {"actor_id": world.paladin, "initiative": 20, "position": {"x": 0, "y": 0}},
                {"actor_id": world.aid, "initiative": 10, "position": {"x": 1, "y": 0}},
            ],
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 20, "height_cells": 20},
            "idempotency_key": "combat",
        },
    )
    return world


async def hit(world, key="attack"):
    return await world.call(
        "combat_resolve_attack",
        {
            "campaign_id": world.cid,
            "actor_id": world.paladin,
            "target_id": world.aid,
            "action": {"weapon_id": "sword"},
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize("accept", [True, False])
def test_public_hit_choice_restart_slot_damage_and_replay(tmp_path, accept):
    async def run():
        world = await prepare(tmp_path)
        try:
            original = await world.snapshot()
            offered = await hit(world)
            assert offered["status"] == "pending_hit"
            before = await world.snapshot()
            assert before[1]["sheet"]["combat"]["hp"] == original[1]["sheet"]["combat"]["hp"]
            assert before[0]["pending_hit"]["id"] == offered["choice"]["id"]
            assert (await actor(world, world.paladin))["sheet"]["spellcasting"]["spell_slots"]["1"][
                "value"
            ] == 2
            world.close()
            world.runtime = create_runtime(world.config)
            args = {
                "character_id": world.paladin,
                "action": "divine_smite",
                "payload": {
                    "choice_id": offered["choice"]["id"],
                    "accept": accept,
                    **({"slot": "1"} if accept else {}),
                },
                "idempotency_key": "choice",
            }
            result = await world.call("character_state_change", args)
            assert result["status"] == "committed"
            card = await actor(world, world.paladin)
            assert card["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 2 - int(accept)
            after = await world.snapshot()
            assert (
                after[1]["sheet"]["combat"]["hp"]["value"]
                < before[1]["sheet"]["combat"]["hp"]["value"]
            )
            replay = await world.call("character_state_change", args)
            assert replay["operation_result"] == result["operation_result"]
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


def test_invalid_choice_and_cas_failure_preserve_hit_slot_hp_and_rng(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path)
        try:
            offered = await hit(world)
            before = await world.snapshot()
            card = await actor(world, world.paladin)
            args = {
                "character_id": world.paladin,
                "action": "divine_smite",
                "payload": {"choice_id": offered["choice"]["id"], "accept": True, "slot": "9"},
                "idempotency_key": "choice",
            }
            with pytest.raises(Exception, match="slot"):
                await world.call("character_state_change", args)
            assert await world.snapshot() == before
            args["payload"]["slot"] = "1"
            args["idempotency_key"] = "valid-choice"
            original = StateMutationService.replace

            def fail(*args, **kwargs):
                raise ValueError("injected CAS conflict")

            monkeypatch.setattr(StateMutationService, "replace", fail)
            with pytest.raises(Exception, match="injected CAS"):
                await world.call("character_state_change", args)
            assert await world.snapshot() == before
            assert await actor(world, world.paladin) == card
            monkeypatch.setattr(StateMutationService, "replace", original)
            assert (await world.call("character_state_change", args))["status"] == "committed"
        finally:
            world.close()

    asyncio.run(run())
