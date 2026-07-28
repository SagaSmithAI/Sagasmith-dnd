"""Shared bounded-resource mutation invariants."""

from __future__ import annotations

from typing import Any, Literal


def mutate_bounded_resource(
    resource: dict[str, Any],
    *,
    amount: int,
    direction: Literal["spend", "recover"],
) -> dict[str, Any]:
    """Mutate one structured counter without inventing contextual recovery rules."""

    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValueError("resource amount must be a non-negative integer")
    if direction not in {"spend", "recover"}:
        raise ValueError("resource direction must be spend or recover")
    current = int(resource.get("value", 0) or 0)
    maximum = int(resource.get("max", current) or current)
    if current < 0 or maximum < 0 or current > maximum:
        raise ValueError("resource bounds are invalid")
    unlimited = bool(resource.get("unlimited", False))
    if unlimited:
        if current != 0 or maximum != 0:
            raise ValueError("unlimited resources must have zero value and max")
        return {
            "before": 0,
            "after": 0,
            "amount": 0,
            "unlimited": True,
        }
    if direction == "spend":
        if current < amount:
            raise ValueError("resource is exhausted")
        after = current - amount
    else:
        after = min(maximum, current + amount)
    resource["value"] = after
    return {
        "before": current,
        "after": after,
        "amount": abs(after - current),
        "unlimited": False,
    }
