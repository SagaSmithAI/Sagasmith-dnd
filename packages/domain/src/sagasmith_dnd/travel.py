"""Deterministic 2014 overland travel pace and forced-march rules."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .character_schema import condition_ids, set_exhaustion_level, validate_character_sheet
from .combat_engine import CombatEngineError

TRAVEL_PACES = {
    "fast": {"feet_per_minute": 400, "miles_per_hour": 4, "miles_per_day": 30},
    "normal": {"feet_per_minute": 300, "miles_per_hour": 3, "miles_per_day": 24},
    "slow": {"feet_per_minute": 200, "miles_per_hour": 2, "miles_per_day": 18},
}

def _bounded_fact(value: Any, field: str) -> dict[str, str]:
    required = {"decision_id", "reason", "source_ref", "source_excerpt"}
    if not isinstance(value, dict) or set(value) != required:
        raise CombatEngineError(f"{field} requires a bounded source fact")
    limits = {"decision_id": 160, "reason": 500, "source_ref": 500, "source_excerpt": 2000}
    normalized: dict[str, str] = {}
    for key, limit in limits.items():
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise CombatEngineError(f"{field}.{key} must be bounded non-empty text")
        normalized[key] = item.strip()
    return normalized


def validate_travel_leg(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "travel_id", "pace", "distance_miles", "difficult_terrain", "route_fact",
        "terrain_fact", "end_trip",
    }:
        raise CombatEngineError("travel leg has unsupported fields")
    travel_id = value.get("travel_id")
    if not isinstance(travel_id, str) or not travel_id.strip() or len(travel_id) > 160:
        raise CombatEngineError("travel_id must be bounded non-empty text")
    pace = str(value.get("pace") or "").strip().casefold()
    if pace not in TRAVEL_PACES:
        raise CombatEngineError("travel pace must be fast, normal, or slow")
    distance = value.get("distance_miles")
    if (
        isinstance(distance, bool) or not isinstance(distance, (int, float))
        or not math.isfinite(float(distance)) or distance <= 0 or distance > 1000
    ):
        raise CombatEngineError("distance_miles must be finite and greater than 0 and at most 1000")
    difficult = value.get("difficult_terrain", False)
    if not isinstance(difficult, bool):
        raise CombatEngineError("difficult_terrain must be boolean")
    terrain_fact = value.get("terrain_fact")
    if difficult:
        terrain_fact = _bounded_fact(terrain_fact, "terrain_fact")
    elif terrain_fact is not None:
        raise CombatEngineError("terrain_fact requires difficult_terrain")
    end_trip = value.get("end_trip", True)
    if not isinstance(end_trip, bool):
        raise CombatEngineError("end_trip must be boolean")
    return {
        "travel_id": travel_id.strip(),
        "pace": pace,
        "distance_miles": float(distance),
        "difficult_terrain": difficult,
        "route_fact": _bounded_fact(value.get("route_fact"), "route_fact"),
        **({"terrain_fact": terrain_fact} if terrain_fact is not None else {}),
        "end_trip": end_trip,
    }


def travel_duration_minutes(
    distance_miles: float,
    pace: str,
    *,
    difficult_terrain: bool = False,
) -> int:
    """Compute the source-defined distance time, including the 8-hour day cap."""
    if pace not in TRAVEL_PACES:
        raise CombatEngineError("travel pace must be fast, normal, or slow")
    if (
        isinstance(distance_miles, bool) or not isinstance(distance_miles, (int, float))
        or not math.isfinite(float(distance_miles)) or distance_miles <= 0
    ):
        raise CombatEngineError("travel distance must be finite and greater than 0")
    effective_miles = float(distance_miles) * (2 if difficult_terrain else 1)
    rules = TRAVEL_PACES[pace]
    rate = float(rules["miles_per_hour"])
    first_day_distance = float(rules["miles_per_day"])
    if effective_miles <= first_day_distance:
        hours = effective_miles / rate
    else:
        hours = 8 + (effective_miles - first_day_distance) / rate
    return max(1, math.ceil(hours * 60))


def forced_march_save_dcs(previous_elapsed_minutes: int, additional_minutes: int) -> list[int]:
    for name, value in (("previous_elapsed_minutes", previous_elapsed_minutes),
                        ("additional_minutes", additional_minutes)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CombatEngineError(f"{name} must be a non-negative integer")
    before_hour = previous_elapsed_minutes // 60
    after_hour = (previous_elapsed_minutes + additional_minutes) // 60
    return [10 + hour - 9 for hour in range(max(9, before_hour + 1), after_hour + 1)]


def validate_travel_state(value: Any = None, *, current_day: int = 0) -> dict[str, Any]:
    if value is None:
        return {
            "schema_version": 1,
            "day_index": current_day,
            "day_elapsed_minutes": 0,
            "active": None,
            "ledger": [],
        }
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "day_index", "day_elapsed_minutes", "active", "ledger",
    }:
        raise CombatEngineError("campaign.state.travel fields are invalid")
    if value.get("schema_version") != 1:
        raise CombatEngineError("campaign.state.travel.schema_version must be 1")
    day_index = value.get("day_index")
    day_elapsed = value.get("day_elapsed_minutes")
    if (
        isinstance(day_index, bool) or not isinstance(day_index, int) or day_index < 0
        or day_index > current_day
        or isinstance(day_elapsed, bool) or not isinstance(day_elapsed, int)
        or not 0 <= day_elapsed <= 1440
    ):
        raise CombatEngineError("campaign.state.travel day accounting is invalid")
    active = value.get("active")
    if active is not None:
        if not isinstance(active, dict) or set(active) != {
            "travel_id", "pace", "participant_ids", "elapsed_minutes", "distance_miles",
        }:
            raise CombatEngineError("campaign.state.travel.active fields are invalid")
        pace = str(active.get("pace") or "").casefold()
        ids = active.get("participant_ids")
        elapsed = active.get("elapsed_minutes")
        distance = active.get("distance_miles")
        if (
            not isinstance(active.get("travel_id"), str) or not active["travel_id"].strip()
            or pace not in TRAVEL_PACES or not isinstance(ids, list) or not ids
            or any(not isinstance(item, str) or not item.strip() for item in ids)
            or ids != sorted(set(ids))
            or isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0
            or isinstance(distance, bool) or not isinstance(distance, (int, float))
            or not math.isfinite(float(distance)) or distance < 0
        ):
            raise CombatEngineError("campaign.state.travel.active is invalid")
        active = {
            "travel_id": active["travel_id"], "pace": pace,
            "participant_ids": list(ids), "elapsed_minutes": elapsed,
            "distance_miles": float(distance),
        }
    ledger = value.get("ledger")
    if not isinstance(ledger, list) or len(ledger) > 100:
        raise CombatEngineError("campaign.state.travel.ledger must contain at most 100 legs")
    normalized_ledger = []
    for index, entry in enumerate(ledger):
        if not isinstance(entry, dict) or set(entry) != {
            "travel_id", "pace", "distance_miles", "difficult_terrain", "duration_minutes",
            "started_elapsed_ticks", "completed_elapsed_ticks", "participant_ids",
            "route_fact", "terrain_fact", "forced_march_saves",
        }:
            raise CombatEngineError(f"campaign.state.travel.ledger[{index}] fields are invalid")
        normalized = deepcopy(entry)
        if normalized["pace"] not in TRAVEL_PACES:
            raise CombatEngineError(f"campaign.state.travel.ledger[{index}].pace is invalid")
        if not isinstance(normalized["difficult_terrain"], bool):
            raise CombatEngineError("travel ledger difficult_terrain must be boolean")
        if (
            isinstance(normalized["duration_minutes"], bool)
            or not isinstance(normalized["duration_minutes"], int)
            or normalized["duration_minutes"] < 1
        ):
            raise CombatEngineError("travel ledger duration_minutes is invalid")
        for field in ("started_elapsed_ticks", "completed_elapsed_ticks"):
            if (
                isinstance(normalized[field], bool)
                or not isinstance(normalized[field], int)
                or normalized[field] < 0
            ):
                raise CombatEngineError(f"travel ledger {field} is invalid")
        normalized_ledger.append(normalized)
    return {
        "schema_version": 1,
        "day_index": day_index,
        "day_elapsed_minutes": day_elapsed,
        "active": active,
        "ledger": normalized_ledger,
    }


def travel_passive_perception_bonus(state: Any, actor_id: str) -> int:
    if not isinstance(state, dict) or bool(dict(state.get("combat") or {}).get("active")):
        return 0
    active = dict(dict(state.get("travel") or {}).get("active") or {})
    if (
        active.get("pace") != "fast"
        or active.get("pace_effects", True) is not True
        or str(actor_id) not in active.get("participant_ids", [])
    ):
        return 0
    return -5


def travel_stealth_allowed(state: Any, actor_id: str) -> bool:
    if not isinstance(state, dict) or bool(dict(state.get("combat") or {}).get("active")):
        return False
    active = dict(dict(state.get("travel") or {}).get("active") or {})
    return (
        active.get("pace") == "slow"
        and active.get("pace_effects", True) is True
        and str(actor_id) in active.get("participant_ids", [])
    )


def settle_forced_march_save(sheet: dict[str, Any], save: dict[str, Any]) -> dict[str, Any]:
    """Apply one engine-rolled forced-march save to a character sheet."""
    value = validate_character_sheet(sheet)
    if value.get("edition") != "2014":
        raise CombatEngineError("forced-march exhaustion is source-bound to 2014 rules")
    if (
        not isinstance(save, dict)
        or save.get("kind") != "save"
        or save.get("ability") != "constitution"
    ):
        raise CombatEngineError("forced-march settlement requires a Constitution save result")
    success = save.get("success")
    if not isinstance(success, bool):
        raise CombatEngineError("forced-march save success must be boolean")
    if "dead" in condition_ids(value.get("conditions")):
        return {"sheet": value, "exhaustion_added": 0, "skipped": "dead"}
    if success:
        return {"sheet": value, "exhaustion_added": 0, "skipped": None}
    before = int(value["combat"]["exhaustion"])
    after = min(6, before + 1)
    updated = set_exhaustion_level(value, after)
    return {
        "sheet": updated,
        "exhaustion_added": int(updated["combat"]["exhaustion"]) - before,
        "died": "dead" in condition_ids(updated.get("conditions")),
    }
