"""D&D-specific document-layout hints for the system-neutral Core converter."""

from sagasmith_core.documents import DocumentLayoutProfile

DND5E_DOCUMENT_LAYOUT_PROFILE = DocumentLayoutProfile(
    name="dnd5e",
    visual_heading_exclusion_patterns=(
        (
            r"(?i)^(?:armor|weapons|tools|skills|saving throws|hit dice|"
            r"hit points at|casting time|range|components|duration)\s*:"
        ),
        r"(?i)\b(?:Melee|Ranged)\s+(?:Weapon|Spell)\s+Attack\s*:",
    ),
)

__all__ = ["DND5E_DOCUMENT_LAYOUT_PROFILE"]
