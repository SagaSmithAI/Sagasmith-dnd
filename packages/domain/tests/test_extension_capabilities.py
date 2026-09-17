from copy import deepcopy
from types import SimpleNamespace

import pytest

from sagasmith_dnd import rule_providers
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd.edition_policy import edition_policy
from sagasmith_dnd.encounter_primitives import (
    assert_state,
    control_actor,
    select_weighted_value,
    update_actor_links,
    update_counter,
    validate_targets,
    weighted_table,
)
from sagasmith_dnd.primitive_contracts import capability_manifest, require_capabilities
from sagasmith_dnd.rule_engine import RuleCompilationError, compile_mechanics


@pytest.mark.parametrize("requires", [{"future.op": 1}, {"healing.apply": 2},
                                     {"healing.apply": True}, []])
def test_unavailable_capabilities_are_rejected_at_compilation(requires):
    with pytest.raises(RuleCompilationError):
        compile_mechanics([{
            "id": "addon.heal", "event": "rest.after", "requires": requires,
            "operations": [{"op": "hp.heal", "amount": 1}],
            "citations": [{"source": "fixture"}],
        }])


def test_capability_discovery_is_detached_and_marks_special_context():
    manifest = capability_manifest()
    assert manifest["attack.ac_bonus"]["execution_context"] == "paid_attack"
    assert manifest["target.validate"]["execution_context"] == "encounter"
    manifest["target.validate"]["required_fields"].clear()
    assert "target_ids" in capability_manifest()["target.validate"]["required_fields"]
    assert require_capabilities({"healing.apply": 1}) == {"healing.apply": 1}


def test_weighted_selection_consumes_an_explicit_recorded_roll():
    raw = {"table": [{"weight": 2, "value": "a"}, {"weight": 3, "value": "b"}]}
    table = weighted_table(raw)
    assert [select_weighted_value(table, i) for i in range(1, 6)] == ["a", "a", "b", "b", "b"]
    with pytest.raises(CombatEngineError):
        select_weighted_value(table, 6)
    assert weighted_table({**raw, "exclude": ["a"]}) == [{"weight": 3, "value": "b"}]
    with pytest.raises(CombatEngineError):
        weighted_table({**raw, "exclude": ["a", "b"]})


def test_target_validation_is_pure_and_missing_positions_need_a_ruling():
    source = {"position": {"x": 0, "y": 0}, "conditions": []}
    targets = {"b": {"position": {"x": 2, "y": 1}, "conditions": []}}
    args = {"source_actor_id": "a", "target_ids": ["b"], "maximum_range_ft": 10,
            "require_visible": True}
    before = deepcopy((source, targets))
    assert validate_targets(source, targets, args)["targets"][0]["distance_ft"] == 10
    assert (source, targets) == before
    with pytest.raises(CombatEngineError, match="outside"):
        validate_targets(source, targets, {**args, "maximum_range_ft": 5})
    with pytest.raises(NeedsRulingError):
        validate_targets({}, targets, args)
    with pytest.raises(CombatEngineError, match="visible"):
        validate_targets(source, {"b": {"conditions": ["invisible"]}},
                         {k: v for k, v in args.items() if k != "maximum_range_ft"})


def test_counter_and_control_transitions_do_not_mutate_inputs():
    counters = {"addon.counter": 2}
    changed, result = update_counter(counters, "world.counter.adjust",
                                    {"key": "addon.counter", "amount": 7, "maximum": 5})
    assert changed == {"addon.counter": 5}
    assert result == {"key": "addon.counter", "before": 2, "after": 5}
    assert counters == {"addon.counter": 2}
    target = {"controlled_by_actor_id": "a", "control_mode": "dominate"}
    released, _ = control_actor(target, {"target_actor_id": "b", "mode": "release"})
    assert released == {}
    assert target["control_mode"] == "dominate"
    assert assert_state({"subject": 5, "expected": 5, "operator": "equals"}) == {"passed": True}
    with pytest.raises(CombatEngineError):
        assert_state({"subject": 4, "expected": 5, "operator": "equals"})


def test_links_keep_provenance_and_reject_implicit_overwrite():
    args = {"source_actor_id": "a", "target_actor_id": "b", "link_kind": "addon.tether"}
    links, result = update_actor_links([], "actor.link", args, plan_id="addon.plan", step_id="one")
    assert result["plan_id"] == "addon.plan"
    with pytest.raises(CombatEngineError, match="already exists"):
        update_actor_links(links, "actor.link", args, plan_id="other.plan", step_id="one")
    removed, _ = update_actor_links(
        links, "actor.unlink", args, plan_id="addon.plan", step_id="two",
    )
    assert removed == [] and len(links) == 1


def test_installed_providers_are_loaded_once_and_cannot_mutate_snapshots(monkeypatch):
    data = [{"id": "addon.one", "operations": [{"op": "hp.heal", "amount": 1}]}]
    calls = []

    def mechanics():
        calls.append(True)
        return data

    provider = SimpleNamespace(id="addon", pack_id="addon.pack", abi_version=1, mechanics=mechanics)
    entry = SimpleNamespace(name="addon", load=lambda: lambda: provider)
    monkeypatch.setenv("SAGASMITH_DND_RULE_PROVIDER_ALLOWLIST", "addon")
    monkeypatch.setattr(rule_providers, "entry_points", lambda **kwargs: [entry])
    loaded = rule_providers.load_native_rule_providers()["addon"]
    data[0]["operations"][0]["amount"] = 9
    loaded.mechanics()[0]["operations"][0]["amount"] = 8
    assert loaded.mechanics()[0]["operations"][0]["amount"] == 1
    assert len(calls) == 1


def test_edition_rest_strategy_preserves_player_choice_and_separate_limits():
    hit_dice = {"d8": {"max": 4, "value": 0}, "d10": {"max": 4, "value": 0}}
    old, new = edition_policy("2014"), edition_policy("2024")
    with pytest.raises(ValueError, match="player allocation"):
        old.rest.recover_hit_dice(hit_dice)
    assert old.rest.recover_hit_dice(hit_dice, {"d8": 1, "d10": 3}) == {"d8": 1, "d10": 3}
    assert new.rest.recover_hit_dice(hit_dice) == {"d8": 4, "d10": 4}
    assert old.rest.hit_die_healing(-1) == 0
    assert new.rest.hit_die_healing(-1) == 1
    assert old.d20.exhaustion_adjustment(exhaustion=2, kind="attack")["bonus"] == 0
    assert new.d20.exhaustion_adjustment(exhaustion=2, kind="attack")["bonus"] == -4
