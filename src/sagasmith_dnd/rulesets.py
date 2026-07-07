"""Small structured ruleset registry for D&D 5e runtime automation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DND5E_2014: dict[str, Any] = {
    "id": "dnd5e-2014",
    "name": "D&D 5e 2014",
    "timeUnits": {
        "turn": {"combat": True},
        "round": {"combat": True},
        "declared_minute": {"timeComponent": "minute"},
        "declared_hour": {"timeComponent": "hour"},
        "exploration_turn": {"timeComponent": "exploration"},
    },
    "activityActivationTypes": {
        "action": {"group": "standard"},
        "bonus": {"group": "standard"},
        "reaction": {"group": "standard"},
        "minute": {"group": "time", "scalar": True, "period": "declared_minute"},
        "hour": {"group": "time", "scalar": True, "period": "declared_hour"},
        "shortRest": {"group": "rest", "passive": True, "period": "short_rest"},
        "longRest": {"group": "rest", "passive": True, "period": "long_rest"},
        "encounter": {"group": "combat", "passive": True, "period": "encounter_start"},
        "turnStart": {"group": "combat", "passive": True, "period": "turn_start"},
        "turnEnd": {"group": "combat", "passive": True, "period": "turn_end"},
        "special": {"passive": True},
    },
    "activityTypes": {
        "attack": {},
        "check": {},
        "damage": {},
        "effect": {},
        "heal": {},
        "save": {},
        "utility": {},
    },
    "limitedUsePeriods": {
        "sr": {"period": "short_rest", "type": "rest"},
        "lr": {"period": "long_rest", "type": "rest"},
        "day": {"period": "declared_day", "type": "time"},
        "recharge": {"period": "recharge", "type": "special"},
        "turnStart": {"period": "turn_start", "type": "combat"},
        "turnEnd": {"period": "turn_end", "type": "combat"},
        "encounter": {"period": "encounter_start", "type": "combat"},
    },
    "activityConsumptionTypes": {
        "activityUses": {},
        "itemUses": {},
        "spellSlots": {},
        "attribute": {},
        "material": {},
        "hitDice": {},
        "turnBudget": {},
    },
    "activities": {
        "attack": {"activation": "action", "type": "attack"},
        "dash": {"activation": "action", "type": "utility"},
        "disengage": {"activation": "action", "type": "utility"},
        "dodge": {"activation": "action", "type": "utility"},
        "help": {"activation": "action", "type": "utility"},
        "hide": {"activation": "action", "type": "check"},
        "ready": {"activation": "action", "type": "utility"},
        "search": {"activation": "action", "type": "check"},
        "use_object": {"activation": "action", "type": "utility"},
        "two_weapon_attack": {"activation": "bonus", "type": "attack"},
        "cunning_action_dash": {"activation": "bonus", "type": "utility", "requires_feature": "cunning-action"},
        "cunning_action_disengage": {"activation": "bonus", "type": "utility", "requires_feature": "cunning-action"},
        "cunning_action_hide": {"activation": "bonus", "type": "check", "requires_feature": "cunning-action"},
        "second_wind": {
            "activation": "bonus",
            "type": "heal",
            "requires_feature": "second-wind",
            "uses": {"resource": "second_wind", "cost": 1, "recovery": ["short_rest", "long_rest"]},
            "healing": "1d10+@fighter_level",
        },
        "action_surge": {
            "activation": "special",
            "type": "utility",
            "requires_feature": "action-surge",
            "uses": {"resource": "action_surge", "cost": 1, "recovery": ["short_rest", "long_rest"]},
            "grant": {"extra_actions": 1},
        },
        "opportunity_attack": {"activation": "reaction", "type": "attack", "trigger": "token.move.provoke"},
        "shield": {
            "activation": "reaction",
            "type": "effect",
            "trigger": "before_hit_resolution",
            "effect": {"ac_bonus": 5, "duration": {"period": "until_turn_start", "anchor": "self"}},
        },
    },
    "conditions": {
        "poisoned": {"tags": ["attack_disadvantage", "ability_check_disadvantage"]},
        "prone": {"tags": ["crawl", "melee_attackers_advantage", "ranged_attackers_disadvantage"]},
        "restrained": {"tags": ["no_movement", "attackers_advantage", "attack_disadvantage", "dex_save_disadvantage"]},
        "incapacitated": {"tags": ["no_actions", "no_reactions"]},
        "unconscious": {"tags": ["incapacitated", "prone"]},
    },
    "conditionTypes": {
        "blinded": {"statuses": []},
        "charmed": {"statuses": []},
        "deafened": {"statuses": []},
        "frightened": {"statuses": []},
        "grappled": {"statuses": []},
        "incapacitated": {"statuses": []},
        "invisible": {"statuses": []},
        "paralyzed": {"statuses": ["incapacitated"]},
        "petrified": {"statuses": ["incapacitated"]},
        "poisoned": {"statuses": []},
        "prone": {"statuses": []},
        "restrained": {"statuses": []},
        "stunned": {"statuses": ["incapacitated"]},
        "unconscious": {"statuses": ["incapacitated"], "riders": ["prone"]},
    },
    "conditionEffects": {
        "noMovement": ["grappled", "paralyzed", "petrified", "restrained", "unconscious"],
        "crawl": ["prone"],
        "abilityCheckDisadvantage": ["poisoned"],
        "attackDisadvantage": ["poisoned"],
        "dexteritySaveDisadvantage": ["restrained"],
        "initiativeDisadvantage": ["incapacitated"],
    },
}


def list_rulesets() -> list[dict[str, str]]:
    return [{"id": DND5E_2014["id"], "name": DND5E_2014["name"]}]


def get_ruleset(ruleset_id: str | None = None) -> dict[str, Any]:
    if ruleset_id in {None, "", "2014", "dnd5e-2014"}:
        return deepcopy(DND5E_2014)
    raise LookupError(f"ruleset not found: {ruleset_id}")


def validate_ruleset(ruleset_id: str | None = None) -> dict[str, Any]:
    ruleset = get_ruleset(ruleset_id)
    errors: list[str] = []
    for action_id, action in ruleset.get("activities", {}).items():
        if action.get("activation") not in {
            "action",
            "bonus",
            "reaction",
            "special",
            "minute",
            "hour",
            "short_rest",
            "long_rest",
            "encounter",
            "turn_start",
            "turn_end",
        }:
            errors.append(f"{action_id}: invalid activation")
        if not action.get("type"):
            errors.append(f"{action_id}: missing type")
    return {
        "id": ruleset["id"],
        "valid": not errors,
        "errors": errors,
        "activities": sorted(ruleset.get("activities", {}).keys()),
        "activityActivationTypes": sorted(ruleset.get("activityActivationTypes", {}).keys()),
        "limitedUsePeriods": sorted(ruleset.get("limitedUsePeriods", {}).keys()),
    }
