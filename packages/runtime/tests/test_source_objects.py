from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd_runtime.services.source_objects import approved_profile


def fixture():
    services = SimpleNamespace(
        content_authority_secret=b"test-object-secret",
        is_dm=lambda campaign, principal: principal == "dm",
        managed_module_source_excerpt=lambda *args, **kwargs: None,
    )
    profile = {
        "id": "door",
        "scene_id": "scene",
        "name": "Door",
        "armor_class": 15,
        "hit_points": 18,
        "material": "wood",
        "size": "medium",
        "resilience": "resilient",
    }
    params = {
        "services": services,
        "campaign_id": "campaign",
        "branch_id": "main",
        "principal_id": "dm",
        "requested": profile,
        "existing": {},
        "source_ref": {"chunk_id": "exact-source"},
        "expanded": {},
        "ruling": {
            "reason": "DM approved the source statistics.",
            "source_excerpt": "Wooden door source.",
        },
    }
    reviewed, approval, hp = approved_profile(**params)
    return params, {"profile": reviewed, "profile_approval": approval, "hit_points": hp}


@pytest.mark.parametrize("tamper", ["stats", "signature", "source", "campaign", "identity"])
def test_signed_profile_rejects_rebinding(tamper):
    params, record = fixture()
    args = {
        **params,
        "existing": record,
        "requested": {"id": "door", "scene_id": "scene"},
        "ruling": None,
    }
    if tamper == "stats":
        record["profile"]["armor_class"] = 1
    elif tamper == "signature":
        record["profile_approval"] = {"fake": True}
    elif tamper == "source":
        args["source_ref"] = {"chunk_id": "different-source"}
    elif tamper == "campaign":
        args["campaign_id"] = "another-campaign"
    else:
        args["requested"]["id"] = "another-object"
    before = deepcopy(record)
    with pytest.raises(ValueError):
        approved_profile(**args)
    assert record == before


def test_inherited_profile_keeps_original_review_and_current_branch_hp():
    params, record = fixture()
    record["hit_points"] = 3
    result, approval, hp = approved_profile(
        **{
            **params,
            "existing": record,
            "branch_id": "fork",
            "principal_id": "player",
            "requested": {"id": "door", "scene_id": "scene"},
            "ruling": None,
        }
    )
    assert hp == 3
    assert approval == record["profile_approval"]
    assert result == record["profile"]


def test_legacy_review_never_restores_hp_or_replaces_original_statistics():
    params, record = fixture()
    legacy = {
        "id": "door",
        "scene_id": "scene",
        "armor_class": 15,
        "hit_point_maximum": 18,
        "hit_points": 3,
        "source_ref": params["source_ref"],
    }
    _, approval, hp = approved_profile(**{**params, "existing": legacy})
    assert hp == 3
    assert approval
    with pytest.raises(ValueError, match="cannot replace"):
        approved_profile(
            **{
                **params,
                "existing": legacy,
                "requested": {**params["requested"], "hit_points": 100},
            }
        )
