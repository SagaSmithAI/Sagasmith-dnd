"""Source-bound 2014 food and water deprivation rules."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .character_schema import condition_ids, set_exhaustion_level, validate_character_sheet
from .combat_engine import CombatEngineError


class SurvivalNeedsCheckError(CombatEngineError):
    """A daily water settlement needs the authoritative Constitution save."""


def validate_daily_intake(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "food_lb", "water_gallons", "hot_weather", "weather_fact",
        "consumed_items",
    }:
        raise CombatEngineError("survival intake has unsupported fields")
    food = value.get("food_lb", 0)
    water = value.get("water_gallons", 0)
    for name, amount, maximum in (
        ("food_lb", food, 100), ("water_gallons", water, 100),
    ):
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount))
            or amount < 0
            or amount > maximum
        ):
            raise CombatEngineError(f"survival intake {name} must be a finite non-negative amount")
    hot = value.get("hot_weather", False)
    if not isinstance(hot, bool):
        raise CombatEngineError("survival intake hot_weather must be boolean")
    fact = value.get("weather_fact")
    if hot:
        if not isinstance(fact, dict) or set(fact) != {
            "decision_id", "reason", "source_ref", "source_excerpt",
        }:
            raise CombatEngineError("hot weather requires a bounded source fact")
        for key, maximum in (("decision_id", 100), ("reason", 500),
                             ("source_ref", 300), ("source_excerpt", 1200)):
            text = fact.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > maximum:
                raise CombatEngineError(f"weather_fact.{key} is invalid")
        fact = {key: str(fact[key]).strip() for key in (
            "decision_id", "reason", "source_ref", "source_excerpt",
        )}
    elif fact is not None:
        raise CombatEngineError("weather_fact is only valid when hot_weather is true")
    consumed_items = value.get("consumed_items", [])
    if not isinstance(consumed_items, list) or len(consumed_items) > 20:
        raise CombatEngineError("consumed_items must be a bounded array")
    normalized_items = []
    for item in consumed_items:
        if not isinstance(item, dict) or set(item) != {"item_id", "source_key", "quantity"}:
            raise CombatEngineError("each consumed item requires item_id, source_key, and quantity")
        if (
            not isinstance(item["item_id"], str) or not item["item_id"].strip()
            or not isinstance(item["source_key"], str) or not item["source_key"].strip()
            or isinstance(item["quantity"], bool)
            or not isinstance(item["quantity"], int) or item["quantity"] < 1
        ):
            raise CombatEngineError("consumed item facts are invalid")
        normalized_items.append({
            "item_id": item["item_id"].strip(),
            "source_key": item["source_key"].strip(),
            "quantity": item["quantity"],
        })
    return {
        "food_lb": float(food),
        "water_gallons": float(water),
        "hot_weather": hot,
        **({"weather_fact": fact} if fact is not None else {}),
        "consumed_items": normalized_items,
    }


def validate_survival_state(value: Any = None, *, current_day: int = 0) -> dict[str, Any]:
    if value is None:
        return {
            "schema_version": 1,
            "last_settled_day": int(current_day) - 1,
            "actors": {},
            "daily_intakes": {},
        }
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "last_settled_day", "actors", "daily_intakes",
    }:
        raise CombatEngineError("campaign.state.survival fields are invalid")
    if value.get("schema_version") != 1:
        raise CombatEngineError("campaign.state.survival.schema_version must be 1")
    last_day = value.get("last_settled_day")
    if isinstance(last_day, bool) or not isinstance(last_day, int) or last_day < -1:
        raise CombatEngineError("campaign.state.survival.last_settled_day is invalid")
    actors = value.get("actors")
    days = value.get("daily_intakes")
    if not isinstance(actors, dict) or not isinstance(days, dict):
        raise CombatEngineError("campaign.state.survival actors and daily_intakes must be objects")
    normalized_actors: dict[str, dict[str, Any]] = {}
    for actor_id, raw in actors.items():
        if not isinstance(actor_id, str) or not actor_id.strip() or not isinstance(raw, dict):
            raise CombatEngineError("campaign.state.survival actor records are invalid")
        allowed = {
            "food_deprivation_days", "starvation_exhaustion_earned",
            "deprivation_exhaustion_levels", "recovery_locked",
        }
        if set(raw) - allowed:
            raise CombatEngineError("campaign.state.survival actor fields are invalid")
        food_days = raw.get("food_deprivation_days", 0)
        if (
            isinstance(food_days, bool) or not isinstance(food_days, (int, float))
            or not math.isfinite(float(food_days)) or food_days < 0
        ):
            raise CombatEngineError("food_deprivation_days must be non-negative")
        starvation_earned = raw.get("starvation_exhaustion_earned", 0)
        deprived_levels = raw.get("deprivation_exhaustion_levels", 0)
        for name, number in (("starvation_exhaustion_earned", starvation_earned),
                             ("deprivation_exhaustion_levels", deprived_levels)):
            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 6:
                raise CombatEngineError(f"{name} must be an integer from 0 to 6")
        recovery_locked = raw.get("recovery_locked", False)
        if not isinstance(recovery_locked, bool):
            raise CombatEngineError("recovery_locked must be boolean")
        normalized_actors[actor_id] = {
            "food_deprivation_days": float(food_days),
            "starvation_exhaustion_earned": starvation_earned,
            "deprivation_exhaustion_levels": deprived_levels,
            "recovery_locked": recovery_locked,
        }
    normalized_days: dict[str, dict[str, dict[str, Any]]] = {}
    for day, raw_day in days.items():
        try:
            day_number = int(day)
        except (TypeError, ValueError) as error:
            raise CombatEngineError("survival intake day keys must be integer strings") from error
        if str(day_number) != str(day) or day_number < 0 or not isinstance(raw_day, dict):
            raise CombatEngineError("survival intake day entries are invalid")
        normalized_days[str(day_number)] = {
            str(actor_id): validate_daily_intake(intake)
            for actor_id, intake in raw_day.items()
        }
    return {
        "schema_version": 1,
        "last_settled_day": last_day,
        "actors": normalized_actors,
        "daily_intakes": normalized_days,
    }


def needs_water_save(intake: Any) -> bool:
    facts = validate_daily_intake(intake)
    required = 2.0 if facts["hot_weather"] else 1.0
    water = facts["water_gallons"]
    return required / 2 <= water < required


def merge_daily_intake(existing: Any, additional: Any) -> dict[str, Any]:
    before = validate_daily_intake(existing or {})
    after = validate_daily_intake(additional or {})
    hot_weather = before["hot_weather"] or after["hot_weather"]
    weather_fact = after.get("weather_fact") or before.get("weather_fact")
    return {
        "food_lb": before["food_lb"] + after["food_lb"],
        "water_gallons": before["water_gallons"] + after["water_gallons"],
        "hot_weather": hot_weather,
        **({"weather_fact": weather_fact} if hot_weather and weather_fact else {}),
        "consumed_items": [*before["consumed_items"], *after["consumed_items"]],
    }


def settle_survival_day(
    sheet: dict[str, Any],
    actor_state: dict[str, Any] | None,
    intake: dict[str, Any] | None,
    *,
    day_index: int,
    water_save: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one day's food and water requirements to one 2014 actor."""
    value = validate_character_sheet(sheet)
    if value.get("edition") != "2014":
        raise CombatEngineError("food and water deprivation requires 2014 rules")
    if isinstance(day_index, bool) or not isinstance(day_index, int) or day_index < 0:
        raise CombatEngineError("survival day index is invalid")
    state = validate_survival_state({
        "schema_version": 1,
        "last_settled_day": -1,
        "actors": {"actor": actor_state or {}},
        "daily_intakes": {},
    })["actors"]["actor"]
    facts = validate_daily_intake(intake or {})
    conditions = condition_ids(value.get("conditions"))
    if "dead" in conditions:
        return {
            "sheet": value,
            "actor_state": state,
            "day_index": day_index,
            "skipped": "dead",
            "exhaustion_added": 0,
        }

    constitution = int(dict(value.get("abilities") or {}).get("constitution", {}).get("score", 10))
    grace_days = max(1, 3 + (constitution - 10) // 2)
    food = facts["food_lb"]
    if food >= 1:
        state["food_deprivation_days"] = 0.0
        state["starvation_exhaustion_earned"] = 0
    else:
        state["food_deprivation_days"] += 1.0 - food
    food_due = max(0, math.floor(state["food_deprivation_days"] - grace_days + 1e-9))
    food_added = max(0, food_due - state["starvation_exhaustion_earned"])
    state["starvation_exhaustion_earned"] = max(
        state["starvation_exhaustion_earned"], food_due,
    )

    required_water = 2.0 if facts["hot_weather"] else 1.0
    water = facts["water_gallons"]
    water_penalty = False
    save_result = None
    if water < required_water:
        if water < required_water / 2:
            water_penalty = True
            save_result = {"kind": "automatic_failure", "reason": "less_than_half_water"}
        else:
            if not isinstance(water_save, dict) or water_save.get("kind") != "save":
                raise SurvivalNeedsCheckError("half water requires a Constitution save")
            save_result = deepcopy(water_save)
            water_penalty = not bool(save_result.get("success"))

    exhaustion_before = int(value["combat"]["exhaustion"])
    before_water = min(6, exhaustion_before + food_added)
    water_added = (2 if before_water > 0 else 1) if water_penalty else 0
    requested_addition = food_added + water_added
    immune = bool(
        {"exhaustion", "exhausted"}
        & condition_ids(value.get("traits", {}).get("condition_immunities"))
    )
    exhaustion_after = min(6, exhaustion_before + requested_addition)
    exhaustion_added = 0 if immune else exhaustion_after - exhaustion_before
    if not immune and exhaustion_added:
        value = set_exhaustion_level(value, exhaustion_after)
        state["deprivation_exhaustion_levels"] = min(
            6, state["deprivation_exhaustion_levels"] + exhaustion_added,
        )
        state["recovery_locked"] = True

    fully_fed = food >= 1
    fully_watered = water >= required_water
    if fully_fed and fully_watered:
        state["recovery_locked"] = False
    return {
        "sheet": value,
        "actor_state": state,
        "day_index": day_index,
        "constitution_grace_days": grace_days,
        "food_deprivation_days": state["food_deprivation_days"],
        "food_lb": food,
        "water_gallons": water,
        "water_required_gallons": required_water,
        "water_save": save_result,
        "fully_fed": fully_fed,
        "fully_watered": fully_watered,
        "recovery_locked": state["recovery_locked"],
        "exhaustion_before": exhaustion_before,
        "exhaustion_added": exhaustion_added,
        "exhaustion_after": min(6, exhaustion_before + exhaustion_added),
        "immunity": immune,
    }
