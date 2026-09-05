"""Pure inventory/ground transitions used by the unconscious-drop boundary.

This module deliberately has no action, spatial, or authority policy.  Callers
must establish those facts before invoking these deterministic transformations.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from .character_schema import validate_character_sheet
from .ground_items import validate_ground_items
from .held_items import held_item_roots


def _validated_sheets(sheets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(sheets, dict) or not sheets:
        raise ValueError("sheets must be a non-empty actor mapping")
    result: dict[str, dict[str, Any]] = {}
    for actor_id, sheet in sheets.items():
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("sheet actor ids must be non-empty strings")
        result[actor_id] = validate_character_sheet(sheet)
    return result


def _item_closure(items: list[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in items}
    if root_id not in by_id:
        raise ValueError(f"held item {root_id!r} is absent from inventory")
    selected = {root_id}
    changed = True
    while changed:
        changed = False
        for item in items:
            if item["container_id"] in selected and item["id"] not in selected:
                selected.add(item["id"])
                changed = True
    return [deepcopy(item) for item in items if item["id"] in selected]


def _clear_dangling_ammunition(items: list[dict[str, Any]], removed_ids: set[str]) -> None:
    for item in items:
        if item["kind"] != "weapon":
            continue
        mechanics = item["mechanics"]
        if mechanics.get("ammunition_item_id") in removed_ids:
            mechanics["ammunition_item_id"] = None


def _clear_missing_ammunition(items: list[dict[str, Any]]) -> None:
    available = {item["id"] for item in items}
    for item in items:
        if item["kind"] != "weapon":
            continue
        mechanics = item["mechanics"]
        if mechanics.get("ammunition_item_id") not in available:
            mechanics["ammunition_item_id"] = None


def _external_items(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    refs = sheet["inventory"]["external_items"]
    if not isinstance(refs, list):
        raise ValueError("inventory.external_items must be a list")
    return refs


def _update_external_locations(
    sheets: dict[str, dict[str, Any]],
    *,
    source_actor_id: str,
    locations: dict[str, dict[str, Any]],
) -> None:
    for sheet in sheets.values():
        refs = _external_items(sheet)
        for ref in refs:
            old = dict(ref.get("location") or {})
            if (
                old.get("kind") == "actor"
                and old.get("actor_id") == source_actor_id
                and old.get("item_id") in locations
            ):
                ref["location"] = deepcopy(locations[old["item_id"]])


def _external_ref(item: dict[str, Any], location: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "attunement": item["attunement"],
        "location": deepcopy(location),
    }


def _upsert_external_ref(
    refs: list[dict[str, Any]], item: dict[str, Any], location: dict[str, Any]
) -> None:
    replacement = _external_ref(item, location)
    for index, ref in enumerate(refs):
        if ref["id"] == item["id"]:
            refs[index] = replacement
            return
    refs.append(replacement)


def drop_held_items(
    sheets: dict[str, dict[str, Any]],
    ground_items: list[dict[str, Any]],
    actor_id: str,
    *,
    record_ids: dict[str, str],
    scene_id: str | None,
    encounter_id: str | None,
    campaign_revision: int,
    location: dict[str, Any],
) -> dict[str, Any]:
    """Move held roots and their container descendants into ground records.

    Ammunition is not physical child data and is never moved.  If a moved
    weapon points at ammunition left behind, the link is cleared in the moved
    and remaining item sets; the source item metadata otherwise remains intact.
    """

    original_sheets = deepcopy(sheets)
    original_ground = deepcopy(ground_items)
    values = _validated_sheets(sheets)
    records = validate_ground_items(ground_items)
    if actor_id not in values:
        raise ValueError("actor_id is absent from sheets")
    if isinstance(campaign_revision, bool) or not isinstance(campaign_revision, int):
        raise ValueError("campaign_revision must be an integer")
    roots = held_item_roots(values[actor_id])
    if set(record_ids) != set(roots):
        raise ValueError("record_ids must contain exactly the held item roots")
    if len(set(record_ids.values())) != len(record_ids):
        raise ValueError("record_ids must be unique")
    next_sheets = deepcopy(values)
    next_records = deepcopy(records)
    dropped: list[dict[str, Any]] = []
    source_items = next_sheets[actor_id]["inventory"]["items"]
    all_removed: set[str] = set()
    for root_id in roots:
        moved = _item_closure(source_items, root_id)
        moved_ids = {item["id"] for item in moved}
        all_removed.update(moved_ids)
        for item in moved:
            item["equipped"] = False
            item["equipped_slot"] = None
        source_items[:] = [item for item in source_items if item["id"] not in moved_ids]
        for slot, item_id in next_sheets[actor_id]["inventory"]["equipment_slots"].items():
            if item_id in moved_ids:
                next_sheets[actor_id]["inventory"]["equipment_slots"][slot] = None
        _clear_dangling_ammunition(source_items, moved_ids)
        _clear_missing_ammunition(moved)
        record = {
            "id": record_ids[root_id],
            "source_actor_id": actor_id,
            "scene_id": scene_id,
            "encounter_id": encounter_id,
            "campaign_revision": campaign_revision,
            "location": deepcopy(location),
            "root_item_id": root_id,
            "items": moved,
        }
        next_records.append(record)
        dropped.append(deepcopy(record))
    _clear_dangling_ammunition(source_items, all_removed)
    for effect in next_sheets[actor_id].get("effects", []):
        source_spell_id = effect.get("source_spell_id")
        if source_spell_id in {
            spec.get("card", {}).get("id")
            for item in sum((record["items"] for record in dropped), [])
            for spec in dict(item.get("mechanics") or {}).get("spellcasting", {}).get("spells", [])
            if isinstance(spec, dict)
        } and not effect.get("source"):
            effect["source"] = f"actor:{actor_id}"
    for record in dropped:
        refs = _external_items(next_sheets[actor_id])
        for item in record["items"]:
            _upsert_external_ref(
                refs,
                item,
                {"kind": "ground", "ground_id": record["id"], "item_id": item["id"]},
            )
    item_locations = {
        item["id"]: {
            "kind": "ground",
            "ground_id": record["id"],
            "item_id": item["id"],
        }
        for record in dropped
        for item in record["items"]
    }
    _update_external_locations(
        next_sheets,
        source_actor_id=actor_id,
        locations=item_locations,
    )
    result_sheets = {key: validate_character_sheet(value) for key, value in next_sheets.items()}
    result_ground = validate_ground_items(next_records)
    if sheets != original_sheets or ground_items != original_ground:
        raise AssertionError("ground transfer mutated its inputs")
    return {"sheets": result_sheets, "ground_items": result_ground, "dropped": dropped}


def pickup_ground_item(
    sheets: dict[str, dict[str, Any]],
    ground_items: list[dict[str, Any]],
    actor_id: str,
    ground_id: str,
) -> dict[str, Any]:
    """Pick up one detached ground record, preserving source attunement ownership."""

    original_sheets = deepcopy(sheets)
    original_ground = deepcopy(ground_items)
    values = _validated_sheets(sheets)
    records = validate_ground_items(ground_items)
    if actor_id not in values:
        raise ValueError("actor_id is absent from sheets")
    record = next((item for item in records if item["id"] == ground_id), None)
    if record is None:
        raise LookupError(ground_id)
    next_sheets = deepcopy(values)
    target_items = next_sheets[actor_id]["inventory"]["items"]
    existing = {item["id"] for item in target_items}
    existing.update(
        ref["id"]
        for ref in _external_items(values[actor_id])
        if dict(ref.get("location") or {}).get("ground_id") != ground_id
    )
    remap: dict[str, str] = {}
    preferred_ids: dict[str, str] = {}
    for owner_id, sheet in values.items():
        for ref in _external_items(sheet):
            location = dict(ref.get("location") or {})
            if (
                owner_id == actor_id
                and location.get("kind") == "ground"
                and location.get("ground_id") == ground_id
            ):
                preferred_ids[str(location.get("item_id"))] = ref["id"]
    for item in record["items"]:
        candidate = preferred_ids.get(item["id"], item["id"])
        suffix = 2
        while candidate in existing or candidate in remap.values():
            digest = sha256(f"{actor_id}:{candidate}:{suffix}".encode()).hexdigest()[:12]
            candidate = f"{candidate[:86]}~{digest}"
            suffix += 1
        remap[item["id"]] = candidate
    moved = deepcopy(record["items"])
    original_ids = [item["id"] for item in moved]
    attuned_owners: dict[str, tuple[str, str]] = {}
    ground_ref_owners: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for owner_id, sheet in values.items():
        refs = _external_items(sheet)
        for ref in refs:
            location = dict(ref.get("location") or {})
            if location.get("kind") == "ground" and location.get("ground_id") == ground_id:
                item_ref_id = str(location.get("item_id") or "")
                if item_ref_id:
                    ground_ref_owners.setdefault(item_ref_id, []).append((owner_id, deepcopy(ref)))
                    previous = attuned_owners.get(item_ref_id)
                    if previous is None or ref["attunement"] == "attuned":
                        attuned_owners[item_ref_id] = (owner_id, ref["attunement"])
    for old_id, refs in ground_ref_owners.items():
        attuned_owner_ids = {owner_id for owner_id, ref in refs if ref["attunement"] == "attuned"}
        if len(attuned_owner_ids) > 1:
            raise ValueError(f"ground item {old_id!r} has multiple attuned owners")
    for item in moved:
        old_id = item["id"]
        if item["attunement"] == "attuned" and old_id not in ground_ref_owners:
            raise ValueError(f"attuned ground item {old_id!r} has no owner reference")
    for item in moved:
        old_id = item["id"]
        item["id"] = remap[old_id]
        if item["container_id"] is not None:
            item["container_id"] = remap.get(item["container_id"], item["container_id"])
        if item["kind"] == "weapon":
            ammo = item["mechanics"].get("ammunition_item_id")
            if ammo is not None:
                item["mechanics"]["ammunition_item_id"] = remap.get(ammo, ammo)
        attuned_owner, owner_state = attuned_owners.get(old_id, (None, "required"))
        if item["attunement"] == "none" and owner_state == "attuned":
            raise ValueError("a non-attunable ground item cannot have an attuned owner")
        if item["attunement"] != "none" and old_id in attuned_owners:
            item["attunement"] = (
                "attuned" if actor_id == attuned_owner and owner_state == "attuned" else "required"
            )
        target_items.append(item)
    _clear_missing_ammunition(target_items)
    next_records = [item for item in records if item["id"] != ground_id]
    for owner_id, sheet in next_sheets.items():
        refs = _external_items(sheet)
        refs[:] = [
            ref
            for ref in refs
            if not (
                dict(ref.get("location") or {}).get("kind") == "ground"
                and dict(ref.get("location") or {}).get("ground_id") == ground_id
            )
        ]
        for old_id in original_ids:
            for historical_owner, historical_ref in ground_ref_owners.get(old_id, []):
                if historical_owner != owner_id or historical_owner == actor_id:
                    continue
                replacement = deepcopy(historical_ref)
                replacement["location"] = {
                    "kind": "actor",
                    "actor_id": actor_id,
                    "item_id": remap[old_id],
                }
                _upsert_external_ref(refs, replacement, replacement["location"])
    next_sheets[actor_id] = validate_character_sheet(next_sheets[actor_id])
    result_sheets = {key: validate_character_sheet(value) for key, value in next_sheets.items()}
    result_ground = validate_ground_items(next_records)
    if sheets != original_sheets or ground_items != original_ground:
        raise AssertionError("ground transfer mutated its inputs")
    remapped_root = remap[record["root_item_id"]]
    return {
        "sheets": result_sheets,
        "ground_items": result_ground,
        "picked_up": {
            "root_item_id": remapped_root,
            "items": moved,
            "id_map": remap,
        },
    }


__all__ = ["drop_held_items", "pickup_ground_item"]
