"""Strict, source-bound import of SRD-style D&D creature statblocks."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet


class StatblockImportError(ValueError):
    """Raised when required statblock facts cannot be recovered from the source text."""


@dataclass(frozen=True)
class ParsedStatblock:
    name: str
    summary: str
    sheet: dict[str, Any]
    challenge_rating: str
    experience_points: int | None
    warnings: tuple[str, ...]
    spellcasting: dict[str, Any] | None = None


_ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
_ABILITY_ABBREVIATIONS = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}
_SKILL_NAMES = {
    "acrobatics": "acrobatics",
    "animal handling": "animal_handling",
    "arcana": "arcana",
    "athletics": "athletics",
    "deception": "deception",
    "history": "history",
    "insight": "insight",
    "intimidation": "intimidation",
    "investigation": "investigation",
    "medicine": "medicine",
    "nature": "nature",
    "perception": "perception",
    "performance": "performance",
    "persuasion": "persuasion",
    "religion": "religion",
    "sleight of hand": "sleight_of_hand",
    "stealth": "stealth",
    "survival": "survival",
}
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
}

_2014_ARMOR = {
    "padded": (11, "full", None, True),
    "leather": (11, "full", None, False),
    "studded leather": (12, "full", None, False),
    "hide": (12, "max", 2, False),
    "chain shirt": (13, "max", 2, False),
    "scale mail": (14, "max", 2, True),
    "breastplate": (14, "max", 2, False),
    "half plate": (15, "max", 2, True),
    "ring mail": (14, "none", None, True),
    "chain mail": (16, "none", None, True),
    "splint": (17, "none", None, True),
    "plate": (18, "none", None, True),
}


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or "action"


def _field(markdown: str, label: str, *, required: bool = False) -> str:
    match = re.search(
        rf"(?im)^\*\*{re.escape(label)}\*\*\s+(.+?)\s*$", markdown
    )
    if match:
        return match.group(1).strip()
    if required:
        raise StatblockImportError(f"statblock is missing {label}")
    return ""


def _signed(value: str) -> int:
    return int(value.replace(" ", ""))


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;]", value) if item.strip() and item != "-"]


def _parse_armor_equipment(
    ac_text: str, source_key: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Recover explicit standard armor without inferring gear from a numeric AC."""

    detail = re.search(r"\(([^)]*)\)", ac_text)
    if not detail:
        return [], {}
    normalized = detail.group(1).casefold()
    items: list[dict[str, Any]] = []
    slots: dict[str, str] = {}
    armor_name = next(
        (
            name
            for name in sorted(_2014_ARMOR, key=len, reverse=True)
            if re.search(rf"\b{re.escape(name)}(?:\s+armor)?\b", normalized)
        ),
        None,
    )
    if armor_name is not None:
        base_ac, dexterity_mode, dexterity_max, stealth_disadvantage = _2014_ARMOR[
            armor_name
        ]
        armor_id = f"statblock-{_slug(armor_name)}"
        mechanics: dict[str, Any] = {
            "base_ac": base_ac,
            "dexterity_mode": dexterity_mode,
            "magic_bonus": 0,
            "stealth_disadvantage": stealth_disadvantage,
        }
        if dexterity_max is not None:
            mechanics["dexterity_max"] = dexterity_max
        items.append(
            {
                "id": armor_id,
                "name": armor_name.title(),
                "kind": "armor",
                "source_key": source_key,
                "description": f"Explicitly listed in Armor Class: {ac_text}",
                "equipped": True,
                "equipped_slot": "armor",
                "mechanics": mechanics,
            }
        )
        slots["armor"] = armor_id
    if re.search(r"\bshield\b", normalized):
        shield_id = "statblock-shield"
        items.append(
            {
                "id": shield_id,
                "name": "Shield",
                "kind": "shield",
                "source_key": source_key,
                "description": f"Explicitly listed in Armor Class: {ac_text}",
                "equipped": True,
                "equipped_slot": "shield",
                "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
            }
        )
        slots["shield"] = shield_id
    return items, slots


def _parse_speed(value: str) -> dict[str, int]:
    speeds = {"walk": 0, "fly": 0, "swim": 0, "climb": 0, "burrow": 0}
    for part in _split_list(value):
        match = re.search(r"(?i)(?:(fly|swim|climb|burrow)\s+)?(\d+)\s*ft", part)
        if match:
            speeds[(match.group(1) or "walk").casefold()] = int(match.group(2))
    if not any(speeds.values()):
        raise StatblockImportError("statblock Speed has no supported movement distance")
    return speeds


def _parse_ability_scores(markdown: str) -> dict[str, int]:
    header = re.search(
        r"(?im)^\|\s*STR\s*\|\s*DEX\s*\|\s*CON\s*\|\s*INT\s*\|\s*WIS\s*\|\s*CHA\s*\|\s*$",
        markdown,
    )
    if not header:
        raise StatblockImportError("statblock is missing the STR/DEX/CON/INT/WIS/CHA table")
    following = markdown[header.end() :].splitlines()
    value_line = next(
        (
            line
            for line in following
            if line.strip().startswith("|") and not re.fullmatch(r"[\s|:\-]+", line)
        ),
        "",
    )
    scores = [int(value) for value in re.findall(r"\|\s*(\d+)\s*\([+\-−]?\d+\)", value_line)]
    if len(scores) != 6:
        raise StatblockImportError("statblock ability table must contain six scores")
    return dict(zip(_ABILITIES, scores, strict=True))


def _parse_bonus_list(value: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for match in re.finditer(r"(?i)([A-Za-z ]+?)\s*([+\-−]\s*\d+)(?=\s*,|\s*;|$)", value):
        result[match.group(1).strip().casefold()] = _signed(match.group(2).replace("−", "-"))
    return result


def _parse_senses(value: str, sheet: dict[str, Any], ability_scores: dict[str, int]) -> None:
    for label, key in (
        ("darkvision", "darkvision"),
        ("blindsight", "blindsight"),
        ("tremorsense", "tremorsense"),
        ("truesight", "truesight"),
    ):
        match = re.search(rf"(?i){label}\s+(\d+)\s*ft", value)
        if match:
            sheet["traits"]["senses"][key] = int(match.group(1))
    passive = re.search(r"(?i)passive\s+Perception\s+(\d+)", value)
    if passive:
        wisdom_modifier = (ability_scores["wisdom"] - 10) // 2
        perception = sheet["skills"]["perception"]
        calculated = 10 + wisdom_modifier + int(perception.get("bonus", 0) or 0)
        sheet["traits"]["senses"]["passive_perception_bonus"] = (
            int(passive.group(1)) - calculated
        )


def _entry_blocks(markdown: str) -> list[tuple[str, str, str]]:
    markers = list(re.finditer(r"(?m)^\*\*\*(.+?)\*\*\*\.\s*", markdown))
    headings = list(re.finditer(r"(?im)^#{2,6}\s+(.+?)\s*$", markdown))
    result: list[tuple[str, str, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        intervening_heading = next(
            (heading for heading in headings if marker.end() < heading.start() < end), None
        )
        if intervening_heading:
            end = intervening_heading.start()
        section = "traits"
        prior = [heading for heading in headings if heading.start() < marker.start()]
        if prior:
            section = prior[-1].group(1).strip().casefold()
        description = re.sub(r"\s+", " ", markdown[marker.end() : end]).strip()
        result.append((section, marker.group(1).strip(), description))
    return result


def _parse_weapon(name: str, description: str, source_key: str) -> dict[str, Any] | None:
    attack = re.search(
        r"(?i)\*?(Melee|Ranged|Melee or Ranged)\s+(Weapon|Spell)\s+Attack:\*?\s*"
        r"([+\-−]\s*\d+)\s+to hit",
        description,
    )
    if not attack:
        return None
    mode = attack.group(1).casefold()
    hit = re.search(
        r"(?i)\*?Hit:\*?\s*\d+\s*\((\d+d\d+(?:\s*[+\-]\s*\d+)?)\)\s*"
        r"([a-z]+)\s+damage",
        description,
    )
    if not hit:
        raise StatblockImportError(f"weapon action {name!r} has no supported Hit dice expression")
    expression = hit.group(1).replace(" ", "")
    damage = re.fullmatch(r"(\d+d\d+)(?:([+\-]\d+))?", expression)
    if not damage:
        raise StatblockImportError(f"weapon action {name!r} has an invalid damage expression")
    additional_damage: list[dict[str, Any]] = []
    last_damage_end = hit.end()
    for extra in re.finditer(
        r"(?i)\bplus\s+\d+\s*\((\d+d\d+(?:\s*[+\-]\s*\d+)?)\)\s*"
        r"([a-z]+)\s+damage",
        description[hit.end() :],
    ):
        extra_expression = extra.group(1).replace(" ", "")
        parsed_extra = re.fullmatch(r"(\d+d\d+)(?:([+\-]\d+))?", extra_expression)
        if not parsed_extra:
            raise StatblockImportError(
                f"weapon action {name!r} has an invalid additional damage expression"
            )
        additional_damage.append(
            {
                "damage_formula": parsed_extra.group(1),
                "damage_bonus": int(parsed_extra.group(2) or 0),
                "damage_type": extra.group(2).casefold(),
            }
        )
        last_damage_end = hit.end() + extra.end()
    on_hit_effect = description[last_damage_end:].strip().lstrip(". ,;").strip()
    reach = re.search(r"(?i)reach\s+(\d+)\s*ft", description)
    ranges = re.search(r"(?i)range\s+(\d+)(?:\s*/\s*(\d+))?\s*ft", description)
    properties: list[str] = []
    if mode == "melee or ranged":
        properties.append("thrown")
    mechanics: dict[str, Any] = {
        "attack_type": "ranged" if mode == "ranged" else "melee",
        "attack_ability": (
            "spell"
            if attack.group(2).casefold() == "spell"
            else "dexterity"
            if mode == "ranged"
            else "strength"
        ),
        "damage_formula": damage.group(1),
        "damage_type": hit.group(2).casefold(),
        "additional_damage": additional_damage,
        "on_hit_effect": on_hit_effect,
        "properties": properties,
        "proficient": False,
        "attack_bonus_override": _signed(attack.group(3).replace("−", "-")),
        "damage_bonus_override": int(damage.group(2) or 0),
        "reach_ft": int(reach.group(1)) if reach else 5,
        "always_available": True,
    }
    if ranges:
        mechanics["normal_range_ft"] = int(ranges.group(1))
        mechanics["long_range_ft"] = int(ranges.group(2) or ranges.group(1))
        if mode == "melee or ranged":
            mechanics["thrown_normal_range_ft"] = int(ranges.group(1))
            mechanics["thrown_long_range_ft"] = int(ranges.group(2) or ranges.group(1))
    return {
        "id": _slug(name),
        "name": name,
        "kind": "weapon",
        "description": description,
        "source_key": source_key,
        "mechanics": mechanics,
    }


def _count(value: str) -> int | None:
    value = value.casefold().strip()
    if value.isdigit():
        return int(value)
    return _NUMBER_WORDS.get(value)


def _weapon_id(value: str, weapons: dict[str, str]) -> str | None:
    normalized = re.sub(r"[^a-z0-9 ]", "", value.casefold()).strip()
    candidates = [normalized]
    if normalized.endswith("s"):
        candidates.append(normalized[:-1])
    for candidate in candidates:
        if candidate in weapons:
            return weapons[candidate]
    return None


def _parse_multiattack(description: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weapons = {
        re.sub(r"[^a-z0-9 ]", "", item["name"].casefold()).strip(): item["id"]
        for item in items
    }
    sentence_groups = re.split(r"(?i)\.\s*(?:Or\s+)?", description)
    options: list[dict[str, Any]] = []
    for group in sentence_groups:
        if "attack" not in group.casefold():
            continue
        attack_mode = "ranged" if "ranged attack" in group.casefold() else "melee"
        attacks: list[dict[str, Any]] = []
        for match in re.finditer(
            r"(?i)(one|two|three|four|five|six|\d+)"
            r"(?:\s+(?:(?:melee|ranged)\s+)?attacks?)?\s+with\s+"
            r"(?:its|his|her|their)\s+"
            r"([a-z][a-z '\-]+?)(?=\s+and\s+|\s*,\s*|\.|$)",
            group,
        ):
            count = _count(match.group(1))
            weapon_id = _weapon_id(match.group(2), weapons)
            if count is None or weapon_id is None:
                return []
            attacks.append({"weapon_id": weapon_id, "attack_mode": attack_mode, "count": count})
        if attacks:
            options.append({"id": attack_mode, "attacks": attacks})
    ids: dict[str, int] = {}
    for option in options:
        base = option["id"]
        ids[base] = ids.get(base, 0) + 1
        if ids[base] > 1:
            option["id"] = f"{base}-{ids[base]}"
    return options


def _parse_spellcasting(description: str) -> dict[str, Any] | None:
    ability_match = re.search(
        r"(?i)spellcasting ability is\s+"
        r"(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)",
        description,
    )
    if not ability_match:
        return None
    save_match = re.search(r"(?i)spell save DC\s*(\d+)", description)
    attack_match = re.search(r"(?i)([+\-]\d+)\s+to hit with spell attacks", description)
    headers = list(
        re.finditer(
            r"(?i)(Cantrips?\s*\(at will\)|"
            r"([1-9])(?:st|nd|rd|th) level\s*\((\d+) slots?\))\s*:\s*",
            description,
        )
    )
    if not headers:
        return None
    spells: list[dict[str, Any]] = []
    slots: dict[str, int] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(description)
        names = [item.strip() for item in description[header.end() : end].split(",")]
        names = [item for item in names if item]
        level = int(header.group(2) or 0)
        if level:
            slots[str(level)] = int(header.group(3))
        spells.extend(
            {"name": name, "level": level, "at_will": level == 0}
            for name in names
        )
    return {
        "ability": ability_match.group(1).casefold(),
        "save_dc": int(save_match.group(1)) if save_match else None,
        "attack_bonus": int(attack_match.group(1)) if attack_match else None,
        "slots": slots,
        "spells": spells,
        "description": description,
    }


def _spell_action_name(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", value).strip().casefold()


def parse_2014_statblock(
    markdown: str,
    *,
    source_key: str,
    rule_refs: list[str] | tuple[str, ...] = (),
    name: str | None = None,
) -> ParsedStatblock:
    """Parse an English 2014 SRD-style creature block into a validated v2 sheet.

    The importer intentionally rejects missing core combat facts. Descriptive traits and
    unsupported action semantics remain source-cited content entries and are reported as
    warnings instead of being silently treated as executable engine mechanics.
    """

    if not isinstance(markdown, str) or not markdown.strip():
        raise StatblockImportError("statblock source text is empty")
    heading = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown)
    actor_name = (name or (heading.group(1) if heading else "")).strip()
    if not actor_name:
        raise StatblockImportError("statblock is missing a creature heading")
    identity = re.search(r"(?m)^\*([^*\n]+)\*\s*$", markdown)
    if not identity:
        raise StatblockImportError("statblock is missing size, type, and alignment")
    identity_text = identity.group(1).strip()
    identity_parts = [part.strip() for part in identity_text.split(",", 1)]
    size_type = identity_parts[0]
    size_match = re.match(r"(?i)(Tiny|Small|Medium|Large|Huge|Gargantuan)\s+(.+)", size_type)
    if not size_match:
        raise StatblockImportError("statblock size/type line is not supported")
    alignment = identity_parts[1] if len(identity_parts) > 1 else ""

    ac_text = _field(markdown, "Armor Class", required=True)
    hp_text = _field(markdown, "Hit Points", required=True)
    speed_text = _field(markdown, "Speed", required=True)
    ac_match = re.match(r"(\d+)", ac_text)
    hp_match = re.match(r"(\d+)(?:\s*\(([^)]+)\))?", hp_text)
    if not ac_match or not hp_match:
        raise StatblockImportError("statblock Armor Class or Hit Points is invalid")
    hp_max = int(hp_match.group(1))
    ability_scores = _parse_ability_scores(markdown)

    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = size_match.group(2).strip()
    sheet["traits"]["size"] = size_match.group(1).casefold()
    sheet["traits"]["alignment"] = alignment
    for ability, score in ability_scores.items():
        sheet["abilities"][ability]["score"] = score
    sheet["combat"]["hp"] = {"value": hp_max, "max": hp_max, "temp": 0}
    sheet["combat"]["ac"] = {"base": int(ac_match.group(1)), "override": int(ac_match.group(1))}
    sheet["combat"]["speed"] = _parse_speed(speed_text)
    hp_dice = re.fullmatch(r"\s*(\d+)d(\d+)(?:\s*[+\-]\s*\d+)?\s*", hp_match.group(2) or "")
    if hp_dice:
        sheet["combat"]["hit_dice"] = {
            f"d{hp_dice.group(2)}": {
                "value": int(hp_dice.group(1)),
                "max": int(hp_dice.group(1)),
                "recovers_on": "long_rest",
            }
        }

    for abbreviation, target in _parse_bonus_list(_field(markdown, "Saving Throws")).items():
        ability = _ABILITY_ABBREVIATIONS.get(abbreviation)
        if ability:
            sheet["abilities"][ability]["bonus"] = target - (ability_scores[ability] - 10) // 2
    for label, target in _parse_bonus_list(_field(markdown, "Skills")).items():
        skill = _SKILL_NAMES.get(label)
        if skill:
            ability = {
                "athletics": "strength",
                "acrobatics": "dexterity",
                "sleight_of_hand": "dexterity",
                "stealth": "dexterity",
                "arcana": "intelligence",
                "history": "intelligence",
                "investigation": "intelligence",
                "nature": "intelligence",
                "religion": "intelligence",
                "animal_handling": "wisdom",
                "insight": "wisdom",
                "medicine": "wisdom",
                "perception": "wisdom",
                "survival": "wisdom",
                "deception": "charisma",
                "intimidation": "charisma",
                "performance": "charisma",
                "persuasion": "charisma",
            }[skill]
            sheet["skills"][skill]["bonus"] = target - (ability_scores[ability] - 10) // 2

    for label, key in (
        ("Damage Resistances", "resistances"),
        ("Damage Immunities", "immunities"),
        ("Damage Vulnerabilities", "vulnerabilities"),
        ("Condition Immunities", "condition_immunities"),
    ):
        sheet["traits"][key] = _split_list(_field(markdown, label))
    sheet["traits"]["languages"] = _split_list(_field(markdown, "Languages"))
    _parse_senses(_field(markdown, "Senses"), sheet, ability_scores)

    challenge_text = _field(markdown, "Challenge")
    challenge_match = re.match(r"([^\s(]+)(?:\s*\(([\d,]+)\s+XP\))?", challenge_text)
    challenge = challenge_match.group(1) if challenge_match else ""
    xp = (
        int(challenge_match.group(2).replace(",", ""))
        if challenge_match and challenge_match.group(2)
        else None
    )

    entries = _entry_blocks(markdown)
    spellcasting: dict[str, Any] | None = None
    spellcasting_entry: tuple[str, str, str] | None = next(
        (
            entry
            for entry in entries
            if entry[1].strip().casefold() == "spellcasting"
        ),
        None,
    )
    if spellcasting_entry is not None:
        spellcasting = _parse_spellcasting(spellcasting_entry[2])
    if spellcasting is not None:
        sheet["spellcasting"]["ability"] = spellcasting["ability"]
        sheet["spellcasting"]["attack_bonus_override"] = spellcasting.get("attack_bonus")
        sheet["spellcasting"]["save_dc_override"] = spellcasting.get("save_dc")
    spell_specs = {
        str(item["name"]).casefold(): item
        for item in (spellcasting or {}).get("spells", [])
    }
    weapons: list[dict[str, Any]] = []
    multiattacks: list[tuple[str, str]] = []
    descriptive: list[tuple[str, str, str]] = []
    unresolved_multiattacks: set[str] = set()
    for section, entry_name, description in entries:
        if entry_name.casefold() == "spellcasting" and spellcasting is not None:
            continue
        if entry_name.casefold() == "multiattack":
            multiattacks.append((entry_name, description))
            continue
        spell_spec = spell_specs.get(_spell_action_name(entry_name))
        if spell_spec is not None:
            spell_spec["action_name"] = entry_name
            spell_spec["action_description"] = description
            continue
        weapon = _parse_weapon(entry_name, description, source_key)
        if weapon:
            weapons.append(weapon)
        else:
            descriptive.append((section, entry_name, description))
    if not weapons:
        raise StatblockImportError("statblock has no supported weapon action")
    ids = [item["id"] for item in weapons]
    if len(ids) != len(set(ids)):
        raise StatblockImportError("statblock contains duplicate weapon action names")
    armor_items, armor_slots = _parse_armor_equipment(ac_text, source_key)
    sheet["inventory"]["items"] = [*armor_items, *weapons]
    sheet["inventory"]["equipment_slots"].update(armor_slots)

    warnings: list[str] = []
    refs = list(dict.fromkeys(str(item) for item in rule_refs if str(item)))
    if spellcasting is not None:
        sheet["content"]["features"].append(
            {
                "id": "spellcasting-passive",
                "name": "Spellcasting",
                "source_key": source_key,
                "description": spellcasting["description"],
                "activation": {"type": "passive", "cost": 0},
                "rule_refs": refs,
            }
        )
    for weapon in weapons:
        if str(dict(weapon.get("mechanics") or {}).get("on_hit_effect") or "").strip():
            warnings.append(f"{weapon['name']}: on-hit effect requires DM settlement")
    for entry_name, description in multiattacks:
        options = _parse_multiattack(description, weapons)
        if options:
            sheet["content"]["activities"].append(
                {
                    "id": f"{_slug(entry_name)}-activity",
                    "name": "Multiattack",
                    "source_key": source_key,
                    "description": description,
                    "activation": {"type": "action", "cost": 1},
                    "choices": {"multiattack_options": options},
                    "rule_refs": refs,
                }
            )
        else:
            unresolved_multiattacks.add(entry_name)
            descriptive.append(("actions", entry_name, description))
    for section, entry_name, description in descriptive:
        activation = (
            "reaction"
            if "reaction" in section
            else "action"
            if "action" in section
            else "passive"
        )
        sheet["content"]["activities" if activation != "passive" else "features"].append(
            {
                "id": f"{_slug(entry_name)}-{activation}",
                "name": entry_name,
                "source_key": source_key,
                "description": description,
                "activation": {"type": activation, "cost": 1 if activation != "passive" else 0},
                "rule_refs": refs,
            }
        )
        warnings.append(
            f"{entry_name}: Multiattack composition requires a DM ruling"
            if entry_name in unresolved_multiattacks
            else f"{entry_name}: descriptive {activation} is not automatically settled"
        )

    validated = validate_character_sheet(sheet)
    summary = f"{identity_text}; CR {challenge or 'unrecorded'}"
    return ParsedStatblock(
        name=actor_name,
        summary=summary,
        sheet=validated,
        challenge_rating=challenge,
        experience_points=xp,
        warnings=tuple(warnings),
        spellcasting=deepcopy(spellcasting),
    )


def _variant_attack_description(item: dict[str, Any], source_ref: str) -> str:
    """Render display text from the same structured mechanics the engine will use."""
    mechanics = dict(item.get("mechanics") or {})
    mode = str(mechanics.get("attack_type") or "melee").strip().casefold()
    attack_kind = "Spell" if mechanics.get("attack_ability") == "spell" else "Weapon"
    attack_bonus = mechanics.get("attack_bonus_override")
    attack_bonus_text = (
        f"{int(attack_bonus):+d}" if attack_bonus is not None else "derived bonus"
    )
    if mode == "ranged":
        normal = int(mechanics.get("normal_range_ft", 0) or 0)
        long = int(mechanics.get("long_range_ft", 0) or 0)
        range_text = f"range {normal}/{long} ft." if long > normal else f"range {normal} ft."
    else:
        range_text = f"reach {int(mechanics.get('reach_ft', 5) or 5)} ft."
    formula = str(mechanics.get("damage_formula") or "structured damage")
    damage_bonus = mechanics.get("damage_bonus_override")
    if damage_bonus:
        formula = f"{formula} {'+' if int(damage_bonus) > 0 else '-'} {abs(int(damage_bonus))}"
    damage_type = str(mechanics.get("damage_type") or "untyped")
    return (
        f"*{mode.title()} {attack_kind} Attack:* {attack_bonus_text} to hit, "
        f"{range_text}, one target. *Hit:* {formula} {damage_type} damage. "
        f"Variant source: {source_ref}."
    )


def apply_statblock_variant(
    sheet: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Apply a narrow, source-cited module variant to a parsed creature sheet.

    Adventures commonly instantiate a published creature with a changed current HP,
    armor, languages, or weapon damage type.  This deliberately does not accept a
    generic sheet patch: every supported override has explicit validation so a module
    citation cannot silently replace unrelated actor rules.
    """

    if not isinstance(variant, dict):
        raise StatblockImportError("statblock variant must be an object")
    allowed = {
        "source_ref",
        "creature_type",
        "current_hit_points",
        "maximum_hit_points",
        "armor_class",
        "languages",
        "remove_actions",
        "action_overrides",
    }
    unknown = set(variant) - allowed
    if unknown:
        raise StatblockImportError(f"unsupported statblock variant fields: {sorted(unknown)}")
    source_ref = str(variant.get("source_ref") or "").strip()
    if not source_ref:
        raise StatblockImportError("statblock variant source_ref is required")

    result = deepcopy(sheet)
    if "creature_type" in variant:
        creature_type = str(variant["creature_type"] or "").strip()
        if not creature_type or len(creature_type) > 100:
            raise StatblockImportError(
                "creature_type must be a non-empty string of at most 100 characters"
            )
        result["progression"]["species"] = creature_type

    hp = result["combat"]["hp"]
    if "maximum_hit_points" in variant:
        maximum = variant["maximum_hit_points"]
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise StatblockImportError("maximum_hit_points must be a positive integer")
        hp["max"] = maximum
        hp["value"] = min(int(hp.get("value", maximum)), maximum)
    if "current_hit_points" in variant:
        current = variant["current_hit_points"]
        if (
            not isinstance(current, int)
            or isinstance(current, bool)
            or current < 0
            or current > int(hp["max"])
        ):
            raise StatblockImportError(
                "current_hit_points must be an integer between 0 and maximum_hit_points"
            )
        hp["value"] = current

    if "armor_class" in variant:
        armor_class = variant["armor_class"]
        if (
            not isinstance(armor_class, int)
            or isinstance(armor_class, bool)
            or not 0 <= armor_class <= 99
        ):
            raise StatblockImportError("armor_class must be an integer between 0 and 99")
        result["combat"]["ac"] = {"base": armor_class, "override": armor_class}

    if "languages" in variant:
        languages = variant["languages"]
        if not isinstance(languages, list):
            raise StatblockImportError("languages must be a list")
        normalized_languages = [str(item).strip() for item in languages]
        if (
            any(not item for item in normalized_languages)
            or len(normalized_languages) != len(set(normalized_languages))
        ):
            raise StatblockImportError("languages must contain unique non-empty strings")
        result["traits"]["languages"] = normalized_languages

    items = list(result["inventory"]["items"])
    remove_actions = variant.get("remove_actions", [])
    if not isinstance(remove_actions, list):
        raise StatblockImportError("remove_actions must be a list")
    remove_keys = [str(item).strip().casefold() for item in remove_actions]
    if any(not item for item in remove_keys) or len(remove_keys) != len(set(remove_keys)):
        raise StatblockImportError("remove_actions must contain unique non-empty ids or names")
    removed_ids: set[str] = set()
    for key in remove_keys:
        matches = [
            item
            for item in items
            if key in {str(item.get("id") or "").casefold(), str(item.get("name") or "").casefold()}
        ]
        if len(matches) != 1:
            raise StatblockImportError(
                f"remove_actions entry must identify exactly one weapon action: {key}"
            )
        removed_ids.add(str(matches[0]["id"]))
        items.remove(matches[0])

    action_overrides = variant.get("action_overrides", {})
    if not isinstance(action_overrides, dict):
        raise StatblockImportError("action_overrides must be an object keyed by weapon action id")
    renamed_ids: dict[str, str] = {}
    for raw_key, raw_patch in action_overrides.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_patch, dict):
            raise StatblockImportError("each action override must be a non-empty id and object")
        matches = [item for item in items if str(item.get("id") or "") == key]
        if len(matches) != 1:
            raise StatblockImportError(
                f"action override must identify exactly one remaining weapon action: {key}"
            )
        patch_allowed = {
            "id",
            "name",
            "damage_type",
            "damage_formula",
            "attack_bonus_override",
            "damage_bonus_override",
        }
        patch_unknown = set(raw_patch) - patch_allowed
        if patch_unknown:
            raise StatblockImportError(
                f"unsupported action override fields for {key}: {sorted(patch_unknown)}"
            )
        item = matches[0]
        mechanics = item["mechanics"]
        if "id" in raw_patch:
            new_id = str(raw_patch["id"] or "").strip()
            if not new_id or _slug(new_id) != new_id:
                raise StatblockImportError("action override id must be a lowercase slug")
            renamed_ids[key] = new_id
            item["id"] = new_id
        if "name" in raw_patch:
            name = str(raw_patch["name"] or "").strip()
            if not name:
                raise StatblockImportError("action override name must be non-empty")
            item["name"] = name
        if "damage_type" in raw_patch:
            damage_type = str(raw_patch["damage_type"] or "").strip().casefold()
            if not damage_type:
                raise StatblockImportError("action override damage_type must be non-empty")
            mechanics["damage_type"] = damage_type
        if "damage_formula" in raw_patch:
            damage_formula = str(raw_patch["damage_formula"] or "").replace(" ", "")
            if not re.fullmatch(r"\d+d\d+", damage_formula):
                raise StatblockImportError("action override damage_formula must be NdM dice")
            mechanics["damage_formula"] = damage_formula
        for field in ("attack_bonus_override", "damage_bonus_override"):
            if field in raw_patch:
                value = raw_patch[field]
                if not isinstance(value, int) or isinstance(value, bool):
                    raise StatblockImportError(f"action override {field} must be an integer")
                mechanics[field] = value
        item["description"] = _variant_attack_description(item, source_ref)

    remaining_ids = [str(item.get("id") or "") for item in items]
    if len(remaining_ids) != len(set(remaining_ids)):
        raise StatblockImportError("statblock variant produces duplicate weapon action ids")
    result["inventory"]["items"] = items
    for activity in result["content"]["activities"]:
        choices = activity.get("choices")
        if not isinstance(choices, dict):
            continue
        for option in choices.get("multiattack_options", []):
            for attack in option.get("attacks", []):
                weapon_id = str(attack.get("weapon_id") or "")
                if weapon_id in renamed_ids:
                    attack["weapon_id"] = renamed_ids[weapon_id]
                if weapon_id in removed_ids:
                    raise StatblockImportError(
                        f"cannot remove action {weapon_id!r} while a multiattack references it"
                    )

    return validate_character_sheet(result)


__all__ = [
    "ParsedStatblock",
    "StatblockImportError",
    "apply_statblock_variant",
    "parse_2014_statblock",
]
