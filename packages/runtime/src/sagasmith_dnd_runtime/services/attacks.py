"""Attacks application operations with explicit shared services."""

from __future__ import annotations

from typing import Any

from sagasmith_dnd.adventuring_gear import (
    ADVENTURING_GEAR_SOURCE_REF,
    normalize_gear_intent,
    resolve_adventuring_gear_intent,
)
from sagasmith_dnd.madness import damage_triggered_confusion_effect_ids

from .. import application_support as _support
from ..result_contracts import affected_state_slice
from . import protection
from .madness import settle_damage_triggered_confusion
from .sunlight import prepare_attack_action, prepare_context


def _gear_target_creature_type(sheet: dict[str, Any]) -> str:
    """Read a source-bound creature type from the target's authoritative sheet."""
    progression = dict(sheet.get("progression") or {})
    value = (
        sheet.get("creature_type")
        or progression.get("creature_type")
        or progression.get("species")
    )
    return " ".join(str(value or "").strip().casefold().split())


class AttacksService:
    def settle_attack_madness_damage(
        self,
        *,
        campaign_id: str,
        target: dict[str, Any],
        encounter: dict[str, Any],
        result: dict[str, Any],
        rules: Any,
        transaction_id: str,
    ) -> list[dict[str, Any]]:
        damage = dict(result.get("damage") or {})
        amount = int(damage.get("applied_amount", 0) or 0)
        if amount <= 0:
            return []
        sheet, events, receipts = settle_damage_triggered_confusion(
            actor=target,
            sheet=target["sheet"],
            damage_taken=amount,
            encounter=encounter,
            rules=rules,
            transaction_id=transaction_id,
        )
        target["sheet"] = sheet
        if events:
            result["madness_triggers"] = events
            result["rule_receipts"] = [*list(result.get("rule_receipts") or []), *receipts]
        return events

    def campaign_adventuring_gear_extinguish(
        self,
        campaign_id: str,
        action_id: str,
        item_id: str,
        source_ref: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        action_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Spend the burning target's action to resolve its source-bound Dex check."""
        self.access.require_campaign(campaign_id, principal_id)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if str(source_ref).strip() != ADVENTURING_GEAR_SOURCE_REF:
            raise _support.CombatEngineError(
                "gear source_ref must match bundled 2014 adventuring gear"
            )
        if action_context is not None:
            raise _support.CombatEngineError(
                "Alchemist's Fire extinguish accepts no caller check, DC, or outcome context"
            )
        normalized_action_id = str(action_id).strip()
        normalized_item_id = str(item_id).strip()
        normalized_actor_id = str(actor_id).strip()
        normalized_target_id = str(target_actor_id).strip()
        if not normalized_action_id or len(normalized_action_id) > 200:
            raise ValueError("action_id must contain 1 to 200 characters")
        if normalized_actor_id != normalized_target_id:
            raise _support.CombatEngineError(
                "a creature must spend its own action to extinguish its Alchemist's Fire"
            )
        request = {
            "action_id": normalized_action_id,
            "item_id": normalized_item_id,
            "intent": "extinguish",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "actor_id": normalized_actor_id,
            "target_actor_id": normalized_target_id,
            "expected_actor_revision": expected_actor_revision,
            "expected_target_revision": expected_target_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-gear-extinguish:{campaign_id}:{resolved_branch_id}:{principal_id}"
        payload = {
            "actor_id": normalized_actor_id,
            "target_id": normalized_target_id,
            "operation": "adventuring_gear.extinguish",
            "gear_action": request,
        }
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError("campaign revision conflict")
        actor = self.characters.get(normalized_actor_id)
        if actor.campaign_id != campaign_id:
            raise ValueError("gear user must belong to the campaign")
        if actor.revision != expected_actor_revision or actor.revision != expected_target_revision:
            raise ValueError("gear actor revision conflict")
        _, encounter = self.active_encounter(campaign_id)
        effect = next(
            (
                entry
                for entry in encounter.get("ongoing_effects", [])
                if isinstance(entry, dict)
                and entry.get("active", True)
                and entry.get("kind") == "adventuring_gear_burning"
                and str(entry.get("target_id") or "") == normalized_actor_id
                and str(entry.get("source_item_id") or "") == normalized_item_id
            ),
            None,
        )
        if effect is None:
            raise _support.CombatEngineError(
                "no active source-bound Alchemist's Fire effect matches this target and item"
            )
        prior_spend = next(
            (
                entry
                for entry in list(dict(campaign.state or {}).get("item_spends") or [])
                if isinstance(entry, dict)
                and str(entry.get("id") or "") == str(effect.get("action_id") or "")
            ),
            None,
        )
        prior_plan = dict(dict(prior_spend or {}).get("rule_plan") or {})
        if (
            not isinstance(prior_spend, dict)
            or str(prior_spend.get("item_id") or "") != normalized_item_id
            or str(prior_spend.get("source_ref") or "") != ADVENTURING_GEAR_SOURCE_REF
            or prior_plan.get("item_name") != "Alchemist's fire (flask)"
            or prior_plan.get("source_key") != effect.get("source_key")
        ):
            raise _support.CombatEngineError(
                "Alchemist's Fire effect lacks its exact originating source plan receipt"
            )
        plan = resolve_adventuring_gear_intent(
            {
                "id": normalized_item_id,
                "name": str(effect.get("item_name") or ""),
                "source_key": str(effect.get("source_key") or ""),
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            },
            "extinguish",
        )
        if plan.get("check") != {"ability": "dexterity", "dc": 10}:
            raise _support.CombatEngineError("source-defined extinguish check is incomplete")
        if _support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id,
                "combat.adventuring_gear.extinguish",
                {"idempotency_key": idempotency_key},
            ):
                return self.campaign_adventuring_gear_extinguish(
                    campaign_id,
                    normalized_action_id,
                    normalized_item_id,
                    ADVENTURING_GEAR_SOURCE_REF,
                    normalized_actor_id,
                    normalized_target_id,
                    expected_actor_revision,
                    expected_target_revision,
                    principal_id,
                    expected_revision,
                    resolved_branch_id,
                    idempotency_key,
                    None,
                )
        action_name = "utilize" if encounter.get("ruleset") == "2024" else "improvise"
        next_encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=normalized_actor_id,
            action=action_name,
            payload={"gear_action_id": normalized_action_id},
        )
        next_effect = next(
            entry
            for entry in next_encounter.get("ongoing_effects", [])
            if isinstance(entry, dict) and str(entry.get("id") or "") == str(effect.get("id") or "")
        )
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_id": normalized_actor_id,
                "kind": "ability_check",
                "ability": "dexterity",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "gear_action_id": normalized_action_id,
            },
        )
        actor_snapshot = self.combat_actor_snapshot(normalized_actor_id)
        check = _support.resolve_actor_check(
            actor_snapshot,
            kind="ability",
            ability="dexterity",
            dc=10,
            encounter=next_encounter,
            rules=rules,
            ruleset="2014",
            rng=_support.active_random_stream(),
        )
        if check.get("success"):
            next_effect["active"] = False
            next_effect["resolution"] = {
                "kind": "extinguished",
                "action_id": normalized_action_id,
                "check": _support.deepcopy(check),
            }
        next_state = _support.deepcopy(dict(campaign.state or {}))
        next_state["combat"] = next_encounter
        response_body = {
            "status": "committed",
            "action_id": normalized_action_id,
            "item_id": normalized_item_id,
            "intent": "extinguish",
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "rule_plan": _support.deepcopy(plan),
            "check": check,
            "burning": {key: value for key, value in next_effect.items() if key != "changes"},
            "combat": next_encounter,
            "campaign_revision": campaign.revision + 1,
        }

        def response(revisions: list[Any]) -> dict[str, Any]:
            result = {**response_body, "revisions": [_support.asdict(item) for item in revisions]}
            stream = _support.active_random_stream()
            if stream is not None and stream.draw_count > 0:
                result["random_stream_receipt"] = stream.receipt()
            return result

        revisions = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            expected_campaign_revision=campaign.revision,
            operation="combat.adventuring_gear.extinguish",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response,
            ),
        )
        return self.combat_response(campaign_id, principal_id, response(list(revisions or [])))

    def campaign_adventuring_gear_attack(
        self,
        campaign_id: str,
        action_id: str,
        item_id: str,
        intent: str,
        source_ref: str,
        actor_id: str,
        target_actor_id: str,
        expected_actor_revision: int,
        expected_target_revision: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        action_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Route an exact gear attack through the combat transaction with its item dose.

        Acid is the first supported intent: its source effect is immediate damage,
        so the normal combat engine can settle attack, damage, turn cost, dose,
        actor revisions, random receipt, and idempotency in one mutation.
        """
        self.access.require_campaign(campaign_id, principal_id)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if str(source_ref).strip() != ADVENTURING_GEAR_SOURCE_REF:
            raise _support.CombatEngineError(
                "gear source_ref must match bundled 2014 adventuring gear"
            )
        if not isinstance(action_context, (dict, type(None))):
            raise _support.CombatEngineError("action_context must be an object")
        owner_id = str(actor_id).strip()
        target_id = str(target_actor_id).strip()
        action_id_value = str(action_id).strip()
        item_id_value = str(item_id).strip()
        normalized_intent = normalize_gear_intent(intent)
        action_payload = self.sanitize_attack_action(
            campaign_id,
            principal_id,
            {"weapon_id": item_id_value, "context": _support.deepcopy(action_context or {})},
        )
        request = {
            "action_id": action_id_value,
            "item_id": item_id_value,
            "intent": normalized_intent,
            "source_ref": ADVENTURING_GEAR_SOURCE_REF,
            "actor_id": owner_id,
            "target_actor_id": target_id,
            "expected_actor_revision": expected_actor_revision,
            "expected_target_revision": expected_target_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-gear-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay_payload = {
            "actor_id": owner_id,
            "target_id": target_id,
            "action": _support.deepcopy(action_payload),
            "cantrip_spell_id": "",
            "spell_resolution_id": "",
            "deflect_attack": None,
            "branch_id": resolved_branch_id,
            "gear_action": request,
        }
        replay = self.replay_idempotent(scope, idempotency_key, replay_payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)

        owner = self.characters.get(owner_id)
        target = self.characters.get(target_id)
        if owner.campaign_id != campaign_id or target.campaign_id != campaign_id:
            raise ValueError("gear attacker and target must belong to the campaign")
        if owner.revision != expected_actor_revision:
            raise ValueError(
                "actor revision conflict: "
                f"expected {expected_actor_revision}, found {owner.revision}"
            )
        if target.revision != expected_target_revision:
            raise ValueError(
                "target revision conflict: "
                f"expected {expected_target_revision}, found {target.revision}"
            )
        source_item = next(
            (
                item
                for item in dict(owner.sheet.get("inventory") or {}).get("items", [])
                if isinstance(item, dict)
                and str(item.get("id") or "") == str(item_id).strip()
            ),
            None,
        )
        if source_item is None:
            raise ValueError("adventuring gear item is absent from the attacker's inventory")
        plan = resolve_adventuring_gear_intent(
            {**source_item, "source_ref": ADVENTURING_GEAR_SOURCE_REF}, intent
        )
        item_name = str(source_item.get("name") or "").strip().casefold()
        supported_attack = (
            item_name == "acid (vial)" and plan["intent"] in {"splash", "throw"}
        ) or (
            item_name == "alchemist's fire (flask)" and plan["intent"] == "throw"
        ) or (
            item_name == "holy water (flask)" and plan["intent"] in {"splash", "throw"}
        )
        if not supported_attack or plan.get("attack") != "ranged_improvised":
            raise _support.CombatEngineError(
                "combat gear attacks support Acid, Alchemist's Fire, and Holy Water source intents"
            )
        if item_name == "holy water (flask)":
            target_type = _gear_target_creature_type(
                _support.validate_character_sheet(target.sheet)
            )
            if not any(
                token.strip(" ,.;:-()") in {"fiend", "undead"}
                for token in target_type.split()
            ):
                raise _support.CombatEngineError(
                    "Holy Water damage requires authoritative target type fiend or undead"
                )
        if not action_id_value or len(action_id_value) > 200:
            raise ValueError("action_id must contain 1 to 200 characters")
        return self._settle_combat_attack(
            campaign_id=campaign_id,
            actor_id=owner.id,
            target_id=target.id,
            action=action_payload,
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            gear_action={
                "request": request,
                "rule_plan": plan,
            },
        )

    def sanitize_attack_action(
        self, campaign_id: str, principal_id: str, action: dict[str, Any]
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        value = dict(action)
        if "use_weapon_mastery" in value and not isinstance(value["use_weapon_mastery"], bool):
            raise _support.CombatEngineError("use_weapon_mastery must be boolean")
        if "attack_ability" in value:
            attack_ability = value["attack_ability"]
            if not isinstance(attack_ability, str) or not attack_ability.strip():
                raise _support.CombatEngineError("attack_ability must be a non-empty ability name")
            value["attack_ability"] = attack_ability.strip().casefold()
        for field, allowed in (
            ("light_extra_attack", {"bonus_action", "nick"}),
            ("weapon_mastery_followup", {"cleave"}),
        ):
            if field not in value:
                continue
            normalized = str(value[field] or "").strip().casefold()
            if normalized not in allowed:
                raise _support.CombatEngineError(
                    f"{field} must be one of: {', '.join(sorted(allowed))}"
                )
            value[field] = normalized
        if "mastery_secondary_target_id" in value:
            secondary_target_id = str(value["mastery_secondary_target_id"] or "").strip()
            if not secondary_target_id:
                raise _support.CombatEngineError("mastery_secondary_target_id must be non-empty")
            value["mastery_secondary_target_id"] = secondary_target_id
        if membership.role not in _support.CAMPAIGN_DM_ROLES:
            # Tactical context (cover, advantage, concealment and reach) is a
            # scene/DM fact, not a client-controlled modifier.
            sunlight = dict(value.get("context") or {}).get("sunlight")
            if sunlight is not None and (
                not isinstance(sunlight, dict) or set(sunlight) != {"receipt"}
            ):
                raise PermissionError("player sunlight context requires a signed DM receipt")
            value["context"] = {"sunlight": sunlight} if sunlight is not None else {}
            value["rulings"] = [
                item
                for item in value.get("rulings", [])
                if isinstance(item, dict) and item.get("source") == "dm_ruling"
            ]
        return value

    def validate_agent_attack_context(
        self,
        campaign_id: str,
        principal_id: str,
        action: dict[str, Any],
        *,
        encounter: dict[str, Any],
    ) -> None:
        """Validate source rulings on a grid or dynamic spatial facts in Agent mode."""

        context = action.get("context")
        if context is None:
            return
        if not isinstance(context, dict):
            raise _support.CombatEngineError("attack context must be an object")
        positioning_mode = str(encounter.get("positioning_mode") or "grid")
        raw_spatial_facts = context.get("spatial_facts")
        if positioning_mode == "agent":
            membership = self.access.require_campaign(campaign_id, principal_id)
            if membership.role not in _support.CAMPAIGN_DM_ROLES:
                raise PermissionError("Agent scene facts require an authorized DM")
            if not isinstance(raw_spatial_facts, dict):
                raise _support.NeedsRulingError(
                    "agent-positioned attacks require an Agent spatial decision",
                    missing=("attack.spatial_facts",),
                    ruling_kind="agent_dm_adjudication",
                )
            vision_fields = (
                {"attacker_vision", "target_vision"}
                if encounter.get("ruleset") == "2014"
                else {"attacker_can_see_target", "target_can_see_attacker"}
            )
            allowed_spatial_fields = {
                "decision_id",
                "reason",
                "targetable",
                "in_range",
                "long_range",
                "cover_degree",
                *vision_fields,
                "target_within_5_ft",
                "attacker_can_hear_target",
                "target_within_10_ft",
                "close_threat_actor_ids",
                "helper_actor_ids",
                "target_adjacent_ally_actor_ids",
                "cleave_secondary_eligible",
            }
            required_spatial_fields = {
                "decision_id",
                "reason",
                "targetable",
                "in_range",
                "cover_degree",
                *vision_fields,
            }
            unknown = set(raw_spatial_facts) - allowed_spatial_fields
            missing = required_spatial_fields - set(raw_spatial_facts)
            decision_id = str(raw_spatial_facts.get("decision_id") or "").strip()
            reason = " ".join(str(raw_spatial_facts.get("reason") or "").split())
            if unknown or missing or not decision_id or not reason:
                raise _support.CombatEngineError(
                    "Agent spatial facts require one decision_id, reason, and the attack "
                    "facts needed to determine targetability; "
                    f"missing fields: {', '.join(sorted(missing)) or 'none'}; "
                    f"unsupported fields: {', '.join(sorted(unknown)) or 'none'}"
                )
            for field in {"targetable", "in_range"}:
                if not isinstance(raw_spatial_facts.get(field), bool):
                    raise _support.CombatEngineError(f"Agent spatial fact {field} must be boolean")
            if encounter.get("ruleset") == "2014":
                for field in ("attacker_vision", "target_vision"):
                    _support.resolve_agent_vision_2014({}, raw_spatial_facts[field])
            else:
                for field in ("attacker_can_see_target", "target_can_see_attacker"):
                    if not isinstance(raw_spatial_facts[field], bool):
                        raise _support.CombatEngineError(
                            f"Agent spatial fact {field} must be boolean"
                        )
            for field in {"long_range", "target_within_5_ft"}:
                if field in raw_spatial_facts and not isinstance(raw_spatial_facts[field], bool):
                    raise _support.CombatEngineError(f"Agent spatial fact {field} must be boolean")
            for field in {"attacker_can_hear_target", "target_within_10_ft"}:
                if field in raw_spatial_facts and not isinstance(raw_spatial_facts[field], bool):
                    raise _support.CombatEngineError(
                        f"Agent spatial fact {field} must be boolean"
                    )
            if "cleave_secondary_eligible" in raw_spatial_facts and not isinstance(
                raw_spatial_facts["cleave_secondary_eligible"], bool
            ):
                raise _support.CombatEngineError(
                    "Agent spatial fact cleave_secondary_eligible must be boolean"
                )
            cover_degree = (
                str(raw_spatial_facts.get("cover_degree") or "")
                .strip()
                .casefold()
                .replace("-", "_")
            )
            if cover_degree not in {"none", "half", "three_quarters", "total"}:
                raise _support.CombatEngineError(
                    "Agent spatial fact cover_degree must be none, half, three_quarters, or total"
                )
            participant_ids = {
                str(item.get("actor_id") or "") for item in encounter.get("combatants", [])
            }
            normalized_lists: dict[str, list[str]] = {}
            for field in {
                "close_threat_actor_ids",
                "helper_actor_ids",
                "target_adjacent_ally_actor_ids",
            }:
                raw_ids = raw_spatial_facts.get(field, [])
                if (
                    not isinstance(raw_ids, list)
                    or any(not isinstance(item, str) for item in raw_ids)
                    or len(set(raw_ids)) != len(raw_ids)
                    or set(raw_ids) - participant_ids
                ):
                    raise _support.CombatEngineError(
                        f"Agent spatial fact {field} must contain unique current combatant IDs"
                    )
                normalized_lists[field] = list(raw_ids)
            context["spatial_facts"] = {
                **dict(raw_spatial_facts),
                **normalized_lists,
                "decision_id": decision_id,
                "reason": reason,
                "cover_degree": cover_degree,
                "long_range": bool(raw_spatial_facts.get("long_range", False)),
                "cleave_secondary_eligible": bool(
                    raw_spatial_facts.get("cleave_secondary_eligible", False)
                ),
            }
            action["context"] = context
            return
        if raw_spatial_facts is not None:
            raise _support.CombatEngineError(
                "grid attacks derive spatial facts from encounter coordinates"
            )
        cover = context.get("cover")
        if cover is not None:
            if not isinstance(cover, dict) or set(cover) != {"degree"}:
                raise _support.CombatEngineError(
                    "attack cover context accepts only one rules-defined degree"
                )
            degree = str(cover.get("degree") or "").strip().casefold().replace("-", "_")
            if degree not in {"half", "three_quarters", "total"}:
                raise _support.CombatEngineError(
                    "Agent cover degree must be half, three_quarters, or total"
                )
            if not isinstance(context.get("agent_ruling"), dict):
                raise _support.CombatEngineError(
                    "Agent cover context requires an exact source-bound Agent ruling"
                )
        raw_ruling = context.get("agent_ruling")
        if raw_ruling is None:
            return
        self.validate_current_scene_agent_ruling(
            campaign_id,
            raw_ruling,
            encounter=encounter,
            field="attack context",
            allowed_ruling_kinds={
                "agent_dm_adjudication",
                "source_or_scene_fact",
            },
        )

    def add_attack_on_hit_window(
        self,
        encounter: dict[str, Any],
        *,
        result: dict[str, Any],
        attacker_id: str,
        target_id: str,
        weapon_id: str,
    ) -> dict[str, Any] | None:
        on_hit_ruling = dict(result.get("on_hit_ruling") or {})
        effect = str(on_hit_ruling.get("effect") or "").strip()
        if not result.get("hit") or not effect:
            return None
        next_value = _support.add_choice_window(
            encounter,
            kind="ruling",
            actor_id_value=target_id,
            event="attack.on_hit.effect",
            candidates=[
                {
                    "id": "execute_plan",
                    "name": "Compile, persist, and execute a source-bound plan",
                },
                {
                    "id": "dismiss",
                    "name": "Agent confirms that no structured effect applies",
                },
            ],
        )
        encounter.clear()
        encounter.update(next_value)
        window = encounter["pending"][-1]
        window.update(
            trigger="attack_on_hit_effect",
            attacker_id=attacker_id,
            target_id=target_id,
            weapon_id=weapon_id,
            effect=effect,
        )
        result["pending_on_hit_ruling_id"] = window["id"]
        return window

    def consume_next_attack_advantage(
        self,
        encounter: dict[str, Any],
        plan: dict[str, Any],
        *,
        attacker_id: str,
        target_id: str,
    ) -> str | None:
        effect_id = str(plan.get("next_attack_advantage_effect_id") or "")
        if not effect_id:
            return None
        effect = next(
            (
                item
                for item in encounter.get("ongoing_effects", [])
                if isinstance(item, dict)
                and str(item.get("id") or "") == effect_id
                and item.get("active", True)
                and item.get("kind") == "next_attack_advantage"
                and str(item.get("target_id") or "") == target_id
            ),
            None,
        )
        if effect is None:
            raise _support.CombatEngineError(
                "next-attack advantage plan does not match an active target effect"
            )
        effect["active"] = False
        effect["resolution"] = {
            "kind": "attack_roll",
            "attacker_id": attacker_id,
            "target_id": target_id,
        }
        return effect_id

    def expire_next_attack_advantage(
        self,
        encounter: dict[str, Any],
        *,
        actor_id: str,
        ended_round: int,
    ) -> list[str]:
        expired: list[str] = []
        for effect in encounter.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("kind") == "next_attack_advantage"
                and not (
                    effect.get("mechanic_id") == _support.SPELL_RESOLUTION_MECHANIC_ID
                    and bool(effect.get("standard_on_hit_mechanic"))
                )
                and str(effect.get("expires_on_actor_id") or "") == actor_id
                and ended_round >= int(effect.get("expires_on_round", 0) or 0)
            ):
                effect["active"] = False
                effect["resolution"] = {
                    "kind": "duration_expired",
                    "actor_id": actor_id,
                    "round": ended_round,
                }
                expired.append(str(effect.get("id") or ""))
        return expired

    def reveal_attacker_to_target(
        self, encounter: dict[str, Any], attacker_id: str, target_id: str
    ) -> None:
        """Reveal a hidden attacker to the creature it attacked, hit or miss."""
        attacker = next(
            item for item in encounter.get("combatants", []) if item.get("actor_id") == attacker_id
        )
        attacker["hidden"] = False
        current_visibility = attacker.get("visible_to_actor_ids")
        if current_visibility is None:
            return
        visible_to = {str(item) for item in list(current_visibility or [])} | {
            attacker_id,
            target_id,
        }
        participant_ids = {str(item.get("actor_id")) for item in encounter.get("combatants", [])}
        attacker["visible_to_actor_ids"] = (
            None if participant_ids <= visible_to else sorted(visible_to)
        )

    def post_hit_attack_defenses(
        self,
        campaign_id: str,
        target: dict[str, Any],
        *,
        plan: dict[str, Any],
        attack: dict[str, Any],
        encounter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Combine card activities with legal source-bound spell reactions."""
        spell_options: list[dict[str, Any]] = []
        for discovered in _support.available_shield_attack_defenses(target["sheet"]):
            spell_id = str(discovered.get("spell_id") or discovered.get("id") or "")
            candidate = next(
                (
                    item
                    for item in _support.available_shield_attack_defenses(
                        target["sheet"],
                        rules=self.effective_rule_context(
                            campaign_id,
                            facts={
                                "actor_id": str(plan["target_id"]),
                                "spell_id": spell_id,
                                "kind": "spell",
                            },
                        ),
                    )
                    if str(item.get("id") or "") == str(discovered.get("id") or "")
                ),
                None,
            )
            if candidate is None:
                continue
            legal_casts = []
            for option in candidate.get("cast_options", []):
                payment = dict(option.get("payment") or {})
                try:
                    self.require_combat_spell_turn_legal(
                        encounter,
                        actor_id=str(plan["target_id"]),
                        payment="reaction",
                        spell_level=1,
                        casting_time="reaction",
                        spent_slot=payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
                    )
                except _support.CombatEngineError:
                    continue
                legal_casts.append(_support.deepcopy(option))
            if legal_casts:
                spell_options.append(
                    {
                        **candidate,
                        "cast_levels": [int(item["cast_level"]) for item in legal_casts],
                        "cast_options": legal_casts,
                    }
                )
        song_defense_id = _support.SCAG_RULE_PACK_ID + ".feature.song-of-defense"
        song_feature = next(
            (
                item
                for item in target["sheet"].get("content", {}).get("features", [])
                if str(item.get("id") or "") == song_defense_id
                and str(item.get("pack_id") or "") == _support.SCAG_RULE_PACK_ID
            ),
            None,
        )
        active_bladesong = any(
            effect.get("active")
            and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
            for effect in target["sheet"].get("effects", [])
        )
        target_combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if str(item.get("actor_id") or "") == str(plan["target_id"])
            ),
            None,
        )
        reaction_available = (
            target_combatant is not None
            and int(dict(target_combatant.get("turn_budget") or {}).get("reaction", 0) or 0) > 0
        )
        if (
            song_feature is not None
            and active_bladesong
            and reaction_available
            and str(plan.get("damage_expression") or "").strip()
        ):
            cast_options = []
            for raw_level, slot in dict(
                target["sheet"].get("spellcasting", {}).get("spell_slots") or {}
            ).items():
                if not str(raw_level).isdigit() or int(raw_level) < 1 or int(raw_level) > 9:
                    continue
                slot_data = dict(slot or {})
                if int(slot_data.get("value", 0) or 0) > 0:
                    level = int(raw_level)
                    cast_options.append(
                        {
                            "cast_level": level,
                            "reduction": 5 * level,
                            "payment": {"economy": "spell_slot", "slot_level": level},
                        }
                    )
            if cast_options:
                spell_options.append(
                    {
                        "id": song_defense_id,
                        "name": "Song of Defense",
                        "kind": "scag_song_defense",
                        "cast_levels": [item["cast_level"] for item in cast_options],
                        "cast_options": cast_options,
                        "source_type": "scag_feature",
                    }
                )
        return _support.available_attack_defenses(
            target,
            plan=plan,
            attack=attack,
            encounter=encounter,
            extra_defenses=spell_options,
        )

    def combat_preflight_attack(
        self,
        campaign_id: str,
        actor_id: str,
        target_id: str,
        action: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Validate an attack without rolling, paying resources or changing state.

        action uses weapon_id from the actor's weapon attacks. In Agent positioning,
        put spatial facts in action.context.spatial_facts; see combat_resolve_attack
        for required fields. Reuse this action for resolution if state is unchanged.
        """
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id
        )
        campaign, encounter = self.active_encounter(campaign_id)
        self.require_mounted_action(encounter, actor_id, "attack")
        resolved_branch_id = self.require_current_branch(campaign_id, None)
        self.require_campaign_actor(campaign_id, target_id)
        action = self.sanitize_attack_action(campaign_id, principal_id, dict(action or {}))
        deflect_declaration = action.pop("deflect_attack", None)
        self.validate_agent_attack_context(
            campaign_id,
            principal_id,
            action,
            encounter=encounter,
        )
        prepared_deflect = self._prepare_steel_defender_deflect(
            campaign_id,
            resolved_branch_id,
            principal_id,
            actor_id,
            target_id,
            deflect_declaration,
            encounter,
        )
        try:
            attacker = self.combat_actor_snapshot(actor_id)
            action = prepare_attack_action(
                self, action, campaign_id=campaign_id, actor_id=actor_id,
                target_id=target_id, principal_id=principal_id, encounter=encounter,
            )
            plan = _support.preflight_attack(
                attacker,
                self.combat_actor_snapshot(target_id),
                action={**action, "target_id": target_id},
                encounter=encounter,
                rules=self.effective_rule_context(
                    campaign_id,
                    facts={"actor_id": actor_id, "target_id": target_id, "kind": "attack"},
                ),
            )
            _support.pay_attack_action(
                encounter,
                attacker,
                weapon_id=str(plan.get("weapon_id") or ""),
                attack_mode=str(plan.get("attack_mode") or "melee"),
                multiattack_option_id=action.get("multiattack_option_id"),
                target_id=target_id,
                light_extra_attack=action.get("light_extra_attack"),
                weapon_mastery_followup=action.get("weapon_mastery_followup"),
            )
            if prepared_deflect is not None:
                plan = _support.apply_deflect_attack_to_plan(
                    plan,
                    defender_id=str(prepared_deflect["eligibility"]["defender_id"]),
                )
        except _support.NeedsRulingError as error:
            if (
                self.access.require_campaign(campaign_id, principal_id).role
                not in _support.CAMPAIGN_DM_ROLES
            ):
                raise _support.CombatEngineError(
                    "attack requires Agent-as-DM adjudication"
                ) from None
            weapon_id = str(action.get("weapon_id") or "")
            missing = {str(item) for item in error.missing}
            if weapon_id and f"weapon.range:{weapon_id}" in missing:
                actor = self.combat_actor_snapshot(actor_id)
                item = next(
                    (
                        value
                        for value in dict(actor.get("sheet") or {})
                        .get("inventory", {})
                        .get("items", [])
                        if isinstance(value, dict) and str(value.get("id") or "") == weapon_id
                    ),
                    None,
                )
                if isinstance(item, dict) and _support._has_source_defined_positional_targeting(
                    item.get("description")
                ):
                    raise _support.NeedsRulingError(
                        (
                            f"{str(item.get('name') or weapon_id)} uses a source-defined "
                            "positional target restriction"
                        ),
                        missing=[f"weapon.targeting:{weapon_id}"],
                        ruling_kind="agent_dm_adjudication",
                    ) from error
            raise
        # Mechanical details are for the commit path and DM audit only.  A
        # player receives an opaque plan token and the legal action metadata,
        # never target AC, attack bonus, or damage formulas.
        membership = self.access.require_campaign(campaign_id, principal_id)
        if membership.role not in _support.CAMPAIGN_DM_ROLES:
            return {
                "status": plan["status"],
                "kind": plan["kind"],
                "attacker_id": plan["attacker_id"],
                "target_id": plan["target_id"],
                "weapon_id": plan.get("weapon_id"),
                "opaque": True,
            }
        if dict(action.get("context") or {}).get("sunlight") is not None:
            plan["sunlight_context"] = {
                "receipt": action["context"]["sunlight"]["receipt"]
            }
        return plan

    def combat_resolve_attack(
        self,
        campaign_id: str,
        actor_id: str,
        target_id: str,
        action: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one attack and atomically update actors and encounter.

        Use the active actor and campaign revision from the latest receipt, plus
        idempotency_key. action={weapon_id, context?}; use an owned weapon ID.
        Agent positioning requires action.context.spatial_facts={decision_id,
        reason, targetable, in_range, cover_degree, attacker_vision,
        target_vision}. For 2014, each vision object contains scene observations:
        distance_ft, illumination, obscuration, magical_darkness, opaque_boundary,
        scene_ref, and scene_excerpt. The engine derives sight from these and the
        actor's senses. Optional facts: long_range, target_within_5_ft,
        close_threat_actor_ids, helper_actor_ids, target_adjacent_ally_actor_ids,
        cleave_secondary_eligible. Ground them in the current scene, never invent
        coordinates to bypass a missing spatial decision. Grid mode uses its map.
        Underwater ranged weapon attacks require an explicit long_range boolean;
        true automatically misses, while false still applies weapon exceptions.
        use_great_weapon_fighting=true opts into one reroll of each qualifying
        weapon die showing 1/2. Protection returns pending_reaction before the
        roll: resolve each owner's combat_choice, then repeat the same attack
        with a new operation ID. Agent context.protection requires decision_id,
        reason, actors=[{actor_id,within_5_ft,can_see_attacker}] for every available
        shield bearer. Failed/unknown calls always retry their original ID.
        """
        return self._settle_combat_attack(
            campaign_id=campaign_id,
            actor_id=actor_id,
            target_id=target_id,
            action=action,
            principal_id=principal_id,
            branch_id=branch_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def _settle_combat_attack(
        self,
        campaign_id: str,
        actor_id: str,
        target_id: str,
        action: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        *,
        spell_release: dict[str, Any] | None = None,
        gear_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Settle an ordinary attack or a verified paid spell continuation atomically."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if actor_id == target_id:
            raise _support.CombatEngineError("an actor cannot attack itself")
        campaign = self.campaigns.get(campaign_id)
        action_payload = self.sanitize_attack_action(
            campaign_id, principal_id, _support.deepcopy(action or {})
        )
        spell_resolution_id = str(action_payload.pop("spell_resolution_id", "") or "")
        cantrip_spell_id = str(action_payload.get("cantrip_spell_id") or "").strip()
        if cantrip_spell_id:
            action_payload.pop("cantrip_spell_id", None)
        deflect_declaration = action_payload.pop("deflect_attack", None)
        payload = {
            "actor_id": actor_id,
            "target_id": target_id,
            # Spatial validation fills omitted optional facts with their engine
            # defaults.  Keep the idempotency request bound to the caller's
            # normalized input instead of mutating it after the hash is bound.
            "action": _support.deepcopy(action_payload),
            "cantrip_spell_id": cantrip_spell_id,
            "spell_resolution_id": spell_resolution_id,
            "deflect_attack": _support.deepcopy(deflect_declaration),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
        if gear_action is not None:
            scope = f"campaign-gear-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
            payload["gear_action"] = _support.deepcopy(gear_action["request"])
        protection_binding = {
            "kind": "combat_attack", "actor_id": actor_id, "target_id": target_id,
            "payload": _support.deepcopy(payload),
        }
        release_fields = {}
        if spell_release:
            scope, payload = spell_release["scope"], spell_release["payload"]
            release_fields = spell_release["fields"]
        replay = None if spell_release else self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if gear_action is not None and any(
            str(dict(item).get("id") or "")
            == str(gear_action["request"].get("action_id") or "")
            for item in list(dict(campaign.state or {}).get("item_spends") or [])
            if isinstance(item, dict)
        ):
            raise ValueError("gear action_id already exists on this branch")
        resolution_id = (
            "resolution-"
            + _support.hashlib.sha256(
                (
                    f"{campaign_id}:{resolved_branch_id}:combat.attack:"
                    f"{idempotency_key}:{actor_id}:{target_id}"
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        _, encounter = self.active_encounter(campaign_id)
        self.require_mounted_action(encounter, actor_id, "attack")
        if spell_release:
            encounter = spell_release["encounter"]
        encounter, protection_accepted = protection.resume(encounter, protection_binding)
        self.validate_agent_attack_context(
            campaign_id,
            principal_id,
            action_payload,
            encounter=encounter,
        )
        spell_resolution: dict[str, Any] | None = None
        if spell_resolution_id:
            spell_resolution = _support.deepcopy(
                dict(dict(encounter.get("spell_resolutions") or {}).get(spell_resolution_id) or {})
            )
            if (
                spell_resolution.get("kind") != "spell_attack"
                or str(spell_resolution.get("caster_id") or "") != actor_id
                or int(spell_resolution.get("remaining_attacks", 0) or 0) < 1
            ):
                raise _support.CombatEngineError(
                    "spell_resolution_id is not an active spell attack for this actor"
                )
            blocking = [
                item
                for item in encounter.get("pending", [])
                if item.get("status", "pending") == "pending"
                and str(item.get("id") or "") != spell_resolution_id
            ]
            if blocking:
                raise _support.CombatEngineError(
                    "resolve the pending save or choice before this attack"
                )
            if spell_resolution.get("readied_id"):
                index = int(spell_resolution["total_attacks"]) - int(
                    spell_resolution["remaining_attacks"]
                )
                stored = spell_resolution["attacks"][index]
                stored_action = {"context": _support.deepcopy(stored.get("context") or {})}
                self.validate_agent_attack_context(
                    campaign_id,
                    principal_id,
                    stored_action,
                    encounter=encounter,
                )
                # Source targets and all original attack semantics stay fixed.
                # Illumination is a newly authorized, expiring scene fact.
                comparison = _support.deepcopy(action_payload)
                comparison.setdefault("context", {}).pop("sunlight", None)
                stored_comparison = _support.deepcopy(stored_action)
                stored_comparison["context"].pop("sunlight", None)
                if (
                    target_id != stored["target_id"]
                    or comparison != stored_comparison
                    or cantrip_spell_id
                    or deflect_declaration
                ):
                    raise _support.CombatEngineError(
                        "readied spell attack must use its stored target and context"
                    )
            if str(spell_resolution.get("spell_id") or "") == "":
                raise _support.CombatEngineError("spell attack resolution has no source spell")
        else:
            self.require_no_blocking_pending(encounter)
        rule_context = self.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": actor_id,
                "target_id": target_id,
                "kind": "spell_attack" if spell_resolution is not None else "attack",
                "spell_id": (
                    str(spell_resolution.get("spell_id") or "")
                    if spell_resolution is not None
                    else ""
                ),
            },
        )
        attacker_record = self.require_campaign_actor(campaign_id, actor_id)
        target_record = self.require_campaign_actor(campaign_id, target_id)
        if gear_action is not None:
            if attacker_record.revision != int(
                gear_action["request"].get("expected_actor_revision", -1)
            ):
                raise ValueError("gear attacker character revision conflict")
            if target_record.revision != int(
                gear_action["request"].get("expected_target_revision", -1)
            ):
                raise ValueError("gear target character revision conflict")
        action_payload = prepare_attack_action(
            self, action_payload, campaign_id=campaign_id, actor_id=actor_id,
            target_id=target_id, principal_id=principal_id, encounter=encounter,
        )
        attacker = self.character_view(attacker_record, rules_context=rule_context)
        target = self.character_view(target_record, rules_context=rule_context)
        if gear_action is not None:
            gear_plan = dict(gear_action.get("rule_plan") or {})
            gear_item_id = str(gear_action["request"].get("item_id") or "")
            if gear_plan.get("attack") != "ranged_improvised":
                raise _support.CombatEngineError("gear attack plan has unsupported attack type")
            abilities = dict(attacker.get("derived", {}).get("ability_modifiers") or {})
            attack_modifier = int(abilities.get("strength", 0) or 0)
            maximum_range = gear_plan.get("maximum_range_feet")
            gear_effect = dict(gear_plan.get("effect") or {})
            damage_expression = str(
                gear_effect.get("damage") or gear_effect.get("hit_damage") or ""
            )
            damage_type = str(
                gear_effect.get("damage_type") or gear_effect.get("hit_damage_type") or ""
            )
            if (
                not gear_item_id
                or not damage_expression
                or not damage_type
                or isinstance(maximum_range, bool)
                or not isinstance(maximum_range, int)
                or maximum_range < 1
            ):
                raise _support.CombatEngineError("gear attack plan is incomplete")
            derived_inventory = attacker.setdefault("derived", {}).setdefault("inventory", {})
            derived_inventory.setdefault("weapon_attacks", []).append(
                {
                    "item_id": gear_item_id,
                    "name": str(gear_plan.get("item_name") or "Adventuring Gear"),
                    "attack_type": "ranged",
                    "attack_ability": "strength",
                    "attack_ability_modifier": attack_modifier,
                    "attack_bonus": attack_modifier,
                    "normal_range_ft": maximum_range,
                    "long_range_ft": maximum_range,
                    "damage_expression": damage_expression,
                    "damage_type": damage_type,
                    "properties": [],
                    "proficient": False,
                }
            )
        settled_attacker_sheet = (
            _support.deepcopy(spell_release["sheet"])
            if spell_release
            else _support.deepcopy(attacker["sheet"])
        )
        if spell_resolution and spell_resolution.get("readied_id"):
            from .readied_spells import bound_sheet

            attacker["sheet"] = bound_sheet(settled_attacker_sheet, spell_resolution["spell_card"])
        prepared_deflect = self._prepare_steel_defender_deflect(
            campaign_id,
            resolved_branch_id,
            principal_id,
            actor_id,
            target_id,
            deflect_declaration,
            encounter,
        )
        deflect_activity = (
            dict(prepared_deflect["activity"]) if prepared_deflect is not None else None
        )
        deflect_contract = (
            dict(prepared_deflect["contract"]) if prepared_deflect is not None else None
        )
        deflect_spatial_facts = (
            dict(prepared_deflect["spatial_facts"]) if prepared_deflect is not None else None
        )
        deflect_eligibility = (
            dict(prepared_deflect["eligibility"]) if prepared_deflect is not None else None
        )
        compiled_item_plan = None
        item_card: dict[str, Any] | None = None
        try:
            if spell_resolution is not None:
                plan = _support.preflight_spell_attack(
                    attacker,
                    target,
                    spell_id=str(spell_resolution["spell_id"]),
                    cast_level=int(spell_resolution["cast_level"]),
                    encounter=encounter,
                    context=dict(action_payload.get("context") or {}),
                    rules=rule_context,
                    allow_out_of_turn=bool(spell_resolution.get("readied_id")),
                )
            elif cantrip_spell_id:
                extra_attack_feature_id = _support.SCAG_RULE_PACK_ID + ".feature.extra-attack"
                extra_attack_feature = next(
                    (
                        item
                        for item in attacker_record.sheet.get("content", {}).get("features", [])
                        if str(item.get("id") or "") == extra_attack_feature_id
                        and str(item.get("pack_id") or "") == _support.SCAG_RULE_PACK_ID
                    ),
                    None,
                )
                if extra_attack_feature is None:
                    raise _support.CombatEngineError(
                        "cantrip substitution requires source-bound SCAG Extra Attack"
                    )
                # The 2014 SCAG wording grants two weapon attacks only.  The
                # cantrip replacement is a later Bladesinging revision and is
                # legal here only when the recorded source card explicitly
                # authorizes it.  Never infer that revision from the feature id.
                extra_attack_text = " ".join(
                    str(extra_attack_feature.get(key) or "")
                    for key in ("description", "effect", "source_excerpt")
                ).casefold()
                if not any(
                    phrase in extra_attack_text
                    for phrase in (
                        "replace one of the attacks",
                        "replace one attack",
                        "one of those attacks with a cantrip",
                        "one of the attacks with a cantrip",
                    )
                ):
                    raise _support.CombatEngineError(
                        "the recorded Extra Attack source does not authorize cantrip substitution"
                    )
                cantrip = next(
                    (
                        item
                        for item in attacker_record.sheet.get("content", {}).get("spells", [])
                        if str(item.get("id") or "") == cantrip_spell_id
                    ),
                    None,
                )
                if cantrip is None or int(cantrip.get("level", 0) or 0) != 0:
                    raise _support.CombatEngineError(
                        "Extra Attack substitution requires a recorded cantrip"
                    )
                plan = _support.preflight_spell_attack(
                    attacker,
                    target,
                    spell_id=cantrip_spell_id,
                    cast_level=0,
                    encounter=encounter,
                    context=dict(action_payload.get("context") or {}),
                    rules=rule_context,
                )
                plan["attack_mode"] = "cantrip"
                plan["cantrip_replacement"] = True
            else:
                plan = _support.preflight_attack(
                    attacker,
                    target,
                    action=action_payload,
                    encounter=encounter,
                    rules=rule_context,
                )
                weapon_id = str(plan.get("weapon_id") or "")
                item_card = next(
                    (
                        item
                        for item in dict(attacker_record.sheet.get("inventory") or {}).get(
                            "items", []
                        )
                        if isinstance(item, dict) and str(item.get("id") or "") == weapon_id
                    ),
                    None,
                )
                if isinstance(item_card, dict) and isinstance(
                    item_card.get("resolution_plan"),
                    dict,
                ):
                    try:
                        compiled_item_plan = _support.compile_resolution_plan(
                            item_card["resolution_plan"]
                        )
                    except _support.ResolutionPlanCompilationError as error:
                        raise _support.CombatEngineError(
                            f"recorded item resolution plan is invalid: {error}"
                        ) from error
                    if (
                        compiled_item_plan.source_card_id != weapon_id
                        or compiled_item_plan.source_card_kind != "item"
                        or compiled_item_plan.trigger != "attack.after_hit"
                    ):
                        raise _support.CombatEngineError(
                            "recorded weapon plan must be an item attack.after_hit contract"
                        )
                    _support._semantic_plan_save_facts(item_card, compiled_item_plan)
        except _support.NeedsRulingError:
            if (
                self.access.require_campaign(campaign_id, principal_id).role
                not in _support.CAMPAIGN_DM_ROLES
            ):
                raise _support.CombatEngineError(
                    "attack requires Agent-as-DM adjudication"
                ) from None
            raise
        attacker["sheet"] = settled_attacker_sheet
        if protection_accepted is None:
            offered = protection.offer(
                self, campaign, encounter, protection_binding, principal_id=principal_id,
                branch_id=resolved_branch_id, idempotency_key=idempotency_key,
                scope=scope, payload=payload,
                facts=dict(action_payload.get("context") or {}).get("protection"),
                sheet_override={actor_id: settled_attacker_sheet} if spell_release else None,
                extra_receipts=spell_release["receipts"] if spell_release else (),
                response_fields=release_fields,
            )
            if offered is not None:
                return offered
        plan = protection.apply(
            plan, protection_accepted, self, campaign_id, resolved_branch_id,
        )
        if spell_resolution is not None:
            next_encounter = _support.deepcopy(encounter)
            attack_payment = {
                "kind": "spell_attack",
                "payment": "spell_cast",
                "spell_resolution_id": spell_resolution_id,
            }
            attack_payment_receipts = _support.core_receipts(
                rule_context,
                [_support.SPELL_RESOLUTION_MECHANIC_ID],
                "combat.spell.attack.payment",
            )
        else:
            next_encounter, attack_payment = _support.pay_attack_action(
                encounter,
                attacker,
                weapon_id=str(plan.get("weapon_id") or ""),
                attack_mode=str(plan.get("attack_mode") or "melee"),
                multiattack_option_id=action_payload.get("multiattack_option_id"),
                target_id=target_id,
                light_extra_attack=action_payload.get("light_extra_attack"),
                weapon_mastery_followup=action_payload.get("weapon_mastery_followup"),
            )
            attack_payment_receipts = _support.core_receipts(
                rule_context,
                ["dnd5e.core.action.multiattack_choice"],
                "combat.attack.payment",
            )
        if spell_release:
            attack_payment_receipts.extend(spell_release["receipts"])
        if deflect_activity is not None:
            assert deflect_contract is not None
            assert deflect_spatial_facts is not None
            assert deflect_eligibility is not None
            try:
                next_encounter = _support.consume_deflect_attack_reaction(
                    next_encounter,
                    defender_id=str(deflect_eligibility["defender_id"]),
                )
            except _support.SteelDefenderError as error:
                raise _support.CombatEngineError(str(error)) from error
            plan = _support.apply_deflect_attack_to_plan(
                plan,
                defender_id=str(deflect_eligibility["defender_id"]),
            )
            attack_payment_receipts = [
                *attack_payment_receipts,
                {
                    "mechanic_id": _support.STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID,
                    "event": "attack.before_roll.steel_defender_deflect",
                    "operations": [{"op": "builtin.expansion_provider"}],
                    "citations": [
                        {
                            "source_artifact_id": deflect_contract["source_artifact_id"],
                            "source_pack_id": deflect_contract["source_pack_id"],
                            "source_pack_version": deflect_contract["source_pack_version"],
                            "reviewed_expression_hash": deflect_contract[
                                "reviewed_expression_hash"
                            ],
                        }
                    ],
                    "facts": _support.deepcopy(deflect_spatial_facts),
                    "ruleset_fingerprint": rule_context.fingerprint,
                },
            ]
        if (
            _support.active_random_stream() is None
            and damage_triggered_confusion_effect_ids(target_record.sheet)
        ):
            with self.campaign_random_context(
                campaign_id,
                "combat.madness.damage_trigger",
                {"idempotency_key": idempotency_key},
            ):
                return self._settle_combat_attack(
                    campaign_id=campaign_id,
                    actor_id=actor_id,
                    target_id=target_id,
                    action=action_payload,
                    principal_id=principal_id,
                    branch_id=resolved_branch_id,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                    spell_release=spell_release,
                    gear_action=gear_action,
                )
        attack_roll = _support.roll_attack_action(plan=plan)
        if deflect_activity is not None:
            attack_roll["deflect_attack"] = {
                "defender_id": str(deflect_eligibility["defender_id"]),
                "activity_id": str(deflect_activity["id"]),
                "reaction_paid": True,
            }
        consumed_attack_advantage = self.consume_next_attack_advantage(
            next_encounter,
            plan,
            attacker_id=actor_id,
            target_id=target_id,
        )
        if consumed_attack_advantage:
            attack_roll["consumed_next_attack_advantage_effect_id"] = consumed_attack_advantage
        mastery_consumption = _support.consume_weapon_mastery_attack_effects(
            next_encounter,
            plan,
        )
        next_encounter = mastery_consumption["encounter"]
        if mastery_consumption["consumed_effect_ids"]:
            attack_roll["consumed_weapon_mastery_effect_ids"] = mastery_consumption[
                "consumed_effect_ids"
            ]
        if spell_resolution is not None:
            attack_roll.update(
                spell_id=str(spell_resolution["spell_id"]),
                cast_level=int(spell_resolution["cast_level"]),
                spell_resolution_id=spell_resolution_id,
            )
        defenses = self.post_hit_attack_defenses(
            campaign_id,
            target,
            plan=plan,
            attack=attack_roll,
            encounter=next_encounter,
        )
        updated_attacker = _support.deepcopy(attacker)
        if plan.get("rage_hostile_attack"):
            from sagasmith_dnd.rage import note_activity

            note_activity(updated_attacker["sheet"], attacked=True)
        # SCAG ends Bladesong after the character makes a two-handed attack.
        # Apply that transition to the attacker sheet before either a pending
        # reaction or a settled damage commit so the termination is atomic with
        # the attack that caused it.
        if str(plan.get("weapon_grip") or "").strip().casefold() == "two_handed":
            for effect in updated_attacker["sheet"].get("effects", []):
                if (
                    effect.get("active")
                    and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
                ):
                    effect["active"] = False
                    effect["ended_reason"] = "two_handed_attack"
        ammunition = None
        limited_use = None
        weapon_id = plan.get("weapon_id")
        if spell_resolution is None and weapon_id and weapon_id != "unarmed-strike":
            if plan.get("weapon_recharge"):
                updated_sheet, limited_use = _support.consume_weapon_limited_use(
                    updated_attacker["sheet"],
                    weapon_id,
                )
                updated_attacker["sheet"] = updated_sheet
            if plan.get("ammunition_item_id"):
                updated_sheet, ammunition = _support.consume_weapon_ammunition(
                    updated_attacker["sheet"],
                    weapon_id,
                    ammunition_item_id=str(plan.get("ammunition_item_id") or "") or None,
                )
                updated_attacker["sheet"] = updated_sheet
        missed_ammunition_poison_state = None
        missed_ammunition_poison = None
        missed_ammunition_poison_receipts: list[dict[str, Any]] = []
        if not attack_roll.get("hit") and plan.get("ammunition_item_id"):
            from .poisons import settle_missed_ammunition_coating

            (
                missed_ammunition_poison_state,
                missed_ammunition_poison,
                missed_ammunition_poison_receipts,
            ) = settle_missed_ammunition_coating(
                self,
                campaign,
                attacker_id=actor_id,
                ammunition_item_id=str(plan.get("ammunition_item_id") or ""),
                branch_id=resolved_branch_id,
            )
        gear_receipt: dict[str, Any] | None = None
        if gear_action is not None:
            gear_rule_plan = dict(gear_action.get("rule_plan") or {})
            resource_cost = dict(gear_rule_plan.get("resource_cost") or {})
            if resource_cost.get("item_quantity") != 1:
                raise _support.CombatEngineError(
                    "gear attack resource cost must be one source-defined item quantity"
                )
            updated_attacker["sheet"], gear_removed = _support.remove_inventory_item(
                updated_attacker["sheet"],
                str(gear_action["request"].get("item_id") or ""),
                1,
            )
            gear_receipt = {
                "action_id": str(gear_action["request"].get("action_id") or ""),
                "item_id": str(gear_action["request"].get("item_id") or ""),
                "intent": str(gear_action["request"].get("intent") or ""),
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "rule_plan": _support.deepcopy(gear_rule_plan),
                "removed": _support.deepcopy(gear_removed),
            }
            if (
                attack_roll.get("hit")
                and str(gear_rule_plan.get("item_name") or "").casefold()
                == "alchemist's fire (flask)"
            ):
                request = dict(gear_action.get("request") or {})
                effect = {
                    "id": f"gear-burning:{request.get('action_id')}",
                    "kind": "adventuring_gear_burning",
                    "mechanic_id": "dnd5e.2014.adventuring_gear.alchemists_fire",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "action_id": str(request.get("action_id") or ""),
                    "source_item_id": str(request.get("item_id") or ""),
                    "source_key": str(gear_rule_plan.get("source_key") or ""),
                    "item_name": str(gear_rule_plan.get("item_name") or ""),
                    "source_actor_id": actor_id,
                    "target_id": target_id,
                    "damage": str(
                        dict(gear_rule_plan.get("effect") or {}).get("ongoing_damage") or ""
                    ),
                    "damage_type": str(
                        dict(gear_rule_plan.get("effect") or {}).get("ongoing_damage_type")
                        or ""
                    ),
                    "active": True,
                }
                if not effect["damage"] or effect["damage_type"] != "fire":
                    raise _support.CombatEngineError(
                        "Alchemist's Fire plan is missing its source-defined ongoing fire damage"
                    )
                next_encounter["ongoing_effects"] = [
                    *list(next_encounter.get("ongoing_effects") or []),
                    effect,
                ]
                gear_receipt["ongoing_effect"] = _support.deepcopy(effect)
        if not attack_roll.get("hit"):
            stroke_choice = self.open_rogue_stroke_attack_choice(
                campaign=campaign,
                campaign_id=campaign_id,
                actor_id=actor_id,
                target_id=target_id,
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                resolution_id=resolution_id,
                encounter=next_encounter,
                updated_attacker=updated_attacker,
                attacker_record=attacker_record,
                attack_roll=attack_roll,
                plan=plan,
                attack_payment=attack_payment,
                attack_payment_receipts=attack_payment_receipts,
                ammunition=ammunition,
                limited_use=limited_use,
                spell_resolution_id=spell_resolution_id,
                poison_campaign_state=missed_ammunition_poison_state,
                poison_event=missed_ammunition_poison,
                poison_receipts=missed_ammunition_poison_receipts,
            )
            if stroke_choice is not None and gear_action is None:
                return stroke_choice
        if defenses:
            result = {
                **attack_roll,
                "attack_payment": attack_payment,
                "pending_reaction": True,
            }
            if gear_receipt is not None:
                result["adventuring_gear"] = _support.deepcopy(gear_receipt)
            if ammunition is not None:
                result["ammunition"] = ammunition
            if limited_use is not None:
                result["limited_use"] = limited_use
            current = next(
                item for item in next_encounter["combatants"] if item.get("actor_id") == actor_id
            )
            if plan.get("attacker_was_hidden"):
                self.reveal_attacker_to_target(next_encounter, actor_id, target_id)
                result["reveals_attacker"] = True
            if plan.get("helped_by"):
                helper = next(
                    (
                        item
                        for item in next_encounter["combatants"]
                        if item.get("actor_id") == plan["helped_by"]
                    ),
                    None,
                )
                if helper is not None:
                    helper_flags = dict(helper.get("turn_flags") or {})
                    helper_flags.pop("helping", None)
                    helper["turn_flags"] = helper_flags
            next_encounter = _support.add_choice_window(
                next_encounter,
                kind="reaction",
                actor_id_value=target_id,
                event="attack.hit.before_damage",
                candidates=[*defenses, {"id": "decline", "name": "Decline"}],
            )
            window = next_encounter["pending"][-1]
            window.update(
                trigger="attack_hit_defense",
                resolution_id=resolution_id,
                thread_id=resolution_id,
                event_sequence=1,
                attacker_id=actor_id,
                target_id=target_id,
                plan=_support.deepcopy(plan),
                attack=_support.deepcopy(attack_roll),
                attack_payment=_support.deepcopy(attack_payment),
                attack_rule_receipts=_support.deepcopy(attack_payment_receipts),
                ammunition=_support.deepcopy(ammunition),
                limited_use=_support.deepcopy(limited_use),
                spell_resolution_id=spell_resolution_id or None,
            )
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "attack_roll",
                    "result": result,
                    "pending_choice_id": window["id"],
                },
            ][-100:]
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            if gear_action is not None:
                gear_receipt = dict(result.get("adventuring_gear") or {})
                next_state["item_spends"] = [
                    *list(next_state.get("item_spends") or []),
                    {
                        "id": gear_receipt["action_id"],
                        "item_id": gear_receipt["item_id"],
                        "quantity": 1,
                        "reason": f"adventuring_gear:{gear_receipt['intent']}",
                        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                        "character_id": actor_id,
                        "owner": {"kind": "character", "character_id": actor_id},
                        "removed": _support.deepcopy(gear_receipt["removed"]),
                        "target_character_id": target_id,
                        "rule_plan": _support.deepcopy(gear_receipt["rule_plan"]),
                    },
                ]
            next_state["resolution_log"] = [
                *list(next_state.get("resolution_log") or []),
                {
                    "id": resolution_id,
                    "thread_id": resolution_id,
                    "event_sequence": 1,
                    "type": "combat_attack",
                    "operation": "combat.attack",
                    "status": "pending",
                    "actor_id": actor_id,
                    "audience": {
                        "scope": "actors",
                        "actor_refs": [actor_id, target_id],
                        "disclosure": "private",
                    },
                    "branch_id": resolved_branch_id,
                    "campaign_revision": campaign.revision + 1,
                    "result": _support.deepcopy(result),
                    "pending_choice": {
                        "id": str(window["id"]),
                        "kind": "attack_hit_defense",
                        "available_actions": [
                            str(item.get("id") or "")
                            for item in window.get("candidates", [])
                            if str(item.get("id") or "")
                        ],
                    },
                },
            ][-200:]
            updates = []
            if updated_attacker["sheet"] != attacker_record.sheet:
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=_support.validate_character_sheet(updated_attacker["sheet"]),
                        notes=_support.validate_character_notes(
                            self.characters.get(actor_id).notes
                        ),
                        expected_revision=self.characters.get(actor_id).revision,
                    )
                )

            from .saving_throws import finalize

            pending_receipts = _support.core_receipts(
                rule_context, ["dnd5e.core.reaction.post_hit_defense"], "attack.hit.before_damage",
            ) + attack_payment_receipts
            next_state, updates, release_fields, pending_receipts = finalize(
                self, campaign, next_state, updates, release_fields, pending_receipts,
            )

            def pending_attack_response(revisions: list[Any]) -> dict[str, Any]:
                response = {
                    **release_fields,
                    "status": "pending_reaction",
                    "resolution_id": resolution_id,
                    "thread_id": resolution_id,
                    "event_sequence": 1,
                    "result": result,
                    "choice": window,
                    "combat": next_encounter,
                    "campaign_revision": campaign.revision + 1,
                    "revisions": [_support.asdict(item) for item in revisions],
                }
                stream = _support.active_random_stream()
                if self.config.local_authority and self.is_dm(campaign_id, principal_id):
                    response["affected_state"] = affected_state_slice(
                        campaign, resolved_branch_id, updates, campaign.revision + 1
                    )
                if stream is not None and stream.draw_count > 0:
                    response["random_stream_receipt"] = stream.receipt()
                return response

            revisions_result = _support.StateMutationService(self.storage.database).replace(
                campaign_id,
                campaign_state=_support.validate_party_state(next_state),
                character_updates=updates,
                expected_campaign_revision=campaign.revision,
                operation="combat.attack.roll",
                actor=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=payload,
                    response=pending_attack_response,
                ),
                rule_receipts=pending_receipts,
            )
            return self.combat_response(
                campaign_id,
                principal_id,
                pending_attack_response(list(revisions_result or [])),
            )
        updated_attacker, updated_target, result = _support.resolve_attack_damage(
            updated_attacker,
            target,
            plan=plan,
            attack=attack_roll,
            rules=rule_context,
        )
        if gear_receipt is not None:
            result["adventuring_gear"] = _support.deepcopy(gear_receipt)
        poison_campaign_state = missed_ammunition_poison_state
        if missed_ammunition_poison is not None:
            result["poison"] = missed_ammunition_poison
            result.setdefault("rule_receipts", []).extend(missed_ammunition_poison_receipts)
        if attack_roll.get("hit"):
            from .poisons import settle_injury_coating

            (
                updated_attacker,
                updated_target,
                poison_campaign_state,
                poison_receipts,
            ) = settle_injury_coating(
                self,
                campaign,
                next_encounter,
                attacker_record=attacker_record,
                target_record=target_record,
                updated_attacker=updated_attacker,
                updated_target=updated_target,
                plan=plan,
                attack=attack_roll,
                result=result,
                branch_id=resolved_branch_id,
            )
            if poison_receipts:
                result["rule_receipts"] = [
                    *list(result.get("rule_receipts") or []),
                    *poison_receipts,
                ]
        self.settle_attack_madness_damage(
            campaign_id=campaign_id,
            target=updated_target,
            encounter=next_encounter,
            result=result,
            rules=rule_context,
            transaction_id=resolution_id,
        )
        mastery_commit = _support.apply_weapon_mastery_to_encounter(
            next_encounter,
            result,
            attacker_id=actor_id,
            target_id=target_id,
        )
        next_encounter = mastery_commit["encounter"]
        if mastery_commit["effect"] is not None:
            result.setdefault("weapon_mastery", {})["committed_effect"] = mastery_commit["effect"]
        if ammunition is not None:
            result["ammunition"] = ammunition
        if limited_use is not None:
            result["limited_use"] = limited_use
        result["attack_payment"] = attack_payment
        result["rule_receipts"] = [
            *list(result.get("rule_receipts") or []),
            *attack_payment_receipts,
        ]
        witch_bolt_spell = None
        if spell_resolution is not None:
            witch_bolt_spell = next(
                (
                    item
                    for item in attacker_record.sheet.get("content", {}).get("spells", [])
                    if str(item.get("id") or "") == str(spell_resolution.get("spell_id") or "")
                    and _support.is_core_witch_bolt_spell(item)
                ),
                None,
            )
        if spell_resolution and spell_resolution.get("readied_id"):
            held_card = spell_resolution["spell_card"]
            witch_bolt_spell = held_card if _support.is_core_witch_bolt_spell(held_card) else None
        if witch_bolt_spell is not None:
            concentration_effect = next(
                (
                    item
                    for item in reversed(updated_attacker["sheet"].get("effects", []))
                    if (
                        item.get("active")
                        and item.get("concentration")
                        and str(item.get("source_spell_id") or "")
                        == str(spell_resolution["spell_id"])
                    )
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Witch Bolt attack has no exact active concentration effect"
                )
            if attack_roll.get("hit"):
                tethered = _support.start_witch_bolt_tether(
                    next_encounter,
                    caster_id=actor_id,
                    target_id=target_id,
                    spell_id=str(spell_resolution["spell_id"]),
                    concentration_effect_id=str(concentration_effect["id"]),
                )
                next_encounter = tethered["encounter"]
                result["witch_bolt"] = {
                    "status": "tethered",
                    "effect": tethered["effect"],
                }
            else:
                ended = _support.end_concentration_effects(
                    updated_attacker["sheet"],
                    effect_ids=[str(concentration_effect["id"])],
                    ended_reason="witch_bolt_initial_attack_missed",
                )
                updated_attacker["sheet"] = ended["sheet"]
                result["witch_bolt"] = {
                    "status": "ended",
                    "reason": "initial_attack_missed",
                    "ended_concentration_effect_ids": ended["ended_effect_ids"],
                }
            result["rule_receipts"] = [
                *list(result.get("rule_receipts") or []),
                *_support.core_receipts(
                    rule_context,
                    [_support.CORE_WITCH_BOLT_MECHANIC_ID],
                    "combat.spell.witch_bolt.initial_attack",
                ),
            ]
        action_ended_tethers = _support.newly_ended_witch_bolt_tethers(
            encounter,
            next_encounter,
            source_actor_id=actor_id,
        )
        if action_ended_tethers:
            ended = _support.end_tether_concentrations(
                updated_attacker["sheet"],
                action_ended_tethers,
            )
            updated_attacker["sheet"] = ended["sheet"]
            result["ended_witch_bolt_tethers"] = [
                {
                    "effect_id": str(item.get("id") or ""),
                    "reason": str(item.get("ended_reason") or ""),
                    "concentration_effect_id": str(item.get("concentration_effect_id") or ""),
                }
                for item in action_ended_tethers
            ]
        current = next(
            item for item in next_encounter["combatants"] if item.get("actor_id") == actor_id
        )
        sneak_attack = dict(result.get("sneak_attack") or {})
        if sneak_attack.get("used"):
            flags = dict(current.get("turn_flags") or {})
            flags["sneak_attack_turn_token"] = sneak_attack["turn_token"]
            current["turn_flags"] = flags
        if result.get("reveals_attacker"):
            self.reveal_attacker_to_target(next_encounter, actor_id, target_id)
        if plan.get("helped_by"):
            helper = next(
                (
                    item
                    for item in next_encounter["combatants"]
                    if item.get("actor_id") == plan["helped_by"]
                ),
                None,
            )
            if helper is not None:
                helper_flags = dict(helper.get("turn_flags") or {})
                helper_flags.pop("helping", None)
                helper["turn_flags"] = helper_flags
        self.sync_combatant_conditions(next_encounter, actor_id, updated_attacker["sheet"])
        self.sync_combatant_conditions(next_encounter, target_id, updated_target["sheet"])
        official_item_commit = _support.apply_official_item_effect_to_encounter(
            next_encounter,
            result,
            attacker_id=actor_id,
            target_id=target_id,
        )
        next_encounter = official_item_commit["encounter"]
        if official_item_commit["effect"] is not None:
            result["official_item_effect"]["committed"] = _support.deepcopy(
                official_item_commit["effect"]
            )
        _support.reconcile_readied_spells(next_encounter, target_id, updated_target["sheet"])
        damage_result = result.get("damage")
        if isinstance(damage_result, dict):
            self.add_concentration_window(
                next_encounter,
                target_id,
                damage_result.get("concentration"),
                next_revision=campaign.revision + 1,
            )
        if isinstance(result.get("damage"), dict):
            result["damage"] = {
                key: value for key, value in result["damage"].items() if key != "sheet"
            }
        if spell_resolution is not None:
            result["spell_resolution"] = self.advance_spell_attack_resolution(
                next_encounter,
                resolution_id=spell_resolution_id,
                result=result,
            )
        self.apply_standard_spell_on_hit_mechanics(
            next_encounter,
            result=result,
            attacker_id=actor_id,
            target_id=target_id,
        )
        on_hit_ruling = dict(result.get("on_hit_ruling") or {})
        pending_on_hit_ruling: dict[str, Any] | None = None
        if attack_roll.get("hit") and compiled_item_plan is not None:
            result.pop("on_hit_ruling", None)
            next_encounter = _support.add_choice_window(
                next_encounter,
                kind="ruling",
                actor_id_value=target_id,
                event="attack.on_hit.semantic_plan",
                candidates=[
                    {
                        "id": "execute_plan",
                        "name": "Execute the reviewed item plan",
                    }
                ],
            )
            pending_on_hit_ruling = next_encounter["pending"][-1]
            pending_on_hit_ruling.update(
                trigger="attack_semantic_plan",
                attack_ref=str(plan.get("attack_id") or plan.get("weapon_id") or ""),
                attacker_id=actor_id,
                branch_id=resolved_branch_id,
                campaign_id=campaign_id,
                critical=bool(attack_roll.get("critical", False)),
                target_id=target_id,
                weapon_id=str(plan.get("weapon_id") or ""),
                plan_id=compiled_item_plan.id,
                plan_fingerprint=compiled_item_plan.fingerprint,
                resolution_plan_contract=_support.resolution_plan_contract(compiled_item_plan),
            )
            result["semantic_plan"] = {
                "status": "payment_recorded",
                "application_id": pending_on_hit_ruling["id"],
                "contract": _support.resolution_plan_contract(compiled_item_plan),
            }
            result["pending_on_hit_ruling_id"] = pending_on_hit_ruling["id"]
        elif attack_roll.get("hit") and str(on_hit_ruling.get("effect") or "").strip():
            custom_item_ruling = bool(
                isinstance(item_card, dict)
                and str(item_card.get("pack_id") or "")
                not in {
                    _support.CORE_CONTENT_PACK_ID,
                    _support.CORE_2024_CONTENT_PACK_ID,
                    _support.STANDARD_2014_CONTENT_PACK_ID,
                }
                and not self.source_card_has_executable_mechanic(
                    campaign_id,
                    item_card,
                )
            )
            pending_on_hit_ruling = self.add_attack_on_hit_window(
                next_encounter,
                result=result,
                attacker_id=actor_id,
                target_id=target_id,
                weapon_id=str(plan.get("weapon_id") or ""),
            )
            assert pending_on_hit_ruling is not None
            pending_on_hit_ruling.update(
                attack_ref=str(plan.get("attack_id") or plan.get("weapon_id") or ""),
                branch_id=resolved_branch_id,
                campaign_id=campaign_id,
                critical=bool(attack_roll.get("critical", False)),
            )
            if custom_item_ruling:
                pending_on_hit_ruling.update(
                    source_card_id=str(plan.get("weapon_id") or ""),
                    source_card_kind="item",
                )
                result["semantic_solution"] = {
                    **self.unresolved_content_solution(
                        item_card,
                        source_card_id=str(plan.get("weapon_id") or ""),
                        source_card_kind="item",
                        character_revision=attacker_record.revision,
                    ),
                    "application_id": pending_on_hit_ruling["id"],
                }
            result["pending_on_hit_ruling_id"] = pending_on_hit_ruling["id"]
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "attack",
                "actor_id": actor_id,
                "target_id": target_id,
                "weapon_id": str(plan.get("weapon_id") or ""),
                "result": result,
                "round": int(next_encounter.get("round", 1) or 1),
                "turn_index": int(next_encounter.get("turn_index", 0) or 0),
            },
        ][-100:]
        next_state = dict(campaign.state or {})
        next_state["combat"] = next_encounter
        if gear_action is not None:
            gear_receipt = dict(result.get("adventuring_gear") or {})
            next_state["item_spends"] = [
                *list(next_state.get("item_spends") or []),
                {
                    "id": gear_receipt["action_id"],
                    "item_id": gear_receipt["item_id"],
                    "quantity": 1,
                    "reason": f"adventuring_gear:{gear_receipt['intent']}",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "character_id": actor_id,
                    "owner": {"kind": "character", "character_id": actor_id},
                    "removed": _support.deepcopy(gear_receipt["removed"]),
                    "target_character_id": target_id,
                    "rule_plan": _support.deepcopy(gear_receipt["rule_plan"]),
                },
            ]
        if poison_campaign_state is not None:
            next_state["poison_coatings"] = poison_campaign_state["poison_coatings"]
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "combat_attack",
                "operation": "combat.attack",
                "status": "pending" if pending_on_hit_ruling else "settled",
                "actor_id": actor_id,
                "audience": {
                    "scope": "actors",
                    "actor_refs": [actor_id, target_id],
                    "disclosure": "private",
                },
                "branch_id": resolved_branch_id,
                "campaign_revision": campaign.revision + 1,
                "result": _support.deepcopy(result),
                "pending_choice": (
                    {
                        "id": str(pending_on_hit_ruling["id"]),
                        "kind": str(pending_on_hit_ruling.get("trigger") or "on_hit_ruling"),
                        "available_actions": [
                            str(item.get("id") or "")
                            for item in pending_on_hit_ruling.get("candidates", [])
                            if str(item.get("id") or "")
                        ],
                    }
                    if pending_on_hit_ruling
                    else None
                ),
            },
        ][-200:]
        updates = []
        for record, updated in (
            (attacker_record, updated_attacker),
            (target_record, updated_target),
        ):
            normalized_sheet = _support.validate_character_sheet(updated["sheet"])
            normalized_notes = _support.validate_character_notes(record.notes)
            if normalized_sheet == record.sheet and normalized_notes == record.notes:
                continue
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=record.id,
                    sheet=normalized_sheet,
                    notes=normalized_notes,
                    expected_revision=record.revision,
                )
            )

        from .saving_throws import finalize

        next_state, updates, release_fields, attack_receipts = finalize(
            self, campaign, next_state, updates, release_fields,
            list(result.get("rule_receipts") or []),
        )

        def attack_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
                **release_fields,
                **_support._ruling_status(
                    "pending_ruling" if pending_on_hit_ruling else "committed",
                    (
                        "module_specific_procedure"
                        if compiled_item_plan is not None
                        else "source_or_scene_fact"
                    ),
                ),
                "resolution_id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "result": result,
                "combat": next_encounter,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }
            stream = _support.active_random_stream()
            if self.config.local_authority and self.is_dm(campaign_id, principal_id):
                response["affected_state"] = affected_state_slice(
                    campaign, resolved_branch_id, updates, campaign.revision + 1
                )
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

        try:
            revisions_result = _support.StateMutationService(self.storage.database).replace(
                campaign_id,
                campaign_state=_support.validate_party_state(next_state),
                character_updates=updates,
                expected_campaign_revision=campaign.revision,
                operation="combat.attack.resolve",
                actor=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=payload,
                    response=attack_response,
                ),
                rule_receipts=attack_receipts,
            )
        except ValueError as error:
            # Two identical requests can pass the read-side replay check before
            # either writer reaches the serialized mutation.  The losing writer
            # must return the committed response once the winner's receipt is
            # visible, rather than surfacing the core duplicate-group guard.
            if not (
                "already has a committed mutation group" in str(error)
                or "campaign revision conflict or branch conflict" in str(error)
            ):
                raise
            replay = self.replay_idempotent(scope, idempotency_key, payload)
            if replay is None:
                raise
            return self.combat_response(campaign_id, principal_id, replay)
        return self.combat_response(
            campaign_id,
            principal_id,
            attack_response(list(revisions_result or [])),
        )

    def open_rogue_stroke_attack_choice(
        self,
        *,
        campaign: Any,
        campaign_id: str,
        actor_id: str,
        target_id: str,
        principal_id: str,
        branch_id: str,
        idempotency_key: str,
        scope: str,
        payload: dict[str, Any],
        resolution_id: str,
        encounter: dict[str, Any],
        updated_attacker: dict[str, Any],
        attacker_record: Any,
        attack_roll: dict[str, Any],
        plan: dict[str, Any],
        attack_payment: dict[str, Any],
        attack_payment_receipts: list[dict[str, Any]],
        ammunition: dict[str, Any] | None,
        limited_use: dict[str, Any] | None,
        spell_resolution_id: str,
        poison_campaign_state: dict[str, Any] | None = None,
        poison_event: dict[str, Any] | None = None,
        poison_receipts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        from sagasmith_dnd.character_schema import srd2014_rogue_stroke_of_luck_feature

        feature = srd2014_rogue_stroke_of_luck_feature(updated_attacker["sheet"])
        if feature is None or int(dict(feature.get("uses") or {}).get("value", 0) or 0) < 1:
            return None
        next_encounter = _support.add_choice_window(
            encounter,
            kind="feature",
            actor_id_value=actor_id,
            event="attack.after_miss",
            candidates=[
                {"id": "use_stroke_of_luck", "name": "Use Stroke of Luck"},
                {"id": "decline", "name": "Decline"},
            ],
        )
        window = next_encounter["pending"][-1]
        choice_id = str(window["id"])
        window.update(
            trigger="rogue_stroke_luck_attack",
            attacker_id=actor_id,
            target_id=target_id,
            branch_id=branch_id,
            resolution_id=resolution_id,
        )
        rule_context = self.effective_rule_context(campaign_id, branch_id=branch_id)
        result = {
            **_support.deepcopy(attack_roll),
            "attack_payment": _support.deepcopy(attack_payment),
            "post_roll_choice": {
                "id": choice_id,
                "kind": "stroke_of_luck",
                "candidates": _support.deepcopy(window["candidates"]),
            },
            "rule_receipts": [
                *list(attack_payment_receipts),
                *list(poison_receipts or []),
                *_support.core_receipts(
                    rule_context,
                    ["dnd5e.core.rogue.stroke_of_luck"],
                    "attack.after_miss",
                ),
            ],
        }
        if ammunition is not None:
            result["ammunition"] = _support.deepcopy(ammunition)
        if limited_use is not None:
            result["limited_use"] = _support.deepcopy(limited_use)
        if poison_event is not None:
            result["poison"] = _support.deepcopy(poison_event)
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "attack_roll",
                "actor_id": actor_id,
                "target_id": target_id,
                "result": _support.deepcopy(result),
                "pending_choice_id": choice_id,
            },
        ][-100:]
        next_state = _support.deepcopy(dict(campaign.state or {}))
        next_state["combat"] = next_encounter
        if poison_campaign_state is not None:
            next_state["poison_coatings"] = _support.deepcopy(
                poison_campaign_state["poison_coatings"]
            )
        next_state["rogue_choices"] = [
            *list(next_state.get("rogue_choices") or []),
            {
                "id": choice_id,
                "actor_id": actor_id,
                "kind": "stroke_of_luck_attack",
                "result": {
                    "offered": _support.deepcopy(result),
                    "resolution_id": resolution_id,
                    "attack": _support.deepcopy(attack_roll),
                    "plan": _support.deepcopy(plan),
                    "attack_payment": _support.deepcopy(attack_payment),
                    "attack_payment_receipts": _support.deepcopy(attack_payment_receipts),
                    "ammunition": _support.deepcopy(ammunition),
                    "limited_use": _support.deepcopy(limited_use),
                    "spell_resolution_id": spell_resolution_id,
                },
                "branch_id": branch_id,
                "created_revision": campaign.revision + 1,
                "target_id": target_id,
            },
        ]
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "combat_attack",
                "operation": "combat.attack",
                "status": "pending",
                "actor_id": actor_id,
                "audience": {
                    "scope": "actors",
                    "actor_refs": [actor_id, target_id],
                    "disclosure": "private",
                },
                "branch_id": branch_id,
                "campaign_revision": campaign.revision + 1,
                "result": _support.deepcopy(result),
                "pending_choice": {
                    "id": choice_id,
                    "kind": "rogue_stroke_luck_attack",
                    "available_actions": ["use_stroke_of_luck", "decline"],
                },
            },
        ][-200:]
        updates = []
        if updated_attacker["sheet"] != attacker_record.sheet:
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated_attacker["sheet"]),
                    notes=_support.validate_character_notes(attacker_record.notes),
                    expected_revision=attacker_record.revision,
                )
            )

        def response_for(revisions: list[Any]) -> dict[str, Any]:
            response = {
                "status": "pending_choice",
                "resolution_id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "result": result,
                "choice": window,
                "combat": next_encounter,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }
            stream = _support.active_random_stream()
            if self.config.local_authority and self.is_dm(campaign_id, principal_id):
                response["affected_state"] = affected_state_slice(
                    campaign, branch_id, updates, campaign.revision + 1
                )
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

        revisions = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            character_updates=updates,
            expected_campaign_revision=campaign.revision,
            operation="combat.attack.stroke_of_luck_choice",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response_for,
            ),
            rule_receipts=list(result.get("rule_receipts") or []),
        )
        return self.combat_response(campaign_id, principal_id, response_for(list(revisions or [])))

    def resolve_rogue_stroke_attack_choice(
        self,
        *,
        campaign: Any,
        campaign_id: str,
        actor_id: str,
        choice_id: str,
        selection: dict[str, Any],
        principal_id: str,
        branch_id: str,
        idempotency_key: str,
        scope: str,
        payload: dict[str, Any],
        pending_choice: dict[str, Any],
    ) -> dict[str, Any]:
        from sagasmith_dnd.character_schema import srd2014_rogue_stroke_of_luck_feature
        from sagasmith_dnd.resources import mutate_bounded_resource

        if (
            pending_choice.get("kind") != "stroke_of_luck_attack"
            or pending_choice.get("actor_id") != actor_id
            or pending_choice.get("branch_id") != branch_id
        ):
            raise _support.CombatEngineError("Stroke of Luck attack choice no longer matches")
        if not isinstance(selection, dict) or set(selection) != {"id"}:
            raise _support.CombatEngineError(
                "Stroke of Luck choice requires exactly one selection id"
            )
        selection_id = str(selection.get("id") or "")
        if selection_id not in {"use_stroke_of_luck", "decline"}:
            raise _support.CombatEngineError("unknown Stroke of Luck choice")
        if selection_id == "use_stroke_of_luck" and campaign.revision != int(
            pending_choice.get("created_revision", 0) or 0
        ):
            raise _support.CombatEngineError(
                "Stroke of Luck can only resolve before another campaign write"
            )
        details = dict(pending_choice.get("result") or {})
        attack = _support.deepcopy(dict(details.get("attack") or {}))
        plan = _support.deepcopy(dict(details.get("plan") or {}))
        target_id = str(pending_choice.get("target_id") or "")
        if not attack or not plan or not target_id or attack.get("hit"):
            raise _support.CombatEngineError("Stroke of Luck attack record is invalid")

        next_state = _support.deepcopy(dict(campaign.state or {}))
        next_state["rogue_choices"] = [
            item
            for item in next_state.get("rogue_choices", [])
            if str(item.get("id") or "") != choice_id
        ]
        encounter = dict(next_state.get("combat") or {})
        if not encounter.get("active", False):
            raise _support.CombatEngineError("Stroke of Luck attack encounter is no longer active")
        window = next(
            (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
            None,
        )
        if not isinstance(window, dict) or window.get("trigger") != "rogue_stroke_luck_attack":
            raise _support.CombatEngineError("Stroke of Luck attack choice window is missing")
        encounter = _support.resolve_choice_window(
            encounter,
            choice_id=choice_id,
            actor_id_value=actor_id,
            selection=selection,
        )
        offered = _support.deepcopy(dict(details.get("offered") or {}))
        result = offered
        updates: list[_support.CharacterStateUpdate] = []
        receipts: list[dict[str, Any]] = []
        if selection_id == "use_stroke_of_luck":
            attacker_record = self.require_campaign_actor(campaign_id, actor_id)
            target_record = self.require_campaign_actor(campaign_id, target_id)
            attacker = self.character_view(attacker_record)
            target = self.character_view(target_record)
            feature = srd2014_rogue_stroke_of_luck_feature(attacker["sheet"])
            if feature is None:
                raise _support.CombatEngineError("Stroke of Luck is not present on the actor card")
            uses = dict(feature.get("uses") or {})
            if int(uses.get("value", 0) or 0) < 1:
                raise _support.CombatEngineError("Stroke of Luck is already expended")
            spend = mutate_bounded_resource(uses, amount=1, direction="spend")
            attack["hit"] = True
            attack["stroke_of_luck_applied"] = True
            attack["stroke_of_luck_original_hit"] = False
            rule_context = self.effective_rule_context(campaign_id, branch_id=branch_id)
            updated_attacker, updated_target, result = _support.resolve_attack_damage(
                attacker,
                target,
                plan=plan,
                attack=attack,
                rules=rule_context,
            )
            feature = srd2014_rogue_stroke_of_luck_feature(updated_attacker["sheet"])
            if feature is None:
                raise _support.CombatEngineError("Stroke of Luck source changed during resolution")
            uses = dict(feature.get("uses") or {})
            mutate_bounded_resource(uses, amount=1, direction="spend")
            feature["uses"] = uses
            stroke_resource = spend
            stroke_receipts = _support.core_receipts(
                rule_context,
                ["dnd5e.core.rogue.stroke_of_luck"],
                "attack.stroke_of_luck",
            )
            receipts = [
                *list(details.get("attack_payment_receipts") or []),
                *stroke_receipts,
                *list(result.get("rule_receipts") or []),
            ]
            result.update(
                {
                    "stroke_of_luck_applied": True,
                    "stroke_of_luck_resource": stroke_resource,
                    "stroke_of_luck_choice": {
                        "id": choice_id,
                        "selection": selection_id,
                        "resolved": True,
                    },
                    "attack_payment": _support.deepcopy(details.get("attack_payment") or {}),
                    "rule_receipts": receipts,
                }
            )
            if details.get("ammunition") is not None:
                result["ammunition"] = _support.deepcopy(details["ammunition"])
            if details.get("limited_use") is not None:
                result["limited_use"] = _support.deepcopy(details["limited_use"])
            mastery_commit = _support.apply_weapon_mastery_to_encounter(
                encounter,
                result,
                attacker_id=actor_id,
                target_id=target_id,
            )
            encounter = mastery_commit["encounter"]
            if mastery_commit["effect"] is not None:
                result.setdefault("weapon_mastery", {})["committed_effect"] = mastery_commit[
                    "effect"
                ]
            self.sync_combatant_conditions(encounter, actor_id, updated_attacker["sheet"])
            self.sync_combatant_conditions(encounter, target_id, updated_target["sheet"])
            _support.reconcile_readied_spells(encounter, target_id, updated_target["sheet"])
            damage = result.get("damage")
            if isinstance(damage, dict):
                self.add_concentration_window(
                    encounter,
                    target_id,
                    damage.get("concentration"),
                    next_revision=campaign.revision + 1,
                )
                result["damage"] = {
                    key: value for key, value in damage.items() if key != "sheet"
                }
            self.apply_standard_spell_on_hit_mechanics(
                encounter,
                result=result,
                attacker_id=actor_id,
                target_id=target_id,
            )
            spell_resolution_id = str(details.get("spell_resolution_id") or "")
            if spell_resolution_id:
                result["spell_resolution"] = self.advance_spell_attack_resolution(
                    encounter,
                    resolution_id=spell_resolution_id,
                    result=result,
                )
            for record, updated in (
                (attacker_record, updated_attacker),
                (target_record, updated_target),
            ):
                sheet = _support.validate_character_sheet(updated["sheet"])
                notes = _support.validate_character_notes(record.notes)
                if sheet == record.sheet and notes == record.notes:
                    continue
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=record.id,
                        sheet=sheet,
                        notes=notes,
                        expected_revision=record.revision,
                    )
                )
        else:
            result["stroke_of_luck_choice"] = {
                "id": choice_id,
                "selection": selection_id,
                "resolved": True,
            }
            receipts = list(result.get("rule_receipts") or [])

        encounter["log"] = [
            *list(encounter.get("log") or []),
            {
                "type": "rogue_stroke_of_luck_attack_choice",
                "actor_id": actor_id,
                "target_id": target_id,
                "result": _support.deepcopy(result),
            },
        ][-100:]
        next_state["combat"] = encounter
        resolution_id = str(details.get("resolution_id") or "")
        next_state["resolution_log"] = [
            {
                **item,
                "status": "settled",
                "campaign_revision": campaign.revision + 1,
                "result": _support.deepcopy(result),
                "pending_choice": None,
            }
            if str(item.get("id") or "") == resolution_id
            else item
            for item in next_state.get("resolution_log", [])
        ]
        result["stroke_of_luck_choice"] = {
            "id": choice_id,
            "selection": selection_id,
            "resolved": True,
        }
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.choice.rogue_stroke_of_luck_attack",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": result,
                "choice_resolution": {"choice_id": choice_id, "selection": selection_id},
                "combat": encounter,
            },
            character_updates=updates,
            rule_receipts=receipts,
            expected_campaign_revision=campaign.revision,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_reaction_attack(
        self,
        campaign_id: str,
        actor_id: str,
        choice_id: str,
        target_id: str,
        action: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an owned opportunity-attack window atomically with its attack."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if actor_id == target_id:
            raise _support.CombatEngineError("an actor cannot attack itself")
        campaign = self.campaigns.get(campaign_id)
        action_payload = self.sanitize_attack_action(campaign_id, principal_id, dict(action or {}))
        payload = {
            "actor_id": actor_id,
            "choice_id": choice_id,
            "target_id": target_id,
            "action": action_payload,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-reaction-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if _support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id,
                "combat_reaction_attack",
                {"idempotency_key": idempotency_key},
            ):
                return self.combat_reaction_attack(
                    campaign_id,
                    actor_id,
                    choice_id,
                    target_id,
                    action_payload,
                    principal_id,
                    resolved_branch_id,
                    expected_revision,
                    idempotency_key,
                )
        _, encounter = self.active_encounter(campaign_id)
        window = next(
            (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
            None,
        )
        if (
            not isinstance(window, dict)
            or window.get("kind") != "reaction"
            or window.get("actor_id") != actor_id
            or window.get("trigger") != "opportunity_attack"
            or target_id not in set(
                window.get("target_actor_ids")
                or [window.get("target_id")]
            )
        ):
            raise _support.CombatEngineError(
                "choice_id is not this actor's opportunity-attack window"
            )
        recorded_weapon_ids = window.get("opportunity_attack_weapon_ids")
        if recorded_weapon_ids is not None:
            if (
                not isinstance(recorded_weapon_ids, list)
                or not recorded_weapon_ids
                or any(
                    not isinstance(item, str) or not item.strip() for item in recorded_weapon_ids
                )
                or len(set(recorded_weapon_ids)) != len(recorded_weapon_ids)
            ):
                raise _support.CombatEngineError(
                    "opportunity-attack window has invalid weapon options"
                )
            requested_weapon_id = str(
                action_payload.get("weapon_id") or action_payload.get("item_id") or ""
            ).strip()
            if requested_weapon_id not in set(recorded_weapon_ids):
                raise _support.CombatEngineError(
                    "the selected weapon did not produce this opportunity-attack boundary"
                )
        return self.settle_reaction_attack(
            campaign_id, campaign, encounter, actor_id, target_id, action_payload,
            choice_id, window, principal_id, resolved_branch_id, idempotency_key, scope, payload,
        )

    def settle_reaction_attack(
        self, campaign_id, campaign, encounter, actor_id, target_id, action_payload,
        choice_id, window, principal_id, resolved_branch_id, idempotency_key, scope, payload,
        *, readied=None,
    ):
        """Settle a verified opportunity or stored Ready attack in one transaction."""
        protection_binding = {
            "kind": "reaction_attack", "actor_id": actor_id, "target_id": target_id,
            "payload": _support.deepcopy(payload),
        }
        encounter, protection_accepted = protection.resume(encounter, protection_binding)
        ready_fields = {} if readied is None else {
            "released": True, "declaration": _support.deepcopy(readied["payload"]),
            "readied_id": readied["id"],
        }
        reaction_receipt_id = (
            "dnd5e.core.ready.action" if readied is not None
            else "dnd5e.core.mcp.opportunity_melee_only"
        )
        reacting = next(a for a in encounter["combatants"] if a["actor_id"] == actor_id)
        if int(reacting.get("turn_budget", {}).get("reaction", 0)) < 1:
            raise _support.CombatEngineError("actor has no reaction remaining")
        self.require_campaign_actor(campaign_id, target_id)
        attacker = self.combat_actor_snapshot(actor_id)
        target = self.combat_actor_snapshot(target_id)
        combatant_position = next(
            (
                item.get("position")
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == actor_id
            ),
            None,
        )
        if isinstance(combatant_position, dict):
            attacker["position"] = dict(combatant_position)
        if isinstance(window.get("target_position"), dict):
            target["position"] = dict(window["target_position"])
        for combatant_state, snapshot in (
            (
                next(item for item in encounter["combatants"] if item.get("actor_id") == actor_id),
                attacker,
            ),
            (
                next(item for item in encounter["combatants"] if item.get("actor_id") == target_id),
                target,
            ),
        ):
            snapshot["hidden"] = bool(combatant_state.get("hidden", False))
            snapshot["visible_to_actor_ids"] = _support.deepcopy(
                combatant_state.get("visible_to_actor_ids")
            )
        if isinstance(window.get("attack_spatial_facts"), dict):
            action_payload = dict(action_payload)
            action_payload["context"] = {
                **dict(action_payload.get("context") or {}),
                "spatial_facts": _support.deepcopy(window["attack_spatial_facts"]),
            }
        elif window.get("target_visible"):
            action_payload = dict(action_payload)
            action_payload["context"] = {
                **dict(action_payload.get("context") or {}),
                "attacker_can_see_target": True,
            }
        trigger_encounter = _support.deepcopy(encounter)
        if isinstance(window.get("target_position"), dict):
            trigger_target = next(
                item
                for item in trigger_encounter["combatants"]
                if item.get("actor_id") == target_id
            )
            trigger_target["position"] = dict(window["target_position"])
        rule_context = self.effective_rule_context(
            campaign_id,
            facts={"actor_id": actor_id, "target_id": target_id, "kind": "attack"},
        )
        action_payload = prepare_attack_action(
            self, action_payload, campaign_id=campaign_id, actor_id=actor_id,
            target_id=target_id, principal_id=principal_id, encounter=trigger_encounter,
        )
        plan = _support.preflight_attack(
            attacker,
            target,
            action=action_payload,
            encounter=trigger_encounter,
            allow_out_of_turn=True,
            rules=rule_context,
        )
        reaction_receipts = _support.core_receipts(
            rule_context,
            [
                reaction_receipt_id,
                *(
                    ["dnd5e.core.vision.light_obscuration_2014"]
                    if encounter.get("ruleset") == "2014" and plan.get("vision")
                    else []
                ),
            ],
            "reaction.opportunity_attack" if readied is None else "reaction.ready.attack",
        )
        weapon = next(
            (
                item
                for item in attacker.get("derived", {})
                .get("inventory", {})
                .get("weapon_attacks", [])
                if item.get("item_id") == plan.get("weapon_id")
            ),
            None,
        )
        if readied is None and weapon is not None and weapon.get("attack_type") != "melee":
            raise _support.CombatEngineError("opportunity attacks require a melee attack")
        if protection_accepted is None:
            offered = protection.offer(
                self, campaign, encounter, protection_binding, principal_id=principal_id,
                branch_id=resolved_branch_id, idempotency_key=idempotency_key,
                scope=scope, payload=payload,
                facts=dict(action_payload.get("context") or {}).get("protection"),
                target_position=window.get("target_position"),
            )
            if offered is not None:
                return offered
        plan = protection.apply(
            plan, protection_accepted, self, campaign_id, resolved_branch_id,
        )
        attack_roll = _support.roll_attack_action(plan=plan)
        defenses = self.post_hit_attack_defenses(
            campaign_id,
            target,
            plan=plan,
            attack=attack_roll,
            encounter=encounter,
        )
        updated_attacker = _support.deepcopy(attacker)
        ammunition = None
        limited_use = None
        weapon_id = plan.get("weapon_id")
        if weapon_id and weapon_id != "unarmed-strike":
            if plan.get("weapon_recharge"):
                updated_sheet, limited_use = _support.consume_weapon_limited_use(
                    updated_attacker["sheet"],
                    weapon_id,
                )
                updated_attacker["sheet"] = updated_sheet
            if plan.get("ammunition_item_id"):
                updated_sheet, ammunition = _support.consume_weapon_ammunition(
                    updated_attacker["sheet"],
                    weapon_id,
                    ammunition_item_id=str(plan.get("ammunition_item_id") or "") or None,
                )
                updated_attacker["sheet"] = updated_sheet
        if readied is None:
            next_encounter = _support.resolve_choice_window(
                encounter, choice_id=choice_id, actor_id_value=actor_id,
                selection={"id": "opportunity_attack"},
            )
        else:
            next_encounter, _ = _support.resolve_readied_action_window(
                encounter, actor_id_value=actor_id, choice_id=choice_id,
                release=True, _spend_reaction=False,
            )
        consumed_attack_advantage = self.consume_next_attack_advantage(
            next_encounter,
            plan,
            attacker_id=actor_id,
            target_id=target_id,
        )
        if consumed_attack_advantage:
            attack_roll["consumed_next_attack_advantage_effect_id"] = consumed_attack_advantage
        combatant = next(
            item for item in next_encounter["combatants"] if item.get("actor_id") == actor_id
        )
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0:
            raise _support.CombatEngineError("actor has no reaction remaining")
        budget["reaction"] = int(budget["reaction"]) - 1
        combatant["turn_budget"] = budget
        if readied is not None:
            next_encounter.setdefault("log", []).append({
                "type": "readied_action_released", "actor_id": actor_id,
                "readied_id": readied["id"], "declaration": _support.deepcopy(readied["payload"]),
            })
        if plan.get("attacker_was_hidden"):
            self.reveal_attacker_to_target(next_encounter, actor_id, target_id)
        if plan.get("helped_by"):
            helper = next(
                (
                    item
                    for item in next_encounter["combatants"]
                    if item.get("actor_id") == plan["helped_by"]
                ),
                None,
            )
            if helper is not None:
                helper_flags = dict(helper.get("turn_flags") or {})
                helper_flags.pop("helping", None)
                helper["turn_flags"] = helper_flags
        if defenses:
            attack_payment = {
                "kind": "reaction_attack",
                "payment": "reaction",
                "trigger": "readied_action" if readied is not None else "opportunity_attack",
            }
            result = {
                **attack_roll,
                "attack_payment": attack_payment,
                "pending_reaction": True,
            }
            if ammunition is not None:
                result["ammunition"] = ammunition
            if limited_use is not None:
                result["limited_use"] = limited_use
            if plan.get("attacker_was_hidden"):
                result["reveals_attacker"] = True
            next_encounter = _support.add_choice_window(
                next_encounter,
                kind="reaction",
                actor_id_value=target_id,
                event="attack.hit.before_damage",
                candidates=[*defenses, {"id": "decline", "name": "Decline"}],
            )
            defense_window = next_encounter["pending"][-1]
            defense_window.update(
                trigger="attack_hit_defense",
                attacker_id=actor_id,
                target_id=target_id,
                plan=_support.deepcopy(plan),
                attack=_support.deepcopy(attack_roll),
                attack_payment=attack_payment,
                ammunition=_support.deepcopy(ammunition),
                limited_use=_support.deepcopy(limited_use),
                source_choice_id=choice_id,
            )
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "reaction_attack_roll",
                    "choice_id": choice_id,
                    "result": result,
                    "pending_choice_id": defense_window["id"],
                },
            ][-100:]
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            updates = []
            if updated_attacker["sheet"] != attacker["sheet"]:
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=_support.validate_character_sheet(updated_attacker["sheet"]),
                        notes=_support.validate_character_notes(
                            self.characters.get(actor_id).notes
                        ),
                        expected_revision=self.characters.get(actor_id).revision,
                    )
                )
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation=("combat.ready.action.release" if readied is not None
                           else "combat.reaction.attack.roll"),
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "pending_reaction",
                    "result": result,
                    "rule_receipts": reaction_receipts,
                    **ready_fields,
                    "choice": defense_window,
                    "combat": next_encounter,
                },
                character_updates=updates,
                rule_receipts=[
                    *reaction_receipts,
                    *_support.core_receipts(
                        rule_context,
                        ["dnd5e.core.reaction.post_hit_defense"],
                        "reaction.opportunity_attack.hit" if readied is None
                        else "reaction.ready.attack",
                    ),
                ],
            )
            return self.combat_response(campaign_id, principal_id, response)
        updated_attacker, updated_target, result = _support.resolve_attack_damage(
            updated_attacker,
            target,
            plan=plan,
            attack=attack_roll,
            rules=rule_context,
        )
        self.settle_attack_madness_damage(
            campaign_id=campaign_id,
            target=updated_target,
            encounter=next_encounter,
            result=result,
            rules=rule_context,
            transaction_id=str(choice_id),
        )
        if ammunition is not None:
            result["ammunition"] = ammunition
        if limited_use is not None:
            result["limited_use"] = limited_use
        self.sync_combatant_conditions(next_encounter, actor_id, updated_attacker["sheet"])
        self.sync_combatant_conditions(next_encounter, target_id, updated_target["sheet"])
        _support.reconcile_readied_spells(next_encounter, target_id, updated_target["sheet"])
        damage_result = result.get("damage")
        if isinstance(damage_result, dict):
            self.add_concentration_window(
                next_encounter,
                target_id,
                damage_result.get("concentration"),
                next_revision=campaign.revision + 1,
            )
        sneak_attack = dict(result.get("sneak_attack") or {})
        if sneak_attack.get("used"):
            attacker_combatant = next(
                item for item in next_encounter["combatants"] if item.get("actor_id") == actor_id
            )
            flags = dict(attacker_combatant.get("turn_flags") or {})
            flags["sneak_attack_turn_token"] = sneak_attack["turn_token"]
            attacker_combatant["turn_flags"] = flags
        if isinstance(result.get("damage"), dict):
            result["damage"] = {
                key: value for key, value in result["damage"].items() if key != "sheet"
            }
        self.apply_standard_spell_on_hit_mechanics(
            next_encounter,
            result=result,
            attacker_id=actor_id,
            target_id=target_id,
        )
        on_hit_window = self.add_attack_on_hit_window(
            next_encounter,
            result=result,
            attacker_id=actor_id,
            target_id=target_id,
            weapon_id=str(plan.get("weapon_id") or ""),
        )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {"type": "reaction_attack", "choice_id": choice_id, "result": result},
        ][-100:]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=("combat.ready.action.release" if readied is not None
                       else "combat.reaction.attack"),
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                **_support._ruling_status(
                    ("pending_ruling" if on_hit_window is not None else "committed"),
                    "source_or_scene_fact",
                ),
                "result": result,
                "rule_receipts": reaction_receipts,
                **ready_fields,
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated_attacker["sheet"]),
                    notes=_support.validate_character_notes(self.characters.get(actor_id).notes),
                    expected_revision=self.characters.get(actor_id).revision,
                ),
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(updated_target["sheet"]),
                    notes=_support.validate_character_notes(self.characters.get(target_id).notes),
                    expected_revision=self.characters.get(target_id).revision,
                ),
            ],
            rule_receipts=[
                *list(result.get("rule_receipts") or []),
                *reaction_receipts,
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_reaction_defense(
        self,
        campaign_id: str,
        actor_id: str,
        choice_id: str,
        selection: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a post-hit defensive reaction before any damage is rolled."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "choice_id": choice_id,
            "selection": selection,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-reaction-defense:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        window = next(
            (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
            None,
        )
        if (
            not isinstance(window, dict)
            or window.get("kind") != "reaction"
            or window.get("trigger") != "attack_hit_defense"
            or window.get("actor_id") != actor_id
            or window.get("target_id") != actor_id
        ):
            raise _support.CombatEngineError("choice_id is not this actor's attack-defense window")
        resolution_id = str(window.get("resolution_id") or "") or (
            "resolution-"
            + _support.hashlib.sha256(
                f"{campaign_id}:{resolved_branch_id}:combat.attack:{choice_id}".encode("utf-8")
            ).hexdigest()[:32]
        )
        selection_id = str(selection.get("id") or "")
        candidate = next(
            (
                item
                for item in window.get("candidates", [])
                if str(item.get("id") or "") == selection_id
            ),
            None,
        )
        if candidate is None:
            raise _support.CombatEngineError(
                "selection is not one of the defensive reaction choices"
            )
        attacker_id = str(window.get("attacker_id") or "")
        self.require_campaign_actor(campaign_id, attacker_id)
        attacker = self.combat_actor_snapshot(attacker_id)
        target = self.combat_actor_snapshot(actor_id)
        plan = _support.deepcopy(dict(window.get("plan") or {}))
        attack = _support.deepcopy(dict(window.get("attack") or {}))
        next_encounter = _support.deepcopy(encounter)
        used = selection_id not in {"decline", "skip", "pass"}
        defense_kind = str(candidate.get("kind") or "")
        spell_result: dict[str, Any] | None = None
        activity_result: dict[str, Any] | None = None
        song_defense_payment: dict[str, Any] | None = None
        song_defense_reduction = 0
        uncanny_dodge_payment: dict[str, Any] | None = None
        uncanny_dodge_outcome: str | None = None
        if used:
            if defense_kind == "uncanny_dodge":
                legal_uncanny = _support.available_attack_defenses(
                    target,
                    plan=plan,
                    attack=attack,
                    encounter=encounter,
                )
                if not any(
                    str(item.get("id") or "") == selection_id
                    and str(item.get("kind") or "") == "uncanny_dodge"
                    for item in legal_uncanny
                ):
                    raise _support.CombatEngineError(
                        "Uncanny Dodge is not currently legal for this attack"
                    )
            next_encounter = _support.pay_activity_activation(
                next_encounter,
                actor_id_value=actor_id,
                activation_type="reaction",
            )
            if defense_kind == "spell_armor_class_bonus":
                cast_level = selection.get("cast_level")
                if isinstance(cast_level, bool) or not isinstance(cast_level, int):
                    raise _support.CombatEngineError(
                        "Shield selection requires an integer cast_level"
                    )
                cast_option = next(
                    (
                        item
                        for item in candidate.get("cast_options", [])
                        if int(item.get("cast_level", 0) or 0) == cast_level
                    ),
                    None,
                )
                if cast_option is None:
                    raise _support.CombatEngineError(
                        "Shield cast_level is not one of the offered choices"
                    )
                cast_payment = dict(cast_option.get("payment") or {})
                self.require_combat_spell_turn_legal(
                    next_encounter,
                    actor_id=actor_id,
                    payment="reaction",
                    spell_level=1,
                    casting_time="reaction",
                    spent_slot=cast_payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
                )
                spell_result = _support.consume_shield_reaction(
                    target["sheet"],
                    spell_id=str(candidate.get("spell_id") or selection_id),
                    cast_level=cast_level,
                    rules=self.effective_rule_context(
                        campaign_id,
                        facts={
                            "actor_id": actor_id,
                            "spell_id": str(candidate.get("spell_id") or selection_id),
                            "cast_level": cast_level,
                        },
                    ),
                )
                if spell_result.get("status") != "committed":
                    raise _support.CombatEngineError("Shield has an unresolved rule choice")
                target["sheet"] = spell_result["sheet"]
                target["derived"] = self.derive_character_sheet(
                    target["sheet"], character_id=actor_id
                )
                self.record_combat_spell_cast(
                    next_encounter,
                    actor_id=actor_id,
                    spell_id=str(candidate.get("spell_id") or selection_id),
                    spell_level=1,
                    payment="reaction",
                    casting_time="reaction",
                    spent_slot=cast_payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
                    cast_level=cast_level,
                )
            elif defense_kind == "armor_class_bonus":
                activity_result = _support.consume_activity(
                    target["sheet"],
                    activity_id=selection_id,
                    rules=self.effective_rule_context(
                        campaign_id,
                        facts={
                            "actor_id": actor_id,
                            "activity_id": selection_id,
                        },
                    ),
                )
                if activity_result.get("status") != "committed":
                    raise _support.CombatEngineError(
                        "reviewed defensive activity could not be consumed"
                    )
                target["sheet"] = activity_result["sheet"]
                target["derived"] = self.derive_character_sheet(
                    target["sheet"], character_id=actor_id
                )
            elif defense_kind == "scag_song_defense":
                cast_level = selection.get("cast_level")
                if isinstance(cast_level, bool) or not isinstance(cast_level, int):
                    raise _support.CombatEngineError(
                        "Song of Defense requires an integer spell slot level"
                    )
                cast_option = next(
                    (
                        item
                        for item in candidate.get("cast_options", [])
                        if int(item.get("cast_level", 0) or 0) == cast_level
                    ),
                    None,
                )
                if cast_option is None or cast_level < 1 or cast_level > 9:
                    raise _support.CombatEngineError(
                        "Song of Defense slot level is not one of the offered choices"
                    )
                slots = target["sheet"].setdefault("spellcasting", {}).setdefault("spell_slots", {})
                slot = dict(slots.get(str(cast_level)) or slots.get(cast_level) or {})
                try:
                    _support.mutate_bounded_resource(slot, amount=1, direction="spend")
                except ValueError as error:
                    raise _support.CombatEngineError(
                        "Song of Defense spell slot is exhausted"
                    ) from error
                slots[str(cast_level)] = slot
                song_defense_reduction = 5 * cast_level
                song_defense_payment = {
                    "kind": "spell_slot",
                    "slot_level": cast_level,
                    "amount": 1,
                    "reaction": True,
                }
            elif defense_kind == "uncanny_dodge":
                uncanny_dodge_outcome = "half"
                uncanny_dodge_payment = {
                    "kind": "reaction",
                    "feature_id": selection_id,
                    "mechanic_id": _support.CORE_UNCANNY_DODGE_MECHANIC_ID,
                }
            else:
                raise _support.CombatEngineError("defensive reaction kind is not executable")
            if defense_kind not in {"scag_song_defense", "uncanny_dodge"}:
                attack = _support.apply_attack_ac_bonus(
                    attack,
                    bonus=int(candidate.get("bonus", 0) or 0),
                    source_id=selection_id,
                )
        next_encounter = _support.resolve_choice_window(
            next_encounter,
            choice_id=choice_id,
            actor_id_value=actor_id,
            selection={"id": selection_id},
        )
        rule_context = self.effective_rule_context(
            campaign_id,
            facts={"actor_id": attacker_id, "target_id": actor_id, "kind": "attack"},
        )
        updated_attacker, updated_target, result = _support.resolve_attack_damage(
            attacker,
            target,
            plan=plan,
            attack=attack,
            rules=rule_context,
            damage_reduction=song_defense_reduction,
            damage_outcome=uncanny_dodge_outcome,
        )
        self.settle_attack_madness_damage(
            campaign_id=campaign_id,
            target=updated_target,
            encounter=next_encounter,
            result=result,
            rules=rule_context,
            transaction_id=str(choice_id),
        )
        mastery_commit = _support.apply_weapon_mastery_to_encounter(
            next_encounter,
            result,
            attacker_id=attacker_id,
            target_id=actor_id,
        )
        next_encounter = mastery_commit["encounter"]
        if mastery_commit["effect"] is not None:
            result.setdefault("weapon_mastery", {})["committed_effect"] = mastery_commit["effect"]
        result["attack_payment"] = _support.deepcopy(window.get("attack_payment") or {})
        if window.get("ammunition") is not None:
            result["ammunition"] = _support.deepcopy(window["ammunition"])
        result["reaction_defense"] = {
            "used": used,
            "source_type": (
                "spell"
                if used and defense_kind == "spell_armor_class_bonus"
                else "scag_feature"
                if used and defense_kind == "scag_song_defense"
                else "feature"
                if used and defense_kind == "uncanny_dodge"
                else "activity"
            )
            if used
            else None,
            "activity_id": (
                selection_id
                if used and defense_kind in {"armor_class_bonus", "scag_song_defense"}
                else None
            ),
            "spell_id": (
                str(candidate.get("spell_id") or selection_id)
                if used and defense_kind == "spell_armor_class_bonus"
                else None
            ),
            "feature_id": (selection_id if used and defense_kind == "uncanny_dodge" else None),
            "mechanic_id": (
                _support.CORE_UNCANNY_DODGE_MECHANIC_ID
                if used and defense_kind == "uncanny_dodge"
                else None
            ),
            "source_key": (
                str(candidate.get("source_key") or "")
                if used and defense_kind == "uncanny_dodge"
                else None
            ),
            "rule_refs": (
                _support.deepcopy(list(candidate.get("rule_refs") or []))
                if used and defense_kind == "uncanny_dodge"
                else []
            ),
            "source_excerpt": (
                str(candidate.get("source_excerpt") or "")
                if used and defense_kind == "uncanny_dodge"
                else None
            ),
            "cast_level": (
                spell_result.get("cast_level")
                if spell_result
                else cast_level
                if used and defense_kind == "scag_song_defense"
                else None
            ),
            "payment": (
                _support.deepcopy(spell_result.get("payment") or {})
                if spell_result
                else _support.deepcopy(activity_result.get("payment"))
                if activity_result
                else _support.deepcopy(song_defense_payment)
                if used and defense_kind == "scag_song_defense"
                else _support.deepcopy(uncanny_dodge_payment)
                if used and defense_kind == "uncanny_dodge"
                else None
            ),
            "effect_id": spell_result.get("effect_id") if spell_result else None,
            "bonus": int(candidate.get("bonus", 0) or 0) if used else 0,
            "reduction": (
                song_defense_reduction if used and defense_kind == "scag_song_defense" else 0
            ),
            "damage_outcome": (
                uncanny_dodge_outcome if used and defense_kind == "uncanny_dodge" else None
            ),
            "payment_override": _support.deepcopy(song_defense_payment),
            "semantic_solution": (
                {
                    "plan_id": candidate.get("plan_id"),
                    "plan_fingerprint": candidate.get("plan_fingerprint"),
                    "solution_version": candidate.get("solution_version"),
                    "compiled_by": _support.deepcopy(candidate.get("compiled_by")),
                    "citations": _support.deepcopy(candidate.get("citations") or []),
                }
                if used and defense_kind == "armor_class_bonus"
                else None
            ),
        }
        result["rule_receipts"] = [
            *list(result.get("rule_receipts") or []),
            *list(window.get("attack_rule_receipts") or []),
            *(list(spell_result.get("rule_receipts") or []) if spell_result else []),
            *(list(activity_result.get("rule_receipts") or []) if activity_result else []),
        ]
        attacker_combatant = next(
            item for item in next_encounter["combatants"] if item.get("actor_id") == attacker_id
        )
        sneak_attack = dict(result.get("sneak_attack") or {})
        if sneak_attack.get("used"):
            flags = dict(attacker_combatant.get("turn_flags") or {})
            flags["sneak_attack_turn_token"] = sneak_attack["turn_token"]
            attacker_combatant["turn_flags"] = flags
        if result.get("reveals_attacker"):
            self.reveal_attacker_to_target(next_encounter, attacker_id, actor_id)
        self.sync_combatant_conditions(next_encounter, attacker_id, updated_attacker["sheet"])
        self.sync_combatant_conditions(next_encounter, actor_id, updated_target["sheet"])
        _support.reconcile_readied_spells(next_encounter, actor_id, updated_target["sheet"])
        damage_result = result.get("damage")
        if isinstance(damage_result, dict):
            self.add_concentration_window(
                next_encounter,
                actor_id,
                damage_result.get("concentration"),
                next_revision=campaign.revision + 1,
            )
            result["damage"] = {
                key: value for key, value in damage_result.items() if key != "sheet"
            }
        self.apply_standard_spell_on_hit_mechanics(
            next_encounter,
            result=result,
            attacker_id=attacker_id,
            target_id=actor_id,
        )
        on_hit_window = self.add_attack_on_hit_window(
            next_encounter,
            result=result,
            attacker_id=attacker_id,
            target_id=actor_id,
            weapon_id=str(plan.get("weapon_id") or ""),
        )
        spell_resolution_id = str(window.get("spell_resolution_id") or "")
        if spell_resolution_id:
            result["spell_resolution"] = self.advance_spell_attack_resolution(
                next_encounter,
                resolution_id=spell_resolution_id,
                result=result,
            )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "attack_defense_resolved",
                "choice_id": choice_id,
                "selection_id": selection_id,
                "result": result,
            },
        ][-100:]
        semantic_application = window.get("semantic_application_id")
        if semantic_application:
            continuation = next_encounter["semantic_state"]["continuations"][semantic_application]
            continuation["results"][window["semantic_step_id"]] = _support.deepcopy(result)
            continuation["waiting_ids"] = [
                item["id"] for item in next_encounter.get("pending", [])
                if item.get("status", "pending") == "pending"
                and (item["id"] in continuation["waiting_ids"]
                     or item["id"] not in {p.get("id") for p in encounter.get("pending", [])})
            ]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        next_resolution = {
            "id": resolution_id,
            "thread_id": str(window.get("thread_id") or resolution_id),
            "event_sequence": int(window.get("event_sequence") or 1) + 1,
            "type": "combat_attack",
            "operation": "combat.attack",
            "status": "pending" if on_hit_window is not None else "settled",
            "actor_id": attacker_id,
            "audience": {
                "scope": "actors",
                "actor_refs": [attacker_id, actor_id],
                "disclosure": "private",
            },
            "branch_id": resolved_branch_id,
            "campaign_revision": campaign.revision + 1,
            "result": _support.deepcopy(result),
            "pending_choice": (
                {
                    "id": str(on_hit_window["id"]),
                    "kind": str(on_hit_window.get("trigger") or "on_hit_ruling"),
                    "available_actions": [
                        str(item.get("id") or "")
                        for item in on_hit_window.get("candidates", [])
                        if str(item.get("id") or "")
                    ],
                }
                if on_hit_window is not None
                else None
            ),
        }
        resolution_log = [
            _support.deepcopy(item)
            for item in list(next_state.get("resolution_log") or [])
            if not isinstance(item, dict) or str(item.get("id") or "") != resolution_id
        ]
        next_state["resolution_log"] = [*resolution_log, next_resolution][-200:]
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.reaction.defense",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                **_support._ruling_status(
                    ("pending_ruling" if on_hit_window is not None else "committed"),
                    "source_or_scene_fact",
                ),
                "resolution_id": resolution_id,
                "thread_id": next_resolution["thread_id"],
                "event_sequence": next_resolution["event_sequence"],
                "result": result,
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=attacker_id,
                    sheet=_support.validate_character_sheet(updated_attacker["sheet"]),
                    notes=_support.validate_character_notes(self.characters.get(attacker_id).notes),
                    expected_revision=self.characters.get(attacker_id).revision,
                ),
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated_target["sheet"]),
                    notes=_support.validate_character_notes(self.characters.get(actor_id).notes),
                    expected_revision=self.characters.get(actor_id).revision,
                ),
            ],
            rule_receipts=[
                *list(result.get("rule_receipts") or []),
                *_support.core_receipts(
                    rule_context,
                    [
                        "dnd5e.core.mcp.reaction_defense_atomicity",
                        *(
                            [_support.CORE_UNCANNY_DODGE_MECHANIC_ID]
                            if used and defense_kind == "uncanny_dodge"
                            else []
                        ),
                        *(
                            ["dnd5e.core.mcp.shield_attack_reaction_atomicity"]
                            if spell_result is not None
                            else []
                        ),
                    ],
                    "attack.hit.defense.resolve",
                ),
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def character_source_object_attack(
        self,
        character_id: str,
        object_state: dict[str, Any],
        weapon_id: str,
        source_ref: dict[str, Any],
        reason: str,
        advantage: bool = False,
        disadvantage: bool = False,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_campaign_revision: int | None = None,
        idempotency_key: str | None = None,
        object_ruling: dict[str, Any] | None = None,
        attack_ruling: dict[str, Any] | None = None,
        sunlight: dict[str, Any] | None = None,
        weapon_grip: str | None = None,
        use_great_weapon_fighting: bool = False,
    ) -> dict[str, Any]:
        """Attack a source-defined destructible scene object outside combat.

        Underwater ranged attacks require attack_ruling={reason,source_excerpt,
        long_range:bool}, reviewed by the DM against the exact module source.
        long_range means beyond this weapon's normal range and automatically
        misses underwater. No coordinates are inferred from the source.
        """
        from sagasmith_dnd.objects import object_attack_plan, resolve_object_attack

        from .source_objects import approved_attack_context, approved_profile, project_response

        if type(advantage) is not bool or type(disadvantage) is not bool:
            raise ValueError("object attack advantage and disadvantage must be booleans")
        if type(use_great_weapon_fighting) is not bool:
            raise ValueError("use_great_weapon_fighting must be boolean")
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "source object attacks")
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_campaign_revision is None:
            raise ValueError("expected_campaign_revision is required")
        campaign_id = str(current.campaign_id or "")
        campaign = self.campaigns.get(campaign_id)
        resolved_branch_id = self.require_current_branch(campaign_id, None)
        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise ValueError("source object attack requires a reason")
        if not isinstance(object_state, dict):
            raise ValueError("source object must be an object")
        requested = _support.deepcopy(object_state)
        object_id = requested.get("id")
        scene_id = requested.get("scene_id")
        if (
            not isinstance(object_id, str)
            or not object_id.strip()
            or not isinstance(scene_id, str)
            or not scene_id.strip()
        ):
            raise ValueError("source object requires id and scene_id")
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise _support.NeedsRulingError(
                "source objects require 2014 rules", missing=("object.edition",)
            )
        _, exact_source, expanded = self.managed_module_source_ref(
            campaign_id,
            source_ref,
            require_exact=True,
            expected_scene_id=scene_id,
            require_active_module=True,
        )
        if exact_source is None:
            raise AssertionError("exact source object citations always resolve to a managed chunk")
        payload = {
            "character_id": character_id,
            "object_state": requested,
            "weapon_id": weapon_id,
            "source_ref": exact_source,
            "reason": normalized_reason,
            "advantage": bool(advantage),
            "disadvantage": bool(disadvantage),
            "branch_id": resolved_branch_id,
            "object_ruling": object_ruling,
            "attack_ruling": attack_ruling,
            **({"weapon_grip": weapon_grip} if weapon_grip is not None else {}),
            **({"use_great_weapon_fighting": True} if use_great_weapon_fighting else {}),
            **({"sunlight": _support.deepcopy(sunlight)} if sunlight is not None else {}),
        }
        scope = f"source-object-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return project_response(replay, dm=self.is_dm(campaign_id, principal_id))
        if current.revision != expected_revision:
            raise ValueError(
                "character revision conflict: "
                f"expected {expected_revision}, found {current.revision}"
            )
        if campaign.revision != expected_campaign_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_campaign_revision}, found {campaign.revision}"
            )

        attacker = self.combat_actor_snapshot(character_id)
        scene_objects = _support.deepcopy(dict(campaign.state.get("scene_objects") or {}))
        scene_state = _support.deepcopy(dict(scene_objects.get(scene_id) or {}))
        existing = _support.deepcopy(dict(scene_state.get(object_id) or {}))
        assert expanded is not None
        profile, approval, hit_points_before = approved_profile(
            self,
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
            principal_id=principal_id,
            requested=requested,
            existing=existing,
            source_ref=exact_source,
            expanded=expanded,
            ruling=object_ruling,
        )
        if hit_points_before <= 0 or existing.get("destroyed"):
            raise _support.CombatEngineError("source object is already destroyed")
        context_approval = approved_attack_context(
            self,
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
            principal_id=principal_id,
            expanded=expanded,
            source_ref=exact_source,
            character_id=character_id,
            object_id=object_id,
            weapon_id=weapon_id,
            campaign_revision=campaign.revision,
            character_revision=current.revision,
            operation_id=str(idempotency_key),
            advantage=advantage,
            disadvantage=disadvantage,
            ruling=attack_ruling,
        )
        immutable = {
            **profile,
            "hit_point_maximum": profile["hit_points"],
            "profile": profile,
            "profile_approval": approval,
            "source_ref": exact_source,
        }
        rules = self.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": character_id,
                "target_kind": "object",
                "target_id": object_id,
                "scene_id": scene_id,
                "_sunlight": prepare_context(
                    self, sunlight, campaign_id=campaign_id, actor_id=character_id,
                    principal_id=principal_id, mode="attack", subject_kind="object",
                    subject_id=object_id, scene_id=scene_id,
                ),
            },
            branch_id=resolved_branch_id,
        )
        effective_profile = _support.deepcopy(profile)
        if "water_environment" in existing:
            effective_profile["fully_immersed"] = existing["water_environment"]["fully_immersed"]
        plan = object_attack_plan(
            attacker,
            effective_profile,
            weapon_id=weapon_id,
            weapon_grip=weapon_grip,
            use_great_weapon_fighting=use_great_weapon_fighting,
            advantage=advantage,
            disadvantage=disadvantage,
            rules=rules,
            reviewed_long_range=(
                context_approval["ruling"].get("long_range") if context_approval else None
            ),
        )
        attack_roll = _support.roll_attack_action(plan=plan)
        updated_attacker, settled = resolve_object_attack(
            attacker,
            effective_profile,
            hit_points_before,
            plan=plan,
            attack=attack_roll,
            rules=rules,
        )
        if "great_weapon_fighting" in settled:
            attack_roll["great_weapon_fighting"] = _support.deepcopy(
                settled["great_weapon_fighting"]
            )
        next_attacker_sheet = _support.deepcopy(updated_attacker["sheet"])
        ammunition = None
        limited_use = None
        if plan.get("weapon_recharge"):
            next_attacker_sheet, limited_use = _support.consume_weapon_limited_use(
                next_attacker_sheet,
                str(weapon_id),
            )
        if plan.get("ammunition_item_id"):
            next_attacker_sheet, ammunition = _support.consume_weapon_ammunition(
                next_attacker_sheet,
                str(weapon_id),
            )
        hit_points_after = int(
            (settled.get("damage") or {}).get("hit_points_after", hit_points_before)
        )
        object_after = {
            **immutable,
            **({"fully_immersed": effective_profile["fully_immersed"]}
               if "fully_immersed" in effective_profile else {}),
            **({"water_environment": existing["water_environment"]}
               if "water_environment" in existing else {}),
            "hit_points": hit_points_after,
            "destroyed": hit_points_after <= 0,
            "last_attack": {
                "character_id": character_id,
                "weapon_id": str(weapon_id),
                "reason": normalized_reason,
                "attack": _support.deepcopy(attack_roll),
                "damage": _support.deepcopy(settled.get("damage")),
                "damage_filter": _support.deepcopy(profile["damage_filter"]),
                "weapon_traits": settled["weapon_traits"],
                "weapon_trait_requirement_met": (settled.get("damage") or {}).get(
                    "weapon_trait_requirement_met"
                ),
                "context_approval": context_approval,
            },
        }
        scene_state[object_id] = object_after
        scene_objects[scene_id] = scene_state
        next_campaign_state = _support.deepcopy(dict(campaign.state or {}))
        next_campaign_state["scene_objects"] = scene_objects
        next_campaign_state["resolution_log"] = [
            *list(next_campaign_state.get("resolution_log") or []),
            {
                "type": "source_object_attack",
                "actor_id": character_id,
                "object_id": object_id,
                "scene_id": scene_id,
                "attack": _support.deepcopy(attack_roll),
                "damage": _support.deepcopy(settled.get("damage")),
                "hit_points_before": hit_points_before,
                "hit_points_after": hit_points_after,
            },
        ][-100:]
        # Even a miss with no expenditure depends on this exact attacker card.
        # Keep its CAS in the same transaction as object HP and the RNG receipt.
        character_updates = [
            _support.CharacterStateUpdate(
                character_id=character_id,
                sheet=_support.validate_character_sheet(next_attacker_sheet),
                notes=_support.validate_character_notes(current.notes),
                expected_revision=current.revision,
            )
        ]
        updated_character = (
            _support.replace(
                current,
                sheet=_support.validate_character_sheet(next_attacker_sheet),
                notes=_support.validate_character_notes(current.notes),
                revision=current.revision + 1,
            )
            if character_updates
            else current
        )
        updated_character_view = self.character_view(updated_character)

        from .saving_throws import finalize

        next_campaign_state, character_updates, choice_fields, object_receipts = finalize(
            self, campaign, next_campaign_state, character_updates,
            {"character": updated_character_view}, list(settled.get("rule_receipts") or []),
        )

        def source_object_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
                "status": "committed",
                **choice_fields,
                "object": object_after,
                "attack": attack_roll,
                "damage": settled.get("damage"),
                "ammunition": ammunition,
                "limited_use": limited_use,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
                "rule_receipts": object_receipts,
            }
            stream = _support.active_random_stream()
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return project_response(response, dm=self.is_dm(campaign_id, principal_id))

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_campaign_state),
            character_updates=character_updates,
            expected_campaign_revision=campaign.revision,
            operation="character.source_object.attack",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=source_object_response,
            ),
            rule_receipts=object_receipts,
        )
        return source_object_response(list(revisions_result or []))
