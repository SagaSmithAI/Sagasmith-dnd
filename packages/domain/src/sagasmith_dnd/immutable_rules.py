"""Canonical JSON snapshots for compiled rule definitions.

Public access returns detached values so existing dict/list authoring adapters
can keep their wire format. The compiled record owns only immutable JSON text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class _Snapshot:
    text: str
    tuple_root: bool = False

    def read(self):
        value = json.loads(self.text)
        return tuple(value) if self.tuple_root else value


class ImmutableRuleFields:
    snapshot_fields: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self):
        for name in self.snapshot_fields:
            value = object.__getattribute__(self, name)
            object.__setattr__(self, name, _Snapshot(
                json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
                isinstance(value, tuple),
            ))

    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        return value.read() if isinstance(value, _Snapshot) else value
