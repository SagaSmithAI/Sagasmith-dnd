"""Deterministic helpers for source-described 2014 traps.

This module deliberately contains no scene discovery or adjudication. Callers
must bind a trap instance to an exact source reference before applying these
rules to campaign state.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

_SEVERITY = {
    "setback": {"save_dc": (10, 11), "attack_bonus": (3, 5)},
    "dangerous": {"save_dc": (12, 15), "attack_bonus": (6, 8)},
    "deadly": {"save_dc": (16, 20), "attack_bonus": (9, 12)},
}
_DAMAGE = {
    (1, 4): {"setback": "1d10", "dangerous": "2d10", "deadly": "4d10"},
    (5, 10): {"setback": "2d10", "dangerous": "4d10", "deadly": "10d10"},
    (11, 16): {"setback": "4d10", "dangerous": "10d10", "deadly": "18d10"},
    (17, 20): {"setback": "10d10", "dangerous": "18d10", "deadly": "24d10"},
}

# Fixed SRD 5.1 declarations. This registry is the only source of supported
# trap numbers; MCP callers can select an identifier but cannot supply DCs,
# dice, damage types, save outcomes, or effects.
_SRD_TRAPS: dict[str, dict[str, Any]] = {
    "srd5.1.collapsing_roof": {
        "name": "Collapsing Roof",
        "detect": {
            "active": [{"ability": "perception", "dc": 10}],
            "passive_dc": 10,
            "no_roll": [
                {
                    "method": "inspect_support_beams",
                    "reveals_on_success": ["wedged_support_beams"],
                }
            ],
        },
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "no_tool_alternative": {"disadvantage": True, "requires_edged_tool": True},
            "failed_check": "trigger",
        },
        "bypass_methods": ["stay_out_of_collapse_area"],
        "trigger": {
            "kind": "area_save_damage",
            "save_ability": "dexterity",
            "save_dc": 15,
            "damage_expression": "4d10",
            "damage_type": "bludgeoning",
            "half_on_success": True,
            "area": "beneath_unstable_ceiling",
            "effects": ["rubble_difficult_terrain"],
        },
        "settlement": "supported_area_damage_and_agent_rubble_movement",
    },
    "srd5.1.falling_net": {
        "name": "Falling Net",
        "detect": {"active": [{"ability": "perception", "dc": 10}], "passive_dc": 10},
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "no_tool_alternative": {"disadvantage": True, "requires_edged_tool": True},
            "failed_check": "trigger",
        },
        "bypass_methods": [],
        "trigger": {
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
        },
        "settlement": "supported_multiple_targets_and_object_hp",
    },
    "srd5.1.fire_breathing_statue": {
        "name": "Fire-Breathing Statue",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "arcana", "dc": 15},
            ],
            "passive_dc": 15,
            "reveals_on_success": [
                "hidden_pressure_plate",
                "faint_scorch_marks_on_floor_and_walls",
            ],
            "reveals_on_success_by_ability": {"arcana": ["magic_trap"]},
        },
        "disable": {"ability": "arcana", "dc": 15},
        "magic_detection": {
            "effect": "detect_magic_or_equivalent",
            "target": "statue",
            "reveals_school": "evocation",
        },
        "spell_disable": {
            "spell": "dispel_magic",
            "dc": 13,
            "target": "statue",
            "effect": "destroy_trap",
        },
        "bypass_methods": ["wedge_pressure_plate"],
        "trigger": {
            "kind": "area_save_damage",
            "area": "30_foot_cone",
            "save_ability": "dexterity",
            "save_dc": 13,
            "damage_expression": "4d10",
            "damage_type": "fire",
            "half_on_success": True,
            "dispel_magic_dc": 13,
        },
        "settlement": "supported_confirmed_area_save_damage",
    },
    "srd5.1.simple_pit": {
        "name": "Simple Pit",
        "detect": {"active": [{"ability": "perception", "dc": 10}], "passive_dc": 10},
        "disable": None,
        "bypass_methods": ["avoid_pit"],
        "trigger": {"kind": "fall", "depth_ft": "source_defined", "save": None},
        "settlement": "supported_confirmed_source_depth_fall",
    },
    "srd5.1.hidden_pit": {
        "name": "Hidden Pit",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": None,
        "bypass_methods": ["wedge_cover"],
        "trigger": {"kind": "fall", "depth_ft": [10, 20], "save": None},
        "settlement": "supported_confirmed_source_depth_fall",
    },
    "srd5.1.locking_pit": {
        "name": "Locking Pit",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "required_scene": ["inside_pit", "mechanism_reachable", "can_see"],
        },
        "bypass_methods": ["wedge_cover"],
        "trigger": {"kind": "fall_and_contain", "depth_ft": [10, 20], "escape_strength_dc": 20},
        "settlement": "supported_confirmed_depth_fall_and_containment_escape",
    },
    "srd5.1.spiked_simple_pit": {
        "name": "Spiked Simple Pit",
        "detect": {"active": [{"ability": "perception", "dc": 10}], "passive_dc": 10},
        "disable": None,
        "bypass_methods": ["avoid_pit"],
        "trigger": {
            "kind": "fall_plus_damage",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "depth_ft": "source_defined",
        },
        "settlement": "supported_confirmed_source_depth_fall_and_spike_damage",
    },
    "srd5.1.spiked_hidden_pit": {
        "name": "Spiked Hidden Pit",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": None,
        "bypass_methods": ["wedge_cover"],
        "trigger": {
            "kind": "fall_plus_damage",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "depth_ft": [10, 20],
        },
        "settlement": "supported_confirmed_source_depth_fall_and_spike_damage",
    },
    "srd5.1.spiked_locking_pit": {
        "name": "Spiked Locking Pit",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "required_scene": ["inside_pit", "mechanism_reachable", "can_see"],
        },
        "bypass_methods": ["wedge_cover"],
        "trigger": {
            "kind": "fall_plus_damage",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "depth_ft": [10, 20],
            "escape_strength_dc": 20,
        },
        "settlement": "supported_confirmed_depth_fall_spike_damage_and_containment_escape",
    },
    "srd5.1.poisoned_spiked_simple_pit": {
        "name": "Poisoned Spiked Simple Pit",
        "detect": {"active": [{"ability": "perception", "dc": 10}], "passive_dc": 10},
        "disable": None,
        "bypass_methods": ["avoid_pit"],
        "trigger": {
            "kind": "fall_plus_spike_and_poison_save",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "poison_save_ability": "constitution",
            "poison_save_dc": 13,
            "poison_damage_expression": "4d10",
            "poison_damage_type": "poison",
            "poison_half_on_success": True,
            "depth_ft": "source_defined",
        },
        "settlement": "supported_confirmed_source_depth_fall_spikes_and_poison_save",
    },
    "srd5.1.poisoned_spiked_hidden_pit": {
        "name": "Poisoned Spiked Hidden Pit",
        "detect": {
            "active": [{"ability": "perception", "dc": 15}, {"ability": "investigation", "dc": 15}],
            "passive_dc": 15,
        },
        "disable": None,
        "bypass_methods": ["wedge_cover"],
        "trigger": {
            "kind": "fall_plus_spike_and_poison_save",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "poison_save_ability": "constitution",
            "poison_save_dc": 13,
            "poison_damage_expression": "4d10",
            "poison_damage_type": "poison",
            "poison_half_on_success": True,
            "depth_ft": [10, 20],
        },
        "settlement": "supported_confirmed_depth_fall_spikes_and_poison_save",
    },
    "srd5.1.poisoned_spiked_locking_pit": {
        "name": "Poisoned Spiked Locking Pit",
        "detect": {
            "active": [{"ability": "perception", "dc": 15}, {"ability": "investigation", "dc": 15}],
            "passive_dc": 15,
        },
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "required_scene": ["inside_pit", "mechanism_reachable", "can_see"],
        },
        "bypass_methods": ["wedge_cover"],
        "trigger": {
            "kind": "fall_plus_spike_and_poison_save",
            "spike_damage_expression": "2d10",
            "spike_damage_type": "piercing",
            "poison_save_ability": "constitution",
            "poison_save_dc": 13,
            "poison_damage_expression": "4d10",
            "poison_damage_type": "poison",
            "poison_half_on_success": True,
            "depth_ft": [10, 20],
            "escape_strength_dc": 20,
        },
        "settlement": "supported_confirmed_depth_fall_spikes_poison_and_containment_escape",
    },
    "srd5.1.poison_darts": {
        "name": "Poison Darts",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": None,
        "bypass_methods": ["wedge_pressure_plate", "stuff_dart_holes"],
        "trigger": {
            "kind": "multi_target_attack_and_save",
            "dart_count": 4,
            "attack_bonus": 8,
            "target_selection": "random_within_10_feet",
            "piercing_expression": "1d4",
            "piercing_type": "piercing",
            "save_ability": "constitution",
            "save_dc": 15,
            "poison_expression": "2d10",
            "poison_type": "poison",
            "half_on_success": True,
        },
        "settlement": "supported_if_eligible_target_area_confirmed",
    },
    "srd5.1.poison_needle": {
        "name": "Poison Needle",
        "detect": {
            "active": [{"ability": "investigation", "dc": 20}],
            "passive_dc": None,
            "requires_inspect_lock": True,
        },
        "disable": {
            "ability": "dexterity",
            "dc": 15,
            "tool": "thieves_tools",
            "failed_check": "trigger",
        },
        "bypass_methods": ["use_proper_key"],
        "trigger": {
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
        },
        "settlement": "supported_single_target_range_confirmed_timed_condition",
    },
    "srd5.1.rolling_sphere": {
        "name": "Rolling Sphere",
        "detect": {
            "active": [
                {"ability": "perception", "dc": 15},
                {"ability": "investigation", "dc": 15},
            ],
            "passive_dc": 15,
        },
        "disable": None,
        "bypass_methods": ["wedge_pressure_plate"],
        "trigger": {
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
        },
        "settlement": "unsupported_complex_movement",
    },
    "srd5.1.sphere_of_annihilation": {
        "name": "Sphere of Annihilation",
        "detect": {
            "active": [{"ability": "arcana", "dc": 20}],
            "passive_dc": None,
            "reveals_on_success": [
                "sphere_of_annihilation_in_stone_mouth",
                "sphere_cannot_be_controlled_or_moved",
            ],
        },
        "disable": None,
        "bypass_methods": [],
        "trigger": {
            "kind": "complex_magic",
            "contact_effect": "obliterate",
            "optional_sympathy": {
                "dispel_magic_dc": 18,
                "target": "face_enchantment",
                "effect": "remove_enchantment_only",
            },
        },
        "settlement": "unsupported_complex_magic_and_instant_death",
    },
}

_LOCKING_PIT_PROFILES = {
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}
_SOURCE_AREA_TARGET_PROFILES = {
    "srd5.1.collapsing_roof",
    "srd5.1.falling_net",
    "srd5.1.fire_breathing_statue",
    "srd5.1.poison_darts",
    "srd5.1.simple_pit",
    "srd5.1.hidden_pit",
    "srd5.1.spiked_simple_pit",
    "srd5.1.spiked_hidden_pit",
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_simple_pit",
    "srd5.1.poisoned_spiked_hidden_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}


def build_poison_needle_condition_effect(
    *,
    profile_id: Any,
    source_ref: Any,
    trap_id: Any,
    target_actor_id: Any,
) -> dict[str, Any]:
    """Build only the fixed Poison Needle's one-hour, trap-owned condition."""
    if profile_id != "srd5.1.poison_needle":
        raise ValueError("timed trap condition is supported only for Poison Needle")
    source = str(source_ref or "").strip()
    instance_id = str(trap_id or "").strip()
    target_id = str(target_actor_id or "").strip()
    # Runtime passes the verified, canonical module/scene/chunk source identity,
    # which is larger than a short citation URL. Keep the bound generous enough
    # for that stable JSON reference while still limiting persisted effect size.
    if not source or len(source) > 8192:
        raise ValueError("Poison Needle condition requires a bounded exact source reference")
    if not instance_id or len(instance_id) > 200:
        raise ValueError("Poison Needle condition requires a bounded trap id")
    if not target_id or len(target_id) > 100:
        raise ValueError("Poison Needle condition requires a bounded target actor id")
    identity = "\0".join((source, instance_id, target_id)).encode("utf-8")
    effect_id = f"trap-poison-needle:{hashlib.sha256(identity).hexdigest()[:32]}"
    source_label = f"trap-poison-needle:{hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]}"
    return {
        "id": effect_id,
        "name": "Poison Needle",
        "kind": "poison",
        # Character effect source labels are intentionally short. The complete
        # source identity remains in trap_state metadata for audit and cleanup.
        "source": source_label,
        "active": True,
        "concentration": False,
        "duration": {"period": "hour", "remaining": 1},
        "changes": [{"path": "conditions", "mode": "add", "value": ["poisoned"]}],
        "description": "The source-bound Poison Needle leaves its target poisoned for 1 hour.",
        "metadata": {
            "trap_state": {
                "profile_id": "srd5.1.poison_needle",
                "trap_id": instance_id,
                "source_ref": source,
                "target_actor_id": target_id,
            }
        },
    }


def trap_severity(severity: str, level: int) -> dict[str, Any]:
    """Return the 2014 DMG severity bands and damage suggestion."""
    key = str(severity).strip().casefold()
    if key not in _SEVERITY:
        raise ValueError("severity must be setback, dangerous, or deadly")
    if type(level) is not int or not 1 <= level <= 20:
        raise ValueError("character level must be an integer from 1 to 20")
    band = next((limits for limits in _DAMAGE if limits[0] <= level <= limits[1]), None)
    assert band is not None
    return {
        "severity": key,
        "level_band": f"{band[0]}-{band[1]}",
        "save_dc_range": list(_SEVERITY[key]["save_dc"]),
        "attack_bonus_range": list(_SEVERITY[key]["attack_bonus"]),
        "damage": _DAMAGE[band][key],
    }


def source_trap_profile(profile: Any, source_excerpt: Any) -> dict[str, Any]:
    """Resolve a fixed SRD profile whose identifier appears in source evidence.

    Only ``profile_id`` is caller-selectable. Rule values are returned from this
    registry, never copied from caller input. The marker binds the selected SRD
    profile identifier to the exact excerpt that Runtime independently verifies.
    """
    if not isinstance(profile, dict) or set(profile) != {"profile_id"}:
        raise ValueError("trap profile must contain only profile_id")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in _SRD_TRAPS:
        raise ValueError("unsupported source-bound SRD trap profile")
    if not isinstance(source_excerpt, str):
        raise ValueError("trap source_excerpt is required")
    marker = "trap_profile: " + json.dumps(
        {"profile_id": profile_id}, sort_keys=True, separators=(",", ":")
    )
    if marker not in source_excerpt:
        raise ValueError("trap profile identifier must match the source excerpt marker")
    return {"profile_id": profile_id, **deepcopy(_SRD_TRAPS[profile_id])}


def source_trap_object_facts(profile: Any, requested: Any = None) -> dict[str, Any]:
    """Return fixed source facts for the Falling Net's destructible object.

    The bundled trap entry fixes AC 10 and 20 hit points, and specifies
    slashing damage for destroying net sections. Runtime may accept other
    reviewed physical properties, but these source-authored facts cannot be
    chosen or changed by a caller.
    """
    if not isinstance(profile, dict) or profile.get("profile_id") != "srd5.1.falling_net":
        raise ValueError("source object facts are supported only for Falling Net")
    object_rules = dict(dict(profile.get("trigger") or {}).get("object") or {})
    armor_class = object_rules.get("ac")
    hit_points = object_rules.get("hp")
    section_damage = object_rules.get("slashing_damage_to_destroy_section")
    if (
        type(armor_class) is not int
        or armor_class != 10
        or type(hit_points) is not int
        or hit_points != 20
        or type(section_damage) is not int
        or section_damage != 5
    ):
        raise ValueError(
            "Falling Net source profile must define AC 10, 20 HP, and 5 slashing section damage"
        )
    damage_filter = {"allowed_damage_types": ["slashing"]}
    normalized_damage_filter = {
        **damage_filter,
        "required_any_weapon_traits": [],
        "allowed_weapon_ids": [],
    }
    if requested is not None:
        if not isinstance(requested, dict):
            raise ValueError("Falling Net object request must be an object")
        if "armor_class" in requested and requested["armor_class"] != armor_class:
            raise ValueError("Falling Net armor class is fixed by its source at 10")
        if "hit_points" in requested and requested["hit_points"] != hit_points:
            raise ValueError("Falling Net hit points are fixed by its source at 20")
        if "damage_filter" in requested:
            requested_filter = requested["damage_filter"]
            if requested_filter != damage_filter and requested_filter != normalized_damage_filter:
                raise ValueError("Falling Net object damage is fixed by its source to slashing")
    return {
        "armor_class": armor_class,
        "hit_points": hit_points,
        "damage_filter": damage_filter,
        "slashing_damage_to_destroy_section": section_damage,
    }


def validate_falling_net_section_spatial_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    trap_id: str,
    object_id: str,
    source_ref: str,
    scene_revision: int,
    campaign_revision: int,
    reviewed_by: str,
    restrained_actor_ids: list[str],
) -> dict[str, Any]:
    """Validate a complete DM-reviewed mapping of trapped actors to net sections."""
    if not isinstance(profile, dict) or profile.get("profile_id") != "srd5.1.falling_net":
        raise ValueError("net section spatial facts require the Falling Net profile")
    expected_fields = {
        "decision_id",
        "reason",
        "scene_id",
        "trap_id",
        "object_id",
        "source_ref",
        "scene_revision",
        "campaign_revision",
        "reviewed_by",
        "target_section_id",
        "actor_sections",
    }
    if not isinstance(facts, dict) or set(facts) != expected_fields:
        raise ValueError(
            "Falling Net section_spatial_facts require decision, scene, trap, object, "
            "source, revisions, reviewer, target section, and complete actor_sections"
        )
    decision_id = facts.get("decision_id")
    reason = " ".join(str(facts.get("reason") or "").split())
    if (
        not isinstance(decision_id, str)
        or not decision_id.strip()
        or len(decision_id) > 200
        or not reason
        or len(reason) > 1000
    ):
        raise ValueError("Falling Net section_spatial_facts require a bounded decision and reason")
    if facts.get("scene_id") != scene_id:
        raise ValueError("Falling Net section facts do not match the source scene")
    if type(facts.get("scene_revision")) is not int or facts["scene_revision"] != scene_revision:
        raise ValueError("Falling Net section facts are stale for the active scene revision")
    if facts.get("trap_id") != trap_id or facts.get("object_id") != object_id:
        raise ValueError("Falling Net section facts do not match this source trap object")
    if facts.get("source_ref") != source_ref:
        raise ValueError("Falling Net section facts do not match the exact trap source")
    if not reviewed_by or facts.get("reviewed_by") != reviewed_by:
        raise ValueError("Falling Net section facts reviewer does not match the authorized DM")
    revision = facts.get("campaign_revision")
    if type(revision) is not int or revision != campaign_revision:
        raise ValueError("Falling Net section facts are stale for the current campaign revision")
    if (
        not isinstance(restrained_actor_ids, list)
        or any(not isinstance(actor_id, str) or not actor_id for actor_id in restrained_actor_ids)
        or len(restrained_actor_ids) != len(set(restrained_actor_ids))
    ):
        raise ValueError("Falling Net section facts require current unique restrained actors")
    target_section_id = facts.get("target_section_id")
    valid_sections = {"northwest", "northeast", "southwest", "southeast"}
    if not isinstance(target_section_id, str) or target_section_id not in valid_sections:
        raise ValueError("Falling Net target section must identify one of four 5-foot squares")
    actor_sections = facts.get("actor_sections")
    if not isinstance(actor_sections, list):
        raise ValueError("Falling Net section facts require complete actor_sections")
    normalized_by_id: dict[str, str] = {}
    for item in actor_sections:
        if not isinstance(item, dict) or set(item) != {"actor_id", "section_id"}:
            raise ValueError("each net actor section fact must contain actor_id and section_id")
        actor_id = item.get("actor_id")
        section_id = item.get("section_id")
        if not isinstance(actor_id, str) or not actor_id or actor_id in normalized_by_id:
            raise ValueError("net actor section facts require unique non-empty actor IDs")
        if not isinstance(section_id, str) or section_id not in valid_sections:
            raise ValueError("net actor section facts must identify one of four 5-foot squares")
        normalized_by_id[actor_id] = section_id
    if set(normalized_by_id) != set(restrained_actor_ids):
        raise ValueError(
            "Falling Net section facts must cover every currently restrained actor exactly once"
        )
    ordered_sections = [
        {"actor_id": actor_id, "section_id": normalized_by_id[actor_id]}
        for actor_id in restrained_actor_ids
    ]
    affected_actor_ids = [
        actor_id
        for actor_id in restrained_actor_ids
        if normalized_by_id[actor_id] == target_section_id
    ]
    return {
        "decision_id": decision_id.strip(),
        "reason": reason,
        "scene_id": scene_id,
        "trap_id": trap_id,
        "object_id": object_id,
        "source_ref": source_ref,
        "scene_revision": scene_revision,
        "campaign_revision": revision,
        "reviewed_by": reviewed_by,
        "target_section_id": target_section_id,
        "actor_sections": ordered_sections,
        "affected_actor_ids": affected_actor_ids,
    }


def validate_falling_net_rescue_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    scene_revision: int,
    trap_id: str,
    source_ref: str,
    campaign_revision: int,
    reviewed_by: str,
    rescuer_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Validate DM-reviewed reach facts for freeing another creature from a net."""
    if not isinstance(profile, dict) or profile.get("profile_id") != "srd5.1.falling_net":
        raise ValueError("rescue facts require the Falling Net profile")
    keys = {
        "decision_id",
        "reason",
        "scene_id",
        "scene_revision",
        "trap_id",
        "source_ref",
        "campaign_revision",
        "reviewed_by",
        "rescuer_id",
        "target_id",
        "within_reach",
    }
    if not isinstance(facts, dict) or set(facts) != keys:
        raise ValueError("Falling Net rescue requires a complete DM-reviewed reach decision")
    if (
        not str(facts.get("decision_id") or "").strip()
        or not str(facts.get("reason") or "").strip()
    ):
        raise ValueError("Falling Net rescue requires a decision id and reason")
    expected = {
        "scene_id": scene_id,
        "scene_revision": scene_revision,
        "trap_id": trap_id,
        "source_ref": source_ref,
        "campaign_revision": campaign_revision,
        "reviewed_by": reviewed_by,
        "rescuer_id": rescuer_id,
        "target_id": target_id,
    }
    for key, value in expected.items():
        if facts.get(key) != value:
            raise ValueError(f"Falling Net rescue fact {key} is stale or mismatched")
    if type(facts.get("within_reach")) is not bool or facts["within_reach"] is not True:
        raise ValueError("Falling Net rescue target must be DM-confirmed within reach")
    if rescuer_id == target_id:
        raise ValueError("Falling Net rescue target must be another creature")
    return {
        **facts,
        "decision_id": facts["decision_id"].strip(),
        "reason": " ".join(facts["reason"].split()),
    }


def falling_net_section_cut_qualifies(profile: Any, attack: Any, damage: Any) -> bool:
    """Return whether an engine-resolved hit dealt the source's section-cut damage."""
    threshold = source_trap_object_facts(profile)["slashing_damage_to_destroy_section"]
    if not isinstance(attack, dict) or attack.get("hit") is not True:
        return False
    if not isinstance(damage, dict):
        return False
    parts = damage.get("parts")
    if not isinstance(parts, list):
        return False
    slashing_amount = sum(
        max(0, int(part.get("adjusted_amount", 0)))
        for part in parts
        if isinstance(part, dict) and part.get("damage_type") == "slashing"
    )
    return slashing_amount >= threshold


def validate_source_pit_depth(profile: Any, depth_ft: Any) -> int:
    """Validate a scene-confirmed pit dimension against its selected SRD profile."""
    if not isinstance(profile, dict):
        raise ValueError("source-bound pit profile is required")
    if profile.get("profile_id") not in {
        "srd5.1.simple_pit",
        "srd5.1.hidden_pit",
        "srd5.1.spiked_simple_pit",
        "srd5.1.spiked_hidden_pit",
        "srd5.1.poisoned_spiked_simple_pit",
        "srd5.1.poisoned_spiked_hidden_pit",
        "srd5.1.locking_pit",
        "srd5.1.spiked_locking_pit",
        "srd5.1.poisoned_spiked_locking_pit",
    }:
        raise ValueError("pit depth is accepted only for supported source-bound pit profiles")
    if isinstance(depth_ft, bool) or not isinstance(depth_ft, int) or depth_ft <= 0:
        raise ValueError("pit depth must be a positive integer scene dimension in feet")
    declared = dict(profile.get("trigger") or {}).get("depth_ft")
    if isinstance(declared, list) and depth_ft not in declared:
        raise ValueError("pit depth is outside the fixed source profile")
    return depth_ft


def validate_rolling_sphere_trigger_fact(
    profile: Any,
    fact: Any,
    *,
    scene_id: str,
    trap_id: str,
) -> dict[str, Any]:
    """Validate the source-defined pressure trigger without inventing a sphere path."""
    if not isinstance(profile, dict) or profile.get("profile_id") != "srd5.1.rolling_sphere":
        raise ValueError("pressure trigger fact requires the source-defined Rolling Sphere")
    trigger = profile.get("trigger")
    minimum_weight = (
        trigger.get("pressure_plate_minimum_weight_lb") if isinstance(trigger, dict) else None
    )
    if type(minimum_weight) is not int or minimum_weight != 20:
        raise ValueError("Rolling Sphere source profile must define a 20 lb minimum trigger weight")
    expected_fields = {"kind", "scene_id", "plate_id", "weight_lb"}
    if not isinstance(fact, dict) or set(fact) != expected_fields:
        raise ValueError("Rolling Sphere trigger requires exact pressure-plate weight facts")
    if fact.get("kind") != "pressure_plate_weight":
        raise ValueError("Rolling Sphere trigger fact must be pressure_plate_weight")
    if fact.get("scene_id") != scene_id:
        raise ValueError("Rolling Sphere trigger fact does not match the source-defined scene")
    if fact.get("plate_id") != trap_id:
        raise ValueError("Rolling Sphere trigger fact must identify this pressure plate")
    weight = fact.get("weight_lb")
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or (isinstance(weight, float) and not math.isfinite(weight))
        or weight < minimum_weight
    ):
        raise ValueError("Rolling Sphere triggers at 20 lb or greater")
    return deepcopy(fact)


def validate_source_trap_area_spatial_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    trap_id: str,
    encounter_id: str,
    source_ref: str,
    campaign_revision: int,
    reviewed_by: str,
    actor_ids: list[str],
    eligible_actor_ids: list[str] | None = None,
    positioning_mode: str = "agent",
    battle_map: dict[str, Any] | None = None,
    combatants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a complete Agent-reviewed area decision bound to one encounter revision."""
    if (
        not isinstance(profile, dict)
        or profile.get("profile_id") not in _SOURCE_AREA_TARGET_PROFILES
    ):
        raise ValueError("reviewed area spatial facts require a supported source trap area")
    expected_fields = {
        "decision_id",
        "reason",
        "scene_id",
        "trap_id",
        "encounter_id",
        "source_ref",
        "campaign_revision",
        "reviewed_by",
    }
    grid_mode = positioning_mode == "grid"
    expected_fields.add("grid_area") if grid_mode else expected_fields.add("actor_facts")
    if not isinstance(facts, dict) or set(facts) != expected_fields:
        raise ValueError(
            "area spatial_facts require decision_id, reason, scene_id, trap_id, encounter_id, "
            "source_ref, campaign_revision, reviewed_by, and the exact reviewed area facts"
        )
    decision_id = str(facts.get("decision_id") or "").strip()
    reason = " ".join(str(facts.get("reason") or "").split())
    if not decision_id or len(decision_id) > 200 or not reason or len(reason) > 1000:
        raise ValueError("area spatial_facts require a bounded reviewed decision_id and reason")
    if facts.get("scene_id") != scene_id:
        raise ValueError("area spatial_facts do not match the source-defined scene")
    if facts.get("trap_id") != trap_id:
        raise ValueError("area spatial_facts do not match this trap")
    if facts.get("encounter_id") != encounter_id:
        raise ValueError("area spatial_facts do not match the active encounter")
    if facts.get("source_ref") != source_ref:
        raise ValueError("area spatial_facts do not match the exact trap source")
    if not reviewed_by or facts.get("reviewed_by") != reviewed_by:
        raise ValueError("area spatial_facts reviewer does not match the authorized DM principal")
    revision = facts.get("campaign_revision")
    if type(revision) is not int or revision != campaign_revision:
        raise ValueError("area spatial_facts are stale for the current campaign revision")
    if (
        not isinstance(actor_ids, list)
        or any(not isinstance(item, str) or not item for item in actor_ids)
        or len(actor_ids) != len(set(actor_ids))
        or not actor_ids
    ):
        raise ValueError("area spatial facts require current unique encounter combatants")
    eligible_ids = actor_ids if eligible_actor_ids is None else eligible_actor_ids
    if (
        not isinstance(eligible_ids, list)
        or any(not isinstance(item, str) or not item for item in eligible_ids)
        or len(eligible_ids) != len(set(eligible_ids))
        or not set(eligible_ids).issubset(actor_ids)
    ):
        raise ValueError("area spatial facts require eligible actors from the active encounter")
    if grid_mode:
        if profile["profile_id"] != "srd5.1.falling_net":
            raise ValueError(
                "Grid area settlement currently supports only the fixed 10-foot Falling Net"
            )
        grid_area = facts.get("grid_area")
        map_value = battle_map if isinstance(battle_map, dict) else {}
        grid = map_value.get("grid") if isinstance(map_value.get("grid"), dict) else {}
        if (
            not isinstance(grid_area, dict)
            or set(grid_area) != {"map_id", "map_revision", "cells"}
            or not isinstance(map_value.get("id"), str)
            or grid_area.get("map_id") != map_value.get("id")
            or type(map_value.get("map_revision")) is not int
            or grid_area.get("map_revision") != map_value.get("map_revision")
            or grid.get("kind") != "square"
            or grid.get("cell_ft") != 5
        ):
            raise ValueError(
                "Grid area review must match the current 5-foot square battle map and revision"
            )
        cells = grid_area.get("cells")
        if (
            not isinstance(cells, list)
            or len(cells) != 4
            or any(not isinstance(cell, str) for cell in cells)
            or len(set(cells)) != 4
        ):
            raise ValueError("Falling Net Grid area must contain exactly four distinct cells")
        points = []
        for cell in cells:
            parts = cell.split(",")
            if len(parts) != 2 or any(not part.isdecimal() for part in parts):
                raise ValueError("Grid area cells must use canonical non-negative x,y coordinates")
            point = (int(parts[0]), int(parts[1]))
            if f"{point[0]},{point[1]}" != cell:
                raise ValueError("Grid area cells must use canonical non-negative x,y coordinates")
            points.append(point)
        xs = {point[0] for point in points}
        ys = {point[1] for point in points}
        if len(xs) != 2 or len(ys) != 2 or {(x, y) for x in xs for y in ys} != set(points):
            raise ValueError("Falling Net Grid area cells must form one exact 2-by-2 square")
        bounds = map_value.get("bounds") if isinstance(map_value.get("bounds"), dict) else {}
        if bounds and any(
            x >= bounds.get("width_cells", 0) or y >= bounds.get("height_cells", 0)
            for x, y in points
        ):
            raise ValueError("Grid area cells must lie inside the reviewed battle map")
        from .spaces import grid_space, overlap

        by_id = {str(item.get("actor_id") or ""): item for item in (combatants or [])}
        affected_actor_ids = []
        for actor_id in eligible_ids:
            actor = by_id.get(actor_id)
            position = actor.get("position") if isinstance(actor, dict) else None
            if not isinstance(position, dict) or not all(key in position for key in ("x", "y")):
                raise ValueError(
                    "Grid trap targeting requires authoritative positions for every eligible actor"
                )
            actor_point = (position["x"], position["y"])
            actor_space = grid_space(actor, actor_point, map_value)["space_ft"]
            if any(overlap((x, y), 5, actor_point, actor_space) for x, y in points):
                affected_actor_ids.append(actor_id)
        if not affected_actor_ids and profile["profile_id"] != "srd5.1.falling_net":
            raise ValueError("source trap area has no eligible targets")
        normalized = {
            "decision_id": decision_id,
            "reason": reason,
            "scene_id": scene_id,
            "trap_id": trap_id,
            "encounter_id": encounter_id,
            "source_ref": source_ref,
            "campaign_revision": revision,
            "reviewed_by": reviewed_by,
            "grid_area": {
                "map_id": map_value["id"],
                "map_revision": map_value["map_revision"],
                "cells": sorted(cells),
            },
            "affected_actor_ids": sorted(affected_actor_ids),
        }
        return normalized

    actor_facts = facts.get("actor_facts")
    if not isinstance(actor_facts, list) or not actor_facts:
        raise ValueError("area spatial_facts require one fact for every active encounter combatant")
    normalized_by_id: dict[str, dict[str, Any]] = {}
    for actor_fact in actor_facts:
        if not isinstance(actor_fact, dict) or set(actor_fact) != {"actor_id", "in_area"}:
            raise ValueError("each area actor fact must contain exactly actor_id and in_area")
        actor_id = actor_fact.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id or actor_id in normalized_by_id:
            raise ValueError("area spatial_facts actor IDs must be unique non-empty strings")
        if type(actor_fact.get("in_area")) is not bool:
            raise ValueError("area spatial_facts in_area values must be booleans")
        normalized_by_id[actor_id] = {
            "actor_id": actor_id,
            "in_area": actor_fact["in_area"],
        }
    if set(normalized_by_id) != set(actor_ids):
        raise ValueError(
            "area spatial_facts must cover every active encounter combatant exactly once"
        )
    ordered_facts = [normalized_by_id[actor_id] for actor_id in actor_ids]
    eligible_set = set(eligible_ids)
    affected_actor_ids = [
        item["actor_id"]
        for item in ordered_facts
        if item["in_area"] and item["actor_id"] in eligible_set
    ]
    if not affected_actor_ids and profile["profile_id"] not in {
        "srd5.1.collapsing_roof",
        "srd5.1.poison_darts",
        "srd5.1.falling_net",
        "srd5.1.fire_breathing_statue",
    }:
        raise ValueError("source trap area has no eligible targets")
    if profile["profile_id"] == "srd5.1.poison_darts":
        affected_actor_ids.sort()
    return {
        "decision_id": decision_id,
        "reason": reason,
        "scene_id": scene_id,
        "trap_id": trap_id,
        "encounter_id": encounter_id,
        "source_ref": source_ref,
        "campaign_revision": revision,
        "reviewed_by": reviewed_by,
        "actor_facts": ordered_facts,
        "affected_actor_ids": affected_actor_ids,
    }


def validate_sphere_annihilation_contact_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    trap_id: str,
    source_ref: str,
    campaign_revision: int,
    reviewed_by: str,
    target_actor_id: str | None = None,
    target_actor_revision: int | None = None,
    target_scene_object_id: str | None = None,
    target_scene_object_source_ref: str | None = None,
) -> dict[str, Any]:
    """Validate a DM-authorized, exact-revision Sphere contact decision."""
    if (
        not isinstance(profile, dict)
        or profile.get("profile_id") != "srd5.1.sphere_of_annihilation"
    ):
        raise ValueError("Sphere contact facts require the Sphere of Annihilation profile")
    actor_target = target_actor_id is not None
    if actor_target == (target_scene_object_id is not None):
        raise ValueError("Sphere contact must bind exactly one actor or scene object")
    target_field = "target_actor_id" if actor_target else "target_scene_object_id"
    target_id = target_actor_id if actor_target else target_scene_object_id
    expected_fields = {
        "decision_id",
        "reason",
        "scene_id",
        "trap_id",
        target_field,
        "source_ref",
        "campaign_revision",
        "reviewed_by",
        "enters_mouth",
    }
    if actor_target:
        expected_fields.add("target_actor_revision")
    else:
        expected_fields.add("target_scene_object_source_ref")
    if not isinstance(facts, dict) or set(facts) != expected_fields:
        raise ValueError("Sphere contact requires exact reviewed target and revision facts")
    if type(facts.get("campaign_revision")) is not int or type(campaign_revision) is not int:
        raise ValueError("Sphere contact campaign revision must be an integer")
    if actor_target and (
        type(facts.get("target_actor_revision")) is not int
        or type(target_actor_revision) is not int
    ):
        raise ValueError("Sphere contact actor revision must be an integer")
    decision_id = facts.get("decision_id")
    reason = " ".join(str(facts.get("reason") or "").split())
    if (
        not isinstance(decision_id, str)
        or not decision_id.strip()
        or len(decision_id) > 200
        or not reason
        or len(reason) > 1000
    ):
        raise ValueError("Sphere contact requires a bounded decision id and reason")
    expected = {
        "scene_id": scene_id,
        "trap_id": trap_id,
        target_field: target_id,
        "source_ref": source_ref,
        "campaign_revision": campaign_revision,
        "reviewed_by": reviewed_by,
    }
    if actor_target:
        expected["target_actor_revision"] = target_actor_revision
    else:
        expected["target_scene_object_source_ref"] = target_scene_object_source_ref
    for key, value in expected.items():
        if facts.get(key) != value:
            if key == "reviewed_by":
                raise ValueError("Sphere contact reviewer does not match the authorized DM")
            raise ValueError(f"Sphere contact fact {key} is stale or mismatched")
    if type(facts.get("enters_mouth")) is not bool or facts["enters_mouth"] is not True:
        raise ValueError("Sphere contact requires DM-confirmed entry into the stone mouth")
    return deepcopy(facts)


def record_sphere_annihilation_contact(
    state: Any,
    *,
    source_ref: str,
    trap_id: str,
    contact: dict[str, Any],
) -> dict[str, Any]:
    """Record a Sphere contact without consuming or destroying the Sphere trap."""
    if not isinstance(state, dict) or not isinstance(contact, dict):
        raise ValueError("Sphere contact state and decision must be objects")
    source = str(source_ref or "").strip()
    instance_id = str(trap_id or "").strip()
    if not source or not instance_id:
        raise ValueError("Sphere contact requires an exact source and trap id")
    if contact.get("trap_id") != instance_id or contact.get("source_ref") != source:
        raise ValueError("Sphere contact does not match its source-bound trap")
    target_id = str(
        contact.get("target_actor_id") or contact.get("target_scene_object_id") or ""
    ).strip()
    if not target_id:
        raise ValueError("Sphere contact requires its exact victim id")
    result = deepcopy(state)
    instances = dict(result.get("traps") or {})
    current = dict(instances.get(instance_id) or {})
    if current.get("source_ref") not in (None, source):
        raise ValueError("trap instance is bound to a different source")
    if current.get("profile_id") not in (None, "srd5.1.sphere_of_annihilation"):
        raise ValueError("trap instance is bound to a different source profile")
    if current.get("status", "armed") not in {"armed", "triggered"}:
        raise ValueError("Sphere contact requires an active source-bound trap")
    current.update({"source_ref": source, "profile_id": "srd5.1.sphere_of_annihilation"})
    current.setdefault("status", "armed")
    contacts = list(current.get("annihilation_contacts") or [])
    contacts.append(deepcopy(contact))
    current["annihilation_contacts"] = contacts[-100:]
    current["sphere_object_removed"] = False
    instances[instance_id] = current
    result["traps"] = instances
    return result


def validate_poison_needle_spatial_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    scene_revision: int,
    trap_id: str,
    target_actor_id: str,
    source_ref: str,
    campaign_revision: int,
    reviewed_by: str,
) -> dict[str, Any]:
    """Validate DM-reviewed distance for the Poison Needle's fixed 3-inch reach."""
    if not isinstance(profile, dict) or profile.get("profile_id") != "srd5.1.poison_needle":
        raise ValueError("Poison Needle spatial facts require the Poison Needle profile")
    expected_fields = {
        "decision_id",
        "reason",
        "scene_id",
        "scene_revision",
        "trap_id",
        "target_actor_id",
        "source_ref",
        "campaign_revision",
        "reviewed_by",
        "distance_inches",
    }
    if not isinstance(facts, dict) or set(facts) != expected_fields:
        raise ValueError(
            "Poison Needle spatial_facts require decision, scene, trap, actor, source, "
            "revision, reviewer, and distance fields"
        )
    decision_id = facts.get("decision_id")
    reason = " ".join(str(facts.get("reason") or "").split())
    if (
        not isinstance(decision_id, str)
        or not decision_id.strip()
        or len(decision_id) > 200
        or not reason
        or len(reason) > 1000
    ):
        raise ValueError("Poison Needle spatial_facts require bounded decision_id and reason")
    if facts.get("scene_id") != scene_id:
        raise ValueError("Poison Needle spatial_facts do not match the source scene")
    revision = facts.get("scene_revision")
    if type(revision) is not int or revision != scene_revision:
        raise ValueError("Poison Needle spatial_facts are stale for the active scene revision")
    if facts.get("trap_id") != trap_id:
        raise ValueError("Poison Needle spatial_facts do not match this trap")
    if facts.get("target_actor_id") != target_actor_id:
        raise ValueError("Poison Needle spatial_facts do not match the target actor")
    if facts.get("source_ref") != source_ref:
        raise ValueError("Poison Needle spatial_facts do not match the exact source")
    if not reviewed_by or facts.get("reviewed_by") != reviewed_by:
        raise ValueError("Poison Needle spatial_facts reviewer does not match the authorized DM")
    campaign_fact_revision = facts.get("campaign_revision")
    if type(campaign_fact_revision) is not int or campaign_fact_revision != campaign_revision:
        raise ValueError("Poison Needle spatial_facts are stale for the current campaign revision")
    distance = facts.get("distance_inches")
    if (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or not math.isfinite(float(distance))
        or float(distance) < 0
        or float(distance) > 3
    ):
        raise ValueError("Poison Needle target must be within its source-defined 3-inch range")
    return {
        "decision_id": decision_id.strip(),
        "reason": reason,
        "scene_id": scene_id,
        "scene_revision": scene_revision,
        "trap_id": trap_id,
        "target_actor_id": target_actor_id,
        "source_ref": source_ref,
        "campaign_revision": campaign_revision,
        "reviewed_by": reviewed_by,
        "distance_inches": float(distance),
    }


def validate_locking_pit_disable_scene_facts(
    profile: Any,
    facts: Any,
    *,
    scene_id: str,
    trap_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Validate DM-confirmed scene facts for disabling a locking pit from inside.

    The identity fields bind the adjudication to the expanded source scene, the
    active trap instance, and the creature making the check. The three required
    scene facts come directly from the selected fixed profile.
    """
    if not isinstance(profile, dict) or profile.get("profile_id") not in _LOCKING_PIT_PROFILES:
        raise ValueError("inside-pit disable facts require a source-defined locking pit")
    disable = profile.get("disable")
    required_scene = disable.get("required_scene") if isinstance(disable, dict) else None
    if (
        not isinstance(disable, dict)
        or disable.get("ability") != "dexterity"
        or disable.get("dc") != 15
        or disable.get("tool") != "thieves_tools"
        or not isinstance(required_scene, list)
        or not required_scene
        or any(not isinstance(key, str) or not key for key in required_scene)
        or len(set(required_scene)) != len(required_scene)
    ):
        raise ValueError("locking pit source profile is missing exact required scene facts")
    identity_fields = {"scene_id", "trap_id", "actor_id", "decision_id", "reason"}
    if not isinstance(facts, dict) or set(facts) != set(required_scene) | identity_fields:
        raise ValueError("locking pit disable requires exact reviewed scene facts")
    if any(type(facts[key]) is not bool or facts[key] is not True for key in required_scene):
        raise ValueError("locking pit disable requires every source-defined scene fact to be true")
    if facts.get("scene_id") != scene_id:
        raise ValueError("locking pit scene facts do not match the source-defined scene")
    if facts.get("trap_id") != trap_id or facts.get("actor_id") != actor_id:
        raise ValueError("locking pit scene facts must identify this trap and actor")
    if any(
        not isinstance(facts[key], str) or not facts[key].strip()
        for key in ("decision_id", "reason")
    ):
        raise ValueError("locking pit scene facts require a reviewed decision id and reason")
    return deepcopy(facts)


def validate_locking_pit_disable_state(
    state: Any,
    *,
    profile_id: str,
    source_ref: str,
    scene_id: str,
    trap_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Require the actor to be contained in this exact, still-open pit instance."""
    if not isinstance(state, dict):
        raise ValueError("trap state must be an object")
    traps = state.get("traps")
    current = traps.get(trap_id) if isinstance(traps, dict) else None
    if not isinstance(current, dict):
        raise ValueError("actor is not contained by this source-bound locking pit")
    if current.get("profile_id") != profile_id or profile_id not in _LOCKING_PIT_PROFILES:
        raise ValueError("locking pit instance does not match its source-defined profile")
    if current.get("source_ref") != source_ref:
        raise ValueError("trap instance is bound to a different source")
    if current.get("scene_id") != scene_id:
        raise ValueError("locking pit instance is bound to a different source-defined scene")
    if current.get("status") != "triggered":
        raise ValueError("only a triggered locking pit can have its spring disabled")
    if actor_id not in list(current.get("contained_actor_ids") or []):
        raise ValueError("actor is not contained by this source-bound locking pit")
    if current.get("spring_disabled") is True:
        raise ValueError("locking pit spring is already disabled")
    return deepcopy(current)


def apply_locking_pit_spring_disable(
    state: Any,
    *,
    profile_id: str,
    source_ref: str,
    scene_id: str,
    trap_id: str,
    actor_id: str,
    success: bool,
) -> dict[str, Any]:
    """Record the spring check while keeping the triggered pit and captives intact."""
    if type(success) is not bool:
        raise ValueError("locking pit disable requires an engine-resolved success value")
    current = validate_locking_pit_disable_state(
        state,
        profile_id=profile_id,
        source_ref=source_ref,
        scene_id=scene_id,
        trap_id=trap_id,
        actor_id=actor_id,
    )
    current["spring_disabled"] = success
    result = deepcopy(state)
    result.setdefault("traps", {})[trap_id] = current
    return result


def transition_trap_state(
    state: dict[str, Any],
    *,
    source_ref: str,
    trap_id: str,
    action: str,
    success: bool | None = None,
    actor_id: str | None = None,
    contained_actor_ids: list[str] | None = None,
    destroyed_object_id: str | None = None,
    destroyed_hit_points: int | None = None,
    section_id: str | None = None,
    released_actor_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Apply an explicit, source-bound trap lifecycle transition."""
    if not isinstance(state, dict):
        raise ValueError("trap state must be an object")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("trap source_ref is required")
    if not isinstance(trap_id, str) or not trap_id.strip():
        raise ValueError("trap_id is required")
    if action not in {
        "detect",
        "disable",
        "bypass",
        "trigger",
        "settle",
        "escape",
        "cut_object_section",
        "destroy_object",
    }:
        raise ValueError("unsupported trap action")
    if action in {"disable", "bypass"} and type(success) is not bool:
        raise ValueError("disable and bypass require an engine-resolved success value")
    if action not in {"disable", "bypass"} and success is not None:
        raise ValueError("success is accepted only for disable or bypass")
    if action == "escape" and (not isinstance(actor_id, str) or not actor_id.strip()):
        raise ValueError("escape requires a trapped actor id")
    if action != "escape" and actor_id is not None:
        raise ValueError("actor_id is accepted only for trap escape")
    if action == "destroy_object":
        if not isinstance(destroyed_object_id, str) or not destroyed_object_id.strip():
            raise ValueError("destroy_object requires a destroyed object id")
        if destroyed_hit_points != 0:
            raise ValueError("destroy_object requires zero authoritative remaining hit points")
    elif destroyed_object_id is not None or destroyed_hit_points is not None:
        raise ValueError("destroyed object facts are accepted only for destroy_object")
    if action == "cut_object_section":
        if not isinstance(section_id, str) or section_id not in {
            "northwest",
            "northeast",
            "southwest",
            "southeast",
        }:
            raise ValueError("cut_object_section requires one of the four net section ids")
        if (
            not isinstance(released_actor_ids, list)
            or any(not isinstance(item, str) or not item.strip() for item in released_actor_ids)
            or len(set(released_actor_ids)) != len(released_actor_ids)
        ):
            raise ValueError("cut_object_section requires distinct released actor ids")
    elif section_id is not None or released_actor_ids is not None:
        raise ValueError("section release facts are accepted only for cut_object_section")
    if contained_actor_ids is not None:
        if action != "trigger":
            raise ValueError("contained_actor_ids are accepted only when a trap triggers")
        if (
            not isinstance(contained_actor_ids, list)
            or not contained_actor_ids
            or any(not isinstance(item, str) or not item.strip() for item in contained_actor_ids)
            or len(set(contained_actor_ids)) != len(contained_actor_ids)
        ):
            raise ValueError("contained_actor_ids must be distinct non-empty actor ids")

    result = {**state}
    instances = dict(result.get("traps") or {})
    current = dict(instances.get(trap_id) or {})
    bound_ref = current.get("source_ref")
    if bound_ref is not None and bound_ref != source_ref:
        raise ValueError("trap instance is bound to a different source")
    current["source_ref"] = source_ref
    current.setdefault("status", "armed")
    status = current["status"]
    if action == "detect":
        current["detected"] = True
    elif action == "disable":
        if status != "armed":
            raise ValueError("only an armed trap can be disabled")
        if success:
            current["status"] = "disabled"
        else:
            current["status"] = "triggered"
    elif action == "bypass":
        if status != "armed":
            raise ValueError("only an armed trap can be bypassed")
        if success:
            current["bypassed"] = True
        else:
            current["status"] = "triggered"
    elif action == "trigger":
        if (
            current.get("bypassed") is True
            and current.get("bypass_method") == "wedge_pressure_plate"
        ):
            raise ValueError("a pressure-plate wedge prevents this trap from triggering")
        if status != "armed":
            raise ValueError("only an armed trap can trigger")
        current["status"] = "triggered"
        if contained_actor_ids is not None:
            current["contained_actor_ids"] = list(contained_actor_ids)
    elif action == "escape":
        if status != "triggered":
            raise ValueError("only a triggered trap can be escaped")
        contained = list(current.get("contained_actor_ids") or [])
        restrained = list(current.get("restrained_actor_ids") or [])
        if actor_id in contained:
            contained.remove(actor_id)
            current["contained_actor_ids"] = contained
            if not contained:
                current["status"] = "spent"
        elif actor_id in restrained:
            restrained.remove(actor_id)
            current["restrained_actor_ids"] = restrained
        else:
            raise ValueError("actor is not restrained by this trap")
        current["last_escaped_actor_id"] = actor_id
    elif action == "cut_object_section":
        if status != "triggered" or current.get("profile_id") != "srd5.1.falling_net":
            raise ValueError("only a triggered Falling Net can lose a section")
        severed = list(current.get("severed_section_ids") or [])
        if section_id in severed:
            raise ValueError("Falling Net section was already destroyed")
        restrained = list(current.get("restrained_actor_ids") or [])
        released = list(released_actor_ids or [])
        if not set(released).issubset(restrained):
            raise ValueError("section release actors must still be restrained by this net")
        owned = list(current.get("trap_added_restrained_actor_ids") or [])
        current["severed_section_ids"] = [*severed, section_id]
        current["restrained_actor_ids"] = [item for item in restrained if item not in released]
        current["trap_added_restrained_actor_ids"] = [
            item for item in owned if item not in released
        ]
        current["released_actor_ids"] = list(
            dict.fromkeys([*list(current.get("released_actor_ids") or []), *released])
        )
        current["last_released_section_id"] = section_id
        current["last_section_released_actor_ids"] = released
    elif action == "destroy_object":
        if status != "triggered":
            raise ValueError("only a triggered trap can be released by object destruction")
        if current.get("profile_id") != "srd5.1.falling_net":
            raise ValueError("object destruction release is supported only for Falling Net")
        if current.get("object_id") != destroyed_object_id:
            raise ValueError("destroyed object is not the source-bound Falling Net")
        restrained = list(current.get("restrained_actor_ids") or [])
        current["status"] = "spent"
        current["restrained_actor_ids"] = []
        current["trap_added_restrained_actor_ids"] = []
        current["released_actor_ids"] = list(
            dict.fromkeys([*list(current.get("released_actor_ids") or []), *restrained])
        )
        current["object_destroyed"] = True
        current["object_hit_points"] = 0
    elif action == "settle":
        if status != "triggered":
            raise ValueError("only a triggered trap can settle")
        current["status"] = "spent"
    instances[trap_id] = current
    result["traps"] = instances
    return result


def settle_trap_object_spell(
    state: dict[str, Any],
    *,
    profile: dict[str, Any],
    source_ref: str,
    scene_id: str,
    trap_id: str,
    actor_id: str,
    action: str,
    component: str,
    success: bool | None,
    face_enchantment_present: bool | None,
    reviewed_by: str,
    campaign_revision: int,
) -> dict[str, Any]:
    """Settle only fixed Fire Statue aura/dispelling and Sphere face dispelling.

    Runtime supplies ``reviewed_by`` from the authenticated campaign principal
    after checking DM membership and validates all source and scene identities.
    The domain function accepts an engine-resolved check result, never caller
    mechanics or a claimed role.
    """
    if not isinstance(state, dict) or not isinstance(profile, dict):
        raise ValueError("source-bound trap spell state and profile must be objects")
    source = str(source_ref or "").strip()
    scene = str(scene_id or "").strip()
    instance_id = str(trap_id or "").strip()
    actor = str(actor_id or "").strip()
    reviewer = str(reviewed_by or "").strip()
    if not source or not scene or not instance_id or not actor or not reviewer:
        raise ValueError("trap spell settlement requires source, scene, trap, actor, and reviewer")
    if type(campaign_revision) is not int or campaign_revision < 0:
        raise ValueError("trap spell review requires the current campaign revision")

    profile_id = profile.get("profile_id")
    if action == "detect_magic":
        detection = dict(profile.get("magic_detection") or {})
        if (
            profile_id != "srd5.1.fire_breathing_statue"
            or component != detection.get("target")
            or detection.get("effect") != "detect_magic_or_equivalent"
            or not detection.get("reveals_school")
            or success is not None
            or face_enchantment_present is not None
        ):
            raise ValueError("Detect Magic is source-defined only for the Fire-Breathing Statue")
        result = {
            "kind": "magic_aura",
            "target": component,
            "school": str(detection["reveals_school"]),
        }
    elif action == "dispel_magic":
        if type(success) is not bool:
            raise ValueError("Dispel Magic requires the engine-resolved check result")
        if face_enchantment_present is not None:
            if (
                profile_id != "srd5.1.sphere_of_annihilation"
                or component != "face_enchantment"
                or face_enchantment_present is not True
            ):
                raise ValueError("face enchantment facts apply only to the Sphere face")
            dispel = dict(dict(profile.get("trigger") or {}).get("optional_sympathy") or {})
            if (
                dispel.get("target") != component
                or dispel.get("effect") != "remove_enchantment_only"
            ):
                raise ValueError("Sphere face dispel does not match its source-defined effect")
            result = {
                "kind": "dispel_magic",
                "target": component,
                "success": success,
                "effect": "remove_enchantment_only" if success else "no_effect",
                "sphere_removed": False,
            }
        else:
            dispel = dict(profile.get("spell_disable") or {})
            if (
                profile_id != "srd5.1.fire_breathing_statue"
                or component != dispel.get("target")
                or dispel.get("spell") != "dispel_magic"
                or dispel.get("effect") != "destroy_trap"
            ):
                raise ValueError(
                    "Dispel Magic is source-defined only for the Fire Statue or Sphere face"
                )
            result = {
                "kind": "dispel_magic",
                "target": component,
                "success": success,
                "effect": "destroy_trap" if success else "no_effect",
            }
    else:
        raise ValueError("unsupported trap-object spell action")

    instances = dict(state.get("traps") or {})
    current = dict(instances.get(instance_id) or {})
    if current.get("source_ref") not in (None, source):
        raise ValueError("trap instance is bound to a different source")
    if current.get("scene_id") not in (None, scene):
        raise ValueError("trap instance is bound to a different scene")
    if current.get("profile_id") not in (None, profile_id):
        raise ValueError("trap instance is bound to a different source profile")

    status = str(current.get("status") or "armed")
    if profile_id == "srd5.1.fire_breathing_statue":
        if status != "armed":
            raise ValueError("only an armed Fire-Breathing Statue can be detected or dispelled")
        if action == "dispel_magic" and success is True:
            current["status"] = "disabled"
    elif action == "dispel_magic" and success is True:
        current["optional_sympathy_active"] = False
        current["sphere_object_removed"] = False

    current.update(
        source_ref=source,
        scene_id=scene,
        profile_id=profile_id,
    )
    if action == "detect_magic":
        current["magic_aura_revealed"] = result["school"]
    instances[instance_id] = current
    updated = {**state, "traps": instances}
    attempts = list(updated.get("attempts") or [])
    attempts.append(
        {
            "trap_id": instance_id,
            "source_ref": source,
            "scene_id": scene,
            "profile_id": profile_id,
            "actor_id": actor,
            "action": action,
            "component": component,
            "reviewed_by": reviewer,
            "campaign_revision": campaign_revision,
            "result": result,
        }
    )
    updated["attempts"] = attempts[-100:]
    return {"state": updated, "trap": current, "result": result}
