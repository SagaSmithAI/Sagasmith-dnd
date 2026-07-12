"""Structured ability-score generation rules for D&D character creation."""

from __future__ import annotations

import copy
import random
from typing import Any

ABILITY_NAMES = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)

ABILITY_GENERATION_RULESETS = {
    "dnd5e-2014": {
        "edition": "2014",
        "standard_array": (15, 14, 13, 12, 10, 8),
        "point_buy": {
            "budget": 27,
            "minimum": 8,
            "maximum": 15,
            "costs": {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9},
        },
        "roll": {"count": 6, "dice": 4, "sides": 6, "drop_lowest": 1},
    }
}


def ruleset_for_edition(edition: str) -> tuple[str, dict[str, Any]]:
    for ruleset_id, ruleset in ABILITY_GENERATION_RULESETS.items():
        if ruleset["edition"] == edition:
            return ruleset_id, ruleset
    raise ValueError(f"no ability generation ruleset is configured for edition {edition}")


def roll_ability_scores(
    edition: str,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    ruleset_id, ruleset = ruleset_for_edition(edition)
    generator = rng or random
    roll_rule = ruleset["roll"]
    values = []
    for _ in range(roll_rule["count"]):
        dice = [generator.randint(1, roll_rule["sides"]) for _ in range(roll_rule["dice"])]
        dropped = min(dice)
        values.append({"dice": dice, "dropped": dropped, "score": sum(dice) - dropped})
    return {"ruleset": ruleset_id, "method": "roll_4d6_drop_lowest", "rolls": values}


def apply_ability_generation(
    sheet: dict[str, Any],
    *,
    method: str,
    assignments: dict[str, Any],
    rolls: list[Any] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(sheet)
    edition = result["edition"]
    ruleset_id, ruleset = ruleset_for_edition(edition)
    normalized_assignments = _assignments(assignments)

    if method == "standard_array":
        expected = sorted(ruleset["standard_array"])
        if sorted(normalized_assignments.values()) != expected:
            raise ValueError("assignments must use the standard array exactly once")
        record = {
            "ruleset": ruleset_id,
            "method": method,
            "assignments": normalized_assignments,
            "point_buy": None,
            "rolls": [],
        }
    elif method == "point_buy":
        point_buy = ruleset["point_buy"]
        costs = point_buy["costs"]
        if any(score not in costs for score in normalized_assignments.values()):
            raise ValueError("point-buy assignments must be between 8 and 15")
        spent = sum(costs[score] for score in normalized_assignments.values())
        if spent != point_buy["budget"]:
            raise ValueError(
                f"point-buy assignments must spend exactly {point_buy['budget']} points"
            )
        record = {
            "ruleset": ruleset_id,
            "method": method,
            "assignments": normalized_assignments,
            "point_buy": {"budget": point_buy["budget"], "spent": spent},
            "rolls": [],
        }
    elif method == "roll_4d6_drop_lowest":
        normalized_rolls = _rolls(rolls, ruleset["roll"])
        if sorted(normalized_assignments.values()) != sorted(
            item["score"] for item in normalized_rolls
        ):
            raise ValueError("rolled assignments must use every generated score exactly once")
        record = {
            "ruleset": ruleset_id,
            "method": method,
            "assignments": normalized_assignments,
            "point_buy": None,
            "rolls": normalized_rolls,
        }
    else:
        raise ValueError("unsupported ability generation method")

    for ability, score in normalized_assignments.items():
        result["abilities"][ability]["score"] = score
    result["ability_generation"] = record
    return result


def normalize_ability_generation(value: Any, edition: str) -> dict[str, Any]:
    if value is None:
        return {
            "ruleset": "",
            "method": "unrecorded",
            "assignments": {},
            "point_buy": None,
            "rolls": [],
        }
    if not isinstance(value, dict):
        raise ValueError("sheet.ability_generation must be an object")
    allowed = {"ruleset", "method", "assignments", "point_buy", "rolls"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"sheet.ability_generation has unsupported fields: {', '.join(unknown)}")
    method = value.get("method", "unrecorded")
    if method == "unrecorded":
        return {
            "ruleset": "",
            "method": "unrecorded",
            "assignments": {},
            "point_buy": None,
            "rolls": [],
        }
    assignments = value.get("assignments")
    if not isinstance(assignments, dict):
        raise ValueError("sheet.ability_generation.assignments must be an object")
    result = apply_ability_generation(
        {
            "edition": edition,
            "abilities": {ability: {"score": 10} for ability in ABILITY_NAMES},
        },
        method=method,
        assignments=assignments,
        rolls=value.get("rolls"),
    )["ability_generation"]
    if value.get("ruleset", result["ruleset"]) != result["ruleset"]:
        raise ValueError("sheet.ability_generation.ruleset does not match the sheet edition")
    if value.get("point_buy") != result["point_buy"]:
        raise ValueError("sheet.ability_generation.point_buy does not match assignments")
    return result


def _assignments(value: dict[str, Any]) -> dict[str, int]:
    if set(value) != set(ABILITY_NAMES):
        raise ValueError("assignments must contain each ability exactly once")
    result: dict[str, int] = {}
    for ability in ABILITY_NAMES:
        score = value[ability]
        if isinstance(score, bool) or not isinstance(score, int):
            raise ValueError(f"assignment for {ability} must be an integer")
        result[ability] = score
    return result


def _rolls(value: list[Any] | None, rule: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != rule["count"]:
        raise ValueError(f"rolled generation requires exactly {rule['count']} rolls")
    normalized = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"dice", "dropped", "score"}:
            raise ValueError("each rolled ability score must contain dice, dropped, and score")
        dice = entry["dice"]
        if not isinstance(dice, list) or len(dice) != rule["dice"]:
            raise ValueError(f"each roll must contain {rule['dice']} dice")
        invalid_die = any(
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 1 <= item <= rule["sides"]
            for item in dice
        )
        if invalid_die:
            raise ValueError(f"rolled dice must be integers between 1 and {rule['sides']}")
        dropped = entry["dropped"]
        score = entry["score"]
        if dropped != min(dice) or score != sum(dice) - dropped:
            raise ValueError("rolled ability score does not match 4d6 drop-lowest")
        normalized.append({"dice": list(dice), "dropped": dropped, "score": score})
    return normalized
