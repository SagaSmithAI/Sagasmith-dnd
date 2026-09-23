import json

import pytest

from sagasmith_dnd.character_schema import add_effect, default_character_sheet
from sagasmith_dnd.conditions import reconcile_ended_effect_conditions
from sagasmith_dnd.traps import (
    build_poison_needle_condition_effect,
    source_trap_profile,
    transition_trap_state,
    trap_severity,
    validate_source_pit_depth,
)


@pytest.mark.parametrize(
    ("level", "severity", "damage"),
    [
        (1, "setback", "1d10"),
        (5, "deadly", "10d10"),
        (11, "dangerous", "10d10"),
        (20, "deadly", "24d10"),
    ],
)
def test_trap_severity_uses_2014_tables(level, severity, damage):
    assert trap_severity(severity, level)["damage"] == damage


def test_trap_transition_is_source_bound_and_failed_disable_triggers():
    state = transition_trap_state(
        {}, source_ref="module:crypt#traps", trap_id="wire-1", action="detect"
    )
    assert state["traps"]["wire-1"]["detected"] is True
    triggered = transition_trap_state(
        state,
        source_ref="module:crypt#traps",
        trap_id="wire-1",
        action="disable",
        success=False,
    )
    assert triggered["traps"]["wire-1"]["status"] == "triggered"
    with pytest.raises(ValueError, match="different source"):
        transition_trap_state(
            triggered, source_ref="module:other", trap_id="wire-1", action="settle"
        )


def test_trigger_settle_is_one_shot():
    triggered = transition_trap_state(
        {}, source_ref="module:crypt#traps", trap_id="pit", action="trigger"
    )
    spent = transition_trap_state(
        triggered, source_ref="module:crypt#traps", trap_id="pit", action="settle"
    )
    assert spent["traps"]["pit"]["status"] == "spent"
    with pytest.raises(ValueError, match="armed trap"):
        transition_trap_state(
            spent, source_ref="module:crypt#traps", trap_id="pit", action="trigger"
        )


@pytest.mark.parametrize(
    "profile_id",
    [
        "srd5.1.collapsing_roof",
        "srd5.1.falling_net",
        "srd5.1.fire_breathing_statue",
        "srd5.1.simple_pit",
        "srd5.1.hidden_pit",
        "srd5.1.locking_pit",
        "srd5.1.spiked_simple_pit",
        "srd5.1.spiked_hidden_pit",
        "srd5.1.spiked_locking_pit",
        "srd5.1.poisoned_spiked_simple_pit",
        "srd5.1.poisoned_spiked_hidden_pit",
        "srd5.1.poisoned_spiked_locking_pit",
        "srd5.1.poison_darts",
        "srd5.1.poison_needle",
        "srd5.1.rolling_sphere",
        "srd5.1.sphere_of_annihilation",
    ],
)
def test_fixed_srd_trap_profiles_are_bound_to_exact_source_marker(profile_id):
    marker = "trap_profile: " + json.dumps(
        {"profile_id": profile_id}, sort_keys=True, separators=(",", ":")
    )
    resolved = source_trap_profile({"profile_id": profile_id}, marker)
    assert resolved["profile_id"] == profile_id
    assert resolved["name"]
    resolved["detect"]["active"][0]["dc"] = 1
    assert source_trap_profile({"profile_id": profile_id}, marker)["detect"]["active"][0]["dc"] != 1


def test_fixed_profile_parser_rejects_caller_rules_and_source_mismatch():
    marker = 'trap_profile: {"profile_id":"srd5.1.poison_needle"}'
    with pytest.raises(ValueError, match="only profile_id"):
        source_trap_profile(
            {"profile_id": "srd5.1.poison_needle", "damage_expression": "99d99"},
            marker,
        )
    with pytest.raises(ValueError, match="marker"):
        source_trap_profile({"profile_id": "srd5.1.poison_needle"}, "needle trap")
    with pytest.raises(ValueError, match="unsupported"):
        source_trap_profile({"profile_id": "homebrew"}, marker)


def test_named_traps_explicitly_mark_incomplete_area_condition_and_complex_settlement():
    profiles = {
        profile_id: source_trap_profile(
            {"profile_id": profile_id},
            "trap_profile: "
            + json.dumps({"profile_id": profile_id}, sort_keys=True, separators=(",", ":")),
        )
        for profile_id in (
            "srd5.1.collapsing_roof",
            "srd5.1.falling_net",
            "srd5.1.fire_breathing_statue",
            "srd5.1.poison_darts",
            "srd5.1.poison_needle",
            "srd5.1.rolling_sphere",
            "srd5.1.sphere_of_annihilation",
        )
    }
    assert profiles["srd5.1.collapsing_roof"]["settlement"] == (
        "supported_confirmed_area_damage_rubble_record_only"
    )
    assert profiles["srd5.1.falling_net"]["trigger"]["object"]["hp"] == 20
    assert profiles["srd5.1.falling_net"]["settlement"] == (
        "supported_single_target_area_confirmed_object_hp_unsupported"
    )
    assert profiles["srd5.1.poison_darts"]["settlement"] == (
        "supported_if_eligible_target_area_confirmed"
    )
    assert profiles["srd5.1.poison_needle"]["settlement"] == (
        "supported_single_target_range_confirmed_timed_condition"
    )
    assert profiles["srd5.1.rolling_sphere"]["settlement"] == "unsupported_complex_movement"
    assert profiles["srd5.1.sphere_of_annihilation"]["settlement"] == (
        "unsupported_complex_magic_and_instant_death"
    )


def test_source_profiles_preserve_disable_failure_trigger_rule():
    for profile_id in ("srd5.1.collapsing_roof", "srd5.1.falling_net"):
        marker = "trap_profile: " + json.dumps(
            {"profile_id": profile_id}, sort_keys=True, separators=(",", ":")
        )
        profile = source_trap_profile({"profile_id": profile_id}, marker)
        assert profile["disable"]["failed_check"] == "trigger"
        assert profile["disable"]["ability"] == "dexterity"
        assert profile["disable"]["dc"] == 15
        assert profile["disable"]["tool"] == "thieves_tools"

    darts_marker = 'trap_profile: {"profile_id":"srd5.1.poison_darts"}'
    assert (
        source_trap_profile({"profile_id": "srd5.1.poison_darts"}, darts_marker)["disable"] is None
    )

    triggered = transition_trap_state(
        {}, source_ref="module:crypt#traps", trap_id="roof", action="disable", success=False
    )
    assert triggered["traps"]["roof"]["status"] == "triggered"


def test_collapsing_roof_source_profile_has_fixed_damage_and_area_effect():
    marker = 'trap_profile: {"profile_id":"srd5.1.collapsing_roof"}'
    profile = source_trap_profile({"profile_id": "srd5.1.collapsing_roof"}, marker)
    assert profile["trigger"] == {
        "kind": "area_save_damage",
        "save_ability": "dexterity",
        "save_dc": 15,
        "damage_expression": "4d10",
        "damage_type": "bludgeoning",
        "half_on_success": True,
        "area": "beneath_unstable_ceiling",
        "effects": ["rubble_difficult_terrain"],
    }
    assert profile["settlement"] == "supported_confirmed_area_damage_rubble_record_only"


def test_falling_net_escape_removes_only_the_bound_actor():
    triggered = transition_trap_state(
        {
            "traps": {
                "net": {
                    "source_ref": "module:crypt#net",
                    "status": "triggered",
                    "restrained_actor_ids": ["scout", "guard"],
                }
            }
        },
        source_ref="module:crypt#net",
        trap_id="net",
        action="escape",
        actor_id="scout",
    )
    assert triggered["traps"]["net"]["restrained_actor_ids"] == ["guard"]
    with pytest.raises(ValueError, match="not restrained"):
        transition_trap_state(
            triggered,
            source_ref="module:crypt#net",
            trap_id="net",
            action="escape",
            actor_id="bystander",
        )


def test_falling_net_object_destruction_releases_its_bound_actors_only():
    state = {
        "traps": {
            "net": {
                "source_ref": "module:crypt#net",
                "profile_id": "srd5.1.falling_net",
                "object_id": "net",
                "status": "triggered",
                "restrained_actor_ids": ["scout", "guard"],
                "trap_added_restrained_actor_ids": ["scout"],
            }
        }
    }
    destroyed = transition_trap_state(
        state,
        source_ref="module:crypt#net",
        trap_id="net",
        action="destroy_object",
        destroyed_object_id="net",
        destroyed_hit_points=0,
    )
    net = destroyed["traps"]["net"]
    assert net["status"] == "spent"
    assert net["restrained_actor_ids"] == []
    assert net["trap_added_restrained_actor_ids"] == []
    assert net["released_actor_ids"] == ["scout", "guard"]
    assert net["object_destroyed"] is True
    assert net["object_hit_points"] == 0
    assert state["traps"]["net"]["restrained_actor_ids"] == ["scout", "guard"]
    with pytest.raises(ValueError, match="different source"):
        transition_trap_state(
            state,
            source_ref="module:other#net",
            trap_id="net",
            action="destroy_object",
            destroyed_object_id="net",
            destroyed_hit_points=0,
        )
    with pytest.raises(ValueError, match="zero authoritative"):
        transition_trap_state(
            state,
            source_ref="module:crypt#net",
            trap_id="net",
            action="destroy_object",
            destroyed_object_id="net",
            destroyed_hit_points=1,
        )


def test_poison_needle_profile_and_owned_condition_are_fixed_and_bounded():
    marker = 'trap_profile: {"profile_id":"srd5.1.poison_needle"}'
    profile = source_trap_profile({"profile_id": "srd5.1.poison_needle"}, marker)
    assert profile["trigger"] == {
        "kind": "single_target_damage_condition",
        "range_in": 3,
        "piercing_damage": 1,
        "piercing_type": "piercing",
        "poison_expression": "2d10",
        "poison_type": "poison",
        "save_ability": "constitution",
        "save_dc": 15,
        "poison_half_on_success": False,
        "condition_on_failure": "poisoned",
        "condition_duration_hours": 1,
    }
    effect = build_poison_needle_condition_effect(
        profile_id=profile["profile_id"],
        source_ref="module:chest#needle",
        trap_id="needle-1",
        target_actor_id="actor-1",
    )
    assert effect["kind"] == "poison"
    assert effect["duration"] == {"period": "hour", "remaining": 1}
    assert effect["changes"] == [{"path": "conditions", "mode": "add", "value": ["poisoned"]}]
    assert effect["metadata"]["trap_state"] == {
        "profile_id": "srd5.1.poison_needle",
        "trap_id": "needle-1",
        "source_ref": "module:chest#needle",
        "target_actor_id": "actor-1",
    }
    assert (
        build_poison_needle_condition_effect(
            profile_id=profile["profile_id"],
            source_ref="module:chest#needle",
            trap_id="needle-1",
            target_actor_id="actor-1",
        )["id"]
        == effect["id"]
    )
    canonical_source = json.dumps(
        {"module_id": "m" * 1200, "chunk_id": "c" * 1200, "content_digest": "a" * 128},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(canonical_source) > 300
    assert build_poison_needle_condition_effect(
        profile_id=profile["profile_id"],
        source_ref=canonical_source,
        trap_id="needle-1",
        target_actor_id="actor-1",
    )["metadata"]["trap_state"]["source_ref"] == canonical_source
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet, _ = add_effect(sheet, effect)
    other_source = {
        "id": "other-poison-source",
        "name": "Other source",
        "kind": "timed_conditions",
        "source": "scene:other-source",
        "active": True,
        "concentration": False,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [{"path": "conditions", "mode": "add", "value": ["poisoned"]}],
        "description": "Independent condition source.",
        "metadata": {},
    }
    sheet, _ = add_effect(sheet, other_source)
    ended_effect = next(item for item in sheet["effects"] if item["id"] == effect["id"])
    ended_effect["active"] = False
    reconcile_ended_effect_conditions(sheet, ended_effects=[ended_effect])
    assert "poisoned" in sheet["conditions"]
    with pytest.raises(ValueError, match="only for Poison Needle"):
        build_poison_needle_condition_effect(
            profile_id="srd5.1.poison_darts",
            source_ref="module:chest#needle",
            trap_id="needle-1",
            target_actor_id="actor-1",
        )


def test_poison_needle_effect_expiry_removes_only_its_condition_source():
    needle = build_poison_needle_condition_effect(
        profile_id="srd5.1.poison_needle",
        source_ref="module:chest#needle",
        trap_id="needle-1",
        target_actor_id="actor-1",
    )
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet, _ = add_effect(sheet, needle)
    independent = {
        "id": "independent-poisoned",
        "name": "Independent source",
        "kind": "timed_conditions",
        "source": "scene:independent",
        "active": True,
        "concentration": False,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [{"path": "conditions", "mode": "add", "value": ["poisoned"]}],
        "description": "Independent condition source.",
        "metadata": {},
    }
    sheet, _ = add_effect(sheet, independent)
    ended_needle = next(item for item in sheet["effects"] if item["id"] == needle["id"])
    ended_needle["active"] = False
    reconcile_ended_effect_conditions(sheet, ended_effects=[ended_needle])
    assert "poisoned" in sheet["conditions"]

    ended_independent = next(item for item in sheet["effects"] if item["id"] == independent["id"])
    ended_independent["active"] = False
    reconcile_ended_effect_conditions(sheet, ended_effects=[ended_independent])
    assert "poisoned" not in sheet["conditions"]


def test_source_pit_depth_is_scene_fact_bounded_by_the_selected_profile():
    def profile(profile_id):
        marker = "trap_profile: " + json.dumps(
            {"profile_id": profile_id}, sort_keys=True, separators=(",", ":")
        )
        return source_trap_profile({"profile_id": profile_id}, marker)

    assert validate_source_pit_depth(profile("srd5.1.simple_pit"), 20) == 20
    assert validate_source_pit_depth(profile("srd5.1.simple_pit"), 200) == 200
    assert validate_source_pit_depth(profile("srd5.1.simple_pit"), 205) == 205
    assert validate_source_pit_depth(profile("srd5.1.hidden_pit"), 10) == 10
    assert validate_source_pit_depth(profile("srd5.1.spiked_hidden_pit"), 20) == 20
    with pytest.raises(ValueError, match="outside the fixed source profile"):
        validate_source_pit_depth(profile("srd5.1.hidden_pit"), 15)
    with pytest.raises(ValueError, match="positive integer"):
        validate_source_pit_depth(profile("srd5.1.simple_pit"), 0)
    with pytest.raises(ValueError, match="positive integer"):
        validate_source_pit_depth(profile("srd5.1.simple_pit"), True)
    assert validate_source_pit_depth(profile("srd5.1.locking_pit"), 10) == 10
    assert validate_source_pit_depth(profile("srd5.1.spiked_locking_pit"), 20) == 20
    assert (
        validate_source_pit_depth(profile("srd5.1.poisoned_spiked_locking_pit"), 10) == 10
    )
    with pytest.raises(ValueError, match="outside the fixed source profile"):
        validate_source_pit_depth(profile("srd5.1.locking_pit"), 15)
