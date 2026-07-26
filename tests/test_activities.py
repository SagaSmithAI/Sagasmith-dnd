import pytest

from sagasmith_dnd.activities import ActivityError, consume_activity
from sagasmith_dnd.character_schema import (
    default_character_sheet,
    validate_character_sheet,
)


def test_activity_consumes_its_shared_resource_without_inventing_an_effect() -> None:
    sheet = default_character_sheet()
    sheet["resources"]["second_wind"] = {
        "label": "Second Wind",
        "value": 1,
        "max": 1,
        "recovers_on": "short_rest",
        "source_key": "fighter",
    }
    sheet["content"]["features"] = [
        {
            "id": "second-wind",
            "name": "Second Wind",
            "source_key": "fighter",
            "description": "Recover hit points.",
            "uses": {},
            "resource_key": "second_wind",
            "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
            "scaling": [],
            "choices": {"healing": "DM rolls by level"},
        }
    ]
    result = consume_activity(sheet, activity_id="second-wind")
    assert result["sheet"]["resources"]["second_wind"]["value"] == 0
    assert result["requires_ruling"] is True
    assert result["payment"] == {"kind": "resource", "key": "second_wind", "amount": 1}


def test_activity_rejects_passive_and_exhausted_cards() -> None:
    sheet = default_character_sheet()
    sheet["content"]["activities"] = [
        {
            "id": "passive",
            "name": "Passive",
            "source_key": "test",
            "description": "",
            "uses": {},
            "resource_key": "",
            "activation": {"type": "passive", "cost": 0, "trigger": ""},
            "scaling": [],
            "choices": {},
        }
    ]
    with pytest.raises(ActivityError, match="passive"):
        consume_activity(sheet, activity_id="passive")


def test_activity_distinguishes_zero_capacity_from_unlimited_uses() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "no-divine-sense",
            "name": "Divine Sense",
            "uses": {"value": 0, "max": 0, "unlimited": False},
            "activation": {"type": "action", "cost": 1},
        },
        {
            "id": "archdruid-wild-shape",
            "name": "Wild Shape",
            "uses": {"value": 0, "max": 0, "unlimited": True},
            "activation": {"type": "action", "cost": 1},
        },
    ]

    with pytest.raises(ActivityError, match="exhausted"):
        consume_activity(sheet, activity_id="no-divine-sense")

    result = consume_activity(sheet, activity_id="archdruid-wild-shape")
    assert result["payment"] is None
    assert result["sheet"]["content"]["features"][1]["uses"]["unlimited"] is True


def test_zero_capacity_without_an_explicit_unlimited_flag_fails_closed() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "legacy-empty-resource",
            "name": "Legacy Empty Resource",
            "uses": {"value": 0, "max": 0},
            "activation": {"type": "action", "cost": 1},
        }
    ]

    with pytest.raises(ActivityError, match="exhausted"):
        consume_activity(sheet, activity_id="legacy-empty-resource")


def test_omitted_uses_remains_unlimited_after_card_validation() -> None:
    sheet = default_character_sheet()
    sheet["content"]["activities"] = [
        {
            "id": "source-action",
            "name": "Source Action",
            "source_key": "module-page",
            "activation": {"type": "action", "cost": 1},
        }
    ]

    validated = validate_character_sheet(sheet)
    result = consume_activity(validated, activity_id="source-action")

    assert validated["content"]["activities"][0]["uses"]["unlimited"] is True
    assert result["status"] == "committed"
    assert result["payment"] is None
