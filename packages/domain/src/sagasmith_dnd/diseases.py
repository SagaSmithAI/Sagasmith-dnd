"""Source-bound 2014 sample disease lifecycle rules.

The functions in this module are deterministic transition helpers. Runtime owns
the save rolls, authoritative creature records, clocks, persistence and commit.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .madness import resolve_madness

DISEASE_SOURCE_REF = "bundled:srd2014/08_Gamemastering/Diseases.md"
MADNESS_SOURCE_REF = "bundled:srd2014/08_Gamemastering/Madness.md"
TICKS_PER_HOUR = 600
TICKS_PER_DAY = 24 * TICKS_PER_HOUR
TICKS_PER_MINUTE = 10


class DiseaseError(ValueError):
    """A disease transition violates its bundled source contract."""


DISEASES_2014: dict[str, dict[str, Any]] = {
    "cackle_fever": {
        "id": "cackle_fever",
        "name": "Cackle Fever",
        "save_dc": 13,
        "eligible_creature_types": ["humanoid"],
        "immune_species": ["gnome"],
        "incubation": {"die": "1d4", "unit": "hour"},
        "source_ref": DISEASE_SOURCE_REF,
    },
    "sewer_plague": {
        "id": "sewer_plague",
        "name": "Sewer Plague",
        "save_dc": 11,
        "eligible_creature_types": ["humanoid"],
        "incubation": {"die": "1d4", "unit": "day"},
        "source_ref": DISEASE_SOURCE_REF,
    },
    "sight_rot": {
        "id": "sight_rot",
        "name": "Sight Rot",
        "save_dc": 15,
        "eligible_creature_types": ["beast", "humanoid"],
        "incubation": {"fixed": 1, "unit": "day"},
        "source_ref": DISEASE_SOURCE_REF,
    },
}


def disease_profile(value: Any) -> dict[str, Any]:
    """Return a copy of one exact 2014 bundled sample profile."""
    key = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if key not in DISEASES_2014:
        raise DiseaseError("disease is not a bundled 2014 sample disease")
    return deepcopy(DISEASES_2014[key])


def infection_state(
    disease: Any,
    *,
    actor_id: Any,
    elapsed_ticks: Any,
    incubation_roll: Any = None,
    save_succeeded: Any,
) -> dict[str, Any] | None:
    """Create infection state after an already-resolved authoritative save.

    The caller must validate source, eligibility, exposure, edition and save
    provenance before calling. Incubation is requested only after failure.
    """
    profile = disease_profile(disease)
    if type(save_succeeded) is not bool:
        raise DiseaseError("infection requires an engine-resolved saving throw")
    if save_succeeded:
        if incubation_roll is not None:
            raise DiseaseError("successful infection save cannot consume incubation dice")
        return None
    if not str(actor_id or "").strip():
        raise DiseaseError("infection requires an authoritative actor identity")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise DiseaseError("infection requires authoritative non-negative campaign time")
    incubation = profile["incubation"]
    if "die" in incubation:
        if (
            isinstance(incubation_roll, bool)
            or not isinstance(incubation_roll, int)
            or not 1 <= incubation_roll <= 4
        ):
            raise DiseaseError("failed infection requires one engine-owned d4 incubation result")
        amount = incubation_roll
    else:
        if incubation_roll is not None:
            raise DiseaseError("fixed incubation does not consume a random die")
        amount = int(incubation["fixed"])
    unit_ticks = TICKS_PER_HOUR if incubation["unit"] == "hour" else TICKS_PER_DAY
    state: dict[str, Any] = {
        "schema_version": 1,
        "disease_id": profile["id"],
        "actor_id": str(actor_id),
        "edition": "2014",
        "source_ref": profile["source_ref"],
        "infected_at_elapsed_ticks": elapsed_ticks,
        "symptoms_due_elapsed_ticks": elapsed_ticks + amount * unit_ticks,
        "symptomatic": False,
        "active": True,
    }
    if profile["id"] == "cackle_fever":
        state.update(
            {
                "laughter_dc": 13,
                "recovery_dc": 13,
                "failed_recovery_saves": 0,
                "indefinite_madness_applied": False,
                "exhaustion_lock": True,
            }
        )
    elif profile["id"] == "sewer_plague":
        state["exhaustion_lock"] = False
    else:
        state.update({"sight_penalty": 0, "blindness_owned": False, "ointment_doses_applied": 0})
    return state


def advance_disease_clock(state: dict[str, Any], *, elapsed_ticks: Any) -> dict[str, Any]:
    """Apply an incubation boundary exactly once to a disease instance."""
    result = deepcopy(state)
    _validate_state(result)
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise DiseaseError("disease clock requires authoritative non-negative campaign time")
    if (
        result["active"]
        and not result["symptomatic"]
        and elapsed_ticks >= result["symptoms_due_elapsed_ticks"]
    ):
        result["symptomatic"] = True
        if result["disease_id"] == "cackle_fever":
            result["symptom_exhaustion_owned"] = True
        elif result["disease_id"] == "sewer_plague":
            result["symptom_exhaustion_owned"] = True
    laughter_deadline = result.get("laughing_until_elapsed_ticks")
    if (
        result.get("active")
        and result.get("laughing")
        and isinstance(laughter_deadline, int)
        and not isinstance(laughter_deadline, bool)
        and elapsed_ticks >= laughter_deadline
    ):
        result["laughing"] = False
        result["incapacitation_owned"] = False
        result.pop("laughing_until_elapsed_ticks", None)
    return result


def resolve_cackle_stress(
    state: dict[str, Any],
    *,
    trigger: Any,
    save_succeeded: Any,
    elapsed_ticks: Any,
    psychic_damage_roll: Any = None,
) -> dict[str, Any]:
    """Resolve one source-listed stress trigger after Runtime resolves its save."""
    result = deepcopy(state)
    _validate_state(result)
    if (
        result["disease_id"] != "cackle_fever"
        or not result["active"]
        or not result["symptomatic"]
        or result.get("laughing")
    ):
        raise DiseaseError("stress trigger requires active symptomatic Cackle Fever")
    trigger_id = str(trigger or "").strip().casefold().replace(" ", "_")
    if trigger_id not in {"entering_combat", "taking_damage", "fear", "nightmare"}:
        raise DiseaseError("Cackle Fever stress trigger is not source-defined")
    _bool(save_succeeded)
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise DiseaseError("stress trigger requires authoritative campaign time")
    if save_succeeded:
        if psychic_damage_roll is not None:
            raise DiseaseError("successful stress save consumes no damage die")
        return {
            "state": result,
            "events": [{"kind": "stress_save_succeeded", "trigger": trigger_id}],
        }
    _die(
        psychic_damage_roll,
        10,
        "failed Cackle Fever stress save requires a d10 psychic damage roll",
    )
    result["laughing_until_elapsed_ticks"] = elapsed_ticks + TICKS_PER_MINUTE
    result["laughing"] = True
    result["incapacitation_owned"] = True
    return {
        "state": result,
        "events": [
            {"kind": "psychic_damage", "rolled": psychic_damage_roll, "damage_type": "psychic"},
            {
                "kind": "mad_laughter_started",
                "condition": "incapacitated",
                "ends_elapsed_ticks": result["laughing_until_elapsed_ticks"],
            },
        ],
    }


def resolve_cackle_end_turn(state: dict[str, Any], *, save_succeeded: Any) -> dict[str, Any]:
    """A successful end-of-turn save ends this instance's laughter condition."""
    result = deepcopy(state)
    _validate_state(result)
    if result["disease_id"] != "cackle_fever" or not result.get("laughing"):
        raise DiseaseError("end-turn recovery requires an active laughter episode")
    _bool(save_succeeded)
    if save_succeeded:
        result["laughing"] = False
        result["incapacitation_owned"] = False
        result.pop("laughing_until_elapsed_ticks", None)
        return {
            "state": result,
            "events": [{"kind": "mad_laughter_ended", "condition": "incapacitated"}],
        }
    return {"state": result, "events": [{"kind": "mad_laughter_continues"}]}


def cackle_carrier_immunity(
    *, target_id: Any, carrier_id: Any, elapsed_ticks: Any
) -> dict[str, Any]:
    """Create carrier-specific 24-hour immunity after a successful spread save."""
    if not str(target_id or "").strip() or not str(carrier_id or "").strip():
        raise DiseaseError("carrier immunity requires authoritative target and carrier identities")
    if str(target_id) == str(carrier_id):
        raise DiseaseError("a carrier cannot gain immunity to its own laughter")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise DiseaseError("carrier immunity requires authoritative campaign time")
    return {
        "schema_version": 1,
        "target_actor_id": str(target_id),
        "carrier_actor_id": str(carrier_id),
        "source_ref": DISEASE_SOURCE_REF,
        "immune_from_elapsed_ticks": elapsed_ticks,
        "expires_elapsed_ticks": elapsed_ticks + TICKS_PER_DAY,
    }


def resolve_cackle_spread(
    carrier_state: dict[str, Any],
    *,
    target_actor_id: Any,
    target_creature_type: Any,
    target_species_id: Any,
    distance_ft: Any = None,
    within_10_feet: Any = None,
    elapsed_ticks: Any,
    save_succeeded: Any = None,
    incubation_roll: Any = None,
    immunity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve spread from a currently laughing carrier at turn start.

    Creature taxonomy, distance and turn-start are authoritative normalized
    facts supplied by Runtime; this function does not infer them from names.
    """
    carrier = deepcopy(carrier_state)
    _validate_state(carrier)
    if (
        carrier["disease_id"] != "cackle_fever"
        or not carrier["active"]
        or not carrier.get("laughing")
    ):
        raise DiseaseError("spread requires a currently laughing active Cackle Fever carrier")
    if not str(target_actor_id or "").strip() or str(target_actor_id) == carrier["actor_id"]:
        raise DiseaseError("spread requires a distinct authoritative target actor")
    if isinstance(elapsed_ticks, bool) or not isinstance(elapsed_ticks, int) or elapsed_ticks < 0:
        raise DiseaseError("spread requires authoritative non-negative campaign time")
    creature_type = str(target_creature_type or "").strip().casefold()
    species_id = str(target_species_id or "").strip().casefold()
    if creature_type != "humanoid":
        if save_succeeded is not None or incubation_roll is not None:
            raise DiseaseError("ineligible target makes no spread save and consumes no RNG")
        return {"status": "ineligible", "infection": None, "immunity": None}
    if species_id == "gnome":
        if save_succeeded is not None or incubation_roll is not None:
            raise DiseaseError("immune gnome makes no spread save and consumes no RNG")
        return {"status": "immune", "infection": None, "immunity": None}
    if distance_ft is not None:
        if (
            isinstance(distance_ft, bool)
            or not isinstance(distance_ft, (int, float))
            or distance_ft < 0
        ):
            raise DiseaseError("spread requires authoritative non-negative distance")
        in_range = distance_ft <= 10
    elif type(within_10_feet) is bool:
        in_range = within_10_feet
    else:
        raise DiseaseError("spread requires authoritative range or distance fact")
    if not in_range:
        if save_succeeded is not None or incubation_roll is not None:
            raise DiseaseError("target outside 10 feet makes no spread save and consumes no RNG")
        return {"status": "out_of_range", "infection": None, "immunity": None}
    if immunity is not None:
        deadline = immunity.get("expires_elapsed_ticks")
        if (
            immunity.get("source_ref") == DISEASE_SOURCE_REF
            and immunity.get("schema_version") == 1
            and immunity.get("target_actor_id") == str(target_actor_id)
            and immunity.get("carrier_actor_id") == carrier["actor_id"]
            and isinstance(immunity.get("immune_from_elapsed_ticks"), int)
            and not isinstance(immunity.get("immune_from_elapsed_ticks"), bool)
            and immunity["immune_from_elapsed_ticks"] <= elapsed_ticks
            and isinstance(deadline, int)
            and not isinstance(deadline, bool)
            and elapsed_ticks < deadline
        ):
            if save_succeeded is not None or incubation_roll is not None:
                raise DiseaseError("active pair immunity makes no save and consumes no RNG")
            return {
                "status": "immune_to_carrier",
                "dc": 10,
                "infection": None,
                "immunity": immunity,
            }
    if type(save_succeeded) is not bool:
        raise DiseaseError("eligible exposure requires an engine-resolved DC 10 save")
    if save_succeeded:
        if incubation_roll is not None:
            raise DiseaseError("successful spread save consumes no incubation die")
        granted = cackle_carrier_immunity(
            target_id=target_actor_id,
            carrier_id=carrier["actor_id"],
            elapsed_ticks=elapsed_ticks,
        )
        return {"status": "saved", "dc": 10, "infection": None, "immunity": granted}
    infection = infection_state(
        "cackle_fever",
        actor_id=target_actor_id,
        elapsed_ticks=elapsed_ticks,
        save_succeeded=False,
        incubation_roll=incubation_roll,
    )
    return {"status": "infected", "dc": 10, "infection": infection, "immunity": None}


def resolve_long_rest(
    state: dict[str, Any],
    *,
    save_succeeded: bool | None,
    elapsed_ticks: Any,
    recovery_die: Any = None,
    madness_d100: Any = None,
) -> dict[str, Any]:
    """Resolve a disease's long-rest trigger; Runtime must own save/RNG/commit.

    Cackle Fever and Sewer Plague require a save on every symptomatic long
    rest. Sight Rot does not call for a save.
    """
    result = advance_disease_clock(state, elapsed_ticks=elapsed_ticks)
    _validate_state(result)
    if not result["active"] or not result["symptomatic"]:
        return {"state": result, "events": []}
    disease_id = result["disease_id"]
    if disease_id == "cackle_fever":
        if save_succeeded is None:
            raise DiseaseError("symptomatic Cackle Fever requires a long-rest save")
        _bool(save_succeeded)
        if save_succeeded:
            _die(recovery_die, 6, "Cackle Fever DC reduction requires one d6")
            amount = recovery_die
            result["recovery_dc"] = max(0, int(result["recovery_dc"]) - amount)
            result["laughter_dc"] = max(0, int(result["laughter_dc"]) - amount)
            if result["recovery_dc"] == 0:
                result["active"] = False
                result["exhaustion_lock"] = False
                result["symptom_exhaustion_owned"] = False
            return {
                "state": result,
                "events": [{"kind": "dc_reduced", "amount": amount, "cured": not result["active"]}],
            }
        if recovery_die is not None:
            raise DiseaseError("failed Cackle Fever recovery consumes no DC reduction die")
        result["failed_recovery_saves"] = int(result["failed_recovery_saves"]) + 1
        events = [{"kind": "recovery_failed", "count": result["failed_recovery_saves"]}]
        if result["failed_recovery_saves"] == 3 and not result["indefinite_madness_applied"]:
            _die(madness_d100, 100, "third Cackle Fever failure requires a d100 madness result")
            result["indefinite_madness_applied"] = True
            events.append(
                {
                    "kind": "indefinite_madness",
                    "madness": resolve_madness("indefinite", madness_d100),
                }
            )
        elif madness_d100 is not None:
            raise DiseaseError(
                "indefinite madness is rolled only on the third failed recovery save"
            )
        return {"state": result, "events": events}
    if madness_d100 is not None or recovery_die is not None:
        raise DiseaseError("this disease does not use Cackle Fever recovery dice")
    events: list[dict[str, Any]] = []
    if disease_id == "sewer_plague":
        _bool(save_succeeded)
        result["long_rest_hp_recovery_blocked"] = True
        events.extend(
            [
                {"kind": "long_rest_hp_recovery_blocked"},
                {"kind": "hit_dice_recovery_halved"},
            ]
        )
    elif save_succeeded is not None:
        _bool(save_succeeded)
    if disease_id == "sewer_plague":
        if save_succeeded:
            # Sewer Plague's explicit decrement follows the ordinary 2014
            # long-rest decrement, so callers pass the post-ordinary level.
            return {
                "state": result,
                "events": [
                    *events,
                    {
                        "kind": "exhaustion_reduce_total",
                        "amount": 1,
                        "cure_below": 1,
                        "after_ordinary_rest_recovery": True,
                    },
                ],
            }
        return {"state": result, "events": [*events, {"kind": "exhaustion_increase", "amount": 1}]}
    # A Sight Rot dose applied before this boundary prevents this worsening.
    if result.pop("ointment_prevent_next_rest", False):
        events.append({"kind": "worsening_prevented_by_ointment"})
    else:
        result["sight_penalty"] = min(5, int(result["sight_penalty"]) + 1)
        if result["sight_penalty"] == 5 and not result["blindness_owned"]:
            result["blindness_owned"] = True
            events.append({"kind": "blindness_applied", "condition": "blinded"})
        events.append({"kind": "sight_penalty_worsened", "penalty": -result["sight_penalty"]})
    return {"state": result, "events": events}


def resolve_sewer_plague_rest_exhaustion(
    *,
    current_exhaustion: Any,
    disease_save_succeeded: Any,
    ordinary_2014_recovery_applies: Any,
) -> dict[str, Any]:
    """Apply ordinary long-rest recovery first, then Sewer Plague's own delta.

    Level 6 is already fatal on entry. A failed disease save can take a living
    character to level 6 after ordinary recovery and causes death then.
    """
    if (
        isinstance(current_exhaustion, bool)
        or not isinstance(current_exhaustion, int)
        or not 0 <= current_exhaustion <= 6
    ):
        raise DiseaseError("exhaustion level must be an integer from 0 through 6")
    _bool(ordinary_2014_recovery_applies)
    if current_exhaustion == 6:
        return {
            "exhaustion": 6,
            "ordinary_recovery_applied": False,
            "disease_delta": 0,
            "disease_cured": False,
            "dead": True,
            "events": [{"kind": "already_dead_at_exhaustion_6"}],
        }
    _bool(disease_save_succeeded)
    level = current_exhaustion
    events = []
    if ordinary_2014_recovery_applies:
        level = max(0, level - 1)
        events.append({"kind": "ordinary_2014_rest_recovery", "exhaustion": level})
    before_disease_delta = level
    if disease_save_succeeded:
        level = max(0, level - 1)
        cured = level < 1
        events.append(
            {
                "kind": "sewer_plague_rest_success",
                "exhaustion": level,
                "cured": cured,
            }
        )
        return {
            "exhaustion": level,
            "ordinary_recovery_applied": ordinary_2014_recovery_applies,
            "disease_delta": level - before_disease_delta,
            "disease_cured": cured,
            "dead": False,
            "events": events,
        }
    level += 1
    dead = level >= 6
    events.append(
        {
            "kind": "sewer_plague_rest_failure",
            "exhaustion": level,
            "dead": dead,
        }
    )
    return {
        "exhaustion": level,
        "ordinary_recovery_applied": ordinary_2014_recovery_applies,
        "disease_delta": 1,
        "disease_cured": False,
        "dead": dead,
        "events": events,
    }


def sewer_plague_hit_die_healing(
    hit_die_rolls: Any, *, constitution_modifier: Any
) -> dict[str, int]:
    """Halve ordinary per-die healing before HP-cap application (2014 Sewer Plague)."""
    if not isinstance(hit_die_rolls, list):
        raise DiseaseError("Sewer Plague healing requires the authoritative Hit Die rolls")
    if (
        isinstance(constitution_modifier, bool)
        or not isinstance(constitution_modifier, int)
    ):
        raise DiseaseError("Sewer Plague healing requires the Constitution modifier")
    normal = 0
    for entry in hit_die_rolls:
        if not isinstance(entry, dict):
            raise DiseaseError("Sewer Plague healing requires per-die roll records")
        rolled = entry.get("total")
        if isinstance(rolled, bool) or not isinstance(rolled, int) or rolled < 0:
            raise DiseaseError("Sewer Plague requires non-negative authoritative Hit Die rolls")
        normal += max(0, rolled + constitution_modifier)
    return {"normal_hit_die_healing": normal, "disease_hit_die_healing": normal // 2}


def apply_sight_rot_ointment(state: dict[str, Any], *, doses: Any = 1) -> dict[str, Any]:
    """Record a dose applied before a rest; inventory consumption is Runtime-owned."""
    result = deepcopy(state)
    _validate_state(result)
    if result["disease_id"] != "sight_rot" or not result["active"]:
        raise DiseaseError("ointment applies only to active Sight Rot")
    if isinstance(doses, bool) or not isinstance(doses, int) or doses < 1 or doses > 3:
        raise DiseaseError("ointment application requires one to three whole doses")
    total = int(result["ointment_doses_applied"]) + doses
    if total > 3:
        raise DiseaseError(
            "Sight Rot ends after three ointment doses; extra doses are not consumed"
        )
    result["ointment_doses_applied"] = total
    result["ointment_prevent_next_rest"] = True
    events = [{"kind": "rest_worsening_prevented"}]
    if total >= 3:
        result["active"] = False
        events.append(
            {
                "kind": "disease_cured",
                "blindness_restoration_required": bool(result["blindness_owned"]),
            }
        )
    return {"state": result, "events": events}


def sight_rot_attack_penalty(state: dict[str, Any]) -> int:
    """Return the disease-owned attack-roll modifier (zero outside symptoms)."""
    _validate_state(state)
    if state["disease_id"] != "sight_rot" or not state["active"] or not state["symptomatic"]:
        return 0
    penalty = state.get("sight_penalty")
    if isinstance(penalty, bool) or not isinstance(penalty, int) or not 0 <= penalty <= 5:
        raise DiseaseError("Sight Rot penalty must be an integer from 0 through 5")
    return -penalty


def sight_rot_ability_check_penalty(
    state: dict[str, Any], *, relies_on_sight: Any
) -> int:
    """Return Sight Rot's check modifier only for a sight-dependent check.

    ``relies_on_sight`` must be resolved from the authoritative check/action
    context by Runtime. This helper intentionally does not project a global
    ability-check penalty onto the actor sheet.
    """
    if type(relies_on_sight) is not bool:
        raise DiseaseError("Sight Rot check penalty requires resolved check context")
    if not relies_on_sight:
        _validate_state(state)
        return 0
    return sight_rot_attack_penalty(state)


def cure_disease(state: dict[str, Any], *, disease_id: Any) -> dict[str, Any]:
    """End only an exact active disease instance and return owned-rider releases."""
    result = deepcopy(state)
    _validate_state(result)
    key = disease_profile(disease_id)["id"]
    if key != result["disease_id"]:
        raise DiseaseError("cure must select the exact disease instance")
    result["active"] = False
    released = []
    for field, event in (
        ("exhaustion_lock", "exhaustion_removal_released"),
        ("symptom_exhaustion_owned", "symptom_exhaustion_released"),
    ):
        if result.get(field):
            result[field] = False
            released.append(event)
    events = [{"kind": "disease_cured"}, *({"kind": e} for e in released)]
    if result.get("blindness_owned"):
        events.append({"kind": "blindness_restoration_required"})
    return {"state": result, "events": events}


def _validate_state(state: dict[str, Any]) -> None:
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or state.get("edition") != "2014"
    ):
        raise DiseaseError("disease state must be a schema-version-1 2014 instance")
    profile = disease_profile(state.get("disease_id"))
    if state.get("source_ref") != profile["source_ref"] or not state.get("actor_id"):
        raise DiseaseError("disease state source or actor identity is invalid")
    for field in ("active", "symptomatic"):
        _bool(state.get(field))


def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise DiseaseError("disease trigger requires an engine-resolved boolean")


def _die(value: Any, sides: int, message: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= sides:
        raise DiseaseError(message)
