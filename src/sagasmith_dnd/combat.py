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
        "combatants": combatants,
        "reaction_windows": [],
        "effects": [],
        "log": [],
    }


def combat_status(combat: dict[str, Any] | None, *, runtime: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not combat:
        return None
    value = deepcopy(combat)
    value["combatants"] = list(value.get("combatants") or value.get("participants") or [])
    value.pop("participants", None)
    if runtime:
        _apply_runtime_budgets(value, runtime)
    active = _current(value)
    value["current"] = active
    value["legal_actions"] = _legal_actions(active) if active else []
    value["legal_action_details"] = _legal_action_details(active) if active else []
    pending = list((runtime or {}).get("pending") or [])
    if active:
        pending = [
            item for item in pending
            if item.get("status", "pending") == "pending" and item.get("actor_id") == active.get("actor_id", active.get("id"))
        ]
    value["pending_reactions"] = pending
    value["combat"] = {
        "active": value.get("active", False),
        "name": value.get("name", ""),
        "scene_id": value.get("scene_id"),
        "ruleset": value.get("ruleset"),
        "round": value.get("round", 1),
        "turn": value.get("turn", 0),
    }
    return value


def recover_period(
    combat: dict[str, Any],
    *,
    period: str,
    actor_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    targets = [_participant(value, actor_id)] if actor_id else list(_combatants(value))
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


def death_save(
    combat: dict[str, Any],
    *,
    target_id: str,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = _require_active(combat)
    target = _participant(value, target_id)
    if int(target.get("hp", 0)) > 0:
        raise ValueError(f"{target['name']} is not at 0 hit points")
    saves = dict(target.get("death_saves") or {"successes": 0, "failures": 0})
    die = roll_d20(advantage=advantage, disadvantage=disadvantage)
    if die["natural"] == 20:
        previous_hp = int(target.get("hp", 0))
        target["hp"] = 1
        target["conditions"] = [item for item in target.get("conditions", []) if item != "unconscious"]
        outcome = "revived"
        result = {
            "type": "death_save",
            "target": target["id"],
            "roll": die,
            "outcome": outcome,
            "previous_hp": previous_hp,
            "hp": target["hp"],
            "death_saves": {"successes": 0, "failures": 0},
        }
        target["death_saves"] = {"successes": 0, "failures": 0}
        _append_log(value, result)
        return value, result
    if die["natural"] == 1:
        saves["failures"] = int(saves.get("failures", 0)) + 2
    elif die["natural"] >= 10:
        saves["successes"] = int(saves.get("successes", 0)) + 1
    else:
        saves["failures"] = int(saves.get("failures", 0)) + 1
    outcome = "pending"
    if int(saves.get("successes", 0)) >= 3:
        outcome = "stable"
        conditions = set(target.get("conditions") or [])
        conditions.add("stable")
        target["conditions"] = sorted(conditions)
    if int(saves.get("failures", 0)) >= 3:
        outcome = "dead"
        conditions = set(target.get("conditions") or [])
        conditions.add("dead")
        target["conditions"] = sorted(conditions)
    target["death_saves"] = saves
    result = {
        "type": "death_save",
        "target": target["id"],
        "roll": die,
        "outcome": outcome,
        "death_saves": dict(saves),
        "conditions": list(target.get("conditions") or []),
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
    if value["turn"] >= len(_combatants(value)):
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
    for item in _combatants(combat):
        if participant_id in {item.get("id"), item.get("character_id"), item.get("actor_id"), item.get("token_id")}:
            return item
    raise ValueError(f"combatant not found: {participant_id}")


def _current(combat: dict[str, Any]) -> dict[str, Any] | None:
    combatants = _combatants(combat)
    if not combatants:
        return None
    return combatants[int(combat.get("turn", 0)) % len(combatants)]


def _combatants(combat: dict[str, Any]) -> list[dict[str, Any]]:
    return list(combat.get("combatants") or combat.get("participants") or [])


def _apply_runtime_budgets(combat: dict[str, Any], runtime: dict[str, Any]) -> None:
    budgets = dict(runtime.get("turn_budgets") or {})
    for combatant in combat.get("combatants") or []:
        actor_id = str(combatant.get("actor_id") or combatant.get("id") or "")
        budget = budgets.get(actor_id)
        if not isinstance(budget, dict):
            continue
        turn_budget = _budget(combatant)
        if "main_action" in budget:
            turn_budget["main_actions"] = int(budget.get("main_action", 0) or 0)
            combatant["action_available"] = turn_budget["main_actions"] > 0
        if "bonus_action" in budget:
            turn_budget["bonus_actions"] = int(budget.get("bonus_action", 0) or 0)
            combatant["bonus_action_available"] = turn_budget["bonus_actions"] > 0
        if "reaction" in budget:
            turn_budget["reactions"] = int(budget.get("reaction", 0) or 0)
            combatant["reaction_available"] = turn_budget["reactions"] > 0
        if "extra_action" in budget:
            turn_budget["extra_actions"] = int(budget.get("extra_action", 0) or 0)
        if "attack_budget" in budget:
            turn_budget["attack_budget"] = int(budget.get("attack_budget", 0) or 0)


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
    if budget.get("attack_budget", 0) > 0:
        actions.append("attack_budget")
    if budget.get("bonus_actions", 0) > 0 or combatant.get("bonus_action_available", True):
        actions.append("bonus_action")
    if budget.get("reactions", 0) > 0 and not _has_condition_tag(combatant, "no_reactions"):
        actions.extend(["reaction", "opportunity_attack"])
    return actions


def _legal_action_details(combatant: dict[str, Any]) -> list[dict[str, Any]]:
    ruleset = get_ruleset()
    details: list[dict[str, Any]] = []
    for action_id in _legal_actions(combatant):
        definition = dict(ruleset["activities"].get(action_id) or _runtime_action_definition(action_id))
        if not definition:
            continue
        details.append(
            {
                "id": action_id,
                "definition": definition,
                "payments": _payment_options(combatant, definition),
            }
        )
    return details


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


def _payment_options(combatant: dict[str, Any], activity: dict[str, Any]) -> list[str]:
    budget = _budget(combatant)
    activation = activity.get("activation")
    if activation == "attack_budget":
        return ["attack_budget"] if budget.get("attack_budget", 0) > 0 else []
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


def _runtime_action_definition(action_id: str) -> dict[str, Any]:
    definitions = {
        "bonus_action": {"activation": "bonus", "type": "runtime"},
        "reaction": {"activation": "reaction", "type": "runtime"},
        "opportunity_attack": {"activation": "reaction", "type": "runtime"},
        "attack_budget": {"activation": "attack_budget", "type": "runtime"},
    }
    return definitions.get(action_id, {})


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
