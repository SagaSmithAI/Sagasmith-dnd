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


def cover_between_tokens(
    maps: MapService,
    *,
    scene_id: str,
    attacker_token_id: str,
    target_token_id: str,
) -> dict[str, Any]:
    scene = maps.get_scene(scene_id)
    attacker = maps.get_token(attacker_token_id)
    target = maps.get_token(target_token_id)
    if attacker.scene_id != scene.id or target.scene_id != scene.id:
        raise ValueError("both tokens must belong to the requested scene")
    cover_regions = [
        region
        for region in maps.list_regions(scene.id)
        if region.behavior == "cover" and _contains(region.shape, target.x, target.y)
    ]
    best = _best_cover(cover_regions)
    return {
        "scene_id": scene.id,
        "attacker_token_id": attacker.id,
        "target_token_id": target.id,
        "cover": best,
        "regions": [
            {
                "id": region.id,
                "name": region.name,
                "degree": _cover_degree(region),
            }
            for region in cover_regions
        ],
        "targetable": best["degree"] != "total",
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


def _best_cover(regions) -> dict[str, Any]:
    degrees = {"none": 0, "half": 1, "three_quarters": 2, "total": 3}
    best = "none"
    for region in regions:
        degree = _cover_degree(region)
        if degrees.get(degree, 0) > degrees.get(best, 0):
            best = degree
    if best == "half":
        return {"degree": best, "ac_bonus": 2, "dex_save_bonus": 2}
    if best == "three_quarters":
        return {"degree": best, "ac_bonus": 5, "dex_save_bonus": 5}
    if best == "total":
        return {"degree": best, "ac_bonus": None, "dex_save_bonus": None}
    return {"degree": "none", "ac_bonus": 0, "dex_save_bonus": 0}


def _cover_degree(region) -> str:
    metadata = dict(region.metadata or {})
    value = str(metadata.get("degree") or metadata.get("cover") or "half")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"three_quarter", "three_quarters", "3_4", "threequarters"}:
        return "three_quarters"
    if normalized in {"total", "full"}:
        return "total"
    return "half"
