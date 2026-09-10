"""Shared condition identifiers and source-ownership invariants."""

from __future__ import annotations

from typing import Any, Iterable

STANDARD_BINARY_CONDITION_IDS = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
)

# These states either are Incapacitated or imply it under the D&D condition
# definitions. Cards may store only the more specific condition, so callers
# must use the complete set when enforcing action/reaction/concentration loss.
INCAPACITATING_STATE_IDS = frozenset(
    {"dead", "incapacitated", "paralyzed", "petrified", "stunned", "unconscious"}
)
LIVING_INCAPACITATING_STATE_IDS = INCAPACITATING_STATE_IDS - {"dead"}
DEATH_SAVE_SETTLED_CONDITIONS = frozenset({"dead", "stable"})

# 2014 Petrified grants immunity to poison and disease while preserving any
# pre-existing effect in the actor ledger. Keep these identifiers centralized so
# effect application, condition projection, and duration advancement agree.
POISON_EFFECT_KINDS = frozenset({"poison", "poisoned"})
DISEASE_EFFECT_KINDS = frozenset({"disease", "nonmagical_disease"})
POISON_DISEASE_EFFECT_KINDS = POISON_EFFECT_KINDS | DISEASE_EFFECT_KINDS
POISON_DISEASE_CONDITION_IDS = frozenset({"poison", "poisoned", "disease", "diseased"})


def condition_ids(value: Any) -> set[str]:
    """Return canonical condition identifiers from a card or combat projection."""

    values: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else ()
    return {
        str(item).strip().casefold().replace("-", "_").replace(" ", "_")
        for item in values
        if str(item).strip()
    }


def normalized_effect_kind(effect: dict[str, Any]) -> str:
    """Return one effect kind in the same form used by lifecycle clocks."""

    return str(effect.get("kind") or "").strip().casefold().replace("-", "_")


def effect_is_poison_or_disease(effect: dict[str, Any]) -> bool:
    """Return whether an effect carries poison/disease state.

    Timed condition effects that explicitly add ``poisoned`` or ``disease``
    are classified as well, so legacy caller-constructed cards share the same
    petrified boundary as named poison/disease kinds.
    """

    kind = normalized_effect_kind(effect)
    if kind in POISON_DISEASE_EFFECT_KINDS:
        return True
    if kind != "timed_conditions":
        return False
    return bool(effect_condition_additions(effect) & POISON_DISEASE_CONDITION_IDS)


def sheet_is_petrified(sheet: dict[str, Any]) -> bool:
    """Return whether 2014 Petrified immunity is currently active."""

    return str(sheet.get("edition") or "2014").strip() == "2014" and (
        "petrified" in condition_ids(sheet.get("conditions"))
    )


def effect_is_suspended_by_petrification(
    sheet: dict[str, Any], effect: dict[str, Any]
) -> bool:
    """Return whether one active poison/disease effect is mechanically paused."""

    return (
        bool(effect.get("active", False))
        and sheet_is_petrified(sheet)
        and effect_is_poison_or_disease(effect)
    )


def active_effect_condition_additions(sheet: dict[str, Any]) -> set[str]:
    """Return conditions still owned by active effects on the actor card."""

    result: set[str] = set()
    for effect in sheet.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active", False):
            continue
        result.update(effect_condition_additions(effect))
    return result


def effect_condition_additions(effect: dict[str, Any]) -> set[str]:
    """Return canonical conditions explicitly granted by one timed effect."""

    if normalized_effect_kind(effect) not in {
        "timed_conditions",
        "turn_undead",
        *POISON_DISEASE_EFFECT_KINDS,
    }:
        return set()
    result: set[str] = set()
    for change in effect.get("changes", []):
        if (
            not isinstance(change, dict)
            or change.get("path") != "conditions"
            or change.get("mode") != "add"
        ):
            continue
        raw = change.get("value")
        result.update(condition_ids(raw if isinstance(raw, list) else [raw]))
    return result


def apply_effect_conditions(sheet: dict[str, Any], effect: dict[str, Any]) -> None:
    """Project a newly active effect's one-time and condition changes."""

    if not effect.get("active", True):
        return
    if effect_is_suspended_by_petrification(sheet, effect):
        raise ValueError("poison and disease effects are blocked while the creature is petrified")
    hp = dict(sheet.setdefault("combat", {}).setdefault("hp", {}))
    for change in effect.get("changes", []):
        if (
            not isinstance(change, dict)
            or change.get("path") != "combat.hp.current_multiplier_on_apply"
        ):
            continue
        multiplier = change.get("value")
        if (
            change.get("mode") != "multiply"
            or isinstance(multiplier, bool)
            or not isinstance(multiplier, int)
            or multiplier < 1
        ):
            raise ValueError("current hit-point multiplier effect is malformed")
        maximum = _active_effect_hit_point_maximum(sheet)
        hp["value"] = min(
            maximum,
            max(0, int(hp.get("value", 0) or 0)) * multiplier,
        )
    sheet["combat"]["hp"] = hp
    for condition_id in effect_condition_additions(effect):
        apply_condition_change(sheet, condition_id=condition_id, add=True)


def reconcile_ended_effect_conditions(
    sheet: dict[str, Any],
    *,
    ended_effects: Iterable[dict[str, Any]],
) -> None:
    """Reconcile one-time end transitions and effect-owned conditions."""

    ended = [effect for effect in ended_effects if isinstance(effect, dict)]
    if any(_effect_converts_excess_hit_points(effect) for effect in ended):
        hp = dict(sheet.setdefault("combat", {}).setdefault("hp", {}))
        maximum = _active_effect_hit_point_maximum(sheet)
        current = max(0, int(hp.get("value", 0) or 0))
        excess = max(0, current - maximum)
        hp["value"] = min(current, maximum)
        if excess:
            hp["temp"] = max(int(hp.get("temp", 0) or 0), excess)
        sheet["combat"]["hp"] = hp
    removable: set[str] = set()
    for effect in ended:
        removable.update(effect_condition_additions(effect))
    active_additions = active_effect_condition_additions(sheet)
    removable -= active_additions

    if (
        any(effect.get("kind") == "turn_undead" for effect in ended)
        and not any(
            isinstance(effect, dict)
            and effect.get("active")
            and effect.get("kind") == "turn_undead"
            for effect in sheet.get("effects", [])
        )
        and "turned" not in active_additions
    ):
        removable.add("turned")

    ended_invisibility = any(
        str(effect.get("source_spell_id") or "").strip().casefold().rsplit(".", 1)[-1]
        == "invisibility"
        for effect in ended
    )
    active_invisibility = any(
        isinstance(effect, dict)
        and effect.get("active")
        and str(effect.get("source_spell_id") or "").strip().casefold().rsplit(".", 1)[-1]
        == "invisibility"
        for effect in sheet.get("effects", [])
    )
    if ended_invisibility and not active_invisibility and "invisible" not in active_additions:
        removable.add("invisible")

    if removable:
        sheet["conditions"] = sorted(condition_ids(sheet.get("conditions")) - removable)
    _restore_petrified_suspended_conditions(sheet)


def _effect_converts_excess_hit_points(effect: dict[str, Any]) -> bool:
    return any(
        isinstance(change, dict)
        and change.get("path") == "combat.hp.excess_on_end"
        and change.get("mode") == "set"
        and change.get("value") == "temporary_hit_points"
        for change in effect.get("changes", [])
    )


def _active_effect_hit_point_maximum(sheet: dict[str, Any]) -> int:
    hp = dict(dict(sheet.get("combat") or {}).get("hp") or {})
    maximum = max(0, int(hp.get("max", 0) or 0))
    for effect in sheet.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active", False):
            continue
        if effect_is_suspended_by_petrification(sheet, effect):
            continue
        for change in effect.get("changes", []):
            if not isinstance(change, dict) or change.get("path") != "combat.hp.maximum_multiplier":
                continue
            multiplier = change.get("value")
            if (
                change.get("mode") != "multiply"
                or isinstance(multiplier, bool)
                or not isinstance(multiplier, int)
                or multiplier < 1
            ):
                raise ValueError("maximum hit-point multiplier effect is malformed")
            maximum *= multiplier
    return maximum


def apply_condition_change(
    sheet: dict[str, Any],
    *,
    condition_id: str,
    add: bool,
) -> None:
    """Apply one direct condition change without defeating immunity or active sources."""

    identifiers = condition_ids([condition_id])
    if not identifiers:
        raise ValueError("condition id is required")
    normalized = identifiers.pop()
    conditions = condition_ids(sheet.get("conditions"))
    if add:
        immunities = condition_ids(dict(sheet.get("traits") or {}).get("condition_immunities"))
        if (
            normalized not in immunities
            and not (sheet_is_petrified(sheet) and normalized in POISON_DISEASE_CONDITION_IDS)
        ):
            conditions.add(normalized)
    elif normalized not in active_effect_condition_additions(sheet):
        conditions.discard(normalized)
    if normalized == "petrified" and add:
        conditions.difference_update(_petrified_suspended_condition_ids(sheet))
    sheet["conditions"] = sorted(conditions)
    if normalized == "petrified" and not add:
        _restore_petrified_suspended_conditions(sheet)


def _petrified_suspended_condition_ids(sheet: dict[str, Any]) -> set[str]:
    """Return poison/disease conditions owned by effects that remain active."""

    result: set[str] = set()
    for effect in sheet.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active", False):
            continue
        if not effect_is_poison_or_disease(effect):
            continue
        result.update(effect_condition_additions(effect) & POISON_DISEASE_CONDITION_IDS)
    return result


def _restore_petrified_suspended_conditions(sheet: dict[str, Any]) -> None:
    """Re-project surviving poison/disease conditions after Petrified ends."""

    if sheet_is_petrified(sheet):
        return
    immunities = condition_ids(dict(sheet.get("traits") or {}).get("condition_immunities"))
    conditions = condition_ids(sheet.get("conditions"))
    conditions.update(_petrified_suspended_condition_ids(sheet) - immunities)
    sheet["conditions"] = sorted(conditions)


def reconcile_condition_projection(
    sheet: dict[str, Any],
    desired: Iterable[Any],
) -> set[str]:
    """Move a card toward one desired condition set through shared invariants.

    Rule-specific code may decide the desired result, but it must not bypass
    condition immunity or remove a condition that another active effect still
    owns. The returned set is the actual card projection after those checks.
    """

    target = condition_ids(desired)
    current = condition_ids(sheet.get("conditions"))
    for condition_id in sorted(current - target):
        apply_condition_change(sheet, condition_id=condition_id, add=False)
    for condition_id in sorted(target - current):
        apply_condition_change(sheet, condition_id=condition_id, add=True)
    return condition_ids(sheet.get("conditions"))
