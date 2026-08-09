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


__all__ = [
    "HeroicInspirationError",
    "grant_heroic_inspiration",
    "spend_heroic_inspiration_reroll",
]
