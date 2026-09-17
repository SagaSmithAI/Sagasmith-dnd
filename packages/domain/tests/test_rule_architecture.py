from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.edition_policy import edition_policy
from sagasmith_dnd.rule_engine import RuleCompilationError, apply_rule_event, resolution_context
from sagasmith_dnd.rule_primitives import apply_sheet_primitive
from sagasmith_dnd.rule_registry import compose_mechanics


def mechanic(identifier, amount=1, **fields):
    return {
        "id": identifier, "event": "activity.after",
        "operations": [{"op": "hp.heal", "amount": amount}],
        "citations": [{"source": "local:test"}], **fields,
    }


def context(mechanics, edition="2014", **kwargs):
    return resolution_context({"edition": edition, "fingerprint": "same-lock",
                               "mechanics": mechanics, "lock": []}, **kwargs)


def injured_sheet():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
    return sheet


@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("with_conflict", [False, True])
def test_unrelated_conflict_cannot_change_dependency_settlement(edition, with_conflict):
    rules = [mechanic("first"), mechanic("second", 4, after=["first"], predicates=[
        {"kind": "sheet_equals", "path": "combat.hp.value", "value": 1},
    ])]
    if with_conflict:
        rules.append(mechanic("unrelated", event="turn.end", operations=[{
            "op": "hp.heal", "amount": 2, "conflict": {"key": "heal", "mode": "max"},
        }]))
    result = apply_rule_event(injured_sheet(), "activity.after", context(rules, edition))
    assert result.sheet["combat"]["hp"]["value"] == 5


def test_same_stage_uses_entry_snapshot_and_explicit_dependency_observes_changes():
    rules = [mechanic("first"), mechanic("second", 4, predicates=[
        {"kind": "sheet_equals", "path": "combat.hp.value", "value": 1},
    ])]
    assert apply_rule_event(injured_sheet(), "activity.after", context(rules)).sheet[
        "combat"]["hp"]["value"] == 1
    rules[1]["after"] = ["first"]
    assert apply_rule_event(injured_sheet(), "activity.after", context(rules)).sheet[
        "combat"]["hp"]["value"] == 5


def test_compiled_state_and_facts_own_detached_snapshots():
    raw = [mechanic("heal", predicates=[{"kind": "fact_equals", "key": "x", "value": [1]}])]
    facts = {"x": [1]}
    compiled = context(raw, facts=facts)
    fingerprint = compiled.fingerprint
    raw[0]["operations"][0]["amount"] = 9
    facts["x"].append(2)
    compiled.mechanics[0].operations[0]["amount"] = 8
    compiled.mechanics[0].predicates[0]["value"].append(3)
    compiled.facts["x"].append(4)
    assert compiled.fingerprint == fingerprint
    assert apply_rule_event(injured_sheet(), "activity.after", compiled).sheet[
        "combat"]["hp"]["value"] == 1
    assert context(raw).fingerprint != fingerprint


def test_edition_selection_and_checksum_patch_dependency_resolution():
    rules = [mechanic("old", editions=["2014"]), mechanic("new", editions=["2024"])]
    assert [item.id for item in context(rules, "2024").mechanics] == ["new"]
    patched = mechanic("patched", patch={"target": "old", "expected_checksum": "a" * 64})
    composed = compose_mechanics([patched, mechanic("dependent", after=["old"])], "2014")
    assert composed[1]["after"] == ["patched"]
    with pytest.raises(RuleCompilationError, match="duplicate"):
        context([mechanic("same"), mechanic("same")])
    with pytest.raises(RuleCompilationError, match="checksum-guarded"):
        context([mechanic("replacement", replaces="old")])


@pytest.mark.parametrize("opcode,args", [
    ("hp.heal", {"amount": 3}),
    ("condition.add", {"id": "frightened"}),
    ("effect.add", {"id": "fear", "effect": {"kind": "timed_conditions", "changes": [
        {"path": "conditions", "mode": "add", "value": "frightened"},
    ]}}),
    ("resource.recover", {"path": "resources.test", "amount": 2}),
])
def test_event_and_direct_primitive_share_transitions(opcode, args):
    sheet = injured_sheet()
    sheet["resources"]["test"] = {"value": 0, "max": 3}
    before = deepcopy(sheet)
    event = apply_rule_event(sheet, "activity.after", context([
        mechanic("operation", operations=[{"op": opcode, **args}]),
    ]))
    assert event.sheet == apply_sheet_primitive(sheet, opcode, args)["sheet"]
    assert sheet == before


def test_failed_resource_spend_preserves_input():
    sheet = injured_sheet()
    sheet["resources"]["test"] = {"value": 1, "max": 3}
    before = deepcopy(sheet)
    with pytest.raises(ValueError):
        apply_sheet_primitive(sheet, "resource.spend", {"path": "resources.test", "amount": 2})
    assert sheet == before


def test_effect_identity_cannot_differ_between_frontends():
    args = {"id": "outer", "effect": {"id": "inner"}}
    with pytest.raises(ValueError, match="identity must match"):
        apply_sheet_primitive(injured_sheet(), "effect.add", args)
    with pytest.raises(RuleCompilationError, match="identity must match"):
        context([mechanic("invalid", operations=[{"op": "effect.add", **args}])])


def test_core_edition_policies_preserve_distinct_spell_and_exhaustion_rules():
    prior = [{"payment": "action", "spell_level": 1, "casting_time": "1 action",
              "spent_slot": True}]
    cast = {"payment": "action", "spell_level": 1, "casting_time": "1 action",
            "spent_slot": True}
    edition_policy("2014").validate_spell_turn(prior, **cast)
    with pytest.raises(ValueError, match="one expended"):
        edition_policy("2024").validate_spell_turn(prior, **cast)
    with pytest.raises(ValueError, match="1-action cantrip"):
        edition_policy("2014").validate_spell_turn(prior, **{**cast, "payment": "bonus_action"})
    assert edition_policy("2014").hit_point_maximum(37, 4) == 18
    assert edition_policy("2024").hit_point_maximum(37, 4) == 37
