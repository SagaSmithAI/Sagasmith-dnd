from __future__ import annotations

import copy

import pytest

from sagasmith_dnd.content_readiness import (
    DND_SELECTION_MATERIALIZERS,
    background_materializer_errors,
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    selection_contract_errors,
    selection_input_errors,
    selection_schema_for_artifact,
    species_materializer_errors,
    subclass_spell_grant_errors,
)


def _artifact() -> dict:
    return {
        "id": "dnd5e.example.spell.star-flare",
        "kind": "spell",
        "card": {
            "name": "Star Flare",
            "level": 1,
            "classes": ["wizard"],
            "definition": {
                "school": "evocation",
                "casting_time": "1 action",
                "range": {"kind": "distance", "normal_ft": 60},
                "components": {
                    "verbal": True,
                    "somatic": True,
                    "material": False,
                },
                "duration": {"kind": "instantaneous", "concentration": False},
                "effect": "A source-defined flare.",
            },
            "description": "A source-defined flare.",
        },
        "source_citations": [
            {
                "source": "rule-source:example",
                "source_ref": {"chunk_key": "example/section-1/chunk-1"},
                "source_excerpt": "A source-defined flare.",
            }
        ],
    }


def _decisions() -> list[dict]:
    checks = {
        "identity": True,
        "classification": True,
        "entry_boundary": True,
        "references": True,
    }
    return [
        {
            "role": "primary",
            "reviewer": "agent:extractor-v1",
            "method": "agent",
            "checks": checks,
            "notes": "Matched the exact entry and type.",
        },
        {
            "role": "critic",
            "reviewer": "agent:critic-v1",
            "method": "agent",
            "checks": checks,
            "notes": "Independently checked boundaries and references.",
        },
    ]


def test_catalog_review_is_dual_and_content_bound() -> None:
    artifact = _artifact()
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=_decisions(),
    )

    assert catalog_review_errors(artifact) == []
    assert artifact["catalog_review"]["reviewed_content_hash"] == content_fingerprint(
        artifact
    )

    stale = copy.deepcopy(artifact)
    stale["card"]["level"] = 2
    assert any("stale" in error for error in catalog_review_errors(stale))

    same_reviewer = _decisions()
    same_reviewer[1]["reviewer"] = same_reviewer[0]["reviewer"]
    with pytest.raises(ValueError, match="independent reviewer"):
        build_catalog_review(_artifact(), decisions=same_reviewer)


def test_catalog_review_cannot_approve_a_failed_boundary_check() -> None:
    decisions = _decisions()
    decisions[0]["checks"]["entry_boundary"] = False

    with pytest.raises(ValueError, match="passing primary"):
        build_catalog_review(_artifact(), decisions=decisions)


def test_selection_readiness_is_independent_from_runtime_settlement() -> None:
    artifact = _artifact()
    artifact["semantic_resolution"] = {
        "status": "resolved",
        "mode": "agent_ruling",
        "first_use_compilation_required": False,
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
    )

    assert selection_contract_errors(artifact) == []
    assert artifact["selection_contract"]["materializer"] == (
        DND_SELECTION_MATERIALIZERS["spell"]
    )
    assert artifact["selection_contract"]["schema"] == selection_schema_for_artifact(
        artifact
    )
    assert selection_input_errors(
        artifact, {"method": "spellbook", "source_class": "wizard"}
    ) == []
    assert selection_input_errors(artifact, {"raw_payload": {}}) == [
        "dnd5e.example.spell.star-flare.selection has unsupported fields: raw_payload"
    ]


def test_selection_contract_fails_closed_for_missing_or_stale_materialization() -> None:
    artifact = _artifact()
    assert selection_contract_errors(artifact) == [
        "dnd5e.example.spell.star-flare.selection_contract is required"
    ]

    with pytest.raises(ValueError, match="blocked status requires blockers"):
        build_selection_contract(artifact, status="blocked")

    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="not_applicable",
    )
    artifact["card"]["name"] = "Changed"
    assert any("stale" in error for error in selection_contract_errors(artifact))


def test_background_materializer_requires_bounded_choice_semantics() -> None:
    card = {
        "name": "Guild Agent",
        "background_grants": {
            "languages": ["Common"],
            "tools": [],
            "equipment_item_ids": [],
            "choices": {
                "language_count": 1,
                "language_options": [],
                "allow_any_language": False,
                "tool_choice_count": 1,
                "tool_options": [],
            },
        },
    }

    assert background_materializer_errors(card) == [
        "background language choices need language_options or allow_any_language",
        "background tool_options cannot satisfy tool_choice_count",
    ]

    card["background_grants"]["choices"].update(
        {
            "language_options": ["Draconic", "Goblin"],
            "tool_options": ["Alchemist's Supplies", "Tinker's Tools"],
        }
    )
    assert background_materializer_errors(card) == []

    card["background_grants"]["spell_list_expansion"] = ["Aid", "aid"]
    assert background_materializer_errors(card) == [
        "background spell_list_expansion must be distinct"
    ]


def test_background_materializer_accepts_only_reviewed_embedded_equipment() -> None:
    card = {
        "name": "Guild Agent",
        "background_grants": {
            "languages": [],
            "tools": [],
            "equipment_item_ids": [],
            "choices": {
                "language_count": 0,
                "tool_choice_count": 0,
                "equipment_packages": {
                    "A": {
                        "items": [
                            {
                                "inventory_template": {
                                    "name": "Guild Signet",
                                    "kind": "equipment",
                                    "quantity": 1,
                                    "description": "A source-reviewed guild signet.",
                                    "mechanics": {},
                                },
                                "quantity": 1,
                            }
                        ],
                        "wallet": {"gp": 10},
                    }
                },
            },
        },
    }

    assert background_materializer_errors(card) == []

    item = card["background_grants"]["choices"]["equipment_packages"]["A"][
        "items"
    ][0]
    item["artifact_id"] = "dnd5e.content.srd2014.item.guild-signet"
    assert background_materializer_errors(card) == [
        "background equipment package A items[0] needs exactly one of artifact_id, "
        "selected_tool, or inventory_template"
    ]


def test_species_materializer_rejects_ocr_counts_and_unbounded_choices() -> None:
    card = {
        "name": "Simic Hybrid",
        "grants": {
            "size": "",
            "size_options": ["Medium"],
            "languages": ["Common"],
            "language_choice_count": 100,
            "language_options": [],
            "allow_any_language": False,
            "skill_proficiencies": [],
            "skill_choice_count": 1,
            "skill_options": [],
            "allow_any_skill": False,
            "tool_proficiencies": [],
            "tool_choice_count": 1,
            "tool_options": [],
        },
    }

    errors = species_materializer_errors(card)
    assert "species language_choice_count must be an integer from 0 to 5" in errors
    assert "species skill choices need skill_options or allow_any_skill" in errors
    assert "species tool_options cannot satisfy tool_choice_count" in errors

    card["grants"].update(
        {
            "language_choice_count": 1,
            "language_options": ["Elvish", "Vedalken"],
            "skill_options": ["Arcana", "Medicine"],
            "tool_options": ["Alchemist's Supplies", "Tinker's Tools"],
        }
    )
    assert species_materializer_errors(card) == []


def test_subclass_spell_grants_keep_known_and_prepared_semantics_distinct() -> None:
    card = {
        "name": "Circle of Spores",
        "class_name": "Druid",
        "minimum_level": 2,
        "always_prepared_spells": [
            {"name": "Blindness/Deafness", "minimum_level": 3}
        ],
        "spell_grants": [
            {"name": "Chill Touch", "minimum_level": 2, "method": "known"}
        ],
    }
    assert subclass_spell_grant_errors(card) == []

    card["spell_grants"][0]["method"] = "prepared"
    assert subclass_spell_grant_errors(card) == [
        "subclass spell_grants[0].method must be always_prepared, known, or spellbook"
    ]


@pytest.mark.parametrize(
    ("kind", "card", "selection_fields"),
    [
        ("activity", {"name": "Special Action"}, []),
        (
            "background",
            {"name": "Sage", "background_grants": {}},
            [
                "ability_score_increases",
                "custom_name",
                "equipment_item_ids",
                "equipment_package",
                "languages",
                "origin_feat_selection",
                "skills",
                "tools",
            ],
        ),
        (
            "feat",
            {
                "name": "Adept",
                "prerequisites": [],
                "repeatable": False,
                "selection_requirements": {"field": "chosen_skill"},
            },
            ["chosen_skill"],
        ),
        (
            "class",
            {
                "name": "Artificer",
                "class_definition": {
                    "hit_die": 8,
                    "saving_throw_proficiencies": ["constitution", "intelligence"],
                    "armor_proficiencies": ["light armor"],
                    "weapon_proficiencies": ["simple weapons"],
                    "tool_proficiencies": ["thieves' tools"],
                    "skill_choice_count": 2,
                    "skill_options": ["arcana", "investigation", "medicine"],
                },
            },
            ["skills"],
        ),
        (
            "feature",
            {
                "name": "Training",
                "class_name": "Fighter",
                "minimum_level": 2,
                "selection_requirements": {"field": "style"},
                "selection_requirements_by_level": {
                    "4": {"field": "ability_score_increases"}
                },
                "mechanical_grants": {},
            },
            [
                "ability_score_increases",
                "grant_level",
                "initial_setup_full_hp",
                "replace_existing",
                "study_started_elapsed_minutes",
                "study_started_elapsed_ticks",
                "style",
            ],
        ),
        (
            "item",
            {
                "name": "Moon Blade",
                "inventory_template": {
                    "name": "Moon Blade",
                    "kind": "weapon",
                    "quantity": 1,
                },
            },
            [],
        ),
        ("species", {"name": "Elf", "grants": {}}, [
            "abilities",
            "ability_scores_include_species_grants",
            "cantrip_artifact_id",
                "hit_points_include_species_grants",
                "languages",
                "size",
                "skills",
            "tools",
            "values_include_species_grants",
        ]),
        (
            "subclass",
            {"name": "Champion", "class_name": "Fighter", "minimum_level": 3},
            ["target_class_name"],
        ),
    ],
)
def test_selection_schema_is_typed_and_card_bound(
    kind: str, card: dict, selection_fields: list[str]
) -> None:
    artifact = {"id": f"example.{kind}", "kind": kind, "card": card}
    artifact["selection_contract"] = build_selection_contract(
        artifact, status="ready"
    )

    assert selection_contract_errors(artifact) == []
    assert artifact["selection_contract"]["schema"]["selection_fields"] == (
        selection_fields
    )

    stale_schema = copy.deepcopy(artifact)
    stale_schema["selection_contract"]["schema"]["selection_fields"].append("payload")
    assert any(
        "schema does not match" in error
        for error in selection_contract_errors(stale_schema)
    )


def test_unsupported_kind_cannot_claim_a_safe_materializer() -> None:
    artifact = {
        "id": "example.monster",
        "kind": "monster",
        "card": {"name": "Inventor"},
    }
    with pytest.raises(ValueError, match="no safe character materializer"):
        build_selection_contract(artifact, status="ready")

    artifact["selection_contract"] = build_selection_contract(
        artifact, status="not_applicable"
    )
    assert selection_contract_errors(artifact) == []
