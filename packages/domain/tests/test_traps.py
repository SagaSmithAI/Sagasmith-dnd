import json

import pytest

from sagasmith_dnd.character_schema import add_effect, default_character_sheet
from sagasmith_dnd.conditions import reconcile_ended_effect_conditions
from sagasmith_dnd.traps import (
    apply_locking_pit_spring_disable,
    build_poison_needle_condition_effect,
    falling_net_section_cut_qualifies,
    record_sphere_annihilation_contact,
    rolling_sphere_actor_route_contact,
    rolling_sphere_actor_route_entry_indices,
    rolling_sphere_entered_actor_ids,
    rolling_sphere_reduce_speed,
    rolling_sphere_turn_path,
    rolling_sphere_within_five_feet,
    settle_trap_object_spell,
    source_trap_object_facts,
    source_trap_profile,
    transition_trap_state,
    trap_severity,
    validate_falling_net_section_spatial_facts,
    validate_locking_pit_disable_scene_facts,
    validate_locking_pit_disable_state,
    validate_poison_needle_spatial_facts,
    validate_rolling_sphere_spatial_facts,
    validate_rolling_sphere_trigger_fact,
    validate_source_pit_depth,
    validate_source_trap_area_spatial_facts,
    validate_sphere_annihilation_contact_facts,
)


def test_sphere_contact_facts_bind_actor_revisions_and_exact_dm_decision():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.sphere_of_annihilation"},
        'trap_profile: {"profile_id":"srd5.1.sphere_of_annihilation"}',
    )
    facts = {
        "decision_id": "sphere-contact-1",
        "reason": "The target fully entered the stone mouth.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "target_actor_id": "actor-1",
        "target_actor_revision": 7,
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 12,
        "reviewed_by": "principal:dm",
        "enters_mouth": True,
    }
    accepted = validate_sphere_annihilation_contact_facts(
        profile,
        facts,
        scene_id="scene-1",
        trap_id="sphere-1",
        source_ref="module:crypt#sphere",
        campaign_revision=12,
        reviewed_by="principal:dm",
        target_actor_id="actor-1",
        target_actor_revision=7,
    )
    state = record_sphere_annihilation_contact(
        {}, source_ref="module:crypt#sphere", trap_id="sphere-1", contact=accepted
    )
    assert state["traps"]["sphere-1"]["profile_id"] == "srd5.1.sphere_of_annihilation"
    assert state["traps"]["sphere-1"]["status"] == "armed"
    assert state["traps"]["sphere-1"]["sphere_object_removed"] is False
    assert state["traps"]["sphere-1"]["annihilation_contacts"] == [facts]

    with pytest.raises(ValueError, match="target_actor_revision"):
        validate_sphere_annihilation_contact_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="sphere-1",
            source_ref="module:crypt#sphere",
            campaign_revision=12,
            reviewed_by="principal:dm",
            target_actor_id="actor-1",
            target_actor_revision=8,
        )
    false_contact = {**facts, "enters_mouth": False}
    with pytest.raises(ValueError, match="entry into the stone mouth"):
        validate_sphere_annihilation_contact_facts(
            profile,
            false_contact,
            scene_id="scene-1",
            trap_id="sphere-1",
            source_ref="module:crypt#sphere",
            campaign_revision=12,
            reviewed_by="principal:dm",
            target_actor_id="actor-1",
            target_actor_revision=7,
        )


def test_sphere_object_contact_facts_bind_target_source_and_current_revisions():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.sphere_of_annihilation"},
        'trap_profile: {"profile_id":"srd5.1.sphere_of_annihilation"}',
    )
    facts = {
        "decision_id": "sphere-object-contact",
        "reason": "The object completely entered the stone mouth.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "target_scene_object_id": "relic-1",
        "target_scene_object_source_ref": "module:crypt#relic",
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 12,
        "reviewed_by": "principal:dm",
        "enters_mouth": True,
    }
    result = validate_sphere_annihilation_contact_facts(
        profile,
        facts,
        scene_id="scene-1",
        trap_id="sphere-1",
        source_ref="module:crypt#sphere",
        campaign_revision=12,
        reviewed_by="principal:dm",
        target_scene_object_id="relic-1",
        target_scene_object_source_ref="module:crypt#relic",
    )
    with pytest.raises(ValueError, match="campaign_revision"):
        validate_sphere_annihilation_contact_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="sphere-1",
            source_ref="module:crypt#sphere",
            campaign_revision=13,
            reviewed_by="principal:dm",
            target_scene_object_id="relic-1",
            target_scene_object_source_ref="module:crypt#relic",
        )
    with pytest.raises(ValueError, match="authorized DM"):
        validate_sphere_annihilation_contact_facts(
            profile,
            result,
            scene_id="scene-1",
            trap_id="sphere-1",
            source_ref="module:crypt#sphere",
            campaign_revision=12,
            reviewed_by="principal:player",
            target_scene_object_id="relic-1",
            target_scene_object_source_ref="module:crypt#relic",
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


def test_pressure_plate_wedge_prevents_a_bypassed_trap_from_later_triggering():
    source_ref = "module:crypt#statue"
    bypassed = transition_trap_state(
        {},
        source_ref=source_ref,
        trap_id="statue-1",
        action="bypass",
        success=True,
    )
    bypassed["traps"]["statue-1"]["bypass_method"] = "wedge_pressure_plate"

    assert bypassed["traps"]["statue-1"]["bypassed"] is True
    assert bypassed["traps"]["statue-1"]["status"] == "armed"
    with pytest.raises(ValueError, match="pressure-plate wedge prevents"):
        transition_trap_state(
            bypassed,
            source_ref=source_ref,
            trap_id="statue-1",
            action="trigger",
        )
    assert bypassed["traps"]["statue-1"]["status"] == "armed"


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


@pytest.mark.parametrize(
    "profile_id",
    (
        "srd5.1.locking_pit",
        "srd5.1.spiked_locking_pit",
        "srd5.1.poisoned_spiked_locking_pit",
    ),
)
@pytest.mark.parametrize(
    "fact_name",
    ("inside_pit", "mechanism_reachable", "can_see"),
)
def test_locking_pit_disable_facts_are_exact_and_bound_to_the_scene_trap_and_actor(
    profile_id, fact_name
):
    marker = "trap_profile: " + json.dumps(
        {"profile_id": profile_id}, sort_keys=True, separators=(",", ":")
    )
    profile = source_trap_profile({"profile_id": profile_id}, marker)
    facts = {
        "inside_pit": True,
        "mechanism_reachable": True,
        "can_see": True,
        "scene_id": "scene-1",
        "trap_id": "pit-1",
        "actor_id": "captive-1",
        "decision_id": "dm-ruling-1",
        "reason": "The captive can reach and see the spring mechanism.",
    }

    assert (
        validate_locking_pit_disable_scene_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="pit-1",
            actor_id="captive-1",
        )
        == facts
    )
    with pytest.raises(ValueError, match="source-defined scene"):
        validate_locking_pit_disable_scene_facts(
            profile,
            {**facts, "scene_id": "other-scene"},
            scene_id="scene-1",
            trap_id="pit-1",
            actor_id="captive-1",
        )
    with pytest.raises(ValueError, match="to be true"):
        validate_locking_pit_disable_scene_facts(
            profile,
            {**facts, fact_name: False},
            scene_id="scene-1",
            trap_id="pit-1",
            actor_id="captive-1",
        )


def test_locking_pit_spring_disable_records_check_without_releasing_captives():
    state = {
        "traps": {
            "pit-1": {
                "profile_id": "srd5.1.locking_pit",
                "source_ref": "module:crypt#pit",
                "scene_id": "scene-1",
                "status": "triggered",
                "contained_actor_ids": ["captive-1", "captive-2"],
            }
        }
    }

    failed = apply_locking_pit_spring_disable(
        state,
        profile_id="srd5.1.locking_pit",
        source_ref="module:crypt#pit",
        scene_id="scene-1",
        trap_id="pit-1",
        actor_id="captive-1",
        success=False,
    )
    assert failed["traps"]["pit-1"]["spring_disabled"] is False
    assert failed["traps"]["pit-1"]["status"] == "triggered"
    assert failed["traps"]["pit-1"]["contained_actor_ids"] == ["captive-1", "captive-2"]

    succeeded = apply_locking_pit_spring_disable(
        state,
        profile_id="srd5.1.locking_pit",
        source_ref="module:crypt#pit",
        scene_id="scene-1",
        trap_id="pit-1",
        actor_id="captive-1",
        success=True,
    )
    assert succeeded["traps"]["pit-1"]["spring_disabled"] is True
    assert succeeded["traps"]["pit-1"]["status"] == "triggered"
    assert succeeded["traps"]["pit-1"]["contained_actor_ids"] == ["captive-1", "captive-2"]
    assert state["traps"]["pit-1"].get("spring_disabled") is None

    with pytest.raises(ValueError, match="not contained"):
        validate_locking_pit_disable_state(
            state,
            profile_id="srd5.1.locking_pit",
            source_ref="module:crypt#pit",
            scene_id="scene-1",
            trap_id="pit-1",
            actor_id="bystander",
        )
    with pytest.raises(ValueError, match="different source"):
        validate_locking_pit_disable_state(
            state,
            profile_id="srd5.1.locking_pit",
            source_ref="module:other#pit",
            scene_id="scene-1",
            trap_id="pit-1",
            actor_id="captive-1",
        )


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
        "supported_area_damage_and_agent_rubble_movement"
    )
    assert profiles["srd5.1.falling_net"]["trigger"]["object"]["hp"] == 20
    assert profiles["srd5.1.falling_net"]["settlement"] == (
        "supported_multiple_targets_and_object_hp"
    )
    assert profiles["srd5.1.poison_darts"]["settlement"] == (
        "supported_if_eligible_target_area_confirmed"
    )
    assert profiles["srd5.1.poison_needle"]["settlement"] == (
        "supported_single_target_range_confirmed_timed_condition"
    )
    assert profiles["srd5.1.rolling_sphere"]["settlement"] == (
        "supported_source_bound_initiative_path_contact"
    )
    assert profiles["srd5.1.sphere_of_annihilation"]["settlement"] == (
        "unsupported_complex_magic_and_instant_death"
    )


def test_fire_statue_and_sphere_profiles_keep_source_detection_and_spell_effects_bounded():
    fire_marker = 'trap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
    fire = source_trap_profile({"profile_id": "srd5.1.fire_breathing_statue"}, fire_marker)
    assert fire["detect"]["active"] == [
        {"ability": "perception", "dc": 15},
        {"ability": "arcana", "dc": 15},
    ]
    assert fire["detect"]["reveals_on_success"] == [
        "hidden_pressure_plate",
        "faint_scorch_marks_on_floor_and_walls",
    ]
    assert fire["detect"]["reveals_on_success_by_ability"] == {"arcana": ["magic_trap"]}
    assert fire["disable"] == {"ability": "arcana", "dc": 15}
    assert fire["magic_detection"] == {
        "effect": "detect_magic_or_equivalent",
        "target": "statue",
        "reveals_school": "evocation",
    }
    assert fire["spell_disable"] == {
        "spell": "dispel_magic",
        "dc": 13,
        "target": "statue",
        "effect": "destroy_trap",
    }

    sphere_marker = 'trap_profile: {"profile_id":"srd5.1.sphere_of_annihilation"}'
    sphere = source_trap_profile({"profile_id": "srd5.1.sphere_of_annihilation"}, sphere_marker)
    assert sphere["detect"]["active"] == [{"ability": "arcana", "dc": 20}]
    assert sphere["detect"]["reveals_on_success"] == [
        "sphere_of_annihilation_in_stone_mouth",
        "sphere_cannot_be_controlled_or_moved",
    ]
    assert sphere["trigger"]["optional_sympathy"] == {
        "dispel_magic_dc": 18,
        "target": "face_enchantment",
        "effect": "remove_enchantment_only",
    }


def test_falling_net_profile_matches_area_restrain_and_strength_save_rules():
    marker = 'trap_profile: {"profile_id":"srd5.1.falling_net"}'
    profile = source_trap_profile({"profile_id": "srd5.1.falling_net"}, marker)
    assert profile["trigger"] == {
        "kind": "area_condition_and_save",
        "area": "10_foot_square",
        "condition_on_trigger": "restrained",
        "save_ability": "strength",
        "save_dc": 10,
        "condition_on_failure": "prone",
        "escape_check": {"ability": "strength", "dc": 10},
        "object": {
            "kind": "net",
            "ac": 10,
            "hp": 20,
            "slashing_damage_to_destroy_section": 5,
        },
    }
    assert source_trap_object_facts(profile) == {
        "armor_class": 10,
        "hit_points": 20,
        "damage_filter": {"allowed_damage_types": ["slashing"]},
        "slashing_damage_to_destroy_section": 5,
    }
    with pytest.raises(ValueError, match="armor class is fixed"):
        source_trap_object_facts(profile, {"armor_class": 1})
    with pytest.raises(ValueError, match="hit points are fixed"):
        source_trap_object_facts(profile, {"hit_points": 200})
    with pytest.raises(ValueError, match="fixed by its source to slashing"):
        source_trap_object_facts(profile, {"damage_filter": {"allowed_damage_types": []}})


def test_falling_net_section_facts_are_complete_source_and_revision_bound():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.falling_net"},
        'trap_profile: {"profile_id":"srd5.1.falling_net"}',
    )
    facts = {
        "decision_id": "net-sections-1",
        "reason": "The DM reviewed the trapped actors against the current net squares.",
        "scene_id": "crypt",
        "scene_revision": 4,
        "trap_id": "net-1",
        "object_id": "net-1",
        "source_ref": "module:crypt#net",
        "campaign_revision": 9,
        "reviewed_by": "dm:owner",
        "target_section_id": "northwest",
        "actor_sections": [
            {"actor_id": "scout", "section_id": "northwest"},
            {"actor_id": "guard", "section_id": "southeast"},
        ],
    }
    validated = validate_falling_net_section_spatial_facts(
        profile,
        facts,
        scene_id="crypt",
        scene_revision=4,
        trap_id="net-1",
        object_id="net-1",
        source_ref="module:crypt#net",
        campaign_revision=9,
        reviewed_by="dm:owner",
        restrained_actor_ids=["scout", "guard"],
    )
    assert validated["affected_actor_ids"] == ["scout"]
    with pytest.raises(ValueError, match="cover every currently restrained actor"):
        validate_falling_net_section_spatial_facts(
            profile,
            {
                **facts,
                "actor_sections": [
                    {"actor_id": "scout", "section_id": "northwest"},
                    {"actor_id": "outsider", "section_id": "southeast"},
                ],
            },
            scene_id="crypt",
            scene_revision=4,
            trap_id="net-1",
            object_id="net-1",
            source_ref="module:crypt#net",
            campaign_revision=9,
            reviewed_by="dm:owner",
            restrained_actor_ids=["scout", "guard"],
        )
    with pytest.raises(ValueError, match="stale for the current campaign revision"):
        validate_falling_net_section_spatial_facts(
            profile,
            {**facts, "campaign_revision": 8},
            scene_id="crypt",
            scene_revision=4,
            trap_id="net-1",
            object_id="net-1",
            source_ref="module:crypt#net",
            campaign_revision=9,
            reviewed_by="dm:owner",
            restrained_actor_ids=["scout", "guard"],
        )


def test_falling_net_section_cut_requires_a_hit_and_five_slashing_damage():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.falling_net"},
        'trap_profile: {"profile_id":"srd5.1.falling_net"}',
    )
    assert falling_net_section_cut_qualifies(
        profile,
        {"hit": True},
        {"parts": [{"damage_type": "slashing", "adjusted_amount": 5}]},
    )
    assert not falling_net_section_cut_qualifies(
        profile,
        {"hit": True},
        {"parts": [{"damage_type": "slashing", "adjusted_amount": 4}]},
    )
    assert not falling_net_section_cut_qualifies(
        profile,
        {"hit": True},
        {"parts": [{"damage_type": "bludgeoning", "adjusted_amount": 10}]},
    )
    assert not falling_net_section_cut_qualifies(
        profile,
        {"hit": False},
        {"parts": [{"damage_type": "slashing", "adjusted_amount": 10}]},
    )


def test_rolling_sphere_profile_preserves_bundled_initiative_movement_and_contact_rules():
    marker = 'trap_profile: {"profile_id":"srd5.1.rolling_sphere"}'
    profile = source_trap_profile({"profile_id": "srd5.1.rolling_sphere"}, marker)

    assert profile["detect"]["active"] == [
        {"ability": "perception", "dc": 15},
        {"ability": "investigation", "dc": 15},
    ]
    assert profile["detect"]["passive_dc"] == 15
    assert profile["bypass_methods"] == ["wedge_pressure_plate"]
    assert profile["trigger"] == {
        "kind": "complex_trap",
        "pressure_plate_minimum_weight_lb": 20,
        "sphere_diameter_ft": 10,
        "initiative_bonus": 8,
        "movement_speed_ft": 60,
        "movement_path": "straight_line",
        "moves_through_creature_spaces": True,
        "creature_can_move_through_sphere_space": True,
        "traversal_terrain": "difficult",
        "stops_at": "wall_or_similar_barrier",
        "can_turn_corners": False,
        "dexterity_save_dc": 15,
        "damage_expression": "10d10",
        "damage_type": "bludgeoning",
        "on_failure": ["prone"],
        "slow_check": {"ability": "strength", "dc": 20, "speed_reduction_ft": 15},
    }


def test_rolling_sphere_pressure_trigger_fact_is_source_bound_and_inclusive_at_twenty_pounds():
    marker = 'trap_profile: {"profile_id":"srd5.1.rolling_sphere"}'
    profile = source_trap_profile({"profile_id": "srd5.1.rolling_sphere"}, marker)
    fact = {
        "kind": "pressure_plate_weight",
        "scene_id": "scene-1",
        "plate_id": "sphere-plate-1",
        "weight_lb": 20,
    }

    assert (
        validate_rolling_sphere_trigger_fact(
            profile,
            fact,
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )
        == fact
    )
    with pytest.raises(ValueError, match="20 lb or greater"):
        validate_rolling_sphere_trigger_fact(
            profile,
            {**fact, "weight_lb": 19.9},
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )
    with pytest.raises(ValueError, match="source-defined scene"):
        validate_rolling_sphere_trigger_fact(
            profile,
            {**fact, "scene_id": "other-scene"},
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )
    with pytest.raises(ValueError, match="pressure plate"):
        validate_rolling_sphere_trigger_fact(
            profile,
            {**fact, "plate_id": "other-plate"},
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )


def test_rolling_sphere_spatial_facts_bind_review_to_current_grid_scene_and_initiative():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.rolling_sphere"},
        'trap_profile: {"profile_id":"srd5.1.rolling_sphere"}',
    )
    battle_map = {
        "id": "map-1",
        "map_revision": 3,
        "checksum": "a" * 64,
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": 12, "height_cells": 6},
        "blocked_cells": ["5,0", "5,1"],
    }
    combatants = [
        {"actor_id": "actor-1", "initiative": 20, "position": {"x": 2, "y": 2}},
        {"actor_id": "actor-2", "initiative": 10, "position": {"x": 8, "y": 2}},
    ]
    facts = {
        "decision_id": "sphere-release-1",
        "reason": "The surveyed corridor runs straight east from the ceiling trapdoor.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "encounter_id": "encounter-1",
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 12,
        "reviewed_by": "system:local",
        "map_id": "map-1",
        "map_revision": 3,
        "map_checksum": "a" * 64,
        "origin": {"x": 2, "y": 2},
        "heading": "east",
    }

    normalized = validate_rolling_sphere_spatial_facts(
        profile,
        facts,
        scene_id="scene-1",
        trap_id="sphere-1",
        encounter_id="encounter-1",
        source_ref="module:crypt#sphere",
        campaign_revision=12,
        reviewed_by="system:local",
        positioning_mode="grid",
        battle_map=battle_map,
        combatants=combatants,
    )
    assert normalized["heading_vector"] == {"x": 1, "y": 0}
    assert normalized["participant_initiatives"] == [
        {"actor_id": "actor-1", "initiative": 20},
        {"actor_id": "actor-2", "initiative": 10},
    ]
    with pytest.raises(ValueError, match="stale or do not match"):
        validate_rolling_sphere_spatial_facts(
            profile,
            {**facts, "campaign_revision": 11},
            scene_id="scene-1",
            trap_id="sphere-1",
            encounter_id="encounter-1",
            source_ref="module:crypt#sphere",
            campaign_revision=12,
            reviewed_by="system:local",
            positioning_mode="grid",
            battle_map=battle_map,
            combatants=combatants,
        )
    with pytest.raises(ValueError, match="authoritative Grid"):
        validate_rolling_sphere_spatial_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="sphere-1",
            encounter_id="encounter-1",
            source_ref="module:crypt#sphere",
            campaign_revision=12,
            reviewed_by="system:local",
            positioning_mode="agent",
            battle_map=battle_map,
            combatants=combatants,
        )


def test_rolling_sphere_straight_route_stops_at_barrier_and_settles_new_entries():
    battle_map = {
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": 12, "height_cells": 6},
        "blocked_cells": ["6,2", "6,3"],
    }
    sphere = {
        "position": {"x": 2, "y": 2},
        "heading_vector": {"x": 1, "y": 0},
        "speed_ft": 60,
    }
    route = rolling_sphere_turn_path(sphere, battle_map)
    assert route["positions"] == [{"x": 3, "y": 2}, {"x": 4, "y": 2}]
    assert route["to"] == {"x": 4, "y": 2}
    assert route["stopped"] is True
    assert route["stop_reason"] == "wall_or_similar_barrier"
    with pytest.raises(ValueError, match="leaves the reviewed map"):
        rolling_sphere_turn_path(
            {**sphere, "position": {"x": 9, "y": 2}},
            {**battle_map, "blocked_cells": []},
        )

    combatants = [
        {"actor_id": "entered", "position": {"x": 4, "y": 2}},
        {"actor_id": "already-inside", "position": {"x": 2, "y": 2}},
    ]
    assert rolling_sphere_entered_actor_ids(
        {"x": 2, "y": 2}, {"x": 3, "y": 2}, combatants, battle_map
    ) == ["entered"]
    actor = {"size": "medium", "position": {"x": 8, "y": 2}}
    assert rolling_sphere_actor_route_contact(
        {"x": 2, "y": 2},
        actor,
        [
            {"x": 8, "y": 2},
            {"x": 7, "y": 2},
            {"x": 6, "y": 2},
            {"x": 5, "y": 2},
            {"x": 4, "y": 2},
            {"x": 3, "y": 2},
        ],
        {**battle_map, "blocked_cells": []},
    )
    assert rolling_sphere_actor_route_entry_indices(
        {"x": 2, "y": 2},
        {"size": "medium", "position": {"x": 4, "y": 2}},
        [
            {"x": 4, "y": 2},
            {"x": 3, "y": 2},
            {"x": 4, "y": 2},
            {"x": 3, "y": 2},
        ],
        {**battle_map, "blocked_cells": []},
    ) == [1, 3]
    assert not rolling_sphere_actor_route_contact(
        {"x": 2, "y": 2},
        actor,
        [{"x": 8, "y": 2}, {"x": 9, "y": 2}],
        {**battle_map, "blocked_cells": []},
    )
    assert rolling_sphere_within_five_feet(
        {"x": 2, "y": 2}, {"position": {"x": 4, "y": 2}}, battle_map
    )
    reduced = rolling_sphere_reduce_speed({"active": True, "speed_ft": 15}, success=True)
    assert reduced["speed_ft"] == 0
    assert reduced["active"] is False


def test_source_trap_area_spatial_facts_bind_every_actor_to_scene_trap_source_and_revision():
    marker = 'trap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
    profile = source_trap_profile({"profile_id": "srd5.1.fire_breathing_statue"}, marker)
    facts = {
        "decision_id": "dm-area-review-1",
        "reason": "Reviewed the statue's cone and the current scene positions.",
        "scene_id": "scene-1",
        "trap_id": "statue-1",
        "encounter_id": "encounter-1",
        "source_ref": "exact-module-chunk-ref",
        "campaign_revision": 12,
        "reviewed_by": "system:local",
        "actor_facts": [
            {"actor_id": "actor-1", "in_area": True},
            {"actor_id": "actor-2", "in_area": False},
        ],
    }

    normalized = validate_source_trap_area_spatial_facts(
        profile,
        facts,
        scene_id="scene-1",
        trap_id="statue-1",
        encounter_id="encounter-1",
        source_ref="exact-module-chunk-ref",
        campaign_revision=12,
        reviewed_by="system:local",
        actor_ids=["actor-1", "actor-2"],
    )
    assert normalized["affected_actor_ids"] == ["actor-1"]
    assert normalized["actor_facts"] == facts["actor_facts"]
    with pytest.raises(ValueError, match="cover every active encounter combatant"):
        validate_source_trap_area_spatial_facts(
            profile,
            {**facts, "actor_facts": facts["actor_facts"][:1]},
            scene_id="scene-1",
            trap_id="statue-1",
            encounter_id="encounter-1",
            source_ref="exact-module-chunk-ref",
            campaign_revision=12,
            reviewed_by="system:local",
            actor_ids=["actor-1", "actor-2"],
        )
    empty_area = validate_source_trap_area_spatial_facts(
        profile,
        {
            **facts,
            "actor_facts": [
                {"actor_id": "actor-1", "in_area": False},
                {"actor_id": "actor-2", "in_area": False},
            ],
        },
        scene_id="scene-1",
        trap_id="statue-1",
        encounter_id="encounter-1",
        source_ref="exact-module-chunk-ref",
        campaign_revision=12,
        reviewed_by="system:local",
        actor_ids=["actor-1", "actor-2"],
    )
    assert empty_area["affected_actor_ids"] == []
    with pytest.raises(ValueError, match="stale"):
        validate_source_trap_area_spatial_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="statue-1",
            encounter_id="encounter-1",
            source_ref="exact-module-chunk-ref",
            campaign_revision=13,
            reviewed_by="system:local",
            actor_ids=["actor-1", "actor-2"],
        )
    with pytest.raises(ValueError, match="active encounter"):
        validate_source_trap_area_spatial_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="statue-1",
            encounter_id="another-encounter",
            source_ref="exact-module-chunk-ref",
            campaign_revision=12,
            reviewed_by="system:local",
            actor_ids=["actor-1", "actor-2"],
        )
    with pytest.raises(ValueError, match="authorized DM principal"):
        validate_source_trap_area_spatial_facts(
            profile,
            facts,
            scene_id="scene-1",
            trap_id="statue-1",
            encounter_id="encounter-1",
            source_ref="exact-module-chunk-ref",
            campaign_revision=12,
            reviewed_by="another-principal",
            actor_ids=["actor-1", "actor-2"],
        )


def test_falling_net_area_spatial_facts_allow_empty_area_for_agent_and_grid():
    marker = 'trap_profile: {"profile_id":"srd5.1.falling_net"}'
    profile = source_trap_profile({"profile_id": "srd5.1.falling_net"}, marker)
    common_facts = {
        "decision_id": "dm-empty-net-review",
        "reason": "Reviewed the fixed net footprint and all encounter positions.",
        "scene_id": "scene-1",
        "trap_id": "net-1",
        "encounter_id": "encounter-1",
        "source_ref": "exact-module-chunk-ref",
        "campaign_revision": 12,
        "reviewed_by": "system:local",
    }
    actor_facts = {
        **common_facts,
        "actor_facts": [
            {"actor_id": "actor-1", "in_area": False},
            {"actor_id": "actor-2", "in_area": False},
        ],
    }
    normalized_agent = validate_source_trap_area_spatial_facts(
        profile,
        actor_facts,
        scene_id="scene-1",
        trap_id="net-1",
        encounter_id="encounter-1",
        source_ref="exact-module-chunk-ref",
        campaign_revision=12,
        reviewed_by="system:local",
        actor_ids=["actor-1", "actor-2"],
    )
    assert normalized_agent["affected_actor_ids"] == []

    grid_facts = {
        **common_facts,
        "grid_area": {
            "map_id": "map-1",
            "map_revision": 4,
            "cells": ["0,0", "0,1", "1,0", "1,1"],
        },
    }
    normalized_grid = validate_source_trap_area_spatial_facts(
        profile,
        grid_facts,
        scene_id="scene-1",
        trap_id="net-1",
        encounter_id="encounter-1",
        source_ref="exact-module-chunk-ref",
        campaign_revision=12,
        reviewed_by="system:local",
        actor_ids=["actor-1", "actor-2"],
        positioning_mode="grid",
        battle_map={
            "id": "map-1",
            "map_revision": 4,
            "grid": {"kind": "square", "cell_ft": 5},
            "bounds": {"width_cells": 8, "height_cells": 8},
        },
        combatants=[
            {"actor_id": "actor-1", "position": {"x": 4, "y": 4}},
            {"actor_id": "actor-2", "position": {"x": 6, "y": 6}},
        ],
    )
    assert normalized_grid["affected_actor_ids"] == []


def test_poison_darts_area_targets_are_sorted_before_runtime_rng_selection():
    marker = 'trap_profile: {"profile_id":"srd5.1.poison_darts"}'
    profile = source_trap_profile({"profile_id": "srd5.1.poison_darts"}, marker)
    facts = {
        "decision_id": "darts-area-review-1",
        "reason": "Reviewed all active combatants against the pressure plate area.",
        "scene_id": "scene-1",
        "trap_id": "darts-1",
        "encounter_id": "encounter-1",
        "source_ref": "exact-module-chunk-ref",
        "campaign_revision": 12,
        "reviewed_by": "system:local",
        "actor_facts": [
            {"actor_id": "actor-z", "in_area": True},
            {"actor_id": "actor-a", "in_area": True},
            {"actor_id": "actor-outside", "in_area": False},
        ],
    }

    normalized = validate_source_trap_area_spatial_facts(
        profile,
        facts,
        scene_id="scene-1",
        trap_id="darts-1",
        encounter_id="encounter-1",
        source_ref="exact-module-chunk-ref",
        campaign_revision=12,
        reviewed_by="system:local",
        actor_ids=["actor-z", "actor-a", "actor-outside"],
    )

    assert normalized["affected_actor_ids"] == ["actor-a", "actor-z"]
    assert normalized["actor_facts"][0]["actor_id"] == "actor-z"


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
    assert profile["detect"]["no_roll"] == [
        {
            "method": "inspect_support_beams",
            "reveals_on_success": ["wedged_support_beams"],
        }
    ]
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
    assert profile["settlement"] == "supported_area_damage_and_agent_rubble_movement"


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


def test_falling_net_section_cut_releases_only_its_owned_restraints_and_accumulates():
    state = {
        "traps": {
            "net": {
                "source_ref": "module:crypt#net",
                "profile_id": "srd5.1.falling_net",
                "object_id": "net",
                "status": "triggered",
                "restrained_actor_ids": ["scout", "guard", "preexisting"],
                "trap_added_restrained_actor_ids": ["scout", "guard"],
            }
        }
    }
    cut = transition_trap_state(
        state,
        source_ref="module:crypt#net",
        trap_id="net",
        action="cut_object_section",
        section_id="northwest",
        released_actor_ids=["scout"],
    )
    net = cut["traps"]["net"]
    assert net["severed_section_ids"] == ["northwest"]
    assert net["restrained_actor_ids"] == ["guard", "preexisting"]
    assert net["trap_added_restrained_actor_ids"] == ["guard"]
    assert net["released_actor_ids"] == ["scout"]
    with pytest.raises(ValueError, match="already destroyed"):
        transition_trap_state(
            cut,
            source_ref="module:crypt#net",
            trap_id="net",
            action="cut_object_section",
            section_id="northwest",
            released_actor_ids=[],
        )
    destroyed = transition_trap_state(
        cut,
        source_ref="module:crypt#net",
        trap_id="net",
        action="destroy_object",
        destroyed_object_id="net",
        destroyed_hit_points=0,
    )
    assert destroyed["traps"]["net"]["released_actor_ids"] == ["scout", "guard", "preexisting"]


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
    assert (
        build_poison_needle_condition_effect(
            profile_id=profile["profile_id"],
            source_ref=canonical_source,
            trap_id="needle-1",
            target_actor_id="actor-1",
        )["metadata"]["trap_state"]["source_ref"]
        == canonical_source
    )
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


def test_poison_needle_spatial_facts_bind_dm_scene_source_actor_and_three_inch_range():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.poison_needle"},
        'trap_profile: {"profile_id":"srd5.1.poison_needle"}',
    )
    facts = {
        "decision_id": "needle-range-1",
        "reason": "DM reviewed the target at the lock.",
        "scene_id": "scene-1",
        "scene_revision": 4,
        "trap_id": "needle-lock-1",
        "target_actor_id": "actor-1",
        "source_ref": "module:chest#needle",
        "campaign_revision": 9,
        "reviewed_by": "principal:dm",
        "distance_inches": 3,
    }
    expected = {
        "scene_id": "scene-1",
        "scene_revision": 4,
        "trap_id": "needle-lock-1",
        "target_actor_id": "actor-1",
        "source_ref": "module:chest#needle",
        "campaign_revision": 9,
        "reviewed_by": "principal:dm",
    }
    normalized = validate_poison_needle_spatial_facts(
        profile,
        facts,
        **expected,
    )
    assert normalized["distance_inches"] == 3.0
    assert normalized["reviewed_by"] == "principal:dm"

    invalid_cases = [
        ({"distance_inches": 3.01}, "3-inch range"),
        ({"campaign_revision": 8}, "stale for the current campaign revision"),
        ({"scene_revision": 3}, "stale for the active scene revision"),
        ({"reviewed_by": "principal:other"}, "authorized DM"),
        ({"target_actor_id": "actor-2"}, "target actor"),
        ({"source_ref": "module:other#needle"}, "exact source"),
        ({"extra": True}, "require decision, scene, trap"),
    ]
    for changes, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            validate_poison_needle_spatial_facts(
                profile,
                {**facts, **changes},
                **expected,
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
    assert validate_source_pit_depth(profile("srd5.1.poisoned_spiked_locking_pit"), 10) == 10
    with pytest.raises(ValueError, match="outside the fixed source profile"):
        validate_source_pit_depth(profile("srd5.1.locking_pit"), 15)


def test_source_trap_spells_reveal_only_aura_and_apply_exact_dispel_effects():
    fire_marker = 'trap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
    fire = source_trap_profile({"profile_id": "srd5.1.fire_breathing_statue"}, fire_marker)
    sphere_marker = 'trap_profile: {"profile_id":"srd5.1.sphere_of_annihilation"}'
    sphere = source_trap_profile({"profile_id": "srd5.1.sphere_of_annihilation"}, sphere_marker)
    base = {
        "source_ref": "module:crypt#traps",
        "scene_id": "crypt-room",
        "trap_id": "statue-1",
        "actor_id": "caster-1",
        "reviewed_by": "principal:dm",
        "campaign_revision": 7,
    }
    aura = settle_trap_object_spell(
        {
            "traps": {
                "statue-1": {
                    "source_ref": base["source_ref"],
                    "scene_id": base["scene_id"],
                    "profile_id": fire["profile_id"],
                    "status": "armed",
                }
            }
        },
        profile=fire,
        action="detect_magic",
        component="statue",
        success=None,
        face_enchantment_present=None,
        **base,
    )
    assert aura["result"] == {
        "kind": "magic_aura",
        "target": "statue",
        "school": "evocation",
    }
    assert aura["trap"]["status"] == "armed"
    assert aura["trap"]["magic_aura_revealed"] == "evocation"
    assert aura["state"]["attempts"][-1]["reviewed_by"] == "principal:dm"

    failed = settle_trap_object_spell(
        aura["state"],
        profile=fire,
        action="dispel_magic",
        component="statue",
        success=False,
        face_enchantment_present=None,
        **base,
    )
    assert failed["trap"]["status"] == "armed"
    assert failed["result"]["effect"] == "no_effect"

    disabled = settle_trap_object_spell(
        failed["state"],
        profile=fire,
        action="dispel_magic",
        component="statue",
        success=True,
        face_enchantment_present=None,
        **base,
    )
    assert disabled["trap"]["status"] == "disabled"

    sphere_result = settle_trap_object_spell(
        {},
        profile=sphere,
        source_ref=base["source_ref"],
        scene_id=base["scene_id"],
        trap_id="sphere-1",
        actor_id=base["actor_id"],
        action="dispel_magic",
        component="face_enchantment",
        success=True,
        face_enchantment_present=True,
        reviewed_by=base["reviewed_by"],
        campaign_revision=base["campaign_revision"],
    )
    assert sphere_result["result"]["effect"] == "remove_enchantment_only"
    assert sphere_result["trap"]["optional_sympathy_active"] is False
    assert sphere_result["trap"]["sphere_object_removed"] is False

    with pytest.raises(ValueError, match="different scene"):
        settle_trap_object_spell(
            disabled["state"],
            profile=fire,
            source_ref=base["source_ref"],
            scene_id="other-room",
            trap_id=base["trap_id"],
            actor_id=base["actor_id"],
            action="detect_magic",
            component="statue",
            success=None,
            face_enchantment_present=None,
            reviewed_by=base["reviewed_by"],
            campaign_revision=8,
        )
