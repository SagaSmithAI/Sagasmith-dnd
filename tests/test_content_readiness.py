from __future__ import annotations

import copy

import pytest

from sagasmith_dnd.content_readiness import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    selection_contract_errors,
)


def _artifact() -> dict:
    return {
        "id": "dnd5e.example.spell.star-flare",
        "kind": "spell",
        "card": {
            "name": "Star Flare",
            "level": 1,
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
        materializer="dnd5e.spell.v1",
        schema={"level": 1, "classes": ["wizard"]},
    )

    assert selection_contract_errors(artifact) == []


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
