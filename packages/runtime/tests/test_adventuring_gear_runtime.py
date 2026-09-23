from __future__ import annotations

import pytest
from sagasmith_dnd.adventuring_gear import (
    ADVENTURING_GEAR_ACTIONS,
    ADVENTURING_GEAR_SOURCE_REF,
    resolve_adventuring_gear_intent,
)
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.engine import DiceResult
from sagasmith_dnd_runtime import application_support
from sagasmith_dnd_runtime.services.attacks import _gear_target_creature_type
from sagasmith_dnd_runtime.services.combat import (
    _settle_adventuring_gear_burning_turn_start,
)
from sagasmith_dnd_runtime.services.inventory import _authoritative_gear_creature_type


def _fire_effect(*, damage: str = "1d4") -> dict:
    return {
        "id": "gear-burning:throw-1",
        "kind": "adventuring_gear_burning",
        "mechanic_id": "dnd5e.2014.adventuring_gear.alchemists_fire",
        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
        "action_id": "throw-1",
        "source_item_id": "item-fire-1",
        "source_key": next(
            action.source_key
            for action in ADVENTURING_GEAR_ACTIONS.values()
            if action.name == "Alchemist's fire (flask)"
        ),
        "item_name": "Alchemist's fire (flask)",
        "source_actor_id": "attacker",
        "target_id": "target",
        "damage": damage,
        "damage_type": "fire",
        "active": True,
    }


def _fire_spend() -> dict:
    item = _fire_effect()
    plan = resolve_adventuring_gear_intent(
        {
            "id": item["source_item_id"],
            "name": item["item_name"],
            "source_key": item["source_key"],
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
        },
        "throw",
    )
    return {
        "id": item["action_id"],
        "item_id": item["source_item_id"],
        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
        "rule_plan": plan,
    }


def test_alchemists_fire_turn_start_damage_is_source_checked_and_once_per_token(monkeypatch):
    sheet = default_character_sheet()
    sheet["combat"]["hp"].update(value=10, max=10)
    encounter = {"ongoing_effects": [_fire_effect()]}
    item_spends = [_fire_spend()]
    monkeypatch.setattr(
        application_support,
        "roll",
        lambda expression: DiceResult(3, (3,), expression, "3"),
    )

    damaged, events = _settle_adventuring_gear_burning_turn_start(
        encounter,
        "target",
        sheet,
        "turn:target:1",
        item_spends=item_spends,
        death_saves=True,
    )

    assert damaged["combat"]["hp"]["value"] == 7
    assert events[0]["damage_roll"]["total"] == 3
    assert encounter["ongoing_effects"][0]["last_trigger_turn_token"] == "turn:target:1"
    _, repeated = _settle_adventuring_gear_burning_turn_start(
        encounter,
        "target",
        damaged,
        "turn:target:1",
        item_spends=item_spends,
        death_saves=True,
    )
    assert repeated == []


def test_alchemists_fire_turn_start_rejects_forged_damage_and_missing_spend():
    with pytest.raises(application_support.CombatEngineError, match="source-defined plan"):
        _settle_adventuring_gear_burning_turn_start(
            {"ongoing_effects": [_fire_effect(damage="99d99")]},
            "target",
            default_character_sheet(),
            "turn:target:1",
            item_spends=[_fire_spend()],
            death_saves=True,
        )
    with pytest.raises(application_support.CombatEngineError, match="item-spend receipt"):
        _settle_adventuring_gear_burning_turn_start(
            {"ongoing_effects": [_fire_effect()]},
            "target",
            default_character_sheet(),
            "turn:target:1",
            item_spends=[],
            death_saves=True,
        )


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [
        ({"progression": {"creature_type": "Undead"}}, "undead"),
        ({"progression": {"species": "Fiend"}}, "fiend"),
        ({"progression": {"species": "Human"}}, "human"),
        ({"progression": {}}, ""),
    ],
)
def test_holy_water_target_type_comes_only_from_authoritative_sheet(sheet, expected):
    assert _gear_target_creature_type(sheet) == expected


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [
        ({"creature_type": "Undead"}, "undead"),
        ({"progression": {"species": "Human"}}, "human"),
        ({"progression": {}}, ""),
    ],
)
def test_antitoxin_target_type_is_read_from_authoritative_sheet(sheet, expected):
    assert _authoritative_gear_creature_type(sheet) == expected
