"""Pure D&D 5e chase state and turn settlement.

The engine models the 2014 Dungeon Master's Guide chase procedure without
owning persistence, authorization, or source review.  The MCP layer supplies
canonical actor snapshots, a campaign random stream, and the reviewed module
transition that ends a particular chase.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, get_args
from uuid import uuid4

from sagasmith_dnd.character_schema import effective_ability_modifier, set_exhaustion_level
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    actor_derived,
    actor_id,
    actor_sheet,
    apply_damage_to_sheet,
    resolve_actor_check,
    start_encounter,
)
from sagasmith_dnd.conditions import apply_condition_change, condition_ids
from sagasmith_dnd.editions import DEFAULT_CHARACTER_EDITION, normalize_dnd_edition
from sagasmith_dnd.engine import roll, roll_d20
from sagasmith_dnd.rule_engine import ResolutionContext

CHASE_BOUNDARY_IDS = (
    "dnd5e.core.chase.sequence",
    "dnd5e.core.chase.dashing",
    "dnd5e.core.chase.urban_complications",
    "dnd5e.core.chase.ending",
)
ChaseManualOutcomeStatus = Literal[
    "caught",
    "destination_reached",
    "quarry_escaped",
    "pursuers_abandoned",
]
CHASE_MANUAL_OUTCOME_STATUS_ORDER = get_args(ChaseManualOutcomeStatus)
CHASE_MANUAL_OUTCOME_STATUSES = frozenset(CHASE_MANUAL_OUTCOME_STATUS_ORDER)


def _conditions(sheet: dict[str, Any]) -> set[str]:
    return condition_ids(sheet.get("conditions"))


def _participant(chase: dict[str, Any], identifier: str) -> dict[str, Any]:
    match = next(
        (
            item
            for item in chase.get("participants", [])
            if str(item.get("actor_id") or "") == identifier
        ),
        None,
    )
    if match is None:
        raise CombatEngineError("actor is not a chase participant")
    return match


def current_chase_participant(chase: dict[str, Any]) -> dict[str, Any] | None:
    """Return the active participant whose chase turn is current."""
    participants = list(chase.get("participants") or [])
    if not chase.get("active", False) or not participants:
        return None
    index = int(chase.get("turn_index", 0) or 0)
    if not 0 <= index < len(participants):
        raise CombatEngineError("chase turn_index is outside the participant list")
    for offset in range(len(participants)):
        participant = participants[(index + offset) % len(participants)]
        if participant.get("active", True):
            return participant
    return None


def start_chase(
    participants: list[dict[str, Any]],
    *,
    quarry_ids: list[str],
    initial_distance_ft: int,
    ruleset: str = DEFAULT_CHARACTER_EDITION,
    scene_id: str | None = None,
    name: str = "Chase",
    close_transition: dict[str, Any] | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Roll initiative and create a theater-of-the-mind chase state."""
    if not participants:
        raise CombatEngineError("chase requires participants")
    try:
        normalized_ruleset = normalize_dnd_edition(ruleset)
    except ValueError as exc:
        raise CombatEngineError(str(exc)) from exc
    if normalized_ruleset != "2014":
        raise CombatEngineError("the structured chase engine currently supports 2014 rules")
    identifiers = [actor_id(actor) for actor in participants]
    normalized_quarry_ids = [str(item) for item in quarry_ids]
    if not normalized_quarry_ids or len(normalized_quarry_ids) != len(set(normalized_quarry_ids)):
        raise CombatEngineError("quarry_ids must contain unique chase participants")
    if not set(normalized_quarry_ids).issubset(identifiers):
        raise CombatEngineError("every quarry must be a chase participant")
    pursuer_ids = [item for item in identifiers if item not in set(normalized_quarry_ids)]
    if not pursuer_ids:
        raise CombatEngineError("chase requires at least one pursuer")
    if isinstance(initial_distance_ft, bool) or int(initial_distance_ft) <= 0:
        raise CombatEngineError("initial_distance_ft must be positive")

    transition = deepcopy(close_transition or {})
    if transition:
        allowed = {"distance_ft", "status", "summary"}
        unknown = set(transition) - allowed
        if unknown:
            raise CombatEngineError(f"unsupported close_transition fields: {sorted(unknown)}")
        distance = int(transition.get("distance_ft", 0) or 0)
        if distance < 0:
            raise CombatEngineError("close_transition distance_ft cannot be negative")
        status = str(transition.get("status") or "").strip()
        summary = str(transition.get("summary") or "").strip()
        if not status or not summary:
            raise CombatEngineError("close_transition requires status and summary")
        transition = {"distance_ft": distance, "status": status, "summary": summary}

    encounter = start_encounter(
        participants,
        ruleset=normalized_ruleset,
        scene_id=scene_id,
        name=name,
        battle_map=None,
        positioning_mode="agent",
        rng=rng,
    )
    actors = {actor_id(actor): actor for actor in participants}
    chase_participants: list[dict[str, Any]] = []
    quarry_set = set(normalized_quarry_ids)
    for combatant in encounter["combatants"]:
        identifier = str(combatant["actor_id"])
        actor = actors[identifier]
        sheet = actor_sheet(actor)
        recorded_walk_speed = actor_derived(actor).get("speed", {}).get("walk")
        base_speed = int(30 if recorded_walk_speed is None else recorded_walk_speed)
        speed_adjustment = actor.get("chase_speed_adjustment_ft", 0)
        if (
            isinstance(speed_adjustment, bool)
            or not isinstance(speed_adjustment, int)
            or not -100 <= speed_adjustment <= 100
        ):
            raise CombatEngineError("chase_speed_adjustment_ft must be an integer from -100 to 100")
        speed = max(0, base_speed + speed_adjustment)
        speed_source_excerpt = str(actor.get("chase_speed_source_excerpt") or "").strip()
        if speed_adjustment and not speed_source_excerpt:
            raise CombatEngineError("a chase speed adjustment requires its reviewed source excerpt")
        role = "quarry" if identifier in quarry_set else "pursuer"
        chase_participants.append(
            {
                "actor_id": identifier,
                "name": str(actor.get("name") or identifier),
                "role": role,
                "initiative": int(combatant["initiative"]),
                "initiative_roll": deepcopy(combatant.get("initiative_roll")),
                "initiative_bonus": int(combatant.get("initiative_bonus", 0) or 0),
                "tie_breaker": int(combatant.get("tie_breaker", 0) or 0),
                "base_speed_ft": base_speed,
                "speed_adjustment_ft": speed_adjustment,
                "speed_source_excerpt": speed_source_excerpt,
                "speed_ft": speed,
                "position_ft": int(initial_distance_ft) if role == "quarry" else 0,
                "dash_count": 0,
                "free_dash_limit": max(
                    0,
                    3 + effective_ability_modifier(sheet, "constitution"),
                ),
                "starting_exhaustion": int(
                    dict(sheet.get("combat") or {}).get("exhaustion", 0) or 0
                ),
                "chase_exhaustion": 0,
                "active": True,
                "dropped_reason": "",
            }
        )
    return {
        "schema_version": 1,
        "id": f"chase-{uuid4().hex}",
        "active": True,
        "name": str(name or "Chase"),
        "scene_id": scene_id,
        "ruleset": normalized_ruleset,
        "mode": "theater_of_the_mind",
        "round": 1,
        "turn_index": 0,
        "quarry_ids": normalized_quarry_ids,
        "pursuer_ids": pursuer_ids,
        "pursuer_passive_perception_max": max(
            (
                10
                if actor_derived(actors[item]).get("passive_perception") is None
                else int(actor_derived(actors[item])["passive_perception"])
            )
            for item in pursuer_ids
        ),
        "participants": chase_participants,
        "pending_complication": None,
        "close_transition": transition or None,
        "log": [],
        "outcome": None,
        "rule_boundary_ids": sorted(
            {
                *CHASE_BOUNDARY_IDS,
                *list(encounter.get("rule_boundary_ids") or []),
            }
        ),
    }


def _check(
    actor: dict[str, Any],
    *,
    dc: int,
    kind: str,
    ability: str,
    rules: ResolutionContext | None,
    rng: Any,
) -> dict[str, Any]:
    return resolve_actor_check(
        actor,
        dc=dc,
        kind=kind,
        ability=ability,
        save_source_kind=("nonmagical_effect" if kind == "save" else None),
        save_effect_conditions=([] if kind == "save" else None),
        ruleset="2014",
        rules=rules,
        rng=rng,
    )


def _choose_check(
    actor: dict[str, Any],
    *,
    choice: str,
    allowed: dict[str, tuple[str, str]],
    dc: int,
    rules: ResolutionContext | None,
    rng: Any,
) -> dict[str, Any]:
    normalized = str(choice or "").strip().casefold().replace(" ", "_")
    if normalized not in allowed:
        raise CombatEngineError("complication_choice must be one of: " + ", ".join(sorted(allowed)))
    kind, ability = allowed[normalized]
    return _check(actor, dc=dc, kind=kind, ability=ability, rules=rules, rng=rng)


def _apply_chase_damage(
    sheet: dict[str, Any],
    *,
    expression: str,
    damage_type: str,
    source: str,
    death_saves: bool,
    rng: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    damage_roll = roll(expression, rng=rng)
    applied = apply_damage_to_sheet(
        sheet,
        amount=damage_roll.total,
        damage_type=damage_type,
        source=source,
        ruleset="2014",
        death_saves=death_saves,
    )
    return applied["sheet"], {
        "expression": damage_roll.expression,
        "rolls": list(damage_roll.rolls),
        "total": damage_roll.total,
        "damage_type": damage_type,
        "applied": {key: value for key, value in applied.items() if key != "sheet"},
    }


def _resolve_urban_complication(
    actor: dict[str, Any],
    *,
    complication: dict[str, Any],
    choice: str,
    death_saves: bool,
    rules: ResolutionContext | None,
    rng: Any,
) -> dict[str, Any]:
    """Resolve the pending DMG Urban Chase Complications result."""
    number = int(complication["number"])
    sheet = actor_sheet(actor)
    updated_actor = deepcopy(actor)
    updated_actor["sheet"] = sheet
    result: dict[str, Any] = {
        "number": number,
        "source_actor_id": complication["source_actor_id"],
        "affected_actor_id": actor_id(actor),
        "movement_penalty_ft": 0,
        "knocked_prone": False,
        "check": None,
        "damage": None,
        "guard_attack_pending": False,
    }

    def settle_check(
        dc: int,
        allowed: dict[str, tuple[str, str]],
        *,
        movement_penalty: int = 0,
        prone: bool = False,
    ) -> None:
        nonlocal sheet, updated_actor
        checked = _choose_check(
            updated_actor,
            choice=choice,
            allowed=allowed,
            dc=dc,
            rules=rules,
            rng=rng,
        )
        result["check"] = checked
        if not checked["success"]:
            result["movement_penalty_ft"] = movement_penalty
            if prone:
                apply_condition_change(sheet, condition_id="prone", add=True)
                updated_actor["sheet"] = sheet
                result["knocked_prone"] = "prone" in _conditions(sheet)

    if number == 1:
        settle_check(15, {"acrobatics": ("ability", "acrobatics")}, movement_penalty=10)
    elif number == 2:
        settle_check(
            10,
            {
                "athletics": ("ability", "athletics"),
                "acrobatics": ("ability", "acrobatics"),
            },
            movement_penalty=10,
        )
    elif number == 3:
        settle_check(10, {"strength": ("save", "strength")}, prone=True)
    elif number == 4:
        settle_check(
            10,
            {
                "acrobatics": ("ability", "acrobatics"),
                "intelligence": ("ability", "intelligence"),
            },
            movement_penalty=10,
        )
    elif number == 5:
        settle_check(10, {"dexterity": ("save", "dexterity")}, prone=True)
    elif number == 6:
        settle_check(10, {"acrobatics": ("ability", "acrobatics")}, movement_penalty=5)
        if result["check"] is not None and not result["check"]["success"]:
            sheet, result["damage"] = _apply_chase_damage(
                sheet,
                expression="1d4",
                damage_type="piercing",
                source="urban chase complication: fighting dogs",
                death_saves=death_saves,
                rng=rng,
            )
    elif number == 7:
        settle_check(
            15,
            {
                "athletics": ("ability", "athletics"),
                "acrobatics": ("ability", "acrobatics"),
                "intimidation": ("ability", "intimidation"),
            },
            movement_penalty=10,
        )
        if result["check"] is not None and not result["check"]["success"]:
            sheet, result["damage"] = _apply_chase_damage(
                sheet,
                expression="2d4",
                damage_type="bludgeoning",
                source="urban chase complication: street brawl",
                death_saves=death_saves,
                rng=rng,
            )
    elif number == 8:
        settle_check(
            10,
            {
                "athletics": ("ability", "athletics"),
                "acrobatics": ("ability", "acrobatics"),
                "intimidation": ("ability", "intimidation"),
            },
            movement_penalty=5,
        )
    elif number == 9:
        result["guard_attack_pending"] = True
    elif number == 10:
        settle_check(10, {"dexterity": ("save", "dexterity")})
        if result["check"] is not None and not result["check"]["success"]:
            sheet, result["damage"] = _apply_chase_damage(
                sheet,
                expression="1d4",
                damage_type="bludgeoning",
                source="urban chase complication: collision",
                death_saves=death_saves,
                rng=rng,
            )
    elif not 11 <= number <= 20:
        raise CombatEngineError("urban chase complication roll must be between 1 and 20")
    result["sheet"] = sheet
    return result


def _guard_attack(
    actor: dict[str, Any],
    *,
    sheet: dict[str, Any],
    moved_ft: int,
    death_saves: bool,
    rng: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if moved_ft < 20:
        return sheet, {"triggered": False, "reason": "moved less than 20 feet"}
    attack = roll_d20(rng=rng)
    attack_total = int(attack["natural"]) + 3
    derived_ac = actor_derived(actor).get("armor_class")
    ac = 10 if derived_ac is None else int(derived_ac)
    hit = attack_total >= ac
    result: dict[str, Any] = {
        "triggered": True,
        "attack_roll": attack,
        "attack_bonus": 3,
        "total": attack_total,
        "target_ac": ac,
        "hit": hit,
        "damage": None,
    }
    if hit:
        sheet, result["damage"] = _apply_chase_damage(
            sheet,
            expression="1d6+1",
            damage_type="piercing",
            source="urban chase complication: overzealous guard spear",
            death_saves=death_saves,
            rng=rng,
        )
    return sheet, result


def _distance_summary(chase: dict[str, Any]) -> dict[str, Any]:
    active_quarries = [
        item
        for item in chase.get("participants", [])
        if item.get("role") == "quarry" and item.get("active", True)
    ]
    active_pursuers = [
        item
        for item in chase.get("participants", [])
        if item.get("role") == "pursuer" and item.get("active", True)
    ]
    pairs = [
        {
            "quarry_id": quarry["actor_id"],
            "pursuer_id": pursuer["actor_id"],
            "distance_ft": max(0, int(quarry["position_ft"]) - int(pursuer["position_ft"])),
        }
        for quarry in active_quarries
        for pursuer in active_pursuers
    ]
    lead = min(pairs, key=lambda item: item["distance_ft"]) if pairs else None
    return {"pairs": pairs, "lead": lead}


def advance_chase_turn(
    chase: dict[str, Any],
    actor: dict[str, Any],
    *,
    actor_id_value: str,
    action: str = "dash",
    complication_choice: str = "",
    stand_from_prone: bool = True,
    quarry_visibility: dict[str, bool] | None = None,
    quarry_actors: dict[str, dict[str, Any]] | None = None,
    death_saves: bool = True,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Settle one ordered chase turn, including checks and the next complication roll."""
    value = deepcopy(chase)
    if not value.get("active", False):
        raise CombatEngineError("chase is not active")
    current = current_chase_participant(value)
    if current is None or str(current["actor_id"]) != str(actor_id_value):
        raise CombatEngineError("it is not this actor's chase turn")
    if actor_id(actor) != str(actor_id_value):
        raise CombatEngineError("actor snapshot does not match the chase turn")
    participant = _participant(value, str(actor_id_value))
    if not participant.get("active", True):
        raise CombatEngineError("inactive chase participants cannot take turns")
    normalized_action = str(action).strip().casefold().replace(" ", "_")
    if normalized_action not in {"dash", "move", "drop_out"}:
        raise CombatEngineError("chase action must be dash, move, or drop_out")

    sheet = actor_sheet(actor)
    actor_for_checks = deepcopy(actor)
    actor_for_checks["sheet"] = sheet
    pending = value.get("pending_complication")
    complication_result = None
    if isinstance(pending, dict):
        complication_result = _resolve_urban_complication(
            actor_for_checks,
            complication=pending,
            choice=complication_choice,
            death_saves=death_saves,
            rules=rules,
            rng=rng,
        )
        sheet = complication_result.pop("sheet")
        actor_for_checks["sheet"] = sheet
        value["pending_complication"] = None

    conditions = _conditions(sheet)
    if int(dict(sheet.get("combat") or {}).get("hp", {}).get("value", 0) or 0) <= 0:
        participant["active"] = False
        participant["dropped_reason"] = "incapacitated"
    speed = int(participant.get("speed_ft", 0) or 0)
    exhaustion = int(dict(sheet.get("combat") or {}).get("exhaustion", 0) or 0)
    if exhaustion >= 5:
        speed = 0
    elif exhaustion >= 2:
        speed //= 2
    stand_cost = 0
    if "prone" in conditions:
        if stand_from_prone and speed > 0:
            apply_condition_change(sheet, condition_id="prone", add=False)
            conditions = _conditions(sheet)
            if "prone" in conditions:
                speed //= 2
            else:
                stand_cost = speed // 2
        else:
            speed //= 2

    movement_ft = (
        max(0, speed - stand_cost) if participant.get("active", True) else 0
    )
    dash_check = None
    exhaustion_gained = 0
    if normalized_action == "dash" and participant.get("active", True):
        participant["dash_count"] = int(participant.get("dash_count", 0) or 0) + 1
        movement_ft += speed
        if participant["dash_count"] > int(participant.get("free_dash_limit", 0) or 0):
            actor_for_checks["sheet"] = sheet
            dash_check = _check(
                actor_for_checks,
                dc=10,
                kind="ability",
                ability="constitution",
                rules=rules,
                rng=rng,
            )
            if not dash_check["success"]:
                sheet = set_exhaustion_level(
                    sheet,
                    min(
                        6,
                        int(dict(sheet.get("combat") or {}).get("exhaustion", 0) or 0) + 1,
                    ),
                )
                combat = sheet["combat"]
                participant["chase_exhaustion"] = (
                    int(participant.get("chase_exhaustion", 0) or 0) + 1
                )
                chase_effect = next(
                    (
                        effect
                        for effect in sheet.get("effects", [])
                        if effect.get("active") and effect.get("kind") == "chase_exhaustion"
                    ),
                    None,
                )
                if chase_effect is None:
                    chase_effect = {
                        "id": f"{value['id']}-exhaustion-{actor_id_value}",
                        "name": "Chase Exhaustion",
                        "kind": "chase_exhaustion",
                        "source": "DMG 2014 chapter 8 chase dashing",
                        "active": True,
                        "concentration": False,
                        "duration": {"period": "manual", "remaining": 0},
                        "changes": [
                            {
                                "path": "combat.exhaustion",
                                "mode": "chase_levels",
                                "value": 1,
                            }
                        ],
                        "description": (
                            "Exhaustion gained during a chase; all recorded levels "
                            "end on a short or long rest."
                        ),
                    }
                    sheet.setdefault("effects", []).append(chase_effect)
                else:
                    change = next(
                        (
                            item
                            for item in chase_effect.get("changes", [])
                            if item.get("path") == "combat.exhaustion"
                            and item.get("mode") == "chase_levels"
                        ),
                        None,
                    )
                    if change is None:
                        raise CombatEngineError("active chase exhaustion effect is malformed")
                    change["value"] = int(change.get("value", 0) or 0) + 1
                exhaustion_gained = 1
                if int(combat["exhaustion"]) >= 6:
                    participant["active"] = False
                    participant["dropped_reason"] = "exhaustion_death"
                elif int(combat["exhaustion"]) >= 5:
                    participant["active"] = False
                    participant["dropped_reason"] = "exhaustion_speed_zero"
    elif normalized_action == "drop_out" and participant.get("active", True):
        movement_ft = 0
        participant["active"] = False
        participant["dropped_reason"] = "voluntary"

    movement_penalty = int((complication_result or {}).get("movement_penalty_ft", 0) or 0)
    moved_ft = max(0, movement_ft - movement_penalty)
    participant["position_ft"] = int(participant.get("position_ft", 0) or 0) + moved_ft
    guard_attack = None
    if (complication_result or {}).get("guard_attack_pending"):
        actor_for_checks["sheet"] = sheet
        sheet, guard_attack = _guard_attack(
            actor_for_checks,
            sheet=sheet,
            moved_ft=moved_ft,
            death_saves=death_saves,
            rng=rng,
        )
    if int(dict(sheet.get("combat") or {}).get("hp", {}).get("value", 0) or 0) <= 0:
        participant["active"] = False
        participant["dropped_reason"] = "incapacitated"

    complication_roll = asdict_roll(roll("1d20", rng=rng))
    next_pending = None
    if int(complication_roll["total"]) <= 10:
        next_pending = {
            "number": int(complication_roll["total"]),
            "source_actor_id": str(actor_id_value),
            "rolled_round": int(value.get("round", 1) or 1),
        }
    value["pending_complication"] = next_pending

    turn_result = {
        "round": int(value.get("round", 1) or 1),
        "actor_id": str(actor_id_value),
        "action": normalized_action,
        "speed_ft": speed,
        "stand_cost_ft": stand_cost,
        "movement_penalty_ft": movement_penalty,
        "moved_ft": moved_ft,
        "position_ft": int(participant["position_ft"]),
        "dash_count": int(participant.get("dash_count", 0) or 0),
        "free_dash_limit": int(participant.get("free_dash_limit", 0) or 0),
        "dash_check": dash_check,
        "exhaustion_gained": exhaustion_gained,
        "complication": complication_result,
        "guard_attack": guard_attack,
        "next_complication_roll": complication_roll,
        "next_complication": deepcopy(next_pending),
    }
    value.setdefault("log", []).append(deepcopy(turn_result))

    participants_list = list(value.get("participants") or [])
    current_index = next(
        index
        for index, item in enumerate(participants_list)
        if str(item.get("actor_id") or "") == str(actor_id_value)
    )
    next_index = (current_index + 1) % len(participants_list)
    round_ended = next_index == 0
    if round_ended:
        value["round"] = int(value.get("round", 1) or 1) + 1
    checked = 0
    while checked < len(participants_list) and not participants_list[next_index].get(
        "active", True
    ):
        checked += 1
        next_index = (next_index + 1) % len(participants_list)
        if next_index == 0 and not round_ended:
            round_ended = True
            value["round"] = int(value.get("round", 1) or 1) + 1
    value["turn_index"] = next_index

    active_pursuers = [
        item
        for item in participants_list
        if item.get("role") == "pursuer" and item.get("active", True)
    ]
    active_quarries = [
        item
        for item in participants_list
        if item.get("role") == "quarry" and item.get("active", True)
    ]
    distance = _distance_summary(value)
    outcome = None
    if not active_pursuers:
        outcome = {"status": "quarry_escaped", "summary": "All pursuers dropped out."}
    elif not active_quarries:
        outcome = {"status": "quarry_incapacitated", "summary": "All quarries dropped out."}
    elif value.get("close_transition") and distance["lead"] is not None:
        transition = dict(value["close_transition"])
        if int(distance["lead"]["distance_ft"]) <= int(transition["distance_ft"]):
            outcome = {
                "status": transition["status"],
                "summary": transition["summary"],
                "distance": deepcopy(distance["lead"]),
            }
    elif distance["lead"] is not None and int(distance["lead"]["distance_ft"]) == 0:
        outcome = {
            "status": "caught",
            "summary": "A pursuer reached the quarry.",
            "distance": deepcopy(distance["lead"]),
        }

    escape_checks = []
    if round_ended and outcome is None:
        visibility = {str(key): bool(item) for key, item in (quarry_visibility or {}).items()}
        raw_passive_ceiling = value.get("pursuer_passive_perception_max")
        passive_ceiling = (
            None if raw_passive_ceiling is None else int(raw_passive_ceiling)
        )
        for quarry in active_quarries:
            quarry_id = str(quarry["actor_id"])
            visible = visibility.get(quarry_id, True)
            if visible:
                escape_checks.append(
                    {
                        "quarry_id": quarry_id,
                        "visible_to_lead_pursuer": True,
                        "automatic_failure": True,
                        "escaped": False,
                    }
                )
                continue
            if passive_ceiling is None:
                raise CombatEngineError("an unseen quarry requires pursuer_passive_perception_max")
            quarry_actor = dict(quarry_actors or {}).get(quarry_id)
            if quarry_actor is None:
                raise CombatEngineError(
                    "an unseen quarry escape check requires its canonical actor snapshot"
                )
            checked = _check(
                quarry_actor,
                dc=passive_ceiling + 1,
                kind="ability",
                ability="stealth",
                rules=rules,
                rng=rng,
            )
            escaped = int(checked["total"]) > passive_ceiling
            escape_checks.append(
                {
                    "quarry_id": quarry_id,
                    "visible_to_lead_pursuer": False,
                    "passive_perception_max": passive_ceiling,
                    "check": checked,
                    "escaped": escaped,
                }
            )
            if escaped:
                quarry["active"] = False
                quarry["dropped_reason"] = "escaped"
        if active_quarries and all(
            not item.get("active", True)
            for item in participants_list
            if item.get("role") == "quarry"
        ):
            outcome = {"status": "quarry_escaped", "summary": "Every quarry escaped."}

    if outcome is not None:
        value["active"] = False
        value["outcome"] = outcome
    turn_result["round_ended"] = round_ended
    turn_result["escape_checks"] = escape_checks
    turn_result["distance"] = distance
    turn_result["outcome"] = deepcopy(outcome)
    return {
        "chase": value,
        "sheet": sheet,
        "turn": turn_result,
    }


def asdict_roll(value: Any) -> dict[str, Any]:
    """Return the stable public form of an engine RollResult."""
    return {
        "expression": value.expression,
        "rolls": list(value.rolls),
        "total": int(value.total),
        "detail": str(value.detail),
    }


def end_chase(
    chase: dict[str, Any],
    *,
    status: str,
    summary: str,
) -> dict[str, Any]:
    """Close an active chase at a reviewed module or DM boundary."""
    value = deepcopy(chase)
    if not value.get("active", False):
        raise CombatEngineError("chase is not active")
    normalized_status = str(status or "").strip()
    normalized_summary = str(summary or "").strip()
    if normalized_status not in CHASE_MANUAL_OUTCOME_STATUSES:
        raise CombatEngineError("unsupported chase outcome status")
    if not normalized_summary:
        raise CombatEngineError("chase outcome summary is required")
    value["active"] = False
    value["outcome"] = {
        "status": normalized_status,
        "summary": normalized_summary,
        "distance": _distance_summary(value)["lead"],
    }
    return value
