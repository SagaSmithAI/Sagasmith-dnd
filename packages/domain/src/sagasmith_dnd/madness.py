"""Source-bound 2014 madness tables and typed effect contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .character_schema import add_effect
from .conditions import apply_condition_change, reconcile_ended_effect_conditions

SOURCE_REF = "bundled:srd2014/08_Gamemastering/Madness.md"
TICKS_PER_MINUTE = 10


class MadnessError(ValueError):
    """A madness roll or cure is outside the bundled 2014 rules."""


_TABLES: dict[str, tuple[tuple[int, int, str, dict[str, Any]], ...]] = {
    "short_term": (
        (1, 20, "retreat_paralysis", {"conditions": ["paralyzed"], "ends_on_damage": True}),
        (
            21,
            30,
            "screaming_laughing_or_weeping",
            {"conditions": ["incapacitated"], "speech": "uncontrolled"},
        ),
        (
            31,
            40,
            "flee_source",
            {
                "conditions": ["frightened"],
                "turn_constraint": "spend_action_and_movement_fleeing_source",
            },
        ),
        (41, 50, "babbling", {"speech": "not_normal", "spellcasting": False}),
        (
            51,
            60,
            "attack_nearest_creature",
            {"turn_constraint": "use_action_to_attack_nearest_creature"},
        ),
        (61, 70, "hallucinations", {"disadvantage": ["ability_checks"]}),
        (
            71,
            75,
            "follow_non_destructive_orders",
            {"behavioral_constraint": "obey_orders_unless_obviously_self_destructive"},
        ),
        (
            76,
            80,
            "eat_strange_substance",
            {"behavioral_constraint": "overpowering_urge_to_eat_strange_substance"},
        ),
        (81, 90, "stunned", {"conditions": ["stunned"]}),
        (91, 100, "unconscious", {"conditions": ["unconscious"]}),
    ),
    "long_term": (
        (1, 10, "repetitive_activity", {"narrative_only": True, "requires_activity_choice": True}),
        (11, 20, "hallucinations", {"disadvantage": ["ability_checks"]}),
        (21, 30, "paranoia", {"disadvantage": ["wisdom_checks", "charisma_checks"]}),
        (
            31,
            40,
            "revulsion",
            {"narrative_only": True, "requires_target_choice": True, "source_effect": "antipathy"},
        ),
        (41, 45, "imagined_potion", {"narrative_only": True, "requires_potion_choice": True}),
        (
            46,
            55,
            "lucky_charm",
            {
                "disadvantage": ["attack_rolls", "ability_checks", "saving_throws"],
                "range_limit_ft": 30,
                "requires_charm_choice": True,
            },
        ),
        (
            56,
            65,
            "blinded_or_deafened",
            {
                "conditional_subroll": "d100",
                "subroll_outcomes": {"1-25": "blinded", "26-100": "deafened"},
            },
        ),
        (
            66,
            75,
            "tremors",
            {
                "disadvantage_for_abilities": ["strength", "dexterity"],
                "applies_to": ["attack_rolls", "ability_checks", "saving_throws"],
            },
        ),
        (
            76,
            85,
            "partial_amnesia",
            {
                "narrative_only": True,
                "retains": ["identity", "racial_traits", "class_features"],
                "forgets": ["other_people", "events_before_onset"],
            },
        ),
        (
            86,
            90,
            "damage_triggered_confusion",
            {
                "trigger": "takes_damage",
                "save": {"ability": "wisdom", "dc": 15},
                "on_failure": "confusion_spell_failed_save",
                "duration_minutes": 1,
            },
        ),
        (91, 95, "cannot_speak", {"speech": False}),
        (
            96,
            100,
            "unwakeable_unconsciousness",
            {"conditions": ["unconscious"], "cannot_wake_by": ["jostling", "damage"]},
        ),
    ),
    "indefinite": (
        (1, 15, "drinking_keeps_me_sane", {"narrative_only": True}),
        (16, 25, "keep_what_i_find", {"narrative_only": True}),
        (
            26,
            30,
            "imitate_another_person",
            {"narrative_only": True, "requires_person_choice": True},
        ),
        (31, 35, "lie_to_be_interesting", {"narrative_only": True}),
        (
            36,
            45,
            "pursue_goal_at_all_costs",
            {"narrative_only": True, "requires_goal_choice": True},
        ),
        (46, 50, "apathy", {"narrative_only": True}),
        (51, 55, "resent_judgment", {"narrative_only": True}),
        (56, 70, "believe_self_superior", {"narrative_only": True}),
        (71, 80, "believe_enemies_hunt_me", {"narrative_only": True}),
        (81, 85, "imaginary_trusted_friend", {"narrative_only": True}),
        (86, 95, "find_seriousness_funny", {"narrative_only": True}),
        (96, 100, "enjoy_killing", {"narrative_only": True}),
    ),
}


def resolve_madness(
    category: str,
    d100: int,
    *,
    duration_die: int | None = None,
    conditional_d100: int | None = None,
) -> dict[str, Any]:
    """Resolve one table roll into a persisted, explicitly typed effect record."""
    table = str(category).strip().casefold()
    if table not in _TABLES:
        raise MadnessError("category must be short_term, long_term, or indefinite")
    if isinstance(d100, bool) or not isinstance(d100, int) or not 1 <= d100 <= 100:
        raise MadnessError("d100 must be an integer from 1 through 100")
    entry = next(row for row in _TABLES[table] if row[0] <= d100 <= row[1])
    mechanics = dict(entry[3])
    if table == "indefinite":
        if duration_die is not None:
            raise MadnessError("indefinite madness has no timed duration die")
        duration: dict[str, Any] = {"kind": "until_cured"}
    else:
        if (
            isinstance(duration_die, bool)
            or not isinstance(duration_die, int)
            or not 1 <= duration_die <= 10
        ):
            raise MadnessError("timed madness requires a d10 duration result from 1 through 10")
        if table == "short_term":
            duration = {"unit": "minute", "amount": duration_die}
        else:
            duration = {"unit": "hour", "amount": duration_die * 10}
    if mechanics.get("conditional_subroll"):
        if (
            isinstance(conditional_d100, bool)
            or not isinstance(conditional_d100, int)
            or not 1 <= conditional_d100 <= 100
        ):
            raise MadnessError("this table result requires an engine-owned conditional d100")
        mechanics["condition"] = "blinded" if conditional_d100 <= 25 else "deafened"
        # The table's subroll selects an actual condition, not just descriptive
        # metadata. Keep the selected condition in the effect projection so the
        # normal condition and recovery machinery can enforce it.
        mechanics["conditions"] = [mechanics["condition"]]
        mechanics.pop("conditional_subroll", None)
    elif conditional_d100 is not None:
        raise MadnessError("conditional_d100 is only used by the 56-65 long-term result")
    changes = [
        {"path": "conditions", "mode": "add", "value": condition}
        for condition in mechanics.get("conditions", [])
    ]
    roll_paths = {
        "attack_rolls": "rolls.attack.disadvantage",
        "ability_checks": "rolls.ability_check.disadvantage",
        "saving_throws": "rolls.saving_throw.disadvantage",
    }
    for roll_kind in mechanics.get("disadvantage", []):
        path = roll_paths.get(roll_kind)
        if path:
            changes.append({"path": path, "mode": "set", "value": True})
    duration_ticks = (
        {"period": duration["unit"], "remaining": duration["amount"]}
        if "unit" in duration
        else {"period": "manual", "remaining": 0}
    )
    runtime_effect = {
        "kind": "timed_conditions",
        "source": SOURCE_REF,
        "active": True,
        "duration": duration_ticks,
        "changes": changes,
        "metadata": {
            "madness": {
                "category": table,
                "roll": d100,
                "range": [entry[0], entry[1]],
                "effect_key": entry[2],
                "mechanics": mechanics,
            }
        },
    }
    return {
        "kind": "madness",
        "category": table,
        "roll": d100,
        "range": [entry[0], entry[1]],
        "effect_key": entry[2],
        "duration": duration,
        "mechanics": mechanics,
        "source_ref": SOURCE_REF,
        "runtime_effect": runtime_effect,
    }


def damage_triggered_confusion_effect_ids(sheet: dict[str, Any]) -> list[str]:
    """Find active long-term madness results whose source trigger is damage."""
    result: list[str] = []
    for effect in sheet.get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("active") is not True
            or effect.get("source") != SOURCE_REF
        ):
            continue
        madness = dict(dict(effect.get("metadata") or {}).get("madness") or {})
        mechanics = dict(madness.get("mechanics") or {})
        if (
            madness.get("category") == "long_term"
            and madness.get("effect_key") == "damage_triggered_confusion"
            and mechanics.get("trigger") == "takes_damage"
            and not madness.get("suppression")
        ):
            result.append(str(effect.get("id") or ""))
    return [effect_id for effect_id in result if effect_id]


def spellcasting_prohibited_effect_ids(sheet: dict[str, Any]) -> list[str]:
    """Return active, unsuppressed 2014 madness effects that prohibit casting."""
    result: list[str] = []
    for effect in sheet.get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("active") is not True
            or effect.get("source") != SOURCE_REF
        ):
            continue
        madness = dict(dict(effect.get("metadata") or {}).get("madness") or {})
        if madness.get("suppression"):
            continue
        mechanics = dict(madness.get("mechanics") or {})
        if mechanics.get("spellcasting") is False:
            effect_id = str(effect.get("id") or "")
            if effect_id:
                result.append(effect_id)
    return result


def active_confusion_effect_ids(sheet: dict[str, Any]) -> list[str]:
    """Return active source-owned madness effects that impose Confusion behavior."""
    result: list[str] = []
    for effect in sheet.get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("kind") != "madness_confusion"
            or effect.get("active") is not True
            or effect.get("source") != SOURCE_REF
            or not isinstance(dict(effect.get("metadata") or {}).get("madness_confusion"), dict)
        ):
            continue
        effect_id = str(effect.get("id") or "")
        if effect_id:
            result.append(effect_id)
    return result


def resolve_confusion_turn(d10: int) -> dict[str, Any]:
    """Resolve the 2014 Confusion spell's turn table to a typed action constraint."""
    if isinstance(d10, bool) or not isinstance(d10, int) or not 1 <= d10 <= 10:
        raise MadnessError("Confusion turn roll must be a d10 result from 1 through 10")
    if d10 == 1:
        key = "move_random_direction"
        mechanics = {"movement": "all_random_direction", "action": "none"}
    elif d10 <= 6:
        key = "no_action_or_movement"
        mechanics = {"movement": "none", "action": "none"}
    elif d10 <= 8:
        key = "attack_random_creature_in_reach"
        mechanics = {
            "movement": "normal",
            "action": "melee_attack_random_creature_in_reach",
            "if_no_target": "act_normally",
        }
    else:
        key = "act_normally"
        mechanics = {"movement": "normal", "action": "normal"}
    return {"roll": d10, "outcome": key, "mechanics": mechanics, "source_ref": SOURCE_REF}


def nearest_creature_ids(distances_ft: dict[str, int]) -> list[str]:
    """Return every creature tied for the shortest authoritative distance."""
    normalized: dict[str, int] = {}
    for actor_id, distance in distances_ft.items():
        key = str(actor_id).strip()
        if (
            not key
            or isinstance(distance, bool)
            or not isinstance(distance, int)
            or distance < 0
        ):
            raise MadnessError("nearest-creature distances require actor IDs and nonnegative feet")
        normalized[key] = distance
    if not normalized:
        return []
    nearest = min(normalized.values())
    return sorted(actor_id for actor_id, distance in normalized.items() if distance == nearest)


def nearest_attack_constraint(
    sheet: dict[str, Any], distances_ft: dict[str, int]
) -> dict[str, Any] | None:
    """Resolve an active 2014 short-term madness nearest-attack constraint.

    Distances are supplied by the authoritative encounter/space engine. This
    helper binds the nearest-target rule to active, source-owned table effects
    and preserves all equidistant targets as legal choices.
    """
    effect_ids: list[str] = []
    for effect in sheet.get("effects", []):
        if (
            not isinstance(effect, dict)
            or effect.get("active") is not True
            or effect.get("source") != SOURCE_REF
        ):
            continue
        madness = dict(dict(effect.get("metadata") or {}).get("madness") or {})
        mechanics = dict(madness.get("mechanics") or {})
        if (
            madness.get("category") == "short_term"
            and madness.get("effect_key") == "attack_nearest_creature"
            and mechanics.get("turn_constraint") == "use_action_to_attack_nearest_creature"
            and not madness.get("suppression")
        ):
            effect_id = str(effect.get("id") or "").strip()
            if effect_id:
                effect_ids.append(effect_id)
    if not effect_ids:
        return None
    return {
        "source_ref": SOURCE_REF,
        "effect_ids": sorted(set(effect_ids)),
        "required_action": "attack",
        "nearest_actor_ids": nearest_creature_ids(distances_ft),
    }


def choose_confusion_random_target(candidate_actor_ids: list[str], d_n: int) -> str:
    """Select the engine-rolled index from an authoritative in-reach candidate list."""
    candidates = [str(actor_id).strip() for actor_id in candidate_actor_ids]
    if not candidates or any(not actor_id for actor_id in candidates):
        raise MadnessError("random Confusion target requires nonempty actor IDs")
    if len(candidates) != len(set(candidates)):
        raise MadnessError("random Confusion target candidates must be unique")
    if isinstance(d_n, bool) or not isinstance(d_n, int) or not 1 <= d_n <= len(candidates):
        raise MadnessError("random Confusion target roll must select a candidate index")
    return candidates[d_n - 1]


def validate_confusion_direction_map(value: Any) -> dict[str, dict[str, int]]:
    """Validate the DM-authored die-face vectors used by 2014 Confusion."""
    if not isinstance(value, dict) or set(value) != {str(face) for face in range(1, 9)}:
        raise MadnessError("Confusion direction map must assign a direction to faces 1 through 8")
    allowed = {
        (dx, dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    }
    result: dict[str, dict[str, int]] = {}
    for face in range(1, 9):
        direction = value[str(face)]
        if not isinstance(direction, dict) or set(direction) != {"dx", "dy"}:
            raise MadnessError("each Confusion direction must contain exactly dx and dy")
        dx, dy = direction["dx"], direction["dy"]
        if (
            isinstance(dx, bool)
            or not isinstance(dx, int)
            or isinstance(dy, bool)
            or not isinstance(dy, int)
            or (dx, dy) not in allowed
        ):
            raise MadnessError("Confusion directions must be one-cell Grid vectors")
        result[str(face)] = {"dx": dx, "dy": dy}
    return result


def apply_damage_triggered_confusion(
    sheet: dict[str, Any],
    *,
    source_effect_id: str,
    damage_taken: int,
    save: dict[str, Any],
    confusion_effect_id: str,
) -> dict[str, Any]:
    """Persist one exact Wisdom-save result and its one-minute Confusion effect."""
    if isinstance(damage_taken, bool) or not isinstance(damage_taken, int) or damage_taken <= 0:
        raise MadnessError("damage trigger requires positive damage actually taken")
    if (
        not isinstance(save, dict)
        or save.get("kind") != "save"
        or save.get("ability") != "wisdom"
    ):
        raise MadnessError("damage-triggered Confusion requires an engine-owned Wisdom save")
    if type(save.get("success")) is not bool or int(save.get("dc", 0) or 0) != 15:
        raise MadnessError("damage-triggered Confusion save must resolve source DC 15")
    if not confusion_effect_id.strip():
        raise MadnessError("Confusion effect requires a stable transaction-bound id")
    result = deepcopy(sheet)
    source_effect = next(
        (item for item in result.get("effects", []) if item.get("id") == source_effect_id), None
    )
    if (
        source_effect is None
        or source_effect.get("active") is not True
        or source_effect.get("source") != SOURCE_REF
        or source_effect_id not in damage_triggered_confusion_effect_ids(result)
    ):
        raise MadnessError(
            "damage trigger must reference an exact active source-owned madness effect"
        )
    event = {
        "trigger": "takes_damage",
        "damage_taken": damage_taken,
        "source_effect_id": source_effect_id,
        "save": deepcopy(save),
        "source_ref": SOURCE_REF,
    }
    if save["success"]:
        return {"sheet": result, "event": {**event, "confusion_applied": False}}

    existing = next(
        (item for item in result.get("effects", []) if item.get("id") == confusion_effect_id), None
    )
    effect = {
        "id": confusion_effect_id,
        "name": "Confusion from long-term madness",
        "kind": "madness_confusion",
        "source": SOURCE_REF,
        "active": True,
        "duration": {"period": "round", "remaining": 10},
        "changes": [],
        "metadata": {
            "madness_confusion": {
                "source_effect_id": source_effect_id,
                "trigger": event,
                "start_turn": "next_turn_start",
            }
        },
    }
    if existing is not None:
        if (
            existing.get("kind") != effect["kind"]
            or existing.get("source") != effect["source"]
            or dict(existing.get("metadata") or {}) != effect["metadata"]
            or existing.get("duration") != effect["duration"]
        ):
            raise MadnessError("Confusion effect id is already bound to another result")
    else:
        result, _ = add_effect(result, effect)
    return {"sheet": result, "event": {**event, "confusion_applied": True}}


def cure_tier(category: str, spell_id: str, *, source_authorizes: bool = False) -> str:
    """Return whether a source-defined cure tier can end this madness category."""
    table = str(category).strip().casefold()
    spell = str(spell_id).strip().casefold().replace(" ", "_")
    if table not in _TABLES:
        raise MadnessError("unknown madness category")
    if spell in {"calm_emotions", "calm_emotion"}:
        return "suppress"
    if spell == "lesser_restoration" and table in {"short_term", "long_term"}:
        return "cure"
    if spell in {"remove_curse", "dispel_evil"}:
        return "cure" if source_authorizes else "requires_source_authorization"
    if spell in {"greater_restoration", "wish", "miracle"}:
        return "cure"
    return "insufficient"


def choose_lucky_charm(
    sheet: dict[str, Any], *, effect_id: str, charm_actor_id: str
) -> dict[str, Any]:
    """Bind a long-term lucky-charm result to one exact campaign actor."""
    normalized_actor_id = str(charm_actor_id or "").strip()
    if not normalized_actor_id:
        raise MadnessError("lucky charm choice requires a campaign actor id")
    result = deepcopy(sheet)
    effect = next(
        (item for item in result.get("effects", []) if item.get("id") == effect_id), None
    )
    if (
        effect is None
        or not effect.get("active")
        or effect.get("source") != SOURCE_REF
    ):
        raise MadnessError("lucky charm choice requires an exact active source-owned effect")
    metadata = dict(effect.get("metadata") or {})
    madness = dict(metadata.get("madness") or {})
    mechanics = dict(madness.get("mechanics") or {})
    if madness.get("effect_key") != "lucky_charm" or mechanics.get("range_limit_ft") != 30:
        raise MadnessError("effect does not require a lucky charm choice")
    if madness.get("suppression"):
        raise MadnessError("a suppressed madness effect cannot receive a choice")
    existing = dict(madness.get("choice") or {})
    if existing and existing != {"kind": "lucky_charm_actor", "actor_id": normalized_actor_id}:
        raise MadnessError("lucky charm choice is already bound to another actor")
    madness["choice"] = {"kind": "lucky_charm_actor", "actor_id": normalized_actor_id}
    metadata["madness"] = madness
    effect["metadata"] = metadata
    return result


def choose_madness_outcome(
    sheet: dict[str, Any], *, effect_id: str, choice: dict[str, Any]
) -> dict[str, Any]:
    """Persist one typed choice required by a source-owned madness result."""
    if not isinstance(choice, dict):
        raise MadnessError("madness choice must be a typed object")
    result = deepcopy(sheet)
    effect = next(
        (item for item in result.get("effects", []) if item.get("id") == effect_id), None
    )
    if (
        effect is None
        or effect.get("active") is not True
        or effect.get("source") != SOURCE_REF
    ):
        raise MadnessError("madness choice requires an exact active source-owned effect")
    metadata = dict(effect.get("metadata") or {})
    madness = dict(metadata.get("madness") or {})
    mechanics = dict(madness.get("mechanics") or {})
    if madness.get("suppression"):
        raise MadnessError("a suppressed madness effect cannot receive a choice")

    required = {
        "repetitive_activity": ("requires_activity_choice", "activity", "activity_id"),
        "revulsion": ("requires_target_choice", "target_actor", "actor_id"),
        "imagined_potion": ("requires_potion_choice", "potion", "potion_name"),
        "lucky_charm": ("requires_charm_choice", "lucky_charm_actor", "actor_id"),
        "imitate_another_person": ("requires_person_choice", "person_actor", "actor_id"),
        "pursue_goal_at_all_costs": ("requires_goal_choice", "goal", "goal_id"),
    }
    effect_key = str(madness.get("effect_key") or "")
    specification = required.get(effect_key)
    if specification is None or mechanics.get(specification[0]) is not True:
        raise MadnessError("madness effect does not require a typed choice")
    _, expected_kind, value_key = specification
    if set(choice) != {"kind", value_key} or choice.get("kind") != expected_kind:
        raise MadnessError("madness choice kind or fields do not match the source result")
    raw_value = choice.get(value_key)
    if not isinstance(raw_value, str):
        raise MadnessError("madness choice value must be a string")
    value = raw_value.strip()
    if not value or len(value) > 256:
        raise MadnessError("madness choice value must contain 1 through 256 characters")
    normalized = {"kind": expected_kind, value_key: value}
    existing = dict(madness.get("choice") or {})
    if existing and existing != normalized:
        raise MadnessError("madness choice is already bound to another outcome")
    madness["choice"] = normalized
    metadata["madness"] = madness
    effect["metadata"] = metadata
    return result


def range_limited_disadvantage_effect_ids(
    sheet: dict[str, Any], *, distance_ft: int, roll_kind: str
) -> list[str]:
    """Return active madness effects imposing a source-defined distance penalty."""
    if isinstance(distance_ft, bool) or not isinstance(distance_ft, int) or distance_ft < 0:
        raise MadnessError("distance must be a nonnegative integer number of feet")
    if roll_kind not in {"attack_rolls", "ability_checks", "saving_throws"}:
        raise MadnessError("unsupported madness roll kind")
    result: list[str] = []
    for effect in sheet.get("effects", []):
        if (
            not isinstance(effect, dict)
            or not effect.get("active")
            or effect.get("source") != SOURCE_REF
        ):
            continue
        madness = dict(dict(effect.get("metadata") or {}).get("madness") or {})
        if madness.get("suppression"):
            continue
        mechanics = dict(madness.get("mechanics") or {})
        limit = mechanics.get("range_limit_ft")
        if limit is None or roll_kind not in set(mechanics.get("disadvantage") or []):
            continue
        choice = dict(madness.get("choice") or {})
        if choice.get("kind") != "lucky_charm_actor" or not choice.get("actor_id"):
            raise MadnessError("lucky charm effect requires an actor choice before rolling")
        if distance_ft > int(limit):
            result.append(str(effect.get("id") or SOURCE_REF))
    return result


def suppress_madness_effect(
    sheet: dict[str, Any],
    *,
    effect_id: str,
    started_elapsed_ticks: int,
    duration_minutes: int = 1,
) -> dict[str, Any]:
    """Suppress one exact active madness effect while preserving its duration.

    The source says Calm Emotions suppresses madness for that spell's duration.
    The caller owns spell authorization and campaign time; the Domain records
    the exact elapsed-tick deadline and removes only this effect's projections.
    """
    if isinstance(started_elapsed_ticks, bool) or not isinstance(started_elapsed_ticks, int):
        raise MadnessError("suppression requires authoritative elapsed ticks")
    if started_elapsed_ticks < 0:
        raise MadnessError("elapsed ticks cannot be negative")
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise MadnessError("suppression duration must be a positive whole minute count")
    if duration_minutes < 1 or duration_minutes > 10:
        raise MadnessError("suppression duration must be 1 through 10 minutes")
    result = deepcopy(sheet)
    effect = next(
        (item for item in result.get("effects", []) if item.get("id") == effect_id), None
    )
    if effect is None or not effect.get("active") or effect.get("source") != SOURCE_REF:
        raise MadnessError("suppression requires an exact active source-owned madness effect")
    metadata = dict(effect.get("metadata") or {})
    madness = dict(metadata.get("madness") or {})
    if not madness or madness.get("suppression"):
        raise MadnessError("madness is missing mechanics or is already suppressed")
    prior_effect = deepcopy(effect)
    madness["suppression"] = {
        "source_ref": "dnd5e.content.srd2014.spell.calm-emotions",
        "started_elapsed_ticks": started_elapsed_ticks,
        "expires_elapsed_ticks": started_elapsed_ticks
        + duration_minutes * TICKS_PER_MINUTE,
        "duration_minutes": duration_minutes,
        "saved_changes": deepcopy(list(effect.get("changes") or [])),
    }
    metadata["madness"] = madness
    effect["metadata"] = metadata
    effect["changes"] = []
    reconcile_ended_effect_conditions(result, ended_effects=[prior_effect])
    return result


def advance_madness_suppression(
    sheet: dict[str, Any], *, elapsed_ticks: int
) -> dict[str, Any]:
    """Resume elapsed Calm Emotions suppressions against campaign game time."""
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise MadnessError("suppression reconciliation requires authoritative elapsed ticks")
    result = deepcopy(sheet)
    resumed: list[str] = []
    for effect in result.get("effects", []):
        if not isinstance(effect, dict) or effect.get("source") != SOURCE_REF:
            continue
        metadata = dict(effect.get("metadata") or {})
        madness = dict(metadata.get("madness") or {})
        suppression = dict(madness.get("suppression") or {})
        if not suppression or elapsed_ticks < int(suppression.get("expires_elapsed_ticks", 0)):
            continue
        saved_changes = deepcopy(list(suppression.get("saved_changes") or []))
        madness.pop("suppression", None)
        metadata["madness"] = madness
        effect["metadata"] = metadata
        if effect.get("active"):
            effect["changes"] = saved_changes
            for change in saved_changes:
                if (
                    isinstance(change, dict)
                    and change.get("path") == "conditions"
                    and change.get("mode") == "add"
                ):
                    apply_condition_change(
                        result,
                        condition_id=str(change.get("value") or ""),
                        add=True,
                    )
            resumed.append(str(effect.get("id") or ""))
        else:
            # A concurrent cure or duration expiry wins over suppression expiry.
            effect["changes"] = []
    return {"sheet": result, "resumed_effect_ids": resumed}
