"""Stable mechanic identifiers for engine-settled standard character features."""

CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID = "dnd5e.core.movement.dwarf_heavy_armor_speed"
SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS = frozenset({"1.24.0", "1.25.0"})
SRD2014_DWARF_SPEED_LEGACY_ARTIFACT_IDS = frozenset(
    {
        "dnd5e.content.srd2014.species.dwarf",
        "dnd5e.content.srd2014.species.hill-dwarf",
    }
)
SRD2014_DWARF_SPEED_SOURCE_RULE_REF = "bundled:srd2014/01_Races/Races_Each/Dwarf.md"
CORE_ORC_AGGRESSIVE_MECHANIC_ID = "dnd5e.core.activity.orc_aggressive"
ORC_AGGRESSIVE_ACTIVITY_ID = "aggressive-bonus_action"
CORE_RELENTLESS_ENDURANCE_MECHANIC_ID = "dnd5e.core.damage.relentless_endurance"

__all__ = [
    "CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID",
    "CORE_ORC_AGGRESSIVE_MECHANIC_ID",
    "CORE_RELENTLESS_ENDURANCE_MECHANIC_ID",
    "ORC_AGGRESSIVE_ACTIVITY_ID",
    "SRD2014_DWARF_SPEED_LEGACY_ARTIFACT_IDS",
    "SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS",
    "SRD2014_DWARF_SPEED_SOURCE_RULE_REF",
]
