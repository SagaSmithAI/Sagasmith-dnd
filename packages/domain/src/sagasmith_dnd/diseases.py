"""Source-bound 2014 sample disease lifecycle rules.

The functions in this module are deterministic transition helpers. Runtime owns
the save rolls, authoritative creature records, clocks, persistence and commit.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from .content_validation import catalog_review_errors, content_fingerprint
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
        "save_dcs": {"infection": 13, "laughter": 13, "recovery": 13, "spread": 10},
        "eligible_creature_types": ["humanoid"],
        "immune_species": ["gnome"],
        "incubation": {"die": "1d4", "unit": "hour"},
        "symptoms": {
            "exhaustion_levels": 1,
            "laughter_duration_ticks": TICKS_PER_MINUTE,
            "psychic_damage_die": "1d10",
        },
        "source_ref": DISEASE_SOURCE_REF,
    },
    "sewer_plague": {
        "id": "sewer_plague",
        "name": "Sewer Plague",
        "save_dc": 11,
        "save_dcs": {"infection": 11, "recovery": 11},
        "eligible_creature_types": ["humanoid"],
        "incubation": {"die": "1d4", "unit": "day"},
        "symptoms": {"exhaustion_levels": 1},
        "source_ref": DISEASE_SOURCE_REF,
    },
    "sight_rot": {
        "id": "sight_rot",
        "name": "Sight Rot",
        "save_dc": 15,
        "save_dcs": {"infection": 15},
        "eligible_creature_types": ["beast", "humanoid"],
        "incubation": {"fixed": 1, "unit": "day"},
        "symptoms": {"penalty_per_long_rest": 1, "blindness_threshold": 5},
        "source_ref": DISEASE_SOURCE_REF,
    },
}


def disease_profile(value: Any) -> dict[str, Any]:
    """Return a copy of one exact 2014 bundled sample profile."""
    key = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if key not in DISEASES_2014:
        raise DiseaseError("disease is not a bundled 2014 sample disease")
    return deepcopy(DISEASES_2014[key])


def _infection_profile(value: Any, override: Mapping[str, Any] | None) -> dict[str, Any]:
    profile = disease_profile(value)
    if override is None:
        return profile
    candidate = deepcopy(dict(override))
    if candidate.get("id") != profile["id"] or candidate.get("source_ref") != profile["source_ref"]:
        raise DiseaseError("compiled disease profile does not match the bundled disease")
    receipt = candidate.get("variant_receipt")
    if not isinstance(receipt, Mapping):
        raise DiseaseError("campaign disease variants require reviewed rule-pack provenance")
    if set(candidate.get("save_dcs") or {}) != set(profile["save_dcs"]):
        raise DiseaseError("compiled disease save DCs are incomplete")
    for dc in candidate["save_dcs"].values():
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 30:
            raise DiseaseError("compiled disease save DC is invalid")
    candidate["save_dc"] = int(candidate["save_dcs"]["infection"])
    candidate["incubation"] = _normalize_variant_incubation(candidate.get("incubation"))
    candidate["symptoms"] = _normalize_variant_symptoms(
        profile["id"], profile["symptoms"], candidate.get("symptoms")
    )
    kinds = candidate.get("eligible_creature_types")
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(kind not in _CREATURE_KINDS for kind in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        raise DiseaseError("compiled disease creature kinds are invalid")
    candidate["immune_species"] = list(profile.get("immune_species", []))
    return candidate


_CREATURE_KINDS = frozenset(
    {
        "aberration",
        "beast",
        "celestial",
        "construct",
        "dragon",
        "elemental",
        "fey",
        "fiend",
        "giant",
        "humanoid",
        "monstrosity",
        "ooze",
        "plant",
        "undead",
    }
)


def normalize_disease_variant_definition(value: Any, raw_variant: Any) -> dict[str, Any]:
    """Validate and normalize the bounded, typed 2014 disease variant payload."""

    profile = disease_profile(value)
    if not isinstance(raw_variant, Mapping):
        raise DiseaseError("disease variant artifact needs a typed disease_variant object")
    variant = deepcopy(dict(raw_variant))
    allowed_fields = {
        "schema_version",
        "edition",
        "disease_id",
        "save_dcs",
        "incubation",
        "eligible_creature_types",
        "symptoms",
    }
    if set(variant) - allowed_fields or not {"schema_version", "edition", "disease_id"} <= set(
        variant
    ):
        raise DiseaseError("disease variant fields are unsupported or incomplete")
    if type(variant.get("schema_version")) is not int or variant["schema_version"] != 1:
        raise DiseaseError("disease variants require schema version 1 and edition 2014")
    if variant.get("edition") != "2014":
        raise DiseaseError("disease variants require schema version 1 and edition 2014")
    if variant.get("disease_id") != profile["id"]:
        raise DiseaseError("disease variant id does not match the selected disease")
    if len(set(variant) - {"schema_version", "edition", "disease_id"}) == 0:
        raise DiseaseError("disease variant must change at least one permitted rule field")

    if "save_dcs" in variant:
        save_dcs = variant["save_dcs"]
        allowed_saves = set(profile["save_dcs"])
        if not isinstance(save_dcs, Mapping) or not save_dcs or set(save_dcs) - allowed_saves:
            raise DiseaseError("disease variant save_dcs contains unsupported save kinds")
        normalized_saves: dict[str, int] = {}
        for save_kind, dc in dict(save_dcs).items():
            if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 30:
                raise DiseaseError("disease variant save DCs must be from 1 through 30")
            normalized_saves[str(save_kind)] = dc
        variant["save_dcs"] = normalized_saves
    if "incubation" in variant:
        variant["incubation"] = _normalize_variant_incubation(variant["incubation"])
    if "eligible_creature_types" in variant:
        kinds = variant["eligible_creature_types"]
        if (
            not isinstance(kinds, list)
            or not kinds
            or any(not isinstance(item, str) or item not in _CREATURE_KINDS for item in kinds)
            or len(set(kinds)) != len(kinds)
        ):
            raise DiseaseError("disease variant creature kinds are invalid or duplicated")
        variant["eligible_creature_types"] = list(kinds)
    if "symptoms" in variant:
        normalized = _normalize_variant_symptoms(
            profile["id"], profile["symptoms"], variant["symptoms"]
        )
        variant["symptoms"] = {
            key: normalized[key] for key in dict(variant["symptoms"])
        }
    return variant


def resolve_reviewed_disease_variant(
    value: Any,
    *,
    artifact: Mapping[str, Any],
    pack_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply one narrow, approved disease variant from an activated rule pack.

    The caller must obtain ``artifact`` from the exact immutable installed
    version in the campaign's effective branch lock. This helper verifies the
    artifact's own review/content binding and accepts only modeled 2014 fields.
    """

    profile = disease_profile(value)
    artifact_value = deepcopy(dict(artifact))
    artifact_id = str(artifact_value.get("id") or "").strip()
    raw_variant = artifact_value.get("disease_variant")
    binding = dict(pack_binding)
    pack_id = str(binding.get("pack_id") or "").strip()
    version = str(binding.get("version") or "").strip()
    checksum = str(binding.get("checksum") or "")
    if (
        artifact_value.get("kind") != "disease_variant"
        or not artifact_id
        or not pack_id
        or not version
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or not artifact_id.startswith(f"{pack_id}.")
    ):
        raise DiseaseError("disease variant must come from an exact activated rule-pack version")
    variant = normalize_disease_variant_definition(value, raw_variant)

    review = artifact_value.get("catalog_review")
    if not isinstance(review, Mapping) or review.get("status") != "approved":
        raise DiseaseError("disease variant requires an approved content review")
    review_errors = catalog_review_errors(artifact_value)
    if review_errors:
        raise DiseaseError(
            "disease variant review is stale or malformed: " + "; ".join(review_errors)
        )
    decisions = list(dict(review).get("decisions") or [])
    checks = {"identity", "classification", "entry_boundary", "references"}
    if not any(
        isinstance(item, Mapping)
        and item.get("role") == "dm"
        and isinstance(item.get("checks"), Mapping)
        and all(dict(item["checks"]).get(key) is True for key in checks)
        for item in decisions
    ):
        raise DiseaseError("disease variant requires a passing DM review decision")
    citations = artifact_value.get("source_citations")
    if not isinstance(citations, list) or not any(
        isinstance(item, Mapping) and str(item.get("source") or "").strip()
        for item in citations
    ):
        raise DiseaseError("disease variant requires a source citation")

    if "save_dcs" in variant:
        for save_kind, dc in variant["save_dcs"].items():
            profile["save_dcs"][save_kind] = dc
        profile["save_dc"] = int(profile["save_dcs"]["infection"])
    if "incubation" in variant:
        profile["incubation"] = variant["incubation"]
    if "eligible_creature_types" in variant:
        profile["eligible_creature_types"] = list(variant["eligible_creature_types"])
    if "symptoms" in variant:
        profile["symptoms"] = _normalize_variant_symptoms(
            profile["id"], profile["symptoms"], variant["symptoms"]
        )
    profile["variant_receipt"] = {
        "pack_id": pack_id,
        "version": version,
        "pack_checksum": checksum,
        "artifact_id": artifact_id,
        "artifact_content_hash": content_fingerprint(artifact_value),
        "reviewed_content_hash": str(review.get("reviewed_content_hash") or ""),
        "source_citations": deepcopy(citations),
    }
    return profile


def _normalize_variant_incubation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiseaseError("disease variant incubation must be an object")
    incubation = dict(value)
    unit = incubation.get("unit")
    if unit not in {"hour", "day"}:
        raise DiseaseError("disease variant incubation unit must be hour or day")
    if set(incubation) == {"die", "unit"}:
        die = incubation.get("die")
        match = re.fullmatch(r"1d(2|[3-9]|1[0-9]|20)", str(die or ""))
        if match is None:
            raise DiseaseError("disease variant incubation die must be a bounded 1dN")
        return {"die": f"1d{int(match.group(1))}", "unit": unit}
    if set(incubation) == {"fixed", "unit"}:
        amount = incubation.get("fixed")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 365:
            raise DiseaseError("disease variant fixed incubation must be from 1 through 365")
        return {"fixed": amount, "unit": unit}
    raise DiseaseError("disease variant incubation fields are unsupported")


def _normalize_variant_symptoms(
    disease_id: str, defaults: Mapping[str, Any], value: Any
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise DiseaseError("disease variant symptoms must be a non-empty object")
    result = deepcopy(dict(defaults))
    supplied = dict(value)
    if disease_id in {"cackle_fever", "sewer_plague"}:
        allowed = {"exhaustion_levels"}
        if disease_id == "cackle_fever":
            allowed |= {"laughter_duration_ticks", "psychic_damage_die"}
        if set(supplied) - allowed:
            raise DiseaseError("disease variant symptoms contain unsupported fields")
        if "exhaustion_levels" in supplied:
            levels = supplied["exhaustion_levels"]
            if isinstance(levels, bool) or not isinstance(levels, int) or not 1 <= levels <= 6:
                raise DiseaseError("symptom exhaustion_levels must be from 1 through 6")
            result["exhaustion_levels"] = levels
        if "laughter_duration_ticks" in supplied:
            ticks = supplied["laughter_duration_ticks"]
            if isinstance(ticks, bool) or not isinstance(ticks, int) or not 1 <= ticks <= 10:
                raise DiseaseError("laughter_duration_ticks must be from 1 through 10")
            result["laughter_duration_ticks"] = ticks
        if "psychic_damage_die" in supplied:
            die = supplied["psychic_damage_die"]
            if die not in {"1d4", "1d6", "1d8", "1d10", "1d12"}:
                raise DiseaseError("psychic_damage_die is unsupported")
            result["psychic_damage_die"] = die
    elif disease_id == "sight_rot":
        if set(supplied) - {"penalty_per_long_rest", "blindness_threshold"}:
            raise DiseaseError("disease variant symptoms contain unsupported fields")
        for field in ("penalty_per_long_rest", "blindness_threshold"):
            if field not in supplied:
                continue
            amount = supplied[field]
            if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10:
                raise DiseaseError(f"{field} must be an integer from 1 through 10")
            result[field] = amount
    else:  # pragma: no cover - profiles are closed above
        raise DiseaseError("disease variant symptoms are unsupported")
    return result


def infection_state(
    disease: Any,
    *,
    actor_id: Any,
    elapsed_ticks: Any,
    incubation_roll: Any = None,
    save_succeeded: Any,
    profile_override: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create infection state after an already-resolved authoritative save.

    The caller must validate source, eligibility, exposure, edition and save
    provenance before calling. Incubation is requested only after failure.
    """
    profile = _infection_profile(disease, profile_override)
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
        sides = _die_sides(incubation["die"])
        if (
            isinstance(incubation_roll, bool)
            or not isinstance(incubation_roll, int)
            or not 1 <= incubation_roll <= sides
        ):
            raise DiseaseError(
                f"failed infection requires one engine-owned {incubation['die']} incubation result"
            )
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
        "disease_rules": {
            "save_dcs": deepcopy(profile["save_dcs"]),
            "incubation": deepcopy(incubation),
            "eligible_creature_types": list(profile["eligible_creature_types"]),
            "symptoms": deepcopy(profile["symptoms"]),
        },
    }
    if profile.get("variant_receipt"):
        state["variant_receipt"] = deepcopy(profile["variant_receipt"])
    if profile["id"] == "cackle_fever":
        state.update(
            {
                "laughter_dc": int(profile["save_dcs"]["laughter"]),
                "recovery_dc": int(profile["save_dcs"]["recovery"]),
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
            result["symptom_exhaustion_levels"] = int(
                _state_rules(result)["symptoms"]["exhaustion_levels"]
            )
        elif result["disease_id"] == "sewer_plague":
            result["symptom_exhaustion_owned"] = True
            result["symptom_exhaustion_levels"] = int(
                _state_rules(result)["symptoms"]["exhaustion_levels"]
            )
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
    symptoms = _state_rules(result)["symptoms"]
    damage_die = str(symptoms["psychic_damage_die"])
    _die(
        psychic_damage_roll,
        _die_sides(damage_die),
        f"failed Cackle Fever stress save requires a {damage_die} psychic damage roll",
    )
    result["laughing_until_elapsed_ticks"] = elapsed_ticks + int(
        symptoms["laughter_duration_ticks"]
    )
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


def cackle_fever_save_dc(state: dict[str, Any], *, save_kind: Any) -> int:
    """Return the current source-owned DC for one Cackle Fever saving throw."""
    _validate_state(state)
    if state["disease_id"] != "cackle_fever" or not state["active"]:
        raise DiseaseError("Cackle Fever save DC requires an active disease instance")
    field = {
        "laughter": "laughter_dc",
        "recovery": "recovery_dc",
    }.get(str(save_kind or "").strip().casefold())
    if field is None:
        raise DiseaseError("Cackle Fever save kind must be laughter or recovery")
    value = state.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 30:
        raise DiseaseError(f"Cackle Fever {field} must be an integer from 0 through 30")
    return value


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
    profile_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve spread from a currently laughing carrier at turn start.

    Creature taxonomy, distance and turn-start are authoritative normalized
    facts supplied by Runtime; this function does not infer them from names.
    """
    carrier = deepcopy(carrier_state)
    _validate_state(carrier)
    profile = _infection_profile("cackle_fever", profile_override)
    spread_dc = int(profile["save_dcs"]["spread"])
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
    if creature_type not in profile["eligible_creature_types"]:
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
                "dc": spread_dc,
                "infection": None,
                "immunity": immunity,
            }
    if type(save_succeeded) is not bool:
        raise DiseaseError(f"eligible exposure requires an engine-resolved DC {spread_dc} save")
    if save_succeeded:
        if incubation_roll is not None:
            raise DiseaseError("successful spread save consumes no incubation die")
        granted = cackle_carrier_immunity(
            target_id=target_actor_id,
            carrier_id=carrier["actor_id"],
            elapsed_ticks=elapsed_ticks,
        )
        return {"status": "saved", "dc": spread_dc, "infection": None, "immunity": granted}
    infection = infection_state(
        "cackle_fever",
        actor_id=target_actor_id,
        elapsed_ticks=elapsed_ticks,
        save_succeeded=False,
        incubation_roll=incubation_roll,
        profile_override=(profile if profile.get("variant_receipt") else None),
    )
    return {"status": "infected", "dc": spread_dc, "infection": infection, "immunity": None}


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
            if recovery_die is not None or madness_d100 is not None:
                raise DiseaseError("skipping Cackle Fever recovery consumes no disease dice")
            return {"state": result, "events": [{"kind": "recovery_skipped"}]}
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
            events = [
                {"kind": "dc_reduced", "amount": amount, "cured": not result["active"]}
            ]
            if not result["active"] and result.get("laughing"):
                result["laughing"] = False
                result["incapacitation_owned"] = False
                result.pop("laughing_until_elapsed_ticks", None)
                events.append({"kind": "mad_laughter_ended", "condition": "incapacitated"})
            return {
                "state": result,
                "events": events,
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
        symptoms = _state_rules(result)["symptoms"]
        threshold = int(symptoms["blindness_threshold"])
        result["sight_penalty"] = min(
            threshold,
            int(result["sight_penalty"]) + int(symptoms["penalty_per_long_rest"]),
        )
        if result["sight_penalty"] >= threshold and not result["blindness_owned"]:
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
    threshold = int(_state_rules(state)["symptoms"]["blindness_threshold"])
    if isinstance(penalty, bool) or not isinstance(penalty, int) or not 0 <= penalty <= threshold:
        raise DiseaseError(f"Sight Rot penalty must be an integer from 0 through {threshold}")
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
    if key == "cackle_fever" and result.get("laughing"):
        result["laughing"] = False
        result["incapacitation_owned"] = False
        result.pop("laughing_until_elapsed_ticks", None)
        events.append({"kind": "mad_laughter_ended", "condition": "incapacitated"})
    if result.get("blindness_owned"):
        events.append({"kind": "blindness_restoration_required"})
    return {"state": result, "events": events}


def disease_save_dc(state: dict[str, Any], *, save_kind: Any) -> int:
    """Return an infection or recurring-save DC from this disease instance."""
    _validate_state(state)
    kind = str(save_kind or "").strip().casefold()
    save_dcs = _state_rules(state)["save_dcs"]
    value = save_dcs.get(kind)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
        raise DiseaseError("disease save kind is not defined by this instance")
    return value


def disease_symptom_exhaustion_levels(state: dict[str, Any]) -> int:
    """Return the onset exhaustion delta pinned to one disease instance."""
    _validate_state(state)
    if state["disease_id"] not in {"cackle_fever", "sewer_plague"}:
        return 0
    return int(_state_rules(state)["symptoms"]["exhaustion_levels"])


def cackle_psychic_damage_die(state: dict[str, Any]) -> str:
    """Return the damage die bound to one active Cackle Fever instance."""
    _validate_state(state)
    if state["disease_id"] != "cackle_fever" or not state["active"]:
        raise DiseaseError("Cackle Fever damage die requires an active disease instance")
    return str(_state_rules(state)["symptoms"]["psychic_damage_die"])


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
    _state_rules(state)
    for field in ("active", "symptomatic"):
        _bool(state.get(field))


def _state_rules(state: Mapping[str, Any]) -> dict[str, Any]:
    profile = disease_profile(state.get("disease_id"))
    raw = state.get("disease_rules")
    if raw is None:
        return {
            "save_dcs": deepcopy(profile["save_dcs"]),
            "incubation": deepcopy(profile["incubation"]),
            "eligible_creature_types": list(profile["eligible_creature_types"]),
            "symptoms": deepcopy(profile["symptoms"]),
        }
    if not isinstance(raw, Mapping) or set(raw) != {
        "save_dcs",
        "incubation",
        "eligible_creature_types",
        "symptoms",
    }:
        raise DiseaseError("disease instance rules are malformed")
    value = dict(raw)
    save_dcs = value["save_dcs"]
    if not isinstance(save_dcs, Mapping) or set(save_dcs) != set(profile["save_dcs"]):
        raise DiseaseError("disease instance save DCs are malformed")
    for dc in save_dcs.values():
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 30:
            raise DiseaseError("disease instance save DC is invalid")
    kinds = value["eligible_creature_types"]
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(kind not in _CREATURE_KINDS for kind in kinds)
        or len(set(kinds)) != len(kinds)
    ):
        raise DiseaseError("disease instance creature kinds are malformed")
    normalized = {
        "save_dcs": dict(save_dcs),
        "incubation": _normalize_variant_incubation(value["incubation"]),
        "eligible_creature_types": list(kinds),
        "symptoms": _normalize_variant_symptoms(
            profile["id"], profile["symptoms"], value["symptoms"]
        ),
    }
    if not state.get("variant_receipt") and normalized != {
        "save_dcs": profile["save_dcs"],
        "incubation": profile["incubation"],
        "eligible_creature_types": profile["eligible_creature_types"],
        "symptoms": profile["symptoms"],
    }:
        raise DiseaseError("unreviewed disease instance rule override is not permitted")
    if state.get("variant_receipt"):
        receipt = state["variant_receipt"]
        if (
            not isinstance(receipt, Mapping)
            or not str(receipt.get("pack_id") or "").strip()
            or not str(receipt.get("version") or "").strip()
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("pack_checksum") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("artifact_content_hash") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("reviewed_content_hash") or ""))
        ):
            raise DiseaseError("disease variant provenance receipt is malformed")
    return normalized


def _die_sides(die: Any) -> int:
    match = re.fullmatch(r"1d([1-9][0-9]?)", str(die or ""))
    if match is None:
        raise DiseaseError("disease die must be a single bounded dN")
    sides = int(match.group(1))
    if not 2 <= sides <= 100:
        raise DiseaseError("disease die must have 2 through 100 sides")
    return sides


def _bool(value: Any) -> None:
    if type(value) is not bool:
        raise DiseaseError("disease trigger requires an engine-resolved boolean")


def _die(value: Any, sides: int, message: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= sides:
        raise DiseaseError(message)
