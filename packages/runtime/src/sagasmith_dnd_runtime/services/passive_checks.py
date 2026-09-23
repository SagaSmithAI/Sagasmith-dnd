"""Source-reviewed, nonrolling scene checks under the normal write contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.combat_engine import resolve_actor_check
from sagasmith_dnd.travel import travel_passive_perception_bonus

from .. import application_support as support
from .sunlight import prepare_check_facts


def _fields(value: Any, required: set[str], optional: set[str], name: str) -> dict:
    if not isinstance(value, dict) or required - set(value) or set(value) - required - optional:
        raise ValueError(
            f"{name} requires {sorted(required)}; optional fields are {sorted(optional)}"
        )
    return deepcopy(value)


def chase_passive_contexts(
    services: Any, campaign_id: str, branch_id: str, principal_id: str,
    pursuer_ids: list[str], raw: dict | None,
) -> dict:
    """Authorize observer facts before the Domain evaluates current pursuer cards."""
    if raw is not None and (not isinstance(raw, dict) or set(raw) - set(pursuer_ids)):
        raise ValueError("passive_rule_facts may identify only chase pursuers")
    return {
        identifier: services.effective_rule_context(
            campaign_id, branch_id=branch_id,
            facts={**prepare_check_facts(
                services, services.checked_rule_facts((raw or {}).get(identifier)),
                campaign_id=campaign_id, actor_id=identifier, principal_id=principal_id,
            ), "actor_id": identifier, "kind": "check", "ability": "perception",
                "passive": True, "action": "chase_observe"},
        ) for identifier in pursuer_ids
    }


def resolve_passive_scene_check(
    services: Any,
    campaign_id: str,
    payload: dict[str, Any],
    *,
    principal_id: str,
    expected_revision: int | None,
    branch_id: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """The DM classifies the sourced task; Runtime derives all actor totals."""
    services.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
    services.require_write_contract(expected_revision, idempotency_key)
    resolved_branch = services.require_current_branch(campaign_id, branch_id)
    data = _fields(payload, {"actor_id", "ability", "task"}, {
        "skill_ability", "rule_facts", "secret",
    }, "passive payload")
    secret = data.get("secret", True)
    if type(secret) is not bool:
        raise ValueError("passive secret must be a boolean")
    task = _fields(data["task"], {"source_ref", "source_excerpt", "reason", "mode"}, {
        "dc", "opponent", "advantage", "disadvantage", "relies_on_sight", "relies_on_hearing",
    }, "passive task")
    if task["mode"] not in {"repeated_task", "exploration", "trap_detection", "opposed"}:
        raise ValueError("unsupported passive task mode")
    if not isinstance(task["reason"], str) or not 1 <= len(task["reason"].strip()) <= 2000:
        raise ValueError("passive task reason must contain 1 to 2000 characters")
    for flag in ("advantage", "disadvantage", "relies_on_sight", "relies_on_hearing"):
        if flag in task and type(task[flag]) is not bool:
            raise ValueError(f"passive task {flag} must be a boolean")
    if task["mode"] == "opposed":
        if "dc" in task or "opponent" not in task:
            raise ValueError("opposed passive tasks require opponent instead of dc")
        opponent = _fields(task["opponent"], {"actor_id", "ability"}, {
            "skill_ability", "rule_facts",
        }, "passive opponent")
        if opponent["actor_id"] == data["actor_id"]:
            raise ValueError("a passive opponent must be a different actor")
    else:
        if "opponent" in task or type(task.get("dc")) is not int or not 0 <= task["dc"] <= 100:
            raise ValueError("passive tasks require an integer dc from 0 to 100")
        opponent = None
    # Preserve the caller's exact identity for unknown-write retries, before any
    # source/state lookup that may have changed since a successful settlement.
    replay_payload = {"payload": data, "branch_id": resolved_branch}
    scope = f"character-passive:{campaign_id}:{resolved_branch}:{principal_id}"
    replay = services.replay_idempotent(scope, idempotency_key, replay_payload)
    if replay is not None:
        return replay
    campaign = services.campaigns.get(campaign_id)
    if campaign.revision != expected_revision:
        raise ValueError(
            f"campaign revision conflict: expected {expected_revision}, found {campaign.revision}"
        )
    if dict(campaign.state or {}).get("combat", {}).get("active"):
        raise ValueError("passive scene checks cannot bypass active combat procedures")
    _, exact_source, expanded = services.managed_module_source_ref(
        campaign_id, task["source_ref"], require_exact=True, require_active_module=True,
    )
    assert expanded is not None
    services.managed_module_source_excerpt(
        expanded, task["source_excerpt"], field="passive task source_excerpt", minimum_length=10,
    )
    task["source_ref"] = exact_source
    snapshots: list[dict] = []

    def resolve(selection: dict, *, dc: int, circumstance: bool = False) -> dict:
        actor = services.require_campaign_actor(campaign_id, selection["actor_id"])
        if services.narrative_only_actor(actor):
            raise ValueError("passive checks require an exact actor statblock")
        snapshot = services.combat_actor_snapshot(actor.id)
        snapshots.append(snapshot)
        facts = services.checked_rule_facts(selection.get("rule_facts"))
        facts = prepare_check_facts(
            services, facts, campaign_id=campaign_id, actor_id=actor.id,
            principal_id=principal_id,
        )
        rules = services.effective_rule_context(
            campaign_id, branch_id=resolved_branch,
            facts={**facts, "actor_id": actor.id, "kind": "check",
                   "ability": selection["ability"], "skill_ability": selection.get("skill_ability"),
                   "dc": dc, "passive": True, "task_mode": task["mode"],
                   **{key: task[key] for key in ("relies_on_sight", "relies_on_hearing")
                      if key in task}},
        )
        travel_bonus = (
            travel_passive_perception_bonus(campaign.state, actor.id)
            if str(selection["ability"]).strip().casefold() == "perception"
            else 0
        )
        result = resolve_actor_check(
            snapshot, kind="check", ability=selection["ability"], dc=dc, passive=True,
            skill_ability=selection.get("skill_ability"), rules=rules,
            bonus=travel_bonus,
            advantage=circumstance and task.get("advantage", False),
            disadvantage=circumstance and task.get("disadvantage", False),
        )
        result["actor_id"] = actor.id
        result["ability"] = selection["ability"]
        if travel_bonus:
            result["travel_pace_modifier"] = travel_bonus
        if selection.get("skill_ability"):
            result["skill_ability"] = selection["skill_ability"]
        return result

    opposing_result = resolve(opponent, dc=0) if opponent else None
    result = resolve(data, dc=(opposing_result["total"] or 0) if opposing_result else task["dc"],
                     circumstance=True)
    if opposing_result is not None:
        # Contests keep the prior situation on a tie, rather than treating the
        # opposing score as an ordinary DC that succeeds on equality.
        result["opponent"] = opposing_result
        if result.get("automatic_failure") or opposing_result.get("automatic_failure"):
            result["tie"] = bool(result.get("automatic_failure")) == bool(
                opposing_result.get("automatic_failure")
            )
            result["success"] = not result.get("automatic_failure", False)
        else:
            result["success"] = result["total"] > opposing_result["total"]
            result["tie"] = result["total"] == opposing_result["total"]
        result["outcome"] = "unchanged" if result["tie"] else (
            "actor" if result["success"] else "opponent"
        )
    resolution_id = f"resolution-{support.uuid4().hex}"
    audience = {"scope": "dm", "actor_refs": [], "disclosure": "hidden"} if secret else {
        "scope": "actors", "actor_refs": [data["actor_id"]], "disclosure": "private",
    }
    next_state = deepcopy(campaign.state)
    next_state["resolution_log"] = [*list(next_state.get("resolution_log") or []), {
        "id": resolution_id, "thread_id": resolution_id, "event_sequence": 1,
        "type": "passive", "operation": "character.passive", "actor_id": data["actor_id"],
        "audience": audience, "branch_id": resolved_branch,
        "campaign_revision": campaign.revision + 1, "result": result, "task": task,
    }][-100:]
    receipts = [*list(result.get("rule_receipts") or []),
                *list((opposing_result or {}).get("rule_receipts") or [])]

    def response(revisions: list[Any]) -> dict:
        return {
            "status": "committed", "resolution_id": resolution_id, "thread_id": resolution_id,
            "event_sequence": 1, "audience": audience, "result": result,
            "task": task, "campaign_revision": campaign.revision + 1,
            "revisions": [support.asdict(item) for item in revisions],
        }

    revisions = support.StateMutationService(services.storage.database).replace(
        campaign_id, campaign_state=support.validate_party_state(next_state),
        expected_campaign_revision=campaign.revision,
        character_updates=[support.CharacterStateUpdate(
            character_id=snapshot["id"], sheet=snapshot["sheet"], notes=snapshot["notes"],
            expected_revision=snapshot["revision"],
        ) for snapshot in snapshots],
        operation="character.passive", actor=principal_id, branch_id=resolved_branch,
        idempotency_key=idempotency_key,
        idempotency_write=support.IdempotencyWrite(scope=scope, payload=replay_payload,
                                                  response=response),
        rule_receipts=receipts,
    )
    return response(list(revisions or []))
