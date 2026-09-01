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
CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID = "dnd5e.core.ac.tortle_natural_armor"
TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID = (
    "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da"
)
TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID = (
    "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da.addon"
)
TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION = "1.0.1"
TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM = (
    "356cbf231ea7ecf8dab48b7cca127523d1d0bd3f5b5535899f45641f24bc5759"
)
TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS = frozenset({"1.0.0"})
TORTLE_NATURAL_ARMOR_ARTIFACT_ID = (
    "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da.species.tortle"
)
TORTLE_NATURAL_ARMOR_SOURCE_KEY = "user.rulebook.d-d-5e-the-tortle-package.e3234de670"
TORTLE_NATURAL_ARMOR_SOURCE_RULE_REF_PREFIX = (
    f"rule-source:{TORTLE_NATURAL_ARMOR_SOURCE_KEY}#chunk:"
)
TORTLE_NATURAL_ARMOR_SOURCE_RULE_REFS = (
    "rule-source:user.rulebook.d-d-5e-the-tortle-package.e3234de670#chunk:"
    "user.rulebook.d-d-5e-the-tortle-package.e3234de670/section-9/chunk-10-fb5a021f5935d9e8",
    "rule-source:user.rulebook.d-d-5e-the-tortle-package.e3234de670#chunk:"
    "user.rulebook.d-d-5e-the-tortle-package.e3234de670/section-9/chunk-11-29741c0b8fe1d411",
)
TORTLE_NATURAL_ARMOR_AUTHORITY_KEY = "sagasmith.official_expansion_authority"
CORE_ORC_AGGRESSIVE_MECHANIC_ID = "dnd5e.core.activity.orc_aggressive"
ORC_AGGRESSIVE_ACTIVITY_ID = "aggressive-bonus_action"
CORE_RELENTLESS_ENDURANCE_MECHANIC_ID = "dnd5e.core.damage.relentless_endurance"

__all__ = [
    "CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID",
    "CORE_ORC_AGGRESSIVE_MECHANIC_ID",
    "CORE_RELENTLESS_ENDURANCE_MECHANIC_ID",
    "CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID",
    "ORC_AGGRESSIVE_ACTIVITY_ID",
    "SRD2014_DWARF_SPEED_LEGACY_ARTIFACT_IDS",
    "SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS",
    "SRD2014_DWARF_SPEED_SOURCE_RULE_REF",
    "TORTLE_NATURAL_ARMOR_ARTIFACT_ID",
    "TORTLE_NATURAL_ARMOR_AUTHORITY_KEY",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION",
    "TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID",
    "TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS",
    "TORTLE_NATURAL_ARMOR_SOURCE_KEY",
    "TORTLE_NATURAL_ARMOR_SOURCE_RULE_REF_PREFIX",
    "TORTLE_NATURAL_ARMOR_SOURCE_RULE_REFS",
]
