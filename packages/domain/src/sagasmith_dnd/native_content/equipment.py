from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sagasmith_dnd.combat_engine import CombatEngineError, _condition_set, current_combatant

"Reviewed native content handlers; registered by the domain composition root."


def apply_official_item_effect_to_encounter(
    encounter: dict[str, Any], result: dict[str, Any], *, attacker_id: str, target_id: str
) -> dict[str, Any]:
    """Commit deterministic encounter effects emitted by official weapons."""
    value = deepcopy(encounter)
    effect = dict(result.get("official_item_effect") or {})
    if not effect:
        return {"encounter": value, "effect": None}
    kind = str(effect.get("kind") or "")
    if kind == "dyrrn_natural_20_stun":
        if (
            str(effect.get("target_id") or "") != target_id
            or str(effect.get("source_actor_id") or "") != attacker_id
        ):
            raise CombatEngineError("Dyrrn stun effect actor binding is invalid")
        combatant = next(
            (
                item
                for item in value.get("combatants", [])
                if str(item.get("actor_id") or "") == target_id
            ),
            None,
        )
        if combatant is None:
            raise CombatEngineError("Dyrrn stun target is not a combatant")
        current = current_combatant(value)
        target_turns_completed = int(combatant.get("turns_completed", 0) or 0)
        target_is_current_turn = bool(
            current is not None and str(current.get("actor_id") or "") == target_id
        )
        conditions = _condition_set(combatant.get("conditions"))
        conditions.add("stunned")
        combatant["conditions"] = sorted(conditions)
        committed = {
            "id": f"official-item-stun-{uuid4().hex}",
            "kind": "official_item_stun",
            "mechanic_id": "dnd5e.expansion.eberron.dyrrn_tentacle_whip",
            "source_actor_id": attacker_id,
            "target_actor_id": target_id,
            "condition": "stunned",
            "expires_on": "target_turn_end",
            "expires_after_target_turns_completed": target_turns_completed
            + (2 if target_is_current_turn else 1),
            "active": True,
        }
        value.setdefault("ongoing_effects", []).append(committed)
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "official_item_effect", "effect": deepcopy(committed)},
        ][-100:]
        return {"encounter": value, "effect": committed}
    if kind == "arcane_propulsion_arm_return":
        if (
            str(effect.get("source_actor_id") or "") != attacker_id
            or str(effect.get("target_id") or "") != target_id
        ):
            raise CombatEngineError("Arcane Propulsion Arm return effect actor binding is invalid")
        committed = {
            "kind": kind,
            "mechanic_id": "dnd5e.expansion.eberron.arcane_propulsion_arm",
            "source_actor_id": attacker_id,
            "target_id": target_id,
            "item_id": str(effect.get("item_id") or ""),
            "state": "attached",
        }
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "official_item_effect", "effect": deepcopy(committed)},
        ][-100:]
        return {"encounter": value, "effect": committed}
    raise CombatEngineError(f"unsupported official item encounter effect: {kind}")
