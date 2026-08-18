from __future__ import annotations

import pytest

from sagasmith_dnd.content_solution import (
    ContentSolutionError,
    build_content_solution,
    normalize_content_solution,
)
from sagasmith_dnd.resolution_plan import compile_resolution_plan


def _plan():
    return compile_resolution_plan(
        {
            "schema_version": 2,
            "id": "module.binding-blade.on-hit",
            "source_card_id": "binding-blade",
            "source_card_kind": "item",
            "trigger": "attack.after_hit",
            "trigger_filter": {
                "source_actor_id": {"$slot": "source_actor"},
                "target_actor_id": {"$slot": "target"},
                "weapon_id": "binding-blade",
                "hit": True,
            },
            "slots": {
                "source_actor": {
                    "kind": "actor_id",
                    "owner": "agent",
                    "description": "The actor wielding the source-bound item.",
                },
                "target": {
                    "kind": "actor_id",
                    "owner": "agent",
                    "description": "The creature hit by the paid attack event.",
                },
            },
            "steps": [
                {
                    "id": "restrain",
                    "op": "condition.apply",
                    "args": {
                        "target_ids": [{"$slot": "target"}],
                        "condition_id": "restrained",
                        "source": "Binding Blade",
                    },
                }
            ],
            "citations": [
                {
                    "source": "module:binding-blade",
                    "source_ref": {
                        "module_id": "module-1",
                        "scene_id": "scene-1",
                    },
                    "source_excerpt": ("On a hit, the binding blade restrains the target."),
                }
            ],
        }
    )


def _ruling() -> dict:
    return {
        "default_resolver": "agent",
        "ruling_kind": "module_specific_procedure",
        "decision": ("Compile the quoted on-hit clause into the stored condition plan."),
        "reason": ("The quoted source defines a deterministic restrained condition."),
    }


def _card() -> dict:
    return {
        "id": "binding-blade",
        "name": "Binding Blade",
        "description": "On a hit, the binding blade restrains the target.",
        "mechanics": {"on_hit": {"condition": "restrained"}},
        "uses": {"value": 1, "max": 1},
    }


def test_build_time_solution_locks_plan_identity_evidence_and_agent_reason() -> None:
    plan = _plan()
    card = _card()

    solution = build_content_solution(
        plan,
        source_card=card,
        application_id="choice:binding-blade",
        agent_ruling=_ruling(),
    )

    assert solution["source_card_id"] == "binding-blade"
    assert solution["plan_fingerprint"] == plan.fingerprint
    assert len(solution["source_fingerprint"]) == 64
    assert len(solution["source_card_fingerprint"]) == 64
    assert normalize_content_solution(solution, plan=plan, source_card=card) == solution


def test_solution_cannot_be_reused_with_changed_plan_or_evidence() -> None:
    plan = _plan()
    card = _card()
    solution = build_content_solution(
        plan,
        source_card=card,
        application_id="choice:binding-blade",
        agent_ruling=_ruling(),
    )
    changed = dict(solution)
    changed["source_fingerprint"] = "0" * 64

    with pytest.raises(ContentSolutionError, match="does not match"):
        normalize_content_solution(changed, plan=plan, source_card=card)

    changed_card = {**card, "description": "The blade now causes blindness."}
    with pytest.raises(ContentSolutionError, match="does not match"):
        normalize_content_solution(
            solution,
            plan=plan,
            source_card=changed_card,
        )

    changed_mechanics = {
        **card,
        "mechanics": {"on_hit": {"condition": "blinded"}},
    }
    with pytest.raises(ContentSolutionError, match="does not match"):
        normalize_content_solution(
            solution,
            plan=plan,
            source_card=changed_mechanics,
        )

    spent_card = {**card, "uses": {"value": 0, "max": 1}}
    assert (
        normalize_content_solution(
            solution,
            plan=plan,
            source_card=spent_card,
        )
        == solution
    )
