"""Condition helpers backed by ActiveEffect documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.rulesets import get_ruleset


def add_actor_condition(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    condition: str,
    duration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    normalized = _normalize(condition)
    ruleset = get_ruleset(include_content=False)
    if normalized not in ruleset.get("conditionTypes", {}):
        raise ValueError(f"unknown condition: {condition}")
    condition_data = ruleset.get("conditionTypes", {}).get(normalized, {})
    implied_statuses = list(condition_data.get("statuses") or [])
    riders = list(condition_data.get("riders") or [])
    statuses = sorted({normalized, *implied_statuses, *riders})
    effect = documents.create_effect(
        campaign_id=campaign_id,
        parent_type="actor",
        parent_id=actor_id,
        actor_id=actor_id,
        origin=f"Condition.{normalized}",
        name=normalized,
        duration=dict(duration or {}),
        statuses=statuses,
        flags={"dnd5e": {"condition": normalized}},
    )
    return {"effect": asdict(effect), "statuses": statuses}


def remove_actor_condition(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    condition: str,
) -> dict[str, Any]:
    normalized = _normalize(condition)
    removed = []
    for effect in documents.list_effects(campaign_id, actor_id=actor_id):
        flags = dict(effect.flags or {}).get("dnd5e", {})
        if flags.get("condition") == normalized or normalized in set(effect.statuses):
            removed.append(asdict(documents.delete_effect(effect.id)))
    return {"removed": removed, "condition": normalized}


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
