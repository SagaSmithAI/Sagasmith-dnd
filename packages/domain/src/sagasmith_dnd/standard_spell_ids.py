"""Stable identifiers for source-bound standard spell mechanics."""

STANDARD_2014_CONTENT_PACK_ID = "dnd5e.content.standard2014"
STANDARD_2014_CONTENT_PACK_VERSION = "1.5.0"

# The 2014 SRD publishes these otherwise-identical spells under generic names.
# Importers may use the mapping only for an official 2014 source and must keep
# the printed source name on the resulting card.
SRD2014_RENAMED_SPELLS = {
    "Bigby's Hand": "Arcane Hand",
    "Drawmij's Instant Summons": "Instant Summons",
    "Evard's Black Tentacles": "Black Tentacles",
    "Leomund's Secret Chest": "Secret Chest",
    "Leomund's Tiny Hut": "Tiny Hut",
    "Melf's Acid Arrow": "Acid Arrow",
    "Mordenkainen's Faithful Hound": "Faithful Hound",
    "Mordenkainen's Magnificent Mansion": "Magnificent Mansion",
    "Mordenkainen's Private Sanctum": "Private Sanctum",
    "Mordenkainen's Sword": "Arcane Sword",
    "Nystul's Magic Aura": "Arcanist's Magic Aura",
    "Otiluke's Freezing Sphere": "Freezing Sphere",
    "Otiluke's Resilient Sphere": "Resilient Sphere",
    "Otto's Irresistible Dance": "Irresistible Dance",
    "Tasha's Hideous Laughter": "Hideous Laughter",
    "Tenser's Floating Disk": "Floating Disk",
}

# Canonical identities printed in the 2014 Player's Handbook but absent from
# the SRD under either their printed or generic renamed identity.  This is an
# identity catalog, not a substitute rules text: imported mechanics and source
# citations must still come from the user's reviewed book.
PHB2014_NON_SRD_SPELL_NAMES = (
    "Arcane Gate",
    "Armor of Agathys",
    "Arms of Hadar",
    "Aura of Life",
    "Aura of Purity",
    "Aura of Vitality",
    "Banishing Smite",
    "Beast Sense",
    "Blade Ward",
    "Blinding Smite",
    "Chromatic Orb",
    "Circle of Power",
    "Cloud of Daggers",
    "Compelled Duel",
    "Conjure Barrage",
    "Conjure Volley",
    "Cordon of Arrows",
    "Crown of Madness",
    "Crusader's Mantle",
    "Destructive Wave",
    "Dissonant Whispers",
    "Elemental Weapon",
    "Ensnaring Strike",
    "Feign Death",
    "Friends",
    "Grasping Vine",
    "Hail of Thorns",
    "Hex",
    "Hunger of Hadar",
    "Lightning Arrow",
    "Phantasmal Force",
    "Power Word Heal",
    "Rary's Telepathic Bond",
    "Ray of Sickness",
    "Searing Smite",
    "Staggering Smite",
    "Swift Quiver",
    "Telepathy",
    "Thorn Whip",
    "Thunderous Smite",
    "Tsunami",
    "Witch Bolt",
    "Wrathful Smite",
)

CORE_BLADE_WARD_MECHANIC_ID = "dnd5e.core.spell.blade_ward"
CORE_BLADE_WARD_SPELL_ID = f"{STANDARD_2014_CONTENT_PACK_ID}.spell.blade-ward"
CORE_FLY_MECHANIC_ID = "dnd5e.core.spell.fly"
CORE_FLY_SPELL_ID = "dnd5e.content.srd2014.spell.fly"
CORE_2024_FLY_SPELL_ID = "dnd5e.content.srd2024.spell.fly"
CORE_FLY_SPELL_IDS = frozenset({CORE_FLY_SPELL_ID, CORE_2024_FLY_SPELL_ID})
CORE_INVISIBILITY_MECHANIC_ID = "dnd5e.core.spell.invisibility"
CORE_INVISIBILITY_SPELL_ID = "dnd5e.content.srd2014.spell.invisibility"
CORE_2024_INVISIBILITY_SPELL_ID = "dnd5e.content.srd2024.spell.invisibility"
CORE_INVISIBILITY_SPELL_IDS = frozenset(
    {CORE_INVISIBILITY_SPELL_ID, CORE_2024_INVISIBILITY_SPELL_ID}
)
CORE_HYPNOTIC_PATTERN_MECHANIC_ID = "dnd5e.core.spell.hypnotic_pattern"
CORE_HYPNOTIC_PATTERN_SPELL_ID = "dnd5e.content.srd2014.spell.hypnotic-pattern"
CORE_2024_HYPNOTIC_PATTERN_SPELL_ID = "dnd5e.content.srd2024.spell.hypnotic-pattern"
CORE_HYPNOTIC_PATTERN_SPELL_IDS = frozenset(
    {CORE_HYPNOTIC_PATTERN_SPELL_ID, CORE_2024_HYPNOTIC_PATTERN_SPELL_ID}
)
CORE_SLEEP_MECHANIC_ID = "dnd5e.core.spell.sleep"
CORE_SLEEP_SPELL_ID = "dnd5e.content.srd2014.spell.sleep"
CORE_WITCH_BOLT_MECHANIC_ID = "dnd5e.core.spell.witch_bolt"
CORE_WITCH_BOLT_SPELL_ID = f"{STANDARD_2014_CONTENT_PACK_ID}.spell.witch-bolt"
CORE_DESTRUCTIVE_WAVE_SPELL_ID = f"{STANDARD_2014_CONTENT_PACK_ID}.spell.destructive-wave"

__all__ = [
    "CORE_BLADE_WARD_MECHANIC_ID",
    "CORE_BLADE_WARD_SPELL_ID",
    "CORE_DESTRUCTIVE_WAVE_SPELL_ID",
    "CORE_2024_FLY_SPELL_ID",
    "CORE_FLY_MECHANIC_ID",
    "CORE_FLY_SPELL_ID",
    "CORE_FLY_SPELL_IDS",
    "CORE_2024_INVISIBILITY_SPELL_ID",
    "CORE_INVISIBILITY_MECHANIC_ID",
    "CORE_INVISIBILITY_SPELL_ID",
    "CORE_INVISIBILITY_SPELL_IDS",
    "CORE_2024_HYPNOTIC_PATTERN_SPELL_ID",
    "CORE_HYPNOTIC_PATTERN_MECHANIC_ID",
    "CORE_HYPNOTIC_PATTERN_SPELL_ID",
    "CORE_HYPNOTIC_PATTERN_SPELL_IDS",
    "CORE_SLEEP_MECHANIC_ID",
    "CORE_SLEEP_SPELL_ID",
    "CORE_WITCH_BOLT_MECHANIC_ID",
    "CORE_WITCH_BOLT_SPELL_ID",
    "PHB2014_NON_SRD_SPELL_NAMES",
    "STANDARD_2014_CONTENT_PACK_ID",
    "STANDARD_2014_CONTENT_PACK_VERSION",
    "SRD2014_RENAMED_SPELLS",
]
