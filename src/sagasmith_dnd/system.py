"""D&D system definition and character-sheet validation."""

from __future__ import annotations

from sagasmith_core.systems import SystemDefinition

from sagasmith_dnd.character_schema import validate_character_sheet

DND5E = SystemDefinition(
    id="dnd5e",
    display_name="Dungeons & Dragons 5e",
    character_types=("pc", "npc", "monster"),
    campaign_defaults={
        "edition": "2024",
        "locale": "en",
        "initiative": [],
        "combat": None,
        "world": {},
    },
    validate_sheet=validate_character_sheet,
)


def get_system() -> SystemDefinition:
    return DND5E
