"""Pure encounter primitives; callers supply authorized state and recorded rolls."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .combat_engine import CombatEngineError, NeedsRulingError


def weighted_table(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = arguments.get("exclude", [])
    table = [deepcopy(item) for item in arguments["table"] if item["value"] not in excluded]
    if not table:
        raise CombatEngineError("semantic roll table has no entries after exclusions")
    if any(isinstance(item.get("weight"), bool) or not isinstance(item.get("weight"), int)
           or item["weight"] <= 0 for item in table):
        raise CombatEngineError("table weights must be positive integers")
    return table


def select_weighted_value(table: list[dict[str, Any]], rolled: int) -> Any:
    if isinstance(rolled, bool) or not isinstance(rolled, int) or not (
        1 <= rolled <= sum(item["weight"] for item in table)
    ):
        raise CombatEngineError("table roll is outside its recorded range")
    for item in table:
        rolled -= item["weight"]
        if rolled <= 0:
            return deepcopy(item["value"])
    raise AssertionError("validated table roll must select an entry")


def validate_targets(source, targets, arguments, *, spatial_facts=None, positioning_mode="grid"):
    """Validate supplied scene facts; never infer positions from narrative text."""
    source_id = str(arguments["source_actor_id"])
    source_conditions = {str(item).strip().casefold() for item in source.get("conditions", [])}
    if arguments.get("require_visible") and "blinded" in source_conditions:
        raise CombatEngineError("a blinded semantic-plan source cannot validate visible targets")
    source_position = dict(source.get("position") or {})
    maximum_range = arguments.get("maximum_range_ft")
    if (maximum_range is not None and positioning_mode == "grid"
            and set(source_position) != {"x", "y"}):
        raise NeedsRulingError(
            "semantic target range requires the source position",
            missing=(f"semantic_target_position:{source_id}",), ruling_kind="source_or_scene_fact",
        )
    required = {str(item).strip().casefold() for item in arguments.get("require_conditions", [])}
    forbidden = {str(item).strip().casefold() for item in arguments.get("forbid_conditions", [])}
    result = []
    for target_id in arguments["target_ids"]:
        if arguments.get("exclude_self", False) and target_id == source_id:
            raise CombatEngineError("semantic target cannot be the source actor")
        target = targets[target_id]
        facts = (spatial_facts or {}).get(target_id, {})
        conditions = {str(item).strip().casefold() for item in target.get("conditions", [])}
        if not required.issubset(conditions):
            raise CombatEngineError("semantic target lacks a source-required condition")
        if conditions & forbidden:
            raise CombatEngineError("semantic target has a source-forbidden condition")
        visible = None
        if arguments.get("require_visible"):
            visible_to = target.get("visible_to_actor_ids")
            if target.get("hidden", False) or "invisible" in conditions or (
                isinstance(visible_to, list) and source_id not in {str(item) for item in visible_to}
            ):
                raise CombatEngineError("semantic target must be visible to the source")
            visible = facts.get("visible")
            if visible is None and isinstance(visible_to, list):
                visible = source_id in visible_to
            if visible is None:
                raise NeedsRulingError(
                    "semantic target visibility requires an explicit scene fact",
                    missing=(f"target_facts:{target_id}:visible",),
                    ruling_kind="source_or_scene_fact",
                )
            if visible is not True:
                raise CombatEngineError("semantic target must be visible to the source")
        distance = None
        if maximum_range is not None:
            position = dict(target.get("position") or {})
            if positioning_mode == "agent":
                distance = facts.get("distance_ft")
                if distance is None:
                    raise NeedsRulingError(
                        "semantic target range requires an explicit distance fact",
                        missing=(f"target_facts:{target_id}:distance_ft",),
                        ruling_kind="source_or_scene_fact",
                    )
                if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0:
                    raise CombatEngineError("target distance must be a non-negative integer")
            elif set(position) != {"x", "y"}:
                raise NeedsRulingError(
                    "semantic target range requires the target position",
                    missing=(f"semantic_target_position:{target_id}",),
                    ruling_kind="source_or_scene_fact",
                )
            else:
                distance = max(abs(int(source_position[axis]) - int(position[axis]))
                               for axis in ("x", "y")) * 5
            if distance > int(maximum_range):
                raise CombatEngineError("semantic target is outside the source-recorded range")
        result.append({"target_id": target_id, "distance_ft": distance,
                       "visible": visible})
    return {"targets": result}


def update_actor_links(links, opcode, arguments, *, plan_id, step_id):
    value = deepcopy(links)
    identity = {key: str(arguments[key]) for key in
                ("source_actor_id", "target_actor_id", "link_kind")}
    existing = next((item for item in value if all(item.get(k) == v for k, v in identity.items())),
                    None)
    if opcode == "actor.link":
        if existing is not None:
            raise CombatEngineError("semantic actor link already exists")
        result = {**identity, "properties": deepcopy(arguments.get("properties") or {}),
                  "plan_id": plan_id, "step_id": step_id}
        value.append(result)
    elif opcode == "actor.unlink":
        if existing is None:
            raise CombatEngineError("semantic actor link does not exist")
        value.remove(existing)
        result = {**identity, "removed": True}
    else:
        raise CombatEngineError(f"unsupported actor link operation: {opcode}")
    return value, deepcopy(result)


def control_actor(target, arguments):
    value = deepcopy(target)
    mode = str(arguments["mode"])
    if mode == "release":
        value.pop("controlled_by_actor_id", None)
        value.pop("control_mode", None)
    else:
        value["controlled_by_actor_id"] = str(arguments["controller_actor_id"])
        value["control_mode"] = mode
    return value, {"target_id": str(arguments["target_actor_id"]),
                   "controller_actor_id": value.get("controlled_by_actor_id"), "mode": mode}


def update_counter(counters, opcode, arguments):
    value = deepcopy(counters)
    key = str(arguments["key"])
    before = int(value.get(key, 0) or 0)
    if opcode == "world.counter.adjust":
        after = before + int(arguments["amount"])
    elif opcode == "world.counter.set":
        after = int(arguments["value"])
    else:
        raise CombatEngineError(f"unsupported counter operation: {opcode}")
    if "minimum" in arguments:
        after = max(after, int(arguments["minimum"]))
    if "maximum" in arguments:
        after = min(after, int(arguments["maximum"]))
    value[key] = after
    return value, {"key": key, "before": before, "after": after}


def assert_state(arguments):
    subject = arguments["subject"]
    expected = arguments.get("expected")
    operator = str(arguments["operator"])
    if operator == "equals":
        passed = subject == expected
    elif operator == "not_equals":
        passed = subject != expected
    elif operator == "truthy":
        passed = bool(subject)
    elif operator == "falsy":
        passed = not bool(subject)
    elif operator == "contains":
        passed = expected in subject
    else:
        raise CombatEngineError(f"unsupported assertion operator: {operator}")
    if not passed:
        raise CombatEngineError(str(arguments.get("message") or "plan state assertion failed"))
    return {"passed": True}
