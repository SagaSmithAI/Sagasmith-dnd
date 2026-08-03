"""Evidence-bound catalog and character-selection readiness for D&D content.

Portable checksums prove that bytes did not change.  These contracts prove a
different fact: reviewers examined the same semantic content that is about to
be catalogued or materialized.  Runtime settlement remains independent; a
card can be safe to add to a character while its source-specific effect is
still resolved by the Agent-as-DM boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

CATALOG_REVIEW_SCHEMA_VERSION = 1
SELECTION_CONTRACT_SCHEMA_VERSION = 1
CATALOG_REVIEW_CHECKS = frozenset(
    {"identity", "classification", "entry_boundary", "references"}
)
CATALOG_REVIEW_ROLES = frozenset({"primary", "critic", "dm"})
CATALOG_REVIEW_METHODS = frozenset({"agent", "deterministic", "human"})
SELECTION_STATUSES = frozenset({"ready", "not_applicable", "blocked"})


def content_fingerprint(artifact: Mapping[str, Any]) -> str:
    """Hash review-relevant content while excluding the attestations themselves."""

    value = copy.deepcopy(dict(artifact))
    for field in ("catalog_review", "selection_contract", "runtime_contract"):
        value.pop(field, None)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_catalog_review(
    artifact: Mapping[str, Any],
    *,
    decisions: Sequence[Mapping[str, Any]],
    status: str = "approved",
) -> dict[str, Any]:
    """Build and validate a review bound to the artifact's exact content."""

    review = {
        "schema_version": CATALOG_REVIEW_SCHEMA_VERSION,
        "status": status,
        "reviewed_content_hash": content_fingerprint(artifact),
        "decisions": copy.deepcopy(list(decisions)),
    }
    errors = catalog_review_errors({**dict(artifact), "catalog_review": review})
    if errors:
        raise ValueError("; ".join(errors))
    return review


def catalog_review_errors(artifact: Mapping[str, Any]) -> list[str]:
    """Return fail-closed catalog review errors for one D&D artifact."""

    artifact_id = str(artifact.get("id") or "artifact")
    prefix = f"{artifact_id}.catalog_review"
    raw = artifact.get("catalog_review")
    if not isinstance(raw, Mapping):
        return [f"{prefix} is required"]
    review = dict(raw)
    expected_fields = {
        "schema_version",
        "status",
        "reviewed_content_hash",
        "decisions",
    }
    errors = _exact_field_errors(review, expected_fields, prefix)
    if review.get("schema_version") != CATALOG_REVIEW_SCHEMA_VERSION:
        errors.append(
            f"{prefix}.schema_version must be {CATALOG_REVIEW_SCHEMA_VERSION}"
        )
    status = str(review.get("status") or "")
    if status not in {"approved", "needs_review", "rejected"}:
        errors.append(f"{prefix}.status is invalid")
    expected_hash = content_fingerprint(artifact)
    if review.get("reviewed_content_hash") != expected_hash:
        errors.append(f"{prefix}.reviewed_content_hash is stale")
    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        errors.append(f"{prefix}.decisions must be an array")
        decisions = []
    normalized_decisions: list[tuple[str, str, bool]] = []
    for index, raw_decision in enumerate(decisions):
        field = f"{prefix}.decisions[{index}]"
        if not isinstance(raw_decision, Mapping):
            errors.append(f"{field} must be an object")
            continue
        decision = dict(raw_decision)
        errors.extend(
            _exact_field_errors(
                decision,
                {"role", "reviewer", "method", "checks", "notes"},
                field,
            )
        )
        role = str(decision.get("role") or "")
        reviewer = str(decision.get("reviewer") or "").strip()
        method = str(decision.get("method") or "")
        if role not in CATALOG_REVIEW_ROLES:
            errors.append(f"{field}.role is invalid")
        if not reviewer or len(reviewer) > 200:
            errors.append(f"{field}.reviewer must contain 1 to 200 characters")
        if method not in CATALOG_REVIEW_METHODS:
            errors.append(f"{field}.method is invalid")
        checks = decision.get("checks")
        if not isinstance(checks, Mapping):
            errors.append(f"{field}.checks must be an object")
            checks = {}
        else:
            errors.extend(
                _exact_field_errors(
                    dict(checks), CATALOG_REVIEW_CHECKS, f"{field}.checks"
                )
            )
        checks_pass = all(checks.get(check) is True for check in CATALOG_REVIEW_CHECKS)
        if any(not isinstance(checks.get(check), bool) for check in CATALOG_REVIEW_CHECKS):
            errors.append(f"{field}.checks values must be booleans")
        notes = decision.get("notes")
        if not isinstance(notes, str) or len(notes) > 2000:
            errors.append(f"{field}.notes must be a string up to 2000 characters")
        normalized_decisions.append((role, reviewer, checks_pass))

    identities = [(role, reviewer) for role, reviewer, _passed in normalized_decisions]
    if len(identities) != len(set(identities)):
        errors.append(f"{prefix}.decisions must not repeat a reviewer role")
    if status == "approved":
        passed = {
            role: reviewer
            for role, reviewer, checks_pass in normalized_decisions
            if checks_pass
        }
        if "primary" not in passed:
            errors.append(f"{prefix} approved status requires a passing primary review")
        independent_role = "critic" if "critic" in passed else "dm" if "dm" in passed else ""
        if not independent_role:
            errors.append(
                f"{prefix} approved status requires a passing critic or DM review"
            )
        elif passed.get("primary") == passed[independent_role]:
            errors.append(f"{prefix} independent reviewer must differ from primary")
    return errors


def build_selection_contract(
    artifact: Mapping[str, Any],
    *,
    status: str,
    materializer: str | None = None,
    schema: Mapping[str, Any] | None = None,
    references: Sequence[str] = (),
    blockers: Sequence[str] = (),
) -> dict[str, Any]:
    """Build and validate one independently reviewable selection contract."""

    contract = {
        "schema_version": SELECTION_CONTRACT_SCHEMA_VERSION,
        "status": status,
        "reviewed_content_hash": content_fingerprint(artifact),
        "materializer": materializer,
        "schema": copy.deepcopy(dict(schema or {})),
        "references": list(references),
        "blockers": list(blockers),
    }
    errors = selection_contract_errors(
        {**dict(artifact), "selection_contract": contract}
    )
    if errors:
        raise ValueError("; ".join(errors))
    return contract


def selection_contract_errors(artifact: Mapping[str, Any]) -> list[str]:
    """Return contract errors without conflating selection with runtime effects."""

    artifact_id = str(artifact.get("id") or "artifact")
    prefix = f"{artifact_id}.selection_contract"
    raw = artifact.get("selection_contract")
    if not isinstance(raw, Mapping):
        return [f"{prefix} is required"]
    contract = dict(raw)
    expected_fields = {
        "schema_version",
        "status",
        "reviewed_content_hash",
        "materializer",
        "schema",
        "references",
        "blockers",
    }
    errors = _exact_field_errors(contract, expected_fields, prefix)
    if contract.get("schema_version") != SELECTION_CONTRACT_SCHEMA_VERSION:
        errors.append(
            f"{prefix}.schema_version must be {SELECTION_CONTRACT_SCHEMA_VERSION}"
        )
    status = str(contract.get("status") or "")
    if status not in SELECTION_STATUSES:
        errors.append(f"{prefix}.status is invalid")
    if contract.get("reviewed_content_hash") != content_fingerprint(artifact):
        errors.append(f"{prefix}.reviewed_content_hash is stale")
    materializer = contract.get("materializer")
    if materializer is not None and (
        not isinstance(materializer, str)
        or not materializer.startswith("dnd5e.")
        or len(materializer) > 200
    ):
        errors.append(f"{prefix}.materializer must be null or a dnd5e materializer id")
    if not isinstance(contract.get("schema"), dict):
        errors.append(f"{prefix}.schema must be an object")
    for field in ("references", "blockers"):
        values = contract.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            errors.append(f"{prefix}.{field} must be a non-empty-string array")
        elif len(values) != len(set(values)):
            errors.append(f"{prefix}.{field} must be unique")
    blockers = contract.get("blockers") if isinstance(contract.get("blockers"), list) else []
    schema = contract.get("schema") if isinstance(contract.get("schema"), dict) else {}
    if status == "ready":
        if materializer is None:
            errors.append(f"{prefix} ready status requires materializer")
        if blockers:
            errors.append(f"{prefix} ready status cannot have blockers")
    elif status == "not_applicable":
        if materializer is not None or schema or blockers:
            errors.append(
                f"{prefix} not_applicable status cannot carry materializer, schema, or blockers"
            )
    elif status == "blocked" and not blockers:
        errors.append(f"{prefix} blocked status requires blockers")
    return errors


def _exact_field_errors(
    value: Mapping[str, Any], expected: set[str] | frozenset[str], field: str
) -> list[str]:
    missing = sorted(set(expected) - set(value))
    unknown = sorted(set(value) - set(expected))
    errors = []
    if missing:
        errors.append(f"{field} is missing: {', '.join(missing)}")
    if unknown:
        errors.append(f"{field} has unsupported fields: {', '.join(unknown)}")
    return errors


__all__ = [
    "CATALOG_REVIEW_CHECKS",
    "CATALOG_REVIEW_METHODS",
    "CATALOG_REVIEW_ROLES",
    "CATALOG_REVIEW_SCHEMA_VERSION",
    "SELECTION_CONTRACT_SCHEMA_VERSION",
    "SELECTION_STATUSES",
    "build_catalog_review",
    "build_selection_contract",
    "catalog_review_errors",
    "content_fingerprint",
    "selection_contract_errors",
]
