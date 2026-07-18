"""Strict, source-bound import of SRD-style D&D creature statblocks."""

from __future__ import annotations

import re
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
            r"(?:\s+(?:melee|ranged)\s+attacks?)?\s+with\s+(?:its|his|her|their)\s+"
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
    weapons: list[dict[str, Any]] = []
    multiattacks: list[tuple[str, str]] = []
    descriptive: list[tuple[str, str, str]] = []
    for section, entry_name, description in entries:
        if entry_name.casefold() == "multiattack":
            multiattacks.append((entry_name, description))
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
    sheet["inventory"]["items"] = weapons

    warnings: list[str] = []
    refs = list(dict.fromkeys(str(item) for item in rule_refs if str(item)))
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
            warnings.append(f"{entry_name}: Multiattack composition requires a DM ruling")
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
        warnings.append(f"{entry_name}: descriptive {activation} is not automatically settled")

    validated = validate_character_sheet(sheet)
    summary = f"{identity_text}; CR {challenge or 'unrecorded'}"
    return ParsedStatblock(
        name=actor_name,
        summary=summary,
        sheet=validated,
        challenge_rating=challenge,
        experience_points=xp,
        warnings=tuple(warnings),
    )


__all__ = ["ParsedStatblock", "StatblockImportError", "parse_2014_statblock"]
