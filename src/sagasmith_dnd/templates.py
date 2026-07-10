"""Activity target template placement as scene regions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core import MapService
from sagasmith_core.foundry_documents import FoundryDocumentService


def place_activity_template(
    documents: FoundryDocumentService,
    maps: MapService,
    *,
    scene_id: str,
    item_id: str,
    activity_id: str,
    x: int,
    y: int,
    name: str | None = None,
    actor_id: str | None = None,
    direction: int = 0,
    duration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scene = maps.get_scene(scene_id)
    item = documents.get_item(item_id)
    activity = documents.get_activity(activity_id)
    if activity.item_id != item_id:
        raise ValueError(f"activity {activity_id} does not belong to item {item_id}")
    if item.actor_id and actor_id and item.actor_id != actor_id:
        raise ValueError(f"item {item_id} is owned by actor {item.actor_id}, not {actor_id}")
    shape = _shape_from_activity(scene, activity.target, x=x, y=y, direction=direction)
    region_triggers = activity.system.get("region_triggers") or []
    if isinstance(region_triggers, dict):
        region_triggers = [region_triggers]
    if not isinstance(region_triggers, list):
        region_triggers = []
    region = maps.create_region(
        scene_id,
        name=name or f"{item.name}: {activity.name}",
        shape=shape,
        behavior="template",
        origin_activity_id=activity.id,
        duration=dict(duration or activity.duration or {}),
        metadata={
            "item_id": item.id,
            "activity_id": activity.id,
            "actor_id": actor_id or item.actor_id,
            "activity_type": activity.activity_type,
            "triggers": region_triggers,
        },
    )
    return {
        "scene": asdict(scene),
        "item": asdict(item),
        "activity": asdict(activity),
        "region": asdict(region),
    }


def _shape_from_activity(
    scene, target: dict[str, Any], *, x: int, y: int, direction: int
) -> dict[str, Any]:
    template = dict(target.get("template") or target or {})
    template_type = str(template.get("type") or "circle").lower()
    grid_distance = int(scene.metadata.get("grid_distance", 5) or 5)
    pixels_per_foot = max(1, int(scene.grid_size)) / max(1, grid_distance)
    size = int(float(template.get("size") or template.get("radius") or template.get("length") or 0))
    width = int(float(template.get("width") or size or 0))
    pixel_size = int(round(size * pixels_per_foot))
    pixel_width = int(round(width * pixels_per_foot))
    if template_type in {"circle", "sphere", "emanation", "radius"}:
        return {
            "type": "circle",
            "x": int(x),
            "y": int(y),
            "radius": pixel_size,
            "units": template.get("units") or "ft",
            "rules_size": size,
        }
    if template_type in {"cone"}:
        return {
            "type": "cone",
            "x": int(x),
            "y": int(y),
            "length": pixel_size,
            "angle": int(template.get("angle") or 53),
            "direction": int(direction),
            "units": template.get("units") or "ft",
            "rules_size": size,
        }
    if template_type in {"line", "ray"}:
        return {
            "type": "line",
            "x": int(x),
            "y": int(y),
            "length": pixel_size,
            "width": pixel_width,
            "direction": int(direction),
            "units": template.get("units") or "ft",
            "rules_size": size,
            "rules_width": width,
        }
    if template_type in {"cube", "square", "rect", "rectangle"}:
        return {
            "type": "rect",
            "x": int(x),
            "y": int(y),
            "width": pixel_size,
            "height": pixel_size,
            "units": template.get("units") or "ft",
            "rules_size": size,
        }
    raise ValueError(f"unsupported template type: {template_type}")
