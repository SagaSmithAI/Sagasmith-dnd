"""D&D system-data contracts for Foundry-style Actor, Item, and Activity documents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.rolls import SKILL_ABILITIES
from sagasmith_dnd.rulesets import get_ruleset


ACTOR_TYPES = {"character", "npc", "vehicle", "group"}
ITEM_TYPES = {
    "weapon",
    "armor",
    "equipment",
    "consumable",
    "tool",
    "spell",
    "feat",
    "class",
    "race",
    "background",
    "container",
    "loot",
}


def normalize_actor_document(actor_type: str, system: dict[str, Any] | None) -> dict[str, Any]:
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown actor type: {actor_type}")
    value = deepcopy(system or {})
    level = int(value.get("level") or value.get("details", {}).get("level") or 1)
    value.setdefault("details", {})["level"] = level
    if "level" in value:
        value["level"] = level
    abilities = value.setdefault("abilities", {})
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        current = abilities.get(ability, {"value": 10})
        if not isinstance(current, dict):
            current = {"value": int(current or 10)}
        current["value"] = int(current.get("value", 10) or 10)
        abilities[ability] = current
    attributes = value.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {"value": 1, "max": 1})
    if not isinstance(hp, dict):
        hp = {"value": int(hp or 1), "max": int(hp or 1)}
        attributes["hp"] = hp
    hp["value"] = int(hp.get("value", hp.get("max", 1)) or 0)
    hp["max"] = max(1, int(hp.get("max", hp.get("value", 1)) or 1))
    ac = attributes.setdefault("ac", {"value": 10})
    if not isinstance(ac, dict):
        ac = {"value": int(ac or 10)}
        attributes["ac"] = ac
    ac["value"] = int(ac.get("value", 10) or 10)
    movement = attributes.setdefault("movement", {"walk": int(value.get("speed", 30) or 30)})
    if not isinstance(movement, dict):
        movement = {"walk": int(movement or 30)}
        attributes["movement"] = movement
    movement["walk"] = int(movement.get("walk", movement.get("value", 30)) or 30)
    value.setdefault("skills", {})
    for skill, ability in SKILL_ABILITIES.items():
        entry = value["skills"].setdefault(skill, {})
        if not isinstance(entry, dict):
            entry = {"prof": int(entry or 0)}
            value["skills"][skill] = entry
        entry.setdefault("ability", ability)
    traits = value.setdefault("traits", {})
    for key in ("dr", "di", "dv", "ci"):
        traits.setdefault(key, {"value": []})
    value.setdefault("resources", {})
    value.setdefault("spells", {})
    value.setdefault("favorites", [])
    return value


def normalize_item_document(item_type: str, system: dict[str, Any] | None) -> dict[str, Any]:
    if item_type not in ITEM_TYPES:
        raise ValueError(f"unknown item type: {item_type}")
    value = deepcopy(system or {})
    value.setdefault("quantity", 1)
    value["quantity"] = max(0, int(value.get("quantity", 1) or 0))
    value.setdefault("equipped", False)
    value.setdefault("identified", True)
    value.setdefault("attunement", "")
    value.setdefault("attuned", False)
    value.setdefault("uses", {})
    value.setdefault("activities", {})
    if item_type in {"weapon", "armor", "equipment", "tool"}:
        value.setdefault("proficient", True)
    if item_type == "spell":
        value.setdefault("level", 0)
        value["level"] = int(value.get("level", 0) or 0)
        value.setdefault("preparation", {})
    return value


def normalize_activity_document(
    activity_type: str,
    *,
    activation: dict[str, Any] | None = None,
    consumption: dict[str, Any] | None = None,
    duration: dict[str, Any] | None = None,
    effects: list[Any] | None = None,
    range: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    uses: dict[str, Any] | None = None,
    system: dict[str, Any] | None = None,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    ruleset = get_ruleset(ruleset_id)
    if activity_type not in ruleset.get("activityTypes", {}):
        raise ValueError(f"unknown activity type: {activity_type}")
    activation_value = deepcopy(activation or {})
    activation_type = str(activation_value.get("type") or activation_value.get("activation") or "free")
    if activation_type not in ruleset.get("activityActivationTypes", {}):
        raise ValueError(f"unknown activity activation: {activation_type}")
    activation_value["type"] = activation_type
    uses_value = deepcopy(uses or {})
    if "spent" in uses_value:
        uses_value["spent"] = max(0, int(uses_value.get("spent", 0) or 0))
    if "max" in uses_value and uses_value.get("max") not in (None, ""):
        uses_value["max"] = max(0, int(uses_value.get("max", 0) or 0))
    if "cost" in uses_value and uses_value.get("cost") not in (None, ""):
        uses_value["cost"] = max(0, int(uses_value.get("cost", 0) or 0))
    return {
        "activation": activation_value,
        "consumption": deepcopy(consumption or {}),
        "duration": deepcopy(duration or {}),
        "effects": [dict(item) for item in effects or [] if isinstance(item, dict)],
        "range": deepcopy(range or {}),
        "target": deepcopy(target or {}),
        "uses": uses_value,
        "system": deepcopy(system or {}),
    }
