"""Source-bound disease modifiers for reviewed non-combat ability checks."""

from __future__ import annotations

from typing import Any


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
) -> dict[str, Any]:
    """Build an auditable receipt for a previously resolved disease modifier."""
    receipt_rules = services.effective_rule_context(
        campaign_id,
        facts={
            "actor_id": actor_id,
            "kind": kind,
            "ability": ability,
            "relies_on_sight": True,
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
            "relies_on_sight": True,
            "penalty": modifier["penalty"],
        },
    }
