"""Clause-level settlement contracts for source-bound D&D content.

An artifact may mix data that is copied to a sheet, mechanics owned by the
trusted kernel, semantic plans executed through primitives, Agent-as-DM
judgment, and prose that is only descriptive.  Each source clause declares
exactly one of those settlement modes so selection validation is based on
coverage rather than on an artifact-wide guess.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

RULE_CLAUSE_SCHEMA_VERSION = 1

SETTLEMENT_MODES = frozenset(
    {
        "agent_ruling",
        "descriptive",
        "kernel_mechanic",
        "primitive_plan",
        "static_grant",
    }
)
RULE_CLAUSE_SCOPES = frozenset({"descriptive", "mechanical"})
AGENT_RULING_KINDS = frozenset(
    {
        "agent_dm_adjudication",
        "environmental_consequence",
        "generic_spell_effect",
        "module_specific_procedure",
        "source_or_scene_fact",
    }
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")
_GRANT_REF_RE = re.compile(r"^card\.[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*$")


class RuleContractError(ValueError):
    """A clause or its settlement declaration is incomplete or unsafe."""


@dataclass(frozen=True)
class CompiledRuleClause:
    schema_version: int
    id: str
    title: str
    scope: str
    source_citations: tuple[dict[str, Any], ...]
    settlement: dict[str, Any]


def compile_rule_clause(value: Any) -> CompiledRuleClause:
    """Validate and normalize one immutable source clause."""

    if not isinstance(value, dict):
        raise RuleContractError("rule clause must be an object")
    allowed = {
        "schema_version",
        "id",
        "title",
        "scope",
        "source_citations",
        "settlement",
    }
    unknown = set(value) - allowed
    if unknown:
        raise RuleContractError(f"rule clause has unsupported fields: {sorted(unknown)}")
    schema_version = value.get("schema_version")
    clause_id = str(value.get("id") or "").strip()
    title = " ".join(str(value.get("title") or "").split())
    scope = str(value.get("scope") or "").strip()
    if schema_version != RULE_CLAUSE_SCHEMA_VERSION:
        raise RuleContractError(f"rule clause schema_version must be {RULE_CLAUSE_SCHEMA_VERSION}")
    if _SAFE_ID_RE.fullmatch(clause_id) is None:
        raise RuleContractError("rule clause id must be a stable safe identifier")
    if not 3 <= len(title) <= 200:
        raise RuleContractError("rule clause title must contain 3..200 characters")
    if scope not in RULE_CLAUSE_SCOPES:
        raise RuleContractError("rule clause scope must be mechanical or descriptive")
    citations = _compile_citations(value.get("source_citations"))
    settlement = _compile_settlement(value.get("settlement"), scope=scope)
    return CompiledRuleClause(
        schema_version=RULE_CLAUSE_SCHEMA_VERSION,
        id=clause_id,
        title=title,
        scope=scope,
        source_citations=citations,
        settlement=settlement,
    )


def compile_rule_clauses(value: Any) -> tuple[CompiledRuleClause, ...]:
    """Compile an ordered, uniquely identified set of source clauses."""

    if not isinstance(value, list) or not value:
        raise RuleContractError("rule_clauses must be a non-empty list")
    clauses = tuple(compile_rule_clause(item) for item in value)
    ids = [clause.id for clause in clauses]
    if len(ids) != len(set(ids)):
        raise RuleContractError("rule clause ids must be unique inside an artifact")
    return clauses


def rule_clause_template(clause: CompiledRuleClause) -> dict[str, Any]:
    """Return the canonical durable form of one compiled clause."""

    return {
        "schema_version": clause.schema_version,
        "id": clause.id,
        "title": clause.title,
        "scope": clause.scope,
        "source_citations": [deepcopy(citation) for citation in clause.source_citations],
        "settlement": deepcopy(clause.settlement),
    }


def rule_clause_templates(
    clauses: tuple[CompiledRuleClause, ...],
) -> list[dict[str, Any]]:
    return [rule_clause_template(clause) for clause in clauses]


def validate_rule_clause_coverage(
    clauses: tuple[CompiledRuleClause, ...],
    *,
    artifact: dict[str, Any],
    plan_ids: set[str] | None = None,
    mechanic_refs: set[str] | None = None,
    require_mechanical_clause: bool = False,
) -> list[str]:
    """Verify every declared settlement points at executable artifact content."""

    errors: list[str] = []
    available_plans = set(plan_ids or set())
    available_mechanics = set(mechanic_refs or set())
    claimed_plans: set[str] = set()
    claimed_mechanics: set[str] = set()
    mechanical_count = 0
    for clause in clauses:
        if clause.scope == "mechanical":
            mechanical_count += 1
        settlement = clause.settlement
        mode = settlement["mode"]
        prefix = f"rule clause {clause.id}"
        if mode == "static_grant":
            for grant_ref in settlement["grant_refs"]:
                if _resolve_grant_ref(artifact, grant_ref) is None:
                    errors.append(f"{prefix} static grant is unavailable: {grant_ref}")
        elif mode == "primitive_plan":
            refs = set(settlement["plan_ids"])
            claimed_plans.update(refs)
            missing = sorted(refs - available_plans)
            if missing:
                errors.append(f"{prefix} references unavailable plans: {', '.join(missing)}")
        elif mode == "kernel_mechanic":
            refs = set(settlement["mechanic_refs"])
            claimed_mechanics.update(refs)
            missing = sorted(refs - available_mechanics)
            if missing:
                errors.append(f"{prefix} references unavailable mechanics: {', '.join(missing)}")
    if require_mechanical_clause and mechanical_count == 0:
        errors.append("mechanical content needs at least one mechanical rule clause")
    unclaimed_plans = sorted(available_plans - claimed_plans)
    if unclaimed_plans:
        errors.append(
            "resolution plans are not assigned to a source clause: " + ", ".join(unclaimed_plans)
        )
    unclaimed_mechanics = sorted(available_mechanics - claimed_mechanics)
    if unclaimed_mechanics:
        errors.append(
            "mechanic refs are not assigned to a source clause: " + ", ".join(unclaimed_mechanics)
        )
    return errors


def _compile_citations(value: Any) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, dict) for item in value)
    ):
        raise RuleContractError("each rule clause needs at least one source citation")
    citations: list[dict[str, Any]] = []
    for citation in value:
        allowed = {"source", "source_ref", "source_excerpt"}
        if set(citation) - allowed:
            raise RuleContractError("rule clause citation has unsupported fields")
        source = str(citation.get("source") or "").strip()
        source_ref = citation.get("source_ref")
        source_excerpt = " ".join(str(citation.get("source_excerpt") or "").split())
        if (
            not source
            or not isinstance(source_ref, dict)
            or not source_ref
            or not 10 <= len(source_excerpt) <= 4000
        ):
            raise RuleContractError(
                "rule clause citation needs source, source_ref, and a bounded excerpt"
            )
        citations.append(
            {
                "source": source,
                "source_ref": deepcopy(source_ref),
                "source_excerpt": source_excerpt,
            }
        )
    return tuple(citations)


def _compile_settlement(value: Any, *, scope: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleContractError("rule clause settlement must be an object")
    mode = str(value.get("mode") or "").strip()
    if mode not in SETTLEMENT_MODES:
        raise RuleContractError("rule clause settlement mode is unsupported")
    if mode == "descriptive":
        if set(value) != {"mode"} or scope != "descriptive":
            raise RuleContractError(
                "descriptive settlement requires descriptive scope and no mechanics"
            )
        return {"mode": mode}
    if scope != "mechanical":
        raise RuleContractError(f"{mode} settlement requires a mechanical rule clause")
    if mode == "static_grant":
        if set(value) != {"mode", "grant_refs"}:
            raise RuleContractError("static_grant settlement requires only grant_refs")
        return {
            "mode": mode,
            "grant_refs": _safe_ref_list(
                value.get("grant_refs"),
                field="grant_refs",
                pattern=_GRANT_REF_RE,
            ),
        }
    if mode == "primitive_plan":
        if set(value) != {"mode", "plan_ids"}:
            raise RuleContractError("primitive_plan settlement requires only plan_ids")
        return {
            "mode": mode,
            "plan_ids": _safe_ref_list(
                value.get("plan_ids"),
                field="plan_ids",
                pattern=_SAFE_ID_RE,
            ),
        }
    if mode == "kernel_mechanic":
        if set(value) != {"mode", "mechanic_refs"}:
            raise RuleContractError("kernel_mechanic settlement requires only mechanic_refs")
        return {
            "mode": mode,
            "mechanic_refs": _safe_ref_list(
                value.get("mechanic_refs"),
                field="mechanic_refs",
                pattern=_SAFE_ID_RE,
            ),
        }
    allowed = {"mode", "default_resolver", "ruling_kind", "reason"}
    if set(value) - allowed or set(value) != allowed:
        raise RuleContractError(
            "agent_ruling settlement requires resolver, ruling_kind, and reason"
        )
    default_resolver = str(value.get("default_resolver") or "")
    ruling_kind = str(value.get("ruling_kind") or "")
    reason = " ".join(str(value.get("reason") or "").split())
    if (
        default_resolver != "agent"
        or ruling_kind not in AGENT_RULING_KINDS
        or not 10 <= len(reason) <= 500
    ):
        raise RuleContractError("agent_ruling settlement must be a bounded Agent-as-DM contract")
    return {
        "mode": mode,
        "default_resolver": default_resolver,
        "ruling_kind": ruling_kind,
        "reason": reason,
    }


def _safe_ref_list(
    value: Any,
    *,
    field: str,
    pattern: re.Pattern[str],
) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise RuleContractError(f"{field} must be a non-empty text list")
    refs = [item.strip() for item in value]
    if any(pattern.fullmatch(item) is None for item in refs) or len(refs) != len(set(refs)):
        raise RuleContractError(f"{field} must contain unique stable references")
    return refs


def _resolve_grant_ref(artifact: dict[str, Any], reference: str) -> Any:
    current: Any = artifact
    for part in reference.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


__all__ = [
    "AGENT_RULING_KINDS",
    "CompiledRuleClause",
    "RULE_CLAUSE_SCHEMA_VERSION",
    "RULE_CLAUSE_SCOPES",
    "RuleContractError",
    "SETTLEMENT_MODES",
    "compile_rule_clause",
    "compile_rule_clauses",
    "rule_clause_template",
    "rule_clause_templates",
    "validate_rule_clause_coverage",
]
