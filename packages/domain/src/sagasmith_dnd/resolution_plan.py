"""Source-bound semantic plans executed through a small set of trusted primitives.

Rule cards own the fixed plan template and its evidence.  An Agent may only fill
declared, typed slots; it cannot add operations or arbitrary state patches.  The
bound plan is validated completely before a runtime transaction begins.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from sagasmith_dnd.save_context import validated_save_source_facts

SEMANTIC_PLAN_VERSION = 2

SOURCE_CARD_KINDS = frozenset(
    {
        "activity",
        "feature",
        "item",
        "monster_action",
        "scene_procedure",
        "spell",
        "trait",
    }
)
PLAN_TRIGGERS = frozenset(
    {
        "action",
        "attack.after_hit",
        "damage.after",
        "rest.after",
        "rest.before",
        "scene",
        "turn.end",
        "turn.start",
    }
)
TRIGGER_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "action": frozenset(
        {
            "action_kind",
            "action_ref",
            "actor_id",
            "branch_id",
            "campaign_id",
            "round",
            "turn",
        }
    ),
    "attack.after_hit": frozenset(
        {
            "application_id",
            "attack_ref",
            "branch_id",
            "campaign_id",
            "critical",
            "hit",
            "round",
            "source_actor_id",
            "target_actor_id",
            "turn",
            "weapon_id",
        }
    ),
    "damage.after": frozenset(
        {
            "application_id",
            "branch_id",
            "campaign_id",
            "damage_type",
            "source_actor_id",
            "source_ref",
            "target_actor_id",
        }
    ),
    "rest.after": frozenset(
        {
            "actor_id",
            "branch_id",
            "campaign_id",
            "rest_kind",
        }
    ),
    "rest.before": frozenset(
        {
            "actor_id",
            "branch_id",
            "campaign_id",
            "rest_kind",
        }
    ),
    "scene": frozenset(
        {
            "branch_id",
            "campaign_id",
            "scene_id",
            "scope_id",
        }
    ),
    "turn.end": frozenset(
        {
            "actor_id",
            "branch_id",
            "campaign_id",
            "round",
            "turn",
        }
    ),
    "turn.start": frozenset(
        {
            "actor_id",
            "branch_id",
            "campaign_id",
            "round",
            "turn",
        }
    ),
}
PLAN_OPS = frozenset(
    {
        "actor.control",
        "actor.link",
        "actor.unlink",
        "attack.ac_bonus",
        "attack.resolve",
        "check.ability",
        "check.contest",
        "check.save",
        "condition.apply",
        "condition.remove",
        "damage.apply",
        "effect.apply",
        "effect.remove",
        "healing.apply",
        "knowledge.transfer",
        "movement.force",
        "movement.move",
        "resource.recover",
        "resource.spend",
        "roll.table",
        "state.assert",
        "target.validate",
        "world.counter.adjust",
        "world.counter.set",
    }
)
SLOT_KINDS = frozenset(
    {
        "ability",
        "actor_id",
        "actor_ids",
        "boolean",
        "condition_id",
        "damage_type",
        "dice",
        "duration",
        "enum",
        "integer",
        "position",
        "knowledge_ids",
        "text",
    }
)
AGENT_SLOT_OWNERS = frozenset({"agent", "external_input"})

_ABILITY_IDS = frozenset(
    {
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    }
)
_DICE_RE = re.compile(r"^[1-9]\d*d[1-9]\d*(?:[+-]\d+)?$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")
_RESULT_REF_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")

_STEP_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "attack.ac_bonus": (
        frozenset({"bonus", "attack_modes"}),
        frozenset(
            {
                "bonus",
                "attack_modes",
                "requires_visible_attacker",
                "requires_wielded_melee_weapon",
            }
        ),
    ),
    "roll.table": (
        frozenset({"table"}),
        frozenset({"table", "roll_id", "exclude"}),
    ),
    "target.validate": (
        frozenset({"source_actor_id", "target_ids"}),
        frozenset(
            {
                "source_actor_id",
                "target_ids",
                "exclude_self",
                "forbid_conditions",
                "maximum_range_ft",
                "require_conditions",
                "require_visible",
                "source",
            }
        ),
    ),
    "check.save": (
        frozenset({"target_ids", "ability", "dc"}),
        frozenset(
            {
                "target_ids",
                "ability",
                "dc",
                "advantage",
                "disadvantage",
                "source",
                "success_damage",
            }
        ),
    ),
    "check.ability": (
        frozenset({"actor_id", "ability", "dc"}),
        frozenset(
            {
                "actor_id",
                "ability",
                "dc",
                "proficient",
                "bonus",
                "advantage",
                "disadvantage",
                "source",
            }
        ),
    ),
    "check.contest": (
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "source_ability",
                "target_ability",
            }
        ),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "source_ability",
                "target_ability",
                "source_proficient",
                "target_proficient",
                "source_bonus",
                "target_bonus",
                "source_advantage",
                "source_disadvantage",
                "target_advantage",
                "target_disadvantage",
            }
        ),
    ),
    "attack.resolve": (
        frozenset({"source_actor_id", "target_actor_id", "attack_ref"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "attack_ref",
                "attack_mode",
                "context",
            }
        ),
    ),
    "damage.apply": (
        frozenset({"target_ids", "damage_type", "source"}),
        frozenset(
            {
                "target_ids",
                "expression",
                "amount",
                "damage_type",
                "source",
                "critical",
                "reduction",
            }
        ),
    ),
    "healing.apply": (
        frozenset({"target_ids", "source"}),
        frozenset({"target_ids", "expression", "amount", "source"}),
    ),
    "condition.apply": (
        frozenset({"target_ids", "condition_id", "source"}),
        frozenset(
            {
                "target_ids",
                "condition_id",
                "source",
                "effect_id",
                "duration",
                "repeat_save",
                "source_actor_id",
            }
        ),
    ),
    "condition.remove": (
        frozenset({"target_ids", "condition_id"}),
        frozenset({"target_ids", "condition_id", "source"}),
    ),
    "effect.apply": (
        frozenset({"target_ids", "effect_id", "effect"}),
        frozenset({"target_ids", "effect_id", "effect", "source"}),
    ),
    "effect.remove": (
        frozenset({"target_ids", "effect_id"}),
        frozenset({"target_ids", "effect_id", "source"}),
    ),
    "resource.spend": (
        frozenset({"actor_id", "resource_ref", "amount"}),
        frozenset({"actor_id", "resource_ref", "amount", "source"}),
    ),
    "resource.recover": (
        frozenset({"actor_id", "resource_ref", "amount"}),
        frozenset({"actor_id", "resource_ref", "amount", "source"}),
    ),
    "movement.move": (
        frozenset({"actor_id"}),
        frozenset({"actor_id", "distance_ft", "destination", "path", "source"}),
    ),
    "movement.force": (
        frozenset({"source_actor_id", "target_actor_id", "distance_ft"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "distance_ft",
                "direction",
                "destination",
                "source",
            }
        ),
    ),
    "actor.link": (
        frozenset({"source_actor_id", "target_actor_id", "link_kind"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "link_kind",
                "properties",
                "source",
            }
        ),
    ),
    "actor.unlink": (
        frozenset({"source_actor_id", "target_actor_id", "link_kind"}),
        frozenset({"source_actor_id", "target_actor_id", "link_kind", "source"}),
    ),
    "actor.control": (
        frozenset({"controller_actor_id", "target_actor_id", "mode"}),
        frozenset(
            {
                "controller_actor_id",
                "target_actor_id",
                "mode",
                "mental_ability_source",
                "source",
            }
        ),
    ),
    "knowledge.transfer": (
        frozenset({"from_actor_id", "to_actor_id", "knowledge_ids"}),
        frozenset(
            {
                "from_actor_id",
                "to_actor_id",
                "knowledge_ids",
                "reason",
                "source",
            }
        ),
    ),
    "world.counter.adjust": (
        frozenset({"key", "amount"}),
        frozenset({"key", "amount", "minimum", "maximum", "source"}),
    ),
    "world.counter.set": (
        frozenset({"key", "value"}),
        frozenset({"key", "value", "source"}),
    ),
    "state.assert": (
        frozenset({"subject", "operator", "expected"}),
        frozenset({"subject", "operator", "expected", "message"}),
    ),
}


class ResolutionPlanError(ValueError):
    """Base error for source-bound semantic plans."""


class ResolutionPlanCompilationError(ResolutionPlanError):
    """The rule-card template is malformed or unsafe."""


class ResolutionPlanBindingError(ResolutionPlanError):
    """Agent or external bindings do not satisfy the rule-card contract."""


class ResolutionPlanExecutionError(ResolutionPlanError):
    """A fully bound plan could not settle atomically."""


@dataclass(frozen=True)
class CompiledResolutionPlan:
    schema_version: int
    id: str
    source_card_id: str
    source_card_kind: str
    trigger: str
    trigger_filter: dict[str, Any]
    slots: dict[str, dict[str, Any]]
    steps: tuple[dict[str, Any], ...]
    citations: tuple[dict[str, Any], ...]
    fingerprint: str


@dataclass(frozen=True)
class BoundResolutionPlan:
    compiled: CompiledResolutionPlan
    bindings: dict[str, Any]
    trigger_filter: dict[str, Any]
    steps: tuple[dict[str, Any], ...]
    agent_ruling: dict[str, Any] | None
    fingerprint: str


@dataclass(frozen=True)
class ResolutionPlanResult:
    status: str
    plan_id: str
    plan_fingerprint: str
    results: dict[str, dict[str, Any]]
    receipt: dict[str, Any]


class ResolutionPrimitiveRuntime(Protocol):
    """Atomic adapter from semantic opcodes to engine-owned state mutations."""

    def begin(self, plan: BoundResolutionPlan) -> None: ...

    def execute(
        self,
        opcode: str,
        arguments: dict[str, Any],
        *,
        step_id: str,
        prior_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def compile_resolution_plan(value: dict[str, Any]) -> CompiledResolutionPlan:
    """Compile one immutable rule-card plan template."""

    if not isinstance(value, dict):
        raise ResolutionPlanCompilationError("resolution plan must be an object")
    allowed = {
        "schema_version",
        "id",
        "source_card_id",
        "source_card_kind",
        "trigger",
        "trigger_filter",
        "slots",
        "steps",
        "citations",
        "fingerprint",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ResolutionPlanCompilationError(
            f"resolution plan has unsupported fields: {sorted(unknown)}"
        )
    schema_version = value.get("schema_version")
    plan_id = str(value.get("id") or "").strip()
    source_card_id = str(value.get("source_card_id") or "").strip()
    source_card_kind = str(value.get("source_card_kind") or "").strip()
    trigger = str(value.get("trigger") or "").strip()
    if schema_version != SEMANTIC_PLAN_VERSION:
        raise ResolutionPlanCompilationError(
            f"resolution plan schema_version must be {SEMANTIC_PLAN_VERSION}"
        )
    for field, candidate in (
        ("id", plan_id),
        ("source_card_id", source_card_id),
    ):
        if not _SAFE_ID_RE.fullmatch(candidate):
            raise ResolutionPlanCompilationError(
                f"resolution plan {field} must be a stable safe identifier"
            )
    if source_card_kind not in SOURCE_CARD_KINDS:
        raise ResolutionPlanCompilationError("unsupported source_card_kind")
    if trigger not in PLAN_TRIGGERS:
        raise ResolutionPlanCompilationError("unsupported resolution plan trigger")

    raw_slots = value.get("slots", {})
    if not isinstance(raw_slots, dict):
        raise ResolutionPlanCompilationError("resolution plan slots must be an object")
    slots: dict[str, dict[str, Any]] = {}
    for slot_name, raw_definition in raw_slots.items():
        normalized_name = str(slot_name or "")
        if not _SAFE_ID_RE.fullmatch(normalized_name):
            raise ResolutionPlanCompilationError("slot names must be stable safe identifiers")
        slots[normalized_name] = _validate_slot_definition(
            normalized_name,
            raw_definition,
        )

    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ResolutionPlanCompilationError("resolution plan needs at least one step")
    steps: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    used_slots: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        step = _validate_step_template(
            raw_step,
            index=index,
            slots=slots,
            prior_step_ids=step_ids,
            used_slots=used_slots,
        )
        steps.append(step)
        step_ids.add(step["id"])
    trigger_filter = _validate_trigger_filter_template(
        value.get("trigger_filter", {}),
        trigger=trigger,
        slots=slots,
        used_slots=used_slots,
    )
    unused_slots = set(slots) - used_slots
    if unused_slots:
        raise ResolutionPlanCompilationError(
            f"resolution plan declares unused slots: {sorted(unused_slots)}"
        )

    citations = _validate_citations(value.get("citations"))
    for step in steps:
        if step["op"] != "check.save":
            continue
        try:
            validated_save_source_facts(
                step["args"].get("source"),
                citations=citations,
                source_card_kind=source_card_kind,
            )
        except ValueError as error:
            raise ResolutionPlanCompilationError(
                f"plan step {step['id']} source: {error}"
            ) from error
    canonical = {
        "schema_version": schema_version,
        "id": plan_id,
        "source_card_id": source_card_id,
        "source_card_kind": source_card_kind,
        "trigger": trigger,
        "trigger_filter": trigger_filter,
        "slots": slots,
        "steps": steps,
        "citations": list(citations),
    }
    fingerprint = _fingerprint(canonical)
    supplied_fingerprint = str(value.get("fingerprint") or "")
    if supplied_fingerprint and supplied_fingerprint != fingerprint:
        raise ResolutionPlanCompilationError(
            "resolution plan fingerprint does not match its canonical template"
        )
    return CompiledResolutionPlan(
        schema_version=schema_version,
        id=plan_id,
        source_card_id=source_card_id,
        source_card_kind=source_card_kind,
        trigger=trigger,
        trigger_filter=deepcopy(trigger_filter),
        slots=deepcopy(slots),
        steps=tuple(deepcopy(steps)),
        citations=citations,
        fingerprint=fingerprint,
    )


def bind_resolution_plan(
    plan: CompiledResolutionPlan | dict[str, Any],
    bindings: dict[str, Any],
    *,
    agent_ruling: dict[str, Any] | None = None,
) -> BoundResolutionPlan:
    """Fill only declared slots and produce a fully validated immutable plan."""

    compiled = plan if isinstance(plan, CompiledResolutionPlan) else compile_resolution_plan(plan)
    if not isinstance(bindings, dict):
        raise ResolutionPlanBindingError("resolution plan bindings must be an object")
    if set(bindings) != set(compiled.slots):
        missing = sorted(set(compiled.slots) - set(bindings))
        unknown = sorted(set(bindings) - set(compiled.slots))
        raise ResolutionPlanBindingError(
            f"resolution plan bindings mismatch; missing={missing}, unknown={unknown}"
        )
    normalized_bindings = {
        name: _normalize_slot_value(name, definition, bindings[name])
        for name, definition in compiled.slots.items()
    }
    bound_trigger_filter = _bind_value(
        compiled.trigger_filter,
        normalized_bindings,
    )
    _reject_unbound_slots(bound_trigger_filter)
    _validate_trigger_filter_values(
        bound_trigger_filter,
        trigger=compiled.trigger,
    )
    bound_steps = tuple(_bind_value(step, normalized_bindings) for step in compiled.steps)
    prior_step_ids: set[str] = set()
    for index, step in enumerate(bound_steps):
        _validate_concrete_step(
            step,
            index=index,
            prior_step_ids=prior_step_ids,
        )
        prior_step_ids.add(str(step["id"]))
    normalized_ruling = _normalize_agent_ruling(agent_ruling)
    canonical = {
        "plan_fingerprint": compiled.fingerprint,
        "bindings": normalized_bindings,
        "trigger_filter": bound_trigger_filter,
        "agent_ruling": normalized_ruling,
    }
    return BoundResolutionPlan(
        compiled=compiled,
        bindings=deepcopy(normalized_bindings),
        trigger_filter=deepcopy(bound_trigger_filter),
        steps=deepcopy(bound_steps),
        agent_ruling=deepcopy(normalized_ruling),
        fingerprint=_fingerprint(canonical),
    )


def execute_resolution_plan(
    plan: BoundResolutionPlan,
    runtime: ResolutionPrimitiveRuntime,
) -> ResolutionPlanResult:
    """Execute a fully bound plan in one runtime-owned atomic transaction."""

    if not isinstance(plan, BoundResolutionPlan):
        raise ResolutionPlanExecutionError("execute_resolution_plan requires a bound plan")
    results: dict[str, dict[str, Any]] = {}
    executed: list[dict[str, Any]] = []
    runtime.begin(plan)
    try:
        for step in plan.steps:
            step_id = str(step["id"])
            when = step.get("when")
            if when is not None and not _evaluate_when(when, results):
                results[step_id] = {"status": "skipped"}
                executed.append(
                    {
                        "step_id": step_id,
                        "op": step["op"],
                        "status": "skipped",
                    }
                )
                continue
            arguments = _resolve_result_refs(step["args"], results)
            result = runtime.execute(
                str(step["op"]),
                deepcopy(arguments),
                step_id=step_id,
                prior_results=deepcopy(results),
            )
            if not isinstance(result, dict):
                raise ResolutionPlanExecutionError(
                    f"resolution primitive {step['op']} returned a non-object result"
                )
            results[step_id] = deepcopy(result)
            executed.append(
                {
                    "step_id": step_id,
                    "op": step["op"],
                    "status": "committed",
                }
            )
        runtime.commit()
    except Exception as error:
        runtime.rollback()
        if isinstance(error, ResolutionPlanExecutionError):
            raise
        raise ResolutionPlanExecutionError(
            f"resolution plan {plan.compiled.id} failed atomically: {error}"
        ) from error
    receipt = {
        "schema_version": plan.compiled.schema_version,
        "plan_id": plan.compiled.id,
        "plan_fingerprint": plan.fingerprint,
        "source_card_id": plan.compiled.source_card_id,
        "source_card_kind": plan.compiled.source_card_kind,
        "trigger": plan.compiled.trigger,
        "trigger_filter": deepcopy(plan.trigger_filter),
        "citations": [deepcopy(item) for item in plan.compiled.citations],
        "agent_ruling": deepcopy(plan.agent_ruling),
        "steps": executed,
        "committed": True,
    }
    return ResolutionPlanResult(
        status="committed",
        plan_id=plan.compiled.id,
        plan_fingerprint=plan.fingerprint,
        results=deepcopy(results),
        receipt=receipt,
    )


def resolution_plan_contract(plan: CompiledResolutionPlan) -> dict[str, Any]:
    """Return the bounded Agent/external-input contract without executable internals."""

    return {
        "schema_version": plan.schema_version,
        "plan_id": plan.id,
        "plan_fingerprint": plan.fingerprint,
        "source_card_id": plan.source_card_id,
        "source_card_kind": plan.source_card_kind,
        "trigger": plan.trigger,
        "trigger_filter": deepcopy(plan.trigger_filter),
        "slots": deepcopy(plan.slots),
        "citations": [deepcopy(item) for item in plan.citations],
    }


def resolution_plan_template(plan: CompiledResolutionPlan) -> dict[str, Any]:
    """Serialize the canonical rule-card template for durable content storage."""

    return {
        "schema_version": plan.schema_version,
        "id": plan.id,
        "source_card_id": plan.source_card_id,
        "source_card_kind": plan.source_card_kind,
        "trigger": plan.trigger,
        "trigger_filter": deepcopy(plan.trigger_filter),
        "slots": deepcopy(plan.slots),
        "steps": [deepcopy(item) for item in plan.steps],
        "citations": [deepcopy(item) for item in plan.citations],
        "fingerprint": plan.fingerprint,
    }


def resolution_plan_trigger_matches(
    plan: BoundResolutionPlan,
    event: dict[str, Any],
) -> bool:
    """Match a bound v2 plan against one canonical engine event."""

    if not isinstance(plan, BoundResolutionPlan) or not isinstance(event, dict):
        return False
    event_trigger = str(event.get("trigger") or event.get("event") or "")
    if event_trigger != plan.compiled.trigger:
        return False
    return all(
        field in event and event[field] == expected
        for field, expected in plan.trigger_filter.items()
    )


def require_resolution_plan_trigger(
    plan: BoundResolutionPlan,
    event: dict[str, Any],
) -> None:
    """Reject execution when the paid engine event does not satisfy the card."""

    if not resolution_plan_trigger_matches(plan, event):
        raise ResolutionPlanExecutionError(
            "resolution plan trigger does not match the paid engine event"
        )


def _validate_trigger_filter_template(
    value: Any,
    *,
    trigger: str,
    slots: dict[str, dict[str, Any]],
    used_slots: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionPlanCompilationError("resolution plan trigger_filter must be an object")
    allowed_fields = TRIGGER_EVENT_FIELDS[trigger]
    unknown = set(value) - allowed_fields
    if unknown:
        raise ResolutionPlanCompilationError(
            f"resolution plan trigger_filter has unsupported event fields: {sorted(unknown)}"
        )
    normalized: dict[str, Any] = {}
    for field, expected in value.items():
        if isinstance(expected, dict) and set(expected) == {"$slot"}:
            slot_name = str(expected["$slot"] or "")
            if slot_name not in slots:
                raise ResolutionPlanCompilationError(
                    f"resolution plan references unknown slot {slot_name}"
                )
            used_slots.add(slot_name)
            normalized[field] = {"$slot": slot_name}
            continue
        if not _is_trigger_filter_literal(expected):
            raise ResolutionPlanCompilationError(
                "resolution plan trigger_filter values must be bounded literals or declared slots"
            )
        normalized[field] = deepcopy(expected)
    return normalized


def _validate_trigger_filter_values(
    value: dict[str, Any],
    *,
    trigger: str,
) -> None:
    unknown = set(value) - TRIGGER_EVENT_FIELDS[trigger]
    if unknown or any(not _is_trigger_filter_literal(expected) for expected in value.values()):
        raise ResolutionPlanBindingError("bound resolution plan trigger_filter is invalid")


def _is_trigger_filter_literal(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, str):
        return 0 < len(value) <= 500
    if isinstance(value, list):
        return len(value) <= 100 and all(
            item is None or isinstance(item, (bool, int, str)) for item in value
        )
    return False


def _validate_slot_definition(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionPlanCompilationError(f"slot {name} definition must be an object")
    allowed = {
        "kind",
        "owner",
        "description",
        "choices",
        "minimum",
        "maximum",
        "minimum_items",
        "maximum_items",
    }
    unknown = set(value) - allowed
    kind = str(value.get("kind") or "")
    owner = str(value.get("owner") or "agent")
    description = " ".join(str(value.get("description") or "").split())
    if (
        unknown
        or kind not in SLOT_KINDS
        or owner not in AGENT_SLOT_OWNERS
        or not 5 <= len(description) <= 500
    ):
        raise ResolutionPlanCompilationError(
            f"slot {name} needs a supported kind, owner, and bounded description"
        )
    normalized: dict[str, Any] = {
        "kind": kind,
        "owner": owner,
        "description": description,
    }
    if kind == "enum":
        choices = value.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or any(not isinstance(item, (str, int)) for item in choices)
            or len(set(map(str, choices))) != len(choices)
        ):
            raise ResolutionPlanCompilationError(f"enum slot {name} needs unique choices")
        normalized["choices"] = deepcopy(choices)
    for field in ("minimum", "maximum", "minimum_items", "maximum_items"):
        if field not in value:
            continue
        candidate = value[field]
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise ResolutionPlanCompilationError(f"slot {name} {field} must be an integer")
        normalized[field] = candidate
    if (
        "minimum" in normalized
        and "maximum" in normalized
        and normalized["maximum"] < normalized["minimum"]
    ):
        raise ResolutionPlanCompilationError(f"slot {name} has a reversed numeric range")
    if (
        "minimum_items" in normalized
        and "maximum_items" in normalized
        and normalized["maximum_items"] < normalized["minimum_items"]
    ):
        raise ResolutionPlanCompilationError(f"slot {name} has a reversed item range")
    return normalized


def _validate_step_template(
    value: Any,
    *,
    index: int,
    slots: dict[str, dict[str, Any]],
    prior_step_ids: set[str],
    used_slots: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionPlanCompilationError(f"plan step {index} must be an object")
    if set(value) - {"id", "op", "args", "when"}:
        raise ResolutionPlanCompilationError(f"plan step {index} has unsupported fields")
    step_id = str(value.get("id") or "")
    opcode = str(value.get("op") or "")
    arguments = value.get("args")
    if (
        not _SAFE_ID_RE.fullmatch(step_id)
        or step_id in prior_step_ids
        or opcode not in PLAN_OPS
        or not isinstance(arguments, dict)
    ):
        raise ResolutionPlanCompilationError(
            f"plan step {index} needs a unique id, supported op, and object args"
        )
    required, allowed = _STEP_FIELDS[opcode]
    unknown = set(arguments) - allowed
    missing = required - set(arguments)
    if unknown or missing:
        raise ResolutionPlanCompilationError(
            f"plan step {step_id} argument mismatch; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    _validate_template_references(
        arguments,
        slots=slots,
        prior_step_ids=prior_step_ids,
        used_slots=used_slots,
    )
    if opcode == "attack.ac_bonus":
        try:
            _validate_common_concrete_arguments(opcode, arguments, index=index)
        except ResolutionPlanBindingError as error:
            raise ResolutionPlanCompilationError(f"plan step {step_id}: {error}") from error
    step = {"id": step_id, "op": opcode, "args": deepcopy(arguments)}
    if "when" in value:
        when = value["when"]
        _validate_when_template(
            when,
            slots=slots,
            prior_step_ids=prior_step_ids,
            used_slots=used_slots,
        )
        step["when"] = deepcopy(when)
    return step


def _validate_template_references(
    value: Any,
    *,
    slots: dict[str, dict[str, Any]],
    prior_step_ids: set[str],
    used_slots: set[str],
) -> None:
    if isinstance(value, dict):
        if set(value) == {"$slot"}:
            slot_name = str(value["$slot"] or "")
            if slot_name not in slots:
                raise ResolutionPlanCompilationError(
                    f"resolution plan references unknown slot {slot_name}"
                )
            used_slots.add(slot_name)
            return
        if set(value) == {"$result"}:
            _validate_result_ref(value["$result"], prior_step_ids)
            return
        for nested in value.values():
            _validate_template_references(
                nested,
                slots=slots,
                prior_step_ids=prior_step_ids,
                used_slots=used_slots,
            )
    elif isinstance(value, list):
        for nested in value:
            _validate_template_references(
                nested,
                slots=slots,
                prior_step_ids=prior_step_ids,
                used_slots=used_slots,
            )


def _validate_when_template(
    value: Any,
    *,
    slots: dict[str, dict[str, Any]],
    prior_step_ids: set[str],
    used_slots: set[str],
) -> None:
    if not isinstance(value, dict):
        raise ResolutionPlanCompilationError("step when must be an object")
    operator = str(value.get("operator") or "")
    required = {"subject", "operator"}
    allowed = {"subject", "operator", "expected"}
    if set(value) - allowed or required - set(value):
        raise ResolutionPlanCompilationError("step when has invalid fields")
    if operator not in {"equals", "not_equals", "truthy", "falsy", "contains"}:
        raise ResolutionPlanCompilationError("step when has an unsupported operator")
    if operator in {"equals", "not_equals", "contains"} and "expected" not in value:
        raise ResolutionPlanCompilationError("step when operator requires expected")
    _validate_template_references(
        value,
        slots=slots,
        prior_step_ids=prior_step_ids,
        used_slots=used_slots,
    )


def _validate_concrete_step(
    step: dict[str, Any],
    *,
    index: int,
    prior_step_ids: set[str],
) -> None:
    arguments = step["args"]
    _reject_unbound_slots(arguments)
    _validate_result_references(arguments, prior_step_ids)
    if "when" in step:
        _reject_unbound_slots(step["when"])
        _validate_result_references(step["when"], prior_step_ids)
    opcode = str(step["op"])
    _validate_common_concrete_arguments(opcode, arguments, index=index)


def _validate_common_concrete_arguments(
    opcode: str,
    arguments: dict[str, Any],
    *,
    index: int,
) -> None:
    del index
    for field in (
        "actor_id",
        "controller_actor_id",
        "from_actor_id",
        "source_actor_id",
        "target_actor_id",
        "to_actor_id",
    ):
        if field in arguments and not _is_result_ref(arguments[field]):
            _require_safe_text(arguments[field], field)
    if "target_ids" in arguments and not _is_result_ref(arguments["target_ids"]):
        _normalize_actor_ids(arguments["target_ids"], field="target_ids")
    for field in ("ability", "source_ability", "target_ability"):
        if field in arguments and not _is_result_ref(arguments[field]):
            if arguments[field] not in _ABILITY_IDS:
                raise ResolutionPlanBindingError(f"{field} must be a D&D ability")
    for field in (
        "dc",
        "amount",
        "distance_ft",
        "maximum_range_ft",
        "minimum",
        "maximum",
        "value",
    ):
        if field in arguments and not _is_result_ref(arguments[field]):
            candidate = arguments[field]
            if isinstance(candidate, bool) or not isinstance(candidate, int):
                raise ResolutionPlanBindingError(f"{field} must be an integer")
    if "dc" in arguments and not _is_result_ref(arguments["dc"]):
        if not 1 <= int(arguments["dc"]) <= 40:
            raise ResolutionPlanBindingError("dc must be in the range 1..40")
    if opcode == "attack.ac_bonus":
        bonus = arguments["bonus"]
        attack_modes = arguments["attack_modes"]
        if (
            _is_result_ref(bonus)
            or isinstance(bonus, bool)
            or not isinstance(bonus, int)
            or not 1 <= bonus <= 20
        ):
            raise ResolutionPlanBindingError("attack.ac_bonus bonus must be in the range 1..20")
        if (
            _is_result_ref(attack_modes)
            or not isinstance(attack_modes, list)
            or not attack_modes
            or any(not isinstance(mode, str) for mode in attack_modes)
            or len(attack_modes) != len({str(mode) for mode in attack_modes})
            or any(str(mode) not in {"melee", "ranged"} for mode in attack_modes)
        ):
            raise ResolutionPlanBindingError(
                "attack.ac_bonus attack_modes must contain unique melee/ranged modes"
            )
        for field in (
            "requires_visible_attacker",
            "requires_wielded_melee_weapon",
        ):
            if field in arguments and not isinstance(arguments[field], bool):
                raise ResolutionPlanBindingError(f"attack.ac_bonus {field} must be boolean")
    if "success_damage" in arguments and not _is_result_ref(arguments["success_damage"]):
        if arguments["success_damage"] not in {"full", "half", "none"}:
            raise ResolutionPlanBindingError("success_damage must be full, half, or none")
    for field in (
        "advantage",
        "critical",
        "disadvantage",
        "proficient",
        "source_advantage",
        "source_disadvantage",
        "source_proficient",
        "target_advantage",
        "target_disadvantage",
        "target_proficient",
        "exclude_self",
        "require_visible",
    ):
        if field in arguments and not _is_result_ref(arguments[field]):
            if not isinstance(arguments[field], bool):
                raise ResolutionPlanBindingError(f"{field} must be a boolean")
    if "expression" in arguments and not _is_result_ref(arguments["expression"]):
        if not _DICE_RE.fullmatch(str(arguments["expression"])):
            raise ResolutionPlanBindingError("expression must be a bounded dice formula")
    if opcode in {"damage.apply", "healing.apply"}:
        has_expression = "expression" in arguments
        has_amount = "amount" in arguments
        if has_expression == has_amount:
            raise ResolutionPlanBindingError(
                f"{opcode} requires exactly one of expression or amount"
            )
    if opcode == "condition.apply" and isinstance(arguments.get("duration"), dict):
        _normalize_duration(
            arguments["duration"],
            field="condition.apply",
        )
        if (
            arguments["duration"].get("kind") == "source_turn_start"
            and "source_actor_id" not in arguments
        ):
            raise ResolutionPlanBindingError(
                "source-turn condition duration requires source_actor_id"
            )
    if opcode == "roll.table":
        table = arguments["table"]
        if (
            not isinstance(table, list)
            or not table
            or any(
                not isinstance(entry, dict)
                or set(entry) != {"weight", "value"}
                or isinstance(entry["weight"], bool)
                or not isinstance(entry["weight"], int)
                or entry["weight"] < 1
                for entry in table
            )
        ):
            raise ResolutionPlanBindingError("roll.table requires weighted value entries")
        excluded = arguments.get("exclude", [])
        if not _is_result_ref(excluded) and not isinstance(excluded, list):
            raise ResolutionPlanBindingError("roll.table exclude must be a list or prior result")
        if not _is_result_ref(excluded):
            values = [entry["value"] for entry in table]
            if any(item not in values for item in excluded):
                raise ResolutionPlanBindingError(
                    "roll.table exclude values must exist in the source table"
                )
            if len(excluded) != len(
                {
                    json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    for item in excluded
                }
            ):
                raise ResolutionPlanBindingError("roll.table exclude values must be unique")
            if len(excluded) >= len(table):
                raise ResolutionPlanBindingError(
                    "roll.table exclude cannot remove every source result"
                )
    if opcode == "target.validate":
        for field in ("require_conditions", "forbid_conditions"):
            values = arguments.get(field, [])
            if _is_result_ref(values):
                continue
            if (
                not isinstance(values, list)
                or any(not str(item).strip() for item in values)
                or len({str(item).strip().casefold() for item in values}) != len(values)
            ):
                raise ResolutionPlanBindingError(
                    f"target.validate {field} must contain unique condition ids"
                )
        maximum_range = arguments.get("maximum_range_ft")
        if (
            maximum_range is not None
            and not _is_result_ref(maximum_range)
            and int(maximum_range) < 0
        ):
            raise ResolutionPlanBindingError("target.validate maximum_range_ft cannot be negative")
    if opcode == "knowledge.transfer":
        knowledge_ids = arguments["knowledge_ids"]
        if not _is_result_ref(knowledge_ids):
            _normalize_knowledge_ids(knowledge_ids)
    if opcode == "state.assert":
        if arguments["operator"] not in {
            "contains",
            "equals",
            "falsy",
            "not_equals",
            "truthy",
        }:
            raise ResolutionPlanBindingError("state.assert operator is unsupported")


def _validate_citations(value: Any) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, dict) for item in value)
    ):
        raise ResolutionPlanCompilationError("resolution plan needs at least one source citation")
    result: list[dict[str, Any]] = []
    for citation in value:
        source = str(citation.get("source") or "").strip()
        source_ref = citation.get("source_ref")
        source_excerpt = " ".join(str(citation.get("source_excerpt") or "").split())
        if (
            not source
            or not isinstance(source_ref, dict)
            or not source_ref
            or not 10 <= len(source_excerpt) <= 4000
        ):
            raise ResolutionPlanCompilationError(
                "each citation needs source, source_ref, and bounded source_excerpt"
            )
        result.append(
            {
                "source": source,
                "source_ref": deepcopy(source_ref),
                "source_excerpt": source_excerpt,
            }
        )
    return tuple(result)


def _normalize_slot_value(
    name: str,
    definition: dict[str, Any],
    value: Any,
) -> Any:
    kind = definition["kind"]
    if kind == "actor_id":
        normalized: Any = _require_safe_text(value, name)
    elif kind == "actor_ids":
        normalized = _normalize_actor_ids(value, field=name)
    elif kind == "ability":
        normalized = str(value or "").strip().casefold()
        if normalized not in _ABILITY_IDS:
            raise ResolutionPlanBindingError(f"slot {name} must be a D&D ability")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ResolutionPlanBindingError(f"slot {name} must be a boolean")
        normalized = value
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ResolutionPlanBindingError(f"slot {name} must be an integer")
        normalized = value
    elif kind == "enum":
        if value not in definition["choices"]:
            raise ResolutionPlanBindingError(f"slot {name} must be one of its rule-card choices")
        normalized = deepcopy(value)
    elif kind == "dice":
        normalized = "".join(str(value or "").split()).casefold()
        if not _DICE_RE.fullmatch(normalized):
            raise ResolutionPlanBindingError(f"slot {name} must be a dice expression")
    elif kind in {"condition_id", "damage_type"}:
        normalized = str(value or "").strip().casefold().replace(" ", "_")
        if not _SAFE_ID_RE.fullmatch(normalized):
            raise ResolutionPlanBindingError(f"slot {name} must be a safe {kind}")
    elif kind == "text":
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise ResolutionPlanBindingError(f"slot {name} must be non-empty text")
    elif kind == "position":
        if (
            not isinstance(value, dict)
            or set(value) != {"x", "y"}
            or any(
                isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                for key in ("x", "y")
            )
        ):
            raise ResolutionPlanBindingError(f"slot {name} must be an x/y position")
        normalized = {"x": value["x"], "y": value["y"]}
    elif kind == "duration":
        normalized = _normalize_duration(value, field=name)
    elif kind == "knowledge_ids":
        normalized = _normalize_knowledge_ids(value)
    else:
        raise ResolutionPlanBindingError(f"slot {name} has an unsupported kind")
    _validate_slot_bounds(name, definition, normalized)
    return normalized


def _validate_slot_bounds(
    name: str,
    definition: dict[str, Any],
    value: Any,
) -> None:
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in definition and value < definition["minimum"]:
            raise ResolutionPlanBindingError(f"slot {name} is below its minimum")
        if "maximum" in definition and value > definition["maximum"]:
            raise ResolutionPlanBindingError(f"slot {name} is above its maximum")
    length: int | None = None
    if isinstance(value, (str, list)):
        length = len(value)
    if length is not None:
        if "minimum_items" in definition and length < definition["minimum_items"]:
            raise ResolutionPlanBindingError(f"slot {name} has too few items")
        if "maximum_items" in definition and length > definition["maximum_items"]:
            raise ResolutionPlanBindingError(f"slot {name} has too many items")


def _normalize_duration(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionPlanBindingError(f"{field} duration must be an object")
    allowed = {"kind", "amount", "until"}
    kind = str(value.get("kind") or "")
    if set(value) - allowed or kind not in {
        "encounter",
        "rounds",
        "source_turn_start",
        "target_turn_end",
        "target_turn_start",
    }:
        raise ResolutionPlanBindingError(f"{field} duration kind is unsupported")
    normalized = {"kind": kind}
    if kind == "rounds":
        amount = value.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ResolutionPlanBindingError(f"{field} rounds must be positive")
        normalized["amount"] = amount
    if "until" in value:
        until = str(value["until"] or "").strip()
        if not until:
            raise ResolutionPlanBindingError(f"{field} until must be non-empty")
        normalized["until"] = until
    return normalized


def _normalize_actor_ids(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ResolutionPlanBindingError(f"{field} must be a non-empty actor-id list")
    normalized = [_require_safe_text(item, field) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ResolutionPlanBindingError(f"{field} actor ids must be unique")
    return normalized


def _normalize_knowledge_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ResolutionPlanBindingError("knowledge transfer needs explicit knowledge ids")
    normalized = [_require_safe_text(item, "knowledge_ids") for item in value]
    if len(normalized) != len(set(normalized)):
        raise ResolutionPlanBindingError("knowledge ids must be unique")
    return normalized


def _normalize_agent_ruling(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ResolutionPlanBindingError("agent_ruling must be an object")
    allowed = {
        "application_id",
        "default_resolver",
        "ruling_kind",
        "decision",
        "reason",
        "source_ref",
        "source_excerpt",
    }
    if set(value) - allowed:
        raise ResolutionPlanBindingError("agent_ruling has unsupported fields")
    normalized = {
        "application_id": str(value.get("application_id") or "").strip(),
        "default_resolver": str(value.get("default_resolver") or ""),
        "ruling_kind": str(value.get("ruling_kind") or ""),
        "decision": " ".join(str(value.get("decision") or "").split()),
        "reason": " ".join(str(value.get("reason") or "").split()),
        "source_ref": deepcopy(value.get("source_ref")),
        "source_excerpt": " ".join(str(value.get("source_excerpt") or "").split()),
    }
    if (
        not normalized["application_id"]
        or normalized["default_resolver"] != "agent"
        or normalized["ruling_kind"]
        not in {
            "agent_dm_adjudication",
            "environmental_consequence",
            "generic_spell_effect",
            "module_specific_procedure",
            "source_or_scene_fact",
        }
        or not 10 <= len(normalized["decision"]) <= 1000
        or not 10 <= len(normalized["reason"]) <= 500
        or not isinstance(normalized["source_ref"], dict)
        or not normalized["source_ref"]
        or not 10 <= len(normalized["source_excerpt"]) <= 4000
    ):
        raise ResolutionPlanBindingError(
            "agent_ruling must be a bounded source-bound Agent decision"
        )
    return normalized


def _bind_value(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$slot"}:
            return deepcopy(bindings[str(value["$slot"])])
        return {key: _bind_value(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_bind_value(item, bindings) for item in value]
    return deepcopy(value)


def _validate_result_ref(value: Any, prior_step_ids: set[str]) -> None:
    if _parse_result_ref(value, prior_step_ids) is None:
        raise ResolutionPlanCompilationError("result references must point to an earlier plan step")


def _validate_result_references(value: Any, prior_step_ids: set[str]) -> None:
    if isinstance(value, dict):
        if set(value) == {"$result"}:
            if _parse_result_ref(value["$result"], prior_step_ids) is None:
                raise ResolutionPlanBindingError(
                    "result references must point to an earlier plan step"
                )
            return
        for nested in value.values():
            _validate_result_references(nested, prior_step_ids)
    elif isinstance(value, list):
        for nested in value:
            _validate_result_references(nested, prior_step_ids)


def _reject_unbound_slots(value: Any) -> None:
    if isinstance(value, dict):
        if set(value) == {"$slot"}:
            raise ResolutionPlanBindingError("bound plan still contains a slot reference")
        for nested in value.values():
            _reject_unbound_slots(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_unbound_slots(nested)


def _resolve_result_refs(value: Any, results: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$result"}:
            parsed = _parse_result_ref(value["$result"], set(results))
            if parsed is None:
                raise ResolutionPlanExecutionError("invalid result reference")
            step_id, path = parsed
            if step_id not in results:
                raise ResolutionPlanExecutionError(
                    f"result reference is unavailable: {value['$result']}"
                )
            result: Any = results[step_id]
            if path:
                for part in path.split("."):
                    if not isinstance(result, dict) or part not in result:
                        raise ResolutionPlanExecutionError(
                            f"result reference is unavailable: {value['$result']}"
                        )
                    result = result[part]
            return deepcopy(result)
        return {key: _resolve_result_refs(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_result_refs(item, results) for item in value]
    return deepcopy(value)


def _evaluate_when(value: dict[str, Any], results: dict[str, dict[str, Any]]) -> bool:
    subject = _resolve_result_refs(value["subject"], results)
    operator = str(value["operator"])
    expected = _resolve_result_refs(value.get("expected"), results)
    if operator == "equals":
        return subject == expected
    if operator == "not_equals":
        return subject != expected
    if operator == "truthy":
        return bool(subject)
    if operator == "falsy":
        return not bool(subject)
    if operator == "contains":
        try:
            return expected in subject
        except TypeError:
            return False
    raise ResolutionPlanExecutionError("unsupported plan condition")


def _is_result_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$result"}


def _parse_result_ref(
    value: Any,
    available_step_ids: set[str],
) -> tuple[str, str] | None:
    reference = str(value or "")
    if _RESULT_REF_RE.fullmatch(reference) is None:
        return None
    for step_id in sorted(available_step_ids, key=len, reverse=True):
        if reference == step_id:
            return step_id, ""
        prefix = f"{step_id}."
        if reference.startswith(prefix) and reference.removeprefix(prefix):
            return step_id, reference.removeprefix(prefix)
    return None


def _require_safe_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(normalized):
        raise ResolutionPlanBindingError(f"{field} must be a stable safe identifier")
    return normalized


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AGENT_SLOT_OWNERS",
    "BoundResolutionPlan",
    "CompiledResolutionPlan",
    "PLAN_OPS",
    "PLAN_TRIGGERS",
    "ResolutionPlanBindingError",
    "ResolutionPlanCompilationError",
    "ResolutionPlanError",
    "ResolutionPlanExecutionError",
    "ResolutionPlanResult",
    "ResolutionPrimitiveRuntime",
    "SEMANTIC_PLAN_VERSION",
    "SLOT_KINDS",
    "SOURCE_CARD_KINDS",
    "TRIGGER_EVENT_FIELDS",
    "bind_resolution_plan",
    "compile_resolution_plan",
    "execute_resolution_plan",
    "require_resolution_plan_trigger",
    "resolution_plan_contract",
    "resolution_plan_template",
    "resolution_plan_trigger_matches",
]
