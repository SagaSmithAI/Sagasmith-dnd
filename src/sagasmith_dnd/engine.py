"""Small deterministic D&D mechanics layer.

The engine deliberately separates ordinary D20 tests from attack rolls and
death saves.  Natural 1/20 handling is not a generic ``resolve_check`` rule:
it belongs to the specific test type.
"""

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
    reroll_ones: bool = False,
    rng: random.Random | None = None,
) -> dict:
    generator = rng or random
    advantage_applied = bool(advantage and not disadvantage)
    disadvantage_applied = bool(disadvantage and not advantage)
    values = [generator.randint(1, 20)]
    if advantage_applied or disadvantage_applied:
        values.append(generator.randint(1, 20))
    rerolls = []
    if reroll_ones and 1 in values:
        # The advantage/disadvantage rule permits only one of its d20s to be
        # rerolled. Halfling Lucky must then keep the replacement result.
        index = values.index(1)
        replacement = generator.randint(1, 20)
        values[index] = replacement
        rerolls.append(
            {
                "index": index,
                "from": 1,
                "to": replacement,
                "source": "halfling_lucky",
            }
        )
    selected = max(values) if advantage_applied else min(values)
    if not advantage_applied and not disadvantage_applied:
        selected = values[0]
    return {
        "natural": selected,
        "rolls": values,
        "rerolls": rerolls,
        "roll_mode": (
            "advantage"
            if advantage_applied
            else "disadvantage"
            if disadvantage_applied
            else "normal"
        ),
        "advantage_applied": advantage_applied,
        "disadvantage_applied": disadvantage_applied,
        "critical": selected == 20,
        "fumble": selected == 1,
    }


def resolve_check(
    *,
    dc: int,
    ability_score: int,
    proficient: bool = False,
    level: int = 1,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    kind: str = "ability",
    reroll_ones: bool = False,
    rng: random.Random | None = None,
) -> dict:
    """Resolve an ability check or saving throw.

    Ordinary ability checks and saving throws succeed when the total meets the
    DC.  Natural 1/20 are only special for attacks and death saves, so callers
    must use :func:`resolve_attack` or :func:`resolve_death_save` for those.
    """
    if kind not in {"ability", "save"}:
        raise ValueError("resolve_check kind must be ability or save")
    die = roll_d20(
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_ones=reroll_ones,
        rng=rng,
    )
    modifier = ability_modifier(ability_score)
    proficiency = proficiency_bonus(level) if proficient else 0
    total = die["natural"] + modifier + proficiency + bonus
    success = total >= dc
    return {
        **die,
        "kind": kind,
        "dc": dc,
        "ability_modifier": modifier,
        "proficiency_bonus": proficiency,
        "bonus": bonus,
        "total": total,
        "success": success,
    }


def resolve_attack(
    *,
    armor_class: int,
    attack_bonus: int,
    advantage: bool = False,
    disadvantage: bool = False,
    reroll_ones: bool = False,
    rng: random.Random | None = None,
) -> dict:
    """Resolve an attack roll with attack-specific natural 1/20 semantics."""
    die = roll_d20(
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_ones=reroll_ones,
        rng=rng,
    )
    total = die["natural"] + attack_bonus
    hit = bool(die["critical"] or (not die["fumble"] and total >= armor_class))
    return {
        **die,
        "kind": "attack",
        "armor_class": armor_class,
        "attack_bonus": attack_bonus,
        "total": total,
        "hit": hit,
    }


def resolve_death_save(
    *,
    successes: int = 0,
    failures: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    bonus: int = 0,
    reroll_ones: bool = False,
    rng: random.Random | None = None,
) -> dict:
    """Resolve a death save, including natural 1/20 special cases."""
    die = roll_d20(
        advantage=advantage,
        disadvantage=disadvantage,
        reroll_ones=reroll_ones,
        rng=rng,
    )
    next_successes = successes
    next_failures = failures
    outcome = "pending"
    if die["natural"] == 20:
        outcome = "revived"
        next_successes = 0
        next_failures = 0
    elif die["natural"] == 1:
        next_failures += 2
    elif die["natural"] + int(bonus) >= 10:
        next_successes += 1
    else:
        next_failures += 1
    if outcome == "pending" and next_successes >= 3:
        outcome = "stable"
    elif outcome == "pending" and next_failures >= 3:
        outcome = "dead"
    return {
        **die,
        "bonus": int(bonus),
        "total": die["natural"] + int(bonus),
        "kind": "death_save",
        "successes": next_successes,
        "failures": next_failures,
        "outcome": outcome,
    }
