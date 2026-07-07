"""Foundry-style activity execution adapted for AI-DM JSON workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.damage import apply_actor_damage
from sagasmith_dnd.engine import roll, roll_d20
from sagasmith_dnd.rolls import roll_actor_d20


PAYMENT_BY_ACTIVATION = {
    "action": "main_action",
    "bonus": "bonus_action",
    "reaction": "reaction",
    "free": "free",
    "none": "free",
    "": "free",
}


def execute_document_activity(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    state: dict[str, Any],
    actor_id: str,
    item_id: str,
    activity_id: str,
    target_id: str | None = None,
    payment: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute a persisted Activity document and return a Foundry-like envelope."""

    actor = documents.get_actor(actor_id)
    item = documents.get_item(item_id)
    activity = documents.get_activity(activity_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    if item.campaign_id != campaign_id:
        raise ValueError(f"item {item_id} is not in campaign {campaign_id}")
    if activity.campaign_id != campaign_id or activity.item_id != item_id:
        raise ValueError(f"activity {activity_id} does not belong to item {item_id}")
    if item.actor_id and item.actor_id != actor_id:
        raise ValueError(f"item {item_id} is owned by actor {item.actor_id}, not {actor_id}")

    payload = dict(payload or {})
    activation = dict(activity.activation or {})
    payment = payment or _default_payment(activation)
    runtime = dict(state.get("runtime") or {})
    budgets = dict(runtime.get("turn_budgets") or {})
    actor_budget = _budget_for(budgets.get(actor_id))
    payment_delta = _spend_payment(actor_budget, payment)
    budgets[actor_id] = actor_budget
    runtime["turn_budgets"] = budgets
    state["runtime"] = runtime

    uses_before = dict(activity.uses or {})
    uses_after = _spend_uses(uses_before, payload)
    if uses_after != uses_before:
        activity = documents.update_activity(activity_id, uses=uses_after)

    actor_system_before = deepcopy(actor.system)
    actor_system_after = _consume_spell_slot(actor_system_before, activity, payload)
    if actor_system_after != actor_system_before:
        actor = documents.update_actor(actor_id, system=actor_system_after)

    execution = _execute_activity_effect(
        documents,
        campaign_id=campaign_id,
        actor_id=actor_id,
        target_id=target_id,
        item_system=item.system,
        activity=activity,
        payload=payload,
    )

    created_effects = []
    effect_target = target_id or actor_id
    if _requires_concentration(activity):
        concentration = documents.create_effect(
            campaign_id=campaign_id,
            parent_type="actor",
            parent_id=actor_id,
            actor_id=actor_id,
            origin=f"Activity.{activity.id}",
            name=f"Concentrating: {activity.name}",
            duration=dict(activity.duration or {}),
            statuses=["concentrating"],
            flags={"dnd5e": {"concentration": True, "item_id": item_id, "activity_id": activity_id}},
        )
        created_effects.append(asdict(concentration))
        runtime = dict(state.get("runtime") or {})
        concentration_state = dict(runtime.get("concentration") or {})
        concentration_state[actor_id] = {
            "effect_id": concentration.id,
            "item_id": item_id,
            "activity_id": activity_id,
        }
        runtime["concentration"] = concentration_state
        state["runtime"] = runtime

    for effect in activity.effects:
        effect_payload = dict(effect)
        created = documents.create_effect(
            campaign_id=campaign_id,
            parent_type=str(effect_payload.pop("parent_type", "actor")),
            parent_id=str(effect_payload.pop("parent_id", effect_target)),
            actor_id=effect_target,
            origin=f"Activity.{activity.id}",
            name=str(effect_payload.pop("name", activity.name)),
            img=str(effect_payload.pop("img", "")),
            disabled=bool(effect_payload.pop("disabled", False)),
            suppressed=bool(effect_payload.pop("suppressed", False)),
            transfer=bool(effect_payload.pop("transfer", False)),
            duration=dict(effect_payload.pop("duration", activity.duration or {})),
            changes=list(effect_payload.pop("changes", [])),
            statuses=list(effect_payload.pop("statuses", [])),
            flags={**dict(effect_payload.pop("flags", {})), "dnd5e": effect_payload},
        )
        created_effects.append(asdict(created))

    deltas = [
        {
            "type": "payment",
            "actor_id": actor_id,
            "payment": payment,
            "before": payment_delta["before"],
            "after": payment_delta["after"],
        }
    ]
    if uses_after != uses_before:
        deltas.append(
            {
                "type": "activity_uses",
                "activity_id": activity_id,
                "before": uses_before,
                "after": uses_after,
            }
        )
    if actor_system_after != actor_system_before:
        deltas.append(
            {
                "type": "actor_system",
                "actor_id": actor_id,
                "before": actor_system_before,
                "after": actor_system_after,
            }
        )
    if execution:
        deltas.append({"type": "activity_execution", "result": execution["result"]})
    for effect in created_effects:
        deltas.append({"type": "active_effect", "effect_id": effect["id"], "after": effect})

    pending = _reaction_windows(activity, actor_id=actor_id, target_id=target_id)
    pending.extend(execution.get("pending", []) if execution else [])
    if pending:
        runtime = dict(state.get("runtime") or {})
        queued = list(runtime.get("pending") or [])
        queued.extend(pending)
        runtime["pending"] = queued
        state["runtime"] = runtime
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="activity",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        item_id=item_id,
        activity_id=activity_id,
        rolls=list(payload.get("rolls") or []),
        deltas=deltas,
        pending=pending,
        narration_hints=_narration_hints(actor.name, item.name, activity.name, target_id),
        flags={"dnd5e": {"activity_type": activity.activity_type}},
    )
    messages = [asdict(message)]
    if execution:
        messages.extend(execution.get("messages", []))

    return state, {
        "type": "activity_result",
        "actor": asdict(actor),
        "item": asdict(item),
        "activity": asdict(activity),
        "target_id": target_id,
        "payment": payment,
        "state_delta": {"runtime": {"turn_budgets": {actor_id: actor_budget}}},
        "deltas": deltas,
        "execution": execution["result"] if execution else None,
        "effects": created_effects,
        "pending": pending,
        "messages": messages,
        "narration_hints": list(message.narration_hints),
    }


def _default_payment(activation: dict[str, Any]) -> str:
    value = str(activation.get("type") or activation.get("activation") or "free")
    return PAYMENT_BY_ACTIVATION.get(value, value)


def _budget_for(value: Any) -> dict[str, int]:
    budget = dict(value or {})
    for key in ("main_action", "bonus_action", "reaction"):
        budget[key] = int(budget.get(key, 1))
    budget["movement"] = int(budget.get("movement", 0))
    return budget


def _spend_payment(budget: dict[str, int], payment: str) -> dict[str, Any]:
    before = dict(budget)
    if payment == "free":
        return {"before": before, "after": dict(budget)}
    if payment not in {"main_action", "bonus_action", "reaction"}:
        raise ValueError(f"unknown payment: {payment}")
    if int(budget.get(payment, 0)) <= 0:
        raise ValueError(f"cannot pay {payment}")
    budget[payment] = int(budget.get(payment, 0)) - 1
    return {"before": before, "after": dict(budget)}


def _spend_uses(uses: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not uses:
        return uses
    value = dict(uses)
    cost = int(payload.get("use_cost", value.get("cost", 1)) or 1)
    max_uses = value.get("max")
    if max_uses in (None, ""):
        return value
    spent = int(value.get("spent", 0) or 0)
    if spent + cost > int(max_uses):
        raise ValueError("activity has no uses remaining")
    value["spent"] = spent + cost
    return value


def _consume_spell_slot(system: dict[str, Any], activity, payload: dict[str, Any]) -> dict[str, Any]:
    if activity.activity_type != "cast":
        return system
    level = int(
        payload.get("spell_level")
        or activity.system.get("level")
        or activity.system.get("spell", {}).get("level")
        or 0
    )
    if level <= 0:
        return system
    value = deepcopy(system)
    spells = value.setdefault("spells", {})
    slot = spells.get(f"spell{level}")
    if not isinstance(slot, dict):
        slots = spells.setdefault("slots", {})
        slot = slots.get(str(level))
        if not isinstance(slot, dict):
            raise ValueError(f"actor has no level {level} spell slots")
    current = int(slot.get("value", slot.get("available", 0)) or 0)
    if current <= 0:
        raise ValueError(f"actor has no level {level} spell slots remaining")
    if "value" in slot or "available" not in slot:
        slot["value"] = current - 1
    else:
        slot["available"] = current - 1
    return value


def _requires_concentration(activity) -> bool:
    duration = dict(activity.duration or {})
    system = dict(activity.system or {})
    return bool(
        duration.get("concentration")
        or system.get("concentration")
        or system.get("spell", {}).get("concentration")
    )


def _execute_activity_effect(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    target_id: str | None,
    item_system: dict[str, Any],
    activity,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    activity_type = activity.activity_type
    if activity_type == "attack":
        return _execute_attack(
            documents,
            campaign_id=campaign_id,
            actor_id=actor_id,
            target_id=target_id,
            item_system=item_system,
            activity=activity,
            payload=payload,
        )
    if activity_type == "damage":
        return _execute_damage(
            documents,
            campaign_id=campaign_id,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    if activity_type == "heal":
        if not (payload.get("amount") or payload.get("healing") or activity.system.get("healing")):
            return None
        return _execute_heal(
            documents,
            campaign_id=campaign_id,
            target_id=target_id or actor_id,
            activity=activity,
            payload=payload,
        )
    if activity_type == "save":
        return _execute_save(
            documents,
            campaign_id=campaign_id,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    return None


def _execute_attack(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    target_id: str | None,
    item_system: dict[str, Any],
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("attack activity requires target-id")
    target = documents.get_actor(target_id)
    attack_bonus = int(
        payload.get("attack_bonus")
        or activity.system.get("attack_bonus")
        or item_system.get("attack_bonus")
        or 0
    )
    die = roll_d20(
        advantage=bool(payload.get("advantage", False)),
        disadvantage=bool(payload.get("disadvantage", False)),
    )
    total = int(die["natural"]) + attack_bonus
    target_ac = int(payload.get("target_ac") or _actor_ac(target.system, target.derived))
    hit = bool(die["critical"] or (not die["fumble"] and total >= target_ac))
    result: dict[str, Any] = {
        "type": "attack",
        "actor_id": actor_id,
        "target_id": target_id,
        "attack_bonus": attack_bonus,
        "target_ac": target_ac,
        "roll": die,
        "total": total,
        "hit": hit,
    }
    messages: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    damage_expression = payload.get("damage") or activity.system.get("damage")
    if hit and damage_expression:
        damage = roll(str(damage_expression))
        damage_result = apply_actor_damage(
            documents,
            campaign_id=campaign_id,
            actor_id=target_id,
            amount=damage.total,
            damage_type=str(payload.get("damage_type") or activity.system.get("damage_type") or ""),
            source=activity.name,
        )
        result["damage_roll"] = {
            "expression": damage.expression,
            "total": damage.total,
            "rolls": list(damage.rolls),
            "detail": damage.detail,
        }
        result["damage"] = damage_result["damage"]
        pending.extend(damage_result.get("pending", []))
        messages.extend(damage_result.get("messages", []))
    return {"result": result, "pending": pending, "messages": messages}


def _execute_damage(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("damage activity requires target-id")
    amount = payload.get("amount")
    rolled = None
    if amount is None:
        expression = str(payload.get("damage") or activity.system.get("damage") or "")
        if not expression:
            raise ValueError("damage activity requires amount or damage expression")
        rolled = roll(expression)
        amount = rolled.total
    damage_result = apply_actor_damage(
        documents,
        campaign_id=campaign_id,
        actor_id=target_id,
        amount=int(amount),
        damage_type=str(payload.get("damage_type") or activity.system.get("damage_type") or ""),
        source=activity.name,
    )
    result = {"type": "damage", **damage_result["damage"]}
    if rolled:
        result["roll"] = {
            "expression": rolled.expression,
            "total": rolled.total,
            "rolls": list(rolled.rolls),
            "detail": rolled.detail,
        }
    return {
        "result": result,
        "pending": list(damage_result.get("pending", [])),
        "messages": list(damage_result.get("messages", [])),
    }


def _execute_heal(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    target_id: str,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    target = documents.get_actor(target_id)
    if target.campaign_id != campaign_id:
        raise ValueError(f"actor {target_id} is not in campaign {campaign_id}")
    amount = payload.get("amount")
    rolled = None
    if amount is None:
        expression = str(payload.get("healing") or activity.system.get("healing") or "")
        if not expression:
            raise ValueError("heal activity requires amount or healing expression")
        rolled = roll(expression)
        amount = rolled.total
    system = deepcopy(target.system)
    hp = _hp(system)
    before = int(hp.get("value", 0))
    maximum = int(hp.get("max", before) or before)
    hp["value"] = min(maximum, before + max(0, int(amount)))
    updated = documents.update_actor(target_id, system=system)
    result: dict[str, Any] = {
        "type": "heal",
        "target_id": target_id,
        "amount": max(0, int(amount)),
        "before_hp": before,
        "after_hp": hp["value"],
    }
    if rolled:
        result["roll"] = {
            "expression": rolled.expression,
            "total": rolled.total,
            "rolls": list(rolled.rolls),
            "detail": rolled.detail,
        }
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="heal",
        speaker={"actor": target_id, "alias": target.name},
        actor_id=target_id,
        deltas=[{"type": "heal", "before": asdict(target), "after": asdict(updated), "result": result}],
        narration_hints=[f"{target.name} regains {result['amount']} hit points."],
    )
    return {"result": result, "pending": [], "messages": [asdict(message)]}


def _execute_save(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("save activity requires target-id")
    dc = int(payload.get("dc") or activity.system.get("dc") or 10)
    ability = str(payload.get("ability") or activity.system.get("ability") or "dex")
    save_result = roll_actor_d20(
        documents,
        campaign_id=campaign_id,
        actor_id=target_id,
        roll_type="save",
        dc=dc,
        ability=ability,
        bonus=int(payload.get("bonus", 0) or 0),
        advantage=bool(payload.get("advantage", False)),
        disadvantage=bool(payload.get("disadvantage", False)),
        source=activity.name,
    )
    result: dict[str, Any] = {"type": "save", **save_result["roll"]}
    pending: list[dict[str, Any]] = []
    messages = list(save_result.get("messages", []))
    damage_expression = payload.get("damage") or activity.system.get("damage")
    if damage_expression:
        rolled = roll(str(damage_expression))
        amount = rolled.total if not result["success"] else rolled.total // 2
        damage_result = apply_actor_damage(
            documents,
            campaign_id=campaign_id,
            actor_id=target_id,
            amount=amount,
            damage_type=str(payload.get("damage_type") or activity.system.get("damage_type") or ""),
            source=activity.name,
        )
        result["damage_roll"] = {
            "expression": rolled.expression,
            "total": rolled.total,
            "applied": amount,
            "rolls": list(rolled.rolls),
            "detail": rolled.detail,
        }
        result["damage"] = damage_result["damage"]
        pending.extend(damage_result.get("pending", []))
        messages.extend(damage_result.get("messages", []))
    return {"result": result, "pending": pending, "messages": messages}


def _actor_ac(system: dict[str, Any], derived: dict[str, Any]) -> int:
    effective = dict((derived or {}).get("effective_system") or system or {})
    ac = effective.get("attributes", {}).get("ac", 10)
    if isinstance(ac, dict):
        return int(ac.get("value", 10))
    return int(ac or 10)


def _hp(system: dict[str, Any]) -> dict[str, Any]:
    attributes = system.setdefault("attributes", {})
    hp = attributes.setdefault("hp", {"value": 1, "max": 1})
    if not isinstance(hp, dict):
        hp = {"value": int(hp), "max": int(hp)}
        attributes["hp"] = hp
    return hp


def _reaction_windows(activity, *, actor_id: str, target_id: str | None) -> list[dict[str, Any]]:
    if activity.activity_type != "attack" or not target_id:
        return []
    return [
        {
            "id": f"reaction-{uuid4().hex}",
            "type": "reaction_window",
            "status": "pending",
            "trigger": "targeted_by_attack",
            "actor_id": target_id,
            "source_actor_id": actor_id,
            "activity_id": activity.id,
            "deadline": "before_attack_resolution",
        }
    ]


def _narration_hints(actor_name: str, item_name: str, activity_name: str, target_id: str | None) -> list[str]:
    target = f" against {target_id}" if target_id else ""
    return [f"{actor_name} uses {activity_name} from {item_name}{target}."]
