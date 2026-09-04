"""Fail-closed validation for cross-actor inventory custody references."""

from __future__ import annotations

from typing import Any

from .character_schema import validate_character_sheet
from .ground_items import validate_ground_items


def validate_external_inventory_custody(
    sheets: dict[str, dict[str, Any]], ground_items: list[dict[str, Any]]
) -> None:
    """Validate external item targets and attunement ownership without mutation.

    A ground snapshot's attunement is historical state, not an additional
    owner.  External references are the authoritative owner links while an
    item is detached.  Carried attuned items and attuned external references
    may name only one actor for each physical item.
    """

    if not isinstance(sheets, dict) or not sheets:
        raise ValueError("sheets must be a non-empty actor mapping")
    normalized = {actor_id: validate_character_sheet(sheet) for actor_id, sheet in sheets.items()}
    records = validate_ground_items(ground_items)
    actor_items = {
        (actor_id, item["id"]): item
        for actor_id, sheet in normalized.items()
        for item in sheet["inventory"]["items"]
    }
    ground_item_map = {
        (record["id"], item["id"]): item for record in records for item in record["items"]
    }
    attuned_owners: dict[tuple[str, str, str], set[str]] = {}

    for actor_id, sheet in normalized.items():
        carried = sheet["inventory"]["items"]
        for item in carried:
            if item["attunement"] == "attuned":
                key = ("actor", actor_id, item["id"])
                attuned_owners.setdefault(key, set()).add(actor_id)
        for ref in sheet["inventory"]["external_items"]:
            location = ref["location"]
            if location["kind"] == "actor":
                target_key = (location["actor_id"], location["item_id"])
                target = actor_items.get(target_key)
                physical_key = ("actor", *target_key)
            else:
                target_key = (location["ground_id"], location["item_id"])
                target = ground_item_map.get(target_key)
                physical_key = ("ground", *target_key)
            if target is None:
                raise ValueError(f"external item {ref['id']!r} points to missing physical item")
            if ref["attunement"] == "attuned":
                if target["attunement"] == "none":
                    raise ValueError("non-attunable physical item has an attuned external ref")
                attuned_owners.setdefault(physical_key, set()).add(actor_id)

    for physical_key, owners in attuned_owners.items():
        if len(owners) > 1:
            raise ValueError(f"physical item {physical_key[1:]} has multiple attuned owners")


__all__ = ["validate_external_inventory_custody"]
