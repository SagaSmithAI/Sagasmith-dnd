"""Presentation application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal

from .. import application_support as _support
from ..contracts import ACTION_PAYLOADS, validate_action_payload


class PresentationService:
    def refresh_portable_resolution_plans(self, value: Any) -> Any:
        """Re-fingerprint plans after stable/local source locators are remapped.

        The values passed here are freshly copied from an immutable archive.  Walk
        them in place so official archive verification does not repeatedly clone
        every source citation and card while applying several artifacts from the
        same pack.  Callers that need a reusable value already own the archive
        copy, and the function still rebuilds every plan reference before return.
        """

        fingerprints: dict[str, str] = {}

        def refresh(item: Any) -> Any:
            if isinstance(item, list):
                for index, child in enumerate(item):
                    item[index] = refresh(child)
                return item
            if not isinstance(item, dict):
                return item
            if "resolution_solution" in item:
                raise ValueError(
                    "rule packs cannot carry campaign-compiled resolution_solution state"
                )
            for key, child in list(item.items()):
                item[key] = refresh(child)
            required_plan_fields = {
                "schema_version",
                "id",
                "source_card_id",
                "source_card_kind",
                "trigger",
                "steps",
                "citations",
            }
            if required_plan_fields.issubset(item):
                candidate = dict(item)
                candidate.pop("fingerprint", None)
                compiled = _support.compile_resolution_plan(candidate)
                fingerprints[compiled.id] = compiled.fingerprint
                return _support.resolution_plan_template(compiled)
            return item

        def refresh_references(item: Any, *, parent: str = "") -> Any:
            if isinstance(item, list):
                for index, child in enumerate(item):
                    item[index] = refresh_references(child, parent=parent)
                return item
            if not isinstance(item, dict):
                return item
            if (
                parent == "resolution_plan"
                and set(item).issubset({"id", "fingerprint"})
                and str(item.get("id") or "") in fingerprints
            ):
                plan_id = str(item["id"])
                return {"id": plan_id, "fingerprint": fingerprints[plan_id]}
            for key, child in list(item.items()):
                item[key] = refresh_references(child, parent=key)
            return item

        return refresh_references(refresh(value))

    def reviewed_official_runtime_artifact(
        self,
        pack_id: str,
        version: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        if pack_id not in _support._reserved_official_definition_owners():
            return artifact
        verified = self.verified_reserved_official_rule_definition(pack_id, version)
        if verified is None:
            raise _support.RulesetUnavailableError(
                "official selection requires its verified archive"
            )
        matches = [
            item
            for item in verified["package"]["content"].get("artifacts", [])
            if item.get("rule_definition_id") == pack_id and item.get("id") == artifact.get("id")
        ]
        if len(matches) != 1:
            raise _support.RulesetUnavailableError(
                "official selection review identity is ambiguous or absent"
            )
        runtime_matches = [
            item for item in verified["runtime_artifacts"] if item.get("id") == artifact.get("id")
        ]
        if len(runtime_matches) != 1 or _support.content_fingerprint(
            runtime_matches[0]
        ) != _support.content_fingerprint(artifact):
            raise _support.RulesetUnavailableError(
                "official selection changed during archive verification"
            )
        return _support._rebind_verified_official_review(runtime_matches[0], matches[0])

    def public_tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register one public MCP tool with all four protocol hints."""

        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            name = function.__name__
            signature = _support.inspect.signature(function)

            def validate_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
                if name in ACTION_PAYLOADS:
                    bound = signature.bind(*args, **kwargs)
                    validate_action_payload(name, dict(bound.arguments))

            read_only = any(
                marker in name
                for marker in (
                    "_query",
                    "_search",
                    "_status",
                    "_list",
                    "_get",
                    "_expand",
                    "_explain",
                    "capabilities",
                    "continuity_context",
                    "bounded_evaluation",
                    "skill_query",
                    "storage_status",
                )
            )
            idempotent = read_only or "idempotency_key" in function.__annotations__
            anticipated = (
                ValueError,
                _support.AccessDeniedError,
                _support.ExposureError,
                _support.RulesetUnavailableError,
                _support.RulePackError,
            )

            if _support.inspect.iscoroutinefunction(function):

                @_support.wraps(function)
                async def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        validate_payload(args, kwargs)
                        return await function(*args, **kwargs)
                    except _support.ToolError:
                        raise
                    except anticipated as exc:
                        raise _support.ToolError(str(exc)) from exc

            else:

                @_support.wraps(function)
                def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        validate_payload(args, kwargs)
                        return function(*args, **kwargs)
                    except _support.ToolError:
                        raise
                    except anticipated as exc:
                        # Anticipated, model-repairable execution failures become
                        # isError=true. Unknown methods/tools stay protocol errors.
                        raise _support.ToolError(str(exc)) from exc

            return self.mcp.tool(
                annotations=_support.ToolAnnotations(
                    read_only_hint=read_only,
                    destructive_hint=False if read_only else True,
                    idempotent_hint=idempotent,
                    open_world_hint=False,
                )
            )(guarded)

        return decorate

    def reviewed_statblock_fill_evidence(
        self,
        campaign_id: str,
        agent_fill: dict[str, Any] | None,
        *,
        rule_source_id: str | None = None,
        module_id: str | None = None,
        page_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """Verify every Agent-added attack against one managed source excerpt."""

        if not isinstance(agent_fill, dict):
            return []
        declarations = agent_fill.get("additional_actions", [])
        if not isinstance(declarations, list):
            raise ValueError("reviewed additional_actions must be a list")
        evidence: list[dict[str, Any]] = []
        for index, declaration in enumerate(declarations):
            if not isinstance(declaration, dict):
                raise ValueError(f"reviewed additional action {index} must be an object")
            source_ref = str(declaration.get("source_ref") or "").strip()
            source_excerpt = " ".join(str(declaration.get("source_excerpt") or "").split())
            kind, separator, identifier = source_ref.partition(":")
            if not separator or not identifier or not source_excerpt:
                raise ValueError(
                    f"reviewed additional action {index} requires managed source "
                    "evidence and an exact excerpt"
                )
            if rule_source_id is not None:
                if kind != "rule-chunk":
                    raise ValueError(
                        "reviewed rule-statblock additional actions must cite rule-chunk"
                    )
                expanded = self.rules.expand(identifier)
                expanded_source_id = str(dict(expanded.get("source") or {}).get("id") or "")
                if expanded_source_id != rule_source_id:
                    raise ValueError(
                        "reviewed additional action rule chunk does not belong to "
                        "the reviewed statblock source"
                    )
            elif module_id is not None:
                if kind != "module-chunk":
                    raise ValueError(
                        "reviewed module-statblock additional actions must cite module-chunk"
                    )
                expanded = self.modules.expand(identifier)
                if str(dict(expanded.get("module") or {}).get("id") or "") != module_id:
                    raise ValueError(
                        "reviewed additional action module chunk does not belong "
                        "to the reviewed module"
                    )
            else:
                raise RuntimeError("reviewed additional action validation has no source boundary")
            source_content = str(expanded.get("content") or "")
            compact_excerpt = _support._compact_agent_evidence(source_excerpt)
            compact_content = _support._compact_agent_evidence(source_content)
            if not compact_excerpt or not _support._agent_evidence_supports_fact(
                compact_excerpt,
                compact_content,
                max_edits=min(12, max(2, len(compact_excerpt) // 75)),
            ):
                raise ValueError(
                    "reviewed additional action source_excerpt is absent from "
                    f"managed source {source_ref}"
                )
            page_start = expanded.get("page_start")
            page_end = expanded.get("page_end")
            if (
                page_number is not None
                and page_start is not None
                and page_end is not None
                and not int(page_start) <= page_number <= int(page_end)
            ):
                raise ValueError(
                    "reviewed additional action source evidence is not on the "
                    "reviewed statblock page"
                )
            evidence.append(
                {
                    "source_ref": source_ref,
                    "kind": kind,
                    "id": identifier,
                    "page_start": page_start,
                    "page_end": page_end,
                    "content_sha256": _support.hashlib.sha256(
                        source_content.encode("utf-8")
                    ).hexdigest(),
                    "source_excerpt_sha256": _support.hashlib.sha256(
                        source_excerpt.encode("utf-8")
                    ).hexdigest(),
                }
            )
        return evidence

    def reviewed_statblock_fill_labels(self, fill: dict[str, Any]) -> list[str]:
        return [
            *[
                str(item.get("activity_id") or "")
                for item in fill.get("multiattack_options", [])
                if isinstance(item, dict)
            ],
            *[
                str(item.get("id") or item.get("name") or "")
                for item in fill.get("additional_actions", [])
                if isinstance(item, dict)
            ],
        ]

    def resolution_rolls(self, resolution_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize trusted engine output without exposing arbitrary result fields."""

        normalized: list[dict[str, Any]] = []
        seen: set[int] = set()

        def visit(value: Any) -> None:
            if len(normalized) >= 32:
                return
            if isinstance(value, dict):
                dice = value.get("rolls")
                if (
                    not isinstance(dice, list)
                    or not dice
                    or not all(
                        isinstance(item, int) and not isinstance(item, bool) for item in dice
                    )
                ):
                    dice = value.get("dice")
                total = value.get("total")
                if (
                    isinstance(dice, list)
                    and dice
                    and all(isinstance(item, int) and not isinstance(item, bool) for item in dice)
                    and isinstance(total, int)
                    and not isinstance(total, bool)
                ):
                    identity = id(value)
                    if identity not in seen:
                        seen.add(identity)
                        natural = value.get("natural")
                        kept = [natural] if isinstance(natural, int) else list(dice)
                        modifier = value.get("modifier")
                        if not isinstance(modifier, int) or isinstance(modifier, bool):
                            modifier = int(total) - int(kept[0]) if len(kept) == 1 else 0
                        normalized.append(
                            {
                                "roll_id": f"{resolution_id}:roll:{len(normalized) + 1}",
                                "expression": str(value.get("expression") or "d20"),
                                "dice": list(dice),
                                "kept": kept,
                                "modifier": int(modifier),
                                "total": int(total),
                            }
                        )
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(result)
        return normalized

    def resolution_outcome(self, result: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "kind",
            "success",
            "critical",
            "fumble",
            "hit",
            "successes",
            "failures",
            "outcome",
            "damage",
            "applied_amount",
            "hp_damage",
            "healed",
            "after_hp",
            "dc",
            "difficulty",
        }
        return {
            key: _support.deepcopy(value)
            for key, value in result.items()
            if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
        }

    def resolution_presentation_view(
        self,
        campaign_id: str,
        resolution_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        campaign = self.campaigns.get(campaign_id)
        events = [
            item
            for key in ("resolution_presentation_log", "resolution_log")
            for item in list(dict(campaign.state or {}).get(key) or [])
            if isinstance(item, dict) and str(item.get("id") or "") == resolution_id
        ]
        if len(events) != 1:
            raise LookupError("resolution presentation not found")
        event = _support.deepcopy(events[0])
        audience = dict(event.get("audience") or {})
        actor_refs = [str(item) for item in audience.get("actor_refs") or []]
        scope = str(audience.get("scope") or ("actors" if actor_refs else "dm"))
        if scope == "dm" and membership.role not in _support.CAMPAIGN_DM_ROLES:
            raise LookupError("resolution presentation not found")
        if scope == "principal" and (
            membership.role not in _support.CAMPAIGN_DM_ROLES
            and str(event.get("principal_id") or "") != principal_id
        ):
            raise LookupError("resolution presentation not found")
        if scope == "actors" and membership.role not in _support.CAMPAIGN_DM_ROLES:
            authorized = False
            for actor_ref in actor_refs:
                try:
                    self.access.require_actor(campaign_id, actor_ref, principal_id, private=True)
                except (LookupError, PermissionError):
                    continue
                authorized = True
                break
            if not authorized:
                raise LookupError("resolution presentation not found")
        result = _support.deepcopy(dict(event.get("result") or {}))
        event_sequence = int(event.get("event_sequence") or 1)
        return {
            "schema": "sagasmith.resolution-presentation/v1",
            "resolution_id": resolution_id,
            "thread_id": str(event.get("thread_id") or resolution_id),
            "event_sequence": event_sequence,
            "system_id": _support.DND5E.id,
            "campaign_id": campaign_id,
            "branch_id": event.get("branch_id"),
            "operation": str(event.get("operation") or event.get("type") or "resolution"),
            "status": str(event.get("status") or "settled"),
            "audience": {
                "scope": scope,
                "actor_refs": actor_refs if scope == "actors" else [],
                "disclosure": str(
                    audience.get("disclosure") or ("private" if scope == "actors" else "hidden")
                ),
            },
            "actor_refs": actor_refs,
            "rolls": self.resolution_rolls(resolution_id, result),
            "outcome": self.resolution_outcome(result),
            "pending_choice": _support.deepcopy(event.get("pending_choice")),
            "campaign_revision": int(event.get("campaign_revision") or campaign.revision),
            "random_stream_receipt": {
                key: _support.deepcopy(value)
                for key, value in dict(event.get("random_stream_receipt") or {}).items()
                if key
                in {
                    "operation",
                    "cursor_before",
                    "cursor_after",
                    "position_before",
                    "position_after",
                    "draw_count",
                    "receipt_digest",
                }
            },
        }

    def agent_resolution_commitment(
        self,
        *,
        application_id: str,
        plan_id: str,
        plan_fingerprint: str,
        bound_plan_fingerprint: str,
        source_card_id: str,
        source_card_kind: str,
        bindings: dict[str, Any],
        agent_ruling: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the immutable payment contract for one bound semantic plan."""

        return {
            "application_id": application_id,
            "plan_id": plan_id,
            "plan_fingerprint": plan_fingerprint,
            "bound_plan_fingerprint": bound_plan_fingerprint,
            "source_card_id": source_card_id,
            "source_card_kind": source_card_kind,
            "bindings": _support.deepcopy(bindings),
            "agent_ruling": _support.deepcopy(agent_ruling),
        }

    def validate_agent_resolution_commitment(
        self,
        campaign_id: str,
        raw_commitment: Any,
        *,
        encounter: dict[str, Any],
        source_actor_id: str,
        source_card_id: str,
        source_card_kind: str,
        compiled_plan: Any,
        allow_paid_revision: bool = False,
    ) -> tuple[dict[str, Any], _support.BoundResolutionPlan]:
        """Bind an Agent decision only to slots declared by its recorded rule card."""

        if not isinstance(raw_commitment, dict):
            raise _support.CombatEngineError("agent_resolution_commitment must be an object")
        required_fields = {
            "application_id",
            "plan_id",
            "plan_fingerprint",
            "source_card_id",
            "source_card_kind",
            "bindings",
            "agent_ruling",
        }
        supplied_fields = set(raw_commitment)
        if supplied_fields != required_fields and supplied_fields != {
            *required_fields,
            "bound_plan_fingerprint",
        }:
            raise _support.CombatEngineError(
                "agent_resolution_commitment requires the exact plan-binding contract"
            )
        normalized_ruling = self.validate_current_scene_agent_ruling(
            campaign_id,
            raw_commitment.get("agent_ruling"),
            encounter=encounter,
            field="semantic plan commitment",
            allowed_ruling_kinds={
                "agent_dm_adjudication",
                "environmental_consequence",
                "generic_spell_effect",
                "module_specific_procedure",
                "source_or_scene_fact",
            },
        )
        if any(
            definition.get("owner") == "external_input"
            for definition in compiled_plan.slots.values()
        ):
            raise _support.NeedsRulingError(
                "semantic plan contains player-owned slots that require an "
                "external choice receipt before Agent settlement",
                missing=tuple(
                    f"player_choice:{slot_name}"
                    for slot_name, definition in compiled_plan.slots.items()
                    if definition.get("owner") == "external_input"
                ),
                ruling_kind="player_owned_choice",
            )
        try:
            bound = _support.bind_resolution_plan(
                compiled_plan,
                raw_commitment.get("bindings"),
                agent_ruling=normalized_ruling,
                edition=str(encounter.get("ruleset") or "2014"),
            )
        except _support.ResolutionPlanBindingError as error:
            raise _support.CombatEngineError(
                f"agent_resolution_commitment is invalid: {error}"
            ) from error
        normalized = self.agent_resolution_commitment(
            application_id=str(raw_commitment.get("application_id") or "").strip(),
            plan_id=str(raw_commitment.get("plan_id") or "").strip(),
            plan_fingerprint=str(raw_commitment.get("plan_fingerprint") or "").strip(),
            bound_plan_fingerprint=bound.fingerprint,
            source_card_id=str(raw_commitment.get("source_card_id") or "").strip(),
            source_card_kind=str(raw_commitment.get("source_card_kind") or "").strip(),
            bindings=bound.bindings,
            agent_ruling=normalized_ruling,
        )
        supplied_bound_fingerprint = str(raw_commitment.get("bound_plan_fingerprint") or "")
        if (
            not normalized["application_id"]
            or normalized["application_id"] != normalized_ruling["application_id"]
            or normalized["plan_id"] != compiled_plan.id
            or normalized["plan_fingerprint"] != compiled_plan.fingerprint
            or (supplied_bound_fingerprint and supplied_bound_fingerprint != bound.fingerprint)
            or normalized["source_card_id"] != source_card_id
            or normalized["source_card_kind"] != source_card_kind
        ):
            raise _support.CombatEngineError(
                "agent_resolution_commitment does not match the recorded plan"
            )
        for slot_name, definition in compiled_plan.slots.items():
            if definition.get("kind") not in {
                "actor_id",
                "actor_ids",
            }:
                continue
            raw_actor_ids = bound.bindings[slot_name]
            actor_ids = [raw_actor_ids] if isinstance(raw_actor_ids, str) else list(raw_actor_ids)
            for target_id in actor_ids:
                self.require_campaign_actor(campaign_id, str(target_id))
                self.require_encounter_combatant(
                    encounter,
                    str(target_id),
                    role=f"semantic plan slot {slot_name}",
                )
        for step in bound.steps:
            step_source_actor_id = dict(step.get("args") or {}).get("source_actor_id")
            if step_source_actor_id is not None and str(step_source_actor_id) != source_actor_id:
                raise _support.CombatEngineError(
                    "semantic plan source_actor_id must match the actor paying for the source card"
                )
        self.require_campaign_actor(campaign_id, source_actor_id)
        self.require_encounter_combatant(
            encounter,
            source_actor_id,
            role="semantic plan source",
        )
        self.validate_resolution_target_facts(
            campaign_id, encounter, bound, source_actor_id,
            allow_paid_revision=allow_paid_revision,
        )
        return normalized, bound

    def validate_resolution_target_facts(
        self, campaign_id, encounter, bound, source_actor_id, *, allow_paid_revision=False,
    ) -> None:
        evidence = (bound.agent_ruling or {}).get("target_facts")
        steps = {step["id"]: step for step in bound.steps if step["op"] == "target.validate"}
        supplied = {}
        if evidence is not None:
            revision = self.campaigns.get(campaign_id).revision
            allowed_revisions = {revision, revision - 1} if allow_paid_revision else {revision}
            if (not isinstance(evidence, dict) or set(evidence) != {
                "encounter_id", "scene_id", "campaign_revision", "steps",
            } or evidence.get("encounter_id") != encounter.get("id")
                    or evidence.get("scene_id") != encounter.get("scene_id")
                    or type(evidence.get("campaign_revision")) is not int
                    or evidence["campaign_revision"] not in allowed_revisions
                    or not isinstance(evidence.get("steps"), dict)):
                raise _support.CombatEngineError(
                    "target_facts must bind the current scene/revision"
                )
            supplied = evidence["steps"]
            if set(supplied) - set(steps):
                raise _support.CombatEngineError("target_facts names an unknown targeting step")
            for step_id, facts in supplied.items():
                if (not isinstance(facts, dict) or set(facts) != {"source_actor_id", "targets"}
                        or facts.get("source_actor_id") != source_actor_id
                        or not isinstance(facts.get("targets"), dict)):
                    raise _support.CombatEngineError("target_facts must bind source and targets")
                arguments = steps[step_id]["args"]
                for target_id, value in facts["targets"].items():
                    if (not isinstance(value, dict) or not value
                            or set(value) - {"visible", "distance_ft"}
                            or ("visible" in value and type(value["visible"]) is not bool)
                            or ("distance_ft" in value and (
                                type(value["distance_ft"]) is not int or value["distance_ft"] < 0
                            ))):
                        raise _support.CombatEngineError("invalid semantic target fact")
                    targets = arguments.get("target_ids")
                    if isinstance(targets, list) and all(isinstance(t, str) for t in targets):
                        if target_id not in targets:
                            raise _support.CombatEngineError(
                                "target fact is outside the bound step"
                            )
                    self.require_campaign_actor(campaign_id, target_id)
                    self.require_encounter_combatant(encounter, target_id, role="target fact")
        # Detect missing evidence before the source activity charges its action/resources.
        for step_id, step in steps.items():
            arguments = step["args"]
            targets = arguments.get("target_ids")
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                continue  # Result references are checked against actual facts during execution.
            facts = supplied.get(step_id, {}).get("targets", {})
            for target_id in targets:
                combatant = self.require_encounter_combatant(
                    encounter, target_id, role="semantic targeting evidence",
                )
                required = []
                if arguments.get("require_visible") and not isinstance(
                    combatant.get("visible_to_actor_ids"), list,
                ) and "visible" not in facts.get(target_id, {}):
                    required.append("visible")
                if (encounter.get("positioning_mode") == "agent"
                        and arguments.get("maximum_range_ft") is not None
                        and "distance_ft" not in facts.get(target_id, {})):
                    required.append("distance_ft")
                if required:
                    raise _support.NeedsRulingError(
                        "semantic targeting requires scene-bound target_facts",
                        missing=tuple(
                            f"target_facts:{step_id}:{target_id}:{key}" for key in required
                        ),
                        ruling_kind="source_or_scene_fact",
                    )

    def require_agent_resolution_payment(
        self,
        encounter: dict[str, Any],
        *,
        campaign_id: str,
        branch_id: str,
        source_actor_id: str,
        source_card_id: str,
        source_card_kind: str,
        commitment: dict[str, Any],
        bound_plan: _support.BoundResolutionPlan,
    ) -> dict[str, Any]:
        """Require the exact paid current-turn declaration before plan execution."""

        current = _support.current_combatant(encounter)
        current_round = int(encounter.get("round", 1) or 1)
        current_turn_index = int(encounter.get("turn_index", 0) or 0)
        if current is None or str(current.get("actor_id") or "") != source_actor_id:
            raise _support.CombatEngineError(
                "semantic plan must settle during its source actor's paid turn"
            )
        if source_card_kind == "item":
            window = next(
                (
                    item
                    for item in encounter.get("pending", [])
                    if isinstance(item, dict)
                    and str(item.get("id") or "") == str(commitment.get("application_id") or "")
                ),
                None,
            )
            if (
                not isinstance(window, dict)
                or window.get("trigger") not in {"attack_semantic_plan", "attack_on_hit_effect"}
                or str(window.get("attacker_id") or "") != source_actor_id
                or str(window.get("weapon_id") or "") != source_card_id
                or (
                    window.get("trigger") == "attack_semantic_plan"
                    and str(window.get("plan_fingerprint") or "") != bound_plan.compiled.fingerprint
                )
            ):
                raise _support.CombatEngineError(
                    "item semantic plan requires its exact pending on-hit event"
                )
            target_id = str(window.get("target_id") or "")
            trigger_event = {
                "trigger": "attack.after_hit",
                "application_id": str(window["id"]),
                "attack_ref": str(window.get("attack_ref") or ""),
                "branch_id": str(window.get("branch_id") or branch_id),
                "campaign_id": str(window.get("campaign_id") or campaign_id),
                "critical": bool(window.get("critical", False)),
                "hit": True,
                "round": current_round,
                "source_actor_id": source_actor_id,
                "target_actor_id": target_id,
                "turn": current_turn_index,
                "weapon_id": source_card_id,
            }
            try:
                _support.require_resolution_plan_trigger(
                    bound_plan,
                    trigger_event,
                )
            except _support.ResolutionPlanExecutionError as error:
                raise _support.CombatEngineError(str(error)) from error
            return {
                "type": "attack_after_hit",
                "application_id": str(window["id"]),
                "actor_id": source_actor_id,
                "target_id": target_id,
                "weapon_id": source_card_id,
                "round": current_round,
                "turn_index": current_turn_index,
                "trigger_event": trigger_event,
            }
        for entry in reversed(list(encounter.get("log") or [])):
            if not isinstance(entry, dict):
                continue
            if (
                int(entry.get("round", -1)) != current_round
                or int(entry.get("turn_index", -1)) != current_turn_index
                or str(entry.get("actor_id") or "") != source_actor_id
            ):
                continue
            declaration: Any = None
            if (
                source_card_kind in {"activity", "feature", "monster_action", "trait"}
                and entry.get("type") == "activity"
                and str(entry.get("activity_id") or "") == source_card_id
            ):
                declaration = dict(entry.get("declaration") or {}).get(
                    "agent_resolution_commitment"
                )
            elif (
                source_card_kind == "spell"
                and entry.get("type") == "common_action"
                and entry.get("action") == "cast"
                and str(dict(entry.get("payload") or {}).get("spell_id") or "") == source_card_id
            ):
                declaration = dict(entry.get("payload") or {}).get("agent_resolution_commitment")
            elif (
                source_card_kind == "scene_procedure"
                and entry.get("type") == "common_action"
                and entry.get("action") == "improvise"
                and str(dict(entry.get("payload") or {}).get("procedure_id") or "")
                == source_card_id
            ):
                declaration = dict(entry.get("payload") or {}).get("agent_resolution_commitment")
            if declaration == commitment:
                payment = _support.deepcopy(entry)
                if bound_plan.compiled.schema_version >= 2:
                    trigger_event = {
                        "trigger": "action",
                        "action_kind": (
                            str(entry.get("activity_id") or "")
                            if entry.get("type") == "activity"
                            else str(entry.get("action") or "")
                        ),
                        "action_ref": source_card_id,
                        "actor_id": source_actor_id,
                        "branch_id": branch_id,
                        "campaign_id": campaign_id,
                        "round": current_round,
                        "turn": current_turn_index,
                    }
                    try:
                        _support.require_resolution_plan_trigger(
                            bound_plan,
                            trigger_event,
                        )
                    except _support.ResolutionPlanExecutionError as error:
                        raise _support.CombatEngineError(str(error)) from error
                    payment["trigger_event"] = trigger_event
                return payment
        raise _support.CombatEngineError(
            "semantic plan requires its exact current-turn commitment to be "
            "paid by the source activity, item hit, spell, or scene procedure"
        )

    def party_view_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        value = _support.validate_party_state(state)
        sheet = self.party_sheet(value)
        return {
            "inventory": sheet["inventory"],
            "derived": self.derive_character_sheet(sheet)["inventory"],
            "notes": value["party"]["notes"],
        }

    def validate_agent_text_statblock_review(
        self,
        *,
        source_id: str,
        page_number: int,
        content: str,
        parsed: Any,
        evidence_chunk_ids: list[str] | None,
        evidence_exclusions: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Bind an Agent transcription to one contiguous indexed page segment."""

        if not isinstance(evidence_chunk_ids, list):
            raise ValueError("evidence_chunk_ids must be a list for an agent_text statblock review")
        chunk_ids = [str(item).strip() for item in evidence_chunk_ids]
        if (
            not chunk_ids
            or len(chunk_ids) > 200
            or any(not item for item in chunk_ids)
            or len(chunk_ids) != len(set(chunk_ids))
        ):
            raise ValueError("evidence_chunk_ids must contain 1 to 200 unique non-empty ids")
        available = self.rules.source_chunks(source_id)
        by_id = {str(item.get("id") or ""): item for item in available}
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in by_id]
        if missing:
            raise ValueError(
                "agent_text statblock evidence chunks do not belong to the rule source"
            )
        selected = [by_id[chunk_id] for chunk_id in chunk_ids]
        for chunk in selected:
            page_start = chunk.get("page_start")
            page_end = chunk.get("page_end")
            if (
                isinstance(page_start, bool)
                or not isinstance(page_start, int)
                or isinstance(page_end, bool)
                or not isinstance(page_end, int)
                or not page_start <= page_number <= page_end
            ):
                raise ValueError("agent_text statblock evidence chunks must all cover page_number")
        ordinals = [item.get("ordinal") for item in selected]
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in ordinals
            )
            or ordinals != sorted(ordinals)
            or any(int(right) != int(left) + 1 for left, right in zip(ordinals, ordinals[1:]))
        ):
            raise ValueError(
                "agent_text statblock evidence chunks must be one ordered contiguous segment"
            )

        if evidence_exclusions is None:
            exclusions: list[dict[str, Any]] = []
        elif not isinstance(evidence_exclusions, list) or len(evidence_exclusions) > 50:
            raise ValueError("evidence_exclusions must be a list with at most 50 entries")
        else:
            exclusions = evidence_exclusions
        selected_ids = set(chunk_ids)
        validated_exclusions: list[dict[str, Any]] = []
        exclusions_by_chunk: dict[str, list[tuple[int, int]]] = {}
        for index, exclusion in enumerate(exclusions):
            if not isinstance(exclusion, dict) or set(exclusion) != {
                "chunk_id",
                "exact_text",
                "reason",
            }:
                raise ValueError(
                    "each evidence exclusion must contain only chunk_id, exact_text, and reason"
                )
            chunk_id = str(exclusion.get("chunk_id") or "").strip()
            exact_text = str(exclusion.get("exact_text") or "")
            reason = str(exclusion.get("reason") or "").strip()
            if chunk_id not in selected_ids:
                raise ValueError(
                    "evidence exclusion chunk_id must belong to the selected contiguous segment"
                )
            if not exact_text or len(exact_text) > 20_000:
                raise ValueError("evidence exclusion exact_text must contain 1 to 20000 characters")
            if not 8 <= len(reason) <= 500:
                raise ValueError("evidence exclusion reason must contain 8 to 500 characters")
            source_text = str(by_id[chunk_id].get("content") or "")
            if source_text.count(exact_text) != 1:
                raise ValueError(
                    "evidence exclusion exact_text must occur exactly once in its source chunk"
                )
            start = source_text.index(exact_text)
            end = start + len(exact_text)
            existing_ranges = exclusions_by_chunk.setdefault(chunk_id, [])
            if any(
                start < existing_end and end > existing_start
                for existing_start, existing_end in existing_ranges
            ):
                raise ValueError("evidence exclusion ranges must not overlap")
            existing_ranges.append((start, end))
            validated_exclusions.append(
                {
                    "chunk_id": chunk_id,
                    "start": start,
                    "end": end,
                    "exact_text_sha256": _support.hashlib.sha256(
                        exact_text.encode("utf-8")
                    ).hexdigest(),
                    "reason": reason,
                    "ordinal": index,
                }
            )

        retained_content_by_id: dict[str, str] = {}
        for chunk in selected:
            chunk_id = str(chunk["id"])
            retained = str(chunk.get("content") or "")
            for start, end in sorted(
                exclusions_by_chunk.get(chunk_id, []),
                reverse=True,
            ):
                retained = retained[:start] + retained[end:]
            retained_content_by_id[chunk_id] = retained

        evidence_parts: list[str] = []
        for chunk in selected:
            evidence_parts.extend(
                str(value) for value in chunk.get("heading_path", []) if str(value).strip()
            )
            evidence_parts.append(retained_content_by_id[str(chunk["id"])])
        compact_evidence = _support._compact_agent_evidence("\n".join(evidence_parts))
        compact_review_body = _support._compact_agent_evidence(
            "\n".join(
                line
                for line in content.splitlines()
                if not _support.re.match(r"^\s*#{1,6}\s+", line)
            )
        )
        unsupported_lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            fact = _support._compact_agent_evidence(line)
            if line.startswith("|"):
                if fact == "strdexconintwischa" or not fact:
                    continue
                ability_cells = _support.re.findall(
                    r"\|\s*(\d+)\s*\(\s*([+-])\s*(\d+)\s*\)",
                    line,
                )
                if len(ability_cells) == 6:
                    continue
                unsupported_lines.append(line)
                continue
            if not fact and any(character.isalnum() for character in line):
                unsupported_lines.append(line)
                continue
            if fact and not _support._agent_evidence_supports_fact(fact, compact_evidence):
                unsupported_lines.append(line)
        if unsupported_lines:
            raise ValueError(
                "agent_text normalized_content contains facts absent from the selected "
                f"evidence: {unsupported_lines[0][:160]}"
            )

        ability_scores = dict(parsed.sheet.get("abilities") or {})
        ability_labels = {
            "STR": "strength",
            "DEX": "dexterity",
            "CON": "constitution",
            "INT": "intelligence",
            "WIS": "wisdom",
            "CHA": "charisma",
        }
        ability_value_line = next(
            (
                line
                for line in content.splitlines()
                if len(
                    _support.re.findall(
                        r"\|\s*(\d+)\s*\(\s*([+-])\s*(\d+)\s*\)",
                        line,
                    )
                )
                == 6
            ),
            "",
        )
        reviewed_ability_cells = _support.re.findall(
            r"\|\s*(\d+)\s*\(\s*([+-])\s*(\d+)\s*\)",
            ability_value_line,
        )
        if len(reviewed_ability_cells) != 6:
            raise ValueError(
                "agent_text normalized_content must preserve six explicit ability score modifiers"
            )
        for abbreviation, ability in ability_labels.items():
            score = int(dict(ability_scores.get(ability) or {}).get("score", 0))
            source_ability = next(
                (
                    _support.re.match(
                        r"^\s*(\d+)\s*\(\s*([+-])\s*(\d+)\s*\)",
                        _support.re.sub(
                            r"(?i)^\s*([li]+)(?=\d*\s*\()",
                            lambda match: "1" * len(match.group(1)),
                            str(chunk.get("content") or ""),
                        ),
                    )
                    for chunk in selected
                    if str(list(chunk.get("heading_path") or [""])[-1]).strip().upper()
                    == abbreviation
                ),
                None,
            )
            if source_ability is None:
                raise ValueError(f"agent_text evidence does not support {abbreviation} {score}")
            ability_index = list(ability_labels).index(abbreviation)
            if int(source_ability.group(1)) != score or reviewed_ability_cells[ability_index] != (
                source_ability.group(1),
                source_ability.group(2),
                source_ability.group(3),
            ):
                raise ValueError(
                    f"agent_text normalized_content does not exactly preserve "
                    f"{abbreviation} score and modifier"
                )

        identity_pattern = _support.re.compile(
            r"(?i)(Tiny|Small|Medium|Large|Huge|Gargantuan)\s+[^,]+,\s*[^.]+"
        )
        ability_prefix = _support.re.compile(r"^\s*\d+\s*\([^)]+\)\s*")
        for chunk in selected:
            source_fact = retained_content_by_id[str(chunk["id"])].strip()
            if not source_fact:
                continue
            final_heading = str(list(chunk.get("heading_path") or [""])[-1]).upper()
            identity_matches = list(identity_pattern.finditer(source_fact))
            if identity_matches:
                if final_heading == "ACTIONS" and identity_matches[0].start() > 0:
                    source_fact = source_fact[: identity_matches[0].start()]
                else:
                    source_fact = source_fact[identity_matches[0].start() :]
                    following = list(identity_pattern.finditer(source_fact))
                    if len(following) > 1:
                        source_fact = source_fact[: following[1].start()]
            if final_heading in ability_labels:
                source_fact = _support.re.sub(
                    r"(?i)^\s*([li]+)(?=\d*\s*\()",
                    lambda match: "1" * len(match.group(1)),
                    source_fact,
                )
                source_fact = ability_prefix.sub("", source_fact, count=1).strip()
            source_segments = [
                value.strip()
                for value in _support.re.split(r"(?<=[.!?])\s+", source_fact)
                if value.strip()
            ]
            for source_segment in source_segments:
                required_fact = _support._compact_agent_evidence(source_segment)
                if required_fact and not _support._agent_evidence_supports_fact(
                    required_fact,
                    compact_review_body,
                    max_edits=min(12, max(2, len(required_fact) // 75)),
                ):
                    raise ValueError(
                        "agent_text normalized_content omits selected evidence from "
                        f"chunk {chunk['id']}"
                    )

        return [
            {
                "id": str(item["id"]),
                "ordinal": int(item["ordinal"]),
                "page_start": int(item["page_start"]),
                "page_end": int(item["page_end"]),
                "content_sha256": _support.hashlib.sha256(
                    str(item.get("content") or "").encode("utf-8")
                ).hexdigest(),
            }
            for item in selected
        ], validated_exclusions

    def validate_indexed_statblock_review(
        self,
        *,
        source_id: str,
        page_number: int,
        content: str,
        parsed: Any,
        evidence_chunk_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Prove a deterministic normalization against its exact source segment.

        ``indexed_text`` used to validate only ownership of the supplied chunk
        ids.  A normalized card could therefore mix fields from adjacent
        columns or creatures and still be labelled ``verified_indexed_text``.
        Deterministic extraction receives the same fact, order, page, and entry
        boundary checks as an Agent transcription; only the producer differs.
        """

        evidence, _ = self.validate_agent_text_statblock_review(
            source_id=source_id,
            page_number=page_number,
            content=content,
            parsed=parsed,
            evidence_chunk_ids=evidence_chunk_ids,
            evidence_exclusions=[],
        )
        return evidence

    def skill_list(self) -> list[dict[str, str]]:
        """List installed D&D DM, campaign-manager, and module-generator skill documents."""
        return [
            {
                "id": item.id,
                "title": item.title,
                "source": item.source,
                "checksum": item.checksum,
            }
            for item in self.catalog.list()
        ]

    def skill_read(self, skill_id: str) -> str:
        """Read one source-of-truth SKILL.md document."""
        return self.catalog.read(skill_id)

    def skill_asset_list(self, source: str | None = None) -> list[dict[str, str]]:
        """List bundled text references, templates, and data files."""
        return [
            {
                "id": asset.id,
                "source": asset.source,
                "checksum": asset.checksum,
                "resource_uri": (f"sagasmith://asset/{self.catalog.resource_id(asset.id)}"),
            }
            for asset in self.catalog.assets()
            if source is None or asset.source == source
        ]

    def skill_asset_read(self, asset_id: str) -> str:
        """Read one text skill asset by the id returned from skill_asset_list."""
        return self.catalog.read_asset(asset_id)

    def facade_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return dict(payload)

    def facade_bool(self, payload: dict[str, Any], name: str, *, default: bool = False) -> bool:
        value = payload.get(name, default)
        return _support._strict_boolean(value, f"payload.{name}")

    def require_facade_phase(self, campaign_id: str, action: str, *phases: str) -> None:
        phase = self.authoritative_phase(campaign_id)
        if phase not in phases:
            allowed = ", ".join(phases)
            raise _support.ExposureError(
                f"{action} is only available during {allowed}; current phase is {phase}."
            )

    def facade_result(
        self,
        action: str,
        result: Any,
        *,
        page: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _support._facade_result(action, result, page=page)

    def facade_render_result(self, rendered: Any) -> _support.RenderResult:
        if not isinstance(rendered, list) or len(rendered) != 2:
            raise TypeError("render facade received an invalid image result")
        if not isinstance(rendered[0], dict) or not isinstance(rendered[1], _support.Image):
            raise TypeError("render facade received an invalid image result")
        return _support.RenderResult(dict(rendered[0]), rendered[1])

    def resolution_presentation(
        self,
        campaign_id: str,
        resolution_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Return one audience-safe, authoritative resolution bubble projection."""

        return self.facade_result(
            "get",
            self.resolution_presentation_view(campaign_id, resolution_id, principal_id),
        )

    def skill_query(
        self,
        kind: Literal["skill", "asset"],
        action: Literal["list", "read", "outline", "section", "search"],
        identifier: str | None = None,
        source: str | None = None,
        heading: str | None = None,
        query: str | None = None,
        max_chars: int = 12_000,
        limit: int = 8,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Discover or read bounded installed workflow guidance."""
        if action == "outline":
            result = self.catalog.outline(
                kind=kind,
                identifier=self.required({"identifier": identifier}, "identifier"),
            )
        elif action == "section":
            result = self.catalog.section(
                kind=kind,
                identifier=self.required({"identifier": identifier}, "identifier"),
                heading=self.required({"heading": heading}, "heading"),
                max_chars=max_chars,
            )
        elif action == "search":
            result = self.catalog.search(
                kind=kind,
                identifier=identifier,
                query=self.required({"query": query}, "query"),
                limit=100,
            )
        elif kind == "skill":
            result = (
                self.skill_list()
                if action == "list"
                else self.skill_read(self.required({"identifier": identifier}, "identifier"))
            )
        else:
            result = (
                self.skill_asset_list(source)
                if action == "list"
                else self.skill_asset_read(self.required({"identifier": identifier}, "identifier"))
            )
        if isinstance(result, list):
            result, page = _support._bounded_page(
                result,
                scope=(
                    f"skill_query:{kind}:{action}:{str(identifier or source or '')}:"
                    f"{_support.json_sha256(str(query or ''))}"
                ),
                query="" if action == "search" else str(query or ""),
                limit=limit,
                cursor=cursor,
            )
            return self.facade_result(action, result, page=page)
        return self.facade_result(action, result)
