"""Safe declarative D&D rule-extension compiler and pure settlement hooks."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from sagasmith_dnd.core_rule_pack import BuiltinCoreRulePack, get_core_rule_pack

ALLOWED_EVENTS = {
    "character.validate",
    "character.derive",
    "activity.before",
    "activity.after",
    "spell.before",
    "spell.after",
    "rest.before",
    "rest.after",
    "attack.preflight",
    "attack.after",
    "check.before",
    "turn.end",
    "duration.advance",
}

ALLOWED_OPS = {
    "resource.spend",
    "resource.recover",
    "hp.heal",
    "hp.temp.set",
    "condition.add",
    "condition.remove",
    "effect.add",
    "effect.remove",
    "spell_slot.spend",
    "spell_slot.recover",
    "modifier.add",
    "advantage.add",
    "disadvantage.add",
    "choice.require",
    "ruling.require",
}

READ_ONLY_EVENTS = {"attack.preflight", "check.before", "character.validate", "character.derive"}
READ_ONLY_OPS = {
    "modifier.add",
    "advantage.add",
    "disadvantage.add",
    "choice.require",
    "ruling.require",
}
ATOMIC_AFTER_EVENTS = {"attack.after", "turn.end", "duration.advance"}


class RuleCompilationError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CompiledMechanic:
    id: str
    event: str
    predicates: tuple[dict[str, Any], ...]
    operations: tuple[dict[str, Any], ...]
    citations: tuple[dict[str, Any], ...]
    priority: int = 0


@dataclass(frozen=True)
class ResolutionContext:
    fingerprint: str
    core_pack: BuiltinCoreRulePack
    mechanics: tuple[CompiledMechanic, ...]
    options: dict[str, Any]
    facts: dict[str, Any]


@dataclass(frozen=True)
class RuleEventResult:
    sheet: dict[str, Any]
    status: str
    receipts: tuple[dict[str, Any], ...]
    modifiers: tuple[dict[str, Any], ...]
    pending: tuple[dict[str, Any], ...]


class CoreRuleProvider:
    """Compatibility adapter around the current hard-coded 2014/2024 behavior."""

    def __init__(self, edition: str) -> None:
        self.pack = get_core_rule_pack(edition)
        self.edition = self.pack.edition

    @property
    def id(self) -> str:
        return self.pack.id

    @property
    def fingerprint(self) -> str:
        return self.pack.fingerprint

    def receipt(self, boundary_id: str, event: str) -> dict[str, Any]:
        return self.pack.receipt(boundary_id, event)


def compile_mechanics(
    values: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[CompiledMechanic, ...]:
    result: list[CompiledMechanic] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise RuleCompilationError("each mechanic must be an object")
        mechanic_id = str(value.get("id") or "")
        event = str(value.get("event") or "")
        if not mechanic_id or mechanic_id in seen:
            raise RuleCompilationError("mechanic ids must be present and unique")
        if event not in ALLOWED_EVENTS:
            raise RuleCompilationError(f"unsupported mechanic event: {event}")
        operations = _object_sequence(value.get("operations", []), "operations")
        if not operations:
            raise RuleCompilationError(f"{mechanic_id} has no operations")
        for operation in operations:
            opcode = str(operation.get("op") or "")
            if opcode not in ALLOWED_OPS:
                raise RuleCompilationError(f"unsupported mechanic operation: {opcode}")
            _validate_operation(operation)
            _validate_event_operation(event, operation)
            if event in READ_ONLY_EVENTS and opcode not in READ_ONLY_OPS:
                raise RuleCompilationError(f"{event} cannot mutate character state")
            if event in ATOMIC_AFTER_EVENTS and opcode in {"choice.require", "ruling.require"}:
                raise RuleCompilationError(f"{event} cannot pause after random settlement")
        predicates = _object_sequence(value.get("predicates", []), "predicates")
        for predicate in predicates:
            _validate_predicate(predicate)
        citations = _object_sequence(value.get("citations", []), "citations")
        if not citations or any(not str(item.get("source") or "") for item in citations):
            raise RuleCompilationError(f"{mechanic_id} needs at least one source citation")
        priority = value.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise RuleCompilationError(f"{mechanic_id} priority must be an integer")
        result.append(
            CompiledMechanic(
                id=mechanic_id,
                event=event,
                predicates=predicates,
                operations=operations,
                citations=citations,
                priority=priority,
            )
        )
        seen.add(mechanic_id)
    return tuple(sorted(result, key=lambda item: (item.priority, item.id)))


def validate_source_bound_mechanics(
    values: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    source_id: str | None = None,
) -> tuple[CompiledMechanic, ...]:
    """Validate canonical citations resolved from a Core rule-document source."""
    compiled = compile_mechanics(values)
    for mechanic in compiled:
        for citation in mechanic.citations:
            if not str(citation.get("source") or "").startswith("rule-source:"):
                raise RuleCompilationError(
                    f"{mechanic.id} source-bound citation needs rule-source provenance"
                )
            citation_source_id = str(citation.get("source_id") or "")
            if not citation_source_id or (
                source_id is not None and citation_source_id != source_id
            ):
                raise RuleCompilationError(
                    f"{mechanic.id} citation is not bound to the requested source"
                )
            if not str(citation.get("source_key") or ""):
                raise RuleCompilationError(f"{mechanic.id} citation needs source_key")
            if not _SHA256_RE.fullmatch(str(citation.get("source_checksum") or "")):
                raise RuleCompilationError(
                    f"{mechanic.id} citation needs a SHA-256 source_checksum"
                )
            if not str(citation.get("chunk_id") or ""):
                raise RuleCompilationError(f"{mechanic.id} citation needs chunk_id")
            heading_path = citation.get("heading_path")
            if not isinstance(heading_path, list) or any(
                not isinstance(item, str) for item in heading_path
            ):
                raise RuleCompilationError(
                    f"{mechanic.id} citation heading_path must be a string list"
                )
            page_start = citation.get("page_start")
            page_end = citation.get("page_end")
            if page_start is not None and (
                isinstance(page_start, bool)
                or not isinstance(page_start, int)
                or page_start < 1
            ):
                raise RuleCompilationError(f"{mechanic.id} citation page_start is invalid")
            if page_end is not None and (
                isinstance(page_end, bool) or not isinstance(page_end, int) or page_end < 1
            ):
                raise RuleCompilationError(f"{mechanic.id} citation page_end is invalid")
            if page_start is not None and page_end is not None and page_end < page_start:
                raise RuleCompilationError(
                    f"{mechanic.id} citation page range is reversed"
                )
    return compiled


def resolution_context(effective: Any, *, facts: dict[str, Any] | None = None) -> ResolutionContext:
    mechanics = getattr(effective, "mechanics", None)
    fingerprint = getattr(effective, "fingerprint", None)
    lock = getattr(effective, "lock", None)
    if isinstance(effective, dict):
        mechanics = effective.get("mechanics", [])
        fingerprint = effective.get("fingerprint", "")
        lock = effective.get("lock", [])
        edition = effective.get("edition", "")
    else:
        edition = getattr(effective, "edition", "")
    options = {
        str(item["pack_id"]): dict(item.get("options") or {})
        for item in lock or []
    }
    core_pack = get_core_rule_pack(str(edition or ""))
    combined_fingerprint = _combined_fingerprint(core_pack.fingerprint, str(fingerprint or ""))
    return ResolutionContext(
        fingerprint=combined_fingerprint,
        core_pack=core_pack,
        mechanics=compile_mechanics(tuple(mechanics or ())),
        options=options,
        facts=dict(facts or {}),
    )


def context_with_facts(
    context: ResolutionContext | None, **facts: Any
) -> ResolutionContext | None:
    if context is None:
        return None
    return replace(context, facts={**context.facts, **facts})


def core_receipts(
    context: ResolutionContext | None, boundary_ids: list[str] | tuple[str, ...], event: str
) -> list[dict[str, Any]]:
    if context is None:
        return []
    receipts = [context.core_pack.receipt(boundary_id, event) for boundary_id in boundary_ids]
    for receipt in receipts:
        receipt["core_pack_fingerprint"] = context.core_pack.fingerprint
        receipt["ruleset_fingerprint"] = context.fingerprint
    return receipts


def run_mechanic_tests(
    mechanics: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    tests: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    fingerprint: str = "validation",
) -> dict[str, Any]:
    """Run pack-supplied positive/negative examples without executing code."""
    context = ResolutionContext(
        fingerprint,
        get_core_rule_pack("2014"),
        compile_mechanics(mechanics),
        {},
        {},
    )
    cases: list[dict[str, Any]] = []
    exercised: set[str] = set()
    for index, case in enumerate(tests):
        if not isinstance(case, dict):
            cases.append(
                {
                    "name": f"case-{index + 1}",
                    "passed": False,
                    "errors": ["test case must be an object"],
                }
            )
            continue
        name = str(case.get("name") or f"case-{index + 1}")
        try:
            event = str(case.get("event") or "")
            if event not in ALLOWED_EVENTS:
                raise RuleCompilationError(f"unsupported test event: {event}")
            sheet_value = case.get("sheet") or {}
            facts_value = case.get("facts") or {}
            expectations = case.get("expect") or []
            if not isinstance(sheet_value, dict) or not isinstance(facts_value, dict):
                raise RuleCompilationError("test sheet and facts must be objects")
            if not isinstance(expectations, list) or any(
                not isinstance(item, dict) for item in expectations
            ):
                raise RuleCompilationError("test expect must be a list of objects")
            sheet = deepcopy(sheet_value)
            case_context = replace(context, facts=dict(facts_value))
            result = apply_rule_event(sheet, event, case_context)
            expected_status = str(case.get("expected_status") or "committed")
            errors: list[str] = []
            if result.status != expected_status:
                errors.append(f"expected status {expected_status}, got {result.status}")
            exercised.update(str(item["mechanic_id"]) for item in result.receipts)
            exercised.update(str(item["mechanic_id"]) for item in result.pending)
            for expectation in expectations:
                actual = _read_path(result.sheet, str(expectation.get("path") or ""))
                if actual != expectation.get("equals"):
                    errors.append(
                        f"{expectation.get('path')} expected {expectation.get('equals')!r}, "
                        f"got {actual!r}"
                    )
            cases.append({"name": name, "passed": not errors, "errors": errors})
        except (RuleCompilationError, TypeError, ValueError) as error:
            cases.append({"name": name, "passed": False, "errors": [str(error)]})
    mechanic_ids = {str(item.get("id") or "") for item in mechanics}
    uncovered = sorted(mechanic_ids - exercised)
    return {
        "passed": (
            bool(cases)
            and all(case["passed"] for case in cases)
            and not uncovered
        ),
        "total": len(cases),
        "cases": cases,
        "mechanics_exercised": sorted(exercised),
        "mechanics_uncovered": uncovered,
    }


def apply_rule_event(
    sheet: dict[str, Any], event: str, context: ResolutionContext | None
) -> RuleEventResult:
    if context is None:
        return RuleEventResult(deepcopy(sheet), "committed", (), (), ())
    original = deepcopy(sheet)
    value = deepcopy(sheet)
    receipts: list[dict[str, Any]] = []
    modifiers: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for mechanic in context.mechanics:
        if mechanic.event != event or not _matches(mechanic.predicates, context, value):
            continue
        applied: list[dict[str, Any]] = []
        for operation in mechanic.operations:
            opcode = operation["op"]
            if opcode in {"choice.require", "ruling.require"}:
                pending.append({"mechanic_id": mechanic.id, **deepcopy(operation)})
                continue
            if opcode in {"modifier.add", "advantage.add", "disadvantage.add"}:
                modifiers.append({"mechanic_id": mechanic.id, **deepcopy(operation)})
                applied.append(deepcopy(operation))
                continue
            _apply_sheet_operation(value, operation)
            applied.append(deepcopy(operation))
        receipts.append(
            {
                "mechanic_id": mechanic.id,
                "event": event,
                "operations": applied,
                "citations": [deepcopy(item) for item in mechanic.citations],
                "ruleset_fingerprint": context.fingerprint,
            }
        )
    if pending:
        status = (
            "pending_choice"
            if any(item["op"] == "choice.require" for item in pending)
            else "pending_ruling"
        )
        for receipt in receipts:
            receipt["committed"] = False
        return RuleEventResult(original, status, tuple(receipts), tuple(modifiers), tuple(pending))
    return RuleEventResult(value, "committed", tuple(receipts), tuple(modifiers), ())


def _validate_operation(operation: dict[str, Any]) -> None:
    opcode = operation["op"]
    if opcode.startswith("resource."):
        path = str(operation.get("path") or "")
        if (
            not path.startswith("resources.")
            or not path.removeprefix("resources.")
            or ".." in path
        ):
            raise RuleCompilationError("resource operations require a resources.<key> path")
    if opcode.startswith("spell_slot."):
        level = operation.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 9:
            raise RuleCompilationError("spell-slot operations require level 1..9")
    amount_operations = {
        "resource.spend",
        "resource.recover",
        "hp.heal",
        "spell_slot.spend",
        "spell_slot.recover",
    }
    if opcode in amount_operations:
        amount = operation.get("amount", 1)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise RuleCompilationError("operation amount must be a non-negative integer")
    if opcode == "hp.temp.set":
        value = operation.get("value", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuleCompilationError("hp.temp.set value must be a non-negative integer")
    if opcode == "modifier.add":
        value = operation.get("value", 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuleCompilationError("modifier.add value must be an integer")
        if not str(operation.get("target") or ""):
            raise RuleCompilationError("modifier.add requires target")
    if opcode in {"effect.add", "condition.add", "condition.remove", "effect.remove"}:
        if not str(operation.get("id") or ""):
            raise RuleCompilationError(f"{opcode} requires id")
    if opcode in {"choice.require", "ruling.require"} and not str(
        operation.get("id") or ""
    ):
        raise RuleCompilationError(f"{opcode} requires id")


def _object_sequence(value: Any, field: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise RuleCompilationError(f"mechanic {field} must be a list")
    if any(not isinstance(item, dict) for item in value):
        raise RuleCompilationError(f"mechanic {field} entries must be objects")
    return tuple(dict(item) for item in value)


def _validate_event_operation(event: str, operation: dict[str, Any]) -> None:
    opcode = str(operation.get("op") or "")
    if opcode == "modifier.add":
        target = str(operation.get("target") or "")
        allowed_targets = {
            "attack.preflight": {"attack_bonus", "target_ac"},
            "check.before": {"check_bonus"},
            "character.derive": {"armor_class", "initiative", "passive_perception"},
        }.get(event, set())
        if target not in allowed_targets:
            raise RuleCompilationError(
                f"{event} cannot consume modifier target {target or '<empty>'}"
            )
    if opcode in {"advantage.add", "disadvantage.add"} and event not in {
        "attack.preflight",
        "check.before",
    }:
        raise RuleCompilationError(f"{event} cannot consume {opcode}")
    if opcode == "effect.add" and not isinstance(operation.get("effect", {}), dict):
        raise RuleCompilationError("effect.add effect must be an object")


def _validate_predicate(predicate: dict[str, Any]) -> None:
    kind = str(predicate.get("kind") or "")
    if kind == "fact_equals":
        if not str(predicate.get("key") or ""):
            raise RuleCompilationError("fact_equals requires key")
    elif kind == "sheet_equals":
        if not str(predicate.get("path") or ""):
            raise RuleCompilationError("sheet_equals requires path")
    elif kind == "has_condition":
        if not str(predicate.get("id") or ""):
            raise RuleCompilationError("has_condition requires id")
    elif kind == "option_equals":
        if not str(predicate.get("pack_id") or "") or not str(
            predicate.get("key") or ""
        ):
            raise RuleCompilationError("option_equals requires pack_id and key")
    else:
        raise RuleCompilationError(f"unsupported predicate: {kind}")


def _matches(
    predicates: tuple[dict[str, Any], ...], context: ResolutionContext, sheet: dict[str, Any]
) -> bool:
    for predicate in predicates:
        kind = str(predicate.get("kind") or "")
        if kind == "fact_equals":
            if context.facts.get(str(predicate.get("key") or "")) != predicate.get("value"):
                return False
        elif kind == "sheet_equals":
            if _read_path(sheet, str(predicate.get("path") or "")) != predicate.get("value"):
                return False
        elif kind == "has_condition":
            conditions = {str(item).casefold() for item in sheet.get("conditions", [])}
            if str(predicate.get("id") or "").casefold() not in conditions:
                return False
        elif kind == "option_equals":
            pack_id = str(predicate.get("pack_id") or "")
            option = context.options.get(pack_id, {}).get(str(predicate.get("key") or ""))
            if option != predicate.get("value"):
                return False
        else:
            raise RuleCompilationError(f"unsupported predicate: {kind}")
    return True


def _apply_sheet_operation(sheet: dict[str, Any], operation: dict[str, Any]) -> None:
    opcode = operation["op"]
    amount = int(operation.get("amount", 1) or 0)
    if opcode.startswith("resource."):
        key = operation["path"].split(".", 1)[1]
        resource = sheet.setdefault("resources", {}).get(key)
        if not isinstance(resource, dict):
            raise RuleCompilationError(f"resource does not exist: {key}")
        current = int(resource.get("value", 0) or 0)
        maximum = int(resource.get("max", current) or current)
        if opcode == "resource.spend":
            if current < amount:
                raise RuleCompilationError(f"resource is exhausted: {key}")
            resource["value"] = current - amount
        else:
            resource["value"] = min(maximum, current + amount)
    elif opcode == "hp.heal":
        hp = sheet.setdefault("combat", {}).setdefault("hp", {})
        hp["value"] = min(int(hp.get("max", 0) or 0), int(hp.get("value", 0) or 0) + amount)
    elif opcode == "hp.temp.set":
        hp = sheet.setdefault("combat", {}).setdefault("hp", {})
        hp["temp"] = max(int(hp.get("temp", 0) or 0), int(operation.get("value", 0) or 0))
    elif opcode in {"condition.add", "condition.remove"}:
        conditions = list(sheet.get("conditions", []))
        condition_id = str(operation["id"])
        if opcode == "condition.add" and condition_id not in conditions:
            conditions.append(condition_id)
        if opcode == "condition.remove":
            conditions = [item for item in conditions if str(item) != condition_id]
        sheet["conditions"] = conditions
    elif opcode == "effect.add":
        effect = deepcopy(operation.get("effect") or {})
        effect.setdefault("id", operation["id"])
        if any(item.get("id") == effect["id"] for item in sheet.get("effects", [])):
            raise RuleCompilationError(f"effect already exists: {effect['id']}")
        sheet.setdefault("effects", []).append(effect)
    elif opcode == "effect.remove":
        sheet["effects"] = [
            item for item in sheet.get("effects", []) if item.get("id") != operation["id"]
        ]
    elif opcode.startswith("spell_slot."):
        slots = sheet.setdefault("spellcasting", {}).setdefault("spell_slots", {})
        key = str(operation["level"])
        resource = slots.get(key) or slots.get(f"spell{key}")
        if not isinstance(resource, dict):
            raise RuleCompilationError(f"spell slot does not exist: {key}")
        current = int(resource.get("value", 0) or 0)
        if opcode == "spell_slot.spend":
            if current < amount:
                raise RuleCompilationError(f"spell slot is exhausted: {key}")
            resource["value"] = current - amount
        else:
            resource["value"] = min(int(resource.get("max", current) or current), current + amount)


def _read_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not part or not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _combined_fingerprint(core_fingerprint: str, extension_fingerprint: str) -> str:
    import hashlib

    return hashlib.sha256(
        f"{core_fingerprint}:{extension_fingerprint}".encode("utf-8")
    ).hexdigest()
