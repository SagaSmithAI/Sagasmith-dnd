"""Small structured ruleset registry for D&D 5e runtime automation."""

from __future__ import annotations

from copy import deepcopy
import json
from importlib import resources
from typing import Any


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
    errors: list[str] = []
    for action_id, action in ruleset.get("activities", {}).items():
        if action.get("activation") not in {
            "action",
            "bonus",
            "reaction",
            "special",
            "minute",
            "hour",
            "short_rest",
            "long_rest",
            "encounter",
            "turn_start",
            "turn_end",
        }:
            errors.append(f"{action_id}: invalid activation")
        if not action.get("type"):
            errors.append(f"{action_id}: missing type")
    return {
        "id": ruleset["id"],
        "valid": not errors,
        "errors": errors,
        "activities": sorted(ruleset.get("activities", {}).keys()),
        "activityActivationTypes": sorted(ruleset.get("activityActivationTypes", {}).keys()),
        "limitedUsePeriods": sorted(ruleset.get("limitedUsePeriods", {}).keys()),
    }
