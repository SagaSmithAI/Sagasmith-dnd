import pytest

from sagasmith_dnd.content_import import (
    compiled_artifacts_from_candidates,
    extract_content_candidates,
    module_statblock_review_candidates,
    validate_selection_ready_artifacts,
)
from sagasmith_dnd.statblocks import parse_2014_statblock


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
    }
    assert validate_selection_ready_artifacts(artifacts) == []


def test_compiler_rejects_duplicate_generated_ids() -> None:
    candidates = [
        {
            "id": "one",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["one"],
            "review_status": "accepted",
            "artifact": {"kind": "feat", "card": {"name": "Lucky"}},
        },
        {
            "id": "two",
            "kind": "feat",
            "name": "Lucky",
            "source_chunk_ids": ["two"],
            "review_status": "accepted",
            "artifact": {"kind": "feat", "card": {"name": "Lucky"}},
        },
    ]
    with pytest.raises(ValueError, match="duplicate generated artifact id"):
        compiled_artifacts_from_candidates(candidates, pack_id="dnd5e.xgte")


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
