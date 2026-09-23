from copy import deepcopy
from pathlib import Path

import pytest
from test_fighting_styles import Dice, styled, target

from sagasmith_dnd import divine_smite as smite
from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.combat_engine import preflight_attack, resolve_attack_action
from sagasmith_dnd.core_content import PACK_ID, PACK_VERSION, build_srd2014_content
from sagasmith_dnd.legendary_resistance import SaveDecisionRequiredError


def paladin(slot=1, *, pact=False):
    actor = styled("", properties=["finesse", "thrown"])
    sheet = actor["sheet"]
    sheet["progression"].update(level=2, classes=[{"name": "Paladin", "level": 2, "hit_die": 10}])
    _, artifacts = build_srd2014_content(Path(__file__).parents[3] / "skills")
    entry = next(a for a in artifacts if a["id"] == smite.FEATURE)
    sheet["content"]["features"] = [
        {
            **{
                k: deepcopy(v)
                for k, v in entry["card"].items()
                if k not in {"class_name", "minimum_level", "unlock_levels"}
            },
            "id": smite.FEATURE,
            "pack_id": PACK_ID,
            "pack_version": PACK_VERSION,
            "rule_refs": entry["rule_refs"],
            "mechanic_refs": entry["mechanic_refs"],
        }
    ]
    sheet["spellcasting"]["ability"] = "charisma"
    if pact:
        sheet["spellcasting"]["pact_magic"] = {
            "slot_level": slot,
            "value": 1,
            "max": 1,
            "recovers_on": "short_rest",
        }
    else:
        sheet["spellcasting"]["spell_slots"] = {
            str(slot): {"value": 1, "max": 1, "recovers_on": "long_rest"}
        }
    actor["derived"] = derive_character_sheet(sheet)
    return actor


@pytest.mark.parametrize(
    "slot,species,critical,pact",
    [
        (1, "humanoid", False, False),
        (2, "undead", True, False),
        (4, "fiend (devil)", False, False),
        (9, "undead", True, False),
        (5, "fiendish human", False, True),
        (1, "undead", False, True),
    ],
)
def test_real_attack_damage_dice_slot_and_defense(slot, species, critical, pact):
    actor, enemy = paladin(slot, pact=pact), target()
    enemy["sheet"]["progression"]["species"] = species
    enemy["sheet"]["traits"]["resistances"] = ["radiant"]
    plan = preflight_attack(
        actor, enemy, action={"weapon_id": "weapon", "attack_ability": "dexterity"}
    )
    count = min(slot + 1, 5) + int(species == "undead" or species == "fiend (devil)")
    dice_count = count * (2 if critical else 1)
    key = "pact_magic" if pact else str(slot)
    with smite.decisions(lambda *args: key):
        changed, _, result = resolve_attack_action(
            actor,
            enemy,
            plan=plan,
            rng=Dice(20 if critical else 18, *([4] * (2 if critical else 1)), *([3] * dice_count)),
        )
    assert result["divine_smite"]["damage_expression"] == f"{count}d8"
    assert result["damage"]["applied_amount"] == (8 if critical else 4) + 2 + (3 * dice_count // 2)
    assert smite.slots(changed["sheet"]) == []
    assert smite.slots(actor["sheet"]) == [{"slot": key, "level": slot}]


@pytest.mark.parametrize(
    "mode", ["miss", "ranged", "no_feature", "no_slots", "2024", "decline", "offer"]
)
def test_no_automatic_spend_and_ineligible_attacks(mode):
    actor, enemy = paladin(), target()
    if mode == "no_feature":
        actor["sheet"]["content"]["features"] = []
    if mode == "no_slots":
        actor["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] = 0
    if mode == "2024":
        actor["sheet"]["edition"] = "2024"
    plan = preflight_attack(
        actor,
        enemy,
        action={"weapon_id": "weapon", "attack_mode": "ranged" if mode == "ranged" else "melee"},
    )
    dice = Dice(1 if mode == "miss" else 18, 4)
    if mode == "offer":
        with pytest.raises(SaveDecisionRequiredError):
            resolve_attack_action(actor, enemy, plan=plan, rng=dice)
        assert dice.values == [4]
    else:
        with smite.decisions(lambda *args: None):
            changed, _, result = resolve_attack_action(actor, enemy, plan=plan, rng=dice)
        assert "divine_smite" not in result
        assert changed["sheet"]["spellcasting"] == actor["sheet"]["spellcasting"]
