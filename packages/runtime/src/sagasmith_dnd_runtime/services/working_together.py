"""One sourced, multi-actor Working Together transaction."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.working_together import resolve_working_together

from .. import application_support as support
from .disease_checks import sight_rot_check_modifier, sight_rot_check_receipt
from .passive_checks import _fields
from .sunlight import prepare_check_facts


@support._agent_ruling_boundary
def working_together_check(
    services: Any, campaign_id: str, payload: dict[str, Any], *, principal_id: str,
    expected_revision: int | None, branch_id: str | None, idempotency_key: str | None,
) -> dict[str, Any]:
    services.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
    services.require_write_contract(expected_revision, idempotency_key)
    branch = services.require_current_branch(campaign_id, branch_id)
    data = _fields(payload, {"actor_ids", "ability", "task"}, {
        "leader_id", "skill_ability", "tool", "rule_facts",
    }, "working_together payload")
    identifiers = data["actor_ids"]
    if (not isinstance(identifiers, list) or len(identifiers) < 2
            or any(not isinstance(item, str) or not item for item in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError("working together requires at least two distinct actor_ids")
    task = _fields(data["task"], {
        "source_ref", "source_excerpt", "reason", "productive", "requirements", "dc",
    }, {"relies_on_sight", "relies_on_hearing"}, "working_together task")
    if not isinstance(task["reason"], str) or not 1 <= len(task["reason"].strip()) <= 2000:
        raise ValueError("working together requires a 1 to 2000 character task review reason")
    replay_payload = {"payload": data, "branch_id": branch}
    scope = f"working-together:{campaign_id}:{branch}:{principal_id}"
    replay = services.replay_idempotent(scope, idempotency_key, replay_payload)
    if replay is not None:
        return replay
    campaign = services.campaigns.get(campaign_id)
    if campaign.revision != expected_revision:
        raise ValueError(
            f"campaign revision conflict: expected {expected_revision}, found {campaign.revision}"
        )
    if services.campaign_rules_edition(campaign_id) != "2014":
        raise ValueError("working together requires separately reviewed 2014 rules")
    if dict(campaign.state or {}).get("combat", {}).get("active"):
        raise ValueError("active combat requires combat_common_action Help with kind=task")
    stream = support.active_random_stream()
    if stream is None:
        with services.campaign_random_context(campaign_id, "character_check", {
            "idempotency_key": idempotency_key,
        }):
            return working_together_check(
                services, campaign_id, payload, principal_id=principal_id,
                expected_revision=expected_revision, branch_id=branch,
                idempotency_key=idempotency_key,
            )
    random_state = support.validate_random_stream_state(
        campaign.state.get("random_stream") or support.initial_random_stream(
            f"sagasmith-dnd:{campaign_id}"
        )
    )
    if (stream.campaign_id != campaign_id or stream.campaign_revision != campaign.revision
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]):
        raise ValueError("working together requires the current campaign random snapshot")
    _, task["source_ref"], expanded = services.managed_module_source_ref(
        campaign_id, task["source_ref"], require_exact=True, require_active_module=True,
    )
    assert expanded is not None
    services.managed_module_source_excerpt(
        expanded, task["source_excerpt"], field="working together source_excerpt",
        minimum_length=10,
    )
    snapshots = []
    contexts = {}
    facts = services.checked_rule_facts(data.get("rule_facts"))
    for identifier in identifiers:
        actor = services.require_campaign_actor(campaign_id, identifier)
        if services.narrative_only_actor(actor):
            raise ValueError("working together requires exact actor statblocks")
        snapshots.append(services.combat_actor_snapshot(identifier))
        prepared = prepare_check_facts(
            services, facts, campaign_id=campaign_id, actor_id=identifier,
            principal_id=principal_id,
        )
        contexts[identifier] = services.effective_rule_context(
            campaign_id, branch_id=branch, facts={
                **prepared, "actor_id": identifier, "kind": "check", "ability": data["ability"],
                "skill_ability": data.get("skill_ability"), "dc": task["dc"],
                "action": "working_together", "participant_ids": identifiers,
                **{key: task[key] for key in ("relies_on_sight", "relies_on_hearing")
                   if key in task},
            },
        )
    sight_modifiers = {
        snapshot["id"]: modifier
        for snapshot in snapshots
        if (modifier := sight_rot_check_modifier(
            snapshot, relies_on_sight=task.get("relies_on_sight")
        )) is not None
    }
    result = resolve_working_together(
        snapshots, ability=data["ability"], dc=task["dc"], task=task,
        leader_id=data.get("leader_id"), skill_ability=data.get("skill_ability"),
        tool=data.get("tool"), rules_by_actor_id=contexts,
        check_bonus_adjustments={
            actor_id: modifier["penalty"]
            for actor_id, modifier in sight_modifiers.items()
        },
    )
    leader_modifier = sight_modifiers.get(result["leader_id"])
    if leader_modifier is not None:
        disease_receipt = sight_rot_check_receipt(
            services,
            campaign_id=campaign_id,
            branch_id=branch,
            actor_id=result["leader_id"],
            kind="check",
            ability=data["ability"],
            modifier=leader_modifier,
            event="character.working_together",
        )
        result["check"]["disease_modifier"] = disease_receipt
        result["check"]["rule_receipts"] = [
            *list(result["check"].get("rule_receipts") or []), disease_receipt,
        ]
        result["rule_receipts"] = [*result["rule_receipts"], disease_receipt]
    resolution_id = f"resolution-{support.uuid4().hex}"
    audience = {"scope": "actors", "actor_refs": identifiers, "disclosure": "private"}
    next_state = deepcopy(campaign.state)
    next_state["resolution_log"] = [*list(next_state.get("resolution_log") or []), {
        "id": resolution_id, "thread_id": resolution_id, "event_sequence": 1,
        "type": "working_together", "operation": "character.working_together",
        "actor_id": result["leader_id"], "audience": audience, "branch_id": branch,
        "campaign_revision": campaign.revision + 1, "result": result, "task": task,
    }][-100:]

    def response(revisions: list[Any]) -> dict:
        value = {
            "status": "committed", "resolution_id": resolution_id, "thread_id": resolution_id,
            "event_sequence": 1, "audience": audience, "result": result, "task": task,
            "campaign_revision": campaign.revision + 1,
            "revisions": [support.asdict(item) for item in revisions],
        }
        stream = support.active_random_stream()
        if stream is not None and stream.draw_count:
            value["random_stream_receipt"] = stream.receipt()
        return value

    revisions = support.StateMutationService(services.storage.database).replace(
        campaign_id, campaign_state=support.validate_party_state(next_state),
        expected_campaign_revision=campaign.revision,
        character_updates=[support.CharacterStateUpdate(
            character_id=snapshot["id"], sheet=snapshot["sheet"], notes=snapshot["notes"],
            expected_revision=snapshot["revision"],
        ) for snapshot in snapshots],
        operation="character.working_together", actor=principal_id, branch_id=branch,
        idempotency_key=idempotency_key,
        idempotency_write=support.IdempotencyWrite(
            scope=scope, payload=replay_payload, response=response,
        ), rule_receipts=result["rule_receipts"],
    )
    return response(list(revisions or []))
