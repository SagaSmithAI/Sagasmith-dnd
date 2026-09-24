"""DM-authorized object profiles and bounded attack rulings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.objects import validate_gear_strength_check, validate_object_profile
from sagasmith_dnd.traps import (
    source_trap_object_facts,
    source_trap_profile,
    validate_falling_net_section_spatial_facts,
)

from .. import application_support as support


def bind_falling_net_object_request(
    requested: dict[str, Any],
    *,
    source_content: str,
    bound_trap: dict[str, Any] | None,
    object_id: str,
    scene_id: str,
    source_ref: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Derive fixed Falling Net object values from its active source-bound trap."""
    if not isinstance(bound_trap, dict) or bound_trap.get("profile_id") != "srd5.1.falling_net":
        return deepcopy(requested), None, None
    exact_source_ref = support.canonical_json(source_ref)
    if (
        bound_trap.get("object_id") != object_id
        or bound_trap.get("scene_id") != scene_id
        or bound_trap.get("source_ref") != exact_source_ref
        or bound_trap.get("status") != "triggered"
    ):
        raise ValueError("Falling Net object attack does not match its triggered source and scene")
    profile = source_trap_profile(
        {"profile_id": "srd5.1.falling_net"},
        source_content,
    )
    object_facts = source_trap_object_facts(profile, requested)
    normalized = deepcopy(requested)
    if set(normalized) != {"id", "scene_id"}:
        normalized.update(
            {
                "armor_class": object_facts["armor_class"],
                "hit_points": object_facts["hit_points"],
                "damage_filter": deepcopy(object_facts["damage_filter"]),
            }
        )
    return normalized, profile, object_facts


def reviewed_falling_net_section_facts(
    services: Any,
    *,
    campaign_id: str,
    principal_id: str,
    profile: dict[str, Any],
    facts: Any,
    scene_id: str,
    trap_id: str,
    object_id: str,
    source_ref: dict[str, Any],
    campaign_revision: int,
    restrained_actor_ids: list[str],
    severed_section_ids: list[str],
) -> dict[str, Any]:
    """Resolve current DM and active-scene authority for a Falling Net section."""
    if not services.is_dm(campaign_id, principal_id):
        raise ValueError("Falling Net section facts require an authorized campaign DM")
    active_scene = services.modules.current_scene(campaign_id, scope_id="party")
    active_progress = (
        dict(active_scene.get("progress") or {}) if isinstance(active_scene, dict) else {}
    )
    scene_revision = (
        active_scene.get("state_version", active_progress.get("state_version"))
        if isinstance(active_scene, dict)
        else None
    )
    if (
        not isinstance(active_scene, dict)
        or str(active_scene.get("scene_id") or "") != scene_id
        or type(scene_revision) is not int
    ):
        raise ValueError("Falling Net section facts require the active source scene and revision")
    normalized = validate_falling_net_section_spatial_facts(
        profile,
        facts,
        scene_id=scene_id,
        scene_revision=scene_revision,
        trap_id=trap_id,
        object_id=object_id,
        source_ref=support.canonical_json(source_ref),
        campaign_revision=campaign_revision,
        reviewed_by=principal_id,
        restrained_actor_ids=restrained_actor_ids,
    )
    if normalized["target_section_id"] in set(severed_section_ids):
        raise ValueError("Falling Net section was already destroyed")
    return normalized


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
            "great_weapon_fighting",
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


def review_gear_strength_check(
    services: Any,
    *,
    campaign_id: str,
    branch_id: str,
    principal_id: str,
    profile: dict[str, Any],
    profile_approval: dict[str, Any],
    source_ref: dict[str, Any],
    expanded: dict[str, Any],
    facts: Any,
    ruling: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sign DM-reviewed door/lever/DC facts for one exact reviewed scene object."""

    normalized_profile = validate_object_profile(profile)
    normalized_facts = validate_gear_strength_check(facts)
    review = _review(
        services, campaign_id, principal_id, ruling, expanded, "object gear strength check"
    )
    profile_receipt = support.verify_receipt_signature(
        profile_approval,
        services.content_authority_secret,
        missing_error="object profile approval is missing",
        invalid_error="object profile approval signature is invalid",
    )
    if (
        profile_receipt.get("purpose") != "source_object_profile"
        or profile_receipt.get("campaign_id") != campaign_id
        or profile_receipt.get("profile_digest") != support.json_sha256(normalized_profile)
        or profile_receipt.get("source_ref") != source_ref
    ):
        raise ValueError("object strength review requires its exact approved scene object")
    review_record = {
        "facts": normalized_facts,
        "profile_digest": support.json_sha256(normalized_profile),
        "profile_approval_digest": support.json_sha256(profile_approval),
        "source_ref": source_ref,
        "reviewed_by": principal_id,
        "reason": review["reason"],
        "source_excerpt": review["source_excerpt"],
    }
    approval = support.sign_receipt(
        {
            "purpose": "scene_object.adventuring_gear_strength_check",
            "schema_version": 1,
            "campaign_id": campaign_id,
            "branch_id": branch_id,
            "scene_id": normalized_profile["scene_id"],
            "object_id": normalized_profile["id"],
            "profile_approval_digest": review_record["profile_approval_digest"],
            "review_digest": support.json_sha256(review_record),
            "source_ref": source_ref,
            "reviewed_by": principal_id,
        },
        services.content_authority_secret,
    )
    return review_record, approval


def approved_gear_strength_check(
    services: Any,
    *,
    campaign_id: str,
    branch_id: str,
    profile: dict[str, Any],
    profile_approval: dict[str, Any],
    review: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Verify the immutable scene review before resolving a player gear check."""

    normalized_profile = validate_object_profile(profile)
    profile_receipt = support.verify_receipt_signature(
        profile_approval,
        services.content_authority_secret,
        missing_error="object profile approval is missing",
        invalid_error="object profile approval signature is invalid",
    )
    source_ref = profile_receipt.get("source_ref")
    if (
        profile_receipt.get("purpose") != "source_object_profile"
        or profile_receipt.get("campaign_id") != campaign_id
        or profile_receipt.get("profile_digest") != support.json_sha256(normalized_profile)
        or not isinstance(source_ref, dict)
    ):
        raise ValueError("source object profile approval does not match this campaign object")
    required_review_fields = {
        "facts",
        "profile_digest",
        "profile_approval_digest",
        "source_ref",
        "reviewed_by",
        "reason",
        "source_excerpt",
    }
    if not isinstance(review, dict) or set(review) != required_review_fields:
        raise ValueError("object strength review is missing its signed facts")
    normalized_review = {
        **review,
        "facts": validate_gear_strength_check(review["facts"]),
    }
    expected = {
        "purpose": "scene_object.adventuring_gear_strength_check",
        "schema_version": 1,
        "campaign_id": campaign_id,
        "branch_id": branch_id,
        "scene_id": normalized_profile["scene_id"],
        "object_id": normalized_profile["id"],
        "profile_approval_digest": support.json_sha256(profile_approval),
        "review_digest": support.json_sha256(normalized_review),
        "source_ref": source_ref,
        "reviewed_by": review["reviewed_by"],
    }
    receipt = support.verify_receipt_signature(
        approval,
        services.content_authority_secret,
        missing_error="object gear strength review approval is missing",
        invalid_error="object gear strength review approval signature is invalid",
    )
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("object gear strength review does not match the signed scene object")
    if (
        review["profile_digest"] != support.json_sha256(normalized_profile)
        or review["profile_approval_digest"] != support.json_sha256(profile_approval)
        or review["source_ref"] != source_ref
    ):
        raise ValueError("object gear strength review has a stale profile or source binding")
    return {**normalized_review, "approval": approval}
