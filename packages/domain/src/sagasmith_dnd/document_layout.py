"""D&D-specific document-layout hints for the system-neutral Core converter."""

from sagasmith_core.documents import DocumentLayoutProfile

_MECHANICAL_FIELD_PATTERN = (
    r"(?i)^(?:armor|weapons|tools|skills|saving throws|hit dice|"
    r"hit points at|armor class|hit points|speed|challenge|casting time|"
    r"range|components|duration)\s*:"
)

DND5E_DOCUMENT_LAYOUT_PROFILE = DocumentLayoutProfile(
    name="dnd5e",
    visual_heading_exclusion_patterns=(
        _MECHANICAL_FIELD_PATTERN,
        r"(?i)\b(?:Melee|Ranged)\s+(?:Weapon|Spell)\s+Attack\s*:",
    ),
    repeated_margin_exclusion_patterns=(_MECHANICAL_FIELD_PATTERN,),
)

__all__ = ["DND5E_DOCUMENT_LAYOUT_PROFILE"]
