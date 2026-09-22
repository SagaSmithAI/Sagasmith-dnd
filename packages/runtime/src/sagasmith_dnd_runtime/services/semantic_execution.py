"""Explicit encounter adapter for registered primitives; Runtime owns authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sagasmith_dnd import encounter_primitives as _domain
from sagasmith_dnd.primitive_contracts import PRIMITIVES
from sagasmith_dnd.resolution_plan import ResolutionPlanPauseError
from sagasmith_dnd.rule_primitives import validate_primitive

from .. import application_support as _support


@dataclass(frozen=True)
class CombatPlanContext:
    encounter: dict[str, Any]
    runtime_services: Any
    campaign: Any
    campaign_id: str
    resolved_branch_id: str | None
    bound_plan: _support.BoundResolutionPlan
    compiled_plan: _support.CompiledResolutionPlan
    save_facts_by_step: dict[str, Any]


class CombatPlanRuntime:
    def __init__(self, context: CombatPlanContext) -> None:
        self.context = context
        self.encounter = _support.deepcopy(self.context.encounter)
        self.records: dict[str, Any] = {}
        self.sheets: dict[str, dict[str, Any]] = {}
        self.knowledge_transfers: list[_support.ActorKnowledgeTransfer] = []
        self.application_id = str((context.bound_plan.agent_ruling or {}).get("application_id")
                                  or context.bound_plan.fingerprint)
        self.continuation = self.encounter.setdefault("semantic_state", {}).setdefault(
            "continuations", {}
        ).get(self.application_id, {})
        self.completed = _support.deepcopy(self.continuation.get("results", {}))

    def begin(self, plan: _support.BoundResolutionPlan) -> None:
        if plan.fingerprint != self.context.bound_plan.fingerprint:
            raise _support.CombatEngineError("semantic plan runtime fingerprint mismatch")
        movement = self.encounter.get("movement_continuation")
        if self.continuation and movement:
            raise _support.NeedsRulingError(
                "finish the source movement and its reactions before resuming the plan",
                missing=(str(movement["choice_id"]),), ruling_kind="player_owned_choice",
            )
        waiting = set(self.continuation.get("waiting_ids", []))
        if any(item.get("id") in waiting and item.get("status", "pending") == "pending"
               for item in self.encounter.get("pending", [])):
            raise _support.NeedsRulingError(
                "resolve the recorded semantic plan choices before resuming",
                missing=tuple(sorted(waiting)), ruling_kind="player_owned_choice",
            )
        if self.continuation and (
            self.continuation["plan_fingerprint"] != plan.compiled.fingerprint
            or self.continuation["bindings"] != plan.bindings
        ):
            raise _support.CombatEngineError("semantic continuation plan changed")
        for step in plan.steps:
            spec = PRIMITIVES.get(step["op"])
            if spec is None or not spec.handler or not callable(getattr(self, spec.handler, None)):
                raise _support.CombatEngineError(
                    f"primitive requires another execution context: {step['op']}"
                )

    def rollback(self) -> None:
        self.encounter = _support.deepcopy(self.context.encounter)
        self.records.clear()
        self.sheets.clear()
        self.knowledge_transfers.clear()

    def commit(self) -> None:
        return None

    def record(self, actor_id: str) -> Any:
        if actor_id not in self.records:
            self.records[actor_id] = self.context.runtime_services.require_campaign_actor(
                self.context.campaign_id,
                actor_id,
            )
        return self.records[actor_id]

    def sheet(self, actor_id: str) -> dict[str, Any]:
        if actor_id not in self.sheets:
            self.sheets[actor_id] = _support.deepcopy(self.record(actor_id).sheet)
        return self.sheets[actor_id]

    def actor(self, actor_id: str) -> dict[str, Any]:
        actor = self.context.runtime_services.combat_actor_snapshot(actor_id)
        actor["sheet"] = _support.deepcopy(self.sheet(actor_id))
        actor["derived"] = self.context.runtime_services.derive_character_sheet(
            actor["sheet"], character_id=actor_id
        )
        return actor

    def set_sheet(self, actor_id: str, sheet: dict[str, Any]) -> None:
        normalized = _support.validate_character_sheet(sheet)
        self.sheets[actor_id] = normalized
        self.context.runtime_services.sync_combatant_conditions(
            self.encounter,
            actor_id,
            normalized,
        )

    def reconcile_movement_tethers(
        self,
        before: dict[str, Any],
    ) -> list[str]:
        ended = _support.newly_ended_witch_bolt_tethers(
            before,
            self.encounter,
        )
        by_caster: dict[str, list[dict[str, Any]]] = {}
        for tether in ended:
            by_caster.setdefault(
                str(tether.get("source_actor_id") or ""),
                [],
            ).append(tether)
        for caster_id, tethers in by_caster.items():
            ended_concentration = _support.end_tether_concentrations(
                self.sheet(caster_id),
                tethers,
            )
            self.set_sheet(caster_id, ended_concentration["sheet"])
        return [str(item.get("id") or "") for item in ended]

    def execute(self, opcode, arguments, *, step_id, prior_results):
        del prior_results
        if step_id in self.completed:
            cached = self.completed[step_id]
            if opcode == "movement.move" and cached.get("movement_status") == "paused":
                final = next((entry for entry in reversed(self.encounter.get("log", []))
                              if entry.get("type") == "source_movement_finished"
                              and entry.get("grant_id") == cached.get("movement_id")), None)
                if final:
                    cached.update(
                        movement_status=final["status"], position=final["position"],
                        turn_budget=final["turn_budget"],
                    )
            return _support.deepcopy(self.completed[step_id])
        validate_primitive(opcode, arguments)
        spec = PRIMITIVES.get(opcode)
        if spec is None or not spec.handler:
            raise _support.CombatEngineError(
                f"semantic plan primitive requires a specialized execution context: {opcode}"
            )
        previous = {item.get("id") for item in self.encounter.get("pending", [])}
        result = getattr(self, spec.handler)(opcode, arguments, step_id=step_id)
        self.completed[step_id] = _support.deepcopy(result)
        pending = [item["id"] for item in self.encounter.get("pending", [])
                   if item.get("id") not in previous
                   and item.get("status", "pending") == "pending"]
        if pending:
            self.pause(pending, result)
        return result

    def pause(self, waiting_ids, result=None):
        self.encounter.setdefault("semantic_state", {}).setdefault("continuations", {})[
            self.application_id
        ] = {
            "plan_fingerprint": self.context.compiled_plan.fingerprint,
            "bindings": _support.deepcopy(self.context.bound_plan.bindings),
            "results": _support.deepcopy(self.completed), "waiting_ids": list(waiting_ids),
        }
        raise ResolutionPlanPauseError(result)

    def _execute_roll_table(self, opcode, arguments, *, step_id):
        table = _domain.weighted_table(arguments)
        total_weight = sum(item["weight"] for item in table)
        rolled = _support.asdict(_support.roll(f"1d{total_weight}"))
        return {"roll": rolled, "value": _domain.select_weighted_value(table, rolled["total"])}

    def _execute_target_validate(self, opcode, arguments, *, step_id):
        source = self.context.runtime_services.require_encounter_combatant(
            self.encounter, str(arguments["source_actor_id"]),
            role="semantic plan targeting source",
        )
        targets = {identifier: self.context.runtime_services.require_encounter_combatant(
            self.encounter, identifier, role="semantic plan target",
        ) for identifier in arguments["target_ids"]}
        evidence = (self.context.bound_plan.agent_ruling or {}).get("target_facts", {})
        facts = evidence.get("steps", {}).get(step_id, {}).get("targets", {})
        return _domain.validate_targets(
            source, targets, arguments, spatial_facts=facts,
            positioning_mode=self.encounter.get("positioning_mode", "agent"),
        )

    def _execute_check_save(self, opcode, arguments, *, step_id):
        ability = str(arguments["ability"])
        dc = int(arguments["dc"])
        success_damage = str(arguments.get("success_damage") or "none")
        target_results: list[dict[str, Any]] = []
        reduction_by_actor_id: dict[str, str] = {}
        for target_id in arguments["target_ids"]:
            result = _support.resolve_actor_check(
                self.actor(str(target_id)),
                kind="save",
                ability=ability,
                dc=dc,
                encounter=self.encounter,
                advantage=bool(arguments.get("advantage", False)),
                disadvantage=bool(arguments.get("disadvantage", False)),
                rules=self.context.runtime_services.effective_rule_context(
                    self.context.campaign_id,
                    facts={
                        "actor_id": str(target_id),
                        "kind": "save",
                        "ability": ability,
                        "dc": dc,
                        "semantic_plan_id": self.context.compiled_plan.id,
                        **self.context.save_facts_by_step[step_id],
                    },
                    branch_id=self.context.resolved_branch_id,
                ),
            )
            reduction_by_actor_id[str(target_id)] = (
                success_damage if result["success"] else "full"
            )
            target_results.append(
                {
                    "target_id": str(target_id),
                    **result,
                }
            )
        return {
            "targets": target_results,
            "all_failed": bool(target_results)
            and all(not bool(item["success"]) for item in target_results),
            "all_succeeded": bool(target_results)
            and all(bool(item["success"]) for item in target_results),
            "success_by_actor_id": {
                item["target_id"]: bool(item["success"]) for item in target_results
            },
            "damage_reduction_by_actor_id": reduction_by_actor_id,
        }

    def _execute_check_ability(self, opcode, arguments, *, step_id):
        actor_id = str(arguments["actor_id"])
        return _support.resolve_actor_check(
            self.actor(actor_id),
            kind="ability",
            ability=str(arguments["ability"]),
            dc=int(arguments["dc"]),
            encounter=self.encounter,
            proficient=bool(arguments.get("proficient", False)),
            bonus=int(arguments.get("bonus", 0) or 0),
            advantage=bool(arguments.get("advantage", False)),
            disadvantage=bool(arguments.get("disadvantage", False)),
            rules=self.context.runtime_services.effective_rule_context(
                self.context.campaign_id,
                facts={
                    "actor_id": actor_id,
                    "kind": "ability",
                    "ability": str(arguments["ability"]),
                    "semantic_plan_id": self.context.compiled_plan.id,
                },
                branch_id=self.context.resolved_branch_id,
            ),
        )

    def _execute_check_contest(self, opcode, arguments, *, step_id):
        return _support.resolve_actor_contest(
            self.actor(str(arguments["source_actor_id"])),
            self.actor(str(arguments["target_actor_id"])),
            source_ability=str(arguments["source_ability"]),
            target_ability=str(arguments["target_ability"]),
            encounter=self.encounter,
            source_proficient=bool(arguments.get("source_proficient", False)),
            target_proficient=bool(arguments.get("target_proficient", False)),
            source_bonus=int(arguments.get("source_bonus", 0) or 0),
            target_bonus=int(arguments.get("target_bonus", 0) or 0),
            source_advantage=bool(arguments.get("source_advantage", False)),
            source_disadvantage=bool(arguments.get("source_disadvantage", False)),
            target_advantage=bool(arguments.get("target_advantage", False)),
            target_disadvantage=bool(arguments.get("target_disadvantage", False)),
            source_rules=self.context.runtime_services.effective_rule_context(
                self.context.campaign_id,
                facts={
                    "actor_id": str(arguments["source_actor_id"]),
                    "kind": "ability",
                    "ability": str(arguments["source_ability"]),
                    "semantic_plan_id": self.context.compiled_plan.id,
                },
                branch_id=self.context.resolved_branch_id,
            ),
            target_rules=self.context.runtime_services.effective_rule_context(
                self.context.campaign_id,
                facts={
                    "actor_id": str(arguments["target_actor_id"]),
                    "kind": "ability",
                    "ability": str(arguments["target_ability"]),
                    "semantic_plan_id": self.context.compiled_plan.id,
                },
                branch_id=self.context.resolved_branch_id,
            ),
        )

    def _execute_attack_resolve(self, opcode, arguments, *, step_id):
        attacker_id = str(arguments["source_actor_id"])
        target_id = str(arguments["target_actor_id"])
        attack_plan = _support.preflight_attack(
            self.actor(attacker_id),
            self.actor(target_id),
            action={
                "weapon_id": str(arguments["attack_ref"]),
                "attack_mode": str(arguments.get("attack_mode") or "melee"),
                "context": _support.deepcopy(arguments.get("context") or {}),
            },
            encounter=self.encounter,
            require_attack_action=False,
            rules=self.context.runtime_services.effective_rule_context(
                self.context.campaign_id,
                facts={
                    "actor_id": attacker_id,
                    "target_id": target_id,
                    "semantic_plan_id": self.context.compiled_plan.id,
                },
                branch_id=self.context.resolved_branch_id,
            ),
        )
        attack = _support.roll_attack_action(plan=attack_plan)
        defenses = _support.available_attack_defenses(
            self.actor(target_id),
            plan=attack_plan,
            attack=attack,
            encounter=self.encounter,
        )
        if defenses:
            self.encounter = _support.add_choice_window(
                self.encounter, kind="reaction", actor_id_value=target_id,
                event="attack.hit.before_damage",
                candidates=[*defenses, {"id": "decline", "name": "Decline"}],
            )
            window = self.encounter["pending"][-1]
            window.update(
                trigger="attack_hit_defense", attacker_id=attacker_id, target_id=target_id,
                plan=_support.deepcopy(attack_plan), attack=_support.deepcopy(attack),
                semantic_application_id=self.application_id, semantic_step_id=step_id,
            )
            self.pause([window["id"]])
        updated_attacker, updated_target, result = _support.resolve_attack_damage(
            self.actor(attacker_id),
            self.actor(target_id),
            plan=attack_plan,
            attack=attack,
            rules=self.context.runtime_services.effective_rule_context(
                self.context.campaign_id,
                facts={
                    "actor_id": attacker_id,
                    "target_id": target_id,
                    "semantic_plan_id": self.context.compiled_plan.id,
                },
                branch_id=self.context.resolved_branch_id,
            ),
        )
        self.set_sheet(attacker_id, dict(updated_attacker["sheet"]))
        self.set_sheet(target_id, dict(updated_target["sheet"]))
        _support.reconcile_readied_spells(self.encounter, target_id, updated_target["sheet"])
        damage = result.get("damage")
        if isinstance(damage, dict):
            self.context.runtime_services.add_concentration_window(
                self.encounter, target_id, damage.get("concentration"),
                next_revision=self.context.campaign.revision + 1,
            )
            result["damage"] = {key: value for key, value in damage.items() if key != "sheet"}
        return result

    def _execute_damage_apply(self, opcode, arguments, *, step_id):
        rolled = (
            _support.asdict(_support.roll(str(arguments["expression"])))
            if "expression" in arguments
            else None
        )
        base_amount = (
            int(rolled["total"]) if rolled is not None else int(arguments["amount"])
        )
        reduction = arguments.get("reduction", "full")
        results: list[dict[str, Any]] = []
        for target_id_value in arguments["target_ids"]:
            target_id = str(target_id_value)
            target_reduction = (
                str(reduction.get(target_id, "full"))
                if isinstance(reduction, dict)
                else str(reduction)
            )
            amount = _support.damage_amount_after_reduction(
                base_amount,
                target_reduction,
            )
            combatant = self.context.runtime_services.require_encounter_combatant(
                self.encounter,
                target_id,
                role="semantic plan damage target",
            )
            applied = _support.apply_damage_to_sheet(
                self.sheet(target_id),
                amount=amount,
                damage_type=str(arguments["damage_type"]),
                source=str(arguments["source"]),
                critical=bool(arguments.get("critical", False)),
                death_saves=bool(
                    combatant.get("death_saves", False)
                    or combatant.get(
                        "zero_hp_recovery",
                        False,
                    )
                ),
            )
            self.set_sheet(target_id, applied["sheet"])
            self.context.runtime_services.add_concentration_window(
                self.encounter,
                target_id,
                applied.get("concentration"),
                next_revision=self.context.campaign.revision + 1,
            )
            results.append(
                {
                    "target_id": target_id,
                    "reduction": target_reduction,
                    **{key: value for key, value in applied.items() if key != "sheet"},
                }
            )
        return {
            "roll": rolled,
            "base_amount": base_amount,
            "targets": results,
        }

    def _execute_healing_apply(self, opcode, arguments, *, step_id):
        rolled = (
            _support.asdict(_support.roll(str(arguments["expression"])))
            if "expression" in arguments
            else None
        )
        amount = (
            int(rolled["total"]) if rolled is not None else int(arguments["amount"])
        )
        results: list[dict[str, Any]] = []
        for target_id_value in arguments["target_ids"]:
            target_id = str(target_id_value)
            self.context.runtime_services.require_healing_not_prevented(
                self.encounter, target_id=target_id
            )
            applied = _support.apply_healing_to_sheet(
                self.sheet(target_id),
                amount=amount,
            )
            self.set_sheet(target_id, applied["sheet"])
            results.append(
                {
                    "target_id": target_id,
                    **{key: value for key, value in applied.items() if key != "sheet"},
                }
            )
        return {
            "roll": rolled,
            "amount": amount,
            "targets": results,
        }

    def _execute_condition_apply(self, opcode, arguments, *, step_id):
        add = opcode == "condition.apply"
        target_results: list[dict[str, Any]] = []
        for target_id_value in arguments["target_ids"]:
            target_id = str(target_id_value)
            sheet = _support.deepcopy(self.sheet(target_id))
            effect_id = None
            duration = arguments.get("duration")
            if add and isinstance(duration, dict):
                duration_kind = str(duration["kind"])
                duration_clock = {
                    "encounter": ("encounter", 1),
                    "rounds": (
                        "round",
                        int(duration.get("amount", 1) or 1),
                    ),
                    "source_turn_start": (
                        "source_turn_start",
                        1,
                    ),
                    "target_turn_start": ("turn_start", 1),
                    "target_turn_end": ("turn_end", 1),
                }.get(duration_kind)
                if duration_clock is None:
                    raise _support.CombatEngineError(
                        "semantic condition duration is unsupported"
                    )
                period, remaining = duration_clock
                effect_id = str(
                    arguments.get("effect_id")
                    or f"semantic-{self.context.bound_plan.fingerprint[:12]}-{step_id}-{target_id}"
                )
                sheet, effect_id = _support.add_effect(
                    sheet,
                    {
                        "id": effect_id,
                        "name": str(arguments["condition_id"])
                        .replace("_", " ")
                        .title(),
                        "kind": "timed_conditions",
                        "source": str(
                            arguments.get("source_actor_id")
                            or arguments.get("source")
                            or self.context.compiled_plan.source_card_id
                        ),
                        "active": True,
                        "duration": {
                            "period": period,
                            "remaining": remaining,
                        },
                        "changes": [
                            {
                                "path": "conditions",
                                "mode": "add",
                                "value": str(arguments["condition_id"]),
                            }
                        ],
                    },
                )
            else:
                from sagasmith_dnd.rule_primitives import apply_sheet_primitive

                sheet = apply_sheet_primitive(sheet, opcode, arguments)["sheet"]
            self.set_sheet(target_id, sheet)
            target_results.append(
                {
                    "target_id": target_id,
                    "condition_id": str(arguments["condition_id"]),
                    "active": add,
                    "effect_id": effect_id,
                }
            )
        return {"targets": target_results}

    def _execute_effect_apply(self, opcode, arguments, *, step_id):
        target_results = []
        for target_id_value in arguments["target_ids"]:
            target_id = str(target_id_value)
            effect = {
                **_support.deepcopy(arguments["effect"]),
                "id": str(arguments["effect_id"]),
            }
            sheet, effect_id = _support.add_effect(
                self.sheet(target_id),
                effect,
            )
            self.set_sheet(target_id, sheet)
            target_results.append(
                {
                    "target_id": target_id,
                    "effect_id": effect_id,
                }
            )
        return {"targets": target_results}

    def _execute_effect_remove(self, opcode, arguments, *, step_id):
        target_results = []
        for target_id_value in arguments["target_ids"]:
            target_id = str(target_id_value)
            sheet = _support.remove_effect(
                self.sheet(target_id),
                str(arguments["effect_id"]),
            )
            self.set_sheet(target_id, sheet)
            target_results.append(
                {
                    "target_id": target_id,
                    "effect_id": str(arguments["effect_id"]),
                }
            )
        return {"targets": target_results}

    def _execute_resource_spend(self, opcode, arguments, *, step_id):
        actor_id = str(arguments["actor_id"])
        sheet = _support.deepcopy(self.sheet(actor_id))
        resource_ref = str(arguments["resource_ref"])
        from sagasmith_dnd.rule_primitives import apply_sheet_primitive

        settled = apply_sheet_primitive(sheet, opcode, arguments)
        self.set_sheet(actor_id, settled["sheet"])
        return {
            "actor_id": actor_id,
            "resource_ref": resource_ref,
            "value": settled["value"],
        }

    def _execute_movement_force(self, opcode, arguments, *, step_id):
        before_movement = _support.deepcopy(self.encounter)
        moved = _support.force_move_directly_away(
            self.encounter,
            source_actor_id=str(arguments["source_actor_id"]),
            target_actor_id=str(arguments["target_actor_id"]),
            distance_ft=int(arguments["distance_ft"]),
        )
        self.encounter = moved["encounter"]
        ended_tether_ids = self.reconcile_movement_tethers(
            before_movement,
        )
        return {key: value for key, value in moved.items() if key != "encounter"} | {
            "ended_witch_bolt_tether_ids": ended_tether_ids
        }

    def _execute_movement_move(self, opcode, arguments, *, step_id):
        actor_id = str(arguments["actor_id"])
        before_movement = _support.deepcopy(self.encounter)
        request = {
            "destination": arguments.get("destination"), "path": arguments.get("path"),
            "travel_mode": arguments.get("travel_mode", "walk"),
            "crawl": arguments.get("crawl", False),
            "spatial_facts": self.context.runtime_services.validate_agent_movement_facts(
                self.encounter, arguments.get("spatial_facts")
            ),
        }
        distance = int(arguments.get("distance_ft", 0) or 0)
        if arguments.get("payment") is None:
            self.encounter = _support.spend_movement(self.encounter, actor_id, distance, **request)
        else:
            from sagasmith_dnd.movement_continuations import spend_source_movement

            self.encounter = spend_source_movement(
                self.encounter, actor_id, distance,
                payment=arguments["payment"], distance_limit=arguments["distance_limit"],
                voluntary=arguments.get("voluntary", True),
                source={
                    "id": self.context.compiled_plan.id, "step_id": step_id,
                    "application_id": self.application_id,
                    "source_card_id": self.context.compiled_plan.source_card_id,
                    "plan_fingerprint": self.context.compiled_plan.fingerprint,
                    "bound_plan_fingerprint": self.context.bound_plan.fingerprint,
                },
                **request,
            )
        ended_tether_ids = self.reconcile_movement_tethers(
            before_movement,
        )
        combatant = self.context.runtime_services.require_encounter_combatant(
            self.encounter,
            actor_id,
            role="semantic plan movement actor",
        )
        return {
            "actor_id": actor_id,
            "position": _support.deepcopy(combatant.get("position")),
            "turn_budget": _support.deepcopy(combatant.get("turn_budget")),
            "movement_payment": arguments.get("payment", "movement"),
            "movement_id": next((
                entry["grant_id"] for entry in reversed(self.encounter.get("log", []))
                if entry.get("type") == "source_movement_payment"
                and entry.get("source", {}).get("application_id") == self.application_id
                and entry.get("source", {}).get("step_id") == step_id
            ), None),
            "movement_status": (
                "paused" if self.encounter.get("movement_continuation") else "completed"
            ),
            "ended_witch_bolt_tether_ids": ended_tether_ids,
        }

    def _execute_actor_link(self, opcode, arguments, *, step_id):
        state = self.encounter.setdefault("semantic_state", {})
        links, result = _domain.update_actor_links(
            state.get("actor_links", []), opcode, arguments,
            plan_id=self.context.compiled_plan.id, step_id=step_id,
        )
        state["actor_links"] = links
        return result

    def _execute_actor_control(self, opcode, arguments, *, step_id):
        target = self.context.runtime_services.require_encounter_combatant(
            self.encounter, str(arguments["target_actor_id"]), role="semantic plan control target",
        )
        changed, result = _domain.control_actor(target, arguments)
        target.clear()
        target.update(changed)
        return result

    def _execute_knowledge_transfer(self, opcode, arguments, *, step_id):
        source_id = str(arguments["from_actor_id"])
        destination_id = str(arguments["to_actor_id"])
        selected_ids = tuple(str(item) for item in arguments["knowledge_ids"])
        visible_ids = {
            item.id
            for item in self.context.runtime_services.knowledge.list(
                self.context.campaign_id,
                actor_id=source_id,
                branch_id=self.context.resolved_branch_id,
            )
        }
        if not set(selected_ids).issubset(visible_ids):
            raise _support.CombatEngineError(
                "semantic knowledge transfer must name current source knowledge ids"
            )
        self.knowledge_transfers.append(
            _support.ActorKnowledgeTransfer(
                source_actor_id=source_id,
                destination_actor_id=destination_id,
                knowledge_key_prefix=(
                    f"semantic-plan.{self.context.bound_plan.fingerprint}.{step_id}"
                ),
                knowledge_ids=selected_ids,
                cause=str(arguments.get("reason") or "semantic_plan"),
            )
        )
        return {
            "from_actor_id": source_id,
            "to_actor_id": destination_id,
            "knowledge_ids": list(selected_ids),
        }

    def _execute_world_counter_adjust(self, opcode, arguments, *, step_id):
        state = self.encounter.setdefault("semantic_state", {})
        counters, result = _domain.update_counter(state.get("counters", {}), opcode, arguments)
        state["counters"] = counters
        return result

    def _execute_state_assert(self, opcode, arguments, *, step_id):
        return _domain.assert_state(arguments)
