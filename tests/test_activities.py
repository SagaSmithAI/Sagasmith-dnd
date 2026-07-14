import pytest

from sagasmith_dnd.activities import ActivityError, consume_activity
from sagasmith_dnd.character_schema import default_character_sheet


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
