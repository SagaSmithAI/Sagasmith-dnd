from __future__ import annotations

import copy

import pytest

from sagasmith_dnd.content_readiness import (
    DND_SELECTION_MATERIALIZERS,
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    selection_contract_errors,
    selection_input_errors,
    selection_schema_for_artifact,
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
        "id": "example.class",
        "kind": "class",
        "card": {"name": "Inventor"},
    }
    with pytest.raises(ValueError, match="no safe character materializer"):
        build_selection_contract(artifact, status="ready")

    artifact["selection_contract"] = build_selection_contract(
        artifact, status="not_applicable"
    )
    assert selection_contract_errors(artifact) == []
