"""Continuity application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from .. import application_support as _support


class ContinuityService:
    def require_no_active_npc_conversation(
        self,
        campaign_id: str,
        *,
        branch_id: str | None = None,
        operation: str,
    ) -> None:
        resolved_branch_id = branch_id or self.current_branch_id(campaign_id)
        if resolved_branch_id and self.npc_conversations.active_ids(
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
        ):
            raise ValueError("close or abort the active NPC conversation before " + operation)

    def bounded_memory_epoch_digest(self, campaign_id: str, branch_id: str) -> str:
        heads = [
            (str(item.id), str(item.revision_id))
            for item in self.memories.list(
                campaign_id,
                branch_id=branch_id,
                include_inactive=True,
            )
        ]
        return _support.hashlib.sha256(
            _support.canonical_json(sorted(heads)).encode("utf-8")
        ).hexdigest()

    def bounded_knowledge_epoch_digest(
        self,
        campaign_id: str,
        branch_id: str,
        actor_ids: list[str],
    ) -> str:
        heads = [
            (actor_id, str(item.id), str(item.revision_id))
            for actor_id in sorted(set(actor_ids))
            for item in self.knowledge.list(
                campaign_id,
                actor_id=actor_id,
                branch_id=branch_id,
                include_inactive=True,
            )
        ]
        return _support.hashlib.sha256(
            _support.canonical_json(sorted(heads)).encode("utf-8")
        ).hexdigest()

    def require_healing_not_prevented(
        self,
        encounter: dict[str, Any] | None,
        *,
        target_id: str,
    ) -> None:
        if not isinstance(encounter, dict) or not encounter.get("active", False):
            return
        blocked = next(
            (
                effect
                for effect in encounter.get("ongoing_effects", [])
                if isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("kind") == "healing_prevention"
                and str(effect.get("target_id") or "") == target_id
            ),
            None,
        )
        if blocked is not None:
            raise _support.CombatEngineError(
                "target cannot regain hit points while a locked standard spell effect is active"
            )

    def memory_list(
        self,
        campaign_id: str,
        kind: str | None = None,
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """List durable world facts visible from one campaign branch."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return [
            _support.asdict(item)
            for item in self.memories.list(
                campaign_id,
                kind=kind,
                branch_id=branch_id,
                include_inactive=include_inactive,
            )
        ]

    def memory_search(
        self,
        campaign_id: str,
        query: str,
        limit: int = 8,
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        include_inactive: bool = False,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Retrieve branch-scoped durable world facts for DM administration."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return [
            _support.asdict(item)
            for item in self.memories.search(
                campaign_id,
                query,
                limit=limit,
                offset=offset,
                branch_id=branch_id,
                include_inactive=include_inactive,
            )
        ]

    def validate_continuity_knowledge_source_audiences(
        self,
        campaign_id: str,
        branch_id: str,
        event_data: dict[str, Any],
        knowledge_data: list[dict[str, Any]],
    ) -> None:
        """Validate every knowledge source in an event-plus-deltas commit."""
        event_audience_scope = str(event_data.get("audience_scope") or "dm")
        for index, item in enumerate(knowledge_data):
            action = str(item.get("action", "add"))
            disclosure_scope = item.get("disclosure_scope")
            source_event_id = item.get("source_event_id")
            if action == "revise" and item.get("knowledge_id"):
                current = self.knowledge.get(str(item["knowledge_id"]), branch_id=branch_id)
                if disclosure_scope is None:
                    disclosure_scope = current.disclosure_scope
            if disclosure_scope is None:
                disclosure_scope = "dm"
            try:
                self.validate_actor_knowledge_source_audience(
                    campaign_id,
                    branch_id,
                    source_event_id=(str(source_event_id) if source_event_id is not None else None),
                    event_audience_scope=(
                        None if source_event_id is not None else event_audience_scope
                    ),
                    disclosure_scope=str(disclosure_scope),
                )
            except ValueError as exc:
                raise ValueError(f"payload.actor_knowledge[{index}]: {exc}") from exc

    def event_add(
        self,
        campaign_id: str,
        summary: str,
        event_type: str = "narrative",
        payload: dict[str, Any] | None = None,
        audience_scope: str = "dm",
        branch_id: str | None = None,
        known_by_actor_ids: list[str] | None = None,
        knowledge_key: str | None = None,
        knowledge_proposition: str | None = None,
        knowledge_disclosure_scope: str = "owner",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append a branch-local chronology event and optional actor knowledge.

        A DM-only event cannot be the source of player-visible ActorKnowledge;
        use a party/player/public (or actor-targeted) event for knowledge that
        may be projected to players.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for event writes")
        branch_id = self.require_current_branch(campaign_id, branch_id)
        if audience_scope == "actor" and not known_by_actor_ids:
            raise ValueError("actor-scoped events require known_by_actor_ids")
        if known_by_actor_ids:
            if not knowledge_key or not knowledge_proposition:
                raise ValueError(
                    "knowledge_key and knowledge_proposition are required when actors are listed"
                )
            for actor_id in known_by_actor_ids:
                self.access.require_actor(campaign_id, actor_id, principal_id, private=True)
            self.validate_actor_knowledge_source_audience(
                campaign_id,
                branch_id,
                source_event_id=None,
                event_audience_scope=audience_scope,
                disclosure_scope=knowledge_disclosure_scope,
            )
        self.validate_embedded_module_source_refs(
            campaign_id,
            payload or {},
            field="event.payload",
        )
        request_payload = {
            "summary": summary,
            "event_type": event_type,
            "payload": payload or {},
            "audience_scope": audience_scope,
            "branch_id": branch_id,
            "known_by_actor_ids": known_by_actor_ids or [],
            "knowledge_key": knowledge_key,
            "knowledge_proposition": knowledge_proposition,
            "knowledge_disclosure_scope": knowledge_disclosure_scope,
        }
        scope = f"event-add:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        if known_by_actor_ids:
            created, knowledge_ids = self.events.add_with_actor_knowledge(
                campaign_id,
                summary=summary,
                actor_ids=known_by_actor_ids,
                knowledge_key=knowledge_key,
                proposition=knowledge_proposition,
                event_type=event_type,
                payload=payload,
                audience_scope=audience_scope,
                disclosure_scope=knowledge_disclosure_scope,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=request_payload,
                    response=lambda result: {
                        **_support.asdict(result[0]),
                        "actor_knowledge_ids": result[1],
                    },
                ),
            )
        else:
            created = self.events.add(
                campaign_id,
                summary=summary,
                event_type=event_type,
                payload=payload,
                audience_scope=audience_scope,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=request_payload,
                    response=lambda result: {
                        **_support.asdict(result),
                        "actor_knowledge_ids": [],
                    },
                ),
            )
            knowledge_ids = []
        response = {**_support.asdict(created), "actor_knowledge_ids": knowledge_ids}
        return response

    def event_list(
        self,
        campaign_id: str,
        limit: int = 50,
        branch_id: str | None = None,
        actor_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        resolved_branch_id = self.readable_branch(campaign_id, branch_id, principal_id)
        audience = "dm" if membership.role in _support.CAMPAIGN_DM_ROLES else "player"
        if actor_id:
            self.access.require_actor(
                campaign_id,
                actor_id,
                principal_id,
                private=True,
                branch_id=resolved_branch_id,
            )
        values = self.events.list_for_audience(
            campaign_id,
            audience=audience,
            actor_id=actor_id,
            limit=limit,
            offset=offset,
            branch_id=resolved_branch_id,
        )
        return [_support.asdict(item) for item in values]

    def continuity_context(
        self,
        campaign_id: str,
        query: str = "",
        actor_id: str | None = None,
        scope_id: str = "party",
        audience: str = "dm",
        purpose: Literal[
            "general",
            "npc_turn",
            "actor_turn",
            "audience_render",
            "faction_turn",
            "campaign_expansion",
            "source_interpretation",
            "bounded_ruling",
            "actor_memory",
        ] = "general",
        subject_ref: str | None = None,
        evaluation_target_refs: list[str] | None = None,
        interlocutor_actor_ids: list[str] | None = None,
        stimulus: dict[str, Any] | None = None,
        conversation_limit: int = 8,
        branch_id: str | None = None,
        limit: int = 8,
        budget_chars: int = 12_000,
        related_refs: list[str] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Retrieve current continuity plus pinned, source-exact DM module context."""
        continuity_purposes = (
            _support.NPC_TURN_PURPOSES | _support.BOUNDED_EVALUATION_PURPOSES | {"actor_memory"}
        )
        if purpose not in continuity_purposes:
            raise ValueError(f"unsupported continuity context purpose: {purpose}")
        live_turn_purposes = {
            "npc_turn",
            "actor_turn",
            "audience_render",
            "faction_turn",
        }
        if purpose in live_turn_purposes and self.authoritative_phase(campaign_id) not in {
            _support.PROFILE_PLAY,
            _support.PROFILE_COMBAT,
        }:
            raise ValueError(f"{purpose} context is available only during Play or Combat")
        if (
            purpose == "campaign_expansion"
            and self.authoritative_phase(campaign_id) != _support.PROFILE_LOBBY
        ):
            raise ValueError("campaign_expansion context is available only during Lobby")
        membership = self.access.require_campaign(campaign_id, principal_id)
        branch_id = self.readable_branch(campaign_id, branch_id, principal_id)
        if audience not in {"dm", "player"}:
            raise ValueError("audience must be dm or player")
        dm_only_purposes = {
            "npc_turn",
            "actor_turn",
            "faction_turn",
            "source_interpretation",
            "bounded_ruling",
            "campaign_expansion",
        }
        if purpose in dm_only_purposes and membership.role not in _support.CAMPAIGN_DM_ROLES:
            raise ValueError(f"{purpose} context is available only to Owner/DM")
        if purpose in {"npc_turn", "actor_turn", "actor_memory"} and not actor_id:
            raise ValueError(f"actor_id is required for {purpose} context")
        if membership.role not in _support.CAMPAIGN_DM_ROLES:
            audience = "player"
            scope_id = self.readable_scene_scope(campaign_id, scope_id, principal_id)
            if actor_id:
                self.access.require_actor(
                    campaign_id,
                    actor_id,
                    principal_id,
                    private=True,
                    branch_id=branch_id,
                )
        elif actor_id:
            self.access.require_actor(
                campaign_id,
                actor_id,
                principal_id,
                private=True,
                branch_id=branch_id,
            )
        if purpose == "audience_render" and audience != "player":
            raise ValueError("audience_render requires audience='player'")
        resolved_related_refs: set[str] = set()
        for item in list(related_refs or []):
            candidate = str(item or "").strip()
            if candidate.startswith("event:"):
                if _support.re.fullmatch(r"event:[^\s:][^\s]{0,279}", candidate) is None:
                    raise ValueError("related_refs[] event refs must use event:<non-empty-id>")
                resolved_related_refs.add(candidate)
            else:
                resolved_related_refs.add(
                    _support.normalize_context_entity_ref(item, field="related_refs[]")
                )
        if actor_id:
            resolved_related_refs.add(f"actor:{actor_id}")
        normalized_interlocutor_ids: list[str] = []
        for item in interlocutor_actor_ids or []:
            value = str(item).strip()
            if not value:
                raise ValueError("interlocutor_actor_ids must not contain blank ids")
            if value in normalized_interlocutor_ids:
                raise ValueError("interlocutor_actor_ids must be unique")
            normalized_interlocutor_ids.append(value)
            resolved_related_refs.add(f"actor:{value}")
        normalized_target_refs: list[str] = []
        for item in evaluation_target_refs or []:
            value = _support.normalize_context_entity_ref(item, field="evaluation_target_refs[]")
            if value in normalized_target_refs:
                raise ValueError("evaluation_target_refs must be unique")
            normalized_target_refs.append(value)
            resolved_related_refs.add(value)
        normalized_subject_ref: str | None = None
        if subject_ref is not None:
            normalized_subject_ref = _support.normalize_context_entity_ref(
                subject_ref, field="subject_ref"
            )
            resolved_related_refs.add(normalized_subject_ref)
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            campaign = self.campaigns.get(campaign_id)
            state = dict(campaign.state or {})
            combat = dict(state.get("combat") or {})
            if combat.get("active", False):
                for combatant in [
                    *list(combat.get("combatants") or []),
                    *list(combat.get("reinforcements") or []),
                ]:
                    combat_actor_id = str(dict(combatant or {}).get("actor_id") or "").strip()
                    if combat_actor_id:
                        resolved_related_refs.add(f"actor:{combat_actor_id}")
            manifest = dict(state.get("playthrough_manifest") or {})
            current = dict(manifest.get("current") or {})
            if current_scene_id := str(current.get("scene_id") or "").strip():
                resolved_related_refs.add(f"scene:{current_scene_id}")
            if current_module_id := str(current.get("module_id") or "").strip():
                resolved_related_refs.add(f"module:{current_module_id}")
            for quest in list(manifest.get("quests") or []):
                quest_value = dict(quest or {})
                if str(quest_value.get("status") or "") != "active":
                    continue
                quest_id = str(quest_value.get("id") or "").strip()
                if quest_id:
                    resolved_related_refs.add(f"quest:{quest_id}")
        result = self.continuity.context(
            campaign_id,
            query=query,
            actor_id=actor_id,
            scope_id=scope_id,
            audience=audience,
            branch_id=branch_id,
            limit=limit,
            budget_chars=budget_chars,
            related_refs=sorted(
                item for item in resolved_related_refs if not item.startswith("event:")
            ),
        )
        if purpose == "actor_memory":
            assert actor_id is not None
            actor = self.characters.get(actor_id)
            if actor.campaign_id != campaign_id:
                raise ValueError("actor memory belongs to another campaign")
            memory_view = self.actor_memory_projection(
                campaign_id=campaign_id,
                branch_id=branch_id,
                actor=actor,
                query=query,
                current_refs=resolved_related_refs,
                budget_chars=budget_chars,
                retrieved_events=list(result.get("events") or []),
                audience=audience,
                knowledge_disclosure_scopes=(
                    _support.ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES
                    if membership.role in _support.CAMPAIGN_DM_ROLES
                    else _support.PLAYER_OWNED_ACTOR_DISCLOSURE_SCOPES
                ),
            )
            response = {
                "schema_version": 1,
                "purpose": "actor_memory",
                "campaign_id": campaign_id,
                "branch_id": branch_id,
                "actor": self.npc_turn_actor_projection(actor),
                "scene": self.npc_turn_scene_projection(result.get("scoped_scene")),
                "memory": memory_view,
                "retrieval": {
                    **dict(result.get("retrieval") or {}),
                    "strategy": memory_view["diagnostics"]["strategy"],
                },
                "host_context_binding": self.host_context_binding(
                    campaign_id=campaign_id,
                    branch_id=branch_id,
                    principal_id=principal_id,
                    role=membership.role,
                    audience=audience,
                ),
            }
            response["context_receipt"] = self.issue_context_receipt(
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                audience=audience,
                actor_id=actor_id,
                scope_id=scope_id,
                related_refs=sorted(resolved_related_refs),
                context=response,
            )
            return response
        if purpose in _support.BOUNDED_EVALUATION_PURPOSES:
            return self.bounded_evaluation_bundle(
                purpose=purpose,
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                role=membership.role,
                audience=audience,
                actor_id=actor_id,
                subject_ref=normalized_subject_ref,
                target_refs=normalized_target_refs,
                query=query,
                result=result,
                related_refs=resolved_related_refs,
                interlocutor_actor_ids=normalized_interlocutor_ids,
                stimulus=stimulus,
            )
        if purpose == "npc_turn":
            assert actor_id is not None
            actor = self.characters.get(actor_id)
            if actor.campaign_id != campaign_id:
                raise ValueError("NPC turn actor belongs to another campaign")
            if actor.character_type not in _support.NON_PLAYER_CHARACTER_TYPES:
                raise ValueError("NPC turn actor must be an NPC or monster")
            if actor_id in normalized_interlocutor_ids:
                raise ValueError("NPC turn actor cannot also be an interlocutor")
            interlocutors: list[dict[str, Any]] = []
            for interlocutor_id in normalized_interlocutor_ids:
                interlocutor = self.characters.get(interlocutor_id)
                if interlocutor.campaign_id != campaign_id:
                    raise ValueError("every NPC interlocutor must belong to the campaign")
                interlocutors.append(
                    {
                        "id": interlocutor.id,
                        "name": interlocutor.name,
                        "character_type": interlocutor.character_type,
                    }
                )
            normalized_stimulus = _support.normalize_npc_stimulus(stimulus)
            stimulus_actor_ids = {
                str(normalized_stimulus.get("speaker_actor_id") or ""),
                *normalized_stimulus.get("target_actor_ids", []),
            } - {""}
            allowed_stimulus_actor_ids = {actor_id, *normalized_interlocutor_ids}
            if unknown_stimulus_actors := sorted(stimulus_actor_ids - allowed_stimulus_actor_ids):
                raise ValueError(
                    "stimulus actor ids must identify the NPC or an interlocutor: "
                    + ", ".join(unknown_stimulus_actors)
                )
            scene = self.npc_turn_scene_projection(result.get("scoped_scene"))
            perception = self.npc_turn_perception_projection(
                self.campaigns.get(campaign_id),
                actor_id=actor_id,
                interlocutors=interlocutors,
                scene=scene,
            )
            actor_state, fact_heads, knowledge_heads = self.npc_turn_actor_state(
                campaign_id,
                branch_id,
                actor_id,
            )
            relationships = [
                item for item in actor_state if item.get("predicate") == "relationship_to"
            ]
            goals = [item for item in actor_state if item.get("predicate") == "goal"]
            memory_query = " ".join(
                item
                for item in (
                    query.strip(),
                    str(normalized_stimulus.get("content") or "").strip(),
                )
                if item
            )
            memory_refs = {
                *resolved_related_refs,
                *(f"event:{item}" for item in normalized_stimulus.get("source_event_ids") or []),
            }
            actor_memory = self.actor_memory_projection(
                campaign_id=campaign_id,
                branch_id=branch_id,
                actor=actor,
                query=memory_query,
                current_refs=memory_refs,
                budget_chars=min(int(budget_chars), 8_000),
                retrieved_events=list(result.get("events") or []),
                audience=audience,
            )
            actor_knowledge = [dict(item["record"]) for item in actor_memory["semantic"]]
            common_context = [
                item
                for item in list(result.get("facts") or [])
                if item.get("disclosure_scope") == "public"
            ]
            relevant_anchor_heads = {
                str(item.id): str(item.revision_id)
                for item in self.memories.list(
                    campaign_id,
                    kind="context_anchor",
                    branch_id=branch_id,
                )
                if resolved_related_refs
                & {str(ref) for ref in list(dict(item.metadata or {}).get("related_refs") or [])}
            }
            context_fact_heads = {
                **relevant_anchor_heads,
                **{str(item["id"]): str(item["revision_id"]) for item in common_context},
            }
            raw_conversation = self.events.list_for_actor(
                campaign_id,
                actor_id=actor_id,
                branch_id=branch_id,
                limit=max(1, min(int(conversation_limit), 30)),
            )
            conversation_events = []
            for event in raw_conversation:
                payload = dict(event.payload or {})
                if event.event_type == "npc_conversation":
                    for transcript_event in list(payload.get("transcript") or []):
                        overall = dict(transcript_event.get("audience_facts") or {})
                        segments = list(transcript_event.get("utterance_segments") or [])
                        segment_facts = list(transcript_event.get("segment_audience_facts") or [])
                        visible_parts: list[str] = []
                        if segments and len(segment_facts) == len(segments):
                            for segment, facts in zip(segments, segment_facts, strict=True):
                                understood = set(facts.get("understood_actor_ids") or [])
                                perceived = set(facts.get("perceived_actor_ids") or [])
                                partial = dict(facts.get("partial_renditions") or {})
                                if actor_id in understood:
                                    visible_parts.append(str(segment.get("text") or ""))
                                elif actor_id in partial:
                                    visible_parts.append(str(partial[actor_id]))
                                elif actor_id in perceived:
                                    visible_parts.append("[Perceived but not understood]")
                        else:
                            understood = set(overall.get("understood_actor_ids") or [])
                            perceived = set(overall.get("perceived_actor_ids") or [])
                            partial = dict(overall.get("partial_renditions") or {})
                            if actor_id in understood:
                                visible_parts.append(str(transcript_event.get("content") or ""))
                            elif actor_id in partial:
                                visible_parts.append(str(partial[actor_id]))
                            elif actor_id in perceived:
                                visible_parts.append("[Perceived but not understood]")
                        visible_text = " ".join(item for item in visible_parts if item).strip()
                        if not visible_text:
                            continue
                        conversation_events.append(
                            {
                                "event_id": str(transcript_event.get("event_id") or event.id),
                                "sequence": int(transcript_event.get("sequence") or event.sequence),
                                "event_type": "npc_conversation_turn",
                                "summary": visible_text,
                                "speaker_actor_id": str(
                                    transcript_event.get("speaker_actor_id") or ""
                                ),
                                "listener_actor_ids": [
                                    str(item.get("actor_id") or "")
                                    for item in event.participants
                                    if str(item.get("actor_id") or "")
                                    != str(transcript_event.get("speaker_actor_id") or "")
                                ],
                                "utterance": visible_text,
                                "language": str(transcript_event.get("language") or ""),
                                "delivery": str(transcript_event.get("delivery") or ""),
                                "visible_action": str(transcript_event.get("visible_action") or ""),
                                "public_speech_acts": [],
                                "visible_portrayal_cues": [
                                    str(item) for item in transcript_event.get("visible_cues") or []
                                ],
                                "participants": [dict(item) for item in event.participants],
                            }
                        )
                    continue
                conversation_events.append(
                    {
                        "event_id": event.id,
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "summary": event.summary,
                        "speaker_actor_id": str(payload.get("speaker_actor_id") or ""),
                        "listener_actor_ids": list(payload.get("listener_actor_ids") or []),
                        "utterance": str(payload.get("utterance") or ""),
                        "language": str(payload.get("language") or ""),
                        "delivery": str(payload.get("delivery") or ""),
                        "visible_action": str(payload.get("visible_action") or ""),
                        "public_speech_acts": [
                            dict(item) for item in list(payload.get("public_speech_acts") or [])
                        ],
                        "visible_portrayal_cues": [
                            str(item) for item in list(payload.get("visible_portrayal_cues") or [])
                        ],
                        "participants": [dict(item) for item in event.participants],
                    }
                )
            conversation_events = conversation_events[-max(1, min(int(conversation_limit), 30)) :]
            conversation = {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "branch_id": branch_id,
                "scope_id": scope_id,
                "scene_id": str((scene or {}).get("scene_id") or ""),
                "cursor": {
                    "latest_event_sequence": max(
                        (int(item["sequence"]) for item in conversation_events),
                        default=0,
                    ),
                    "event_count": len(conversation_events),
                },
                "participants": [
                    {
                        "actor_id": actor_id,
                        "name": actor.name,
                        "role": "speaker",
                    },
                    *(
                        {
                            "actor_id": str(item["id"]),
                            "name": str(item["name"]),
                            "role": "interlocutor",
                        }
                        for item in interlocutors
                    ),
                ],
                "events": conversation_events,
            }
            stimulus_ref = (
                "stimulus:"
                + _support.hashlib.sha256(
                    _support.canonical_json(normalized_stimulus).encode("utf-8")
                ).hexdigest()
            )
            allowed_basis_refs = {
                f"actor:{actor_id}:identity",
                f"actor:{actor_id}:self_state",
                *([stimulus_ref] if normalized_stimulus.get("kind") != "none" else []),
                *(f"knowledge:{item['id']}:{item['revision_id']}" for item in actor_knowledge),
                *(f"fact:{item['id']}:{item['revision_id']}" for item in actor_state),
                *(str(item["basis_ref"]) for item in perception),
                *(f"event:{item['event_id']}" for item in conversation_events),
                *(
                    str(item["basis_ref"])
                    for track in ("identity", "motivational", "semantic", "episodic")
                    for item in actor_memory[track]
                ),
            }
            portrayal_context = [
                {
                    **dict(item),
                    "context_role": "dm_portrayal_context",
                    "disclosure_policy": "not_speakable_without_actor_basis",
                }
                for item in list(result.get("module_evidence") or [])
            ]
            branch = dict(result.get("branch") or {})
            bundle: dict[str, Any] = {
                "schema_version": _support.NPC_TURN_BUNDLE_SCHEMA_VERSION,
                "bundle_id": str(_support.uuid4()),
                "purpose": "npc_turn",
                "authority": {
                    "campaign_id": campaign_id,
                    "branch_id": branch_id,
                    "head_snapshot_id": branch.get("head_snapshot_id"),
                    "campaign_revision": self.campaigns.get(campaign_id).revision,
                    "latest_event_sequence": self.npc_turn_latest_event_sequence(
                        campaign_id, branch_id
                    ),
                    "actor_revision": actor.revision,
                    "scene_state_version": int((scene or {}).get("state_version") or 0),
                    "host_context_binding": self.host_context_binding(
                        campaign_id=campaign_id,
                        branch_id=branch_id,
                        principal_id=principal_id,
                        role=membership.role,
                        audience=audience,
                    ),
                },
                "actor": self.npc_turn_actor_projection(actor),
                "interlocutors": interlocutors,
                "stimulus": {**normalized_stimulus, "basis_ref": stimulus_ref},
                "perception": perception,
                "actor_knowledge": actor_knowledge,
                "actor_memory": actor_memory,
                "common_context": common_context,
                "relationships": relationships,
                "goals": goals,
                "conversation": conversation,
                "scene": scene,
                "portrayal_context": portrayal_context,
                "constraints": {
                    "allowed_basis_refs": sorted(allowed_basis_refs),
                    "allowed_target_actor_ids": sorted({actor_id, *normalized_interlocutor_ids}),
                    "module_evidence_is_actor_knowledge": False,
                    "common_context_is_actor_knowledge": False,
                    "may_roll_dice": False,
                    "may_call_tools": False,
                    "may_write_state": False,
                    "output_contract": "npc-turn-proposal.v1",
                },
                "delegation": {
                    "schema_version": 1,
                    "contract": "sagasmith.delegation.v1",
                    "task": "propose_npc_turn",
                    "execution": "awaited_fresh_context",
                    "inherit_agent_history": False,
                    "tools_exposed": False,
                    "persist_worker_session": False,
                    "authoritative_result": False,
                    "output_contract": "npc-turn-proposal.v1",
                },
                "retrieval": {
                    **dict(result.get("retrieval") or {}),
                    "actor_state_count": len(actor_state),
                    "actor_memory_selected_count": actor_memory["diagnostics"]["selected_count"],
                    "conversation_event_count": len(conversation_events),
                },
            }
            receipt = self.issue_npc_turn_receipt(
                bundle=bundle,
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                actor_id=actor_id,
                actor_revision=actor.revision,
                interlocutor_actor_ids=normalized_interlocutor_ids,
                scene=scene,
                fact_heads=fact_heads,
                context_fact_heads=context_fact_heads,
                knowledge_heads=knowledge_heads,
                allowed_basis_refs=sorted(allowed_basis_refs),
                stimulus=normalized_stimulus,
                module_evidence=portrayal_context,
            )
            bundle["bundle_receipt"] = receipt
            return bundle
        result["host_context_binding"] = self.host_context_binding(
            campaign_id=campaign_id,
            branch_id=branch_id,
            principal_id=principal_id,
            role=membership.role,
            audience=audience,
        )
        result["context_receipt"] = self.issue_context_receipt(
            campaign_id=campaign_id,
            branch_id=branch_id,
            principal_id=principal_id,
            audience=audience,
            actor_id=actor_id,
            scope_id=scope_id,
            related_refs=sorted(resolved_related_refs),
            context=result,
        )
        return result

    def npc_conversation_require_fresh(self, session: dict[str, Any]) -> None:
        if session.get("status") == "stale":
            raise ValueError("SESSION_STALE: close or abort and open a new conversation")
        if session.get("status") not in _support.ACTIVE_CONVERSATION_STATUSES:
            raise ValueError(f"conversation is not active: {session.get('status')}")
        authority = dict(session["authority"])
        reasons = []
        current_branch_id = self.readable_branch(
            str(session["campaign_id"]), str(session["branch_id"]), str(session["principal_id"])
        )
        if current_branch_id != session["branch_id"]:
            reasons.append("branch_id")
        scene = self.npc_turn_scene_projection(
            self.modules.current_scene(
                str(session["campaign_id"]), scope_id=str(session["scope_id"])
            )
        )
        if str((scene or {}).get("scene_id") or "") != str(session.get("scene_id") or ""):
            reasons.append("scene_id")
        elif int((scene or {}).get("state_version") or 0) != int(
            authority.get("scene_state_version") or 0
        ):
            reasons.append("scene_state_version")
        refreshed_actor_ids: list[str] = []
        for actor_id, expected_revision in dict(authority["actor_revisions"]).items():
            try:
                actor = self.characters.get(str(actor_id))
            except LookupError:
                reasons.append(f"actor:{actor_id}:missing")
                continue
            runtime = session["actor_runtimes"].get(str(actor_id))
            if runtime is None:
                continue
            lock = dict(dict(authority.get("actor_context_locks") or {}).get(actor_id) or {})
            current_lock = self.npc_conversation_context_lock(
                {
                    "authority": {
                        "campaign_id": session["campaign_id"],
                        "branch_id": session["branch_id"],
                    },
                    "actor": {"id": actor_id},
                }
            )
            fact_changed = dict(lock.get("fact_heads") or {}) != dict(current_lock["fact_heads"])
            knowledge_changed = dict(lock.get("knowledge_heads") or {}) != dict(
                current_lock["knowledge_heads"]
            )
            if actor.revision != int(expected_revision) or fact_changed or knowledge_changed:
                old_runtime_id = str(runtime["actor_runtime_id"])
                bundle = self.continuity_context(
                    campaign_id=str(session["campaign_id"]),
                    query="",
                    actor_id=str(actor_id),
                    scope_id=str(session["scope_id"]),
                    audience="dm",
                    purpose="npc_turn",
                    interlocutor_actor_ids=[
                        item for item in session["participant_ids"] if item != actor_id
                    ],
                    stimulus=None,
                    branch_id=str(session["branch_id"]),
                    principal_id=str(session["principal_id"]),
                )
                runtime["context"] = self.npc_conversation_private_context(bundle)
                runtime["actor_runtime_id"] = (
                    f"{session['conversation_id']}:{actor_id}:r{actor.revision}:"
                    f"c{int(session['conversation_revision']) + 1}"
                )
                runtime["working_state_revision"] += 1
                replacement_activations = []
                for activation in list(session["activations"].values()):
                    if activation["actor_id"] == actor_id and activation["status"] in {
                        "pending",
                        "claimed",
                    }:
                        replacement = {
                            "activation_id": str(_support.uuid4()),
                            "actor_runtime_id": runtime["actor_runtime_id"],
                            "actor_id": str(actor_id),
                            "reason": str(activation["reason"]),
                            "response_required": bool(activation["response_required"]),
                            "from_cursor": int(activation["from_cursor"]),
                            "to_cursor": int(activation["to_cursor"]),
                            "status": "pending",
                            "lease": None,
                            "replacement_for": str(activation["activation_id"]),
                        }
                        activation["status"] = "invalidated"
                        activation["lease"] = None
                        replacement_activations.append(replacement)
                for replacement in replacement_activations:
                    session["activations"][replacement["activation_id"]] = replacement
                invalidated_activation_ids = {
                    str(activation["activation_id"])
                    for activation in session["activations"].values()
                    if activation["actor_id"] == actor_id
                    and str(activation.get("actor_runtime_id") or "") == old_runtime_id
                }
                for publication in session.get("publications") or []:
                    if (
                        publication.get("status") == "pending_audience"
                        and str(publication.get("activation_id") or "")
                        in invalidated_activation_ids
                    ):
                        publication["status"] = "invalidated"
                for resolution in session.get("pending_resolutions") or []:
                    if (
                        resolution.get("status") == "pending"
                        and str(resolution.get("activation_id") or "") in invalidated_activation_ids
                    ):
                        resolution["status"] = "invalidated"
                for candidate in session.get("memory_candidates") or []:
                    if str(
                        candidate.get("source_activation_id") or ""
                    ) in invalidated_activation_ids and not candidate.get("source_event_id"):
                        candidate["status"] = "invalidated"
                authority["actor_revisions"][actor_id] = actor.revision
                authority.setdefault("actor_context_locks", {})[actor_id] = (
                    self.npc_conversation_context_lock(runtime["context"])
                )
                refreshed_actor_ids.append(str(actor_id))
        if reasons:
            session["status"] = "stale"
            session["stale_reasons"] = reasons
            session["updated_at_ns"] = _support.time.time_ns()
            self.npc_conversations.save(session)
            raise ValueError("SESSION_STALE: authoritative state changed: " + ", ".join(reasons))
        if refreshed_actor_ids:
            session["authority"] = authority
            session["refreshed_actor_ids"] = refreshed_actor_ids
            session["conversation_revision"] = int(session["conversation_revision"]) + 1
            session["updated_at_ns"] = _support.time.time_ns()
            self.npc_conversations.save(session)

    def npc_conversation_private_context(self, bundle: dict[str, Any]) -> dict[str, Any]:
        context = {
            key: _support.deepcopy(value)
            for key, value in bundle.items()
            if key != "bundle_receipt"
        }
        context["purpose"] = "npc_conversation"
        bundle_authority = dict(context["authority"])
        context["authority"] = {
            key: _support.deepcopy(bundle_authority[key])
            for key in ("campaign_id", "branch_id", "actor_revision", "scene_state_version")
        }
        context["constraints"] = {
            **dict(context["constraints"]),
            "utterance_content_modes": [
                "nonfactual",
                "grounded",
                "deception",
                "uncertain",
            ],
            "factual_content_requires_actor_owned_basis_refs": True,
            "output_contract": "npc-conversation-proposal.v5",
        }
        context["delegation"] = {
            "schema_version": 1,
            "contract": "sagasmith.delegation.v1",
            "task": "propose_npc_conversation_turn",
            "execution": "persistent_actor_worker",
            "inherit_agent_history": False,
            "tools_exposed": False,
            "persist_worker_session": True,
            "authoritative_result": False,
            "output_contract": "npc-conversation-proposal.v5",
        }
        actor_id = str(dict(context["actor"])["id"])
        commitments = [
            _support.asdict(item)
            for item in self.memories.list_for_subject_refs(
                str(dict(context["authority"])["campaign_id"]),
                subject_refs={f"actor:{actor_id}"},
                predicates={"commitment"},
                kinds={"actor_state"},
                branch_id=str(dict(context["authority"])["branch_id"]),
            )
        ]
        context["commitments"] = commitments
        return context

    def npc_conversation_context_lock(self, context: dict[str, Any]) -> dict[str, Any]:
        authority = dict(context["authority"])
        actor_id = str(dict(context["actor"])["id"])
        campaign_id = str(authority["campaign_id"])
        branch_id = str(authority["branch_id"])
        actor_facts = [
            _support.asdict(item)
            for item in self.memories.list_for_subject_refs(
                campaign_id,
                subject_refs={f"actor:{actor_id}"},
                predicates={"relationship_to", "goal", "commitment"},
                kinds={"actor_state"},
                branch_id=branch_id,
                include_inactive=True,
            )
        ]
        actor_knowledge = self.knowledge.list(
            campaign_id,
            actor_id=actor_id,
            branch_id=branch_id,
            include_inactive=True,
        )
        return {
            "fact_heads": {str(item["fact_key"]): str(item["revision_id"]) for item in actor_facts},
            "knowledge_heads": {
                str(item.knowledge_key): str(item.revision_id) for item in actor_knowledge
            },
        }

    def npc_conversation_open_impl(
        self,
        campaign_id: str,
        participant_actor_ids: list[str],
        idempotency_key: str,
        scope_id: str = "party",
        query: str = "",
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Open one Play conversation and materialize each NPC's private runtime once."""

        membership = self.access.require_campaign(
            campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
        )
        self.require_facade_phase(campaign_id, "npc_conversation", _support.PROFILE_PLAY)
        branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        if dict(campaign.state.get("chase") or {}).get("active", False):
            raise _support.CombatEngineError(
                "end the active chase before opening an NPC conversation"
            )
        actor_ids = [str(item).strip() for item in participant_actor_ids]
        if not actor_ids or any(not item for item in actor_ids):
            raise ValueError("participant_actor_ids must contain at least one actor")
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("participant_actor_ids must be unique")
        if len(actor_ids) > 20:
            raise ValueError("a conversation supports at most 20 participants")
        actors = []
        for actor_id in actor_ids:
            actor = self.characters.get(actor_id)
            if actor.campaign_id != campaign_id:
                raise ValueError("every conversation participant must belong to the campaign")
            actors.append(actor)
        npc_actors = [
            actor for actor in actors if actor.character_type in _support.NON_PLAYER_CHARACTER_TYPES
        ]
        if not npc_actors:
            raise ValueError(
                "conversation requires at least one NPC or monster campaign runtime id "
                "inside payload.participant_actor_ids"
            )

        actor_contexts: dict[str, dict[str, Any]] = {}
        first_bundle: dict[str, Any] | None = None
        for actor in npc_actors:
            bundle = self.continuity_context(
                campaign_id=campaign_id,
                query=query,
                actor_id=actor.id,
                scope_id=scope_id,
                audience="dm",
                purpose="npc_turn",
                interlocutor_actor_ids=[item for item in actor_ids if item != actor.id],
                stimulus=None,
                branch_id=branch_id,
                principal_id=principal_id,
            )
            first_bundle = first_bundle or bundle
            actor_contexts[actor.id] = self.npc_conversation_private_context(bundle)
        assert first_bundle is not None
        first_authority = dict(first_bundle["authority"])
        scene = dict(first_bundle.get("scene") or {})
        authority = {
            "scene_state_version": int(first_authority.get("scene_state_version") or 0),
            "actor_revisions": {actor.id: actor.revision for actor in actors},
            "actor_context_locks": {
                actor_id: self.npc_conversation_context_lock(context)
                for actor_id, context in actor_contexts.items()
            },
            "principal_fingerprint": dict(first_authority["host_context_binding"])[
                "principal_fingerprint"
            ],
        }
        opened = self.npc_conversations.open(
            campaign_id=campaign_id,
            branch_id=branch_id,
            principal_id=principal_id,
            scope_id=scope_id,
            scene_id=str(scene.get("scene_id") or ""),
            authority=authority,
            participants=[
                {
                    "actor_id": actor.id,
                    "name": actor.name,
                    "kind": actor.character_type,
                    "npc_runtime": actor.id in actor_contexts,
                }
                for actor in actors
            ],
            actor_contexts=actor_contexts,
            idempotency_key=idempotency_key,
        )
        opened["role"] = membership.role
        opened["next_step"] = (
            "Call npc_conversation(action='ingest') with Agent-resolved audience_facts, "
            "then let the authenticated Host dispatch only the returned activation_refs."
        )
        return opened

    def npc_conversation_status_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Read public orchestration state without exposing any NPC private capsule."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        if session.get("status") == "open":
            self.npc_conversation_require_fresh(session)
        result = self.npc_conversations.public_status(session)
        if session.get("status") == "stale":
            result["stale_reasons"] = list(session.get("stale_reasons") or [])
        result["activations"] = self.npc_conversations.list_activations(session)
        result["pending_publications"] = [
            {
                key: _support.deepcopy(value)
                for key, value in item.items()
                if key not in {"speaker_actor_id"}
            }
            for item in session["publications"]
            if item.get("status") == "pending_audience"
        ]
        result["pending_resolutions"] = [
            _support.deepcopy(item)
            for item in session.get("pending_resolutions") or []
            if item.get("status") == "pending"
        ]
        result["memory_candidates"] = self.npc_conversations.memory_candidates(session)
        result["refreshed_actor_ids"] = list(session.get("refreshed_actor_ids") or [])
        return result

    def npc_conversation_list_impl(
        self,
        campaign_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """List this principal's active public conversation recovery handles."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        branch_id = self.require_current_branch(campaign_id, None)
        conversations = self.npc_conversations.active_public_statuses(
            campaign_id=campaign_id,
            branch_id=branch_id,
            principal_id=principal_id,
        )
        return {
            "campaign_id": campaign_id,
            "branch_id": branch_id,
            "count": len(conversations),
            "conversations": conversations,
        }

    def npc_conversation_ingest_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        event: dict[str, Any],
        audience_facts: dict[str, Any],
        expected_conversation_revision: int,
        idempotency_key: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Append one stimulus using explicit Agent-resolved audience facts."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        self.npc_conversation_require_fresh(session)
        raw = dict(event or {})
        allowed_fields = {
            "type",
            "speaker_actor_id",
            "content",
            "language",
            "delivery",
            "declared_target_actor_ids",
            "resolved_resolution_ids",
        }
        if unknown := set(raw) - allowed_fields:
            raise ValueError(f"conversation event has unknown fields: {sorted(unknown)}")
        event_type = str(raw.get("type") or "speech").strip()
        if event_type not in {"speech", "action", "scene_prompt", "resolution"}:
            raise ValueError(
                "conversation event type must be speech, action, scene_prompt, or resolution"
            )
        speaker_actor_id = str(raw.get("speaker_actor_id") or "").strip()
        if speaker_actor_id and speaker_actor_id not in session["participant_ids"]:
            raise ValueError("conversation speaker must be a participant")
        if event_type in {"speech", "action"} and not speaker_actor_id:
            raise ValueError("speech and action events require a participant speaker")
        content = str(raw.get("content") or "").strip()
        if not content or len(content) > 6_000:
            raise ValueError("conversation event content must contain 1 to 6000 characters")
        target_actor_ids = [
            str(item).strip() for item in raw.get("declared_target_actor_ids") or []
        ]
        if len(target_actor_ids) != len(set(target_actor_ids)) or any(
            item not in session["participant_ids"] for item in target_actor_ids
        ):
            raise ValueError("declared conversation targets must be unique participants")
        language = str(raw.get("language") or "").strip()
        delivery = str(raw.get("delivery") or "").strip()
        resolved_resolution_ids = [
            str(item).strip() for item in raw.get("resolved_resolution_ids") or []
        ]
        if len(resolved_resolution_ids) != len(set(resolved_resolution_ids)) or any(
            not item for item in resolved_resolution_ids
        ):
            raise ValueError("resolved_resolution_ids must contain unique non-empty ids")
        if event_type == "resolution" and not resolved_resolution_ids:
            raise ValueError("resolution events must identify at least one resolved resolution")
        if event_type != "resolution" and resolved_resolution_ids:
            raise ValueError("only resolution events may include resolved_resolution_ids")
        audience = _support.normalize_audience_facts(
            audience_facts,
            participant_ids=set(session["participant_ids"]),
            response_actor_ids=set(session["actor_runtimes"]),
        )
        result = self.npc_conversations.append_event(
            session,
            event={
                "type": event_type,
                "speaker_actor_id": speaker_actor_id,
                "content": content,
                "language": language,
                "delivery": delivery,
                "declared_target_actor_ids": target_actor_ids,
                "resolved_resolution_ids": resolved_resolution_ids,
            },
            audience_facts=audience,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
        )
        return result

    def npc_activation_claim_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        activation_ref: str,
        expected_conversation_revision: int,
        idempotency_key: str,
        cursor: int = 0,
        include_bootstrap: bool = True,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Lease one private actor capsule to the matching isolated NPC subagent."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        self.npc_conversation_require_fresh(session)
        return self.npc_conversations.checkout(
            session,
            activation_ref=activation_ref,
            cursor=cursor,
            include_bootstrap=include_bootstrap,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
        )

    def npc_proposal_submit_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        activation_ref: str,
        lease_id: str,
        proposal: dict[str, Any],
        expected_conversation_revision: int,
        idempotency_key: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Validate one NPC proposal and return only a server-derived publication."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        self.npc_conversation_require_fresh(session)
        return self.npc_conversations.submit(
            session,
            activation_ref=activation_ref,
            lease_id=lease_id,
            proposal=proposal,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
        )

    def npc_activation_cancel_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        activation_ref: str,
        lease_id: str,
        expected_conversation_revision: int,
        idempotency_key: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        return self.npc_conversations.cancel_activation(
            session,
            activation_ref=activation_ref,
            lease_id=lease_id,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
        )

    def npc_conversation_publish_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        publication_id: str,
        audience_facts: dict[str, Any],
        segment_audience_facts: list[dict[str, Any]] | None,
        expected_conversation_revision: int,
        idempotency_key: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        self.npc_conversation_require_fresh(session)
        audience = _support.normalize_audience_facts(
            audience_facts,
            participant_ids=set(session["participant_ids"]),
            response_actor_ids=set(session["actor_runtimes"]),
        )
        segment_audience = [
            _support.normalize_audience_facts(
                item,
                participant_ids=set(session["participant_ids"]),
                response_actor_ids=set(session["actor_runtimes"]),
            )
            for item in list(segment_audience_facts or [])
        ]
        return self.npc_conversations.publish(
            session,
            publication_id=publication_id,
            audience_facts=audience,
            segment_audience_facts=segment_audience,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
        )

    def npc_conversation_close_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        expected_conversation_revision: int,
        accepted_candidate_ids: list[str] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically commit the exact public transcript and accepted long-term deltas."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for npc_conversation(close)")
        self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        session, replay = self.npc_conversations.begin_mutation(
            conversation_id,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
            operation="close",
            payload={"accepted_candidate_ids": accepted_candidate_ids or []},
        )
        if replay is not None:
            return replay
        self.npc_conversation_require_fresh(session)
        if session.get("status") != "open":
            raise ValueError("conversation must be open before closing")
        if any(
            item.get("status") in {"pending", "claimed"} for item in session["activations"].values()
        ):
            raise ValueError("conversation has unfinished NPC activations")
        if any(
            item.get("status") == "pending_audience" for item in session.get("publications") or []
        ):
            raise ValueError("conversation has unpublished NPC output")
        if any(
            item.get("status") == "pending" for item in session.get("pending_resolutions") or []
        ):
            raise ValueError("conversation has unresolved mechanic requests")
        candidate_ids = list(accepted_candidate_ids or [])
        if any(not isinstance(item, str) or not item for item in candidate_ids) or len(
            candidate_ids
        ) != len(set(candidate_ids)):
            raise ValueError("accepted_candidate_ids must be a unique non-empty string list")
        candidates = {
            str(item["candidate_id"]): item
            for item in self.npc_conversations.memory_candidates(session)
        }
        if unknown := sorted(set(candidate_ids) - set(candidates)):
            raise ValueError(f"accepted_candidate_ids contains unavailable candidates: {unknown}")
        facts_data: list[dict[str, Any]] = []
        knowledge_data: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            candidate = candidates[candidate_id]
            if candidate["status"] != "available":
                raise ValueError(f"memory candidate is not available: {candidate_id}")
            actor_id = str(candidate["actor_id"])
            value = _support.deepcopy(candidate["value"])
            if candidate["kind"] == "fact":
                facts_data.append(value)
            elif candidate["kind"] == "actor_knowledge":
                knowledge_data.append(value)
            elif candidate["kind"] == "commitment":
                commitment = value
                commitment_key = str(commitment["commitment_key"])
                facts_data.append(
                    {
                        "action": "upsert",
                        "fact_key": f"actor:{actor_id}:commitment:{commitment_key}",
                        "content": str(commitment["content"]),
                        "kind": "actor_state",
                        "subject": actor_id,
                        "subject_ref": f"actor:{actor_id}",
                        "predicate": "commitment",
                        "metadata": dict(commitment.get("metadata") or {}),
                        "importance": int(commitment.get("importance", 3)),
                        "disclosure_scope": "dm",
                    }
                )
            else:
                raise ValueError(f"unsupported memory candidate kind: {candidate['kind']}")
        participants = {str(item) for item in session["participant_ids"]}
        current_facts = {
            item.fact_key: item
            for item in self.memories.list(
                campaign_id, branch_id=session["branch_id"], include_inactive=True
            )
        }
        for index, fact in enumerate(facts_data):
            actor_ref = str(fact.get("subject_ref") or "")
            if actor_ref.removeprefix("actor:") not in participants:
                raise ValueError(f"accepted fact[{index}] belongs outside the conversation")
            if str(fact.get("kind") or "") != "actor_state" or str(
                fact.get("predicate") or ""
            ) not in {"relationship_to", "goal", "commitment"}:
                raise ValueError("accepted conversation facts must be actor-state continuity")
            fact["disclosure_scope"] = "dm"
            current = current_facts.get(str(fact.get("fact_key") or ""))
            if current is not None and str(fact.get("action") or "upsert") == "upsert":
                fact.setdefault("expected_revision_id", current.revision_id)
        for index, item in enumerate(knowledge_data):
            if str(item.get("actor_id") or "") not in participants:
                raise ValueError(
                    f"accepted ActorKnowledge[{index}] belongs outside the conversation"
                )
            item["disclosure_scope"] = str(item.get("disclosure_scope") or "dm")

        transcript = [
            {
                key: _support.deepcopy(event.get(key))
                for key in (
                    "event_id",
                    "sequence",
                    "type",
                    "speaker_actor_id",
                    "content",
                    "language",
                    "delivery",
                    "declared_target_actor_ids",
                    "publication_id",
                    "utterance_segments",
                    "visible_cues",
                    "visible_action",
                    "resolved_resolution_ids",
                    "audience_facts",
                    "segment_audience_facts",
                )
                if key in event
            }
            for event in session["events"]
        ]
        names = {str(item["actor_id"]): str(item["name"]) for item in session["participants"]}
        retrieval_lines = []
        for event in transcript[-12:]:
            content = str(event.get("content") or "").strip()
            if content:
                retrieval_lines.append(
                    f"{names.get(str(event.get('speaker_actor_id')), 'Scene')}: {content[:300]}"
                )
        event_summary = (
            f"Conversation among {', '.join(names.values())}; "
            f"{len(transcript)} public events and {len(session['publications'])} NPC publications."
        )
        commit = self.continuity_commit(
            campaign_id,
            {
                "branch_id": session["branch_id"],
                "event": {
                    "event_type": "npc_conversation",
                    "summary": event_summary,
                    "retrieval_text": "\n".join(retrieval_lines),
                    # Only the exact public transcript enters this actor-visible
                    # event. Private authority and DM-only commitment details
                    # remain in their authoritative stores.
                    "audience_scope": "actor",
                    "participants": [
                        {"actor_id": actor_id, "role": "witness"}
                        for actor_id in session["participant_ids"]
                    ],
                    "payload": {
                        "schema_version": 2,
                        "conversation_id": conversation_id,
                        "scene_id": session["scene_id"],
                        "scope_id": session["scope_id"],
                        "transcript": transcript,
                        "unresolved_resolution_requests": _support.deepcopy(
                            [
                                item
                                for item in session.get("pending_resolutions") or []
                                if item.get("status") == "pending"
                            ]
                        ),
                    },
                },
                "facts": facts_data,
                "actor_knowledge": knowledge_data,
            },
            principal_id,
            self.campaigns.get(campaign_id).revision,
            idempotency_key,
        )
        session["status"] = "closed"
        for runtime in session["actor_runtimes"].values():
            runtime["status"] = "closed"
            runtime["context"] = {}
        return self.npc_conversations.finish_mutation(session, commit)

    def npc_conversation_abort_impl(
        self,
        campaign_id: str,
        conversation_id: str,
        expected_conversation_revision: int,
        idempotency_key: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Discard an uncommitted conversation draft and every private actor capsule."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        session = self.npc_conversations.require_owner(
            conversation_id, campaign_id=campaign_id, principal_id=principal_id
        )
        if session.get("status") == "closed":
            raise ValueError("a committed conversation cannot be aborted")
        session, replay = self.npc_conversations.begin_mutation(
            conversation_id,
            expected_revision=expected_conversation_revision,
            idempotency_key=idempotency_key,
            operation="abort",
            payload={},
        )
        if replay is not None:
            return replay
        session["status"] = "aborted"
        for runtime in session["actor_runtimes"].values():
            runtime["context"] = {}
        for candidate in session.get("memory_candidates") or []:
            candidate["status"] = "invalidated"
        return self.npc_conversations.finish_mutation(
            session,
            {"conversation_id": conversation_id, "status": "aborted", "recoverable": False},
        )

    def npc_conversation(
        self,
        campaign_id: str,
        action: Literal["open", "list", "get", "ingest", "publish", "close", "abort"],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Run the complete public NPC conversation workflow through one facade.

        For ``action='ingest'``, put the public stimulus in ``payload.event``
        with ``type`` (speech, action, scene_prompt, or resolution),
        ``speaker_actor_id``, and ``content`` (not ``text``); optional fields
        include ``language``, ``delivery``, and ``declared_target_actor_ids``.
        Include ``payload.audience_facts`` with ``decision_id``,
        ``resolver='agent'``, perceived/understood/response actor id lists,
        ``partial_renditions``, ``basis_refs``, and a scene-specific ``reason``.
        Understood and response actors must be perceived, and response actors
        must have NPC runtimes.
        All writes require payload.idempotency_key. ingest/publish/close/abort
        also require payload.conversation_id and expected_conversation_revision
        from the latest conversation receipt (not the campaign revision).
        open uses participant_actor_ids and optional scope_id inside payload.
        Pending NPC activations require a connected Host worker; close only when
        they finish. Without that Host, report the missing capability or explicitly
        abort the conversation, never fabricate NPC publications or busy-poll it.
        """

        data = dict(payload or {})
        if action == "open":
            if not isinstance(data.get("participant_actor_ids"), list):
                raise ValueError(
                    "npc_conversation open requires payload.participant_actor_ids: "
                    "put every PC and NPC campaign runtime id in that one array; "
                    "there are no npc_actor_ids or npc_ids fields"
                )
            if not str(data.get("idempotency_key") or "").strip():
                raise ValueError(
                    "Field payload.idempotency_key is required for npc_conversation(open); "
                    "retry with a stable non-empty business idempotency key."
                )
            return self.npc_conversation_open_impl(
                campaign_id=campaign_id,
                participant_actor_ids=list(data["participant_actor_ids"]),
                idempotency_key=str(data["idempotency_key"]),
                scope_id=str(data.get("scope_id") or "party"),
                query=str(data.get("query") or ""),
                branch_id=data.get("branch_id"),
                principal_id=principal_id,
            )
        if action == "list":
            listing = self.npc_conversation_list_impl(campaign_id, principal_id)
            conversations, page = _support._bounded_page(
                list(listing.get("conversations") or []),
                scope=f"npc_conversation:list:{campaign_id}:{principal_id}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            return {
                **listing,
                "count": len(conversations),
                "conversations": conversations,
                "page": page,
                "next_cursor": page["next_cursor"],
            }
        required_fields = {"conversation_id"}
        if action in {"ingest", "publish", "close", "abort"}:
            required_fields.update({"expected_conversation_revision", "idempotency_key"})
        if action == "ingest":
            required_fields.update({"event", "audience_facts"})
        elif action == "publish":
            required_fields.update({"publication_id", "audience_facts"})
        missing = sorted(key for key in required_fields if key not in data or data[key] is None)
        if missing:
            raise ValueError(
                f"npc_conversation({action}) requires "
                + ", ".join(f"payload.{key}" for key in missing)
            )
        conversation_id = str(data["conversation_id"])
        if action == "get":
            return self.npc_conversation_status_impl(campaign_id, conversation_id, principal_id)
        if action == "ingest":
            return self.npc_conversation_ingest_impl(
                campaign_id,
                conversation_id,
                dict(data["event"]),
                dict(data["audience_facts"]),
                int(data["expected_conversation_revision"]),
                str(data["idempotency_key"]),
                principal_id,
            )
        if action == "publish":
            return self.npc_conversation_publish_impl(
                campaign_id,
                conversation_id,
                str(data["publication_id"]),
                dict(data["audience_facts"]),
                [dict(item) for item in data.get("segment_audience_facts") or []],
                int(data["expected_conversation_revision"]),
                str(data["idempotency_key"]),
                principal_id,
            )
        if action == "close":
            allowed = {
                "conversation_id",
                "expected_conversation_revision",
                "accepted_candidate_ids",
                "idempotency_key",
            }
            if unknown := sorted(set(data) - allowed):
                raise ValueError(f"npc_conversation close has unknown fields: {unknown}")
            accepted_candidate_ids = data.get("accepted_candidate_ids") or []
            if not isinstance(accepted_candidate_ids, list):
                raise ValueError("accepted_candidate_ids must be a list")
            return self.npc_conversation_close_impl(
                campaign_id,
                conversation_id,
                int(data["expected_conversation_revision"]),
                accepted_candidate_ids,
                principal_id,
                str(data["idempotency_key"]),
            )
        if action == "abort":
            return self.npc_conversation_abort_impl(
                campaign_id,
                conversation_id,
                int(data["expected_conversation_revision"]),
                str(data["idempotency_key"]),
                principal_id,
            )
        raise ValueError(f"unsupported npc_conversation action: {action}")

    def npc_conversation_transport(
        self,
        campaign_id: str,
        conversation_id: str,
        action: Literal["claim_activation", "submit_proposal", "cancel_activation"],
        payload: dict[str, Any],
        host_token: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Host-private activation transport; never exposed to a model tool profile."""

        if not self.config.npc_host_token or not _support.secrets.compare_digest(
            host_token, self.config.npc_host_token
        ):
            raise PermissionError("NPC host transport authentication failed")
        data = dict(payload or {})
        common = {
            "campaign_id": campaign_id,
            "conversation_id": conversation_id,
            "activation_ref": str(data["activation_ref"]),
            "expected_conversation_revision": int(data["expected_conversation_revision"]),
            "idempotency_key": str(data["idempotency_key"]),
            "principal_id": principal_id,
        }
        if action == "claim_activation":
            return self.npc_activation_claim_impl(
                **common,
                cursor=int(data.get("cursor") or 0),
                include_bootstrap=bool(data.get("include_bootstrap", True)),
            )
        if action == "submit_proposal":
            return self.npc_proposal_submit_impl(
                **common,
                lease_id=str(data["lease_id"]),
                proposal=dict(data["proposal"]),
            )
        if action == "cancel_activation":
            return self.npc_activation_cancel_impl(
                **common,
                lease_id=str(data["lease_id"]),
            )
        raise ValueError(f"unsupported NPC host transport action: {action}")

    def continuity_diagnostics(
        self,
        campaign_id: str,
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Inspect continuity health without returning secret narrative content."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        branch_id = self.readable_branch(campaign_id, branch_id, principal_id)
        result = self.continuity.diagnostics(campaign_id, branch_id=branch_id)
        current_manifest = self.catalog.manifest()
        latest_manifest = None
        for item in reversed(self.events.list(campaign_id, limit=500, branch_id=branch_id)):
            candidate = item.payload.get("_sagasmith_skill_manifest")
            if isinstance(candidate, list):
                latest_manifest = candidate
                break
        result["skill_manifest"] = {
            "current": current_manifest,
            "latest_event": latest_manifest,
            "drift": (latest_manifest != current_manifest if latest_manifest is not None else None),
        }
        latest_slot = result["snapshots"]["latest_slot"]
        recap = self.snapshots.get(campaign_id, latest_slot).get("recap") if latest_slot else None
        provenance = dict(recap.get("provenance") or {}) if isinstance(recap, dict) else {}
        result["recap"] = {
            "schema_version": recap.get("schema_version") if isinstance(recap, dict) else None,
            "source": recap.get("source") if isinstance(recap, dict) else None,
            "has_presentation": bool(
                isinstance(recap, dict) and recap.get("presentation") is not None
            ),
            "evidence_event_count": len(provenance.get("evidence_event_ids") or []),
        }
        return result

    def continuity_commit(
        self,
        campaign_id: str,
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically save a scene event, fact changes, actor knowledge, and snapshot."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for continuity commits")
        data = self.facade_payload(payload)
        branch_id = self.require_current_branch(campaign_id, data.get("branch_id"))
        raw_event = self.required(data, "event")
        raw_facts = data.get("facts") or []
        raw_knowledge = data.get("actor_knowledge") or []
        if not isinstance(raw_event, dict):
            raise ValueError("payload.event must be an object")
        if not isinstance(raw_facts, list) or not all(isinstance(item, dict) for item in raw_facts):
            raise ValueError("payload.facts must be a list of objects")
        if not isinstance(raw_knowledge, list) or not all(
            isinstance(item, dict) for item in raw_knowledge
        ):
            raise ValueError("payload.actor_knowledge must be a list of objects")
        event_data = dict(raw_event)
        facts_data = [dict(item) for item in raw_facts]
        knowledge_data = [dict(item) for item in raw_knowledge]
        context_receipt = data.get("context_receipt")
        npc_turn_data = data.get("npc_turn")
        npc_turn_receipt: dict[str, Any] | None = None
        npc_turn_trace: dict[str, Any] | None = None
        if npc_turn_data is not None:
            if not isinstance(npc_turn_data, dict):
                raise ValueError("payload.npc_turn must be an object")
            unknown_npc_fields = set(npc_turn_data) - {
                "bundle_receipt",
                "proposal",
                "accepted_fact_indexes",
                "accepted_actor_knowledge_indexes",
                "accepted_action",
                "isolation_level",
            }
            if unknown_npc_fields:
                raise ValueError(
                    f"payload.npc_turn has unknown fields: {sorted(unknown_npc_fields)}"
                )
            if facts_data or knowledge_data:
                raise ValueError(
                    "NPC turn facts and ActorKnowledge must come from accepted proposal indexes"
                )
            proposal = _support.normalize_npc_turn_proposal(npc_turn_data.get("proposal"))
            npc_turn_receipt = self.verify_npc_turn_receipt(
                npc_turn_data.get("bundle_receipt"),
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                require_fresh=False,
            )
            if proposal["bundle_id"] != npc_turn_receipt.get("bundle_id"):
                raise ValueError("NPC proposal belongs to another context bundle")
            if proposal["speaker_actor_id"] != npc_turn_receipt.get("actor_id"):
                raise ValueError("NPC proposal speaker does not match its context bundle")
            _support.validate_npc_basis_refs(
                proposal,
                allowed_basis_refs={
                    str(item) for item in npc_turn_receipt.get("allowed_basis_refs") or []
                },
            )
            _support.validate_npc_targets(
                proposal,
                allowed_actor_ids={
                    str(npc_turn_receipt["actor_id"]),
                    *(str(item) for item in npc_turn_receipt.get("interlocutor_actor_ids") or []),
                },
            )
            if proposal["resolution_requests"]:
                raise ValueError(
                    "NPC proposal requires resolution; use public rules tools and read a new bundle"
                )
            fact_indexes = npc_turn_data.get("accepted_fact_indexes") or []
            knowledge_indexes = npc_turn_data.get("accepted_actor_knowledge_indexes") or []
            if not isinstance(fact_indexes, list) or not isinstance(knowledge_indexes, list):
                raise ValueError("accepted NPC proposal indexes must be lists")
            facts_data, knowledge_data = _support.accepted_proposal_deltas(
                proposal,
                fact_indexes=fact_indexes,
                actor_knowledge_indexes=knowledge_indexes,
            )
            speaker_actor_id = str(npc_turn_receipt["actor_id"])
            listener_actor_ids = [
                str(item) for item in npc_turn_receipt.get("interlocutor_actor_ids") or []
            ]
            allowed_delta_actor_ids = {speaker_actor_id, *listener_actor_ids}
            allowed_fact_fields = {
                "action",
                "fact_key",
                "memory_id",
                "content",
                "kind",
                "subject",
                "metadata",
                "subject_ref",
                "predicate",
                "status",
                "valid_from",
                "valid_to",
                "source_event_ids",
                "importance",
                "disclosure_scope",
                "expected_revision_id",
            }
            for index, fact in enumerate(facts_data):
                if unknown := set(fact) - allowed_fact_fields:
                    raise ValueError(
                        f"accepted NPC fact[{index}] has unknown fields: {sorted(unknown)}"
                    )
                if str(fact.get("kind") or "") != "actor_state":
                    raise ValueError("accepted NPC facts must use kind='actor_state'")
                if str(fact.get("subject_ref") or "") != f"actor:{speaker_actor_id}":
                    raise ValueError("accepted NPC facts must belong to the speaking actor")
                if str(fact.get("predicate") or "") not in {"relationship_to", "goal"}:
                    raise ValueError("accepted NPC facts may update only relationships or goals")
                if str(fact.get("disclosure_scope") or "dm") != "dm":
                    raise ValueError("accepted NPC actor-state facts must remain DM-only")
                fact["disclosure_scope"] = "dm"
            allowed_knowledge_fields = {
                "action",
                "actor_id",
                "knowledge_key",
                "knowledge_id",
                "proposition",
                "subject_ref",
                "epistemic_status",
                "confidence",
                "cause",
                "disclosure_scope",
                "expected_revision_id",
            }
            for index, item in enumerate(knowledge_data):
                if unknown := set(item) - allowed_knowledge_fields:
                    raise ValueError(
                        "accepted NPC ActorKnowledge"
                        f"[{index}] has unknown fields: {sorted(unknown)}"
                    )
                if str(item.get("actor_id") or "") not in allowed_delta_actor_ids:
                    raise ValueError(
                        "accepted NPC ActorKnowledge may target only the speaker or listeners"
                    )
            raw_accepted_action = npc_turn_data.get("accepted_action", False)
            if not isinstance(raw_accepted_action, bool):
                raise ValueError("payload.npc_turn.accepted_action must be boolean")
            accepted_action = raw_accepted_action
            action_kind = str(proposal["proposed_action"]["kind"])
            if accepted_action and action_kind not in _support.NPC_NARRATIVE_ACTION_KINDS:
                raise ValueError(
                    "mechanical NPC actions require public engine tools before continuity commit"
                )
            action_target_ref = str(proposal["proposed_action"].get("target_ref") or "")
            if accepted_action and action_target_ref:
                allowed_action_targets = {
                    f"actor:{actor_id_value}" for actor_id_value in allowed_delta_actor_ids
                }
                if action_target_ref not in allowed_action_targets:
                    raise ValueError(
                        "accepted narrative NPC action target must be the speaker or listener"
                    )
            isolation_level = str(npc_turn_data.get("isolation_level") or "logical")
            if isolation_level not in {"isolated", "logical"}:
                raise ValueError("NPC turn isolation_level must be isolated or logical")
            audience_scope = str(event_data.get("audience_scope") or "actor")
            if audience_scope not in {"actor", "party", "public"}:
                raise ValueError("NPC dialogue audience_scope must be actor, party, or public")
            summary = str(event_data.get("summary") or "").strip()
            if not summary:
                raise ValueError("payload.event.summary is required")
            proposal_digest = _support.hashlib.sha256(
                _support.canonical_json(proposal).encode("utf-8")
            ).hexdigest()
            visible_action = ""
            if accepted_action and action_kind != "none":
                visible_action = str(proposal["proposed_action"].get("summary") or action_kind)
            npc_turn_trace = {
                "bundle_digest": str(npc_turn_receipt["bundle_digest"]),
                "proposal_digest": proposal_digest,
                "isolation_level": isolation_level,
            }
            event_data = {
                "event_type": "npc_dialogue_turn",
                "summary": summary,
                "audience_scope": audience_scope,
                "participants": [
                    {"actor_id": speaker_actor_id, "role": "speaker"},
                    *(
                        {"actor_id": listener_id, "role": "listener"}
                        for listener_id in listener_actor_ids
                    ),
                ],
                "payload": {
                    "schema_version": _support.NPC_TURN_SCHEMA_VERSION,
                    "speaker_actor_id": speaker_actor_id,
                    "listener_actor_ids": listener_actor_ids,
                    "scene_id": str(npc_turn_receipt.get("scene_id") or ""),
                    "language": str(proposal["utterance"].get("language") or ""),
                    "utterance": str(proposal["utterance"].get("text") or ""),
                    "delivery": str(proposal["utterance"].get("delivery") or ""),
                    "visible_action": visible_action,
                    "public_speech_acts": [
                        {
                            "kind": str(item.get("kind") or ""),
                            "content": str(item.get("content") or ""),
                            "targets": [str(target) for target in item.get("targets") or []],
                        }
                        for item in proposal["speech_acts"]
                    ],
                    "visible_portrayal_cues": [
                        str(item) for item in proposal["portrayal"].get("visible_cues") or []
                    ],
                    "turn_trace": npc_turn_trace,
                },
            }
            context_receipt = npc_turn_data.get("bundle_receipt")
        snapshot_data = dict(data["snapshot"]) if data.get("snapshot") is not None else None
        self.validate_embedded_module_source_refs(
            campaign_id,
            event_data.get("payload") or {},
            field="payload.event.payload",
        )
        self.validate_embedded_module_source_refs(
            campaign_id,
            facts_data,
            field="payload.facts",
        )
        self.validate_embedded_module_source_refs(
            campaign_id,
            knowledge_data,
            field="payload.actor_knowledge",
        )
        if (
            event_data.get("audience_scope") == "actor"
            and not knowledge_data
            and not event_data.get("participants")
        ):
            raise ValueError(
                "actor-scoped continuity events require ActorKnowledge or event participants"
            )
        manifest = self.catalog.manifest()
        for fact in facts_data:
            _support.validate_subject_context_fact(
                kind=fact.get("kind"),
                subject_ref=fact.get("subject_ref"),
            )
        event_payload = dict(event_data.get("payload") or {})
        event_payload["_sagasmith_skill_manifest"] = manifest
        event_data["payload"] = event_payload
        # A response-lost retry normally carries the current post-commit
        # revision. Keep concurrency tokens out of the logical request hash so
        # the original atomic response can be replayed without duplicating it.
        request_payload = {
            "event": event_data,
            "facts": facts_data,
            "actor_knowledge": knowledge_data,
            "snapshot": snapshot_data,
            "branch_id": branch_id,
            "skill_manifest": manifest,
            "npc_turn_trace": npc_turn_trace,
        }
        scope = f"continuity-commit:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        embedded_source_digests = self.managed_module_source_digests(
            [event_data.get("payload") or {}, facts_data, knowledge_data]
        )
        required_source_digests = embedded_source_digests & self.anchored_source_digests(
            campaign_id,
            branch_id,
        )
        if npc_turn_receipt is not None:
            npc_turn_receipt = self.verify_npc_turn_receipt(
                context_receipt,
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                required_source_digests=required_source_digests,
            )
        elif required_source_digests:
            self.verify_context_receipt(
                context_receipt,
                campaign_id=campaign_id,
                branch_id=branch_id,
                principal_id=principal_id,
                required_source_digests=required_source_digests,
            )
        current_facts = {
            item.fact_key: item
            for item in self.memories.list(
                campaign_id,
                branch_id=branch_id,
                include_inactive=True,
            )
        }
        for index, fact in enumerate(facts_data):
            action = str(fact.get("action", "upsert"))
            if action == "upsert" and current_facts.get(str(fact.get("fact_key", ""))):
                if fact.get("expected_revision_id") is None:
                    raise ValueError(
                        f"payload.facts[{index}].expected_revision_id is required "
                        "when upsert revises a fact"
                    )
            if action == "revise" and fact.get("expected_revision_id") is None:
                raise ValueError(
                    f"payload.facts[{index}].expected_revision_id is required for revisions"
                )
            if "valid_from" in fact:
                fact["valid_from"] = self.optional_datetime(
                    fact.get("valid_from"), f"facts[{index}].valid_from"
                )
            if "valid_to" in fact:
                fact["valid_to"] = self.optional_datetime(
                    fact.get("valid_to"), f"facts[{index}].valid_to"
                )
        for index, item in enumerate(knowledge_data):
            if (
                str(item.get("action", "add")) == "revise"
                and item.get("expected_revision_id") is None
            ):
                raise ValueError(
                    "payload.actor_knowledge"
                    f"[{index}].expected_revision_id is required for revisions"
                )
        self.validate_continuity_knowledge_source_audiences(
            campaign_id,
            branch_id,
            event_data,
            knowledge_data,
        )
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        response = self.continuity_commits.commit(
            campaign_id,
            event=event_data,
            facts=facts_data,
            actor_knowledge=knowledge_data,
            snapshot=snapshot_data,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=lambda result: {
                    **result,
                    "skill_manifest": manifest,
                },
            ),
        )
        response["skill_manifest"] = manifest
        return response

    def memory_query(
        self,
        campaign_id: str,
        view: Literal["list", "search", "diagnostics"] = "list",
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read objective campaign memory; actor knowledge remains a separate subjective store."""
        if view == "diagnostics":
            self.require_facade_phase(
                campaign_id,
                "memory_query(diagnostics)",
                _support.PROFILE_LOBBY,
                _support.PROFILE_PLAY,
            )
            data = self.facade_payload(payload)
            return self.facade_result(
                view,
                self.continuity_diagnostics(
                    campaign_id,
                    data.get("branch_id"),
                    principal_id,
                ),
            )
        data = self.facade_payload(payload)
        include_inactive = self.facade_bool(data, "include_inactive")
        effective_query = query or str(data.get("query") or "")
        page_limit = _support._page_limit(data.get("limit", limit))
        page_scope = (
            f"memory_query:{campaign_id}:{view}:{principal_id}:"
            f"{str(data.get('branch_id') or '')}:{include_inactive}:"
            f"{_support.json_sha256(effective_query)}"
        )
        if view == "search":
            fingerprint, page_offset = _support._cursor_offset(
                scope=page_scope,
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            result = self.memory_search(
                campaign_id,
                self.required(data, "query") if not query else query,
                page_limit + 1,
                data.get("branch_id"),
                principal_id,
                include_inactive,
                page_offset,
            )
            result, page = _support._authority_page(
                result,
                fingerprint=fingerprint,
                offset=page_offset,
                limit=page_limit,
            )
        else:
            result = self.memory_list(
                campaign_id,
                data.get("kind"),
                data.get("branch_id"),
                principal_id,
                include_inactive,
            )
            result, page = _support._bounded_page(
                result,
                scope=page_scope,
                query=effective_query,
                limit=page_limit,
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
        return self.facade_result(view, result, page=page)

    def memory_change(
        self,
        campaign_id: str,
        action: Literal[
            "add", "upsert", "revise", "supersede", "retract", "forget", "commit"
        ] = "add",
        payload: dict[str, Any] | None = None,
        content: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add, upsert, supersede, retract, or forget an objective fact.

        Writes preserve immutable history. ``expected_revision`` is the campaign
        revision guard; revision UUIDs belong in ``payload.expected_revision_id``.
        Commit payloads use the same event/ActorKnowledge audience boundary as
        ``campaign_event``: a DM-only event cannot back player-visible
        owner/party/player/public knowledge, including NPC conversation closes.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for memory writes")
        if action == "commit":
            self.require_facade_phase(
                campaign_id,
                "memory_change(commit)",
                _support.PROFILE_LOBBY,
                _support.PROFILE_PLAY,
            )
            commit_payload = self.facade_payload(payload)
            if branch_id is not None and commit_payload.get("branch_id") not in {None, branch_id}:
                raise ValueError("payload.branch_id conflicts with top-level branch_id")
            if branch_id is not None:
                commit_payload["branch_id"] = branch_id
            return self.facade_result(
                action,
                self.continuity_commit(
                    campaign_id,
                    commit_payload,
                    principal_id,
                    expected_revision,
                    idempotency_key,
                ),
            )
        data = {
            "content": content,
            "kind": kind,
            "subject": subject,
            "metadata": metadata,
            "branch_id": branch_id,
        }
        data.update(self.facade_payload(payload))
        self.validate_embedded_module_source_refs(
            campaign_id,
            data,
            field=f"memory_change({action})",
        )
        resolved_branch_id = self.require_current_branch(campaign_id, data.get("branch_id"))
        if action in {"add", "upsert"}:
            _support.validate_subject_context_fact(
                kind=data.get("kind") or "fact",
                subject_ref=data.get("subject_ref") or "",
            )
        request_payload = {
            "action": action,
            **data,
            "branch_id": resolved_branch_id,
            # The campaign CAS token is part of the idempotency fingerprint.
            # Reusing a business key with a different base revision must be a
            # conflict, never a replay of the earlier mutation.
            "expected_revision": expected_revision,
        }
        scope = f"memory-change:{action}:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return self.facade_result(action, replay)

        visible = self.memories.list(
            campaign_id,
            branch_id=resolved_branch_id,
            include_inactive=True,
        )
        by_id = {item.id: item for item in visible}
        by_key = {item.fact_key: item for item in visible}
        atomic_write = _support.IdempotencyWrite(
            scope=scope,
            payload=request_payload,
            response=lambda item: _support.asdict(item),
        )
        if action == "add":
            result = self.memories.add(
                campaign_id,
                content=str(self.required(data, "content")),
                kind=str(data.get("kind") or "fact"),
                subject=str(data.get("subject") or ""),
                metadata=dict(data.get("metadata") or {}),
                branch_id=resolved_branch_id,
                fact_key=data.get("fact_key"),
                subject_ref=str(data.get("subject_ref") or ""),
                predicate=str(data.get("predicate") or ""),
                status=str(data.get("status") or "active"),
                valid_from=self.optional_datetime(data.get("valid_from"), "valid_from"),
                valid_to=self.optional_datetime(data.get("valid_to"), "valid_to"),
                source_event_ids=list(data.get("source_event_ids") or []),
                importance=int(data.get("importance", 3)),
                disclosure_scope=data.get("disclosure_scope"),
                expected_campaign_revision=expected_revision,
                idempotency_key=idempotency_key,
                idempotency_write=atomic_write,
            )
        elif action == "upsert":
            fact_key = str(self.required(data, "fact_key"))
            current = by_key.get(fact_key)
            expected_revision_id = data.get("expected_revision_id")
            if current is not None and expected_revision_id is None:
                raise ValueError("expected_revision_id is required when upsert revises a fact")
            result = self.memories.upsert(
                campaign_id,
                fact_key=fact_key,
                content=str(self.required(data, "content")),
                kind=(str(data["kind"]) if data.get("kind") is not None else None),
                subject=(str(data["subject"]) if data.get("subject") is not None else None),
                subject_ref=(
                    str(data["subject_ref"]) if data.get("subject_ref") is not None else None
                ),
                predicate=(str(data["predicate"]) if data.get("predicate") is not None else None),
                metadata=(
                    None
                    if current is not None and data.get("metadata") is None
                    else dict(data.get("metadata") or {})
                ),
                branch_id=resolved_branch_id,
                expected_revision_id=expected_revision_id,
                status=str(data.get("status") or (current.status if current else "active")),
                valid_from=self.optional_datetime(data.get("valid_from"), "valid_from"),
                valid_to=self.optional_datetime(data.get("valid_to"), "valid_to"),
                source_event_ids=(
                    list(data["source_event_ids"])
                    if data.get("source_event_ids") is not None
                    else None
                ),
                importance=(
                    int(data["importance"])
                    if data.get("importance") is not None
                    else current.importance
                    if current
                    else 3
                ),
                disclosure_scope=data.get("disclosure_scope"),
                expected_campaign_revision=expected_revision,
                idempotency_key=idempotency_key,
                idempotency_write=atomic_write,
            )
        else:
            memory_id = str(self.required(data, "memory_id"))
            current = by_id.get(memory_id)
            if current is None:
                raise LookupError(memory_id)
            expected_revision_id = str(self.required(data, "expected_revision_id"))
            result = self.memories.revise(
                memory_id,
                content=(
                    current.content
                    if action in {"supersede", "retract", "forget"} and not data.get("content")
                    else str(self.required(data, "content"))
                ),
                metadata=(dict(data["metadata"]) if data.get("metadata") is not None else None),
                branch_id=resolved_branch_id,
                expected_revision_id=expected_revision_id,
                status=(
                    "superseded"
                    if action == "supersede"
                    else "retracted"
                    if action == "retract"
                    else "forgotten"
                    if action == "forget"
                    else data.get("status")
                ),
                valid_from=self.optional_datetime(data.get("valid_from"), "valid_from"),
                valid_to=self.optional_datetime(data.get("valid_to"), "valid_to"),
                source_event_ids=(
                    list(data["source_event_ids"])
                    if data.get("source_event_ids") is not None
                    else None
                ),
                importance=(
                    int(data["importance"]) if data.get("importance") is not None else None
                ),
                disclosure_scope=data.get("disclosure_scope"),
                expected_campaign_revision=expected_revision,
                idempotency_key=idempotency_key,
                idempotency_write=atomic_write,
            )
        return self.facade_result(action, _support.asdict(result))
