"""Validated built-in consumable mechanics shared by CLI and MCP runtimes."""

from __future__ import annotations

from typing import Any

HEALING_POTION_MECHANIC_ID = "dnd5e.core.item.healing_potion"


def healing_potion_formula(item: dict[str, Any], *, edition: str) -> str:
    """Return the core healing expression for an identified standard potion."""

    normalized_edition = str(edition).strip()
    if normalized_edition not in {"2014", "2024"}:
        raise ValueError("healing potion use requires edition 2014 or 2024")
    if not isinstance(item, dict):
        raise ValueError("healing potion item must be an object")
    if str(item.get("kind") or "") != "consumable":
        raise ValueError("healing potion item must have kind consumable")
    if str(item.get("name") or "").strip().casefold() != "potion of healing":
        raise ValueError("item is not a standard Potion of Healing")
    if item.get("identified", True) is not True:
        raise ValueError("an unidentified potion cannot use the healing potion mechanic")
    return "2d4+2"
