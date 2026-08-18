"""Allowlisted native rule-provider seam for mechanics outside declarative IR."""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from typing import Any, Protocol

RULE_PROVIDER_ABI = 1
ENTRY_POINT_GROUP = "sagasmith.dnd.rule_providers"


class NativeRuleProvider(Protocol):
    id: str
    pack_id: str
    abi_version: int

    def mechanics(self) -> list[dict[str, Any]]: ...


def load_native_rule_providers() -> dict[str, NativeRuleProvider]:
    """Load only providers manually installed and named in the process allowlist."""
    allowed = {
        item.strip()
        for item in os.environ.get("SAGASMITH_DND_RULE_PROVIDER_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if not allowed:
        return {}
    result: dict[str, NativeRuleProvider] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.name not in allowed:
            continue
        provider = entry_point.load()()
        if int(getattr(provider, "abi_version", 0)) != RULE_PROVIDER_ABI:
            raise RuntimeError(f"native rule provider ABI mismatch: {entry_point.name}")
        provider_id = str(getattr(provider, "id", ""))
        pack_id = str(getattr(provider, "pack_id", ""))
        if not provider_id or not pack_id or provider_id in result:
            raise RuntimeError("native rule providers need unique ids")
        result[provider_id] = provider
    missing = sorted(allowed - set(result))
    if missing:
        raise RuntimeError(
            f"allowlisted native rule providers are unavailable: {', '.join(missing)}"
        )
    return result
