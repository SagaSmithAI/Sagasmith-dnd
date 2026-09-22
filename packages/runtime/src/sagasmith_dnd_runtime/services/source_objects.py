"""DM-authorized object profiles and bounded attack rulings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.objects import validate_object_profile

from .. import application_support as support


def project_response(response: dict[str, Any], *, dm: bool) -> dict[str, Any]:
    """Project both fresh and replayed receipts using the caller's current role."""
    if dm:
        return response
    result = {
        key: deepcopy(response[key]) for key in ("status", "campaign_revision") if key in response
    }
    fields = {
        "character": ("id", "name", "revision"),
        "object": ("id", "name", "scene_id", "destroyed"),
        "attack": (
            "hit",
            "critical",
            "fumble",
            "natural",
            "rolls",
            "rerolls",
            "total",
            "bonus",
            "advantage",
            "disadvantage",
        ),
        "damage": ("amount", "expression", "rolled_expression", "rolls", "detail"),
        "ammunition": ("item_id", "name", "quantity", "remaining"),
        "limited_use": ("item_id", "name", "before", "remaining"),
    }
    for key, allowed in fields.items():
        value = response.get(key)
        result[key] = (
            {field: deepcopy(value[field]) for field in allowed if field in value}
            if isinstance(value, dict)
            else None
        )
    return result


def _review(
    services: Any,
    campaign_id: str,
    principal_id: str,
    ruling: Any,
    expanded: dict[str, Any],
    purpose: str,
) -> dict[str, str]:
    if not services.is_dm(campaign_id, principal_id):
        raise ValueError(f"{purpose} requires a DM source review")
    if not isinstance(ruling, dict) or set(ruling) != {"reason", "source_excerpt"}:
        raise ValueError(f"{purpose} requires ruling={{reason, source_excerpt}}")
    reason = ruling["reason"]
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise ValueError(f"{purpose} reason must contain 10 to 1000 characters")
    services.managed_module_source_excerpt(
        expanded, ruling["source_excerpt"], field=f"{purpose} source_excerpt", minimum_length=10
    )
    return {"reason": reason.strip(), "source_excerpt": ruling["source_excerpt"]}


def approved_profile(
    services: Any,
    *,
    campaign_id: str,
    branch_id: str | None,
    principal_id: str,
    requested: dict[str, Any],
    existing: dict[str, Any],
    source_ref: dict[str, Any],
    expanded: dict[str, Any],
    ruling: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    reference_only = set(requested) == {"id", "scene_id"}
    if existing.get("profile_approval"):
        profile = validate_object_profile(existing["profile"])
        receipt = support.verify_receipt_signature(
            existing["profile_approval"],
            services.content_authority_secret,
            missing_error="object profile approval is missing",
            invalid_error="object profile approval signature is invalid",
        )
        expected = {
            "purpose": "source_object_profile",
            "schema_version": 1,
            "campaign_id": campaign_id,
            "profile_digest": support.json_sha256(profile),
            "source_ref": source_ref,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "object profile approval does not match its source, campaign, or statistics"
            )
        if not reference_only and validate_object_profile(requested) != profile:
            raise ValueError("source object id already exists with different reviewed data")
        if requested.get("id") != profile["id"] or requested.get("scene_id") != profile["scene_id"]:
            raise ValueError("object reference does not match its reviewed profile")
        if ruling is not None:
            _review(services, campaign_id, principal_id, ruling, expanded, "object profile")
        hit_points = existing["hit_points"]
        if type(hit_points) is not int or not 0 <= hit_points <= profile["hit_points"]:
            raise ValueError("object remaining hit_points are invalid")
        # A fork inherits the same immutable source facts. Its HP and attack
        # receipts remain branch-scoped by the shared commit boundary; the
        # original review branch remains provenance, not an expiring capability.
        return profile, deepcopy(existing["profile_approval"]), hit_points
    if reference_only:
        raise ValueError(
            "object has no reviewed profile; a DM must supply its source-bound statistics"
        )
    profile = validate_object_profile(requested)
    review = _review(services, campaign_id, principal_id, ruling, expanded, "object profile")
    hit_points = profile["hit_points"]
    if existing:
        # Older records need review, but review must never resurrect an object
        # or rewrite its previously recorded AC, source, or original maximum.
        expected_legacy = {
            "id": profile["id"],
            "scene_id": profile["scene_id"],
            "armor_class": profile["armor_class"],
            "hit_point_maximum": profile["hit_points"],
            "source_ref": source_ref,
        }
        if any(existing.get(key) != value for key, value in expected_legacy.items()):
            raise ValueError(
                "legacy object review cannot replace its recorded source or statistics"
            )
        hit_points = existing["hit_points"]
    if type(hit_points) is not int or not 0 <= hit_points <= profile["hit_points"]:
        raise ValueError("object remaining hit_points are invalid")
    approval = support.sign_receipt(
        {
            "purpose": "source_object_profile",
            "schema_version": 1,
            "campaign_id": campaign_id,
            "branch_id": branch_id,
            "profile_digest": support.json_sha256(profile),
            "source_ref": source_ref,
            "reviewed_by": principal_id,
            "ruling": review,
        },
        services.content_authority_secret,
    )
    return profile, approval, hit_points


def approved_attack_context(
    services: Any,
    *,
    campaign_id: str,
    branch_id: str | None,
    principal_id: str,
    expanded: dict[str, Any],
    source_ref: dict[str, Any],
    character_id: str,
    object_id: str,
    weapon_id: str,
    campaign_revision: int,
    character_revision: int,
    operation_id: str,
    advantage: bool,
    disadvantage: bool,
    ruling: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if type(advantage) is not bool or type(disadvantage) is not bool:
        raise ValueError("object attack advantage and disadvantage must be booleans")
    if not advantage and not disadvantage and ruling is None:
        return None
    range_value = None
    if isinstance(ruling, dict) and "long_range" in ruling:
        ruling = deepcopy(ruling)
        range_value = ruling.pop("long_range")
        if type(range_value) is not bool:
            raise ValueError("object attack long_range must be a boolean")
    review = dict(_review(
        services, campaign_id, principal_id, ruling, expanded, "object attack context",
    ))
    if range_value is not None:
        review["long_range"] = range_value
    return support.sign_receipt(
        {
            "purpose": "source_object_attack_context",
            "schema_version": 1,
            "campaign_id": campaign_id,
            "branch_id": branch_id,
            "source_ref": source_ref,
            "character_id": character_id,
            "object_id": object_id,
            "weapon_id": weapon_id,
            "campaign_revision": campaign_revision,
            "character_revision": character_revision,
            "operation_id": operation_id,
            "advantage": advantage,
            "disadvantage": disadvantage,
            "reviewed_by": principal_id,
            "ruling": review,
        },
        services.content_authority_secret,
    )
