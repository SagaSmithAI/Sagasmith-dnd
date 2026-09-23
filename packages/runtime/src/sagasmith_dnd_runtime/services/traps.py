"""Source-bound trap checks and one atomic 2014 failure settlement path."""

from __future__ import annotations

from typing import Any

from sagasmith_dnd.combat_engine import resolve_fall_to_sheet
from sagasmith_dnd.conditions import effect_is_immune, effect_is_suspended_by_petrification
from sagasmith_dnd.engine import resolve_attack, roll
from sagasmith_dnd.traps import (
    build_poison_needle_condition_effect,
    source_trap_profile,
    transition_trap_state,
    validate_source_pit_depth,
)

from .. import application_support as _support

_SOURCE_PIT_PROFILES = {
    "srd5.1.simple_pit",
    "srd5.1.hidden_pit",
    "srd5.1.spiked_simple_pit",
    "srd5.1.spiked_hidden_pit",
    "srd5.1.poisoned_spiked_simple_pit",
    "srd5.1.poisoned_spiked_hidden_pit",
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}
_SOURCE_LOCKING_PIT_PROFILES = {
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}
_SOURCE_TRIGGER_FACT_KINDS = {
    "srd5.1.fire_breathing_statue": ("pressure_plate_weight", "plate_id"),
    "srd5.1.poison_darts": ("pressure_plate_weight", "plate_id"),
    "srd5.1.poison_needle": ("lock_opened", "lock_id"),
    "srd5.1.simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.collapsing_roof": ("knock_wedged_beam", "beam_id"),
}


class TrapService:
    def source_bound_trap_transition(
        self,
        campaign_id: str,
        trap_id: str,
        action: str,
        source_ref: str,
        source_excerpt: str,
        profile: dict[str, Any],
        actor_id: str,
        *,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        method: str | None = None,
        target_ids: list[str] | None = None,
        area_confirmed: bool | None = None,
        trap_depth_ft: int | None = None,
        trigger_fact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve source-bound trap detection and explicit no-roll bypass actions.

        The request selects a fixed profile id. Rules are loaded from the domain
        SRD registry, never from caller-provided DC, damage, or effect values.
        Trigger settlement stays fail-closed until a trap's complete target,
        area, condition, and object mechanics can be applied atomically.
        """
        if action not in {
            "detect",
            "passive_detect",
            "disable",
            "bypass",
            "trigger",
            "escape",
        }:
            raise _support.CombatEngineError(
                "trap action must be detect, passive_detect, disable, bypass, trigger, or escape"
            )
        try:
            normalized_profile = source_trap_profile(profile, source_excerpt)
        except ValueError as exc:
            raise _support.CombatEngineError(str(exc)) from exc
        if action == "bypass":
            if method not in normalized_profile["bypass_methods"]:
                raise _support.CombatEngineError(
                    "bypass method is not authorized by this source-bound trap profile"
                )
        elif action == "disable":
            disable_spec = normalized_profile.get("disable")
            if not isinstance(disable_spec, dict):
                raise _support.CombatEngineError("this source-bound trap has no disable check")
            methods = [str(disable_spec.get("tool") or "")]
            alternative = disable_spec.get("no_tool_alternative")
            if isinstance(alternative, dict) and alternative.get("requires_edged_tool"):
                methods.append("edged_tool")
            if method not in methods:
                raise _support.CombatEngineError(
                    "disable method must match this source-bound trap profile"
                )
            if disable_spec.get("failed_check") == "trigger" and area_confirmed is not True:
                raise _support.CombatEngineError(
                    "failed disable requires an explicit confirmed source-defined area fact"
                )
            failed_area_trigger = disable_spec.get("failed_check") == "trigger"
            if target_ids is not None and not failed_area_trigger:
                raise _support.CombatEngineError("disable does not accept target_ids")
        elif method is not None:
            raise _support.CombatEngineError("method is accepted only for trap bypass")
        if action in {"trigger", "escape"}:
            if action == "trigger" and area_confirmed is not True:
                raise _support.CombatEngineError(
                    "trap trigger requires an explicit confirmed source-defined area fact"
                )
            if action == "escape" and (target_ids is not None or area_confirmed is not None):
                raise _support.CombatEngineError("escape does not accept target or area facts")
            if action == "trigger" and normalized_profile["profile_id"] in _SOURCE_PIT_PROFILES:
                try:
                    validate_source_pit_depth(normalized_profile, trap_depth_ft)
                except ValueError as exc:
                    raise _support.CombatEngineError(str(exc)) from exc
            elif trap_depth_ft is not None:
                raise _support.CombatEngineError(
                    "trap_depth_ft is accepted only for source-bound pit triggers"
                )
            expected_fact = _SOURCE_TRIGGER_FACT_KINDS.get(normalized_profile["profile_id"])
            if action == "trigger" and expected_fact is not None:
                if not isinstance(trigger_fact, dict):
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger requires exact source facts"
                    )
                if (
                    normalized_profile["profile_id"] == "srd5.1.poison_needle"
                    and trigger_fact.get("kind") == "lock_pick_failed"
                ):
                    fact_kind, identity_key = "lock_pick_failed", "lock_id"
                    expected_keys = {"kind", "scene_id", identity_key, "pick_succeeded"}
                else:
                    fact_kind, identity_key = expected_fact
                    expected_keys = {"kind", "scene_id", identity_key}
                    if fact_kind == "pressure_plate_weight":
                        expected_keys.add("weight_lb")
                    elif fact_kind == "lock_opened":
                        expected_keys.add("proper_key_used")
                    elif fact_kind == "knock_wedged_beam":
                        expected_keys.add("action_spent")
                if set(trigger_fact) != expected_keys:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger requires exact {fact_kind} facts"
                    )
                if trigger_fact.get("kind") != fact_kind:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger fact kind must be {fact_kind}"
                    )
                component_id = trigger_fact.get(identity_key)
                if not isinstance(component_id, str) or component_id != trap_id:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger fact must identify "
                        "this trap component"
                    )
                if fact_kind == "pressure_plate_weight":
                    weight = trigger_fact.get("weight_lb")
                    if (
                        isinstance(weight, bool)
                        or not isinstance(weight, (int, float))
                        or weight <= 20
                    ):
                        raise _support.CombatEngineError(
                            "pressure plate trigger requires a confirmed weight greater than 20 lb"
                        )
                elif (
                    fact_kind == "lock_opened"
                    and trigger_fact.get("proper_key_used") is not False
                ):
                    raise _support.CombatEngineError(
                        "Poison Needle triggers only when its lock opens without the proper key"
                    )
                elif (
                    fact_kind == "lock_pick_failed"
                    and trigger_fact.get("pick_succeeded") is not False
                ):
                    raise _support.CombatEngineError(
                        "Poison Needle triggers only on a failed lock-pick attempt"
                    )
                elif (
                    fact_kind == "knock_wedged_beam"
                    and trigger_fact.get("action_spent") is not True
                ):
                    raise _support.CombatEngineError(
                        "Collapsing Roof beam trigger requires confirmation that the "
                        "action was spent"
                    )
            elif action == "trigger" and expected_fact is None and trigger_fact is not None:
                raise _support.CombatEngineError(
                    "trigger_fact is not defined for this source-bound trap profile"
                )
            if action == "trigger" and normalized_profile["profile_id"] not in {
                "srd5.1.poison_darts",
                "srd5.1.falling_net",
                "srd5.1.poison_needle",
                "srd5.1.collapsing_roof",
                "srd5.1.fire_breathing_statue",
                *_SOURCE_PIT_PROFILES,
            }:
                raise _support.CombatEngineError(
                    "this source-bound trap trigger has no complete runtime settlement"
                )
            if action == "escape" and normalized_profile["profile_id"] not in {
                "srd5.1.falling_net",
                *_SOURCE_LOCKING_PIT_PROFILES,
            }:
                raise _support.CombatEngineError(
                    "this source-bound trap has no supported escape procedure"
                )
        elif action == "disable" and trigger_fact is not None:
            raise _support.CombatEngineError(
                "trigger_fact is accepted only for direct trap trigger"
            )
        elif action not in {"disable"} and (
            target_ids is not None
            or area_confirmed is not None
            or trap_depth_ft is not None
            or trigger_fact is not None
        ):
            raise _support.CombatEngineError(
                "target_ids and area_confirmed are accepted only for trap trigger"
            )
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_campaign_actor(campaign_id, actor_id)
        self.require_write_contract(expected_revision, idempotency_key)
        if action == "disable" and normalized_profile["profile_id"] not in {
            "srd5.1.falling_net",
            "srd5.1.poison_needle",
            "srd5.1.collapsing_roof",
        }:
            raise _support.CombatEngineError(
                "this source-bound trap disable settlement is unsupported"
            )
        resolved_branch = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "trap_id": trap_id,
            "action": action,
            "source_ref": source_ref,
            "source_excerpt": source_excerpt,
            "profile": normalized_profile,
            "actor_id": actor_id,
            "method": method,
            "target_ids": target_ids,
            "area_confirmed": area_confirmed,
            "trap_depth_ft": trap_depth_ft,
            "trigger_fact": trigger_fact,
        }
        scope = f"trap-state:{campaign_id}:{resolved_branch}:{principal_id}"
        replay_payload = {"payload": payload, "branch_id": resolved_branch}
        replay = self.replay_idempotent(scope, idempotency_key, replay_payload)
        if replay is not None:
            return replay

        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: expected "
                f"{expected_revision}, found {campaign.revision}"
            )
        if _support.active_random_stream() is None:
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign.state,
                operation="trap.state.transition",
                idempotency_key=str(idempotency_key or ""),
                campaign_revision=campaign.revision,
            )
            with _support.use_random_stream(stream):
                return self.source_bound_trap_transition(
                    campaign_id,
                    trap_id,
                    action,
                    source_ref,
                    source_excerpt,
                    profile,
                    actor_id,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch,
                    idempotency_key=idempotency_key,
                    method=method,
                    target_ids=target_ids,
                    area_confirmed=area_confirmed,
                    trap_depth_ft=trap_depth_ft,
                    trigger_fact=trigger_fact,
                )
        stream = _support.active_random_stream()
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
        )
        if (
            stream.campaign_id != campaign_id
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
        ):
            raise _support.CombatEngineError(
                "trap checks require the current campaign random snapshot"
            )

        exact_source, _, expanded = self.managed_module_source_ref(
            campaign_id,
            source_ref,
            require_exact=True,
            require_active_module=True,
        )
        assert expanded is not None
        self.managed_module_source_excerpt(
            expanded,
            source_excerpt,
            field="trap source_excerpt",
            minimum_length=10,
        )
        actor = self.combat_actor_snapshot(actor_id)
        encounter = dict(dict(campaign.state or {}).get("combat") or {})
        ruleset = (
            self.encounter_rules_edition(campaign_id, encounter)
            if encounter
            else self.campaign_rules_edition(campaign_id)
        )
        if ruleset != "2014":
            raise _support.CombatEngineError("source-bound trap profiles require the 2014 ruleset")
        if action in {"trigger", "escape", "disable"}:
            if (
                action == "trigger"
                and trigger_fact is not None
                and trigger_fact.get("scene_id") != str(expanded["scene"]["id"])
            ):
                raise _support.CombatEngineError(
                    "trap trigger fact scene_id does not match the source-defined scene"
                )
            return self._source_bound_trap_effect_transition(
                campaign=campaign,
                campaign_id=campaign_id,
                trap_id=trap_id,
                action=action,
                exact_source=exact_source,
                scene_id=str(expanded["scene"]["id"]),
                normalized_profile=normalized_profile,
                actor_id=actor_id,
                method=method,
                target_ids=target_ids,
                area_confirmed=area_confirmed,
                trap_depth_ft=trap_depth_ft,
                trigger_fact=trigger_fact,
                encounter=encounter,
                ruleset=ruleset,
                stream=stream,
                principal_id=principal_id,
                branch_id=resolved_branch,
                idempotency_key=idempotency_key,
                scope=scope,
                replay_payload=replay_payload,
            )
        checks: list[dict[str, Any]] = []
        if action in {"detect", "passive_detect"}:
            detection = normalized_profile["detect"]
            if action == "passive_detect":
                dc = detection.get("passive_dc")
                if dc is None:
                    raise _support.CombatEngineError(
                        "this source-bound trap profile has no passive detection rule"
                    )
                specs = [{"ability": "perception", "dc": dc}]
            else:
                specs = list(detection["active"])
            for check_spec in specs:
                passive = action == "passive_detect"
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=resolved_branch,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": check_spec["ability"],
                        "dc": check_spec["dc"],
                        "passive": passive,
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": action,
                    },
                )
                resolved_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=check_spec["ability"],
                    dc=check_spec["dc"],
                    passive=passive,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                resolved_check = dict(resolved_check)
                resolved_check["ability"] = check_spec["ability"]
                resolved_check["passive"] = passive
                checks.append(resolved_check)
                if checks[-1].get("success") is not True:
                    break
        check = checks[-1] if checks else {}

        next_state = _support.deepcopy(campaign.state)
        trap_state = dict(next_state.get("trap_state") or {})
        if action == "detect" and checks and all(item.get("success") is True for item in checks):
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="detect",
            )
        if action == "passive_detect" and check.get("success") is True:
            if len(normalized_profile["detect"]["active"]) == 1:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="detect",
                )
            else:
                instances = dict(trap_state.get("traps") or {})
                instance = dict(instances.get(trap_id) or {})
                if instance.get("source_ref") not in (None, exact_source):
                    raise _support.CombatEngineError("trap instance is bound to a different source")
                instance.update({"source_ref": exact_source, "status": "armed", "suspected": True})
                instances[trap_id] = instance
                trap_state["traps"] = instances
        if action == "bypass":
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="bypass",
                success=True,
            )
            trap_state["traps"][trap_id]["bypass_method"] = method

        result: dict[str, Any] = {"check": check, "checks": checks, "action": action}
        character_updates: list[Any] = []
        rule_receipts = [receipt for item in checks for receipt in item.get("rule_receipts", [])]
        trap_state.setdefault("attempts", [])
        trap_state["attempts"] = [
            *list(trap_state["attempts"]),
            {
                "trap_id": trap_id,
                "source_ref": exact_source,
                "actor_id": actor_id,
                "action": action,
                "check": check,
                "checks": checks,
                "method": method,
                "campaign_revision": campaign.revision + 1,
            },
        ][-100:]
        next_state["trap_state"] = trap_state
        response_fields = {
            "status": "committed",
            "trap_id": trap_id,
            "action": action,
            "trap": trap_state.get("traps", {}).get(trap_id),
            "check": check,
            "checks": checks,
            **(
                {"failure_settlement": result["failure_settlement"]}
                if "failure_settlement" in result
                else {}
            ),
        }
        stream_receipt = stream.receipt() if stream.draw_count else None
        if stream_receipt is not None:
            response_fields["random_stream_receipt"] = stream_receipt
        return self.commit_campaign_state(
            campaign,
            next_state,
            operation="trap.state.transition",
            principal_id=principal_id,
            branch_id=resolved_branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=replay_payload,
            response_fields=response_fields,
            character_updates=character_updates,
            rule_receipts=rule_receipts,
            expected_campaign_revision=campaign.revision,
        )

    def _source_bound_trap_effect_transition(
        self,
        *,
        campaign: Any,
        campaign_id: str,
        trap_id: str,
        action: str,
        exact_source: str,
        scene_id: str,
        normalized_profile: dict[str, Any],
        actor_id: str,
        method: str | None,
        target_ids: list[str] | None,
        area_confirmed: bool | None,
        trap_depth_ft: int | None,
        trigger_fact: dict[str, Any] | None,
        encounter: dict[str, Any],
        ruleset: str,
        stream: Any,
        principal_id: str,
        branch_id: str,
        idempotency_key: str,
        scope: str,
        replay_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Settle the bounded Poison Darts and Falling Net source mechanics."""
        profile_id = normalized_profile["profile_id"]
        if profile_id == "srd5.1.poison_darts" and action == "trigger":
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "poison darts require 1 to 20 distinct eligible target actor ids"
                )
            snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
            )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            trap_state["traps"][trap_id]["trigger_fact"] = dict(trigger_fact or {})
            current_sheets = {
                target_id: _support.deepcopy(snapshot["sheet"])
                for target_id, snapshot in snapshots.items()
            }
            dart_results: list[dict[str, Any]] = []
            receipts: list[dict[str, Any]] = []
            concentration_events: list[tuple[str, dict[str, Any]]] = []
            for dart_index in range(1, int(normalized_profile["trigger"]["dart_count"]) + 1):
                selected_id = target_ids[stream.randint(0, len(target_ids) - 1)]
                target_snapshot = snapshots[selected_id]
                target_snapshot = {**target_snapshot, "sheet": current_sheets[selected_id]}
                attack = resolve_attack(
                    armor_class=int(target_snapshot["derived"]["armor_class"]),
                    attack_bonus=int(normalized_profile["trigger"]["attack_bonus"]),
                    rng=stream,
                )
                entry: dict[str, Any] = {
                    "dart": dart_index,
                    "target_id": selected_id,
                    "attack": attack,
                }
                if attack["hit"]:
                    piercing_expression = "2d4" if attack["critical"] else "1d4"
                    piercing_roll = roll(piercing_expression, rng=stream)
                    piercing = _support.apply_damage_to_sheet(
                        current_sheets[selected_id],
                        amount=piercing_roll.total,
                        damage_type=normalized_profile["trigger"]["piercing_type"],
                        source=f"trap:{exact_source}:{trap_id}:dart:{dart_index}",
                        critical=bool(attack["critical"]),
                        ruleset=ruleset,
                        weapon_attack=True,
                        attack_facts={"kind": "trap_attack", "attack_bonus": 8},
                    )
                    current_sheets[selected_id] = piercing["sheet"]
                    piercing_public = {
                        key: value for key, value in piercing.items() if key != "sheet"
                    }
                    entry["piercing"] = {
                        "expression": piercing_expression,
                        "rolls": list(piercing_roll.rolls),
                        **piercing_public,
                    }
                    concentration_events.append((selected_id, piercing))
                    save_context = self.effective_rule_context(
                        campaign_id,
                        branch_id=branch_id,
                        facts={
                            "kind": "save_damage",
                            "actor_ids": [selected_id],
                            "ability": normalized_profile["trigger"]["save_ability"],
                            "dc": normalized_profile["trigger"]["save_dc"],
                            "trap_id": trap_id,
                            "trap_source_ref": exact_source,
                            "dart_index": dart_index,
                        },
                    )
                    poison_target = {**target_snapshot, "sheet": current_sheets[selected_id]}
                    settled = _support.resolve_save_damage_to_sheets(
                        [poison_target],
                        save_ability=normalized_profile["trigger"]["save_ability"],
                        save_dc=normalized_profile["trigger"]["save_dc"],
                        damage_expression=normalized_profile["trigger"]["poison_expression"],
                        damage_type=normalized_profile["trigger"]["poison_type"],
                        half_on_success=normalized_profile["trigger"]["half_on_success"],
                        source=f"trap:{exact_source}:{trap_id}:dart:{dart_index}:poison",
                        encounter=encounter or None,
                        ruleset=ruleset,
                        rules=save_context,
                        rng=stream,
                    )
                    current_sheets[selected_id] = settled["sheets"][selected_id]
                    poison_result = dict(settled["result"]["targets"][0])
                    entry["poison"] = poison_result
                    receipts.extend(receipt for receipt in poison_result.get("rule_receipts", []))
                    poison_damage = dict(poison_result.get("damage") or {})
                    if poison_damage:
                        concentration_events.append((selected_id, poison_damage))
                dart_results.append(entry)

            character_updates: list[Any] = []
            for target_id, snapshot in snapshots.items():
                updated_sheet = _support.validate_character_sheet(current_sheets[target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    for event_target, damage in concentration_events:
                        if event_target == target_id:
                            self.add_concentration_window(
                                encounter,
                                target_id,
                                damage.get("concentration"),
                                next_revision=campaign.revision + 1,
                            )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "eligible_target_ids": target_ids,
                    "darts": dart_results,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": trap_state["traps"][trap_id],
                "darts": dart_results,
                "eligible_target_ids": target_ids,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.poison_needle" and action in {"trigger", "disable"}:
            if target_ids is not None:
                raise _support.CombatEngineError(
                    "Poison Needle settles its single explicitly confirmed target"
                )
            self.require_campaign_actor(campaign_id, actor_id)
            if encounter.get("active"):
                self.require_encounter_combatant(encounter, actor_id, role="trap target")
            actor = self.combat_actor_snapshot(actor_id)
            disable_check = None
            if action == "disable":
                disable_spec = dict(normalized_profile.get("disable") or {})
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(actor["sheet"].get("traits") or {}).get("proficiencies") or {}
                    ).get("tools", [])
                }
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                    },
                )
                disable_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency,
                }
                if disable_check.get("success") is True:
                    next_state = _support.deepcopy(campaign.state)
                    trap_state = dict(next_state.get("trap_state") or {})
                    trap_state = transition_trap_state(
                        trap_state,
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    current_trap = dict(trap_state["traps"][trap_id])
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        rule_receipts=list(disable_check.get("rule_receipts", [])),
                        expected_campaign_revision=campaign.revision,
                    )
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            if action == "disable":
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            needle = normalized_profile["trigger"]
            piercing = _support.apply_damage_to_sheet(
                _support.deepcopy(actor["sheet"]),
                amount=needle["piercing_damage"],
                damage_type=needle["piercing_type"],
                source=f"trap:{exact_source}:{trap_id}:needle",
                ruleset=ruleset,
            )
            piercing_sheet = piercing["sheet"]
            poison_target = {
                **actor,
                "sheet": piercing_sheet,
                "derived": _support.derive_domain_character_sheet(piercing_sheet),
            }
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": [actor_id],
                    "ability": needle["save_ability"],
                    "dc": needle["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "target_within_source_defined_range": True,
                },
            )
            settled = _support.resolve_save_damage_to_sheets(
                [poison_target],
                save_ability=needle["save_ability"],
                save_dc=needle["save_dc"],
                damage_expression=needle["poison_expression"],
                damage_type=needle["poison_type"],
                half_on_success=needle["poison_half_on_success"],
                source=f"trap:{exact_source}:{trap_id}:poison",
                encounter=encounter or None,
                ruleset=ruleset,
                rules=save_context,
                rng=stream,
            )
            poison = dict(settled["result"]["targets"][0])
            poison["damage_expression"] = needle["poison_expression"]
            poison["damage_roll"] = dict(settled["result"]["damage_roll"])
            updated_sheet = settled["sheets"][actor_id]
            effect = None
            effect_suppressed = None
            if poison.get("success") is False:
                effect = build_poison_needle_condition_effect(
                    profile_id=profile_id,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    target_actor_id=actor_id,
                )
                if effect_is_immune(updated_sheet, effect):
                    effect_suppressed = "immune"
                    effect = None
                elif effect_is_suspended_by_petrification(updated_sheet, effect):
                    effect_suppressed = "petrified"
                    effect = None
                else:
                    updated_sheet, _ = _support.add_effect(updated_sheet, effect)
            updated_sheet = _support.validate_character_sheet(updated_sheet)
            current_actor = self.characters.get(actor_id)
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["triggered_actor_id"] = actor_id
            current_trap["range_confirmed"] = True
            if action == "trigger":
                current_trap["trigger_fact"] = dict(trigger_fact or {})
            if effect is not None:
                current_trap["condition_effect_id"] = effect["id"]
            trap_state["traps"][trap_id] = current_trap
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    **({"disable_check": disable_check} if disable_check is not None else {}),
                    "range_confirmed": True,
                    "piercing": {key: value for key, value in piercing.items() if key != "sheet"},
                    "poison": poison,
                    "condition_effect_id": effect["id"] if effect is not None else None,
                    "condition_suppressed": effect_suppressed,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            if encounter.get("active"):
                poison_damage = dict(poison.get("damage") or {})
                for damage in (
                    piercing,
                    poison_damage,
                ):
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            actor_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                self.sync_combatant_conditions(encounter, actor_id, updated_sheet)
                _support.reconcile_readied_spells(encounter, actor_id, updated_sheet)
                next_state["combat"] = encounter
            character_update = _support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=updated_sheet,
                notes=_support.validate_character_notes(current_actor.notes),
                expected_revision=current_actor.revision,
            )
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                **({"disable_check": disable_check} if disable_check is not None else {}),
                "trap": current_trap,
                "target_id": actor_id,
                "piercing": {key: value for key, value in piercing.items() if key != "sheet"},
                "poison": poison,
                "condition_effect": effect,
                "condition_suppressed": effect_suppressed,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            receipts = list(poison.get("rule_receipts") or [])
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=[character_update],
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.collapsing_roof" and action in {"trigger", "disable"}:
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "Collapsing Roof requires 1 to 20 distinct explicitly confirmed targets"
                )
            disable_check = None
            if action == "disable":
                disable_actor = self.combat_actor_snapshot(actor_id)
                disable_spec = dict(normalized_profile.get("disable") or {})
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(disable_actor["sheet"].get("traits") or {}).get("proficiencies")
                        or {}
                    ).get("tools", [])
                }
                tool_alternative = method == "edged_tool"
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                        "tool_alternative": method,
                    },
                )
                disable_check = _support.resolve_actor_check(
                    disable_actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency and not tool_alternative,
                    disadvantage=tool_alternative,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency and not tool_alternative,
                }
                if disable_check.get("success") is True:
                    next_state = _support.deepcopy(campaign.state)
                    trap_state = transition_trap_state(
                        dict(next_state.get("trap_state") or {}),
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    current_trap = dict(trap_state["traps"][trap_id])
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        rule_receipts=list(disable_check.get("rule_receipts", [])),
                        expected_campaign_revision=campaign.revision,
                    )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            if action == "disable":
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            trigger = normalized_profile["trigger"]
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": list(target_ids),
                    "ability": trigger["save_ability"],
                    "dc": trigger["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "area": trigger["area"],
                    "area_confirmed": True,
                    "failed_disable_check": disable_check,
                },
            )
            settled = _support.resolve_save_damage_to_sheets(
                list(target_snapshots.values()),
                save_ability=trigger["save_ability"],
                save_dc=trigger["save_dc"],
                damage_expression=trigger["damage_expression"],
                damage_type=trigger["damage_type"],
                half_on_success=trigger["half_on_success"],
                source=f"trap:{exact_source}:{trap_id}:collapse",
                encounter=encounter or None,
                ruleset=ruleset,
                rules=save_context,
                rng=stream,
            )
            current_trap = dict(trap_state["traps"][trap_id])
            terrain_effect = {
                "kind": "difficult_terrain",
                "effect": "rubble",
                "area": trigger["area"],
                "source_ref": exact_source,
                "trap_id": trap_id,
                "active": True,
            }
            current_trap["area_confirmed"] = True
            current_trap["affected_actor_ids"] = list(target_ids)
            if action == "trigger":
                current_trap["trigger_fact"] = dict(trigger_fact or {})
            current_trap["terrain_effects"] = [terrain_effect]
            trap_state["traps"][trap_id] = current_trap
            character_updates = []
            target_results = settled["result"]["targets"]
            receipts = [
                receipt for target in target_results for receipt in target.get("rule_receipts", [])
            ]
            for target_id in target_ids:
                updated_sheet = _support.validate_character_sheet(settled["sheets"][target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    damage = dict(target_result.get("damage") or {})
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            target_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    **({"disable_check": disable_check} if disable_check is not None else {}),
                    "area_confirmed": True,
                    "target_ids": list(target_ids),
                    "save_damage": settled["result"],
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                **({"disable_check": disable_check} if disable_check is not None else {}),
                **({"check": disable_check} if disable_check is not None else {}),
                "affected_actor_ids": list(target_ids),
                "damage_roll": settled["result"]["damage_roll"],
                "targets": target_results,
                "terrain_effect": terrain_effect,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts
                + (list(disable_check.get("rule_receipts", [])) if disable_check else []),
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.fire_breathing_statue" and action == "trigger":
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "Fire-Breathing Statue requires 1 to 20 distinct explicitly confirmed targets"
                )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
            )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            trigger = normalized_profile["trigger"]
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": list(target_ids),
                    "ability": trigger["save_ability"],
                    "dc": trigger["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "area": trigger["area"],
                    "area_confirmed": True,
                },
            )
            settled = _support.resolve_save_damage_to_sheets(
                list(target_snapshots.values()),
                save_ability=trigger["save_ability"],
                save_dc=trigger["save_dc"],
                damage_expression=trigger["damage_expression"],
                damage_type=trigger["damage_type"],
                half_on_success=trigger["half_on_success"],
                source=f"trap:{exact_source}:{trap_id}:fire-breathing-statue",
                encounter=encounter or None,
                ruleset=ruleset,
                rules=save_context,
                rng=stream,
            )
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["area_confirmed"] = True
            current_trap["affected_actor_ids"] = list(target_ids)
            current_trap["trigger_fact"] = dict(trigger_fact or {})
            current_trap["area_effect"] = {
                "kind": "fire_cone",
                "area": trigger["area"],
                "source_ref": exact_source,
                "trap_id": trap_id,
                "active": False,
            }
            trap_state["traps"][trap_id] = current_trap
            character_updates = []
            target_results = settled["result"]["targets"]
            receipts = [
                receipt for target in target_results for receipt in target.get("rule_receipts", [])
            ]
            for target_id in target_ids:
                updated_sheet = _support.validate_character_sheet(settled["sheets"][target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    damage = dict(target_result.get("damage") or {})
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            target_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "target_ids": list(target_ids),
                    "save_damage": settled["result"],
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "affected_actor_ids": list(target_ids),
                "damage_roll": settled["result"]["damage_roll"],
                "targets": target_results,
                "area_effect": current_trap["area_effect"],
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id in _SOURCE_PIT_PROFILES and action == "trigger":
            depth_ft = validate_source_pit_depth(normalized_profile, trap_depth_ft)
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "pit trigger requires 1 to 20 distinct explicitly confirmed targets"
                )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            is_locking_pit = profile_id in _SOURCE_LOCKING_PIT_PROFILES
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
                contained_actor_ids=target_ids if is_locking_pit else None,
            )
            trap_state["traps"][trap_id]["trigger_fact"] = dict(trigger_fact or {})
            if not is_locking_pit:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="settle",
                )
            trigger = normalized_profile["trigger"]
            target_results: list[dict[str, Any]] = []
            updated_sheets: dict[str, dict[str, Any]] = {}
            receipts: list[dict[str, Any]] = []
            for target_id in target_ids:
                snapshot = target_snapshots[target_id]
                fall = resolve_fall_to_sheet(
                    _support.deepcopy(snapshot["sheet"]),
                    distance_ft=depth_ft,
                    source=f"trap:{exact_source}:{trap_id}:fall",
                    ruleset=ruleset,
                    rng=stream,
                )
                sheet = fall["sheet"]
                entry: dict[str, Any] = {
                    "target_id": target_id,
                    "fall": {key: value for key, value in fall.items() if key != "sheet"},
                }
                spike_expression = trigger.get("spike_damage_expression")
                if spike_expression:
                    spike_roll = roll(spike_expression, rng=stream)
                    spike = _support.apply_damage_to_sheet(
                        sheet,
                        amount=spike_roll.total,
                        damage_type=trigger["spike_damage_type"],
                        source=f"trap:{exact_source}:{trap_id}:spikes",
                        ruleset=ruleset,
                    )
                    sheet = spike["sheet"]
                    entry["spikes"] = {
                        "expression": spike_expression,
                        "rolls": list(spike_roll.rolls),
                        **{key: value for key, value in spike.items() if key != "sheet"},
                    }
                poison_expression = trigger.get("poison_damage_expression")
                if poison_expression:
                    poison_context = self.effective_rule_context(
                        campaign_id,
                        branch_id=branch_id,
                        facts={
                            "kind": "save_damage",
                            "actor_ids": [target_id],
                            "ability": trigger["poison_save_ability"],
                            "dc": trigger["poison_save_dc"],
                            "trap_id": trap_id,
                            "trap_source_ref": exact_source,
                            "pit_depth_ft": depth_ft,
                        },
                    )
                    poison_target = {
                        **snapshot,
                        "sheet": sheet,
                        "derived": _support.derive_domain_character_sheet(sheet),
                    }
                    settled_poison = _support.resolve_save_damage_to_sheets(
                        [poison_target],
                        save_ability=trigger["poison_save_ability"],
                        save_dc=trigger["poison_save_dc"],
                        damage_expression=poison_expression,
                        damage_type=trigger["poison_damage_type"],
                        half_on_success=trigger["poison_half_on_success"],
                        source=f"trap:{exact_source}:{trap_id}:spike-poison",
                        encounter=encounter or None,
                        ruleset=ruleset,
                        rules=poison_context,
                        rng=stream,
                    )
                    sheet = settled_poison["sheets"][target_id]
                    poison_result = dict(settled_poison["result"]["targets"][0])
                    entry["poison"] = poison_result
                    receipts.extend(poison_result.get("rule_receipts", []))
                updated_sheets[target_id] = _support.validate_character_sheet(sheet)
                target_results.append(entry)
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["area_confirmed"] = True
            current_trap["trap_depth_ft"] = depth_ft
            current_trap["affected_actor_ids"] = list(target_ids)
            if is_locking_pit:
                current_trap["contained_actor_ids"] = list(target_ids)
            trap_state["traps"][trap_id] = current_trap
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "trap_depth_ft": depth_ft,
                    "target_ids": list(target_ids),
                    "targets": target_results,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            character_updates = []
            for target_id in target_ids:
                current_actor = self.characters.get(target_id)
                updated_sheet = updated_sheets[target_id]
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    for damage in (
                        target_result["fall"].get("damage"),
                        target_result.get("spikes", {}).get("damage"),
                        target_result.get("poison", {}).get("damage"),
                    ):
                        if damage:
                            self.add_concentration_window(
                                encounter,
                                target_id,
                                damage.get("concentration"),
                                next_revision=campaign.revision + 1,
                            )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "affected_actor_ids": list(target_ids),
                "trap_depth_ft": depth_ft,
                "targets": target_results,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id != "srd5.1.falling_net" and profile_id not in _SOURCE_LOCKING_PIT_PROFILES:
            raise _support.CombatEngineError(
                "this source-bound trap effect has no complete runtime settlement"
            )
        self.require_campaign_actor(campaign_id, actor_id)
        if encounter.get("active"):
            self.require_encounter_combatant(encounter, actor_id, role="trap target")
        actor = self.combat_actor_snapshot(actor_id)
        next_state = _support.deepcopy(campaign.state)
        trap_state = dict(next_state.get("trap_state") or {})
        current_trap = dict(dict(trap_state.get("traps") or {}).get(trap_id) or {})
        if action in {"trigger", "disable"}:
            if target_ids is not None:
                raise _support.CombatEngineError(
                    "falling net currently settles one explicitly confirmed target"
                )
            disable_check = None
            if action == "disable":
                disable_spec = normalized_profile.get("disable")
                if not isinstance(disable_spec, dict):
                    raise _support.CombatEngineError(
                        "falling net disable check is missing from its source profile"
                    )
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(actor["sheet"].get("traits") or {}).get("proficiencies") or {}
                    ).get("tools", [])
                }
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                tool_alternative = method == "edged_tool"
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                        "tool_alternative": method,
                    },
                )
                disable_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency and not tool_alternative,
                    disadvantage=tool_alternative,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency and not tool_alternative,
                }
                if disable_check.get("success") is True:
                    trap_state = transition_trap_state(
                        trap_state,
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    updated_sheet = _support.validate_character_sheet(
                        _support.deepcopy(actor["sheet"])
                    )
                    current_actor = self.characters.get(actor_id)
                    current_trap = dict(trap_state["traps"][trap_id])
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "area_confirmed": True,
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    character_update = _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        character_updates=[character_update],
                        expected_campaign_revision=campaign.revision,
                    )
                # A failed source-defined disable transitions to trigger, then
                # the same atomic command rolls the net's Dexterity save.
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            check_spec = normalized_profile["trigger"]
            check_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save",
                    "actor_id": actor_id,
                    "ability": check_spec["save_ability"],
                    "dc": check_spec["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "target_in_source_defined_area": True,
                },
            )
            check = _support.resolve_actor_check(
                actor,
                kind="save",
                ability=check_spec["save_ability"],
                dc=check_spec["save_dc"],
                encounter=encounter or None,
                ruleset=ruleset,
                rules=check_context,
                rng=stream,
            )
            check = {**check, "ability": check_spec["save_ability"]}
            current_trap = dict(trap_state["traps"][trap_id])
            restrained_ids = list(current_trap.get("restrained_actor_ids") or [])
            trap_added_ids = list(current_trap.get("trap_added_restrained_actor_ids") or [])
            updated_sheet = _support.deepcopy(actor["sheet"])
            if check.get("success") is False:
                had_condition = "restrained" in set(updated_sheet.get("conditions") or [])
                _support.apply_condition_change(updated_sheet, condition_id="restrained", add=True)
                if "restrained" in set(updated_sheet.get("conditions") or []):
                    restrained_ids.append(actor_id)
                    if not had_condition:
                        trap_added_ids.append(actor_id)
            current_trap["restrained_actor_ids"] = list(dict.fromkeys(restrained_ids))
            current_trap["trap_added_restrained_actor_ids"] = list(dict.fromkeys(trap_added_ids))
            current_trap["area_confirmed"] = True
            current_trap["profile_id"] = profile_id
            if profile_id == "srd5.1.falling_net":
                current_trap["object_id"] = trap_id
                current_trap["scene_id"] = scene_id
            trap_state["traps"][trap_id] = current_trap
            result: dict[str, Any] = {
                "check": check,
                "action": action,
                **({"disable_check": disable_check} if disable_check is not None else {}),
            }
            receipts = list(check.get("rule_receipts") or [])
            if disable_check is not None:
                receipts.extend(disable_check.get("rule_receipts") or [])
        elif action == "escape":
            locking_pit = profile_id in _SOURCE_LOCKING_PIT_PROFILES
            trapped_actor_ids = list(
                current_trap.get(
                    "contained_actor_ids" if locking_pit else "restrained_actor_ids"
                )
                or []
            )
            if actor_id not in trapped_actor_ids:
                if locking_pit:
                    raise _support.CombatEngineError(
                        "actor is not contained by this source-bound locking pit"
                    )
                raise _support.CombatEngineError("actor is not restrained by this falling net")
            escape = (
                {
                    "ability": "strength",
                    "dc": normalized_profile["trigger"]["escape_strength_dc"],
                }
                if locking_pit
                else normalized_profile["trigger"]["escape_check"]
            )
            check_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "check",
                    "actor_id": actor_id,
                    "ability": escape["ability"],
                    "dc": escape["dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                },
            )
            check = _support.resolve_actor_check(
                actor,
                kind="check",
                ability=escape["ability"],
                dc=escape["dc"],
                encounter=encounter or None,
                ruleset=ruleset,
                rules=check_context,
                rng=stream,
            )
            check = {**check, "ability": escape["ability"]}
            if check.get("success") is True:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="escape",
                    actor_id=actor_id,
                )
            current_trap = dict(trap_state["traps"][trap_id])
            updated_sheet = _support.deepcopy(actor["sheet"])
            if check.get("success") is True and actor_id in list(
                current_trap.get("trap_added_restrained_actor_ids") or []
            ):
                _support.apply_condition_change(updated_sheet, condition_id="restrained", add=False)
                current_trap["trap_added_restrained_actor_ids"] = [
                    item
                    for item in current_trap["trap_added_restrained_actor_ids"]
                    if item != actor_id
                ]
                trap_state["traps"][trap_id] = current_trap
            result = {
                "check": check,
                "action": action,
                **({"contained_actor_ids": list(current_trap.get("contained_actor_ids") or [])}
                   if locking_pit else {}),
            }
            receipts = list(check.get("rule_receipts") or [])

        updated_sheet = _support.validate_character_sheet(updated_sheet)
        current_actor = self.characters.get(actor_id)
        character_updates = [
            _support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=updated_sheet,
                notes=_support.validate_character_notes(current_actor.notes),
                expected_revision=current_actor.revision,
            )
        ]
        if encounter.get("active"):
            self.sync_combatant_conditions(encounter, actor_id, updated_sheet)
            _support.reconcile_readied_spells(encounter, actor_id, updated_sheet)
            next_state["combat"] = encounter
        trap_state.setdefault("attempts", [])
        trap_state["attempts"] = [
            *list(trap_state["attempts"]),
            {
                "trap_id": trap_id,
                "source_ref": exact_source,
                "actor_id": actor_id,
                "action": action,
                "check": check,
                "area_confirmed": True if action in {"trigger", "disable"} else None,
                "method": method,
                "campaign_revision": campaign.revision + 1,
            },
        ][-100:]
        next_state["trap_state"] = trap_state
        response = {
            "status": "committed",
            "trap_id": trap_id,
            "action": action,
            "trap": trap_state["traps"][trap_id],
            **result,
        }
        receipt = stream.receipt() if stream.draw_count else None
        if receipt is not None:
            response["random_stream_receipt"] = receipt
        return self.commit_campaign_state(
            campaign,
            next_state,
            operation="trap.state.transition",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=replay_payload,
            response_fields=response,
            character_updates=character_updates,
            rule_receipts=receipts,
            expected_campaign_revision=campaign.revision,
        )
