"""Scene/token/region rule helpers for D&D map runtime."""

from __future__ import annotations

from dataclasses import asdict
from math import sqrt
from typing import Any
from uuid import uuid4

from sagasmith_core import FoundryDocumentService, MapService


def move_token_with_movement_cost(
    maps: MapService,
    *,
    documents: FoundryDocumentService | None = None,
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
    previous = [region for region in regions if _contains(region.shape, before.x, before.y)]
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
    pending = _opportunity_windows(
        maps,
        scene_id=scene.id,
        moved_token=before,
        from_x=before.x,
        from_y=before.y,
        to_x=moved.x,
        to_y=moved.y,
        grid_distance=grid_distance,
        grid_size=scene.grid_size,
        disengaged=bool((metadata or {}).get("disengage") or (metadata or {}).get("disengaged")),
    )
    region_effects = _apply_region_effects(
        documents,
        campaign_id=scene.campaign_id,
        token=moved,
        previous=previous,
        entered=entered,
    )
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
            "pending": pending,
            "region_effects": region_effects,
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


def _apply_region_effects(
    documents: FoundryDocumentService | None,
    *,
    campaign_id: str,
    token,
    previous: list[Any],
    entered: list[Any],
) -> dict[str, Any]:
    if documents is None or not token.actor_id:
        return {"created": [], "removed": []}
    previous_ids = {region.id for region in previous}
    entered_ids = {region.id for region in entered}
    created = []
    removed = []
    for region in entered:
        if region.id in previous_ids or not _is_effect_region(region):
            continue
        effect = _region_effect_data(region)
        created.append(
            asdict(
                documents.create_effect(
                    campaign_id=campaign_id,
                    parent_type="region",
                    parent_id=region.id,
                    actor_id=token.actor_id,
                    origin=f"SceneRegion.{region.id}",
                    name=str(effect.get("name") or region.name),
                    duration=dict(region.duration or effect.get("duration") or {}),
                    changes=list(effect.get("changes") or []),
                    statuses=list(effect.get("statuses") or []),
                    flags={"region_id": region.id, **dict(effect.get("flags") or {})},
                )
            )
        )
    for region in previous:
        if region.id in entered_ids or not _is_effect_region(region):
            continue
        if not dict(region.metadata or {}).get("remove_on_exit", True):
            continue
        for effect in documents.list_effects(
            campaign_id,
            actor_id=token.actor_id,
            parent_type="region",
            parent_id=region.id,
        ):
            removed.append(asdict(documents.delete_effect(effect.id)))
    return {"created": created, "removed": removed}


def _is_effect_region(region) -> bool:
    behavior = str(region.behavior or "").lower().replace("-", "_")
    return behavior in {"apply_active_effect", "active_effect", "effect", "hazard"}


def _region_effect_data(region) -> dict[str, Any]:
    metadata = dict(region.metadata or {})
    effect = metadata.get("effect")
    if isinstance(effect, dict):
        return effect
    return {
        "name": metadata.get("effect_name") or region.name,
        "changes": metadata.get("changes") or [],
        "statuses": metadata.get("statuses") or [],
        "duration": metadata.get("duration") or {},
        "flags": metadata.get("flags") or {},
    }


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


def _opportunity_windows(
    maps: MapService,
    *,
    scene_id: str,
    moved_token,
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    grid_distance: int,
    grid_size: int,
    disengaged: bool,
) -> list[dict[str, Any]]:
    if disengaged:
        return []
    result = []
    for token in maps.list_tokens(scene_id):
        if token.id == moved_token.id or token.hidden:
            continue
        if not _hostile(token.disposition, moved_token.disposition):
            continue
        reach = int(token.metadata.get("reach", 5) or 5)
        before_distance = measure_distance(grid_size, token.x, token.y, from_x, from_y) * grid_distance
        after_distance = measure_distance(grid_size, token.x, token.y, to_x, to_y) * grid_distance
        if before_distance <= reach < after_distance:
            result.append(
                {
                    "id": f"reaction-{uuid4().hex}",
                    "type": "reaction_window",
                    "status": "pending",
                    "trigger": "opportunity_attack",
                    "actor_id": token.actor_id,
                    "token_id": token.id,
                    "target_actor_id": moved_token.actor_id,
                    "target_token_id": moved_token.id,
                    "deadline": "before_token_leaves_reach",
                }
            )
    return result


def _hostile(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return False
    return "hostile" in {left, right}
