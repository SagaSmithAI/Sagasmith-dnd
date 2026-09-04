"""Shared hit-point invariants used by cards, rules, combat, and recovery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.conditions import apply_condition_change, condition_ids
from sagasmith_dnd.editions import DEFAULT_CHARACTER_EDITION, normalize_dnd_edition


def effective_hit_point_maximum_value(
    *,
    edition: str,
    base_maximum: int,
    exhaustion: int,
) -> int:
    """Return the one rules-effective maximum for recorded base HP."""

    maximum = int(base_maximum)
    if normalize_dnd_edition(edition) == "2014" and int(exhaustion) >= 4:
        return max(1, maximum // 2)
    return maximum


def apply_basic_healing_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
) -> dict[str, Any]:
    """Apply ordinary healing and its universal zero-HP state transition."""

    requested = int(amount)
    if requested < 0:
        raise ValueError("healing amount cannot be negative")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    conditions = condition_ids(value.get("conditions"))
    if "dead" in conditions:
        raise ValueError("ordinary healing cannot restore a dead actor")
    # Local import avoids the character_schema -> hit_points -> breathing
    # import cycle during package initialization.
    from sagasmith_dnd.breathing import breathing_blocks_recovery

    if breathing_blocks_recovery(value):
        raise ValueError("a suffocating actor cannot regain hit points until it can breathe")
    before = int(hp.get("value", 0) or 0)
    maximum = effective_hit_point_maximum_value(
        edition=str(value.get("edition") or DEFAULT_CHARACTER_EDITION),
        base_maximum=int(hp.get("max", 0) or 0),
        exhaustion=int(combat.get("exhaustion", 0) or 0),
    )
    hp["value"] = min(maximum, before + requested)
    combat["hp"] = hp
    if hp["value"] > 0:
        apply_condition_change(value, condition_id="unconscious", add=False)
        apply_condition_change(value, condition_id="stable", add=False)
        combat["death_saves"] = {"successes": 0, "failures": 0}
    return {
        "sheet": value,
        "before_hp": before,
        "after_hp": int(hp["value"]),
        "amount": int(hp["value"]) - before,
    }
