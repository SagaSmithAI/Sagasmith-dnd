from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import validate_party_state
from sagasmith_dnd.playthrough import new_playthrough_manifest

SOURCE_REF = {
    "purpose": "party_size",
    "asset_path": "Campaign.pdf",
    "asset_sha256": "a" * 64,
    "page_start": 2,
    "page_end": 2,
    "heading_path": ["Introduction"],
    "chunk_content_sha256": "b" * 64,
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
    manifest = _manifest()
    assert manifest["party"]["selected_size"] == 6
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
        "equipment": [],
        "knowledge_scope_actor_id": "",
    }
    manifest["party"]["members"] = []
    for index in range(6):
        current = deepcopy(member)
        current["actor_id"] = f"actor-{index}"
        current["knowledge_scope_actor_id"] = current["actor_id"]
        manifest["party"]["members"].append(current)
    validate_party_state({"playthrough_manifest": manifest})

    manifest["status"] = "in_progress"
    with pytest.raises(ValueError, match="current scene"):
        validate_party_state({"playthrough_manifest": manifest})
