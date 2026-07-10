"""Data-driven advancement application for Actor documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.document_contracts import (
    normalize_actor_document,
    normalize_activity_document,
    normalize_item_document,
)
from sagasmith_dnd.rulesets import get_ruleset


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
            item_type = str(step.get("item_type") or step.get("item", {}).get("type") or "feat")
            item = documents.create_item(
                campaign_id=campaign_id,
                system_id=actor.system_id,
                actor_id=actor_id,
                item_type=item_type,
                name=str(step.get("name") or step.get("item", {}).get("name") or "Granted Item"),
                source_key=str(step.get("source_key") or step.get("item", {}).get("_id") or ""),
                system=normalize_item_document(
                    item_type,
                    dict(step.get("system") or step.get("item", {}).get("system") or {}),
                ),
                effects=list(step.get("effects") or step.get("item", {}).get("effects") or []),
                flags={"dnd5e": {"advancement": step}},
            )
            granted.append(asdict(item))
            deltas.append({"type": kind, "item_id": item.id, "name": item.name})
        else:
            raise ValueError(f"unsupported advancement step: {kind}")
    updated = documents.update_actor(
        actor_id,
        system=normalize_actor_document(actor.actor_type, system),
    )
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


def grant_ruleset_feature(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    feature_id: str,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    ruleset = get_ruleset(ruleset_id)
    normalized = _feature_key(feature_id)
    feature = dict(ruleset.get("classFeatures", {}).get(normalized) or {})
    if not feature:
        raise ValueError(f"unknown ruleset feature: {feature_id}")
    item_type = str(feature.get("item_type") or "feat")
    item = documents.create_item(
        campaign_id=campaign_id,
        system_id=actor.system_id,
        actor_id=actor_id,
        item_type=item_type,
        name=str(feature.get("name") or normalized),
        source_key=normalized,
        system=normalize_item_document(item_type, dict(feature.get("system") or {})),
        effects=list(feature.get("effects") or []),
        flags={"dnd5e": {"ruleset_feature": normalized}},
    )
    activities = []
    for template in feature.get("activities") or []:
        activity_type = str(template.get("type") or "utility")
        contract = normalize_activity_document(
            activity_type,
            activation=dict(template.get("activation") or {}),
            consumption=dict(template.get("consumption") or {}),
            duration=dict(template.get("duration") or {}),
            effects=list(template.get("effects") or []),
            range=dict(template.get("range") or {}),
            target=dict(template.get("target") or {}),
            uses=dict(template.get("uses") or {}),
            system=dict(template.get("system") or {}),
            ruleset_id=ruleset["id"],
        )
        activities.append(
            asdict(
                documents.create_activity(
                    item_id=item.id,
                    activity_type=activity_type,
                    name=str(template.get("name") or feature.get("name") or normalized),
                    activation=contract["activation"],
                    consumption=contract["consumption"],
                    duration=contract["duration"],
                    effects=contract["effects"],
                    range=contract["range"],
                    target=contract["target"],
                    uses=contract["uses"],
                    system=contract["system"],
                    flags={
                        "dnd5e": {
                            "ruleset_feature": normalized,
                            "source_key": str(template.get("source_key") or ""),
                        }
                    },
                )
            )
        )
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="advancement",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        item_id=item.id,
        deltas=[
            {
                "type": "ruleset_feature_grant",
                "feature": normalized,
                "item_id": item.id,
                "activity_ids": [activity["id"] for activity in activities],
            }
        ],
        narration_hints=[f"{actor.name} gains {item.name}."],
        flags={"dnd5e": {"ruleset_feature": normalized}},
    )
    return {"item": asdict(item), "activities": activities, "messages": [asdict(message)]}


def _feature_key(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


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
