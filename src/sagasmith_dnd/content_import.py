"""Conservative candidate extraction for user-imported D&D rule sources."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.abilities import ABILITY_LABELS, ABILITY_NAMES, SKILL_ABILITIES
from sagasmith_dnd.character_schema import normalize_spell_definition
from sagasmith_dnd.content_readiness import (
    background_materializer_errors,
    build_catalog_review,
    build_selection_contract,
    selection_contract_errors,
    selection_schema_for_artifact,
    species_materializer_errors,
)
from sagasmith_dnd.resolution_plan import (
    ResolutionPlanCompilationError,
    compile_resolution_plan,
    resolution_plan_template,
)
from sagasmith_dnd.rule_contract import (
    RuleContractError,
    compile_rule_clauses,
    rule_clause_templates,
    validate_rule_clause_coverage,
)
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    normalize_spell_resolution,
)
from sagasmith_dnd.statblocks import (
    StatblockImportError,
    parameterized_statblock_requirements,
    parry_reaction_settlement,
    parse_2014_statblock,
)

_ITEM_HEADER_RE = re.compile(
    r"(?im)^(?:wondrous item|weapon|armor|potion|ring|rod|scroll|staff|wand)"
    r"(?:\s*\([^\n)]{1,120}\))?\s*(?:,|—|–|-)"
)
_ITEM_CATEGORY_LABELS = {
    "armor": "Armor",
    "potion": "Potion",
    "ring": "Ring",
    "rod": "Rod",
    "scroll": "Scroll",
    "staff": "Staff",
    "wand": "Wand",
    "weapon": "Weapon",
    "wondrousitem": "Wondrous item",
}
_SPELL_LEVEL_RE = re.compile(
    r"(?im)^(?:\d+(?:st|nd|rd|th)[ -]level\s+[a-z]+|[a-z]+\s+cantrip)\b"
)
_STATBLOCK_LABELS = ("armor class", "hit points", "speed", "challenge")
_CLASS_NAMES = {
    "artificer",
    "barbarian",
    "bard",
    "blood hunter",
    "cleric",
    "druid",
    "fighter",
    "monk",
    "paladin",
    "ranger",
    "rogue",
    "sorcerer",
    "warlock",
    "wizard",
}
_SUBCLASS_MINIMUM_LEVELS_2014 = {
    "cleric": 1,
    "sorcerer": 1,
    "warlock": 1,
    "druid": 2,
    "wizard": 2,
}
_GENERIC_TITLES = {
    "background",
    "backgrounds",
    "class",
    "class features",
    "feats",
    "magic items",
    "options",
    "spells",
    "subclass",
}
_GENERIC_SUBCLASS_PARENT_TITLES = {
    "arcane tradition",
    "artificer specialists",
    "bard college",
    "divine domain",
    "druid circle",
    "martial archetype",
    "monastic tradition",
    "otherworldly patron",
    "primal path",
    "ranger archetype",
    "ranger conclave",
    "roguish archetype",
    "sacred oath",
    "sorcerous origin",
}
_GENERIC_SUBCLASS_PARENT_TITLES.update(
    {
        f"{title}s"
        for title in tuple(_GENERIC_SUBCLASS_PARENT_TITLES)
        if not title.endswith("s")
    }
)
_SUBCLASS_PARENT_CLASS_NAMES = {
    "arcane tradition": "Wizard",
    "artificer specialists": "Artificer",
    "bard college": "Bard",
    "divine domain": "Cleric",
    "druid circle": "Druid",
    "martial archetype": "Fighter",
    "monastic tradition": "Monk",
    "otherworldly patron": "Warlock",
    "primal path": "Barbarian",
    "ranger archetype": "Ranger",
    "ranger conclave": "Ranger",
    "roguish archetype": "Rogue",
    "sacred oath": "Paladin",
    "sorcerous origin": "Sorcerer",
}
_SUBCLASS_PARENT_CLASS_NAMES.update(
    {
        f"{title}s": class_name
        for title, class_name in tuple(_SUBCLASS_PARENT_CLASS_NAMES.items())
        if not title.endswith("s")
    }
)
_GENERIC_TITLES.update(_GENERIC_SUBCLASS_PARENT_TITLES)
_GENERIC_FEATURE_TITLES = {
    "class features",
    "equipment",
    "hit points",
    "proficiencies",
    "quick build",
}
_GENERIC_FEATURE_TITLES.update(_GENERIC_SUBCLASS_PARENT_TITLES)
_STANDARD_FLAT_SUBCLASS_TITLES = {
    "arcane trickster",
    "assassin",
    "battle master",
    "beast master",
    "champion",
    "draconic bloodline",
    "eldritch knight",
    "hunter",
    "the archfey",
    "the fiend",
    "the great old one",
    "thief",
    "wild magic",
}
_STATBLOCK_PLACEHOLDER_TITLES = {
    "character name",
    "creature name",
    "monster name",
}
_PAGE_HEADER_RE = re.compile(r"(?i)^(?:chapter|part|appendix)\b")
_STATBLOCK_FIELD_LABELS = (
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
_CREATURE_CORE_RE = re.compile(
    r"(?is)^\s*(?P<identity>(?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+.+?)"
    r"\s+Armor Class\s+(?P<armor>.+?)"
    r"\s+Hit Points\s+(?P<hit_points>.+?)"
    r"\s+Speed\s+(?P<speed>.+?)\s*$"
)
_ENTRY_START_RE = re.compile(
    r"(?P<prefix>^|(?<=\.)[ \t]+|\n+)"
    r"(?P<name>[A-Z][A-Za-z0-9'’/-]*(?:\s+(?:[A-Z][A-Za-z0-9'’/-]*|"
    r"and|or|of|the|a|an))*(?:\s+\([^.\n]{1,60}\))?)\.\s+"
    r"(?=[A-Z*])"
)
_MECHANICAL_ENTRY_START_RE = re.compile(
    r"(?P<prefix>^|(?<=\.)[ \t]+|\n+)"
    r"(?P<name>[A-Z][^.\n]{0,99}?)\.\s+"
    r"(?=(?:\*?)(?:Melee|Ranged|Melee or Ranged)\s+"
    r"(?:Weapon|Spell)\s+Attack:)",
    re.IGNORECASE,
)
_SPELL_CLASSES = (
    "artificer",
    "bard",
    "cleric",
    "druid",
    "paladin",
    "ranger",
    "sorcerer",
    "warlock",
    "wizard",
)
_SPELL_SCHOOLS = (
    "abjuration",
    "conjuration",
    "divination",
    "enchantment",
    "evocation",
    "illusion",
    "necromancy",
    "transmutation",
)
_EMBEDDED_SPELL_START_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9/'’\-]+"
    r"(?:\s+(?:[A-Z][A-Za-z0-9/'’\-]+|of|the|and|or|from|with)){0,8})\s+"
    r"(?P<level>(?:[1-9](?:st|nd|rd|th)-level\s+"
    r"(?:" + "|".join(_SPELL_SCHOOLS) + r")|"
    r"(?:" + "|".join(_SPELL_SCHOOLS) + r")\s+cantrip))"
    r"(?:\s+\(ritual\))?\s+"
    r"Casting\s+Time:\s*",
    re.IGNORECASE,
)
_SPELL_FIELDS_RE = re.compile(
    r"(?is)(?P<casting_time>.+?)\s+Range:\s*(?P<range>.+?)\s+"
    r"Components:\s*(?P<components>.+?)\s+Duration:\s*"
    r"(?P<duration>Instantaneous|Concentration,\s*up to\s+"
    r"\d+\s+(?:rounds?|minutes?|hours?|days?)|"
    r"(?:Up to\s+)?\d+\s+(?:rounds?|minutes?|hours?|days?)|"
    r"Until dispelled|Special)"
    r"(?:\.\s*|\s+)?(?P<effect>.*)?$"
)
_SPELL_LIST_SECTION_RE = re.compile(
    r"(?i)\b(?P<class>" + "|".join(_SPELL_CLASSES) + r")\s+Spells\b"
)
_SPELL_LIST_ENTRY_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9/'’\-]+"
    r"(?:\s+(?:[A-Z][A-Za-z0-9/'’\-]+|of|the|and|or|from|with)){0,8})\s+"
    r"\((?P<school>" + "|".join(_SPELL_SCHOOLS) + r")"
    r"(?P<ritual>,\s*ritual)?\)",
    re.IGNORECASE,
)
_EMBEDDED_SPECIES_START_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z'’\-]+"
    r"(?:\s+[A-Z][A-Za-z'’\-]+){0,2})\s+Traits\s+"
    r"(?=.{0,500}?Ability Score Increase\s*[.:])",
)
_OCR_ABILITY_DIGITS = str.maketrans(
    {"l": "1", "I": "1", "O": "0", "S": "5"}
)

_ARTISAN_TOOL_OPTIONS_2014 = (
    "Alchemist's Supplies",
    "Brewer's Supplies",
    "Calligrapher's Supplies",
    "Carpenter's Tools",
    "Cartographer's Tools",
    "Cobbler's Tools",
    "Cook's Utensils",
    "Glassblower's Tools",
    "Jeweler's Tools",
    "Leatherworker's Tools",
    "Mason's Tools",
    "Painter's Supplies",
    "Potter's Tools",
    "Smith's Tools",
    "Tinker's Tools",
    "Weaver's Tools",
    "Woodcarver's Tools",
)
_GAMING_SET_OPTIONS_2014 = (
    "Dice",
    "Dragonchess",
    "Playing Cards",
    "Three-Dragon Ante",
)
_MUSICAL_INSTRUMENT_OPTIONS_2014 = (
    "Bagpipes",
    "Drum",
    "Dulcimer",
    "Flute",
    "Horn",
    "Lute",
    "Lyre",
    "Pan Flute",
    "Shawm",
    "Viol",
)
_BACKGROUND_FIXED_TOOLS = (
    "Disguise Kit",
    "Forgery Kit",
    "Herbalism Kit",
    "Navigator's Tools",
    "Poisoner's Kit",
    "Thieves' Tools",
    "Vehicles (Land)",
    "Vehicles (Water)",
)


def _bounded_ocr_edit_distance(left: str, right: str, maximum: int) -> int:
    """Return a bounded edit distance for short OCR identity comparisons."""

    if abs(len(left) - len(right)) > maximum:
        return maximum + 1
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, 1):
        current = [row]
        row_minimum = row
        for column, right_character in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_character != right_character),
                )
            )
            row_minimum = min(row_minimum, current[-1])
        if row_minimum > maximum:
            return maximum + 1
        previous = current
    return previous[-1]


def _normalize_item_header_ocr_text(value: str) -> str:
    """Repair only the bounded category label before an item's first comma."""

    match = re.match(
        r"^(?P<leading>\s*)(?P<label>[^,(\n0-9]{1,40})"
        r"(?P<parenthetical>\s*\([^\n)]{1,120}\))?"
        r"(?P<rest>\s*(?:,|—|–|-)[\s\S]*)$",
        value,
    )
    if match is None:
        return value
    label = match.group("label").rstrip()
    parenthetical = str(match.group("parenthetical") or "")
    compact = re.sub(r"[^A-Za-z]", "", label).casefold()
    if len(compact) < 2:
        return value
    matches = sorted(
        (
            _bounded_ocr_edit_distance(compact, expected, 2),
            expected,
            rendered,
        )
        for expected, rendered in _ITEM_CATEGORY_LABELS.items()
        if abs(len(compact) - len(expected)) <= 2
    )
    if not matches or matches[0][0] > 2:
        return value
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return value
    return (
        f"{match.group('leading')}{matches[0][2]}{parenthetical}"
        f"{match.group('rest')}"
    )


def _item_category_suffix(value: str) -> tuple[int, str] | None:
    """Return a bounded OCR category suffix start and its canonical label."""

    tokens = list(re.finditer(r"[^\s]+", value))
    matches: list[tuple[int, int, int, str]] = []
    for token_count in (1, 2, 3):
        if len(tokens) < token_count:
            continue
        start = tokens[-token_count].start()
        raw = value[start:]
        compact = re.sub(r"[^A-Za-z]", "", raw).casefold()
        for expected, rendered in _ITEM_CATEGORY_LABELS.items():
            if abs(len(compact) - len(expected)) > 2:
                continue
            distance = _bounded_ocr_edit_distance(compact, expected, 2)
            if distance <= 2:
                matches.append((distance, -len(raw), start, rendered))
    matches.sort()
    if not matches:
        return None
    if (
        len(matches) > 1
        and matches[0][0] == matches[1][0]
        and matches[0][3] != matches[1][3]
    ):
        return None
    return matches[0][2], matches[0][3]


def _canonical_source_heading(value: str) -> str:
    """Ignore layout-OCR spacing and punctuation inside a source heading."""

    return "".join(character for character in value.casefold() if character.isalnum())


def _normalize_candidate_display_name(value: str) -> str:
    """Repair bounded OCR spacing mistakes without rewriting source semantics."""

    normalized = " ".join(str(value).strip().strip(" .:;").split())
    normalized = re.sub(r"(?i)(?<=[A-Za-z])1s\b", "'s", normalized)
    normalized = re.sub(
        r"(?<=[A-Z])[il](?=[A-Z])",
        "I",
        normalized,
    )
    if normalized.isupper():
        normalized = re.sub(r"\b([A-Z]{3,})\s+S\s+(?=[A-Z])", r"\1'S ", normalized)
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)
    fused_standard_name = {
        "schoolofenchantment": "School of Enchantment",
        "thearchfey": "The Archfey",
    }.get(_canonical_source_heading(normalized))
    if fused_standard_name:
        return fused_standard_name
    return re.sub(r"([’'][sS])(?=[A-Z])", r"\1 ", normalized)


def _canonical_spell_header(value: str) -> str | None:
    """Recover only a uniquely identifiable spell level-and-school header."""

    observed = "".join(character for character in value.casefold() if character.isalnum())
    if not observed:
        return None
    observed = re.sub(r"^[il](?=st)", "1", observed)
    observed = re.sub(r"^[il]s(?=level)", "1st", observed)
    ritual = "ritual" in observed
    observed = observed.replace("ritual", "")
    candidates: list[tuple[int, str]] = []
    ordinals = (
        (1, "st"),
        (2, "nd"),
        (3, "rd"),
        (4, "th"),
        (5, "th"),
        (6, "th"),
        (7, "th"),
        (8, "th"),
        (9, "th"),
    )
    for school in _SPELL_SCHOOLS:
        labels = [(f"{school}cantrip", f"{school} cantrip")]
        labels.extend(
            (
                f"{level}{suffix}level{school}",
                f"{level}{suffix}-level {school}",
            )
            for level, suffix in ordinals
        )
        for target, rendered in labels:
            maximum = max(3, min(5, len(target) // 4))
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                candidates.append((score, rendered))
    candidates.sort()
    if not candidates or (
        len(candidates) > 1 and candidates[0][0] == candidates[1][0]
    ):
        return None
    header = candidates[0][1]
    return f"{header} (ritual)" if ritual else header


def _normalize_spell_field_labels(value: str) -> str:
    """Repair uniquely identifiable schema labels immediately before colons."""

    canonical_labels = {
        "castingtime": "Casting Time",
        "range": "Range",
        "component": "Components",
        "components": "Components",
        "duration": "Duration",
    }

    def replace(match: re.Match[str]) -> str:
        observed = "".join(
            character
            for character in match.group("label").casefold()
            if character.isalnum()
        )
        matches: list[tuple[int, str]] = []
        for canonical, rendered in canonical_labels.items():
            maximum = 2 if len(canonical) >= 8 else 1
            score = _bounded_ocr_edit_distance(observed, canonical, maximum)
            if score <= maximum:
                matches.append((score, rendered))
        matches.sort()
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"{matches[0][1]}:"

    return re.sub(
        r"(?P<label>[A-Za-z!;1]{3,14}(?:\s+[A-Za-z!;1]{2,10})?)\s*:",
        replace,
        value,
    )


def _normalize_spell_ocr_text(value: str) -> str:
    """Repair OCR damage in the bounded, schema-bearing part of spell text."""

    normalized = " ".join(str(value).replace("\x02", " ").split())
    normalized = _normalize_spell_field_labels(normalized)
    casting_matches = list(
        re.finditer(r"(?i)\bCasting\s+Time\s*:", normalized)
    )
    for casting in reversed(casting_matches):
        lookback_start = max(0, casting.start() - 80)
        lookback = normalized[lookback_start : casting.start()]
        starts = [
            match.start()
            for match in re.finditer(
                r"(?i)(?<![A-Za-z0-9])(?:[1-9lI]\s*(?:st|nd|rd|th|s)\b|"
                r"[A-Za-z][A-Za-z/;!]{3,18}(?=\s*can\s*trip\b))",
                lookback,
            )
        ]
        if lookback_start == 0:
            starts.append(0)
        repaired = next(
            (
                (start, header)
                for start in reversed(sorted(set(starts)))
                if (
                    header := _canonical_spell_header(lookback[start:])
                )
                is not None
            ),
            None,
        )
        if repaired is not None:
            start, header = repaired
            absolute_start = lookback_start + start
            normalized = (
                normalized[:absolute_start]
                + header
                + " "
                + normalized[casting.start() :]
            )
    for token in (*_SPELL_SCHOOLS, "instantaneous", "concentration", "self"):
        pattern = r"\s*".join(re.escape(character) for character in token)
        normalized = re.sub(rf"(?i)\b{pattern}\b", token, normalized)
    normalized = re.sub(
        r"(?i)\b([1-9lI])\s*(st|nd|rd|th|s)\s*[.~-]?\s*-?\s*/?\s*"
        r"(?:[lI/]?evel|leve[lI1])\b",
        lambda match: (
            f"{ {'l': '1', 'i': '1'}.get(match.group(1).casefold(), match.group(1)) }"
            f"{'st' if match.group(2).casefold() == 's' else match.group(2).lower()}-level"
        ),
        normalized,
    )

    def distance(left: str, right: str, maximum: int) -> int:
        if abs(len(left) - len(right)) > maximum:
            return maximum + 1
        previous = list(range(len(right) + 1))
        for row, left_character in enumerate(left, 1):
            current = [row]
            row_minimum = row
            for column, right_character in enumerate(right, 1):
                current.append(
                    min(
                        current[-1] + 1,
                        previous[column] + 1,
                        previous[column - 1] + (left_character != right_character),
                    )
                )
                row_minimum = min(row_minimum, current[-1])
            if row_minimum > maximum:
                return maximum + 1
            previous = current
        return previous[-1]

    def repair_words(text: str, words: tuple[str, ...]) -> str:
        def replace(match: re.Match[str]) -> str:
            observed = match.group(0)
            folded = observed.casefold()
            matches = []
            for canonical in words:
                maximum = 2 if len(canonical) >= 8 else 1
                score = distance(folded, canonical, maximum)
                if score <= maximum:
                    matches.append((score, canonical))
            matches.sort()
            if not matches or (
                len(matches) > 1 and matches[0][0] == matches[1][0]
            ):
                return observed
            return matches[0][1]

        return re.sub(r"[A-Za-z!\[\]1]{3,18}", replace, text)

    def repair_number_tokens(text: str) -> str:
        return re.sub(
            r"\b(?:[0-9IOS]{1,3}|[l])\b",
            lambda match: match.group(0).translate(
                str.maketrans({"I": "1", "l": "1", "O": "0", "S": "5"})
            ),
            text,
        )

    def repair_field_value(
        match: re.Match[str],
        words: tuple[str, ...],
    ) -> str:
        return (
            match.group("label")
            + repair_number_tokens(repair_words(match.group("value"), words))
        )

    # A normalized chunk can contain multiple spell cards when a PDF text
    # layer fails to promote a later display heading. Repair every bounded
    # schema value, not only the first Casting Time/Duration quartet.
    normalized = re.sub(r"(?i)\bup\s+lo\b", "up to", normalized)
    normalized = re.sub(
        r"(?is)(?P<label>\bCasting\s+Time\s*:\s*)"
        r"(?P<value>.{1,60}?)(?=\s+Range\s*:)",
        lambda match: repair_field_value(
            match,
            ("action", "bonus", "reaction", "minute", "minutes", "hour", "hours"),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?is)(?P<label>\bRange\s*:\s*)"
        r"(?P<value>.{1,80}?)(?=\s+Components\s*:)",
        lambda match: repair_field_value(
            match,
            ("self", "touch", "sight", "special", "feet", "foot", "mile", "miles"),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?is)(?P<label>\bDuration\s*:\s*)"
        r"(?P<value>[A-Za-z!\[\]1]{3,18})",
        lambda match: repair_field_value(
            match,
            (
                "instantaneous",
                "concentration",
                "special",
                "dispelled",
            ),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?is)(?P<label>\bDuration\s*:\s*"
        r"(?:concentration\s*[,.;]?\s*up\s+to\s+)?"
        r"(?:[0-9IOSl]{1,3}|one)\s+)"
        r"(?P<value>[A-Za-z!\[\]1]{3,14})",
        lambda match: repair_field_value(
            match,
            ("round", "rounds", "minute", "minutes", "hour", "hours", "day", "days"),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\bDuration\s*:\s*concentration\s*[,.;]?\s*up\s+to\s+)"
        r"(?P<number>[0-9IOSl]{1,3}|one)\b",
        lambda match: (
            match.group(1)
            + (
                "1"
                if match.group("number").casefold() == "one"
                else repair_number_tokens(match.group("number"))
            )
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\bDuration\s*:\s*)(?P<number>[0-9IOSl]{1,3}|one)"
        r"(?=\s+(?:rounds?|minutes?|hours?|days?)\b)",
        lambda match: (
            match.group(1)
            + (
                "1"
                if match.group("number").casefold() == "one"
                else repair_number_tokens(match.group("number"))
            )
        ),
        normalized,
    )

    casting = re.search(r"(?i)\bCasting\s+Time\s*:", normalized)
    if casting is not None:
        prefix = repair_words(
            normalized[: casting.start()],
            (*_SPELL_SCHOOLS, "cantrip", "level"),
        )
        normalized = prefix + normalized[casting.start() :]
    normalized = re.sub(r"(?i)\bSe[iI]\s+f\b", "self", normalized)
    normalized = re.sub(
        r"(?i)(\bDuration\s*:\s*Concentration)\.\s*",
        r"\1, ",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\bDuration\s*:\s*Concentration)\s+(?=up\s+to\b)",
        r"\1, ",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\b([1-9])\s*(st|nd|rd|th)\s*-?\s*le\s*vel\b",
        lambda match: f"{match.group(1)}{match.group(2).lower()}-level",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\b[lI1]\s*st\s*-\s*/?(?:le\s*vel|eve)/?",
        "1st-level",
        normalized,
    )
    normalized = re.sub(r"(?i)\bStli\s*-\s*level\b", "5th-level", normalized)
    normalized = re.sub(
        r"(?i)^[A-Z0-9]\s+(?=(?:[1-9](?:st|nd|rd|th)-level|"
        r"(?:" + "|".join(_SPELL_SCHOOLS) + r")\s+cantrip)\b)",
        "",
        normalized,
    )
    normalized = re.sub(r"(?i)\b(\d+)\s+minut\b", r"\1 minutes", normalized)
    for label in ("Casting Time", "Range", "Components", "Duration"):
        normalized = re.sub(
            rf"(?i)\b{re.escape(label)}\s*:\s*",
            f"{label}: ",
            normalized,
        )
    normalized = re.sub(r"(?i)\bcan\s+trip\b", "cantrip", normalized)
    normalized = re.sub(r"\s+,", ",", normalized)
    return normalized


def _looks_like_spell_schema_text(value: str) -> bool:
    folded = value[:4000].casefold()
    return (
        "casting" in folded
        or
        sum(
            label in folded
            for label in ("casting", "range", "components", "duration")
        )
        >= 2
        or bool(
            re.search(
                r"\b(?:cantrip|[1-9](?:st|nd|rd|th).{0,8}le[a-z]{2,5})\b",
                folded,
            )
        )
    )


def _repair_split_option_headings(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join adjacent option-name fragments without changing source evidence."""

    repaired = [deepcopy(dict(chunk)) for chunk in chunks]
    for index, chunk in enumerate(repaired[:-1]):
        content = str(chunk.get("content") or "").strip()
        path = [str(value).strip() for value in chunk.get("heading_path") or []]
        following = repaired[index + 1]
        following_path = [
            str(value).strip() for value in following.get("heading_path") or []
        ]
        if (
            content
            or len(path) < 2
            or len(following_path) != len(path)
            or path[:-1] != following_path[:-1]
            or not any("feat" in value.casefold() for value in path[:-1])
            or path[-1].casefold() in _GENERIC_TITLES
            or _PAGE_HEADER_RE.match(path[-1])
        ):
            continue
        following_content = str(following.get("content") or "").strip()
        if (
            len(following_content) < 80
            and "prerequisite" not in following_content.casefold()
        ):
            continue
        combined = _normalize_candidate_display_name(
            f"{path[-1]} {following_path[-1]}"
        )
        repaired[index]["heading_path"] = [*path[:-1], combined]
        repaired[index + 1]["heading_path"] = [*path[:-1], combined]
    last_feat_path: list[str] = []
    for chunk in repaired:
        path = [str(value).strip() for value in chunk.get("heading_path") or []]
        if len(path) < 2 or not any(
            "feat" in value.casefold() for value in path[:-1]
        ):
            continue
        title = path[-1].casefold()
        content = str(chunk.get("content") or "").strip()
        if title == "options" and content and last_feat_path:
            chunk["heading_path"] = list(last_feat_path)
            continue
        if (
            content
            and title not in _GENERIC_TITLES
            and not _PAGE_HEADER_RE.match(title)
            and not title.startswith("part ")
        ):
            last_feat_path = path
    return repaired


def _ordered_class_candidates(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover base classes when PDF headings flatten every feature tree."""

    anchors: list[tuple[int, str]] = []
    for index, chunk in enumerate(chunks):
        path = [str(value).strip() for value in chunk.get("heading_path") or []]
        if not path or path[-1].casefold() != "class features":
            continue
        if not any(
            "class" in value.casefold() and "chapter" in value.casefold()
            for value in path[:-1]
        ):
            continue
        match = re.search(
            r"(?i)\bas\s+an?\s+(?P<name>[A-Za-z]{3,20})\b",
            str(chunk.get("content") or "")[:300],
        )
        if match is not None:
            observed = match.group("name").casefold()
            matches = sorted(
                (
                    _bounded_ocr_edit_distance(observed, class_name, 2),
                    class_name.title(),
                )
                for class_name in _CLASS_NAMES
                if _bounded_ocr_edit_distance(observed, class_name, 2) <= 2
            )
            if matches and not (
                len(matches) > 1 and matches[0][0] == matches[1][0]
            ):
                anchors.append((index, matches[0][1]))

    candidates: list[dict[str, Any]] = []
    for anchor_index, (start, name) in enumerate(anchors):
        hard_end = (
            anchors[anchor_index + 1][0]
            if anchor_index + 1 < len(anchors)
            else len(chunks)
        )
        end = hard_end
        if anchor_index + 1 < len(anchors):
            next_name = anchors[anchor_index + 1][1]
            canonical_next_name = _canonical_source_heading(next_name)
            for index in range(start + 1, hard_end):
                path = [
                    str(value).strip()
                    for value in chunks[index].get("heading_path") or []
                    if str(value).strip()
                ]
                if len(path) < 2:
                    continue
                observed = _canonical_source_heading(path[-1])
                if (
                    _bounded_ocr_edit_distance(
                        observed,
                        canonical_next_name,
                        2,
                    )
                    <= 2
                ):
                    end = index
                    break
            if end == hard_end:
                next_class_pattern = re.compile(
                    rf"(?i)\b{re.escape(next_name)}s?\b"
                )
                for index in range(max(start + 1, hard_end - 12), hard_end):
                    path = " ".join(
                        str(value).strip()
                        for value in chunks[index].get("heading_path") or []
                    )
                    content = str(chunks[index].get("content") or "")[:1600]
                    if next_class_pattern.search(f"{path}\n{content}"):
                        end = index
                        break
        else:
            start_path = [
                str(value).strip()
                for value in chunks[start].get("heading_path") or []
                if str(value).strip()
            ]
            start_root = start_path[0].casefold() if start_path else ""
            if start_root:
                for index in range(start + 1, hard_end):
                    path = [
                        str(value).strip()
                        for value in chunks[index].get("heading_path") or []
                        if str(value).strip()
                    ]
                    if path and path[0].casefold() != start_root:
                        end = index
                        break
        evidence = [
            chunk
            for chunk in chunks[start:end]
            if str(chunk.get("id") or "").strip()
        ]
        content_parts = [
            str(chunk.get("content") or "").strip()
            for chunk in evidence
            if str(chunk.get("content") or "").strip()
        ]
        source_chunk_ids = [str(chunk["id"]) for chunk in evidence]
        if not source_chunk_ids or not content_parts:
            continue
        description = "\n\n".join(content_parts)
        page_start = None
        page_end = None
        for chunk in evidence:
            page_start = _minimum_page(page_start, chunk.get("page_start"))
            page_end = _maximum_page(page_end, chunk.get("page_end"))
        identity = "\x1f".join(("class", name.casefold(), *source_chunk_ids))
        candidates.append(
            {
                "id": "candidate:"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                "kind": "class",
                "name": name,
                "source_class_name": name,
                "source_chunk_ids": source_chunk_ids,
                "source_heading_path": ["Chapter 3 - Classes", name],
                "page_start": page_start,
                "page_end": page_end,
                "extraction_confidence": "high",
                "extraction_signals": ["ordered class boundary", "class features"],
                "review_status": "pending",
                "mechanical_scope": "review_required",
                "application_state": "catalog_only",
                "execution_state": "agent_resolution_required",
                "artifact": {
                    "kind": "class",
                    "application_state": "catalog_only",
                    "card": {"name": name, "description": description[:24000]},
                },
            }
        )
    return candidates


def _species_name_from_traits_path(heading_path: list[str]) -> tuple[str, str] | None:
    if not heading_path:
        return None
    title = re.sub(r"(?<=-)\s+", "", heading_path[-1]).strip()
    folded = title.casefold()
    if folded == "traits":
        ancestors = [
            value
            for value in reversed(heading_path[:-1])
            if value.casefold() not in {"races", "chapter 2 - races"}
        ]
        if not ancestors:
            return None
        name = ancestors[0]
    elif folded.endswith(" traits"):
        name = title[: -len(" Traits")]
    else:
        return None
    name = _normalize_candidate_display_name(name)
    if name.casefold() in {"racial", "personality", "species"}:
        return None
    base_species = name
    if name.casefold().startswith("variant "):
        base_species = name[len("Variant ") :]
    return name, base_species


def _ordered_species_candidates(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover species and subraces from source order when outline parents drift."""

    anchors: list[tuple[int, str, str]] = []
    for index, chunk in enumerate(chunks):
        path = [str(value).strip() for value in chunk.get("heading_path") or []]
        if not any("race" in value.casefold() for value in path[:-1]):
            continue
        identity = _species_name_from_traits_path(path)
        if identity is not None:
            anchors.append((index, *identity))

    base_records: list[dict[str, Any]] = []
    for anchor_index, (start, name, base_species) in enumerate(anchors):
        hard_end = (
            anchors[anchor_index + 1][0]
            if anchor_index + 1 < len(anchors)
            else len(chunks)
        )
        evidence: list[dict[str, Any]] = []
        for chunk in chunks[start:hard_end]:
            evidence.append(chunk)
            if re.search(
                r"(?i)\bLanguages?\s*[.:]", str(chunk.get("content") or "")
            ):
                break
        base_records.append(
            {
                "start": start,
                "hard_end": hard_end,
                "name": name,
                "base_species": base_species,
                "evidence": evidence,
            }
        )

    result: list[dict[str, Any]] = []
    base_evidence_by_name = {
        str(record["name"]).casefold(): list(record["evidence"])
        for record in base_records
        if str(record["name"]).casefold()
        == str(record["base_species"]).casefold()
    }
    for record in base_records:
        record_name = str(record["name"])
        base_species = str(record["base_species"])
        evidence = list(record["evidence"])
        if record_name.casefold() != base_species.casefold():
            evidence = [
                *base_evidence_by_name.get(base_species.casefold(), []),
                *evidence,
            ]
        base_candidate = _species_candidate_from_evidence(
            name=record_name,
            base_species=base_species,
            evidence=evidence,
            signal="ordered species traits",
        )
        if base_candidate is None:
            continue
        subraces: list[tuple[int, str]] = []
        base_name = str(record["base_species"])
        for index in range(int(record["start"]) + 1, int(record["hard_end"])):
            chunk = chunks[index]
            path = [str(value).strip() for value in chunk.get("heading_path") or []]
            if len(path) < 2 or base_name.casefold() not in {
                value.casefold() for value in path[:-1]
            }:
                continue
            body = str(chunk.get("content") or "").strip()
            title = _normalize_candidate_display_name(path[-1])
            if re.search(r"(?i)^As\s+an?\s+", body) or re.search(
                r"(?i)\b(?:earlier|other)\s+subrace\b", body[:500]
            ):
                subraces.append((index, title))
        if subraces:
            base_candidate["artifact"]["selection_applicability"] = "not_applicable"
        result.append(base_candidate)
        for subrace_index, (subrace_start, subrace_name) in enumerate(subraces):
            subrace_end = (
                subraces[subrace_index + 1][0]
                if subrace_index + 1 < len(subraces)
                else int(record["hard_end"])
            )
            evidence = list(record["evidence"])
            for chunk in chunks[subrace_start:subrace_end]:
                path = [str(value).strip() for value in chunk.get("heading_path") or []]
                if len(path) >= 2 and base_name.casefold() not in {
                    value.casefold() for value in path[:-1]
                }:
                    break
                evidence.append(chunk)
            candidate = _species_candidate_from_evidence(
                name=subrace_name,
                base_species=base_name,
                evidence=evidence,
                signal="ordered subrace boundary",
            )
            if candidate is not None:
                result.append(candidate)
    return result


def _species_candidate_from_evidence(
    *,
    name: str,
    base_species: str,
    evidence: list[dict[str, Any]],
    signal: str,
) -> dict[str, Any] | None:
    evidence = [
        chunk for chunk in evidence if str(chunk.get("id") or "").strip()
    ]
    source_chunk_ids = list(dict.fromkeys(str(chunk["id"]) for chunk in evidence))
    description = "\n\n".join(
        str(chunk.get("content") or "").strip()
        for chunk in evidence
        if str(chunk.get("content") or "").strip()
    )
    if not source_chunk_ids or not description:
        return None
    page_start = None
    page_end = None
    for chunk in evidence:
        page_start = _minimum_page(page_start, chunk.get("page_start"))
        page_end = _maximum_page(page_end, chunk.get("page_end"))
    identity = "\x1f".join(("species", name.casefold(), *source_chunk_ids))
    return {
        "id": "candidate:"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        "kind": "species",
        "name": name,
        "source_chunk_ids": source_chunk_ids,
        "source_heading_path": ["Chapter 2 - Races", base_species, name],
        "page_start": page_start,
        "page_end": page_end,
        "extraction_confidence": "high",
        "extraction_signals": [signal],
        "review_status": "pending",
        "mechanical_scope": "review_required",
        "application_state": "catalog_only",
        "execution_state": "agent_resolution_required",
        "artifact": {
            "kind": "species",
            "application_state": "catalog_only",
            "card": {
                "name": name,
                "base_species": base_species,
                "description": description[:12000],
            },
        },
    }


def extract_content_candidates(
    chunks: list[dict[str, Any]],
    *,
    source_title: str = "",
) -> list[dict[str, Any]]:
    """Extract review-required cards; never claim unsupported mechanics are executable."""
    chunks = _repair_split_option_headings(chunks)
    spell_class_index = _spell_class_index(chunks)
    sections: dict[tuple[str, ...], dict[str, Any]] = {}
    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()
        heading_path = [str(item).strip() for item in chunk.get("heading_path") or []]
        title = next((item for item in reversed(heading_path) if item), "")
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id or not title:
            continue
        key = tuple(item.casefold() for item in heading_path)
        section = sections.setdefault(
            key,
            {
                "title": title,
                "heading_path": heading_path,
                "source_chunk_ids": [],
                "content": [],
                "page_start": None,
                "page_end": None,
            },
        )
        section["source_chunk_ids"].append(chunk_id)
        if content and content not in section["content"]:
            section["content"].append(content)
        section["page_start"] = _minimum_page(
            section.get("page_start"), chunk.get("page_start")
        )
        section["page_end"] = _maximum_page(
            section.get("page_end"), chunk.get("page_end")
        )

    own_classifications: dict[
        tuple[str, ...], tuple[str, tuple[str, ...]] | None
    ] = {}
    primary_content_indexes: dict[tuple[str, ...], int | None] = {}
    for key, section in sections.items():
        title = str(section["title"])
        heading_path = list(section["heading_path"])
        contents = list(section["content"])
        joined_classification = _classify(
            title,
            heading_path,
            "\n\n".join(contents),
            source_title=source_title,
        )
        fragment_classifications = [
            _classify(
                title,
                heading_path,
                content,
                source_title=source_title,
            )
            for content in contents
        ]
        classified_fragments = [
            (index, classification)
            for index, classification in enumerate(fragment_classifications)
            if classification is not None
        ]
        # PDF illustration labels and running prose can share the canonical
        # heading of the real entity body. Classify every source fragment as
        # well as the joined section so a harmless leading fragment cannot
        # hide a later, structurally complete entity header.
        fragment_kinds = {
            classification[0] for _index, classification in classified_fragments
        }
        if joined_classification is not None:
            classification = joined_classification
        elif len(fragment_kinds) == 1:
            classification = classified_fragments[0][1]
        else:
            classification = None
        own_classifications[key] = classification
        primary_content_indexes[key] = next(
            (
                index
                for index, fragment_classification in classified_fragments
                if classification is not None
                and fragment_classification[0] == classification[0]
            ),
            None,
        )
    descendants_by_key: dict[tuple[str, ...], list[dict[str, Any]]] = {
        key: [] for key in sections
    }
    descendant_kinds_by_key: dict[tuple[str, ...], set[str]] = {
        key: set() for key in sections
    }
    for candidate_key, value in sections.items():
        classification = own_classifications[candidate_key]
        for depth in range(1, len(candidate_key)):
            prefix = candidate_key[:depth]
            if prefix not in descendants_by_key:
                continue
            descendants_by_key[prefix].append(value)
            if classification is not None:
                descendant_kinds_by_key[prefix].add(classification[0])
    candidates: list[dict[str, Any]] = []
    source_class_name = _class_name_from_source(source_title)
    for key, section in sections.items():
        descendants = descendants_by_key[key]
        own_content_parts = [*section["content"]]
        primary_content_index = primary_content_indexes[key]
        if primary_content_index not in {None, 0}:
            primary_content = own_content_parts.pop(primary_content_index)
            own_content_parts.insert(0, primary_content)
        content_parts = own_content_parts
        source_chunk_ids = list(section["source_chunk_ids"])
        page_start = section["page_start"]
        page_end = section["page_end"]
        for descendant in descendants:
            content_parts.extend(descendant["content"])
            source_chunk_ids.extend(descendant["source_chunk_ids"])
            page_start = _minimum_page(page_start, descendant.get("page_start"))
            page_end = _maximum_page(page_end, descendant.get("page_end"))
        content = "\n\n".join(content_parts)
        classification = own_classifications[key]
        if classification is None:
            aggregate_classification = _classify(
                str(section["title"]),
                list(section["heading_path"]),
                content,
                source_title=source_title,
            )
            # Entity headings should normally classify from their own body.
            # Restrict descendant aggregation to container-shaped entities;
            # otherwise prose introductions inherit nested level features and
            # become bogus selectable options.
            if aggregate_classification is not None and aggregate_classification[0] in {
                "background",
                "class",
                "species",
                "statblock",
            }:
                classification = aggregate_classification
        if classification is None:
            continue
        kind, signals = classification
        candidate_name: str = (
            source_class_name
            if kind == "class"
            and source_class_name
            and str(section["title"]).casefold() == "class features"
            else section["title"]
        )
        heading_path = list(section["heading_path"])
        if (
            kind == "spell"
            and _SPELL_LEVEL_RE.match(str(candidate_name).strip())
            and len(heading_path) >= 2
        ):
            candidate_name = heading_path[-2]
        if kind == "subclass" and str(candidate_name).casefold().endswith(" features"):
            candidate_name = str(candidate_name)[: -len(" Features")]
        if kind == "subclass" and str(candidate_name).casefold().endswith(" spells"):
            candidate_name = str(candidate_name)[: -len(" Spells")]
        candidate_name = _normalize_candidate_display_name(str(candidate_name))
        if kind == "spell":
            indexed_spell = _spell_class_record(spell_class_index, candidate_name)
            if indexed_spell.get("name"):
                indexed_name = str(indexed_spell["name"])
                if "'" not in candidate_name or "'" in indexed_name:
                    candidate_name = indexed_name
        if kind == "class" and source_class_name:
            source_chunk_ids = [
                chunk_id
                for value in sections.values()
                for chunk_id in value["source_chunk_ids"]
            ]
            all_class_bodies = [
                body
                for value in sections.values()
                for body in value["content"]
                if body
            ]
            relevant_class_bodies = [
                body
                for body in all_class_bodies
                if re.search(
                    r"(?i)\b(?:Hit\s+Dice?|Armor|Weapons|Tools|Saving\s+Throws|"
                    r"Skills)\s*:",
                    body,
                )
            ]
            content = "\n\n".join(
                dict.fromkeys([*content_parts, *relevant_class_bodies, *all_class_bodies])
            )
        if (
            own_classifications[key] is None
            and kind in descendant_kinds_by_key[key]
        ):
            # A heading-only catalog such as "Optional Spells" must not become a
            # duplicate entity merely because its descendant spell text was
            # aggregated. Entity parents such as a class still aggregate their
            # differently classified feature descendants.
            continue
        artifact_card: dict[str, Any] = {
            "name": candidate_name,
            "description": content[: (24000 if kind == "class" else 12000)],
        }
        if kind == "spell":
            structured_spell = _spell_card_from_section(
                candidate_name,
                heading_path,
                content,
                chunks,
                source_chunk_ids=source_chunk_ids,
                class_index=spell_class_index,
            )
            if structured_spell is not None:
                artifact_card.update(structured_spell)
        identity = "\x1f".join((kind, *key))
        candidates.append(
            {
                "id": "candidate:"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                "kind": kind,
                "name": candidate_name,
                "source_class_name": source_class_name,
                "source_chunk_ids": list(dict.fromkeys(source_chunk_ids)),
                "source_heading_path": section["heading_path"],
                "page_start": page_start,
                "page_end": page_end,
                "extraction_confidence": "high" if len(signals) >= 3 else "medium",
                "extraction_signals": list(signals),
                "review_status": "pending",
                "mechanical_scope": (
                    "mechanical" if kind in {"spell", "statblock"} else "review_required"
                ),
                "application_state": "catalog_only",
                "execution_state": "not_compiled",
                "artifact": {
                    "kind": kind,
                    "application_state": "catalog_only",
                    "card": artifact_card,
                },
            }
        )
    candidates.extend(
        _embedded_spell_candidates(
            chunks,
            source_title=source_title,
            class_index=spell_class_index,
        )
    )
    candidates.extend(_embedded_item_candidates(chunks))
    candidates.extend(_ordered_class_candidates(chunks))
    candidates.extend(_ordered_species_candidates(chunks))
    candidates.extend(_embedded_species_candidates(chunks))
    candidates.extend(_background_variant_candidates(chunks, candidates=candidates))
    candidates.extend(_rulebook_statblock_candidates(chunks))
    merged = _merge_extracted_candidates(candidates)
    claimed_chunks = {
        str(chunk_id)
        for candidate in merged
        for chunk_id in candidate.get("source_chunk_ids") or []
    }
    merged.extend(
        _mechanical_source_fragment_candidates(
            chunks,
            claimed_chunk_ids=claimed_chunks,
        )
    )
    return merged


def extract_content_inventory(
    chunks: list[dict[str, Any]],
    *,
    source_title: str = "",
) -> dict[str, Any]:
    """Return exhaustive chunk disposition plus the structured entity catalog.

    Completeness is not inferred from entity counts. Every indexed chunk is
    represented in the ledger; mechanically suggestive chunks that were not
    claimed by any candidate are surfaced as a blocking review queue.
    """

    candidates = extract_content_candidates(chunks, source_title=source_title)
    claims: dict[str, list[str]] = {}
    fallback_ids = {
        str(candidate["id"])
        for candidate in candidates
        if candidate.get("coverage_fallback") is True
    }
    for candidate in candidates:
        for chunk_id in candidate.get("source_chunk_ids") or []:
            claims.setdefault(str(chunk_id), []).append(str(candidate["id"]))
    ledger = []
    unresolved = []
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id:
            continue
        content = str(chunk.get("content") or "")
        entity_ids = sorted(set(claims.get(chunk_id, [])))
        signals = _unclaimed_mechanical_signals(content)
        disposition = "structured_entity" if entity_ids else "descriptive_context"
        if signals and (not entity_ids or all(item in fallback_ids for item in entity_ids)):
            disposition = "mechanical_review_required"
            unresolved.append(
                {
                    "chunk_id": chunk_id,
                    "heading_path": list(chunk.get("heading_path") or []),
                    "page_start": chunk.get("page_start"),
                    "page_end": chunk.get("page_end"),
                    "signals": signals,
                    "candidate_ids": entity_ids,
                }
            )
        ledger.append(
            {
                "chunk_id": chunk_id,
                "disposition": disposition,
                "entity_ids": entity_ids,
                "signals": signals,
            }
        )
    counts: dict[str, int] = {}
    for candidate in candidates:
        kind = str(candidate["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "schema_version": 1,
        "source_title": source_title,
        "chunk_count": len(ledger),
        "candidate_count": len(candidates),
        "candidate_counts": dict(sorted(counts.items())),
        "claimed_chunk_count": sum(
            1 for item in ledger if item["disposition"] == "structured_entity"
        ),
        "descriptive_chunk_count": sum(
            1 for item in ledger if item["disposition"] == "descriptive_context"
        ),
        "unresolved_mechanical_count": len(unresolved),
        "unresolved_mechanical_chunks": unresolved,
        "ledger": ledger,
        "candidates": candidates,
    }


def _mechanical_source_fragment_candidates(
    chunks: list[dict[str, Any]],
    *,
    claimed_chunk_ids: set[str],
) -> list[dict[str, Any]]:
    """Retain every unclaimed mechanical fragment for build-time Agent resolution."""

    candidates = []
    for index, chunk in enumerate(chunks):
        chunk_id = str(chunk.get("id") or "").strip()
        content = str(chunk.get("content") or "").strip()
        signals = _unclaimed_mechanical_signals(content)
        meaningful_text = "".join(character for character in content if character.isalnum())
        if (
            not chunk_id
            or chunk_id in claimed_chunk_ids
            or len(meaningful_text) < 12
            or not signals
        ):
            continue
        heading_path = [
            str(item).strip()
            for item in chunk.get("heading_path") or []
            if str(item).strip()
        ]
        heading = heading_path[-1] if heading_path else "Unheaded rule fragment"
        page = chunk.get("page_start")
        name = f"Source fragment: {heading}"
        if page is not None:
            name += f" (p. {page})"
        identity = "\x1f".join(("source-fragment", chunk_id, str(index)))
        candidate_id = (
            "candidate:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        )
        candidates.append(
            {
                "id": candidate_id,
                "kind": "feature",
                "name": name,
                "source_chunk_ids": [chunk_id],
                "source_heading_path": heading_path,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "extraction_confidence": "review_required",
                "extraction_signals": ["mechanical source fragment", *signals],
                "review_status": "pending",
                "mechanical_scope": "review_required",
                "application_state": "catalog_only",
                "execution_state": "agent_resolution_required",
                "coverage_fallback": True,
                "ruling_requirement": {
                    "reason": (
                        "This exact source fragment contains mechanical signals but "
                        "does not match a safely structured entity schema."
                    ),
                    "default_resolver": "agent",
                    "ruling_kind": "source_or_scene_fact",
                },
                "artifact": {
                    "kind": "feature",
                    "application_state": "catalog_only",
                    "mechanical_scope": "review_required",
                    "card": {
                        "name": name,
                        "description": content[:4000],
                        "source_fragment": True,
                    },
                },
            }
        )
    return candidates


def _spell_text_and_field_continuations(
    content: str,
    *,
    source_chunk_ids: list[str],
    chunks: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Join adjacent spell fields and retain every supporting source chunk."""

    by_id = {
        str(chunk.get("id") or ""): index
        for index, chunk in enumerate(chunks)
        if str(chunk.get("id") or "")
    }
    positions = [by_id[item] for item in source_chunk_ids if item in by_id]
    if not positions:
        return content, list(dict.fromkeys(source_chunk_ids))
    position = max(positions)
    result = content
    used_chunk_ids = list(dict.fromkeys(source_chunk_ids))
    current_path = [
        _canonical_source_heading(item)
        for item in chunks[position].get("heading_path") or []
    ]
    labels = ("Casting Time", "Range", "Components", "Duration")
    for next_position in range(position + 1, min(len(chunks), position + 4)):
        normalized = _normalize_spell_ocr_text(result)
        missing = next(
            (
                label
                for label in labels
                if not re.search(rf"(?i)\b{re.escape(label)}\s*:", normalized)
            ),
            None,
        )
        if missing is None:
            break
        next_chunk = chunks[next_position]
        next_path = [
            _canonical_source_heading(item)
            for item in next_chunk.get("heading_path") or []
        ]
        if current_path[:-1] != next_path[:-1]:
            break
        continuation = _normalize_spell_ocr_text(next_chunk.get("content") or "")
        continuation_match = re.match(
            rf"(?is)^\s*(?:\d{{1,3}}\s+)?"
            rf"(?P<body>{re.escape(missing)}\s*:.*)$",
            continuation,
        )
        if continuation_match is None:
            break
        result = f"{result} {continuation_match.group('body')}".strip()
        next_chunk_id = str(next_chunk.get("id") or "")
        if next_chunk_id and next_chunk_id not in used_chunk_ids:
            used_chunk_ids.append(next_chunk_id)
        position = next_position
        current_path = next_path
    return result, used_chunk_ids


def _spell_text_with_field_continuations(
    content: str,
    *,
    source_chunk_ids: list[str],
    chunks: list[dict[str, Any]],
) -> str:
    """Join only an adjacent continuation that starts with the missing field."""

    return _spell_text_and_field_continuations(
        content,
        source_chunk_ids=source_chunk_ids,
        chunks=chunks,
    )[0]


def _spell_card_from_section(
    name: str,
    heading_path: list[str],
    content: str,
    chunks: list[dict[str, Any]],
    *,
    source_chunk_ids: list[str] | None = None,
    class_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    indexed = _spell_class_record(class_index or _spell_class_index(chunks), name)
    content = _spell_text_with_field_continuations(
        content,
        source_chunk_ids=list(source_chunk_ids or []),
        chunks=chunks,
    )
    normalized_content = _normalize_spell_ocr_text(content)
    level_heading = next(
        (
            _normalize_spell_ocr_text(str(heading).strip())
            for heading in reversed(heading_path)
            if _SPELL_LEVEL_RE.match(
                _normalize_spell_ocr_text(str(heading).strip())
            )
        ),
        "",
    )
    content_header = _SPELL_LEVEL_RE.match(normalized_content)
    if not level_heading and content_header is not None:
        level_heading = content_header.group(0)
    if not level_heading:
        return None
    level_folded = level_heading.casefold()
    school = next((item for item in _SPELL_SCHOOLS if item in level_folded), "")
    level_match = re.match(r"(?i)(\d)(?:st|nd|rd|th)", level_heading)
    level = int(level_match.group(1)) if level_match else 0
    field_content = (
        normalized_content[content_header.end() :].strip()
        if content_header is not None
        else normalized_content
    )
    field_content = re.sub(r"(?i)^\s*\(ritual\)\s*", "", field_content)
    fields = _SPELL_FIELDS_RE.match(
        re.sub(
            r"(?i)^\s*Casting\s+Time\s*:\s*",
            "",
            field_content,
        ).strip()
    )
    if not school or fields is None:
        return None
    mentioned = (
        {"classes": set(), "source_chunk_ids": set()}
        if indexed.get("classes")
        else _spell_class_mentions(chunks, name)
    )
    classes = sorted(set(indexed.get("classes", [])) | set(mentioned["classes"]))
    return {
        "level": level,
        "classes": classes,
        "definition": {
            "school": school,
            "casting_time": fields.group("casting_time").strip(),
            "range": _spell_range(fields.group("range")),
            "duration": _spell_duration(fields.group("duration")),
            "components": _spell_components(fields.group("components")),
            "effect": (fields.group("effect") or "").strip()[:4000],
        },
    }


def _embedded_spell_candidates(
    chunks: list[dict[str, Any]],
    *,
    source_title: str,
    class_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    class_index = class_index or _spell_class_index(chunks)
    candidates = []
    chunks_by_id = {
        str(chunk.get("id") or ""): chunk
        for chunk in chunks
        if str(chunk.get("id") or "")
    }
    for chunk in chunks:
        content = _normalize_spell_ocr_text(chunk.get("content") or "")
        chunk_id = str(chunk.get("id") or "").strip()
        if not content or not chunk_id:
            continue
        # Document layout normally promotes the first spell name to the section
        # heading, so the chunk itself begins with ``2nd-level conjuration``.
        # Reattach only that exact heading for scanning; otherwise the first
        # spell in every section is systematically downgraded to a name-only
        # catalog card while later spells in the same chunk are structured.
        scan_content = content
        heading_path = [
            " ".join(str(item).split())
            for item in chunk.get("heading_path") or []
            if str(item).strip()
        ]
        heading_name = heading_path[-1].strip(" .:;") if heading_path else ""
        if (
            heading_name
            and heading_name.casefold() not in _GENERIC_TITLES
            and not _PAGE_HEADER_RE.match(heading_name)
            and re.match(
                r"(?i)^(?:[1-9](?:st|nd|rd|th)-level\s+"
                r"(?:" + "|".join(_SPELL_SCHOOLS) + r")|"
                r"(?:" + "|".join(_SPELL_SCHOOLS) + r")\s+cantrip)\b",
                content,
            )
        ):
            scan_content = f"{heading_name} {content}"
        starts = list(_EMBEDDED_SPELL_START_RE.finditer(scan_content))
        for index, match in enumerate(starts):
            end = (
                starts[index + 1].start()
                if index + 1 < len(starts)
                else len(scan_content)
            )
            name = " ".join(match.group("name").split()).strip(" .")
            name = re.sub(r"(?i)^spells?\s+", "", name).strip()
            name = _normalize_candidate_display_name(name)
            if (
                heading_name
                and _canonical_source_heading(heading_name).endswith(
                    _canonical_source_heading(name)
                )
            ):
                name = _normalize_candidate_display_name(heading_name)
            level_text = match.group("level").casefold()
            school = next(
                school for school in _SPELL_SCHOOLS if school in level_text
            )
            level = 0 if "cantrip" in level_text else int(level_text[0])
            spell_text, spell_chunk_ids = _spell_text_and_field_continuations(
                scan_content[match.start() : end].strip(),
                source_chunk_ids=[chunk_id],
                chunks=chunks,
            )
            spell_starts = list(_EMBEDDED_SPELL_START_RE.finditer(spell_text))
            if not spell_starts:
                continue
            spell_end = (
                spell_starts[1].start()
                if len(spell_starts) >= 2
                else len(spell_text)
            )
            fields = _SPELL_FIELDS_RE.match(
                spell_text[spell_starts[0].end() : spell_end].strip()
            )
            if fields is None:
                continue
            indexed = _spell_class_record(class_index, name)
            if indexed.get("name"):
                name = str(indexed["name"])
            mentioned = (
                {"classes": set(), "source_chunk_ids": set()}
                if indexed.get("classes")
                else _spell_class_mentions(chunks, name)
            )
            classes = sorted(
                set(indexed.get("classes", [])) | set(mentioned.get("classes", []))
            )
            list_chunks = sorted(
                set(indexed.get("source_chunk_ids", []))
                | set(mentioned.get("source_chunk_ids", []))
            )
            duration = _spell_duration(fields.group("duration"))
            components = _spell_components(fields.group("components"))
            spell_range = _spell_range(fields.group("range"))
            identity = "\x1f".join(("spell", name.casefold(), chunk_id))
            source_chunk_ids = list(dict.fromkeys([*spell_chunk_ids, *list_chunks]))
            source_chunks = [
                chunks_by_id[item]
                for item in spell_chunk_ids
                if item in chunks_by_id
            ]
            candidates.append(
                {
                    "id": "candidate:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                    "kind": "spell",
                    "name": name,
                    "source_chunk_ids": source_chunk_ids,
                    "source_heading_path": list(chunk.get("heading_path") or []),
                    "page_start": _minimum_page_values(
                        item.get("page_start") for item in source_chunks
                    ),
                    "page_end": _maximum_page_values(
                        item.get("page_end") for item in source_chunks
                    ),
                    "extraction_confidence": "high" if classes else "medium",
                    "extraction_signals": [
                        "embedded spell header",
                        "casting time",
                        "range",
                        "components",
                        "duration",
                        *(["spell list association"] if classes else []),
                    ],
                    "review_status": "pending",
                    "mechanical_scope": "review_required",
                    "application_state": "catalog_only",
                    "execution_state": "agent_resolution_required",
                    "artifact": {
                        "kind": "spell",
                        "application_state": "catalog_only",
                        "mechanical_scope": "review_required",
                        "card": {
                            "name": name,
                            "level": level,
                            "classes": classes,
                            "definition": {
                                "school": school,
                                "casting_time": fields.group("casting_time").strip(),
                                "range": spell_range,
                                "duration": duration,
                                "components": components,
                                "effect": (fields.group("effect") or "").strip()[:4000],
                            },
                            "source_title": source_title,
                        },
                    },
                }
            )
    return candidates


def _embedded_item_candidates(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover item entries whose display heading was fused into prior prose."""

    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or "").strip()
        heading_path = [
            str(value).strip()
            for value in chunk.get("heading_path") or []
            if str(value).strip()
        ]
        if not chunk_id or not heading_path or not any(
            "magic item" in value.casefold()
            or value.casefold() in {"artifact", "artifacts"}
            for value in heading_path
        ):
            continue
        content = str(chunk.get("content") or "")
        recovered: list[tuple[int, int, str, str]] = []
        for comma in re.finditer(",", content):
            window_start = max(0, comma.start() - 200)
            before_comma = content[window_start : comma.start()].rstrip()
            category_end = len(before_comma)
            if before_comma.endswith(")"):
                opening = before_comma.rfind("(", max(0, category_end - 130))
                if opening >= 0:
                    category_end = opening
            category_prefix = before_comma[:category_end].rstrip()
            category = _item_category_suffix(category_prefix)
            if category is None:
                continue
            category_start, rendered_category = category
            name_prefix = category_prefix[:category_start]
            name_start = max(
                name_prefix.rfind("."),
                name_prefix.rfind("!"),
                name_prefix.rfind("?"),
            ) + 1
            raw_name = name_prefix[name_start:].strip(
                " \t\r\n\"'~:;,-·•"
            )
            ascii_letters = [
                character
                for character in raw_name
                if character.isascii() and character.isalpha()
            ]
            if (
                not 3 <= len(raw_name) <= 100
                or not ascii_letters
                or len(raw_name.split()) > 12
                or sum(character.isupper() for character in ascii_letters)
                / len(ascii_letters)
                < 0.7
            ):
                continue
            name = _normalize_candidate_display_name(raw_name)
            if not name:
                continue
            recovered.append(
                (
                    window_start + name_start,
                    window_start + category_start,
                    name,
                    rendered_category,
                )
            )
        recovered = list(dict.fromkeys(recovered))
        for index, (entry_start, category_start, name, rendered_category) in enumerate(
            recovered
        ):
            end = recovered[index + 1][0] if index + 1 < len(recovered) else len(content)
            description = content[category_start:end].strip()
            if not description:
                continue
            context_end = max(
                (
                    position
                    for position, value in enumerate(heading_path)
                    if "magic item" in value.casefold()
                    or value.casefold() in {"artifact", "artifacts"}
                ),
                default=len(heading_path) - 2,
            )
            source_heading_path = [*heading_path[: context_end + 1], name]
            identity = "\x1f".join(
                ("item", chunk_id, str(entry_start), _canonical_source_heading(name))
            )
            candidates.append(
                {
                    "id": "candidate:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                    "kind": "item",
                    "name": name,
                    "source_chunk_ids": [chunk_id],
                    "source_heading_path": source_heading_path,
                    "page_start": chunk.get("page_start"),
                    "page_end": chunk.get("page_end"),
                    "extraction_confidence": "medium",
                    "extraction_signals": [
                        "embedded item category",
                        rendered_category.casefold(),
                    ],
                    "review_status": "pending",
                    "mechanical_scope": "review_required",
                    "application_state": "catalog_only",
                    "execution_state": "not_compiled",
                    "artifact": {
                        "kind": "item",
                        "application_state": "catalog_only",
                        "card": {
                            "name": name,
                            "description": description[:12000],
                        },
                    },
                }
            )
    return candidates


def _clean_species_name(value: str) -> str:
    words = " ".join(value.split()).strip(" .").split()
    while words and words[0].casefold() in {"chapter", "race", "races", "traits"}:
        words.pop(0)
    compound_modifiers = {
        "air",
        "dark",
        "deep",
        "earth",
        "eladrin",
        "fallen",
        "feral",
        "fire",
        "forest",
        "ghostwise",
        "gray",
        "half",
        "high",
        "hill",
        "lightfoot",
        "mountain",
        "protector",
        "rock",
        "scourge",
        "sea",
        "shadar-kai",
        "simic",
        "stout",
        "variant",
        "water",
        "wood",
    }
    if len(words) >= 2 and words[-2].casefold() in compound_modifiers:
        return " ".join(words[-2:])
    return words[-1] if words else "Unknown Species"


def _embedded_species_candidates(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sections: dict[tuple[str, ...], dict[str, Any]] = {}
    for chunk in chunks:
        content = " ".join(str(chunk.get("content") or "").split())
        chunk_id = str(chunk.get("id") or "").strip()
        if not content or not chunk_id:
            continue
        heading_path = [
            str(value).strip()
            for value in chunk.get("heading_path") or []
            if str(value).strip()
        ]
        key = tuple(value.casefold() for value in heading_path)
        section = sections.setdefault(
            key,
            {
                "heading_path": heading_path,
                "content": "",
                "spans": [],
            },
        )
        separator = " " if section["content"] else ""
        start = len(section["content"]) + len(separator)
        section["content"] += separator + content
        section["spans"].append(
            {
                "start": start,
                "end": start + len(content),
                "chunk_id": chunk_id,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
            }
        )

    candidates = []
    for section in sections.values():
        content = str(section["content"])
        starts = list(_EMBEDDED_SPECIES_START_RE.finditer(content))
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(content)
            name = _clean_species_name(match.group("name"))
            body = content[match.end() : end].strip()
            signals = [
                label
                for label in (
                    "ability score increase",
                    "age",
                    "alignment",
                    "size",
                    "speed",
                    "languages",
                )
                if re.search(rf"(?i)\b{re.escape(label)}\s*[.:]", body)
            ]
            if len(signals) < 4:
                continue
            evidence = [
                item
                for item in section["spans"]
                if int(item["end"]) > match.start() and int(item["start"]) < end
            ]
            chunk_ids = [str(item["chunk_id"]) for item in evidence]
            if not chunk_ids:
                continue
            page_start = None
            page_end = None
            for item in evidence:
                page_start = _minimum_page(page_start, item.get("page_start"))
                page_end = _maximum_page(page_end, item.get("page_end"))
            identity = "\x1f".join(("species", name.casefold(), *chunk_ids))
            candidates.append(
                {
                    "id": "candidate:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                    "kind": "species",
                    "name": name,
                    "source_chunk_ids": chunk_ids,
                    "source_heading_path": list(section["heading_path"]),
                    "page_start": page_start,
                    "page_end": page_end,
                    "extraction_confidence": "high",
                    "extraction_signals": ["embedded species traits", *signals],
                    "review_status": "pending",
                    "mechanical_scope": "review_required",
                    "application_state": "catalog_only",
                    "execution_state": "agent_resolution_required",
                    "artifact": {
                        "kind": "species",
                        "application_state": "catalog_only",
                        "mechanical_scope": "review_required",
                        "card": {"name": name, "description": body[:12000]},
                    },
                }
            )
    return candidates


_BACKGROUND_VARIANT_HEADING_RE = re.compile(
    r"(?i)^VARIANT\s+(?P<base>[A-Z !1'-]{3,40})\s*:\s*"
    r"(?P<variant>[A-Z !1'-]{3,40})$"
)
_BACKGROUND_VARIANT_INLINE_RE = re.compile(
    r"\bVARIANT\s+(?P<base>[A-Z !1'-]{3,40})\s*:\s*"
    r"(?P<variant>[A-Z][A-Za-z!1'-]*(?:\s+[A-Z][A-Za-z!1'-]*){0,3})"
    r"(?=\s+(?:Although|Instead|A|An|If|You|Your|Vou|This|The)\b)",
)


def _background_variant_candidates(
    chunks: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize printed background variants with their base proficiencies."""

    backgrounds = {
        _canonical_source_heading(str(item.get("name") or "")): item
        for item in candidates
        if item.get("kind") == "background"
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id:
            continue
        path = [str(value).strip() for value in chunk.get("heading_path") or []]
        body = str(chunk.get("content") or "")
        sources = [
            (path[-1] if path else "", _BACKGROUND_VARIANT_HEADING_RE),
            (body, _BACKGROUND_VARIANT_INLINE_RE),
        ]
        for source, pattern in sources:
            for match in pattern.finditer(source):
                base = _normalize_candidate_display_name(match.group("base"))
                variant = _normalize_candidate_display_name(match.group("variant"))
                if base.isupper():
                    base = base.title()
                if variant.isupper():
                    variant = variant.title()
                if base.casefold() == "feature":
                    continue
                base_candidate = backgrounds.get(_canonical_source_heading(base))
                if base_candidate is None:
                    continue
                identity_key = (base.casefold(), variant.casefold(), chunk_id)
                if identity_key in seen:
                    continue
                seen.add(identity_key)
                base_card = dict(
                    dict(base_candidate.get("artifact") or {}).get("card") or {}
                )
                base_description = str(base_card.get("description") or "").strip()
                variant_description = (
                    body[match.end() :].strip()
                    if source == body
                    else body.strip()
                )
                description = "\n\n".join(
                    value
                    for value in (base_description, variant_description)
                    if value
                )
                name = f"{base} ({variant})"
                source_chunk_ids = list(
                    dict.fromkeys(
                        [
                            *[
                                str(value)
                                for value in base_candidate.get("source_chunk_ids") or []
                            ],
                            chunk_id,
                        ]
                    )
                )
                identity = "\x1f".join(
                    ("background", name.casefold(), *source_chunk_ids)
                )
                result.append(
                    {
                        "id": "candidate:"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                        "kind": "background",
                        "name": name,
                        "source_chunk_ids": source_chunk_ids,
                        "source_heading_path": [*path[:-1], name],
                        "page_start": _minimum_page(
                            base_candidate.get("page_start"), chunk.get("page_start")
                        ),
                        "page_end": _maximum_page(
                            base_candidate.get("page_end"), chunk.get("page_end")
                        ),
                        "extraction_confidence": "high",
                        "extraction_signals": [
                            "printed background variant",
                            "base background grants",
                        ],
                        "review_status": "pending",
                        "mechanical_scope": "review_required",
                        "application_state": "catalog_only",
                        "execution_state": "agent_resolution_required",
                        "artifact": {
                            "kind": "background",
                            "application_state": "catalog_only",
                            "card": {"name": name, "description": description[:12000]},
                        },
                    }
                )
    return result


def _spell_class_index(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index spell-list membership across ordinary and fused multi-column layouts."""

    result: dict[str, dict[str, Any]] = {}
    known_spell_name_set: set[str] = set()
    for chunk in chunks:
        raw_content = str(chunk.get("content") or "")
        if not _looks_like_spell_schema_text(raw_content):
            continue
        normalized_content = _normalize_spell_ocr_text(raw_content)
        if chunk.get("heading_path") and any(
            _canonical_source_heading(item) == "spelldescriptions"
            for item in chunk.get("heading_path") or []
        ):
            folded = normalized_content.casefold()[:1200]
            if sum(
                label in folded
                for label in (
                    "casting time:",
                    "range:",
                    "components:",
                    "duration:",
                )
            ) >= 3:
                known_spell_name_set.add(
                    _normalize_candidate_display_name(
                        str(chunk.get("heading_path")[-1])
                    )
                )
        known_spell_name_set.update(
            _normalize_candidate_display_name(match.group("name"))
            for match in _EMBEDDED_SPELL_START_RE.finditer(normalized_content)
        )
    known_spell_names = sorted(
        known_spell_name_set,
        key=lambda item: (-len(item), item.casefold()),
    )
    spell_records = [
        {
            "name": name,
            "key": _canonical_source_heading(name),
            "token_count": len(re.findall(r"[A-Za-z0-9/'鈥橽]+", name)),
        }
        for name in known_spell_names
    ]
    exact_spell_names: dict[str, list[str]] = {}
    spell_records_by_length: dict[int, list[dict[str, Any]]] = {}
    spell_window_sizes: set[int] = set()
    for record_value in spell_records:
        exact_spell_names.setdefault(str(record_value["key"]), []).append(
            str(record_value["name"])
        )
        spell_records_by_length.setdefault(
            len(str(record_value["key"])), []
        ).append(record_value)
        token_count = int(record_value["token_count"])
        spell_window_sizes.update(
            range(max(1, token_count - 1), token_count + 2)
        )

    def clean_entry_name(name: str) -> str:
        display_name = _normalize_candidate_display_name(name)
        level = re.search(r"(?i)\blevel\b", display_name)
        if level is not None and level.start() <= 80:
            display_name = display_name[level.end() :].strip()
        cantrip = re.search(r"(?i)\bcantrips?\b[^A-Za-z]{0,20}", display_name)
        if cantrip is not None and cantrip.start() <= 80:
            display_name = display_name[cantrip.end() :].strip()
        return display_name

    def normalized_list_text(value: str) -> str:
        result = value
        result = re.sub(
            r"(?i)(?:\(\s*A|C\s*A)\s*NTR\s*I?\s*PS",
            "CANTRIPS",
            result,
        )
        for class_name in _SPELL_CLASSES:
            split_pattern = r"\s*".join(
                (re.escape(class_name[:-1]), re.escape(class_name[-1:]))
            )
            result = re.sub(rf"(?i)\b{split_pattern}\b", class_name, result)
        for school in _SPELL_SCHOOLS:
            split_pattern = r"\s*".join(
                (re.escape(school[:-3]), re.escape(school[-3:]))
            )
            result = re.sub(rf"(?i)\b{split_pattern}\b", school, result)
        return result

    def record(name: str, class_name: str, chunk_id: str, *, ritual: bool) -> None:
        display_name = clean_entry_name(name)
        key = _canonical_source_heading(display_name)
        if not key:
            return
        item = result.setdefault(
            key,
            {
                "name": display_name,
                "classes": set(),
                "source_chunk_ids": set(),
                "ritual": False,
            },
        )
        item["classes"].add(class_name.casefold())
        if chunk_id:
            item["source_chunk_ids"].add(chunk_id)
        item["ritual"] = bool(item["ritual"] or ritual)

    def printed_names(segment: str) -> list[tuple[str, str]]:
        tokens = re.findall(r"[A-Za-z0-9/'鈥橽]+", segment)
        windows: list[tuple[int, str, str]] = []
        for window_size in sorted(spell_window_sizes):
            if window_size > len(tokens):
                continue
            for index in range(len(tokens) - window_size + 1):
                printed = " ".join(tokens[index : index + window_size])
                windows.append((index, printed, _canonical_source_heading(printed)))
        found: dict[str, tuple[int, int, str]] = {}
        for index, printed, observed in windows:
            exact = exact_spell_names.get(observed, [])
            if len(exact) == 1:
                found[exact[0]] = (0, index, printed)
        for index, printed, observed in windows:
            for length in range(max(1, len(observed) - 3), len(observed) + 4):
                for record_value in spell_records_by_length.get(length, []):
                    name = str(record_value["name"])
                    if name in found and found[name][0] == 0:
                        continue
                    target = str(record_value["key"])
                    maximum = max(1, min(3, (len(target) + 4) // 5))
                    score = _bounded_ocr_edit_distance(target, observed, maximum)
                    if score > maximum:
                        continue
                    candidate_match = (score, index, printed)
                    if name not in found or candidate_match < found[name]:
                        found[name] = candidate_match
        return [
            (name, match[2])
            for name, match in sorted(
                found.items(), key=lambda item: (item[1], item[0].casefold())
            )
        ]

    for chunk in chunks:
        content = normalized_list_text(
            " ".join(str(chunk.get("content") or "").split())
        )
        chunk_id = str(chunk.get("id") or "").strip()
        sections = list(_SPELL_LIST_SECTION_RE.finditer(content))
        for index, section in enumerate(sections):
            end = sections[index + 1].start() if index + 1 < len(sections) else len(content)
            class_name = section.group("class").casefold()
            for entry in _SPELL_LIST_ENTRY_RE.finditer(content[section.end() : end]):
                name = " ".join(entry.group("name").split())
                name = re.sub(
                    r"(?i)^(?:(?:st|nd|rd|th)\s+level|cantrips?\s*\(0\s+level\))\s+",
                    "",
                    name,
                )
                record(
                    name,
                    class_name,
                    chunk_id,
                    ritual=bool(entry.group("ritual")),
                )

    # Multi-column pages can collapse all column headers into one outline
    # heading while their bodies remain sequential chunks. Track the printed
    # class order and advance only when the spell level resets.
    active_classes: list[str] = []
    active_index = 0
    previous_level: int | None = None
    level_marker = re.compile(
        r"(?i)(?:cantrips?\s*[({][^)}]*[0o]\s+level[^)}]*[)}]|"
        r"[1-9lIsS\]](?:st|nd|rd|th)\s+level)"
    )
    for chunk in chunks:
        heading_parts = [
            " ".join(str(item).split())
            for item in chunk.get("heading_path") or []
            if str(item).strip()
        ]
        heading_text = normalized_list_text(" ".join(heading_parts))
        heading_identities = {
            _canonical_source_heading(item) for item in heading_parts
        }
        phb_spell_list = bool(
            "chapter11spells" in heading_identities
            and "spelldescriptions" not in heading_identities
        )
        if not re.search(r"(?i)\bspell\s+lists?\b", heading_text) and not phb_spell_list:
            active_classes = []
            active_index = 0
            previous_level = None
            continue
        header_classes = [
            match.group("class").casefold()
            for match in _SPELL_LIST_SECTION_RE.finditer(heading_text)
        ]
        if header_classes:
            active_classes = list(dict.fromkeys(header_classes))
            active_index = 0
            previous_level = None
        if not active_classes:
            continue
        content = normalized_list_text(
            " ".join(str(chunk.get("content") or "").split())
        )
        chunk_id = str(chunk.get("id") or "").strip()
        leaf = heading_parts[-1] if heading_parts else ""
        scan = f"{leaf} {content}".strip()
        markers = list(level_marker.finditer(scan))
        if not markers:
            segments = [(None, scan)]
        else:
            segments = (
                [(None, scan[: markers[0].start()])]
                if scan[: markers[0].start()].strip()
                else []
            ) + [
                (
                    marker.group(0),
                    scan[
                        marker.end() : (
                            markers[index + 1].start()
                            if index + 1 < len(markers)
                            else len(scan)
                        )
                    ],
                )
                for index, marker in enumerate(markers)
            ]
        for raw_level, segment in segments:
            level = previous_level
            if raw_level is not None:
                level = (
                    0
                    if raw_level.casefold().startswith("cantrip")
                    else int(
                        {
                            "i": "1",
                            "l": "1",
                            "s": "5",
                            "]": "7",
                        }.get(raw_level[0].casefold(), raw_level[0])
                    )
                )
                if (
                    previous_level is not None
                    and level <= previous_level
                    and active_index + 1 < len(active_classes)
                ):
                    active_index += 1
            for entry in _SPELL_LIST_ENTRY_RE.finditer(segment):
                record(
                    entry.group("name"),
                    active_classes[active_index],
                    chunk_id,
                    ritual=bool(entry.group("ritual")),
                )
            for _source_name, printed_name in printed_names(segment):
                record(
                    printed_name,
                    active_classes[active_index],
                    chunk_id,
                    ritual=False,
                )
            if level is not None:
                previous_level = level
    return result


def _spell_class_record(
    index: dict[str, dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    key = _canonical_source_heading(name)
    exact = index.get(key)
    if exact is not None:
        return exact

    maximum = max(1, min(3, (len(key) + 4) // 5))
    matches = [
        (
            _bounded_ocr_edit_distance(key, candidate_key, maximum),
            candidate_key,
            record,
        )
        for candidate_key, record in index.items()
        if abs(len(key) - len(candidate_key)) <= maximum
    ]
    matches = sorted(item for item in matches if item[0] <= maximum)
    if not matches or (len(matches) > 1 and matches[0][0] == matches[1][0]):
        return {}
    return matches[0][2]


def _spell_class_mentions(chunks: list[dict[str, Any]], spell_name: str) -> dict[str, set[str]]:
    """Recover list eligibility from list headings and bounded prose declarations."""

    classes: set[str] = set()
    source_chunk_ids: set[str] = set()
    name_pattern = re.compile(
        r"(?i)(?<![A-Za-z])"
        + r"\s+".join(re.escape(part) for part in spell_name.split())
        + r"(?![A-Za-z])"
    )
    for chunk in chunks:
        content = " ".join(str(chunk.get("content") or "").split())
        heading_text = " ".join(str(item) for item in chunk.get("heading_path") or [])
        match = name_pattern.search(content)
        heading_match = name_pattern.search(heading_text)
        if match is None and heading_match is None:
            continue
        list_layout = any(
            _canonical_source_heading(item) == "spelllists"
            for item in chunk.get("heading_path") or []
        )
        prior_classes = set(classes)
        if not list_layout:
            for class_name in _SPELL_CLASSES:
                if re.search(
                    rf"(?i)\b{re.escape(class_name)}\s+Spell(?:s|\s+List)\b",
                    heading_text + " " + content[:500],
                ):
                    classes.add(class_name.casefold())
            sentence = (
                content[max(0, match.start() - 250) : match.end() + 350]
                if match is not None
                else heading_text + " " + content[:500]
            )
            if re.search(r"(?i)\bspell\s+lists?\b", sentence):
                for class_name in _SPELL_CLASSES:
                    if re.search(rf"(?i)\b{re.escape(class_name)}\b", sentence):
                        classes.add(class_name.casefold())
        if classes != prior_classes and (chunk_id := str(chunk.get("id") or "").strip()):
            source_chunk_ids.add(chunk_id)

        if list_layout:
            continue

        # Some books introduce a group of spells once (for example, "These
        # cantrips are on the sorcerer, warlock, and wizard spell lists") and
        # then nest each spell beneath that declaration.  PDF layout can also
        # split a word across the declaration heading and its body.  Recover
        # that bounded ancestor context instead of requiring every spell name
        # to be repeated in the introductory sentence.
        heading_parts = {
            " ".join(str(item).split()).casefold()
            for item in chunk.get("heading_path") or []
            if str(item).strip()
        }
        for context_chunk in chunks:
            context_headings = [
                " ".join(str(item).split())
                for item in context_chunk.get("heading_path") or []
                if str(item).strip()
            ]
            shared_declaration = any(
                heading.casefold() in heading_parts
                and re.search(r"(?i)\b(?:cantrips?|spells?)\b", heading)
                and heading.casefold()
                not in {"spell", "spells", "spell lists", "spell descriptions"}
                and not re.match(
                    r"(?i)^(?:ch(?:apter)?\.?\s*\d+\s*[:.-]?\s*)?spells$",
                    heading.strip(),
                )
                for heading in context_headings
            )
            if not shared_declaration:
                continue
            declaration = " ".join(
                [
                    *context_headings,
                    " ".join(str(context_chunk.get("content") or "").split()),
                ]
            )
            declaration = re.sub(r"\ufffe\s*", "", declaration)
            if not re.search(
                r"(?i)\b(?:cantrips?|spells?)\b.{0,300}\bspell\s+lists?\b",
                declaration,
            ):
                continue
            prior_classes = set(classes)
            for class_name in _SPELL_CLASSES:
                if re.search(rf"(?i)\b{re.escape(class_name)}\b", declaration):
                    classes.add(class_name.casefold())
            if classes != prior_classes and (
                context_id := str(context_chunk.get("id") or "").strip()
            ):
                source_chunk_ids.add(context_id)
    return {"classes": classes, "source_chunk_ids": source_chunk_ids}


def _spell_range(value: str) -> dict[str, Any]:
    text = " ".join(value.split())
    folded = text.casefold()
    if folded.startswith("self"):
        kind = "self"
    elif folded == "touch":
        kind = "touch"
    elif folded == "sight":
        kind = "sight"
    elif folded == "unlimited":
        kind = "unlimited"
    else:
        kind = (
            "distance"
            if re.search(r"\b\d+\s*(?:feet|foot|ft\.?|miles?)\b", folded)
            else "special"
        )
    distance = re.search(r"\b(\d+)\s*(feet|foot|ft\.?|miles?)\b", folded)
    normal_ft = 0
    if distance:
        normal_ft = int(distance.group(1)) * (5280 if "mile" in distance.group(2) else 1)
    area = ""
    if kind == "self" and "(" in text and ")" in text:
        area = text[text.find("(") + 1 : text.rfind(")")].strip()
    return {"kind": kind, "normal_ft": normal_ft, "long_ft": 0, "area": area}


def _spell_duration(value: str) -> dict[str, Any]:
    text = " ".join(value.split())
    folded = text.casefold()
    concentration = folded.startswith("concentration")
    if folded == "instantaneous":
        return {
            "kind": "instantaneous",
            "value": 0,
            "unit": "round",
            "concentration": False,
        }
    if folded in {"until dispelled", "special"}:
        return {
            "kind": "until_dispelled" if folded == "until dispelled" else "special",
            "value": 0,
            "unit": "special",
            "concentration": concentration,
        }
    duration = re.search(r"(\d+)\s+(round|minute|hour|day)s?", folded)
    return {
        "kind": "timed" if duration else "special",
        "value": int(duration.group(1)) if duration else 0,
        "unit": duration.group(2) if duration else "special",
        "concentration": concentration,
    }


def _spell_components(value: str) -> dict[str, Any]:
    text = " ".join(value.split())
    tokens = {item.strip().upper() for item in text.split(",")[:3]}
    material_match = re.search(r"(?i)\bM\s*\((.+)\)\s*$", text)
    material_description = material_match.group(1).strip() if material_match else ""
    return {
        "verbal": "V" in tokens or bool(re.search(r"(?i)(?:^|,\s*)V(?:,|$)", text)),
        "somatic": "S" in tokens or bool(re.search(r"(?i)(?:^|,\s*)S(?:,|$)", text)),
        "material": bool(material_match or re.search(r"(?i)(?:^|,\s*)M(?:,|$)", text)),
        "material_description": material_description,
        "material_cost_cp": 0,
        "consumed": "consume" in material_description.casefold(),
    }


def _rulebook_statblock_candidates(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed = list(enumerate(chunks))
    ordered = [
        chunk
        for _index, chunk in sorted(
            indexed,
            key=lambda item: (
                item[1].get("section_ordinal", 0),
                item[1].get("ordinal", 0),
                item[1].get("page_start", 0),
                item[0],
            ),
        )
    ]
    roots = [
        index
        for index, chunk in enumerate(ordered)
        if _CREATURE_CORE_RE.match(str(chunk.get("content") or "").strip())
    ]
    candidates = []
    ignored_titles = {
        *{item.casefold() for item in ABILITY_LABELS},
        "actions",
        "reactions",
        "legendary actions",
        "traits",
    }

    def ignored_heading(value: str) -> bool:
        folded = value.casefold()
        canonical = _canonical_source_heading(value)
        return (
            folded in ignored_titles
            or canonical.isdecimal()
            or bool(re.match(r"^(?:chapter|appendix)\d+", canonical))
            or bool(re.search(r"(?:chapter|appendix)\d+", canonical))
        )

    def preceding_statblock_name(start: int, *, page_number: int | None) -> str | None:
        lower_bound = max(0, start - 20)
        matches: list[str] = []
        for chunk in reversed(ordered[lower_bound:start]):
            chunk_start = chunk.get("page_start")
            chunk_end = chunk.get("page_end")
            if (
                page_number is not None
                and isinstance(chunk_start, int)
                and isinstance(chunk_end, int)
                and not chunk_start <= page_number <= chunk_end
            ):
                continue
            content = " ".join(str(chunk.get("content") or "").split())
            for match in re.finditer(
                r"(?i)\buses\s+the\s+"
                r"(?P<name>[A-Z][A-Za-z0-9 '/()\-]{1,100}?)\s+stat\s+block\b",
                content,
            ):
                matches.append(" ".join(match.group("name").split()))
            if matches:
                break
        unique = list(dict.fromkeys(item.casefold() for item in matches))
        if len(unique) != 1:
            return None
        return next(item for item in matches if item.casefold() == unique[0])

    for root_number, start in enumerate(roots):
        end = roots[root_number + 1] if root_number + 1 < len(roots) else len(ordered)
        root = ordered[start]
        path = [str(item).strip() for item in root.get("heading_path") or [] if str(item).strip()]
        name_index = next(
            (
                index
                for index in range(len(path) - 1, -1, -1)
                if not ignored_heading(path[index])
            ),
            None,
        )
        while (
            name_index is not None
            and name_index > 0
            and _canonical_source_heading(path[name_index - 1])
            == _canonical_source_heading(path[name_index])
        ):
            name_index -= 1
        scoped_path = path[: name_index + 1] if name_index is not None else path
        name = (
            path[name_index]
            if name_index is not None
            else f"Creature on page {root.get('page_start') or '?'}"
        )
        if name.casefold() in _STATBLOCK_PLACEHOLDER_TITLES:
            continue
        ignored_suffix = name_index is not None and name_index < len(path) - 1
        if (
            name_index is None
            or ignored_suffix
            or ignored_heading(name)
            or name.casefold() in _GENERIC_TITLES
        ):
            inferred_name = preceding_statblock_name(
                start,
                page_number=(
                    root.get("page_start")
                    if isinstance(root.get("page_start"), int)
                    else None
                ),
            )
            if inferred_name is not None:
                name = inferred_name
                scoped_path = (
                    path[: name_index + 1]
                    if ignored_suffix and name_index is not None
                    else path[:name_index]
                    if name_index is not None
                    else path[:-1]
                )

        # Some two-column PDFs attach the creature name only to the core row,
        # while the six ability cells remain siblings under the surrounding
        # section.  Widen to that parent only when the complete ordered ability
        # row is present before the next creature core.
        widened_to_sibling_abilities = False
        if name_index is not None and scoped_path:
            parent_path = path[:name_index]
            def ability_coverage(prefix: list[str]) -> set[str]:
                coverage: set[str] = set()
                for chunk in ordered[start + 1 : end]:
                    chunk_path = [
                        str(value).strip()
                        for value in chunk.get("heading_path") or []
                        if str(value).strip()
                    ]
                    if chunk_path[: len(prefix)] != prefix or not chunk_path:
                        continue
                    coverage.update(
                        _statblock_ability_heading_labels(chunk_path[-1])
                    )
                return coverage

            all_abilities = set(ABILITY_LABELS)
            current_has_abilities = all_abilities.issubset(
                ability_coverage(scoped_path)
            )
            sibling_has_abilities = all_abilities.issubset(
                ability_coverage(parent_path)
            )
            if not current_has_abilities and sibling_has_abilities:
                scoped_path = parent_path
                widened_to_sibling_abilities = True

        if scoped_path:
            end = next(
                (
                    index
                    for index in range(start + 1, end)
                    if [
                        str(item).strip()
                        for item in ordered[index].get("heading_path") or []
                        if str(item).strip()
                    ][: len(scoped_path)]
                    != scoped_path
                ),
                end,
            )
        if widened_to_sibling_abilities:
            # Widening to the parent is necessary for malformed PDF tables
            # whose six ability cells became siblings of the creature title.
            # That parent can also contain an entire chapter, so retain only
            # the shortest local prefix that forms a complete statblock, plus
            # immediately following action/reaction sections.
            minimum_end: int | None = None
            for candidate_end in range(start + 1, end + 1):
                try:
                    _normalize_module_statblock(name, ordered[start:candidate_end])
                except (StatblockImportError, ValueError):
                    continue
                minimum_end = candidate_end
                break
            if minimum_end is not None:
                end = minimum_end
                while end < len(ordered):
                    next_path = [
                        str(item).strip()
                        for item in ordered[end].get("heading_path") or []
                        if str(item).strip()
                    ]
                    if next_path[: len(scoped_path)] != scoped_path:
                        break
                    if not next_path or _canonical_statblock_section_title(
                        next_path[-1]
                    ) is None:
                        break
                    end += 1
        scoped = ordered[start:end]
        chunk_ids = list(
            dict.fromkeys(
                str(item.get("id") or "")
                for item in scoped
                if str(item.get("id") or "")
            )
        )
        identity = "\x1f".join(("statblock", name.casefold(), chunk_ids[0]))
        candidate = {
            "id": "candidate:"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
            "kind": "statblock",
            "name": name,
            "source_chunk_ids": chunk_ids,
            "source_heading_path": scoped_path,
            "page_start": _minimum_page_values(item.get("page_start") for item in scoped),
            "page_end": _maximum_page_values(item.get("page_end") for item in scoped),
            "extraction_confidence": "high",
            "extraction_signals": ["creature core", "ordered statblock fields"],
            "review_status": "pending",
            "mechanical_scope": "review_required",
            "application_state": "catalog_only",
            "execution_state": "agent_resolution_required",
            "artifact": {
                "kind": "statblock",
                "application_state": "catalog_only",
                "mechanical_scope": "review_required",
                "card": {"name": name},
            },
        }
        try:
            normalized_content = _normalize_module_statblock(name, scoped)
            candidate["normalized_content"] = normalized_content
            candidate["artifact"]["card"]["normalized_content"] = normalized_content
            candidate["execution_state"] = "review_ready"
        except (StatblockImportError, ValueError) as error:
            candidate["review_error"] = str(error)
            candidate["extraction_confidence"] = "medium"
        candidates.append(candidate)
    return candidates


def _merge_extracted_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _repair_subclass_table_ocr_identities(candidates)
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    generic_spell_titles = {"spell", "spells", "spell descriptions", "optional spells"}
    embedded_spell_chunks = {
        chunk_id
        for item in candidates
        if item.get("kind") == "spell"
        and "embedded spell header" in item.get("extraction_signals", [])
        for chunk_id in item.get("source_chunk_ids") or []
    }
    structural_statblock_chunks = {
        chunk_id
        for item in candidates
        if item.get("kind") == "statblock"
        and "creature core" in item.get("extraction_signals", [])
        for chunk_id in item.get("source_chunk_ids") or []
    }
    structural_statblock_paths = [
        [str(value).strip().casefold() for value in item.get("source_heading_path") or []]
        for item in candidates
        if item.get("kind") == "statblock"
        and "creature core" in item.get("extraction_signals", [])
    ]
    ordered_species_chunks = {
        chunk_id
        for item in candidates
        if item.get("kind") == "species"
        and any(
            signal in {"ordered species traits", "ordered subrace boundary"}
            for signal in item.get("extraction_signals", [])
        )
        for chunk_id in item.get("source_chunk_ids") or []
    }
    for candidate in candidates:
        if candidate.get("kind") == "species" and str(
            candidate.get("name") or ""
        ).casefold().endswith(" traits"):
            candidate["name"] = str(candidate["name"])[: -len(" Traits")]
            card = dict(dict(candidate.get("artifact") or {}).get("card") or {})
            card["name"] = candidate["name"]
            candidate["artifact"]["card"] = card
        if (
            candidate.get("kind") == "spell"
            and str(candidate.get("name") or "").casefold() in generic_spell_titles
            and embedded_spell_chunks.intersection(candidate.get("source_chunk_ids") or [])
        ):
            continue
        if (
            candidate.get("kind") == "species"
            and not any(
                signal in {"ordered species traits", "ordered subrace boundary"}
                for signal in candidate.get("extraction_signals", [])
            )
            and ordered_species_chunks.intersection(
                candidate.get("source_chunk_ids") or []
            )
        ):
            continue
        if (
            candidate.get("kind") == "statblock"
            and "creature core" not in candidate.get("extraction_signals", [])
            and (
                structural_statblock_chunks.intersection(
                    candidate.get("source_chunk_ids") or []
                )
                or any(
                    structural_path[: len(candidate_path)] == candidate_path
                    for structural_path in structural_statblock_paths
                    if (
                        candidate_path := [
                            str(value).strip().casefold()
                            for value in candidate.get("source_heading_path") or []
                        ]
                    )
                )
            )
        ):
            continue
        kind = str(candidate.get("kind") or "").casefold()
        candidate_name = _normalize_candidate_display_name(
            str(candidate.get("name") or "")
        )
        candidate["name"] = candidate_name
        candidate_card = dict(dict(candidate.get("artifact") or {}).get("card") or {})
        candidate_card["name"] = candidate_name
        candidate["artifact"]["card"] = candidate_card
        if kind == "spell":
            candidate_name = candidate_name.rstrip(" .:;")
        identity_context: tuple[str, ...] = ()
        if kind in {"feat", "feature", "subclass"}:
            # Same-named features commonly belong to different classes or
            # subclasses (for example two Artificer specialists each grant
            # "Tools of the Trade"). Keep those source-defined identities
            # separate; the compiler already gives them stable disambiguated
            # artifact ids. Candidates from the same heading path still merge.
            identity_context = tuple(
                _canonical_source_heading(value)
                for value in candidate.get("source_heading_path") or []
            )[:-1]
        key = (kind, _canonical_source_heading(candidate_name), *identity_context)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        existing_signals = set(existing.get("extraction_signals") or [])
        candidate_signals = set(candidate.get("extraction_signals") or [])
        preferred, other = (
            (candidate, existing)
            if (
                "embedded spell header" in candidate_signals
                or "creature core" in candidate_signals
            )
            and not (
                "embedded spell header" in existing_signals
                or "creature core" in existing_signals
            )
            else (existing, candidate)
        )
        if (
            kind == "spell"
            and "'" not in str(preferred.get("name") or "")
            and "'" in str(other.get("name") or "")
        ):
            preferred_words = str(preferred.get("name") or "").split()
            possessive_words = [
                word for word in str(other.get("name") or "").split() if "'" in word
            ]
            for possessive in possessive_words:
                possessive_key = _canonical_source_heading(possessive)
                for index, preferred_word in enumerate(preferred_words):
                    if _canonical_source_heading(preferred_word) != possessive_key:
                        continue
                    if possessive.casefold().endswith("'s") and preferred_word.casefold().endswith(
                        "s"
                    ):
                        preferred_words[index] = preferred_word[:-1] + "'s"
                    break
            preferred["name"] = " ".join(preferred_words)
            preferred_artifact = dict(preferred.get("artifact") or {})
            preferred_card = dict(preferred_artifact.get("card") or {})
            preferred_card["name"] = preferred["name"]
            preferred_artifact["card"] = preferred_card
            preferred["artifact"] = preferred_artifact
        preferred["source_chunk_ids"] = list(
            dict.fromkeys(
                [
                    *preferred.get("source_chunk_ids", []),
                    *other.get("source_chunk_ids", []),
                ]
            )
        )
        preferred_artifact = dict(preferred.get("artifact") or {})
        preferred_card = dict(preferred_artifact.get("card") or {})
        other_card = dict(dict(other.get("artifact") or {}).get("card") or {})
        descriptions = list(
            dict.fromkeys(
                str(value).strip()
                for value in (
                    preferred_card.get("description"),
                    other_card.get("description"),
                )
                if str(value or "").strip()
            )
        )
        if descriptions:
            preferred_card["description"] = "\n\n".join(descriptions)[:12000]
            preferred_artifact["card"] = preferred_card
            preferred["artifact"] = preferred_artifact
        merged[key] = preferred
    return list(merged.values())


def _repair_subclass_table_ocr_identities(
    candidates: list[dict[str, Any]],
) -> None:
    """Join a uniquely near-identical spell table to its subclass heading.

    Display-font OCR can corrupt one glyph in a repeated subclass name even
    when the prose heading was recovered correctly (for example ``LICHT
    DOMAIN SPELLS`` beside ``LIGHT DOMAIN``).  The correction is intentionally
    relational: it needs a non-table subclass in the same source container and
    a unique one-edit match.  A standalone custom name is therefore never
    rewritten from a built-in vocabulary guess.
    """

    bases: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    tables: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for candidate in candidates:
        if str(candidate.get("kind") or "").casefold() != "subclass":
            continue
        path = [str(value).strip() for value in candidate.get("source_heading_path") or []]
        if not path:
            continue
        context = tuple(_canonical_source_heading(value) for value in path[:-1])
        if path[-1].casefold().endswith(" spells"):
            tables.append((context, candidate))
        else:
            bases.setdefault(context, []).append(candidate)

    for context, table in tables:
        table_key = _canonical_source_heading(str(table.get("name") or ""))
        matches = [
            candidate
            for candidate in bases.get(context, [])
            if _bounded_ocr_edit_distance(
                table_key,
                _canonical_source_heading(str(candidate.get("name") or "")),
                1,
            )
            <= 1
        ]
        if len(matches) != 1:
            continue
        repaired_name = str(matches[0].get("name") or "").strip()
        if not repaired_name:
            continue
        table["name"] = repaired_name
        artifact = dict(table.get("artifact") or {})
        card = dict(artifact.get("card") or {})
        card["name"] = repaired_name
        artifact["card"] = card
        table["artifact"] = artifact


def _unclaimed_mechanical_signals(content: str) -> list[str]:
    sample = " ".join(content.split())[:12000]
    folded = sample.casefold()
    signals = []
    if "casting time:" in folded and "components:" in folded:
        signals.append("spell fields")
    if all(label in folded for label in ("armor class", "hit points", "speed")):
        signals.append("statblock core")
    if re.search(r"(?i)\b(?:class|subclass|archetype) features\b", sample):
        signals.append("class feature table")
    if _ITEM_HEADER_RE.search(_normalize_item_header_ocr_text(sample[:1000])):
        signals.append("item header")
    if len(
        [
            label
            for label in ("ability score increase", "age", "alignment", "size", "speed")
            if label in folded
        ]
    ) >= 4:
        signals.append("species traits")
    dice_expressions = re.findall(
        r"\b\d+d(?:4|6|8|10|12|20|100)(?:\s*[+-]\s*\d+)?\b",
        folded,
    )
    numbered_entries = re.findall(r"(?:^|\s)\d{1,5}\s+[a-z]", folded)
    operational_terms = sum(
        term in folded
        for term in (
            "attack",
            "caster",
            "damage",
            "hit points",
            "paralyzed",
            "poisoned",
            "round",
            "saving throw",
            "spell",
            "target",
            "turn",
        )
    )
    if len(numbered_entries) >= 2 and (dice_expressions or operational_terms >= 1):
        signals.append("random effect table")
    elif len(dice_expressions) >= 2 and operational_terms >= 2:
        signals.append("dice procedure")
    if re.search(
        r"\b(?:armor class|check|damage|hit points?|roll|saving throw|spell slots?)\b",
        folded,
    ) and re.search(
        r"\b(?:allow|can|for\s+\d|may|must|per|until)\b",
        folded,
    ):
        signals.append("rule procedure")
    if operational_terms >= 1 and (
        re.search(r"\bif\b.{0,500}\bthen\b", folded)
        or (
            "should" in folded
            and any(
                marker in folded
                for marker in ("benefit", "effect", "gm", "player", "price")
            )
        )
    ):
        signals.append("adjudication guidance")
    return signals


def module_statblock_review_candidates(
    chunks: list[dict[str, Any]],
    *,
    source_title: str = "",
) -> list[dict[str, Any]]:
    """Build review-only executable statblocks from source-proven module chunks.

    Candidates that retain ambiguous OCR or omit a required combat fact remain
    visible with a manual-review error. They are never silently repaired.
    """
    del source_title
    candidates: list[dict[str, Any]] = []
    scoped_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for ordered in _ordered_chunks_by_scene(chunks):
        roots: list[int] = []
        for index, chunk in enumerate(ordered):
            content = str(chunk.get("content") or "").strip()
            path = [
                str(item).strip()
                for item in chunk.get("heading_path") or []
                if str(item).strip()
            ]
            if not path or _CREATURE_CORE_RE.match(content) is None:
                continue
            roots.append(index)
        for root_index, start in enumerate(roots):
            end = roots[root_index + 1] if root_index + 1 < len(roots) else len(ordered)
            root = ordered[start]
            path = [
                str(item).strip()
                for item in root.get("heading_path") or []
                if str(item).strip()
            ]
            key = tuple(item.casefold() for item in path)
            scene_id = str(root.get("scene_id") or "")
            scoped = ordered[start:end]
            identity = "\x1f".join(("statblock", scene_id, *key))
            candidate_id = "candidate:" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:20]
            scoped_by_candidate[candidate_id] = scoped
            candidates.append(
                {
                    "id": candidate_id,
                    "kind": "statblock",
                    "name": path[-1],
                    "source_chunk_ids": list(
                        dict.fromkeys(
                            str(chunk.get("id") or "")
                            for chunk in scoped
                            if str(chunk.get("id") or "")
                        )
                    ),
                    "source_heading_path": path,
                    "page_start": _minimum_page_values(
                        chunk.get("page_start") for chunk in scoped
                    ),
                    "page_end": _maximum_page_values(
                        chunk.get("page_end") for chunk in scoped
                    ),
                    "extraction_confidence": "high",
                    "extraction_signals": [
                        "armor class",
                        "hit points",
                        "speed",
                        "six ability headings",
                    ],
                    "review_status": "pending",
                    "application_state": "review_only",
                    "execution_state": "not_compiled",
                }
            )
    for candidate in candidates:
        scoped = scoped_by_candidate[str(candidate["id"])]
        candidate["source_scene_ids"] = list(
            dict.fromkeys(
                str(chunk.get("scene_id") or "")
                for chunk in scoped
                if str(chunk.get("scene_id") or "")
            )
        )
        try:
            normalized = _normalize_module_statblock(str(candidate["name"]), scoped)
            parsed = parse_2014_statblock(
                normalized,
                source_key=f"module-candidate:{candidate['id']}",
            )
        except (StatblockImportError, ValueError) as error:
            candidate["review_status"] = "manual_review_required"
            candidate["execution_state"] = "blocked"
            candidate["review_error"] = str(error)
            continue
        candidate["normalized_content"] = normalized
        candidate["review_status"] = "pending"
        candidate["execution_state"] = "review_ready"
        candidate["validation"] = {
            "name": parsed.name,
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "normalization_notes": list(parsed.normalization_notes),
            "settlement": "automatic" if not parsed.warnings else "mixed",
        }
    return candidates


def normalize_2014_statblock_candidate(
    name: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recover one named 2014 statblock from deterministic text-layout chunks.

    PDF layout extraction can place a section heading before the final ability
    column, or attach all headings to the preceding creature. Select the
    requested creature by its own core heading and stop at the next creature
    core so recovery never borrows facts from an adjacent statblock.
    """

    target = name.strip()
    if not target:
        raise ValueError("statblock candidate name must not be empty")
    canonical_target = _canonical_source_heading(target)
    matches: list[tuple[list[dict[str, Any]], int]] = []
    for ordered in _ordered_chunks_by_scene(chunks):
        for index, chunk in enumerate(ordered):
            if (
                _CREATURE_CORE_RE.match(str(chunk.get("content") or "").strip())
                is None
            ):
                continue
            heading = next(
                (
                    str(value).strip()
                    for value in reversed(chunk.get("heading_path") or [])
                    if str(value).strip()
                ),
                "",
            )
            if _canonical_source_heading(heading) == canonical_target:
                matches.append((ordered, index))
    if not matches:
        raise StatblockImportError(
            f"statblock source chunks contain no creature core headed {target!r}"
        )
    if len(matches) > 1:
        raise StatblockImportError(
            f"statblock source chunks contain multiple creature cores headed {target!r}"
        )
    ordered, root_index = matches[0]
    end_index = next(
        (
            index
            for index in range(root_index + 1, len(ordered))
            if _CREATURE_CORE_RE.match(
                str(ordered[index].get("content") or "").strip()
            )
            is not None
        ),
        len(ordered),
    )
    if end_index < len(ordered):
        next_core_path = [
            str(value).strip()
            for value in ordered[end_index].get("heading_path") or []
            if str(value).strip()
        ]
        next_core_heading = (
            _canonical_source_heading(next_core_path[-1])
            if next_core_path
            else ""
        )
        if next_core_heading:
            # Two-column extraction can emit the next creature's lore before
            # its compact core line. Rewind across only contiguous chunks that
            # already carry that exact next-creature heading, so the current
            # card cannot absorb adjacent lore.
            while end_index > root_index + 1:
                prior_path = [
                    _canonical_source_heading(str(value))
                    for value in ordered[end_index - 1].get("heading_path") or []
                ]
                if next_core_heading not in prior_path:
                    break
                end_index -= 1
    scoped = ordered[root_index:end_index]
    normalized = _normalize_module_statblock(target, scoped)
    source_chunk_ids = list(
        dict.fromkeys(
            str(chunk.get("id") or "").strip()
            for chunk in scoped
            if str(chunk.get("id") or "").strip()
        )
    )
    return {
        "name": target,
        "normalized_content": normalized,
        "source_chunk_ids": source_chunk_ids,
    }


def _ordered_chunks_by_scene(
    chunks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Keep scene-local ordinals from interleaving unrelated statblocks."""

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, chunk in enumerate(chunks):
        grouped.setdefault(str(chunk.get("scene_id") or ""), []).append((index, chunk))
    return [
        [
            chunk
            for _index, chunk in sorted(
                indexed,
                key=lambda item: (
                    item[1].get("ordinal")
                    if isinstance(item[1].get("ordinal"), int)
                    else item[0],
                    item[0],
                ),
            )
        ]
        for indexed in grouped.values()
    ]


def _canonical_statblock_section_title(value: str) -> str | None:
    title = " ".join(str(value or "").split())
    compact = re.sub(r"[^A-Z0-9]", "", title.upper())
    common = {
        "ACTIONS": "ACTIONS",
        "REACTIONS": "REACTIONS",
        "LEGENDARYACTIONS": "LEGENDARY ACTIONS",
    }.get(compact)
    if common is not None:
        return common
    variant = re.fullmatch(r"(?i)ACTIONS\s+FOR\s+(.+)", title)
    if variant is None:
        return None
    label = " ".join(variant.group(1).split()).upper()
    return f"ACTIONS FOR {label}" if label else None


def _statblock_ability_heading_labels(value: str) -> list[str]:
    """Return an exact ability-label partition for a PDF heading.

    Column extraction can preserve ``DEX CON`` as one heading while emitting
    the other labels separately. Only a full concatenation of the six closed
    D&D abbreviations is accepted, so prose headings cannot become score rows.
    """

    compact = re.sub(r"[^A-Z]", "", str(value or "").upper())
    labels: list[str] = []
    cursor = 0
    while cursor < len(compact):
        label = next(
            (
                ability
                for ability in ABILITY_LABELS
                if compact.startswith(ability, cursor)
            ),
            None,
        )
        if label is None or label in labels:
            return []
        labels.append(label)
        cursor += len(label)
    return labels


def _normalize_module_statblock(name: str, chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        raise StatblockImportError("statblock candidate has no source chunks")
    root = next(
        (
            chunk
            for chunk in chunks
            if _CREATURE_CORE_RE.match(str(chunk.get("content") or "").strip())
        ),
        None,
    )
    if root is None:
        raise StatblockImportError("statblock candidate has no creature core chunk")
    root_path = list(root.get("heading_path") or [])
    root_text = str(root.get("content") or "").strip()
    core_text = re.split(r"(?m)^#{2,6}\s+", root_text, maxsplit=1)[0].strip()
    core = _CREATURE_CORE_RE.match(" ".join(core_text.splitlines()))
    if core is None:
        raise StatblockImportError(
            "statblock candidate needs an unambiguous size/type, Armor Class, Hit Points, and Speed"
        )

    ability_values: dict[str, str] = {}
    detail_parts: list[str] = []
    section_parts: dict[str, list[str]] = {
        "ACTIONS": [],
        "REACTIONS": [],
        "LEGENDARY ACTIONS": [],
    }
    active_section: str | None = None
    expanded_chunks = [chunk for chunk in chunks if chunk is not root]
    heading_matches = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", root_text))
    for index, match in enumerate(heading_matches):
        end = (
            heading_matches[index + 1].start()
            if index + 1 < len(heading_matches)
            else len(root_text)
        )
        expanded_chunks.append(
            {
                "heading_path": [*root_path, match.group(1).strip()],
                "content": root_text[match.end() : end].strip(),
            }
        )
    ability_chunks = [
        (chunk, _statblock_ability_heading_labels(path[-1]))
        for chunk in expanded_chunks
        if (
            path := [
                str(item).strip()
                for item in chunk.get("heading_path") or []
                if str(item).strip()
            ]
        )
        and _statblock_ability_heading_labels(path[-1])
    ]
    flattened_ability_labels = [
        label for _chunk, labels in ability_chunks for label in labels
    ]
    consumed_ability_chunks: set[int] = set()
    fragmented_ability_row = any(
        not str(chunk.get("content") or "").strip()
        for chunk, _labels in ability_chunks
    )
    if (
        len(flattened_ability_labels) == len(ABILITY_LABELS)
        and set(flattened_ability_labels) == set(ABILITY_LABELS)
        and fragmented_ability_row
    ):
        ability_row = " ".join(
            str(chunk.get("content") or "").strip()
            for chunk, _labels in ability_chunks
            if str(chunk.get("content") or "").strip()
        )
        cursor = 0
        for ability in ABILITY_LABELS:
            score = re.match(
                r"^\s*(?P<score>\d+)\s*\([^)]{1,12}\)",
                ability_row[cursor:],
            )
            if score is None:
                raise StatblockImportError(
                    "statblock reconstructed ability score row is ambiguous"
                )
            score_value = int(score.group("score"))
            modifier = (score_value - 10) // 2
            ability_values[ability] = f"{score_value} ({modifier:+d})"
            cursor += score.end()
        tail = ability_row[cursor:].strip()
        if tail:
            detail_parts.append(tail)
        consumed_ability_chunks = {id(chunk) for chunk, _labels in ability_chunks}
    for chunk in expanded_chunks:
        if id(chunk) in consumed_ability_chunks:
            continue
        path = [str(item).strip() for item in chunk.get("heading_path") or []]
        title = path[-1].upper()
        compact_title = re.sub(r"[^A-Z]", "", title)
        ability_titles = _statblock_ability_heading_labels(path[-1])
        canonical_section = _canonical_statblock_section_title(path[-1])
        content = str(chunk.get("content") or "").strip()
        if canonical_section is not None:
            section_parts.setdefault(canonical_section, [])
            active_section = canonical_section
        if ability_titles and len(ability_titles) > 1:
            cursor = 0
            for ability in ability_titles:
                score = re.match(
                    r"^\s*(\d+)\s*\(\s*(?P<modifier>[^)]{1,8})\s*\)",
                    content[cursor:],
                )
                if score is None:
                    raise StatblockImportError(
                        "statblock grouped ability score row is ambiguous"
                    )
                modifier = score.group("modifier").translate(_OCR_ABILITY_DIGITS)
                modifier = re.sub(r"[^+\-0-9]", "-", modifier)
                ability_values[ability] = f"{score.group(1)} ({modifier})"
                cursor += score.end()
            tail = content[cursor:].strip()
            if tail:
                if active_section is None:
                    detail_parts.append(tail)
                else:
                    section_parts[active_section].append(tail)
            continue
        if compact_title == "".join(ABILITY_LABELS):
            cursor = 0
            for ability in ABILITY_LABELS:
                score = re.match(
                    r"^\s*(\d+)\s*\(([+\-−][0-9lIOS]+)\)",
                    content[cursor:],
                )
                if score is None:
                    raise StatblockImportError(
                        "statblock combined ability score row is ambiguous"
                    )
                modifier = score.group(2).translate(_OCR_ABILITY_DIGITS)
                ability_values[ability] = f"{score.group(1)} ({modifier})"
                cursor += score.end()
            tail = content[cursor:].strip()
            if tail:
                if active_section is None:
                    detail_parts.append(tail)
                else:
                    section_parts[active_section].append(tail)
            continue
        ability_title = compact_title if compact_title in ABILITY_LABELS else title
        if ability_title in ABILITY_LABELS:
            score = re.match(
                r"^\s*(\d+)\s*\(\s*(?P<sign>[+\-−]?)\s*"
                r"(?P<modifier>[0-9lIOS]+)\s*\)(?P<tail>.*)$",
                content,
                re.S,
            )
            if score is None:
                raise StatblockImportError(f"statblock {title} score is ambiguous")
            sign = score.group("sign").replace("−", "-")
            modifier = score.group("modifier").translate(_OCR_ABILITY_DIGITS)
            ability_values[ability_title] = f"{score.group(1)} ({sign}{modifier})"
            tail = score.group("tail").strip()
            if tail:
                if active_section is None:
                    detail_parts.append(tail)
                else:
                    section_parts[active_section].append(tail)
            continue
        path_sections = {
            _canonical_statblock_section_title(item) for item in path
        }
        section = next((value for value in section_parts if value in path_sections), None)
        if section is not None:
            if canonical_section != section and content:
                section_parts[section].append(f"{path[-1]}. {content}")
            elif content:
                section_parts[section].append(content)
        elif content:
            detail_parts.append(content)
    missing_abilities = [label for label in ABILITY_LABELS if label not in ability_values]
    if missing_abilities:
        raise StatblockImportError(
            "statblock candidate is missing ability scores: " + ", ".join(missing_abilities)
        )

    normalized_details = _normalize_statblock_ocr(" ".join(detail_parts))
    inline_actions = re.search(
        r"(?i)(?<![A-Za-z])Actions"
        r"(?:\s*\(Requires?\s+Your\s+Bonus\s+Action\))?\s+"
        r"(?=[A-Z][A-Za-z0-9'鈥?-]*(?:\s+[A-Z][A-Za-z0-9'鈥?-]*)*\.\s+"
        r"(?:Melee|Ranged|Melee or Ranged)\s+(?:Weapon|Spell)\s+Attack:)",
        normalized_details,
    )
    if inline_actions is not None:
        section_parts["ACTIONS"].insert(
            0, normalized_details[inline_actions.end() :].strip()
        )
        normalized_details = normalized_details[: inline_actions.start()].strip()
    fields, traits = _split_statblock_details(normalized_details)
    parry_boundary = re.search(r"(?i)(?<![A-Za-z])Parry\.\s+", traits)
    if parry_boundary is not None:
        parry_settlement = parry_reaction_settlement(
            traits[parry_boundary.end() :]
        )
        if parry_settlement is not None:
            traits = traits[: parry_boundary.start()].strip()
            section_parts["REACTIONS"].append(
                f"Parry. {parry_settlement[1]}"
            )
    # Some two-column PDFs flatten the decorated Actions divider into the
    # final trait column.  Recover only the distinctive OCR form so ordinary
    # prose that happens to mention actions cannot move between sections.
    actions_boundary = re.search(
        r"(?i)(?<![A-Za-z])A\s+ctions\s+_{4,}\s*",
        traits,
    )
    if actions_boundary is not None:
        action_tail = traits[actions_boundary.end() :].strip()
        traits = traits[: actions_boundary.start()].strip()
        if action_tail:
            section_parts["ACTIONS"].insert(0, action_tail)
    rendered = [
        f"# {name}",
        "",
        f"*{_normalize_statblock_ocr(core.group('identity').strip())}*",
        "",
        f"**Armor Class** {core.group('armor').strip()}",
        f"**Hit Points** {_normalize_statblock_ocr(core.group('hit_points').strip())}",
        f"**Speed** {core.group('speed').strip()}",
        "",
        "| STR | DEX | CON | INT | WIS | CHA |",
        "|---:|---:|---:|---:|---:|---:|",
        "| " + " | ".join(ability_values[label] for label in ABILITY_LABELS) + " |",
    ]
    for label in _STATBLOCK_FIELD_LABELS:
        if fields.get(label):
            rendered.append(f"**{label}** {fields[label]}")
    if traits:
        rendered.extend(
            ("", "## Traits", "", _mark_statblock_entries(_normalize_statblock_ocr(traits)))
        )
    for section, parts in section_parts.items():
        content = _trim_trailing_statblock_lore(
            " ".join(parts).strip(),
            creature_name=name,
        )
        if content:
            rendered.extend(
                (
                    "",
                    f"## {section.title()}",
                    "",
                    _mark_statblock_entries(_normalize_statblock_ocr(content)),
                )
            )
    return "\n".join(rendered).strip() + "\n"


def _trim_trailing_statblock_lore(
    content: str,
    *,
    creature_name: str = "",
) -> str:
    """Exclude adjacent creature lore from the final attack entry.

    Module appendices sometimes place general creature lore immediately after
    the last statblock action without a heading boundary.  Trim only a
    conservative completed mechanical clause followed by recognizable ancestry
    or habitat prose.  Mechanical continuations such as "The target is
    restrained" therefore remain part of the action.
    """

    canonical_name = " ".join(str(creature_name).split())
    plural_name = (
        rf"{re.escape(canonical_name)}(?:s|es)"
        if canonical_name
        else r"(?!x)x"
    )
    named_lore_boundary = re.search(
        rf"(?is)\bdamage\.\s+(?={plural_name}\s+(?:are|were|have|often|typically)\b)",
        content,
    )
    if named_lore_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: named_lore_boundary.start()]
    ) is not None:
        return content[: named_lore_boundary.start() + len("damage.")].rstrip()
    boundary = re.search(
        r"(?is)\bdamage\.\s+"
        r"(?=[A-Z][A-Za-z'’/-]*(?:\s+\([^.\n]{1,80}\))?"
        r"\s+(?:are|is)\s+(?:a|an|the)\b)",
        content,
    )
    if boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: boundary.start()]
    ) is not None:
        return content[: boundary.start() + len("damage.")].rstrip()
    inclusion_boundary = re.search(
        r"(?is)\bdamage\.\s+(?=[A-Z][A-Za-z'鈥?-]+s?\s+include\s+"
        r"(?:members|creatures|people|warriors|servants)\b)",
        content,
    )
    if inclusion_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: inclusion_boundary.start()]
    ) is not None:
        return content[: inclusion_boundary.start() + len("damage.")].rstrip()
    habitat_boundary = re.search(
        r"(?is)\bdamage\)\.\s+(?=Usually found\b)",
        content,
    )
    if habitat_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: habitat_boundary.start()]
    ) is not None:
        return content[: habitat_boundary.start() + len("damage).")].rstrip()
    origin_boundary = re.search(
        r"(?is)\bdamage\.\s+(?=Ha\s+iling\s+from\s+uncivilized\s+lands\b)",
        content,
    )
    if origin_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: origin_boundary.start()]
    ) is not None:
        return content[: origin_boundary.start() + len("damage.")].rstrip()
    return content


def _normalize_statblock_ocr(content: str) -> str:
    """Repair only context-bounded, unambiguous statblock OCR tokens."""

    def normalize(match: re.Match[str]) -> str:
        token = match.group(0)
        sides = token[2:].replace("l", "1").replace("I", "1")
        if not sides.isdigit() or int(sides) < 2:
            return token
        return f"1d{sides}"

    normalized = re.sub(
        r"(?<![A-Za-z0-9])[1lI]d[0-9lI]+(?![A-Za-z0-9])",
        normalize,
        content,
    )
    normalized = re.sub(r"\s+([.,;:])", r"\1", normalized)
    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])(\d+)dS(?![A-Za-z0-9])",
        r"\1d8",
        normalized,
    )
    normalized = re.sub(
        r"(?i)^((?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+"
        r"[A-Za-z][A-Za-z0-9 '/()\-]{0,120})\.\s*"
        r"((?:(?:lawful|neutral|chaotic)\s+(?:good|neutral|evil))|neutral|"
        r"unaligned|any(?:\s+[A-Za-z-]+){0,4}\s+alignment)$",
        r"\1, \2",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])(Challenge\s+)[lI](?=\s*(?:\(|$))",
        r"\g<1>1",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\brange\s+(\d+)\s*f\s*(\d+)\s*ft\.",
        r"range \1/\2 ft.",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\branged\s+(\d+)\s*ft\.?\s*/\s*(\d+)\s*ft\.",
        r"range \1/\2 ft.",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\([^){}\n]{1,60})\}\."
        r"(?=\s+(?:Melee|Ranged|Melee or Ranged)\s+"
        r"(?:Weapon|Spell)\s+Attack:)",
        r"\1).",
        normalized,
    )
    normalized = re.sub(
        r"\b([A-Z])\s+([a-z][A-Za-z'-]+)"
        r"(?=\.\s+(?i:Melee|Ranged|Melee or Ranged)\s+"
        r"(?i:Weapon|Spell)\s+Attack:)",
        lambda match: f"{match.group(1)}{match.group(2)}",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bM\s+elee(?=\s+Weapon\s+Attack\b)",
        "Melee",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bR\s+anged(?=\s+Weapon\s+Attack\b)",
        "Ranged",
        normalized,
    )
    for pattern, replacement in (
        (r"(?i)\bray\s+offrost\b", "ray of frost"),
        (r"(?i)\binvisihility\b", "invisibility"),
        (r"(?i)\bfaeriefire\b", "faerie fire"),
    ):
        normalized = re.sub(pattern, replacement, normalized)
    normalized = re.sub(
        r"(?i)\b(\d+)(st|nd|rd|th)[\u00b7\u2022]\s*level\b",
        r"\1\2-level",
        normalized,
    )
    return re.sub(
        r"(?i)(?<![A-Za-z0-9])[lI]\s+st\s+level"
        r"(?=\s*\(\s*\d+\s+slots?\s*\)\s*:)",
        "1st level",
        normalized,
    )


def _split_statblock_details(content: str) -> tuple[dict[str, str], str]:
    value_starts = {
        "Saving Throws": (
            r"(?:Str|Dex|Con|Int|Wis|Cha)\s*[+\-鈭抅]\s*\d"
        ),
        "Skills": r"[A-Za-z][A-Za-z ]{1,40}\s*[+\-鈭抅]\s*\d",
        "Damage Vulnerabilities": (
            r"(?:acid|bludgeoning|cold|fire|force|lightning|necrotic|"
            r"piercing|poison|psychic|radiant|slashing|thunder)\b"
        ),
        "Damage Resistances": (
            r"(?:acid|bludgeoning|cold|fire|force|lightning|necrotic|"
            r"piercing|poison|psychic|radiant|slashing|thunder)\b"
        ),
        "Damage Immunities": (
            r"(?:acid|bludgeoning|cold|fire|force|lightning|necrotic|"
            r"piercing|poison|psychic|radiant|slashing|thunder)\b"
        ),
        "Condition Immunities": (
            r"(?:blinded|charmed|deafened|exhaustion|frightened|grappled|"
            r"incapacitated|invisible|paralyzed|petrified|poisoned|prone|"
            r"restrained|stunned|unconscious)\b"
        ),
        "Senses": (
            r"(?:blindsight|darkvision|passive\s+Perception|tremorsense|"
            r"truesight)\b"
        ),
        "Languages": (
            r"(?:[-—]+|all\b|any\b|understands\b|telepathy\b|"
            r"(?!it\b|the\b|this\b|that\b)[A-Z][A-Za-z' -]{1,40})"
        ),
        "Challenge": r"(?:\d+(?:/\d+)?|—|-)(?=\s|$)",
    }
    matches: list[tuple[int, int, str]] = []
    for label in _STATBLOCK_FIELD_LABELS:
        match = next(
            (
                candidate
                for candidate in re.finditer(
                    rf"(?i)(?<!\w){re.escape(label)}\s+",
                    content,
                )
                if re.match(
                    rf"(?i){value_starts[label]}",
                    content[candidate.end() :],
                )
            ),
            None,
        )
        if match:
            matches.append((match.start(), match.end(), label))
    matches.sort()
    fields: dict[str, str] = {}
    traits = content[: matches[0][0]].strip() if matches else content.strip()
    for index, (start, end, label) in enumerate(matches):
        next_start = matches[index + 1][0] if index + 1 < len(matches) else len(content)
        value = content[end:next_start].strip()
        if label == "Challenge":
            challenge = re.match(r"([^\s(]+(?:\s*\([\d,]+\s+XP\))?)(?P<tail>.*)", value, re.S)
            if challenge:
                fields[label] = challenge.group(1).strip()
                tail = challenge.group("tail").strip()
                traits = " ".join(part for part in (traits, tail) if part).strip()
                continue
        trait_boundary = re.search(
            r"(?<![A-Za-z])"
            r"(?P<name>[A-Z][A-Za-z'鈥?-]+(?:\s+(?:[A-Z][A-Za-z'鈥?-]+|"
            r"of|the|and|or)){0,7})\.\s+(?=[A-Z])",
            value,
        )
        if trait_boundary is not None:
            tail = value[trait_boundary.start() :].strip()
            value = value[: trait_boundary.start()].strip()
            traits = " ".join(part for part in (traits, tail) if part).strip()
        fields[label] = value
    return fields, traits


def _mark_statblock_entries(content: str) -> str:
    mechanically_marked = _MECHANICAL_ENTRY_START_RE.sub(
        lambda match: (
            f"{match.group('prefix')}***{match.group('name').strip()}***. "
        ),
        content,
    )
    return _ENTRY_START_RE.sub(
        lambda match: (
            f"{match.group('prefix')}***{match.group('name').strip()}***. "
        ),
        mechanically_marked,
    )


def _minimum_page_values(values: Any) -> int | None:
    pages = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return min(pages) if pages else None


def _maximum_page_values(values: Any) -> int | None:
    pages = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return max(pages) if pages else None


def compiled_artifacts_from_candidates(
    candidates: list[dict[str, Any]],
    *,
    pack_id: str,
    require_review_contracts: bool = False,
) -> list[dict[str, Any]]:
    """Turn DM-approved candidates into source-bound pack artifacts.

    `catalog_only` is intentional: it gives the agent searchable source-linked
    content without permitting an incomplete parse to alter a character sheet.
    A reviewed artifact must explicitly opt into `selection_ready`.
    """
    accepted = [
        candidate
        for candidate in candidates
        if candidate.get("review_status") == "accepted"
    ]
    generated_bases: dict[str, int] = {}
    for candidate in accepted:
        value = dict(candidate.get("artifact") or {})
        if str(value.get("id") or "").strip():
            continue
        kind = str(value.get("kind") or candidate.get("kind") or "").strip()
        card = dict(value.get("card") or {})
        name = str(card.get("name") or candidate.get("name") or "").strip()
        if kind and name:
            base = _artifact_id(pack_id, kind, name)
            generated_bases[base] = generated_bases.get(base, 0) + 1

    artifacts: list[dict[str, Any]] = []
    ids: set[str] = set()
    for candidate in accepted:
        value = deepcopy(dict(candidate.get("artifact") or {}))
        reviewed_catalog = value.pop("catalog_review", None)
        reviewed_selection = value.pop("selection_contract", None)
        if require_review_contracts and (
            not isinstance(reviewed_catalog, dict)
            or reviewed_catalog.get("status") != "approved"
        ):
            raise ValueError(
                f"accepted candidate {candidate.get('id')} needs an approved "
                "catalog review"
            )
        if require_review_contracts and not isinstance(reviewed_selection, dict):
            raise ValueError(
                f"accepted candidate {candidate.get('id')} needs a selection contract"
            )
        if (reviewed_catalog is None) != (reviewed_selection is None):
            raise ValueError(
                f"accepted candidate {candidate.get('id')} must keep catalog and "
                "selection attestations together"
            )
        kind = str(value.get("kind") or candidate.get("kind") or "").strip()
        card = dict(value.get("card") or {})
        name = str(card.get("name") or candidate.get("name") or "").strip()
        if not kind or not name:
            raise ValueError(f"accepted candidate {candidate.get('id')} needs kind and card.name")
        explicit_artifact_id = str(value.get("id") or "").strip()
        base_artifact_id = _artifact_id(pack_id, kind, name)
        artifact_id = explicit_artifact_id or base_artifact_id
        if not explicit_artifact_id and generated_bases.get(base_artifact_id, 0) > 1:
            artifact_id = (
                f"{base_artifact_id}-{_candidate_artifact_disambiguator(candidate)}"
            )
        if artifact_id in ids:
            collision_kind = "explicit" if explicit_artifact_id else "generated"
            raise ValueError(f"duplicate {collision_kind} artifact id: {artifact_id}")
        ids.add(artifact_id)
        chunk_ids = [str(item) for item in candidate.get("source_chunk_ids") or [] if str(item)]
        if not chunk_ids:
            raise ValueError(f"accepted candidate {candidate.get('id')} needs source_chunk_ids")
        state = str(
            value.get("application_state") or candidate.get("application_state") or "catalog_only"
        )
        if state not in {"catalog_only", "selection_ready"}:
            raise ValueError("application_state must be catalog_only or selection_ready")
        if kind == "spell" and card.get("resolution") is not None:
            card["resolution"] = normalize_spell_resolution(
                card["resolution"], f"candidate {candidate.get('id')} spell.resolution"
            )
            mechanic_refs = list(
                dict.fromkeys(
                    [
                        *list(value.get("mechanic_refs") or []),
                        *list(card.get("mechanic_refs") or []),
                        SPELL_RESOLUTION_MECHANIC_ID,
                    ]
                )
            )
            value["mechanic_refs"] = mechanic_refs
            card["mechanic_refs"] = mechanic_refs
        raw_plan = value.get("resolution_plan", card.get("resolution_plan"))
        raw_plans = value.get("resolution_plans", card.get("resolution_plans"))
        if raw_plan is not None and raw_plans is not None:
            raise ValueError(
                f"candidate {candidate.get('id')} cannot combine resolution_plan "
                "and resolution_plans"
            )
        if raw_plans is not None and (
            not isinstance(raw_plans, list) or not raw_plans
        ):
            raise ValueError(
                f"candidate {candidate.get('id')} resolution_plans must be "
                "a non-empty list"
            )
        plan_values = (
            [raw_plan]
            if raw_plan is not None
            else list(raw_plans or [])
        )
        compiled_plans = []
        for plan_value in plan_values:
            try:
                compiled_plan = compile_resolution_plan(plan_value)
            except ResolutionPlanCompilationError as error:
                raise ValueError(
                    f"candidate {candidate.get('id')} resolution_plan: {error}"
                ) from error
            if compiled_plan.source_card_id != artifact_id:
                raise ValueError(
                    f"candidate {candidate.get('id')} resolution_plan source_card_id "
                    "must match its artifact id"
                )
            _require_candidate_plan_evidence(
                compiled_plan.citations,
                candidate=candidate,
            )
            compiled_plans.append(compiled_plan)
        plan_ids = [plan.id for plan in compiled_plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError(
                f"candidate {candidate.get('id')} resolution plan ids must be unique"
            )
        if compiled_plans:
            stored_plans = [
                resolution_plan_template(plan) for plan in compiled_plans
            ]
            if raw_plan is not None:
                value["resolution_plan"] = stored_plans[0]
            else:
                value["resolution_plans"] = stored_plans
            card.pop("resolution_plan", None)
            card.pop("resolution_plans", None)
            mechanic_refs = list(
                dict.fromkeys(
                    [
                        *list(value.get("mechanic_refs") or []),
                        *list(card.get("mechanic_refs") or []),
                        *plan_ids,
                    ]
                )
            )
            value["mechanic_refs"] = mechanic_refs
            card["mechanic_refs"] = mechanic_refs
            value["embedded_mechanic_refs"] = list(
                dict.fromkeys(
                    [
                        *list(value.get("embedded_mechanic_refs") or []),
                        *plan_ids,
                    ]
                )
            )
            value["execution_state"] = "plan_ready"
        raw_clauses = value.get("rule_clauses", card.get("rule_clauses"))
        compiled_clauses = ()
        if raw_clauses is not None:
            try:
                compiled_clauses = compile_rule_clauses(raw_clauses)
            except RuleContractError as error:
                raise ValueError(
                    f"candidate {candidate.get('id')} rule_clauses: {error}"
                ) from error
            for clause in compiled_clauses:
                _require_candidate_clause_evidence(
                    clause.source_citations,
                    candidate=candidate,
                    clause_id=clause.id,
                )
            value["rule_clauses"] = rule_clause_templates(compiled_clauses)
            card.pop("rule_clauses", None)
        mechanical_scope = str(
            value.get("mechanical_scope")
            or candidate.get("mechanical_scope")
            or "review_required"
        )
        if mechanical_scope not in {"descriptive", "mechanical", "review_required"}:
            raise ValueError(
                f"candidate {candidate.get('id')} mechanical_scope is invalid"
            )
        artifact_for_coverage = {
            **value,
            "id": artifact_id,
            "kind": kind,
            "card": card,
        }
        if compiled_clauses:
            coverage_errors = validate_rule_clause_coverage(
                compiled_clauses,
                artifact=artifact_for_coverage,
                plan_ids=set(plan_ids),
                mechanic_refs=set(value.get("mechanic_refs") or []) - set(plan_ids),
                require_mechanical_clause=mechanical_scope == "mechanical",
            )
            if coverage_errors:
                raise ValueError(
                    f"candidate {candidate.get('id')} rule clause coverage: "
                    + "; ".join(coverage_errors)
                )
            clause_modes = {
                str(clause.settlement["mode"])
                for clause in compiled_clauses
            }
            value["execution_state"] = (
                "ruling_ready"
                if clause_modes == {"agent_ruling"}
                else "descriptive_ready"
                if clause_modes == {"descriptive"}
                else "clause_ready"
            )
        elif (
            state == "selection_ready"
            and mechanical_scope == "mechanical"
            and not compiled_plans
            and not (kind == "spell" and card.get("resolution") is not None)
        ):
            raise ValueError(
                f"candidate {candidate.get('id')} mechanical content needs a "
                "rule_clauses or resolution_plan before it becomes selection_ready"
            )
        artifact = {
            **value,
            "id": artifact_id,
            "kind": kind,
            "card": card,
            "application_state": state,
            "mechanical_scope": mechanical_scope,
            "source_chunk_ids": chunk_ids,
        }
        if isinstance(reviewed_catalog, dict) and isinstance(
            reviewed_selection, dict
        ):
            selection_status = str(reviewed_selection.get("status") or "")
            artifact["selection_contract"] = build_selection_contract(
                artifact,
                status=selection_status,
                references=list(reviewed_selection.get("references") or []),
                blockers=list(reviewed_selection.get("blockers") or []),
            )
            artifact["catalog_review"] = build_catalog_review(
                artifact,
                decisions=list(reviewed_catalog.get("decisions") or []),
                status="approved",
            )
        artifacts.append(artifact)
    return artifacts


def _selection_schema_reference(
    *,
    kind: str,
    name: str,
    card: Mapping[str, Any],
    reference_artifacts: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Select one exact or bounded-OCR reference without applying aliases."""

    target = _canonical_source_heading(name)
    if not target:
        return None
    matches: list[tuple[int, int, str, Mapping[str, Any]]] = []

    def unresolved_priority(artifact: Mapping[str, Any]) -> int:
        artifact_card = artifact.get("card")
        grants = (
            artifact_card.get("grants")
            if isinstance(artifact_card, Mapping)
            else None
        )
        return 1 if isinstance(grants, Mapping) and grants.get("unresolved") else 0
    # A generic catalog name may repair one OCR glyph, but broader edits can
    # turn one real spell into another (Blinding/Branding Smite, for example).
    # Wider matching is reserved for the explicit printed-name aliases below.
    maximum = 1
    alias_maximum = max(1, min(3, (len(target) + 5) // 6))
    for artifact in reference_artifacts:
        if str(artifact.get("kind") or "") != kind:
            continue
        reference_card = artifact.get("card")
        if not isinstance(reference_card, Mapping):
            continue
        reference_name = " ".join(str(reference_card.get("name") or "").split())
        reference_key = _canonical_source_heading(reference_name)
        source_names = {
            _canonical_source_heading(str(item)): " ".join(str(item).split())
            for item in artifact.get("_selection_source_names") or []
            if str(item).strip()
        }
        source_scores = [
            (
                _bounded_ocr_edit_distance(target, source_name, alias_maximum),
                rendered_name,
            )
            for source_name, rendered_name in source_names.items()
            if abs(len(target) - len(source_name)) <= alias_maximum
        ]
        source_scores.sort()
        if source_scores and source_scores[0][0] <= alias_maximum:
            matched_artifact = dict(artifact)
            matched_artifact["_selection_matched_source_name"] = source_scores[0][1]
            matches.append(
                (
                    source_scores[0][0],
                    unresolved_priority(matched_artifact),
                    reference_name.casefold(),
                    matched_artifact,
                )
            )
            continue
        imported_definition = card.get("definition")
        reference_definition = reference_card.get("definition")
        imported_level = card.get("level")
        reference_level = reference_card.get("level")
        imported_school = (
            str(imported_definition.get("school") or "").casefold()
            if isinstance(imported_definition, Mapping)
            else ""
        )
        reference_school = (
            str(reference_definition.get("school") or "").casefold()
            if isinstance(reference_definition, Mapping)
            else ""
        )
        typed_suffix_match = (
            kind == "spell"
            and isinstance(imported_level, int)
            and not isinstance(imported_level, bool)
            and imported_level == reference_level
            and bool(imported_school)
            and imported_school == reference_school
            and bool(reference_key)
            and (reference_key.endswith(target) or target.endswith(reference_key))
        )
        if typed_suffix_match:
            imported_classes = {
                str(item).casefold() for item in card.get("classes") or [] if str(item)
            }
            reference_classes = {
                str(item).casefold()
                for item in reference_card.get("classes") or []
                if str(item)
            }
            if not imported_classes or not reference_classes or (
                imported_classes & reference_classes
            ):
                matched_artifact = dict(artifact)
                default_source_name = " ".join(
                    str(artifact.get("_selection_default_source_name") or "").split()
                )
                if default_source_name:
                    matched_artifact["_selection_matched_source_name"] = (
                        default_source_name
                    )
                matches.append(
                    (
                        alias_maximum + 1,
                        unresolved_priority(matched_artifact),
                        reference_name.casefold(),
                        matched_artifact,
                    )
                )
                continue
        if not reference_key or abs(len(target) - len(reference_key)) > maximum:
            continue
        score = _bounded_ocr_edit_distance(target, reference_key, maximum)
        if score <= maximum:
            matched_artifact = dict(artifact)
            default_source_name = " ".join(
                str(artifact.get("_selection_default_source_name") or "").split()
            )
            if default_source_name:
                matched_artifact["_selection_matched_source_name"] = (
                    default_source_name
                )
            matches.append(
                (
                    score,
                    unresolved_priority(matched_artifact),
                    reference_name.casefold(),
                    matched_artifact,
                )
            )
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    if not matches or (
        len(matches) > 1
        and matches[0][:2] == matches[1][:2]
        and matches[0][2] != matches[1][2]
    ):
        return None
    return matches[0][3]


def _hydrate_spell_selection_schema(
    value: dict[str, Any],
    card: dict[str, Any],
    *,
    reference: Mapping[str, Any],
) -> None:
    """Fill typed 2014 spell fields while retaining imported source prose."""

    reference_card = reference.get("card")
    if not isinstance(reference_card, Mapping):
        return
    reference_name = " ".join(str(reference_card.get("name") or "").split())
    matched_source_name = " ".join(
        str(reference.get("_selection_matched_source_name") or "").split()
    )
    if matched_source_name:
        card["name"] = matched_source_name
    elif reference_name and reference.get("_preserve_imported_name") is not True:
        card["name"] = reference_name
    reference_level = reference_card.get("level")
    if (
        not isinstance(card.get("level"), int)
        or isinstance(card.get("level"), bool)
    ) and isinstance(reference_level, int) and not isinstance(reference_level, bool):
        card["level"] = reference_level
    current_classes = [
        str(item).strip().title()
        for item in card.get("classes") or []
        if str(item).strip()
    ]
    reference_classes = [
        str(item).strip().title()
        for item in reference_card.get("classes") or []
        if str(item).strip()
    ]
    card["classes"] = list(dict.fromkeys([*current_classes, *reference_classes]))
    definition = card.get("definition")
    if not isinstance(definition, dict):
        reference_definition = reference_card.get("definition")
        if isinstance(reference_definition, Mapping):
            definition = deepcopy(dict(reference_definition))
            description = " ".join(str(card.get("description") or "").split())
            if description:
                definition["effect"] = description[:4000]
            card["definition"] = definition
    reference_resolution = reference_card.get("resolution")
    if card.get("resolution") is None and isinstance(reference_resolution, Mapping):
        # The imported prose and citations remain source-local, but a trusted
        # official dependency owns the standard executable settlement. Reuse
        # that reviewed contract instead of demoting a standard spell to a
        # generic Agent ruling merely because it was selected from an imported
        # printing of the same rule.
        card["resolution"] = deepcopy(dict(reference_resolution))
    reference_mechanic_refs = [
        str(item)
        for item in (
            reference.get("mechanic_refs")
            or reference_card.get("mechanic_refs")
            or []
        )
        if str(item)
    ]
    if reference_mechanic_refs:
        mechanic_refs = list(
            dict.fromkeys(
                [
                    *[str(item) for item in value.get("mechanic_refs") or [] if str(item)],
                    *reference_mechanic_refs,
                ]
            )
        )
        value["mechanic_refs"] = mechanic_refs
        card["mechanic_refs"] = list(
            dict.fromkeys(
                [
                    *[str(item) for item in card.get("mechanic_refs") or [] if str(item)],
                    *reference_mechanic_refs,
                ]
            )
        )
    reference_identity = reference.get("_selection_schema_reference")
    if isinstance(reference_identity, Mapping):
        existing = list(value.get("selection_schema_references") or [])
        identity = {
            key: str(reference_identity.get(key) or "")
            for key in ("pack_id", "pack_version", "artifact_id")
        }
        if all(identity.values()) and identity not in existing:
            value["selection_schema_references"] = [*existing, identity]


def _hydrate_character_selection_schema(
    value: dict[str, Any],
    card: dict[str, Any],
    *,
    kind: str,
    reference: Mapping[str, Any],
) -> None:
    """Reuse trusted typed grants while preserving the imported source prose."""

    reference_card = reference.get("card")
    if not isinstance(reference_card, Mapping):
        return
    reference_name = " ".join(str(reference_card.get("name") or "").split())
    if reference_name:
        card["name"] = reference_name
    fields_by_kind = {
        "background": ("skill_proficiencies", "background_grants"),
        "class": ("class_definition",),
        "feat": (
            "prerequisites",
            "repeatable",
            "selection_requirements",
            "mechanical_grants",
        ),
        "item": ("inventory_template",),
        "species": ("base_species", "grants"),
        "subclass": (
            "class_name",
            "minimum_level",
            "always_prepared_spells",
            "spell_grants",
        ),
    }
    for field in fields_by_kind.get(kind, ()):
        if field in reference_card:
            card[field] = deepcopy(reference_card[field])
    reference_identity = reference.get("_selection_schema_reference")
    if isinstance(reference_identity, Mapping):
        identity = {
            key: str(reference_identity.get(key) or "")
            for key in ("pack_id", "pack_version", "artifact_id")
        }
        existing = list(value.get("selection_schema_references") or [])
        if all(identity.values()) and identity not in existing:
            value["selection_schema_references"] = [*existing, identity]


def _record_selection_schema_reference(
    value: dict[str, Any], reference: Mapping[str, Any]
) -> None:
    reference_identity = reference.get("_selection_schema_reference")
    if not isinstance(reference_identity, Mapping):
        return
    identity = {
        key: str(reference_identity.get(key) or "")
        for key in ("pack_id", "pack_version", "artifact_id")
    }
    existing = list(value.get("selection_schema_references") or [])
    if all(identity.values()) and identity not in existing:
        value["selection_schema_references"] = [*existing, identity]


def _merge_species_grants(
    base: Mapping[str, Any],
    specific: Mapping[str, Any],
    *,
    replaces_base_ability_scores: bool = False,
) -> dict[str, Any]:
    """Combine one trusted base species with source-local variant grants.

    Imported subrace evidence is not guaranteed to repeat every base trait.  It
    is also common for a PDF chunk to repeat some, but not all, base traits.
    This merger therefore treats fixed base facts as defaults and unions
    additive grants without double-counting repeated evidence.
    """

    merged = deepcopy(dict(base))
    specific_value = deepcopy(dict(specific))
    base_increases = dict(base.get("ability_score_increases") or {})
    specific_increases = dict(specific_value.get("ability_score_increases") or {})
    merged["ability_score_increases"] = (
        specific_increases
        if replaces_base_ability_scores
        else {**base_increases, **specific_increases}
    )
    specific_ability_choice = dict(specific_value.get("ability_choice") or {})
    if replaces_base_ability_scores or int(specific_ability_choice.get("count", 0) or 0):
        merged["ability_choice"] = specific_ability_choice

    list_fields = (
        "armor_proficiencies",
        "condition_immunities",
        "immunities",
        "language_options",
        "languages",
        "narrative_choice_groups",
        "proficiency_choice_groups",
        "resistances",
        "skill_options",
        "skill_proficiencies",
        "spell_grants",
        "spell_list_expansion",
        "tool_choices",
        "tool_expertise_options",
        "tool_options",
        "tool_proficiencies",
        "unresolved",
        "weapon_proficiencies",
    )
    for field in list_fields:
        base_values = list(base.get(field) or [])
        specific_values = list(specific_value.get(field) or [])
        combined: list[Any] = []
        seen: set[str] = set()
        for item in [*base_values, *specific_values]:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            combined.append(deepcopy(item))
        merged[field] = combined

    base_features = [deepcopy(dict(item)) for item in base.get("features") or []]
    feature_keys = {
        _canonical_source_heading(str(item.get("name") or item.get("id") or ""))
        for item in base_features
    }
    for raw_feature in specific_value.get("features") or []:
        feature = deepcopy(dict(raw_feature))
        key = _canonical_source_heading(
            str(feature.get("name") or feature.get("id") or "")
        )
        if key and key in feature_keys:
            continue
        base_features.append(feature)
        if key:
            feature_keys.add(key)
    merged["features"] = base_features

    for field in (
        "darkvision_ft",
        "fly_speed",
        "hp_per_level",
        "language_choice_count",
        "natural_armor_base",
        "skill_choice_count",
        "swim_speed",
        "tool_choice_count",
        "tool_expertise_choice_count",
        "walk_speed",
    ):
        specific_number = specific_value.get(field)
        if isinstance(specific_number, int) and not isinstance(specific_number, bool):
            if specific_number > 0:
                merged[field] = specific_number
    for field in ("size",):
        specific_text = str(specific_value.get(field) or "").strip()
        if specific_text:
            merged[field] = specific_text
    for field in ("allow_any_language", "allow_any_skill", "allow_any_proficient_tool_expertise"):
        merged[field] = bool(base.get(field)) or bool(specific_value.get(field))
    for field in ("cantrip_choice", "feat_choice"):
        specific_mapping = specific_value.get(field)
        if isinstance(specific_mapping, Mapping) and specific_mapping:
            merged[field] = deepcopy(dict(specific_mapping))
    merged["resources"] = {
        **deepcopy(dict(base.get("resources") or {})),
        **deepcopy(dict(specific_value.get("resources") or {})),
    }
    for field, field_value in specific_value.items():
        if field not in merged:
            merged[field] = deepcopy(field_value)
    return merged


def author_selection_card_from_candidate(
    candidate: dict[str, Any],
    *,
    source_chunks_by_id: dict[str, str] | None = None,
    reference_artifacts: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Prepare a conservative typed card for the primary build-time reviewer.

    This is an authoring aid, not an approval.  The returned artifact is still
    bound to the primary review and an independent critic/DM review before it
    can enter a portable catalog.  It promotes only fields that the existing
    character materializers can validate; source-specific effects remain on
    the source-bound Agent ruling clause added later in the review pipeline.
    """

    value = deepcopy(dict(candidate.get("artifact") or {}))
    kind = str(value.get("kind") or candidate.get("kind") or "").strip()
    card = deepcopy(dict(value.get("card") or {}))
    name = " ".join(str(card.get("name") or candidate.get("name") or "").split())
    description = " ".join(str(card.get("description") or "").split())
    if not kind or not name:
        return value
    card["name"] = name
    value["kind"] = kind
    value["card"] = card
    selection_applicability = str(value.get("selection_applicability") or "")
    if selection_applicability == "not_applicable":
        # A reviewer may deliberately retain a rule tip, parent heading, table,
        # or other contextual feature in the catalog.  Never undo that explicit
        # boundary merely because the storage kind has a character materializer.
        value["application_state"] = "catalog_only"
        return value
    if value.get("application_state") == "selection_ready" and kind != "spell":
        return value
    if card.get("source_fragment") is True:
        # Coverage fallbacks are runtime context for the Agent-as-DM. They are
        # not character options merely because their storage kind is feature.
        value["selection_applicability"] = "not_applicable"
        value["application_state"] = "catalog_only"
        return value

    species_base_reference: Mapping[str, Any] | None = None
    if kind in {"background", "class", "feat", "item", "species", "subclass"}:
        reference = _selection_schema_reference(
            kind=kind,
            name=name,
            card=card,
            reference_artifacts=reference_artifacts,
        )
        if reference is not None:
            _hydrate_character_selection_schema(
                value,
                card,
                kind=kind,
                reference=reference,
            )
        elif kind == "species":
            base_species = " ".join(str(card.get("base_species") or "").split())
            if base_species and _canonical_source_heading(base_species) != (
                _canonical_source_heading(name)
            ):
                species_base_reference = _selection_schema_reference(
                    kind="species",
                    name=base_species,
                    card=card,
                    reference_artifacts=reference_artifacts,
                )

    if kind == "statblock":
        reviewed_template = dict(card.get("dependent_actor_template") or {})
        reviewed_owner_class_name = " ".join(
            str(
                card.pop("owner_class_name", None)
                or reviewed_template.get("owner_class_name")
                or ""
            ).split()
        )
        source_text = str(card.get("normalized_content") or "").strip()
        if not source_text and source_chunks_by_id:
            source_text = "\n\n".join(
                str(source_chunks_by_id.get(str(chunk_id)) or "").strip()
                for chunk_id in candidate.get("source_chunk_ids") or []
                if str(source_chunks_by_id.get(str(chunk_id)) or "").strip()
            )
            if source_text:
                source_text = f"# {name}\n\n{source_text}"
        requirement = parameterized_statblock_requirements(source_text)
        if requirement is not None:
            if reviewed_owner_class_name:
                requirement["owner_class_name"] = reviewed_owner_class_name
            solution = dict(requirement.get("solution") or {})
            if (
                "owner_class_level" in set(solution.get("numeric_parameters") or [])
                and not solution.get("owner_class_names")
            ):
                owner_class_name = _candidate_class_name(candidate, source_text)
                if owner_class_name:
                    requirement["owner_class_name"] = owner_class_name
            card["normalized_content"] = source_text
            card["dependent_actor_template"] = requirement
            value["selection_applicability"] = "not_applicable"
            value["application_state"] = "catalog_only"
        return value

    if kind == "spell":
        reference = _selection_schema_reference(
            kind=kind,
            name=name,
            card=card,
            reference_artifacts=reference_artifacts,
        )
        if reference is not None:
            _hydrate_spell_selection_schema(value, card, reference=reference)
        classes = [
            str(item).strip().title()
            for item in card.get("classes") or []
            if str(item).strip()
        ]
        definition = card.get("definition")
        level = card.get("level")
        if (
            classes
            and isinstance(level, int)
            and not isinstance(level, bool)
            and 0 <= level <= 9
            and isinstance(definition, dict)
        ):
            normalize_spell_definition(definition, "candidate spell.definition")
            card["classes"] = list(dict.fromkeys(classes))
            value["application_state"] = "selection_ready"
        return value

    if kind == "class":
        if isinstance(card.get("class_definition"), dict):
            try:
                selection_schema_for_artifact({"kind": kind, "card": card})
            except ValueError:
                pass
            else:
                value["application_state"] = "selection_ready"
                return value
        definition = _class_selection_definition(description)
        if definition is not None:
            card.setdefault("class_definition", definition)
            value["application_state"] = "selection_ready"
        return value

    if kind in {"activity", "feat", "feature"}:
        if kind == "feat":
            card.setdefault("prerequisites", [])
            card.setdefault("repeatable", False)
            card.setdefault("selection_requirements", None)
            card.setdefault("mechanical_grants", {})
        elif kind == "feature":
            class_name = _candidate_class_name(candidate, description)
            subclass_name = _candidate_subclass_name(candidate, description)
            minimum_level = _candidate_minimum_level(description)
            if class_name:
                card.setdefault("class_name", class_name)
            if subclass_name:
                card.setdefault("subclass_name", subclass_name)
            if minimum_level is not None:
                card.setdefault("minimum_level", minimum_level)
            explicit_character_option = selection_applicability == "character"
            reviewed_character_binding = any(
                (
                    str(card.get("class_name") or "").strip(),
                    str(card.get("subclass_name") or "").strip(),
                    isinstance(card.get("mechanical_grants"), dict),
                    isinstance(card.get("selection_requirements"), dict),
                    isinstance(card.get("selection_requirements_by_level"), dict),
                )
            )
            if not explicit_character_option and not reviewed_character_binding:
                # Generic headings and source procedures are frequently stored
                # as feature candidates for retrieval.  A name and an Agent
                # ruling clause do not prove that adding the card to a character
                # sheet is meaningful or safe.
                value["selection_applicability"] = "not_applicable"
                value["application_state"] = "catalog_only"
                return value
        value["application_state"] = "selection_ready"
        return value

    if kind == "item":
        item_kind = (
            "magic_item"
            if _ITEM_HEADER_RE.search(
                _normalize_item_header_ocr_text(description[:500])
            )
            else "equipment"
        )
        card.setdefault(
            "inventory_template",
            {
                "name": name,
                "kind": item_kind,
                "quantity": 1,
                "description": description[:1200],
                "attunement": (
                    "required" if "requires attunement" in description.casefold() else "none"
                ),
                "mechanics": {},
            },
        )
        value["application_state"] = "selection_ready"
        return value

    if kind == "background":
        inferred = _background_selection_card(description)
        for field, inferred_value in inferred.items():
            reviewed_value = card.get(field)
            if isinstance(inferred_value, dict) and isinstance(reviewed_value, dict):
                card[field] = _merge_inferred_defaults(
                    inferred_value,
                    reviewed_value,
                )
            else:
                card.setdefault(field, inferred_value)
        value["application_state"] = (
            "selection_ready"
            if not background_materializer_errors(card)
            else "catalog_only"
        )
        return value

    if kind == "species":
        card.setdefault("base_species", name)
        grants = _species_grants(description)
        reviewed_grants = card.get("grants")
        if grants is not None or isinstance(reviewed_grants, dict):
            if isinstance(reviewed_grants, dict):
                card["grants"] = {
                    **deepcopy(grants or {}),
                    **deepcopy(reviewed_grants),
                }
            else:
                assert grants is not None
                card["grants"] = grants
            if species_base_reference is not None:
                reference_card = species_base_reference.get("card")
                base_grants = (
                    reference_card.get("grants")
                    if isinstance(reference_card, Mapping)
                    else None
                )
                if isinstance(base_grants, Mapping):
                    card["grants"] = _merge_species_grants(
                        base_grants,
                        card["grants"],
                        replaces_base_ability_scores=bool(
                            re.search(
                                r"(?i)\breplace\w*\s+the\s+human'?s\s+"
                                r"Ability Score Increase trait\b",
                                _normalize_species_ocr_text(description),
                            )
                        ),
                    )
                    reference_name = " ".join(
                        str(reference_card.get("name") or "").split()
                    )
                    if reference_name:
                        card["base_species"] = reference_name
                    _record_selection_schema_reference(value, species_base_reference)
            value["application_state"] = (
                "selection_ready"
                if not species_materializer_errors(card)
                else "catalog_only"
            )
        return value

    if kind == "subclass":
        class_name = " ".join(str(card.get("class_name") or "").split()) or (
            _candidate_class_name(candidate, description)
        )
        if class_name:
            card["class_name"] = class_name
            card["minimum_level"] = card.get("minimum_level") or (
                _candidate_minimum_level(description)
                or _SUBCLASS_MINIMUM_LEVELS_2014.get(class_name.casefold(), 3)
            )
            card.setdefault("always_prepared_spells", [])
            card.setdefault("spell_grants", [])
            value["application_state"] = "selection_ready"
        return value
    return value


def _merge_inferred_defaults(
    inferred: dict[str, Any],
    reviewed: dict[str, Any],
) -> dict[str, Any]:
    """Fill absent nested fields while preserving explicit reviewer semantics."""

    merged = deepcopy(inferred)
    for field, reviewed_value in reviewed.items():
        inferred_value = merged.get(field)
        if isinstance(inferred_value, dict) and isinstance(reviewed_value, dict):
            merged[field] = _merge_inferred_defaults(inferred_value, reviewed_value)
        else:
            merged[field] = deepcopy(reviewed_value)
    return merged


def _candidate_class_name(candidate: dict[str, Any], description: str) -> str:
    source_class_name = " ".join(
        str(candidate.get("source_class_name") or "").split()
    )
    if source_class_name:
        return source_class_name
    for heading in reversed(candidate.get("source_heading_path") or []):
        parent_class = _SUBCLASS_PARENT_CLASS_NAMES.get(str(heading).casefold().strip())
        if parent_class:
            return parent_class
    values = [
        *[str(item) for item in candidate.get("source_heading_path") or []],
        str(candidate.get("name") or ""),
        description[:1000],
    ]
    combined = " ".join(values).casefold()
    for class_name in sorted(_CLASS_NAMES, key=len, reverse=True):
        if re.search(
            rf"\b{re.escape(class_name)}(?:s|\s+level|\s+class|\b)", combined
        ):
            return class_name.title()
    if "revised ranger" in combined:
        return "Revised Ranger"
    return ""


def _candidate_subclass_name(
    candidate: dict[str, Any],
    description: str,
) -> str:
    match = re.search(
        r"(?i)\b\d{1,2}(?:st|nd|rd|th)\s*[- ]?\s*level\s+"
        r"(?P<owner>[A-Z][A-Za-z'鈥橽 -]{1,70}?)\s+feature\b",
        description[:2400],
    )
    if match is not None:
        owner = _normalize_candidate_display_name(match.group("owner"))
        folded_owner = owner.casefold()
        if folded_owner not in _CLASS_NAMES and folded_owner != "revised ranger":
            return owner
    headings = [
        " ".join(str(value).split())
        for value in candidate.get("source_heading_path") or []
        if str(value).strip()
    ]
    for index, heading in enumerate(headings[:-1]):
        if heading.casefold() in _GENERIC_SUBCLASS_PARENT_TITLES:
            return headings[index + 1]
    return ""


def _candidate_minimum_level(description: str) -> int | None:
    matches = [
        int(match.group(1))
        for match in re.finditer(
            r"(?i)\b(?:at\s*)?(\d{1,2})(?:st|nd|rd|th)\s*[- ]?\s*level\b",
            description,
        )
        if 1 <= int(match.group(1)) <= 20
    ]
    return min(matches) if matches else None


def _class_selection_definition(description: str) -> dict[str, Any] | None:
    description = _normalize_class_ocr_text(description)
    hit_die_match = re.search(
        r"(?i)\bHit\s+Dice?\s*:\s*1?d\s*(6|8|10|12)(?=\s|per\b)",
        description,
    )
    if hit_die_match is None:
        return None
    segments: dict[str, str] = {}
    for label, body in re.findall(
        r"(?is)\b(Armor|Weapons|Tools|Saving\s+Throws|Skills)\s*:\s*(.+?)"
        r"(?=\s+(?:Armor|Weapons|Tools|Saving\s+Throws|Skills)\s*:|"
        r"\s+You\s+start\s+with\b|\s+The\s+\w+\s+Proficiency\b|$)",
        description,
    ):
        segments.setdefault(label.casefold(), body)
    saves_text = segments.get("saving throws", "")
    saving_throws = _fuzzy_catalog_names(saves_text, ABILITY_NAMES)
    skills_text = segments.get("skills", "")
    skill_options = (
        list(SKILL_ABILITIES)
        if re.search(r"(?i)\bChoose\s+any\b", skills_text)
        else _fuzzy_catalog_names(skills_text, tuple(SKILL_ABILITIES))
    )
    choice_match = re.search(
        r"(?i)\bChoose\s+(?:(?P<any>any)\s+)?"
        r"(?P<count>[A-Za-z0-9]{1,5})(?=\s|$|from\b)",
        skills_text,
    )
    skill_choice_count = (
        _ocr_word_number(choice_match.group("count")) if choice_match else 0
    )
    if len(saving_throws) != 2 or not skill_options or not 0 < skill_choice_count <= len(
        skill_options
    ):
        return None

    def proficiencies(label: str) -> list[str]:
        text = segments.get(label, "")
        if not text or text.strip().casefold() == "none":
            return []
        text = re.sub(r"(?i)\blightarmor\b", "light armor", text)
        text = re.sub(r"(?i)\bmediumarmor\b", "medium armor", text)
        text = re.sub(r"(?i)\bheavyarmor\b", "heavy armor", text)
        values = [
            " ".join(item.strip(" .;").split())
            for item in re.split(r",|\band\b", text, flags=re.IGNORECASE)
        ]
        return list(dict.fromkeys(item for item in values if item))

    return {
        "hit_die": int(hit_die_match.group(1)),
        "saving_throw_proficiencies": saving_throws,
        "armor_proficiencies": proficiencies("armor"),
        "weapon_proficiencies": proficiencies("weapons"),
        "tool_proficiencies": proficiencies("tools"),
        "skill_choice_count": skill_choice_count,
        "skill_options": skill_options,
    }


def _normalize_class_ocr_text(description: str) -> str:
    normalized = " ".join(description.split())
    labels = ("Hit Dice", "Armor", "Weapons", "Tools", "Saving Throws", "Skills")

    def normalize_label(match: re.Match[str]) -> str:
        observed = _canonical_source_heading(match.group("label"))
        matches: list[tuple[int, str]] = []
        for label in labels:
            target = _canonical_source_heading(label)
            maximum = 3 if len(target) >= 8 else 2
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                matches.append((score, label))
        matches.sort()
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"{matches[0][1]}:"

    normalized = re.sub(
        r"(?P<label>[A-Za-z!1]{2,12}(?:\s+[A-Za-z!1]{2,12})?)\s*:",
        normalize_label,
        normalized,
    )
    normalized = re.sub(r"(?i)\bVou\b", "You", normalized)
    normalized = re.sub(
        r"(?i)\bPART\s*[0-9I ]+\s+C[L~]+SSES\b", " ", normalized
    )
    for label in labels:
        normalized = re.sub(
            rf"(?<!^)(?<!\s)(?={re.escape(label)}:)", " ", normalized
        )

    def normalize_hit_die(match: re.Match[str]) -> str:
        observed = (
            match.group("die")
            .casefold()
            .replace("i", "1")
            .replace("l", "1")
            .replace("o", "0")
        )
        observed = observed.replace("s", "8")
        candidates = ("6", "8", "10", "12")
        matches = sorted(
            (
                _bounded_ocr_edit_distance(observed, candidate, 1),
                candidate,
            )
            for candidate in candidates
            if _bounded_ocr_edit_distance(observed, candidate, 1) <= 1
        )
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"Hit Dice: 1d{matches[0][1]}"

    normalized = re.sub(
        r"(?i)\bHit\s+Dice?\s*:\s*[1Il]?d(?P<die>[0-9IlSO]{1,2})",
        normalize_hit_die,
        normalized,
    )
    return normalized


def _fuzzy_catalog_names(value: str, names: tuple[str, ...] | Iterable[str]) -> list[str]:
    observed_values = [
        _canonical_source_heading(
            re.sub(r"(?i)^.*?from\s*", "", item)
        )
        for item in re.split(r",|\band\b|\bfrom\b", value, flags=re.IGNORECASE)
        if _canonical_source_heading(
            re.sub(r"(?i)^.*?from\s*", "", item)
        )
    ]
    result: list[str] = []
    for observed in observed_values:
        matches: list[tuple[int, str]] = []
        for name in names:
            target = _canonical_source_heading(str(name).replace("_", " "))
            maximum = 2 if len(target) >= 7 else 1
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                matches.append((score, str(name)))
        matches.sort()
        if matches and not (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            result.append(matches[0][1])
    return list(dict.fromkeys(result))


def _ocr_word_number(value: str) -> int:
    direct = _word_number(value)
    if direct:
        return direct
    observed = _canonical_source_heading(value)
    matches = sorted(
        (
            _bounded_ocr_edit_distance(observed, word, 1),
            number,
        )
        for word, number in (("one", 1), ("two", 2), ("three", 3), ("four", 4))
        if _bounded_ocr_edit_distance(observed, word, 1) <= 1
    )
    if not matches or (len(matches) > 1 and matches[0][0] == matches[1][0]):
        return 0
    return matches[0][1]


def _normalize_background_ocr_text(description: str) -> str:
    normalized = " ".join(description.split())
    field_labels = {
        "skillproficiencies": "Skill Proficiencies",
        "toolproficiency": "Tool Proficiencies",
        "toolproficiencies": "Tool Proficiencies",
        "languages": "Languages",
        "language": "Languages",
        "equipment": "Equipment",
        "feature": "Feature",
    }

    def normalize_label(match: re.Match[str]) -> str:
        observed = "".join(
            character
            for character in match.group("label").casefold()
            if character.isalnum()
        )
        matches: list[tuple[int, str]] = []
        for canonical, rendered in field_labels.items():
            maximum = 2 if len(canonical) >= 8 else 1
            score = _bounded_ocr_edit_distance(observed, canonical, maximum)
            if score <= maximum:
                matches.append((score, rendered))
        matches.sort()
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"{matches[0][1]}:"

    normalized = re.sub(
        r"(?P<label>[A-Za-z!1]{3,14}(?:\s+Proficienc[A-Za-z!1]{0,5})?)\s*:",
        normalize_label,
        normalized,
    )
    return normalized


def _background_selection_card(description: str) -> dict[str, Any]:
    normalized = _normalize_background_ocr_text(description)

    def field(label: str) -> str:
        match = re.search(
            rf"(?i)\b{re.escape(label)}\s*:\s*(.+?)"
            r"(?=\s+(?:Skill Proficiencies|Tool Proficiencies|Languages|"
            r"Equipment|Feature)\s*:|$)",
            normalized,
        )
        return " ".join(match.group(1).split()) if match else ""

    skills: list[str] = []
    skill_text = field("Skill Proficiencies")
    for raw_skill in re.split(r",|\band\b", skill_text, flags=re.IGNORECASE):
        observed = _canonical_source_heading(raw_skill)
        if not observed:
            continue
        matches = []
        for skill in SKILL_ABILITIES:
            target = _canonical_source_heading(skill.replace("_", " "))
            maximum = 2 if len(target) >= 8 else 1
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                matches.append((score, skill))
        matches.sort()
        if matches and not (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            skills.append(matches[0][1])
    language_count = 0
    language_text = field("Languages")
    if "choice" in language_text.casefold():
        language_count = _word_number(language_text)
    tool_text = field("Tool Proficiencies")
    fixed_tools: list[str] = []
    tool_options: list[str] = []
    tool_groups: list[dict[str, Any]] = []
    tool_count = 0
    choice_groups = (
        (
            "artisan-tools",
            "one type of artisan's tools",
            _ARTISAN_TOOL_OPTIONS_2014,
        ),
        ("gaming-set", "one type of gaming set", _GAMING_SET_OPTIONS_2014),
        (
            "musical-instrument",
            "one type of musical instrument",
            _MUSICAL_INSTRUMENT_OPTIONS_2014,
        ),
    )
    for raw_tool in re.split(r",|\band\b", tool_text, flags=re.IGNORECASE):
        observed = _canonical_source_heading(raw_tool)
        if not observed:
            continue
        group_matches: list[tuple[int, str, tuple[str, ...]]] = []
        for group_id, phrase, options in choice_groups:
            target = _canonical_source_heading(phrase)
            maximum = max(2, min(4, len(target) // 8))
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                group_matches.append((score, group_id, options))
        group_matches.sort(key=lambda item: (item[0], item[1]))
        if group_matches and not (
            len(group_matches) > 1
            and group_matches[0][0] == group_matches[1][0]
        ):
            _score, group_id, options = group_matches[0]
            tool_count += 1
            tool_options.extend(options)
            tool_groups.append(
                {"id": group_id, "maximum": 1, "options": list(options)}
            )
            continue
        fixed_matches: list[tuple[int, str]] = []
        for tool in _BACKGROUND_FIXED_TOOLS:
            target = _canonical_source_heading(tool)
            maximum = 2 if len(target) >= 8 else 1
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                fixed_matches.append((score, tool))
        fixed_matches.sort()
        if fixed_matches and not (
            len(fixed_matches) > 1
            and fixed_matches[0][0] == fixed_matches[1][0]
        ):
            fixed_tools.append(fixed_matches[0][1])
            continue
        if "choice" in raw_tool.casefold() or re.search(
            r"(?i)\b(?:one|two|three|four|five)\s+(?:type|kind)\b",
            raw_tool,
        ):
            tool_count += max(1, _word_number(raw_tool))
    equipment_description = field("Equipment")
    feature_match = re.search(
        r"(?i)\bFeature\s*:\s*([A-Za-z][A-Za-z' -]{1,100})",
        normalized,
    )
    feature = " ".join(feature_match.group(1).split()) if feature_match else ""
    return {
        "skill_proficiencies": list(dict.fromkeys(skills)),
        "background_grants": {
            "skills": list(dict.fromkeys(skills)),
            "feature": feature,
            "tools": list(dict.fromkeys(fixed_tools)),
            "languages": [],
            "equipment_item_ids": [],
            "choices": {
                "language_count": language_count,
                "language_options": [],
                "allow_any_language": bool(
                    language_count and "your choice" in language_text.casefold()
                ),
                "tool_choice_count": tool_count,
                "tool_options": list(dict.fromkeys(tool_options)),
                "tool_option_groups": tool_groups,
                "equipment_description": equipment_description,
            },
        },
    }


def _species_grants(description: str) -> dict[str, Any] | None:
    description = _normalize_species_ocr_text(description)
    folded = description.casefold()
    if not any(label in folded for label in ("ability score increase", "size.", "speed.")):
        return None
    increases: dict[str, int] = {}
    for ability in ABILITY_NAMES:
        match = re.search(
            rf"(?i)\b(?:your\s+)?{ability}\s+score\s+increases?\s+by\s+(\d+)",
            description,
        )
        if match:
            increases[ability] = int(match.group(1))
    if re.search(
        r"(?i)\bability scores? each increase(?:s)? by 1\b", description
    ):
        increases = {ability: 1 for ability in ABILITY_NAMES}
    if re.search(
        r"(?i)\breplace\w*\s+the\s+human'?s\s+Ability Score Increase trait\b",
        description,
    ):
        increases = {}
    ability_choice = {"count": 0, "amount": 0, "exclude": [], "options": []}
    choice_match = re.search(
        r"(?i)\b(?P<count>one|two|three|four|\d+)\s+"
        r"(?:(?:different|other)\s+)?ability scores? of your choice\s+"
        r"increase(?:s)? by\s+(?P<amount>\d+)\b",
        description,
    )
    if choice_match is not None:
        ability_choice = {
            "count": _word_number(choice_match.group("count")),
            "amount": int(choice_match.group("amount")),
            "exclude": list(increases),
            "options": [
                ability for ability in ABILITY_NAMES if ability not in increases
            ],
        }
    size_match = re.search(r"(?i)\byour size is\s+(Tiny|Small|Medium|Large)\b", description)
    speed_match = re.search(
        r"(?i)\b(?:base\s+)?walking speed (?:is|increases? to)\s+(\d+)\s*feet\b",
        description,
    )
    darkvision_match = re.search(r"(?i)\bdarkvision\b.{0,200}?\b(\d+)\s*feet\b", description)
    language_text = ""
    language_match = re.search(
        r"(?i)\bLanguages?\.\s*([^.!?]+[.!?]?)",
        description,
    )
    if language_match:
        language_text = language_match.group(1)
    languages = [
        language
        for language in (
            "Abyssal",
            "Aquan",
            "Auran",
            "Celestial",
            "Common",
            "Deep Speech",
            "Draconic",
            "Dwarvish",
            "Elvish",
            "Giant",
            "Gnomish",
            "Goblin",
            "Halfling",
            "Ignan",
            "Infernal",
            "Orc",
            "Primordial",
            "Sylvan",
            "Terran",
            "Undercommon",
        )
        if re.search(rf"(?i)\b{re.escape(language)}\b", language_text)
    ]
    language_options: list[str] = []
    bounded_language_choice = re.search(
        r"(?i)\b(?:your\s+)?choice\s+of\s+"
        r"(?P<options>[A-Za-z][A-Za-z' -]+(?:\s+or\s+[A-Za-z][A-Za-z' -]+)+)",
        language_text,
    )
    if bounded_language_choice is not None:
        language_options = [
            " ".join(item.strip().split()).title()
            for item in re.split(
                r"(?i)\s+or\s+", bounded_language_choice.group("options")
            )
            if item.strip()
        ]
        option_keys = {item.casefold() for item in language_options}
        languages = [item for item in languages if item.casefold() not in option_keys]
    language_choice_count = 0
    if "choice" in language_text.casefold():
        language_choice_count = _word_number(language_text) or 1
    skill_proficiencies = [
        skill
        for skill in SKILL_ABILITIES
        if re.search(
            rf"(?i)\bgain proficiency in (?:the )?"
            rf"{re.escape(skill.replace('_', ' '))}(?: skill)?\b",
            description,
        )
    ]
    skill_choice_match = re.search(
        r"(?i)\bgain proficiency in\s+"
        r"(?P<count>one|two|three|four|\d+)\s+skills? of your choice\b",
        description,
    )
    skill_choice_count = (
        _word_number(skill_choice_match.group("count"))
        if skill_choice_match is not None
        else 0
    )
    proficiency_segments = [
        match.group("items")
        for match in re.finditer(
            r"(?i)\b(?:you have|gain) proficiency with\s+(?P<items>[^.]+)",
            description,
        )
    ]
    armor_proficiencies: list[str] = []
    weapon_proficiencies: list[str] = []
    for segment in proficiency_segments:
        folded_segment = segment.casefold()
        if "armor" in folded_segment or "shield" in folded_segment:
            armor_proficiencies.extend(
                armor
                for armor in ("light armor", "medium armor", "heavy armor", "shields")
                if re.search(rf"(?i)\b{re.escape(armor.removesuffix(' armor'))}\b", segment)
            )
            continue
        values = [
            re.sub(r"(?i)^the\s+", "", " ".join(item.strip(" ,;").split())).casefold()
            for item in re.split(r",|\band\b", segment, flags=re.IGNORECASE)
        ]
        weapon_proficiencies.extend(item for item in values if item)
    resistances = [
        damage
        for damage in (
            "acid",
            "cold",
            "fire",
            "force",
            "lightning",
            "necrotic",
            "poison",
            "psychic",
            "radiant",
            "thunder",
        )
        if re.search(rf"(?i)\bresistance to {damage} damage\b", description)
    ]
    return {
        "ability_score_increases": increases,
        "ability_choice": ability_choice,
        "size": size_match.group(1).casefold() if size_match else "",
        "size_options": [],
        "walk_speed": int(speed_match.group(1)) if speed_match else 0,
        "swim_speed": 0,
        "darkvision_ft": int(darkvision_match.group(1)) if darkvision_match else 0,
        "languages": list(dict.fromkeys(languages)),
        "language_choice_count": language_choice_count,
        "language_options": language_options,
        "allow_any_language": language_choice_count > 0 and not language_options,
        "skill_proficiencies": list(dict.fromkeys(skill_proficiencies)),
        "skill_choice_count": skill_choice_count,
        "skill_options": list(SKILL_ABILITIES) if skill_choice_count else [],
        "allow_any_skill": skill_choice_count > 0,
        "armor_proficiencies": list(dict.fromkeys(armor_proficiencies)),
        "tool_proficiencies": [],
        "tool_choices": [],
        "tool_choice_count": 0,
        "tool_options": [],
        "proficiency_choice_groups": [],
        "narrative_choice_groups": [],
        "tool_expertise_choice_count": 0,
        "tool_expertise_options": [],
        "allow_any_proficient_tool_expertise": False,
        "weapon_proficiencies": list(dict.fromkeys(weapon_proficiencies)),
        "cantrip_choice": None,
        "feat_choice": (
            {"count": 1, "allowed_categories": []}
            if re.search(r"(?i)\bFeat\.\s+(?:You|Vou) gain one feat of your choice\b", description)
            else None
        ),
        "spell_grants": [],
        "resistances": list(dict.fromkeys(resistances)),
        "features": _species_feature_cards(description),
    }


def _normalize_species_ocr_text(description: str) -> str:
    normalized = " ".join(description.split())
    normalized = re.sub(r"(?i)\bVour\b", "Your", normalized)
    normalized = re.sub(r"(?i)\bEIvish\b", "Elvish", normalized)
    normalized = re.sub(r"(?i)\bskilIs\b", "skills", normalized)
    normalized = re.sub(r"(?i)\breplaee\b", "replace", normalized)
    normalized = re.sub(r"(?i)\bseores\b", "scores", normalized)
    normalized = re.sub(r"(?i)\bseore\b", "score", normalized)
    normalized = re.sub(r"(?i)\behoiee\b", "choice", normalized)
    normalized = re.sub(r"(?i)\binereases\b", "increases", normalized)
    normalized = re.sub(r"(?i)\binerease\b", "increase", normalized)
    normalized = re.sub(r"(?i)\bprofieieney\b", "proficiency", normalized)
    field_labels = (
        "Ability Score Increase",
        "Age",
        "Alignment",
        "Size",
        "Speed",
        "Languages",
    )

    def normalize_label(match: re.Match[str]) -> str:
        observed = _canonical_source_heading(match.group("label"))
        matches: list[tuple[int, str]] = []
        for label in field_labels:
            target = _canonical_source_heading(label)
            maximum = 3 if len(target) >= 12 else 2
            score = _bounded_ocr_edit_distance(observed, target, maximum)
            if score <= maximum:
                matches.append((score, label))
        matches.sort()
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"{matches[0][1]}."

    normalized = re.sub(
        r"(?P<label>[A-Za-z!1 ]{3,24})\.\s+",
        normalize_label,
        normalized,
    )

    def normalize_ability(match: re.Match[str]) -> str:
        observed = _canonical_source_heading(match.group("ability"))
        matches = sorted(
            (
                _bounded_ocr_edit_distance(observed, ability, 2),
                ability.title(),
            )
            for ability in ABILITY_NAMES
            if _bounded_ocr_edit_distance(observed, ability, 2) <= 2
        )
        if not matches or (
            len(matches) > 1 and matches[0][0] == matches[1][0]
        ):
            return match.group(0)
        return f"{matches[0][1]} score increases"

    normalized = re.sub(
        r"(?i)\b(?P<ability>[A-Za-z]{3,14})\s+s[ec]ore increases",
        normalize_ability,
        normalized,
    )
    normalized = re.sub(r"(?i)(\bincreases? by\s+)[Il](?=\b)", r"\g<1>1", normalized)
    normalized = re.sub(r"(?i)(\b\d+)\s+fe(?:el|e!)\b", r"\1 feet", normalized)
    normalized = re.sub(r"(?i)\bOwarvish\b", "Dwarvish", normalized)
    normalized = re.sub(r"(?i)\bOre\b", "Orc", normalized)
    return normalized


def _species_feature_cards(description: str) -> list[dict[str, Any]]:
    starts = [
        match
        for match in re.finditer(
            r"(?<!\w)(?P<name>[A-Z][A-Za-z' -]{1,50})\.\s+",
            description,
        )
        if len(match.group("name").split()) <= 6
        and _looks_like_species_trait_heading(match.group("name"))
    ]
    features: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    ignored = {"ability score increase", "age", "alignment", "size", "speed", "languages"}
    for index, match in enumerate(starts):
        name = " ".join(match.group("name").split())
        if name.casefold() in ignored:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(description)
        body = description[match.end() : end].strip()
        if not body:
            continue
        feature = {"name": name, "description": body[:4000]}
        key = name.casefold()
        previous = positions.get(key)
        if previous is None:
            positions[key] = len(features)
            features.append(feature)
        elif len(body) > len(str(features[previous]["description"])):
            # A page/column boundary can repeat the heading with a short
            # leading fragment. Keep the complete reviewed occurrence.
            features[previous] = feature
    return features


def _looks_like_species_trait_heading(value: str) -> bool:
    connectors = {"a", "an", "and", "of", "or", "the"}
    words = [word.strip("()") for word in value.split() if word.strip("()")]
    return bool(words) and all(
        word.casefold() in connectors
        or word.isupper()
        or (word[0].isupper() and (len(word) == 1 or word[1:].islower()))
        for word in words
    )


def _word_number(value: str) -> int:
    folded = value.casefold()
    for word, number in (("one", 1), ("two", 2), ("three", 3), ("four", 4)):
        if re.search(rf"\b{word}\b", folded):
            return number
    digit = re.search(r"\b(\d+)\b", folded)
    if digit:
        return int(digit.group(1))
    return 0


def artifact_with_direct_resolution(
    candidate: dict[str, Any],
    *,
    citation_source: str = "rule-source:reviewed-import",
    source_chunks_by_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Finalize an imported candidate's unresolved semantics before export.

    Imported commercial, third-party, and homebrew text must not silently become
    trusted engine code.  When a reviewer has not supplied a static grant,
    kernel mechanic, or primitive plan, the complete and honest resolution is a
    source-bound Agent-as-DM clause.  Persisting that clause at build time keeps
    the original evidence and the execution boundary portable, with no runtime
    authoring pass when the content is used.

    This helper does not make an unsafe card selection-ready and does not infer
    game state mutations from prose.  It only turns the previous lazy semantic
    placeholder into an explicit reviewed settlement contract.
    """

    value = deepcopy(dict(candidate.get("artifact") or {}))
    card = deepcopy(dict(value.get("card") or {}))
    if value.get("rule_clauses") is not None or card.get("rule_clauses") is not None:
        return value
    if any(
        item is not None
        for item in (
            value.get("resolution"),
            card.get("resolution"),
            value.get("resolution_plan"),
            card.get("resolution_plan"),
            value.get("resolution_plans"),
            card.get("resolution_plans"),
        )
    ) or list(value.get("mechanic_refs") or card.get("mechanic_refs") or []):
        return value
    name = " ".join(
        str(card.get("name") or candidate.get("name") or "Imported content").split()
    )
    chunk_ids = [
        str(item).strip()
        for item in (
            candidate.get("source_chunk_ids")
            or value.get("source_chunk_ids")
            or []
        )
        if str(item).strip()
    ]
    if not chunk_ids:
        raise ValueError(
            f"candidate {candidate.get('id')} needs source chunks before direct resolution"
        )
    citation_chunk_id = chunk_ids[0]
    excerpt = ""
    if source_chunks_by_id is not None:
        for chunk_id in chunk_ids:
            exact_content = " ".join(
                str(source_chunks_by_id.get(chunk_id) or "").split()
            )
            if len(exact_content) >= 10:
                citation_chunk_id = chunk_id
                excerpt = exact_content[:4000]
                break
        if not excerpt:
            raise ValueError(
                f"candidate {candidate.get('id')} has no exact indexed source excerpt"
            )
    else:
        excerpt = _candidate_resolution_excerpt(candidate, card=card, name=name)

    scope = str(
        value.get("mechanical_scope")
        or candidate.get("mechanical_scope")
        or "review_required"
    )
    descriptive = scope == "descriptive"
    settlement: dict[str, Any]
    if descriptive:
        settlement = {"mode": "descriptive"}
        clause_scope = "descriptive"
    else:
        ruling_kind = _candidate_ruling_kind(candidate)
        resolution_reason = (
            f"The imported {str(value.get('kind') or candidate.get('kind') or 'content')} "
            f"card {name!r} has source-specific semantics that are not wholly owned "
            "by a registered kernel mechanic or reviewed primitive plan. Apply only "
            "the exact cited text through the Agent-as-DM ruling boundary and public "
            "engine tools."
        )
        settlement = {
            "mode": "agent_ruling",
            "default_resolver": "agent",
            "ruling_kind": ruling_kind,
            "reason": resolution_reason,
        }
        clause_scope = "mechanical"
        value["mechanical_scope"] = "mechanical"
        existing_requirements = list(card.get("ruling_requirements") or [])
        existing_requirements.append(
            {
                "kind": "source_bound_import_resolution",
                "reason": resolution_reason,
                "source_excerpt": excerpt,
                "default_resolver": "agent",
                "ruling_kind": ruling_kind,
                "policy_ref": "rule_clause.v1",
                "requires_external_input_only_for": [],
            }
        )
        card["ruling_requirements"] = existing_requirements

    clause_id = "source-resolution-" + hashlib.sha256(
        "\x1f".join(
            (
                str(candidate.get("id") or ""),
                str(value.get("kind") or candidate.get("kind") or "content"),
                name,
                citation_chunk_id,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    value["rule_clauses"] = [
        {
            "schema_version": 1,
            "id": clause_id,
            "title": name,
            "scope": clause_scope,
            "source_citations": [
                {
                    "source": citation_source,
                    "source_ref": {"chunk_id": citation_chunk_id},
                    "source_excerpt": excerpt,
                }
            ],
            "settlement": settlement,
        }
    ]
    value["card"] = card
    value["semantic_resolution"] = {
        "status": "resolved",
        "mode": settlement["mode"],
        "first_use_compilation_required": False,
        "clause_ids": [clause_id],
    }
    value["execution_state"] = (
        "descriptive_ready"
        if settlement["mode"] == "descriptive"
        else "ruling_ready"
    )
    return value


def _candidate_resolution_excerpt(
    candidate: dict[str, Any],
    *,
    card: dict[str, Any],
    name: str,
) -> str:
    definition = dict(card.get("definition") or {})
    choices = dict(card.get("choices") or {})
    manual_ruling = dict(choices.get("manual_ruling") or {})
    raw = next(
        (
            item
            for item in (
                definition.get("effect"),
                card.get("description"),
                card.get("normalized_content"),
                candidate.get("normalized_content"),
                manual_ruling.get("source_excerpt"),
            )
            if str(item or "").strip()
        ),
        "",
    )
    excerpt = " ".join(str(raw).split())
    if len(excerpt) < 10:
        excerpt = f"Imported source card: {name}."
    return excerpt[:4000]


def _candidate_ruling_kind(candidate: dict[str, Any]) -> str:
    declared = dict(candidate.get("ruling_requirement") or {})
    kind = str(declared.get("ruling_kind") or "").strip()
    if kind in {
        "agent_dm_adjudication",
        "environmental_consequence",
        "generic_spell_effect",
        "module_specific_procedure",
        "source_or_scene_fact",
    }:
        return kind
    return (
        "generic_spell_effect"
        if str(candidate.get("kind") or "") == "spell"
        else "agent_dm_adjudication"
    )


def validate_selection_ready_artifacts(artifacts: list[dict[str, Any]]) -> list[str]:
    """Check the minimum schema needed before a catalog card can mutate a sheet."""
    errors: list[str] = []
    for index, artifact in enumerate(artifacts):
        if artifact.get("application_state", "selection_ready") != "selection_ready":
            continue
        kind = str(artifact.get("kind") or "")
        card = dict(artifact.get("card") or {})
        prefix = f"artifacts[{index}]"
        if artifact.get("selection_contract") is not None:
            errors.extend(
                f"{prefix}: {error}"
                for error in selection_contract_errors(artifact)
            )
        raw_plan = artifact.get("resolution_plan", card.get("resolution_plan"))
        raw_plans = artifact.get("resolution_plans", card.get("resolution_plans"))
        if raw_plan is not None and raw_plans is not None:
            errors.append(
                f"{prefix} cannot combine resolution_plan and resolution_plans"
            )
            plan_values: list[Any] = []
        elif raw_plans is not None and (
            not isinstance(raw_plans, list) or not raw_plans
        ):
            errors.append(f"{prefix}.resolution_plans must be a non-empty list")
            plan_values = []
        else:
            plan_values = (
                [raw_plan]
                if raw_plan is not None
                else list(raw_plans or [])
            )
        compiled_plans = []
        for plan_value in plan_values:
            try:
                compiled_plan = compile_resolution_plan(plan_value)
            except ResolutionPlanCompilationError as error:
                errors.append(f"{prefix}.resolution_plan: {error}")
            else:
                if compiled_plan.source_card_id != str(artifact.get("id") or ""):
                    errors.append(
                        f"{prefix}.resolution_plan source_card_id must match artifact id"
                    )
                compiled_plans.append(compiled_plan)
        if len({plan.id for plan in compiled_plans}) != len(compiled_plans):
            errors.append(f"{prefix}.resolution plan ids must be unique")
        mechanical_scope = str(
            artifact.get("mechanical_scope")
            or ("mechanical" if kind in {"spell", "statblock"} else "review_required")
        )
        if mechanical_scope not in {"descriptive", "mechanical", "review_required"}:
            errors.append(f"{prefix}.mechanical_scope is invalid")
        raw_clauses = artifact.get("rule_clauses", card.get("rule_clauses"))
        compiled_clauses = ()
        if raw_clauses is not None:
            try:
                compiled_clauses = compile_rule_clauses(raw_clauses)
            except RuleContractError as error:
                errors.append(f"{prefix}.rule_clauses: {error}")
            else:
                errors.extend(
                    f"{prefix}: {error}"
                    for error in validate_rule_clause_coverage(
                        compiled_clauses,
                        artifact=artifact,
                        plan_ids={plan.id for plan in compiled_plans},
                        mechanic_refs=set(artifact.get("mechanic_refs") or [])
                        - {plan.id for plan in compiled_plans},
                        require_mechanical_clause=mechanical_scope == "mechanical",
                    )
                )
        if (
            mechanical_scope == "mechanical"
            and not compiled_plans
            and not compiled_clauses
            and not (kind == "spell" and card.get("resolution") is not None)
        ):
            errors.append(
                f"{prefix} mechanical content needs rule_clauses or a resolution_plan"
            )
        if kind == "spell":
            if not isinstance(card.get("classes"), list) or not card["classes"]:
                errors.append(f"{prefix} spell needs a nonempty classes list")
            level = card.get("level")
            if not isinstance(level, int) or not 0 <= level <= 9:
                errors.append(f"{prefix} spell level must be an integer from 0 to 9")
            if not isinstance(card.get("definition"), dict):
                errors.append(f"{prefix} spell needs a structured definition")
            else:
                try:
                    normalize_spell_definition(
                        card["definition"],
                        f"{prefix}.card.definition",
                    )
                except ValueError as error:
                    errors.append(str(error))
            if card.get("resolution") is not None:
                try:
                    normalize_spell_resolution(
                        card["resolution"], f"{prefix}.card.resolution"
                    )
                except ValueError as error:
                    errors.append(str(error))
        elif kind == "subclass":
            if not str(card.get("class_name") or "").strip():
                errors.append(f"{prefix} subclass needs class_name")
            if not isinstance(card.get("minimum_level"), int) or card["minimum_level"] < 1:
                errors.append(f"{prefix} subclass needs minimum_level >= 1")
        elif kind == "background":
            errors.extend(
                f"{prefix} {error}" for error in background_materializer_errors(card)
            )
        elif kind == "species":
            errors.extend(
                f"{prefix} {error}" for error in species_materializer_errors(card)
            )
        elif (
            kind == "feat"
            and "prerequisites" in card
            and not isinstance(card["prerequisites"], list)
        ):
            errors.append(f"{prefix} feat prerequisites must be a list")
    return errors


def audit_release_resolution_readiness(
    artifacts: list[dict[str, Any]],
    *,
    settled_mechanic_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Audit that a published artifact never depends on first-use compilation."""

    unresolved: list[dict[str, str]] = []
    modes: dict[str, int] = {}
    for index, artifact in enumerate(artifacts):
        artifact_id = str(artifact.get("id") or f"artifacts[{index}]")
        execution_state = str(artifact.get("execution_state") or "")
        if execution_state in {
            "agent_resolution_required",
            "content_authoring_required",
            "first_use_compilation_required",
        }:
            unresolved.append(
                {
                    "artifact_id": artifact_id,
                    "reason": (
                        "artifact still declares deferred semantic authoring: "
                        f"{execution_state}"
                    ),
                }
            )
            continue
        card = dict(artifact.get("card") or {})
        semantic = dict(artifact.get("semantic_resolution") or {})
        semantic_mode = str(semantic.get("mode") or "")
        expected_state = (
            "ruling_ready"
            if semantic_mode == "agent_ruling"
            else "descriptive_ready"
            if semantic_mode == "descriptive"
            else ""
        )
        if (
            semantic.get("status") == "resolved"
            and semantic.get("first_use_compilation_required") is False
            and expected_state
            and execution_state != expected_state
        ):
            unresolved.append(
                {
                    "artifact_id": artifact_id,
                    "reason": (
                        "resolved semantic mode has a non-canonical execution state: "
                        f"expected {expected_state}, found {execution_state or 'missing'}"
                    ),
                }
            )
            continue
        plan = artifact.get("resolution_plan", card.get("resolution_plan"))
        plans = artifact.get("resolution_plans", card.get("resolution_plans"))
        solution = artifact.get(
            "resolution_solution", card.get("resolution_solution")
        )
        if plan is not None or plans is not None:
            modes["primitive_plan"] = modes.get("primitive_plan", 0) + 1
            continue
        raw_clauses = artifact.get("rule_clauses", card.get("rule_clauses"))
        if raw_clauses is not None:
            try:
                clauses = compile_rule_clauses(raw_clauses)
            except RuleContractError as error:
                unresolved.append(
                    {
                        "artifact_id": artifact_id,
                        "reason": f"invalid rule_clauses: {error}",
                    }
                )
                continue
            for clause in clauses:
                mode = str(clause.settlement["mode"])
                modes[mode] = modes.get(mode, 0) + 1
            continue

        mode = str(semantic.get("mode") or "")
        if (
            semantic.get("status") == "resolved"
            and semantic.get("first_use_compilation_required") is False
            and mode
        ):
            modes[mode] = modes.get(mode, 0) + 1
            continue
        if card.get("resolution") is not None:
            modes["kernel_mechanic"] = modes.get("kernel_mechanic", 0) + 1
            continue
        mechanic_refs = {
            str(item)
            for item in [
                *list(artifact.get("mechanic_refs") or []),
                *list(card.get("mechanic_refs") or []),
            ]
            if str(item)
        }
        if (
            mechanic_refs
            and settled_mechanic_ids is not None
            and mechanic_refs <= settled_mechanic_ids
        ):
            modes["kernel_mechanic"] = modes.get("kernel_mechanic", 0) + 1
            continue
        if solution is not None:
            unresolved.append(
                {
                    "artifact_id": artifact_id,
                    "reason": "resolution_solution has no persisted resolution_plan",
                }
            )
            continue
        unresolved.append(
            {
                "artifact_id": artifact_id,
                "reason": "artifact has no build-time semantic resolution",
            }
        )

    return {
        "schema_version": 1,
        "complete": not unresolved,
        "artifact_count": len(artifacts),
        "resolved_count": len(artifacts) - len(
            {item["artifact_id"] for item in unresolved}
        ),
        "modes": dict(sorted(modes.items())),
        "unresolved": unresolved,
        "first_use_compilation_required": False if not unresolved else True,
    }


def _require_candidate_plan_evidence(
    citations: tuple[dict[str, Any], ...],
    *,
    candidate: dict[str, Any],
) -> None:
    """Keep every executable plan citation inside the reviewed candidate source."""

    allowed_chunks = {
        str(item)
        for item in candidate.get("source_chunk_ids") or []
        if str(item)
    }
    if not allowed_chunks:
        raise ValueError(
            f"candidate {candidate.get('id')} needs source chunks before plan compilation"
        )
    for citation in citations:
        source_ref = dict(citation.get("source_ref") or {})
        chunk_id = str(source_ref.get("chunk_id") or "")
        if chunk_id not in allowed_chunks:
            raise ValueError(
                f"candidate {candidate.get('id')} resolution_plan citation must "
                "reference one of its reviewed source chunks"
            )


def _require_candidate_clause_evidence(
    citations: tuple[dict[str, Any], ...],
    *,
    candidate: dict[str, Any],
    clause_id: str,
) -> None:
    """Keep every clause, including prose-only clauses, source-addressable."""

    allowed_chunks = {
        str(item)
        for item in candidate.get("source_chunk_ids") or []
        if str(item)
    }
    if not allowed_chunks:
        raise ValueError(
            f"candidate {candidate.get('id')} needs source chunks before "
            "rule clause compilation"
        )
    for citation in citations:
        source_ref = dict(citation.get("source_ref") or {})
        chunk_id = str(source_ref.get("chunk_id") or "")
        if chunk_id not in allowed_chunks:
            raise ValueError(
                f"candidate {candidate.get('id')} rule clause {clause_id} "
                "citation must reference one of its reviewed source chunks"
            )


def _has_level_feature_marker(content: str) -> bool:
    return bool(
        re.search(
            r"(?i)\b(?:at|starting\s+at|beginning\s+at)\s*"
            r"\d{1,2}(?:st|nd|rd|th)\s*[- ]?\s*level\b",
            content,
        )
        or re.search(
            r"(?i)\b\d{1,2}(?:st|nd|rd|th)\s*[- ]\s*level\b"
            r".{0,100}\bfeature\b",
            content,
        )
    )


def _classify(
    title: str,
    heading_path: list[str],
    content: str,
    *,
    source_title: str = "",
) -> tuple[str, tuple[str, ...]] | None:
    normalized_title = _normalize_candidate_display_name(title)
    title_folded = normalized_title.casefold().strip()
    ancestors = " ".join(heading_path[:-1]).casefold()
    direct_parent = (
        str(heading_path[-2]).casefold().strip() if len(heading_path) >= 2 else ""
    )
    sample = content[:2400]
    folded = sample.casefold()

    normalized_spell_sample = (
        _normalize_spell_ocr_text(sample)
        if _looks_like_spell_schema_text(sample)
        else sample
    )
    spell_labels = tuple(
        label
        for label in ("casting time", "range", "components", "duration")
        if re.search(
            rf"(?i)\b{re.escape(label)}\s*:", normalized_spell_sample
        )
    )
    spell_level = bool(_SPELL_LEVEL_RE.search(normalized_spell_sample))
    if (
        title_folded not in _GENERIC_TITLES
        and _PAGE_HEADER_RE.match(title_folded) is None
        and "casting time" in spell_labels
        and (spell_level or len(spell_labels) >= 3)
    ):
        signals = [*spell_labels, *(["spell level"] if spell_level else [])]
        return "spell", tuple(signals)

    statblock_labels = tuple(label for label in _STATBLOCK_LABELS if label in folded)
    ability_row = all(value in folded for value in ("str", "dex", "con", "int", "wis", "cha"))
    if title_folded not in {"actions", "cha", "legendary actions"} and len(
        statblock_labels
    ) >= 3 and ability_row:
        return "statblock", (*statblock_labels, "six abilities")

    normalized_background = _normalize_background_ocr_text(sample).casefold()
    background_signals = tuple(
        label
        for label in (
            "skill proficiencies",
            "tool proficiencies",
            "languages",
            "equipment",
            "background feature",
        )
        if label in normalized_background
    )
    if "skill proficiencies" in background_signals and (
        "background" in ancestors or len(background_signals) >= 2
    ):
        return "background", background_signals

    feat_section = bool(re.search(r"\bfeats?\b", ancestors))
    if (
        title_folded not in _GENERIC_TITLES
        and not _PAGE_HEADER_RE.match(title_folded)
        and feat_section
        and (
        "prerequisite" in folded or len(folded) >= 80
        )
    ):
        signals = ["feat section"]
        if "prerequisite" in folded:
            signals.append("prerequisite")
        return "feat", tuple(signals)

    subclass_title = bool(
        re.search(
            r"\b(?:path|way|college|domain|circle|oath|school|patron|origin|bloodline|"
            r"archetype|tradition)\s+of\b|"
            r"\b\w+\s+domain(?:\s+(?:features|spells))?$",
            title_folded,
        )
    )
    subclass_section = "subclass" in ancestors or "subclasses" in ancestors
    explicit_feature_grant = _has_level_feature_marker(folded)
    subclass_identity_shape = (
        len(re.findall(r"[A-Za-z][A-Za-z'’\-]*", normalized_title)) >= 2
        or title_folded in folded
    )
    subclass_parent = (
        direct_parent in _GENERIC_SUBCLASS_PARENT_TITLES
        and not explicit_feature_grant
        and len(folded) >= 40
        and (
            subclass_identity_shape
            or title_folded in _STANDARD_FLAT_SUBCLASS_TITLES
        )
        and (
            subclass_title
            or title_folded in _STANDARD_FLAT_SUBCLASS_TITLES
            or "subclass features" in folded
            or not direct_parent.endswith("s")
        )
    )
    subclass_features = "subclass features" in folded
    if title_folded not in _GENERIC_TITLES and (
        subclass_features
        or subclass_parent
        or (
            subclass_title
            and not title_folded.endswith(" spells")
            and (subclass_section or "level" in folded or len(folded) >= 80)
        )
    ):
        signals = [
            *(["subclass title"] if subclass_title else []),
            *(["subclass section"] if subclass_section else []),
            *(["subclass parent"] if subclass_parent else []),
            *(["subclass features"] if subclass_features else []),
        ]
        return "subclass", tuple(signals)

    class_signals = [
        label
        for label in ("class features", "hit dice", "primary ability", "saving throw proficiencies")
        if label in folded
    ]
    if title_folded == "class features" and "class features" not in class_signals:
        class_signals.insert(0, "class features")
    source_class_name = _class_name_from_source(source_title).casefold()
    known_source_class = source_class_name in _CLASS_NAMES or source_class_name == "revised ranger"
    if (
        title_folded == "class features"
        and known_source_class
        and "source class" not in class_signals
    ):
        class_signals.append("source class")
    class_title = title_folded in _CLASS_NAMES or any(
        name in title_folded for name in ("artificer", "blood hunter")
    )
    class_title = class_title or (
        title_folded == "class features" and known_source_class
    )
    if class_title and "class features" in class_signals and len(class_signals) >= 2:
        return "class", tuple(class_signals)

    species_signals = tuple(
        label
        for label in ("ability score increase", "age", "alignment", "size", "speed", "languages")
        if re.search(rf"(?i)\b{re.escape(label)}\s*[.:]", sample)
    )
    if len(species_signals) >= 4:
        return "species", species_signals

    raw_item_sample = sample[:500]
    item_header = _ITEM_HEADER_RE.search(
        _normalize_item_header_ocr_text(raw_item_sample)
    )
    exact_item_header = _ITEM_HEADER_RE.search(raw_item_sample)
    item_context = "magic item" in ancestors or bool(
        re.search(
            r"(?i)\b(?:common|uncommon|rare|very rare|legendary|artifact|varies)\b",
            raw_item_sample[:240],
        )
    )
    if (
        title_folded not in _GENERIC_FEATURE_TITLES
        and _PAGE_HEADER_RE.match(title_folded) is None
        and item_header
        and (exact_item_header or item_context)
    ):
        signals = ["item category"]
        for label in ("rarity", "requires attunement", "charges"):
            if label in folded:
                signals.append(label)
        return "item", tuple(signals)

    feature_section = (
        "class features" in ancestors
        or "subclass features" in ancestors
        or known_source_class
    )
    level_grant = explicit_feature_grant
    subclass_gateway = bool(
        re.search(
            r"(?i)\bat\s*\d{1,2}(?:st|nd|rd|th)\s*[- ]?\s*level\s*,?\s*"
            r"(?:an?\s+)?(?:artificer|barbarian|bard|blood hunter|cleric|druid|"
            r"fighter|monk|paladin|ranger|rogue|sorcerer|warlock|wizard)\s+"
            r"gains?\s+the\s+.{1,80}\bfeature\b",
            folded,
        )
        and bool(re.search(r"(?i)\b(?:option|available|presented here)\b", folded))
    )
    if (
        title_folded not in _GENERIC_FEATURE_TITLES
        and (feature_section or len(folded) >= 80)
        and level_grant
        and not subclass_gateway
    ):
        return "feature", ("feature section", "level grant")
    return None


def _class_name_from_source(source_title: str) -> str:
    folded = source_title.casefold()
    compact = re.sub(r"[^a-z]+", "", folded)
    if "revisedranger" in compact:
        return "Revised Ranger"
    if "bloodhunter" in compact:
        return "Blood Hunter"
    for name in sorted(_CLASS_NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}(?:v\d+)?\b", folded):
            return name.title()
    return ""


def _minimum_page(left: Any, right: Any) -> int | None:
    values = [
        value
        for value in (left, right)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return min(values) if values else None


def _maximum_page(left: Any, right: Any) -> int | None:
    values = [
        value
        for value in (left, right)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return max(values) if values else None


def _artifact_id(pack_id: str, kind: str, name: str) -> str:
    slug = ascii_slug(name)
    if not slug:
        slug = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{pack_id}.{kind}.{slug[:100]}"


def _candidate_artifact_disambiguator(candidate: dict[str, Any]) -> str:
    """Return a source-stable suffix for legitimate same-named entities.

    Runtime chunk UUIDs and review candidate IDs are deliberately excluded so
    importing the same source into a fresh database yields the same artifact
    identities. Source position plus the reviewed card/body distinguishes
    repeated headings and genuinely separate same-named entries.
    """

    artifact = dict(candidate.get("artifact") or {})
    stable_locator = {
        "kind": str(artifact.get("kind") or candidate.get("kind") or ""),
        "name": str(
            dict(artifact.get("card") or {}).get("name")
            or candidate.get("name")
            or ""
        ),
        "source_heading_path": list(candidate.get("source_heading_path") or []),
        "page_start": candidate.get("page_start"),
        "page_end": candidate.get("page_end"),
        "artifact": _stable_candidate_value(artifact),
        "normalized_content": candidate.get("normalized_content"),
        "extraction_signals": list(candidate.get("extraction_signals") or []),
    }
    serialized = json.dumps(
        stable_locator,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


def _stable_candidate_value(value: Any) -> Any:
    """Remove runtime locators and derived fingerprints from an ID seed."""

    if isinstance(value, list):
        return [_stable_candidate_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _stable_candidate_value(item)
        for key, item in value.items()
        if key
        not in {
            "chunk_id",
            "fingerprint",
            "source_chunk_ids",
            "source_id",
        }
    }
