"""Pure, branch-agnostic D&D combat planning and resolution.

This module deliberately does not read or write a database.  The MCP layer owns
authorization, branch selection, optimistic revisions, idempotency, and the
atomic commit.  The functions here receive validated actor-card snapshots and
return new values plus an auditable result.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from uuid import uuid4

from sagasmith_dnd.engine import (
    resolve_attack,
    resolve_check,
    resolve_death_save,
    roll,
    roll_d20,
)


class CombatEngineError(ValueError):
    """Base error for a rejected or incomplete combat operation."""


class NeedsRulingError(CombatEngineError):
    """Raised when the engine cannot safely infer a narrative prerequisite."""

    def __init__(self, message: str, *, missing: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.missing = tuple(missing)


@dataclass(frozen=True)
class ActionIntent:
    """A fully identified action declaration before mechanical resolution."""

    campaign_id: str
    actor_id: str
    action_type: str
    target_ids: tuple[str, ...] = ()
    item_id: str | None = None
    activity_id: str | None = None
    payment: str | None = None
    branch_id: str | None = None
    principal_id: str = "system:local"
    expected_revisions: dict[str, int] = field(default_factory=dict)
    idempotency_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    rulings: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ActionIntent":
        targets = value.get("target_ids", value.get("targets", ()))
        if isinstance(targets, str):
            targets = (targets,)
        if not isinstance(targets, (list, tuple)):
            raise CombatEngineError("target_ids must be a list")
        rulings = value.get("rulings", ())
        if isinstance(rulings, dict):
            rulings = (rulings,)
        return cls(
            campaign_id=str(value.get("campaign_id") or ""),
            actor_id=str(value.get("actor_id") or ""),
            action_type=str(value.get("action_type") or value.get("type") or ""),
            target_ids=tuple(str(item) for item in targets),
            item_id=value.get("item_id"),
            activity_id=value.get("activity_id"),
            payment=value.get("payment"),
            branch_id=value.get("branch_id"),
            principal_id=str(value.get("principal_id") or "system:local"),
            expected_revisions={
                str(key): int(item)
                for key, item in dict(value.get("expected_revisions") or {}).items()
            },
            idempotency_key=value.get("idempotency_key"),
            payload=deepcopy(dict(value.get("payload") or {})),
            rulings=tuple(deepcopy(item) for item in rulings if isinstance(item, dict)),
        )

    def validate(self) -> None:
        if not self.campaign_id:
            raise CombatEngineError("campaign_id is required")
        if not self.actor_id:
            raise CombatEngineError("actor_id is required")
        if not self.action_type:
            raise CombatEngineError("action_type is required")
        for ruling in self.rulings:
            if not ruling.get("kind") or "value" not in ruling:
                raise CombatEngineError("every ruling needs kind and value")
            if ruling.get("source") not in {"rule", "module", "scene", "dm_ruling"}:
                raise CombatEngineError("ruling source is invalid")


@dataclass(frozen=True)
class ChoiceWindow:
    id: str
    kind: str
    actor_id: str
    event: str
    candidates: tuple[dict[str, Any], ...] = ()
    deadline: str = "before_commit"
    status: str = "pending"


@dataclass(frozen=True)
class ResolutionReceipt:
    id: str
    operation: str
    status: str
    campaign_id: str
    branch_id: str | None
    actor_id: str | None
    result: dict[str, Any]
    rolls: tuple[dict[str, Any], ...] = ()
    changes: tuple[dict[str, Any], ...] = ()
    pending: tuple[dict[str, Any], ...] = ()
    rulings: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def actor_id(actor: dict[str, Any]) -> str:
    value = actor.get("id") or actor.get("character_id") or actor.get("actor_id")
    if not value:
        raise CombatEngineError("actor snapshot has no id")
    return str(value)


def actor_sheet(actor: dict[str, Any]) -> dict[str, Any]:
    sheet = actor.get("sheet")
    if not isinstance(sheet, dict):
        raise CombatEngineError(f"actor {actor_id(actor)} has no validated sheet")
    return deepcopy(sheet)


def actor_derived(actor: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(actor.get("derived") or {}))


def start_encounter(
    participants: list[dict[str, Any]],
    *,
    ruleset: str = "2014",
    scene_id: str | None = None,
    name: str = "Combat",
    rng: Any = None,
) -> dict[str, Any]:
    """Create encounter state from actor references and derived values.

    The caller must supply any narrative decisions such as surprise and hidden
    participation.  This function only rolls initiative and creates budgets.
    """
    if not participants:
        raise CombatEngineError("combat requires at least one participant")
    normalized_ruleset = _normalize_ruleset(ruleset)
    combatants: list[dict[str, Any]] = []
    for index, actor in enumerate(participants):
        identifier = actor_id(actor)
        derived = actor_derived(actor)
        sheet = actor_sheet(actor)
        conditions = _condition_set(sheet.get("conditions"))
        exhaustion = int(sheet.get("combat", {}).get("exhaustion", 0) or 0)
        if exhaustion >= 6 and "dead" not in conditions:
            raise CombatEngineError(
                f"actor {identifier} has exhaustion level 6 and must be marked dead"
            )
        initiative_bonus = int(derived.get("initiative", 0))
        if normalized_ruleset == "2024":
            initiative_bonus -= 2 * exhaustion
        speed = int(derived.get("speed", {}).get("walk", 30) or 30)
        if normalized_ruleset == "2024":
            speed = max(0, speed - 5 * exhaustion)
        elif exhaustion >= 5:
            speed = 0
        elif exhaustion >= 2:
            speed //= 2
        supplied = actor.get("initiative")
        die = None
        if supplied is None:
            surprised = bool(actor.get("surprised", False))
            die = roll_d20(
                advantage=bool(actor.get("initiative_advantage", False))
                or ("invisible" in conditions and normalized_ruleset == "2024"),
                disadvantage=bool(actor.get("initiative_disadvantage", False))
                or (surprised and normalized_ruleset == "2024")
                or (exhaustion >= 1 and normalized_ruleset == "2014"),
                rng=rng,
            )
            initiative = die["natural"] + initiative_bonus
        else:
            initiative = int(supplied)
        combatants.append(
            {
                "actor_id": identifier,
                "token_id": actor.get("token_id"),
                "name": actor.get("name", identifier),
                "initiative": initiative,
                "initiative_roll": die,
                "initiative_bonus": initiative_bonus,
                "_initiative_supplied": supplied is not None,
                "tie_breaker": int(actor.get("tie_breaker", index)),
                "_tie_breaker_supplied": "tie_breaker" in actor,
                "turn_budget": {
                    "main_action": 1,
                    "bonus_action": 1,
                    "reaction": 1,
                    "movement": speed,
                    "speed": speed,
                    "object_interaction": 1,
                    "attack_budget": 0,
                },
                "conditions": list(sheet.get("conditions") or []),
                "position": deepcopy(actor.get("position")),
                "hidden": bool(actor.get("hidden", False)),
                "visible_to_actor_ids": deepcopy(actor.get("visible_to_actor_ids")),
                "disposition": _normalize_disposition(actor.get("disposition")),
                "reach_ft": _positive_int(actor.get("reach_ft"), default=5),
                "surprised": bool(actor.get("surprised", False)),
                "death_saves": bool(actor.get("death_saves", actor.get("character_type") == "pc")),
                "exhaustion": exhaustion,
            }
        )
        if combatants[-1]["surprised"] and normalized_ruleset == "2014":
            combatants[-1]["turn_budget"].update(
                main_action=0,
                movement=0,
                reaction=0,
            )
    ties: dict[int, list[dict[str, Any]]] = {}
    for combatant in combatants:
        ties.setdefault(int(combatant["initiative"]), []).append(combatant)
    if any(
        len(items) > 1
        and all(item["_initiative_supplied"] for item in items)
        and not all(item["_tie_breaker_supplied"] for item in items)
        for items in ties.values()
    ):
        raise NeedsRulingError(
            "initiative ties need explicit tie_breaker choices", missing=("tie_breaker",)
        )
    for combatant in combatants:
        combatant.pop("_tie_breaker_supplied", None)
        combatant.pop("_initiative_supplied", None)
    combatants.sort(
        key=lambda value: (-value["initiative"], value["tie_breaker"], value["actor_id"])
    )
    return {
        "id": f"encounter-{uuid4().hex}",
        "active": True,
        "name": name or "Combat",
        "scene_id": scene_id,
        "ruleset": normalized_ruleset,
        "round": 1,
        "turn_index": 0,
        "combatants": combatants,
        "pending": [],
        "readied": [],
        "effects": [],
        "log": [],
    }


def current_combatant(encounter: dict[str, Any]) -> dict[str, Any] | None:
    combatants = list(encounter.get("combatants") or [])
    if not combatants:
        return None
    return combatants[int(encounter.get("turn_index", 0)) % len(combatants)]


def available_actions(encounter: dict[str, Any], actor_id_value: str) -> list[str]:
    if not encounter.get("active", True):
        return []
    combatant = next(
        (
            item
            for item in encounter.get("combatants", [])
            if item.get("actor_id") == actor_id_value
        ),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    conditions = _condition_set(combatant.get("conditions"))
    budget = dict(combatant.get("turn_budget") or {})
    current = current_combatant(encounter)
    if current is None or current.get("actor_id") != actor_id_value:
        return []
    if combatant.get("surprised") and _normalize_ruleset(encounter.get("ruleset")) == "2014":
        return []
    if conditions & {"dead", "unconscious", "stunned", "paralyzed", "petrified"}:
        return []
    if "incapacitated" in conditions:
        return ["move"] if "grappled" not in conditions and "restrained" not in conditions else []
    actions = (
        ["move"]
        if budget.get("movement", 0) > 0 and not conditions & {"grappled", "restrained"}
        else []
    )
    if budget.get("main_action", 0) > 0 or budget.get("extra_action", 0) > 0:
        actions.extend(
            ["attack", "cast", "dash", "disengage", "dodge", "help", "hide", "ready", "search"]
        )
        if _normalize_ruleset(encounter.get("ruleset")) == "2024":
            actions.extend(["influence", "study", "utilize"])
        else:
            actions.append("use_object")
    if budget.get("attack_budget", 0) > 0:
        actions.append("attack")
    return actions


def preflight_attack(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    action: dict[str, Any],
    encounter: dict[str, Any] | None = None,
    allow_out_of_turn: bool = False,
) -> dict[str, Any]:
    """Validate an attack declaration without changing any state or rolling."""
    actor_sheet(attacker)
    actor_sheet(target)
    if encounter is not None:
        current = current_combatant(encounter)
        if not allow_out_of_turn and current and current.get("actor_id") != actor_id(attacker):
            raise CombatEngineError("it is not this actor's turn")
        if not allow_out_of_turn and "attack" not in available_actions(
            encounter, actor_id(attacker)
        ):
            raise CombatEngineError("actor has no legal attack action")
        attacker = deepcopy(attacker)
        target = deepcopy(target)
        for combatant in encounter.get("combatants", []):
            if combatant.get("actor_id") == actor_id(attacker):
                attacker["position"] = deepcopy(combatant.get("position"))
                attacker["turn_flags"] = deepcopy(combatant.get("turn_flags") or {})
                attacker["conditions"] = deepcopy(combatant.get("conditions") or [])
                attacker["hidden"] = bool(combatant.get("hidden", False))
                attacker["death_saves"] = bool(combatant.get("death_saves", True))
                attacker["visible_to_actor_ids"] = deepcopy(combatant.get("visible_to_actor_ids"))
            elif combatant.get("actor_id") == actor_id(target):
                target["position"] = deepcopy(combatant.get("position"))
                target["turn_flags"] = deepcopy(combatant.get("turn_flags") or {})
                target["conditions"] = deepcopy(combatant.get("conditions") or [])
                target["hidden"] = bool(combatant.get("hidden", False))
                target["death_saves"] = bool(combatant.get("death_saves", True))
                target["visible_to_actor_ids"] = deepcopy(combatant.get("visible_to_actor_ids"))
    attacker_unresolved = actor_derived(attacker).get("unresolved_rules") or []
    if attacker_unresolved:
        raise NeedsRulingError("attacker has unresolved rules", missing=attacker_unresolved)
    target_unresolved = actor_derived(target).get("unresolved_rules") or []
    if target_unresolved:
        raise NeedsRulingError("target has unresolved rules", missing=target_unresolved)
    target_ac = int(actor_derived(target).get("armor_class", 10))
    attacks = list(actor_derived(attacker).get("inventory", {}).get("weapon_attacks", []))
    weapon_id = action.get("weapon_id") or action.get("item_id")
    weapon = next((item for item in attacks if item.get("item_id") == weapon_id), None)
    if weapon is None:
        if weapon_id:
            raise CombatEngineError("weapon is not present in the actor's derived attacks")
        if len(attacks) == 1:
            weapon = attacks[0]
        elif not attacks:
            strength = int(
                (actor_sheet(attacker).get("abilities", {}).get("strength") or {}).get("score", 10)
            )
            modifier = (strength - 10) // 2
            weapon = {
                "item_id": "unarmed-strike",
                "attack_bonus": modifier + int(actor_derived(attacker).get("proficiency_bonus", 2)),
                "damage_expression": f"1 {'+' if modifier >= 0 else '-'} {abs(modifier)}",
                "damage_type": "bludgeoning",
            }
        else:
            raise CombatEngineError("weapon_id is required when actor has multiple attacks")
    attack_bonus = int(weapon.get("attack_bonus", 0))
    context = dict(action.get("context") or {})
    cover = dict(context.get("cover") or {})
    if cover.get("degree") == "total" or context.get("targetable") is False:
        raise CombatEngineError("target has total cover")
    cover_degree = str(cover.get("degree") or "").replace("-", "_")
    cover_bonus = {"half": 2, "three_quarters": 5}.get(cover_degree, 0)
    if cover.get("ac_bonus") is not None:
        declared_bonus = int(cover["ac_bonus"])
        if cover_bonus and declared_bonus != cover_bonus:
            raise CombatEngineError("cover ac_bonus does not match the declared cover degree")
        cover_bonus = declared_bonus
    target_ac += cover_bonus
    expression = weapon.get("damage_expression") or weapon.get("damage") or ""
    damage_type = str(weapon.get("damage_type") or "")
    range_result = _attack_range(attacker, target, weapon)
    if range_result["disadvantage"]:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("weapon_long_range")
    attacker_conditions = _condition_set(
        attacker.get("conditions") or actor_sheet(attacker).get("conditions")
    )
    attacker_exhaustion = int(actor_sheet(attacker).get("combat", {}).get("exhaustion", 0) or 0)
    ruleset = (
        _normalize_ruleset(encounter.get("ruleset"))
        if encounter is not None
        else _normalize_ruleset(actor_sheet(attacker).get("edition"))
    )
    if ruleset == "2024":
        attack_bonus -= 2 * attacker_exhaustion
    elif attacker_exhaustion >= 3:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("exhaustion")
    target_conditions = _condition_set(
        target.get("conditions") or actor_sheet(target).get("conditions")
    )
    attacker_can_see_target = bool(
        context.get("attacker_can_see_target", _can_see(attacker, target))
    )
    target_can_see_attacker = bool(
        context.get("target_can_see_attacker", _can_see(target, attacker))
    )
    if not target_can_see_attacker:
        context["advantage"] = True
        context.setdefault("advantage_sources", []).append("attacker_unseen")
    if not attacker_can_see_target:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("target_unseen")
    if attacker_conditions & {"blinded", "poisoned", "prone", "restrained"}:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).extend(
            sorted(attacker_conditions & {"blinded", "poisoned", "prone", "restrained"})
        )
    unresolved_condition_sources = attacker_conditions & {"charmed", "frightened"}
    if unresolved_condition_sources:
        raise NeedsRulingError(
            "condition source is required to determine this attack's legality",
            missing=sorted(unresolved_condition_sources),
        )
    if target_conditions & {
        "blinded",
        "paralyzed",
        "petrified",
        "restrained",
        "stunned",
        "unconscious",
    }:
        context["advantage"] = True
        context.setdefault("advantage_sources", []).extend(
            sorted(
                target_conditions
                & {"blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"}
            )
        )
    distance = range_result.get("distance_ft")
    if target_conditions & {"prone", "unconscious"} and distance is not None:
        if int(distance) <= 5:
            context["advantage"] = True
            context.setdefault("advantage_sources", []).append("target_prone_within_5_ft")
        else:
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append("target_prone_beyond_5_ft")
    target_flags = dict(target.get("turn_flags") or {})
    target_speed = int(actor_derived(target).get("speed", {}).get("walk", 0) or 0)
    if target_conditions & {
        "grappled",
        "paralyzed",
        "petrified",
        "restrained",
        "stunned",
        "unconscious",
    }:
        target_speed = 0
    if (
        target_flags.get("dodging")
        and "incapacitated" not in target_conditions
        and target_speed > 0
        and target_can_see_attacker
    ):
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("target_dodging")
    helped_by = None
    if encounter is not None:
        target_position = _position(target.get("position"))
        for helper in encounter.get("combatants", []):
            helping = dict(helper.get("turn_flags") or {}).get("helping")
            helper_position = _position(helper.get("position"))
            if (
                isinstance(helping, dict)
                and helping.get("target_id") == actor_id(attacker)
                and target_position is not None
                and helper_position is not None
                and _grid_distance(helper_position, target_position) <= 5
                and not _condition_set(helper.get("conditions"))
                & {"dead", "unconscious", "stunned", "incapacitated", "paralyzed", "petrified"}
            ):
                context["advantage"] = True
                context.setdefault("advantage_sources", []).append("help")
                helped_by = str(helper.get("actor_id"))
                break
    automatic_critical = bool(
        distance is not None
        and int(distance) <= 5
        and target_conditions & {"paralyzed", "unconscious"}
    )
    return {
        "status": "ready",
        "kind": "attack",
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
        "attack_bonus": attack_bonus,
        "target_ac": target_ac,
        "damage_expression": str(expression),
        "damage_type": damage_type,
        "advantage": bool(context.get("advantage", False)),
        "disadvantage": bool(context.get("disadvantage", False)),
        "advantage_sources": list(context.get("advantage_sources") or []),
        "disadvantage_sources": list(context.get("disadvantage_sources") or []),
        "rulings": list(action.get("rulings") or []),
        "weapon_id": weapon.get("item_id"),
        "resource_cost": deepcopy(weapon.get("resource_cost") or {}),
        "range": range_result,
        "automatic_critical_on_hit": automatic_critical,
        "ruleset": ruleset,
        "target_uses_death_saves": bool(target.get("death_saves", True)),
        "knock_out": bool(action.get("knock_out", False)),
        "melee_attack": str(weapon.get("attack_type") or "melee") == "melee",
        "helped_by": helped_by,
    }


def resolve_attack_action(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve a prepared attack and return updated attacker/target snapshots."""
    attack = resolve_attack(
        armor_class=int(plan["target_ac"]),
        attack_bonus=int(plan["attack_bonus"]),
        advantage=bool(plan.get("advantage")),
        disadvantage=bool(plan.get("disadvantage")),
        rng=rng,
    )
    if attack["hit"] and plan.get("automatic_critical_on_hit"):
        attack["critical"] = True
    updated_attacker = deepcopy(attacker)
    updated_target = deepcopy(target)
    result: dict[str, Any] = {
        **attack,
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
        "damage": None,
    }
    expression = str(plan.get("damage_expression") or "")
    if attack["hit"] and expression:
        damage_expression = _critical_expression(expression) if attack["critical"] else expression
        damage_roll = roll(damage_expression, rng=rng)
        target_sheet = actor_sheet(updated_target)
        damage = apply_damage_to_sheet(
            target_sheet,
            amount=max(0, damage_roll.total),
            damage_type=str(plan.get("damage_type") or ""),
            source=actor_id(attacker),
            critical=bool(attack["critical"]),
            ruleset=str(plan.get("ruleset") or "2014"),
            death_saves=bool(plan.get("target_uses_death_saves", True)),
            knock_out=bool(plan.get("knock_out", False)),
            melee=bool(plan.get("melee_attack", False)),
        )
        updated_target["sheet"] = damage["sheet"]
        result["damage"] = {
            **damage,
            "expression": expression,
            "rolled_expression": damage_expression,
            "rolls": list(damage_roll.rolls),
            "detail": damage_roll.detail,
        }
    if updated_attacker.get("hidden"):
        updated_attacker["hidden"] = False
        result["reveals_attacker"] = True
    return updated_attacker, updated_target, result


def apply_damage_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
    damage_type: str = "",
    source: str = "",
    critical: bool = False,
    ruleset: str | None = None,
    death_saves: bool = True,
    knock_out: bool = False,
    melee: bool = False,
) -> dict[str, Any]:
    """Apply one typed damage part with temp HP and trait ordering."""
    raw, adjusted, normalized, adjustment = _adjust_damage_amount(
        sheet, amount=amount, damage_type=damage_type
    )
    return _apply_adjusted_damage(
        sheet,
        raw=raw,
        adjusted=adjusted,
        damage_type=normalized,
        adjustment=adjustment,
        source=source,
        critical=critical,
        ruleset=ruleset,
        death_saves=death_saves,
        knock_out=knock_out,
        melee=melee,
    )


def _apply_adjusted_damage(
    sheet: dict[str, Any],
    *,
    raw: int,
    adjusted: int,
    damage_type: str,
    adjustment: str,
    source: str,
    critical: bool,
    ruleset: str | None,
    death_saves: bool,
    knock_out: bool,
    melee: bool,
) -> dict[str, Any]:
    """Apply an already trait-adjusted simultaneous damage instance once."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before_temp = int(hp.get("temp", 0) or 0)
    before_hp = int(hp.get("value", 0) or 0)
    absorbed = min(before_temp, adjusted)
    hp_damage = adjusted - absorbed
    hp["temp"] = before_temp - absorbed
    hp["value"] = max(0, before_hp - hp_damage)
    combat["hp"] = hp
    conditions = set(value.get("conditions") or [])
    max_hp = int(hp.get("max", before_hp) or before_hp)
    massive_excess = max(0, hp_damage - before_hp)
    became_zero = hp["value"] == 0 and before_hp > 0
    if became_zero:
        conditions.update({"prone", "unconscious"})
        normalized_ruleset = _normalize_ruleset(ruleset or value.get("edition"))
        if knock_out and not melee:
            raise CombatEngineError("only a melee attack can knock a creature out")
        if knock_out and melee:
            if normalized_ruleset == "2024":
                hp["value"] = 1
            conditions.add("stable")
        elif massive_excess >= max_hp:
            conditions.discard("unconscious")
            conditions.add("dead")
        elif normalized_ruleset == "2014" and not death_saves:
            conditions.discard("unconscious")
            conditions.add("dead")
    death = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    if before_hp == 0 and hp_damage > 0 and "dead" not in conditions and death_saves:
        conditions.discard("stable")
        conditions.update({"prone", "unconscious"})
        if hp_damage >= max_hp:
            conditions.discard("unconscious")
            conditions.add("dead")
        else:
            death["failures"] = min(3, int(death.get("failures", 0)) + (2 if critical else 1))
            if death["failures"] >= 3:
                conditions.discard("unconscious")
                conditions.add("dead")
    combat["death_saves"] = death
    if hp["value"] > 0:
        conditions.discard("unconscious")
    value["conditions"] = sorted(conditions)
    if hp["value"] == 0 and ("unconscious" in conditions or "dead" in conditions):
        for effect in value.get("effects", []):
            if bool(effect.get("concentration")):
                effect["active"] = False
                effect["ended_reason"] = "unconscious"
    concentration_effects = [
        effect.get("id")
        for effect in value.get("effects", [])
        if effect.get("active") and effect.get("concentration")
    ]
    concentration = None
    if adjusted > 0 and hp["value"] > 0 and concentration_effects:
        dc = max(10, adjusted // 2)
        if str(value.get("edition") or "2014") == "2024":
            dc = min(30, dc)
        concentration = {
            "dc": dc,
            "effect_ids": concentration_effects,
            "status": "pending",
        }
    return {
        "sheet": value,
        "input_amount": raw,
        "applied_amount": adjusted,
        "absorbed_temp": absorbed,
        "hp_damage": hp_damage,
        "before_temp": before_temp,
        "after_temp": hp["temp"],
        "before_hp": before_hp,
        "after_hp": hp["value"],
        "damage_type": damage_type,
        "adjustment": adjustment,
        "source": source,
        "concentration": concentration,
        "massive_damage": massive_excess >= max_hp,
    }


def apply_damage_parts_to_sheet(
    sheet: dict[str, Any],
    parts: Iterable[dict[str, Any]],
    *,
    source: str = "",
    critical: bool = False,
) -> dict[str, Any]:
    """Apply one simultaneous multi-type damage instance and preserve each part.

    Damage types are adjusted separately, but temporary HP, dropping to zero,
    death-save failures, instant death, and concentration are settled once for
    the combined instance. Separate sources must call this function separately.
    """
    grouped: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, dict):
            raise CombatEngineError("damage parts must be objects")
        amount = int(part.get("amount", 0))
        if amount < 0:
            raise CombatEngineError("damage amount cannot be negative")
        damage_type = str(part.get("damage_type") or part.get("type") or "").strip().lower()
        grouped[damage_type] = grouped.get(damage_type, 0) + amount
    details: list[dict[str, Any]] = []
    for grouped_type, grouped_amount in grouped.items():
        raw, adjusted, damage_type, adjustment = _adjust_damage_amount(
            sheet,
            amount=grouped_amount,
            damage_type=grouped_type,
        )
        details.append(
            {
                "input_amount": raw,
                "applied_amount": adjusted,
                "damage_type": damage_type,
                "adjustment": adjustment,
            }
        )
    if not details:
        raise CombatEngineError("damage packet must contain at least one part")
    applied = _apply_adjusted_damage(
        sheet,
        raw=sum(item["input_amount"] for item in details),
        adjusted=sum(item["applied_amount"] for item in details),
        damage_type="mixed" if len(details) > 1 else details[0]["damage_type"],
        adjustment="per_part" if len(details) > 1 else details[0]["adjustment"],
        source=source,
        critical=critical,
        ruleset=None,
        death_saves=True,
        knock_out=False,
        melee=False,
    )
    remaining_temp = applied["before_temp"]
    for detail in details:
        absorbed = min(remaining_temp, detail["applied_amount"])
        remaining_temp -= absorbed
        detail["absorbed_temp"] = absorbed
        detail["hp_damage"] = detail["applied_amount"] - absorbed
    return {
        "sheet": applied["sheet"],
        "parts": details,
        "input_amount": applied["input_amount"],
        "applied_amount": applied["applied_amount"],
        "hp_damage": applied["hp_damage"],
        "before_hp": applied["before_hp"],
        "after_hp": applied["after_hp"],
        "before_temp": applied["before_temp"],
        "after_temp": applied["after_temp"],
        "concentration": applied["concentration"],
        "massive_damage": applied["massive_damage"],
    }


def _adjust_damage_amount(
    sheet: dict[str, Any], *, amount: int, damage_type: str
) -> tuple[int, int, str, str]:
    raw = int(amount)
    if raw < 0:
        raise CombatEngineError("damage amount cannot be negative")
    traits = dict(sheet.get("traits") or {})
    normalized = damage_type.strip().lower()
    immunities = _trait_set(traits.get("immunities"))
    resistances = _trait_set(traits.get("resistances"))
    vulnerabilities = _trait_set(traits.get("vulnerabilities"))
    if normalized in immunities:
        return raw, 0, normalized, "immune"
    adjusted = raw
    resistant = normalized in resistances or "petrified" in _condition_set(sheet.get("conditions"))
    if resistant:
        adjusted //= 2
    if normalized in vulnerabilities:
        adjusted *= 2
    if resistant and normalized in vulnerabilities:
        adjustment = "resistant_and_vulnerable"
    elif resistant:
        adjustment = "resistant"
    elif normalized in vulnerabilities:
        adjustment = "vulnerable"
    else:
        adjustment = "normal"
    return raw, adjusted, normalized, adjustment


def resolve_death_save_to_sheet(
    sheet: dict[str, Any],
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    bonus: int = 0,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve and persist one death save, including natural-20 recovery."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 1, "temp": 0}))
    if int(hp.get("value", 0) or 0) > 0:
        raise CombatEngineError("death saves are only available at 0 hit points")
    conditions = _condition_set(value.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("dead actors cannot make death saves")
    if "stable" in conditions:
        raise CombatEngineError("stable actors do not make additional death saves")
    death = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    result = resolve_death_save(
        successes=int(death.get("successes", 0)),
        failures=int(death.get("failures", 0)),
        advantage=advantage,
        disadvantage=disadvantage,
        bonus=bonus,
        rng=rng,
    )
    if result["outcome"] == "revived":
        hp["value"] = max(1, int(hp.get("value", 0)))
        combat["hp"] = hp
        value["conditions"] = [
            item for item in value.get("conditions", []) if item != "unconscious"
        ]
    death.update(successes=result["successes"], failures=result["failures"])
    if result["outcome"] == "stable":
        death.update(successes=0, failures=0)
        value["conditions"] = sorted(set(value.get("conditions", [])) | {"stable", "unconscious"})
    combat["death_saves"] = death
    if result["outcome"] == "dead":
        value["conditions"] = sorted(set(value.get("conditions", [])) | {"dead"})
    return {"sheet": value, **result}


def apply_concentration_result(
    sheet: dict[str, Any],
    *,
    effect_ids: Iterable[str],
    success: bool,
) -> dict[str, Any]:
    """Keep concentration on a successful save and deactivate named effects on failure."""
    value = deepcopy(sheet)
    ids = {str(item) for item in effect_ids}
    if not success:
        for effect in value.get("effects", []):
            if effect.get("id") in ids:
                effect["active"] = False
    return value


def spend_movement(
    encounter: dict[str, Any],
    actor_id_value: str,
    distance: int,
    *,
    destination: Any = None,
    path: list[Any] | None = None,
    movement_mode: str = "voluntary",
    crawl: bool = False,
) -> dict[str, Any]:
    """Consume movement and open opportunity-reaction windows from known geometry.

    Only explicit token positions and reach values are automated.  Terrain,
    blocking, forced movement, and line-of-effect remain DM-rulable because
    this encounter state does not claim to model them.
    """
    value = deepcopy(encounter)
    distance = int(distance)
    if distance < 0:
        raise CombatEngineError("movement distance cannot be negative")
    movement_mode = str(movement_mode).strip().lower().replace("-", "_")
    if movement_mode not in {"voluntary", "forced", "teleport"}:
        raise CombatEngineError("movement_mode must be voluntary, forced, or teleport")
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    if not value.get("active", True):
        raise CombatEngineError("combat is not active")
    if any(
        item.get("kind") == "reaction"
        and item.get("target_id") == actor_id_value
        and item.get("status", "pending") == "pending"
        for item in value.get("pending", [])
    ):
        raise CombatEngineError("pending reaction must be resolved before this actor moves again")
    current = current_combatant(value)
    if current is None or current.get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    conditions = _condition_set(combatant.get("conditions"))
    if conditions & {
        "dead",
        "unconscious",
        "stunned",
        "paralyzed",
        "petrified",
        "restrained",
    }:
        raise CombatEngineError("actor cannot move under its current conditions")
    if "grappled" in conditions:
        raise NeedsRulingError(
            "grapple source is needed to determine movement", missing=("grapple_source",)
        )
    if "prone" in conditions and not crawl:
        raise CombatEngineError("a prone actor must crawl or stand before moving")
    if combatant.get("surprised") and _normalize_ruleset(value.get("ruleset")) == "2014":
        raise CombatEngineError("surprised actor cannot move on its first turn")
    budget = dict(combatant.get("turn_budget") or {})
    available = int(budget.get("movement", 0) or 0)
    movement_cost = distance * 2 if crawl else distance
    if movement_cost > available:
        raise CombatEngineError("movement exceeds the remaining speed")
    origin = _position(combatant.get("position"))
    if path is not None:
        if not path:
            raise CombatEngineError("path must contain at least one waypoint")
        if origin is None:
            raise CombatEngineError("a waypoint path requires a known origin")
        waypoints = [_position(item) for item in path]
        if any(item is None for item in waypoints):
            raise CombatEngineError("path waypoints must contain numeric x and y coordinates")
        if waypoints[0] != origin:
            waypoints.insert(0, origin)
        destination = path[-1]
        segment_distance = sum(
            _grid_distance(left, right) for left, right in zip(waypoints, waypoints[1:])
        )
        if segment_distance != distance:
            raise CombatEngineError("movement distance must equal the path segment distance")
    target_position = _position(destination)
    if destination is not None and target_position is None:
        raise CombatEngineError("destination must contain numeric x and y coordinates")
    if path is None and origin is not None and target_position is not None:
        geometric_distance = _grid_distance(origin, target_position)
        if geometric_distance != distance:
            raise CombatEngineError(
                "movement distance must equal the grid distance between origin and destination"
            )
    budget["movement"] = available - movement_cost
    combatant["turn_budget"] = budget
    if destination is not None:
        combatant["position"] = deepcopy(destination)
    if (
        movement_mode == "voluntary"
        and origin is not None
        and target_position is not None
        and not _disengaged(combatant)
    ):
        existing = {
            (item.get("event"), item.get("actor_id"), item.get("target_id"))
            for item in value.get("pending", [])
            if item.get("status", "pending") == "pending"
        }
        movement_segments = (
            list(zip(waypoints, waypoints[1:])) if path is not None else [(origin, target_position)]
        )
        for threat in value.get("combatants", []):
            if not _can_make_opportunity_attack(threat, combatant):
                continue
            threat_position = _position(threat.get("position"))
            if threat_position is None:
                continue
            reach = _positive_int(threat.get("reach_ft"), default=5)
            leaving_segment = next(
                (
                    start
                    for start, end in movement_segments
                    if _grid_distance(start, threat_position)
                    <= reach
                    < _grid_distance(end, threat_position)
                ),
                None,
            )
            if leaving_segment is not None:
                key = ("movement.leave_reach", threat.get("actor_id"), actor_id_value)
                if key in existing:
                    continue
                value["pending"] = [
                    *list(value.get("pending") or []),
                    {
                        "id": f"reaction-{uuid4().hex}",
                        "kind": "reaction",
                        "actor_id": threat["actor_id"],
                        "target_id": actor_id_value,
                        "target_position": {"x": leaving_segment[0], "y": leaving_segment[1]},
                        "target_visible": True,
                        "event": "movement.leave_reach",
                        "trigger": "opportunity_attack",
                        "candidates": [
                            {"id": "opportunity_attack"},
                            {"id": "decline"},
                        ],
                        "deadline": "before_commit",
                        "status": "pending",
                    },
                ]
    return value


def stand_up(encounter: dict[str, Any], actor_id_value: str) -> dict[str, Any]:
    """Spend half the recorded speed to end Prone without spending an action."""
    value = deepcopy(encounter)
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None or current_combatant(value) is None:
        raise CombatEngineError("actor is not the current combatant")
    if current_combatant(value).get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    conditions = _condition_set(combatant.get("conditions"))
    if "prone" not in conditions:
        raise CombatEngineError("actor is not prone")
    if conditions & {"dead", "unconscious", "stunned", "paralyzed", "petrified"}:
        raise CombatEngineError("actor cannot stand under its current conditions")
    budget = dict(combatant.get("turn_budget") or {})
    cost = int(budget.get("speed", 0) or 0) // 2
    if int(budget.get("movement", 0) or 0) < cost:
        raise CombatEngineError("standing requires half the actor's speed in remaining movement")
    budget["movement"] = int(budget["movement"]) - cost
    combatant["turn_budget"] = budget
    combatant["conditions"] = [
        item for item in combatant.get("conditions", []) if str(item).casefold() != "prone"
    ]
    return value


def resolve_common_action(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    action: str,
    target_id: str | None = None,
    trigger: str | None = None,
    payload: dict[str, Any] | None = None,
    payment: str | None = None,
) -> dict[str, Any]:
    """Settle the non-attack actions that have deterministic action-economy effects.

    Narrative outcomes (a successful hide/search/help consequence) remain a DM
    ruling.  The action payment and temporary tactical flags do not.
    """
    value = deepcopy(encounter)
    action = str(action).strip().lower().replace("-", "_")
    supported = {
        "cast",
        "dash",
        "disengage",
        "dodge",
        "help",
        "hide",
        "ready",
        "search",
        "influence",
        "study",
        "utilize",
        "use_object",
    }
    if action not in supported:
        raise CombatEngineError(f"unsupported common action: {action}")
    current = current_combatant(value)
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError("actor is not a combatant")
    out_of_turn_reaction = action == "cast" and payment == "reaction"
    if not out_of_turn_reaction and (current is None or current.get("actor_id") != actor_id_value):
        raise CombatEngineError("it is not this actor's turn")
    available_action = action
    if action == "cast" and payment in {"bonus_action", "reaction"}:
        available_action = payment
    if not out_of_turn_reaction and available_action not in available_actions(
        value, actor_id_value
    ):
        raise CombatEngineError("actor has no legal action payment available")
    acting = combatant if out_of_turn_reaction else current
    assert acting is not None
    if out_of_turn_reaction and _condition_set(acting.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
    }:
        raise CombatEngineError("actor cannot take a reaction under its current conditions")
    budget = dict(acting.get("turn_budget") or {})
    payment = payment or ("extra_action" if budget.get("extra_action", 0) > 0 else "main_action")
    if payment not in {"main_action", "extra_action", "bonus_action", "reaction"}:
        raise CombatEngineError("invalid action payment")
    if int(budget.get(payment, 0) or 0) <= 0:
        raise CombatEngineError("actor has no action payment available")
    budget[payment] = int(budget[payment]) - 1
    acting["turn_budget"] = budget
    flags = dict(acting.get("turn_flags") or {})
    if action == "cast":
        flags["cast_declared"] = deepcopy(payload or {})
    elif action == "dash":
        budget["movement"] = int(budget.get("movement", 0) or 0) + int(budget.get("speed", 0) or 0)
    elif action == "disengage":
        flags["disengaged"] = True
    elif action == "dodge":
        flags["dodging"] = True
    elif action == "help":
        if not target_id:
            raise CombatEngineError("help requires a target actor")
        flags["helping"] = {"target_id": target_id, "payload": deepcopy(payload or {})}
    elif action in {"hide", "search", "influence", "study", "utilize", "use_object"}:
        flags[f"{action}_declared"] = deepcopy(payload or {})
    elif action == "ready":
        if not trigger:
            raise CombatEngineError("ready requires an explicit trigger")
        ready_payload = deepcopy(payload or {})
        if ready_payload.get("spell_id") or ready_payload.get("kind") == "spell":
            raise CombatEngineError(
                "readying a spell is not supported by the generic Ready action; "
                "it requires spell-slot and concentration settlement"
            )
        value["readied"] = [
            *list(value.get("readied") or []),
            {
                "id": f"ready-{uuid4().hex}",
                "actor_id": actor_id_value,
                "trigger": trigger,
                "payload": ready_payload,
                "status": "armed",
            },
        ]
    acting["turn_flags"] = flags
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "common_action",
            "action": action,
            "actor_id": actor_id_value,
            "target_id": target_id,
        },
    ][-100:]
    return value


def arm_readied_spell(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    spell_id: str,
    trigger: str,
    holding_effect_id: str,
    release_concentration: bool,
    release_duration: dict[str, Any],
    release_effect_kind: str,
    declaration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a paid, concentrated spell until its trigger or the next turn."""
    value = deepcopy(encounter)
    if not str(trigger).strip():
        raise CombatEngineError("readying a spell requires an explicit perceivable trigger")
    if any(
        item.get("actor_id") == actor_id_value and item.get("status") in {"armed", "triggered"}
        for item in value.get("readied", [])
    ):
        raise CombatEngineError("actor already has a readied action")
    value["readied"] = [
        *list(value.get("readied") or []),
        {
            "id": f"ready-spell-{uuid4().hex}",
            "kind": "spell",
            "actor_id": actor_id_value,
            "spell_id": spell_id,
            "trigger": str(trigger).strip(),
            "holding_effect_id": holding_effect_id,
            "release_concentration": bool(release_concentration),
            "release_duration": deepcopy(release_duration),
            "release_effect_kind": release_effect_kind,
            "declaration": deepcopy(declaration or {}),
            "status": "armed",
        },
    ]
    return value


def trigger_readied_spell(
    encounter: dict[str, Any], *, readied_id: str, event: str
) -> dict[str, Any]:
    """Open the owned reaction window after the DM confirms the trigger occurred."""
    value = deepcopy(encounter)
    event_text = str(event).strip()
    if not event_text:
        raise CombatEngineError("triggering a readied spell requires the observed event")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == readied_id), None
    )
    if readied is None or readied.get("kind") != "spell" or readied.get("status") != "armed":
        raise CombatEngineError("readied spell is not armed")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("resolve the pending save or choice before another trigger")
    readied["status"] = "triggered"
    window = {
        "id": f"reaction-{uuid4().hex}",
        "kind": "reaction",
        "actor_id": readied["actor_id"],
        "event": event_text,
        "trigger": "readied_spell",
        "readied_id": readied_id,
        "candidates": [{"id": "release"}, {"id": "decline"}],
        "deadline": "immediate_after_trigger",
        "status": "pending",
    }
    value["pending"] = [*list(value.get("pending") or []), window]
    return value


def trigger_readied_action(
    encounter: dict[str, Any], *, readied_id: str, event: str
) -> dict[str, Any]:
    """Open the reaction choice for a non-spell Ready action after DM confirmation."""
    value = deepcopy(encounter)
    event_text = str(event).strip()
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == readied_id), None
    )
    if (
        not event_text
        or readied is None
        or readied.get("kind") == "spell"
        or readied.get("status") != "armed"
    ):
        raise CombatEngineError("readied non-spell action is not armed or has no observed trigger")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("resolve the pending save or choice before another trigger")
    readied["status"] = "triggered"
    value["pending"] = [
        *list(value.get("pending") or []),
        {
            "id": f"reaction-{uuid4().hex}",
            "kind": "reaction",
            "actor_id": readied["actor_id"],
            "event": event_text,
            "trigger": "readied_action",
            "readied_id": readied_id,
            "candidates": [{"id": "release"}, {"id": "decline"}],
            "deadline": "immediate_after_trigger",
            "status": "pending",
        },
    ]
    return value


def resolve_readied_action_window(
    encounter: dict[str, Any], *, actor_id_value: str, choice_id: str, release: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spend a reaction for a generic readied action; its effect remains a DM ruling."""
    value = deepcopy(encounter)
    window = next((item for item in value.get("pending", []) if item.get("id") == choice_id), None)
    if (
        not isinstance(window, dict)
        or window.get("trigger") != "readied_action"
        or window.get("actor_id") != actor_id_value
    ):
        raise CombatEngineError("choice_id is not this actor's readied-action window")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == window.get("readied_id")),
        None,
    )
    if readied is None or readied.get("status") != "triggered":
        raise CombatEngineError("readied action is no longer available")
    value = resolve_choice_window(
        value,
        choice_id=choice_id,
        actor_id_value=actor_id_value,
        selection={"id": "release" if release else "decline"},
    )
    if release:
        combatant = next(
            item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
        )
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0:
            raise CombatEngineError("actor has no reaction remaining")
        budget["reaction"] = int(budget["reaction"]) - 1
        combatant["turn_budget"] = budget
        value["readied"] = [
            item for item in value.get("readied", []) if item.get("id") != readied["id"]
        ]
    else:
        readied["status"] = "armed"
    return value, deepcopy(readied)


def resolve_readied_spell_window(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    choice_id: str,
    release: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Release held energy with a reaction or ignore this occurrence of the trigger."""
    value = deepcopy(encounter)
    window = next((item for item in value.get("pending", []) if item.get("id") == choice_id), None)
    if (
        window is None
        or window.get("kind") != "reaction"
        or window.get("trigger") != "readied_spell"
        or window.get("actor_id") != actor_id_value
    ):
        raise CombatEngineError("choice_id is not this actor's readied-spell window")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == window.get("readied_id")),
        None,
    )
    if readied is None or readied.get("status") != "triggered":
        raise CombatEngineError("readied spell is no longer available")
    value = resolve_choice_window(
        value,
        choice_id=choice_id,
        actor_id_value=actor_id_value,
        selection={"id": "release" if release else "decline"},
    )
    readied = next(item for item in value["readied"] if item.get("id") == readied["id"])
    if release:
        combatant = next(
            item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
        )
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0:
            raise CombatEngineError("actor has no reaction remaining")
        budget["reaction"] = int(budget["reaction"]) - 1
        combatant["turn_budget"] = budget
        value["readied"] = [
            item for item in value.get("readied", []) if item.get("id") != readied["id"]
        ]
    else:
        readied["status"] = "armed"
    return value, deepcopy(readied)


def pay_activity_activation(
    encounter: dict[str, Any], *, actor_id_value: str, activation_type: str
) -> dict[str, Any]:
    """Pay only the action-economy portion of a structured activity card.

    Effects, targets, and choices intentionally remain outside this helper: a
    content card describes resources and timing, not a universally safe way to
    infer narrative resolution.
    """
    value = deepcopy(encounter)
    activation = str(activation_type).strip().lower()
    if activation not in {"action", "bonus_action", "reaction", "special"}:
        raise CombatEngineError("activity activation type is not usable in combat")
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError("actor is not a combatant")
    if _condition_set(combatant.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
    }:
        raise CombatEngineError("actor cannot activate content under its current conditions")
    if activation in {"action", "bonus_action"}:
        current = current_combatant(value)
        if current is None or current.get("actor_id") != actor_id_value:
            raise CombatEngineError("it is not this actor's turn")
    budget = dict(combatant.get("turn_budget") or {})
    if activation == "action":
        payment = "extra_action" if int(budget.get("extra_action", 0) or 0) > 0 else "main_action"
    elif activation in {"bonus_action", "reaction"}:
        payment = activation
    else:
        payment = None
    if payment is not None:
        if int(budget.get(payment, 0) or 0) <= 0:
            raise CombatEngineError(f"actor has no {activation} remaining")
        budget[payment] = int(budget[payment]) - 1
        combatant["turn_budget"] = budget
    value["log"] = [
        *list(value.get("log") or []),
        {"type": "activity_activation", "actor_id": actor_id_value, "activation": activation},
    ][-100:]
    return value


def available_reactions(encounter: dict[str, Any], actor_id_value: str) -> list[dict[str, Any]]:
    """Return reaction windows owned by an actor, even outside its own turn."""
    combatant = next(
        (
            item
            for item in encounter.get("combatants", [])
            if item.get("actor_id") == actor_id_value
        ),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    if int(dict(combatant.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
        return []
    if _condition_set(combatant.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
    }:
        return []
    return [
        deepcopy(item)
        for item in encounter.get("pending", [])
        if item.get("kind") == "reaction"
        and item.get("actor_id") == actor_id_value
        and item.get("status", "pending") == "pending"
    ]


def add_choice_window(
    encounter: dict[str, Any],
    *,
    kind: str,
    actor_id_value: str,
    event: str,
    candidates: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Persist a DM/actor choice without resolving a narrative fact implicitly."""
    value = deepcopy(encounter)
    window = ChoiceWindow(
        id=f"choice-{uuid4().hex}",
        kind=kind,
        actor_id=actor_id_value,
        event=event,
        candidates=tuple(deepcopy(item) for item in candidates),
    )
    value["pending"] = [*list(value.get("pending") or []), asdict(window)]
    return value


def resolve_choice_window(
    encounter: dict[str, Any],
    *,
    choice_id: str,
    actor_id_value: str,
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one pending choice and append its auditable selection."""
    value = deepcopy(encounter)
    pending = list(value.get("pending") or [])
    window = next((item for item in pending if item.get("id") == choice_id), None)
    if window is None:
        raise CombatEngineError("choice window not found")
    if window.get("actor_id") != actor_id_value:
        raise CombatEngineError("actor cannot resolve this choice window")
    candidates = list(window.get("candidates") or [])
    if (
        candidates
        and selection not in candidates
        and selection.get("id") not in {item.get("id") for item in candidates}
    ):
        raise CombatEngineError("selection is not one of the choice candidates")
    value["pending"] = [item for item in pending if item.get("id") != choice_id]
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "choice",
            "choice_id": choice_id,
            "actor_id": actor_id_value,
            "selection": deepcopy(selection),
        },
    ][-100:]
    return value


def apply_healing_to_sheet(sheet: dict[str, Any], *, amount: int) -> dict[str, Any]:
    value = deepcopy(sheet)
    hp = dict(value.setdefault("combat", {}).setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before = int(hp.get("value", 0) or 0)
    if "dead" in _condition_set(value.get("conditions")):
        raise CombatEngineError("ordinary healing cannot restore a dead actor")
    maximum = int(hp.get("max", before) or before)
    hp["value"] = min(maximum, before + max(0, int(amount)))
    value["combat"]["hp"] = hp
    if hp["value"] > 0:
        value["conditions"] = [
            item for item in value.get("conditions", []) if item not in {"unconscious", "stable"}
        ]
        value["combat"]["death_saves"] = {"successes": 0, "failures": 0}
    return {
        "sheet": value,
        "before_hp": before,
        "after_hp": hp["value"],
        "amount": hp["value"] - before,
    }


def resolve_actor_check(
    actor: dict[str, Any],
    *,
    kind: str,
    ability: str,
    dc: int,
    proficient: bool = False,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    ruleset: str | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    sheet = actor_sheet(actor)
    normalized_ruleset = _normalize_ruleset(ruleset or sheet.get("edition"))
    conditions = _condition_set(sheet.get("conditions"))
    exhaustion = int(sheet.get("combat", {}).get("exhaustion", 0) or 0)
    roll_bonus = int(bonus)
    abilities = dict(sheet.get("abilities") or {})
    if kind not in {"ability", "check", "save", "death_save", "attack"}:
        raise CombatEngineError("unsupported check kind")
    if kind == "save" and _long_ability_name(ability) in {"strength", "dexterity"}:
        automatic = conditions & {"paralyzed", "petrified", "stunned", "unconscious"}
        if automatic:
            return {
                "kind": "save",
                "dc": dc,
                "natural": None,
                "rolls": [],
                "critical": False,
                "fumble": False,
                "total": None,
                "success": False,
                "automatic_failure": True,
                "reason": sorted(automatic)[0],
            }
    if kind in {"ability", "check"} and "poisoned" in conditions:
        disadvantage = True
    if kind == "save" and _long_ability_name(ability) == "dexterity" and "restrained" in conditions:
        disadvantage = True
    if normalized_ruleset == "2024":
        roll_bonus -= 2 * exhaustion
    elif (kind in {"ability", "check"} and exhaustion >= 1) or (
        kind in {"save", "death_save"} and exhaustion >= 3
    ):
        disadvantage = True
    derived_skills = dict(actor_derived(actor).get("skills") or {})
    if kind in {"ability", "check"} and ability in derived_skills:
        return resolve_check(
            dc=dc,
            ability_score=10,
            proficient=False,
            level=int(sheet.get("progression", {}).get("level", 1) or 1),
            bonus=int(derived_skills[ability]) + roll_bonus,
            advantage=advantage,
            disadvantage=disadvantage,
            kind="ability",
            rng=rng,
        )
    entry = abilities.get(ability) or abilities.get(_long_ability_name(ability)) or {}
    score = int(entry.get("score", 10) if isinstance(entry, dict) else entry)
    level = int(sheet.get("progression", {}).get("level", 1) or 1)
    if kind == "save" and isinstance(entry, dict):
        proficient = bool(entry.get("save_proficient", False))
        bonus = int(entry.get("bonus", 0) or 0) + roll_bonus
    else:
        bonus = roll_bonus
    if kind == "ability" and ability in dict(sheet.get("skills") or {}):
        skill = dict(sheet.get("skills", {}).get(ability) or {})
        multiplier = {"none": 0, "half": 0.5, "proficient": 1, "expertise": 2}.get(
            str(skill.get("proficiency", "none")), 0
        )
        bonus = int(skill.get("bonus", 0) or 0)
        proficient = multiplier > 0
        if multiplier == 2:
            bonus += 2 + (level - 1) // 4
    if kind == "attack":
        raise CombatEngineError("use resolve_attack for attacks")
    if kind == "death_save":
        death = dict(sheet.get("combat", {}).get("death_saves") or {})
        return resolve_death_save(
            successes=int(death.get("successes", 0)),
            failures=int(death.get("failures", 0)),
            advantage=advantage,
            disadvantage=disadvantage,
            bonus=roll_bonus,
            rng=rng,
        )
    return resolve_check(
        dc=dc,
        ability_score=score,
        proficient=proficient,
        level=level,
        bonus=bonus,
        advantage=advantage,
        disadvantage=disadvantage,
        kind="save" if kind == "save" else "ability",
        rng=rng,
    )


def end_turn(encounter: dict[str, Any], *, actor_id_value: str | None = None) -> dict[str, Any]:
    value = deepcopy(encounter)
    current = current_combatant(value)
    if current is None:
        raise CombatEngineError("combat has no participants")
    if actor_id_value and current.get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("pending choice or save must be resolved before ending the turn")
    current_conditions = _condition_set(current.get("conditions"))
    current_flags = dict(current.get("turn_flags") or {})
    if (
        current.get("death_saves", False)
        and "unconscious" in current_conditions
        and not current_conditions & {"dead", "stable"}
        and not current_flags.get("death_save_used")
    ):
        raise CombatEngineError("a required death save must be resolved before ending the turn")
    was_surprised = bool(current.get("surprised"))
    current["surprised"] = False
    retained_flags = {
        key: deepcopy(item) for key, item in current_flags.items() if key in {"dodging", "helping"}
    }
    if retained_flags:
        current["turn_flags"] = retained_flags
    else:
        current.pop("turn_flags", None)
    if was_surprised and _normalize_ruleset(value.get("ruleset")) == "2014":
        # A surprised creature regains access to reactions as soon as its first
        # turn ends, not at the start of its second turn.
        current.setdefault("turn_budget", {})["reaction"] = 1
    combatants = list(value.get("combatants") or [])
    value["turn_index"] = (int(value.get("turn_index", 0)) + 1) % len(combatants)
    if value["turn_index"] == 0:
        value["round"] = int(value.get("round", 1)) + 1
    next_actor = current_combatant(value)
    if next_actor:
        next_flags = dict(next_actor.get("turn_flags") or {})
        next_flags.pop("dodging", None)
        next_flags.pop("helping", None)
        next_flags.pop("death_save_used", None)
        if next_flags:
            next_actor["turn_flags"] = next_flags
        else:
            next_actor.pop("turn_flags", None)
        value["readied"] = [
            item
            for item in value.get("readied", [])
            if item.get("actor_id") != next_actor.get("actor_id")
        ]
        budget = dict(next_actor.get("turn_budget") or {})
        budget.update(
            main_action=1,
            bonus_action=1,
            reaction=1,
            movement=int(budget.get("speed", 30) or 30),
            object_interaction=1,
            attack_budget=0,
        )
        if next_actor.get("surprised") and _normalize_ruleset(value.get("ruleset")) == "2014":
            budget.update(main_action=0, movement=0, reaction=0)
        next_actor["turn_budget"] = budget
    value["turn_spell_casts"] = {}
    return value


def _normalize_ruleset(value: Any) -> str:
    text = str(value or "2014").lower().replace("dnd", "").replace("5e", "").strip()
    if text in {"2014", "5.1", "2014 rules"}:
        return "2014"
    if text in {"2024", "5.2", "2024 rules"}:
        return "2024"
    raise CombatEngineError("ruleset must be 2014 or 2024")


def _normalize_disposition(value: Any) -> str:
    normalized = str(value or "neutral").strip().lower()
    if normalized not in {"friendly", "neutral", "hostile"}:
        raise CombatEngineError("disposition must be friendly, neutral, or hostile")
    return normalized


def _positive_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _position(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return float(value["x"]), float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _grid_distance(left: tuple[float, float], right: tuple[float, float]) -> int:
    """Use the D&D diagonal-grid convention: one square is five feet."""
    return int(max(abs(left[0] - right[0]), abs(left[1] - right[1])) * 5)


def _disengaged(combatant: dict[str, Any]) -> bool:
    return bool(dict(combatant.get("turn_flags") or {}).get("disengaged"))


def _can_make_opportunity_attack(threat: dict[str, Any], moving: dict[str, Any]) -> bool:
    if threat.get("actor_id") == moving.get("actor_id"):
        return False
    if not _can_see(threat, moving):
        # Whether a particular creature can perceive a hidden or invisible
        # mover is a DM fact unless visible_to_actor_ids records it explicitly.
        return False
    if _condition_set(threat.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
    }:
        return False
    if int(dict(threat.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
        return False
    # A reaction requires an opposed relationship.  Disposition is relative to
    # the party, so the check must be symmetric: a friendly PC can threaten a
    # hostile NPC just as an hostile NPC can threaten a friendly PC.  Factions
    # take precedence when the scene supplies them.
    threat_faction = threat.get("faction") or threat.get("team")
    moving_faction = moving.get("faction") or moving.get("team")
    if threat_faction and moving_faction:
        return threat_faction != moving_faction
    threat_disposition = _normalize_disposition(threat.get("disposition"))
    moving_disposition = _normalize_disposition(moving.get("disposition"))
    return {threat_disposition, moving_disposition} == {"hostile", "friendly"}


def _can_see(viewer: dict[str, Any], subject: dict[str, Any]) -> bool:
    """Resolve only recorded visibility, defaulting ordinary creatures to visible."""
    visible_to = subject.get("visible_to_actor_ids")
    if isinstance(visible_to, list):
        return actor_id(viewer) in {str(item) for item in visible_to}
    if subject.get("hidden", False):
        return False
    conditions = (
        subject.get("conditions")
        if "conditions" in subject
        else actor_sheet(subject).get("conditions")
    )
    return "invisible" not in _condition_set(conditions)


def _attack_range(
    attacker: dict[str, Any], target: dict[str, Any], weapon: dict[str, Any]
) -> dict[str, Any]:
    """Validate only deterministic range facts when both combatants are positioned."""
    attacker_position = _position(attacker.get("position"))
    target_position = _position(target.get("position"))
    if attacker_position is None or target_position is None:
        return {"enforced": False, "distance_ft": None, "disadvantage": False}
    distance = _grid_distance(attacker_position, target_position)
    attack_type = str(weapon.get("attack_type") or "melee").lower()
    range_data = (
        weapon.get("range_ft") if attack_type == "ranged" else weapon.get("thrown_range_ft")
    )
    if not isinstance(range_data, dict) or not range_data.get("normal"):
        if attack_type == "melee":
            reach = _positive_int(weapon.get("reach_ft"), default=5)
            if distance > reach:
                raise CombatEngineError("target is outside melee reach")
            return {
                "enforced": True,
                "distance_ft": distance,
                "normal_ft": reach,
                "long_ft": reach,
                "disadvantage": False,
            }
        return {"enforced": False, "distance_ft": distance, "disadvantage": False}
    normal = _positive_int(range_data.get("normal"), default=5)
    long = _positive_int(range_data.get("long"), default=normal)
    if long < normal:
        long = normal
    if distance > long:
        raise CombatEngineError("target is outside weapon range")
    return {
        "enforced": True,
        "distance_ft": distance,
        "normal_ft": normal,
        "long_ft": long,
        "disadvantage": distance > normal,
    }


def _trait_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        value = value.get("value", [])
    return {str(item).strip().lower() for item in value or []}


def _condition_set(value: Any) -> set[str]:
    return {str(item).strip().lower().replace("-", "_") for item in value or []}


def _long_ability_name(value: str) -> str:
    return {
        "str": "strength",
        "dex": "dexterity",
        "con": "constitution",
        "int": "intelligence",
        "wis": "wisdom",
        "cha": "charisma",
    }.get(value.lower(), value)


def _critical_expression(expression: str) -> str:
    import re

    return re.sub(
        r"(?<!\d)(\d*)d(\d+)",
        lambda match: f"{int(match.group(1) or 1) * 2}d{match.group(2)}",
        expression,
    )
