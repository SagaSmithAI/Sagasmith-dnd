from __future__ import annotations

import random

import pytest

from sagasmith_dnd.adventuring_gear import (
    _GEAR_INTENTS,
    ADVENTURING_GEAR_ACTIONS,
    ADVENTURING_GEAR_SOURCE_REF,
    adventuring_gear_action,
    normalize_gear_intent,
    resolve_adventuring_gear_intent,
)
from sagasmith_dnd.character_schema import (
    active_effect_roll_advantage,
    default_character_sheet,
    derive_character_sheet,
)
from sagasmith_dnd.combat_engine import CombatEngineError, resolve_actor_check
from sagasmith_dnd.game_time import TICKS_PER_HOUR, TICKS_PER_MINUTE


def _item(action):
    return {
        "id": "gear-1",
        "name": action.name,
        "source_key": action.source_key,
        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
    }


def test_priority_gear_catalog_preserves_source_effects_and_exclusions() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    alchemist_fire = next(
        action
        for action in ADVENTURING_GEAR_ACTIONS.values()
        if action.name == "Alchemist's fire (flask)"
    )
    healer = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Healer's kit"
    )
    assert (acid.range_feet, acid.attack, acid.damage, acid.damage_type) == (
        20,
        True,
        "2d6",
        "acid",
    )
    assert alchemist_fire.duration == "start_of_turn_until_extinguished"
    assert "DC 10 Dexterity" in alchemist_fire.notes
    assert "10 uses" in healer.notes
    # Poison, trap infrastructure, and lighting consumers have their own issue slices.
    assert not any(
        "poison" in action.name.casefold() for action in ADVENTURING_GEAR_ACTIONS.values()
    )


@pytest.mark.parametrize(
    "field,value", [("source_key", "custom.acid"), ("source_ref", "custom.md"), ("name", "Acid")]
)
def test_gear_action_requires_exact_source_key_reference_and_name(field: str, value: str) -> None:
    action = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    item = {**_item(action), field: value}
    with pytest.raises(CombatEngineError, match="source-bound|identity"):
        adventuring_gear_action(item)


def test_all_prioritized_gear_actions_round_trip_by_source_identity() -> None:
    assert len(ADVENTURING_GEAR_ACTIONS) == 22
    for action in ADVENTURING_GEAR_ACTIONS.values():
        assert adventuring_gear_action(_item(action)) == action


def test_every_catalogued_priority_item_has_at_least_one_executable_intent() -> None:
    assert set(_GEAR_INTENTS) == {
        action.source_key.rsplit(".", 1)[-1].replace("-", "_")
        for action in ADVENTURING_GEAR_ACTIONS.values()
    }
    assert all(intents for intents in _GEAR_INTENTS.values())


def test_normalized_intent_returns_fixed_attack_damage_and_item_consumption() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    result = resolve_adventuring_gear_intent(_item(acid), "  THROW  ")

    assert result["intent"] == "throw"
    assert result["action_economy"] == "action"
    assert result["maximum_range_feet"] == 20
    assert result["attack"] == "ranged_improvised"
    assert result["effect"] == {"damage": "2d6", "damage_type": "acid"}
    assert result["resource_cost"] == {"item_quantity": 1}
    assert "roll" not in result and "success" not in result


def test_ground_gear_resolves_source_area_save_and_movement_trigger() -> None:
    bearings = next(
        action
        for action in ADVENTURING_GEAR_ACTIONS.values()
        if action.name == "Ball bearings (bag of 1,000)"
    )
    result = resolve_adventuring_gear_intent(_item(bearings), "Spread")

    assert result["area"] == {"shape": "square", "width_feet": 10, "depth_feet": 10}
    assert result["effect"]["save"] == {"ability": "dexterity", "dc": 10}
    assert result["effect"]["failure"] == {"condition": "prone"}
    assert result["effect"]["half_speed_avoids_save"] is True
    assert result["duration_ticks"] is None


def test_time_and_object_rules_are_fixed_without_inventing_unstated_action_costs() -> None:
    antitoxin = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Antitoxin (vial)"
    )
    antitoxin_use = resolve_adventuring_gear_intent(_item(antitoxin), "drink")
    lamp = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Lamp")
    lamp_use = resolve_adventuring_gear_intent(_item(lamp), "light")
    rope = next(
        action
        for action in ADVENTURING_GEAR_ACTIONS.values()
        if action.name == "Rope, hempen (50 feet)"
    )
    rope_use = resolve_adventuring_gear_intent(_item(rope), "burst")
    chain = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Chain (10 feet)"
    )
    chain_use = resolve_adventuring_gear_intent(_item(chain), "burst")

    assert antitoxin_use["duration_ticks"] == TICKS_PER_HOUR
    assert antitoxin_use["requirements"] == ["target_not_undead", "target_not_construct"]
    assert antitoxin_use["action_economy"] is None
    assert lamp_use["duration_ticks"] == 6 * TICKS_PER_HOUR
    assert lamp_use["resource_cost"]["fuel"]["quantity"] == 1
    assert lamp_use["resource_cost"]["fuel"]["source_key"].endswith("oil-flask")
    assert rope_use["object_hit_points"] == 2
    assert rope_use["check"] == {"ability": "strength", "dc": 17}
    assert chain_use["object_hit_points"] == 10
    assert chain_use["check"] == {"ability": "strength", "dc": 20}
    assert chain_use["effect"] == {"requires_state": "intact", "success_state": "broken"}
    assert 5 * TICKS_PER_MINUTE == 50


@pytest.mark.parametrize(
    ("name", "bright", "dim", "shape"),
    [
        ("Lamp", 15, 30, "radius"),
        ("Lantern, bullseye", 60, 60, "cone"),
        ("Lantern, hooded", 30, 30, "radius"),
    ],
)
def test_2014_lamp_and_lantern_plans_keep_light_radius_fuel_and_duration(
    name: str, bright: int, dim: int, shape: str
) -> None:
    item = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == name)
    plan = resolve_adventuring_gear_intent(_item(item), "light")

    assert plan["action_economy"] is None
    assert plan["duration_ticks"] == 6 * TICKS_PER_HOUR
    assert plan["effect"]["bright_light"] == {"shape": shape, "feet": bright}
    assert plan["effect"]["dim_light_additional_feet"] == dim
    assert plan["resource_cost"]["fuel"] == {
        "source_key": "dnd5e.content.srd2014.item.oil-flask",
        "quantity": 1,
    }


def test_2014_torch_plan_uses_action_and_one_hour_source_light_without_fuel() -> None:
    torch = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Torch")
    light = resolve_adventuring_gear_intent(_item(torch), "light")
    extinguish = resolve_adventuring_gear_intent(_item(torch), "extinguish")

    assert light["action_economy"] == "action"
    assert light["duration_ticks"] == TICKS_PER_HOUR
    assert light["effect"]["bright_light"] == {"shape": "radius", "feet": 20}
    assert light["effect"]["dim_light_additional_feet"] == 20
    assert light["resource_cost"] == {}
    assert extinguish["effect"] == {"light_off": True}


def test_2014_hooded_lantern_lowering_and_oil_ground_effect_are_bounded() -> None:
    lantern = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Lantern, hooded"
    )
    lower = resolve_adventuring_gear_intent(_item(lantern), "lower_hood")
    raise_plan = resolve_adventuring_gear_intent(_item(lantern), "raise_hood")
    oil = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Oil (flask)"
    )
    pour = resolve_adventuring_gear_intent(_item(oil), "pour_ground")

    assert lower["action_economy"] == "action"
    assert lower["effect"]["dim_light"] == {"shape": "radius", "feet": 5}
    assert raise_plan["action_economy"] is None
    assert raise_plan["effect"]["bright_light"] == {"shape": "radius", "feet": 30}
    assert pour["area"] == {"shape": "square", "width_feet": 5, "depth_feet": 5}
    assert pour["effect"]["if_lit"]["duration_rounds"] == 2


def test_oil_ground_rule_preserves_surface_and_per_turn_damage_limits() -> None:
    oil = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Oil (flask)"
    )
    result = resolve_adventuring_gear_intent(_item(oil), "pour-ground")

    assert result["intent"] == "pour_ground"
    assert result["target"] == "level_ground_surface"
    assert result["area"] == {"shape": "square", "width_feet": 5, "depth_feet": 5}
    assert result["effect"]["if_lit"] == {
        "duration_rounds": 2,
        "trigger": "creature_enters_or_ends_turn_in_area",
        "damage": "5",
        "damage_type": "fire",
        "once_per_turn_per_creature": True,
    }


def test_alchemists_fire_extinguish_is_source_bound_action_check_without_consumable_cost() -> None:
    fire = next(
        action
        for action in ADVENTURING_GEAR_ACTIONS.values()
        if action.name == "Alchemist's fire (flask)"
    )
    plan = resolve_adventuring_gear_intent(_item(fire), "extinguish")

    assert plan["action_economy"] == "action"
    assert plan["target"] == "self_burning"
    assert plan["check"] == {"ability": "dexterity", "dc": 10}
    assert plan["effect"] == {"ends_effect_kind": "adventuring_gear_burning"}
    assert plan["resource_cost"] == {}


def test_antitoxin_advantage_is_scoped_to_authoritative_poison_save_purpose() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "antitoxin:test",
            "name": "Antitoxin",
            "kind": "source_effect",
            "active": True,
            "concentration": False,
            "duration": {"period": "hour", "remaining": 1},
            "metadata": {"save_purpose": "poison"},
            "changes": [
                {
                    "path": "rolls.saving_throw.advantage",
                    "mode": "set",
                    "value": True,
                }
            ],
        }
    ]

    assert active_effect_roll_advantage(sheet, "save", purpose="poison") == (True, False)
    assert active_effect_roll_advantage(sheet, "save", purpose="spell") == (False, False)
    assert active_effect_roll_advantage(sheet, "save") == (False, False)

    actor = {"id": "target", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    poison_save = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_purpose="poison",
        ruleset="2014",
        rng=random.Random(1),
    )
    unrelated_save = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_purpose="effect",
        ruleset="2014",
        rng=random.Random(1),
    )
    assert len(poison_save["rolls"]) == 2
    assert len(unrelated_save["rolls"]) == 1


def test_intent_rejects_unknown_operations_and_caller_computed_rule_overrides() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    item = _item(acid)
    with pytest.raises(CombatEngineError, match="not a source-defined action"):
        resolve_adventuring_gear_intent(item, "deal 999 damage")
    with pytest.raises(CombatEngineError, match="not authoritative"):
        resolve_adventuring_gear_intent(item, "throw", supplied_effects={"damage": "99d99"})
    with pytest.raises(CombatEngineError, match="must be text"):
        normalize_gear_intent({"intent": "throw"})


def test_returned_effect_data_cannot_mutate_canonical_rules() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    first = resolve_adventuring_gear_intent(_item(acid), "throw")
    first["effect"]["damage"] = "99d99"
    next_result = resolve_adventuring_gear_intent(_item(acid), "throw")

    assert next_result["effect"]["damage"] == "2d6"


def test_forged_inventory_provenance_cannot_access_official_effect_resolver() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    forged = {**_item(acid), "source_ref": "custom.rules.md"}
    with pytest.raises(CombatEngineError, match="source-bound"):
        resolve_adventuring_gear_intent(forged, "throw")


def test_materialized_official_item_resolves_from_its_exact_source_key() -> None:
    acid = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Acid (vial)"
    )
    materialized = {key: value for key, value in _item(acid).items() if key != "source_ref"}

    result = resolve_adventuring_gear_intent(materialized, "throw")

    assert result["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
    assert result["source_key"] == acid.source_key


def test_bound_target_size_and_tool_eligibility_are_encoded_as_requirements() -> None:
    manacles = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Manacles"
    )
    escape = resolve_adventuring_gear_intent(_item(manacles), "escape")
    pick = resolve_adventuring_gear_intent(_item(manacles), "pick")
    bind = resolve_adventuring_gear_intent(_item(manacles), "bind")
    unlock = resolve_adventuring_gear_intent(_item(manacles), "unlock")

    assert escape["target"] == "bound_small_or_medium_creature"
    assert escape["check"] == {"ability": "dexterity", "dc": 20}
    assert pick["check"] == {"ability": "dexterity", "dc": 15}
    assert pick["requirements"] == ["thieves_tools_proficiency"]
    assert bind["target"] == "small_or_medium_creature"
    assert bind["effect"] == {"binding_state": "bound"}
    assert unlock["target"] == "bound_small_or_medium_creature"
    assert unlock["effect"] == {
        "requires_provided_key": True,
        "binding_state": "released",
    }
    manacles = next(
        action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Manacles"
    )
    breaking = resolve_adventuring_gear_intent(_item(manacles), "break")
    assert breaking["object_hit_points"] == 15
    assert breaking["check"] == {"ability": "strength", "dc": 20}
    assert manacles.target == "small_or_medium_creature"


@pytest.mark.parametrize(
    ("name", "intent", "expected_effect"),
    [
        (
            "Crowbar",
            "apply_leverage",
            {"advantage": True, "eligible_targets": ["door", "object"]},
        ),
        (
            "Magnifying glass",
            "inspect",
            {"ability_check_advantage": "appraise_or_inspect"},
        ),
        (
            "Lock",
            "pick",
            {
                "key_provided_by_source": True,
                "requires_key_unavailable": True,
                "requires_state": "locked",
                "success_state": "open",
                "failure_state": "locked",
            },
        ),
        (
            "Rope, hempen (50 feet)",
            "burst",
            {"requires_state": "intact", "success_state": "broken"},
        ),
    ],
)
def test_source_bound_check_families_have_fixed_effects_and_state_gates(
    name: str, intent: str, expected_effect: dict[str, object]
) -> None:
    action = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == name)
    plan = resolve_adventuring_gear_intent(_item(action), intent)

    assert plan["effect"] == expected_effect
    assert plan["source_key"] == action.source_key
    assert plan["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
    if name == "Crowbar":
        assert plan["requirements"] == ["leverage_applies_to_check"]
    elif name == "Magnifying glass":
        assert plan["target"] == "small_or_highly_detailed_object"
    elif name == "Lock":
        assert plan["check"] == {"ability": "dexterity", "dc": 15}
        assert plan["requirements"] == ["thieves_tools_proficiency"]
    else:
        assert plan["object_hit_points"] == 2
        assert plan["check"] == {"ability": "strength", "dc": 17}


def test_source_bound_check_families_reject_forged_item_identity() -> None:
    for name, intent in (
        ("Crowbar", "apply_leverage"),
        ("Magnifying glass", "inspect"),
        ("Lock", "pick"),
        ("Rope, hempen (50 feet)", "burst"),
    ):
        action = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == name)
        forged = {**_item(action), "source_key": "custom.crowbar"}
        with pytest.raises(CombatEngineError, match="source-bound|identity"):
            resolve_adventuring_gear_intent(forged, intent)


def test_lock_key_unlock_is_source_bound_and_has_no_check() -> None:
    action = next(action for action in ADVENTURING_GEAR_ACTIONS.values() if action.name == "Lock")
    plan = resolve_adventuring_gear_intent(_item(action), "unlock")

    assert plan["action_economy"] is None
    assert plan["check"] is None
    assert plan["target"] == "lock"
    assert plan["effect"] == {
        "requires_state": "locked",
        "requires_provided_key": True,
        "success_state": "open",
    }
