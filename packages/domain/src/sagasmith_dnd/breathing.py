"""Source-backed 2014 holding-breath and suffocation settlement.

The runtime stores its one countdown on the actor card.  It is advanced only
by the authoritative round clock; callers must explicitly say whether the
actor can breathe, so this module never guesses from a scene or position.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.character_schema import effective_ability_modifier, validate_character_sheet
from sagasmith_dnd.conditions import condition_ids, reconcile_condition_projection
from sagasmith_dnd.standard_feature_ids import (
    CORE_SUFFOCATION_MECHANIC_ID,
    TORTLE_HOLD_BREATH_ARTIFACT_ID,
    TORTLE_HOLD_BREATH_FEATURE_ID,
    TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
    TORTLE_HOLD_BREATH_LEGACY_PACK_VERSIONS,
    TORTLE_HOLD_BREATH_SOURCE_RULE_REF_PREFIX,
)

BREATHING_EFFECT_ID = "dnd5e.effect.2014.breathing"
_BREATHING_KIND = "2014_breathing_lifecycle"
_DESCRIPTION = "Authoritative 2014 breathing lifecycle; source PHB p. 183."


def tortle_hold_breath_available(sheet: dict[str, Any]) -> bool:
    """Recognize exactly the finalized Tortle provenance, never display text."""

    if sheet.get("edition") != "2014":
        return False
    selections = [
        item
        for item in dict(sheet.get("content") or {}).get("selections", [])
        if isinstance(item, dict)
        and item.get("kind") == "species"
        and item.get("pack_id") == TORTLE_HOLD_BREATH_LEGACY_PACK_ID
        and item.get("pack_version") in TORTLE_HOLD_BREATH_LEGACY_PACK_VERSIONS
        and item.get("artifact_id") == TORTLE_HOLD_BREATH_ARTIFACT_ID
        and item.get("mechanic_refs") == []
        and isinstance(item.get("rule_refs"), list)
        and item["rule_refs"]
        and all(
            isinstance(ref, str) and ref.startswith(TORTLE_HOLD_BREATH_SOURCE_RULE_REF_PREFIX)
            for ref in item["rule_refs"]
        )
    ]
    features = [
        item
        for item in dict(sheet.get("content") or {}).get("features", [])
        if isinstance(item, dict)
        and item.get("id") == TORTLE_HOLD_BREATH_FEATURE_ID
        and item.get("name") == "Hold Breath"
        and item.get("source_key") == "Tortle"
        and item.get("pack_id") == TORTLE_HOLD_BREATH_LEGACY_PACK_ID
        and item.get("pack_version") in TORTLE_HOLD_BREATH_LEGACY_PACK_VERSIONS
        and item.get("mechanic_refs") == []
        and isinstance(item.get("rule_refs"), list)
        and item["rule_refs"]
        and all(
            isinstance(ref, str) and ref.startswith(TORTLE_HOLD_BREATH_SOURCE_RULE_REF_PREFIX)
            for ref in item["rule_refs"]
        )
    ]
    if len(selections) > 1 or len(features) > 1:
        raise ValueError("actor card has duplicate Tortle Hold Breath provenance")
    return (
        len(selections) == 1
        and len(features) == 1
        and selections[0]["pack_version"] == features[0]["pack_version"]
    )


def _effect(sheet: dict[str, Any]) -> dict[str, Any] | None:
    matches = [
        item
        for item in sheet.get("effects", [])
        if isinstance(item, dict) and item.get("id") == BREATHING_EFFECT_ID and item.get("active")
    ]
    if len(matches) > 1:
        raise ValueError("actor card has duplicate breathing lifecycle effects")
    return matches[0] if matches else None


def begin_holding_breath(sheet: dict[str, Any], *, choking: bool = False) -> dict[str, Any]:
    """Start an explicit no-air transition and return its source-backed timer."""

    value = validate_character_sheet(sheet)
    if value["edition"] != "2014":
        raise ValueError("holding breath lifecycle is available only for 2014 actors")
    if _effect(value) is not None:
        raise ValueError("actor is already unable to breathe")
    conditions = condition_ids(value.get("conditions"))
    if "dead" in conditions:
        raise ValueError("a dead actor cannot begin holding breath")
    constitution_modifier = effective_ability_modifier(value, "constitution")
    hold_rounds = (
        600 if tortle_hold_breath_available(value) else max(5, (1 + constitution_modifier) * 10)
    )
    if choking:
        hold_rounds = 0
    effect = {
        "id": BREATHING_EFFECT_ID,
        "name": "Holding Breath",
        "kind": _BREATHING_KIND,
        "source": CORE_SUFFOCATION_MECHANIC_ID,
        "active": True,
        "concentration": False,
        "duration": {"period": "round", "remaining": hold_rounds + max(1, constitution_modifier)},
        "changes": [],
        "description": _DESCRIPTION,
        "metadata": {
            "schema_version": 1,
            "hold_remaining_rounds": hold_rounds,
            "suffocation_remaining_rounds": max(1, constitution_modifier),
            "phase": "suffocating" if choking else "holding_breath",
            "source": "tortle_hold_breath"
            if tortle_hold_breath_available(value)
            else "constitution",
        },
    }
    value["effects"].append(effect)
    if choking:
        reconcile_condition_projection(
            value, condition_ids(value.get("conditions")) | {"suffocating"}
        )
    return {"sheet": validate_character_sheet(value), "effect": deepcopy(effect)}


def restore_breathing(sheet: dict[str, Any]) -> dict[str, Any]:
    """End the authoritative no-air state after an explicit breathable transition."""

    value = validate_character_sheet(sheet)
    effect = _effect(value) or next(
        (
            item
            for item in value.get("effects", [])
            if isinstance(item, dict) and item.get("id") == BREATHING_EFFECT_ID
        ),
        None,
    )
    if effect is None:
        return {"sheet": value, "status": "already_breathing"}
    value["effects"] = [item for item in value["effects"] if item.get("id") != BREATHING_EFFECT_ID]
    reconcile_condition_projection(value, condition_ids(value.get("conditions")) - {"suffocating"})
    return {"sheet": validate_character_sheet(value), "status": "breathing_restored"}


def _valid(effect: dict[str, Any]) -> bool:
    metadata = dict(effect.get("metadata") or {})
    return (
        effect.get("name") in {"Holding Breath", "Suffocating"}
        and effect.get("kind") == _BREATHING_KIND
        and effect.get("source") == CORE_SUFFOCATION_MECHANIC_ID
        and effect.get("active") is True
        and effect.get("concentration") is False
        and effect.get("changes") == []
        and effect.get("description") == _DESCRIPTION
        and dict(effect.get("duration") or {}).get("period") == "round"
        and metadata.get("schema_version") == 1
        and metadata.get("phase") in {"holding_breath", "suffocating"}
        and isinstance(metadata.get("hold_remaining_rounds"), int)
        and isinstance(metadata.get("suffocation_remaining_rounds"), int)
    )


def advance_breathing_rounds(
    sheet: dict[str, Any], *, rounds: int, defer_drop_until_turn_start: bool = False
) -> dict[str, Any]:
    """Settle a canonical elapsed-round interval exactly once for one actor."""

    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("breathing advance rounds must be a positive integer")
    value = deepcopy(sheet)
    effect = _effect(value)
    if effect is None:
        return {"sheet": value, "status": "unchanged"}
    if not _valid(effect):
        raise ValueError("persisted breathing lifecycle effect is malformed")
    metadata = dict(effect["metadata"])
    hold = int(metadata["hold_remaining_rounds"])
    suffocation = int(metadata["suffocation_remaining_rounds"])
    remaining = rounds
    if hold > 0:
        spent = min(hold, remaining)
        hold -= spent
        remaining -= spent
    if hold == 0 and remaining:
        spent = min(suffocation, remaining)
        suffocation -= spent
        remaining -= spent
    metadata["hold_remaining_rounds"] = hold
    metadata["suffocation_remaining_rounds"] = suffocation
    if hold == 0:
        metadata["phase"] = "suffocating"
        effect["name"] = "Suffocating"
        reconcile_condition_projection(
            value, condition_ids(value.get("conditions")) | {"suffocating"}
        )
    effect["metadata"] = metadata
    effect["duration"] = {"period": "round", "remaining": hold + suffocation}
    if hold == 0 and suffocation == 0 and not defer_drop_until_turn_start:
        effect["active"] = False
        effect["ended_reason"] = "suffocation_expired"
        value.setdefault("combat", {}).setdefault("hp", {})["value"] = 0
        conditions = condition_ids(value.get("conditions")) - {"stable"}
        conditions.update({"suffocating", "unconscious"})
        reconcile_condition_projection(value, conditions)
        # Import lazily: the combat engine also consumes the recovery predicate
        # from this module. Use its shared cleanup to remove owned conditions too.
        from sagasmith_dnd.combat_engine import end_concentration_for_incapacitating_conditions

        ended_concentration = end_concentration_for_incapacitating_conditions(value)
        return {
            "sheet": validate_character_sheet(value),
            "status": "dropped_to_zero",
            "effect_id": BREATHING_EFFECT_ID,
            "ended_concentration_effect_ids": ended_concentration,
        }
    return {
        "sheet": validate_character_sheet(value),
        "status": metadata["phase"],
        "effect_id": BREATHING_EFFECT_ID,
    }


def settle_breathing_turn_start(sheet: dict[str, Any]) -> dict[str, Any]:
    """Drop only after the elapsed grace rounds, never spend time on a turn event."""

    effect = _effect(sheet)
    if effect is None:
        return {"sheet": deepcopy(sheet), "status": "unchanged"}
    if not _valid(effect):
        raise ValueError("persisted breathing lifecycle effect is malformed")
    metadata = effect["metadata"]
    if metadata["hold_remaining_rounds"] or metadata["suffocation_remaining_rounds"]:
        return {"sheet": deepcopy(sheet), "status": "unchanged"}
    return advance_breathing_rounds(sheet, rounds=1)


def breathing_blocks_recovery(sheet: dict[str, Any]) -> bool:
    if "suffocating" not in condition_ids(sheet.get("conditions")):
        return False
    # The 2014 rule's recovery restriction begins once the actor drops to
    # 0 HP and is dying; the Constitution-based grace rounds are still a
    # live creature's window to reach air.
    return int(dict(dict(sheet.get("combat") or {}).get("hp") or {}).get("value", 0) or 0) <= 0
