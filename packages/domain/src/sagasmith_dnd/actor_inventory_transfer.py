"""Deterministic physical inventory transfer between two actors.

Authority, action cost, reachability, revisions, and replay belong to callers.
This module only transforms already-authorized normalized character sheets.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from .character_schema import validate_character_sheet
from .external_custody import validate_external_inventory_custody
from .ground_transfer import _clear_dangling_ammunition, _item_closure


def _bounded_id(seed: str, used: set[str]) -> str:
    candidate = seed
    counter = 2
    while candidate in used:
        digest = sha256(f"{seed}:{counter}".encode()).hexdigest()[:12]
        candidate = f"{seed[:86]}~{digest}"
        counter += 1
    if len(candidate) > 100:
        candidate = candidate[:87] + "~" + sha256(seed.encode()).hexdigest()[:12]
    return candidate


def _refs(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    return sheet["inventory"]["external_items"]


def _location(ref: dict[str, Any]) -> dict[str, Any]:
    return dict(ref.get("location") or {})


def transfer_actor_inventory_item(
    sheets: dict[str, dict[str, Any]],
    ground_items: list[dict[str, Any]],
    source_actor_id: str,
    target_actor_id: str,
    item_id: str,
    quantity: int | None = None,
) -> dict[str, Any]:
    """Move one item (and, when whole, its container descendants) to an actor.

    An attuned item's original owner's external attuned reference remains the
    authoritative bond after transfer; the recipient receives a ``required``
    item.  Partial attunable stacks are rejected because a bond cannot be
    fabricated for only part of one physical item.
    """

    if source_actor_id == target_actor_id:
        raise ValueError("source and target actors must differ")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item_id must be a non-empty string")
    if source_actor_id not in sheets or target_actor_id not in sheets:
        raise ValueError("source and target actors must exist")
    original_sheets = deepcopy(sheets)
    original_ground = deepcopy(ground_items)
    normalized = {actor: validate_character_sheet(sheet) for actor, sheet in sheets.items()}
    # Validate ground state as part of the same custody boundary, even though
    # this operation does not modify it.
    from .ground_items import validate_ground_items

    records = validate_ground_items(ground_items)
    source = normalized[source_actor_id]
    source_items = source["inventory"]["items"]
    item = next((entry for entry in source_items if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    count = item["quantity"] if quantity is None else quantity
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("quantity must be a positive integer")
    if count > item["quantity"]:
        raise ValueError("quantity exceeds the item stack")
    full = count == item["quantity"]
    attuned_transfer = full and item["attunement"] == "attuned"
    if not full and item["attunement"] != "none":
        raise ValueError("cannot split an attunable item stack")

    next_sheets = deepcopy(normalized)
    next_source = next_sheets[source_actor_id]
    next_target = next_sheets[target_actor_id]
    next_source_items = next_source["inventory"]["items"]
    source_item = next(item for item in next_source_items if item["id"] == item_id)
    if full:
        moved = _item_closure(next_source_items, item_id)
        moved_ids = {entry["id"] for entry in moved}
        if any(
            entry["container_id"] == item_id and entry["id"] not in moved_ids
            for entry in next_source_items
        ):
            raise ValueError("container closure is incomplete")
        next_source_items[:] = [
            entry for entry in next_source_items if entry["id"] not in moved_ids
        ]
        for slot, equipped_id in next_source["inventory"]["equipment_slots"].items():
            if equipped_id in moved_ids:
                next_source["inventory"]["equipment_slots"][slot] = None
        for entry in moved:
            entry["equipped"] = False
            entry["equipped_slot"] = None
        _clear_dangling_ammunition(next_source_items, moved_ids)
    else:
        moved = [deepcopy(source_item)]
        source_item["quantity"] -= count
        moved[0]["quantity"] = count
        moved[0]["id"] = _bounded_id(
            f"{item_id}~transfer", {entry["id"] for entry in next_source_items}
        )
        moved[0]["equipped"] = False
        moved[0]["equipped_slot"] = None

    used = {entry["id"] for entry in next_target["inventory"]["items"]}
    used.update(ref["id"] for ref in _refs(next_target))
    id_map: dict[str, str] = {}
    for entry in moved:
        id_map[entry["id"]] = _bounded_id(entry["id"], used | set(id_map.values()))
    for entry in moved:
        old_id = entry["id"]
        entry["id"] = id_map[old_id]
        if entry["container_id"] is not None:
            entry["container_id"] = id_map.get(entry["container_id"], entry["container_id"])
        if entry["kind"] == "weapon":
            ammo_id = entry["mechanics"].get("ammunition_item_id")
            if ammo_id not in id_map:
                entry["mechanics"]["ammunition_item_id"] = None
    root_new_id = id_map[item_id] if full else id_map[moved[0]["id"]]
    if attuned_transfer:
        for entry in moved:
            if entry["id"] == root_new_id:
                entry["attunement"] = "required"

    next_target["inventory"]["items"].extend(moved)
    # Every external physical reference follows the item, including the
    # original owner's attuned bond. Partial transfers leave the source ref on
    # the remainder and therefore have no reference to rewrite.
    if full:
        for sheet in next_sheets.values():
            for ref in _refs(sheet):
                loc = _location(ref)
                if loc.get("kind") == "actor" and loc.get("actor_id") == source_actor_id:
                    old = loc.get("item_id")
                    if old in id_map:
                        loc["actor_id"] = target_actor_id
                        loc["item_id"] = id_map[old]
                        ref["location"] = loc
                        if ref["attunement"] == "attuned":
                            ref["attunement"] = "attuned"
        # A carried attuned item has no external record while held.  Once it
        # leaves the owner's inventory, retain an explicit bond record there.
        for entry in moved:
            if not attuned_transfer or entry["id"] != root_new_id:
                continue
            if any(
                _location(ref).get("item_id") == entry["id"]
                and ref["attunement"] == "attuned"
                for ref in _refs(next_source)
            ):
                continue
            _refs(next_source).append(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "attunement": "attuned",
                    "location": {
                        "kind": "actor",
                        "actor_id": target_actor_id,
                        "item_id": entry["id"],
                    },
                }
            )

    # Effects remain active; only an ammunition selection pointing to a
    # physical item not transferred is cleared.  Weapon ammunition properties
    # themselves remain intact.
    _clear_dangling_ammunition(next_target["inventory"]["items"], set())
    result_sheets = {actor: validate_character_sheet(sheet) for actor, sheet in next_sheets.items()}
    validate_external_inventory_custody(result_sheets, records)
    if sheets != original_sheets or ground_items != original_ground:
        raise AssertionError("actor transfer mutated its inputs")
    return {
        "sheets": result_sheets,
        "ground_items": records,
        "item": deepcopy(
            next(
                entry
                for entry in result_sheets[target_actor_id]["inventory"]["items"]
                if entry["id"] == root_new_id
            )
        ),
        "id_map": id_map,
    }


__all__ = ["transfer_actor_inventory_item"]
