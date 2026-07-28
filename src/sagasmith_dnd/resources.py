"""Shared bounded-resource mutation invariants."""

from __future__ import annotations

from typing import Any, Literal


def resize_bounded_resource(
    resource: dict[str, Any],
    *,
    maximum: int,
    unlimited: bool = False,
    previous_maximum: int | None = None,
) -> dict[str, Any]:
    """Resize one counter while preserving its already-spent capacity."""

    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError("resource maximum must be a non-negative integer")
    if previous_maximum is not None and (
        isinstance(previous_maximum, bool)
        or not isinstance(previous_maximum, int)
        or previous_maximum < 0
    ):
        raise ValueError("previous resource maximum must be a non-negative integer")
    if not isinstance(unlimited, bool):
        raise ValueError("resource unlimited must be a boolean")
    prior_maximum = (
        previous_maximum
        if previous_maximum is not None
        else int(resource.get("max", 0) or 0)
    )
    prior_value = int(resource.get("value", prior_maximum) or 0)
    if prior_value < 0 or prior_value > prior_maximum:
        raise ValueError("resource bounds are invalid")
    if unlimited:
        next_value = 0
        maximum = 0
    else:
        next_value = min(
            maximum,
            prior_value + max(0, maximum - prior_maximum),
        )
    resource["value"] = next_value
    resource["max"] = maximum
    resource["unlimited"] = unlimited
    return {
        "before": prior_value,
        "after": next_value,
        "old_max": prior_maximum,
        "new_max": maximum,
        "unlimited": unlimited,
    }


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
