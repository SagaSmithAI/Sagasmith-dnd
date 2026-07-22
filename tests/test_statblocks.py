import pytest

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.statblocks import (
    StatblockImportError,
    apply_statblock_variant,
    parse_2014_statblock,
)

COMMONER = """### Commoner

*Medium humanoid (any race), any alignment*

**Armor Class** 10

**Hit Points** 4 (1d8)

**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 10 (+0) | 10 (+0) | 10 (+0) | 10 (+0) | 10 (+0) | 10 (+0) |

**Senses** passive Perception 10

**Languages** any one language (usually Common)

**Challenge** 0 (10 XP)

###### Actions

***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.
*Hit:* 2 (1d4) bludgeoning damage.
"""


BANDIT_CAPTAIN = """### Bandit Captain

*Medium humanoid (any race), any non-lawful alignment*

**Armor Class** 15 (studded leather)

**Hit Points** 65 (10d8 + 20)

**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 15 (+2) | 16 (+3) | 14 (+2) | 14 (+2) | 11 (+0) | 14 (+2) |

**Saving Throws** Str +4, Dex +5, Wis +2

**Skills** Athletics +4, Deception +4

**Senses** passive Perception 10

**Languages** any two languages

**Challenge** 2 (450 XP)

###### Actions

***Multiattack***. The captain makes three melee attacks: two with its scimitar and one with its
dagger. Or the captain makes two ranged attacks with its daggers.

***Scimitar***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 6 (1d6 + 3) slashing damage.

***Dagger***. *Melee or Ranged Weapon Attack:* +5 to hit, reach 5 ft. or range 20/60 ft.,
one target. *Hit:* 5 (1d4 + 2) piercing damage.

###### Reactions

***Parry***. The captain adds 2 to its AC against one melee attack that would hit it.
"""


def test_commoner_statblock_becomes_an_exact_executable_actor_sheet() -> None:
    parsed = parse_2014_statblock(
        COMMONER,
        source_key="srd-commoner",
        rule_refs=["chunk-commoner"],
    )
    derived = derive_character_sheet(parsed.sheet)

    assert parsed.name == "Commoner"
    assert parsed.challenge_rating == "0"
    assert parsed.experience_points == 10
    assert parsed.warnings == ()
    assert derived["armor_class"] == 10
    assert derived["hit_points"]["max"] == 4
    assert derived["speed"]["walk"] == 30
    assert derived["inventory"]["weapon_attacks"] == [
        derived["inventory"]["weapon_attacks"][0]
    ]
    club = derived["inventory"]["weapon_attacks"][0]
    assert club["item_id"] == "club"
    assert club["attack_bonus"] == 2
    assert club["damage_expression"] == "1d4"
    assert club["reach_ft"] == 5


def test_bandit_captain_preserves_exact_overrides_and_multiattack_composition() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="srd-bandit-captain",
        rule_refs=["chunk-bandit-captain"],
    )
    derived = derive_character_sheet(parsed.sheet)

    assert derived["armor_class"] == 15
    assert parsed.sheet["inventory"]["equipment_slots"]["armor"] == "statblock-studded-leather"
    assert derived["stealth_disadvantage"] is False
    assert derived["saving_throws"]["strength"] == 4
    assert derived["saving_throws"]["dexterity"] == 5
    assert derived["skills"]["athletics"] == 4
    assert derived["skills"]["deception"] == 4
    attacks = {item["item_id"]: item for item in derived["inventory"]["weapon_attacks"]}
    assert attacks["scimitar"]["attack_bonus"] == 5
    assert attacks["scimitar"]["damage_expression"] == "1d6 + 3"
    assert attacks["dagger"]["damage_expression"] == "1d4 + 2"
    assert attacks["dagger"]["thrown_range_ft"] == {"normal": 20, "long": 60}
    assert derived["attacks_per_action"] == 1
    options = {item["id"]: item["attacks"] for item in derived["multiattack_options"]}
    assert options["melee"] == [
        {"weapon_id": "scimitar", "attack_mode": "melee", "count": 2},
        {"weapon_id": "dagger", "attack_mode": "melee", "count": 1},
    ]
    assert options["ranged"] == [
        {"weapon_id": "dagger", "attack_mode": "ranged", "count": 2}
    ]
    assert parsed.warnings == ("Parry: descriptive reaction is not automatically settled",)


def test_statblock_explicit_heavy_armor_preserves_non_ac_mechanics_with_override() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "**Armor Class** 15 (studded leather)",
            "**Armor Class** 18 (chain mail, shield)",
        ),
        source_key="module-review:fist-of-bane",
    )
    derived = derive_character_sheet(parsed.sheet)

    assert derived["armor_class"] == 18
    assert parsed.sheet["inventory"]["equipment_slots"]["armor"] == "statblock-chain-mail"
    assert parsed.sheet["inventory"]["equipment_slots"]["shield"] == "statblock-shield"
    assert derived["stealth_disadvantage"] is True


def test_numeric_statblock_spell_attack_is_executable() -> None:
    parsed = parse_2014_statblock(
        """# Necromite of Myrkul

*Medium humanoid (human), neutral evil*

**Armor Class** 11
**Hit Points** 13 (2d8 + 4)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 10 (+0) | 13 (+1) | 15 (+2) | 16 (+3) | 11 (+0) | 10 (+0) |

**Skills** Arcana +5, Religion +5
**Senses** passive Perception 10
**Languages** Abyssal, Common, Infernal
**Challenge** 1/2 (100 XP)

## Actions

***Skull Flail***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.
*Hit:* 4 (1d8) bludgeoning damage.

***Claws of the Grave***. *Ranged Spell Attack:* +5 to hit, range 90 ft., one target.
*Hit:* 8 (2d4 + 3) necrotic damage.
""",
        source_key="module-review:necromite",
    )

    attacks = {item["name"]: item for item in parsed.sheet["inventory"]["items"]}
    claws = attacks["Claws of the Grave"]
    assert claws["mechanics"]["attack_type"] == "ranged"
    assert claws["mechanics"]["attack_ability"] == "spell"
    assert claws["mechanics"]["attack_bonus_override"] == 5
    assert claws["mechanics"]["damage_formula"] == "2d4"
    assert claws["mechanics"]["damage_bonus_override"] == 3
    assert claws["mechanics"]["damage_type"] == "necrotic"
    assert parsed.warnings == ()


def test_spellcasting_metadata_and_named_spell_actions_are_not_free_weapons() -> None:
    parsed = parse_2014_statblock(
        """# Master of Souls

*Medium humanoid (human), neutral evil*

**Armor Class** 12
**Hit Points** 45 (6d8 + 18)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 10 (+0) | 14 (+2) | 17 (+3) | 19 (+4) | 14 (+2) | 13 (+1) |

**Senses** passive Perception 12
**Languages** Common
**Challenge** 4 (1,100 XP)

***Spellcasting***. The master of souls is a 5th-level spellcaster. Its spellcasting
ability is Intelligence (spell save DC 14, +6 to hit with spell attacks). It has the
following wizard spells prepared:

Cantrips (at will): chill touch, mage hand

1st level (4 slots): ray of sickness, shield

2nd level (3 slots): scorching ray

## Actions

***Multiattack***. The master of souls makes two attacks with its silvered skull flail.

***Silvered Skull Flail***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.
*Hit:* 4 (1d8) bludgeoning damage.

***Chill Touch***. *Ranged Spell Attack:* +6 to hit, range 120 ft., one target.
*Hit:* 13 (2d8) necrotic damage.

***Ray of Sickness (1st-Level Spell; Requires a Spell Slot)***.
*Ranged Spell Attack:* +6 to hit, range 60 ft., one target.
*Hit:* 9 (2d8) poison damage.

***Scorching Ray (2nd-Level Spell; Requires a Spell Slot)***.
*Ranged Spell Attack:* +6 to hit, range 60 ft., one target.
*Hit:* 7 (2d6) fire damage.
""",
        source_key="module-review:master-of-souls",
    )
    derived = derive_character_sheet(parsed.sheet)

    assert parsed.spellcasting is not None
    assert parsed.spellcasting["ability"] == "intelligence"
    assert parsed.spellcasting["save_dc"] == 14
    assert parsed.spellcasting["attack_bonus"] == 6
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert [item["name"] for item in parsed.spellcasting["spells"]] == [
        "chill touch",
        "mage hand",
        "ray of sickness",
        "shield",
        "scorching ray",
    ]
    assert {
        item["name"]: item.get("action_description")
        for item in parsed.spellcasting["spells"]
        if item.get("action_description")
    }.keys() == {"chill touch", "ray of sickness", "scorching ray"}
    assert [item["item_id"] for item in derived["inventory"]["weapon_attacks"]] == [
        "silvered-skull-flail"
    ]
    assert derived["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [
                {"weapon_id": "silvered-skull-flail", "attack_mode": "melee", "count": 2}
            ],
        }
    ]
    assert parsed.warnings == ()


def test_source_bound_variant_can_apply_common_module_instance_changes() -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-commoner")

    sheet = apply_statblock_variant(
        parsed.sheet,
        {
            "source_ref": "module-scene:d12",
            "creature_type": "undead",
            "current_hit_points": 1,
            "armor_class": 12,
            "languages": ["Common", "Elvish"],
            "action_overrides": {
                "club": {
                    "id": "gauntlet-slam",
                    "name": "Gauntlet Slam",
                    "damage_type": "force",
                }
            },
        },
    )
    derived = derive_character_sheet(sheet)

    assert sheet["combat"]["hp"] == {"value": 1, "max": 4, "temp": 0}
    assert sheet["progression"]["species"] == "undead"
    assert derived["armor_class"] == 12
    assert sheet["traits"]["languages"] == ["Common", "Elvish"]
    assert derived["inventory"]["weapon_attacks"][0]["item_id"] == "gauntlet-slam"
    assert derived["inventory"]["weapon_attacks"][0]["damage_type"] == "force"
    attack = sheet["inventory"]["items"][0]
    assert "*Melee Weapon Attack:* +2 to hit" in attack["description"]
    assert "1d4 bludgeoning damage" not in attack["description"]
    assert "1d4 force damage" in attack["description"]
    assert "Variant source: module-scene:d12" in attack["description"]


def test_statblock_variant_rejects_unbound_or_broad_sheet_patches() -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-commoner")

    with pytest.raises(StatblockImportError, match="source_ref"):
        apply_statblock_variant(parsed.sheet, {"current_hit_points": 1})
    with pytest.raises(StatblockImportError, match="unsupported statblock variant fields"):
        apply_statblock_variant(
            parsed.sheet,
            {"source_ref": "module-scene:d12", "sheet": {"abilities": {}}},
        )
    with pytest.raises(StatblockImportError, match="exactly one weapon action"):
        apply_statblock_variant(
            parsed.sheet,
            {"source_ref": "module-scene:d12", "remove_actions": ["missing"]},
        )
    with pytest.raises(StatblockImportError, match="creature_type"):
        apply_statblock_variant(
            parsed.sheet,
            {"source_ref": "module-scene:d12", "creature_type": ""},
        )


def test_unresolved_multiattack_produces_one_specific_warning() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            "###### Actions\n\n***Multiattack***. The commoner attacks and shouts a command.",
        ),
        source_key="module-review:commanding-commoner",
    )

    assert parsed.warnings == (
        "Multiattack: Multiattack composition requires a DM ruling",
    )
