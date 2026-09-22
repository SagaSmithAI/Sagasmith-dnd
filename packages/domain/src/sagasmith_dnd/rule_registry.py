"""Edition-aware registration of core and already resolved extension definitions.

Pack dependencies and checksum-guarded patches are resolved once by Core's
RulePackService. This layer must not introduce a second override authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .editions import SUPPORTED_DND_EDITIONS, normalize_dnd_edition
from .immutable_rules import ImmutableRuleFields


@dataclass(frozen=True)
class RuleRegistration(ImmutableRuleFields):
    snapshot_fields = ("definition",)
    id: str
    kind: str
    event: str
    source: str
    editions: tuple[str, ...]
    definition: dict[str, Any]

    def __post_init__(self):
        if not self.id or not self.event:
            raise ValueError("registered rules require identity and event")
        if not self.source:
            raise ValueError("registered rules require a source citation")
        if not self.editions or any(item not in SUPPORTED_DND_EDITIONS for item in self.editions):
            raise ValueError("registered rules require supported explicit editions")
        super().__post_init__()


@dataclass(frozen=True)
class RuleRegistry:
    registrations: tuple[RuleRegistration, ...]

    def select(self, edition: str) -> tuple[RuleRegistration, ...]:
        """Choose compatible definitions; no implicit last-pack-wins override."""
        edition = normalize_dnd_edition(edition)
        selected = {}
        for item in self.registrations:
            if edition not in item.editions:
                continue
            if item.id in selected:
                raise ValueError(f"duplicate rule registration: {item.id}")
            selected[item.id] = item
        return tuple(selected.values())


def compose_mechanics(values: list[dict[str, Any]], edition: str) -> list[dict[str, Any]]:
    registrations = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("each mechanic must be an object")
        if "replaces" in value:
            raise ValueError("use a checksum-guarded rule-pack patch for replacement")
        editions = value.get("editions", [edition])
        if not isinstance(editions, (list, tuple)):
            raise ValueError("mechanic editions must be a list")
        citations = value.get("citations") or []
        source = next((str(item.get("source") or "") for item in citations
                       if isinstance(item, dict)), "")
        registrations.append(RuleRegistration(
            id=str(value.get("id") or ""), kind="event", event=str(value.get("event") or ""),
            source=source, editions=tuple(editions), definition=value,
        ))
    registry = RuleRegistry(tuple(registrations))
    selected = registry.select(edition)
    # Core already verified these patch targets and checksums. Resolve references
    # to the removed definition to the active patch before dependency validation.
    replacements = {item.definition["patch"]["target"]: item.id for item in selected
                    if item.definition.get("patch")}
    result = []
    for item in selected:
        definition = item.definition
        dependencies = []
        for key in definition.get("after", []):
            visited = set()
            while key in replacements:
                if key in visited:
                    raise ValueError("rule patch dependency contains a cycle")
                visited.add(key)
                key = replacements[key]
            dependencies.append(key)
        definition["after"] = dependencies
        result.append(definition)
    return result
