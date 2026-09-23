import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.travel import (
    forced_march_save_dcs,
    settle_forced_march_save,
    travel_duration_minutes,
    travel_passive_perception_bonus,
    travel_stealth_allowed,
    validate_travel_leg,
    validate_travel_state,
)


def _fact():
    return {
        "decision_id": "route-1",
        "reason": "The party follows the surveyed road.",
        "source_ref": "scene:road/segment-1",
        "source_excerpt": "A maintained road continues north.",
    }


@pytest.mark.parametrize(
    ("pace", "distance", "expected_minutes"),
    [("fast", 30, 450), ("normal", 24, 480), ("slow", 18, 540)],
)
def test_travel_pace_distance_and_day_columns(pace, distance, expected_minutes):
    assert travel_duration_minutes(distance, pace) == expected_minutes


def test_difficult_terrain_doubles_time_and_forced_march_dc_scales_by_hour():
    assert travel_duration_minutes(12, "normal", difficult_terrain=True) == 480
    assert travel_duration_minutes(50, "fast") == 780
    assert forced_march_save_dcs(0, 480) == []
    assert forced_march_save_dcs(480, 30) == []
    assert forced_march_save_dcs(480, 60) == [10]
    assert forced_march_save_dcs(540, 60) == [11]
    assert forced_march_save_dcs(480, 180) == [10, 11, 12]


def test_travel_leg_requires_bounded_route_and_difficult_terrain_facts():
    leg = validate_travel_leg({
        "travel_id": "trip-1", "pace": "normal", "distance_miles": 12,
        "route_fact": _fact(),
    })
    assert leg["distance_miles"] == 12
    with pytest.raises(CombatEngineError, match="terrain_fact"):
        validate_travel_leg({
            "travel_id": "trip-1", "pace": "normal", "distance_miles": 12,
            "route_fact": _fact(), "difficult_terrain": True,
        })
    with pytest.raises(CombatEngineError, match="unsupported"):
        validate_travel_leg({
            "travel_id": "trip-1", "pace": "normal", "distance_miles": 12,
            "route_fact": _fact(), "exhaustion_added": 1,
        })


def test_forced_march_save_adds_exhaustion_and_respects_immunity_and_death():
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    failed = {"kind": "save", "ability": "constitution", "dc": 10, "success": False}
    result = settle_forced_march_save(sheet, failed)
    assert result["exhaustion_added"] == 1
    assert result["sheet"]["combat"]["exhaustion"] == 1

    immune = default_character_sheet()
    immune["edition"] = "2014"
    immune["traits"]["condition_immunities"] = ["exhaustion"]
    assert settle_forced_march_save(immune, failed)["exhaustion_added"] == 0

    lethal = default_character_sheet()
    lethal["edition"] = "2014"
    lethal["combat"]["exhaustion"] = 5
    result = settle_forced_march_save(lethal, failed)
    assert result["exhaustion_added"] == 1
    assert result["died"] is True

    with pytest.raises(CombatEngineError, match="Constitution save"):
        settle_forced_march_save(sheet, {"kind": "check", "success": False})


def test_travel_state_is_strict_and_defaults_empty():
    assert validate_travel_state() == {
        "schema_version": 1, "day_index": 0, "day_elapsed_minutes": 0,
        "active": None, "ledger": [],
    }
    with pytest.raises(CombatEngineError, match="fields are invalid"):
        validate_travel_state({
            "schema_version": 1, "active": None, "ledger": [], "owner": "caller",
        })


@pytest.mark.parametrize(("pace", "perception", "stealth"), [
    ("fast", -5, False), ("normal", 0, False), ("slow", 0, True),
])
def test_declared_travel_pace_controls_passive_perception_and_stealth(
    pace, perception, stealth,
):
    state = {
        "combat": {"active": False},
        "travel": {"active": {"pace": pace, "participant_ids": ["scout"]}},
    }
    assert travel_passive_perception_bonus(state, "scout") == perception
    assert travel_stealth_allowed(state, "scout") is stealth
    assert travel_passive_perception_bonus(state, "other") == 0
    assert travel_stealth_allowed({**state, "combat": {"active": True}}, "scout") is False
