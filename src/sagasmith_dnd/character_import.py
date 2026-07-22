"""Conservative inspection of user-supplied D&D character documents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sagasmith_core.documents import NormalizedDocument

_CLASSES = (
    "artificer",
    "barbarian",
    "bard",
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
)
_ABILITY_FIELDS = {
    "strength": "Front_Str Score",
    "dexterity": "Front_Dex Score",
    "constitution": "Front_Con Score",
    "intelligence": "Front_Int Score",
    "wisdom": "Front_Wis Score",
    "charisma": "Front_Cha Score",
}
_REQUIRED_FORM_SIGNALS = (
    "frontcharactername",
    "frontrace",
    "frontlevel",
    "frontstrscore",
)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered or rendered.casefold() in {"/off", "off", "none"}:
        return None
    return rendered[1:] if rendered.startswith("/") else rendered


def _value(fields: dict[str, Any], *names: str) -> str | None:
    by_key = {_key(name): value for name, value in fields.items()}
    for name in names:
        rendered = _text(by_key.get(_key(name)))
        if rendered is not None:
            return rendered
    return None


def _integer(value: str | None, *, minimum: int, maximum: int) -> int | None:
    if value is None or not re.fullmatch(r"[+-]?\d+", value):
        return None
    number = int(value)
    return number if minimum <= number <= maximum else None


def _class_hint(content: str) -> str | None:
    headings = [
        match.group(1).strip().casefold()
        for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", content, re.MULTILINE)
    ]
    for heading in headings:
        for class_name in _CLASSES:
            if heading == class_name or heading.startswith(f"{class_name} "):
                return class_name
    return None


def _ability_score_sets(content: str) -> list[list[int]]:
    result: list[list[int]] = []
    for line in content.splitlines():
        values = [int(item) for item in re.findall(r"(?<!\d)\d{1,2}(?!\d)", line)]
        if len(values) == 6 and all(3 <= value <= 20 for value in values):
            result.append(values)
    return result


def inspect_character_document(
    document: NormalizedDocument,
    *,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Describe source-backed character evidence without inventing missing values."""
    metadata = dict(document.metadata)
    field_names = [str(item) for item in metadata.get("form_field_names") or []]
    fields = {
        str(name): value
        for name, value in dict(metadata.get("populated_form_fields") or {}).items()
    }
    normalized_names = {_key(name) for name in field_names}
    character_signals = sum(signal in normalized_names for signal in _REQUIRED_FORM_SIGNALS)
    score_sets = _ability_score_sets(document.content)
    score_source_lines = [
        line.strip()
        for line in document.content.splitlines()
        if line.strip() and not line.lstrip().startswith("<!-- page:")
    ]
    source = source_name or Path(document.source_path).name

    if character_signals < 3:
        if score_sets and len(score_sets) == len(score_source_lines):
            return {
                "document_kind": "ability_score_options",
                "status": "review_ready",
                "ready_to_create": False,
                "ability_score_sets": score_sets,
                "manual_assignment_required": True,
                "manual_input": {
                    "ability_scores_allowed": True,
                    "modes": ["manual", "source_set", "standard_array", "point_buy", "roll"],
                },
                "source": {
                    "name": source,
                    "path": document.source_path,
                    "checksum": document.checksum,
                    "page_count": document.page_count,
                },
                "warnings": list(document.warnings),
            }
        return {
            "document_kind": "unknown",
            "status": "unsupported",
            "ready_to_create": False,
            "source": {
                "name": source,
                "path": document.source_path,
                "checksum": document.checksum,
                "page_count": document.page_count,
            },
            "warnings": [*document.warnings, "document is not a recognized D&D character sheet"],
        }

    name = _value(fields, "Front_Character Name", "Back_Character Name")
    species = _value(fields, "Front_Race")
    background = _value(fields, "Front_Background")
    subclass = _value(fields, "Front_Archetype")
    level = _integer(_value(fields, "Front_Level"), minimum=1, maximum=20)
    class_name = _class_hint(document.content)
    abilities = {
        ability: _integer(_value(fields, field), minimum=1, maximum=30)
        for ability, field in _ABILITY_FIELDS.items()
    }
    hp_max = _integer(
        _value(
            fields,
            "Front_Maximum Hit Points",
            "Front_Max HP",
            "Front_Hit Point Maximum",
        ),
        minimum=1,
        maximum=10000,
    )
    missing = []
    if name is None:
        missing.append("name")
    if class_name is None:
        missing.append("class")
    if level is None:
        missing.append("level")
    missing.extend(f"ability_scores.{name}" for name, value in abilities.items() if value is None)
    if hp_max is None:
        missing.append("hit_points.maximum")

    save_proficiencies = sorted(
        name.removeprefix("Front_Save ").casefold()
        for name, value in fields.items()
        if name.startswith("Front_Save ") and _text(value) == "Yes"
    )
    skill_proficiencies = sorted(
        name.removeprefix("Front_Proficiency ").strip()
        for name, value in fields.items()
        if name.startswith("Front_Proficiency ") and _text(value) == "Yes"
    )
    draft = {
        "name": name,
        "progression": {
            "class": class_name,
            "subclass": subclass,
            "level": level,
            "species": species,
            "background": background,
        },
        "ability_scores": abilities,
        "hit_points": {"maximum": hp_max},
        "save_proficiencies": save_proficiencies,
        "skill_proficiencies": skill_proficiencies,
    }
    warnings = list(document.warnings)
    if missing:
        warnings.append(
            "character document is an incomplete template; complete every missing field "
            "and apply campaign rule-pack content before creation"
        )
    return {
        "document_kind": "character_sheet",
        "status": "ready" if not missing else "incomplete_template",
        "ready_to_create": not missing,
        "draft": draft,
        "missing_fields": missing,
        "manual_input": {
            "ability_scores_allowed": True,
            "modes": ["manual", "source_set", "standard_array", "point_buy", "roll"],
            "required_manual_fields": missing,
        },
        "form_evidence": {
            "field_count": int(metadata.get("form_field_count") or 0),
            "populated_field_count": int(metadata.get("populated_form_field_count") or 0),
            "populated_field_names": sorted(fields),
        },
        "source": {
            "name": source,
            "path": document.source_path,
            "checksum": document.checksum,
            "page_count": document.page_count,
        },
        "warnings": warnings,
    }
