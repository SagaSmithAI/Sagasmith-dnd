"""Shared immutable instruction form for declarative and semantic rule authors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

ALIASES = {
    "condition.add": "condition.apply",
    "hp.heal": "healing.apply",
    "effect.add": "effect.apply",
}


@dataclass(frozen=True)
class EntityRef:
    scope: Literal["actor", "self"]
    identifier: str


@dataclass(frozen=True)
class ResolutionInstruction:
    step_id: str
    opcode: str
    arguments_json: str
    targets: tuple[EntityRef, ...]
    source_id: str
    citations_json: str
    authoring_opcode: str

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)

    def receipt(self) -> dict[str, Any]:
        return {
            "ir_version": 1,
            "step_id": self.step_id,
            "opcode": self.opcode,
            "source_id": self.source_id,
            "instruction_fingerprint": hashlib.sha256(
                (self.opcode + "\n" + self.arguments_json + "\n" + self.citations_json).encode()
            ).hexdigest(),
        }


def lower_instruction(
    *,
    step_id: str,
    opcode: str,
    arguments: dict[str, Any],
    source_id: str,
    citations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> ResolutionInstruction:
    if not step_id or not source_id or not isinstance(arguments, dict):
        raise ValueError("resolution instructions require identity, source and typed arguments")
    targets = arguments.get("target_ids", [])
    if "target_id" in arguments:
        targets = [arguments["target_id"]]
    if not isinstance(targets, list) or any(not isinstance(i, str) or not i for i in targets):
        raise ValueError("resolution instruction targets must be actor identifiers")
    refs = tuple(EntityRef("actor", item) for item in targets) or (EntityRef("self", "self"),)
    return ResolutionInstruction(
        step_id,
        ALIASES.get(opcode, opcode),
        json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False),
        refs,
        source_id,
        json.dumps(citations, sort_keys=True, separators=(",", ":"), allow_nan=False),
        opcode,
    )


def execute_instruction(
    instruction: ResolutionInstruction,
    execute: Callable[[str, dict[str, Any]], Any],
) -> Any:
    """All authoring forms cross this immutable instruction boundary before execution."""
    return execute(instruction.opcode, instruction.arguments)


def resolve_conflicts(instructions: list[ResolutionInstruction]) -> list[ResolutionInstruction]:
    """Resolve declared conflicts without changing historical ordered operations."""
    selected: list[ResolutionInstruction] = []
    groups: dict[str, list[ResolutionInstruction]] = {}
    for instruction in instructions:
        conflict = instruction.arguments.get("conflict")
        if conflict is None:
            selected.append(instruction)
            continue
        if not isinstance(conflict, dict) or not str(conflict.get("key") or ""):
            raise ValueError("conflict declarations require a key")
        if conflict.get("mode") not in {"stack", "replace", "exclusive", "max"}:
            raise ValueError("unknown resolution conflict mode")
        groups.setdefault(conflict["key"], []).append(instruction)
    for key, group in groups.items():
        modes = {item.arguments["conflict"]["mode"] for item in group}
        if len(modes) != 1:
            raise ValueError(f"incompatible conflict policies for {key}")
        mode = next(iter(modes))
        if mode == "exclusive" and len(group) > 1:
            raise ValueError(f"mutually exclusive operations for {key}")
        if mode in {"stack", "exclusive"}:
            selected.extend(group)
        elif mode == "replace":
            selected.append(group[-1])
        else:
            values = [item.arguments.get("value", item.arguments.get("amount")) for item in group]
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float)) for value in values
            ):
                raise ValueError("max conflict policy requires numeric value or amount")
            selected.append(group[max(range(len(values)), key=lambda index: values[index])])
    chosen = {id(item) for item in selected}
    return [item for item in instructions if id(item) in chosen]
