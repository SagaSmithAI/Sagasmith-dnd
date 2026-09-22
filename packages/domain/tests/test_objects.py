from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import NeedsRulingError, roll_attack_action
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.objects import (
    OBJECT_RULE,
    apply_object_damage,
    object_attack_plan,
    resolve_object_attack,
    validate_object_profile,
)
from sagasmith_dnd.rule_engine import CompiledMechanic, ResolutionContext


def profile(**changes):
    return {
        "id": "wall-section",
        "name": "Wall section",
        "scene_id": "vault",
        "material": "stone",
        "size": "large",
        "resilience": "resilient",
        "armor_class": 17,
        "hit_points": 27,
        **changes,
    }


def damage(*, amount=10, kind="bludgeoning", traits=None, parts=None, **changes):
    return apply_object_damage(
        profile(**changes),
        27,
        parts or [{"amount": amount, "damage_type": kind}],
        weapon_id="mace",
        weapon_traits=traits or [],
    )


@pytest.mark.parametrize("kind", ["poison", "psychic"])
def test_object_inherent_immunities_win_over_vulnerability(kind):
    result = damage(kind=kind, amount=999, damage_vulnerabilities=[kind])
    assert result["applied_amount"] == 0
    assert result["hit_points_after"] == 27


@pytest.mark.parametrize("amount,expected", [(9, 0), (10, 10), (11, 11)])
def test_threshold_below_equal_above(amount, expected):
    result = damage(amount=amount, damage_threshold=10)
    assert result["applied_amount"] == expected
    assert result["threshold_met"] == (amount >= 10)


@pytest.mark.parametrize(
    "amount,resistant,vulnerable,threshold,expected",
    [
        (19, True, False, 10, 0),
        (20, True, False, 10, 10),
        (5, False, True, 10, 10),
        (11, True, True, 10, 10),
    ],
)
def test_typed_adjustments_precede_threshold(amount, resistant, vulnerable, threshold, expected):
    result = damage(
        amount=amount,
        damage_threshold=threshold,
        damage_resistances=["bludgeoning"] if resistant else [],
        damage_vulnerabilities=["bludgeoning"] if vulnerable else [],
    )
    assert result["applied_amount"] == expected


def test_one_attack_threshold_aggregates_adjusted_types_and_rounds_type_once():
    result = damage(
        damage_threshold=10,
        damage_resistances=["bludgeoning"],
        parts=[
            {"amount": 3, "damage_type": "bludgeoning"},
            {"amount": 3, "damage_type": "bludgeoning"},
            {"amount": 7, "damage_type": "fire"},
            {"amount": 100, "damage_type": "poison"},
        ],
    )
    assert result["applied_amount"] == 10
    assert result["hit_points_after"] == 17


@pytest.mark.parametrize("traits,expected", [([], 0), (["magical"], 10), (["adamantine"], 10)])
def test_source_bound_weapon_exceptions(traits, expected):
    result = damage(
        traits=traits, damage_filter={"required_any_weapon_traits": ["magical", "adamantine"]}
    )
    assert result["applied_amount"] == expected


def test_unsuitable_type_and_tool_cannot_damage_object():
    assert damage(damage_filter={"allowed_damage_types": ["slashing"]})["applied_amount"] == 0
    assert damage(damage_filter={"allowed_weapon_ids": ["pick"]})["applied_amount"] == 0


def test_huge_object_sections_have_independent_hp_and_no_parent_destruction_inference():
    parent = {"id": "statue", "size": "gargantuan"}
    first = profile(section_of=parent)
    second = profile(id="second-leg", section_of=parent)
    result = apply_object_damage(
        first,
        27,
        [{"amount": 100, "damage_type": "bludgeoning"}],
        weapon_id="mace",
        weapon_traits=[],
    )
    assert result["destroyed"]
    assert second["hit_points"] == 27
    assert first["hit_points"] == 27
    assert "parent_destroyed" not in result


@pytest.mark.parametrize(
    "change",
    [
        {"damage_threshold": -1},
        {"damage_threshold": True},
        {"damage_threshold": 1.5},
        {"armor_class": "17"},
        {"hit_points": 0},
        {"size": "huge"},
        {"material": ""},
        {"damage_immunities": "fire"},
        {"damage_resistances": ["all"]},
        {"conditions": ["unconscious"]},
        {"damage_filter": {"required_any_weapon_traits": ["silvered"]}},
        {"damage_filter": {"attack_bonus": 100}},
        {"section_of": {"id": "wall-section", "size": "huge"}},
    ],
)
def test_malformed_or_rule_forging_profiles_rejected(change):
    with pytest.raises(ValueError):
        validate_object_profile(profile(**change))


class Dice:
    def __init__(self, *values):
        self.values = list(values)

    def randint(self, low, high):
        value = self.values.pop(0)
        assert low <= value <= high
        return value


def attacker(**mechanics):
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 12, "max": 12, "temp": 0}
    sheet["abilities"]["strength"]["score"] = 16
    sheet["inventory"]["items"] = [
        {
            "id": "mace",
            "name": "Mace",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "bludgeoning",
                "properties": [],
                "proficient": True,
                **mechanics,
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "mace"
    return {"id": "actor", "sheet": sheet, "derived": derive_character_sheet(sheet)}


def test_critical_object_hit_rolls_dice_without_creature_zero_hp_effects():
    actor = attacker()
    before = deepcopy(actor)
    obj = profile(hit_points=5, damage_threshold=10)
    rules = ResolutionContext("object-test", get_core_rule_pack("2014"), (), {}, {})
    plan = object_attack_plan(actor, obj, weapon_id="mace", rules=rules)
    hit = roll_attack_action(plan=plan, rng=Dice(20))
    updated, result = resolve_object_attack(
        actor, obj, 5, plan=plan, attack=hit, rules=rules, rng=Dice(4, 4)
    )
    assert result["critical"]
    assert result["damage"]["applied_amount"] == 11
    assert result["damage"]["destroyed"]
    assert updated == before == actor
    assert {"sheet", "death_saves", "concentration", "conditions"}.isdisjoint(result["damage"])
    assert any(receipt["mechanic_id"] == OBJECT_RULE for receipt in result["rule_receipts"])
    assert not any(
        receipt["mechanic_id"] == "dnd5e.core.damage.zero_hp" for receipt in result["rule_receipts"]
    )


def test_unsupported_on_hit_does_not_enter_creature_resolver():
    actor = attacker(on_hit_effect="The target creature is stunned until its next turn.")
    with pytest.raises(NeedsRulingError, match="object-specific"):
        object_attack_plan(actor, profile(), weapon_id="mace")


def test_generic_creature_attack_extension_cannot_silently_apply_or_disappear():
    actor = attacker()
    before = deepcopy(actor)
    mechanic = CompiledMechanic(
        "test.after", "attack.after", (), ({"op": "hp.temp.set", "value": 5},), ()
    )
    rules = ResolutionContext("extension-test", get_core_rule_pack("2014"), (mechanic,), {}, {})
    with pytest.raises(NeedsRulingError, match="attack extensions"):
        object_attack_plan(actor, profile(), weapon_id="mace", rules=rules)
    assert actor == before


@pytest.mark.parametrize(
    "mechanics,traits",
    [({}, []), ({"magical": True}, ["magical"]), ({"materials": ["adamantine"]}, ["adamantine"])],
)
def test_magic_and_material_exceptions_use_effective_weapon_facts(mechanics, traits):
    actor = attacker(**mechanics)
    actor["sheet"]["inventory"]["items"][0]["attunement"] = "attuned"
    actor["derived"] = derive_character_sheet(actor["sheet"])
    obj = profile(damage_filter={"required_any_weapon_traits": ["magical", "adamantine"]})
    plan = object_attack_plan(actor, obj, weapon_id="mace")
    updated, result = resolve_object_attack(
        actor, obj, 27, plan=plan, attack={"hit": True, "critical": False}, rng=Dice(4)
    )
    assert result["weapon_traits"] == traits
    assert result["damage"]["applied_amount"] == (7 if traits else 0)


def test_incapacitated_attacker_cannot_roll_against_objects():
    actor = attacker()
    actor["sheet"]["combat"]["hp"]["value"] = 0
    with pytest.raises(ValueError, match="incapacitated"):
        object_attack_plan(actor, profile(), weapon_id="mace")
