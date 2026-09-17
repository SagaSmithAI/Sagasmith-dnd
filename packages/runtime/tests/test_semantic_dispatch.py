import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.primitive_contracts import PLAN_OPS, PRIMITIVES
from sagasmith_dnd.resolution_plan import (
    ResolutionPlanExecutionError,
    bind_resolution_plan,
    execute_resolution_plan,
)
from sagasmith_dnd_runtime.services.semantic_execution import CombatPlanContext, CombatPlanRuntime


def test_every_general_plan_capability_has_an_installed_handler():
    for opcode in PLAN_OPS - {"attack.ac_bonus"}:
        assert callable(getattr(CombatPlanRuntime, PRIMITIVES[opcode].handler, None)), opcode


def make_plan(*, fail=False):
    steps = [{"id": "count", "op": "world.counter.adjust", "args": {
        "key": "addon.counter", "amount": 2,
    }}]
    if fail:
        steps.append({"id": "gate", "op": "state.assert", "args": {
            "subject": {"$result": "count.after"}, "expected": 100, "operator": "equals",
        }})
    return bind_resolution_plan({
        "schema_version": 2, "id": "addon.plan", "source_card_id": "addon.card",
        "source_card_kind": "activity", "trigger": "action", "slots": {}, "steps": steps,
        "requires": {"world.counter.adjust": 1},
        "citations": [{"source": "fixture", "source_ref": {"chunk_id": "one"},
                       "source_excerpt": "Increase the recorded counter by two."}],
    }, {})


def make_runtime(plan):
    return CombatPlanRuntime(CombatPlanContext(
        encounter={"semantic_state": {"counters": {"addon.counter": 1}}},
        runtime_services=None, campaign=SimpleNamespace(revision=1), campaign_id="campaign",
        resolved_branch_id="branch", bound_plan=plan, compiled_plan=plan.compiled,
        save_facts_by_step={},
    ))


def test_registered_dispatch_commits_to_working_state_and_rolls_back_all_steps():
    plan = make_plan()
    runtime = make_runtime(plan)
    result = execute_resolution_plan(plan, runtime)
    assert result.results["count"]["after"] == 3
    assert runtime.context.encounter["semantic_state"]["counters"]["addon.counter"] == 1
    assert runtime.encounter["semantic_state"]["counters"]["addon.counter"] == 3
    failed = make_plan(fail=True)
    runtime = make_runtime(failed)
    with pytest.raises(ResolutionPlanExecutionError):
        execute_resolution_plan(failed, runtime)
    assert runtime.encounter == runtime.context.encounter


@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("dead_target", [False, True])
def test_new_example_extension_composes_existing_capabilities_atomically(edition, dead_target):
    path = Path(__file__).parents[3] / "examples/extensions/field-medic.plan.json"
    plan = bind_resolution_plan(json.loads(path.read_text(encoding="utf-8")),
                                {"source_actor": "medic", "target_actor": "patient"},
                                edition=edition)
    records = {}
    for identifier in ("medic", "patient"):
        sheet = default_character_sheet()
        sheet["edition"] = edition
        sheet["combat"]["hp"].update(value=1, max=10)
        records[identifier] = SimpleNamespace(sheet=sheet)
    records["medic"].sheet["resources"]["example_medical_supplies"] = {
        "label": "Medical supplies", "value": 1, "max": 1, "recovers_on": "none",
        "source_key": "example.field-medic", "slot_level": 0,
    }
    if dead_target:
        records["patient"].sheet["conditions"] = ["dead"]

    class SceneServices:
        def require_campaign_actor(self, campaign_id, identifier):
            return records[identifier]

        def require_encounter_combatant(self, encounter, identifier, *, role):
            return encounter["actors"][identifier]

        def sync_combatant_conditions(self, encounter, identifier, sheet):
            encounter["actors"][identifier]["conditions"] = sheet["conditions"]

        def require_healing_not_prevented(self, encounter, *, target_id):
            pass  # This fixture has no scene-specific prevention; Domain still checks death.

    base = make_runtime(plan).context
    runtime = CombatPlanRuntime(replace(base, runtime_services=SceneServices(), encounter={
        "actors": {
            key: {"conditions": record.sheet["conditions"]} for key, record in records.items()
        },
    }))
    if dead_target:
        with pytest.raises(ResolutionPlanExecutionError):
            execute_resolution_plan(plan, runtime)
        assert runtime.sheets == {}
        assert runtime.encounter == runtime.context.encounter
    else:
        assert execute_resolution_plan(plan, runtime).status == "committed"
        assert runtime.sheets["medic"]["resources"]["example_medical_supplies"]["value"] == 0
        assert runtime.sheets["patient"]["combat"]["hp"]["value"] == 6
    assert records["medic"].sheet["resources"]["example_medical_supplies"]["value"] == 1
