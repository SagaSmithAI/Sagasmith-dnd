"""Source-bound disease modifiers for reviewed non-combat ability checks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def active_sight_rot_check_required(actor_snapshot: dict[str, Any]) -> bool:
    """Whether this actor currently has a Sight Rot check penalty to adjudicate."""
    from sagasmith_dnd.diseases import DISEASE_SOURCE_REF

    for effect in actor_snapshot.get("sheet", {}).get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("kind") != "disease_state"
            or effect.get("source") != DISEASE_SOURCE_REF
            or effect.get("active") is not True
        ):
            continue
        state = dict(dict(effect.get("metadata") or {}).get("disease_state") or {})
        if (
            state.get("source_ref") == DISEASE_SOURCE_REF
            and state.get("edition") == "2014"
            and state.get("disease_id") == "sight_rot"
            and state.get("active") is True
            and state.get("symptomatic") is True
            and isinstance(state.get("sight_penalty"), int)
            and not isinstance(state.get("sight_penalty"), bool)
            and state["sight_penalty"] > 0
        ):
            return True
    return False


def validate_sensory_check_context(
    value: Any, *, required: bool = False
) -> dict[str, str] | None:
    """Validate the DM's structured check task and sensory basis.

    A bare caller boolean cannot classify a check. The task/reason pair is
    recorded with the operation, while Runtime's campaign DM authorization and
    current rule-context fingerprint bind this adjudication to the mutation.
    """
    if value is None:
        if required:
            raise ValueError(
                "Sight Rot requires a reviewed check_context with task, sensory_basis, and reason"
            )
        return None
    if not isinstance(value, dict) or set(value) != {"task", "sensory_basis", "reason"}:
        raise ValueError(
            "check_context requires exactly task, sensory_basis, and reason"
        )
    task = value.get("task")
    reason = value.get("reason")
    basis = value.get("sensory_basis")
    if not isinstance(task, str) or not 1 <= len(task.strip()) <= 1000:
        raise ValueError("check_context task must contain 1 to 1000 characters")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
        raise ValueError("check_context reason must contain 1 to 2000 characters")
    if not isinstance(basis, str) or basis not in {"sight", "nonvisual"}:
        raise ValueError("check_context sensory_basis must be sight or nonvisual")
    return {
        "task": task.strip(),
        "sensory_basis": basis,
        "reason": reason.strip(),
    }


def sight_rot_check_modifier(
    actor_snapshot: dict[str, Any], *, relies_on_sight: bool | None,
) -> dict[str, Any] | None:
    """Resolve only an active, exact-source 2014 Sight Rot check penalty."""
    if relies_on_sight is not None and type(relies_on_sight) is not bool:
        raise ValueError("sight-dependent check context must be a boolean")
    if relies_on_sight is not True:
        return None

    from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, sight_rot_ability_check_penalty

    for effect in actor_snapshot.get("sheet", {}).get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("kind") != "disease_state"
            or effect.get("source") != DISEASE_SOURCE_REF
            or effect.get("active") is not True
        ):
            continue
        disease_state = dict(dict(effect.get("metadata") or {}).get("disease_state") or {})
        if (
            disease_state.get("source_ref") != DISEASE_SOURCE_REF
            or disease_state.get("edition") != "2014"
            or disease_state.get("disease_id") != "sight_rot"
        ):
            continue
        penalty = sight_rot_ability_check_penalty(disease_state, relies_on_sight=True)
        if penalty:
            return {
                "effect_id": str(effect.get("id") or ""),
                "source_ref": DISEASE_SOURCE_REF,
                "penalty": penalty,
            }
    return None


def sight_rot_check_receipt(
    services: Any,
    *,
    campaign_id: str,
    branch_id: str,
    actor_id: str,
    kind: str,
    ability: str,
    modifier: dict[str, Any],
    event: str,
    check_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an auditable receipt for a previously resolved disease modifier."""
    receipt_rules = services.effective_rule_context(
        campaign_id,
        facts={
            "actor_id": actor_id,
            "kind": kind,
            "ability": ability,
            "sensory_basis": "sight",
            "check_context": deepcopy(check_context),
            "disease_effect_id": modifier["effect_id"],
            "penalty": modifier["penalty"],
        },
        branch_id=branch_id,
    )
    return {
        "mechanic_id": "dnd5e.core.gamemastering.disease.sight_rot.sight_dependent_checks.2014",
        "event": event,
        "operations": [{"op": "modify_check_bonus"}],
        "citations": [{"source": modifier["source_ref"], "edition": "2014"}],
        "ruleset_fingerprint": receipt_rules.fingerprint,
        "facts": {
            "actor_id": actor_id,
            "disease_effect_id": modifier["effect_id"],
            "kind": kind,
            "ability": str(ability).strip().casefold().replace(" ", "_"),
            "sensory_basis": "sight",
            "check_context": deepcopy(check_context),
            "penalty": modifier["penalty"],
        },
    }
