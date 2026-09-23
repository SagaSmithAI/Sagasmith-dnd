"""Deterministic 2014 lifestyle and downtime activity rules.

The Runtime must supply authoritative elapsed days, resolved checks, current
actor proficiencies, wallet/material balances, and exact module source refs.
This module validates those facts and computes rule outcomes without writing
campaign or character state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ACTIVITY_HOURS_PER_DAY = 8
CRAFTING_GP_PER_CHARACTER_DAY = 5
RESEARCH_GP_PER_DAY = 1
TRAINING_DAYS = 250
TRAINING_GP_PER_DAY = 1
RECUPERATION_DAYS = 3
RECUPERATION_SAVE_DC = 15

# Prices are copper pieces per day. Aristocratic is a minimum, not a fixed cap.
LIFESTYLE_DAILY_COST_CP = {
    "wretched": 0,
    "squalid": 10,
    "poor": 20,
    "modest": 100,
    "comfortable": 200,
    "wealthy": 400,
}

_ACTIVITIES = {"crafting", "profession", "recuperating", "research", "training"}
_LIFESTYLES = {*LIFESTYLE_DAILY_COST_CP, "aristocratic"}


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def validate_source_record(source_ref: Any, source_excerpt: Any) -> dict[str, str]:
    """Validate provenance shape; Runtime resolves identity and excerpt membership."""
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("source_ref is required")
    if not isinstance(source_excerpt, str) or len(source_excerpt.strip()) < 10:
        raise ValueError("source_excerpt must contain at least 10 characters")
    return {"source_ref": source_ref.strip(), "source_excerpt": source_excerpt.strip()}


def qualifies_downtime_day(hours: int) -> bool:
    """A day counts only after at least eight hours on its downtime activity."""
    _integer(hours, "hours")
    if hours > 24:
        raise ValueError("hours cannot exceed 24 in one day")
    return hours >= ACTIVITY_HOURS_PER_DAY


def record_downtime_day(
    state: dict[str, Any],
    *,
    activity: str,
    day_key: str,
    hours: int,
    source_ref: str,
    source_excerpt: str,
) -> dict[str, Any]:
    """Append one dated, source-recorded activity day; short days do not count."""
    if not isinstance(state, dict):
        raise ValueError("downtime state must be an object")
    activity_key = str(activity).strip().casefold()
    if activity_key not in _ACTIVITIES:
        raise ValueError("unsupported downtime activity")
    if not isinstance(day_key, str) or not day_key.strip():
        raise ValueError("day_key is required")
    _integer(hours, "hours")
    if hours > 24:
        raise ValueError("hours cannot exceed 24 in one day")
    source = validate_source_record(source_ref, source_excerpt)
    result = deepcopy(state)
    days = list(result.get("days") or [])
    if any(
        isinstance(item, dict)
        and item.get("activity") == activity_key
        and item.get("day_key") == day_key
        for item in days
    ):
        raise ValueError("activity already has a record for this day")
    days.append({
        "activity": activity_key,
        "day_key": day_key,
        "hours": hours,
        "qualifies": qualifies_downtime_day(hours),
        "source": source,
    })
    result["days"] = days
    return result


def completed_activity_days(state: dict[str, Any], activity: str) -> int:
    """Count qualifying days for one activity; dates need not be consecutive."""
    if not isinstance(state, dict) or not isinstance(state.get("days", []), list):
        raise ValueError("downtime state must contain a day list")
    activity_key = str(activity).strip().casefold()
    if activity_key not in _ACTIVITIES:
        raise ValueError("unsupported downtime activity")
    return sum(
        item.get("activity") == activity_key and item.get("qualifies") is True
        for item in state.get("days", [])
        if isinstance(item, dict)
    )


def lifestyle_daily_cost_cp(lifestyle: str, *, aristocratic_cost_cp: int | None = None) -> int:
    """Return 2014 daily lifestyle expense without social-effect assumptions."""
    key = str(lifestyle).strip().casefold()
    if key not in _LIFESTYLES:
        raise ValueError("unsupported lifestyle")
    if key != "aristocratic":
        if aristocratic_cost_cp is not None:
            raise ValueError("aristocratic_cost_cp is only valid for aristocratic lifestyle")
        return LIFESTYLE_DAILY_COST_CP[key]
    if aristocratic_cost_cp is None:
        return 1000
    return _integer(aristocratic_cost_cp, "aristocratic_cost_cp", minimum=1000)


def lifestyle_period_cost_cp(
    lifestyle: str, days: int, *, aristocratic_cost_cp: int | None = None,
) -> int:
    """Charge a selected lifestyle's per-day price over the requested period."""
    count = _integer(days, "days")
    return count * lifestyle_daily_cost_cp(
        lifestyle, aristocratic_cost_cp=aristocratic_cost_cp,
    )


def select_lifestyle(
    lifestyle: str,
    *,
    days: int,
    source_ref: str,
    source_excerpt: str,
    aristocratic_cost_cp: int | None = None,
) -> dict[str, Any]:
    """Return the selected active lifestyle, exact charge, and source record."""
    return {
        "lifestyle": str(lifestyle).strip().casefold(),
        "days": _integer(days, "days"),
        "cost_cp": lifestyle_period_cost_cp(
            lifestyle, days, aristocratic_cost_cp=aristocratic_cost_cp,
        ),
        "source": validate_source_record(source_ref, source_excerpt),
    }


def crafting_day(
    *,
    market_value_remaining_gp: int,
    qualifying_crafters: int,
    required_tools: str,
    proficient_tools_by_crafter: list[str],
    materials_available_cp: int,
    collaborating_in_same_place: bool,
    facility_required: bool,
    facility_available: bool,
) -> dict[str, int]:
    """Compute one day of nonmagical crafting and required raw-material cost."""
    remaining = _integer(market_value_remaining_gp, "market_value_remaining_gp")
    crafters = _integer(qualifying_crafters, "qualifying_crafters", minimum=1)
    if not isinstance(required_tools, str) or not required_tools.strip():
        raise ValueError("required_tools is required")
    if not isinstance(proficient_tools_by_crafter, list):
        raise ValueError("proficient_tools_by_crafter must be a list")
    normalized = [str(item).strip().casefold() for item in proficient_tools_by_crafter]
    tool = required_tools.strip().casefold()
    if len(normalized) != crafters or any(item != tool for item in normalized):
        raise ValueError("every crafting collaborator must have the required tool proficiency")
    materials = _integer(materials_available_cp, "materials_available_cp")
    if type(collaborating_in_same_place) is not bool:
        raise ValueError("collaborating_in_same_place must be a boolean")
    if crafters > 1 and not collaborating_in_same_place:
        raise ValueError("crafting collaborators must work together in the same place")
    if type(facility_required) is not bool or type(facility_available) is not bool:
        raise ValueError("facility facts must be booleans")
    if facility_required and not facility_available:
        raise ValueError("required crafting facility is unavailable")
    progress = min(remaining, crafters * CRAFTING_GP_PER_CHARACTER_DAY)
    materials_cost_cp = progress * 50
    if materials < materials_cost_cp:
        raise ValueError("insufficient raw materials")
    return {"progress_gp": progress, "materials_cost_cp": materials_cost_cp}


def crafting_lifestyle_cost_cp(lifestyle: str) -> int:
    """Apply the crafting lifestyle rule: modest free, comfortable half price."""
    key = str(lifestyle).strip().casefold()
    if key == "modest":
        return 0
    if key == "comfortable":
        return lifestyle_daily_cost_cp(key) // 2
    return lifestyle_daily_cost_cp(key)


def profession_support_tier(
    *, organization_employment: bool, performance_proficient: bool, performance_used: bool,
) -> str:
    """Return the lifestyle supported by practicing a profession."""
    if any(type(value) is not bool for value in (
        organization_employment, performance_proficient, performance_used,
    )):
        raise ValueError("profession facts must be booleans")
    if performance_proficient and performance_used:
        return "wealthy"
    if organization_employment:
        return "comfortable"
    return "modest"


def recuperation_outcome(
    *,
    qualifying_days: int,
    save_success: bool,
    choice: str | None = None,
    effect_id: str | None = None,
    blocking_effect_ids: list[str] | None = None,
    condition_id: str | None = None,
    condition_kind: str | None = None,
    current_condition_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a Runtime-resolved DC 15 save and exactly one eligible choice."""
    days = _integer(qualifying_days, "qualifying_days")
    if days < RECUPERATION_DAYS:
        raise ValueError("recuperation requires three qualifying days")
    if type(save_success) is not bool:
        raise ValueError("save_success must be an engine-resolved boolean")
    if not save_success:
        if any(value is not None for value in (choice, effect_id, condition_id, condition_kind)):
            raise ValueError("a failed save grants no recuperation choice")
        return {"save_dc": RECUPERATION_SAVE_DC, "success": False, "choice": None}
    if choice == "end_hp_recovery_effect":
        if effect_id is None or effect_id not in (blocking_effect_ids or []):
            raise ValueError("choice must identify a current effect preventing HP recovery")
        if condition_id is not None or condition_kind is not None:
            raise ValueError("recuperation applies exactly one choice")
        return {
            "save_dc": RECUPERATION_SAVE_DC,
            "success": True,
            "choice": {"kind": choice, "effect_id": effect_id},
        }
    if choice == "advantage_against_condition":
        if condition_kind not in {"disease", "poison"}:
            raise ValueError("choice must name one current disease or poison")
        if condition_id is None or condition_id not in (current_condition_ids or []):
            raise ValueError("condition_id must identify a current disease or poison")
        if effect_id is not None:
            raise ValueError("recuperation applies exactly one choice")
        return {
            "save_dc": RECUPERATION_SAVE_DC,
            "success": True,
            "choice": {
                "kind": choice,
                "condition_id": condition_id,
                "condition_kind": condition_kind,
                "duration_hours": 24,
            },
        }
    raise ValueError("a successful recuperation save requires exactly one supported choice")


def research_result(
    *,
    available: bool,
    required_days: int,
    qualifying_days: int,
    restrictions_satisfied: bool,
    required_check_ids: list[str],
    passed_check_ids: list[str],
    lifestyle: str,
    plan_source_ref: str,
    plan_source_excerpt: str,
    aristocratic_cost_cp: int | None = None,
    information_source_ref: str | None = None,
    information_source_excerpt: str | None = None,
) -> dict[str, Any]:
    """Reveal only GM-authorized research whose days, restrictions, and checks pass."""
    if type(available) is not bool or type(restrictions_satisfied) is not bool:
        raise ValueError("research availability and restriction facts must be booleans")
    required = _integer(required_days, "required_days", minimum=1)
    elapsed = _integer(qualifying_days, "qualifying_days")
    if not isinstance(required_check_ids, list) or not isinstance(passed_check_ids, list):
        raise ValueError("research check ids must be lists")
    if any(
        not isinstance(item, str) or not item.strip()
        for item in [*required_check_ids, *passed_check_ids]
    ):
        raise ValueError("research check ids must be non-empty strings")
    missing_checks = sorted(set(required_check_ids) - set(passed_check_ids))
    plan_source = validate_source_record(plan_source_ref, plan_source_excerpt)
    complete = available and elapsed >= required and restrictions_satisfied and not missing_checks
    revealed = None
    if complete:
        if information_source_ref is None or information_source_excerpt is None:
            raise ValueError("complete research requires source-bound information")
        revealed = validate_source_record(information_source_ref, information_source_excerpt)
    elif information_source_ref is not None or information_source_excerpt is not None:
        raise ValueError("research information cannot be supplied before all requirements pass")
    return {
        "available": available,
        "required_days": required,
        "qualifying_days": elapsed,
        "missing_check_ids": missing_checks,
        "plan_source": plan_source,
        "complete": complete,
        "daily_cost_gp": RESEARCH_GP_PER_DAY,
        "research_cost_cp": elapsed * RESEARCH_GP_PER_DAY * 100,
        "lifestyle_cost_cp": lifestyle_period_cost_cp(
            lifestyle, elapsed, aristocratic_cost_cp=aristocratic_cost_cp,
        ),
        "information_source": revealed,
    }


def training_progress(
    *,
    qualifying_days: int,
    days_to_add: int,
    instructor_id: str,
    instructor_persisted: bool,
    instructor_willing: bool,
    target_kind: str,
    target_id: str,
    required_check_ids: list[str] | None = None,
    passed_check_ids: list[str] | None = None,
    lifestyle: str,
    aristocratic_cost_cp: int | None = None,
) -> dict[str, Any]:
    """Compute 2014 training progress; Runtime applies cost and final proficiency."""
    elapsed = _integer(qualifying_days, "qualifying_days")
    addition = _integer(days_to_add, "days_to_add")
    if elapsed > TRAINING_DAYS or addition > TRAINING_DAYS:
        raise ValueError("training days cannot exceed the 250-day requirement")
    if not isinstance(instructor_id, str) or not instructor_id.strip():
        raise ValueError("a persisted instructor_id is required")
    if type(instructor_persisted) is not bool or not instructor_persisted:
        raise ValueError("training requires a persisted instructor")
    if type(instructor_willing) is not bool or not instructor_willing:
        raise ValueError("training requires a willing instructor")
    if target_kind not in {"language", "tool"}:
        raise ValueError("training target must be one language or one tool")
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("training target_id is required")
    required_checks = required_check_ids or []
    passed_checks = passed_check_ids or []
    if not isinstance(required_checks, list) or not isinstance(passed_checks, list):
        raise ValueError("training check ids must be lists")
    missing_checks = sorted(set(required_checks) - set(passed_checks))
    new_total = min(TRAINING_DAYS, elapsed + addition)
    credited = new_total - elapsed
    return {
        "instructor_id": instructor_id,
        "target": {"kind": target_kind, "id": target_id},
        "qualifying_days": new_total,
        "days_credited": credited,
        "cost_gp": credited * TRAINING_GP_PER_DAY,
        "training_cost_cp": credited * TRAINING_GP_PER_DAY * 100,
        "lifestyle_cost_cp": lifestyle_period_cost_cp(
            lifestyle, credited, aristocratic_cost_cp=aristocratic_cost_cp,
        ),
        "complete": new_total == TRAINING_DAYS and not missing_checks,
        "missing_check_ids": missing_checks,
    }
