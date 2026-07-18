"""Audited D&D 5e (2014) single-class level advancement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError

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


def advance_single_class_level(
    sheet: dict[str, Any],
    *,
    class_name: str,
    hp_method: str,
    hp_roll: int | None = None,
    hp_per_level_bonus: int = 0,
    source: str = "",
) -> dict[str, Any]:
    """Advance an existing 2014 class exactly one level.

    This transaction settles deterministic card state only. Class features,
    subclass choices, feats, and selected spells remain catalog operations and
    are reported as follow-up choices by the MCP layer.
    """
    value = deepcopy(sheet)
    if "2014" not in str(value.get("edition") or "2014"):
        raise CombatEngineError("level advancement currently supports D&D 5e 2014 cards only")
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
    normalized_method = str(hp_method).strip().casefold().replace("-", "_")
    if normalized_method == "fixed":
        if hp_roll is not None:
            raise CombatEngineError("hp_roll must be omitted when hp_method is fixed")
        die_value = hit_die // 2 + 1
    elif normalized_method == "rolled":
        if isinstance(hp_roll, bool) or not isinstance(hp_roll, int) or not 1 <= hp_roll <= hit_die:
            raise CombatEngineError(f"hp_roll must be an integer from 1 to {hit_die}")
        die_value = hp_roll
    else:
        raise CombatEngineError("hp_method must be fixed or rolled")
    if isinstance(hp_per_level_bonus, bool) or not isinstance(hp_per_level_bonus, int):
        raise CombatEngineError("hp_per_level_bonus must be an integer")
    if hp_per_level_bonus < 0:
        raise CombatEngineError("hp_per_level_bonus cannot be negative")
    constitution_modifier = _ability_modifier(value, "constitution")
    class_hp_gain = max(1, die_value + constitution_modifier)
    hp_gain = class_hp_gain + hp_per_level_bonus

    hp = value.setdefault("combat", {}).setdefault("hp", {})
    old_hp_max = int(hp.get("max", 0) or 0)
    hp["max"] = old_hp_max + hp_gain
    # The 2014 rule increases maximum HP; it does not say that leveling heals
    # damage already taken. Current HP therefore remains unchanged.
    hp_progression = value["combat"].setdefault("hp_progression", [])
    if any(int(item.get("level", 0) or 0) == new_level for item in hp_progression):
        raise CombatEngineError("hit-point progression already records the new level")
    hp_progression.append(
        {
            "level": new_level,
            "method": normalized_method,
            "value": hp_gain,
            "source": source or f"{class_name} level {new_level}",
        }
    )

    hit_dice = value["combat"].setdefault("hit_dice", {})
    hit_die_key, hit_die_resource = _class_hit_die_resource(hit_dice, class_name, hit_die)
    hit_die_resource["max"] = int(hit_die_resource.get("max", 0) or 0) + 1
    hit_die_resource["value"] = int(hit_die_resource.get("value", 0) or 0) + 1
    hit_dice[hit_die_key] = hit_die_resource

    target["level"] = new_level
    progression["classes"] = [target]
    progression["level"] = new_level
    spellcasting = _advance_spellcasting(value, class_name, old_level, new_level)
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
        "spell_choices": _spell_choice_delta(class_name, old_level, new_level),
    }


def _advance_spellcasting(
    sheet: dict[str, Any], class_name: str, old_level: int, new_level: int
) -> dict[str, Any]:
    key = class_name.casefold()
    config = CASTER_CONFIG.get(key)
    if config is None:
        return {"kind": "none", "slot_changes": {}}
    ability, mode, kind = config
    spellcasting = sheet.setdefault("spellcasting", {})
    spellcasting["ability"] = spellcasting.get("ability") or ability
    preparation = spellcasting.setdefault("preparation", {})
    preparation["mode"] = mode
    preparation.setdefault("selected_spell_ids", [])
    preparation["changes_on"] = "long_rest"
    spellcasting.setdefault("spellbook", {"enabled": False, "spell_ids": []})
    if key == "wizard":
        spellcasting["spellbook"]["enabled"] = True
    slot_changes: dict[str, dict[str, int]] = {}
    if kind == "pact":
        old_max, old_slot_level = PACT_MAGIC[old_level]
        new_max, new_slot_level = PACT_MAGIC[new_level]
        pact = dict(spellcasting.get("pact_magic") or {})
        old_value = int(pact.get("value", old_max) or 0)
        pact.update(
            label="Pact Magic",
            value=old_value + max(0, new_max - old_max),
            max=new_max,
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
        table = FULL_CASTER_SLOTS if kind == "full" else HALF_CASTER_SLOTS
        old_slots = table[old_level]
        new_slots = table[new_level]
        resources = spellcasting.setdefault("spell_slots", {})
        for slot_level in range(1, max(len(old_slots), len(new_slots)) + 1):
            old_max = old_slots[slot_level - 1] if slot_level <= len(old_slots) else 0
            new_max = new_slots[slot_level - 1] if slot_level <= len(new_slots) else 0
            if new_max == 0:
                continue
            resource = dict(resources.get(str(slot_level)) or {})
            old_value = int(resource.get("value", old_max) or 0)
            resource.update(
                label=f"Level {slot_level} spell slots",
                value=old_value + max(0, new_max - old_max),
                max=new_max,
                recovers_on="long_rest",
                source_key=class_name,
                slot_level=slot_level,
            )
            resources[str(slot_level)] = resource
            if old_max != new_max:
                slot_changes[str(slot_level)] = {"old_max": old_max, "new_max": new_max}
    if mode in {"prepared", "spellbook"}:
        modifier = _ability_modifier(sheet, ability)
        class_contribution = new_level // 2 if key == "paladin" else new_level
        preparation["max_prepared"] = max(1, class_contribution + modifier)
    return {
        "kind": kind,
        "ability": ability,
        "mode": mode,
        "slot_changes": slot_changes,
        "max_prepared": int(preparation.get("max_prepared", 0) or 0),
    }


def _spell_choice_delta(class_name: str, old_level: int, new_level: int) -> dict[str, int]:
    key = class_name.casefold()
    result = {"cantrips_to_add": 0, "leveled_spells_to_add": 0}
    cantrips = CANTRIPS_KNOWN.get(key)
    if cantrips:
        result["cantrips_to_add"] = max(0, cantrips[new_level - 1] - cantrips[old_level - 1])
    known = KNOWN_SPELLS.get(key)
    if known:
        result["leveled_spells_to_add"] = max(
            0, known[new_level - 1] - known[old_level - 1]
        )
    if key == "wizard":
        result["leveled_spells_to_add"] = 2
    return result


def _ability_modifier(sheet: dict[str, Any], ability: str) -> int:
    score = int(sheet.get("abilities", {}).get(ability, {}).get("score", 10) or 10)
    return (score - 10) // 2


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
