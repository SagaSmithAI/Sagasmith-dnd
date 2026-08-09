from sagasmith_dnd.parsing_vocabulary import (
    DND5E_2014_CLASS_NAMES,
    DND5E_2014_STANDARD_SUBCLASS_TITLES,
    DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES,
    DND5E_2014_VOCABULARY_VERSION,
    DND5E_2024_CLASS_NAMES,
    DND5E_2024_VOCABULARY_VERSION,
    DND5E_STATBLOCK_FIELD_LABELS,
)


def test_parsing_vocabulary_is_versioned_and_ruleset_scoped() -> None:
    assert DND5E_2014_VOCABULARY_VERSION == "5e-2014-v1"
    assert DND5E_2024_VOCABULARY_VERSION == "5e-2024-v1"
    assert "artificer" in DND5E_2014_CLASS_NAMES
    assert "Artificer" not in DND5E_2024_CLASS_NAMES
    assert DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES["divine domains"] == "Cleric"
    assert "the archfey" in DND5E_2014_STANDARD_SUBCLASS_TITLES
    assert DND5E_STATBLOCK_FIELD_LABELS == (
        "armor class",
        "hit points",
        "speed",
        "challenge",
    )
