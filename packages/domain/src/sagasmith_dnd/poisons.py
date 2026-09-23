"""Source-bound 2014 poison profiles and delivery predicates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .combat_engine import CombatEngineError
from .conditions import effect_condition_additions, reconcile_ended_effect_conditions
from .game_time import TICKS_PER_DAY

POISON_SOURCE_REF = "bundled:srd2014/08_Gamemastering/Poisons.md"
BASIC_POISON_SOURCE_REF = "bundled:srd2014/04_Equipment/Adventuring_Gear.md"
BASIC_POISON_MECHANIC_ID = "dnd5e.core.equipment.basic_poison_2014"
POISON_ITEM_SOURCE_PREFIX = "dnd5e.content.srd2014.item.poison."
MINUTE_TICKS = 10
HOUR_TICKS = 600
DAY_TICKS = 24 * HOUR_TICKS


@dataclass(frozen=True)
class PoisonProfile:
    id: str
    name: str
    delivery: str
    save_dc: int
    price_gp: int
    initial_damage: str = ""
    half_on_success: bool = False
    condition: str = ""
    duration_ticks: int = 0
    secondary_condition: str = ""
    secondary_condition_duration_ticks: int = 0
    recurring_period: str = ""
    recurring_damage: str = ""
    successes_to_end: int = 0
    wake_policy: tuple[str, ...] = ()
    truth_constraint: bool = False
    truth_constraint_kind: str = ""
    healing_locked_damage: bool = False
    delayed_until_midnight: bool = False
    rolled_duration: str = ""
    harvest_source: str = ""
    source_note: str = ""
    source_ref: str = POISON_SOURCE_REF


_PROFILES = (
    PoisonProfile(
        "assassins_blood",
        "Assassin's Blood",
        "ingested",
        10,
        150,
        initial_damage="1d12",
        half_on_success=True,
        condition="poisoned",
        duration_ticks=DAY_TICKS,
    ),
    PoisonProfile(
        "burnt_othur_fumes",
        "Burnt Othur Fumes",
        "inhaled",
        13,
        500,
        initial_damage="3d6",
        recurring_period="start_of_turn",
        recurring_damage="1d6",
        successes_to_end=3,
    ),
    PoisonProfile(
        "crawler_mucus",
        "Crawler Mucus",
        "contact",
        13,
        200,
        condition="poisoned",
        duration_ticks=MINUTE_TICKS,
        secondary_condition="paralyzed",
        secondary_condition_duration_ticks=MINUTE_TICKS,
        recurring_period="end_of_turn_save",
        harvest_source="crawler:dead_or_incapacitated",
    ),
    PoisonProfile(
        "drow_poison",
        "Drow Poison",
        "injury",
        13,
        200,
        condition="poisoned",
        duration_ticks=HOUR_TICKS,
        secondary_condition="unconscious",
        wake_policy=("damage", "action_shake"),
        source_note=(
            "unconscious_if_save_failed_by_5; typically_made_by_drow_in_a_place_far_from_sunlight"
        ),
    ),
    PoisonProfile(
        "essence_of_ether",
        "Essence of Ether",
        "inhaled",
        15,
        300,
        condition="poisoned",
        duration_ticks=8 * HOUR_TICKS,
        secondary_condition="unconscious",
        wake_policy=("damage", "action_shake"),
    ),
    PoisonProfile(
        "malice",
        "Malice",
        "inhaled",
        15,
        250,
        condition="poisoned",
        duration_ticks=HOUR_TICKS,
        secondary_condition="blinded",
        secondary_condition_duration_ticks=HOUR_TICKS,
    ),
    PoisonProfile(
        "midnight_tears",
        "Midnight Tears",
        "ingested",
        17,
        1500,
        initial_damage="9d6",
        half_on_success=True,
        delayed_until_midnight=True,
    ),
    PoisonProfile(
        "oil_of_taggit",
        "Oil of Taggit",
        "contact",
        13,
        400,
        condition="poisoned",
        duration_ticks=DAY_TICKS,
        secondary_condition="unconscious",
        wake_policy=("damage",),
    ),
    PoisonProfile(
        "pale_tincture",
        "Pale Tincture",
        "ingested",
        16,
        250,
        initial_damage="1d6",
        recurring_period="every_24_hours",
        recurring_damage="1d6",
        successes_to_end=7,
        healing_locked_damage=True,
    ),
    PoisonProfile(
        "purple_worm_poison",
        "Purple Worm Poison",
        "injury",
        19,
        2000,
        initial_damage="12d6",
        half_on_success=True,
        harvest_source="purple_worm:dead_or_incapacitated",
    ),
    PoisonProfile(
        "serpent_venom",
        "Serpent Venom",
        "injury",
        11,
        200,
        initial_damage="3d6",
        half_on_success=True,
        harvest_source="giant_poisonous_snake:dead_or_incapacitated",
    ),
    PoisonProfile(
        "torpor",
        "Torpor",
        "ingested",
        15,
        600,
        condition="poisoned",
        rolled_duration="4d6 hours",
        secondary_condition="incapacitated",
        source_note="incapacitated_for_poison_duration",
    ),
    PoisonProfile(
        "truth_serum",
        "Truth Serum",
        "ingested",
        11,
        150,
        condition="poisoned",
        duration_ticks=HOUR_TICKS,
        truth_constraint=True,
        truth_constraint_kind="cannot_knowingly_speak_a_lie",
    ),
    PoisonProfile(
        "wyvern_poison",
        "Wyvern Poison",
        "injury",
        15,
        1200,
        initial_damage="7d6",
        half_on_success=True,
        harvest_source="wyvern:dead_or_incapacitated",
    ),
)

POISONS_2014: dict[str, PoisonProfile] = {profile.id: profile for profile in _PROFILES}
_BASIC_POISON = PoisonProfile(
    "basic_poison",
    "Basic Poison",
    "injury",
    10,
    100,
    initial_damage="1d4",
    duration_ticks=MINUTE_TICKS,
    source_note="hit_trigger_without_condition_or_half_damage",
    source_ref=BASIC_POISON_SOURCE_REF,
)
_POISON_IDS_BY_NAME = {profile.name.casefold(): profile.id for profile in _PROFILES}


def poison_profile(value: Any) -> PoisonProfile:
    """Resolve one exact source profile by stable id or display name."""

    key = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if key == "basic_poison":
        return _BASIC_POISON
    profile_id = (
        key
        if key in POISONS_2014
        else _POISON_IDS_BY_NAME.get(str(value or "").strip().casefold(), "")
    )
    if not profile_id:
        raise CombatEngineError("poison is not a bundled 2014 sample poison")
    return POISONS_2014[profile_id]


def normalize_poison_dose_identity(value: Any) -> dict[str, str]:
    """Validate the small immutable identity stored on an inventory dose."""

    if not isinstance(value, dict) or set(value) != {
        "poison_id",
        "delivery",
        "edition",
        "source_ref",
    }:
        raise CombatEngineError("poison dose identity has unsupported fields")
    profile = poison_profile(value.get("poison_id"))
    normalized = {
        "poison_id": profile.id,
        "delivery": str(value.get("delivery") or ""),
        "edition": str(value.get("edition") or ""),
        "source_ref": str(value.get("source_ref") or ""),
    }
    if normalized != {
        "poison_id": profile.id,
        "delivery": profile.delivery,
        "edition": "2014",
        "source_ref": profile.source_ref,
    }:
        raise CombatEngineError("poison dose identity does not match its bundled 2014 source")
    return normalized


def validate_partial_ingested_ruling(value: Any) -> str:
    """Accept only the two examples the source permits for a partial dose."""

    ruling = str(value or "").strip().casefold().replace("-", "_")
    if ruling not in {"advantage_on_save", "half_damage_on_failure"}:
        raise CombatEngineError(
            "a partial ingested dose requires an authorized advantage or half-damage ruling"
        )
    return ruling


def contact_exposes(*, same_smeared_object: bool, exposed_skin_touch: bool) -> bool:
    """Clothed touch, proximity, and touching another object do not expose a target."""

    if type(same_smeared_object) is not bool or type(exposed_skin_touch) is not bool:
        raise CombatEngineError("contact poison requires authoritative object and skin facts")
    return same_smeared_object and exposed_skin_touch


def injury_exposes(
    *,
    coated_object_id: Any,
    damaging_object_id: Any,
    damage_type: Any,
    damage_applied: Any,
) -> bool:
    """Require damage from the exact coated object and a piercing/slashing wound."""

    if (
        isinstance(damage_applied, bool)
        or not isinstance(damage_applied, int)
        or damage_applied < 0
    ):
        raise CombatEngineError("injury poison requires authoritative non-negative applied damage")
    normalized_damage_type = str(damage_type or "").strip().casefold()
    if normalized_damage_type not in {"piercing", "slashing"}:
        return False
    return bool(
        damage_applied > 0
        and str(coated_object_id or "")
        and str(coated_object_id) == str(damaging_object_id or "")
    )


def validate_ingested_delivery(*, swallowed_entire_dose: Any, partial_ruling: Any = None) -> str:
    """Validate whole-dose ingestion or return the exact authorized partial ruling."""

    if type(swallowed_entire_dose) is not bool:
        raise CombatEngineError("ingested poison requires an authoritative swallowing fact")
    if swallowed_entire_dose:
        if partial_ruling not in (None, ""):
            raise CombatEngineError(
                "a complete ingested dose cannot also carry a partial-dose ruling"
            )
        return "whole_dose"
    return validate_partial_ingested_ruling(partial_ruling)


def build_poison_effect(
    poison: Any,
    *,
    effect_id: Any,
    source_actor_id: Any = "",
    elapsed_ticks: Any,
    save_succeeded: Any,
    save_failed_by: Any = 0,
    partial_ruling: Any = None,
    duration_roll_hours: Any = None,
) -> dict[str, Any] | None:
    """Create one effect record solely from a bundled profile and resolved facts."""

    profile = poison if isinstance(poison, PoisonProfile) else poison_profile(poison)
    if not str(effect_id or "").strip() or len(str(effect_id)) > 100:
        raise CombatEngineError("poison effect requires a bounded engine-generated id")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise CombatEngineError("poison exposure requires authoritative campaign time")
    if type(save_succeeded) is not bool:
        raise CombatEngineError("poison exposure requires an engine-resolved saving throw")
    if (
        isinstance(save_failed_by, bool)
        or not isinstance(save_failed_by, int)
        or save_failed_by < 0
    ):
        raise CombatEngineError("poison save margin must be a non-negative integer")
    if partial_ruling is not None:
        validate_partial_ingested_ruling(partial_ruling)
    if save_succeeded:
        return None

    additions = [value for value in (profile.condition, profile.secondary_condition) if value]
    if profile.source_note.startswith("unconscious_if_save_failed_by_5") and save_failed_by < 5:
        additions = [profile.condition] if profile.condition else []
    duration_ticks = profile.duration_ticks
    if profile.rolled_duration:
        if (
            isinstance(duration_roll_hours, bool)
            or not isinstance(duration_roll_hours, int)
            or not 4 <= duration_roll_hours <= 24
        ):
            raise CombatEngineError("Torpor requires its engine-rolled 4d6-hour duration")
        duration_ticks = duration_roll_hours * HOUR_TICKS
    elif duration_roll_hours is not None:
        raise CombatEngineError("this poison has no rolled duration")

    duration = {"period": "manual", "remaining": 0}
    for period, ticks in (("day", DAY_TICKS), ("hour", HOUR_TICKS), ("minute", MINUTE_TICKS)):
        if duration_ticks and duration_ticks % ticks == 0:
            duration = {"period": period, "remaining": duration_ticks // ticks}
            break
    poison_state = {
        "poison_id": profile.id,
        "source_ref": profile.source_ref,
        "edition": "2014",
        "source_actor_id": str(source_actor_id or "").strip(),
        "save_dc": profile.save_dc,
        "applied_at_elapsed_ticks": elapsed_ticks,
        "successes": 0,
        "recurring_period": profile.recurring_period,
        "recurring_damage": profile.recurring_damage,
        "successes_to_end": profile.successes_to_end,
        "next_due_elapsed_ticks": (
            elapsed_ticks + DAY_TICKS if profile.recurring_period == "every_24_hours" else None
        ),
        "wake_policy": list(profile.wake_policy),
        "truth_constraint": profile.truth_constraint,
        "truth_constraint_kind": profile.truth_constraint_kind,
        "healing_locked_damage": profile.healing_locked_damage,
        "healing_locked_damage_remaining": 0,
        "healing_locked_damage_ids": [],
        "partial_ruling": partial_ruling or "",
    }
    effect: dict[str, Any] = {
        "id": str(effect_id),
        "name": profile.name,
        "kind": "poison",
        "source": profile.source_ref,
        "active": True,
        "concentration": False,
        "duration": duration,
        "changes": (
            [{"path": "conditions", "mode": "add", "value": additions}] if additions else []
        ),
        "description": f"Source-bound 2014 {profile.name} effect.",
        "metadata": {"poison_state": poison_state},
    }
    return effect


def next_anchored_midnight_elapsed_ticks(elapsed_ticks: Any, world_time: Any) -> int:
    """Find the next midnight on the campaign's anchored calendar timeline."""

    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise CombatEngineError("Midnight Tears requires authoritative campaign time")
    if not isinstance(world_time, dict) or not world_time:
        raise CombatEngineError("Midnight Tears requires an anchored campaign calendar")
    offset = world_time.get("calendar_offset_ticks")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise CombatEngineError("Midnight Tears calendar anchor is invalid")
    calendar_ticks = elapsed_ticks + offset
    if calendar_ticks < 0:
        raise CombatEngineError("Midnight Tears calendar anchor precedes day one")
    next_calendar_midnight = (calendar_ticks // TICKS_PER_DAY + 1) * TICKS_PER_DAY
    return next_calendar_midnight - offset


def build_midnight_tears_effect(
    *,
    effect_id: Any,
    target_actor_id: Any,
    elapsed_ticks: Any,
    world_time: Any,
) -> dict[str, Any]:
    """Persist one ingested dose against the next anchored stroke of midnight."""

    due = next_anchored_midnight_elapsed_ticks(elapsed_ticks, world_time)
    actor_id = str(target_actor_id or "").strip()
    if not actor_id:
        raise CombatEngineError("Midnight Tears requires an exact target actor")
    return {
        "id": str(effect_id),
        "name": "Midnight Tears",
        "kind": "poison",
        "source": POISON_SOURCE_REF,
        "active": True,
        "concentration": False,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [],
        "description": "One source-bound ingested Midnight Tears dose awaiting midnight.",
        "metadata": {
            "poison_state": {
                "poison_id": "midnight_tears",
                "source_ref": POISON_SOURCE_REF,
                "edition": "2014",
                "target_actor_id": actor_id,
                "applied_at_elapsed_ticks": elapsed_ticks,
                "delayed_until_midnight": True,
                "midnight_due_elapsed_ticks": due,
                "successes": 0,
                "healing_locked_damage": False,
                "healing_locked_damage_remaining": 0,
                "healing_locked_damage_ids": [],
            }
        },
    }


def settle_poison_repeat_save(
    effect: Any,
    *,
    save_succeeded: Any,
    elapsed_ticks: Any,
) -> dict[str, Any]:
    """Advance one due recurring save without trusting caller-selected outcomes."""

    if not isinstance(effect, dict) or not effect.get("active") or effect.get("kind") != "poison":
        raise CombatEngineError("recurring poison save requires one active poison effect")
    if type(save_succeeded) is not bool:
        raise CombatEngineError("recurring poison save requires an engine-resolved result")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise CombatEngineError("recurring poison save requires authoritative campaign time")
    result = deepcopy(effect)
    metadata = dict(result.get("metadata") or {})
    state = dict(metadata.get("poison_state") or {})
    profile = poison_profile(state.get("poison_id"))
    if not profile.recurring_period:
        raise CombatEngineError("this poison has no recurring save")
    if profile.recurring_period == "every_24_hours":
        due = state.get("next_due_elapsed_ticks")
        if isinstance(due, bool) or not isinstance(due, int) or elapsed_ticks < due:
            raise CombatEngineError("Pale Tincture recurring save is not due yet")
    outcome = {
        "poison_id": profile.id,
        "save_succeeded": save_succeeded,
        "damage_expression": "" if save_succeeded else profile.recurring_damage,
        "damage_type": "poison" if profile.recurring_damage and not save_succeeded else "",
        "successes": int(state.get("successes", 0) or 0),
        "ended": False,
    }
    if save_succeeded:
        outcome["successes"] += 1
        threshold = profile.successes_to_end
        if profile.recurring_period == "end_of_turn_save" or (
            threshold and outcome["successes"] >= threshold
        ):
            result["active"] = False
            result["ended_reason"] = "recurring_poison_save_succeeded"
            outcome["ended"] = True
        elif profile.recurring_period == "every_24_hours":
            due = int(state["next_due_elapsed_ticks"])
            state["next_due_elapsed_ticks"] = due + DAY_TICKS
    elif profile.recurring_period == "every_24_hours":
        due = int(state["next_due_elapsed_ticks"])
        state["next_due_elapsed_ticks"] = due + DAY_TICKS
    state["successes"] = outcome["successes"]
    metadata["poison_state"] = state
    result["metadata"] = metadata
    outcome["effect"] = result
    return outcome


def record_poison_healing_lock(effect: Any, *, hp_damage: Any) -> dict[str, Any]:
    """Record only actual HP damage caused by Pale Tincture for its healing ban."""

    if not isinstance(effect, dict) or effect.get("kind") != "poison":
        raise CombatEngineError("healing restriction requires one poison effect")
    if isinstance(hp_damage, bool) or not isinstance(hp_damage, int) or hp_damage < 0:
        raise CombatEngineError("Pale Tincture healing restriction needs actual HP damage")
    result = deepcopy(effect)
    metadata = dict(result.get("metadata") or {})
    state = dict(metadata.get("poison_state") or {})
    profile = poison_profile(state.get("poison_id"))
    if not profile.healing_locked_damage:
        raise CombatEngineError("only Pale Tincture locks healing from its own damage")
    state["healing_locked_damage_remaining"] = (
        int(state.get("healing_locked_damage_remaining", 0) or 0) + hp_damage
    )
    metadata["poison_state"] = state
    result["metadata"] = metadata
    return result


def poison_healing_lock_amount(sheet: Any) -> int:
    """Return the remaining HP damage protected by active Pale Tincture instances."""

    if not isinstance(sheet, dict) or str(sheet.get("edition") or "2014") != "2014":
        return 0
    locked = 0
    for effect in sheet.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active"):
            continue
        if effect.get("kind") != "poison" or effect.get("source") != POISON_SOURCE_REF:
            continue
        state = dict(dict(effect.get("metadata") or {}).get("poison_state") or {})
        if (
            state.get("poison_id") != "pale_tincture"
            or state.get("source_ref") != POISON_SOURCE_REF
        ):
            continue
        remaining = state.get("healing_locked_damage_remaining", 0)
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
            raise CombatEngineError("Pale Tincture healing restriction ledger is invalid")
        if state.get("healing_locked_damage") is not True or state.get("edition") != "2014":
            raise CombatEngineError("Pale Tincture healing restriction provenance is invalid")
        locked += remaining
    return locked


def filter_poison_condition_immunities(sheet: Any, effect: Any) -> dict[str, Any]:
    """Remove only immune condition riders while preserving poison damage effects."""

    if not isinstance(sheet, dict) or not isinstance(effect, dict):
        raise CombatEngineError("poison condition immunity requires actor and effect records")
    result = deepcopy(effect)
    immune = set(
        str(value).strip().casefold().replace("-", "_").replace(" ", "_")
        for value in dict(sheet.get("traits") or {}).get("condition_immunities", [])
    )
    if not immune:
        return result
    changes = []
    for change in result.get("changes", []):
        if not isinstance(change, dict) or change.get("path") != "conditions":
            changes.append(change)
            continue
        raw = change.get("value")
        conditions = raw if isinstance(raw, list) else [raw]
        remaining = [
            item
            for item in conditions
            if str(item or "").strip().casefold().replace("-", "_").replace(" ", "_") not in immune
        ]
        if remaining:
            changes.append({**change, "value": remaining})
    result["changes"] = changes
    return result


def wake_poison_effects(
    sheet: Any,
    *,
    trigger: str,
    effect_ids: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """End only source-linked unconscious riders allowed by the wake trigger."""

    if not isinstance(sheet, dict):
        raise CombatEngineError("poison wake requires an actor sheet")
    if trigger not in {"damage", "action_shake"}:
        raise CombatEngineError("poison wake trigger is invalid")
    selected = None if effect_ids is None else {str(value) for value in effect_ids}
    result = deepcopy(sheet)
    woken: list[str] = []
    for effect in result.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active"):
            continue
        state = dict(dict(effect.get("metadata") or {}).get("poison_state") or {})
        if (
            effect.get("kind") != "poison"
            or effect.get("source") != POISON_SOURCE_REF
            or state.get("source_ref") != POISON_SOURCE_REF
            or state.get("edition") != "2014"
            or trigger not in state.get("wake_policy", [])
            or (selected is not None and str(effect.get("id") or "") not in selected)
            or "unconscious" not in effect_condition_additions(effect)
        ):
            continue
        original = deepcopy(effect)
        changes = []
        for change in effect.get("changes", []):
            if not isinstance(change, dict) or change.get("path") != "conditions":
                changes.append(change)
                continue
            raw = change.get("value")
            values = raw if isinstance(raw, list) else [raw]
            remaining = [value for value in values if str(value) != "unconscious"]
            if remaining:
                changes.append({**change, "value": remaining})
        effect["changes"] = changes
        metadata = dict(effect.get("metadata") or {})
        state["unconscious_wake_reason"] = trigger
        metadata["poison_state"] = state
        effect["metadata"] = metadata
        reconcile_ended_effect_conditions(result, ended_effects=[original])
        woken.append(str(effect.get("id") or ""))
    return result, woken


def validate_poison_coatings(value: Any = None) -> dict[str, Any]:
    """Validate campaign-persisted, source-bound doses already applied to objects."""

    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"schema_version", "coatings"}:
        raise CombatEngineError("campaign.state.poison_coatings fields are invalid")
    version = value.get("schema_version", 1)
    if isinstance(version, bool) or version != 1:
        raise CombatEngineError("campaign.state.poison_coatings.schema_version must be 1")
    raw_coatings = value.get("coatings", [])
    if not isinstance(raw_coatings, list):
        raise CombatEngineError("campaign.state.poison_coatings.coatings must be a list")
    coatings = []
    for index, raw in enumerate(raw_coatings):
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "poison_id",
            "object_actor_id",
            "object_item_id",
            "applied_by_actor_id",
            "dose_item_id",
            "source_key",
            "source_ref",
            "created_at_elapsed_ticks",
            "active",
        }:
            raise CombatEngineError(
                f"campaign.state.poison_coatings.coatings[{index}] fields are invalid"
            )
        profile = poison_profile(raw.get("poison_id"))
        if profile.delivery not in {"contact", "injury"}:
            raise CombatEngineError("only contact and injury doses can be coated on an object")
        identity = {
            "id": str(raw.get("id") or "").strip(),
            "poison_id": profile.id,
            "object_actor_id": str(raw.get("object_actor_id") or "").strip(),
            "object_item_id": str(raw.get("object_item_id") or "").strip(),
            "applied_by_actor_id": str(raw.get("applied_by_actor_id") or "").strip(),
            "dose_item_id": str(raw.get("dose_item_id") or "").strip(),
            "source_key": str(raw.get("source_key") or "").strip(),
            "source_ref": str(raw.get("source_ref") or "").strip(),
            "created_at_elapsed_ticks": raw.get("created_at_elapsed_ticks"),
            "active": raw.get("active"),
        }
        if any(
            not identity[key]
            for key in (
                "id",
                "object_actor_id",
                "object_item_id",
                "applied_by_actor_id",
                "dose_item_id",
            )
        ):
            raise CombatEngineError("poison coating object and actor identities are required")
        if identity["source_key"] != POISON_ITEM_SOURCE_PREFIX + profile.id:
            raise CombatEngineError("poison coating must originate from its exact official dose")
        if identity["source_ref"] != profile.source_ref:
            raise CombatEngineError("poison coating source citation is invalid")
        tick = identity["created_at_elapsed_ticks"]
        if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
            raise CombatEngineError("poison coating needs an authoritative campaign tick")
        if type(identity["active"]) is not bool:
            raise CombatEngineError("poison coating active state must be boolean")
        coatings.append(identity)
    if len({item["id"] for item in coatings}) != len(coatings):
        raise CombatEngineError("campaign.state.poison_coatings contains duplicate ids")
    return {"schema_version": 1, "coatings": coatings}
