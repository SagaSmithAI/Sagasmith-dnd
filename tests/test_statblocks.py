import pytest

from sagasmith_dnd.character_schema import derive_character_sheet, validate_character_sheet
from sagasmith_dnd.statblocks import (
    StatblockImportError,
    apply_statblock_variant,
    effective_statblock_rating,
    gazer_eye_ray_spec,
    parse_2014_statblock,
    source_contest_effect_spec,
    source_save_effect_spec,
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


TROLL = """## Troll

*Large giant, chaotic evil*

**Armor Class** 15 (natural armor)

**Hit Points** 84 (8d10+40)

**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 18 (+4) | 13 (+1) | 20 (+5) | 7 (-2) | 9 (-1) | 7 (-2) |

**Senses** darkvision 60 ft., passive Perception 12

**Languages** Giant

**Challenge** 5 (1,800 XP)

***Regeneration***. The troll regains 10 hit points at the start of its turn.
If the troll takes acid or fire damage, this trait doesn't function at the
start of the troll's next turn. The troll dies only if it starts its turn with
0 hit points and doesn't regenerate.

###### Actions

***Claw***. *Melee Weapon Attack:* +7 to hit, reach 5 ft., one target.
*Hit:* 11 (2d6+4) slashing damage.
"""

KOBOLD = """# Kobold

*Small humanoid (kobold), lawful evil*

**Armor Class** 12

**Hit Points** 5 (2d6 - 2)

**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 7 (-2) | 15 (+2) | 9 (-1) | 8 (-1) | 7 (-2) | 8 (-1) |

**Senses** darkvision 60 ft., passive Perception 8

**Languages** Common, Draconic

**Challenge** 1/8 (25 XP)

***Sunlight Sensitivity***. While in sunlight, the kobold has disadvantage on
attack rolls, as well as on Wisdom (Perception) checks that rely on sight.

***Pack Tactics***. The kobold has advantage on an attack roll against a creature
if at least one of the kobold's allies is within 5 feet of the creature and the
ally isn't incapacitated.

###### Actions

***Dagger***. *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
*Hit:* 4 (1d4 + 2) piercing damage.

***Sling***. *Ranged Weapon Attack:* +4 to hit, range 30/120 ft., one target.
*Hit:* 4 (1d4 + 2) bludgeoning damage.
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

GIANT_SPIDER = """### Giant Spider

*Large beast, unaligned*

**Armor Class** 14 (natural armor)

**Hit Points** 26 (4d10 + 4)

**Speed** 30 ft., climb 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 14 (+2) | 16 (+3) | 12 (+1) | 2 (-4) | 11 (+0) | 4 (-3) |

**Senses** blindsight 10 ft., darkvision 60 ft., passive Perception 10

**Languages** —

**Challenge** 1 (200 XP)

###### Actions

***Bite***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one creature.
*Hit:* 7 (1d8 + 3) piercing damage.

***Web (Recharge 5-6)***. *Ranged Weapon Attack:* +5 to hit, range 30/60 ft.,
one creature. *Hit:* The target is restrained by webbing. As an action, the
restrained target can make a DC 12 Strength check, bursting the webbing on a
success.
"""


GAZER = """### Gazer

*Tiny aberration, neutral evil*

**Armor Class** 13

**Hit Points** 13 (3d4 + 6)

**Speed** 0 ft., fly 30 ft. (hover)

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 3 (-4) | 17 (+3) | 14 (+2) | 3 (-4) | 10 (+0) | 7 (-2) |

**Saving Throws** Wis +2

**Skills** Perception +4, Stealth +5

**Senses** darkvision 60 ft., passive Perception 14

**Languages** —

**Challenge** 1/2 (100 XP)

###### Actions

***Bite***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 1 piercing damage.

***Eye Rays***. The gazer shoots two of the following magical eye rays at random
(reroll duplicates), choosing one or two targets it can see within 60 feet of it:

***Dazing Ray***. The targeted creature must succeed on a DC 12 Wisdom saving throw
or be charmed until the start of the gazer's next turn. While the target is charmed
in this way, its speed is halved, and it has disadvantage on attack rolls.

***Fear Ray***. The targeted creature must succeed on a DC 12 Wisdom saving throw or
be frightened until the start of the gazer's next turn.

***Frost Ray***. The targeted creature must succeed on a DC 12 Dexterity saving throw
or take 10 (3d6) cold damage.

***Telekinetic Ray***. If the target is a creature that is Medium or smaller, it must
succeed on a DC 12 Strength saving throw or be moved up to 30 feet directly away from
the gazer.
"""


INTELLECT_DEVOURER = """### Intellect Devourer

*Tiny aberration, lawful evil*

**Armor Class** 12

**Hit Points** 21 (6d4 + 6)

**Speed** 40 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 6 (-2) | 14 (+2) | 13 (+1) | 12 (+1) | 11 (+0) | 10 (+0) |

**Skills** Perception +2, Stealth +4

**Senses** blindsight 60 ft. (blind beyond this radius), passive Perception 12

**Languages** understands Deep Speech but can't speak, telepathy 60 ft.

**Challenge** 2 (450 XP)

###### Actions

***Multiattack***. The intellect devourer makes one attack with its claws and uses
Devour Intellect.

***Claws***. *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
*Hit:* 7 (2d4 + 2) slashing damage.

***Devour Intellect***. The intellect devourer targets one creature it can see
within 10 feet of it that has a brain. The target must succeed on a DC 12
Intelligence saving throw against this magic or take 11 (2d10) psychic damage.
Also on a failure, roll 3d6: If the total equals or exceeds the target's
Intelligence score, that score is reduced to 0. The target is stunned until it
regains at least one point of Intelligence.

***Body Thief***. The intellect devourer initiates an Intelligence contest with an
incapacitated humanoid within 5 feet of it. If it wins the contest, the intellect
devourer magically consumes the target's brain, teleports into the target's skull,
and takes control of the target's body. While inside a creature, the intellect
devourer has total cover against attacks and other effects originating outside its
host. The intellect devourer retains its Intelligence, Wisdom, and Charisma scores,
as well as its understanding of Deep Speech, its telepathy, and its traits. It
otherwise adopts the target's statistics. It knows everything the creature knew,
including spells and languages.

If the host body drops to 0 hit points, the intellect devourer must leave it. A
*protection from evil and good* spell cast on the body drives the intellect devourer
out. The intellect devourer is also forced out if the target regains its devoured
brain by means of a *wish*. By spending 5 feet of its movement, the intellect
devourer can voluntarily leave the body, teleporting to the nearest unoccupied
space within 5 feet of it. The body then dies, unless its brain is restored within
1 round.
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


def test_markdown_emphasized_monster_lore_after_last_action_is_not_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        COMMONER
        + """

**Commoners** include peasants, serfs, slaves, servants, pilgrims, merchants,
artisans, and hermits.
""",
        source_key="srd-commoner-with-lore",
    )
    club = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert club["on_hit_effect"] == ""
    assert parsed.warnings == (
        "Club: trailing creature prose excluded from action settlement",
    )


def test_flat_damage_weapon_is_executable_without_inventing_damage_dice() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace("*Hit:* 2 (1d4) bludgeoning damage.", "*Hit:* 1 piercing damage."),
        source_key="module-review:gazer",
    )
    attack = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert attack["damage_expression"] == "1"
    assert attack["damage_type"] == "piercing"
    assert attack["on_hit_effect"] == ""
    assert parsed.warnings == ()


def test_gazer_eye_rays_are_structured_from_the_exact_source_action() -> None:
    parsed = parse_2014_statblock(
        GAZER,
        source_key="module-review:waterdeep-gazer",
        rule_refs=["waterdeep-page-204"],
    )
    activities = parsed.sheet["content"]["activities"]
    eye_rays = next(item for item in activities if item["name"] == "Eye Rays")
    spec = gazer_eye_ray_spec(parsed.sheet, eye_rays["id"])

    assert spec is not None
    assert spec["draw_count"] == 2
    assert spec["reroll_duplicates"] is True
    assert spec["range_ft"] == 60
    assert spec["target_count"] == {"minimum": 1, "maximum": 2}
    assert [effect["id"] for effect in spec["effects"]] == [
        "dazing-ray",
        "fear-ray",
        "frost-ray",
        "telekinetic-ray",
    ]
    assert spec["effects"][0]["failure"] == {
        "kind": "timed_condition",
        "condition": "charmed",
        "duration": {"period": "source_turn_start", "remaining": 1},
        "speed_multiplier": 0.5,
        "attack_disadvantage": True,
    }
    assert spec["effects"][2]["failure"] == {
        "kind": "damage",
        "expression": "3d6",
        "damage_type": "cold",
    }
    assert spec["effects"][3]["failure"] == {
        "kind": "forced_movement",
        "maximum_size": "medium",
        "distance_ft": 30,
        "direction": "directly_away",
    }
    assert not {
        "Dazing Ray",
        "Fear Ray",
        "Frost Ray",
        "Telekinetic Ray",
    } & {item["name"] for item in activities}
    assert parsed.warnings == ()


def test_intellect_devourer_actions_are_structured_from_exact_source() -> None:
    parsed = parse_2014_statblock(
        INTELLECT_DEVOURER,
        source_key="reviewed-intellect-devourer",
        rule_refs=["monster-manual-page-191"],
    )
    derived = derive_character_sheet(parsed.sheet)
    devour = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Devour Intellect"
    )
    body_thief = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Body Thief"
    )

    assert source_save_effect_spec(parsed.sheet, devour["id"]) == {
        "kind": "intellect_devourer_devour_intellect_2014",
        "range_ft": 10,
        "target_count": 1,
        "target_requirement": "has_brain",
        "save": {"ability": "intelligence", "dc": 12},
        "failure": {
            "damage_expression": "2d10",
            "damage_type": "psychic",
            "secondary_roll": "3d6",
            "secondary_threshold": "target_intelligence_score",
            "ability_override": {"ability": "intelligence", "score": 0},
            "condition": "stunned",
            "ends_when": "target_intelligence_score_at_least_1",
        },
        "source_excerpt": " ".join(devour["description"].split()),
    }
    assert derived["multiattack_options"] == [
        {
            "id": "claws-and-devour-intellect",
            "attacks": [
                {"weapon_id": "claws", "attack_mode": "melee", "count": 1}
            ],
            "activities": [
                {"activity_id": "devour-intellect-action", "count": 1}
            ],
        }
    ]
    assert source_contest_effect_spec(parsed.sheet, body_thief["id"]) == {
        "kind": "intellect_devourer_body_thief_2014",
        "range_ft": 5,
        "target_count": 1,
        "target_requirements": ["incapacitated", "humanoid"],
        "contest": {
            "source_ability": "intelligence",
            "target_ability": "intelligence",
            "ties": "no_winner",
        },
        "success": {
            "brain_consumed": True,
            "source_inside_host": True,
            "source_total_cover": True,
            "source_retains": [
                "intelligence",
                "wisdom",
                "charisma",
                "deep_speech",
                "telepathy",
                "traits",
            ],
            "source_adopts": "target_statistics_otherwise",
            "knowledge_transfer": "all_target_knowledge",
            "host_zero_hp": "source_must_leave",
        },
        "source_excerpt": " ".join(body_thief["description"].split()),
    }
    assert parsed.warnings == (
        "Body Thief: protection, wish, and voluntary exit require DM settlement",
    )


def test_mixed_weapon_and_special_action_multiattack_stays_a_dm_boundary() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.",
            (
                "***Multiattack***. The commoner makes one attack with its club "
                "and uses Devour Intellect.\n\n"
                "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target."
            ),
        ),
        source_key="reviewed-mixed-multiattack",
    )
    derived = derive_character_sheet(parsed.sheet)

    assert derived["multiattack_options"] == []
    assert any(
        activity["name"] == "Multiattack"
        for activity in parsed.sheet["content"]["activities"]
    )
    assert "Multiattack: Multiattack composition requires a DM ruling" in parsed.warnings


def test_regeneration_statblock_trait_is_structured_without_a_descriptive_warning() -> None:
    parsed = parse_2014_statblock(TROLL, source_key="srd-troll")

    regeneration = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Regeneration"
    )

    assert regeneration["activation"]["trigger"] == "start of its turn"
    assert regeneration["choices"]["source_trait"] == {
        "kind": "regeneration",
        "trigger": "turn_start",
        "amount": 10,
        "suppressed_by_damage_types": ["acid", "fire"],
        "dies_at_zero_when_suppressed": True,
    }
    assert parsed.warnings == ()


def test_kobold_attack_traits_are_structured() -> None:
    parsed = parse_2014_statblock(KOBOLD, source_key="monster-manual-2014:p195")
    features = {
        item["name"]: item
        for item in parsed.sheet["content"]["features"]
    }

    assert features["Pack Tactics"]["choices"]["source_trait"] == {
        "kind": "pack_tactics",
        "trigger": "attack_roll",
        "ally_within_target_ft": 5,
        "requires_ally_not_incapacitated": True,
        "grants": "advantage",
        "automatic": True,
    }
    assert features["Sunlight Sensitivity"]["choices"]["source_trait"] == {
        "kind": "sunlight_sensitivity",
        "trigger": "attack_roll_or_sight_perception",
        "environment_fact": "direct_sunlight",
        "grants": "disadvantage",
        "automatic": True,
    }
    assert parsed.warnings == ()


def test_effect_only_weapon_attack_preserves_web_ruling_without_fake_damage() -> None:
    parsed = parse_2014_statblock(GIANT_SPIDER, source_key="module-review:giant-spider")
    attacks = {
        attack["item_id"]: attack
        for attack in derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"]
    }

    web = attacks["web-recharge-5-6"]
    assert web["attack_bonus"] == 5
    assert web["damage_expression"] == ""
    assert web["damage_type"] == ""
    assert web["on_hit_effect"].startswith("The target is restrained by webbing")
    assert parsed.warnings == ("Web (Recharge 5-6): on-hit effect requires DM settlement",)


def test_multiple_statblock_actions_can_share_one_normalized_line() -> None:
    parsed = parse_2014_statblock(
        """# GOBLIN

*Small humanoid (goblinoid), neutral evil*

**Armor Class** 15 (leather armor, shield)
**Hit Points** 7 (2d6)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 8 (-1) | 14 (+2) | 10 (+0) | 10 (+0) | 8 (-1) | 8 (-1) |
**Skills** Stealth +6
**Senses** darkvision 60 ft., passive Perception 9
**Languages** Common, Goblin
**Challenge** 1/4 (50 XP)

## Actions

***Scimitar***. Melee Weapon Attack: +4 to hit, reach 5 ft., one target.
Hit: 5 (1d6 + 2) slashing damage. ***Shortbow***. Ranged Weapon Attack:
+4 to hit, range 80 ft./320 ft., one target. Hit: 5 (1d6 + 2) piercing damage.
Goblins are black-hearted and gather in overwhelming numbers.
""",
        source_key="module-review:goblin",
        name="Goblin Ambusher 1",
    )

    assert parsed.name == "Goblin Ambusher 1"
    attacks = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"]
    assert [attack["item_id"] for attack in attacks] == ["scimitar", "shortbow"]
    assert attacks[1]["attack_type"] == "ranged"
    assert attacks[1]["range_ft"] == {"normal": 80, "long": 320}
    assert attacks[1]["on_hit_effect"] == ""
    assert parsed.warnings == (
        "Shortbow: trailing creature prose excluded from action settlement",
    )


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
    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )
    assert parry["activation"] == {
        "type": "reaction",
        "cost": 1,
        "trigger": "hit by a melee attack",
    }
    assert parry["choices"]["reaction_defense"] == {
        "kind": "armor_class_bonus",
        "bonus": 2,
        "attack_modes": ["melee"],
        "requires_visible_attacker": False,
        "requires_wielded_melee_weapon": False,
    }
    assert parsed.warnings == ()


def test_generic_multiattack_uses_only_unambiguous_compatible_weapon() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            (
                "###### Actions\n\n"
                "***Multiattack***. The commoner makes two melee attacks."
            ),
        ),
        source_key="module-review:generic-multiattack",
    )
    derived = derive_character_sheet(parsed.sheet)

    assert derived["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [{"weapon_id": "club", "attack_mode": "melee", "count": 2}],
        }
    ]
    assert parsed.warnings == ()


def test_generic_multiattack_requires_one_compatible_weapon() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            (
                "The captain makes three melee attacks: two with its scimitar and one with its\n"
                "dagger. Or the captain makes two ranged attacks with its daggers."
            ),
            "The captain makes two melee weapon attacks.",
        ),
        source_key="module-review:ambiguous-generic-multiattack",
    )

    assert derive_character_sheet(parsed.sheet)["multiattack_options"] == []
    assert parsed.warnings == ("Multiattack: Multiattack composition requires a DM ruling",)


def test_source_parry_preserves_visibility_and_wielded_weapon_requirements() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            (
                "The captain adds 2 to its AC against one melee attack that would hit it. "
                "To do so, the captain must see the attacker and be wielding a melee weapon."
            ),
        ),
        source_key="module-review:nimblewright",
    )

    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )
    assert parry["choices"]["reaction_defense"] == {
        "kind": "armor_class_bonus",
        "bonus": 2,
        "attack_modes": ["melee"],
        "requires_visible_attacker": True,
        "requires_wielded_melee_weapon": True,
    }
    assert parsed.warnings == ()


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
    assert parsed.spellcasting["class_lists"] == ["wizard"]
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert derived["spellcasting"]["attack_bonus"] == 6
    assert derived["spellcasting"]["save_dc"] == 14
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


def test_statblock_weapon_preserves_additional_damage_and_on_hit_ruling() -> None:
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

## Actions

***Silvered Skull Flail***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.
*Hit:* 4 (1d8) bludgeoning damage plus 14 (4d6) necrotic damage. Until the end of
the target's next turn, it has disadvantage on saving throws against effects that
turn undead.
""",
        source_key="module-review:master-of-souls",
    )
    attack = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert attack["damage_expression"] == "1d8"
    assert attack["additional_damage"] == [
        {
            "damage_formula": "4d6",
            "damage_bonus": 0,
            "damage_type": "necrotic",
            "damage_expression": "4d6",
        }
    ]
    assert attack["on_hit_effect"].startswith("Until the end of the target's next turn")
    assert parsed.warnings == (
        "Silvered Skull Flail: on-hit effect requires DM settlement",
    )


def test_source_bound_variant_can_apply_common_module_instance_changes() -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-commoner")

    sheet = apply_statblock_variant(
        parsed.sheet,
        {
            "source_ref": "module-scene:d12",
            "creature_type": "undead",
            "current_hit_points": 1,
            "armor_class": 12,
            "alignment": "chaotic evil",
            "darkvision_ft": 60,
            "languages": ["Common", "Elvish"],
            "relentless_endurance": {
                "feature_id": "relentless-endurance",
                "source_excerpt": (
                    "When reduced to 0 hit points, he drops to 1 hit point instead "
                    "(but can't do this again until he finishes a long rest)."
                ),
            },
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
    assert sheet["traits"]["alignment"] == "chaotic evil"
    assert sheet["traits"]["senses"]["darkvision"] == 60
    assert sheet["traits"]["languages"] == ["Common", "Elvish"]
    feature = next(
        item
        for item in sheet["content"]["features"]
        if item["id"] == "relentless-endurance"
    )
    assert {
        key: feature["uses"][key]
        for key in ("label", "value", "max", "recovers_on")
    } == {
        "label": "uses",
        "value": 1,
        "max": 1,
        "recovers_on": "long_rest",
    }
    assert feature["choices"]["source_trait"] == {
        "kind": "relentless_endurance",
        "trigger": "reduced_to_zero",
        "drop_to_hit_points": 1,
        "requires_not_killed_outright": True,
        "automatic": True,
    }
    assert derived["inventory"]["weapon_attacks"][0]["item_id"] == "gauntlet-slam"
    assert derived["inventory"]["weapon_attacks"][0]["damage_type"] == "force"
    attack = sheet["inventory"]["items"][0]
    assert "*Melee Weapon Attack:* +2 to hit" in attack["description"]
    assert "1d4 bludgeoning damage" not in attack["description"]
    assert "1d4 force damage" in attack["description"]
    assert "Variant source: module-scene:d12" in attack["description"]


def test_source_bound_variant_can_apply_a_complete_spellcaster_instance() -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-spellcaster")
    sheet = parsed.sheet
    sheet["spellcasting"]["ability"] = "intelligence"
    sheet["spellcasting"]["spell_slots"] = {
        "3": {
            "label": "Level 3 spell slots",
            "value": 3,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "srd-spellcaster",
            "slot_level": 3,
        },
        "4": {
            "label": "Level 4 spell slots",
            "value": 3,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "srd-spellcaster",
            "slot_level": 4,
        },
    }
    sheet["content"]["spells"] = [
        {
            "id": spell_id,
            "source_key": "srd-spellcaster",
            "name": name,
            "level": level,
            "access": {
                "known": True,
                "prepared": prepared,
                "always_prepared": prepared,
                "in_spellbook": False,
                "ritual_available": False,
                "at_will": False,
            },
        }
        for spell_id, name, level, prepared in (
            ("counterspell", "Counterspell", 3, True),
            ("greater-invisibility", "Greater Invisibility", 4, True),
            ("animate-dead", "Animate Dead", 3, False),
            ("blight", "Blight", 4, False),
        )
    ]
    sheet["spellcasting"]["preparation"] = {
        "mode": "known",
        "max_prepared": 2,
        "changes_on": "manual",
        "selected_spell_ids": ["counterspell", "greater-invisibility"],
    }

    result = apply_statblock_variant(
        validate_character_sheet(sheet),
        {
            "source_ref": "module-chunk:losser",
            "size": "small",
            "walking_speed_ft": 25,
            "maximum_hit_points": 31,
            "current_hit_points": 31,
            "alignment": "chaotic evil",
            "languages": ["Common", "Halfling"],
            "spell_replacements": [
                {
                    "remove_spell_id": "counterspell",
                    "add_spell_id": "animate-dead",
                },
                {
                    "remove_spell_id": "greater-invisibility",
                    "add_spell_id": "blight",
                },
            ],
            "expend_all_spell_slots": True,
            "add_features": [
                {
                    "id": "halfling-nimbleness",
                    "name": "Halfling Nimbleness",
                    "description": (
                        "The actor can move through the space of a Medium or "
                        "larger creature."
                    ),
                },
                {
                    "id": "brave",
                    "name": "Brave",
                    "description": (
                        "The actor has advantage on saving throws against being "
                        "frightened."
                    ),
                },
            ],
        },
    )
    derived = derive_character_sheet(result)

    assert result["traits"]["size"] == "small"
    assert result["combat"]["speed"]["walk"] == 25
    assert result["combat"]["hp"] == {"value": 31, "max": 31, "temp": 0}
    assert result["traits"]["alignment"] == "chaotic evil"
    assert result["traits"]["languages"] == ["Common", "Halfling"]
    assert set(derived["spellcasting"]["prepared_spell_ids"]) == {
        "animate-dead",
        "blight",
    }
    assert all(
        slot["value"] == 0
        for slot in result["spellcasting"]["spell_slots"].values()
    )
    assert {
        item["id"] for item in result["content"]["features"]
    } >= {"halfling-nimbleness", "brave"}
    assert {
        item["id"] for item in result["content"]["spells"]
    } == {"animate-dead", "blight"}


def test_source_bound_variant_can_remove_confiscated_gear_and_dependent_activities() -> None:
    parsed = parse_2014_statblock(BANDIT_CAPTAIN, source_key="srd-bandit-captain")

    sheet = apply_statblock_variant(
        parsed.sheet,
        {
            "source_refs": [
                "module-chunk:prisoner-condition",
                "module-chunk:prisoner-gear",
            ],
            "armor_class": 10,
            "remove_items": ["Studded Leather", "Scimitar", "Dagger"],
            "remove_activities": ["Parry"],
        },
    )
    derived = derive_character_sheet(sheet)

    assert sheet["inventory"]["items"] == []
    assert sheet["inventory"]["equipment_slots"]["armor"] is None
    assert derived["armor_class"] == 10
    assert derived["inventory"]["weapon_attacks"] == []
    assert derived["multiattack_options"] == []
    assert sheet["content"]["activities"] == []


def test_source_bound_variant_can_remove_weapon_riders_and_override_encounter_rating() -> None:
    parsed = parse_2014_statblock(
        """# Assassin

*Medium humanoid (any race), any non-good alignment*

**Armor Class** 15 (studded leather)
**Hit Points** 78 (12d8 + 24)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 11 (+0) | 16 (+3) | 14 (+2) | 13 (+1) | 11 (+0) | 10 (+0) |

**Senses** passive Perception 14
**Languages** Thieves' cant plus any two languages
**Challenge** 8 (3,900 XP)

## Actions

***Multiattack***. The assassin makes two shortsword attacks.

***Shortsword***. *Melee Weapon Attack:* +7 to hit, reach 5 ft., one target.
*Hit:* 6 (1d6 + 3) piercing damage, and the target must make a DC 15 Constitution
saving throw, taking 24 (7d6) poison damage on a failed save, or half as much
damage on a successful one.

***Light Crossbow***. *Ranged Weapon Attack:* +7 to hit, range 80/320 ft., one target.
*Hit:* 7 (1d8 + 3) piercing damage, and the target must make a DC 15 Constitution
saving throw, taking 24 (7d6) poison damage on a failed save, or half as much
damage on a successful one.
""",
        source_key="srd-assassin",
    )
    variant = {
        "source_ref": "module-chunk:gralhund-g15",
        "maximum_hit_points": 50,
        "challenge_rating": "3",
        "experience_points": 700,
        "action_overrides": {
            "shortsword": {"remove_on_hit_effect": True},
            "light-crossbow": {"remove_on_hit_effect": True},
        },
    }

    sheet = apply_statblock_variant(parsed.sheet, variant)
    derived = derive_character_sheet(sheet)
    attacks = {
        attack["item_id"]: attack
        for attack in derived["inventory"]["weapon_attacks"]
    }

    assert sheet["combat"]["hp"] == {"value": 50, "max": 50, "temp": 0}
    assert effective_statblock_rating(
        parsed.challenge_rating,
        parsed.experience_points,
        variant,
    ) == ("3", 700)
    assert attacks["shortsword"]["damage_expression"] == "1d6 + 3"
    assert attacks["shortsword"]["on_hit_effect"] == ""
    assert attacks["light-crossbow"]["damage_expression"] == "1d8 + 3"
    assert attacks["light-crossbow"]["on_hit_effect"] == ""
    assert derived["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [
                {
                    "weapon_id": "shortsword",
                    "attack_mode": "melee",
                    "count": 2,
                }
            ],
        }
    ]
    assert all("Multiattack composition" not in warning for warning in parsed.warnings)
    assert all(
        "DC 15" not in item["description"] and "poison" not in item["description"]
        for item in sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    )


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
    with pytest.raises(StatblockImportError, match="overridden together"):
        apply_statblock_variant(
            parsed.sheet,
            {"source_ref": "module-scene:d12", "challenge_rating": "3"},
        )
    with pytest.raises(StatblockImportError, match="challenge XP table"):
        apply_statblock_variant(
            parsed.sheet,
            {
                "source_ref": "module-scene:d12",
                "challenge_rating": "3",
                "experience_points": 701,
            },
        )
    with pytest.raises(StatblockImportError, match="must be true"):
        apply_statblock_variant(
            parsed.sheet,
            {
                "source_ref": "module-scene:d12",
                "action_overrides": {"club": {"remove_on_hit_effect": False}},
            },
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
