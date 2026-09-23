"""Transaction helpers for runtime madness triggers."""

from __future__ import annotations

import hashlib
from typing import Any

from sagasmith_dnd import madness as madness_domain

from .. import application_support as support


def settle_damage_triggered_confusion(
    *,
    actor: dict[str, Any],
    sheet: dict[str, Any],
    damage_taken: int,
    encounter: dict[str, Any] | None,
    rules: Any,
    transaction_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve source-owned DC 15 Wisdom saves in the damage transaction."""
    if damage_taken <= 0:
        return sheet, [], []
    effect_ids = madness_domain.damage_triggered_confusion_effect_ids(sheet)
    if not effect_ids:
        return sheet, [], []
    stream = support.active_random_stream()
    if stream is None:
        raise support.CombatEngineError(
            "damage-triggered madness requires the campaign random stream"
        )

    next_sheet = support.deepcopy(sheet)
    events: list[dict[str, Any]] = []
    for source_effect_id in effect_ids:
        check_actor = {**support.deepcopy(actor), "sheet": next_sheet}
        check = support.resolve_actor_check(
            check_actor,
            kind="save",
            ability="wisdom",
            dc=15,
            encounter=encounter,
            rules=rules,
            ruleset="2014",
            rng=stream,
        )
        source_save = {**check, "kind": "save", "ability": "wisdom", "dc": 15}
        digest = hashlib.sha256(
            f"{transaction_id}:{source_effect_id}".encode("utf-8")
        ).hexdigest()[:32]
        settled = madness_domain.apply_damage_triggered_confusion(
            next_sheet,
            source_effect_id=source_effect_id,
            damage_taken=damage_taken,
            save=source_save,
            confusion_effect_id=f"madness-confusion-{digest}",
        )
        next_sheet = settled["sheet"]
        events.append(settled["event"])
    receipts = support.core_receipts(
        rules,
        ["dnd5e.core.madness.2014"],
        "madness.damage_trigger",
    )
    return next_sheet, events, receipts
