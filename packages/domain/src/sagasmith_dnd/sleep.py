"""Pure 2014 Sleep spell target settlement.

The MCP layer owns spell-card authority, dice rolling, and atomic persistence.
This module only applies the source-defined hit-point pool to already selected
targets and returns new sheets.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable
from uuid import uuid4

from sagasmith_dnd.character_schema import validate_character_sheet
from sagasmith_dnd.conditions import (
    apply_condition_change,
    apply_effect_conditions,
    reconcile_ended_effect_conditions,
)

SLEEP_SPELL_ID = "dnd5e.content.srd2014.spell.sleep"


def _sheet(actor: dict[str, Any]) -> dict[str, Any]:
    value = actor.get("sheet")
    if not isinstance(value, dict):
        raise ValueError("Sleep target actor must contain a sheet")
    return value


def _hit_points(sheet: dict[str, Any]) -> int:
    return max(0, int(dict(dict(sheet.get("combat") or {}).get("hp") or {}).get("value", 0) or 0))


def _condition_immunities(sheet: dict[str, Any]) -> set[str]:
    return {
        str(item).strip().casefold().replace("-", "_").replace(" ", "_")
        for item in dict(sheet.get("traits") or {}).get("condition_immunities", [])
        if str(item).strip()
    }


def _is_undead(sheet: dict[str, Any]) -> bool:
    species = str(dict(sheet.get("progression") or {}).get("species") or "")
    return re.fullmatch(r"undead(?:\s+\([^()]+\))?", species.strip(), re.IGNORECASE) is not None


def _has_magical_sleep_immunity(sheet: dict[str, Any]) -> bool:
    for feature in dict(sheet.get("content") or {}).get("features", []):
        if not isinstance(feature, dict):
            continue
        trait = dict(dict(feature.get("choices") or {}).get("source_trait") or {})
        if (
            trait.get("kind") == "fey_ancestry"
        ):
            if (
                not isinstance(feature.get("mechanic_refs"), list)
                or "dnd5e.core.save.fey_ancestry" not in feature.get("mechanic_refs", [])
                or trait.get("automatic") is not True
                or trait.get("magical_sleep_immunity") is not True
                or not isinstance(trait.get("source_excerpt"), str)
                or not trait["source_excerpt"].strip()
            ):
                raise ValueError("malformed Fey Ancestry sleep-immunity trait")
            return True
    return False


def resolve_sleep_targets(
    target_actors: Iterable[dict[str, Any]],
    *,
    pool: int,
    source_actor_id: str,
    source_spell_id: str,
    source_rule_refs: Iterable[str] = (),
    ruleset: str = "2014",
) -> dict[str, Any]:
    """Settle 2014 Sleep's HP pool against targets in ascending current HP.

    Targets are copied and never mutated.  The caller supplies the rolled pool
    (5d8 plus any higher-slot dice) and must persist the returned sheets.
    """

    if str(ruleset).strip() != "2014":
        raise ValueError("Sleep settlement requires the 2014 rules")
    if isinstance(pool, bool) or not isinstance(pool, int) or pool < 0:
        raise ValueError("Sleep pool must be a non-negative integer")
    source = str(source_actor_id).strip()
    spell = str(source_spell_id).strip()
    if not source or not spell:
        raise ValueError("Sleep source actor and spell ids are required")
    if spell != SLEEP_SPELL_ID:
        raise ValueError("Sleep settlement requires the exact 2014 Sleep spell id")
    refs = [str(item).strip() for item in source_rule_refs if str(item).strip()]
    ordered = sorted(
        ((str(actor.get("id") or ""), deepcopy(_sheet(actor))) for actor in target_actors),
        key=lambda item: (_hit_points(item[1]), item[0]),
    )
    if any(not actor_id for actor_id, _sheet_value in ordered) or len(
        {item[0] for item in ordered}
    ) != len(ordered):
        raise ValueError("Sleep targets require unique actor ids")
    if any(str(sheet.get("edition") or "2014") != "2014" for _, sheet in ordered):
        raise ValueError("Sleep settlement requires 2014 target sheets")
    remaining = pool
    updated: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for actor_id, original in ordered:
        hp = _hit_points(original)
        reason = ""
        affected = False
        ended_concentration_effect_ids: list[str] = []
        if "unconscious" in set(original.get("conditions") or []):
            reason = "already_unconscious"
        elif _is_undead(original):
            reason = "undead"
        elif "charmed" in _condition_immunities(original):
            reason = "immune_to_charmed"
        elif _has_magical_sleep_immunity(original):
            reason = "immune_to_magical_sleep"
        elif "unconscious" in _condition_immunities(original):
            reason = "immune_to_unconscious"
        elif hp <= remaining:
            effect_id = f"sleep-{uuid4().hex}"
            effect = {
                "id": effect_id,
                "name": "Sleep",
                "kind": "timed_conditions",
                "source": source,
                "source_spell_id": spell,
                "active": True,
                "concentration": False,
                "duration": {"period": "minute", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
                "description": (
                    "Magical slumber; ends when the spell ends, the sleeper takes damage, "
                    "or is shaken awake."
                ),
            }
            value = deepcopy(original)
            value.setdefault("effects", []).append(effect)
            apply_effect_conditions(value, effect)
            # Unconscious creatures fall prone, but prone is not owned by Sleep
            # and therefore remains after the Sleep effect is ended.
            apply_condition_change(value, condition_id="prone", add=True)
            # Import locally to avoid a module import cycle with combat_engine.
            from sagasmith_dnd.combat_engine import end_concentration_for_incapacitating_conditions

            ended_concentration_effect_ids = end_concentration_for_incapacitating_conditions(
                value, ended_reason="unconscious"
            )
            value = validate_character_sheet(value)
            remaining -= hp
            affected = True
        else:
            reason = "insufficient_remaining_hit_points"
        updated[actor_id] = value if affected else original
        results.append(
            {
                "target_id": actor_id,
                "current_hit_points": hp,
                "affected": affected,
                "remaining_pool": remaining,
                "skip_reason": reason,
                "effect_id": effect_id if affected else "",
                "ended_concentration_effect_ids": ended_concentration_effect_ids,
                "source_rule_refs": list(refs),
            }
        )
    return {
        "sheets": updated,
        "targets": results,
        "pool_remaining": remaining,
        "source_rule_refs": refs,
    }


def wake_sleep_effects(sheet: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """End active 2014 Sleep effects and preserve other unconscious sources."""

    normalized_reason = str(reason).strip()
    if not normalized_reason:
        raise ValueError("Sleep wake reason is required")
    value = deepcopy(validate_character_sheet(sheet))
    ended: list[dict[str, Any]] = []
    for effect in value.get("effects", []):
        if effect.get("active") and effect.get("source_spell_id") == SLEEP_SPELL_ID:
            effect["active"] = False
            effect["ended_reason"] = normalized_reason
            ended.append(deepcopy(effect))
    if ended:
        reconcile_ended_effect_conditions(value, ended_effects=ended)
    return {
        "sheet": validate_character_sheet(value),
        "ended_effect_ids": [str(effect.get("id") or "") for effect in ended],
        "ended_reason": normalized_reason,
    }
