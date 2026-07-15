"""Deterministic temporary battle-map compilation from reviewed scene evidence."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any
from uuid import uuid4


class BattleMapError(ValueError):
    pass


def compile_battle_map(
    scene: dict[str, Any], request: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a frozen encounter-local map; never infer walls or line of sight."""
    request = deepcopy(request or {})
    spatial = dict(scene.get("spatial") or {})
    locations = [item for item in spatial.get("locations", []) if isinstance(item, dict)]
    requested_key = request.get("location_key")
    location = next((item for item in locations if item.get("key") == requested_key), None)
    if requested_key and location is None:
        raise BattleMapError("battle-map location_key is not in scene spatial evidence")
    location = location or (locations[0] if locations else None)
    dimensions = dict((location or {}).get("dimensions_ft") or {})
    grid = dict(spatial.get("grid") or {"kind": "square", "cell_ft": 5})
    if str(grid.get("kind") or "square") != "square":
        raise BattleMapError("D&D temporary battle maps require a square grid")
    cell_ft = int(request.get("cell_ft") or grid.get("cell_ft") or 5)
    if cell_ft != 5:
        raise BattleMapError("D&D combat resolution requires five-foot grid cells")
    width = int(
        request.get("width_cells") or max(6, int(dimensions.get("width", 0) or 0) // cell_ft) or 12
    )
    height = int(
        request.get("height_cells")
        or max(6, int(dimensions.get("height", 0) or 0) // cell_ft)
        or 12
    )
    if not 1 <= width <= 200 or not 1 <= height <= 200:
        raise BattleMapError("battle-map bounds must be between 1 and 200 cells")
    blocked = _cells(request.get("blocked_cells") or [], width, height, "blocked_cells")
    difficult = _cells(request.get("difficult_cells") or [], width, height, "difficult_cells")
    source = {
        "scene_id": scene["scene_id"],
        "module_id": scene.get("module_id"),
        "location_key": (location or {}).get("key"),
        "scene_spatial_schema": spatial.get("schema_version", 1),
    }
    value = {
        "id": f"battle-map-{uuid4().hex}",
        "schema_version": 1,
        "map_revision": 1,
        "lifecycle": "temporary",
        "source": source,
        "grid": {"kind": "square", "cell_ft": cell_ft},
        "bounds": {"width_cells": width, "height_cells": height},
        "blocked_cells": blocked,
        "difficult_cells": difficult,
        "dm_overrides": bool(request),
        "world_patches": [],
    }
    value["checksum"] = _checksum(value)
    return value


def patch_battle_map(
    battle_map: dict[str, Any], patches: list[dict[str, Any]]
) -> dict[str, Any]:
    """Append reviewed world patches and refresh the immutable map identity.

    Patches document scene-runtime changes. They do not create walls, cover,
    line of sight, terrain costs, or any other mechanic the combat engine has
    not explicitly implemented.
    """
    next_map = deepcopy(battle_map)
    normalized: list[dict[str, Any]] = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise BattleMapError("each map patch must be an object")
        key = patch.get("key")
        if not isinstance(key, str) or not key.strip():
            raise BattleMapError("each map patch needs a non-empty string key")
        normalized.append({"key": key.strip(), "value": deepcopy(patch.get("value"))})
    next_map["world_patches"] = [
        *list(next_map.get("world_patches") or []),
        *normalized,
    ]
    next_map["map_revision"] = int(next_map.get("map_revision") or 1) + 1
    next_map["checksum"] = _checksum(next_map)
    return next_map


def validate_position(battle_map: dict[str, Any], position: dict[str, Any] | None) -> None:
    if position is None:
        return
    if not isinstance(position, dict):
        raise BattleMapError("battle-map positions must be objects")
    x, y = position.get("x"), position.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
    ):
        raise BattleMapError("battle-map positions need integer x and y cells")
    bounds = dict(battle_map.get("bounds") or {})
    if not (
        0 <= x < int(bounds.get("width_cells", 0)) and 0 <= y < int(bounds.get("height_cells", 0))
    ):
        raise BattleMapError("position is outside temporary battle-map bounds")
    key = _cell_key(x, y)
    if key in set(battle_map.get("blocked_cells") or []):
        raise BattleMapError("position is blocked on the temporary battle map")


def _cells(values: list[Any], width: int, height: int, field: str) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            raise BattleMapError(f"{field} entries must be objects")
        x, y = value.get("x"), value.get("y")
        if (
            not isinstance(x, int)
            or not isinstance(y, int)
            or not (0 <= x < width and 0 <= y < height)
        ):
            raise BattleMapError(f"{field} contains an out-of-bounds cell")
        key = _cell_key(x, y)
        if key not in result:
            result.append(key)
    return sorted(result)


def _cell_key(x: int | float, y: int | float) -> str:
    return f"{int(x)},{int(y)}"


def _checksum(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "checksum"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
