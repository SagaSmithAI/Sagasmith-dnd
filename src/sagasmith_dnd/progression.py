"""Audited D&D 5e 2014/2024 single-class level advancement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_dnd.character_schema import normalize_class_spellcasting_profile
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.editions import normalize_dnd_edition
from sagasmith_dnd.engine import ability_modifier, roll
from sagasmith_dnd.resources import resize_bounded_resource
from sagasmith_dnd.spells import (
    PREPARED_SPELL_LIMITS_2024,
    prepared_spell_limit,
    synchronize_prepared_spell_limit,
)
from sagasmith_dnd.vocabulary import PREPARED_SELECTION_MODES

FULL_CASTER_SLOTS: dict[int, tuple[int, ...]] = {
    1: (2,),
    2: (3,),
    3: (4, 2),
    4: (4, 3),
    5: (4, 3, 2),
    6: (4, 3, 3),
    7: (4, 3, 3, 1),
    8: (4, 3, 3, 2),
    9: (4, 3, 3, 3, 1),
    10: (4, 3, 3, 3, 2),
    11: (4, 3, 3, 3, 2, 1),
    12: (4, 3, 3, 3, 2, 1),
    13: (4, 3, 3, 3, 2, 1, 1),
    14: (4, 3, 3, 3, 2, 1, 1),
    15: (4, 3, 3, 3, 2, 1, 1, 1),
    16: (4, 3, 3, 3, 2, 1, 1, 1),
    17: (4, 3, 3, 3, 2, 1, 1, 1, 1),
    18: (4, 3, 3, 3, 3, 1, 1, 1, 1),
    19: (4, 3, 3, 3, 3, 2, 1, 1, 1),
    20: (4, 3, 3, 3, 3, 2, 2, 1, 1),
}

HALF_CASTER_SLOTS: dict[int, tuple[int, ...]] = {
    1: (),
    2: (2,),
    3: (3,),
    4: (3,),
    5: (4, 2),
    6: (4, 2),
    7: (4, 3),
    8: (4, 3),
    9: (4, 3, 2),
    10: (4, 3, 2),
    11: (4, 3, 3),
    12: (4, 3, 3),
    13: (4, 3, 3, 1),
    14: (4, 3, 3, 1),
    15: (4, 3, 3, 2),
    16: (4, 3, 3, 2),
    17: (4, 3, 3, 3, 1),
    18: (4, 3, 3, 3, 1),
    19: (4, 3, 3, 3, 2),
    20: (4, 3, 3, 3, 2),
}

# The 2024 Paladin and Ranger receive Spellcasting at level 1. Later slot
# progression is identical to the 2014 half-caster table.
HALF_CASTER_SLOTS_2024: dict[int, tuple[int, ...]] = {
    **HALF_CASTER_SLOTS,
    1: (2,),
}

PACT_MAGIC: dict[int, tuple[int, int]] = {
    1: (1, 1),
    2: (2, 1),
    3: (2, 2),
    4: (2, 2),
    5: (2, 3),
    6: (2, 3),
    7: (2, 4),
    8: (2, 4),
    9: (2, 5),
    10: (2, 5),
    11: (3, 5),
    12: (3, 5),
    13: (3, 5),
    14: (3, 5),
    15: (3, 5),
    16: (3, 5),
    17: (4, 5),
    18: (4, 5),
    19: (4, 5),
    20: (4, 5),
}

CASTER_CONFIG = {
    "bard": ("charisma", "known", "full"),
    "cleric": ("wisdom", "prepared", "full"),
    "druid": ("wisdom", "prepared", "full"),
    "paladin": ("charisma", "prepared", "half"),
    "ranger": ("wisdom", "known", "half"),
    "sorcerer": ("charisma", "known", "full"),
    "warlock": ("charisma", "known", "pact"),
    "wizard": ("intelligence", "spellbook", "full"),
}

KNOWN_SPELLS = {
    "bard": (4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 15, 16, 18, 19, 19, 20, 22, 22, 22),
    "ranger": (0, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11),
    "sorcerer": (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12, 13, 13, 14, 14, 15, 15, 15, 15),
    "warlock": (2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15),
}

CANTRIPS_KNOWN = {
    "bard": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    "cleric": (3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
    "druid": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    "sorcerer": (4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6),
    "warlock": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
    "wizard": (3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
}

# D&D 5e uses cumulative experience totals: reaching the threshold makes a
# character eligible to advance, but the level transaction is still separate.
EXPERIENCE_THRESHOLDS: tuple[int, ...] = (
    0,
    300,
    900,
    2_700,
    6_500,
    14_000,
    23_000,
    34_000,
    48_000,
    64_000,
    85_000,
    100_000,
    120_000,
    140_000,
    165_000,
    195_000,
    225_000,
    265_000,
    305_000,
    355_000,
)


def experience_status(sheet: dict[str, Any]) -> dict[str, Any]:
    """Return the current cumulative-XP advancement status without mutating the card."""
    progression = dict(sheet.get("progression") or {})
    level = int(progression.get("level", 0) or 0)
    experience = int(progression.get("xp", 0) or 0)
    if level < 1 or level > 20:
        raise CombatEngineError("character level must be from 1 to 20")
    if experience < 0:
        raise CombatEngineError("experience cannot be negative")
    next_level = level + 1 if level < 20 else None
    next_threshold = EXPERIENCE_THRESHOLDS[level] if next_level is not None else None
    return {
        "level": level,
        "xp": experience,
        "current_level_threshold": EXPERIENCE_THRESHOLDS[level - 1],
        "next_level": next_level,
        "next_level_threshold": next_threshold,
        "xp_to_next_level": (
            max(0, int(next_threshold) - experience) if next_threshold is not None else None
        ),
        "eligible": next_threshold is not None and experience >= next_threshold,
    }


def award_experience(sheet: dict[str, Any], *, amount: int) -> dict[str, Any]:
    """Add cumulative XP without silently applying the separate level transaction."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise CombatEngineError("experience award amount must be a positive integer")
    value = deepcopy(sheet)
    before = experience_status(value)
    value.setdefault("progression", {})["xp"] = before["xp"] + amount
    after = experience_status(value)
    return {
        "sheet": value,
        "amount": amount,
        "old_xp": before["xp"],
        "new_xp": after["xp"],
        "advancement": after,
    }


def apply_per_level_hit_point_bonus(
    sheet: dict[str, Any],
    *,
    amount: int,
    source: str,
    adjust_current: bool = False,
) -> dict[str, Any]:
    """Apply a species-style HP bonus and keep an existing HP ledger balanced."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise CombatEngineError("per-level hit-point bonus must be a non-negative integer")
    value = deepcopy(sheet)
    if amount == 0:
        return value
    level = int(value.get("progression", {}).get("level", 0) or 0)
    if level < 1 or level > 20:
        raise CombatEngineError("character level must be from 1 to 20")
    normalized_source = str(source).strip()
    if not normalized_source:
        raise CombatEngineError("per-level hit-point bonus source is required")
    if len(normalized_source) > 300:
        raise CombatEngineError("per-level hit-point bonus source exceeds 300 characters")

    combat = value.setdefault("combat", {})
    hp = combat.setdefault("hp", {})
    total_bonus = amount * level
    hp["max"] = int(hp.get("max", 0) or 0) + total_bonus
    if adjust_current:
        hp["value"] = min(
            int(hp["max"]),
            int(hp.get("value", 0) or 0) + total_bonus,
        )

    # The ledger is optional for imported/manual cards. If it is present, it
    # must describe every existing level so recorded_gain_total remains exact.
    progression = list(combat.setdefault("hp_progression", []))
    if progression:
        by_level = {int(item.get("level", 0) or 0): item for item in progression}
        missing = [item for item in range(1, level + 1) if item not in by_level]
        if missing:
            raise CombatEngineError(
                "hit-point progression must record every existing level before "
                "applying a per-level bonus"
            )
        for existing_level in range(1, level + 1):
            entry = by_level[existing_level]
            entry["value"] = int(entry.get("value", 0) or 0) + amount
            _append_hit_point_adjustment(
                entry,
                {
                    "kind": "per_level_bonus",
                    "amount": amount,
                    "source": normalized_source,
                },
            )
    return value


def apply_constitution_score_hit_point_change(
    sheet: dict[str, Any],
    *,
    previous_score: int,
    new_score: int,
    source: str,
    adjust_current: bool = False,
) -> dict[str, Any]:
    """Apply Constitution's retrospective per-level maximum hit-point change."""

    if any(
        isinstance(score, bool) or not isinstance(score, int) or score < 1 or score > 30
        for score in (previous_score, new_score)
    ):
        raise CombatEngineError("Constitution scores must be integers from 1 to 30")
    normalized_source = str(source).strip()
    if not normalized_source:
        raise CombatEngineError("Constitution hit-point change source is required")
    if len(normalized_source) > 300:
        raise CombatEngineError("Constitution hit-point change source exceeds 300 characters")
    modifier_delta = ability_modifier(new_score) - ability_modifier(previous_score)
    value = deepcopy(sheet)
    if modifier_delta == 0:
        return value
    level = int(value.get("progression", {}).get("level", 0) or 0)
    if level < 1 or level > 20:
        raise CombatEngineError("character level must be from 1 to 20")
    combat = value.setdefault("combat", {})
    hp = combat.setdefault("hp", {})
    total_delta = modifier_delta * level
    new_maximum = int(hp.get("max", 0) or 0) + total_delta
    current = int(hp.get("value", 0) or 0)
    if new_maximum < 1:
        raise CombatEngineError("Constitution change would produce invalid hit points")
    hp["max"] = new_maximum
    hp["value"] = min(
        new_maximum,
        current + total_delta if adjust_current else current,
    )
    progression = list(combat.setdefault("hp_progression", []))
    if progression:
        by_level = {int(item.get("level", 0) or 0): item for item in progression}
        missing = [item for item in range(1, level + 1) if item not in by_level]
        if missing:
            raise CombatEngineError(
                "hit-point progression must record every existing level before "
                "applying a Constitution modifier change"
            )
        for existing_level in range(1, level + 1):
            entry = by_level[existing_level]
            entry["value"] = int(entry.get("value", 0) or 0) + modifier_delta
            _append_hit_point_adjustment(
                entry,
                {
                    "kind": "constitution_modifier_change",
                    "amount": modifier_delta,
                    "source": normalized_source,
                    "previous_score": previous_score,
                    "new_score": new_score,
                },
            )
    return value


def _append_hit_point_adjustment(
    entry: dict[str, Any],
    adjustment: dict[str, Any],
) -> None:
    """Record a retroactive HP cause without rewriting the level's base source."""

    adjustments = list(entry.get("adjustments") or [])
    if adjustment not in adjustments:
        adjustments.append(deepcopy(adjustment))
    entry["adjustments"] = adjustments


def _profile_slot_table(profile: dict[str, Any]) -> dict[int, tuple[int, ...]]:
    progression = str(profile.get("slot_progression") or "none")
    if progression == "full":
        return FULL_CASTER_SLOTS
    if progression == "half":
        return HALF_CASTER_SLOTS
    if progression == "half_round_up":
        return HALF_CASTER_SLOTS_2024
    if progression in {"none", "pact"}:
        return {}
    raise CombatEngineError("class spellcasting slot_progression is invalid")


def _profile_prepared_limit(
    sheet: dict[str, Any], profile: dict[str, Any], class_level: int
) -> int:
    formula = dict(profile.get("prepared_limit") or {})
    if not formula:
        return 0
    divisor = int(formula["class_level_divisor"])
    quotient = (
        (class_level + divisor - 1) // divisor
        if formula["rounding"] == "up"
        else class_level // divisor
    )
    score = int(sheet.get("abilities", {}).get(formula["ability"], {}).get("score", 10) or 10)
    return max(int(formula["minimum"]), quotient + ability_modifier(score))


def _initialize_profile_spellcasting(
    sheet: dict[str, Any],
    *,
    class_name: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    spellcasting = sheet.setdefault("spellcasting", {})
    spellcasting["ability"] = profile["ability"]
    spellcasting["class_lists"] = list(
        dict.fromkeys([*list(spellcasting.get("class_lists") or []), profile["class_list"]])
    )
    spellcasting["ritual_casting"] = bool(profile["ritual_casting"])
    spellcasting["spellbook"] = {
        "enabled": bool(profile["spellbook"]),
        "spell_ids": [],
    }
    preparation = spellcasting.setdefault("preparation", {})
    preparation.update(
        mode=profile["preparation_mode"],
        max_prepared=_profile_prepared_limit(sheet, profile, 1),
        changes_on="long_rest",
        selected_spell_ids=[],
    )
    slot_changes: dict[str, dict[str, int]] = {}
    if profile["slot_progression"] == "pact":
        maximum, slot_level = PACT_MAGIC[1]
        spellcasting["pact_magic"] = {
            "label": "Pact Magic",
            "value": maximum,
            "max": maximum,
            "recovers_on": "short_rest",
            "source_key": class_name,
            "slot_level": slot_level,
        }
    else:
        spellcasting["pact_magic"] = None
        slots = _profile_slot_table(profile).get(1, ())
        spellcasting["spell_slots"] = {
            str(level): {
                "label": f"Level {level} spell slots",
                "value": maximum,
                "max": maximum,
                "recovers_on": "long_rest",
                "source_key": class_name,
                "slot_level": level,
            }
            for level, maximum in enumerate(slots, start=1)
        }
        slot_changes = {
            str(level): {"old_max": 0, "new_max": maximum}
            for level, maximum in enumerate(slots, start=1)
        }
    cantrips = list(profile.get("cantrips_known_by_level") or [])
    spells_known = list(profile.get("leveled_spells_known_by_level") or [])
    return {
        "kind": profile["slot_progression"],
        "ability": profile["ability"],
        "mode": profile["preparation_mode"],
        "slot_changes": slot_changes,
        "max_prepared": int(preparation["max_prepared"]),
        "spell_choices": {
            "cantrips_to_add": cantrips[0] if cantrips else 0,
            "leveled_spells_to_add": spells_known[0] if spells_known else 0,
        },
    }


def initialize_base_class(
    sheet: dict[str, Any],
    *,
    class_name: str,
    class_definition: dict[str, Any],
    skill_choices: list[str],
    tool_choices: list[str] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Materialize the source-reviewed, system-neutral portion of a level-1 class.

    Addon-specific features, equipment bundles, and spellcasting exceptions remain
    separate catalog selections or Agent rulings.  This function owns only the D&D
    invariants common to every base class: hit die/HP, saving throws, proficiencies,
    and the class skill choice.
    """

    value = deepcopy(sheet)
    progression = value.setdefault("progression", {})
    if progression.get("classes"):
        raise CombatEngineError("base-class selection requires an actor with no class")
    if int(progression.get("level", 0) or 0) != 1:
        raise CombatEngineError("base-class selection is available only at level 1")
    normalized_name = str(class_name).strip()
    if not normalized_name:
        raise CombatEngineError("base class needs a name")

    definition = dict(class_definition)
    expected_fields = {
        "armor_proficiencies",
        "hit_die",
        "saving_throw_proficiencies",
        "skill_choice_count",
        "skill_options",
        "tool_proficiencies",
        "weapon_proficiencies",
    }
    optional_fields = {"tool_choice_count", "tool_options", "spellcasting"}
    if (
        not expected_fields.issubset(definition)
        or set(definition) - expected_fields - optional_fields
    ):
        raise CombatEngineError("class_definition has missing or unsupported fields")
    hit_die = definition.get("hit_die")
    if isinstance(hit_die, bool) or not isinstance(hit_die, int) or hit_die not in {6, 8, 10, 12}:
        raise CombatEngineError("class hit_die must be one of 6, 8, 10, or 12")

    ability_names = set(value.get("abilities") or {})
    saving_throws = _normalized_distinct_names(
        definition.get("saving_throw_proficiencies"),
        field="saving_throw_proficiencies",
    )
    if len(saving_throws) != 2 or any(item not in ability_names for item in saving_throws):
        raise CombatEngineError("class needs exactly two valid saving throw proficiencies")
    skill_options = _normalized_distinct_names(
        definition.get("skill_options"), field="skill_options"
    )
    if any(item not in value.get("skills", {}) for item in skill_options):
        raise CombatEngineError("class skill_options contains an unknown skill")
    choice_count = definition.get("skill_choice_count")
    if isinstance(choice_count, bool) or not isinstance(choice_count, int) or choice_count < 0:
        raise CombatEngineError("class skill_choice_count must be a non-negative integer")
    selected_skills = _normalized_distinct_names(skill_choices, field="skill_choices")
    if len(selected_skills) != choice_count:
        raise CombatEngineError(f"class requires exactly {choice_count} distinct skill choices")
    if any(item not in skill_options for item in selected_skills):
        raise CombatEngineError("class skill choice is not one of the reviewed options")
    if any(value["skills"][item].get("proficiency") != "none" for item in selected_skills):
        raise CombatEngineError("class skill choice is already proficient")
    tool_options = _display_distinct_names(definition.get("tool_options", []), field="tool_options")
    tool_choice_count = definition.get("tool_choice_count", 0)
    if (
        isinstance(tool_choice_count, bool)
        or not isinstance(tool_choice_count, int)
        or not 0 <= tool_choice_count <= len(tool_options)
    ):
        raise CombatEngineError("class tool_choice_count is invalid")
    selected_tools = _display_distinct_names(tool_choices or [], field="tool_choices")
    if len(selected_tools) != tool_choice_count:
        raise CombatEngineError(f"class requires exactly {tool_choice_count} distinct tool choices")
    tool_option_keys = {item.casefold(): item for item in tool_options}
    if any(item.casefold() not in tool_option_keys for item in selected_tools):
        raise CombatEngineError("class tool choice is not one of the reviewed options")

    proficiency_fields = {
        "armor": "armor_proficiencies",
        "weapons": "weapon_proficiencies",
        "tools": "tool_proficiencies",
    }
    normalized_proficiencies = {
        target: _display_distinct_names(definition.get(field), field=field)
        for target, field in proficiency_fields.items()
    }
    fixed_tool_keys = {item.casefold() for item in normalized_proficiencies["tools"]}
    if any(item.casefold() in fixed_tool_keys for item in selected_tools):
        raise CombatEngineError("class tool choice duplicates a fixed tool proficiency")
    existing_tool_keys = {
        str(item).casefold()
        for item in value.get("traits", {}).get("proficiencies", {}).get("tools", [])
    }
    if any(item.casefold() in existing_tool_keys for item in selected_tools):
        raise CombatEngineError("class tool choice is already proficient")
    normalized_proficiencies["tools"].extend(selected_tools)

    combat = value.setdefault("combat", {})
    hp = combat.setdefault("hp", {})
    old_max = int(hp.get("max", 0) or 0)
    old_value = int(hp.get("value", 0) or 0)
    if old_max < 1 or old_value != old_max:
        raise CombatEngineError("base-class setup requires full lobby hit points")
    constitution_modifier = _ability_modifier(value, "constitution")
    class_hp = max(1, hit_die + constitution_modifier)
    prior_bonus = old_max - 1
    hp["max"] = class_hp + prior_bonus
    hp["value"] = hp["max"]
    combat.setdefault("hp_progression", []).append(
        {
            "level": 1,
            "method": "manual",
            "value": class_hp,
            "source": source or f"{normalized_name} level 1",
        }
    )
    hit_dice = combat.setdefault("hit_dice", {})
    hit_die_key, hit_die_resource = _class_hit_die_resource(hit_dice, normalized_name, hit_die)
    hit_die_resource["max"] = int(hit_die_resource.get("max", 0) or 0) + 1
    hit_die_resource["value"] = int(hit_die_resource.get("value", 0) or 0) + 1
    hit_dice[hit_die_key] = hit_die_resource

    class_entry: dict[str, Any] = {
        "name": normalized_name,
        "level": 1,
        "subclass": "",
        "hit_die": hit_die,
    }
    spellcasting_materialization: dict[str, Any] | None = None
    if "spellcasting" in definition:
        try:
            spellcasting_profile = normalize_class_spellcasting_profile(definition["spellcasting"])
        except ValueError as error:
            raise CombatEngineError(str(error)) from error
        class_entry["spellcasting"] = spellcasting_profile
        spellcasting_materialization = _initialize_profile_spellcasting(
            value,
            class_name=normalized_name,
            profile=spellcasting_profile,
        )
    progression["classes"] = [class_entry]
    for ability in saving_throws:
        value["abilities"][ability]["save_proficient"] = True
    for skill in selected_skills:
        value["skills"][skill]["proficiency"] = "proficient"
    proficiencies = value.setdefault("traits", {}).setdefault("proficiencies", {})
    for target, additions in normalized_proficiencies.items():
        proficiencies[target] = list(
            dict.fromkeys([*list(proficiencies.get(target) or []), *additions])
        )
    return {
        "sheet": value,
        "class_name": normalized_name,
        "hit_die": hit_die,
        "hit_die_key": hit_die_key,
        "hit_points": {"class_base": class_hp, "prior_bonus": prior_bonus},
        "saving_throw_proficiencies": saving_throws,
        "skill_proficiencies": selected_skills,
        "tool_proficiency_choices": selected_tools,
        "proficiencies": normalized_proficiencies,
        "spellcasting": spellcasting_materialization,
    }


def _normalized_distinct_names(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise CombatEngineError(f"class {field} must be an array")
    normalized = [str(item).strip().casefold().replace(" ", "_") for item in value]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise CombatEngineError(f"class {field} must contain distinct non-empty names")
    return normalized


def _display_distinct_names(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise CombatEngineError(f"class {field} must be an array")
    normalized = [" ".join(str(item).split()) for item in value]
    if any(not item for item in normalized) or len({item.casefold() for item in normalized}) != len(
        normalized
    ):
        raise CombatEngineError(f"class {field} must contain distinct non-empty names")
    return normalized


def advance_single_class_level(
    sheet: dict[str, Any],
    *,
    class_name: str,
    hp_method: str,
    hp_per_level_bonus: int = 0,
    source: str = "",
    source_ref: str = "",
    reason: str = "",
    rng: Any = None,
) -> dict[str, Any]:
    """Advance an existing 2014 or 2024 class exactly one level.

    This transaction settles deterministic card state only. Class features,
    subclass choices, feats, and selected spells remain catalog operations and
    are reported as follow-up choices by the MCP layer.
    """
    value = deepcopy(sheet)
    try:
        edition = normalize_dnd_edition(value.get("edition"))
    except ValueError as exc:
        raise CombatEngineError(str(exc)) from exc
    progression = value.setdefault("progression", {})
    classes = list(progression.get("classes") or [])
    if len(classes) != 1:
        raise CombatEngineError("level advancement currently requires a single-class actor")
    target = classes[0]
    if str(target.get("name") or "").casefold() != str(class_name).strip().casefold():
        raise CombatEngineError("class_name must match the actor's existing class")
    old_level = int(target.get("level", 0) or 0)
    if old_level < 1 or old_level >= 20:
        raise CombatEngineError("the existing class level must be from 1 to 19")
    if int(progression.get("level", 0) or 0) != old_level:
        raise CombatEngineError("single-class total level does not match its class level")
    new_level = old_level + 1

    hit_die = int(target.get("hit_die", 0) or 0)
    if hit_die not in {6, 8, 10, 12}:
        raise CombatEngineError("class hit_die must be one of 6, 8, 10, or 12")
    if isinstance(hp_per_level_bonus, bool) or not isinstance(hp_per_level_bonus, int):
        raise CombatEngineError("hp_per_level_bonus must be an integer")
    if hp_per_level_bonus < 0:
        raise CombatEngineError("hp_per_level_bonus cannot be negative")
    hp_progression = value.setdefault("combat", {}).setdefault("hp_progression", [])
    if any(int(item.get("level", 0) or 0) == new_level for item in hp_progression):
        raise CombatEngineError("hit-point progression already records the new level")
    normalized_method = str(hp_method).strip().casefold().replace("-", "_")
    hp_roll_result: dict[str, Any] | None = None
    if normalized_method == "fixed":
        die_value = hit_die // 2 + 1
    elif normalized_method == "rolled":
        hp_roll_result = asdict(roll(f"1d{hit_die}", rng=rng))
        die_value = int(hp_roll_result["total"])
    else:
        raise CombatEngineError("hp_method must be fixed or rolled")
    constitution_modifier = _ability_modifier(value, "constitution")
    class_hp_gain = max(1, die_value + constitution_modifier)
    hp_gain = class_hp_gain + hp_per_level_bonus

    hp = value.setdefault("combat", {}).setdefault("hp", {})
    old_hp_max = int(hp.get("max", 0) or 0)
    hp["max"] = old_hp_max + hp_gain
    # The 2014 rule increases maximum HP; it does not say that leveling heals
    # damage already taken. Current HP therefore remains unchanged.
    hp_gain_entry = {
        "level": new_level,
        "method": normalized_method,
        "value": hp_gain,
        "source": source or f"{class_name} level {new_level}",
    }
    if source_ref:
        hp_gain_entry["source_ref"] = str(source_ref)
    if reason:
        hp_gain_entry["reason"] = str(reason)
    hp_progression.append(hp_gain_entry)

    hit_dice = value["combat"].setdefault("hit_dice", {})
    hit_die_key, hit_die_resource = _class_hit_die_resource(hit_dice, class_name, hit_die)
    hit_die_resource["max"] = int(hit_die_resource.get("max", 0) or 0) + 1
    hit_die_resource["value"] = int(hit_die_resource.get("value", 0) or 0) + 1
    hit_dice[hit_die_key] = hit_die_resource

    target["level"] = new_level
    progression["classes"] = [target]
    progression["level"] = new_level
    spellcasting = _advance_spellcasting(
        value,
        class_name,
        old_level,
        new_level,
        edition=edition,
        progression_profile=dict(target.get("spellcasting") or {}),
    )
    resource_sync = synchronize_class_feature_resources(value)
    value = resource_sync["sheet"]
    return {
        "sheet": value,
        "status": "committed",
        "class_name": str(target.get("name") or class_name),
        "old_level": old_level,
        "new_level": new_level,
        "hit_points": {
            "method": normalized_method,
            "hit_die": hit_die,
            "die_value": die_value,
            "roll": hp_roll_result,
            "constitution_modifier": constitution_modifier,
            "class_gain": class_hp_gain,
            "per_level_bonus": hp_per_level_bonus,
            "maximum_gain": hp_gain,
            "old_max": old_hp_max,
            "new_max": hp["max"],
            "current_unchanged": int(hp.get("value", 0) or 0),
        },
        "hit_die": {
            "key": hit_die_key,
            "value": hit_die_resource["value"],
            "max": hit_die_resource["max"],
        },
        "spellcasting": spellcasting,
        "spell_choices": _spell_choice_delta(
            class_name,
            old_level,
            new_level,
            edition=edition,
            progression_profile=dict(target.get("spellcasting") or {}),
        ),
        "feature_resource_changes": resource_sync["changes"],
    }


def synchronize_class_feature_resources(sheet: dict[str, Any]) -> dict[str, Any]:
    """Materialize source-card resource and attack scaling without stacking."""

    value = deepcopy(sheet)
    class_levels = {
        str(item.get("name") or "").casefold(): int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
    }
    changes: list[dict[str, Any]] = []
    for feature in value.get("content", {}).get("features", []):
        scaling = dict(feature.get("resource_scaling") or {})
        if not scaling:
            continue
        class_name = str(scaling.get("class_name") or "").casefold()
        class_level = class_levels.get(class_name, 0)
        if class_level < 1:
            raise CombatEngineError(
                "feature resource scaling references a class absent from the actor card"
            )
        new_maximum, unlimited = _scaled_resource_capacity(value, scaling, class_level)
        if new_maximum is None:
            continue
        recovery = str(scaling.get("recovers_on") or "none")
        for raw_level, candidate in sorted(
            dict(scaling.get("recovery_by_level") or {}).items(),
            key=lambda item: int(item[0]),
        ):
            if int(raw_level) <= class_level:
                recovery = str(candidate)
        target = str(scaling.get("target") or "")
        resources = value.setdefault("resources", {})
        if target == "uses":
            old_resource = dict(feature.get("uses") or {})
        else:
            old_resource = dict(resources.get(target) or {})
        old_maximum = int(old_resource.get("max", 0) or 0)
        old_value = int(old_resource.get("value", old_maximum) or 0)
        recovery_requirements = dict(old_resource.get("recovery_requirements") or {})
        recovery_amounts = dict(
            scaling.get("recovery_amounts") or old_resource.get("recovery_amounts") or {}
        )
        updated = {
            "label": str(scaling.get("label") or old_resource.get("label") or target),
            "value": old_value,
            "max": old_maximum,
            "recovers_on": recovery,
            "source_key": str(scaling.get("class_name") or old_resource.get("source_key") or ""),
            "slot_level": int(old_resource.get("slot_level", 0) or 0),
        }
        if recovery_requirements:
            updated["recovery_requirements"] = recovery_requirements
        if recovery_amounts:
            updated["recovery_amounts"] = recovery_amounts
        resize_bounded_resource(
            updated,
            maximum=new_maximum,
            unlimited=unlimited,
            previous_maximum=old_maximum,
        )
        if target == "uses":
            feature["uses"] = updated
        else:
            if not target:
                raise CombatEngineError("feature resource scaling target is empty")
            resources[target] = updated
        if updated != old_resource:
            changes.append(
                {
                    "feature_id": str(feature.get("id") or ""),
                    "target": target,
                    "class_level": class_level,
                    "old_max": old_maximum,
                    "new_max": new_maximum,
                    "old_value": old_value,
                    "new_value": updated["value"],
                    "recovers_on": recovery,
                    "unlimited": unlimited,
                }
            )
    _remove_unreferenced_shadow_resources(value, changes)
    current_attacks = int(value.setdefault("combat", {}).get("attacks_per_action", 1) or 1)
    scaled_attacks = 1
    attack_sources: list[str] = []
    for feature in value.get("content", {}).get("features", []):
        scaling = dict(feature.get("attack_scaling") or {})
        if not scaling:
            continue
        class_name = str(scaling.get("class_name") or "").casefold()
        class_level = class_levels.get(class_name)
        if class_level is None:
            raise CombatEngineError("attack scaling references a class absent from the actor card")
        candidate = 1
        for raw_level, amount in sorted(
            dict(scaling.get("attacks_per_action_by_level") or {}).items(),
            key=lambda item: int(item[0]),
        ):
            if int(raw_level) <= class_level:
                candidate = int(amount)
        source_id = str(feature.get("id") or feature.get("name") or "")
        if candidate > scaled_attacks:
            scaled_attacks = candidate
            attack_sources = [source_id]
        elif candidate == scaled_attacks and candidate > 1:
            attack_sources.append(source_id)
    new_attacks = max(current_attacks, scaled_attacks)
    if new_attacks != current_attacks:
        value["combat"]["attacks_per_action"] = new_attacks
        changes.append(
            {
                "target": "combat.attacks_per_action",
                "old_value": current_attacks,
                "new_value": new_attacks,
                "source_feature_ids": list(dict.fromkeys(attack_sources)),
            }
        )
    preparation_sync = synchronize_prepared_spell_limit(value)
    value = preparation_sync["sheet"]
    if preparation_sync["change"] is not None:
        changes.append(preparation_sync["change"])
    return {"sheet": value, "changes": changes}


def _remove_unreferenced_shadow_resources(
    sheet: dict[str, Any],
    changes: list[dict[str, Any]],
) -> None:
    """Remove legacy top-level counters shadowed by authoritative card-local uses.

    Early callers could manually seed ``sheet.resources`` for a class feature
    and later apply the structured feature card.  A local-use card deliberately
    leaves ``resource_key`` empty, so the card's ``uses`` counter is the only
    counter consumed by :func:`consume_activity`.  Keeping an unreferenced
    top-level counter with the same label and class creates two independently
    recoverable representations of one rules concept.

    The migration is intentionally conservative: a top-level counter is removed
    only when no card or spell references its key and exactly one scaling
    feature owns an identically labelled, identically sourced local counter.
    """

    content = dict(sheet.get("content") or {})
    referenced_keys: set[str] = set()
    for section in ("activities", "features", "feats"):
        for card in content.get(section, []):
            resource_key = str(card.get("resource_key") or "")
            if resource_key:
                referenced_keys.add(resource_key)
    for spell in content.get("spells", []):
        access = dict(spell.get("access") or {})
        innate_key = str(access.get("innate_resource_key") or "")
        if innate_key:
            referenced_keys.add(innate_key)

    local_owners: dict[tuple[str, str], list[str]] = {}
    for feature in content.get("features", []):
        scaling = dict(feature.get("resource_scaling") or {})
        if str(scaling.get("target") or "") != "uses":
            continue
        uses = dict(feature.get("uses") or {})
        label = str(uses.get("label") or scaling.get("label") or "").strip().casefold()
        source_key = (
            str(uses.get("source_key") or scaling.get("class_name") or "").strip().casefold()
        )
        if not label or not source_key:
            continue
        local_owners.setdefault((label, source_key), []).append(str(feature.get("id") or ""))

    resources = sheet.setdefault("resources", {})
    for resource_key, raw_resource in list(resources.items()):
        if resource_key in referenced_keys:
            continue
        resource = dict(raw_resource or {})
        semantic_key = (
            str(resource.get("label") or "").strip().casefold(),
            str(resource.get("source_key") or "").strip().casefold(),
        )
        owners = local_owners.get(semantic_key, [])
        if len(owners) != 1:
            continue
        del resources[resource_key]
        changes.append(
            {
                "feature_id": owners[0],
                "target": f"resources.{resource_key}",
                "operation": "remove_shadow",
                "old_resource": resource,
            }
        )


def _scaled_resource_capacity(
    sheet: dict[str, Any], scaling: dict[str, Any], class_level: int
) -> tuple[int | None, bool]:
    unlimited_at = int(scaling.get("unlimited_at_level", 0) or 0)
    if unlimited_at and class_level >= unlimited_at:
        return 0, True
    formula = dict(scaling.get("maximum_formula") or {})
    if formula:
        kind = str(formula.get("kind") or "")
        if kind == "class_level":
            base = class_level
        elif kind == "ability_modifier":
            ability = str(formula.get("ability") or "")
            base = _ability_modifier(sheet, ability)
        else:
            raise CombatEngineError("feature resource scaling formula is invalid")
        return (
            max(
                int(formula.get("minimum", 0) or 0),
                base * int(formula.get("multiplier", 1) or 1) + int(formula.get("offset", 0) or 0),
            ),
            False,
        )
    maximum: int | None = None
    for raw_level, candidate in sorted(
        dict(scaling.get("maximum_by_level") or {}).items(),
        key=lambda item: int(item[0]),
    ):
        if int(raw_level) <= class_level:
            maximum = int(candidate)
    return maximum, False


def _advance_spellcasting(
    sheet: dict[str, Any],
    class_name: str,
    old_level: int,
    new_level: int,
    *,
    edition: str,
    progression_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = class_name.casefold()
    profile = dict(progression_profile or {})
    config = CASTER_CONFIG.get(key)
    if not profile and config is None:
        return {"kind": "none", "slot_changes": {}}
    if profile:
        ability = str(profile["ability"])
        kind = str(profile["slot_progression"])
        mode = str(profile["preparation_mode"])
    else:
        assert config is not None
        ability, legacy_mode, kind = config
        mode = "spellbook" if key == "wizard" else "prepared" if edition == "2024" else legacy_mode
    spellcasting = sheet.setdefault("spellcasting", {})
    spellcasting["ability"] = spellcasting.get("ability") or ability
    preparation = spellcasting.setdefault("preparation", {})
    preparation["mode"] = mode
    preparation.setdefault("selected_spell_ids", [])
    preparation["changes_on"] = "long_rest"
    spellcasting.setdefault("spellbook", {"enabled": False, "spell_ids": []})
    if key == "wizard" or profile.get("spellbook") is True:
        spellcasting["spellbook"]["enabled"] = True
    if profile:
        spellcasting["ritual_casting"] = bool(profile["ritual_casting"])
        spellcasting["class_lists"] = list(
            dict.fromkeys(
                [
                    *list(spellcasting.get("class_lists") or []),
                    str(profile["class_list"]),
                ]
            )
        )
    slot_changes: dict[str, dict[str, int]] = {}
    if kind == "pact":
        old_max, old_slot_level = PACT_MAGIC[old_level]
        new_max, new_slot_level = PACT_MAGIC[new_level]
        pact = dict(spellcasting.get("pact_magic") or {})
        resize_bounded_resource(
            pact,
            maximum=new_max,
            previous_maximum=old_max,
        )
        pact.update(
            label="Pact Magic",
            recovers_on="short_rest",
            source_key=class_name,
            slot_level=new_slot_level,
        )
        spellcasting["pact_magic"] = pact
        slot_changes["pact_magic"] = {
            "old_max": old_max,
            "new_max": new_max,
            "old_slot_level": old_slot_level,
            "new_slot_level": new_slot_level,
        }
    else:
        table = (
            _profile_slot_table(profile)
            if profile
            else FULL_CASTER_SLOTS
            if kind == "full"
            else HALF_CASTER_SLOTS_2024
            if edition == "2024"
            else HALF_CASTER_SLOTS
        )
        old_slots = table[old_level]
        new_slots = table[new_level]
        resources = spellcasting.setdefault("spell_slots", {})
        for slot_level in range(1, max(len(old_slots), len(new_slots)) + 1):
            old_max = old_slots[slot_level - 1] if slot_level <= len(old_slots) else 0
            new_max = new_slots[slot_level - 1] if slot_level <= len(new_slots) else 0
            if new_max == 0:
                continue
            resource = dict(resources.get(str(slot_level)) or {})
            resize_bounded_resource(
                resource,
                maximum=new_max,
                previous_maximum=old_max,
            )
            resource.update(
                label=f"Level {slot_level} spell slots",
                recovers_on="long_rest",
                source_key=class_name,
                slot_level=slot_level,
            )
            resources[str(slot_level)] = resource
            if old_max != new_max:
                slot_changes[str(slot_level)] = {"old_max": old_max, "new_max": new_max}
    if mode in PREPARED_SELECTION_MODES:
        preparation["max_prepared"] = (
            _profile_prepared_limit(sheet, profile, new_level)
            if profile
            else prepared_spell_limit(
                sheet,
                normalize_dnd_edition(sheet.get("edition")),
                key,
                new_level,
            )
        )
    return {
        "kind": kind,
        "ability": ability,
        "mode": mode,
        "slot_changes": slot_changes,
        "max_prepared": int(preparation.get("max_prepared", 0) or 0),
    }


def _spell_choice_delta(
    class_name: str,
    old_level: int,
    new_level: int,
    *,
    edition: str = "2014",
    progression_profile: dict[str, Any] | None = None,
) -> dict[str, int]:
    key = class_name.casefold()
    result = {"cantrips_to_add": 0, "leveled_spells_to_add": 0}
    profile = dict(progression_profile or {})
    if profile:
        cantrips = list(profile.get("cantrips_known_by_level") or [])
        spells_known = list(profile.get("leveled_spells_known_by_level") or [])
        if cantrips:
            result["cantrips_to_add"] = max(0, cantrips[new_level - 1] - cantrips[old_level - 1])
        if spells_known:
            result["leveled_spells_to_add"] = max(
                0, spells_known[new_level - 1] - spells_known[old_level - 1]
            )
        return result
    cantrips = CANTRIPS_KNOWN.get(key)
    if cantrips:
        result["cantrips_to_add"] = max(0, cantrips[new_level - 1] - cantrips[old_level - 1])
    if edition == "2024":
        prepared = PREPARED_SPELL_LIMITS_2024.get(key)
        if prepared:
            result["leveled_spells_to_add"] = max(
                0,
                prepared[new_level - 1] - prepared[old_level - 1],
            )
        if key == "wizard":
            result["spellbook_spells_to_add"] = 2
        return result
    known = KNOWN_SPELLS.get(key)
    if known:
        result["leveled_spells_to_add"] = max(0, known[new_level - 1] - known[old_level - 1])
        # The Bard table includes the two unrestricted Magical Secrets in
        # Spells Known at levels 10, 14, and 18. The corresponding feature
        # artifact settles those choices, so they must not also be requested
        # as ordinary Bard-list spells.
        if key == "bard" and new_level in {10, 14, 18}:
            result["leveled_spells_to_add"] = max(0, result["leveled_spells_to_add"] - 2)
    if key == "wizard":
        result["leveled_spells_to_add"] = 2
    return result


def _ability_modifier(sheet: dict[str, Any], ability: str) -> int:
    score = int(sheet.get("abilities", {}).get(ability, {}).get("score", 10) or 10)
    return ability_modifier(score)


def _class_hit_die_resource(
    hit_dice: dict[str, Any], class_name: str, hit_die: int
) -> tuple[str, dict[str, Any]]:
    match = next(
        (
            (key, dict(resource))
            for key, resource in hit_dice.items()
            if isinstance(resource, dict)
            and str(resource.get("source_key") or "").casefold() == class_name.casefold()
        ),
        None,
    )
    if match is not None:
        return match
    key = f"d{hit_die}"
    if key in hit_dice:
        return key, dict(hit_dice[key])
    return key, {
        "label": key,
        "value": 0,
        "max": 0,
        "recovers_on": "long_rest",
        "source_key": class_name,
        "slot_level": 0,
    }
