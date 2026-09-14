import copy

import pytest

from sagasmith_dnd.character_schema import (
    active_effect_roll_advantage,
    active_effect_roll_bonus,
    add_effect,
    default_character_sheet,
    remove_effect,
)
from sagasmith_dnd.conditions import (
    DEATH_SAVE_SETTLED_CONDITIONS,
    INCAPACITATING_STATE_IDS,
    LIVING_INCAPACITATING_STATE_IDS,
    apply_condition_change,
    condition_immunities_for_source,
    effect_is_immune,
    reconcile_condition_projection,
)


def _effect(effect_id: str, kind: str, condition: str) -> dict:
    return {
        "id": effect_id,
        "name": effect_id,
        "kind": kind,
        "source": "test-source",
        "active": True,
        "concentration": False,
        "duration": {"period": "hour", "remaining": 3},
        "changes": [{"path": "conditions", "mode": "add", "value": condition}],
        "description": "",
    }


def test_condition_state_groups_have_one_canonical_relationship() -> None:
    assert DEATH_SAVE_SETTLED_CONDITIONS == {"dead", "stable"}
    assert LIVING_INCAPACITATING_STATE_IDS == INCAPACITATING_STATE_IDS - {"dead"}


def test_condition_projection_respects_immunity_and_active_effect_ownership() -> None:
    sheet = default_character_sheet()
    sheet["traits"]["condition_immunities"] = ["stunned"]
    sheet["conditions"] = ["prone"]
    sheet["effects"] = [
        {
            "id": "held-prone",
            "name": "Held Prone",
            "kind": "timed_conditions",
            "source": "module",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "prone"}],
            "description": "",
        }
    ]

    actual = reconcile_condition_projection(sheet, {"stunned"})

    assert actual == {"prone"}
    assert sheet["conditions"] == ["prone"]


def test_petrified_rejects_new_poison_and_disease_without_mutating_the_sheet() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["petrified"]
    before = copy.deepcopy(sheet)

    with pytest.raises(ValueError, match="blocked while the creature is petrified"):
        add_effect(sheet, _effect("new-poison", "poison", "poisoned"))
    with pytest.raises(ValueError, match="blocked while the creature is petrified"):
        add_effect(sheet, _effect("new-disease", "disease", "diseased"))
    apply_condition_change(sheet, condition_id="poisoned", add=True)

    assert sheet == before


def test_petrified_suspends_effect_conditions_until_all_petrified_sources_end() -> None:
    sheet, _ = add_effect(default_character_sheet(), _effect("poison", "poison", "poisoned"))
    sheet, petrified_id = add_effect(sheet, _effect("stone-a", "timed_conditions", "petrified"))
    sheet, second_petrified_id = add_effect(
        sheet, _effect("stone-b", "timed_conditions", "petrified")
    )

    assert sheet["conditions"] == ["petrified"]
    sheet = remove_effect(sheet, petrified_id)
    assert sheet["conditions"] == ["petrified"]
    sheet = remove_effect(sheet, second_petrified_id)

    assert sheet["conditions"] == ["poisoned"]
    assert next(effect for effect in sheet["effects"] if effect["id"] == "poison")["active"]


def test_petrified_suspends_poison_effect_roll_modifiers_without_deactivating_the_effect() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["petrified", "poisoned"]
    sheet["effects"] = [
        {
            **_effect("poison", "poison", "poisoned"),
            "changes": [{"path": "rolls.ability_check.bonus", "mode": "add", "value": 3}],
        }
    ]

    assert active_effect_roll_bonus(sheet, "ability") == 0
    sheet["conditions"] = ["poisoned"]
    assert active_effect_roll_bonus(sheet, "ability") == 3
    assert sheet["effects"][0]["active"] is True


def test_petrified_suspends_poison_effect_roll_advantage_without_deactivating_the_effect() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["petrified"]
    sheet["effects"] = [
        {
            **_effect("poison", "poison", "poisoned"),
            "changes": [{"path": "rolls.ability_check.advantage", "mode": "set", "value": True}],
        }
    ]

    assert active_effect_roll_advantage(sheet, "ability") == (False, False)
    sheet["conditions"] = []
    assert active_effect_roll_advantage(sheet, "ability") == (True, False)
    assert sheet["effects"][0]["active"] is True


def test_conditional_nature_ward_immunity_requires_authoritative_source_type() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "nature-ward",
            "name": "Nature's Ward",
            "choices": {
                "_conditional_condition_immunities": {
                    "charmed": ["elemental", "fey"],
                    "frightened": ["elemental", "fey"],
                }
            },
        }
    ]

    assert condition_immunities_for_source(sheet, "charmed", source_creature_type="fey")
    assert not condition_immunities_for_source(
        sheet, "charmed", source_creature_type="aberration"
    )
    with pytest.raises(ValueError, match="authoritative source creature type"):
        condition_immunities_for_source(sheet, "charmed")

    apply_condition_change(
        sheet,
        condition_id="charmed",
        add=True,
        source_creature_type="beast",
    )
    assert sheet["conditions"] == ["charmed"]


def test_static_feature_immunity_rejects_poison_and_disease_effects() -> None:
    sheet = default_character_sheet()
    sheet["traits"]["immunities"] = ["poison"]
    sheet["traits"]["condition_immunities"] = ["disease", "poisoned"]
    poison = {
        "id": "poison-effect",
        "name": "Poison",
        "kind": "poison",
        "source": "hazard",
        "active": True,
        "concentration": False,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [],
        "description": "",
    }
    disease = {**poison, "id": "disease-effect", "kind": "nonmagical_disease"}

    assert effect_is_immune(sheet, poison)
    assert effect_is_immune(sheet, disease)
    with pytest.raises(ValueError, match="blocked"):
        add_effect(sheet, poison)
    with pytest.raises(ValueError, match="blocked"):
        add_effect(sheet, disease)

    inactive = {**disease, "id": "inactive-disease", "active": False}
    updated, effect_id = add_effect(sheet, inactive)
    assert effect_id == "inactive-disease"
    assert updated["effects"][0]["active"] is False
