from copy import deepcopy

import pytest
from sagasmith_core.rule_packs import RulesetUnavailableError
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    selection_contract_errors,
)

from sagasmith_dnd_mcp.server import _rebind_verified_official_review


def _reviewed_class():
    artifact = {
        "id": "fixture.class",
        "kind": "class",
        "rule_refs": ["portable:source#chunk:proficiencies"],
        "card": {
            "name": "Fighter",
            "class_definition": {
                "hit_die": 10,
                "saving_throw_proficiencies": ["strength", "constitution"],
                "armor_proficiencies": ["all armor"],
                "weapon_proficiencies": ["simple weapons", "martial weapons"],
                "tool_proficiencies": [],
                "skill_choice_count": 2,
                "skill_options": ["athletics", "survival"],
            },
        },
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=artifact["rule_refs"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "primary",
                "reviewer": "fixture reviewer",
                "method": "agent",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Synthetic source contract fixture.",
            }
        ],
    )
    return artifact


def test_verified_runtime_projection_is_non_mutating_and_rebinds_local_hash():
    archive = _reviewed_class()
    local = deepcopy(archive)
    local.pop("selection_contract")
    local.pop("catalog_review")
    local["rule_refs"] = ["local:source#chunk:proficiencies"]
    before = deepcopy((local, archive))
    result = _rebind_verified_official_review(local, archive)
    assert (local, archive) == before
    assert selection_contract_errors(result) == catalog_review_errors(result) == []
    assert result["selection_contract"]["schema"] == archive["selection_contract"]["schema"]
    assert result["selection_contract"]["references"] == archive["rule_refs"]
    assert (
        result["selection_contract"]["reviewed_content_hash"]
        != archive["selection_contract"]["reviewed_content_hash"]
    )
    result["card"]["name"] = "Changed copy"
    assert local["card"]["name"] == archive["card"]["name"] == "Fighter"


@pytest.mark.parametrize(
    "tamper",
    [
        "identity",
        "kind",
        "source_card",
        "review_hash",
        "contract_hash",
        "contract_schema",
        "review_status",
        "missing_review",
    ],
)
def test_runtime_projection_rejects_invalid_source_review(tamper):
    archive = _reviewed_class()
    local = deepcopy(archive)
    if tamper == "identity":
        archive["id"] = "different"
    elif tamper == "kind":
        archive["kind"] = "feat"
    elif tamper == "source_card":
        archive["card"]["class_definition"]["hit_die"] = 12
    elif tamper == "review_hash":
        archive["catalog_review"]["reviewed_content_hash"] = "0" * 64
    elif tamper == "contract_hash":
        archive["selection_contract"]["reviewed_content_hash"] = "0" * 64
    elif tamper == "contract_schema":
        archive["selection_contract"]["schema"]["selection_fields"].append("arbitrary")
    elif tamper == "review_status":
        archive["catalog_review"]["status"] = "needs_review"
    elif tamper == "missing_review":
        archive.pop("catalog_review")
    with pytest.raises(RulesetUnavailableError):
        _rebind_verified_official_review(local, archive)


def test_no_review_is_not_fabricated_and_installed_attestations_are_not_trusted():
    local = _reviewed_class()
    local["application_state"] = "catalog_only"
    archive = deepcopy(local)
    archive.pop("catalog_review")
    archive.pop("selection_contract")
    result = _rebind_verified_official_review(local, archive)
    assert "selection_contract" not in result and "catalog_review" not in result


@pytest.mark.parametrize("application_state", [None, "selection_ready"])
def test_selection_ready_without_archive_review_fails_closed(application_state):
    local = _reviewed_class()
    if application_state is not None:
        local["application_state"] = application_state
    archive = deepcopy(local)
    archive.pop("catalog_review")
    archive.pop("selection_contract")
    with pytest.raises(RulesetUnavailableError, match="requires an archive review"):
        _rebind_verified_official_review(local, archive)


def test_blocked_review_remains_blocked():
    archive = _reviewed_class()
    archive["selection_contract"] = build_selection_contract(
        archive,
        status="blocked",
        blockers=["unresolved source choice"],
    )
    result = _rebind_verified_official_review(archive, archive)
    assert result["selection_contract"]["status"] == "blocked"
    assert result["selection_contract"]["blockers"] == ["unresolved source choice"]
