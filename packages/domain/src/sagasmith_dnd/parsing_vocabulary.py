"""Versioned D&D vocabulary used by document-to-catalog parsing.

These values are ruleset data, not evidence that a source span represents an
entity.  Keeping them here prevents document-layout recovery and source review
decisions from being mixed into vocabulary tables.
"""

from __future__ import annotations

DND5E_2014_VOCABULARY_VERSION = "5e-2014-v1"
DND5E_2024_VOCABULARY_VERSION = "5e-2024-v1"

DND5E_2014_CLASS_NAMES = frozenset(
    {
        "artificer",
        "barbarian",
        "bard",
        "blood hunter",
        "cleric",
        "druid",
        "fighter",
        "monk",
        "paladin",
        "ranger",
        "rogue",
        "sorcerer",
        "warlock",
        "wizard",
    }
)

DND5E_2024_CLASS_NAMES = frozenset(
    {
        "Barbarian",
        "Bard",
        "Cleric",
        "Druid",
        "Fighter",
        "Monk",
        "Paladin",
        "Ranger",
        "Rogue",
        "Sorcerer",
        "Warlock",
        "Wizard",
    }
)

DND5E_ITEM_CATEGORY_LABELS = {
    "armor": "Armor",
    "potion": "Potion",
    "ring": "Ring",
    "rod": "Rod",
    "scroll": "Scroll",
    "staff": "Staff",
    "wand": "Wand",
    "weapon": "Weapon",
    "wondrousitem": "Wondrous item",
}

DND5E_STATBLOCK_FIELD_LABELS = ("armor class", "hit points", "speed", "challenge")

DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES = {
    "arcane tradition": "Wizard",
    "artificer specialists": "Artificer",
    "bard college": "Bard",
    "divine domain": "Cleric",
    "druid circle": "Druid",
    "martial archetype": "Fighter",
    "monastic tradition": "Monk",
    "otherworldly patron": "Warlock",
    "primal path": "Barbarian",
    "ranger archetype": "Ranger",
    "ranger conclave": "Ranger",
    "roguish archetype": "Rogue",
    "sacred oath": "Paladin",
    "sorcerous origin": "Sorcerer",
}
DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES.update(
    {
        f"{title}s": class_name
        for title, class_name in tuple(DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES.items())
        if not title.endswith("s")
    }
)

# Edition-pinned identities used only to recognize subclass cards in flattened
# 2014 rules text.  They are vocabulary, never source-bound merge evidence.
DND5E_2014_STANDARD_SUBCLASS_TITLES = frozenset(
    {
        "arcane trickster",
        "assassin",
        "battle master",
        "beast master",
        "champion",
        "draconic bloodline",
        "eldritch knight",
        "hunter",
        "the archfey",
        "the fiend",
        "the great old one",
        "thief",
        "wild magic",
    }
)

__all__ = [
    "DND5E_2014_CLASS_NAMES",
    "DND5E_2014_SUBCLASS_PARENT_CLASS_NAMES",
    "DND5E_2014_STANDARD_SUBCLASS_TITLES",
    "DND5E_2014_VOCABULARY_VERSION",
    "DND5E_2024_CLASS_NAMES",
    "DND5E_2024_VOCABULARY_VERSION",
    "DND5E_ITEM_CATEGORY_LABELS",
    "DND5E_STATBLOCK_FIELD_LABELS",
]
