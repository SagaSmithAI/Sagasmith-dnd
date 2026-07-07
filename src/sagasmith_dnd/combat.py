"""Structured D&D combat state helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sagasmith_dnd.engine import roll, roll_d20
from sagasmith_dnd.rulesets import get_ruleset


def start_combat(
    *,
    name: str,
    participants: list[dict[str, Any]],
    scene_id: str | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not participants:
        raise ValueError("combat requires at least one participant")
    combatants = [_normalize_combatant(item, index) for index, item in enumerate(participants)]
    combatants.sort(key=lambda item: (-int(item["initiative"]), item["name"], item["id"]))
    return {
        "active": True,
        "name": name or "Combat",
        "scene_id": scene_id,
        "ruleset": "dnd5e-2014",
        "round": 1,
        "turn": 0,
        "environment": dict(environment or {}),
        "participants": combatants,
        "reaction_windows": [],
        "effects": [],
        "log": [],
    }


def combat_status(combat: dict[str, Any] | None) -> dict[str, Any] | None:
    if not combat:
        return None
    value = deepcopy(combat)
    active = _current(value)
    value["current"] = active
    value["legal_actions"] = _legal_actions(active) if active else []
    value["legal_action_details"] = _legal_action_details(active) if active else []
    return value


def execute_activity(
    combat: dict[str, Any],
    *,
    actor_id: str,
    activity_id: str,
    target_id: str | None = None,
    payment: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    actor = _participant(value, actor_id)
    ruleset = get_ruleset(value.get("ruleset"))
    activity = dict(ruleset["activities"].get(activity_id) or {})
    if not activity:
        raise ValueError(f"unknown activity: {activity_id}")
    if not _has_required_feature(actor, activity.get("requires_feature")):
        raise ValueError(f"{actor['name']} lacks required feature for {activity_id}")
    payment = payment or _default_payment(activity)
    _spend_payment(actor, payment)
    _spend_uses(actor, activity)

    payload = dict(payload or {})
    result: dict[str, Any] = {
        "type": "activity",
        "activity": activity_id,
        "activity_type": activity.get("type", "utility"),
        "activation": activity.get("activation"),
        "payment": payment,
        "actor": actor["id"],
        "target": target_id,
    }
    if activity_id == "action_surge":
        grant = activity.get("grant") or {}
        budget = _budget(actor)
        budget["extra_actions"] = int(budget.get("extra_actions", 0)) + int(grant.get("extra_actions", 0))
        result["turn_budget"] = dict(budget)
    elif activity_id == "second_wind":
        fighter_level = int(payload.get("fighter_level", actor.get("class_levels", {}).get("fighter", 1)))
        amount = roll(f"1d10+{fighter_level}")
        value, healed = heal(
            value,
            target_id=target_id or actor["id"],
            amount=amount.total,
            source="second_wind",
        )
        result["healing"] = healed
    elif activity.get("type") == "attack":
        if target_id is None:
            raise ValueError("attack activity requires target")
        value, attack_result = attack(
            value,
            actor_id=actor["id"],
            target_id=target_id,
            attack_bonus=int(payload.get("attack_bonus", payload.get("bonus", 0))),
            damage_expression=payload.get("damage") or payload.get("damage_expression"),
            damage_type=payload.get("damage_type"),
            advantage=bool(payload.get("advantage", False)),
            disadvantage=bool(payload.get("disadvantage", False)),
            label=payload.get("label", activity_id),
            consume_action=False,
        )
        result["attack"] = attack_result
    elif activity.get("type") == "effect":
        effect = {
            "id": f"effect-{len(value.get('effects') or []) + 1}",
            "source": activity_id,
            "actor": actor["id"],
            "target": target_id or actor["id"],
            **dict(activity.get("effect") or {}),
        }
        value.setdefault("effects", []).append(effect)
        result["effect"] = effect
    _append_log(value, result)
    return value, result


def apply_effect(
    combat: dict[str, Any],
    *,
    target_id: str,
    effect: dict[str, Any],
    source: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    _participant(value, target_id)
    entry = {
        "id": str(effect.get("id") or f"effect-{len(value.get('effects') or []) + 1}"),
        "source": source or effect.get("source", "manual"),
        "target": target_id,
        **dict(effect),
    }
    value.setdefault("effects", []).append(entry)
    result = {"type": "effect.add", "effect": entry}
    _append_log(value, result)
    return value, result


def remove_effect(
    combat: dict[str, Any],
    *,
    effect_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    effects = list(value.get("effects") or [])
    kept = [item for item in effects if item.get("id") != effect_id]
    if len(kept) == len(effects):
        raise ValueError(f"effect not found: {effect_id}")
    value["effects"] = kept
    result = {"type": "effect.remove", "effect": effect_id}
    _append_log(value, result)
    return value, result


def recover_period(
    combat: dict[str, Any],
    *,
    period: str,
    actor_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    targets = [_participant(value, actor_id)] if actor_id else list(value.get("participants") or [])
    recovered: list[dict[str, Any]] = []
    for combatant in targets:
        for resource_id, resource in (combatant.get("resources") or {}).items():
            if period in _resource_recovery_periods(resource_id):
                before = int(resource.get("spent", 0))
                resource["spent"] = 0
                if before:
                    recovered.append({"actor": combatant["id"], "resource": resource_id, "spent": before})
    _tick_durations(value, period, actor_id)
    result = {"type": "period.recover", "period": period, "recovered": recovered}
    _append_log(value, result)
    return value, result


def attack(
    combat: dict[str, Any],
    *,
    actor_id: str,
    target_id: str,
    attack_bonus: int = 0,
    damage_expression: str | None = None,
    damage_type: str | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    label: str = "",
    consume_action: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    actor = _participant(value, actor_id)
    target = _participant(value, target_id)
    if consume_action and not _consume_attack_budget(actor):
        raise ValueError(f"{actor['name']} has no action available")
    die = roll_d20(advantage=advantage, disadvantage=disadvantage)
    total = int(die["natural"]) + int(attack_bonus)
    hit = bool(die["critical"] or (not die["fumble"] and total >= int(target["ac"])))
    result: dict[str, Any] = {
        "type": "attack",
        "label": label,
        "actor": actor["id"],
        "target": target["id"],
        "target_ac": target["ac"],
        "attack_bonus": attack_bonus,
        "roll": die,
        "total": total,
        "hit": hit,
    }
    if consume_action:
        actor["action_available"] = False
    if hit and damage_expression:
        damage = _damage_roll(damage_expression, critical=bool(die["critical"]))
        value, damage_result = apply_damage(
            value,
            target_id=target["id"],
            amount=damage["total"],
            damage_type=damage_type or "",
            source=label or "attack",
            roll_result=damage,
        )
        result["damage"] = damage_result
    _append_log(value, result)
    return value, result


def apply_damage(
    combat: dict[str, Any],
    *,
    target_id: str,
    amount: int,
    damage_type: str = "",
    source: str = "",
    roll_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    target = _participant(value, target_id)
    previous_hp = int(target["hp"])
    target["hp"] = max(0, previous_hp - max(0, int(amount)))
    if target["hp"] == 0 and "unconscious" not in target["conditions"]:
        target["conditions"].append("unconscious")
    result = {
        "type": "damage",
        "target": target["id"],
        "amount": max(0, int(amount)),
        "damage_type": damage_type,
        "source": source,
        "roll": roll_result,
        "previous_hp": previous_hp,
        "hp": target["hp"],
        "conditions": list(target["conditions"]),
    }
    _append_log(value, result)
    return value, result


def heal(
    combat: dict[str, Any],
    *,
    target_id: str,
    amount: int,
    source: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    target = _participant(value, target_id)
    previous_hp = int(target["hp"])
    target["hp"] = min(int(target["max_hp"]), previous_hp + max(0, int(amount)))
    if target["hp"] > 0 and "unconscious" in target["conditions"]:
        target["conditions"] = [item for item in target["conditions"] if item != "unconscious"]
    result = {
        "type": "heal",
        "target": target["id"],
        "amount": max(0, int(amount)),
        "source": source,
        "previous_hp": previous_hp,
        "hp": target["hp"],
        "conditions": list(target["conditions"]),
    }
    _append_log(value, result)
    return value, result


def set_condition(
    combat: dict[str, Any],
    *,
    target_id: str,
    condition: str,
    present: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    target = _participant(value, target_id)
    normalized = condition.strip().lower()
    if not normalized:
        raise ValueError("condition is required")
    conditions = list(target.get("conditions") or [])
    if present and normalized not in conditions:
        conditions.append(normalized)
    if not present:
        conditions = [item for item in conditions if item != normalized]
    target["conditions"] = conditions
    result = {
        "type": "condition.add" if present else "condition.remove",
        "target": target["id"],
        "condition": normalized,
        "conditions": conditions,
    }
    _append_log(value, result)
    return value, result


def end_turn(combat: dict[str, Any], *, actor_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    current = _current(value)
    if not current:
        raise ValueError("combat has no active participant")
    if actor_id and current["id"] != actor_id:
        raise ValueError(f"it is {current['id']}'s turn, not {actor_id}")
    _tick_durations(value, "turn_end", current["id"])
    current["reaction_available"] = True
    _budget(current)["reactions"] = 1
    value["turn"] = int(value.get("turn", 0)) + 1
    if value["turn"] >= len(value["participants"]):
        value["turn"] = 0
        value["round"] = int(value.get("round", 1)) + 1
    next_actor = _current(value)
    if next_actor:
        _reset_turn_resources(next_actor)
        _tick_durations(value, "turn_start", next_actor["id"])
    result = {
        "type": "end_turn",
        "ended": current["id"],
        "round": value["round"],
        "turn": value["turn"],
        "current": next_actor["id"] if next_actor else None,
    }
    _append_log(value, result)
    return value, result


def _normalize_combatant(item: dict[str, Any], index: int) -> dict[str, Any]:
    sheet = dict(item.get("sheet") or {})
    name = str(item.get("name") or sheet.get("name") or f"Combatant {index + 1}")
    dexterity = int((sheet.get("abilities") or {}).get("dexterity", 10))
    initiative_bonus = int(item.get("initiative_bonus", (dexterity - 10) // 2))
    initiative = item.get("initiative")
    if initiative is None:
        initiative = roll_d20()["natural"] + initiative_bonus
    hp = int(item.get("hp", sheet.get("hit_points", 1)))
    max_hp = int(item.get("max_hp", sheet.get("max_hit_points", hp)))
    speed = int(item.get("speed", sheet.get("speed", 30)))
    features = _normalize_features(item.get("features") or sheet.get("features") or [])
    class_levels = dict(item.get("class_levels") or sheet.get("class_levels") or {})
    attacks_per_action = int(item.get("attacks_per_action", sheet.get("attacks_per_action", 1)))
    if "extra-attack" in features or "extra_attack" in features:
        attacks_per_action = max(attacks_per_action, 2)
    return {
        "id": str(item.get("id") or item.get("character_id") or f"c{index + 1}-{uuid4().hex[:8]}"),
        "name": name,
        "kind": str(item.get("kind") or item.get("type") or "creature"),
        "character_id": item.get("character_id"),
        "actor_id": item.get("actor_id") or item.get("character_id"),
        "actor_type": item.get("actor_type") or item.get("kind") or item.get("type") or "character",
        "token_id": item.get("token_id"),
        "ac": int(item.get("ac", sheet.get("armor_class", 10))),
        "hp": max(0, hp),
        "max_hp": max(max_hp, hp, 1),
        "initiative": int(initiative),
        "initiative_bonus": initiative_bonus,
        "conditions": list(item.get("conditions") or []),
        "position": item.get("position", ""),
        "speed": speed,
        "movement_remaining": int(item.get("movement_remaining", speed)),
        "action_available": bool(item.get("action_available", True)),
        "bonus_action_available": bool(item.get("bonus_action_available", True)),
        "reaction_available": bool(item.get("reaction_available", True)),
        "features": sorted(features),
        "class_levels": class_levels,
        "resources": _normalize_resources(item, features),
        "attacks_per_action": attacks_per_action,
        "turn_budget": {
            "main_actions": 1,
            "bonus_actions": 1,
            "reactions": 1,
            "extra_actions": 0,
            "attack_budget": 0,
        },
    }


def _require_active(combat: dict[str, Any] | None) -> dict[str, Any]:
    if not combat or not combat.get("active"):
        raise ValueError("combat is not active")
    return deepcopy(combat)


def _participant(combat: dict[str, Any], participant_id: str) -> dict[str, Any]:
    for item in combat.get("participants") or []:
        if item.get("id") == participant_id or item.get("character_id") == participant_id:
            return item
    raise ValueError(f"combatant not found: {participant_id}")


def _current(combat: dict[str, Any]) -> dict[str, Any] | None:
    participants = combat.get("participants") or []
    if not participants:
        return None
    return participants[int(combat.get("turn", 0)) % len(participants)]


def _reset_turn_resources(combatant: dict[str, Any]) -> None:
    combatant["movement_remaining"] = int(combatant.get("speed", combatant.get("movement_remaining", 30)) or 30)
    combatant["action_available"] = True
    combatant["bonus_action_available"] = True
    budget = _budget(combatant)
    budget["main_actions"] = 1
    budget["bonus_actions"] = 1
    budget["extra_actions"] = 0
    budget["attack_budget"] = 0


def _legal_actions(combatant: dict[str, Any]) -> list[str]:
    if _has_condition_tag(combatant, "no_actions"):
        return ["move"] if not _has_condition_tag(combatant, "no_movement") else []
    actions = ["move"]
    budget = _budget(combatant)
    if budget.get("main_actions", 0) > 0 or budget.get("extra_actions", 0) > 0 or combatant.get("action_available", True):
        actions.extend(["attack", "dash", "disengage", "dodge", "help", "hide", "ready", "search", "use_object"])
    if budget.get("bonus_actions", 0) > 0 or combatant.get("bonus_action_available", True):
        actions.append("bonus_action")
        if _has_required_feature(combatant, "cunning-action"):
            actions.extend(["cunning_action_dash", "cunning_action_disengage", "cunning_action_hide"])
        if _has_required_feature(combatant, "second-wind"):
            actions.append("second_wind")
    if budget.get("reactions", 0) > 0 and not _has_condition_tag(combatant, "no_reactions"):
        actions.extend(["reaction", "opportunity_attack"])
    if _has_required_feature(combatant, "action-surge") and _resource_remaining(combatant, "action_surge") > 0:
        actions.append("action_surge")
    return actions


def _legal_action_details(combatant: dict[str, Any]) -> list[dict[str, Any]]:
    ruleset = get_ruleset()
    return [
        {
            "id": action_id,
            "definition": ruleset["activities"].get(action_id, {}),
            "payments": _payment_options(combatant, ruleset["activities"].get(action_id, {})),
        }
        for action_id in _legal_actions(combatant)
        if action_id in ruleset["activities"]
    ]


def _budget(combatant: dict[str, Any]) -> dict[str, int]:
    budget = combatant.setdefault("turn_budget", {})
    for key, default in {
        "main_actions": 1 if combatant.get("action_available", True) else 0,
        "bonus_actions": 1 if combatant.get("bonus_action_available", True) else 0,
        "reactions": 1 if combatant.get("reaction_available", True) else 0,
        "extra_actions": 0,
        "attack_budget": 0,
    }.items():
        budget[key] = int(budget.get(key, default) or 0)
    return budget


def _default_payment(activity: dict[str, Any]) -> str:
    activation = activity.get("activation")
    if activation == "action":
        return "main_action"
    if activation == "bonus":
        return "bonus_action"
    if activation == "reaction":
        return "reaction"
    return "free"


def _payment_options(combatant: dict[str, Any], activity: dict[str, Any]) -> list[str]:
    budget = _budget(combatant)
    activation = activity.get("activation")
    if activation == "action":
        values = []
        if budget.get("main_actions", 0) > 0:
            values.append("main_action")
        if budget.get("extra_actions", 0) > 0:
            values.append("extra_action")
        return values
    if activation == "bonus":
        return ["bonus_action"] if budget.get("bonus_actions", 0) > 0 else []
    if activation == "reaction":
        return ["reaction"] if budget.get("reactions", 0) > 0 else []
    return ["free"]


def _spend_payment(combatant: dict[str, Any], payment: str) -> None:
    budget = _budget(combatant)
    key = {
        "main_action": "main_actions",
        "bonus_action": "bonus_actions",
        "reaction": "reactions",
        "extra_action": "extra_actions",
        "free": "",
    }.get(payment)
    if key is None:
        raise ValueError(f"unknown payment: {payment}")
    if key:
        if budget.get(key, 0) <= 0:
            raise ValueError(f"{combatant['name']} cannot pay {payment}")
        budget[key] -= 1
        if key in {"main_actions", "extra_actions"}:
            budget["attack_budget"] = max(
                int(budget.get("attack_budget", 0)),
                int(combatant.get("attacks_per_action", 1)),
            )
        if key == "main_actions":
            combatant["action_available"] = False
        elif key == "bonus_actions":
            combatant["bonus_action_available"] = False
        elif key == "reactions":
            combatant["reaction_available"] = False


def _consume_attack_budget(combatant: dict[str, Any]) -> bool:
    budget = _budget(combatant)
    if budget.get("attack_budget", 0) > 0:
        budget["attack_budget"] -= 1
        return True
    if budget.get("main_actions", 0) > 0:
        _spend_payment(combatant, "main_action")
        budget["attack_budget"] = max(0, int(combatant.get("attacks_per_action", 1)) - 1)
        return True
    if budget.get("extra_actions", 0) > 0:
        _spend_payment(combatant, "extra_action")
        budget["attack_budget"] = max(0, int(combatant.get("attacks_per_action", 1)) - 1)
        return True
    return False


def _normalize_features(raw: Any) -> set[str]:
    if isinstance(raw, dict):
        raw = [key for key, enabled in raw.items() if enabled]
    return {str(item).strip().lower().replace("_", "-").replace(" ", "-") for item in raw or []}


def _normalize_resources(item: dict[str, Any], features: set[str]) -> dict[str, dict[str, int]]:
    resources = {key: dict(value) for key, value in dict(item.get("resources") or {}).items()}
    if "action-surge" in features:
        resources.setdefault("action_surge", {"spent": 0, "max": 1})
    if "second-wind" in features:
        resources.setdefault("second_wind", {"spent": 0, "max": 1})
    return resources


def _has_required_feature(combatant: dict[str, Any], feature: str | None) -> bool:
    if not feature:
        return True
    normalized = feature.strip().lower().replace("_", "-")
    return normalized in set(combatant.get("features") or [])


def _resource_remaining(combatant: dict[str, Any], resource: str) -> int:
    value = dict((combatant.get("resources") or {}).get(resource) or {})
    return max(0, int(value.get("max", 0)) - int(value.get("spent", 0)))


def _spend_uses(combatant: dict[str, Any], activity: dict[str, Any]) -> None:
    uses = activity.get("uses") or {}
    resource = uses.get("resource")
    if not resource:
        return
    resources = combatant.setdefault("resources", {})
    value = resources.setdefault(resource, {"spent": 0, "max": int(uses.get("cost", 1))})
    cost = int(uses.get("cost", 1))
    if int(value.get("spent", 0)) + cost > int(value.get("max", 0)):
        raise ValueError(f"{combatant['name']} has no {resource} uses remaining")
    value["spent"] = int(value.get("spent", 0)) + cost


def _resource_recovery_periods(resource_id: str) -> set[str]:
    if resource_id in {"action_surge", "second_wind"}:
        return {"short_rest", "long_rest"}
    return set()


def _has_condition_tag(combatant: dict[str, Any], tag: str) -> bool:
    ruleset = get_ruleset()
    tags: set[str] = set()
    conditions = set(combatant.get("conditions") or [])
    expanded = set(conditions)
    for condition in list(conditions):
        expanded.update(ruleset.get("conditions", {}).get(condition, {}).get("tags", []))
    for condition in expanded:
        tags.update(ruleset.get("conditions", {}).get(condition, {}).get("tags", []))
        tags.add(condition)
    return tag in tags


def _tick_durations(combat: dict[str, Any], period: str, actor_id: str | None = None) -> None:
    remaining = []
    for effect in combat.get("effects") or []:
        duration = dict(effect.get("duration") or {})
        if duration.get("period") not in {period, f"until_{period}"}:
            remaining.append(effect)
            continue
        if duration.get("anchor") == "self" and actor_id and effect.get("target") != actor_id:
            remaining.append(effect)
            continue
        _append_log(combat, {"type": "effect.expire", "effect": effect.get("id"), "period": period})
    combat["effects"] = remaining


def _damage_roll(expression: str, *, critical: bool) -> dict[str, Any]:
    expression_to_roll = _critical_expression(expression) if critical else expression
    result = roll(expression_to_roll)
    return {
        "expression": expression,
        "rolled_expression": expression_to_roll,
        "total": result.total,
        "rolls": list(result.rolls),
        "detail": result.detail,
        "critical": critical,
    }


def _critical_expression(expression: str) -> str:
    # Double only dice terms. Flat modifiers stay unchanged, matching common D&D handling.
    import re

    return re.sub(r"(?<!\d)(\d*)d(\d+)", lambda m: f"{int(m.group(1) or 1) * 2}d{m.group(2)}", expression)


def _append_log(combat: dict[str, Any], entry: dict[str, Any]) -> None:
    log = list(combat.get("log") or [])
    log.append(entry)
    combat["log"] = log[-100:]
