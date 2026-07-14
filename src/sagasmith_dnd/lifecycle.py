"""Deterministic v2-card recovery and duration advancement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError


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


def apply_rest(
    sheet: dict[str, Any],
    *,
    rest_type: str,
    hit_dice_spent: int = 0,
) -> dict[str, Any]:
    """Settle deterministic resource recovery; individual hit-die rolls remain explicit."""
    rest_type = str(rest_type).strip().lower().replace("-", "_")
    if rest_type not in {"short_rest", "long_rest"}:
        raise CombatEngineError("rest_type must be short_rest or long_rest")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.get("hp") or {})
    recovered: dict[str, int] = {}
    if rest_type == "long_rest":
        hp["value"] = int(hp.get("max", 0) or 0)
        hp["temp"] = 0
        combat["death_saves"] = {"successes": 0, "failures": 0}
        value["conditions"] = [
            item for item in value.get("conditions", []) if item not in {"unconscious", "stable"}
        ]
    elif hit_dice_spent:
        raise CombatEngineError(
            "short-rest hit-die healing needs an explicit rolled healing result"
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
        for key, resource in value.get("combat", {}).get("hit_dice", {}).items():
            if not isinstance(resource, dict):
                continue
            before = int(resource.get("value", 0) or 0)
            maximum = int(resource.get("max", 0) or 0)
            resource["value"] = min(maximum, before + max(1, maximum // 2)) if maximum else 0
            recovered[f"hit_dice:{key}"] = resource["value"] - before
    for section in ("activities", "features", "feats"):
        for index, item in enumerate(value.get("content", {}).get(section, [])):
            recover_resource(item.get("uses"), f"{section}:{index}:uses")
    for index, item in enumerate(value.get("inventory", {}).get("items", [])):
        recover_resource(item.get("uses"), f"inventory:{index}:uses")
        recover_resource(item.get("charges"), f"inventory:{index}:charges")
    value["combat"] = combat | {"hp": hp}
    duration = advance_effect_durations(value, period=rest_type)
    return {
        "sheet": duration["sheet"],
        "rest_type": rest_type,
        "recovered": recovered,
        "effects_expired": duration["expired"],
    }
