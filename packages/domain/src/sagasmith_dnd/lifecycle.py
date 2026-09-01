"""Deterministic v2-card recovery and duration advancement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sagasmith_dnd.activities import ACTIVITY_CONTENT_SECTIONS
from sagasmith_dnd.character_schema import (
    effective_ability_modifier,
    effective_hit_point_maximum,
    set_exhaustion_level,
)
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.conditions import (
    apply_condition_change,
    condition_ids,
    reconcile_condition_projection,
    reconcile_ended_effect_conditions,
)
from sagasmith_dnd.editions import normalize_dnd_edition
from sagasmith_dnd.engine import roll
from sagasmith_dnd.game_time import (
    TICKS_PER_DAY,
    TICKS_PER_HOUR,
    TICKS_PER_MINUTE,
)
from sagasmith_dnd.heroic_inspiration import grant_heroic_inspiration
from sagasmith_dnd.hit_points import apply_basic_healing_to_sheet
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts
from sagasmith_dnd.vocabulary import REST_TYPES

SHORT_REST_MINIMUM_MINUTES = 60
LONG_REST_MINIMUM_MINUTES = 480
TRANCE_LONG_REST_MINUTES = 240
REST_MINIMUM_MINUTES = {
    "short_rest": SHORT_REST_MINIMUM_MINUTES,
    "long_rest": LONG_REST_MINIMUM_MINUTES,
}
COMBAT_BOUND_EFFECT_PERIODS = frozenset(
    {"source_turn_start", "turn_start", "turn_end", "round", "encounter"}
)
RAISE_DEAD_SPELL_ID = "dnd5e.content.srd2014.spell.raise-dead"
REVIVAL_ORDEAL_EFFECT_KIND = "revival_ordeal"
REVIVAL_ORDEAL_ROLL_PATHS = (
    "rolls.attack.bonus",
    "rolls.ability_check.bonus",
    "rolls.saving_throw.bonus",
)


def _sheet_edition(sheet: dict[str, Any]) -> str:
    try:
        return normalize_dnd_edition(sheet.get("edition"))
    except ValueError as exc:
        raise CombatEngineError(str(exc)) from exc


def minimum_rest_minutes(rest_type: str, *, allows_trance: bool = False) -> int:
    """Return the canonical minimum duration for one rest contract."""

    normalized = str(rest_type).strip().lower().replace("-", "_")
    if normalized not in REST_TYPES:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    if normalized == "long_rest" and allows_trance:
        return TRANCE_LONG_REST_MINUTES
    return REST_MINIMUM_MINUTES[normalized]


def validate_rest_eligibility(sheet: dict[str, Any], *, rest_type: str) -> str:
    """Validate the creature-state prerequisites for starting one rest."""

    normalized = str(rest_type).strip().lower().replace("-", "_")
    minimum_rest_minutes(normalized)
    hp = int(dict(sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = condition_ids(sheet.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot benefit from a rest")
    if hp <= 0:
        requires_positive_hp = normalized == "long_rest" or _sheet_edition(sheet) != "2014"
        if requires_positive_hp:
            rest_label = normalized.replace("_", " ")
            raise CombatEngineError(
                f"a creature must have at least 1 hit point at the start of a {rest_label}"
            )
        if "stable" not in conditions:
            raise CombatEngineError(
                "a creature at 0 hit points must be stable to benefit from a short rest"
            )
    return normalized


def apply_raise_dead_to_sheet(
    sheet: dict[str, Any],
    *,
    elapsed_days: int,
    soul_willing: bool,
    body_intact: bool,
    source_ref: str,
    source_actor_id: str | None = None,
) -> dict[str, Any]:
    """Apply the complete 2014 Raise Dead state transition to a dead actor."""

    value = deepcopy(sheet)
    if _sheet_edition(value) != "2014":
        raise CombatEngineError("this Raise Dead executor is bound to the 2014 rules")
    if isinstance(elapsed_days, bool) or not isinstance(elapsed_days, int):
        raise CombatEngineError("Raise Dead elapsed_days must be an integer")
    if elapsed_days < 0 or elapsed_days > 10:
        raise CombatEngineError("Raise Dead requires death no longer than 10 days ago")
    if soul_willing is not True:
        raise CombatEngineError("Raise Dead requires a willing soul at liberty to return")
    if body_intact is not True:
        raise CombatEngineError("Raise Dead requires all body parts integral for survival")
    exact_source_ref = str(source_ref).strip()
    if not exact_source_ref:
        raise CombatEngineError("Raise Dead requires an exact source_ref")
    creature_type = (
        str(dict(value.get("progression") or {}).get("species") or "").strip().casefold()
    )
    if "undead" in creature_type.split():
        raise CombatEngineError("Raise Dead cannot return an undead creature to life")
    conditions = condition_ids(value.get("conditions"))
    hit_points = dict(dict(value.get("combat") or {}).get("hp") or {})
    if "dead" not in conditions or int(hit_points.get("value", 0) or 0) != 0:
        raise CombatEngineError("Raise Dead requires a dead creature at 0 hit points")

    neutralized_effect_ids: list[str] = []
    for effect in value.get("effects", []):
        if not effect.get("active"):
            continue
        effect_kind = str(effect.get("kind") or "").strip().casefold().replace("-", "_")
        if effect_kind not in {"poison", "poisoned", "nonmagical_disease"}:
            continue
        effect["active"] = False
        effect["ended_reason"] = "neutralized_by_raise_dead"
        neutralized_effect_ids.append(str(effect.get("id") or ""))
    for condition_id in ("dead", "unconscious", "stable", "poisoned"):
        apply_condition_change(value, condition_id=condition_id, add=False)
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    hp["value"] = 1
    hp["temp"] = 0
    combat["hp"] = hp
    combat["death_saves"] = {"successes": 0, "failures": 0}

    for effect in value.get("effects", []):
        if effect.get("active") and effect.get("kind") == REVIVAL_ORDEAL_EFFECT_KIND:
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_raise_dead"
    effect_id = f"raise-dead-ordeal-{uuid4().hex}"
    value.setdefault("effects", []).append(
        {
            "id": effect_id,
            "name": "Raise Dead ordeal",
            "kind": REVIVAL_ORDEAL_EFFECT_KIND,
            "source": str(source_actor_id or exact_source_ref),
            "source_spell_id": RAISE_DEAD_SPELL_ID,
            "active": True,
            "concentration": False,
            "duration": {"period": "long_rest", "remaining": 4},
            "changes": [
                {"path": path, "mode": "add", "value": -4} for path in REVIVAL_ORDEAL_ROLL_PATHS
            ],
            "description": (
                "2014 Raise Dead: -4 to attack rolls, saving throws, and ability "
                "checks; the penalty decreases by 1 after each long rest."
            ),
        }
    )
    reconcile_condition_projection(value, value.get("conditions"))
    return {
        "sheet": value,
        "status": "revived",
        "spell_id": RAISE_DEAD_SPELL_ID,
        "hit_points": 1,
        "effect_id": effect_id,
        "penalty": -4,
        "remaining_long_rests": 4,
        "source_ref": exact_source_ref,
        "source_actor_id": source_actor_id,
        "neutralized_effect_ids": neutralized_effect_ids,
    }


def reduce_revival_ordeal_after_long_rest(sheet: dict[str, Any]) -> dict[str, Any]:
    """Reduce each active Raise Dead ordeal by exactly one before duration expiry."""

    value = deepcopy(sheet)
    reduced: list[dict[str, Any]] = []
    for effect in value.get("effects", []):
        if not effect.get("active") or effect.get("kind") != REVIVAL_ORDEAL_EFFECT_KIND:
            continue
        changes = list(effect.get("changes") or [])
        applicable = [
            change
            for change in changes
            if str(change.get("path") or "") in REVIVAL_ORDEAL_ROLL_PATHS
        ]
        if {str(change.get("path") or "") for change in applicable} != set(
            REVIVAL_ORDEAL_ROLL_PATHS
        ) or any(
            change.get("mode") != "add"
            or isinstance(change.get("value"), bool)
            or not isinstance(change.get("value"), int)
            for change in applicable
        ):
            raise CombatEngineError("active Raise Dead ordeal effect is malformed")
        before = {int(change["value"]) for change in applicable}
        if len(before) != 1 or next(iter(before)) >= 0:
            raise CombatEngineError("active Raise Dead ordeal penalty is malformed")
        before_penalty = next(iter(before))
        after_penalty = min(0, before_penalty + 1)
        for change in applicable:
            change["value"] = after_penalty
        reduced.append(
            {
                "effect_id": str(effect.get("id") or ""),
                "before": before_penalty,
                "after": after_penalty,
            }
        )
    return {"sheet": value, "reduced": reduced}


def _reconcile_ended_effects(sheet: dict[str, Any], ended_effect_ids: list[str]) -> None:
    """Project all effect endings through the shared condition-ownership primitive."""

    ids = set(ended_effect_ids)
    reconcile_ended_effect_conditions(
        sheet,
        ended_effects=[
            effect for effect in sheet.get("effects", []) if str(effect.get("id") or "") in ids
        ],
    )


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
    allows_trance: bool = False,
) -> dict[str, int]:
    """Derive the mechanical rest allocation from duration and actor features."""
    normalized_type = str(rest_type).strip().lower().replace("-", "_")
    minimum_minutes = minimum_rest_minutes(
        normalized_type,
        allows_trance=allows_trance,
    )
    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or duration_minutes < minimum_minutes
    ):
        raise CombatEngineError(f"{normalized_type} requires at least {minimum_minutes} minutes")
    if normalized_type == "short_rest":
        return {
            "sleep_minutes": 0,
            "light_activity_minutes": duration_minutes,
            "strenuous_activity_minutes": 0,
            "trance_minutes": 0,
        }
    if allows_trance:
        return {
            "sleep_minutes": max(0, duration_minutes - TRANCE_LONG_REST_MINUTES),
            "light_activity_minutes": 0,
            "strenuous_activity_minutes": 0,
            "trance_minutes": TRANCE_LONG_REST_MINUTES,
        }
    return {
        "sleep_minutes": duration_minutes,
        "light_activity_minutes": 0,
        "strenuous_activity_minutes": 0,
        "trance_minutes": 0,
    }


def record_rest_completion(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    started_elapsed_ticks: int | None = None,
    completed_elapsed_ticks: int | None = None,
    hit_dice_spent_count: int = 0,
    expected_character_revision: int = 0,
    song_of_rest_die_sides: int | None = None,
    song_of_rest_used: bool = False,
) -> dict[str, Any]:
    """Validate game-time rest timing and preserve canonical tick positions."""
    normalized = str(rest_type).strip().lower().replace("-", "_")
    minimum_rest_minutes(normalized)
    if started_elapsed_ticks is None or completed_elapsed_ticks is None:
        raise CombatEngineError("rest tick bounds must be supplied together")
    if (
        isinstance(hit_dice_spent_count, bool)
        or not isinstance(hit_dice_spent_count, int)
        or hit_dice_spent_count < 0
    ):
        raise CombatEngineError("hit_dice_spent_count must be a non-negative integer")
    if (
        isinstance(expected_character_revision, bool)
        or not isinstance(expected_character_revision, int)
        or expected_character_revision < 0
    ):
        raise CombatEngineError("expected_character_revision must be a non-negative integer")
    if song_of_rest_die_sides is not None and (
        isinstance(song_of_rest_die_sides, bool)
        or not isinstance(song_of_rest_die_sides, int)
        or song_of_rest_die_sides < 1
    ):
        raise CombatEngineError("song_of_rest_die_sides must be a positive integer")
    if not isinstance(song_of_rest_used, bool):
        raise CombatEngineError("song_of_rest_used must be a boolean")
    if song_of_rest_used and song_of_rest_die_sides is None:
        raise CombatEngineError("used Song of Rest must record its die size")
    started = int(started_elapsed_ticks)
    completed = int(completed_elapsed_ticks)
    if started < 0 or completed < started:
        raise CombatEngineError("rest clock bounds are invalid")
    allows_trance = allows_trance_rest(sheet)
    minimum_minutes = minimum_rest_minutes(
        normalized,
        allows_trance=allows_trance,
    )
    duration_ticks = completed - started
    if duration_ticks < minimum_minutes * TICKS_PER_MINUTE:
        raise CombatEngineError(f"{normalized} requires at least {minimum_minutes} minutes")
    if duration_ticks % TICKS_PER_MINUTE:
        raise CombatEngineError("rest duration must contain a whole number of minutes")
    validate_rest_schedule(
        rest_type=normalized,
        duration_minutes=duration_ticks // TICKS_PER_MINUTE,
        allows_trance=allows_trance,
    )
    validate_rest_eligibility(sheet, rest_type=normalized)
    history = dict(dict(sheet.get("combat") or {}).get("rest_history") or {})
    previous_completed = history.get("last_rest_completed_elapsed_ticks")
    if previous_completed is not None and completed <= int(previous_completed):
        raise CombatEngineError(
            "a creature cannot benefit from more than one rest ending at the same campaign time"
        )
    previous_long = history.get("last_long_rest_elapsed_ticks")
    if (
        normalized == "long_rest"
        and previous_long is not None
        and completed - int(previous_long) < TICKS_PER_DAY
    ):
        raise CombatEngineError(
            "a creature cannot benefit from more than one long rest in 24 hours"
        )
    value = deepcopy(sheet)
    next_history = value.setdefault("combat", {}).setdefault("rest_history", {})
    next_history.update(
        {
            "last_rest_type": normalized,
            "last_rest_started_elapsed_ticks": started,
            "last_rest_completed_elapsed_ticks": completed,
        }
    )
    if normalized == "long_rest":
        next_history["last_long_rest_elapsed_ticks"] = completed
    else:
        next_history.setdefault("last_long_rest_elapsed_ticks", previous_long)
    combat = value["combat"]
    combat.pop("short_rest_hit_dice", None)
    hp = int(dict(combat.get("hp") or {}).get("value", 0) or 0)
    if (
        normalized == "short_rest"
        and _sheet_edition(value) == "2014"
        and hp < effective_hit_point_maximum(value)
    ):
        remaining = {
            str(key): int(resource.get("value", 0) or 0)
            for key, resource in dict(combat.get("hit_dice") or {}).items()
            if isinstance(resource, dict) and int(resource.get("value", 0) or 0) > 0
        }
        if remaining:
            combat["short_rest_hit_dice"] = {
                "rest_completed_elapsed_ticks": completed,
                "expected_character_revision": expected_character_revision,
                "remaining": remaining,
                "spent_count": hit_dice_spent_count,
                "song_of_rest_die_sides": song_of_rest_die_sides,
                "song_of_rest_used": song_of_rest_used,
            }
    return value


def apply_short_rest_hit_die_choice(
    sheet: dict[str, Any],
    *,
    decision: str,
    hit_die_key: str | None = None,
    rest_completed_elapsed_ticks: int,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve one 2014 post-rest Hit Die decision from the persisted window."""

    if _sheet_edition(sheet) != "2014":
        raise CombatEngineError("sequential short-rest Hit Dice require the 2014 rules")
    if isinstance(rest_completed_elapsed_ticks, bool) or not isinstance(
        rest_completed_elapsed_ticks, int
    ):
        raise CombatEngineError("rest_completed_elapsed_ticks must be an integer")
    normalized_decision = str(decision).strip().casefold().replace("-", "_")
    if normalized_decision not in {"spend", "stop"}:
        raise CombatEngineError("decision must be spend or stop")
    normalized_key = str(hit_die_key or "").strip()
    if normalized_decision == "stop" and normalized_key:
        raise CombatEngineError("hit_die_key must be omitted when stopping")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    window = combat.get("short_rest_hit_dice")
    if not isinstance(window, dict):
        raise CombatEngineError("no sequential Hit Die choice is open")
    window_ticks = int(window.get("rest_completed_elapsed_ticks", -1) or 0)
    if window_ticks != rest_completed_elapsed_ticks:
        raise CombatEngineError("the Hit Die choice belongs to a different short rest")
    if normalized_decision == "stop":
        combat.pop("short_rest_hit_dice", None)
        return {
            "sheet": value,
            "status": "closed",
            "decision": "stop",
            "close_reason": "player_stopped",
            "rest_completed_elapsed_ticks": window_ticks,
            "hit_die_roll": None,
            "rolled_healing": 0,
            "applied_healing": 0,
            "song_of_rest": None,
            "rule_receipts": core_receipts(
                rules,
                ["dnd5e.core.rest.hit_dice"],
                "rest.hit_die_choice.stop",
            ),
            "ruleset_fingerprint": rules.fingerprint if rules else "",
        }

    hp = int(dict(combat.get("hp") or {}).get("value", 0) or 0)
    if hp >= effective_hit_point_maximum(value):
        combat.pop("short_rest_hit_dice", None)
        return {
            "sheet": value,
            "status": "closed",
            "decision": "spend",
            "close_reason": "full_hp",
            "rest_completed_elapsed_ticks": window_ticks,
            "hit_die_roll": None,
            "rolled_healing": 0,
            "applied_healing": 0,
            "song_of_rest": None,
            "rule_receipts": core_receipts(
                rules,
                ["dnd5e.core.rest.hit_dice"],
                "rest.hit_die_choice.full_hp",
            ),
            "ruleset_fingerprint": rules.fingerprint if rules else "",
        }
    key = normalized_key
    if not key:
        raise CombatEngineError("hit_die_key is required when spending a Hit Die")
    remaining = {
        str(candidate): int(amount)
        for candidate, amount in dict(window.get("remaining") or {}).items()
        if int(amount) > 0
    }
    if remaining.get(key, 0) < 1:
        raise CombatEngineError(f"Hit Die is not available in this short rest: {key}")
    hit_dice = dict(combat.get("hit_dice") or {})
    resource = hit_dice.get(key)
    if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) < 1:
        raise CombatEngineError(f"not enough hit dice remain for {key}")

    sides = _hit_die_sides(key, resource)
    constitution_modifier = effective_ability_modifier(value, "constitution")
    rolled = asdict(roll(f"1d{sides}", rng=rng))
    mutate_bounded_resource(resource, amount=1, direction="spend")
    remaining[key] -= 1
    if remaining[key] == 0:
        remaining.pop(key)
    if remaining:
        combat["short_rest_hit_dice"] = {
            **window,
            "remaining": dict(remaining),
        }
    else:
        combat.pop("short_rest_hit_dice", None)
    rolled_healing = max(
        0,
        int(rolled["total"]) + constitution_modifier,
    )
    applied_healing = 0
    if rolled_healing:
        healed = apply_basic_healing_to_sheet(value, amount=rolled_healing)
        value = healed["sheet"]
        combat = value["combat"]
        applied_healing = int(healed["amount"])

    song_result = None
    song_die_sides = window.get("song_of_rest_die_sides")
    song_used = bool(window.get("song_of_rest_used", False))
    if applied_healing > 0 and song_die_sides is not None and not song_used:
        song_roll = asdict(roll(f"1d{int(song_die_sides)}", rng=rng))
        song_healing = apply_basic_healing_to_sheet(value, amount=int(song_roll["total"]))
        value = song_healing["sheet"]
        combat = value["combat"]
        song_result = {
            "die": f"1d{int(song_die_sides)}",
            "roll": song_roll,
            "rolled_healing": int(song_roll["total"]),
            "applied_healing": int(song_healing["amount"]),
        }
        song_used = True

    spent_count = int(window.get("spent_count", 0) or 0) + 1
    close_reason = None
    if int(dict(combat.get("hp") or {}).get("value", 0) or 0) >= effective_hit_point_maximum(value):
        close_reason = "full_hp"
    elif not remaining:
        close_reason = "no_hit_dice"
    if close_reason is not None:
        combat.pop("short_rest_hit_dice", None)
        status = "closed"
    else:
        combat["short_rest_hit_dice"] = {
            "rest_completed_elapsed_ticks": window_ticks,
            "remaining": remaining,
            "spent_count": spent_count,
            "song_of_rest_die_sides": song_die_sides,
            "song_of_rest_used": song_used,
        }
        status = "open"
    mechanic_ids = ["dnd5e.core.rest.hit_dice"]
    if song_result is not None:
        mechanic_ids.append("dnd5e.core.rest.song_of_rest")
    return {
        "sheet": value,
        "status": status,
        "decision": "spend",
        "close_reason": close_reason,
        "rest_completed_elapsed_ticks": window_ticks,
        "hit_die_key": key,
        "hit_die_roll": {"key": key, **rolled},
        "rolled_healing": rolled_healing,
        "applied_healing": applied_healing,
        "song_of_rest": song_result,
        "spent_count": spent_count,
        "remaining": dict(remaining),
        "rule_receipts": core_receipts(
            rules,
            mechanic_ids,
            "rest.hit_die_choice.spend",
        ),
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


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
    _reconcile_ended_effects(value, expired)
    return {
        "sheet": value,
        "period": normalized,
        "amount": amount,
        "advanced": advanced,
        "expired": expired,
    }


def advance_source_turn_effect_durations(
    sheet: dict[str, Any], *, source_actor_id: str
) -> dict[str, Any]:
    """Expire effects timed to the start of one named source actor's turn."""
    source_id = str(source_actor_id).strip()
    if not source_id:
        raise CombatEngineError("source_actor_id is required")
    value = deepcopy(sheet)
    advanced: list[str] = []
    expired: list[str] = []
    for effect in value.get("effects", []):
        if not effect.get("active"):
            continue
        duration = dict(effect.get("duration") or {})
        if (
            duration.get("period") != "source_turn_start"
            or str(effect.get("source") or "") != source_id
        ):
            continue
        remaining = int(duration.get("remaining", 0) or 0)
        if remaining <= 1:
            effect["active"] = False
            effect["ended_reason"] = "duration_expired"
            expired.append(str(effect.get("id")))
        else:
            duration["remaining"] = remaining - 1
            effect["duration"] = duration
            advanced.append(str(effect.get("id")))
    _reconcile_ended_effects(value, expired)
    return {
        "sheet": value,
        "source_actor_id": source_id,
        "period": "source_turn_start",
        "amount": 1,
        "advanced": advanced,
        "expired": expired,
    }


def expire_combat_bound_effects(sheet: dict[str, Any]) -> dict[str, Any]:
    """End effects whose duration clock cannot continue after combat closes."""
    value = deepcopy(sheet)
    expired: list[str] = []
    for effect in value.get("effects", []):
        if not effect.get("active"):
            continue
        duration = dict(effect.get("duration") or {})
        if duration.get("period") not in COMBAT_BOUND_EFFECT_PERIODS:
            continue
        effect["active"] = False
        effect["ended_reason"] = "combat_ended"
        expired.append(str(effect.get("id")))
    _reconcile_ended_effects(value, expired)
    return {
        "sheet": value,
        "periods": sorted(COMBAT_BOUND_EFFECT_PERIODS),
        "expired": expired,
    }


def _elapsed_duration_ticks(
    *,
    elapsed_ticks: int,
    subject: str,
) -> int:
    """Validate one duration delta on the canonical tick stream."""

    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 1:
        raise CombatEngineError(f"elapsed {subject} duration ticks must be positive")
    return elapsed_ticks


def _advance_elapsed_effect_collection(
    value: dict[str, Any],
    *,
    collection_key: str,
    elapsed_ticks: int,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Apply the one tick-based elapsed-duration algorithm to any effect ledger."""

    result = deepcopy(value)
    advanced: list[str] = []
    expired: list[str] = []
    unit_ticks = {
        "minute": TICKS_PER_MINUTE,
        "hour": TICKS_PER_HOUR,
        "day": TICKS_PER_DAY,
    }
    for effect in result.get(collection_key, []):
        if not effect.get("active"):
            continue
        duration = dict(effect.get("duration") or {})
        period = str(duration.get("period") or "")
        unit = unit_ticks.get(period)
        if unit is None:
            continue
        previous_remainder = int(duration.get("elapsed_ticks_remainder", 0) or 0)
        elapsed_units, remainder = divmod(previous_remainder + elapsed_ticks, unit)
        if elapsed_units == 0:
            if remainder != previous_remainder:
                duration["elapsed_ticks_remainder"] = remainder
                effect["duration"] = duration
                advanced.append(str(effect.get("id")))
            continue
        remaining = int(duration.get("remaining", 0) or 0)
        if remaining <= elapsed_units:
            effect["active"] = False
            effect["ended_reason"] = "duration_expired"
            duration.pop("elapsed_ticks_remainder", None)
            effect["duration"] = duration
            expired.append(str(effect.get("id")))
            continue
        duration["remaining"] = remaining - elapsed_units
        if remainder:
            duration["elapsed_ticks_remainder"] = remainder
        else:
            duration.pop("elapsed_ticks_remainder", None)
        effect["duration"] = duration
        advanced.append(str(effect.get("id")))
    return result, advanced, expired


def advance_elapsed_effect_durations(
    sheet: dict[str, Any],
    *,
    elapsed_ticks: int,
) -> dict[str, Any]:
    """Advance actor effects by an exact interval on the canonical tick stream."""

    delta_ticks = _elapsed_duration_ticks(
        elapsed_ticks=elapsed_ticks,
        subject="effect",
    )
    value, advanced, expired = _advance_elapsed_effect_collection(
        sheet,
        collection_key="effects",
        elapsed_ticks=delta_ticks,
    )
    _reconcile_ended_effects(value, expired)
    return {
        "sheet": value,
        "elapsed_ticks": delta_ticks,
        "elapsed_minutes": delta_ticks // TICKS_PER_MINUTE,
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


def advance_elapsed_world_effect_durations(
    state: dict[str, Any],
    *,
    elapsed_ticks: int,
) -> dict[str, Any]:
    """Advance campaign-space effects with the actor tick algorithm."""

    delta_ticks = _elapsed_duration_ticks(
        elapsed_ticks=elapsed_ticks,
        subject="world effect",
    )
    value, advanced, expired = _advance_elapsed_effect_collection(
        state,
        collection_key="world_effects",
        elapsed_ticks=delta_ticks,
    )
    return {
        "state": value,
        "elapsed_ticks": delta_ticks,
        "elapsed_minutes": delta_ticks // TICKS_PER_MINUTE,
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
    conditions = condition_ids(value.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot recover from being stable")
    if int(hp.get("value", 0) or 0) != 0 or "stable" not in conditions:
        raise CombatEngineError("stable recovery requires a Stable creature at 0 hit points")
    value = apply_basic_healing_to_sheet(value, amount=1)["sheet"]
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
    conditions = condition_ids(value.get("conditions"))
    if int(hp.get("value", 0) or 0) != 0:
        raise CombatEngineError("stable_unconscious source state requires 0 hit points")
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot be initialized as stable unconscious")
    conditions.update({"prone", "stable", "unconscious"})
    combat["death_saves"] = {"successes": 0, "failures": 0}
    reconcile_condition_projection(value, conditions)
    return {
        "sheet": value,
        "status": "initialized",
        "source_state": normalized,
    }


def stand_outside_combat(sheet: dict[str, Any]) -> dict[str, Any]:
    """Stand a conscious living creature without exposing arbitrary condition edits."""
    value = deepcopy(sheet)
    hp = int(dict(value.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = condition_ids(value.get("conditions"))
    if hp <= 0 or "dead" in conditions or "unconscious" in conditions:
        raise CombatEngineError("standing requires a conscious living creature above 0 hit points")
    if "prone" not in conditions:
        raise CombatEngineError("standing requires the Prone condition")
    apply_condition_change(value, condition_id="prone", add=False)
    if "prone" in condition_ids(value.get("conditions")):
        raise CombatEngineError("the Prone condition is still owned by an active effect")
    return {"sheet": value, "status": "stood", "removed_condition": "prone"}


def knock_prone_outside_combat(sheet: dict[str, Any]) -> dict[str, Any]:
    """Apply Prone to a conscious living creature without arbitrary condition edits."""
    value = deepcopy(sheet)
    hp = int(dict(value.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = condition_ids(value.get("conditions"))
    if hp <= 0 or "dead" in conditions or "unconscious" in conditions:
        raise CombatEngineError(
            "knocking prone outside combat requires a conscious living creature above 0 hit points"
        )
    if "prone" in conditions:
        return {"sheet": value, "status": "already_prone", "added_condition": None}
    apply_condition_change(value, condition_id="prone", add=True)
    if "prone" not in condition_ids(value.get("conditions")):
        return {
            "sheet": value,
            "status": "immune",
            "added_condition": None,
        }
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


def recover_chase_exhaustion(sheet: dict[str, Any]) -> dict[str, Any]:
    """Remove every exhaustion level explicitly recorded as chase fatigue."""
    value = deepcopy(sheet)
    recovered = 0
    for effect in value.get("effects", []):
        if not effect.get("active") or effect.get("kind") != "chase_exhaustion":
            continue
        changes = [
            item
            for item in effect.get("changes", [])
            if item.get("path") == "combat.exhaustion" and item.get("mode") == "chase_levels"
        ]
        if len(changes) != 1:
            raise CombatEngineError("active chase exhaustion effect is malformed")
        levels = changes[0].get("value")
        if isinstance(levels, bool) or not isinstance(levels, int) or levels <= 0:
            raise CombatEngineError("active chase exhaustion level count is invalid")
        recovered += levels
        effect["active"] = False
        effect["ended_reason"] = "short_or_long_rest"
    combat = value.setdefault("combat", {})
    before = int(combat.get("exhaustion", 0) or 0)
    value = set_exhaustion_level(value, max(0, before - recovered))
    combat = value["combat"]
    return {
        "sheet": value,
        "before": before,
        "recovered": recovered,
        "after": int(combat["exhaustion"]),
    }


def apply_rest(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    hit_dice_spends: list[dict[str, Any]] | None = None,
    hit_dice_recovery: dict[str, int] | None = None,
    arcane_recovery: dict[str, int] | None = None,
    natural_recovery: dict[str, int] | None = None,
    sorcerous_restoration_points: int | None = None,
    rest_activity_minutes: dict[str, int] | None = None,
    food_and_drink: bool = False,
    song_of_rest_source_sheet: dict[str, Any] | None = None,
    rules: ResolutionContext | None = None,
    rng: Any = None,
    game_day: int | None = None,
) -> dict[str, Any]:
    """Settle a short or long rest without inventing player-choice allocations."""
    rest_type = str(rest_type).strip().lower().replace("-", "_")
    if rest_type not in REST_TYPES:
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
    if rest_type != "short_rest" and sorcerous_restoration_points is not None:
        raise CombatEngineError(
            "Sorcerous Restoration can be used only when finishing a short rest"
        )
    if rest_type != "short_rest" and song_of_rest_source_sheet is not None:
        raise CombatEngineError("Song of Rest applies only when finishing a short rest")
    normalized_rest_activities = validate_rest_activity_minutes(rest_activity_minutes)
    song_of_rest_die_sides = (
        validate_song_of_rest_source(song_of_rest_source_sheet)
        if song_of_rest_source_sheet is not None
        else None
    )
    if rest_type == "short_rest":
        validate_initial_rest_hit_dice_requests(sheet, hit_dice_spends)
        validate_arcane_recovery_choice(
            sheet,
            arcane_recovery,
            game_day=game_day,
        )
        validate_natural_recovery_choice(
            sheet,
            natural_recovery,
            rest_activity_minutes=normalized_rest_activities,
        )
        validate_sorcerous_restoration_choice(
            sheet,
            sorcerous_restoration_points,
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
    chase_recovery = recover_chase_exhaustion(before_rules.sheet)
    value = chase_recovery["sheet"]
    value.setdefault("combat", {}).pop("short_rest_hit_dice", None)
    validate_rest_eligibility(value, rest_type=rest_type)
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    edition = _sheet_edition(value)
    recovered: dict[str, int] = {}
    unmet_recovery_requirements: dict[str, dict[str, Any]] = {}
    hit_die_healing = 0
    hit_die_applied_healing = 0
    hit_dice_rolls: list[dict[str, Any]] = []
    arcane_recovery_result: dict[str, Any] | None = None
    natural_recovery_result: dict[str, Any] | None = None
    song_of_rest_result: dict[str, Any] | None = None
    sorcerous_restoration_result: dict[str, Any] | None = None
    heroic_inspiration_result: dict[str, Any] | None = None
    if rest_type == "long_rest":
        exhaustion = int(combat.get("exhaustion", 0) or 0)
        if edition == "2024" or food_and_drink:
            exhaustion = max(0, exhaustion - 1)
        value = set_exhaustion_level(value, exhaustion)
        combat = value["combat"]
        hp = dict(combat["hp"])
        hp["value"] = effective_hit_point_maximum(value)
        hp["temp"] = 0
        combat["hp"] = hp
        combat["death_saves"] = {"successes": 0, "failures": 0}
        apply_condition_change(value, condition_id="unconscious", add=False)
        apply_condition_change(value, condition_id="stable", add=False)
    else:
        hit_dice = combat.get("hit_dice", {})
        hit_die_resolution = roll_rest_hit_dice(value, hit_dice_spends, rng=rng)
        hit_dice_rolls = hit_die_resolution["rolls"]
        for spend in hit_die_resolution["spends"]:
            key = str(spend["key"])
            resource = hit_dice.get(key)
            roll_value = int(spend["roll"])
            mutate_bounded_resource(resource, amount=1, direction="spend")
            healing = roll_value + effective_ability_modifier(value, "constitution")
            hit_die_healing += max(1 if edition == "2024" else 0, healing)
        if hit_die_healing:
            healed = apply_basic_healing_to_sheet(value, amount=hit_die_healing)
            value = healed["sheet"]
            combat = value["combat"]
            hp = dict(combat["hp"])
            hit_die_applied_healing = int(healed["amount"])
        if hit_die_applied_healing > 0 and song_of_rest_die_sides is not None:
            song_roll = asdict(roll(f"1d{song_of_rest_die_sides}", rng=rng))
            healed = apply_basic_healing_to_sheet(
                value,
                amount=int(song_roll["total"]),
            )
            value = healed["sheet"]
            combat = value["combat"]
            hp = dict(combat["hp"])
            song_of_rest_result = {
                "die": f"1d{song_of_rest_die_sides}",
                "roll": song_roll,
                "rolled_healing": int(song_roll["total"]),
                "applied_healing": int(healed["amount"]),
            }
        if arcane_recovery:
            arcane_recovery_result = apply_arcane_recovery_choice(
                value,
                arcane_recovery,
                game_day=game_day,
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
                recovered[f"spell_slot:{level}"] = recovered.get(f"spell_slot:{level}", 0) + amount
        sorcerous_restoration_result = apply_sorcerous_restoration(
            value,
            points=sorcerous_restoration_points,
        )
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
        recovery_amounts = dict(resource.get("recovery_amounts") or {})
        recovery_amount = recovery_amounts.get(rest_type, "all")
        amount = (
            int(resource.get("max", 0) or 0) if recovery_amount == "all" else int(recovery_amount)
        )
        mutation = mutate_bounded_resource(
            resource,
            amount=amount,
            direction="recover",
        )
        recovered[key] = recovered.get(key, 0) + mutation["amount"]

    for key, resource in value.get("resources", {}).items():
        recover_resource(resource, key)
    for key, resource in value.get("spellcasting", {}).get("spell_slots", {}).items():
        if rest_type == "long_rest":
            mutation = mutate_bounded_resource(
                resource,
                amount=int(resource.get("max", 0) or 0),
                direction="recover",
            )
            recovered[f"spell_slot:{key}"] = mutation["amount"]
    if rest_type == "long_rest":
        points = value.get("spellcasting", {}).get("spell_points")
        if isinstance(points, dict):
            mutation = mutate_bounded_resource(
                points,
                amount=int(points.get("max", 0) or 0),
                direction="recover",
            )
            recovered["spell_points"] = mutation["amount"]
    pact_magic = value.get("spellcasting", {}).get("pact_magic")
    recover_resource(pact_magic, "pact_magic")
    if (
        rest_type == "long_rest"
        and isinstance(pact_magic, dict)
        and pact_magic.get("recovers_on") == "none"
    ):
        mutation = mutate_bounded_resource(
            pact_magic,
            amount=int(pact_magic.get("max", 0) or 0),
            direction="recover",
        )
        recovered["pact_magic"] = mutation["amount"]
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
            if hit_dice_recovery is None:
                if (
                    sum(1 for amount in missing.values() if amount > 0) > 1
                    and sum(missing.values()) > allowance
                ):
                    raise CombatEngineError(
                        "2014 long-rest hit-die recovery needs a player allocation"
                    )
                allocation = {
                    key: min(amount, allowance) for key, amount in missing.items()
                }
            else:
                if not isinstance(hit_dice_recovery, dict):
                    raise CombatEngineError(
                        "2014 hit-die recovery allocation must be an object"
                    )
                unknown_keys = set(hit_dice_recovery) - set(missing)
                if unknown_keys:
                    raise CombatEngineError(
                        "2014 hit-die recovery allocation contains an unknown hit die"
                    )
                if any(
                    isinstance(amount, bool)
                    or not isinstance(amount, int)
                    or amount < 0
                    for amount in hit_dice_recovery.values()
                ):
                    raise CombatEngineError(
                        "2014 hit-die recovery counts must be non-negative integers"
                    )
                allocation = {
                    key: hit_dice_recovery.get(key, 0) for key in missing
                }
            if (
                any(amount < 0 or amount > missing[key] for key, amount in allocation.items())
                or sum(allocation.values()) > allowance
            ):
                raise CombatEngineError("2014 hit-die recovery allocation is invalid")
        for key, amount in allocation.items():
            resource = hit_dice[key]
            mutation = mutate_bounded_resource(
                resource,
                amount=amount,
                direction="recover",
            )
            recovered[f"hit_dice:{key}"] = mutation["amount"]
    for section in ACTIVITY_CONTENT_SECTIONS:
        for index, item in enumerate(value.get("content", {}).get(section, [])):
            recover_resource(item.get("uses"), f"{section}:{index}:uses")
    for index, item in enumerate(value.get("inventory", {}).get("items", [])):
        recover_resource(item.get("uses"), f"inventory:{index}:uses")
        recover_resource(item.get("charges"), f"inventory:{index}:charges")
    value["combat"] = combat | {"hp": hp}
    if (
        rest_type == "long_rest"
        and edition == "2024"
        and any(
            str(dict(feature.get("choices") or {}).get("grant_heroic_inspiration_on") or "")
            == "long_rest"
            for feature in value.get("content", {}).get("features", [])
            if isinstance(feature, dict)
        )
    ):
        heroic_inspiration_result = grant_heroic_inspiration(value)
        value = heroic_inspiration_result["sheet"]
    revival_ordeals = {"sheet": value, "reduced": []}
    if rest_type == "long_rest":
        revival_ordeals = reduce_revival_ordeal_after_long_rest(value)
        value = revival_ordeals["sheet"]
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
        "chase_exhaustion_recovery": {
            key: item for key, item in chase_recovery.items() if key != "sheet"
        },
        "hit_die_healing": hit_die_healing,
        "hit_die_applied_healing": hit_die_applied_healing,
        "hit_dice_rolls": hit_dice_rolls,
        "arcane_recovery": arcane_recovery_result,
        "natural_recovery": natural_recovery_result,
        "song_of_rest": song_of_rest_result,
        "sorcerous_restoration": sorcerous_restoration_result,
        "heroic_inspiration": (
            {
                key: item
                for key, item in heroic_inspiration_result.items()
                if key not in {"sheet", "recipient_sheet"}
            }
            if heroic_inspiration_result is not None
            else None
        ),
        "revival_ordeals": revival_ordeals["reduced"],
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
                    *(["dnd5e.core.rest.song_of_rest"] if song_of_rest_result is not None else []),
                    *(
                        ["dnd5e.core.rest.sorcerous_restoration"]
                        if sorcerous_restoration_result is not None
                        else []
                    ),
                    *(
                        ["dnd5e.core.heroic_inspiration"]
                        if heroic_inspiration_result is not None
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
    edition = _sheet_edition(sheet)
    if edition != "2014":
        raise CombatEngineError("Song of Rest requires the 2014 Bard feature")
    hp = int(dict(sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = condition_ids(sheet.get("conditions"))
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
        if isinstance(item, dict) and str(item.get("name") or "").strip().casefold() == "bard"
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


def validate_initial_rest_hit_dice_requests(
    sheet: dict[str, Any],
    spends: list[dict[str, Any]] | None,
) -> list[tuple[str, int]]:
    """Validate the choices visible before any short-rest Hit Die roll."""

    requested = validate_rest_hit_dice_requests(sheet, spends)
    if _sheet_edition(sheet) == "2014" and sum(count for _key, count in requested) > 1:
        raise CombatEngineError(
            "2014 short rests accept one initial Hit Die; decide on each additional die "
            "after its preceding roll"
        )
    return requested


def validate_arcane_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int] | None,
    *,
    game_day: int | None = None,
) -> dict[str, Any] | None:
    """Validate Arcane Recovery using the edition's source-defined reset."""
    if not choice:
        return None
    if not isinstance(choice, dict):
        raise CombatEngineError("arcane_recovery must map spell-slot levels to counts")
    feature = _arcane_recovery_feature(sheet)
    if feature is None:
        raise CombatEngineError("the actor does not have Arcane Recovery")
    resolved_game_day = game_day
    edition = _sheet_edition(sheet)
    choices = dict(feature.get("choices") or {})
    uses = dict(feature.get("uses") or {})
    if edition == "2014":
        if (
            isinstance(resolved_game_day, bool)
            or not isinstance(resolved_game_day, int)
            or resolved_game_day < 1
        ):
            raise CombatEngineError("2014 Arcane Recovery requires the current game day")
        last_used_day = choices.get("_arcane_recovery_last_used_game_day")
        if last_used_day is not None and int(last_used_day) == resolved_game_day:
            raise CombatEngineError("Arcane Recovery has already been used on this game day")
    elif int(uses.get("max", 0) or 0) != 1 or int(uses.get("value", 0) or 0) < 1:
        raise CombatEngineError(
            "2024 Arcane Recovery has already been used since the last long rest"
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
    result = {
        "allowance": allowance,
        "used_levels": used_levels,
        "recovered": normalized,
        "edition": edition,
        "reset_on": "game_day" if edition == "2014" else "long_rest",
    }
    if edition == "2014":
        result["game_day"] = resolved_game_day
    return result


def apply_arcane_recovery_choice(
    sheet: dict[str, Any],
    choice: dict[str, int],
    *,
    game_day: int | None = None,
) -> dict[str, Any]:
    """Apply one previously validated Arcane Recovery allocation in place."""
    result = validate_arcane_recovery_choice(
        sheet,
        choice,
        game_day=game_day,
    )
    assert result is not None
    slots = sheet["spellcasting"]["spell_slots"]
    for level, count in result["recovered"].items():
        mutate_bounded_resource(slots[level], amount=count, direction="recover")
    feature = _arcane_recovery_feature(sheet)
    assert feature is not None
    feature["uses"] = {
        "label": "Arcane Recovery",
        "value": 0,
        "max": 1,
        "recovers_on": ("manual" if result["edition"] == "2014" else "long_rest"),
        "source_key": "Wizard",
        "slot_level": 0,
    }
    feature_choices = dict(feature.get("choices") or {})
    feature_choices.pop("_arcane_recovery_last_used_game_day", None)
    if result["edition"] == "2014":
        feature_choices["_arcane_recovery_last_used_game_day"] = result["game_day"]
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
    if _sheet_edition(sheet) != "2014":
        raise CombatEngineError("Natural Recovery requires the 2014 Druid feature")
    feature = _natural_recovery_feature(sheet)
    if feature is None:
        raise CombatEngineError("the actor does not have source-bound Natural Recovery")
    if int((rest_activity_minutes or {}).get("meditation", 0) or 0) < 1:
        raise CombatEngineError("Natural Recovery requires declared meditation during the rest")
    uses = dict(feature.get("uses") or {})
    if int(uses.get("max", 0) or 0) == 1 and int(uses.get("value", 0) or 0) == 0:
        raise CombatEngineError("Natural Recovery has already been used since the last long rest")
    druid_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip().casefold() == "druid"
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
        mutate_bounded_resource(slots[level], amount=count, direction="recover")
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


def validate_sorcerous_restoration_choice(sheet: dict[str, Any], points: int | None) -> None:
    """Preflight the optional SRD 5.2.1 short-rest Sorcery Point recovery."""

    if points is None:
        return
    if isinstance(points, bool) or not isinstance(points, int) or points < 1:
        raise CombatEngineError("Sorcerous Restoration points must be a positive integer")
    if _sheet_edition(sheet) != "2024":
        raise CombatEngineError("declared Sorcerous Restoration points require the 2024 feature")
    feature = _sorcerous_restoration_feature(sheet)
    if feature is None:
        raise CombatEngineError("Sorcerous Restoration is not on the actor card")
    sorcerer_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip().casefold() == "sorcerer"
    )
    if sorcerer_level < 5:
        raise CombatEngineError("Sorcerous Restoration requires 5 Sorcerer levels")
    allowance = sorcerer_level // 2
    if points > allowance:
        raise CombatEngineError(
            "Sorcerous Restoration exceeds half the Sorcerer level rounded down"
        )
    uses = feature.get("uses")
    if not isinstance(uses, dict) or int(uses.get("value", 0) or 0) < 1:
        raise CombatEngineError("Sorcerous Restoration has already been used")
    resource = sheet.get("resources", {}).get("sorcery_points")
    if not isinstance(resource, dict):
        raise CombatEngineError("Sorcerous Restoration requires the Sorcery Points resource")
    missing = int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
    if points > missing:
        raise CombatEngineError(
            "Sorcerous Restoration cannot recover more Sorcery Points than are expended"
        )


def apply_sorcerous_restoration(
    sheet: dict[str, Any], *, points: int | None = None
) -> dict[str, Any] | None:
    """Apply the edition-specific Sorcerous Restoration short-rest rule."""
    feature = _sorcerous_restoration_feature(sheet)
    if feature is None:
        if points is not None:
            raise CombatEngineError("Sorcerous Restoration is not on the actor card")
        return None
    edition = _sheet_edition(sheet)
    if edition == "2024" and points is None:
        return None
    if edition == "2014" and points is not None:
        raise CombatEngineError("the 2014 Sorcerous Restoration recovery is automatic")
    sorcerer_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip().casefold() == "sorcerer"
    )
    required_level = 20 if edition == "2014" else 5
    if sorcerer_level < required_level:
        raise CombatEngineError(f"Sorcerous Restoration requires {required_level} Sorcerer levels")
    resource = sheet.get("resources", {}).get("sorcery_points")
    if not isinstance(resource, dict):
        raise CombatEngineError("Sorcerous Restoration requires the Sorcery Points resource")
    if edition == "2024":
        validate_sorcerous_restoration_choice(sheet, points)
        mutate_bounded_resource(feature["uses"], amount=1, direction="spend")
        recovery_amount = int(points or 0)
    else:
        recovery_amount = 4
    mutation = mutate_bounded_resource(
        resource,
        amount=recovery_amount,
        direction="recover",
    )
    result = {
        "sorcerer_level": sorcerer_level,
        "before": mutation["before"],
        "recovered": mutation["amount"],
        "after": mutation["after"],
        "maximum": int(resource.get("max", 0) or 0),
    }
    if edition == "2024":
        result["edition"] = edition
        result["feature_uses_remaining"] = int(feature["uses"]["value"])
    return result


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
            raise CombatEngineError(f"{feature_name} cannot restore a level 6 or higher slot")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
            raise CombatEngineError(f"{feature_name} slot counts must be positive integers")
        resource = slots.get(str(level))
        if not isinstance(resource, dict):
            raise CombatEngineError(f"the actor has no level {level} spell slots")
        missing = int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
        if raw_count > missing:
            raise CombatEngineError(f"{feature_name} exceeds missing level {level} slots")
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
                str(item.get("id") or "").endswith("circle-of-the-land-natural-recovery")
                or str(item.get("name") or "").strip().casefold() == "natural recovery"
            )
            and any(
                str(ref).startswith("bundled:srd2014/02_Classes/Druid")
                for ref in item.get("rule_refs", [])
            )
        ),
        None,
    )


def _sorcerous_restoration_feature(sheet: dict[str, Any]) -> dict[str, Any] | None:
    edition = _sheet_edition(sheet)
    source_prefix = f"bundled:srd{edition}/"
    return next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if isinstance(item, dict)
            and (
                str(item.get("id") or "").endswith("sorcerer-sorcerous-restoration")
                or str(item.get("name") or "").strip().casefold() == "sorcerous restoration"
            )
            and str(item.get("source_key") or "").strip().casefold() == "sorcerer"
            and any(str(ref).startswith(source_prefix) for ref in item.get("rule_refs", []))
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


def _hit_die_sides(key: str, resource: dict[str, Any]) -> int:
    for candidate in (key, str(resource.get("label") or "")):
        lowered = candidate.casefold()
        if "d" in lowered:
            tail = lowered.rsplit("d", 1)[1]
            digits = "".join(char for char in tail if char.isdigit())
            if digits and int(digits) > 0:
                return int(digits)
    raise CombatEngineError(f"hit die {key} must identify its die size, for example d8")
