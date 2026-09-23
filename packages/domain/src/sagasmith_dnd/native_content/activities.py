from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    _are_hostile,
    _condition_set,
    _effective_speed_ft,
    _stances,
    _update_movement_accounting,
    can_see,
    current_combatant,
)
from sagasmith_dnd.standard_feature_ids import (
    CORE_ORC_AGGRESSIVE_MECHANIC_ID,
    ORC_AGGRESSIVE_ACTIVITY_ID,
)

"Reviewed native content handlers; registered by the domain composition root."


def settle_core_activity_effect(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    activity_id: str,
    declaration: dict[str, Any] | None = None,
    source_card: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Settle narrow engine-owned effects for canonical Core activity cards."""
    value = deepcopy(encounter)
    action_surge_ids = {
        "dnd5e.content.srd2014.feature.fighter-action-surge",
        "dnd5e.content.srd2024.feature.fighter-action-surge",
    }
    cunning_action_ids = {
        "dnd5e.content.srd2014.feature.rogue-cunning-action",
        "dnd5e.content.srd2024.feature.rogue-cunning-action",
    }
    aggressive_spec = dict(dict(source_card or {}).get("choices") or {}).get("standard_resolution")
    orc_aggressive = (
        activity_id == ORC_AGGRESSIVE_ACTIVITY_ID
        and CORE_ORC_AGGRESSIVE_MECHANIC_ID
        in {str(item) for item in dict(source_card or {}).get("mechanic_refs") or []}
        and (dict(dict(source_card or {}).get("activation") or {}).get("type") == "bonus_action")
        and (
            aggressive_spec
            == {"kind": "aggressive_movement", "maximum": "speed", "target": "one_visible_hostile"}
        )
    )
    if activity_id not in {*action_surge_ids, *cunning_action_ids} and (not orc_aggressive):
        return (value, None)
    current = current_combatant(value)
    if current is None or current.get("actor_id") != actor_id_value:
        raise CombatEngineError("this Core activity can be used only on the actor's turn")
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value)
    )
    _stances.require_action_allowed(combatant)
    if orc_aggressive:
        declared = dict(declaration or {})
        if set(declared) != {"target_id"} or not str(declared.get("target_id") or "").strip():
            raise CombatEngineError("Aggressive declaration requires exactly one target_id")
        target_id = str(declared["target_id"]).strip()
        if target_id == actor_id_value:
            raise CombatEngineError("Aggressive must target another creature")
        target = next(
            (
                item
                for item in value.get("combatants", [])
                if str(item.get("actor_id") or "") == target_id
            ),
            None,
        )
        if target is None or "dead" in _condition_set(target.get("conditions")):
            raise CombatEngineError("Aggressive target must be a living combatant")
        if not _are_hostile(combatant, target):
            raise CombatEngineError("Aggressive target must be hostile")
        if not can_see(combatant, target, value):
            raise CombatEngineError("Aggressive target must be visible to the Orc")
        flags = dict(combatant.get("turn_flags") or {})
        if "aggressive_movement" in flags:
            raise CombatEngineError("Aggressive already granted movement on this turn")
        granted = _effective_speed_ft(combatant)
        flags["aggressive_movement"] = {
            "source_activity_id": activity_id,
            "target_actor_id": target_id,
            "granted": granted,
            "remaining": granted,
        }
        combatant["turn_flags"] = flags
        effect = {
            "kind": "orc_aggressive",
            "target_id": target_id,
            "movement_granted": granted,
            "movement_remaining": granted,
            "requires_ruling": False,
        }
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "orc_aggressive", "actor_id": actor_id_value, "effect": effect},
        ][-100:]
        return (value, effect)
    if activity_id in cunning_action_ids:
        selected = str(dict(declaration or {}).get("action") or "")
        selected = selected.strip().lower().replace("-", "_").replace(" ", "_")
        if selected not in {"dash", "disengage", "hide"}:
            raise CombatEngineError(
                "Cunning Action declaration.action must be dash, disengage, or hide"
            )
        budget = dict(combatant.get("turn_budget") or {})
        flags = dict(combatant.get("turn_flags") or {})
        if selected == "dash":
            _update_movement_accounting(
                combatant, budget, extra_grant_delta=_effective_speed_ft(combatant)
            )
        elif selected == "disengage":
            flags["disengaged"] = True
            combatant["turn_flags"] = flags
        else:
            flags["hide_declared"] = {
                "source_activity_id": activity_id,
                "declaration": deepcopy(declaration or {}),
            }
            combatant["turn_flags"] = flags
        effect = {
            "kind": "cunning_action",
            "action": selected,
            "requires_ruling": selected == "hide",
        }
        if selected == "hide":
            effect["ruling_requirement"] = {
                "default_resolver": "agent",
                "ruling_kind": "source_or_scene_fact",
                "reason": (
                    "Determine from the current cover, visibility, and observer facts "
                    "whether hiding is possible and resolve the Stealth boundary."
                ),
            }
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "cunning_action", "actor_id": actor_id_value, "effect": effect},
        ][-100:]
        return (value, effect)
    flags = dict(combatant.get("turn_flags") or {})
    if flags.get("action_surge_used"):
        raise CombatEngineError("Action Surge can be used only once on the same turn")
    budget = dict(combatant.get("turn_budget") or {})
    budget["extra_action"] = int(budget.get("extra_action", 0) or 0) + 1
    combatant["turn_budget"] = budget
    flags["action_surge_used"] = True
    if activity_id.endswith("srd2024.feature.fighter-action-surge"):
        flags["extra_action_forbidden_actions"] = ["cast"]
    combatant["turn_flags"] = flags
    effect = {
        "kind": "action_surge",
        "extra_actions_granted": 1,
        "extra_actions_available": budget["extra_action"],
    }
    value["log"] = [
        *list(value.get("log") or []),
        {"type": "action_surge", "actor_id": actor_id_value, "effect": effect},
    ][-100:]
    return (value, effect)
