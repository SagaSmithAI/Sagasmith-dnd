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

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    normalize_spell_definition,
)

CATALOG_REVIEW_SCHEMA_VERSION = 1
SELECTION_CONTRACT_SCHEMA_VERSION = 1
CATALOG_REVIEW_CHECKS = frozenset(
    {"identity", "classification", "entry_boundary", "references"}
)
CATALOG_REVIEW_ROLES = frozenset({"primary", "critic", "dm"})
CATALOG_REVIEW_METHODS = frozenset({"agent", "deterministic", "human"})
SELECTION_STATUSES = frozenset({"ready", "not_applicable", "blocked"})

# These IDs name reviewed engine entry points, not user-provided functions.  A
# portable package can select one only by supplying the exact, deterministic
# schema derived below from the same content hash.
DND_SELECTION_MATERIALIZERS = {
    "activity": "dnd5e.character.activity.v1",
    "background": "dnd5e.character.background.v1",
    "feat": "dnd5e.character.feat.v1",
    "feature": "dnd5e.character.feature.v1",
    "item": "dnd5e.character.inventory_item.v1",
    "species": "dnd5e.character.species.v1",
    "spell": "dnd5e.character.spell.v1",
    "subclass": "dnd5e.character.subclass.v1",
}

_SELECTION_FIELDS = {
    "activity": (),
    "background": (
        "ability_score_increases",
        "custom_name",
        "equipment_item_ids",
        "equipment_package",
        "languages",
        "origin_feat_selection",
        "skills",
        "tools",
    ),
    "feat": (),
    "feature": (
        "grant_level",
        "initial_setup_full_hp",
        "replace_existing",
        "study_started_elapsed_minutes",
        "study_started_elapsed_ticks",
    ),
    "item": (),
    "species": (
        "abilities",
        "ability_scores_include_species_grants",
        "cantrip_artifact_id",
        "hit_points_include_species_grants",
        "languages",
        "skills",
        "tools",
        "values_include_species_grants",
    ),
    "spell": ("method", "source_class"),
    "subclass": ("target_class_name",),
}

_CARD_BINDINGS = {
    "activity": ("name",),
    "background": ("name", "background_grants"),
    "feat": ("name", "prerequisites", "repeatable", "selection_requirements"),
    "feature": (
        "name",
        "class_name",
        "subclass_name",
        "minimum_level",
        "repeatable_selection_levels",
        "selection_requirements",
        "selection_requirements_by_level",
        "mechanical_grants",
    ),
    "item": ("name", "inventory_template"),
    "species": ("name", "base_species", "grants"),
    "spell": ("name", "classes", "level", "definition"),
    "subclass": ("name", "class_name", "minimum_level", "always_prepared_spells"),
}


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

    normalized_status = str(status)
    if normalized_status == "ready" and schema is None:
        schema = selection_schema_for_artifact(artifact)
    if normalized_status == "ready" and materializer is None:
        materializer = DND_SELECTION_MATERIALIZERS.get(str(artifact.get("kind") or ""))
    contract = {
        "schema_version": SELECTION_CONTRACT_SCHEMA_VERSION,
        "status": normalized_status,
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


def selection_schema_for_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only allowed selection surface for a supported artifact kind.

    The schema deliberately contains no executable expression or arbitrary
    payload.  It binds the reviewed card fields that drive an existing D&D
    materializer and publishes the exact input keys that the materializer may
    accept.  The materializer continues to perform all value-level rules
    validation and transactional mutation.
    """

    kind = str(artifact.get("kind") or "")
    if kind not in DND_SELECTION_MATERIALIZERS:
        raise ValueError(f"{kind or 'artifact'} has no safe character materializer")
    card = artifact.get("card")
    if not isinstance(card, Mapping):
        raise ValueError(f"{kind} artifact needs a structured card")
    card_value = dict(card)
    binding = {
        field: copy.deepcopy(card_value.get(field))
        for field in _CARD_BINDINGS[kind]
    }
    _validate_materializer_card(kind, binding)
    selection_fields = list(_SELECTION_FIELDS[kind])
    if kind in {"feat", "feature"}:
        dynamic_fields = _dynamic_selection_fields(kind, card_value)
        selection_fields = sorted(set(selection_fields) | set(dynamic_fields))
    return {
        "artifact_kind": kind,
        "selection_fields": selection_fields,
        "card_binding": binding,
    }


def selection_input_errors(
    artifact: Mapping[str, Any], selection: Mapping[str, Any]
) -> list[str]:
    """Reject facade inputs that are not published by the bound contract."""

    artifact_id = str(artifact.get("id") or "artifact")
    raw = artifact.get("selection_contract")
    if not isinstance(raw, Mapping):
        return [f"{artifact_id}.selection_contract is required"]
    contract_errors = selection_contract_errors(artifact)
    if contract_errors:
        return contract_errors
    contract = dict(raw)
    if contract.get("status") != "ready":
        return [f"{artifact_id}.selection_contract is not ready"]
    schema = dict(contract["schema"])
    allowed = set(schema["selection_fields"])
    unknown = sorted(set(selection) - allowed)
    return (
        [f"{artifact_id}.selection has unsupported fields: {', '.join(unknown)}"]
        if unknown
        else []
    )


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
        kind = str(artifact.get("kind") or "")
        expected_materializer = DND_SELECTION_MATERIALIZERS.get(kind)
        if expected_materializer is None:
            errors.append(f"{prefix} {kind or 'artifact'} has no safe materializer")
        elif materializer != expected_materializer:
            errors.append(
                f"{prefix}.materializer must be {expected_materializer} for {kind}"
            )
        try:
            expected_schema = selection_schema_for_artifact(artifact)
        except ValueError as error:
            errors.append(f"{prefix}.schema: {error}")
        else:
            if schema != expected_schema:
                errors.append(f"{prefix}.schema does not match the reviewed card")
    elif status == "not_applicable":
        if materializer is not None or schema or blockers:
            errors.append(
                f"{prefix} not_applicable status cannot carry materializer, schema, or blockers"
            )
    elif status == "blocked" and not blockers:
        errors.append(f"{prefix} blocked status requires blockers")
    return errors


def _dynamic_selection_fields(kind: str, card: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    requirement_values: list[Any] = []
    if kind == "feat":
        requirement_values.append(card.get("selection_requirements"))
    elif kind == "feature":
        requirement_values.append(card.get("selection_requirements"))
        by_level = card.get("selection_requirements_by_level")
        if isinstance(by_level, Mapping):
            requirement_values.extend(by_level.values())
    for requirement in requirement_values:
        if not isinstance(requirement, Mapping):
            continue
        field = str(requirement.get("field") or "").strip()
        if field:
            values.append(field)
    return values


def _validate_materializer_card(kind: str, binding: Mapping[str, Any]) -> None:
    name = str(binding.get("name") or "").strip()
    if not name:
        raise ValueError(f"{kind} card needs name")
    if kind == "spell":
        classes = binding.get("classes")
        level = binding.get("level")
        if not isinstance(classes, list) or not classes or any(
            not isinstance(value, str) or not value.strip() for value in classes
        ):
            raise ValueError("spell card needs a non-empty classes list")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9:
            raise ValueError("spell card level must be an integer from 0 to 9")
        if not isinstance(binding.get("definition"), Mapping):
            raise ValueError("spell card needs a structured definition")
        try:
            normalize_spell_definition(binding["definition"], "spell.definition")
        except ValueError as error:
            raise ValueError(f"spell definition is invalid: {error}") from error
    elif kind == "subclass":
        if not str(binding.get("class_name") or "").strip():
            raise ValueError("subclass card needs class_name")
        minimum = binding.get("minimum_level")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ValueError("subclass card needs minimum_level >= 1")
        if not isinstance(binding.get("always_prepared_spells"), (list, type(None))):
            raise ValueError("subclass always_prepared_spells must be an array")
    elif kind == "background":
        if not isinstance(binding.get("background_grants"), Mapping):
            raise ValueError("background card needs background_grants")
    elif kind == "species":
        if not isinstance(binding.get("grants"), Mapping):
            raise ValueError("species card needs grants")
    elif kind == "feat":
        if not isinstance(binding.get("prerequisites"), (list, type(None))):
            raise ValueError("feat prerequisites must be an array")
        if not isinstance(binding.get("repeatable"), (bool, type(None))):
            raise ValueError("feat repeatable must be a boolean")
        if not isinstance(binding.get("selection_requirements"), (Mapping, type(None))):
            raise ValueError("feat selection_requirements must be an object")
    elif kind == "feature":
        minimum = binding.get("minimum_level")
        if minimum is not None and (
            isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1
        ):
            raise ValueError("feature minimum_level must be an integer >= 1")
        for field in (
            "selection_requirements",
            "selection_requirements_by_level",
            "mechanical_grants",
        ):
            if not isinstance(binding.get(field), (Mapping, type(None))):
                raise ValueError(f"feature {field} must be an object")
    elif kind == "item":
        if not isinstance(binding.get("inventory_template"), Mapping):
            raise ValueError("item card needs inventory_template")
        try:
            add_inventory_item(
                default_character_sheet(),
                dict(binding["inventory_template"]),
            )
        except ValueError as error:
            raise ValueError(f"item inventory_template is invalid: {error}") from error


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
    "DND_SELECTION_MATERIALIZERS",
    "SELECTION_CONTRACT_SCHEMA_VERSION",
    "SELECTION_STATUSES",
    "build_catalog_review",
    "build_selection_contract",
    "catalog_review_errors",
    "content_fingerprint",
    "selection_contract_errors",
    "selection_input_errors",
    "selection_schema_for_artifact",
]
