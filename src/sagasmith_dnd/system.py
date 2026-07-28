"""D&D system definition and character-sheet validation."""

from __future__ import annotations

from sagasmith_core.systems import SystemDefinition

from sagasmith_dnd.character_schema import validate_character_sheet

DND5E = SystemDefinition(
    id="dnd5e",
    display_name="Dungeons & Dragons 5e",
    character_types=("pc", "npc", "monster"),
    # Campaign settings contain configuration only. Runtime combat, initiative,
    # world state, edition, and locale each have a dedicated authority.
    campaign_defaults={},
    validate_sheet=validate_character_sheet,
)


def get_system() -> SystemDefinition:
    return DND5E
