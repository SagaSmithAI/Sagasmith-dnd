"""Selected 2014 SRD Fighting Styles; weapon and choice facts stay authoritative."""

from __future__ import annotations

import random
from copy import deepcopy

STYLE_RULE = "dnd5e.core.class.fighting_styles"
_OPTIONS = {
    "fighter": {"archery", "defense", "dueling", "great weapon fighting", "protection",
                "two-weapon fighting"},
    "paladin": {"defense", "dueling", "great weapon fighting", "protection"},
    "ranger": {"archery", "defense", "dueling", "two-weapon fighting"},
}


def has_style(sheet, style):
    if sheet.get("edition") != "2014":
        return False
    for feature in sheet.get("content", {}).get("features", []):
        for class_name, options in _OPTIONS.items():
            identifiers = {f"dnd5e.content.srd2014.feature.{class_name}-fighting-style"}
            if class_name == "fighter":
                identifiers.add("dnd5e.content.srd2014.feature.fighter-additional-fighting-style")
            if (feature.get("id") in identifiers and style in options
                    and str(feature.get("choices", {}).get("option", "")).casefold() == style):
                return True
    return False


def archery_bonus(sheet, weapon):
    return 2 if (has_style(sheet, "archery") and weapon.get("attack_type") == "ranged"
                 and not weapon.get("spell_attack")) else 0


def great_weapon_eligible(sheet, weapon, *, attack_mode, grip):
    properties = {str(p).replace("-", "_") for p in weapon.get("properties", [])}
    return (has_style(sheet, "great weapon fighting") and attack_mode == "melee"
            and weapon.get("attack_type") == "melee" and not weapon.get("spell_attack")
            and grip == "two_handed" and bool(properties & {"two_handed", "versatile"}))


def roll_weapon_damage(expression, *, reroll_low=False, rng=None):
    """A declared style use rerolls only the weapon's own dice, each at most once."""
    from .engine import roll
    from .random_stream import active_random_source

    if not reroll_low:
        return roll(expression, rng=rng), []
    generator = rng or active_random_source() or random
    rerolls = []

    class WeaponDice:
        index = 0

        def randint(self, minimum, sides):
            first = generator.randint(minimum, sides)
            result = first
            if first in {1, 2}:
                result = generator.randint(minimum, sides)
                rerolls.append({"index": self.index, "sides": sides, "from": first,
                                "to": result, "source": "great_weapon_fighting"})
            self.index += 1
            return result

    return roll(expression, rng=WeaponDice()), rerolls


def protection_ready(sheet, combatant):
    """A shield and a selected style are prerequisites, never inferred from a name."""
    from .conditions import condition_ids

    inventory = sheet.get("inventory", {})
    shield_id = inventory.get("equipment_slots", {}).get("shield")
    shield = next((item for item in inventory.get("items", [])
                   if item.get("id") == shield_id), {})
    conditions = condition_ids(sheet.get("conditions")) | condition_ids(combatant.get("conditions"))
    return (
        has_style(sheet, "protection") and bool(shield_id) and shield.get("kind") == "shield"
        and shield.get("equipped") and shield.get("condition") != "destroyed"
        and sheet.get("combat", {}).get("hp", {}).get("value", 0) > 0
        and not conditions & {"incapacitated", "unconscious", "paralyzed", "petrified", "stunned"}
        and not combatant.get("departed")
        and int(combatant.get("turn_budget", {}).get("reaction", 0)) > 0
    )


def protection_candidates(encounter, sheets, attacker_id, target_id, *, facts=None):
    """Resolve grid geometry or require complete, coordinate-free DM scene facts."""
    from .combat_engine import CombatEngineError, NeedsRulingError, can_see
    from .spaces import distance_between, grid_space

    if encounter.get("ruleset") != "2014":
        return []
    combatants = {a["actor_id"]: a for a in encounter.get("combatants", [])}
    attacker, target = combatants[attacker_id], combatants[target_id]
    possible = [a for a in combatants.values()
                if a["actor_id"] != target_id
                and protection_ready(sheets[a["actor_id"]], a)]
    by_id = {}
    if possible and encounter.get("positioning_mode") == "agent":
        if not isinstance(facts, dict):
            raise NeedsRulingError(
                "Protection requires each eligible shield bearer's scene facts before the roll",
                missing=("attack.context.protection",), ruling_kind="agent_dm_adjudication",
            )
        if (set(facts) != {"decision_id", "reason", "actors"}
                or not isinstance(facts["decision_id"], str) or not facts["decision_id"].strip()
                or not isinstance(facts["reason"], str) or not facts["reason"].strip()
                or not isinstance(facts["actors"], list)):
            raise CombatEngineError("Protection requires decision_id, reason and actors")
        for fact in facts["actors"]:
            if (not isinstance(fact, dict)
                    or set(fact) != {"actor_id", "within_5_ft", "can_see_attacker"}
                    or type(fact["within_5_ft"]) is not bool
                    or type(fact["can_see_attacker"]) is not bool
                    or fact["actor_id"] in by_id):
                raise CombatEngineError("Protection actor facts require unique actors and booleans")
            by_id[fact["actor_id"]] = fact
        if set(by_id) != {a["actor_id"] for a in possible}:
            raise CombatEngineError("Protection facts must cover every eligible shield bearer")
    candidates = []
    for protector in possible:
        identifier = protector["actor_id"]
        if encounter.get("positioning_mode") == "agent":
            eligible = by_id[identifier]["within_5_ft"] and by_id[identifier]["can_see_attacker"]
        else:
            # The grid's recorded visibility is the same authority used by attacks.
            p, t = protector["position"], target["position"]
            pxy, txy = (p["x"], p["y"]), (t["x"], t["y"])
            battle_map = encounter.get("battle_map") or {}
            pspace = grid_space(protector, pxy, battle_map)["space_ft"]
            tspace = grid_space(target, txy, battle_map)["space_ft"]
            eligible = distance_between(pxy, pspace, txy, tspace) <= 5 and can_see(
                {"sheet": sheets[identifier], **protector},
                {"sheet": sheets[attacker_id], **attacker},
            )
        if eligible:
            candidates.append(identifier)
    return candidates


def apply_protection(plan, actor_ids):
    value = deepcopy(plan)
    if actor_ids:
        value["disadvantage"] = True
        value["disadvantage_sources"] = list(dict.fromkeys([
            *value.get("disadvantage_sources", []), "protection",
        ]))
        value["protection"] = {"actor_ids": list(actor_ids), "mechanic_id": STYLE_RULE}
    return value
