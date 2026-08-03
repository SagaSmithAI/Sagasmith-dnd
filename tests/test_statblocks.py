from copy import deepcopy

import pytest

from sagasmith_dnd.activity_identity import (
    MULTIATTACK_MECHANIC_ID,
    is_multiattack_source_name,
)
from sagasmith_dnd.character_schema import derive_character_sheet, validate_character_sheet
from sagasmith_dnd.statblocks import (
    StatblockImportError,
    _ocr_ability_score_matches,
    _ocr_column_split,
    _ocr_probable_peer_heading,
    _repair_layout_ocr_text,
    _structure_gazer_eye_rays,
    _structure_intellect_devourer_actions,
    apply_dependent_actor_template_variant,
    apply_reviewed_statblock_fill,
    apply_statblock_variant,
    area_save_damage_spec,
    discover_2014_statblock_names_from_layout,
    effective_statblock_rating,
    finalize_imported_actor_rulings,
    gazer_eye_ray_spec,
    materialize_parameterized_statblock_source,
    parameterized_statblock_requirements,
    parse_2014_statblock,
    recover_2014_statblock_from_ocr,
    source_contest_effect_spec,
    source_save_effect_spec,
)


def test_parameterized_statblock_requirements_keep_companion_hp_source_bound() -> None:
    source = (
        "# Steel Defender\n\n"
        "**Armor Class** 15 (natural armor)\n"
        "**Hit Points** equal the steel defender's Constitution modifier + "
        "your Intelligence modifier + five times your level in this class\n"
    )

    requirement = parameterized_statblock_requirements(source)

    assert requirement is not None
    assert {
        key: requirement[key]
        for key in (
            "schema_version",
            "kind",
            "target_path",
            "source_expression",
            "source_excerpt",
            "source_expressions",
            "parameters",
        )
    } == {
        "schema_version": 1,
        "kind": "dependent_actor_template",
        "target_path": "combat.hp.max",
        "source_expression": (
            "equal the steel defender's Constitution modifier + your Intelligence "
            "modifier + five times your level in this class"
        ),
        "source_excerpt": (
            "**Hit Points** equal the steel defender's Constitution modifier + "
            "your Intelligence modifier + five times your level in this class"
        ),
        "source_expressions": [
            {
                "target_path": "combat.hp.max",
                "source_expression": (
                    "equal the steel defender's Constitution modifier + your "
                    "Intelligence modifier + five times your level in this class"
                ),
                "source_excerpt": (
                    "**Hit Points** equal the steel defender's Constitution modifier + "
                    "your Intelligence modifier + five times your level in this class"
                ),
            }
        ],
        "parameters": ["owner_class_level", "owner_intelligence_modifier"],
    }
    assert requirement["instantiation_phase"] == "lobby_play_or_combat"
    assert requirement["runtime_ready"] is True
    assert requirement["solution"]["numeric_parameters"] == [
        "owner_class_level",
        "owner_intelligence_modifier",
    ]
    assert parameterized_statblock_requirements(
        "**Hit Points** 45 (6d10 + 12)"
    ) is None
    assert parameterized_statblock_requirements(
        "**Hit Points** unreadable OCR"
    ) is None


def test_parameterized_statblock_compiles_split_ocr_formula_tokens() -> None:
    requirement = parameterized_statblock_requirements(
        "# Steel Defender\n\n"
        "**Armor Class** 15 (natural armor)\n"
        "**Hit Points** equal the steel defender's Constitution modifier + "
        "your I ntelligence modifier + five times your level i n this class\n"
    )

    assert requirement is not None
    assert requirement["runtime_ready"] is True
    assert requirement["parameters"] == [
        "owner_class_level",
        "owner_intelligence_modifier",
    ]
    assert requirement["solution"]["numeric_parameters"] == [
        "owner_class_level",
        "owner_intelligence_modifier",
    ]


def test_parameterized_statblock_requirements_cover_numeric_owner_and_spell_formulas() -> None:
    owner = parameterized_statblock_requirements(
        "# Wildfire Spirit\n\n"
        "**Armor Class** 13 (natural armor)\n"
        "**Hit Points** 5 + your Wisdom modifier + five times your druid level\n"
        "***Flame Seed.*** Ranged Weapon Attack: your spell attack modifier to hit.\n"
    )
    summoned = parameterized_statblock_requirements(
        "# Aberrant Spirit\n\n"
        "**Armor Class** 11 + the level of the spell (natural armor)\n"
        "**Hit Points** 40 + 10 for each spell level above 4th\n"
        "**Proficiency Bonus** equals your bonus\n"
        "***Claws.*** Melee Weapon Attack: your spell attack modifier to hit.\n"
    )

    assert owner is not None
    assert owner["parameters"] == [
        "owner_class_level",
        "owner_wisdom_modifier",
        "owner_spell_attack_modifier",
    ]
    assert owner["target_path"] == "combat.hp.max"
    assert owner["source_expressions"][0]["source_expression"] == (
        "5 + your Wisdom modifier + five times your druid level"
    )
    assert summoned is not None
    assert summoned["parameters"] == [
        "owner_proficiency_bonus",
        "owner_spell_attack_modifier",
        "casting_slot_level",
    ]
    assert [item["target_path"] for item in summoned["source_expressions"]] == [
        "combat.armor_class",
        "combat.hp.max",
        "combat.proficiency_bonus",
    ]


def test_parameterized_statblock_requirements_accept_bounded_flat_pdf_fields() -> None:
    requirement = parameterized_statblock_requirements(
        "Tiny construct, neutral Armor Class 13 (natural armor) "
        "Hit Points equal to five times your level in this class + your "
        "Intelligence modifier Speed 20 ft., fly 30 ft. "
        "STR DEX CON INT WIS CHA"
    )

    assert requirement is not None
    assert requirement["target_path"] == "combat.hp.max"
    assert requirement["source_expression"] == (
        "equal to five times your level in this class + your Intelligence modifier"
    )
    assert requirement["parameters"] == [
        "owner_class_level",
        "owner_intelligence_modifier",
    ]


def test_parameterized_statblock_accepts_wrapped_markdown_core_field() -> None:
    requirement = parameterized_statblock_requirements(
        "# Steel Defender\n\n"
        "**Armor Class** 15 (natural armor)\n\n"
        "**Hit Points** equal the steel defender's Constitution modifier + your\n"
        "Intelligence modifier + five times your artificer level\n\n"
        "**Speed** 40 ft.\n"
    )

    assert requirement is not None
    assert requirement["runtime_ready"] is True
    assert requirement["source_expression"].endswith("your artificer level")


def test_parameterized_statblock_ignores_narrative_hit_point_phrases() -> None:
    requirement = parameterized_statblock_requirements(
        "Tiny construct Armor Class 13 (natural armor) "
        "Hit Points equal to five times your artificer level + your "
        "Intelligence modifier Speed 20 ft. STR DEX CON INT WIS CHA "
        "A salve grants hit points equal to 2d6 + your Intelligence modifier. "
        "Another effect restores hit points or deals acid damage."
    )

    assert requirement is not None
    assert requirement["source_expressions"] == [
        {
            "target_path": "combat.hp.max",
            "source_expression": (
                "equal to five times your artificer level + your Intelligence modifier"
            ),
            "source_excerpt": (
                "Hit Points equal to five times your artificer level + your "
                "Intelligence modifier"
            ),
        }
    ]


def test_runtime_spell_level_effect_is_not_a_dependent_actor_template() -> None:
    assert parameterized_statblock_requirements(
        "# Abjurer\n\n"
        "**Armor Class** 12\n"
        "**Hit Points** 84 (13d8 + 26)\n"
        "***Arcane Ward.*** When the abjurer casts an abjuration spell of "
        "1st level or higher, the ward regains hit points equal to twice the "
        "level of the spell."
    ) is None


def test_parameterized_statblock_solution_materializes_owner_and_self_values() -> None:
    source = (
        "# Steel Defender\n\n"
        "**Armor Class** 15 (natural armor)\n"
        "**Hit Points** equal the steel defender's Constitution modifier + "
        "your Intelligence modifier + five times your level in this class\n"
        "***Force-Empowered Rend.*** Melee Weapon Attack: your spell attack "
        "modifier to hit. Hit: 1d8 + PB force damage.\n"
    )
    requirement = parameterized_statblock_requirements(source)
    assert requirement is not None

    rendered, resolved = materialize_parameterized_statblock_source(
        source,
        requirement,
        numeric_parameters={
            "owner_class_level": 7,
            "owner_intelligence_modifier": 4,
            "owner_proficiency_bonus": 3,
            "owner_spell_attack_modifier": 7,
        },
        self_ability_modifiers={"constitution": 2},
    )

    assert "**Hit Points** 41" in rendered
    assert "Melee Weapon Attack: +7 to hit" in rendered
    assert "1d8 + 3 force damage" in rendered
    assert resolved == {"combat.hp.max": 41}


def test_parameterized_statblock_solution_requires_reviewed_variant() -> None:
    source = (
        "# Bestial Spirit\n\n"
        "**Armor Class** 11 + the level of the spell (natural armor)\n"
        "**Hit Points** 20 (Air only) or 30 (Land and Water only) + "
        "5 for each spell level above 2nd\n"
        "**Proficiency Bonus** equals your bonus\n"
    )
    requirement = parameterized_statblock_requirements(source)
    assert requirement is not None
    assert requirement["solution"]["variant_options"] == ["air", "land", "water"]

    rendered, resolved = materialize_parameterized_statblock_source(
        source,
        requirement,
        numeric_parameters={
            "casting_slot_level": 4,
            "owner_proficiency_bonus": 3,
        },
        self_ability_modifiers={},
        template_variant="water",
    )

    assert "**Armor Class** 15" in rendered
    assert "**Hit Points** 40" in rendered
    assert "**Proficiency Bonus** 3" in rendered
    assert resolved == {
        "combat.armor_class": 15,
        "combat.hp.max": 40,
        "combat.proficiency_bonus": 3,
    }


def test_dependent_actor_template_variant_filters_only_other_forms() -> None:
    requirement = parameterized_statblock_requirements(
        "# Celestial Spirit\n\n"
        "**Armor Class** 11 + the level of the spell (natural armor) + "
        "2 (Defender only)\n"
        "**Hit Points** 40 + 10 for each spell level above 5th\n"
        "***Radiant Bow (Archer Only).*** Ranged Weapon Attack.\n"
    )
    assert requirement is not None
    sheet = parse_2014_statblock(COMMONER, source_key="test").sheet
    sheet["content"]["features"] = [
        {
            "id": "bow",
            "name": "Radiant Bow (Archer Only)",
            "description": "Archer form only.",
            "activation": {"type": "passive", "cost": 0},
            "mechanic_refs": [],
        },
        {
            "id": "mace",
            "name": "Radiant Mace (Defender Only)",
            "description": "Defender form only.",
            "activation": {"type": "passive", "cost": 0},
            "mechanic_refs": [],
        },
    ]

    selected = apply_dependent_actor_template_variant(
        sheet,
        requirement,
        template_variant="defender",
    )

    assert [item["id"] for item in selected["content"]["features"]] == ["mace"]


def test_finalize_imported_actor_rulings_eliminates_lazy_semantic_fill() -> None:
    parsed_sheet = parse_2014_statblock(COMMONER, source_key="test").sheet
    sheet = validate_character_sheet(
        {
            **parsed_sheet,
            "content": {
                **parsed_sheet["content"],
                "features": [
                    {
                        "id": "strange-aura",
                        "name": "Strange Aura",
                        "description": (
                            "Creatures in the source-defined aura suffer its unusual effect."
                        ),
                        "activation": {"type": "passive", "cost": 0},
                        # Accounting or other partial mechanics do not settle
                        # the source-authored outcome by themselves.
                        "mechanic_refs": [
                            "dnd5e.core.activity.resource_accounting"
                        ],
                    }
                ],
            },
            "inventory": {
                **parsed_sheet["inventory"],
                "items": [
                    *parsed_sheet["inventory"]["items"],
                    {
                        "id": "strange-blade",
                        "name": "Strange Blade",
                        "kind": "weapon",
                        "mechanics": {
                            "damage_formula": "1d8",
                            "damage_type": "slashing",
                            "on_hit_effect": (
                                "On a hit, the blade applies its source-defined mark."
                            ),
                        },
                    },
                ],
            },
        }
    )

    finalized = finalize_imported_actor_rulings(sheet)

    requirement = finalized["content"]["features"][0]["ruling_requirements"][0]
    assert requirement["policy_ref"] == "actor_card.import.v1"
    assert requirement["default_resolver"] == "agent"
    assert requirement["source_excerpt"].startswith("Creatures in the source-defined")
    item = next(
        entry
        for entry in finalized["inventory"]["items"]
        if entry["id"] == "strange-blade"
    )
    assert item["ruling_requirements"][0]["ruling_kind"] == (
        "attack_on_hit_effect"
    )

    engine_settled = finalize_imported_actor_rulings(
        sheet,
        settled_mechanic_ids={"dnd5e.core.activity.resource_accounting"},
        settled_card_ids={"strange-blade"},
    )
    assert not engine_settled["content"]["features"][0]["ruling_requirements"]
    settled_item = next(
        entry
        for entry in engine_settled["inventory"]["items"]
        if entry["id"] == "strange-blade"
    )
    assert not settled_item["ruling_requirements"]

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


def test_layout_discovery_finds_each_column_without_prose_guesses() -> None:
    def block(text: str, x: int, y: int) -> dict:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x, y, x + 220, y + 14],
        }

    layout = {
        "page_number": 23,
        "width": 1000,
        "height": 1400,
        "blocks": [
            block("Tortle Druid", 40, 100),
            block("Medium humanoid (tortle), lawful neutral", 40, 118),
            block("Armor Class 17 (natural)", 40, 136),
            block("Hit Points 33 (6d8 + 6)", 40, 154),
            block("Speed 30 ft.", 40, 172),
            block("Tortle", 560, 100),
            block("Medium humanoid (tortle), lawful good", 560, 118),
            block("Armor Class 17 (natural)", 560, 136),
            block("Hit Points 22 (4d8 + 4)", 560, 154),
            block("Speed 30 ft.", 560, 172),
            block("Tortles prefer quiet lives.", 560, 230),
        ],
    }

    discovered = discover_2014_statblock_names_from_layout(layout)

    assert [(item["name"], item["column"]) for item in discovered] == [
        ("Tortle Druid", 0),
        ("Tortle", 1),
    ]


def test_layout_discovery_rejects_size_word_lore_and_repairs_identity_separator() -> None:
    def block(text: str, y: int) -> dict:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [40, y, 440, y + 14],
        }

    layout = {
        "page_number": 302,
        "width": 1000,
        "height": 1400,
        "blocks": [
            block("FASTIETH", 40),
            block("Large eyes, brightly colored and patterned scales, and", 58),
            block("MORDAKHESH", 120),
            block("Medium.fiend, lawful evil", 138),
            block("Armor Class 18 (plate)", 156),
            block("Hit Points 170 (20d8 + 80)", 174),
            block("Speed 40 ft.", 192),
        ],
    }

    discovered = discover_2014_statblock_names_from_layout(layout)

    assert [item["name"] for item in discovered] == ["MORDAKHESH"]
    assert discovered[0]["identity"] == "Medium.fiend, lawful evil"


def test_point_radius_save_damage_is_structured_from_exact_source() -> None:
    source = COMMONER.replace(
        '***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n'
        '*Hit:* 2 (1d4) bludgeoning damage.',
        (
            '***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n'
            '*Hit:* 2 (1d4) bludgeoning damage.\n\n'
            '***Lightning Strike (Recharge 5-6)***. The giant hurls a magical '
            'lightning bolt at a point it can see within 500 feet of it. Each '
            'creature within 10 feet of that point must make a DC 17 Dexterity '
            'saving throw, taking 54 (12d8) lightning damage on a failed save, '
            'or half as much damage on a successful one.'
        ),
    )

    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:p157",
        name="Storm Giant",
    )
    activity = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Lightning Strike (Recharge 5-6)"
    )

    assert parsed.warnings == ()
    assert activity["uses"]["value"] == 1
    assert activity["uses"]["max"] == 1
    assert activity["choices"]["recharge"] == {
        "kind": "d6_turn_start",
        "minimum": 5,
        "maximum": 6,
        "source_marker": "(Recharge 5-6)",
    }
    assert set(activity["mechanic_refs"]) == {
        "dnd5e.core.activity.area_save_damage",
        "dnd5e.core.activity.recharge",
    }
    assert area_save_damage_spec(parsed.sheet, activity["id"]) == {
        "kind": "visible_point_radius_save_damage",
        "origin": {"kind": "visible_point", "range_ft": 500},
        "area": {"shape": "radius", "radius_ft": 10},
        "targets": "each_creature",
        "save_ability": "dexterity",
        "save_dc": 17,
        "damage_formula": "12d8",
        "average_damage": 54,
        "damage_type": "lightning",
        "half_on_success": True,
        "save_source_kind": "magical_effect",
        "source_excerpt": (
            "The giant hurls a magical lightning bolt at a point it can see within "
            "500 feet of it. Each creature within 10 feet of that point must make "
            "a DC 17 Dexterity saving throw, taking 54 (12d8) lightning damage on "
            "a failed save, or half as much damage on a successful one."
        ),
    }


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

BLACK_PUDDING = """## Black Pudding

*Large ooze, unaligned*

**Armor Class** 7

**Hit Points** 85 (10d10 + 30)

**Speed** 20 ft., climb 20 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 16 (+3) | 5 (-3) | 16 (+3) | 1 (-5) | 6 (-2) | 1 (-5) |

**Damage Immunities** acid, cold, lightning, slashing

**Condition Immunities** blinded, charmed, deafened, exhaustion, frightened, prone

**Senses** blindsight 60 ft. (blind beyond this radius), passive Perception 8

**Languages** -

**Challenge** 4 (1,100 XP)

***Amorphous.*** The pudding can move through a space as narrow as 1 inch wide
without squeezing.

***Corrosive Form.*** A creature that touches the pudding or hits it with a
melee attack while within 5 feet of it takes 4 (1d8) acid damage. Any
nonmagical weapon made of metal or wood that hits the pudding corrodes. After
dealing damage, the weapon takes a permanent and cumulative -1 penalty to
damage rolls. If its penalty drops to -5, the weapon is destroyed. Nonmagical
ammunition made of metal or wood that hits the pudding is destroyed after
dealing damage. The pudding can eat through 2-inch-thick, nonmagical wood or
metal in 1 round.

***Spider Climb.*** The pudding can climb difficult surfaces, including upside
down on ceilings, without needing to make an ability check.

###### Actions

***Pseudopod.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 6 (1d6 + 3) bludgeoning damage plus 18 (4d8) acid damage. In addition,
nonmagical armor worn by the target is partly dissolved and takes a permanent
and cumulative -1 penalty to the AC it offers. The armor is destroyed if the
penalty reduces its AC to 10.

###### Reactions

***Split.*** When a pudding that is Medium or larger is subjected to lightning
or slashing damage, it splits into two new puddings if it has at least 10 hit
points. Each new pudding has hit points equal to half the original pudding's,
rounded down. New puddings are one size smaller than the original pudding.
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

MAGMIN = """# Magmin

*Small elemental, chaotic neutral*

**Armor Class** 14 (natural armor)

**Hit Points** 9 (2d6 + 2)

**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 7 (-2) | 15 (+2) | 12 (+1) | 8 (-1) | 11 (+0) | 10 (+0) |

**Damage Resistances** bludgeoning, piercing, and slashing from nonmagical weapons

**Damage Immunities** fire

**Senses** darkvision 60 ft., passive Perception 10

**Languages** Ignan

**Challenge** 1/2 (100 XP)

***Death Burst.*** When the magmin dies, it explodes in a burst of fire and
magma. Each creature within 10 ft. of it must make a DC 11 Dexterity saving
throw, taking 7 (2d6) fire damage on a failed save, or half as much damage on
a successful one. Flammable objects that aren't being worn or carried in that
area are ignited.

***Ignited Illumination.*** As a bonus action, the magmin can set itself ablaze
or extinguish its flames. While ablaze, the magmin sheds bright light in a
10-foot radius and dim light for an additional 10 ft.

###### Actions

***Touch.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
*Hit:* 7 (2d6) fire damage. If the target is a creature or a flammable object,
it ignites. Until a creature takes an action to douse the fire, the creature
takes 3 (1d6) fire damage at the end of each of its turns.
"""


SALAMANDER = """# Salamander

*Large elemental, neutral evil*

**Armor Class** 15 (natural armor)
**Hit Points** 90 (12d10 + 24)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 18 (+4) | 14 (+2) | 15 (+2) | 11 (+0) | 10 (+0) | 12 (+1) |

**Damage Vulnerabilities** cold
**Damage Resistances** bludgeoning, piercing, and slashing from nonmagical weapons
**Damage Immunities** fire
**Senses** darkvision 60 ft., passive Perception 10
**Languages** Ignan
**Challenge** 5 (1,800 XP)

***Heated Body.*** A creature that touches the salamander or hits it with a
melee attack while within 5 feet of it takes 7 (2d6) fire damage.

***Heated Weapons.*** Any metal melee weapon the salamander wields deals an
extra 3 (1d6) fire damage on a hit (included in the attack).

## Actions

***Multiattack.*** The salamander makes two attacks: one with its spear and one
with its tail.

***Spear.*** *Melee or Ranged Weapon Attack:* +7 to hit, reach 5 ft. or range
20/60 ft., one target. *Hit:* 11 (2d6 + 4) piercing damage, or 13 (2d8 + 4)
piercing damage if used with two hands to make a melee attack, plus 3 (1d6)
fire damage.

***Tail.*** *Melee Weapon Attack:* +7 to hit, reach 10 ft., one target.
*Hit:* 11 (2d6 + 4) bludgeoning damage plus 7 (2d6) fire damage.
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


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Multiattack", True),
        ("Multiattack (Yuan-ti Form Only)", True),
        ("Multiattack (Humanoid or Hybrid Form Only)", True),
        ("Multiattack Defense", False),
        ("Greater Multiattack", False),
    ],
)
def test_multiattack_source_identity_preserves_qualified_rulebook_titles(
    name: str,
    expected: bool,
) -> None:
    assert is_multiattack_source_name(name) is expected


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


def test_statblock_rejects_silently_unparsed_weapon_action_marker() -> None:
    malformed = GIANT_SPIDER.replace(
        "***Web (Recharge 5-6)***.",
        "Web (Recharge 5-6}.",
    )

    with pytest.raises(
        StatblockImportError,
        match="unparsed weapon action markers",
    ):
        parse_2014_statblock(
            malformed,
            source_key="rule-source:malformed-giant-spider",
        )


def test_recognized_unstructured_attack_is_scoped_to_agent_ruling() -> None:
    parsed = parse_2014_statblock(
        """# Variant Cultist

*Medium humanoid (human), neutral evil*

**Armor Class** 15
**Hit Points** 30 (4d8 + 12)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|---|
| 10 (+0) | 16 (+3) | 16 (+3) | 10 (+0) | 12 (+1) | 12 (+1) |

**Senses** passive Perception 11
**Languages** Common
**Challenge** 2 (450 XP)

## Actions

***Multiattack.*** The cultist attacks twice with its shortsword.

***Shortsword.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 6 (1d6 + 3) piercing damage.

***Elemental Orb (2/Day).*** *Ranged Spell Attack:* TBD to hit, range 90 ft.,
one target. *Hit:* 22 (5d8) damage of the type selected for this creature.
""",
        source_key="module-review:variant-cultist",
    )

    derived = derive_character_sheet(parsed.sheet)
    assert [item["item_id"] for item in derived["inventory"]["weapon_attacks"]] == [
        "shortsword"
    ]
    orb = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Elemental Orb (2/Day)"
    )
    assert orb["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": (
            "*Ranged Spell Attack:* TBD to hit, range 90 ft., one target. "
            "*Hit:* 22 (5d8) damage of the type selected for this creature."
        ),
    }
    assert (
        "Elemental Orb (2/Day): descriptive action is not automatically settled"
        in parsed.warnings
    )


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
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Club: trailing creature prose excluded from action settlement",
    )


def test_lore_paragraph_not_starting_with_actor_name_is_not_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace("### Commoner", "### Spy").replace(
            "*Medium humanoid (any race), any alignment*",
            "*Medium humanoid (any race), any alignment*",
        )
        + """

Rulers, nobles, merchants, and other wealthy individuals use **spies** to
gain the upper hand. A spy is trained to secretly gather information.
""",
        source_key="srd-spy-with-lore",
    )
    club = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert club["on_hit_effect"] == ""
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Club: trailing creature prose excluded from action settlement",
    )


def test_multiword_actor_lore_using_head_noun_is_not_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        """### Cult Fanatic

*Medium humanoid (any race), any non-good alignment*

**Armor Class** 13
**Hit Points** 33 (6d8 + 6)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 11 (+0) | 14 (+2) | 12 (+1) | 10 (+0) | 13 (+1) | 14 (+2) |

**Senses** passive Perception 10
**Languages** Common
**Challenge** 2 (450 XP)

## Actions

***Dagger.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., one creature.
*Hit:* 4 (1d4 + 2) piercing damage. Fanatics often lead dangerous cults.
""",
        source_key="srd-cult-fanatic-with-lore",
    )
    dagger = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert dagger["on_hit_effect"] == ""
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Dagger: trailing creature prose excluded from action settlement",
    )


def test_explicit_sling_stones_become_ammunition_not_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        """### DARZ HELGAR

*Medium humanoid (human), neutral*

**Armor Class** 12
**Hit Points** 27 (5d8 + 5)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 15 (+2) | 15 (+2) | 12 (+1) | 10 (+0) | 11 (+0) | 11 (+0) |

**Senses** passive Perception 10
**Languages** Common

## Actions

***Sling***. *Ranged Weapon Attack:* +4 to hit, range 30/120 ft.,
one target. *Hit:* 4 (1d4 + 2) bludgeoning damage. Darz carries
twenty sling stones.
""",
        source_key="storm-kings-thunder:p60",
    )
    derived = derive_character_sheet(parsed.sheet)
    sling = derived["inventory"]["weapon_attacks"][0]
    stones = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "ammunition"
    )

    assert sling["on_hit_effect"] == ""
    assert sling["ammunition_item_id"] == "sling-ammunition"
    assert stones["id"] == "sling-ammunition"
    assert stones["name"] == "Sling Stones"
    assert stones["quantity"] == 20
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Sling: trailing ammunition inventory structured separately "
        "from action settlement",
    )


@pytest.mark.parametrize(
    ("name", "weapon", "damage", "inventory", "ammunition_name"),
    [
        (
            "Urgala",
            "Shortbow",
            "5 (1d6 + 1) piercing",
            "Urgala carries a quiver of twenty arrows.",
            "Arrows",
        ),
        (
            "Narth",
            "Hand Crossbow",
            "5 (1d6 + 2) piercing",
            "Narth carries twenty crossbow bolts.",
            "Crossbow Bolts",
        ),
    ],
)
def test_standard_ammunition_container_phrases_are_not_hit_effects(
    name: str,
    weapon: str,
    damage: str,
    inventory: str,
    ammunition_name: str,
) -> None:
    parsed = parse_2014_statblock(
        f"""### {name}

*Medium humanoid (human), neutral*

**Armor Class** 12
**Hit Points** 27 (5d8 + 5)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 15 (+2) | 15 (+2) | 12 (+1) | 10 (+0) | 11 (+0) | 11 (+0) |

**Senses** passive Perception 10
**Languages** Common

## Actions

***{weapon}***. *Ranged Weapon Attack:* +4 to hit, range 30/120 ft.,
one target. *Hit:* {damage} damage. {inventory}
""",
        source_key=f"storm-kings-thunder:{name.casefold()}",
    )
    weapon_attack = derive_character_sheet(parsed.sheet)["inventory"][
        "weapon_attacks"
    ][0]
    ammunition = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "ammunition"
    )

    assert weapon_attack["on_hit_effect"] == ""
    assert weapon_attack["ammunition_item_id"] == (
        f"{weapon.casefold().replace(' ', '-')}-ammunition"
    )
    assert ammunition["name"] == ammunition_name
    assert ammunition["quantity"] == 20
    assert parsed.warnings == ()


def test_article_and_emphasis_before_actor_lore_are_not_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        """### Worg

*Large monstrosity, neutral evil*

**Armor Class** 13
**Hit Points** 26 (4d10 + 4)
**Speed** 50 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 16 (+3) | 13 (+1) | 13 (+1) | 7 (-2) | 11 (+0) | 8 (-1) |

**Senses** darkvision 60 ft., passive Perception 14
**Languages** Goblin, Worg
**Challenge** 1/2 (100 XP)

## Actions

***Bite.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 10 (2d6 + 3) piercing damage. If the target is a creature, it must
succeed on a DC 13 Strength saving throw or be knocked prone.

A **worg** is an evil predator that delights in hunting weaker creatures.
""",
        source_key="srd-worg-with-lore",
    )
    bite = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert bite["on_hit_effect"] == (
        "If the target is a creature, it must succeed on a DC 13 Strength "
        "saving throw or be knocked prone."
    )
    assert parsed.warnings == ("Bite: on-hit effect requires DM settlement",)
    assert parsed.normalization_notes == (
        "Bite: trailing creature prose excluded from action settlement",
    )


def test_quoted_variant_actions_do_not_mutate_the_base_statblock() -> None:
    parsed = parse_2014_statblock(
        """### Giant Rat

*Small beast, unaligned*

**Armor Class** 12
**Hit Points** 7 (2d6)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 7 (-2) | 15 (+2) | 11 (+0) | 2 (-4) | 10 (+0) | 4 (-3) |

**Senses** darkvision 60 ft., passive Perception 10
**Languages** -
**Challenge** 1/8 (25 XP)

## Actions

***Bite.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
*Hit:* 4 (1d4 + 2) piercing damage.

>**Variant: Diseased Giant Rats**
>
>***Bite.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
>*Hit:* 4 (1d4 + 2) piercing damage. The target contracts a disease.
""",
        source_key="srd-giant-rat-with-variant",
    )
    attacks = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"]

    assert [attack["item_id"] for attack in attacks] == ["bite"]
    assert attacks[0]["on_hit_effect"] == ""


def test_plain_size_type_alignment_line_is_accepted() -> None:
    parsed = parse_2014_statblock(
        """# Young Red Dragon

Large dragon, chaotic evil

**Armor Class** 18 (natural armor)
**Hit Points** 178 (17d10 + 85)
**Speed** 40 ft., climb 40 ft., fly 80 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 23 (+6) | 10 (+0) | 21 (+5) | 14 (+2) | 11 (+0) | 19 (+4) |

**Senses** blindsight 30 ft., darkvision 120 ft., passive Perception 18
**Languages** Common, Draconic
**Challenge** 10 (5,900 XP)

## Actions

***Bite.*** *Melee Weapon Attack:* +10 to hit, reach 10 ft., one target.
*Hit:* 17 (2d10 + 6) piercing damage plus 3 (1d6) fire damage.
""",
        source_key="srd-young-red-dragon-plain-identity",
    )

    assert parsed.sheet["traits"]["size"] == "large"
    assert parsed.sheet["progression"]["species"] == "dragon"
    assert parsed.sheet["traits"]["alignment"] == "chaotic evil"


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


def test_weapon_damage_accepts_unambiguous_ocr_spacing_inside_dice() -> None:
    parsed = parse_2014_statblock(
        """# Neronvain

*Medium humanoid (elf), neutral evil*

**Armor Class** 17
**Hit Points** 117 (18d8 + 36)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|---|
| 8 (-1) | 17 (+3) | 14 (+2) | 16 (+3) | 13 (+1) | 18 (+4) |

**Senses** passive Perception 11
**Languages** Common, Draconic, Elvish
**Challenge** 9 (5,000 XP)

## Actions

***Eldritch Arrow.*** *Ranged Spell Attack:* +7 to hit, range 120 ft.,
one target. *Hit:* 11 (2d 10) force damage plus 9 (2d 8) poison damage.
""",
        source_key="module-review:neronvain",
    )

    weapon = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["id"] == "eldritch-arrow"
    )
    assert weapon["mechanics"]["damage_formula"] == "2d10"
    assert weapon["mechanics"]["damage_type"] == "force"
    assert weapon["mechanics"]["additional_damage"] == [
        {
            "damage_formula": "2d8",
            "damage_bonus": 0,
            "damage_type": "poison",
        }
    ]
    assert weapon["mechanics"]["on_hit_effect"] == ""
    assert parsed.warnings == ()


def test_weapon_damage_structures_in_parentheses_additional_dice() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "*Hit:* 14 (1d12 + 4 plus 1d8) slashing damage.",
        ),
        source_key="monster-manual:orc-war-chief",
    )
    weapon = parsed.sheet["inventory"]["items"][0]

    assert weapon["mechanics"]["damage_formula"] == "1d12"
    assert weapon["mechanics"]["damage_bonus_override"] == 4
    assert weapon["mechanics"]["damage_type"] == "slashing"
    assert weapon["mechanics"]["additional_damage"] == [
        {
            "damage_formula": "1d8",
            "damage_bonus": 0,
            "damage_type": "slashing",
        }
    ]
    assert weapon["mechanics"]["on_hit_effect"] == ""
    assert parsed.warnings == ()


def test_weapon_damage_structures_two_handed_melee_alternative() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "*Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "*Melee or Ranged Weapon Attack:* +6 to hit, reach 5 ft. or range "
            "20/60 ft., one target. *Hit:* 12 (1d6 + 4 plus 1d8) piercing "
            "damage, or 13 (2d8 + 4) piercing damage if used with two hands "
            "to make a melee attack.",
        ),
        source_key="monster-manual:orc-war-chief",
    )
    weapon = parsed.sheet["inventory"]["items"][0]

    assert weapon["mechanics"]["damage_formula"] == "1d6"
    assert weapon["mechanics"]["damage_bonus_override"] == 4
    assert weapon["mechanics"]["additional_damage"] == [
        {
            "damage_formula": "1d8",
            "damage_bonus": 0,
            "damage_type": "piercing",
        }
    ]
    assert weapon["mechanics"]["versatile_damage_formula"] == "2d8"
    assert weapon["mechanics"]["properties"] == ["thrown", "versatile"]
    assert weapon["mechanics"]["on_hit_effect"] == ""
    assert parsed.warnings == ()


def test_weapon_damage_structures_short_two_handed_alternative() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "***Battleaxe***. *Melee Weapon Attack:* +6 to hit, reach 5 ft., "
            "one target. *Hit:* 8 (1d8 + 4) slashing damage, or 9 "
            "(1d10 + 4) slashing damage if used with two hands.",
        ),
        source_key="storm-kings-thunder:ghelryn",
    )
    weapon = parsed.sheet["inventory"]["items"][0]

    assert weapon["mechanics"]["damage_formula"] == "1d8"
    assert weapon["mechanics"]["damage_bonus_override"] == 4
    assert weapon["mechanics"]["versatile_damage_formula"] == "1d10"
    assert weapon["mechanics"]["properties"] == ["versatile"]
    assert weapon["mechanics"]["on_hit_effect"] == ""
    assert parsed.warnings == ()


def test_trailing_page_number_is_not_imported_as_an_on_hit_effect() -> None:
    parsed = parse_2014_statblock(
        TROLL + "\n291\n",
        source_key="monster-manual-page-291",
    )
    claw = derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0]

    assert claw["on_hit_effect"] == ""
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Claw: trailing page furniture excluded from action settlement",
    )


def test_gazer_eye_rays_are_structured_from_the_exact_source_action() -> None:
    parsed = parse_2014_statblock(
        GAZER,
        source_key="module-review:waterdeep-gazer",
        rule_refs=["waterdeep-page-204"],
    )
    reviewed_sheet = deepcopy(parsed.sheet)
    reviewed_warnings = list(parsed.warnings)
    _structure_gazer_eye_rays(reviewed_sheet, reviewed_warnings)
    activities = reviewed_sheet["content"]["activities"]
    eye_rays = next(item for item in activities if item["name"] == "Eye Rays")
    spec = gazer_eye_ray_spec(reviewed_sheet, eye_rays["id"])

    assert spec is not None
    assert spec["save_source_kind"] == "magical_effect"
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
    assert eye_rays["mechanic_refs"] == [
        "dnd5e.core.activity.random_save_effects"
    ]
    assert not {
        "Dazing Ray",
        "Fear Ray",
        "Frost Ray",
        "Telekinetic Ray",
    } & {item["name"] for item in activities}
    assert reviewed_warnings == []


def test_custom_same_named_gazer_requires_agent_semantic_review() -> None:
    parsed = parse_2014_statblock(
        GAZER,
        source_key="custom:gazer-like-creation",
        rule_refs=["waterdeep-page-204"],
    )
    activities = parsed.sheet["content"]["activities"]
    eye_rays = next(item for item in activities if item["name"] == "Eye Rays")

    assert gazer_eye_ray_spec(parsed.sheet, eye_rays["id"]) is None
    assert {
        "Dazing Ray",
        "Fear Ray",
        "Frost Ray",
        "Telekinetic Ray",
    }.issubset({item["name"] for item in activities})
    assert eye_rays["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert any(
        warning.startswith("Eye Rays: descriptive action")
        for warning in parsed.warnings
    )


def test_intellect_devourer_actions_are_structured_from_exact_source() -> None:
    parsed = parse_2014_statblock(
        INTELLECT_DEVOURER,
        source_key="reviewed-intellect-devourer",
        rule_refs=["monster-manual-page-191"],
    )
    reviewed_sheet = deepcopy(parsed.sheet)
    reviewed_warnings = list(parsed.warnings)
    _structure_intellect_devourer_actions(reviewed_sheet, reviewed_warnings)
    derived = derive_character_sheet(reviewed_sheet)
    devour = next(
        item
        for item in reviewed_sheet["content"]["activities"]
        if item["name"] == "Devour Intellect"
    )
    body_thief = next(
        item
        for item in reviewed_sheet["content"]["activities"]
        if item["name"] == "Body Thief"
    )

    assert source_save_effect_spec(reviewed_sheet, devour["id"]) == {
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
    assert devour["mechanic_refs"] == [
        "dnd5e.core.activity.source_save_effect"
    ]
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
    assert source_contest_effect_spec(reviewed_sheet, body_thief["id"]) == {
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
    assert body_thief["mechanic_refs"] == [
        "dnd5e.core.activity.source_contest_effect"
    ]
    assert tuple(reviewed_warnings) == (
        "Body Thief: protection, wish, and voluntary exit require DM settlement",
    )


def test_custom_same_named_intellect_actions_do_not_gain_core_privileges() -> None:
    parsed = parse_2014_statblock(
        INTELLECT_DEVOURER,
        source_key="custom:intellect-like-creation",
        rule_refs=["monster-manual-page-191"],
    )
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

    assert source_save_effect_spec(parsed.sheet, devour["id"]) is None
    assert source_contest_effect_spec(parsed.sheet, body_thief["id"]) is None
    assert devour["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert body_thief["choices"]["manual_ruling"]["default_resolver"] == "agent"


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
    multiattack = next(
        activity
        for activity in parsed.sheet["content"]["activities"]
        if activity["name"] == "Multiattack"
    )
    assert multiattack["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": (
            "The commoner makes one attack with its club and uses Devour Intellect."
        ),
    }
    assert "Multiattack: Multiattack composition requires a DM ruling" in parsed.warnings


def test_descriptive_statblock_action_is_marked_for_agent_ruling() -> None:
    source_excerpt = (
        "The commoner exhales lightning in a 30-foot line that is 5 feet wide. "
        "Each creature in that line must make a DC 12 Dexterity saving throw."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.",
            (
                f"***Lightning Breath (Recharge 5-6)***. {source_excerpt}\n\n"
                "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target."
            ),
        ),
        source_key="module-review:descriptive-breath",
    )
    activity = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Lightning Breath (Recharge 5-6)"
    )

    assert activity["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": source_excerpt,
    }
    assert parsed.warnings == (
        "Lightning Breath (Recharge 5-6): descriptive action is not automatically settled",
    )


def test_choice_container_with_nested_attack_is_not_misparsed_as_a_weapon() -> None:
    source_excerpt = (
        "The kobold randomly chooses one of its inventions. 1. Acid. "
        "Ranged Weapon Attack: +4 to hit, range 5/20 ft., one target. "
        "Hit: 7 (2d6) acid damage. 2. Basket of Centipedes. The target "
        "must succeed on a DC 12 Constitution saving throw."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            f"***Weapon Invention***. {source_excerpt}",
        ),
        source_key="volo-2016:p167",
        name="Kobold Inventor",
    )

    assert not any(
        item["name"] == "Weapon Invention"
        for item in parsed.sheet["inventory"]["items"]
    )
    activity = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Weapon Invention"
    )
    assert activity["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": source_excerpt,
    }


def test_agent_can_compile_a_custom_statblock_action_without_a_python_branch() -> None:
    source_excerpt = (
        "The commoner releases a prismatic pulse. Each creature chosen in the "
        "area must make a DC 14 Wisdom saving throw, taking 3d8 radiant damage "
        "on a failed save."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.",
            (
                f"***Prismatic Pulse (Recharge 5-6)***. {source_excerpt}\n\n"
                "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target."
            ),
        ),
        source_key="module-review:custom-prismatic-pulse",
        rule_refs=("module-chunk:prismatic-pulse",),
    )
    activity = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Prismatic Pulse (Recharge 5-6)"
    )
    plan_id = "module.custom-prismatic-pulse.plan"
    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "resolution_plans": [
                {
                    "source_card_id": activity["id"],
                    "reason": (
                        "The Agent mapped only the explicit save and damage clauses "
                        "from the reviewed source action."
                    ),
                    "resolution_plan": {
                        "schema_version": 1,
                        "id": plan_id,
                        "source_card_id": activity["id"],
                        "source_card_kind": "monster_action",
                        "trigger": "action",
                        "slots": {
                            "targets": {
                                "kind": "actor_ids",
                                "owner": "agent",
                                "description": (
                                    "Creatures selected within the source-defined area."
                                ),
                                "minimum_items": 1,
                            }
                        },
                        "steps": [
                            {
                                "id": "save",
                                "op": "check.save",
                                "args": {
                                    "target_ids": {"$slot": "targets"},
                                    "ability": "wisdom",
                                    "dc": 14,
                                    "source": "Prismatic Pulse",
                                    "success_damage": "none",
                                },
                            },
                            {
                                "id": "damage",
                                "op": "damage.apply",
                                "args": {
                                    "target_ids": {"$slot": "targets"},
                                    "expression": "3d8",
                                    "damage_type": "radiant",
                                    "source": "Prismatic Pulse",
                                    "reduction": {
                                        "$result": "save.damage_reduction_by_actor_id"
                                    },
                                },
                            },
                        ],
                        "citations": [
                            {
                                "source": "module-review:custom-prismatic-pulse",
                                "source_ref": {
                                    "chunk_id": "module-chunk:prismatic-pulse"
                                },
                                "source_excerpt": source_excerpt,
                            }
                        ],
                    },
                }
            ]
        },
    )
    compiled_activity = next(
        item
        for item in filled["sheet"]["content"]["activities"]
        if item["id"] == activity["id"]
    )

    assert compiled_activity["resolution_plan"]["id"] == plan_id
    assert compiled_activity["choices"]["resolution_plan"]["id"] == plan_id
    assert "manual_ruling" not in compiled_activity["choices"]
    assert filled["resolved_warnings"] == [
        "Prismatic Pulse (Recharge 5-6): descriptive action is not automatically settled"
    ]
    assert filled["fill"]["resolution_plans"][0]["resolution_plan"]["fingerprint"]


def test_agent_can_compile_a_weapon_on_hit_plan_from_the_exact_statblock_text() -> None:
    parsed = parse_2014_statblock(
        GIANT_SPIDER,
        source_key="module-review:giant-spider-web-plan",
    )
    web = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["id"] == "web-recharge-5-6"
    )
    source_excerpt = web["description"]
    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "resolution_plans": [
                {
                    "source_card_id": web["id"],
                    "reason": (
                        "The Agent mapped the printed on-hit restraint to a "
                        "source-bound condition plan."
                    ),
                    "resolution_plan": {
                        "schema_version": 1,
                        "id": "module.giant-spider.web.on-hit",
                        "source_card_id": web["id"],
                        "source_card_kind": "item",
                        "trigger": "attack.after_hit",
                        "slots": {
                            "source_actor": {
                                "kind": "actor_id",
                                "owner": "agent",
                                "description": (
                                    "The giant spider that made the triggering attack."
                                ),
                            },
                            "target": {
                                "kind": "actor_id",
                                "owner": "agent",
                                "description": (
                                    "The creature hit by the triggering web attack."
                                ),
                            },
                        },
                        "steps": [
                            {
                                "id": "targets",
                                "op": "target.validate",
                                "args": {
                                    "source_actor_id": {"$slot": "source_actor"},
                                    "target_ids": [{"$slot": "target"}],
                                    "exclude_self": True,
                                    "require_visible": True,
                                    "source": "Web",
                                },
                            },
                            {
                                "id": "restrain",
                                "op": "condition.apply",
                                "args": {
                                    "target_ids": [{"$slot": "target"}],
                                    "condition_id": "restrained",
                                    "source": "Web",
                                },
                            },
                        ],
                        "citations": [
                            {
                                "source": "module-review:giant-spider-web-plan",
                                "source_ref": {"chunk_id": "giant-spider-web"},
                                "source_excerpt": source_excerpt,
                            }
                        ],
                    },
                }
            ]
        },
    )
    compiled_web = next(
        item
        for item in filled["sheet"]["inventory"]["items"]
        if item["id"] == web["id"]
    )

    assert compiled_web["resolution_plan"]["source_card_kind"] == "item"
    assert compiled_web["resolution_plan"]["fingerprint"]
    assert filled["resolved_warnings"] == [
        "Web (Recharge 5-6): on-hit effect requires DM settlement"
    ]


def test_descriptive_statblock_sections_preserve_nonstandard_action_economies() -> None:
    parsed = parse_2014_statblock(
        COMMONER
        + """

## Bonus Actions

***Shadow Step.*** The commoner teleports to an unoccupied space it can see.

## Legendary Actions

***Detect.*** The commoner makes a Wisdom (Perception) check.

## Lair Actions

***Falling Stones.*** Stones fall at a point the commoner can see.
""",
        source_key="module-review:descriptive-action-economies",
    )
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }

    assert activities["Shadow Step"]["activation"] == {
        "type": "bonus_action",
        "cost": 1,
        "trigger": "",
    }
    assert activities["Detect"]["activation"] == {
        "type": "special",
        "cost": 1,
        "trigger": "",
    }
    assert activities["Falling Stones"]["activation"] == {
        "type": "special",
        "cost": 1,
        "trigger": "",
    }
    assert all(
        item["choices"]["manual_ruling"]["kind"] == "descriptive_activity"
        for item in activities.values()
        if item["name"] in {"Shadow Step", "Detect", "Falling Stones"}
    )
    assert (
        "Shadow Step: descriptive bonus action is not automatically settled"
        in parsed.warnings
    )
    assert "Detect: descriptive special is not automatically settled" in parsed.warnings
    assert (
        "Falling Stones: descriptive special is not automatically settled"
        in parsed.warnings
    )


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


def test_black_pudding_standard_traits_are_structured() -> None:
    parsed = parse_2014_statblock(
        BLACK_PUDDING,
        source_key="monster-manual-2014:p241",
    )
    features = {
        item["name"]: item for item in parsed.sheet["content"]["features"]
    }
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }
    pseudopod = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Pseudopod"
    )

    assert features["Amorphous"]["choices"]["source_trait"] == {
        "kind": "amorphous",
        "trigger": "movement",
        "minimum_space_width_inches": 1,
        "requires_squeezing": False,
        "automatic": True,
    }
    assert features["Corrosive Form"]["choices"]["source_trait"][
        "contact_damage_formula"
    ] == "1d8"
    assert features["Spider Climb"]["choices"]["source_trait"][
        "ability_check_required"
    ] is False
    assert activities["Split"]["choices"]["source_trait"] == {
        "kind": "split",
        "trigger": "subjected_to_damage",
        "damage_types": ["lightning", "slashing"],
        "minimum_size": "medium",
        "minimum_hit_points": 10,
        "new_creature_count": 2,
        "hit_points": "half_original_rounded_down",
        "size_change": -1,
    }
    assert pseudopod["mechanics"]["on_hit_effect"] == ""
    assert pseudopod["mechanics"]["on_hit_resolution"]["kind"] == (
        "armor_corrosion"
    )
    assert parsed.warnings == ()


def test_salamander_standard_traits_are_structured() -> None:
    parsed = parse_2014_statblock(
        SALAMANDER,
        source_key="monster-manual-2014:p267",
    )
    features = {
        item["name"]: item for item in parsed.sheet["content"]["features"]
    }
    weapons = {
        item["name"]: item for item in parsed.sheet["inventory"]["items"]
    }

    assert features["Heated Body"]["choices"]["source_trait"] == {
        "kind": "heated_body",
        "trigger": "contact_or_melee_hit",
        "melee_range_ft": 5,
        "contact_damage_formula": "2d6",
        "average_damage": 7,
        "contact_damage_type": "fire",
        "automatic": True,
        "source_excerpt": (
            "A creature that touches the salamander or hits it with a melee "
            "attack while within 5 feet of it takes 7 (2d6) fire damage."
        ),
    }
    assert features["Heated Weapons"]["choices"]["source_trait"][
        "embedded_in_weapon_actions"
    ] is True
    expected_fire = {
        "damage_formula": "1d6",
        "damage_bonus": 0,
        "damage_type": "fire",
    }
    assert weapons["Spear"]["mechanics"]["additional_damage"] == [expected_fire]
    assert weapons["Spear"]["mechanics"]["versatile_additional_damage"] == [
        expected_fire
    ]
    assert parsed.warnings == ()


def test_magmin_standard_mechanics_are_structured() -> None:
    parsed = parse_2014_statblock(
        MAGMIN,
        source_key="monster-manual-2014:p212",
    )
    features = {
        item["name"]: item for item in parsed.sheet["content"]["features"]
    }
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }
    touch = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Touch"
    )

    assert features["Death Burst"]["id"] == "dnd5e.core.monster.death-burst"
    assert features["Death Burst"]["choices"]["source_trait"] == {
        "kind": "death_burst",
        "trigger": "death",
        "range_ft": 10,
        "target": "each_creature_in_range",
        "save_ability": "dexterity",
        "save_dc": 11,
        "damage_formula": "2d6",
        "average_damage": 7,
        "damage_type": "fire",
        "failed_save": "full",
        "successful_save": "half",
        "ignite_flammable_unworn_objects": True,
        "automatic": True,
        "source_excerpt": (
            "When the magmin dies, it explodes in a burst of fire and magma. "
            "Each creature within 10 ft. of it must make a DC 11 Dexterity "
            "saving throw, taking 7 (2d6) fire damage on a failed save, or "
            "half as much damage on a successful one. Flammable objects that "
            "aren't being worn or carried in that area are ignited."
        ),
    }
    assert activities["Ignited Illumination"]["id"] == (
        "dnd5e.core.monster.ignited-illumination"
    )
    assert activities["Ignited Illumination"]["activation"] == {
        "type": "bonus_action",
        "cost": 1,
        "trigger": "bonus action on its turn",
    }
    assert activities["Ignited Illumination"]["choices"]["source_trait"] == {
        "kind": "ignited_illumination",
        "trigger": "bonus_action",
        "mode": "toggle",
        "bright_light_radius_ft": 10,
        "additional_dim_light_ft": 10,
        "automatic": True,
    }
    assert touch["mechanics"]["on_hit_effect"] == ""
    assert touch["mechanics"]["on_hit_resolution"]["kind"] == (
        "ignition_ongoing_damage"
    )
    assert touch["mechanics"]["on_hit_resolution"]["trigger_timing"] == "turn_end"
    assert parsed.warnings == ()


def test_magmin_illumination_heading_repairs_only_the_bounded_ocr_comma() -> None:
    assert _repair_layout_ocr_text(
        "Ignited Illumination, As a bonus action, the magmin can set itself ablaze."
    ) == (
        "Ignited Illumination. As a bonus action, the magmin can set itself ablaze."
    )
    assert _repair_layout_ocr_text("Unknown Feature, As a bonus action") == (
        "Unknown Feature, As a bonus action"
    )


def test_magmin_standard_mechanics_accept_ocr_wrapped_source_wording() -> None:
    parsed = parse_2014_statblock(
        MAGMIN.replace("additional 10 ft.", "additional 10 feet.").replace(
            "If the target is a creature or a flammable object,\n"
            "it ignites. Until a creature takes an action to douse the fire, "
            "the creature\n"
            "takes 3 (1d6) fire damage at the end of each of its turns.",
            "If the target is a creature or a\n\n"
            "flammable object, it ignites. Until a creature takes an action to\n\n"
            "douse the fire, the creature takes 3 (1d6) fire damage at the end\n\n"
            "of each of its turns.",
        ),
        source_key="monster-manual-2014",
    )

    illumination = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Ignited Illumination"
    )
    touch = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Touch"
    )
    assert illumination["choices"]["source_trait"]["kind"] == (
        "ignited_illumination"
    )
    assert touch["mechanics"]["on_hit_resolution"]["kind"] == (
        "ignition_ongoing_damage"
    )
    assert parsed.warnings == ()


def test_keen_perception_trait_is_structured() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            (
                "***Keen Hearing and Sight.*** The commoner has advantage on "
                "Wisdom (Perception) checks that rely on hearing or sight.\n\n"
                "###### Actions"
            ),
        ),
        source_key="monster-manual-2014:p349",
    )
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Keen Hearing and Sight"
    )

    assert feature["activation"]["trigger"] == (
        "hearing- or sight-based Perception check"
    )
    assert feature["choices"]["source_trait"] == {
        "kind": "keen_perception",
        "trigger": "perception_check",
        "senses": ["hearing", "sight"],
        "grants": "advantage",
        "automatic": True,
    }
    assert parsed.warnings == ()


def test_orc_war_chief_standard_traits_and_multiattack_are_structured() -> None:
    source = (
        COMMONER.replace(
            "**Challenge** 0 (10 XP)",
            """**Challenge** 4 (1,100 XP)

***Aggressive.*** As a bonus action, the commoner can move up to its speed
toward a hostile creature that it can see.

***Gruumsh's Fury.*** The commoner deals an extra 4 (1d8) damage when it hits
with a weapon attack (included in the attacks).""",
        )
        .replace(
            "###### Actions",
            """###### Actions

***Multiattack.*** The commoner makes two attacks with its club or its spear.

***Battle Cry (1/Day).*** Each creature of the commoner's choice that is within
30 feet of it, can hear it, and is not already affected by Battle Cry gains
advantage on attack rolls until the start of the commoner's next turn. The
commoner can then make one attack as a bonus action.""",
        )
        .replace(
            "*Hit:* 2 (1d4) bludgeoning damage.",
            """*Hit:* 6 (1d4 plus 1d8) bludgeoning damage.

***Spear.*** *Melee or Ranged Weapon Attack:* +2 to hit, reach 5 ft. or range
20/60 ft., one target. *Hit:* 7 (1d6 plus 1d8) piercing damage.""",
        )
    )
    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:p246",
    )
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }
    features = {
        item["name"]: item for item in parsed.sheet["content"]["features"]
    }

    assert activities["Aggressive"]["id"] == "dnd5e.core.monster.aggressive"
    battle_cry = activities["Battle Cry (1/Day)"]
    assert battle_cry["id"] == "dnd5e.core.monster.battle-cry"
    assert battle_cry["uses"] == {
        "label": "Battle Cry (1/Day)",
        "value": 1,
        "max": 1,
        "recovers_on": "long_rest",
        "source_key": "monster-manual-2014:p246",
        "slot_level": 0,
    }
    assert [
        option["id"]
        for option in activities["Multiattack"]["choices"]["multiattack_options"]
    ] == ["melee", "melee-2", "ranged"]
    assert features["Gruumsh's Fury"]["choices"]["source_trait"][
        "embedded_in_weapon_actions"
    ] is True
    assert parsed.warnings == ()


def test_spy_standard_traits_are_structured_from_their_exact_text() -> None:
    source = COMMONER.replace(
        "###### Actions",
        """***Cunning Action.*** On each of its turns, the commoner can use a
bonus action to take the Dash, Disengage, or Hide action.

***Sneak Attack (1/Turn).*** The commoner deals an extra 7 (2d6) damage when it
hits a target with a weapon attack and has advantage on the attack roll, or
when the target is within 5 feet of an ally of the commoner that isn't
incapacitated and the commoner doesn't have disadvantage on the attack roll.

###### Actions""",
    )
    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:p349",
    )
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }
    sneak_attack = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Sneak Attack (1/Turn)"
    )

    assert activities["Cunning Action"]["id"] == (
        "dnd5e.content.srd2014.feature.rogue-cunning-action"
    )
    assert sneak_attack["choices"]["source_trait"] == {
        "kind": "sneak_attack",
        "trigger": "eligible_weapon_hit",
        "damage_formula": "2d6",
        "average_damage": 7,
        "uses_per_turn": 1,
        "requires_finesse_or_ranged": False,
        "ally_within_target_ft": 5,
        "requires_ally_not_incapacitated": True,
        "requires_no_disadvantage": True,
        "alternative": "effective_advantage",
    }
    assert parsed.warnings == ()


def test_magic_resistance_and_evasion_are_structured_from_exact_text() -> None:
    source = COMMONER.replace(
        "###### Actions",
        """***Magic Resistance.*** The arch mage has advantage on saving throws
against spells and other magical effects.

***Evasion.*** If the assassin is subjected to an effect that allows it to make
a Dexterity saving throw to take only half damage, the assassin instead takes
no damage if it succeeds on the saving throw, and only half damage if it fails.

###### Actions""",
    )

    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:standard-save-traits",
    )
    traits = {
        item["name"]: item["choices"]["source_trait"]
        for item in parsed.sheet["content"]["features"]
    }

    assert traits["Magic Resistance"] == {
        "kind": "magic_resistance",
        "trigger": "saving_throw",
        "save_source_kinds": ["spell", "magical_effect"],
        "grants": "advantage",
        "automatic": True,
        "source_excerpt": (
            "The arch mage has advantage on saving throws against spells "
            "and other magical effects."
        ),
    }
    assert traits["Evasion"] == {
        "kind": "evasion",
        "trigger": "dexterity_save_for_half_damage",
        "save_ability": "dexterity",
        "ordinary_successful_save": "half",
        "successful_save": "none",
        "failed_save": "half",
        "automatic": True,
        "source_excerpt": (
            "If the assassin is subjected to an effect that allows it to make "
            "a Dexterity saving throw to take only half damage, the assassin "
            "instead takes no damage if it succeeds on the saving throw, and "
            "only half damage if it fails."
        ),
    }
    assert parsed.warnings == ()


def test_dark_devotion_is_structured_from_exact_text() -> None:
    source_excerpt = (
        "The cultist has advantage on saving throws against being charmed or "
        "frightened ."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            f"***Dark Devotion.*** {source_excerpt}\n\n###### Actions",
        ),
        source_key="monster-manual-2014:cultist",
    )
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Dark Devotion"
    )

    assert feature["choices"]["source_trait"] == {
        "kind": "save_advantage_against_conditions",
        "trigger": "saving_throw",
        "effect_conditions": ["charmed", "frightened"],
        "grants": "advantage",
        "automatic": True,
        "source_excerpt": source_excerpt,
    }
    assert parsed.warnings == ()


def test_archmage_precombat_footnote_is_not_parsed_as_a_spell_name() -> None:
    source = COMMONER.replace(
        "###### Actions",
        """***Spellcasting.*** The archmage is an 18th-level spellcaster. Its
spellcasting ability is Intelligence (spell save DC 17, +9 to hit with spell
attacks). The archmage has the following wizard spells prepared:

Cantrips (at will): fire bolt, light, mage hand, prestidigitation, shocking grasp

1st level (4 slots): detect magic, identify, mage armor*, magic missile

2nd level (3 slots): detect thoughts, mirror image*, misty step

3rd level (3 slots): counterspell, fly*, lightning bolt

4th level (3 slots): banishment, fire shield, stoneskin*

5th level (3 slots): cone of cold, scrying*, wall of force

6th level (1 slot): globe of invulnerability

7th level (1 slot): teleport

8th level (1 slot): mind blank*

9th level (1 slot): time stop

*The archmage casts these spells on itself before combat.*

###### Actions""",
    )

    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:archmage",
    )
    assert parsed.spellcasting is not None
    names = [item["name"] for item in parsed.spellcasting["spells"]]
    assert names[-1] == "time stop"
    assert "mage armor" in names
    assert all("before combat" not in name for name in names)
    spellcasting = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Spellcasting"
    )
    assert "casts these spells on itself before combat" in spellcasting["description"]


def test_source_traits_are_compiled_from_complete_text_not_feature_names() -> None:
    parsed = parse_2014_statblock(
        KOBOLD.replace(
            "***Pack Tactics***.",
            "***Coordinated Assault***.",
        ).replace(
            "***Sunlight Sensitivity***.",
            "***Harsh Light***.",
        ),
        source_key="module-review:renamed-kobold-traits",
    )
    features = {
        item["name"]: item
        for item in parsed.sheet["content"]["features"]
    }

    assert features["Coordinated Assault"]["choices"]["source_trait"]["kind"] == (
        "pack_tactics"
    )
    assert features["Harsh Light"]["choices"]["source_trait"]["kind"] == (
        "sunlight_sensitivity"
    )
    assert parsed.warnings == ()


def test_false_appearance_stays_descriptive_for_agent_adjudication() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            (
                "***False Appearance***. While the gargoyle remains motion less, "
                "it is indistinguishable from an inanimate statue.\n\n"
                "###### Actions"
            ),
        ),
        source_key="monster-manual-2014:gargoyle",
    )
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "False Appearance"
    )

    assert feature["choices"]["manual_ruling"] == {
        "kind": "descriptive_passive",
        "default_resolver": "agent",
        "source_excerpt": (
            "While the gargoyle remains motion less, "
            "it is indistinguishable from an inanimate statue."
        ),
    }
    assert parsed.warnings == (
        "False Appearance: descriptive passive is not automatically settled",
    )


def test_ancient_blue_dragon_standard_actions_are_structured() -> None:
    parsed = parse_2014_statblock(
        """# Ancient Blue Dragon

*Gargantuan dragon, lawful evil*

**Armor Class** 22 (natural armor)
**Hit Points** 481 (26d20 + 208)
**Speed** 40 ft., burrow 40 ft., fly 80 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 29 (+9) | 10 (+0) | 27 (+8) | 18 (+4) | 17 (+3) | 21 (+5) |

**Saving Throws** Dex +7, Con +15, Wis +10, Cha +12
**Skills** Perception +17, Stealth +7
**Damage Immunities** lightning
**Senses** blindsight 60 ft., darkvision 120 ft., passive Perception 27
**Languages** Common, Draconic
**Challenge** 23 (32,500 XP)

***Legendary Resistance (3/Day).*** If the dragon fails a saving throw, it can
choose to succeed instead.

## Actions

***Multiattack.*** The dragon can use its Frightful Presence. It then makes
three attacks: one with its bite and two with its claws.

***Bite.*** *Melee Weapon Attack:* +16 to hit, reach 15 ft., one target.
*Hit:* 20 (2d10 + 9) piercing damage plus 11 (2d10) lightning damage.

***Claw.*** *Melee Weapon Attack:* +16 to hit, reach 10 ft., one target.
*Hit:* 16 (2d6 + 9) slashing damage.

***Tail.*** *Melee Weapon Attack:* +16 to hit, reach 20 ft., one target.
*Hit:* 18 (2d8 + 9) bludgeoning damage.

***Frightful Presence.*** Each creature of the dragon's choice that is within
120 feet of the dragon and aware of it must succeed on a DC 20 Wisdom saving
throw or become frightened for 1 minute. A creature can repeat the saving throw
at the end of each of its turns, ending the effect on itself on a success. If a
creature's saving throw is successful or the effect ends for it, the creature
is immune to the dragon's Frightful Presence for the next 24 hours.

***Lightning Breath (Recharge 5-6).*** The dragon exhales lightning in a
120-foot line that is 10 feet wide. Each creature in that line must make a DC
23 Dexterity saving throw, taking 88 (16d10) lightning damage on a failed save,
or half as much damage on a successful one.

## Legendary Actions

The dragon can take 3 legendary actions, choosing from the options below. Only
one legendary action option can be used at a time and only at the end of
another creature's turn. The dragon regains spent legendary actions at the
start of its turn.

***Detect.*** The dragon makes a Wisdom (Perception) check.

***Tail Attack.*** The dragon makes a tail attack.

***Wing Attack (Costs 2 Actions).*** The dragon beats its wings. Each creature
within 15 feet of the dragon must succeed on a DC 24 Dexterity saving throw or
take 16 (2d6 + 9) bludgeoning damage and be knocked prone. The dragon can then
fly up to half its flying speed.
""",
        source_key="monster-manual-2014:p91",
    )
    activities = {
        item["name"]: item for item in parsed.sheet["content"]["activities"]
    }
    legendary_resistance = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Legendary Resistance (3/Day)"
    )
    assert legendary_resistance["choices"]["manual_ruling"] == {
        "kind": "descriptive_passive",
        "default_resolver": "agent",
        "source_excerpt": (
            "If the dragon fails a saving throw, it can choose to succeed instead."
        ),
    }
    assert activities["Frightful Presence"]["choices"][
        "frightful_presence"
    ]["save_dc"] == 20
    assert activities["Frightful Presence"]["choices"][
        "frightful_presence"
    ]["save_source_kind"] == "nonmagical_effect"
    assert activities["Lightning Breath (Recharge 5-6)"]["choices"][
        "area_save_damage"
    ]["area"] == {
        "shape": "line",
        "length_ft": 120,
        "width_ft": 10,
    }
    assert activities["Lightning Breath (Recharge 5-6)"]["choices"][
        "area_save_damage"
    ]["save_source_kind"] == "nonmagical_effect"
    assert activities["Detect"]["choices"]["legendary_action"]["effect"] == {
        "kind": "skill_check",
        "ability": "wisdom",
        "skill": "perception",
    }
    assert activities["Tail Attack"]["choices"]["legendary_action"]["effect"] == {
        "kind": "weapon_attack",
        "weapon_id": "tail",
        "attack_mode": "melee",
    }
    wing = activities["Wing Attack (Costs 2 Actions)"]
    assert wing["activation"]["cost"] == 2
    assert wing["choices"]["legendary_action"]["effect"]["kind"] == (
        "wing_attack_2014"
    )
    assert wing["choices"]["legendary_action"]["effect"][
        "save_source_kind"
    ] == "nonmagical_effect"
    assert parsed.warnings == (
        "Legendary Resistance (3/Day): descriptive passive is not automatically settled",
    )


def test_assassinate_is_structured_from_exact_text() -> None:
    source_text = (
        "During its first turn, the assassin has advantage on attack rolls "
        "against any creature that hasn't taken a turn. Any hit the assassin "
        "scores against a surprised creature is a critical hit."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            f"***Assassinate***. {source_text}\n\n###### Actions",
        ),
        source_key="monster-manual-2014:assassin",
    )
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Assassinate"
    )

    assert feature["choices"]["source_trait"] == {
        "kind": "assassinate",
        "trigger": "attack_roll",
        "attacker_turn": "first",
        "advantage_if_target_has_not_taken_turn": True,
        "critical_on_hit_if_target_surprised": True,
        "automatic": True,
        "source_excerpt": source_text,
    }
    assert parsed.warnings == ()


def test_weapon_hit_save_damage_is_structured_from_exact_text() -> None:
    source_excerpt = (
        "and the target must make a DC 15 Constitution saving throw, taking "
        "24 (7d6) poison damage on a failed save, or half as much damage on a "
        "successful one."
    )
    parsed = parse_2014_statblock(
        COMMONER.replace("### Commoner", "### Assassin").replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "***Shortsword***. *Melee Weapon Attack:* +7 to hit, reach 5 ft., "
            "one target.\n"
            "*Hit:* 6 (1d6 + 3) piercing damage, and the target must\n\n"
            "make a DC 15 Constitution saving throw, taking 24 (7d6)\n\n"
            "poison damage on a failed save, or half as much damage on a\n\n"
            "successful one.\n\n"
            "Assassins are remorseless killers who work for anyone who can "
            "afford them.",
        ),
        source_key="monster-manual-2014:assassin",
    )
    shortsword = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Shortsword"
    )

    assert shortsword["mechanics"]["on_hit_effect"] == ""
    assert shortsword["mechanics"]["on_hit_resolution"] == {
        "kind": "save_damage",
        "trigger": "weapon_hit",
        "save_ability": "constitution",
        "save_dc": 15,
        "damage_formula": "7d6",
        "average_damage": 24,
        "damage_type": "poison",
        "half_on_success": True,
        "save_source_kind": "nonmagical_effect",
        "automatic": True,
        "source_excerpt": source_excerpt,
    }
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Shortsword: trailing creature prose excluded from action settlement",
    )


def test_merrow_standard_traits_and_harpoon_are_fully_structured() -> None:
    parsed = parse_2014_statblock(
        """# Merrow

*Large monstrosity, chaotic evil*

**Armor Class** 13 (natural armor)
**Hit Points** 45 (6d10 + 12)
**Speed** 10 ft., swim 40 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 18 (+4) | 10 (+0) | 15 (+2) | 8 (-1) | 10 (+0) | 9 (-1) |

**Senses** darkvision 60 ft., passive Perception 10
**Languages** Abyssal, Aquan
**Challenge** 2 (450 XP)

***Amphibious***. The merrow can breathe air and water.

## Actions

***Multiattack***. The merrow makes two attacks: one with its bite and one with
its claws or harpoon.

***Bite***. *Melee Weapon Attack:* +6 to hit, reach 5 ft., one target.
*Hit:* 8 (1d8 + 4) piercing damage.

***Claws***. *Melee Weapon Attack:* +6 to hit, reach 5 ft., one target.
*Hit:* 9 (2d4 + 4) slashing damage.

***Harpoon***. *Melee or Ranged Weapon Attack:* +6 to hit, reach 5 ft. or range
20/60 ft., one target. *Hit:* 11 (2d6 + 4) piercing damage. If the target is a
Huge or smaller creature, it must succeed on a Strength contest against the
merrow or be pulled up to 20 feet toward the merrow.
""",
        source_key="monster-manual-2014:p220",
    )

    amphibious = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Amphibious"
    )
    assert amphibious["choices"]["source_trait"] == {
        "kind": "breathing_media",
        "trigger": "environmental_breathing",
        "media": ["air", "water"],
        "automatic": True,
        "source_excerpt": "The merrow can breathe air and water.",
    }
    multiattack = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Multiattack"
    )
    assert multiattack["choices"]["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [
                {"weapon_id": "bite", "attack_mode": "melee", "count": 1},
                {"weapon_id": "claws", "attack_mode": "melee", "count": 1},
            ],
        },
        {
            "id": "melee-2",
            "attacks": [
                {"weapon_id": "bite", "attack_mode": "melee", "count": 1},
                {"weapon_id": "harpoon", "attack_mode": "melee", "count": 1},
            ],
        },
        {
            "id": "mixed",
            "attacks": [
                {"weapon_id": "bite", "attack_mode": "melee", "count": 1},
                {"weapon_id": "harpoon", "attack_mode": "ranged", "count": 1},
            ],
        },
    ]
    harpoon = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Harpoon"
    )
    assert harpoon["mechanics"]["on_hit_effect"] == ""
    assert harpoon["mechanics"]["on_hit_resolution"] == {
        "kind": "contest_pull",
        "trigger": "weapon_hit",
        "required_target_kind": "creature",
        "maximum_target_size": "huge",
        "source_ability": "strength",
        "target_ability": "strength",
        "ties": "no_movement",
        "maximum_distance_ft": 20,
        "direction": "toward_source",
        "automatic": True,
        "source_excerpt": (
            "If the target is a Huge or smaller creature, it must succeed on a "
            "Strength contest against the merrow or be pulled up to 20 feet "
            "toward the merrow."
        ),
    }
    assert parsed.warnings == ()


def test_source_trait_with_unparsed_clause_stays_an_agent_ruling() -> None:
    parsed = parse_2014_statblock(
        KOBOLD.replace(
            "ally isn't incapacitated.",
            "ally isn't incapacitated. The kobold also deals 2 extra damage.",
        ),
        source_key="module-review:extended-pack-tactics",
    )
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Pack Tactics"
    )

    assert feature["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert "Pack Tactics: descriptive passive is not automatically settled" in (
        parsed.warnings
    )


def test_statblock_entries_accept_period_inside_emphasis() -> None:
    markdown = (
        KOBOLD.replace(
            "***Sunlight Sensitivity***.",
            "***Sunlight Sensitivity.***",
        )
        .replace("***Pack Tactics***.", "***Pack Tactics.***")
        .replace("***Dagger***.", "***Dagger.***")
        .replace("***Sling***.", "***Sling.***")
    )

    parsed = parse_2014_statblock(
        markdown,
        source_key="monster-manual-2014:p195-visual-review",
    )

    assert {
        item["name"]
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    } == {"Dagger", "Sling"}
    assert {
        item["name"]
        for item in parsed.sheet["content"]["features"]
    } >= {"Sunlight Sensitivity", "Pack Tactics"}
    assert parsed.warnings == ()


def test_effect_only_weapon_attack_preserves_web_ruling_without_fake_damage() -> None:
    parsed = parse_2014_statblock(GIANT_SPIDER, source_key="module-review:giant-spider")
    assert parsed.sheet["traits"]["languages"] == []
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
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Shortbow: trailing creature prose excluded from action settlement",
    )


def test_weapon_range_recovers_ocr_f_separator_inside_attack_grammar() -> None:
    source = BANDIT_CAPTAIN.replace("range 20/60 ft.", "range 20f60 ft.")
    parsed = parse_2014_statblock(source, source_key="module-review:ocr-bandit-captain")
    dagger = next(
        item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Dagger"
    )
    attacks = {
        item["item_id"]: item
        for item in derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"]
    }

    assert "range 20f60 ft." in dagger["description"]
    assert attacks["dagger"]["thrown_range_ft"] == {"normal": 20, "long": 60}


def test_weapon_range_does_not_recover_ambiguous_prose_as_separator() -> None:
    source = BANDIT_CAPTAIN.replace("range 20/60 ft.", "range 20fuzzy60 ft.")
    parsed = parse_2014_statblock(
        source,
        source_key="module-review:ambiguous-bandit-captain",
    )
    dagger = next(
        item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Dagger"
    )
    attacks = {
        item["item_id"]: item
        for item in derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"]
    }

    assert "range 20fuzzy60 ft." in dagger["description"]
    assert attacks["dagger"]["thrown_range_ft"] == {"normal": 0, "long": 0}


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


def test_multiattack_parses_once_with_each_weapon_composition() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            (
                "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
                "*Hit:* 2 (1d4) bludgeoning damage."
            ),
            (
                "***Multiattack***. The drake attacks twice, once with its bite and "
                "once with its tail.\n\n"
                "***Bite***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target. "
                "*Hit:* 7 (1d8 + 3) piercing damage.\n\n"
                "***Tail***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target. "
                "*Hit:* 6 (1d6 + 3) bludgeoning damage."
            ),
        ),
        source_key="module-review:guard-drake",
    )
    derived = derive_character_sheet(parsed.sheet)

    assert derived["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [
                {"weapon_id": "bite", "attack_mode": "melee", "count": 1},
                {"weapon_id": "tail", "attack_mode": "melee", "count": 1},
            ],
        }
    ]
    assert parsed.warnings == ()


def test_multiattack_quantity_does_not_match_inside_creature_name() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace("### Commoner", "### Pentadrone")
        .replace(
            "###### Actions",
            (
                "###### Actions\n\n"
                "***Multiattack.*** The pentadrone makes five arm attacks."
            ),
        )
        .replace("***Club***", "***Arm***"),
        source_key="monster-manual-2014:pentadrone",
    )

    assert derive_character_sheet(parsed.sheet)["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [{"weapon_id": "arm", "attack_mode": "melee", "count": 5}],
        }
    ]
    assert parsed.warnings == ()


def test_multiattack_parses_complete_melee_or_ranged_compositions() -> None:
    source = COMMONER.replace(
        "###### Actions",
        (
            "###### Actions\n\n"
            "***Multiattack.*** The centaur makes two attacks: one with its pike "
            "and one with its hooves or two with its longbow.\n\n"
            "***Pike.*** Melee Weapon Attack: +6 to hit, reach 10 ft., one target. "
            "Hit: 9 (1d10 + 4) piercing damage.\n\n"
            "***Hooves.*** Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
            "Hit: 11 (2d6 + 4) bludgeoning damage.\n\n"
            "***Longbow.*** Ranged Weapon Attack: +4 to hit, range 150/600 ft., "
            "one target. Hit: 6 (1d8 + 2) piercing damage."
        ),
    )
    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:centaur",
    )

    assert derive_character_sheet(parsed.sheet)["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [
                {"weapon_id": "pike", "attack_mode": "melee", "count": 1},
                {"weapon_id": "hooves", "attack_mode": "melee", "count": 1},
            ],
        },
        {
            "id": "ranged",
            "attacks": [
                {"weapon_id": "longbow", "attack_mode": "ranged", "count": 2}
            ],
        },
    ]
    assert parsed.warnings == ()


def test_multiattack_matches_qualified_and_singularized_weapon_names() -> None:
    source = COMMONER.replace(
        "###### Actions",
        (
            "###### Actions\n\n"
            "***Multiattack.*** The slaad makes three attacks: one with its bite "
            "and two with its claws or greatsword.\n\n"
            "***Bite (Slaad Form Only).*** Melee Weapon Attack: +7 to hit, reach "
            "5 ft., one target. Hit: 6 (1d6 + 3) piercing damage.\n\n"
            "***Claws (Slaad Form Only).*** Melee Weapon Attack: +7 to hit, reach "
            "5 ft., one target. Hit: 8 (1d10 + 3) slashing damage.\n\n"
            "***Greatsword.*** Melee Weapon Attack: +7 to hit, reach 5 ft., one "
            "target. Hit: 10 (2d6 + 3) slashing damage."
        ),
    )
    parsed = parse_2014_statblock(
        source,
        source_key="monster-manual-2014:slaad",
    )

    options = derive_character_sheet(parsed.sheet)["multiattack_options"]
    assert [item["attacks"] for item in options] == [
        [
            {
                "weapon_id": "bite-slaad-form-only",
                "attack_mode": "melee",
                "count": 1,
            },
            {
                "weapon_id": "claws-slaad-form-only",
                "attack_mode": "melee",
                "count": 2,
            },
        ],
        [
            {
                "weapon_id": "bite-slaad-form-only",
                "attack_mode": "melee",
                "count": 1,
            },
            {"weapon_id": "greatsword", "attack_mode": "melee", "count": 2},
        ],
    ]
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


def test_agent_review_can_fill_unresolved_multiattack_without_new_text_heuristics() -> None:
    source_excerpt = (
        "In one coordinated assault, the captain slashes with its scimitar "
        "and follows with its dagger."
    )
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            (
                "The captain makes three melee attacks: two with its scimitar and one with its\n"
                "dagger. Or the captain makes two ranged attacks with its daggers."
            ),
            source_excerpt,
        ),
        source_key="module-review:agent-filled-captain",
    )

    assert derive_character_sheet(parsed.sheet)["multiattack_options"] == []
    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "multiattack_options": [
                {
                    "activity_id": "multiattack-action",
                    "source_excerpt": source_excerpt,
                    "reason": (
                        "The exact source describes one scimitar attack followed by "
                        "one dagger attack."
                    ),
                    "options": [
                        {
                            "id": "coordinated-assault",
                            "attacks": [
                                {
                                    "weapon_id": "scimitar",
                                    "attack_mode": "melee",
                                    "count": 1,
                                },
                                {
                                    "weapon_id": "dagger",
                                    "attack_mode": "melee",
                                    "count": 1,
                                },
                            ],
                        }
                    ],
                }
            ]
        },
    )

    assert derive_character_sheet(filled["sheet"])["multiattack_options"] == [
        {
            "id": "coordinated-assault",
            "attacks": [
                {"weapon_id": "scimitar", "attack_mode": "melee", "count": 1},
                {"weapon_id": "dagger", "attack_mode": "melee", "count": 1},
            ],
        }
    ]
    assert filled["resolved_warnings"] == [
        "Multiattack: Multiattack composition requires a DM ruling"
    ]
    assert filled["fill"]["multiattack_options"][0]["default_resolver"] == "agent"
    assert filled["fill"]["multiattack_options"][0]["ruling_kind"] == (
        "module_specific_procedure"
    )


def test_qualified_multiattack_keeps_source_name_and_accepts_reviewed_fill() -> None:
    source_excerpt = (
        "The yuan-ti makes two ranged attacks or two melee attacks, "
        "but can use its bite only once."
    )
    qualified = COMMONER.replace("### Commoner", "### Yuan-ti Malison")
    qualified = qualified.replace(
        (
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage."
        ),
        (
            "***Multiattack (Yuan-ti Form Only)***. "
            f"{source_excerpt}\n\n"
            "***Bite***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one creature. "
            "*Hit:* 5 (1d4 + 3) piercing damage.\n\n"
            "***Scimitar***. *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target. "
            "*Hit:* 6 (1d6 + 3) slashing damage.\n\n"
            "***Longbow (Yuan-ti Form Only)***. *Ranged Weapon Attack:* +4 to hit, "
            "range 150/600 ft., one target. *Hit:* 6 (1d8 + 2) piercing damage."
        ),
    )

    parsed = parse_2014_statblock(
        qualified,
        source_key="rule-source:mm2014/page-310/yuan-ti-malison-type-1",
    )
    activity = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["id"] == "multiattack-yuan-ti-form-only-action"
    )
    assert activity["name"] == "Multiattack (Yuan-ti Form Only)"
    assert activity["mechanic_refs"] == [MULTIATTACK_MECHANIC_ID]
    assert derive_character_sheet(parsed.sheet)["multiattack_options"] == []

    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "multiattack_options": [
                {
                    "activity_id": activity["id"],
                    "source_excerpt": source_excerpt,
                    "reason": (
                        "The two legal melee compositions and the ranged "
                        "composition use only parsed source weapons."
                    ),
                    "options": [
                        {
                            "id": "two-longbows",
                            "attacks": [
                                {
                                    "weapon_id": "longbow-yuan-ti-form-only",
                                    "attack_mode": "ranged",
                                    "count": 2,
                                }
                            ],
                        },
                        {
                            "id": "bite-and-scimitar",
                            "attacks": [
                                {
                                    "weapon_id": "bite",
                                    "attack_mode": "melee",
                                    "count": 1,
                                },
                                {
                                    "weapon_id": "scimitar",
                                    "attack_mode": "melee",
                                    "count": 1,
                                },
                            ],
                        },
                    ],
                }
            ]
        },
    )

    assert [item["id"] for item in derive_character_sheet(filled["sheet"])[
        "multiattack_options"
    ]] == ["two-longbows", "bite-and-scimitar"]
    assert filled["resolved_warnings"] == [
        "Multiattack (Yuan-ti Form Only): "
        "Multiattack composition requires a DM ruling"
    ]


def test_agent_reviewed_multiattack_fill_requires_exact_source_and_parsed_weapons() -> None:
    source_excerpt = (
        "In one coordinated assault, the captain slashes with its scimitar "
        "and follows with its dagger."
    )
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            (
                "The captain makes three melee attacks: two with its scimitar and one with its\n"
                "dagger. Or the captain makes two ranged attacks with its daggers."
            ),
            source_excerpt,
        ),
        source_key="module-review:agent-fill-validation",
    )
    fill = {
        "multiattack_options": [
            {
                "activity_id": "multiattack-action",
                "source_excerpt": "The captain makes two attacks.",
                "reason": "Reviewed exact source.",
                "options": [
                    {
                        "id": "coordinated-assault",
                        "attacks": [
                            {
                                "weapon_id": "invented-claw",
                                "attack_mode": "melee",
                                "count": 2,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    with pytest.raises(StatblockImportError, match="exactly match"):
        apply_reviewed_statblock_fill(parsed.sheet, fill)

    fill["multiattack_options"][0]["source_excerpt"] = source_excerpt
    with pytest.raises(StatblockImportError, match="parsed weapon"):
        apply_reviewed_statblock_fill(parsed.sheet, fill)


def test_agent_reviewed_statblock_fill_rejects_an_empty_submission() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="module-review:empty-agent-fill",
    )

    with pytest.raises(StatblockImportError, match="at least one"):
        apply_reviewed_statblock_fill(parsed.sheet, {"multiattack_options": []})


def test_agent_review_can_confirm_parser_recognized_multiattack() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="module-review:agent-confirmed-captain",
    )
    multiattack = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Multiattack"
    )

    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "multiattack_options": [
                {
                    "activity_id": multiattack["id"],
                    "source_excerpt": multiattack["description"],
                    "reason": (
                        "The Agent checked the exact module text and confirmed both "
                        "printed alternatives."
                    ),
                    "options": multiattack["choices"]["multiattack_options"],
                }
            ]
        },
    )

    assert derive_character_sheet(filled["sheet"])["multiattack_options"] == (
        derive_character_sheet(parsed.sheet)["multiattack_options"]
    )
    assert filled["resolved_warnings"] == []
    assert filled["fill"]["multiattack_options"][0]["default_resolver"] == "agent"


def test_agent_review_can_add_a_source_cited_variant_weapon_action() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="rule-review:base-statblock",
    )
    multiattack = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Multiattack"
    )
    web_garrote = (
        "Melee Weapon Attack: +4 to hit, reach 5 ft., one Medium or Small "
        "creature against which the ettercap has advantage on the attack roll. "
        "Hit: 4 (1d4 + 2) bludgeoning damage, and the target is grappled "
        "(escape DC 12). Until this grapple ends, the target can't breathe, "
        "and the ettercap has advantage on attack rolls against it."
    )

    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "additional_actions": [
                {
                    "name": "Web Garrote",
                    "source_ref": "rule-chunk:web-garrote",
                    "source_excerpt": web_garrote,
                    "reason": (
                        "The reviewed adjacent-column variant grants this exact "
                        "weapon action to an armed ettercap."
                    ),
                }
            ],
            "multiattack_options": [
                {
                    "activity_id": multiattack["id"],
                    "source_excerpt": multiattack["description"],
                    "reason": "The Agent confirmed the printed base composition.",
                    "options": multiattack["choices"]["multiattack_options"],
                }
            ],
        },
    )

    weapon = next(
        item
        for item in filled["sheet"]["inventory"]["items"]
        if item["id"] == "web-garrote"
    )
    assert weapon["source_key"] == "agent-fill:rule-chunk:web-garrote"
    assert weapon["mechanics"]["attack_bonus_override"] == 4
    assert weapon["mechanics"]["damage_formula"] == "1d4"
    assert weapon["mechanics"]["damage_bonus_override"] == 2
    assert weapon["mechanics"]["damage_type"] == "bludgeoning"
    assert "target is grappled" in weapon["mechanics"]["on_hit_effect"]
    assert weapon["mechanics"]["required_target_sizes"] == ["medium", "small"]
    assert weapon["mechanics"]["requires_attack_advantage"] is True
    assert filled["fill"]["additional_actions"][0]["id"] == "web-garrote"
    assert filled["added_warnings"] == [
        "Web Garrote: on-hit effect requires DM settlement"
    ]
    replayed = apply_reviewed_statblock_fill(parsed.sheet, filled["fill"])
    assert replayed["fill"] == filled["fill"]

    mismatched = deepcopy(filled["fill"])
    mismatched["additional_actions"][0]["id"] = "agent-overridden-id"
    with pytest.raises(StatblockImportError, match="parser-derived weapon id"):
        apply_reviewed_statblock_fill(parsed.sheet, mismatched)


def test_agent_reviewed_additional_action_rejects_unmanaged_or_duplicate_actions() -> None:
    parsed = parse_2014_statblock(
        COMMONER,
        source_key="rule-review:base-commoner",
    )
    declaration = {
        "name": "Web Garrote",
        "source_ref": "free-text:web-garrote",
        "source_excerpt": (
            "Melee Weapon Attack: +4 to hit, reach 5 ft., one target. "
            "Hit: 4 (1d4 + 2) bludgeoning damage."
        ),
        "reason": "The Agent reviewed the printed variant action.",
    }
    with pytest.raises(StatblockImportError, match="managed source"):
        apply_reviewed_statblock_fill(
            parsed.sheet,
            {"additional_actions": [declaration]},
        )

    declaration["source_ref"] = "rule-chunk:web-garrote"
    declaration["name"] = "Club"
    with pytest.raises(StatblockImportError, match="duplicates"):
        apply_reviewed_statblock_fill(
            parsed.sheet,
            {"additional_actions": [declaration]},
        )


def test_agent_review_can_keep_custom_multiattack_as_agent_ruling() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="module-review:agent-ruled-captain",
    )
    multiattack = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Multiattack"
    )

    filled = apply_reviewed_statblock_fill(
        parsed.sheet,
        {
            "multiattack_options": [
                {
                    "activity_id": multiattack["id"],
                    "source_excerpt": multiattack["description"],
                    "reason": (
                        "The source mixes a module procedure that the structured "
                        "weapon-only executor cannot represent."
                    ),
                    "resolution": "agent_ruling",
                }
            ]
        },
    )

    assert derive_character_sheet(filled["sheet"])["multiattack_options"] == []
    retained = next(
        item
        for item in filled["sheet"]["content"]["activities"]
        if item["id"] == multiattack["id"]
    )
    assert retained["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": multiattack["description"],
    }
    assert filled["resolved_warnings"] == []
    assert filled["added_warnings"] == [
        "Multiattack: Multiattack composition requires a DM ruling"
    ]
    assert filled["fill"]["multiattack_options"][0]["resolution"] == "agent_ruling"


def test_agent_ruling_multiattack_rejects_structured_options() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN,
        source_key="module-review:invalid-agent-ruling-captain",
    )
    multiattack = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Multiattack"
    )

    with pytest.raises(StatblockImportError, match="must not contain options"):
        apply_reviewed_statblock_fill(
            parsed.sheet,
            {
                "multiattack_options": [
                    {
                        "activity_id": multiattack["id"],
                        "source_excerpt": multiattack["description"],
                        "reason": "Retain as an Agent ruling.",
                        "resolution": "agent_ruling",
                        "options": multiattack["choices"]["multiattack_options"],
                    }
                ]
            },
        )


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


def test_source_parry_excludes_a_following_creature_lore_paragraph() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            (
                "The captain adds 2 to its AC against one melee attack that would hit it. "
                "To do so, the captain must see the attacker and be wielding a melee weapon."
            ),
        )
        + """

It takes a strong personality and ruthless cunning to keep a gang of bandits
in line. The bandit captain has these qualities in spades.
""",
        source_key="srd-bandit-captain-with-lore",
    )

    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )
    assert parry["choices"]["reaction_defense"]["kind"] == "armor_class_bonus"
    assert "strong personality" not in parry["description"]
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Parry: trailing creature prose excluded from reaction settlement",
    )


def test_source_parry_excludes_conflated_trailing_creature_lore() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            (
                "The captain adds 2 to its AC against one melee attack that would hit it. "
                "To do so, the captain must see the attacker and be wielding a melee weapon. "
                "It takes a strong personality, ruthless cunning, and a silver tongue to "
                "keep a gang of bandits in line. The bandit captain has these qualities "
                "in spades. To keep the crew in line, the captain must mete out rewards "
                "and punishment on a regular basis."
            ),
        ),
        source_key="rulebook-ocr:bandit-captain-with-conflated-lore",
    )

    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )
    assert parry["description"] == (
        "The captain adds 2 to its AC against one melee attack that would hit it. "
        "To do so, the captain must see the attacker and be wielding a melee weapon."
    )
    assert parry["choices"]["reaction_defense"] == {
        "kind": "armor_class_bonus",
        "bonus": 2,
        "attack_modes": ["melee"],
        "requires_visible_attacker": True,
        "requires_wielded_melee_weapon": True,
    }
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Parry: trailing creature prose excluded from reaction settlement",
    )


@pytest.mark.parametrize(
    "reaction",
    [
        (
            "The noble adds 2 to its AC aga inst one melee attack that would hit it. "
            "To do so, the noble mu st see the attacker and be wielding a melee weapon."
        ),
        (
            "The veteran adds 3 to its AC against one melee attack that would hit it. "
            "To do so, the veteran must se e the attacker and be wielding a melee weapon."
        ),
        (
            "The hobgoblin adds 3 to its AC against one melee attack that would hit it. "
            "To do so, the hobgoblin mus t see the attacker and be wielding a melee weapon."
        ),
        (
            "Th e death kni ght add s 6 to its AC against one melee attack that wo uld "
            "hit it. To do so, the dea th knight mu st see the attacker and be wield ing "
            "a me lee wea pon."
        ),
    ],
)
def test_source_parry_repairs_rulebook_ocr_word_splits(reaction: str) -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            reaction,
        ),
        source_key="rulebook-ocr:parry",
    )

    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )
    defense = parry["choices"]["reaction_defense"]
    assert defense["kind"] == "armor_class_bonus"
    assert defense["bonus"] in {2, 3, 6}
    assert defense["attack_modes"] == ["melee"]
    assert defense["requires_visible_attacker"] is True
    assert defense["requires_wielded_melee_weapon"] is True
    assert parsed.warnings == ()
    assert parsed.normalization_notes == (
        "Parry: standard reaction OCR word splits repaired",
    )


def test_source_parry_rejects_mismatched_requirement_subject() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            (
                "The captain adds 2 to its AC against one melee attack that would hit it. "
                "To do so, the knight must see the attacker and be wielding a melee weapon."
            ),
        ),
        source_key="module-review:mismatched-parry-subject",
    )
    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )

    assert parry["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert "Parry: descriptive reaction is not automatically settled" in parsed.warnings


def test_source_parry_excludes_noble_lore_that_uses_wield_descriptively() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "The captain adds 2 to its AC against one melee attack that would hit it.",
            (
                "The noble adds 2 to its AC aga inst one melee attack that would hit it. "
                "To do so, the noble mu st see the attacker and be wielding a melee weapon. "
                "Nobles wield great authority and influence as members of the upper class, "
                "possessing wealth and connections that can make them as powerful as generals."
            ),
        ),
        source_key="rulebook-ocr:noble-with-lore",
    )
    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )

    assert parry["choices"]["reaction_defense"]["requires_wielded_melee_weapon"] is True
    assert "great authority" not in parry["description"]
    assert parsed.warnings == ()


def test_source_parry_keeps_unparsed_wielded_shield_extension_for_agent() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "one melee attack that would hit it.",
            "one melee attack that would hit it. It wields a shield until its next turn.",
        ),
        source_key="module-review:extended-parry-shield",
    )
    parry = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )

    assert parry["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert "Parry: descriptive reaction is not automatically settled" in parsed.warnings


def test_reaction_defense_is_compiled_from_complete_text_not_activity_name() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace("***Parry***.", "***Deflect***."),
        source_key="module-review:renamed-parry",
    )
    reaction = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Deflect"
    )

    assert reaction["choices"]["reaction_defense"]["kind"] == "armor_class_bonus"
    assert parsed.warnings == ()


def test_reaction_defense_with_unparsed_clause_stays_an_agent_ruling() -> None:
    parsed = parse_2014_statblock(
        BANDIT_CAPTAIN.replace(
            "one melee attack that would hit it.",
            "one melee attack that would hit it. It may then move 10 feet.",
        ),
        source_key="module-review:extended-parry",
    )
    reaction = next(
        item
        for item in parsed.sheet["content"]["activities"]
        if item["name"] == "Parry"
    )

    assert reaction["choices"]["manual_ruling"]["default_resolver"] == "agent"
    assert "Parry: descriptive reaction is not automatically settled" in parsed.warnings


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


def test_spellcasting_repairs_split_of_glyph_in_spell_name() -> None:
    parsed = parse_2014_statblock(
        """### Ritual Wizard

*Medium humanoid, neutral evil*

**Armor Class** 12
**Hit Points** 40 (9d8)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 9 (-1) | 14 (+2) | 11 (+0) | 17 (+3) | 12 (+1) | 11 (+0) |

**Senses** passive Perception 11
**Languages** Common
**Challenge** 6 (2,300 XP)

***Spellcasting***. The wizard is an 11th-level spellcaster. Its
spellcasting ability is Intelligence (spell save DC 15, +7 to hit with
spell attacks). It has the following wizard spells prepared:

6th level (1 slot): globe o f invulnerability

###### Actions

***Quarterstaff***. *Melee Weapon Attack:* +3 to hit, reach 5 ft.,
one target. *Hit:* 3 (1d6) bludgeoning damage.
""",
        source_key="module-chunk:split-spell-name",
    )

    assert parsed.spellcasting is not None
    assert [item["name"] for item in parsed.spellcasting["spells"]] == [
        "globe of invulnerability"
    ]


def test_spellcasting_preserves_source_qualifier_without_changing_spell_identity() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "###### Actions",
            """***Spellcasting.*** The commoner is a 3rd-level spellcaster. Its
spellcasting ability is Wisdom (spell save DC 11, +3 to hit with spell
attacks). The commoner has the following cleric spells prepared:

Cantrips (at will): guidance

1st level (4 slots): bless

2nd level (2 slots): spiritual weapon (spear)

###### Actions""",
        ),
        source_key="monster-manual:orc-eye-of-gruumsh",
    )

    assert parsed.spellcasting is not None
    spiritual_weapon = parsed.spellcasting["spells"][-1]
    assert spiritual_weapon == {
        "name": "spiritual weapon",
        "source_name": "spiritual weapon (spear)",
        "source_qualifier": "spear",
        "level": 2,
        "at_will": False,
    }


def test_innate_spellcasting_preserves_at_will_and_per_day_resources() -> None:
    parsed = parse_2014_statblock(
        """# Yuan-ti Malison

*Medium monstrosity (shapechanger, yuan-ti), neutral evil*

**Armor Class** 12
**Hit Points** 66 (12d8 + 12)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|
| 16 (+3) | 14 (+2) | 13 (+1) | 14 (+2) | 12 (+1) | 16 (+3) |

**Senses** darkvision 60 ft., passive Perception 11
**Languages** Abyssal, Common, Draconic
**Challenge** 3 (700 XP)

***Innate Spellcasting (Yuan-ti Form Only).*** The yuan-ti's innate
spellcasting ability is Charisma (spell save DC 13). The yuan-ti can innately
cast the following spells, requiring no material components:

At will: animal friendship (snakes only)

3/day: suggestion

## Actions

***Bite.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 5 (1d4 + 3) piercing damage.
""",
        source_key="module-review:yuan-ti-malison",
    )

    assert parsed.spellcasting is not None
    assert parsed.spellcasting["innate"] is True
    assert parsed.spellcasting["no_material_components"] is True
    assert parsed.spellcasting["slots"] == {}
    assert parsed.spellcasting["spells"] == [
        {
            "name": "animal friendship",
            "source_name": "animal friendship (snakes only)",
            "source_qualifier": "snakes only",
            "level": None,
            "at_will": True,
            "uses_per_day": None,
            "uses_are_independent": True,
            "usage_group": "",
        },
        {
            "name": "suggestion",
            "source_name": "suggestion",
            "source_qualifier": "",
            "level": None,
            "at_will": False,
            "uses_per_day": 3,
            "uses_are_independent": True,
            "usage_group": "daily-3-2",
        },
    ]
    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Innate Spellcasting (Yuan-ti Form Only)"
    )
    assert "manual_ruling" not in feature["choices"]
    assert not any(
        warning.startswith("Innate Spellcasting")
        for warning in parsed.warnings
    )


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


def test_unstructured_statblock_passive_is_an_agent_ruling() -> None:
    parsed = parse_2014_statblock(
        """# Peryton

*Medium monstrosity, chaotic evil*

**Armor Class** 13 (natural armor)
**Hit Points** 33 (6d8 + 6)
**Speed** 20 ft., fly 60 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---|---|---|---|---|---|---|
| 16 (+3) | 12 (+1) | 13 (+1) | 9 (-1) | 12 (+1) | 10 (+0) |

**Senses** passive Perception 15
**Languages** understands Common and Elvish but can't speak
**Challenge** 2 (450 XP)

***Dive Attack.*** If the peryton is flying and dives at least 30 feet straight
toward a target and then hits it with a melee weapon attack, the attack deals an
extra 9 (2d8) damage to the target.

## Actions

***Gore.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target.
*Hit:* 7 (1d8 + 3) piercing damage.
""",
        source_key="monster-manual-2014:p252",
    )

    feature = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["id"] == "dive-attack-passive"
    )
    assert feature["choices"]["manual_ruling"] == {
        "kind": "descriptive_passive",
        "default_resolver": "agent",
        "source_excerpt": (
            "If the peryton is flying and dives at least 30 feet straight toward a "
            "target and then hits it with a melee weapon attack, the attack deals "
            "an extra 9 (2d8) damage to the target."
        ),
    }
    assert parsed.warnings == (
        "Dive Attack: descriptive passive is not automatically settled",
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
            "ability_scores": {
                "intelligence": 10,
                "wisdom": 10,
            },
            "alignment": "chaotic evil",
            "darkvision_ft": 60,
            "languages": ["Common", "Elvish"],
            "condition_immunities": ["Poisoned", "Exhaustion"],
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
                    "reach_ft": 15,
                }
            },
        },
    )
    derived = derive_character_sheet(sheet)

    assert sheet["combat"]["hp"] == {"value": 1, "max": 4, "temp": 0}
    assert sheet["progression"]["species"] == "undead"
    assert derived["armor_class"] == 12
    assert derived["ability_scores"]["intelligence"] == 10
    assert derived["ability_scores"]["wisdom"] == 10
    assert derived["passive_perception"] == 10
    assert sheet["traits"]["alignment"] == "chaotic evil"
    assert sheet["traits"]["senses"]["darkvision"] == 60
    assert sheet["traits"]["languages"] == ["Common", "Elvish"]
    assert sheet["traits"]["condition_immunities"] == ["poisoned", "exhaustion"]
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
    assert derived["inventory"]["weapon_attacks"][0]["reach_ft"] == 15
    attack = sheet["inventory"]["items"][0]
    assert "*Melee Weapon Attack:* +2 to hit" in attack["description"]
    assert "reach 15 ft." in attack["description"]
    assert "1d4 bludgeoning damage" not in attack["description"]
    assert "1d4 force damage" in attack["description"]
    assert "Variant source: module-scene:d12" in attack["description"]


def test_source_bound_variant_can_replace_ranged_attack_damage_and_range() -> None:
    parsed = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "***Rock***. *Ranged Weapon Attack:* +2 to hit, range 60/240 ft., "
            "one target.\n*Hit:* 2 (1d4) bludgeoning damage.",
        ),
        source_key="srd-commoner-rock",
    )

    sheet = apply_statblock_variant(
        parsed.sheet,
        {
            "source_ref": "module-chunk:foundry-lower-level",
            "action_overrides": {
                "rock": {
                    "id": "molten-iron",
                    "name": "Molten Iron",
                    "damage_formula": "3d6",
                    "damage_bonus_override": 7,
                    "normal_range_ft": 30,
                    "long_range_ft": 120,
                    "additional_damage": [
                        {
                            "damage_formula": "4d10",
                            "damage_bonus": 0,
                            "damage_type": "fire",
                        }
                    ],
                }
            },
        },
    )

    attack = next(
        item
        for item in derive_character_sheet(sheet)["inventory"]["weapon_attacks"]
        if item["item_id"] == "molten-iron"
    )
    assert attack["range_ft"] == {"normal": 30, "long": 120}
    assert attack["damage_expression"] == "3d6 + 7"
    assert [
        {
            key: part[key]
            for key in ("damage_formula", "damage_bonus", "damage_type")
        }
        for part in attack["additional_damage"]
    ] == [
        {
            "damage_formula": "4d10",
            "damage_bonus": 0,
            "damage_type": "fire",
        }
    ]
    item = next(
        item
        for item in sheet["inventory"]["items"]
        if item["id"] == "molten-iron"
    )
    assert "range 30/120 ft." in item["description"]
    assert "3d6 + 7 bludgeoning damage plus 4d10 fire damage" in item["description"]


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
    with pytest.raises(StatblockImportError, match="unsupported abilities"):
        apply_statblock_variant(
            parsed.sheet,
            {
                "source_ref": "module-scene:d12",
                "ability_scores": {"luck": 20},
            },
        )
    with pytest.raises(StatblockImportError, match="integer between 1 and 30"):
        apply_statblock_variant(
            parsed.sheet,
            {
                "source_ref": "module-scene:d12",
                "ability_scores": {"wisdom": 31},
            },
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
    ranged_sheet = parse_2014_statblock(
        COMMONER.replace(
            "***Club***. *Melee Weapon Attack:* +2 to hit, reach 5 ft., one target.\n"
            "*Hit:* 2 (1d4) bludgeoning damage.",
            "***Rock***. *Ranged Weapon Attack:* +2 to hit, range 60/240 ft., "
            "one target.\n*Hit:* 2 (1d4) bludgeoning damage.",
        ),
        source_key="srd-commoner-rock",
    ).sheet
    with pytest.raises(StatblockImportError, match="long range at least as large"):
        apply_statblock_variant(
            ranged_sheet,
            {
                "source_ref": "module-scene:d12",
                "action_overrides": {
                    "rock": {
                        "normal_range_ft": 120,
                        "long_range_ft": 30,
                    }
                },
            },
        )
    with pytest.raises(StatblockImportError, match="requires a melee weapon action"):
        apply_statblock_variant(
            ranged_sheet,
            {
                "source_ref": "module-scene:d12",
                "action_overrides": {"rock": {"reach_ft": 15}},
            },
        )
    with pytest.raises(StatblockImportError, match="D&D damage type"):
        apply_statblock_variant(
            parsed.sheet,
            {
                "source_ref": "module-scene:d12",
                "action_overrides": {
                    "club": {
                        "additional_damage": [
                            {
                                "damage_formula": "4d10",
                                "damage_type": "lava",
                            }
                        ]
                    }
                },
            },
        )


def test_statblock_variant_applies_canonical_damage_defenses() -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-commoner")

    sheet = apply_statblock_variant(
        parsed.sheet,
        {
            "source_ref": "module-chunk:ice-troll",
            "damage_resistances": [" Fire "],
            "damage_immunities": ["COLD"],
            "damage_vulnerabilities": ["radiant"],
        },
    )

    assert sheet["traits"]["resistances"] == ["fire"]
    assert sheet["traits"]["immunities"] == ["cold"]
    assert sheet["traits"]["vulnerabilities"] == ["radiant"]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("damage_resistances", "fire", "must be a list"),
        (
            "damage_immunities",
            ["cold", "COLD"],
            "unique non-empty damage types",
        ),
        (
            "damage_vulnerabilities",
            ["frost"],
            "unsupported D&D 5e damage types",
        ),
    ],
)
def test_statblock_variant_rejects_invalid_damage_defenses(
    field: str,
    value: object,
    match: str,
) -> None:
    parsed = parse_2014_statblock(COMMONER, source_key="srd-commoner")

    with pytest.raises(StatblockImportError, match=match):
        apply_statblock_variant(
            parsed.sheet,
            {"source_ref": "module-chunk:variant", field: value},
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


def test_layout_ocr_recovers_one_statblock_without_image_reasoning() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 92,
        "width": 1000,
        "height": 1500,
        "blocks": [
            block("YOUNG BLUE DRAGON", 590, 110, 880, 145),
            block("Large dragon, lawful evil", 590, 145, 790, 170),
            block("Armor Class 18 (natural armor)", 590, 185, 830, 210),
            block("Hit Points 152 (16d10 + 64)", 590, 210, 810, 235),
            block("Speed 40 ft., burrow 20 ft., fly 80 ft.", 590, 235, 860, 260),
            block("ADULT BLUE DRAGON", 80, 180, 390, 215),
            block("Huge dragon, lawful evil", 80, 215, 270, 240),
            block("Armor Class 19 (natural armor)", 80, 255, 320, 280),
            block("Hit Points 225 (18d12 + 108)", 80, 280, 310, 305),
            block("Speed 40 ft., burrow 30 ft., fly 80 ft.", 80, 305, 360, 330),
            *[
                block(label, 90 + index * 75, 345, 135 + index * 75, 370)
                for index, label in enumerate(("STR", "DEX", "CON", "INT", "WIS", "CHA"))
            ],
            *[
                block(value, 85 + index * 75, 370, 145 + index * 75, 395)
                for index, value in enumerate(
                    (
                        "25 (+7)",
                        "10 (+0)",
                        "23 (+6)",
                        "16 (+3)",
                        "15 (+2)",
                        "19 (+4)",
                    )
                )
            ],
            block("Saving Throws Dex +5, Con +11, Wis +7, Cha +9", 80, 410, 440, 435),
            block("Skills Perception +12, Stealth +5", 80, 435, 330, 460),
            block("Damage Immunities lightning", 80, 460, 310, 485),
            block(
                "Senses blindsight 60 ft., darkvision 120 ft., passive Perception 22",
                80,
                485,
                500,
                510,
            ),
            block("Languages Common, Draconic", 80, 510, 320, 535),
            block("Challenge 16 (15,000 XP)", 80, 535, 300, 560),
            block(
                "Legendary Resistance (3/Day). If the dragon fails a saving throw,",
                80,
                575,
                510,
                600,
            ),
            block("it can choose to succeed instead.", 80, 600, 330, 625),
            block("ACTIONS", 80, 645, 190, 675),
            block(
                "Bite. Melee Weapon Attack: +12 to hit, reach 10 ft., one target.",
                80,
                690,
                520,
                715,
            ),
            block(
                "Hit: 18 (2d10 + 7) piercing damage plus 5 (1d10) lightning damage.",
                80,
                715,
                540,
                740,
            ),
            block(
                "Breath. The target takes 12 (2d10) damage, or half as much darmage on",
                80,
                745,
                540,
                770,
            ),
            block("a successful save.", 80, 770, 260, 795),
            block("ADULT BLUE DRAGONS.", 80, 805, 280, 830),
            # Real Monster Manual OCR can overlap the next statblock heading and
            # identity boxes by a few pixels. The peer still bounds this block.
            block("BLUE DRAGON WYRMLING", 80, 840, 390, 875),
            block("Medium dragon, lawful evil", 80, 870, 300, 895),
            *[
                block(label, 90 + index * 75, 910, 135 + index * 75, 935)
                for index, label in enumerate(("STR", "DEX", "CON", "INT", "WIS", "CHA"))
            ],
            *[
                block(value, 85 + index * 75, 935, 145 + index * 75, 960)
                for index, value in enumerate(
                    (
                        "15 (+2)",
                        "10 (+0)",
                        "13 (+1)",
                        "10 (+0)",
                        "11 (+0)",
                        "13 (+1)",
                    )
                )
            ],
            block("ADULT BLUE DRAGON", 590, 840, 900, 875),
            block("BLUE DRAGON WYRMLING", 590, 900, 900, 935),
            block("Medium dragon, lawful evil", 590, 935, 810, 960),
            block("291", 80, 1420, 120, 1450),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(
        layout,
        name="Adult Blue Dragon",
    )

    assert recovered["validation"]["name"] == "Adult Blue Dragon"
    assert recovered["validation"]["challenge_rating"] == "16"
    assert recovered["validation"]["experience_points"] == 15_000
    assert recovered["evidence"]["text_only"] is True
    assert recovered["evidence"]["matching_heading_count"] == 2
    assert recovered["evidence"]["structural_heading_count"] == 1
    assert recovered["evidence"]["excluded_page_furniture_count"] == 0
    assert recovered["evidence"]["excluded_trailing_subject_heading_count"] == 1
    assert recovered["critical_facts"] == {
        "identity": "Huge dragon, lawful evil",
        "armor_class": "19 (natural armor)",
        "hit_points": "225 (18d12 + 108)",
        "speed": "40 ft., burrow 30 ft., fly 80 ft.",
        "abilities": {
            "str": "25 (+7)",
            "dex": "10 (+0)",
            "con": "23 (+6)",
            "int": "16 (+3)",
            "wis": "15 (+2)",
            "cha": "19 (+4)",
        },
        "fields": {
            "Saving Throws": "Dex +5, Con +11, Wis +7, Cha +9",
            "Skills": "Perception +12, Stealth +5",
            "Damage Immunities": "lightning",
            "Senses": (
                "blindsight 60 ft., darkvision 120 ft., passive Perception 22"
            ),
            "Languages": "Common, Draconic",
        },
        "challenge": "16 (15,000 XP)",
    }
    assert "**Armor Class** 19 (natural armor)" in recovered["normalized_content"]
    assert "| 25 (+7) | 10 (+0) | 23 (+6)" in recovered["normalized_content"]
    assert "half as much damage on" in recovered["normalized_content"]
    assert "darmage" not in recovered["normalized_content"]
    assert "ADULT BLUE DRAGONS" not in recovered["normalized_content"]
    assert "\n\n291" not in recovered["normalized_content"]

    constitution = next(
        item for item in layout["blocks"] if item["text"] == "23 (+6)"
    )
    constitution["text"] = "233 (+6)"
    with pytest.raises(
        StatblockImportError,
        match=r"failed D&D sheet validation: .*constitution\.score must be at most 30",
    ):
        recover_2014_statblock_from_ocr(layout, name="Adult Blue Dragon")
    constitution["text"] = "23 (+6)"

    next(
        item for item in layout["blocks"] if item["text"].startswith("Challenge ")
    )["confidence"] = 0.5
    with pytest.raises(
        StatblockImportError,
        match="low-confidence identity or core combat fields",
    ):
        recover_2014_statblock_from_ocr(layout, name="Adult Blue Dragon")


def test_layout_ocr_separates_asymmetric_same_name_lore_column() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 198,
        "width": 1200,
        "height": 1600,
        "blocks": [
            block("SIRE OF INSANITY", 120, 100, 380, 130),
            block("Huge fiend (demon), chaotic evil", 120, 135, 420, 160),
            block("Armor Class 17 (natural armor)", 120, 180, 390, 205),
            block("Hit Points 157 (15d12 + 60)", 120, 210, 380, 235),
            block("Speed 40 ft.", 120, 240, 260, 265),
            *[
                block(label, 135 + index * 80, 290, 180 + index * 80, 315)
                for index, label in enumerate(("STR", "DEX", "CON", "INT", "WIS", "CHA"))
            ],
            *[
                block(value, 130 + index * 80, 320, 190 + index * 80, 345)
                for index, value in enumerate(
                    ("23 (+6)", "6 (-2)", "19 (+4)", "14 (+2)", "19 (+4)", "22 (+6)")
                )
            ],
            block("Senses truesight 120 ft., passive Perception 14", 120, 370, 500, 395),
            block("Languages Abyssal, Common, telepathy 120 ft.", 120, 400, 500, 425),
            block("Challenge 12 (8,400 XP)", 120, 430, 350, 455),
            block(
                "Aura of Mind Erosion. A creature has disadvantage on Wisdom and",
                120,
                500,
                570,
                525,
            ),
            block("Charisma saves.", 120, 530, 280, 555),
            block("ACTIONS", 120, 1200, 240, 1230),
            block(
                "Bite. Melee Weapon Attack: +10 to hit, reach 5 ft., one target.",
                120,
                1260,
                570,
                1285,
            ),
            block("Hit: 25 (3d12 + 6) piercing damage.", 120, 1290, 440, 1315),
            block(
                "Claws. Melee Weapon Attack: +10 to hit, reach 10 ft., one target.",
                120,
                1340,
                580,
                1365,
            ),
            block("Hit: 10 (1d8 + 6) slashing damage.", 120, 1370, 440, 1395),
            block("SIRE OF INSANITY", 640, 1080, 900, 1110),
            block("Rakdos nightclubs are the favored", 640, 1140, 1060, 1165),
            block("haunts of the demons known as sires of insanity.", 640, 1210, 1100, 1235),
            block("This is lore, not part of the creature card.", 640, 1280, 1050, 1305),
            block("The sire works its evil from the shadows.", 640, 1350, 1040, 1375),
            block("Its victims remember only terror.", 640, 1420, 980, 1445),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Sire of Insanity")

    assert recovered["validation"]["challenge_rating"] == "12"
    assert recovered["evidence"]["matching_heading_count"] == 2
    assert recovered["evidence"]["structural_heading_count"] == 1
    assert recovered["evidence"]["column_split"] is not None
    assert "Wisdom and Charisma saves" in recovered["normalized_content"]
    assert "This is lore" not in recovered["normalized_content"]

def test_layout_recovery_accepts_unaligned_cards_and_grouped_ability_cells() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 170,
        "width": 1000,
        "height": 1400,
        "blocks": [
            block("TINY SERVANT", 80, 100, 280, 125),
            block("Tiny construct", 80, 126, 220, 145),
            block("Armor Class 15 (natural armor)", 80, 160, 330, 180),
            block("Hit Points", 80, 181, 170, 201),
            block("10 (4d4)", 80, 199, 170, 219),
            block("Speed 30 ft., climb 30 ft.", 80, 220, 310, 240),
            block("STR", 90, 260, 130, 280),
            block("DEX CON", 160, 260, 270, 280),
            block("INT WIS CHA", 310, 260, 470, 280),
            block("4 (-3)", 90, 285, 140, 305),
            block("16 (+3) 10 (+0)", 160, 285, 285, 305),
            block("2 (-4) 10 (+0) 1 (-5)", 310, 285, 480, 305),
            block("Damage Immunities poison, psychic", 80, 325, 350, 345),
            block("Senses blindsight 60 ft., passive Perception 10", 80, 350, 430, 370),
            block("Languages -", 80, 375, 180, 395),
            block("ACTIONS", 80, 420, 180, 445),
            block(
                "Slam. Melee Weapon Attack: +5 to hit, reach 5 ft., one target.",
                80,
                455,
                550,
                475,
            ),
            block("Hit: 5 (1d4 + 3) bludgeoning damage.", 80, 478, 390, 498),
        ],
    }

    assert [
        item["name"] for item in discover_2014_statblock_names_from_layout(layout)
    ] == ["TINY SERVANT"]

    recovered = recover_2014_statblock_from_ocr(layout, name="Tiny Servant")

    assert recovered["validation"]["name"] == "Tiny Servant"
    assert recovered["critical_facts"]["hit_points"] == "10 (4d4)"
    assert recovered["critical_facts"]["challenge"] is None
    assert recovered["critical_facts"]["abilities"] == {
        "str": "4 (-3)",
        "dex": "16 (+3)",
        "con": "10 (+0)",
        "int": "2 (-4)",
        "wis": "10 (+0)",
        "cha": "1 (-5)",
    }


def test_layout_ocr_repairs_only_bounded_weapon_entry_underscore() -> None:
    assert _repair_layout_ocr_text(
        "Slam_ Melee Weapon Attack: +5 to hit, reach 5 ft., one target."
    ) == "Slam. Melee Weapon Attack: +5 to hit, reach 5 ft., one target."
    assert _repair_layout_ocr_text("A line_with an underscore") == (
        "A line_with an underscore"
    )
    assert _repair_layout_ocr_text("Smallf ey") == "Small fey"
    assert _repair_layout_ocr_text("Smallest objects") == "Smallest objects"
    assert _repair_layout_ocr_text("Languagesбк") == "Languages -"
    assert _repair_layout_ocr_text("Languages бк") == "Languages -"
    assert _repair_layout_ocr_text("Me/ee Weapon") == "Melee Weapon"
    assert _repair_layout_ocr_text("open/close") == "open/close"
    assert _repair_layout_ocr_text("Hit: 2 (1d6 \u2013 1) bludgeoning damage") == (
        "Hit: 2 (1d6 - 1) bludgeoning damage"
    )
    assert _repair_layout_ocr_text("\u2013 creature can move") == "\u2013 creature can move"
    assert _repair_layout_ocr_text("8 (-1") == "8 (-1)"
    assert _repair_layout_ocr_text("8 (-1 point") == "8 (-1 point"
    assert _repair_layout_ocr_text("Hit Points 1 9 (3d8 + 6)") == (
        "Hit Points 19 (3d8 + 6)"
    )
    assert _repair_layout_ocr_text(
        "Senses truesight 1 20 ft., passive Perception 1 5"
    ) == "Senses truesight 120 ft., passive Perception 15"
    assert _repair_layout_ocr_text("Hit: 19 (3d 1 2) radiant damage") == (
        "Hit: 19 (3d12) radiant damage"
    )
    assert _repair_layout_ocr_text("Hit Points 142 (l 5d10 + 60)") == (
        "Hit Points 142 (15d10 + 60)"
    )
    assert _repair_layout_ocr_text("Melee Weapon Attack: + 5 to hit") == (
        "Melee Weapon Attack: +5 to hit"
    )
    assert _repair_layout_ocr_text("RADIANT I D O L") == "RADIANT I D O L"


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("5(3)", "5 (-3)"),
        ("17 (.+3)", "17 (+3)"),
        ("8 (1)", "8 (-1)"),
        ("8 1)", "8 (-1)"),
    ],
)
def test_layout_recovery_repairs_only_redundant_ability_modifier_ocr(
    source_value: str,
    expected: str,
) -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    scores = ["11 (+0)", "16 (+3)", "11 (+0)", "2 (-4)", "14 (+2)", source_value]
    layout = {
        "page_number": 322,
        "width": 600,
        "height": 800,
        "blocks": [
            block("DEER", 40, 60, 160, 80),
            block("Medium beast, unaligned", 40, 82, 260, 100),
            block("Armor Class 13", 40, 115, 180, 135),
            block("Hit Points 4 (1d8)", 40, 138, 210, 158),
            block("Speed 50 ft.", 40, 161, 160, 181),
            *[
                block(label, 45 + index * 80, 200, 85 + index * 80, 220)
                for index, label in enumerate(("STR", "DEX", "CON", "INT", "WIS", "CHA"))
            ],
            *[
                block(value, 40 + index * 80, 225, 100 + index * 80, 245)
                for index, value in enumerate(scores)
            ],
            block("Senses passive Perception 12", 40, 265, 280, 285),
            block("Languages -", 40, 288, 180, 308),
            block("Challenge 0 (10 XP)", 40, 311, 240, 331),
            block("ACTIONS", 40, 350, 150, 370),
            block(
                "Bite. Melee Weapon Attack: +2 to hit, reach 5 ft., one target.",
                40,
                385,
                540,
                405,
            ),
            block("Hit: 2 (1d4) piercing damage.", 40, 408, 330, 428),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Deer")

    assert recovered["critical_facts"]["abilities"]["cha"] == expected
    assert recovered["evidence"]["ability_modifier_repairs"][-1] == {
        "ability": "CHA",
        "source_text": source_value,
        "normalized_text": expected,
    }
    if source_value == "5(3)":
        layout["blocks"] = [
            item for item in layout["blocks"] if item["text"] != "INT"
        ]
        repaired_label = recover_2014_statblock_from_ocr(layout, name="Deer")
        assert repaired_label["critical_facts"]["abilities"]["int"] == "2 (-4)"
        assert repaired_label["evidence"]["ability_label_repairs"] == [
            {
                "ability": "INT",
                "basis": "canonical_six_column_ability_table",
            }
        ]


@pytest.mark.parametrize("source_value", ["8 unknown", "8 1"])
def test_layout_recovery_does_not_guess_an_ability_score(
    source_value: str,
) -> None:
    assert _ocr_ability_score_matches(source_value) is None


def test_layout_recovery_joins_spaced_digits_inside_explicit_ability_cells() -> None:
    parsed = _ocr_ability_score_matches(
        "1 7 (+3) 1 5 (+2) 14 (+2) 1 0 (+O) 1 5 (+2) 11 (+O)"
    )

    assert parsed is not None
    assert [value for value, _source in parsed[0]] == [
        "17 (+3)",
        "15 (+2)",
        "14 (+2)",
        "10 (+0)",
        "15 (+2)",
        "11 (+0)",
    ]


def test_layout_recovery_joins_split_weapon_attack_marker() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 183,
        "width": 600,
        "height": 800,
        "blocks": [
            block("DROW SCOUT", 40, 60, 200, 80),
            block("Medium humanoid (elf), chaotic evil", 40, 82, 280, 100),
            block("Armor Class 15", 40, 115, 180, 135),
            block("Hit Points 22 (5d8)", 40, 138, 210, 158),
            block("Speed 30 ft.", 40, 161, 160, 181),
            block("STR DEX CON INT WIS CHA", 40, 200, 330, 220),
            block(
                "10 (+0) 16 (+3) 10 (+0) 12 (+1) 12 (+1) 14 (+7)",
                40,
                225,
                430,
                245,
            ),
            block(
                "Senses darkvision 120 ft., passive Perception 11",
                40,
                265,
                390,
                285,
            ),
            block("Languages Elvish, Undercommon", 40, 288, 300, 308),
            block("Challenge 1 (200 XP)", 40, 311, 240, 331),
            block("ACTIONS", 40, 350, 150, 370),
            block("Shortsword. Me/ee Weapon", 40, 385, 250, 405),
            block("Attack: +5 to hit, reach 5 ft., one target.", 40, 408, 390, 428),
            block("Hit: 6 (1d6 + 3) piercing damage.", 40, 431, 340, 451),
            block(
                "Web (Scout Form Only; Recharge 5-6). Ranged Weapon Attack: "
                "+5 to hit, range 30/60 ft., one target.",
                40,
                454,
                540,
                474,
            ),
            block("Hit: The target is restrained.", 40, 477, 300, 497),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Drow Scout")

    assert "***Shortsword.*** Melee Weapon Attack:" in recovered["normalized_content"]
    assert "***Web (Scout Form Only; Recharge 5-6).***" in recovered[
        "normalized_content"
    ]
    assert recovered["critical_facts"]["abilities"]["cha"] == "14 (+2)"
    assert recovered["evidence"]["ability_modifier_repairs"] == [
        {
            "ability": "CHA",
            "source_text": "14 (+7)",
            "normalized_text": "14 (+2)",
        }
    ]
    assert recovered["validation"]["name"] == "Drow Scout"


def test_layout_column_split_uses_parallel_statblock_identities_as_fallback() -> None:
    blocks = [
        {
            "text": "Medium fiend (demon), chaotic evil",
            "x0": 50.0,
            "x1": 250.0,
            "y0": 300.0,
            "y1": 320.0,
            "cx": 150.0,
        },
        {
            "text": "Medium fiend (demon), chaotic evil",
            "x0": 620.0,
            "x1": 850.0,
            "y0": 500.0,
            "y1": 520.0,
            "cx": 735.0,
        },
    ]

    assert _ocr_column_split(blocks, width=1000.0) == 500.0


def test_layout_column_split_does_not_cut_one_six_ability_row() -> None:
    blocks = [
        {
            "text": text,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y0 + 20.0,
            "cx": (x0 + x1) / 2,
        }
        for text, x0, x1, y0 in [
            ("COMMONER", 30.0, 180.0, 20.0),
            ("Medium humanoid, any alignment", 30.0, 250.0, 45.0),
            ("Armor Class 10", 30.0, 160.0, 75.0),
            ("Hit Points 4 (1d8)", 30.0, 190.0, 95.0),
            ("Speed 30 ft.", 30.0, 150.0, 115.0),
            *[
                (label, 30.0 + index * 70, 70.0 + index * 70, 145.0)
                for index, label in enumerate(("STR", "DEX", "CON", "INT", "WIS", "CHA"))
            ],
            *[
                ("10 (+0)", 25.0 + index * 70, 80.0 + index * 70, 165.0)
                for index in range(6)
            ],
            ("Senses passive Perception 10", 30.0, 250.0, 200.0),
            ("Languages Common", 30.0, 180.0, 220.0),
            ("Challenge 0 (10 XP)", 30.0, 200.0, 240.0),
            ("ACTIONS", 30.0, 130.0, 275.0),
            (
                "Club. Melee Weapon Attack: +2 to hit, reach 5 ft., one target.",
                30.0,
                480.0,
                305.0,
            ),
            ("Hit: 2 (1d4) bludgeoning damage.", 30.0, 310.0, 325.0),
        ]
    ]

    assert _ocr_column_split(blocks, width=600.0) is None


def test_layout_column_split_detects_one_card_flowing_across_columns() -> None:
    blocks = [
        {
            "text": text,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y0 + 20.0,
            "cx": (x0 + x1) / 2,
        }
        for text, x0, x1, y0 in [
            ("Small humanoid (kobold), lawful evil", 50.0, 430.0, 100.0),
            ("Armor Class 15", 50.0, 250.0, 140.0),
            ("Hit Points 27 (5d6 + 10)", 50.0, 300.0, 170.0),
            ("Speed 30 ft.", 50.0, 210.0, 200.0),
            ("Sorcery Points. The kobold has 3 sorcery points.", 570.0, 950.0, 100.0),
            ("Pack Tactics. The kobold has advantage.", 570.0, 930.0, 180.0),
            ("ACTIONS", 570.0, 700.0, 260.0),
            ("Dagger. Melee Weapon Attack: +4 to hit.", 570.0, 950.0, 300.0),
            ("Hit: 4 (1d4 + 2) piercing damage.", 570.0, 900.0, 330.0),
        ]
    ]

    assert _ocr_column_split(blocks, width=1000.0) == 500.0


def test_layout_column_split_detects_numbered_action_continuation() -> None:
    blocks = [
        {
            "text": text,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y0 + 20.0,
            "cx": (x0 + x1) / 2,
        }
        for text, x0, x1, y0 in [
            ("Small humanoid (kobold), lawful evil", 50.0, 430.0, 100.0),
            ("Armor Class 12", 50.0, 250.0, 140.0),
            ("Hit Points 13 (3d6 + 3)", 50.0, 300.0, 170.0),
            ("Speed 30 ft.", 50.0, 210.0, 200.0),
            ("1. Acid. The kobold throws acid.", 50.0, 430.0, 300.0),
            ("2. Alchemist's Fire. The kobold throws a flask.", 50.0, 470.0, 340.0),
            ("3. Basket of Centipedes. The kobold throws it.", 50.0, 470.0, 380.0),
            ("4. Green Slime Pot. The kobold throws it.", 50.0, 450.0, 420.0),
            ("5. Rot Grub Pot. The kobold throws it.", 570.0, 950.0, 300.0),
            ("6. Scorpion on a Stick. The kobold attacks.", 570.0, 950.0, 340.0),
            ("7. Skunk in a Cage. The kobold releases it.", 570.0, 950.0, 380.0),
            ("8. Wasp Nest in a Bag. The kobold throws it.", 570.0, 950.0, 420.0),
        ]
    ]

    assert _ocr_column_split(blocks, width=1000.0) == 500.0


def test_layout_recovery_bounds_probable_peer_with_corrupt_identity() -> None:
    ordered = [
        {
            "text": text,
            "x0": 600.0,
            "x1": 900.0,
            "y0": y0,
            "y1": y0 + 20.0,
            "cx": 750.0,
        }
        for text, y0 in [
            ("BLACKGUARD", 100.0),
            ("Mmed -o ( ( ( ment", 130.0),
            ("Armor Class 18 (plate)", 180.0),
            ("Hit Points 153 (18d8 + 72)", 210.0),
            ("Speed 30 ft.", 240.0),
            ("ACTIONS", 400.0),
        ]
    ]

    assert _ocr_probable_peer_heading(ordered, 0) is True
    assert _ocr_probable_peer_heading(ordered, 1) is False


def test_layout_recovery_rejects_bottom_border_text_interleaving() -> None:
    def block(text: str, y0: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [40, y0, 560, y1],
        }

    layout = {
        "page_number": 183,
        "width": 600,
        "height": 800,
        "blocks": [
            block("DROW SCOUT", 60, 80),
            block("Medium humanoid (elf), chaotic evil", 82, 100),
            block("Armor Class 15", 115, 135),
            block("Hit Points 22 (5d8)", 138, 158),
            block("Speed 30 ft.", 161, 181),
            block("STR DEX CON INT WIS CHA", 200, 220),
            block("10 (+0) 16 (+3) 10 (+0) 12 (+1) 12 (+1) 14 (+2)", 225, 245),
            block("Senses darkvision 120 ft., passive Perception 11", 265, 285),
            block("Languages Elvish, Undercommon", 288, 308),
            block("Challenge 1 (200 XP)", 311, 331),
            block("ACTIONS", 350, 370),
            block(
                "Shortsword. Melee Weapon Attack: +5 to hit, one target. "
                "Hit: 6 (1d6 + 3) piercing damage.",
                385,
                405,
            ),
            block("=an=d:::::p=sy=c=hi=c=d=a=m=a=g=e=)", 721, 729),
        ],
    }

    with pytest.raises(StatblockImportError, match="decorative glyph interleaving"):
        recover_2014_statblock_from_ocr(layout, name="Drow Scout")


def test_layout_recovery_uses_one_structural_near_heading_and_numeric_s() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 236,
        "width": 1200,
        "height": 700,
        "blocks": [
            block("JARAD VOD SAVO", 80, 100, 320, 125),
            block("Medium undead, neutral evil", 80, 126, 330, 146),
            block("Armor Class 17 (natural armor)", 80, 160, 360, 180),
            block("Hit Points 180 (24d8 + 72)", 80, 181, 350, 201),
            block("Speed 30 ft.", 80, 202, 230, 222),
            block("STR DEX CON", 90, 250, 280, 270),
            block("INT WIS CHA", 310, 250, 480, 270),
            block("16 (+3) 12 (+1) 16 (+3)", 90, 275, 300, 295),
            block("20 (+S) 16 (+3) 15 (+2)", 310, 275, 520, 295),
            block("Damage Resistances necrotic; bludgeoning, piercing, and", 80, 315, 560, 335),
            block("slashing from nonmagical attacks", 100, 335, 400, 355),
            block("Senses darkvision 60 ft., passive Perception 13", 80, 360, 500, 380),
            block("Languages Common, Elvish", 80, 385, 340, 405),
            block("Challenge 10 (5,900 XP)", 80, 410, 340, 430),
            block(
                "Spellcasting. Jarad is a 10th-level spellcaster. His "
                "spellcasting ability is Intelligence",
                700,
                150,
                1160,
                180,
            ),
            block("ACTIONS", 700, 290, 800, 310),
            block("Multiattack. Jarad makes two attacks.", 700, 320, 1030, 340),
            block(
                "Sticky Leg. Melee Weapon Attack: +8 to hit, reach 5 ft., one",
                700,
                350,
                1160,
                370,
            ),
            block(
                "Medium or smaller creature. Hit: The target is grappled "
                "until it escapes (escape DC 12).",
                700,
                380,
                1180,
                400,
            ),
            block("LEGENDARY ACTIONS", 700, 410, 930, 430),
            block("Cantrip. Jarad casts a cantrip.", 700, 440, 1020, 460),
            block("REACTIONS", 700, 470, 900, 490),
            block(
                "Instinctive Charm (Recharges after the Enchanter Casts an",
                700,
                500,
                1120,
                520,
            ),
            block(
                "Enchantment Spell of 1st Level or Higher). Jarad diverts an attack.",
                700,
                525,
                1160,
                545,
            ),
            block(
                "The attacker must make a DC 14 Wisdom saving throw. On a "
                "failed save, it changes targets.",
                700,
                550,
                1180,
                570,
            ),
            block("236", 700, 650, 750, 675),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Jarad Von Savo")

    assert recovered["validation"]["name"] == "Jarad Von Savo"
    assert recovered["critical_facts"]["abilities"]["int"] == "20 (+5)"
    assert recovered["critical_facts"]["fields"]["Damage Resistances"] == (
        "necrotic; bludgeoning, piercing, and slashing from nonmagical attacks"
    )
    assert "## Actions" in recovered["normalized_content"]
    assert "Spellcasting.*** Jarad is a 10th-level spellcaster" in recovered[
        "normalized_content"
    ]
    assert "Multiattack" in recovered["normalized_content"]
    assert "one Medium or smaller creature. Hit:" in recovered["normalized_content"]
    assert "## Legendary Actions" in recovered["normalized_content"]
    assert "***Instinctive Charm (Recharges after the Enchanter Casts an " in recovered[
        "normalized_content"
    ]
    assert "***The attacker must make" not in recovered["normalized_content"]
    assert recovered["evidence"]["heading"] == "JARAD VOD SAVO"
    assert recovered["evidence"]["heading_match_mode"] == "bounded_structural_fuzzy"
    assert recovered["evidence"]["matching_heading_count"] == 0
    assert recovered["evidence"]["fuzzy_heading_count"] == 1
    assert recovered["evidence"]["cross_column_continuation_block_count"] == 12


def test_layout_recovery_ignores_lore_heading_above_cross_column_continuation() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 287,
        "width": 1200,
        "height": 1700,
        "blocks": [
            block("BELASHYRRA", 80, 460, 360, 490),
            block("Mediumaberration, chaotic evil", 80, 492, 420, 516),
            block("Armor Class 19 (natural armor)", 80, 540, 380, 565),
            block("Hit Points 304 (32d8 + 160)", 80, 568, 390, 593),
            block("Speed 40 ft., fly 40 ft. (hover)", 80, 596, 410, 621),
            block("STR DEX CON INT WIS CHA", 90, 650, 520, 675),
            block(
                "24 (+7) 21 (+5) 20 (+5) 25 (+7) 22 (+6) 23 (+6)",
                90,
                680,
                550,
                705,
            ),
            block("Senses truesight 120 ft., passive Perception 23", 80, 735, 520, 760),
            block("Languages Deep Speech, telepathy 120 ft.", 80, 765, 490, 790),
            block("Challenge 22 (41,000 XP)", 80, 795, 360, 820),
            block("ACTIONS", 80, 1050, 240, 1075),
            block("Multiattack. Belashyrra makes two attacks.", 80, 1080, 520, 1105),
            block("MADNESS OF BELASHYRRA", 650, 120, 1050, 150),
            block("d6 Flaw (lasts until cured)", 650, 155, 1040, 180),
            block(
                "Eye Ray. Belashyrra shoots one magical eye ray.",
                650,
                470,
                1120,
                500,
            ),
            block("LEG E N DARY ACT I O N S", 650, 1120, 980, 1145),
            block("Claw. Belashyrra makes one claw attack.", 650, 1150, 1080, 1175),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Belashyrra")

    assert "***Eye Ray.***" in recovered["normalized_content"]
    assert "*Medium aberration, chaotic evil*" in recovered["normalized_content"]
    assert "## Legendary Actions" in recovered["normalized_content"]
    assert "MADNESS OF BELASHYRRA" not in recovered["normalized_content"]
    assert recovered["evidence"]["cross_column_continuation_block_count"] == 3
    assert recovered["evidence"]["identity_spacing_repair"] == {
        "source_text": "Mediumaberration, chaotic evil",
        "normalized_text": "Medium aberration, chaotic evil",
    }


def test_layout_recovery_binds_source_name_to_one_unique_unheaded_card() -> None:
    def block(text: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, object]:
        return {
            "text": text,
            "confidence": 0.99,
            "bbox": [x0, y0, x1, y1],
        }

    layout = {
        "page_number": 309,
        "width": 1200,
        "height": 1600,
        "blocks": [
            block("RADIANT I D O L", 60, 800, 320, 840),
            block("A radiant idol was an angel banished from Syrania.", 60, 850, 500, 875),
            block("Large celestial, lawful evil", 650, 300, 1050, 325),
            block("Armor Class 18 (natural armor)", 650, 350, 1050, 375),
            block("Hit Points 142 (15d10 + 60)", 650, 380, 1050, 405),
            block("Speed 40 ft.", 650, 410, 900, 435),
            block("STR DEX CON INT WIS CHA", 660, 470, 1080, 495),
            block(
                "23 (+6) 18 (+4) 19 (+4) 17 (+3) 20 (+5) 21 (+5)",
                660,
                500,
                1100,
                525,
            ),
            block("Senses darkvision 120 ft., passive Perception 19", 650, 560, 1120, 585),
            block("Languages all, telepathy 120 ft.", 650, 590, 1030, 615),
            block("Challenge 11 (7,200 XP)", 650, 620, 980, 645),
            block("ACTIONS", 650, 680, 850, 705),
            block("Multiattack. The radiant idol makes two melee attacks.", 650, 720, 1130, 745),
            block(
                "Flail. Melee Weapon Attack: +10 to hit, reach 5 ft., one target. "
                "Hit: 10 (1d8 + 6) bludgeoning damage.",
                650,
                760,
                1160,
                800,
            ),
        ],
    }

    recovered = recover_2014_statblock_from_ocr(layout, name="Radiant Idol")

    assert recovered["critical_facts"]["identity"] == "Large celestial, lawful evil"
    assert recovered["evidence"]["heading_match_mode"] == (
        "source_name_unique_identity"
    )
    assert "***Flail.***" in recovered["normalized_content"]
