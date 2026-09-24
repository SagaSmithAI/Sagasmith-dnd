"""Inventory application operations with explicit shared services."""

from __future__ import annotations

import math
from typing import Any, Literal

from sagasmith_dnd.adventuring_gear import (
    ADVENTURING_GEAR_SOURCE_REF,
    adventuring_gear_action,
    normalize_gear_intent,
    resolve_adventuring_gear_intent,
)
from sagasmith_dnd.character_schema import effective_ability_scores, effective_size
from sagasmith_dnd.conditions import effect_is_suspended_by_petrification
from sagasmith_dnd.game_time import TICKS_PER_MINUTE
from sagasmith_dnd.objects import validate_object_profile

from .. import application_support as _support


def _authoritative_gear_creature_type(sheet: dict[str, Any]) -> str:
    progression = dict(sheet.get("progression") or {})
    return " ".join(
        str(
            sheet.get("creature_type")
            or progression.get("creature_type")
            or progression.get("species")
            or ""
        )
        .strip()
        .casefold()
        .split()
    )


class InventoryService:
    def source_adventuring_gear_action(self, item: dict[str, Any]) -> Any:
        """Resolve fixed action facts only for exact bundled gear identities."""
        return adventuring_gear_action(item)

    def campaign_adventuring_gear_action(
        self,
        campaign_id: str,
        action_id: str,
        item_id: str,
        intent: str,
        source_ref: str,
        actor_id: str,
        expected_actor_revision: int,
        target_actor_id: str | None = None,
        expected_target_revision: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        action_context: dict[str, Any] | None = None,
        target_object: dict[str, Any] | None = None,
        object_source_ref: dict[str, Any] | None = None,
        object_reason: str | None = None,
        object_ruling: dict[str, Any] | None = None,
        attack_ruling: dict[str, Any] | None = None,
        helper_actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically settle supported source-bound gear actions.

        Rules and resources come from the Domain plan. Caller action_context cannot
        replace source DCs, damage, target facts, or outcomes.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        state = _support.deepcopy(dict(campaign.state or {}))
        normalized_action_id = str(action_id).strip()
        normalized_item_id = str(item_id).strip()
        normalized_actor_id = str(actor_id).strip()
        normalized_target_id = str(target_actor_id or "").strip()
        object_target = target_object is not None
        if helper_actor_id is not None and not object_target:
            raise _support.CombatEngineError(
                "helper_actor_id applies only to Portable Ram object checks"
            )
        normalized_intent = normalize_gear_intent(intent)
        if not normalized_action_id or len(normalized_action_id) > 200:
            raise ValueError("action_id must contain 1 to 200 characters")
        if not normalized_item_id or len(normalized_item_id) > 200:
            raise ValueError("item_id must contain 1 to 200 characters")
        if not normalized_actor_id:
            raise ValueError("actor_id is required")
        if object_target:
            if normalized_target_id:
                raise ValueError("supply target_object or target_actor_id, not both")
            if expected_target_revision is not None:
                raise ValueError("expected_target_revision applies only to a character target")
            if not isinstance(target_object, dict):
                raise ValueError("target_object must be a scene object reference")
        if str(source_ref).strip() != ADVENTURING_GEAR_SOURCE_REF:
            raise _support.CombatEngineError(
                "gear source_ref must match bundled 2014 adventuring gear"
            )
        revisions = [("expected_actor_revision", expected_actor_revision)]
        if normalized_target_id:
            revisions.append(("expected_target_revision", expected_target_revision))
        elif expected_target_revision is not None:
            raise ValueError("expected_target_revision requires a character target")
        for field, value in revisions:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")

        if not object_target and normalized_intent in {"splash", "throw"}:
            return self.campaign_adventuring_gear_attack(
                campaign_id=campaign_id,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                intent=normalized_intent,
                source_ref=ADVENTURING_GEAR_SOURCE_REF,
                actor_id=normalized_actor_id,
                target_actor_id=normalized_target_id,
                expected_actor_revision=expected_actor_revision,
                expected_target_revision=expected_target_revision,
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                action_context=action_context,
            )

        scope = f"campaign-gear-action:{campaign_id}:{resolved_branch_id}:{principal_id}"
        request_payload = {
            "action_id": normalized_action_id,
            "item_id": normalized_item_id,
            "intent": normalized_intent,
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "actor_id": normalized_actor_id,
            "target_actor_id": normalized_target_id if not object_target else None,
            "target_object": _support.deepcopy(target_object) if object_target else None,
            "object_source_ref": _support.deepcopy(object_source_ref) if object_target else None,
            "object_reason": str(object_reason or "").strip() if object_target else None,
            "object_ruling": _support.deepcopy(object_ruling) if object_target else None,
            "attack_ruling": _support.deepcopy(attack_ruling) if object_target else None,
            "action_context": _support.deepcopy(action_context),
            "helper_actor_id": str(helper_actor_id or "").strip() or None,
            "expected_actor_revision": expected_actor_revision,
            "expected_target_revision": expected_target_revision,
            "branch_id": resolved_branch_id,
        }
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            if normalized_intent in {"light", "lower_hood", "raise_hood"}:
                return self.combat_response(campaign_id, principal_id, replay)
            return replay

        if normalized_intent == "extinguish":
            extinguish_owner = self.characters.get(normalized_actor_id)
            extinguish_item = next(
                (
                    candidate
                    for candidate in dict(extinguish_owner.sheet.get("inventory") or {}).get(
                        "items", []
                    )
                    if str(candidate.get("id") or "") == normalized_item_id
                ),
                None,
            )
            if str((extinguish_item or {}).get("name") or "").strip().casefold() in {
                "lamp",
                "lantern, bullseye",
                "lantern, hooded",
                "torch",
            }:
                pass
            else:
                return self.campaign_adventuring_gear_extinguish(
                    campaign_id=campaign_id,
                    action_id=normalized_action_id,
                    item_id=normalized_item_id,
                    source_ref=ADVENTURING_GEAR_SOURCE_REF,
                    actor_id=normalized_actor_id,
                    target_actor_id=normalized_target_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_target_revision=expected_target_revision,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                    action_context=action_context,
                )

        owner = self.characters.get(normalized_actor_id)
        if owner.campaign_id != campaign_id:
            raise ValueError("gear user must belong to the campaign")
        target = None
        if not object_target and normalized_target_id:
            target = self.characters.get(normalized_target_id)
            if target.campaign_id != campaign_id:
                raise ValueError("gear user and target must belong to the campaign")
        owner_sheet = _support.validate_character_sheet(owner.sheet)
        item = next(
            (
                candidate
                for candidate in owner_sheet.get("inventory", {}).get("items", [])
                if str(candidate.get("id") or "") == normalized_item_id
            ),
            None,
        )
        if item is None and normalized_intent == "escape":
            if action_context is not None:
                raise _support.CombatEngineError(
                    "Hunting Trap escape accepts no caller-supplied check or damage outcome"
                )
            hazards = list(dict(state.get("combat") or {}).get("adventuring_gear_hazards") or [])
            trapped_hazard = next(
                (
                    hazard
                    for hazard in hazards
                    if isinstance(hazard, dict)
                    and hazard.get("kind") == "adventuring_gear_ground_hazard"
                    and hazard.get("hazard_kind") == "hunting trap"
                    and hazard.get("item_id") == normalized_item_id
                    and hazard.get("trapped_actor_id") == normalized_target_id
                    and hazard.get("source_ref") == ADVENTURING_GEAR_SOURCE_REF
                    and isinstance(hazard.get("anchor_review"), dict)
                ),
                None,
            )
            if trapped_hazard is not None:
                anchor_review = dict(trapped_hazard.get("anchor_review") or {})
                try:
                    _, exact_ref, expanded = self.managed_module_source_ref(
                        campaign_id,
                        anchor_review.get("source_ref"),
                        require_exact=True,
                        expected_scene_id=str(trapped_hazard.get("scene_id") or ""),
                        require_active_module=True,
                    )
                    if expanded is None or exact_ref != anchor_review.get("source_ref"):
                        raise ValueError("anchor source changed")
                    self.managed_module_source_excerpt(
                        expanded,
                        anchor_review.get("source_excerpt"),
                        field="Hunting Trap anchor source_excerpt",
                        minimum_length=10,
                    )
                except (AssertionError, LookupError, ValueError) as error:
                    raise _support.NeedsRulingError(
                        "Hunting Trap escape requires its active anchor source",
                        missing=("adventuring_gear.immobile_anchor_source",),
                        ruling_kind="missing_or_conflicting_source_review",
                    ) from error
                item = {
                    "id": normalized_item_id,
                    "name": "Hunting trap",
                    "source_key": trapped_hazard.get("source_key"),
                    "quantity": 1,
                }
                plan = resolve_adventuring_gear_intent(
                    {**item, "source_ref": ADVENTURING_GEAR_SOURCE_REF}, "escape"
                )
                return self.settle_hunting_trap_escape(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    target=target,
                    item=item,
                    hazard=trapped_hazard,
                    plan=plan,
                    action_id=normalized_action_id,
                    item_id=normalized_item_id,
                    actor_id=normalized_actor_id,
                    target_actor_id=normalized_target_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_target_revision=int(expected_target_revision),
                    expected_campaign_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    principal_id=principal_id,
                    idempotency_key=str(idempotency_key),
                    scope=scope,
                    request_payload=request_payload,
                )
        if item is None:
            raise ValueError("adventuring gear item is absent from the user's inventory")
        # Inventory templates carry the exact official source_key. The source
        # reference comes from the trusted request and is checked above because
        # legacy materialized templates do not duplicate source_ref on each item.
        source_item = {**item, "source_ref": ADVENTURING_GEAR_SOURCE_REF}
        plan = resolve_adventuring_gear_intent(source_item, normalized_intent)
        gear_name = str(item.get("name") or "").strip().casefold()
        if gear_name == "climber's kit" and normalized_intent in {"anchor", "undo_anchor"}:
            if object_target or normalized_target_id or expected_target_revision is not None:
                raise _support.CombatEngineError("Climber's Kit anchors do not accept a target")
            return self.settle_climber_kit_anchor(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                expected_actor_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if gear_name == "candle" and normalized_intent == "light":
            if object_target or normalized_target_id or expected_target_revision is not None:
                raise _support.CombatEngineError("Candle lighting does not accept a target")
            if action_context is not None:
                raise _support.CombatEngineError(
                    "Candle lighting accepts no caller-supplied time or light outcome"
                )
            if int(item.get("quantity", 0) or 0) < 1:
                raise _support.CombatEngineError("a Candle is required to light it")
            tinderbox = next(
                (
                    candidate
                    for candidate in owner_sheet.get("inventory", {}).get("items", [])
                    if str(candidate.get("name") or "").strip().casefold() == "tinderbox"
                    and str(candidate.get("source_key") or "")
                    == "dnd5e.content.srd2014.item.tinderbox"
                    and int(candidate.get("quantity", 0) or 0) > 0
                ),
                None,
            )
            if tinderbox is None:
                raise _support.CombatEngineError(
                    "lighting a Candle requires an available source-bound Tinderbox"
                )
            return self.campaign_advance_effects(
                campaign_id,
                "minute",
                count=1,
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                expected_elapsed_ticks=int(
                    dict(state.get("game_time") or {}).get("elapsed_ticks", 0) or 0
                )
                + TICKS_PER_MINUTE,
                candle_lighting={
                    "action_id": normalized_action_id,
                    "actor_id": normalized_actor_id,
                    "item_id": normalized_item_id,
                    "source_key": str(item.get("source_key") or ""),
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "expected_actor_revision": expected_actor_revision,
                    "plan": _support.deepcopy(plan),
                    "gear_request_payload": _support.deepcopy(request_payload),
                },
            )
        if gear_name == "block and tackle" and normalized_intent == "hoist":
            if (
                object_target
                or normalized_target_id
                or expected_target_revision is not None
                or any(
                    value is not None
                    for value in (object_source_ref, object_reason, object_ruling, attack_ruling)
                )
            ):
                raise _support.CombatEngineError(
                    "Block and Tackle hoisting accepts its load only through the exact "
                    "DM-reviewed action_context contract"
                )
            return self.settle_adventuring_gear_hoist(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                expected_actor_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if gear_name == "magnifying glass" and normalized_intent == "inspect":
            if not object_target or normalized_target_id or expected_target_revision is not None:
                raise _support.CombatEngineError(
                    "Magnifying Glass inspection requires one reviewed scene object"
                )
            if any(
                value is not None
                for value in (object_source_ref, object_reason, object_ruling, attack_ruling)
            ):
                raise _support.CombatEngineError(
                    "Magnifying Glass uses the stored signed object profile and accepts "
                    "no caller outcome"
                )
            return self.settle_magnifying_glass_inspection(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                target_object=target_object,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                expected_actor_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if gear_name in {"lamp", "lantern, bullseye", "lantern, hooded", "torch"}:
            if object_target or normalized_target_id or expected_target_revision is not None:
                raise _support.CombatEngineError(
                    "lamp and lantern actions do not accept a character or object target"
                )
            return self.settle_adventuring_gear_light_lifecycle(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                expected_actor_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if (
            normalized_intent in {"spread", "pour_ground", "set"}
            and str(item.get("name") or "").strip().casefold()
            in {
                "ball bearings (bag of 1,000)",
                "ball bearings",
                "caltrops (bag of 20)",
                "caltrops",
                "oil (flask)",
                "hunting trap",
            }
            and plan.get("target") in {"ground_area", "level_ground_surface"}
            or (
                normalized_intent == "set"
                and str(item.get("name") or "").strip().casefold() == "hunting trap"
                and plan.get("target") == "ground_location"
            )
        ):
            if normalized_target_id or expected_target_revision is not None:
                raise _support.CombatEngineError(
                    "ground-area deployment does not accept a character target"
                )
            return self.settle_adventuring_gear_ground_deployment(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                expected_actor_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if not normalized_target_id and not object_target:
            raise ValueError("target_actor_id is required for a character target")
        if object_target:
            item_name = str(item.get("name") or "").strip().casefold()
            supported_strength_check = (
                item_name == "crowbar" and normalized_intent == "apply_leverage"
            ) or (item_name == "ram, portable" and normalized_intent == "break_door")
            if supported_strength_check:
                if set(target_object) != {"id", "scene_id"}:
                    raise _support.CombatEngineError(
                        "Crowbar and Portable Ram object targets accept only the reviewed "
                        "object id and scene_id"
                    )
                if any(
                    value is not None
                    for value in (
                        action_context,
                        object_source_ref,
                        object_reason,
                        object_ruling,
                        attack_ruling,
                    )
                ):
                    raise _support.CombatEngineError(
                        "Crowbar and Portable Ram use the stored DM-reviewed object fact; "
                        "caller check DCs and outcomes are not accepted"
                    )
                if helper_actor_id is not None and item_name != "ram, portable":
                    raise _support.CombatEngineError(
                        "only Portable Ram checks accept a helper actor"
                    )
                return self.settle_scene_object_gear_strength_check(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    item=item,
                    plan=plan,
                    target_object=target_object,
                    action_id=normalized_action_id,
                    item_id=normalized_item_id,
                    actor_id=normalized_actor_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_campaign_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    request_payload=request_payload,
                    helper_actor_id=helper_actor_id,
                )
            supported_object_attack = (
                item_name == "acid (vial)" and normalized_intent == "throw"
            ) or (item_name == "alchemist's fire (flask)" and normalized_intent == "throw")
            if helper_actor_id is not None:
                raise _support.CombatEngineError(
                    "only Portable Ram object checks accept a helper actor"
                )
            if not supported_object_attack or not plan.get("attack"):
                raise _support.CombatEngineError(
                    "destructible object targets support only Acid and Alchemist's Fire attacks"
                )
            if not isinstance(object_source_ref, dict):
                raise ValueError("object_source_ref is required for a destructible object attack")
            if not isinstance(object_reason, str) or not object_reason.strip():
                raise ValueError("object_reason is required for a destructible object attack")
            if action_context is not None:
                raise _support.CombatEngineError(
                    "object attacks use reviewed source rulings, not action_context"
                )
            return self.character_source_object_attack(
                normalized_actor_id,
                _support.deepcopy(target_object),
                normalized_item_id,
                _support.deepcopy(object_source_ref),
                str(object_reason or "").strip(),
                principal_id=principal_id,
                expected_revision=expected_actor_revision,
                expected_campaign_revision=expected_revision,
                idempotency_key=idempotency_key,
                object_ruling=_support.deepcopy(object_ruling),
                attack_ruling=_support.deepcopy(attack_ruling),
                branch_id=resolved_branch_id,
                gear_action={
                    "request": {
                        "action_id": normalized_action_id,
                        "item_id": normalized_item_id,
                        "intent": normalized_intent,
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    },
                    "rule_plan": _support.deepcopy(plan),
                },
                idempotency_scope=scope,
                idempotency_payload=request_payload,
            )
        gear_name = str(item.get("name") or "").casefold()
        if gear_name == "manacles":
            return self.settle_adventuring_gear_manacles(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                target=target,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                target_actor_id=normalized_target_id,
                expected_actor_revision=expected_actor_revision,
                expected_target_revision=expected_target_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if (
            str(item.get("name") or "").casefold() == "antitoxin (vial)"
            and normalized_intent == "drink"
        ):
            return self.settle_adventuring_gear_antitoxin(
                campaign,
                state,
                owner,
                target,
                owner_sheet,
                item,
                plan,
                normalized_action_id,
                normalized_item_id,
                normalized_actor_id,
                normalized_target_id,
                expected_actor_revision,
                expected_target_revision,
                expected_revision,
                resolved_branch_id,
                principal_id,
                idempotency_key,
                scope,
                request_payload,
            )
        if plan.get("attack"):
            return self.campaign_adventuring_gear_attack(
                campaign_id=campaign_id,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                intent=normalized_intent,
                source_ref=ADVENTURING_GEAR_SOURCE_REF,
                actor_id=normalized_actor_id,
                target_actor_id=normalized_target_id,
                expected_actor_revision=expected_actor_revision,
                expected_target_revision=expected_target_revision,
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                action_context=action_context,
            )
        if (
            (
                str(item.get("name") or "").casefold() == "lock"
                and normalized_intent in {"pick", "unlock"}
            )
            or (
                str(item.get("name") or "").casefold() == "rope, hempen (50 feet)"
                and normalized_intent == "burst"
            )
            or (
                str(item.get("name") or "").casefold() == "chain (10 feet)"
                and normalized_intent == "burst"
            )
        ):
            return self.settle_adventuring_gear_object_check(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                target_actor_id=normalized_target_id,
                expected_actor_revision=expected_actor_revision,
                expected_target_revision=expected_target_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
                action_context=action_context,
            )
        if (
            str(item.get("name") or "").casefold() == "healer's kit"
            and normalized_intent == "stabilize"
            and bool(dict(state.get("combat") or {}).get("active"))
        ):
            if action_context is not None:
                raise _support.CombatEngineError(
                    "Healer's Kit stabilization uses the bundled automatic effect"
                )
            return self.settle_adventuring_gear_combat_stabilize(
                campaign=campaign,
                state=state,
                owner=owner,
                owner_sheet=owner_sheet,
                target=target,
                item=item,
                plan=plan,
                action_id=normalized_action_id,
                item_id=normalized_item_id,
                actor_id=normalized_actor_id,
                target_actor_id=normalized_target_id,
                expected_actor_revision=expected_actor_revision,
                expected_target_revision=expected_target_revision,
                expected_campaign_revision=expected_revision,
                branch_id=resolved_branch_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                scope=scope,
                request_payload=request_payload,
            )
        if self.authoritative_phase(campaign_id) != _support.PROFILE_PLAY:
            raise _support.CombatEngineError(
                "non-attack adventuring gear actions are available only outside active combat"
            )
        if item.get("name", "").casefold() != "healer's kit" or plan["intent"] != "stabilize":
            raise _support.CombatEngineError(
                "Runtime has no atomic settlement for this source-defined gear intent"
            )

        target_sheet = _support.validate_character_sheet(target.sheet)
        hp = dict(target_sheet.get("combat", {}).get("hp") or {})
        if int(hp.get("value", 0) or 0) != 0:
            raise _support.CombatEngineError(
                "Healer's Kit stabilize requires a target at 0 hit points"
            )
        if normalized_actor_id != normalized_target_id:
            stabilized_sheet = _support.stabilize_sheet(target_sheet)["sheet"]
        else:
            stabilized_sheet = None

        use_key = "healer_s_kit"
        use_state = dict(item.get("uses") or {})
        if not use_state or (
            use_state.get("max") == 0
            and use_state.get("value") == 0
            and not use_state.get("source_key")
        ):
            use_state = {
                "label": "Healer's Kit uses",
                "value": 10,
                "max": 10,
                "unlimited": False,
                "recovers_on": "none",
                "source_key": item["source_key"],
            }
        else:
            use_state = dict(use_state)
            if (
                use_state.get("max") != 10
                or use_state.get("unlimited") is not False
                or use_state.get("recovers_on") != "none"
                or use_state.get("source_key") != item["source_key"]
            ):
                raise _support.CombatEngineError(
                    "Healer's Kit use resource has invalid source identity"
                )
        remaining = use_state.get("value")
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 1:
            raise _support.CombatEngineError("Healer's Kit has no uses remaining")
        use_state["value"] = remaining - 1
        item["uses"] = use_state
        owner_sheet = _support.validate_character_sheet(owner_sheet)

        spends = list(state.get("item_spends") or [])
        if any(
            str(dict(entry).get("id") or "") == normalized_action_id
            for entry in spends
            if isinstance(entry, dict)
        ):
            raise ValueError("gear action_id already exists on this branch")

        character_updates: list[_support.CharacterStateUpdate] = []
        owner_after_sheet = owner_sheet
        target_after_sheet = stabilized_sheet
        if normalized_actor_id == normalized_target_id:
            owner_after_sheet = _support.validate_character_sheet(
                _support.stabilize_sheet(owner_sheet)
            )
            target_after_sheet = owner_after_sheet
            character_updates.append(
                _support.CharacterStateUpdate(
                    owner.id,
                    owner_after_sheet,
                    _support.validate_character_notes(
                        owner.notes, character_type=owner.character_type
                    ),
                    expected_actor_revision,
                )
            )
        else:
            character_updates.extend(
                [
                    _support.CharacterStateUpdate(
                        owner.id,
                        owner_after_sheet,
                        _support.validate_character_notes(
                            owner.notes, character_type=owner.character_type
                        ),
                        expected_actor_revision,
                    ),
                    _support.CharacterStateUpdate(
                        target.id,
                        target_after_sheet,
                        _support.validate_character_notes(
                            target.notes, character_type=target.character_type
                        ),
                        expected_target_revision,
                    ),
                ]
            )
        if (
            normalized_actor_id == normalized_target_id
            and expected_target_revision != expected_actor_revision
        ):
            raise ValueError("same-actor gear use requires equal actor and target revisions")

        response = {
            "status": "committed",
            "action_id": normalized_action_id,
            "item_id": normalized_item_id,
            "intent": plan["intent"],
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": plan,
            "resource": {"key": use_key, "spent": 1, "remaining": remaining - 1},
            "target": {
                "character_id": target.id,
                "stabilization": {"status": "stable", "hp": int(hp.get("value", 0) or 0)},
            },
            "owner": {"kind": "character", "character_id": owner.id},
            "campaign": _support.asdict(
                _support.replace(campaign, state=state, revision=campaign.revision + 1)
            ),
            "character": self.character_view(
                _support.replace(
                    owner,
                    sheet=owner_after_sheet,
                    revision=owner.revision + 1,
                )
            ),
            **(
                {
                    "target_character": self.character_view(
                        _support.replace(
                            target,
                            sheet=target_after_sheet,
                            revision=target.revision + 1,
                        )
                    )
                }
                if target.id != owner.id
                else {}
            ),
        }
        receipt = {
            "id": normalized_action_id,
            "item_id": normalized_item_id,
            "quantity": 1,
            "reason": "Healer's Kit stabilize",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "character_id": owner.id,
            "owner": {"kind": "character", "character_id": owner.id},
            "gear_intent": plan["intent"],
            "target_character_id": target.id,
            "uses_remaining": remaining - 1,
        }
        spends.append(receipt)
        state["item_spends"] = spends
        normalized_state = _support.validate_party_state(state)
        response["campaign"]["state"] = normalized_state
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=normalized_state,
            character_updates=character_updates,
            expected_campaign_revision=expected_revision,
            operation="campaign.adventuring_gear.action",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def settle_adventuring_gear_hoist(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Atomically compare a DM-reviewed scene load against source lift capacity."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError(f"campaign revision conflict: {campaign.id}")
        if owner.campaign_id != campaign.id or owner.id != actor_id:
            raise _support.CombatEngineError("Block and Tackle user must belong to the campaign")
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if item.get("name") != "Block and Tackle" or item.get("source_key") != (
            "dnd5e.content.srd2014.item.block-and-tackle"
        ):
            raise _support.CombatEngineError("hoisting requires the exact source-bound item")
        if (
            plan.get("intent") != "hoist"
            or dict(plan.get("effect") or {}).get("maximum_lift_multiplier") != 4
        ):
            raise _support.CombatEngineError("hoisting requires the bundled four-times rule")
        if not isinstance(action_context, dict) or set(action_context) != {
            "load_object",
            "source_ref",
            "load_weight_lb",
            "review_reason",
        }:
            raise _support.NeedsRulingError(
                "Block and Tackle requires one DM-reviewed load weight bound to an exact "
                "scene object and source",
                missing=("adventuring_gear.reviewed_load_weight_lb",),
                ruling_kind="source_or_scene_fact",
            )
        load_object = action_context["load_object"]
        if (
            not isinstance(load_object, dict)
            or set(load_object) != {"id", "scene_id"}
            or not all(isinstance(load_object.get(key), str) for key in ("id", "scene_id"))
            or not load_object["id"].strip()
            or not load_object["scene_id"].strip()
        ):
            raise _support.CombatEngineError(
                "load_object must identify exactly one scene object by id and scene_id"
            )
        weight_lb = action_context["load_weight_lb"]
        if isinstance(weight_lb, bool) or not isinstance(weight_lb, int) or weight_lb < 0:
            raise _support.CombatEngineError(
                "reviewed load_weight_lb must be a non-negative integer"
            )
        review_reason = str(action_context["review_reason"] or "").strip()
        if not review_reason or len(review_reason) > 600:
            raise _support.CombatEngineError("load weight review requires a bounded reason")

        scene_id = load_object["scene_id"].strip()
        object_id = load_object["id"].strip()
        _, normalized_source_ref, expanded = self.managed_module_source_ref(
            campaign.id,
            action_context["source_ref"],
            require_exact=True,
            expected_scene_id=scene_id,
            require_active_module=True,
        )
        if normalized_source_ref is None or expanded is None:
            raise _support.CombatEngineError("load source must be an exact active scene reference")
        scene_objects = dict(state.get("scene_objects") or {})
        scene_object = dict(dict(scene_objects.get(scene_id) or {}).get(object_id) or {})
        if not scene_object:
            raise _support.CombatEngineError(
                "load must be an existing DM-reviewed scene object with a signed profile"
            )
        profile = validate_object_profile(scene_object.get("profile"))
        profile_approval = scene_object.get("profile_approval")
        approval = _support.verify_receipt_signature(
            profile_approval,
            self.content_authority_secret,
            missing_error="load scene object profile approval is missing",
            invalid_error="load scene object profile approval is invalid",
        )
        expected_profile_digest = _support.json_sha256(profile)
        if (
            profile.get("id") != object_id
            or profile.get("scene_id") != scene_id
            or approval.get("purpose") != "source_object_profile"
            or approval.get("campaign_id") != campaign.id
            or approval.get("profile_digest") != expected_profile_digest
            or approval.get("source_ref") != normalized_source_ref
            or scene_object.get("source_ref") != normalized_source_ref
        ):
            raise _support.CombatEngineError(
                "reviewed load does not match the exact approved scene object and source"
            )

        strength = effective_ability_scores(owner_sheet).get("strength")
        size = effective_size(owner_sheet)
        size_multiplier = {
            "tiny": 0.5,
            "small": 1,
            "medium": 1,
            "large": 2,
            "huge": 4,
            "gargantuan": 8,
        }.get(size)
        if isinstance(strength, bool) or not isinstance(strength, int) or strength < 0:
            raise _support.CombatEngineError("authoritative Strength score is unavailable")
        if size_multiplier is None:
            raise _support.CombatEngineError("effective creature size has no SRD lift multiplier")

        carrying_multiplier = 1.0
        recognized_modifiers: list[dict[str, Any]] = []
        bull_strength_effects = 0
        for effect in owner_sheet.get("effects", []):
            if not effect.get("active") or effect_is_suspended_by_petrification(
                owner_sheet, effect
            ):
                continue
            source_spell_id = str(effect.get("source_spell_id") or "")
            is_bulls_strength = (
                source_spell_id == "dnd5e.content.srd2014.spell.enhance-ability"
                and str(effect.get("source") or "") == "spell.cast"
                and " ".join(str(effect.get("name") or "").casefold().split())
                in {"bull's strength", "bulls strength"}
            )
            has_bulls_strength_multiplier = False
            for change in effect.get("changes", []):
                path = str(change.get("path") or "").casefold()
                if path == "carrying_capacity.multiplier":
                    multiplier = change.get("value")
                    if (
                        not is_bulls_strength
                        or change.get("mode") != "multiply"
                        or isinstance(multiplier, bool)
                        or not isinstance(multiplier, (int, float))
                        or not math.isfinite(float(multiplier))
                        or float(multiplier) != 2.0
                    ):
                        raise _support.CombatEngineError(
                            "unrecognized carrying-capacity modifier; only source-bound "
                            "Bull's Strength x2 is currently supported"
                        )
                    has_bulls_strength_multiplier = True
                elif any(token in path for token in ("carry", "capacity", "lift", "push", "drag")):
                    raise _support.CombatEngineError(
                        "unrecognized carrying-capacity modifier cannot be applied safely"
                    )
            if is_bulls_strength and (not effect.get("changes") or has_bulls_strength_multiplier):
                bull_strength_effects += 1
        if bull_strength_effects > 1:
            raise _support.CombatEngineError(
                "multiple Bull's Strength carrying-capacity effects have unresolved stacking"
            )
        if bull_strength_effects:
            carrying_multiplier = 2.0
            recognized_modifiers.append(
                {
                    "source_spell_id": "dnd5e.content.srd2014.spell.enhance-ability",
                    "effect": "bulls_strength",
                    "multiplier": 2,
                }
            )
        normal_lift_lb = 30 * strength * float(size_multiplier) * carrying_multiplier
        maximum_hoist_lb = normal_lift_lb * int(plan["effect"]["maximum_lift_multiplier"])
        if not math.isfinite(normal_lift_lb) or not math.isfinite(maximum_hoist_lb):
            raise _support.CombatEngineError("derived lift capacity is outside supported bounds")
        lifted_within_capacity = weight_lb <= maximum_hoist_lb

        hoist_result = {
            "status": "within_capacity" if lifted_within_capacity else "over_capacity",
            "within_capacity": lifted_within_capacity,
            "load_weight_lb": weight_lb,
            "normal_lift_capacity_lb": normal_lift_lb,
            "block_and_tackle_capacity_lb": maximum_hoist_lb,
            "strength_score": strength,
            "effective_size": size,
            "size_multiplier": size_multiplier,
            "carrying_capacity_modifiers": recognized_modifiers,
            "load_object": {"id": object_id, "scene_id": scene_id},
            "destination": None,
            "weight_review": {
                "reviewed_by": principal_id,
                "reviewed_campaign_revision": campaign.revision,
                "reason": review_reason,
                "source_ref": normalized_source_ref,
            },
        }
        spends = list(state.get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id for entry in spends
        ):
            raise ValueError("gear action_id already exists on this branch")
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": "Block and Tackle hoist capacity determination",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item.get("source_key"),
            "character_id": actor_id,
            "gear_intent": "hoist",
            "rule_plan": _support.deepcopy(plan),
            "result": _support.deepcopy(hoist_result),
        }
        spends.append(receipt)
        state["item_spends"] = spends
        normalized_state = _support.validate_party_state(state)
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": "hoist",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "hoist": hoist_result,
            "rule_receipts": _support.core_receipts(
                self.effective_rule_context(campaign.id, branch_id=branch_id),
                ["dnd5e.core.encumbrance"],
                "adventuring_gear.block_and_tackle.hoist",
            ),
        }
        return self.commit_campaign_state(
            campaign,
            normalized_state,
            operation="campaign.adventuring_gear.block_and_tackle.hoist",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields=response,
            expected_campaign_revision=expected_campaign_revision,
        )

    def settle_adventuring_gear_light_lifecycle(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Settle bundled Lamp, Lantern, and Torch light state and lifecycle."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for adventuring gear light")
        if owner.campaign_id != campaign.id or owner.id != actor_id:
            raise _support.CombatEngineError("light source owner must belong to the campaign")
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if int(item.get("quantity", 0) or 0) < 1:
            raise _support.CombatEngineError("the source-bound light item is unavailable")
        if str(item.get("source_key") or "") != str(plan.get("source_key") or ""):
            raise _support.CombatEngineError("light item source identity is invalid")
        if plan.get("intent") not in {"light", "extinguish", "lower_hood", "raise_hood"}:
            raise _support.CombatEngineError("unsupported source-defined light intent")

        next_encounter = _support.deepcopy(dict(state.get("combat") or {}))
        if not next_encounter.get("active"):
            raise _support.CombatEngineError(
                "dynamic adventuring gear lights require an active encounter scene"
            )
        if self.encounter_rules_edition(campaign.id, next_encounter) != "2014":
            raise _support.CombatEngineError("adventuring gear lights require the 2014 rules")
        self.require_no_blocking_pending(next_encounter)
        if next_encounter.get("positioning_mode") != "grid":
            raise _support.NeedsRulingError(
                "dynamic gear lights require authoritative Grid geometry",
                missing=("combat.grid_positions",),
                ruling_kind="agent_dm_adjudication",
            )
        battle_map = dict(next_encounter.get("battle_map") or {})
        map_source = dict(battle_map.get("source") or {})
        scene_id = str(next_encounter.get("scene_id") or map_source.get("scene_id") or "").strip()
        map_checksum = str(battle_map.get("checksum") or "").strip()
        map_revision = battle_map.get("map_revision")
        grid = dict(battle_map.get("grid") or {})
        if (
            not scene_id
            or not map_checksum
            or isinstance(map_revision, bool)
            or not isinstance(map_revision, int)
            or map_revision < 1
            or grid.get("kind") != "square"
            or grid.get("cell_ft") != 5
        ):
            raise _support.NeedsRulingError(
                "dynamic gear lights require a signed five-foot square scene map",
                missing=("combat.grid_scene_geometry",),
                ruling_kind="agent_dm_adjudication",
            )
        light_user = self.require_encounter_combatant(
            next_encounter, actor_id, role="adventuring gear light user"
        )
        from sagasmith_dnd.spatial import validate_position

        position = light_user.get("position")
        if not isinstance(position, dict):
            raise _support.NeedsRulingError(
                "dynamic gear lights require the user's recorded grid position",
                missing=("combat.grid_positions",),
                ruling_kind="agent_dm_adjudication",
            )
        validate_position(battle_map, position)

        item_name = str(item.get("name") or "").strip()
        light_records = list(next_encounter.get("adventuring_gear_lights") or [])
        light_id = f"gear-light:{actor_id}:{item_id}"
        matches = [
            record
            for record in light_records
            if isinstance(record, dict) and str(record.get("id") or "") == light_id
        ]
        if len(matches) > 1:
            raise _support.CombatEngineError("duplicate source-bound gear light state")
        prior = _support.deepcopy(matches[0]) if matches else None
        now_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0) or 0)
        next_owner_sheet = _support.validate_character_sheet(owner_sheet)
        consumed_oil: dict[str, Any] | None = None
        action_paid = False

        if plan["intent"] == "light":
            if prior is not None and bool(prior.get("active")):
                raise _support.CombatEngineError("this source-bound gear light is already lit")
            if item_name.casefold() == "torch":
                if plan.get("action_economy") != "action":
                    raise _support.CombatEngineError(
                        "lighting a Torch requires its source-defined action"
                    )
                tinderbox = next(
                    (
                        candidate
                        for candidate in next_owner_sheet.get("inventory", {}).get("items", [])
                        if str(candidate.get("name") or "").strip().casefold() == "tinderbox"
                        and str(candidate.get("source_key") or "")
                        == "dnd5e.content.srd2014.item.tinderbox"
                        and int(candidate.get("quantity", 0) or 0) > 0
                    ),
                    None,
                )
                if tinderbox is None:
                    raise _support.CombatEngineError(
                        "lighting a Torch requires an available source-bound Tinderbox"
                    )
                next_encounter = _support.resolve_common_action(
                    next_encounter,
                    actor_id_value=actor_id,
                    action="use_object",
                    payload={
                        "kind": "adventuring_gear",
                        "intent": "light",
                        "item_id": item_id,
                    },
                )
                action_paid = True
            orientation: str | None = None
            if item_name.casefold() == "lantern, bullseye":
                required_context = {
                    "direction",
                    "scene_id",
                    "map_checksum",
                    "map_revision",
                }
                if not isinstance(action_context, dict) or set(action_context) != required_context:
                    raise _support.NeedsRulingError(
                        "Bullseye Lantern lighting requires a DM-reviewed cone direction",
                        missing=("adventuring_gear.bullseye_orientation_review",),
                        ruling_kind="agent_dm_adjudication",
                    )
                orientation = str(action_context.get("direction") or "").strip().casefold()
                if orientation not in {"north", "east", "south", "west"}:
                    raise _support.CombatEngineError(
                        "Bullseye Lantern direction must be north, east, south, or west"
                    )
                if (
                    action_context.get("scene_id") != scene_id
                    or action_context.get("map_checksum") != map_checksum
                    or action_context.get("map_revision") != map_revision
                ):
                    raise _support.CombatEngineError(
                        "Bullseye Lantern review must match the current scene and map revision"
                    )
            elif action_context is not None:
                raise _support.CombatEngineError(
                    "Lamp, Hooded Lantern, and Torch lighting accept no caller scene facts"
                )

            remaining = int((prior or {}).get("remaining_fuel_ticks", 0) or 0)
            if prior is not None and bool(prior.get("active")):
                remaining = max(
                    0,
                    int(prior.get("fuel_due_elapsed_ticks", now_ticks) or now_ticks) - now_ticks,
                )
            if remaining <= 0:
                resource_cost = dict(plan.get("resource_cost") or {})
                fuel_spec = dict(resource_cost.get("fuel") or {})
                if not fuel_spec and item_name.casefold() == "torch":
                    remaining = int(plan.get("duration_ticks") or 0)
                if not fuel_spec and item_name.casefold() != "torch":
                    raise _support.CombatEngineError("light fuel is unavailable in the source plan")
                fuel_source_key = str(fuel_spec.get("source_key") or "")
                fuel_item = (
                    next(
                        (
                            candidate
                            for candidate in sorted(
                                next_owner_sheet.get("inventory", {}).get("items", []),
                                key=lambda entry: str(entry.get("id") or ""),
                            )
                            if str(candidate.get("source_key") or "") == fuel_source_key
                            and str(candidate.get("name") or "").strip().casefold() == "oil (flask)"
                            and int(candidate.get("quantity", 0) or 0) >= 1
                        ),
                        None,
                    )
                    if fuel_spec
                    else None
                )
                if fuel_spec:
                    if fuel_item is None:
                        raise _support.CombatEngineError(
                            "lighting requires one owned, source-matched Oil (flask)"
                        )
                    next_owner_sheet, _ = _support.remove_inventory_item(
                        next_owner_sheet, str(fuel_item["id"]), 1
                    )
                    consumed_oil = _support.deepcopy(fuel_item)
                    remaining = int(plan.get("duration_ticks") or 0)
            if remaining <= 0:
                raise _support.CombatEngineError("light fuel duration is unavailable")
            prior = {
                "id": light_id,
                "actor_id": actor_id,
                "item_id": item_id,
                "item_name": str(plan.get("item_name") or item_name),
                "source_key": str(item.get("source_key") or ""),
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "active": True,
                "remaining_fuel_ticks": remaining,
                "fuel_due_elapsed_ticks": now_ticks + remaining,
                "hood": "raised",
                "orientation": orientation,
                "reviewer_principal_id": principal_id if orientation else None,
                "reviewed_scene_id": scene_id if orientation else None,
                "reviewed_map_checksum": map_checksum if orientation else None,
                "reviewed_map_revision": map_revision if orientation else None,
                "last_action_id": action_id,
            }
        else:
            if action_context is not None:
                raise _support.CombatEngineError(
                    "light state changes accept no caller-provided outcomes or scene facts"
                )
            if prior is None:
                raise _support.CombatEngineError(
                    "no source-bound light state matches this owner and item"
                )
            if (
                prior.get("actor_id") != actor_id
                or prior.get("item_id") != item_id
                or prior.get("source_key") != item.get("source_key")
                or prior.get("source_ref") != ADVENTURING_GEAR_SOURCE_REF
                or prior.get("item_name") != str(plan.get("item_name") or item_name)
            ):
                raise _support.CombatEngineError("stored light source identity does not match")
            if item_name.casefold() == "lantern, bullseye" and (
                prior.get("reviewed_scene_id") != scene_id
                or prior.get("reviewed_map_checksum") != map_checksum
                or prior.get("reviewed_map_revision") != map_revision
            ):
                raise _support.CombatEngineError(
                    "Bullseye Lantern direction review is stale for the current map"
                )
            if item_name.casefold() == "lantern, bullseye":
                reviewer_principal = str(prior.get("reviewer_principal_id") or "")
                if not reviewer_principal:
                    raise _support.CombatEngineError(
                        "Bullseye Lantern direction has no recorded DM reviewer"
                    )
                self.access.require_campaign(
                    campaign.id,
                    reviewer_principal,
                    roles=_support.CAMPAIGN_DM_ROLES,
                )
            if plan["intent"] == "extinguish":
                if not prior.get("active"):
                    raise _support.CombatEngineError("this source-bound gear light is not lit")
                prior["remaining_fuel_ticks"] = max(
                    0,
                    int(prior.get("fuel_due_elapsed_ticks", now_ticks) or now_ticks) - now_ticks,
                )
                prior["active"] = False
                prior["fuel_due_elapsed_ticks"] = None
            elif plan["intent"] in {"lower_hood", "raise_hood"}:
                if item_name.casefold() != "lantern, hooded":
                    raise _support.CombatEngineError("only a Hooded Lantern has a hood state")
                if not prior.get("active"):
                    raise _support.CombatEngineError("the Hooded Lantern must be lit")
                desired = "lowered" if plan["intent"] == "lower_hood" else "raised"
                if prior.get("hood") == desired:
                    raise _support.CombatEngineError(
                        f"the Hooded Lantern hood is already {desired}"
                    )
                if plan.get("action_economy") == "action":
                    next_encounter = _support.resolve_common_action(
                        next_encounter,
                        actor_id_value=actor_id,
                        action="use_object",
                        payload={
                            "kind": "adventuring_gear",
                            "intent": plan["intent"],
                            "item_id": item_id,
                        },
                    )
                prior["hood"] = desired
                prior["last_action_id"] = action_id
            else:
                raise _support.CombatEngineError("unsupported source-defined light intent")

        existing_spends = list(state.get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id
            for entry in existing_spends
        ):
            raise ValueError("gear action_id already exists on this branch")
        action_receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": f"adventuring gear light:{plan['intent']}",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item.get("source_key"),
            "character_id": actor_id,
            "gear_intent": plan["intent"],
            "rule_plan": _support.deepcopy(plan),
            "resulting_light": _support.deepcopy(prior),
            **({"action_cost": "action", "action_paid": True} if action_paid else {}),
            "reviewer_principal_id": principal_id
            if plan["intent"] == "light" and item_name.casefold() == "lantern, bullseye"
            else None,
        }
        spends = [*existing_spends, action_receipt]
        if consumed_oil is not None:
            fuel_spend_id = f"{action_id}:fuel"
            if any(
                isinstance(entry, dict) and str(entry.get("id") or "") == fuel_spend_id
                for entry in spends
            ):
                raise ValueError("gear fuel action_id already exists on this branch")
            spends.append(
                {
                    "id": fuel_spend_id,
                    "item_id": str(consumed_oil.get("id") or ""),
                    "quantity": 1,
                    "reason": f"Fuel {item_name}",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "source_key": consumed_oil.get("source_key"),
                    "character_id": actor_id,
                    "gear_intent": "fuel",
                    "light_action_id": action_id,
                }
            )
        state["item_spends"] = spends
        light_records = [record for record in light_records if record.get("id") != light_id]
        light_records.append(prior)
        next_encounter["adventuring_gear_lights"] = light_records
        state["combat"] = next_encounter
        response = self.commit_campaign_state(
            campaign,
            _support.validate_party_state(state),
            operation="campaign.adventuring_gear.light_lifecycle",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields={
                "status": "committed",
                "action_id": action_id,
                "item_id": item_id,
                "intent": plan["intent"],
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "rule_plan": _support.deepcopy(plan),
                "light": _support.deepcopy(prior),
                **({"action_cost": "action", "action_paid": True} if action_paid else {}),
                "fuel": {
                    "oil_item_id": str(consumed_oil.get("id") or ""),
                    "quantity_spent": 1,
                    "remaining_fuel_ticks": prior["remaining_fuel_ticks"],
                }
                if consumed_oil is not None
                else {"quantity_spent": 0, "remaining_fuel_ticks": prior["remaining_fuel_ticks"]},
                "owner": {"kind": "character", "character_id": actor_id},
                "combat": next_encounter,
                **(
                    {
                        "character": self.character_view(
                            _support.replace(
                                owner,
                                sheet=next_owner_sheet,
                                revision=owner.revision + 1,
                            )
                        )
                    }
                    if consumed_oil is not None
                    else {}
                ),
            },
            character_updates=(
                [
                    _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=next_owner_sheet,
                        notes=_support.validate_character_notes(
                            owner.notes, character_type=owner.character_type
                        ),
                        expected_revision=owner.revision,
                    )
                ]
                if consumed_oil is not None
                else None
            ),
        )
        return self.combat_response(campaign.id, principal_id, response)

    def settle_hunting_trap_escape(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        target: Any,
        item: dict[str, Any],
        hazard: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve a trapped creature escape and both character updates in one CAS."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for Hunting Trap escape")
        if (
            owner.revision != expected_actor_revision
            or target is None
            or target.revision != expected_target_revision
        ):
            raise ValueError("character revision conflict for Hunting Trap escape")
        if owner.campaign_id != campaign.id or target.campaign_id != campaign.id:
            raise _support.CombatEngineError("Hunting Trap user and target must belong to campaign")
        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        if (
            encounter.get("active") is not True
            or self.encounter_rules_edition(campaign.id, encounter) != "2014"
        ):
            raise _support.CombatEngineError("Hunting Trap escape requires active 2014 combat")
        self.require_no_blocking_pending(encounter)
        self.require_encounter_combatant(encounter, actor_id, role="Hunting Trap rescuer")
        trapped = self.require_encounter_combatant(
            encounter, target_actor_id, role="trapped creature"
        )
        if hazard.get("active") is not False or hazard.get("trapped_actor_id") != target_actor_id:
            raise _support.CombatEngineError("Hunting Trap has no active capture for this target")
        distance = self.madness_grid_distance_ft(encounter, actor_id, target_actor_id)
        if actor_id != target_actor_id and distance > 5:
            raise _support.NeedsRulingError(
                "rescuer must be within reach of the trapped creature",
                missing=("adventuring_gear.rescuer_reach",),
                ruling_kind="agent_dm_adjudication",
            )
        encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=actor_id,
            action="use_object",
            payload={"kind": "adventuring_gear", "intent": "escape", "item_id": item_id},
        )
        stream = _support.active_random_stream()
        if stream is None:
            with _support.use_random_stream(
                _support.CampaignRandomStream.from_campaign_state(
                    campaign.id,
                    campaign.state,
                    operation="campaign.adventuring_gear.hunting_trap_escape",
                    idempotency_key=idempotency_key,
                    campaign_revision=campaign.revision,
                )
            ):
                return self.settle_hunting_trap_escape(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    target=target,
                    item=item,
                    hazard=hazard,
                    plan=plan,
                    action_id=action_id,
                    item_id=item_id,
                    actor_id=actor_id,
                    target_actor_id=target_actor_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_target_revision=expected_target_revision,
                    expected_campaign_revision=expected_campaign_revision,
                    branch_id=branch_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    request_payload=request_payload,
                )
        check_spec = dict(plan.get("check") or {})
        check = _support.resolve_actor_check(
            self.combat_actor_snapshot(actor_id),
            kind="check",
            ability=str(check_spec.get("ability") or "strength"),
            dc=int(check_spec.get("dc") or 13),
            encounter=encounter,
            ruleset="2014",
            rng=stream,
        )
        success = check.get("success") is True
        next_target_sheet = _support.validate_character_sheet(target.sheet)
        damage = None
        stored_hazard = next(
            item
            for item in encounter.get("adventuring_gear_hazards", [])
            if isinstance(item, dict) and item.get("id") == hazard.get("id")
        )
        if success:
            stored_hazard["trapped_actor_id"] = None
            stored_hazard["capture_status"] = "freed"
            stored_hazard["active"] = False
        else:
            damage = _support.apply_damage_parts_to_sheet(
                next_target_sheet,
                [{"amount": 1, "damage_type": "piercing"}],
                source=ADVENTURING_GEAR_SOURCE_REF,
                ruleset="2014",
                death_saves=self.combatant_zero_hp_buffered(trapped),
            )
            next_target_sheet = damage["sheet"]
            self.sync_combatant_conditions(encounter, target_actor_id, next_target_sheet)
        next_state = _support.deepcopy(state)
        next_state["combat"] = encounter
        response_campaign = _support.replace(
            campaign,
            state=_support.validate_party_state(next_state),
            revision=campaign.revision + 1,
        )
        owner_after = _support.replace(
            owner,
            sheet=next_target_sheet if actor_id == target_actor_id else owner_sheet,
            revision=owner.revision + 1,
        )
        target_after = (
            owner_after
            if actor_id == target_actor_id
            else _support.replace(target, sheet=next_target_sheet, revision=target.revision + 1)
        )
        updates = [
            _support.CharacterStateUpdate(
                character_id=owner.id,
                sheet=next_target_sheet if actor_id == target_actor_id else owner_sheet,
                notes=_support.validate_character_notes(
                    owner.notes, character_type=owner.character_type
                ),
                expected_revision=owner.revision,
            )
        ]
        if target_actor_id != actor_id:
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=target_actor_id,
                    sheet=next_target_sheet,
                    notes=target.notes,
                    expected_revision=target.revision,
                )
            )
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": "escape",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "check": check,
            "success": success,
            "damage": None if damage is None else {k: v for k, v in damage.items() if k != "sheet"},
            "campaign": _support.asdict(response_campaign),
            "owner": self.character_view(owner_after),
            **(
                {"target": self.character_view(target_after)} if actor_id != target_actor_id else {}
            ),
            "action_cost": "action",
            "action_paid": True,
        }
        stream_receipt = stream.receipt()
        response["random_stream_receipt"] = stream_receipt
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=response_campaign.state,
            character_updates=updates,
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.hunting_trap_escape",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope, payload=request_payload, response=response
            ),
        )
        return response

    def settle_adventuring_gear_ground_deployment(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Deploy exact bundled ground hazards on a reviewed five-foot combat grid."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for ground-area deployment")
        if owner.campaign_id != campaign.id or owner.id != actor_id:
            raise _support.CombatEngineError("ground-area gear user must belong to the campaign")
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if int(item.get("quantity", 0) or 0) < 1:
            raise _support.CombatEngineError("the source-bound gear item is unavailable")
        item_name = str(item.get("name") or "").strip().casefold()
        is_oil = item_name == "oil (flask)"
        is_hunting_trap = item_name == "hunting trap"
        if plan.get("intent") != (
            "set" if is_hunting_trap else "pour_ground" if is_oil else "spread"
        ) or plan.get("target") != (
            "ground_location"
            if is_hunting_trap
            else "level_ground_surface"
            if is_oil
            else "ground_area"
        ):
            raise _support.CombatEngineError("invalid bundled ground-area action plan")
        if plan.get("action_economy") != "action":
            raise _support.CombatEngineError("ground-area deployment requires its bundled action")
        expected_context_keys = (
            {
                "area_origin",
                "anchor_object_id",
                "anchor_source_ref",
                "anchor_source_excerpt",
                "anchor_reason",
                "trigger_reason",
            }
            if is_hunting_trap
            else {"area_origin", "fire_source_id"}
            if is_oil
            else {"area_origin"}
        )
        if not isinstance(action_context, dict) or set(action_context) != expected_context_keys:
            raise _support.NeedsRulingError(
                (
                    "Hunting Trap deployment requires an exact Grid cell and reviewed "
                    "anchor/pressure-plate facts"
                )
                if is_hunting_trap
                else (
                    "ground-area deployment requires its exact Grid cell and, for Oil, "
                    "reviewed fire source"
                ),
                missing=("adventuring_gear.area_origin", "adventuring_gear.fire_source_id")
                if is_oil
                else (
                    "adventuring_gear.area_origin",
                    "adventuring_gear.immobile_anchor_review",
                    "adventuring_gear.pressure_plate_review",
                )
                if is_hunting_trap
                else ("adventuring_gear.area_origin",),
                ruling_kind="source_or_scene_fact",
            )
        area_origin = action_context.get("area_origin")
        if (
            not isinstance(area_origin, dict)
            or set(area_origin) != {"x", "y"}
            or any(type(area_origin.get(key)) is not int for key in ("x", "y"))
        ):
            raise _support.CombatEngineError("area_origin must contain integer x and y grid cells")
        dimensions_by_item = {
            "ball bearings (bag of 1,000)": (2, 2),
            "ball bearings": (2, 2),
            "caltrops (bag of 20)": (1, 1),
            "caltrops": (1, 1),
            "oil (flask)": (1, 1),
            "hunting trap": (1, 1),
        }
        dimensions = dimensions_by_item.get(item_name)
        if dimensions is None:
            raise _support.CombatEngineError(
                "the current ground-area settlement supports only source-bound "
                "Ball Bearings and Caltrops"
            )
        expected_area = {
            "shape": "square",
            "width_feet": dimensions[0] * 5,
            "depth_feet": dimensions[1] * 5,
        }
        if not is_hunting_trap and plan.get("area") != expected_area:
            raise _support.CombatEngineError("ground-area gear requires its exact bundled square")
        if is_hunting_trap and (
            plan.get("area") != expected_area
            or not all(
                str(action_context.get(key) or "").strip()
                for key in (
                    "anchor_object_id",
                    "anchor_source_ref",
                    "anchor_source_excerpt",
                    "anchor_reason",
                    "trigger_reason",
                )
            )
        ):
            raise _support.NeedsRulingError(
                "Hunting Trap requires a source-bound immobile anchor and pressure-plate review",
                missing=(
                    "adventuring_gear.immobile_anchor_review",
                    "adventuring_gear.pressure_plate_review",
                ),
                ruling_kind="source_or_scene_fact",
            )

        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        if not encounter.get("active"):
            raise _support.CombatEngineError("ground-area deployment requires active combat")
        if self.encounter_rules_edition(campaign.id, encounter) != "2014":
            raise _support.CombatEngineError("ground-area gear deployment requires the 2014 rules")
        self.require_no_blocking_pending(encounter)
        if encounter.get("positioning_mode") != "grid":
            raise _support.NeedsRulingError(
                "ground-area gear requires authoritative Grid geometry",
                missing=("combat.grid_positions",),
                ruling_kind="agent_dm_adjudication",
            )
        scene_id = str(encounter.get("scene_id") or "").strip()
        battle_map = _support.deepcopy(dict(encounter.get("battle_map") or {}))
        grid = dict(battle_map.get("grid") or {})
        bounds = dict(battle_map.get("bounds") or {})
        if not scene_id or grid.get("kind") != "square" or grid.get("cell_ft") != 5:
            raise _support.NeedsRulingError(
                "ground-area gear requires an encounter scene and five-foot square grid",
                missing=("combat.grid_scene_geometry",),
                ruling_kind="agent_dm_adjudication",
            )
        fire_source = None
        anchor_review = None
        if is_oil:
            fire_source = next(
                (
                    record
                    for record in state.get("fire_source_reviews", [])
                    if isinstance(record, dict)
                    and record.get("id") == action_context.get("fire_source_id")
                    and record.get("active") is True
                ),
                None,
            )
            if (
                not isinstance(fire_source, dict)
                or fire_source.get("scene_id") != scene_id
                or fire_source.get("encounter_id") != str(encounter.get("id") or "")
                or fire_source.get("map_revision") != battle_map.get("map_revision")
                or fire_source.get("map_sha256") != _support.json_sha256(battle_map)
                or fire_source.get("cell") != area_origin
                or fire_source.get("level_ground_surface") is not True
            ):
                raise _support.NeedsRulingError(
                    "Oil must be poured in the currently reviewed fire-source cell",
                    missing=("adventuring_gear.current_fire_source",),
                    ruling_kind="missing_or_conflicting_source_review",
                )
            try:
                _, exact_source_ref, expanded = self.managed_module_source_ref(
                    campaign.id,
                    fire_source.get("source_ref"),
                    require_exact=True,
                    expected_scene_id=scene_id,
                    require_active_module=True,
                )
                if expanded is None or exact_source_ref != fire_source.get("source_ref"):
                    raise ValueError("source ref changed")
                self.managed_module_source_excerpt(
                    expanded,
                    fire_source.get("source_excerpt"),
                    field="fire source source_excerpt",
                    minimum_length=10,
                )
            except (AssertionError, LookupError, ValueError) as error:
                raise _support.NeedsRulingError(
                    "Oil fire source no longer matches the active module source",
                    missing=("adventuring_gear.current_fire_source",),
                    ruling_kind="missing_or_conflicting_source_review",
                ) from error
        if is_hunting_trap:
            try:
                _, exact_anchor_ref, expanded_anchor = self.managed_module_source_ref(
                    campaign.id,
                    action_context.get("anchor_source_ref"),
                    require_exact=True,
                    expected_scene_id=scene_id,
                    require_active_module=True,
                )
                if expanded_anchor is None or exact_anchor_ref != action_context.get(
                    "anchor_source_ref"
                ):
                    raise ValueError("anchor source changed")
                self.managed_module_source_excerpt(
                    expanded_anchor,
                    action_context.get("anchor_source_excerpt"),
                    field="Hunting Trap anchor source_excerpt",
                    minimum_length=10,
                )
                anchor_text = str(action_context.get("anchor_source_excerpt") or "").casefold()
                if (
                    "immobile" not in anchor_text
                    or str(action_context.get("anchor_object_id") or "").casefold()
                    not in anchor_text
                ):
                    raise ValueError("anchor excerpt does not bind the named immobile object")
                if (
                    "pressure plate"
                    not in str(action_context.get("trigger_reason") or "").casefold()
                ):
                    raise ValueError("trigger review does not identify the pressure plate")
            except (AssertionError, LookupError, ValueError) as error:
                raise _support.NeedsRulingError(
                    "Hunting Trap anchor must match an active scene source",
                    missing=("adventuring_gear.immobile_anchor_source",),
                    ruling_kind="missing_or_conflicting_source_review",
                ) from error
            anchor_review = {
                "object_id": str(action_context["anchor_object_id"]).strip(),
                "source_ref": _support.deepcopy(action_context["anchor_source_ref"]),
                "source_excerpt": str(action_context["anchor_source_excerpt"]).strip(),
                "reviewed_by": principal_id,
                "reason": str(action_context["anchor_reason"]).strip(),
                "trigger_reason": str(action_context["trigger_reason"]).strip(),
            }
        x, y = area_origin["x"], area_origin["y"]
        width, height = dimensions
        width_cells = bounds.get("width_cells")
        height_cells = bounds.get("height_cells")
        if (
            type(width_cells) is not int
            or type(height_cells) is not int
            or x < 0
            or y < 0
            or x + width > width_cells
            or y + height > height_cells
        ):
            raise _support.CombatEngineError(
                "ground-area deployment must fit within the authoritative battle map"
            )
        cells = [
            {"x": cell_x, "y": cell_y}
            for cell_x in range(x, x + width)
            for cell_y in range(y, y + height)
        ]
        from sagasmith_dnd.spatial import validate_position

        for cell in cells:
            validate_position(battle_map, cell)
        deployer = self.require_encounter_combatant(
            encounter, actor_id, role="ground-area gear user"
        )
        deployer_position = deployer.get("position")
        if not isinstance(deployer_position, dict):
            raise _support.NeedsRulingError(
                "ground-area deployment requires the user's recorded grid position",
                missing=("combat.grid_positions",),
                ruling_kind="agent_dm_adjudication",
            )
        from sagasmith_dnd.spaces import grid_space

        footprint = (
            grid_space(
                deployer,
                (deployer_position["x"], deployer_position["y"]),
                battle_map,
            )["space_ft"]
            // 5
        )
        actor_x, actor_y = int(deployer_position["x"]), int(deployer_position["y"])
        actor_cells = {
            (cell_x, cell_y)
            for cell_x in range(actor_x, actor_x + footprint)
            for cell_y in range(actor_y, actor_y + footprint)
        }
        area_cells = {(cell["x"], cell["y"]) for cell in cells}
        if area_cells & actor_cells:
            raise _support.CombatEngineError(
                "ground-area deployment cannot cover the user's occupied cells"
            )
        gap_x = max(0, actor_x - (x + width - 1), x - (actor_x + footprint - 1))
        gap_y = max(0, actor_y - (y + height - 1), y - (actor_y + footprint - 1))
        if max(gap_x, gap_y) > 1:
            raise _support.NeedsRulingError(
                "ground-area deployment must be within one grid cell of its user",
                missing=("adventuring_gear.deployment_reach",),
                ruling_kind="agent_dm_adjudication",
            )

        next_encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=actor_id,
            action="use_object",
            payload={
                "kind": "adventuring_gear",
                "intent": "set" if is_hunting_trap else "pour_ground" if is_oil else "spread",
                "item_id": item_id,
                "source_key": item.get("source_key"),
            },
        )
        hazard = {
            "id": f"gear-hazard-{_support.uuid4().hex}",
            "kind": "adventuring_gear_ground_hazard",
            "hazard_kind": item_name,
            "item_id": item_id,
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item.get("source_key"),
            "source_actor_id": actor_id,
            "encounter_id": str(encounter.get("id") or ""),
            "scene_id": scene_id,
            "battle_map_sha256": _support.json_sha256(battle_map),
            "area_origin": {"x": x, "y": y},
            "width_cells": width,
            "height_cells": height,
            "cells": [f"{cell['x']},{cell['y']}" for cell in cells],
            "active": True,
            "created_revision": campaign.revision + 1,
            "created_action_id": action_id,
        }
        if is_hunting_trap:
            hazard.update(
                {"anchor_review": anchor_review, "trapped_actor_id": None, "tether_feet": 3}
            )
        if is_oil:
            oil_effect = dict(plan.get("effect") or {}).get("if_lit")
            if (
                not isinstance(oil_effect, dict)
                or oil_effect.get("duration_rounds") != 2
                or oil_effect.get("trigger") != "creature_enters_or_ends_turn_in_area"
                or oil_effect.get("damage") != "5"
                or oil_effect.get("damage_type") != "fire"
                or oil_effect.get("once_per_turn_per_creature") is not True
            ):
                raise _support.CombatEngineError("Oil ground plan differs from its bundled source")
            hazard.update(
                {
                    "oil_lit": True,
                    "fire_source_id": fire_source["id"],
                    "created_round": int(encounter.get("round", 1) or 1),
                    "expires_at_round": int(encounter.get("round", 1) or 1) + 2,
                    "expires_at_actor_id": actor_id,
                    "triggered_turn_tokens": {},
                }
            )
        hazards = list(next_encounter.get("adventuring_gear_hazards") or [])
        if any(
            isinstance(existing, dict) and existing.get("id") == hazard["id"]
            for existing in hazards
        ):
            raise _support.CombatEngineError("ground-area hazard identity already exists")
        hazards.append(hazard)
        next_encounter["adventuring_gear_hazards"] = hazards
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {"type": "adventuring_gear_hazard_deployed", "hazard": _support.deepcopy(hazard)},
        ][-100:]

        owner_sheet = _support.validate_character_sheet(owner_sheet)
        inventory_item = next(
            (
                candidate
                for candidate in owner_sheet.get("inventory", {}).get("items", [])
                if str(candidate.get("id") or "") == item_id
            ),
            None,
        )
        if inventory_item is None or int(inventory_item.get("quantity", 0) or 0) < 1:
            raise _support.CombatEngineError("ground-area gear disappeared during settlement")
        owner_sheet, _ = _support.remove_inventory_item(owner_sheet, item_id, 1)
        remaining_quantity = next(
            (
                int(candidate.get("quantity", 0) or 0)
                for candidate in owner_sheet.get("inventory", {}).get("items", [])
                if str(candidate.get("id") or "") == item_id
            ),
            0,
        )
        spends = list(state.get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id for entry in spends
        ):
            raise ValueError("gear action_id already exists on this branch")
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 1,
            "reason": f"Deploy {item.get('name')}",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item.get("source_key"),
            "character_id": actor_id,
            "owner": {"kind": "character", "character_id": actor_id},
            "gear_intent": plan["intent"],
            "combat_action": "use_object",
            "hazard_id": hazard["id"],
        }
        spends.append(receipt)
        state["item_spends"] = spends
        state["combat"] = next_encounter
        response = self.commit_campaign_state(
            campaign,
            _support.validate_party_state(state),
            operation="campaign.adventuring_gear.deploy_ground_hazard",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields={
                "status": "committed",
                "action_id": action_id,
                "item_id": item_id,
                "intent": plan["intent"],
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "rule_plan": plan,
                "resource": {"quantity_spent": 1, "remaining": remaining_quantity},
                "receipt": receipt,
                "hazard": hazard,
                "owner": {"kind": "character", "character_id": actor_id},
                "combat": next_encounter,
                "character": self.character_view(
                    _support.replace(owner, sheet=owner_sheet, revision=owner.revision + 1)
                ),
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=owner_sheet,
                    notes=_support.validate_character_notes(owner.notes),
                    expected_revision=owner.revision,
                )
            ],
        )
        return self.combat_response(campaign.id, principal_id, response)

    def settle_adventuring_gear_combat_stabilize(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        target: Any,
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Use a source-bound Healer's Kit as one paid combat action."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for Healer's Kit stabilization")
        if self.campaign_rules_edition(campaign.id) != "2014":
            raise _support.CombatEngineError(
                "Healer's Kit combat stabilization requires the 2014 rules"
            )
        if owner.campaign_id != campaign.id or target.campaign_id != campaign.id:
            raise _support.CombatEngineError(
                "Healer's Kit user and target must belong to the campaign"
            )
        if owner.id != actor_id or target.id != target_actor_id:
            raise _support.CombatEngineError(
                "Healer's Kit stabilization requires its exact user and target"
            )
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if target.revision != expected_target_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_target_revision}, "
                f"found {target.revision}"
            )
        if actor_id == target_actor_id:
            raise _support.CombatEngineError(
                "an unconscious 0 HP combatant cannot use a Healer's Kit"
            )
        if int(item.get("quantity", 0) or 0) < 1:
            raise _support.CombatEngineError("the source-bound Healer's Kit is unavailable")
        if plan.get("intent") != "stabilize" or plan.get("action_economy") != "action":
            raise _support.CombatEngineError("invalid bundled Healer's Kit action plan")

        use_key = "healer_s_kit"
        use_state = dict(item.get("uses") or {})
        if not use_state or (
            use_state.get("max") == 0
            and use_state.get("value") == 0
            and not use_state.get("source_key")
        ):
            use_state = {
                "label": "Healer's Kit uses",
                "value": 10,
                "max": 10,
                "unlimited": False,
                "recovers_on": "none",
                "source_key": item["source_key"],
            }
        else:
            use_state = dict(use_state)
            if (
                use_state.get("max") != 10
                or use_state.get("unlimited") is not False
                or use_state.get("recovers_on") != "none"
                or use_state.get("source_key") != item.get("source_key")
            ):
                raise _support.CombatEngineError(
                    "Healer's Kit use resource has invalid source identity"
                )
        remaining = use_state.get("value")
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 1:
            raise _support.CombatEngineError("Healer's Kit has no uses remaining")

        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        if not encounter.get("active"):
            raise _support.CombatEngineError(
                "Healer's Kit combat stabilization requires active combat"
            )
        self.encounter_rules_edition(campaign.id, encounter)
        self.require_no_blocking_pending(encounter)
        self.require_encounter_combatant(encounter, actor_id, role="Healer's Kit user")
        target_combatant = self.require_encounter_combatant(
            encounter, target_actor_id, role="Healer's Kit target"
        )
        if not target_combatant.get("death_saves", False):
            raise _support.CombatEngineError(
                "Healer's Kit stabilization requires a combatant that uses death saves"
            )

        target_sheet = _support.validate_character_sheet(target.sheet)
        hp_value = int(dict(target_sheet.get("combat", {}).get("hp") or {}).get("value", 0) or 0)
        if hp_value != 0:
            raise _support.CombatEngineError(
                "Healer's Kit stabilize requires a target at 0 hit points"
            )
        if int(target_combatant.get("hit_points", -1)) != hp_value:
            raise _support.CombatEngineError(
                "Healer's Kit target hit points diverge from active combat"
            )
        stabilized = _support.stabilize_sheet(target_sheet)
        updated_target_sheet = _support.validate_character_sheet(stabilized["sheet"])

        next_encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=actor_id,
            action="use_object",
            payload={
                "kind": "adventuring_gear",
                "intent": "stabilize",
                "item_id": item_id,
                "source_key": item.get("source_key"),
                "target_actor_id": target_actor_id,
            },
        )
        self.sync_combatant_conditions(next_encounter, target_actor_id, updated_target_sheet)
        _support.reconcile_readied_spells(next_encounter, target_actor_id, updated_target_sheet)
        result = {key: value for key, value in stabilized.items() if key != "sheet"}
        result.update(
            {
                "kind": "healer_kit_stabilization",
                "actor_id": actor_id,
                "target_id": target_actor_id,
                "action": "use_object",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": item.get("source_key"),
                "medicine_check_required": False,
            }
        )

        item["uses"] = use_state
        owner_sheet = _support.validate_character_sheet(owner_sheet)
        inventory_item = next(
            (
                candidate
                for candidate in owner_sheet.get("inventory", {}).get("items", [])
                if str(candidate.get("id") or "") == item_id
            ),
            None,
        )
        if inventory_item is None:
            raise _support.CombatEngineError("Healer's Kit disappeared from the user's inventory")
        inventory_use_state = dict(inventory_item.get("uses") or {})
        if (
            inventory_use_state.get("value") != remaining
            or inventory_use_state.get("max") != 10
            or inventory_use_state.get("source_key") != item.get("source_key")
        ):
            raise _support.CombatEngineError("Healer's Kit use state changed during settlement")
        inventory_use_state["value"] = remaining - 1
        inventory_item["uses"] = inventory_use_state

        spends = list(state.get("item_spends") or [])
        if any(
            str(entry.get("id") or "") == action_id for entry in spends if isinstance(entry, dict)
        ):
            raise ValueError("gear action_id already exists on this branch")
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": "Healer's Kit stabilize",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item.get("source_key"),
            "character_id": actor_id,
            "owner": {"kind": "character", "character_id": actor_id},
            "gear_intent": plan["intent"],
            "target_character_id": target_actor_id,
            "uses_key": use_key,
            "uses_spent": 1,
            "uses_remaining": remaining - 1,
            "combat_action": "use_object",
        }
        spends.append(receipt)
        state["item_spends"] = spends
        state["combat"] = next_encounter
        normalized_state = _support.validate_party_state(state)
        response = self.commit_campaign_state(
            campaign,
            normalized_state,
            operation="campaign.adventuring_gear.action",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields={
                "status": "committed",
                "action_id": action_id,
                "item_id": item_id,
                "intent": plan["intent"],
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "rule_plan": plan,
                "resource": {"key": use_key, "spent": 1, "remaining": remaining - 1},
                "receipt": receipt,
                "stabilization": result,
                "target": {"character_id": target_actor_id, "stabilization": result},
                "owner": {"kind": "character", "character_id": actor_id},
                "combat": next_encounter,
                "character": self.character_view(
                    _support.replace(owner, sheet=owner_sheet, revision=owner.revision + 1)
                ),
                "target_character": self.character_view(
                    _support.replace(
                        target,
                        sheet=updated_target_sheet,
                        revision=target.revision + 1,
                    )
                ),
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    owner.id,
                    owner_sheet,
                    _support.validate_character_notes(
                        owner.notes, character_type=owner.character_type
                    ),
                    expected_actor_revision,
                ),
                _support.CharacterStateUpdate(
                    target.id,
                    updated_target_sheet,
                    _support.validate_character_notes(
                        target.notes, character_type=target.character_type
                    ),
                    expected_target_revision,
                ),
            ],
            expected_campaign_revision=expected_campaign_revision,
        )
        return response

    def settle_adventuring_gear_manacles(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        target: Any,
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        expected_campaign_revision: int,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Settle exact Manacles binding, key, and source-fixed escape outcomes."""
        if self.campaign_rules_edition(campaign.id) != "2014":
            raise _support.CombatEngineError("source-bound Manacles require the 2014 ruleset")
        if owner.campaign_id != campaign.id or target.campaign_id != campaign.id:
            raise _support.CombatEngineError(
                "Manacles users and targets must belong to the campaign"
            )
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"Manacles user character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if target.revision != expected_target_revision:
            raise ValueError(
                "Manacles target character revision conflict: "
                f"expected {expected_target_revision}, "
                f"found {target.revision}"
            )
        if campaign.revision != expected_campaign_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_campaign_revision}, "
                f"found {campaign.revision}"
            )
        if actor_id == target_actor_id and expected_actor_revision != expected_target_revision:
            raise ValueError("same-actor Manacles action requires matching character revisions")

        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        active_combat = encounter.get("active") is True
        if active_combat:
            self.require_encounter_combatant(encounter, actor_id, role="Manacles user")
            self.require_encounter_combatant(encounter, target_actor_id, role="Manacles target")
        target_sheet = (
            owner_sheet
            if target_actor_id == actor_id
            else _support.validate_character_sheet(target.sheet)
        )
        size = str(dict(target_sheet.get("traits") or {}).get("size") or "").casefold()
        if size not in {"small", "medium"}:
            raise _support.CombatEngineError(
                "source-defined Manacles can bind only Small or Medium creatures"
            )
        if not isinstance(action_id, str) or not action_id or len(action_id) > 200:
            raise ValueError("Manacles action_id must contain 1 to 200 characters")
        if item.get("name") != "Manacles":
            raise _support.CombatEngineError("Manacles settlement requires the exact source item")

        existing_spends = list(dict(campaign.state or {}).get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id
            for entry in existing_spends
        ):
            raise ValueError("gear action_id already exists on this branch")

        bindings = _support.deepcopy(dict(state.get("adventuring_gear_bindings") or {}))
        binding_key = f"{branch_id}:{actor_id}:{item_id}:{target_actor_id}"
        prior = dict(bindings.get(binding_key) or {})
        if prior and (
            prior.get("source_ref") != ADVENTURING_GEAR_SOURCE_REF
            or prior.get("source_key") != item.get("source_key")
            or prior.get("item_name") != "Manacles"
            or prior.get("owner_actor_id") != actor_id
            or prior.get("item_id") != item_id
            or prior.get("target_actor_id") != target_actor_id
            or prior.get("branch_id") != branch_id
        ):
            raise _support.CombatEngineError(
                "Manacles binding state has a mismatched source identity"
            )

        intent = str(plan.get("intent") or "")
        if active_combat and intent != "bind":
            raise _support.CombatEngineError(
                "Manacles escape, break, unlock, and pick are unavailable during active combat "
                "until their action costs are explicitly adjudicated"
            )
        if active_combat and intent == "bind":
            if "incapacitated" not in _support.condition_ids(target_sheet.get("conditions")):
                raise _support.CombatEngineError(
                    "active-combat Manacles binding requires an authoritative incapacitated "
                    "condition on the target"
                )
            encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="use_object",
                payload={"item_id": item_id, "intent": "manacles_bind"},
                payment="main_action",
            )
        success = True
        check: dict[str, Any] | None = None
        next_binding: dict[str, Any]
        target_sheet_after = _support.deepcopy(target_sheet)
        if intent == "bind":
            active_binding_for_item = next(
                (
                    entry
                    for entry in bindings.values()
                    if isinstance(entry, dict)
                    and entry.get("branch_id") == branch_id
                    and entry.get("owner_actor_id") == actor_id
                    and entry.get("item_id") == item_id
                    and entry.get("status") == "bound"
                ),
                None,
            )
            if active_binding_for_item is not None:
                raise _support.CombatEngineError("this Manacles set already has an active binding")
            if prior.get("status") == "broken":
                raise _support.CombatEngineError("broken Manacles cannot bind another target")
            required_fields = {
                "binding_possible",
                "reason",
                "key_available",
                "key_reason",
            }
            if not isinstance(action_context, dict) or set(action_context) != required_fields:
                raise _support.CombatEngineError(
                    "Manacles bind requires a bounded review of binding possibility and key state"
                )
            if action_context.get("binding_possible") is not True:
                raise _support.CombatEngineError(
                    "the reviewed facts do not permit binding this target"
                )
            if type(action_context.get("key_available")) is not bool:
                raise _support.CombatEngineError("Manacles key_available review must be boolean")
            review_reason = str(action_context.get("reason") or "").strip()
            key_reason = str(action_context.get("key_reason") or "").strip()
            if not review_reason or len(review_reason) > 600:
                raise _support.CombatEngineError(
                    "Manacles binding review requires a bounded reason"
                )
            if not key_reason or len(key_reason) > 600:
                raise _support.CombatEngineError("Manacles key review requires a bounded reason")
            next_binding = {
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": str(item.get("source_key") or ""),
                "item_name": "Manacles",
                "owner_actor_id": actor_id,
                "item_id": item_id,
                "target_actor_id": target_actor_id,
                "branch_id": branch_id,
                "binding_action_id": action_id,
                "status": "bound",
                "size": size,
                "key_holder_actor_id": actor_id,
                "key_available": action_context["key_available"],
                "binding_review": {
                    "reviewed_by": principal_id,
                    "reviewed_actor_id": actor_id,
                    "reason": review_reason,
                },
                "key_review": {
                    "reviewed_by": principal_id,
                    "reviewed_actor_id": actor_id,
                    "available": action_context["key_available"],
                    "reason": key_reason,
                },
            }
        else:
            if prior.get("status") != "bound":
                raise _support.CombatEngineError(
                    "Manacles escape, break, unlock, and pick require an "
                    "active source-owned binding"
                )
            if prior.get("key_holder_actor_id") != actor_id:
                raise _support.CombatEngineError(
                    "only the actor bound to this Manacles set may use its recorded key"
                )
            if intent == "unlock":
                if action_context is not None:
                    raise _support.CombatEngineError(
                        "Manacles unlock does not accept caller-supplied key or outcome data"
                    )
                if prior.get("key_available") is not True:
                    raise _support.CombatEngineError(
                        "the source-provided Manacles key is not authoritatively available"
                    )
                success = True
            elif intent in {"escape", "break", "pick"}:
                if intent == "pick":
                    proficiencies = dict(
                        dict(owner_sheet.get("traits") or {}).get("proficiencies") or {}
                    )
                    tools = {
                        " ".join(str(value).casefold().replace("’", "'").split())
                        for value in proficiencies.get("tools", [])
                    }
                    if "thieves' tools" not in tools:
                        raise _support.CombatEngineError(
                            "source-defined Manacles picking requires authoritative "
                            "thieves' tools proficiency"
                        )
                    if prior.get("key_available") is True:
                        if (
                            not isinstance(action_context, dict)
                            or set(action_context) != {"key_unavailable", "reason"}
                            or action_context.get("key_unavailable") is not True
                            or not str(action_context.get("reason") or "").strip()
                        ):
                            raise _support.CombatEngineError(
                                "Manacles picking requires a bounded review that "
                                "the held key is unavailable"
                            )
                        reason = str(action_context["reason"]).strip()
                        if len(reason) > 600:
                            raise _support.CombatEngineError(
                                "Manacles key review reason is too long"
                            )
                        prior["key_available"] = False
                        prior["key_review"] = {
                            "reviewed_by": principal_id,
                            "reviewed_actor_id": actor_id,
                            "available": False,
                            "reason": reason,
                        }
                    elif action_context is not None:
                        raise _support.CombatEngineError(
                            "Manacles key unavailability is already recorded on this binding"
                        )
                    if prior.get("key_available") is not False:
                        raise _support.CombatEngineError(
                            "Manacles picking requires no currently available key"
                        )
                elif action_context is not None:
                    raise _support.CombatEngineError(
                        "Manacles escape and break do not accept caller-supplied check outcomes"
                    )

                check_spec = dict(plan.get("check") or {})
                check_actor_id = actor_id if intent == "pick" else target_actor_id
                stream = _support.active_random_stream()
                if stream is None:
                    with _support.use_random_stream(
                        _support.CampaignRandomStream.from_campaign_state(
                            campaign.id,
                            campaign.state,
                            operation="campaign.adventuring_gear.manacles",
                            idempotency_key=idempotency_key,
                            campaign_revision=campaign.revision,
                        )
                    ):
                        return self.settle_adventuring_gear_manacles(
                            campaign=campaign,
                            state=state,
                            owner=owner,
                            owner_sheet=owner_sheet,
                            target=target,
                            item=item,
                            plan=plan,
                            action_id=action_id,
                            item_id=item_id,
                            actor_id=actor_id,
                            target_actor_id=target_actor_id,
                            expected_actor_revision=expected_actor_revision,
                            expected_target_revision=expected_target_revision,
                            expected_campaign_revision=expected_campaign_revision,
                            branch_id=branch_id,
                            principal_id=principal_id,
                            idempotency_key=idempotency_key,
                            scope=scope,
                            request_payload=request_payload,
                            action_context=action_context,
                        )
                random_state = _support.validate_random_stream_state(
                    dict(campaign.state or {}).get("random_stream")
                    or _support.initial_random_stream(f"sagasmith-dnd:{campaign.id}")
                )
                if (
                    stream.campaign_id != campaign.id
                    or stream.seed != random_state["seed"]
                    or stream.start_position != random_state["position"]
                    or (
                        stream.campaign_revision is not None
                        and stream.campaign_revision != campaign.revision
                    )
                ):
                    raise _support.CombatEngineError(
                        "Manacles checks require the current campaign random snapshot"
                    )
                check = _support.resolve_actor_check(
                    self.combat_actor_snapshot(check_actor_id),
                    kind="check",
                    ability=str(check_spec.get("ability") or ""),
                    dc=int(check_spec["dc"]),
                    proficient=(intent == "pick"),
                    ruleset="2014",
                    rng=stream,
                )
                success = check.get("success") is True
            else:
                raise _support.CombatEngineError("unsupported source-defined Manacles intent")

            next_binding = _support.deepcopy(prior)
            if success:
                next_binding["status"] = "broken" if intent == "break" else "released"
                next_binding["last_action_id"] = action_id
                next_binding["last_intent"] = intent
                if intent == "break":
                    next_binding["object_hit_points"] = 0
            elif intent == "break":
                next_binding["object_hit_points"] = int(plan.get("object_hit_points") or 0)

        bindings[binding_key] = next_binding
        next_state = _support.deepcopy(state)
        next_state["adventuring_gear_bindings"] = bindings
        if active_combat:
            next_state["combat"] = encounter

        check_success = True if check is None else success
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": f"adventuring gear manacles:{intent}",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": str(item.get("source_key") or ""),
            "character_id": owner.id,
            "target_character_id": target_actor_id,
            "gear_intent": intent,
            "rule_plan": _support.deepcopy(plan),
            "check": _support.deepcopy(check),
            "success": check_success,
            "key_holder_actor_id": next_binding.get("key_holder_actor_id"),
            "key_available": next_binding.get("key_available"),
            "resulting_state": _support.deepcopy(next_binding),
        }
        if active_combat:
            receipt.update({"action_cost": "action", "action_paid": True})
        spends = [*existing_spends, receipt]
        next_state["item_spends"] = spends
        resolution = {
            "type": "adventuring_gear_manacles",
            "action_id": action_id,
            "actor_id": actor_id,
            "target_actor_id": target_actor_id,
            "item_id": item_id,
            "intent": intent,
            "success": check_success,
            "check": _support.deepcopy(check),
            "binding": _support.deepcopy(next_binding),
        }
        if active_combat:
            resolution.update({"action_cost": "action", "action_paid": True})
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            resolution,
        ][-100:]
        normalized_state = _support.validate_party_state(next_state)

        updates_by_id: dict[str, Any] = {}
        updates_by_id[owner.id] = _support.CharacterStateUpdate(
            character_id=owner.id,
            sheet=_support.validate_character_sheet(
                target_sheet_after if target_actor_id == owner.id else owner_sheet
            ),
            notes=_support.validate_character_notes(
                target.notes if target_actor_id == owner.id else owner.notes,
                character_type=owner.character_type,
            ),
            expected_revision=owner.revision,
        )
        if target_actor_id != owner.id:
            updates_by_id[target_actor_id] = _support.CharacterStateUpdate(
                character_id=target_actor_id,
                sheet=_support.validate_character_sheet(target_sheet_after),
                notes=_support.validate_character_notes(
                    target.notes,
                    character_type=target.character_type,
                ),
                expected_revision=target.revision,
            )
        updated_owner = _support.replace(
            owner,
            sheet=updates_by_id[owner.id].sheet,
            revision=owner.revision + 1,
        )
        updated_target = (
            updated_owner
            if target_actor_id == owner.id
            else _support.replace(
                target,
                sheet=updates_by_id[target_actor_id].sheet,
                revision=target.revision + 1,
            )
        )
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": intent,
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "success": check_success,
            "binding": _support.deepcopy(next_binding),
            "check": _support.deepcopy(check),
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_state,
                    revision=campaign.revision + 1,
                )
            ),
            "owner": self.character_view(updated_owner),
            **(
                {"target": self.character_view(updated_target)}
                if target_actor_id != owner.id
                else {}
            ),
        }
        if active_combat:
            response.update({"action_cost": "action", "action_paid": True})
        response["campaign"]["state"] = normalized_state
        stream = _support.active_random_stream()
        if stream is not None and stream.draw_count > 0:
            response["random_stream_receipt"] = stream.receipt()
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=list(updates_by_id.values()),
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.manacles",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def settle_magnifying_glass_inspection(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        target_object: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve source-granted advantage against one signed Small item."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for Magnifying Glass inspection")
        if owner.id != actor_id or owner.campaign_id != campaign.id:
            raise _support.CombatEngineError("inspection requires its owning campaign actor")
        if owner.revision != expected_actor_revision:
            raise ValueError("character revision conflict for Magnifying Glass inspection")
        if self.campaign_rules_edition(campaign.id) != "2014":
            raise _support.CombatEngineError("Magnifying Glass requires the 2014 ruleset")
        if bool(dict(state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError(
                "Magnifying Glass checks are unavailable during active combat until "
                "their action cost is settled"
            )
        if str(item.get("source_key") or "") != "dnd5e.content.srd2014.item.magnifying-glass":
            raise _support.CombatEngineError("Magnifying Glass item source identity is invalid")
        if item.get("quantity", 0) < 1:
            raise _support.CombatEngineError("Magnifying Glass is not available in inventory")
        if (
            not isinstance(action_context, dict)
            or set(action_context) != {"purpose", "ability", "dc"}
            or action_context.get("purpose") not in {"appraise", "inspect"}
        ):
            raise _support.NeedsRulingError(
                "Magnifying Glass requires a DM-defined appraise or inspect check",
                missing=("adventuring_gear.appraise_or_inspect_ability_check",),
                ruling_kind="source_or_scene_fact",
            )
        ability = str(action_context.get("ability") or "").strip().casefold()
        if ability not in {
            "strength",
            "dexterity",
            "constitution",
            "intelligence",
            "wisdom",
            "charisma",
        }:
            raise _support.CombatEngineError("DM-reviewed check ability must be a core ability")
        dc = action_context.get("dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 40:
            raise _support.CombatEngineError("DM-reviewed check DC must be an integer from 1 to 40")
        if not isinstance(target_object, dict) or set(target_object) != {"id", "scene_id"}:
            raise _support.CombatEngineError("target_object accepts only exact id and scene_id")
        scene_id = str(target_object.get("scene_id") or "").strip()
        object_id = str(target_object.get("id") or "").strip()
        if not scene_id or not object_id:
            raise _support.CombatEngineError("target_object requires id and scene_id")
        objects = dict(state.get("scene_objects") or {})
        obj = dict(dict(objects.get(scene_id) or {}).get(object_id) or {})
        if not obj:
            raise _support.CombatEngineError("inspection target has no DM-reviewed scene profile")
        _, source_ref, expanded = self.managed_module_source_ref(
            campaign.id,
            obj.get("source_ref"),
            require_exact=True,
            expected_scene_id=scene_id,
            require_active_module=True,
        )
        if source_ref is None or expanded is None:
            raise _support.CombatEngineError(
                "inspection target must belong to an exact active scene"
            )
        profile = validate_object_profile(obj.get("profile"))
        approval = _support.verify_receipt_signature(
            obj.get("profile_approval"),
            self.content_authority_secret,
            missing_error="inspection target profile approval is missing",
            invalid_error="inspection target profile approval is invalid",
        )
        if (
            profile["id"] != object_id
            or profile["scene_id"] != scene_id
            or profile["size"] not in {"tiny", "small"}
            or approval.get("purpose") != "source_object_profile"
            or approval.get("campaign_id") != campaign.id
            or approval.get("profile_digest") != _support.json_sha256(profile)
            or approval.get("source_ref") != source_ref
            or obj.get("source_ref") != source_ref
        ):
            raise _support.NeedsRulingError(
                "Advantage needs a signed Tiny or Small item; highly detailed status is "
                "not represented",
                missing=("scene_object.profile.size_or_reviewed_detail",),
                ruling_kind="source_or_scene_fact",
            )
        if any(
            isinstance(row, dict) and row.get("id") == action_id
            for row in state.get("item_spends", [])
        ):
            raise ValueError("gear action_id already exists on this branch")
        stream = _support.active_random_stream()
        if stream is None:
            with _support.use_random_stream(
                _support.CampaignRandomStream.from_campaign_state(
                    campaign.id,
                    campaign.state,
                    operation="campaign.adventuring_gear.magnifying_glass_inspection",
                    idempotency_key=idempotency_key,
                    campaign_revision=campaign.revision,
                )
            ):
                return self.settle_magnifying_glass_inspection(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    item=item,
                    plan=plan,
                    target_object=target_object,
                    action_id=action_id,
                    item_id=item_id,
                    actor_id=actor_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_campaign_revision=expected_campaign_revision,
                    branch_id=branch_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    request_payload=request_payload,
                    action_context=action_context,
                )
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign.id}")
        )
        if (
            stream.campaign_id != campaign.id
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
        ):
            raise _support.CombatEngineError(
                "inspection check requires the current random snapshot"
            )
        rules = self.effective_rule_context(
            campaign.id,
            branch_id=branch_id,
            facts={
                "action": "magnifying_glass_inspect",
                "actor_id": actor_id,
                "target_id": object_id,
            },
        )
        check = _support.resolve_actor_check(
            self.combat_actor_snapshot(actor_id),
            kind="check",
            ability=ability,
            action=f"magnifying_glass_{action_context['purpose']}",
            dc=dc,
            bonus=0,
            advantage=True,
            rules=rules,
            ruleset="2014",
            rng=stream,
        )
        check["source_item_modifier"] = {
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item["source_key"],
            "advantage": True,
            "reviewed_object_profile_digest": _support.json_sha256(profile),
        }
        success = check.get("success") is True
        next_state = _support.deepcopy(state)
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": f"Magnifying Glass {action_context['purpose']} check",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item["source_key"],
            "owner_actor_id": actor_id,
            "owner_revision_before": owner.revision,
            "owner_revision_after": owner.revision + 1,
            "target_scene_id": scene_id,
            "target_object_id": object_id,
            "target_source_ref": source_ref,
            "profile_approval_digest": _support.json_sha256(obj["profile_approval"]),
            "check": _support.deepcopy(check),
            "success": success,
            "rule_plan": _support.deepcopy(plan),
        }
        next_state["item_spends"] = [*list(next_state.get("item_spends") or []), receipt]
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            {
                "id": f"resolution-{_support.uuid4().hex}",
                "type": "adventuring_gear_magnifying_glass_inspection",
                "operation": "campaign.adventuring_gear.magnifying_glass_inspection",
                "campaign_revision": campaign.revision + 1,
                "branch_id": branch_id,
                "actor_id": actor_id,
                "scene_id": scene_id,
                "object_id": object_id,
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "success": success,
                "result": receipt,
            },
        ][-100:]
        normalized_state = _support.validate_party_state(next_state)
        updated_owner = _support.replace(owner, sheet=owner_sheet, revision=owner.revision + 1)
        response = {
            "status": "committed",
            "action_id": action_id,
            "intent": "inspect",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "check": _support.deepcopy(check),
            "success": success,
            "target_object": {
                "id": object_id,
                "scene_id": scene_id,
                "profile_approval": _support.deepcopy(obj["profile_approval"]),
            },
            "owner": self.character_view(updated_owner),
            "campaign": _support.asdict(
                _support.replace(campaign, state=normalized_state, revision=campaign.revision + 1)
            ),
            "receipt": receipt,
        }
        response["campaign"]["state"] = normalized_state
        if stream.draw_count > 0:
            response["random_stream_receipt"] = stream.receipt()
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=owner.id,
                    sheet=owner_sheet,
                    notes=_support.validate_character_notes(
                        owner.notes, character_type=owner.character_type
                    ),
                    expected_revision=owner.revision,
                )
            ],
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.magnifying_glass_inspection",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope, payload=request_payload, response=response
            ),
        )
        return response

    def settle_adventuring_gear_object_check(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Settle source-fixed object checks and persist object state atomically."""
        if actor_id != target_actor_id or expected_actor_revision != expected_target_revision:
            raise _support.CombatEngineError(
                "gear object checks require the owning actor as both actor and target"
            )
        if self.authoritative_phase(campaign.id) != _support.PROFILE_PLAY:
            raise _support.CombatEngineError(
                "gear object checks are available only outside active combat"
            )
        if self.campaign_rules_edition(campaign.id) != "2014":
            raise _support.CombatEngineError(
                "source-bound adventuring gear object checks require the 2014 ruleset"
            )
        name = str(item.get("name") or "")
        item_key = str(item.get("source_key") or "")
        state_key = (
            "lock"
            if name.casefold() == "lock"
            else ("chain" if name.casefold() == "chain (10 feet)" else "rope")
        )
        intent = str(plan.get("intent") or "")
        if action_context is not None and not (state_key == "lock" and intent == "pick"):
            raise _support.CombatEngineError(
                "adventuring gear object checks do not accept caller rule or outcome context"
            )
        object_states = dict(state.get("adventuring_gear_objects") or {})
        object_state_key = f"{actor_id}:{item_id}"
        gear_state = dict(object_states.get(object_state_key) or {})
        if gear_state and (
            gear_state.get("source_ref") != ADVENTURING_GEAR_SOURCE_REF
            or gear_state.get("source_key") != item_key
            or gear_state.get("item_name") != name
            or gear_state.get("owner_actor_id") != actor_id
            or gear_state.get("item_id") != item_id
        ):
            raise _support.CombatEngineError("gear object state has a mismatched source identity")
        key_holder_actor_id = actor_id
        key_available = True
        key_review = None
        if state_key == "lock":
            if gear_state and (
                gear_state.get("key_holder_actor_id") != actor_id
                or type(gear_state.get("key_available")) is not bool
                or gear_state.get("key_source") != "provided_with_lock"
            ):
                raise _support.CombatEngineError(
                    "Lock key state has a mismatched source or holder identity"
                )
            if gear_state:
                key_holder_actor_id = str(gear_state["key_holder_actor_id"])
                key_available = gear_state["key_available"]
                key_review = _support.deepcopy(gear_state.get("key_review"))
        default_state = "locked" if state_key == "lock" else "intact"
        current_state = str(gear_state.get("state") or default_state)
        required_state = str(dict(plan.get("effect") or {}).get("requires_state") or "")
        if current_state != required_state:
            raise _support.CombatEngineError(
                f"{state_key} must be {required_state} to use this source-defined action"
            )
        if state_key == "lock":
            if key_holder_actor_id != actor_id:
                raise _support.CombatEngineError(
                    "only the actor holding the source-provided Lock key may unlock it"
                )
            if intent == "unlock":
                if key_available is not True:
                    raise _support.CombatEngineError(
                        "the source-provided Lock key is not authoritatively available"
                    )
            elif intent == "pick":
                if key_available is True:
                    if (
                        not isinstance(action_context, dict)
                        or set(action_context) != {"key_unavailable", "reason"}
                        or action_context.get("key_unavailable") is not True
                    ):
                        raise _support.CombatEngineError(
                            "Lock picking requires a bounded review that the "
                            "provided key is unavailable"
                        )
                    reason = str(action_context.get("reason") or "").strip()
                    if not reason or len(reason) > 600:
                        raise _support.CombatEngineError(
                            "Lock key review requires a bounded reason"
                        )
                    key_available = False
                    key_review = {
                        "reviewed_by": principal_id,
                        "reviewed_actor_id": actor_id,
                        "available": False,
                        "reason": reason,
                    }
                elif action_context is not None:
                    raise _support.CombatEngineError(
                        "Lock key unavailability is already recorded on this item"
                    )
                if key_available is not False:
                    raise _support.CombatEngineError(
                        "Lock picking requires no currently available source-provided key"
                    )
            else:
                raise _support.CombatEngineError("unsupported source-defined Lock intent")
        requirements = set(plan.get("requirements") or [])
        if "thieves_tools_proficiency" in requirements:
            proficiencies = dict(dict(owner_sheet.get("traits") or {}).get("proficiencies") or {})
            tools = {
                " ".join(str(value).casefold().replace("’", "'").split())
                for value in proficiencies.get("tools", [])
            }
            if "thieves' tools" not in tools:
                raise _support.CombatEngineError(
                    "source-defined Lock picking requires authoritative thieves' tools proficiency"
                )

        campaign_state = _support.deepcopy(state)
        stream_context = _support.active_random_stream()
        lock_key_unlock = state_key == "lock" and intent == "unlock"
        if not lock_key_unlock and stream_context is None:
            with _support.use_random_stream(
                _support.CampaignRandomStream.from_campaign_state(
                    campaign.id,
                    campaign.state,
                    operation="campaign.adventuring_gear.object_check",
                    idempotency_key=idempotency_key,
                    campaign_revision=campaign.revision,
                )
            ):
                return self.settle_adventuring_gear_object_check(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    item=item,
                    plan=plan,
                    action_id=action_id,
                    item_id=item_id,
                    actor_id=actor_id,
                    target_actor_id=target_actor_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_target_revision=expected_target_revision,
                    expected_campaign_revision=expected_campaign_revision,
                    branch_id=branch_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    request_payload=request_payload,
                    action_context=action_context,
                )
        check = None
        success = True
        stream = _support.active_random_stream()
        if not lock_key_unlock:
            random_state = _support.validate_random_stream_state(
                dict(campaign.state or {}).get("random_stream")
                or _support.initial_random_stream(f"sagasmith-dnd:{campaign.id}")
            )
            if (
                stream.campaign_id != campaign.id
                or stream.seed != random_state["seed"]
                or stream.start_position != random_state["position"]
                or (
                    stream.campaign_revision is not None
                    and stream.campaign_revision != campaign.revision
                )
            ):
                raise _support.CombatEngineError(
                    "gear object checks require the current campaign random snapshot"
                )
            check_spec = dict(plan.get("check") or {})
            check = _support.resolve_actor_check(
                self.combat_actor_snapshot(actor_id),
                kind="check",
                ability=str(check_spec.get("ability") or ""),
                dc=int(check_spec["dc"]),
                proficient=(state_key == "lock" and intent == "pick"),
                ruleset=self.campaign_rules_edition(campaign.id),
                rng=stream,
            )
            success = check.get("success") is True
        if state_key == "lock":
            gear_state = {
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": item_key,
                "item_name": name,
                "owner_actor_id": actor_id,
                "item_id": item_id,
                "state": (
                    dict(plan.get("effect") or {}).get("success_state")
                    if success
                    else dict(plan.get("effect") or {}).get("failure_state")
                ),
                "key_source": "provided_with_lock",
                "key_holder_actor_id": key_holder_actor_id,
                "key_available": key_available,
            }
            if key_review is not None:
                gear_state["key_review"] = key_review
        elif success:
            gear_state = {
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": item_key,
                "item_name": name,
                "owner_actor_id": actor_id,
                "item_id": item_id,
                "state": dict(plan.get("effect") or {}).get("success_state"),
            }
            if state_key in {"chain", "rope"}:
                gear_state["object_hit_points"] = 0
        else:
            gear_state = {
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": item_key,
                "item_name": name,
                "owner_actor_id": actor_id,
                "item_id": item_id,
                "state": current_state,
            }
            if state_key in {"chain", "rope"}:
                gear_state["object_hit_points"] = int(plan.get("object_hit_points") or 0)
        object_states[object_state_key] = gear_state
        campaign_state["adventuring_gear_objects"] = object_states

        spends = list(campaign_state.get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id for entry in spends
        ):
            raise ValueError("gear action_id already exists on this branch")
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": f"adventuring gear {plan['intent']}",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": item_key,
            "character_id": owner.id,
            "gear_intent": plan["intent"],
            "check": check,
            "resulting_state": gear_state,
        }
        if state_key == "lock":
            receipt["key_holder_actor_id"] = key_holder_actor_id
            receipt["key_available"] = key_available
        spends.append(receipt)
        campaign_state["item_spends"] = spends
        owner_sheet = _support.validate_character_sheet(owner_sheet)
        actor_update = _support.CharacterStateUpdate(
            owner.id,
            owner_sheet,
            _support.validate_character_notes(owner.notes, character_type=owner.character_type),
            expected_actor_revision,
        )
        normalized_state = _support.validate_party_state(campaign_state)
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": plan["intent"],
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": plan,
            "check": check,
            "success": success,
            "resulting_state": receipt["resulting_state"],
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_state,
                    revision=campaign.revision + 1,
                )
            ),
            "character": self.character_view(
                _support.replace(
                    owner,
                    sheet=owner_sheet,
                    revision=owner.revision + 1,
                )
            ),
        }
        response["campaign"]["state"] = normalized_state
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=[actor_update],
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.object_check",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def settle_scene_object_gear_strength_check(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        target_object: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        helper_actor_id: str | None,
    ) -> dict[str, Any]:
        """Resolve the Crowbar/Ram Strength check against one signed scene object."""
        from sagasmith_dnd.objects import validate_object_profile

        from .source_objects import approved_gear_strength_check

        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for object Strength check")
        if owner.id != actor_id or owner.campaign_id != campaign.id:
            raise _support.CombatEngineError(
                "gear Strength check requires its owning campaign actor"
            )
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if self.campaign_rules_edition(campaign.id) != "2014":
            raise _support.CombatEngineError("object gear Strength checks require the 2014 rules")
        if type(helper_actor_id) not in (str, type(None)):
            raise ValueError("helper_actor_id must be text when supplied")
        helper_id = str(helper_actor_id or "").strip() or None
        intent = str(plan.get("intent") or "")
        source_key = str(item.get("source_key") or "")
        item_name = str(item.get("name") or "").strip().casefold()
        is_crowbar = item_name == "crowbar" and intent == "apply_leverage"
        is_ram = item_name == "ram, portable" and intent == "break_door"
        if not (is_crowbar or is_ram):
            raise _support.CombatEngineError(
                "only Crowbar and Portable Ram checks target scene objects"
            )
        if helper_id is not None and not is_ram:
            raise _support.CombatEngineError("only Portable Ram checks accept a helper")
        if item.get("quantity", 0) < 1:
            raise _support.CombatEngineError("the source-bound gear item is not available")
        scene_id = str(target_object.get("scene_id") or "").strip()
        object_id = str(target_object.get("id") or "").strip()
        if not scene_id or not object_id:
            raise ValueError("target_object requires its exact reviewed id and scene_id")
        scene_objects = _support.deepcopy(dict(state.get("scene_objects") or {}))
        scene_state = _support.deepcopy(dict(scene_objects.get(scene_id) or {}))
        object_state = _support.deepcopy(dict(scene_state.get(object_id) or {}))
        if not object_state:
            raise _support.CombatEngineError("target scene object has no DM-reviewed profile")
        profile = validate_object_profile(object_state.get("profile"))
        if (
            profile["id"] != object_id
            or profile["scene_id"] != scene_id
            or not isinstance(object_state.get("profile_approval"), dict)
        ):
            raise _support.CombatEngineError("target scene object identity or approval is invalid")
        check_review = approved_gear_strength_check(
            self,
            campaign_id=campaign.id,
            branch_id=branch_id,
            profile=profile,
            profile_approval=object_state["profile_approval"],
            review=object_state.get("gear_strength_check"),
            approval=object_state.get("gear_strength_check_approval"),
        )
        facts = dict(check_review["facts"])
        if is_ram and facts["door"] is not True:
            raise _support.CombatEngineError("Portable Ram requires a DM-reviewed door target")
        if object_state.get("destroyed") or int(object_state.get("hit_points", 0) or 0) <= 0:
            raise _support.CombatEngineError("target scene object is already destroyed")
        current_state = dict(object_state.get("gear_strength_check_state") or {})
        if current_state.get("state") in {"open", "breached"}:
            raise _support.CombatEngineError("target scene object is already open or breached")
        if helper_id == actor_id:
            raise _support.CombatEngineError("Portable Ram requires a different helper actor")

        helper = None
        helper_sheet = None
        if helper_id is not None:
            helper = self.require_campaign_actor(campaign.id, helper_id)
            if self.narrative_only_actor(helper):
                raise _support.CombatEngineError("Portable Ram helper requires an exact actor card")
            helper_sheet = _support.validate_character_sheet(helper.sheet)
            incapacitating = set(_support.condition_ids(helper_sheet.get("conditions"))) & set(
                _support.INCAPACITATING_STATE_IDS
            )
            if (
                int(helper_sheet.get("combat", {}).get("hp", {}).get("value", 0)) <= 0
                or incapacitating
            ):
                raise _support.CombatEngineError(
                    "an incapacitated actor cannot help use a Portable Ram"
                )

        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        active_combat = bool(encounter.get("active"))
        if active_combat:
            self.require_no_blocking_pending(encounter)
            encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="improvise",
                payload={"gear_check": intent, "object_id": object_id, "scene_id": scene_id},
            )
            if helper_id is not None and not any(
                str(value.get("actor_id") or "") == helper_id
                for value in encounter.get("combatants", [])
            ):
                raise _support.CombatEngineError(
                    "Portable Ram helper must be in the active encounter"
                )
        elif self.authoritative_phase(campaign.id) == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("active combat state is unavailable for object check")

        stream = _support.active_random_stream()
        if stream is None:
            with _support.use_random_stream(
                _support.CampaignRandomStream.from_campaign_state(
                    campaign.id,
                    campaign.state,
                    operation="campaign.adventuring_gear.scene_object_strength_check",
                    idempotency_key=idempotency_key,
                    campaign_revision=campaign.revision,
                )
            ):
                return self.settle_scene_object_gear_strength_check(
                    campaign=campaign,
                    state=state,
                    owner=owner,
                    owner_sheet=owner_sheet,
                    item=item,
                    plan=plan,
                    target_object=target_object,
                    action_id=action_id,
                    item_id=item_id,
                    actor_id=actor_id,
                    expected_actor_revision=expected_actor_revision,
                    expected_campaign_revision=expected_campaign_revision,
                    branch_id=branch_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    request_payload=request_payload,
                    helper_actor_id=helper_id,
                )
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign.id}")
        )
        if (
            stream.campaign_id != campaign.id
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
        ):
            raise _support.CombatEngineError(
                "object Strength check requires the current random snapshot"
            )
        check_plan = dict(plan.get("check") or {})
        bonus = int(check_plan.get("bonus", 0) or 0)
        advantage = is_crowbar and facts["crowbar_leverage"]
        if active_combat:
            rules = self.effective_rule_context(
                campaign.id,
                branch_id=branch_id,
                facts={"action": intent, "actor_id": actor_id, "target_id": object_id},
            )
            check = _support.resolve_actor_check(
                self.combat_actor_snapshot(actor_id),
                kind="check",
                ability="strength",
                action=intent,
                dc=int(facts["strength_dc"]),
                bonus=bonus,
                advantage=advantage,
                encounter=encounter,
                rules=rules,
                ruleset="2014",
                rng=stream,
            )
            helped_by = str(check.get("helped_by") or "")
            if helper_id is not None and helped_by != helper_id:
                raise _support.CombatEngineError(
                    "the named Portable Ram helper has no matching paid task Help action"
                )
            if helped_by:
                encounter = _support.consume_task_help(
                    encounter, actor_id_value=actor_id, helper_id=helped_by
                )
        else:
            rules = self.effective_rule_context(
                campaign.id,
                branch_id=branch_id,
                facts={"action": intent, "actor_id": actor_id, "target_id": object_id},
            )
            check = _support.resolve_actor_check(
                self.combat_actor_snapshot(actor_id),
                kind="check",
                ability="strength",
                action=intent,
                dc=int(facts["strength_dc"]),
                bonus=bonus,
                advantage=bool(advantage or helper_id),
                rules=rules,
                ruleset="2014",
                rng=stream,
            )
            if helper_id:
                check["helped_by"] = helper_id
                check["advantage_source"] = "portable_ram_helper"
                check["advantage_sources"] = ["portable_ram_helper"]
        check["source_item_modifier"] = {
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": source_key,
            "bonus": bonus,
            "crowbar_leverage_applies": facts["crowbar_leverage"] if is_crowbar else None,
            "reviewed_strength_dc": facts["strength_dc"],
        }
        success = check.get("success") is True

        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id
            for entry in list(state.get("item_spends") or [])
        ):
            raise ValueError("gear action_id already exists on this branch")
        campaign_state = _support.deepcopy(state)
        if active_combat:
            campaign_state["combat"] = encounter
        next_object = _support.deepcopy(object_state)
        if success:
            next_object["gear_strength_check_state"] = {
                "state": facts["success_state"],
                "action_id": action_id,
                "actor_id": actor_id,
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "scene_id": scene_id,
                "object_id": object_id,
                "review_approval_digest": _support.json_sha256(check_review["approval"]),
                "campaign_revision": campaign.revision + 1,
            }
            scene_state[object_id] = next_object
            scene_objects[scene_id] = scene_state
            campaign_state["scene_objects"] = scene_objects
        resulting_state = _support.deepcopy(
            next_object.get("gear_strength_check_state")
            or {"state": "intact", "scene_id": scene_id, "object_id": object_id}
        )
        receipt = {
            "id": action_id,
            "item_id": item_id,
            "quantity": 0,
            "reason": f"adventuring gear scene-object Strength check:{intent}",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "source_key": source_key,
            "owner_actor_id": actor_id,
            "owner_revision_before": owner.revision,
            "owner_revision_after": owner.revision + 1,
            "target_scene_id": scene_id,
            "target_object_id": object_id,
            "target_object_revision_before": campaign.revision,
            "target_object_revision_after": campaign.revision + 1,
            "target_source_ref": check_review["source_ref"],
            "review_approval_digest": _support.json_sha256(check_review["approval"]),
            "helper_actor_id": check.get("helped_by"),
            "action_paid": active_combat,
            "rule_plan": _support.deepcopy(plan),
            "check": _support.deepcopy(check),
            "success": success,
            "resulting_state": resulting_state,
        }
        spends = [*list(campaign_state.get("item_spends") or []), receipt]
        campaign_state["item_spends"] = spends
        resolution_id = f"resolution-{_support.uuid4().hex}"
        campaign_state["resolution_log"] = [
            *list(campaign_state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "type": "adventuring_gear_scene_object_strength_check",
                "operation": "campaign.adventuring_gear.scene_object_strength_check",
                "campaign_revision": campaign.revision + 1,
                "branch_id": branch_id,
                "actor_id": actor_id,
                "actor_revision": owner.revision,
                "scene_id": scene_id,
                "object_id": object_id,
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "target_source_ref": check_review["source_ref"],
                "success": success,
                "result": receipt,
            },
        ][-100:]
        character_updates = [
            _support.CharacterStateUpdate(
                character_id=owner.id,
                sheet=owner_sheet,
                notes=_support.validate_character_notes(
                    owner.notes, character_type=owner.character_type
                ),
                expected_revision=owner.revision,
            )
        ]
        updated_owner = _support.replace(owner, sheet=owner_sheet, revision=owner.revision + 1)
        updated_helper = None
        if helper is not None and helper_sheet is not None:
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=helper.id,
                    sheet=helper_sheet,
                    notes=_support.validate_character_notes(
                        helper.notes, character_type=helper.character_type
                    ),
                    expected_revision=helper.revision,
                )
            )
            updated_helper = _support.replace(
                helper, sheet=helper_sheet, revision=helper.revision + 1
            )
        normalized_state = _support.validate_party_state(campaign_state)
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": intent,
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "check": _support.deepcopy(check),
            "success": success,
            "target_object": {
                "id": object_id,
                "scene_id": scene_id,
                "state": resulting_state,
                "profile_approval": _support.deepcopy(object_state["profile_approval"]),
                "strength_check_approval": _support.deepcopy(
                    object_state["gear_strength_check_approval"]
                ),
            },
            "owner": self.character_view(updated_owner),
            **({"helper": self.character_view(updated_helper)} if updated_helper else {}),
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_state,
                    revision=campaign.revision + 1,
                )
            ),
            "receipt": receipt,
        }
        response["campaign"]["state"] = normalized_state
        if stream.draw_count > 0:
            response["random_stream_receipt"] = stream.receipt()
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=character_updates,
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.scene_object_strength_check",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def settle_adventuring_gear_antitoxin(
        self,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        target: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        target_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        expected_revision: int,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if plan.get("intent") != "drink" or plan.get("action_economy") is not None:
            raise _support.CombatEngineError("invalid bundled Antitoxin action plan")
        if actor_id == target_id and expected_actor_revision != expected_target_revision:
            raise ValueError("same-actor gear use requires equal actor and target revisions")
        target_sheet = (
            owner_sheet
            if actor_id == target_id
            else _support.validate_character_sheet(target.sheet)
        )
        creature_type = _authoritative_gear_creature_type(target_sheet)
        if not creature_type:
            raise _support.CombatEngineError(
                "Antitoxin requires an authoritative target creature type"
            )
        if any(
            token.strip(" ,.;:-()") in {"undead", "construct"} for token in creature_type.split()
        ):
            raise _support.CombatEngineError("Antitoxin has no effect on undead or constructs")
        active_antitoxin = any(
            isinstance(effect, dict)
            and effect.get("active") is True
            and dict(effect.get("metadata") or {}).get("save_purpose") == "poison"
            and dict(effect.get("metadata") or {}).get("source_item_id") == item_id
            for effect in target_sheet.get("effects", [])
        )
        if active_antitoxin:
            raise _support.CombatEngineError("target already has this Antitoxin effect active")
        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        active_combat = bool(encounter.get("active"))
        combat_action = None
        action_cost = None
        if active_combat:
            self.encounter_rules_edition(campaign.id, encounter)
            self.require_no_blocking_pending(encounter)
            user_combatant = self.require_encounter_combatant(
                encounter, actor_id, role="Antitoxin user"
            )
            self.require_encounter_combatant(encounter, target_id, role="Antitoxin target")
            use_interaction = (
                int(dict(user_combatant.get("turn_budget") or {}).get("object_interaction", 0) or 0)
                > 0
            )
            combat_action = "interact_object" if use_interaction else "use_object"
            action_cost = "object_interaction" if use_interaction else "action"
            encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action=combat_action,
                payload=(
                    {"object_description": "Antitoxin (vial)", "interaction": "drink"}
                    if use_interaction
                    else {
                        "kind": "adventuring_gear",
                        "intent": "drink",
                        "item_id": item_id,
                        "source_key": item.get("source_key"),
                        "target_actor_id": target_id,
                    }
                ),
                **({"payment": "object_interaction"} if use_interaction else {}),
            )
            state["combat"] = encounter
        owner_after, removed = _support.remove_inventory_item(owner_sheet, item_id, 1)
        effect = {
            "id": f"adventuring-gear-antitoxin:{action_id}",
            "name": "Antitoxin",
            "kind": "source_effect",
            "active": True,
            "concentration": False,
            "duration": {"period": "hour", "remaining": 1},
            "metadata": {
                "save_purpose": "poison",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "action_id": action_id,
                "source_item_id": item_id,
                "source_key": str(item.get("source_key") or ""),
            },
            "changes": [
                {
                    "path": "rolls.saving_throw.advantage",
                    "mode": "set",
                    "value": True,
                }
            ],
        }
        target_after = _support.deepcopy(target_sheet)
        target_after["effects"] = [*list(target_after.get("effects") or []), effect]
        if actor_id == target_id:
            target_after = _support.validate_character_sheet(owner_after)
            target_after["effects"].append(effect)
            owner_after = target_after
        else:
            owner_after = _support.validate_character_sheet(owner_after)
            target_after = _support.validate_character_sheet(target_after)

        spends = list(state.get("item_spends") or [])
        if any(
            isinstance(entry, dict) and str(entry.get("id") or "") == action_id for entry in spends
        ):
            raise ValueError("gear action_id already exists on this branch")
        spends.append(
            {
                "id": action_id,
                "item_id": item_id,
                "quantity": 1,
                "reason": "Antitoxin drink",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "character_id": owner.id,
                "owner": {"kind": "character", "character_id": owner.id},
                "gear_intent": plan["intent"],
                "target_character_id": target.id,
                "removed": _support.deepcopy(removed),
                "rule_plan": _support.deepcopy(plan),
                **(
                    {
                        "combat_action": combat_action,
                        "action_cost": action_cost,
                        "action_paid": action_cost == "action",
                    }
                    if active_combat
                    else {}
                ),
            }
        )
        state["item_spends"] = spends
        normalized_state = _support.validate_party_state(state)
        character_updates = [
            _support.CharacterStateUpdate(
                owner.id,
                owner_after,
                _support.validate_character_notes(owner.notes, character_type=owner.character_type),
                expected_actor_revision,
            )
        ]
        if actor_id != target_id:
            character_updates.append(
                _support.CharacterStateUpdate(
                    target.id,
                    target_after,
                    _support.validate_character_notes(
                        target.notes, character_type=target.character_type
                    ),
                    expected_target_revision,
                )
            )
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": plan["intent"],
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "effect": _support.deepcopy(effect),
            "removed": _support.deepcopy(removed),
            "campaign": _support.asdict(
                _support.replace(campaign, state=normalized_state, revision=campaign.revision + 1)
            ),
            "owner": self.character_view(
                _support.replace(owner, sheet=owner_after, revision=owner.revision + 1)
            ),
            **(
                {
                    "combat": _support.deepcopy(encounter),
                    "action_cost": action_cost,
                    "action_paid": action_cost == "action",
                }
                if active_combat
                else {}
            ),
        }
        if actor_id != target_id:
            response["target"] = self.character_view(
                _support.replace(target, sheet=target_after, revision=target.revision + 1)
            )
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=character_updates,
            expected_campaign_revision=expected_revision,
            operation="campaign.adventuring_gear.antitoxin",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def settle_climber_kit_anchor(
        self,
        *,
        campaign: Any,
        state: dict[str, Any],
        owner: Any,
        owner_sheet: dict[str, Any],
        item: dict[str, Any],
        plan: dict[str, Any],
        action_id: str,
        item_id: str,
        actor_id: str,
        expected_actor_revision: int,
        expected_campaign_revision: int | None,
        branch_id: str,
        principal_id: str,
        idempotency_key: str,
        scope: str,
        request_payload: dict[str, Any],
        action_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Persist a Climber's Kit tether at the actor's recorded Grid location."""
        if expected_campaign_revision is None or campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict for Climber's Kit anchor")
        if owner.campaign_id != campaign.id or owner.id != actor_id:
            raise _support.CombatEngineError("Climber's Kit user must belong to the campaign")
        if owner.revision != expected_actor_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_actor_revision}, "
                f"found {owner.revision}"
            )
        if int(item.get("quantity", 0) or 0) < 1:
            raise _support.CombatEngineError("the source-bound Climber's Kit is unavailable")
        if plan.get("action_economy") != ("action" if plan.get("intent") == "anchor" else None):
            raise _support.CombatEngineError("invalid bundled Climber's Kit action plan")

        encounter = _support.deepcopy(dict(state.get("combat") or {}))
        if not encounter.get("active") or encounter.get("positioning_mode") != "grid":
            raise _support.NeedsRulingError(
                "Climber's Kit requires a recorded Grid location and elevation",
                missing=("adventuring_gear.climber_kit.grid_location",),
                ruling_kind="agent_dm_adjudication",
            )
        self.encounter_rules_edition(campaign.id, encounter)
        self.require_no_blocking_pending(encounter)
        combatant = self.require_encounter_combatant(encounter, actor_id, role="Climber's Kit user")
        position = dict(combatant.get("position") or {})
        x, y = position.get("x"), position.get("y")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise _support.NeedsRulingError(
                "Climber's Kit requires an integer Grid position",
                missing=("adventuring_gear.climber_kit.grid_location",),
                ruling_kind="agent_dm_adjudication",
            )

        anchors = _support.deepcopy(list(state.get("adventuring_gear_anchors") or []))
        active_anchor = next(
            (
                record
                for record in anchors
                if isinstance(record, dict)
                and record.get("actor_id") == actor_id
                and record.get("item_id") == item_id
                and record.get("active") is True
            ),
            None,
        )
        if plan["intent"] == "undo_anchor":
            if action_context is not None:
                raise _support.CombatEngineError("undo_anchor accepts no caller-supplied outcome")
            if active_anchor is None:
                raise _support.CombatEngineError("this Climber's Kit has no active anchor")
            active_anchor["active"] = False
            active_anchor["undone_action_id"] = action_id
            encounter_anchors = _support.deepcopy(
                list(encounter.get("adventuring_gear_anchors") or [])
            )
            for entry in encounter_anchors:
                if isinstance(entry, dict) and entry.get("id") == active_anchor.get("id"):
                    entry["active"] = False
                    entry["undone_action_id"] = action_id
            encounter["adventuring_gear_anchors"] = encounter_anchors
        else:
            recorded_elevation = position.get("elevation_ft")
            if (
                action_context is None
                and isinstance(recorded_elevation, int)
                and not isinstance(recorded_elevation, bool)
            ):
                elevation_ft = recorded_elevation
                elevation_review = {"source": "combatant.position.elevation_ft"}
            else:
                if action_context is None or set(action_context) != {"elevation_ft", "reason"}:
                    raise _support.NeedsRulingError(
                        "Climber's Kit anchoring requires a DM-reviewed current elevation",
                        missing=("adventuring_gear.climber_kit.elevation_review",),
                        ruling_kind="agent_dm_adjudication",
                    )
                elevation_ft = action_context.get("elevation_ft")
                reason = action_context.get("reason")
                if recorded_elevation is not None and recorded_elevation != elevation_ft:
                    raise _support.CombatEngineError(
                        "reviewed Climber's Kit elevation must match the recorded Grid position"
                    )
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                    raise _support.CombatEngineError(
                        "Climber's Kit elevation review needs an integer elevation and reason"
                    )
                elevation_review = {
                    "reviewed_by": principal_id,
                    "reason": reason.strip(),
                }
            if isinstance(elevation_ft, bool) or not isinstance(elevation_ft, int):
                raise _support.CombatEngineError("Climber's Kit elevation must be an integer")
            if active_anchor is not None:
                raise _support.CombatEngineError("this Climber's Kit already has an active anchor")
            position["elevation_ft"] = elevation_ft
            combatant["position"] = position
            encounter_anchors = _support.deepcopy(
                list(encounter.get("adventuring_gear_anchors") or [])
            )
            encounter_anchors = [
                entry
                for entry in encounter_anchors
                if not (
                    isinstance(entry, dict)
                    and entry.get("actor_id") == actor_id
                    and entry.get("item_id") == item_id
                )
            ]
            anchor = {
                "id": f"climber-kit-anchor:{action_id}",
                "actor_id": actor_id,
                "item_id": item_id,
                "source_key": item.get("source_key"),
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "branch_id": branch_id,
                "encounter_id": encounter.get("id"),
                "position": {"x": x, "y": y, "elevation_ft": elevation_ft},
                "maximum_fall_feet": 25,
                "maximum_climb_feet": 25,
                "active": True,
                "created_action_id": action_id,
                "created_revision": campaign.revision + 1,
                "elevation_review": elevation_review,
            }
            anchors.append(anchor)
            encounter_anchors.append(_support.deepcopy(anchor))
            encounter["adventuring_gear_anchors"] = encounter_anchors
            if plan.get("action_economy") == "action":
                encounter = _support.resolve_common_action(
                    encounter,
                    actor_id_value=actor_id,
                    action="use_object",
                    payload={
                        "kind": "adventuring_gear",
                        "intent": "anchor",
                        "item_id": item_id,
                        "source_key": item.get("source_key"),
                    },
                )

        spends = list(state.get("item_spends") or [])
        if any(isinstance(entry, dict) and entry.get("id") == action_id for entry in spends):
            raise ValueError("gear action_id already exists on this branch")
        state["adventuring_gear_anchors"] = anchors
        state["combat"] = encounter
        state["item_spends"] = [
            *spends,
            {
                "id": action_id,
                "item_id": item_id,
                "quantity": 0,
                "reason": f"Climber's Kit {plan['intent']}",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "source_key": item.get("source_key"),
                "character_id": actor_id,
                "owner": {"kind": "character", "character_id": actor_id},
                "gear_intent": plan["intent"],
                "rule_plan": _support.deepcopy(plan),
                **(
                    {"combat_action": "use_object", "action_cost": "action", "action_paid": True}
                    if plan.get("action_economy") == "action"
                    else {}
                ),
                "resulting_anchor": _support.deepcopy(
                    active_anchor if plan["intent"] == "undo_anchor" else anchor
                ),
            },
        ]
        normalized_state = _support.validate_party_state(state)
        response = {
            "status": "committed",
            "action_id": action_id,
            "item_id": item_id,
            "intent": plan["intent"],
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "anchor": _support.deepcopy(
                active_anchor if plan["intent"] == "undo_anchor" else anchor
            ),
            "action_paid": plan.get("action_economy") == "action",
            "combat": _support.deepcopy(encounter),
            "campaign": _support.asdict(
                _support.replace(campaign, state=normalized_state, revision=campaign.revision + 1)
            ),
        }
        response["campaign"]["state"] = normalized_state
        _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=normalized_state,
            character_updates=[
                _support.CharacterStateUpdate(
                    owner.id,
                    owner_sheet,
                    _support.validate_character_notes(
                        owner.notes, character_type=owner.character_type
                    ),
                    expected_actor_revision,
                )
            ],
            expected_campaign_revision=expected_campaign_revision,
            operation="campaign.adventuring_gear.climber_kit",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def reconcile_completed_item_attunements(
        self,
        campaign: Any,
        state: dict[str, Any],
        updates: list[_support.CharacterStateUpdate],
        completed: dict[str, str],
    ) -> list[_support.CharacterStateUpdate]:
        if not completed:
            return updates
        records = {actor.id: actor for actor in self.characters.list(campaign_id=campaign.id)}
        by_id = {update.character_id: update for update in updates}
        sheets = {
            actor_id: by_id[actor_id].sheet if actor_id in by_id else actor.sheet
            for actor_id, actor in records.items()
        }
        resolved = _support.complete_item_attunement_ownership(
            sheets, state.get("ground_items", []), completed
        )
        for actor_id, sheet in resolved.items():
            if actor_id in by_id:
                by_id[actor_id] = _support.replace(by_id[actor_id], sheet=sheet)
            elif sheet != _support.validate_character_sheet(records[actor_id].sheet):
                actor = records[actor_id]
                by_id[actor_id] = _support.CharacterStateUpdate(
                    actor_id, sheet, actor.notes, actor.revision
                )
        return list(by_id.values())

    def validate_inventory_custody_update(
        self,
        campaign: Any,
        state: dict[str, Any] | None,
        updates: list[_support.CharacterStateUpdate] | None,
    ) -> None:
        """Reject legacy item writes that would strand a recorded owner reference."""
        sheets = {actor.id: actor.sheet for actor in self.characters.list(campaign_id=campaign.id)}
        sheets.update({update.character_id: update.sheet for update in updates or []})
        if sheets:
            _support.validate_external_inventory_custody(
                sheets,
                (state if state is not None else campaign.state or {}).get("ground_items", []),
            )

    def ground_drop_context(
        self, campaign: Any, state: dict[str, Any], actor_id: str
    ) -> dict[str, Any]:
        encounter = dict(state.get("combat") or {})
        combatant = next(
            (
                item
                for item in [
                    *encounter.get("combatants", []),
                    *encounter.get("reinforcements", []),
                ]
                if item.get("actor_id") == actor_id
            ),
            None,
        )
        scene = self.npc_turn_scene_projection(
            self.modules.current_scene(campaign.id, scope_id="party")
        )
        result = {
            "scene_id": (
                encounter.get("scene_id")
                if encounter.get("active")
                else (scene or {}).get("scene_id")
            )
            or None,
            "encounter_id": encounter.get("id") if encounter.get("active") else None,
            "campaign_revision": campaign.revision,
            "location": {"mode": "agent", "anchor_actor_id": actor_id},
        }
        if encounter.get("active") and encounter.get("positioning_mode") == "grid" and combatant:
            result["location"] = {
                "mode": "grid",
                "position": _support.deepcopy(combatant["position"]),
            }
        return result

    def reconcile_unconscious_inventory(
        self,
        campaign: Any,
        campaign_state: dict[str, Any] | None,
        updates: list[_support.CharacterStateUpdate] | None,
        response_fields: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[_support.CharacterStateUpdate], dict[str, Any]]:
        """Settle Prone and held objects in the same unconscious mutation."""
        candidates = list(updates or [])
        posture_changes: set[str] = set()
        for index, update in enumerate(candidates):
            if update.sheet.get("edition") != "2014" or "unconscious" not in _support.condition_ids(
                update.sheet.get("conditions")
            ):
                continue
            sheet = _support.deepcopy(update.sheet)
            _support.apply_condition_change(sheet, condition_id="prone", add=True)
            if sheet != update.sheet:
                candidates[index] = _support.replace(update, sheet=sheet)
                posture_changes.add(update.character_id)
        actor_ids = [
            update.character_id
            for update in candidates
            if update.sheet.get("edition") == "2014"
            and "unconscious" in _support.condition_ids(update.sheet.get("conditions"))
            and _support.held_item_roots(update.sheet)
        ]
        if not actor_ids and not posture_changes:
            return campaign_state, candidates, response_fields
        records = {actor.id: actor for actor in self.characters.list(campaign_id=campaign.id)}
        by_id = {update.character_id: update for update in candidates}
        sheets = {
            actor_id: _support.deepcopy(by_id[actor_id].sheet if actor_id in by_id else actor.sheet)
            for actor_id, actor in records.items()
        }
        original_state = (
            campaign_state if campaign_state is not None else dict(campaign.state or {})
        )
        state = _support.deepcopy(original_state)
        encounter = state.get("combat")
        if isinstance(encounter, dict):
            for actor_id in sorted(posture_changes):
                self.sync_combatant_conditions(encounter, actor_id, sheets[actor_id])
        ground = state.get("ground_items", [])
        dropped = []
        for actor_id in actor_ids:
            result = _support.drop_held_items(
                sheets,
                ground,
                actor_id,
                record_ids={
                    item_id: f"ground-{_support.uuid4().hex}"
                    for item_id in _support.held_item_roots(sheets[actor_id])
                },
                **self.ground_drop_context(campaign, state, actor_id),
            )
            sheets, ground = result["sheets"], result["ground_items"]
            # A caster can cause another actor to drop a container. The receipt
            # identifies ground records without disclosing private contents.
            dropped.append(
                {"actor_id": actor_id, "ground_ids": [item["id"] for item in result["dropped"]]}
            )
        if actor_ids:
            state["ground_items"] = ground
        for actor_id, sheet in sheets.items():
            previous = by_id.get(actor_id)
            if previous is not None:
                by_id[actor_id] = _support.replace(previous, sheet=sheet)
            elif sheet != _support.validate_character_sheet(records[actor_id].sheet):
                actor = records[actor_id]
                by_id[actor_id] = _support.CharacterStateUpdate(
                    actor_id, sheet, actor.notes, actor.revision
                )

        def refresh_preview(value: Any) -> Any:
            if isinstance(value, list):
                return [refresh_preview(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: refresh_preview(item) for key, item in value.items()}
            if (
                isinstance(value.get("sheet"), dict)
                and isinstance(value.get("id"), str)
                and value.get("id") in by_id
            ):
                result["sheet"] = _support.deepcopy(by_id[value["id"]].sheet)
                if "derived" in value:
                    result["derived"] = self.character_view(
                        _support.replace(records[value["id"]], sheet=by_id[value["id"]].sheet)
                    )["derived"]
            return result

        response = refresh_preview(response_fields)
        if posture_changes and "combat" in response and isinstance(encounter, dict):
            response["combat"] = _support.deepcopy(encounter)
        if dropped:
            response["ground_item_drops"] = dropped
        # A card-only posture correction must not invent ground records or a
        # campaign revision when no encounter projection needs to change.
        settled_state = state if state != original_state else campaign_state
        return settled_state, list(by_id.values()), response

    def inventory_item_for_receipt(
        self,
        target_sheet: dict[str, Any],
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """Detach inventory-local links that do not exist at the destination."""
        received = _support.deepcopy(item)
        if str(received.get("kind") or "") != "weapon":
            return received
        mechanics = dict(received.get("mechanics") or {})
        ammunition_item_id = str(mechanics.get("ammunition_item_id") or "").strip()
        if not ammunition_item_id:
            return received
        target_ammunition = next(
            (
                candidate
                for candidate in dict(target_sheet.get("inventory") or {}).get("items", [])
                if str(candidate.get("id") or "") == ammunition_item_id
                and str(candidate.get("kind") or "") == "ammunition"
            ),
            None,
        )
        if target_ammunition is None:
            mechanics["ammunition_item_id"] = None
            received["mechanics"] = mechanics
        return received

    def campaign_item_spend(
        self,
        campaign_id: str,
        spend_id: str,
        item_id: str,
        quantity: int,
        reason: str,
        source_ref: str,
        character_id: str | None = None,
        expected_character_revision: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically expend one source-bound item from party or character inventory."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        state = dict(campaign.state or {})
        phase = self.authoritative_phase(campaign_id)
        if phase == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("end active combat before spending a party item")
        if phase != _support.PROFILE_PLAY:
            raise _support.CombatEngineError("source-bound items can be spent only in play")

        normalized_spend_id = str(spend_id).strip()
        normalized_item_id = str(item_id).strip()
        normalized_reason = str(reason).strip()
        normalized_character_id = str(character_id or "").strip()
        if not normalized_spend_id or len(normalized_spend_id) > 200:
            raise ValueError("spend_id must contain 1 to 200 characters")
        if not normalized_item_id or len(normalized_item_id) > 200:
            raise ValueError("item_id must contain 1 to 200 characters")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not normalized_reason or len(normalized_reason) > 1000:
            raise ValueError("reason must contain 1 to 1000 characters")
        if bool(normalized_character_id) != (expected_character_revision is not None):
            raise ValueError(
                "character_id and expected_character_revision must be provided together"
            )
        if expected_character_revision is not None and (
            isinstance(expected_character_revision, bool)
            or not isinstance(expected_character_revision, int)
            or expected_character_revision < 0
        ):
            raise ValueError("expected_character_revision must be a non-negative integer")
        normalized_source_ref, _, _ = self.managed_module_source_ref(
            campaign_id,
            source_ref,
        )

        request_payload = {
            "spend_id": normalized_spend_id,
            "item_id": normalized_item_id,
            "quantity": quantity,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "character_id": normalized_character_id or None,
            "expected_character_revision": expected_character_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-item-spend:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        spends = list(state.get("item_spends") or [])
        if any(
            str(dict(item).get("id") or "") == normalized_spend_id
            for item in spends
            if isinstance(item, dict)
        ):
            raise ValueError("item spend_id already exists on this branch")

        character_update: _support.CharacterStateUpdate | None = None
        character_after: dict[str, Any] | None = None
        if normalized_character_id:
            current_character = self.characters.get(normalized_character_id)
            if current_character.campaign_id != campaign_id:
                raise ValueError("item owner must belong to the campaign")
            character_sheet, removed = _support.remove_inventory_item(
                current_character.sheet,
                normalized_item_id,
                quantity,
            )
            normalized_character_sheet = _support.validate_character_sheet(character_sheet)
            character_update = _support.CharacterStateUpdate(
                character_id=current_character.id,
                sheet=normalized_character_sheet,
                notes=_support.validate_character_notes(
                    current_character.notes,
                    character_type=current_character.character_type,
                ),
                expected_revision=expected_character_revision,
            )
            character_after = self.character_view(
                _support.replace(
                    current_character,
                    sheet=normalized_character_sheet,
                    revision=current_character.revision + 1,
                )
            )
            next_state = _support.deepcopy(state)
        else:
            sheet, removed = _support.remove_inventory_item(
                self.party_sheet(state),
                normalized_item_id,
                quantity,
            )
            next_state = self.party_state(state, sheet)
        spends.append(
            {
                "id": normalized_spend_id,
                "item_id": normalized_item_id,
                "quantity": quantity,
                "reason": normalized_reason,
                "source_ref": normalized_source_ref,
                **(
                    {
                        "character_id": normalized_character_id,
                        "owner": {
                            "kind": "character",
                            "character_id": normalized_character_id,
                        },
                    }
                    if normalized_character_id
                    else {}
                ),
                "removed": _support.deepcopy(removed),
            }
        )
        next_state["item_spends"] = spends
        normalized_next_state = _support.validate_party_state(next_state)
        response = {
            "status": "committed",
            "spend_id": normalized_spend_id,
            "item_id": normalized_item_id,
            "quantity": quantity,
            "removed": removed,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "owner": (
                {
                    "kind": "character",
                    "character_id": normalized_character_id,
                }
                if normalized_character_id
                else {"kind": "party"}
            ),
            "party": self.party_view_from_state(normalized_next_state),
            **({"character": character_after} if character_after is not None else {}),
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_next_state,
                    revision=campaign.revision + 1,
                )
            ),
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=normalized_next_state,
            character_updates=([character_update] if character_update is not None else None),
            expected_campaign_revision=expected_revision,
            operation="campaign.item.spend",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
        )
        return response

    def character_wallet_adjust(
        self,
        character_id: str,
        denomination: str,
        amount: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Adjust one D&D character wallet denomination through the v2 schema."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "wallet adjustment")
        return self.update_sheet(
            character_id,
            _support.adjust_wallet(current.sheet, denomination, amount),
            operation="character.wallet.adjust",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"denomination": denomination, "amount": amount},
        )

    def character_inventory_add(
        self,
        character_id: str,
        item: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a normalized inventory item and return its assigned item id."""
        if dict(item.get("mechanics") or {}).get("poison_dose") is not None:
            raise _support.CombatEngineError(
                "source-bound poison doses must be added from their reviewed content artifact"
            )
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "inventory changes")
        campaign = self.campaigns.get(current.campaign_id) if current.campaign_id else None
        phase = (
            self.authoritative_phase(current.campaign_id)
            if campaign is not None
            else _support.PROFILE_LOBBY
        )
        if item.get("attunement") == "attuned" and phase != _support.PROFILE_LOBBY:
            raise _support.CombatEngineError(
                "an item can enter Play as required, but attunement must be "
                "completed through a short rest"
            )
        sheet, item_id = _support.add_inventory_item(current.sheet, item)
        return self.update_sheet(
            character_id,
            sheet,
            operation="character.inventory.add",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"item": item},
            response_extra={"item_id": item_id},
        )

    def character_inventory_update(
        self,
        character_id: str,
        item_id: str,
        patch: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Update one structured inventory item without bypassing D&D validation."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "inventory changes")
        normalized_patch = _support.deepcopy(patch)
        current_item = next(
            (
                item
                for item in current.sheet.get("inventory", {}).get("items", [])
                if str(item.get("id") or "") == item_id
            ),
            None,
        )
        if (
            current_item is not None
            and dict(current_item.get("mechanics") or {}).get("poison_dose") is not None
        ) or dict(normalized_patch.get("mechanics") or {}).get("poison_dose") is not None:
            raise _support.CombatEngineError(
                "source-bound poison dose identity changes only through poison operations"
            )
        patched_mechanics = normalized_patch.get("mechanics")
        if (
            isinstance(patched_mechanics, dict)
            and dict(patched_mechanics).get("spellcasting") is not None
        ):
            if current.campaign_id is None:
                raise ValueError("magic item spell hydration requires a campaign-bound character")
            current_item = next(
                (
                    item
                    for item in current.sheet.get("inventory", {}).get("items", [])
                    if str(item.get("id") or "") == item_id
                ),
                None,
            )
            if current_item is None:
                raise LookupError(item_id)
            hydrated = self.hydrate_magic_item_spell_artifacts(
                current.campaign_id,
                {**_support.deepcopy(current_item), **normalized_patch, "id": item_id},
            )
            normalized_patch["mechanics"] = _support.deepcopy(hydrated["mechanics"])
        if "attunement" in normalized_patch:
            campaign = self.campaigns.get(current.campaign_id) if current.campaign_id else None
            phase = (
                self.authoritative_phase(current.campaign_id)
                if campaign is not None
                else _support.PROFILE_LOBBY
            )
            current_item = next(
                (
                    item
                    for item in current.sheet.get("inventory", {}).get("items", [])
                    if str(item.get("id") or "") == item_id
                ),
                None,
            )
            if (
                phase != _support.PROFILE_LOBBY
                and current_item is not None
                and normalized_patch["attunement"] != current_item.get("attunement")
            ):
                raise _support.CombatEngineError(
                    "attunement cannot be patched during Play; use a short rest"
                )
        return self.update_sheet(
            character_id,
            _support.update_inventory_item(current.sheet, item_id, normalized_patch),
            operation="character.inventory.update",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"item_id": item_id, "patch": normalized_patch},
        )

    def character_inventory_remove(
        self,
        character_id: str,
        item_id: str,
        quantity: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Remove an inventory stack or quantity and return the removed item data."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "inventory changes")
        sheet, removed = _support.remove_inventory_item(current.sheet, item_id, quantity)
        return self.update_sheet(
            character_id,
            sheet,
            operation="character.inventory.remove",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"item_id": item_id, "quantity": quantity},
            response_extra={"removed": removed},
        )

    def character_inventory_equip(
        self,
        character_id: str,
        item_id: str,
        slot: str | None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Equip an inventory item in a validated D&D equipment slot, or unequip it."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "equipment changes")
        return self.update_sheet(
            character_id,
            _support.equip_inventory_item(current.sheet, item_id, slot),
            operation="character.inventory.equip",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"item_id": item_id, "slot": slot},
        )

    def character_inventory_recharge(
        self,
        character_id: str,
        item_id: str,
        trigger: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Roll and apply one source-declared magic-item charge recovery."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "magic item recharge")
        if current.campaign_id is None:
            raise ValueError("magic item recharge requires a campaign-bound character")
        source_item = next(
            (
                item
                for item in current.sheet.get("inventory", {}).get("items", [])
                if str(item.get("id") or "") == item_id
            ),
            None,
        )
        if source_item is None or source_item.get("kind") != "magic_item":
            raise ValueError("item_id is not a magic item on this actor card")
        charge_rules = dict(dict(source_item.get("mechanics") or {}).get("charge_rules") or {})
        formula = str(charge_rules.get("recovery_formula") or "")
        if not formula:
            raise ValueError("magic item has no source-declared charge recovery")
        dice = _support.asdict(_support.roll(formula))
        applied = _support.recharge_magic_item_charges(
            current.sheet,
            source_item_id=item_id,
            trigger=trigger,
            rolled_total=int(dice["total"]),
        )
        receipts = _support.core_receipts(
            self.effective_rule_context(current.campaign_id),
            [_support.CORE_MAGIC_ITEM_RECHARGE_MECHANIC_ID],
            "character.inventory.magic_item.recharge",
        )
        return self.update_sheet(
            character_id,
            applied["sheet"],
            operation="character.inventory.magic_item.recharge",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"item_id": item_id, "trigger": trigger},
            response_extra={
                "recharge": {
                    key: value
                    for key, value in applied.items()
                    if key not in {"sheet", "rule_receipts"}
                }
                | {"roll": dice},
                "rule_receipts": receipts,
            },
            rule_receipts=receipts,
        )

    def ground_inventory_settlement(
        self,
        campaign_id: str,
        actor_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        principal_id: str,
        expected_revision: int | None,
        expected_character_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None,
        in_combat: bool,
    ) -> dict[str, Any]:
        """Commit ground custody, character cards and pickup payment atomically."""
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected campaign revision must be a non-negative integer")
        if not in_combat and (
            type(expected_character_revision) is not int or expected_character_revision < 0
        ):
            raise ValueError("expected character revision must be a non-negative integer")
        resolved_branch = self.require_current_branch(campaign_id, branch_id)
        allowed = {
            "pickup_ground": {"ground_id", "slot", "spatial_facts"},
            "draw_weapon": {"item_id", "slot"},
            "stow_weapon": {"item_id"},
            "drop_held": set(),
        }.get(action)
        if allowed is None or set(payload) - allowed:
            raise ValueError("unsupported ground inventory action or payload")
        if action in {"draw_weapon", "stow_weapon"} and not in_combat:
            raise ValueError("draw/stow settlement requires active combat")
        request = {
            "actor_id": actor_id,
            "action": action,
            "payload": _support.deepcopy(payload),
            "in_combat": in_combat,
            "branch_id": resolved_branch,
        }
        scope = f"ground-inventory:{campaign_id}:{resolved_branch}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError("campaign revision conflict")
        records = {actor.id: actor for actor in self.characters.list(campaign_id=campaign_id)}
        actor = records[actor_id]
        if (
            expected_character_revision is not None
            and actor.revision != expected_character_revision
        ):
            raise ValueError("character revision conflict")
        if actor.sheet.get("edition") != "2014":
            raise ValueError("this ground inventory settlement implements the 2014 rules")
        if (
            _support.condition_ids(actor.sheet.get("conditions"))
            & _support.INCAPACITATING_STATE_IDS
        ):
            raise _support.CombatEngineError(
                "an incapacitated actor cannot drop or pick up an object voluntarily"
            )
        state = _support.deepcopy(dict(campaign.state or {}))
        encounter = state.get("combat")
        active = isinstance(encounter, dict) and encounter.get("active", False)
        if bool(active) != in_combat:
            raise _support.CombatEngineError(
                "use the ground inventory entry point for the current game phase"
            )
        if in_combat:
            self.require_no_blocking_pending(encounter)
            acting = _support.current_combatant(encounter)
            if not acting or acting.get("actor_id") != actor_id:
                raise _support.CombatEngineError(
                    "ground item interaction requires the actor's turn"
                )
        context = self.ground_drop_context(campaign, state, actor_id)
        sheets = {key: _support.deepcopy(record.sheet) for key, record in records.items()}
        ground = state.get("ground_items", [])
        payment = None
        if action in {"draw_weapon", "stow_weapon"}:
            item_id = payload.get("item_id")
            item = next(
                (
                    entry
                    for entry in sheets[actor_id]["inventory"]["items"]
                    if entry["id"] == item_id
                ),
                None,
            )
            if item is None or item.get("kind") != "weapon":
                raise ValueError("draw/stow requires an owned weapon item_id")
            slots = sheets[actor_id]["inventory"]["equipment_slots"]
            if action == "draw_weapon":
                slot = payload.get("slot")
                if slot not in {"main_hand", "off_hand"}:
                    raise ValueError("draw requires main_hand or off_hand slot")
                if item.get("equipped") or slots[slot] is not None:
                    raise ValueError("draw requires a stowed weapon and an empty hand slot")
            else:
                if item.get("equipped_slot") not in {"main_hand", "off_hand"}:
                    raise ValueError("stow requires a weapon currently held in a hand")
                slot = None
            sheets[actor_id] = _support.equip_inventory_item(sheets[actor_id], item_id, slot)
            payment = (
                "object_interaction"
                if acting.get("turn_budget", {}).get("object_interaction", 0) > 0
                else "main_action"
                if acting.get("turn_budget", {}).get("main_action", 0) > 0
                else "extra_action"
            )
            state["combat"] = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="interact_object" if payment == "object_interaction" else "use_object",
                payload={"object_description": item["name"], "interaction": action},
                payment=payment,
            )
            settled = {"sheets": sheets, "ground_items": ground}
            details = {"item_id": item_id, "slot": slot, "payment": payment}
        elif action == "drop_held":
            roots = _support.held_item_roots(actor.sheet)
            if not roots:
                raise ValueError("actor has no held objects to drop")
            settled = _support.drop_held_items(
                sheets,
                ground,
                actor_id,
                record_ids={item_id: f"ground-{_support.uuid4().hex}" for item_id in roots},
                **context,
            )
            details = {"dropped": settled["dropped"]}
        else:
            ground_id = payload.get("ground_id")
            if not isinstance(ground_id, str) or not ground_id.strip():
                raise ValueError("pickup requires a ground_id")
            entry = next((item for item in ground if item.get("id") == ground_id), None)
            if entry is None:
                raise ValueError("ground item is not available")
            if (
                entry.get("scene_id") is not None
                and context["scene_id"] is not None
                and entry["scene_id"] != context["scene_id"]
            ):
                raise _support.CombatEngineError("ground item belongs to a different scene")
            grid = (
                in_combat
                and encounter.get("positioning_mode") == "grid"
                and entry["location"]["mode"] == "grid"
                and entry["encounter_id"] == encounter.get("id")
            )
            if grid:
                if "spatial_facts" in payload:
                    raise _support.CombatEngineError(
                        "grid pickup does not accept spatial overrides"
                    )
                distance = self.combat_distance(
                    acting.get("position"),
                    entry["location"]["position"],
                    cell_ft=int(
                        dict(dict(encounter.get("battle_map") or {}).get("grid") or {}).get(
                            "cell_ft", 5
                        )
                    ),
                )
                if distance is None or distance > 5:
                    raise _support.CombatEngineError("ground item must be within 5 feet")
            else:
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                facts = payload.get("spatial_facts")
                if facts is None:
                    raise _support.NeedsRulingError(
                        "ground pickup needs a source-location reach decision",
                        missing=("ground_item.spatial_facts",),
                        ruling_kind="agent_dm_adjudication",
                    )
                if not isinstance(facts, dict) or set(facts) != {
                    "decision_id",
                    "reason",
                    "campaign_revision",
                    "can_reach_ground_item",
                }:
                    raise ValueError("ground pickup requires exact reach decision fields")
                if (
                    not isinstance(facts["decision_id"], str)
                    or not 1 <= len(facts["decision_id"].strip()) <= 100
                    or not isinstance(facts["reason"], str)
                    or not 10 <= len(facts["reason"].strip()) <= 1000
                    or type(facts["campaign_revision"]) is not int
                    or facts["campaign_revision"] != campaign.revision
                    or facts["can_reach_ground_item"] is not True
                ):
                    raise ValueError("ground pickup needs current affirmative reach evidence")
            settled = _support.pickup_ground_item(sheets, ground, actor_id, ground_id)
            slot = payload.get("slot")
            if slot is not None:
                if slot not in {"main_hand", "off_hand"}:
                    raise ValueError("pickup slot must be main_hand or off_hand")
                if settled["sheets"][actor_id]["inventory"]["equipment_slots"][slot] is not None:
                    raise ValueError("pickup requires an empty hand slot")
                picked_id = settled["picked_up"]["root_item_id"]
                settled["sheets"][actor_id] = _support.equip_inventory_item(
                    settled["sheets"][actor_id], picked_id, slot
                )
            if in_combat:
                payment = (
                    "object_interaction"
                    if acting.get("turn_budget", {}).get("object_interaction", 0) > 0
                    else "main_action"
                    if acting.get("turn_budget", {}).get("main_action", 0) > 0
                    else "extra_action"
                )
                encounter = _support.resolve_common_action(
                    encounter,
                    actor_id_value=actor_id,
                    action="interact_object" if payment == "object_interaction" else "use_object",
                    payload={
                        "object_description": entry["items"][0]["name"],
                        "interaction": "pick up",
                    },
                    payment=payment,
                )
                state["combat"] = encounter
            details = {
                "picked_up": settled["picked_up"],
                "payment": payment,
                "spatial_facts": _support.deepcopy(payload.get("spatial_facts")),
            }
        state["ground_items"] = settled["ground_items"]
        updates = [
            _support.CharacterStateUpdate(key, sheet, records[key].notes, records[key].revision)
            for key, sheet in settled["sheets"].items()
            if sheet != _support.validate_character_sheet(records[key].sheet)
        ]
        after = _support.replace(
            actor, sheet=settled["sheets"][actor_id], revision=actor.revision + 1
        )
        return self.commit_campaign_state(
            campaign,
            state,
            operation=f"inventory.{action}",
            principal_id=principal_id,
            branch_id=resolved_branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request,
            response_fields={
                "status": "committed",
                "result": details,
                "character": self.visible_character_view(after, principal_id),
            },
            character_updates=updates,
        )

    def character_inventory_transfer(
        self,
        source_character_id: str,
        target_character_id: str,
        item_id: str,
        quantity: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_campaign_revision: int | None = None,
        expected_source_revision: int | None = None,
        expected_target_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Move an inventory item between two actors in the same campaign."""
        payload = {
            "source_character_id": source_character_id,
            "target_character_id": target_character_id,
            "item_id": item_id,
            "quantity": quantity,
        }
        source = self.characters.get(source_character_id)
        target = self.characters.get(target_character_id)
        self.require_outside_active_combat(source, "inventory transfer")
        self.require_outside_active_combat(target, "inventory transfer")
        if source.campaign_id is None or source.campaign_id != target.campaign_id:
            raise ValueError("characters must belong to the same campaign")
        if (
            expected_campaign_revision is None
            or expected_source_revision is None
            or expected_target_revision is None
            or not idempotency_key
        ):
            raise ValueError(
                "expected_campaign_revision, expected_source_revision, "
                "expected_target_revision, and idempotency_key are required for inventory transfer"
            )
        branch_id = self.require_current_branch(source.campaign_id, None)
        payload["branch_id"] = branch_id
        scope = f"character-inventory:{source.campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        self.access.require_actor(source.campaign_id, source.id, principal_id, control=True)
        self.access.require_actor(source.campaign_id, target.id, principal_id, control=True)
        campaign = self.campaigns.get(source.campaign_id)
        if campaign.revision != expected_campaign_revision:
            raise ValueError("campaign revision conflict")
        if (
            source.revision != expected_source_revision
            or target.revision != expected_target_revision
        ):
            raise ValueError("character revision conflict")
        reference_updates = []
        if source.sheet.get("edition") == target.sheet.get("edition") == "2014":
            records = {actor.id: actor for actor in self.characters.list(campaign_id=campaign.id)}
            settled = _support.transfer_actor_inventory_item(
                {key: actor.sheet for key, actor in records.items()},
                (campaign.state or {}).get("ground_items", []),
                source.id,
                target.id,
                item_id,
                quantity,
            )
            source_sheet, target_sheet = settled["sheets"][source.id], settled["sheets"][target.id]
            moved = settled["item"]
            reference_updates = [
                _support.CharacterStateUpdate(key, sheet, records[key].notes, records[key].revision)
                for key, sheet in settled["sheets"].items()
                if key not in {source.id, target.id}
                and sheet != _support.validate_character_sheet(records[key].sheet)
            ]
        else:
            source_sheet, moved = _support.remove_inventory_item(source.sheet, item_id, quantity)
            moved = self.inventory_item_for_receipt(target.sheet, moved)
            target_sheet = _support.receive_inventory_item(target.sheet, moved)
        source_sheet = self.finalize_actor_sheet_rulings(source_sheet, source.campaign_id)
        target_sheet = self.finalize_actor_sheet_rulings(target_sheet, target.campaign_id)
        source_after = _support.replace(
            source,
            sheet=source_sheet,
            revision=source.revision + 1,
        )
        target_after = _support.replace(
            target,
            sheet=target_sheet,
            revision=target.revision + 1,
        )
        response = self.commit_campaign_state(
            campaign,
            None,
            operation="character.inventory.transfer",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "source": self.visible_character_view(source_after, principal_id),
                "target": self.visible_character_view(target_after, principal_id),
                "item": moved,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    source.id, source_sheet, source.notes, expected_source_revision
                ),
                _support.CharacterStateUpdate(
                    target.id, target_sheet, target.notes, expected_target_revision
                ),
                *reference_updates,
            ],
            include_campaign_revision=True,
            include_revisions=False,
            expected_campaign_revision=expected_campaign_revision,
        )
        return response

    def party_inventory_add(
        self,
        campaign_id: str,
        item: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add an item to the campaign shared inventory."""
        if dict(item.get("mechanics") or {}).get("poison_dose") is not None:
            raise _support.CombatEngineError(
                "source-bound poison doses must be added from their reviewed content artifact"
            )
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required for party inventory writes"
            )
        before = self.campaigns.get(campaign_id)
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {"item": item, "expected_revision": expected_revision, "branch_id": branch_id}
        scope = f"party-inventory:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        sheet, item_id = _support.add_inventory_item(self.party_sheet(before.state), item)
        after = self.campaigns.update_audited(
            campaign_id,
            state=self.party_state(before.state, sheet),
            expected_revision=expected_revision,
            operation="party.inventory.add",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "inventory": sheet["inventory"],
                    "item_id": item_id,
                    "campaign": _support.asdict(result),
                },
            ),
        )
        return {
            "inventory": sheet["inventory"],
            "item_id": item_id,
            "campaign": _support.asdict(after),
        }

    def party_inventory_remove(
        self,
        campaign_id: str,
        item_id: str,
        quantity: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Remove an item or partial stack from the campaign shared inventory."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required for party inventory writes"
            )
        before = self.campaigns.get(campaign_id)
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {
            "item_id": item_id,
            "quantity": quantity,
            "expected_revision": expected_revision,
            "branch_id": branch_id,
        }
        scope = f"party-inventory:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        sheet, removed = _support.remove_inventory_item(
            self.party_sheet(before.state), item_id, quantity
        )
        after = self.campaigns.update_audited(
            campaign_id,
            state=self.party_state(before.state, sheet),
            expected_revision=expected_revision,
            operation="party.inventory.remove",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "inventory": sheet["inventory"],
                    "removed": removed,
                    "campaign": _support.asdict(result),
                },
            ),
        )
        return {
            "inventory": sheet["inventory"],
            "removed": removed,
            "campaign": _support.asdict(after),
        }

    def party_inventory_transfer(
        self,
        campaign_id: str,
        character_id: str,
        item_id: str,
        direction: str,
        quantity: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_campaign_revision: int | None = None,
        expected_character_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Deposit an actor item to, or withdraw one from, the party shared inventory."""
        if direction not in {"deposit", "withdraw"}:
            raise ValueError("direction must be deposit or withdraw")
        if (
            expected_campaign_revision is None
            or expected_character_revision is None
            or not idempotency_key
        ):
            raise ValueError(
                "expected campaign/character revisions and idempotency_key are required"
            )
        campaign = self.campaigns.get(campaign_id)
        character = self.characters.get(character_id)
        if character.campaign_id != campaign_id:
            raise ValueError("character must belong to the campaign")
        self.access.require_actor(campaign_id, character_id, principal_id, control=True)
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {
            "campaign_id": campaign_id,
            "character_id": character_id,
            "item_id": item_id,
            "direction": direction,
            "quantity": quantity,
            "expected_campaign_revision": expected_campaign_revision,
            "expected_character_revision": expected_character_revision,
            "branch_id": branch_id,
        }
        scope = f"party-inventory:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        shared = self.party_sheet(campaign.state)
        if direction == "deposit":
            character_sheet, moved = _support.remove_inventory_item(
                character.sheet, item_id, quantity
            )
            moved = self.inventory_item_for_receipt(shared, moved)
            shared_sheet = _support.receive_inventory_item(shared, moved)
        else:
            shared_sheet, moved = _support.remove_inventory_item(shared, item_id, quantity)
            moved = self.inventory_item_for_receipt(character.sheet, moved)
            character_sheet = _support.receive_inventory_item(character.sheet, moved)
        updated_state = self.party_state(campaign.state, shared_sheet)
        normalized_character_sheet = _support.validate_character_sheet(
            self.finalize_actor_sheet_rulings(character_sheet, campaign_id),
            rules=self.effective_rule_context(campaign_id),
        )
        response = {
            "party": self.party_view_from_state(updated_state),
            "campaign_id": campaign_id,
            "campaign_revision": campaign.revision + 1,
            "branch_id": branch_id,
            "character": self.character_view(
                _support.replace(
                    character,
                    sheet=normalized_character_sheet,
                    revision=character.revision + 1,
                )
            ),
            "item": moved,
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=updated_state,
            character_updates=[
                _support.CharacterStateUpdate(
                    character.id,
                    normalized_character_sheet,
                    character.notes,
                    expected_character_revision,
                )
            ],
            operation=f"party.inventory.{direction}",
            actor=principal_id,
            branch_id=branch_id,
            expected_campaign_revision=expected_campaign_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response,
            ),
        )
        return response

    def party_wallet_adjust(
        self,
        campaign_id: str,
        denomination: str,
        amount: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Credit or debit one denomination in the shared party wallet."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required for party wallet writes"
            )
        before = self.campaigns.get(campaign_id)
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {
            "denomination": denomination,
            "amount": amount,
            "expected_revision": expected_revision,
            "branch_id": branch_id,
        }
        scope = f"party-wallet:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        sheet = _support.adjust_wallet(self.party_sheet(before.state), denomination, amount)
        after = self.campaigns.update_audited(
            campaign_id,
            state=self.party_state(before.state, sheet),
            expected_revision=expected_revision,
            operation="party.wallet.adjust",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "wallet": sheet["inventory"]["wallet"],
                    "campaign": _support.asdict(result),
                },
            ),
        )
        return {
            "wallet": sheet["inventory"]["wallet"],
            "campaign": _support.asdict(after),
        }

    def party_wallet_transfer(
        self,
        campaign_id: str,
        character_id: str,
        denomination: str,
        amount: int,
        direction: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_campaign_revision: int | None = None,
        expected_character_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Deposit currency to, or withdraw currency from, the shared party wallet."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        if direction not in {"deposit", "withdraw"}:
            raise ValueError("direction must be deposit or withdraw")
        if (
            expected_campaign_revision is None
            or expected_character_revision is None
            or not idempotency_key
        ):
            raise ValueError(
                "expected campaign/character revisions and idempotency_key are required"
            )
        campaign = self.campaigns.get(campaign_id)
        character = self.characters.get(character_id)
        if character.campaign_id != campaign_id:
            raise ValueError("character must belong to the campaign")
        self.access.require_actor(campaign_id, character_id, principal_id, control=True)
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {
            "campaign_id": campaign_id,
            "character_id": character_id,
            "denomination": denomination,
            "amount": amount,
            "direction": direction,
            "expected_campaign_revision": expected_campaign_revision,
            "expected_character_revision": expected_character_revision,
            "branch_id": branch_id,
        }
        scope = f"party-wallet:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if campaign.revision != expected_campaign_revision:
            raise ValueError(f"campaign revision conflict: {campaign_id}")
        shared = self.party_sheet(campaign.state)
        delta = amount if direction == "deposit" else -amount
        shared_sheet = _support.adjust_wallet(shared, denomination, delta)
        character_sheet = _support.adjust_wallet(character.sheet, denomination, -delta)
        updated_state = self.party_state(campaign.state, shared_sheet)
        normalized_character_sheet = _support.validate_character_sheet(character_sheet)
        response = {
            "party": self.party_view_from_state(updated_state),
            "character": self.character_view(
                _support.replace(
                    character,
                    sheet=normalized_character_sheet,
                    revision=character.revision + 1,
                )
            ),
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=updated_state,
            character_updates=[
                _support.CharacterStateUpdate(
                    character.id,
                    normalized_character_sheet,
                    character.notes,
                    expected_character_revision
                    if expected_character_revision is not None
                    else character.revision,
                )
            ],
            operation=f"party.wallet.{direction}",
            actor=principal_id,
            branch_id=branch_id,
            expected_campaign_revision=expected_campaign_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response,
            ),
        )
        return response

    def settle_magic_item_last_charge(
        self,
        applied: dict[str, Any],
        *,
        source_item_id: str | None,
        rules: _support.ResolutionContext,
    ) -> dict[str, Any]:
        """Resolve a source-bound last-charge destruction check in the same mutation."""
        if not source_item_id or not applied.get("last_charge_rule"):
            return applied
        last_charge_rule = dict(applied["last_charge_rule"])
        dice = _support.asdict(_support.roll(str(last_charge_rule["formula"])))
        resolution = _support.resolve_magic_item_last_charge(
            applied["sheet"],
            source_item_id=source_item_id,
            rolled_total=int(dice["total"]),
        )
        result = dict(applied)
        result["sheet"] = resolution["sheet"]
        result["last_charge_resolution"] = {
            key: value for key, value in resolution.items() if key not in {"sheet", "rule_receipts"}
        } | {"roll": dice}
        result["rule_receipts"] = [
            *list(applied.get("rule_receipts") or []),
            *_support.core_receipts(
                rules,
                [_support.CORE_MAGIC_ITEM_LAST_CHARGE_MECHANIC_ID],
                "spell.magic_item.last_charge",
            ),
        ]
        return result

    def spend_exact_wallet_payment(
        self, wallet: dict[str, Any], payment: Any, *, required_cp: int
    ) -> dict[str, int]:
        """Validate an explicit coin payment without inventing currency exchange or change."""
        if not isinstance(payment, dict):
            raise ValueError("spellbook copy selection.payment must be a coin object")
        unknown = set(payment) - set(_support.DENOMINATION_CP_VALUES)
        if unknown:
            raise ValueError(f"spellbook copy payment has unknown coins: {sorted(unknown)}")
        normalized: dict[str, int] = {}
        total_cp = 0
        for denomination, multiplier in _support.DENOMINATION_CP_VALUES.items():
            amount = payment.get(denomination, 0)
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError("spellbook copy coin amounts must be non-negative integers")
            if amount > int(wallet.get(denomination, 0) or 0):
                raise ValueError(f"insufficient {denomination} for spellbook copy")
            normalized[denomination] = amount
            total_cp += amount * multiplier
        if total_cp != required_cp:
            raise ValueError(
                f"spellbook copy payment must equal exactly {required_cp} cp; got {total_cp} cp"
            )
        for denomination, amount in normalized.items():
            wallet[denomination] = int(wallet.get(denomination, 0) or 0) - amount
        return normalized

    def inventory_change(
        self,
        owner: Literal["character", "party"],
        action: Literal[
            "add",
            "update",
            "remove",
            "equip",
            "recharge",
            "consume_ammunition",
        ],
        owner_id: str,
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Change owned inventory using the owner's current revision and a request key.

        Character payloads: add={item}, update={item_id, patch},
        remove={item_id, quantity?}, equip={item_id, slot},
        where an explicit slot=null unequips the item; do not patch equipped flags.
        recharge={item_id, trigger}, consume_ammunition={weapon_id, quantity?}.
        item_id is the owned sheet.inventory.items[].id from the latest receipt,
        not a catalog artifact_id. Party supports only add/remove. Apply catalog
        equipment with character_content_apply first, then equip its returned
        owned item ID; change quantity through update rather than applying twice.
        update.patch.mechanics merges mechanic fields; omitted fields are preserved.
        Bind ammunition with patch={mechanics:{ammunition_item_id:<owned ammo id>}}.
        Explicit null clears a nullable field; nested records and lists replace
        their whole field value. Revision and idempotency_key are top-level inputs.
        """
        data = self.facade_payload(payload)
        if owner == "party" and action not in {"add", "remove"}:
            raise ValueError("party inventory supports only add and remove")
        if owner == "character":
            if action == "add":
                current = self.characters.get(owner_id)
                item = self.required(data, "item")
                if (
                    isinstance(item, dict)
                    and dict(item.get("mechanics") or {}).get("spellcasting") is not None
                ):
                    if current.campaign_id is None:
                        raise ValueError(
                            "magic item spell hydration requires a campaign-bound character"
                        )
                    item = self.hydrate_magic_item_spell_artifacts(
                        current.campaign_id,
                        item,
                    )
                result = self.character_inventory_add(
                    owner_id,
                    item,
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif action == "update":
                result = self.character_inventory_update(
                    owner_id,
                    self.required(data, "item_id"),
                    self.required(data, "patch"),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif action == "remove":
                result = self.character_inventory_remove(
                    owner_id,
                    self.required(data, "item_id"),
                    data.get("quantity"),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif action == "equip":
                if "slot" not in data:
                    raise ValueError("payload.slot is required; use null to unequip")
                result = self.character_inventory_equip(
                    owner_id,
                    self.required(data, "item_id"),
                    data["slot"],
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif action == "recharge":
                result = self.character_inventory_recharge(
                    owner_id,
                    self.required(data, "item_id"),
                    self.required(data, "trigger"),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            else:
                result = self.character_ammunition_consume(
                    owner_id,
                    self.required(data, "weapon_id"),
                    data.get("quantity", 1),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
        elif action == "add":
            result = self.party_inventory_add(
                owner_id,
                self.required(data, "item"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        else:
            result = self.party_inventory_remove(
                owner_id,
                self.required(data, "item_id"),
                data.get("quantity"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        return self.facade_result(action, result)

    def inventory_transfer(
        self,
        mode: Literal[
            "character_to_character",
            "party_to_character",
            "character_to_party",
            "character_to_ground",
            "ground_to_character",
        ],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Transfer inventory with the revision contract required by every affected owner."""
        data = self.facade_payload(payload)
        if mode in {"character_to_ground", "ground_to_character"}:
            shared = {
                "campaign_id",
                "character_id",
                "expected_campaign_revision",
                "expected_character_revision",
            }
            specific = (
                {"ground_id", "slot", "spatial_facts"} if mode == "ground_to_character" else set()
            )
            if set(data) - shared - specific or shared - set(data):
                raise ValueError(
                    "ground transfer requires exact campaign, character and revision fields"
                )
            return self.facade_result(
                mode,
                self.ground_inventory_settlement(
                    data["campaign_id"],
                    data["character_id"],
                    "pickup_ground" if mode == "ground_to_character" else "drop_held",
                    {key: value for key, value in data.items() if key in specific},
                    principal_id=principal_id,
                    expected_revision=data["expected_campaign_revision"],
                    expected_character_revision=data["expected_character_revision"],
                    idempotency_key=idempotency_key,
                    in_combat=False,
                ),
            )
        if mode == "character_to_character":
            result = self.character_inventory_transfer(
                self.required(data, "source_character_id"),
                self.required(data, "target_character_id"),
                self.required(data, "item_id"),
                data.get("quantity"),
                principal_id,
                self.required(data, "expected_campaign_revision"),
                self.required(data, "expected_source_revision"),
                self.required(data, "expected_target_revision"),
                idempotency_key,
            )
        else:
            direction = "withdraw" if mode == "party_to_character" else "deposit"
            result = self.party_inventory_transfer(
                self.required(data, "campaign_id"),
                self.required(data, "character_id"),
                self.required(data, "item_id"),
                direction,
                data.get("quantity"),
                principal_id,
                self.required(data, "expected_campaign_revision"),
                self.required(data, "expected_character_revision"),
                idempotency_key,
            )
        return self.facade_result(mode, result)

    def wallet_change(
        self,
        owner: Literal["character", "party"],
        action: Literal["adjust", "transfer_to_character", "transfer_from_character"],
        owner_id: str,
        denomination: str,
        amount: int,
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Adjust a wallet or transfer money through the party with all affected revisions.

        owner=party means owner_id is the campaign UUID, never the string party;
        owner=character means owner_id is the character UUID. adjust requires
        top-level expected_revision of that owner and idempotency_key. Use
        campaign_query(get/resume) or character_query(get) for a missing revision.
        Module-authored loot parcels use campaign_change(action="loot_acquire")
        with their exact source evidence rather than separate manual credits.
        Prefer payload={detail:"summary"} to retain wallet results and affected
        entity ids/revisions without full campaign history or character sheets.
        Omit detail or use "full" for the complete response. Detail changes only
        response projection; replaying the same idempotency key never pays twice.
        """
        data = self.facade_payload(payload)
        detail = data.get("detail", "full")
        if detail not in {"full", "summary"}:
            raise ValueError("wallet detail must be full or summary")
        if action == "adjust":
            result = (
                self.character_wallet_adjust(
                    owner_id, denomination, amount, principal_id, expected_revision, idempotency_key
                )
                if owner == "character"
                else self.party_wallet_adjust(
                    owner_id, denomination, amount, principal_id, expected_revision, idempotency_key
                )
            )
        else:
            if owner != "party":
                raise ValueError("wallet transfers use the party as owner")
            direction = "withdraw" if action == "transfer_to_character" else "deposit"
            result = self.party_wallet_transfer(
                owner_id,
                self.required(data, "character_id"),
                denomination,
                amount,
                direction,
                principal_id,
                self.required(data, "expected_campaign_revision"),
                self.required(data, "expected_character_revision"),
                idempotency_key,
            )
        if detail == "summary":
            result = dict(result)
            for key in ("campaign", "character"):
                entity = result.get(key)
                if isinstance(entity, dict):
                    result[key] = {
                        field: entity[field] for field in ("id", "revision") if field in entity
                    }
        return self.facade_result(action, result)
