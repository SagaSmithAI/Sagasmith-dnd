"""Fail-closed refreshes for source-bound dependent actor sheets.

The importer materializes a dependent statblock into an ordinary actor sheet.  A
later owner change must therefore patch only values proven to have changed in a
second materialization; copying the new sheet wholesale would destroy combat and
inventory state.  The first implementation is deliberately limited to the 2014
Steel Defender contract.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from sagasmith_dnd.character_schema import validate_character_sheet

_MISSING = object()
_RUNTIME_PATHS = {
    ("combat", "hp", "value"),
    ("combat", "hp", "temp"),
    ("combat", "death_saves"),
}
_STEEL_DEFENDER_RELATION_KEY = "steel_defender"
_STEEL_DEFENDER_REVIEWED_EXPRESSION_HASH = (
    "539cc387391b58fce93a7f0268910b66615db8a42006ab1913378222f1216e8c"
)
_STEEL_DEFENDER_BASE_PROFICIENCY_BONUS = 2


def _normalized_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _normalized_activity_name(value: Any) -> str:
    """Normalize an activity label without its parser-added usage suffix."""

    return re.sub(r"\s*\([^()]*\)\s*$", "", _normalized_name(value)).strip()


def _replace_once(value: str, old: str, new: str, *, label: str) -> str:
    if value.count(old) != 1:
        raise ValueError(f"Steel Defender {label} no longer matches its reviewed baseline")
    return value.replace(old, new, 1)


def materialize_dependent_actor_owner_scaling(
    sheet: Mapping[str, Any],
    numeric_parameters: Mapping[str, int],
    *,
    relation_key: str,
    reviewed_expression_hash: str,
) -> dict[str, Any]:
    """Apply the exact 2014 Steel Defender Might of the Master progression.

    The reviewed card is printed at proficiency bonus +2.  Its trait says the
    listed saves, skills, Rend numbers, and Repair healing each increase by one
    whenever the owner's proficiency bonus increases by one.  This function is
    intentionally bound to that exact reviewed template hash and baseline.
    """

    result = validate_character_sheet(deepcopy(dict(sheet)))
    if _normalized_name(relation_key) != _STEEL_DEFENDER_RELATION_KEY:
        return result
    if reviewed_expression_hash != _STEEL_DEFENDER_REVIEWED_EXPRESSION_HASH:
        return result
    raw_pb = numeric_parameters.get("owner_proficiency_bonus")
    if isinstance(raw_pb, bool) or not isinstance(raw_pb, int):
        raise ValueError("Steel Defender owner proficiency bonus is required")
    if raw_pb < _STEEL_DEFENDER_BASE_PROFICIENCY_BONUS or raw_pb > 6:
        raise ValueError("Steel Defender owner proficiency bonus is outside the 2014 range")
    increase = raw_pb - _STEEL_DEFENDER_BASE_PROFICIENCY_BONUS

    baseline_paths = {
        ("abilities", "dexterity", "bonus"): 2,
        ("abilities", "constitution", "bonus"): 2,
        ("skills", "athletics", "bonus"): 2,
        ("skills", "perception", "bonus"): 4,
    }
    for path, baseline in baseline_paths.items():
        if _get_path(result, path) != baseline:
            raise ValueError(f"Steel Defender reviewed baseline diverged at {_path_text(path)}")
        _set_path(result, path, baseline + increase)

    items = list(dict(result.get("inventory") or {}).get("items") or [])
    rend_matches = [
        item for item in items if _normalized_name(item.get("name")) == "force-empowered rend"
    ]
    if len(rend_matches) != 1:
        raise ValueError("Steel Defender owner scaling requires exactly one Force-Empowered Rend")
    rend = rend_matches[0]
    mechanics = dict(rend.get("mechanics") or {})
    if mechanics.get("attack_bonus_override") != 4 or mechanics.get("damage_bonus_override") != 2:
        raise ValueError("Steel Defender Rend no longer matches its reviewed baseline")
    mechanics["attack_bonus_override"] = 4 + increase
    mechanics["damage_bonus_override"] = 2 + increase
    rend["mechanics"] = mechanics
    description = str(rend.get("description") or "")
    description = _replace_once(
        description,
        "+4 to hit",
        f"+{4 + increase} to hit",
        label="Rend attack",
    )
    rend["description"] = _replace_once(
        description,
        "1d8 + 2 force damage",
        f"1d8 + {2 + increase} force damage",
        label="Rend damage",
    )

    activities = list(dict(result.get("content") or {}).get("activities") or [])
    repairs = [
        activity
        for activity in activities
        if _normalized_activity_name(activity.get("name")) == "repair"
    ]
    if len(repairs) != 1:
        raise ValueError("Steel Defender owner scaling requires exactly one Repair activity")
    repair = repairs[0]
    repair["description"] = _replace_once(
        str(repair.get("description") or ""),
        "2d8 + 2 hit points",
        f"2d8 + {2 + increase} hit points",
        label="Repair healing",
    )
    choices = dict(repair.get("choices") or {})
    manual = dict(choices.get("manual_ruling") or {})
    manual["source_excerpt"] = _replace_once(
        str(manual.get("source_excerpt") or ""),
        "2d8 + 2 hit points",
        f"2d8 + {2 + increase} hit points",
        label="Repair ruling excerpt",
    )
    choices["manual_ruling"] = manual
    repair["choices"] = choices
    return validate_character_sheet(result)


def _get_path(value: Any, path: tuple[str | int, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _set_path(value: Any, path: tuple[str | int, ...], replacement: Any) -> None:
    if not path:
        raise ValueError("dependent actor refresh cannot replace the complete sheet")
    current = value
    for part in path[:-1]:
        if isinstance(current, Mapping):
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int):
            current = current[part]
        else:  # pragma: no cover - paths are generated from validated sheets
            raise ValueError("dependent actor refresh encountered an invalid path")
    last = path[-1]
    if isinstance(current, dict):
        current[last] = deepcopy(replacement)
    elif isinstance(current, list) and isinstance(last, int):
        current[last] = deepcopy(replacement)
    else:  # pragma: no cover - paths are generated from validated sheets
        raise ValueError("dependent actor refresh encountered an invalid path")


def _diff_paths(
    before: Any,
    after: Any,
    prefix: tuple[str | int, ...] = (),
) -> set[tuple[str | int, ...]]:
    if isinstance(before, dict) and isinstance(after, dict):
        if set(before) != set(after):
            return {prefix}
        differences: set[tuple[str | int, ...]] = set()
        for key in before:
            differences.update(_diff_paths(before[key], after[key], (*prefix, key)))
        return differences
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return {prefix}
        differences = set()
        for index, (old_item, new_item) in enumerate(zip(before, after, strict=True)):
            differences.update(_diff_paths(old_item, new_item, (*prefix, index)))
        return differences
    return set() if before == after else {prefix}


def _path_text(path: tuple[str | int, ...]) -> str:
    result = ""
    for part in path:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result.lstrip(".")


def _is_runtime_path(path: tuple[str | int, ...]) -> bool:
    return any(path[: len(runtime_path)] == runtime_path for runtime_path in _RUNTIME_PATHS)


def _steel_defender_paths(
    old_sheet: dict[str, Any],
    new_sheet: dict[str, Any],
) -> set[tuple[str | int, ...]]:
    paths: set[tuple[str | int, ...]] = {
        ("combat", "hp", "max"),
        ("abilities", "dexterity", "bonus"),
        ("abilities", "constitution", "bonus"),
        ("skills", "athletics", "bonus"),
        ("skills", "perception", "bonus"),
    }
    old_items = list(dict(old_sheet.get("inventory") or {}).get("items") or [])
    new_items = list(dict(new_sheet.get("inventory") or {}).get("items") or [])
    old_rend = [
        (index, item)
        for index, item in enumerate(old_items)
        if _normalized_name(item.get("name")) == "force-empowered rend"
    ]
    new_rend = [
        (index, item)
        for index, item in enumerate(new_items)
        if _normalized_name(item.get("name")) == "force-empowered rend"
    ]
    if len(old_rend) != 1 or len(new_rend) != 1:
        raise ValueError("Steel Defender refresh requires exactly one Force-Empowered Rend")
    old_index, old_item = old_rend[0]
    new_index, new_item = new_rend[0]
    if old_item.get("id") != new_item.get("id") or old_index != new_index:
        raise ValueError("Steel Defender refresh cannot remap Force-Empowered Rend")
    paths.update(
        {
            ("inventory", "items", old_index, "description"),
            ("inventory", "items", old_index, "mechanics", "attack_bonus_override"),
            ("inventory", "items", old_index, "mechanics", "damage_bonus_override"),
        }
    )
    old_activities = list(dict(old_sheet.get("content") or {}).get("activities") or [])
    new_activities = list(dict(new_sheet.get("content") or {}).get("activities") or [])
    old_repairs = [
        (index, item)
        for index, item in enumerate(old_activities)
        if _normalized_activity_name(item.get("name")) == "repair"
    ]
    new_repairs = [
        (index, item)
        for index, item in enumerate(new_activities)
        if _normalized_activity_name(item.get("name")) == "repair"
    ]
    if len(old_repairs) != 1 or len(new_repairs) != 1:
        raise ValueError("Steel Defender refresh cannot remap the Repair activity")
    old_index, old_activity = old_repairs[0]
    new_index, new_activity = new_repairs[0]
    if old_activity.get("id") != new_activity.get("id") or old_index != new_index:
        raise ValueError("Steel Defender refresh cannot remap the Repair activity")
    paths.update(
        {
            ("content", "activities", old_index, "description"),
            (
                "content",
                "activities",
                old_index,
                "choices",
                "manual_ruling",
                "source_excerpt",
            ),
        }
    )
    return paths


def refresh_dependent_actor_sheet(
    current_sheet: Mapping[str, Any],
    old_authoritative_sheet: Mapping[str, Any],
    new_authoritative_sheet: Mapping[str, Any],
    old_numeric_parameters: Mapping[str, int],
    new_numeric_parameters: Mapping[str, int],
    *,
    relation_key: str = _STEEL_DEFENDER_RELATION_KEY,
) -> dict[str, Any]:
    """Apply one source-bound owner refresh without touching runtime state.

    ``old_authoritative_sheet`` and ``new_authoritative_sheet`` are fresh,
    deterministic materializations of the same template.  Any difference not in
    the Steel Defender whitelist is rejected.  A current field that no longer
    equals the old materialization is also rejected instead of being silently
    overwritten.
    """

    if _normalized_name(relation_key) != _STEEL_DEFENDER_RELATION_KEY:
        raise ValueError("dependent actor refresh supports only the Steel Defender relation")

    current = validate_character_sheet(deepcopy(dict(current_sheet)))
    old_authoritative = validate_character_sheet(deepcopy(dict(old_authoritative_sheet)))
    new_authoritative = validate_character_sheet(deepcopy(dict(new_authoritative_sheet)))
    for label, parameters in (
        ("old", old_numeric_parameters),
        ("new", new_numeric_parameters),
    ):
        if not isinstance(parameters, Mapping) or not parameters:
            raise ValueError(f"{label} dependent actor numeric parameters are required")
        if any(
            not isinstance(name, str) or isinstance(value, bool) or not isinstance(value, int)
            for name, value in parameters.items()
        ):
            raise ValueError(f"{label} dependent actor numeric parameters must be integers")
    if set(old_numeric_parameters) != set(new_numeric_parameters):
        raise ValueError("dependent actor refresh numeric parameter sets must match")
    changed_parameters = sorted(
        name
        for name in old_numeric_parameters
        if old_numeric_parameters[name] != new_numeric_parameters[name]
    )
    if not changed_parameters:
        return {"sheet": current, "changed_paths": [], "changed_parameters": []}

    allowed_paths = _steel_defender_paths(old_authoritative, new_authoritative)
    differences = _diff_paths(old_authoritative, new_authoritative)
    differences = {path for path in differences if not _is_runtime_path(path)}
    unsupported = sorted(
        (_path_text(path) for path in differences if path not in allowed_paths),
        key=str,
    )
    if unsupported:
        raise ValueError(
            "dependent actor refresh found unsupported owner-dependent paths: "
            + ", ".join(unsupported)
        )

    refreshed = deepcopy(current)
    changed_paths: list[str] = []
    for path in sorted(differences, key=_path_text):
        current_value = _get_path(current, path)
        old_value = _get_path(old_authoritative, path)
        new_value = _get_path(new_authoritative, path)
        if current_value is _MISSING or old_value is _MISSING or new_value is _MISSING:
            raise ValueError(f"dependent actor refresh path is missing: {_path_text(path)}")
        if current_value != old_value:
            raise ValueError(
                f"dependent actor current field diverged before refresh: {_path_text(path)}"
            )
        _set_path(refreshed, path, new_value)
        changed_paths.append(_path_text(path))

    # Validation also rejects a lower new maximum that would invalidate the
    # current HP.  In particular, this deliberately does not clamp current HP.
    refreshed = validate_character_sheet(refreshed)
    return {
        "sheet": refreshed,
        "changed_paths": changed_paths,
        "changed_parameters": changed_parameters,
    }


__all__ = ["materialize_dependent_actor_owner_scaling", "refresh_dependent_actor_sheet"]
