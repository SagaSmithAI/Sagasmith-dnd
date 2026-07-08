"""Rest and recovery for Foundry-style Actor/Item/Activity documents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.engine import roll


def recover_document_rest(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    period: str,
    actor_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actors = [documents.get_actor(actor_id)] if actor_id else documents.list_actors(campaign_id)
    config = dict(config or {})
    recovered: list[dict[str, Any]] = []
    for actor in actors:
        if actor.campaign_id != campaign_id:
            raise ValueError(f"actor {actor.id} is not in campaign {campaign_id}")
        system = dict(actor.system or {})
        actor_recovery: list[dict[str, Any]] = []
        if period == "long_rest":
            actor_recovery.extend(_restore_hit_points(system))
            restored = _restore_spell_slots(system)
            if restored:
                actor_recovery.append({"type": "spell_slots", "slots": restored})
            actor_recovery.extend(_recover_hit_dice(system))
            actor_recovery.extend(_reset_death_saves(system))
        if period == "short_rest" and int(config.get("hit_dice", 0) or 0) > 0:
            actor_recovery.extend(_spend_hit_dice(system, int(config.get("hit_dice", 0) or 0)))
        actor_recovery.extend(_recover_actor_resources(system, period))
        if actor_recovery:
            documents.update_actor(actor.id, system=system)
            for item in actor_recovery:
                recovered.append({"actor_id": actor.id, **item})
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


def _restore_hit_points(system: dict[str, Any]) -> list[dict[str, Any]]:
    attributes = system.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {})
    if not isinstance(hp, dict):
        return []
    before = dict(hp)
    maximum = int(hp.get("max", hp.get("value", 0)) or 0)
    hp["value"] = maximum
    hp["temp"] = 0
    hp["tempmax"] = 0
    if hp != before:
        return [{"type": "hit_points", "before": before, "after": dict(hp)}]
    return []


def _recover_hit_dice(system: dict[str, Any]) -> list[dict[str, Any]]:
    hd = _hit_dice(system)
    if not hd:
        return []
    before = dict(hd)
    maximum = int(hd.get("max", _actor_level(system)) or _actor_level(system))
    spent = int(hd.get("spent", 0) or 0)
    recovered = min(spent, max(1, maximum // 2))
    if recovered <= 0:
        return []
    hd["max"] = maximum
    hd["spent"] = spent - recovered
    return [{"type": "hit_dice_recovered", "before": before, "after": dict(hd), "recovered": recovered}]


def _spend_hit_dice(system: dict[str, Any], count: int) -> list[dict[str, Any]]:
    hd = _hit_dice(system)
    hp = system.setdefault("attributes", {}).setdefault("hp", {})
    if not hd or not isinstance(hp, dict):
        return []
    maximum = int(hd.get("max", _actor_level(system)) or _actor_level(system))
    spent = int(hd.get("spent", 0) or 0)
    available = max(0, maximum - spent)
    to_spend = min(max(0, count), available)
    if to_spend <= 0:
        return []
    die = str(hd.get("die") or hd.get("denomination") or "d8")
    if not die.startswith("d"):
        die = f"d{die}"
    con_mod = _ability_mod(system, "con")
    before_hp = dict(hp)
    rolls = []
    healed = 0
    for _ in range(to_spend):
        rolled = roll(f"1{die}+{con_mod}")
        amount = max(0, rolled.total)
        healed += amount
        rolls.append(asdict(rolled))
    hp["value"] = min(int(hp.get("max", hp.get("value", 0)) or 0), int(hp.get("value", 0) or 0) + healed)
    hd["max"] = maximum
    hd["spent"] = spent + to_spend
    return [
        {
            "type": "hit_dice_spent",
            "spent": to_spend,
            "healed": healed,
            "rolls": rolls,
            "hp_before": before_hp,
            "hp_after": dict(hp),
            "hit_dice": dict(hd),
        }
    ]


def _reset_death_saves(system: dict[str, Any]) -> list[dict[str, Any]]:
    death = system.setdefault("attributes", {}).setdefault("death", {})
    if not isinstance(death, dict):
        return []
    before = dict(death)
    death.update({"successes": 0, "failures": 0, "success": 0, "failure": 0, "stable": False, "dead": False})
    if death != before:
        return [{"type": "death_saves_reset", "before": before, "after": dict(death)}]
    return []


def _recover_actor_resources(system: dict[str, Any], period: str) -> list[dict[str, Any]]:
    resources = system.get("resources")
    if not isinstance(resources, dict):
        return []
    recovered = []
    for key, resource in resources.items():
        if not isinstance(resource, dict) or not _resource_recovers(resource, period):
            continue
        before = dict(resource)
        if "spent" in resource:
            resource["spent"] = 0
        if "value" in resource and "max" in resource:
            resource["value"] = int(resource.get("max", resource.get("value", 0)) or 0)
        if resource != before:
            recovered.append({"type": "resource", "resource": key, "before": before, "after": dict(resource)})
    return recovered


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


def _hit_dice(system: dict[str, Any]) -> dict[str, Any]:
    attributes = system.setdefault("attributes", {})
    hd = attributes.setdefault("hd", {})
    if not isinstance(hd, dict):
        return {}
    hd.setdefault("max", _actor_level(system))
    hd.setdefault("spent", 0)
    return hd


def _actor_level(system: dict[str, Any]) -> int:
    return int(system.get("level") or system.get("details", {}).get("level") or 1)


def _ability_mod(system: dict[str, Any], ability: str) -> int:
    raw = dict(system.get("abilities") or {}).get(ability, {"value": 10})
    score = int(raw.get("value", raw) if isinstance(raw, dict) else raw)
    return (score - 10) // 2


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


def _resource_recovers(resource: dict[str, Any], period: str) -> bool:
    if period == "short_rest" and resource.get("sr"):
        return True
    if period == "long_rest" and (resource.get("lr") or resource.get("sr")):
        return True
    return _recovers_on(resource, period)


def _normalize_period(value: str) -> str:
    normalized = value.strip().replace("-", "_")
    if normalized in {"shortRest", "short_rest", "sr"}:
        return "short_rest"
    if normalized in {"longRest", "long_rest", "lr"}:
        return "long_rest"
    return normalized
