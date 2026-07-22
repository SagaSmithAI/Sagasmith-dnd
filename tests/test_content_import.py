import pytest

from sagasmith_dnd.content_import import (
    compiled_artifacts_from_candidates,
    extract_content_candidates,
    validate_selection_ready_artifacts,
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
