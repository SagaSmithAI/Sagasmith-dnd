"""Build-time semantic settlement for bundled, source-linked D&D content."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from sagasmith_dnd.rule_contract import (
    compile_rule_clauses,
    rule_clause_templates,
    validate_rule_clause_coverage,
)

_PROSE_FIELDS = ("description", "statblock_source")
_NON_GRANT_FIELDS = {
    "description",
    "definition",
    "mechanic_refs",
    "name",
    "ruling_requirements",
}


def finalize_bundled_artifact_resolutions(
    artifacts: list[dict[str, Any]],
    *,
    source_root: Path,
    source_prefix: str,
) -> list[dict[str, Any]]:
    """Persist a complete settlement contract for every bundled artifact.

    Structured grants and registered mechanics remain engine-owned.  Prose that
    is not wholly implemented by those paths is stored as an evidence-bound
    Agent-as-DM ruling, while catalog-only text stays descriptive.  Nothing is
    left for a first-use semantic-authoring pass.
    """

    return [
        _finalize_bundled_artifact(
            artifact,
            source_root=source_root,
            source_prefix=source_prefix,
        )
        for artifact in artifacts
    ]


def _finalize_bundled_artifact(
    raw_artifact: dict[str, Any],
    *,
    source_root: Path,
    source_prefix: str,
) -> dict[str, Any]:
    artifact = deepcopy(raw_artifact)
    if (
        artifact.get("rule_clauses") is not None
        or artifact.get("resolution_plan") is not None
        or artifact.get("resolution_plans") is not None
    ):
        return artifact

    card = deepcopy(dict(artifact.get("card") or {}))
    artifact_id = str(artifact.get("id") or "")
    name = " ".join(str(card.get("name") or artifact_id or "Bundled content").split())
    excerpt, citation = _artifact_evidence(
        artifact,
        card=card,
        source_root=source_root,
        source_prefix=source_prefix,
    )
    clauses: list[dict[str, Any]] = []

    grant_refs = _static_grant_refs(card)
    if grant_refs:
        clauses.append(
            _clause(
                artifact_id,
                suffix="static",
                title=f"{name} structured grants",
                citation=citation,
                excerpt=excerpt,
                settlement={"mode": "static_grant", "grant_refs": grant_refs},
            )
        )

    mechanic_refs = list(
        dict.fromkeys(
            str(item).strip()
            for item in [
                *list(artifact.get("mechanic_refs") or []),
                *list(card.get("mechanic_refs") or []),
            ]
            if str(item).strip()
        )
    )
    if mechanic_refs:
        clauses.append(
            _clause(
                artifact_id,
                suffix="kernel",
                title=f"{name} registered mechanics",
                citation=citation,
                excerpt=excerpt,
                settlement={
                    "mode": "kernel_mechanic",
                    "mechanic_refs": mechanic_refs,
                },
            )
        )

    has_unsettled_prose = bool(card.get("ruling_requirements")) or (
        not mechanic_refs and card.get("resolution") is None and _has_unsettled_prose(card)
    )
    descriptive_only = (
        str(artifact.get("application_state") or "") == "catalog_only"
        and str(artifact.get("kind") or "") == "class"
    )
    if has_unsettled_prose and not (
        card.get("resolution") is not None and not card.get("ruling_requirements")
    ):
        if descriptive_only:
            clauses.append(
                _clause(
                    artifact_id,
                    suffix="description",
                    title=f"{name} description",
                    citation=citation,
                    excerpt=excerpt,
                    scope="descriptive",
                    settlement={"mode": "descriptive"},
                )
            )
        else:
            ruling_kind = _ruling_kind(artifact)
            reason = (
                f"Apply the remaining source text for bundled {artifact.get('kind') or 'content'} "
                f"{name!r} through Agent-as-DM judgment and public engine operations; "
                "registered mechanics and structured grants remain engine-owned."
            )
            clauses.append(
                _clause(
                    artifact_id,
                    suffix="ruling",
                    title=f"{name} source ruling",
                    citation=citation,
                    excerpt=excerpt,
                    settlement={
                        "mode": "agent_ruling",
                        "default_resolver": "agent",
                        "ruling_kind": ruling_kind,
                        "reason": reason,
                    },
                )
            )
            card["ruling_requirements"] = _settled_ruling_requirements(
                list(card.get("ruling_requirements") or []),
                excerpt=excerpt,
                reason=reason,
                ruling_kind=ruling_kind,
            )

    if not clauses:
        clauses.append(
            _clause(
                artifact_id,
                suffix="description",
                title=f"{name} source record",
                citation=citation,
                excerpt=excerpt,
                scope="descriptive",
                settlement={"mode": "descriptive"},
            )
        )

    compiled = compile_rule_clauses(clauses)
    errors = validate_rule_clause_coverage(
        compiled,
        artifact={**artifact, "card": card},
        mechanic_refs=set(mechanic_refs),
        require_mechanical_clause=False,
    )
    if errors:
        raise ValueError(
            f"bundled artifact {artifact_id} has invalid resolution coverage: " + "; ".join(errors)
        )
    normalized_clauses = rule_clause_templates(compiled)
    modes = {str(clause["settlement"]["mode"]) for clause in normalized_clauses}
    semantic_mode = next(iter(modes)) if len(modes) == 1 else "clause_set"
    artifact["card"] = card
    artifact["rule_clauses"] = normalized_clauses
    artifact["semantic_resolution"] = {
        "status": "resolved",
        "mode": semantic_mode,
        "first_use_compilation_required": False,
        "clause_ids": [str(clause["id"]) for clause in normalized_clauses],
    }
    artifact["execution_state"] = (
        "ruling_ready"
        if modes == {"agent_ruling"}
        else "descriptive_ready"
        if modes == {"descriptive"}
        else "clause_ready"
    )
    artifact["mechanical_scope"] = "descriptive" if modes == {"descriptive"} else "mechanical"
    return artifact


def _clause(
    artifact_id: str,
    *,
    suffix: str,
    title: str,
    citation: dict[str, Any],
    excerpt: str,
    settlement: dict[str, Any],
    scope: str = "mechanical",
) -> dict[str, Any]:
    digest = hashlib.sha256(f"{artifact_id}\x1f{suffix}".encode()).hexdigest()[:16]
    return {
        "schema_version": 1,
        "id": f"bundled-{suffix}-{digest}",
        "title": title[:200],
        "scope": scope,
        "source_citations": [
            {
                "source": citation["source"],
                "source_ref": citation["source_ref"],
                "source_excerpt": excerpt,
            }
        ],
        "settlement": settlement,
    }


def _static_grant_refs(card: dict[str, Any]) -> list[str]:
    refs = [
        f"card.{key}"
        for key, value in card.items()
        if key not in _NON_GRANT_FIELDS and value not in (None, "", [], {})
    ]
    definition = dict(card.get("definition") or {})
    refs.extend(
        f"card.definition.{key}"
        for key, value in definition.items()
        if key != "effect" and value not in (None, "", [], {})
    )
    return sorted(dict.fromkeys(refs))


def _has_unsettled_prose(card: dict[str, Any]) -> bool:
    if list(card.get("ruling_requirements") or []):
        return True
    if any(str(card.get(field) or "").strip() for field in _PROSE_FIELDS):
        return True
    return bool(str(dict(card.get("definition") or {}).get("effect") or "").strip())


def _settled_ruling_requirements(
    requirements: list[dict[str, Any]],
    *,
    excerpt: str,
    reason: str,
    ruling_kind: str,
) -> list[dict[str, Any]]:
    if not requirements:
        requirements = [
            {
                "kind": "source_bound_semantics",
                "reason": reason,
                "source_excerpt": excerpt,
                "default_resolver": "agent",
                "ruling_kind": ruling_kind,
                "requires_external_input_only_for": [],
            }
        ]
    result: list[dict[str, Any]] = []
    for raw in requirements:
        requirement = deepcopy(dict(raw))
        requirement["default_resolver"] = "agent"
        requirement["ruling_kind"] = str(requirement.get("ruling_kind") or ruling_kind)
        requirement["source_excerpt"] = str(requirement.get("source_excerpt") or excerpt)[:4000]
        requirement["reason"] = str(requirement.get("reason") or reason)[:1000]
        requirement["policy_ref"] = "rule_clause.v1"
        requirement.setdefault("requires_external_input_only_for", [])
        result.append(requirement)
    return result


def _ruling_kind(artifact: dict[str, Any]) -> str:
    kind = str(artifact.get("kind") or "")
    if kind == "spell":
        return "generic_spell_effect"
    if kind == "monster":
        return "agent_dm_adjudication"
    return "source_or_scene_fact"


def _artifact_evidence(
    artifact: dict[str, Any],
    *,
    card: dict[str, Any],
    source_root: Path,
    source_prefix: str,
) -> tuple[str, dict[str, Any]]:
    citations = list(artifact.get("source_citations") or [])
    if not citations:
        raise ValueError(f"bundled artifact {artifact.get('id')} has no source citation")
    raw_citation = dict(citations[0])
    source = str(raw_citation.get("source") or "").strip()
    if not source.startswith(source_prefix):
        raise ValueError(
            f"bundled artifact {artifact.get('id')} has an unexpected source {source!r}"
        )
    source_ref = dict(raw_citation.get("source_ref") or {})
    if not source_ref:
        source_ref = {"locator": str(raw_citation.get("locator") or source).strip()}

    candidates = [
        *(
            str(item.get("source_excerpt") or "")
            for item in list(card.get("ruling_requirements") or [])
            if isinstance(item, dict)
        ),
        str(dict(card.get("definition") or {}).get("effect") or ""),
        *(str(card.get(field) or "") for field in _PROSE_FIELDS),
    ]
    excerpt = next(
        (_bounded_text(value) for value in candidates if len(_bounded_text(value)) >= 10),
        "",
    )
    if not excerpt:
        excerpt = _excerpt_from_source(
            source,
            name=str(card.get("name") or ""),
            source_root=source_root,
            source_prefix=source_prefix,
        )
    if len(excerpt) < 10:
        raise ValueError(f"bundled artifact {artifact.get('id')} has no bounded source excerpt")
    return excerpt, {"source": source, "source_ref": source_ref}


def _excerpt_from_source(
    source: str,
    *,
    name: str,
    source_root: Path,
    source_prefix: str,
) -> str:
    relative = source.removeprefix(source_prefix).split("#", 1)[0]
    path = source_root.joinpath(*relative.split("/"))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    folded_name = " ".join(name.split()).casefold()
    start = 0
    if folded_name:
        for index, line in enumerate(lines):
            if folded_name in " ".join(line.split()).casefold():
                start = index
                break
    else:
        match = re.search(r"#L(\d+)$", source)
        if match is not None:
            start = max(0, int(match.group(1)) - 1)
    return _bounded_text("\n".join(lines[start : start + 32]))


def _bounded_text(value: str) -> str:
    return " ".join(str(value).split())[:4000]


__all__ = ["finalize_bundled_artifact_resolutions"]
