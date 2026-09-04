"""Pure physical custody transfer; holding an item does not transfer its bond."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from .character_schema import validate_character_sheet
from .external_custody import validate_external_inventory_custody
from .ground_items import validate_ground_items
from .ground_transfer import (
    _clear_dangling_ammunition,
    _clear_missing_ammunition,
    _item_closure,
    _update_external_locations,
    _upsert_external_ref,
)


def _bounded_id(seed: str, used: set[str]) -> str:
    candidate = (
        seed if len(seed) <= 100 else f"{seed[:87]}~{sha256(seed.encode()).hexdigest()[:12]}"
    )
    counter = 2
    while candidate in used:
        digest = sha256(f"{seed}:{counter}".encode()).hexdigest()[:12]
        candidate = f"{seed[:87]}~{digest}"
        counter += 1
    return candidate


def transfer_actor_inventory_item(
    sheets: dict[str, dict[str, Any]],
    ground_items: list[dict[str, Any]],
    source_actor_id: str,
    target_actor_id: str,
    item_id: str,
    quantity: int | None = None,
) -> dict[str, Any]:
    """Move a whole item tree or part of a non-attunable stack without mutation.

    Caller owns authority, revisions and physical reach. Existing historical
    references follow whole items; split-stack references stay on the remainder.
    An owner's bond survives transfer and is restored when that owner retrieves
    the item. New references preserve only required background history or bonds.
    """
    if source_actor_id == target_actor_id:
        raise ValueError("source and target actors must differ")
    if source_actor_id not in sheets or target_actor_id not in sheets:
        raise ValueError("source and target actors must exist")
    values = {actor_id: validate_character_sheet(sheet) for actor_id, sheet in sheets.items()}
    ground = validate_ground_items(ground_items)
    validate_external_inventory_custody(values, ground)
    source, target = values[source_actor_id], values[target_actor_id]
    source_items = source["inventory"]["items"]
    root = next((item for item in source_items if item["id"] == item_id), None)
    if root is None:
        raise LookupError(item_id)
    count = root["quantity"] if quantity is None else quantity
    if type(count) is not int or count < 1 or count > root["quantity"]:
        raise ValueError("quantity must be a positive integer within the item stack")
    full = count == root["quantity"]
    if not full and root["attunement"] != "none":
        raise ValueError("cannot split an attunable item stack")
    moved = _item_closure(source_items, item_id) if full else [deepcopy(root)]
    originals = {item["id"]: deepcopy(item) for item in moved}
    moved_ids = set(originals)
    if full:
        source_items[:] = [item for item in source_items if item["id"] not in moved_ids]
        for slot, equipped in source["inventory"]["equipment_slots"].items():
            if equipped in moved_ids:
                source["inventory"]["equipment_slots"][slot] = None
        _clear_dangling_ammunition(source_items, moved_ids)
    else:
        root["quantity"] -= count
        moved[0]["quantity"] = count

    def returning(ref: dict[str, Any]) -> bool:
        loc = ref["location"]
        return (
            full
            and loc["kind"] == "actor"
            and loc["actor_id"] == source_actor_id
            and loc["item_id"] in moved_ids
        )

    returning_refs = [ref for ref in target["inventory"]["external_items"] if returning(ref)]
    preferred = {ref["location"]["item_id"]: ref["id"] for ref in returning_refs}
    used = {item["id"] for item in target["inventory"]["items"]}
    used.update(ref["id"] for ref in target["inventory"]["external_items"] if not returning(ref))
    id_map = {}
    for original_id in originals:
        id_map[original_id] = _bounded_id(preferred.get(original_id, original_id), used)
        used.add(id_map[original_id])
    owners = {
        original_id: source_actor_id
        for original_id, item in originals.items()
        if item["attunement"] == "attuned"
    }
    if full:
        for owner_id, sheet in values.items():
            for ref in sheet["inventory"]["external_items"]:
                if returning(ref) and ref["attunement"] == "attuned":
                    owners[ref["location"]["item_id"]] = owner_id
    for item in moved:
        original_id = item["id"]
        item["id"] = id_map[original_id]
        item["equipped"] = False
        item["equipped_slot"] = None
        item["container_id"] = None if original_id == item_id else id_map[item["container_id"]]
        if item["kind"] == "weapon":
            item["mechanics"]["ammunition_item_id"] = id_map.get(
                item["mechanics"].get("ammunition_item_id")
            )
        if item["attunement"] != "none":
            item["attunement"] = (
                "attuned" if owners.get(original_id) == target_actor_id else "required"
            )
    target["inventory"]["items"].extend(moved)
    if full:
        target["inventory"]["external_items"] = [
            ref for ref in target["inventory"]["external_items"] if not returning(ref)
        ]
        locations = {
            old: {"kind": "actor", "actor_id": target_actor_id, "item_id": new}
            for old, new in id_map.items()
        }
        _update_external_locations(values, source_actor_id=source_actor_id, locations=locations)
        history_ids = set(
            ((source.get("progression") or {}).get("background_grants") or {}).get(
                "equipment_item_ids"
            )
            or []
        )
        for original_id, item in originals.items():
            if original_id in history_ids or item["attunement"] == "attuned":
                _upsert_external_ref(
                    source["inventory"]["external_items"], item, locations[original_id]
                )
        spell_ids = {
            spec.get("card", {}).get("id")
            for item in originals.values()
            for spec in dict(item.get("mechanics") or {}).get("spellcasting", {}).get("spells", [])
            if isinstance(spec, dict) and isinstance(spec.get("card"), dict)
        }
        for effect in source.get("effects", []):
            if effect.get("source_spell_id") in spell_ids and not effect.get("source"):
                effect["source"] = f"actor:{source_actor_id}"
    _clear_missing_ammunition(target["inventory"]["items"])
    result = {actor_id: validate_character_sheet(sheet) for actor_id, sheet in values.items()}
    validate_external_inventory_custody(result, ground)
    return {
        "sheets": result,
        "ground_items": ground,
        "id_map": id_map,
        "item": deepcopy(
            next(
                item
                for item in result[target_actor_id]["inventory"]["items"]
                if item["id"] == id_map[item_id]
            )
        ),
    }
