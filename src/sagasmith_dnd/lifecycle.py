"""Deterministic v2-card recovery and duration advancement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts


def advance_effect_durations(sheet: dict[str, Any], *, period: str) -> dict[str, Any]:
    """Advance effects whose declared period matches and deactivate expired ones."""
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
        if remaining <= 1:
            effect["active"] = False
            expired.append(str(effect.get("id")))
        else:
            duration["remaining"] = remaining - 1
            effect["duration"] = duration
            advanced.append(str(effect.get("id")))
    return {"sheet": value, "period": normalized, "advanced": advanced, "expired": expired}


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


def apply_rest(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    hit_dice_spends: list[dict[str, Any]] | None = None,
    hit_dice_recovery: dict[str, int] | None = None,
    food_and_drink: bool = False,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Settle a short or long rest without inventing player-choice allocations."""
    rest_type = str(rest_type).strip().lower().replace("-", "_")
    if rest_type not in {"short_rest", "long_rest"}:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    before_rules = apply_rule_event(sheet, "rest.before", rules)
    if before_rules.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "rest_type": rest_type,
            "status": before_rules.status,
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
        for spend in hit_dice_spends or []:
            if not isinstance(spend, dict):
                raise CombatEngineError("each hit-die spend must be an object")
            key = str(spend.get("key") or "")
            resource = hit_dice.get(key)
            if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) <= 0:
                raise CombatEngineError(f"no hit die remains for {key}")
            sides = _hit_die_sides(key, resource)
            roll = spend.get("roll")
            if isinstance(roll, bool) or not isinstance(roll, int) or not 1 <= roll <= sides:
                raise CombatEngineError(f"{key} hit-die roll must be an integer from 1 to {sides}")
            resource["value"] = int(resource["value"]) - 1
            healing = roll + _constitution_modifier(value)
            hit_die_healing += max(1 if edition == "2024" else 0, healing)
        if hit_die_healing:
            hp["value"] = min(
                int(hp.get("max", 0) or 0), int(hp.get("value", 0) or 0) + hit_die_healing
            )

    def recover_resource(resource: object, key: str) -> None:
        if not isinstance(resource, dict) or resource.get("recovers_on") != rest_type:
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
            "rule_receipts": [*before_rules.receipts, *after_rules.receipts],
            "pending": list(after_rules.pending),
        }
    return {
        "sheet": after_rules.sheet,
        "rest_type": rest_type,
        "recovered": recovered,
        "hit_die_healing": hit_die_healing,
        "effects_expired": duration["expired"],
        "status": "committed",
        "rule_receipts": [
            *core_receipts(
                rules,
                ["dnd5e.core.rest.hit_dice", "dnd5e.core.rest.exhaustion"],
                "rest.apply",
            ),
            *before_rules.receipts,
            *after_rules.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


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
