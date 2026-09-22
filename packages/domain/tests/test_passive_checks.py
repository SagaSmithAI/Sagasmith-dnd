"""The same actor modifiers must govern rolled and passive 2014 checks."""

from copy import deepcopy

import pytest

from sagasmith_dnd.abilities import ABILITY_NAMES, SKILL_ABILITIES
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.chase_engine import advance_chase_turn, start_chase
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError, resolve_actor_check
from sagasmith_dnd.rule_engine import resolution_context


class NoRng:
    def randint(self, *_):
        raise AssertionError("a passive check must not draw random numbers")


class TenRng:
    def randint(self, low, high):
        assert low <= 10 <= high
        return 10


def actor(sheet=None, *, identifier="observer", rules=None):
    sheet = validate_character_sheet(sheet or default_character_sheet())
    return {"id": identifier, "sheet": sheet, "derived": derive_character_sheet(sheet, rules=rules)}


def passive(subject, ability="perception", **kwargs):
    return resolve_actor_check(
        subject, kind="check", ability=ability, dc=15, passive=True, rng=NoRng(), **kwargs
    )


@pytest.mark.parametrize("ability", [*ABILITY_NAMES, *SKILL_ABILITIES])
@pytest.mark.parametrize("proficiency", ["none", "half", "proficient", "expertise"])
def test_passive_uses_all_authoritative_ability_and_skill_modifiers(ability, proficiency):
    sheet = default_character_sheet()
    for index, entry in enumerate(sheet["abilities"].values()):
        entry["score"] = 8 + 2 * index
    if ability in SKILL_ABILITIES:
        sheet["skills"][ability].update(proficiency=proficiency, bonus=2)
    subject = actor(sheet)
    before = deepcopy(subject)
    rolled = resolve_actor_check(subject, kind="check", ability=ability, dc=15, rng=TenRng())
    result = passive(subject, ability)
    assert result["total"] == rolled["total"]
    assert result["success"] == rolled["success"]
    assert result["natural"] is None and result["rolls"] == []
    assert subject == before


@pytest.mark.parametrize(
    ("advantage", "disadvantage", "adjustment"),
    [
        (False, False, 0),
        (True, False, 5),
        (False, True, -5),
        (True, True, 0),
    ],
)
def test_passive_net_advantage_never_rolls(advantage, disadvantage, adjustment):
    result = passive(actor(), advantage=advantage, disadvantage=disadvantage)
    assert result["total"] == 10 + adjustment
    assert result["passive_adjustment"] == adjustment


def test_passive_variant_uses_selected_ability_but_keeps_skill_expertise():
    sheet = default_character_sheet()
    sheet["abilities"]["strength"]["score"] = 8
    sheet["abilities"]["constitution"]["score"] = 18
    sheet["skills"]["athletics"].update(proficiency="expertise", bonus=1)
    subject = actor(sheet)
    assert passive(subject, "athletics")["total"] == 14
    assert passive(subject, "athletics", skill_ability="constitution")["total"] == 19
    with pytest.raises(CombatEngineError, match="skill_ability"):
        passive(subject, "strength", skill_ability="constitution")


@pytest.mark.parametrize(
    "proficiency,expected", [("none", 11), ("half", 11), ("proficient", 12), ("expertise", 14)]
)
def test_passive_jack_and_half_proficiency_do_not_stack(proficiency, expected):
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "jack",
            "name": "Jack of All Trades",
            "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
        }
    ]
    sheet["skills"]["perception"]["proficiency"] = proficiency
    subject = actor(sheet)
    assert passive(subject)["total"] == expected
    assert subject["derived"]["passive_perception"] == expected
    assert passive(subject, "constitution")["total"] == 11


@pytest.mark.parametrize("condition", ["poisoned", "exhaustion"])
def test_passive_sheet_and_resolver_share_condition_penalties(condition):
    sheet = default_character_sheet()
    if condition == "exhaustion":
        sheet["combat"]["exhaustion"] = 1
    else:
        sheet["conditions"] = [condition]
    subject = actor(sheet)
    assert subject["derived"]["passive_perception"] == 5
    assert passive(subject)["total"] == 5
    assert passive(subject, advantage=True)["total"] == 10


@pytest.mark.parametrize("equipment", ["armor", "encumbrance"])
def test_passive_physical_ability_variant_obeys_equipment(equipment):
    sheet = default_character_sheet()
    if equipment == "armor":
        sheet, identifier = add_inventory_item(
            sheet,
            {
                "id": "mail",
                "name": "Chain mail",
                "kind": "armor",
                "weight_oz": 880,
                "mechanics": {
                    "base_ac": 16,
                    "category": "heavy",
                    "dexterity_mode": "none",
                    "stealth_disadvantage": True,
                },
            },
        )
        sheet = equip_inventory_item(sheet, identifier, "armor")
    else:
        sheet["inventory"]["encumbrance"]["mode"] = "variant"
        sheet, _ = add_inventory_item(
            sheet, {"id": "load", "name": "Load", "kind": "equipment", "weight_oz": 1800}
        )
    subject = actor(sheet)
    assert passive(subject, "stealth")["total"] == 5
    penalized_ability = "strength" if equipment == "armor" else "constitution"
    assert passive(subject, "athletics", skill_ability=penalized_ability)["total"] == 5
    assert passive(subject, "athletics", skill_ability="intelligence")["total"] == 10


def test_passive_effect_disadvantage_is_not_misread_as_advantage():
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "source-effect",
            "name": "A sourced penalty",
            "active": True,
            "changes": [
                {"path": "rolls.ability_check.disadvantage", "mode": "set", "value": True},
                {"path": "rolls.ability_check.bonus", "mode": "add", "value": -2},
            ],
        }
    ]
    subject = actor(sheet)
    assert passive(subject)["total"] == 3
    assert subject["derived"]["passive_perception"] == 3
    rolled = resolve_actor_check(subject, kind="check", ability="perception", dc=15, rng=TenRng())
    assert rolled["roll_mode"] == "disadvantage"
    sheet["effects"][0]["active"] = False
    assert passive(actor(sheet))["total"] == 10


def test_passive_rule_pack_modifiers_are_applied_once():
    rules = resolution_context(
        {
            "edition": "2014",
            "fingerprint": "passive-test",
            "lock": [],
            "mechanics": [
                {
                    "id": "dnd5e.test.check-bonus",
                    "event": "check.before",
                    "operations": [
                        {"op": "modifier.add", "target": "check_bonus", "value": 2},
                        {"op": "advantage.add"},
                    ],
                    "citations": [{"source": "local:test", "section": "Checks"}],
                },
                {
                    "id": "dnd5e.test.passive-bonus",
                    "event": "character.derive",
                    "operations": [
                        {"op": "modifier.add", "target": "passive_perception", "value": 3},
                    ],
                    "citations": [{"source": "local:test", "section": "Passives"}],
                },
            ],
        }
    )
    subject = actor(rules=rules)
    assert subject["derived"]["passive_perception"] == 20
    assert passive(subject, rules=rules)["total"] == 20


def test_passive_frightened_requires_visibility_and_does_not_cache_a_fabricated_score():
    sheet = default_character_sheet()
    sheet["conditions"] = ["frightened"]
    sheet["effects"] = [
        {
            "id": "fear",
            "name": "Fear",
            "active": True,
            "source": "foe",
            "kind": "timed_conditions",
            "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
        }
    ]
    subject = actor(sheet)
    assert subject["derived"]["passive_perception"] is None
    with pytest.raises(NeedsRulingError, match="visibility"):
        passive(subject)
    assert (
        passive(subject, encounter={"combatants": [{"actor_id": "foe", "conditions": []}]})["total"]
        == 5
    )


def test_chase_refreshes_passive_after_actor_condition_changes():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    pursuer = {**actor(sheet), "initiative": 20}
    quarry = {**actor(sheet, identifier="quarry"), "initiative": 10}
    chase = start_chase([pursuer, quarry], quarry_ids=["quarry"], initial_distance_ft=100)
    assert chase["pursuer_passive_perception_max"] == 10
    sheet["conditions"] = ["poisoned"]
    pursuer = actor(sheet)
    result = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value=pursuer["id"],
        action="move",
        pursuer_actors={pursuer["id"]: pursuer},
        rng=TenRng(),
    )
    assert result["chase"]["pursuer_passive_perception_max"] == 5


def test_chase_defers_unknown_passive_context_until_an_escape_comparison():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    quarry = {**actor(sheet, identifier="quarry"), "initiative": 10}
    sheet["conditions"] = ["frightened"]
    sheet["effects"] = [{
        "id": "fear", "kind": "timed_conditions", "source": "absent", "active": True,
        "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
    }]
    pursuer = {**actor(sheet), "initiative": 20}
    chase = start_chase([pursuer, quarry], quarry_ids=["quarry"], initial_distance_ft=100)
    assert chase["pursuer_passive_perception_max"] is None
    # Move directly to the end-of-round escape boundary in this pure fixture.
    chase["turn_index"] = 1
    with pytest.raises(NeedsRulingError, match="resolved passive Perception"):
        advance_chase_turn(
            chase, quarry, actor_id_value="quarry", action="move", rng=TenRng(),
            pursuer_actors={pursuer["id"]: pursuer}, quarry_actors={"quarry": quarry},
            quarry_visibility={"quarry": False},
        )


@pytest.mark.parametrize("condition,reliance", [("blinded", "relies_on_sight"),
                                               ("deafened", "relies_on_hearing")])
def test_passive_sensory_failure_requires_task_facts(condition, reliance):
    sheet = default_character_sheet()
    sheet["conditions"] = [condition]
    subject = actor(sheet)
    assert subject["derived"]["passive_perception"] is None
    with pytest.raises(NeedsRulingError, match="sensory task"):
        passive(subject)
    for required in (True, False):
        rules = resolution_context({"edition": "2014", "fingerprint": "sense-test",
                                    "lock": [], "mechanics": []}, facts={reliance: required})
        result = passive(subject, rules=rules)
        if required:
            assert result["total"] is None and result["automatic_failure"] is True
            assert result["success"] is False
        else:
            assert result["total"] == 10


def test_chase_escapes_when_every_pursuer_automatically_fails_perception():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    quarry = {**actor(sheet, identifier="quarry"), "initiative": 10}
    sheet["conditions"] = ["blinded"]
    pursuer = {**actor(sheet), "initiative": 20}
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "sight", "lock": [], "mechanics": []},
        facts={"relies_on_sight": True},
    )
    chase = start_chase(
        [pursuer, quarry], quarry_ids=["quarry"], initial_distance_ft=100,
        pursuer_rules={pursuer["id"]: rules},
    )
    chase["turn_index"] = 1
    result = advance_chase_turn(
        chase, quarry, actor_id_value="quarry", action="move", rng=TenRng(),
        pursuer_actors={pursuer["id"]: pursuer}, quarry_actors={"quarry": quarry},
        pursuer_rules={pursuer["id"]: rules}, quarry_visibility={"quarry": False},
    )
    assert result["chase"]["outcome"]["status"] == "quarry_escaped"
    escape = result["turn"]["escape_checks"][0]
    assert escape["automatic_success"] is True and "check" not in escape


@pytest.mark.parametrize(
    "ability,edition,kind",
    [("stealthy", "2014", "check"), ("wisdom", "2024", "check"), ("wisdom", "2014", "save")],
)
def test_passive_rejects_unknown_checks_other_editions_and_saves(ability, edition, kind):
    sheet = default_character_sheet()
    sheet["edition"] = edition
    with pytest.raises(CombatEngineError):
        resolve_actor_check(
            actor(sheet), kind=kind, ability=ability, dc=10, passive=True, rng=NoRng()
        )
