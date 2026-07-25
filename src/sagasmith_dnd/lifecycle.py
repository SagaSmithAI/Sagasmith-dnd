"""Deterministic v2-card recovery and duration advancement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.engine import roll
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts

REST_MINIMUM_MINUTES = {"short_rest": 60, "long_rest": 480}
REST_SCHEDULE_FIELDS = {
    "sleep_minutes",
    "light_activity_minutes",
    "strenuous_activity_minutes",
}
REST_SCHEDULE_OPTIONAL_FIELDS = {"trance_minutes"}


def allows_trance_rest(sheet: dict[str, Any]) -> bool:
    """Return whether a source-bound actor feature grants the elf Trance rest."""
    return any(
        str(feature.get("name") or "").strip().casefold() == "trance"
        and str(feature.get("source_key") or "").strip()
        for feature in sheet.get("content", {}).get("features", [])
        if isinstance(feature, dict)
    )


def validate_rest_schedule(
    *,
    rest_type: str,
    duration_minutes: int,
    rest_schedule: dict[str, int] | None,
    allows_trance: bool = False,
) -> dict[str, int]:
    """Require a complete rest schedule matching the 2014 rest definition."""
    normalized_type = str(rest_type).strip().lower().replace("-", "_")
    if normalized_type not in REST_MINIMUM_MINUTES:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    minimum_minutes = (
        240
        if normalized_type == "long_rest" and allows_trance
        else REST_MINIMUM_MINUTES[normalized_type]
    )
    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or duration_minutes < minimum_minutes
    ):
        raise CombatEngineError(f"{normalized_type} requires at least {minimum_minutes} minutes")
    if not isinstance(rest_schedule, dict):
        raise CombatEngineError("rest requires an explicit rest_schedule")
    unknown = set(rest_schedule) - REST_SCHEDULE_FIELDS - REST_SCHEDULE_OPTIONAL_FIELDS
    missing = REST_SCHEDULE_FIELDS - set(rest_schedule)
    if unknown or missing:
        raise CombatEngineError(
            "rest_schedule requires exactly sleep_minutes, "
            "light_activity_minutes, strenuous_activity_minutes, and optional "
            "trance_minutes"
        )
    normalized: dict[str, int] = {}
    for field in sorted(REST_SCHEDULE_FIELDS | REST_SCHEDULE_OPTIONAL_FIELDS):
        value = rest_schedule.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CombatEngineError(f"rest_schedule.{field} must be a nonnegative integer")
        normalized[field] = value
    if sum(normalized.values()) != duration_minutes:
        raise CombatEngineError("rest_schedule minutes must equal the campaign-clock rest duration")
    if normalized_type == "short_rest":
        if normalized["strenuous_activity_minutes"] != 0:
            raise CombatEngineError(
                "a short rest permits no activity more strenuous than light activity"
            )
    else:
        trance_satisfies_sleep = allows_trance and normalized["trance_minutes"] >= 240
        if not trance_satisfies_sleep and normalized["sleep_minutes"] < 360:
            raise CombatEngineError(
                "a long rest requires at least 6 hours of sleep or a source-granted 4-hour trance"
            )
        if not trance_satisfies_sleep and duration_minutes < 480:
            raise CombatEngineError("a long rest requires at least 8 hours")
        if normalized["light_activity_minutes"] > 120:
            raise CombatEngineError("a long rest permits no more than 2 hours of light activity")
        if normalized["strenuous_activity_minutes"] >= 60:
            raise CombatEngineError("at least 1 hour of strenuous activity interrupts a long rest")
    return normalized


def record_rest_completion(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    started_elapsed_minutes: int,
    completed_elapsed_minutes: int,
    rest_schedule: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Validate campaign-clock rest timing and preserve the last benefit time."""
    normalized = str(rest_type).strip().lower().replace("-", "_")
    if normalized not in REST_MINIMUM_MINUTES:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    started = int(started_elapsed_minutes)
    completed = int(completed_elapsed_minutes)
    if started < 0 or completed < started:
        raise CombatEngineError("rest clock bounds are invalid")
    allows_trance = allows_trance_rest(sheet)
    minimum_minutes = (
        240 if normalized == "long_rest" and allows_trance else REST_MINIMUM_MINUTES[normalized]
    )
    if completed - started < minimum_minutes:
        raise CombatEngineError(f"{normalized} requires at least {minimum_minutes} minutes")
    validate_rest_schedule(
        rest_type=normalized,
        duration_minutes=completed - started,
        rest_schedule=rest_schedule,
        allows_trance=allows_trance,
    )
    hp = int(dict(sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = {str(item).casefold() for item in sheet.get("conditions", [])}
    if hp <= 0 or "dead" in conditions:
        raise CombatEngineError("a creature must have at least 1 hit point at the start of a rest")
    history = dict(dict(sheet.get("combat") or {}).get("rest_history") or {})
    previous_completed = history.get("last_rest_completed_elapsed_minutes")
    if previous_completed is not None and completed <= int(previous_completed):
        raise CombatEngineError(
            "a creature cannot benefit from more than one rest ending at the same campaign time"
        )
    previous_long = history.get("last_long_rest_elapsed_minutes")
    if (
        normalized == "long_rest"
        and previous_long is not None
        and completed - int(previous_long) < 1440
    ):
        raise CombatEngineError(
            "a creature cannot benefit from more than one long rest in 24 hours"
        )
    value = deepcopy(sheet)
    next_history = value.setdefault("combat", {}).setdefault("rest_history", {})
    next_history.update(
        {
            "last_rest_type": normalized,
            "last_rest_started_elapsed_minutes": started,
            "last_rest_completed_elapsed_minutes": completed,
        }
    )
    if normalized == "long_rest":
        next_history["last_long_rest_elapsed_minutes"] = completed
    else:
        next_history.setdefault("last_long_rest_elapsed_minutes", previous_long)
    return value


def advance_effect_durations(
    sheet: dict[str, Any], *, period: str, amount: int = 1
) -> dict[str, Any]:
    """Advance effects whose declared period matches and deactivate expired ones."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        raise CombatEngineError("effect duration advance amount must be a positive integer")
    normalized = str(period).strip().lower().replace("-", "_")
    aliases = {"round_end": "round", "round_start": "round", "turn": "turn_end"}
    normalized = aliases.get(normalized, normalized)
    value = deepcopy(sheet)
    advanced: list[str] = []
    expired: list[str] = []
    for effect in value.get("effects", []):
        if not effect.get("active"):
            continue
        duration = dict(effect.get("duration") or {})
        if duration.get("period") != normalized:
            continue
        remaining = int(duration.get("remaining", 0) or 0)
        if remaining <= amount:
            effect["active"] = False
            effect["ended_reason"] = "duration_expired"
            expired.append(str(effect.get("id")))
        else:
            duration["remaining"] = remaining - amount
            effect["duration"] = duration
            advanced.append(str(effect.get("id")))
    if not any(
        effect.get("active") and effect.get("kind") == "turn_undead"
        for effect in value.get("effects", [])
    ):
        value["conditions"] = [
            condition
            for condition in value.get("conditions", [])
            if str(condition).casefold() != "turned"
        ]
    return {
        "sheet": value,
        "period": normalized,
        "amount": amount,
        "advanced": advanced,
        "expired": expired,
    }


def advance_world_effect_durations(
    state: dict[str, Any], *, period: str, amount: int = 1
) -> dict[str, Any]:
    """Advance structured campaign-space effects with the actor-effect semantics."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        raise CombatEngineError("world effect duration advance amount must be positive")
    normalized = str(period).strip().lower().replace("-", "_")
    value = deepcopy(state)
    advanced: list[str] = []
    expired: list[str] = []
    for effect in value.get("world_effects", []):
        if not effect.get("active"):
            continue
        duration = dict(effect.get("duration") or {})
        if duration.get("period") != normalized:
            continue
        remaining = int(duration.get("remaining", 0) or 0)
        if remaining <= amount:
            effect["active"] = False
            effect["ended_reason"] = "duration_expired"
            expired.append(str(effect.get("id")))
        else:
            duration["remaining"] = remaining - amount
            effect["duration"] = duration
            advanced.append(str(effect.get("id")))
    return {
        "state": value,
        "period": normalized,
        "amount": amount,
        "advanced": advanced,
        "expired": expired,
    }


def recover_stable_creature(sheet: dict[str, Any], *, recovery_hours: int) -> dict[str, Any]:
    """Resolve the automatic 1 HP recovery of an unhealed Stable creature."""
    if isinstance(recovery_hours, bool) or not isinstance(recovery_hours, int):
        raise CombatEngineError("stable recovery hours must be an integer from 1 to 4")
    if not 1 <= recovery_hours <= 4:
        raise CombatEngineError("stable recovery hours must be an integer from 1 to 4")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    conditions = {str(item).casefold() for item in value.get("conditions", [])}
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot recover from being stable")
    if int(hp.get("value", 0) or 0) != 0 or "stable" not in conditions:
        raise CombatEngineError("stable recovery requires a Stable creature at 0 hit points")
    hp["value"] = 1
    combat["hp"] = hp
    combat["death_saves"] = {"successes": 0, "failures": 0}
    value["conditions"] = [
        item
        for item in value.get("conditions", [])
        if str(item).casefold() not in {"stable", "unconscious"}
    ]
    return {
        "sheet": value,
        "status": "recovered",
        "recovery_hours": recovery_hours,
        "before_hp": 0,
        "after_hp": 1,
    }


def initialize_source_state(sheet: dict[str, Any], *, state: str) -> dict[str, Any]:
    """Apply one narrow, source-authored initial creature state.

    This is intentionally not a generic condition editor. Adventures sometimes
    introduce a creature already at 0 HP, unconscious, and stable; representing
    that authored state must not require inventing healing and damage events.
    """
    normalized = str(state).strip().lower().replace("-", "_")
    if normalized != "stable_unconscious":
        raise CombatEngineError("source state must be stable_unconscious")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    conditions = {str(item).casefold() for item in value.get("conditions", [])}
    if int(hp.get("value", 0) or 0) != 0:
        raise CombatEngineError("stable_unconscious source state requires 0 hit points")
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot be initialized as stable unconscious")
    conditions.update({"prone", "stable", "unconscious"})
    combat["death_saves"] = {"successes": 0, "failures": 0}
    value["conditions"] = sorted(conditions)
    return {
        "sheet": value,
        "status": "initialized",
        "source_state": normalized,
    }


def stand_outside_combat(sheet: dict[str, Any]) -> dict[str, Any]:
    """Stand a conscious living creature without exposing arbitrary condition edits."""
    value = deepcopy(sheet)
    hp = int(dict(value.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = [str(item).casefold() for item in value.get("conditions", [])]
    if hp <= 0 or "dead" in conditions or "unconscious" in conditions:
        raise CombatEngineError("standing requires a conscious living creature above 0 hit points")
    if "prone" not in conditions:
        raise CombatEngineError("standing requires the Prone condition")
    value["conditions"] = [
        item for item in value.get("conditions", []) if str(item).casefold() != "prone"
    ]
    return {"sheet": value, "status": "stood", "removed_condition": "prone"}


def knock_prone_outside_combat(sheet: dict[str, Any]) -> dict[str, Any]:
    """Apply Prone to a conscious living creature without arbitrary condition edits."""
    value = deepcopy(sheet)
    hp = int(dict(value.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = [str(item).casefold() for item in value.get("conditions", [])]
    if hp <= 0 or "dead" in conditions or "unconscious" in conditions:
        raise CombatEngineError(
            "knocking prone outside combat requires a conscious living creature above 0 hit points"
        )
    if "prone" in conditions:
        return {"sheet": value, "status": "already_prone", "added_condition": None}
    value.setdefault("conditions", []).append("prone")
    return {"sheet": value, "status": "knocked_prone", "added_condition": "prone"}


def validate_rest_activity_minutes(
    rest_activity_minutes: dict[str, int] | None,
) -> dict[str, int]:
    """Normalize explicit activities that gate resource recovery during a rest."""
    normalized: dict[str, int] = {}
    for raw_activity, raw_minutes in (rest_activity_minutes or {}).items():
        activity = str(raw_activity).strip().casefold()
        if (
            not activity
            or isinstance(raw_minutes, bool)
            or not isinstance(raw_minutes, int)
            or raw_minutes < 0
        ):
            raise CombatEngineError(
                "rest_activity_minutes requires named activities and nonnegative integer minutes"
            )
        normalized[activity] = raw_minutes
    return normalized


def apply_rest(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    hit_dice_spends: list[dict[str, Any]] | None = None,
    hit_dice_recovery: dict[str, int] | None = None,
    arcane_recovery: dict[str, int] | None = None,
    natural_recovery: dict[str, int] | None = None,
    rest_activity_minutes: dict[str, int] | None = None,
    food_and_drink: bool = False,
    song_of_rest_source_sheet: dict[str, Any] | None = None,
    rules: ResolutionContext | None = None,
    rng: Any = None,
    world_day: int | None = None,
) -> dict[str, Any]:
    """Settle a short or long rest without inventing player-choice allocations."""
    rest_type = str(rest_type).strip().lower().replace("-", "_")
    if rest_type not in {"short_rest", "long_rest"}:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    if rest_type == "long_rest" and hit_dice_spends:
        raise CombatEngineError("hit dice can be spent only during a short rest")
    if rest_type == "short_rest" and hit_dice_recovery:
        raise CombatEngineError("hit dice recover only during a long rest")
    if rest_type == "short_rest" and food_and_drink:
        raise CombatEngineError("food_and_drink affects exhaustion recovery only on a long rest")
    if rest_type != "short_rest" and arcane_recovery:
        raise CombatEngineError("Arcane Recovery can be used only when finishing a short rest")
    if rest_type != "short_rest" and natural_recovery:
        raise CombatEngineError("Natural Recovery can be used only during a short rest")
    if rest_type != "short_rest" and song_of_rest_source_sheet is not None:
        raise CombatEngineError("Song of Rest applies only when finishing a short rest")
    normalized_rest_activities = validate_rest_activity_minutes(rest_activity_minutes)
    song_of_rest_die_sides = (
        validate_song_of_rest_source(song_of_rest_source_sheet)
        if song_of_rest_source_sheet is not None
        else None
    )
    if rest_type == "short_rest":
        validate_rest_hit_dice_requests(sheet, hit_dice_spends)
        validate_arcane_recovery_choice(sheet, arcane_recovery, world_day=world_day)
        validate_natural_recovery_choice(
            sheet,
            natural_recovery,
            rest_activity_minutes=normalized_rest_activities,
        )
    before_rules = apply_rule_event(sheet, "rest.before", rules)
    if before_rules.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "rest_type": rest_type,
            "status": before_rules.status,
            "hit_dice_rolls": [],
            "rule_receipts": list(before_rules.receipts),
            "pending": list(before_rules.pending),
        }
    value = before_rules.sheet
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    if int(hp.get("value", 0) or 0) <= 0 or "dead" in {
        str(item).casefold() for item in value.get("conditions", [])
    }:
        raise CombatEngineError("a creature at 0 hit points or dead cannot benefit from a rest")
    edition = "2024" if "2024" in str(value.get("edition") or "") else "2014"
    recovered: dict[str, int] = {}
    unmet_recovery_requirements: dict[str, dict[str, Any]] = {}
    hit_die_healing = 0
    hit_die_applied_healing = 0
    hit_dice_rolls: list[dict[str, Any]] = []
    arcane_recovery_result: dict[str, Any] | None = None
    natural_recovery_result: dict[str, Any] | None = None
    song_of_rest_result: dict[str, Any] | None = None
    sorcerous_restoration_result: dict[str, Any] | None = None
    if rest_type == "long_rest":
        hp["value"] = int(hp.get("max", 0) or 0)
        hp["temp"] = 0
        combat["death_saves"] = {"successes": 0, "failures": 0}
        value["conditions"] = [
            item for item in value.get("conditions", []) if item not in {"unconscious", "stable"}
        ]
        exhaustion = int(combat.get("exhaustion", 0) or 0)
        if edition == "2024" or food_and_drink:
            combat["exhaustion"] = max(0, exhaustion - 1)
    else:
        hit_dice = combat.get("hit_dice", {})
        hit_die_resolution = roll_rest_hit_dice(value, hit_dice_spends, rng=rng)
        hit_dice_rolls = hit_die_resolution["rolls"]
        for spend in hit_die_resolution["spends"]:
            key = str(spend["key"])
            resource = hit_dice.get(key)
            roll_value = int(spend["roll"])
            resource["value"] = int(resource["value"]) - 1
            healing = roll_value + _constitution_modifier(value)
            hit_die_healing += max(1 if edition == "2024" else 0, healing)
        if hit_die_healing:
            hp_before_hit_dice = int(hp.get("value", 0) or 0)
            hp["value"] = min(
                int(hp.get("max", 0) or 0), hp_before_hit_dice + hit_die_healing
            )
            hit_die_applied_healing = int(hp["value"]) - hp_before_hit_dice
        if hit_die_applied_healing > 0 and song_of_rest_die_sides is not None:
            song_roll = asdict(roll(f"1d{song_of_rest_die_sides}", rng=rng))
            hp_before_song = int(hp.get("value", 0) or 0)
            hp["value"] = min(
                int(hp.get("max", 0) or 0),
                hp_before_song + int(song_roll["total"]),
            )
            song_of_rest_result = {
                "die": f"1d{song_of_rest_die_sides}",
                "roll": song_roll,
                "rolled_healing": int(song_roll["total"]),
                "applied_healing": int(hp["value"]) - hp_before_song,
            }
        if arcane_recovery:
            arcane_recovery_result = apply_arcane_recovery_choice(
                value,
                arcane_recovery,
                world_day=world_day,
            )
            for level, amount in arcane_recovery_result["recovered"].items():
                recovered[f"spell_slot:{level}"] = amount
        if natural_recovery:
            natural_recovery_result = apply_natural_recovery_choice(
                value,
                natural_recovery,
                rest_activity_minutes=normalized_rest_activities,
            )
            for level, amount in natural_recovery_result["recovered"].items():
                recovered[f"spell_slot:{level}"] = (
                    recovered.get(f"spell_slot:{level}", 0) + amount
                )
        sorcerous_restoration_result = apply_sorcerous_restoration(value)
        if sorcerous_restoration_result is not None:
            recovered["sorcery_points"] = sorcerous_restoration_result["recovered"]

    def recover_resource(resource: object, key: str) -> None:
        if not isinstance(resource, dict):
            return
        recovery = resource.get("recovers_on")
        if recovery != rest_type and not (rest_type == "long_rest" and recovery == "short_rest"):
            return
        requirements = dict(resource.get("recovery_requirements") or {})
        required_activities = dict(requirements.get("activity_minutes") or {})
        unmet = {
            str(activity): {
                "required_minutes": int(required_minutes),
                "actual_minutes": int(normalized_rest_activities.get(str(activity).casefold(), 0)),
            }
            for activity, required_minutes in required_activities.items()
            if normalized_rest_activities.get(str(activity).casefold(), 0) < int(required_minutes)
        }
        if unmet:
            unmet_recovery_requirements[key] = {"activity_minutes": unmet}
            return
        if bool(resource.get("unlimited", False)):
            return
        before = int(resource.get("value", 0) or 0)
        resource["value"] = int(resource.get("max", 0) or 0)
        recovered[key] = recovered.get(key, 0) + resource["value"] - before

    for key, resource in value.get("resources", {}).items():
        recover_resource(resource, key)
    for key, resource in value.get("spellcasting", {}).get("spell_slots", {}).items():
        if rest_type == "long_rest":
            before = int(resource.get("value", 0) or 0)
            resource["value"] = int(resource.get("max", 0) or 0)
            recovered[f"spell_slot:{key}"] = resource["value"] - before
    if rest_type == "long_rest":
        points = value.get("spellcasting", {}).get("spell_points")
        if isinstance(points, dict):
            before = int(points.get("value", 0) or 0)
            points["value"] = int(points.get("max", 0) or 0)
            recovered["spell_points"] = points["value"] - before
    pact_magic = value.get("spellcasting", {}).get("pact_magic")
    recover_resource(pact_magic, "pact_magic")
    if (
        rest_type == "long_rest"
        and isinstance(pact_magic, dict)
        and pact_magic.get("recovers_on") == "none"
    ):
        before = int(pact_magic.get("value", 0) or 0)
        pact_magic["value"] = int(pact_magic.get("max", 0) or 0)
        recovered["pact_magic"] = pact_magic["value"] - before
    if rest_type == "long_rest":
        hit_dice = value.get("combat", {}).get("hit_dice", {})
        if edition == "2024":
            allocation = {
                key: int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
                for key, resource in hit_dice.items()
                if isinstance(resource, dict)
            }
        else:
            missing = {
                key: int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
                for key, resource in hit_dice.items()
                if isinstance(resource, dict)
            }
            allowance = max(
                1,
                sum(
                    int(resource.get("max", 0) or 0)
                    for resource in hit_dice.values()
                    if isinstance(resource, dict)
                )
                // 2,
            )
            if (
                hit_dice_recovery is None
                and sum(1 for amount in missing.values() if amount > 0) > 1
                and sum(missing.values()) > allowance
            ):
                raise CombatEngineError("2014 long-rest hit-die recovery needs a player allocation")
            requested = hit_dice_recovery or {}
            allocation = {
                key: int(requested.get(key, min(amount, allowance)))
                for key, amount in missing.items()
            }
            if (
                any(amount < 0 or amount > missing[key] for key, amount in allocation.items())
                or sum(allocation.values()) > allowance
            ):
                raise CombatEngineError("2014 hit-die recovery allocation is invalid")
        for key, amount in allocation.items():
            resource = hit_dice[key]
            before = int(resource.get("value", 0) or 0)
            resource["value"] = min(int(resource.get("max", 0) or 0), before + amount)
            recovered[f"hit_dice:{key}"] = resource["value"] - before
    for section in ("activities", "features", "feats"):
        for index, item in enumerate(value.get("content", {}).get(section, [])):
            recover_resource(item.get("uses"), f"{section}:{index}:uses")
    for index, item in enumerate(value.get("inventory", {}).get("items", [])):
        recover_resource(item.get("uses"), f"inventory:{index}:uses")
        recover_resource(item.get("charges"), f"inventory:{index}:charges")
    value["combat"] = combat | {"hp": hp}
    duration = advance_effect_durations(value, period=rest_type)
    after_rules = apply_rule_event(duration["sheet"], "rest.after", rules)
    if after_rules.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "rest_type": rest_type,
            "status": after_rules.status,
            "hit_dice_rolls": hit_dice_rolls,
            "arcane_recovery": arcane_recovery_result,
            "natural_recovery": natural_recovery_result,
            "song_of_rest": song_of_rest_result,
            "sorcerous_restoration": sorcerous_restoration_result,
            "rule_receipts": [*before_rules.receipts, *after_rules.receipts],
            "pending": list(after_rules.pending),
        }
    return {
        "sheet": after_rules.sheet,
        "rest_type": rest_type,
        "recovered": recovered,
        "unmet_recovery_requirements": unmet_recovery_requirements,
        "hit_die_healing": hit_die_healing,
        "hit_die_applied_healing": hit_die_applied_healing,
        "hit_dice_rolls": hit_dice_rolls,
        "arcane_recovery": arcane_recovery_result,
        "natural_recovery": natural_recovery_result,
        "song_of_rest": song_of_rest_result,
        "sorcerous_restoration": sorcerous_restoration_result,
        "effects_expired": duration["expired"],
        "status": "committed",
        "rule_receipts": [
            *core_receipts(
                rules,
                [
                    "dnd5e.core.rest.hit_dice",
                    "dnd5e.core.rest.exhaustion",
                    *(
                        ["dnd5e.core.rest.arcane_recovery"]
                        if arcane_recovery_result is not None
                        else []
                    ),
                    *(
                        ["dnd5e.core.rest.natural_recovery"]
                        if natural_recovery_result is not None
                        else []
                    ),
                    *(
                        ["dnd5e.core.rest.song_of_rest"]
                        if song_of_rest_result is not None
                        else []
                    ),
                    *(
                        ["dnd5e.core.rest.sorcerous_restoration"]
                        if sorcerous_restoration_result is not None
                        else []
                    ),
                ],
                "rest.apply",
            ),
            *before_rules.receipts,
            *after_rules.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def validate_song_of_rest_source(sheet: dict[str, Any]) -> int:
    """Return the 2014 Song of Rest die size for a living source-bound bard."""
    edition = "2024" if "2024" in str(sheet.get("edition") or "") else "2014"
    if edition != "2014":
        raise CombatEngineError("Song of Rest requires the 2014 Bard feature")
    hp = int(dict(sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = {str(item).casefold() for item in sheet.get("conditions", [])}
    if hp <= 0 or "dead" in conditions or "unconscious" in conditions:
        raise CombatEngineError("Song of Rest requires a conscious living bard")
    feature = next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if isinstance(item, dict)
            and (
                str(item.get("id") or "").endswith("bard-song-of-rest")
                or str(item.get("name") or "").strip().casefold() == "song of rest"
            )
            and str(item.get("source_key") or "").strip().casefold() == "bard"
            and any(
                str(ref).startswith("bundled:srd2014/02_Classes/Bard")
                for ref in item.get("rule_refs", [])
            )
        ),
        None,
    )
    bard_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict)
        and str(item.get("name") or "").strip().casefold() == "bard"
    )
    if feature is None or bard_level < 2:
        raise CombatEngineError("Song of Rest requires a source-bound Bard level of at least 2")
    if bard_level >= 17:
        return 12
    if bard_level >= 13:
        return 10
    if bard_level >= 9:
        return 8
    return 6


def validate_rest_hit_dice_requests(
    sheet: dict[str, Any],
    spends: list[dict[str, Any]] | None,
) -> list[tuple[str, int]]:
    """Validate and aggregate player hit-die choices without consuming RNG."""
    hit_dice = dict(sheet.get("combat", {}).get("hit_dice") or {})
    requested: dict[str, int] = {}
    order: list[str] = []
    for spend in spends or []:
        if not isinstance(spend, dict) or set(spend) - {"key", "count"}:
            raise CombatEngineError("each hit-die request accepts only key and count")
        key = str(spend.get("key") or "").strip()
        count = spend.get("count", 1)
        if not key or isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise CombatEngineError("hit-die request count must be a positive integer")
        if key not in requested:
            order.append(key)
        requested[key] = requested.get(key, 0) + count
    for key, count in requested.items():
        resource = hit_dice.get(key)
        if not isinstance(resource, dict):
            raise CombatEngineError(f"hit die is not recorded: {key}")
        if count > int(resource.get("value", 0) or 0):
            raise CombatEngineError(f"not enough hit dice remain for {key}")
    return [(key, requested[key]) for key in order]


def validate_arcane_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int] | None,
    *,
    world_day: int | None = None,
) -> dict[str, Any] | None:
    """Validate the Wizard's once-per-day short-rest slot allocation."""
    if not choice:
        return None
    if not isinstance(choice, dict):
        raise CombatEngineError("arcane_recovery must map spell-slot levels to counts")
    feature = _arcane_recovery_feature(sheet)
    if feature is None:
        raise CombatEngineError("the actor does not have Arcane Recovery")
    if isinstance(world_day, bool) or not isinstance(world_day, int) or world_day < 1:
        raise CombatEngineError("Arcane Recovery requires the current campaign day")
    choices = dict(feature.get("choices") or {})
    last_used_day = choices.get("_arcane_recovery_last_used_day")
    if last_used_day is not None and int(last_used_day) == world_day:
        raise CombatEngineError("Arcane Recovery has already been used on this campaign day")
    uses = dict(feature.get("uses") or {})
    if (
        last_used_day is None
        and int(uses.get("max", 0) or 0) == 1
        and int(uses.get("value", 0) or 0) == 0
    ):
        raise CombatEngineError(
            "Arcane Recovery has a legacy used marker without a campaign day; reconcile it first"
        )
    wizard_level = next(
        (
            int(item.get("level", 0) or 0)
            for item in sheet.get("progression", {}).get("classes", [])
            if str(item.get("name") or "").casefold() == "wizard"
        ),
        0,
    )
    if wizard_level < 1:
        raise CombatEngineError("Arcane Recovery requires a Wizard class level")
    allowance = (wizard_level + 1) // 2
    slots = dict(sheet.get("spellcasting", {}).get("spell_slots") or {})
    normalized: dict[str, int] = {}
    for raw_level, raw_count in choice.items():
        level_text = str(raw_level).strip()
        if not level_text.isdigit():
            raise CombatEngineError("Arcane Recovery spell-slot levels must be integers")
        level = int(level_text)
        count = raw_count
        if level < 1 or level >= 6:
            raise CombatEngineError("Arcane Recovery cannot restore a level 6 or higher slot")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise CombatEngineError("Arcane Recovery slot counts must be positive integers")
        resource = slots.get(str(level))
        if not isinstance(resource, dict):
            raise CombatEngineError(f"the actor has no level {level} spell slots")
        missing = int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
        if count > missing:
            raise CombatEngineError(f"Arcane Recovery exceeds missing level {level} slots")
        normalized[str(level)] = normalized.get(str(level), 0) + count
    if not normalized:
        raise CombatEngineError("Arcane Recovery requires at least one spell-slot choice")
    for level, count in normalized.items():
        resource = slots[level]
        missing = int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
        if count > missing:
            raise CombatEngineError(f"Arcane Recovery exceeds missing level {level} slots")
    used_levels = sum(int(level) * count for level, count in normalized.items())
    if used_levels > allowance:
        raise CombatEngineError("Arcane Recovery exceeds half the Wizard level rounded up")
    return {
        "allowance": allowance,
        "used_levels": used_levels,
        "recovered": normalized,
        "campaign_day": world_day,
    }


def apply_arcane_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int],
    *,
    world_day: int,
) -> dict[str, Any]:
    """Apply one previously validated Arcane Recovery allocation in place."""
    result = validate_arcane_recovery_choice(sheet, choice, world_day=world_day)
    assert result is not None
    slots = sheet["spellcasting"]["spell_slots"]
    for level, count in result["recovered"].items():
        slots[level]["value"] = int(slots[level].get("value", 0) or 0) + count
    feature = _arcane_recovery_feature(sheet)
    assert feature is not None
    feature["uses"] = {
        "label": "Arcane Recovery",
        "value": 0,
        "max": 1,
        "recovers_on": "manual",
        "source_key": "Wizard",
        "slot_level": 0,
    }
    feature_choices = dict(feature.get("choices") or {})
    feature_choices["_arcane_recovery_last_used_day"] = world_day
    feature["choices"] = feature_choices
    return result


def validate_natural_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int] | None,
    *,
    rest_activity_minutes: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Validate the Land Druid's once-per-long-rest slot allocation."""
    if not choice:
        return None
    if not isinstance(choice, dict):
        raise CombatEngineError("natural_recovery must map spell-slot levels to counts")
    if "2024" in str(sheet.get("edition") or ""):
        raise CombatEngineError("Natural Recovery requires the 2014 Druid feature")
    feature = _natural_recovery_feature(sheet)
    if feature is None:
        raise CombatEngineError("the actor does not have source-bound Natural Recovery")
    if int((rest_activity_minutes or {}).get("meditation", 0) or 0) < 1:
        raise CombatEngineError("Natural Recovery requires declared meditation during the rest")
    uses = dict(feature.get("uses") or {})
    if (
        int(uses.get("max", 0) or 0) == 1
        and int(uses.get("value", 0) or 0) == 0
    ):
        raise CombatEngineError("Natural Recovery has already been used since the last long rest")
    druid_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict)
        and str(item.get("name") or "").strip().casefold() == "druid"
    )
    if druid_level < 2:
        raise CombatEngineError("Natural Recovery requires at least 2 Druid levels")
    result = _validate_recovered_spell_slots(
        sheet,
        choice,
        allowance=(druid_level + 1) // 2,
        feature_name="Natural Recovery",
    )
    return {
        **result,
        "druid_level": druid_level,
    }


def apply_natural_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int],
    *,
    rest_activity_minutes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Apply one validated Natural Recovery allocation in place."""
    result = validate_natural_recovery_choice(
        sheet,
        choice,
        rest_activity_minutes=rest_activity_minutes,
    )
    assert result is not None
    slots = sheet["spellcasting"]["spell_slots"]
    for level, count in result["recovered"].items():
        slots[level]["value"] = int(slots[level].get("value", 0) or 0) + count
    feature = _natural_recovery_feature(sheet)
    assert feature is not None
    feature["uses"] = {
        "label": "Natural Recovery",
        "value": 0,
        "max": 1,
        "recovers_on": "long_rest",
        "source_key": "Circle of the Land",
        "slot_level": 0,
    }
    return result


def apply_sorcerous_restoration(sheet: dict[str, Any]) -> dict[str, Any] | None:
    """Apply the 2014 level-20 Sorcerer's automatic short-rest recovery."""
    if _sorcerous_restoration_feature(sheet) is None:
        return None
    if "2024" in str(sheet.get("edition") or ""):
        raise CombatEngineError("Sorcerous Restoration requires the 2014 Sorcerer feature")
    sorcerer_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict)
        and str(item.get("name") or "").strip().casefold() == "sorcerer"
    )
    if sorcerer_level < 20:
        raise CombatEngineError("Sorcerous Restoration requires 20 Sorcerer levels")
    resource = sheet.get("resources", {}).get("sorcery_points")
    if not isinstance(resource, dict):
        raise CombatEngineError("Sorcerous Restoration requires the Sorcery Points resource")
    before = int(resource.get("value", 0) or 0)
    maximum = int(resource.get("max", 0) or 0)
    resource["value"] = min(maximum, before + 4)
    return {
        "sorcerer_level": sorcerer_level,
        "before": before,
        "recovered": int(resource["value"]) - before,
        "after": int(resource["value"]),
        "maximum": maximum,
    }


def _validate_recovered_spell_slots(
    sheet: dict[str, Any],
    choice: dict[str, int],
    *,
    allowance: int,
    feature_name: str,
) -> dict[str, Any]:
    slots = dict(sheet.get("spellcasting", {}).get("spell_slots") or {})
    normalized: dict[str, int] = {}
    for raw_level, raw_count in choice.items():
        level_text = str(raw_level).strip()
        if not level_text.isdigit():
            raise CombatEngineError(f"{feature_name} spell-slot levels must be integers")
        level = int(level_text)
        if level < 1 or level >= 6:
            raise CombatEngineError(
                f"{feature_name} cannot restore a level 6 or higher slot"
            )
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
            raise CombatEngineError(
                f"{feature_name} slot counts must be positive integers"
            )
        resource = slots.get(str(level))
        if not isinstance(resource, dict):
            raise CombatEngineError(f"the actor has no level {level} spell slots")
        missing = int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
        if raw_count > missing:
            raise CombatEngineError(
                f"{feature_name} exceeds missing level {level} slots"
            )
        normalized[str(level)] = normalized.get(str(level), 0) + raw_count
    if not normalized:
        raise CombatEngineError(f"{feature_name} requires at least one spell-slot choice")
    used_levels = sum(int(level) * count for level, count in normalized.items())
    if used_levels > allowance:
        raise CombatEngineError(f"{feature_name} exceeds half the class level rounded up")
    return {
        "allowance": allowance,
        "used_levels": used_levels,
        "recovered": normalized,
    }


def _arcane_recovery_feature(sheet: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if str(item.get("id") or "").endswith("wizard-arcane-recovery")
            or str(item.get("name") or "").casefold() == "arcane recovery"
        ),
        None,
    )


def _natural_recovery_feature(sheet: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if isinstance(item, dict)
            and (
                str(item.get("id") or "").endswith(
                    "circle-of-the-land-natural-recovery"
                )
                or str(item.get("name") or "").strip().casefold()
                == "natural recovery"
            )
            and any(
                str(ref).startswith("bundled:srd2014/02_Classes/Druid")
                for ref in item.get("rule_refs", [])
            )
        ),
        None,
    )


def _sorcerous_restoration_feature(sheet: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if isinstance(item, dict)
            and (
                str(item.get("id") or "").endswith(
                    "sorcerer-sorcerous-restoration"
                )
                or str(item.get("name") or "").strip().casefold()
                == "sorcerous restoration"
            )
            and str(item.get("source_key") or "").strip().casefold()
            == "sorcerer"
            and any(
                str(ref).startswith("bundled:srd2014/02_Classes/Sorcerer")
                for ref in item.get("rule_refs", [])
            )
        ),
        None,
    )


def roll_rest_hit_dice(
    sheet: dict[str, Any],
    spends: list[dict[str, Any]] | None,
    *,
    rng: Any = None,
) -> dict[str, Any]:
    """Produce engine-owned rolls after validating requested hit-die counts."""
    hit_dice = dict(sheet.get("combat", {}).get("hit_dice") or {})
    requested = validate_rest_hit_dice_requests(sheet, spends)
    resolved: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for key, count in requested:
        sides = _hit_die_sides(key, hit_dice[key])
        for _ in range(count):
            rolled = asdict(roll(f"1d{sides}", rng=rng))
            resolved.append({"key": key, "roll": int(rolled["total"])})
            audits.append({"key": key, **rolled})
    return {"spends": resolved, "rolls": audits}


def _constitution_modifier(sheet: dict[str, Any]) -> int:
    score = int(sheet.get("abilities", {}).get("constitution", {}).get("score", 10) or 10)
    return (score - 10) // 2


def _hit_die_sides(key: str, resource: dict[str, Any]) -> int:
    for candidate in (key, str(resource.get("label") or "")):
        lowered = candidate.casefold()
        if "d" in lowered:
            tail = lowered.rsplit("d", 1)[1]
            digits = "".join(char for char in tail if char.isdigit())
            if digits and int(digits) > 0:
                return int(digits)
    raise CombatEngineError(f"hit die {key} must identify its die size, for example d8")
