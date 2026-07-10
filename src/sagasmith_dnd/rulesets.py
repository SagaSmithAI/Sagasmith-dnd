"""Small structured ruleset registry for D&D 5e runtime automation."""

from __future__ import annotations

from copy import deepcopy
import json
from importlib import resources
from typing import Any


def ruleset_schema() -> dict[str, Any]:
    root = resources.files("sagasmith_dnd").joinpath("data", "schemas")
    return json.loads(root.joinpath("ruleset.schema.json").read_text(encoding="utf-8"))


def _load_structured_rulesets() -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    try:
        root = resources.files("sagasmith_dnd").joinpath("data", "rulesets")
        for file in root.iterdir():
            if file.suffix != ".json":
                continue
            value = json.loads(file.read_text(encoding="utf-8"))
            loaded[str(value["id"])] = value
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    return loaded


def _rulesets() -> dict[str, dict[str, Any]]:
    return _load_structured_rulesets()


def list_rulesets() -> list[dict[str, str]]:
    return [
        {"id": value["id"], "name": value["name"]}
        for value in sorted(_rulesets().values(), key=lambda item: item["id"])
    ]


def get_ruleset(ruleset_id: str | None = None) -> dict[str, Any]:
    lookup = "dnd5e-2014" if ruleset_id in {None, "", "2014"} else str(ruleset_id)
    if lookup == "2024":
        lookup = "dnd5e-2024"
    values = _rulesets()
    if lookup in values:
        return deepcopy(values[lookup])
    raise LookupError(f"ruleset not found: {ruleset_id}")


def validate_ruleset(ruleset_id: str | None = None) -> dict[str, Any]:
    ruleset = get_ruleset(ruleset_id)
    schema = ruleset_schema()
    errors = _schema_errors(ruleset, schema)
    activation_types = set(ruleset.get("activityActivationTypes", {}))
    activity_types = set(ruleset.get("activityTypes", {}))
    limited_use_periods = set(ruleset.get("limitedUsePeriods", {}))
    period_values = {
        value.get("period")
        for value in ruleset.get("limitedUsePeriods", {}).values()
        if value.get("period")
    }
    period_values.update(ruleset.get("durationPeriods", {}).keys())
    ability_types = set(ruleset.get("abilities", {}))
    condition_types = set(ruleset.get("conditionTypes", {}))
    resource_types = set(ruleset.get("resources", {}))
    for skill_id, skill in ruleset.get("skills", {}).items():
        ability = str(skill.get("ability") or "")
        if ability not in ability_types:
            errors.append(f"skills.{skill_id}.ability: unknown ability {ability!r}")
    reaction_refresh = str((ruleset.get("actionEconomy", {}).get("reactionRefresh") or ""))
    if reaction_refresh and reaction_refresh not in period_values:
        errors.append(f"actionEconomy.reactionRefresh: unknown period {reaction_refresh!r}")
    for resource_id, resource in ruleset.get("resources", {}).items():
        for period in resource.get("recovery") or []:
            if period not in period_values:
                errors.append(f"resources.{resource_id}.recovery: unknown period {period!r}")
    for effect_id, effect in ruleset.get("effects", {}).items():
        period = effect.get("period")
        if period and period not in period_values:
            errors.append(f"effects.{effect_id}.period: unknown period {period!r}")
    for action_id, action in ruleset.get("activities", {}).items():
        activation = str(action.get("activation") or "")
        activity_type = str(action.get("type") or "")
        if activation not in activation_types:
            errors.append(f"activities.{action_id}.activation: unknown activation {activation!r}")
        if activity_type not in activity_types:
            errors.append(f"activities.{action_id}.type: unknown activity type {activity_type!r}")
        resource = (action.get("uses") or {}).get("resource")
        if resource and resource not in resource_types:
            errors.append(f"activities.{action_id}.uses.resource: unknown resource {resource!r}")
        for period in (action.get("uses") or {}).get("recovery") or []:
            if period not in limited_use_periods and period not in period_values:
                errors.append(f"activities.{action_id}.uses.recovery: unknown period {period!r}")
    for condition_id, condition in ruleset.get("conditionTypes", {}).items():
        for status in condition.get("statuses") or []:
            if status not in condition_types:
                errors.append(f"conditionTypes.{condition_id}.statuses: unknown condition {status!r}")
        for rider in condition.get("riders") or []:
            if rider not in condition_types:
                errors.append(f"conditionTypes.{condition_id}.riders: unknown condition {rider!r}")
    for effect_id, conditions in ruleset.get("conditionEffects", {}).items():
        for condition in conditions:
            if condition not in condition_types:
                errors.append(f"conditionEffects.{effect_id}: unknown condition {condition!r}")
    return {
        "id": ruleset["id"],
        "schema": schema["$id"],
        "valid": not errors,
        "errors": errors,
        "activities": sorted(ruleset.get("activities", {}).keys()),
        "activityActivationTypes": sorted(ruleset.get("activityActivationTypes", {}).keys()),
        "limitedUsePeriods": sorted(ruleset.get("limitedUsePeriods", {}).keys()),
    }


def _schema_errors(value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _matches_type(value, expected_type):
        return [f"{path}: expected {_type_label(expected_type)}"]
    if isinstance(value, str) and int(schema.get("minLength", 0) or 0) > len(value):
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, _resolve_schema(item_schema), path=f"{path}[{index}]"))
        return errors
    if not isinstance(value, dict):
        return errors
    for required in schema.get("required") or []:
        if required not in value:
            errors.append(f"{path}.{required}: missing required field")
    properties = schema.get("properties") or {}
    for key, child_schema in properties.items():
        if key in value:
            errors.extend(_schema_errors(value[key], _resolve_schema(child_schema), path=f"{path}.{key}"))
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        known = set(properties)
        for key, child in value.items():
            if key not in known:
                errors.extend(_schema_errors(child, _resolve_schema(additional), path=f"{path}.{key}"))
    return errors


def _resolve_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    root = ruleset_schema()
    ref = str(schema["$ref"])
    if not ref.startswith("#/$defs/"):
        return {}
    return dict(root.get("$defs", {}).get(ref.removeprefix("#/$defs/"), {}))


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    expected_types = [expected] if isinstance(expected, str) else list(expected)
    for item in expected_types:
        if item == "object" and isinstance(value, dict):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, int | float) and not isinstance(value, bool):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "null" and value is None:
            return True
    return False


def _type_label(expected: str | list[str]) -> str:
    if isinstance(expected, str):
        return expected
    return " or ".join(expected)
