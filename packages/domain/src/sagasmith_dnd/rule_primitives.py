"""Shared domain primitives for core strategies and both extension frontends.

The registry validates resolved instructions. Encounter authority, targeting,
random streams and persistence belong to the caller's Runtime adapter.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .primitive_contracts import ALIASES, PRIMITIVES, SHEET_OPS


def validate_primitive(opcode: str, arguments: dict[str, Any]) -> str:
    opcode = ALIASES.get(opcode, opcode)
    if opcode not in PRIMITIVES:
        raise ValueError(f"unsupported rule primitive: {opcode}")
    if not isinstance(arguments, dict):
        raise ValueError("primitive arguments must be an object")
    for field in ("amount", "value"):
        if field in arguments and opcode in SHEET_OPS | {"damage.apply", "modifier.add"}:
            value = arguments[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{opcode} {field} must be an integer")
            if value < 0 and opcode != "modifier.add":
                raise ValueError(f"{opcode} {field} must be non-negative")
    if opcode.startswith("resource."):
        path = arguments.get("resource_ref", arguments.get("path", ""))
        if not isinstance(path, str) or not path.startswith("resources.") or (
            not path.removeprefix("resources.") or ".." in path
        ):
            raise ValueError("resource operations require a resources.<key> path")
    if opcode.startswith("spell_slot."):
        level = arguments.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 9:
            raise ValueError("spell-slot operations require level 1..9")
    if opcode.startswith(("condition.", "effect.")):
        field = "condition_id" if opcode.startswith("condition.") else "effect_id"
        identifier = arguments.get(field, arguments.get("id"))
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"{opcode} requires id")
        if opcode == "effect.apply":
            effect = arguments.get("effect", {})
            if not isinstance(effect, dict):
                raise ValueError("effect.apply effect must be an object")
            if "id" in effect and effect["id"] != identifier:
                raise ValueError("effect identity must match the instruction id")
    conflict = arguments.get("conflict")
    if conflict is not None:
        if not isinstance(conflict, dict) or not isinstance(conflict.get("key"), str) or (
            not conflict["key"].strip()
        ):
            raise ValueError("conflict declarations require a key")
        if conflict.get("mode") not in {"stack", "replace", "exclusive", "max"}:
            raise ValueError("unknown resolution conflict mode")
    return opcode


def apply_sheet_primitive(sheet: dict[str, Any], opcode: str, arguments: dict[str, Any]):
    """Pure state transition; never mutate the input or silently ignore an opcode."""
    from .conditions import (
        apply_condition_change,
        apply_effect_conditions,
        reconcile_ended_effect_conditions,
    )
    from .hit_points import apply_basic_healing_to_sheet
    from .resources import mutate_bounded_resource

    opcode = validate_primitive(opcode, arguments)
    if opcode not in SHEET_OPS:
        raise ValueError(f"{opcode} requires an encounter primitive adapter")
    value = deepcopy(sheet)
    amount = arguments.get("amount", 1)
    result: dict[str, Any] = {}
    if opcode.startswith(("resource.", "spell_slot.")):
        if opcode.startswith("resource."):
            path = arguments.get("resource_ref", arguments.get("path"))
            key = path.removeprefix("resources.")
            resource = value.get("resources", {}).get(key)
        else:
            slots = value.get("spellcasting", {}).get("spell_slots", {})
            key = str(arguments["level"])
            resource = slots.get(key) or slots.get(f"spell{key}")
        if not isinstance(resource, dict):
            raise ValueError(f"resource does not exist: {key}")
        mutate_bounded_resource(resource, amount=amount,
                                direction="spend" if opcode.endswith("spend") else "recover")
        result["value"] = resource["value"]
    elif opcode == "healing.apply":
        return apply_basic_healing_to_sheet(value, amount=amount)
    elif opcode == "hp.temp.set":
        hp = value.setdefault("combat", {}).setdefault("hp", {})
        hp["temp"] = max(int(hp.get("temp", 0) or 0), arguments.get("value", 0))
    elif opcode in {"condition.apply", "condition.remove"}:
        apply_condition_change(value, condition_id=str(
            arguments.get("condition_id", arguments.get("id", ""))),
            add=opcode == "condition.apply")
    elif opcode == "effect.apply":
        effect = deepcopy(arguments.get("effect") or {})
        effect.setdefault("id", arguments.get("effect_id", arguments.get("id")))
        effect.setdefault("active", True)
        if any(item.get("id") == effect["id"] for item in value.get("effects", [])):
            raise ValueError(f"effect already exists: {effect['id']}")
        value.setdefault("effects", []).append(effect)
        apply_effect_conditions(value, effect)
        result["effect_id"] = effect["id"]
    elif opcode == "effect.remove":
        identifier = arguments.get("effect_id", arguments.get("id"))
        effects = value.get("effects", [])
        effect = next((item for item in effects if item.get("id") == identifier), None)
        if effect is None:
            raise ValueError(f"effect does not exist: {identifier}")
        effects.remove(effect)
        reconcile_ended_effect_conditions(value, ended_effects=[effect])
        result["effect_id"] = identifier
    return {"sheet": value, **result}
