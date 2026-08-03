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
SELECTION_CONTRACT_SCHEMA_VERSION = 2
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
    "class": "dnd5e.character.base_class.v1",
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
    "class": ("skills",),
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
        "proficiency_choices",
        "size",
        "skills",
        "tool_expertise",
        "tools",
        "values_include_species_grants",
    ),
    "spell": ("method", "source_class"),
    "subclass": ("target_class_name",),
}

_CARD_BINDINGS = {
    "activity": ("name",),
    "background": ("name", "background_grants"),
    "class": ("name", "class_definition"),
    "feat": (
        "name",
        "prerequisites",
        "repeatable",
        "selection_requirements",
        "mechanical_grants",
    ),
    "feature": (
        "name",
        "class_name",
        "subclass_name",
        "species_name",
        "minimum_level",
        "repeatable_selection_levels",
        "selection_requirements",
        "selection_requirements_by_level",
        "mechanical_grants",
    ),
    "item": ("name", "inventory_template"),
    "species": ("name", "base_species", "grants"),
    "spell": ("name", "classes", "level", "definition"),
    "subclass": (
        "name",
        "class_name",
        "minimum_level",
        "always_prepared_spells",
        "spell_grants",
    ),
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


def background_materializer_errors(binding: Mapping[str, Any]) -> list[str]:
    """Return failures that would make a background choice ambiguous at runtime."""

    grants = binding.get("background_grants")
    if not isinstance(grants, Mapping):
        return ["background card needs background_grants"]
    errors: list[str] = []

    def string_list(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            errors.append(f"background {label} must be an array of non-empty strings")
            return []
        normalized = [item.strip() for item in value]
        if len({item.casefold() for item in normalized}) != len(normalized):
            errors.append(f"background {label} must be distinct")
        return normalized

    fixed_languages = string_list(grants.get("languages", []), "languages")
    fixed_tools = string_list(grants.get("tools", []), "tools")
    string_list(grants.get("spell_list_expansion", []), "spell_list_expansion")
    if not isinstance(grants.get("equipment_item_ids", []), list):
        errors.append("background equipment_item_ids must be an array")
    choices = grants.get("choices", {})
    if not isinstance(choices, Mapping):
        errors.append("background choices must be an object")
        return errors

    def choice_count(field: str) -> int:
        raw = choices.get(field, 0)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 5:
            errors.append(f"background {field} must be an integer from 0 to 5")
            return 0
        return raw

    language_count = choice_count("language_count")
    tool_count = choice_count("tool_choice_count")
    language_options = string_list(
        choices.get("language_options", []), "language_options"
    )
    tool_options = string_list(choices.get("tool_options", []), "tool_options")
    allow_any_language = choices.get("allow_any_language", False)
    if not isinstance(allow_any_language, bool):
        errors.append("background allow_any_language must be a boolean")
        allow_any_language = False
    if language_count and not language_options and not allow_any_language:
        errors.append(
            "background language choices need language_options or allow_any_language"
        )
    if language_options and len(language_options) < language_count:
        errors.append("background language_options cannot satisfy language_count")
    if tool_count and len(tool_options) < tool_count:
        errors.append("background tool_options cannot satisfy tool_choice_count")
    if {item.casefold() for item in fixed_languages}.intersection(
        item.casefold() for item in language_options
    ):
        errors.append("background language_options cannot repeat fixed languages")
    if {item.casefold() for item in fixed_tools}.intersection(
        item.casefold() for item in tool_options
    ):
        errors.append("background tool_options cannot repeat fixed tools")

    equipment_packages = choices.get("equipment_packages", {})
    if not isinstance(equipment_packages, Mapping):
        errors.append("background equipment_packages must be an object")
        return errors
    for package_name, raw_package in equipment_packages.items():
        prefix = f"background equipment package {package_name}"
        if not str(package_name).strip():
            errors.append("background equipment package names must not be empty")
        if not isinstance(raw_package, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        unsupported_package = set(raw_package) - {"items", "wallet"}
        if unsupported_package:
            errors.append(
                f"{prefix} has unsupported fields: {sorted(unsupported_package)}"
            )
        items = raw_package.get("items", [])
        if not isinstance(items, list):
            errors.append(f"{prefix} items must be an array")
            items = []
        for index, raw_item in enumerate(items):
            item_prefix = f"{prefix} items[{index}]"
            if not isinstance(raw_item, Mapping):
                errors.append(f"{item_prefix} must be an object")
                continue
            unsupported_item = set(raw_item) - {
                "artifact_id",
                "display_name",
                "inventory_template",
                "quantity",
                "selected_tool",
            }
            if unsupported_item:
                errors.append(
                    f"{item_prefix} has unsupported fields: {sorted(unsupported_item)}"
                )
            sources = [
                bool(str(raw_item.get("artifact_id") or "").strip()),
                raw_item.get("selected_tool") is True,
                isinstance(raw_item.get("inventory_template"), Mapping),
            ]
            if sum(sources) != 1:
                errors.append(
                    f"{item_prefix} needs exactly one of artifact_id, selected_tool, "
                    "or inventory_template"
                )
            if "selected_tool" in raw_item and raw_item.get("selected_tool") is not True:
                errors.append(f"{item_prefix} selected_tool must be true when present")
            if raw_item.get("selected_tool") is True and tool_count != 1:
                errors.append(
                    f"{item_prefix} selected_tool requires exactly one background tool choice"
                )
            quantity = raw_item.get("quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                errors.append(f"{item_prefix} quantity must be a positive integer")
            display_name = raw_item.get("display_name", "")
            if not isinstance(display_name, str):
                errors.append(f"{item_prefix} display_name must be a string")
            template = raw_item.get("inventory_template")
            if isinstance(template, Mapping):
                try:
                    add_inventory_item(
                        default_character_sheet(),
                        {**copy.deepcopy(dict(template)), "quantity": quantity},
                    )
                except ValueError as error:
                    errors.append(f"{item_prefix} inventory_template is invalid: {error}")
        wallet = raw_package.get("wallet", {})
        if not isinstance(wallet, Mapping):
            errors.append(f"{prefix} wallet must be an object")
        else:
            for denomination, amount in wallet.items():
                if str(denomination).casefold() not in {"cp", "sp", "ep", "gp", "pp"}:
                    errors.append(f"{prefix} wallet has an unknown denomination")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    errors.append(f"{prefix} wallet amounts must be non-negative integers")
    return errors


def species_materializer_errors(binding: Mapping[str, Any]) -> list[str]:
    """Return failures for unbounded or internally inconsistent species grants."""

    grants = binding.get("grants")
    if not isinstance(grants, Mapping):
        return ["species card needs grants"]
    errors: list[str] = []

    def string_list(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            errors.append(f"species {label} must be an array of non-empty strings")
            return []
        normalized = [item.strip() for item in value]
        if len({item.casefold() for item in normalized}) != len(normalized):
            errors.append(f"species {label} must be distinct")
        return normalized

    fixed_languages = string_list(grants.get("languages", []), "languages")
    fixed_skills = string_list(
        grants.get("skill_proficiencies", []), "skill_proficiencies"
    )
    fixed_tools = string_list(
        grants.get("tool_proficiencies", []), "tool_proficiencies"
    )
    ability_names = {
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    }
    fixed_increases = grants.get("ability_score_increases", {})
    if not isinstance(fixed_increases, Mapping):
        errors.append("species ability_score_increases must be an object")
        fixed_increases = {}
    for ability, amount in fixed_increases.items():
        if str(ability).casefold() not in ability_names:
            errors.append("species ability_score_increases contains an unknown ability")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            errors.append("species ability_score_increases must use nonnegative integers")
    raw_ability_choice = grants.get("ability_choice", {})
    if not isinstance(raw_ability_choice, Mapping):
        errors.append("species ability_choice must be an object")
        raw_ability_choice = {}
    unsupported_ability_choice = set(raw_ability_choice) - {
        "count",
        "amount",
        "exclude",
        "options",
    }
    if unsupported_ability_choice:
        errors.append(
            "species ability_choice has unsupported fields: "
            f"{sorted(unsupported_ability_choice)}"
        )
    ability_choice_count = raw_ability_choice.get("count", 0)
    if (
        isinstance(ability_choice_count, bool)
        or not isinstance(ability_choice_count, int)
        or not 0 <= ability_choice_count <= len(ability_names)
    ):
        errors.append("species ability_choice.count must be an integer from 0 to 6")
        ability_choice_count = 0
    ability_choice_amount = raw_ability_choice.get("amount", 0)
    if (
        isinstance(ability_choice_amount, bool)
        or not isinstance(ability_choice_amount, int)
        or ability_choice_amount < 0
        or (ability_choice_count > 0 and ability_choice_amount < 1)
    ):
        errors.append(
            "species ability_choice.amount must be a positive integer when choices exist"
        )
    excluded_abilities = [
        item.casefold()
        for item in string_list(raw_ability_choice.get("exclude", []), "ability_choice.exclude")
    ]
    ability_options = [
        item.casefold()
        for item in string_list(raw_ability_choice.get("options", []), "ability_choice.options")
    ]
    if any(item not in ability_names for item in [*excluded_abilities, *ability_options]):
        errors.append("species ability_choice contains an unknown ability")
    if ability_options and len(ability_options) < ability_choice_count:
        errors.append("species ability_choice.options cannot satisfy ability_choice.count")
    if set(excluded_abilities).intersection(ability_options):
        errors.append("species ability_choice.options cannot include an excluded ability")
    raw_groups = grants.get("proficiency_choice_groups", [])
    if not isinstance(raw_groups, list):
        errors.append("species proficiency_choice_groups must be an array")
        raw_groups = []
    group_ids: list[str] = []
    fixed_proficiencies = {
        "language": {item.casefold() for item in fixed_languages},
        "skill": {item.casefold().replace(" ", "_") for item in fixed_skills},
        "tool": {item.casefold() for item in fixed_tools},
        "weapon": {
            item.casefold()
            for item in string_list(
                grants.get("weapon_proficiencies", []), "weapon_proficiencies"
            )
        },
    }
    known_skills = set(default_character_sheet()["skills"])
    for index, raw_group in enumerate(raw_groups):
        prefix = f"species proficiency_choice_groups[{index}]"
        if not isinstance(raw_group, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        unsupported = set(raw_group) - {"id", "count", "options"}
        if unsupported:
            errors.append(f"{prefix} has unsupported fields: {sorted(unsupported)}")
        group_id = str(raw_group.get("id") or "").strip()
        if not group_id:
            errors.append(f"{prefix}.id must not be empty")
        group_ids.append(group_id.casefold())
        group_count = raw_group.get("count", 0)
        if (
            isinstance(group_count, bool)
            or not isinstance(group_count, int)
            or not 1 <= group_count <= 5
        ):
            errors.append(f"{prefix}.count must be an integer from 1 to 5")
            group_count = 0
        raw_options = raw_group.get("options", [])
        if not isinstance(raw_options, list):
            errors.append(f"{prefix}.options must be an array")
            raw_options = []
        option_keys: list[tuple[str, str]] = []
        for option_index, raw_option in enumerate(raw_options):
            option_prefix = f"{prefix}.options[{option_index}]"
            if not isinstance(raw_option, Mapping):
                errors.append(f"{option_prefix} must be an object")
                continue
            unsupported_option = set(raw_option) - {"kind", "name"}
            if unsupported_option:
                errors.append(
                    f"{option_prefix} has unsupported fields: {sorted(unsupported_option)}"
                )
            option_kind = str(raw_option.get("kind") or "").strip().casefold()
            option_name = str(raw_option.get("name") or "").strip()
            if option_kind not in {"language", "skill", "tool", "weapon"}:
                errors.append(f"{option_prefix}.kind is unsupported")
            if not option_name:
                errors.append(f"{option_prefix}.name must not be empty")
            normalized_name = option_name.casefold()
            if option_kind == "skill":
                normalized_name = normalized_name.replace(" ", "_")
                if normalized_name not in known_skills:
                    errors.append(f"{option_prefix} references an unknown skill")
            key = (option_kind, normalized_name)
            option_keys.append(key)
            if normalized_name in fixed_proficiencies.get(option_kind, set()):
                errors.append(f"{option_prefix} repeats a fixed proficiency")
        if len(option_keys) != len(set(option_keys)):
            errors.append(f"{prefix}.options must be distinct")
        if len(option_keys) < group_count:
            errors.append(f"{prefix}.options cannot satisfy count")
    if len(group_ids) != len(set(group_ids)):
        errors.append("species proficiency_choice_groups ids must be distinct")
    language_options = string_list(
        grants.get("language_options", []), "language_options"
    )
    skill_options = string_list(grants.get("skill_options", []), "skill_options")
    tool_options = string_list(
        grants.get("tool_options", grants.get("tool_choices", [])),
        "tool_options",
    )
    size_options = [
        item.casefold()
        for item in string_list(grants.get("size_options", []), "size_options")
    ]
    fixed_size = str(grants.get("size") or "").strip().casefold()
    valid_sizes = {"tiny", "small", "medium", "large"}
    if fixed_size and fixed_size not in valid_sizes:
        errors.append("species size must be Tiny, Small, Medium, or Large")
    if any(item not in valid_sizes for item in size_options):
        errors.append("species size_options contain an unsupported size")
    if fixed_size and size_options:
        errors.append("species cannot declare both fixed size and size_options")

    def count(field: str) -> int:
        raw = grants.get(field, 0)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 5:
            errors.append(f"species {field} must be an integer from 0 to 5")
            return 0
        return raw

    language_count = count("language_choice_count")
    skill_count = count("skill_choice_count")
    tool_count = count("tool_choice_count")
    tool_expertise_count = count("tool_expertise_choice_count")
    tool_expertise_options = string_list(
        grants.get("tool_expertise_options", []), "tool_expertise_options"
    )
    allow_any_tool_expertise = grants.get(
        "allow_any_proficient_tool_expertise", False
    )
    if not isinstance(allow_any_tool_expertise, bool):
        errors.append("species allow_any_proficient_tool_expertise must be a boolean")
        allow_any_tool_expertise = False
    if (
        tool_expertise_count
        and not tool_expertise_options
        and not allow_any_tool_expertise
    ):
        errors.append(
            "species tool expertise choices need options or an explicit proficient-tool choice"
        )
    if tool_expertise_options and len(tool_expertise_options) < tool_expertise_count:
        errors.append(
            "species tool_expertise_options cannot satisfy tool_expertise_choice_count"
        )
    for flag in ("allow_any_language", "allow_any_skill"):
        if flag in grants and not isinstance(grants.get(flag), bool):
            errors.append(f"species {flag} must be a boolean")
    if language_count and not language_options and grants.get("allow_any_language") is not True:
        errors.append(
            "species language choices need language_options or allow_any_language"
        )
    if skill_count and not skill_options and grants.get("allow_any_skill") is not True:
        errors.append("species skill choices need skill_options or allow_any_skill")
    if language_options and len(language_options) < language_count:
        errors.append("species language_options cannot satisfy language_choice_count")
    if skill_options and len(skill_options) < skill_count:
        errors.append("species skill_options cannot satisfy skill_choice_count")
    if tool_count and len(tool_options) < tool_count:
        errors.append("species tool_options cannot satisfy tool_choice_count")
    for fixed, options, label in (
        (fixed_languages, language_options, "language"),
        (fixed_skills, skill_options, "skill"),
        (fixed_tools, tool_options, "tool"),
    ):
        if {item.casefold() for item in fixed}.intersection(
            item.casefold() for item in options
        ):
            errors.append(f"species {label}_options cannot repeat fixed {label}s")
    return errors


def feat_materializer_errors(binding: Mapping[str, Any]) -> list[str]:
    """Validate bounded feat grants and source-constrained spell choices."""

    errors: list[str] = []
    ability_names = {
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    }

    def string_list(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            errors.append(f"feat {label} must be an array of non-empty strings")
            return []
        normalized = [item.strip() for item in value]
        if len({item.casefold() for item in normalized}) != len(normalized):
            errors.append(f"feat {label} must be distinct")
        return normalized

    def validate_spell_grant(
        raw_grant: Any,
        *,
        prefix: str,
        choice_group: bool,
    ) -> None:
        if not isinstance(raw_grant, Mapping):
            errors.append(f"{prefix} must be an object")
            return
        required = {
            "level",
            "eligible_classes",
            "method",
            "spellcasting_ability",
            "free_casts",
            "recovers_on",
            "allow_slot_cast",
        }
        if choice_group:
            required.update({"id", "count"})
        else:
            required.add("name")
        unsupported = set(raw_grant) - required
        missing = required - set(raw_grant)
        if unsupported:
            errors.append(f"{prefix} has unsupported fields: {sorted(unsupported)}")
        if missing:
            errors.append(f"{prefix} has missing fields: {sorted(missing)}")
        if choice_group:
            if not str(raw_grant.get("id") or "").strip():
                errors.append(f"{prefix}.id must not be empty")
            count = raw_grant.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
                errors.append(f"{prefix}.count must be an integer from 1 to 5")
        elif not str(raw_grant.get("name") or "").strip():
            errors.append(f"{prefix}.name must not be empty")
        level = raw_grant.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9:
            errors.append(f"{prefix}.level must be an integer from 0 to 9")
        eligible_classes = string_list(
            raw_grant.get("eligible_classes"),
            f"{prefix.removeprefix('feat ')}.eligible_classes",
        )
        if not eligible_classes:
            errors.append(f"{prefix}.eligible_classes must not be empty")
        if raw_grant.get("method") not in {"known", "limited_use"}:
            errors.append(f"{prefix}.method must be known or limited_use")
        ability = str(raw_grant.get("spellcasting_ability") or "").casefold()
        if ability not in ability_names:
            errors.append(f"{prefix}.spellcasting_ability is invalid")
        free_casts = raw_grant.get("free_casts")
        if (
            isinstance(free_casts, bool)
            or not isinstance(free_casts, int)
            or not 0 <= free_casts <= 9
        ):
            errors.append(f"{prefix}.free_casts must be an integer from 0 to 9")
            free_casts = 0
        recovers_on = raw_grant.get("recovers_on")
        if free_casts and recovers_on not in {"short_rest", "long_rest"}:
            errors.append(
                f"{prefix}.recovers_on must be short_rest or long_rest for free casts"
            )
        if not free_casts and recovers_on is not None:
            errors.append(f"{prefix}.recovers_on must be null without free casts")
        if not isinstance(raw_grant.get("allow_slot_cast"), bool):
            errors.append(f"{prefix}.allow_slot_cast must be a boolean")

    prerequisites = binding.get("prerequisites")
    if prerequisites is not None and not isinstance(prerequisites, list):
        errors.append("feat prerequisites must be an array")
    elif isinstance(prerequisites, list):
        supported_prerequisites = {
            "ability_any_minimum",
            "ability_minimum",
            "feature_forbidden",
            "feature_required",
            "level_minimum",
            "species_required",
        }
        for index, prerequisite in enumerate(prerequisites):
            prefix = f"feat prerequisites[{index}]"
            if not isinstance(prerequisite, Mapping):
                errors.append(f"{prefix} must be an object")
                continue
            kind = str(prerequisite.get("kind") or "")
            if kind not in supported_prerequisites:
                errors.append(f"{prefix}.kind is unsupported")

    grants = binding.get("mechanical_grants")
    if grants is None:
        grants = {}
    if not isinstance(grants, Mapping):
        errors.append("feat mechanical_grants must be an object")
        grants = {}
    supported_grants = {
        "ability_score_increases",
        "maximum_ability_score",
        "languages",
        "tool_proficiencies",
        "weapon_proficiencies",
        "spell_grants",
    }
    unsupported_grants = set(grants) - supported_grants
    if unsupported_grants:
        errors.append(
            f"feat mechanical_grants has unsupported fields: {sorted(unsupported_grants)}"
        )
    increases = grants.get("ability_score_increases", {})
    if not isinstance(increases, Mapping):
        errors.append("feat ability_score_increases must be an object")
        increases = {}
    for ability, amount in increases.items():
        if str(ability).casefold() not in ability_names:
            errors.append("feat ability_score_increases contains an unknown ability")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            errors.append("feat ability_score_increases must use positive integers")
    maximum = grants.get("maximum_ability_score", 20)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 30:
        errors.append("feat maximum_ability_score must be an integer from 1 to 30")
    for field in ("languages", "tool_proficiencies", "weapon_proficiencies"):
        string_list(grants.get(field, []), field)
    raw_spell_grants = grants.get("spell_grants", [])
    if not isinstance(raw_spell_grants, list):
        errors.append("feat spell_grants must be an array")
        raw_spell_grants = []
    fixed_spell_names: list[str] = []
    for index, raw_grant in enumerate(raw_spell_grants):
        validate_spell_grant(
            raw_grant,
            prefix=f"feat spell_grants[{index}]",
            choice_group=False,
        )
        if isinstance(raw_grant, Mapping):
            fixed_spell_names.append(str(raw_grant.get("name") or "").casefold())
    if len(fixed_spell_names) != len(set(fixed_spell_names)):
        errors.append("feat spell_grants must not repeat a spell")

    requirements = binding.get("selection_requirements")
    if requirements is not None and not isinstance(requirements, Mapping):
        errors.append("feat selection_requirements must be an object")
    elif isinstance(requirements, Mapping) and requirements.get("kind") == "spell_grants":
        supported_requirement_fields = {"field", "kind", "groups"}
        unsupported = set(requirements) - supported_requirement_fields
        if unsupported:
            errors.append(
                "feat spell selection requirements have unsupported fields: "
                f"{sorted(unsupported)}"
            )
        if not str(requirements.get("field") or "").strip():
            errors.append("feat spell selection requirements need a field")
        groups = requirements.get("groups")
        if not isinstance(groups, list) or not groups:
            errors.append("feat spell selection requirements need non-empty groups")
            groups = []
        group_ids: list[str] = []
        for index, raw_group in enumerate(groups):
            validate_spell_grant(
                raw_group,
                prefix=f"feat spell selection groups[{index}]",
                choice_group=True,
            )
            if isinstance(raw_group, Mapping):
                group_ids.append(str(raw_group.get("id") or "").casefold())
        if len(group_ids) != len(set(group_ids)):
            errors.append("feat spell selection group ids must be distinct")
    return errors


def subclass_spell_grant_errors(binding: Mapping[str, Any]) -> list[str]:
    """Validate source-reviewed subclass grants without conflating access modes."""

    errors: list[str] = []
    normalized_names: list[str] = []
    legacy = binding.get("always_prepared_spells")
    if legacy is None:
        legacy = []
    if not isinstance(legacy, list):
        return ["subclass always_prepared_spells must be an array"]
    grants = binding.get("spell_grants")
    if grants is None:
        grants = []
    if not isinstance(grants, list):
        return ["subclass spell_grants must be an array"]
    for label, entries, legacy_mode in (
        ("always_prepared_spells", legacy, True),
        ("spell_grants", grants, False),
    ):
        for index, raw_grant in enumerate(entries):
            prefix = f"subclass {label}[{index}]"
            if not isinstance(raw_grant, Mapping):
                errors.append(f"{prefix} must be an object")
                continue
            expected = {"minimum_level", "name"}
            if not legacy_mode:
                expected.add("method")
            unsupported = set(raw_grant) - expected
            missing = expected - set(raw_grant)
            if unsupported:
                errors.append(f"{prefix} has unsupported fields: {sorted(unsupported)}")
            if missing:
                errors.append(f"{prefix} has missing fields: {sorted(missing)}")
            name = str(raw_grant.get("name") or "").strip()
            if not name:
                errors.append(f"{prefix}.name must not be empty")
            else:
                normalized_names.append(name.casefold())
            minimum_level = raw_grant.get("minimum_level")
            if (
                isinstance(minimum_level, bool)
                or not isinstance(minimum_level, int)
                or not 1 <= minimum_level <= 20
            ):
                errors.append(f"{prefix}.minimum_level must be an integer from 1 to 20")
            if not legacy_mode and raw_grant.get("method") not in {
                "always_prepared",
                "known",
                "spellbook",
            }:
                errors.append(
                    f"{prefix}.method must be always_prepared, known, or spellbook"
                )
    if len(normalized_names) != len(set(normalized_names)):
        errors.append("subclass spell grants must not repeat a spell")
    return errors


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
        errors = subclass_spell_grant_errors(binding)
        if errors:
            raise ValueError(errors[0])
    elif kind == "background":
        errors = background_materializer_errors(binding)
        if errors:
            raise ValueError(errors[0])
    elif kind == "species":
        errors = species_materializer_errors(binding)
        if errors:
            raise ValueError(errors[0])
    elif kind == "class":
        definition = binding.get("class_definition")
        if not isinstance(definition, Mapping):
            raise ValueError("class card needs class_definition")
        expected = {
            "armor_proficiencies",
            "hit_die",
            "saving_throw_proficiencies",
            "skill_choice_count",
            "skill_options",
            "tool_proficiencies",
            "weapon_proficiencies",
        }
        if set(definition) != expected:
            raise ValueError("class_definition has missing or unsupported fields")
        hit_die = definition.get("hit_die")
        if isinstance(hit_die, bool) or not isinstance(hit_die, int) or hit_die not in {
            6,
            8,
            10,
            12,
        }:
            raise ValueError("class hit_die must be one of 6, 8, 10, or 12")
        sheet = default_character_sheet()
        saves = definition.get("saving_throw_proficiencies")
        if (
            not isinstance(saves, list)
            or len(saves) != 2
            or len({str(item).casefold() for item in saves}) != 2
            or any(str(item).casefold() not in sheet["abilities"] for item in saves)
        ):
            raise ValueError("class needs exactly two valid saving throw proficiencies")
        options = definition.get("skill_options")
        count = definition.get("skill_choice_count")
        if (
            not isinstance(options, list)
            or not options
            or any(
                str(item).casefold().replace(" ", "_") not in sheet["skills"]
                for item in options
            )
        ):
            raise ValueError("class skill_options must contain known skills")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= len(options):
            raise ValueError("class skill_choice_count is invalid")
        for field in (
            "armor_proficiencies",
            "weapon_proficiencies",
            "tool_proficiencies",
        ):
            values = definition.get(field)
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError(f"class {field} must be an array of names")
    elif kind == "species":
        if not isinstance(binding.get("grants"), Mapping):
            raise ValueError("species card needs grants")
    elif kind == "feat":
        if not isinstance(binding.get("repeatable"), (bool, type(None))):
            raise ValueError("feat repeatable must be a boolean")
        errors = feat_materializer_errors(binding)
        if errors:
            raise ValueError(errors[0])
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
    "feat_materializer_errors",
    "selection_contract_errors",
    "selection_input_errors",
    "selection_schema_for_artifact",
]
