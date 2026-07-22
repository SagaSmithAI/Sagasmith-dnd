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
from sagasmith_dnd.rule_engine import (
    ResolutionContext,
    apply_rule_event,
    context_with_facts,
    core_receipts,
)
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    scaled_roll_expression,
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
    battle_map: dict[str, Any] | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Create encounter state from actor references and derived values.

    The caller must supply any narrative decisions such as surprise and hidden
    participation.  This function only rolls initiative and creates budgets.
    """
    if not participants:
        raise CombatEngineError("combat requires at least one participant")
    normalized_ruleset = _normalize_ruleset(ruleset)
    validated_participants: list[
        tuple[int, dict[str, Any], str, dict[str, Any], dict[str, Any], set[str], int]
    ] = []
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
        validated_participants.append(
            (index, actor, identifier, derived, sheet, conditions, exhaustion)
        )

    combatants: list[dict[str, Any]] = []
    for index, actor, identifier, derived, sheet, conditions, exhaustion in (
        validated_participants
    ):
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
                reroll_ones=_has_halfling_lucky(sheet),
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
                "reach_ft": _nonnegative_int(actor.get("reach_ft"), default=5),
                "can_share_space": bool(actor.get("can_share_space", False)),
                "surprised": bool(actor.get("surprised", False)),
                "death_saves": bool(actor.get("death_saves", actor.get("character_type") == "pc")),
                "exhaustion": exhaustion,
            }
        )
        if combatants[-1]["surprised"] and normalized_ruleset == "2014":
            combatants[-1]["turn_budget"].update(
                main_action=0,
                bonus_action=0,
                movement=0,
                reaction=0,
                object_interaction=0,
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
        "battle_map": deepcopy(battle_map) if battle_map is not None else None,
        "ruleset": normalized_ruleset,
        "round": 1,
        "turn_index": 0,
        "combatants": combatants,
        "reinforcements": [],
        "pending": [],
        "readied": [],
        "effects": [],
        "log": [],
}


def queue_combatant(
    encounter: dict[str, Any],
    actor: dict[str, Any],
    *,
    joins_round: int | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Queue one canonical actor to enter at the start of a future round."""
    value = deepcopy(encounter)
    if not value.get("active", True):
        raise CombatEngineError("cannot join an inactive encounter")
    identifier = actor_id(actor)
    occupied_ids = {
        str(item.get("actor_id") or "")
        for item in [
            *list(value.get("combatants") or []),
            *list(value.get("reinforcements") or []),
        ]
    }
    if identifier in occupied_ids:
        raise CombatEngineError("actor is already present or queued in this encounter")
    current_round = int(value.get("round", 1) or 1)
    due_round = current_round + 1 if joins_round is None else int(joins_round)
    if due_round <= current_round:
        raise CombatEngineError("a queued combatant must join in a future round")

    generated = start_encounter(
        [actor],
        ruleset=value.get("ruleset"),
        rng=rng,
    )["combatants"][0]
    same_initiative = [
        item
        for item in [
            *list(value.get("combatants") or []),
            *list(value.get("reinforcements") or []),
        ]
        if int(item.get("initiative", 0) or 0) == int(generated["initiative"])
    ]
    if same_initiative and "tie_breaker" not in actor:
        raise NeedsRulingError(
            "joining initiative ties need an explicit tie_breaker choice",
            missing=("tie_breaker",),
        )
    if "tie_breaker" not in actor:
        generated["tie_breaker"] = max(
            (int(item.get("tie_breaker", 0) or 0) for item in value["combatants"]),
            default=-1,
        ) + 1
    generated["join_round"] = due_round
    value["reinforcements"] = [
        *list(value.get("reinforcements") or []),
        generated,
    ]
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "reinforcement_queued",
            "actor_id": identifier,
            "initiative": generated["initiative"],
            "join_round": due_round,
        },
    ][-100:]
    return value


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
    if "turned" in conditions:
        actions = (
            ["move"]
            if budget.get("movement", 0) > 0
            and not conditions & {"grappled", "restrained"}
            else []
        )
        if budget.get("main_action", 0) > 0 or budget.get("extra_action", 0) > 0:
            actions.extend(["dash", "dodge"])
            if conditions & {"grappled", "restrained"}:
                actions.append("escape")
        return actions
    actions = (
        ["move"]
        if budget.get("movement", 0) > 0 and not conditions & {"grappled", "restrained"}
        else []
    )
    if budget.get("main_action", 0) > 0 or budget.get("extra_action", 0) > 0:
        actions.extend(
            [
                "attack",
                "cast",
                "dash",
                "disengage",
                "dodge",
                "help",
                "hide",
                "ready",
                "search",
                "stabilize",
            ]
        )
        if _normalize_ruleset(encounter.get("ruleset")) == "2024":
            actions.extend(["influence", "study", "utilize"])
        else:
            actions.extend(["improvise", "use_object"])
    if budget.get("bonus_action", 0) > 0:
        actions.append("bonus_action")
    if budget.get("attack_budget", 0) > 0:
        actions.append("attack")
    return actions


def pay_attack_action(
    encounter: dict[str, Any],
    attacker: dict[str, Any],
    *,
    weapon_id: str,
    attack_mode: str,
    multiattack_option_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pay one attack while preserving a recorded monster Multiattack composition."""
    value = deepcopy(encounter)
    current = current_combatant(value)
    attacker_id = actor_id(attacker)
    if current is None or current.get("actor_id") != attacker_id:
        raise CombatEngineError("it is not this actor's turn")
    combatant = next(
        item for item in value.get("combatants", []) if item.get("actor_id") == attacker_id
    )
    budget = dict(combatant.get("turn_budget") or {})
    flags = dict(combatant.get("turn_flags") or {})
    active_multiattack = flags.get("multiattack")

    if int(budget.get("attack_budget", 0) or 0) > 0:
        if active_multiattack:
            if (
                multiattack_option_id
                and multiattack_option_id != active_multiattack.get("option_id")
            ):
                raise CombatEngineError("multiattack option cannot change during the action")
            remaining = _consume_multiattack_entry(
                active_multiattack.get("remaining"), weapon_id, attack_mode
            )
            if remaining:
                flags["multiattack"] = {
                    **dict(active_multiattack),
                    "remaining": remaining,
                }
            else:
                flags.pop("multiattack", None)
            payment = {
                "kind": "multiattack_followup",
                "option_id": active_multiattack.get("option_id"),
            }
        else:
            if multiattack_option_id:
                raise CombatEngineError(
                    "multiattack option can be selected only on its first attack"
                )
            payment = {"kind": "extra_attack"}
        budget["attack_budget"] -= 1
    else:
        payment_key = (
            "main_action"
            if int(budget.get("main_action", 0) or 0) > 0
            else "extra_action"
            if int(budget.get("extra_action", 0) or 0) > 0
            else ""
        )
        if not payment_key:
            raise CombatEngineError("actor has no attack payment available")
        if multiattack_option_id:
            multiattack_options = _validated_multiattack_options(attacker)
            option = _select_multiattack_option(
                multiattack_options, multiattack_option_id
            )
            remaining = _consume_multiattack_entry(
                option["attacks"], weapon_id, attack_mode
            )
            total = sum(int(item["count"]) for item in option["attacks"])
            budget["attack_budget"] = total - 1
            if remaining:
                flags["multiattack"] = {
                    "activity_id": option["activity_id"],
                    "option_id": option["id"],
                    "remaining": remaining,
                }
            payment = {
                "kind": "multiattack",
                "payment": payment_key,
                "activity_id": option["activity_id"],
                "option_id": option["id"],
                "attack_count": total,
            }
        else:
            count = int(actor_derived(attacker).get("attacks_per_action", 1) or 1)
            budget["attack_budget"] = max(0, count - 1)
            payment = {
                "kind": "attack_action",
                "payment": payment_key,
                "attack_count": count,
            }
        budget[payment_key] -= 1

    combatant["turn_budget"] = budget
    if flags:
        combatant["turn_flags"] = flags
    else:
        combatant.pop("turn_flags", None)
    return value, payment


def preflight_attack(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    action: dict[str, Any],
    encounter: dict[str, Any] | None = None,
    allow_out_of_turn: bool = False,
    require_attack_action: bool = True,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Validate an attack declaration without changing any state or rolling."""
    actor_sheet(attacker)
    actor_sheet(target)
    if encounter is not None:
        current = current_combatant(encounter)
        if not allow_out_of_turn and current and current.get("actor_id") != actor_id(attacker):
            raise CombatEngineError("it is not this actor's turn")
        if require_attack_action and not allow_out_of_turn and "attack" not in available_actions(
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
    if weapon_id == "unarmed-strike":
        strength = int(
            (actor_sheet(attacker).get("abilities", {}).get("strength") or {}).get("score", 10)
        )
        modifier = (strength - 10) // 2
        weapon = {
            "item_id": "unarmed-strike",
            "name": "Unarmed Strike",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": modifier + int(actor_derived(attacker).get("proficiency_bonus", 2)),
            "damage_expression": f"1 {'+' if modifier >= 0 else '-'} {abs(modifier)}",
            "damage_type": "bludgeoning",
        }
    elif weapon is None:
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
    ammunition_item_id = weapon.get("ammunition_item_id")
    if ammunition_item_id:
        ammunition = next(
            (
                item
                for item in actor_sheet(attacker).get("inventory", {}).get("items", [])
                if item.get("id") == ammunition_item_id
            ),
            None,
        )
        if (
            not isinstance(ammunition, dict)
            or ammunition.get("kind") != "ammunition"
            or int(ammunition.get("quantity", 0) or 0) < 1
        ):
            raise CombatEngineError("weapon has no linked ammunition remaining")
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
    attack_mode = str(action.get("attack_mode") or weapon.get("attack_type") or "melee").lower()
    if attack_mode not in {"melee", "ranged"}:
        raise CombatEngineError("attack_mode must be melee or ranged")
    weapon_attack_type = str(weapon.get("attack_type") or "melee").lower()
    if weapon_attack_type == "ranged" and attack_mode != "ranged":
        raise CombatEngineError("a ranged weapon cannot make a melee weapon attack")
    if attack_mode == "ranged" and weapon_attack_type != "ranged":
        thrown_range = weapon.get("thrown_range_ft")
        if not isinstance(thrown_range, dict) or not thrown_range.get("normal"):
            raise CombatEngineError("weapon has no recorded ranged attack mode")
    dueling_bonus = _dueling_damage_bonus(attacker, weapon, attack_mode=attack_mode)
    if dueling_bonus and expression:
        expression = f"{expression} + {dueling_bonus}"
    damage_type = str(weapon.get("damage_type") or "")
    additional_damage = deepcopy(list(weapon.get("additional_damage") or []))
    on_hit_effect = str(weapon.get("on_hit_effect") or "").strip()
    range_result = _attack_range(attacker, target, weapon, attack_mode=attack_mode)
    if range_result["disadvantage"]:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("weapon_long_range")
    close_combat_threat_ids: list[str] = []
    attacker_position = _position(attacker.get("position"))
    if attack_mode == "ranged" and encounter is not None and attacker_position is not None:
        for candidate in encounter.get("combatants", []):
            candidate_id = str(candidate.get("actor_id") or "")
            candidate_position = _position(candidate.get("position"))
            if (
                not candidate_id
                or candidate_id == actor_id(attacker)
                or candidate_position is None
                or _grid_distance(attacker_position, candidate_position) > 5
                or not _are_hostile(candidate, attacker)
                or not _can_see(candidate, attacker)
                or _condition_set(candidate.get("conditions"))
                & {
                    "dead",
                    "unconscious",
                    "stunned",
                    "incapacitated",
                    "paralyzed",
                    "petrified",
                }
            ):
                continue
            close_combat_threat_ids.append(candidate_id)
        if close_combat_threat_ids:
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append("hostile_creature_within_5_ft")
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
    extension = apply_rule_event(actor_sheet(attacker), "attack.preflight", rules)
    if extension.status != "committed":
        raise NeedsRulingError(
            "an active rule pack requires an attack choice or ruling",
            missing=[item["mechanic_id"] for item in extension.pending],
        )
    for modifier in extension.modifiers:
        opcode = modifier["op"]
        if opcode == "modifier.add":
            target_field = str(modifier.get("target") or "")
            if target_field == "attack_bonus":
                attack_bonus += int(modifier.get("value", 0) or 0)
            elif target_field == "target_ac":
                target_ac += int(modifier.get("value", 0) or 0)
            else:
                raise CombatEngineError(f"unsupported attack modifier target: {target_field}")
        elif opcode == "advantage.add":
            context["advantage"] = True
            context.setdefault("advantage_sources", []).append(modifier["mechanic_id"])
        elif opcode == "disadvantage.add":
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append(modifier["mechanic_id"])
    sneak_attack = _sneak_attack_plan(
        attacker,
        target,
        weapon=weapon,
        context=context,
        encounter=encounter,
        requested=bool(action.get("use_sneak_attack", False)),
    )
    core_boundary_ids: list[str] = []
    if weapon.get("item_id") == "unarmed-strike":
        core_boundary_ids.append("dnd5e.core.attack.unarmed_strike")
    if attack_mode == "ranged" and range_result.get("enforced"):
        core_boundary_ids.append("dnd5e.core.attack.range")
    if close_combat_threat_ids:
        core_boundary_ids.append("dnd5e.core.attack.ranged_close_combat")
    if ammunition_item_id:
        core_boundary_ids.append("dnd5e.core.attack.ammunition")
    if cover_degree or cover.get("ac_bonus") is not None:
        core_boundary_ids.append("dnd5e.core.attack.cover")
    if helped_by:
        core_boundary_ids.append("dnd5e.core.attack.help")
    return {
        "status": "ready",
        "kind": "attack",
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
        "attack_bonus": attack_bonus,
        "target_ac": target_ac,
        "damage_expression": str(expression),
        "damage_modifiers": (
            [{"source": "Fighting Style: Dueling", "value": dueling_bonus}]
            if dueling_bonus
            else []
        ),
        "damage_type": damage_type,
        "additional_damage": additional_damage,
        "on_hit_effect": on_hit_effect,
        "advantage": bool(context.get("advantage", False)),
        "disadvantage": bool(context.get("disadvantage", False)),
        "advantage_sources": list(context.get("advantage_sources") or []),
        "disadvantage_sources": list(context.get("disadvantage_sources") or []),
        "rulings": list(action.get("rulings") or []),
        "weapon_id": weapon.get("item_id"),
        "attack_mode": attack_mode,
        "resource_cost": deepcopy(weapon.get("resource_cost") or {}),
        "range": range_result,
        "close_combat_threat_ids": close_combat_threat_ids,
        "automatic_critical_on_hit": automatic_critical,
        "ruleset": ruleset,
        "target_uses_death_saves": bool(target.get("death_saves", True)),
        "knock_out": bool(action.get("knock_out", False)),
        "melee_attack": attack_mode == "melee",
        "attacker_was_hidden": bool(attacker.get("hidden", False)),
        "target_can_see_attacker": target_can_see_attacker,
        "helped_by": helped_by,
        "sneak_attack": sneak_attack,
        "halfling_lucky": _has_halfling_lucky(actor_sheet(attacker)),
        "rule_receipts": [
            *core_receipts(
                rules,
                core_boundary_ids,
                "attack.preflight",
            ),
            *extension.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def preflight_spell_attack(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int,
    encounter: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Build an attack only from the spell card's reviewed resolution contract."""
    sheet = actor_sheet(attacker)
    spell = next(
        (
            item
            for item in sheet.get("content", {}).get("spells", [])
            if str(item.get("id") or "") == str(spell_id)
        ),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not recorded on the attacker card")
    resolution = dict(spell.get("resolution") or {})
    if resolution.get("kind") != "spell_attack":
        raise CombatEngineError("spell does not have a reviewed spell-attack resolution")
    attack = dict(resolution.get("attack") or {})
    damage = dict(attack.get("damage") or {})
    derived = deepcopy(actor_derived(attacker))
    spellcasting = dict(derived.get("spellcasting") or {})
    attack_bonus = attack.get("attack_bonus_override")
    if attack_bonus is None:
        attack_bonus = spellcasting.get("attack_bonus")
    if attack_bonus is None:
        raise CombatEngineError("spell attack bonus is not derivable from the attacker card")
    attack_mode = str(attack.get("mode") or "").casefold()
    definition_range = dict(dict(spell.get("definition") or {}).get("range") or {})
    range_ft = attack.get("range_ft_override")
    if range_ft is None:
        if definition_range.get("kind") == "touch":
            range_ft = 5
        else:
            range_ft = definition_range.get("normal_ft")
    range_ft = int(range_ft or 0)
    if range_ft <= 0:
        raise NeedsRulingError(
            "spell attack has no recorded range",
            missing=(f"spell.range:{spell_id}",),
        )
    synthetic_id = f"spell-attack:{spell_id}"
    synthetic = {
        "item_id": synthetic_id,
        "name": str(spell.get("name") or spell_id),
        "attack_type": attack_mode,
        "attack_bonus": int(attack_bonus),
        "damage_expression": scaled_roll_expression(
            damage,
            cast_level=int(cast_level),
            actor_level=int(sheet.get("progression", {}).get("level", 1) or 1),
        ),
        "damage_type": str(damage.get("damage_type") or ""),
        "on_hit_effect": str(attack.get("on_hit_ruling") or ""),
        "properties": [],
    }
    if attack_mode == "ranged":
        synthetic["range_ft"] = {"normal": range_ft, "long": range_ft}
    else:
        synthetic["reach_ft"] = range_ft
    derived.setdefault("inventory", {}).setdefault("weapon_attacks", []).append(synthetic)
    spell_attacker = {**deepcopy(attacker), "derived": derived}
    plan = preflight_attack(
        spell_attacker,
        target,
        action={
            "weapon_id": synthetic_id,
            "attack_mode": attack_mode,
            "context": deepcopy(context or {}),
        },
        encounter=encounter,
        require_attack_action=False,
        rules=rules,
    )
    plan.update(
        kind="spell_attack",
        spell_id=str(spell_id),
        spell_name=str(spell.get("name") or spell_id),
        cast_level=int(cast_level),
        mechanic_id=SPELL_RESOLUTION_MECHANIC_ID,
    )
    plan["rule_receipts"] = [
        *list(plan.get("rule_receipts") or []),
        *core_receipts(
            rules,
            [SPELL_RESOLUTION_MECHANIC_ID],
            "spell.attack.preflight",
        ),
    ]
    return plan


def roll_attack_action(
    *,
    plan: dict[str, Any],
    rng: Any = None,
) -> dict[str, Any]:
    """Roll one prepared attack without rolling damage or changing actor state."""
    attack = resolve_attack(
        armor_class=int(plan["target_ac"]),
        attack_bonus=int(plan["attack_bonus"]),
        advantage=bool(plan.get("advantage")),
        disadvantage=bool(plan.get("disadvantage")),
        reroll_ones=bool(plan.get("halfling_lucky")),
        rng=rng,
    )
    if attack["hit"] and plan.get("automatic_critical_on_hit"):
        attack["critical"] = True
    return {
        **attack,
        "attacker_id": str(plan["attacker_id"]),
        "target_id": str(plan["target_id"]),
        "damage": None,
    }


def apply_attack_ac_bonus(
    attack: dict[str, Any],
    *,
    bonus: int,
    source_id: str,
) -> dict[str, Any]:
    """Re-evaluate a stored attack roll against a reaction AC bonus."""
    value = deepcopy(attack)
    amount = int(bonus)
    if amount <= 0:
        raise CombatEngineError("reaction AC bonus must be positive")
    base_ac = int(value.get("armor_class", 0) or 0)
    effective_ac = base_ac + amount
    hit = bool(
        int(value.get("natural", 0) or 0) == 20
        or (
            not bool(value.get("fumble"))
            and int(value.get("total", 0) or 0) >= effective_ac
        )
    )
    value.update(
        base_armor_class=base_ac,
        armor_class=effective_ac,
        hit=hit,
        critical=bool(hit and value.get("critical")),
        defense={
            "source_id": str(source_id),
            "armor_class_bonus": amount,
            "effective_armor_class": effective_ac,
        },
    )
    return value


def available_attack_defenses(
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    attack: dict[str, Any],
    encounter: dict[str, Any] | None = None,
    extra_defenses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return structured reaction defenses legal after this stored attack roll."""
    if not bool(attack.get("hit")):
        return []
    target_id_value = actor_id(target)
    target_conditions = _condition_set(actor_sheet(target).get("conditions"))
    if encounter is not None:
        combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == target_id_value
            ),
            None,
        )
        if combatant is None:
            raise CombatEngineError("attack target is not a combatant")
        target_conditions |= _condition_set(combatant.get("conditions"))
        if int(dict(combatant.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
            return []
    if target_conditions & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
    }:
        return []
    equipped_melee = any(
        str(item.get("attack_type") or "").casefold() == "melee"
        for item in actor_derived(target).get("inventory", {}).get("weapon_attacks", [])
    )
    options: list[dict[str, Any]] = []
    for activity in actor_sheet(target).get("content", {}).get("activities", []):
        if str(dict(activity.get("activation") or {}).get("type") or "").casefold() != "reaction":
            continue
        mechanic = dict(dict(activity.get("choices") or {}).get("reaction_defense") or {})
        if str(mechanic.get("kind") or "").casefold() != "armor_class_bonus":
            continue
        modes = {
            str(item).casefold() for item in mechanic.get("attack_modes", []) if str(item).strip()
        }
        if str(plan.get("attack_mode") or "").casefold() not in modes:
            continue
        if mechanic.get("requires_visible_attacker") and not plan.get(
            "target_can_see_attacker"
        ):
            continue
        if mechanic.get("requires_wielded_melee_weapon") and not equipped_melee:
            continue
        bonus = int(mechanic.get("bonus", 0) or 0)
        if bonus <= 0:
            continue
        projected = apply_attack_ac_bonus(
            attack,
            bonus=bonus,
            source_id=str(activity.get("id") or ""),
        )
        options.append(
            {
                "id": str(activity.get("id") or ""),
                "name": str(activity.get("name") or "Reaction defense"),
                "kind": "armor_class_bonus",
                "bonus": bonus,
                "projected_hit": bool(projected["hit"]),
                "source_key": str(activity.get("source_key") or ""),
                "rule_refs": deepcopy(list(activity.get("rule_refs") or [])),
            }
        )
    known_ids = {str(item.get("id") or "") for item in options}
    for candidate in extra_defenses or []:
        candidate_id = str(candidate.get("id") or "")
        bonus = int(candidate.get("bonus", 0) or 0)
        if (
            not candidate_id
            or candidate_id in known_ids
            or str(candidate.get("kind") or "").casefold()
            not in {"armor_class_bonus", "spell_armor_class_bonus"}
            or bonus <= 0
        ):
            continue
        projected = apply_attack_ac_bonus(attack, bonus=bonus, source_id=candidate_id)
        options.append(
            {
                **deepcopy(candidate),
                "id": candidate_id,
                "bonus": bonus,
                "projected_hit": bool(projected["hit"]),
            }
        )
        known_ids.add(candidate_id)
    return options


def resolve_attack_damage(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    attack: dict[str, Any],
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve damage and after-effects from one already rolled attack."""
    updated_attacker = deepcopy(attacker)
    updated_target = deepcopy(target)
    result: dict[str, Any] = deepcopy(attack)
    result.update(
        attacker_id=actor_id(attacker),
        target_id=actor_id(target),
        damage=None,
    )
    expression = str(plan.get("damage_expression") or "")
    if attack["hit"] and expression:
        damage_expression = _critical_expression(expression) if attack["critical"] else expression
        damage_roll = roll(damage_expression, rng=rng)
        sneak_plan = dict(plan.get("sneak_attack") or {})
        sneak_roll = None
        if sneak_plan:
            sneak_expression = str(sneak_plan["expression"])
            rolled_sneak_expression = (
                _critical_expression(sneak_expression) if attack["critical"] else sneak_expression
            )
            sneak_roll = roll(rolled_sneak_expression, rng=rng)
        target_sheet = actor_sheet(updated_target)
        rolled_parts = [
            {
                "expression": expression,
                "rolled_expression": damage_expression,
                "rolls": list(damage_roll.rolls),
                "detail": damage_roll.detail,
                "amount": max(0, damage_roll.total + (sneak_roll.total if sneak_roll else 0)),
                "damage_type": str(plan.get("damage_type") or ""),
            }
        ]
        for extra in list(plan.get("additional_damage") or []):
            extra_expression = str(extra.get("damage_expression") or "")
            if not extra_expression:
                continue
            rolled_expression = (
                _critical_expression(extra_expression) if attack["critical"] else extra_expression
            )
            extra_roll = roll(rolled_expression, rng=rng)
            rolled_parts.append(
                {
                    "expression": extra_expression,
                    "rolled_expression": rolled_expression,
                    "rolls": list(extra_roll.rolls),
                    "detail": extra_roll.detail,
                    "amount": max(0, extra_roll.total),
                    "damage_type": str(extra.get("damage_type") or ""),
                }
            )
        if len(rolled_parts) == 1:
            damage = apply_damage_to_sheet(
                target_sheet,
                amount=rolled_parts[0]["amount"],
                damage_type=rolled_parts[0]["damage_type"],
                source=actor_id(attacker),
                critical=bool(attack["critical"]),
                ruleset=str(plan.get("ruleset") or "2014"),
                death_saves=bool(plan.get("target_uses_death_saves", True)),
                knock_out=bool(plan.get("knock_out", False)),
                melee=bool(plan.get("melee_attack", False)),
            )
        else:
            damage = apply_damage_parts_to_sheet(
                target_sheet,
                rolled_parts,
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
            "roll_parts": rolled_parts,
        }
        if sneak_roll is not None:
            result["sneak_attack"] = {
                **sneak_plan,
                "used": True,
                "rolled_expression": sneak_roll.expression,
                "rolls": list(sneak_roll.rolls),
                "total": sneak_roll.total,
                "detail": sneak_roll.detail,
            }
            result["damage"]["sneak_attack"] = deepcopy(result["sneak_attack"])
        if plan.get("on_hit_effect"):
            result["on_hit_ruling"] = {
                "required": True,
                "effect": str(plan["on_hit_effect"]),
            }
    elif plan.get("sneak_attack"):
        result["sneak_attack"] = {**dict(plan["sneak_attack"]), "used": False}
    was_hidden = bool(plan.get("attacker_was_hidden", updated_attacker.get("hidden")))
    if was_hidden:
        updated_attacker["hidden"] = False
        result["reveals_attacker"] = True
    resolution_boundaries: list[str] = []
    if was_hidden:
        resolution_boundaries.append("dnd5e.core.attack.hidden_reveal")
    if isinstance(result.get("damage"), dict):
        resolution_boundaries.append("dnd5e.core.damage.zero_hp")
        if bool(plan.get("knock_out", False)):
            resolution_boundaries.append("dnd5e.core.damage.knockout")
    extension_receipts: list[dict[str, Any]] = [
        *list(plan.get("rule_receipts") or []),
        *core_receipts(
            rules,
            resolution_boundaries,
            "attack.resolve",
        ),
    ]
    facts = {
        "kind": "attack",
        "hit": bool(result.get("hit")),
        "critical": bool(result.get("critical")),
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
    }
    attacker_rules = apply_rule_event(
        actor_sheet(updated_attacker),
        "attack.after",
        context_with_facts(rules, **facts, subject="attacker"),
    )
    target_rules = apply_rule_event(
        actor_sheet(updated_target),
        "attack.after",
        context_with_facts(rules, **facts, subject="target"),
    )
    updated_attacker["sheet"] = attacker_rules.sheet
    updated_target["sheet"] = target_rules.sheet
    extension_receipts.extend(attacker_rules.receipts)
    extension_receipts.extend(target_rules.receipts)
    result["rule_receipts"] = extension_receipts
    result["ruleset_fingerprint"] = rules.fingerprint if rules else ""
    return updated_attacker, updated_target, result


def resolve_attack_action(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve an attack atomically when no post-hit reaction window is needed."""
    attack = roll_attack_action(plan=plan, rng=rng)
    return resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=attack,
        rules=rules,
        rng=rng,
    )


def _sneak_attack_plan(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    weapon: dict[str, Any],
    context: dict[str, Any],
    encounter: dict[str, Any] | None,
    requested: bool,
) -> dict[str, Any] | None:
    """Validate the 2014 Rogue Sneak Attack boundary without inventing eligibility."""
    if not requested:
        return None
    sheet = actor_sheet(attacker)
    feature = next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if item.get("id") == "dnd5e.content.srd2014.feature.rogue-sneak-attack"
            or (
                str(item.get("name") or "").casefold() == "sneak attack"
                and str(item.get("source_key") or "").casefold() == "rogue"
            )
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("Sneak Attack is not recorded on this actor card")
    rogue_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").casefold() == "rogue"
    )
    if rogue_level < 1:
        raise CombatEngineError("Sneak Attack requires at least one Rogue level")
    properties = {str(item).casefold() for item in weapon.get("properties", [])}
    if str(weapon.get("attack_type") or "melee") != "ranged" and "finesse" not in properties:
        raise CombatEngineError("Sneak Attack requires a finesse or ranged weapon")
    advantage = bool(context.get("advantage"))
    disadvantage = bool(context.get("disadvantage"))
    if disadvantage and not advantage:
        raise CombatEngineError("Sneak Attack cannot be used while the attack has disadvantage")
    effective_advantage = advantage and not disadvantage
    turn_token = ""
    nearby_enemy = False
    if encounter is not None:
        current = current_combatant(encounter)
        turn_token = (
            f"{int(encounter.get('round', 1))}:"
            f"{int(encounter.get('turn_index', 0))}:"
            f"{str((current or {}).get('actor_id') or '')}"
        )
        attacker_state = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == actor_id(attacker)
            ),
            None,
        )
        if attacker_state is None:
            raise CombatEngineError("Sneak Attack attacker is not in the encounter")
        if dict(attacker_state.get("turn_flags") or {}).get(
            "sneak_attack_turn_token"
        ) == turn_token:
            raise CombatEngineError("Sneak Attack has already been used on this turn")
        target_state = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == actor_id(target)
            ),
            None,
        )
        if target_state is None:
            raise CombatEngineError("Sneak Attack target is not in the encounter")
        target_position = _position(target_state.get("position"))
        target_disposition = _normalize_disposition(target_state.get("disposition"))
        for candidate in encounter.get("combatants", []):
            if candidate.get("actor_id") in {actor_id(attacker), actor_id(target)}:
                continue
            if _condition_set(candidate.get("conditions")) & {
                "dead",
                "unconscious",
                "stunned",
                "incapacitated",
                "paralyzed",
                "petrified",
            }:
                continue
            candidate_position = _position(candidate.get("position"))
            if target_position is None or candidate_position is None:
                continue
            candidate_disposition = _normalize_disposition(candidate.get("disposition"))
            if (
                {target_disposition, candidate_disposition} == {"friendly", "hostile"}
                and _grid_distance(target_position, candidate_position) <= 5
            ):
                nearby_enemy = True
                break
    if not effective_advantage and not nearby_enemy:
        raise CombatEngineError(
            "Sneak Attack needs effective advantage or another active enemy within 5 feet "
            "of the target"
        )
    dice = (rogue_level + 1) // 2
    return {
        "feature_id": str(feature.get("id") or "sneak-attack"),
        "expression": f"{dice}d6",
        "turn_token": turn_token,
        "eligibility": "advantage" if effective_advantage else "adjacent_enemy",
    }


def _dueling_damage_bonus(
    attacker: dict[str, Any], weapon: dict[str, Any], *, attack_mode: str
) -> int:
    sheet = actor_sheet(attacker)
    has_style = any(
        str(item.get("name") or "").casefold() == "fighting style"
        and str(item.get("source_key") or "").casefold() == "fighter"
        and str(dict(item.get("choices") or {}).get("option") or "").casefold() == "dueling"
        for item in sheet.get("content", {}).get("features", [])
    )
    if not has_style or attack_mode != "melee":
        return 0
    weapon_id = str(weapon.get("item_id") or "")
    selected = next(
        (
            item
            for item in sheet.get("inventory", {}).get("items", [])
            if item.get("id") == weapon_id
        ),
        None,
    )
    if selected is None or selected.get("equipped_slot") not in {"main_hand", "off_hand"}:
        return 0
    other_weapons = [
        item
        for item in sheet.get("inventory", {}).get("items", [])
        if item.get("id") != weapon_id
        and item.get("kind") == "weapon"
        and item.get("equipped")
        and item.get("equipped_slot") in {"main_hand", "off_hand"}
    ]
    return 0 if other_weapons else 2


def _validated_multiattack_options(attacker: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = actor_sheet(attacker)
    weapons = {
        str(item.get("item_id") or ""): item
        for item in actor_derived(attacker).get("inventory", {}).get("weapon_attacks", [])
    }
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for activity in sheet.get("content", {}).get("activities", []):
        if str(activity.get("name") or "").casefold() != "multiattack":
            continue
        if str(dict(activity.get("activation") or {}).get("type") or "") != "action":
            raise CombatEngineError("recorded Multiattack must use action activation")
        options = dict(activity.get("choices") or {}).get("multiattack_options")
        if not isinstance(options, list) or not options:
            raise CombatEngineError("recorded Multiattack has no structured options")
        for raw_option in options:
            if not isinstance(raw_option, dict):
                raise CombatEngineError("Multiattack option must be an object")
            option_id = str(raw_option.get("id") or "").strip()
            if not option_id or option_id in seen_ids:
                raise CombatEngineError("Multiattack option ids must be nonempty and unique")
            seen_ids.add(option_id)
            raw_attacks = raw_option.get("attacks")
            if not isinstance(raw_attacks, list) or not raw_attacks:
                raise CombatEngineError("Multiattack option must list its attacks")
            attacks: list[dict[str, Any]] = []
            total = 0
            for raw_attack in raw_attacks:
                if not isinstance(raw_attack, dict):
                    raise CombatEngineError("Multiattack attack entry must be an object")
                weapon_id = str(raw_attack.get("weapon_id") or "")
                attack_mode = str(raw_attack.get("attack_mode") or "melee").lower()
                count = int(raw_attack.get("count", 0) or 0)
                if weapon_id not in weapons:
                    raise CombatEngineError(
                        "Multiattack references a weapon absent from derived attacks"
                    )
                if attack_mode not in {"melee", "ranged"}:
                    raise CombatEngineError("Multiattack attack_mode must be melee or ranged")
                weapon = weapons[weapon_id]
                if attack_mode == "ranged" and str(
                    weapon.get("attack_type") or "melee"
                ) != "ranged":
                    thrown = weapon.get("thrown_range_ft")
                    if not isinstance(thrown, dict) or not thrown.get("normal"):
                        raise CombatEngineError(
                            "Multiattack ranged entry needs a ranged or thrown weapon"
                        )
                if count < 1:
                    raise CombatEngineError("Multiattack attack count must be positive")
                attacks.append(
                    {
                        "weapon_id": weapon_id,
                        "attack_mode": attack_mode,
                        "count": count,
                    }
                )
                total += count
            if total < 2 or total > 10:
                raise CombatEngineError("Multiattack option must contain 2 to 10 attacks")
            result.append(
                {
                    "activity_id": str(activity.get("id") or "multiattack"),
                    "id": option_id,
                    "attacks": attacks,
                }
            )
    return result


def _select_multiattack_option(
    options: list[dict[str, Any]], option_id: str | None
) -> dict[str, Any]:
    if option_id:
        selected = next((item for item in options if item["id"] == option_id), None)
        if selected is None:
            raise CombatEngineError("multiattack_option_id is not recorded on the actor card")
        return selected
    if len(options) != 1:
        raise CombatEngineError("multiattack_option_id is required for this actor")
    return options[0]


def _consume_multiattack_entry(
    entries: Any, weapon_id: str, attack_mode: str
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise CombatEngineError("active Multiattack state is malformed")
    remaining = deepcopy(entries)
    match = next(
        (
            item
            for item in remaining
            if item.get("weapon_id") == weapon_id
            and item.get("attack_mode") == attack_mode
            and int(item.get("count", 0) or 0) > 0
        ),
        None,
    )
    if match is None:
        raise CombatEngineError("attack is not allowed by the remaining Multiattack sequence")
    match["count"] = int(match["count"]) - 1
    return [item for item in remaining if int(item.get("count", 0) or 0) > 0]


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
    ended_effect_ids: list[str] = []
    if adjusted > 0:
        for effect in value.get("effects", []):
            if effect.get("active") and effect.get("kind") == "turn_undead":
                effect["active"] = False
                effect["ended_reason"] = "damaged"
                ended_effect_ids.append(str(effect.get("id") or ""))
        if ended_effect_ids:
            conditions.discard("turned")
    max_hp = int(hp.get("max", before_hp) or before_hp)
    massive_excess = max(0, hp_damage - before_hp)
    became_zero = hp["value"] == 0 and before_hp > 0
    normalized_ruleset = _normalize_ruleset(ruleset or value.get("edition"))
    if became_zero:
        conditions.update({"prone", "unconscious"})
        if knock_out and not melee:
            raise CombatEngineError("only a melee attack can knock a creature out")
        if knock_out and melee:
            if normalized_ruleset == "2024":
                hp["value"] = 1
                conditions.discard("stable")
            else:
                conditions.add("stable")
        elif massive_excess >= max_hp:
            conditions.discard("unconscious")
            conditions.add("dead")
        elif not death_saves:
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
    knocked_out_2024 = became_zero and knock_out and melee and normalized_ruleset == "2024"
    if hp["value"] > 0 and not knocked_out_2024:
        conditions.discard("unconscious")
    value["conditions"] = sorted(conditions)
    if hp["value"] == 0 and ("unconscious" in conditions or "dead" in conditions):
        for effect in value.get("effects", []):
            if effect.get("active") and bool(effect.get("concentration")):
                effect["active"] = False
                effect["ended_reason"] = "unconscious"
                effect_id = str(effect.get("id") or "")
                if effect_id and effect_id not in ended_effect_ids:
                    ended_effect_ids.append(effect_id)
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
        "ended_effect_ids": ended_effect_ids,
        "massive_damage": massive_excess >= max_hp,
    }


def apply_damage_parts_to_sheet(
    sheet: dict[str, Any],
    parts: Iterable[dict[str, Any]],
    *,
    source: str = "",
    critical: bool = False,
    ruleset: str | None = None,
    death_saves: bool = True,
    knock_out: bool = False,
    melee: bool = False,
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
        ruleset=ruleset,
        death_saves=death_saves,
        knock_out=knock_out,
        melee=melee,
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
        "ended_effect_ids": applied["ended_effect_ids"],
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
        reroll_ones=_has_halfling_lucky(value),
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
        final_conditions = set(value.get("conditions", []))
        final_conditions.discard("stable")
        final_conditions.discard("unconscious")
        final_conditions.add("dead")
        value["conditions"] = sorted(final_conditions)
    return {"sheet": value, **result}


def stabilize_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """Make one living creature at 0 HP stable and clear its death-save tally."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 1, "temp": 0}))
    if int(hp.get("value", 0) or 0) != 0:
        raise CombatEngineError("only a creature at 0 hit points can be stabilized")
    conditions = _condition_set(value.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot be stabilized")
    if "stable" in conditions:
        raise CombatEngineError("the creature is already stable")
    before = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    combat["death_saves"] = {"successes": 0, "failures": 0}
    value["conditions"] = sorted(conditions | {"stable", "unconscious"})
    return {
        "sheet": value,
        "status": "stable",
        "before_death_saves": before,
        "after_death_saves": {"successes": 0, "failures": 0},
        "conditions": list(value["conditions"]),
    }


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
                effect["ended_reason"] = "failed_concentration_save"
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

    Explicit token positions, reach values, map bounds/blocked cells, and
    difficult cells crossed by a cell-by-cell path are automated. Other terrain,
    forced-movement causes, and line-of-effect remain DM-rulable.
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
    origin = _position(combatant.get("position"))
    waypoints: list[tuple[float, float]] = []
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
    battle_map = dict(value.get("battle_map") or {})
    difficult_cells = set(battle_map.get("difficult_cells") or [])
    terrain_cost = 0
    if movement_mode == "voluntary" and difficult_cells and distance > 0:
        if path is None and distance > 5:
            raise NeedsRulingError(
                "a cell-by-cell path is required to settle difficult terrain",
                missing=("movement_path_for_difficult_terrain",),
            )
        route = waypoints[1:] if path is not None else [target_position]
        if path is not None and any(
            _grid_distance(left, right) != 5
            for left, right in zip(waypoints, waypoints[1:])
        ):
            raise CombatEngineError(
                "difficult-terrain paths must enumerate each crossed five-foot cell"
            )
        terrain_cost = sum(
            5
            for point in route
            if point is not None and f"{int(point[0])},{int(point[1])}" in difficult_cells
        )
    movement_cost = distance + (distance if crawl else 0) + terrain_cost
    if movement_cost > available:
        raise CombatEngineError("movement exceeds the remaining speed")
    if target_position is not None:
        occupants = [
            item
            for item in value.get("combatants", [])
            if item.get("actor_id") != actor_id_value
            and _position(item.get("position")) == target_position
            and "dead" not in _condition_set(item.get("conditions"))
        ]
        sharing_allowed = bool(combatant.get("can_share_space")) or any(
            bool(item.get("can_share_space")) for item in occupants
        )
        if occupants and not sharing_allowed:
            if movement_mode == "voluntary":
                raise CombatEngineError(
                    "an actor cannot willingly end movement in another creature's space"
                )
            raise NeedsRulingError(
                "an effect-specific ruling is required for an occupied destination",
                missing=("occupied_destination_resolution",),
            )
    turning = dict(combatant.get("turned") or {})
    if (
        movement_mode == "voluntary"
        and "turned" in conditions
        and origin is not None
        and target_position is not None
    ):
        source_id = str(turning.get("source_actor_id") or "")
        source = next(
            (
                item
                for item in value.get("combatants", [])
                if str(item.get("actor_id") or "") == source_id
            ),
            None,
        )
        source_position = _position((source or {}).get("position"))
        if source_position is None:
            raise NeedsRulingError(
                "turned movement requires the turning source position",
                missing=("turn_undead_source_position",),
            )
        before_distance = _grid_distance(origin, source_position)
        after_distance = _grid_distance(target_position, source_position)
        if after_distance <= before_distance:
            raise CombatEngineError(
                "a turned creature must voluntarily move farther from the turning source"
            )
        if before_distance >= 30 and after_distance < 30:
            raise CombatEngineError(
                "a turned creature cannot willingly move within 30 feet of the turning source"
            )
    budget["movement"] = available - movement_cost
    combatant["turn_budget"] = budget
    if destination is not None:
        from sagasmith_dnd.spatial import validate_position

        if battle_map:
            for point in (path or [destination]):
                validate_position(battle_map, point)
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
            reach = _nonnegative_int(threat.get("reach_ft"), default=5)
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
        "escape",
        "help",
        "hide",
        "ready",
        "search",
        "influence",
        "improvise",
        "study",
        "stabilize",
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
    if "turned" in _condition_set(acting.get("conditions")):
        if action not in {"dash", "dodge", "escape"}:
            raise CombatEngineError("a turned creature can use its action only to Dash or escape")
        if action == "dodge" and dict(payload or {}).get("nowhere_to_move") is not True:
            raise CombatEngineError(
                "a turned creature can Dodge only after the DM confirms nowhere to move"
            )
        if action == "escape" and not _condition_set(acting.get("conditions")) & {
            "grappled",
            "restrained",
        }:
            raise CombatEngineError(
                "a turned creature can try to escape only from an effect preventing movement"
            )
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
    elif action == "stabilize":
        if not target_id:
            raise CombatEngineError("stabilize requires a target actor")
        flags["stabilizing"] = {
            "target_id": target_id,
            "payload": deepcopy(payload or {}),
        }
    elif action == "escape":
        flags["escape_declared"] = deepcopy(payload or {})
    elif action in {
        "hide",
        "search",
        "influence",
        "improvise",
        "study",
        "utilize",
        "use_object",
    }:
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
        "turned",
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


def settle_core_activity_effect(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    activity_id: str,
    declaration: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Settle narrow engine-owned effects for canonical Core activity cards."""
    value = deepcopy(encounter)
    action_surge_id = "dnd5e.content.srd2014.feature.fighter-action-surge"
    cunning_action_id = "dnd5e.content.srd2014.feature.rogue-cunning-action"
    if activity_id not in {action_surge_id, cunning_action_id}:
        return value, None
    current = current_combatant(value)
    if current is None or current.get("actor_id") != actor_id_value:
        raise CombatEngineError("this Core activity can be used only on the actor's turn")
    combatant = next(
        item
        for item in value.get("combatants", [])
        if item.get("actor_id") == actor_id_value
    )
    if activity_id == cunning_action_id:
        selected = str(dict(declaration or {}).get("action") or "")
        selected = selected.strip().lower().replace("-", "_").replace(" ", "_")
        if selected not in {"dash", "disengage", "hide"}:
            raise CombatEngineError(
                "Cunning Action declaration.action must be dash, disengage, or hide"
            )
        budget = dict(combatant.get("turn_budget") or {})
        flags = dict(combatant.get("turn_flags") or {})
        if selected == "dash":
            budget["movement"] = int(budget.get("movement", 0) or 0) + int(
                budget.get("speed", 0) or 0
            )
            combatant["turn_budget"] = budget
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
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "cunning_action", "actor_id": actor_id_value, "effect": effect},
        ][-100:]
        return value, effect
    flags = dict(combatant.get("turn_flags") or {})
    if flags.get("action_surge_used"):
        raise CombatEngineError("Action Surge can be used only once on the same turn")
    budget = dict(combatant.get("turn_budget") or {})
    budget["extra_action"] = int(budget.get("extra_action", 0) or 0) + 1
    combatant["turn_budget"] = budget
    flags["action_surge_used"] = True
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
    return value, effect


def resolve_second_wind_to_sheet(
    sheet: dict[str, Any], *, rng: Any = None
) -> dict[str, Any]:
    """Roll and apply the 2014 Fighter's canonical Second Wind healing."""
    value = deepcopy(sheet)
    fighter_level = sum(
        int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").strip().casefold() == "fighter"
    )
    if fighter_level <= 0:
        raise CombatEngineError("Second Wind requires a recorded Fighter class level")
    rolled = asdict(roll("1d10", rng=rng))
    amount = int(rolled["total"]) + fighter_level
    healed = apply_healing_to_sheet(value, amount=amount)
    return {
        "sheet": healed["sheet"],
        "kind": "second_wind",
        "fighter_level": fighter_level,
        "roll": rolled,
        "healing_amount": amount,
        "before_hp": healed["before_hp"],
        "after_hp": healed["after_hp"],
        "applied_amount": healed["amount"],
    }


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


def apply_healing_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
    source_sheet: dict[str, Any] | None = None,
    spell_id: str | None = None,
    spell_level: int | None = None,
) -> dict[str, Any]:
    """Apply healing and settle source-linked spell modifiers before HP clamping."""
    value = deepcopy(sheet)
    hp = dict(value.setdefault("combat", {}).setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before = int(hp.get("value", 0) or 0)
    if "dead" in _condition_set(value.get("conditions")):
        raise CombatEngineError("ordinary healing cannot restore a dead actor")
    requested_amount = int(amount)
    source_supplied = source_sheet is not None or spell_id is not None or spell_level is not None
    if requested_amount < 0 or (requested_amount == 0 and not source_supplied):
        raise CombatEngineError("healing amount must be positive unless a spell rolled zero")
    bonus = 0
    source: dict[str, Any] | None = None
    if source_supplied:
        if source_sheet is None or not spell_id or spell_level is None:
            raise CombatEngineError(
                "spell healing requires source_sheet, spell_id, and spell_level"
            )
        spell = next(
            (
                item
                for item in source_sheet.get("content", {}).get("spells", [])
                if str(item.get("id") or "") == str(spell_id)
            ),
            None,
        )
        if spell is None:
            raise CombatEngineError("healing spell is not recorded on the source actor card")
        base_level = int(spell.get("level", 0) or 0)
        cast_level = int(spell_level)
        if base_level < 1 or cast_level < base_level:
            raise CombatEngineError(
                "spell healing requires a level 1+ spell and a legal cast level"
            )
        disciple = next(
            (
                item
                for item in source_sheet.get("content", {}).get("features", [])
                if item.get("id")
                == "dnd5e.content.srd2014.feature.life-domain-disciple-of-life"
                or (
                    str(item.get("name") or "").casefold() == "disciple of life"
                    and str(item.get("source_key") or "").casefold() == "life domain"
                )
            ),
            None,
        )
        if disciple is not None:
            bonus = 2 + cast_level
        source = {
            "kind": "spell",
            "spell_id": str(spell_id),
            "spell_name": str(spell.get("name") or spell_id),
            "spell_level": cast_level,
            "modifiers": (
                [
                    {
                        "feature_id": str(disciple.get("id") or "disciple-of-life"),
                        "name": "Disciple of Life",
                        "amount": bonus,
                    }
                ]
                if disciple is not None
                else []
            ),
        }
    maximum = int(hp.get("max", before) or before)
    effective_amount = max(0, requested_amount + bonus)
    hp["value"] = min(maximum, before + effective_amount)
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
        "requested_amount": requested_amount,
        "bonus_amount": bonus,
        "effective_amount": effective_amount,
        "source": source,
    }


def resolve_preserve_life_to_sheets(
    source_sheet: dict[str, Any],
    target_sheets: dict[str, dict[str, Any]],
    *,
    allocations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Settle the Life Domain's deterministic Channel Divinity allocation."""
    feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if str(item.get("id") or "").endswith(
                "life-domain-channel-divinity-preserve-life"
            )
            or str(item.get("name") or "").casefold() == "channel divinity: preserve life"
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("source actor does not have Preserve Life")
    cleric_level = next(
        (
            int(item.get("level", 0) or 0)
            for item in source_sheet.get("progression", {}).get("classes", [])
            if str(item.get("name") or "").casefold() == "cleric"
        ),
        0,
    )
    if cleric_level < 2:
        raise CombatEngineError("Preserve Life requires at least two Cleric levels")
    pool = cleric_level * 5
    if not isinstance(allocations, list) or not allocations:
        raise CombatEngineError("Preserve Life requires at least one healing allocation")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise CombatEngineError("each Preserve Life allocation must be an object")
        target_id = str(allocation.get("target_id") or "").strip()
        amount = allocation.get("amount")
        if not target_id or target_id in seen:
            raise CombatEngineError("Preserve Life target ids must be present and unique")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise CombatEngineError("Preserve Life amounts must be positive integers")
        target = target_sheets.get(target_id)
        if target is None:
            raise CombatEngineError(f"Preserve Life target sheet is missing: {target_id}")
        creature_type = str(target.get("progression", {}).get("species") or "").casefold()
        if "undead" in creature_type or "construct" in creature_type:
            raise CombatEngineError("Preserve Life has no effect on Undead or Constructs")
        hp = dict(target.get("combat", {}).get("hp") or {})
        current = int(hp.get("value", 0) or 0)
        maximum = int(hp.get("max", 0) or 0)
        capacity = maximum // 2 - current
        if capacity < 1 or amount > capacity:
            raise CombatEngineError(
                f"Preserve Life allocation would raise {target_id} above half maximum HP"
            )
        seen.add(target_id)
        total += amount
        normalized.append({"target_id": target_id, "amount": amount})
    if total > pool:
        raise CombatEngineError("Preserve Life allocations exceed five times Cleric level")
    updated = {target_id: deepcopy(sheet) for target_id, sheet in target_sheets.items()}
    results: list[dict[str, Any]] = []
    for allocation in normalized:
        target_id = allocation["target_id"]
        healed = apply_healing_to_sheet(updated[target_id], amount=allocation["amount"])
        updated[target_id] = healed["sheet"]
        results.append(
            {
                "target_id": target_id,
                "before_hp": healed["before_hp"],
                "after_hp": healed["after_hp"],
                "amount": healed["amount"],
            }
        )
    return {
        "sheets": updated,
        "pool": pool,
        "allocated": total,
        "remaining_unallocated": pool - total,
        "targets": results,
    }


def resolve_turn_undead_to_sheets(
    source_actor: dict[str, Any],
    target_actors: dict[str, dict[str, Any]],
    *,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve 2014 Turn Undead saves and apply its damage-ended minute effect."""
    source_sheet = actor_sheet(source_actor)
    feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if item.get("id") == "dnd5e.content.srd2014.feature.cleric-channel-divinity"
            and "turn undead" in {
                str(option).strip().casefold()
                for option in dict(item.get("choices") or {}).get("options", [])
            }
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("source actor does not have source-bound Turn Undead")
    cleric_level = sum(
        int(item.get("level", 0) or 0)
        for item in source_sheet.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").casefold() == "cleric"
    )
    if cleric_level < 2:
        raise CombatEngineError("Turn Undead requires at least two Cleric levels")
    save_dc = int(
        dict(actor_derived(source_actor).get("spellcasting") or {}).get("save_dc", 0) or 0
    )
    if save_dc < 1:
        raise CombatEngineError("Turn Undead requires the cleric's canonical spell save DC")
    if not target_actors:
        raise CombatEngineError("Turn Undead requires at least one perceiving undead target")

    updated: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for target_id, target_actor in target_actors.items():
        target_sheet = actor_sheet(target_actor)
        creature_type = str(
            target_sheet.get("progression", {}).get("species") or ""
        ).casefold()
        if "undead" not in creature_type:
            raise CombatEngineError(f"Turn Undead target is not Undead: {target_id}")
        save = resolve_actor_check(
            target_actor,
            kind="save",
            ability="wisdom",
            dc=save_dc,
            rules=rules,
            rng=rng,
        )
        effect_id = None
        if not save["success"]:
            value = deepcopy(target_sheet)
            for effect in value.get("effects", []):
                if effect.get("active") and effect.get("kind") == "turn_undead":
                    effect["active"] = False
                    effect["ended_reason"] = "replaced_by_turn_undead"
            effect_id = f"turn-undead-{uuid4().hex}"
            value.setdefault("effects", []).append(
                {
                    "id": effect_id,
                    "name": "Turn Undead",
                    "kind": "turn_undead",
                    "source": actor_id(source_actor),
                    "active": True,
                    "concentration": False,
                    "duration": {"period": "minute", "remaining": 1},
                    "changes": [],
                    "description": (
                        "Must move as far from the turning source as possible; cannot "
                        "willingly approach within 30 feet; cannot react; action is Dash, "
                        "escape, or Dodge only if nowhere to move. Ends on any damage."
                    ),
                }
            )
            value["conditions"] = sorted(
                _condition_set(value.get("conditions")) | {"turned"}
            )
            updated[target_id] = value
        else:
            updated[target_id] = target_sheet
        results.append(
            {
                "target_id": target_id,
                "save": save,
                "turned": not save["success"],
                "effect_id": effect_id,
            }
        )
    return {
        "sheets": updated,
        "save_dc": save_dc,
        "duration": {"period": "minute", "remaining": 1},
        "targets": results,
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
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    sheet = actor_sheet(actor)
    derived = actor_derived(actor)
    normalized_ruleset = _normalize_ruleset(ruleset or sheet.get("edition"))
    conditions = _condition_set(sheet.get("conditions"))
    exhaustion = int(sheet.get("combat", {}).get("exhaustion", 0) or 0)
    roll_bonus = int(bonus)
    extension = apply_rule_event(sheet, "check.before", rules)
    if extension.status != "committed":
        raise NeedsRulingError(
            "an active rule pack requires a check choice or ruling",
            missing=[item["mechanic_id"] for item in extension.pending],
        )
    for modifier in extension.modifiers:
        if modifier["op"] == "modifier.add" and modifier.get("target") == "check_bonus":
            roll_bonus += int(modifier.get("value", 0) or 0)
        elif modifier["op"] == "advantage.add":
            advantage = True
        elif modifier["op"] == "disadvantage.add":
            disadvantage = True
    normalized_ability = str(ability).strip().casefold().replace(" ", "_")
    armor_stealth_disadvantage = (
        kind in {"ability", "check"}
        and normalized_ability == "stealth"
        and bool(derived.get("stealth_disadvantage", False))
    )
    if armor_stealth_disadvantage:
        disadvantage = True
    boundary_ids = []
    if (
            kind == "save"
            and _long_ability_name(ability) == "dexterity"
            and "restrained" in conditions
    ):
        boundary_ids.append("dnd5e.core.save.restrained_dexterity")
    if armor_stealth_disadvantage:
        boundary_ids.append("dnd5e.core.check.armor_stealth_disadvantage")

    def with_rule_receipts(result: dict[str, Any]) -> dict[str, Any]:
        result["rule_receipts"] = [
            *core_receipts(rules, boundary_ids, "check.resolve"),
            *extension.receipts,
        ]
        result["ruleset_fingerprint"] = rules.fingerprint if rules else ""
        return result
    abilities = dict(sheet.get("abilities") or {})
    if kind not in {"ability", "check", "save", "death_save", "attack"}:
        raise CombatEngineError("unsupported check kind")
    if kind == "save" and _long_ability_name(ability) in {"strength", "dexterity"}:
        automatic = conditions & {"paralyzed", "petrified", "stunned", "unconscious"}
        if automatic:
            return with_rule_receipts({
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
            })
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
    derived_skills = dict(derived.get("skills") or {})
    if kind in {"ability", "check"} and ability in derived_skills:
        return with_rule_receipts(resolve_check(
            dc=dc,
            ability_score=10,
            proficient=False,
            level=int(sheet.get("progression", {}).get("level", 1) or 1),
            bonus=int(derived_skills[ability]) + roll_bonus,
            advantage=advantage,
            disadvantage=disadvantage,
            kind="ability",
            reroll_ones=_has_halfling_lucky(sheet),
            rng=rng,
        ))
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
        return with_rule_receipts(resolve_death_save(
            successes=int(death.get("successes", 0)),
            failures=int(death.get("failures", 0)),
            advantage=advantage,
            disadvantage=disadvantage,
            bonus=roll_bonus,
            reroll_ones=_has_halfling_lucky(sheet),
            rng=rng,
        ))
    return with_rule_receipts(resolve_check(
        dc=dc,
        ability_score=score,
        proficient=proficient,
        level=level,
        bonus=bonus,
        advantage=advantage,
        disadvantage=disadvantage,
        kind="save" if kind == "save" else "ability",
        reroll_ones=_has_halfling_lucky(sheet),
        rng=rng,
    ))


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
    next_index = (int(value.get("turn_index", 0)) + 1) % len(combatants)

    def begin_next_round() -> None:
        nonlocal combatants
        value["round"] = int(value.get("round", 1)) + 1
        joining = [
            item
            for item in value.get("reinforcements", [])
            if int(item.get("join_round", 0) or 0) <= int(value["round"])
        ]
        if joining:
            for item in joining:
                item.pop("join_round", None)
            combatants.extend(joining)
            combatants.sort(
                key=lambda item: (
                    -int(item.get("initiative", 0) or 0),
                    int(item.get("tie_breaker", 0) or 0),
                    str(item.get("actor_id") or ""),
                )
            )
            value["combatants"] = combatants
            joined_ids = {str(item.get("actor_id") or "") for item in joining}
            value["reinforcements"] = [
                item
                for item in value.get("reinforcements", [])
                if str(item.get("actor_id") or "") not in joined_ids
            ]
            value["log"] = [
                *list(value.get("log") or []),
                *[
                    {
                        "type": "reinforcement_joined",
                        "actor_id": item.get("actor_id"),
                        "round": value["round"],
                    }
                    for item in joining
                ],
            ][-100:]
        combatants = list(value.get("combatants") or combatants)

    if next_index == 0:
        begin_next_round()

    # Dead creatures no longer take turns.  Do not apply this to an unconscious
    # combatant that uses death saves: that turn is still required so the save
    # can be resolved at its start.  Keep one dead index if nobody remains able
    # to take a turn so the encounter can still be ended explicitly.
    skipped_dead: list[str] = []
    checked = 0
    while checked < len(combatants) and "dead" in _condition_set(
        combatants[next_index].get("conditions")
    ):
        skipped_dead.append(str(combatants[next_index].get("actor_id") or ""))
        checked += 1
        candidate_index = (next_index + 1) % len(combatants)
        if candidate_index == 0:
            begin_next_round()
        next_index = candidate_index
    if skipped_dead and checked < len(combatants):
        value["log"] = [
            *list(value.get("log") or []),
            *[
                {
                    "type": "turn_skipped",
                    "actor_id": skipped_actor_id,
                    "reason": "dead",
                    "round": int(value.get("round", 1)),
                }
                for skipped_actor_id in skipped_dead
            ],
        ][-100:]
    value["turn_index"] = next_index
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
            extra_action=0,
        )
        if "turned" in _condition_set(next_actor.get("conditions")):
            budget["reaction"] = 0
        if next_actor.get("surprised") and _normalize_ruleset(value.get("ruleset")) == "2014":
            budget.update(
                main_action=0,
                bonus_action=0,
                movement=0,
                reaction=0,
                object_interaction=0,
            )
        next_actor["turn_budget"] = budget
    value["turn_spell_casts"] = {}
    return value


def _has_halfling_lucky(sheet: dict[str, Any]) -> bool:
    return any(
        item.get("id") == "dnd5e.content.srd2014.species-feature.lightfoot-lucky"
        or (
            str(item.get("name") or "").casefold() == "lucky"
            and str(item.get("source_key") or "").casefold() in {"halfling", "lightfoot"}
        )
        for item in sheet.get("content", {}).get("features", [])
    )


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


def _nonnegative_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


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
        "turned",
    }:
        return False
    if int(dict(threat.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
        return False
    return _are_hostile(threat, moving)


def _are_hostile(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Resolve an opposed relationship from factions first, then party disposition."""
    # Disposition is relative to the party, so the check is symmetric: a
    # friendly PC opposes a hostile NPC just as the NPC opposes the PC.
    left_faction = left.get("faction") or left.get("team")
    right_faction = right.get("faction") or right.get("team")
    if left_faction and right_faction:
        return left_faction != right_faction
    left_disposition = _normalize_disposition(left.get("disposition"))
    right_disposition = _normalize_disposition(right.get("disposition"))
    return {left_disposition, right_disposition} == {"hostile", "friendly"}


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
    attacker: dict[str, Any],
    target: dict[str, Any],
    weapon: dict[str, Any],
    *,
    attack_mode: str,
) -> dict[str, Any]:
    """Validate only deterministic range facts when both combatants are positioned."""
    attacker_position = _position(attacker.get("position"))
    target_position = _position(target.get("position"))
    if attacker_position is None or target_position is None:
        return {"enforced": False, "distance_ft": None, "disadvantage": False}
    distance = _grid_distance(attacker_position, target_position)
    range_data = (
        weapon.get("range_ft")
        if str(weapon.get("attack_type") or "melee").lower() == "ranged"
        else weapon.get("thrown_range_ft")
    )
    if attack_mode == "melee":
        reach = _nonnegative_int(weapon.get("reach_ft"), default=5)
        if distance > reach:
            raise CombatEngineError("target is outside melee reach")
        return {
            "enforced": True,
            "distance_ft": distance,
            "normal_ft": reach,
            "long_ft": reach,
            "disadvantage": False,
        }
    if not isinstance(range_data, dict) or not range_data.get("normal"):
        raise NeedsRulingError(
            "weapon ranged attack has no recorded range",
            missing=[f"weapon.range:{weapon.get('item_id') or 'unknown'}"],
        )
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
