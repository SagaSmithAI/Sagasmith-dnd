"""2014 inanimate objects: bounded profiles and damage without creature state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .character_schema import DAMAGE_TYPES, default_character_sheet
from .combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    _critical_expression,
    _end_attack_broken_invisibility,
    preflight_attack,
)
from .conditions import INCAPACITATING_STATE_IDS, condition_ids
from .engine import roll
from .rule_engine import ResolutionContext, apply_rule_event, context_with_facts, core_receipts

OBJECT_RULE = "dnd5e.core.objects.damage"
PROFILE_FIELDS = frozenset(
    {
        "id",
        "name",
        "scene_id",
        "armor_class",
        "hit_points",
        "material",
        "size",
        "resilience",
        "damage_threshold",
        "damage_immunities",
        "damage_resistances",
        "damage_vulnerabilities",
        "damage_filter",
        "section_of",
        "fully_immersed",
    }
)


def _integer(value: Any, field: str, minimum: int, maximum: int = 1_000_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"object {field} must be an integer from {minimum} to {maximum}")
    return value


def _text(value: Any, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"object {field} must contain 1 to {maximum} characters")
    return value.strip()


def _strings(value: Any, field: str, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"object {field} must be a list of at most 100 strings")
    values = sorted({_text(item, field) for item in value})
    if allowed is not None and set(values) - allowed:
        raise ValueError(f"object {field} contains unsupported values")
    return values


def validate_object_profile(value: Any) -> dict[str, Any]:
    """Validate reviewed facts; never infer a material, AC, HP, or applicability."""
    if not isinstance(value, dict) or set(value) - PROFILE_FIELDS:
        raise ValueError("unsupported source object fields")
    result = {
        field: _text(value.get(field), field)
        for field in ("id", "name", "scene_id", "material", "size", "resilience")
    }
    if result["size"] not in {"tiny", "small", "medium", "large"}:
        raise ValueError(
            "object size must be Large or smaller; divide larger objects into sections"
        )
    if result["resilience"] not in {"fragile", "resilient"}:
        raise ValueError("object resilience must be fragile or resilient")
    if any(value[field] != result[field] for field in ("id", "scene_id")):
        raise ValueError("object identifiers cannot contain surrounding whitespace")
    result["armor_class"] = _integer(value.get("armor_class"), "armor_class", 1, 30)
    result["hit_points"] = _integer(value.get("hit_points"), "hit_points", 1)
    result["damage_threshold"] = _integer(value.get("damage_threshold", 0), "damage_threshold", 0)
    if "fully_immersed" in value:
        if type(value["fully_immersed"]) is not bool:
            raise ValueError("object fully_immersed must be a boolean")
        result["fully_immersed"] = value["fully_immersed"]
    for defense in ("immunities", "resistances", "vulnerabilities"):
        key = f"damage_{defense}"
        result[key] = _strings(value.get(key, []), key, set(DAMAGE_TYPES))
    result["damage_immunities"] = sorted(set(result["damage_immunities"]) | {"poison", "psychic"})
    damage_filter = value.get("damage_filter", {})
    filter_fields = {"allowed_damage_types", "required_any_weapon_traits", "allowed_weapon_ids"}
    if not isinstance(damage_filter, dict) or set(damage_filter) - filter_fields:
        raise ValueError("unsupported source object damage_filter fields")
    result["damage_filter"] = {
        "allowed_damage_types": _strings(
            damage_filter.get("allowed_damage_types", []), "allowed_damage_types", set(DAMAGE_TYPES)
        ),
        "required_any_weapon_traits": _strings(
            damage_filter.get("required_any_weapon_traits", []),
            "required_any_weapon_traits",
            {"magical", "adamantine"},
        ),
        "allowed_weapon_ids": _strings(
            damage_filter.get("allowed_weapon_ids", []), "allowed_weapon_ids"
        ),
    }
    section = value.get("section_of")
    if section is not None:
        if not isinstance(section, dict) or set(section) != {"id", "size"}:
            raise ValueError("object section_of requires only id and size")
        parent_id = _text(section["id"], "section_of.id")
        if parent_id == result["id"] or section["size"] not in {"huge", "gargantuan"}:
            raise ValueError("object section_of must identify a separate Huge or Gargantuan object")
        result["section_of"] = {"id": parent_id, "size": section["size"]}
    return result


def object_attack_plan(
    attacker: dict[str, Any],
    profile: dict[str, Any],
    *,
    weapon_id: str,
    advantage: bool = False,
    disadvantage: bool = False,
    rules: ResolutionContext | None = None,
    reviewed_long_range: bool | None = None,
) -> dict[str, Any]:
    """Use only the shared attack preflight; the object never enters creature settlement."""
    profile = validate_object_profile(profile)
    if attacker["sheet"].get("edition") != "2014":
        raise NeedsRulingError(
            "source object attacks require the 2014 object rules", missing=("object.edition",)
        )
    if (
        int(attacker["sheet"]["combat"]["hp"]["value"]) <= 0
        or condition_ids(attacker["sheet"].get("conditions")) & INCAPACITATING_STATE_IDS
    ):
        raise CombatEngineError("incapacitated actors cannot attack source objects")
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    # This disposable shell is only an AC input for attack preflight. Neither
    # its conditions, HP, creature hooks nor zero-HP machinery are settled.
    target = {
        "id": f"scene-object:{profile['scene_id']}:{profile['id']}",
        "kind": "object",
        "object_id": profile["id"],
        "sheet": sheet,
        "derived": {"armor_class": profile["armor_class"]},
        "death_saves": False,
    }
    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": weapon_id,
            "context": {
                "advantage": advantage,
                "disadvantage": disadvantage,
                "target_can_see_attacker": True,
                "sunlight": dict(rules.facts).get("_sunlight") if rules else None,
            },
        },
        require_attack_action=False,
        rules=rules,
        reviewed_object_long_range=reviewed_long_range,
    )
    unsupported = [
        key
        for key in (
            "on_hit_effect",
            "additional_damage",
            "standard_on_hit_mechanics",
            "weapon_mastery",
            "sneak_attack",
            "resource_cost",
        )
        if plan.get(key)
    ]
    if unsupported:
        raise NeedsRulingError(
            "weapon effects need an object-specific source resolver",
            missing=tuple(f"object.{key}" for key in unsupported),
        )
    if rules and any(mechanic.event == "attack.after" for mechanic in rules.mechanics):
        # Do not silently drop or run a generic creature on-hit hook. Check the
        # possible outcomes on copies before dice; object-scoped source support
        # is required for any matching extension rather than guessing its intent.
        for hit, critical in ((False, False), (True, False), (True, True)):
            preview = apply_rule_event(
                attacker["sheet"],
                "attack.after",
                context_with_facts(
                    rules,
                    kind="attack",
                    target_kind="object",
                    hit=hit,
                    critical=critical,
                    attacker_id=attacker["id"],
                    target_id=target["id"],
                    subject="attacker",
                ),
            )
            if preview.receipts:
                raise NeedsRulingError(
                    "attack extensions need an object-specific source resolver",
                    missing=tuple(r["mechanic_id"] for r in preview.receipts),
                )
    return plan


def apply_object_damage(
    profile: dict[str, Any],
    hit_points: int,
    parts: list[dict[str, Any]],
    *,
    weapon_id: str,
    weapon_traits: list[str],
) -> dict[str, Any]:
    """Adjust each type, then compare the single attack/effect total with its threshold."""
    profile = validate_object_profile(profile)
    before = _integer(hit_points, "remaining hit_points", 0, profile["hit_points"])
    damage_filter = profile["damage_filter"]
    traits_met = not damage_filter["required_any_weapon_traits"] or bool(
        set(weapon_traits) & set(damage_filter["required_any_weapon_traits"])
    )
    weapon_met = (
        not damage_filter["allowed_weapon_ids"] or weapon_id in damage_filter["allowed_weapon_ids"]
    )
    # Combine equal types before rounding resistance, so splitting a damage
    # packet cannot change its result. Immunity always wins over vulnerability.
    grouped: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"amount", "damage_type"}:
            raise ValueError("object damage parts require amount and damage_type")
        kind = part["damage_type"]
        if kind not in DAMAGE_TYPES:
            raise ValueError("object damage_type is unsupported")
        grouped[kind] = grouped.get(kind, 0) + _integer(part["amount"], "damage amount", 0)
    adjustments = []
    for kind, amount in grouped.items():
        immune = kind in profile["damage_immunities"]
        applicable = (
            traits_met
            and weapon_met
            and (
                not damage_filter["allowed_damage_types"]
                or kind in damage_filter["allowed_damage_types"]
            )
        )
        resistant = kind in profile["damage_resistances"] or (
            kind == "fire" and profile.get("fully_immersed", False)
        )
        vulnerable = kind in profile["damage_vulnerabilities"]
        adjusted = 0 if immune or not applicable else (amount // 2 if resistant else amount)
        if vulnerable:
            adjusted *= 2
        adjustments.append(
            {
                "damage_type": kind,
                "amount": amount,
                "adjusted_amount": adjusted,
                "immune": immune,
                "applicable": applicable,
                "resistant": resistant,
                "vulnerable": vulnerable,
            }
        )
    total = sum(part["adjusted_amount"] for part in adjustments)
    threshold_met = total >= profile["damage_threshold"]
    damage = total if threshold_met else 0
    after = max(0, before - damage)
    return {
        "amount": sum(grouped.values()),
        "adjusted_amount": total,
        "applied_amount": damage,
        "hp_loss": before - after,
        "hit_points_before": before,
        "hit_points_after": after,
        "destroyed": after == 0,
        "damage_threshold": profile["damage_threshold"],
        "threshold_met": threshold_met,
        "parts": adjustments,
        "weapon_trait_requirement_met": traits_met,
        "weapon_applicability_met": weapon_met,
    }


def resolve_object_attack(
    attacker: dict[str, Any],
    profile: dict[str, Any],
    hit_points: int,
    *,
    plan: dict[str, Any],
    attack: dict[str, Any],
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Settle a simple weapon hit with no creature target, saves, traits or conditions."""
    updated = deepcopy(attacker)
    result = deepcopy(attack)
    result["damage"] = None
    facts = dict(plan.get("attack_facts") or {})
    weapon_traits = sorted(
        ({"magical"} if facts.get("magical") else set())
        | (set(facts.get("materials") or []) & {"adamantine"})
    )
    result["weapon_traits"] = weapon_traits
    if attack["hit"]:
        expression = str(plan["damage_expression"])
        rolled_expression = _critical_expression(expression) if attack["critical"] else expression
        damage_roll = roll(rolled_expression, rng=rng)
        result["damage"] = {
            **apply_object_damage(
                profile,
                hit_points,
                [{"amount": max(0, damage_roll.total), "damage_type": plan["damage_type"]}],
                weapon_id=plan["weapon_id"],
                weapon_traits=weapon_traits,
            ),
            "expression": expression,
            "rolled_expression": rolled_expression,
            "rolls": list(damage_roll.rolls),
            "detail": damage_roll.detail,
        }
    ended = _end_attack_broken_invisibility(updated["sheet"])
    if ended:
        result["ended_invisibility_effect_ids"] = ended
    # An attack still ends Bladesong when it uses two hands, even on a miss.
    if plan.get("weapon_grip") == "two_handed":
        for effect in updated["sheet"].get("effects", []):
            if effect.get("active") and effect.get("metadata", {}).get("scag_bladesong"):
                effect.update(active=False, ended_reason="two_handed_attack")
    result["rule_receipts"] = [
        *plan.get("rule_receipts", []),
        *core_receipts(rules, [OBJECT_RULE], "object.attack.resolve"),
    ]
    if profile.get("fully_immersed") and plan["damage_type"] == "fire" and attack["hit"]:
        from .water import WATER_RULE

        result["rule_receipts"].extend(core_receipts(rules, [WATER_RULE], "object.damage.fire"))
    return updated, result
