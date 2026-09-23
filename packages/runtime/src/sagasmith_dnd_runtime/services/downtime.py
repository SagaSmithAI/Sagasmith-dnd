"""Atomic, source-backed 2014 downtime settlement.

The campaign clock supplies the day key. The DM supplies activity facts and an
exact coin payment; the Domain validates activity-specific requirements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.downtime import (
    completed_activity_days,
    crafting_day,
    crafting_lifestyle_cost_cp,
    lifestyle_daily_cost_cp,
    profession_support_tier,
    record_downtime_day,
    recuperation_outcome,
    research_result,
    select_lifestyle,
    training_progress,
    validate_source_record,
)

from .. import application_support as support

_EXPENSES_REF = "bundled:srd2014/04_Equipment/Expenses.md"
_ADVENTURING_REF = "bundled:srd2014/06_Gameplay/Adventuring.md"
_DISEASE_REF = "bundled:srd2014/08_Gamemastering/Diseases.md"
_POISON_REF = "bundled:srd2014/08_Gamemastering/Poisons.md"
_SOURCE_BY_ACTIVITY = {
    "lifestyle": _EXPENSES_REF,
    "crafting": _ADVENTURING_REF,
    "profession": _ADVENTURING_REF,
    "recuperating": _ADVENTURING_REF,
    "research": _ADVENTURING_REF,
    "training": _ADVENTURING_REF,
}


class DowntimeService:
    """MCP-facing downtime transaction mixin; composed by the application."""

    def character_downtime_settle(
        self,
        campaign_id: str,
        actor_id: str,
        activity: str,
        payload: dict[str, Any],
        *,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a downtime day and its costs/progress atomically.

        ``payload`` carries activity facts, a source excerpt, hours, and exact
        coin payment. Day identity always comes from authoritative game time.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required for downtime settlement")
        if not isinstance(payload, dict):
            raise ValueError("downtime payload must be an object")
        activity_key = str(activity or "").strip().casefold()
        if activity_key not in _SOURCE_BY_ACTIVITY:
            raise ValueError("unsupported 2014 downtime activity")
        source_ref = str(payload.get("source_ref") or "").strip()
        excerpt = str(payload.get("source_excerpt") or "").strip()
        if source_ref != _SOURCE_BY_ACTIVITY[activity_key]:
            raise support.CombatEngineError("downtime source must match the bundled 2014 chapter")
        source_path = (
            Path(__file__).parents[5]
            / "skills/full/skills/dnd-dm/srd/references-2014-en"
            / (
                "04_Equipment/Expenses.md"
                if activity_key == "lifestyle"
                else "06_Gameplay/Adventuring.md"
            )
        )
        try:
            bundled_source = " ".join(source_path.read_text(encoding="utf-8").casefold().split())
        except OSError as error:
            raise support.CombatEngineError("bundled downtime source is unavailable") from error
        normalized_excerpt = " ".join(excerpt.casefold().split())
        if normalized_excerpt not in bundled_source:
            raise support.CombatEngineError(
                "downtime source excerpt is not present in the bundled chapter"
            )
        try:
            source = support.deepcopy(validate_source_record(source_ref, excerpt))
        except ValueError as error:
            raise support.CombatEngineError(str(error)) from error

        resolved_branch = self.require_current_branch(campaign_id, branch_id)
        request_data = support.deepcopy(payload)
        request_data["source_ref"] = source_ref
        request_data["source_excerpt"] = excerpt
        request = {
            "actor_id": actor_id,
            "activity": activity_key,
            "payload": request_data,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": resolved_branch,
        }
        scope = f"character-downtime:{campaign_id}:{resolved_branch}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("downtime activities require a 2014 campaign")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        if dict(state.get("combat") or {}).get("active"):
            raise support.CombatEngineError("downtime cannot be settled during active combat")
        actor = self.require_campaign_actor(campaign_id, actor_id)
        if actor.revision != expected_actor_revision:
            raise ValueError(
                "actor revision conflict: "
                f"expected {expected_actor_revision}, found {actor.revision}"
            )
        if actor.sheet.get("edition") != "2014":
            raise support.CombatEngineError("downtime activities require a 2014 character")
        participant_records = {actor_id: actor}
        if activity_key == "crafting":
            collaborator_ids = payload.get("collaborator_ids", [actor_id])
            if (
                not isinstance(collaborator_ids, list)
                or not collaborator_ids
                or any(not isinstance(item, str) or not item for item in collaborator_ids)
                or actor_id not in collaborator_ids
                or len(collaborator_ids) != len(set(collaborator_ids))
            ):
                raise support.CombatEngineError(
                    "crafting collaborator_ids must uniquely include the acting character"
                )
            collaborator_revisions = dict(payload.get("collaborator_revisions") or {})
            if set(collaborator_revisions) != set(collaborator_ids) - {actor_id}:
                raise support.CombatEngineError(
                    "collaborator_revisions must provide each helper's current revision"
                )
            for collaborator_id in collaborator_ids:
                if collaborator_id == actor_id:
                    continue
                collaborator = self.require_campaign_actor(campaign_id, collaborator_id)
                if collaborator.revision != collaborator_revisions[collaborator_id]:
                    raise ValueError(
                        f"collaborator revision conflict for {collaborator_id}: "
                        f"expected {collaborator_revisions[collaborator_id]}, "
                        f"found {collaborator.revision}"
                    )
                if collaborator.sheet.get("edition") != "2014":
                    raise support.CombatEngineError("crafting requires 2014 collaborators")
                participant_records[collaborator_id] = collaborator
        elapsed = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0))
        day_ticks = support.game_time_ticks("day")
        day_key = str(elapsed // day_ticks)
        hours = payload.get("hours")
        if type(hours) is not int or not 0 <= hours <= 24:
            raise support.CombatEngineError("hours must be an integer from 0 through 24")

        downtime_events = [
            event
            for event in state.get("resolution_log", [])
            if event.get("type") == "downtime_settlement"
        ]
        actor_events = [event for event in downtime_events if event.get("actor_id") == actor_id]
        actor_downtime = support.deepcopy(
            dict(actor_events[-1].get("downtime_state") or {}) if actor_events else {}
        )
        days_state = support.deepcopy(dict(actor_downtime.get("activities") or {}))
        if activity_key == "lifestyle" and any(
            item.get("day_key") == day_key
            for item in actor_downtime.get("lifestyle_history", [])
            if isinstance(item, dict)
        ):
            raise support.CombatEngineError("lifestyle already recorded for campaign day")
        old_days = list(days_state.get(activity_key, {}).get("days") or [])
        if any(item.get("day_key") == day_key for item in old_days if isinstance(item, dict)):
            raise support.CombatEngineError("downtime activity already recorded for campaign day")
        activity_state = support.deepcopy(days_state.get(activity_key, {}))
        if activity_key != "lifestyle":
            activity_state = record_downtime_day(
                activity_state,
                activity=activity_key,
                day_key=day_key,
                hours=hours,
                source_ref=source["source_ref"],
                source_excerpt=source["source_excerpt"],
            )
        counted_days = (
            0
            if activity_key == "lifestyle"
            else completed_activity_days(activity_state, activity_key)
        )
        result: dict[str, Any] = {"activity": activity_key, "day_key": day_key, "hours": hours}
        cost_cp = 0
        character_sheet = support.deepcopy(actor.sheet)
        material_receipt: list[dict[str, Any]] = []
        if activity_key == "lifestyle":
            days = payload.get("days", 1)
            lifestyle = str(payload.get("lifestyle") or "")
            selected = select_lifestyle(
                lifestyle,
                days=days,
                source_ref=source["source_ref"],
                source_excerpt=source["source_excerpt"],
                aristocratic_cost_cp=payload.get("aristocratic_cost_cp"),
            )
            cost_cp = selected["cost_cp"]
            actor_downtime["active_lifestyle"] = selected
            actor_downtime.setdefault("lifestyle_history", []).append(
                {
                    "day_key": day_key,
                    **selected,
                }
            )
            result.update(selected)
        elif activity_key == "crafting":
            tools = str(payload.get("required_tools") or "").casefold()
            market_remaining = payload.get("market_value_remaining_gp")
            if hours < 8:
                craft = {"progress_gp": 0, "materials_cost_cp": 0}
            else:
                same_place = payload.get("collaborating_in_same_place") is True
                if len(participant_records) > 1 and not same_place:
                    raise support.CombatEngineError(
                        "multiple crafters must be confirmed as working in the same place"
                    )
                proficient_tools = []
                for participant in participant_records.values():
                    participant_tools = {
                        str(item).casefold()
                        for item in participant.sheet["traits"]["proficiencies"]["tools"]
                    }
                    proficient_tools.append(tools if tools in participant_tools else "")
                material_ids = payload.get("materials_item_ids")
                if (
                    not isinstance(material_ids, list)
                    or not material_ids
                    or any(not isinstance(item, str) or not item for item in material_ids)
                    or len(material_ids) != len(set(material_ids))
                ):
                    raise support.CombatEngineError(
                        "crafting requires raw-material inventory item ids"
                    )
                material_items = [
                    next(
                        (
                            item
                            for item in character_sheet["inventory"]["items"]
                            if item["id"] == item_id
                        ),
                        None,
                    )
                    for item_id in material_ids
                ]
                if any(item is None for item in material_items):
                    raise support.CombatEngineError(
                        "every raw-material item must be in the acting crafter inventory"
                    )
                materials_available = sum(
                    int(item["price_cp"]) * int(item["quantity"]) for item in material_items
                )
                craft = crafting_day(
                    market_value_remaining_gp=market_remaining,
                    qualifying_crafters=len(participant_records),
                    required_tools=tools,
                    proficient_tools_by_crafter=proficient_tools,
                    materials_available_cp=materials_available,
                    collaborating_in_same_place=same_place,
                    facility_required=payload.get("facility_required") is True,
                    facility_available=payload.get("facility_available") is True,
                )
                materials_remaining = craft["materials_cost_cp"]
                for item_id in material_ids:
                    item = next(
                        item
                        for item in character_sheet["inventory"]["items"]
                        if item["id"] == item_id
                    )
                    unit_price = int(item["price_cp"])
                    if unit_price <= 0:
                        raise support.CombatEngineError(
                            "raw materials need a positive inventory price"
                        )
                    quantity = min(
                        int(item["quantity"]),
                        (materials_remaining + unit_price - 1) // unit_price,
                    )
                    if quantity:
                        character_sheet, consumed = support.remove_inventory_item(
                            character_sheet, item_id, quantity
                        )
                        material_receipt.append(consumed)
                        materials_remaining -= unit_price * quantity
                    if materials_remaining <= 0:
                        break
                if materials_remaining > 0:
                    raise support.CombatEngineError("insufficient raw materials in inventory")
            lifestyle = str(payload.get("lifestyle") or "modest")
            lifestyle_cost = (
                crafting_lifestyle_cost_cp(lifestyle) * len(participant_records)
                if hours >= 8
                else 0
            )
            cost_cp += lifestyle_cost
            result.update(
                {
                    **craft,
                    "lifestyle_cost_cp": lifestyle_cost,
                    "collaborator_ids": list(participant_records),
                    "materials_consumed": material_receipt,
                }
            )
            activity_state["market_value_remaining_gp"] = max(
                0, int(market_remaining) - craft["progress_gp"]
            )
        elif activity_key == "profession":
            if hours >= 8:
                tier = profession_support_tier(
                    organization_employment=payload.get("organization_employment") is True,
                    performance_proficient=payload.get("performance_proficient") is True,
                    performance_used=payload.get("performance_used") is True,
                )
                actor_downtime["active_lifestyle"] = tier
                result["supported_lifestyle"] = tier
        elif activity_key == "recuperating":
            if counted_days < 3:
                result.update({"status": "progressed", "qualifying_days": counted_days})
            elif hours >= 8:
                stream = support.active_random_stream()
                if stream is None:
                    with self.campaign_random_context(
                        campaign_id,
                        "character.downtime.recuperation",
                        {"idempotency_key": idempotency_key},
                    ):
                        return self.character_downtime_settle(
                            campaign_id,
                            actor_id,
                            activity_key,
                            payload,
                            principal_id=principal_id,
                            expected_revision=expected_revision,
                            expected_actor_revision=expected_actor_revision,
                            branch_id=resolved_branch,
                            idempotency_key=idempotency_key,
                        )
                actor_snapshot = self.combat_actor_snapshot(actor_id)
                actor_snapshot["sheet"] = support.deepcopy(actor.sheet)
                actor_snapshot["derived"] = derive_character_sheet(actor.sheet)
                save = support.resolve_actor_check(
                    actor_snapshot,
                    kind="save",
                    ability="constitution",
                    dc=15,
                    rules=self.effective_rule_context(campaign_id, branch_id=resolved_branch),
                    rng=support.active_random_stream(),
                    ruleset="2014",
                )
                current_effect_ids = [
                    str(item.get("id"))
                    for item in actor.sheet.get("effects", [])
                    if item.get("active") is True
                    and dict(item.get("metadata") or {}).get("prevents_hp_recovery") is True
                ]
                current_conditions = [
                    str(item.get("id"))
                    for item in actor.sheet.get("effects", [])
                    if item.get("active") is True
                    and (
                        (
                            item.get("kind") == "disease_state"
                            and item.get("source") == _DISEASE_REF
                            and isinstance(
                                dict(item.get("metadata") or {}).get("disease_state"), dict
                            )
                        )
                        or (
                            item.get("kind") == "poison"
                            and item.get("source") == _POISON_REF
                            and isinstance(
                                dict(item.get("metadata") or {}).get("poison_state"), dict
                            )
                        )
                        or dict(item.get("metadata") or {}).get("condition_kind")
                        in {"disease", "poison"}
                    )
                ]
                recuperation = recuperation_outcome(
                    qualifying_days=counted_days,
                    save_success=bool(save.get("success")),
                    choice=payload.get("choice"),
                    effect_id=payload.get("effect_id"),
                    blocking_effect_ids=current_effect_ids,
                    condition_id=payload.get("condition_id"),
                    condition_kind=payload.get("condition_kind"),
                    current_condition_ids=current_conditions,
                )
                if (
                    recuperation.get("success")
                    and recuperation["choice"]["kind"] == "end_hp_recovery_effect"
                ):
                    selected = next(
                        item
                        for item in character_sheet["effects"]
                        if item["id"] == payload["effect_id"]
                    )
                    selected["active"] = False
                    selected["ended_reason"] = "recuperated"
                elif recuperation.get("success"):
                    marker = {
                        "id": f"recuperation-advantage-{day_key}-{actor_id}",
                        "name": "Recuperation advantage",
                        "kind": "manual",
                        "source": _ADVENTURING_REF,
                        "active": True,
                        "duration": {"period": "hour", "remaining": 24},
                        "changes": [
                            {"path": "rolls.saving_throw.advantage", "mode": "set", "value": True}
                        ],
                        "metadata": {
                            "recuperation_advantage_against": recuperation["choice"],
                        },
                    }
                    character_sheet["effects"].append(marker)
                result.update({"recuperation": recuperation, "save": save})
        elif activity_key == "research":
            plan = dict(payload.get("plan") or {})
            _, plan["source_ref"], plan_expanded = self.managed_module_source_ref(
                campaign_id,
                str(plan.get("source_ref") or ""),
                require_exact=True,
                require_active_module=True,
            )
            assert plan_expanded is not None
            plan["source_excerpt"] = self.managed_module_source_excerpt(
                plan_expanded,
                plan.get("source_excerpt"),
                field="research plan source_excerpt",
                minimum_length=10,
            )
            if counted_days >= int(plan.get("required_days", 1)):
                required_checks = set(plan.get("required_check_ids") or [])
                passed_checks = set(plan.get("passed_check_ids") or [])
            else:
                required_checks = set(plan.get("required_check_ids") or [])
                passed_checks = set(plan.get("passed_check_ids") or [])
            research_complete = (
                plan.get("available") is True
                and plan.get("restrictions_satisfied") is True
                and counted_days >= int(plan.get("required_days", 1))
                and required_checks <= passed_checks
            )
            if research_complete:
                normalized_information_source_ref, _, info_expanded = (
                    self.managed_module_source_ref(
                    campaign_id,
                    str(plan.get("information_source_ref") or ""),
                    require_exact=True,
                    require_active_module=True,
                    )
                )
                assert info_expanded is not None
                plan["information_source_ref"] = normalized_information_source_ref
                plan["information_source_excerpt"] = self.managed_module_source_excerpt(
                    info_expanded,
                    plan.get("information_source_excerpt"),
                    field="research information source_excerpt",
                    minimum_length=10,
                )
            research = research_result(
                available=plan.get("available") is True,
                required_days=plan.get("required_days"),
                qualifying_days=counted_days,
                restrictions_satisfied=plan.get("restrictions_satisfied") is True,
                required_check_ids=list(plan.get("required_check_ids") or []),
                passed_check_ids=list(plan.get("passed_check_ids") or []),
                lifestyle=str(payload.get("lifestyle") or "modest"),
                plan_source_ref=str(plan.get("source_ref") or ""),
                plan_source_excerpt=str(plan.get("source_excerpt") or ""),
                information_source_ref=plan.get("information_source_ref")
                if research_complete
                else None,
                information_source_excerpt=plan.get("information_source_excerpt")
                if research_complete
                else None,
            )
            research_day_cost = 100 if hours >= 8 else 0
            lifestyle_day_cost = (
                lifestyle_daily_cost_cp(str(payload.get("lifestyle") or "modest"))
                if hours >= 8
                else 0
            )
            research["research_cost_cp"] = research_day_cost
            research["lifestyle_cost_cp"] = lifestyle_day_cost
            cost_cp = research_day_cost + lifestyle_day_cost
            result["research"] = research
        elif activity_key == "training":
            plan = dict(payload.get("plan") or {})
            instructor_id = str(plan.get("instructor_id") or "")
            instructor = self.require_campaign_actor(campaign_id, instructor_id)
            if plan.get("instructor_willing") is not True:
                raise support.CombatEngineError("downtime training requires a willing instructor")
            training = training_progress(
                qualifying_days=counted_days - (1 if hours >= 8 else 0),
                days_to_add=1 if hours >= 8 else 0,
                instructor_id=instructor_id,
                instructor_persisted=instructor is not None,
                instructor_willing=True,
                target_kind=str(plan.get("target_kind") or ""),
                target_id=str(plan.get("target_id") or ""),
                required_check_ids=list(plan.get("required_check_ids") or []),
                passed_check_ids=list(plan.get("passed_check_ids") or []),
                lifestyle=str(payload.get("lifestyle") or "modest"),
            )
            cost_cp = training["training_cost_cp"] + training["lifestyle_cost_cp"]
            if training["complete"]:
                if training["target"]["kind"] == "language":
                    languages = character_sheet["traits"]["languages"]
                    if training["target"]["id"] not in languages:
                        languages.append(training["target"]["id"])
                else:
                    tools = character_sheet["traits"]["proficiencies"]["tools"]
                    if training["target"]["id"] not in tools:
                        tools.append(training["target"]["id"])
            result["training"] = training

        if activity_key != "lifestyle":
            days_state[activity_key] = activity_state
            actor_downtime["activities"] = days_state
        payment = payload.get("payment") or {}
        wallet = support.deepcopy(character_sheet["inventory"]["wallet"])
        paid = self.spend_exact_wallet_payment(wallet, payment, required_cp=cost_cp)
        character_sheet["inventory"]["wallet"] = wallet
        result["cost_cp"] = cost_cp
        result["payment"] = paid
        receipt = {
            "mechanic_id": f"dnd5e.core.gameplay.downtime.{activity_key}.2014",
            "event": "character.downtime.settle",
            "operations": [{"op": "builtin.core_provider"}],
            "citations": [{"source": source_ref, "edition": "2014", "excerpt": excerpt}],
            "ruleset_fingerprint": self.effective_rule_context(
                campaign_id, branch_id=resolved_branch
            ).fingerprint,
            "facts": {
                "actor_id": actor_id,
                "activity": activity_key,
                "day_key": day_key,
                "hours": hours,
            },
        }
        character_updates = [
            support.CharacterStateUpdate(
                character_id=actor.id,
                sheet=support.validate_character_sheet(character_sheet),
                notes=support.validate_character_notes(actor.notes),
                expected_revision=actor.revision,
            )
        ]
        for participant_id, participant in participant_records.items():
            if participant_id == actor_id:
                continue
            character_updates.append(
                support.CharacterStateUpdate(
                    character_id=participant.id,
                    sheet=support.validate_character_sheet(support.deepcopy(participant.sheet)),
                    notes=support.validate_character_notes(participant.notes),
                    expected_revision=participant.revision,
                )
            )
        resolution_id = f"resolution-{support.uuid4().hex}"
        old_log = list(state.get("resolution_log") or [])
        downtime_events = [event for event in old_log if event.get("type") == "downtime_settlement"]
        ordinary_events = [
            event for event in old_log if event.get("type") != "downtime_settlement"
        ][-100:]
        state["resolution_log"] = [
            *ordinary_events,
            *downtime_events,
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "downtime_settlement",
                "operation": "character.downtime.settle",
                "actor_id": actor_id,
                "audience": {"scope": "actors", "actor_refs": [actor_id], "disclosure": "private"},
                "branch_id": resolved_branch,
                "campaign_revision": campaign.revision + 1,
                "result": result,
                "downtime_state": actor_downtime,
            },
        ]
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.downtime.settle",
            principal_id=principal_id,
            branch_id=resolved_branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "rule_receipts": [receipt],
            },
            character_updates=character_updates,
            rule_receipts=[receipt],
            expected_campaign_revision=expected_revision,
        )
