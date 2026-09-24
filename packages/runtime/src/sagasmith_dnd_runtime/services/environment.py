"""Source-bound environmental transitions shared by combat and exploration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.water import WATER_RULE, validate_water_state

from .. import application_support as support
from .passive_checks import _fields


def change_water_environment(
    services: Any, campaign_id: str, payload: dict[str, Any], *, principal_id: str,
    expected_revision: int | None, branch_id: str | None, idempotency_key: str | None,
) -> dict[str, Any]:
    services.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
    services.require_write_contract(expected_revision, idempotency_key)
    branch = services.require_current_branch(campaign_id, branch_id)
    data = _fields(payload, {"source_ref", "source_excerpt", "reason"}, {
        "actors", "objects",
    }, "water environment")
    if not isinstance(data["reason"], str) or not 1 <= len(data["reason"].strip()) <= 2000:
        raise ValueError("water environment requires a bounded DM review reason")
    for key in ("actors", "objects"):
        if key in data and (not isinstance(data[key], list) or len(data[key]) > 100):
            raise ValueError(f"water {key} must be a list of at most 100 entries")
    if not data.get("actors") and not data.get("objects"):
        raise ValueError("water environment requires at least one actor or object")
    scope = f"water-environment:{campaign_id}:{branch}:{principal_id}"
    replay_payload = {"payload": data, "branch_id": branch}
    replay = services.replay_idempotent(scope, idempotency_key, replay_payload)
    if replay is not None:
        return replay
    campaign = services.campaigns.get(campaign_id)
    if campaign.revision != expected_revision:
        raise ValueError(
            f"campaign revision conflict: expected {expected_revision}, found {campaign.revision}"
        )
    if services.campaign_rules_edition(campaign_id) != "2014":
        raise ValueError("water environment requires the reviewed 2014 rules")
    combat = dict(campaign.state.get("combat") or {})
    if combat.get("active"):
        services.require_no_blocking_pending(combat)
    _, source_ref, expanded = services.managed_module_source_ref(
        campaign_id, data["source_ref"], require_exact=True, require_active_module=True,
    )
    assert expanded is not None
    services.managed_module_source_excerpt(
        expanded, data["source_excerpt"], field="water source_excerpt", minimum_length=10,
    )
    rules = services.effective_rule_context(campaign_id, branch_id=branch)
    receipts = support.core_receipts(rules, [WATER_RULE], "environment.water.transition")
    resolution_id = f"resolution-{support.uuid4().hex}"
    next_state = deepcopy(campaign.state)
    updates = []
    actor_states = {}
    object_states = []
    for entry in data.get("actors", []):
        entry = _fields(entry, {"actor_id", "underwater", "fully_immersed"}, set(), "water actor")
        identifier = entry.pop("actor_id")
        if not isinstance(identifier, str) or not identifier or identifier in actor_states:
            raise ValueError("water actor_ids must be unique nonempty identifiers")
        state = validate_water_state({**entry, "resolution_id": resolution_id,
                                      "rule_receipts": receipts})
        actor = services.require_campaign_actor(campaign_id, identifier)
        if services.narrative_only_actor(actor):
            raise ValueError("water combat mechanics require an exact actor card")
        sheet = deepcopy(actor.sheet)
        sheet["combat"]["water_environment"] = state
        updates.append(support.CharacterStateUpdate(
            character_id=actor.id, sheet=support.validate_character_sheet(sheet), notes=actor.notes,
            expected_revision=actor.revision,
        ))
        actor_states[identifier] = state
    seen_objects = set()
    for entry in data.get("objects", []):
        entry = _fields(entry, {"scene_id", "object_id", "fully_immersed"}, set(), "water object")
        scene_id, object_id = entry["scene_id"], entry["object_id"]
        if any(not isinstance(item, str) or not item for item in (scene_id, object_id)):
            raise ValueError("water object identifiers must be nonempty strings")
        if (scene_id, object_id) in seen_objects:
            raise ValueError("water object identifiers must be unique")
        seen_objects.add((scene_id, object_id))
        if type(entry["fully_immersed"]) is not bool:
            raise ValueError("object fully_immersed must be a boolean")
        existing = next_state.get("scene_objects", {}).get(scene_id, {}).get(object_id)
        if not isinstance(existing, dict) or not existing.get("profile_approval"):
            raise ValueError("water object requires an existing source-reviewed object profile")
        existing["water_environment"] = {
            "fully_immersed": entry["fully_immersed"], "resolution_id": resolution_id,
            "rule_receipts": receipts,
        }
        existing["fully_immersed"] = entry["fully_immersed"]
        object_states.append(entry)
    audience = {"scope": "dm", "actor_refs": [], "disclosure": "hidden"}
    next_state["resolution_log"] = [*list(next_state.get("resolution_log") or []), {
        "id": resolution_id, "thread_id": resolution_id, "event_sequence": 1,
        "type": "environment_water", "operation": "environment.water.transition",
        "audience": audience, "branch_id": branch, "campaign_revision": campaign.revision + 1,
        "result": {"actors": actor_states, "objects": object_states},
        "source_ref": source_ref, "source_excerpt": data["source_excerpt"],
        "reason": data["reason"],
    }][-100:]

    def response(revisions: list[Any]) -> dict:
        return {
            "status": "committed", "resolution_id": resolution_id, "thread_id": resolution_id,
            "event_sequence": 1, "audience": audience, "campaign_revision": campaign.revision + 1,
            "actors": actor_states, "objects": object_states, "rule_receipts": receipts,
            "revisions": [support.asdict(item) for item in revisions],
        }

    revisions = support.StateMutationService(services.storage.database).replace(
        campaign_id, campaign_state=support.validate_party_state(next_state),
        expected_campaign_revision=campaign.revision, character_updates=updates,
        operation="environment.water.transition", actor=principal_id, branch_id=branch,
        idempotency_key=idempotency_key, rule_receipts=receipts,
        idempotency_write=support.IdempotencyWrite(
            scope=scope, payload=replay_payload, response=response,
        ),
    )
    return response(list(revisions or []))


def review_scene_object_strength_check(
    services: Any,
    campaign_id: str,
    payload: dict[str, Any],
    *,
    principal_id: str,
    expected_revision: int | None,
    branch_id: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Register a DM-reviewed Strength-check fact for one exact scene object."""
    from sagasmith_dnd.objects import validate_gear_strength_check

    from .source_objects import approved_profile, review_gear_strength_check

    services.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
    services.require_write_contract(expected_revision, idempotency_key)
    branch = services.require_current_branch(campaign_id, branch_id)
    data = _fields(
        payload,
        {"object", "object_source_ref", "object_ruling", "strength_check"},
        set(),
        "object strength review",
    )
    requested = data["object"]
    if not isinstance(requested, dict):
        raise ValueError("object strength review requires a source object profile or reference")
    if services.campaign_rules_edition(campaign_id) != "2014":
        raise ValueError("object gear strength checks require the reviewed 2014 rules")
    facts = validate_gear_strength_check(data["strength_check"])
    scope = f"scene-object-strength-review:{campaign_id}:{branch}:{principal_id}"
    replay_payload = {"payload": data, "branch_id": branch}
    replay = services.replay_idempotent(scope, idempotency_key, replay_payload)
    if replay is not None:
        return replay
    campaign = services.campaigns.get(campaign_id)
    if campaign.revision != expected_revision:
        raise ValueError(
            f"campaign revision conflict: expected {expected_revision}, found {campaign.revision}"
        )
    combat = dict(campaign.state.get("combat") or {})
    if combat.get("active"):
        services.require_no_blocking_pending(combat)
    object_id = str(requested.get("id") or "").strip()
    scene_id = str(requested.get("scene_id") or "").strip()
    if not object_id or not scene_id:
        raise ValueError("object strength review requires object id and scene_id")
    if facts["door"] is False and str(requested.get("name") or "").strip() == "":
        raise ValueError("object strength review requires the exact named scene object")
    _, source_ref, expanded = services.managed_module_source_ref(
        campaign_id,
        data["object_source_ref"],
        require_exact=True,
        expected_scene_id=scene_id,
        require_active_module=True,
    )
    assert source_ref is not None and expanded is not None
    scene_objects = deepcopy(dict(campaign.state.get("scene_objects") or {}))
    scene_state = deepcopy(dict(scene_objects.get(scene_id) or {}))
    existing = deepcopy(dict(scene_state.get(object_id) or {}))
    profile, profile_approval, hit_points = approved_profile(
        services,
        campaign_id=campaign_id,
        branch_id=branch,
        principal_id=principal_id,
        requested=requested,
        existing=existing,
        source_ref=source_ref,
        expanded=expanded,
        ruling=data["object_ruling"],
    )
    if hit_points <= 0 or existing.get("destroyed"):
        raise ValueError("a destroyed scene object cannot receive a new Strength-check review")
    review_record, review_approval = review_gear_strength_check(
        services,
        campaign_id=campaign_id,
        branch_id=branch,
        principal_id=principal_id,
        profile=profile,
        profile_approval=profile_approval,
        source_ref=source_ref,
        expanded=expanded,
        facts=facts,
        ruling=data["object_ruling"],
    )
    next_object = {
        **existing,
        **profile,
        "hit_point_maximum": profile["hit_points"],
        "profile": profile,
        "profile_approval": profile_approval,
        "source_ref": source_ref,
        "hit_points": hit_points,
        "destroyed": False,
        "gear_strength_check": review_record,
        "gear_strength_check_approval": review_approval,
    }
    scene_state[object_id] = next_object
    scene_objects[scene_id] = scene_state
    next_state = deepcopy(campaign.state)
    next_state["scene_objects"] = scene_objects
    resolution_id = f"resolution-{support.uuid4().hex}"
    audience = {"scope": "dm", "actor_refs": [], "disclosure": "hidden"}
    result = {
        "object_id": object_id,
        "scene_id": scene_id,
        "strength_check": review_record,
        "strength_check_approval": review_approval,
        "profile_approval": profile_approval,
    }
    next_state["resolution_log"] = [
        *list(next_state.get("resolution_log") or []),
        {
            "id": resolution_id,
            "thread_id": resolution_id,
            "event_sequence": 1,
            "type": "scene_object_strength_review",
            "operation": "environment.object_strength_review",
            "audience": audience,
            "branch_id": branch,
            "campaign_revision": campaign.revision + 1,
            "result": result,
            "source_ref": source_ref,
            "source_excerpt": review_record["source_excerpt"],
            "reason": review_record["reason"],
        },
    ][-100:]

    def response(revisions: list[Any]) -> dict[str, Any]:
        return {
            "status": "committed",
            "resolution_id": resolution_id,
            "event_sequence": 1,
            "audience": audience,
            "campaign_revision": campaign.revision + 1,
            "object": deepcopy(next_object),
            "rule_receipts": [],
            "revisions": [support.asdict(item) for item in revisions],
        }

    revisions = support.StateMutationService(services.storage.database).replace(
        campaign_id,
        campaign_state=support.validate_party_state(next_state),
        expected_campaign_revision=campaign.revision,
        character_updates=[],
        operation="environment.object_strength_review",
        actor=principal_id,
        branch_id=branch,
        idempotency_key=idempotency_key,
        idempotency_write=support.IdempotencyWrite(
            scope=scope, payload=replay_payload, response=response,
        ),
    )
    return response(list(revisions or []))
