from copy import deepcopy

import pytest

from sagasmith_dnd.resolution_ir import execute_instruction, lower_instruction, resolve_conflicts


def test_mechanic_dependency_order_overrides_priority_and_rejects_cycles():
    from sagasmith_dnd.rule_engine import RuleCompilationError, compile_mechanics
    first = {"id": "first", "event": "rest.after", "priority": 10,
             "operations": [{"op": "hp.heal", "amount": 1}],
             "citations": [{"source": "fixture"}]}
    second = {**first, "id": "second", "priority": -10, "after": ["first"]}
    assert [item.id for item in compile_mechanics([second, first])] == ["first", "second"]
    with pytest.raises(RuleCompilationError, match="cycle"):
        compile_mechanics([second, {**first, "after": ["second"]}])


def instruction(opcode="hp.heal", **arguments):
    return lower_instruction(
        step_id=str(arguments.get("amount", 1)),
        opcode=opcode,
        arguments=arguments,
        source_id="source:fixture",
        citations=[],
    )


def test_both_authoring_forms_lower_to_the_same_immutable_instruction():
    authored = {"amount": 3, "target_ids": ["actor"]}
    first = instruction("hp.heal", **authored)
    second = instruction("healing.apply", **authored)
    assert first.opcode == second.opcode == "healing.apply"
    assert first.receipt() == second.receipt()
    authored["target_ids"].append("mutated")
    assert first.arguments["target_ids"] == ["actor"]
    assert execute_instruction(first, lambda op, args: (op, args)) == (
        "healing.apply",
        {"amount": 3, "target_ids": ["actor"]},
    )


@pytest.mark.parametrize("mode,expected", [("stack", [1, 3]), ("replace", [3]), ("max", [3])])
def test_explicit_conflict_modes(mode, expected):
    values = [
        instruction(amount=value, conflict={"key": "healing", "mode": mode}) for value in (1, 3)
    ]
    assert [item.arguments["amount"] for item in resolve_conflicts(values)] == expected


def test_exclusive_conflict_fails_before_execution():
    values = [
        instruction(amount=value, conflict={"key": "stance", "mode": "exclusive"})
        for value in (1, 3)
    ]
    with pytest.raises(ValueError, match="exclusive"):
        resolve_conflicts(values)


def test_legacy_and_ir_shadow_settlement_preserve_the_same_sheet():
    from sagasmith_dnd.character_schema import default_character_sheet
    from sagasmith_dnd.rule_engine import _apply_sheet_operation

    original = default_character_sheet()
    original["combat"]["hp"].update(value=1, max=10)
    reference = deepcopy(original)
    candidate = deepcopy(original)
    _apply_sheet_operation(reference, {"op": "hp.heal", "amount": 3})
    execute_instruction(
        instruction(amount=3),
        lambda op, args: _apply_sheet_operation(
            candidate,
            {"op": op, **args},
        ),
    )
    assert candidate == reference
    assert original["combat"]["hp"]["value"] == 1
