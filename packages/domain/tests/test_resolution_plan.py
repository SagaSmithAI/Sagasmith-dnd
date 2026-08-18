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
    require_resolution_plan_trigger,
    resolution_plan_contract,
    resolution_plan_template,
    resolution_plan_trigger_matches,
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
        "schema_version": 2,
        "id": "module.custom-monster.prismatic-pulse",
        "source_card_id": "custom-monster.prismatic-pulse",
        "source_card_kind": "monster_action",
        "trigger": "action",
        "slots": {
            "source_actor": {
                "kind": "actor_id",
                "owner": "agent",
                "description": "The actor using the reviewed source card.",
            },
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
                "id": "targets",
                "op": "target.validate",
                "args": {
                    "source_actor_id": {"$slot": "source_actor"},
                    "target_ids": {"$slot": "targets"},
                    "exclude_self": True,
                    "require_visible": True,
                    "source": "Prismatic Pulse",
                },
            },
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
                    "reduction": {"$result": "save.damage_reduction_by_actor_id"},
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
            "source_actor": "prism-beast",
            "targets": ["hero-1", "hero-2"],
            "save_dc": 14,
            "damage": "3d8+2",
        },
        agent_ruling=_agent_ruling(),
    )
    assert bound.steps[0]["args"]["target_ids"] == ["hero-1", "hero-2"]
    assert bound.steps[2]["args"]["expression"] == "3d8+2"
    assert bound.fingerprint != compiled.fingerprint
    assert compile_resolution_plan(resolution_plan_template(compiled)) == compiled


def test_plan_rejects_extra_operations_unknown_slots_and_unsafe_values() -> None:
    plan = _plan()
    plan["steps"].append({"id": "python", "op": "python.eval", "args": {"code": "pass"}})
    with pytest.raises(ResolutionPlanCompilationError, match="supported op"):
        compile_resolution_plan(plan)

    compiled = compile_resolution_plan(_plan())
    with pytest.raises(ResolutionPlanBindingError, match="unknown=.*outcome"):
        bind_resolution_plan(
            compiled,
            {
                "source_actor": "prism-beast",
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
                "source_actor": "prism-beast",
                "targets": ["hero-1"],
                "save_dc": 99,
                "damage": "3d8",
            },
        )


def test_result_references_are_backward_only() -> None:
    plan = _plan()
    plan["steps"][1]["args"]["dc"] = {"$result": "damage.total"}
    with pytest.raises(ResolutionPlanCompilationError, match="earlier plan step"):
        compile_resolution_plan(plan)


def test_plan_executes_atomically_and_resolves_prior_results() -> None:
    bound = bind_resolution_plan(
        _plan(),
        {
            "source_actor": "prism-beast",
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
            "source_actor": "prism-beast",
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


def test_v2_trigger_filter_binds_a_plan_to_the_paid_attack_event() -> None:
    plan = _plan()
    plan["schema_version"] = 2
    plan["trigger"] = "attack.after_hit"
    plan["slots"]["target_actor"] = {
        "kind": "actor_id",
        "owner": "external_input",
        "description": "The target recorded by the paid attack event.",
    }
    plan["slots"]["weapon_ref"] = {
        "kind": "text",
        "owner": "external_input",
        "description": "The weapon identifier recorded by the paid attack event.",
    }
    del plan["slots"]["targets"]
    for step in plan["steps"]:
        if "target_ids" in step["args"]:
            step["args"]["target_ids"] = [{"$slot": "target_actor"}]
    plan["trigger_filter"] = {
        "source_actor_id": {"$slot": "source_actor"},
        "target_actor_id": {"$slot": "target_actor"},
        "weapon_id": {"$slot": "weapon_ref"},
        "hit": True,
    }

    compiled = compile_resolution_plan(plan)
    bound = bind_resolution_plan(
        compiled,
        {
            "source_actor": "prism-beast",
            "target_actor": "hero-1",
            "weapon_ref": "binding-blade",
            "save_dc": 14,
            "damage": "3d8",
        },
    )
    event = {
        "trigger": "attack.after_hit",
        "source_actor_id": "prism-beast",
        "target_actor_id": "hero-1",
        "weapon_id": "binding-blade",
        "hit": True,
        "critical": False,
    }

    assert resolution_plan_contract(compiled)["trigger_filter"]["hit"] is True
    assert bound.trigger_filter["target_actor_id"] == "hero-1"
    assert resolution_plan_trigger_matches(bound, event) is True
    require_resolution_plan_trigger(bound, event)
    assert (
        resolution_plan_trigger_matches(
            bound,
            {**event, "target_actor_id": "hero-2"},
        )
        is False
    )
    with pytest.raises(ResolutionPlanExecutionError, match="paid engine event"):
        require_resolution_plan_trigger(
            bound,
            {**event, "weapon_id": "lookalike-blade"},
        )
    assert compile_resolution_plan(resolution_plan_template(compiled)) == compiled


def test_retired_plan_schema_and_unknown_event_fields_are_rejected() -> None:
    retired = _plan()
    retired["schema_version"] = 1
    with pytest.raises(ResolutionPlanCompilationError, match="schema_version must be 2"):
        compile_resolution_plan(retired)

    plan = _plan()
    plan["schema_version"] = 2
    plan["trigger_filter"] = {"weapon_id": "not-an-action-field"}
    with pytest.raises(
        ResolutionPlanCompilationError,
        match="unsupported event fields",
    ):
        compile_resolution_plan(plan)


def _attack_ac_bonus_plan(arguments: dict) -> dict:
    return {
        "schema_version": 2,
        "id": "addon.parry.after-hit",
        "source_card_id": "parry",
        "source_card_kind": "activity",
        "trigger": "attack.after_hit",
        "trigger_filter": {"hit": True},
        "slots": {},
        "steps": [
            {
                "id": "defend",
                "op": "attack.ac_bonus",
                "args": arguments,
            }
        ],
        "citations": [_citation()],
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"bonus": 0, "attack_modes": ["melee"]}, "range 1..20"),
        ({"bonus": 21, "attack_modes": ["melee"]}, "range 1..20"),
        ({"bonus": True, "attack_modes": ["melee"]}, "range 1..20"),
        ({"bonus": 2, "attack_modes": []}, "unique melee/ranged"),
        (
            {"bonus": 2, "attack_modes": ["melee", "melee"]},
            "unique melee/ranged",
        ),
        ({"bonus": 2, "attack_modes": ["spell"]}, "unique melee/ranged"),
        (
            {
                "bonus": 2,
                "attack_modes": ["melee"],
                "requires_visible_attacker": 1,
            },
            "must be boolean",
        ),
    ],
)
def test_attack_ac_bonus_plan_rejects_nonstatic_or_invalid_semantics(
    arguments: dict,
    message: str,
) -> None:
    with pytest.raises(ResolutionPlanCompilationError, match=message):
        compile_resolution_plan(_attack_ac_bonus_plan(arguments))


def test_attack_ac_bonus_plan_rejects_agent_bound_bonus_slots() -> None:
    plan = _attack_ac_bonus_plan({"bonus": {"$slot": "bonus"}, "attack_modes": ["melee"]})
    plan["slots"] = {
        "bonus": {
            "kind": "integer",
            "owner": "agent",
            "description": "Untrusted contextual bonus.",
            "minimum": 1,
            "maximum": 20,
        }
    }
    with pytest.raises(ResolutionPlanCompilationError, match="range 1..20"):
        compile_resolution_plan(plan)


def test_duration_and_random_exclusions_are_source_bounded() -> None:
    plan = _plan()
    plan["steps"].extend(
        [
            {
                "id": "ray",
                "op": "roll.table",
                "args": {
                    "table": [
                        {"weight": 1, "value": "dazing"},
                        {"weight": 1, "value": "fear"},
                    ],
                    "exclude": ["fear"],
                },
            },
            {
                "id": "condition",
                "op": "condition.apply",
                "args": {
                    "source_actor_id": {"$slot": "source_actor"},
                    "target_ids": {"$slot": "targets"},
                    "condition_id": "frightened",
                    "duration": {"kind": "source_turn_start"},
                    "source": "Prismatic Pulse",
                },
            },
        ]
    )
    bindings = {
        "source_actor": "prism-beast",
        "targets": ["hero-1"],
        "save_dc": 14,
        "damage": "3d8",
    }
    bound = bind_resolution_plan(
        plan,
        bindings,
        agent_ruling=_agent_ruling(),
    )
    assert bound.steps[-2]["args"]["exclude"] == ["fear"]
    assert bound.steps[-1]["args"]["duration"] == {"kind": "source_turn_start"}

    invalid_duration = deepcopy(plan)
    invalid_duration["steps"][-1]["args"]["duration"] = {"kind": "source_turn_end"}
    with pytest.raises(
        ResolutionPlanBindingError,
        match="duration kind is unsupported",
    ):
        bind_resolution_plan(
            invalid_duration,
            bindings,
            agent_ruling=_agent_ruling(),
        )

    excluded_all = deepcopy(plan)
    excluded_all["steps"][-2]["args"]["exclude"] = ["dazing", "fear"]
    with pytest.raises(
        ResolutionPlanBindingError,
        match="cannot remove every",
    ):
        bind_resolution_plan(
            excluded_all,
            bindings,
            agent_ruling=_agent_ruling(),
        )
