"""Campaigns application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.game_time import TICKS_PER_DAY, TICKS_PER_MINUTE
from sagasmith_dnd.survival import merge_daily_intake, validate_daily_intake
from sagasmith_dnd.travel import (
    forced_march_save_dcs,
    settle_forced_march_save,
    travel_duration_minutes,
    validate_travel_leg,
)

from .. import application_support as _support
from ..result_contracts import affected_state_slice
from .grapples import reconcile_grapples
from .mounted_combat import needs_mounted_rider_save, reconcile_mounted_conditions
from .movement_continuations import reconcile_movement
from .survival import needs_survival_save, reconcile_survival_days


class CampaignsService:
    def assert_snapshot_core_available(self, document: dict[str, Any]) -> None:
        """Fail before materialization when a save's exact built-in core is unavailable."""
        profile = dict(document.get("payload", {}).get("rule_profile") or {})
        options = dict(profile.get("options") or {})
        locked = dict(options.get("_core_rule_pack_lock") or {})
        if not locked:
            raise _support.RulesetUnavailableError(
                "snapshot has no locked built-in core rule pack; "
                "it cannot be restored without explicit conversion"
            )
        edition = str(profile.get("edition") or "")
        try:
            core_pack = _support.get_core_rule_pack(edition)
        except (KeyError, ValueError) as error:
            raise _support.RulesetUnavailableError(
                f"snapshot requires unsupported D&D edition {edition!r}"
            ) from error
        available = {
            "id": core_pack.id,
            "version": core_pack.version,
            "fingerprint": core_pack.fingerprint,
        }
        if locked != available:
            raise _support.RulesetUnavailableError(
                "snapshot's locked built-in core rule pack is unavailable; "
                "runtime upgrade needs an explicit conversion before restore"
            )
        try:
            _support.require_compatible_build(dict(options.get("_implementation_identity") or {}))
        except ValueError as error:
            raise _support.RulesetUnavailableError(str(error)) from error

    def campaign_random_context(
        self,
        campaign_id: str,
        tool_id: str,
        arguments: dict[str, Any],
    ):
        campaign = self.campaigns.get(campaign_id)
        stream = _support.CampaignRandomStream.from_campaign_state(
            campaign_id,
            campaign.state,
            operation=tool_id,
            idempotency_key=str(arguments.get("idempotency_key") or ""),
            campaign_revision=campaign.revision,
        )
        return _support.use_random_stream(stream)

    def campaign_advancement_mode(self, campaign: Any) -> str:
        advancement = dict((campaign.settings or {}).get("advancement") or {})
        try:
            return self.normalized_advancement_mode(advancement.get("mode"))
        except ValueError as error:
            raise _support.CombatEngineError(
                "campaign advancement mode is not configured; "
                "use campaign_change(action='advancement_configure') in lobby"
            ) from error

    def current_branch_id(self, campaign_id: str) -> str | None:
        current = self.branches.current(campaign_id)
        return current.id if current is not None else None

    def require_current_branch(self, campaign_id: str, branch_id: str | None) -> str | None:
        current = self.current_branch_id(campaign_id)
        if branch_id is not None and current is not None and branch_id != current:
            raise ValueError("branch_id must match the campaign's checked-out branch")
        return current if branch_id is None else branch_id

    def readable_branch(
        self, campaign_id: str, branch_id: str | None, principal_id: str
    ) -> str | None:
        """Players can read only the checked-out timeline; DM roles may inspect alternatives."""
        current = self.current_branch_id(campaign_id)
        if not self.is_dm(campaign_id, principal_id) and branch_id not in {None, current}:
            raise PermissionError("players may only inspect the checked-out branch")
        return current if branch_id is None else branch_id

    def commit_campaign_state(
        self,
        campaign: Any,
        campaign_state: dict[str, Any] | None,
        *,
        operation: str,
        principal_id: str,
        branch_id: str,
        idempotency_key: str,
        scope: str,
        payload: dict[str, Any],
        response_fields: dict[str, Any],
        character_updates: list[_support.CharacterStateUpdate] | None = None,
        actor_knowledge_transfers: list[_support.ActorKnowledgeTransfer] | None = None,
        rule_receipts: list[dict[str, Any]] | None = None,
        include_campaign_revision: bool = True,
        include_revisions: bool = True,
        expected_campaign_revision: int | None = None,
    ) -> dict[str, Any]:
        """Commit one public state result and its exact retry response atomically."""

        needs_mounted_save = needs_mounted_rider_save(self, campaign_state, character_updates)
        needs_water_save = needs_survival_save(self, campaign, campaign_state, character_updates)
        if _support.active_random_stream() is None and (needs_mounted_save or needs_water_save):
            with self.campaign_random_context(
                campaign.id,
                f"{operation}.rule_settlement",
                {"idempotency_key": idempotency_key},
            ):
                return self.commit_campaign_state(
                    campaign,
                    campaign_state,
                    operation=operation,
                    principal_id=principal_id,
                    branch_id=branch_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    response_fields=response_fields,
                    character_updates=character_updates,
                    actor_knowledge_transfers=actor_knowledge_transfers,
                    rule_receipts=rule_receipts,
                    include_campaign_revision=include_campaign_revision,
                    include_revisions=include_revisions,
                    expected_campaign_revision=expected_campaign_revision,
                )

        survival_rule_receipts: list[dict[str, Any]] = []
        if campaign_state is not None:
            (
                campaign_state,
                character_updates,
                response_fields,
                survival_rule_receipts,
            ) = reconcile_survival_days(
                self,
                campaign,
                campaign_state,
                character_updates,
                response_fields,
                branch_id,
            )
            rule_receipts = [*list(rule_receipts or []), *survival_rule_receipts]

        from .saving_throws import finalize

        campaign_state, character_updates, response_fields, rule_receipts = finalize(
            self,
            campaign,
            campaign_state,
            character_updates,
            response_fields,
            rule_receipts,
        )

        campaign_state, character_updates, response_fields = self.reconcile_steel_defender_deaths(
            campaign,
            campaign_state,
            character_updates,
            response_fields,
            branch_id=branch_id,
        )
        (
            campaign_state,
            character_updates,
            response_fields,
        ) = self.reconcile_actor_effect_dependencies(
            campaign,
            campaign_state,
            character_updates,
            response_fields,
        )
        campaign_state, character_updates, response_fields = self.reconcile_unconscious_inventory(
            campaign, campaign_state, character_updates, response_fields
        )
        campaign_state, character_updates, response_fields = reconcile_grapples(
            self, campaign_state, character_updates, response_fields
        )
        before_mounted = campaign_state
        campaign_state, character_updates, response_fields = reconcile_mounted_conditions(
            self, campaign, campaign_state, character_updates, response_fields
        )
        if campaign_state is not before_mounted:
            rule_receipts = [
                *list(rule_receipts or []),
                *list(response_fields.get("mounted_rule_receipts") or []),
            ]
        before_movement = campaign_state
        campaign_state, character_updates, response_fields = reconcile_movement(
            self, campaign, campaign_state, character_updates, response_fields
        )
        if campaign_state is not before_movement:
            (
                campaign_state,
                character_updates,
                response_fields,
            ) = self.reconcile_actor_effect_dependencies(
                campaign, campaign_state, character_updates, response_fields
            )
            rule_receipts = [
                *list(rule_receipts or []),
                *list(response_fields.get("movement_rule_receipts") or []),
            ]
        self.validate_inventory_custody_update(campaign, campaign_state, character_updates)
        if "narrative_followup" not in response_fields:
            followup = self.narrative_followup_for_mutation(
                campaign,
                branch_id=branch_id,
                campaign_state=campaign_state,
                character_updates=character_updates,
            )
            if followup is not None:
                response_fields = {
                    **response_fields,
                    "narrative_followup": followup,
                }
        stream_before_commit = _support.active_random_stream()
        persists_campaign = campaign_state is not None or (
            stream_before_commit is not None
            and stream_before_commit.campaign_id == campaign.id
            and stream_before_commit.has_unpersisted_draws
        )

        def response_for(revisions: list[Any]) -> dict[str, Any]:
            stream = _support.active_random_stream()
            response = dict(response_fields)
            if self.config.local_authority and self.is_dm(campaign.id, principal_id):
                # These are the exact documents being committed, not a later readback.
                response["affected_state"] = affected_state_slice(
                    campaign,
                    branch_id,
                    character_updates or [],
                    campaign.revision + (1 if persists_campaign else 0),
                )
            if include_campaign_revision:
                response["campaign_revision"] = campaign.revision + (1 if persists_campaign else 0)
            if include_revisions:
                response["revisions"] = [_support.asdict(item) for item in revisions]
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign.id,
            campaign_state=(
                _support.validate_party_state(campaign_state)
                if campaign_state is not None
                else None
            ),
            character_updates=character_updates,
            actor_knowledge_transfers=actor_knowledge_transfers,
            expected_campaign_revision=(
                campaign.revision
                if expected_campaign_revision is None
                else expected_campaign_revision
            ),
            operation=operation,
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response_for,
            ),
            rule_receipts=rule_receipts,
        )
        return response_for(list(revisions_result or []))

    def storage_status(self) -> dict[str, Any]:
        """Return storage health without exposing credentials or host paths."""
        return self.storage.status()

    def storage_migrate(self) -> dict[str, Any]:
        """Run the embedded SQLite schema migrations."""
        self.storage.migrate()
        return {"status": "ok", "database": self.storage.status()["database"]}

    def campaign_create(
        self,
        name: str,
        description: str = "",
        edition: str = _support.DEFAULT_CAMPAIGN_EDITION,
        locale: str = "en",
        advancement_mode: Literal["milestone", "xp"] = "milestone",
        random_seed: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a D&D 5e campaign inside the MCP-owned SQLite database."""
        if not idempotency_key:
            raise ValueError("idempotency_key is required for campaign creation")
        try:
            edition = _support.normalize_dnd_edition(edition)
        except ValueError as exc:
            raise ValueError(f"unsupported D&D core edition: {edition!r}") from exc
        if random_seed is not None and (not random_seed or len(random_seed) > 512):
            raise ValueError("random_seed must contain between 1 and 512 characters")
        normalized_mode = self.normalized_advancement_mode(advancement_mode)
        # Reject before persistence so an unsupported edition cannot leave a
        # partially initialized campaign without its required Core lock.
        _support.get_core_rule_pack(edition)
        created = self.campaigns.create_owned(
            system_id=_support.DND5E.id,
            name=name,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            description=description,
            settings={
                "advancement": {"mode": normalized_mode},
            },
            state=_support.validate_party_state(
                {
                    "random_stream": _support.initial_random_stream(
                        random_seed
                        or (
                            f"sagasmith-dnd:{principal_id}:{idempotency_key}:"
                            f"{name}:{edition}:{locale}"
                        )
                    )
                }
            ),
            rule_profile={
                "edition": edition,
                "locale": locale,
                "publications": [],
                "options": self.profile_options_with_core_lock(edition),
            },
        )
        return _support.asdict(created)

    def campaign_list(
        self,
        status: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """List D&D 5e campaigns."""
        allowed = self.access.accessible_campaign_ids(principal_id)
        return [
            self.campaign_audience_view(item.id, principal_id)
            for item in self.campaigns.list(system_id=_support.DND5E.id, status=status)
            if item.id in allowed
        ]

    def campaign_audience_view(self, campaign_id: str, principal_id: str) -> dict[str, Any]:
        """Project campaign state through the same audience boundary as domain reads."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        campaign = self.campaigns.get(campaign_id)
        value = _support.asdict(campaign)
        from .saving_throws import STATE_KEY, public_choice

        pending = value["state"].pop(STATE_KEY, None)
        value["state"].pop("bardic_inspiration_grants", None)
        value["state"].pop("_rage_activations", None)
        if pending:
            field = (
                "pending_roll"
                if pending["decisions"][-1].get("kind") == "bardic_inspiration"
                else "pending_hit"
                if pending["decisions"][-1].get("kind") == "divine_smite"
                else "pending_save"
            )
            value[field] = public_choice(
                self,
                campaign_id,
                principal_id,
                pending["decisions"][-1],
            )
        value["effective_game_phase"] = _support.campaign_phase(campaign.state)
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            return value
        state = dict(value.get("state") or {})
        safe_state: dict[str, Any] = {
            "game_phase": str(state.get("game_phase") or _support.PROFILE_LOBBY),
            "game_time": _support.deepcopy(dict(state.get("game_time") or {})),
            "party": _support.deepcopy(dict(state.get("party") or {})),
            "world_time": _support.deepcopy(dict(state.get("world_time") or {})),
            "world_effects": [
                _support.deepcopy(effect)
                for effect in state.get("world_effects", [])
                if str(effect.get("visibility") or "party")
                in _support.PLAYER_GAMEPLAY_VISIBILITY_SCOPES
            ],
        }
        combat_state = dict(state.get("combat") or {})
        if bool(combat_state.get("active", False)):
            combat = self.combat_view(campaign_id, principal_id)
            if combat is not None:
                safe_state["combat"] = combat
        elif combat_state:
            # A finished encounter remains useful to the DM as an audit snapshot,
            # but its participants, source refs, initiative, and map are not part
            # of the player's general campaign/resume projection.
            safe_state["combat"] = {"active": False}
        value["state"] = safe_state
        value["state_redacted"] = True
        return value

    def campaign_get(
        self, campaign_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, Any]:
        """Read one campaign, including its persisted party and combat state."""
        return self.campaign_audience_view(campaign_id, principal_id)

    def campaign_member_grant(
        self,
        campaign_id: str,
        principal_id: str,
        role: str = "player",
        by_principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Grant DM/player/observer campaign access; caller role is resolved server-side."""
        caller = by_principal_id or _support.LOCAL_SYSTEM_PRINCIPAL_ID
        self.access.require_campaign(campaign_id, caller, roles=_support.CAMPAIGN_DM_ROLES)
        if role == "owner":
            raise _support.AccessDeniedError(
                "campaign owners can only be created with campaign_create"
            )
        self.access.ensure_principal(principal_id, platform="mcp", external_id=principal_id)
        return _support.asdict(self.access.grant_campaign(campaign_id, principal_id, role=role))

    def campaign_member_revoke(
        self,
        campaign_id: str,
        principal_id: str,
        by_principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Revoke non-owner membership and its actor grants at one authority boundary."""

        caller = by_principal_id or _support.LOCAL_SYSTEM_PRINCIPAL_ID
        caller_membership = self.access.require_campaign(
            campaign_id, caller, roles=_support.CAMPAIGN_DM_ROLES
        )
        target = self.access.membership(campaign_id, principal_id)
        if target is not None and target.role == "owner":
            raise _support.AccessDeniedError("campaign owners cannot be revoked")
        if target is not None and target.role == "dm" and caller_membership.role != "owner":
            raise _support.AccessDeniedError("only a campaign owner can revoke a DM")
        return _support.asdict(self.access.revoke_campaign(campaign_id, principal_id))

    def campaign_update(
        self,
        campaign_id: str,
        name: str | None = None,
        status: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply a reviewed campaign-level update without bypassing its state document."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required for campaign updates"
            )
        branch_id = self.require_current_branch(campaign_id, None)
        payload = {
            "name": name,
            "status": status,
            "description": description,
            "settings": settings,
            "state": state,
            "branch_id": branch_id,
        }
        scope = f"campaign-update:{campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        before = self.campaigns.get(campaign_id)
        normalized_settings = (
            _support.merge_reviewed_campaign_settings(
                before.settings,
                settings,
            )
            if settings is not None
            else None
        )
        normalized_state = None
        if state is not None:
            normalized_state = _support.validate_party_state(
                _support.merge_reviewed_campaign_state(
                    before.state,
                    state,
                )
            )
        after = self.campaigns.update_audited(
            campaign_id,
            name=name,
            status=status,
            description=description,
            settings=normalized_settings,
            state=normalized_state,
            expected_revision=expected_revision,
            operation="campaign.update",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            request_hash=_support.request_hash(payload),
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: _support.asdict(result),
            ),
        )
        return _support.asdict(after)

    def campaign_advancement_configure(
        self,
        campaign_id: str,
        mode: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select milestone or cumulative-XP advancement for one campaign."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        normalized_mode = self.normalized_advancement_mode(mode)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        before = self.campaigns.get(campaign_id)
        phase = self.authoritative_phase(campaign_id)
        if phase == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("end active combat before changing advancement mode")
        if phase != _support.PROFILE_LOBBY:
            raise _support.CombatEngineError("switch to lobby before changing advancement mode")
        payload = {
            "mode": normalized_mode,
            "expected_revision": expected_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-advancement:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        settings = _support.deepcopy(dict(before.settings or {}))
        settings["advancement"] = {"mode": normalized_mode}
        after = self.campaigns.update_audited(
            campaign_id,
            settings=settings,
            expected_revision=expected_revision,
            operation="campaign.advancement.configure",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            request_hash=_support.request_hash(payload),
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "status": "committed",
                    "advancement": {"mode": normalized_mode},
                    "campaign": _support.asdict(result),
                },
            ),
        )
        response = {
            "status": "committed",
            "advancement": {"mode": normalized_mode},
            "campaign": _support.asdict(after),
        }
        return response

    def campaign_experience_award(
        self,
        campaign_id: str,
        awards: list[dict[str, Any]],
        reason: str,
        source_ref: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically award source-bound cumulative XP to one or more PCs."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        if self.campaign_advancement_mode(campaign) != "xp":
            raise _support.CombatEngineError(
                "experience can be awarded only in xp advancement mode"
            )
        state = dict(campaign.state or {})
        if isinstance(state.get("combat"), dict) and state["combat"].get("active", False):
            raise _support.CombatEngineError("end active combat before awarding experience")
        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise ValueError("reason and source_ref are required for audited experience awards")
        if len(normalized_reason) > 1000:
            raise ValueError("experience award reason must not exceed 1000 characters")
        normalized_source_ref = self.advancement_source_ref(
            campaign_id,
            source_ref,
            branch_id=resolved_branch_id,
        )
        if not isinstance(awards, list) or not awards:
            raise ValueError("awards must be a non-empty array")

        normalized_awards: list[dict[str, Any]] = []
        character_ids: set[str] = set()
        for index, award in enumerate(awards):
            if not isinstance(award, dict):
                raise ValueError(f"awards[{index}] must be an object")
            unexpected = set(award) - {"character_id", "amount", "expected_revision"}
            if unexpected:
                raise ValueError(f"awards[{index}] has unexpected fields: {sorted(unexpected)}")
            character_id = str(self.required(award, "character_id")).strip()
            if not character_id or character_id in character_ids:
                raise ValueError("experience awards require unique non-empty character ids")
            amount = self.required(award, "amount")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("experience award amount must be a positive integer")
            character_revision = self.required(award, "expected_revision")
            if (
                isinstance(character_revision, bool)
                or not isinstance(character_revision, int)
                or character_revision < 0
            ):
                raise ValueError(
                    "experience award expected_revision must be a non-negative integer"
                )
            character_ids.add(character_id)
            normalized_awards.append(
                {
                    "character_id": character_id,
                    "amount": amount,
                    "expected_revision": character_revision,
                }
            )

        payload = {
            "awards": normalized_awards,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "expected_revision": expected_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-xp:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay

        updates: list[_support.CharacterStateUpdate] = []
        results: list[dict[str, Any]] = []
        recipient_after_views: dict[str, dict[str, Any]] = {}
        for award in normalized_awards:
            current = self.characters.get(award["character_id"])
            if current.campaign_id != campaign_id:
                raise ValueError("every experience recipient must belong to the campaign")
            if current.character_type != "pc":
                raise ValueError("experience can be awarded only to player characters")
            applied = _support.award_experience(current.sheet, amount=award["amount"])
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=current.id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(
                        current.notes, character_type=current.character_type
                    ),
                    expected_revision=award["expected_revision"],
                )
            )
            recipient_after_views[current.id] = self.character_view(
                _support.replace(
                    current,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    revision=current.revision + 1,
                )
            )
            results.append(
                {
                    "character_id": current.id,
                    "amount": applied["amount"],
                    "old_xp": applied["old_xp"],
                    "new_xp": applied["new_xp"],
                    "advancement": applied["advancement"],
                }
            )

        next_state = _support.deepcopy(state)
        advancement_state = dict(next_state.get("advancement") or {})
        history = list(advancement_state.get("xp_awards") or [])
        award_id = str(_support.uuid4())
        history.append(
            {
                "id": award_id,
                "reason": normalized_reason,
                "source_ref": normalized_source_ref,
                "awards": _support.deepcopy(results),
            }
        )
        advancement_state["xp_awards"] = history
        next_state["advancement"] = advancement_state
        rules = self.effective_rule_context(
            campaign_id,
            facts={
                "award_id": award_id,
                "recipient_count": len(results),
                "xp_total": sum(item["amount"] for item in results),
                "source_ref": normalized_source_ref,
            },
        )
        receipts = _support.core_receipts(
            rules,
            ["dnd5e.core.progression.experience"],
            "campaign.experience.award",
        )
        normalized_next_state = _support.validate_party_state(next_state)
        response = {
            "status": "committed",
            "award_id": award_id,
            "mode": "xp",
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "awards": [
                {
                    **item,
                    "character": recipient_after_views[item["character_id"]],
                }
                for item in results
            ],
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_next_state,
                    revision=campaign.revision + 1,
                )
            ),
            "rule_receipts": receipts,
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=normalized_next_state,
            character_updates=updates,
            expected_campaign_revision=expected_revision,
            operation="campaign.experience.award",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=response,
            ),
            rule_receipts=receipts,
        )
        return response

    def campaign_loot_acquire(
        self,
        campaign_id: str,
        acquisition_id: str,
        coins: dict[str, Any],
        items: list[dict[str, Any]],
        reason: str,
        source_ref: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically add one source-bound loot parcel to the shared party inventory."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        state = dict(campaign.state or {})
        phase = self.authoritative_phase(campaign_id)
        if phase == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("end active combat before acquiring loot")
        if phase != _support.PROFILE_PLAY:
            raise _support.CombatEngineError("source-bound loot can be acquired only in play")

        normalized_acquisition_id = str(acquisition_id).strip()
        normalized_reason = str(reason).strip()
        if not normalized_acquisition_id or len(normalized_acquisition_id) > 200:
            raise ValueError("acquisition_id must contain 1 to 200 characters")
        if not normalized_reason or len(normalized_reason) > 1000:
            raise ValueError("reason must contain 1 to 1000 characters")
        normalized_source_ref, _, _ = self.managed_module_source_ref(
            campaign_id,
            source_ref,
        )

        if not isinstance(coins, dict):
            raise ValueError("coins must be an object")
        unexpected_denominations = sorted(set(coins) - set(_support.DENOMINATIONS))
        if unexpected_denominations:
            raise ValueError(f"coins has unsupported denominations: {unexpected_denominations}")
        normalized_coins: dict[str, int] = {}
        for denomination, amount in coins.items():
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("loot coin amounts must be positive integers")
            normalized_coins[str(denomination)] = amount
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("items must be a list of objects")
        if not normalized_coins and not items:
            raise ValueError("loot acquisition requires coins or items")

        # The idempotency identity describes the logical acquisition. A retry
        # after a lost response is expected to observe the post-commit revision.
        request_payload = {
            "acquisition_id": normalized_acquisition_id,
            "coins": normalized_coins,
            "items": _support.deepcopy(items),
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-loot:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        acquisitions = list(state.get("loot_acquisitions") or [])
        if any(
            str(dict(item).get("id") or "") == normalized_acquisition_id
            for item in acquisitions
            if isinstance(item, dict)
        ):
            raise ValueError("loot acquisition_id already exists on this branch")

        sheet = self.party_sheet(state)
        for denomination, amount in normalized_coins.items():
            sheet = _support.adjust_wallet(sheet, denomination, amount)
        item_ids: list[str] = []
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            sheet, item_id = _support.add_inventory_item(sheet, item)
            item_ids.append(item_id)
            normalized_items.append(
                _support.deepcopy(
                    next(entry for entry in sheet["inventory"]["items"] if entry["id"] == item_id)
                )
            )

        acquisitions.append(
            {
                "id": normalized_acquisition_id,
                "reason": normalized_reason,
                "source_ref": normalized_source_ref,
                "coins": _support.deepcopy(normalized_coins),
                "items": _support.deepcopy(normalized_items),
            }
        )
        next_state = self.party_state(state, sheet)
        next_state["loot_acquisitions"] = acquisitions
        normalized_next_state = _support.validate_party_state(next_state)
        response = {
            "status": "committed",
            "acquisition_id": normalized_acquisition_id,
            "coins": normalized_coins,
            "items": normalized_items,
            "item_ids": item_ids,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "party": self.party_view_from_state(normalized_next_state),
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
            expected_campaign_revision=expected_revision,
            operation="campaign.loot.acquire",
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

    def campaign_currency_spend(
        self,
        campaign_id: str,
        spend_id: str,
        coins: dict[str, Any],
        reason: str,
        source_ref: str,
        rule_ref: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically pay one source-bound expense from the shared party wallet."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        state = dict(campaign.state or {})
        phase = self.authoritative_phase(campaign_id)
        if phase == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("end active combat before spending party currency")
        if phase != _support.PROFILE_PLAY:
            raise _support.CombatEngineError("source-bound currency can be spent only in play")

        normalized_spend_id = str(spend_id).strip()
        normalized_reason = str(reason).strip()
        normalized_rule_ref = str(rule_ref).strip()
        if not normalized_spend_id or len(normalized_spend_id) > 200:
            raise ValueError("spend_id must contain 1 to 200 characters")
        if not normalized_reason or len(normalized_reason) > 1000:
            raise ValueError("reason must contain 1 to 1000 characters")
        if not normalized_rule_ref or len(normalized_rule_ref) > 2048:
            raise ValueError("rule_ref must contain 1 to 2048 characters")
        normalized_source_ref, _, _ = self.managed_module_source_ref(
            campaign_id,
            source_ref,
        )

        if not isinstance(coins, dict):
            raise ValueError("coins must be an object")
        unexpected_denominations = sorted(set(coins) - set(_support.DENOMINATIONS))
        if unexpected_denominations:
            raise ValueError(f"coins has unsupported denominations: {unexpected_denominations}")
        normalized_coins: dict[str, int] = {}
        for denomination, amount in coins.items():
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("currency spend amounts must be positive integers")
            normalized_coins[str(denomination)] = amount
        if not normalized_coins:
            raise ValueError("currency spend requires at least one coin denomination")

        # Optimistic revisions guard the first attempt but are not part of the
        # logical idempotency identity; a recovered retry sees newer revisions.
        request_payload = {
            "spend_id": normalized_spend_id,
            "coins": normalized_coins,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "rule_ref": normalized_rule_ref,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-currency-spend:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        spends = list(state.get("currency_spends") or [])
        if any(
            str(dict(item).get("id") or "") == normalized_spend_id
            for item in spends
            if isinstance(item, dict)
        ):
            raise ValueError("currency spend_id already exists on this branch")

        sheet = self.party_sheet(state)
        for denomination, amount in normalized_coins.items():
            sheet = _support.adjust_wallet(sheet, denomination, -amount)
        spends.append(
            {
                "id": normalized_spend_id,
                "reason": normalized_reason,
                "source_ref": normalized_source_ref,
                "rule_ref": normalized_rule_ref,
                "coins": _support.deepcopy(normalized_coins),
            }
        )
        next_state = self.party_state(state, sheet)
        next_state["currency_spends"] = spends
        normalized_next_state = _support.validate_party_state(next_state)
        response = {
            "status": "committed",
            "spend_id": normalized_spend_id,
            "coins": normalized_coins,
            "reason": normalized_reason,
            "source_ref": normalized_source_ref,
            "rule_ref": normalized_rule_ref,
            "party": self.party_view_from_state(normalized_next_state),
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
            expected_campaign_revision=expected_revision,
            operation="campaign.currency.spend",
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

    def campaign_consumable_use(
        self,
        campaign_id: str,
        use_id: str,
        item_id: str,
        target_character_id: str,
        expected_character_revision: int,
        reason: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically consume one shared healing potion and settle its rolled healing."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_use_id = str(use_id).strip()
        normalized_item_id = str(item_id).strip()
        normalized_target_id = str(target_character_id).strip()
        normalized_reason = str(reason).strip()
        if not normalized_use_id or len(normalized_use_id) > 200:
            raise ValueError("use_id must contain 1 to 200 characters")
        if not normalized_item_id or not normalized_target_id:
            raise ValueError("item_id and target_character_id are required")
        if not normalized_reason or len(normalized_reason) > 1000:
            raise ValueError("reason must contain 1 to 1000 characters")
        if (
            isinstance(expected_character_revision, bool)
            or not isinstance(expected_character_revision, int)
            or expected_character_revision < 0
        ):
            raise ValueError("expected_character_revision must be a non-negative integer")

        request_payload = {
            "use_id": normalized_use_id,
            "item_id": normalized_item_id,
            "target_character_id": normalized_target_id,
            "reason": normalized_reason,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-consumable:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        campaign = self.campaigns.get(campaign_id)
        state = dict(campaign.state or {})
        phase = self.authoritative_phase(campaign_id)
        if phase == _support.PROFILE_COMBAT:
            raise _support.CombatEngineError("use combat actions for consumables during combat")
        if phase != _support.PROFILE_PLAY:
            raise _support.CombatEngineError("shared consumables can be used only in play")
        uses = list(state.get("consumable_uses") or [])
        if any(
            str(dict(item).get("id") or "") == normalized_use_id
            for item in uses
            if isinstance(item, dict)
        ):
            raise ValueError("consumable use_id already exists on this branch")

        target = self.require_campaign_actor(campaign_id, normalized_target_id)
        self.require_character_control(target, principal_id)
        if target.revision != expected_character_revision:
            raise ValueError(f"character revision conflict: {normalized_target_id}")
        shared = self.party_sheet(state)
        item = next(
            (
                entry
                for entry in shared["inventory"]["items"]
                if str(entry["id"]) == normalized_item_id
            ),
            None,
        )
        if item is None:
            raise LookupError(normalized_item_id)
        edition = self.campaign_rules_edition(campaign.id)
        expression = _support.healing_potion_formula(item, edition=edition)
        healing_roll = _support.asdict(_support.roll(expression))
        self.require_healing_not_prevented(
            dict(campaign.state or {}).get("combat"),
            target_id=normalized_target_id,
        )
        healed = _support.apply_healing_to_sheet(target.sheet, amount=int(healing_roll["total"]))
        shared, removed = _support.remove_inventory_item(shared, normalized_item_id, 1)
        healing_result = {key: value for key, value in healed.items() if key != "sheet"}
        uses.append(
            {
                "id": normalized_use_id,
                "item": _support.deepcopy(removed),
                "target_character_id": normalized_target_id,
                "reason": normalized_reason,
                "formula": expression,
                "roll": _support.deepcopy(healing_roll),
                "healing": _support.deepcopy(healing_result),
            }
        )
        next_state = self.party_state(state, shared)
        next_state["consumable_uses"] = uses
        rules = self.effective_rule_context(
            campaign_id,
            facts={
                "use_id": normalized_use_id,
                "item_id": normalized_item_id,
                "target_character_id": normalized_target_id,
                "formula": expression,
            },
        )
        receipts = _support.core_receipts(
            rules,
            [_support.HEALING_POTION_MECHANIC_ID],
            "campaign.consumable.healing_potion",
        )
        normalized_next_state = _support.validate_party_state(next_state)
        normalized_target_sheet = _support.validate_character_sheet(healed["sheet"])
        response = {
            "status": "committed",
            "use_id": normalized_use_id,
            "item": removed,
            "target_character_id": normalized_target_id,
            "reason": normalized_reason,
            "formula": expression,
            "roll": healing_roll,
            "healing": healing_result,
            "party": self.party_view_from_state(normalized_next_state),
            "character": self.character_view(
                _support.replace(
                    target,
                    sheet=normalized_target_sheet,
                    revision=target.revision + 1,
                )
            ),
            "campaign": _support.asdict(
                _support.replace(
                    campaign,
                    state=normalized_next_state,
                    revision=campaign.revision + 1,
                )
            ),
            "rule_receipts": receipts,
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=normalized_next_state,
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target.id,
                    sheet=normalized_target_sheet,
                    notes=_support.validate_character_notes(
                        target.notes, character_type=target.character_type
                    ),
                    expected_revision=expected_character_revision,
                )
            ],
            expected_campaign_revision=expected_revision,
            operation="campaign.consumable.healing_potion",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
            rule_receipts=receipts,
        )
        return response

    def campaign_world_effect_change(
        self,
        campaign_id: str,
        action: str,
        payload: dict[str, Any],
        principal_id: str,
        expected_revision: int | None,
        branch_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Add or dismiss one structured campaign-space effect outside combat."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if action not in {"effect_add", "effect_remove"}:
            raise ValueError("world effect action must be effect_add or effect_remove")
        request_payload = {
            "action": action,
            "payload": payload,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-world-effect:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        if bool(dict(state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("world effects cannot be edited during active combat")
        effects = list(state.get("world_effects") or [])
        if action == "effect_add":
            raw_effect = _support.deepcopy(self.required(payload, "effect"))
            elapsed_ticks = int(state["game_time"]["elapsed_ticks"])
            raw_effect.setdefault("created_at_elapsed_ticks", elapsed_ticks)
            effect = _support.validate_world_effect(raw_effect)
            if any(item["id"] == effect["id"] for item in effects):
                raise ValueError("world effect id is already present")
            effects.append(effect)
        else:
            effect_id = str(self.required(payload, "effect_id"))
            effect = next((item for item in effects if item["id"] == effect_id), None)
            if effect is None:
                raise ValueError("world effect is not present")
            if not effect.get("active"):
                raise ValueError("world effect is already inactive")
            effect["active"] = False
            effect["metadata"] = {
                **dict(effect.get("metadata") or {}),
                "ended_by": principal_id,
                "ended_reason": str(payload.get("reason") or "dismissed"),
            }
        state["world_effects"] = effects

        def world_effect_response(revisions: list[Any]) -> dict[str, Any]:
            return {
                "status": "committed",
                "effect": effect,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=state,
            expected_campaign_revision=campaign.revision,
            operation=f"campaign.world_effect.{action.removeprefix('effect_')}",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=world_effect_response,
            ),
        )
        return world_effect_response(list(revisions_result or []))

    def campaign_clock_set(
        self,
        campaign_id: str,
        day: int,
        hour: int = 0,
        minute: int = 0,
        label: str = "",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set the branch-local campaign clock without fabricating elapsed time."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        calendar_point = _support.calendar_minute_point(day=day, hour=hour, minute=minute)
        day = calendar_point["day"]
        hour = calendar_point["hour"]
        minute = calendar_point["minute"]
        payload = {
            "day": day,
            "hour": hour,
            "minute": minute,
            "label": str(label).strip(),
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-clock-set:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        if bool(dict(state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("campaign clock cannot be set during active combat")
        existing_clock = dict(state.get("world_time") or {})
        world_time = _support.anchor_world_time(
            state["game_time"],
            day=day,
            hour=hour,
            minute=minute,
            label=str(label).strip(),
        )
        if existing_clock and any(
            int(existing_clock.get(key, 0) or 0) != int(world_time[key])
            for key in ("day", "hour", "minute", "second")
        ):
            raise ValueError(
                "campaign clock is already set; use clock_advance so timed effects "
                "stay synchronized"
            )
        state["world_time"] = world_time

        def clock_set_response(revisions: list[Any]) -> dict[str, Any]:
            return {
                "status": "committed",
                "world_time": world_time,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=state,
            expected_campaign_revision=campaign.revision,
            operation="campaign.clock.set",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=clock_set_response,
            ),
        )
        return clock_set_response(list(revisions_result or []))

    def campaign_advance_effects(
        self,
        campaign_id: str,
        period: str,
        count: int = 1,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        expected_elapsed_ticks: int | None = None,
    ) -> dict[str, Any]:
        """Advance the campaign clock and matching timed effects atomically."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_period = str(period).strip().lower().replace("-", "_")
        if normalized_period not in _support.FIXED_GAME_TIME_PERIODS | {"encounter"}:
            raise ValueError("period must be minute, hour, day, round, or encounter")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("count must be a positive integer")
        if expected_elapsed_ticks is not None and (
            isinstance(expected_elapsed_ticks, bool)
            or not isinstance(expected_elapsed_ticks, int)
            or expected_elapsed_ticks < 0
        ):
            raise ValueError("expected_elapsed_ticks must be a nonnegative integer")
        if (
            normalized_period in _support.NARRATIVE_GAME_TIME_PERIODS
            and expected_elapsed_ticks is None
        ):
            raise ValueError("minute, hour, and day advances require expected_elapsed_ticks")
        if normalized_period == "encounter" and expected_elapsed_ticks is not None:
            raise ValueError(
                "expected_elapsed_ticks is invalid for an encounter advance "
                "because it has no fixed elapsed duration"
            )
        payload = {
            "period": normalized_period,
            "count": count,
            "branch_id": resolved_branch_id,
        }
        if expected_elapsed_ticks is not None:
            payload["expected_elapsed_ticks"] = expected_elapsed_ticks
        scope = f"campaign-advance-effects:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            next_state,
            operation="advancing campaign time",
        )
        if bool(dict(next_state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("campaign time cannot advance during active combat")
        world_time: dict[str, Any] | None = None
        time_transition: dict[str, Any] | None = None
        if normalized_period in _support.FIXED_GAME_TIME_PERIODS:
            next_state, time_transition = self.advance_state_game_time(
                next_state,
                period=normalized_period,
                count=count,
            )
            world_time = time_transition["world_time_after"]
            if (
                expected_elapsed_ticks is not None
                and int(time_transition["after"]["elapsed_ticks"]) != expected_elapsed_ticks
            ):
                raise ValueError(
                    "clock advance does not reach expected_elapsed_ticks: "
                    f"computed {time_transition['after']['elapsed_ticks']}"
                )
            after_elapsed = int(time_transition["after"]["elapsed_ticks"])
            if (
                self.poison_clock_events_due(campaign_id, after_elapsed)
                and _support.active_random_stream() is None
            ):
                with self.campaign_random_context(
                    campaign_id,
                    "campaign.poison.clock",
                    {"idempotency_key": idempotency_key},
                ):
                    return self.campaign_advance_effects(
                        campaign_id,
                        normalized_period,
                        count,
                        principal_id,
                        expected_revision,
                        resolved_branch_id,
                        idempotency_key,
                        expected_elapsed_ticks,
                    )
        elapsed_ticks = int(time_transition["elapsed_ticks"]) if time_transition is not None else 0
        elapsed_minutes = (
            int(time_transition["elapsed_minutes"]) if time_transition is not None else 0
        )
        effect_steps = (
            {"round": int(time_transition["elapsed_ticks"])}
            if time_transition is not None
            else {"encounter": count}
        )
        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps=effect_steps,
        )
        next_state = world_duration["state"]
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]
        world_state_changed = bool(world_advanced or world_expired)
        updates: list[_support.CharacterStateUpdate] = []
        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        rule_receipts: list[dict[str, Any]] = []
        rule_context = self.effective_rule_context(campaign_id)
        for character in self.characters.list(campaign_id=campaign_id):
            sheet = character.sheet
            character_advanced: list[str] = []
            character_expired: list[str] = []
            if elapsed_ticks:
                result = _support.advance_elapsed_effect_durations(
                    sheet,
                    elapsed_ticks=elapsed_ticks,
                )
                extension = _support.apply_rule_event(
                    result["sheet"],
                    "duration.advance",
                    _support.context_with_facts(
                        rule_context,
                        actor_id=character.id,
                        period="tick",
                        amount=elapsed_ticks,
                        elapsed_minutes=elapsed_minutes,
                    ),
                )
                rule_receipts.extend(extension.receipts)
                sheet = extension.sheet
                character_advanced.extend(result["advanced"])
                character_expired.extend(result["expired"])
            for effect_period, amount in effect_steps.items():
                result = _support.advance_effect_durations(
                    sheet,
                    period=effect_period,
                    amount=amount,
                    advance_breathing=not bool(elapsed_ticks and effect_period == "round"),
                )
                extension = _support.apply_rule_event(
                    result["sheet"],
                    "duration.advance",
                    _support.context_with_facts(
                        rule_context,
                        actor_id=character.id,
                        period=effect_period,
                        amount=amount,
                    ),
                )
                rule_receipts.extend(extension.receipts)
                sheet = extension.sheet
                character_advanced.extend(result["advanced"])
                character_expired.extend(result["expired"])
            if not character_advanced and not character_expired and sheet == character.sheet:
                continue
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=character.id,
                    sheet=_support.validate_character_sheet(sheet),
                    notes=_support.validate_character_notes(character.notes),
                    expected_revision=character.revision,
                )
            )
            advanced[character.id] = list(dict.fromkeys(character_advanced))
            expired[character.id] = list(dict.fromkeys(character_expired))
        updates, poison_events, poison_receipts = self.settle_poison_clock_events(
            campaign,
            campaign_id,
            next_state,
            updates,
            branch_id=resolved_branch_id,
        )
        rule_receipts.extend(poison_receipts)
        next_state, updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, updates, {}
        )
        mutation_required = bool(updates or time_transition is not None or world_state_changed)

        def clock_advance_response(revisions: list[Any]) -> dict[str, Any]:
            return {
                "status": "committed" if mutation_required else "no_change",
                "period": normalized_period,
                "count": count,
                "game_time": (time_transition["after"] if time_transition is not None else None),
                "world_time": world_time,
                "advanced": advanced,
                "expired": expired,
                "world_advanced": list(dict.fromkeys(world_advanced)),
                "world_expired": list(dict.fromkeys(world_expired)),
                "poison_events": poison_events,
                "rule_receipts": rule_receipts,
                "ruleset_fingerprint": rule_context.fingerprint,
                "campaign_revision": campaign.revision + (1 if mutation_required else 0),
                "revisions": [_support.asdict(item) for item in revisions],
            }

        revisions_result = None
        if mutation_required:
            revisions_result = _support.StateMutationService(self.storage.database).replace(
                campaign_id,
                campaign_state=(
                    next_state if time_transition is not None or world_state_changed else None
                ),
                character_updates=updates,
                expected_campaign_revision=campaign.revision,
                operation="campaign.effects.advance",
                actor=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=payload,
                    response=clock_advance_response,
                ),
                rule_receipts=rule_receipts,
            )
        response = clock_advance_response(list(revisions_result or []))
        if mutation_required:
            return response
        return self.remember_idempotent(
            scope, idempotency_key, payload, response, campaign_id=campaign_id
        )

    def branch_list(
        self, campaign_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> list[dict[str, Any]]:
        """List playable, non-destructive campaign timelines."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        values = [_support.asdict(item) for item in self.branches.list(campaign_id)]
        if membership.role not in _support.CAMPAIGN_DM_ROLES:
            current = self.current_branch_id(campaign_id)
            return [item for item in values if item["id"] == current]
        return values

    def branch_compare(
        self,
        campaign_id: str,
        left_branch_id: str,
        right_branch_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Compare facts and actor knowledge across branches without auto-merging them."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return self.branches.compare(campaign_id, left_branch_id, right_branch_id)

    def branch_create(
        self,
        campaign_id: str,
        name: str,
        from_snapshot_id: str | None = None,
        checkout: bool = False,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Fork a timeline from a snapshot without changing its source branch."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or expected_branch_id is None or not idempotency_key:
            raise ValueError(
                "expected_revision, expected_branch_id, and idempotency_key are required "
                "for branch creation"
            )
        request_payload = {
            "name": name,
            "from_snapshot_id": from_snapshot_id,
            "checkout": checkout,
            "expected_branch_id": expected_branch_id,
        }
        scope = f"branch-create:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.current_branch_id(campaign_id) != expected_branch_id:
            raise ValueError("active branch changed before branch creation")
        if checkout:
            self.require_no_active_npc_conversation(
                campaign_id,
                branch_id=expected_branch_id,
                operation="creating and checking out a branch",
            )
            source_snapshot_id = (
                from_snapshot_id or self.branches.current(campaign_id).head_snapshot_id
            )
            if source_snapshot_id:
                self.assert_snapshot_core_available(
                    self.snapshots.get_by_id(campaign_id, source_snapshot_id)
                )
            self.snapshots.assert_clean(campaign_id)
        created = self.branches.create(
            campaign_id,
            name=name,
            from_snapshot_id=from_snapshot_id,
            checkout=checkout,
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=lambda value: {
                    **_support.asdict(value["branch"]),
                    "campaign_revision": expected_revision + 1,
                    **(
                        {"snapshot": _support.asdict(value["snapshot"])}
                        if value["snapshot"] is not None
                        else {}
                    ),
                },
            ),
        )
        response = {
            **_support.asdict(created),
            "campaign_revision": self.campaigns.get(campaign_id).revision,
        }
        if checkout and created.head_snapshot_id:
            snapshot = next(
                item
                for item in self.snapshots.list(campaign_id)
                if item.id == created.head_snapshot_id
            )
            response["snapshot"] = _support.asdict(snapshot)
        return response

    def branch_checkout(
        self,
        campaign_id: str,
        branch_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Load a branch head as live campaign state without creating a new save."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or expected_branch_id is None or not idempotency_key:
            raise ValueError(
                "expected_revision, expected_branch_id, and idempotency_key are required "
                "for branch checkout"
            )
        request_payload = {
            "branch_id": branch_id,
            "expected_branch_id": expected_branch_id,
        }
        scope = f"branch-checkout:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.current_branch_id(campaign_id) != expected_branch_id:
            raise ValueError("active branch changed before branch checkout")
        self.require_no_active_npc_conversation(
            campaign_id,
            branch_id=expected_branch_id,
            operation="checking out another branch",
        )
        target_branch = self.branches.get(campaign_id, branch_id)
        if target_branch.head_snapshot_id:
            self.assert_snapshot_core_available(
                self.snapshots.get_by_id(campaign_id, target_branch.head_snapshot_id)
            )
        checked_out = self.branches.checkout(
            campaign_id,
            branch_id,
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=lambda value: {
                    "branch": _support.asdict(value["branch"]),
                    "campaign_revision": expected_revision + int(branch_id != expected_branch_id),
                    "snapshot": (
                        _support.asdict(value["snapshot"])
                        if value["snapshot"] is not None
                        else None
                    ),
                },
            ),
        )
        snapshot = (
            next(
                item
                for item in self.snapshots.list(campaign_id)
                if item.id == checked_out.head_snapshot_id
            )
            if checked_out.head_snapshot_id
            else None
        )
        response = {
            "branch": _support.asdict(checked_out),
            "campaign_revision": self.campaigns.get(campaign_id).revision,
            "snapshot": _support.asdict(snapshot) if snapshot else None,
        }
        return response

    def snapshot_create(
        self,
        campaign_id: str,
        label: str = "",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_head_snapshot_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Save current authoritative state on the current branch.

        Requires campaign expected_revision, idempotency_key, and the exact
        expected_head_snapshot_id from branch_query(list); use "" only for a
        branch without a head. Returns slot (integer) and id; verify via
        snapshot_query(view="verify", payload={slot}), not snapshot_id.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision, expected_head_snapshot_id, and idempotency_key are "
                "required for snapshot creation"
            )
        request_payload = {
            "label": label,
            "expected_head_snapshot_id": expected_head_snapshot_id,
        }
        scope = f"snapshot-create:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        branch = self.branches.current(campaign_id)
        if (branch.head_snapshot_id or "") != expected_head_snapshot_id:
            raise ValueError("branch head changed before snapshot creation")
        response = _support.asdict(
            self.snapshots.create(
                campaign_id,
                label=label,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=request_payload,
                    response=lambda result: _support.asdict(result),
                ),
            )
        )
        return response

    def snapshot_list(
        self, campaign_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> list[dict[str, Any]]:
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return [_support.asdict(item) for item in self.snapshots.list(campaign_id)]

    def snapshot_restore(
        self,
        campaign_id: str,
        slot: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Fork from a saved integer slot, preserving existing future history.

        Pass current expected_revision, expected_branch_id and idempotency_key.
        After restoring, resume once and use the returned branch/revisions;
        saved revision numbers are not current concurrency tokens.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or expected_branch_id is None or not idempotency_key:
            raise ValueError(
                "expected_revision, expected_branch_id, and idempotency_key are required "
                "for snapshot restore"
            )
        request_payload = {
            "slot": slot,
            "expected_branch_id": expected_branch_id,
        }
        scope = f"snapshot-restore:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.current_branch_id(campaign_id) != expected_branch_id:
            raise ValueError("active branch changed before snapshot restore")
        self.require_no_active_npc_conversation(
            campaign_id,
            branch_id=expected_branch_id,
            operation="restoring a snapshot",
        )
        self.assert_snapshot_core_available(self.snapshots.get(campaign_id, slot))
        response = _support.asdict(
            self.snapshots.restore(
                campaign_id,
                slot,
                idempotency_key=idempotency_key,
                idempotency_write=_support.IdempotencyWrite(
                    scope=scope,
                    payload=request_payload,
                    response=lambda result: _support.asdict(result),
                ),
            )
        )
        return response

    def snapshot_core_lock(
        self,
        campaign_id: str,
        slot: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Inspect one snapshot's immutable Core lock and current conversion target."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        document = self.snapshots.get(campaign_id, slot)
        profile = dict(document.get("payload", {}).get("rule_profile") or {})
        options = dict(profile.get("options") or {})
        locked = dict(options.get("_core_rule_pack_lock") or {})
        edition = str(profile.get("edition") or "")
        available = None
        if edition:
            try:
                pack = _support.get_core_rule_pack(edition)
                available = {
                    "id": pack.id,
                    "version": pack.version,
                    "edition": pack.edition,
                    "fingerprint": pack.fingerprint,
                }
            except (KeyError, ValueError):
                available = None
        return {
            "snapshot": {
                "id": document["id"],
                "slot": document["slot"],
                "checksum": document["checksum"],
                "valid": bool(document["valid"]),
            },
            "edition": edition,
            "core_pack": locked or None,
            "available_core_pack": available,
            "conversion_required": bool(
                locked
                and available
                and locked
                != {
                    "id": available["id"],
                    "version": available["version"],
                    "fingerprint": available["fingerprint"],
                }
            ),
        }

    def snapshot_restore_core_upgrade(
        self,
        campaign_id: str,
        slot: int,
        name: str,
        expected_snapshot_core_fingerprint: str,
        expected_runtime_core_fingerprint: str,
        reason: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Fork an old-Core snapshot after an explicit, audited runtime conversion."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or expected_branch_id is None or not idempotency_key:
            raise ValueError(
                "expected_revision, expected_branch_id, and idempotency_key are required "
                "for snapshot Core conversion"
            )
        normalized_name = str(name or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_name:
            raise ValueError("name is required for snapshot Core conversion")
        if not normalized_reason or len(normalized_reason) > 500:
            raise ValueError("a reason of at most 500 characters is required for Core conversion")
        request_payload = {
            "slot": int(slot),
            "name": normalized_name,
            "expected_snapshot_core_fingerprint": expected_snapshot_core_fingerprint,
            "expected_runtime_core_fingerprint": expected_runtime_core_fingerprint,
            "reason": normalized_reason,
            "expected_branch_id": expected_branch_id,
        }
        scope = f"snapshot-core-convert:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.current_branch_id(campaign_id) != expected_branch_id:
            raise ValueError("active branch changed before snapshot Core conversion")
        if dict(campaign.state or {}).get("combat", {}).get("active", False):
            raise _support.CombatEngineError(
                "snapshot Core conversion cannot run during active combat"
            )
        lock_view = self.snapshot_core_lock(campaign_id, slot, principal_id)
        if not lock_view["snapshot"]["valid"]:
            raise ValueError("snapshot failed integrity verification")
        previous = dict(lock_view.get("core_pack") or {})
        latest = dict(lock_view.get("available_core_pack") or {})
        if not previous:
            raise _support.RulesetUnavailableError(
                "snapshot has no locked built-in core rule pack; "
                "an explicit edition migration is required"
            )
        if not latest:
            raise _support.RulesetUnavailableError(
                "snapshot edition has no available built-in Core"
            )
        if not lock_view["conversion_required"]:
            raise ValueError("snapshot already uses the available built-in Core")
        if previous.get("fingerprint") != expected_snapshot_core_fingerprint:
            raise ValueError("expected snapshot Core fingerprint does not match")
        if latest.get("fingerprint") != expected_runtime_core_fingerprint:
            raise ValueError("expected runtime Core fingerprint does not match")
        document = self.snapshots.get(campaign_id, slot)
        source_profile = _support.deepcopy(dict(document["payload"].get("rule_profile") or {}))
        source_options = dict(source_profile.get("options") or {})
        user_options = {
            key: value for key, value in source_options.items() if key != "_core_rule_pack_lock"
        }
        converted_profile = {
            **source_profile,
            "options": self.profile_options_with_core_lock(
                str(source_profile.get("edition") or ""),
                user_options,
            ),
        }

        def conversion_response(result: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "converted",
                "reason": normalized_reason,
                "source_snapshot": lock_view["snapshot"],
                "previous_core_pack": previous,
                "core_pack": latest,
                "branch": _support.asdict(result["branch"]),
                "snapshot": _support.asdict(result["snapshot"]),
                "campaign_revision": result["campaign_revision"],
            }

        self.snapshots.restore_with_rule_profile_conversion(
            campaign_id,
            slot,
            rule_profile=converted_profile,
            branch_name=normalized_name,
            label=f"Converted Core for slot {slot}: {normalized_reason}",
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=conversion_response,
            ),
        )
        committed = self.idempotency.lookup(scope, str(idempotency_key), request_payload)
        assert committed is not None and committed.response is not None
        return committed.response

    def snapshot_verify(
        self, campaign_id: str, slot: int, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, bool | int]:
        """Verify that a saved snapshot has an internally consistent payload."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        document = self.snapshots.get(campaign_id, slot)
        campaign = dict(document.get("payload", {}).get("campaign") or {})
        return {
            "valid": self.snapshots.verify(campaign_id, slot),
            "captured_campaign_revision": int(campaign.get("revision") or 0),
        }

    def snapshot_lineage(
        self,
        campaign_id: str,
        slot: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """List the lineage of a save without mutating campaign history."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return [_support.asdict(item) for item in self.snapshots.lineage(campaign_id, slot)]

    def snapshot_regenerate_recap(
        self, campaign_id: str, slot: int, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, Any]:
        """Regenerate a deterministic recap from a saved snapshot payload."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return self.snapshots.regenerate_recap(campaign_id, slot)

    def campaign_stable_recovery(
        self,
        campaign_id: str,
        members: list[dict[str, Any]],
        resting_members: list[dict[str, Any]] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Recover Stable creatures while conscious companions rest on the same clock."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if not isinstance(members, list) or not members:
            raise ValueError("stable recovery requires at least one member")
        normalized_members: list[dict[str, Any]] = []
        for index, member in enumerate(members):
            if not isinstance(member, dict):
                raise ValueError(f"members[{index}] must be an object")
            unknown = sorted(set(member) - {"character_id", "expected_revision"})
            character_id = str(member.get("character_id") or "").strip()
            character_revision = member.get("expected_revision")
            if unknown:
                raise ValueError(f"members[{index}] has unsupported fields: {unknown}")
            if not character_id:
                raise ValueError(f"members[{index}].character_id is required")
            if isinstance(character_revision, bool) or not isinstance(character_revision, int):
                raise ValueError(f"members[{index}].expected_revision is required")
            normalized_members.append(
                {
                    "character_id": character_id,
                    "expected_revision": character_revision,
                }
            )
        member_ids = [item["character_id"] for item in normalized_members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("stable recovery member ids must be unique")
        if resting_members is None:
            resting_members = []
        if not isinstance(resting_members, list):
            raise ValueError("resting_members must be an array")
        allowed_resting_fields = {
            "character_id",
            "expected_revision",
            "rest_activity_minutes",
            "hit_dice_spends",
            "arcane_recovery",
            "natural_recovery",
            "sorcerous_restoration_points",
            "song_of_rest_source_actor_id",
            "attune_item_id",
            "attunement_prerequisite_confirmed",
        }
        normalized_resting: list[dict[str, Any]] = []
        for index, raw_member in enumerate(resting_members):
            if not isinstance(raw_member, dict):
                raise ValueError(f"resting_members[{index}] must be an object")
            unknown = sorted(set(raw_member) - allowed_resting_fields)
            if unknown:
                raise ValueError(f"resting_members[{index}] has unsupported fields: {unknown}")
            character_id = str(raw_member.get("character_id") or "").strip()
            character_revision = raw_member.get("expected_revision")
            if not character_id:
                raise ValueError(f"resting_members[{index}].character_id is required")
            if isinstance(character_revision, bool) or not isinstance(character_revision, int):
                raise ValueError(f"resting_members[{index}].expected_revision is required")
            hit_dice_spends = raw_member.get("hit_dice_spends")
            arcane_recovery = raw_member.get("arcane_recovery")
            natural_recovery = raw_member.get("natural_recovery")
            sorcerous_restoration_points = raw_member.get("sorcerous_restoration_points")
            if hit_dice_spends is not None and not isinstance(hit_dice_spends, list):
                raise ValueError(f"resting_members[{index}].hit_dice_spends must be an array")
            if arcane_recovery is not None and not isinstance(arcane_recovery, dict):
                raise ValueError(f"resting_members[{index}].arcane_recovery must be an object")
            if natural_recovery is not None and not isinstance(natural_recovery, dict):
                raise ValueError(f"resting_members[{index}].natural_recovery must be an object")
            if sorcerous_restoration_points is not None and (
                isinstance(sorcerous_restoration_points, bool)
                or not isinstance(sorcerous_restoration_points, int)
            ):
                raise ValueError(
                    f"resting_members[{index}].sorcerous_restoration_points must be an integer"
                )
            attune_item_id = str(raw_member.get("attune_item_id") or "").strip() or None
            attunement_confirmed = raw_member.get("attunement_prerequisite_confirmed")
            if attune_item_id and attunement_confirmed is not True:
                raise _support.NeedsRulingError(
                    "attunement requires explicit DM confirmation that the actor "
                    "satisfies every source-defined prerequisite",
                    missing=("attunement_prerequisite",),
                    ruling_kind="source_or_scene_fact",
                )
            if not attune_item_id and attunement_confirmed is not None:
                raise ValueError("attunement_prerequisite_confirmed requires attune_item_id")
            normalized_resting.append(
                {
                    "character_id": character_id,
                    "expected_revision": character_revision,
                    "rest_activity_minutes": _support.validate_rest_activity_minutes(
                        raw_member.get("rest_activity_minutes")
                    ),
                    "hit_dice_spends": list(hit_dice_spends or []),
                    "arcane_recovery": _support.deepcopy(arcane_recovery or {}),
                    "natural_recovery": _support.deepcopy(natural_recovery or {}),
                    "sorcerous_restoration_points": (sorcerous_restoration_points),
                    "song_of_rest_source_actor_id": (
                        str(raw_member.get("song_of_rest_source_actor_id") or "").strip() or None
                    ),
                    "attune_item_id": attune_item_id,
                    "attunement_prerequisite_confirmed": attunement_confirmed,
                }
            )
        resting_member_ids = [item["character_id"] for item in normalized_resting]
        if len(resting_member_ids) != len(set(resting_member_ids)):
            raise ValueError("resting member ids must be unique")
        if set(member_ids) & set(resting_member_ids):
            raise ValueError(
                "a stable recovery member cannot also complete a concurrent short rest"
            )
        song_source_ids = {
            str(item["song_of_rest_source_actor_id"])
            for item in normalized_resting
            if item["song_of_rest_source_actor_id"] is not None
        }
        if song_source_ids - set(resting_member_ids):
            raise _support.CombatEngineError(
                "every Song of Rest source must participate in the concurrent short rest"
            )
        request_payload = {
            "operation": "campaign.party.stable_recovery",
            "members": _support.deepcopy(normalized_members),
            "resting_members": _support.deepcopy(normalized_resting),
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-stable-recovery:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            next_state,
            operation="stable recovery",
        )
        if bool(dict(next_state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError(
                "stable recovery is not allowed while combat is active"
            )
        all_characters = {item.id: item for item in self.characters.list(campaign_id=campaign_id)}
        member_by_id = {item["character_id"]: item for item in normalized_members}
        resting_by_id = {item["character_id"]: item for item in normalized_resting}
        for member in normalized_members:
            current = all_characters.get(member["character_id"])
            if current is None:
                raise ValueError(
                    f"stable recovery actor is not in this campaign: {member['character_id']}"
                )
            self.require_character_control(current, principal_id)
            if current.revision != member["expected_revision"]:
                raise ValueError(f"character revision conflict: {current.id}")
            _support.recover_stable_creature(current.sheet, recovery_hours=1)
        for member in normalized_resting:
            current = all_characters.get(member["character_id"])
            if current is None:
                raise ValueError(
                    f"concurrent rest actor is not in this campaign: {member['character_id']}"
                )
            self.require_character_control(current, principal_id)
            if current.revision != member["expected_revision"]:
                raise ValueError(f"character revision conflict: {current.id}")
            _support.validate_initial_rest_hit_dice_requests(
                current.sheet,
                member["hit_dice_spends"],
            )
            _support.validate_natural_recovery_choice(
                current.sheet,
                member["natural_recovery"],
                rest_activity_minutes=member["rest_activity_minutes"],
            )
            _support.validate_sorcerous_restoration_choice(
                current.sheet,
                member["sorcerous_restoration_points"],
            )
            if member["song_of_rest_source_actor_id"] is not None:
                _support.validate_song_of_rest_source(
                    all_characters[str(member["song_of_rest_source_actor_id"])].sheet
                )
            if member["attune_item_id"] is not None:
                _support.attune_inventory_item(current.sheet, str(member["attune_item_id"]))

        recovery_rolls = {
            character_id: _support.asdict(_support.roll("1d4")) for character_id in member_ids
        }
        recovery_hours = {
            character_id: int(recovery_rolls[character_id]["total"]) for character_id in member_ids
        }
        elapsed_hours = max(recovery_hours.values())
        duration_minutes = elapsed_hours * 60
        started_elapsed_ticks = int(next_state["game_time"]["elapsed_ticks"])
        for member in normalized_resting:
            member["derived_rest_timing"] = _support.validate_rest_schedule(
                rest_type="short_rest",
                duration_minutes=duration_minutes,
                allows_trance=False,
            )
            _support.record_rest_completion(
                all_characters[member["character_id"]].sheet,
                rest_type="short_rest",
                started_elapsed_ticks=started_elapsed_ticks,
                completed_elapsed_ticks=started_elapsed_ticks
                + _support.game_time_ticks("hour", elapsed_hours),
            )
        next_state, time_transition = self.advance_state_game_time(
            next_state,
            period="hour",
            count=elapsed_hours,
        )
        next_world_time = time_transition["world_time_after"]
        elapsed_ticks = int(time_transition["elapsed_ticks"])
        elapsed_minutes = elapsed_ticks // _support.TICKS_PER_MINUTE
        completed_game_day = _support.rules_day_from_ticks(
            int(time_transition["after"]["elapsed_ticks"])
        )
        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": elapsed_ticks},
        )
        next_state = world_duration["state"]
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]

        rules = self.effective_rule_context(campaign_id)
        receipts: list[dict[str, Any]] = []
        updates: list[_support.CharacterStateUpdate] = []
        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        recoveries: dict[str, dict[str, Any]] = {}
        rested: dict[str, dict[str, Any]] = {}
        for current in all_characters.values():
            sheet = current.sheet
            character_advanced: list[str] = []
            character_expired: list[str] = []
            duration_result = _support.advance_elapsed_effect_durations(
                sheet,
                elapsed_ticks=elapsed_ticks,
            )
            round_result = _support.advance_effect_durations(
                duration_result["sheet"],
                period="round",
                amount=int(time_transition["elapsed_ticks"]),
                advance_breathing=False,
            )
            extension = _support.apply_rule_event(
                round_result["sheet"],
                "duration.advance",
                _support.context_with_facts(
                    rules,
                    actor_id=current.id,
                    period="minute",
                    amount=elapsed_minutes,
                ),
            )
            receipts.extend(extension.receipts)
            sheet = extension.sheet
            character_advanced.extend(duration_result["advanced"])
            character_expired.extend(duration_result["expired"])
            character_advanced.extend(round_result["advanced"])
            character_expired.extend(round_result["expired"])
            member = member_by_id.get(current.id)
            if member is not None:
                applied = _support.recover_stable_creature(
                    sheet, recovery_hours=recovery_hours[current.id]
                )
                sheet = applied["sheet"]
                recoveries[current.id] = {
                    "status": applied["status"],
                    "recovery_roll": recovery_rolls[current.id],
                    "recovery_hours": applied["recovery_hours"],
                    "before_hp": applied["before_hp"],
                    "after_hp": applied["after_hp"],
                }
                receipts.extend(
                    _support.core_receipts(
                        self.effective_rule_context(
                            campaign_id,
                            facts={
                                "actor_id": current.id,
                                "recovery_hours": recovery_hours[current.id],
                            },
                        ),
                        ["dnd5e.core.damage.stable_recovery"],
                        "character.stable_recovery",
                    )
                )
            resting_member = resting_by_id.get(current.id)
            if resting_member is not None:
                rest_rules = self.effective_rule_context(
                    campaign_id,
                    facts={
                        "actor_id": current.id,
                        "rest_type": "short_rest",
                    },
                )
                song_source_id = resting_member["song_of_rest_source_actor_id"]
                song_source_sheet = (
                    all_characters[str(song_source_id)].sheet
                    if song_source_id is not None
                    else None
                )
                applied_rest = _support.apply_rest(
                    sheet,
                    rest_type="short_rest",
                    rest_activity_minutes=resting_member["rest_activity_minutes"],
                    rules=rest_rules,
                    game_day=completed_game_day,
                    hit_dice_spends=resting_member["hit_dice_spends"],
                    arcane_recovery=resting_member["arcane_recovery"],
                    natural_recovery=resting_member["natural_recovery"],
                    sorcerous_restoration_points=resting_member["sorcerous_restoration_points"],
                    song_of_rest_source_sheet=song_source_sheet,
                    rng=_support.active_random_stream(),
                )
                if applied_rest.get("status") != "committed":
                    raise _support.CombatEngineError(
                        f"concurrent short rest for {current.id} requires an unresolved rule choice"
                    )
                sheet = applied_rest["sheet"]
                if resting_member["attune_item_id"] is not None:
                    sheet = _support.attune_inventory_item(
                        sheet,
                        str(resting_member["attune_item_id"]),
                    )
                    applied_rest["attuned_item_id"] = str(resting_member["attune_item_id"])
                sheet = _support.record_rest_completion(
                    sheet,
                    rest_type="short_rest",
                    started_elapsed_ticks=started_elapsed_ticks,
                    completed_elapsed_ticks=int(time_transition["after"]["elapsed_ticks"]),
                    hit_dice_spent_count=len(applied_rest.get("hit_dice_rolls") or []),
                    expected_character_revision=current.revision + 1,
                    song_of_rest_die_sides=(
                        _support.validate_song_of_rest_source(song_source_sheet)
                        if song_source_sheet is not None
                        else None
                    ),
                    song_of_rest_used=applied_rest.get("song_of_rest") is not None,
                )
                rested[current.id] = {
                    key: value
                    for key, value in applied_rest.items()
                    if key not in {"sheet", "rule_receipts"}
                }
                rested[current.id]["short_rest_hit_dice"] = _support.deepcopy(
                    dict(sheet.get("combat") or {}).get("short_rest_hit_dice")
                )
                receipts.extend(applied_rest.get("rule_receipts") or [])
            if sheet != current.sheet:
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=current.id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(current.notes),
                        expected_revision=(
                            member["expected_revision"] if member is not None else current.revision
                        ),
                    )
                )
            if character_advanced:
                advanced[current.id] = list(dict.fromkeys(character_advanced))
            if character_expired:
                expired[current.id] = list(dict.fromkeys(character_expired))
        next_state, updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, updates, {}
        )
        updates = self.reconcile_completed_item_attunements(
            campaign,
            next_state,
            updates,
            {
                member["character_id"]: str(member["attune_item_id"])
                for member in normalized_resting
                if member["attune_item_id"] is not None
                and all_characters[member["character_id"]].sheet.get("edition") == "2014"
            },
        )
        update_by_id = {item.character_id: item for item in updates}
        stream = _support.active_random_stream()
        response = {
            "status": "recovered",
            "member_ids": member_ids,
            "resting_member_ids": resting_member_ids,
            "elapsed_hours": elapsed_hours,
            "recoveries": recoveries,
            "rested": rested,
            "characters": {
                character_id: self.character_view(
                    _support.replace(
                        all_characters[character_id],
                        sheet=update_by_id[character_id].sheet,
                        notes=update_by_id[character_id].notes,
                        revision=all_characters[character_id].revision + 1,
                    )
                )
                for character_id in member_ids
            },
            "game_time": time_transition["after"],
            "world_time": next_world_time,
            "advanced": advanced,
            "expired": expired,
            "world_advanced": list(dict.fromkeys(world_advanced)),
            "world_expired": list(dict.fromkeys(world_expired)),
            "rule_receipts": receipts,
            "campaign_revision": campaign.revision + 1,
            **(
                {"random_stream_receipt": stream.receipt()}
                if stream is not None and stream.draw_count > 0
                else {}
            ),
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=next_state,
            character_updates=updates,
            expected_campaign_revision=campaign.revision,
            operation="campaign.party.stable_recovery",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
            rule_receipts=receipts,
        )
        return response

    def campaign_party_travel(
        self,
        campaign_id: str,
        trip: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one source-bound 2014 travel leg and forced-march saves atomically."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if not isinstance(trip, dict):
            raise ValueError("trip must be an object")
        if set(trip) - {
            "travel_id",
            "pace",
            "distance_miles",
            "difficult_terrain",
            "route_fact",
            "terrain_fact",
            "end_trip",
            "participants",
        }:
            raise ValueError("trip has unsupported fields")
        raw_participants = trip.get("participants")
        if not isinstance(raw_participants, list) or not raw_participants:
            raise ValueError("trip.participants must be a non-empty array")
        leg = validate_travel_leg(
            {key: value for key, value in trip.items() if key != "participants"}
        )
        payload = {"trip": _support.deepcopy(trip), "branch_id": resolved_branch_id}
        scope = f"campaign-party-travel:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        participant_fields = {"character_id", "expected_revision", "survival_intake"}
        normalized_members: list[dict[str, Any]] = []
        member_ids: list[str] = []
        campaign = self.campaigns.get(campaign_id)
        campaign_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if bool(dict(campaign_state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("party travel is not allowed during active combat")
        for index, raw in enumerate(raw_participants):
            if not isinstance(raw, dict) or set(raw) - participant_fields:
                raise ValueError(f"trip.participants[{index}] has unsupported fields")
            actor_id = str(raw.get("character_id") or "").strip()
            revision = raw.get("expected_revision")
            if not actor_id:
                raise ValueError(f"trip.participants[{index}].character_id is required")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError(f"trip.participants[{index}].expected_revision is required")
            record = self.characters.get(actor_id)
            if record.campaign_id != campaign_id or record.sheet.get("edition") != "2014":
                raise _support.CombatEngineError(
                    "2014 party travel participants must be 2014 characters in this campaign"
                )
            if record.revision != revision:
                raise ValueError(f"character revision conflict: {actor_id}")
            raw_intake = raw.get("survival_intake")
            if not isinstance(raw_intake, dict):
                raise ValueError(
                    f"trip.participants[{index}].survival_intake is required for daily travel"
                )
            allowed_intake = {
                "food_lb",
                "water_gallons",
                "hot_weather",
                "weather_fact",
                "rations",
            }
            unknown_intake = set(raw_intake) - allowed_intake
            if unknown_intake:
                raise ValueError(
                    f"trip.participants[{index}].survival_intake has unsupported fields: "
                    f"{sorted(unknown_intake)}"
                )
            base_intake = validate_daily_intake(
                {
                    key: raw_intake[key]
                    for key in ("food_lb", "water_gallons", "hot_weather", "weather_fact")
                    if key in raw_intake
                }
            )
            ration_entries = raw_intake.get("rations", [])
            if not isinstance(ration_entries, list) or len(ration_entries) > 20:
                raise ValueError("survival_intake.rations must be a bounded array")
            inventory = dict(record.sheet.get("inventory") or {})
            consumed_items = []
            ration_food_lb = 0
            for ration_index, ration in enumerate(ration_entries):
                if not isinstance(ration, dict) or set(ration) != {"item_id", "quantity"}:
                    raise ValueError(
                        f"survival_intake.rations[{ration_index}] requires item_id and quantity"
                    )
                item_id, quantity = str(ration.get("item_id") or "").strip(), ration.get("quantity")
                item = next(
                    (
                        entry
                        for entry in inventory.get("items", [])
                        if str(entry.get("id") or "") == item_id
                    ),
                    None,
                )
                source_key = str((item or {}).get("source_key") or "")
                if (
                    not item_id
                    or isinstance(quantity, bool)
                    or not isinstance(quantity, int)
                    or quantity < 1
                    or item is None
                    or str(item.get("name") or "").strip().casefold() not in {"ration", "rations"}
                    or source_key.rsplit(":", 1)[-1] != "dnd5e.content.srd2014.item.rations"
                    or quantity > int(item.get("quantity", 0) or 0)
                ):
                    raise ValueError(
                        "survival rations must reference available bundled 2014 Rations"
                    )
                consumed_items.append(
                    {
                        "item_id": item_id,
                        "source_key": source_key,
                        "quantity": quantity,
                    }
                )
                ration_food_lb += quantity
            base_intake["food_lb"] += ration_food_lb
            base_intake["consumed_items"] = consumed_items
            base_intake = validate_daily_intake(base_intake)
            normalized_members.append(
                {
                    "character_id": actor_id,
                    "expected_revision": revision,
                    "survival_intake": base_intake,
                }
            )
            member_ids.append(actor_id)
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("trip participant ids must be unique")
        member_ids.sort()
        members_by_id = {item["character_id"]: item for item in normalized_members}
        campaign_characters = {
            item.id: item for item in self.characters.list(campaign_id=campaign_id)
        }
        required_ids = sorted(
            item.id for item in campaign_characters.values() if item.sheet.get("edition") == "2014"
        )
        if member_ids != required_ids:
            raise _support.NeedsRulingError(
                "daily travel must identify every 2014 campaign character and its intake",
                missing=tuple(
                    f"trip.participants.{actor_id}"
                    for actor_id in sorted(set(required_ids) - set(member_ids))
                ),
                ruling_kind="source_or_scene_fact",
            )

        current_ticks = int(campaign_state["game_time"]["elapsed_ticks"])
        current_day = current_ticks // TICKS_PER_DAY
        travel_state = campaign_state["travel"]
        day_elapsed = (
            int(travel_state["day_elapsed_minutes"])
            if int(travel_state["day_index"]) == current_day
            else 0
        )
        active = travel_state.get("active")
        if active is not None and (
            active["travel_id"] != leg["travel_id"]
            or active["pace"] != leg["pace"]
            or active["participant_ids"] != member_ids
        ):
            raise _support.CombatEngineError(
                "end the current party travel leg before changing route, pace, or participants"
            )
        prior_trip_elapsed = int(active["elapsed_minutes"]) if active else 0
        prior_trip_distance = float(active["distance_miles"]) if active else 0.0
        effective_distance = leg["distance_miles"] * (2 if leg["difficult_terrain"] else 1)
        trip_distance = prior_trip_distance + effective_distance
        trip_elapsed = travel_duration_minutes(trip_distance, leg["pace"])
        duration_minutes = trip_elapsed - prior_trip_elapsed
        if duration_minutes < 1:
            raise _support.CombatEngineError("travel leg must advance at least one minute")
        if duration_minutes > 1440:
            raise _support.NeedsRulingError(
                "split journeys longer than one day at a daily travel boundary",
                missing=("travel.daily_segment",),
                ruling_kind="source_or_scene_fact",
            )
        minutes_to_midnight = max(
            1,
            (TICKS_PER_DAY - current_ticks % TICKS_PER_DAY) // TICKS_PER_MINUTE,
        )
        if duration_minutes > minutes_to_midnight:
            raise _support.NeedsRulingError(
                "split party travel at the campaign day boundary so daily intake "
                "settles atomically",
                missing=("travel.day_boundary",),
                ruling_kind="source_or_scene_fact",
            )
        dcs = forced_march_save_dcs(day_elapsed, duration_minutes)
        if day_elapsed + duration_minutes > 1440:
            raise _support.NeedsRulingError(
                "split a forced march before it passes a second unsettled travel day",
                missing=("travel.daily_segment",),
                ruling_kind="source_or_scene_fact",
            )
        if dcs and _support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id,
                "campaign.party.travel",
                {"idempotency_key": idempotency_key},
            ):
                return self.campaign_party_travel(
                    campaign_id,
                    trip,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                )

        next_state = _support.deepcopy(campaign_state)
        next_state, time_transition = self.advance_state_game_time(
            next_state,
            period="minute",
            count=duration_minutes,
        )
        elapsed_ticks = int(time_transition["elapsed_ticks"])
        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": elapsed_ticks},
        )
        next_state = world_duration["state"]

        survival = next_state["survival"]
        day_entries = survival["daily_intakes"].setdefault(str(current_day), {})
        updates_by_actor: dict[str, _support.CharacterStateUpdate] = {}
        for actor_id in member_ids:
            record = campaign_characters[actor_id]
            member = members_by_id[actor_id]
            if current_day <= int(survival["last_settled_day"]):
                raise _support.CombatEngineError(
                    "travel intake cannot be added to an already settled campaign day"
                )
            merged = merge_daily_intake(day_entries.get(actor_id), member["survival_intake"])
            day_entries[actor_id] = merged
            sheet = record.sheet
            for ration in member["survival_intake"]["consumed_items"]:
                sheet, _ = _support.remove_inventory_item(
                    sheet,
                    ration["item_id"],
                    ration["quantity"],
                )
            if sheet != record.sheet:
                updates_by_actor[actor_id] = _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=sheet,
                    notes=record.notes,
                    expected_revision=record.revision,
                )
        survival["daily_intakes"] = next_state["survival"]["daily_intakes"]
        next_state["survival"] = survival

        travel_record = {
            "travel_id": leg["travel_id"],
            "pace": leg["pace"],
            "distance_miles": leg["distance_miles"],
            "difficult_terrain": leg["difficult_terrain"],
            "duration_minutes": duration_minutes,
            "started_elapsed_ticks": current_ticks,
            "completed_elapsed_ticks": int(time_transition["after"]["elapsed_ticks"]),
            "participant_ids": member_ids,
            "route_fact": leg["route_fact"],
            "terrain_fact": leg.get("terrain_fact"),
            "forced_march_saves": [],
        }
        travel_state = next_state["travel"]
        ledger = [*travel_state["ledger"], travel_record][-100:]
        is_new_day = int(next_state["game_time"]["elapsed_ticks"]) // TICKS_PER_DAY != current_day
        travel_state.update(
            {
                "day_index": current_day + (1 if is_new_day else 0),
                "day_elapsed_minutes": 0 if is_new_day else day_elapsed + duration_minutes,
                "active": (
                    None
                    if leg["end_trip"]
                    else {
                        "travel_id": leg["travel_id"],
                        "pace": leg["pace"],
                        "participant_ids": member_ids,
                        "elapsed_minutes": trip_elapsed,
                        "distance_miles": trip_distance,
                    }
                ),
                "ledger": ledger,
            }
        )
        next_state["travel"] = travel_state

        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]
        rule_receipts: list[dict[str, Any]] = []
        rule_context = self.effective_rule_context(campaign_id, branch_id=resolved_branch_id)
        for record in campaign_characters.values():
            update = updates_by_actor.get(record.id)
            sheet = update.sheet if update is not None else record.sheet
            actor_advanced: list[str] = []
            actor_expired: list[str] = []
            duration = _support.advance_elapsed_effect_durations(
                sheet,
                elapsed_ticks=elapsed_ticks,
            )
            extension = _support.apply_rule_event(
                duration["sheet"],
                "duration.advance",
                _support.context_with_facts(
                    rule_context,
                    actor_id=record.id,
                    period="tick",
                    amount=elapsed_ticks,
                    elapsed_minutes=duration_minutes,
                ),
            )
            sheet = extension.sheet
            rule_receipts.extend(extension.receipts)
            actor_advanced.extend(duration["advanced"])
            actor_expired.extend(duration["expired"])
            round_duration = _support.advance_effect_durations(
                sheet,
                period="round",
                amount=elapsed_ticks,
                advance_breathing=False,
            )
            extension = _support.apply_rule_event(
                round_duration["sheet"],
                "duration.advance",
                _support.context_with_facts(
                    rule_context,
                    actor_id=record.id,
                    period="round",
                    amount=elapsed_ticks,
                ),
            )
            sheet = extension.sheet
            rule_receipts.extend(extension.receipts)
            actor_advanced.extend(round_duration["advanced"])
            actor_expired.extend(round_duration["expired"])
            if sheet != (update.sheet if update is not None else record.sheet):
                updates_by_actor[record.id] = _support.CharacterStateUpdate(
                    character_id=record.id,
                    sheet=_support.validate_character_sheet(sheet),
                    notes=update.notes if update is not None else record.notes,
                    expected_revision=(
                        update.expected_revision if update is not None else record.revision
                    ),
                )
            if actor_advanced:
                advanced[record.id] = list(dict.fromkeys(actor_advanced))
            if actor_expired:
                expired[record.id] = list(dict.fromkeys(actor_expired))

        forced_march_results = []
        stream = _support.active_random_stream()
        for hour_index, dc in enumerate(dcs, start=1):
            for actor_id in member_ids:
                record = campaign_characters[actor_id]
                update = updates_by_actor.get(actor_id)
                sheet = update.sheet if update is not None else record.sheet
                if "dead" in _support.condition_ids(sheet.get("conditions")):
                    forced_march_results.append(
                        {
                            "actor_id": actor_id,
                            "hour_after_eight": hour_index,
                            "dc": dc,
                            "skipped": "dead",
                            "exhaustion_added": 0,
                        }
                    )
                    continue
                snapshot = self.combat_actor_snapshot(actor_id)
                snapshot["sheet"] = _support.deepcopy(sheet)
                snapshot["derived"] = derive_character_sheet(sheet)
                check_rules = self.effective_rule_context(
                    campaign_id,
                    branch_id=resolved_branch_id,
                    facts={
                        "actor_id": actor_id,
                        "travel_id": leg["travel_id"],
                        "hour_after_eight": hour_index,
                        "kind": "save",
                        "ability": "constitution",
                        "dc": dc,
                    },
                )
                save = _support.resolve_actor_check(
                    snapshot,
                    kind="save",
                    ability="constitution",
                    dc=dc,
                    encounter=next_state.get("combat"),
                    rules=check_rules,
                    rng=stream,
                    ruleset="2014",
                )
                applied = settle_forced_march_save(sheet, save)
                if applied["sheet"] != sheet:
                    updates_by_actor[actor_id] = _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=applied["sheet"],
                        notes=update.notes if update is not None else record.notes,
                        expected_revision=(
                            update.expected_revision if update is not None else record.revision
                        ),
                    )
                settlement = {
                    "actor_id": actor_id,
                    "hour_after_eight": hour_index,
                    "dc": dc,
                    "save": save,
                    "exhaustion_added": applied["exhaustion_added"],
                }
                if applied.get("died"):
                    settlement["died"] = True
                forced_march_results.append(settlement)
        travel_record["forced_march_saves"] = forced_march_results
        next_state["travel"]["ledger"][-1] = travel_record
        updates = list(updates_by_actor.values())
        next_state, updates, _ = self.reconcile_actor_effect_dependencies(
            campaign,
            next_state,
            updates,
            {},
        )
        receipts = _support.core_receipts(
            rule_context,
            [
                "dnd5e.core.travel.pace_2014",
                *(["dnd5e.core.travel.difficult_terrain_2014"] if leg["difficult_terrain"] else []),
                *(["dnd5e.core.travel.forced_march_2014"] if dcs else []),
            ],
            "campaign.travel.settlement",
        )
        rule_receipts.extend(receipts)
        response_fields = {
            "status": "committed",
            "travel_id": leg["travel_id"],
            "pace": leg["pace"],
            "distance_miles": leg["distance_miles"],
            "difficult_terrain": leg["difficult_terrain"],
            "duration_minutes": duration_minutes,
            "game_time": time_transition["after"],
            "world_time": time_transition["world_time_after"],
            "forced_march_saves": forced_march_results,
            "advanced": advanced,
            "expired": expired,
            "world_advanced": list(dict.fromkeys(world_advanced)),
            "world_expired": list(dict.fromkeys(world_expired)),
            "ruleset_fingerprint": rule_context.fingerprint,
            "rule_receipts": rule_receipts,
        }
        return self.commit_campaign_state(
            campaign,
            next_state,
            operation="campaign.party.travel",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields=response_fields,
            character_updates=updates,
            rule_receipts=rule_receipts,
            expected_campaign_revision=campaign.revision,
        )

    def campaign_party_rest(
        self,
        campaign_id: str,
        members: list[dict[str, Any]],
        duration_minutes: int = _support.LONG_REST_MINIMUM_MINUTES,
        rest_type: str = "long_rest",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Advance one short or long rest and settle every member atomically."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_rest_type = str(rest_type).strip().lower().replace("-", "_")
        if normalized_rest_type not in _support.REST_TYPES:
            raise _support.CombatEngineError("rest_type must be short_rest or long_rest")
        if normalized_rest_type == "short_rest" and _support.active_random_stream() is None:
            campaign_snapshot = self.campaigns.get(campaign_id)
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign_snapshot.state,
                operation="campaign_change",
                idempotency_key=str(idempotency_key or ""),
                campaign_revision=campaign_snapshot.revision,
            )
            with _support.use_random_stream(stream):
                return self.campaign_party_rest(
                    campaign_id,
                    members,
                    duration_minutes=duration_minutes,
                    rest_type=normalized_rest_type,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                )
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
            raise ValueError("duration_minutes must be an integer")
        minimum_possible_minutes = _support.minimum_rest_minutes(
            normalized_rest_type,
            allows_trance=True,
        )
        if duration_minutes < minimum_possible_minutes:
            raise _support.CombatEngineError(
                f"{normalized_rest_type} requires at least {minimum_possible_minutes} minutes"
            )
        if not isinstance(members, list) or not members:
            raise ValueError("party rest requires at least one member")
        scope = f"campaign-party-rest:{campaign_id}:{resolved_branch_id}:{principal_id}"
        request_payload = {
            "members": _support.deepcopy(members),
            "duration_minutes": duration_minutes,
            "rest_type": normalized_rest_type,
            "branch_id": resolved_branch_id,
        }
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        allowed_member_fields = {
            "character_id",
            "expected_revision",
            "prepared_spell_ids",
            "hit_dice_recovery",
            "rest_activity_minutes",
            "food_and_drink",
            "hit_dice_spends",
            "arcane_recovery",
            "natural_recovery",
            "sorcerous_restoration_points",
            "song_of_rest_source_actor_id",
            "attune_item_id",
            "attunement_prerequisite_confirmed",
            "survival_intake",
        }
        long_rest_fields = {
            "prepared_spell_ids",
            "hit_dice_recovery",
            "food_and_drink",
            "survival_intake",
        }
        short_rest_fields = {
            "hit_dice_spends",
            "arcane_recovery",
            "natural_recovery",
            "sorcerous_restoration_points",
            "song_of_rest_source_actor_id",
            "attune_item_id",
            "attunement_prerequisite_confirmed",
        }
        normalized_members: list[dict[str, Any]] = []
        member_ids: list[str] = []
        for index, raw_member in enumerate(members):
            if not isinstance(raw_member, dict):
                raise ValueError(f"members[{index}] must be an object")
            unknown = sorted(set(raw_member) - allowed_member_fields)
            if unknown:
                raise ValueError(f"members[{index}] has unsupported fields: {unknown}")
            disallowed = (
                short_rest_fields if normalized_rest_type == "long_rest" else long_rest_fields
            )
            wrong_rest_fields = sorted(set(raw_member) & disallowed)
            if wrong_rest_fields:
                raise ValueError(
                    f"members[{index}] fields are invalid for {normalized_rest_type}: "
                    f"{wrong_rest_fields}"
                )
            character_id = str(raw_member.get("character_id") or "").strip()
            if not character_id:
                raise ValueError(f"members[{index}].character_id is required")
            character_revision = raw_member.get("expected_revision")
            if isinstance(character_revision, bool) or not isinstance(character_revision, int):
                raise ValueError(f"members[{index}].expected_revision is required")
            current_member = self.characters.get(character_id)
            if current_member.campaign_id != campaign_id:
                raise ValueError(f"party rest actor is not in this campaign: {character_id}")
            prepared_ids = raw_member.get("prepared_spell_ids")
            if prepared_ids is not None and not isinstance(prepared_ids, list):
                raise ValueError(f"members[{index}].prepared_spell_ids must be an array")
            recovery = raw_member.get("hit_dice_recovery")
            if recovery is not None and not isinstance(recovery, dict):
                raise ValueError(f"members[{index}].hit_dice_recovery must be an object")
            hit_dice_spends = raw_member.get("hit_dice_spends")
            if hit_dice_spends is not None and not isinstance(hit_dice_spends, list):
                raise ValueError(f"members[{index}].hit_dice_spends must be an array")
            arcane_recovery = raw_member.get("arcane_recovery")
            if arcane_recovery is not None and not isinstance(arcane_recovery, dict):
                raise ValueError(f"members[{index}].arcane_recovery must be an object")
            natural_recovery = raw_member.get("natural_recovery")
            if natural_recovery is not None and not isinstance(natural_recovery, dict):
                raise ValueError(f"members[{index}].natural_recovery must be an object")
            sorcerous_restoration_points = raw_member.get("sorcerous_restoration_points")
            if sorcerous_restoration_points is not None and (
                isinstance(sorcerous_restoration_points, bool)
                or not isinstance(sorcerous_restoration_points, int)
            ):
                raise ValueError(
                    f"members[{index}].sorcerous_restoration_points must be an integer"
                )
            song_source_id = (
                str(raw_member.get("song_of_rest_source_actor_id") or "").strip() or None
            )
            attune_item_id = str(raw_member.get("attune_item_id") or "").strip() or None
            attunement_confirmed = raw_member.get("attunement_prerequisite_confirmed")
            if attune_item_id and attunement_confirmed is not True:
                raise _support.NeedsRulingError(
                    "attunement requires explicit DM confirmation that the actor "
                    "satisfies every source-defined prerequisite",
                    missing=("attunement_prerequisite",),
                    ruling_kind="source_or_scene_fact",
                )
            if not attune_item_id and attunement_confirmed is not None:
                raise ValueError("attunement_prerequisite_confirmed requires attune_item_id")
            food_and_drink = raw_member.get("food_and_drink", False)
            if not isinstance(food_and_drink, bool):
                raise ValueError(f"members[{index}].food_and_drink must be a boolean")
            survival_intake = None
            if (
                normalized_rest_type == "long_rest"
                and current_member.sheet.get("edition") == "2014"
            ):
                if "food_and_drink" in raw_member:
                    raise ValueError(
                        "2014 long rests use survival_intake; food_and_drink is not an "
                        "outcome input"
                    )
                raw_intake = raw_member.get("survival_intake")
                if raw_intake is not None and not isinstance(raw_intake, dict):
                    raise ValueError(
                        f"members[{index}].survival_intake must be an object when provided"
                    )
                unknown_intake = set(raw_intake or {}) - {
                    "food_lb",
                    "water_gallons",
                    "hot_weather",
                    "weather_fact",
                    "rations",
                }
                if unknown_intake:
                    raise ValueError(
                        f"members[{index}].survival_intake has unsupported fields: "
                        f"{sorted(unknown_intake)}"
                    )
                ration_entries = (raw_intake or {}).get("rations", [])
                if not isinstance(ration_entries, list) or len(ration_entries) > 20:
                    raise ValueError("survival_intake.rations must be a bounded array")
                if raw_intake is not None:
                    survival_intake = validate_daily_intake(
                        {
                            key: raw_intake[key]
                            for key in ("food_lb", "water_gallons", "hot_weather", "weather_fact")
                            if key in raw_intake
                        }
                    )
                normalized_rations = []
                ration_food_lb = 0.0
                inventory = dict(current_member.sheet.get("inventory") or {})
                for ration_index, ration in enumerate(ration_entries):
                    if not isinstance(ration, dict) or set(ration) != {"item_id", "quantity"}:
                        raise ValueError(
                            f"survival_intake.rations[{ration_index}] requires item_id and quantity"
                        )
                    item_id = str(ration.get("item_id") or "").strip()
                    quantity = ration.get("quantity")
                    if (
                        not item_id
                        or isinstance(quantity, bool)
                        or not isinstance(quantity, int)
                        or quantity < 1
                    ):
                        raise ValueError("survival ration item_id and quantity are invalid")
                    item = next(
                        (
                            entry
                            for entry in inventory.get("items", [])
                            if str(entry.get("id") or "") == item_id
                        ),
                        None,
                    )
                    source_key = str((item or {}).get("source_key") or "")
                    canonical_source = source_key.rsplit(":", 1)[-1]
                    if (
                        item is None
                        or item.get("name", "").strip().casefold() not in {"ration", "rations"}
                        or canonical_source != "dnd5e.content.srd2014.item.rations"
                        or quantity > int(item.get("quantity", 0) or 0)
                    ):
                        raise ValueError(
                            "survival rations must reference an available bundled 2014 Rations item"
                        )
                    normalized_rations.append(
                        {
                            "item_id": item_id,
                            "source_key": source_key,
                            "quantity": quantity,
                        }
                    )
                    ration_food_lb += quantity
                if survival_intake is not None:
                    survival_intake["food_lb"] += ration_food_lb
                    survival_intake["consumed_items"] = normalized_rations
                    survival_intake = validate_daily_intake(survival_intake)
            rest_activities = _support.validate_rest_activity_minutes(
                raw_member.get("rest_activity_minutes")
            )
            derived_rest_timing = _support.validate_rest_schedule(
                rest_type=normalized_rest_type,
                duration_minutes=duration_minutes,
                rest_activity_minutes=rest_activities,
                allows_trance=(
                    _support.allows_trance_rest(current_member.sheet)
                    if normalized_rest_type == "long_rest"
                    else False
                ),
            )
            normalized_member = {
                "character_id": character_id,
                "expected_revision": character_revision,
                "rest_activity_minutes": rest_activities,
                "derived_rest_timing": derived_rest_timing,
            }
            if normalized_rest_type == "long_rest":
                normalized_member.update(
                    {
                        "prepared_spell_ids": prepared_ids,
                        "hit_dice_recovery": recovery,
                        "food_and_drink": food_and_drink,
                        "survival_intake": survival_intake,
                    }
                )
            else:
                normalized_member.update(
                    {
                        "hit_dice_spends": list(hit_dice_spends or []),
                        "arcane_recovery": _support.deepcopy(arcane_recovery or {}),
                        "natural_recovery": _support.deepcopy(natural_recovery or {}),
                        "sorcerous_restoration_points": (sorcerous_restoration_points),
                        "song_of_rest_source_actor_id": song_source_id,
                        "attune_item_id": attune_item_id,
                        "attunement_prerequisite_confirmed": attunement_confirmed,
                    }
                )
            normalized_members.append(normalized_member)
            member_ids.append(character_id)
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("party rest member ids must be unique")
        if normalized_rest_type == "short_rest":
            song_source_ids = {
                str(item["song_of_rest_source_actor_id"])
                for item in normalized_members
                if item["song_of_rest_source_actor_id"] is not None
            }
            if song_source_ids - set(member_ids):
                raise _support.CombatEngineError(
                    "every Song of Rest source must participate in the same party rest"
                )
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            next_state,
            operation="another rest",
        )
        if bool(dict(next_state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("rest is not allowed while combat is active")
        started_elapsed_ticks = int(next_state["game_time"]["elapsed_ticks"])
        next_state, time_transition = self.advance_state_game_time(
            next_state,
            period="minute",
            count=duration_minutes,
        )
        completed_clock = time_transition["world_time_after"]
        completed_elapsed_ticks = int(time_transition["after"]["elapsed_ticks"])
        elapsed_ticks = int(time_transition["elapsed_ticks"])
        completed_game_day = _support.rules_day_from_ticks(completed_elapsed_ticks)
        all_characters = {item.id: item for item in self.characters.list(campaign_id=campaign_id)}
        survival_state = next_state["survival"]
        survival_day_index = started_elapsed_ticks // _support.game_time_ticks("day")
        daily_intakes = survival_state["daily_intakes"]
        for member in normalized_members:
            current = all_characters.get(member["character_id"])
            if current is None:
                raise ValueError(
                    f"party rest actor is not in this campaign: {member['character_id']}"
                )
            if current.revision != member["expected_revision"]:
                raise ValueError(f"character revision conflict: {current.id}")
            intake = member.get("survival_intake")
            if normalized_rest_type == "long_rest" and intake is not None:
                if survival_day_index <= survival_state["last_settled_day"]:
                    raise _support.CombatEngineError(
                        "survival intake cannot be added to an already settled campaign day"
                    )
                day_entry = daily_intakes.setdefault(str(survival_day_index), {})
                merged = merge_daily_intake(day_entry.get(current.id), intake)
                day_entry[current.id] = merged
                member["survival_intake"] = merged
                required_water = 2.0 if merged["hot_weather"] else 1.0
                member["food_and_drink"] = (
                    merged["food_lb"] >= 1 and merged["water_gallons"] >= required_water
                )
                sheet = current.sheet
                for ration in intake["consumed_items"]:
                    sheet, _ = _support.remove_inventory_item(
                        sheet,
                        ration["item_id"],
                        ration["quantity"],
                    )
                if sheet != current.sheet:
                    current = _support.replace(current, sheet=sheet)
                    all_characters[current.id] = current
            _support.record_rest_completion(
                current.sheet,
                rest_type=normalized_rest_type,
                started_elapsed_ticks=started_elapsed_ticks,
                completed_elapsed_ticks=completed_elapsed_ticks,
                rest_activity_minutes=member["rest_activity_minutes"],
            )
            if normalized_rest_type == "long_rest" and member["prepared_spell_ids"] is not None:
                preparation_hydration = self.hydrate_class_prepared_spell_cards(
                    campaign_id,
                    current.sheet,
                    spell_ids=member["prepared_spell_ids"],
                    branch_id=resolved_branch_id,
                )
                _support.replace_prepared_spells(
                    preparation_hydration["sheet"],
                    spell_ids=member["prepared_spell_ids"],
                    event="long_rest",
                )
            if normalized_rest_type == "short_rest":
                _support.validate_initial_rest_hit_dice_requests(
                    current.sheet,
                    member["hit_dice_spends"],
                )
                _support.validate_arcane_recovery_choice(
                    current.sheet,
                    member["arcane_recovery"],
                    game_day=completed_game_day,
                )
                _support.validate_natural_recovery_choice(
                    current.sheet,
                    member["natural_recovery"],
                    rest_activity_minutes=member["rest_activity_minutes"],
                )
                _support.validate_sorcerous_restoration_choice(
                    current.sheet,
                    member["sorcerous_restoration_points"],
                )
                song_source_id = member["song_of_rest_source_actor_id"]
                if song_source_id is not None:
                    _support.validate_song_of_rest_source(all_characters[str(song_source_id)].sheet)
                if member["attune_item_id"] is not None:
                    _support.attune_inventory_item(
                        current.sheet,
                        str(member["attune_item_id"]),
                    )

        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": elapsed_ticks},
        )
        next_state = world_duration["state"]
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]

        member_by_id = {item["character_id"]: item for item in normalized_members}
        updates: list[_support.CharacterStateUpdate] = []
        recovered: dict[str, Any] = {}
        preparations: dict[str, Any] = {}
        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        rule_receipts: list[dict[str, Any]] = []
        rule_context = self.effective_rule_context(campaign_id)
        for current in all_characters.values():
            sheet = current.sheet
            actor_advanced: list[str] = []
            actor_expired: list[str] = []
            duration = _support.advance_elapsed_effect_durations(
                sheet,
                elapsed_ticks=elapsed_ticks,
            )
            round_duration = _support.advance_effect_durations(
                duration["sheet"],
                period="round",
                amount=int(time_transition["elapsed_ticks"]),
                advance_breathing=False,
            )
            extension = _support.apply_rule_event(
                round_duration["sheet"],
                "duration.advance",
                _support.context_with_facts(
                    rule_context,
                    actor_id=current.id,
                    period="minute",
                    amount=duration_minutes,
                ),
            )
            if extension.status != "committed":
                raise _support.CombatEngineError(
                    f"party rest duration for {current.id} requires an unresolved rule choice"
                )
            sheet = extension.sheet
            actor_advanced.extend(duration["advanced"])
            actor_expired.extend(duration["expired"])
            actor_advanced.extend(round_duration["advanced"])
            actor_expired.extend(round_duration["expired"])
            rule_receipts.extend(extension.receipts)
            member = member_by_id.get(current.id)
            if member is not None:
                rest_rules = self.effective_rule_context(
                    campaign_id,
                    facts={
                        "actor_id": current.id,
                        "rest_type": normalized_rest_type,
                    },
                )
                rest_arguments: dict[str, Any] = {
                    "rest_type": normalized_rest_type,
                    "rest_activity_minutes": member["rest_activity_minutes"],
                    "rules": rest_rules,
                    "game_day": completed_game_day,
                }
                song_source_sheet = None
                if normalized_rest_type == "long_rest":
                    rest_arguments.update(
                        {
                            "hit_dice_recovery": member["hit_dice_recovery"],
                            "food_and_drink": member["food_and_drink"],
                        }
                    )
                else:
                    song_source_id = member["song_of_rest_source_actor_id"]
                    song_source_sheet = (
                        all_characters[str(song_source_id)].sheet
                        if song_source_id is not None
                        else None
                    )
                    rest_arguments.update(
                        {
                            "hit_dice_spends": member["hit_dice_spends"],
                            "arcane_recovery": member["arcane_recovery"],
                            "natural_recovery": member["natural_recovery"],
                            "sorcerous_restoration_points": member["sorcerous_restoration_points"],
                            "song_of_rest_source_sheet": song_source_sheet,
                        }
                    )
                applied = _support.apply_rest(
                    sheet,
                    **rest_arguments,
                    rng=_support.active_random_stream(),
                )
                if applied.get("status") != "committed":
                    raise _support.CombatEngineError(
                        f"party rest for {current.id} requires an unresolved rule choice"
                    )
                if (
                    normalized_rest_type == "long_rest"
                    and member.get("survival_intake") is not None
                    and member["food_and_drink"]
                ):
                    survival_actor = next_state["survival"]["actors"].setdefault(
                        current.id,
                        {
                            "food_deprivation_days": 0.0,
                            "starvation_exhaustion_earned": 0,
                            "deprivation_exhaustion_levels": 0,
                            "recovery_locked": False,
                        },
                    )
                    exhaustion_recovered = max(
                        0,
                        int(sheet["combat"]["exhaustion"])
                        - int(applied["sheet"]["combat"]["exhaustion"]),
                    )
                    survival_actor["deprivation_exhaustion_levels"] = max(
                        0,
                        int(survival_actor["deprivation_exhaustion_levels"]) - exhaustion_recovered,
                    )
                    survival_actor["recovery_locked"] = False
                if normalized_rest_type == "short_rest" and member["attune_item_id"] is not None:
                    applied["sheet"] = _support.attune_inventory_item(
                        applied["sheet"],
                        str(member["attune_item_id"]),
                    )
                    applied["attuned_item_id"] = str(member["attune_item_id"])
                sheet = _support.record_rest_completion(
                    applied["sheet"],
                    rest_type=normalized_rest_type,
                    started_elapsed_ticks=started_elapsed_ticks,
                    completed_elapsed_ticks=completed_elapsed_ticks,
                    rest_activity_minutes=member["rest_activity_minutes"],
                    hit_dice_spent_count=len(applied.get("hit_dice_rolls") or []),
                    expected_character_revision=current.revision + 1,
                    song_of_rest_die_sides=(
                        _support.validate_song_of_rest_source(song_source_sheet)
                        if normalized_rest_type == "short_rest" and song_source_sheet is not None
                        else None
                    ),
                    song_of_rest_used=applied.get("song_of_rest") is not None,
                )
                preparation_result = None
                if normalized_rest_type == "long_rest" and member["prepared_spell_ids"] is not None:
                    preparation_hydration = self.hydrate_class_prepared_spell_cards(
                        campaign_id,
                        sheet,
                        spell_ids=member["prepared_spell_ids"],
                        branch_id=resolved_branch_id,
                    )
                    preparation_result = _support.replace_prepared_spells(
                        preparation_hydration["sheet"],
                        spell_ids=member["prepared_spell_ids"],
                        event="long_rest",
                    )
                    preparation_result["materialized_spell_ids"] = preparation_hydration[
                        "materialized_spell_ids"
                    ]
                    sheet = preparation_result["sheet"]
                    preparations[current.id] = {
                        key: value for key, value in preparation_result.items() if key != "sheet"
                    }
                recovered[current.id] = {
                    key: value
                    for key, value in applied.items()
                    if key not in {"sheet", "rule_receipts"}
                }
                if normalized_rest_type == "short_rest":
                    recovered[current.id]["short_rest_hit_dice"] = _support.deepcopy(
                        dict(sheet.get("combat") or {}).get("short_rest_hit_dice")
                    )
                rule_receipts.extend(applied.get("rule_receipts") or [])
                if normalized_rest_type == "long_rest":
                    rule_receipts.extend(
                        _support.core_receipts(
                            rest_rules,
                            ["dnd5e.core.rest.long_rest_timing"],
                            "party.rest.long_rest",
                        )
                    )
                if preparation_result is not None:
                    rule_receipts.extend(
                        _support.core_receipts(
                            rest_rules,
                            ["dnd5e.core.spell.preparation"],
                            "spell.prepare.long_rest",
                        )
                    )
            if sheet != current.sheet:
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=current.id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(current.notes),
                        expected_revision=current.revision,
                    )
                )
            if actor_advanced:
                advanced[current.id] = list(dict.fromkeys(actor_advanced))
            if actor_expired:
                expired[current.id] = list(dict.fromkeys(actor_expired))
        stream = _support.active_random_stream()
        next_state, updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, updates, {}
        )
        updates = self.reconcile_completed_item_attunements(
            campaign,
            next_state,
            updates,
            {
                member["character_id"]: str(member["attune_item_id"])
                for member in normalized_members
                if normalized_rest_type == "short_rest"
                and member["attune_item_id"] is not None
                and all_characters[member["character_id"]].sheet.get("edition") == "2014"
            },
        )
        base_response = {
            "status": "committed",
            "rest_type": normalized_rest_type,
            "duration_minutes": duration_minutes,
            "member_ids": member_ids,
            "game_time": time_transition["after"],
            "world_time": completed_clock,
            "recovered": recovered,
            "preparations": preparations,
            "advanced": advanced,
            "expired": expired,
            "world_advanced": list(dict.fromkeys(world_advanced)),
            "world_expired": list(dict.fromkeys(world_expired)),
            "campaign_revision": campaign.revision + 1,
            "rule_receipts": rule_receipts,
            "ruleset_fingerprint": rule_context.fingerprint,
            **(
                {"random_stream_receipt": stream.receipt()}
                if stream is not None and stream.draw_count > 0
                else {}
            ),
        }

        return self.commit_campaign_state(
            campaign,
            next_state,
            operation=f"campaign.party.rest.{normalized_rest_type}",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields=base_response,
            character_updates=updates,
            rule_receipts=rule_receipts,
            expected_campaign_revision=campaign.revision,
        )

    def campaign_short_rest_hit_die(
        self,
        campaign_id: str,
        character_id: str,
        decision: str,
        rest_completed_elapsed_ticks: int,
        hit_die_key: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_character_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one spend-or-stop choice from a completed 2014 short rest."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if isinstance(expected_character_revision, bool) or not isinstance(
            expected_character_revision, int
        ):
            raise ValueError("expected_character_revision is required")
        if isinstance(rest_completed_elapsed_ticks, bool) or not isinstance(
            rest_completed_elapsed_ticks, int
        ):
            raise ValueError("rest_completed_elapsed_ticks must be an integer")
        normalized_decision = str(decision).strip().casefold().replace("-", "_")
        if normalized_decision not in {"spend", "stop"}:
            raise ValueError("decision must be spend or stop")
        normalized_key = str(hit_die_key or "").strip() or None
        if normalized_decision == "spend" and normalized_key is None:
            raise ValueError("hit_die_key is required when decision is spend")
        if normalized_decision == "stop" and normalized_key is not None:
            raise ValueError("hit_die_key must be omitted when decision is stop")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        request_payload = {
            "character_id": str(character_id),
            "decision": normalized_decision,
            "rest_completed_elapsed_ticks": rest_completed_elapsed_ticks,
            "hit_die_key": normalized_key,
            "expected_character_revision": expected_character_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"campaign-short-rest-hit-die:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        current = self.characters.get(str(character_id))
        if current.campaign_id != campaign_id:
            raise ValueError("short-rest Hit Die actor is not in this campaign")
        if current.revision != expected_character_revision:
            raise ValueError(
                "character revision conflict: "
                f"expected {expected_character_revision}, found {current.revision}"
            )
        choice_window = dict(current.sheet.get("combat") or {}).get("short_rest_hit_dice")
        if not isinstance(choice_window, dict):
            raise _support.CombatEngineError("no sequential Hit Die choice is open")
        if int(choice_window.get("expected_character_revision", -1)) != current.revision:
            raise _support.CombatEngineError(
                "the short-rest Hit Die choice was invalidated by a later character change"
            )
        state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        if bool(dict(state.get("combat") or {}).get("active")):
            raise _support.CombatEngineError("short-rest Hit Dice cannot be spent during combat")
        if bool(dict(state.get("chase") or {}).get("active")):
            raise _support.CombatEngineError("short-rest Hit Dice cannot be spent during a chase")
        current_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0) or 0)
        if current_ticks != rest_completed_elapsed_ticks:
            raise _support.CombatEngineError(
                "the short-rest Hit Die choice expired because campaign time advanced"
            )
        rest_rules = self.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": current.id,
                "rest_type": "short_rest",
            },
        )
        inherited_stream = _support.active_random_stream()
        if inherited_stream is not None:
            random_state = _support.validate_random_stream_state(
                dict(campaign.state or {}).get("random_stream")
                or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
            )
            if (
                inherited_stream.campaign_id != campaign_id
                or (
                    inherited_stream.campaign_revision is not None
                    and inherited_stream.campaign_revision != campaign.revision
                )
                or inherited_stream.seed != random_state["seed"]
                or inherited_stream.start_position != random_state["position"]
            ):
                raise ValueError(
                    "campaign random snapshot conflict: short-rest Hit Die resolution "
                    "requires the same campaign revision and random-stream position that "
                    "opened the request"
                )
        stream = inherited_stream or _support.CampaignRandomStream.from_campaign_state(
            campaign_id,
            campaign.state,
            operation="campaign_change",
            idempotency_key=str(idempotency_key),
            campaign_revision=campaign.revision,
        )
        context_manager = (
            _support.nullcontext(stream)
            if inherited_stream is not None
            else _support.use_random_stream(stream)
        )
        with context_manager:
            applied = _support.apply_short_rest_hit_die_choice(
                current.sheet,
                decision=normalized_decision,
                hit_die_key=normalized_key,
                rest_completed_elapsed_ticks=rest_completed_elapsed_ticks,
                rules=rest_rules,
            )
            continued_window = dict(applied["sheet"].get("combat") or {}).get("short_rest_hit_dice")
            if isinstance(continued_window, dict):
                continued_window["expected_character_revision"] = current.revision + 1
            next_sheet = _support.validate_character_sheet(applied["sheet"])
            update = _support.CharacterStateUpdate(
                character_id=current.id,
                sheet=next_sheet,
                notes=_support.validate_character_notes(current.notes),
                expected_revision=current.revision,
            )
            result = {key: value for key, value in applied.items() if key != "sheet"}
            response_fields = {
                "status": str(result["status"]),
                "result": result,
                "character": self.character_view(
                    _support.replace(current, sheet=next_sheet, revision=current.revision + 1)
                ),
                "rule_receipts": list(applied.get("rule_receipts") or []),
                "ruleset_fingerprint": rest_rules.fingerprint,
            }
            return self.commit_campaign_state(
                campaign,
                None,
                operation="campaign.party.rest.short_rest.hit_die",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=str(idempotency_key),
                scope=scope,
                payload=request_payload,
                response_fields=response_fields,
                character_updates=[update],
                rule_receipts=list(applied.get("rule_receipts") or []),
                expected_campaign_revision=campaign.revision,
            )

    def settle_campaign_randomness(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        branch_id: str | None,
        expected_campaign_revision: int | None,
        idempotency_key: str | None,
        operation: str,
        payload: dict[str, Any],
        resolver: Any,
    ) -> dict[str, Any]:
        self.access.require_campaign(campaign_id, principal_id)
        if expected_campaign_revision is None or not idempotency_key:
            raise ValueError(
                "expected_campaign_revision and idempotency_key are required for random resolution"
            )
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        scope = f"campaign-random:{campaign_id}:{resolved_branch_id}:{principal_id}"
        request_payload = {"operation": operation, **_support.deepcopy(payload)}
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_campaign_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_campaign_revision}, found {campaign.revision}"
            )
        inherited_stream = _support.active_random_stream()
        if inherited_stream is not None and inherited_stream.campaign_id != campaign_id:
            raise ValueError("active random stream belongs to another campaign")
        stream = inherited_stream or _support.CampaignRandomStream.from_campaign_state(
            campaign_id,
            campaign.state,
            operation=operation,
            idempotency_key=idempotency_key,
        )
        context_manager = (
            _support.nullcontext(stream) if inherited_stream else _support.use_random_stream(stream)
        )
        with context_manager:
            result = resolver()
            if stream.draw_count == 0:
                raise ValueError("random resolution must consume at least one random draw")
            resolution_id = (
                "resolution-"
                + _support.hashlib.sha256(
                    (f"{campaign_id}:{resolved_branch_id}:{operation}:{idempotency_key}").encode(
                        "utf-8"
                    )
                ).hexdigest()[:32]
            )
            membership = self.access.require_campaign(campaign_id, principal_id)
            audience = (
                {"scope": "dm", "actor_refs": [], "disclosure": "hidden"}
                if membership.role in _support.CAMPAIGN_DM_ROLES
                else {"scope": "principal", "actor_refs": [], "disclosure": "private"}
            )
            receipt = stream.receipt()
            response = {
                **dict(result),
                "resolution_id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "campaign_revision": campaign.revision + 1,
                "random_stream_receipt": receipt,
            }
            next_state = _support.deepcopy(dict(campaign.state or {}))
            next_state["resolution_presentation_log"] = [
                *list(next_state.get("resolution_presentation_log") or []),
                {
                    "id": resolution_id,
                    "thread_id": resolution_id,
                    "event_sequence": 1,
                    "operation": operation,
                    "status": "settled",
                    "audience": audience,
                    "principal_id": principal_id if audience["scope"] == "principal" else None,
                    "branch_id": resolved_branch_id,
                    "campaign_revision": campaign.revision + 1,
                    "result": _support.deepcopy(dict(result)),
                    "random_stream_receipt": receipt,
                },
            ][-200:]
            _support.StateMutationService(self.storage.database).replace(
                campaign_id,
                campaign_state=_support.validate_party_state(next_state),
                expected_campaign_revision=expected_campaign_revision,
                operation=operation,
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

    def campaign_core_relock(
        self,
        campaign_id: str,
        expected_core_fingerprint: str,
        reason: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        expected_head_snapshot_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly adopt the current built-in Core after a checkpointed runtime upgrade."""

        from sagasmith_core.rule_profiles import RuleProfileMaintenance

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if not expected_head_snapshot_id:
            raise ValueError("expected_head_snapshot_id is required for a Core relock")
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("reason is required for a Core relock")
        if len(normalized_reason) > 500:
            raise ValueError("Core relock reason exceeds 500 characters")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "expected_core_fingerprint": expected_core_fingerprint,
            "reason": normalized_reason,
            "branch_id": resolved_branch_id,
            "expected_head_snapshot_id": expected_head_snapshot_id,
        }
        scope = f"campaign-core-relock:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        branch = self.branches.current(campaign_id)
        if branch.id != resolved_branch_id or branch.head_snapshot_id != expected_head_snapshot_id:
            raise ValueError("current branch head changed before Core relock")
        profile = self.rule_profiles.get(campaign_id)
        if profile is None:
            raise _support.RulePackError("campaign has no rule profile to relock")
        options = dict(profile.options or {})
        previous = dict(options.get("_core_rule_pack_lock") or {})
        if previous.get("fingerprint") != expected_core_fingerprint:
            raise ValueError("expected_core_fingerprint does not match the campaign lock")
        latest = _support.get_core_rule_pack(profile.edition)
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if (
            previous.get("fingerprint") == latest.fingerprint
            and options.get("_implementation_identity") == _support.implementation_identity()
        ):
            return self.remember_idempotent(
                scope,
                idempotency_key,
                payload,
                {
                    "status": "current",
                    "reason": normalized_reason,
                    "previous_core_pack": previous,
                    "core_pack": {
                        "id": latest.id,
                        "version": latest.version,
                        "edition": latest.edition,
                        "fingerprint": latest.fingerprint,
                    },
                    "profile": _support.asdict(profile),
                    "branch_id": resolved_branch_id,
                    "checkpoint_snapshot_id": expected_head_snapshot_id,
                    "campaign_revision": campaign.revision,
                    "mutation_applied": False,
                },
                campaign_id=campaign_id,
            )
        user_options = {
            key: value for key, value in options.items() if key != "_core_rule_pack_lock"
        }

        def relock_response(result: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "relocked",
                "reason": normalized_reason,
                "previous_core_pack": previous,
                "core_pack": {
                    "id": latest.id,
                    "version": latest.version,
                    "edition": latest.edition,
                    "fingerprint": latest.fingerprint,
                },
                "profile": _support.asdict(result["profile"]),
                "branch_id": resolved_branch_id,
                "checkpoint_snapshot_id": expected_head_snapshot_id,
                "campaign_revision": result["campaign_revision"],
                "mutation_applied": True,
            }

        self.rule_profiles.set(
            campaign_id,
            edition=profile.edition,
            locale=profile.locale,
            publications=list(profile.publications),
            options=self.profile_options_with_core_lock(profile.edition, user_options),
            expected_campaign_revision=expected_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=relock_response,
            ),
            maintenance=RuleProfileMaintenance(
                lock_id=_support.COMBAT_MUTATION_LOCK_ID,
                option_keys=frozenset({"_core_rule_pack_lock", "_implementation_identity"}),
                branch_id=resolved_branch_id,
                head_snapshot_id=expected_head_snapshot_id,
            ),
        )
        committed = self.idempotency.lookup(scope, str(idempotency_key), payload)
        assert committed is not None and committed.response is not None
        return committed.response

    def campaign_query(
        self,
        view: Literal["list", "get", "party", "resume", "binding"] = "list",
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        offset: Annotated[int, _support.Field(ge=0, le=100_000)] = 0,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read campaign pages, party state, or one complete resume bundle.

        view=list needs no campaign id. All other views require payload.campaign_id.
        With no campaign, list or campaign_create first. Agent startup/resume uses
        payload={campaign_id, detail:"summary"} to avoid returning combat logs and
        full state documents. detail defaults to full for existing UI clients.
        Follow summary read_next for omitted detail; omission never means absent.
        Use resume once when reconnecting/restoring, then reuse write receipts.
        """
        data = self.facade_payload(payload)
        detail = data.get("detail", "full")
        if detail not in {"full", "summary"}:
            raise ValueError("payload.detail must be full or summary")
        if view == "binding":
            data = self.facade_payload(payload)
            campaign_id = self.required(data, "campaign_id")
            result = self.authoritative_host_context_binding(
                campaign_id,
                principal_id,
            )
            if result is None:
                raise LookupError("campaign has no active branch context")
        elif view == "resume":
            data = self.facade_payload(payload)
            campaign_id = self.required(data, "campaign_id")
            membership = self.access.require_campaign(campaign_id, principal_id)
            branch_values = self.branch_list(campaign_id, principal_id)
            current_branch = next(
                (item for item in branch_values if item.get("is_current")),
                None,
            )
            manifest_result = None
            if membership.role in _support.CAMPAIGN_DM_ROLES:
                try:
                    manifest_result = self.playthrough_manifest(
                        campaign_id,
                        "get",
                        principal_id=principal_id,
                    )
                except LookupError:
                    manifest_result = None
            context = self.continuity_context(
                campaign_id,
                query=str(data.get("query") or ""),
                actor_id=data.get("actor_id"),
                scope_id=str(data.get("scope_id") or "party"),
                audience=str(data.get("audience") or "dm"),
                branch_id=(str(current_branch["id"]) if current_branch is not None else None),
                limit=int(data.get("limit", 8)),
                budget_chars=int(
                    data.get(
                        "budget_chars",
                        4_000 if detail == "summary" else 12_000,
                    )
                ),
                related_refs=data.get("related_refs"),
                principal_id=principal_id,
            )
            result = {
                "campaign": self.campaign_get(campaign_id, principal_id),
                "branches": branch_values,
                "current_branch": current_branch,
                "manifest": manifest_result,
                "current_scene": self.module_current(
                    campaign_id,
                    scope_id=str(data.get("scope_id") or "party"),
                    principal_id=principal_id,
                ),
                "continuity": context,
                "host_context_binding": context["host_context_binding"],
                "resume_invariants": {
                    "discard_pre_restore_context": True,
                    "context_receipt_revision": context["context_receipt"]["campaign_revision"],
                    "reuse_bound_exposure_after_restore": True,
                    "refresh_tools_after_phase_or_checkout_change": True,
                },
            }
        elif view == "get":
            result = self.campaign_get(self.required(data, "campaign_id"), principal_id)
        elif view == "party":
            result = self.party_show(self.required(data, "campaign_id"), principal_id)
        else:
            result = self.campaign_list(data.get("status"), principal_id)
            result = sorted(
                result, key=lambda item: (str(item.get("name", "")), str(item.get("id", "")))
            )
            result, page = _support._bounded_page(
                result,
                scope=f"campaign_query:list:{principal_id}:{str(data.get('status') or '')}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=offset or data.get("offset", 0),
            )
            return self.facade_result(view, result, page=page)
        if detail == "summary" and view in {"get", "resume"}:

            def identity(record: dict[str, Any]) -> dict[str, Any]:
                keys = {
                    "id",
                    "campaign_id",
                    "module_id",
                    "scene_id",
                    "scope_id",
                    "name",
                    "title",
                    "slug",
                    "system_id",
                    "status",
                    "revision",
                    "state_version",
                    "effective_game_phase",
                    "base_snapshot_id",
                    "head_snapshot_id",
                    "is_current",
                    "current_location_key",
                    "progress",
                }
                return {
                    key: value[:512] if isinstance(value, str) else value
                    for key, value in record.items()
                    if key in keys and isinstance(value, (str, int, float, bool, type(None)))
                }

            campaign_value = result["campaign"] if view == "resume" else result
            brief = identity(campaign_value)
            state = campaign_value.get("state") or {}
            combat = state.get("combat") or {}
            brief["combat"] = {
                key: combat[key]
                for key in ("id", "active", "scene_id", "round", "turn_index")
                if key in combat
            }
            brief["detail"] = "summary"
            if view == "get":
                result = brief
            else:
                result["campaign"] = brief
                result["branch_count"] = len(result["branches"])
                result["branches"] = [identity(item) for item in result["branches"][:10]]
                if isinstance(result.get("manifest"), dict):
                    result["manifest"] = {"available": True, **identity(result["manifest"])}
                scene = result.get("current_scene")
                if isinstance(scene, dict):
                    result["current_scene"] = {
                        **identity(scene),
                        **{
                            key: identity(value)
                            for key, value in scene.items()
                            if key in {"module", "scene", "progress"} and isinstance(value, dict)
                        },
                    }
            result["read_next"] = {
                "campaign_state": (
                    "campaign_query(view=get, payload={campaign_id, detail:full}) "
                    "only when full state is needed"
                ),
                "combat": "combat_query(view=status) for an active encounter",
                "actors": "character_query(view=list/batch) with bounded relevant actor IDs",
                "scene": (
                    "module_query(view=current/scene/progress); "
                    "current is a scope pointer, not a progress search"
                ),
                "branches": "branch_query for remaining branches",
            }
            result["omitted_detail"] = [
                "campaign.state",
                "campaign.settings",
                "full manifest",
                "full scene",
                "branches after first 10",
            ]
        return self.facade_result(view, result)

    def environment_change(
        self,
        campaign_id: str,
        payload: dict[str, Any],
        action: Literal["water"] = "water",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record a DM-reviewed 2014 water transition without moving an actor.

        payload={source_ref,source_excerpt,reason,actors?:[{actor_id,underwater,
        fully_immersed}],objects?:[{scene_id,object_id,fully_immersed}]}.
        Copy the exact active module source. All booleans are explicit; fully
        immersed actors must be underwater. Objects require a reviewed profile;
        an initial source-object profile may also declare fully_immersed.
        This records environmental facts only. Set breathing separately from
        the actor's real ability to breathe; immersion alone does not decide it.
        Use this operation on entering/leaving water, including during combat.
        Pending combat choices must finish before changing their environment.
        """
        from .environment import change_water_environment

        return change_water_environment(
            self,
            campaign_id,
            payload,
            principal_id=principal_id,
            expected_revision=expected_revision,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
        )

    def campaign_change(
        self,
        campaign_id: str,
        payload: dict[str, Any],
        action: Literal[
            "update",
            "clock_set",
            "clock_advance",
            "party_rest",
            "party_travel",
            "short_rest_hit_die",
            "stable_recovery",
            "effect_add",
            "effect_remove",
            "advancement_configure",
            "experience_award",
            "loot_acquire",
            "currency_spend",
            "item_spend",
            "consumable_use",
        ] = "update",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Update campaign state, advancement, clock, travel, or campaign-space effects.

        party_rest payload: {members:[{character_id, expected_revision}],
        rest_type:"long_rest"|"short_rest", duration_minutes}. Each member uses
        its actor revision; top-level expected_revision is the campaign revision.
        Optional member rest choices are documented in runtime-workflows.md.

        party_travel payload: {trip:{travel_id, pace, distance_miles, route_fact,
        participants:[{character_id, expected_revision, survival_intake}]}}.
        In 2014, spend at most one initial Hit Die per member, then inspect its
        roll before deciding on another. short_rest_hit_die payload requires
        {character_id,expected_character_revision,decision:"spend"|"stop",
        rest_completed_elapsed_ticks,hit_die_key?}. Use the completed rest's
        actual elapsed ticks and the actor's exact Hit Die pool key; hit_die_key
        is required for spend and omitted for stop. Top-level expected_revision
        is the current campaign revision. Each new decision gets a new key.
        For live prepared-spell changes, put prepared_spell_ids on that member
        in a legal long_rest; returning to Lobby does not reopen initial setup.
        clock_advance payload={period, count?, expected_elapsed_ticks?}. period is
        minute/hour/day/round/encounter; expected_elapsed_ticks is the resulting
        clock, required for minute/hour/day (10 ticks/minute), not the old clock.
        """
        action_contracts: dict[str, tuple[set[str], tuple[str, ...]]] = {
            "update": (
                {"name", "status", "description", "settings", "state"},
                (),
            ),
            "clock_set": (
                {"day", "hour", "minute", "label"},
                ("day",),
            ),
            "clock_advance": (
                {
                    "period",
                    "count",
                    "expected_elapsed_ticks",
                },
                ("period",),
            ),
            "party_rest": (
                {"members", "duration_minutes", "rest_type"},
                ("members",),
            ),
            "party_travel": ({"trip"}, ("trip",)),
            "short_rest_hit_die": (
                {
                    "character_id",
                    "expected_character_revision",
                    "decision",
                    "hit_die_key",
                    "rest_completed_elapsed_ticks",
                },
                (
                    "character_id",
                    "expected_character_revision",
                    "decision",
                    "rest_completed_elapsed_ticks",
                ),
            ),
            "stable_recovery": (
                {"members", "resting_members"},
                ("members",),
            ),
            "effect_add": ({"effect"}, ("effect",)),
            "effect_remove": ({"effect_id", "reason"}, ("effect_id",)),
            "advancement_configure": ({"mode"}, ("mode",)),
            "experience_award": (
                {"awards", "reason", "source_ref"},
                ("awards", "reason", "source_ref"),
            ),
            "loot_acquire": (
                {"acquisition_id", "coins", "items", "reason", "source_ref"},
                ("acquisition_id", "reason", "source_ref"),
            ),
            "currency_spend": (
                {"spend_id", "coins", "reason", "source_ref", "rule_ref"},
                ("spend_id", "coins", "reason", "source_ref", "rule_ref"),
            ),
            "item_spend": (
                {
                    "spend_id",
                    "item_id",
                    "quantity",
                    "reason",
                    "source_ref",
                    "character_id",
                    "expected_character_revision",
                },
                ("spend_id", "item_id", "quantity", "reason", "source_ref"),
            ),
            "consumable_use": (
                {
                    "use_id",
                    "item_id",
                    "target_character_id",
                    "expected_character_revision",
                    "reason",
                },
                (
                    "use_id",
                    "item_id",
                    "target_character_id",
                    "expected_character_revision",
                    "reason",
                ),
            ),
        }
        if action not in action_contracts:
            raise ValueError(f"unsupported campaign_change action: {action}")
        allowed_fields, required_fields = action_contracts[action]
        data = self.facade_payload(payload)
        if action == "clock_set":
            result = self.campaign_clock_set_impl(
                campaign_id,
                self.required(data, "day"),
                data.get("hour", 0),
                data.get("minute", 0),
                data.get("label", ""),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "clock_advance":
            result = self.campaign_advance_effects_impl(
                campaign_id,
                self.required(data, "period"),
                data.get("count", 1),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
                data.get("expected_elapsed_ticks"),
            )
        elif action == "party_rest":
            result = self.campaign_party_rest(
                campaign_id=campaign_id,
                members=self.required(data, "members"),
                duration_minutes=data.get(
                    "duration_minutes",
                    _support.LONG_REST_MINIMUM_MINUTES,
                ),
                rest_type=data.get("rest_type", "long_rest"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        elif action == "party_travel":
            result = self.campaign_party_travel(
                campaign_id=campaign_id,
                trip=self.required(data, "trip"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        elif action == "short_rest_hit_die":
            result = self.campaign_short_rest_hit_die(
                campaign_id=campaign_id,
                character_id=self.required(data, "character_id"),
                decision=self.required(data, "decision"),
                rest_completed_elapsed_ticks=self.required(
                    data,
                    "rest_completed_elapsed_ticks",
                ),
                hit_die_key=data.get("hit_die_key"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                expected_character_revision=self.required(
                    data,
                    "expected_character_revision",
                ),
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        elif action == "stable_recovery":
            result = self.campaign_stable_recovery(
                campaign_id=campaign_id,
                members=self.required(data, "members"),
                resting_members=data.get("resting_members"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        elif action in {"effect_add", "effect_remove"}:
            result = self.campaign_world_effect_change(
                campaign_id,
                action,
                data,
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "advancement_configure":
            result = self.campaign_advancement_configure(
                campaign_id,
                self.required(data, "mode"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "experience_award":
            result = self.campaign_experience_award(
                campaign_id,
                self.required(data, "awards"),
                self.required(data, "reason"),
                self.required(data, "source_ref"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "loot_acquire":
            result = self.campaign_loot_acquire(
                campaign_id,
                self.required(data, "acquisition_id"),
                data.get("coins") or {},
                data.get("items") or [],
                self.required(data, "reason"),
                self.required(data, "source_ref"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "currency_spend":
            result = self.campaign_currency_spend(
                campaign_id,
                self.required(data, "spend_id"),
                self.required(data, "coins"),
                self.required(data, "reason"),
                self.required(data, "source_ref"),
                self.required(data, "rule_ref"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "item_spend":
            result = self.campaign_item_spend(
                campaign_id,
                self.required(data, "spend_id"),
                self.required(data, "item_id"),
                self.required(data, "quantity"),
                self.required(data, "reason"),
                self.required(data, "source_ref"),
                data.get("character_id"),
                data.get("expected_character_revision"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "consumable_use":
            result = self.campaign_consumable_use(
                campaign_id,
                self.required(data, "use_id"),
                self.required(data, "item_id"),
                self.required(data, "target_character_id"),
                self.required(data, "expected_character_revision"),
                self.required(data, "reason"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "update":
            result = self.campaign_update(
                campaign_id,
                data.get("name"),
                data.get("status"),
                data.get("description"),
                data.get("settings"),
                data.get("state"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        else:  # pragma: no cover - guarded by action_contracts above
            raise ValueError(f"unsupported campaign_change action: {action}")
        return self.facade_result(action, result)

    def access_grant(
        self,
        scope: Literal["campaign", "actor"],
        campaign_id: str,
        principal_id: str,
        payload: dict[str, Any] | None = None,
        by_principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Grant campaign membership or actor-level authority without exposing unrelated edits."""
        data = self.facade_payload(payload)
        if scope == "campaign":
            result = self.campaign_member_grant(
                campaign_id, principal_id, data.get("role", "player"), by_principal_id
            )
        else:
            allowed_fields = {"actor_id", "can_control", "can_view_private"}
            unsupported_fields = sorted(set(data) - allowed_fields)
            permission_fields = {"can_control", "can_view_private"} & set(data)
            if unsupported_fields or not permission_fields:
                details = []
                if unsupported_fields:
                    details.append("unsupported fields: " + ", ".join(unsupported_fields))
                if not permission_fields:
                    details.append("provide can_control and/or can_view_private")
                raise ValueError("actor access grant has " + "; ".join(details))
            for field in permission_fields:
                if not isinstance(data[field], bool):
                    raise ValueError(f"actor access grant {field} must be a boolean")
            result = self.actor_grant(
                campaign_id,
                principal_id,
                self.required(data, "actor_id"),
                data.get("can_control"),
                data.get("can_view_private"),
                by_principal_id,
            )
        return self.facade_result(scope, result)

    def access_revoke(
        self,
        campaign_id: str,
        principal_id: str,
        by_principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Revoke one campaign member and all subordinate actor authority atomically."""

        return self.facade_result(
            "campaign",
            self.campaign_member_revoke(campaign_id, principal_id, by_principal_id),
        )

    def campaign_event(
        self,
        campaign_id: str,
        action: Literal["add", "list"],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Append an auditable campaign event or retrieve its branch-visible event log.

        When actor knowledge is included, a DM-only event may only create
        DM-scoped knowledge.  Owner, party, player, and public knowledge must
        cite a party/player/public/actor-visible event.

        For add, put event details in payload.payload and visibility in
        payload.audience_scope; campaign_id belongs at the top level.
        """
        data = self.facade_payload(payload)
        if action == "add":
            allowed = {
                "summary",
                "event_type",
                "payload",
                "audience_scope",
                "branch_id",
                "known_by_actor_ids",
                "knowledge_key",
                "knowledge_proposition",
                "knowledge_disclosure_scope",
            }
            unknown = sorted(set(data) - allowed)
            if unknown:
                raise ValueError(
                    f"campaign_event add has unexpected payload fields: {unknown}. "
                    "Use audience_scope for visibility, payload for event details, "
                    "event_type for type, and top-level campaign_id. "
                    f"Allowed fields: {sorted(allowed)}"
                )
            result = self.event_add(
                campaign_id,
                self.required(data, "summary"),
                data.get("event_type", "narrative"),
                data.get("payload"),
                data.get("audience_scope", "dm"),
                data.get("branch_id"),
                data.get("known_by_actor_ids"),
                data.get("knowledge_key"),
                data.get("knowledge_proposition"),
                data.get("knowledge_disclosure_scope", "owner"),
                principal_id,
                idempotency_key,
            )
        else:
            effective_query = query or str(data.get("query") or "")
            page_limit = _support._page_limit(data.get("limit", limit))
            page_scope = (
                f"campaign_event:list:{campaign_id}:{principal_id}:"
                f"{str(data.get('branch_id') or '')}:{str(data.get('actor_id') or '')}"
            )
            fingerprint, page_offset = _support._cursor_offset(
                scope=page_scope,
                query=effective_query,
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            result = self.event_list(
                campaign_id,
                page_limit + 1,
                data.get("branch_id"),
                data.get("actor_id"),
                principal_id,
                page_offset,
            )
            result, page = _support._authority_page(
                result,
                fingerprint=fingerprint,
                offset=page_offset,
                limit=page_limit,
                query=effective_query,
                chronological_tail=True,
            )
            return self.facade_result(action, result, page=page)
        return self.facade_result(action, result)

    def branch_query(
        self,
        campaign_id: str,
        view: Literal["list", "compare"] = "list",
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """List branches or compare two branch heads without changing checkout state."""
        data = self.facade_payload(payload)
        result = (
            self.branch_compare(
                campaign_id,
                self.required(data, "left_branch_id"),
                self.required(data, "right_branch_id"),
                principal_id,
            )
            if view == "compare"
            else self.branch_list(campaign_id, principal_id)
        )
        if view == "list":
            result, page = _support._bounded_page(
                result,
                scope=f"branch_query:{campaign_id}:{principal_id}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            return self.facade_result(view, result, page=page)
        return self.facade_result(view, result)

    def branch_change(
        self,
        campaign_id: str,
        action: Literal["create", "checkout", "create_core_upgrade"],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create or checkout a branch under campaign and branch revision guards."""
        data = self.facade_payload(payload)
        if action == "create":
            result = self.branch_create(
                campaign_id,
                self.required(data, "name"),
                data.get("from_snapshot_id"),
                self.facade_bool(data, "checkout"),
                principal_id,
                expected_revision,
                expected_branch_id,
                idempotency_key,
            )
        elif action == "checkout":
            result = self.branch_checkout(
                campaign_id,
                self.required(data, "branch_id"),
                principal_id,
                expected_revision,
                expected_branch_id,
                idempotency_key,
            )
        else:
            result = self.snapshot_restore_core_upgrade(
                campaign_id,
                self.required(data, "slot"),
                self.required(data, "name"),
                self.required(data, "expected_snapshot_core_fingerprint"),
                self.required(data, "expected_runtime_core_fingerprint"),
                self.required(data, "reason"),
                principal_id,
                expected_revision,
                expected_branch_id,
                idempotency_key,
            )
        return self.facade_result(action, result)

    def snapshot_query(
        self,
        campaign_id: str,
        view: Literal["list", "verify", "lineage", "recap", "core"] = "list",
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read snapshot history, integrity, lineage, or a regenerated recap.

        list needs no payload. verify/core/recap require payload={slot:<integer>}
        from snapshot_create/list, not a snapshot UUID. lineage accepts slot.
        """
        data = self.facade_payload(payload)
        if view == "list":
            result = self.snapshot_list(campaign_id, principal_id)
        elif view == "verify":
            result = self.snapshot_verify(campaign_id, self.required(data, "slot"), principal_id)
        elif view == "lineage":
            result = self.snapshot_lineage(campaign_id, data.get("slot"), principal_id)
        elif view == "recap":
            result = self.snapshot_regenerate_recap(
                campaign_id, self.required(data, "slot"), principal_id
            )
        else:
            result = self.snapshot_core_lock(campaign_id, self.required(data, "slot"), principal_id)
        if isinstance(result, list):
            result, page = _support._bounded_page(
                result,
                scope=f"snapshot_query:{campaign_id}:{view}:{principal_id}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            return self.facade_result(view, result, page=page)
        return self.facade_result(view, result)

    def state_revision(
        self,
        campaign_id: str,
        action: Literal["history", "receipt", "undo", "redo"],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read revision history, retrieve a known write receipt, or guarded undo/redo.

        receipt requires payload.idempotency_key of the original dispatched write;
        never generate a new key for a lookup. To read the current campaign revision,
        use campaign_query(view="get", payload={campaign_id}), not receipt.
        """
        data = self.facade_payload(payload)
        if action == "history":
            effective_query = query or str(data.get("query") or "")
            page_limit = _support._page_limit(data.get("limit", limit))
            page_scope = f"state_revision:history:{campaign_id}:{principal_id}"
            fingerprint, page_offset = _support._cursor_offset(
                scope=page_scope,
                query=effective_query,
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            result = self.state_history(campaign_id, page_limit + 1, principal_id, page_offset)
        elif action == "receipt":
            receipt_key = str(self.required(data, "idempotency_key")).strip()
            if not receipt_key:
                raise ValueError("idempotency_key is required")
            result = self.state_idempotency_receipt(
                campaign_id,
                receipt_key,
                data.get("branch_id"),
                principal_id,
            )
        elif action == "undo":
            result = self.state_undo(
                campaign_id, principal_id, data.get("expected_history_sequence"), idempotency_key
            )
        else:
            result = self.state_redo(
                campaign_id, principal_id, data.get("expected_history_sequence"), idempotency_key
            )
        if action == "history":
            result, page = _support._authority_page(
                result,
                fingerprint=fingerprint,
                offset=page_offset,
                limit=page_limit,
                query=effective_query,
            )
            return self.facade_result(action, result, page=page)
        return self.facade_result(action, result)
