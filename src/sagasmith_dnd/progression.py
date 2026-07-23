"""Audited D&D 5e (2014) single-class level advancement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.engine import roll

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
            max(0, int(next_threshold) - experience)
            if next_threshold is not None
            else None
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
    hp["value"] = int(hp.get("value", 0) or 0) + total_bonus

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
            old_source = str(entry.get("source") or "").strip()
            combined_source = (
                f"{old_source}; {normalized_source}" if old_source else normalized_source
            )
            if len(combined_source) > 300:
                raise CombatEngineError(
                    "combined hit-point progression source exceeds 300 characters"
                )
            entry["source"] = combined_source
    return value


def apply_constitution_score_hit_point_change(
    sheet: dict[str, Any],
    *,
    previous_score: int,
    new_score: int,
    source: str,
) -> dict[str, Any]:
    """Apply the retrospective per-level HP change caused by Constitution."""

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
    modifier_delta = (new_score - 10) // 2 - (previous_score - 10) // 2
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
    new_current = int(hp.get("value", 0) or 0) + total_delta
    if new_maximum < 1 or new_current < 0:
        raise CombatEngineError("Constitution change would produce invalid hit points")
    hp["max"] = new_maximum
    hp["value"] = min(new_maximum, new_current)
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
            old_source = str(entry.get("source") or "").strip()
            entry["source"] = (
                f"{old_source}; {normalized_source}" if old_source else normalized_source
            )
    return value


def advance_single_class_level(
    sheet: dict[str, Any],
    *,
    class_name: str,
    hp_method: str,
    hp_per_level_bonus: int = 0,
    source: str = "",
    rng: Any = None,
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
