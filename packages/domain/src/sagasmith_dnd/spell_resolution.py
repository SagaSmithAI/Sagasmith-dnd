"""Strict, portable combat-resolution contracts for source-bound spell cards."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.abilities import ABILITY_IDS
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_FLY_MECHANIC_ID,
    CORE_FLY_SPELL_IDS,
    CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
    CORE_INVISIBILITY_MECHANIC_ID,
    CORE_WITCH_BOLT_MECHANIC_ID,
)
from sagasmith_dnd.vocabulary import ATTACK_MODES

SPELL_RESOLUTION_MECHANIC_ID = "dnd5e.core.spell.structured_resolution"
ENGINE_SETTLED_SPELL_MECHANIC_IDS = frozenset(
    {
        "dnd5e.core.spell.mage_armor",
        "dnd5e.core.spell.magic_missile",
        "dnd5e.core.spell.raise_dead",
        "dnd5e.core.spell.shield",
        CORE_BLADE_WARD_MECHANIC_ID,
        CORE_FLY_MECHANIC_ID,
        CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
        CORE_INVISIBILITY_MECHANIC_ID,
        CORE_WITCH_BOLT_MECHANIC_ID,
    }
)
SPELL_RESOLUTION_PATHS = frozenset(
    {
        "agent_ruling",
        "engine_mechanic",
        "incomplete",
        "semantic_plan",
        "structured_resolution",
    }
)
STANDARD_SPELL_ON_HIT_MECHANICS = frozenset(
    {
        "next_attack_advantage_until_source_turn_end",
        "healing_prevention_until_source_turn_start",
        "healing_prevention_until_source_turn_end",
        "undead_attack_disadvantage_against_source_until_source_turn_start",
    }
)

_DICE = re.compile(r"(?i)^([1-9]\d*)d([1-9]\d*)$")
_SRD2014_SPELL_PREFIX = "dnd5e.content.srd2014.spell."
_SRD2024_SPELL_PREFIX = "dnd5e.content.srd2024.spell."


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _reject_unknown(value: dict[str, Any], field: str, allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {sorted(unknown)}")


def _integer(
    value: Any,
    field: str,
    *,
    default: int = 0,
    minimum: int = 0,
    maximum: int = 999,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _optional_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _integer(value, field, minimum=minimum, maximum=maximum)


def _boolean(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _text(value: Any, field: str, *, default: str = "", maximum: int = 1200) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    result = value.strip()
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return result


def _dice(value: Any, field: str, *, required: bool = False) -> str:
    result = _text(value, field).replace(" ", "").casefold()
    if required and not result:
        raise ValueError(f"{field} is required")
    if result and _DICE.fullmatch(result) is None:
        raise ValueError(f"{field} must be an NdM dice expression")
    return result


def _normalize_roll(value: Any, field: str, *, damage: bool) -> dict[str, Any]:
    roll = _object(value, field)
    allowed = {
        "base_dice",
        "per_slot_dice",
        "slot_base_level",
        "cantrip_dice",
    }
    if damage:
        allowed.add("damage_type")
    _reject_unknown(roll, field, allowed)
    cantrip_dice = _object(roll.get("cantrip_dice") or {}, f"{field}.cantrip_dice")
    normalized_cantrip: dict[str, str] = {}
    for raw_level, expression in cantrip_dice.items():
        level = str(raw_level)
        if level not in {"1", "5", "11", "17"}:
            raise ValueError(f"{field}.cantrip_dice supports levels 1, 5, 11, and 17")
        normalized_cantrip[level] = _dice(
            expression, f"{field}.cantrip_dice.{level}", required=True
        )
    normalized: dict[str, Any] = {
        "base_dice": _dice(roll.get("base_dice"), f"{field}.base_dice", required=True),
        "per_slot_dice": _dice(roll.get("per_slot_dice"), f"{field}.per_slot_dice"),
        "slot_base_level": _integer(
            roll.get("slot_base_level"),
            f"{field}.slot_base_level",
            minimum=0,
            maximum=9,
        ),
        "cantrip_dice": normalized_cantrip,
    }
    if damage:
        normalized["damage_type"] = _text(
            roll.get("damage_type"), f"{field}.damage_type", maximum=100
        ).casefold()
        if not normalized["damage_type"]:
            raise ValueError(f"{field}.damage_type is required")
    if normalized["per_slot_dice"] and normalized["slot_base_level"] < 1:
        raise ValueError(f"{field}.slot_base_level is required with per_slot_dice")
    if normalized_cantrip and normalized["per_slot_dice"]:
        raise ValueError(f"{field} cannot combine cantrip and slot scaling")
    return normalized


def normalize_spell_resolution(value: Any, field: str = "spell.resolution") -> dict[str, Any]:
    """Normalize the executable subset of a source-bound spell card."""
    resolution = _object(value, field)
    _reject_unknown(resolution, field, {"kind", "targeting", "attack", "save", "healing"})
    kind = _text(resolution.get("kind"), f"{field}.kind", maximum=40).casefold()
    if kind not in {"healing", "spell_attack", "saving_throw"}:
        raise ValueError(f"{field}.kind is invalid")

    targeting = _object(resolution.get("targeting") or {}, f"{field}.targeting")
    _reject_unknown(
        targeting,
        f"{field}.targeting",
        {
            "mode",
            "requires_sight",
            "max_targets",
            "excluded_creature_types",
            "area",
        },
    )
    mode = _text(
        targeting.get("mode"), f"{field}.targeting.mode", default="creature", maximum=40
    ).casefold()
    if mode not in {"creature", "area"}:
        raise ValueError(f"{field}.targeting.mode is invalid")
    raw_types = targeting.get("excluded_creature_types") or []
    if not isinstance(raw_types, list) or any(not isinstance(item, str) for item in raw_types):
        raise ValueError(f"{field}.targeting.excluded_creature_types must be a text list")
    excluded_types = [item.strip().casefold() for item in raw_types]
    if any(not item for item in excluded_types) or len(excluded_types) != len(set(excluded_types)):
        raise ValueError(
            f"{field}.targeting.excluded_creature_types must contain unique non-empty values"
        )
    area = _object(targeting.get("area") or {}, f"{field}.targeting.area")
    _reject_unknown(
        area,
        f"{field}.targeting.area",
        {"shape", "radius_ft", "length_ft", "width_ft"},
    )
    normalized_area: dict[str, Any] | None = None
    if mode == "area":
        shape = _text(area.get("shape"), f"{field}.targeting.area.shape").casefold()
        if shape == "sphere":
            if area.get("length_ft") is not None or area.get("width_ft") is not None:
                raise ValueError(f"{field}.targeting.area sphere cannot define length or width")
            normalized_area = {
                "shape": shape,
                "radius_ft": _integer(
                    area.get("radius_ft"),
                    f"{field}.targeting.area.radius_ft",
                    minimum=5,
                    maximum=1000,
                ),
            }
        elif shape == "line":
            if area.get("radius_ft") is not None:
                raise ValueError(f"{field}.targeting.area line cannot define a radius")
            normalized_area = {
                "shape": shape,
                "length_ft": _integer(
                    area.get("length_ft"),
                    f"{field}.targeting.area.length_ft",
                    minimum=5,
                    maximum=1000,
                ),
                "width_ft": _integer(
                    area.get("width_ft"),
                    f"{field}.targeting.area.width_ft",
                    minimum=5,
                    maximum=1000,
                ),
            }
        else:
            raise ValueError(f"{field}.targeting.area.shape currently supports sphere or line")
    elif area:
        raise ValueError(f"{field}.targeting.area requires area mode")
    normalized_targeting = {
        "mode": mode,
        "requires_sight": _boolean(
            targeting.get("requires_sight"), f"{field}.targeting.requires_sight"
        ),
        "max_targets": _integer(
            targeting.get("max_targets"),
            f"{field}.targeting.max_targets",
            default=1,
            minimum=1,
            maximum=100,
        ),
        "excluded_creature_types": excluded_types,
        "area": normalized_area,
    }

    normalized: dict[str, Any] = {
        "kind": kind,
        "targeting": normalized_targeting,
        "attack": None,
        "save": None,
        "healing": None,
    }
    if kind == "spell_attack":
        if mode != "creature":
            raise ValueError(f"{field} spell attacks require creature targeting")
        attack = _object(resolution.get("attack") or {}, f"{field}.attack")
        _reject_unknown(
            attack,
            f"{field}.attack",
            {
                "mode",
                "count",
                "damage",
                "attack_bonus_override",
                "range_ft_override",
                "on_hit_ruling",
                "on_hit_mechanics",
            },
        )
        attack_mode = _text(attack.get("mode"), f"{field}.attack.mode").casefold()
        if attack_mode not in ATTACK_MODES:
            raise ValueError(f"{field}.attack.mode is invalid")
        count = _object(attack.get("count") or {}, f"{field}.attack.count")
        _reject_unknown(
            count,
            f"{field}.attack.count",
            {"base", "per_slot_above", "slot_base_level"},
        )
        normalized_count = {
            "base": _integer(
                count.get("base"), f"{field}.attack.count.base", default=1, minimum=1, maximum=100
            ),
            "per_slot_above": _integer(
                count.get("per_slot_above"),
                f"{field}.attack.count.per_slot_above",
                minimum=0,
                maximum=20,
            ),
            "slot_base_level": _integer(
                count.get("slot_base_level"),
                f"{field}.attack.count.slot_base_level",
                minimum=0,
                maximum=9,
            ),
        }
        if normalized_count["per_slot_above"] and normalized_count["slot_base_level"] < 1:
            raise ValueError(f"{field}.attack.count.slot_base_level is required")
        raw_on_hit_mechanics = attack.get("on_hit_mechanics") or []
        if not isinstance(raw_on_hit_mechanics, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_on_hit_mechanics
        ):
            raise ValueError(f"{field}.attack.on_hit_mechanics must be a string list")
        on_hit_mechanics = [item.strip() for item in raw_on_hit_mechanics]
        unknown_on_hit_mechanics = set(on_hit_mechanics) - STANDARD_SPELL_ON_HIT_MECHANICS
        if unknown_on_hit_mechanics:
            raise ValueError(
                f"{field}.attack.on_hit_mechanics contains unsupported standard mechanics: "
                f"{sorted(unknown_on_hit_mechanics)}"
            )
        if len(on_hit_mechanics) != len(set(on_hit_mechanics)):
            raise ValueError(f"{field}.attack.on_hit_mechanics must not contain duplicates")
        normalized["attack"] = {
            "mode": attack_mode,
            "count": normalized_count,
            "damage": _normalize_roll(
                attack.get("damage") or {}, f"{field}.attack.damage", damage=True
            ),
            "attack_bonus_override": _optional_integer(
                attack.get("attack_bonus_override"),
                f"{field}.attack.attack_bonus_override",
                minimum=-20,
                maximum=40,
            ),
            "range_ft_override": _optional_integer(
                attack.get("range_ft_override"),
                f"{field}.attack.range_ft_override",
                minimum=0,
                maximum=10000,
            ),
            "on_hit_ruling": _text(attack.get("on_hit_ruling"), f"{field}.attack.on_hit_ruling"),
            "on_hit_mechanics": on_hit_mechanics,
        }
    elif kind == "saving_throw":
        save = _object(resolution.get("save") or {}, f"{field}.save")
        _reject_unknown(
            save,
            f"{field}.save",
            {
                "ability",
                "success",
                "damage",
                "save_dc_override",
                "ignores_cover",
                "on_failed_save_ruling",
            },
        )
        ability = _text(save.get("ability"), f"{field}.save.ability").casefold()
        if ability not in ABILITY_IDS:
            raise ValueError(f"{field}.save.ability is invalid")
        success = _text(save.get("success"), f"{field}.save.success").casefold()
        if success not in {"half", "none"}:
            raise ValueError(f"{field}.save.success must be half or none")
        normalized["save"] = {
            "ability": ability,
            "success": success,
            "damage": _normalize_roll(
                save.get("damage") or {}, f"{field}.save.damage", damage=True
            ),
            "save_dc_override": _optional_integer(
                save.get("save_dc_override"),
                f"{field}.save.save_dc_override",
                minimum=0,
                maximum=99,
            ),
            "ignores_cover": _boolean(save.get("ignores_cover"), f"{field}.save.ignores_cover"),
            "on_failed_save_ruling": _text(
                save.get("on_failed_save_ruling"),
                f"{field}.save.on_failed_save_ruling",
            ),
        }
    else:
        if mode != "creature":
            raise ValueError(f"{field} healing requires creature targeting")
        healing = _object(resolution.get("healing") or {}, f"{field}.healing")
        _reject_unknown(
            healing,
            f"{field}.healing",
            {
                "base_dice",
                "per_slot_dice",
                "slot_base_level",
                "cantrip_dice",
                "add_spellcasting_modifier",
            },
        )
        normalized["healing"] = {
            **_normalize_roll(
                {key: item for key, item in healing.items() if key != "add_spellcasting_modifier"},
                f"{field}.healing",
                damage=False,
            ),
            "add_spellcasting_modifier": _boolean(
                healing.get("add_spellcasting_modifier"),
                f"{field}.healing.add_spellcasting_modifier",
            ),
        }

    for key in {"attack", "save", "healing"} - {
        "attack" if kind == "spell_attack" else "save" if kind == "saving_throw" else "healing"
    }:
        if resolution.get(key) is not None:
            raise ValueError(f"{field}.{key} does not apply to {kind}")
    return normalized


def scaled_roll_expression(
    roll: dict[str, Any],
    *,
    cast_level: int,
    actor_level: int,
    flat_modifier: int = 0,
) -> str:
    """Build one trusted dice expression from normalized slot/cantrip scaling."""
    if isinstance(flat_modifier, bool) or not isinstance(flat_modifier, int):
        raise ValueError("flat_modifier must be an integer")
    cantrip = dict(roll.get("cantrip_dice") or {})
    if cantrip:
        eligible = [int(level) for level in cantrip if int(level) <= int(actor_level)]
        level = max(eligible or [1])
        expression = str(cantrip[str(level)])
    else:
        expressions = [str(roll["base_dice"])]
        per_slot = str(roll.get("per_slot_dice") or "")
        base_level = int(roll.get("slot_base_level", 0) or 0)
        if per_slot and int(cast_level) > base_level:
            count, sides = _DICE.fullmatch(per_slot).groups()  # type: ignore[union-attr]
            expressions.append(f"{int(count) * (int(cast_level) - base_level)}d{sides}")
        expression = " + ".join(expressions)
    if flat_modifier:
        operator = "+" if flat_modifier > 0 else "-"
        expression = f"{expression} {operator} {abs(flat_modifier)}"
    return expression


def spell_attack_count(resolution: dict[str, Any], *, cast_level: int) -> int:
    count = dict(dict(resolution.get("attack") or {}).get("count") or {})
    result = int(count.get("base", 1) or 1)
    base_level = int(count.get("slot_base_level", 0) or 0)
    if int(cast_level) > base_level:
        result += (int(cast_level) - base_level) * int(count.get("per_slot_above", 0) or 0)
    return result


def known_spell_resolution(name: str) -> dict[str, Any] | None:
    """Return the reviewed executable subset for selected bundled SRD spells."""
    key = ascii_slug(name)
    values: dict[str, dict[str, Any]] = {
        "healing-word": {
            "kind": "healing",
            "targeting": {
                "mode": "creature",
                "requires_sight": True,
                "max_targets": 1,
                "excluded_creature_types": ["undead", "construct"],
            },
            "healing": {
                "base_dice": "1d4",
                "per_slot_dice": "1d4",
                "slot_base_level": 1,
                "add_spellcasting_modifier": True,
            },
        },
        "cure-wounds": {
            "kind": "healing",
            "targeting": {
                "mode": "creature",
                "max_targets": 1,
                "excluded_creature_types": ["undead", "construct"],
            },
            "healing": {
                "base_dice": "1d8",
                "per_slot_dice": "1d8",
                "slot_base_level": 1,
                "add_spellcasting_modifier": True,
            },
        },
        "scorching-ray": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 100},
            "attack": {
                "mode": "ranged",
                "count": {"base": 3, "per_slot_above": 1, "slot_base_level": 2},
                "damage": {"base_dice": "2d6", "damage_type": "fire"},
            },
        },
        "guiding-bolt": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 1},
            "attack": {
                "mode": "ranged",
                "count": {"base": 1},
                "damage": {
                    "base_dice": "4d6",
                    "damage_type": "radiant",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 1,
                },
                "on_hit_mechanics": ["next_attack_advantage_until_source_turn_end"],
            },
        },
        "chill-touch": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 1},
            "attack": {
                "mode": "ranged",
                "count": {"base": 1},
                "damage": {
                    "base_dice": "1d8",
                    "damage_type": "necrotic",
                    "cantrip_dice": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"},
                },
                "on_hit_mechanics": [
                    "healing_prevention_until_source_turn_start",
                    "undead_attack_disadvantage_against_source_until_source_turn_start",
                ],
            },
        },
        "sacred-flame": {
            "kind": "saving_throw",
            "targeting": {"mode": "creature", "requires_sight": True, "max_targets": 1},
            "save": {
                "ability": "dexterity",
                "success": "none",
                "ignores_cover": True,
                "damage": {
                    "base_dice": "1d8",
                    "damage_type": "radiant",
                    "cantrip_dice": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"},
                },
            },
        },
        "fireball": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {"shape": "sphere", "radius_ft": 20},
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "fire",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 3,
                },
            },
        },
        "lightning-bolt": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {
                    "shape": "line",
                    "length_ft": 100,
                    "width_ft": 5,
                },
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "lightning",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 3,
                },
            },
        },
    }
    value = values.get(key)
    return normalize_spell_resolution(deepcopy(value)) if value is not None else None


def known_2024_spell_resolution(name: str) -> dict[str, Any] | None:
    """Return only resolutions re-reviewed against SRD 5.2.1 spell text.

    This is deliberately separate from the 2014 catalog.  Several spells kept
    their names while changing dice, range, attack mode, creature exclusions, or
    secondary effects.  Falling back by name across editions would therefore be
    a silent rules corruption rather than a harmless compatibility shortcut.
    """

    key = ascii_slug(name)
    values: dict[str, dict[str, Any]] = {
        "healing-word": {
            "kind": "healing",
            "targeting": {
                "mode": "creature",
                "requires_sight": True,
                "max_targets": 1,
            },
            "healing": {
                "base_dice": "2d4",
                "per_slot_dice": "2d4",
                "slot_base_level": 1,
                "add_spellcasting_modifier": True,
            },
        },
        "cure-wounds": {
            "kind": "healing",
            "targeting": {"mode": "creature", "max_targets": 1},
            "healing": {
                "base_dice": "2d8",
                "per_slot_dice": "2d8",
                "slot_base_level": 1,
                "add_spellcasting_modifier": True,
            },
        },
        "scorching-ray": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 100},
            "attack": {
                "mode": "ranged",
                "count": {"base": 3, "per_slot_above": 1, "slot_base_level": 2},
                "damage": {"base_dice": "2d6", "damage_type": "fire"},
            },
        },
        "guiding-bolt": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 1},
            "attack": {
                "mode": "ranged",
                "count": {"base": 1},
                "damage": {
                    "base_dice": "4d6",
                    "damage_type": "radiant",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 1,
                },
                "on_hit_mechanics": ["next_attack_advantage_until_source_turn_end"],
            },
        },
        "chill-touch": {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 1},
            "attack": {
                "mode": "melee",
                "count": {"base": 1},
                "damage": {
                    "base_dice": "1d10",
                    "damage_type": "necrotic",
                    "cantrip_dice": {
                        "1": "1d10",
                        "5": "2d10",
                        "11": "3d10",
                        "17": "4d10",
                    },
                },
                "on_hit_mechanics": ["healing_prevention_until_source_turn_end"],
            },
        },
        "sacred-flame": {
            "kind": "saving_throw",
            "targeting": {"mode": "creature", "requires_sight": True, "max_targets": 1},
            "save": {
                "ability": "dexterity",
                "success": "none",
                "ignores_cover": True,
                "damage": {
                    "base_dice": "1d8",
                    "damage_type": "radiant",
                    "cantrip_dice": {
                        "1": "1d8",
                        "5": "2d8",
                        "11": "3d8",
                        "17": "4d8",
                    },
                },
            },
        },
        "fireball": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {"shape": "sphere", "radius_ft": 20},
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "fire",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 3,
                },
            },
        },
        "lightning-bolt": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {"shape": "line", "length_ft": 100, "width_ft": 5},
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "lightning",
                    "per_slot_dice": "1d6",
                    "slot_base_level": 3,
                },
            },
        },
    }
    value = values.get(key)
    return normalize_spell_resolution(deepcopy(value)) if value is not None else None


def effective_spell_resolution(spell: dict[str, Any]) -> dict[str, Any] | None:
    """Return a stored contract or the built-in contract for one exact SRD spell id."""

    if not isinstance(spell, dict):
        raise ValueError("spell must be an object")
    stored = spell.get("resolution")
    if isinstance(stored, dict):
        return normalize_spell_resolution(deepcopy(stored))
    spell_id = str(spell.get("id") or "")
    if spell_id.startswith(_SRD2014_SPELL_PREFIX):
        return known_spell_resolution(spell_id.removeprefix(_SRD2014_SPELL_PREFIX))
    if spell_id.startswith(_SRD2024_SPELL_PREFIX):
        return known_2024_spell_resolution(spell_id.removeprefix(_SRD2024_SPELL_PREFIX))
    return None


def spell_resolution_path(spell: dict[str, Any]) -> str:
    """Classify the one explicit effect-settlement path recorded on a spell card."""

    if not isinstance(spell, dict):
        raise ValueError("spell must be an object")
    if isinstance(spell.get("resolution_plan"), dict):
        return "semantic_plan"
    if effective_spell_resolution(spell) is not None:
        return "structured_resolution"
    if str(spell.get("id") or "") in CORE_FLY_SPELL_IDS:
        return "engine_mechanic"
    mechanic_refs = {
        str(item).strip() for item in spell.get("mechanic_refs", []) if str(item).strip()
    }
    if mechanic_refs & ENGINE_SETTLED_SPELL_MECHANIC_IDS:
        return "engine_mechanic"
    requirements = spell.get("ruling_requirements")
    if isinstance(requirements, list) and any(
        isinstance(item, dict)
        and str(item.get("default_resolver") or "") == "agent"
        and str(item.get("ruling_kind") or "") == "generic_spell_effect"
        and bool(str(item.get("source_excerpt") or "").strip())
        for item in requirements
    ):
        return "agent_ruling"
    return "incomplete"


def audit_spell_resolution_paths(sheet: dict[str, Any]) -> dict[str, Any]:
    """Prove that every spell and cantrip retained on a card has one visible path."""

    if not isinstance(sheet, dict):
        raise ValueError("sheet must be an object")
    spells = list(dict(sheet.get("content") or {}).get("spells") or [])
    entries: list[dict[str, Any]] = []
    counts = {path: 0 for path in sorted(SPELL_RESOLUTION_PATHS)}
    for raw in spells:
        if not isinstance(raw, dict):
            raise ValueError("sheet content spells must be objects")
        path = spell_resolution_path(raw)
        counts[path] += 1
        access = dict(raw.get("access") or {})
        level = int(raw.get("level", 0) or 0)
        entries.append(
            {
                "spell_id": str(raw.get("id") or ""),
                "name": str(raw.get("name") or ""),
                "level": level,
                "is_cantrip": level == 0,
                "available": bool(
                    access.get("known")
                    or access.get("prepared")
                    or access.get("always_prepared")
                    or access.get("at_will")
                ),
                "resolution_path": path,
            }
        )
    missing = [item["spell_id"] for item in entries if item["resolution_path"] == "incomplete"]
    cantrip_entries = [item for item in entries if item["is_cantrip"]]
    available_entries = [item for item in entries if item["available"]]
    return {
        "complete": not missing,
        "counts": counts,
        "spell_count": len(entries),
        "cantrip_count": len(cantrip_entries),
        "available_spell_ids": [item["spell_id"] for item in available_entries],
        "cantrip_spell_ids": [item["spell_id"] for item in cantrip_entries],
        "missing_spell_ids": missing,
        "entries": entries,
    }


def spell_attack_action_resolution(description: str) -> dict[str, Any] | None:
    """Compile a complete SRD-style statblock spell attack without guessing omissions."""
    text = str(description or "")
    attack = re.search(
        r"(?is)(Melee|Ranged)\s+Spell\s+Attack:\**\s*([+\-]\d+)\s+to hit,\s*"
        r"(?:range|reach)\s+(\d+)(?:\s*/\s*\d+)?\s*ft\.?",
        text,
    )
    hit = re.search(
        r"(?is)Hit:\**\s*(?:\d+\s*)?\(\s*(\d+d\d+(?:\s*[+\-]\s*\d+)?)\s*\)\s*"
        r"([A-Za-z]+)\s+damage",
        text,
    )
    if attack is None or hit is None:
        return None
    formula = hit.group(1).replace(" ", "")
    if not _DICE.fullmatch(formula):
        # Flat modifiers cannot safely be treated as scalable spell dice.
        return None
    return normalize_spell_resolution(
        {
            "kind": "spell_attack",
            "targeting": {"mode": "creature", "max_targets": 1},
            "attack": {
                "mode": attack.group(1).casefold(),
                "count": {"base": 1},
                "attack_bonus_override": int(attack.group(2)),
                "range_ft_override": int(attack.group(3)),
                "damage": {
                    "base_dice": formula,
                    "damage_type": hit.group(2).casefold(),
                },
            },
        }
    )


def overlay_spell_attack_action(resolution: dict[str, Any], description: str) -> dict[str, Any]:
    """Overlay actor-specific attack facts while retaining the spell's ray/scaling rules."""
    parsed = spell_attack_action_resolution(description)
    if parsed is None or resolution.get("kind") != "spell_attack":
        return deepcopy(resolution)
    value = deepcopy(resolution)
    actor_attack = dict(parsed["attack"])
    value["attack"].update(
        attack_bonus_override=actor_attack["attack_bonus_override"],
        range_ft_override=actor_attack["range_ft_override"],
    )
    value["attack"]["damage"] = actor_attack["damage"]
    return normalize_spell_resolution(value)


def overlay_spell_attack_card(card: dict[str, Any], description: str) -> dict[str, Any]:
    """Apply one complete statblock spell action to both display and settlement data.

    A monster statblock can deliberately shorten a spell's range or replace its
    damage at the creature's printed level. Keeping only a resolution override
    leaves ``definition`` contradictory, which can make an Agent or UI narrate the
    base spell while the engine settles the statblock action. Preserve the exact
    catalog card and its components, but make the printed action authoritative for
    the displayed effect, range, and structured resolution.
    """

    parsed = spell_attack_action_resolution(description)
    resolution = card.get("resolution")
    if parsed is None or not isinstance(resolution, dict):
        return deepcopy(card)
    if resolution.get("kind") != "spell_attack":
        return deepcopy(card)

    value = deepcopy(card)
    value["resolution"] = overlay_spell_attack_action(resolution, description)
    actor_attack = dict(parsed["attack"])
    range_ft = int(actor_attack["range_ft_override"])
    definition = dict(value.get("definition") or {})
    definition["range"] = {
        **dict(definition.get("range") or {}),
        "kind": "distance",
        "normal_ft": range_ft,
        "long_ft": 0,
    }
    definition["effect"] = str(description).strip()
    value["definition"] = definition
    note = "Statblock action overrides the base spell's displayed range and effect."
    existing_notes = str(value.get("notes") or "").strip()
    value["notes"] = f"{existing_notes} {note}".strip()
    return value


__all__ = [
    "ENGINE_SETTLED_SPELL_MECHANIC_IDS",
    "SPELL_RESOLUTION_MECHANIC_ID",
    "SPELL_RESOLUTION_PATHS",
    "audit_spell_resolution_paths",
    "effective_spell_resolution",
    "known_2024_spell_resolution",
    "known_spell_resolution",
    "normalize_spell_resolution",
    "overlay_spell_attack_action",
    "overlay_spell_attack_card",
    "scaled_roll_expression",
    "spell_attack_action_resolution",
    "spell_attack_count",
    "spell_resolution_path",
]
