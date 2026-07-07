"""Data-driven advancement application for Actor documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


def apply_advancement(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    advancement: dict[str, Any],
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    system = dict(actor.system or {})
    deltas = []
    granted = []
    for step in advancement.get("steps") or []:
        kind = str(step.get("type") or "")
        if kind == "level":
            before = system.get("level", 1)
            system["level"] = int(step.get("value", before))
            deltas.append({"type": kind, "before": before, "after": system["level"]})
        elif kind == "hit_points":
            delta = _apply_hit_points(system, step)
            deltas.append(delta)
        elif kind == "scale_value":
            delta = _apply_scale_value(system, step)
            deltas.append(delta)
        elif kind == "item_grant":
            item = documents.create_item(
                campaign_id=campaign_id,
                system_id=actor.system_id,
                actor_id=actor_id,
                item_type=str(step.get("item_type") or step.get("item", {}).get("type") or "feat"),
                name=str(step.get("name") or step.get("item", {}).get("name") or "Granted Item"),
                source_key=str(step.get("source_key") or step.get("item", {}).get("_id") or ""),
                system=dict(step.get("system") or step.get("item", {}).get("system") or {}),
                effects=list(step.get("effects") or step.get("item", {}).get("effects") or []),
                flags={"dnd5e": {"advancement": step}},
            )
            granted.append(asdict(item))
            deltas.append({"type": kind, "item_id": item.id, "name": item.name})
        else:
            raise ValueError(f"unsupported advancement step: {kind}")
    updated = documents.update_actor(actor_id, system=system)
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        deltas=deltas,
        narration_hints=[f"{actor.name}'s advancement is applied."],
        flags={"dnd5e": {"advancement": advancement}},
    )
    return {
        "actor": asdict(updated),
        "granted_items": granted,
        "deltas": deltas,
        "messages": [asdict(message)],
    }


def _apply_hit_points(system: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    attributes = system.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {"value": 1, "max": 1})
    before = dict(hp)
    increase = int(step.get("increase", 0) or 0)
    hp["max"] = int(hp.get("max", 0) or 0) + increase
    if step.get("heal", True):
        hp["value"] = int(hp.get("value", 0) or 0) + increase
    return {"type": "hit_points", "before": before, "after": dict(hp)}


def _apply_scale_value(system: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    scale = system.setdefault("scale", {})
    namespace = str(step.get("namespace") or "class")
    values = scale.setdefault(namespace, {})
    key = str(step.get("key") or "")
    if not key:
        raise ValueError("scale_value step requires key")
    before = values.get(key)
    values[key] = step.get("value")
    return {
        "type": "scale_value",
        "namespace": namespace,
        "key": key,
        "before": before,
        "after": values[key],
    }
