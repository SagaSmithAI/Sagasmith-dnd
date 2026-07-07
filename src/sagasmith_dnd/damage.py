"""Damage application for Actor documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


def apply_actor_damage(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    amount: int,
    damage_type: str = "",
    source: str = "",
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    system = dict(actor.system or {})
    effective = dict((actor.derived or {}).get("effective_system") or system)
    hp = _hp(system)
    before_hp = int(hp.get("value", 0))
    adjusted = _adjust_damage(effective, max(0, int(amount)), damage_type)
    hp["value"] = max(0, before_hp - adjusted["amount"])
    system.setdefault("attributes", {})["hp"] = hp
    updated = documents.update_actor(actor_id, system=system)
    pending = _concentration_pending(
        documents,
        campaign_id=campaign_id,
        actor_id=actor_id,
        applied_damage=adjusted["amount"],
    )
    deltas = [
        {
            "type": "damage",
            "actor_id": actor_id,
            "damage_type": damage_type,
            "source": source,
            "input_amount": max(0, int(amount)),
            "applied_amount": adjusted["amount"],
            "adjustment": adjusted["adjustment"],
            "before_hp": before_hp,
            "after_hp": hp["value"],
        }
    ]
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="damage",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        deltas=deltas,
        pending=pending,
        narration_hints=[f"{actor.name} takes {adjusted['amount']} {damage_type} damage."],
    )
    return {
        "actor": asdict(updated),
        "damage": deltas[0],
        "pending": pending,
        "messages": [asdict(message)],
    }


def _hp(system: dict[str, Any]) -> dict[str, Any]:
    attributes = system.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {"value": 1, "max": 1})
    if not isinstance(hp, dict):
        hp = {"value": int(hp), "max": int(hp)}
        attributes["hp"] = hp
    return hp


def _adjust_damage(system: dict[str, Any], amount: int, damage_type: str) -> dict[str, Any]:
    traits = dict(system.get("traits") or {})
    normalized = damage_type.strip().lower()
    if normalized and normalized in _trait_values(traits.get("di")):
        return {"amount": 0, "adjustment": "immune"}
    if normalized and normalized in _trait_values(traits.get("dr")):
        return {"amount": amount // 2, "adjustment": "resistant"}
    if normalized and normalized in _trait_values(traits.get("dv")):
        return {"amount": amount * 2, "adjustment": "vulnerable"}
    return {"amount": amount, "adjustment": "normal"}


def _trait_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        raw = value.get("value", [])
    else:
        raw = value or []
    return {str(item).strip().lower() for item in raw}


def _concentration_pending(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    applied_damage: int,
) -> list[dict[str, Any]]:
    if applied_damage <= 0:
        return []
    concentrating = [
        effect
        for effect in documents.list_effects(campaign_id, actor_id=actor_id)
        if "concentrating" in set(effect.statuses)
    ]
    if not concentrating:
        return []
    return [
        {
            "type": "concentration_save_required",
            "actor_id": actor_id,
            "dc": max(10, applied_damage // 2),
            "ability": "con",
            "effect_ids": [effect.id for effect in concentrating],
            "deadline": "after_damage",
        }
    ]
