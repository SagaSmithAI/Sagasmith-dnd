from copy import deepcopy
from pathlib import Path

import pytest
from test_bardic_inspiration import Dice
from test_combat_engine import _grid_encounter
from test_fighting_styles import styled, target

from sagasmith_dnd import rage
from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.combat_engine import (
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    preflight_attack,
    resolve_actor_check,
    resolve_attack_action,
)
from sagasmith_dnd.conditions import apply_condition_change
from sagasmith_dnd.core_content import PACK_ID, PACK_VERSION, build_srd2014_content
from sagasmith_dnd.lifecycle import advance_elapsed_effect_durations
from sagasmith_dnd.spell_components import check_components


def barbarian(level=1, *, armor=False, persistent=True):
    _, artifacts = build_srd2014_content(Path(__file__).parents[3] / "skills")
    sheet = default_character_sheet()
    sheet["progression"].update(
        level=level, classes=[{"name": "Barbarian", "level": level, "hit_die": 12}]
    )
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    sheet["traits"]["proficiencies"]["armor"] = ["heavy armor"]
    for identifier in [rage.FEATURE, rage.PERSISTENT] if persistent else [rage.FEATURE]:
        source = next(a for a in artifacts if a["id"] == identifier)
        sheet["content"]["features"].append(
            {
                **{
                    k: deepcopy(v)
                    for k, v in source["card"].items()
                    if k not in {"class_name", "minimum_level", "unlock_levels"}
                },
                "id": identifier,
                "pack_id": PACK_ID,
                "pack_version": PACK_VERSION,
                "rule_refs": source["rule_refs"],
                "mechanic_refs": source["mechanic_refs"],
            }
        )
    if armor:
        sheet, armor_id = add_inventory_item(
            sheet,
            {
                "id": "plate",
                "name": "Plate",
                "kind": "armor",
                "mechanics": {"category": "heavy", "base_ac": 18, "dexterity_mode": "none"},
            },
        )
        sheet = equip_inventory_item(sheet, armor_id, "armor")
    return sheet


@pytest.mark.parametrize("level,bonus", [(1, 2), (8, 2), (9, 3), (15, 3), (16, 4), (20, 4)])
def test_actual_strength_melee_damage_bonus_flat_on_critical(level, bonus):
    actor = styled("", properties=["finesse", "thrown"])
    source = barbarian(level)
    source["inventory"] = actor["sheet"]["inventory"]
    source["abilities"] = actor["sheet"]["abilities"]
    source["traits"]["proficiencies"]["weapons"] = ["simple weapons"]
    source = rage.enter(source, "rage")["sheet"]
    actor.update(sheet=source, derived=derive_character_sheet(source))
    enemy = target()
    for ability, mode, expected in [
        ("strength", "melee", bonus),
        ("dexterity", "melee", 0),
        ("strength", "ranged", 0),
    ]:
        action = {"weapon_id": "weapon", "attack_ability": ability, "attack_mode": mode}
        plan = preflight_attack(actor, enemy, action=action)
        assert plan["rage_damage_bonus"] == expected
        dice = Dice(20, 2, 3)
        _, _, result = resolve_attack_action(actor, enemy, plan=plan, rng=dice)
        if mode == "melee":
            modifier = 3 if ability == "strength" else 2
            assert result["damage"]["input_amount"] == 5 + modifier + expected


@pytest.mark.parametrize("armor", [False, True])
def test_heavy_armor_suppresses_benefits_but_not_activation_or_prohibitions(armor):
    source = rage.enter(barbarian(armor=armor), "rage")["sheet"]
    assert bool(rage.active(source)) and rage.benefits(source) is (not armor)
    actor = {"id": "b", "sheet": source, "derived": derive_character_sheet(source)}
    for kind, ability, alternative in [
        ("ability", "athletics", None),
        ("save", "strength", None),
        ("ability", "intimidation", "strength"),
    ]:
        rng = Dice(4, 17)
        result = resolve_actor_check(
            actor, kind=kind, ability=ability, skill_ability=alternative, dc=10, rng=rng
        )
        assert len(result["rolls"]) == (1 if armor else 2)
    assert apply_damage_to_sheet(source, amount=7, damage_type="slashing")["applied_amount"] == (
        7 if armor else 3
    )
    with pytest.raises(ValueError, match="Rage"):
        check_components(source, {}, {}, overrides={"ignore_components": True})
    with pytest.raises(ValueError, match="Rage"):
        add_effect(source, {"id": "spell", "name": "Spell", "concentration": True})


def test_damage_parts_immunity_vulnerability_temp_hp_and_end_conditions():
    source = rage.enter(barbarian(), "rage")["sheet"]
    source["traits"]["immunities"] = ["piercing"]
    source["traits"]["vulnerabilities"] = ["slashing"]
    source["combat"]["hp"]["temp"] = 20
    result = apply_damage_parts_to_sheet(
        source,
        [
            {"amount": 7, "damage_type": "bludgeoning"},
            {"amount": 7, "damage_type": "piercing"},
            {"amount": 7, "damage_type": "slashing"},
            {"amount": 7, "damage_type": "fire"},
        ],
    )
    assert result["applied_amount"] == 16
    assert result["sheet"]["combat"]["hp"]["value"] == 100
    derive_character_sheet(result["sheet"])
    kept = rage.end_turn(result["sheet"])
    assert rage.active(kept["sheet"])
    assert not rage.active(rage.end_turn(kept["sheet"])["sheet"])
    immune = apply_damage_to_sheet(source, amount=7, damage_type="piercing")
    assert not rage.active(rage.end_turn(immune["sheet"])["sheet"])
    for condition in ("unconscious", "dead"):
        changed = deepcopy(source)
        apply_condition_change(changed, condition_id=condition, add=True)
        assert not rage.active(changed)


def test_persistent_rage_exact_expiry_edition_and_concentration_end():
    sheet = barbarian(15)
    sheet, _ = add_effect(sheet, {"id": "spell", "name": "Spell", "concentration": True})
    entered = rage.enter(sheet, "rage")
    assert entered["ended_concentration_effect_ids"] == ["spell"]
    assert rage.active(rage.end_turn(entered["sheet"])["sheet"])
    assert rage.active(advance_elapsed_effect_durations(entered["sheet"], elapsed_ticks=9)["sheet"])
    assert not rage.active(
        advance_elapsed_effect_durations(entered["sheet"], elapsed_ticks=10)["sheet"]
    )
    missing = rage.enter(barbarian(15, persistent=False), "r")["sheet"]
    assert not rage.active(rage.end_turn(missing)["sheet"])
    sheet["edition"] = "2024"
    with pytest.raises(ValueError, match="2014"):
        rage.enter(sheet, "new")


@pytest.mark.parametrize("hostile", [False, True])
def test_missed_hostile_attack_before_activation_counts(hostile):
    attacker = styled("")
    source = barbarian()
    source["inventory"] = attacker["sheet"]["inventory"]
    attacker.update(
        sheet=source, derived=derive_character_sheet(source), initiative=20, disposition="friendly"
    )
    enemy = target()
    enemy.update(initiative=10, disposition="hostile" if hostile else "friendly")
    encounter = _grid_encounter([attacker, enemy])
    plan = preflight_attack(attacker, enemy, action={"weapon_id": "weapon"}, encounter=encounter)
    updated, _, result = resolve_attack_action(attacker, enemy, plan=plan, rng=Dice(1))
    assert not result["hit"]
    entered = rage.enter(updated["sheet"], "rage")
    assert bool(rage.active(rage.end_turn(entered["sheet"])["sheet"])) is hostile
