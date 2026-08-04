"""Strict, source-bound import of SRD-style D&D creature statblocks."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping

from sagasmith_core.text import ascii_slug, compact_ascii_key

from sagasmith_dnd.abilities import ABILITY_ABBREVIATIONS, ABILITY_NAMES
from sagasmith_dnd.activity_identity import (
    MULTIATTACK_MECHANIC_ID,
    is_multiattack_activity,
    is_multiattack_source_name,
)
from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.engine import ability_modifier
from sagasmith_dnd.resolution_plan import (
    ResolutionPlanCompilationError,
    compile_resolution_plan,
    resolution_plan_template,
)
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.vocabulary import ATTACK_MODES, DAMAGE_TYPES


class StatblockImportError(ValueError):
    """Raised when required statblock facts cannot be recovered from the source text."""


OCR_STATBLOCK_RECOVERY_VERSION = 16


@dataclass(frozen=True)
class ParsedStatblock:
    name: str
    summary: str
    sheet: dict[str, Any]
    challenge_rating: str
    experience_points: int | None
    warnings: tuple[str, ...]
    normalization_notes: tuple[str, ...] = ()
    spellcasting: dict[str, Any] | None = None


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
    "once": 1,
    "two": 2,
    "twice": 2,
    "three": 3,
    "thrice": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
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
    result = ascii_slug(value)
    if len(result) > 80:
        result = f"{result[:63].rstrip('-')}-{hashlib.sha256(result.encode()).hexdigest()[:12]}"
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
    empty_markers = {"-", "–", "—", "none"}
    return [
        item.strip()
        for item in re.split(r"[,;]", value)
        if item.strip() and item.strip().casefold() not in empty_markers
    ]


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
    matched_distance = False
    for part in _split_list(value):
        match = re.search(r"(?i)(?:(fly|swim|climb|burrow)\s+)?(\d+)\s*ft", part)
        if match:
            matched_distance = True
            speeds[(match.group(1) or "walk").casefold()] = int(match.group(2))
    if not matched_distance:
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
    return dict(zip(ABILITY_NAMES, scores, strict=True))


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
        wisdom_modifier = ability_modifier(ability_scores["wisdom"])
        perception = sheet["skills"]["perception"]
        calculated = 10 + wisdom_modifier + int(perception.get("bonus", 0) or 0)
        sheet["traits"]["senses"]["passive_perception_bonus"] = (
            int(passive.group(1)) - calculated
        )


def _base_statblock_markdown(markdown: str) -> str:
    """Exclude explicitly labeled variants from the immutable base creature card."""

    variant = re.search(r"(?im)^>\s*\*\*Variant:", markdown)
    return markdown[: variant.start()] if variant else markdown


def split_2014_statblock_action_variants(
    markdown: str,
) -> list[dict[str, str]]:
    """Split a shared statblock with two or more named action-set variants.

    Some official cards print one common core followed by headings such as
    ``Actions for Type 1``. Each action set is a complete selectable actor
    form; flattening all of them into one inventory creates duplicate action
    identifiers. This splitter relies only on explicit source headings and
    never invents a variant or combines action sets.
    """

    heading_pattern = re.compile(r"(?im)^#{2,6}\s+(.+?)\s*$")
    headings = list(heading_pattern.finditer(markdown))
    variants: list[tuple[re.Match[str], str]] = []
    for heading in headings:
        match = re.fullmatch(
            r"(?i)Actions\s+For\s+(.+)",
            heading.group(1).strip(),
        )
        if match is not None:
            variants.append((heading, " ".join(match.group(1).split())))
    if len(variants) < 2 or len(
        {label.casefold() for _heading, label in variants}
    ) != len(variants):
        return []
    root = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown)
    if root is None:
        return []
    first_variant_start = variants[0][0].start()
    ranges: list[tuple[int, int, str, str]] = []
    for variant_index, (heading, label) in enumerate(variants):
        next_heading = next(
            (candidate for candidate in headings if candidate.start() > heading.start()),
            None,
        )
        end = next_heading.start() if next_heading is not None else len(markdown)
        if (
            next_heading is not None
            and variant_index < len(variants) - 1
            and next_heading is not variants[variant_index + 1][0]
        ):
            # A non-variant section between variant action sets is ambiguous.
            return []
        body = markdown[heading.end() : end].strip()
        if not body or not re.search(
            r"(?<!\*)\*\*\*.+?(?:\.\*\*\*|\*\*\*\.)",
            body,
        ):
            return []
        ranges.append((heading.start(), end, label, body))
    common_after = markdown[ranges[-1][1] :]
    common_before = markdown[:first_variant_start].rstrip()
    result: list[dict[str, str]] = []
    base_name = " ".join(root.group(1).split())
    for _start, _end, label, body in ranges:
        variant_name = f"{base_name} ({label})"
        prefix = re.sub(
            r"(?m)^#{1,6}\s+.+?\s*$",
            f"# {variant_name}",
            common_before,
            count=1,
        )
        normalized = (
            prefix
            + "\n\n## Actions\n\n"
            + body
            + ("\n\n" + common_after.lstrip() if common_after.strip() else "")
        ).strip() + "\n"
        result.append(
            {
                "label": label,
                "name": variant_name,
                "normalized_content": normalized,
            }
        )
    return result


def _entry_blocks(markdown: str) -> list[tuple[str, str, str]]:
    markdown = _base_statblock_markdown(markdown)
    markers = list(
        re.finditer(
            r"(?<!\*)\*\*\*(.+?)(?:\.\*\*\*|\*\*\*\.)\s*",
            markdown,
        )
    )
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
        description = markdown[marker.end() : end].replace("\r\n", "\n").replace("\r", "\n")
        description = re.sub(r"[^\S\n]+", " ", description)
        description = re.sub(r"\n[ \t]*\n+", "\n\n", description)
        description = re.sub(r"(?<!\n)\n(?!\n)", " ", description).strip()
        result.append((section, marker.group(1).strip(), description))
    return result


def _trailing_standard_ammunition(
    description: str,
    *,
    actor_name: str,
    weapon_name: str,
    source_key: str,
) -> tuple[int, dict[str, Any]] | None:
    """Recover an explicit standard ammunition count after a weapon action."""

    normalized_weapon = " ".join(weapon_name.casefold().split())
    ammunition_patterns = (
        (r"sling", "", r"sling\s+stones?", "Sling Stones"),
        (
            r"(?:short|long)bow",
            r"(?:a\s+quiver\s+of\s+)?",
            r"arrows?",
            "Arrows",
        ),
        (
            r"(?:hand|light|heavy)\s+crossbow",
            "",
            r"(?:crossbow\s+)?bolts?",
            "Crossbow Bolts",
        ),
        (r"blowgun", "", r"needles?", "Blowgun Needles"),
    )
    ammunition = next(
        (
            (prefix, pattern, label)
            for weapon_pattern, prefix, pattern, label in ammunition_patterns
            if re.fullmatch(weapon_pattern, normalized_weapon)
        ),
        None,
    )
    actor_tokens = [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z'\-]*", actor_name)
        if token
    ]
    aliases = list(
        dict.fromkeys(
            [
                actor_name.strip(),
                *(actor_tokens[:1] if actor_tokens else []),
                *(actor_tokens[-1:] if len(actor_tokens) > 1 else []),
            ]
        )
    )
    if ammunition is None or not aliases:
        return None
    ammunition_prefix, ammunition_pattern, ammunition_label = ammunition
    count_pattern = "|".join(
        [
            r"\d+",
            *(
                re.escape(value)
                for value in sorted(_NUMBER_WORDS, key=len, reverse=True)
                if value not in {"once", "twice", "thrice"}
            ),
        ]
    )
    match = re.search(
        (
            rf"(?i)(?:^|(?<=[.!?])\s+)"
            rf"(?:{'|'.join(re.escape(alias) for alias in aliases)})\s+"
            rf"carries\s+{ammunition_prefix}(?P<count>{count_pattern})\s+"
            rf"{ammunition_pattern}\s*\.?\s*$"
        ),
        description,
    )
    if match is None:
        return None
    count = _NUMBER_WORDS.get(match.group("count").casefold())
    if count is None:
        count = int(match.group("count"))
    item_id = f"{_slug(weapon_name)}-ammunition"
    return (
        match.start(),
        {
            "id": item_id,
            "name": ammunition_label,
            "kind": "ammunition",
            "quantity": count,
            "source_key": source_key,
            "description": match.group(0).strip(),
        },
    )


def _parse_weapon(
    name: str,
    description: str,
    source_key: str,
    *,
    actor_name: str = "",
) -> dict[str, Any] | None:
    attack = re.match(
        r"(?i)\*?(Melee|Ranged|Melee or Ranged)\s+"
        r"(?:(Weapon|Spell)\s+)?Attack(?:\s+Roll)?:\*?\s*"
        r"([+\-−]\s*\d+)\s+to hit",
        description,
    )
    if not attack:
        return None
    mode = attack.group(1).casefold()
    hit = re.search(
        r"(?i)\*?Hit:\*?\s*(?:"
        r"(?P<average>\d+)(?:\s*\((?P<formula>\d+\s*d\s*\d+"
        r"(?:\s*[+\-]\s*\d+)?(?:\s+plus\s+\d+\s*d\s*\d+"
        r"(?:\s*[+\-]\s*\d+)?)*)\))?"
        r"|(?P<bare_formula>\d+\s*d\s*\d+(?:\s*[+\-]\s*\d+)?"
        r"(?:\s+plus\s+\d+\s*d\s*\d+(?:\s*[+\-]\s*\d+)?)*)"
        r")\s*(?P<damage_type>[a-z]+)\s+damage",
        description,
    )
    additional_damage: list[dict[str, Any]] = []
    versatile_additional_damage: list[dict[str, Any]] = []
    complete_structured_on_hit: dict[str, Any] | None = None
    if hit:
        expression_parts = re.split(
            r"(?i)\s+plus\s+",
            hit.group("formula")
            or hit.group("bare_formula")
            or hit.group("average"),
        )
        expression = re.sub(r"\s+", "", expression_parts[0])
        damage = re.fullmatch(r"(\d+d\d+)(?:([+\-]\d+))?", expression)
        if (hit.group("formula") or hit.group("bare_formula")) and not damage:
            raise StatblockImportError(
                f"weapon action {name!r} has an invalid damage expression"
            )
        for raw_extra_expression in expression_parts[1:]:
            extra_expression = re.sub(r"\s+", "", raw_extra_expression)
            parsed_extra = re.fullmatch(
                r"(\d+d\d+)(?:([+\-]\d+))?",
                extra_expression,
            )
            if not parsed_extra:
                raise StatblockImportError(
                    f"weapon action {name!r} has an invalid additional damage expression"
                )
            additional_damage.append(
                {
                    "damage_formula": parsed_extra.group(1),
                    "damage_bonus": int(parsed_extra.group(2) or 0),
                    "damage_type": hit.group("damage_type").casefold(),
                }
            )
        last_damage_end = hit.end()
        versatile_damage_formula = ""
        versatile = re.match(
            (
                r"(?i)\s*,?\s*or\s+\d+\s*"
                r"\((\d+\s*d\s*\d+)(?:\s*([+\-])\s*(\d+))?\)\s*"
                rf"{re.escape(hit.group('damage_type'))}\s+damage\s+"
                r"if\s+used\s+with\s+two\s+hands"
                r"(?:\s+to\s+make\s+a\s+melee\s+attack)?"
            ),
            description[hit.end() :],
        )
        if versatile:
            versatile_bonus = int(
                f"{versatile.group(2) or '+'}{versatile.group(3) or '0'}"
            )
            if versatile_bonus == (int(damage.group(2) or 0) if damage else 0):
                versatile_damage_formula = re.sub(r"\s+", "", versatile.group(1))
                last_damage_end = hit.end() + versatile.end()
        for extra in re.finditer(
            r"(?i)\bplus\s+\d+\s*\((\d+\s*d\s*\d+"
            r"(?:\s*[+\-]\s*\d+)?)\)\s*"
            r"([a-z]+)\s+damage",
            description[hit.end() :],
        ):
            extra_expression = re.sub(r"\s+", "", extra.group(1))
            parsed_extra = re.fullmatch(r"(\d+d\d+)(?:([+\-]\d+))?", extra_expression)
            if not parsed_extra:
                raise StatblockImportError(
                    f"weapon action {name!r} has an invalid additional damage expression"
                )
            damage_part = {
                "damage_formula": parsed_extra.group(1),
                "damage_bonus": int(parsed_extra.group(2) or 0),
                "damage_type": extra.group(2).casefold(),
            }
            additional_damage.append(damage_part)
            if versatile is not None and extra.start() >= versatile.end():
                versatile_additional_damage.append(deepcopy(damage_part))
            last_damage_end = hit.end() + extra.end()
        raw_on_hit_effect = description[last_damage_end:]
        trailing_paragraph_prose = ""
        normalized_actor_name = actor_name.strip()
        actor_lore_candidate = re.sub(r"[*_`~]", " ", raw_on_hit_effect)
        actor_lore_match = (
            re.search(
                (
                    rf"(?i)(?:^\s*|(?<=[.!?])\s+)"
                    rf"[^.!?]*?"
                    rf"(?:(?:a|an|the)\s+)?"
                    rf"(?:{re.escape(normalized_actor_name)}"
                    rf"|{re.escape(normalized_actor_name.split()[-1])})s?\b"
                ),
                actor_lore_candidate,
            )
            if normalized_actor_name
            else None
        )
        carries_standard_ammunition = _trailing_standard_ammunition(
            raw_on_hit_effect,
            actor_name=actor_name,
            weapon_name=name,
            source_key=source_key,
        )
        if (
            actor_lore_match is not None
            and carries_standard_ammunition is None
            and not _looks_like_mechanical_on_hit_suffix(
                raw_on_hit_effect[actor_lore_match.start() :]
            )
        ):
            trailing_paragraph_prose = raw_on_hit_effect[
                actor_lore_match.start() :
            ].strip()
            raw_on_hit_effect = raw_on_hit_effect[
                : actor_lore_match.start()
            ].strip()
        complete_structured_on_hit = (
            _armor_corrosion_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
            or _ignition_ongoing_damage_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
            or _saving_throw_damage_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
            or _contest_pull_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
        )
        trailing_paragraph_match = re.search(r"\n\s*\n", raw_on_hit_effect)
        if trailing_paragraph_match and complete_structured_on_hit is None:
            trailing_paragraph_prose = raw_on_hit_effect[
                trailing_paragraph_match.end() :
            ].strip()
            raw_on_hit_effect = raw_on_hit_effect[: trailing_paragraph_match.start()]
        on_hit_effect = raw_on_hit_effect.strip().lstrip(". ,;").strip()
        damage_formula = damage.group(1) if damage else expression
        damage_type = hit.group("damage_type").casefold()
        damage_bonus = int(damage.group(2) or 0) if damage else 0
    else:
        effect_hit = re.search(r"(?is)\*?Hit:\*?\s*(\S.+)$", description)
        if not effect_hit:
            raise StatblockImportError(
                f"weapon action {name!r} has neither supported Hit damage nor an effect"
            )
        damage_formula = ""
        damage_type = ""
        damage_bonus = 0
        on_hit_effect = effect_hit.group(1).strip()
        versatile_damage_formula = ""
        trailing_paragraph_prose = ""
    trailing_prose = ""
    trailing_warning = ""
    ammunition_item: dict[str, Any] | None = None
    carried_ammunition = _trailing_standard_ammunition(
        on_hit_effect,
        actor_name=actor_name,
        weapon_name=name,
        source_key=source_key,
    )
    if carried_ammunition is not None:
        ammunition_start, ammunition_item = carried_ammunition
        trailing_prose = on_hit_effect[ammunition_start:].strip()
        on_hit_effect = on_hit_effect[:ammunition_start].rstrip(" .;,")
        trailing_warning = (
            f"{name}: trailing ammunition inventory structured separately "
            "from action settlement"
        )
    normalized_actor_name = actor_name.strip()
    unformatted_on_hit_effect = re.sub(
        r"^[\s*_`~]+",
        "",
        on_hit_effect,
    )
    actor_lore_candidate = re.sub(r"[*_`~]", " ", on_hit_effect)
    actor_lore_match = (
        re.search(
            (
                rf"(?i)(?:^\s*|(?<=[.!?])\s+)"
                rf"[^.!?]*?"
                rf"(?:(?:a|an|the)\s+)?"
                rf"(?:{re.escape(normalized_actor_name)}"
                rf"|{re.escape(normalized_actor_name.split()[-1])})s?\b"
            ),
            actor_lore_candidate,
        )
        if normalized_actor_name
        else None
    )
    paragraph_break = re.search(r"\n\s*\n", on_hit_effect)
    if trailing_prose:
        pass
    elif trailing_paragraph_prose:
        trailing_prose = trailing_paragraph_prose
        trailing_warning = (
            f"{name}: trailing page furniture excluded from action settlement"
            if re.fullmatch(
                r"(?i)(?:page\s+)?\d{1,4}",
                re.sub(r"^[\s*_`~]+", "", trailing_paragraph_prose),
            )
            else f"{name}: trailing creature prose excluded from action settlement"
        )
    elif paragraph_break and complete_structured_on_hit is None:
        trailing_prose = on_hit_effect[paragraph_break.end() :].strip()
        on_hit_effect = on_hit_effect[: paragraph_break.start()].strip()
        trailing_warning = f"{name}: trailing creature prose excluded from action settlement"
    elif re.fullmatch(r"(?i)(?:page\s+)?\d{1,4}", unformatted_on_hit_effect):
        trailing_prose = on_hit_effect
        on_hit_effect = ""
        trailing_warning = f"{name}: trailing page furniture excluded from action settlement"
    elif (
        actor_lore_match
        and not _looks_like_mechanical_on_hit_suffix(
            on_hit_effect[actor_lore_match.start() :]
        )
    ):
        trailing_prose = on_hit_effect[actor_lore_match.start() :].strip()
        on_hit_effect = on_hit_effect[: actor_lore_match.start()].strip()
        trailing_warning = f"{name}: trailing creature prose excluded from action settlement"
    reach = re.search(r"(?i)reach\s+(\d+)\s*ft", description)
    # Some text-only OCR pipelines confuse the slash in a weapon's two-part
    # range with ``f``.  Recover that glyph only inside the complete attack
    # range grammar; the source description itself remains unchanged.
    ranges = re.search(
        (
            r"(?i)\brange\s+(\d+)"
            r"(?:(?:(?:\s*ft\.?)?\s*/\s*|\s*f\s*)(\d+))?"
            r"\s*ft\.?"
        ),
        description,
    )
    properties: list[str] = []
    if mode == "melee or ranged":
        properties.append("thrown")
    if versatile_damage_formula:
        properties.append("versatile")
    mechanics: dict[str, Any] = {
        "attack_type": "ranged" if mode == "ranged" else "melee",
        "attack_ability": (
            "spell"
            if str(attack.group(2) or "").casefold() == "spell"
            else "dexterity"
            if mode == "ranged"
            else "strength"
        ),
        "damage_formula": damage_formula,
        "damage_type": damage_type,
        "additional_damage": additional_damage,
        "versatile_additional_damage": versatile_additional_damage,
        "on_hit_effect": on_hit_effect,
        "versatile_damage_formula": versatile_damage_formula,
        "properties": properties,
        "proficient": False,
        "attack_bonus_override": _signed(attack.group(3).replace("−", "-")),
        "damage_bonus_override": damage_bonus,
        "reach_ft": int(reach.group(1)) if reach else 5,
        "always_available": True,
    }
    structured_on_hit = (
        _armor_corrosion_on_hit(on_hit_effect)
        or _ignition_ongoing_damage_on_hit(on_hit_effect)
        or _saving_throw_damage_on_hit(on_hit_effect)
        or _contest_pull_on_hit(on_hit_effect)
    )
    if structured_on_hit is not None:
        mechanics["on_hit_resolution"] = structured_on_hit
        mechanics["on_hit_effect"] = ""
    advantage_target = re.search(
        (
            r"(?i)\bone\s+"
            r"(?P<sizes>Tiny|Small|Medium|Large|Huge|Gargantuan)"
            r"(?:\s+or\s+(?P<second_size>Tiny|Small|Medium|Large|Huge|Gargantuan))?"
            r"\s+creature\s+against\s+which\s+"
            r"(?:the\s+[a-z][a-z '\-]*|it|he|she|they)\s+has\s+advantage\s+"
            r"on\s+the\s+attack\s+roll\b"
        ),
        description,
    )
    if advantage_target:
        mechanics["required_target_sizes"] = list(
            dict.fromkeys(
                [
                    advantage_target.group("sizes").casefold(),
                    *(
                        [advantage_target.group("second_size").casefold()]
                        if advantage_target.group("second_size")
                        else []
                    ),
                ]
            )
        )
        mechanics["requires_attack_advantage"] = True
    if ranges:
        mechanics["normal_range_ft"] = int(ranges.group(1))
        mechanics["long_range_ft"] = int(ranges.group(2) or ranges.group(1))
        if mode == "melee or ranged":
            mechanics["thrown_normal_range_ft"] = int(ranges.group(1))
            mechanics["thrown_long_range_ft"] = int(ranges.group(2) or ranges.group(1))
    if ammunition_item is not None:
        mechanics["properties"] = [
            *list(mechanics["properties"]),
            "ammunition",
        ]
        mechanics["ammunition_item_id"] = ammunition_item["id"]
    result = {
        "id": _slug(name),
        "name": name,
        "kind": "weapon",
        "description": description,
        "source_key": source_key,
        "mechanics": mechanics,
    }
    if trailing_prose:
        result["_normalization_note"] = trailing_warning
    if ammunition_item is not None:
        result["_ammunition_item"] = ammunition_item
    return result


def _looks_like_mechanical_on_hit_suffix(description: str) -> bool:
    normalized = " ".join(description.split())
    return (
        re.search(
            r"(?i)\b(?:the target|target|creature)\b.{0,160}\b"
            r"(?:must|saving throw|contest|pulled|pushed|grappled|"
            r"restrained|knocked|feet)\b",
            normalized,
        )
        is not None
    )


def _armor_corrosion_on_hit(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"In addition, non\s*magical armor worn by the target is partly dissolved "
        r"and takes a permanent and cumulative (?P<penalty>-\s*\d+) penalty "
        r"to the AC it offers\. The armor is destroyed if the penalty reduces "
        r"its AC\s*to (?P<destroyed_ac>\d+)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    penalty = int(match.group("penalty").replace(" ", ""))
    if penalty >= 0:
        return None
    return {
        "kind": "armor_corrosion",
        "trigger": "weapon_hit",
        "requires_worn_armor": True,
        "requires_nonmagical_armor": True,
        "armor_class_penalty": penalty,
        "destroyed_at_armor_class": int(match.group("destroyed_ac")),
        "automatic": True,
        "source_excerpt": normalized,
    }


def _ignition_ongoing_damage_on_hit(
    description: str,
) -> dict[str, Any] | None:
    """Compile the complete standard ignite-and-douse weapon rider."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"If the target is a creature or a flammable object, it ignites\. "
        r"Until a (?:creature|target) takes an action to douse the fire, "
        r"the (?:creature|target) takes (?P<average>\d+) "
        r"\((?P<formula>\d+d\d+)\) (?P<damage_type>[a-z]+) damage at the "
        r"(?P<timing>start|end) of each of its turns\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "ignition_ongoing_damage",
        "trigger": "weapon_hit",
        "creature_target_automatic": True,
        "flammable_object_requires_scene_fact": True,
        "damage_formula": match.group("formula").casefold(),
        "average_damage": int(match.group("average")),
        "damage_type": match.group("damage_type").casefold(),
        "trigger_timing": f"turn_{match.group('timing').casefold()}",
        "end_action": "use_object",
        "end_action_description": "douse the fire",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _saving_throw_damage_on_hit(description: str) -> dict[str, Any] | None:
    """Compile a weapon rider that deals save-for-half damage on a hit."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"and the target must make a DC (?P<dc>\d+) "
        r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
        r"saving throw, taking (?P<average>\d+) "
        r"\((?P<formula>\d+d\d+(?:\s*[+\-]\s*\d+)?)\) "
        r"(?P<damage_type>[a-z]+) damage on a failed save, or half as much "
        r"damage on a successful one\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "save_damage",
        "trigger": "weapon_hit",
        "save_ability": match.group("ability").casefold(),
        "save_dc": int(match.group("dc")),
        "damage_formula": re.sub(r"\s+", "", match.group("formula")).casefold(),
        "average_damage": int(match.group("average")),
        "damage_type": match.group("damage_type").casefold(),
        "half_on_success": True,
        "save_source_kind": "nonmagical_effect",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _contest_pull_on_hit(description: str) -> dict[str, Any] | None:
    """Compile a size-limited Strength contest that pulls toward the attacker."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"If the target is a (?P<maximum_size>Tiny|Small|Medium|Large|Huge|Gargantuan) "
        r"or smaller creature, it must succeed on a (?P<ability>Strength|Dexterity|"
        r"Constitution|Intelligence|Wisdom|Charisma) contest against the "
        r"[A-Za-z][A-Za-z '\-]* or be pulled up to (?P<distance>\d+) feet "
        r"toward the [A-Za-z][A-Za-z '\-]*\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "contest_pull",
        "trigger": "weapon_hit",
        "required_target_kind": "creature",
        "maximum_target_size": match.group("maximum_size").casefold(),
        "source_ability": match.group("ability").casefold(),
        "target_ability": match.group("ability").casefold(),
        "ties": "no_movement",
        "maximum_distance_ft": int(match.group("distance")),
        "direction": "toward_source",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _count(value: str) -> int | None:
    value = value.casefold().strip()
    if value.isdigit():
        return int(value)
    return _NUMBER_WORDS.get(value)


def _weapon_id(value: str, weapons: dict[str, str]) -> str | None:
    """Resolve one printed attack label without guessing across ambiguities.

    Statblocks commonly refer to ``Claws (Slaad Form Only)`` as ``claws`` in
    Multiattack and alternate singular/plural labels such as ``talon`` and
    ``Talons``.  Only aliases that identify exactly one parsed action are
    accepted; a colliding short label deliberately remains unresolved.
    """

    normalized = re.sub(r"[^a-z0-9 ]", "", value.casefold()).strip()

    def number_aliases(label: str) -> set[str]:
        aliases = {label}
        if label.endswith("ies") and len(label) > 3:
            aliases.add(f"{label[:-3]}y")
        elif label.endswith("es") and len(label) > 2:
            aliases.add(label[:-2])
        elif label.endswith("s") and len(label) > 1:
            aliases.add(label[:-1])
        else:
            aliases.add(f"{label}s")
            aliases.add(f"{label}es")
        return aliases

    matches: set[str] = set()
    requested = number_aliases(normalized)
    for weapon_name, weapon_id in weapons.items():
        base_name = re.sub(r"\s*\([^)]*\)\s*$", "", weapon_name).strip()
        aliases = number_aliases(weapon_name) | number_aliases(base_name)
        if requested & aliases:
            matches.add(weapon_id)
            continue
        # A source can shorten ``tail stinger`` to ``stinger``.  Require a
        # complete trailing word sequence and uniqueness across all actions.
        if any(
            alias.endswith(f" {candidate}") or alias.startswith(f"{candidate} ")
            for alias in aliases
            for candidate in requested
            if candidate
        ):
            matches.add(weapon_id)
    return next(iter(matches)) if len(matches) == 1 else None


def _parse_multiattack(description: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weapon_aliases: dict[str, set[str]] = {}
    for item in items:
        raw_name = str(item["name"])
        for name in (
            raw_name,
            re.sub(r"\s*\([^)]*\)\s*$", "", raw_name).strip(),
        ):
            alias = re.sub(r"[^a-z0-9 ]", "", name.casefold()).strip()
            if alias:
                weapon_aliases.setdefault(alias, set()).add(str(item["id"]))
    weapons = {
        alias: next(iter(weapon_ids))
        for alias, weapon_ids in weapon_aliases.items()
        if len(weapon_ids) == 1
    }

    def weapon_modes(weapon_id: str) -> list[str]:
        weapon = next(item for item in items if item["id"] == weapon_id)
        mechanics = dict(weapon.get("mechanics") or {})
        modes = [str(mechanics.get("attack_type") or "melee")]
        if "thrown" in {
            str(value).casefold()
            for value in mechanics.get("properties") or []
        }:
            modes.append("ranged")
        return list(dict.fromkeys(modes))

    sentence_groups = re.split(r"(?i)\.\s*(?:Or\s+)?", description)
    options: list[dict[str, Any]] = []
    for group in sentence_groups:
        repeated_use = re.search(
            r"(?i)\buses?\s+(?:(?:its|his|her|their)\s+)?"
            r"(?P<weapon>[a-z][a-z '\-]+?)\s+"
            r"(?P<count>once|twice|thrice)\s*$",
            group,
        )
        if repeated_use is not None:
            count = _count(repeated_use.group("count"))
            weapon_id = _weapon_id(repeated_use.group("weapon"), weapons)
            if count is None or weapon_id is None:
                return []
            options.extend(
                {
                    "id": mode,
                    "attacks": [
                        {
                            "weapon_id": weapon_id,
                            "attack_mode": mode,
                            "count": count,
                        }
                    ],
                }
                for mode in weapon_modes(weapon_id)
            )
            continue
        if "attack" not in group.casefold() and "strike" not in group.casefold():
            continue
        complete_alternatives = re.search(
            r"(?i)\bmakes?\s+"
            r"(?P<total>one|two|three|four|five|six|\d+)\s+attacks?\s*:\s*"
            r"(?P<required_count>one|two|three|four|five|six|\d+)\s+with\s+"
            r"(?:its|his|her|their)\s+(?P<required>[a-z][a-z '\-]+?)\s+and\s+"
            r"(?P<secondary_count>one|two|three|four|five|six|\d+)\s+with\s+"
            r"(?:its|his|her|their)\s+(?P<secondary>[a-z][a-z '\-]+?)\s+or\s+"
            r"(?P<alternative_count>one|two|three|four|five|six|\d+)\s+"
            r"(?:(?:melee|ranged)\s+attacks?\s+)?with\s+"
            r"(?:its|his|her|their)\s+(?P<alternative>[a-z][a-z '\-]+?)\s*$",
            group,
        )
        if complete_alternatives is not None:
            total = _count(complete_alternatives.group("total"))
            required_count = _count(
                complete_alternatives.group("required_count")
            )
            secondary_count = _count(
                complete_alternatives.group("secondary_count")
            )
            alternative_count = _count(
                complete_alternatives.group("alternative_count")
            )
            required_id = _weapon_id(
                complete_alternatives.group("required"), weapons
            )
            secondary_id = _weapon_id(
                complete_alternatives.group("secondary"), weapons
            )
            alternative_id = _weapon_id(
                complete_alternatives.group("alternative"), weapons
            )
            if (
                total is None
                or required_count is None
                or secondary_count is None
                or alternative_count is None
                or total != required_count + secondary_count
                or total != alternative_count
                or required_id is None
                or secondary_id is None
                or alternative_id is None
            ):
                return []
            for required_mode in weapon_modes(required_id):
                for secondary_mode in weapon_modes(secondary_id):
                    options.append(
                        {
                            "id": (
                                required_mode
                                if required_mode == secondary_mode
                                else "mixed"
                            ),
                            "attacks": [
                                {
                                    "weapon_id": required_id,
                                    "attack_mode": required_mode,
                                    "count": required_count,
                                },
                                {
                                    "weapon_id": secondary_id,
                                    "attack_mode": secondary_mode,
                                    "count": secondary_count,
                                },
                            ],
                        }
                    )
            for alternative_mode in weapon_modes(alternative_id):
                options.append(
                    {
                        "id": alternative_mode,
                        "attacks": [
                            {
                                "weapon_id": alternative_id,
                                "attack_mode": alternative_mode,
                                "count": alternative_count,
                            }
                        ],
                    }
                )
            continue
        required_plus_alternative = re.search(
            r"(?i)\bmakes?\s+"
            r"(?P<total>one|two|three|four|five|six|\d+)\s+attacks?\s*:\s*"
            r"(?P<required_count>one|two|three|four|five|six|\d+)\s+with\s+"
            r"(?:its|his|her|their)\s+(?P<required>[a-z][a-z '\-]+?)\s+and\s+"
            r"(?P<alternative_count>one|two|three|four|five|six|\d+)\s+with\s+"
            r"(?:its|his|her|their)\s+(?P<first>[a-z][a-z '\-]+?)\s+or\s+"
            r"(?:(?:its|his|her|their)\s+)?(?P<second>[a-z][a-z '\-]+?)\s*$",
            group,
        )
        if required_plus_alternative is not None:
            total = _count(required_plus_alternative.group("total"))
            required_count = _count(
                required_plus_alternative.group("required_count")
            )
            alternative_count = _count(
                required_plus_alternative.group("alternative_count")
            )
            required_id = _weapon_id(
                required_plus_alternative.group("required"),
                weapons,
            )
            alternative_ids = [
                _weapon_id(required_plus_alternative.group("first"), weapons),
                _weapon_id(required_plus_alternative.group("second"), weapons),
            ]
            if (
                total is None
                or required_count is None
                or alternative_count is None
                or total != required_count + alternative_count
                or required_id is None
                or any(weapon_id is None for weapon_id in alternative_ids)
            ):
                return []
            for required_mode in weapon_modes(required_id):
                for alternative_id in alternative_ids:
                    assert alternative_id is not None
                    for alternative_mode in weapon_modes(alternative_id):
                        option_mode = (
                            required_mode
                            if required_mode == alternative_mode
                            else "mixed"
                        )
                        options.append(
                            {
                                "id": option_mode,
                                "attacks": [
                                    {
                                        "weapon_id": required_id,
                                        "attack_mode": required_mode,
                                        "count": required_count,
                                    },
                                    {
                                        "weapon_id": alternative_id,
                                        "attack_mode": alternative_mode,
                                        "count": alternative_count,
                                    },
                                ],
                            }
                        )
            continue
        named_alternatives = re.search(
            r"(?i)\bmakes?\s+"
            r"(one|once|two|twice|three|thrice|four|five|six|\d+)\s+attacks?\s+"
            r"with\s+(?:its|his|her|their)\s+"
            r"([a-z][a-z '\-]+?)\s+or\s+"
            r"(?:(?:its|his|her|their)\s+)?([a-z][a-z '\-]+?)\s*$",
            group,
        )
        if named_alternatives is not None:
            count = _count(named_alternatives.group(1))
            alternatives = [
                _weapon_id(named_alternatives.group(2), weapons),
                _weapon_id(named_alternatives.group(3), weapons),
            ]
            if count is None or any(weapon_id is None for weapon_id in alternatives):
                return []
            for weapon_id in alternatives:
                assert weapon_id is not None
                options.extend(
                    {
                        "id": mode,
                        "attacks": [
                            {
                                "weapon_id": weapon_id,
                                "attack_mode": mode,
                                "count": count,
                            }
                        ],
                    }
                    for mode in weapon_modes(weapon_id)
                )
            continue
        repeated_action_alternatives = re.search(
            r"(?i)\bmakes?\s+"
            r"(?P<first_count>one|two|three|four|five|six|\d+)\s+"
            r"(?P<first>[a-z][a-z '\-]+?)\s+attacks?\s+or\s+"
            r"(?P<second_count>one|two|three|four|five|six|\d+)\s+"
            r"(?P<second>[a-z][a-z '\-]+?)\s+attacks?\s*$",
            group,
        )
        if repeated_action_alternatives is not None:
            declarations = [
                (
                    _count(repeated_action_alternatives.group("first_count")),
                    _weapon_id(repeated_action_alternatives.group("first"), weapons),
                ),
                (
                    _count(repeated_action_alternatives.group("second_count")),
                    _weapon_id(repeated_action_alternatives.group("second"), weapons),
                ),
            ]
            if any(
                count is None or weapon_id is None
                for count, weapon_id in declarations
            ):
                return []
            for count, weapon_id in declarations:
                assert count is not None and weapon_id is not None
                options.extend(
                    {
                        "id": mode,
                        "attacks": [
                            {
                                "weapon_id": weapon_id,
                                "attack_mode": mode,
                                "count": count,
                            }
                        ],
                    }
                    for mode in weapon_modes(weapon_id)
                )
            continue
        either_named_alternatives = re.search(
            r"(?i)\bmakes?\s+"
            r"(?P<count>one|two|three|four|five|six|\d+)\s+attacks?\s*,?\s*"
            r"either\s+with\s+(?:its|his|her|their)\s+"
            r"(?P<first>[a-z][a-z '\-]+?)\s+or\s+"
            r"(?:(?:its|his|her|their)\s+)?(?P<second>[a-z][a-z '\-]+?)\s*$",
            group,
        )
        if either_named_alternatives is not None:
            count = _count(either_named_alternatives.group("count"))
            alternatives = [
                _weapon_id(either_named_alternatives.group("first"), weapons),
                _weapon_id(either_named_alternatives.group("second"), weapons),
            ]
            if count is None or any(weapon_id is None for weapon_id in alternatives):
                return []
            for weapon_id in alternatives:
                assert weapon_id is not None
                options.extend(
                    {
                        "id": mode,
                        "attacks": [
                            {
                                "weapon_id": weapon_id,
                                "attack_mode": mode,
                                "count": count,
                            }
                        ],
                    }
                    for mode in weapon_modes(weapon_id)
                )
            continue
        alternative = re.search(
            r"(?i)\battacks?\s+"
            r"(one|once|two|twice|three|thrice|four|five|six|\d+)\s*,?\s+"
            r"either\s+with\s+(?:its|his|her|their)\s+"
            r"([a-z][a-z '\-]+?)\s+or\s+"
            r"(?:(?:its|his|her|their)\s+)?([a-z][a-z '\-]+?)\s*$",
            group,
        )
        if alternative is not None:
            count = _count(alternative.group(1))
            alternatives = [
                _weapon_id(alternative.group(2), weapons),
                _weapon_id(alternative.group(3), weapons),
            ]
            if count is None or any(weapon_id is None for weapon_id in alternatives):
                return []
            for weapon_id in alternatives:
                assert weapon_id is not None
                options.extend(
                    {
                        "id": mode,
                        "attacks": [
                            {
                                "weapon_id": weapon_id,
                                "attack_mode": mode,
                                "count": count,
                            }
                        ],
                    }
                    for mode in weapon_modes(weapon_id)
                )
            continue
        attack_mode = "ranged" if "ranged attack" in group.casefold() else "melee"
        attacks: list[dict[str, Any]] = []
        for match in re.finditer(
            r"(?i)\b(one|once|two|twice|three|thrice|four|five|six|\d+)\b"
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
        if not attacks:
            for match in re.finditer(
                r"(?i)\b(one|two|three|four|five|six|\d+)\s+"
                r"([a-z][a-z '\-]+?)\s+(?:attacks?|strikes?)"
                r"(?=\s+and\s+|\s*,\s*|\.|$)",
                group,
            ):
                count = _count(match.group(1))
                weapon_id = _weapon_id(match.group(2), weapons)
                if count is None or weapon_id is None:
                    break
                weapon = next(item for item in items if item["id"] == weapon_id)
                weapon_mode = str(
                    dict(weapon.get("mechanics") or {}).get("attack_type") or "melee"
                )
                attacks.append(
                    {
                        "weapon_id": weapon_id,
                        "attack_mode": weapon_mode,
                        "count": count,
                    }
                )
        if not attacks:
            generic_attacks = list(
                re.finditer(
                    r"(?i)\b"
                r"(one|two|three|four|five|six|\d+)\s+"
                    r"(melee|ranged)\s+(?:weapon\s+)?attacks?\b",
                    group,
                )
            )
            if generic_attacks:
                generic_options: list[dict[str, Any]] = []
                for generic in generic_attacks:
                    count = _count(generic.group(1))
                    generic_mode = generic.group(2).casefold()
                    compatible: list[dict[str, Any]] = []
                    for item in items:
                        mechanics = dict(item.get("mechanics") or {})
                        properties = {
                            str(value).casefold()
                            for value in mechanics.get("properties") or []
                        }
                        if generic_mode == "melee":
                            supported = mechanics.get("attack_type") == "melee"
                        else:
                            supported = (
                                mechanics.get("attack_type") == "ranged"
                                or "thrown" in properties
                            )
                        if supported:
                            compatible.append(item)
                    if count is None or len(compatible) != 1:
                        # Never publish only the uniquely inferred subset of a
                        # source action containing multiple legal branches.
                        return []
                    generic_options.append(
                        {
                            "id": generic_mode,
                            "attacks": [
                                {
                                    "weapon_id": compatible[0]["id"],
                                    "attack_mode": generic_mode,
                                    "count": count,
                                }
                            ],
                        }
                    )
                options.extend(generic_options)
                continue
        if attacks:
            if sum(int(item["count"]) for item in attacks) < 2:
                # A source Multiattack can combine one weapon attack with a
                # special action (for example, Claws plus Devour Intellect).
                # Until every constituent action is structured, exposing the
                # lone weapon as executable Multiattack would misrepresent the
                # authored action and is rejected by the combat engine anyway.
                return []
            options.append({"id": attack_mode, "attacks": attacks})
    ids: dict[str, int] = {}
    for option in options:
        base = option["id"]
        ids[base] = ids.get(base, 0) + 1
        if ids[base] > 1:
            option["id"] = f"{base}-{ids[base]}"
    return options


def _parse_spellcasting(
    description: str,
    *,
    innate: bool = False,
) -> dict[str, Any] | None:
    ability_match = re.search(
        r"(?i)(?:spellcasting ability is\s+"
        r"(?P<ability_after>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)"
        r"|uses\s+"
        r"(?P<ability_before>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)"
        r"\s+as\s+(?:its|his|her|their)\s+spellcasting ability)",
        description,
    )
    if not ability_match:
        return None
    save_match = re.search(r"(?i)spell save DC\s*(\d+)", description)
    attack_match = re.search(r"(?i)([+\-]\d+)\s+to hit with spell attacks", description)
    headers = list(
        re.finditer(
            (
                r"(?i)(At will|(?P<uses>[1-9]\d*)/day"
                r"(?P<each>\s+each)?)\s*:\s*"
                if innate
                else (
                    r"(?i)(Cantrips?\s*\(at will\)|"
                    r"([1-9])(?:st|nd|rd|th) level\s*\((\d+) slots?\))\s*:\s*"
                )
            ),
            description,
        )
    )
    if not headers:
        return None
    class_lists = [
        match.group(1).casefold()
        for match in re.finditer(
            r"(?i)\b(?:from\s+the\s+)?([A-Za-z]+)'s\s+spell\s+list\b",
            description,
        )
    ]
    if not class_lists:
        prepared_class = re.search(
            r"(?i)\bfollowing\s+([A-Za-z]+)\s+spells?\s+(?:prepared|known)\b",
            description,
        )
        if prepared_class:
            class_lists.append(prepared_class.group(1).casefold())
    spells: list[dict[str, Any]] = []
    slots: dict[str, int] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(description)
        spell_list_text = description[header.end() : end]
        # The standard Archmage marks pre-cast self buffs with asterisks and
        # explains the marker in a separate paragraph after the final spell
        # list.  Preserve that paragraph in the feature description, while
        # keeping it out of the final spell identity.
        spell_list_text = re.split(
            r"(?is)\n\s*\n\s*\*?The [A-Za-z][A-Za-z '\-]* casts these spells "
            r"on itself before combat\.\*?",
            spell_list_text,
            maxsplit=1,
        )[0]
        names = [
            re.sub(r"(?:\s*[-*]\s*)+$", "", item.strip()).lstrip("-* ")
            for item in spell_list_text.split(",")
        ]
        names = [
            re.sub(r"(?i)\bo\s+f\b", "of", item)
            for item in names
            if item
        ]
        if innate:
            at_will = header.group(1).casefold() == "at will"
            uses_per_day = (
                None if at_will else int(header.group("uses"))
            )
            for source_name in names:
                canonical_name = re.sub(
                    r"\s+\((?P<qualifier>[^()]*)\)\s*$",
                    "",
                    source_name,
                ).strip()
                qualifier_match = re.search(
                    r"\s+\((?P<qualifier>[^()]*)\)\s*$",
                    source_name,
                )
                spells.append(
                    {
                        "name": canonical_name,
                        "source_name": source_name,
                        "source_qualifier": (
                            qualifier_match.group("qualifier").strip()
                            if qualifier_match
                            else ""
                        ),
                        "level": None,
                        "at_will": at_will,
                        "uses_per_day": uses_per_day,
                        "uses_are_independent": bool(header.group("each"))
                        or len(names) == 1,
                        "usage_group": (
                            ""
                            if uses_per_day is None
                            else f"daily-{uses_per_day}-{index + 1}"
                        ),
                    }
                )
        else:
            level = int(header.group(2) or 0)
            if level:
                slots[str(level)] = int(header.group(3))
            for source_name in names:
                qualifier_match = re.search(
                    r"\s+\((?P<qualifier>[^()]*)\)\s*$",
                    source_name,
                )
                spells.append(
                    {
                        "name": (
                            source_name[: qualifier_match.start()].strip()
                            if qualifier_match
                            else source_name
                        ),
                        "source_name": source_name,
                        "source_qualifier": (
                            qualifier_match.group("qualifier").strip()
                            if qualifier_match
                            else ""
                        ),
                        "level": level,
                        "at_will": level == 0,
                    }
                )
    return {
        "ability": (
            ability_match.group("ability_after") or ability_match.group("ability_before")
        ).casefold(),
        "save_dc": int(save_match.group(1)) if save_match else None,
        "attack_bonus": int(attack_match.group(1)) if attack_match else None,
        "class_lists": list(dict.fromkeys(class_lists)),
        "slots": slots,
        "spells": spells,
        "innate": innate,
        "no_material_components": bool(
            innate
            and re.search(
                r"(?i)\brequir(?:e|ing)s?\s+no\s+material\s+components\b",
                description,
            )
        ),
        "description": description,
    }


def _spell_action_name(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", value).strip().casefold()


def _regeneration_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The (?P<subject>[A-Za-z][A-Za-z '\-]*) regains "
        r"(?P<amount>\d+) hit points at the start of its turn\. "
        r"If the (?P=subject) takes "
        r"(?P<damage_types>[a-z]+(?:\s+or\s+[a-z]+)+) damage, "
        r"this trait doesn't function at the start of the "
        r"(?P=subject)'s next turn\. The (?P=subject) dies only if it starts "
        r"its turn with 0 hit points and doesn't regenerate\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    damage_types = [
        item.strip().casefold()
        for item in re.split(r"\s+or\s+", match.group("damage_types"))
        if item.strip()
    ]
    if not damage_types or len(damage_types) != len(set(damage_types)):
        return None
    return {
        "kind": "regeneration",
        "trigger": "turn_start",
        "amount": int(match.group("amount")),
        "suppressed_by_damage_types": damage_types,
        "dies_at_zero_when_suppressed": True,
    }


def _pack_tactics_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The (?P<subject>[A-Za-z][A-Za-z '\-]*) has advantage on an attack "
        r"roll against a creature if at least one of the "
        r"(?P=subject)'s allies is within (?P<distance>\d+) feet of the creature "
        r"and the ally isn't incapacitated\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "pack_tactics",
        "trigger": "attack_roll",
        "ally_within_target_ft": int(match.group("distance")),
        "requires_ally_not_incapacitated": True,
        "grants": "advantage",
        "automatic": True,
    }


def _sunlight_sensitivity_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    if not re.fullmatch(
        r"While in sunlight, the [A-Za-z][A-Za-z '\-]* has disadvantage on "
        r"attack rolls, as well as on Wisdom \(Perception\) checks that rely "
        r"on sight\.",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    return {
        "kind": "sunlight_sensitivity",
        "trigger": "attack_roll_or_sight_perception",
        "environment_fact": "direct_sunlight",
        "grants": "disadvantage",
        "automatic": True,
    }


def _keen_perception_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* has advantage on Wisdom "
        r"\(Perception\) checks that rely on "
        r"(?P<senses>hearing|sight|hearing or sight)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    senses = [
        value.strip().casefold()
        for value in re.split(r"\s+or\s+", match.group("senses"))
    ]
    return {
        "kind": "keen_perception",
        "trigger": "perception_check",
        "senses": senses,
        "grants": "advantage",
        "automatic": True,
    }


def _magic_resistance_source_trait(description: str) -> dict[str, Any] | None:
    """Compile the complete standard Magic Resistance saving-throw rule."""

    normalized = " ".join(description.split())
    if not re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* has advantage on saving throws against "
        r"spells and other magical effects\.",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    return {
        "kind": "magic_resistance",
        "trigger": "saving_throw",
        "save_source_kinds": ["spell", "magical_effect"],
        "grants": "advantage",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _evasion_source_trait(description: str) -> dict[str, Any] | None:
    """Compile the complete 2014 monster Evasion damage settlement."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"If the (?P<subject>[A-Za-z][A-Za-z '\-]*) is subjected to an effect "
        r"that allows it to make a Dexterity saving throw to take only half "
        r"damage, the (?P=subject) instead takes no damage if it succeeds on "
        r"the saving throw, and only half damage if it fails\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "evasion",
        "trigger": "dexterity_save_for_half_damage",
        "save_ability": "dexterity",
        "ordinary_successful_save": "half",
        "successful_save": "none",
        "failed_save": "half",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _save_advantage_against_conditions_source_trait(
    description: str,
) -> dict[str, Any] | None:
    """Compile a standard trait granting save advantage against named states."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* has advantage on saving throws against "
        r"being (?P<first>charmed|frightened)"
        r"(?: or (?P<second>charmed|frightened))?\s*\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    conditions = [
        match.group("first").casefold(),
        *(
            [match.group("second").casefold()]
            if match.group("second")
            else []
        ),
    ]
    return {
        "kind": "save_advantage_against_conditions",
        "trigger": "saving_throw",
        "effect_conditions": conditions,
        "grants": "advantage",
        "automatic": True,
        "source_excerpt": normalized,
    }


def _breathing_media_source_trait(description: str) -> dict[str, Any] | None:
    """Compile an exact standard breathing capability for environment rulings."""

    normalized = " ".join(description.split())
    if (
        re.fullmatch(
            r"The [A-Za-z][A-Za-z '\-]* can breathe air and water\.",
            normalized,
            flags=re.IGNORECASE,
        )
        is None
    ):
        return None
    return {
        "kind": "breathing_media",
        "trigger": "environmental_breathing",
        "media": ["air", "water"],
        "automatic": True,
        "source_excerpt": normalized,
    }


def _assassinate_source_trait(description: str) -> dict[str, Any] | None:
    """Compile the complete standard Assassin opening-turn rule."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"During its first turn, the (?P<subject>[A-Za-z][A-Za-z '\-]*) has "
        r"advantage on attack rolls against any creature that hasn't taken a "
        r"turn\. Any hit the (?P=subject) scores against a surprised creature "
        r"is a critical hit\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "assassinate",
        "trigger": "attack_roll",
        "attacker_turn": "first",
        "advantage_if_target_has_not_taken_turn": True,
        "critical_on_hit_if_target_surprised": True,
        "automatic": True,
        "source_excerpt": normalized,
    }


def _aggressive_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    if not re.fullmatch(
        r"As a bonus action, the [A-Za-z][A-Za-z '\-]* can move up to its "
        r"speed toward a hostile creature that it can see\.",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    return {
        "kind": "aggressive",
        "trigger": "bonus_action",
        "maximum_movement": "speed",
        "requires_visible_hostile_target": True,
        "direction": "toward_target",
    }


def _cunning_action_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    if not re.fullmatch(
        r"On each of its turns, the [A-Za-z][A-Za-z '\-]* can use a bonus "
        r"action to take the Dash, Disengage, or Hide action\.",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    return {
        "kind": "cunning_action",
        "trigger": "bonus_action",
        "options": ["dash", "disengage", "hide"],
    }


def _included_weapon_damage_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* deals an extra (?P<average>\d+) "
        r"\((?P<formula>\d+d\d+)\) damage when it hits with a weapon attack "
        r"\(included in the attacks?\)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "included_weapon_damage",
        "trigger": "weapon_hit",
        "damage_formula": match.group("formula").casefold(),
        "average_damage": int(match.group("average")),
        "embedded_in_weapon_actions": True,
    }


def _heated_body_source_trait(description: str) -> dict[str, Any] | None:
    """Compile a complete standard contact-retaliation trait from its card."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"A creature that touches the (?P<subject>[A-Za-z][A-Za-z '\-]*) or hits "
        r"it with a melee attack while within (?P<range>\d+) feet of it takes "
        r"(?P<average>\d+) \((?P<formula>\d+d\d+)\) "
        r"(?P<damage_type>[a-z]+) damage\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "heated_body",
        "trigger": "contact_or_melee_hit",
        "melee_range_ft": int(match.group("range")),
        "contact_damage_formula": match.group("formula").casefold(),
        "average_damage": int(match.group("average")),
        "contact_damage_type": match.group("damage_type").casefold(),
        "automatic": True,
        "source_excerpt": normalized,
    }


def _heated_weapons_source_trait(description: str) -> dict[str, Any] | None:
    """Compile extra damage already printed into a card's weapon action."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"Any (?P<material>[a-z]+) melee weapon the "
        r"(?P<subject>[A-Za-z][A-Za-z '\-]*) wields deals an extra "
        r"(?P<average>\d+) \((?P<formula>\d+d\d+)\) "
        r"(?P<damage_type>[a-z]+) damage on a hit "
        r"\(included in the attack\)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "heated_weapons",
        "trigger": "weapon_hit",
        "required_weapon_material": match.group("material").casefold(),
        "required_weapon_category": "melee",
        "damage_formula": match.group("formula").casefold(),
        "average_damage": int(match.group("average")),
        "damage_type": match.group("damage_type").casefold(),
        "embedded_in_weapon_actions": True,
        "automatic": True,
        "source_excerpt": normalized,
    }


def _battle_cry_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"Each creature of the [^.]{1,80}?'s choice that is within "
        r"(?P<distance>\d+) feet of it, can hear it, and is not already "
        r"affected by Battle Cry gains advantage on attack rolls until the "
        r"start of the [^.]{1,80}?'s next turn\. The "
        r"[^.]{1,80}? can then make one attack as a bonus action\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "battle_cry",
        "trigger": "action",
        "uses": 1,
        "recovers_on": "long_rest",
        "range_ft": int(match.group("distance")),
        "requires_hearing": True,
        "grants": "attack_advantage",
        "duration": "until_source_next_turn_start",
        "grants_source_bonus_attack": True,
    }


def _death_burst_source_trait(description: str) -> dict[str, Any] | None:
    """Compile the standard death-triggered area save and damage grammar."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"When the (?P<subject>[A-Za-z][A-Za-z '\-]*) dies, "
        r"(?P<preamble>[^.]{1,180}\.) "
        r"Each creature within (?P<range>\d+) (?:feet|ft\.) of it must make a "
        r"DC (?P<dc>\d+) (?P<ability>Strength|Dexterity|Constitution|"
        r"Intelligence|Wisdom|Charisma) saving throw, taking "
        r"(?P<average>\d+) \((?P<formula>\d+d\d+(?:\s*[+\-]\s*\d+)?)\) "
        r"(?P<damage_type>[a-z]+) damage on a failed save, or half as much "
        r"damage on a successful one\. "
        r"Flammable objects that aren't being worn or carried in that area "
        r"are ignited\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "death_burst",
        "trigger": "death",
        "range_ft": int(match.group("range")),
        "target": "each_creature_in_range",
        "save_ability": match.group("ability").casefold(),
        "save_dc": int(match.group("dc")),
        "damage_formula": re.sub(r"\s+", "", match.group("formula")).casefold(),
        "average_damage": int(match.group("average")),
        "damage_type": match.group("damage_type").casefold(),
        "failed_save": "full",
        "successful_save": "half",
        "ignite_flammable_unworn_objects": True,
        "automatic": True,
        "source_excerpt": normalized,
    }


def _ignited_illumination_source_trait(
    description: str,
) -> dict[str, Any] | None:
    """Compile the Magmin's standard bonus-action light toggle."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"As a bonus action, the (?P<subject>[A-Za-z][A-Za-z '\-]*) can set "
        r"itself ablaze or extinguish its flames\. While ablaze, the "
        r"(?P=subject) sheds bright light in a (?P<bright>\d+)-foot radius "
        r"and dim light for an additional (?P<dim>\d+) (?:feet|ft)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "ignited_illumination",
        "trigger": "bonus_action",
        "mode": "toggle",
        "bright_light_radius_ft": int(match.group("bright")),
        "additional_dim_light_ft": int(match.group("dim")),
        "automatic": True,
    }


def _sneak_attack_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* deals an extra (?P<average>\d+) "
        r"\((?P<formula>\d+d6)\) damage when it hits a target with a weapon "
        r"attack and has advantage on the attack roll, or when the target is "
        r"within (?P<distance>\d+) feet of an ally of the "
        r"[A-Za-z][A-Za-z '\-]* that isn't incapacitated and the "
        r"[A-Za-z][A-Za-z '\-]* doesn't have disadvantage on the attack roll\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "sneak_attack",
        "trigger": "eligible_weapon_hit",
        "damage_formula": match.group("formula").casefold(),
        "average_damage": int(match.group("average")),
        "uses_per_turn": 1,
        "requires_finesse_or_ranged": False,
        "ally_within_target_ft": int(match.group("distance")),
        "requires_ally_not_incapacitated": True,
        "requires_no_disadvantage": True,
        "alternative": "effective_advantage",
    }


def _amorphous_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* can move through a space as narrow as "
        r"(?P<width>\d+) inch wide without squeezing\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return {
        "kind": "amorphous",
        "trigger": "movement",
        "minimum_space_width_inches": int(match.group("width")),
        "requires_squeezing": False,
        "automatic": True,
    }


def _spider_climb_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    if not re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* can climb difficult surfaces, including "
        r"upside down on ceilings, without needing to make an ability check\.",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    return {
        "kind": "spider_climb",
        "trigger": "climb_movement",
        "difficult_surfaces": True,
        "ceilings": True,
        "ability_check_required": False,
        "automatic": True,
    }


def _corrosive_form_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"A creature that touches the (?P<subject>[A-Za-z][A-Za-z '\-]*) or hits "
        r"it with a melee attack while within (?P<range>\d+) feet of it takes "
        r"\d+ \((?P<damage>\d+\s*d\s*\d+)\) acid damage\. Any non\s*magical "
        r"weapon made of metal or wood that hits the (?P=subject) corrodes\. "
        r"After dealing damage, the weapon takes a permanent (?:and )?cumulative "
        r"(?P<penalty>-\s*(?:\d+|~)) penalty to damage r[0o]lls\. If its penalty drops "
        r"to (?P<destroyed>-\s*\d+), the weapon is [de]estroyed\. Non\s*magical "
        r"ammunition made of metal (?:or|br) wood that hits the (?P=subject) is "
        r"destroyed after dealing damage\. The (?P=subject) can eat through "
        r"(?P<thickness>\d+)-inch-thick, non\s*magical wood or metal in "
        r"(?P<rounds>\d+) round\s*\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    raw_penalty = match.group("penalty").replace(" ", "")
    penalty = -1 if raw_penalty == "-~" else int(raw_penalty)
    destroyed_at = int(match.group("destroyed").replace(" ", ""))
    if penalty >= 0 or destroyed_at >= 0:
        return None
    return {
        "kind": "corrosive_form",
        "trigger": "contact_or_melee_hit",
        "melee_range_ft": int(match.group("range")),
        "contact_damage_formula": re.sub(r"\s+", "", match.group("damage")).casefold(),
        "contact_damage_type": "acid",
        "weapon_materials": ["metal", "wood"],
        "requires_nonmagical_weapon": True,
        "weapon_damage_roll_penalty": penalty,
        "weapon_destroyed_at_penalty": destroyed_at,
        "ammunition_destroyed_after_hit": True,
        "object_materials": ["wood", "metal"],
        "object_maximum_thickness_inches": int(match.group("thickness")),
        "object_dissolution_rounds": int(match.group("rounds")),
        "automatic": True,
    }


def _split_source_trait(description: str) -> dict[str, Any] | None:
    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"When a [A-Za-z][A-Za-z '\-]* that is Medium or larger is subjected "
        r"to (?P<damage_types>[a-z]+(?:\s+or\s+[a-z]+)+) damage, it splits "
        r"into (?P<count>\w+) new [A-Za-z][A-Za-z '\-]* if it has at least "
        r"(?P<hit_points>\d+) hit points\. Each new [A-Za-z][A-Za-z '\-]* "
        r"has hit points equal to half the original [A-Za-z][A-Za-z '\-]*'s, "
        r"rounded down\s*\. New [A-Za-z][A-Za-z '\-]* are one size smaller than "
        r"the original [A-Za-z][A-Za-z '\-]*\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    count = _count(match.group("count"))
    damage_types = [
        item.strip().casefold()
        for item in re.split(r"\s+or\s+", match.group("damage_types"))
        if item.strip()
    ]
    if count is None or count < 2 or len(damage_types) != len(set(damage_types)):
        return None
    return {
        "kind": "split",
        "trigger": "subjected_to_damage",
        "damage_types": damage_types,
        "minimum_size": "medium",
        "minimum_hit_points": int(match.group("hit_points")),
        "new_creature_count": count,
        "hit_points": "half_original_rounded_down",
        "size_change": -1,
    }


def _source_trait_from_description(description: str) -> dict[str, Any] | None:
    matches = [
        parsed
        for parser in (
            _regeneration_source_trait,
            _pack_tactics_source_trait,
            _sunlight_sensitivity_source_trait,
            _keen_perception_source_trait,
            _magic_resistance_source_trait,
            _evasion_source_trait,
            _save_advantage_against_conditions_source_trait,
            _breathing_media_source_trait,
            _assassinate_source_trait,
            _aggressive_source_trait,
            _cunning_action_source_trait,
            _included_weapon_damage_source_trait,
            _heated_body_source_trait,
            _heated_weapons_source_trait,
            _battle_cry_source_trait,
            _death_burst_source_trait,
            _ignited_illumination_source_trait,
            _sneak_attack_source_trait,
            _amorphous_source_trait,
            _spider_climb_source_trait,
            _corrosive_form_source_trait,
            _split_source_trait,
        )
        if (parsed := parser(description)) is not None
    ]
    return matches[0] if len(matches) == 1 else None


def parry_reaction_settlement(
    description: str,
) -> tuple[dict[str, Any], str, str, bool] | None:
    """Structure a complete standard post-hit reaction and isolate adjacent lore."""

    normalized = " ".join(description.split())
    repaired = normalized
    for word in (
        "against",
        "attacker",
        "attack",
        "melee",
        "weapon",
        "wielding",
        "would",
        "must",
        "adds",
        "see",
        "the",
    ):
        fragmented_word = (
            r"\b(?:"
            + "|".join(
                re.escape(word[:split_at])
                + r"\s+"
                + re.escape(word[split_at:])
                for split_at in range(1, len(word))
            )
            + r")\b"
        )
        repaired = re.sub(
            fragmented_word,
            lambda match: word.capitalize() if match.group(0)[0].isupper() else word,
            repaired,
            flags=re.IGNORECASE,
        )
    ocr_repaired = repaired != normalized
    normalized = repaired
    match = re.match(
        r"The (?P<subject>[A-Za-z][A-Za-z '\-]*?) adds? "
        r"(?P<bonus>\d+) to (?:its|his|her|their) AC against one melee attack "
        r"that would hit (?:it|him|her|them)\."
        r"(?: To do so, the (?P<requirement_subject>[A-Za-z][A-Za-z '\-]*?) "
        r"must see the attacker and be wielding "
        r"a melee weapon\.)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    requirement_subject = match.group("requirement_subject")
    if requirement_subject is not None:
        compact_subject = re.sub(r"[^a-z]", "", match.group("subject").casefold())
        compact_requirement_subject = re.sub(
            r"[^a-z]",
            "",
            requirement_subject.casefold(),
        )
        if compact_subject != compact_requirement_subject:
            return None
    mechanics_text = normalized[: match.end()].strip()
    trailing_text = normalized[match.end() :].strip()
    trailing_lead = trailing_text.split(".", 1)[0]
    subject_plural = re.compile(
        rf"(?i)^{re.escape(match.group('subject').strip())}(?:s|es)\b"
    )
    trailing_is_subject_lore = subject_plural.match(trailing_lead) is not None
    if trailing_text and not trailing_is_subject_lore and re.search(
        (
            r"(?i)\b(?:AC|action|advantage|attack|attacker|bonus|can|creature|"
            r"damage|DC|disadvantage|feet|ft|hit|may|move|movement|must|range|"
            r"reach|reaction|roll|round|save|speed|target|turn|weapon)\b"
            r"|\bwield(?:s|ing)?\b.{0,40}\b(?:shield|weapon)\b"
            r"|\b\d+\b|\b\d*d\d+\b"
        ),
        trailing_lead,
    ):
        # A mechanically meaningful trailing clause remains part of the
        # reaction and must not be silently discarded.
        return None
    bonus = int(match.group("bonus"))
    if bonus <= 0:
        return None
    return (
        {
            "kind": "armor_class_bonus",
            "bonus": bonus,
            "attack_modes": ["melee"],
            "requires_visible_attacker": bool(
                re.search(
                    r"\bmust\s+see\s+the\s+attacker\b",
                    mechanics_text,
                    re.IGNORECASE,
                )
            ),
            "requires_wielded_melee_weapon": bool(
                re.search(
                    r"\b(?:be\s+)?wielding\s+a\s+melee\s+weapon\b",
                    mechanics_text,
                    re.IGNORECASE,
                )
            ),
        },
        mechanics_text,
        trailing_text,
        ocr_repaired,
    )


def _parry_reaction_defense(description: str) -> dict[str, Any] | None:
    """Return the engine contract for a complete standard Parry reaction."""

    settlement = parry_reaction_settlement(description)
    return settlement[0] if settlement is not None else None


def gazer_eye_ray_spec(
    sheet: dict[str, Any],
    activity_id: str = "eye-rays-action",
) -> dict[str, Any] | None:
    """Return only a previously reviewed and recorded random-ray contract."""

    activities = {
        str(item.get("id") or ""): item
        for item in dict(sheet.get("content") or {}).get("activities", [])
        if isinstance(item, dict)
    }
    parent = activities.get(activity_id)
    if parent is None or str(parent.get("name") or "").strip().casefold() != "eye rays":
        return None
    recorded = dict(dict(parent.get("choices") or {}).get("random_save_effects") or {})
    return deepcopy(recorded) if recorded else None


def _compile_gazer_eye_ray_spec(
    sheet: dict[str, Any],
    activity_id: str = "eye-rays-action",
) -> dict[str, Any] | None:
    """Compile the legacy contract only at a trusted-source review boundary."""

    activities = {
        str(item.get("id") or ""): item
        for item in dict(sheet.get("content") or {}).get("activities", [])
        if isinstance(item, dict)
    }
    parent = activities.get(activity_id)
    if parent is None or str(parent.get("name") or "").strip().casefold() != "eye rays":
        return None
    parent_description = " ".join(str(parent.get("description") or "").split())
    parent_match = re.search(
        r"(?i)\bshoots\s+(one|two|three|four|\d+)\s+of\s+the\s+following\s+"
        r"magical\s+eye\s+rays\s+at\s+random\s+\(reroll\s+duplicates\),\s+"
        r"choosing\s+one\s+or\s+two\s+targets\s+it\s+can\s+see\s+within\s+"
        r"(\d+)\s+feet\b",
        parent_description,
    )
    if parent_match is None:
        return None
    draw_count = _count(parent_match.group(1))
    if draw_count is None or draw_count < 1:
        return None
    by_name = {
        str(item.get("name") or "").strip().casefold(): item
        for item in activities.values()
    }
    required_names = ("dazing ray", "fear ray", "frost ray", "telekinetic ray")
    if any(name not in by_name for name in required_names):
        return None

    descriptions = {
        name: " ".join(str(by_name[name].get("description") or "").split())
        for name in required_names
    }
    dazing = re.search(
        r"(?i)\bDC\s+(\d+)\s+Wisdom\s+saving\s+throw\s+or\s+be\s+charmed\s+"
        r"until\s+the\s+start\s+of\s+the\s+gazer's\s+next\s+turn\.\s+While\s+"
        r"the\s+target\s+is\s+charmed\s+in\s+this\s+way,\s+its\s+speed\s+is\s+"
        r"halved,\s+and\s+it\s+has\s+disadvantage\s+on\s+attack\s+rolls\b",
        descriptions["dazing ray"],
    )
    fear = re.search(
        r"(?i)\bDC\s+(\d+)\s+Wisdom\s+saving\s+throw\s+or\s+be\s+frightened\s+"
        r"until\s+the\s+start\s+of\s+the\s+gazer's\s+next\s+turn\b",
        descriptions["fear ray"],
    )
    frost = re.search(
        r"(?i)\bDC\s+(\d+)\s+Dexterity\s+saving\s+throw\s+or\s+take\s+"
        r"\d+\s+\((\d+d\d+(?:\s*[+\-]\s*\d+)?)\)\s+([a-z]+)\s+damage\b",
        descriptions["frost ray"],
    )
    telekinetic = re.search(
        r"(?i)\btarget\s+is\s+a\s+creature\s+that\s+is\s+(Tiny|Small|Medium)\s+"
        r"or\s+smaller,\s+it\s+must\s+succeed\s+on\s+a\s+DC\s+(\d+)\s+Strength\s+"
        r"saving\s+throw\s+or\s+be\s+moved\s+up\s+to\s+(\d+)\s+feet\s+directly\s+"
        r"away\s+from\s+the\s+gazer\b",
        descriptions["telekinetic ray"],
    )
    if any(match is None for match in (dazing, fear, frost, telekinetic)):
        return None
    assert dazing is not None
    assert fear is not None
    assert frost is not None
    assert telekinetic is not None
    effects = [
        {
            "id": "dazing-ray",
            "source_activity_id": str(by_name["dazing ray"]["id"]),
            "save": {"ability": "wisdom", "dc": int(dazing.group(1))},
            "failure": {
                "kind": "timed_condition",
                "condition": "charmed",
                "duration": {"period": "source_turn_start", "remaining": 1},
                "speed_multiplier": 0.5,
                "attack_disadvantage": True,
            },
            "source_excerpt": descriptions["dazing ray"],
        },
        {
            "id": "fear-ray",
            "source_activity_id": str(by_name["fear ray"]["id"]),
            "save": {"ability": "wisdom", "dc": int(fear.group(1))},
            "failure": {
                "kind": "timed_condition",
                "condition": "frightened",
                "duration": {"period": "source_turn_start", "remaining": 1},
            },
            "source_excerpt": descriptions["fear ray"],
        },
        {
            "id": "frost-ray",
            "source_activity_id": str(by_name["frost ray"]["id"]),
            "save": {"ability": "dexterity", "dc": int(frost.group(1))},
            "failure": {
                "kind": "damage",
                "expression": frost.group(2).replace(" ", ""),
                "damage_type": frost.group(3).casefold(),
            },
            "source_excerpt": descriptions["frost ray"],
        },
        {
            "id": "telekinetic-ray",
            "source_activity_id": str(by_name["telekinetic ray"]["id"]),
            "save": {"ability": "strength", "dc": int(telekinetic.group(2))},
            "failure": {
                "kind": "forced_movement",
                "maximum_size": telekinetic.group(1).casefold(),
                "distance_ft": int(telekinetic.group(3)),
                "direction": "directly_away",
            },
            "source_excerpt": descriptions["telekinetic ray"],
        },
    ]
    return {
        "kind": "gazer_eye_rays_2014",
        "save_source_kind": "magical_effect",
        "draw_count": draw_count,
        "reroll_duplicates": True,
        "range_ft": int(parent_match.group(2)),
        "target_count": {"minimum": 1, "maximum": 2},
        "effects": effects,
        "source_excerpt": parent_description,
    }


def _structure_gazer_eye_rays(
    sheet: dict[str, Any],
    warnings: list[str],
) -> None:
    spec = _compile_gazer_eye_ray_spec(sheet)
    if spec is None:
        return
    activities = list(sheet["content"]["activities"])
    parent = next(item for item in activities if item.get("id") == "eye-rays-action")
    parent["choices"] = {"random_save_effects": spec}
    parent["mechanic_refs"] = sorted(
        {
            *list(parent.get("mechanic_refs") or []),
            "dnd5e.core.activity.random_save_effects",
        }
    )
    component_ids = {
        str(item["source_activity_id"]) for item in spec["effects"]
    }
    sheet["content"]["activities"] = [
        item for item in activities if str(item.get("id") or "") not in component_ids
    ]
    structured_names = {"Eye Rays", "Dazing Ray", "Fear Ray", "Frost Ray", "Telekinetic Ray"}
    warnings[:] = [
        warning
        for warning in warnings
        if not any(warning.startswith(f"{name}:") for name in structured_names)
    ]


def source_save_effect_spec(
    sheet: dict[str, Any],
    activity_id: str,
) -> dict[str, Any] | None:
    """Return a reviewed, deterministic source saving-throw action contract."""

    activity = next(
        (
            item
            for item in dict(sheet.get("content") or {}).get("activities", [])
            if str(item.get("id") or "") == activity_id
        ),
        None,
    )
    if activity is None:
        return None
    recorded = dict(
        dict(activity.get("choices") or {}).get("source_save_effect") or {}
    )
    if not recorded:
        return None
    if recorded.get("kind") != "intellect_devourer_devour_intellect_2014":
        raise StatblockImportError("unsupported source saving-throw action contract")
    return deepcopy(recorded)


def area_save_damage_spec(
    sheet: dict[str, Any],
    activity_id: str,
) -> dict[str, Any] | None:
    """Return a strict source-derived area save-damage contract."""

    activity = next(
        (
            item
            for item in dict(sheet.get("content") or {}).get("activities", [])
            if str(item.get("id") or "") == activity_id
        ),
        None,
    )
    if activity is None:
        return None
    recorded = dict(
        dict(activity.get("choices") or {}).get("area_save_damage") or {}
    )
    if not recorded:
        return None
    if recorded.get("kind") not in {
        "visible_point_radius_save_damage",
        "self_line_save_damage",
        "self_cone_save_damage",
    }:
        raise StatblockImportError("unsupported area saving-throw damage contract")
    save_formula = recorded.get("save_dc_formula")
    if save_formula is not None:
        if not isinstance(save_formula, Mapping) or set(save_formula) != {
            "base",
            "ability",
            "include_proficiency",
        }:
            raise StatblockImportError("area save DC formula is invalid")
        ability = str(save_formula.get("ability") or "").casefold()
        derived = derive_character_sheet(sheet)
        modifiers = dict(derived.get("ability_modifiers") or {})
        if ability not in modifiers:
            raise StatblockImportError("area save DC formula references an unknown ability")
        recorded["save_dc"] = (
            int(save_formula.get("base", 0) or 0)
            + int(modifiers[ability])
            + (
                int(derived.get("proficiency_bonus", 0) or 0)
                if save_formula.get("include_proficiency") is True
                else 0
            )
        )
    damage_by_level = recorded.get("damage_formula_by_level")
    if damage_by_level is not None:
        if not isinstance(damage_by_level, Mapping) or not damage_by_level:
            raise StatblockImportError("area damage scaling must be a non-empty object")
        total_level = int(dict(sheet.get("progression") or {}).get("level", 0) or 0)
        thresholds = sorted(
            int(level)
            for level in damage_by_level
            if str(level).isdigit() and 1 <= int(level) <= 20
        )
        if len(thresholds) != len(damage_by_level) or not thresholds:
            raise StatblockImportError("area damage scaling levels are invalid")
        eligible = [level for level in thresholds if level <= max(1, total_level)]
        threshold = max(eligible or [min(thresholds)])
        recorded["damage_formula"] = str(damage_by_level[str(threshold)])
    return deepcopy(recorded)


def frightful_presence_spec(
    sheet: dict[str, Any],
    activity_id: str,
) -> dict[str, Any] | None:
    """Return the exact standard Frightful Presence contract."""

    activity = next(
        (
            item
            for item in dict(sheet.get("content") or {}).get("activities", [])
            if str(item.get("id") or "") == activity_id
        ),
        None,
    )
    if activity is None:
        return None
    recorded = dict(
        dict(activity.get("choices") or {}).get("frightful_presence") or {}
    )
    if not recorded:
        return None
    if recorded.get("kind") != "frightful_presence_2014":
        raise StatblockImportError("unsupported Frightful Presence contract")
    return deepcopy(recorded)


def legendary_action_spec(
    sheet: dict[str, Any],
    activity_id: str,
) -> dict[str, Any] | None:
    """Return one exact standard legendary-action option contract."""

    activity = next(
        (
            item
            for item in dict(sheet.get("content") or {}).get("activities", [])
            if str(item.get("id") or "") == activity_id
        ),
        None,
    )
    if activity is None:
        return None
    recorded = dict(
        dict(activity.get("choices") or {}).get("legendary_action") or {}
    )
    if not recorded:
        return None
    if recorded.get("kind") != "legendary_action_2014":
        raise StatblockImportError("unsupported legendary-action contract")
    return deepcopy(recorded)


def _compile_area_save_damage(description: str) -> dict[str, Any] | None:
    """Compile the common 2014 point/radius saving-throw damage grammar."""

    source_excerpt = " ".join(str(description or "").split())
    match = re.fullmatch(
        (
            r".+?\bat a point (?:it|he|she|they) can see within "
            r"(?P<range>\d+) feet of (?:it|him|her|them)\. "
            r"Each creature within (?P<radius>\d+) feet of that point must "
            r"make a DC (?P<dc>\d+) "
            r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
            r"saving throw, taking (?P<average>\d+) "
            r"\((?P<damage>\d+d\d+(?:\s*[+\-]\s*\d+)?)\) "
            r"(?P<damage_type>[A-Za-z]+) damage on a failed save, or half as "
            r"much damage on a successful one\."
        ),
        source_excerpt,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    damage_type = match.group("damage_type").casefold()
    if damage_type not in DAMAGE_TYPES:
        return None
    source_range = int(match.group("range"))
    radius = int(match.group("radius"))
    save_dc = int(match.group("dc"))
    average = int(match.group("average"))
    if (
        not 1 <= source_range <= 5_000
        or not 1 <= radius <= 1_000
        or not 1 <= save_dc <= 40
        or average < 1
    ):
        return None
    return {
        "kind": "visible_point_radius_save_damage",
        "origin": {
            "kind": "visible_point",
            "range_ft": source_range,
        },
        "area": {
            "shape": "radius",
            "radius_ft": radius,
        },
        "targets": "each_creature",
        "save_ability": match.group("ability").casefold(),
        "save_dc": save_dc,
        "damage_formula": match.group("damage").replace(" ", "").casefold(),
        "average_damage": average,
        "damage_type": damage_type,
        "half_on_success": True,
        "save_source_kind": "magical_effect",
        "source_excerpt": source_excerpt,
    }


def _compile_self_line_save_damage(description: str) -> dict[str, Any] | None:
    """Compile the standard breath-weapon line/save/half-damage grammar."""

    source_excerpt = " ".join(str(description or "").split())
    match = re.fullmatch(
        (
            r".+?\b(?:in|into) a (?P<length>\d+)-foot line that is "
            r"(?P<width>\d+) feet wide\. Each creature in that line must make "
            r"a DC (?P<dc>\d+) "
            r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
            r"saving throw, taking (?P<average>\d+) "
            r"\((?P<damage>\d+d\d+(?:\s*[+\-]\s*\d+)?)\) "
            r"(?P<damage_type>[A-Za-z]+) damage on a failed save, or half as "
            r"much damage on a successful one\."
        ),
        source_excerpt,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    damage_type = match.group("damage_type").casefold()
    if damage_type not in DAMAGE_TYPES:
        return None
    length = int(match.group("length"))
    width = int(match.group("width"))
    save_dc = int(match.group("dc"))
    average = int(match.group("average"))
    if (
        not 1 <= length <= 5_000
        or not 1 <= width <= 1_000
        or not 1 <= save_dc <= 40
        or average < 1
    ):
        return None
    return {
        "kind": "self_line_save_damage",
        "origin": {"kind": "self"},
        "area": {
            "shape": "line",
            "length_ft": length,
            "width_ft": width,
        },
        "targets": "each_creature",
        "save_ability": match.group("ability").casefold(),
        "save_dc": save_dc,
        "damage_formula": match.group("damage").replace(" ", "").casefold(),
        "average_damage": average,
        "damage_type": damage_type,
        "half_on_success": True,
        "save_source_kind": "nonmagical_effect",
        "source_excerpt": source_excerpt,
    }


def _compile_frightful_presence(description: str) -> dict[str, Any] | None:
    """Compile the 2014 dragon Frightful Presence action."""

    source_excerpt = " ".join(str(description or "").split())
    match = re.fullmatch(
        (
            r"Each creature of the [A-Za-z][A-Za-z '\-]*'s choice that is within "
            r"(?P<range>\d+) feet of the [A-Za-z][A-Za-z '\-]* and aware of it "
            r"must succeed on a DC (?P<dc>\d+) Wisdom saving throw or become "
            r"frightened for (?P<duration>\d+) minute(?:s)?\. A creature can "
            r"repeat the saving throw at the end of each of its turns, ending "
            r"the effect on itself on a success\. If a creature's saving throw "
            r"is successful or the effect ends for it, the creature is immune "
            r"to the [A-Za-z][A-Za-z '\-]*'s Frightful Presence for the next "
            r"(?P<immunity>\d+) hours\."
        ),
        source_excerpt,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    range_ft = int(match.group("range"))
    save_dc = int(match.group("dc"))
    duration_minutes = int(match.group("duration"))
    immunity_hours = int(match.group("immunity"))
    if (
        not 1 <= range_ft <= 5_000
        or not 1 <= save_dc <= 40
        or not 1 <= duration_minutes <= 1_440
        or not 1 <= immunity_hours <= 24 * 365
    ):
        return None
    return {
        "kind": "frightful_presence_2014",
        "range_ft": range_ft,
        "targets": "source_choice",
        "requires_awareness": True,
        "save_ability": "wisdom",
        "save_dc": save_dc,
        "save_source_kind": "nonmagical_effect",
        "condition": "frightened",
        "duration": {"period": "minute", "remaining": duration_minutes},
        "repeat_save_timing": "turn_end",
        "ends_on_repeat_save_success": True,
        "immunity_on_success_or_end": {
            "period": "hour",
            "remaining": immunity_hours,
            "source_scoped": True,
        },
        "source_excerpt": source_excerpt,
    }


def _legendary_action_pool(markdown: str) -> dict[str, Any] | None:
    """Recover the standard 2014 legendary-action pool and timing text."""

    normalized = " ".join(_base_statblock_markdown(markdown).split())
    match = re.search(
        (
            r"The [A-Za-z][A-Za-z '\-]* can take (?P<count>\d+) legendary "
            r"actions, choosing from the options below\. Only one legendary "
            r"action option can be used at a time and only at the end of "
            r"another creature's turn\s*\. The [A-Za-z][A-Za-z '\-]* regains "
            r"spent legendary actions at the start of its turn\."
        ),
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    maximum = int(match.group("count"))
    if not 1 <= maximum <= 20:
        return None
    return {
        "kind": "legendary_action_pool_2014",
        "maximum": maximum,
        "one_option_per_trigger": True,
        "trigger": "end_of_another_creature_turn",
        "recovers_on": "source_turn_start",
        "source_excerpt": match.group(0),
    }


def _compile_legendary_action(
    entry_name: str,
    description: str,
    *,
    pool: dict[str, Any],
    weapons: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compile standard check, weapon, and wing legendary-action options."""

    cost_match = re.search(
        r"\(\s*Costs?\s+(?P<cost>\d+)\s+Actions?\s*\)",
        entry_name,
        flags=re.IGNORECASE,
    )
    cost = int(cost_match.group("cost")) if cost_match else 1
    if not 1 <= cost <= int(pool["maximum"]):
        return None
    normalized = " ".join(description.split())
    effect: dict[str, Any] | None = None
    check = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* makes a "
        r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
        r"\((?P<skill>[A-Za-z ]+)\) check\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if check is not None:
        skill = _SKILL_NAMES.get(check.group("skill").strip().casefold())
        if skill is not None:
            effect = {
                "kind": "skill_check",
                "ability": check.group("ability").casefold(),
                "skill": skill,
            }
    weapon_match = re.fullmatch(
        r"The [A-Za-z][A-Za-z '\-]* makes an? "
        r"(?P<weapon>[A-Za-z][A-Za-z '\-]*) attack\.",
        normalized,
        flags=re.IGNORECASE,
    )
    if weapon_match is not None:
        weapon_name = weapon_match.group("weapon").strip().casefold()
        matches = [
            item
            for item in weapons
            if str(item.get("name") or "").strip().casefold() == weapon_name
        ]
        if len(matches) == 1:
            effect = {
                "kind": "weapon_attack",
                "weapon_id": str(matches[0]["id"]),
                "attack_mode": str(
                    dict(matches[0].get("mechanics") or {}).get(
                        "attack_type"
                    )
                    or "melee"
                ),
            }
    wing = re.fullmatch(
        (
            r"The [A-Za-z][A-Za-z '\-]* beats its wings\. Each creature within "
            r"(?P<radius>\d+) feet of the [A-Za-z][A-Za-z '\-]* must succeed "
            r"on a DC (?P<dc>\d+) Dexterity saving throw or take "
            r"(?P<average>\d+) \((?P<damage>\d+d\d+(?:\s*[+\-]\s*\d+)?)\) "
            r"(?P<damage_type>[A-Za-z]+) damage and be knocked prone\. The "
            r"[A-Za-z][A-Za-z '\-]* can then fly up to half its flying speed\s*\."
        ),
        normalized,
        flags=re.IGNORECASE,
    )
    if wing is not None and wing.group("damage_type").casefold() in DAMAGE_TYPES:
        effect = {
            "kind": "wing_attack_2014",
            "area": {
                "shape": "self_radius",
                "radius_ft": int(wing.group("radius")),
                "targets": "each_other_creature",
            },
            "save_ability": "dexterity",
            "save_dc": int(wing.group("dc")),
            "save_source_kind": "nonmagical_effect",
            "damage_formula": wing.group("damage").replace(" ", "").casefold(),
            "average_damage": int(wing.group("average")),
            "damage_type": wing.group("damage_type").casefold(),
            "damage_on_success": "none",
            "condition_on_failure": "prone",
            "movement_after": {
                "mode": "fly",
                "maximum": "half_fly_speed",
            },
        }
    if effect is None:
        return None
    return {
        "kind": "legendary_action_2014",
        "pool": deepcopy(pool),
        "cost": cost,
        "effect": effect,
        "source_excerpt": normalized,
    }


def _recharge_contract(entry_name: str) -> dict[str, Any] | None:
    match = re.search(
        r"\(\s*Recharge\s+(?P<minimum>[1-6])(?:\s*[-–]\s*(?P<maximum>[1-6]))?\s*\)",
        entry_name,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    minimum = int(match.group("minimum"))
    maximum = int(match.group("maximum") or minimum)
    if minimum > maximum:
        raise StatblockImportError("Recharge action has a descending d6 success range")
    return {
        "kind": "d6_turn_start",
        "minimum": minimum,
        "maximum": maximum,
        "source_marker": match.group(0),
    }


def source_contest_effect_spec(
    sheet: dict[str, Any],
    activity_id: str,
) -> dict[str, Any] | None:
    """Return a reviewed, deterministic source ability-contest action contract."""

    activity = next(
        (
            item
            for item in dict(sheet.get("content") or {}).get("activities", [])
            if str(item.get("id") or "") == activity_id
        ),
        None,
    )
    if activity is None:
        return None
    recorded = dict(
        dict(activity.get("choices") or {}).get("source_contest_effect") or {}
    )
    if not recorded:
        return None
    if recorded.get("kind") != "intellect_devourer_body_thief_2014":
        raise StatblockImportError("unsupported source ability-contest action contract")
    return deepcopy(recorded)


def _structure_intellect_devourer_actions(
    sheet: dict[str, Any],
    warnings: list[str],
) -> None:
    """Structure the 2014 Intellect Devourer actions and mixed Multiattack."""

    activities = list(sheet["content"]["activities"])
    devour = next(
        (
            item
            for item in activities
            if str(item.get("name") or "").strip().casefold() == "devour intellect"
        ),
        None,
    )
    multiattack = next(
        (
            item
            for item in activities
            if is_multiattack_activity(item)
        ),
        None,
    )
    claws = next(
        (
            item
            for item in sheet["inventory"]["items"]
            if str(item.get("name") or "").strip().casefold() == "claws"
            and item.get("kind") == "weapon"
        ),
        None,
    )
    body_thief = next(
        (
            item
            for item in activities
            if str(item.get("name") or "").strip().casefold() == "body thief"
        ),
        None,
    )
    if body_thief is not None:
        body_description = " ".join(
            str(body_thief.get("description") or "").split()
        )
        body_match_text = body_description.replace("*", "")
        body_match = re.fullmatch(
            r"The intellect devourer initiates an Intelligence contest with an "
            r"incapacitated humanoid within (?P<range>\d+) feet of it\. If it "
            r"wins the contest, the intellect devourer magically consumes the "
            r"target's brain, teleports into the target's skull, and takes control "
            r"of the target's body\. While inside a creature, the intellect "
            r"devourer has total cover against attacks and other effects originating "
            r"outside its host\. The intellect devourer retains its Intelligence, "
            r"Wisdom, and Charisma scores, as well as its understanding of Deep "
            r"Speech, its telepathy, and its traits\. It otherwise adopts the "
            r"target's statistics\. It knows everything the creature knew, including "
            r"spells and languages\. If the host body drops to 0 hit points, the "
            r"intellect devourer must leave it\. A protection from evil and good "
            r"spell cast on the body drives the intellect devourer out\. The "
            r"intellect devourer is also forced out if the target regains its "
            r"devoured brain by means of a wish\. By spending 5 feet of its movement, "
            r"the intellect devourer can voluntarily leave the body, teleporting to "
            r"the nearest unoccupied space within 5 feet of it\. The body then dies, "
            r"unless its brain is restored within 1 round\.",
            body_match_text,
            flags=re.IGNORECASE,
        )
        if body_match is not None:
            choices = dict(body_thief.get("choices") or {})
            choices.pop("manual_ruling", None)
            body_thief["choices"] = {
                **choices,
                "source_contest_effect": {
                    "kind": "intellect_devourer_body_thief_2014",
                    "range_ft": int(body_match.group("range")),
                    "target_count": 1,
                    "target_requirements": ["incapacitated", "humanoid"],
                    "contest": {
                        "source_ability": "intelligence",
                        "target_ability": "intelligence",
                        "ties": "no_winner",
                    },
                    "success": {
                        "brain_consumed": True,
                        "source_inside_host": True,
                        "source_total_cover": True,
                        "source_retains": [
                            "intelligence",
                            "wisdom",
                            "charisma",
                            "deep_speech",
                            "telepathy",
                            "traits",
                        ],
                        "source_adopts": "target_statistics_otherwise",
                        "knowledge_transfer": "all_target_knowledge",
                        "host_zero_hp": "source_must_leave",
                    },
                    "source_excerpt": body_description,
                },
            }
            body_thief["mechanic_refs"] = sorted(
                {
                    *list(body_thief.get("mechanic_refs") or []),
                    "dnd5e.core.activity.source_contest_effect",
                }
            )
            warnings[:] = [
                warning
                for warning in warnings
                if warning
                != "Body Thief: descriptive action is not automatically settled"
            ]
            warnings.append(
                "Body Thief: protection, wish, and voluntary exit require DM settlement"
            )
    if devour is None:
        return
    description = " ".join(str(devour.get("description") or "").split())
    match = re.fullmatch(
        r"The intellect devourer targets one creature it can see within "
        r"(?P<range>\d+) feet of it that has a brain\. The target must succeed "
        r"on a DC (?P<dc>\d+) Intelligence saving throw against this magic or "
        r"take \d+ \((?P<damage>\d+d\d+)\) psychic damage\. Also on a failure, "
        r"roll (?P<secondary>\d+d\d+): If the total equals or exceeds the target's "
        r"Intelligence score, that score is reduced to 0\. The target is stunned "
        r"until it regains at least one point of Intelligence\.",
        description,
        flags=re.IGNORECASE,
    )
    if match is None:
        return
    choices = dict(devour.get("choices") or {})
    choices.pop("manual_ruling", None)
    devour["choices"] = {
        **choices,
        "source_save_effect": {
            "kind": "intellect_devourer_devour_intellect_2014",
            "range_ft": int(match.group("range")),
            "target_count": 1,
            "target_requirement": "has_brain",
            "save": {
                "ability": "intelligence",
                "dc": int(match.group("dc")),
            },
            "failure": {
                "damage_expression": match.group("damage").lower(),
                "damage_type": "psychic",
                "secondary_roll": match.group("secondary").lower(),
                "secondary_threshold": "target_intelligence_score",
                "ability_override": {
                    "ability": "intelligence",
                    "score": 0,
                },
                "condition": "stunned",
                "ends_when": "target_intelligence_score_at_least_1",
            },
            "source_excerpt": description,
        },
    }
    devour["mechanic_refs"] = sorted(
        {
            *list(devour.get("mechanic_refs") or []),
            "dnd5e.core.activity.source_save_effect",
        }
    )
    warnings[:] = [
        warning
        for warning in warnings
        if warning != "Devour Intellect: descriptive action is not automatically settled"
    ]
    if multiattack is None or claws is None:
        return
    multiattack_description = " ".join(
        str(multiattack.get("description") or "").split()
    )
    if not re.fullmatch(
        r"The intellect devourer makes one attack with its claws and uses "
        r"Devour Intellect\.",
        multiattack_description,
        flags=re.IGNORECASE,
    ):
        return
    multiattack["choices"] = {
        "multiattack_options": [
            {
                "id": "claws-and-devour-intellect",
                "attacks": [
                    {
                        "weapon_id": str(claws["id"]),
                        "attack_mode": "melee",
                        "count": 1,
                    }
                ],
                "activities": [
                    {
                        "activity_id": str(devour["id"]),
                        "count": 1,
                    }
                ],
            }
        ]
    }
    warnings[:] = [
        warning
        for warning in warnings
        if warning != "Multiattack: Multiattack composition requires a DM ruling"
    ]


def _parse_srd_statblock(
    markdown: str,
    *,
    source_key: str,
    rule_refs: list[str] | tuple[str, ...] = (),
    name: str | None = None,
    edition: str,
) -> ParsedStatblock:
    """Parse one normalized English SRD creature block into a validated v2 sheet.

    The importer intentionally rejects missing core combat facts. Descriptive traits and
    unsupported action semantics remain source-cited content entries and are reported as
    warnings instead of being silently treated as executable engine mechanics.
    """

    if not isinstance(markdown, str) or not markdown.strip():
        raise StatblockImportError("statblock source text is empty")
    heading = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown)
    source_actor_name = (heading.group(1) if heading else "").strip()
    actor_name = (name or source_actor_name).strip()
    if not actor_name:
        raise StatblockImportError("statblock is missing a creature heading")
    identity = re.search(r"(?m)^\*([^*\n]+)\*\s*$", markdown)
    if not identity and heading:
        preamble_end = re.search(r"(?im)^\*\*Armor Class\*\*", markdown)
        preamble = markdown[
            heading.end() : preamble_end.start() if preamble_end else len(markdown)
        ]
        identity = re.search(
            (
                r"(?im)^\s*"
                r"((?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+[^,\n]+"
                r"(?:,\s*[^\n]+)?)\s*$"
            ),
            preamble,
        )
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
    sheet["edition"] = edition
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
        ability = ABILITY_ABBREVIATIONS.get(abbreviation)
        if ability:
            sheet["abilities"][ability]["bonus"] = target - ability_modifier(
                ability_scores[ability]
            )
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
            sheet["skills"][skill]["bonus"] = target - ability_modifier(
                ability_scores[ability]
            )

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
    spellcasting_entries = [
        entry
        for entry in entries
        if entry[1].strip().casefold() == "spellcasting"
        or entry[1].strip().casefold().startswith("innate spellcasting")
    ]
    # A source can contain both ordinary and innate casting. The current sheet
    # owns one canonical spellcasting ability, so prefer a complete ordinary
    # slot progression and only use innate casting as the primary model when it
    # is the sole parseable source. Any additional trait remains source-bound
    # descriptive content instead of silently borrowing the wrong ability.
    spellcasting_entries.sort(
        key=lambda entry: entry[1].strip().casefold() != "spellcasting"
    )
    spellcasting_entry: tuple[str, str, str] | None = None
    for candidate in spellcasting_entries:
        candidate_innate = (
            candidate[1].strip().casefold().startswith("innate spellcasting")
        )
        parsed_spellcasting = _parse_spellcasting(
            candidate[2],
            innate=candidate_innate,
        )
        if parsed_spellcasting is not None:
            spellcasting_entry = candidate
            spellcasting = parsed_spellcasting
            break
    if spellcasting is not None:
        sheet["spellcasting"]["ability"] = spellcasting["ability"]
        sheet["spellcasting"]["class_lists"] = list(spellcasting.get("class_lists") or [])
        sheet["spellcasting"]["attack_bonus_override"] = spellcasting.get("attack_bonus")
        sheet["spellcasting"]["save_dc_override"] = spellcasting.get("save_dc")
    spell_specs = {
        str(item["name"]).casefold(): item
        for item in (spellcasting or {}).get("spells", [])
    }
    weapons: list[dict[str, Any]] = []
    ammunition_items: dict[str, dict[str, Any]] = {}
    multiattacks: list[tuple[str, str]] = []
    descriptive: list[tuple[str, str, str]] = []
    descriptive_attack_markers = 0
    unresolved_multiattacks: set[str] = set()
    warnings: list[str] = []
    normalization_notes: list[str] = []
    legendary_pool = _legendary_action_pool(markdown)
    attack_marker_pattern = re.compile(
        r"(?i)\b(?:Melee|Ranged|Melee or Ranged)\s+"
        r"(?:(?:Weapon|Spell)\s+)?Attack(?:\s+Roll)?:\*?"
    )
    structured_spell_attack_markers = 0
    for section, entry_name, description in entries:
        if (
            spellcasting_entry is not None
            and entry_name == spellcasting_entry[1]
            and spellcasting is not None
        ):
            continue
        if is_multiattack_source_name(entry_name):
            multiattacks.append((entry_name, description))
            continue
        spell_spec = spell_specs.get(_spell_action_name(entry_name))
        if spell_spec is not None:
            spell_spec["action_name"] = entry_name
            spell_spec["action_description"] = description
            structured_spell_attack_markers += len(
                attack_marker_pattern.findall(description)
            )
            continue
        weapon = _parse_weapon(
            entry_name,
            description,
            source_key,
            actor_name=source_actor_name,
        )
        if weapon:
            ammunition_item = weapon.pop("_ammunition_item", None)
            normalization_note = str(weapon.pop("_normalization_note", "") or "")
            if normalization_note:
                normalization_notes.append(normalization_note)
            if ammunition_item is not None:
                ammunition_id = str(ammunition_item["id"])
                if ammunition_id in ammunition_items:
                    raise StatblockImportError(
                        "statblock contains duplicate explicit ammunition stacks"
                    )
                ammunition_items[ammunition_id] = ammunition_item
            weapons.append(weapon)
        else:
            descriptive.append((section, entry_name, description))
            descriptive_attack_markers += len(
                attack_marker_pattern.findall(description)
            )
    source_attack_markers = len(
        attack_marker_pattern.findall(_base_statblock_markdown(markdown))
    )
    settled_attack_markers = (
        len(weapons)
        + structured_spell_attack_markers
        + descriptive_attack_markers
    )
    if source_attack_markers != settled_attack_markers:
        raise StatblockImportError(
            "statblock contains unparsed weapon action markers"
        )
    if not weapons:
        if not entries:
            raise StatblockImportError(
                "statblock has neither a supported weapon attack nor source-bound entries"
            )
        warnings.append(
            "statblock has no weapon attack; the actor remains valid with its "
            "source-bound traits, actions, and reactions"
        )
    ids = [item["id"] for item in weapons]
    if len(ids) != len(set(ids)):
        raise StatblockImportError("statblock contains duplicate weapon action names")
    armor_items, armor_slots = (
        _parse_armor_equipment(ac_text, source_key)
        if edition == "2014"
        else ([], {})
    )
    sheet["inventory"]["items"] = [
        *armor_items,
        *ammunition_items.values(),
        *weapons,
    ]
    sheet["inventory"]["equipment_slots"].update(armor_slots)

    refs = list(dict.fromkeys(str(item) for item in rule_refs if str(item)))
    if spellcasting is not None:
        feature_name = (
            spellcasting_entry[1]
            if spellcasting_entry is not None
            else "Spellcasting"
        )
        sheet["content"]["features"].append(
            {
                "id": (
                    f"{_slug(feature_name)}-passive"
                    if spellcasting.get("innate")
                    else "spellcasting-passive"
                ),
                "name": feature_name,
                "source_key": source_key,
                "description": spellcasting["description"],
                "activation": {"type": "passive", "cost": 0},
                "rule_refs": refs,
            }
        )
    for weapon in weapons:
        on_hit_effect = str(
            dict(weapon.get("mechanics") or {}).get("on_hit_effect") or ""
        ).strip()
        if on_hit_effect:
            warnings.append(f"{weapon['name']}: on-hit effect requires DM settlement")
    for entry_name, description in multiattacks:
        options = _parse_multiattack(description, weapons)
        if options:
            sheet["content"]["activities"].append(
                {
                    "id": f"{_slug(entry_name)}-activity",
                    "name": entry_name,
                    "source_key": source_key,
                    "description": description,
                    "activation": {"type": "action", "cost": 1},
                    "choices": {"multiattack_options": options},
                    "rule_refs": refs,
                    "mechanic_refs": [MULTIATTACK_MECHANIC_ID],
                }
            )
        else:
            unresolved_multiattacks.add(entry_name)
            descriptive.append(("actions", entry_name, description))
    for section, entry_name, description in descriptive:
        if "reaction" in section:
            activation = "reaction"
        elif "bonus action" in section:
            activation = "bonus_action"
        elif any(
            special_section in section
            for special_section in ("legendary action", "lair action", "mythic action")
        ):
            activation = "special"
        elif "action" in section:
            activation = "action"
        else:
            activation = "passive"
        entry = {
            "id": f"{_slug(entry_name)}-{activation}",
            "name": entry_name,
            "source_key": source_key,
            "description": description,
            "activation": {"type": activation, "cost": 1 if activation != "passive" else 0},
            "rule_refs": refs,
        }
        if entry_name in unresolved_multiattacks:
            entry["mechanic_refs"] = [MULTIATTACK_MECHANIC_ID]
        source_trait_description = (
            description.split("\n\n", 1)[0].strip()
            if activation == "reaction"
            else description
        )
        source_trait = (
            _source_trait_from_description(source_trait_description)
            if edition == "2014"
            else None
        )
        if source_trait is not None:
            if source_trait_description != description:
                entry["description"] = source_trait_description
                normalization_notes.append(
                    f"{entry_name}: trailing creature prose excluded from trait settlement"
                )
            if source_trait["kind"] in {
                "aggressive",
                "cunning_action",
                "ignited_illumination",
            }:
                activation = "bonus_action"
                entry["activation"] = {"type": activation, "cost": 1}
            elif source_trait["kind"] == "battle_cry":
                activation = "action"
                entry["activation"] = {"type": activation, "cost": 1}
            if source_trait["kind"] == "aggressive":
                entry["id"] = "dnd5e.core.monster.aggressive"
            elif source_trait["kind"] == "cunning_action":
                entry["id"] = "dnd5e.content.srd2014.feature.rogue-cunning-action"
            elif source_trait["kind"] == "battle_cry":
                entry["id"] = "dnd5e.core.monster.battle-cry"
                daily_uses = re.search(
                    r"\((?P<count>\d+)\s*/\s*Day\)",
                    entry_name,
                    flags=re.IGNORECASE,
                )
                if daily_uses is None:
                    raise StatblockImportError(
                        "Battle Cry needs an explicit uses-per-day source marker"
                    )
                count = int(daily_uses.group("count"))
                entry["uses"] = {
                    "label": entry_name,
                    "value": count,
                    "max": count,
                    "recovers_on": "long_rest",
                    "source_key": source_key,
                }
            elif source_trait["kind"] == "death_burst":
                entry["id"] = "dnd5e.core.monster.death-burst"
            elif source_trait["kind"] == "ignited_illumination":
                entry["id"] = "dnd5e.core.monster.ignited-illumination"
            entry["activation"]["trigger"] = {
                "regeneration": "start of its turn",
                "pack_tactics": "attack roll",
                "sunlight_sensitivity": "attack roll or sight-based Perception check",
                "keen_perception": "hearing- or sight-based Perception check",
                "magic_resistance": "saving throw against a spell or magical effect",
                "evasion": "Dexterity saving throw for half damage",
                "save_advantage_against_conditions": (
                    "saving throw against a named condition"
                ),
                "breathing_media": "environmental breathing requirement",
                "assassinate": "attack roll during its first turn",
                "aggressive": "bonus action on its turn",
                "cunning_action": "bonus action on its turn",
                "included_weapon_damage": "weapon hit; included in weapon actions",
                "heated_body": "contact or a melee hit within range",
                "heated_weapons": "qualifying weapon hit; included in weapon actions",
                "battle_cry": "action on its turn",
                "death_burst": "when it dies",
                "ignited_illumination": "bonus action on its turn",
                "sneak_attack": "eligible weapon hit once per turn",
                "amorphous": "movement through narrow spaces",
                "spider_climb": "climb movement",
                "corrosive_form": "contact or a melee hit within range",
                "split": "subjected to a listed damage type",
            }[str(source_trait["kind"])]
            entry["choices"] = {"source_trait": source_trait}
        reaction_description = (
            description.split("\n\n", 1)[0].strip()
            if activation == "reaction"
            else description
        )
        reaction_settlement = (
            parry_reaction_settlement(reaction_description)
            if activation == "reaction" and edition == "2014"
            else None
        )
        reaction_defense = (
            reaction_settlement[0] if reaction_settlement is not None else None
        )
        if reaction_defense is not None:
            settled_description = reaction_settlement[1]
            trailing_reaction_prose = reaction_settlement[2]
            if reaction_settlement[3]:
                normalization_notes.append(
                    f"{entry_name}: standard reaction OCR word splits repaired"
                )
            if (
                trailing_reaction_prose
                or reaction_description != description
                or reaction_settlement[3]
            ):
                entry["description"] = settled_description
            if trailing_reaction_prose or reaction_description != description:
                normalization_notes.append(
                    f"{entry_name}: trailing creature prose excluded from reaction settlement"
                )
            entry["activation"]["trigger"] = "hit by a melee attack"
            entry["choices"] = {"reaction_defense": reaction_defense}
        area_save_damage = None
        if activation == "action" and edition == "2014":
            area_save_damage = (
                _compile_area_save_damage(description)
                or _compile_self_line_save_damage(description)
            )
        if area_save_damage is not None:
            choices = dict(entry.get("choices") or {})
            choices["area_save_damage"] = area_save_damage
            recharge = _recharge_contract(entry_name)
            if recharge is not None:
                choices["recharge"] = recharge
                entry["uses"] = {
                    "label": entry_name,
                    "value": 1,
                    "max": 1,
                    "recovers_on": "manual",
                    "source_key": source_key,
                }
            entry["choices"] = choices
            entry["mechanic_refs"] = sorted(
                {
                    *list(entry.get("mechanic_refs") or []),
                    "dnd5e.core.activity.area_save_damage",
                    *(
                        ["dnd5e.core.activity.recharge"]
                        if recharge is not None
                        else []
                    ),
                }
            )
        frightful_presence = (
            _compile_frightful_presence(description)
            if activation == "action" and edition == "2014"
            else None
        )
        if frightful_presence is not None:
            choices = dict(entry.get("choices") or {})
            choices["frightful_presence"] = frightful_presence
            entry["choices"] = choices
            entry["mechanic_refs"] = sorted(
                {
                    *list(entry.get("mechanic_refs") or []),
                    "dnd5e.core.activity.frightful_presence",
                }
            )
        legendary_action = (
            _compile_legendary_action(
                entry_name,
                description,
                pool=legendary_pool,
                weapons=weapons,
            )
            if (
                activation == "special"
                and legendary_pool is not None
                and edition == "2014"
            )
            else None
        )
        if legendary_action is not None:
            entry["activation"]["cost"] = int(legendary_action["cost"])
            entry["activation"]["trigger"] = "end of another creature's turn"
            choices = dict(entry.get("choices") or {})
            choices["legendary_action"] = legendary_action
            entry["choices"] = choices
            entry["mechanic_refs"] = sorted(
                {
                    *list(entry.get("mechanic_refs") or []),
                    "dnd5e.core.activity.legendary_action",
                }
            )
        if (
            source_trait is None
            and reaction_defense is None
            and area_save_damage is None
            and frightful_presence is None
            and legendary_action is None
        ):
            entry["choices"] = {
                "manual_ruling": {
                    "kind": (
                        "descriptive_passive"
                        if activation == "passive"
                        else "descriptive_activity"
                    ),
                    "default_resolver": "agent",
                    "source_excerpt": description,
                }
            }
        sheet["content"]["activities" if activation != "passive" else "features"].append(entry)
        if (
            source_trait is None
            and reaction_defense is None
            and area_save_damage is None
            and frightful_presence is None
            and legendary_action is None
        ):
            warnings.append(
                f"{entry_name}: Multiattack composition requires a DM ruling"
                if entry_name in unresolved_multiattacks
                else (
                    f"{entry_name}: descriptive "
                    f"{activation.replace('_', ' ')} is not automatically settled"
                )
            )

    included_damage_traits = [
        dict(dict(item.get("choices") or {}).get("source_trait") or {})
        for item in sheet["content"]["features"]
        if dict(dict(item.get("choices") or {}).get("source_trait") or {}).get(
            "kind"
        )
        == "included_weapon_damage"
    ]
    for source_trait in included_damage_traits:
        formula = str(source_trait.get("damage_formula") or "")
        if not weapons or any(
            formula
            not in {
                str(part.get("damage_formula") or "").casefold()
                for part in dict(weapon.get("mechanics") or {}).get(
                    "additional_damage", []
                )
            }
            for weapon in weapons
        ):
            raise StatblockImportError(
                "included weapon damage is missing from one or more weapon actions"
            )

    validated = validate_character_sheet(sheet)
    validated_weapons = [
        item
        for item in dict(validated.get("inventory") or {}).get("items", [])
        if isinstance(item, dict) and item.get("kind") == "weapon"
    ]
    heated_weapon_traits = [
        dict(dict(item.get("choices") or {}).get("source_trait") or {})
        for item in sheet["content"]["features"]
        if dict(dict(item.get("choices") or {}).get("source_trait") or {}).get(
            "kind"
        )
        == "heated_weapons"
    ]
    for source_trait in heated_weapon_traits:
        material = str(source_trait.get("required_weapon_material") or "")
        expected_part = {
            "damage_formula": str(source_trait.get("damage_formula") or ""),
            "damage_bonus": 0,
            "damage_type": str(source_trait.get("damage_type") or ""),
        }
        qualifying_weapons = [
            weapon
            for weapon in validated_weapons
            if material
            in {
                str(item).casefold()
                for item in dict(weapon.get("mechanics") or {}).get("materials", [])
            }
            and str(
                dict(weapon.get("mechanics") or {}).get("attack_type") or ""
            ).casefold()
            == "melee"
        ]
        if not qualifying_weapons or any(
            expected_part
            not in dict(weapon.get("mechanics") or {}).get("additional_damage", [])
            for weapon in qualifying_weapons
        ):
            raise StatblockImportError(
                "heated weapon damage is missing from a qualifying weapon action"
            )

    summary = f"{identity_text}; CR {challenge or 'unrecorded'}"
    return ParsedStatblock(
        name=actor_name,
        summary=summary,
        sheet=validated,
        challenge_rating=challenge,
        experience_points=xp,
        warnings=tuple(warnings),
        normalization_notes=tuple(normalization_notes),
        spellcasting=deepcopy(spellcasting),
    )


def parse_2014_statblock(
    markdown: str,
    *,
    source_key: str,
    rule_refs: list[str] | tuple[str, ...] = (),
    name: str | None = None,
) -> ParsedStatblock:
    """Parse an English 2014 SRD-style creature block."""

    return _parse_srd_statblock(
        markdown,
        source_key=source_key,
        rule_refs=rule_refs,
        name=name,
        edition="2014",
    )


def _normalize_2024_statblock(markdown: str) -> tuple[str, bool]:
    """Translate SRD 5.2.1 presentation markup into the shared fact grammar."""

    source_lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    lines: list[str] = []
    for raw_line in source_lines:
        line = re.sub(r"^> ?", "", raw_line)
        stripped = line.strip()
        if stripped in {"```", "```markdown", "___"}:
            continue
        if re.match(r"^\*\*(?:Initiative)\*\*", stripped, re.IGNORECASE):
            continue
        lines.append(line)

    normalized = "\n".join(lines)
    ability_scores: dict[str, int] = {}
    saving_throws: dict[str, int] = {}
    signed = r"[+\-\u2212]?\d+"
    code_ability = re.compile(
        rf"\b(STR|DEX|CON|INT|WIS|CHA)\s+(\d+)\s+"
        rf"\({signed}\)\s*\|\s*({signed})"
    )
    for abbreviation, score, save in code_ability.findall(normalized):
        ability_scores[abbreviation] = int(score)
        saving_throws[abbreviation] = int(save.replace("\u2212", "-"))
    compact_ability = re.compile(
        rf"\|\s*\*\*(STR|DEX|CON|INT|WIS|CHA)\*\*\s+(\d+)\s*\|"
        rf"\s*{signed}\s*\|\s*({signed})\s*\|"
    )
    for abbreviation, score, save in compact_ability.findall(normalized):
        ability_scores[abbreviation] = int(score)
        saving_throws[abbreviation] = int(save.replace("\u2212", "-"))
    wide_ability = re.compile(
        rf"\*\*(STR|DEX|CON|INT|WIS|CHA)\*\*\s+(\d+)\s*\|"
        rf"\s*{signed}\s*\|\s*({signed})",
        re.IGNORECASE,
    )
    for abbreviation, score, save in wide_ability.findall(normalized):
        ability_scores[abbreviation.upper()] = int(score)
        saving_throws[abbreviation.upper()] = int(save.replace("\u2212", "-"))
    modifiers_only = False
    modifier_table = re.search(
        rf"(?ms)^\|\s*STR\s*\|\s*DEX\s*\|\s*CON\s*\|\s*INT\s*\|"
        rf"\s*WIS\s*\|\s*CHA\s*\|.*?"
        rf"^\|\s*\*\*MOD\*\*\s*\|\s*\*\*SAVE\*\*.*?\|\s*$\n"
        rf"(?P<first>^\|(?:\s*{signed}\s*\|){{6}}\s*$).*?"
        rf"^\|\s*\*\*MOD\*\*\s*\|\s*\*\*SAVE\*\*.*?\|\s*$\n"
        rf"(?P<second>^\|(?:\s*{signed}\s*\|){{6}}\s*$)",
        normalized,
        re.IGNORECASE,
    )
    if modifier_table and not ability_scores:
        values = [
            int(value.replace("\u2212", "-"))
            for value in re.findall(
                signed,
                modifier_table.group("first") + modifier_table.group("second"),
            )
        ]
        if len(values) == 12:
            for abbreviation, modifier, save in zip(
                ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
                values[::2],
                values[1::2],
                strict=True,
            ):
                if not -5 <= modifier <= 10:
                    raise StatblockImportError(
                        "2024 statblock ability modifier cannot be represented by a "
                        "D&D ability score"
                    )
                # Some SRD 5.2.1 statblocks publish only MOD and SAVE.  The
                # shared v2 actor schema still requires a score, so retain the
                # exact modifier with the lowest even representative score
                # (and score 1 for -5). No mechanic may infer an omitted odd
                # score from this representation.
                ability_scores[abbreviation] = (
                    1 if modifier == -5 else 10 + 2 * modifier
                )
                saving_throws[abbreviation] = save
            modifiers_only = True
    table = re.search(
        r"(?ms)^\|\s*STR\s*\|\s*DEX\s*\|\s*CON\s*\|\s*INT\s*\|"
        r"\s*WIS\s*\|\s*CHA\s*\|\s*\n"
        r"\|[^\n]+\|\s*\n(?P<scores>\|[^\n]+\|)\s*\n"
        r"(?P<saves>\|[^\n]+\|)",
        normalized,
        flags=re.IGNORECASE,
    )
    if table:
        scores = [int(item) for item in re.findall(r"(\d+)\s*\(", table.group("scores"))]
        saves = [
            int(item.replace("\u2212", "-"))
            for item in re.findall(rf"\*\*Save\*\*\s*({signed})", table.group("saves"))
        ]
        if len(scores) == 6 and len(saves) == 6:
            for abbreviation, score, save in zip(
                ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
                scores,
                saves,
                strict=True,
            ):
                ability_scores[abbreviation] = score
                saving_throws[abbreviation] = save
    normalized = re.sub(r"(?m)^\*\*AC\*\*\s+", "**Armor Class** ", normalized)
    normalized = re.sub(r"(?m)^\*\*HP\*\*\s+", "**Hit Points** ", normalized)
    normalized = re.sub(
        r"(?m)^\*\*Resistances\*\*\s+",
        "**Damage Resistances** ",
        normalized,
    )
    normalized = re.sub(
        r"(?m)^\*\*Vulnerabilities\*\*\s+",
        "**Damage Vulnerabilities** ",
        normalized,
    )

    immunity = re.search(r"(?m)^\*\*Immunities\*\*\s+(.+?)\s*$", normalized)
    if immunity:
        damage_text, separator, condition_text = immunity.group(1).partition(";")
        replacement = f"**Damage Immunities** {damage_text.strip()}"
        if separator and condition_text.strip():
            replacement += f"\n**Condition Immunities** {condition_text.strip()}"
        normalized = normalized[: immunity.start()] + replacement + normalized[immunity.end() :]

    def normalize_challenge(match: re.Match[str]) -> str:
        value = match.group(1).strip()
        challenge = value.split("(", 1)[0].strip()
        experience = re.search(r"\bXP\s+([\d,]+)", value, re.IGNORECASE)
        return (
            f"**Challenge** {challenge} ({experience.group(1)} XP)"
            if experience
            else f"**Challenge** {challenge}"
        )

    normalized = re.sub(
        r"(?m)^\*\*CR\*\*\s+(.+?)\s*$",
        normalize_challenge,
        normalized,
    )
    normalized = re.sub(
        r"(?m)^\*\*Languages\*\*\s+None\s*$",
        "**Languages** -",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?m)^\*\*([^*\n]+?)\.\*\*\s*",
        r"***\1.*** ",
        normalized,
    )
    normalized = re.sub(
        r"\*(Melee|Ranged|Melee or Ranged) Attack Roll:\*\s*"
        r"([+\-]\d+)(\s*\([^)]*\))?(?=,)",
        r"*\1 Weapon Attack:* \2 to hit\3",
        normalized,
        flags=re.IGNORECASE,
    )

    if len(ability_scores) != 6 or len(saving_throws) != 6:
        raise StatblockImportError(
            "2024 statblock must contain six ability scores and saving throws"
        )
    ability_table = (
        "| STR | DEX | CON | INT | WIS | CHA |\n"
        "|---|---|---|---|---|---|\n"
        "| "
        + " | ".join(
            f"{ability_scores[abbreviation]} (+0)"
            for abbreviation in ("STR", "DEX", "CON", "INT", "WIS", "CHA")
        )
        + " |\n"
        + "**Saving Throws** "
        + ", ".join(
            f"{abbreviation.title()} {saving_throws[abbreviation]:+d}"
            for abbreviation in ("STR", "DEX", "CON", "INT", "WIS", "CHA")
        )
    )
    speed = re.search(r"(?m)^\*\*Speed\*\*\s+.+?$", normalized)
    if speed is None:
        raise StatblockImportError("2024 statblock is missing Speed")
    return (
        normalized[: speed.end()] + "\n" + ability_table + normalized[speed.end() :],
        modifiers_only,
    )


def parse_2024_statblock(
    markdown: str,
    *,
    source_key: str,
    rule_refs: list[str] | tuple[str, ...] = (),
    name: str | None = None,
) -> ParsedStatblock:
    """Parse an SRD 5.2.1 statblock without borrowing 2014 unique semantics."""

    normalized, modifiers_only = _normalize_2024_statblock(markdown)
    parsed = _parse_srd_statblock(
        normalized,
        source_key=source_key,
        rule_refs=rule_refs,
        name=name,
        edition="2024",
    )
    return ParsedStatblock(
        name=parsed.name,
        summary=parsed.summary,
        sheet=parsed.sheet,
        challenge_rating=parsed.challenge_rating,
        experience_points=parsed.experience_points,
        warnings=parsed.warnings,
        normalization_notes=(
            *parsed.normalization_notes,
            "SRD 5.2.1 presentation fields normalized without 2014 unique-trait inference",
            *(
                (
                    "SRD 5.2.1 supplied ability modifiers and saves without raw scores; "
                    "stored scores are canonical representatives of the exact modifiers",
                )
                if modifiers_only
                else ()
            ),
        ),
        spellcasting=parsed.spellcasting,
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
    damage_text = f"{formula} {damage_type} damage"
    for part in mechanics.get("additional_damage", []):
        part_formula = str(
            part.get("damage_formula") or "structured damage"
        )
        part_bonus = int(part.get("damage_bonus", 0) or 0)
        if part_bonus:
            part_formula = (
                f"{part_formula} {'+' if part_bonus > 0 else '-'} "
                f"{abs(part_bonus)}"
            )
        damage_text += (
            f" plus {part_formula} "
            f"{str(part.get('damage_type') or 'untyped')} damage"
        )
    return (
        f"*{mode.title()} {attack_kind} Attack:* {attack_bonus_text} to hit, "
        f"{range_text}, one target. *Hit:* {damage_text}. "
        f"Variant source: {source_ref}."
    )


def effective_statblock_rating(
    challenge_rating: str,
    experience_points: int | None,
    variant: dict[str, Any] | None,
) -> tuple[str, int | None]:
    """Return a source-cited encounter rating override for a statblock variant."""

    if variant is None:
        return challenge_rating, experience_points
    has_challenge = "challenge_rating" in variant
    has_experience = "experience_points" in variant
    if has_challenge != has_experience:
        raise StatblockImportError(
            "challenge_rating and experience_points must be overridden together"
        )
    if not has_challenge:
        return challenge_rating, experience_points
    overridden_challenge = str(variant["challenge_rating"] or "").strip()
    challenge_xp = {
        "0": 10,
        "1/8": 25,
        "1/4": 50,
        "1/2": 100,
        **dict(
            zip(
                (str(value) for value in range(1, 31)),
                (
                    200,
                    450,
                    700,
                    1100,
                    1800,
                    2300,
                    2900,
                    3900,
                    5000,
                    5900,
                    7200,
                    8400,
                    10000,
                    11500,
                    13000,
                    15000,
                    18000,
                    20000,
                    22000,
                    25000,
                    33000,
                    41000,
                    50000,
                    62000,
                    75000,
                    90000,
                    105000,
                    120000,
                    135000,
                    155000,
                ),
                strict=True,
            )
        ),
    }
    if overridden_challenge not in challenge_xp:
        raise StatblockImportError(
            "challenge_rating must be a D&D 5e challenge from 0 through 30"
        )
    overridden_experience = variant["experience_points"]
    if (
        not isinstance(overridden_experience, int)
        or isinstance(overridden_experience, bool)
        or overridden_experience < 0
    ):
        raise StatblockImportError("experience_points must be a non-negative integer")
    if overridden_experience != challenge_xp[overridden_challenge]:
        raise StatblockImportError(
            "experience_points must match the D&D 5e challenge XP table"
        )
    return overridden_challenge, overridden_experience


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
        "source_refs",
        "challenge_rating",
        "experience_points",
        "creature_type",
        "size",
        "walking_speed_ft",
        "current_hit_points",
        "maximum_hit_points",
        "armor_class",
        "ability_scores",
        "alignment",
        "darkvision_ft",
        "languages",
        "damage_resistances",
        "damage_immunities",
        "damage_vulnerabilities",
        "condition_immunities",
        "spell_replacements",
        "expend_all_spell_slots",
        "add_features",
        "relentless_endurance",
        "remove_actions",
        "remove_items",
        "remove_activities",
        "action_overrides",
    }
    unknown = set(variant) - allowed
    if unknown:
        raise StatblockImportError(f"unsupported statblock variant fields: {sorted(unknown)}")
    source_refs = []
    source_ref = str(variant.get("source_ref") or "").strip()
    if source_ref:
        source_refs.append(source_ref)
    additional_source_refs = variant.get("source_refs", [])
    if not isinstance(additional_source_refs, list):
        raise StatblockImportError("statblock variant source_refs must be a list")
    source_refs.extend(str(item).strip() for item in additional_source_refs)
    if any(not item for item in source_refs):
        raise StatblockImportError(
            "statblock variant source_refs must contain non-empty strings"
        )
    if not source_refs:
        raise StatblockImportError("statblock variant source_ref or source_refs is required")
    if len(source_refs) != len(set(source_refs)):
        raise StatblockImportError("statblock variant source refs must be unique")
    source_ref = ", ".join(source_refs)
    effective_statblock_rating("", None, variant)

    result = deepcopy(sheet)
    if "creature_type" in variant:
        creature_type = str(variant["creature_type"] or "").strip()
        if not creature_type or len(creature_type) > 100:
            raise StatblockImportError(
                "creature_type must be a non-empty string of at most 100 characters"
            )
        result["progression"]["species"] = creature_type

    if "size" in variant:
        size = str(variant["size"] or "").strip().casefold()
        if size not in {"tiny", "small", "medium", "large", "huge", "gargantuan"}:
            raise StatblockImportError("size must be a supported D&D creature size")
        result["traits"]["size"] = size

    if "walking_speed_ft" in variant:
        walking_speed = variant["walking_speed_ft"]
        if (
            not isinstance(walking_speed, int)
            or isinstance(walking_speed, bool)
            or not 0 <= walking_speed <= 1000
        ):
            raise StatblockImportError(
                "walking_speed_ft must be an integer between 0 and 1000"
            )
        result["combat"]["speed"]["walk"] = walking_speed

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

    if "ability_scores" in variant:
        ability_scores = variant["ability_scores"]
        if not isinstance(ability_scores, dict) or not ability_scores:
            raise StatblockImportError("ability_scores must be a non-empty object")
        unknown_abilities = set(ability_scores) - set(ABILITY_NAMES)
        if unknown_abilities:
            raise StatblockImportError(
                "ability_scores contains unsupported abilities: "
                f"{sorted(unknown_abilities)}"
            )
        for ability, score in ability_scores.items():
            if (
                not isinstance(score, int)
                or isinstance(score, bool)
                or not 1 <= score <= 30
            ):
                raise StatblockImportError(
                    f"ability_scores.{ability} must be an integer between 1 and 30"
                )
            result["abilities"][ability]["score"] = score

    if "alignment" in variant:
        alignment = str(variant["alignment"] or "").strip()
        if not alignment or len(alignment) > 100:
            raise StatblockImportError(
                "alignment must be a non-empty string of at most 100 characters"
            )
        result["traits"]["alignment"] = alignment

    if "darkvision_ft" in variant:
        darkvision_ft = variant["darkvision_ft"]
        if (
            not isinstance(darkvision_ft, int)
            or isinstance(darkvision_ft, bool)
            or not 0 <= darkvision_ft <= 1000
        ):
            raise StatblockImportError(
                "darkvision_ft must be an integer between 0 and 1000"
            )
        result["traits"]["senses"]["darkvision"] = darkvision_ft

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

    damage_variant_fields = {
        "damage_resistances": "resistances",
        "damage_immunities": "immunities",
        "damage_vulnerabilities": "vulnerabilities",
    }
    for variant_field, trait_field in damage_variant_fields.items():
        if variant_field not in variant:
            continue
        raw_damage_types = variant[variant_field]
        if not isinstance(raw_damage_types, list):
            raise StatblockImportError(f"{variant_field} must be a list")
        normalized_damage_types = [
            str(item).strip().casefold() for item in raw_damage_types
        ]
        if (
            any(not item for item in normalized_damage_types)
            or len(normalized_damage_types) != len(set(normalized_damage_types))
        ):
            raise StatblockImportError(
                f"{variant_field} must contain unique non-empty damage types"
            )
        unsupported_damage_types = set(normalized_damage_types) - DAMAGE_TYPES
        if unsupported_damage_types:
            raise StatblockImportError(
                f"{variant_field} contains unsupported D&D 5e damage types: "
                f"{sorted(unsupported_damage_types)}"
            )
        result["traits"][trait_field] = normalized_damage_types

    if "condition_immunities" in variant:
        raw_condition_ids = variant["condition_immunities"]
        if not isinstance(raw_condition_ids, list):
            raise StatblockImportError("condition_immunities must be a list")
        normalized_condition_ids = [
            str(item).strip().casefold().replace("-", "_").replace(" ", "_")
            for item in raw_condition_ids
        ]
        if (
            any(not item for item in normalized_condition_ids)
            or len(normalized_condition_ids) != len(set(normalized_condition_ids))
        ):
            raise StatblockImportError(
                "condition_immunities must contain unique non-empty condition ids"
            )
        result["traits"]["condition_immunities"] = normalized_condition_ids

    if "spell_replacements" in variant:
        replacements = variant["spell_replacements"]
        if not isinstance(replacements, list) or not replacements:
            raise StatblockImportError("spell_replacements must be a non-empty list")
        normalized_replacements: list[tuple[str, str]] = []
        for index, raw in enumerate(replacements):
            if not isinstance(raw, dict):
                raise StatblockImportError(
                    f"spell_replacements[{index}] must be an object"
                )
            unknown_replacement_fields = set(raw) - {
                "remove_spell_id",
                "add_spell_id",
            }
            if unknown_replacement_fields:
                raise StatblockImportError(
                    "unsupported spell replacement fields: "
                    f"{sorted(unknown_replacement_fields)}"
                )
            remove_spell_id = str(raw.get("remove_spell_id") or "").strip()
            add_spell_id = str(raw.get("add_spell_id") or "").strip()
            if not remove_spell_id or not add_spell_id:
                raise StatblockImportError(
                    f"spell_replacements[{index}] requires remove_spell_id and add_spell_id"
                )
            if remove_spell_id == add_spell_id:
                raise StatblockImportError(
                    f"spell_replacements[{index}] must replace two different spells"
                )
            normalized_replacements.append((remove_spell_id, add_spell_id))
        remove_ids = [item[0] for item in normalized_replacements]
        add_ids = [item[1] for item in normalized_replacements]
        if len(remove_ids) != len(set(remove_ids)) or len(add_ids) != len(set(add_ids)):
            raise StatblockImportError(
                "spell_replacements must use unique removed and added spell ids"
            )
        if set(remove_ids) & set(add_ids):
            raise StatblockImportError(
                "spell_replacements cannot chain removed and added spell ids"
            )

        spells = list(result["content"]["spells"])
        spells_by_id = {
            str(item.get("id") or ""): item
            for item in spells
            if str(item.get("id") or "")
        }
        preparation = result["spellcasting"]["preparation"]
        selected_ids = list(preparation.get("selected_spell_ids") or [])
        for remove_spell_id, add_spell_id in normalized_replacements:
            removed_spell = spells_by_id.get(remove_spell_id)
            added_spell = spells_by_id.get(add_spell_id)
            if removed_spell is None:
                raise StatblockImportError(
                    f"spell replacement source is not on the statblock: {remove_spell_id}"
                )
            if added_spell is None:
                raise StatblockImportError(
                    f"spell replacement target is not hydrated: {add_spell_id}"
                )
            if remove_spell_id not in selected_ids:
                raise StatblockImportError(
                    f"spell replacement source is not prepared: {remove_spell_id}"
                )
            if add_spell_id in selected_ids:
                raise StatblockImportError(
                    f"spell replacement target is already prepared: {add_spell_id}"
                )
            if int(removed_spell.get("level", 0) or 0) != int(
                added_spell.get("level", 0) or 0
            ):
                raise StatblockImportError(
                    "spell replacements must preserve the printed spell level"
                )
            selected_ids[selected_ids.index(remove_spell_id)] = add_spell_id
            access = added_spell.setdefault("access", {})
            access.update(
                {
                    "known": True,
                    "prepared": True,
                    "always_prepared": True,
                    "in_spellbook": False,
                }
            )
        result["content"]["spells"] = [
            spell for spell in spells if str(spell.get("id") or "") not in set(remove_ids)
        ]
        preparation["selected_spell_ids"] = selected_ids

    if "expend_all_spell_slots" in variant:
        if variant["expend_all_spell_slots"] is not True:
            raise StatblockImportError("expend_all_spell_slots must be true")
        slots = result["spellcasting"]["spell_slots"]
        if not slots:
            raise StatblockImportError(
                "expend_all_spell_slots requires a statblock with spell slots"
            )
        for slot in slots.values():
            mutate_bounded_resource(
                slot,
                amount=int(slot.get("value", 0) or 0),
                direction="spend",
            )

    if "add_features" in variant:
        features = variant["add_features"]
        if not isinstance(features, list) or not features:
            raise StatblockImportError("add_features must be a non-empty list")
        existing_feature_ids = {
            str(item.get("id") or "") for item in result["content"]["features"]
        }
        added_feature_ids: set[str] = set()
        for index, raw in enumerate(features):
            if not isinstance(raw, dict):
                raise StatblockImportError(f"add_features[{index}] must be an object")
            unknown_feature_fields = set(raw) - {"id", "name", "description"}
            if unknown_feature_fields:
                raise StatblockImportError(
                    f"unsupported add_features fields: {sorted(unknown_feature_fields)}"
                )
            feature_id = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            description = str(raw.get("description") or "").strip()
            if not feature_id or _slug(feature_id) != feature_id:
                raise StatblockImportError(
                    f"add_features[{index}].id must be a lowercase slug"
                )
            if not name or not description:
                raise StatblockImportError(
                    f"add_features[{index}] requires name and description"
                )
            if len(name) > 200 or len(description) > 4000:
                raise StatblockImportError(
                    f"add_features[{index}] exceeds the supported text length"
                )
            if feature_id in existing_feature_ids or feature_id in added_feature_ids:
                raise StatblockImportError(
                    f"add_features contains duplicate feature id: {feature_id}"
                )
            result["content"]["features"].append(
                {
                    "id": feature_id,
                    "name": name,
                    "source_key": f"variant:{source_ref}",
                    "description": description,
                    "activation": {"type": "passive", "cost": 0},
                    "rule_refs": list(source_refs),
                }
            )
            added_feature_ids.add(feature_id)

    if "relentless_endurance" in variant:
        raw_feature = variant["relentless_endurance"]
        if not isinstance(raw_feature, dict):
            raise StatblockImportError("relentless_endurance must be an object")
        unknown_feature_fields = set(raw_feature) - {
            "feature_id",
            "source_excerpt",
        }
        if unknown_feature_fields:
            raise StatblockImportError(
                "unsupported relentless_endurance fields: "
                f"{sorted(unknown_feature_fields)}"
            )
        feature_id = str(raw_feature.get("feature_id") or "").strip()
        source_excerpt = str(raw_feature.get("source_excerpt") or "").strip()
        if not feature_id or _slug(feature_id) != feature_id:
            raise StatblockImportError(
                "relentless_endurance feature_id must be a lowercase slug"
            )
        normalized_excerpt = " ".join(source_excerpt.split())
        mechanically_complete = (
            re.search(
                r"(?i)\bwhen reduced to 0 hit points?\b",
                normalized_excerpt,
            )
            is not None
            and re.search(
                r"(?i)\bdrops? to 1 hit point instead\b",
                normalized_excerpt,
            )
            is not None
            and re.search(
                r"(?i)\bcan(?:no|'?t) do this again until "
                r"(?:he|she|it|they) finishes? a long rest\b",
                normalized_excerpt,
            )
            is not None
        )
        if not mechanically_complete:
            raise StatblockImportError(
                "relentless_endurance source_excerpt is not mechanically complete"
            )
        features = result["content"]["features"]
        if any(str(item.get("id") or "") == feature_id for item in features):
            raise StatblockImportError(
                "relentless_endurance feature_id duplicates an existing feature"
            )
        features.append(
            {
                "id": feature_id,
                "name": "Relentless Endurance",
                "source_key": source_ref,
                "description": source_excerpt,
                "activation": {
                    "type": "passive",
                    "cost": 0,
                    "trigger": "reduced to 0 hit points",
                },
                "uses": {
                    "label": "uses",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "long_rest",
                },
                "choices": {
                    "source_trait": {
                        "kind": "relentless_endurance",
                        "trigger": "reduced_to_zero",
                        "drop_to_hit_points": 1,
                        "requires_not_killed_outright": True,
                        "automatic": True,
                    }
                },
                "rule_refs": list(source_refs),
            }
        )

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

    remove_items = variant.get("remove_items", [])
    if not isinstance(remove_items, list):
        raise StatblockImportError("remove_items must be a list")
    remove_item_keys = [str(item).strip().casefold() for item in remove_items]
    if any(not item for item in remove_item_keys) or len(remove_item_keys) != len(
        set(remove_item_keys)
    ):
        raise StatblockImportError("remove_items must contain unique non-empty ids or names")
    for key in remove_item_keys:
        matches = [
            item
            for item in items
            if key in {str(item.get("id") or "").casefold(), str(item.get("name") or "").casefold()}
        ]
        if len(matches) != 1:
            raise StatblockImportError(
                f"remove_items entry must identify exactly one inventory item: {key}"
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
            "additional_damage",
            "reach_ft",
            "normal_range_ft",
            "long_range_ft",
            "attack_bonus_override",
            "damage_bonus_override",
            "remove_on_hit_effect",
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
        if "additional_damage" in raw_patch:
            raw_additional_damage = raw_patch["additional_damage"]
            if not isinstance(raw_additional_damage, list):
                raise StatblockImportError(
                    "action override additional_damage must be a list"
                )
            additional_damage: list[dict[str, Any]] = []
            for index, raw_damage in enumerate(raw_additional_damage):
                if (
                    not isinstance(raw_damage, dict)
                    or set(raw_damage)
                    - {"damage_formula", "damage_bonus", "damage_type"}
                ):
                    raise StatblockImportError(
                        "each action override additional_damage entry accepts only "
                        "damage_formula, damage_bonus, and damage_type"
                    )
                damage_formula = str(
                    raw_damage.get("damage_formula") or ""
                ).replace(" ", "")
                damage_bonus = raw_damage.get("damage_bonus", 0)
                damage_type = str(
                    raw_damage.get("damage_type") or ""
                ).strip().casefold()
                if not re.fullmatch(r"\d+d\d+", damage_formula):
                    raise StatblockImportError(
                        "action override additional_damage damage_formula "
                        f"at index {index} must be NdM dice"
                    )
                if not isinstance(damage_bonus, int) or isinstance(
                    damage_bonus, bool
                ):
                    raise StatblockImportError(
                        "action override additional_damage damage_bonus "
                        f"at index {index} must be an integer"
                    )
                if damage_type not in DAMAGE_TYPES:
                    raise StatblockImportError(
                        "action override additional_damage damage_type "
                        f"at index {index} must be a D&D damage type"
                    )
                additional_damage.append(
                    {
                        "damage_formula": damage_formula,
                        "damage_bonus": damage_bonus,
                        "damage_type": damage_type,
                    }
                )
            mechanics["additional_damage"] = additional_damage
        if "reach_ft" in raw_patch:
            reach_ft = raw_patch["reach_ft"]
            if (
                not isinstance(reach_ft, int)
                or isinstance(reach_ft, bool)
                or not 1 <= reach_ft <= 10_000
            ):
                raise StatblockImportError(
                    "action override reach_ft must be an integer from 1 through 10000"
                )
            if str(mechanics.get("attack_type") or "") not in {
                "melee",
                "melee_or_ranged",
            }:
                raise StatblockImportError(
                    "action override reach_ft requires a melee weapon action"
                )
            mechanics["reach_ft"] = reach_ft
        for field in ("normal_range_ft", "long_range_ft"):
            if field not in raw_patch:
                continue
            value = raw_patch[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 10_000
            ):
                raise StatblockImportError(
                    f"action override {field} must be an integer from 0 through 10000"
                )
            mechanics[field] = value
        if (
            "normal_range_ft" in raw_patch
            or "long_range_ft" in raw_patch
        ):
            if str(mechanics.get("attack_type") or "") != "ranged":
                raise StatblockImportError(
                    "action override ranged distances require a ranged weapon action"
                )
            normal_range = int(mechanics.get("normal_range_ft", 0) or 0)
            long_range = int(mechanics.get("long_range_ft", 0) or 0)
            if normal_range < 1 or long_range < normal_range:
                raise StatblockImportError(
                    "action override ranged distances require a positive normal "
                    "range and a long range at least as large"
                )
        for field in ("attack_bonus_override", "damage_bonus_override"):
            if field in raw_patch:
                value = raw_patch[field]
                if not isinstance(value, int) or isinstance(value, bool):
                    raise StatblockImportError(f"action override {field} must be an integer")
                mechanics[field] = value
        if "remove_on_hit_effect" in raw_patch:
            remove_on_hit_effect = raw_patch["remove_on_hit_effect"]
            if remove_on_hit_effect is not True:
                raise StatblockImportError(
                    "action override remove_on_hit_effect must be true"
                )
            mechanics["on_hit_effect"] = ""
        item["description"] = _variant_attack_description(item, source_ref)

    remaining_ids = [str(item.get("id") or "") for item in items]
    if len(remaining_ids) != len(set(remaining_ids)):
        raise StatblockImportError("statblock variant produces duplicate weapon action ids")
    result["inventory"]["items"] = items
    for slot, item_id in result["inventory"]["equipment_slots"].items():
        if item_id in removed_ids:
            result["inventory"]["equipment_slots"][slot] = None

    remove_activities = variant.get("remove_activities", [])
    if not isinstance(remove_activities, list):
        raise StatblockImportError("remove_activities must be a list")
    remove_activity_keys = [str(item).strip().casefold() for item in remove_activities]
    if any(not item for item in remove_activity_keys) or len(remove_activity_keys) != len(
        set(remove_activity_keys)
    ):
        raise StatblockImportError(
            "remove_activities must contain unique non-empty ids or names"
        )
    activities = list(result["content"]["activities"])
    for key in remove_activity_keys:
        matches = [
            activity
            for activity in activities
            if key
            in {
                str(activity.get("id") or "").casefold(),
                str(activity.get("name") or "").casefold(),
            }
        ]
        if len(matches) != 1:
            raise StatblockImportError(
                f"remove_activities entry must identify exactly one activity: {key}"
            )
        activities.remove(matches[0])

    retained_activities: list[dict[str, Any]] = []
    for activity in activities:
        choices = activity.get("choices")
        if not isinstance(choices, dict):
            retained_activities.append(activity)
            continue
        options = choices.get("multiattack_options")
        if not isinstance(options, list):
            retained_activities.append(activity)
            continue
        retained_options = []
        for option in options:
            references_removed_action = False
            for attack in option.get("attacks", []):
                weapon_id = str(attack.get("weapon_id") or "")
                if weapon_id in renamed_ids:
                    attack["weapon_id"] = renamed_ids[weapon_id]
                if weapon_id in removed_ids:
                    references_removed_action = True
            if not references_removed_action:
                retained_options.append(option)
        if retained_options:
            choices["multiattack_options"] = retained_options
            retained_activities.append(activity)
    result["content"]["activities"] = retained_activities

    return validate_character_sheet(result)


def apply_reviewed_statblock_fill(
    sheet: dict[str, Any],
    fill: dict[str, Any],
) -> dict[str, Any]:
    """Apply an Agent-reviewed semantic fill to unresolved module statblock prose.

    This is deliberately narrower than a generic character-sheet patch. The parser
    still owns ordinary source transcription, while the Agent must either turn a
    Multiattack sentence into canonical weapon/count choices or explicitly retain it
    as an Agent-ruling boundary after reviewing the exact source excerpt. An Agent
    may also add a source-cited weapon action that the selected statblock body omits
    (for example, a printed variant action in an adjacent rulebook column); the
    ordinary attack parser still owns all mechanics extraction.
    """

    if not isinstance(fill, dict):
        raise StatblockImportError("reviewed statblock fill must be an object")
    unknown = set(fill) - {
        "multiattack_options",
        "additional_actions",
        "resolution_plans",
    }
    if unknown:
        raise StatblockImportError(
            f"unsupported reviewed statblock fill fields: {sorted(unknown)}"
        )
    declarations = fill.get("multiattack_options", [])
    additional_actions = fill.get("additional_actions", [])
    resolution_plans = fill.get("resolution_plans", [])
    if not isinstance(declarations, list):
        raise StatblockImportError("reviewed multiattack_options must be a list")
    if not isinstance(additional_actions, list):
        raise StatblockImportError("reviewed additional_actions must be a list")
    if not isinstance(resolution_plans, list):
        raise StatblockImportError("reviewed resolution_plans must be a list")
    if not declarations and not additional_actions and not resolution_plans:
        raise StatblockImportError(
            "reviewed statblock fill must contain at least one semantic declaration"
        )

    result = deepcopy(sheet)
    activities = list(result["content"]["activities"])
    weapons = {
        str(item.get("id") or ""): item
        for item in result["inventory"]["items"]
        if str(item.get("kind") or "") == "weapon"
    }
    normalized_declarations: list[dict[str, Any]] = []
    normalized_additional_actions: list[dict[str, Any]] = []
    normalized_resolution_plans: list[dict[str, Any]] = []
    resolved_warnings: list[str] = []
    added_warnings: list[str] = []
    used_activity_ids: set[str] = set()

    content_cards = [
        *result["content"]["activities"],
        *result["content"]["features"],
        *weapons.values(),
    ]
    for declaration in resolution_plans:
        if not isinstance(declaration, dict):
            raise StatblockImportError(
                "each reviewed resolution plan fill must be an object"
            )
        allowed = {
            "source_card_id",
            "resolution_plan",
            "reason",
            "default_resolver",
            "ruling_kind",
        }
        if set(declaration) - allowed:
            raise StatblockImportError(
                "unsupported reviewed resolution plan fields: "
                f"{sorted(set(declaration) - allowed)}"
            )
        if declaration.get("default_resolver", "agent") != "agent":
            raise StatblockImportError(
                "reviewed resolution plan default_resolver must be agent"
            )
        if (
            declaration.get("ruling_kind", "module_specific_procedure")
            != "module_specific_procedure"
        ):
            raise StatblockImportError(
                "reviewed resolution plan ruling_kind must be "
                "module_specific_procedure"
            )
        source_card_id = str(declaration.get("source_card_id") or "").strip()
        matching_cards = [
            card
            for card in content_cards
            if str(card.get("id") or "") == source_card_id
        ]
        if len(matching_cards) != 1:
            raise StatblockImportError(
                "reviewed resolution plan source_card_id must identify exactly "
                "one parsed activity, feature, or weapon"
            )
        card = matching_cards[0]
        is_weapon_card = str(card.get("kind") or "") == "weapon"
        reason = " ".join(str(declaration.get("reason") or "").split())
        if not 10 <= len(reason) <= 500:
            raise StatblockImportError(
                "reviewed resolution plan reason must contain 10 to 500 characters"
            )
        raw_plan = declaration.get("resolution_plan")
        try:
            compiled_plan = compile_resolution_plan(raw_plan)
        except ResolutionPlanCompilationError as error:
            raise StatblockImportError(
                f"reviewed resolution plan is invalid: {error}"
            ) from error
        if compiled_plan.source_card_id != source_card_id:
            raise StatblockImportError(
                "reviewed resolution plan source_card_id must match its parsed card"
            )
        expected_kinds = (
            {"item"}
            if is_weapon_card
            else (
                {"feature", "trait"}
                if str(dict(card.get("activation") or {}).get("type") or "")
                == "passive"
                else {"activity", "monster_action"}
            )
        )
        if compiled_plan.source_card_kind not in expected_kinds:
            raise StatblockImportError(
                "reviewed resolution plan source_card_kind does not match its "
                "parsed activity type"
            )
        source_description = " ".join(
            str(card.get("description") or "").split()
        )
        if not any(
            " ".join(str(citation.get("source_excerpt") or "").split())
            == source_description
            for citation in compiled_plan.citations
        ):
            raise StatblockImportError(
                "reviewed resolution plan must cite the exact parsed source excerpt"
            )
        stored_plan = resolution_plan_template(compiled_plan)
        card["resolution_plan"] = stored_plan
        if not is_weapon_card:
            card["mechanic_refs"] = list(
                dict.fromkeys(
                    [
                        *list(card.get("mechanic_refs") or []),
                        compiled_plan.id,
                    ]
                )
            )
            choices = dict(card.get("choices") or {})
            manual_ruling = dict(choices.pop("manual_ruling", {}) or {})
            choices["resolution_plan"] = {
                "id": compiled_plan.id,
                "fingerprint": compiled_plan.fingerprint,
            }
            card["choices"] = choices
        else:
            manual_ruling = {}
        activation = str(
            dict(card.get("activation") or {}).get("type") or "passive"
        )
        if is_weapon_card and str(
            dict(card.get("mechanics") or {}).get("on_hit_effect") or ""
        ).strip():
            resolved_warnings.append(
                f"{card['name']}: on-hit effect requires DM settlement"
            )
        elif manual_ruling:
            resolved_warnings.append(
                (
                    f"{card['name']}: Multiattack composition requires a DM ruling"
                    if is_multiattack_activity(card)
                    else (
                        f"{card['name']}: descriptive "
                        f"{activation.replace('_', ' ')} is not automatically settled"
                    )
                )
            )
        normalized_resolution_plans.append(
            {
                "source_card_id": source_card_id,
                "resolution_plan": stored_plan,
                "reason": reason,
                "default_resolver": "agent",
                "ruling_kind": "module_specific_procedure",
            }
        )

    for declaration in additional_actions:
        if not isinstance(declaration, dict):
            raise StatblockImportError(
                "each reviewed additional action fill must be an object"
            )
        declaration_unknown = set(declaration) - {
            "id",
            "name",
            "source_ref",
            "source_excerpt",
            "reason",
            "default_resolver",
            "ruling_kind",
        }
        if declaration_unknown:
            raise StatblockImportError(
                "unsupported reviewed additional action fields: "
                f"{sorted(declaration_unknown)}"
            )
        if declaration.get("default_resolver", "agent") != "agent":
            raise StatblockImportError(
                "reviewed additional action default_resolver must be agent"
            )
        if (
            declaration.get("ruling_kind", "module_specific_procedure")
            != "module_specific_procedure"
        ):
            raise StatblockImportError(
                "reviewed additional action ruling_kind must be "
                "module_specific_procedure"
            )
        name = " ".join(str(declaration.get("name") or "").split())
        source_ref = str(declaration.get("source_ref") or "").strip()
        source_excerpt = " ".join(
            str(declaration.get("source_excerpt") or "").split()
        )
        reason = " ".join(str(declaration.get("reason") or "").split())
        if not name or len(name) > 200:
            raise StatblockImportError(
                "reviewed additional action name must contain 1 to 200 characters"
            )
        if (
            not re.fullmatch(
                r"(?:module-chunk|module-review|rule-chunk):[^\s:][^\s]*",
                source_ref,
            )
            or len(source_ref) > 500
        ):
            raise StatblockImportError(
                "reviewed additional action source_ref must identify one managed source"
            )
        if not source_excerpt or len(source_excerpt) > 4_000:
            raise StatblockImportError(
                "reviewed additional action source_excerpt must contain "
                "1 to 4000 characters"
            )
        if not reason or len(reason) > 500:
            raise StatblockImportError(
                "reviewed additional action reason must contain 1 to 500 characters"
            )
        weapon = _parse_weapon(
            name,
            source_excerpt,
            source_key=f"agent-fill:{source_ref}",
        )
        if weapon is None:
            raise StatblockImportError(
                "reviewed additional action source_excerpt must contain one "
                "parseable weapon attack"
            )
        parser_warning = str(weapon.pop("_parser_warning", "") or "")
        if parser_warning:
            added_warnings.append(parser_warning)
        weapon_id = str(weapon.get("id") or "")
        declared_id = str(declaration.get("id") or "").strip()
        if declared_id and declared_id != weapon_id:
            raise StatblockImportError(
                "reviewed additional action id must match the parser-derived weapon id"
            )
        if weapon_id in weapons:
            raise StatblockImportError(
                "reviewed additional action duplicates a parsed weapon id"
            )
        result["inventory"]["items"].append(weapon)
        weapons[weapon_id] = weapon
        on_hit_effect = str(
            dict(weapon.get("mechanics") or {}).get("on_hit_effect") or ""
        ).strip()
        if on_hit_effect:
            added_warnings.append(
                f"{weapon['name']}: on-hit effect requires DM settlement"
            )
        normalized_additional_actions.append(
            {
                "id": weapon_id,
                "name": name,
                "source_ref": source_ref,
                "source_excerpt": source_excerpt,
                "reason": reason,
                "default_resolver": "agent",
                "ruling_kind": "module_specific_procedure",
            }
        )

    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise StatblockImportError("each reviewed multiattack fill must be an object")
        declaration_unknown = set(declaration) - {
            "activity_id",
            "source_excerpt",
            "reason",
            "options",
            "resolution",
            "default_resolver",
            "ruling_kind",
        }
        if declaration_unknown:
            raise StatblockImportError(
                "unsupported reviewed multiattack fill fields: "
                f"{sorted(declaration_unknown)}"
            )
        if declaration.get("default_resolver", "agent") != "agent":
            raise StatblockImportError(
                "reviewed multiattack default_resolver must be agent"
            )
        if (
            declaration.get("ruling_kind", "module_specific_procedure")
            != "module_specific_procedure"
        ):
            raise StatblockImportError(
                "reviewed multiattack ruling_kind must be module_specific_procedure"
            )
        activity_id = str(declaration.get("activity_id") or "").strip()
        if not activity_id or activity_id in used_activity_ids:
            raise StatblockImportError(
                "reviewed multiattack activity_id must be non-empty and unique"
            )
        used_activity_ids.add(activity_id)
        matches = [
            activity
            for activity in activities
            if str(activity.get("id") or "") == activity_id
        ]
        if len(matches) != 1:
            raise StatblockImportError(
                "reviewed multiattack activity_id must identify exactly one activity"
            )
        activity = matches[0]
        choices = dict(activity.get("choices") or {})
        manual_ruling = dict(choices.get("manual_ruling") or {})
        parsed_options = choices.get("multiattack_options")
        if (
            not is_multiattack_activity(activity)
            or (
                manual_ruling.get("kind") != "descriptive_activity"
                and not isinstance(parsed_options, list)
            )
        ):
            raise StatblockImportError(
                "reviewed multiattack fill may target only a parsed Multiattack activity"
            )
        source_excerpt = " ".join(
            str(declaration.get("source_excerpt") or "").split()
        )
        source_description = " ".join(str(activity.get("description") or "").split())
        if not source_excerpt or source_excerpt != source_description:
            raise StatblockImportError(
                "reviewed multiattack source_excerpt must exactly match the source activity"
            )
        reason = " ".join(str(declaration.get("reason") or "").split())
        if not reason or len(reason) > 500:
            raise StatblockImportError(
                "reviewed multiattack reason must contain 1 to 500 characters"
            )
        resolution = str(declaration.get("resolution") or "structured").strip()
        if resolution not in {"structured", "agent_ruling"}:
            raise StatblockImportError(
                "reviewed multiattack resolution must be structured or agent_ruling"
            )
        if resolution == "agent_ruling":
            if "options" in declaration:
                raise StatblockImportError(
                    "reviewed agent_ruling multiattack must not contain options"
                )
            activity["choices"] = {
                "manual_ruling": {
                    "kind": "descriptive_activity",
                    "default_resolver": "agent",
                    "source_excerpt": source_excerpt,
                }
            }
            normalized_declarations.append(
                {
                    "activity_id": activity_id,
                    "source_excerpt": source_excerpt,
                    "reason": reason,
                    "resolution": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "module_specific_procedure",
                }
            )
            continue
        raw_options = declaration.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            raise StatblockImportError(
                "reviewed multiattack options must be a non-empty list"
            )
        option_ids: set[str] = set()
        options: list[dict[str, Any]] = []
        for raw_option in raw_options:
            if not isinstance(raw_option, dict) or set(raw_option) - {"id", "attacks"}:
                raise StatblockImportError(
                    "each reviewed multiattack option accepts only id and attacks"
                )
            option_id = str(raw_option.get("id") or "").strip()
            if not option_id or _slug(option_id) != option_id or option_id in option_ids:
                raise StatblockImportError(
                    "reviewed multiattack option ids must be unique lowercase slugs"
                )
            option_ids.add(option_id)
            raw_attacks = raw_option.get("attacks")
            if not isinstance(raw_attacks, list) or not raw_attacks:
                raise StatblockImportError(
                    "reviewed multiattack attacks must be a non-empty list"
                )
            attacks: list[dict[str, Any]] = []
            total_attacks = 0
            for raw_attack in raw_attacks:
                if not isinstance(raw_attack, dict) or set(raw_attack) - {
                    "weapon_id",
                    "attack_mode",
                    "count",
                }:
                    raise StatblockImportError(
                        "each reviewed multiattack attack accepts only "
                        "weapon_id, attack_mode, and count"
                    )
                weapon_id = str(raw_attack.get("weapon_id") or "").strip()
                weapon = weapons.get(weapon_id)
                if weapon is None:
                    raise StatblockImportError(
                        "reviewed multiattack weapon_id must identify a parsed weapon"
                    )
                attack_mode = str(raw_attack.get("attack_mode") or "").strip().casefold()
                if attack_mode not in ATTACK_MODES:
                    raise StatblockImportError(
                        "reviewed multiattack attack_mode must be melee or ranged"
                    )
                mechanics = dict(weapon.get("mechanics") or {})
                properties = {
                    str(item).casefold() for item in mechanics.get("properties") or []
                }
                if attack_mode != str(mechanics.get("attack_type") or "melee") and not (
                    attack_mode == "ranged" and "thrown" in properties
                ):
                    raise StatblockImportError(
                        "reviewed multiattack attack_mode is incompatible with its weapon"
                    )
                count = raw_attack.get("count")
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or not 1 <= count <= 20
                ):
                    raise StatblockImportError(
                        "reviewed multiattack count must be an integer from 1 through 20"
                    )
                total_attacks += count
                attacks.append(
                    {
                        "weapon_id": weapon_id,
                        "attack_mode": attack_mode,
                        "count": count,
                    }
                )
            if total_attacks > 20:
                raise StatblockImportError(
                    "reviewed multiattack option cannot contain more than 20 attacks"
                )
            options.append({"id": option_id, "attacks": attacks})
        activity["choices"] = {"multiattack_options": options}
        if manual_ruling.get("kind") == "descriptive_activity":
            resolved_warnings.append(
                f"{activity['name']}: Multiattack composition requires a DM ruling"
            )
        normalized_declarations.append(
            {
                "activity_id": activity_id,
                "source_excerpt": source_excerpt,
                "reason": reason,
                "options": options,
                "default_resolver": "agent",
                "ruling_kind": "module_specific_procedure",
            }
        )

    normalized_fill: dict[str, Any] = {
        "multiattack_options": normalized_declarations,
    }
    if normalized_additional_actions:
        normalized_fill["additional_actions"] = normalized_additional_actions
    if normalized_resolution_plans:
        normalized_fill["resolution_plans"] = normalized_resolution_plans
    added_warnings.extend(
        f"{activity['name']}: Multiattack composition requires a DM ruling"
        for declaration in normalized_declarations
        if declaration.get("resolution", "structured") == "agent_ruling"
        for activity in activities
        if str(activity.get("id") or "") == declaration["activity_id"]
    )
    return {
        "sheet": validate_character_sheet(result),
        "fill": normalized_fill,
        "resolved_warnings": resolved_warnings,
        "added_warnings": list(dict.fromkeys(added_warnings)),
    }


_OCR_CREATURE_TYPES = (
    "aberration",
    "beast",
    "celestial",
    "construct",
    "dragon",
    "elemental",
    "fey",
    "fiend",
    "giant",
    "humanoid",
    "monstrosity",
    "ooze",
    "plant",
    "undead",
)
_OCR_CREATURE_TYPE_PATTERN = "(?:" + "|".join(_OCR_CREATURE_TYPES) + ")"
_OCR_IDENTITY_RE = re.compile(
    rf"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)[.\s]*"
    rf"("
    rf"(?:{_OCR_CREATURE_TYPE_PATTERN})(?:\s*\([^)]{{1,120}}\))?"
    rf"|swarm\s+of\s+(?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+"
    rf"(?:{_OCR_CREATURE_TYPE_PATTERN})s?"
    rf")(?:,\s*(.+))?$"
)


def _normalize_ocr_identity_text(text: str) -> str:
    """Repair bounded glyph noise around a printed size/type identity line."""

    normalized = unicodedata.normalize("NFKD", " ".join(str(text).split()))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"^[^A-Za-z]{1,3}(?=[A-Za-z])", "", normalized)
    size_prefix = re.match(r"^([A-Za-z]{3,12})(.*)$", normalized)
    if size_prefix is not None:
        observed_size = size_prefix.group(1).lower()
        ranked_sizes = sorted(
            (
                SequenceMatcher(None, observed_size, size.lower()).ratio(),
                size,
            )
            for size in ("Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan")
        )
        best_score, best_size = ranked_sizes[-1]
        runner_up_score = ranked_sizes[-2][0]
        if best_score >= 0.72 and best_score - runner_up_score >= 0.12:
            normalized = f"{best_size}{size_prefix.group(2)}"
    normalized = re.sub(
        r"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)[^A-Za-z0-9(),]{1,3}"
        rf"(?={_OCR_CREATURE_TYPE_PATTERN}\b)",
        r"\1 ",
        normalized,
    )
    direct_match = _OCR_IDENTITY_RE.fullmatch(normalized)
    if direct_match is None:
        identity_prefix = re.match(
            r"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)[.\s]*"
            r"([A-Za-z]{3,14})(.*)$",
            normalized,
        )
        if identity_prefix is not None:
            observed_type = identity_prefix.group(2).lower()
            ranked_types = sorted(
                (
                    SequenceMatcher(None, observed_type, creature_type).ratio(),
                    creature_type,
                )
                for creature_type in _OCR_CREATURE_TYPES
            )
            best_score, best_type = ranked_types[-1]
            runner_up_score = ranked_types[-2][0]
            if best_score >= 0.72 and best_score - runner_up_score >= 0.12:
                normalized = (
                    f"{identity_prefix.group(1)} {best_type}"
                    f"{identity_prefix.group(3)}"
                )
    return " ".join(normalized.split())


def _ocr_identity_match(text: str) -> re.Match[str] | None:
    return _OCR_IDENTITY_RE.fullmatch(_normalize_ocr_identity_text(text))


def is_2014_statblock_identity_line(text: str) -> bool:
    """Return whether one OCR line is a bounded 2014 size/type identity."""

    return _ocr_identity_match(text) is not None
_OCR_FIELD_LABELS = (
    "Armor Class",
    "Hit Points",
    "Speed",
    "Saving Throws",
    "Skills",
    "Damage Vulnerabilities",
    "Damage Resistances",
    "Damage Immunities",
    "Condition Immunities",
    "Senses",
    "Languages",
    "Challenge",
)
_OCR_ENTRY_RE = re.compile(
    r"^([A-Z][A-Za-z0-9 '/();\-–—]{1,80})\.\s*(.*)$"
)


# Printed recharge qualifiers can make an otherwise ordinary action or reaction
# name longer than 80 characters.  The terminating period remains the bounded
# structural marker; the prose guard below rejects sentence-shaped false hits.
_OCR_ENTRY_RE = re.compile(r"^([A-Z][^.\r\n]{1,200})\.\s*(.*)$")


def _ocr_structural_entry_match(text: str) -> re.Match[str] | None:
    """Reject ordinary prose sentences that merely contain an early period."""

    match = _OCR_ENTRY_RE.match(text)
    if match is None:
        return None
    title = match.group(1)
    if ":" in title:
        # Hit/Failure/Success continuations are effect prose belonging to the
        # preceding activity, never independent statblock entries.
        return None
    if re.match(r"(?i)^(?:the|a|an|each|any)\s+", title) and re.search(
        r"(?i)\b(?:must|is|are|has|have|can|cannot|makes?|takes?|targets?|"
        r"becomes?|succeeds?|fails?)\b",
        title,
    ):
        return None
    return match


def _ocr_statblock_section_heading(text: str) -> tuple[str, str] | None:
    """Normalize printed statblock section dividers without dropping qualifiers."""

    normalized = " ".join(str(text or "").split())
    action = re.fullmatch(
        r"(?i)ACTIONS(?:\s*\(\s*(REQUIRES?\s+YOUR\s+BONUS\s+ACTION)\s*\))?",
        normalized,
    )
    if action is not None:
        qualifier = (
            "The following actions require your bonus action."
            if action.group(1)
            else ""
        )
        return "Actions", qualifier
    compact = re.sub(r"[^A-Z]", "", normalized.upper())
    section = {
        "BONUSACTIONS": "Bonus Actions",
        "REACTION": "Reactions",
        "REACTIONS": "Reactions",
        "LEGENDARYACTIONS": "Legendary Actions",
    }.get(compact)
    return (section, "") if section is not None else None


def _ocr_non_statblock_heading(text: str) -> bool:
    """Identify a new all-caps source section after a statblock action area."""

    normalized = " ".join(str(text or "").split())
    words = re.findall(r"[A-Z][A-Z0-9'\-]*", normalized)
    letters = re.sub(r"[^A-Za-z]", "", normalized)
    return (
        1 <= len(words) <= 10
        and len(letters) >= 5
        and letters == letters.upper()
        and not re.search(r"[.!?:]", normalized)
    )


def _ocr_key(value: str) -> str:
    return compact_ascii_key(value)


def _strip_ocr_label(text: str, label: str) -> str:
    return re.sub(rf"(?i)^{re.escape(label)}\s+", "", text)


def _repair_layout_ocr_text(text: str) -> str:
    """Repair only mechanically bounded OCR substitutions before statblock parsing."""

    normalized = re.sub(
        r"(?i)^H[il1]t\s+P[o0][il1]nts(?=\s+\S)",
        "Hit Points",
        text,
    )
    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])(?P<count>(?:[0-9]+|[lI]\s*[0-9]+))\s*d\s*"
        r"(?P<size>[0-9lIOS](?:\s*[0-9lIOS]){0,2})(?![A-Za-z0-9])",
        lambda match: (
            re.sub(r"\s+", "", match.group("count")).translate(
                _OCR_ABILITY_DIGITS
            )
            + "d"
            + re.sub(r"\s+", "", match.group("size")).translate(
                _OCR_ABILITY_DIGITS
            )
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?i)^Challenge\s*[-\u2012\u2013\u2014]\s*(?=\(|\d)",
        "Challenge - ",
        normalized,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9])(?P<digits>[0-9](?:\s+[0-9]{1,2}){1,2})"
        r"(?=\s*(?:ft\.|feet\b|miles?\b|points?\b|XP\b|[(,.;]|$))",
        lambda match: re.sub(r"\s+", "", match.group("digits")),
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\bAttack:\s*)([+\-])\s+(?=\d)",
        r"\1\2",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])[lI]d(?=\d)",
        "1d",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\bhalf\s+as\s+much\s+)darmage(?=\s+on\b)",
        r"\1damage",
        normalized,
    )
    normalized = re.sub(
        r"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)\s*"
        r"([a-z])\s+([a-z]{2,})(?=(?:\s+\([^)]*\))?(?:,|$))",
        r"\1 \2\3",
        normalized,
    )
    normalized = re.sub(
        r"(?i)^Languages\s*[^\x00-\x7f]{1,3}$",
        "Languages -",
        normalized,
    )
    normalized = re.sub(
        r"(?i)^((?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+"
        r"[A-Za-z][A-Za-z0-9 '/()\-]{0,120})[.;]\s*"
        r"((?:(?:lawful|neutral|chaotic)\s+(?:good|neutral|evil))|neutral|"
        r"unaligned|any(?:\s+[A-Za-z-]+){0,4}\s+alignment)$",
        r"\1, \2",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\b(Ignited\s+Illumination),\s+(?=As\s+a\s+bonus\s+action\b)",
        r"\1. ",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bMe[/\\]ee(?=\s+(?:Weapon|Spell)(?:\s+Attack)?\b)",
        "Melee",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\(\s*\d+\s*d\s*\d+)\s*[\u2012\u2013\u2212]\s*(\d+\s*\))",
        r"\1 - \2",
        normalized,
    )
    normalized = re.sub(
        r"^([0-9lIOS]{1,3}\s*\([+\-]\s*[0-9lIOS]{1,2})$",
        r"\1)",
        normalized,
    )
    return re.sub(
        r"^([A-Z][A-Za-z0-9 '/()\-]{1,80})_\s+"
        r"(?=(?:Melee|Ranged|Melee or Ranged)\s+(?:Weapon|Spell)\s+Attack:)",
        r"\1. ",
        normalized,
    )


def _ocr_block(raw: dict[str, Any], index: int) -> dict[str, Any]:
    bbox = raw.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox)
    ):
        raise StatblockImportError(f"OCR block {index} has an invalid bbox")
    text = _repair_layout_ocr_text(str(raw.get("text") or "").strip())
    if not text:
        raise StatblockImportError(f"OCR block {index} has no text")
    x0, y0, x1, y1 = (float(value) for value in bbox)
    if x1 <= x0 or y1 <= y0:
        raise StatblockImportError(f"OCR block {index} has an invalid bbox")
    confidence = raw.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise StatblockImportError(f"OCR block {index} has invalid confidence")
    return {
        "index": index,
        "text": text,
        "confidence": float(confidence),
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "cx": (x0 + x1) / 2,
    }


def _ocr_column_split(
    blocks: list[dict[str, Any]],
    *,
    width: float,
) -> float | None:
    ability_rows: list[list[dict[str, Any]]] = []
    ability_blocks = [
        block for block in blocks if _ocr_ability_tokens(block["text"]) is not None
    ]
    for anchor in ability_blocks:
        row = sorted(
            (
                block
                for block in ability_blocks
                if abs(block["y0"] - anchor["y0"]) <= 12
                and abs(block["y1"] - anchor["y1"]) <= 12
            ),
            key=lambda block: block["x0"],
        )
        tokens = [
            token
            for block in row
            for token in (_ocr_ability_tokens(block["text"]) or [])
        ]
        if tokens == list(_OCR_ABILITY_ORDER) and row not in ability_rows:
            ability_rows.append(row)

    def cuts_complete_ability_row(candidate_split: float) -> bool:
        """Do not mistake one six-column ability table for a page gutter."""

        return any(
            any(block["cx"] < candidate_split for block in row)
            and any(block["cx"] >= candidate_split for block in row)
            for row in ability_rows
        )

    def structural_midpoint_fallback() -> float | None:
        midpoint = width / 2
        identities = [block for block in blocks if _ocr_identity_match(block["text"])]
        if (
            any(block["cx"] < midpoint for block in identities)
            and any(block["cx"] >= midpoint for block in identities)
        ):
            return midpoint
        for identity in identities:
            identity_is_left = identity["cx"] < midpoint
            same_side = [
                block
                for block in blocks
                if (block["cx"] < midpoint) == identity_is_left
            ]
            other_side = [
                block
                for block in blocks
                if (block["cx"] < midpoint) != identity_is_left
            ]
            if all(
                any(
                    re.match(rf"(?i)^{re.escape(label)}\s+\S", block["text"])
                    for block in same_side
                )
                for label in ("Armor Class", "Hit Points", "Speed")
            ) and any(
                block["text"].upper()
                in {"ACTIONS", "BONUS ACTIONS", "REACTIONS", "LEGENDARY ACTIONS"}
                for block in other_side
            ):
                return midpoint
            same_ordinals = [
                int(match.group(1))
                for block in same_side
                if (match := re.match(r"^(\d{1,2})\.\s+", block["text"]))
            ]
            other_ordinals = [
                int(match.group(1))
                for block in other_side
                if (match := re.match(r"^(\d{1,2})\.\s+", block["text"]))
            ]
            if (
                same_ordinals
                and other_ordinals
                and max(same_ordinals) + 1 == min(other_ordinals)
            ):
                return midpoint
        return None

    candidates = [
        width * fraction / 100
        for fraction in range(30, 71)
    ]
    ranked: list[tuple[int, float, float]] = []
    content_top = min(block["y0"] for block in blocks)
    content_bottom = max(block["y1"] for block in blocks)
    content_span = max(1.0, content_bottom - content_top)
    for split in candidates:
        if cuts_complete_ability_row(split):
            continue
        crossing = sum(
            1
            for block in blocks
            if block["x0"] < split < block["x1"]
        )
        left = sum(1 for block in blocks if block["cx"] < split)
        right = len(blocks) - left
        left_blocks = [block for block in blocks if block["cx"] < split]
        right_blocks = [block for block in blocks if block["cx"] >= split]
        left_span = (
            max(block["y1"] for block in left_blocks)
            - min(block["y0"] for block in left_blocks)
            if left_blocks
            else 0.0
        )
        right_span = (
            max(block["y1"] for block in right_blocks)
            - min(block["y0"] for block in right_blocks)
            if right_blocks
            else 0.0
        )
        if (
            left >= 5
            and right >= 5
            and left_span >= content_span * 0.25
            and right_span >= content_span * 0.25
        ):
            ranked.append((crossing, abs(split - width / 2), split))
    if not ranked:
        return structural_midpoint_fallback()
    crossing, _distance, split = min(ranked)
    if crossing > max(2, len(blocks) // 20):
        return structural_midpoint_fallback()
    return split


def _ocr_peer_heading(
    block: dict[str, Any],
    following: dict[str, Any] | None,
) -> bool:
    text = block["text"]
    return bool(
        following is not None
        and text == text.upper()
        and 3 <= len(text) <= 80
        and _ocr_heading_has_identity(block, following)
    )


def _ocr_probable_peer_heading(
    ordered: list[dict[str, Any]],
    index: int,
) -> bool:
    """Bound a sibling card even when OCR corrupts only its identity line."""

    block = ordered[index]
    following = ordered[index + 1] if index + 1 < len(ordered) else None
    if _ocr_peer_heading(block, following):
        return True
    if not (
        block["text"] == block["text"].upper()
        and 3 <= len(block["text"]) <= 80
    ):
        return False
    nearby = [
        candidate
        for candidate in ordered[index + 1 :]
        if candidate["y0"] - block["y1"] <= 220
    ]
    return all(
        any(
            re.match(rf"(?i)^{re.escape(label)}\s+\S", candidate["text"])
            for candidate in nearby
        )
        for label in ("Armor Class", "Hit Points", "Speed")
    )


def _ocr_heading_has_identity(
    heading: dict[str, Any],
    following: dict[str, Any] | None,
) -> bool:
    """Recognize a statblock heading and identity line with normal OCR box overlap."""

    return bool(
        following is not None
        and _ocr_identity_match(following["text"])
        and -20 <= following["y0"] - heading["y1"] <= 80
    )


_OCR_ABILITY_ORDER = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
_OCR_ABILITY_DIGITS = str.maketrans(
    {"l": "1", "I": "1", "O": "0", "S": "5"}
)
_OCR_ABILITY_SCORE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<score>[0-9lIOS](?:\s*[0-9lIOS]){0,2})\s*[({]\s*"
    r"(?P<sign>[+\-])\s*(?P<modifier>[0-9lIOS](?:\s*[0-9lIOS])?)\s*[)}]"
    r"(?![A-Za-z0-9])"
)
_OCR_ABILITY_SCORE_REDUNDANT_MODIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<score>[0-9lIOS](?:\s*[0-9lIOS]){0,2})\s*"
    r"(?:"
    r"[({]\s*[.,:;·]?\s*[+\-]?\s*[0-9lIOS]{1,2}\s*[)}]"
    r"|"
    r"[0-9lIOS]{1,2}\s*[)}]"
    r")"
    r"(?![A-Za-z0-9])"
)


def _ocr_ability_score_matches(
    text: str,
) -> tuple[list[tuple[str, str]], str] | None:
    """Read a score row while treating its printed modifier as redundant.

    The strict grammar remains preferred.  The bounded fallback accepts only a
    short modifier-shaped suffix in the same OCR box; it never supplies or
    changes an ability score.  D&D derives the modifier from that source score,
    so corruption such as ``5(3)`` or ``17 (.+3)`` can be repaired without an
    Agent guess.
    """

    for pattern, preserve_source in (
        (_OCR_ABILITY_SCORE_RE, False),
        (_OCR_ABILITY_SCORE_REDUNDANT_MODIFIER_RE, True),
    ):
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        remainder = pattern.sub("", text)
        if remainder.strip(" \t,;|/"):
            continue
        values: list[tuple[str, str]] = []
        for match in matches:
            score = int(
                re.sub(r"\s+", "", match.group("score")).translate(
                    _OCR_ABILITY_DIGITS
                )
            )
            if preserve_source and not 1 <= score <= 30:
                return None
            normalized_value = f"{score} ({(score - 10) // 2:+d})"
            if preserve_source:
                source_value = match.group(0).strip()
            else:
                source_value = (
                    str(score)
                    + " ("
                    + match.group("sign")
                    + re.sub(r"\s+", "", match.group("modifier")).translate(
                        _OCR_ABILITY_DIGITS
                    )
                    + ")"
                )
            values.append((normalized_value, source_value))
        return values, remainder
    return None


def _ocr_ability_tokens(text: str) -> list[str] | None:
    compact = re.sub(r"[^A-Z]", "", text.upper())
    if not compact:
        return None
    result: list[str] = []
    while compact:
        token = next(
            (ability for ability in _OCR_ABILITY_ORDER if compact.startswith(ability)),
            None,
        )
        if token is None:
            return None
        result.append(token)
        compact = compact[len(token) :]
    return result


def _ocr_ability_table(
    scoped: list[dict[str, Any]],
    *,
    reviewed_ability_scores: Mapping[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Recover six ability columns even when a PDF groups adjacent cells.

    Embedded PDF text and OCR engines legitimately emit either one box per
    table cell, boxes such as ``DEX CON``, or one box containing the whole
    score row.  The printed left-to-right order is authoritative; this helper
    never supplies a missing score or reorders a noncanonical row unless an
    upstream, source-corroborated Agent review names the exact missing column.
    """

    label_blocks: list[tuple[dict[str, Any], list[str]]] = []
    for block in scoped:
        tokens = _ocr_ability_tokens(block["text"])
        if tokens is not None:
            label_blocks.append((block, tokens))
    label_blocks.sort(key=lambda item: (item[0]["x0"], item[0]["y0"]))
    labels = [token for _block, tokens in label_blocks for token in tokens]
    missing = [ability for ability in _OCR_ABILITY_ORDER if ability not in labels]
    can_repair_one_label = bool(
        len(missing) == 1
        and len(labels) == 5
        and len(set(labels)) == 5
        and labels
        == [ability for ability in _OCR_ABILITY_ORDER if ability != missing[0]]
    )
    if labels != list(_OCR_ABILITY_ORDER) and not can_repair_one_label:
        if missing:
            raise StatblockImportError(
                "OCR statblock is missing ability labels: " + ", ".join(missing)
            )
        raise StatblockImportError("OCR statblock ability labels are ambiguous")

    label_top = min(block["y0"] for block, _tokens in label_blocks)
    label_bottom = max(block["y1"] for block, _tokens in label_blocks)
    score_blocks: list[tuple[dict[str, Any], list[tuple[str, str]]]] = []
    for block in scoped:
        if block["y0"] < label_top or block["y0"] > label_bottom + 80:
            continue
        parsed_values = _ocr_ability_score_matches(block["text"])
        if parsed_values is None:
            continue
        values, _remainder = parsed_values
        score_blocks.append((block, values))
    score_blocks.sort(key=lambda item: (item[0]["x0"], item[0]["y0"]))
    reviewed_scores = {
        str(ability).upper(): str(value)
        for ability, value in dict(reviewed_ability_scores or {}).items()
    }
    if any(ability not in _OCR_ABILITY_ORDER for ability in reviewed_scores):
        raise StatblockImportError("reviewed OCR ability score has an unknown ability")
    reviewed_values: dict[str, tuple[str, str]] = {}
    for ability, source_value in reviewed_scores.items():
        parsed = _ocr_ability_score_matches(source_value)
        if parsed is None or len(parsed[0]) != 1 or parsed[1].strip():
            raise StatblockImportError(
                f"reviewed OCR {ability} score must be one exact score and modifier"
            )
        reviewed_values[ability] = parsed[0][0]
    scores = [value for _block, values in score_blocks for value in values]
    if len(scores) + len(reviewed_values) != len(_OCR_ABILITY_ORDER):
        raise StatblockImportError(
            "OCR statblock requires exactly six source ability scores"
        )

    label_by_ability: dict[str, dict[str, Any]] = {}
    for block, tokens in label_blocks:
        for token in tokens:
            label_by_ability[token] = block
    value_by_ability: dict[str, dict[str, Any]] = {}
    remaining_abilities = [
        ability for ability in _OCR_ABILITY_ORDER if ability not in reviewed_values
    ]
    if reviewed_values and all(len(values) == 1 for _block, values in score_blocks):
        for block, values in score_blocks:
            available = [
                ability for ability in remaining_abilities if ability not in value_by_ability
            ]
            if not available:
                raise StatblockImportError("reviewed OCR ability scores are ambiguous")
            ability = min(
                available,
                key=lambda item: abs(
                    float(block["cx"]) - float(label_by_ability[item]["cx"])
                ),
            )
            value, source_value = values[0]
            value_by_ability[ability] = {
                **block,
                "text": value,
                "source_text": source_value,
            }
    else:
        cursor = 0
        for block, values in score_blocks:
            for value, source_value in values:
                ability = remaining_abilities[cursor]
                value_by_ability[ability] = {
                    **block,
                    "text": value,
                    "source_text": source_value,
                }
                cursor += 1
    for ability, (value, source_value) in reviewed_values.items():
        label = label_by_ability[ability]
        value_by_ability[ability] = {
            **label,
            "text": value,
            "source_text": source_value,
            "confidence": 1.0,
            "reviewed_ocr_correction": True,
        }
    if can_repair_one_label:
        missing_ability = missing[0]
        label_by_ability[missing_ability] = {
            **value_by_ability[missing_ability],
            "text": missing_ability,
            "inferred_from": "canonical_six_column_ability_table",
        }
    return label_by_ability, value_by_ability


def _ocr_field_with_continuation(
    scoped: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any] | None:
    """Join only vertically adjacent source boxes belonging to one field."""

    ordered = sorted(scoped, key=lambda block: (block["y0"], block["x0"]))
    field_index = next(
        (
            index
            for index, block in enumerate(ordered)
            if re.fullmatch(rf"{re.escape(label)}(?:\s+\S.*)?", block["text"])
        ),
        None,
    )
    if field_index is None:
        return None
    parts = [ordered[field_index]]
    current = parts[0]
    for following in ordered[field_index + 1 :]:
        if (
            following["x1"] < current["x0"] - 40
            or following["x0"] > current["x1"] + 40
        ):
            continue
        if following["y0"] - current["y1"] > 20:
            break
        if following["x0"] < current["x0"] - 8:
            break
        if any(
            re.match(rf"^{re.escape(other)}(?:\s|$)", following["text"])
            for other in _OCR_FIELD_LABELS
        ) or _ocr_ability_tokens(following["text"]) is not None:
            break
        if following["text"].upper() in {
            "ACTIONS",
            "BONUS ACTIONS",
            "REACTIONS",
            "LEGENDARY ACTIONS",
        }:
            break
        parts.append(following)
        current = following
    text = " ".join(block["text"] for block in parts)
    if not re.match(rf"^{re.escape(label)}\s+\S", text):
        return None
    return {
        **parts[0],
        "text": text,
        "confidence": min(block["confidence"] for block in parts),
        "x1": max(block["x1"] for block in parts),
        "y1": max(block["y1"] for block in parts),
        "source_indices": [block["index"] for block in parts],
    }


def _ocr_repair_section_heading_fragments(
    scoped: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize spaced headings and rejoin a decorated initial ``A``."""

    result = [dict(block) for block in scoped]
    suppressed: set[int] = set()
    for block in result:
        compact = re.sub(r"[^A-Z]", "", block["text"].upper())
        complete = {
            "ACTIONS": "ACTIONS",
            "BONUSACTIONS": "BONUS ACTIONS",
            "REACTIONS": "REACTIONS",
            "LEGENDARYACTIONS": "LEGENDARY ACTIONS",
        }.get(compact)
        if complete is not None:
            block["text"] = complete
            continue
        missing_initial = {
            "CTIONS": "ACTIONS",
            "BONUSCTIONS": "BONUS ACTIONS",
        }.get(compact)
        if missing_initial is None:
            continue
        initial = next(
            (
                candidate
                for candidate in result
                if candidate["index"] != block["index"]
                and candidate["text"].strip().upper() == "A"
                and abs(candidate["y0"] - block["y0"]) <= 8
            ),
            None,
        )
        if initial is None:
            continue
        block["text"] = missing_initial
        block["confidence"] = min(block["confidence"], initial["confidence"])
        suppressed.add(initial["index"])
    return [block for block in result if block["index"] not in suppressed]


def _ocr_is_terminal_subject_heading(
    block: dict[str, Any],
    *,
    target_key: str,
) -> bool:
    """Recognize a trailing plural lore heading for the recovered creature."""

    text = str(block["text"]).strip()
    if text != text.upper():
        return False
    heading_key = _ocr_key(text.rstrip(".:"))
    if heading_key == target_key:
        return True
    if not text.endswith((".", ":")):
        return False
    plural_keys = {
        f"{target_key}s",
        f"{target_key}es",
        f"{target_key[:-1]}ies" if target_key.endswith("y") else "",
    }
    return heading_key in plural_keys


def discover_2014_statblock_names_from_layout(
    layout: dict[str, Any],
    *,
    minimum_confidence: float = 0.8,
) -> list[dict[str, Any]]:
    """Find every structurally proven statblock heading on one layout page.

    The heading is accepted only when the next block in the same detected
    column is an exact size/type/alignment identity line.  This lets a
    text-only host enumerate two-column creature pages without guessing names
    from prose headings or a table of contents.
    """

    if not isinstance(layout, dict):
        raise StatblockImportError("OCR layout must be an object")
    width = layout.get("width")
    raw_blocks = layout.get("blocks")
    if (
        isinstance(width, bool)
        or not isinstance(width, (int, float))
        or width <= 0
        or not isinstance(raw_blocks, list)
    ):
        raise StatblockImportError("OCR layout requires positive width and text blocks")
    blocks = [_ocr_block(raw, index) for index, raw in enumerate(raw_blocks)]
    if not blocks:
        return []
    split = _ocr_column_split(blocks, width=float(width))
    columns = (
        [blocks]
        if split is None
        else [
            [block for block in blocks if block["cx"] < split],
            [block for block in blocks if block["cx"] >= split],
        ]
    )
    discovered: list[dict[str, Any]] = []
    for column_index, column in enumerate(columns):
        ordered = sorted(column, key=lambda block: (block["y0"], block["x0"]))
        for index, heading in enumerate(ordered[:-1]):
            identity = ordered[index + 1]
            name = " ".join(str(heading["text"]).split())
            if (
                not 2 <= len(name) <= 200
                or name.endswith((".", ":"))
                or _ocr_key(name) in {_ocr_key(label) for label in _OCR_FIELD_LABELS}
                or not _ocr_heading_has_identity(heading, identity)
                or min(heading["confidence"], identity["confidence"])
                < minimum_confidence
            ):
                continue
            discovered.append(
                {
                    "name": name,
                    "page_number": layout.get("page_number"),
                    "column": column_index,
                    "heading_confidence": heading["confidence"],
                    "identity": identity["text"],
                    "identity_confidence": identity["confidence"],
                    "heading_bbox": [
                        heading["x0"],
                        heading["y0"],
                        heading["x1"],
                        heading["y1"],
                    ],
                }
            )
    return discovered


def discover_2014_statblock_slots_from_layout(
    layout: dict[str, Any],
    *,
    minimum_confidence: float = 0.5,
) -> list[dict[str, Any]]:
    """Enumerate mechanically proven card slots even when a title is decorative.

    A slot is anchored by one size/type identity followed, in the same detected
    column, by Armor Class, Hit Points, and Speed.  The returned 1-based slot is
    suitable for an Agent to name after reading the rendered page or the bounded
    text evidence; it never asks the Agent to transcribe numeric mechanics.
    """

    if not isinstance(layout, dict):
        raise StatblockImportError("OCR layout must be an object")
    width = layout.get("width")
    raw_blocks = layout.get("blocks")
    if (
        isinstance(width, bool)
        or not isinstance(width, (int, float))
        or width <= 0
        or not isinstance(raw_blocks, list)
    ):
        raise StatblockImportError("OCR layout requires positive width and text blocks")
    blocks = [_ocr_block(raw, index) for index, raw in enumerate(raw_blocks)]
    if not blocks:
        return []
    split = _ocr_column_split(blocks, width=float(width))
    columns = (
        [blocks]
        if split is None
        else [
            [block for block in blocks if block["cx"] < split],
            [block for block in blocks if block["cx"] >= split],
        ]
    )
    slots: list[dict[str, Any]] = []
    for column_index, column in enumerate(columns):
        ordered = sorted(column, key=lambda block: (block["y0"], block["x0"]))
        identity_indexes = [
            index
            for index, block in enumerate(ordered)
            if _ocr_identity_match(block["text"])
            and block["confidence"] >= minimum_confidence
        ]
        for identity_ordinal, identity_index in enumerate(identity_indexes):
            end = (
                identity_indexes[identity_ordinal + 1]
                if identity_ordinal + 1 < len(identity_indexes)
                else len(ordered)
            )
            scoped = ordered[identity_index:end]
            core: dict[str, dict[str, Any]] = {}
            for label in _OCR_FIELD_LABELS[:3]:
                field = _ocr_field_with_continuation(scoped, label=label)
                if field is not None and field["confidence"] >= minimum_confidence:
                    core[label] = field
            if set(core) != set(_OCR_FIELD_LABELS[:3]):
                continue
            identity = ordered[identity_index]
            heading = ordered[identity_index - 1] if identity_index else None
            discovered_name = (
                " ".join(str(heading["text"]).split())
                if heading is not None and _ocr_heading_has_identity(heading, identity)
                else None
            )
            slots.append(
                {
                    "slot": len(slots) + 1,
                    "column": column_index,
                    "identity": identity["text"],
                    "identity_bbox": [
                        identity["x0"],
                        identity["y0"],
                        identity["x1"],
                        identity["y1"],
                    ],
                    "discovered_name": discovered_name,
                    "core": {
                        label: field["text"] for label, field in core.items()
                    },
                    "minimum_core_confidence": min(
                        identity["confidence"],
                        *(field["confidence"] for field in core.values()),
                    ),
                    "_identity_index": identity["index"],
                }
            )
    return slots


def recover_2014_statblock_from_ocr(
    layout: dict[str, Any],
    *,
    name: str,
    minimum_confidence: float = 0.8,
    statblock_slot: int | None = None,
    reviewed_ability_scores: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recover one statblock from layout OCR without requiring an image-capable model."""

    if not isinstance(layout, dict):
        raise StatblockImportError("OCR layout must be an object")
    width = layout.get("width")
    height = layout.get("height")
    if (
        isinstance(width, bool)
        or not isinstance(width, (int, float))
        or width <= 0
        or isinstance(height, bool)
        or not isinstance(height, (int, float))
        or height <= 0
    ):
        raise StatblockImportError("OCR layout requires positive width and height")
    raw_blocks = layout.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise StatblockImportError("OCR layout has no text blocks")
    blocks = [_ocr_block(raw, index) for index, raw in enumerate(raw_blocks)]
    target_key = _ocr_key(name)
    headings = [block for block in blocks if _ocr_key(block["text"]) == target_key]
    split = _ocr_column_split(blocks, width=float(width))

    def has_structural_identity(candidate: dict[str, Any]) -> bool:
        if split is None:
            candidate_column = list(blocks)
        elif candidate["cx"] < split:
            candidate_column = [block for block in blocks if block["cx"] < split]
        else:
            candidate_column = [block for block in blocks if block["cx"] >= split]
        candidate_ordered = sorted(
            candidate_column,
            key=lambda block: (block["y0"], block["x0"]),
        )
        candidate_index = next(
            index
            for index, block in enumerate(candidate_ordered)
            if block["index"] == candidate["index"]
        )
        following = (
            candidate_ordered[candidate_index + 1]
            if candidate_index + 1 < len(candidate_ordered)
            else None
        )
        return _ocr_heading_has_identity(candidate, following)

    structural_headings: list[dict[str, Any]] = []
    for candidate in headings:
        if has_structural_identity(candidate):
            structural_headings.append(candidate)
    fuzzy_headings: list[dict[str, Any]] = []
    if not headings:
        for candidate in blocks:
            candidate_key = _ocr_key(candidate["text"])
            if (
                not candidate_key
                or abs(len(candidate_key) - len(target_key)) > 2
                or SequenceMatcher(None, candidate_key, target_key).ratio() < 0.86
                or not has_structural_identity(candidate)
            ):
                continue
            fuzzy_headings.append(candidate)
    heading_match_mode = ""
    selected_slot: dict[str, Any] | None = None
    selected_slot_boundary_identity_index: int | None = None
    selected_slot_boundary_y0: float | None = None
    if statblock_slot is not None:
        if (
            isinstance(statblock_slot, bool)
            or not isinstance(statblock_slot, int)
            or statblock_slot < 1
        ):
            raise StatblockImportError("statblock_slot must be a positive integer")
        slots = discover_2014_statblock_slots_from_layout(
            layout,
            minimum_confidence=minimum_confidence,
        )
        if statblock_slot > len(slots):
            raise StatblockImportError(
                f"statblock_slot {statblock_slot} is absent; page exposes {len(slots)} slots"
            )
        selected_slot = slots[statblock_slot - 1]
        same_column_slots = [
            item for item in slots if item["column"] == selected_slot["column"]
        ]
        same_column_position = next(
            index
            for index, item in enumerate(same_column_slots)
            if item["slot"] == selected_slot["slot"]
        )
        if same_column_position + 1 < len(same_column_slots):
            selected_slot_boundary_identity_index = int(
                same_column_slots[same_column_position + 1]["_identity_index"]
            )
            boundary_identity = next(
                block
                for block in blocks
                if block["index"] == selected_slot_boundary_identity_index
            )
            boundary_heading_height = max(
                12.0,
                float(boundary_identity["y1"] - boundary_identity["y0"]),
            )
            selected_slot_boundary_y0 = (
                float(boundary_identity["y0"]) - boundary_heading_height - 2.0
            )
        identity_block = next(
            block
            for block in blocks
            if block["index"] == selected_slot["_identity_index"]
        )
        heading_height = max(12.0, float(identity_block["y1"] - identity_block["y0"]))
        heading = {
            **identity_block,
            "index": max(block["index"] for block in blocks) + 1,
            "text": name,
            "y0": identity_block["y0"] - heading_height - 2.0,
            "y1": identity_block["y0"] - 2.0,
            "confidence": float(selected_slot["minimum_core_confidence"]),
        }
        blocks.append(heading)
        heading_match_mode = "agent_named_structural_slot"
    elif len(structural_headings) == 1:
        heading = structural_headings[0]
        heading_match_mode = "exact"
    elif len(fuzzy_headings) == 1:
        heading = fuzzy_headings[0]
        heading_match_mode = "bounded_structural_fuzzy"
    elif len(headings) == 1:
        identity_candidates = [
            block for block in blocks if _ocr_identity_match(block["text"])
        ]
        if len(identity_candidates) != 1:
            raise StatblockImportError(
                f"OCR recovery requires one structurally unambiguous heading matching {name!r}"
            )
        source_heading = headings[0]
        unique_identity = identity_candidates[0]
        heading_height = max(1.0, float(source_heading["y1"] - source_heading["y0"]))
        heading = {
            **source_heading,
            "index": max(block["index"] for block in blocks) + 1,
            "text": name,
            "x0": unique_identity["x0"],
            "x1": max(unique_identity["x1"], unique_identity["x0"] + 1.0),
            "y0": unique_identity["y0"] - heading_height - 2.0,
            "y1": unique_identity["y0"] - 2.0,
            "cx": unique_identity["cx"],
            "confidence": min(
                source_heading["confidence"], unique_identity["confidence"]
            ),
        }
        blocks.append(heading)
        heading_match_mode = "source_name_unique_identity"
    else:
        raise StatblockImportError(
            f"OCR recovery requires one structurally unambiguous heading matching {name!r}"
        )
    if split is None:
        column_blocks = list(blocks)
        column_bounds = [0.0, float(width)]
    elif heading["cx"] < split:
        column_blocks = [block for block in blocks if block["cx"] < split]
        column_bounds = [0.0, split]
    else:
        column_blocks = [block for block in blocks if block["cx"] >= split]
        column_bounds = [split, float(width)]
    ordered = sorted(column_blocks, key=lambda block: (block["y0"], block["x0"]))
    heading_index = next(
        index for index, block in enumerate(ordered) if block["index"] == heading["index"]
    )
    end = len(ordered)
    if selected_slot_boundary_identity_index is not None:
        boundary_index = next(
            index
            for index, block in enumerate(ordered)
            if block["index"] == selected_slot_boundary_identity_index
        )
        if boundary_index > heading_index:
            preceding = ordered[boundary_index - 1]
            end = (
                boundary_index - 1
                if _ocr_peer_heading(preceding, ordered[boundary_index])
                else boundary_index
            )
    for index in range(heading_index + 1, end):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        if _ocr_peer_heading(ordered[index], following):
            end = index
            break
    continuation_blocks: list[dict[str, Any]] = []
    if split is not None and heading["cx"] < split:
        right_ordered = _ocr_repair_section_heading_fragments(
            sorted(
                (block for block in blocks if block["cx"] >= split),
                key=lambda block: (block["y0"], block["x0"]),
            )
        )
        section_index = next(
            (
                index
                for index, block in enumerate(right_ordered)
                if block["y0"] >= heading["y0"]
                and block["text"].upper()
                in {"ACTIONS", "BONUS ACTIONS", "REACTIONS", "LEGENDARY ACTIONS"}
            ),
            None,
        )
        if section_index is not None:
            peer_before_section = any(
                _ocr_probable_peer_heading(right_ordered, index)
                for index in range(section_index)
            )
            if not peer_before_section:
                continuation_start = section_index
                earliest_after_heading = next(
                    (
                        index
                        for index, block in enumerate(right_ordered)
                        if block["y0"] >= heading["y0"] - 10
                    ),
                    section_index,
                )
                if (
                    earliest_after_heading <= section_index
                    and right_ordered[earliest_after_heading]["y0"]
                    - heading["y0"]
                    <= max(80.0, float(height) * 0.04)
                ):
                    continuation_start = earliest_after_heading
                continuation_end = len(right_ordered)
                for index in range(section_index + 1, len(right_ordered)):
                    if _ocr_probable_peer_heading(right_ordered, index):
                        continuation_end = index
                        break
                continuation_blocks = right_ordered[
                    continuation_start:continuation_end
                ]
        else:
            left_ordinals = [
                int(match.group(1))
                for block in ordered[heading_index:end]
                if (match := re.match(r"^(\d{1,2})\.\s+", block["text"]))
            ]
            right_ordinal_indexes = [
                (index, int(match.group(1)))
                for index, block in enumerate(right_ordered)
                if block["y0"] >= heading["y0"]
                and (match := re.match(r"^(\d{1,2})\.\s+", block["text"]))
            ]
            if (
                left_ordinals
                and right_ordinal_indexes
                and max(left_ordinals) + 1 == right_ordinal_indexes[0][1]
            ):
                continuation_blocks = right_ordered[
                    right_ordinal_indexes[0][0] :
                ]
        if selected_slot_boundary_y0 is not None:
            continuation_blocks = [
                block
                for block in continuation_blocks
                if float(block["y0"]) < selected_slot_boundary_y0
            ]
    unfiltered_scoped = [*ordered[heading_index:end], *continuation_blocks]
    corrupt_decorative_blocks = [
        block
        for block in unfiltered_scoped
        if block["y0"] >= float(height) * 0.85
        and block["text"].count("=") >= 4
        and any(character.isalpha() for character in block["text"])
    ]
    if corrupt_decorative_blocks:
        # Embedded PDF fonts can interleave a statblock's final source line
        # with the decorative bottom border. Accepting that text would produce
        # a structurally valid but corrupted card, so require the caller to
        # retry the exact page through its independent OCR layout provider.
        raise StatblockImportError(
            "OCR statblock layout contains decorative glyph interleaving"
        )
    page_furniture = [
        block
        for block in unfiltered_scoped
        if block["y0"] >= float(height) * 0.9
        and (
            re.fullmatch(r"\d{1,4}", block["text"])
            or re.match(
                r"(?i)^(?:chapter|c(?:h|[lI1]{1,2})apter|part|appendix)"
                r"\s+[^|:]{1,80}\s*[|:]",
                block["text"],
            )
            or (
                block["text"] == block["text"].upper()
                and re.match(
                    r"^(?:chapter|part|appendix)\d",
                    _ocr_key(block["text"]),
                )
            )
        )
    ]
    page_furniture_ids = {block["index"] for block in page_furniture}
    non_furniture = [
        block
        for block in unfiltered_scoped
        if block["index"] not in page_furniture_ids
    ]
    trailing_subject_headings = (
        [non_furniture[-1]]
        if non_furniture
        and _ocr_is_terminal_subject_heading(
            non_furniture[-1],
            target_key=target_key,
        )
        else []
    )
    excluded_ids = {
        *page_furniture_ids,
        *(block["index"] for block in trailing_subject_headings),
    }
    scoped = [
        block
        for block in unfiltered_scoped
        if block["index"] not in excluded_ids
    ]
    scoped = _ocr_repair_section_heading_fragments(scoped)
    identity = next(
        (block for block in scoped[1:] if _ocr_identity_match(block["text"])),
        None,
    )
    if identity is None:
        raise StatblockImportError("OCR statblock has no unambiguous size/type line")
    identity_source_text = str(identity["text"])
    identity_match = _ocr_identity_match(identity_source_text)
    assert identity_match is not None
    normalized_identity = (
        f"{identity_match.group(1)} {identity_match.group(2).strip()}"
        + (
            f", {identity_match.group(3).strip()}"
            if identity_match.group(3) is not None
            else ""
        )
    )
    identity = {**identity, "text": normalized_identity}

    core_fields: dict[str, dict[str, Any]] = {}
    for label in _OCR_FIELD_LABELS[:3]:
        core_fields[label] = _ocr_field_with_continuation(scoped, label=label)
        if core_fields[label] is None:
            raise StatblockImportError(f"OCR statblock is missing {label}")

    ability_labels, ability_values = _ocr_ability_table(
        scoped,
        reviewed_ability_scores=reviewed_ability_scores,
    )
    challenge = next(
        (
            block
            for block in scoped
            if re.match(r"(?i)^Challenge\s+\S", block["text"])
        ),
        None,
    )
    detail_fields: dict[str, dict[str, Any]] = {}
    for label in _OCR_FIELD_LABELS[3:-1]:
        field = _ocr_field_with_continuation(scoped, label=label)
        if field is not None:
            detail_fields[label] = field

    critical = [
        heading,
        identity,
        *core_fields.values(),
        *ability_labels.values(),
        *ability_values.values(),
        *detail_fields.values(),
        *([challenge] if challenge is not None else []),
    ]
    low_confidence = [
        {"text": block["text"], "confidence": block["confidence"]}
        for block in critical
        if block["confidence"] < minimum_confidence
    ]
    if low_confidence:
        raise StatblockImportError(
            "OCR statblock has low-confidence identity or core combat fields"
        )

    skipped = {
        heading["index"],
        identity["index"],
        *(
            source_index
            for block in core_fields.values()
            for source_index in block.get("source_indices", [block["index"]])
        ),
        *(block["index"] for block in ability_labels.values()),
        *(block["index"] for block in ability_values.values()),
        *(
            source_index
            for block in detail_fields.values()
            for source_index in block.get("source_indices", [block["index"]])[1:]
        ),
    }
    detail_field_by_index = {
        block["index"]: (label, block) for label, block in detail_fields.items()
    }
    detail_start = max(block["y1"] for block in ability_values.values())
    continuation_ids = {block["index"] for block in continuation_blocks}
    body_left_samples = [
        identity["x0"],
        *(block["x0"] for block in core_fields.values()),
        *(block["x0"] for block in detail_fields.values()),
        *(
            block["x0"]
            for block in scoped
            if _ocr_statblock_section_heading(block["text"]) is not None
        ),
    ]
    ordered_left_samples = sorted(float(value) for value in body_left_samples)
    body_left = ordered_left_samples[len(ordered_left_samples) // 2]
    paragraph_indent = max(5.0, float(width) * 0.008)
    details: list[str] = []
    entered_action_section = False
    action_entry_count = 0
    surrounding_prose_boundary: dict[str, Any] | None = None
    for scoped_index, block in enumerate(scoped):
        if block["index"] in skipped or (
            block["index"] not in continuation_ids and block["y0"] < detail_start
        ):
            continue
        text = block["text"]
        section_heading = _ocr_statblock_section_heading(text)
        if section_heading is not None:
            section, qualifier = section_heading
            details.append(f"## {section}")
            if qualifier:
                details.append(f"***Command Requirement.*** {qualifier}")
            entered_action_section = True
            continue
        if entered_action_section and _ocr_non_statblock_heading(text):
            break
        entry = _ocr_structural_entry_match(text)
        if (
            entered_action_section
            and action_entry_count
            and entry is not None
            and block["x0"] - body_left >= paragraph_indent
            and scoped_index > 0
            and block["y0"] - scoped[scoped_index - 1]["y1"] >= paragraph_indent
            and scoped_index + 1 < len(scoped)
        ):
            following = scoped[scoped_index + 1]
            if (
                following["x0"] <= body_left + paragraph_indent / 2
                and 0 <= following["y0"] - block["y1"] <= 20
            ):
                # A compact statblock may end above ordinary indented source
                # prose.  Named prose paragraphs look like actions in plain
                # text, so use the layout transition as the bounded signal.
                surrounding_prose_boundary = block
                break
        if details and details[-1].startswith("***"):
            previous_body = details[-1].split("***", 2)[-1].strip()
            if not previous_body or not re.search(r"[.!?:]$", previous_body):
                details[-1] = f"{details[-1]} {text}"
                continue
        if (
            details
            and not details[-1].startswith(("## ", "**"))
            and not re.search(r"[.!?:]$", details[-1])
        ):
            joined = f"{details[-1]} {text}"
            joined_entry = _ocr_structural_entry_match(joined)
            details[-1] = (
                f"***{joined_entry.group(1)}.*** "
                f"{joined_entry.group(2)}".rstrip()
                if joined_entry is not None
                else joined
            )
            continue
        if (
            details
            and re.search(
                r"(?i)\b(?:Melee|Ranged|Melee\s+or\s+Ranged)\s+"
                r"(?:Weapon|Spell)$",
                details[-1],
            )
            and re.match(r"(?i)^Attack:\s*", text)
        ):
            details[-1] = f"{details[-1]} {text}"
            continue
        if (
            details
            and re.search(r"(?i)\bone$", details[-1])
            and re.match(
                r"(?i)^(?:Tiny|Small|Medium|Large|Huge|Gargantuan)"
                r"(?:\s+or\s+(?:Tiny|Small|Medium|Large|Huge|Gargantuan|"
                r"smaller|larger))?\s+creatures?\.\s+Hit:",
                text,
            )
        ):
            details[-1] = f"{details[-1]} {text}"
            continue
        reviewed_detail = detail_field_by_index.get(block["index"])
        if reviewed_detail is not None:
            label, joined = reviewed_detail
            details.append(f"**{label}** {_strip_ocr_label(joined['text'], label)}")
            continue
        field = next(
            (
                label
                for label in _OCR_FIELD_LABELS[3:]
                if re.match(rf"^{re.escape(label)}\s+\S", text)
            ),
            None,
        )
        if field is not None:
            details.append(f"**{field}** {_strip_ocr_label(text, field)}")
            continue
        if entry:
            details.append(f"***{entry.group(1)}.*** {entry.group(2)}".rstrip())
            if entered_action_section:
                action_entry_count += 1
        elif details and details[-1].startswith("***"):
            details[-1] = f"{details[-1]} {text}"
        else:
            details.append(text)

    scores = [
        re.sub(r"^(\d+)\s*\(", r"\1 (", ability_values[ability]["text"])
        for ability in ("STR", "DEX", "CON", "INT", "WIS", "CHA")
    ]
    content = "\n\n".join(
        [
            f"# {name}",
            f"*{identity['text']}*",
            *[
                f"**{label}** {_strip_ocr_label(core_fields[label]['text'], label)}"
                for label in ("Armor Class", "Hit Points", "Speed")
            ],
            "| STR | DEX | CON | INT | WIS | CHA |",
            "|---:|---:|---:|---:|---:|---:|",
            "| " + " | ".join(scores) + " |",
            *details,
        ]
    )
    try:
        parsed = parse_2014_statblock_template_preview(
            content,
            source_key="ocr-layout-recovery",
            name=name,
        )
    except ValueError as error:
        raise StatblockImportError(
            f"OCR statblock failed D&D sheet validation: {error}"
        ) from error
    critical_facts = {
        "identity": identity["text"],
        "armor_class": _strip_ocr_label(
            core_fields["Armor Class"]["text"], "Armor Class"
        ),
        "hit_points": _strip_ocr_label(
            core_fields["Hit Points"]["text"], "Hit Points"
        ),
        "speed": _strip_ocr_label(core_fields["Speed"]["text"], "Speed"),
        "abilities": {
            ability.casefold(): ability_values[ability]["text"]
            for ability in ("STR", "DEX", "CON", "INT", "WIS", "CHA")
        },
        "fields": {
            label: _strip_ocr_label(block["text"], label)
            for label, block in detail_fields.items()
        },
        "challenge": (
            _strip_ocr_label(challenge["text"], "Challenge")
            if challenge is not None
            else None
        ),
    }
    return {
        "normalized_content": content,
        "critical_facts": critical_facts,
        "validation": {
            "name": parsed.name,
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "normalization_notes": list(parsed.normalization_notes),
        },
        "evidence": {
            "recovery_version": OCR_STATBLOCK_RECOVERY_VERSION,
            "page_number": layout.get("page_number"),
            "heading": heading["text"],
            "heading_match_mode": heading_match_mode,
            "heading_confidence": heading["confidence"],
            "identity_spacing_repair": (
                {
                    "source_text": identity_source_text,
                    "normalized_text": normalized_identity,
                }
                if identity_source_text != normalized_identity
                else None
            ),
            "reviewed_ocr_corrections": (
                {
                    "abilities": {
                        ability.lower(): value
                        for ability, value in dict(reviewed_ability_scores or {}).items()
                    }
                }
                if reviewed_ability_scores
                else None
            ),
            "matching_heading_count": len(headings),
            "structural_heading_count": len(structural_headings),
            "fuzzy_heading_count": len(fuzzy_headings),
            "statblock_slot": statblock_slot,
            "statblock_slot_summary": (
                {
                    key: value
                    for key, value in selected_slot.items()
                    if not key.startswith("_")
                }
                if selected_slot is not None
                else None
            ),
            "minimum_core_confidence": min(block["confidence"] for block in critical),
            "block_count": len(scoped),
            "cross_column_continuation_block_count": len(continuation_blocks),
            "ability_modifier_repairs": [
                {
                    "ability": ability,
                    "source_text": ability_values[ability]["source_text"],
                    "normalized_text": ability_values[ability]["text"],
                }
                for ability in _OCR_ABILITY_ORDER
                if ability_values[ability]["source_text"]
                != ability_values[ability]["text"]
            ],
            "ability_label_repairs": [
                {
                    "ability": ability,
                    "basis": ability_labels[ability]["inferred_from"],
                }
                for ability in _OCR_ABILITY_ORDER
                if ability_labels[ability].get("inferred_from")
            ],
            "excluded_page_furniture_count": len(page_furniture),
            "excluded_trailing_subject_heading_count": len(
                trailing_subject_headings
            ),
            "surrounding_prose_boundary": (
                {
                    "text": surrounding_prose_boundary["text"],
                    "bbox": [
                        surrounding_prose_boundary["x0"],
                        surrounding_prose_boundary["y0"],
                        surrounding_prose_boundary["x1"],
                        surrounding_prose_boundary["y1"],
                    ],
                }
                if surrounding_prose_boundary is not None
                else None
            ),
            "column_split": split,
            "column_bounds": column_bounds,
            "text_only": True,
        },
    }


def finalize_imported_actor_rulings(
    sheet: dict[str, Any],
    *,
    settled_mechanic_ids: Iterable[str] = (),
    settled_card_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Persist direct Agent-ruling boundaries on imported actor content.

    Statblock normalization deliberately does not guess arbitrary passive,
    spell, or action semantics from prose.  A portable addon nevertheless must
    not postpone deciding *how* those entries are resolved until first use.
    This pass records the exact excerpt and the already-reviewed Agent boundary
    while leaving engine-native mechanics and authored primitive plans intact.
    """

    value = deepcopy(sheet)
    settled_mechanics = {str(item) for item in settled_mechanic_ids if str(item)}
    settled_cards = {str(item) for item in settled_card_ids if str(item)}
    content = value.setdefault("content", {})
    for section in ("activities", "features", "feats", "spells"):
        entries = list(content.get(section) or [])
        for entry in entries:
            effect = str(
                entry.get("description")
                or dict(entry.get("definition") or {}).get("effect")
                or ""
            ).strip()
            if not effect:
                continue
            if str(entry.get("id") or "") in settled_cards:
                continue
            mechanic_refs = {
                str(item) for item in entry.get("mechanic_refs", []) if str(item)
            }
            if mechanic_refs & settled_mechanics:
                continue
            if (
                entry.get("resolution") is not None
                or entry.get("resolution_plan") is not None
            ):
                continue
            manual_ruling = dict(
                dict(entry.get("choices") or {}).get("manual_ruling") or {}
            )
            if (
                manual_ruling.get("default_resolver") == "agent"
                and str(manual_ruling.get("source_excerpt") or "").strip()
            ):
                continue
            requirements = list(entry.get("ruling_requirements") or [])
            if any(
                isinstance(item, dict)
                and item.get("default_resolver") == "agent"
                and str(item.get("source_excerpt") or "").strip()
                for item in requirements
            ):
                continue
            if str(entry.get("pack_id") or "") in {
                "dnd5e.content.srd2014",
                "dnd5e.content.srd2024",
                "dnd5e.content.standard2014",
            }:
                # Bundled standard cards must arrive with their reviewed
                # build-time clause. A caller-supplied core-looking id is not
                # enough to turn unknown prose into a trusted ruling.
                continue
            requirements.append(
                {
                    "kind": "source_bound_import_resolution",
                    "reason": (
                        "This imported actor-card entry has source-specific semantics "
                        "without an exact registered kernel mechanic or primitive plan. "
                        "Resolve the cited text through the Agent-as-DM boundary and "
                        "ordinary public engine tools."
                    ),
                    "source_excerpt": " ".join(effect.split())[:4000],
                    "default_resolver": "agent",
                    "ruling_kind": (
                        "generic_spell_effect"
                        if section == "spells"
                        else "agent_dm_adjudication"
                    ),
                    "policy_ref": "actor_card.import.v1",
                    "requires_external_input_only_for": [],
                }
            )
            entry["ruling_requirements"] = requirements
        content[section] = entries
    value["content"] = content
    inventory = value.setdefault("inventory", {})
    items = list(inventory.get("items") or [])
    for item in items:
        mechanics = dict(item.get("mechanics") or {})
        effect = str(mechanics.get("on_hit_effect") or "").strip()
        if not effect or mechanics.get("on_hit_resolution") is not None:
            continue
        if str(item.get("id") or "") in settled_cards:
            continue
        mechanic_refs = {
            str(entry) for entry in item.get("mechanic_refs", []) if str(entry)
        }
        if mechanic_refs & settled_mechanics:
            continue
        if item.get("resolution_plan") is not None:
            continue
        requirements = list(item.get("ruling_requirements") or [])
        if any(
            isinstance(requirement, dict)
            and requirement.get("default_resolver") == "agent"
            and str(requirement.get("source_excerpt") or "").strip()
            for requirement in requirements
        ):
            continue
        if str(item.get("pack_id") or "") in {
            "dnd5e.content.srd2014",
            "dnd5e.content.srd2024",
            "dnd5e.content.standard2014",
        }:
            continue
        requirements.append(
            {
                "kind": "source_bound_import_resolution",
                "reason": (
                    "This imported item has a source-specific on-hit effect without "
                    "an exact registered kernel mechanic or primitive plan. Resolve "
                    "the cited text through the Agent-as-DM on-hit ruling boundary "
                    "and ordinary public engine tools."
                ),
                "source_excerpt": " ".join(effect.split())[:4000],
                "default_resolver": "agent",
                "ruling_kind": "attack_on_hit_effect",
                "policy_ref": "actor_card.import.v1",
                "requires_external_input_only_for": [],
            }
        )
        item["ruling_requirements"] = requirements
    inventory["items"] = items
    value["inventory"] = inventory
    return validate_character_sheet(value)


_DEPENDENT_TEMPLATE_GRAMMAR_TOKENS = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
    "level",
    "modifier",
    "proficiency",
    "bonus",
    "times",
    "class",
    "spellcasting",
    "hit point maximum",
    "summoner",
    "in",
)


def _normalize_dependent_template_ocr_tokens(value: str) -> str:
    """Collapse whitespace inserted inside the bounded formula vocabulary."""

    normalized = str(value or "")
    for token in _DEPENDENT_TEMPLATE_GRAMMAR_TOKENS:
        pattern = r"(?<![A-Za-z])" + r"\s*".join(re.escape(char) for char in token)
        pattern += r"(?![A-Za-z])"
        normalized = re.sub(pattern, token, normalized, flags=re.IGNORECASE)
    return normalized


def parameterized_statblock_requirements(source_text: str) -> dict[str, Any] | None:
    """Describe a source statblock whose values depend on its owner or casting.

    Companion statblocks such as class-created constructs are reusable templates,
    not complete monster instances.  Treating their printed formula as broken OCR
    either drops legitimate content or tempts an importer to invent one owner's
    level and ability modifier.  This detector is intentionally narrow: an
    ordinary missing numeric HP value remains an error.
    """

    text = str(source_text or "")
    folded = _normalize_dependent_template_ocr_tokens(
        " ".join(text.split()).casefold()
    )
    parameter_markers = (
        (r"\byour\s+[a-z]+\s+level\b|\byour level\b", "owner_class_level"),
        (
            r"\byour proficiency bonus\b|\bequals your bonus\b|\bpb\b",
            "owner_proficiency_bonus",
        ),
        *(
            (
                rf"\byour {ability} modifier\b",
                f"owner_{ability}_modifier",
            )
            for ability in (
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            )
        ),
        (
            r"\byour spellcasting ability modifier\b",
            "owner_spellcasting_ability_modifier",
        ),
        (r"\byour spell attack modifier\b", "owner_spell_attack_modifier"),
        (r"\byour spell save dc\b", "owner_spell_save_dc"),
        (
            r"\bhalf (?:the )?hit point maximum of (?:its|the) summoner\b|"
            r"\bhalf your hit point maximum\b",
            "owner_hit_point_maximum",
        ),
        (
            r"\b(?:the )?spell(?:'s)? level\b|\blevel of the spell\b|"
            r"\beach spell level\b",
            "casting_slot_level",
        ),
    )
    parameters = [
        parameter
        for pattern, parameter in parameter_markers
        if re.search(pattern, folded)
    ]
    if not parameters:
        return None
    source_expressions = []
    markdown_fields = list(
        re.finditer(
            r"(?ims)^\s*\*\*(?P<label>Armor Class|Hit Points|Proficiency Bonus)\*\*\s+"
            r"(?P<expression>.+?)\s*(?=\r?\n\s*\r?\n|\r?\n\s*\*\*|"
            r"\r?\n\s*#{1,6}\s|\Z)",
            text,
        )
    )
    # PDF layout extraction can legitimately flatten one statblock row into a
    # single chunk before the Markdown normalizer has inserted emphasis.  Keep
    # the fallback bounded by printed core-field labels so ordinary prose that
    # happens to mention hit points never becomes an actor template.
    for match in markdown_fields:
        excerpt = " ".join(match.group(0).split())
        if any(re.search(pattern, excerpt.casefold()) for pattern, _ in parameter_markers):
            target_path = {
                "armor class": "combat.armor_class",
                "hit points": "combat.hp.max",
                "proficiency bonus": "combat.proficiency_bonus",
            }[match.group("label").casefold()]
            source_expressions.append(
                {
                    "target_path": target_path,
                    "source_expression": " ".join(match.group("expression").split()),
                    "source_excerpt": excerpt,
                }
            )
    if not markdown_fields:
        flat_core = re.search(
            r"(?s)(?<![A-Za-z])Armor Class\s+(?P<armor_class>.+?)\s+"
            r"Hit Points\s+(?P<hit_points>.+?)\s+Speed\b",
            text,
        )
        if flat_core is not None:
            for label, group, target_path in (
                ("Armor Class", "armor_class", "combat.armor_class"),
                ("Hit Points", "hit_points", "combat.hp.max"),
            ):
                expression = " ".join(flat_core.group(group).split())
                excerpt = f"{label} {expression}"
                if any(
                    re.search(pattern, excerpt.casefold())
                    for pattern, _ in parameter_markers
                ):
                    source_expressions.append(
                        {
                            "target_path": target_path,
                            "source_expression": expression,
                            "source_excerpt": excerpt,
                        }
                    )
    if not source_expressions:
        # A spell effect that scales with the expended slot or spell level is
        # runtime effect semantics, not an actor-template parameter.  Only a
        # printed core card field can make the actor itself dependent on its
        # owner or summoning cast; ordinary effects stay on the complete actor
        # card and receive their build-time kernel/Agent ruling resolution.
        return None
    source_variant_options = sorted(
        {
            label
            for match in re.finditer(r"\(([^()]+?)\s+Only\)", text, re.IGNORECASE)
            for label in _variant_labels(match.group(1))
        }
    )
    solution = compile_parameterized_statblock_solution(
        source_expressions,
        parameters=parameters,
        variant_options=source_variant_options,
    )
    return {
        "schema_version": 1,
        "kind": "dependent_actor_template",
        "target_path": source_expressions[0]["target_path"],
        "source_expression": source_expressions[0]["source_expression"],
        "source_excerpt": source_expressions[0]["source_excerpt"],
        "source_expressions": source_expressions,
        "parameters": list(dict.fromkeys(parameters)),
        "variant_options": source_variant_options,
        "instantiation_phase": "lobby_play_or_combat",
        "solution": solution,
        "runtime_ready": solution is not None,
    }


_DEPENDENT_TEMPLATE_NUMERIC_PARAMETERS = frozenset(
    {
        "owner_class_level",
        "owner_proficiency_bonus",
        "owner_strength_modifier",
        "owner_dexterity_modifier",
        "owner_constitution_modifier",
        "owner_intelligence_modifier",
        "owner_wisdom_modifier",
        "owner_charisma_modifier",
        "owner_spellcasting_ability_modifier",
        "owner_spell_attack_modifier",
        "owner_spell_save_dc",
        "owner_hit_point_maximum",
        "casting_slot_level",
    }
)


def _normalized_template_expression(value: str) -> str:
    """Normalize bounded PDF/OCR noise without changing formula meaning."""

    text = _normalize_dependent_template_ocr_tokens(
        " ".join(str(value or "").split()).casefold()
    )
    replacements = {
        "intell igence": "intelligence",
        "i ntell igence": "intelligence",
        "l 0": "10",
        "l s": "15",
        "for.each": "for each",
        "3 rd": "3rd",
        "4 th": "4th",
        "5 th": "5th",
        "6 th": "6th",
        "on ly": "only",
        "ha·s": "has",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"^(?:equals to|equal to|equals|equal)\s+", "", text)
    return text.strip(" .")


def _template_parameter_for_owner_level(expression: str) -> str | None:
    if re.fullmatch(
        r"your (?:[a-z]+ )?level(?: in this class)?|your level in this class",
        expression,
    ):
        return "owner_class_level"
    return None


def _template_sum_term(term: str) -> dict[str, Any] | None:
    value = term.strip()
    if re.fullmatch(r"\d+", value):
        return {"op": "constant", "value": int(value)}
    owner_level = _template_parameter_for_owner_level(value)
    if owner_level:
        return {"op": "parameter", "name": owner_level}
    level_multiplier = re.fullmatch(
        r"(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
        r"times (?P<level>your (?:[a-z]+ )?level(?: in this class)?|"
        r"your level in this class)",
        value,
    )
    if level_multiplier is not None:
        raw_count = level_multiplier.group("count")
        coefficient = int(raw_count) if raw_count.isdigit() else _NUMBER_WORDS[raw_count]
        return {
            "op": "multiply",
            "coefficient": coefficient,
            "term": {"op": "parameter", "name": "owner_class_level"},
        }
    owner_modifier = re.fullmatch(
        r"your (strength|dexterity|constitution|intelligence|wisdom|charisma) "
        r"modifier",
        value,
    )
    if owner_modifier is not None:
        return {
            "op": "parameter",
            "name": f"owner_{owner_modifier.group(1)}_modifier",
        }
    if value == "your spellcasting ability modifier":
        return {"op": "parameter", "name": "owner_spellcasting_ability_modifier"}
    if value in {"pb", "your proficiency bonus", "your bonus"}:
        return {"op": "parameter", "name": "owner_proficiency_bonus"}
    if value in {"the level of the spell", "the spell's level", "spell level"}:
        return {"op": "parameter", "name": "casting_slot_level"}
    if value in {
        "half the hit point maximum of its summoner",
        "half the hit point maximum of the summoner",
        "half hit point maximum of its summoner",
        "half your hit point maximum",
    }:
        return {
            "op": "floor_divide",
            "divisor": 2,
            "term": {"op": "parameter", "name": "owner_hit_point_maximum"},
        }
    self_modifier = re.fullmatch(
        r"(?:the )?.+?[’']s (strength|dexterity|constitution|intelligence|wisdom|"
        r"charisma) modifier",
        value,
    )
    if self_modifier is not None:
        return {"op": "self_ability_modifier", "ability": self_modifier.group(1)}
    scaled = re.fullmatch(
        r"(?P<amount>\d+) for each spell level above (?P<base>\d+)(?:st|nd|rd|th)",
        value,
    )
    if scaled is not None:
        return {
            "op": "scale_above",
            "parameter": "casting_slot_level",
            "baseline": int(scaled.group("base")),
            "per_step": int(scaled.group("amount")),
        }
    return None


def _variant_labels(value: str) -> list[str]:
    normalized = re.sub(r"\bonly\b", "", value.casefold())
    return [
        label.strip().replace(" ", "_")
        for label in re.split(r"\s*(?:,|\band\b)\s*", normalized)
        if label.strip()
    ]


def _compile_template_expression(source_expression: str) -> dict[str, Any] | None:
    expression = _normalized_template_expression(source_expression)
    # Printed Hit Dice and natural-armor annotations describe the resulting
    # card but do not participate in the numeric formula.
    expression = re.sub(r"\s*\(the .+? hit dice.+?\)\s*$", "", expression)
    expression = re.sub(r"\s*\(natural armor\)\s*", " ", expression).strip()

    # Form-specific bases such as the summon spells in Tasha's are finite,
    # source-authored choices.  They become an enum, never a free-form input.
    variant_prefix = re.match(
        r"^(?P<variants>\d+ \([^)]+ only\)(?: or \d+ \([^)]+ only\))+)(?P<rest>.*)$",
        expression,
    )
    terms: list[dict[str, Any]] = []
    if variant_prefix is not None:
        options: dict[str, int] = {}
        for value, labels in re.findall(
            r"(\d+) \(([^)]+ only)\)", variant_prefix.group("variants")
        ):
            for label in _variant_labels(labels):
                options[label] = int(value)
        if not options:
            return None
        terms.append(
            {
                "op": "variant",
                "parameter": "template_variant",
                "options": options,
            }
        )
        expression = variant_prefix.group("rest").strip()
        expression = expression.removeprefix("+").strip()

    for raw_term in (part.strip() for part in expression.split("+")):
        if not raw_term:
            continue
        variant_addition = re.fullmatch(r"(\d+) \(([^)]+ only)\)", raw_term)
        if variant_addition is not None:
            options = {
                label: int(variant_addition.group(1))
                for label in _variant_labels(variant_addition.group(2))
            }
            terms.append(
                {
                    "op": "variant",
                    "parameter": "template_variant",
                    "options": options,
                    "default": 0,
                }
            )
            continue
        compiled = _template_sum_term(raw_term)
        if compiled is None:
            return None
        terms.append(compiled)
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else {"op": "sum", "terms": terms}


def _dependent_template_solution_hash(
    source_expressions: Iterable[Mapping[str, Any]],
) -> str:
    canonical = "\n".join(
        f"{str(item.get('target_path') or '')}\0"
        f"{' '.join(str(item.get('source_expression') or '').split())}"
        for item in source_expressions
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_parameterized_statblock_solution(
    source_expressions: Iterable[Mapping[str, Any]],
    *,
    parameters: Iterable[str] = (),
    variant_options: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Compile exact printed card fields to a closed, data-only formula plan."""

    expressions = [dict(item) for item in source_expressions]
    compiled_fields: list[dict[str, Any]] = []
    for expression in expressions:
        target_path = str(expression.get("target_path") or "")
        if target_path not in {
            "combat.armor_class",
            "combat.hp.max",
            "combat.proficiency_bonus",
        }:
            return None
        formula = _compile_template_expression(
            str(expression.get("source_expression") or "")
        )
        if formula is None:
            return None
        compiled_fields.append(
            {
                "target_path": target_path,
                "source_expression": " ".join(
                    str(expression.get("source_expression") or "").split()
                ),
                "formula": formula,
            }
        )
    if not compiled_fields:
        return None
    numeric_parameters: set[str] = {
        str(value) for value in parameters if str(value)
    }
    variants: set[str] = {str(value) for value in variant_options if str(value)}

    def collect(node: Mapping[str, Any]) -> None:
        operation = node.get("op")
        if operation == "parameter":
            numeric_parameters.add(str(node.get("name") or ""))
        elif operation == "scale_above":
            numeric_parameters.add(str(node.get("parameter") or ""))
        elif operation == "variant":
            variants.update(str(value) for value in dict(node.get("options") or {}))
        elif operation == "multiply":
            collect(dict(node.get("term") or {}))
        elif operation == "floor_divide":
            collect(dict(node.get("term") or {}))
        elif operation == "sum":
            for term in node.get("terms") or []:
                collect(dict(term))

    for field in compiled_fields:
        collect(dict(field["formula"]))
    owner_class_names = sorted(
        {
            match.group(1)
            for expression in expressions
            for match in re.finditer(
                r"\byour ([a-z]+) level\b",
                _normalized_template_expression(
                    str(expression.get("source_expression") or "")
                ),
            )
        }
    )
    return {
        "schema_version": 1,
        "kind": "bounded_statblock_formula_plan",
        "reviewed_expression_hash": _dependent_template_solution_hash(expressions),
        "fields": compiled_fields,
        "numeric_parameters": sorted(numeric_parameters),
        "owner_class_names": owner_class_names,
        "variant_parameter": "template_variant" if variants else None,
        "variant_options": sorted(variants),
    }


def dependent_actor_template_solution_errors(
    requirement: Mapping[str, Any],
) -> list[str]:
    """Validate a portable formula plan and its immutable source binding."""

    errors: list[str] = []
    expressions = requirement.get("source_expressions")
    if not isinstance(expressions, list) or not expressions:
        return ["dependent actor template needs source_expressions"]
    solution = requirement.get("solution")
    if not isinstance(solution, Mapping):
        return ["dependent actor template needs a bounded solution"]
    expected = compile_parameterized_statblock_solution(
        [dict(item) for item in expressions if isinstance(item, Mapping)],
        parameters=(
            [str(value) for value in requirement.get("parameters") or []]
            if isinstance(requirement.get("parameters"), list)
            else []
        ),
        variant_options=(
            [str(value) for value in requirement.get("variant_options") or []]
            if isinstance(requirement.get("variant_options"), list)
            else []
        ),
    )
    if expected is None:
        return ["dependent actor template source expressions are not safely compilable"]
    if dict(solution) != expected:
        errors.append("dependent actor template solution is stale or unsupported")
    declared = requirement.get("parameters")
    if not isinstance(declared, list) or any(
        str(value) not in _DEPENDENT_TEMPLATE_NUMERIC_PARAMETERS for value in declared
    ):
        errors.append("dependent actor template parameters are unsupported")
    owner_class_name = requirement.get("owner_class_name")
    if owner_class_name is not None and (
        not isinstance(owner_class_name, str)
        or not owner_class_name.strip()
        or len(owner_class_name) > 200
    ):
        errors.append("dependent actor template owner_class_name is invalid")
    if requirement.get("runtime_ready") is not True:
        errors.append("dependent actor template is not runtime-ready")
    return errors


def _evaluate_dependent_template_formula(
    formula: Mapping[str, Any],
    *,
    numeric_parameters: Mapping[str, int],
    self_ability_modifiers: Mapping[str, int],
    template_variant: str | None,
) -> int:
    operation = str(formula.get("op") or "")
    if operation == "constant":
        return int(formula["value"])
    if operation == "parameter":
        return int(numeric_parameters[str(formula["name"])])
    if operation == "self_ability_modifier":
        return int(self_ability_modifiers[str(formula["ability"])])
    if operation == "multiply":
        return int(formula["coefficient"]) * _evaluate_dependent_template_formula(
            dict(formula["term"]),
            numeric_parameters=numeric_parameters,
            self_ability_modifiers=self_ability_modifiers,
            template_variant=template_variant,
        )
    if operation == "floor_divide":
        divisor = int(formula["divisor"])
        if divisor < 1:
            raise ValueError("dependent actor divisor must be positive")
        return _evaluate_dependent_template_formula(
            dict(formula["term"]),
            numeric_parameters=numeric_parameters,
            self_ability_modifiers=self_ability_modifiers,
            template_variant=template_variant,
        ) // divisor
    if operation == "scale_above":
        value = int(numeric_parameters[str(formula["parameter"])])
        return max(0, value - int(formula["baseline"])) * int(formula["per_step"])
    if operation == "variant":
        options = dict(formula["options"])
        if template_variant in options:
            return int(options[str(template_variant)])
        if "default" in formula:
            return int(formula["default"])
        raise ValueError("template_variant must select a reviewed source option")
    if operation == "sum":
        return sum(
            _evaluate_dependent_template_formula(
                dict(term),
                numeric_parameters=numeric_parameters,
                self_ability_modifiers=self_ability_modifiers,
                template_variant=template_variant,
            )
            for term in formula["terms"]
        )
    raise ValueError("dependent actor template formula operation is unsupported")


def materialize_parameterized_statblock_source(
    source_text: str,
    requirement: Mapping[str, Any],
    *,
    numeric_parameters: Mapping[str, int],
    self_ability_modifiers: Mapping[str, int] | None = None,
    template_variant: str | None = None,
    allow_self_modifier_placeholders: bool = False,
) -> tuple[str, dict[str, int]]:
    """Resolve one reviewed template into ordinary statblock text.

    This function never evaluates source text.  It executes only the exact
    formula plan reproduced by the deterministic validator above.
    """

    errors = dependent_actor_template_solution_errors(requirement)
    if errors:
        raise ValueError("; ".join(errors))
    solution = dict(requirement["solution"])
    expected_parameters = set(solution["numeric_parameters"])
    supplied = set(numeric_parameters)
    if supplied != expected_parameters:
        missing = sorted(expected_parameters - supplied)
        unknown = sorted(supplied - expected_parameters)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unsupported " + ", ".join(unknown))
        raise ValueError("template numeric parameters are invalid: " + "; ".join(details))
    normalized_parameters: dict[str, int] = {}
    for name, raw_value in numeric_parameters.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise ValueError(f"template parameter {name} must be an integer")
        if name in {"owner_class_level", "owner_proficiency_bonus", "casting_slot_level"}:
            if raw_value < 1 or raw_value > 30:
                raise ValueError(f"template parameter {name} is outside its bounded range")
        elif name == "owner_hit_point_maximum":
            if raw_value < 1 or raw_value > 100_000:
                raise ValueError(f"template parameter {name} is outside its bounded range")
        elif raw_value < -10 or raw_value > 40:
            raise ValueError(f"template parameter {name} is outside its bounded range")
        normalized_parameters[str(name)] = raw_value
    options = list(solution.get("variant_options") or [])
    normalized_variant = (
        str(template_variant).strip().casefold().replace(" ", "_")
        if template_variant is not None
        else None
    )
    if options and normalized_variant not in options:
        raise ValueError("template_variant must be one of: " + ", ".join(options))
    if not options and normalized_variant is not None:
        raise ValueError("template_variant is not accepted by this template")
    self_values = dict(self_ability_modifiers or {})
    rendered = str(source_text)
    resolved: dict[str, int] = {}
    for field in solution["fields"]:
        formula = dict(field["formula"])
        try:
            result = _evaluate_dependent_template_formula(
                formula,
                numeric_parameters=normalized_parameters,
                self_ability_modifiers=self_values,
                template_variant=normalized_variant,
            )
        except KeyError as error:
            if not allow_self_modifier_placeholders:
                raise ValueError(
                    f"template needs self ability modifier {error.args[0]}"
                ) from error
            result = 1
        if result < 0 or result > 100_000:
            raise ValueError("template formula result is outside its bounded range")
        source_expression = str(field["source_expression"])
        expression_pattern = re.compile(
            r"\s+".join(re.escape(part) for part in source_expression.split()),
            re.IGNORECASE,
        )
        rendered, count = expression_pattern.subn(str(result), rendered, count=1)
        if count != 1:
            raise ValueError("template source expression no longer matches reviewed text")
        resolved[str(field["target_path"])] = result

    token_values = {
        "owner_proficiency_bonus": lambda value: f"{value:+d}",
        "owner_spell_attack_modifier": lambda value: f"{value:+d}",
        "owner_spell_save_dc": str,
        "owner_spellcasting_ability_modifier": lambda value: f"{value:+d}",
        "owner_strength_modifier": lambda value: f"{value:+d}",
        "owner_dexterity_modifier": lambda value: f"{value:+d}",
        "owner_constitution_modifier": lambda value: f"{value:+d}",
        "owner_intelligence_modifier": lambda value: f"{value:+d}",
        "owner_wisdom_modifier": lambda value: f"{value:+d}",
        "owner_charisma_modifier": lambda value: f"{value:+d}",
    }
    token_patterns = {
        "owner_proficiency_bonus": r"\byour proficiency bonus\b",
        "owner_spell_attack_modifier": r"\byour spell attack modifier\b",
        "owner_spell_save_dc": r"\byour spell save dc\b",
        "owner_spellcasting_ability_modifier": r"\byour spellcasting ability modifier\b",
        **{
            f"owner_{ability}_modifier": rf"\byour {ability} modifier\b"
            for ability in (
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            )
        },
    }
    for name, pattern in token_patterns.items():
        if name not in normalized_parameters:
            continue
        rendered = re.sub(
            pattern,
            token_values[name](normalized_parameters[name]),
            rendered,
            flags=re.IGNORECASE,
        )
    if "owner_proficiency_bonus" in normalized_parameters:
        rendered = re.sub(
            r"(?<![A-Za-z])PB(?![A-Za-z])",
            str(normalized_parameters["owner_proficiency_bonus"]),
            rendered,
        )
    return rendered, resolved


def parse_2014_statblock_template_preview(
    source_text: str,
    *,
    source_key: str,
    rule_refs: list[str] | tuple[str, ...] = (),
    name: str | None = None,
) -> ParsedStatblock:
    """Validate an ordinary or dependent 2014 statblock without erasing formulas.

    Dependent companion cards cannot be parsed until their owner or casting
    parameters are known.  Build-time OCR and review still need to prove that
    the complete card is structurally executable.  Materialize only a bounded
    in-memory preview, then return its parsed shape while callers retain the
    original source text and reviewed expressions.
    """

    requirement = parameterized_statblock_requirements(source_text)
    if requirement is None:
        return parse_2014_statblock(
            source_text,
            source_key=source_key,
            rule_refs=rule_refs,
            name=name,
        )
    errors = dependent_actor_template_solution_errors(requirement)
    if errors:
        raise StatblockImportError(
            "dependent statblock template is not runtime-ready: " + "; ".join(errors)
        )
    preview_values = {
        "owner_class_level": 10,
        "owner_proficiency_bonus": 4,
        "owner_spell_attack_modifier": 8,
        "owner_spell_save_dc": 16,
        "owner_hit_point_maximum": 101,
        "owner_spellcasting_ability_modifier": 4,
        "owner_strength_modifier": 4,
        "owner_dexterity_modifier": 4,
        "owner_constitution_modifier": 4,
        "owner_intelligence_modifier": 4,
        "owner_wisdom_modifier": 4,
        "owner_charisma_modifier": 4,
        "casting_slot_level": 9,
    }
    required_parameters = set(
        dict(requirement.get("solution") or {}).get("numeric_parameters") or []
    )
    rendered, _resolved = materialize_parameterized_statblock_source(
        source_text,
        requirement,
        numeric_parameters={
            parameter: preview_values[parameter] for parameter in required_parameters
        },
        self_ability_modifiers={},
        template_variant=(
            str(dict(requirement["solution"])["variant_options"][0])
            if dict(requirement["solution"]).get("variant_options")
            else None
        ),
        allow_self_modifier_placeholders=True,
    )
    return parse_2014_statblock(
        rendered,
        source_key=source_key,
        rule_refs=rule_refs,
        name=name,
    )


def apply_dependent_actor_template_variant(
    sheet: Mapping[str, Any],
    requirement: Mapping[str, Any],
    *,
    template_variant: str | None,
) -> dict[str, Any]:
    """Remove entries explicitly restricted to a different reviewed form."""

    value = deepcopy(dict(sheet))
    solution = dict(requirement.get("solution") or {})
    options = {
        str(option) for option in solution.get("variant_options") or []
    }
    if not options:
        if template_variant is not None:
            raise ValueError("template_variant is not accepted by this template")
        return value
    selected = str(template_variant or "").strip().casefold().replace(" ", "_")
    if selected not in options:
        raise ValueError("template_variant must select a reviewed source option")

    restriction = re.compile(r"\((?P<labels>[^()]+?)\s+only\)", re.IGNORECASE)

    def available(entry: Mapping[str, Any]) -> bool:
        text = " ".join(
            str(entry.get(field) or "") for field in ("name", "description")
        )
        match = restriction.search(text)
        if match is None:
            return True
        labels = set(_variant_labels(match.group("labels")))
        bounded_labels = labels & options
        return not bounded_labels or selected in bounded_labels

    content = dict(value.get("content") or {})
    for section in ("activities", "features", "feats", "spells"):
        entries = content.get(section)
        if isinstance(entries, list):
            content[section] = [
                deepcopy(dict(entry))
                for entry in entries
                if isinstance(entry, Mapping) and available(entry)
            ]
    value["content"] = content
    return validate_character_sheet(value)


__all__ = [
    "ParsedStatblock",
    "OCR_STATBLOCK_RECOVERY_VERSION",
    "StatblockImportError",
    "apply_statblock_variant",
    "apply_dependent_actor_template_variant",
    "effective_statblock_rating",
    "finalize_imported_actor_rulings",
    "compile_parameterized_statblock_solution",
    "dependent_actor_template_solution_errors",
    "materialize_parameterized_statblock_source",
    "parameterized_statblock_requirements",
    "parse_2014_statblock_template_preview",
    "discover_2014_statblock_names_from_layout",
    "discover_2014_statblock_slots_from_layout",
    "parse_2014_statblock",
    "parse_2024_statblock",
    "recover_2014_statblock_from_ocr",
    "split_2014_statblock_action_variants",
]
