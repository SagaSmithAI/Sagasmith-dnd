"""Strict, detached ground-item records for scene and encounter state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.character_schema import default_character_sheet, validate_inventory

_RECORD_KEYS = {
    "id",
    "source_actor_id",
    "scene_id",
    "encounter_id",
    "campaign_revision",
    "location",
    "root_item_id",
    "items",
}


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError(f"{field} must be a non-empty string of at most 100 characters")
    return value.strip()


def _location(value: Any, *, source_actor_id: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"mode", "position"}
        and set(value)
        != {
            "mode",
            "anchor_actor_id",
        }
    ):
        raise ValueError("ground item location has unsupported fields")
    mode = value.get("mode")
    if mode == "grid":
        if set(value) != {"mode", "position"} or not isinstance(value["position"], dict):
            raise ValueError("grid location requires only an integer position")
        position = value["position"]
        if set(position) != {"x", "y"} or any(
            isinstance(position[key], bool) or not isinstance(position[key], int)
            for key in ("x", "y")
        ):
            raise ValueError("grid location requires strict integer x and y")
        return {"mode": "grid", "position": {"x": position["x"], "y": position["y"]}}
    if mode == "agent":
        if set(value) != {"mode", "anchor_actor_id"}:
            raise ValueError("agent location requires only an anchor actor")
        anchor = _text(value.get("anchor_actor_id"), "location.anchor_actor_id")
        if anchor != source_actor_id:
            raise ValueError("agent location anchor must be the source actor")
        return {"mode": "agent", "anchor_actor_id": anchor}
    raise ValueError("ground item location mode must be grid or agent")


def _normalized_items(raw_items: Any, root_item_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("ground item record requires non-empty items")
    for index, item in enumerate(raw_items):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"].strip()
            or len(item["id"]) > 100
        ):
            raise ValueError(f"ground_items.items[{index}].id must be an explicit string id")
    inventory = deepcopy(default_character_sheet()["inventory"])
    inventory["items"] = deepcopy(raw_items)
    inventory["equipment_slots"] = {slot: None for slot in inventory["equipment_slots"]}
    normalized = validate_inventory(inventory)["items"]
    by_id = {item["id"]: item for item in normalized}
    if root_item_id not in by_id:
        raise ValueError("ground item root_item_id must reference an item")
    if by_id[root_item_id]["container_id"] is not None:
        raise ValueError("ground item root must not be contained")
    if any(item["equipped"] or item["equipped_slot"] is not None for item in normalized):
        raise ValueError("ground items cannot contain equipped items")
    for item in normalized:
        if item["id"] == root_item_id:
            continue
        seen = {item["id"]}
        current = item
        while current["container_id"] is not None:
            parent_id = current["container_id"]
            if parent_id in seen or parent_id not in by_id:
                raise ValueError("ground item containers must form a closed tree")
            seen.add(parent_id)
            current = by_id[parent_id]
        if current["id"] != root_item_id:
            raise ValueError("every ground item must descend from root_item_id")
    return normalized


def validate_ground_items(value: Any) -> list[dict[str, Any]]:
    """Normalize detached ground-item records without generating identities."""

    if not isinstance(value, list):
        raise ValueError("ground items must be an array")
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_physical_items: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        field = f"ground_items[{index}]"
        if not isinstance(raw, dict) or set(raw) != _RECORD_KEYS:
            raise ValueError(f"{field} has unsupported or missing fields")
        record_id = _text(raw["id"], f"{field}.id")
        assert record_id is not None
        if record_id in seen_ids:
            raise ValueError("ground items contain duplicate record ids")
        seen_ids.add(record_id)
        source_actor_id = _text(raw["source_actor_id"], f"{field}.source_actor_id")
        assert source_actor_id is not None
        scene_id = _text(raw["scene_id"], f"{field}.scene_id", nullable=True)
        encounter_id = _text(raw["encounter_id"], f"{field}.encounter_id", nullable=True)
        revision = raw["campaign_revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError(f"{field}.campaign_revision must be a non-negative integer")
        root_item_id = _text(raw["root_item_id"], f"{field}.root_item_id")
        assert root_item_id is not None
        normalized_items = _normalized_items(raw["items"], root_item_id)
        physical_items = {(source_actor_id, item["id"]) for item in normalized_items}
        if seen_physical_items.intersection(physical_items):
            raise ValueError("ground items contain duplicate physical item ids")
        seen_physical_items.update(physical_items)
        records.append(
            {
                "id": record_id,
                "source_actor_id": source_actor_id,
                "scene_id": scene_id,
                "encounter_id": encounter_id,
                "campaign_revision": revision,
                "location": _location(raw["location"], source_actor_id=source_actor_id),
                "root_item_id": root_item_id,
                "items": normalized_items,
            }
        )
    return records


__all__ = ["validate_ground_items"]
