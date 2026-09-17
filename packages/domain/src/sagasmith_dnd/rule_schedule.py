"""Deterministic dependency stages for event rule settlement."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def dependency_stages(
    entries: Iterable[T], *, identity: Callable[[T], str],
    dependencies: Callable[[T], Iterable[str]],
    order: Callable[[T], object],
) -> tuple[tuple[T, ...], ...]:
    pending = {}
    for entry in entries:
        key = identity(entry)
        if not key or key in pending:
            raise ValueError("rule identities must be present and unique")
        pending[key] = entry
    for entry in pending.values():
        if not set(dependencies(entry)) <= pending.keys():
            raise ValueError("rule dependencies must exist in the same event")
    stages = []
    while pending:
        ready = sorted((entry for entry in pending.values()
                        if not set(dependencies(entry)) & pending.keys()), key=order)
        if not ready:
            raise ValueError("rule dependency ordering contains a cycle")
        stages.append(tuple(ready))
        for entry in ready:
            del pending[identity(entry)]
    return tuple(stages)
