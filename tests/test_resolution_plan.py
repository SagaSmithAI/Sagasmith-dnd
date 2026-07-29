from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.resolution_plan import (
    ResolutionPlanBindingError,
    ResolutionPlanCompilationError,
    ResolutionPlanExecutionError,
    bind_resolution_plan,
    compile_resolution_plan,
    execute_resolution_plan,
    resolution_plan_contract,
    resolution_plan_template,
)


def _citation() -> dict:
    return {
        "source": "module:custom-monster",
        "source_ref": {
            "source_key": "custom-monster",
            "chunk_id": "chunk-1",
            "page_start": 7,
        },
        "source_excerpt": (
            "Prismatic Pulse. Each chosen creature must make a Wisdom saving "
            "throw, taking radiant damage on a failed save."
        ),
    }


def _plan() -> dict:
    return {
        "schema_version": 1,
        "id": "module.custom-monster.prismatic-pulse",
        "source_card_id": "custom-monster.prismatic-pulse",
        "source_card_kind": "monster_action",
        "trigger": "action",
        "slots": {
            "targets": {
                "kind": "actor_ids",
                "owner": "agent",
                "description": "Creatures selected from the active encounter.",
                "minimum_items": 1,
                "maximum_items": 3,
            },
            "save_dc": {
                "kind": "integer",
                "owner": "agent",
                "description": "The source-recorded saving throw difficulty class.",
                "minimum": 1,
                "maximum": 40,
            },
            "damage": {
                "kind": "dice",
                "owner": "agent",
                "description": "The source-recorded radiant damage expression.",
            },
        },
        "steps": [
            {
                "id": "save",
                "op": "check.save",
                "args": {
                    "target_ids": {"$slot": "targets"},
                    "ability": "wisdom",
                    "dc": {"$slot": "save_dc"},
                    "source": "Prismatic Pulse",
                    "success_damage": "none",
                },
            },
            {
                "id": "damage",
                "op": "damage.apply",
                "args": {
                    "target_ids": {"$slot": "targets"},
                    "expression": {"$slot": "damage"},
                    "damage_type": "radiant",
                    "source": "Prismatic Pulse",
                    "reduction": {
                        "$result": "save.damage_reduction_by_actor_id"
                    },
                },
            },
        ],
        "citations": [_citation()],
    }


class RecordingRuntime:
    def __init__(self, *, fail_at: str = "") -> None:
        self.fail_at = fail_at
        self.events: list[str] = []

    def begin(self, plan) -> None:
        self.events.append(f"begin:{plan.compiled.id}")

    def execute(self, opcode, arguments, *, step_id, prior_results):
        del prior_results
        self.events.append(f"execute:{step_id}:{opcode}")
        if step_id == self.fail_at:
            raise RuntimeError("forced primitive failure")
        if opcode == "check.save":
            return {
                "damage_reduction_by_actor_id": {
                    actor_id: "none" for actor_id in arguments["target_ids"]
                }
            }
        return {"arguments": deepcopy(arguments)}

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def _agent_ruling() -> dict:
    return {
        "application_id": "pulse-1",
        "default_resolver": "agent",
        "ruling_kind": "agent_dm_adjudication",
        "decision": "The pulse includes both creatures standing inside its marked area.",
        "reason": "Their recorded positions place them within the source-defined radius.",
        "source_ref": _citation()["source_ref"],
        "source_excerpt": _citation()["source_excerpt"],
    }


def test_rule_card_locks_steps_while_agent_only_fills_typed_slots() -> None:
    compiled = compile_resolution_plan(_plan())
    contract = resolution_plan_contract(compiled)

    assert contract["slots"]["targets"]["owner"] == "agent"
    assert "steps" not in contract
    bound = bind_resolution_plan(
        compiled,
        {
            "targets": ["hero-1", "hero-2"],
            "save_dc": 14,
            "damage": "3d8+2",
        },
        agent_ruling=_agent_ruling(),
    )
    assert bound.steps[0]["args"]["target_ids"] == ["hero-1", "hero-2"]
    assert bound.steps[1]["args"]["expression"] == "3d8+2"
    assert bound.fingerprint != compiled.fingerprint
    assert compile_resolution_plan(resolution_plan_template(compiled)) == compiled


def test_plan_rejects_extra_operations_unknown_slots_and_unsafe_values() -> None:
    plan = _plan()
    plan["steps"].append(
        {"id": "python", "op": "python.eval", "args": {"code": "pass"}}
    )
    with pytest.raises(ResolutionPlanCompilationError, match="supported op"):
        compile_resolution_plan(plan)

    compiled = compile_resolution_plan(_plan())
    with pytest.raises(ResolutionPlanBindingError, match="unknown=.*outcome"):
        bind_resolution_plan(
            compiled,
            {
                "targets": ["hero-1"],
                "save_dc": 14,
                "damage": "3d8",
                "outcome": "dead",
            },
        )
    with pytest.raises(ResolutionPlanBindingError, match="above its maximum"):
        bind_resolution_plan(
            compiled,
            {
                "targets": ["hero-1"],
                "save_dc": 99,
                "damage": "3d8",
            },
        )


def test_result_references_are_backward_only() -> None:
    plan = _plan()
    plan["steps"][0]["args"]["dc"] = {"$result": "damage.total"}
    with pytest.raises(ResolutionPlanCompilationError, match="earlier plan step"):
        compile_resolution_plan(plan)


def test_plan_executes_atomically_and_resolves_prior_results() -> None:
    bound = bind_resolution_plan(
        _plan(),
        {
            "targets": ["hero-1", "hero-2"],
            "save_dc": 14,
            "damage": "3d8",
        },
        agent_ruling=_agent_ruling(),
    )
    runtime = RecordingRuntime()

    result = execute_resolution_plan(bound, runtime)

    assert result.status == "committed"
    assert runtime.events[-1] == "commit"
    assert result.results["damage"]["arguments"]["reduction"] == {
        "hero-1": "none",
        "hero-2": "none",
    }
    assert result.receipt["citations"] == [_citation()]
    assert result.receipt["agent_ruling"]["application_id"] == "pulse-1"


def test_plan_rolls_back_every_step_on_primitive_failure() -> None:
    bound = bind_resolution_plan(
        _plan(),
        {
            "targets": ["hero-1"],
            "save_dc": 14,
            "damage": "3d8",
        },
    )
    runtime = RecordingRuntime(fail_at="damage")

    with pytest.raises(ResolutionPlanExecutionError, match="failed atomically"):
        execute_resolution_plan(bound, runtime)

    assert runtime.events[-1] == "rollback"
    assert "commit" not in runtime.events
