from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    preflight_attack,
    resolve_attack_action,
    roll_attack_action,
)
from sagasmith_dnd.fighting_styles import (
    apply_protection,
    has_style,
    protection_candidates,
    roll_weapon_damage,
)


class Dice:
    def __init__(self, *values):
        self.values = list(values)

    def randint(self, minimum, maximum):
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def styled(
    style,
    class_name="fighter",
    *,
    edition="2014",
    ranged=False,
    properties=(),
    damage="1d8",
    override=None,
):
    sheet = default_character_sheet()
    sheet["edition"] = edition
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    sheet["abilities"]["strength"]["score"] = 16
    sheet["abilities"]["dexterity"]["score"] = 14
    sheet["traits"]["proficiencies"]["weapons"] = ["simple weapons"]
    sheet["content"]["features"] = [
        {
            "id": f"dnd5e.content.srd2014.feature.{class_name}-fighting-style",
            "name": "Fighting Style",
            "source_key": class_name.title(),
            "choices": {"option": style},
        }
    ]
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "id": "weapon",
            "name": "Weapon",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "ranged" if ranged else "melee",
                "attack_ability": "dexterity" if ranged else "strength",
                "damage_formula": damage,
                "damage_type": "slashing",
                "properties": list(properties),
                "normal_range_ft": 30,
                "long_range_ft": 120,
                "thrown_normal_range_ft": 30,
                "thrown_long_range_ft": 120,
                "versatile_damage_formula": "1d10" if "versatile" in properties else "",
                **({"attack_bonus_override": override} if override is not None else {}),
            },
        },
    )
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")
    return {
        "id": "attacker",
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
        "position": {"x": 0, "y": 0},
    }


def target():
    actor = styled("Defense")
    actor.update(id="target", position={"x": 1, "y": 0})
    return actor


@pytest.mark.parametrize("class_name", ["fighter", "ranger"])
@pytest.mark.parametrize("explicit,override", [(False, None), (True, None), (False, 7), (True, 7)])
def test_archery_derived_and_settlement_apply_once(class_name, explicit, override):
    actor = styled("Archery", class_name, ranged=True, properties=["thrown"], override=override)
    expected = (4 if override is None else override) + 2
    card = actor["derived"]["inventory"]["weapon_attacks"][0]
    assert card["attack_bonus"] == expected
    plan = preflight_attack(
        actor,
        target(),
        action={
            "weapon_id": "weapon",
            "attack_mode": "ranged",
            **({"attack_ability": "dexterity"} if explicit else {}),
        },
    )
    assert plan["attack_bonus"] == expected
    assert roll_attack_action(plan=plan, rng=Dice(10))["total"] == 10 + expected


@pytest.mark.parametrize(
    "edition,class_name,ranged,expected",
    [
        ("2024", "fighter", True, 0),
        ("2014", "paladin", True, 0),
        ("2014", "fighter", False, 0),
        ("2014", "ranger", True, 2),
    ],
)
def test_archery_respects_edition_class_and_weapon_type(edition, class_name, ranged, expected):
    actor = styled("Archery", class_name, edition=edition, ranged=ranged, properties=["thrown"])
    plan = preflight_attack(
        actor, target(), action={"weapon_id": "weapon", "attack_mode": "ranged"}
    )
    assert plan["archery_bonus"] == expected


@pytest.mark.parametrize("class_name", ["fighter", "paladin", "ranger"])
def test_dueling_applies_to_every_supported_class_and_not_two_hands(class_name):
    actor = styled("Dueling", class_name, properties=["versatile"])
    plan = preflight_attack(actor, target(), action={"weapon_id": "weapon"})
    assert plan["damage_expression"] == "1d8 + 3 + 2"
    two = preflight_attack(
        actor, target(), action={"weapon_id": "weapon", "weapon_grip": "two_handed"}
    )
    assert two["damage_expression"] == "1d10 + 3"


def test_great_weapon_rerolls_once_and_retains_low_replacement():
    dice = Dice(1, 2, 2, 1, 6)
    rolled, rerolls = roll_weapon_damage("3d6 + 3", reroll_low=True, rng=dice)
    assert rolled.rolls == (2, 1, 6)
    assert rolled.total == 12
    assert [(r["from"], r["to"]) for r in rerolls] == [(1, 2), (2, 1)]
    assert not dice.values


@pytest.mark.parametrize(
    "natural,dice,expected", [(10, [1, 5, 2, 1], 9), (20, [1, 5, 2, 1, 4, 6], 19)]
)
def test_great_weapon_normal_and_critical_damage(natural, dice, expected):
    actor = styled("Great Weapon Fighting", properties=["two_handed"], damage="2d6")
    plan = preflight_attack(
        actor, target(), action={"weapon_id": "weapon", "use_great_weapon_fighting": True}
    )
    rng = Dice(natural, *dice)
    _, damaged, result = resolve_attack_action(actor, target(), plan=plan, rng=rng)
    assert result["great_weapon_fighting"]["used"]
    assert result["damage"]["input_amount"] == expected
    assert damaged["sheet"]["combat"]["hp"]["value"] == 100 - expected
    assert not rng.values


def test_great_weapon_choice_is_optional_and_validated_before_rng():
    actor = styled("Great Weapon Fighting", properties=["versatile"])
    plan = preflight_attack(actor, target(), action={"weapon_id": "weapon"})
    assert not plan["great_weapon_fighting_available"]
    with pytest.raises(CombatEngineError, match="two-handed"):
        preflight_attack(
            actor, target(), action={"weapon_id": "weapon", "use_great_weapon_fighting": True}
        )
    with pytest.raises(CombatEngineError, match="boolean"):
        preflight_attack(
            actor, target(), action={"weapon_id": "weapon", "use_great_weapon_fighting": "yes"}
        )
    actor = styled("Great Weapon Fighting", properties=["two_handed"], damage="2d6")
    plan = preflight_attack(actor, target(), action={"weapon_id": "weapon"})
    assert plan["great_weapon_fighting_available"] and not plan["use_great_weapon_fighting"]
    _, _, result = resolve_attack_action(actor, target(), plan=plan, rng=Dice(10, 1, 2))
    assert result["damage"]["input_amount"] == 6


def protection_fixture():
    actor = styled("Protection")
    sheet, identifier = add_inventory_item(
        actor["sheet"],
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2},
        },
    )
    sheet = equip_inventory_item(sheet, identifier, "shield")
    sheets = {"attacker": target()["sheet"], "target": target()["sheet"], "protector": sheet}
    encounter = {
        "ruleset": "2014",
        "positioning_mode": "grid",
        "combatants": [
            {"actor_id": "attacker", "position": {"x": 0, "y": 0}},
            {"actor_id": "target", "position": {"x": 1, "y": 0}},
            {
                "actor_id": "protector",
                "position": {"x": 2, "y": 0},
                "size": "medium",
                "turn_budget": {"reaction": 1},
            },
        ],
    }
    return sheets, encounter


@pytest.mark.parametrize(
    "invalid", ["self", "far", "hidden", "blind", "reaction", "shield", "edition", "incapacitated"]
)
def test_protection_eligibility_does_not_spend_or_roll(invalid):
    sheets, encounter = protection_fixture()
    assert protection_candidates(encounter, sheets, "attacker", "target") == ["protector"]
    protector = encounter["combatants"][2]
    target_id = "target"
    if invalid == "self":
        target_id = "protector"
    elif invalid == "far":
        protector["position"]["x"] = 3
    elif invalid == "hidden":
        encounter["combatants"][0]["hidden"] = True
    elif invalid == "blind":
        protector["conditions"] = ["blinded"]
    elif invalid == "reaction":
        protector["turn_budget"]["reaction"] = 0
    elif invalid == "shield":
        sheets["protector"] = equip_inventory_item(sheets["protector"], "shield", None)
    elif invalid == "edition":
        sheets["protector"]["edition"] = "2024"
    else:
        sheets["protector"]["conditions"] = ["incapacitated"]
    before = deepcopy(encounter)
    assert protection_candidates(encounter, sheets, "attacker", target_id) == []
    assert encounter == before


def test_protection_agent_facts_are_complete_and_disadvantage_cancels_advantage():
    sheets, encounter = protection_fixture()
    encounter["positioning_mode"] = "agent"
    for actor in encounter["combatants"]:
        actor.pop("position")
    with pytest.raises(NeedsRulingError, match="scene facts"):
        protection_candidates(encounter, sheets, "attacker", "target")
    facts = {
        "decision_id": "scene-1",
        "reason": "Shield bearer sees attacker next to ally.",
        "actors": [{"actor_id": "protector", "within_5_ft": True, "can_see_attacker": True}],
    }
    assert protection_candidates(encounter, sheets, "attacker", "target", facts=facts) == [
        "protector"
    ]
    plan = preflight_attack(styled("Defense"), target(), action={"weapon_id": "weapon"})
    plan["advantage"] = True
    applied = apply_protection(plan, ["protector"])
    dice = Dice(10)
    attack = roll_attack_action(plan=applied, rng=dice)
    assert len(attack["rolls"]) == 1
    assert not dice.values
    assert attack["protection"]["actor_ids"] == ["protector"]


def test_additional_style_and_unknown_source_binding():
    actor = styled("Archery")
    feature = actor["sheet"]["content"]["features"][0]
    feature["id"] = "dnd5e.content.srd2014.feature.fighter-additional-fighting-style"
    assert has_style(actor["sheet"], "archery")
    feature["id"] = "custom-fighting-style"
    assert not has_style(actor["sheet"], "archery")


def test_great_weapon_leaves_additional_damage_dice_unchanged():
    actor = styled("Great Weapon Fighting", properties=["two_handed"], damage="2d6")
    weapon = actor["sheet"]["inventory"]["items"][0]
    weapon["mechanics"]["additional_damage"] = [{"damage_formula": "1d6", "damage_type": "fire"}]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    plan = preflight_attack(actor, target(), action={
        "weapon_id": "weapon", "use_great_weapon_fighting": True,
    })
    dice = Dice(10, 1, 6, 2, 2, 1)
    _, _, result = resolve_attack_action(actor, target(), plan=plan, rng=dice)
    assert result["damage"]["roll_parts"][0]["rolls"] == [6, 2]
    assert result["damage"]["roll_parts"][1]["rolls"] == [1]
    assert len(result["great_weapon_fighting"]["rerolls"]) == 2
    assert not dice.values
