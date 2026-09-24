"""Source-bound action facts for mechanically described 2014 adventuring gear.

This module records only fixed facts stated by the bundled SRD. It does not
resolve scene judgments, targets, attack rolls, saves, or movement triggers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sagasmith_core.text import ascii_slug

from .combat_engine import CombatEngineError
from .game_time import TICKS_PER_HOUR, TICKS_PER_MINUTE

ADVENTURING_GEAR_SOURCE_REF = "bundled:srd2014/04_Equipment/Adventuring_Gear.md"
ADVENTURING_GEAR_SOURCE_PREFIX = "dnd5e.content.srd2014.item."


@dataclass(frozen=True)
class GearAction:
    name: str
    action: str
    range_feet: int | None = None
    target: str = ""
    attack: bool = False
    save_ability: str = ""
    save_dc: int | None = None
    damage: str = ""
    damage_type: str = ""
    duration: str = ""
    notes: str = ""

    @property
    def source_key(self) -> str:
        return ADVENTURING_GEAR_SOURCE_PREFIX + ascii_slug(self.name)


_ACTIONS = (
    GearAction(
        "Acid (vial)",
        "action",
        20,
        "creature_or_object",
        True,
        damage="2d6",
        damage_type="acid",
        notes="splash within 5 feet or throw; ranged improvised weapon",
    ),
    GearAction(
        "Alchemist's fire (flask)",
        "action",
        20,
        "creature_or_object",
        True,
        damage="1d4",
        damage_type="fire",
        duration="start_of_turn_until_extinguished",
        notes="ranged improvised weapon; action DC 10 Dexterity check ends burning",
    ),
    GearAction(
        "Antitoxin (vial)",
        "unspecified",
        target="self_or_creature_drinking",
        duration="1 hour",
        notes="advantage on saves against poison; no benefit to undead or constructs",
    ),
    GearAction(
        "Ball bearings (bag of 1,000)",
        "action",
        target="10-foot-square",
        save_ability="dexterity",
        save_dc=10,
        notes=("creature crossing falls prone on failure; no save when moving at half speed"),
    ),
    GearAction(
        "Block and tackle",
        "unspecified",
        target="hoisting",
        notes="hoist up to four times normal lift capacity",
    ),
    GearAction(
        "Caltrops (bag of 20)",
        "action",
        target="5-foot-square",
        save_ability="dexterity",
        save_dc=15,
        damage="1",
        damage_type="piercing",
        notes=(
            "on failed save stop moving this turn; walking speed reduced by 10 feet "
            "until at least 1 hit point regained; half-speed movement avoids save"
        ),
    ),
    GearAction(
        "Chain (10 feet)",
        "unspecified",
        target="object",
        notes="10 hit points; burst with DC 20 Strength check",
    ),
    GearAction(
        "Climber's kit",
        "action",
        target="self",
        notes=(
            "anchor; cannot fall more than 25 feet from anchor or climb more than "
            "25 feet away without undoing it"
        ),
    ),
    GearAction(
        "Crowbar", "passive", target="strength_check", notes="advantage when leverage applies"
    ),
    GearAction(
        "Holy Water (flask)",
        "action",
        20,
        "creature",
        True,
        damage="2d6",
        damage_type="radiant",
        notes=(
            "splash within 5 feet or throw; damage only to fiends or undead; "
            "ranged improvised weapon"
        ),
    ),
    GearAction(
        "Hunting trap",
        "action",
        target="placed_trap",
        save_ability="dexterity",
        save_dc=13,
        damage="1d4",
        damage_type="piercing",
        notes=(
            "failed trigger save stops movement; escape with action DC 13 Strength; "
            "failed escape deals 1 piercing damage"
        ),
    ),
    GearAction(
        "Lamp",
        "unspecified",
        target="light",
        duration="6 hours per pint of oil",
        notes="bright 15-foot radius; dim for 30 more feet",
    ),
    GearAction(
        "Lantern, bullseye",
        "unspecified",
        target="light",
        duration="6 hours per pint of oil",
        notes="bright 60-foot cone; dim for 60 more feet",
    ),
    GearAction(
        "Lantern, hooded",
        "unspecified",
        target="light",
        duration="6 hours per pint of oil",
        notes=(
            "bright 30-foot radius; dim for 30 more feet; action to hood down to dim 5-foot radius"
        ),
    ),
    GearAction(
        "Torch",
        "action",
        target="light",
        duration="1 hour",
        notes="bright 20-foot radius; dim for 20 more feet; lighting uses a tinderbox",
    ),
    GearAction(
        "Lock",
        "unspecified",
        target="lock",
        save_ability="dexterity",
        save_dc=15,
        notes="pick requires thieves' tools proficiency; key is provided",
    ),
    GearAction(
        "Magnifying glass",
        "unspecified",
        target="appraise_or_inspect",
        notes=(
            "advantage for small or highly detailed item; sunlight, tinder, "
            "and about 5 minutes to start fire"
        ),
    ),
    GearAction(
        "Manacles",
        "unspecified",
        target="small_or_medium_creature",
        save_ability="dexterity",
        save_dc=20,
        notes=(
            "escape DC 20 Dexterity; break DC 20 Strength; pick DC 15 Dexterity "
            "with thieves' tools proficiency; 15 hit points"
        ),
    ),
    GearAction(
        "Oil (flask)",
        "action",
        20,
        "creature_or_object",
        True,
        damage="5",
        damage_type="fire",
        duration="1 minute to dry",
        notes=(
            "hit coats target; next fire damage adds 5; ground pour covers level "
            "5-foot square, burns 2 rounds for 5 fire damage once per turn"
        ),
    ),
    GearAction(
        "Ram, portable",
        "unspecified",
        target="door",
        notes="+4 Strength check; one helper grants advantage",
    ),
    GearAction(
        "Rope, hempen (50 feet)",
        "unspecified",
        target="object",
        notes="2 hit points; burst with DC 17 Strength check",
    ),
    GearAction(
        "Healer's kit",
        "action",
        target="creature_at_0_hp",
        notes="expend one of 10 uses to stabilize without a Wisdom (Medicine) check",
    ),
)

ADVENTURING_GEAR_ACTIONS = {action.source_key: action for action in _ACTIONS}


def adventuring_gear_action(item: Any) -> GearAction:
    """Resolve an official gear action from an exact bundled inventory identity."""

    if not isinstance(item, dict):
        raise CombatEngineError("adventuring gear action requires an inventory item")
    source_key = str(item.get("source_key") or "").strip()
    action = ADVENTURING_GEAR_ACTIONS.get(source_key)
    source_ref = str(item.get("source_ref") or "").strip()
    if action is None or (source_ref and source_ref != ADVENTURING_GEAR_SOURCE_REF):
        raise CombatEngineError("item is not an exact source-bound 2014 adventuring gear action")
    if str(item.get("name") or "").strip().casefold() != action.name.casefold():
        raise CombatEngineError("adventuring gear source identity does not match its item name")
    return action


def normalize_gear_intent(value: Any) -> str:
    """Normalize a client intent token without interpreting its claimed outcome."""
    if not isinstance(value, str):
        raise CombatEngineError("gear intent must be text")
    normalized = "_".join(value.strip().casefold().replace("-", " ").split())
    if not normalized or len(normalized) > 80:
        raise CombatEngineError("gear intent must contain 1 to 80 characters")
    return normalized


def _intent(
    economy: str | None,
    target: str,
    *,
    maximum_range: int | None = None,
    area: dict[str, Any] | None = None,
    attack: str | None = None,
    save: dict[str, Any] | None = None,
    effect: dict[str, Any] | None = None,
    duration_ticks: int | None = None,
    object_hp: int | None = None,
    check: dict[str, Any] | None = None,
    resource: dict[str, Any] | None = None,
    requires: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "action_economy": economy,
        "target": target,
        "maximum_range_feet": maximum_range,
        "area": area,
        "attack": attack,
        "save": save,
        "effect": effect or {},
        "duration_ticks": duration_ticks,
        "object_hit_points": object_hp,
        "check": check,
        "resource_cost": resource or {},
        "requirements": list(requires),
    }


_CONSUME_ONE = {"item_quantity": 1}
_ONE_HEALER_USE = {"item_uses": 1}
_OIL_FUEL = {
    "source_key": ADVENTURING_GEAR_SOURCE_PREFIX + ascii_slug("Oil (flask)"),
    "quantity": 1,
}
_AREA_10 = {"shape": "square", "width_feet": 10, "depth_feet": 10}
_AREA_5 = {"shape": "square", "width_feet": 5, "depth_feet": 5}

# These records are executable rule parameters, not settled outcomes. A caller
# cannot supply damage, DCs, durations, or costs; only the action intent is input.
_GEAR_INTENTS: dict[str, dict[str, dict[str, Any]]] = {
    "acid_vial": {
        "splash": _intent(
            "action",
            "creature_within_5_feet",
            maximum_range=5,
            attack="ranged_improvised",
            effect={"damage": "2d6", "damage_type": "acid"},
            resource=_CONSUME_ONE,
        ),
        "throw": _intent(
            "action",
            "creature_or_object",
            maximum_range=20,
            attack="ranged_improvised",
            effect={"damage": "2d6", "damage_type": "acid"},
            resource=_CONSUME_ONE,
        ),
    },
    "alchemist_s_fire_flask": {
        "throw": _intent(
            "action",
            "creature_or_object",
            maximum_range=20,
            attack="ranged_improvised",
            effect={
                "hit_damage": "1d4",
                "hit_damage_type": "fire",
                "ongoing_damage": "1d4",
                "ongoing_damage_type": "fire",
                "trigger": "start_of_target_turn",
                "extinguish_action": "action",
                "extinguish_check": {"ability": "dexterity", "dc": 10},
            },
            resource=_CONSUME_ONE,
        ),
        "extinguish": _intent(
            "action",
            "self_burning",
            check={"ability": "dexterity", "dc": 10},
            effect={"ends_effect_kind": "adventuring_gear_burning"},
        ),
    },
    "antitoxin_vial": {
        "drink": _intent(
            None,
            "creature_drinking",
            duration_ticks=TICKS_PER_HOUR,
            effect={"saving_throw_advantage": "poison"},
            requires=("target_not_undead", "target_not_construct"),
            resource=_CONSUME_ONE,
        ),
    },
    "ball_bearings_bag_of_1_000": {
        "spread": _intent(
            "action",
            "ground_area",
            area=_AREA_10,
            effect={
                "trigger": "creature_crosses_area",
                "save": {"ability": "dexterity", "dc": 10},
                "failure": {"condition": "prone"},
                "half_speed_avoids_save": True,
            },
            resource=_CONSUME_ONE,
        ),
    },
    "block_and_tackle": {
        "hoist": _intent(
            None,
            "load",
            effect={"maximum_lift_multiplier": 4},
            requires=("reviewed_load_weight_lb", "authoritative_normal_lift_capacity_lb"),
        ),
    },
    "caltrops_bag_of_20": {
        "spread": _intent(
            "action",
            "ground_area",
            area=_AREA_5,
            effect={
                "trigger": "creature_enters_area",
                "save": {"ability": "dexterity", "dc": 15},
                "failure": {
                    "movement_stops_this_turn": True,
                    "damage": "1",
                    "damage_type": "piercing",
                    "walking_speed_reduction_feet": 10,
                    "speed_reduction_ends_when_hp_at_least": 1,
                },
                "half_speed_avoids_save": True,
            },
            resource=_CONSUME_ONE,
        ),
    },
    "chain_10_feet": {
        "burst": _intent(
            None,
            "chain",
            object_hp=10,
            check={"ability": "strength", "dc": 20},
            effect={"requires_state": "intact", "success_state": "broken"},
        ),
    },
    "climber_s_kit": {
        "anchor": _intent(
            "action",
            "self",
            effect={
                "maximum_fall_feet_from_anchor": 25,
                "maximum_climb_feet_from_anchor": 25,
                "requires_undo_before_exceeding": True,
            },
        ),
        "undo_anchor": _intent(None, "self", effect={"remove_anchor": True}),
    },
    "crowbar": {
        "apply_leverage": _intent(
            None,
            "strength_check_on_door_or_object",
            effect={"advantage": True, "eligible_targets": ["door", "object"]},
            requires=("leverage_applies_to_check",),
        ),
    },
    "holy_water_flask": {
        "splash": _intent(
            "action",
            "creature_within_5_feet",
            maximum_range=5,
            attack="ranged_improvised",
            effect={
                "damage": "2d6",
                "damage_type": "radiant",
                "damage_only_if_target_type": ["fiend", "undead"],
            },
            resource=_CONSUME_ONE,
        ),
        "throw": _intent(
            "action",
            "creature",
            maximum_range=20,
            attack="ranged_improvised",
            effect={
                "damage": "2d6",
                "damage_type": "radiant",
                "damage_only_if_target_type": ["fiend", "undead"],
            },
            resource=_CONSUME_ONE,
        ),
    },
    "hunting_trap": {
        "set": _intent(
            "action",
            "ground_location",
            effect={
                "trigger": "creature_steps_on_pressure_plate",
                "save": {"ability": "dexterity", "dc": 13},
                "failure": {
                    "damage": "1d4",
                    "damage_type": "piercing",
                    "movement_stops": True,
                    "movement_limit_feet": 3,
                },
            },
        ),
        "escape": _intent(
            "action",
            "trapped_creature",
            check={"ability": "strength", "dc": 13},
            effect={
                "success": "free_trapped_creature",
                "failure_damage": "1",
                "failure_damage_type": "piercing",
            },
        ),
    },
    "lamp": {
        "light": _intent(
            None,
            "lamp",
            duration_ticks=6 * TICKS_PER_HOUR,
            effect={
                "bright_light": {"shape": "radius", "feet": 15},
                "dim_light_additional_feet": 30,
            },
            resource={"fuel": _OIL_FUEL},
        ),
        "extinguish": _intent(None, "lamp", effect={"light_off": True}),
    },
    "lantern_bullseye": {
        "light": _intent(
            None,
            "lantern",
            duration_ticks=6 * TICKS_PER_HOUR,
            effect={"bright_light": {"shape": "cone", "feet": 60}, "dim_light_additional_feet": 60},
            resource={"fuel": _OIL_FUEL},
        ),
        "extinguish": _intent(None, "lantern", effect={"light_off": True}),
    },
    "lantern_hooded": {
        "light": _intent(
            None,
            "lantern",
            duration_ticks=6 * TICKS_PER_HOUR,
            effect={
                "bright_light": {"shape": "radius", "feet": 30},
                "dim_light_additional_feet": 30,
            },
            resource={"fuel": _OIL_FUEL},
        ),
        "lower_hood": _intent(
            "action",
            "lantern",
            effect={"bright_light": None, "dim_light": {"shape": "radius", "feet": 5}},
        ),
        "raise_hood": _intent(
            None,
            "lantern",
            effect={
                "bright_light": {"shape": "radius", "feet": 30},
                "dim_light_additional_feet": 30,
            },
        ),
        "extinguish": _intent(None, "lantern", effect={"light_off": True}),
    },
    "torch": {
        "light": _intent(
            "action",
            "torch",
            duration_ticks=TICKS_PER_HOUR,
            effect={
                "bright_light": {"shape": "radius", "feet": 20},
                "dim_light_additional_feet": 20,
            },
        ),
        "extinguish": _intent(None, "torch", effect={"light_off": True}),
    },
    "lock": {
        "unlock": _intent(
            None,
            "lock",
            effect={
                "requires_state": "locked",
                "requires_provided_key": True,
                "success_state": "open",
            },
        ),
        "pick": _intent(
            None,
            "lock",
            check={"ability": "dexterity", "dc": 15},
            effect={
                "key_provided_by_source": True,
                "requires_key_unavailable": True,
                "requires_state": "locked",
                "success_state": "open",
                "failure_state": "locked",
            },
            requires=("thieves_tools_proficiency",),
        ),
    },
    "magnifying_glass": {
        "inspect": _intent(
            None,
            "small_or_highly_detailed_object",
            effect={"ability_check_advantage": "appraise_or_inspect"},
        ),
        "start_fire": _intent(
            None,
            "fire_site",
            duration_ticks=5 * TICKS_PER_MINUTE,
            effect={"requires_sunlight": True, "requires_tinder": True},
        ),
    },
    "manacles": {
        "bind": _intent(
            None,
            "small_or_medium_creature",
            effect={"binding_state": "bound"},
        ),
        "escape": _intent(
            None, "bound_small_or_medium_creature", check={"ability": "dexterity", "dc": 20}
        ),
        "break": _intent(None, "manacles", object_hp=15, check={"ability": "strength", "dc": 20}),
        "unlock": _intent(
            None,
            "bound_small_or_medium_creature",
            effect={"requires_provided_key": True, "binding_state": "released"},
        ),
        "pick": _intent(
            None,
            "manacles_lock",
            check={"ability": "dexterity", "dc": 15},
            effect={"key_provided_by_source": True},
            requires=("thieves_tools_proficiency",),
        ),
    },
    "oil_flask": {
        "splash": _intent(
            "action",
            "creature_within_5_feet",
            attack="ranged_improvised",
            effect={
                "coat_target_in_oil": True,
                "fire_damage_before_drying_bonus": 5,
                "bonus_damage_type": "fire",
            },
            duration_ticks=TICKS_PER_MINUTE,
            resource=_CONSUME_ONE,
        ),
        "throw": _intent(
            "action",
            "creature_or_object",
            maximum_range=20,
            attack="ranged_improvised",
            effect={
                "coat_target_in_oil": True,
                "fire_damage_before_drying_bonus": 5,
                "bonus_damage_type": "fire",
            },
            duration_ticks=TICKS_PER_MINUTE,
            resource=_CONSUME_ONE,
        ),
        "pour_ground": _intent(
            "action",
            "level_ground_surface",
            area=_AREA_5,
            effect={
                "if_lit": {
                    "duration_rounds": 2,
                    "trigger": "creature_enters_or_ends_turn_in_area",
                    "damage": "5",
                    "damage_type": "fire",
                    "once_per_turn_per_creature": True,
                }
            },
            resource=_CONSUME_ONE,
        ),
    },
    "ram_portable": {
        "break_door": _intent(
            None,
            "door",
            check={"ability": "strength", "bonus": 4},
            effect={"one_helper_grants_advantage": True},
        ),
    },
    "rope_hempen_50_feet": {
        "burst": _intent(
            None,
            "rope",
            object_hp=2,
            check={"ability": "strength", "dc": 17},
            effect={"requires_state": "intact", "success_state": "broken"},
        ),
    },
    "healer_s_kit": {
        "stabilize": _intent(
            "action",
            "creature_at_0_hp",
            effect={"stabilize": True, "medicine_check_required": False},
            resource=_ONE_HEALER_USE,
        ),
    },
}


def resolve_adventuring_gear_intent(
    item: Any,
    intent: Any,
    *,
    supplied_effects: Any = None,
) -> dict[str, Any]:
    """Return canonical fixed mechanics for one intent, without rolling or adjudicating.

    ``supplied_effects`` is rejected: callers cannot replace source DCs, ranges,
    damage, durations, target rules, or resource costs with computed claims.
    """
    source_action = adventuring_gear_action(item)
    normalized_intent = normalize_gear_intent(intent)
    if supplied_effects is not None:
        raise CombatEngineError("caller-supplied gear effects are not authoritative")
    intent_key = ascii_slug(source_action.name).replace("-", "_")
    item_intents = _GEAR_INTENTS.get(intent_key, {})
    resolved = item_intents.get(normalized_intent)
    if resolved is None:
        raise CombatEngineError("intent is not a source-defined action for this gear item")
    return {
        "source_key": source_action.source_key,
        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
        "item_name": source_action.name,
        "intent": normalized_intent,
        **deepcopy(resolved),
    }
