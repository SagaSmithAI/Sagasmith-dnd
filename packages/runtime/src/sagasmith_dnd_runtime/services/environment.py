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
