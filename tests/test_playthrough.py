from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import validate_party_state
from sagasmith_dnd.playthrough import (
    PARTY_MEMBER_SOURCES,
    PLAYTHROUGH_SOURCE_FIELDS,
    new_playthrough_manifest,
    playthrough_source_bindings,
)

SOURCE_REF = {
    "purpose": "party_size",
    "asset_path": "Campaign.pdf",
    "asset_sha256": "a" * 64,
    "page_start": 2,
    "page_end": 2,
    "heading_path": ["Introduction"],
    "content_sha256": "b" * 64,
}


def _manifest():
    return new_playthrough_manifest(
        run_id="run-1",
        campaign_line_id="campaign-1",
        module_ids=["module-1"],
        recommended_party_minimum=4,
        recommended_party_maximum=6,
        selected_party_size=6,
        source_refs=[SOURCE_REF],
    )


def test_manifest_records_every_required_resume_section() -> None:
    assert PARTY_MEMBER_SOURCES == {"pregen", "generated", "replacement"}
    assert set(SOURCE_REF) <= PLAYTHROUGH_SOURCE_FIELDS
    manifest = _manifest()
    assert manifest["party"]["selected_size"] == 6
    assert manifest["party"]["party_size_status"] == "source_confirmed"
    assert manifest["party"]["party_size_review"] == {}
    assert manifest["party"]["use_pregenerated_first"] is True
    assert manifest["current"]["scene_id"] == ""
    assert set(manifest) >= {
        "current",
        "traversal",
        "party",
        "npcs",
        "quests",
        "clues",
        "world_state",
        "snapshot_dag",
        "random_stream",
        "ending",
    }
    state = validate_party_state({"playthrough_manifest": manifest})
    assert state["playthrough_manifest"] == manifest


def test_manifest_rejects_retired_chunk_hash_name() -> None:
    invalid = {key: value for key, value in SOURCE_REF.items() if key != "content_sha256"}
    invalid["chunk_content_sha256"] = SOURCE_REF["content_sha256"]

    with pytest.raises(ValueError, match="unsupported fields"):
        new_playthrough_manifest(
            run_id="invalid-source-ref",
            campaign_line_id="campaign-1",
            module_ids=["module-1"],
            recommended_party_minimum=4,
            recommended_party_maximum=4,
            selected_party_size=4,
            source_refs=[invalid],
        )


def test_source_ref_uses_canonical_ordered_heading_paths() -> None:
    source_ref = deepcopy(SOURCE_REF)
    source_ref["heading_path"] = ["Temple", "Temple", "Crypt", "Temple"]

    manifest = new_playthrough_manifest(
        run_id="heading-path",
        campaign_line_id="campaign-1",
        module_ids=["module-1"],
        recommended_party_minimum=1,
        recommended_party_maximum=1,
        selected_party_size=1,
        source_refs=[source_ref],
    )

    assert manifest["source_refs"][0]["heading_path"] == [
        "Temple",
        "Crypt",
        "Temple",
    ]


def test_manifest_preserves_completed_party_size_dm_review_without_faking_source() -> None:
    manifest = new_playthrough_manifest(
        run_id="waterdeep-1",
        campaign_line_id="waterdeep-dragon-heist",
        module_ids=["module-1"],
        recommended_party_minimum=4,
        recommended_party_maximum=4,
        selected_party_size=4,
        source_refs=[SOURCE_REF],
        party_size_status="dm_review_completed",
        party_size_review={
            "module_party_size_status": "not_stated",
            "reviewed_pages": [6, 13, 15],
            "rules_source_sha256": "c" * 64,
            "represented_as_module_recommendation": False,
        },
    )

    assert manifest["party"]["party_size_status"] == "dm_review_completed"
    assert manifest["party"]["selected_size"] == 4
    assert manifest["party"]["party_size_review"]["represented_as_module_recommendation"] is False

    invalid = deepcopy(manifest)
    invalid["party"]["party_size_review"]["represented_as_module_recommendation"] = True
    with pytest.raises(ValueError, match="must not be represented"):
        validate_party_state({"playthrough_manifest": invalid})


def test_manifest_keeps_unresolved_party_size_dm_review_blocked() -> None:
    manifest = new_playthrough_manifest(
        run_id="review-1",
        campaign_line_id="unknown-size",
        module_ids=["module-1"],
        recommended_party_minimum=None,
        recommended_party_maximum=None,
        selected_party_size=None,
        source_refs=[SOURCE_REF],
        review_blocks=[{"kind": "recommended_party_size"}],
    )
    assert manifest["party"]["party_size_status"] == "dm_review_required"
    assert manifest["party"]["party_size_review"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }

    manifest["party"]["selected_size"] = 4
    with pytest.raises(ValueError, match="cannot select"):
        validate_party_state({"playthrough_manifest": manifest})


def test_party_size_dm_review_cannot_be_relabelled_as_external_input() -> None:
    manifest = new_playthrough_manifest(
        run_id="review-ownership",
        campaign_line_id="unknown-size",
        module_ids=["module-1"],
        recommended_party_minimum=None,
        recommended_party_maximum=None,
        selected_party_size=None,
        source_refs=[SOURCE_REF],
        party_size_review={
            "default_resolver": "external_input",
            "ruling_kind": "owner_approval",
        },
    )

    assert manifest["party"]["party_size_review"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }

    manifest["party"]["party_size_review"].update(
        default_resolver="external_input",
        ruling_kind="missing_or_conflicting_source_review",
    )
    normalized = validate_party_state({"playthrough_manifest": manifest})
    assert normalized["playthrough_manifest"]["party"]["party_size_review"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }


def test_manifest_rejects_default_four_and_cross_actor_replacement_knowledge() -> None:
    manifest = _manifest()
    manifest["party"]["selected_size"] = 4
    with pytest.raises(ValueError, match="recommended maximum"):
        validate_party_state({"playthrough_manifest": manifest})

    manifest = _manifest()
    manifest["party"]["members"] = [
        {
            "actor_id": "replacement",
            "name": "Replacement",
            "status": "active",
            "source": "replacement",
            "source_asset_path": "",
            "level": 2,
            "xp": 300,
            "hit_points": {"current": 10, "maximum": 10},
            "resources": {},
            "wallet": {"gp": 25},
            "equipment": [],
            "knowledge_scope_actor_id": "dead-predecessor",
        }
    ]
    with pytest.raises(ValueError, match="must equal actor_id"):
        validate_party_state({"playthrough_manifest": manifest})


def test_ending_conditions_require_exact_source_and_machine_checks() -> None:
    manifest = _manifest()
    manifest["ending"]["conditions"] = [
        {
            "id": "victory",
            "label": "The threat is ended",
            "source_ref": deepcopy(SOURCE_REF),
            "all_of": [
                {
                    "kind": "manifest_value",
                    "path": "quests.main.status",
                    "actor_id": "",
                    "fact_key": "",
                    "operator": "equals",
                    "value": "completed",
                }
            ],
        }
    ]
    validated = validate_party_state({"playthrough_manifest": manifest})
    assert validated["playthrough_manifest"]["ending"]["conditions"][0]["id"] == "victory"

    manifest["ending"]["conditions"][0]["all_of"] = []
    with pytest.raises(ValueError, match="at least one machine check"):
        validate_party_state({"playthrough_manifest": manifest})


def test_source_binding_catalogue_covers_every_manifest_evidence_location() -> None:
    manifest = _manifest()
    manifest["traversal"]["excluded_scenes"] = [
        {
            "scene_id": "excluded",
            "reason": "The other branch was not selected.",
            "source_ref": {**SOURCE_REF, "purpose": "excluded"},
        }
    ]
    manifest["quests"] = [
        {
            "id": "quest",
            "title": "Main quest",
            "status": "available",
            "source_ref": {**SOURCE_REF, "purpose": "quest"},
            "outcome": "",
        }
    ]
    manifest["clues"] = [
        {
            "id": "clue",
            "label": "A clue",
            "status": "hidden",
            "known_by_actor_ids": [],
            "source_ref": {**SOURCE_REF, "purpose": "clue"},
        }
    ]
    manifest["ending"]["conditions"] = [
        {
            "id": "ending",
            "label": "The ending",
            "source_ref": {**SOURCE_REF, "purpose": "ending"},
            "all_of": [
                {
                    "kind": "manifest_value",
                    "path": "world_state.victory",
                    "actor_id": "",
                    "fact_key": "",
                    "operator": "truthy",
                    "value": None,
                }
            ],
        }
    ]

    assert [path for path, _source_ref in playthrough_source_bindings(manifest)] == [
        "source_refs[0]",
        "traversal.excluded_scenes[0].source_ref",
        "quests[0].source_ref",
        "clues[0].source_ref",
        "ending.conditions[0].source_ref",
    ]


def test_manifest_completion_has_one_verified_ending_state() -> None:
    manifest = _manifest()
    member = {
        "actor_id": "",
        "name": "Party member",
        "status": "active",
        "source": "generated",
        "source_asset_path": "",
        "level": 1,
        "xp": 0,
        "hit_points": {"current": 8, "maximum": 8},
        "resources": {},
        "wallet": {},
        "equipment": [],
        "knowledge_scope_actor_id": "",
    }
    for index in range(6):
        current = deepcopy(member)
        current["actor_id"] = f"actor-{index}"
        current["knowledge_scope_actor_id"] = current["actor_id"]
        manifest["party"]["members"].append(current)
    manifest["current"]["scene_id"] = "ending-scene"
    manifest["ending"]["conditions"] = [
        {
            "id": "victory",
            "label": "The threat is ended",
            "source_ref": deepcopy(SOURCE_REF),
            "all_of": [
                {
                    "kind": "manifest_value",
                    "path": "quests.main.status",
                    "actor_id": "",
                    "fact_key": "",
                    "operator": "equals",
                    "value": "completed",
                }
            ],
        }
    ]

    top_only = deepcopy(manifest)
    top_only["status"] = "completed"
    with pytest.raises(ValueError, match="enter completed together"):
        validate_party_state({"playthrough_manifest": top_only})

    ending_only = deepcopy(manifest)
    ending_only["status"] = "in_progress"
    ending_only["ending"]["status"] = "completed"
    ending_only["ending"]["achieved_condition_id"] = "victory"
    ending_only["ending"]["verification"] = [{"passed": True}]
    with pytest.raises(ValueError, match="enter completed together"):
        validate_party_state({"playthrough_manifest": ending_only})

    unverified = deepcopy(manifest)
    unverified["status"] = "completed"
    unverified["ending"]["status"] = "completed"
    unverified["ending"]["achieved_condition_id"] = "victory"
    unverified["ending"]["verification"] = [{"passed": False}]
    with pytest.raises(ValueError, match="every verification result"):
        validate_party_state({"playthrough_manifest": unverified})

    stale = deepcopy(manifest)
    stale["status"] = "in_progress"
    stale["ending"]["achieved_condition_id"] = "victory"
    with pytest.raises(ValueError, match="cannot retain achieved_condition_id"):
        validate_party_state({"playthrough_manifest": stale})

    completed = deepcopy(manifest)
    completed["status"] = "completed"
    completed["ending"]["status"] = "completed"
    completed["ending"]["achieved_condition_id"] = "victory"
    completed["ending"]["verification"] = [{"passed": True}]
    assert (
        validate_party_state({"playthrough_manifest": completed})[
            "playthrough_manifest"
        ]["status"]
        == "completed"
    )


def test_manifest_cannot_leave_lobby_before_quality_gate_passes() -> None:
    manifest = _manifest()
    manifest["status"] = "ready"
    manifest["review_blocks"] = [{"kind": "pregen_review"}]
    with pytest.raises(ValueError, match="review blocks"):
        validate_party_state({"playthrough_manifest": manifest})

    manifest["review_blocks"] = []
    with pytest.raises(ValueError, match="members match selected_size"):
        validate_party_state({"playthrough_manifest": manifest})

    member = {
        "actor_id": "",
        "name": "Party member",
        "status": "active",
        "source": "generated",
        "source_asset_path": "",
        "level": 1,
        "xp": 0,
        "hit_points": {"current": 8, "maximum": 8},
        "resources": {},
        "wallet": {"gp": 10},
        "equipment": [],
        "knowledge_scope_actor_id": "",
    }
    manifest["party"]["members"] = []
    for index in range(6):
        current = deepcopy(member)
        current["actor_id"] = f"actor-{index}"
        current["knowledge_scope_actor_id"] = current["actor_id"]
        manifest["party"]["members"].append(current)
    validated = validate_party_state({"playthrough_manifest": manifest})
    assert validated["playthrough_manifest"]["party"]["members"][0]["wallet"] == {"gp": 10}
    missing_wallet = deepcopy(manifest)
    for member in missing_wallet["party"]["members"]:
        member.pop("wallet")
    with pytest.raises(ValueError, match="wallet must be an object"):
        validate_party_state({"playthrough_manifest": missing_wallet})

    manifest["status"] = "in_progress"
    with pytest.raises(ValueError, match="current scene"):
        validate_party_state({"playthrough_manifest": manifest})
