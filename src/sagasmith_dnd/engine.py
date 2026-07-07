"""Small deterministic D&D mechanics layer."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

_DICE_TERM = re.compile(r"(?P<sign>[+-]?)(?P<count>\d*)d(?P<sides>\d+)|(?P<flat>[+-]?\d+)", re.I)


@dataclass(frozen=True)
class DiceResult:
    total: int
    rolls: tuple[int, ...]
    expression: str
    detail: str


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    return 2 + (max(1, level) - 1) // 4


def roll(expression: str, *, rng: random.Random | None = None) -> DiceResult:
    generator = rng or random
    normalized = expression.replace(" ", "")
    cursor = 0
    total = 0
    rolls: list[int] = []
    details: list[str] = []
    for match in _DICE_TERM.finditer(normalized):
        if match.start() != cursor:
            raise ValueError(f"invalid dice expression {expression!r}")
        cursor = match.end()
        if match.group("flat") is not None:
            value = int(match.group("flat"))
            total += value
            details.append(f"{value:+d}")
            continue
        sign = -1 if match.group("sign") == "-" else 1
        count = int(match.group("count") or 1)
        sides = int(match.group("sides"))
        if count < 1 or count > 100 or sides < 2 or sides > 1000:
            raise ValueError("dice count or size is outside supported bounds")
        term_rolls = [generator.randint(1, sides) for _ in range(count)]
        rolls.extend(term_rolls)
        subtotal = sign * sum(term_rolls)
        total += subtotal
        details.append(f"{'-' if sign < 0 else ''}{count}d{sides}{term_rolls}")
    if cursor != len(normalized):
        raise ValueError(f"invalid dice expression {expression!r}")
    return DiceResult(total, tuple(rolls), expression, " ".join(details))


def roll_d20(
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> dict:
    generator = rng or random
    values = [generator.randint(1, 20)]
    if advantage != disadvantage:
        values.append(generator.randint(1, 20))
    selected = max(values) if advantage and not disadvantage else min(values)
    if advantage == disadvantage:
        selected = values[0]
    return {
        "natural": selected,
        "rolls": values,
        "critical": selected == 20,
        "fumble": selected == 1,
    }


def resolve_check(
    *,
    dc: int,
    ability_score: int,
    proficient: bool = False,
    proficiency_multiplier: int = 1,
    level: int = 1,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> dict:
    die = roll_d20(advantage=advantage, disadvantage=disadvantage, rng=rng)
    modifier = ability_modifier(ability_score)
    proficiency = proficiency_bonus(level) * max(1, proficiency_multiplier) if proficient else 0
    total = die["natural"] + modifier + proficiency + bonus
    success = die["natural"] == 20 or (die["natural"] != 1 and total >= dc)
    return {
        **die,
        "dc": dc,
        "ability_modifier": modifier,
        "proficiency_bonus": proficiency,
        "bonus": bonus,
        "total": total,
        "success": success,
    }

