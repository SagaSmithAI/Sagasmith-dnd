"""Strict, persistence-safe validation for dependent actor relations."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from sagasmith_dnd.dependent_actor_lifecycle import validate_steel_defender_lifecycle_policy

_FIELDS = frozenset(
    {
        "owner_character_id",
        "dependent_actor_id",
        "relation_key",
        "source_artifact_id",
        "source_pack_id",
        "source_pack_version",
        "status",
        "created_campaign_revision",
        "created_long_rest_elapsed_ticks",
        "death_elapsed_ticks",
        "revival_started_elapsed_ticks",
        "revival_completes_elapsed_ticks",
        "template_binding",
    }
)
_STATUSES = frozenset({"active", "dead", "replaced"})
_TEMPLATE_BINDING_FIELDS = frozenset(
    {
        "owner_class_name",
        "casting_slot_level",
        "template_variant",
        "numeric_parameters",
        "reviewed_expression_hash",
        "authorization",
    }
)
_AUTHORIZATION_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "purpose",
        "campaign_id",
        "owner_character_id",
        "dependent_actor_id",
        "relation_key",
        "source_artifact_id",
        "source_pack_id",
        "source_pack_version",
        "owner_class_name",
        "casting_slot_level",
        "template_variant",
        "numeric_parameters",
        "reviewed_expression_hash",
    }
)
_AUTHORIZATION_FIELDS = _AUTHORIZATION_PAYLOAD_FIELDS | {"signature"}
_TEMPLATE_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: Any, field: str, *, maximum: int = 500) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must be a non-empty string of at most {maximum} characters")
    return normalized


def _template_binding(value: Any, field: str, *, relation_key: str) -> dict[str, Any]:
    lifecycle_fields = {"lifecycle_policy"} if relation_key == "steel_defender" else set()
    if not isinstance(value, dict) or set(value) != _TEMPLATE_BINDING_FIELDS | lifecycle_fields:
        raise ValueError(f"{field} must contain exactly the template binding fields")
    lifecycle_policy = (
        validate_steel_defender_lifecycle_policy(value["lifecycle_policy"])
        if lifecycle_fields else None
    )
    owner_class_name = value["owner_class_name"]
    if owner_class_name is not None:
        owner_class_name = _text(owner_class_name, f"{field}.owner_class_name", maximum=200)
    casting_slot_level = value["casting_slot_level"]
    if casting_slot_level is not None and (
        isinstance(casting_slot_level, bool)
        or not isinstance(casting_slot_level, int)
        or not 1 <= casting_slot_level <= 9
    ):
        raise ValueError(f"{field}.casting_slot_level must be null or an integer from 1 to 9")
    template_variant = value["template_variant"]
    if template_variant is not None:
        template_variant = _text(template_variant, f"{field}.template_variant", maximum=100)
    parameters = value["numeric_parameters"]
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError(f"{field}.numeric_parameters must be a non-empty object")
    numeric_parameters: dict[str, int] = {}
    for name, raw in parameters.items():
        if (
            not isinstance(name, str)
            or _TEMPLATE_PARAMETER_NAME.fullmatch(name) is None
            or isinstance(raw, bool)
            or not isinstance(raw, int)
        ):
            raise ValueError(f"{field}.numeric_parameters must contain integer values")
        numeric_parameters[name] = raw
    reviewed_expression_hash = value["reviewed_expression_hash"]
    if (
        not isinstance(reviewed_expression_hash, str)
        or _SHA256.fullmatch(reviewed_expression_hash) is None
    ):
        raise ValueError(f"{field}.reviewed_expression_hash must be a SHA-256 hex digest")
    authorization = value["authorization"]
    if not isinstance(authorization, dict) or set(authorization) != (
        _AUTHORIZATION_FIELDS | lifecycle_fields
    ):
        raise ValueError(f"{field}.authorization must contain exactly the authority fields")
    payload = {
        key: deepcopy(authorization[key])
        for key in _AUTHORIZATION_PAYLOAD_FIELDS | lifecycle_fields
    }
    if lifecycle_fields and (
        validate_steel_defender_lifecycle_policy(payload["lifecycle_policy"]) != lifecycle_policy
    ):
        raise ValueError(f"{field}.authorization.lifecycle_policy does not match binding")
    if (
        isinstance(payload["schema_version"], bool)
        or not isinstance(payload["schema_version"], int)
        or payload["schema_version"] != 1
        or payload["purpose"] != "dependent_actor_template"
    ):
        raise ValueError(f"{field}.authorization purpose or schema_version is invalid")
    for key in (
        "campaign_id",
        "owner_character_id",
        "dependent_actor_id",
        "relation_key",
        "source_artifact_id",
        "source_pack_id",
        "source_pack_version",
    ):
        _text(payload[key], f"{field}.authorization.{key}")
    if payload["owner_class_name"] != owner_class_name:
        raise ValueError(f"{field}.authorization.owner_class_name does not match binding")
    if payload["casting_slot_level"] != casting_slot_level:
        raise ValueError(f"{field}.authorization.casting_slot_level does not match binding")
    if payload["template_variant"] != template_variant:
        raise ValueError(f"{field}.authorization.template_variant does not match binding")
    if payload["numeric_parameters"] != numeric_parameters:
        raise ValueError(f"{field}.authorization.numeric_parameters does not match binding")
    if payload["reviewed_expression_hash"] != reviewed_expression_hash:
        raise ValueError(f"{field}.authorization.reviewed_expression_hash does not match binding")
    signature = authorization["signature"]
    if not isinstance(signature, str) or _SHA256.fullmatch(signature) is None:
        raise ValueError(f"{field}.authorization.signature must be a SHA-256 hex digest")
    return {
        "owner_class_name": owner_class_name,
        "casting_slot_level": casting_slot_level,
        "template_variant": template_variant,
        "numeric_parameters": numeric_parameters,
        "reviewed_expression_hash": reviewed_expression_hash,
        **({"lifecycle_policy": lifecycle_policy} if lifecycle_fields else {}),
        "authorization": deepcopy(authorization),
    }


def validate_dependent_actor_relations(value: Any) -> list[dict[str, Any]]:
    """Return a deep-normalized relation list, rejecting ambiguous ownership."""

    if not isinstance(value, list):
        raise ValueError("dependent_actor_relations must be an array")
    normalized: list[dict[str, Any]] = []
    dependent_ids: set[str] = set()
    active_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or set(raw) != _FIELDS:
            raise ValueError(
                f"dependent_actor_relations[{index}] must contain exactly the relation fields"
            )
        relation = deepcopy(raw)
        for field in (
            "owner_character_id",
            "dependent_actor_id",
            "relation_key",
            "source_artifact_id",
            "source_pack_id",
            "source_pack_version",
        ):
            relation[field] = _text(
                relation[field],
                f"dependent_actor_relations[{index}].{field}",
                maximum=200 if field == "relation_key" else 500,
            )
        status = relation["status"]
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError(f"dependent_actor_relations[{index}].status is invalid")
        relation["template_binding"] = _template_binding(
            relation["template_binding"],
            f"dependent_actor_relations[{index}].template_binding",
            relation_key=relation["relation_key"],
        )
        revision = relation["created_campaign_revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError(
                f"dependent_actor_relations[{index}].created_campaign_revision must be "
                "a non-negative integer"
            )
        rest_tick = relation["created_long_rest_elapsed_ticks"]
        if rest_tick is not None and (
            isinstance(rest_tick, bool) or not isinstance(rest_tick, int) or rest_tick < 0
        ):
            raise ValueError(
                f"dependent_actor_relations[{index}].created_long_rest_elapsed_ticks must be "
                "null or a non-negative integer"
            )
        death_tick = relation["death_elapsed_ticks"]
        if death_tick is not None and (
            isinstance(death_tick, bool) or not isinstance(death_tick, int) or death_tick < 0
        ):
            raise ValueError(
                f"dependent_actor_relations[{index}].death_elapsed_ticks must be "
                "null or a non-negative integer"
            )
        revival_start = relation["revival_started_elapsed_ticks"]
        revival_complete = relation["revival_completes_elapsed_ticks"]
        for field_name, tick in (
            ("revival_started_elapsed_ticks", revival_start),
            ("revival_completes_elapsed_ticks", revival_complete),
        ):
            if tick is not None and (
                isinstance(tick, bool) or not isinstance(tick, int) or tick < 0
            ):
                raise ValueError(
                    f"dependent_actor_relations[{index}].{field_name} must be "
                    "null or a non-negative integer"
                )
        if status == "active" and (
            death_tick is not None or revival_start is not None or revival_complete is not None
        ):
            raise ValueError("an active dependent actor cannot retain death or revival timing")
        if status in {"dead", "replaced"} and death_tick is None:
            raise ValueError("a dead or replaced dependent actor requires its death time")
        if status != "dead" and (revival_start is not None or revival_complete is not None):
            raise ValueError("only a dead dependent actor can have a pending revival")
        if (revival_start is None) != (revival_complete is None):
            raise ValueError("dependent actor revival timing must contain both boundaries")
        if revival_start is not None and (
            revival_start < int(death_tick)
            or revival_complete != revival_start + 10
        ):
            raise ValueError("dependent actor revival must complete exactly 10 ticks after start")
        dependent_id = relation["dependent_actor_id"]
        if dependent_id in dependent_ids:
            raise ValueError("dependent_actor_relations contains a duplicate dependent actor")
        dependent_ids.add(dependent_id)
        active_key = (relation["owner_character_id"], relation["relation_key"])
        if status == "active" and active_key in active_keys:
            raise ValueError(
                "dependent_actor_relations contains multiple active relations for an owner"
            )
        if status == "active":
            active_keys.add(active_key)
        normalized.append(relation)
    return normalized


def validate_dependent_actor_references(relations: Any, actor_ids: Any) -> list[dict[str, Any]]:
    """Validate relations and require both endpoints to exist in one actor set."""

    normalized = validate_dependent_actor_relations(relations)
    if not isinstance(actor_ids, (list, tuple, set, frozenset)):
        raise ValueError("actor_ids must be an array or set")
    known: set[str] = set()
    for index, actor_id in enumerate(actor_ids):
        known.add(_text(actor_id, f"actor_ids[{index}]"))
    for relation in normalized:
        if relation["owner_character_id"] not in known:
            raise ValueError("dependent relation references an unknown owner actor")
        if relation["dependent_actor_id"] not in known:
            raise ValueError("dependent relation references an unknown dependent actor")
    return normalized


__all__ = ["validate_dependent_actor_relations", "validate_dependent_actor_references"]
