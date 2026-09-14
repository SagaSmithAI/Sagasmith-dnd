import pytest

from sagasmith_dnd.character_schema import add_effect, default_character_sheet
from sagasmith_dnd.conditions import (
    DEATH_SAVE_SETTLED_CONDITIONS,
    INCAPACITATING_STATE_IDS,
    LIVING_INCAPACITATING_STATE_IDS,
    apply_condition_change,
    condition_immunities_for_source,
    effect_is_immune,
    reconcile_condition_projection,
)


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
