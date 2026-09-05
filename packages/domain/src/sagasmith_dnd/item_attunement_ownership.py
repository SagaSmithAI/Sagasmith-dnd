"""Reconcile physical-item bonds after an authorized attunement completes."""

from __future__ import annotations

from typing import Any

from .character_schema import validate_character_sheet
from .external_custody import validate_external_inventory_custody


def complete_item_attunement_ownership(
    sheets: dict[str, dict[str, Any]],
    ground_items: list[dict[str, Any]],
    completed: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """End old bonds when another creature finishes attuning to the same item.

    The caller owns rest timing, physical contact and prerequisites. Each named
    item must already be carried and attuned by that actor's completed rest.
    Merely transferring custody must never invoke this transition.
    """
    values = {actor_id: validate_character_sheet(sheet) for actor_id, sheet in sheets.items()}
    for actor_id, item_id in completed.items():
        if actor_id not in values:
            raise ValueError("completed attunement actor is absent")
        item = next(
            (item for item in values[actor_id]["inventory"]["items"] if item["id"] == item_id),
            None,
        )
        if item is None or item["attunement"] != "attuned":
            raise ValueError("completed attunement requires a carried attuned item")
        for owner_id, sheet in values.items():
            if owner_id == actor_id:
                continue
            for ref in sheet["inventory"]["external_items"]:
                if (
                    ref["location"] == {"kind": "actor", "actor_id": actor_id, "item_id": item_id}
                    and ref["attunement"] == "attuned"
                ):
                    ref["attunement"] = "required"
    validate_external_inventory_custody(values, ground_items)
    return values
