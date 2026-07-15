from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.lifecycle import advance_effect_durations, apply_rest


def test_effect_duration_and_long_rest_recovery_are_card_local() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 2, "max": 10, "temp": 4}
    sheet["resources"] = {
        "feature": {
            "label": "Feature",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "x",
        }
    }
    sheet["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "active": True,
            "duration": {"period": "round", "remaining": 2},
        }
    ]
    advanced = advance_effect_durations(sheet, period="round_end")
    assert advanced["sheet"]["effects"][0]["duration"]["remaining"] == 1
    result = apply_rest(advanced["sheet"], rest_type="long_rest")
    assert result["sheet"]["combat"]["hp"] == {"value": 10, "max": 10, "temp": 0}
    assert result["recovered"]["feature"] == 2


def test_elapsed_time_only_advances_matching_effect_periods() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "minute-effect",
            "name": "Minute Effect",
            "active": True,
            "duration": {"period": "minute", "remaining": 1},
        },
        {
            "id": "hour-effect",
            "name": "Hour Effect",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
        },
    ]
    result = advance_effect_durations(sheet, period="minute")
    assert result["expired"] == ["minute-effect"]
    assert result["sheet"]["effects"][1]["active"] is True


def test_short_rest_uses_explicit_hit_die_roll_and_2024_long_rest_recovers_all() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["abilities"]["constitution"]["score"] = 14
    sheet["combat"]["hp"] = {"value": 2, "max": 20, "temp": 0}
    sheet["combat"]["hit_dice"] = {
        "d8": {"label": "d8", "value": 1, "max": 3, "recovers_on": "none", "source_key": "cleric"}
    }
    short_rest = apply_rest(sheet, rest_type="short_rest", hit_dice_spends=[{"key": "d8", "roll": 4}])
    assert short_rest["hit_die_healing"] == 6
    assert short_rest["sheet"]["combat"]["hp"]["value"] == 8
    long_rest = apply_rest(short_rest["sheet"], rest_type="long_rest")
    assert long_rest["sheet"]["combat"]["hit_dice"]["d8"]["value"] == 3
