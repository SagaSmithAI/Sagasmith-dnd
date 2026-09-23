"""Internal bindings for paid Ready spells; never accepts replacement source semantics."""

from copy import deepcopy

from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    preflight_spell_attack,
)
from sagasmith_dnd.spell_resolution import spell_attack_count
from sagasmith_dnd.spells import apply_cast_effects

from .sunlight import prepare_attack_action


def bound_sheet(sheet, card):
    """Use the held source for evaluation without rewriting the actor's current cards."""
    value = deepcopy(sheet)
    spells = value.setdefault("content", {}).setdefault("spells", [])
    value["content"]["spells"] = [s for s in spells if s.get("id") != card["id"]] + [deepcopy(card)]
    return value


def release_effects(sheet, readied, *, off_turn):
    value = deepcopy(sheet)
    holding = next(
        (e for e in value.get("effects", []) if e.get("id") == readied.get("holding_effect_id")),
        None,
    )
    if not holding or not holding.get("active") or not holding.get("concentration"):
        raise CombatEngineError("readied spell is no longer held by concentration")
    holding["active"] = False
    holding["ended_reason"] = "readied_spell_released"
    paid = deepcopy(readied["paid_cast"])
    effects = apply_cast_effects(
        value,
        spell=readied["spell_card"],
        duration=paid["effect_duration"],
        off_turn=off_turn,
    )
    return {
        **paid,
        **effects,
        "payment": {
            "economy": "readied_spell",
            "reaction": True,
            "prepaid": deepcopy(paid["payment"]),
        },
        "concentration_started": bool(paid["effect_duration"].get("concentration")),
        "spell_effects_deferred": False,
    }


def validate_attacks(
    service,
    campaign_id,
    encounter,
    actor_id,
    sheet,
    spell,
    resolution,
    cast_level,
    declaration,
    principal_id,
):
    declared = deepcopy(declaration or {})
    attacks = declared.get("attacks")
    if (
        set(declared) != {"attacks"}
        or not isinstance(attacks, list)
        or len(attacks) != spell_attack_count(resolution, cast_level=cast_level)
    ):
        raise CombatEngineError(
            "readied spell attack declaration requires one stored attack per ray"
        )
    attacker = service.combat_actor_snapshot(actor_id)
    attacker["sheet"] = bound_sheet(sheet, spell)
    for attack in attacks:
        if (
            not isinstance(attack, dict)
            or set(attack) - {"target_id", "context"}
            or not isinstance(attack.get("target_id"), str)
            or not attack["target_id"]
        ):
            raise CombatEngineError("readied spell attacks require target_id and optional context")
        if attack["target_id"] == actor_id:
            raise CombatEngineError("an actor cannot attack itself")
        service.require_campaign_actor(campaign_id, attack["target_id"])
        action = service.sanitize_attack_action(
            campaign_id,
            principal_id,
            {"context": deepcopy(attack.get("context") or {})},
        )
        service.validate_agent_attack_context(
            campaign_id, principal_id, action, encounter=encounter
        )
        action = prepare_attack_action(
            service, action, campaign_id=campaign_id, actor_id=actor_id,
            target_id=attack["target_id"], principal_id=principal_id, encounter=encounter,
        )
        preflight_spell_attack(
            attacker,
            service.combat_actor_snapshot(attack["target_id"]),
            spell_id=spell["id"],
            cast_level=cast_level,
            encounter=encounter,
            context=action.get("context"),
            allow_out_of_turn=True,
            rules=service.effective_rule_context(campaign_id),
        )
    return attacks
