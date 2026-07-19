import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.lifecycle import (
    advance_effect_durations,
    advance_world_effect_durations,
    apply_rest,
    recover_stable_creature,
    stand_outside_combat,
)


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


def test_long_rest_also_recovers_short_rest_resources() -> None:
    sheet = default_character_sheet()
    sheet["resources"] = {
        "channel_divinity": {
            "label": "Channel Divinity",
            "value": 0,
            "max": 1,
            "recovers_on": "short_rest",
            "source_key": "Cleric",
        }
    }

    result = apply_rest(sheet, rest_type="long_rest")

    assert result["sheet"]["resources"]["channel_divinity"]["value"] == 1
    assert result["recovered"]["channel_divinity"] == 1


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


def test_effect_duration_advance_accepts_audited_multi_period_amount() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "hourly-ward",
            "name": "Hourly Ward",
            "active": True,
            "duration": {"period": "hour", "remaining": 3},
        }
    ]

    result = advance_effect_durations(sheet, period="hour", amount=2)

    assert result["amount"] == 2
    assert result["advanced"] == ["hourly-ward"]
    assert result["sheet"]["effects"][0]["duration"]["remaining"] == 1

    expired = advance_effect_durations(result["sheet"], period="hour", amount=2)
    assert expired["expired"] == ["hourly-ward"]
    assert expired["sheet"]["effects"][0]["active"] is False


def test_effect_duration_advance_rejects_nonpositive_amount() -> None:
    with pytest.raises(CombatEngineError, match="positive integer"):
        advance_effect_durations(default_character_sheet(), period="hour", amount=0)


def test_world_effect_duration_uses_the_same_expiry_boundary() -> None:
    state = {
        "world_effects": [
            {
                "id": "mace-light",
                "active": True,
                "duration": {"period": "hour", "remaining": 1},
            }
        ]
    }
    result = advance_world_effect_durations(state, period="hour", amount=3)
    assert result["expired"] == ["mace-light"]
    assert result["state"]["world_effects"][0]["active"] is False


def test_short_rest_uses_explicit_hit_die_roll_and_2024_long_rest_recovers_all() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["abilities"]["constitution"]["score"] = 14
    sheet["combat"]["hp"] = {"value": 2, "max": 20, "temp": 0}
    sheet["combat"]["hit_dice"] = {
        "d8": {"label": "d8", "value": 1, "max": 3, "recovers_on": "none", "source_key": "cleric"}
    }
    short_rest = apply_rest(
        sheet, rest_type="short_rest", hit_dice_spends=[{"key": "d8", "roll": 4}]
    )
    assert short_rest["hit_die_healing"] == 6
    assert short_rest["sheet"]["combat"]["hp"]["value"] == 8
    long_rest = apply_rest(short_rest["sheet"], rest_type="long_rest")
    assert long_rest["sheet"]["combat"]["hit_dice"]["d8"]["value"] == 3


def test_stable_creature_recovers_one_hp_after_rolled_hours() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    sheet["combat"]["death_saves"] = {"successes": 0, "failures": 0}
    sheet["conditions"] = ["prone", "stable", "unconscious"]

    result = recover_stable_creature(sheet, recovery_hours=3)

    assert result["recovery_hours"] == 3
    assert result["sheet"]["combat"]["hp"]["value"] == 1
    assert result["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert result["sheet"]["conditions"] == ["prone"]
    assert sheet["combat"]["hp"]["value"] == 0


def test_stable_recovery_rejects_nonstable_dead_or_invalid_roll() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    with pytest.raises(CombatEngineError, match="Stable creature at 0"):
        recover_stable_creature(sheet, recovery_hours=1)
    sheet["conditions"] = ["dead", "stable", "unconscious"]
    with pytest.raises(CombatEngineError, match="dead creature"):
        recover_stable_creature(sheet, recovery_hours=1)
    with pytest.raises(CombatEngineError, match="integer from 1 to 4"):
        recover_stable_creature(sheet, recovery_hours=5)


def test_conscious_recovered_creature_can_stand_outside_combat() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone"]

    result = stand_outside_combat(sheet)

    assert result["status"] == "stood"
    assert result["sheet"]["conditions"] == []
    assert sheet["conditions"] == ["prone"]


def test_outside_combat_stand_rejects_unconscious_or_nonprone_creature() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone", "unconscious"]
    with pytest.raises(CombatEngineError, match="conscious living creature"):
        stand_outside_combat(sheet)
    sheet["conditions"] = []
    with pytest.raises(CombatEngineError, match="Prone condition"):
        stand_outside_combat(sheet)
