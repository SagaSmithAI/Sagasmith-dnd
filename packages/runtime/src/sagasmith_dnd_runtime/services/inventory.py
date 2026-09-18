"""Inventory application operations with explicit shared services."""

from __future__ import annotations

from typing import Any, Literal

from .. import application_support as _support


class InventoryService:
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
                (entry for entry in sheets[actor_id]["inventory"]["items"]
                 if entry["id"] == item_id), None
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
                encounter, actor_id_value=actor_id,
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
            include_campaign_revision=False,
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
        recharge={item_id, trigger}, consume_ammunition={weapon_id, quantity?}.
        item_id is the owned sheet.inventory.items[].id from the latest receipt,
        not a catalog artifact_id. Party supports only add/remove. Apply catalog
        equipment with character_content_apply first, then equip its returned
        owned item ID; change quantity through update rather than applying twice.
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
                result = self.character_inventory_equip(
                    owner_id,
                    self.required(data, "item_id"),
                    self.required(data, "slot"),
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
