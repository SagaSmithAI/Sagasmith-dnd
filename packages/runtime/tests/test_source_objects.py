from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd_runtime.services.source_objects import (
    approved_profile,
    bind_falling_net_object_request,
    reviewed_falling_net_section_facts,
)


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


def test_falling_net_object_profile_uses_bound_source_values_not_caller_stats():
    excerpt = (
        "The net has AC 10 and 20 hit points; 5 slashing damage destroys a section. "
        'trap_profile: {"profile_id":"srd5.1.falling_net"}'
    )
    source_ref = {"chunk_id": "net-source", "module_id": "module-1", "scene_id": "scene-1"}
    bound_trap = {
        "profile_id": "srd5.1.falling_net",
        "object_id": "net-1",
        "scene_id": "scene-1",
        "source_ref": '{"chunk_id":"net-source","module_id":"module-1","scene_id":"scene-1"}',
        "status": "triggered",
    }
    requested = {
        "id": "net-1",
        "name": "Falling Net",
        "scene_id": "scene-1",
        "material": "rope",
        "size": "large",
        "resilience": "fragile",
    }

    normalized, profile, facts = bind_falling_net_object_request(
        requested,
        source_content=excerpt,
        bound_trap=bound_trap,
        object_id="net-1",
        scene_id="scene-1",
        source_ref=source_ref,
    )
    assert profile["profile_id"] == "srd5.1.falling_net"
    assert facts["armor_class"] == normalized["armor_class"] == 10
    assert facts["hit_points"] == normalized["hit_points"] == 20
    assert normalized["damage_filter"] == {"allowed_damage_types": ["slashing"]}
    assert requested == {
        "id": "net-1",
        "name": "Falling Net",
        "scene_id": "scene-1",
        "material": "rope",
        "size": "large",
        "resilience": "fragile",
    }

    with pytest.raises(ValueError, match="armor class is fixed"):
        bind_falling_net_object_request(
            {**requested, "armor_class": 5},
            source_content=excerpt,
            bound_trap=bound_trap,
            object_id="net-1",
            scene_id="scene-1",
            source_ref=source_ref,
        )
    with pytest.raises(ValueError, match="hit points are fixed"):
        bind_falling_net_object_request(
            {**requested, "hit_points": 200},
            source_content=excerpt,
            bound_trap=bound_trap,
            object_id="net-1",
            scene_id="scene-1",
            source_ref=source_ref,
        )
    with pytest.raises(ValueError, match="triggered source and scene"):
        bind_falling_net_object_request(
            requested,
            source_content=excerpt,
            bound_trap={**bound_trap, "scene_id": "other-scene"},
            object_id="net-1",
            scene_id="scene-1",
            source_ref=source_ref,
        )


def test_falling_net_section_review_requires_authorized_dm_and_exact_live_bindings():
    profile = {
        "profile_id": "srd5.1.falling_net",
        "trigger": {
            "object": {
                "ac": 10,
                "hp": 20,
                "slashing_damage_to_destroy_section": 5,
            }
        },
    }
    source_ref = {"module_id": "module-1", "scene_id": "scene-1", "chunk_id": "net-1"}
    facts = {
        "decision_id": "net-sections-1",
        "reason": "The DM reviewed the trapped actors against the current net squares.",
        "scene_id": "scene-1",
        "scene_revision": 4,
        "trap_id": "net-1",
        "object_id": "net-1",
        "source_ref": (
            '{"chunk_id":"net-1","module_id":"module-1","scene_id":"scene-1"}'
        ),
        "campaign_revision": 9,
        "reviewed_by": "dm",
        "target_section_id": "northwest",
        "actor_sections": [
            {"actor_id": "scout", "section_id": "northwest"},
            {"actor_id": "guard", "section_id": "southeast"},
        ],
    }
    services = SimpleNamespace(
        is_dm=lambda campaign_id, principal_id: principal_id == "dm",
        modules=SimpleNamespace(
            current_scene=lambda campaign_id, scope_id: {
                "scene_id": "scene-1",
                "state_version": 4,
                "progress": {},
            }
        ),
    )
    args = {
        "campaign_id": "campaign-1",
        "principal_id": "dm",
        "profile": profile,
        "facts": facts,
        "scene_id": "scene-1",
        "trap_id": "net-1",
        "object_id": "net-1",
        "source_ref": source_ref,
        "campaign_revision": 9,
        "restrained_actor_ids": ["scout", "guard"],
        "severed_section_ids": [],
    }
    reviewed = reviewed_falling_net_section_facts(services, **args)
    assert reviewed["affected_actor_ids"] == ["scout"]
    with pytest.raises(ValueError, match="authorized campaign DM"):
        reviewed_falling_net_section_facts(services, **{**args, "principal_id": "player"})
    with pytest.raises(ValueError, match="section_spatial_facts"):
        reviewed_falling_net_section_facts(services, **{**args, "facts": None})
    with pytest.raises(ValueError, match="stale for the current campaign revision"):
        reviewed_falling_net_section_facts(
            services,
            **{**args, "facts": {**facts, "campaign_revision": 8}},
        )
    with pytest.raises(ValueError, match="already destroyed"):
        reviewed_falling_net_section_facts(
            services,
            **{**args, "severed_section_ids": ["northwest"]},
        )
