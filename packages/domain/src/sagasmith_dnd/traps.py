"""Deterministic helpers for source-described 2014 traps.

This module deliberately contains no scene discovery or adjudication. Callers
must bind a trap instance to an exact source reference before applying these
rules to campaign state.
"""

from __future__ import annotations

import hashlib
import json
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
        "detect": {"active": [{"ability": "perception", "dc": 10}], "passive_dc": 10},
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
        "settlement": "supported_confirmed_area_damage_rubble_record_only",
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
            "kind": "area_save_condition",
            "area": "10_foot_square",
            "save_ability": "dexterity",
            "save_dc": 10,
            "condition_on_failure": "restrained",
            "escape_check": {"ability": "strength", "dc": 10},
            "object": {"kind": "net", "ac": 10, "hp": 20, "slashing_damage_to_destroy_section": 5},
        },
        "settlement": "supported_single_target_area_confirmed_object_hp_unsupported",
    },
    "srd5.1.fire_breathing_statue": {
        "name": "Fire-Breathing Statue",
        "detect": {"active": [{"ability": "perception", "dc": 15}], "passive_dc": 15},
        "disable": None,
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
        "detect": {"active": [{"ability": "perception", "dc": 15}], "passive_dc": 15},
        "disable": None,
        "bypass_methods": ["move_to_side_alcove"],
        "trigger": {
            "kind": "complex_trap",
            "dexterity_save_dc": 15,
            "damage_expression": "10d10",
            "damage_type": "bludgeoning",
            "on_failure": ["prone"],
            "speed_ft": 60,
            "slow_check": {"ability": "strength", "dc": 20, "speed_reduction_ft": 15},
        },
        "settlement": "unsupported_complex_movement",
    },
    "srd5.1.sphere_of_annihilation": {
        "name": "Sphere of Annihilation",
        "detect": {"active": [{"ability": "arcana", "dc": 20}], "passive_dc": None},
        "disable": None,
        "bypass_methods": [],
        "trigger": {
            "kind": "complex_magic",
            "contact_effect": "obliterate",
            "optional_sympathy": {"dispel_magic_dc": 18},
        },
        "settlement": "unsupported_complex_magic_and_instant_death",
    },
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
        current["released_actor_ids"] = restrained
        current["object_destroyed"] = True
        current["object_hit_points"] = 0
    elif action == "settle":
        if status != "triggered":
            raise ValueError("only a triggered trap can settle")
        current["status"] = "spent"
    instances[trap_id] = current
    result["traps"] = instances
    return result
