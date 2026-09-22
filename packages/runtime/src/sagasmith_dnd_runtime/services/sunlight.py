"""Authorize one scene-grounded illumination ruling for an exact state snapshot."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .. import application_support as support


def state_binding(services: Any, campaign_id: str, actor_id: str) -> dict[str, Any]:
    campaign = services.campaigns.get(campaign_id)
    actor = services.require_campaign_actor(campaign_id, actor_id)
    return {
        "campaign_id": campaign_id,
        "branch_id": services.current_branch_id(campaign_id),
        "campaign_revision": campaign.revision,
        "actor_id": actor_id,
        "actor_revision": actor.revision,
    }


def prepare_context(
    services: Any,
    raw: Any,
    *,
    campaign_id: str,
    actor_id: str,
    principal_id: str,
    mode: str,
    subject_kind: str | None = None,
    subject_id: str | None = None,
    scene_id: str | None = None,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("sunlight context must be an object")
    binding = state_binding(services, campaign_id, actor_id)
    if set(raw) == {"receipt"}:
        review = support.verify_receipt_signature(
            raw["receipt"],
            services.content_authority_secret,
            missing_error="sunlight receipt is missing",
            invalid_error="sunlight receipt signature is invalid",
        )
        if review.get("purpose") != "scene_sunlight" or review.get("mode") != mode:
            raise ValueError("sunlight receipt does not match this operation")
        receipt = deepcopy(raw["receipt"])
    else:
        if not services.is_dm(campaign_id, principal_id):
            raise PermissionError("sunlight facts require a DM source ruling or signed receipt")
        allowed = {
            "subject",
            "actor_in_direct_sunlight",
            "subject_in_direct_sunlight",
            "relies_on_sight",
            "ruling",
            "binding",
        }
        if set(raw) - allowed:
            raise ValueError("sunlight context cannot contain computed modifiers or extra fields")
        facts = {
            key: deepcopy(raw.get(key))
            for key in (
                "subject",
                "actor_in_direct_sunlight",
                "subject_in_direct_sunlight",
                "relies_on_sight",
            )
        }
        facts["actor_id"] = actor_id
        for key in ("actor_in_direct_sunlight", "subject_in_direct_sunlight", "relies_on_sight"):
            if facts[key] is not None and type(facts[key]) is not bool:
                raise ValueError(f"sunlight {key} must be a boolean")
        ruling = raw.get("ruling")
        if not isinstance(ruling, dict) or set(ruling) != {
            "source_ref",
            "source_excerpt",
            "reason",
        }:
            raise ValueError("sunlight ruling requires source_ref, source_excerpt and reason")
        if not isinstance(ruling["reason"], str) or not 10 <= len(ruling["reason"].strip()) <= 1000:
            raise ValueError("sunlight ruling reason must contain 10 to 1000 characters")
        review = {
            "purpose": "scene_sunlight",
            "mode": mode,
            "binding": deepcopy(raw.get("binding")),
            "facts": facts,
            "ruling": deepcopy(ruling),
            "reviewer": principal_id,
        }
        receipt = None
    if review.get("binding") != binding:
        raise support.NeedsRulingError(
            "sunlight ruling is missing or stale for the current actor, branch or campaign",
            missing=("sunlight.current_state_binding",),
        )
    facts = review["facts"]
    subject = facts.get("subject")
    if (
        not isinstance(subject, dict)
        or set(subject) != {"kind", "id", "scene_id"}
        or subject["kind"] not in {"actor", "object", "scene"}
        or any(
            not isinstance(subject[key], str) or not subject[key].strip()
            for key in ("id", "scene_id")
        )
        or (subject_kind is not None and subject["kind"] != subject_kind)
        or (subject_id is not None and subject["id"] != subject_id)
        or (scene_id is not None and subject["scene_id"] != scene_id)
        or facts.get("actor_id") != actor_id
    ):
        raise ValueError(
            "sunlight ruling does not match the observer and perceived/attacked subject"
        )
    if subject["kind"] == "actor":
        services.require_campaign_actor(campaign_id, subject["id"])
    elif subject["kind"] == "scene" and subject["id"] != subject["scene_id"]:
        raise ValueError("a scene perception subject must identify that exact scene")
    elif subject["kind"] == "object" and mode == "perception":
        objects = services.campaigns.get(campaign_id).state.get("scene_objects", {})
        if subject["id"] not in objects.get(subject["scene_id"], {}):
            raise ValueError("sunlight perception subject is not a recorded scene object")
    _, exact, expanded = services.managed_module_source_ref(
        campaign_id,
        review["ruling"]["source_ref"],
        require_exact=True,
        expected_scene_id=subject["scene_id"],
        require_active_module=True,
    )
    assert expanded is not None
    services.managed_module_source_excerpt(
        expanded,
        review["ruling"]["source_excerpt"],
        field="sunlight source_excerpt",
        minimum_length=10,
    )
    if receipt is None:
        review["ruling"]["source_ref"] = exact
        receipt = support.sign_receipt(review, services.content_authority_secret)
    return {"facts": deepcopy(facts), "receipt": receipt}


def prepare_check_facts(
    services: Any,
    facts: dict[str, Any],
    *,
    campaign_id: str,
    actor_id: str,
    principal_id: str,
) -> dict[str, Any]:
    value = deepcopy(facts)
    raw = value.pop("sunlight", None)
    per_actor = value.pop("sunlight_by_actor", {})
    if not isinstance(per_actor, dict) or (raw is not None and per_actor):
        raise ValueError("use sunlight or sunlight_by_actor, not both")
    raw = per_actor.get(actor_id, raw)
    if raw is not None:
        encounter = services.campaigns.get(campaign_id).state.get("combat") or {}
        value["_sunlight"] = prepare_context(
            services,
            raw,
            campaign_id=campaign_id,
            actor_id=actor_id,
            principal_id=principal_id,
            mode="perception",
            scene_id=encounter.get("scene_id") if encounter.get("active") else None,
        )
    return value


def check_updates(snapshots: list[dict[str, Any]], *facts: dict[str, Any]) -> list[Any]:
    """Keep the reviewed observer cards under CAS in the same check transaction."""
    if not any(f.get("sunlight") or f.get("sunlight_by_actor") for f in facts):
        return []
    return [
        support.CharacterStateUpdate(
            character_id=actor["id"],
            sheet=support.validate_character_sheet(actor["sheet"]),
            notes=support.validate_character_notes(actor["notes"]),
            expected_revision=actor["revision"],
        )
        for actor in snapshots
    ]


def bind_local_contexts(services: Any, name: str, args: dict[str, Any], campaign_id: str) -> None:
    """Local Host supplies snapshot metadata before freezing the operation journal."""
    if name not in {
        "combat_preflight_attack",
        "combat_resolve_attack",
        "combat_reaction_attack",
        "combat_check",
        "character_check",
        "character_action",
        "combat_ready",
    }:
        return
    data = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    actor_id = args.get("actor_id") or args.get("character_id") or data.get("actor_id")
    candidates = []
    action = args.get("action") if isinstance(args.get("action"), dict) else {}
    candidates.append((dict(action.get("context") or {}).get("sunlight"), actor_id))
    candidates.append((data.get("sunlight"), actor_id))
    for raw in data.get("sunlight_contexts") or []:
        candidates.append((raw, actor_id))
    declaration = data.get("declaration") or {}
    if isinstance(declaration, dict):
        for attack in declaration.get("attacks") or []:
            if isinstance(attack, dict):
                candidates.append((dict(attack.get("context") or {}).get("sunlight"), actor_id))
    for container in (args, data):
        for key, owner in (
            ("rule_facts", actor_id),
            ("source_rule_facts", data.get("source_actor_id")),
            ("target_rule_facts", data.get("target_actor_id")),
        ):
            facts = container.get(key) or {}
            if not isinstance(facts, dict):
                continue
            candidates.append((facts.get("sunlight"), owner))
            per_actor = facts.get("sunlight_by_actor") or {}
            if isinstance(per_actor, dict):
                candidates.extend((value, key) for key, value in per_actor.items())
    for raw, owner in candidates:
        if isinstance(raw, dict) and "receipt" not in raw and "binding" not in raw and owner:
            raw["binding"] = state_binding(services, campaign_id, owner)


def prepare_attack_action(
    services: Any,
    action: dict[str, Any],
    *,
    campaign_id: str,
    actor_id: str,
    target_id: str,
    principal_id: str,
    encounter: dict[str, Any],
) -> dict[str, Any]:
    value = deepcopy(action)
    context = value.setdefault("context", {})
    if "sunlight" in context:
        context["sunlight"] = prepare_context(
            services,
            context["sunlight"],
            campaign_id=campaign_id,
            actor_id=actor_id,
            principal_id=principal_id,
            mode="attack",
            subject_kind="actor",
            subject_id=target_id,
            scene_id=encounter.get("scene_id"),
        )
    return value
