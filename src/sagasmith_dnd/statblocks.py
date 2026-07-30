"""Strict, source-bound import of SRD-style D&D creature statblocks."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sagasmith_core.text import ascii_slug, compact_ascii_key

from sagasmith_dnd.abilities import ABILITY_ABBREVIATIONS, ABILITY_NAMES
from sagasmith_dnd.activity_identity import (
    MULTIATTACK_MECHANIC_ID,
    is_multiattack_activity,
    is_multiattack_source_name,
)
from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
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


OCR_STATBLOCK_RECOVERY_VERSION = 5


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
        (r"sling", r"sling\s+stones?", "Sling Stones"),
        (r"(?:short|long)bow", r"arrows?", "Arrows"),
        (r"(?:hand|light|heavy)\s+crossbow", r"bolts?", "Bolts"),
        (r"blowgun", r"needles?", "Needles"),
    )
    ammunition = next(
        (
            (pattern, label)
            for weapon_pattern, pattern, label in ammunition_patterns
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
    ammunition_pattern, ammunition_label = ammunition
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
            rf"carries\s+(?P<count>{count_pattern})\s+"
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
    attack = re.search(
        r"(?i)\*?(Melee|Ranged|Melee or Ranged)\s+(Weapon|Spell)\s+Attack:\*?\s*"
        r"([+\-−]\s*\d+)\s+to hit",
        description,
    )
    if not attack:
        return None
    mode = attack.group(1).casefold()
    hit = re.search(
        r"(?i)\*?Hit:\*?\s*(\d+)"
        r"(?:\s*\((\d+\s*d\s*\d+(?:\s*[+\-]\s*\d+)?"
        r"(?:\s+plus\s+\d+\s*d\s*\d+(?:\s*[+\-]\s*\d+)?)*)\))?\s*"
        r"([a-z]+)\s+damage",
        description,
    )
    additional_damage: list[dict[str, Any]] = []
    complete_structured_on_hit: dict[str, Any] | None = None
    if hit:
        expression_parts = re.split(
            r"(?i)\s+plus\s+",
            hit.group(2) or hit.group(1),
        )
        expression = re.sub(r"\s+", "", expression_parts[0])
        damage = re.fullmatch(r"(\d+d\d+)(?:([+\-]\d+))?", expression)
        if hit.group(2) and not damage:
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
                    "damage_type": hit.group(3).casefold(),
                }
            )
        last_damage_end = hit.end()
        versatile_damage_formula = ""
        versatile = re.match(
            (
                r"(?i)\s*,?\s*or\s+\d+\s*"
                r"\((\d+\s*d\s*\d+)(?:\s*([+\-])\s*(\d+))?\)\s*"
                rf"{re.escape(hit.group(3))}\s+damage\s+"
                r"if\s+used\s+with\s+two\s+hands\s+to\s+make\s+a\s+melee\s+attack"
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
            additional_damage.append(
                {
                    "damage_formula": parsed_extra.group(1),
                    "damage_bonus": int(parsed_extra.group(2) or 0),
                    "damage_type": extra.group(2).casefold(),
                }
            )
            last_damage_end = hit.end() + extra.end()
        raw_on_hit_effect = description[last_damage_end:]
        complete_structured_on_hit = (
            _armor_corrosion_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
            or _ignition_ongoing_damage_on_hit(
                raw_on_hit_effect.strip().lstrip(". ,;").strip()
            )
        )
        trailing_paragraph_match = re.search(r"\n\s*\n", raw_on_hit_effect)
        trailing_paragraph_prose = ""
        if trailing_paragraph_match and complete_structured_on_hit is None:
            trailing_paragraph_prose = raw_on_hit_effect[
                trailing_paragraph_match.end() :
            ].strip()
            raw_on_hit_effect = raw_on_hit_effect[: trailing_paragraph_match.start()]
        on_hit_effect = raw_on_hit_effect.strip().lstrip(". ,;").strip()
        damage_formula = damage.group(1) if damage else expression
        damage_type = hit.group(3).casefold()
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
    elif actor_lore_match:
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
            if attack.group(2).casefold() == "spell"
            else "dexterity"
            if mode == "ranged"
            else "strength"
        ),
        "damage_formula": damage_formula,
        "damage_type": damage_type,
        "additional_damage": additional_damage,
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
                weapon = next(item for item in items if item["id"] == weapon_id)
                mechanics = dict(weapon.get("mechanics") or {})
                modes = [str(mechanics.get("attack_type") or "melee")]
                if "thrown" in {
                    str(value).casefold()
                    for value in mechanics.get("properties") or []
                }:
                    modes.append("ranged")
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
                    for mode in modes
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
                weapon = next(item for item in items if item["id"] == weapon_id)
                mechanics = dict(weapon.get("mechanics") or {})
                modes = [str(mechanics.get("attack_type") or "melee")]
                if "thrown" in {
                    str(value).casefold() for value in mechanics.get("properties") or []
                }:
                    modes.append("ranged")
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
                    for mode in modes
                )
            continue
        attack_mode = "ranged" if "ranged attack" in group.casefold() else "melee"
        attacks: list[dict[str, Any]] = []
        for match in re.finditer(
            r"(?i)(one|once|two|twice|three|thrice|four|five|six|\d+)"
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
                r"(?i)(one|two|three|four|five|six|\d+)\s+"
                r"([a-z][a-z '\-]+?)\s+attacks?"
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
        names = [
            re.sub(r"(?:\s*[-*]\s*)+$", "", item.strip()).lstrip("-* ")
            for item in description[header.end() : end].split(",")
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
            _aggressive_source_trait,
            _cunning_action_source_trait,
            _included_weapon_damage_source_trait,
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


def _parry_reaction_defense(description: str) -> dict[str, Any] | None:
    """Structure a complete standard post-hit reaction without trusting its title."""

    normalized = " ".join(description.split())
    match = re.fullmatch(
        r"The (?P<subject>[A-Za-z][A-Za-z '\-]*) adds? "
        r"(?P<bonus>\d+) to (?:its|his|her|their) AC against one melee attack "
        r"that would hit (?:it|him|her|them)\."
        r"(?: To do so, the (?P=subject) must see the attacker and be wielding "
        r"a melee weapon\.)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    bonus = int(match.group("bonus"))
    if bonus <= 0:
        return None
    return {
        "kind": "armor_class_bonus",
        "bonus": bonus,
        "attack_modes": ["melee"],
        "requires_visible_attacker": bool(
            re.search(r"\bmust\s+see\s+the\s+attacker\b", normalized, re.IGNORECASE)
        ),
        "requires_wielded_melee_weapon": bool(
            re.search(
                r"\b(?:be\s+)?wielding\s+a\s+melee\s+weapon\b",
                normalized,
                re.IGNORECASE,
            )
        ),
    }


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
    attack_marker_pattern = re.compile(
        r"(?i)\b(?:Melee|Ranged|Melee or Ranged)\s+"
        r"(?:Weapon|Spell)\s+Attack:\*?"
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
        raise StatblockImportError("statblock has no supported weapon action")
    ids = [item["id"] for item in weapons]
    if len(ids) != len(set(ids)):
        raise StatblockImportError("statblock contains duplicate weapon action names")
    armor_items, armor_slots = _parse_armor_equipment(ac_text, source_key)
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
        source_trait = _source_trait_from_description(source_trait_description)
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
                "aggressive": "bonus action on its turn",
                "cunning_action": "bonus action on its turn",
                "included_weapon_damage": "weapon hit; included in weapon actions",
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
        reaction_defense = (
            _parry_reaction_defense(reaction_description)
            if activation == "reaction"
            else None
        )
        if reaction_defense is not None:
            if reaction_description != description:
                entry["description"] = reaction_description
                normalization_notes.append(
                    f"{entry_name}: trailing creature prose excluded from reaction settlement"
                )
            entry["activation"]["trigger"] = "hit by a melee attack"
            entry["choices"] = {"reaction_defense": reaction_defense}
        if source_trait is None and reaction_defense is None:
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
        if source_trait is None and reaction_defense is None:
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
        "alignment",
        "darkvision_ft",
        "languages",
        "damage_resistances",
        "damage_immunities",
        "damage_vulnerabilities",
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


_OCR_IDENTITY_RE = re.compile(
    r"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)\s+([^,]+),\s*(.+)$"
)
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
    r"^([A-Z][A-Za-z0-9 '/()\-–—]{1,80})\.\s*(.*)$"
)


def _ocr_key(value: str) -> str:
    return compact_ascii_key(value)


def _strip_ocr_label(text: str, label: str) -> str:
    return re.sub(rf"(?i)^{re.escape(label)}\s+", "", text)


def _repair_layout_ocr_text(text: str) -> str:
    """Repair only mechanically bounded OCR substitutions before statblock parsing."""

    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])[lI]d(?=\d)",
        "1d",
        text,
    )
    normalized = re.sub(
        r"(?i)(\bhalf\s+as\s+much\s+)darmage(?=\s+on\b)",
        r"\1damage",
        normalized,
    )
    return re.sub(
        r"(?i)\b(Ignited\s+Illumination),\s+(?=As\s+a\s+bonus\s+action\b)",
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
    candidates = [
        width * fraction / 100
        for fraction in range(30, 71)
    ]
    ranked: list[tuple[int, float, float]] = []
    content_top = min(block["y0"] for block in blocks)
    content_bottom = max(block["y1"] for block in blocks)
    content_span = max(1.0, content_bottom - content_top)
    for split in candidates:
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
            and left_span >= content_span * 0.4
            and right_span >= content_span * 0.4
        ):
            ranked.append((crossing, abs(split - width / 2), split))
    if not ranked:
        return None
    crossing, _distance, split = min(ranked)
    if crossing > max(2, len(blocks) // 20):
        return None
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


def _ocr_heading_has_identity(
    heading: dict[str, Any],
    following: dict[str, Any] | None,
) -> bool:
    """Recognize a statblock heading and identity line with normal OCR box overlap."""

    return bool(
        following is not None
        and _OCR_IDENTITY_RE.fullmatch(following["text"])
        and -20 <= following["y0"] - heading["y1"] <= 80
    )


def _ocr_is_terminal_subject_heading(
    block: dict[str, Any],
    *,
    target_key: str,
) -> bool:
    """Recognize a trailing plural lore heading for the recovered creature."""

    text = str(block["text"]).strip()
    if not text.endswith((".", ":")) or text != text.upper():
        return False
    heading_key = _ocr_key(text.rstrip(".:"))
    plural_keys = {
        f"{target_key}s",
        f"{target_key}es",
        f"{target_key[:-1]}ies" if target_key.endswith("y") else "",
    }
    return heading_key in plural_keys


def recover_2014_statblock_from_ocr(
    layout: dict[str, Any],
    *,
    name: str,
    minimum_confidence: float = 0.8,
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
    structural_headings: list[dict[str, Any]] = []
    for candidate in headings:
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
        if _ocr_heading_has_identity(candidate, following):
            structural_headings.append(candidate)
    if len(headings) == 1:
        heading = headings[0]
    elif len(structural_headings) == 1:
        heading = structural_headings[0]
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
    for index in range(heading_index + 1, len(ordered)):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        if _ocr_peer_heading(ordered[index], following):
            end = index
            break
    unfiltered_scoped = ordered[heading_index:end]
    page_furniture = [
        block
        for block in unfiltered_scoped
        if block["y0"] >= float(height) * 0.9
        and re.fullmatch(r"\d{1,4}", block["text"])
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
    identity = next(
        (block for block in scoped[1:] if _OCR_IDENTITY_RE.fullmatch(block["text"])),
        None,
    )
    if identity is None:
        raise StatblockImportError("OCR statblock has no unambiguous size/type line")

    core_fields: dict[str, dict[str, Any]] = {}
    for label in _OCR_FIELD_LABELS[:3]:
        core_fields[label] = next(
            (
                block
                for block in scoped
                if re.match(rf"(?i)^{re.escape(label)}\s+\S", block["text"])
            ),
            None,
        )
        if core_fields[label] is None:
            raise StatblockImportError(f"OCR statblock is missing {label}")

    ability_labels: dict[str, dict[str, Any]] = {}
    for ability in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        matches = [block for block in scoped if block["text"].upper() == ability]
        if len(matches) != 1:
            raise StatblockImportError(f"OCR statblock requires one {ability} label")
        ability_labels[ability] = matches[0]
    ability_values: dict[str, dict[str, Any]] = {}
    for ability, label_block in ability_labels.items():
        candidates = [
            block
            for block in scoped
            if label_block["y0"] <= block["y0"] <= label_block["y1"] + 60
            and abs(block["cx"] - label_block["cx"]) <= 45
            and re.fullmatch(r"\d+\s*\([+\-]\s*\d+\)", block["text"])
        ]
        if len(candidates) != 1:
            raise StatblockImportError(f"OCR statblock requires one {ability} score")
        ability_values[ability] = candidates[0]
    challenge = next(
        (
            block
            for block in scoped
            if re.match(r"(?i)^Challenge\s+\S", block["text"])
        ),
        None,
    )
    if challenge is None:
        raise StatblockImportError("OCR statblock is missing Challenge")
    detail_fields: dict[str, dict[str, Any]] = {}
    for label in _OCR_FIELD_LABELS[3:-1]:
        matches = [
            block
            for block in scoped
            if re.match(rf"(?i)^{re.escape(label)}\s+\S", block["text"])
        ]
        if len(matches) > 1:
            raise StatblockImportError(f"OCR statblock has ambiguous {label} fields")
        if matches:
            detail_fields[label] = matches[0]

    critical = [
        heading,
        identity,
        *core_fields.values(),
        *ability_labels.values(),
        *ability_values.values(),
        *detail_fields.values(),
        challenge,
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
        *(block["index"] for block in core_fields.values()),
        *(block["index"] for block in ability_labels.values()),
        *(block["index"] for block in ability_values.values()),
    }
    detail_start = max(block["y1"] for block in ability_values.values())
    details: list[str] = []
    for block in scoped:
        if block["index"] in skipped or block["y0"] < detail_start:
            continue
        text = block["text"]
        if text.upper() in {"ACTIONS", "REACTIONS", "LEGENDARY ACTIONS"}:
            details.append(f"## {text.title()}")
            continue
        field = next(
            (
                label
                for label in _OCR_FIELD_LABELS[3:]
                if re.match(rf"(?i)^{re.escape(label)}\s+\S", text)
            ),
            None,
        )
        if field is not None:
            details.append(f"**{field}** {_strip_ocr_label(text, field)}")
            continue
        entry = _OCR_ENTRY_RE.match(text)
        if entry:
            details.append(f"***{entry.group(1)}.*** {entry.group(2)}".rstrip())
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
    parsed = parse_2014_statblock(
        content,
        source_key="ocr-layout-recovery",
        name=name,
    )
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
        "challenge": _strip_ocr_label(challenge["text"], "Challenge"),
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
            "heading_confidence": heading["confidence"],
            "matching_heading_count": len(headings),
            "structural_heading_count": len(structural_headings),
            "minimum_core_confidence": min(block["confidence"] for block in critical),
            "block_count": len(scoped),
            "excluded_page_furniture_count": len(page_furniture),
            "excluded_trailing_subject_heading_count": len(
                trailing_subject_headings
            ),
            "column_split": split,
            "column_bounds": column_bounds,
            "text_only": True,
        },
    }


__all__ = [
    "ParsedStatblock",
    "OCR_STATBLOCK_RECOVERY_VERSION",
    "StatblockImportError",
    "apply_statblock_variant",
    "effective_statblock_rating",
    "parse_2014_statblock",
    "recover_2014_statblock_from_ocr",
]
