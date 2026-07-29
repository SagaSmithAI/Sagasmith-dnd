"""Validated v2 D&D character, inventory, and narrative document contracts."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from sagasmith_dnd.abilities import ABILITY_NAMES, SKILL_ABILITIES
from sagasmith_dnd.ability_generation import normalize_ability_generation
from sagasmith_dnd.activity_identity import is_multiattack_activity
from sagasmith_dnd.actor_types import NON_PLAYER_CHARACTER_TYPES
from sagasmith_dnd.conditions import (
    apply_effect_conditions,
    condition_ids,
    reconcile_ended_effect_conditions,
)
from sagasmith_dnd.editions import DEFAULT_CHARACTER_EDITION, normalize_dnd_edition
from sagasmith_dnd.engine import ability_modifier, proficiency_bonus
from sagasmith_dnd.game_time import TICKS_PER_DAY, TICKS_PER_HOUR, TICKS_PER_MINUTE
from sagasmith_dnd.hit_points import effective_hit_point_maximum_value
from sagasmith_dnd.rule_engine import (
    DERIVED_STAT_MODIFIER_TARGETS,
    EXTERNAL_RULING_KINDS,
    ResolutionContext,
    RuleEventRulingRequiredError,
    apply_rule_event,
    core_receipts,
)
from sagasmith_dnd.spell_resolution import normalize_spell_resolution
from sagasmith_dnd.vocabulary import (
    ATTACK_MODES,
    CAMPAIGN_GAME_PHASES,
    DAMAGE_TYPES,
    DENOMINATION_CP_VALUES,
    DENOMINATIONS,
    GAMEPLAY_VISIBILITY_SCOPES,
    PLAYER_GAMEPLAY_VISIBILITY_SCOPES,
    PREPARATION_MODES,
    PREPARED_SELECTION_MODES,
    REST_TYPES,
)

ITEM_KINDS = {
    "weapon",
    "armor",
    "shield",
    "equipment",
    "consumable",
    "tool",
    "container",
    "ammunition",
    "loot",
    "magic_item",
    "focus",
    "spellbook",
}
EQUIPMENT_SLOTS = (
    "armor",
    "shield",
    "main_hand",
    "off_hand",
    "head",
    "neck",
    "cloak",
    "gloves",
    "boots",
    "ring_1",
    "ring_2",
    "shoulders",
    "back",
    "chest",
    "wrists",
    "waist",
    "legs",
)
SLOT_ITEM_KINDS = {
    "armor": {"armor"},
    "shield": {"shield"},
    "main_hand": {"weapon", "equipment", "tool", "focus", "consumable", "magic_item", "loot"},
    "off_hand": {"weapon", "equipment", "tool", "focus", "consumable", "magic_item", "loot"},
    "head": {"equipment", "magic_item"},
    "neck": {"equipment", "magic_item"},
    "cloak": {"equipment", "magic_item"},
    "gloves": {"equipment", "magic_item"},
    "boots": {"equipment", "magic_item"},
    "ring_1": {"magic_item"},
    "ring_2": {"magic_item"},
    "shoulders": {"equipment", "magic_item"},
    "back": {"equipment", "magic_item"},
    "chest": {"equipment", "magic_item"},
    "wrists": {"equipment", "magic_item"},
    "waist": {"equipment", "magic_item"},
    "legs": {"equipment", "magic_item"},
}
SENSE_NAMES = ("darkvision", "blindsight", "tremorsense", "truesight")
ATTACK_ABILITIES = {"strength", "dexterity", "spell", "none"}
RECOVERY_PERIODS = REST_TYPES | {"none", "turn", "dawn", "manual"}
EFFECT_PERIODS = REST_TYPES | {
    "manual",
    "source_turn_start",
    "turn_start",
    "turn_end",
    "round",
    "encounter",
    "minute",
    "hour",
    "day",
}
# Compatibility names retained for callers that imported these from the card schema.
EFFECT_VISIBILITY_SCOPES = GAMEPLAY_VISIBILITY_SCOPES
PLAYER_EFFECT_VISIBILITY_SCOPES = PLAYER_GAMEPLAY_VISIBILITY_SCOPES


def _uuid() -> str:
    return str(uuid.uuid4())


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return list(value)


def _text(value: Any, field: str, *, default: str = "", maximum: int = 4000) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return value


def _integer(
    value: Any,
    field: str,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def _number(
    value: Any,
    field: str,
    *,
    default: float = 0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> int | float:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def _boolean(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    values = _array(value or [], field)
    return [_text(item, f"{field}[]", maximum=300) for item in values]


def _damage_parts(value: Any, field: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(value or [], field)):
        part_field = f"{field}[{index}]"
        part = _object(raw, part_field)
        _reject_unknown(part, part_field, {"damage_formula", "damage_bonus", "damage_type"})
        formula = _text(part.get("damage_formula"), f"{part_field}.damage_formula", maximum=100)
        damage_type = _text(part.get("damage_type"), f"{part_field}.damage_type", maximum=100)
        if not formula or not damage_type:
            raise ValueError(f"{part_field} requires damage_formula and damage_type")
        parts.append(
            {
                "damage_formula": formula,
                "damage_bonus": _integer(part.get("damage_bonus"), f"{part_field}.damage_bonus"),
                "damage_type": damage_type,
            }
        )
    return parts


def _reject_unknown(value: dict[str, Any], field: str, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} has unsupported fields: {', '.join(unknown)}")


def _default_ability() -> dict[str, Any]:
    return {"score": 10, "save_proficient": False, "bonus": 0}


def _default_skill() -> dict[str, Any]:
    return {"proficiency": "none", "bonus": 0}


def _default_inventory() -> dict[str, Any]:
    return {
        "wallet": {denomination: 0 for denomination in DENOMINATIONS},
        "items": [],
        "equipment_slots": {slot: None for slot in EQUIPMENT_SLOTS},
        "encumbrance": {"mode": "standard", "ignore_currency_weight": True},
    }


def default_character_sheet() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "edition": DEFAULT_CHARACTER_EDITION,
        "identity": {
            "gender": "",
            "age": "",
            "height_cm": None,
            "weight_lb": None,
            "faith": "",
            "deity": "",
            "hair": "",
            "skin": "",
            "eyes": "",
            "portrait_uri": "",
        },
        "progression": {
            "level": 1,
            "xp": 0,
            "classes": [],
            "background": "",
            "background_grants": {
                "feature": "",
                "equipment_item_ids": [],
                "languages": [],
                "tools": [],
                "choices": {},
            },
            "species": "",
        },
        "ability_generation": {
            "ruleset": "",
            "method": "unrecorded",
            "assignments": {},
            "point_buy": None,
            "rolls": [],
        },
        "abilities": {ability: _default_ability() for ability in ABILITY_NAMES},
        "skills": {skill: _default_skill() for skill in SKILL_ABILITIES},
        "combat": {
            "hp": {"value": 1, "max": 1, "temp": 0},
            "ac": {"base": 10, "override": None},
            "initiative": {"ability": "dexterity", "bonus": 0},
            "attacks_per_action": 1,
            "speed": {"walk": 30, "fly": 0, "swim": 0, "climb": 0, "burrow": 0},
            "hit_dice": {},
            "hp_progression": [],
            "death_saves": {"successes": 0, "failures": 0},
            "exhaustion": 0,
            "inspiration": False,
            "wounded": False,
            "rest_history": {
                "last_rest_type": "",
                "last_rest_started_elapsed_ticks": None,
                "last_rest_completed_elapsed_ticks": None,
                "last_long_rest_elapsed_ticks": None,
            },
        },
        "traits": {
            "size": "medium",
            "alignment": "",
            "languages": [],
            "proficiencies": {
                "armor": [],
                "weapons": [],
                "tools": [],
                "tool_expertise": [],
            },
            "resistances": [],
            "immunities": [],
            "vulnerabilities": [],
            "condition_immunities": [],
            "senses": {
                "darkvision": 0,
                "blindsight": 0,
                "tremorsense": 0,
                "truesight": 0,
                "passive_perception_bonus": 0,
            },
        },
        "resources": {},
        "spellcasting": {
            "ability": None,
            "class_lists": [],
            "spell_slots": {},
            "pact_magic": None,
            "preparation": {
                "mode": "known",
                "max_prepared": 0,
                "changes_on": "long_rest",
                "selected_spell_ids": [],
            },
            "ritual_casting": False,
            "spellbook": {"enabled": False, "spell_ids": []},
            "casting_economy": "slots",
            "spell_points": None,
            "attack_bonus_override": None,
            "save_dc_override": None,
        },
        "content": {
            "spells": [],
            "features": [],
            "feats": [],
            "activities": [],
            "selections": [],
        },
        "conditions": [],
        "effects": [],
        "adventure_state": {
            "reputation": {},
            "contributions": {},
            "blessings": [],
            "wards": [],
            "legendary_boons": [],
            "status_tags": [],
        },
        "inventory": _default_inventory(),
    }


def default_character_notes() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "profile": {
            "summary": "",
            "appearance": "",
            "personality_traits": [],
            "ideals": [],
            "bonds": [],
            "flaws": [],
            "motivation": "",
            "backstory": "",
            "dm_notes": "",
        },
        "memories": [],
        "relationships": [],
        "goals": [],
    }


def _merge_defaults(default: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(default)
    for key, value in supplied.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_ability(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(item, field, {"score", "save_proficient", "bonus"})
    return {
        "score": _integer(item.get("score"), f"{field}.score", default=10, minimum=1, maximum=30),
        "save_proficient": _boolean(item.get("save_proficient"), f"{field}.save_proficient"),
        "bonus": _integer(item.get("bonus"), f"{field}.bonus"),
    }


def _normalize_skill(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(item, field, {"proficiency", "bonus"})
    proficiency = _text(item.get("proficiency"), f"{field}.proficiency", default="none")
    if proficiency not in {"none", "half", "proficient", "expertise"}:
        raise ValueError(f"{field}.proficiency is invalid")
    return {"proficiency": proficiency, "bonus": _integer(item.get("bonus"), f"{field}.bonus")}


def _normalize_resource(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(
        item,
        field,
        {
            "label",
            "value",
            "max",
            "unlimited",
            "recovers_on",
            "recovery_requirements",
            "source_key",
            "slot_level",
        },
    )
    maximum = _integer(item.get("max"), f"{field}.max", minimum=0)
    current = _integer(item.get("value"), f"{field}.value", default=maximum, minimum=0)
    if current > maximum:
        raise ValueError(f"{field}.value cannot exceed max")
    unlimited = _boolean(
        item.get("unlimited"),
        f"{field}.unlimited",
        default=maximum == 0,
    )
    if unlimited and (maximum != 0 or current != 0):
        raise ValueError(f"{field}.unlimited resources must have zero value and max")
    recovery = _text(item.get("recovers_on"), f"{field}.recovers_on", default="none")
    if recovery not in RECOVERY_PERIODS:
        raise ValueError(f"{field}.recovers_on is invalid")
    raw_requirements = _object(
        item.get("recovery_requirements") or {},
        f"{field}.recovery_requirements",
    )
    _reject_unknown(
        raw_requirements,
        f"{field}.recovery_requirements",
        {"activity_minutes"},
    )
    raw_activity_minutes = _object(
        raw_requirements.get("activity_minutes") or {},
        f"{field}.recovery_requirements.activity_minutes",
    )
    activity_minutes = {
        _text(
            raw_activity,
            f"{field}.recovery_requirements.activity_minutes activity",
            maximum=100,
        ): _integer(
            raw_minutes,
            f"{field}.recovery_requirements.activity_minutes.{raw_activity}",
            minimum=1,
        )
        for raw_activity, raw_minutes in raw_activity_minutes.items()
    }
    if "" in activity_minutes:
        raise ValueError(f"{field}.recovery_requirements.activity_minutes activity is required")
    normalized = {
        "label": _text(item.get("label"), f"{field}.label", default="", maximum=200),
        "value": current,
        "max": maximum,
        "recovers_on": recovery,
        "source_key": _text(item.get("source_key"), f"{field}.source_key", default="", maximum=300),
        "slot_level": _integer(item.get("slot_level"), f"{field}.slot_level", minimum=0, maximum=9),
    }
    if "unlimited" in item:
        normalized["unlimited"] = unlimited
    if activity_minutes:
        normalized["recovery_requirements"] = {"activity_minutes": activity_minutes}
    return normalized


def _normalize_resource_scaling(value: Any, field: str) -> dict[str, Any]:
    item = _object(value or {}, field)
    if not item:
        return {}
    _reject_unknown(
        item,
        field,
        {
            "target",
            "label",
            "class_name",
            "maximum_by_level",
            "maximum_formula",
            "recovers_on",
            "recovery_by_level",
            "unlimited_at_level",
        },
    )
    target = _text(item.get("target"), f"{field}.target", maximum=200)
    if not target:
        raise ValueError(f"{field}.target is required")
    class_name = _text(item.get("class_name"), f"{field}.class_name", maximum=200)
    if not class_name:
        raise ValueError(f"{field}.class_name is required")
    maximum_by_level = _object(item.get("maximum_by_level") or {}, f"{field}.maximum_by_level")
    normalized_maximums: dict[str, int] = {}
    for raw_level, raw_maximum in maximum_by_level.items():
        level_text = str(raw_level).strip()
        if not level_text.isdigit():
            raise ValueError(f"{field}.maximum_by_level level must be an integer")
        level = _integer(
            int(level_text),
            f"{field}.maximum_by_level level",
            minimum=1,
            maximum=20,
        )
        normalized_maximums[str(level)] = _integer(
            raw_maximum,
            f"{field}.maximum_by_level.{level}",
            minimum=0,
        )
    formula = _object(item.get("maximum_formula") or {}, f"{field}.maximum_formula")
    normalized_formula: dict[str, Any] = {}
    if formula:
        _reject_unknown(
            formula,
            f"{field}.maximum_formula",
            {"kind", "ability", "minimum", "multiplier", "offset"},
        )
        kind = _text(formula.get("kind"), f"{field}.maximum_formula.kind")
        if kind not in {"class_level", "ability_modifier"}:
            raise ValueError(f"{field}.maximum_formula.kind is invalid")
        ability = _text(
            formula.get("ability"),
            f"{field}.maximum_formula.ability",
            default="",
        )
        if kind == "ability_modifier" and ability not in ABILITY_NAMES:
            raise ValueError(f"{field}.maximum_formula.ability is invalid")
        if kind == "class_level" and ability:
            raise ValueError(f"{field}.maximum_formula.ability is not allowed")
        normalized_formula = {
            "kind": kind,
            "ability": ability,
            "minimum": _integer(
                formula.get("minimum"),
                f"{field}.maximum_formula.minimum",
                minimum=0,
            ),
            "multiplier": _integer(
                formula.get("multiplier"),
                f"{field}.maximum_formula.multiplier",
                default=1,
                minimum=1,
            ),
            "offset": _integer(
                formula.get("offset"),
                f"{field}.maximum_formula.offset",
                default=0,
            ),
        }
    recovery = _text(
        item.get("recovers_on"),
        f"{field}.recovers_on",
        default="none",
    )
    if recovery not in RECOVERY_PERIODS:
        raise ValueError(f"{field}.recovers_on is invalid")
    recovery_by_level = _object(
        item.get("recovery_by_level") or {},
        f"{field}.recovery_by_level",
    )
    normalized_recoveries: dict[str, str] = {}
    for raw_level, raw_recovery in recovery_by_level.items():
        level_text = str(raw_level).strip()
        if not level_text.isdigit():
            raise ValueError(f"{field}.recovery_by_level level must be an integer")
        level = _integer(
            int(level_text),
            f"{field}.recovery_by_level level",
            minimum=1,
            maximum=20,
        )
        level_recovery = _text(raw_recovery, f"{field}.recovery_by_level.{level}")
        if level_recovery not in RECOVERY_PERIODS:
            raise ValueError(f"{field}.recovery_by_level.{level} is invalid")
        normalized_recoveries[str(level)] = level_recovery
    unlimited_at_level = _integer(
        item.get("unlimited_at_level"),
        f"{field}.unlimited_at_level",
        minimum=0,
        maximum=20,
    )
    if not normalized_maximums and not normalized_formula and not unlimited_at_level:
        raise ValueError(f"{field} needs a maximum or unlimited level")
    return {
        "target": target,
        "label": _text(item.get("label"), f"{field}.label", maximum=200),
        "class_name": class_name,
        "maximum_by_level": normalized_maximums,
        "maximum_formula": normalized_formula,
        "recovers_on": recovery,
        "recovery_by_level": normalized_recoveries,
        "unlimited_at_level": unlimited_at_level,
    }


def _normalize_item_mechanics(kind: str, value: Any, field: str) -> dict[str, Any]:
    mechanics = _object(value or {}, field)
    if kind == "weapon":
        _reject_unknown(
            mechanics,
            field,
            {
                "category",
                "attack_type",
                "attack_ability",
                "damage_formula",
                "damage_type",
                "additional_damage",
                "on_hit_effect",
                "versatile_damage_formula",
                "properties",
                "normal_range_ft",
                "long_range_ft",
                "thrown_normal_range_ft",
                "thrown_long_range_ft",
                "ammunition_item_id",
                "proficient",
                "magic_bonus",
                "reach_ft",
                "attack_bonus_override",
                "damage_bonus_override",
                "always_available",
                "required_target_sizes",
                "requires_attack_advantage",
            },
        )
        category = _text(mechanics.get("category"), f"{field}.category", default="other")
        if category not in {"simple", "martial", "natural", "improvised", "other"}:
            raise ValueError(f"{field}.category is invalid")
        attack_type = _text(mechanics.get("attack_type"), f"{field}.attack_type", default="melee")
        if attack_type not in ATTACK_MODES:
            raise ValueError(f"{field}.attack_type is invalid")
        attack_ability = _text(
            mechanics.get("attack_ability"), f"{field}.attack_ability", default="strength"
        )
        if attack_ability not in ATTACK_ABILITIES:
            raise ValueError(f"{field}.attack_ability is invalid")
        properties = _string_list(mechanics.get("properties"), f"{field}.properties")
        required_target_sizes = [
            item.casefold()
            for item in _string_list(
                mechanics.get("required_target_sizes"),
                f"{field}.required_target_sizes",
            )
        ]
        valid_sizes = {"tiny", "small", "medium", "large", "huge", "gargantuan"}
        if (
            len(required_target_sizes) != len(set(required_target_sizes))
            or any(item not in valid_sizes for item in required_target_sizes)
        ):
            raise ValueError(f"{field}.required_target_sizes is invalid")
        return {
            "category": category,
            "attack_type": attack_type,
            "attack_ability": attack_ability,
            "damage_formula": _text(
                mechanics.get("damage_formula"), f"{field}.damage_formula", maximum=100
            ),
            "damage_type": _text(mechanics.get("damage_type"), f"{field}.damage_type", maximum=100),
            "additional_damage": _damage_parts(
                mechanics.get("additional_damage"), f"{field}.additional_damage"
            ),
            "on_hit_effect": _text(
                mechanics.get("on_hit_effect"), f"{field}.on_hit_effect", maximum=4000
            ),
            "versatile_damage_formula": _text(
                mechanics.get("versatile_damage_formula"),
                f"{field}.versatile_damage_formula",
                maximum=100,
            ),
            "properties": properties,
            "normal_range_ft": _integer(
                mechanics.get("normal_range_ft"), f"{field}.normal_range_ft", minimum=0
            ),
            "long_range_ft": _integer(
                mechanics.get("long_range_ft"), f"{field}.long_range_ft", minimum=0
            ),
            "thrown_normal_range_ft": _integer(
                mechanics.get("thrown_normal_range_ft"),
                f"{field}.thrown_normal_range_ft",
                minimum=0,
            ),
            "thrown_long_range_ft": _integer(
                mechanics.get("thrown_long_range_ft"),
                f"{field}.thrown_long_range_ft",
                minimum=0,
            ),
            "ammunition_item_id": (
                _text(mechanics["ammunition_item_id"], f"{field}.ammunition_item_id", maximum=100)
                if mechanics.get("ammunition_item_id") is not None
                else None
            ),
            "proficient": _boolean(
                mechanics.get("proficient"), f"{field}.proficient", default=True
            ),
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
            "reach_ft": _integer(
                mechanics.get("reach_ft"),
                f"{field}.reach_ft",
                default=10 if "reach" in {item.casefold() for item in properties} else 5,
                minimum=0,
            ),
            "attack_bonus_override": (
                _integer(mechanics["attack_bonus_override"], f"{field}.attack_bonus_override")
                if mechanics.get("attack_bonus_override") is not None
                else None
            ),
            "damage_bonus_override": (
                _integer(mechanics["damage_bonus_override"], f"{field}.damage_bonus_override")
                if mechanics.get("damage_bonus_override") is not None
                else None
            ),
            "always_available": _boolean(
                mechanics.get("always_available"), f"{field}.always_available"
            ),
            "required_target_sizes": required_target_sizes,
            "requires_attack_advantage": _boolean(
                mechanics.get("requires_attack_advantage"),
                f"{field}.requires_attack_advantage",
            ),
        }
    if kind == "container":
        _reject_unknown(
            mechanics, field, {"capacity_oz", "weightless_contents", "extra_dimensional"}
        )
        return {
            "capacity_oz": _integer(
                mechanics.get("capacity_oz"), f"{field}.capacity_oz", minimum=0
            ),
            "weightless_contents": _boolean(
                mechanics.get("weightless_contents"), f"{field}.weightless_contents"
            ),
            "extra_dimensional": _boolean(
                mechanics.get("extra_dimensional"), f"{field}.extra_dimensional"
            ),
        }
    if kind == "spellbook":
        _reject_unknown(
            mechanics,
            field,
            {
                "edition",
                "spell_ids",
                "unresolved_spell_names",
                "owner_mark",
                "source_scene_id",
                "deciphered",
                "copyable",
            },
        )
        edition = normalize_dnd_edition(
            _text(
                mechanics.get("edition"),
                f"{field}.edition",
                default=DEFAULT_CHARACTER_EDITION,
            )
        )
        spell_ids = _string_list(mechanics.get("spell_ids"), f"{field}.spell_ids")
        if len(spell_ids) != len(set(spell_ids)):
            raise ValueError(f"{field}.spell_ids contains duplicate ids")
        unresolved_spell_names = _string_list(
            mechanics.get("unresolved_spell_names"),
            f"{field}.unresolved_spell_names",
        )
        if len(unresolved_spell_names) != len(set(unresolved_spell_names)):
            raise ValueError(f"{field}.unresolved_spell_names contains duplicate names")
        return {
            "edition": edition,
            "spell_ids": spell_ids,
            "unresolved_spell_names": unresolved_spell_names,
            "owner_mark": _text(mechanics.get("owner_mark"), f"{field}.owner_mark", maximum=300),
            "source_scene_id": _text(
                mechanics.get("source_scene_id"), f"{field}.source_scene_id", maximum=100
            ),
            "deciphered": _boolean(
                mechanics.get("deciphered"), f"{field}.deciphered", default=False
            ),
            "copyable": _boolean(mechanics.get("copyable"), f"{field}.copyable", default=True),
        }
    if kind == "armor":
        _reject_unknown(
            mechanics,
            field,
            {
                "base_ac",
                "dexterity_mode",
                "dexterity_max",
                "magic_bonus",
                "stealth_disadvantage",
            },
        )
        if "base_ac" not in mechanics:
            raise ValueError(f"{field}.base_ac is required for armor")
        dexterity_mode = _text(
            mechanics.get("dexterity_mode"), f"{field}.dexterity_mode", default="none"
        )
        if dexterity_mode not in {"none", "full", "max"}:
            raise ValueError(f"{field}.dexterity_mode is invalid")
        dexterity_max = mechanics.get("dexterity_max")
        if dexterity_mode == "max":
            if dexterity_max is None:
                raise ValueError(f"{field}.dexterity_max is required when dexterity_mode is max")
            dexterity_max = _integer(dexterity_max, f"{field}.dexterity_max", minimum=0, maximum=10)
        elif dexterity_max is not None:
            raise ValueError(f"{field}.dexterity_max is only valid when dexterity_mode is max")
        return {
            "base_ac": _integer(mechanics["base_ac"], f"{field}.base_ac", minimum=1),
            "dexterity_mode": dexterity_mode,
            "dexterity_max": dexterity_max,
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
            "stealth_disadvantage": _boolean(
                mechanics.get("stealth_disadvantage"),
                f"{field}.stealth_disadvantage",
                default=False,
            ),
        }
    if kind == "shield":
        _reject_unknown(mechanics, field, {"ac_bonus", "magic_bonus"})
        if "ac_bonus" not in mechanics:
            raise ValueError(f"{field}.ac_bonus is required for shield")
        return {
            "ac_bonus": _integer(mechanics["ac_bonus"], f"{field}.ac_bonus", minimum=0),
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
        }
    if kind == "magic_item":
        if "grants" in mechanics:
            grants = _object(mechanics["grants"], f"{field}.grants")
            _reject_unknown(
                grants,
                f"{field}.grants",
                {"resistances", "immunities", "vulnerabilities"},
            )
            normalized_grants: dict[str, list[str]] = {}
            for defense in ("resistances", "immunities", "vulnerabilities"):
                values = [
                    value.strip().casefold()
                    for value in _string_list(
                        grants.get(defense),
                        f"{field}.grants.{defense}",
                    )
                ]
                if len(values) != len(set(values)):
                    raise ValueError(f"{field}.grants.{defense} contains duplicates")
                invalid = sorted(set(values) - DAMAGE_TYPES)
                if invalid:
                    raise ValueError(
                        f"{field}.grants.{defense} contains invalid damage types: "
                        + ", ".join(invalid)
                    )
                normalized_grants[defense] = values
            mechanics["grants"] = normalized_grants
        if "ac_bonus" in mechanics:
            mechanics["ac_bonus"] = _integer(mechanics["ac_bonus"], f"{field}.ac_bonus")
        if "charge_rules" in mechanics:
            charge_rules = _object(mechanics["charge_rules"], f"{field}.charge_rules")
            _reject_unknown(
                charge_rules,
                f"{field}.charge_rules",
                {
                    "recovery_trigger",
                    "recovery_formula",
                    "last_charge_check_formula",
                    "destroy_on",
                },
            )
            recovery_trigger = _text(
                charge_rules.get("recovery_trigger"),
                f"{field}.charge_rules.recovery_trigger",
                maximum=40,
            )
            if recovery_trigger and recovery_trigger not in RECOVERY_PERIODS:
                raise ValueError(f"{field}.charge_rules.recovery_trigger is invalid")
            destroy_on_raw = charge_rules.get("destroy_on") or []
            if not isinstance(destroy_on_raw, list):
                raise ValueError(f"{field}.charge_rules.destroy_on must be an array")
            destroy_on = [
                _integer(
                    result,
                    f"{field}.charge_rules.destroy_on[{index}]",
                    minimum=1,
                    maximum=1000,
                )
                for index, result in enumerate(destroy_on_raw)
            ]
            if len(destroy_on) != len(set(destroy_on)):
                raise ValueError(f"{field}.charge_rules.destroy_on contains duplicate results")
            recovery_formula = _text(
                charge_rules.get("recovery_formula"),
                f"{field}.charge_rules.recovery_formula",
                maximum=100,
            )
            last_charge_check_formula = _text(
                charge_rules.get("last_charge_check_formula"),
                f"{field}.charge_rules.last_charge_check_formula",
                maximum=100,
            )
            if bool(recovery_trigger) != bool(recovery_formula):
                raise ValueError(
                    f"{field}.charge_rules recovery trigger and formula must be supplied together"
                )
            if bool(last_charge_check_formula) != bool(destroy_on):
                raise ValueError(
                    f"{field}.charge_rules last-charge formula and destroy results "
                    "must be supplied together"
                )
            mechanics["charge_rules"] = {
                "recovery_trigger": recovery_trigger,
                "recovery_formula": recovery_formula,
                "last_charge_check_formula": last_charge_check_formula,
                "destroy_on": destroy_on,
            }
    if kind == "ammunition":
        _reject_unknown(mechanics, field, {"magic", "rarity", "slaying"})
        normalized_ammunition: dict[str, Any] = {}
        if "magic" in mechanics:
            normalized_ammunition["magic"] = _boolean(
                mechanics["magic"], f"{field}.magic"
            )
        if "rarity" in mechanics:
            normalized_ammunition["rarity"] = _text(
                mechanics["rarity"], f"{field}.rarity", maximum=40
            )
        if "slaying" in mechanics:
            slaying = _object(mechanics["slaying"], f"{field}.slaying")
            _reject_unknown(
                slaying,
                f"{field}.slaying",
                {
                    "target_groups",
                    "save_ability",
                    "save_dc",
                    "damage_formula",
                    "damage_type",
                    "half_on_success",
                    "source_excerpt",
                    "rule_refs",
                },
            )
            target_groups = [
                value.strip().casefold()
                for value in _string_list(
                    slaying.get("target_groups"),
                    f"{field}.slaying.target_groups",
                )
            ]
            save_ability = _text(
                slaying.get("save_ability"),
                f"{field}.slaying.save_ability",
            ).casefold()
            damage_formula = _text(
                slaying.get("damage_formula"),
                f"{field}.slaying.damage_formula",
                maximum=40,
            ).replace(" ", "")
            damage_type = _text(
                slaying.get("damage_type"),
                f"{field}.slaying.damage_type",
            ).casefold()
            source_excerpt = _text(
                slaying.get("source_excerpt"),
                f"{field}.slaying.source_excerpt",
                maximum=1200,
            )
            rule_refs = _string_list(
                slaying.get("rule_refs"),
                f"{field}.slaying.rule_refs",
            )
            if (
                not target_groups
                or len(target_groups) != len(set(target_groups))
                or save_ability not in ABILITY_NAMES
                or not re.fullmatch(r"\d+d\d+(?:[+-]\d+)?", damage_formula)
                or damage_type not in DAMAGE_TYPES
                or not source_excerpt
                or not rule_refs
            ):
                raise ValueError(
                    f"{field}.slaying requires unique targets, a valid save, "
                    "damage, exact source excerpt, and rule refs"
                )
            normalized_ammunition["slaying"] = {
                "target_groups": target_groups,
                "save_ability": save_ability,
                "save_dc": _integer(
                    slaying.get("save_dc"),
                    f"{field}.slaying.save_dc",
                    minimum=1,
                    maximum=40,
                ),
                "damage_formula": damage_formula,
                "damage_type": damage_type,
                "half_on_success": _boolean(
                    slaying.get("half_on_success"),
                    f"{field}.slaying.half_on_success",
                ),
                "source_excerpt": source_excerpt,
                "rule_refs": rule_refs,
            }
        return normalized_ammunition
    return mechanics


def _validate_item_slot(item: dict[str, Any], slot: str) -> None:
    if item["kind"] not in SLOT_ITEM_KINDS[slot]:
        raise ValueError(f"{item['kind']} cannot be equipped in {slot}")


def _normalize_item(value: Any, field: str, *, generate_id: bool = True) -> dict[str, Any]:
    item = _object(value, field)
    allowed = {
        "id",
        "name",
        "kind",
        "quantity",
        "weight_oz",
        "price_cp",
        "description",
        "source_key",
        "container_id",
        "equipped",
        "equipped_slot",
        "identified",
        "attunement",
        "condition",
        "uses",
        "charges",
        "mechanics",
    }
    _reject_unknown(item, field, allowed)
    item_id = _text(
        item.get("id"), f"{field}.id", default=_uuid() if generate_id else "", maximum=100
    )
    if not item_id:
        raise ValueError(f"{field}.id is required")
    kind = _text(item.get("kind"), f"{field}.kind", default="equipment")
    if kind not in ITEM_KINDS:
        raise ValueError(f"{field}.kind is invalid")
    if (
        kind in {"armor", "shield"}
        and _integer(item.get("quantity"), f"{field}.quantity", default=1, minimum=1) != 1
    ):
        raise ValueError(f"{field}.quantity must be 1 for {kind}")
    attunement = _text(item.get("attunement"), f"{field}.attunement", default="none")
    if attunement not in {"none", "required", "attuned"}:
        raise ValueError(f"{field}.attunement is invalid")
    equipped_slot = item.get("equipped_slot")
    if equipped_slot is not None:
        equipped_slot = _text(equipped_slot, f"{field}.equipped_slot")
        if equipped_slot not in EQUIPMENT_SLOTS:
            raise ValueError(f"{field}.equipped_slot is invalid")
    uses = _normalize_resource(item.get("uses") or {}, f"{field}.uses")
    charges = _normalize_resource(item.get("charges") or {}, f"{field}.charges")
    return {
        "id": item_id,
        "name": _text(item.get("name"), f"{field}.name", maximum=300),
        "kind": kind,
        "quantity": _integer(
            item.get("quantity"),
            f"{field}.quantity",
            default=1,
            minimum=0 if kind == "ammunition" else 1,
        ),
        "weight_oz": _number(item.get("weight_oz"), f"{field}.weight_oz", minimum=0),
        "price_cp": _integer(item.get("price_cp"), f"{field}.price_cp", minimum=0),
        "description": _text(item.get("description"), f"{field}.description", maximum=1200),
        "source_key": _text(item.get("source_key"), f"{field}.source_key", maximum=300),
        "container_id": (
            _text(item.get("container_id"), f"{field}.container_id", maximum=100)
            if item.get("container_id") is not None
            else None
        ),
        "equipped": _boolean(item.get("equipped"), f"{field}.equipped"),
        "equipped_slot": equipped_slot,
        "identified": _boolean(item.get("identified"), f"{field}.identified", default=True),
        "attunement": attunement,
        "condition": _text(
            item.get("condition"), f"{field}.condition", default="normal", maximum=100
        ),
        "uses": uses,
        "charges": charges,
        "mechanics": _normalize_item_mechanics(kind, item.get("mechanics"), f"{field}.mechanics"),
    }


def validate_inventory(value: Any) -> dict[str, Any]:
    inventory = _merge_defaults(_default_inventory(), _object(value or {}, "inventory"))
    _reject_unknown(inventory, "inventory", {"wallet", "items", "equipment_slots", "encumbrance"})
    wallet = _object(inventory["wallet"], "inventory.wallet")
    _reject_unknown(wallet, "inventory.wallet", set(DENOMINATIONS))
    normalized_wallet = {
        denomination: _integer(
            wallet.get(denomination), f"inventory.wallet.{denomination}", minimum=0
        )
        for denomination in DENOMINATIONS
    }
    items = [
        _normalize_item(item, f"inventory.items[{index}]")
        for index, item in enumerate(_array(inventory["items"], "inventory.items"))
    ]
    item_ids = {item["id"] for item in items}
    if len(item_ids) != len(items):
        raise ValueError("inventory.items contains duplicate ids")
    attuned_items = [item for item in items if item["attunement"] == "attuned"]
    if len(attuned_items) > 3:
        raise ValueError("a character cannot be attuned to more than three magic items")
    # source_key records provenance and can legitimately differ between two
    # copies acquired from different adventure chunks.  The 2014 attunement
    # restriction is about copies of the same item, so the item name is the
    # stable identity unless a legacy nameless record needs the source fallback.
    attuned_identities = [
        str(item.get("name") or item.get("source_key") or "").strip().casefold()
        for item in attuned_items
    ]
    if len(attuned_identities) != len(set(attuned_identities)):
        raise ValueError("a character cannot attune to more than one copy of an item")
    by_id = {item["id"]: item for item in items}
    for item in items:
        container_id = item["container_id"]
        if container_id is not None:
            container = by_id.get(container_id)
            if container is None or container["kind"] != "container":
                raise ValueError("inventory item references an invalid container")
            seen = {item["id"]}
            current = container
            while current["container_id"] is not None:
                parent_id = current["container_id"]
                if parent_id in seen:
                    raise ValueError("inventory containers must not form a cycle")
                seen.add(parent_id)
                current = by_id[parent_id]
        if item["kind"] == "weapon":
            ammunition_item_id = item["mechanics"]["ammunition_item_id"]
            if ammunition_item_id is not None:
                ammunition = by_id.get(ammunition_item_id)
                if ammunition is None or ammunition["kind"] != "ammunition":
                    raise ValueError(
                        "weapon ammunition_item_id must reference ammunition in inventory"
                    )
    for container in (item for item in items if item["kind"] == "container"):
        capacity = container["mechanics"]["capacity_oz"]
        contained_weight = sum(
            item["weight_oz"] * item["quantity"]
            for item in items
            if item["container_id"] == container["id"]
        )
        if capacity and contained_weight > capacity:
            raise ValueError("container contents exceed capacity_oz")
    slots = _object(inventory["equipment_slots"], "inventory.equipment_slots")
    _reject_unknown(slots, "inventory.equipment_slots", set(EQUIPMENT_SLOTS))
    normalized_slots: dict[str, str | None] = {}
    for slot in EQUIPMENT_SLOTS:
        item_id = slots.get(slot)
        if item_id is not None:
            item_id = _text(item_id, f"inventory.equipment_slots.{slot}", maximum=100)
            if item_id not in by_id:
                raise ValueError(f"inventory.equipment_slots.{slot} references an unknown item")
            item = by_id[item_id]
            _validate_item_slot(item, slot)
            if not item["equipped"] or item["equipped_slot"] != slot:
                raise ValueError("inventory equipment slot and item equipped state must agree")
        normalized_slots[slot] = item_id
    for item in items:
        equipped_slot = item["equipped_slot"]
        if item["equipped"]:
            if equipped_slot is None:
                raise ValueError("equipped item must declare an equipped_slot")
            if normalized_slots[equipped_slot] != item["id"]:
                raise ValueError("equipped item must be referenced by its equipment slot")
            _validate_item_slot(item, equipped_slot)
        elif equipped_slot is not None:
            raise ValueError("unequipped item cannot declare an equipped_slot")
    encumbrance = _object(inventory["encumbrance"], "inventory.encumbrance")
    _reject_unknown(encumbrance, "inventory.encumbrance", {"mode", "ignore_currency_weight"})
    mode = _text(encumbrance.get("mode"), "inventory.encumbrance.mode", default="standard")
    if mode not in {"standard", "variant"}:
        raise ValueError("inventory.encumbrance.mode is invalid")
    return {
        "wallet": normalized_wallet,
        "items": items,
        "equipment_slots": normalized_slots,
        "encumbrance": {
            "mode": mode,
            "ignore_currency_weight": _boolean(
                encumbrance.get("ignore_currency_weight"),
                "inventory.encumbrance.ignore_currency_weight",
                default=True,
            ),
        },
    }


def _normalize_ruling_requirements(value: Any, field: str) -> list[dict[str, Any]]:
    requirements = _array(value, field)
    if len(requirements) > 32:
        raise ValueError(f"{field} cannot contain more than 32 entries")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(requirements):
        entry_field = f"{field}[{index}]"
        entry = _object(raw, entry_field)
        _reject_unknown(
            entry,
            entry_field,
            {
                "kind",
                "reason",
                "source_excerpt",
                "default_resolver",
                "ruling_kind",
                "policy_ref",
                "requires_external_input_only_for",
            },
        )
        resolver = _text(
            entry.get("default_resolver"),
            f"{entry_field}.default_resolver",
        )
        if resolver not in {"agent", "external_input"}:
            raise ValueError(f"{entry_field}.default_resolver is invalid")
        ruling_kind = _text(
            entry.get("ruling_kind"),
            f"{entry_field}.ruling_kind",
            maximum=200,
        )
        expected_resolver = "external_input" if ruling_kind in EXTERNAL_RULING_KINDS else "agent"
        if resolver != expected_resolver:
            raise ValueError(
                f"{entry_field}.default_resolver must be {expected_resolver} "
                f"for ruling_kind {ruling_kind}"
            )
        normalized.append(
            {
                "kind": _text(entry.get("kind"), f"{entry_field}.kind", maximum=200),
                "reason": _text(
                    entry.get("reason"),
                    f"{entry_field}.reason",
                    maximum=1000,
                ),
                "source_excerpt": _text(
                    entry.get("source_excerpt"),
                    f"{entry_field}.source_excerpt",
                    maximum=4000,
                ),
                "default_resolver": resolver,
                "ruling_kind": ruling_kind,
                "policy_ref": _text(
                    entry.get("policy_ref"),
                    f"{entry_field}.policy_ref",
                    maximum=300,
                ),
                "requires_external_input_only_for": _string_list(
                    entry.get("requires_external_input_only_for") or [],
                    f"{entry_field}.requires_external_input_only_for",
                ),
            }
        )
    return normalized


def _normalize_spell(value: Any, field: str) -> dict[str, Any]:
    spell = _object(value, field)
    allowed = {
        "id",
        "source_key",
        "name",
        "level",
        "grant",
        "access",
        "definition",
        "point_cost",
        "custom_definition",
        "notes",
        "pack_id",
        "pack_version",
        "rule_refs",
        "mechanic_refs",
        "resolution",
        "ruling_requirements",
    }
    _reject_unknown(spell, field, allowed)
    grant = _object(spell.get("grant") or {}, f"{field}.grant")
    _reject_unknown(grant, f"{field}.grant", {"source_type", "source_key", "method"})
    access = _object(spell.get("access") or {}, f"{field}.access")
    _reject_unknown(
        access,
        f"{field}.access",
        {
            "known",
            "prepared",
            "always_prepared",
            "in_spellbook",
            "ritual_available",
            "at_will",
            "at_will_sources",
        },
    )
    definition = _object(spell.get("definition") or {}, f"{field}.definition")
    _reject_unknown(
        definition,
        f"{field}.definition",
        {"school", "casting_time", "range", "duration", "components", "effect"},
    )
    spell_range = _object(definition.get("range") or {}, f"{field}.definition.range")
    _reject_unknown(
        spell_range, f"{field}.definition.range", {"kind", "normal_ft", "long_ft", "area"}
    )
    range_kind = _text(spell_range.get("kind"), f"{field}.definition.range.kind", default="special")
    if range_kind not in {"self", "touch", "distance", "sight", "unlimited", "special"}:
        raise ValueError(f"{field}.definition.range.kind is invalid")
    duration = _object(definition.get("duration") or {}, f"{field}.definition.duration")
    _reject_unknown(
        duration, f"{field}.definition.duration", {"kind", "value", "unit", "concentration"}
    )
    duration_kind = _text(
        duration.get("kind"), f"{field}.definition.duration.kind", default="instantaneous"
    )
    if duration_kind not in {"instantaneous", "timed", "until_dispelled", "special"}:
        raise ValueError(f"{field}.definition.duration.kind is invalid")
    duration_unit = _text(
        duration.get("unit"), f"{field}.definition.duration.unit", default="round"
    )
    if duration_unit not in {"round", "minute", "hour", "day", "special"}:
        raise ValueError(f"{field}.definition.duration.unit is invalid")
    components = _object(definition.get("components") or {}, f"{field}.definition.components")
    _reject_unknown(
        components,
        f"{field}.definition.components",
        {"verbal", "somatic", "material", "material_description", "material_cost_cp", "consumed"},
    )
    at_will_sources = _string_list(
        access.get("at_will_sources") or [],
        f"{field}.access.at_will_sources",
    )
    if len(at_will_sources) != len(set(at_will_sources)):
        raise ValueError(f"{field}.access.at_will_sources contains duplicates")
    return {
        "id": _text(spell.get("id"), f"{field}.id", default=_uuid(), maximum=100),
        "source_key": _text(spell.get("source_key"), f"{field}.source_key", maximum=300),
        "name": _text(spell.get("name"), f"{field}.name", maximum=300),
        "level": _integer(spell.get("level"), f"{field}.level", minimum=0, maximum=9),
        "grant": {
            "source_type": _text(
                grant.get("source_type"), f"{field}.grant.source_type", default="custom"
            ),
            "source_key": _text(grant.get("source_key"), f"{field}.grant.source_key", maximum=300),
            "method": _text(
                grant.get("method"), f"{field}.grant.method", default="known", maximum=100
            ),
        },
        "access": {
            "known": _boolean(access.get("known"), f"{field}.access.known"),
            "prepared": _boolean(access.get("prepared"), f"{field}.access.prepared"),
            "always_prepared": _boolean(
                access.get("always_prepared"), f"{field}.access.always_prepared"
            ),
            "in_spellbook": _boolean(access.get("in_spellbook"), f"{field}.access.in_spellbook"),
            "ritual_available": _boolean(
                access.get("ritual_available"), f"{field}.access.ritual_available"
            ),
            "at_will": (
                _boolean(access.get("at_will"), f"{field}.access.at_will") or bool(at_will_sources)
            ),
            "at_will_sources": at_will_sources,
        },
        "definition": {
            "school": _text(definition.get("school"), f"{field}.definition.school", maximum=100),
            "casting_time": _text(
                definition.get("casting_time"), f"{field}.definition.casting_time", maximum=200
            ),
            "range": {
                "kind": range_kind,
                "normal_ft": _integer(
                    spell_range.get("normal_ft"), f"{field}.definition.range.normal_ft", minimum=0
                ),
                "long_ft": _integer(
                    spell_range.get("long_ft"), f"{field}.definition.range.long_ft", minimum=0
                ),
                "area": _text(
                    spell_range.get("area"), f"{field}.definition.range.area", maximum=200
                ),
            },
            "duration": {
                "kind": duration_kind,
                "value": _integer(
                    duration.get("value"), f"{field}.definition.duration.value", minimum=0
                ),
                "unit": duration_unit,
                "concentration": _boolean(
                    duration.get("concentration"), f"{field}.definition.duration.concentration"
                ),
            },
            "components": {
                "verbal": _boolean(
                    components.get("verbal"), f"{field}.definition.components.verbal"
                ),
                "somatic": _boolean(
                    components.get("somatic"), f"{field}.definition.components.somatic"
                ),
                "material": _boolean(
                    components.get("material"), f"{field}.definition.components.material"
                ),
                "material_description": _text(
                    components.get("material_description"),
                    f"{field}.definition.components.material_description",
                    maximum=500,
                ),
                "material_cost_cp": _integer(
                    components.get("material_cost_cp"),
                    f"{field}.definition.components.material_cost_cp",
                    minimum=0,
                ),
                "consumed": _boolean(
                    components.get("consumed"), f"{field}.definition.components.consumed"
                ),
            },
            "effect": _text(definition.get("effect"), f"{field}.definition.effect", maximum=4000),
        },
        "point_cost": _integer(spell.get("point_cost"), f"{field}.point_cost", minimum=0),
        "custom_definition": (
            _object(spell["custom_definition"], f"{field}.custom_definition")
            if spell.get("custom_definition") is not None
            else None
        ),
        "notes": _text(spell.get("notes"), f"{field}.notes", maximum=1200),
        "pack_id": _text(spell.get("pack_id"), f"{field}.pack_id", maximum=200),
        "pack_version": _text(spell.get("pack_version"), f"{field}.pack_version", maximum=64),
        "rule_refs": _string_list(spell.get("rule_refs") or [], f"{field}.rule_refs"),
        "mechanic_refs": _string_list(spell.get("mechanic_refs") or [], f"{field}.mechanic_refs"),
        "resolution": (
            normalize_spell_resolution(spell["resolution"], f"{field}.resolution")
            if spell.get("resolution") is not None
            else None
        ),
        **(
            {
                "ruling_requirements": _normalize_ruling_requirements(
                    spell.get("ruling_requirements") or [],
                    f"{field}.ruling_requirements",
                )
            }
            if "ruling_requirements" in spell
            else {}
        ),
    }


def _normalize_effect(value: Any, field: str) -> dict[str, Any]:
    effect = _object(value, field)
    allowed = {
        "id",
        "name",
        "kind",
        "source",
        "source_spell_id",
        "active",
        "concentration",
        "duration",
        "changes",
        "description",
        "ended_reason",
    }
    _reject_unknown(effect, field, allowed)
    normalized_duration = _normalize_effect_duration(
        effect.get("duration"),
        f"{field}.duration",
        allowed_periods=EFFECT_PERIODS,
    )
    changes = []
    for index, change in enumerate(_array(effect.get("changes") or [], f"{field}.changes")):
        item = _object(change, f"{field}.changes[{index}]")
        _reject_unknown(item, f"{field}.changes[{index}]", {"path", "mode", "value"})
        changes.append(
            {
                "path": _text(item.get("path"), f"{field}.changes[{index}].path", maximum=300),
                "mode": _text(
                    item.get("mode"),
                    f"{field}.changes[{index}].mode",
                    default="override",
                    maximum=100,
                ),
                "value": item.get("value"),
            }
        )
    normalized = {
        "id": _text(effect.get("id"), f"{field}.id", default=_uuid(), maximum=100),
        "name": _text(effect.get("name"), f"{field}.name", maximum=300),
        "kind": _text(effect.get("kind"), f"{field}.kind", default="custom", maximum=100),
        "source": _text(effect.get("source"), f"{field}.source", maximum=300),
        "source_spell_id": _text(
            effect.get("source_spell_id"), f"{field}.source_spell_id", maximum=100
        ),
        "active": _boolean(effect.get("active"), f"{field}.active", default=True),
        "concentration": _boolean(effect.get("concentration"), f"{field}.concentration"),
        "duration": normalized_duration,
        "changes": changes,
        "description": _text(effect.get("description"), f"{field}.description", maximum=1200),
    }
    ended_reason = _text(effect.get("ended_reason"), f"{field}.ended_reason", maximum=300)
    if ended_reason:
        if normalized["active"]:
            raise ValueError(f"{field}.ended_reason requires an inactive effect")
        normalized["ended_reason"] = ended_reason
    return normalized


def _normalize_effect_duration(
    value: Any,
    field: str,
    *,
    allowed_periods: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Normalize actor and world durations onto the same tick remainder."""

    duration = _object(value or {}, field)
    _reject_unknown(
        duration,
        field,
        {
            "period",
            "remaining",
            "elapsed_ticks_remainder",
            "elapsed_minutes_remainder",
        },
    )
    period = _text(duration.get("period"), f"{field}.period", default="manual")
    if period not in allowed_periods:
        raise ValueError(f"{field}.period is invalid")
    normalized = {
        "period": period,
        "remaining": _integer(
            duration.get("remaining"),
            f"{field}.remaining",
            minimum=0,
        ),
    }
    legacy_minutes = _integer(
        duration.get("elapsed_minutes_remainder"),
        f"{field}.elapsed_minutes_remainder",
        minimum=0,
    )
    elapsed_ticks = _integer(
        duration.get("elapsed_ticks_remainder"),
        f"{field}.elapsed_ticks_remainder",
        default=legacy_minutes * TICKS_PER_MINUTE,
        minimum=0,
    )
    if (
        "elapsed_ticks_remainder" in duration
        and "elapsed_minutes_remainder" in duration
        and elapsed_ticks != legacy_minutes * TICKS_PER_MINUTE
    ):
        raise ValueError(f"{field}.elapsed_minutes_remainder must match elapsed_ticks_remainder")
    if elapsed_ticks:
        period_ticks = {
            "minute": TICKS_PER_MINUTE,
            "hour": TICKS_PER_HOUR,
            "day": TICKS_PER_DAY,
        }
        unit_ticks = period_ticks.get(period)
        if unit_ticks is None:
            raise ValueError(
                f"{field}.elapsed_ticks_remainder requires a minute, hour, or day period"
            )
        if elapsed_ticks >= unit_ticks:
            raise ValueError(f"{field}.elapsed_ticks_remainder must be below {unit_ticks}")
        normalized["elapsed_ticks_remainder"] = elapsed_ticks
    return normalized


def validate_character_sheet(
    sheet: dict[str, Any], *, rules: ResolutionContext | None = None
) -> dict[str, Any]:
    value = _merge_defaults(default_character_sheet(), _object(sheet, "sheet"))
    allowed = {
        "schema_version",
        "edition",
        "identity",
        "progression",
        "ability_generation",
        "abilities",
        "skills",
        "combat",
        "traits",
        "resources",
        "spellcasting",
        "content",
        "conditions",
        "effects",
        "adventure_state",
        "inventory",
    }
    _reject_unknown(value, "sheet", allowed)
    if _integer(value["schema_version"], "sheet.schema_version") != 2:
        raise ValueError("sheet.schema_version must be 2")
    edition = normalize_dnd_edition(_text(value["edition"], "sheet.edition"))
    ability_generation = normalize_ability_generation(value["ability_generation"], edition)

    identity = _object(value["identity"], "sheet.identity")
    _reject_unknown(
        identity,
        "sheet.identity",
        {
            "gender",
            "age",
            "height_cm",
            "weight_lb",
            "faith",
            "deity",
            "hair",
            "skin",
            "eyes",
            "portrait_uri",
        },
    )

    progression = _object(value["progression"], "sheet.progression")
    _reject_unknown(
        progression,
        "sheet.progression",
        {"level", "xp", "classes", "background", "background_grants", "species"},
    )
    classes = []
    for index, item in enumerate(_array(progression["classes"], "sheet.progression.classes")):
        entry = _object(item, f"sheet.progression.classes[{index}]")
        _reject_unknown(
            entry, f"sheet.progression.classes[{index}]", {"name", "level", "subclass", "hit_die"}
        )
        classes.append(
            {
                "name": _text(
                    entry.get("name"), f"sheet.progression.classes[{index}].name", maximum=200
                ),
                "level": _integer(
                    entry.get("level"),
                    f"sheet.progression.classes[{index}].level",
                    minimum=1,
                    maximum=20,
                ),
                "subclass": _text(
                    entry.get("subclass"),
                    f"sheet.progression.classes[{index}].subclass",
                    maximum=200,
                ),
                "hit_die": _integer(
                    entry.get("hit_die"),
                    f"sheet.progression.classes[{index}].hit_die",
                    minimum=1,
                    maximum=20,
                ),
            }
        )
    level = _integer(progression["level"], "sheet.progression.level", minimum=1, maximum=20)
    if classes and sum(item["level"] for item in classes) != level:
        raise ValueError("sheet.progression.level must equal the total class levels")
    background_grants = _object(
        progression["background_grants"], "sheet.progression.background_grants"
    )
    _reject_unknown(
        background_grants,
        "sheet.progression.background_grants",
        {"feature", "equipment_item_ids", "languages", "tools", "choices"},
    )

    abilities = _object(value["abilities"], "sheet.abilities")
    _reject_unknown(abilities, "sheet.abilities", set(ABILITY_NAMES))
    normalized_abilities = {
        ability: _normalize_ability(abilities[ability], f"sheet.abilities.{ability}")
        for ability in ABILITY_NAMES
    }
    skills = _object(value["skills"], "sheet.skills")
    _reject_unknown(skills, "sheet.skills", set(SKILL_ABILITIES))
    normalized_skills = {
        skill: _normalize_skill(skills[skill], f"sheet.skills.{skill}") for skill in SKILL_ABILITIES
    }

    combat = _object(value["combat"], "sheet.combat")
    _reject_unknown(
        combat,
        "sheet.combat",
        {
            "hp",
            "ac",
            "initiative",
            "attacks_per_action",
            "speed",
            "hit_dice",
            "hp_progression",
            "death_saves",
            "exhaustion",
            "inspiration",
            "wounded",
            "rest_history",
        },
    )
    hp = _object(combat["hp"], "sheet.combat.hp")
    _reject_unknown(hp, "sheet.combat.hp", {"value", "max", "temp"})
    hp_max = _integer(hp["max"], "sheet.combat.hp.max", minimum=1)
    hp_value = _integer(hp["value"], "sheet.combat.hp.value", minimum=0)
    if hp_value > hp_max:
        raise ValueError("sheet.combat.hp.value cannot exceed max")
    exhaustion = _integer(
        combat["exhaustion"],
        "sheet.combat.exhaustion",
        minimum=0,
        maximum=6,
    )
    hp_value = min(
        hp_value,
        effective_hit_point_maximum_value(
            edition=edition,
            base_maximum=hp_max,
            exhaustion=exhaustion,
        ),
    )
    ac = _object(combat["ac"], "sheet.combat.ac")
    _reject_unknown(ac, "sheet.combat.ac", {"base", "override"})
    initiative = _object(combat["initiative"], "sheet.combat.initiative")
    _reject_unknown(initiative, "sheet.combat.initiative", {"ability", "bonus"})
    initiative_ability = _text(initiative["ability"], "sheet.combat.initiative.ability")
    if initiative_ability not in ABILITY_NAMES:
        raise ValueError("sheet.combat.initiative.ability is invalid")
    speed = _object(combat["speed"], "sheet.combat.speed")
    _reject_unknown(speed, "sheet.combat.speed", {"walk", "fly", "swim", "climb", "burrow"})
    hit_dice = _object(combat["hit_dice"], "sheet.combat.hit_dice")
    normalized_hit_dice = {
        key: _normalize_resource(item, f"sheet.combat.hit_dice.{key}")
        for key, item in hit_dice.items()
    }
    hp_progression = []
    recorded_hp_levels: set[int] = set()
    for index, gain in enumerate(_array(combat["hp_progression"], "sheet.combat.hp_progression")):
        entry = _object(gain, f"sheet.combat.hp_progression[{index}]")
        _reject_unknown(
            entry,
            f"sheet.combat.hp_progression[{index}]",
            {
                "level",
                "method",
                "value",
                "source",
                "source_ref",
                "reason",
                "adjustments",
            },
        )
        gain_level = _integer(
            entry.get("level"), f"sheet.combat.hp_progression[{index}].level", minimum=1, maximum=20
        )
        if gain_level in recorded_hp_levels:
            raise ValueError("sheet.combat.hp_progression has duplicate levels")
        recorded_hp_levels.add(gain_level)
        method = _text(
            entry.get("method"), f"sheet.combat.hp_progression[{index}].method", default="manual"
        )
        if method not in {"fixed", "rolled", "manual"}:
            raise ValueError(f"sheet.combat.hp_progression[{index}].method is invalid")
        normalized_gain = {
            "level": gain_level,
            "method": method,
            "value": _integer(
                entry.get("value"), f"sheet.combat.hp_progression[{index}].value", minimum=0
            ),
            "source": _text(
                entry.get("source"), f"sheet.combat.hp_progression[{index}].source", maximum=300
            ),
        }
        if "source_ref" in entry:
            normalized_gain["source_ref"] = _text(
                entry.get("source_ref"),
                f"sheet.combat.hp_progression[{index}].source_ref",
                maximum=8192,
            )
        if "reason" in entry:
            normalized_gain["reason"] = _text(
                entry.get("reason"),
                f"sheet.combat.hp_progression[{index}].reason",
                maximum=1000,
            )
        if "adjustments" in entry:
            normalized_adjustments = []
            for adjustment_index, raw_adjustment in enumerate(
                _array(
                    entry.get("adjustments"),
                    f"sheet.combat.hp_progression[{index}].adjustments",
                )
            ):
                adjustment_path = (
                    f"sheet.combat.hp_progression[{index}]"
                    f".adjustments[{adjustment_index}]"
                )
                adjustment = _object(raw_adjustment, adjustment_path)
                _reject_unknown(
                    adjustment,
                    adjustment_path,
                    {
                        "kind",
                        "amount",
                        "source",
                        "previous_score",
                        "new_score",
                    },
                )
                kind = _text(adjustment.get("kind"), f"{adjustment_path}.kind")
                if kind not in {
                    "per_level_bonus",
                    "constitution_modifier_change",
                }:
                    raise ValueError(f"{adjustment_path}.kind is invalid")
                amount = _integer(
                    adjustment.get("amount"),
                    f"{adjustment_path}.amount",
                    minimum=-30,
                    maximum=30,
                )
                if amount == 0:
                    raise ValueError(f"{adjustment_path}.amount must not be zero")
                normalized_adjustment = {
                    "kind": kind,
                    "amount": amount,
                    "source": _text(
                        adjustment.get("source"),
                        f"{adjustment_path}.source",
                        maximum=300,
                    ),
                }
                score_fields = {"previous_score", "new_score"} & set(adjustment)
                if kind == "constitution_modifier_change":
                    if score_fields != {"previous_score", "new_score"}:
                        raise ValueError(
                            f"{adjustment_path} requires previous_score and new_score"
                        )
                    normalized_adjustment["previous_score"] = _integer(
                        adjustment.get("previous_score"),
                        f"{adjustment_path}.previous_score",
                        minimum=1,
                        maximum=30,
                    )
                    normalized_adjustment["new_score"] = _integer(
                        adjustment.get("new_score"),
                        f"{adjustment_path}.new_score",
                        minimum=1,
                        maximum=30,
                    )
                elif score_fields:
                    raise ValueError(
                        f"{adjustment_path} score fields require a Constitution change"
                    )
                normalized_adjustments.append(normalized_adjustment)
            normalized_gain["adjustments"] = normalized_adjustments
        hp_progression.append(normalized_gain)
    death_saves = _object(combat["death_saves"], "sheet.combat.death_saves")
    _reject_unknown(death_saves, "sheet.combat.death_saves", {"successes", "failures"})
    rest_history = _object(combat["rest_history"], "sheet.combat.rest_history")
    _reject_unknown(
        rest_history,
        "sheet.combat.rest_history",
        {
            "last_rest_type",
            "last_rest_started_elapsed_ticks",
            "last_rest_completed_elapsed_ticks",
            "last_long_rest_elapsed_ticks",
            "last_rest_started_elapsed_minutes",
            "last_rest_completed_elapsed_minutes",
            "last_long_rest_elapsed_minutes",
        },
    )
    last_rest_type = _text(
        rest_history.get("last_rest_type"), "sheet.combat.rest_history.last_rest_type"
    )
    if last_rest_type not in REST_TYPES | {""}:
        raise ValueError("sheet.combat.rest_history.last_rest_type is invalid")

    def optional_elapsed_ticks(tick_key: str, legacy_minute_key: str) -> int | None:
        raw_ticks = rest_history.get(tick_key)
        raw_minutes = rest_history.get(legacy_minute_key)
        ticks = (
            _integer(raw_ticks, f"sheet.combat.rest_history.{tick_key}", minimum=0)
            if raw_ticks is not None
            else None
        )
        legacy_ticks = (
            _integer(
                raw_minutes,
                f"sheet.combat.rest_history.{legacy_minute_key}",
                minimum=0,
            )
            * 10
            if raw_minutes is not None
            else None
        )
        if ticks is not None and legacy_ticks is not None and ticks != legacy_ticks:
            raise ValueError(f"sheet.combat.rest_history.{legacy_minute_key} must match {tick_key}")
        return ticks if ticks is not None else legacy_ticks

    normalized_rest_history = {
        "last_rest_type": last_rest_type,
        "last_rest_started_elapsed_ticks": optional_elapsed_ticks(
            "last_rest_started_elapsed_ticks",
            "last_rest_started_elapsed_minutes",
        ),
        "last_rest_completed_elapsed_ticks": optional_elapsed_ticks(
            "last_rest_completed_elapsed_ticks",
            "last_rest_completed_elapsed_minutes",
        ),
        "last_long_rest_elapsed_ticks": optional_elapsed_ticks(
            "last_long_rest_elapsed_ticks",
            "last_long_rest_elapsed_minutes",
        ),
    }
    started = normalized_rest_history["last_rest_started_elapsed_ticks"]
    completed = normalized_rest_history["last_rest_completed_elapsed_ticks"]
    if (started is None) != (completed is None):
        raise ValueError("sheet.combat.rest_history must record rest start and completion together")
    if started is not None and completed is not None and completed < started:
        raise ValueError("sheet.combat.rest_history completion cannot precede its start")

    traits = _object(value["traits"], "sheet.traits")
    _reject_unknown(
        traits,
        "sheet.traits",
        {
            "size",
            "alignment",
            "languages",
            "proficiencies",
            "resistances",
            "immunities",
            "vulnerabilities",
            "condition_immunities",
            "senses",
        },
    )
    proficiencies = _object(traits["proficiencies"], "sheet.traits.proficiencies")
    _reject_unknown(
        proficiencies,
        "sheet.traits.proficiencies",
        {"armor", "weapons", "tools", "tool_expertise"},
    )
    senses = _object(traits["senses"], "sheet.traits.senses")
    _reject_unknown(senses, "sheet.traits.senses", {*SENSE_NAMES, "passive_perception_bonus"})

    resources = _object(value["resources"], "sheet.resources")
    normalized_resources = {
        key: _normalize_resource(item, f"sheet.resources.{key}") for key, item in resources.items()
    }
    spellcasting = _object(value["spellcasting"], "sheet.spellcasting")
    _reject_unknown(
        spellcasting,
        "sheet.spellcasting",
        {
            "ability",
            "class_lists",
            "spell_slots",
            "pact_magic",
            "preparation",
            "ritual_casting",
            "spellbook",
            "casting_economy",
            "spell_points",
            "attack_bonus_override",
            "save_dc_override",
        },
    )
    spell_ability = spellcasting["ability"]
    if spell_ability is not None and spell_ability not in ABILITY_NAMES:
        raise ValueError("sheet.spellcasting.ability is invalid")
    spell_class_lists = _string_list(spellcasting["class_lists"], "sheet.spellcasting.class_lists")
    slots = _object(spellcasting["spell_slots"], "sheet.spellcasting.spell_slots")
    normalized_slots = {
        key: _normalize_resource(item, f"sheet.spellcasting.spell_slots.{key}")
        for key, item in slots.items()
    }
    pact_magic = spellcasting["pact_magic"]
    if pact_magic is not None:
        pact_magic = _normalize_resource(pact_magic, "sheet.spellcasting.pact_magic")
        if not int(pact_magic.get("slot_level", 0) or 0):
            raise ValueError("sheet.spellcasting.pact_magic.slot_level is required")
    casting_economy = _text(
        spellcasting["casting_economy"], "sheet.spellcasting.casting_economy", default="slots"
    )
    if casting_economy not in {"slots", "spell_points"}:
        raise ValueError("sheet.spellcasting.casting_economy is invalid")
    spell_points = spellcasting["spell_points"]
    if spell_points is not None:
        spell_points = _normalize_resource(spell_points, "sheet.spellcasting.spell_points")
    if casting_economy == "spell_points" and spell_points is None:
        raise ValueError("sheet.spellcasting.spell_points is required for spell_points casting")
    spell_attack_bonus_override = (
        _integer(
            spellcasting["attack_bonus_override"],
            "sheet.spellcasting.attack_bonus_override",
            minimum=-20,
            maximum=40,
        )
        if spellcasting["attack_bonus_override"] is not None
        else None
    )
    spell_save_dc_override = (
        _integer(
            spellcasting["save_dc_override"],
            "sheet.spellcasting.save_dc_override",
            minimum=0,
            maximum=99,
        )
        if spellcasting["save_dc_override"] is not None
        else None
    )
    preparation = _object(spellcasting["preparation"], "sheet.spellcasting.preparation")
    _reject_unknown(
        preparation,
        "sheet.spellcasting.preparation",
        {"mode", "max_prepared", "changes_on", "selected_spell_ids"},
    )
    preparation_mode = _text(preparation["mode"], "sheet.spellcasting.preparation.mode")
    if preparation_mode not in PREPARATION_MODES:
        raise ValueError("sheet.spellcasting.preparation.mode is invalid")
    changes_on = _text(preparation["changes_on"], "sheet.spellcasting.preparation.changes_on")
    if changes_on not in {"long_rest", "manual"}:
        raise ValueError("sheet.spellcasting.preparation.changes_on is invalid")
    spellbook = _object(spellcasting["spellbook"], "sheet.spellcasting.spellbook")
    _reject_unknown(spellbook, "sheet.spellcasting.spellbook", {"enabled", "spell_ids"})

    content = _object(value["content"], "sheet.content")
    _reject_unknown(
        content,
        "sheet.content",
        {"spells", "features", "feats", "activities", "selections"},
    )
    spells = [
        _normalize_spell(item, f"sheet.content.spells[{index}]")
        for index, item in enumerate(_array(content["spells"], "sheet.content.spells"))
    ]
    spell_ids = {spell["id"] for spell in spells}
    if len(spell_ids) != len(spells):
        raise ValueError("sheet.content.spells contains duplicate ids")
    spellbook_ids = _string_list(spellbook["spell_ids"], "sheet.spellcasting.spellbook.spell_ids")
    if not set(spellbook_ids).issubset(spell_ids):
        raise ValueError("spellbook references an unknown spell")
    for spell in spells:
        spell["access"]["in_spellbook"] = spell["id"] in spellbook_ids
    selected_spell_ids = _string_list(
        preparation["selected_spell_ids"], "sheet.spellcasting.preparation.selected_spell_ids"
    )
    if len(selected_spell_ids) != len(set(selected_spell_ids)):
        raise ValueError("sheet.spellcasting.preparation.selected_spell_ids contains duplicates")
    if len(selected_spell_ids) > _integer(
        preparation["max_prepared"], "sheet.spellcasting.preparation.max_prepared", minimum=0
    ):
        raise ValueError("prepared spell selection exceeds max_prepared")
    for spell_id in selected_spell_ids:
        spell = next((item for item in spells if item["id"] == spell_id), None)
        if spell is None:
            raise ValueError("prepared spell selection references an unknown spell")
        class_prepared = spell["grant"]["method"] == "class_prepared"
        if class_prepared:
            if preparation["mode"] != "prepared":
                raise ValueError("class-prepared spell requires prepared-caster mode")
            source_class = spell["grant"]["source_key"].strip().casefold()
            class_names = {item["name"].strip().casefold() for item in classes}
            if source_class not in class_names:
                raise ValueError("class-prepared spell grant must name a recorded class")
        if not (spell["access"]["known"] or spell["id"] in spellbook_ids or class_prepared):
            raise ValueError("prepared spell must be known or in the spellbook")
    for spell in spells:
        spell["access"]["prepared"] = (
            spell["id"] in selected_spell_ids or spell["access"]["always_prepared"]
        )

    def _content_entries(name: str) -> list[dict[str, Any]]:
        result = []
        for index, item in enumerate(_array(content[name], f"sheet.content.{name}")):
            entry = _object(item, f"sheet.content.{name}[{index}]")
            _reject_unknown(
                entry,
                f"sheet.content.{name}[{index}]",
                {
                    "id",
                    "name",
                    "source_key",
                    "description",
                    "uses",
                    "resource_key",
                    "activation",
                    "scaling",
                    "resource_scaling",
                    "attack_scaling",
                    "choices",
                    "advancement_grants",
                    "pack_id",
                    "pack_version",
                    "rule_refs",
                    "mechanic_refs",
                },
            )
            activation = _object(
                entry.get("activation") or {}, f"sheet.content.{name}[{index}].activation"
            )
            _reject_unknown(
                activation,
                f"sheet.content.{name}[{index}].activation",
                {"type", "cost", "trigger"},
            )
            activation_type = _text(
                activation.get("type"),
                f"sheet.content.{name}[{index}].activation.type",
                default="passive",
            )
            if activation_type not in {"passive", "action", "bonus_action", "reaction", "special"}:
                raise ValueError(f"sheet.content.{name}[{index}].activation.type is invalid")
            scaling = []
            for scale_index, scale in enumerate(
                _array(entry.get("scaling") or [], f"sheet.content.{name}[{index}].scaling")
            ):
                scale_entry = _object(
                    scale, f"sheet.content.{name}[{index}].scaling[{scale_index}]"
                )
                _reject_unknown(
                    scale_entry,
                    f"sheet.content.{name}[{index}].scaling[{scale_index}]",
                    {"level", "value", "description"},
                )
                scaling.append(
                    {
                        "level": _integer(
                            scale_entry.get("level"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].level",
                            minimum=1,
                            maximum=20,
                        ),
                        "value": _integer(
                            scale_entry.get("value"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].value",
                        ),
                        "description": _text(
                            scale_entry.get("description"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].description",
                            maximum=1000,
                        ),
                    }
                )
            advancement_grants = []
            seen_grant_levels: set[int] = set()
            for grant_index, grant in enumerate(
                _array(
                    entry.get("advancement_grants") or [],
                    f"sheet.content.{name}[{index}].advancement_grants",
                )
            ):
                grant_entry = _object(
                    grant,
                    (f"sheet.content.{name}[{index}].advancement_grants[{grant_index}]"),
                )
                _reject_unknown(
                    grant_entry,
                    (f"sheet.content.{name}[{index}].advancement_grants[{grant_index}]"),
                    {"level", "choices", "pack_id", "pack_version", "rule_refs"},
                )
                grant_level = _integer(
                    grant_entry.get("level"),
                    (f"sheet.content.{name}[{index}].advancement_grants[{grant_index}].level"),
                    minimum=1,
                    maximum=20,
                )
                if grant_level in seen_grant_levels:
                    raise ValueError(
                        f"sheet.content.{name}[{index}].advancement_grants "
                        "contains duplicate levels"
                    )
                seen_grant_levels.add(grant_level)
                advancement_grants.append(
                    {
                        "level": grant_level,
                        "choices": _object(
                            grant_entry.get("choices") or {},
                            (
                                f"sheet.content.{name}[{index}]."
                                f"advancement_grants[{grant_index}].choices"
                            ),
                        ),
                        "pack_id": _text(
                            grant_entry.get("pack_id"),
                            (
                                f"sheet.content.{name}[{index}]."
                                f"advancement_grants[{grant_index}].pack_id"
                            ),
                            maximum=200,
                        ),
                        "pack_version": _text(
                            grant_entry.get("pack_version"),
                            (
                                f"sheet.content.{name}[{index}]."
                                f"advancement_grants[{grant_index}].pack_version"
                            ),
                            maximum=64,
                        ),
                        "rule_refs": _string_list(
                            grant_entry.get("rule_refs") or [],
                            (
                                f"sheet.content.{name}[{index}]."
                                f"advancement_grants[{grant_index}].rule_refs"
                            ),
                        ),
                    }
                )
            raw_attack_scaling = _object(
                entry.get("attack_scaling") or {},
                f"sheet.content.{name}[{index}].attack_scaling",
            )
            _reject_unknown(
                raw_attack_scaling,
                f"sheet.content.{name}[{index}].attack_scaling",
                {"class_name", "attacks_per_action_by_level"},
            )
            attacks_by_level: dict[str, int] = {}
            raw_attacks_by_level = _object(
                raw_attack_scaling.get("attacks_per_action_by_level") or {},
                (f"sheet.content.{name}[{index}].attack_scaling.attacks_per_action_by_level"),
            )
            for raw_level, amount in raw_attacks_by_level.items():
                level_text = str(raw_level).strip()
                if not level_text.isdigit():
                    raise ValueError(
                        f"sheet.content.{name}[{index}].attack_scaling level must be an integer"
                    )
                level = _integer(
                    int(level_text),
                    (
                        f"sheet.content.{name}[{index}].attack_scaling."
                        "attacks_per_action_by_level level"
                    ),
                    minimum=1,
                    maximum=20,
                )
                attacks_by_level[str(level)] = _integer(
                    amount,
                    (
                        f"sheet.content.{name}[{index}].attack_scaling."
                        f"attacks_per_action_by_level.{level}"
                    ),
                    minimum=1,
                    maximum=10,
                )
            attack_scaling = (
                {
                    "class_name": _text(
                        raw_attack_scaling.get("class_name"),
                        (f"sheet.content.{name}[{index}].attack_scaling.class_name"),
                        maximum=200,
                    ),
                    "attacks_per_action_by_level": attacks_by_level,
                }
                if raw_attack_scaling
                else {}
            )
            result.append(
                {
                    "id": _text(
                        entry.get("id"),
                        f"sheet.content.{name}[{index}].id",
                        default=_uuid(),
                        maximum=100,
                    ),
                    "name": _text(
                        entry.get("name"), f"sheet.content.{name}[{index}].name", maximum=300
                    ),
                    "source_key": _text(
                        entry.get("source_key"),
                        f"sheet.content.{name}[{index}].source_key",
                        maximum=300,
                    ),
                    "description": _text(
                        entry.get("description"),
                        f"sheet.content.{name}[{index}].description",
                        maximum=2000,
                    ),
                    "uses": _normalize_resource(
                        (entry.get("uses") or {} if "uses" in entry else {"unlimited": True}),
                        f"sheet.content.{name}[{index}].uses",
                    ),
                    "resource_key": _text(
                        entry.get("resource_key"),
                        f"sheet.content.{name}[{index}].resource_key",
                        maximum=200,
                    ),
                    "activation": {
                        "type": activation_type,
                        "cost": _integer(
                            activation.get("cost"),
                            f"sheet.content.{name}[{index}].activation.cost",
                            minimum=0,
                        ),
                        "trigger": _text(
                            activation.get("trigger"),
                            f"sheet.content.{name}[{index}].activation.trigger",
                            maximum=1000,
                        ),
                    },
                    "scaling": scaling,
                    "resource_scaling": _normalize_resource_scaling(
                        entry.get("resource_scaling") or {},
                        f"sheet.content.{name}[{index}].resource_scaling",
                    ),
                    "attack_scaling": attack_scaling,
                    "choices": _object(
                        entry.get("choices") or {}, f"sheet.content.{name}[{index}].choices"
                    ),
                    "advancement_grants": advancement_grants,
                    "pack_id": _text(
                        entry.get("pack_id"),
                        f"sheet.content.{name}[{index}].pack_id",
                        maximum=200,
                    ),
                    "pack_version": _text(
                        entry.get("pack_version"),
                        f"sheet.content.{name}[{index}].pack_version",
                        maximum=64,
                    ),
                    "rule_refs": _string_list(
                        entry.get("rule_refs") or [],
                        f"sheet.content.{name}[{index}].rule_refs",
                    ),
                    "mechanic_refs": _string_list(
                        entry.get("mechanic_refs") or [],
                        f"sheet.content.{name}[{index}].mechanic_refs",
                    ),
                }
            )
        return result

    selections: list[dict[str, Any]] = []
    for index, item in enumerate(_array(content["selections"], "sheet.content.selections")):
        entry = _object(item, f"sheet.content.selections[{index}]")
        _reject_unknown(
            entry,
            f"sheet.content.selections[{index}]",
            {
                "artifact_id",
                "kind",
                "name",
                "pack_id",
                "pack_version",
                "rule_refs",
                "mechanic_refs",
                "selection",
            },
        )
        selections.append(
            {
                "artifact_id": _text(
                    entry.get("artifact_id"),
                    f"sheet.content.selections[{index}].artifact_id",
                    maximum=300,
                ),
                "kind": _text(
                    entry.get("kind"),
                    f"sheet.content.selections[{index}].kind",
                    maximum=100,
                ),
                "name": _text(
                    entry.get("name"),
                    f"sheet.content.selections[{index}].name",
                    maximum=300,
                ),
                "pack_id": _text(
                    entry.get("pack_id"),
                    f"sheet.content.selections[{index}].pack_id",
                    maximum=200,
                ),
                "pack_version": _text(
                    entry.get("pack_version"),
                    f"sheet.content.selections[{index}].pack_version",
                    maximum=64,
                ),
                "rule_refs": _string_list(
                    entry.get("rule_refs") or [],
                    f"sheet.content.selections[{index}].rule_refs",
                ),
                "mechanic_refs": _string_list(
                    entry.get("mechanic_refs") or [],
                    f"sheet.content.selections[{index}].mechanic_refs",
                ),
                "selection": _object(
                    entry.get("selection") or {},
                    f"sheet.content.selections[{index}].selection",
                ),
            }
        )
    selection_ids = [item["artifact_id"] for item in selections]
    if len(selection_ids) != len(set(selection_ids)):
        raise ValueError("sheet.content.selections contains duplicate artifact ids")

    inventory = validate_inventory(value["inventory"])
    item_spell_ids = {
        str(dict(specification.get("card") or {}).get("id") or "")
        for item in inventory["items"]
        for specification in (
            dict(dict(item.get("mechanics") or {}).get("spellcasting") or {}).get("spells") or []
        )
        if isinstance(specification, dict)
    }
    item_spell_ids.discard("")

    conditions = sorted(
        condition_ids(_string_list(value["conditions"], "sheet.conditions"))
    )
    if exhaustion >= 6 and "dead" not in conditions:
        conditions.append("dead")
    if "dead" in conditions:
        for item in inventory["items"]:
            if item["attunement"] == "attuned":
                item["attunement"] = "required"
    effects = [
        _normalize_effect(item, f"sheet.effects[{index}]")
        for index, item in enumerate(_array(value["effects"], "sheet.effects"))
    ]
    effect_ids = {effect["id"] for effect in effects}
    if len(effect_ids) != len(effects):
        raise ValueError("sheet.effects contains duplicate ids")
    active_concentration = [
        effect for effect in effects if effect["active"] and effect["concentration"]
    ]
    if len(active_concentration) > 1:
        raise ValueError("a character can have only one active concentration effect")
    for effect in effects:
        source_spell_id = effect["source_spell_id"]
        if source_spell_id and source_spell_id not in spell_ids | item_spell_ids:
            raise ValueError("effect source_spell_id references an unknown spell")

    adventure_state = _object(value["adventure_state"], "sheet.adventure_state")
    _reject_unknown(
        adventure_state,
        "sheet.adventure_state",
        {"reputation", "contributions", "blessings", "wards", "legendary_boons", "status_tags"},
    )
    reputation = _object(adventure_state["reputation"], "sheet.adventure_state.reputation")
    contributions = _object(adventure_state["contributions"], "sheet.adventure_state.contributions")
    background_item_ids = _string_list(
        background_grants["equipment_item_ids"],
        "sheet.progression.background_grants.equipment_item_ids",
    )
    inventory_item_ids = {item["id"] for item in inventory["items"]}
    if not set(background_item_ids).issubset(inventory_item_ids):
        raise ValueError("background equipment references an unknown inventory item")

    normalized = {
        "schema_version": 2,
        "edition": edition,
        "identity": {
            "gender": _text(identity["gender"], "sheet.identity.gender", maximum=100),
            "age": _text(identity["age"], "sheet.identity.age", maximum=100),
            "height_cm": (
                _integer(identity["height_cm"], "sheet.identity.height_cm", minimum=1)
                if identity["height_cm"] is not None
                else None
            ),
            "weight_lb": (
                _integer(identity["weight_lb"], "sheet.identity.weight_lb", minimum=1)
                if identity["weight_lb"] is not None
                else None
            ),
            "faith": _text(identity["faith"], "sheet.identity.faith", maximum=200),
            "deity": _text(identity["deity"], "sheet.identity.deity", maximum=200),
            "hair": _text(identity["hair"], "sheet.identity.hair", maximum=100),
            "skin": _text(identity["skin"], "sheet.identity.skin", maximum=100),
            "eyes": _text(identity["eyes"], "sheet.identity.eyes", maximum=100),
            "portrait_uri": _text(
                identity["portrait_uri"], "sheet.identity.portrait_uri", maximum=2000
            ),
        },
        "ability_generation": ability_generation,
        "progression": {
            "level": level,
            "xp": _integer(progression["xp"], "sheet.progression.xp", minimum=0),
            "classes": classes,
            "background": _text(
                progression["background"], "sheet.progression.background", maximum=200
            ),
            "background_grants": {
                "feature": _text(
                    background_grants["feature"],
                    "sheet.progression.background_grants.feature",
                    maximum=300,
                ),
                "equipment_item_ids": background_item_ids,
                "languages": _string_list(
                    background_grants["languages"], "sheet.progression.background_grants.languages"
                ),
                "tools": _string_list(
                    background_grants["tools"], "sheet.progression.background_grants.tools"
                ),
                "choices": _object(
                    background_grants["choices"], "sheet.progression.background_grants.choices"
                ),
            },
            "species": _text(progression["species"], "sheet.progression.species", maximum=200),
        },
        "abilities": normalized_abilities,
        "skills": normalized_skills,
        "combat": {
            "hp": {
                "value": hp_value,
                "max": hp_max,
                "temp": _integer(hp["temp"], "sheet.combat.hp.temp", minimum=0),
            },
            "ac": {
                "base": _integer(ac["base"], "sheet.combat.ac.base", minimum=0),
                "override": (
                    _integer(ac["override"], "sheet.combat.ac.override", minimum=0)
                    if ac["override"] is not None
                    else None
                ),
            },
            "initiative": {
                "ability": initiative_ability,
                "bonus": _integer(initiative["bonus"], "sheet.combat.initiative.bonus"),
            },
            "attacks_per_action": _integer(
                combat["attacks_per_action"],
                "sheet.combat.attacks_per_action",
                minimum=1,
                maximum=10,
            ),
            "speed": {
                mode: _integer(speed[mode], f"sheet.combat.speed.{mode}", minimum=0)
                for mode in ("walk", "fly", "swim", "climb", "burrow")
            },
            "hit_dice": normalized_hit_dice,
            "hp_progression": hp_progression,
            "death_saves": {
                "successes": _integer(
                    death_saves["successes"],
                    "sheet.combat.death_saves.successes",
                    minimum=0,
                    maximum=3,
                ),
                "failures": _integer(
                    death_saves["failures"],
                    "sheet.combat.death_saves.failures",
                    minimum=0,
                    maximum=3,
                ),
            },
            "exhaustion": exhaustion,
            "inspiration": _boolean(combat["inspiration"], "sheet.combat.inspiration"),
            "wounded": _boolean(combat["wounded"], "sheet.combat.wounded"),
            "rest_history": normalized_rest_history,
        },
        "traits": {
            "size": _text(traits["size"], "sheet.traits.size", maximum=100),
            "alignment": _text(traits["alignment"], "sheet.traits.alignment", maximum=100),
            "languages": _string_list(traits["languages"], "sheet.traits.languages"),
            "proficiencies": {
                key: _string_list(proficiencies[key], f"sheet.traits.proficiencies.{key}")
                for key in ("armor", "weapons", "tools", "tool_expertise")
            },
            "resistances": _string_list(traits["resistances"], "sheet.traits.resistances"),
            "immunities": _string_list(traits["immunities"], "sheet.traits.immunities"),
            "vulnerabilities": _string_list(
                traits["vulnerabilities"], "sheet.traits.vulnerabilities"
            ),
            "condition_immunities": _string_list(
                traits["condition_immunities"], "sheet.traits.condition_immunities"
            ),
            "senses": {
                sense: _integer(senses[sense], f"sheet.traits.senses.{sense}", minimum=0)
                for sense in SENSE_NAMES
            }
            | {
                "passive_perception_bonus": _integer(
                    senses["passive_perception_bonus"],
                    "sheet.traits.senses.passive_perception_bonus",
                ),
            },
        },
        "resources": normalized_resources,
        "spellcasting": {
            "ability": spell_ability,
            "class_lists": spell_class_lists,
            "spell_slots": normalized_slots,
            "pact_magic": pact_magic,
            "casting_economy": casting_economy,
            "spell_points": spell_points,
            "attack_bonus_override": spell_attack_bonus_override,
            "save_dc_override": spell_save_dc_override,
            "preparation": {
                "mode": preparation_mode,
                "max_prepared": _integer(
                    preparation["max_prepared"],
                    "sheet.spellcasting.preparation.max_prepared",
                    minimum=0,
                ),
                "changes_on": changes_on,
                "selected_spell_ids": selected_spell_ids,
            },
            "ritual_casting": _boolean(
                spellcasting["ritual_casting"], "sheet.spellcasting.ritual_casting"
            ),
            "spellbook": {
                "enabled": _boolean(spellbook["enabled"], "sheet.spellcasting.spellbook.enabled"),
                "spell_ids": spellbook_ids,
            },
        },
        "content": {
            "spells": spells,
            "features": _content_entries("features"),
            "feats": _content_entries("feats"),
            "activities": _content_entries("activities"),
            "selections": selections,
        },
        "conditions": conditions,
        "effects": effects,
        "adventure_state": {
            "reputation": {
                key: _integer(entry, f"sheet.adventure_state.reputation.{key}")
                for key, entry in reputation.items()
            },
            "contributions": {
                key: _integer(entry, f"sheet.adventure_state.contributions.{key}")
                for key, entry in contributions.items()
            },
            "blessings": _string_list(
                adventure_state["blessings"], "sheet.adventure_state.blessings"
            ),
            "wards": _string_list(adventure_state["wards"], "sheet.adventure_state.wards"),
            "legendary_boons": _string_list(
                adventure_state["legendary_boons"], "sheet.adventure_state.legendary_boons"
            ),
            "status_tags": _string_list(
                adventure_state["status_tags"], "sheet.adventure_state.status_tags"
            ),
        },
        "inventory": inventory,
    }
    extension = apply_rule_event(normalized, "character.validate", rules)
    if extension.status != "committed":
        raise RuleEventRulingRequiredError(
            "active rule pack requires a character validation ruling",
            event="character.validate",
            status=extension.status,
            pending=extension.pending,
        )
    return normalized


def validate_character_notes(
    notes: dict[str, Any], *, character_type: str | None = None
) -> dict[str, Any]:
    value = _merge_defaults(default_character_notes(), _object(notes, "notes"))
    _reject_unknown(
        value, "notes", {"schema_version", "profile", "memories", "relationships", "goals"}
    )
    if _integer(value["schema_version"], "notes.schema_version") != 2:
        raise ValueError("notes.schema_version must be 2")
    profile = _object(value["profile"], "notes.profile")
    _reject_unknown(
        profile,
        "notes.profile",
        {
            "summary",
            "appearance",
            "personality_traits",
            "ideals",
            "bonds",
            "flaws",
            "motivation",
            "backstory",
            "dm_notes",
        },
    )
    memories = []
    for index, item in enumerate(_array(value["memories"], "notes.memories")):
        memory = _object(item, f"notes.memories[{index}]")
        _reject_unknown(
            memory,
            f"notes.memories[{index}]",
            {
                "id",
                "kind",
                "summary",
                "importance",
                "participants",
                "source_event_id",
                "visibility",
                "status",
            },
        )
        visibility = _text(
            memory.get("visibility"), f"notes.memories[{index}].visibility", default="dm"
        )
        if visibility not in GAMEPLAY_VISIBILITY_SCOPES:
            raise ValueError("memory visibility is invalid")
        status = _text(memory.get("status"), f"notes.memories[{index}].status", default="active")
        if status not in {"active", "resolved", "superseded"}:
            raise ValueError("memory status is invalid")
        memories.append(
            {
                "id": _text(
                    memory.get("id"), f"notes.memories[{index}].id", default=_uuid(), maximum=100
                ),
                "kind": _text(
                    memory.get("kind"), f"notes.memories[{index}].kind", default="fact", maximum=100
                ),
                "summary": _text(
                    memory.get("summary"), f"notes.memories[{index}].summary", maximum=1200
                ),
                "importance": _integer(
                    memory.get("importance"),
                    f"notes.memories[{index}].importance",
                    default=3,
                    minimum=1,
                    maximum=5,
                ),
                "participants": _string_list(
                    memory.get("participants") or [], f"notes.memories[{index}].participants"
                ),
                "source_event_id": _text(
                    memory.get("source_event_id"),
                    f"notes.memories[{index}].source_event_id",
                    maximum=100,
                ),
                "visibility": visibility,
                "status": status,
            }
        )
    normalized = {
        "schema_version": 2,
        "profile": {
            "summary": _text(profile["summary"], "notes.profile.summary", maximum=1200),
            "appearance": _text(profile["appearance"], "notes.profile.appearance", maximum=1200),
            "personality_traits": _string_list(
                profile["personality_traits"], "notes.profile.personality_traits"
            ),
            "ideals": _string_list(profile["ideals"], "notes.profile.ideals"),
            "bonds": _string_list(profile["bonds"], "notes.profile.bonds"),
            "flaws": _string_list(profile["flaws"], "notes.profile.flaws"),
            "motivation": _text(profile["motivation"], "notes.profile.motivation", maximum=1200),
            "backstory": _text(profile["backstory"], "notes.profile.backstory", maximum=8000),
            "dm_notes": _text(profile["dm_notes"], "notes.profile.dm_notes", maximum=4000),
        },
        "memories": memories,
        "relationships": [
            _object(item, "notes.relationships[]")
            for item in _array(value["relationships"], "notes.relationships")
        ],
        "goals": [_object(item, "notes.goals[]") for item in _array(value["goals"], "notes.goals")],
    }
    if character_type in NON_PLAYER_CHARACTER_TYPES and not normalized["profile"]["summary"]:
        raise ValueError(f"{character_type} notes.profile.summary is required")
    return normalized


def validate_character_notes_update(
    current: dict[str, Any],
    candidate: dict[str, Any],
    *,
    character_type: str | None = None,
) -> dict[str, Any]:
    """Validate mutable notes while keeping legacy embedded memories import-only."""

    # Legacy actor rows may predate the required NPC/monster profile summary.
    # They remain readable so a canonical update can repair them in place.
    before = validate_character_notes(current)
    after = validate_character_notes(candidate, character_type=character_type)
    if after["memories"] != before["memories"]:
        raise ValueError("notes.memories is import-only; use ActorKnowledge for subjective memory")
    return after


def validate_party_state(state: dict[str, Any]) -> dict[str, Any]:
    from sagasmith_dnd.game_time import (
        game_time_from_ticks,
        validate_game_time,
        validate_world_time,
    )
    from sagasmith_dnd.playthrough import validate_playthrough_manifest
    from sagasmith_dnd.random_stream import validate_random_stream_state

    value = copy.deepcopy(_object(state, "campaign.state"))
    # ModuleService owns the active immutable module revision. Older MCP
    # releases copied that fact into campaign state, where it could drift
    # across activation, restore, and branch operations.
    value.pop("module_imports", None)
    game_phase = str(value.get("game_phase") or "lobby").strip().casefold()
    if game_phase == "combat":
        # Legacy state duplicated the active encounter as a second combat flag.
        # Combat exposure is now derived only from campaign.state.combat.active.
        game_phase = "play"
    if game_phase not in CAMPAIGN_GAME_PHASES:
        raise ValueError("campaign.state.game_phase must be lobby or play")
    value["game_phase"] = game_phase
    if "game_time" in value:
        game_time = validate_game_time(value["game_time"])
    else:
        legacy_clock = _object(value.get("world_time") or {}, "campaign.state.world_time")
        legacy_elapsed = legacy_clock.get("elapsed_minutes")
        game_time = game_time_from_ticks(
            int(legacy_elapsed) * 10
            if isinstance(legacy_elapsed, int) and not isinstance(legacy_elapsed, bool)
            else 0
        )
    value["game_time"] = game_time
    party = _object(value.get("party") or {}, "campaign.state.party")
    _reject_unknown(party, "campaign.state.party", {"inventory", "notes"})
    value["party"] = {
        "inventory": validate_inventory(party.get("inventory") or {}),
        "notes": _text(party.get("notes"), "campaign.state.party.notes", maximum=1200),
    }
    world_effects = [
        validate_world_effect(item, field=f"campaign.state.world_effects[{index}]")
        for index, item in enumerate(
            _array(value.get("world_effects") or [], "campaign.state.world_effects")
        )
    ]
    world_effect_ids = [item["id"] for item in world_effects]
    if len(world_effect_ids) != len(set(world_effect_ids)):
        raise ValueError("campaign.state.world_effects contains duplicate ids")
    value["world_effects"] = world_effects
    if "world_time" in value:
        value["world_time"] = validate_world_time(
            value["world_time"],
            game_time=game_time,
        )
    if "random_stream" in value:
        value["random_stream"] = validate_random_stream_state(value["random_stream"])
    if "playthrough_manifest" in value:
        value["playthrough_manifest"] = validate_playthrough_manifest(value["playthrough_manifest"])
    return value


def validate_world_time(value: Any, *, game_time: Any | None = None) -> dict[str, Any]:
    """Validate the canonical branch-local campaign clock."""

    from sagasmith_dnd.game_time import (
        game_time_from_ticks,
    )
    from sagasmith_dnd.game_time import (
        validate_world_time as validate_calendar,
    )

    return validate_calendar(
        value,
        game_time=game_time if game_time is not None else game_time_from_ticks(),
    )


def validate_world_effect(value: Any, *, field: str = "world_effect") -> dict[str, Any]:
    """Normalize a timed effect attached to campaign space rather than an actor."""
    effect = _object(value, field)
    _reject_unknown(
        effect,
        field,
        {
            "id",
            "name",
            "kind",
            "source",
            "source_spell_id",
            "source_actor_id",
            "target",
            "active",
            "visibility",
            "duration",
            "description",
            "created_at_elapsed_ticks",
            "created_at_elapsed_minutes",
            "metadata",
            "ended_reason",
        },
    )
    normalized_duration = _normalize_effect_duration(
        effect.get("duration"),
        f"{field}.duration",
        allowed_periods={"manual", "round", "encounter", "minute", "hour", "day"},
    )
    target = _object(effect.get("target") or {}, f"{field}.target")
    _reject_unknown(target, f"{field}.target", {"kind", "id", "label"})
    target_kind = _text(target.get("kind"), f"{field}.target.kind", default="campaign")
    if target_kind not in {"campaign", "scene", "location", "object"}:
        raise ValueError(f"{field}.target.kind is invalid")
    target_id = _text(target.get("id"), f"{field}.target.id", maximum=300)
    if target_kind != "campaign" and not target_id:
        raise ValueError(f"{field}.target.id is required for {target_kind} effects")
    raw_created_minutes = effect.get("created_at_elapsed_minutes")
    raw_created_ticks = effect.get("created_at_elapsed_ticks")
    created_at_elapsed_minutes = (
        _integer(
            raw_created_minutes,
            f"{field}.created_at_elapsed_minutes",
            minimum=0,
        )
        if raw_created_minutes is not None
        else None
    )
    created_at_elapsed_ticks = (
        _integer(
            raw_created_ticks,
            f"{field}.created_at_elapsed_ticks",
            minimum=0,
        )
        if raw_created_ticks is not None
        else (created_at_elapsed_minutes or 0) * TICKS_PER_MINUTE
    )
    if (
        created_at_elapsed_minutes is not None
        and created_at_elapsed_minutes != created_at_elapsed_ticks // TICKS_PER_MINUTE
    ):
        raise ValueError(f"{field}.created_at_elapsed_minutes must match created_at_elapsed_ticks")
    normalized = {
        "id": _text(effect.get("id"), f"{field}.id", default=_uuid(), maximum=100),
        "name": _text(effect.get("name"), f"{field}.name", maximum=300),
        "kind": _text(effect.get("kind"), f"{field}.kind", default="custom", maximum=100),
        "source": _text(effect.get("source"), f"{field}.source", maximum=300),
        "source_spell_id": _text(
            effect.get("source_spell_id"), f"{field}.source_spell_id", maximum=300
        ),
        "source_actor_id": _text(
            effect.get("source_actor_id"), f"{field}.source_actor_id", maximum=100
        ),
        "target": {
            "kind": target_kind,
            "id": target_id,
            "label": _text(target.get("label"), f"{field}.target.label", maximum=300),
        },
        "active": _boolean(effect.get("active"), f"{field}.active", default=True),
        "visibility": _text(effect.get("visibility"), f"{field}.visibility", default="party"),
        "duration": normalized_duration,
        "description": _text(effect.get("description"), f"{field}.description", maximum=1200),
        "created_at_elapsed_ticks": created_at_elapsed_ticks,
        "metadata": _object(effect.get("metadata") or {}, f"{field}.metadata"),
    }
    if normalized["visibility"] not in EFFECT_VISIBILITY_SCOPES:
        raise ValueError(f"{field}.visibility is invalid")
    ended_reason = _text(effect.get("ended_reason"), f"{field}.ended_reason", maximum=300)
    if ended_reason:
        if normalized["active"]:
            raise ValueError(f"{field}.ended_reason requires an inactive effect")
        normalized["ended_reason"] = ended_reason
    return normalized


def _derive_armor_class(
    value: dict[str, Any], ability_modifiers: dict[str, int], active_effects: list[dict[str, Any]]
) -> tuple[int, dict[str, Any], set[str]]:
    inventory = value["inventory"]
    items = {item["id"]: item for item in inventory["items"]}
    ac = value["combat"]["ac"]
    override = ac["override"]
    breakdown: dict[str, Any] = {
        "mode": "override" if override is not None else "base",
        "base": override if override is not None else ac["base"],
        "armor": None,
        "shield": None,
        "magic_items": [],
        "effects": [],
    }
    unarmored_formulas: list[dict[str, Any]] = []
    valid_unarmored_changes: set[tuple[str, str]] = set()
    for effect in active_effects:
        for change in effect["changes"]:
            if (
                change["path"] == "combat.ac.unarmored_base"
                and change["mode"] == "override"
                and not isinstance(change["value"], bool)
                and isinstance(change["value"], int)
            ):
                unarmored_formulas.append(
                    {
                        "base": int(change["value"]),
                        "ability": None,
                        "allows_shield": True,
                        "effect_id": effect["id"],
                        "effect_name": effect["name"],
                        "path": change["path"],
                    }
                )
                valid_unarmored_changes.add((effect["id"], change["path"]))
            elif (
                change["path"] == "combat.ac.unarmored_formula"
                and change["mode"] == "override"
                and isinstance(change["value"], dict)
            ):
                formula = dict(change["value"])
                ability = str(formula.get("ability") or "").casefold()
                base = formula.get("base")
                allows_shield = formula.get("allows_shield")
                if (
                    set(formula) == {"base", "ability", "allows_shield"}
                    and not isinstance(base, bool)
                    and isinstance(base, int)
                    and base >= 0
                    and ability in ability_modifiers
                    and isinstance(allows_shield, bool)
                ):
                    unarmored_formulas.append(
                        {
                            "base": base,
                            "ability": ability,
                            "allows_shield": allows_shield,
                            "effect_id": effect["id"],
                            "effect_name": effect["name"],
                            "path": change["path"],
                        }
                    )
                    valid_unarmored_changes.add((effect["id"], change["path"]))

    armor_id = inventory["equipment_slots"]["armor"]
    total = breakdown["base"]
    shield_bonus = 0
    if override is None:
        if armor_id:
            armor = items[armor_id]
            mechanics = armor["mechanics"]
            magic_bonus = mechanics["magic_bonus"] if armor.get("attunement") != "required" else 0
            dexterity_modifier = ability_modifiers["dexterity"]
            dexterity_mode = mechanics["dexterity_mode"]
            if dexterity_mode == "none":
                dexterity_bonus = 0
            elif dexterity_mode == "full":
                dexterity_bonus = dexterity_modifier
            else:
                dexterity_bonus = min(dexterity_modifier, mechanics["dexterity_max"])
            total = mechanics["base_ac"] + dexterity_bonus + magic_bonus
            breakdown["mode"] = "armor"
            breakdown["base"] = mechanics["base_ac"]
            breakdown["armor"] = {
                "item_id": armor_id,
                "name": armor["name"],
                "dexterity_bonus": dexterity_bonus,
                "magic_bonus": magic_bonus,
                "magic_suppressed_by_attunement": (
                    armor.get("attunement") == "required" and mechanics["magic_bonus"] != 0
                ),
                "stealth_disadvantage": mechanics["stealth_disadvantage"],
            }
        elif ac["base"] == 10:
            dexterity_bonus = ability_modifiers["dexterity"]
            total += dexterity_bonus
            breakdown["mode"] = "unarmored"
            breakdown["dexterity_bonus"] = dexterity_bonus
        shield_id = inventory["equipment_slots"]["shield"]
        if shield_id:
            shield = items[shield_id]
            mechanics = shield["mechanics"]
            shield_magic_bonus = (
                mechanics["magic_bonus"] if shield.get("attunement") != "required" else 0
            )
            shield_bonus = mechanics["ac_bonus"] + shield_magic_bonus
            total += shield_bonus
            breakdown["shield"] = {
                "item_id": shield_id,
                "name": shield["name"],
                "bonus": shield_bonus,
                "magic_bonus": shield_magic_bonus,
                "magic_suppressed_by_attunement": (
                    shield.get("attunement") == "required" and mechanics["magic_bonus"] != 0
                ),
            }

    selected_unarmored_change: tuple[str, str] | None = None
    shield_id = inventory["equipment_slots"]["shield"]
    candidates = []
    if not armor_id:
        for formula in unarmored_formulas:
            if shield_id and not formula["allows_shield"]:
                continue
            dexterity_bonus = ability_modifiers["dexterity"]
            ability = formula["ability"]
            ability_bonus = ability_modifiers[ability] if ability else 0
            candidate_total = (
                formula["base"]
                + dexterity_bonus
                + ability_bonus
                + (shield_bonus if formula["allows_shield"] else 0)
            )
            candidates.append(
                (
                    candidate_total,
                    formula,
                    dexterity_bonus,
                    ability_bonus,
                )
            )
    if candidates:
        unarmored_total, formula, dexterity_bonus, ability_bonus = max(
            candidates,
            key=lambda candidate: candidate[0],
        )
        if override is None or unarmored_total > total:
            total = unarmored_total
            selected_unarmored_change = (formula["effect_id"], formula["path"])
            breakdown["mode"] = (
                "mage_armor"
                if formula["effect_name"].casefold() == "mage armor"
                else "unarmored_formula"
            )
            breakdown["base"] = formula["base"]
            breakdown["dexterity_bonus"] = dexterity_bonus
            if formula["ability"]:
                breakdown["ability_bonus"] = {
                    "ability": formula["ability"],
                    "bonus": ability_bonus,
                }

    # A statblock AC override is the creature's printed AC calculation. Explicit
    # equipped magic-item bonuses still modify that calculation, just as active
    # effects do below. Keeping these bonuses outside the override branch lets a
    # source-bound item such as the Staff of Defense work on imported NPCs.
    for item in inventory["items"]:
        if item["kind"] != "magic_item" or not item["equipped"]:
            continue
        if item.get("attunement") == "required":
            continue
        bonus = item["mechanics"].get("ac_bonus", 0)
        if bonus:
            total += bonus
            breakdown["magic_items"].append(
                {"item_id": item["id"], "name": item["name"], "bonus": bonus}
            )

    unresolved_effects: set[str] = set()
    for effect in active_effects:
        for change in effect["changes"]:
            if (
                effect["kind"] == "timed_conditions"
                and change["path"] == "conditions"
                and change["mode"] == "add"
            ):
                continue
            if (
                re.fullmatch(r"abilities\.[a-z_]+\.score", change["path"])
                and change["mode"] == "override"
                and not isinstance(change["value"], bool)
                and isinstance(change["value"], int)
                and 0 <= change["value"] <= 30
            ):
                continue
            if change["path"] in {
                "combat.ac.unarmored_base",
                "combat.ac.unarmored_formula",
            }:
                change_key = (effect["id"], change["path"])
                if change_key in valid_unarmored_changes:
                    breakdown["effects"].append(
                        {
                            "effect_id": effect["id"],
                            "name": effect["name"],
                            "mode": change["mode"],
                            "value": change["value"],
                            "applied": change_key == selected_unarmored_change,
                        }
                    )
                else:
                    unresolved_effects.add(effect["id"])
                continue
            if change["path"] not in {"derived.armor_class", "combat.ac"}:
                unresolved_effects.add(effect["id"])
                continue
            if (
                change["mode"] not in {"add", "override"}
                or isinstance(change["value"], bool)
                or not isinstance(change["value"], int)
            ):
                unresolved_effects.add(effect["id"])
                continue
            if change["mode"] == "add":
                total += change["value"]
            else:
                total = change["value"]
            breakdown["effects"].append(
                {
                    "effect_id": effect["id"],
                    "name": effect["name"],
                    "mode": change["mode"],
                    "value": change["value"],
                }
            )
    breakdown["total"] = total
    return total, breakdown, unresolved_effects


def _inventory_weight_oz(inventory: dict[str, Any]) -> float:
    """Return carried weight after extra-dimensional container exceptions."""
    items = {item["id"]: item for item in inventory["items"]}

    def weight(item_id: str) -> float:
        item = items[item_id]
        own_weight = item["weight_oz"] * item["quantity"]
        contents = sum(
            weight(child["id"]) for child in items.values() if child["container_id"] == item_id
        )
        if item["kind"] == "container" and item["mechanics"]["weightless_contents"]:
            contents = 0
        return own_weight + contents

    total = sum(weight(item_id) for item_id, item in items.items() if item["container_id"] is None)
    if not inventory["encumbrance"]["ignore_currency_weight"]:
        total += sum(inventory["wallet"].values()) * 0.32  # 50 coins per pound.
    return total


def _weapon_attacks(
    inventory: dict[str, Any],
    ability_modifiers: dict[str, int],
    proficiency: int,
    spell_ability: str | None,
) -> list[dict[str, Any]]:
    attacks = []
    for item in inventory["items"]:
        if item["kind"] != "weapon" or not (
            item["equipped"] or item["mechanics"].get("always_available", False)
        ):
            continue
        mechanics = item["mechanics"]
        magic_properties_active = item.get("attunement") != "required"
        magic_bonus = mechanics["magic_bonus"] if magic_properties_active else 0
        ability = mechanics["attack_ability"]
        modifier = (
            ability_modifiers.get(spell_ability or "", 0)
            if ability == "spell"
            else ability_modifiers.get(ability, 0)
        )
        attack_bonus = mechanics.get("attack_bonus_override")
        if attack_bonus is None:
            attack_bonus = modifier + magic_bonus
            if mechanics["proficient"]:
                attack_bonus += proficiency
        damage_bonus = mechanics.get("damage_bonus_override")
        if damage_bonus is None:
            damage_bonus = modifier + magic_bonus
        damage_formula = mechanics["damage_formula"]
        damage_expression = damage_formula
        if damage_formula and damage_bonus:
            damage_expression = (
                f"{damage_formula} {'+' if damage_bonus > 0 else '-'} {abs(damage_bonus)}"
            )
        attacks.append(
            {
                "item_id": item["id"],
                "name": item["name"],
                "equipped_slot": item["equipped_slot"],
                "attack_type": mechanics["attack_type"],
                "reach_ft": mechanics.get("reach_ft", 5),
                "attack_ability": ability,
                "attack_bonus": attack_bonus,
                "damage_formula": damage_formula,
                "damage_bonus": damage_bonus,
                "damage_expression": damage_expression,
                "damage_type": mechanics["damage_type"],
                "additional_damage": [
                    {
                        **part,
                        "damage_expression": (
                            f"{part['damage_formula']} "
                            f"{'+' if part['damage_bonus'] > 0 else '-'} "
                            f"{abs(part['damage_bonus'])}"
                            if part["damage_bonus"]
                            else part["damage_formula"]
                        ),
                    }
                    for part in (mechanics["additional_damage"] if magic_properties_active else [])
                ],
                "on_hit_effect": (mechanics["on_hit_effect"] if magic_properties_active else ""),
                "magic_bonus": magic_bonus,
                "magic_suppressed_by_attunement": (
                    not magic_properties_active
                    and (
                        mechanics["magic_bonus"] != 0
                        or bool(mechanics["additional_damage"])
                        or bool(mechanics["on_hit_effect"])
                    )
                ),
                "versatile_damage_formula": mechanics["versatile_damage_formula"],
                "properties": mechanics["properties"],
                "range_ft": {
                    "normal": mechanics["normal_range_ft"],
                    "long": mechanics["long_range_ft"],
                },
                "thrown_range_ft": {
                    "normal": mechanics["thrown_normal_range_ft"],
                    "long": mechanics["thrown_long_range_ft"],
                },
                "ammunition_item_id": mechanics["ammunition_item_id"],
                "required_target_sizes": list(mechanics["required_target_sizes"]),
                "requires_attack_advantage": mechanics["requires_attack_advantage"],
            }
        )
    return attacks


def effective_hit_point_maximum(sheet: dict[str, Any]) -> int:
    """Return the rules-effective maximum without overwriting the recorded base maximum."""
    combat = dict(sheet.get("combat") or {})
    hit_points = dict(combat.get("hp") or {})
    return effective_hit_point_maximum_value(
        edition=str(sheet.get("edition") or DEFAULT_CHARACTER_EDITION),
        base_maximum=int(hit_points.get("max", 0) or 0),
        exhaustion=int(combat.get("exhaustion", 0) or 0),
    )


def effective_ability_scores(sheet: dict[str, Any]) -> dict[str, int]:
    """Return ability scores after narrow, source-owned override effects."""

    value = validate_character_sheet(sheet)
    scores = {ability: int(entry["score"]) for ability, entry in value["abilities"].items()}
    for effect in value["effects"]:
        if not effect["active"]:
            continue
        for change in effect["changes"]:
            match = re.fullmatch(r"abilities\.([a-z_]+)\.score", change["path"])
            if match is None:
                continue
            ability = match.group(1)
            score = change["value"]
            if (
                ability not in scores
                or change["mode"] != "override"
                or isinstance(score, bool)
                or not isinstance(score, int)
                or not 0 <= score <= 30
            ):
                raise ValueError("active ability-score override effect is malformed")
            scores[ability] = score
    return scores


def effective_ability_modifier(sheet: dict[str, Any], ability: str) -> int:
    """Return one modifier from the same effective ability-score projection."""

    normalized = str(ability).strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized not in ABILITY_NAMES:
        raise ValueError(f"unsupported ability: {ability}")
    return ability_modifier(effective_ability_scores(sheet)[normalized])


def derive_character_sheet(
    sheet: dict[str, Any], *, rules: ResolutionContext | None = None
) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    level = value["progression"]["level"]
    proficiency = proficiency_bonus(level)
    active_effects = [effect for effect in value["effects"] if effect["active"]]
    ability_scores = effective_ability_scores(value)
    ability_modifiers = {
        ability: ability_modifier(score) for ability, score in ability_scores.items()
    }
    saves = {
        ability: ability_modifiers[ability]
        + entry["bonus"]
        + (proficiency if entry["save_proficient"] else 0)
        for ability, entry in value["abilities"].items()
    }
    multipliers = {"none": 0, "half": 0.5, "proficient": 1, "expertise": 2}
    skills = {
        skill: ability_modifiers[SKILL_ABILITIES[skill]]
        + entry["bonus"]
        + int(proficiency * multipliers[entry["proficiency"]])
        for skill, entry in value["skills"].items()
    }
    inventory = value["inventory"]
    total_weight = _inventory_weight_oz(inventory)
    wallet_cp = sum(
        inventory["wallet"][name] * multiplier
        for name, multiplier in DENOMINATION_CP_VALUES.items()
    )
    spell_ability = value["spellcasting"]["ability"]
    spell_attack_bonus_override = value["spellcasting"]["attack_bonus_override"]
    spell_save_dc_override = value["spellcasting"]["save_dc_override"]
    armor_class, armor_class_breakdown, unresolved_effects = _derive_armor_class(
        value, ability_modifiers, active_effects
    )
    equipped_armor_id = inventory["equipment_slots"]["armor"]
    equipped_armor = next(
        (item for item in inventory["items"] if item["id"] == equipped_armor_id),
        None,
    )
    stealth_disadvantage = bool(
        equipped_armor
        and dict(equipped_armor.get("mechanics") or {}).get("stealth_disadvantage", False)
    )
    size_multiplier = {
        "tiny": 0.5,
        "small": 1,
        "medium": 1,
        "large": 2,
        "huge": 4,
        "gargantuan": 8,
    }.get(value["traits"]["size"].lower(), 1)
    strength = ability_scores["strength"]
    maximum_weight = strength * 240 * size_multiplier
    encumbrance = inventory["encumbrance"]
    encumbrance_summary = {
        "mode": encumbrance["mode"],
        "carried_weight_oz": total_weight,
        "light_threshold_oz": strength * 80 * size_multiplier,
        "heavy_threshold_oz": strength * 160 * size_multiplier,
        "maximum_oz": maximum_weight,
        "state": (
            "over_capacity"
            if total_weight > maximum_weight
            else "heavily_encumbered"
            if encumbrance["mode"] == "variant" and total_weight > strength * 160 * size_multiplier
            else "encumbered"
            if encumbrance["mode"] == "variant" and total_weight > strength * 80 * size_multiplier
            else "normal"
        ),
    }
    multiattack_options = []
    for activity in value["content"]["activities"]:
        if (
            not is_multiattack_activity(activity)
            or str(dict(activity.get("activation") or {}).get("type") or "") != "action"
        ):
            continue
        options = dict(activity.get("choices") or {}).get("multiattack_options")
        if isinstance(options, list):
            multiattack_options.extend(copy.deepcopy(options))
    effective_hp_max = effective_hit_point_maximum(value)
    derived = {
        "proficiency_bonus": proficiency,
        "ability_scores": ability_scores,
        "ability_modifiers": ability_modifiers,
        "saving_throws": saves,
        "skills": skills,
        "passive_perception": 10
        + skills["perception"]
        + value["traits"]["senses"]["passive_perception_bonus"],
        "armor_class": armor_class,
        "armor_class_breakdown": armor_class_breakdown,
        "stealth_disadvantage": stealth_disadvantage,
        "initiative": ability_modifiers[value["combat"]["initiative"]["ability"]]
        + value["combat"]["initiative"]["bonus"],
        "attacks_per_action": value["combat"]["attacks_per_action"],
        "multiattack_options": multiattack_options,
        "hit_points": {
            **dict(value["combat"]["hp"]),
            "value": min(int(value["combat"]["hp"]["value"]), effective_hp_max),
            "max": effective_hp_max,
            "base_max": int(value["combat"]["hp"]["max"]),
        },
        "hit_point_progression": {
            "gains": list(value["combat"]["hp_progression"]),
            "recorded_gain_total": sum(gain["value"] for gain in value["combat"]["hp_progression"]),
        },
        "speed": dict(value["combat"]["speed"]),
        "spellcasting": (
            {
                "ability": spell_ability,
                "attack_bonus": (
                    spell_attack_bonus_override
                    if spell_attack_bonus_override is not None
                    else ability_modifiers[spell_ability] + proficiency
                ),
                "save_dc": (
                    spell_save_dc_override
                    if spell_save_dc_override is not None
                    else 8 + ability_modifiers[spell_ability] + proficiency
                ),
                "prepared_spell_ids": [
                    spell["id"]
                    for spell in value["content"]["spells"]
                    if spell["access"]["prepared"]
                ],
            }
            if spell_ability
            else None
        ),
        "inventory": {
            "total_weight_oz": total_weight,
            "wallet_value_cp": wallet_cp,
            "encumbrance": encumbrance_summary,
            "weapon_attacks": _weapon_attacks(
                inventory, ability_modifiers, proficiency, spell_ability
            ),
        },
        "active_effects": [
            {"id": effect["id"], "name": effect["name"]} for effect in active_effects
        ],
        "unresolved_rules": sorted(unresolved_effects),
        "ruling_requirements": [],
    }
    extension = apply_rule_event(value, "character.derive", rules)
    if extension.status != "committed":
        derived["unresolved_rules"] = sorted(
            {
                *derived["unresolved_rules"],
                *(item["mechanic_id"] for item in extension.pending),
            }
        )
        derived["ruling_requirements"] = [
            {
                "mechanic_id": str(item.get("mechanic_id") or ""),
                "reason": str(item.get("id") or "declarative rule adjudication"),
                "default_resolver": str(item.get("default_resolver") or "agent"),
                "ruling_kind": str(item.get("ruling_kind") or "agent_dm_adjudication"),
            }
            for item in extension.pending
        ]
    for modifier in extension.modifiers:
        if modifier["op"] != "modifier.add":
            continue
        target = str(modifier.get("target") or "")
        if target in DERIVED_STAT_MODIFIER_TARGETS:
            derived[target] = int(derived[target]) + int(modifier.get("value", 0) or 0)
        else:
            derived["unresolved_rules"] = sorted(
                {*derived["unresolved_rules"], modifier["mechanic_id"]}
            )
    core_boundary_ids: list[str] = []
    if derived["armor_class_breakdown"].get("mode") == "unarmored":
        core_boundary_ids.append("dnd5e.core.armor_class.unarmored")
    if any(
        int(item.get("reach_ft", 5) or 5) > 5 for item in derived["inventory"]["weapon_attacks"]
    ):
        core_boundary_ids.append("dnd5e.core.weapon.reach")
    derived["rule_receipts"] = [
        *core_receipts(rules, core_boundary_ids, "character.derive"),
        *extension.receipts,
    ]
    derived["ruleset_fingerprint"] = rules.fingerprint if rules else ""
    return derived


def add_inventory_item(sheet: dict[str, Any], item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = validate_character_sheet(sheet)
    entry = _normalize_item(item, "item")
    if any(current["id"] == entry["id"] for current in value["inventory"]["items"]):
        raise ValueError("item id already exists in inventory")
    value["inventory"]["items"].append(entry)
    return validate_character_sheet(value), entry["id"]


def update_inventory_item(
    sheet: dict[str, Any], item_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    item = next((entry for entry in value["inventory"]["items"] if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    replacement = {**item, **_object(patch, "item patch"), "id": item_id}
    replacement = _normalize_item(replacement, "item", generate_id=False)
    index = value["inventory"]["items"].index(item)
    value["inventory"]["items"][index] = replacement
    return validate_character_sheet(value)


def attune_inventory_item(sheet: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Complete one source-required item attunement after its rest gate."""
    value = validate_character_sheet(sheet)
    item = next(
        (entry for entry in value["inventory"]["items"] if entry["id"] == item_id),
        None,
    )
    if item is None:
        raise LookupError(item_id)
    if item["attunement"] == "none":
        raise ValueError("item does not require attunement")
    if item["attunement"] == "attuned":
        raise ValueError("item is already attuned")
    attuned = [entry for entry in value["inventory"]["items"] if entry["attunement"] == "attuned"]
    if len(attuned) >= 3:
        raise ValueError("a character cannot be attuned to more than three magic items")
    identity = str(item.get("name") or item.get("source_key") or "").strip().casefold()
    if any(
        str(entry.get("name") or entry.get("source_key") or "").strip().casefold() == identity
        for entry in attuned
    ):
        raise ValueError("a character cannot attune to more than one copy of an item")
    item["attunement"] = "attuned"
    return validate_character_sheet(value)


def remove_inventory_item(
    sheet: dict[str, Any], item_id: str, quantity: int | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = validate_character_sheet(sheet)
    items = value["inventory"]["items"]
    item = next((entry for entry in items if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    count = quantity if quantity is not None else item["quantity"]
    count = _integer(count, "quantity", minimum=1)
    if count > item["quantity"]:
        raise ValueError("quantity exceeds the item stack")
    moved = copy.deepcopy(item)
    moved["quantity"] = count
    if count == item["quantity"]:
        if any(entry["container_id"] == item_id for entry in items):
            raise ValueError("cannot remove a container while it still has contents")
        items.remove(item)
        for slot, equipped_id in value["inventory"]["equipment_slots"].items():
            if equipped_id == item_id:
                value["inventory"]["equipment_slots"][slot] = None
    else:
        item["quantity"] -= count
        moved["id"] = _uuid()
    return validate_character_sheet(value), moved


def consume_weapon_ammunition(
    sheet: dict[str, Any],
    weapon_id: str,
    quantity: int = 1,
    *,
    ammunition_item_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume linked ammunition or one explicitly selected compatible stack."""
    value = validate_character_sheet(sheet)
    weapon = next((item for item in value["inventory"]["items"] if item["id"] == weapon_id), None)
    if weapon is None or weapon["kind"] != "weapon":
        raise ValueError("weapon_id must reference a weapon in inventory")
    selected_ammunition_id = (
        str(ammunition_item_id).strip()
        if ammunition_item_id is not None
        else weapon["mechanics"]["ammunition_item_id"]
    )
    if not selected_ammunition_id:
        raise ValueError("weapon has no linked ammunition")
    if ammunition_item_id is not None and "ammunition" not in {
        str(item).strip().casefold()
        for item in weapon["mechanics"].get("properties", [])
    }:
        raise ValueError("weapon cannot use selected ammunition")
    count = _integer(quantity, "quantity", minimum=1)
    ammunition = next(
        (item for item in value["inventory"]["items"] if item["id"] == selected_ammunition_id),
        None,
    )
    if ammunition is None or ammunition["kind"] != "ammunition":
        raise ValueError("weapon ammunition is not present in inventory")
    if count > int(ammunition["quantity"]):
        raise ValueError("not enough weapon ammunition remains")
    ammunition["quantity"] = int(ammunition["quantity"]) - count
    return validate_character_sheet(value), {
        "item_id": selected_ammunition_id,
        "name": ammunition["name"],
        "quantity": count,
        "remaining": ammunition["quantity"],
    }


def receive_inventory_item(sheet: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    entry = _normalize_item(item, "item", generate_id=False)
    if entry["attunement"] == "attuned":
        raise ValueError(
            "an attuned item cannot be transferred until its attunement "
            "ends under a source-defined condition"
        )
    if any(current["id"] == entry["id"] for current in value["inventory"]["items"]):
        entry["id"] = _uuid()
    entry["container_id"] = None
    entry["equipped"] = False
    entry["equipped_slot"] = None
    value["inventory"]["items"].append(entry)
    return validate_character_sheet(value)


def adjust_wallet(sheet: dict[str, Any], denomination: str, amount: int) -> dict[str, Any]:
    if denomination not in DENOMINATIONS:
        raise ValueError("denomination must be cp, sp, ep, gp, or pp")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        raise ValueError("amount must be a non-zero integer")
    value = validate_character_sheet(sheet)
    wallet = value["inventory"]["wallet"]
    if wallet[denomination] + amount < 0:
        raise ValueError("wallet balance cannot be negative")
    wallet[denomination] += amount
    return validate_character_sheet(value)


def equip_inventory_item(sheet: dict[str, Any], item_id: str, slot: str | None) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    item = next((entry for entry in value["inventory"]["items"] if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    if slot is not None and slot not in EQUIPMENT_SLOTS:
        raise ValueError("invalid equipment slot")
    if slot is not None:
        _validate_item_slot(item, slot)
    for key, current_id in value["inventory"]["equipment_slots"].items():
        if current_id == item_id:
            value["inventory"]["equipment_slots"][key] = None
    item["equipped"] = slot is not None
    item["equipped_slot"] = slot
    if slot is not None:
        previous_id = value["inventory"]["equipment_slots"][slot]
        if previous_id:
            previous = next(
                entry for entry in value["inventory"]["items"] if entry["id"] == previous_id
            )
            previous["equipped"] = False
            previous["equipped_slot"] = None
        value["inventory"]["equipment_slots"][slot] = item_id
    return validate_character_sheet(value)


def add_effect(sheet: dict[str, Any], effect: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = validate_character_sheet(sheet)
    entry = _normalize_effect(effect, "effect")
    if any(current["id"] == entry["id"] for current in value["effects"]):
        raise ValueError("effect id already exists")
    value["effects"].append(entry)
    apply_effect_conditions(value, entry)
    return validate_character_sheet(value), entry["id"]


def remove_effect(sheet: dict[str, Any], effect_id: str) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    effects = value["effects"]
    effect = next((entry for entry in effects if entry["id"] == effect_id), None)
    if effect is None:
        raise LookupError(effect_id)
    effects.remove(effect)
    reconcile_ended_effect_conditions(value, ended_effects=[effect])
    return validate_character_sheet(value)


def set_spell_prepared(sheet: dict[str, Any], spell_id: str, prepared: bool) -> dict[str, Any]:
    """Set one preparation during card setup; live rest changes use an atomic full list."""
    value = validate_character_sheet(sheet)
    preparation = value["spellcasting"]["preparation"]
    if preparation["mode"] not in PREPARED_SELECTION_MODES:
        raise ValueError("this character does not prepare spells")
    spell = next((entry for entry in value["content"]["spells"] if entry["id"] == spell_id), None)
    if spell is None:
        raise LookupError(spell_id)
    if spell["level"] == 0:
        raise ValueError("cantrips are known, not selected as prepared level 1+ spells")
    if spell["access"]["always_prepared"]:
        raise ValueError("always-prepared spells are not part of the selected list")
    if preparation["mode"] == "spellbook" and not spell["access"]["in_spellbook"]:
        raise ValueError("a spellbook caster can prepare only spells in the spellbook")
    selected = preparation["selected_spell_ids"]
    if prepared:
        if spell_id not in selected:
            if len(selected) >= preparation["max_prepared"]:
                raise ValueError("prepared spell selection exceeds max_prepared")
            selected.append(spell_id)
    elif spell_id in selected:
        selected.remove(spell_id)
    return validate_character_sheet(value)


def set_resource_value(sheet: dict[str, Any], key: str, value: int) -> dict[str, Any]:
    result = validate_character_sheet(sheet)
    resource = result["resources"].get(key)
    if resource is None:
        raise LookupError(key)
    resource["value"] = _integer(value, "resource value", minimum=0)
    if resource["value"] > resource["max"]:
        raise ValueError("resource value cannot exceed max")
    return validate_character_sheet(result)


def set_exhaustion_level(sheet: dict[str, Any], value: int) -> dict[str, Any]:
    """Set the rules-visible exhaustion level through the validated sheet contract."""
    result = validate_character_sheet(sheet)
    level = _integer(
        value,
        "exhaustion level",
        minimum=0,
        maximum=6,
    )
    result["combat"]["exhaustion"] = level
    hit_points = result["combat"]["hp"]
    hit_points["value"] = min(
        int(hit_points["value"]),
        effective_hit_point_maximum(result),
    )
    if level >= 6 and "dead" not in condition_ids(result["conditions"]):
        result["conditions"].append("dead")
    return validate_character_sheet(result)


def legacy_memory_candidates(
    notes: dict[str, Any],
    *,
    actor_id: str,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """Convert embedded v2 memories into explicit ActorKnowledge add payloads."""
    normalized = validate_character_notes(notes)
    candidates = []
    for memory in normalized["memories"]:
        if not include_inactive and memory["status"] != "active":
            continue
        candidates.append(
            {
                "action": "add",
                "actor_id": actor_id,
                "knowledge_key": f"legacy-memory:{memory['id']}",
                "subject_ref": "",
                "proposition": memory["summary"],
                "epistemic_status": "known",
                "confidence": memory["importance"],
                "source_event_id": memory.get("source_event_id") or None,
                "cause": "legacy_character_note",
                "disclosure_scope": memory["visibility"],
                "legacy_memory": {
                    "id": memory["id"],
                    "kind": memory["kind"],
                    "participants": list(memory["participants"]),
                    "status": memory["status"],
                },
            }
        )
    return candidates
