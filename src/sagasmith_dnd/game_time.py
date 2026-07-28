"""Single-authority, branch-local game time for D&D campaign state.

``game_time.elapsed_ticks`` is the only advancing chronology. One tick is six
seconds, the rules duration of a combat round. ``world_time`` is an optional
calendar projection anchored to that chronology; changing or restoring a
calendar label never creates a second elapsed-time counter.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

GAME_TIME_SCHEMA_VERSION = 1
WORLD_TIME_SCHEMA_VERSION = 2
TICK_SECONDS = 6
TICKS_PER_MINUTE = 10
TICKS_PER_HOUR = 600
TICKS_PER_DAY = 14_400

_PERIOD_TICKS = {
    "round": 1,
    "minute": TICKS_PER_MINUTE,
    "hour": TICKS_PER_HOUR,
    "day": TICKS_PER_DAY,
}


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer at least {minimum}")
    return value


def _signed_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _label(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("campaign.state.world_time.label must be text")
    normalized = value.strip()
    if len(normalized) > 300:
        raise ValueError("campaign.state.world_time.label must contain at most 300 characters")
    return normalized


def game_time_from_ticks(elapsed_ticks: int = 0) -> dict[str, int]:
    """Create the one authoritative monotonic game-time record."""

    return {
        "schema_version": GAME_TIME_SCHEMA_VERSION,
        "tick_seconds": TICK_SECONDS,
        "elapsed_ticks": _integer(
            elapsed_ticks,
            "campaign.state.game_time.elapsed_ticks",
        ),
    }


def rules_day_from_ticks(elapsed_ticks: int) -> int:
    """Return the deterministic 1-based rules day on the game timeline."""

    return (
        _integer(
            elapsed_ticks,
            "campaign.state.game_time.elapsed_ticks",
        )
        // TICKS_PER_DAY
        + 1
    )


def validate_game_time(value: Any) -> dict[str, int]:
    """Validate an authoritative game-time record."""

    if not isinstance(value, dict):
        raise ValueError("campaign.state.game_time must be an object")
    record = deepcopy(value)
    required = {"schema_version", "tick_seconds", "elapsed_ticks"}
    unknown = sorted(set(record) - required)
    missing = sorted(required - set(record))
    if unknown or missing:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unsupported " + ", ".join(unknown))
        raise ValueError("campaign.state.game_time fields are invalid: " + "; ".join(details))
    version = _integer(
        record.get("schema_version"),
        "campaign.state.game_time.schema_version",
        minimum=1,
    )
    if version != GAME_TIME_SCHEMA_VERSION:
        raise ValueError("campaign.state.game_time.schema_version must be 1")
    tick_seconds = _integer(
        record.get("tick_seconds"),
        "campaign.state.game_time.tick_seconds",
        minimum=1,
    )
    if tick_seconds != TICK_SECONDS:
        raise ValueError("campaign.state.game_time.tick_seconds must be 6")
    return game_time_from_ticks(
        _integer(
            record.get("elapsed_ticks"),
            "campaign.state.game_time.elapsed_ticks",
        )
    )


def _calendar_ticks(
    *,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> int:
    normalized_day = _integer(day, "campaign.state.world_time.day", minimum=1)
    normalized_hour = _integer(hour, "campaign.state.world_time.hour")
    normalized_minute = _integer(minute, "campaign.state.world_time.minute")
    normalized_second = _integer(second, "campaign.state.world_time.second")
    if normalized_hour > 23:
        raise ValueError("campaign.state.world_time.hour must be from 0 to 23")
    if normalized_minute > 59:
        raise ValueError("campaign.state.world_time.minute must be from 0 to 59")
    if normalized_second > 59 or normalized_second % TICK_SECONDS:
        raise ValueError(
            "campaign.state.world_time.second must be a multiple of 6 from 0 to 54"
        )
    return (
        (normalized_day - 1) * TICKS_PER_DAY
        + normalized_hour * TICKS_PER_HOUR
        + normalized_minute * TICKS_PER_MINUTE
        + normalized_second // TICK_SECONDS
    )


def project_world_time(
    game_time: Any,
    *,
    calendar_offset_ticks: int,
    label: str = "",
) -> dict[str, Any]:
    """Project an anchored calendar from the authoritative tick position."""

    timeline = validate_game_time(game_time)
    offset = _signed_integer(
        calendar_offset_ticks,
        "campaign.state.world_time.calendar_offset_ticks",
    )
    calendar_ticks = timeline["elapsed_ticks"] + offset
    if calendar_ticks < 0:
        raise ValueError("campaign.state.world_time cannot project before day 1")
    whole_minutes, round_remainder = divmod(calendar_ticks, TICKS_PER_MINUTE)
    return {
        "schema_version": WORLD_TIME_SCHEMA_VERSION,
        "tick_seconds": TICK_SECONDS,
        "calendar_offset_ticks": offset,
        "day": whole_minutes // 1440 + 1,
        "hour": (whole_minutes % 1440) // 60,
        "minute": whole_minutes % 60,
        "second": round_remainder * TICK_SECONDS,
        "elapsed_minutes": whole_minutes,
        "round_remainder": round_remainder,
        "label": _label(label),
    }


def anchor_world_time(
    game_time: Any,
    *,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    label: str = "",
) -> dict[str, Any]:
    """Anchor an exact calendar position without resetting elapsed game time."""

    timeline = validate_game_time(game_time)
    calendar_ticks = _calendar_ticks(
        day=day,
        hour=hour,
        minute=minute,
        second=second,
    )
    return project_world_time(
        timeline,
        calendar_offset_ticks=calendar_ticks - timeline["elapsed_ticks"],
        label=label,
    )


def validate_world_time(value: Any, *, game_time: Any) -> dict[str, Any]:
    """Validate and migrate a v1/v2 calendar projection at ``game_time``."""

    if not isinstance(value, dict):
        raise ValueError("campaign.state.world_time must be an object")
    if not value:
        return {}
    clock = deepcopy(value)
    timeline = validate_game_time(game_time)
    schema_version = clock.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError("campaign.state.world_time.schema_version must be an integer")
    if schema_version == 1:
        allowed = {
            "schema_version",
            "day",
            "hour",
            "minute",
            "elapsed_minutes",
            "label",
        }
        unknown = sorted(set(clock) - allowed)
        if unknown:
            raise ValueError(
                "campaign.state.world_time contains unsupported fields: "
                + ", ".join(unknown)
            )
        calendar_ticks = _calendar_ticks(
            day=clock.get("day"),
            hour=clock.get("hour"),
            minute=clock.get("minute"),
        )
        elapsed_minutes = _integer(
            clock.get("elapsed_minutes"),
            "campaign.state.world_time.elapsed_minutes",
        )
        if elapsed_minutes != calendar_ticks // TICKS_PER_MINUTE:
            raise ValueError(
                "campaign.state.world_time.elapsed_minutes must match day/hour/minute"
            )
        return project_world_time(
            timeline,
            calendar_offset_ticks=calendar_ticks - timeline["elapsed_ticks"],
            label=_label(clock.get("label")),
        )
    if schema_version != WORLD_TIME_SCHEMA_VERSION:
        raise ValueError("campaign.state.world_time.schema_version must be 1 or 2")

    required = {
        "schema_version",
        "tick_seconds",
        "calendar_offset_ticks",
        "day",
        "hour",
        "minute",
        "second",
        "elapsed_minutes",
        "round_remainder",
        "label",
    }
    unknown = sorted(set(clock) - required)
    missing = sorted(required - set(clock))
    if unknown or missing:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unsupported " + ", ".join(unknown))
        raise ValueError(
            "campaign.state.world_time v2 fields are invalid: " + "; ".join(details)
        )
    tick_seconds = _integer(
        clock.get("tick_seconds"),
        "campaign.state.world_time.tick_seconds",
        minimum=1,
    )
    if tick_seconds != TICK_SECONDS:
        raise ValueError("campaign.state.world_time.tick_seconds must be 6")
    projected = project_world_time(
        timeline,
        calendar_offset_ticks=_signed_integer(
            clock.get("calendar_offset_ticks"),
            "campaign.state.world_time.calendar_offset_ticks",
        ),
        label=_label(clock.get("label")),
    )
    if clock != projected:
        raise ValueError(
            "campaign.state.world_time calendar fields must match game_time"
        )
    return projected


def game_time_ticks(period: str, count: int = 1) -> int:
    """Convert one supported elapsed-time unit into canonical ticks."""

    normalized_period = str(period).strip().lower().replace("-", "_")
    if normalized_period not in _PERIOD_TICKS:
        raise ValueError("game time period must be round, minute, hour, or day")
    normalized_count = _integer(count, "game time count", minimum=1)
    return _PERIOD_TICKS[normalized_period] * normalized_count


def advance_game_time(
    game_time: Any,
    *,
    world_time: Any | None = None,
    period: str | None = None,
    count: int = 1,
    elapsed_ticks: int | None = None,
) -> dict[str, Any]:
    """Advance the authority and its optional calendar projection together."""

    before = validate_game_time(game_time)
    before_world = (
        validate_world_time(world_time, game_time=before)
        if world_time
        else None
    )
    if (period is None) == (elapsed_ticks is None):
        raise ValueError("provide exactly one of period or elapsed_ticks")
    delta_ticks = (
        game_time_ticks(str(period), count)
        if period is not None
        else _integer(elapsed_ticks, "elapsed_ticks", minimum=1)
    )
    after = game_time_from_ticks(before["elapsed_ticks"] + delta_ticks)
    after_world = (
        project_world_time(
            after,
            calendar_offset_ticks=before_world["calendar_offset_ticks"],
            label=before_world["label"],
        )
        if before_world is not None
        else None
    )
    return {
        "before": before,
        "after": after,
        "world_time_before": before_world,
        "world_time_after": after_world,
        "elapsed_ticks": delta_ticks,
        "elapsed_rounds": delta_ticks,
        "elapsed_minutes": after["elapsed_ticks"] // TICKS_PER_MINUTE
        - before["elapsed_ticks"] // TICKS_PER_MINUTE,
    }
