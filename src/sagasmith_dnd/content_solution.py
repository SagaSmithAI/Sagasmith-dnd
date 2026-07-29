"""Durable provenance for Agent-compiled custom-content solutions."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from sagasmith_dnd.resolution_plan import CompiledResolutionPlan

CONTENT_SOLUTION_SCHEMA_VERSION = 1

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")
_RULING_KINDS = frozenset(
    {
        "agent_dm_adjudication",
        "environmental_consequence",
        "generic_spell_effect",
        "module_specific_procedure",
        "source_or_scene_fact",
    }
)


class ContentSolutionError(ValueError):
    """An Agent-compiled solution is not durable or source-bound."""


def content_solution_source_fingerprint(
    plan: CompiledResolutionPlan,
) -> str:
    """Hash the exact card identity and evidence used by the Agent."""

    canonical = {
        "source_card_id": plan.source_card_id,
        "source_card_kind": plan.source_card_kind,
        "citations": [deepcopy(item) for item in plan.citations],
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_content_solution(
    plan: CompiledResolutionPlan,
    *,
    application_id: str,
    agent_ruling: dict[str, Any],
    solution_version: int = 1,
    replaces_plan_fingerprint: str = "",
) -> dict[str, Any]:
    """Build normalized metadata for one first-use Agent compilation."""

    return normalize_content_solution(
        {
            "schema_version": CONTENT_SOLUTION_SCHEMA_VERSION,
            "status": "compiled",
            "solution_version": solution_version,
            "source_card_id": plan.source_card_id,
            "source_card_kind": plan.source_card_kind,
            "source_fingerprint": content_solution_source_fingerprint(plan),
            "plan_fingerprint": plan.fingerprint,
            "application_id": application_id,
            "compiled_by": agent_ruling,
            "replaces_plan_fingerprint": replaces_plan_fingerprint,
        },
        plan=plan,
    )


def normalize_content_solution(
    value: Any,
    *,
    plan: CompiledResolutionPlan,
) -> dict[str, Any]:
    """Validate stored solution provenance against its exact compiled plan."""

    if not isinstance(value, dict):
        raise ContentSolutionError("resolution_solution must be an object")
    allowed = {
        "schema_version",
        "status",
        "solution_version",
        "source_card_id",
        "source_card_kind",
        "source_fingerprint",
        "plan_fingerprint",
        "application_id",
        "compiled_by",
        "replaces_plan_fingerprint",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ContentSolutionError(
            f"resolution_solution has unsupported fields: {sorted(unknown)}"
        )
    schema_version = value.get("schema_version")
    status = str(value.get("status") or "")
    solution_version = value.get("solution_version")
    application_id = str(value.get("application_id") or "").strip()
    source_fingerprint = str(value.get("source_fingerprint") or "")
    plan_fingerprint = str(value.get("plan_fingerprint") or "")
    replaces = str(value.get("replaces_plan_fingerprint") or "")
    if schema_version != CONTENT_SOLUTION_SCHEMA_VERSION:
        raise ContentSolutionError(
            "resolution_solution schema_version is unsupported"
        )
    if status != "compiled":
        raise ContentSolutionError("resolution_solution status must be compiled")
    if (
        isinstance(solution_version, bool)
        or not isinstance(solution_version, int)
        or solution_version < 1
    ):
        raise ContentSolutionError(
            "resolution_solution solution_version must be positive"
        )
    if _SAFE_ID_RE.fullmatch(application_id) is None:
        raise ContentSolutionError(
            "resolution_solution application_id must be stable"
        )
    if (
        str(value.get("source_card_id") or "") != plan.source_card_id
        or str(value.get("source_card_kind") or "") != plan.source_card_kind
        or plan_fingerprint != plan.fingerprint
        or source_fingerprint != content_solution_source_fingerprint(plan)
    ):
        raise ContentSolutionError(
            "resolution_solution does not match its compiled plan and evidence"
        )
    if replaces and _FINGERPRINT_RE.fullmatch(replaces) is None:
        raise ContentSolutionError(
            "resolution_solution replaces_plan_fingerprint is invalid"
        )
    compiled_by = _normalize_compiler_ruling(value.get("compiled_by"))
    return {
        "schema_version": CONTENT_SOLUTION_SCHEMA_VERSION,
        "status": "compiled",
        "solution_version": solution_version,
        "source_card_id": plan.source_card_id,
        "source_card_kind": plan.source_card_kind,
        "source_fingerprint": source_fingerprint,
        "plan_fingerprint": plan_fingerprint,
        "application_id": application_id,
        "compiled_by": compiled_by,
        "replaces_plan_fingerprint": replaces,
    }


def _normalize_compiler_ruling(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContentSolutionError(
            "resolution_solution compiled_by must be an object"
        )
    allowed = {
        "default_resolver",
        "ruling_kind",
        "decision",
        "reason",
    }
    if set(value) != allowed:
        raise ContentSolutionError(
            "resolution_solution compiled_by requires the exact Agent contract"
        )
    decision = " ".join(str(value.get("decision") or "").split())
    reason = " ".join(str(value.get("reason") or "").split())
    ruling_kind = str(value.get("ruling_kind") or "")
    if (
        value.get("default_resolver") != "agent"
        or ruling_kind not in _RULING_KINDS
        or not 10 <= len(decision) <= 1000
        or not 10 <= len(reason) <= 500
    ):
        raise ContentSolutionError(
            "resolution_solution compiled_by must be a bounded Agent ruling"
        )
    return {
        "default_resolver": "agent",
        "ruling_kind": ruling_kind,
        "decision": decision,
        "reason": reason,
    }


__all__ = [
    "CONTENT_SOLUTION_SCHEMA_VERSION",
    "ContentSolutionError",
    "build_content_solution",
    "content_solution_source_fingerprint",
    "normalize_content_solution",
]
