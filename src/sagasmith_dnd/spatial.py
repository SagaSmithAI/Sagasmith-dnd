"""Scene/token/region rule helpers for D&D map runtime."""

from __future__ import annotations

from dataclasses import asdict
from math import sqrt
from typing import Any

from sagasmith_core import MapService


def move_token_with_movement_cost(
    maps: MapService,
    *,
    token_id: str,
    x: int,
    y: int,
    elevation: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before = maps.get_token(token_id)
    scene = maps.get_scene(before.scene_id)
    regions = maps.list_regions(scene.id)
    distance = measure_distance(scene.grid_size, before.x, before.y, x, y)
    entered = [region for region in regions if _contains(region.shape, x, y)]
    multiplier = 2 if any(region.behavior == "difficult_terrain" for region in entered) else 1
    moved = maps.move_token(
        token_id,
        x=x,
        y=y,
        elevation=elevation,
        metadata=metadata,
    )
    grid_distance = int(scene.metadata.get("grid_distance", 5) or 5)
    return {
        **asdict(moved),
        "movement": {
            "from": {"x": before.x, "y": before.y, "elevation": before.elevation},
            "to": {"x": moved.x, "y": moved.y, "elevation": moved.elevation},
            "grid_size": scene.grid_size,
            "grid_distance": grid_distance,
            "distance": distance * grid_distance,
            "cost": distance * grid_distance * multiplier,
            "cost_multiplier": multiplier,
            "regions": [
                {
                    "id": region.id,
                    "name": region.name,
                    "behavior": region.behavior,
                }
                for region in entered
            ],
        },
    }


def measure_distance(grid_size: int, x1: int, y1: int, x2: int, y2: int) -> float:
    grid = max(1, int(grid_size or 1))
    dx = (x2 - x1) / grid
    dy = (y2 - y1) / grid
    return round(sqrt((dx * dx) + (dy * dy)), 3)


def _contains(shape: dict[str, Any], x: int, y: int) -> bool:
    shape_type = str(shape.get("type") or "").lower()
    if shape_type == "circle":
        cx = int(shape.get("x", 0) or 0)
        cy = int(shape.get("y", 0) or 0)
        radius = int(shape.get("radius", 0) or 0)
        return sqrt(((x - cx) ** 2) + ((y - cy) ** 2)) <= radius
    if shape_type in {"rect", "rectangle"}:
        left = int(shape.get("x", 0) or 0)
        top = int(shape.get("y", 0) or 0)
        width = int(shape.get("width", 0) or 0)
        height = int(shape.get("height", 0) or 0)
        return left <= x <= left + width and top <= y <= top + height
    return False
