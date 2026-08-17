"""SRD 5.2.1 Heroic Inspiration grant, transfer, and reroll primitives."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.character_schema import validate_character_sheet
from sagasmith_dnd.engine import roll


class HeroicInspirationError(ValueError):
    """Raised when an inspiration grant or reroll is not rules-legal."""


def grant_heroic_inspiration(
    sheet: dict[str, Any],
    *,
    recipient_sheet: dict[str, Any] | None = None,
    recipient_is_player_character: bool = False,
) -> dict[str, Any]:
    """Grant Heroic Inspiration, optionally transferring a duplicate grant."""

    source = deepcopy(validate_character_sheet(sheet))
    if not bool(source["combat"].get("inspiration", False)):
        source["combat"]["inspiration"] = True
        return {
            "sheet": validate_character_sheet(source),
            "recipient_sheet": deepcopy(recipient_sheet),
            "outcome": "granted",
        }
    if recipient_sheet is None:
        return {
            "sheet": source,
            "recipient_sheet": None,
            "outcome": "duplicate_lost",
        }
    if not recipient_is_player_character:
        raise HeroicInspirationError(
            "a duplicate Heroic Inspiration grant can transfer only to a player character"
        )
    recipient = deepcopy(validate_character_sheet(recipient_sheet))
    if bool(recipient["combat"].get("inspiration", False)):
        return {
            "sheet": source,
            "recipient_sheet": recipient,
            "outcome": "duplicate_lost",
        }
    recipient["combat"]["inspiration"] = True
    return {
        "sheet": source,
        "recipient_sheet": validate_character_sheet(recipient),
        "outcome": "transferred",
    }


def spend_heroic_inspiration_reroll(
    sheet: dict[str, Any],
    *,
    die_sides: int,
    original_roll: int,
    rng: Any = None,
) -> dict[str, Any]:
    """Spend Heroic Inspiration and replace one just-rolled die with a new result."""

    if isinstance(die_sides, bool) or not isinstance(die_sides, int) or not 2 <= die_sides <= 1000:
        raise HeroicInspirationError("die_sides must be an integer between 2 and 1000")
    if (
        isinstance(original_roll, bool)
        or not isinstance(original_roll, int)
        or not 1 <= original_roll <= die_sides
    ):
        raise HeroicInspirationError("original_roll is outside the recorded die")
    value = deepcopy(validate_character_sheet(sheet))
    if not bool(value["combat"].get("inspiration", False)):
        raise HeroicInspirationError("the actor has no Heroic Inspiration")
    reroll = roll(f"1d{die_sides}", rng=rng)
    value["combat"]["inspiration"] = False
    return {
        "sheet": validate_character_sheet(value),
        "die_sides": die_sides,
        "original_roll": original_roll,
        "new_roll": int(reroll.total),
        "must_use_new_roll": True,
        "roll": {
            "expression": reroll.expression,
            "rolls": list(reroll.rolls),
            "total": reroll.total,
            "detail": reroll.detail,
        },
    }


def reroll_recorded_d20_result(
    sheet: dict[str, Any],
    recorded_result: dict[str, Any],
    *,
    roll_index: int,
    expected_original_roll: int,
    rng: Any = None,
) -> dict[str, Any]:
    """Spend Heroic Inspiration and rebuild one canonical recorded d20 result."""

    if isinstance(roll_index, bool) or not isinstance(roll_index, int) or roll_index < 0:
        raise HeroicInspirationError("roll_index must be a non-negative integer")
    original = deepcopy(dict(recorded_result))
    rolls = list(original.get("rolls") or [])
    if (
        roll_index >= len(rolls)
        or isinstance(rolls[roll_index], bool)
        or int(rolls[roll_index]) != int(expected_original_roll)
    ):
        raise HeroicInspirationError(
            "roll_index and expected_original_roll must match the recorded die"
        )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20
        for value in rolls
    ):
        raise HeroicInspirationError("recorded d20 rolls must contain integers between 1 and 20")
    natural_before = original.get("natural")
    total_before = original.get("total")
    if (
        isinstance(natural_before, bool)
        or not isinstance(natural_before, int)
        or isinstance(total_before, bool)
        or not isinstance(total_before, int)
    ):
        raise HeroicInspirationError("recorded d20 result requires integer natural and total")
    spent = spend_heroic_inspiration_reroll(
        sheet,
        die_sides=20,
        original_roll=int(expected_original_roll),
        rng=rng,
    )
    rolls[roll_index] = int(spent["new_roll"])
    roll_mode = str(original.get("roll_mode") or "normal")
    if roll_mode not in {"normal", "advantage", "disadvantage"}:
        raise HeroicInspirationError("recorded d20 result has an unsupported roll_mode")
    natural = (
        max(rolls)
        if roll_mode == "advantage"
        else min(rolls)
        if roll_mode == "disadvantage"
        else rolls[0]
    )
    modifier = int(total_before) - int(natural_before)
    result = {
        **original,
        "rolls": rolls,
        "natural": natural,
        "critical": natural == 20,
        "fumble": natural == 1,
        "total": natural + modifier,
    }
    if isinstance(original.get("dc"), int) and not isinstance(original.get("dc"), bool):
        result["success"] = int(result["total"]) >= int(original["dc"])
    return {
        "sheet": spent["sheet"],
        "result": result,
        "heroic_inspiration_reroll": {
            key: value for key, value in spent.items() if key != "sheet"
        },
    }


__all__ = [
    "HeroicInspirationError",
    "grant_heroic_inspiration",
    "reroll_recorded_d20_result",
    "spend_heroic_inspiration_reroll",
]
