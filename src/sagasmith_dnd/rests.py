"""Rest and recovery for Foundry-style Actor/Item/Activity documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


def recover_document_rest(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    period: str,
    actor_id: str | None = None,
) -> dict[str, Any]:
    actors = [documents.get_actor(actor_id)] if actor_id else documents.list_actors(campaign_id)
    recovered: list[dict[str, Any]] = []
    for actor in actors:
        if actor.campaign_id != campaign_id:
            raise ValueError(f"actor {actor.id} is not in campaign {campaign_id}")
        system = dict(actor.system or {})
        if period == "long_rest":
            restored = _restore_spell_slots(system)
            if restored:
                documents.update_actor(actor.id, system=system)
                recovered.append({"actor_id": actor.id, "type": "spell_slots", "slots": restored})
        for item in documents.list_items(campaign_id, actor_id=actor.id):
            for activity in documents.list_activities(item.id):
                uses = dict(activity.uses or {})
                if not uses or int(uses.get("spent", 0) or 0) <= 0:
                    continue
                if not _recovers_on(uses, period):
                    continue
                before = dict(uses)
                uses["spent"] = 0
                documents.update_activity(activity.id, uses=uses)
                recovered.append(
                    {
                        "actor_id": actor.id,
                        "item_id": item.id,
                        "activity_id": activity.id,
                        "type": "activity_uses",
                        "before": before,
                        "after": uses,
                    }
                )
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="rest",
        speaker={"actor": actor_id} if actor_id else {"party": True},
        deltas=[{"type": "rest_recovery", "period": period, "recovered": recovered}],
        narration_hints=[f"{period.replace('_', ' ')} recovery is applied."],
    )
    return {"period": period, "recovered": recovered, "messages": [asdict(message)]}


def _restore_spell_slots(system: dict[str, Any]) -> list[dict[str, Any]]:
    restored = []
    spells = system.get("spells")
    if not isinstance(spells, dict):
        return restored
    for key, slot in spells.items():
        if key == "slots" or not isinstance(slot, dict):
            continue
        before = int(slot.get("value", slot.get("available", 0)) or 0)
        maximum = int(slot.get("max", before) or before)
        if before < maximum:
            slot["value"] = maximum
            restored.append({"slot": key, "before": before, "after": maximum})
    slots = spells.get("slots")
    if isinstance(slots, dict):
        for key, slot in slots.items():
            if not isinstance(slot, dict):
                continue
            before = int(slot.get("value", slot.get("available", 0)) or 0)
            maximum = int(slot.get("max", before) or before)
            if before < maximum:
                if "value" in slot or "available" not in slot:
                    slot["value"] = maximum
                else:
                    slot["available"] = maximum
                restored.append({"slot": key, "before": before, "after": maximum})
    return restored


def _recovers_on(uses: dict[str, Any], period: str) -> bool:
    periods = set()
    for entry in uses.get("recovery") or []:
        if isinstance(entry, str):
            periods.add(_normalize_period(entry))
        elif isinstance(entry, dict):
            periods.add(_normalize_period(str(entry.get("period") or "")))
    if period == "long_rest" and "short_rest" in periods:
        return True
    return period in periods


def _normalize_period(value: str) -> str:
    normalized = value.strip().replace("-", "_")
    if normalized in {"shortRest", "short_rest", "sr"}:
        return "short_rest"
    if normalized in {"longRest", "long_rest", "lr"}:
        return "long_rest"
    return normalized
