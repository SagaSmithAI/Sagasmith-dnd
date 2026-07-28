from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.conditions import reconcile_condition_projection


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
