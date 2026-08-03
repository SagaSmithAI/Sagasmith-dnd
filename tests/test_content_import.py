from copy import deepcopy

import pytest

from sagasmith_dnd.content_import import (
    _trim_trailing_statblock_lore,
    artifact_with_direct_resolution,
    audit_release_resolution_readiness,
    author_selection_card_from_candidate,
    compiled_artifacts_from_candidates,
    extract_content_candidates,
    extract_content_inventory,
    module_statblock_review_candidates,
    normalize_2014_statblock_candidate,
    validate_selection_ready_artifacts,
)
from sagasmith_dnd.statblocks import (
    StatblockImportError,
    parse_2014_statblock,
    split_2014_statblock_action_variants,
)


def test_extracts_review_required_catalog_candidates() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "chunk-fireball",
                "heading_path": ["Chapter 3", "Fireball"],
                "content": "3rd-level evocation spell\nCasting Time: 1 action",
                "page_start": 42,
                "page_end": 42,
            },
            {
                "id": "chunk-background",
                "heading_path": ["Backgrounds", "City Watch"],
                "content": "Skill Proficiencies: Athletics, Insight",
            },
        ]
    )
    assert [item["kind"] for item in candidates] == ["spell", "background"]
    assert all(item["review_status"] == "pending" for item in candidates)
    assert all(item["application_state"] == "catalog_only" for item in candidates)


def test_same_named_features_under_different_subclasses_do_not_merge() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "alchemist-tools",
                "heading_path": ["Artificer Specialists", "Alchemist", "Tools of the Trade"],
                "content": (
                    "By the time you adopt this specialty at 3rd level, your work "
                    "grants proficiency with alchemist's supplies."
                ),
            },
            {
                "id": "artillerist-tools",
                "heading_path": [
                    "Artificer Specialists",
                    "Artillerist",
                    "Tools of the Trade",
                ],
                "content": (
                    "By the time you adopt this specialty at 3rd level, your work "
                    "grants proficiency with smith's tools."
                ),
            },
        ],
        source_title="Artificer",
    )

    tools = [item for item in candidates if item["name"] == "Tools of the Trade"]

    assert len(tools) == 2
    assert {tuple(item["source_heading_path"][:-1]) for item in tools} == {
        ("Artificer Specialists", "Alchemist"),
        ("Artificer Specialists", "Artillerist"),
    }


def test_ocr_spacing_and_nested_headers_keep_entity_identity() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "geometry-table",
                "heading_path": ["SCHOOL OF GEOMETRY FEATURES"],
                "content": "Wizard Level Features 2nd Spell Map 6th Spell Link",
            },
            {
                "id": "geometry-feature",
                "heading_path": ["ArcaneTopography"],
                "content": (
                    "Beginning at2nd level, the layout of your spell map grants you "
                    "insight into the fabric of reality and a bonus to initiative."
                ),
            },
            {
                "id": "spell-intro",
                "heading_path": ["New Spell"],
                "content": (
                    "A new cantrip is presented here: mind sliver. It appears on the "
                    "sorcerer, warlock, and wizard spell lists."
                ),
            },
            {
                "id": "spell",
                "heading_path": ["New Spell", "Mind Sliver", "Enchantment cantrip"],
                "content": (
                    "Casting Time: 1 action Range: 60 feet Components: V "
                    "Duration: 1 round. The target makes a saving throw."
                ),
            },
        ]
    )

    assert {(item["kind"], item["name"]) for item in candidates} == {
        ("subclass", "SCHOOL OF GEOMETRY"),
        ("feature", "Arcane Topography"),
        ("spell", "Mind Sliver"),
    }
    mind_sliver = next(item for item in candidates if item["kind"] == "spell")
    assert mind_sliver["artifact"]["card"]["classes"] == [
        "sorcerer",
        "warlock",
        "wizard",
    ]


def test_spell_list_heading_supplies_embedded_spell_class_eligibility() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "list",
                "heading_path": ["Artificer Spell List"],
                "content": "1st Level alarm arcane weapon cure wounds detect magic",
            },
            {
                "id": "spell",
                "heading_path": ["New Spell", "arcane weapon."],
                "content": (
                    "1st-level transmutation Casting Time: 1 bonus action Range: Self "
                    "Components: V, S Duration: Concentration, up to 1 hour. The weapon "
                    "deals extra damage."
                ),
            },
        ]
    )

    spell = next(item for item in candidates if item["kind"] == "spell")
    assert spell["name"] == "arcane weapon"
    assert spell["artifact"]["card"]["classes"] == ["artificer"]


def test_nested_subclass_headers_do_not_promote_the_generic_parent() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "parent",
                "heading_path": ["Sorcerous Origin"],
                "content": "Here is a playtest option for the sorcerer.",
            },
            {
                "id": "subclass",
                "heading_path": ["Sorcerous Origin", "Aberrant Mind"],
                "content": "An alien influence has altered your mind and body.",
            },
            {
                "id": "feature",
                "heading_path": ["Sorcerous Origin", "Aberrant Mind", "Invasive Thoughts"],
                "content": (
                    "1st-level Aberrant Mind feature. At1st level, you can use a "
                    "bonus action to create a telepathic link with one creature."
                ),
            },
        ]
    )

    assert {(item["kind"], item["name"]) for item in candidates} == {
        ("subclass", "Aberrant Mind"),
        ("feature", "Invasive Thoughts"),
    }
    subclass = next(item for item in candidates if item["kind"] == "subclass")
    authored = author_selection_card_from_candidate(subclass)
    assert authored["card"]["class_name"] == "Sorcerer"
    assert authored["card"]["minimum_level"] == 1


def test_subclass_parent_siblings_keep_features_and_rule_tips_out_of_subclass_catalog() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "gateway",
                "heading_path": ["Divine Domain"],
                "content": (
                    "At 1st level, a cleric gains the Divine Domain feature. "
                    "Here is a playtest option for that feature."
                ),
            },
            {
                "id": "domain",
                "heading_path": ["Divine Domain", "Twilight Domain"],
                "content": (
                    "The Twilight Domain governs the transition from light into "
                    "darkness and offers comfort at the threshold of the unknown."
                ),
            },
            {
                "id": "feature",
                "heading_path": ["Divine Domain", "Eyes of Night"],
                "content": (
                    "1st-level Twilight Domain feature. Your eyes are blessed, "
                    "allowing you to see through the deepest gloom."
                ),
            },
            {
                "id": "tip",
                "heading_path": ["Divine Domain", "Stack"],
                "content": "Temporary hit points do not add together.",
            },
            {
                "id": "wildfire",
                "heading_path": ["Circle of Wildfire"],
                "content": (
                    "Druids of the Circle of Wildfire understand that destruction "
                    "and creation are bound together in a cycle of renewal."
                ),
            },
        ]
    )

    assert {(item["kind"], item["name"]) for item in candidates} == {
        ("subclass", "Twilight Domain"),
        ("feature", "Eyes of Night"),
        ("subclass", "Circle of Wildfire"),
    }
    eyes = next(item for item in candidates if item["name"] == "Eyes of Night")
    authored_eyes = author_selection_card_from_candidate(eyes)
    assert authored_eyes["card"]["class_name"] == "Cleric"
    assert authored_eyes["card"]["subclass_name"] == "Twilight Domain"


def test_descriptive_parent_does_not_inherit_nested_level_feature() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "intro",
                "heading_path": ["Intense Rivalries"],
                "content": "Artificers compete for prestige and recognition.",
            },
            {
                "id": "feature",
                "heading_path": ["Intense Rivalries", "Magic Item Analysis"],
                "content": (
                    "At 1st level, you learn detect magic and identify and can cast "
                    "them as rituals."
                ),
            },
        ],
        source_title="UA Artificer",
    )

    assert [item["name"] for item in candidates] == ["Magic Item Analysis"]
    authored = author_selection_card_from_candidate(candidates[0])
    assert authored["card"]["class_name"] == "Artificer"


@pytest.mark.parametrize(
    ("kind", "name", "description", "expected"),
    [
        (
            "species",
            "Tortle",
            (
                "Ability Score Increase. Your Strength score increases by 2, and your "
                "Wisdom score increases by 1. Size. Your size is Medium. Speed. Your "
                "base walking speed is 30 feet. Alignment. Most are lawful good. A "
                "few can be selfish. Natural Armor. Your shell gives you a base AC "
                "of 17. Shell Defense. You gain +4 AC while withdrawn. normal. Shell "
                "Defense. You gain +4 AC while withdrawn, are prone, have speed 0, "
                "and cannot take reactions. Survival Instinct. You gain proficiency in the "
                "Survival skill. Languages. You can speak, read, and write Aquan and Common."
            ),
            "grants",
        ),
        (
            "background",
            "House Agent",
            (
                "Skill Proficiencies: Investigation, Persuasion "
                "Languages: Two of your choice Equipment: fine clothes."
            ),
            "background_grants",
        ),
        (
            "item",
            "Orb of Shielding",
            "Wondrous Item, common (requires attunement). The orb shields its bearer.",
            "inventory_template",
        ),
        (
            "subclass",
            "School of Geometry",
            "Wizard Level Features 2nd Spell Map 6th Spell Link.",
            "class_name",
        ),
        (
            "feat",
            "Durable Adept",
            "You have practiced a durable technique.",
            "prerequisites",
        ),
        (
            "class",
            "Artificer",
            (
                "Hit Dice: 1d8per artificer level. Armor: Light armor, medium "
                "armor, shields Weapons: Simple weapons Tools: Thieves' tools, "
                "tinker's tools Saving Throws: Constitution, Intelligence "
                "Skills: Choose twofrom Arcana, History, Investigation, Medicine, "
                "Nature, Perception, and Sleight of Hand. You start with equipment."
            ),
            "class_definition",
        ),
    ],
)
def test_primary_review_authors_safe_typed_selection_cards(
    kind: str,
    name: str,
    description: str,
    expected: str,
) -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": f"candidate:{kind}",
            "kind": kind,
            "name": name,
            "source_heading_path": [name],
            "artifact": {
                "kind": kind,
                "application_state": "catalog_only",
                "card": {"name": name, "description": description},
            },
        }
    )

    assert artifact["application_state"] == "selection_ready"
    assert expected in artifact["card"]
    if kind == "species":
        grants = artifact["card"]["grants"]
        assert grants["ability_score_increases"] == {"strength": 2, "wisdom": 1}
        assert grants["walk_speed"] == 30
        assert grants["languages"] == ["Aquan", "Common"]
        assert grants["skill_proficiencies"] == ["survival"]
        assert [item["name"] for item in grants["features"]] == [
            "Natural Armor",
            "Shell Defense",
            "Survival Instinct",
        ]
        shell = next(
            item for item in grants["features"] if item["name"] == "Shell Defense"
        )
        assert "cannot take reactions" in shell["description"]
    elif kind == "background":
        assert artifact["card"]["skill_proficiencies"] == [
            "investigation",
            "persuasion",
        ]
        assert artifact["card"]["background_grants"]["choices"][
            "language_count"
        ] == 2
    elif kind == "item":
        assert artifact["card"]["inventory_template"]["attunement"] == "required"
    elif kind == "subclass":
        assert artifact["card"]["class_name"] == "Wizard"
        assert artifact["card"]["minimum_level"] == 2
    elif kind == "class":
        definition = artifact["card"]["class_definition"]
        assert definition["hit_die"] == 8
        assert definition["saving_throw_proficiencies"] == [
            "constitution",
            "intelligence",
        ]
        assert definition["skill_choice_count"] == 2
        assert definition["skill_options"] == [
            "arcana",
            "history",
            "investigation",
            "medicine",
            "nature",
            "perception",
            "sleight_of_hand",
        ]


def test_source_coverage_fragment_is_runtime_context_not_character_feature() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:source-fragment",
            "kind": "feature",
            "name": "Source fragment: Random Encounters (p. 5)",
            "artifact": {
                "kind": "feature",
                "application_state": "catalog_only",
                "card": {
                    "name": "Source fragment: Random Encounters (p. 5)",
                    "description": "Roll three encounter checks per day.",
                    "source_fragment": True,
                },
            },
        }
    )

    assert artifact["application_state"] == "catalog_only"
    assert artifact["selection_applicability"] == "not_applicable"


def test_contextual_feature_is_not_promoted_without_character_binding() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:remarkable-heroes",
            "kind": "feature",
            "name": "Remarkable Heroes",
            "source_heading_path": ["Pulp Adventure", "Remarkable Heroes"],
            "artifact": {
                "kind": "feature",
                "application_state": "catalog_only",
                "card": {
                    "name": "Remarkable Heroes",
                    "description": "Player characters are remarkable heroes.",
                },
            },
        }
    )

    assert artifact["application_state"] == "catalog_only"
    assert artifact["selection_applicability"] == "not_applicable"


def test_reviewed_generic_feature_requires_explicit_character_applicability() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:campaign-gift",
            "kind": "feature",
            "name": "Campaign Gift",
            "artifact": {
                "kind": "feature",
                "application_state": "catalog_only",
                "selection_applicability": "character",
                "card": {
                    "name": "Campaign Gift",
                    "description": "The character gains this reviewed feature.",
                },
            },
        }
    )

    assert artifact["application_state"] == "selection_ready"
    assert artifact["selection_applicability"] == "character"


def test_explicit_not_applicable_feature_cannot_be_repromoted() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:rule-tip",
            "kind": "feature",
            "name": "Keeping Track of Proficiency",
            "artifact": {
                "kind": "feature",
                "application_state": "selection_ready",
                "selection_applicability": "not_applicable",
                "card": {
                    "name": "Keeping Track of Proficiency",
                    "class_name": "Revised Ranger",
                },
            },
        }
    )

    assert artifact["application_state"] == "catalog_only"
    assert artifact["selection_applicability"] == "not_applicable"


def test_agent_added_subclass_keeps_its_reviewed_parent_class() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:agent:gunsmith",
            "kind": "subclass",
            "name": "Gunsmith",
            "source_heading_path": ["Gunsmith"],
            "artifact": {
                "kind": "subclass",
                "application_state": "catalog_only",
                "card": {
                    "name": "Gunsmith",
                    "description": "A master engineer who forges a magical firearm.",
                    "class_name": "Artificer",
                    "minimum_level": 1,
                    "always_prepared_spells": [],
                },
            },
        }
    )

    assert artifact["application_state"] == "selection_ready"
    assert artifact["card"]["class_name"] == "Artificer"
    assert artifact["card"]["minimum_level"] == 1


def test_parameterized_statblock_persists_its_lobby_template_contract() -> None:
    artifact = author_selection_card_from_candidate(
        {
            "id": "candidate:homunculus",
            "kind": "statblock",
            "name": "Alchemical Homunculus",
            "source_chunk_ids": ["core"],
            "artifact": {
                "kind": "statblock",
                "application_state": "catalog_only",
                "card": {"name": "Alchemical Homunculus"},
            },
        },
        source_chunks_by_id={
            "core": (
                "Tiny construct, neutral Armor Class 13 (natural armor) "
                "Hit Points equal to five times your level in this class + your "
                "Intelligence modifier Speed 20 ft., fly 30 ft. "
                "STR DEX CON INT WIS CHA"
            )
        },
    )

    requirement = artifact["card"]["dependent_actor_template"]
    assert requirement["kind"] == "dependent_actor_template"
    assert requirement["parameters"] == [
        "owner_class_level",
        "owner_intelligence_modifier",
    ]
    assert requirement["runtime_ready"] is True
    assert artifact["card"]["normalized_content"].startswith(
        "# Alchemical Homunculus"
    )
    assert artifact["selection_applicability"] == "not_applicable"
    assert artifact["application_state"] == "catalog_only"


def test_inventory_splits_flattened_spell_descriptions_and_class_lists() -> None:
    inventory = extract_content_inventory(
        [
            {
                "id": "spell-list",
                "section_ordinal": 0,
                "ordinal": 0,
                "heading_path": ["Spells", "Spell Lists"],
                "content": (
                    "Wizard Spells 1st Level Absorb Elements (abjuration) "
                    "Catapult (transmutation) 2nd Level "
                    "Skywrite (transmutation, ritual)"
                ),
                "page_start": 1,
                "page_end": 1,
            },
            {
                "id": "descriptions",
                "section_ordinal": 1,
                "ordinal": 0,
                "heading_path": ["Spells", "Spell Descriptions"],
                "content": (
                    "Absorb Elements 1st-level abjuration Casting Time: 1 reaction "
                    "Range: Self Components: S Duration: 1 round You resist energy. "
                    "Catapult 1st-level transmutation Casting Time: 1 action "
                    "Range: 150 feet Components: S Duration: Instantaneous "
                    "You launch an object. "
                    "Skywrite 2nd-level transmutation (ritual) Casting Time: 1 action "
                    "Range: Sight Components: V, S Duration: Concentration, up to 1 hour "
                    "You form words in the sky."
                ),
                "page_start": 2,
                "page_end": 2,
            },
        ],
        source_title="Example Spells",
    )

    spells = [item for item in inventory["candidates"] if item["kind"] == "spell"]
    assert [item["name"] for item in spells] == [
        "Absorb Elements",
        "Catapult",
        "Skywrite",
    ]
    assert all(item["artifact"]["card"]["classes"] == ["wizard"] for item in spells)
    assert spells[1]["artifact"]["card"]["definition"]["range"]["normal_ft"] == 150
    assert spells[2]["artifact"]["card"]["definition"]["duration"] == {
        "kind": "timed",
        "value": 1,
        "unit": "hour",
        "concentration": True,
    }
    assert inventory["unresolved_mechanical_count"] == 0


def test_inventory_reattaches_spell_section_heading_to_leading_definition() -> None:
    inventory = extract_content_inventory(
        [
            {
                "id": "spell-list",
                "section_ordinal": 0,
                "ordinal": 0,
                "heading_path": ["Spells", "Spell Lists"],
                "content": "Druid Spells Cantrips (0 Level) Create Bonfire (conjuration)",
                "page_start": 1,
                "page_end": 1,
            },
            {
                "id": "create-bonfire",
                "section_ordinal": 1,
                "ordinal": 0,
                "heading_path": ["Spell Descriptions", "Create Bonfire"],
                "content": (
                    "Conjuration cantrip Casting Time: 1 action Range: 60 feet "
                    "Components: V, S Duration: Concentration, up to 1 minute "
                    "A creature in the bonfire must make a Dexterity saving throw."
                ),
                "page_start": 2,
                "page_end": 2,
            },
        ],
        source_title="Elemental Spells",
    )

    create_bonfire = next(
        item
        for item in inventory["candidates"]
        if item["kind"] == "spell" and item["name"] == "Create Bonfire"
    )
    card = create_bonfire["artifact"]["card"]
    assert card["level"] == 0
    assert card["classes"] == ["druid"]
    assert card["definition"]["school"] == "conjuration"
    assert card["definition"]["range"]["normal_ft"] == 60


def test_inventory_finds_ordered_rulebook_statblock_and_flags_unclaimed_mechanics() -> None:
    chunks = [
        {
            "id": "core",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": ["Bestiary", "Clockwork Guard"],
            "content": (
                "Medium construct, lawful neutral Armor Class 15 Hit Points 22 (4d8 + 4) "
                "Speed 30 ft."
            ),
            "page_start": 3,
            "page_end": 3,
        },
        *[
            {
                "id": ability.casefold(),
                "section_ordinal": index + 1,
                "ordinal": 0,
                "heading_path": ["Bestiary", "Clockwork Guard", ability],
                "content": value,
                "page_start": 3,
                "page_end": 3,
            }
            for index, (ability, value) in enumerate(
                zip(
                    ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
                    ("14 (+2)", "12 (+1)", "12 (+1)", "6 (-2)", "10 (+0)", "5 (-3)"),
                    strict=True,
                )
            )
        ],
        {
            "id": "unclaimed",
            "section_ordinal": 20,
            "ordinal": 0,
            "heading_path": ["Appendix", "Damaged Spell"],
            "content": "Casting Time: 1 action Components: V, S",
            "page_start": 4,
            "page_end": 4,
        },
    ]
    inventory = extract_content_inventory(chunks, source_title="Example Bestiary")

    statblocks = [
        item for item in inventory["candidates"] if item["kind"] == "statblock"
    ]
    assert len(statblocks) == 1
    assert statblocks[0]["name"] == "Clockwork Guard"
    assert statblocks[0]["execution_state"] == "review_ready"
    assert inventory["unresolved_mechanical_count"] == 1
    assert inventory["unresolved_mechanical_chunks"][0]["chunk_id"] == "unclaimed"
    fallback = next(
        item for item in inventory["candidates"] if item.get("coverage_fallback") is True
    )
    assert fallback["source_chunk_ids"] == ["unclaimed"]
    assert fallback["execution_state"] == "agent_resolution_required"
    assert inventory["unresolved_mechanical_chunks"][0]["candidate_ids"] == [
        fallback["id"]
    ]


def test_inventory_does_not_misclassify_dense_random_effect_tables_as_prose() -> None:
    inventory = extract_content_inventory(
        [
            {
                "id": "random-effects",
                "heading_path": ["Random Magical Effects"],
                "content": (
                    "0023 The caster turns blue. "
                    "0024 The spell target becomes invisible. "
                    "0025 The target changes shape at dawn."
                ),
                "page_start": 5,
                "page_end": 5,
            },
            {
                "id": "ordinary-prose",
                "heading_path": ["Introduction"],
                "content": "This book contains optional material for a fantasy campaign.",
                "page_start": 1,
                "page_end": 1,
            },
            {
                "id": "duration-table",
                "heading_path": ["Sample durations"],
                "content": (
                    "57 The target remains awake for 4d6 days. "
                    "58 The target retrieves a coin from the sea. "
                    "59 The target rolls 1d20 at dawn."
                ),
                "page_start": 6,
                "page_end": 6,
            },
            {
                "id": "adjudication-procedure",
                "heading_path": ["Adjudication"],
                "content": (
                    "The GM may allow the player to roll an Intelligence check. "
                    "The character loses one hit point per round until it succeeds."
                ),
                "page_start": 7,
                "page_end": 7,
            },
            {
                "id": "conditional-guidance",
                "heading_path": ["Adjudication"],
                "content": (
                    "If the price of the spell effect is negated, then its benefit "
                    "should also be negated by the GM."
                ),
                "page_start": 8,
                "page_end": 8,
            },
        ],
        source_title="Random Effects",
    )

    assert inventory["unresolved_mechanical_count"] == 4
    unresolved = {
        item["chunk_id"]: item
        for item in inventory["unresolved_mechanical_chunks"]
    }
    assert "random effect table" in unresolved["random-effects"]["signals"]
    assert "random effect table" in unresolved["duration-table"]["signals"]
    assert "rule procedure" in unresolved["adjudication-procedure"]["signals"]
    assert "adjudication guidance" in unresolved["conditional-guidance"]["signals"]
    fallback_ids = {
        item["source_chunk_ids"][0]
        for item in inventory["candidates"]
        if item.get("coverage_fallback") is True
    }
    assert fallback_ids == {
        "random-effects",
        "duration-table",
        "adjudication-procedure",
        "conditional-guidance",
    }
    assert all(
        item["execution_state"] == "agent_resolution_required"
        for item in inventory["candidates"]
        if item.get("coverage_fallback") is True
    )
    ordinary = next(
        item for item in inventory["ledger"] if item["chunk_id"] == "ordinary-prose"
    )
    assert ordinary["disposition"] == "descriptive_context"


def test_rulebook_statblock_ignores_ocr_chapter_footer_inside_heading_path() -> None:
    chunks = [
        {
            "id": "core",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": [
                "Bestiary",
                "Dolgaunt",
                "DOLGAUNT",
                "C HAPTER 6 I FRIENDS AND FOES",
            ],
            "content": (
                "Medium aberration, lawful evil Armor Class 16 "
                "Hit Points 33 (6d8 + 6) Speed 40 ft."
            ),
            "page_start": 291,
            "page_end": 291,
        },
        *[
            {
                "id": ability.casefold(),
                "section_ordinal": index + 1,
                "ordinal": 0,
                "heading_path": ["Bestiary", "Dolgaunt", ability],
                "content": value,
                "page_start": 291,
                "page_end": 291,
            }
            for index, (ability, value) in enumerate(
                zip(
                    ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
                    (
                        "14 (+2)",
                        "18 (+4)",
                        "12 (+l)",
                        "13 (+1)",
                        "14 (+2)",
                        (
                            "11 (+O) Senses blindsight 120 ft., passive Perception 14 "
                            "Languages Deep Speech Challenge 3 (700 XP)"
                        ),
                    ),
                    strict=True,
                )
            )
        ],
        {
            "id": "actions",
            "section_ordinal": 7,
            "ordinal": 0,
            "heading_path": ["Bestiary", "Dolgaunt", "ACTIONS"],
            "content": (
                "Tentacle. Melee Weapon Attack: +6 to hit, reach 15 ft., one target. "
                "Hit: 7 (1d6 + 4) bludgeoning damage."
            ),
            "page_start": 291,
            "page_end": 291,
        },
    ]

    inventory = extract_content_inventory(chunks, source_title="Example Bestiary")
    statblock = next(
        item
        for item in inventory["candidates"]
        if item["kind"] == "statblock" and item["name"] == "Dolgaunt"
    )

    assert statblock["source_heading_path"] == ["Bestiary", "Dolgaunt"]
    assert statblock["execution_state"] == "review_ready"
    assert "**Challenge** 3 (700 XP)" in statblock["normalized_content"]


def test_rulebook_statblock_recovers_name_and_sibling_abilities_from_spell_evidence() -> None:
    parent = ["Spells", "Summon Celestial"]
    chunks = [
        {
            "id": "spell",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": parent,
            "content": (
                "This corporeal form uses the Celestial Spirit stat block. "
                "Your choice determines its attack."
            ),
            "page_start": 111,
            "page_end": 111,
        },
        {
            "id": "core",
            "section_ordinal": 1,
            "ordinal": 0,
            "heading_path": [*parent, "I IO CHAPTER 3 I MAGICAL MISCELLANY"],
            "content": (
                "Large celestial Armor Class 13 Hit Points 50 Speed 30 ft., fly 40 ft."
            ),
            "page_start": 111,
            "page_end": 111,
        },
        *[
            {
                "id": ability.casefold(),
                "section_ordinal": index + 2,
                "ordinal": 0,
                "heading_path": [*parent, ability],
                "content": value,
                "page_start": 111,
                "page_end": 111,
            }
            for index, (ability, value) in enumerate(
                zip(
                    ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
                    ("16 (+3)", "14 (+2)", "16 (+3)", "10 (+0)", "14 (+2)", "16 (+3)"),
                    strict=True,
                )
            )
        ],
        {
            "id": "actions",
            "section_ordinal": 9,
            "ordinal": 0,
            "heading_path": [*parent, "ACTIONS"],
            "content": (
                "Radiant Mace. Melee Weapon Attack: +8 to hit, reach 5 ft., one target. "
                "Hit: 8 (1d10 + 3) radiant damage."
            ),
            "page_start": 111,
            "page_end": 111,
        },
    ]

    inventory = extract_content_inventory(chunks, source_title="Summoning Rules")
    statblock = next(
        item for item in inventory["candidates"] if item["kind"] == "statblock"
    )

    assert statblock["name"] == "Celestial Spirit"
    assert statblock["source_heading_path"] == parent
    assert statblock["execution_state"] == "review_ready"
    assert "| 16 (+3) | 14 (+2) | 16 (+3)" in statblock["normalized_content"]


def test_rulebook_statblock_repairs_spaced_single_ability_heading() -> None:
    chunks = [
        {
            "id": "core",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": ["Bestiary", "Fey Spirit"],
            "content": (
                "Small fey Armor Class 14 Hit Points 30 Speed 40 ft."
            ),
            "page_start": 10,
            "page_end": 10,
        },
        *[
            {
                "id": ability.replace(" ", "").casefold(),
                "section_ordinal": index + 1,
                "ordinal": 0,
                "heading_path": ["Bestiary", "Fey Spirit", ability],
                "content": value,
                "page_start": 10,
                "page_end": 10,
            }
            for index, (ability, value) in enumerate(
                zip(
                    ("STR", "DEX", "CON", "I NT", "WIS", "CHA"),
                    ("13 (+1)", "16 (+3)", "14 (+2)", "14 (+2)", "11 (+0)", "16 (+3)"),
                    strict=True,
                )
            )
        ],
    ]

    statblock = next(
        item
        for item in extract_content_inventory(chunks)["candidates"]
        if item["kind"] == "statblock"
    )

    assert statblock["execution_state"] == "review_ready"
    assert "| 13 (+1) | 16 (+3) | 14 (+2) | 14 (+2)" in (
        statblock["normalized_content"]
    )


def test_rulebook_statblock_uses_combined_sibling_ability_row_without_parent_duplicate() -> None:
    chunks = [
        {
            "id": "feature",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": ["Circle", "Summon Spirit"],
            "content": (
                "You summon your spirit. See the Wildfire Spirit stat block."
            ),
            "page_start": 3,
            "page_end": 3,
        },
        {
            "id": "core",
            "section_ordinal": 1,
            "ordinal": 0,
            "heading_path": ["Circle", "Summon Spirit", "Wildfire Spirit"],
            "content": (
                "Small elemental Armor Class 13 Hit Points 10 Speed 20 ft., fly 30 ft."
            ),
            "page_start": 3,
            "page_end": 3,
        },
        {
            "id": "abilities",
            "section_ordinal": 2,
            "ordinal": 0,
            "heading_path": ["Circle", "Summon Spirit", "STR DEX CON INT WIS CHA"],
            "content": (
                "10 (+0) 14 (+2) 14 (+2) 13 (+1) 15 (+2) 11 (+0) "
                "Damage Immunities fire Languages understands the languages you speak"
            ),
            "page_start": 3,
            "page_end": 3,
        },
    ]

    statblocks = [
        item
        for item in extract_content_inventory(chunks)["candidates"]
        if item["kind"] == "statblock"
    ]

    assert [item["name"] for item in statblocks] == ["Wildfire Spirit"]
    assert statblocks[0]["execution_state"] == "review_ready"
    assert statblocks[0]["source_chunk_ids"] == ["core", "abilities"]


def test_rulebook_statblock_uses_fragmented_sibling_ability_headings() -> None:
    parent = ["Circle", "Summon Wildfire"]
    chunks = [
        {
            "id": "feature",
            "section_ordinal": 0,
            "ordinal": 0,
            "heading_path": parent,
            "content": "See the Wildfire Spirit stat block.",
            "page_start": 3,
            "page_end": 3,
        },
        {
            "id": "core",
            "section_ordinal": 1,
            "ordinal": 0,
            "heading_path": [*parent, "Wildfire Spirit"],
            "content": (
                "Small elemental, any chaotic alignment Armor Class 13 "
                "(natural armor) Hit Points 54 Speed 20 ft., fly 30 ft. (hover)"
            ),
            "page_start": 3,
            "page_end": 3,
        },
        *[
            {
                "id": heading.casefold().replace(" ", "-"),
                "section_ordinal": index + 2,
                "ordinal": 0,
                "heading_path": [*parent, heading],
                "content": value,
                "page_start": 3,
                "page_end": 3,
            }
            for index, (heading, value) in enumerate(
                (
                    ("STR", "10 (+0)"),
                    ("DEX CON", "14 (+2) 14 (+2)"),
                    ("INT", "13 (+1)"),
                    ("WIS", "15 (+2)"),
                    (
                        "CHA",
                        "11 (+0) Saving Throws Dex +4, Con +4, Wis +4 "
                        "Damage Immunities fire Languages understands the languages you speak "
                        "Soul Bond. Its attacks use its owner's proficiency. "
                        "Actions (Requires Your Bonus Action) Flame Seed. "
                        "Ranged Weapon Attack: +4 to hit, range 30 ft., one target. "
                        "Hit: 5 (1d6 + 2) fire damage.",
                    ),
                )
            )
        ],
        {
            "id": "next-feature",
            "section_ordinal": 8,
            "ordinal": 0,
            "heading_path": ["Circle", "Enhanced Bond"],
            "content": "This narrative is not part of the stat block.",
            "page_start": 3,
            "page_end": 3,
        },
    ]

    statblock = next(
        item
        for item in extract_content_inventory(chunks)["candidates"]
        if item["kind"] == "statblock"
    )

    assert statblock["execution_state"] == "review_ready"
    assert statblock["source_chunk_ids"] == [
        "core",
        "str",
        "dex-con",
        "int",
        "wis",
        "cha",
    ]
    assert "| 10 (+0) | 14 (+2) | 14 (+2) | 13 (+1) | 15 (+2) | 11 (+0) |" in (
        statblock["normalized_content"]
    )
    assert "Enhanced Bond" not in statblock["normalized_content"]
    assert "**Languages** understands the languages you speak" in (
        statblock["normalized_content"]
    )
    assert "***Soul Bond***." in statblock["normalized_content"]
    assert "## Actions" in statblock["normalized_content"]
    assert "***Flame Seed***. Ranged Weapon Attack" in statblock["normalized_content"]


def test_named_statblock_lore_is_trimmed_only_after_a_complete_attack() -> None:
    action = (
        "Dagger. Melee or Ranged Weapon Attack: +6 to hit, reach 5 ft. or "
        "range 20/60 ft., one target. Hit: 4 (1d4 + 2) piercing damage."
    )
    content = (
        action
        + " Archmages are powerful spellcasters who study throughout their lives."
    )

    assert (
        _trim_trailing_statblock_lore(content, creature_name="Archmage")
        == action
    )
    assert _trim_trailing_statblock_lore(
        "Bite. Hit: 3 piercing damage. The target is poisoned.",
        creature_name="Archmage",
    ).endswith("The target is poisoned.")


def test_extractor_aggregates_all_chunks_from_one_structural_entry() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "spell-a",
                "heading_path": ["Spells", "Fireball"],
                "content": "3rd-level evocation\nCasting Time: 1 action\nRange: 150 feet",
                "page_start": 10,
                "page_end": 10,
            },
            {
                "id": "spell-b",
                "heading_path": ["Spells", "Fireball"],
                "content": "Components: V, S, M\nDuration: Instantaneous\nA bright streak flashes.",
                "page_start": 10,
                "page_end": 11,
            },
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["source_chunk_ids"] == ["spell-a", "spell-b"]
    assert candidates[0]["page_start"] == 10
    assert candidates[0]["page_end"] == 11
    assert "bright streak" in candidates[0]["artifact"]["card"]["description"]


def test_extractor_recovers_species_traits_split_across_same_section() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "birdfolk-a",
                "heading_path": ["Races", "Birdfolk Lore"],
                "content": (
                    "Aarakocra Traits As an aarakocra, you have these traits. "
                    "Ability Score Increase. Your Dexterity score increases by 2. "
                    "Age. Aarakocra reach maturity by age 3. "
                    "Alignment. Most aarakocra are good."
                ),
                "page_start": 4,
                "page_end": 4,
            },
            {
                "id": "birdfolk-b",
                "heading_path": ["Races", "Birdfolk Lore"],
                "content": (
                    "Size. Your size is Medium. Speed. Your base walking speed is "
                    "25 feet. Languages. You can speak Common and Auran."
                ),
                "page_start": 5,
                "page_end": 5,
            },
        ]
    )

    aarakocra = next(item for item in candidates if item["name"] == "Aarakocra")
    assert aarakocra["source_chunk_ids"] == ["birdfolk-a", "birdfolk-b"]
    assert aarakocra["page_start"] == 4
    assert aarakocra["page_end"] == 5


def test_extractor_requires_structural_signals_instead_of_loose_keywords() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "ordinary",
                "heading_path": ["Advice", "Schools and Weapons"],
                "content": (
                    "This chapter discusses a school, a weapon, armor, and a legendary feat "
                    "as ordinary examples without defining player content."
                ),
            },
            {
                "id": "monster",
                "heading_path": ["Monsters", "Goblin"],
                "content": (
                    "Armor Class 15\nHit Points 7\nSpeed 30 ft.\n"
                    "STR 8 DEX 14 CON 10 INT 10 WIS 8 CHA 8\nChallenge 1/4"
                ),
            },
        ]
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("statblock", "Goblin")
    ]


def test_module_statblock_chunks_become_review_ready_without_guessing_ocr() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "GOBLIN"]
    chunks = [
        {
            "id": "goblin-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Small humanoid (goblinoid), neutral evil Armor Class 15 "
                "(leather armor, shield) Hit Points 7 (2d6) Speed 30 ft."
            ),
            "page_start": 58,
            "page_end": 58,
        },
    ]
    values = {
        "STR": "8 (-1)",
        "DEX": "14 (+2)",
        "CON": "10 (+0)",
        "INT": "10 (+0)",
        "WIS": "8 (-1)",
        "CHA": (
            "8 (-1) Skills Stealth +6 Senses darkvision 60 ft., passive Perception 9 "
            "Languages Common, Goblin Challenge 1/4 (50 XP) Nimble Escape. "
            "The goblin can take the Disengage or Hide action as a bonus action."
        ),
    }
    chunks.extend(
        {
            "id": f"goblin-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 58,
            "page_end": 58,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "goblin-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Scimitar. Melee Weapon Attack: +4 to hit, reach 5 ft., one target. "
                "Hit: 5 (ld6 + 2) slashing damage. Shortbow. Ranged Weapon Attack: "
                "+4 to hit, range 80 ft./320 ft., one target. Hit: 5 (1d6 + 2) "
                "piercing damage. Heavy Crossbow. Ranged Weapon Attack: +2 to hit, "
                "range 100/400 ft., one target. Hit: 5 (ldl0) piercing damage."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    )

    candidates = module_statblock_review_candidates(chunks, source_title="Lost Mine")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["name"] == "GOBLIN"
    assert candidate["execution_state"] == "review_ready"
    assert candidate["source_scene_ids"] == ["monster-scene"]
    assert candidate["page_start"] == 58
    assert candidate["page_end"] == 58
    assert candidate["validation"]["challenge_rating"] == "1/4"
    assert "**Armor Class** 15 (leather armor, shield)" in candidate["normalized_content"]
    assert "***Scimitar***. Melee Weapon Attack" in candidate["normalized_content"]
    assert "Hit: 5 (1d6 + 2) slashing damage" in candidate["normalized_content"]
    assert "Hit: 5 (1d10) piercing damage" in candidate["normalized_content"]


def test_module_statblock_keeps_effect_only_hit_clause_inside_its_attack() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "GIANT SPIDER"]
    chunks = [
        {
            "id": "spider-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Large beast, unaligned Armor Class 14 (natural armor) "
                "Hit Points 26 (4d10 + 4) Speed 30 ft., climb 30 ft."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    ]
    values = {
        "STR": "14 (+2)",
        "DEX": "16 (+3)",
        "CON": "12 (+1)",
        "INT": "2 (-4)",
        "WIS": "11 (+0)",
        "CHA": (
            "4 (-3) Skills Stealth +7 Senses blindsight 10 ft., darkvision 60 ft., "
            "passive Perception 10 Languages — Challenge 1 (200 XP)"
        ),
    }
    chunks.extend(
        {
            "id": f"spider-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 58,
            "page_end": 58,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "spider-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Bite. Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. "
                "Hit: 7 (1d8 + 3) piercing damage. Web (Recharge 5-6). "
                "Ranged Weapon Attack: +5 to hit, range 30 ft./60 ft., one creature. "
                "Hit: The target is restrained by webbing. As an action, the restrained "
                "target can make a DC 12 Strength check, bursting the webbing on a "
                "success. The webbing can also be attacked and destroyed (AC 10; hp 5; "
                "vulnerable to fire damage; immune to bludgeoning, poison, and psychic "
                "damage). Usually found underground, the lair of "
                "a giant spider is often festooned with webs holding helpless victims."
            ),
            "page_start": 58,
            "page_end": 58,
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "Hit: The target is restrained by webbing." in candidate["normalized_content"]
    assert "***The target is restrained by webbing***" not in candidate["normalized_content"]
    assert "Usually found underground" not in candidate["normalized_content"]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-review:giant-spider",
    )
    web = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["id"] == "web-recharge-5-6"
    )
    assert web["mechanics"]["on_hit_effect"].endswith(
        "immune to bludgeoning, poison, and psychic damage)."
    )
    assert candidate["validation"]["warnings"] == [
        "Web (Recharge 5-6): on-hit effect requires DM settlement"
    ]


def test_text_layout_recovery_scopes_split_guard_without_images() -> None:
    base = ["Appendix B: Nonplayer Characters", "CULT FANATIC"]
    chunks = [
        {
            "id": "gladiator-reaction",
            "ordinal": 3839,
            "heading_path": [*base, "REACTIONS"],
            "content": "Parry. The gladiator adds 3 to its AC.",
        },
        {
            "id": "guard-core",
            "ordinal": 3840,
            "heading_path": [*base, "GUARD"],
            "content": (
                "Medium humanoid (any race), any alignment Armor Class 16 "
                "(chain shirt, shield) Hit Points 11 (2d8 + 2) Speed 30ft."
            ),
        },
        {
            "id": "guard-str",
            "ordinal": 3841,
            "heading_path": [*base, "STR"],
            "content": "13 (+1)",
        },
        {
            "id": "guard-dex",
            "ordinal": 3842,
            "heading_path": [*base, "DEX"],
            "content": "12 (+1) Skills Perception +2",
        },
        {
            "id": "guard-con",
            "ordinal": 3843,
            "heading_path": [*base, "CON"],
            "content": "12 (+1) Senses passive Perception 12",
        },
        {
            "id": "guard-int",
            "ordinal": 3844,
            "heading_path": [*base, "INT"],
            "content": "10 (+0)",
        },
        {
            "id": "guard-wis",
            "ordinal": 3845,
            "heading_path": [*base, "WIS"],
            "content": (
                "11 (+0) Languages any one language (usually Common) "
                "Challenge 1/8 (25 XP)"
            ),
        },
        {
            "id": "guard-actions",
            "ordinal": 3846,
            "heading_path": [*base, "ACTIONS"],
            "content": "",
        },
        {
            "id": "guard-cha",
            "ordinal": 3847,
            "heading_path": [*base, "CHA"],
            "content": (
                "10 (+0) Spear. Melee or Ranged Weapon Attack: +3 to hit, "
                "reach 5 ft. or range 20f60 ft., one target. Hit: 4 "
                "(1d6 + 1) piercing damage. Guards include members of a city "
                "watch, sentries in a citadel or fortified town."
            ),
        },
        {
            "id": "knight-core",
            "ordinal": 3848,
            "heading_path": [*base, "KNIGHT"],
            "content": (
                "Medium humanoid (any race), any alignment Armor Class 18 "
                "(plate) Hit Points 52 (8d8 + 16) Speed 30ft."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Guard", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
        rule_refs=candidate["source_chunk_ids"],
        name="Mill Ruse Guard",
    )

    assert candidate["source_chunk_ids"] == [
        "guard-core",
        "guard-str",
        "guard-dex",
        "guard-con",
        "guard-int",
        "guard-wis",
        "guard-actions",
        "guard-cha",
    ]
    assert parsed.name == "Mill Ruse Guard"
    assert parsed.challenge_rating == "1/8"
    assert parsed.experience_points == 25
    assert "range 20/60 ft." in candidate["normalized_content"]
    assert "Guards include members" not in candidate["normalized_content"]
    assert "knight" not in candidate["normalized_content"].casefold()
    spear = next(item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Spear")
    assert spear["mechanics"]["attack_bonus_override"] == 3
    assert spear["mechanics"]["thrown_normal_range_ft"] == 20
    assert spear["mechanics"]["thrown_long_range_ft"] == 60
    assert parsed.warnings == ()
    with pytest.raises(StatblockImportError, match="CULT FANATIC"):
        normalize_2014_statblock_candidate("CULT FANATIC", chunks)


def test_text_layout_recovery_does_not_turn_trait_saves_into_a_field() -> None:
    base = ["Appendix B: Nonplayer Characters", "CULTIST"]
    chunks = [
        {
            "id": "cultist-core",
            "ordinal": 1,
            "heading_path": base,
            "content": (
                "Medium humanoid (any race), any non-good alignment "
                "Armor Class 12 (leather armor) Hit Points 9 (2d8) "
                "Speed 30ft."
            ),
        },
        *[
            {
                "id": f"cultist-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "11 (+0)",
                    "DEX": "12 (+1) Skills Deception +2, Religion +2",
                    "CON": "10 (+0) Senses passive Perception 10",
                    "INT": "10 (+0)",
                    "WIS": (
                        "11 (+0) Languages any one language (usually Common) "
                        "Challenge 1/8 (25 XP)"
                    ),
                    "CHA": (
                        "10 (+0) Dark Devotion. The cultist has advantage on "
                        "saving throws against being charmed or frightened ."
                    ),
                }.items()
            )
        ],
        {
            "id": "cultist-actions",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Scimitar. Melee Weapon Attack: +3 to hit, reach 5 ft., one "
                "creature. Hit: 4 (1d6 + 1) slashing damage. Cultists swear "
                "allegiance to dark powers."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Cultist", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
    )
    dark_devotion = next(
        item
        for item in parsed.sheet["content"]["features"]
        if item["name"] == "Dark Devotion"
    )

    assert "**Saving Throws**" not in candidate["normalized_content"]
    assert dark_devotion["choices"]["source_trait"]["kind"] == (
        "save_advantage_against_conditions"
    )
    assert parsed.warnings == ()


def test_text_layout_preserves_and_splits_explicit_action_set_variants() -> None:
    base = ["Bestiary", "YUAN-TI MALISON"]
    chunks = [
        {
            "id": "malison-core",
            "ordinal": 1,
            "heading_path": base,
            "content": (
                "Medium monstrosity (shapechanger), neutral evil "
                "Armor Class 12 Hit Points 66 (12d8 + 12) Speed 30 ft."
            ),
        },
        *[
            {
                "id": f"malison-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "16 (+3)",
                    "DEX": "14 (+2)",
                    "CON": "13 (+1)",
                    "INT": "14 (+2)",
                    "WIS": (
                        "12 (+1) Senses darkvision 60 ft., passive Perception 11 "
                        "Languages Common Challenge 3 (700 XP)"
                    ),
                    "CHA": "16 (+3)",
                }.items()
            )
        ],
        {
            "id": "malison-type-1",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS FOR TYPE 1"],
            "content": (
                "Multiattack. The yuan-ti makes two bite attacks. "
                "Bite. Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
                "Hit: 5 (1d4 + 3) piercing damage."
            ),
        },
        {
            "id": "malison-type-2",
            "ordinal": 9,
            "heading_path": [*base, "ACTIONS FOR TYPE 2"],
            "content": (
                "Multiattack. The yuan-ti makes two constrict attacks. "
                "Constrict. Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
                "Hit: 10 (2d6 + 3) bludgeoning damage."
            ),
        },
        {
            "id": "pureblood-lore",
            "ordinal": 10,
            "heading_path": ["Bestiary", "YUAN-TI PUREBLOOD"],
            "content": "Purebloods form the lowest caste of yuan-ti society.",
        },
        {
            "id": "pureblood-core",
            "ordinal": 11,
            "heading_path": ["Bestiary", "YUAN-TI PUREBLOOD"],
            "content": (
                "Medium humanoid, neutral evil Armor Class 11 "
                "Hit Points 40 (9d8) Speed 30 ft."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("YUAN-TI MALISON", chunks)
    variants = split_2014_statblock_action_variants(
        candidate["normalized_content"]
    )

    assert "Purebloods form" not in candidate["normalized_content"]
    assert [item["name"] for item in variants] == [
        "YUAN-TI MALISON (Type 1)",
        "YUAN-TI MALISON (Type 2)",
    ]
    parsed = [
        parse_2014_statblock(item["normalized_content"], source_key="test")
        for item in variants
    ]
    assert [item.name for item in parsed] == [
        "YUAN-TI MALISON (Type 1)",
        "YUAN-TI MALISON (Type 2)",
    ]
    assert [
        [weapon["name"] for weapon in item.sheet["inventory"]["items"]]
        for item in parsed
    ] == [["Bite"], ["Constrict"]]


def test_text_layout_recovery_moves_complete_noble_parry_out_of_traits() -> None:
    base = ["Appendix B: Nonplayer Characters", "CULT FANATIC"]
    chunks = [
        {
            "id": "noble-core",
            "ordinal": 3866,
            "heading_path": [*base, "NOBLE"],
            "content": (
                "Medium humanoid (any race), any alignment Armor Class 15 "
                "(breastplate) Hit Points 9 (2d8) Speed 30 ft."
            ),
        },
        *[
            {
                "id": f"noble-{ability.casefold()}",
                "ordinal": 3867 + index,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                (
                    ("STR", "11 (+0)"),
                    ("DEX", "12 (+1)"),
                    ("CON", "11 (+0)"),
                    ("INT", "12 (+1)"),
                    (
                        "WIS",
                        (
                            "14 (+2) Skills Deception +5, Insight +4, Persuasion +5 "
                            "Senses passive Perception 10 Languages any two languages "
                            "Challenge 1/8 (25 XP)"
                        ),
                    ),
                    (
                        "CHA",
                        (
                            "16 (+3) Rapier. Melee Weapon Attack: +3 to hit, reach "
                            "5 ft., one target. Hit: 5 (1d8 + 1) piercing damage."
                        ),
                    ),
                )
            )
        ],
        {
            "id": "noble-actions",
            "ordinal": 3873,
            "heading_path": [*base, "ACTIONS"],
            "content": "",
        },
        {
            "id": "noble-parry",
            "ordinal": 3874,
            "heading_path": [*base, "NOBLE"],
            "content": (
                "Parry. The noble adds 2 to its AC aga inst one melee attack that "
                "would hit it. To do so, the noble mu st see the attacker and be "
                "wielding a melee weapon. Nobles wield great authority and influence "
                "as members of the upper class."
            ),
        },
    ]

    recovered = normalize_2014_statblock_candidate("Noble", chunks)
    parsed = parse_2014_statblock(
        recovered["normalized_content"],
        source_key="rulebook-ocr:noble-layout",
    )
    parry = next(
        activity
        for activity in parsed.sheet["content"]["activities"]
        if activity["name"] == "Parry"
    )

    assert "## Reactions" in recovered["normalized_content"]
    assert "great authority" not in recovered["normalized_content"]
    assert parry["choices"]["reaction_defense"] == {
        "kind": "armor_class_bonus",
        "bonus": 2,
        "attack_modes": ["melee"],
        "requires_visible_attacker": True,
        "requires_wielded_melee_weapon": True,
    }
    assert parsed.warnings == ()


def test_text_layout_recovery_ignores_ocr_noise_inside_creature_heading() -> None:
    base = ["Appendix B: Nonplayer Characters", "CUSTOMIZING NPCS"]
    chunks = [
        {
            "id": "berserker-core",
            "ordinal": 1,
            "heading_path": [*base, "BER,SERKER"],
            "content": (
                "Medium humanoid (any race), any chaotic alignment Armor Class 13 "
                "(hide armor) Hit Points 67 (9d8 + 27) Speed 30 ft."
            ),
        },
        *[
            {
                "id": f"berserker-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "16 (+3)",
                    "DEX": "12 (+1)",
                    "CON": "17 (+3)",
                    "INT": "9 (-1)",
                    "WIS": (
                        "11 (+0) Languages any one language (usually Common) "
                        "Challenge 2 (450 XP)"
                    ),
                    "CHA": (
                        "9 (-1) Reckless. At the start of its turn, the berserker "
                        "can gain advantage on all melee weapon attack rolls during "
                        "that turn."
                    ),
                }.items()
            )
        ],
        {
            "id": "berserker-actions",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Greataxe. Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
                "Hit: 9 (1d12 + 3) slashing damage. Ha iling from uncivilized "
                "lands, unpredictable berserkers seek conflict wherever they can."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Berserker", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
    )

    assert candidate["name"] == "Berserker"
    assert candidate["source_chunk_ids"][0] == "berserker-core"
    assert candidate["normalized_content"].startswith("# Berserker\n")
    assert "Ha iling from" not in candidate["normalized_content"]
    greataxe = next(
        item for item in parsed.sheet["inventory"]["items"] if item["name"] == "Greataxe"
    )
    assert greataxe["mechanics"]["on_hit_effect"] == ""
    assert not any(warning.startswith("Greataxe:") for warning in parsed.warnings)


def test_text_layout_recovery_repairs_generic_action_bracket_and_range_ocr() -> None:
    base = ["Appendix A: Miscellaneous Creatures", "GIANT SPIDER"]
    chunks = [
        {
            "id": "spider-core",
            "ordinal": 1,
            "heading_path": base,
            "content": (
                "Large beast, unaligned Armor Class 14 (natural armor) "
                "Hit Points 26 (4d10 + 4) Speed 30 ft., climb 30 ft."
            ),
        },
        *[
            {
                "id": f"spider-{ability.casefold()}",
                "ordinal": index + 2,
                "heading_path": [*base, ability],
                "content": value,
            }
            for index, (ability, value) in enumerate(
                {
                    "STR": "14 (+2)",
                    "DEX": "16 (+3) Skills Stealth +7",
                    "CON": "12 (+1)",
                    "INT": "2 (-4)",
                    "WIS": "11 (+0)",
                    "CHA": (
                        "4 (-3) Senses blindsight 10 ft., darkvision 60 ft., "
                        "passive Perception 10 Languages Challenge 1 (200 XP)"
                    ),
                }.items()
            )
        ],
        {
            "id": "spider-actions",
            "ordinal": 8,
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Bite. Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. "
                "Hit: 7 (1d8 + 3) piercing damage. "
                "Web (Recharge 5-6}. Ranged Weapon Attack: +5 to hit, "
                "range 30f60 ft., one creature. Hit: The target is restrained "
                "by webbing."
            ),
        },
    ]

    candidate = normalize_2014_statblock_candidate("Giant Spider", chunks)
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="rule-source:monster-manual",
    )

    attacks = {
        item["name"]: item["mechanics"]
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    }
    assert set(attacks) == {"Bite", "Web (Recharge 5-6)"}
    assert attacks["Web (Recharge 5-6)"]["normal_range_ft"] == 30
    assert attacks["Web (Recharge 5-6)"]["long_range_ft"] == 60
    assert any(
        warning.startswith("Web (Recharge 5-6):")
        for warning in parsed.warnings
    )


def test_module_statblock_recovers_flattened_actions_and_ranged_distance() -> None:
    base = ["Appendix D", "LANGDEDROSA CYANWRATH"]
    chunks = [
        {
            "id": "cyanwrath-core",
            "scene_id": "statblock-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (half-dragon), lawful evil Armor Class 17 (splint) "
                "Hit Points 57 (6d12 + 18) Speed 30 ft."
            ),
        }
    ]
    values = {
        "STR": "19 (+4)",
        "DEX": "13 (+1)",
        "CON": "16 (+3)",
        "INT": "10 (+0)",
        "WIS": "14 (+2)",
        "CHA": (
            "12 (+1) Saving Throws Str +6, Con +5 Skills Athletics +6, "
            "Intimidation +3, Perception +4 Damage Resistances lightning "
            "Senses blindsight 10 ft., darkvision 60 ft., passive Perception 14 "
            "Languages Common, Draconic Challenge 4 (1,100 XP) "
            "Action Surge (Recharges when Langdedrosa Finishes a Short or Long Rest). "
            "On his turn, Langdedrosa can take one additional action. "
            "Improved Critical. Langdedrosa's weapon attacks score a critical hit "
            "on a roll of 19 or 20. A ctions ____________________________________ "
            "Multiattack. Langdedrosa attacks twice, either with his greatsword or spear. "
            "G reatsword. Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
            "Hit: 11 (2d6 + 4) slashing damage. "
            "Spear. Melee or Ranged Weapon Attack: +6 to hit, reach 5 ft. or "
            "ranged 20 ft./60 ft., one target. Hit: 7 (1d6 + 4) piercing damage. "
            "Lightning Breath (Recharge 5-6). Langdedrosa breathes lightning in a "
            "30-foot line that is 5 feet wide. Each creature in the line must make "
            "a DC 13 Dexterity saving throw, taking 22 (4d10) lightning damage on "
            "a failed save, or half as much damage on a successful one."
        ),
    }
    chunks.extend(
        {
            "id": f"cyanwrath-{ability.casefold()}",
            "scene_id": "statblock-scene",
            "heading_path": [*base, ability],
            "content": content,
        }
        for ability, content in values.items()
    )

    candidate = module_statblock_review_candidates(chunks)[0]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:cyanwrath",
    )

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "## Actions" in candidate["normalized_content"]
    assert "***Greatsword***." in candidate["normalized_content"]
    assert "range 20/60 ft." in candidate["normalized_content"]
    weapons = {
        item["name"]: item
        for item in parsed.sheet["inventory"]["items"]
        if item["kind"] == "weapon"
    }
    assert set(weapons) == {"Greatsword", "Spear"}
    assert weapons["Spear"]["mechanics"]["thrown_normal_range_ft"] == 20
    assert weapons["Spear"]["mechanics"]["thrown_long_range_ft"] == 60
    multiattack = next(
        item for item in parsed.sheet["content"]["activities"] if item["name"] == "Multiattack"
    )
    options = {
        option["id"]: option["attacks"]
        for option in multiattack["choices"]["multiattack_options"]
    }
    assert options == {
        "melee": [
            {"weapon_id": "greatsword", "attack_mode": "melee", "count": 2}
        ],
        "melee-2": [{"weapon_id": "spear", "attack_mode": "melee", "count": 2}],
        "ranged": [{"weapon_id": "spear", "attack_mode": "ranged", "count": 2}],
    }
    assert all("Multiattack composition" not in warning for warning in parsed.warnings)
    action_names = {item["name"] for item in parsed.sheet["content"]["activities"]}
    feature_names = {item["name"] for item in parsed.sheet["content"]["features"]}
    assert "Lightning Breath (Recharge 5-6)" in action_names
    assert "Improved Critical" in feature_names


def test_named_statblock_recovery_repairs_merrow_layout_without_losing_rules() -> None:
    path = ["Appendix A: Monsters", "MERROW"]
    chunks = [
        {
            "id": "merrow-core",
            "scene_id": "monster-manual-page-220",
            "ordinal": 0,
            "heading_path": path,
            "content": (
                "Large monstrosity, chaotic evil Armor Class 13 (natural armor) "
                "Hit Points 45 (6d10 + 12) Speed 10 ft., swim 40 ft."
            ),
        }
    ]
    ability_values = {
        "STR": "18 (+4)",
        "DEX": "10 (+0)",
        "CON": "15 (+2)",
        "INT": "8 (- 1)",
        "WIS": "10 (+0)",
        "CHA": (
            "9 (- 1) Senses darkvision 60 ft., passive Perception 10 "
            "Languages Abyssal, Aquan Challenge 2 (450 XP) "
            "Amphibious. The merrow can breathe air and water."
        ),
    }
    chunks.extend(
        {
            "id": f"merrow-{ability.casefold()}",
            "scene_id": "monster-manual-page-220",
            "ordinal": ordinal,
            "heading_path": [*path, ability],
            "content": value,
        }
        for ordinal, (ability, value) in enumerate(
            ability_values.items(),
            start=1,
        )
    )
    chunks.append(
        {
            "id": "merrow-actions",
            "scene_id": "monster-manual-page-220",
            "ordinal": 7,
            "heading_path": [*path, "ACTIONS"],
            "content": (
                "Multiattack. The merrow makes two attacks: one with its bite "
                "and one with its claws or harpoon . "
                "Bite. Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
                "Hit: 8 (1d8 + 4) piercing damage. "
                "Claws. Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
                "Hit: 9 (2d4 + 4) slashing damage. "
                "Harpoon. Melee or Ranged Weapon Attack: +6 to hit, reach 5 ft. "
                "or range 20/60 ft., one target. Hit: 11 (2d6 + 4) piercing "
                "damage. If the target is a Huge or smaller creature, it must "
                "succeed on a Strength contest against the merrow or be pulled "
                "up to 20 feet toward the merrow."
            ),
        }
    )

    recovered = normalize_2014_statblock_candidate("Merrow", chunks)
    parsed = parse_2014_statblock(
        recovered["normalized_content"],
        source_key="monster-manual-2014:p220",
    )

    assert "| 18 (+4) | 10 (+0) | 15 (+2) | 8 (-1)" in (
        recovered["normalized_content"]
    )
    assert "harpoon." in recovered["normalized_content"].casefold()
    assert recovered["source_chunk_ids"] == [
        "merrow-core",
        "merrow-str",
        "merrow-dex",
        "merrow-con",
        "merrow-int",
        "merrow-wis",
        "merrow-cha",
        "merrow-actions",
    ]
    assert parsed.warnings == ()
    assert parsed.normalization_notes == ()
    assert any(
        item["name"] == "Multiattack"
        and item["choices"].get("multiattack_options")
        for item in parsed.sheet["content"]["activities"]
    )
    harpoon = next(
        item
        for item in parsed.sheet["inventory"]["items"]
        if item["name"] == "Harpoon"
    )
    assert harpoon["mechanics"]["on_hit_resolution"]["kind"] == "contest_pull"


def test_module_statblock_marks_named_actor_spellcasting_trait() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "NEZZNAR"]
    chunks = [
        {
            "id": "nezznar-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (elf), neutral evil Armor Class 11 "
            "Hit Points 27 (6dS) Speed 30 ft."
            ),
            "page_start": 59,
            "page_end": 59,
        }
    ]
    values = {
        "STR": "9 (-1)",
        "DEX": "13 (+1)",
        "CON": "10 (+0)",
        "INT": "16 (+3)",
        "WIS": "14 (+2)",
        "CHA": (
            "13 (+1) Saving Throws Int +5, Wis +4 Skills Arcana +5, Perception +4 "
            "Senses darkvision 120 ft., passive Perception 14 Languages Elvish, "
            "Undercommon Challenge 2 (450 XP) Special Equipment. Nezznar has a "
            "spider staff. Fey Ancestry. Nezznar has advantage on saving throws "
            "against being charmed. Innate Spellcasting. Nezznar can innately cast "
            "the following spells, requiring no material components:\n"
            "- At will: dancing lights\n- 1/day each: darkness, faerie fire (save DC 12)\n"
            "Spellcasting. Nezznar is a 4th-level spellcaster "
            "that uses Intelligence as his spellcasting ability (spell save DC 13; "
            "+5 to hit with spell attacks). Nezznar has the following spells prepared "
            "from the wizard's spell list: Cantrips (at will): mage hand, ray offrost, "
            "shocking grasp 1st Level (4 slots): mage armor, magic missile, shield "
            "- 2nd Level (3 slots): invisihility, suggestion"
        ),
    }
    chunks.extend(
        {
            "id": f"nezznar-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 59,
            "page_end": 59,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "nezznar-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Spider Staff. Melee Weapon Attack: +1 to hit, reach 5 ft., one target. "
                "Hit: 2 (1d6 - 1) bludgeoning damage plus 3 (1d6) poison damage. "
                "Drow are a subterranean race that worships Lolth, the Demon Queen of "
                "Spiders. Drow society is strictly matriarchal."
            ),
            "page_start": 59,
            "page_end": 59,
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]
    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:nezznar",
    )

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "***Spellcasting***. Nezznar is a 4th-level spellcaster" in candidate[
        "normalized_content"
    ]
    assert "***Demon Queen of Spiders***" not in candidate["normalized_content"]
    assert "Drow are a subterranean race" not in candidate["normalized_content"]
    assert "**Hit Points** 27 (6d8)" in candidate["normalized_content"]
    assert parsed.spellcasting is not None
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert [spell["name"] for spell in parsed.spellcasting["spells"]] == [
        "mage hand",
        "ray of frost",
        "shocking grasp",
        "mage armor",
        "magic missile",
        "shield",
        "invisibility",
        "suggestion",
    ]
    spider_staff = parsed.sheet["inventory"]["items"][0]
    assert spider_staff["name"] == "Spider Staff"
    assert spider_staff["mechanics"]["additional_damage"] == [
        {
            "damage_formula": "1d6",
            "damage_bonus": 0,
            "damage_type": "poison",
        }
    ]
    assert spider_staff["mechanics"]["on_hit_effect"] == ""
    assert not any(warning.startswith("Spider Staff:") for warning in parsed.warnings)


def test_module_statblock_repairs_bounded_spellcasting_ocr() -> None:
    base = ["Appendix B: Monsters", "MONSTER DESCRIPTIONS", "EVILMAGE"]
    chunks = [
        {
            "id": "evil-mage-core",
            "scene_id": "monster-scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (human), lawful evil Armor Class 12 "
                "Hit Points 22 (5d8) Speed 30 ft."
            ),
            "page_start": 57,
            "page_end": 57,
        },
    ]
    values = {
        "STR": "9 (-1)",
        "DEX": "14 (+2)",
        "CON": "11 (+0)",
        "INT": "17 (+3)",
        "WIS": "12 (+1)",
        "CHA": (
            "11 (+0) Saving Throws Int +5, Wis +3 Skills Arcana +5, History +5 "
            "Senses passive Perception 11 Languages Common, Draconic, Dwarvish, Elvish "
            "Challenge 1 (200 XP) Spellcasting. The mage is a 4th·level spellcaster "
            "that uses Intelligence as its spellcasting ability (spell save DC 13; "
            "+5 to hit with spell attacks). The mage knows the following spells from "
            "the wizard's spell list: Cantrips (at will): light, mage hand, shocking "
            "grasp l st Level (4 slots): charm person, magic missile 2nd Level "
            "(3 slots): hold person, misty step"
        ),
    }
    chunks.extend(
        {
            "id": f"evil-mage-{ability.casefold()}",
            "scene_id": "monster-scene",
            "heading_path": [*base, ability],
            "content": content,
            "page_start": 57,
            "page_end": 57,
        }
        for ability, content in values.items()
    )
    chunks.append(
        {
            "id": "evil-mage-actions",
            "scene_id": "monster-scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Quarterstaff. Melee Weapon Attack: +1 to hit, reach 5 ft., one target. "
                "Hit: 3 (1d8 - 1) bludgeoning damage."
            ),
            "page_start": 57,
            "page_end": 57,
        }
    )

    candidates = module_statblock_review_candidates(chunks, source_title="Lost Mine")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["execution_state"] == "review_ready"
    assert candidate["validation"]["warnings"] == []
    assert "4th-level spellcaster" in candidate["normalized_content"]
    assert "1st level (4 slots)" in candidate["normalized_content"]

    parsed = parse_2014_statblock(
        candidate["normalized_content"],
        source_key="module-candidate:evil-mage",
    )
    assert parsed.spellcasting is not None
    assert parsed.spellcasting["ability"] == "intelligence"
    assert parsed.spellcasting["save_dc"] == 13
    assert parsed.spellcasting["attack_bonus"] == 5
    assert parsed.spellcasting["class_lists"] == ["wizard"]
    assert parsed.spellcasting["slots"] == {"1": 4, "2": 3}
    assert [item["name"] for item in parsed.spellcasting["spells"]] == [
        "light",
        "mage hand",
        "shocking grasp",
        "charm person",
        "magic missile",
        "hold person",
        "misty step",
    ]
    assert parsed.warnings == ()


def test_module_statblock_candidate_keeps_ambiguous_ocr_blocked() -> None:
    base = ["Monsters", "HOBGOBLIN"]
    chunks = [
        {
            "id": "core",
            "scene_id": "scene",
            "heading_path": base,
            "content": (
                "Medium humanoid (goblinoid), lawful evil Armor Class lS "
                "(chain mail, shield) Hit Points 11 (2d8 + 2) Speed 30 ft."
            ),
        }
    ]
    for ability, score in zip(
        ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
        (13, 12, 12, 10, 10, 9),
        strict=True,
    ):
        suffix = (
            " Challenge 1/2 (100 XP)"
            if ability == "CHA"
            else ""
        )
        chunks.append(
            {
                "id": ability,
                "scene_id": "scene",
                "heading_path": [*base, ability],
                "content": f"{score} (+0){suffix}",
            }
        )
    chunks.append(
        {
            "id": "actions",
            "scene_id": "scene",
            "heading_path": [*base, "ACTIONS"],
            "content": (
                "Longsword. Melee Weapon Attack: +3 to hit, reach 5 ft., one target. "
                "Hit: 5 (1d8 + 1) slashing damage."
            ),
        }
    )

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "blocked"
    assert candidate["review_status"] == "manual_review_required"
    assert "Armor Class or Hit Points is invalid" in candidate["review_error"]


def test_module_statblock_candidates_keep_scene_local_ordinals_together() -> None:
    dragon_path = ["Appendix B: Monsters", "dragonclaw"]
    other_path = ["Appendix B: Monsters", "Other Guard"]
    chunks = [
        {
            "id": "a-dragon-core",
            "scene_id": "dragon-scene",
            "ordinal": 0,
            "heading_path": dragon_path,
            "content": (
                "Medium humanoid (human), neutral evil Armor Class 14 "
                "(leather armor) Hit Points 16 (3d8 + 3) Speed 30 ft."
            ),
            "page_start": 90,
            "page_end": 90,
        },
        {
            "id": "z-other-core",
            "scene_id": "other-scene",
            "ordinal": 0,
            "heading_path": other_path,
            "content": (
                "Medium humanoid (human), neutral Armor Class 12 "
                "Hit Points 11 (2d8 + 2) Speed 30 ft."
            ),
            "page_start": 12,
            "page_end": 12,
        },
    ]
    other_values = {
        "STR": "10 (+0)",
        "DEX": "10 (+0)",
        "CON": "12 (+1)",
        "INT": "10 (+0)",
        "WIS": "10 (+0)",
        "CHA": "10 (+0) Challenge 1/4 (50 XP)",
    }
    chunks.append(
        {
            "id": "dragon-abilities",
            "scene_id": "dragon-scene",
            "ordinal": 1,
            "heading_path": [*dragon_path, "STR DEX CO N INT WIS CHA"],
            "content": (
                "9 (-1) 16 (+3) 13 (+1) 11 (+0) 10 (+0) 12 (+1) "
                "Saving Throws Wis +2 Skills Deception +3, Stealth +5 "
                "Senses passive Perception 10 Languages Common, Draconic "
                "Challenge 1 (200 XP) Pack Tactics. The dragonclaw has advantage "
                "on an attack roll when an ally is within 5 feet of the target."
            ),
            "page_start": 90,
            "page_end": 90,
        }
    )
    for ordinal, ability in enumerate(
        ("STR", "DEX", "CON", "INT", "WIS", "CHA"),
        start=1,
    ):
        chunks.append(
            {
                "id": f"other-{ability.casefold()}",
                "scene_id": "other-scene",
                "ordinal": ordinal,
                "heading_path": [*other_path, ability],
                "content": other_values[ability],
                "page_start": 12,
                "page_end": 12,
            }
        )
    chunks.extend(
        (
            {
                "id": "dragon-actions",
                "scene_id": "dragon-scene",
                "ordinal": 7,
                "heading_path": [*dragon_path, "A c t i o n s"],
                "content": (
                    "Multiattack. The dragonclaw attacks twice with its scimitar. "
                    "Scimitar. M elee Weapon Attack: +5 to hit, reach 5 ft., one "
                    "target. Hit: 6 (1d6 + 3) slashing damage."
                ),
                "page_start": 90,
                "page_end": 90,
            },
            {
                "id": "other-actions",
                "scene_id": "other-scene",
                "ordinal": 7,
                "heading_path": [*other_path, "ACTIONS"],
                "content": (
                    "Club. Melee Weapon Attack: +2 to hit, reach 5 ft., one target. "
                    "Hit: 2 (1d4) bludgeoning damage."
                ),
                "page_start": 12,
                "page_end": 12,
            },
        )
    )

    candidates = module_statblock_review_candidates(chunks)
    dragon = next(item for item in candidates if item["name"] == "dragonclaw")

    assert dragon["execution_state"] == "review_ready", dragon.get("review_error")
    assert dragon["source_scene_ids"] == ["dragon-scene"]
    assert dragon["source_chunk_ids"] == [
        "a-dragon-core",
        "dragon-abilities",
        "dragon-actions",
    ]
    assert dragon["validation"]["challenge_rating"] == "1"
    assert "Other Guard" not in dragon["normalized_content"]


def test_module_statblock_recovers_attack_heading_with_spaced_ocr_connector() -> None:
    path = ["Appendix A: Monsters", "dragonfang"]
    chunks = [
        {
            "id": "core",
            "scene_id": "scene",
            "ordinal": 0,
            "heading_path": path,
            "content": (
                "Medium humanoid (human), neutral evil Armor Class 15 "
                "(studded leather) Hit Points 78 (12d8 + 24) Speed 30 ft."
            ),
        },
        {
            "id": "details",
            "scene_id": "scene",
            "ordinal": 1,
            "heading_path": [*path, "STR DEX CON INT WIS CHA"],
            "content": (
                "11 (+0) 16 (+3) 14 (+2) 12 (+1) 12 (+1) 14 (+2) "
                "Senses passive Perception 11 Languages Common, Draconic "
                "Challenge 5 (1,800 XP)"
            ),
        },
        {
            "id": "actions",
            "scene_id": "scene",
            "ordinal": 2,
            "heading_path": [*path, "A ct io ns"],
            "content": (
                "Multiattack. The dragonfang attacks twice with its shortsword. "
                "Shortsword. Melee Weapon Attack: +5 to hit, reach 5 ft., one "
                "target. Hit: 6 (1d6 + 3) piercing damage. "
                "Orb o f Dragon?s Breath (2/Day). Ranged Spell Attack: +5 to hit, "
                "range 90 ft., one target. Hit: 22 (5d8) force damage."
            ),
        },
    ]

    candidate = module_statblock_review_candidates(chunks)[0]

    assert candidate["execution_state"] == "review_ready", candidate.get("review_error")
    assert "***Orb o f Dragon?s Breath (2/Day)***." in candidate["normalized_content"]


def test_class_features_are_not_misclassified_as_feats() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class",
                "heading_path": ["Chapter 3: Classes", "Barbarian"],
                "content": "The Barbarian",
            },
            {
                "id": "class-features",
                "heading_path": ["Chapter 3: Classes", "Barbarian", "Class Features"],
                "content": "Class Features\nHit Dice: 1d12 per barbarian level",
            },
            {
                "id": "rage",
                "heading_path": [
                    "Chapter 3: Classes",
                    "Barbarian",
                    "Class Features",
                    "Rage",
                ],
                "content": "At 1st level, you fight with primal ferocity.",
            },
            {
                "id": "spell",
                "heading_path": ["Chapter 11: Spells", "Spark"],
                "content": "1st-level evocation\nCasting Time: 1 action",
            },
        ],
        source_title="D&D 5E - Player's Handbook",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Barbarian"),
        ("feature", "Rage"),
        ("spell", "Spark"),
    ]
    assert candidates[0]["source_chunk_ids"] == ["class", "class-features", "rage"]


def test_character_sheet_placeholder_is_not_a_statblock_candidate() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "character-sheet-core",
                "section_ordinal": 0,
                "ordinal": 0,
                "heading_path": ["Character Sheet", "Character Name"],
                "content": (
                    "Medium humanoid, unaligned Armor Class 10 "
                    "Hit Points 1 Speed 30 ft."
                ),
                "page_start": 1,
                "page_end": 1,
            }
        ]
    )

    assert not any(item["kind"] == "statblock" for item in candidates)


def test_source_title_recovers_a_supplement_class_heading() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class-features",
                "heading_path": ["Class Features"],
                "content": "Class Features\nHit Dice: 1d8 per artificer level",
            },
            {
                "id": "proficiencies",
                "heading_path": ["Class Features", "Proficiencies"],
                "content": "Saving Throw Proficiencies: Constitution, Intelligence",
            },
            {
                "id": "infuse-item",
                "heading_path": ["Class Features", "Infuse Item"],
                "content": "At 2nd level, you gain the ability to imbue mundane items.",
            },
        ],
        source_title="D&D 5E - UA - ArtificerV2",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Artificer"),
        ("feature", "Infuse Item"),
    ]
    assert candidates[0]["source_chunk_ids"] == [
        "class-features",
        "proficiencies",
        "infuse-item",
    ]


def test_source_title_recovers_flat_ocr_class_headings() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "class-features",
                "heading_path": ["CLASS FEATURES"],
                "content": "As a blood hunter, you gain the following class features.",
            },
            {
                "id": "hit-points",
                "heading_path": ["HIT POINTS"],
                "content": "Hit Dice: 1d10 per blood hunter level",
            },
        ],
        source_title="D&D 5E - UA - Blood Hunter Class 1.2",
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("class", "Blood Hunter")
    ]
    assert candidates[0]["source_chunk_ids"] == ["class-features", "hit-points"]


def test_parent_catalog_does_not_duplicate_a_descendant_spell() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "catalog",
                "heading_path": ["Optional Spells"],
                "content": "Optional Spells",
            },
            {
                "id": "spark",
                "heading_path": ["Optional Spells", "Spark"],
                "content": "1st-level evocation\nCasting Time: 1 action",
            },
        ]
    )

    assert [(item["kind"], item["name"]) for item in candidates] == [
        ("spell", "Spark")
    ]


def test_compiler_requires_review_and_selection_ready_structure() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "chunk-fireball",
                "heading_path": ["Spells", "Fireball"],
                "content": "3rd-level evocation spell\nCasting Time: 1 action",
            }
        ]
    )
    candidates[0]["review_status"] = "accepted"
    artifacts = compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")
    assert artifacts[0]["application_state"] == "catalog_only"
    artifacts[0]["application_state"] = "selection_ready"
    assert "spell needs a nonempty classes list" in "\n".join(
        validate_selection_ready_artifacts(artifacts)
    )
    artifacts[0]["card"] = {
        "name": "Fireball",
        "level": 3,
        "classes": ["wizard"],
        "definition": {},
        "resolution": {
            "kind": "saving_throw",
            "targeting": {
                "mode": "area",
                "max_targets": 100,
                "area": {"shape": "sphere", "radius_ft": 20},
            },
            "save": {
                "ability": "dexterity",
                "success": "half",
                "damage": {
                    "base_dice": "8d6",
                    "damage_type": "fire",
                },
            },
        },
    }
    assert validate_selection_ready_artifacts(artifacts) == []


def test_selection_ready_spell_uses_character_definition_validation() -> None:
    artifact = {
        "id": "spell:chromatic-orb",
        "kind": "spell",
        "application_state": "selection_ready",
        "card": {
            "name": "Chromatic Orb",
            "level": 1,
            "classes": ["sorcerer", "wizard"],
            "resolution": {
                "kind": "spell_attack",
                "targeting": {"mode": "creature", "max_targets": 1},
                "attack": {
                    "mode": "ranged",
                    "count": {"base": 1},
                    "damage": {
                        "base_dice": "3d8",
                        "damage_type": "acid",
                    },
                },
            },
            "definition": {
                "duration": {
                    "kind": "instantaneous",
                    "unit": "",
                }
            },
        },
    }

    errors = validate_selection_ready_artifacts([artifact])

    assert errors == ["artifacts[0].card.definition.duration.unit is invalid"]
    del artifact["card"]["definition"]["duration"]["unit"]
    assert validate_selection_ready_artifacts([artifact]) == []


def test_compiler_stably_disambiguates_same_named_generated_ids() -> None:
    candidates = [
        {
            "id": "one",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["one"],
            "source_heading_path": ["Chapter One", "Lucky"],
            "page_start": 10,
            "page_end": 10,
            "review_status": "accepted",
            "artifact": {
                "kind": "feat",
                "card": {"name": "Lucky", "description": "First source entry."},
                "source_citations": [
                    {"source_id": "runtime-source", "chunk_id": "one"}
                ],
            },
        },
        {
            "id": "two",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["two"],
            "source_heading_path": ["Chapter Two", "Lucky"],
            "page_start": 20,
            "page_end": 20,
            "review_status": "accepted",
            "artifact": {
                "kind": "feat",
                "card": {"name": "Lucky", "description": "Second source entry."},
                "source_citations": [
                    {"source_id": "runtime-source", "chunk_id": "two"}
                ],
            },
        },
    ]

    forward = compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")
    reverse = compiled_artifacts_from_candidates(
        list(reversed(candidates)), pack_id="dnd5e.xgte"
    )

    assert len({item["id"] for item in forward}) == 2
    assert all(item["id"].startswith("dnd5e.xgte.feat.lucky-") for item in forward)
    assert {item["id"] for item in forward} == {item["id"] for item in reverse}
    rebound = deepcopy(candidates)
    for index, candidate in enumerate(rebound):
        chunk_id = f"fresh-runtime-chunk-{index}"
        candidate["source_chunk_ids"] = [chunk_id]
        candidate["artifact"]["source_citations"][0] = {
            "source_id": "fresh-runtime-source",
            "chunk_id": chunk_id,
        }
    rebound_artifacts = compiled_artifacts_from_candidates(
        rebound, pack_id="dnd5e.xgte"
    )
    assert {item["id"] for item in forward} == {
        item["id"] for item in rebound_artifacts
    }


def test_compiler_rejects_duplicate_explicit_ids() -> None:
    candidates = [
        {
            "id": candidate_id,
            "kind": "feat",
            "name": name,
            "source_chunk_ids": [candidate_id],
            "review_status": "accepted",
            "artifact": {
                "id": "dnd5e.xgte.feat.shared",
                "kind": "feat",
                "card": {"name": name},
            },
        }
        for candidate_id, name in (("one", "Lucky"), ("two", "Fortunate"))
    ]

    with pytest.raises(ValueError, match="duplicate explicit artifact id"):
        compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")


def test_spell_merge_ignores_trailing_heading_punctuation() -> None:
    candidates = extract_content_candidates(
        [
            {
                "id": "heading-spell",
                "heading_path": ["Spells", "arcane weapon."],
                "content": "1st-level transmutation spell\nCasting Time: 1 bonus action",
                "page_start": 10,
                "page_end": 10,
            },
            {
                "id": "embedded-spell",
                "heading_path": ["Spells", "Arcane Weapon"],
                "content": (
                    "Arcane Weapon 1st-level transmutation "
                    "Casting Time: 1 bonus action Range: Self "
                    "Components: V, S Duration: Concentration, up to 1 hour "
                    "The weapon becomes magical."
                ),
                "page_start": 10,
                "page_end": 10,
            },
        ],
        source_title="Artificer",
    )

    arcane_weapon = [
        item
        for item in candidates
        if item["kind"] == "spell" and item["name"].casefold() == "arcane weapon"
    ]
    assert len(arcane_weapon) == 1
    assert set(arcane_weapon[0]["source_chunk_ids"]) == {
        "heading-spell",
        "embedded-spell",
    }


def test_reviewed_extension_spell_resolution_binds_to_core_executor() -> None:
    candidate = {
        "id": "candidate:healing-spell",
        "kind": "spell",
        "name": "Restoring Word",
        "source_chunk_ids": ["chunk:restoring-word"],
        "review_status": "accepted",
        "application_state": "selection_ready",
        "artifact": {
            "kind": "spell",
            "application_state": "selection_ready",
            "card": {
                "name": "Restoring Word",
                "level": 1,
                "classes": ["cleric"],
                "definition": {},
                "resolution": {
                    "kind": "healing",
                    "targeting": {"mode": "creature", "requires_sight": True},
                    "healing": {
                        "base_dice": "1d4",
                        "per_slot_dice": "1d4",
                        "slot_base_level": 1,
                        "add_spellcasting_modifier": True,
                    },
                },
            },
        },
    }

    artifacts = compiled_artifacts_from_candidates([candidate], pack_id="dnd5e.extension")

    assert validate_selection_ready_artifacts(artifacts) == []
    assert artifacts[0]["mechanic_refs"] == ["dnd5e.core.spell.structured_resolution"]
    assert artifacts[0]["card"]["mechanic_refs"] == [
        "dnd5e.core.spell.structured_resolution"
    ]


def test_custom_mechanical_artifact_persists_a_source_bound_plan_template() -> None:
    artifact_id = "dnd5e.extension.feature.prismatic-pulse"
    excerpt = (
        "Prismatic Pulse. Each chosen creature must make a Wisdom saving throw, "
        "taking 3d8 radiant damage on a failed save."
    )
    candidate = {
        "id": "candidate:prismatic-pulse",
        "kind": "feature",
        "name": "Prismatic Pulse",
        "source_chunk_ids": ["chunk:prismatic-pulse"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {"name": "Prismatic Pulse"},
            "resolution_plan": {
                "schema_version": 1,
                "id": "dnd5e.extension.plan.prismatic-pulse",
                "source_card_id": artifact_id,
                "source_card_kind": "feature",
                "trigger": "action",
                "slots": {
                    "targets": {
                        "kind": "actor_ids",
                        "owner": "agent",
                        "description": "Creatures selected inside the reviewed source area.",
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
                        "source": "rule-source:custom",
                        "source_ref": {"chunk_id": "chunk:prismatic-pulse"},
                        "source_excerpt": excerpt,
                    }
                ],
            },
        },
    }

    artifacts = compiled_artifacts_from_candidates(
        [candidate],
        pack_id="dnd5e.extension",
    )

    assert validate_selection_ready_artifacts(artifacts) == []
    assert artifacts[0]["execution_state"] == "plan_ready"
    assert artifacts[0]["resolution_plan"]["source_card_id"] == artifact_id
    assert artifacts[0]["resolution_plan"]["fingerprint"]
    assert artifacts[0]["mechanic_refs"] == [
        "dnd5e.extension.plan.prismatic-pulse"
    ]
    assert artifacts[0]["embedded_mechanic_refs"] == [
        "dnd5e.extension.plan.prismatic-pulse"
    ]


def test_static_grant_rule_clause_makes_non_plan_content_selection_ready() -> None:
    candidate = {
        "id": "candidate:sage",
        "kind": "background",
        "name": "Sage",
        "source_chunk_ids": ["chunk:sage"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "id": "dnd5e.extension.background.sage",
            "kind": "background",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {
                "name": "Sage",
                "background_grants": {"skills": ["arcana", "history"]},
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "skill-proficiencies",
                    "title": "Skill Proficiencies",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "rule-source:extension",
                            "source_ref": {"chunk_id": "chunk:sage"},
                            "source_excerpt": (
                                "Skill Proficiencies: Arcana and History."
                            ),
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": ["card.background_grants.skills"],
                    },
                }
            ],
        },
    }

    artifacts = compiled_artifacts_from_candidates(
        [candidate],
        pack_id="dnd5e.extension",
    )

    assert artifacts[0]["execution_state"] == "clause_ready"
    assert validate_selection_ready_artifacts(artifacts) == []


def test_direct_import_resolution_persists_source_bound_agent_clause() -> None:
    candidate = {
        "id": "candidate:odd-device",
        "kind": "feature",
        "name": "Odd Device",
        "source_chunk_ids": ["chunk:odd-device"],
        "review_status": "accepted",
        "mechanical_scope": "review_required",
        "application_state": "catalog_only",
        "execution_state": "agent_resolution_required",
        "artifact": {
            "kind": "feature",
            "application_state": "catalog_only",
            "mechanical_scope": "review_required",
            "card": {
                "name": "Odd Device",
                "description": (
                    "The device changes its bearer according to the exact imported "
                    "source procedure."
                ),
            },
        },
    }

    exact_source = (
        "The device changes its bearer according to the exact imported source "
        "procedure. Additional indexed context remains available to the Agent."
    )
    candidate["artifact"] = artifact_with_direct_resolution(
        candidate,
        citation_source="rule-source:example.odd-device",
        source_chunks_by_id={"chunk:odd-device": exact_source},
    )
    artifacts = compiled_artifacts_from_candidates(
        [candidate],
        pack_id="dnd5e.extension",
    )

    artifact = artifacts[0]
    assert artifact["execution_state"] == "ruling_ready"
    assert artifact["semantic_resolution"] == {
        "status": "resolved",
        "mode": "agent_ruling",
        "first_use_compilation_required": False,
        "clause_ids": [artifact["rule_clauses"][0]["id"]],
    }
    assert artifact["execution_state"] == "ruling_ready"
    clause = artifact["rule_clauses"][0]
    assert clause["settlement"]["mode"] == "agent_ruling"
    assert artifact["card"]["ruling_requirements"][0]["policy_ref"] == (
        "rule_clause.v1"
    )
    assert clause["source_citations"][0]["source_ref"] == {
        "chunk_id": "chunk:odd-device"
    }
    assert clause["source_citations"][0]["source"] == (
        "rule-source:example.odd-device"
    )
    assert clause["source_citations"][0]["source_excerpt"] == exact_source
    assert validate_selection_ready_artifacts(artifacts) == []


def test_direct_import_resolution_keeps_descriptive_content_nonmechanical() -> None:
    candidate = {
        "id": "candidate:lore",
        "kind": "feature",
        "name": "Local Lore",
        "source_chunk_ids": ["chunk:lore"],
        "mechanical_scope": "descriptive",
        "artifact": {
            "kind": "feature",
            "application_state": "catalog_only",
            "mechanical_scope": "descriptive",
            "card": {
                "name": "Local Lore",
                "description": "This is descriptive setting context without a rule effect.",
            },
        },
    }

    resolved = artifact_with_direct_resolution(candidate)

    assert resolved["rule_clauses"][0]["scope"] == "descriptive"
    assert resolved["rule_clauses"][0]["settlement"] == {"mode": "descriptive"}
    assert resolved["execution_state"] == "descriptive_ready"
    assert resolved["semantic_resolution"]["mode"] == "descriptive"


def test_release_resolution_audit_rejects_first_use_placeholders() -> None:
    unresolved = {
        "id": "dnd5e.extension.feature.lazy",
        "kind": "feature",
        "card": {"name": "Lazy", "description": "Resolve this later."},
        "application_state": "catalog_only",
    }
    candidate = {
        "id": "candidate:resolved",
        "kind": "feature",
        "name": "Resolved",
        "source_chunk_ids": ["chunk:resolved"],
        "mechanical_scope": "review_required",
        "artifact": {
            "id": "dnd5e.extension.feature.resolved",
            "kind": "feature",
            "card": {
                "name": "Resolved",
                "description": "Use the exact reviewed procedure through an Agent ruling.",
            },
            "application_state": "catalog_only",
        },
    }
    candidate["artifact"] = artifact_with_direct_resolution(candidate)
    resolved = compiled_artifacts_from_candidates(
        [{**candidate, "review_status": "accepted"}],
        pack_id="dnd5e.extension",
    )[0]

    report = audit_release_resolution_readiness([resolved, unresolved])

    assert report["complete"] is False
    assert report["resolved_count"] == 1
    assert report["modes"] == {"agent_ruling": 1}
    assert report["unresolved"] == [
        {
            "artifact_id": "dnd5e.extension.feature.lazy",
            "reason": "artifact has no build-time semantic resolution",
        }
    ]


def test_release_resolution_audit_rejects_stale_lazy_state_even_with_clause() -> None:
    candidate = {
        "id": "candidate:stale",
        "kind": "feature",
        "name": "Stale",
        "source_chunk_ids": ["chunk:stale"],
        "mechanical_scope": "review_required",
        "artifact": {
            "id": "dnd5e.extension.feature.stale",
            "kind": "feature",
            "card": {
                "name": "Stale",
                "description": "Use the exact reviewed source procedure.",
            },
            "application_state": "catalog_only",
        },
    }
    resolved = artifact_with_direct_resolution(candidate)
    resolved["execution_state"] = "agent_resolution_required"

    report = audit_release_resolution_readiness([resolved])

    assert report["complete"] is False
    assert report["modes"] == {}
    assert report["unresolved"] == [
        {
            "artifact_id": "dnd5e.extension.feature.stale",
            "reason": (
                "artifact still declares deferred semantic authoring: "
                "agent_resolution_required"
            ),
        }
    ]


def test_release_resolution_audit_requires_a_proven_mechanic_provider() -> None:
    artifact = {
        "id": "dnd5e.extension.feature.proven-mechanic",
        "kind": "feature",
        "card": {"name": "Proven Mechanic"},
        "mechanic_refs": ["dnd5e.extension.mechanic.proven"],
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
    }

    unresolved = audit_release_resolution_readiness([artifact])
    resolved = audit_release_resolution_readiness(
        [artifact],
        settled_mechanic_ids={"dnd5e.extension.mechanic.proven"},
    )

    assert unresolved["complete"] is False
    assert resolved == {
        "schema_version": 1,
        "complete": True,
        "artifact_count": 1,
        "resolved_count": 1,
        "modes": {"kernel_mechanic": 1},
        "unresolved": [],
        "first_use_compilation_required": False,
    }


def test_rule_clause_cannot_claim_a_plan_that_the_artifact_does_not_store() -> None:
    candidate = {
        "id": "candidate:missing-plan",
        "kind": "feature",
        "name": "Missing Plan",
        "source_chunk_ids": ["chunk:feature"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "id": "dnd5e.extension.feature.missing-plan",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {"name": "Missing Plan"},
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "effect",
                    "title": "Missing Effect",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "rule-source:extension",
                            "source_ref": {"chunk_id": "chunk:feature"},
                            "source_excerpt": (
                                "The feature produces a source-defined effect."
                            ),
                        }
                    ],
                    "settlement": {
                        "mode": "primitive_plan",
                        "plan_ids": ["dnd5e.extension.plan.missing"],
                    },
                }
            ],
        },
    }

    with pytest.raises(ValueError, match="references unavailable plans"):
        compiled_artifacts_from_candidates(
            [candidate],
            pack_id="dnd5e.extension",
        )


def test_custom_mechanical_artifact_cannot_escape_its_reviewed_source_chunks() -> None:
    candidate = {
        "id": "candidate:unsafe",
        "kind": "feature",
        "name": "Unsafe",
        "source_chunk_ids": ["chunk:reviewed"],
        "review_status": "accepted",
        "mechanical_scope": "mechanical",
        "application_state": "selection_ready",
        "artifact": {
            "id": "dnd5e.extension.feature.unsafe",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "card": {"name": "Unsafe"},
            "resolution_plan": {
                "schema_version": 1,
                "id": "dnd5e.extension.plan.unsafe",
                "source_card_id": "dnd5e.extension.feature.unsafe",
                "source_card_kind": "feature",
                "trigger": "action",
                "slots": {},
                "steps": [
                    {
                        "id": "damage",
                        "op": "damage.apply",
                        "args": {
                            "target_ids": ["target"],
                            "amount": 1,
                            "damage_type": "force",
                            "source": "Unsafe",
                        },
                    }
                ],
                "citations": [
                    {
                        "source": "rule-source:other",
                        "source_ref": {"chunk_id": "chunk:not-reviewed"},
                        "source_excerpt": (
                            "This unrelated text must not authorize the imported effect."
                        ),
                    }
                ],
            },
        },
    }

    with pytest.raises(ValueError, match="reviewed source chunks"):
        compiled_artifacts_from_candidates(
            [candidate],
            pack_id="dnd5e.extension",
        )


def test_module_statblock_repairs_only_bounded_identity_and_challenge_ocr() -> None:
    path = ["Chapter 6: Friends and Foes", "HYBRID POISONER"]
    chunks = [
        {
            "id": "poisoner-core",
            "scene_id": "poisoner",
            "ordinal": 0,
            "heading_path": path,
            "content": (
                "Medium humanoid (Simic hybrid). neutral good Armor Class 14 "
                "Hit Points 26 (4d8 + 8) Speed 40 ft."
            ),
            "page_start": 218,
            "page_end": 218,
        },
        {
            "id": "poisoner-details",
            "scene_id": "poisoner",
            "ordinal": 1,
            "heading_path": [*path, "STR DEX CON INT WIS CHA"],
            "content": (
                "12 (+1) 19 (+4) 14 (+2) 12 (+1) 13 (+1) 12 (+1) "
                "Saving Throws Dex +6, Con +4 Skills Athletics +3, Perception +3, "
                "Stealth +6 Damage Immunities poison Condition Immunities poisoned "
                "Senses darkvision 30 ft., passive Perception 13 "
                "Languages Common plus any one language Challenge l (200 XP) "
                "Assassinate. The hybrid has advantage during its first turn."
            ),
            "page_start": 218,
            "page_end": 218,
        },
        {
            "id": "poisoner-action",
            "scene_id": "poisoner",
            "ordinal": 2,
            "heading_path": [*path, "ACTIONS", "Toxic Touch"],
            "content": (
                "Melee Weapon Attack: +6 to hit, reach 5 ft., one target. "
                "Hit: 7 (2d6) bludgeoning damage."
            ),
            "page_start": 218,
            "page_end": 218,
        },
    ]

    candidate = module_statblock_review_candidates(chunks)[0]
    normalized = candidate["normalized_content"]

    assert "*Medium humanoid (Simic hybrid), neutral good*" in normalized
    assert "**Languages** Common plus any one language" in normalized
    assert "**Challenge** 1 (200 XP)" in normalized
    parsed = parse_2014_statblock(
        normalized,
        source_key="ravnica-regression",
        name="Hybrid Poisoner",
    )
    assert parsed.challenge_rating == "1"
