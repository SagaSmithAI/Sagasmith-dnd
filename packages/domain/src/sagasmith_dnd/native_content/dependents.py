from __future__ import annotations

import re
from typing import Any

from sagasmith_dnd.combat_engine import (
    _DEPENDENT_TURN_FIELDS,
    STEEL_DEFENDER_TURN_KIND,
    CombatEngineError,
    _condition_set,
    actor_id,
)
from sagasmith_dnd.conditions import (
    INCAPACITATING_STATE_IDS,
)

"Reviewed native content handlers; registered by the domain composition root."


def _dependent_turn_contract(
    actor: dict[str, Any], *, participant_ids: set[str], ruleset: str
) -> dict[str, Any] | None:
    raw = actor.get("dependent_turn")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _DEPENDENT_TURN_FIELDS:
        raise CombatEngineError("dependent turn authority has invalid fields")
    contract = {key: str(raw.get(key) or "").strip() for key in _DEPENDENT_TURN_FIELDS}
    if any((not value for value in contract.values())):
        raise CombatEngineError("dependent turn authority contains an empty field")
    if contract["kind"] != STEEL_DEFENDER_TURN_KIND or ruleset != "2014":
        raise CombatEngineError("dependent turn authority is incompatible with this ruleset")
    owner_id = contract["owner_actor_id"]
    dependent_id = actor_id(actor)
    if owner_id == dependent_id or owner_id not in participant_ids:
        raise CombatEngineError("a Steel Defender requires its owner in the encounter")
    if re.fullmatch("[0-9a-f]{64}", contract["reviewed_expression_hash"]) is None:
        raise CombatEngineError("dependent turn authority has an invalid reviewed hash")
    return contract


def _controlled_dependent(
    encounter: dict[str, Any], owner_actor_id: str, dependent_actor_id: str
) -> dict[str, Any]:
    dependent = next(
        (
            item
            for item in encounter.get("combatants", [])
            if str(item.get("actor_id") or "") == dependent_actor_id
        ),
        None,
    )
    contract = dict((dependent or {}).get("dependent_turn") or {})
    if (
        dependent is None
        or contract.get("kind") != STEEL_DEFENDER_TURN_KIND
        or contract.get("owner_actor_id") != owner_actor_id
    ):
        raise CombatEngineError("target is not this actor's active Steel Defender")
    if "dead" in _condition_set(dependent.get("conditions")):
        raise CombatEngineError("a dead Steel Defender cannot be commanded")
    return dependent


def _begin_dependent_turn(encounter: dict[str, Any], dependent: dict[str, Any]) -> None:
    contract = dict(dependent.get("dependent_turn") or {})
    if contract.get("kind") != STEEL_DEFENDER_TURN_KIND:
        return
    owner_id = str(contract.get("owner_actor_id") or "")
    owner = next(
        (
            item
            for item in encounter.get("combatants", [])
            if str(item.get("actor_id") or "") == owner_id
        ),
        None,
    )
    if owner is None:
        raise CombatEngineError("Steel Defender combat authority lost its owner")
    flags = dict(dependent.get("turn_flags") or {})
    pending = dict(flags.pop("owner_command_pending", None) or {})
    owner_incapacitated = bool(_condition_set(owner.get("conditions")) & INCAPACITATING_STATE_IDS)
    commanded = (
        not owner_incapacitated
        and pending.get("owner_actor_id") == owner_id
        and (
            int(pending.get("owner_turns_completed", -1))
            == int(owner.get("turns_completed", 0) or 0)
        )
    )
    if owner_incapacitated:
        flags["dependent_owner_incapacitated"] = True
        event = "steel_defender_owner_incapacitated"
    elif commanded:
        flags["dependent_command_active"] = True
        event = "steel_defender_command_activated"
    else:
        budget = dict(dependent.get("turn_budget") or {})
        if int(budget.get("main_action", 0) or 0) > 0:
            budget["main_action"] = int(budget["main_action"]) - 1
            dependent["turn_budget"] = budget
        flags["dodging"] = True
        flags["dependent_default_dodge"] = True
        event = "steel_defender_default_dodge"
    dependent["turn_flags"] = flags
    encounter["log"] = [
        *list(encounter.get("log") or []),
        {
            "type": event,
            "actor_id": str(dependent.get("actor_id") or ""),
            "owner_actor_id": owner_id,
            "round": int(encounter.get("round", 1) or 1),
        },
    ][-100:]
