from sagasmith_dnd.abilities import (
    ABILITY_ABBREVIATIONS,
    ABILITY_IDS,
    ABILITY_LABELS,
    ABILITY_NAMES,
    SKILL_ABILITIES,
)
from sagasmith_dnd.ability_generation import ABILITY_NAMES as GENERATION_ABILITIES
from sagasmith_dnd.character_schema import (
    ABILITY_NAMES as SCHEMA_ABILITIES,
)
from sagasmith_dnd.character_schema import (
    SKILL_ABILITIES as SCHEMA_SKILL_ABILITIES,
)
from sagasmith_dnd.conditions import STANDARD_BINARY_CONDITION_IDS


def test_all_rules_paths_share_canonical_ability_identifiers() -> None:
    assert GENERATION_ABILITIES is ABILITY_NAMES
    assert SCHEMA_ABILITIES is ABILITY_NAMES
    assert SCHEMA_SKILL_ABILITIES is SKILL_ABILITIES
    assert ABILITY_IDS == set(ABILITY_NAMES)
    assert tuple(ABILITY_ABBREVIATIONS.values()) == ABILITY_NAMES
    assert ABILITY_LABELS == ("STR", "DEX", "CON", "INT", "WIS", "CHA")


def test_binary_condition_catalog_is_complete_without_numeric_exhaustion() -> None:
    assert len(STANDARD_BINARY_CONDITION_IDS) == 14
    assert {"invisible", "petrified", "unconscious"} <= STANDARD_BINARY_CONDITION_IDS
    assert "exhaustion" not in STANDARD_BINARY_CONDITION_IDS
