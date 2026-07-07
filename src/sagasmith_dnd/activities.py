"""Foundry-style activity execution adapted for AI-DM JSON workflows."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


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

    created_effects = []
    effect_target = target_id or actor_id
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
    for effect in created_effects:
        deltas.append({"type": "active_effect", "effect_id": effect["id"], "after": effect})

    pending = _reaction_windows(activity, actor_id=actor_id, target_id=target_id)
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

    return state, {
        "type": "activity_result",
        "actor": asdict(actor),
        "item": asdict(item),
        "activity": asdict(activity),
        "target_id": target_id,
        "payment": payment,
        "state_delta": {"runtime": {"turn_budgets": {actor_id: actor_budget}}},
        "deltas": deltas,
        "effects": created_effects,
        "pending": pending,
        "messages": [asdict(message)],
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


def _reaction_windows(activity, *, actor_id: str, target_id: str | None) -> list[dict[str, Any]]:
    if activity.activity_type != "attack" or not target_id:
        return []
    return [
        {
            "type": "reaction_window",
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
