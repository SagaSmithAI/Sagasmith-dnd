"""D&D system definition and character-sheet validation."""

from __future__ import annotations

from typing import Any

from sagasmith_core.systems import SystemDefinition


def validate_character_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(sheet)
    abilities = dict(normalized.get("abilities", {}))
    for ability in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        value = int(abilities.get(ability, 10))
        if not 1 <= value <= 30:
            raise ValueError(f"{ability} must be between 1 and 30")
        abilities[ability] = value
    normalized["abilities"] = abilities
    normalized["level"] = max(1, min(20, int(normalized.get("level", 1))))
    normalized["armor_class"] = max(0, int(normalized.get("armor_class", 10)))
    normalized["hit_points"] = max(0, int(normalized.get("hit_points", 1)))
    normalized["max_hit_points"] = max(
        normalized["hit_points"],
        int(normalized.get("max_hit_points", normalized["hit_points"])),
    )
    normalized.setdefault("class", "")
    normalized.setdefault("species", "")
    normalized.setdefault("background", "")
    normalized.setdefault("proficiencies", [])
    normalized.setdefault("spells", [])
    return normalized


DND5E = SystemDefinition(
    id="dnd5e",
    display_name="Dungeons & Dragons 5e",
    character_types=("pc", "npc", "monster"),
    campaign_defaults={
        "edition": "2024",
        "locale": "en",
        "initiative": [],
        "combat": None,
        "time": {},
        "rests": {},
        "map": {"active_scene_id": None},
        "world": {},
    },
    validate_sheet=validate_character_sheet,
)


def get_system() -> SystemDefinition:
    return DND5E
