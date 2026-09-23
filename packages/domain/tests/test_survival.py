import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.survival import (
    SurvivalNeedsCheckError,
    needs_water_save,
    settle_survival_day,
    validate_daily_intake,
)


def _settle(sheet, state=None, intake=None, **kwargs):
    return settle_survival_day(
        sheet,
        state,
        intake or {"food_lb": 0, "water_gallons": 0},
        day_index=kwargs.pop("day_index", 0),
        **kwargs,
    )


def test_starvation_uses_current_constitution_grace_and_half_rations():
    sheet = default_character_sheet()
    sheet["abilities"]["constitution"]["score"] = 10
    state = None
    for day in range(6):
        settled = _settle(
            sheet,
            state,
            {"food_lb": 0.5, "water_gallons": 1},
            day_index=day,
        )
        sheet, state = settled["sheet"], settled["actor_state"]
        assert settled["exhaustion_added"] == 0
    settled = _settle(
        sheet,
        state,
        {"food_lb": 0.5, "water_gallons": 1},
        day_index=6,
    )
    assert settled["constitution_grace_days"] == 3
    assert settled["food_deprivation_days"] == 3.5
    assert settled["exhaustion_added"] == 0
    sheet["abilities"]["constitution"]["score"] = 8
    settled = _settle(
        sheet,
        settled["actor_state"],
        {"food_lb": 0, "water_gallons": 1},
        day_index=7,
    )
    assert settled["constitution_grace_days"] == 2
    assert settled["exhaustion_added"] == 2


def test_water_half_requires_save_and_hot_weather_doubles_requirement():
    assert needs_water_save({"water_gallons": 0.5})
    assert needs_water_save({"water_gallons": 1, "hot_weather": True,
                             "weather_fact": {
                                 "decision_id": "weather-1", "reason": "The noon heat is severe.",
                                 "source_ref": "scene:desert", "source_excerpt": "Blazing heat.",
                             }})
    sheet = default_character_sheet()
    with pytest.raises(SurvivalNeedsCheckError):
        _settle(sheet, intake={"food_lb": 1, "water_gallons": 0.5})
    settled = _settle(
        sheet,
        intake={"food_lb": 1, "water_gallons": 0.5},
        water_save={"kind": "save", "success": False, "dc": 15, "total": 12},
    )
    assert settled["exhaustion_added"] == 1
    assert settled["water_save"]["dc"] == 15
    hot = _settle(
        sheet,
        intake={"food_lb": 1, "water_gallons": 1, "hot_weather": True,
                 "weather_fact": {
                     "decision_id": "weather-1", "reason": "The noon heat is severe.",
                     "source_ref": "scene:desert", "source_excerpt": "Blazing heat.",
                 }},
        water_save={"kind": "save", "success": True, "dc": 15, "total": 15},
    )
    assert hot["water_required_gallons"] == 2
    assert hot["exhaustion_added"] == 0


def test_water_deprivation_doubles_when_exhaustion_preexists():
    sheet = default_character_sheet()
    sheet = _settle(
        sheet,
        intake={"food_lb": 1, "water_gallons": 0.5},
        water_save={"kind": "save", "success": False},
    )["sheet"]
    settled = _settle(sheet, intake={"food_lb": 1, "water_gallons": 0})
    assert settled["exhaustion_added"] == 2


def test_full_intake_unlocks_deprivation_recovery_and_immunity_and_death_hold():
    sheet = default_character_sheet()
    deprived = _settle(sheet, intake={"food_lb": 0, "water_gallons": 0})
    assert deprived["actor_state"]["recovery_locked"] is True
    recovered_gate = _settle(
        deprived["sheet"],
        deprived["actor_state"],
        {"food_lb": 1, "water_gallons": 1},
    )
    assert recovered_gate["actor_state"]["recovery_locked"] is False
    immune = default_character_sheet()
    immune["traits"]["condition_immunities"] = ["exhaustion"]
    settled = _settle(immune, intake={"food_lb": 0, "water_gallons": 0})
    assert settled["exhaustion_added"] == 0
    dead = default_character_sheet()
    dead["conditions"] = ["dead"]
    settled_dead = _settle(dead, intake={"food_lb": 0, "water_gallons": 0})
    assert settled_dead["skipped"] == "dead"
    assert settled_dead["sheet"]["combat"]["exhaustion"] == 0


def test_daily_intake_rejects_authored_outcomes_and_unbounded_weather():
    with pytest.raises(CombatEngineError):
        validate_daily_intake({"food_lb": 1, "exhaustion_added": 0})
    with pytest.raises(CombatEngineError):
        validate_daily_intake({"hot_weather": True})
    with pytest.raises(CombatEngineError):
        validate_daily_intake({"water_gallons": -1})
