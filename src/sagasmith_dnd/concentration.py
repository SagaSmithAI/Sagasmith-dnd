"""Concentration resolution helpers."""

from __future__ import annotations

from dataclasses import asdict

from sagasmith_core.foundry_documents import FoundryDocumentService


def resolve_concentration(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    success: bool,
) -> dict:
    effects = [
        effect
        for effect in documents.list_effects(campaign_id, actor_id=actor_id)
        if "concentrating" in set(effect.statuses)
    ]
    removed = []
    if not success:
        for effect in effects:
            removed.append(asdict(documents.delete_effect(effect.id)))
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="concentration",
        speaker={"actor": actor_id},
        actor_id=actor_id,
        deltas=[
            {
                "type": "concentration",
                "success": success,
                "removed": [effect["id"] for effect in removed],
            }
        ],
        narration_hints=[
            "Concentration holds." if success else "Concentration is broken."
        ],
    )
    return {
        "success": success,
        "active": [asdict(effect) for effect in effects] if success else [],
        "removed": removed,
        "messages": [asdict(message)],
    }
