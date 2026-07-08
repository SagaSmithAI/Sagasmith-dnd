"""Foundry-style activity execution adapted for AI-DM JSON workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import re
from typing import Any
from uuid import uuid4

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.damage import apply_actor_damage
from sagasmith_dnd.engine import roll, roll_d20
from sagasmith_dnd.rolls import roll_actor_d20
from sagasmith_dnd.rulesets import get_ruleset


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
    _require_actor_can_use_activity(documents, campaign_id=campaign_id, actor=actor, activity=activity)
    runtime = dict(state.get("runtime") or {})
    budgets = dict(runtime.get("turn_budgets") or {})
    actor_budget = _budget_for(budgets.get(actor_id))
    payment_options = activity_payment_options(activity, actor_budget)
    if payment is None:
        payment = payment_options[0] if payment_options else _default_payment(activation)
    elif (
        payment not in payment_options
        and payment != "free"
        and not _allows_reaction_override(activity, payload=payload, payment=payment)
    ):
        raise ValueError(f"cannot pay {payment} for {activity.name}")
    payment_delta = _spend_payment(actor_budget, payment)
    _apply_activity_grants(actor_budget, actor=actor, activity=activity, payment=payment)
    budgets[actor_id] = actor_budget
    runtime["turn_budgets"] = budgets
    state["runtime"] = runtime

    uses_before = dict(activity.uses or {})
    uses_after = _spend_uses(uses_before, payload)
    if uses_after != uses_before:
        activity = documents.update_activity(activity_id, uses=uses_after)

    actor_system_before = deepcopy(actor.system)
    actor_system_after = _consume_spell_slot(actor_system_before, item, activity, payload)
    if actor_system_after != actor_system_before:
        actor = documents.update_actor(actor_id, system=actor_system_after)

    execution = _execute_activity_effect(
        documents,
        campaign_id=campaign_id,
        actor=actor,
        actor_id=actor_id,
        target_id=target_id,
        item=item,
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
        if not _effect_matches_execution(effect_payload, execution):
            continue
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


def _effect_matches_execution(effect: dict[str, Any], execution: dict[str, Any] | None) -> bool:
    apply_on = str(effect.get("apply_on") or effect.get("applyOn") or "use").strip().lower()
    if apply_on in {"", "use", "always"}:
        return True
    result = dict((execution or {}).get("result") or {})
    if apply_on in {"hit", "attack_hit"}:
        return bool(result.get("type") == "attack" and result.get("hit"))
    if apply_on in {"miss", "attack_miss"}:
        return bool(result.get("type") == "attack" and not result.get("hit"))
    if apply_on in {"failed_save", "save_failed"}:
        return bool(result.get("type") == "save" and not result.get("success"))
    if apply_on in {"successful_save", "save_success"}:
        return bool(result.get("type") == "save" and result.get("success"))
    return True


def list_actor_activity_options(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    budget = turn_budget_for_state(state, actor_id)
    available = []
    unavailable = []
    for item in documents.list_items(campaign_id, actor_id=actor_id):
        item_system = dict(item.system or {})
        if item_system.get("hidden"):
            continue
        for activity in documents.list_activities(item.id):
            payments = activity_payment_options(activity, budget)
            entry = {
                "item_id": item.id,
                "item_name": item.name,
                "item_type": item.item_type,
                "activity_id": activity.id,
                "activity_name": activity.name,
                "activity_type": activity.activity_type,
                "activation": dict(activity.activation or {}),
                "payments": payments,
                "requires_target": activity.activity_type in {"attack", "damage", "heal", "save"},
            }
            if payments:
                available.append(entry)
            else:
                unavailable.append({**entry, "reason": "no_available_payment"})
    return {
        "actor_id": actor_id,
        "turn_budget": budget,
        "available": available,
        "unavailable": unavailable,
    }


def turn_budget_for_state(state: dict[str, Any], actor_id: str) -> dict[str, int]:
    runtime = dict(state.get("runtime") or {})
    budgets = dict(runtime.get("turn_budgets") or {})
    return _budget_for(budgets.get(actor_id))


def activity_payment_options(activity, budget: dict[str, int]) -> list[str]:
    activation = dict(getattr(activity, "activation", {}) or {})
    activation_type = str(activation.get("type") or activation.get("activation") or "free")
    if activation_type == "action":
        values = []
        if getattr(activity, "activity_type", "") == "attack" and int(budget.get("attack_budget", 0)) > 0:
            values.append("attack_budget")
        if int(budget.get("main_action", 0)) > 0:
            values.append("main_action")
        if int(budget.get("extra_action", 0)) > 0:
            values.append("extra_action")
        return values
    if activation_type == "bonus":
        return ["bonus_action"] if int(budget.get("bonus_action", 0)) > 0 else []
    if activation_type == "reaction":
        return ["reaction"] if int(budget.get("reaction", 0)) > 0 else []
    return ["free"]


def _default_payment(activation: dict[str, Any]) -> str:
    value = str(activation.get("type") or activation.get("activation") or "free")
    return PAYMENT_BY_ACTIVATION.get(value, value)


def _budget_for(value: Any) -> dict[str, int]:
    budget = dict(value or {})
    for key in ("main_action", "bonus_action", "reaction"):
        budget[key] = int(budget.get(key, 1))
    budget["extra_action"] = int(budget.get("extra_action", 0) or 0)
    budget["attack_budget"] = int(budget.get("attack_budget", 0) or 0)
    budget["movement"] = int(budget.get("movement", 0))
    return budget


def _spend_payment(budget: dict[str, int], payment: str) -> dict[str, Any]:
    before = dict(budget)
    if payment == "free":
        return {"before": before, "after": dict(budget)}
    if payment not in {"main_action", "bonus_action", "reaction", "extra_action", "attack_budget"}:
        raise ValueError(f"unknown payment: {payment}")
    if int(budget.get(payment, 0)) <= 0:
        raise ValueError(f"cannot pay {payment}")
    budget[payment] = int(budget.get(payment, 0)) - 1
    return {"before": before, "after": dict(budget)}


def _allows_reaction_override(activity, *, payload: dict[str, Any], payment: str) -> bool:
    return (
        payment == "reaction"
        and getattr(activity, "activity_type", "") == "attack"
        and str(payload.get("reaction_trigger") or "") == "opportunity_attack"
    )


def _apply_activity_grants(budget: dict[str, int], *, actor, activity, payment: str) -> None:
    grant = dict(activity.system.get("grant") or {})
    if grant.get("extra_actions") is not None:
        budget["extra_action"] = int(budget.get("extra_action", 0)) + int(grant.get("extra_actions", 0) or 0)
    if activity.activity_type == "attack" and payment in {"main_action", "extra_action"}:
        attacks = _attacks_per_action(actor)
        budget["attack_budget"] = max(int(budget.get("attack_budget", 0)), max(0, attacks - 1))


def _attacks_per_action(actor) -> int:
    system = dict((getattr(actor, "derived", None) or {}).get("effective_system") or getattr(actor, "system", {}) or {})
    explicit = system.get("attacks_per_action")
    if explicit not in (None, ""):
        return max(1, int(explicit))
    features = system.get("features") or []
    if isinstance(features, dict):
        feature_ids = {str(key).strip().lower().replace("_", "-") for key, enabled in features.items() if enabled}
    else:
        feature_ids = {str(item).strip().lower().replace("_", "-").replace(" ", "-") for item in features}
    if "extra-attack" in feature_ids:
        return 2
    return 1


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


def _consume_spell_slot(system: dict[str, Any], item, activity, payload: dict[str, Any]) -> dict[str, Any]:
    if activity.activity_type != "cast":
        return system
    item_system = dict(getattr(item, "system", {}) or {})
    level = int(
        payload.get("spell_level")
        or activity.system.get("level")
        or activity.system.get("spell", {}).get("level")
        or item_system.get("level")
        or item_system.get("spell", {}).get("level")
        or 0
    )
    if level <= 0:
        return system
    if _ritual_cast(item_system, activity, payload):
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


def _ritual_cast(item_system: dict[str, Any], activity, payload: dict[str, Any]) -> bool:
    if not bool(payload.get("ritual", False)):
        return False
    system = dict(getattr(activity, "system", {}) or {})
    return bool(system.get("ritual") or system.get("spell", {}).get("ritual") or item_system.get("ritual"))


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
    actor,
    actor_id: str,
    target_id: str | None,
    item,
    item_system: dict[str, Any],
    activity,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    activity_type = activity.activity_type
    if activity_type == "attack":
        return _execute_attack(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            actor_id=actor_id,
            target_id=target_id,
            item=item,
            item_system=item_system,
            activity=activity,
            payload=payload,
        )
    if activity_type == "damage":
        return _execute_damage(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            item=item,
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
            actor=actor,
            item=item,
            target_id=target_id or actor_id,
            activity=activity,
            payload=payload,
        )
    if activity_type == "save":
        return _execute_save(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            item=item,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    if activity_type == "cast":
        return _execute_cast(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            actor_id=actor_id,
            item=item,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    if activity_type == "check":
        return _execute_check(
            documents,
            campaign_id=campaign_id,
            actor_id=actor_id,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    return None


def _execute_cast(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
    actor_id: str,
    item,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    system = {**dict(getattr(item, "system", {}) or {}), **dict(getattr(activity, "system", {}) or {})}
    if system.get("save") or system.get("dc"):
        return _execute_save(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            item=item,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    if system.get("attack") or system.get("attack_bonus"):
        return _execute_attack(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            actor_id=actor_id,
            target_id=target_id,
            item=item,
            item_system=dict(getattr(item, "system", {}) or {}),
            activity=activity,
            payload=payload,
        )
    if system.get("healing") or system.get("heal_at_slot_level"):
        return _execute_heal(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            item=item,
            target_id=target_id or actor.id,
            activity=activity,
            payload=payload,
        )
    if system.get("damage"):
        return _execute_damage(
            documents,
            campaign_id=campaign_id,
            actor=actor,
            item=item,
            target_id=target_id,
            activity=activity,
            payload=payload,
        )
    return None


def _execute_attack(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
    actor_id: str,
    target_id: str | None,
    item,
    item_system: dict[str, Any],
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("attack activity requires target-id")
    target = documents.get_actor(target_id)
    range_result = _activity_range_result(activity, item_system=item_system, payload=payload)
    roll_data = _roll_data(actor, item, activity, payload)
    attack_bonus = _formula_int(
        payload.get("attack_bonus")
        or activity.system.get("attack_bonus")
        or item_system.get("attack_bonus")
        or _default_spell_attack_bonus(actor, item, activity),
        roll_data,
    )
    roll_context = _attack_roll_context(
        documents,
        campaign_id=campaign_id,
        actor=actor,
        target=target,
        range_result=range_result,
        advantage=bool(payload.get("advantage", False)),
        disadvantage=bool(payload.get("disadvantage", False)),
    )
    die = roll_d20(
        advantage=roll_context["advantage"],
        disadvantage=roll_context["disadvantage"],
    )
    total = int(die["natural"]) + attack_bonus
    target_ac = _formula_int(payload.get("target_ac") or _actor_ac(target.system, target.derived), roll_data)
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
        "advantage": roll_context["advantage"],
        "disadvantage": roll_context["disadvantage"],
        "advantage_sources": roll_context["advantage_sources"],
        "disadvantage_sources": roll_context["disadvantage_sources"],
    }
    if range_result:
        result["range"] = range_result
    messages: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    damage_spec = _damage_spec(activity, payload, item_system=item_system, roll_data=roll_data)
    if hit and damage_spec["expression"]:
        expression = str(damage_spec["expression"])
        rolled_expression = _critical_expression(expression) if die["critical"] else expression
        damage = roll(_resolve_formula(rolled_expression, roll_data))
        damage_result = apply_actor_damage(
            documents,
            campaign_id=campaign_id,
            actor_id=target_id,
            amount=damage.total,
            damage_type=str(payload.get("damage_type") or damage_spec["damage_type"]),
            source=activity.name,
        )
        result["damage_roll"] = {
            "expression": damage.expression,
            "base_expression": expression,
            "total": damage.total,
            "rolls": list(damage.rolls),
            "detail": damage.detail,
            "parts": damage_spec["parts"],
            "critical": bool(die["critical"]),
        }
        result["damage"] = damage_result["damage"]
        pending.extend(damage_result.get("pending", []))
        messages.extend(damage_result.get("messages", []))
    return {"result": result, "pending": pending, "messages": messages}


def _activity_range_result(activity, *, item_system: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    context = dict(payload.get("range_context") or {})
    if not context:
        return {}
    distance = float(context.get("distance", 0) or 0)
    range_data = _activity_range_data(activity, item_system=item_system)
    if not range_data:
        return {
            "distance": distance,
            "units": str(context.get("units") or "ft"),
            "checked": False,
        }
    mode = _range_mode(range_data)
    normal = _range_value(range_data, "value", "normal", "range", "reach")
    long = _range_value(range_data, "long", "max")
    if mode in {"melee", "touch"} and normal <= 0:
        normal = 5
    if mode == "self":
        normal = 0
        if distance > 0:
            raise ValueError(f"target is out of range ({distance:g} ft > self)")
    maximum = long if long > 0 else normal
    if maximum > 0 and distance > maximum:
        raise ValueError(f"target is out of range ({distance:g} ft > {maximum:g} ft)")
    disadvantage = bool(long > 0 and normal > 0 and distance > normal)
    return {
        "distance": distance,
        "units": str(context.get("units") or "ft"),
        "mode": mode,
        "normal": normal,
        "long": long,
        "maximum": maximum,
        "disadvantage": disadvantage,
        "actor_token_id": context.get("actor_token_id"),
        "target_token_id": context.get("target_token_id"),
        "scene_id": context.get("scene_id"),
        "checked": True,
    }


def _activity_range_data(activity, *, item_system: dict[str, Any]) -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    for candidate in (
        item_system.get("range"),
        getattr(activity, "range", None),
        getattr(activity, "system", {}).get("range") if isinstance(getattr(activity, "system", {}), dict) else None,
    ):
        if isinstance(candidate, dict):
            values.append(candidate)
    merged: dict[str, Any] = {}
    for value in values:
        merged.update({key: item for key, item in value.items() if item not in (None, "")})
    return merged


def _range_mode(range_data: dict[str, Any]) -> str:
    value = str(
        range_data.get("type")
        or range_data.get("mode")
        or range_data.get("units")
        or range_data.get("unit")
        or ""
    ).strip().lower()
    if value in {"touch", "self", "melee"}:
        return value
    if range_data.get("reach") not in (None, ""):
        return "melee"
    if range_data.get("long") not in (None, ""):
        return "ranged"
    return "ranged" if _range_value(range_data, "value", "normal", "range") > 5 else "melee"


def _range_value(range_data: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = range_data.get(key)
        if isinstance(value, dict):
            value = value.get("value", value.get("range", value.get("distance")))
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _require_actor_can_use_activity(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
    activity,
) -> None:
    activation = str((getattr(activity, "activation", {}) or {}).get("type") or "free").lower()
    if activation in {"free", "none", ""}:
        return
    statuses = _actor_statuses(documents, campaign_id=campaign_id, actor=actor)
    if statuses & _condition_effects("cannotAct"):
        raise ValueError(f"{actor.name} is incapacitated and cannot take actions or reactions")


def _attack_roll_context(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
    target,
    range_result: dict[str, Any],
    advantage: bool,
    disadvantage: bool,
) -> dict[str, Any]:
    advantage_sources: list[str] = ["payload"] if advantage else []
    disadvantage_sources: list[str] = ["payload"] if disadvantage else []
    attacker_statuses = _actor_statuses(documents, campaign_id=campaign_id, actor=actor)
    target_statuses = _actor_statuses(documents, campaign_id=campaign_id, actor=target)

    for status in sorted(attacker_statuses & _condition_effects("attackerAttackAdvantage")):
        advantage_sources.append(f"attacker:{status}")
    for status in sorted(target_statuses & _condition_effects("attacksAgainstAdvantage")):
        advantage_sources.append(f"target:{status}")
    if target_statuses & _condition_effects("meleeAttacksAgainstAdvantage"):
        if _attacker_within_5_feet(range_result):
            advantage_sources.append("target:prone:within_5_ft")
        else:
            disadvantage_sources.append("target:prone:beyond_5_ft")

    for status in sorted(attacker_statuses & _condition_effects("attackDisadvantage")):
        disadvantage_sources.append(f"attacker:{status}")
    for status in sorted(target_statuses & _condition_effects("attacksAgainstDisadvantage")):
        disadvantage_sources.append(f"target:{status}")
    if range_result.get("disadvantage"):
        disadvantage_sources.append("range:long")

    return {
        "advantage": bool(advantage_sources),
        "disadvantage": bool(disadvantage_sources),
        "advantage_sources": advantage_sources,
        "disadvantage_sources": disadvantage_sources,
    }


def _attacker_within_5_feet(range_result: dict[str, Any]) -> bool:
    if "distance" in range_result:
        return float(range_result.get("distance", 999) or 999) <= 5
    mode = str(range_result.get("mode") or "").lower()
    return mode in {"melee", "touch"}


def _actor_statuses(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
) -> set[str]:
    values = set(str(item) for item in (getattr(actor, "derived", {}) or {}).get("statuses") or [])
    for effect in documents.list_effects(campaign_id, actor_id=actor.id):
        if effect.disabled or effect.suppressed:
            continue
        values.update(str(item) for item in effect.statuses)
    return {item.strip().lower().replace("-", "_").replace(" ", "_") for item in values if item}


def _condition_effects(key: str) -> set[str]:
    values = get_ruleset().get("conditionEffects", {}).get(key) or []
    return {str(item).strip().lower().replace("-", "_").replace(" ", "_") for item in values}


def _execute_damage(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor,
    item,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("damage activity requires target-id")
    amount = payload.get("amount")
    rolled = None
    roll_data = _roll_data(actor, item, activity, payload)
    damage_spec = _damage_spec(activity, payload, item_system=dict(item.system or {}), roll_data=roll_data)
    if amount is None:
        expression = str(damage_spec["expression"] or "")
        if not expression:
            raise ValueError("damage activity requires amount or damage expression")
        rolled = roll(_resolve_formula(expression, roll_data))
        amount = rolled.total
    damage_result = apply_actor_damage(
        documents,
        campaign_id=campaign_id,
        actor_id=target_id,
        amount=int(amount),
        damage_type=str(payload.get("damage_type") or damage_spec["damage_type"]),
        source=activity.name,
    )
    result = {"type": "damage", **damage_result["damage"]}
    if rolled:
        result["roll"] = {
            "expression": rolled.expression,
            "total": rolled.total,
            "rolls": list(rolled.rolls),
            "detail": rolled.detail,
            "parts": damage_spec["parts"],
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
    actor,
    item,
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
        expression = str(
            _healing_expression(
                activity,
                payload,
                item_system=dict(item.system or {}),
                roll_data=_roll_data(actor, item, activity, payload),
            )
            or ""
        )
        if not expression:
            raise ValueError("heal activity requires amount or healing expression")
        rolled = roll(_resolve_formula(expression, _roll_data(actor, item, activity, payload)))
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
    actor,
    item,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not target_id:
        raise ValueError("save activity requires target-id")
    roll_data = _roll_data(actor, item, activity, payload)
    item_system = dict(getattr(item, "system", {}) or {})
    save_data = dict(activity.system.get("save") or item_system.get("dc") or {})
    dc_data = save_data.get("dc") if isinstance(save_data.get("dc"), dict) else {}
    dc_type = save_data.get("dc_type") if isinstance(save_data.get("dc_type"), dict) else {}
    dc = _formula_int(
        payload.get("dc")
        or activity.system.get("dc")
        or dc_data.get("formula")
        or dc_data.get("value")
        or _default_spell_save_dc(actor, item, activity),
        roll_data,
    )
    ability = str(
        payload.get("ability")
        or activity.system.get("ability")
        or save_data.get("ability")
        or dc_type.get("index")
        or "dex"
    )
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
    damage_spec = _damage_spec(activity, payload, item_system=dict(item.system or {}), roll_data=roll_data)
    if damage_spec["expression"]:
        rolled = roll(_resolve_formula(str(damage_spec["expression"]), roll_data))
        on_save = str(damage_spec["on_save"] or "").lower()
        amount = rolled.total // 2 if result["success"] and on_save in {"", "half"} else rolled.total
        if result["success"] and on_save in {"none", "false"}:
            amount = 0
        damage_result = apply_actor_damage(
            documents,
            campaign_id=campaign_id,
            actor_id=target_id,
            amount=amount,
            damage_type=str(payload.get("damage_type") or damage_spec["damage_type"]),
            source=activity.name,
        )
        result["damage_roll"] = {
            "expression": rolled.expression,
            "total": rolled.total,
            "applied": amount,
            "rolls": list(rolled.rolls),
            "detail": rolled.detail,
            "parts": damage_spec["parts"],
        }
        result["damage"] = damage_result["damage"]
        pending.extend(damage_result.get("pending", []))
        messages.extend(damage_result.get("messages", []))
    return {"result": result, "pending": pending, "messages": messages}


def _execute_check(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    target_id: str | None,
    activity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    check_data = dict(activity.system.get("check") or {})
    ability = payload.get("ability") or activity.system.get("ability") or check_data.get("ability")
    skill = payload.get("skill") or activity.system.get("skill") or check_data.get("skill")
    dc = int(payload.get("dc") or activity.system.get("dc") or check_data.get("dc") or 10)
    actor_roll = roll_actor_d20(
        documents,
        campaign_id=campaign_id,
        actor_id=actor_id,
        roll_type="skill" if skill else "ability",
        dc=dc,
        ability=str(ability or ""),
        skill=str(skill or ""),
        bonus=int(payload.get("bonus", 0) or 0),
        advantage=bool(payload.get("advantage", False)),
        disadvantage=bool(payload.get("disadvantage", False)),
        source=activity.name,
    )
    result: dict[str, Any] = {
        "type": "check",
        "actor": actor_roll["roll"],
        "dc": dc,
        "success": bool(actor_roll["roll"]["success"]),
    }
    messages = list(actor_roll.get("messages", []))
    contest = dict(payload.get("contest") or activity.system.get("contest") or {})
    if target_id and contest:
        target_ability = contest.get("ability") or payload.get("target_ability") or ability
        target_skill = contest.get("skill") or payload.get("target_skill")
        target_roll = roll_actor_d20(
            documents,
            campaign_id=campaign_id,
            actor_id=target_id,
            roll_type="skill" if target_skill else "ability",
            dc=0,
            ability=str(target_ability or ""),
            skill=str(target_skill or ""),
            bonus=int(contest.get("bonus", payload.get("target_bonus", 0)) or 0),
            advantage=bool(contest.get("advantage", False)),
            disadvantage=bool(contest.get("disadvantage", False)),
            source=f"Contest: {activity.name}",
        )
        result["target"] = target_roll["roll"]
        result["success"] = int(actor_roll["roll"]["total"]) >= int(target_roll["roll"]["total"])
        messages.extend(target_roll.get("messages", []))
    return {"result": result, "pending": [], "messages": messages}


def _actor_ac(system: dict[str, Any], derived: dict[str, Any]) -> int:
    effective = dict((derived or {}).get("effective_system") or system or {})
    ac = effective.get("attributes", {}).get("ac", 10)
    if isinstance(ac, dict):
        return int(ac.get("value", 10))
    return int(ac or 10)


def _damage_spec(
    activity,
    payload: dict[str, Any],
    *,
    item_system: dict[str, Any] | None = None,
    roll_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_system = dict(item_system or {})
    roll_data = dict(roll_data or {})
    if payload.get("damage"):
        return {
            "expression": str(payload["damage"]),
            "damage_type": _damage_type(payload.get("damage_type") or activity.system.get("damage_type") or ""),
            "on_save": activity.system.get("damage", {}).get("onSave") if isinstance(activity.system.get("damage"), dict) else None,
            "parts": [],
        }
    damage = activity.system.get("damage") or item_system.get("damage")
    if isinstance(damage, str):
        return {
            "expression": damage,
            "damage_type": _damage_type(activity.system.get("damage_type") or ""),
            "on_save": None,
            "parts": [],
        }
    if not isinstance(damage, dict):
        return {"expression": "", "damage_type": "", "on_save": None, "parts": []}
    scaled = _scaled_spell_expression(damage, roll_data)
    if scaled:
        return {
            "expression": scaled,
            "damage_type": _damage_type(activity.system.get("damage_type") or damage.get("damage_type")),
            "on_save": damage.get("onSave") or damage.get("dc_success") or item_system.get("dc", {}).get("dc_success"),
            "parts": [],
        }
    expressions = []
    parts_summary = []
    damage_type = _damage_type(activity.system.get("damage_type") or damage.get("damage_type"))
    for part in damage.get("parts") or []:
        if not isinstance(part, dict):
            continue
        expression = _part_formula(part)
        if not expression:
            continue
        expressions.append(expression)
        types = [str(item) for item in part.get("types") or [] if item]
        if not damage_type and types:
            damage_type = types[0]
        parts_summary.append({"formula": expression, "types": types})
    return {
        "expression": "+".join(expressions),
        "damage_type": damage_type,
        "on_save": damage.get("onSave"),
        "parts": parts_summary,
    }


def _healing_expression(
    activity,
    payload: dict[str, Any],
    *,
    item_system: dict[str, Any] | None = None,
    roll_data: dict[str, Any] | None = None,
) -> str:
    if payload.get("healing"):
        return str(payload["healing"])
    item_system = dict(item_system or {})
    healing = activity.system.get("healing") or item_system.get("healing")
    if isinstance(healing, str):
        return healing
    slot_scaled = _scaled_spell_expression(
        activity.system.get("heal_at_slot_level") or item_system.get("heal_at_slot_level") or {},
        dict(roll_data or {}),
    )
    if slot_scaled:
        return slot_scaled
    if not isinstance(healing, dict):
        return ""
    custom = dict(healing.get("custom") or {})
    if custom.get("enabled") and custom.get("formula") not in (None, ""):
        base = str(custom.get("formula"))
    else:
        base = _part_formula(healing)
    scaling = dict(healing.get("scaling") or {})
    scaling_formula = str(scaling.get("formula") or "")
    if scaling_formula:
        return "+".join(part for part in (base, scaling_formula) if part)
    return base


def _part_formula(part: dict[str, Any]) -> str:
    custom = dict(part.get("custom") or {})
    if custom.get("enabled") and custom.get("formula") not in (None, ""):
        return str(custom.get("formula"))
    number = part.get("number")
    denomination = part.get("denomination")
    bonus = str(part.get("bonus") or "")
    if number not in (None, "") and denomination not in (None, ""):
        base = f"{number}d{denomination}"
    else:
        base = ""
    return "+".join(item for item in (base, bonus) if item)


def _scaled_spell_expression(data: dict[str, Any], roll_data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    character_level = int(roll_data.get("level", 1) or 1)
    spell_level = int(roll_data.get("spell", {}).get("level", 0) or 0)
    if data.get("damage_at_character_level"):
        return _scaled_table_value(data["damage_at_character_level"], character_level)
    if data.get("damage_at_slot_level"):
        return _scaled_table_value(data["damage_at_slot_level"], spell_level)
    if all(str(key).isdigit() for key in data):
        return _scaled_table_value(data, spell_level)
    return ""


def _scaled_table_value(table: dict[str, Any], level: int) -> str:
    candidates = sorted((int(key), str(value)) for key, value in table.items() if str(key).isdigit())
    value = ""
    for threshold, expression in candidates:
        if level >= threshold:
            value = expression
    return value


def _damage_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("index") or value.get("name") or "")
    return str(value or "")


_ROLL_REF = re.compile(r"@(?P<path>[A-Za-z0-9_.-]+)")
_SAFE_FORMULA = re.compile(r"^[0-9+\-*/().,a-zA-Z_ ]+$")
_DICE_COUNT_EXPR = re.compile(r"\((?P<expr>[0-9+\-*/().,a-zA-Z_ ]+)\)d(?P<sides>\d+)", re.I)
_DAMAGE_FLAVOR = re.compile(r"\[[^\]]+\]")


def _roll_data(actor, item, activity, payload: dict[str, Any]) -> dict[str, Any]:
    system = dict((getattr(actor, "derived", None) or {}).get("effective_system") or getattr(actor, "system", {}) or {})
    item_system = dict(getattr(item, "system", {}) or {})
    activity_system = dict(getattr(activity, "system", {}) or {})
    spell_ability = _spellcasting_ability(system, item_system, activity_system)
    ability = str(
        payload.get("ability")
        or activity_system.get("ability")
        or item_system.get("ability")
        or (spell_ability if getattr(item, "item_type", "") == "spell" else "str")
    )
    base_spell_level = int(
        activity.system.get("level")
        or activity.system.get("spell", {}).get("level")
        or item_system.get("level")
        or item_system.get("spell", {}).get("level")
        or 0
    )
    spell_level = int(payload.get("spell_level") or base_spell_level or 0)
    actor_level = int(system.get("level") or system.get("details", {}).get("level") or 1)
    return {
        "level": actor_level,
        "prof": system.get("attributes", {}).get("prof", 2),
        "mod": _ability_mod(system, ability),
        "abilities": _ability_roll_data(system),
        "classes": {
            key: {"levels": value} for key, value in dict(system.get("class_levels") or {}).items()
        },
        "item": {
            "level": item_system.get("level", item_system.get("spell", {}).get("level", 0)),
            "uses": dict(getattr(activity, "uses", {}) or {}),
            "system": item_system,
        },
        "spell": {"level": spell_level, "base_level": base_spell_level},
        "spellcasting": {
            "ability": spell_ability,
            "mod": _ability_mod(system, spell_ability),
            "attack": _default_spell_attack_bonus(actor, item, activity),
            "dc": _default_spell_save_dc(actor, item, activity),
        },
        "activity": {
            "uses": dict(getattr(activity, "uses", {}) or {}),
            "system": dict(getattr(activity, "system", {}) or {}),
        },
        "payload": payload,
    }


def _default_spell_attack_bonus(actor, item, activity) -> int:
    if getattr(item, "item_type", "") != "spell" and getattr(activity, "activity_type", "") != "cast":
        return 0
    system = dict((getattr(actor, "derived", None) or {}).get("effective_system") or getattr(actor, "system", {}) or {})
    item_system = dict(getattr(item, "system", {}) or {})
    ability = _spellcasting_ability(system, item_system, dict(getattr(activity, "system", {}) or {}))
    return int(system.get("attributes", {}).get("prof", 2) or 2) + _ability_mod(system, ability) + _spell_bonus(system, "attack")


def _default_spell_save_dc(actor, item, activity) -> int:
    if getattr(item, "item_type", "") != "spell" and getattr(activity, "activity_type", "") != "cast":
        return 10
    system = dict((getattr(actor, "derived", None) or {}).get("effective_system") or getattr(actor, "system", {}) or {})
    item_system = dict(getattr(item, "system", {}) or {})
    ability = _spellcasting_ability(system, item_system, dict(getattr(activity, "system", {}) or {}))
    return 8 + int(system.get("attributes", {}).get("prof", 2) or 2) + _ability_mod(system, ability) + _spell_bonus(system, "dc")


def _spellcasting_ability(
    system: dict[str, Any],
    item_system: dict[str, Any],
    activity_system: dict[str, Any] | None = None,
) -> str:
    activity_system = dict(activity_system or {})
    attributes = dict(system.get("attributes") or {})
    spell_attr = attributes.get("spell") if isinstance(attributes.get("spell"), dict) else {}
    spellcasting = system.get("spellcasting") if isinstance(system.get("spellcasting"), dict) else {}
    for candidate in (
        activity_system.get("ability"),
        item_system.get("ability"),
        spell_attr.get("ability"),
        attributes.get("spellcasting"),
        spellcasting.get("ability"),
    ):
        if str(candidate or "") in {"str", "dex", "con", "int", "wis", "cha"}:
            return str(candidate)
    return "int"


def _spell_bonus(system: dict[str, Any], key: str) -> int:
    attributes = dict(system.get("attributes") or {})
    spell = attributes.get("spell")
    if isinstance(spell, dict):
        value = spell.get(key, 0)
        if isinstance(value, dict):
            value = value.get("value", value.get("bonus", 0))
        return int(value or 0)
    return 0


def _resolve_formula(expression: str, data: dict[str, Any]) -> str:
    resolved = _ROLL_REF.sub(lambda match: str(_lookup_roll_data(data, match.group("path"))), expression)
    resolved = _DAMAGE_FLAVOR.sub("", resolved)
    resolved = resolved.replace(" ", "")
    resolved = _DICE_COUNT_EXPR.sub(
        lambda match: f"{int(_eval_formula(match.group('expr')))}d{match.group('sides')}",
        resolved,
    )
    if "d" not in resolved.lower() and _SAFE_FORMULA.match(resolved):
        return str(int(_eval_formula(resolved)))
    return resolved


def _critical_expression(expression: str) -> str:
    return re.sub(
        r"(?<!\d)(?P<count>\d*)d(?P<sides>\d+)",
        lambda match: f"{int(match.group('count') or 1) * 2}d{match.group('sides')}",
        expression,
        flags=re.I,
    )


def _formula_int(value: Any, data: dict[str, Any]) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return int(_eval_formula(_resolve_formula(str(value), data)))


def _lookup_roll_data(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return 0
    return current


def _eval_formula(expression: str) -> int | float:
    if not _SAFE_FORMULA.match(expression):
        raise ValueError(f"unsupported formula expression: {expression}")
    return eval(
        expression,
        {"__builtins__": {}},
        {
            "ceil": __import__("math").ceil,
            "floor": __import__("math").floor,
            "max": max,
            "min": min,
            "round": round,
        },
    )


def _ability_roll_data(system: dict[str, Any]) -> dict[str, Any]:
    values = {}
    for key, value in dict(system.get("abilities") or {}).items():
        score = int(value.get("value", value) if isinstance(value, dict) else value)
        values[key] = {"value": score, "mod": (score - 10) // 2}
    return values


def _ability_mod(system: dict[str, Any], ability: str) -> int:
    value = dict(system.get("abilities") or {}).get(ability, 10)
    score = int(value.get("value", value) if isinstance(value, dict) else value)
    return (score - 10) // 2


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
