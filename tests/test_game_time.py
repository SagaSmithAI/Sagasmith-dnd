from __future__ import annotations

import pytest

from sagasmith_dnd.game_time import (
    advance_calendar_minute_point,
    advance_calendar_minutes_from_elapsed,
    advance_game_time,
    anchor_world_time,
    calendar_minute_point,
    calendar_minute_point_from_elapsed,
    game_time_from_ticks,
    game_time_ticks,
    validate_calendar_minute_point,
    validate_world_time,
)


def test_v1_clock_migrates_without_changing_its_calendar_position() -> None:
    timeline = game_time_from_ticks(33150)
    assert validate_world_time(
        {
            "schema_version": 1,
            "day": 3,
            "hour": 7,
            "minute": 15,
            "elapsed_minutes": 3315,
            "label": "Trade Way",
        },
        game_time=timeline,
    ) == {
        "schema_version": 2,
        "tick_seconds": 6,
        "calendar_offset_ticks": 0,
        "day": 3,
        "hour": 7,
        "minute": 15,
        "second": 0,
        "elapsed_minutes": 3315,
        "round_remainder": 0,
        "label": "Trade Way",
    }


def test_calendar_anchor_does_not_reset_unanchored_elapsed_game_time() -> None:
    timeline = game_time_from_ticks(37)
    clock = anchor_world_time(timeline, day=12, hour=4, minute=5, label="Sword Coast")
    assert timeline["elapsed_ticks"] == 37
    assert clock["day"] == 12
    assert clock["hour"] == 4
    assert clock["minute"] == 5
    assert clock["second"] == 0
    assert clock["calendar_offset_ticks"] == 160_850 - 37


def test_five_rounds_in_two_encounters_cross_one_shared_minute() -> None:
    started = game_time_from_ticks()
    clock = anchor_world_time(started, day=1, hour=10, label="Road")
    first = advance_game_time(started, world_time=clock, period="round", count=5)
    assert first["elapsed_minutes"] == 0
    assert first["world_time_after"]["second"] == 30

    second = advance_game_time(
        first["after"],
        world_time=first["world_time_after"],
        period="round",
        count=5,
    )
    assert second["elapsed_minutes"] == 1
    assert second["world_time_after"]["hour"] == 10
    assert second["world_time_after"]["minute"] == 1
    assert second["world_time_after"]["second"] == 0


def test_narrative_minutes_preserve_the_subminute_combat_position() -> None:
    timeline = game_time_from_ticks(5)
    clock = anchor_world_time(timeline, day=1, label="Dungeon")
    advanced = advance_game_time(
        timeline,
        world_time=clock,
        period="minute",
        count=60,
    )
    assert advanced["elapsed_ticks"] == 600
    assert advanced["elapsed_minutes"] == 60
    assert advanced["world_time_after"]["hour"] == 1
    assert advanced["world_time_after"]["minute"] == 0
    assert advanced["world_time_after"]["second"] == 0
    assert advanced["after"]["elapsed_ticks"] == 605


def test_unanchored_time_advances_without_fabricating_a_calendar() -> None:
    advanced = advance_game_time(game_time_from_ticks(), period="round", count=3)
    assert advanced["after"]["elapsed_ticks"] == 3
    assert advanced["world_time_after"] is None


def test_game_time_units_share_one_tick_scale() -> None:
    assert game_time_ticks("round", 10) == game_time_ticks("minute")
    assert game_time_ticks("minute", 60) == game_time_ticks("hour")
    assert game_time_ticks("hour", 24) == game_time_ticks("day")


def test_minute_calendar_points_share_validation_and_projection() -> None:
    point = calendar_minute_point(day=3, hour=7, minute=15)
    assert point == {
        "day": 3,
        "hour": 7,
        "minute": 15,
        "elapsed_minutes": 3315,
    }
    assert validate_calendar_minute_point(point) == point
    assert calendar_minute_point_from_elapsed(3315) == point
    assert advance_calendar_minutes_from_elapsed(3315, 60)["elapsed_minutes"] == 3375
    assert advance_calendar_minute_point(point, 60) == {
        "day": 3,
        "hour": 8,
        "minute": 15,
        "elapsed_minutes": 3375,
    }
    with pytest.raises(ValueError, match="must match"):
        validate_calendar_minute_point({**point, "elapsed_minutes": 3314})


def test_v2_rejects_a_calendar_field_that_drifts_from_game_time() -> None:
    timeline = game_time_from_ticks(17)
    clock = anchor_world_time(timeline, day=1)
    clock["minute"] = 2
    with pytest.raises(ValueError, match="must match game_time"):
        validate_world_time(clock, game_time=timeline)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"day": 1, "hour": 24},
        {"day": 1, "minute": 60},
        {"day": 1, "second": 1},
        {"day": 1, "second": 60},
    ],
)
def test_calendar_rejects_noncanonical_fields(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        anchor_world_time(game_time_from_ticks(), **kwargs)


def test_advance_requires_exactly_one_delta_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        advance_game_time(
            game_time_from_ticks(),
            period="minute",
            elapsed_ticks=1,
        )
    with pytest.raises(ValueError, match="exactly one"):
        advance_game_time(game_time_from_ticks())
