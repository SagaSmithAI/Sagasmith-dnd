"""Deterministic v2-card recovery and duration advancement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.engine import roll
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts

REST_MINIMUM_MINUTES = {"short_rest": 60, "long_rest": 480}


def record_rest_completion(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    started_elapsed_minutes: int,
    completed_elapsed_minutes: int,
) -> dict[str, Any]:
    """Validate campaign-clock rest timing and preserve the last benefit time."""
    normalized = str(rest_type).strip().lower().replace("-", "_")
    if normalized not in REST_MINIMUM_MINUTES:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    started = int(started_elapsed_minutes)
    completed = int(completed_elapsed_minutes)
    if started < 0 or completed < started:
        raise CombatEngineError("rest clock bounds are invalid")
    if completed - started < REST_MINIMUM_MINUTES[normalized]:
        raise CombatEngineError(
            f"{normalized} requires at least {REST_MINIMUM_MINUTES[normalized]} minutes"
        )
    hp = int(dict(sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
    conditions = {str(item).casefold() for item in sheet.get("conditions", [])}
    if hp <= 0 or "dead" in conditions:
        raise CombatEngineError("a creature must have at least 1 hit point at the start of a rest")
    history = dict(dict(sheet.get("combat") or {}).get("rest_history") or {})
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
            expired.append(str(effect.get("id")))
        else:
            duration["remaining"] = remaining - amount
            effect["duration"] = duration
            advanced.append(str(effect.get("id")))
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


def recover_stable_creature(
    sheet: dict[str, Any], *, recovery_hours: int
) -> dict[str, Any]:
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


def apply_rest(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    hit_dice_spends: list[dict[str, Any]] | None = None,
    hit_dice_recovery: dict[str, int] | None = None,
    arcane_recovery: dict[str, int] | None = None,
    food_and_drink: bool = False,
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
    if rest_type == "short_rest":
        validate_rest_hit_dice_requests(sheet, hit_dice_spends)
        validate_arcane_recovery_choice(sheet, arcane_recovery, world_day=world_day)
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
    hit_die_healing = 0
    hit_dice_rolls: list[dict[str, Any]] = []
    arcane_recovery_result: dict[str, Any] | None = None
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
            hp["value"] = min(
                int(hp.get("max", 0) or 0), int(hp.get("value", 0) or 0) + hit_die_healing
            )
        if arcane_recovery:
            arcane_recovery_result = apply_arcane_recovery_choice(
                value,
                arcane_recovery,
                world_day=world_day,
            )
            for level, amount in arcane_recovery_result["recovered"].items():
                recovered[f"spell_slot:{level}"] = amount

    def recover_resource(resource: object, key: str) -> None:
        if not isinstance(resource, dict):
            return
        recovery = resource.get("recovers_on")
        if recovery != rest_type and not (
            rest_type == "long_rest" and recovery == "short_rest"
        ):
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
            "rule_receipts": [*before_rules.receipts, *after_rules.receipts],
            "pending": list(after_rules.pending),
        }
    return {
        "sheet": after_rules.sheet,
        "rest_type": rest_type,
        "recovered": recovered,
        "hit_die_healing": hit_die_healing,
        "hit_dice_rolls": hit_dice_rolls,
        "arcane_recovery": arcane_recovery_result,
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
                ],
                "rest.apply",
            ),
            *before_rules.receipts,
            *after_rules.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


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
