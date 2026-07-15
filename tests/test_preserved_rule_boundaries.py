import random

import pytest

from sagasmith_dnd.ability_generation import roll_ability_scores
from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    NeedsRulingError,
    apply_damage_to_sheet,
    available_actions,
    preflight_attack,
    resolve_actor_check,
    resolve_attack_action,
    resolve_common_action,
    resolve_readied_action_window,
    spend_movement,
    stand_up,
    start_encounter,
    trigger_readied_action,
)
from sagasmith_dnd.lifecycle import apply_rest
from sagasmith_dnd.rule_engine import resolution_context


def _rules(edition: str = "2014"):
    return resolution_context(
        {"edition": edition, "fingerprint": "extensions", "lock": [], "mechanics": []}
    )


def _actor(identifier: str, *, initiative: int = 10) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    return {
        "id": identifier,
        "name": identifier,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
        "initiative": initiative,
        "tie_breaker": 0,
    }


def test_core_pack_preserves_2024_generation_unarmored_ac_and_reach() -> None:
    rolled = roll_ability_scores("2024", rng=random.Random(2))
    assert len(rolled["rolls"]) == 6
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["abilities"]["dexterity"]["score"] = 14
    sheet["inventory"]["items"] = [
        {
            "id": "glaive",
            "name": "Glaive",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d10",
                "damage_type": "slashing",
                "properties": ["reach", "two_handed"],
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "glaive"
    derived = derive_character_sheet(validate_character_sheet(sheet), rules=_rules("2024"))
    assert derived["armor_class"] == 12
    assert derived["armor_class_breakdown"]["mode"] == "unarmored"
    assert derived["inventory"]["weapon_attacks"][0]["reach_ft"] == 10
    receipt_ids = {item["mechanic_id"] for item in derived["rule_receipts"]}
    assert {
        "dnd5e.core.armor_class.unarmored",
        "dnd5e.core.weapon.reach",
    } <= receipt_ids


def test_core_pack_preserves_edition_actions_and_condition_rulings() -> None:
    first = _actor("first", initiative=20)
    second = _actor("second", initiative=10)
    rules_2014 = start_encounter([first, second], ruleset="2014")
    rules_2024 = start_encounter([first, second], ruleset="2024")
    assert "use_object" in available_actions(rules_2014, "first")
    assert "influence" not in available_actions(rules_2014, "first")
    assert {"influence", "study", "utilize"} <= set(
        available_actions(rules_2024, "first")
    )

    first["conditions"] = ["frightened"]
    with pytest.raises(NeedsRulingError, match="condition source"):
        preflight_attack(first, second, action={})


def test_core_pack_preserves_hidden_reveal_and_2024_knockout() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["hidden"] = True
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "club",
            "attack_type": "melee",
            "attack_bonus": 99,
            "damage_expression": "1",
            "damage_type": "bludgeoning",
        }
    ]
    plan = preflight_attack(
        attacker, target, action={"weapon_id": "club", "knock_out": True}
    )
    updated_attacker, updated_target, result = resolve_attack_action(
        attacker, target, plan=plan, rng=random.Random(1)
    )
    assert updated_attacker["hidden"] is False
    assert result["reveals_attacker"] is True

    knocked_out = apply_damage_to_sheet(
        updated_target["sheet"],
        amount=50,
        damage_type="bludgeoning",
        ruleset="2024",
        knock_out=True,
        melee=True,
    )
    assert knocked_out["sheet"]["combat"]["hp"]["value"] == 1
    assert "stable" in knocked_out["sheet"]["conditions"]


def test_core_pack_preserves_prone_grapple_and_ready_boundaries() -> None:
    actor = _actor("actor", initiative=20)
    other = _actor("other", initiative=10)
    actor["sheet"]["conditions"] = ["prone"]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    encounter = start_encounter([actor, other])
    with pytest.raises(ValueError, match="must crawl or stand"):
        spend_movement(encounter, "actor", 5)
    crawled = spend_movement(encounter, "actor", 5, crawl=True)
    assert crawled["combatants"][0]["turn_budget"]["movement"] == 20
    stood = stand_up(encounter, "actor")
    assert "prone" not in stood["combatants"][0]["conditions"]

    encounter["combatants"][0]["conditions"] = ["grappled"]
    with pytest.raises(NeedsRulingError, match="grapple source"):
        spend_movement(encounter, "actor", 5)

    ready = resolve_common_action(
        start_encounter([_actor("actor", initiative=20), other]),
        actor_id_value="actor",
        action="ready",
        trigger="the door opens",
        payload={"action": "dash"},
    )
    readied_id = ready["readied"][0]["id"]
    triggered = trigger_readied_action(ready, readied_id=readied_id, event="door opened")
    choice = triggered["pending"][0]["id"]
    resolved, _ = resolve_readied_action_window(
        triggered, actor_id_value="actor", choice_id=choice, release=True
    )
    assert resolved["combatants"][0]["turn_budget"]["reaction"] == 0


def test_core_pack_preserves_restrained_save_and_2014_rest_allocation() -> None:
    actor = _actor("actor")
    actor["sheet"]["conditions"] = ["restrained"]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="dexterity",
        dc=10,
        rules=_rules(),
        rng=random.Random(3),
    )
    assert len(result["rolls"]) == 2
    assert result["rule_receipts"][0]["mechanic_id"] == (
        "dnd5e.core.save.restrained_dexterity"
    )

    unrestricted = _actor("unrestricted")
    ordinary_save = resolve_actor_check(
        unrestricted,
        kind="save",
        ability="dexterity",
        dc=10,
        rules=_rules(),
        rng=random.Random(3),
    )
    assert "dnd5e.core.save.restrained_dexterity" not in {
        item["mechanic_id"] for item in ordinary_save["rule_receipts"]
    }

    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hit_dice"] = {
        "fighter-d10": {
            "label": "d10",
            "value": 0,
            "max": 2,
            "recovers_on": "none",
            "source_key": "fighter",
        },
        "wizard-d6": {
            "label": "d6",
            "value": 0,
            "max": 2,
            "recovers_on": "none",
            "source_key": "wizard",
        },
    }
    with pytest.raises(ValueError, match="player allocation"):
        apply_rest(sheet, rest_type="long_rest")
    recovered = apply_rest(
        sheet,
        rest_type="long_rest",
        hit_dice_recovery={"fighter-d10": 1, "wizard-d6": 1},
    )
    assert recovered["sheet"]["combat"]["hit_dice"]["fighter-d10"]["value"] == 1
