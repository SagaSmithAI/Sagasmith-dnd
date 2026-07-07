"""D&D 5e 2014 character-based check helpers."""

from __future__ import annotations

from typing import Any

from sagasmith_dnd.engine import ability_modifier, proficiency_bonus, resolve_check


ABILITIES = {
    "str": "strength",
    "strength": "strength",
    "dex": "dexterity",
    "dexterity": "dexterity",
    "con": "constitution",
    "constitution": "constitution",
    "int": "intelligence",
    "intelligence": "intelligence",
    "wis": "wisdom",
    "wisdom": "wisdom",
    "cha": "charisma",
    "charisma": "charisma",
}

SKILLS_2014 = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}


def resolve_character_check(
    *,
    sheet: dict[str, Any],
    check_type: str,
    dc: int,
    ability: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    source: str = "",
) -> dict[str, Any]:
    level = int(sheet.get("level", 1))
    abilities = dict(sheet.get("abilities") or {})
    proficiency_entries = _proficiency_entries(sheet)
    expertise_entries = _expertise_entries(sheet)

    subject = ""
    if check_type == "skill":
        subject = _normalize_name(skill or "")
        if subject not in SKILLS_2014:
            raise ValueError(f"unknown 2014 skill: {skill}")
        ability_name = _ability_name(ability) if ability else SKILLS_2014[subject]
        proficient = _has(proficiency_entries, "skill", subject)
        expertise = _has(expertise_entries, "skill", subject)
    elif check_type == "save":
        ability_name = _ability_name(ability or "")
        subject = ability_name
        proficient = _has(proficiency_entries, "save", ability_name)
        expertise = False
    elif check_type == "tool":
        subject = _normalize_name(tool or "")
        if not subject:
            raise ValueError("tool is required")
        ability_name = _ability_name(ability or "")
        proficient = _has(proficiency_entries, "tool", subject)
        expertise = _has(expertise_entries, "tool", subject)
    elif check_type == "initiative":
        ability_name = "dexterity"
        subject = "initiative"
        proficient = _has(proficiency_entries, "initiative", "initiative")
        expertise = _has(expertise_entries, "initiative", "initiative")
    else:
        ability_name = _ability_name(ability or "")
        subject = ability_name
        proficient = _has(proficiency_entries, "ability", ability_name)
        expertise = _has(expertise_entries, "ability", ability_name)

    score = int(abilities.get(ability_name, 10))
    result = resolve_check(
        dc=dc,
        ability_score=score,
        proficient=proficient or expertise,
        proficiency_multiplier=2 if expertise else 1,
        level=level,
        bonus=bonus,
        advantage=advantage,
        disadvantage=disadvantage,
    )
    return {
        **result,
        "ruleset": "5e-2014",
        "check_type": check_type,
        "subject": subject,
        "ability": ability_name,
        "ability_score": score,
        "ability_modifier": ability_modifier(score),
        "level": level,
        "proficient": proficient or expertise,
        "expertise": expertise,
        "proficiency_value": proficiency_bonus(level),
        "source": source,
        "breakdown": {
            "d20": result["natural"],
            "ability_modifier": ability_modifier(score),
            "proficiency_bonus": result["proficiency_bonus"],
            "bonus": bonus,
        },
    }


def _ability_name(value: str) -> str:
    key = _normalize_name(value)
    if key not in ABILITIES:
        raise ValueError(f"unknown ability: {value}")
    return ABILITIES[key]


def _proficiency_entries(sheet: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("proficiencies", "saving_throw_proficiencies", "skill_proficiencies", "tool_proficiencies"):
        raw = sheet.get(key, [])
        if isinstance(raw, dict):
            raw = [name for name, enabled in raw.items() if enabled]
        for item in raw or []:
            values.add(_normalize_proficiency(str(item), key))
    return values


def _expertise_entries(sheet: dict[str, Any]) -> set[str]:
    raw = sheet.get("expertise", [])
    if isinstance(raw, dict):
        raw = [name for name, enabled in raw.items() if enabled]
    return {_normalize_proficiency(str(item), "expertise") for item in raw or []}


def _normalize_proficiency(value: str, field: str) -> str:
    name = _normalize_name(value)
    if ":" in name:
        prefix, rest = name.split(":", 1)
        return f"{_kind_alias(prefix)}:{rest}"
    if field == "saving_throw_proficiencies":
        return f"save:{_ability_name(name)}"
    if field == "skill_proficiencies":
        return f"skill:{name}"
    if field == "tool_proficiencies":
        return f"tool:{name}"
    if field == "expertise" and name in SKILLS_2014:
        return f"skill:{name}"
    if name in SKILLS_2014:
        return f"skill:{name}"
    if name in ABILITIES:
        return f"save:{_ability_name(name)}"
    return name


def _kind_alias(value: str) -> str:
    if value in {"saving_throw", "saving_throws", "save_proficiency", "save"}:
        return "save"
    if value in {"skills", "skill"}:
        return "skill"
    if value in {"tools", "tool"}:
        return "tool"
    return value


def _has(entries: set[str], kind: str, name: str) -> bool:
    normalized = _normalize_name(name)
    return f"{kind}:{normalized}" in entries or normalized in entries


def _normalize_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
