"""Ready action runtime for AI-DM combat flow."""

from __future__ import annotations

from typing import Any
from uuid import uuid4


def set_ready_action(
    state: dict[str, Any],
    *,
    actor_id: str,
    trigger: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = dict(state.get("runtime") or {})
    budgets = dict(runtime.get("turn_budgets") or {})
    budget = _budget_for(budgets.get(actor_id))
    if budget["main_action"] <= 0:
        raise ValueError("ready action requires an available action")
    budget["main_action"] -= 1
    budgets[actor_id] = budget
    ready = {
        "id": f"ready-{uuid4().hex}",
        "actor_id": actor_id,
        "trigger": trigger,
        "payload": dict(payload),
        "status": "readied",
        "requires_reaction": True,
    }
    readied = [item for item in runtime.get("readied") or [] if item.get("actor_id") != actor_id]
    readied.append(ready)
    runtime["turn_budgets"] = budgets
    runtime["readied"] = readied
    state["runtime"] = runtime
    return state, {"ready": ready, "turn_budget": budget}


def trigger_ready_action(
    state: dict[str, Any],
    *,
    ready_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = dict(state.get("runtime") or {})
    readied = list(runtime.get("readied") or [])
    updated = []
    triggered = None
    for item in readied:
        value = dict(item)
        if value.get("id") == ready_id and value.get("status") == "readied":
            budgets = dict(runtime.get("turn_budgets") or {})
            budget = _budget_for(budgets.get(value["actor_id"]))
            if budget["reaction"] <= 0:
                raise ValueError("ready trigger requires an available reaction")
            budget["reaction"] -= 1
            budgets[value["actor_id"]] = budget
            runtime["turn_budgets"] = budgets
            value["status"] = "triggered"
            value["turn_budget"] = budget
            triggered = value
        updated.append(value)
    if triggered is None:
        raise LookupError(ready_id)
    runtime["readied"] = updated
    state["runtime"] = runtime
    return state, {"ready": triggered}


def clear_ready_actions(state: dict[str, Any], *, actor_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = dict(state.get("runtime") or {})
    readied = list(runtime.get("readied") or [])
    if actor_id:
        kept = [item for item in readied if item.get("actor_id") != actor_id]
    else:
        kept = []
    removed = [item for item in readied if item not in kept]
    runtime["readied"] = kept
    state["runtime"] = runtime
    return state, {"removed": removed}


def _budget_for(value: Any) -> dict[str, int]:
    budget = dict(value or {})
    budget["main_action"] = int(budget.get("main_action", 1))
    budget["bonus_action"] = int(budget.get("bonus_action", 1))
    budget["reaction"] = int(budget.get("reaction", 1))
    return budget
