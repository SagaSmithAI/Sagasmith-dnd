"""Actor-document d20 roll pipeline."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService

from sagasmith_dnd.checks import SKILLS_2014
from sagasmith_dnd.engine import ability_modifier, proficiency_bonus, resolve_check

ABILITY_ALIASES = {
    "str": "str",
    "strength": "str",
    "dex": "dex",
    "dexterity": "dex",
    "con": "con",
    "constitution": "con",
    "int": "int",
    "intelligence": "int",
    "wis": "wis",
    "wisdom": "wis",
    "cha": "cha",
    "charisma": "cha",
}

SKILL_ABILITIES = {
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}


def roll_actor_d20(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
    roll_type: str,
    dc: int,
    ability: str | None = None,
    skill: str | None = None,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    source: str = "",
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    system = dict((actor.derived or {}).get("effective_system") or actor.system or {})
    statuses = set((actor.derived or {}).get("statuses") or [])
    level = _level(system)
    prof = _proficiency(system, level)

    if roll_type == "skill":
        subject = _normalize(skill or "")
        if subject not in SKILLS_2014:
            raise ValueError(f"unknown 2014 skill: {skill}")
        ability_key = _ability_key(ability) if ability else SKILL_ABILITIES[subject]
        multiplier = _skill_multiplier(system, subject)
    elif roll_type == "save":
        ability_key = _ability_key(ability or "")
        subject = ability_key
        multiplier = _save_multiplier(system, ability_key)
    elif roll_type == "initiative":
        ability_key = "dex"
        subject = "initiative"
        multiplier = _initiative_multiplier(system)
    else:
        ability_key = _ability_key(ability or "")
        subject = ability_key
        multiplier = 0

    if "poisoned" in statuses and roll_type in {"ability", "skill"}:
        disadvantage = True
    if "blinded" in statuses and roll_type == "attack":
        disadvantage = True

    score = _ability_score(system, ability_key)
    result = resolve_check(
        dc=dc,
        ability_score=score,
        proficient=multiplier > 0,
        proficiency_multiplier=max(1, int(multiplier)) if multiplier else 1,
        level=level,
        bonus=bonus,
        advantage=advantage,
        disadvantage=disadvantage,
    )
    result = {
        **result,
        "type": roll_type,
        "actor_id": actor_id,
        "subject": subject,
        "ability": ability_key,
        "ability_score": score,
        "ability_modifier": ability_modifier(score),
        "proficiency_value": prof,
        "proficiency_multiplier": multiplier,
        "source": source,
        "breakdown": {
            "d20": result["natural"],
            "ability_modifier": ability_modifier(score),
            "proficiency_bonus": result["proficiency_bonus"],
            "bonus": bonus,
        },
    }
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="roll",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        rolls=[result],
        narration_hints=[f"{actor.name} rolls {roll_type}: {result['total']}."],
        flags={"dnd5e": {"roll_type": roll_type, "subject": subject}},
    )
    return {"roll": result, "messages": [asdict(message)]}


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _ability_key(value: str) -> str:
    key = _normalize(value)
    if key not in ABILITY_ALIASES:
        raise ValueError(f"unknown ability: {value}")
    return ABILITY_ALIASES[key]


def _level(system: dict[str, Any]) -> int:
    return int(system.get("level") or system.get("details", {}).get("level") or 1)


def _proficiency(system: dict[str, Any], level: int) -> int:
    return int(system.get("attributes", {}).get("prof") or proficiency_bonus(level))


def _ability_score(system: dict[str, Any], ability: str) -> int:
    abilities = dict(system.get("abilities") or {})
    value = abilities.get(ability)
    if value is None:
        long_name = next((name for name, short in ABILITY_ALIASES.items() if short == ability and len(name) > 3), "")
        value = abilities.get(long_name)
    if isinstance(value, dict):
        return int(value.get("value", 10))
    return int(value or 10)


def _skill_multiplier(system: dict[str, Any], skill: str) -> int:
    data = dict(system.get("skills") or {}).get(skill, {})
    if isinstance(data, dict):
        if data.get("expertise"):
            return 2
        if data.get("proficient"):
            return 1
        return int(data.get("prof", 0) or 0)
    return int(data or 0)


def _save_multiplier(system: dict[str, Any], ability: str) -> int:
    data = dict(system.get("abilities") or {}).get(ability, {})
    if isinstance(data, dict):
        return 1 if data.get("save_proficient") or data.get("proficient") else int(data.get("saveProf", 0) or 0)
    return 0


def _initiative_multiplier(system: dict[str, Any]) -> int:
    return int(system.get("attributes", {}).get("init", {}).get("prof", 0) or 0)
