"""Combat application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from .. import application_support as _support


class CombatService:
    def npc_turn_latest_event_sequence(self, campaign_id: str, branch_id: str | None) -> int:
        values = self.events.list(campaign_id, limit=1, branch_id=branch_id)
        return int(values[-1].sequence) if values else 0

    def npc_turn_actor_state(
        self,
        campaign_id: str,
        branch_id: str | None,
        actor_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
        actor_ref = f"actor:{actor_id}"
        state_facts = [
            _support.asdict(item)
            for item in self.memories.list_for_subject_refs(
                campaign_id,
                subject_refs={actor_ref},
                predicates={"relationship_to", "goal", "commitment"},
                kinds={"actor_state"},
                branch_id=branch_id,
            )
        ]
        fact_heads = {str(item["fact_key"]): str(item["revision_id"]) for item in state_facts}
        knowledge_heads = {
            str(item.knowledge_key): str(item.revision_id)
            for item in self.knowledge.list(
                campaign_id,
                actor_id=actor_id,
                branch_id=branch_id,
                include_inactive=True,
            )
        }
        return state_facts, fact_heads, knowledge_heads

    def npc_turn_scene_projection(self, scene: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(scene, dict):
            return None
        progress = dict(scene.get("progress") or {})
        return {
            "scene_id": str(scene.get("scene_id") or ""),
            "title": str(scene.get("title") or ""),
            "module_id": str(scene.get("module_id") or ""),
            "chapter_id": str(scene.get("chapter_id") or ""),
            "scope_id": str(scene.get("scope_id") or "party"),
            "current_room": str(progress.get("current_room") or ""),
            "current_location_key": str(progress.get("current_location_key") or ""),
            "state_version": int(progress.get("state_version") or 0),
        }

    def npc_turn_perception_projection(
        self,
        campaign: Any,
        *,
        actor_id: str,
        interlocutors: list[dict[str, Any]],
        scene: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Project only immediate, outwardly observable turn context."""

        result: list[dict[str, Any]] = [
            {
                "basis_ref": f"perception:actor:{item['id']}:presence",
                "kind": "interlocutor_presence",
                "actor_id": str(item["id"]),
                "name": str(item["name"]),
                "character_type": str(item["character_type"]),
            }
            for item in interlocutors
        ]
        if scene is not None and scene.get("scene_id"):
            result.append(
                {
                    "basis_ref": f"perception:scene:{scene['scene_id']}:location",
                    "kind": "current_location",
                    "scene_id": str(scene["scene_id"]),
                    "title": str(scene.get("title") or ""),
                    "current_room": str(scene.get("current_room") or ""),
                    "current_location_key": str(scene.get("current_location_key") or ""),
                }
            )
        combat = dict(dict(campaign.state or {}).get("combat") or {})
        if combat.get("active", False):
            allowed_actor_ids = {actor_id, *(str(item["id"]) for item in interlocutors)}
            for raw in [
                *list(combat.get("combatants") or []),
                *list(combat.get("reinforcements") or []),
            ]:
                item = dict(raw or {})
                observed_actor_id = str(item.get("actor_id") or "")
                if observed_actor_id not in allowed_actor_ids:
                    continue
                result.append(
                    {
                        "basis_ref": f"perception:combat:{observed_actor_id}:position",
                        "kind": "combat_position",
                        "actor_id": observed_actor_id,
                        **{
                            key: _support.deepcopy(item[key])
                            for key in ("position", "location", "x", "y", "zone", "status")
                            if key in item
                        },
                    }
                )
        return result

    def npc_turn_actor_projection(self, actor: Any) -> dict[str, Any]:
        notes = self.canonical_character_notes(
            actor.notes,
            character_type=actor.character_type,
            name=actor.name,
            summary=actor.summary,
        )
        profile = dict(notes.get("profile") or {})
        profile.pop("dm_notes", None)
        sheet = _support.validate_character_sheet(actor.sheet)
        return {
            "id": actor.id,
            "name": actor.name,
            "character_type": actor.character_type,
            "summary": actor.summary,
            "revision": actor.revision,
            "profile": profile,
            "self_state": {
                "identity": _support.deepcopy(sheet.get("identity") or {}),
                "progression": _support.deepcopy(sheet.get("progression") or {}),
                "abilities": _support.deepcopy(sheet.get("abilities") or {}),
                "skills": _support.deepcopy(sheet.get("skills") or {}),
                "combat": _support.deepcopy(sheet.get("combat") or {}),
                "traits": _support.deepcopy(sheet.get("traits") or {}),
                "conditions": _support.deepcopy(sheet.get("conditions") or []),
                "effects": _support.deepcopy(sheet.get("effects") or []),
                "status_tags": _support.deepcopy(
                    dict(sheet.get("adventure_state") or {}).get("status_tags") or []
                ),
            },
        }

    def issue_npc_turn_receipt(
        self,
        *,
        bundle: dict[str, Any],
        campaign_id: str,
        branch_id: str | None,
        principal_id: str,
        actor_id: str,
        actor_revision: int,
        interlocutor_actor_ids: list[str],
        scene: dict[str, Any] | None,
        fact_heads: dict[str, str],
        context_fact_heads: dict[str, str],
        knowledge_heads: dict[str, str],
        allowed_basis_refs: list[str],
        stimulus: dict[str, Any],
        module_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        issued_ns = _support.time.monotonic_ns()
        branch = (
            self.branches.get(campaign_id, branch_id)
            if branch_id
            else self.branches.current(campaign_id)
        )
        payload = {
            "schema_version": 2,
            "purpose": "npc_turn",
            "bundle_id": str(bundle["bundle_id"]),
            "bundle_digest": _support.hashlib.sha256(
                _support.canonical_json(bundle).encode("utf-8")
            ).hexdigest(),
            "campaign_id": campaign_id,
            "branch_id": branch.id,
            "head_snapshot_id": branch.head_snapshot_id,
            "campaign_revision": self.campaigns.get(campaign_id).revision,
            "latest_event_sequence": self.npc_turn_latest_event_sequence(campaign_id, branch.id),
            "principal_fingerprint": self.receipt_principal_fingerprint(principal_id),
            "actor_id": actor_id,
            "actor_revision": actor_revision,
            "interlocutor_actor_ids": list(interlocutor_actor_ids),
            "scene_id": str((scene or {}).get("scene_id") or ""),
            "scope_id": str((scene or {}).get("scope_id") or "party"),
            "scene_state_version": int((scene or {}).get("state_version") or 0),
            "fact_heads": dict(sorted(fact_heads.items())),
            "context_fact_heads": dict(sorted(context_fact_heads.items())),
            "knowledge_heads": dict(sorted(knowledge_heads.items())),
            "allowed_basis_refs": sorted(allowed_basis_refs),
            "stimulus_digest": _support.hashlib.sha256(
                _support.canonical_json(stimulus).encode("utf-8")
            ).hexdigest(),
            "module_source_ref_digests": sorted(
                self.managed_module_source_digests(
                    [{"source_ref": dict(item or {}).get("source_ref")} for item in module_evidence]
                )
            ),
            "issued_monotonic_ns": issued_ns,
            "expires_monotonic_ns": issued_ns + self.npc_turn_receipt_ttl_ns,
        }
        return _support.sign_receipt(payload, self.context_receipt_secret)

    def verify_npc_turn_receipt(
        self,
        receipt: Any,
        *,
        campaign_id: str,
        branch_id: str | None,
        principal_id: str,
        required_source_digests: set[str] | None = None,
        require_fresh: bool = True,
    ) -> dict[str, Any]:
        payload = _support.verify_receipt_signature(
            receipt,
            self.context_receipt_secret,
            missing_error="npc_turn.bundle_receipt is required",
            invalid_error="npc_turn.bundle_receipt signature is invalid",
        )
        if payload.get("schema_version") != 2 or payload.get("purpose") != "npc_turn":
            raise ValueError("npc_turn.bundle_receipt has the wrong purpose or schema")
        if payload.get("campaign_id") != campaign_id:
            raise ValueError("npc_turn.bundle_receipt belongs to another campaign")
        if payload.get("branch_id") != branch_id:
            raise ValueError("npc_turn.bundle_receipt belongs to another branch")
        if payload.get("principal_fingerprint") != self.receipt_principal_fingerprint(principal_id):
            raise ValueError("npc_turn.bundle_receipt belongs to another principal")
        required = set(required_source_digests or set())
        received = {str(item) for item in payload.get("module_source_ref_digests") or []}
        if missing := sorted(required - received):
            raise ValueError(
                "npc_turn.bundle_receipt did not include cited module sources: "
                + ", ".join(missing)
            )
        if not require_fresh:
            return payload
        if int(payload.get("expires_monotonic_ns") or 0) < _support.time.monotonic_ns():
            raise ValueError("npc_turn.bundle_receipt expired; read the NPC turn bundle again")
        if int(payload.get("campaign_revision") or -1) != self.campaigns.get(campaign_id).revision:
            raise ValueError("npc_turn.bundle_receipt is stale at the campaign revision")
        branch = self.branches.current(campaign_id)
        if branch.id != branch_id or payload.get("head_snapshot_id") != branch.head_snapshot_id:
            raise ValueError("npc_turn.bundle_receipt is stale after branch or snapshot change")
        if int(payload.get("latest_event_sequence") or 0) != self.npc_turn_latest_event_sequence(
            campaign_id, branch_id
        ):
            raise ValueError("npc_turn.bundle_receipt is stale after a continuity event")
        actor_id = str(payload.get("actor_id") or "")
        actor = self.characters.get(actor_id)
        if actor.campaign_id != campaign_id or actor.revision != int(
            payload.get("actor_revision") or -1
        ):
            raise ValueError("npc_turn.bundle_receipt is stale at the actor revision")
        scene_scope_id = str(payload.get("scope_id") or "party")
        scene = self.npc_turn_scene_projection(
            self.modules.current_scene(campaign_id, scope_id=scene_scope_id)
        )
        if str((scene or {}).get("scene_id") or "") != str(payload.get("scene_id") or ""):
            raise ValueError("npc_turn.bundle_receipt is stale after a scene change")
        if int((scene or {}).get("state_version") or 0) != int(
            payload.get("scene_state_version") or 0
        ):
            raise ValueError("npc_turn.bundle_receipt is stale at the scene revision")
        _state, fact_heads, knowledge_heads = self.npc_turn_actor_state(
            campaign_id,
            branch_id,
            actor_id,
        )
        if dict(payload.get("fact_heads") or {}) != fact_heads:
            raise ValueError("npc_turn.bundle_receipt is stale at an actor-state fact")
        current_memory_heads = {
            str(item.id): str(item.revision_id)
            for item in self.memories.list(
                campaign_id,
                branch_id=branch_id,
                include_inactive=True,
            )
        }
        if any(
            current_memory_heads.get(str(memory_id)) != str(revision_id)
            for memory_id, revision_id in dict(payload.get("context_fact_heads") or {}).items()
        ):
            raise ValueError("npc_turn.bundle_receipt is stale at a context fact")
        if dict(payload.get("knowledge_heads") or {}) != knowledge_heads:
            raise ValueError("npc_turn.bundle_receipt is stale at ActorKnowledge")
        return payload

    def require_outside_active_combat(self, character: Any, operation: str) -> None:
        """Keep direct card mutations from bypassing encounter action economy."""
        if character.campaign_id is None:
            return
        combat = dict(self.campaigns.get(character.campaign_id).state or {}).get("combat")
        if isinstance(combat, dict) and combat.get("active", False):
            raise _support.CombatEngineError(f"{operation} is not allowed while combat is active")

    def combat_actor_snapshot(self, character_id: str) -> dict[str, Any]:
        """Build the pure engine input from the canonical Character row."""
        return self.character_view(self.characters.get(character_id))

    def encounter_actor_ids(self, encounter: dict[str, Any]) -> set[str]:
        return {
            str(item.get("actor_id") or "").strip()
            for item in [
                *encounter.get("combatants", []),
                *encounter.get("reinforcements", []),
            ]
            if isinstance(item, dict) and str(item.get("actor_id") or "").strip()
        }

    def combat_card_analysis(self, character: Any) -> dict[str, Any]:
        """Separate card validity, actor state, and capability-local restrictions."""
        view = self.character_view(character)
        derived = dict(view.get("derived") or {})
        sheet = dict(view.get("sheet") or {})
        attacks = list(dict(derived.get("inventory") or {}).get("weapon_attacks") or [])
        multiattacks = list(derived.get("multiattack_options") or [])
        spellcasting = dict(derived.get("spellcasting") or {})
        prepared_spells = list(spellcasting.get("prepared_spell_ids") or [])
        unresolved = list(derived.get("unresolved_rules") or [])
        hit_points = int(dict(derived.get("hit_points") or {}).get("value", 0) or 0)
        conditions = {str(item).strip().casefold() for item in sheet.get("conditions", [])}
        hard_blockers: list[str] = []
        state_flags: list[str] = []
        disabled_capabilities: list[dict[str, str]] = [
            {
                "id": f"rule:{index + 1}",
                "kind": "rule",
                "reason": str(reason),
            }
            for index, reason in enumerate(unresolved)
        ]
        if self.narrative_only_actor(character):
            hard_blockers.append("narrative_only_noncombat")
        if hit_points <= 0:
            state_flags.append("zero_hit_points")
        if "dead" in conditions:
            state_flags.append("dead")
        dm_notes = str(
            dict(dict(view.get("notes") or {}).get("profile") or {}).get("dm_notes") or ""
        )
        note_lines = dm_notes.splitlines()
        statblock_provenance_prefixes = (
            "Statblock import:",
            "Reviewed rule statblock:",
            "Reviewed module statblock:",
        )
        provenance_indices = [
            index
            for index, line in enumerate(note_lines)
            if line.strip().startswith(statblock_provenance_prefixes)
        ]
        if provenance_indices:
            note_lines = note_lines[provenance_indices[-1] :]
        manual_rulings: list[str] = []
        normalization_notes: list[str] = []
        for line in note_lines:
            if "Manual rulings:" in line:
                value = line.split("Manual rulings:", 1)[1].strip().rstrip(".")
                value = value.partition(" Variant source:")[0].rstrip(". ")
                manual_rulings.extend(item.strip() for item in value.split(";") if item.strip())
            if "Normalization notes:" in line:
                value = line.split("Normalization notes:", 1)[1].strip().rstrip(".")
                normalization_notes.extend(
                    item.strip() for item in value.split(";") if item.strip()
                )
        manual_rulings = list(dict.fromkeys(manual_rulings))
        normalization_notes = list(dict.fromkeys(normalization_notes))
        specific_multiattacks = {
            item.split(":", 1)[0]
            for item in manual_rulings
            if item.endswith("Multiattack composition requires a DM ruling")
        }
        manual_rulings = [
            item
            for item in manual_rulings
            if not (
                item.endswith("descriptive action is not automatically settled")
                and item.split(":", 1)[0] in specific_multiattacks
            )
        ]
        incomplete_spellcasting = any(
            item.endswith("no active spell artifact or complete statblock action exists")
            or (
                item.startswith("Spellcasting:")
                and item.endswith("descriptive passive is not automatically settled")
            )
            for item in manual_rulings
        )
        if incomplete_spellcasting:
            disabled_capabilities.append(
                {
                    "id": "spellcasting",
                    "kind": "spellcasting",
                    "reason": "incomplete_statblock_spell_hydration",
                }
            )
        inventory_items = {
            str(item.get("id") or ""): item
            for item in dict(sheet.get("inventory") or {}).get("items", [])
        }
        for attack in attacks:
            attack_id = str(attack.get("item_id") or "")
            attack_name = str(attack.get("name") or attack_id or "Weapon")
            attack_item = dict(inventory_items.get(attack_id) or {})
            properties = {str(item).strip().casefold() for item in attack.get("properties", [])}
            if (
                str(attack.get("attack_type") or "melee").casefold() == "ranged"
                and int(dict(attack.get("range_ft") or {}).get("normal", 0) or 0) <= 0
            ):
                if _support._has_source_defined_positional_targeting(
                    attack_item.get("description")
                ):
                    reason = (
                        f"{attack_name}: source-defined positional targeting requires a DM ruling"
                    )
                else:
                    reason = f"{attack_name}: ranged weapon range is missing"
                    disabled_capabilities.append(
                        {"id": attack_id, "kind": "attack", "reason": reason}
                    )
                manual_rulings.append(reason)
            if (
                "thrown" in properties
                and int(dict(attack.get("thrown_range_ft") or {}).get("normal", 0) or 0) <= 0
            ):
                reason = f"{attack_name}: thrown weapon range is missing"
                manual_rulings.append(reason)
                disabled_capabilities.append({"id": attack_id, "kind": "attack", "reason": reason})
            ammunition_id = str(attack.get("ammunition_item_id") or "")
            if (
                ammunition_id
                and int(dict(inventory_items.get(ammunition_id) or {}).get("quantity", 0) or 0) <= 0
            ):
                disabled_capabilities.append(
                    {
                        "id": attack_id,
                        "kind": "attack",
                        "reason": f"ammunition {ammunition_id} is unavailable",
                    }
                )

        spell_resolution_audit = _support.audit_spell_resolution_paths(sheet)
        automatic_spell_ids: list[str] = []
        ruling_spell_ids: list[str] = []
        incomplete_spell_ids: list[str] = []
        for entry in spell_resolution_audit["entries"]:
            if not entry["available"]:
                continue
            spell_id = str(entry["spell_id"])
            path = str(entry["resolution_path"])
            if path in {
                "engine_mechanic",
                "semantic_plan",
                "structured_resolution",
            }:
                automatic_spell_ids.append(spell_id)
            elif path == "agent_ruling":
                ruling_spell_ids.append(spell_id)
            else:
                incomplete_spell_ids.append(spell_id)
        if ruling_spell_ids:
            manual_rulings.append(
                "Available spells require Agent effect settlement: " + ", ".join(ruling_spell_ids)
            )
        if incomplete_spell_ids:
            manual_rulings.append(
                "Available spells lack a recorded settlement path: "
                + ", ".join(incomplete_spell_ids)
            )
            disabled_capabilities.extend(
                {
                    "id": spell_id,
                    "kind": "spell",
                    "reason": "spell lacks a recorded settlement path",
                }
                for spell_id in incomplete_spell_ids
            )
        manual_rulings = list(dict.fromkeys(manual_rulings))
        character_type = str(view.get("character_type") or "")
        ruling_requirements = [
            *[
                _support._ruling_requirement(
                    str(rule),
                    "missing_or_conflicting_source_review",
                )
                for rule in unresolved
            ],
            *[
                _support._ruling_requirement(
                    reason,
                    self.statblock_ruling_kind(
                        reason,
                        character_type=character_type,
                    ),
                )
                for reason in manual_rulings
            ],
        ]
        settlement = (
            "source_review_required" if unresolved else "mixed" if manual_rulings else "automatic"
        )
        disabled_capabilities = list(
            {
                (item["id"], item["kind"], item["reason"]): item for item in disabled_capabilities
            }.values()
        )
        disabled_ids = {item["id"] for item in disabled_capabilities}
        available_capabilities = [
            {"id": "unarmed-strike", "kind": "attack"},
            *[
                {"id": str(item.get("item_id") or ""), "kind": "attack"}
                for item in attacks
                if str(item.get("item_id") or "") not in disabled_ids
            ],
            *[
                {"id": spell_id, "kind": "spell"}
                for spell_id in [*automatic_spell_ids, *ruling_spell_ids]
                if spell_id not in disabled_ids and not incomplete_spellcasting
            ],
        ]
        return {
            "card_valid": not hard_blockers,
            "hard_blockers": sorted(set(hard_blockers)),
            "state_flags": sorted(set(state_flags)),
            "can_take_turn": not state_flags,
            "disabled_capabilities": disabled_capabilities,
            "available_capabilities": available_capabilities,
            "settlement": settlement,
            "default_dm_resolver": "agent",
            "unresolved_rules": unresolved,
            "manual_rulings": manual_rulings,
            "normalization_notes": normalization_notes,
            "ruling_requirements": ruling_requirements,
            "agent_rulings": [
                item["reason"]
                for item in ruling_requirements
                if item["default_resolver"] == "agent"
            ],
            "external_source_gaps": [
                item["reason"]
                for item in ruling_requirements
                if item["default_resolver"] == "external_input"
            ],
            "hit_points": hit_points,
            "maximum_hit_points": int(dict(derived.get("hit_points") or {}).get("max", 0) or 0),
            "armor_class": (
                10 if derived.get("armor_class") is None else int(derived["armor_class"])
            ),
            "weapon_attack_ids": [str(item.get("item_id") or "") for item in attacks],
            "multiattack_option_ids": [str(item.get("id") or "") for item in multiattacks],
            "prepared_spell_ids": prepared_spells,
            "available_spell_ids": spell_resolution_audit["available_spell_ids"],
            "cantrip_spell_ids": spell_resolution_audit["cantrip_spell_ids"],
            "spell_resolution_audit": spell_resolution_audit,
            "automatic_spell_ids": automatic_spell_ids,
            "ruling_spell_ids": ruling_spell_ids,
            "incomplete_spell_ids": incomplete_spell_ids,
            "unarmed_fallback": True,
            "unarmed_attack_id": "unarmed-strike",
        }

    def combat_audience_view(
        self,
        campaign_id: str,
        principal_id: str,
        encounter: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Project a specific encounter revision without replacing it with live state."""

        if encounter is None:
            return None
        membership = self.access.require_campaign(campaign_id, principal_id)
        value = _support.deepcopy(dict(encounter))
        if bool(encounter.get("active", False)):
            value["snapshot_role"] = "live_encounter"
            value["combatant_state_is_current"] = True
            value["current_character_state_source"] = "combat_and_character_query"
        else:
            value["snapshot_role"] = "historical_final_encounter"
            value["combatant_state_is_current"] = False
            value["current_character_state_source"] = "character_query"
        if membership.role not in _support.CAMPAIGN_DM_ROLES:
            viewer_actor_ids: set[str] = set()
            for item in encounter.get("combatants", []):
                actor_id_value = str(item.get("actor_id") or "")
                try:
                    self.access.require_actor(
                        campaign_id,
                        actor_id_value,
                        principal_id,
                        private=True,
                    )
                except PermissionError:
                    continue
                viewer_actor_ids.add(actor_id_value)

            def player_can_see(item: dict[str, Any]) -> bool:
                actor_id_value = str(item.get("actor_id") or "")
                if actor_id_value in viewer_actor_ids:
                    return True
                concealed = bool(item.get("hidden", False)) or "invisible" in {
                    str(condition).casefold() for condition in item.get("conditions", [])
                }
                if not concealed:
                    return True
                visible_to = item.get("visible_to_actor_ids")
                return isinstance(visible_to, list) and bool(
                    viewer_actor_ids & {str(actor_id) for actor_id in visible_to}
                )

            visible_combatants = [
                {
                    key: item[key]
                    for key in ("actor_id", "token_id", "name", "initiative", "position")
                    if key in item
                }
                for item in encounter.get("combatants", [])
                if player_can_see(item)
            ]
            current = _support.current_combatant(encounter)
            current_id = str(current.get("actor_id")) if current is not None else None
            visible_ids = [str(item.get("actor_id")) for item in visible_combatants]
            value["combatants"] = visible_combatants
            value["turn_index"] = (
                visible_ids.index(current_id) if current_id in visible_ids else None
            )
            value.pop("log", None)
            value.pop("rulings", None)
            value.pop("pending", None)
            value.pop("readied", None)
            value.pop("effects", None)
            value.pop("reinforcements", None)
            value.pop("participant_manifest", None)
            value.pop("semantic_state", None)
            battle_map = value.get("battle_map")
            if isinstance(battle_map, dict):
                value["battle_map"] = {
                    key: _support.deepcopy(battle_map[key])
                    for key in (
                        "id",
                        "schema_version",
                        "map_revision",
                        "lifecycle",
                        "source",
                        "grid",
                        "bounds",
                    )
                    if key in battle_map
                }
        return value

    def combat_view(self, campaign_id: str, principal_id: str) -> dict[str, Any] | None:
        campaign = self.campaigns.get(campaign_id)
        encounter = dict(campaign.state or {}).get("combat")
        return self.combat_audience_view(campaign_id, principal_id, encounter)

    def party_public_combat_view(
        self,
        campaign_id: str,
        principal_id: str,
    ) -> dict[str, Any] | None:
        """Return a shared-party projection that cannot elevate the caller's view."""

        caller_view = self.combat_view(campaign_id, principal_id)
        if caller_view is None:
            return None
        value = _support.deepcopy(caller_view)
        combatants = [
            dict(item)
            for item in value.get("combatants", [])
            if isinstance(item, dict)
            and not bool(item.get("hidden", False))
            and "invisible" not in _support.condition_ids(item.get("conditions", []))
        ]
        current = _support.current_combatant(caller_view)
        current_id = str(current.get("actor_id")) if current is not None else None
        visible_combatants = [
            {
                key: item[key]
                for key in ("actor_id", "token_id", "name", "initiative", "position")
                if key in item
            }
            for item in combatants
        ]
        visible_ids = [str(item.get("actor_id")) for item in visible_combatants]
        value["combatants"] = visible_combatants
        value["turn_index"] = visible_ids.index(current_id) if current_id in visible_ids else None
        for key in (
            "log",
            "rulings",
            "pending",
            "semantic_state",
            "readied",
            "effects",
            "reinforcements",
            "participant_manifest",
        ):
            value.pop(key, None)
        battle_map = value.get("battle_map")
        if isinstance(battle_map, dict):
            public_battle_map = {
                key: _support.deepcopy(battle_map[key])
                for key in (
                    "schema_version",
                    "map_revision",
                    "grid",
                    "bounds",
                )
                if key in battle_map
            }
            raw_encounter = dict(
                dict(self.campaigns.get(campaign_id).state or {}).get("combat") or {}
            )
            raw_battle_map = dict(raw_encounter.get("battle_map") or {})
            raw_bounds = dict(raw_battle_map.get("bounds") or {})
            try:
                public_battle_map["party_public_map_asset"] = (
                    _support.normalize_party_public_map_asset(
                        raw_battle_map.get("party_public_map_asset"),
                        width_cells=int(raw_bounds.get("width_cells", 0) or 0),
                        height_cells=int(raw_bounds.get("height_cells", 0) or 0),
                    )
                )
            except _support.BattleMapError:
                public_battle_map.pop("party_public_map_asset", None)
            value["battle_map"] = public_battle_map
        return value

    def render_combat_snapshot(
        self,
        campaign_id: str,
        principal_id: str,
        audience_projection: Literal["caller", "party_public"],
    ) -> list[Any]:
        """Render a read-only audience projection without affecting combat readiness."""

        if audience_projection == "party_public":
            encounter = self.party_public_combat_view(campaign_id, principal_id)
            campaign = self.campaigns.get(campaign_id)
            authoritative_encounter = dict(dict(campaign.state or {}).get("combat") or {})
            public_map_asset = (
                self.party_public_map_asset_content(campaign_id, authoritative_encounter)
                if authoritative_encounter
                else None
            )
        else:
            encounter = self.combat_view(campaign_id, principal_id)
            public_map_asset = None
        if encounter is None:
            raise _support.CombatEngineError("campaign has no combat snapshot to render")
        portraits: dict[str, bytes] = {}
        for combatant in encounter.get("combatants", []):
            if not isinstance(combatant, dict):
                continue
            actor_id_value = str(combatant.get("actor_id") or "")
            if not actor_id_value:
                continue
            character = self.characters.get(actor_id_value)
            portrait_ref = dict(
                dict(dict(character.notes or {}).get("profile") or {}).get("portrait_ref") or {}
            )
            portrait = self.storage.read_actor_image(str(portrait_ref.get("checksum") or ""))
            if portrait is not None:
                portraits[actor_id_value] = portrait
            if audience_projection != "caller":
                continue
            try:
                self.access.require_actor(campaign_id, actor_id_value, principal_id, private=True)
            except PermissionError:
                continue
            hp = dict(dict(character.sheet or {}).get("combat") or {}).get("hp")
            if isinstance(hp, dict):
                combatant["hp"] = {
                    "current": hp.get("value"),
                    "max": hp.get("max"),
                    "temporary": hp.get("temp"),
                }
        metadata, content = _support._render_combat_png(
            encounter,
            portraits=portraits,
            audience_projection=audience_projection,
            party_public_map_asset=public_map_asset,
        )
        campaign = self.campaigns.get(campaign_id)
        metadata.update(
            {
                "campaign_id": campaign_id,
                "campaign_revision": campaign.revision,
                "view": "render",
                "render_is_authoritative": False,
                "combat_state_is_authoritative": True,
            }
        )
        return [metadata, _support.Image(data=content, format="png")]

    def combat_response(
        self, campaign_id: str, principal_id: str, response: dict[str, Any]
    ) -> dict[str, Any]:
        """Project every combat write result through the same audience boundary."""
        value = dict(response)
        if "combat" in value:
            encounter = value.get("combat")
            value["combat"] = self.combat_audience_view(
                campaign_id,
                principal_id,
                dict(encounter) if isinstance(encounter, dict) else None,
            )
        if self.is_dm(campaign_id, principal_id):
            return value
        result = value.get("result")
        if isinstance(result, dict):
            allowed = {
                "kind",
                "attacker_id",
                "target_id",
                "hit",
                "critical",
                "fumble",
                "natural",
                "rolls",
                "rerolls",
                "total",
                "bonus",
                "success",
                "successes",
                "failures",
                "outcome",
                "amount",
                "applied_amount",
                "hp_damage",
                "healed",
                "after_hp",
                "effects_active",
                "conditions",
                "parts",
                "activity_id",
                "content_type",
                "name",
                "payment",
                "requires_ruling",
                "declaration",
                "defense",
                "reaction_defense",
                "pending_reaction",
                "spell_id",
                "cast_level",
                "resolution_id",
                "attack_count",
                "remaining_attacks",
                "spell_resolution",
                "deflect_attack",
                "distance_ft",
                "dice_count",
                "damage_roll",
                "damage",
                "prone_added",
                "knocked_prone",
            }
            value["result"] = {key: item for key, item in result.items() if key in allowed}
        value.pop("revisions", None)
        return value

    def active_encounter(self, campaign_id: str) -> tuple[Any, dict[str, Any]]:
        campaign = self.campaigns.get(campaign_id)
        encounter = dict(campaign.state or {}).get("combat")
        if not isinstance(encounter, dict) or not encounter.get("active", False):
            raise _support.CombatEngineError("combat is not active")
        self.encounter_rules_edition(campaign_id, encounter)
        return campaign, encounter

    def encounter_rules_edition(
        self,
        campaign_id: str,
        encounter: dict[str, Any],
    ) -> str:
        """Verify the immutable encounter projection against the campaign profile."""

        authoritative = self.campaign_rules_edition(campaign_id)
        projected = str(encounter.get("ruleset") or "")
        if not projected:
            raise RuntimeError("active encounter has no ruleset projection")
        if projected != authoritative:
            raise RuntimeError("active encounter ruleset diverges from the campaign rule profile")
        return authoritative

    def require_encounter_combatant(
        self, encounter: dict[str, Any], actor_id: str, *, role: str = "actor"
    ) -> dict[str, Any]:
        """Return one encounter participant or reject cross-boundary settlement."""
        combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if str(item.get("actor_id") or "") == actor_id
            ),
            None,
        )
        if combatant is None:
            raise _support.CombatEngineError(f"{role} is not a combatant in the active encounter")
        return combatant

    def validate_agent_movement_facts(
        self,
        encounter: dict[str, Any],
        spatial_facts: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Validate the Agent's coordinate-free movement judgment."""

        positioning_mode = str(encounter.get("positioning_mode") or "grid")
        if positioning_mode == "grid":
            if spatial_facts is not None:
                raise _support.CombatEngineError(
                    "grid movement derives spatial facts from encounter coordinates"
                )
            return None
        if not isinstance(spatial_facts, dict):
            raise _support.NeedsRulingError(
                "agent-positioned movement requires an Agent spatial decision",
                missing=("movement.spatial_facts",),
                ruling_kind="agent_dm_adjudication",
            )
        allowed_fields = {
            "decision_id",
            "reason",
            "destination_legal",
            "distance_ft",
            "difficult_terrain_extra_ft",
            "moves_farther_from_turn_source",
            "enters_turn_source_30_ft",
            "moves_closer_to_visible_fear_source",
            "moves_toward_aggressive_target",
            "opportunity_attack_actor_ids",
        }
        required_fields = {"decision_id", "reason", "destination_legal", "distance_ft"}
        if set(spatial_facts) - allowed_fields or required_fields - set(spatial_facts):
            raise _support.CombatEngineError(
                "Agent movement spatial facts require decision_id, reason, destination_legal, "
                "and distance_ft"
            )
        decision_id = str(spatial_facts.get("decision_id") or "").strip()
        reason = " ".join(str(spatial_facts.get("reason") or "").split())
        if not decision_id or not reason:
            raise _support.CombatEngineError("Agent movement facts require decision_id and reason")
        for field in {
            "destination_legal",
            "moves_farther_from_turn_source",
            "enters_turn_source_30_ft",
            "moves_closer_to_visible_fear_source",
            "moves_toward_aggressive_target",
        }:
            if not isinstance(spatial_facts.get(field, False), bool):
                raise _support.CombatEngineError(
                    f"Agent movement spatial fact {field} must be boolean"
                )
        for field in {"distance_ft", "difficult_terrain_extra_ft"}:
            value = spatial_facts.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value % 5:
                raise _support.CombatEngineError(
                    f"Agent movement spatial fact {field} must be a non-negative "
                    "five-foot increment"
                )
        participant_ids = {
            str(item.get("actor_id") or "") for item in encounter.get("combatants", [])
        }
        threat_ids = spatial_facts.get("opportunity_attack_actor_ids", [])
        if (
            not isinstance(threat_ids, list)
            or any(not isinstance(item, str) for item in threat_ids)
            or len(set(threat_ids)) != len(threat_ids)
            or set(threat_ids) - participant_ids
        ):
            raise _support.CombatEngineError(
                "Agent movement opportunity_attack_actor_ids must contain unique current "
                "combatant IDs"
            )
        return {
            **dict(spatial_facts),
            "decision_id": decision_id,
            "reason": reason,
            "difficult_terrain_extra_ft": int(spatial_facts.get("difficult_terrain_extra_ft", 0)),
            "moves_farther_from_turn_source": bool(
                spatial_facts.get("moves_farther_from_turn_source", False)
            ),
            "enters_turn_source_30_ft": bool(spatial_facts.get("enters_turn_source_30_ft", False)),
            "moves_closer_to_visible_fear_source": bool(
                spatial_facts.get("moves_closer_to_visible_fear_source", False)
            ),
            "moves_toward_aggressive_target": bool(
                spatial_facts.get("moves_toward_aggressive_target", False)
            ),
            "opportunity_attack_actor_ids": list(threat_ids),
        }

    def sync_combatant_conditions(
        self, encounter: dict[str, Any], actor_id: str, sheet: dict[str, Any]
    ) -> None:
        # SCAG Bladesong ends immediately when its source-defined equipment or
        # incapacitation condition becomes true.  Keep this projection in the
        # same transaction as the character update so it cannot be forged by a
        # stale encounter snapshot.
        conditions = {str(item).casefold() for item in sheet.get("conditions", [])}
        slots = dict(sheet.get("inventory", {}).get("equipment_slots") or {})
        items = {
            str(item.get("id") or ""): item for item in sheet.get("inventory", {}).get("items", [])
        }
        worn_armor = items.get(str(slots.get("armor") or ""))
        worn_shield = items.get(str(slots.get("shield") or ""))
        worn_armor_category = str(
            dict(dict(worn_armor or {}).get("mechanics") or {}).get("category") or ""
        ).casefold()
        bladesong_end_reason = None
        if conditions.intersection(_support.INCAPACITATING_STATE_IDS):
            bladesong_end_reason = "incapacitated"
        elif worn_armor_category in {"medium", "heavy"} or worn_shield is not None:
            bladesong_end_reason = "armor_or_shield"
        if bladesong_end_reason:
            for effect in sheet.get("effects", []):
                if (
                    effect.get("active")
                    and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
                ):
                    effect["active"] = False
                    effect["ended_reason"] = bladesong_end_reason
        for combatant in [
            *encounter.get("combatants", []),
            *encounter.get("reinforcements", []),
        ]:
            if combatant.get("actor_id") == actor_id:
                # Upgrade old encounter snapshots before replacing their stale
                # projection. Otherwise removing a zero-speed or grappling
                # effect could make a persisted dodging=True flag active again.
                prior_dodge_transition = _support.reconcile_dodge_lifecycle(combatant)
                combatant["conditions"] = list(sheet.get("conditions") or [])
                combatant["hit_points"] = int(sheet["combat"]["hp"]["value"])
                if (
                    combatant["hit_points"] > 0
                    or _support.condition_ids(sheet.get("conditions"))
                    & _support.DEATH_SAVE_SETTLED_CONDITIONS
                ):
                    flags = combatant.get("turn_flags")
                    if isinstance(flags, dict) and "death_save_due" in flags:
                        flags["death_save_due"] = False
                combatant["condition_sources"] = _support.timed_condition_sources(sheet)
                combatant["speed_multiplier"] = _support.source_speed_multiplier(sheet)
                if conditions.intersection(_support.INCAPACITATING_STATE_IDS):
                    flags = dict(combatant.get("turn_flags") or {})
                    flags.pop("helping", None)
                    if flags:
                        combatant["turn_flags"] = flags
                    else:
                        combatant.pop("turn_flags", None)
                _support.reconcile_tortle_shell_defense_projection(combatant, sheet)
                current_dodge_transition = _support.reconcile_dodge_lifecycle(combatant)
                dodge_transition = (
                    prior_dodge_transition
                    if prior_dodge_transition["ended_reason"] is not None
                    else current_dodge_transition
                )
                if dodge_transition["ended_reason"] is not None:
                    encounter["log"] = [
                        *list(encounter.get("log") or []),
                        {
                            "type": "dodge_ended",
                            "actor_id": actor_id,
                            "mechanic_id": dodge_transition["mechanic_id"],
                            "reason": dodge_transition["ended_reason"],
                        },
                    ][-100:]
                if "turned" not in {str(item).casefold() for item in combatant["conditions"]}:
                    combatant.pop("turned", None)
                self.reconcile_actor_witch_bolt_concentration(
                    encounter,
                    actor_id,
                    sheet,
                )
                return

    def combatant_zero_hp_buffered(self, combatant: dict[str, Any]) -> bool:
        return bool(combatant.get("death_saves", False) or combatant.get("zero_hp_recovery", False))

    def encounter_turn_token(self, encounter: dict[str, Any]) -> str:
        current = _support.current_combatant(encounter)
        return (
            f"{int(encounter.get('round', 1) or 1)}:"
            f"{int(encounter.get('turn_index', 0) or 0)}:"
            f"{str((current or {}).get('actor_id') or '')}"
        )

    def expire_standard_source_turn_effects(
        self,
        encounter: dict[str, Any],
        *,
        actor_id: str,
        phase: Literal["source_turn_start", "source_turn_end"],
        turn_token: str,
    ) -> list[str]:
        expired: list[str] = []
        for effect in encounter.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("mechanic_id") == _support.SPELL_RESOLUTION_MECHANIC_ID
                and bool(effect.get("standard_on_hit_mechanic"))
                and str(effect.get("expires_on_actor_id") or "") == actor_id
                and effect.get("expires_at") == phase
                and str(effect.get("activated_turn_token") or "") != turn_token
            ):
                effect["active"] = False
                effect["resolution"] = {
                    "kind": "duration_expired",
                    "actor_id": actor_id,
                    "phase": phase,
                    "turn_token": turn_token,
                }
                expired.append(str(effect.get("id") or ""))
        return expired

    def combat_coordinates(self, position: Any) -> tuple[float, float] | None:
        if isinstance(position, dict) and "x" in position and "y" in position:
            return float(position["x"]), float(position["y"])
        if isinstance(position, (list, tuple)) and len(position) == 2:
            return float(position[0]), float(position[1])
        return None

    def combat_distance(
        self,
        left: Any,
        right: Any,
        *,
        cell_ft: int = 5,
    ) -> int | None:
        left_coordinates = self.combat_coordinates(left)
        right_coordinates = self.combat_coordinates(right)
        if left_coordinates is None or right_coordinates is None:
            return None
        return int(
            max(
                abs(left_coordinates[0] - right_coordinates[0]),
                abs(left_coordinates[1] - right_coordinates[1]),
            )
            * int(cell_ft)
        )

    def _steel_defender_turn_contracts(
        self,
        campaign_id: str,
        branch_id: str,
        participant_ids: set[str],
    ) -> dict[str, dict[str, str]]:
        """Project verified active Steel Defender relations into encounter-only state."""

        state = dict(self.campaigns.get(campaign_id).state or {})
        relations = _support.validate_dependent_actor_relations(
            state.get("dependent_actor_relations", [])
        )
        contracts: dict[str, dict[str, str]] = {}
        for relation in relations:
            dependent_id = relation["dependent_actor_id"]
            if relation["status"] != "active" or dependent_id not in participant_ids:
                continue
            if relation["relation_key"] != _support.STEEL_DEFENDER_RELATION_KEY:
                continue
            owner_id = relation["owner_character_id"]
            if owner_id not in participant_ids:
                raise _support.CombatEngineError(
                    "an active Steel Defender can enter combat only with its owner"
                )
            contracts[dependent_id] = self._verified_steel_defender_relation(
                campaign_id,
                branch_id,
                relation,
                require_current_parameters=True,
            )
        return contracts

    def require_combat_actor_or_steel_defender_owner_control(
        self,
        campaign_id: str,
        actor_id_value: str,
        principal_id: str,
        *,
        branch_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Authorize direct control or the signed owner of an active Steel Defender."""

        try:
            self.access.require_actor(
                campaign_id,
                actor_id_value,
                principal_id,
                control=True,
            )
            return None
        except PermissionError as direct_error:
            campaign, encounter = self.active_encounter(campaign_id)
            if actor_id_value not in {
                str(item.get("actor_id") or "") for item in encounter.get("combatants", [])
            }:
                raise direct_error
            matches = [
                relation
                for relation in _support.validate_dependent_actor_relations(
                    dict(campaign.state or {}).get("dependent_actor_relations", [])
                )
                if relation["dependent_actor_id"] == actor_id_value
                and relation["relation_key"] == _support.STEEL_DEFENDER_RELATION_KEY
                and relation["status"] == "active"
            ]
            if len(matches) != 1:
                raise direct_error
            resolved_branch_id = branch_id or self.require_current_branch(campaign_id, None)
            contract = self._verified_steel_defender_relation(
                campaign_id,
                resolved_branch_id,
                matches[0],
                require_current_parameters=True,
            )
            self.access.require_actor(
                campaign_id,
                matches[0]["owner_character_id"],
                principal_id,
                control=True,
            )
            return {
                **contract,
                "owner_character_id": matches[0]["owner_character_id"],
                "dependent_actor_id": actor_id_value,
            }

    def reviewed_chase_source(
        self,
        campaign_id: str,
        *,
        scene_id: str,
        source_ref: dict[str, Any],
        source_excerpt: str,
    ) -> dict[str, Any]:
        """Resolve an exact managed module citation for a chase mutation."""
        _, normalized_source, expanded = self.managed_module_source_ref(
            campaign_id,
            source_ref,
            require_exact=True,
            expected_scene_id=scene_id,
        )
        if normalized_source is None or expanded is None:
            raise AssertionError("exact chase citations always resolve to a managed chunk")
        normalized_excerpt = self.managed_module_source_excerpt(
            expanded,
            source_excerpt,
            field="chase source_excerpt",
        )
        return {
            **normalized_source,
            "source_excerpt": normalized_excerpt,
        }

    def chase_start(
        self,
        campaign_id: str,
        participant_ids: list[str],
        quarry_ids: list[str],
        initial_distance_ft: int,
        scene_id: str,
        source_ref: dict[str, Any],
        source_excerpt: str,
        name: str = "Chase",
        participant_config: list[dict[str, Any]] | None = None,
        close_transition: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a source-reviewed 2014 chase without creating a combat map."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        if not participant_ids or len(participant_ids) != len(set(participant_ids)):
            raise ValueError("participant_ids must contain unique actors")
        if self.npc_conversations.active_ids(
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
        ):
            raise _support.CombatEngineError(
                "close or abort the active NPC conversation before starting a chase"
            )
        evidence = self.reviewed_chase_source(
            campaign_id,
            scene_id=scene_id,
            source_ref=source_ref,
            source_excerpt=source_excerpt,
        )
        resolved_close_transition: dict[str, Any] | None = None
        if close_transition is not None:
            if not isinstance(close_transition, dict):
                raise ValueError("chase close_transition must be an object")
            allowed = {
                "distance_ft",
                "status",
                "summary",
                "source_ref",
                "source_excerpt",
            }
            unknown = sorted(set(close_transition) - allowed)
            if unknown:
                raise ValueError("unsupported chase close_transition fields: " + ", ".join(unknown))
            transition_evidence = self.reviewed_chase_source(
                campaign_id,
                scene_id=scene_id,
                source_ref=close_transition.get("source_ref"),
                source_excerpt=str(close_transition.get("source_excerpt") or ""),
            )
            transition_summary = _support._normalize_source_evidence_text(
                str(close_transition.get("summary") or "")
            )
            if transition_summary != transition_evidence["source_excerpt"]:
                raise ValueError(
                    "chase close_transition summary must equal its exact source_excerpt"
                )
            resolved_close_transition = {
                "distance_ft": close_transition.get("distance_ft"),
                "status": close_transition.get("status"),
                "summary": transition_summary,
                "source_ref": transition_evidence,
            }
        config_by_actor: dict[str, dict[str, Any]] = {}
        for raw in participant_config or []:
            if not isinstance(raw, dict) or not raw.get("actor_id"):
                raise ValueError("each chase participant_config entry needs actor_id")
            unknown = set(raw) - {
                "actor_id",
                "initiative",
                "initiative_group_id",
                "tie_breaker",
                "speed_adjustment_ft",
                "source_excerpt",
            }
            if unknown:
                raise ValueError(f"unsupported chase participant_config fields: {sorted(unknown)}")
            identifier = str(raw["actor_id"])
            if identifier not in participant_ids or identifier in config_by_actor:
                raise ValueError("chase participant_config actor_id is invalid or duplicated")
            has_speed_adjustment = "speed_adjustment_ft" in raw
            has_speed_source = "source_excerpt" in raw
            if has_speed_adjustment != has_speed_source:
                raise ValueError(
                    "chase participant speed adjustment requires both "
                    "speed_adjustment_ft and source_excerpt"
                )
            normalized = {
                key: _support.deepcopy(raw[key])
                for key in ("actor_id", "initiative", "tie_breaker")
                if key in raw
            }
            if has_speed_adjustment:
                adjustment = raw["speed_adjustment_ft"]
                speed_excerpt = " ".join(str(raw["source_excerpt"] or "").split()).strip()
                if (
                    isinstance(adjustment, bool)
                    or not isinstance(adjustment, (int, float))
                    or not float(adjustment).is_integer()
                    or int(adjustment) == 0
                    or not -100 <= int(adjustment) <= 100
                ):
                    raise ValueError(
                        "chase participant speed adjustment must be a nonzero "
                        "integer from -100 to 100"
                    )
                if not speed_excerpt or _support._normalize_source_evidence_text(
                    speed_excerpt
                ) not in _support._normalize_source_evidence_text(evidence["source_excerpt"]):
                    raise ValueError(
                        "chase participant speed adjustment requires exact chase-source evidence"
                    )
                normalized["speed_adjustment_ft"] = int(adjustment)
                normalized["source_excerpt"] = speed_excerpt
            config_by_actor[identifier] = normalized
        normalized_participant_config = [
            _support.deepcopy(config_by_actor[str(raw["actor_id"])])
            for raw in participant_config or []
        ]
        payload = {
            "participant_ids": list(participant_ids),
            "quarry_ids": list(quarry_ids),
            "initial_distance_ft": initial_distance_ft,
            "scene_id": scene_id,
            "source_ref": evidence,
            "name": name,
            "participant_config": normalized_participant_config,
            "close_transition": resolved_close_transition,
            "branch_id": resolved_branch_id,
        }
        scope = f"chase-start:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            _support.validate_party_state(_support.deepcopy(campaign.state or {})),
            operation="starting a chase",
        )
        if dict(campaign.state.get("combat") or {}).get("active", False):
            raise _support.CombatEngineError("a chase cannot start while combat is active")
        if dict(campaign.state.get("chase") or {}).get("active", False):
            raise _support.CombatEngineError("a chase is already active")
        rules_context = self.effective_rule_context(
            campaign_id,
            facts={
                "scene_id": scene_id,
                "initial_distance_ft": initial_distance_ft,
            },
        )
        actor_snapshots: list[dict[str, Any]] = []
        for identifier in participant_ids:
            record = self.require_campaign_actor(campaign_id, identifier)
            if int(dict(record.sheet.get("combat") or {}).get("hp", {}).get("value", 0) or 0) <= 0:
                raise _support.CombatEngineError("incapacitated actors cannot start a chase")
            snapshot = self.character_view(record, rules_context=rules_context)
            participant_overrides = config_by_actor.get(identifier, {})
            snapshot.update(
                {
                    key: _support.deepcopy(participant_overrides[key])
                    for key in ("initiative", "tie_breaker")
                    if key in participant_overrides
                }
            )
            if "speed_adjustment_ft" in participant_overrides:
                snapshot["chase_speed_adjustment_ft"] = int(
                    participant_overrides["speed_adjustment_ft"]
                )
                snapshot["chase_speed_source_excerpt"] = str(
                    participant_overrides["source_excerpt"]
                )
            actor_snapshots.append(snapshot)
        chase = _support.start_chase(
            actor_snapshots,
            quarry_ids=quarry_ids,
            initial_distance_ft=initial_distance_ft,
            ruleset=self.campaign_rules_edition(campaign.id),
            scene_id=scene_id,
            name=name,
            close_transition=(
                {
                    key: resolved_close_transition[key]
                    for key in ("distance_ft", "status", "summary")
                }
                if resolved_close_transition
                else None
            ),
        )
        chase["source_ref"] = evidence
        if resolved_close_transition:
            chase["close_transition"]["source_ref"] = resolved_close_transition["source_ref"]
        next_state = _support.deepcopy(campaign.state)
        next_state["chase"] = chase
        receipts = _support.core_receipts(
            rules_context,
            list(chase["rule_boundary_ids"]),
            "chase.start",
        )
        response = {
            "status": "committed",
            "chase": chase,
            "campaign_revision": campaign.revision + 1,
            "rule_receipts": receipts,
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            expected_campaign_revision=expected_revision,
            operation="chase.start",
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

    def chase_query(
        self,
        campaign_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Inspect the current or most recently closed chase and canonical actors."""
        self.access.require_campaign(campaign_id, principal_id)
        campaign = self.campaigns.get(campaign_id)
        chase = _support.deepcopy(dict(campaign.state.get("chase") or {}))
        actor_views = []
        for item in chase.get("participants", []):
            try:
                actor_views.append(self.character_view(self.characters.get(str(item["actor_id"]))))
            except LookupError:
                continue
        return {
            "chase": chase or None,
            "current": _support.current_chase_participant(chase) if chase else None,
            "actors": actor_views,
            "campaign_revision": campaign.revision,
        }

    def chase_take_turn(
        self,
        campaign_id: str,
        actor_id: str,
        action: Literal["dash", "move", "drop_out"] = "dash",
        complication_choice: str = "",
        stand_from_prone: bool = True,
        quarry_visibility: dict[str, bool] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Settle one ordered chase turn and the DMG urban complication stream."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "action": action,
            "complication_choice": complication_choice,
            "stand_from_prone": stand_from_prone,
            "quarry_visibility": quarry_visibility or {},
            "branch_id": resolved_branch_id,
        }
        scope = f"chase-turn:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        chase = _support.deepcopy(dict(campaign.state.get("chase") or {}))
        if not chase.get("active", False):
            raise _support.CombatEngineError("chase is not active")
        normalized_quarry_visibility = (
            {str(identifier): visible for identifier, visible in quarry_visibility.items()}
            if isinstance(quarry_visibility, dict)
            else {}
        )
        if set(normalized_quarry_visibility) != {
            str(identifier) for identifier in chase.get("quarry_ids", [])
        } or any(
            not isinstance(visible, bool) for visible in normalized_quarry_visibility.values()
        ):
            raise _support.CombatEngineError(
                "each chase turn requires an explicit boolean visibility fact for every quarry"
            )
        current_actor = self.require_campaign_actor(campaign_id, actor_id)
        if current_actor.revision != expected_actor_revision:
            raise ValueError(
                "character revision conflict: "
                f"expected {expected_actor_revision}, found {current_actor.revision}"
            )
        rules_context = self.effective_rule_context(
            campaign_id,
            facts={
                "chase_id": chase.get("id"),
                "actor_id": actor_id,
                "round": chase.get("round"),
                "action": action,
            },
        )
        settled = _support.advance_chase_turn(
            chase,
            self.character_view(current_actor, rules_context=rules_context),
            actor_id_value=actor_id,
            action=action,
            complication_choice=complication_choice,
            stand_from_prone=stand_from_prone,
            quarry_visibility=normalized_quarry_visibility,
            quarry_actors={
                str(identifier): self.character_view(
                    self.require_campaign_actor(campaign_id, str(identifier)),
                    rules_context=rules_context,
                )
                for identifier in chase.get("quarry_ids", [])
            },
            death_saves=current_actor.character_type == "pc",
            rules=rules_context,
        )
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state))
        next_state["chase"] = settled["chase"]
        round_changed = int(settled["chase"].get("round", 1) or 1) > int(chase.get("round", 1) or 1)
        time_transition: dict[str, Any] | None = None
        elapsed_ticks = 0
        elapsed_minutes = 0
        world_advanced: list[str] = []
        world_expired: list[str] = []
        if round_changed:
            self.require_resolved_short_rest_hit_dice(
                campaign_id,
                next_state,
                operation="advancing a chase round",
            )
            next_state, time_transition = self.advance_state_game_time(
                next_state,
                period="round",
            )
            elapsed_ticks = int(time_transition["elapsed_ticks"])
            elapsed_minutes = int(time_transition["elapsed_minutes"])
            world_duration = self.advance_world_effect_clocks(
                next_state,
                elapsed_ticks=elapsed_ticks,
                period_steps={"round": 1},
            )
            next_state = world_duration["state"]
            world_advanced.extend(world_duration["advanced"])
            world_expired.extend(world_duration["expired"])
        character_updates: list[_support.CharacterStateUpdate] = []
        expired: dict[str, list[str]] = {}
        advanced: dict[str, list[str]] = {}
        receipts: list[dict[str, Any]] = []
        all_characters = self.characters.list(campaign_id=campaign_id)
        for character in all_characters:
            sheet = settled["sheet"] if character.id == current_actor.id else character.sheet
            actor_advanced: list[str] = []
            actor_expired: list[str] = []
            if round_changed:
                round_duration = _support.advance_effect_durations(
                    sheet,
                    period="round",
                )
                sheet = round_duration["sheet"]
                actor_advanced.extend(round_duration["advanced"])
                actor_expired.extend(round_duration["expired"])
                elapsed_duration = _support.advance_elapsed_effect_durations(
                    sheet,
                    elapsed_ticks=elapsed_ticks,
                    advance_breathing=False,
                )
                sheet = elapsed_duration["sheet"]
                actor_advanced.extend(elapsed_duration["advanced"])
                actor_expired.extend(elapsed_duration["expired"])
                duration_extension = _support.apply_rule_event(
                    sheet,
                    "duration.advance",
                    _support.context_with_facts(
                        rules_context,
                        actor_id=character.id,
                        period="round",
                        amount=1,
                        elapsed_ticks=elapsed_ticks,
                        elapsed_minutes=elapsed_minutes,
                    ),
                )
                sheet = duration_extension.sheet
                receipts.extend(duration_extension.receipts)
            if sheet != character.sheet:
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=character.id,
                        sheet=_support.validate_character_sheet(sheet, rules=rules_context),
                        notes=_support.validate_character_notes(
                            character.notes,
                            character_type=character.character_type,
                        ),
                        expected_revision=(
                            expected_actor_revision
                            if character.id == current_actor.id
                            else character.revision
                        ),
                    )
                )
            if actor_advanced:
                advanced[character.id] = list(dict.fromkeys(actor_advanced))
            if actor_expired:
                expired[character.id] = list(dict.fromkeys(actor_expired))
        receipts.extend(
            _support.core_receipts(
                rules_context,
                list(_support.CHASE_BOUNDARY_IDS),
                "chase.turn",
            )
        )
        next_state, character_updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, character_updates, {}
        )
        updated_actor = current_actor
        for character_update in character_updates:
            if character_update.character_id == current_actor.id:
                updated_actor = _support.replace(
                    current_actor,
                    sheet=character_update.sheet,
                    notes=character_update.notes,
                    revision=current_actor.revision + 1,
                )
                break
        response = {
            "status": "committed",
            "chase": settled["chase"],
            "turn": settled["turn"],
            "character": self.character_view(updated_actor),
            "game_time": (
                time_transition["after"] if time_transition is not None else next_state["game_time"]
            ),
            "world_time": _support.deepcopy(next_state.get("world_time")),
            "advanced": advanced,
            "expired": expired,
            "world_advanced": list(dict.fromkeys(world_advanced)),
            "world_expired": list(dict.fromkeys(world_expired)),
            "campaign_revision": campaign.revision + 1,
            "rule_receipts": receipts,
        }
        stream = _support.active_random_stream()
        if stream is not None and stream.draw_count > 0:
            response["random_stream_receipt"] = stream.receipt()
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            character_updates=character_updates,
            expected_campaign_revision=expected_revision,
            operation="chase.turn",
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

    def chase_end(
        self,
        campaign_id: str,
        status: _support.ChaseManualOutcomeStatus,
        summary: str,
        source_ref: dict[str, Any],
        source_excerpt: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Close a chase only at an exact reviewed source or DM outcome boundary."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        chase = _support.deepcopy(dict(campaign.state.get("chase") or {}))
        scene_id = str(chase.get("scene_id") or "")
        evidence = self.reviewed_chase_source(
            campaign_id,
            scene_id=scene_id,
            source_ref=source_ref,
            source_excerpt=source_excerpt,
        )
        payload = {
            "status": status,
            "summary": summary,
            "source_ref": evidence,
            "branch_id": resolved_branch_id,
        }
        scope = f"chase-end:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        closed = _support.end_chase(chase, status=status, summary=summary)
        closed["outcome"]["source_ref"] = evidence
        next_state = _support.deepcopy(campaign.state)
        next_state["chase"] = closed
        rules_context = self.effective_rule_context(
            campaign_id,
            facts={"chase_id": chase.get("id"), "status": status},
        )
        receipts = _support.core_receipts(
            rules_context,
            ["dnd5e.core.chase.ending"],
            "chase.end",
        )
        response = {
            "status": "committed",
            "chase": closed,
            "campaign_revision": campaign.revision + 1,
            "rule_receipts": receipts,
        }
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            expected_campaign_revision=expected_revision,
            operation="chase.end",
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

    def combat_start(
        self,
        campaign_id: str,
        participant_ids: list[str],
        positioning_mode: Literal["grid", "agent"],
        participant_config: list[dict[str, Any]] | None = None,
        participant_manifest: dict[str, Any] | None = None,
        name: str = "Combat",
        scene_id: str | None = None,
        scope_id: str = "party",
        battle_map: dict[str, Any] | None = None,
        battle_map_template_id: str | None = None,
        battle_map_override_reason: str | None = None,
        ruleset: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one encounter from existing canonical campaign actor IDs.

        For a module encounter, pass its scene_id and source-backed
        participant_manifest after module_query(view="preflight"). Omitting
        scene_id creates an ad-hoc encounter and does not validate module progress.
        Read the current scene rather than inventing a replacement encounter.
        positioning_mode="agent" uses explicit DM spatial decisions without a
        fabricated grid; grid mode requires an actual map or declared override.
        participant_config supplies encounter facts, not replacement hp/max_hp.
        Initiative ties may require corrected tie_breaker values before startup.
        Reuse returned combat state/revisions; execute actors in returned turn order.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        participant_config = participant_config or []
        campaign = self.campaigns.get(campaign_id)
        authoritative_ruleset = self.campaign_rules_edition(campaign_id)
        if ruleset is not None and str(ruleset) != authoritative_ruleset:
            raise _support.CombatEngineError(
                "combat ruleset must match the authoritative campaign rule profile"
            )
        payload = {
            "participant_ids": list(participant_ids),
            "positioning_mode": positioning_mode,
            "participant_config": participant_config,
            "participant_manifest": participant_manifest,
            "name": name,
            "scene_id": scene_id,
            "scope_id": scope_id,
            "battle_map": battle_map,
            "battle_map_template_id": battle_map_template_id,
            "battle_map_override_reason": battle_map_override_reason,
            "ruleset": authoritative_ruleset,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-start:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            _support.validate_party_state(_support.deepcopy(campaign.state or {})),
            operation="starting combat",
        )
        if not participant_ids:
            raise ValueError("participant_ids must not be empty")
        if isinstance(campaign.state, dict) and campaign.state.get("combat", {}).get("active"):
            raise _support.CombatEngineError(
                "combat is already active; end it before starting another encounter"
            )
        if dict(campaign.state.get("chase") or {}).get("active", False):
            raise _support.CombatEngineError("end the active chase before starting combat")
        if self.npc_conversations.active_ids(
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
        ):
            raise _support.CombatEngineError(
                "close or abort the active NPC conversation before starting combat"
            )
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("participant_ids must be unique")
        config_by_actor: dict[str, dict[str, Any]] = {}
        for entry in participant_config:
            if not isinstance(entry, dict) or not entry.get("actor_id"):
                raise ValueError("each participant_config entry needs actor_id")
            actor_id_value = str(entry["actor_id"])
            if actor_id_value not in participant_ids:
                raise ValueError("participant_config actor_id must be a participant")
            if actor_id_value in config_by_actor:
                raise ValueError("participant_config actor_id must be unique")
            allowed = {
                "actor_id",
                "token_id",
                "position",
                "deployment_zone_id",
                "hidden",
                "visible_to_actor_ids",
                "disposition",
                "reach_ft",
                "can_share_space",
                "surprised",
                "death_saves",
                "initiative",
                "initiative_group_id",
                "tie_breaker",
                "source_conditions",
            }
            unknown = set(entry) - allowed
            if unknown:
                raise ValueError(f"unsupported participant config fields: {sorted(unknown)}")
            visible_to = entry.get("visible_to_actor_ids")
            if visible_to is not None:
                if not isinstance(visible_to, list) or any(
                    str(item) not in participant_ids for item in visible_to
                ):
                    raise ValueError("visible_to_actor_ids must contain only participant actor IDs")
            source_conditions = entry.get("source_conditions")
            if source_conditions is not None and (
                not isinstance(source_conditions, list)
                or any(not isinstance(item, dict) for item in source_conditions)
            ):
                raise ValueError("source_conditions must be a list of objects")
            config_by_actor[actor_id_value] = dict(entry)
        if battle_map_template_id is not None and battle_map is not None:
            raise _support.CombatEngineError(
                "battle_map_template_id and battle_map are mutually exclusive map authorities"
            )
        if positioning_mode == "agent" and battle_map_template_id is not None:
            raise _support.CombatEngineError(
                "agent-positioned combat does not accept a battle_map_template_id"
            )
        current_scene_context = self.modules.current_scene(campaign_id, scope_id=scope_id)
        scene_context = None
        resolved_scene_id = scene_id
        if resolved_scene_id is None:
            scene_context = current_scene_context
            if scene_context is not None:
                resolved_scene_id = str(scene_context["scene_id"])
        if resolved_scene_id is not None and scene_context is None:
            scene_context = (
                current_scene_context
                if current_scene_context is not None
                and str(current_scene_context["scene_id"]) == resolved_scene_id
                else self.modules.read_scene(campaign_id, resolved_scene_id)
            )
        preflight = None
        if participant_manifest is not None:
            if resolved_scene_id is None:
                raise ValueError("participant_manifest requires an encounter scene_id")
            preflight = self.module_scene_preflight(
                campaign_id,
                resolved_scene_id,
                participant_manifest,
                principal_id,
            )
            if not preflight["ready"]:
                unavailable_groups = [
                    item["key"]
                    for item in preflight["groups"]
                    if item["blocking"] and (item["missing_count"] or item["invalid_count"])
                ]
                raise _support.CombatEngineError(
                    "scene participant manifest has missing or unusable groups: "
                    + ", ".join(unavailable_groups)
                )
            omitted = sorted(set(preflight["initial_actor_ids"]) - set(participant_ids))
            if omitted:
                raise _support.CombatEngineError(
                    "combat participant_ids omit manifest combatants: " + ", ".join(omitted)
                )
            premature = sorted(set(preflight["reinforcement_actor_ids"]) & set(participant_ids))
            if premature:
                raise _support.CombatEngineError(
                    "manifest reinforcements must enter through combat_join: "
                    + ", ".join(premature)
                )
        compiled_map = None
        if positioning_mode == "grid" and battle_map_template_id is not None:
            if scene_context is None:
                raise _support.CombatEngineError(
                    "battle_map_template_id requires an encounter scene from a "
                    "finalized module Pack"
                )
            try:
                template_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
                for candidate_scene in self.modules.scene_index(
                    campaign_id, module_id=str(scene_context["module_id"])
                ):
                    templates = _support.normalize_combat_grid_templates(
                        list(
                            dict(candidate_scene.get("profile_data") or {}).get(
                                "combat_grid_templates"
                            )
                            or []
                        )
                    )
                    template_matches.extend(
                        (candidate_scene, template)
                        for template in templates
                        if template["id"] == str(battle_map_template_id)
                    )
                if len(template_matches) != 1:
                    raise _support.BattleMapError(
                        "battle_map_template_id must resolve to exactly one template in the "
                        "encounter module"
                    )
                template_scene, template = template_matches[0]
                archived = self._content_pack_module_archive(
                    campaign_id, str(scene_context["module_id"])
                )
                if archived is None:
                    raise _support.BattleMapError(
                        "battle-map templates require an installed finalized module Pack archive"
                    )
                package, _template_blobs, _template_artifact = archived
                authority_receipt = {
                    "schema_version": 1,
                    "kind": "content_pack_template",
                    "package_id": str(package["id"]),
                    "package_version": str(package["version"]),
                    "package_checksum": str(package["checksum"]),
                    "template_id": str(template["id"]),
                    "template_checksum": _support.json_sha256(template),
                    "scene_stable_key": str(template_scene.get("stable_key") or ""),
                }
                map_scene_context = self.modules.read_scene(
                    campaign_id, str(template_scene["scene_id"])
                )
                map_scene_context = {
                    **map_scene_context,
                    "encounter_scene_id": resolved_scene_id,
                }
                compiled_map = _support.compile_battle_map_template(
                    map_scene_context,
                    template,
                    authority_receipt=authority_receipt,
                )
            except _support.BattleMapError as error:
                raise _support.NeedsRulingError(
                    str(error), missing=("battle_map_template_id",)
                ) from error
            for entry in config_by_actor.values():
                try:
                    _support.validate_position(compiled_map, entry.get("position"))
                except _support.BattleMapError as error:
                    raise _support.CombatEngineError(str(error)) from error
        elif positioning_mode == "grid" and scene_context is not None:
            try:
                explicit_battle_map_request = _support.deepcopy(battle_map or {})
                battle_map_request = _support.deepcopy(battle_map or {})
                progress_context = scene_context
                if current_scene_context is not None and current_scene_context.get(
                    "module_id"
                ) == scene_context.get("module_id"):
                    progress_context = current_scene_context
                progress = dict(progress_context.get("progress") or {})
                progress_location_key = progress.get("current_location_key")
                if progress_location_key and not battle_map_request.get("location_key"):
                    battle_map_request["location_key"] = progress_location_key
                map_scene_context = scene_context
                requested_location_key = battle_map_request.get("location_key")
                scene_location_keys = {
                    str(item.get("key"))
                    for item in dict(scene_context.get("spatial") or {}).get("locations", [])
                    if isinstance(item, dict) and item.get("key")
                }
                if requested_location_key and requested_location_key not in scene_location_keys:
                    progress_state = dict(progress.get("state") or {})
                    location_scene_id = progress_state.get("location_scene_id")
                    spatial_candidates = []
                    if location_scene_id:
                        location_scene = self.modules.read_scene(
                            campaign_id, str(location_scene_id)
                        )
                        if location_scene.get("module_id") != scene_context.get("module_id"):
                            raise _support.BattleMapError(
                                "progress location_scene_id must belong to the encounter module"
                            )
                        spatial_candidates = [location_scene]
                    else:
                        spatial_candidates = [
                            item
                            for item in self.modules.scene_index(
                                campaign_id, module_id=scene_context.get("module_id")
                            )
                            if requested_location_key
                            in {
                                str(location.get("key"))
                                for location in dict(item.get("spatial") or {}).get("locations", [])
                                if isinstance(location, dict) and location.get("key")
                            }
                        ]
                    if len(spatial_candidates) != 1:
                        raise _support.BattleMapError(
                            "battle-map location_key must identify exactly one spatial "
                            "location in the encounter module"
                        )
                    candidate_keys = {
                        str(location.get("key"))
                        for location in dict(spatial_candidates[0].get("spatial") or {}).get(
                            "locations", []
                        )
                        if isinstance(location, dict) and location.get("key")
                    }
                    if requested_location_key not in candidate_keys:
                        raise _support.BattleMapError(
                            "progress location_scene_id does not contain current_location_key"
                        )
                    map_scene_context = {
                        **spatial_candidates[0],
                        "encounter_scene_id": resolved_scene_id,
                    }
                compiled_map = _support.compile_battle_map(map_scene_context, battle_map_request)
                mechanical_override_fields = sorted(
                    set(explicit_battle_map_request) - {"location_key"}
                )
                if mechanical_override_fields:
                    reason = str(
                        battle_map_override_reason or "Explicit DM battle-map override."
                    ).strip()
                    if len(reason) > 2000:
                        raise _support.BattleMapError(
                            "battle_map_override_reason must not exceed 2000 characters"
                        )
                    compiled_map["authority_receipt"] = {
                        "schema_version": 1,
                        "kind": "dm_override",
                        "principal_id": principal_id,
                        "reason": reason,
                        "request_checksum": _support.json_sha256(explicit_battle_map_request),
                        "fields": mechanical_override_fields,
                    }
                else:
                    if battle_map_override_reason is not None:
                        raise _support.BattleMapError(
                            "battle_map_override_reason requires a mechanical battle_map override"
                        )
                    compiled_map["authority_receipt"] = {
                        "schema_version": 1,
                        "kind": "scene_spatial",
                        "location_key": compiled_map["source"].get("location_key"),
                    }
                compiled_map["checksum"] = _support.json_sha256(
                    {key: value for key, value in compiled_map.items() if key != "checksum"}
                )
                for entry in config_by_actor.values():
                    _support.validate_position(compiled_map, entry.get("position"))
            except _support.BattleMapError as error:
                raise _support.NeedsRulingError(str(error), missing=("battle_map",)) from error
        elif positioning_mode == "grid" and battle_map is not None:
            try:
                mechanical_override_fields = sorted(set(battle_map) - {"location_key"})
                reason = str(
                    battle_map_override_reason or "Explicit DM battle-map override."
                ).strip()
                if mechanical_override_fields and len(reason) > 2000:
                    raise _support.BattleMapError(
                        "battle_map_override_reason must not exceed 2000 characters"
                    )
                if not mechanical_override_fields and battle_map_override_reason is not None:
                    raise _support.BattleMapError(
                        "battle_map_override_reason requires a mechanical battle_map override"
                    )
                compiled_map = _support.compile_battle_map(
                    {
                        "scene_id": resolved_scene_id or f"ad-hoc-combat:{campaign_id}",
                        "spatial": {},
                    },
                    _support.deepcopy(battle_map),
                )
                compiled_map["authority_receipt"] = (
                    {
                        "schema_version": 1,
                        "kind": "dm_override",
                        "principal_id": principal_id,
                        "reason": reason,
                        "request_checksum": _support.json_sha256(battle_map),
                        "fields": mechanical_override_fields,
                    }
                    if mechanical_override_fields
                    else {
                        "schema_version": 1,
                        "kind": "scene_spatial",
                        "location_key": compiled_map["source"].get("location_key"),
                    }
                )
                compiled_map["checksum"] = _support.json_sha256(
                    {key: value for key, value in compiled_map.items() if key != "checksum"}
                )
                for entry in config_by_actor.values():
                    _support.validate_position(compiled_map, entry.get("position"))
            except _support.BattleMapError as error:
                raise _support.NeedsRulingError(str(error), missing=("battle_map",)) from error
        elif positioning_mode == "agent":
            if battle_map is not None:
                raise _support.CombatEngineError(
                    "agent-positioned combat does not accept a battle_map"
                )
            positioned_actor_ids = sorted(
                actor_id_value
                for actor_id_value, entry in config_by_actor.items()
                if entry.get("position") is not None
            )
            if positioned_actor_ids:
                raise _support.CombatEngineError(
                    "agent-positioned combat does not accept participant coordinates: "
                    + ", ".join(positioned_actor_ids)
                )
        if positioning_mode == "grid":
            if compiled_map is None:
                raise _support.CombatEngineError("grid combat requires a compiled battle map")
            missing_positions = sorted(
                actor_id_value
                for actor_id_value in participant_ids
                if not isinstance(
                    dict(config_by_actor.get(actor_id_value) or {}).get("position"),
                    dict,
                )
            )
            if missing_positions:
                raise _support.CombatEngineError(
                    "grid combat requires positions for every participant: "
                    + ", ".join(missing_positions)
                )
            deployment_zones = {
                str(zone["id"]): set(zone.get("cells") or [])
                for zone in compiled_map.get("deployment_zones") or []
                if isinstance(zone, dict) and zone.get("id")
            }
            declared_zone_actor_ids = {
                actor_id_value
                for actor_id_value, entry in config_by_actor.items()
                if entry.get("deployment_zone_id") is not None
            }
            if deployment_zones:
                missing_zones = sorted(set(participant_ids) - declared_zone_actor_ids)
                if missing_zones:
                    raise _support.CombatEngineError(
                        "template-backed grid combat requires a deployment_zone_id for every "
                        "participant: " + ", ".join(missing_zones)
                    )
                for actor_id_value in participant_ids:
                    entry = dict(config_by_actor.get(actor_id_value) or {})
                    zone_id = str(entry.get("deployment_zone_id") or "")
                    if zone_id not in deployment_zones:
                        raise _support.CombatEngineError(
                            f"participant {actor_id_value} has an unknown deployment_zone_id"
                        )
                    position = dict(entry["position"])
                    if f"{position['x']},{position['y']}" not in deployment_zones[zone_id]:
                        raise _support.CombatEngineError(
                            f"participant {actor_id_value} position is outside deployment zone "
                            f"{zone_id}"
                        )
            elif declared_zone_actor_ids:
                raise _support.CombatEngineError(
                    "deployment_zone_id is accepted only by a template with deployment zones"
                )
        participants = [self.characters.get(item) for item in participant_ids]
        if any(char.campaign_id != campaign_id for char in participants):
            raise ValueError("all participants must belong to the campaign")
        narrative_only_ids = [
            str(character.id) for character in participants if self.narrative_only_actor(character)
        ]
        if narrative_only_ids:
            raise _support.CombatEngineError(
                "narrative-only actors cannot enter combat without an exact statblock: "
                + ", ".join(narrative_only_ids)
            )
        source_condition_records: list[dict[str, Any]] = []
        source_condition_sheets: dict[str, dict[str, Any]] = {}
        source_condition_characters = {character.id: character for character in participants}
        for actor in participants:
            actor_id_value = actor.id
            config_entry = config_by_actor.get(actor_id_value, {})
            _, conditions, sheet = self.source_participant_rules(
                campaign_id,
                resolved_scene_id,
                actor,
                config_entry,
            )
            source_condition_records.extend(conditions)
            if sheet is not None:
                source_condition_sheets[actor_id_value] = sheet
        dependent_turn_contracts = self._steel_defender_turn_contracts(
            campaign_id,
            resolved_branch_id,
            set(participant_ids),
        )
        actors = []
        for character_id in participant_ids:
            actor = self.combat_actor_snapshot(character_id)
            actor_config = dict(config_by_actor.get(character_id, {}))
            actor_config.pop("source_conditions", None)
            actor_config.pop("deployment_zone_id", None)
            actor.update(actor_config)
            if character_id in dependent_turn_contracts:
                actor["dependent_turn"] = _support.deepcopy(dependent_turn_contracts[character_id])
            if character_id in source_condition_sheets:
                actor["sheet"] = _support.validate_character_sheet(
                    source_condition_sheets[character_id],
                    rules=self.effective_rule_context(campaign_id),
                )
            actors.append(actor)
        encounter = _support.start_encounter(
            actors,
            ruleset=authoritative_ruleset,
            scene_id=resolved_scene_id,
            name=name,
            battle_map=compiled_map,
            positioning_mode=positioning_mode,
        )
        if preflight is not None:
            encounter["participant_manifest"] = preflight
        if compiled_map and compiled_map.get("deployment_zones"):
            encounter["deployment_assignments"] = [
                {
                    "actor_id": actor_id_value,
                    "deployment_zone_id": str(
                        config_by_actor[actor_id_value]["deployment_zone_id"]
                    ),
                    "position": _support.deepcopy(config_by_actor[actor_id_value]["position"]),
                }
                for actor_id_value in participant_ids
            ]
        if source_condition_records:
            encounter["source_conditions"] = source_condition_records
        initial_sheets = {
            str(character.id): _support.deepcopy(
                source_condition_sheets.get(str(character.id), character.sheet)
            )
            for character in participants
        }
        for actor_id_value, sheet in initial_sheets.items():
            self.sync_combatant_conditions(encounter, actor_id_value, sheet)
        initiatives = [
            int(item.get("initiative", 0) or 0) for item in encounter.get("combatants", [])
        ]
        start_boundary_ids = list(encounter.get("rule_boundary_ids") or [])
        if len(initiatives) != len(set(initiatives)):
            start_boundary_ids.append("dnd5e.core.initiative.tie")
        start_boundary_ids = sorted(set(start_boundary_ids))
        start_receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id),
            start_boundary_ids,
            "combat.start",
        )
        updated_state = dict(campaign.state or {})
        updated_state["combat"] = encounter
        # The active encounter is the sole Combat authority.  ``game_phase``
        # records only the non-combat Lobby/Play lifecycle.
        updated_state["game_phase"] = _support.PROFILE_PLAY
        updated_state = _support._with_combat_mutation_lock(updated_state, active=True)
        updated_state = _support.validate_party_state(updated_state)
        source_start_updates = [
            _support.CharacterStateUpdate(
                character_id=actor_id_value,
                sheet=_support.validate_character_sheet(
                    sheet,
                    rules=self.effective_rule_context(campaign_id),
                ),
                notes=_support.validate_character_notes(
                    source_condition_characters[actor_id_value].notes
                ),
                expected_revision=source_condition_characters[actor_id_value].revision,
            )
            for actor_id_value, sheet in initial_sheets.items()
            if sheet != source_condition_characters[actor_id_value].sheet
            or actor_id_value in source_condition_sheets
        ]
        updated_state, source_start_updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, updated_state, source_start_updates, {}
        )
        updated_state, source_start_updates, start_fields = self.reconcile_unconscious_inventory(
            campaign, updated_state, source_start_updates, {"combat": encounter}
        )
        self.validate_inventory_custody_update(campaign, updated_state, source_start_updates)
        encounter = updated_state["combat"]

        def start_response(revisions: list[Any]) -> dict[str, Any]:
            return {
                **start_fields,
                "combat": encounter,
                "tool_profile": _support.PROFILE_COMBAT,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=updated_state,
            character_updates=source_start_updates,
            expected_campaign_revision=campaign.revision,
            operation="combat.start",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=start_response,
            ),
            rule_receipts=start_receipts,
        )
        return self.combat_response(
            campaign_id,
            principal_id,
            start_response(list(revisions_result or [])),
        )

    def combat_join(
        self,
        campaign_id: str,
        actor_id: str,
        participant_config: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Queue one canonical campaign actor to enter combat at the next round."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        config_value = dict(participant_config or {})
        allowed = {
            "token_id",
            "position",
            "hidden",
            "visible_to_actor_ids",
            "disposition",
            "reach_ft",
            "can_share_space",
            "surprised",
            "death_saves",
            "initiative",
            "initiative_group_id",
            "tie_breaker",
            "join_round",
            "source_conditions",
        }
        unknown = set(config_value) - allowed
        if unknown:
            raise ValueError(f"unsupported participant config fields: {sorted(unknown)}")
        source_conditions = config_value.get("source_conditions")
        if source_conditions is not None and (
            not isinstance(source_conditions, list)
            or any(not isinstance(item, dict) for item in source_conditions)
        ):
            raise ValueError("source_conditions must be a list of objects")
        payload = {
            "actor_id": actor_id,
            "participant_config": _support.deepcopy(config_value),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-join:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        self.require_no_blocking_pending(encounter)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        joining_actor = self.require_campaign_actor(campaign_id, actor_id)
        if self.narrative_only_actor(joining_actor):
            raise _support.CombatEngineError(
                "narrative-only actors cannot enter combat without an exact statblock"
            )
        visible_to = config_value.get("visible_to_actor_ids")
        encounter_actor_ids = (
            {str(item.get("actor_id") or "") for item in encounter.get("combatants", [])}
            | {str(item.get("actor_id") or "") for item in encounter.get("reinforcements", [])}
            | {actor_id}
        )
        if visible_to is not None and (
            not isinstance(visible_to, list)
            or any(str(item) not in encounter_actor_ids for item in visible_to)
        ):
            raise ValueError(
                "visible_to_actor_ids must contain only current or joining participant IDs"
            )
        battle_map = encounter.get("battle_map")
        if isinstance(battle_map, dict):
            try:
                _support.validate_position(battle_map, config_value.get("position"))
            except _support.BattleMapError as error:
                raise _support.NeedsRulingError(str(error), missing=("position",)) from error
        join_round = config_value.pop("join_round", None)
        if join_round is not None and (
            isinstance(join_round, bool)
            or not isinstance(join_round, int)
            or join_round <= int(encounter.get("round", 1) or 1)
        ):
            raise ValueError("join_round must be an integer after the current combat round")
        _, joining_conditions, joining_sheet = self.source_participant_rules(
            campaign_id,
            str(encounter.get("scene_id") or "") or None,
            joining_actor,
            config_value,
        )
        config_value.pop("source_conditions", None)
        actor = self.combat_actor_snapshot(actor_id)
        actor.update(config_value)
        turn_contract = self._steel_defender_turn_contracts(
            campaign_id,
            resolved_branch_id,
            encounter_actor_ids,
        ).get(actor_id)
        if turn_contract is not None:
            actor["dependent_turn"] = _support.deepcopy(turn_contract)
        if joining_sheet is not None:
            actor["sheet"] = _support.validate_character_sheet(
                joining_sheet,
                rules=self.effective_rule_context(campaign_id),
            )
        next_encounter = _support.queue_combatant(
            encounter,
            actor,
            joins_round=join_round,
        )
        if joining_conditions:
            next_encounter["source_conditions"] = [
                *list(next_encounter.get("source_conditions") or []),
                *joining_conditions,
            ]
        queued = next(
            item
            for item in next_encounter.get("reinforcements", [])
            if item.get("actor_id") == actor_id
        )
        tied = any(
            item.get("actor_id") != actor_id
            and int(item.get("initiative", 0) or 0) == int(queued.get("initiative", 0) or 0)
            for item in [
                *list(next_encounter.get("combatants") or []),
                *list(next_encounter.get("reinforcements") or []),
            ]
        )
        join_boundary_ids = list(queued.get("rule_boundary_ids") or [])
        if tied:
            join_boundary_ids.append("dnd5e.core.initiative.tie")
        receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id),
            sorted(set(join_boundary_ids)),
            "combat.join",
        )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        character_updates = (
            [
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(
                        joining_sheet,
                        rules=self.effective_rule_context(campaign_id),
                    ),
                    notes=_support.validate_character_notes(joining_actor.notes),
                    expected_revision=joining_actor.revision,
                )
            ]
            if joining_sheet is not None
            else []
        )
        next_state, character_updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, character_updates, {}
        )
        next_state, character_updates, join_fields = self.reconcile_unconscious_inventory(
            campaign, next_state, character_updates, {"combat": next_encounter}
        )
        self.validate_inventory_custody_update(campaign, next_state, character_updates)
        next_encounter = next_state["combat"]
        queued = next(
            item for item in next_encounter["reinforcements"] if item.get("actor_id") == actor_id
        )

        def join_response(revisions: list[Any]) -> dict[str, Any]:
            return {
                **join_fields,
                "status": "committed",
                "queued": _support.deepcopy(queued),
                "combat": next_encounter,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            character_updates=character_updates,
            expected_campaign_revision=campaign.revision,
            operation="combat.participant.join",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=join_response,
            ),
            rule_receipts=receipts,
        )
        return self.combat_response(
            campaign_id,
            principal_id,
            join_response(list(revisions_result or [])),
        )

    def combat_status(
        self,
        campaign_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any] | None:
        """Read an audience-filtered structured encounter."""
        return self.combat_view(campaign_id, principal_id)

    def combat_available_actions(
        self,
        campaign_id: str,
        actor_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """List legal action categories without consuming a turn resource."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id
        )
        campaign = self.campaigns.get(campaign_id)
        campaign, encounter = self.active_encounter(campaign_id)
        combatant = next(
            (item for item in encounter.get("combatants", []) if item.get("actor_id") == actor_id),
            None,
        )
        if combatant is None:
            raise _support.CombatEngineError("actor is not a combatant")
        actions = _support.available_actions(encounter, actor_id)
        actor = self.combat_actor_snapshot(actor_id)
        hit_points = int(dict(actor.get("derived", {}).get("hit_points") or {}).get("value", 0))
        current = _support.current_combatant(encounter)
        if (
            current is not None
            and current.get("actor_id") == actor_id
            and bool(combatant.get("death_saves", False))
            and hit_points == 0
        ):
            actions = ["death_save"] if _support.death_save_due(combatant, actor["sheet"]) else []
        return {
            "actor_id": actor_id,
            "actions": actions,
            "turn_budget": combatant.get("turn_budget", {}),
        }

    def combat_on_hit_ruling(
        self,
        campaign_id: str,
        target_id: str,
        choice_id: str,
        selection: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Dismiss a reviewed no-op; executable on-hit effects use a persisted plan."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        normalized = _support.deepcopy(dict(selection or {}))
        if set(normalized) != {"id", "source_excerpt"} or normalized.get("id") != "dismiss":
            raise _support.CombatEngineError(
                "custom on-hit mechanics must be compiled with content_solution and "
                "settled with combat_choice(action='execute_plan'); on_hit_ruling only "
                "dismisses an Agent-reviewed no-op"
            )
        _campaign, encounter = self.active_encounter(campaign_id)
        window = next(
            (
                item
                for item in encounter.get("pending", [])
                if str(item.get("id") or "") == choice_id
            ),
            None,
        )
        if (
            not isinstance(window, dict)
            or window.get("kind") != "ruling"
            or window.get("trigger") != "attack_on_hit_effect"
            or str(window.get("target_id") or "") != target_id
        ):
            raise _support.CombatEngineError("choice_id is not this target's pending on-hit review")
        source_excerpt = str(normalized.get("source_excerpt") or "").strip()
        if (
            not source_excerpt
            or source_excerpt.casefold() != str(window.get("effect") or "").strip().casefold()
        ):
            raise _support.CombatEngineError("dismissal requires the exact reviewed source excerpt")
        return self.combat_choice_resolve(
            campaign_id,
            target_id,
            choice_id,
            {"id": "dismiss", "source_excerpt": source_excerpt},
            principal_id=principal_id,
            expected_revision=expected_revision,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
        )

    def combat_end_turn(
        self,
        campaign_id: str,
        actor_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Advance a structured encounter turn with optimistic concurrency."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        campaign = self.campaigns.get(campaign_id)
        payload = {"actor_id": actor_id, "branch_id": resolved_branch_id}
        scope = f"combat-end-turn:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        _, encounter = self.active_encounter(campaign_id)
        before_readied = list(encounter.get("readied", []))
        current = self.characters.get(actor_id)
        current_sheet = _support.deepcopy(current.sheet)
        next_encounter = _support.deepcopy(encounter)
        self.require_no_blocking_pending(next_encounter)
        duration = _support.advance_effect_durations(current_sheet, period="turn_end")
        ended_turn_token = self.encounter_turn_token(next_encounter)
        expired_standard_turn_end = self.expire_standard_source_turn_effects(
            next_encounter,
            actor_id=actor_id,
            phase="source_turn_end",
            turn_token=ended_turn_token,
        )
        next_state = dict(campaign.state or {})
        next_state["combat"] = _support.end_turn(
            next_encounter, actor_id_value=actor_id, current_actor_sheet=current_sheet
        )
        expired_attack_advantage = self.expire_next_attack_advantage(
            next_state["combat"],
            actor_id=actor_id,
            ended_round=int(encounter.get("round", 1) or 1),
        )
        remaining_readied_ids = {
            str(item.get("id")) for item in next_state["combat"].get("readied", [])
        }
        expired_readied = [
            item
            for item in before_readied
            if item.get("kind") == "spell" and str(item.get("id")) not in remaining_readied_ids
        ]
        next_combatant = _support.current_combatant(next_state["combat"])
        expired_standard_turn_start: list[str] = []
        if next_combatant is not None:
            next_actor_id = str(next_combatant.get("actor_id") or "")
            _support.record_death_save_turn_start(
                next_combatant, self.characters.get(next_actor_id).sheet
            )
            expired_standard_turn_start = self.expire_standard_source_turn_effects(
                next_state["combat"],
                actor_id=next_actor_id,
                phase="source_turn_start",
                turn_token=self.encounter_turn_token(next_state["combat"]),
            )
        round_changed = int(next_state["combat"].get("round", 1)) > int(encounter.get("round", 1))
        time_transition: dict[str, Any] | None = None
        if round_changed:
            self.require_resolved_short_rest_hit_dice(
                campaign_id,
                _support.validate_party_state(_support.deepcopy(next_state)),
                operation="advancing a combat round",
            )
            next_state, time_transition = self.advance_state_game_time(
                next_state,
                period="round",
            )
        elapsed_ticks = int(time_transition["elapsed_ticks"]) if time_transition is not None else 0
        elapsed_minutes = (
            int(time_transition["elapsed_minutes"]) if time_transition is not None else 0
        )
        minute_changed = elapsed_minutes > 0
        world_advanced: list[str] = []
        world_expired: list[str] = []
        world_time = _support.deepcopy(dict(next_state.get("world_time") or {}))
        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": 1} if round_changed else None,
        )
        next_state = world_duration["state"]
        world_advanced.extend(world_duration["advanced"])
        world_expired.extend(world_duration["expired"])
        source_sheets = {
            str(item.get("actor_id")): _support.deepcopy(
                duration["sheet"]
                if str(item.get("actor_id")) == actor_id
                else self.characters.get(str(item.get("actor_id"))).sheet
            )
            for item in next_state["combat"].get("combatants", [])
        }
        skipped_source_ids: list[str] = []
        for event in reversed(list(next_state["combat"].get("log") or [])):
            if event.get("type") != "turn_skipped" or event.get("reason") != "dead":
                break
            skipped_source_ids.append(str(event.get("actor_id") or ""))
        skipped_source_ids.reverse()
        source_turn_actor_ids = [
            *skipped_source_ids,
            *([str(next_combatant.get("actor_id") or "")] if next_combatant is not None else []),
        ]
        source_duration_expired: list[str] = []
        for target_id, sheet in list(source_sheets.items()):
            if elapsed_ticks:
                # Consume the one real clock interval before resolving the new
                # turn. Turn starts themselves never spend grace rounds.
                sheet = _support.advance_breathing_rounds(
                    sheet,
                    rounds=elapsed_ticks,
                    defer_drop_until_turn_start=True,
                )["sheet"]
            for source_actor_id in source_turn_actor_ids:
                source_duration = _support.advance_source_turn_effect_durations(
                    sheet,
                    source_actor_id=source_actor_id,
                )
                sheet = source_duration["sheet"]
                source_duration_expired.extend(source_duration["expired"])
            source_sheets[target_id] = sheet
        rule_context = self.effective_rule_context(campaign_id)
        started_effects_by_actor: dict[str, dict[str, Any]] = {}
        activity_recharges: list[dict[str, Any]] = []
        activity_recharge_receipts: list[dict[str, Any]] = []
        if next_combatant is not None:
            next_actor_id = str(next_combatant.get("actor_id") or "")
            try:
                recharged = _support.recharge_activities_at_turn_start(
                    source_sheets[next_actor_id],
                    rules=rule_context,
                )
            except _support.ActivityError as error:
                raise _support.CombatEngineError(str(error)) from error
            source_sheets[next_actor_id] = recharged["sheet"]
            activity_recharges = list(recharged["results"])
            activity_recharge_receipts = list(recharged.get("rule_receipts") or [])
            if activity_recharges:
                next_state["combat"]["log"] = [
                    *list(next_state["combat"].get("log") or []),
                    {
                        "type": "activity_recharge",
                        "actor_id": next_actor_id,
                        "results": activity_recharges,
                    },
                ][-100:]
            started_effects_by_actor[next_actor_id] = _support.advance_effect_durations(
                source_sheets[next_actor_id],
                period="turn_start",
            )
            source_sheets[next_actor_id] = started_effects_by_actor[next_actor_id]["sheet"]
        combat_updates: list[_support.CharacterStateUpdate] = []
        expired_effects = {
            *duration["expired"],
            *expired_attack_advantage,
            *expired_standard_turn_end,
            *expired_standard_turn_start,
            *source_duration_expired,
        }
        rule_receipts: list[dict[str, Any]] = _support.core_receipts(
            rule_context,
            ["dnd5e.core.mcp.duration_clock"],
            "turn.end.duration_clock",
        )
        rule_receipts.extend(activity_recharge_receipts)
        for combatant in next_state["combat"].get("combatants", []):
            target_id = str(combatant.get("actor_id"))
            target = self.characters.get(target_id)
            sheet = _support.deepcopy(source_sheets[target_id])
            for readied in expired_readied:
                if str(readied.get("actor_id")) != target_id:
                    continue
                for effect in sheet.get("effects", []):
                    if effect.get("id") == readied.get("holding_effect_id"):
                        effect["active"] = False
            expired: list[str] = []
            if target_id in started_effects_by_actor:
                expired.extend(started_effects_by_actor[target_id]["expired"])
            if round_changed:
                rounded = _support.advance_effect_durations(
                    sheet,
                    period="round",
                    advance_breathing=False,
                )
                sheet = rounded["sheet"]
                expired.extend(rounded["expired"])
            if elapsed_ticks:
                minutes = _support.advance_elapsed_effect_durations(
                    sheet,
                    elapsed_ticks=elapsed_ticks,
                    # Breathing consumed this interval before the turn-start pass.
                    advance_breathing=False,
                )
                sheet = minutes["sheet"]
                expired.extend(minutes["expired"])
            extension = _support.apply_rule_event(
                sheet,
                "duration.advance",
                _support.context_with_facts(
                    rule_context,
                    actor_id=target_id,
                    ended_actor_id=actor_id,
                    round_changed=round_changed,
                    minute_changed=minute_changed,
                ),
            )
            sheet = extension.sheet
            rule_receipts.extend(extension.receipts)
            if target_id == actor_id:
                ended = _support.apply_rule_event(
                    sheet,
                    "turn.end",
                    _support.context_with_facts(rule_context, actor_id=target_id),
                )
                sheet = ended.sheet
                rule_receipts.extend(ended.receipts)
            expired_effects.update(expired)
            self.sync_combatant_conditions(next_state["combat"], target_id, sheet)
            normalized_sheet = _support.validate_character_sheet(sheet)
            normalized_notes = _support.validate_character_notes(target.notes)
            if normalized_sheet != target.sheet or normalized_notes != target.notes:
                combat_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=normalized_sheet,
                        notes=normalized_notes,
                        expected_revision=target.revision,
                    )
                )
        if elapsed_ticks:
            combatant_ids = set(source_sheets)
            for target in self.characters.list(campaign_id=campaign_id):
                if target.id in combatant_ids:
                    continue
                minutes = _support.advance_elapsed_effect_durations(
                    target.sheet,
                    elapsed_ticks=elapsed_ticks,
                )
                extension = _support.apply_rule_event(
                    minutes["sheet"],
                    "duration.advance",
                    _support.context_with_facts(
                        rule_context,
                        actor_id=target.id,
                        ended_actor_id=actor_id,
                        round_changed=round_changed,
                        minute_changed=minute_changed,
                        elapsed_ticks=elapsed_ticks,
                    ),
                )
                rule_receipts.extend(extension.receipts)
                expired_effects.update(minutes["expired"])
                normalized_sheet = _support.validate_character_sheet(extension.sheet)
                normalized_notes = _support.validate_character_notes(target.notes)
                if normalized_sheet != target.sheet or normalized_notes != target.notes:
                    combat_updates.append(
                        _support.CharacterStateUpdate(
                            character_id=target.id,
                            sheet=normalized_sheet,
                            notes=normalized_notes,
                            expected_revision=target.revision,
                        )
                    )
        for ongoing in next_state["combat"].get("ongoing_effects", []):
            if (
                isinstance(ongoing, dict)
                and ongoing.get("active", True)
                and str(ongoing.get("id") or "") in expired_effects
            ):
                ongoing["active"] = False
                ongoing["resolution"] = {
                    "kind": "duration_expired",
                    "actor_id": actor_id,
                    "round": int(next_state["combat"].get("round", 1) or 1),
                }

        next_state, combat_updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, combat_updates, {}
        )
        next_state, combat_updates, steel_defender_lifecycle = self.reconcile_steel_defender_deaths(
            campaign,
            next_state,
            combat_updates,
            {},
            branch_id=resolved_branch_id,
        )
        assert next_state is not None

        def turn_end_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
                "status": "committed",
                "combat": next_state["combat"],
                "effects_expired": sorted(expired_effects),
                "game_time": _support.deepcopy(next_state.get("game_time")),
                "world_time": world_time or None,
                "world_advanced": list(dict.fromkeys(world_advanced)),
                "world_expired": list(dict.fromkeys(world_expired)),
                "readied_spells_expired": sorted(str(item.get("id")) for item in expired_readied),
                "activity_recharges": activity_recharges,
                "rule_receipts": rule_receipts,
                "ruleset_fingerprint": rule_context.fingerprint,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
                **steel_defender_lifecycle,
            }
            stream = _support.active_random_stream()
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=_support.validate_party_state(next_state),
            character_updates=combat_updates,
            expected_campaign_revision=campaign.revision,
            operation="combat.turn.end",
            actor=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=turn_end_response,
            ),
            rule_receipts=rule_receipts,
        )
        response = turn_end_response(list(revisions_result or []))
        return self.combat_response(
            campaign_id,
            principal_id,
            response,
        )

    def combat_move(
        self,
        campaign_id: str,
        actor_id: str,
        distance: int,
        destination: Any = None,
        path: list[Any] | None = None,
        movement_mode: str = "voluntary",
        travel_mode: str = "walk",
        crawl: bool = False,
        spatial_facts: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply movement with a reason and an independent travel-speed mode.

        Only voluntary movement spends the actor's turn budget or opens owned
        opportunity-reaction windows.  Effect-driven forced movement and
        teleportation may move a combatant off-turn without consuming speed.
        """
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "distance": distance,
            "destination": destination,
            "path": path,
            "movement_mode": movement_mode,
            "travel_mode": travel_mode,
            "crawl": crawl,
            "spatial_facts": spatial_facts,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-move:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        _, encounter = self.active_encounter(campaign_id)
        self.require_no_blocking_pending(encounter)
        normalized_spatial_facts = self.validate_agent_movement_facts(encounter, spatial_facts)
        moving_combatant = next(
            item for item in encounter.get("combatants", []) if item.get("actor_id") == actor_id
        )
        moving_conditions = {
            str(item).casefold() for item in moving_combatant.get("conditions", [])
        }
        pending_before = {str(item.get("id")) for item in encounter.get("pending", [])}
        next_encounter = _support.spend_movement(
            encounter,
            actor_id,
            distance,
            destination=destination,
            path=path,
            movement_mode=movement_mode,
            travel_mode=travel_mode,
            crawl=crawl,
            spatial_facts=normalized_spatial_facts,
        )
        range_reconciliation = _support.reconcile_witch_bolt_range(next_encounter)
        next_encounter = range_reconciliation["encounter"]
        ended_tethers = _support.newly_ended_witch_bolt_tethers(
            encounter,
            next_encounter,
        )
        concentration_updates: list[_support.CharacterStateUpdate] = []
        for caster_id in sorted(
            {
                str(item.get("source_actor_id") or "")
                for item in ended_tethers
                if str(item.get("source_actor_id") or "")
            }
        ):
            caster = self.characters.get(caster_id)
            caster_tethers = [
                item
                for item in ended_tethers
                if str(item.get("source_actor_id") or "") == caster_id
            ]
            ended = _support.end_tether_concentrations(
                caster.sheet,
                caster_tethers,
            )
            if ended["sheet"] == caster.sheet:
                continue
            self.sync_combatant_conditions(
                next_encounter,
                caster_id,
                ended["sheet"],
            )
            concentration_updates.append(
                _support.CharacterStateUpdate(
                    character_id=caster_id,
                    sheet=_support.validate_character_sheet(ended["sheet"]),
                    notes=_support.validate_character_notes(caster.notes),
                    expected_revision=caster.revision,
                )
            )
        movement_boundary_ids: list[str] = []
        normalized_movement_mode = str(movement_mode).strip().lower().replace("-", "_")
        if normalized_movement_mode == "aggressive":
            movement_boundary_ids.append(_support.CORE_ORC_AGGRESSIVE_MECHANIC_ID)
        if normalized_movement_mode in {"forced", "teleport"}:
            movement_boundary_ids.append("dnd5e.core.movement.forced_and_teleport")
        if "prone" in moving_conditions:
            movement_boundary_ids.append("dnd5e.core.movement.prone_crawl_stand")
        if "grappled" in moving_conditions:
            movement_boundary_ids.append("dnd5e.core.movement.grapple_source")
        if "turned" in moving_conditions:
            movement_boundary_ids.append("dnd5e.core.activity.turn_undead")
        if destination is not None:
            movement_boundary_ids.append("dnd5e.core.movement.occupied_destination")
        difficult_cells = set(dict(encounter.get("battle_map") or {}).get("difficult_cells") or [])
        route_points = list(path or ([] if destination is None else [destination]))
        if any(
            isinstance(point, dict) and f"{point.get('x')},{point.get('y')}" in difficult_cells
            for point in route_points
        ):
            movement_boundary_ids.append("dnd5e.core.movement.difficult_terrain")
        if any(
            str(item.get("id")) not in pending_before
            and item.get("kind") == "reaction"
            and item.get("trigger") == "opportunity_attack"
            for item in next_encounter.get("pending", [])
        ):
            movement_boundary_ids.append("dnd5e.core.reaction.opportunity_path")
        if ended_tethers:
            movement_boundary_ids.append(_support.CORE_WITCH_BOLT_MECHANIC_ID)
        movement_receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id),
            movement_boundary_ids,
            "movement.spend",
        )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.movement.spend",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "combat": next_encounter,
                "rule_receipts": movement_receipts,
                "ended_witch_bolt_tether_ids": [
                    str(item.get("id") or "") for item in ended_tethers
                ],
            },
            character_updates=concentration_updates,
            rule_receipts=movement_receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_stand(
        self,
        campaign_id: str,
        actor_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Stand from Prone by spending half the actor's speed, without using an action."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {"actor_id": actor_id, "branch_id": resolved_branch_id}
        scope = f"combat-stand:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        self.require_no_blocking_pending(encounter)
        next_encounter = _support.stand_up(encounter, actor_id)
        stand_receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id),
            ["dnd5e.core.movement.prone_crawl_stand"],
            "movement.stand",
        )
        current = self.characters.get(actor_id)
        updated_sheet = _support.deepcopy(current.sheet)
        _support.apply_condition_change(updated_sheet, condition_id="prone", add=False)
        self.sync_combatant_conditions(next_encounter, actor_id, updated_sheet)
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.stand",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated_sheet),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=stand_receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_common_action(
        self,
        campaign_id: str,
        actor_id: str,
        action: Literal[
            "command_dependent",
            "dash",
            "disengage",
            "dodge",
            "drop_held",
            "draw_weapon",
            "stow_weapon",
            "emerge_shell",
            "escape",
            "help",
            "hide",
            "influence",
            "interact_object",
            "improvise",
            "pickup_ground",
            "ready",
            "revive_steel_defender",
            "search",
            "shell_defense",
            "shake_hypnotic_pattern",
            "shake_sleep",
            "stabilize",
            "study",
            "sustain_spell",
            "use_object",
            "utilize",
        ],
        target_id: str | None = None,
        trigger: str | None = None,
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Settle a common action atomically. In 2014 combat, draw_weapon uses
        payload={item_id, slot: main_hand|off_hand}; stow_weapon uses {item_id}.
        Requires an owned weapon, empty destination hand, and current actor turn.
        Pays the free object interaction, otherwise an available action. Do not
        prepay interact_object or use inventory_change(equip) during combat.
        """
        if action in {"drop_held", "pickup_ground", "draw_weapon", "stow_weapon"}:
            if target_id is not None or trigger is not None:
                raise ValueError("ground inventory actions use only their exact payload")
            return self.ground_inventory_settlement(
                campaign_id,
                actor_id,
                action,
                dict(payload or {}),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                in_combat=True,
            )
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload_value = {
            "actor_id": actor_id,
            "action": action,
            "target_id": target_id,
            "trigger": trigger,
            "payload": payload or {},
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-common-action:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload_value)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        self.require_no_blocking_pending(encounter)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if str(action).strip().lower().replace("-", "_") == "cast":
            raise _support.CombatEngineError("use combat_cast_spell for spell resource settlement")
        if target_id is not None:
            self.require_campaign_actor(campaign_id, target_id)
        normalized_action = str(action).strip().lower().replace("-", "_")
        revival_relation_index: int | None = None
        revival_relations: list[dict[str, Any]] | None = None
        revival_relation: dict[str, Any] | None = None
        revival_contract: dict[str, str] | None = None
        revival_distance_ft: float | None = None
        revival_spatial_facts: dict[str, Any] | None = None
        revival_slot_level: int | None = None
        if normalized_action == "revive_steel_defender":
            if not target_id or trigger is not None:
                raise _support.CombatEngineError(
                    "revive_steel_defender requires a target and does not accept a trigger"
                )
            revival_payload = dict(payload or {})
            positioning_mode = str(encounter.get("positioning_mode") or "agent")
            expected_fields = (
                {"slot_level"} if positioning_mode == "grid" else {"slot_level", "spatial_facts"}
            )
            if set(revival_payload) != expected_fields:
                raise _support.CombatEngineError(
                    "revive_steel_defender requires its exact slot and positioning payload"
                )
            raw_slot_level = revival_payload.get("slot_level")
            if (
                isinstance(raw_slot_level, bool)
                or not isinstance(raw_slot_level, int)
                or not 1 <= raw_slot_level <= 9
            ):
                raise _support.CombatEngineError(
                    "Steel Defender revival slot_level must be from 1 to 9"
                )
            revival_slot_level = raw_slot_level
            candidate_relations = _support.validate_dependent_actor_relations(
                dict(campaign.state or {}).get("dependent_actor_relations", [])
            )
            matches = [
                (index, relation)
                for index, relation in enumerate(candidate_relations)
                if relation["owner_character_id"] == actor_id
                and relation["dependent_actor_id"] == target_id
                and relation["relation_key"] == _support.STEEL_DEFENDER_RELATION_KEY
            ]
            if len(matches) != 1 or matches[0][1]["status"] != "dead":
                raise _support.CombatEngineError(
                    "Steel Defender revival requires this owner's one dead defender relation"
                )
            revival_relation_index, revival_relation = matches[0]
            revival_relations = candidate_relations
            revival_contract = self._verified_steel_defender_relation(
                campaign_id,
                resolved_branch_id,
                revival_relation,
                require_current_parameters=False,
            )
            if positioning_mode == "grid":
                owner_combatant = self.require_encounter_combatant(
                    encounter,
                    actor_id,
                    role="Steel Defender owner",
                )
                defender_combatant = self.require_encounter_combatant(
                    encounter,
                    target_id,
                    role="dead Steel Defender",
                )
                cell_ft = int(
                    dict(dict(encounter.get("battle_map") or {}).get("grid") or {}).get(
                        "cell_ft", 5
                    )
                    or 5
                )
                distance = self.combat_distance(
                    owner_combatant.get("position"),
                    defender_combatant.get("position"),
                    cell_ft=cell_ft,
                )
                if distance is None:
                    raise _support.CombatEngineError(
                        "Steel Defender revival requires both grid token positions"
                    )
                revival_distance_ft = float(distance)
                revival_spatial_facts = {
                    "distance_ft": revival_distance_ft,
                    "source": "grid_token_positions",
                    "committed": True,
                }
            else:
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                raw_spatial = revival_payload.get("spatial_facts")
                if not isinstance(raw_spatial, dict) or set(raw_spatial) != {
                    "distance_ft",
                    "default_resolver",
                    "ruling_kind",
                    "reason",
                }:
                    raise _support.CombatEngineError(
                        "Steel Defender revival requires exact Agent spatial_facts"
                    )
                distance = raw_spatial.get("distance_ft")
                reason = " ".join(str(raw_spatial.get("reason") or "").split())
                if (
                    isinstance(distance, bool)
                    or not isinstance(distance, (int, float))
                    or not _support.math.isfinite(float(distance))
                    or not 0 <= float(distance) <= 5
                    or raw_spatial.get("default_resolver") != "agent"
                    or raw_spatial.get("ruling_kind") != "agent_dm_adjudication"
                    or not reason
                    or len(reason) > 500
                ):
                    raise _support.CombatEngineError(
                        "Steel Defender revival requires a bounded within-5-feet Agent ruling"
                    )
                revival_distance_ft = float(distance)
                revival_spatial_facts = {
                    **raw_spatial,
                    "distance_ft": revival_distance_ft,
                    "reason": reason,
                    "committed": True,
                }
        if normalized_action == "sustain_spell":
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
            sustain_payload = dict(payload or {})
            if set(sustain_payload) != {
                "effect_id",
                "target_total_cover",
                "agent_ruling",
            }:
                raise _support.CombatEngineError(
                    "sustain_spell requires exactly effect_id, target_total_cover, and agent_ruling"
                )
            total_cover = sustain_payload["target_total_cover"]
            raw_ruling = sustain_payload["agent_ruling"]
            if not isinstance(total_cover, bool) or not isinstance(raw_ruling, dict):
                raise _support.CombatEngineError(
                    "sustain_spell requires a boolean cover fact and Agent ruling"
                )
            if set(raw_ruling) != {
                "default_resolver",
                "ruling_kind",
                "decision",
                "reason",
            }:
                raise _support.CombatEngineError(
                    "Witch Bolt cover adjudication requires exactly "
                    "default_resolver, ruling_kind, decision, and reason"
                )
            decision = " ".join(str(raw_ruling.get("decision") or "").split())
            reason = " ".join(str(raw_ruling.get("reason") or "").split())
            if (
                raw_ruling.get("default_resolver") != "agent"
                or raw_ruling.get("ruling_kind") != "agent_dm_adjudication"
                or not decision
                or len(decision) > 1_000
                or not reason
                or len(reason) > 500
            ):
                raise _support.CombatEngineError(
                    "Witch Bolt cover must be a bounded Agent-as-DM scene fact"
                )
            caster_record = self.characters.get(actor_id)
            self.reconcile_actor_witch_bolt_concentration(
                encounter,
                actor_id,
                caster_record.sheet,
            )
            sustained = _support.pay_witch_bolt_sustain_action(
                encounter,
                actor_id_value=actor_id,
                effect_id=str(sustain_payload["effect_id"]),
                target_total_cover=total_cover,
            )
            next_encounter = sustained["encounter"]
            effect = sustained["effect"]
            character_updates: list[_support.CharacterStateUpdate] = []
            result: dict[str, Any] = {
                "kind": "witch_bolt_sustain",
                "effect_id": str(effect.get("id") or ""),
                "spell_id": str(effect.get("source_spell_id") or ""),
                "target_id": str(effect.get("target_id") or ""),
                "status": sustained["status"],
                "payment": sustained["payment"],
                "target_total_cover": total_cover,
                "agent_ruling": {
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "decision": decision,
                    "reason": reason,
                    "committed": True,
                },
            }
            if sustained["status"] == "spell_ended":
                ended_tethers = _support.newly_ended_witch_bolt_tethers(
                    encounter,
                    next_encounter,
                    source_actor_id=actor_id,
                )
                ended = _support.end_tether_concentrations(
                    caster_record.sheet,
                    ended_tethers,
                )
                result["ended_reason"] = str(effect.get("ended_reason") or "")
                result["ended_concentration_effect_ids"] = ended["ended_effect_ids"]
                if ended["sheet"] != caster_record.sheet:
                    character_updates.append(
                        _support.CharacterStateUpdate(
                            character_id=actor_id,
                            sheet=_support.validate_character_sheet(ended["sheet"]),
                            notes=_support.validate_character_notes(caster_record.notes),
                            expected_revision=caster_record.revision,
                        )
                    )
            else:
                concentration_effect_id = str(effect.get("concentration_effect_id") or "")
                if not any(
                    item.get("active")
                    and item.get("concentration")
                    and str(item.get("id") or "") == concentration_effect_id
                    for item in caster_record.sheet.get("effects", [])
                ):
                    raise _support.CombatEngineError(
                        "Witch Bolt tether has no matching active concentration"
                    )
                target_actor_id = str(effect.get("target_id") or "")
                target_record = self.require_campaign_actor(
                    campaign_id,
                    target_actor_id,
                )
                damage_roll = _support.asdict(_support.roll("1d12"))
                target_combatant = self.require_encounter_combatant(
                    next_encounter,
                    target_actor_id,
                    role="Witch Bolt target",
                )
                damaged = _support.apply_damage_to_sheet(
                    target_record.sheet,
                    amount=int(damage_roll["total"]),
                    damage_type="lightning",
                    source=str(effect.get("source_spell_id") or ""),
                    ruleset=str(next_encounter.get("ruleset") or "2014"),
                    death_saves=self.combatant_zero_hp_buffered(target_combatant),
                )
                self.sync_combatant_conditions(
                    next_encounter,
                    target_actor_id,
                    damaged["sheet"],
                )
                _support.reconcile_readied_spells(
                    next_encounter,
                    target_actor_id,
                    damaged["sheet"],
                )
                self.add_concentration_window(
                    next_encounter,
                    target_actor_id,
                    damaged.get("concentration"),
                    next_revision=campaign.revision + 1,
                )
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_actor_id,
                        sheet=_support.validate_character_sheet(damaged["sheet"]),
                        notes=_support.validate_character_notes(target_record.notes),
                        expected_revision=target_record.revision,
                    )
                )
                result.update(
                    damage_roll=damage_roll,
                    damage={key: item for key, item in damaged.items() if key != "sheet"},
                )
            next_state = {
                **dict(campaign.state or {}),
                "combat": next_encounter,
            }
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.witch_bolt.sustain",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload_value,
                response_fields={
                    "status": "committed",
                    "action": action,
                    "result": result,
                    "combat": next_encounter,
                },
                character_updates=character_updates,
                rule_receipts=_support.core_receipts(
                    self.effective_rule_context(campaign_id),
                    [_support.CORE_WITCH_BOLT_MECHANIC_ID],
                    "combat.spell.witch_bolt.sustain",
                ),
            )
            return self.combat_response(
                campaign_id,
                principal_id,
                response,
            )
        condition_resolution = None
        character_updates: list[_support.CharacterStateUpdate] = []
        engine_payload = payload
        source_condition_record = None
        source_condition = ""
        source_condition_ruling = None
        hypnotic_target_record = None
        sleep_wake_spatial_facts = None
        shell_defense_record = None
        shell_defense_sheet = None
        if normalized_action in {"shell_defense", "emerge_shell"}:
            if target_id is not None or trigger is not None or payload:
                raise _support.CombatEngineError(
                    f"{normalized_action} does not accept a target, trigger, or payload"
                )
            shell_defense_record = self.characters.get(actor_id)
            shell_defense_sheet = (
                _support.enter_tortle_shell_defense(shell_defense_record.sheet)
                if normalized_action == "shell_defense"
                else _support.emerge_tortle_shell_defense(shell_defense_record.sheet)
            )
        if normalized_action in {"shake_hypnotic_pattern", "shake_sleep"}:
            if target_id is None or target_id == actor_id:
                raise _support.CombatEngineError("shaking awake requires another target creature")
            acting_combatant = self.require_encounter_combatant(
                encounter,
                actor_id,
                role="shaking actor",
            )
            target_combatant = self.require_encounter_combatant(
                encounter,
                target_id,
                role="sleeping target",
            )
            if normalized_action == "shake_sleep" and encounter.get("positioning_mode") == "agent":
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                sleep_wake_spatial_facts = _support._agent_ruling_boundary(
                    _support._normalize_sleep_wake_spatial_facts
                )(payload, campaign_revision=campaign.revision)
                if sleep_wake_spatial_facts.get("status") == "pending_ruling":
                    return sleep_wake_spatial_facts
                engine_payload = {"spatial_facts": sleep_wake_spatial_facts}
            else:
                if payload:
                    raise _support.CombatEngineError(
                        f"{normalized_action} does not accept a payload"
                    )
                battle_map = dict(encounter.get("battle_map") or {})
                cell_ft = int(dict(battle_map.get("grid") or {}).get("cell_ft", 5) or 5)
                distance = self.combat_distance(
                    acting_combatant.get("position"),
                    target_combatant.get("position"),
                    cell_ft=cell_ft,
                )
                if distance is None or distance > 5:
                    raise _support.CombatEngineError("shaking awake requires an adjacent target")
            hypnotic_target_record = self.characters.get(target_id)
            active_shaken_effect_ids = (
                _support.active_hypnotic_pattern_effect_ids(hypnotic_target_record.sheet)
                if normalized_action == "shake_hypnotic_pattern"
                else [
                    str(effect["id"])
                    for effect in hypnotic_target_record.sheet.get("effects", [])
                    if effect.get("active")
                    and effect.get("source_spell_id") == _support.CORE_SLEEP_SPELL_ID
                ]
            )
            if not active_shaken_effect_ids:
                raise _support.CombatEngineError(
                    f"target has no active effect for {normalized_action}"
                )
        if "agent_ruling_commitment" in dict(payload or {}):
            if normalized_action != "improvise":
                raise _support.CombatEngineError(
                    "a scene-procedure Agent ruling commitment must pay the Improvise action"
                )
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
            committed_payload = dict(payload or {})
            committed_payload["agent_ruling_commitment"] = (
                self.validate_agent_save_damage_commitment(
                    campaign_id,
                    committed_payload["agent_ruling_commitment"],
                    encounter=encounter,
                    source_actor_id=actor_id,
                    source_card_id=str(committed_payload.get("procedure_id") or ""),
                    source_card_kind="scene_procedure",
                )
            )
            engine_payload = committed_payload
        if normalized_action == "interact_object":
            interaction_payload = dict(payload or {})
            allowed_fields = {
                "object_description",
                "interaction",
                "remove_source_condition",
                "source_ref",
                "source_excerpt",
                "agent_ruling",
            }
            unknown_fields = set(interaction_payload) - allowed_fields
            if unknown_fields:
                raise _support.CombatEngineError(
                    "interact_object contains unsupported fields: "
                    + ", ".join(sorted(unknown_fields))
                )
            object_description = " ".join(
                str(interaction_payload.get("object_description") or "").split()
            )
            interaction = " ".join(
                str(interaction_payload.get("interaction") or "").split()
            ).casefold()
            if not object_description or not interaction:
                raise _support.CombatEngineError(
                    "interact_object requires an object_description and interaction"
                )
            source_condition = (
                str(interaction_payload.get("remove_source_condition") or "").strip().casefold()
            )
            conditional_fields = {
                "remove_source_condition",
                "source_ref",
                "source_excerpt",
                "agent_ruling",
            }
            if source_condition:
                self.access.require_campaign(
                    campaign_id,
                    principal_id,
                    roles=_support.CAMPAIGN_DM_ROLES,
                )
                if interaction != "remove":
                    raise _support.CombatEngineError(
                        "ending a source condition requires interaction=remove"
                    )
                if not conditional_fields <= set(interaction_payload):
                    raise _support.CombatEngineError(
                        "ending a source condition requires remove_source_condition, "
                        "source_ref, source_excerpt, and agent_ruling"
                    )
                source_ref = interaction_payload.get("source_ref")
                source_excerpt = " ".join(
                    str(interaction_payload.get("source_excerpt") or "").split()
                )
                raw_ruling = interaction_payload.get("agent_ruling")
                if not isinstance(source_ref, dict) or not source_excerpt:
                    raise _support.CombatEngineError(
                        "ending a source condition requires exact source evidence"
                    )
                if not isinstance(raw_ruling, dict):
                    raise _support.CombatEngineError(
                        "ending a source condition requires an Agent adjudication"
                    )
                allowed_ruling_fields = {
                    "default_resolver",
                    "ruling_kind",
                    "decision",
                    "reason",
                }
                if set(raw_ruling) != allowed_ruling_fields:
                    raise _support.CombatEngineError(
                        "source-condition Agent ruling requires exactly "
                        "default_resolver, ruling_kind, decision, and reason"
                    )
                decision = " ".join(str(raw_ruling.get("decision") or "").split())
                reason = " ".join(str(raw_ruling.get("reason") or "").split())
                if (
                    raw_ruling.get("default_resolver") != "agent"
                    or raw_ruling.get("ruling_kind") != "agent_dm_adjudication"
                    or not decision
                    or len(decision) > 1_000
                    or not reason
                    or len(reason) > 500
                ):
                    raise _support.CombatEngineError(
                        "source-condition Agent ruling must be a bounded settled "
                        "agent_dm_adjudication"
                    )
                source_condition_ruling = {
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "decision": decision,
                    "reason": reason,
                    "committed": True,
                }
                source_condition_record = next(
                    (
                        item
                        for item in encounter.get("source_conditions", [])
                        if isinstance(item, dict)
                        and item.get("active", True)
                        and str(item.get("actor_id") or "") == actor_id
                        and str(item.get("condition") or "").casefold() == source_condition
                        and item.get("source_ref") == source_ref
                        and " ".join(str(item.get("source_excerpt") or "").split())
                        == source_excerpt
                        and item.get("added_by_encounter", False)
                    ),
                    None,
                )
                if source_condition_record is None:
                    raise _support.CombatEngineError(
                        "the cited active encounter source condition is not owned by this actor"
                    )
                current_character = self.characters.get(actor_id)
                if source_condition not in _support.condition_ids(
                    current_character.sheet.get("conditions", [])
                ):
                    raise _support.CombatEngineError(
                        "the cited encounter source condition is not active on the actor"
                    )
            elif set(interaction_payload) & conditional_fields:
                raise _support.CombatEngineError(
                    "source-condition fields require remove_source_condition"
                )
            engine_payload = {
                "object_description": object_description,
                "interaction": interaction,
            }
        next_encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=actor_id,
            action=action,
            target_id=target_id,
            trigger=trigger,
            payload=engine_payload,
        )
        if normalized_action == "revive_steel_defender":
            assert target_id is not None
            assert revival_relation_index is not None
            assert revival_relations is not None
            assert revival_relation is not None
            assert revival_distance_ft is not None
            assert revival_slot_level is not None
            owner_record = self.characters.get(actor_id)
            defender_record = self.require_campaign_actor(campaign_id, target_id)
            try:
                started_revival = _support.begin_steel_defender_revival(
                    owner_record.sheet,
                    defender_record.sheet,
                    relation=revival_relation,
                    elapsed_ticks=int(dict(campaign.state["game_time"])["elapsed_ticks"]),
                    distance_ft=revival_distance_ft,
                    slot_level=revival_slot_level,
                    action_available=True,
                )
            except _support.SteelDefenderError as error:
                raise _support.CombatEngineError(str(error)) from error
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(started_revival["owner_sheet"]),
                    notes=_support.validate_character_notes(owner_record.notes),
                    expected_revision=owner_record.revision,
                )
            )
            pending = dict(started_revival["pending_revival"])
            revival_relations[revival_relation_index] = {
                **revival_relation,
                "revival_started_elapsed_ticks": pending["started_elapsed_ticks"],
                "revival_completes_elapsed_ticks": pending["completes_elapsed_ticks"],
            }
            condition_resolution = {
                "kind": "steel_defender_revival_started",
                "dependent_actor_id": target_id,
                "payment": _support.deepcopy(started_revival["payment"]),
                "action_paid": True,
                "completes_elapsed_ticks": pending["completes_elapsed_ticks"],
                "spatial_facts": _support.deepcopy(revival_spatial_facts),
            }
        if shell_defense_record is not None and shell_defense_sheet is not None:
            self.sync_combatant_conditions(next_encounter, actor_id, shell_defense_sheet)
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(shell_defense_sheet),
                    notes=_support.validate_character_notes(shell_defense_record.notes),
                    expected_revision=shell_defense_record.revision,
                )
            )
            withdrawn = normalized_action == "shell_defense"
            condition_resolution = {
                "kind": "tortle_shell_defense",
                "withdrawn": withdrawn,
                "armor_class": self.derive_character_sheet(
                    shell_defense_sheet, character_id=actor_id
                )["armor_class"],
                "speed_multiplier": _support.source_speed_multiplier(shell_defense_sheet),
                "conditions": list(shell_defense_sheet.get("conditions") or []),
            }
        if hypnotic_target_record is not None:
            ended_hypnotic = (
                _support.end_hypnotic_pattern_effects(
                    hypnotic_target_record.sheet, ended_reason="shaken_awake"
                )
                if normalized_action == "shake_hypnotic_pattern"
                else _support.wake_sleep_effects(
                    hypnotic_target_record.sheet, reason="shaken_awake"
                )
            )
            shaken_kind = (
                "hypnotic_pattern_shaken_awake"
                if normalized_action == "shake_hypnotic_pattern"
                else "sleep_shaken_awake"
            )
            self.sync_combatant_conditions(
                next_encounter,
                str(target_id),
                ended_hypnotic["sheet"],
            )
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=str(target_id),
                    sheet=ended_hypnotic["sheet"],
                    notes=_support.validate_character_notes(hypnotic_target_record.notes),
                    expected_revision=hypnotic_target_record.revision,
                )
            )
            condition_resolution = {
                "kind": shaken_kind,
                "target_id": str(target_id),
                "ended_effect_ids": ended_hypnotic["ended_effect_ids"],
                "ended_reason": "shaken_awake",
                **({"spatial_facts": sleep_wake_spatial_facts} if sleep_wake_spatial_facts else {}),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": shaken_kind,
                    "actor_id": actor_id,
                    "target_id": str(target_id),
                    "ended_effect_ids": ended_hypnotic["ended_effect_ids"],
                },
            ][-100:]
        if source_condition_record is not None:
            next_source_condition = next(
                item
                for item in next_encounter.get("source_conditions", [])
                if isinstance(item, dict)
                and item.get("active", True)
                and str(item.get("actor_id") or "") == actor_id
                and str(item.get("condition") or "").casefold() == source_condition
                and item.get("source_ref") == source_condition_record["source_ref"]
                and item.get("source_excerpt") == source_condition_record["source_excerpt"]
            )
            next_source_condition["active"] = False
            next_source_condition["ended_reason"] = "agent_object_interaction"
            next_source_condition["ended_round"] = int(next_encounter.get("round", 1) or 1)
            next_source_condition["agent_ruling"] = source_condition_ruling
            current_character = self.characters.get(actor_id)
            updated_sheet = _support.deepcopy(current_character.sheet)
            if not _support.has_active_owned_condition(
                next_encounter,
                target_id=actor_id,
                condition=source_condition,
            ):
                _support.apply_condition_change(
                    updated_sheet,
                    condition_id=source_condition,
                    add=False,
                )
            self.sync_combatant_conditions(next_encounter, actor_id, updated_sheet)
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated_sheet),
                    notes=_support.validate_character_notes(current_character.notes),
                    expected_revision=current_character.revision,
                )
            )
            condition_resolution = {
                "condition": source_condition,
                "removed": source_condition
                not in _support.condition_ids(updated_sheet.get("conditions", [])),
                "source_ref": _support.deepcopy(next_source_condition["source_ref"]),
                "source_excerpt": str(next_source_condition["source_excerpt"]),
                "agent_ruling": _support.deepcopy(source_condition_ruling),
            }
        action_ended_tethers = _support.newly_ended_witch_bolt_tethers(
            encounter,
            next_encounter,
            source_actor_id=actor_id,
        )
        if action_ended_tethers:
            existing_update = next(
                (item for item in character_updates if item.character_id == actor_id),
                None,
            )
            current_character = self.characters.get(actor_id)
            base_sheet = (
                existing_update.sheet if existing_update is not None else current_character.sheet
            )
            ended = _support.end_tether_concentrations(
                base_sheet,
                action_ended_tethers,
            )
            self.sync_combatant_conditions(
                next_encounter,
                actor_id,
                ended["sheet"],
            )
            if existing_update is not None:
                character_updates[character_updates.index(existing_update)] = _support.replace(
                    existing_update,
                    sheet=_support.validate_character_sheet(ended["sheet"]),
                )
            elif ended["sheet"] != current_character.sheet:
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=_support.validate_character_sheet(ended["sheet"]),
                        notes=_support.validate_character_notes(current_character.notes),
                        expected_revision=current_character.revision,
                    )
                )
        boundary_ids: list[str] = []
        if normalized_action == "ready":
            boundary_ids.append("dnd5e.core.ready.action")
        if normalized_action == "shake_hypnotic_pattern":
            boundary_ids.append(_support.CORE_HYPNOTIC_PATTERN_MECHANIC_ID)
        if normalized_action == "shake_sleep":
            boundary_ids.append(_support.CORE_SLEEP_MECHANIC_ID)
        if normalized_action in {"shell_defense", "emerge_shell"}:
            boundary_ids.append(_support.CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID)
        acting_combatant = next(
            item for item in encounter.get("combatants", []) if item.get("actor_id") == actor_id
        )
        if "turned" in {str(item).casefold() for item in acting_combatant.get("conditions", [])}:
            boundary_ids.append("dnd5e.core.activity.turn_undead")
        action_receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id),
            boundary_ids,
            f"action.{normalized_action}",
        )
        if normalized_action == "revive_steel_defender":
            assert revival_contract is not None
            action_receipts.append(
                {
                    "mechanic_id": "dnd5e.expansion.steel_defender.revival",
                    "event": "combat.steel_defender.revival.start",
                    "operations": [{"op": "builtin.expansion_provider"}],
                    "citations": [
                        {
                            "source_artifact_id": revival_contract["source_artifact_id"],
                            "source_pack_id": revival_contract["source_pack_id"],
                            "source_pack_version": revival_contract["source_pack_version"],
                            "reviewed_expression_hash": revival_contract[
                                "reviewed_expression_hash"
                            ],
                        }
                    ],
                    "ruleset_fingerprint": self.effective_rule_context(campaign_id).fingerprint,
                }
            )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        if revival_relations is not None:
            next_state["dependent_actor_relations"] = _support.validate_dependent_actor_relations(
                revival_relations
            )
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=f"combat.common.{action}",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload_value,
            response_fields={
                "status": "committed",
                "action": action,
                "condition_resolution": condition_resolution,
                "combat": next_encounter,
            },
            character_updates=character_updates,
            rule_receipts=action_receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_reactions(
        self,
        campaign_id: str,
        actor_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """Read reaction windows an actor may resolve outside its own turn."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id
        )
        _campaign, encounter = self.active_encounter(campaign_id)
        windows = _support.available_reactions(encounter, actor_id)
        if self.is_dm(campaign_id, principal_id):
            return windows
        allowed = {
            "id",
            "kind",
            "actor_id",
            "event",
            "candidates",
            "deadline",
            "status",
            "trigger",
            "attacker_id",
            "target_id",
            "opportunity_attack_weapon_ids",
            "opportunity_attack_reach_ft",
        }
        return [
            {key: value for key, value in window.items() if key in allowed} for window in windows
        ]

    def combat_readied_action_trigger(
        self,
        campaign_id: str,
        readied_id: str,
        event: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Confirm a generic Ready trigger in the DM role and open its reaction window."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {"readied_id": readied_id, "event": event, "branch_id": resolved_branch_id}
        scope = f"combat-ready-action-trigger:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        next_encounter = _support.trigger_readied_action(
            encounter, readied_id=readied_id, event=event
        )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.ready.action.trigger",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "triggered",
                "combat": next_encounter,
            },
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_readied_action_resolve(
        self,
        campaign_id: str,
        actor_id: str,
        choice_id: str,
        release: bool,
        declaration: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Spend a reaction for a generic Ready action; settle its declared effect by ruling."""
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "choice_id": choice_id,
            "release": release,
            "declaration": declaration or {},
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-ready-action-resolve:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if release and choice_id not in {
            str(item.get("id")) for item in _support.available_reactions(encounter, actor_id)
        }:
            raise _support.CombatEngineError("actor cannot take this reaction")
        next_encounter, readied = _support.resolve_readied_action_window(
            encounter, actor_id_value=actor_id, choice_id=choice_id, release=release
        )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "readied_action_released" if release else "readied_action_declined",
                "actor_id": actor_id,
                "readied_id": readied.get("id"),
                "declaration": declaration or {},
            },
        ][-100:]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=("combat.ready.action.release" if release else "combat.ready.action.decline"),
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                **_support._ruling_status(
                    "pending_ruling" if release else "armed",
                    "ready_release_effect",
                ),
                "released": release,
                "declaration": declaration or {},
                "combat": next_encounter,
            },
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_use_official_item(
        self,
        campaign_id: str,
        actor_id: str,
        item_id: str,
        operation: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one attuned Eberron weapon transition through the turn economy."""

        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "item_id": item_id,
            "operation": operation,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-official-item:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        self.require_no_blocking_pending(encounter)
        record = self.require_campaign_actor(campaign_id, actor_id)
        item = next(
            (
                value
                for value in record.sheet.get("inventory", {}).get("items", [])
                if str(value.get("id") or "") == str(item_id)
            ),
            None,
        )
        if not isinstance(item, dict):
            raise _support.CombatEngineError("official item is not present in the actor inventory")
        if item.get("condition") == "destroyed":
            raise _support.CombatEngineError("a destroyed official item cannot be activated")
        contract = dict(dict(item.get("mechanics") or {}).get("official_item") or {})
        kind = str(contract.get("kind") or "")
        source_key = str(item.get("source_key") or "")
        expected_suffix = {
            "arcane_propulsion_arm": _support.ARCANE_PROPULSION_ARM_ID,
            "armblade": _support.ARMBLADE_ID,
            "dyrrn_tentacle_whip": _support.DYRRN_TENTACLE_WHIP_ID,
        }.get(kind)
        if not expected_suffix or not source_key.endswith(f":{expected_suffix}"):
            raise _support.CombatEngineError(
                "official item activation requires an exact reviewed source key"
            )
        selection_receipt = next(
            (
                selection
                for selection in record.sheet.get("content", {}).get("selections", [])
                if isinstance(selection, dict)
                and str(selection.get("artifact_id") or "") == expected_suffix
            ),
            None,
        )
        if (
            not isinstance(selection_receipt, dict)
            or str(selection_receipt.get("pack_id") or "") != _support.EBERRON_ITEM_PACK_ID
        ):
            raise _support.CombatEngineError(
                "official item activation requires the matching content selection receipt"
            )
        if str(selection_receipt.get("kind") or "") != "item":
            raise _support.CombatEngineError(
                "official item activation requires an item selection receipt"
            )
        recorded_selection = dict(selection_receipt.get("selection") or {})
        reviewed_hash = _support.reviewed_official_item_hash(expected_suffix)
        if (
            str(recorded_selection.get("inventory_item_id") or "") != str(item_id)
            or not reviewed_hash
            or str(recorded_selection.get("artifact_content_hash") or "") != reviewed_hash
            or str(recorded_selection.get("reviewed_content_hash") or "") != reviewed_hash
        ):
            raise _support.CombatEngineError(
                "official item activation receipt does not bind this inventory item"
            )
        selected_version = str(selection_receipt.get("pack_version") or "")
        if not selected_version or not source_key.startswith(
            f"{_support.EBERRON_ITEM_PACK_ID}@{selected_version}:"
        ):
            raise _support.CombatEngineError(
                "official item activation provenance does not match selection"
            )
        applied_receipt_fields = {
            "event": "character.content.apply",
            "artifact_id": expected_suffix,
            "character_id": actor_id,
            "pack_id": _support.EBERRON_ITEM_PACK_ID,
            "pack_version": selected_version,
            "artifact_content_hash": reviewed_hash,
            "reviewed_content_hash": reviewed_hash,
        }
        if not self.rule_receipts.has_applied_receipt(
            campaign_id,
            event="character.content.apply",
            receipt_fields=applied_receipt_fields,
            branch_id=resolved_branch_id,
        ):
            raise _support.CombatEngineError(
                "official item activation requires the applied content receipt"
            )
        if item.get("attunement") != "attuned":
            raise _support.CombatEngineError(
                "official item activation requires completed attunement"
            )
        state = str(contract.get("state") or "")
        normalized_operation = str(operation or "").strip().casefold().replace("-", "_")
        if kind == "arcane_propulsion_arm" and normalized_operation == (
            "attach" if state == "attached" else "remove" if state == "detached" else ""
        ):
            raise _support.CombatEngineError("Arcane Propulsion Arm is already in that state")
        if (
            kind in {"armblade", "dyrrn_tentacle_whip"}
            and item.get("equipped_slot") not in _support.WEAPON_HAND_SLOTS
        ):
            raise _support.CombatEngineError(
                "this official weapon must be equipped in a weapon hand"
            )
        activation = "action" if kind == "arcane_propulsion_arm" else "bonus_action"
        next_encounter, payment = _support.pay_official_item_activation(
            encounter,
            actor_id_value=actor_id,
            item_id=item_id,
            activation=activation,
        )
        next_sheet, transition = _support.use_official_item_action(record.sheet, item_id, operation)
        result = {
            "item_id": item_id,
            "official_item": transition,
            "payment": payment,
            "status": "committed",
        }
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={"actor_id": actor_id, "item_id": item_id, "operation": operation},
        )
        result["rule_receipts"] = [
            {
                "ruleset_fingerprint": rules.fingerprint,
                "mechanic_id": f"dnd5e.expansion.eberron.{kind}",
                "event": "combat.official_item.activation",
                "source_key": source_key,
                "operation": transition["operation"],
            }
        ]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            {
                "id": f"resolution-{_support.uuid4().hex}",
                "type": "combat_official_item",
                "operation": "combat.official_item.activation",
                "actor_id": actor_id,
                "item_id": item_id,
                "result": _support.deepcopy(result),
                "branch_id": resolved_branch_id,
                "campaign_revision": campaign.revision + 1,
            },
        ][-200:]
        response_fields = {
            "status": "committed",
            "result": result,
            "combat": next_encounter,
            "campaign_revision": campaign.revision + 1,
        }
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.official_item.activation",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields=response_fields,
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(next_sheet),
                    notes=_support.validate_character_notes(record.notes),
                    expected_revision=record.revision,
                )
            ],
            rule_receipts=list(result["rule_receipts"]),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_use_activity(
        self,
        campaign_id: str,
        actor_id: str,
        activity_id: str,
        declaration: dict[str, Any] | None = None,
        choice_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Pay an activity and settle supported Core outcomes; return rulings for the rest."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id, branch_id=branch_id
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "activity_id": activity_id,
            "declaration": declaration or {},
            "choice_id": choice_id,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-activity:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        current = self.characters.get(actor_id)
        activity_card, activity_source_card_kind = self.character_activity_source_card(
            current.sheet,
            activity_id,
            character_type=current.character_type,
        )
        scag_bladesong_activity = (
            activity_id == _support.SCAG_RULE_PACK_ID + ".feature.bladesong"
            and activity_source_card_kind in {"activity", "feature"}
            and str(activity_card.get("pack_id") or "") == _support.SCAG_RULE_PACK_ID
            and any(
                str(item.get("id") or "") == activity_id
                and str(item.get("pack_id") or "") == _support.SCAG_RULE_PACK_ID
                for item in current.sheet.get("content", {}).get("features", [])
            )
        )
        scag_bladesong_dismiss = False
        if scag_bladesong_activity:
            declared_bladesong = dict(declaration or {})
            scag_bladesong_dismiss = bool(
                declared_bladesong.get("dismiss") is True
                or str(declared_bladesong.get("action") or "").casefold() == "dismiss"
            )
            if scag_bladesong_dismiss and set(declared_bladesong) - {"dismiss", "action"}:
                raise _support.CombatEngineError("Bladesong dismissal accepts only dismiss/action")
            if not scag_bladesong_dismiss:
                slots = dict(current.sheet.get("inventory", {}).get("equipment_slots") or {})
                items = {
                    str(item.get("id") or ""): item
                    for item in current.sheet.get("inventory", {}).get("items", [])
                }
                worn_armor = items.get(str(slots.get("armor") or ""))
                armor_category = str(
                    dict(dict(worn_armor or {}).get("mechanics") or {}).get("category") or ""
                ).casefold()
                if armor_category in {"medium", "heavy"} or slots.get("shield"):
                    raise _support.CombatEngineError(
                        "Bladesong requires no medium or heavy armor and no shield"
                    )
        steel_defender_contract = self._steel_defender_turn_contracts(
            campaign_id,
            resolved_branch_id,
            {character.id for character in self.characters.list(campaign_id=campaign_id)},
        ).get(actor_id)
        steel_defender_repair = False
        if steel_defender_contract is not None:
            active_relations = [
                relation
                for relation in _support.validate_dependent_actor_relations(
                    dict(campaign.state or {}).get("dependent_actor_relations", [])
                )
                if relation["dependent_actor_id"] == actor_id
                and relation["status"] == "active"
                and relation["relation_key"] == _support.STEEL_DEFENDER_RELATION_KEY
            ]
            if len(active_relations) != 1:
                raise _support.CombatEngineError(
                    "Steel Defender Repair requires one active relation"
                )
            expected_repair, expected_kind, steel_defender_contract = (
                self._verified_steel_defender_repair_activity(
                    campaign_id,
                    resolved_branch_id,
                    active_relations[0],
                )
            )
            if str(expected_repair.get("id") or "") == activity_id:
                if activity_source_card_kind != expected_kind or self._activity_source_identity(
                    activity_card
                ) != self._activity_source_identity(expected_repair):
                    raise _support.CombatEngineError(
                        "Steel Defender Repair does not match its signed source template"
                    )
                steel_defender_repair = True
        repair_target_record = None
        repair_target_kind = ""
        repair_distance_ft: float = 0
        repair_spatial_facts: dict[str, Any] | None = None
        if steel_defender_repair:
            declared_repair = dict(declaration or {})
            if set(declared_repair) - {"target_id", "spatial_facts"}:
                raise _support.CombatEngineError(
                    "Steel Defender Repair accepts only target_id and spatial_facts"
                )
            repair_target_id = str(declared_repair.get("target_id") or "").strip()
            if not repair_target_id:
                raise _support.CombatEngineError("Steel Defender Repair requires target_id")
            combatants_by_id = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            source_combatant = combatants_by_id.get(actor_id)
            target_combatant = combatants_by_id.get(repair_target_id)
            if source_combatant is None or target_combatant is None:
                raise _support.CombatEngineError(
                    "Steel Defender Repair source and target must be current combatants"
                )
            repair_target_record = self.require_campaign_actor(campaign_id, repair_target_id)
            repair_target_kind = (
                "self"
                if repair_target_id == actor_id
                else (
                    "construct"
                    if "construct"
                    in str(
                        dict(repair_target_record.sheet.get("progression") or {}).get("species")
                        or repair_target_record.sheet.get("creature_type")
                        or ""
                    ).casefold()
                    else "object"
                    if "object"
                    in str(
                        dict(repair_target_record.sheet.get("progression") or {}).get("species")
                        or repair_target_record.sheet.get("creature_type")
                        or ""
                    ).casefold()
                    else "invalid"
                )
            )
            if repair_target_id == actor_id:
                if declared_repair.get("spatial_facts") is not None:
                    raise _support.CombatEngineError(
                        "self-targeted Steel Defender Repair derives zero distance"
                    )
                repair_distance_ft = 0.0
                repair_spatial_facts = {
                    "distance_ft": 0.0,
                    "source": "self_target",
                    "committed": True,
                }
            elif str(encounter.get("positioning_mode") or "agent") == "grid":
                if declared_repair.get("spatial_facts") is not None:
                    raise _support.CombatEngineError(
                        "grid Steel Defender Repair derives distance from token positions"
                    )
                distance = self.combat_distance(
                    source_combatant.get("position"),
                    target_combatant.get("position"),
                )
                if distance is None:
                    raise _support.CombatEngineError(
                        "Steel Defender Repair requires token positions"
                    )
                repair_distance_ft = float(distance)
                repair_spatial_facts = {
                    "distance_ft": repair_distance_ft,
                    "source": "grid_token_positions",
                    "committed": True,
                }
            else:
                raw_spatial = declared_repair.get("spatial_facts")
                if not isinstance(raw_spatial, dict) or set(raw_spatial) != {
                    "distance_ft",
                    "default_resolver",
                    "ruling_kind",
                    "reason",
                }:
                    raise _support.CombatEngineError(
                        "agent-positioned Steel Defender Repair requires exact spatial_facts"
                    )
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                reason = " ".join(str(raw_spatial.get("reason") or "").split())
                if (
                    isinstance(raw_spatial.get("distance_ft"), bool)
                    or not isinstance(raw_spatial.get("distance_ft"), (int, float))
                    or not _support.math.isfinite(float(raw_spatial["distance_ft"]))
                    or float(raw_spatial["distance_ft"]) < 0
                    or raw_spatial.get("default_resolver") != "agent"
                    or raw_spatial.get("ruling_kind") != "agent_dm_adjudication"
                    or not reason
                    or len(reason) > 500
                ):
                    raise _support.CombatEngineError(
                        "Steel Defender Repair spatial_facts require a bounded Agent ruling"
                    )
                repair_distance_ft = raw_spatial["distance_ft"]
                repair_spatial_facts = {**raw_spatial, "reason": reason, "committed": True}
        compiled_activity_plan = None
        bound_activity_plan: _support.BoundResolutionPlan | None = None
        if isinstance(
            activity_card.get("resolution_plan"),
            dict,
        ):
            try:
                compiled_activity_plan = _support.compile_resolution_plan(
                    activity_card["resolution_plan"]
                )
            except _support.ResolutionPlanCompilationError as error:
                raise _support.CombatEngineError(
                    f"recorded activity resolution plan is invalid: {error}"
                ) from error
            if (
                compiled_activity_plan.source_card_id != activity_id
                or compiled_activity_plan.source_card_kind != activity_source_card_kind
            ):
                raise _support.CombatEngineError(
                    "recorded activity resolution plan does not match its card"
                )
            _support._semantic_plan_save_facts(activity_card, compiled_activity_plan)
            if compiled_activity_plan.trigger != "action":
                raise _support.CombatEngineError(
                    "a combat-used activity resolution plan must use the action trigger"
                )
            if "agent_resolution_commitment" not in dict(declaration or {}):
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "module_specific_procedure",
                    ),
                    "result": {
                        "activity_id": activity_id,
                        "resolution_plan_contract": _support.resolution_plan_contract(
                            compiled_activity_plan
                        ),
                        "payment_required": True,
                    },
                    "campaign_revision": campaign.revision,
                }
            if set(dict(declaration or {})) != {"agent_resolution_commitment"}:
                raise _support.CombatEngineError(
                    "a planned activity accepts only agent_resolution_commitment"
                )
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
            normalized_commitment, bound_activity_plan = self.validate_agent_resolution_commitment(
                campaign_id,
                dict(declaration or {}).get("agent_resolution_commitment"),
                encounter=encounter,
                source_actor_id=actor_id,
                source_card_id=activity_id,
                source_card_kind=activity_source_card_kind,
                compiled_plan=compiled_activity_plan,
            )
            declaration = {"agent_resolution_commitment": normalized_commitment}
        elif (
            not steel_defender_repair
            and str(activity_card.get("description") or "").strip()
            and not self.source_card_has_executable_mechanic(
                campaign_id,
                activity_card,
            )
        ):
            return {
                **_support._ruling_status(
                    "pending_ruling",
                    "module_specific_procedure",
                ),
                "result": {
                    "activity_id": activity_id,
                    "semantic_solution": self.unresolved_content_solution(
                        activity_card,
                        source_card_id=activity_id,
                        source_card_kind=activity_source_card_kind,
                        character_revision=current.revision,
                    ),
                    "payment_required": False,
                },
                "campaign_revision": campaign.revision,
            }
        rule_context = self.effective_rule_context(
            campaign_id,
            facts={"actor_id": actor_id, "activity_id": activity_id},
            branch_id=resolved_branch_id,
        )
        legendary_spec = (
            None
            if compiled_activity_plan is not None
            else _support.legendary_action_spec(current.sheet, activity_id)
        )
        if legendary_spec is not None:
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError("legendary actions require the Agent in the DM role")
            legendary_effect = dict(legendary_spec.get("effect") or {})
            legendary_kind = str(legendary_effect.get("kind") or "")
            if legendary_kind not in {"skill_check", "weapon_attack"}:
                raise _support.CombatEngineError(
                    "nonstandard legendary actions require a source-bound resolution plan"
                )
            if dict(declaration or {}):
                raise _support.CombatEngineError("this legendary action accepts no declaration")
        dragonborn_breath = _support.CORE_DRAGONBORN_BREATH_MECHANIC_ID in {
            str(item) for item in activity_card.get("mechanic_refs") or []
        }
        breath_target_records: dict[str, Any] = {}
        breath_targets: list[dict[str, Any]] = []
        breath_target_contexts: list[dict[str, Any]] = []
        breath_spec: dict[str, Any] = {}
        breath_save_dc = 0
        breath_damage_expression = ""
        if dragonborn_breath:
            if str(
                activity_card.get("pack_id") or ""
            ) != _support.STANDARD_2014_CONTENT_PACK_ID or not activity_id.startswith(
                f"{_support.STANDARD_2014_CONTENT_PACK_ID}.species.dragonborn.activity."
            ):
                raise _support.CombatEngineError(
                    "Dragonborn Breath Weapon requires its exact locked standard card"
                )
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError("area activity settlement requires the Agent in the DM role")
            breath_spec = dict(
                dict(activity_card.get("choices") or {}).get("standard_resolution") or {}
            )
            if (
                breath_spec.get("kind") != "area_save_damage"
                or dict(breath_spec.get("origin") or {}) != {"kind": "self"}
                or breath_spec.get("targets") != "each_creature"
                or breath_spec.get("half_on_success") is not True
                or breath_spec.get("save_source_kind") != "nonmagical_effect"
            ):
                raise _support.CombatEngineError(
                    "Dragonborn Breath Weapon has an invalid standard resolution contract"
                )
            normalized_area = self.normalize_area_declaration(
                encounter,
                source_id=actor_id,
                area=dict(breath_spec.get("area") or {}),
                declaration=declaration,
            )
            breath_target_contexts = list(normalized_area["targets"])
            for target_context in breath_target_contexts:
                target_id = str(target_context["target_id"])
                target = self.characters.get(target_id)
                if target.campaign_id != campaign_id:
                    raise _support.CombatEngineError(
                        "Breath Weapon targets must share the campaign"
                    )
                self.access.require_actor(
                    campaign_id,
                    target_id,
                    principal_id,
                    control=True,
                )
                breath_target_records[target_id] = target
                breath_targets.append(self.combat_actor_snapshot(target_id))
            save_dc_formula = dict(breath_spec.get("save_dc_formula") or {})
            if save_dc_formula != {
                "base": 8,
                "ability": "constitution",
                "include_proficiency": True,
            }:
                raise _support.CombatEngineError(
                    "Dragonborn Breath Weapon has an invalid save DC formula"
                )
            source_derived = self.derive_character_sheet(current.sheet, character_id=current.id)
            breath_save_dc = (
                8
                + int(source_derived["ability_modifiers"]["constitution"])
                + int(source_derived["proficiency_bonus"])
            )
            source_level = int(current.sheet.get("progression", {}).get("level", 0) or 0)
            damage_by_level = {
                int(level): str(expression)
                for level, expression in dict(
                    breath_spec.get("damage_formula_by_level") or {}
                ).items()
                if str(level).isdigit()
            }
            eligible_levels = [level for level in damage_by_level if level <= source_level]
            if source_level < 1 or not eligible_levels:
                raise _support.CombatEngineError(
                    "Dragonborn Breath Weapon has no damage formula for this level"
                )
            breath_damage_expression = damage_by_level[max(eligible_levels)]
        turn_undead = (
            activity_id
            in {
                "dnd5e.content.srd2014.feature.cleric-channel-divinity",
                "dnd5e.content.srd2024.feature.cleric-channel-divinity",
            }
            and str(dict(declaration or {}).get("option") or "")
            .strip()
            .casefold()
            .replace(" ", "_")
            == "turn_undead"
        )
        divine_spark = (
            activity_id == "dnd5e.content.srd2024.feature.cleric-channel-divinity"
            and str(dict(declaration or {}).get("option") or "")
            .strip()
            .casefold()
            .replace(" ", "_")
            == "divine_spark"
        )
        divine_spark_target_record = None
        divine_spark_target: dict[str, Any] | None = None
        if divine_spark:
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError(
                    "Divine Spark multi-actor settlement requires the Agent in the DM role"
                )
            declared = dict(declaration or {})
            mode = str(declared.get("mode") or "").strip().casefold()
            expected_fields = {"option", "target_id", "mode"}
            if mode == "damage":
                expected_fields.add("damage_type")
            if set(declared) != expected_fields:
                raise _support.CombatEngineError(
                    "Divine Spark declaration requires option, target_id, mode, and "
                    "damage_type only for damage"
                )
            target_id = str(declared.get("target_id") or "").strip()
            if not target_id or target_id == actor_id:
                raise _support.CombatEngineError("Divine Spark targets one other creature")
            combatants_by_id = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            source_combatant = combatants_by_id.get(actor_id)
            target_combatant = combatants_by_id.get(target_id)
            if source_combatant is None or target_combatant is None:
                raise _support.CombatEngineError(
                    "Divine Spark source and target must be current combatants"
                )
            source_position = dict(source_combatant.get("position") or {})
            target_position = dict(target_combatant.get("position") or {})
            if set(source_position) != {"x", "y"} or set(target_position) != {
                "x",
                "y",
            }:
                raise _support.NeedsRulingError(
                    "Divine Spark requires source and target battle-map positions",
                    missing=("divine_spark_positions",),
                )
            source_conditions = {
                str(item).casefold() for item in source_combatant.get("conditions", [])
            }
            target_conditions = {
                str(item).casefold() for item in target_combatant.get("conditions", [])
            }
            visible_to = target_combatant.get("visible_to_actor_ids")
            if (
                "blinded" in source_conditions
                or "dead" in target_conditions
                or bool(target_combatant.get("hidden", False))
                or "invisible" in target_conditions
                or (
                    isinstance(visible_to, list)
                    and actor_id not in {str(item) for item in visible_to}
                )
            ):
                raise _support.CombatEngineError(
                    "Divine Spark requires one living target the Cleric can see"
                )
            distance = (
                max(
                    abs(int(source_position["x"]) - int(target_position["x"])),
                    abs(int(source_position["y"]) - int(target_position["y"])),
                )
                * 5
            )
            if distance > 30:
                raise _support.CombatEngineError("Divine Spark target is outside 30 feet")
            divine_spark_target_record = self.require_campaign_actor(campaign_id, target_id)
            self.access.require_actor(campaign_id, target_id, principal_id, control=True)
            divine_spark_target = self.combat_actor_snapshot(target_id)
        turn_targets: dict[str, Any] = {}
        turn_target_records: dict[str, Any] = {}
        if turn_undead:
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError(
                    "Turn Undead multi-actor settlement requires the Agent in the DM role"
                )
            declared = dict(declaration or {})
            if set(declared) not in (
                {"option", "perception"},
                {"option", "perception", "sear_undead"},
            ):
                raise _support.CombatEngineError(
                    "Turn Undead declaration requires option, perception, and optional sear_undead"
                )
            if "sear_undead" in declared and not isinstance(declared["sear_undead"], bool):
                raise _support.CombatEngineError("Turn Undead sear_undead must be boolean")
            perceptions = declared.get("perception")
            if not isinstance(perceptions, list):
                raise _support.CombatEngineError("Turn Undead perception must be a list")
            source_combatant = next(
                (
                    item
                    for item in encounter.get("combatants", [])
                    if item.get("actor_id") == actor_id
                ),
                None,
            )
            source_position = dict((source_combatant or {}).get("position") or {})
            if set(source_position) != {"x", "y"}:
                raise _support.NeedsRulingError(
                    "Turn Undead requires the cleric's battle-map position",
                    missing=("turn_undead_source_position",),
                )
            eligible: dict[str, Any] = {}
            for combatant in encounter.get("combatants", []):
                target_id = str(combatant.get("actor_id") or "")
                if target_id == actor_id or "dead" in {
                    str(item).casefold() for item in combatant.get("conditions", [])
                }:
                    continue
                target = self.characters.get(target_id)
                creature_type = str(
                    target.sheet.get("progression", {}).get("species") or ""
                ).casefold()
                if "undead" not in creature_type:
                    continue
                target_position = dict(combatant.get("position") or {})
                if set(target_position) != {"x", "y"}:
                    raise _support.NeedsRulingError(
                        "Turn Undead requires each undead target's battle-map position",
                        missing=(f"turn_undead_target_position:{target_id}",),
                    )
                distance = (
                    max(
                        abs(int(source_position["x"]) - int(target_position["x"])),
                        abs(int(source_position["y"]) - int(target_position["y"])),
                    )
                    * 5
                )
                if distance <= 30:
                    eligible[target_id] = {"record": target, "distance_ft": distance}
            normalized_perception: dict[str, dict[str, Any]] = {}
            for item in perceptions:
                if not isinstance(item, dict) or set(item) - {
                    "target_id",
                    "can_see_or_hear",
                    "reason",
                }:
                    raise _support.CombatEngineError(
                        "each Turn Undead perception entry requires target_id, "
                        "can_see_or_hear, and optional reason"
                    )
                target_id = str(item.get("target_id") or "")
                if target_id in normalized_perception or target_id not in eligible:
                    raise _support.CombatEngineError(
                        "Turn Undead perception targets must be unique eligible undead"
                    )
                can_perceive = item.get("can_see_or_hear")
                if not isinstance(can_perceive, bool):
                    raise _support.CombatEngineError("can_see_or_hear must be boolean")
                reason = str(item.get("reason") or "").strip()
                if not can_perceive and not reason:
                    raise _support.CombatEngineError(
                        "an excluded Turn Undead target requires a sensory reason"
                    )
                normalized_perception[target_id] = {
                    "target_id": target_id,
                    "can_see_or_hear": can_perceive,
                    "reason": reason,
                    "distance_ft": eligible[target_id]["distance_ft"],
                }
            if set(normalized_perception) != set(eligible):
                raise _support.CombatEngineError(
                    "Turn Undead must adjudicate every living undead within 30 feet"
                )
            for target_id, perception in normalized_perception.items():
                if not perception["can_see_or_hear"]:
                    continue
                target = eligible[target_id]["record"]
                self.access.require_actor(campaign_id, target_id, principal_id, control=True)
                turn_target_records[target_id] = target
                turn_targets[target_id] = self.combat_actor_snapshot(target_id)
            if not turn_targets:
                raise _support.CombatEngineError(
                    "Turn Undead has no undead within 30 feet that can see or hear the cleric"
                )
        lay_on_hands = str(activity_id).endswith("paladin-lay-on-hands")
        lay_on_hands_target_record = None
        lay_on_hands_target_id = ""
        additional_updates: list[_support.CharacterStateUpdate] = []
        if lay_on_hands:
            if activity_source_card_kind != "feature":
                raise _support.RulesetUnavailableError(
                    "Lay on Hands must be recorded as a Paladin feature"
                )
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError(
                    "Lay on Hands multi-actor settlement requires the Agent in the DM role"
                )
            declared = dict(declaration or {})
            mode = str(declared.get("mode") or "").strip().casefold()
            expected = {"target_id", "mode"}
            expected.add(
                "amount" if mode == "heal" else "effect_id" if mode == "cure" else "invalid"
            )
            if set(declared) != expected or mode not in {"heal", "cure"}:
                raise _support.CombatEngineError(
                    "Lay on Hands declaration requires target_id, mode, and amount for "
                    "healing or effect_id for curing"
                )
            lay_on_hands_target_id = str(declared.get("target_id") or "").strip()
            if not lay_on_hands_target_id:
                raise _support.CombatEngineError("Lay on Hands requires target_id")
            combatants_by_id = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            source_combatant = combatants_by_id.get(actor_id)
            target_combatant = combatants_by_id.get(lay_on_hands_target_id)
            if source_combatant is None or target_combatant is None:
                raise _support.CombatEngineError(
                    "Lay on Hands source and target must be current combatants"
                )
            if lay_on_hands_target_id == actor_id:
                distance = 0
            else:
                source_position = dict(source_combatant.get("position") or {})
                target_position = dict(target_combatant.get("position") or {})
                if set(source_position) != {"x", "y"} or set(target_position) != {"x", "y"}:
                    raise _support.NeedsRulingError(
                        "Lay on Hands requires source and target battle-map positions",
                        missing=("lay_on_hands_positions",),
                    )
                distance = (
                    max(
                        abs(int(source_position["x"]) - int(target_position["x"])),
                        abs(int(source_position["y"]) - int(target_position["y"])),
                    )
                    * 5
                )
            if distance > 5:
                raise _support.CombatEngineError("Lay on Hands target is outside touch range")
            lay_on_hands_target_record = self.require_campaign_actor(
                campaign_id, lay_on_hands_target_id
            )
            self.access.require_actor(
                campaign_id, lay_on_hands_target_id, principal_id, control=True
            )
        preserve_life = str(activity_id).endswith(
            "life-domain-channel-divinity-preserve-life"
        ) or str(activity_id).endswith("life-domain-preserve-life")
        preserve_target_records: dict[str, Any] = {}
        preserve_target_sheets: dict[str, dict[str, Any]] = {}
        preserve_allocations: list[dict[str, Any]] = []
        if preserve_life:
            if not self.is_dm(campaign_id, principal_id):
                raise PermissionError(
                    "Preserve Life multi-actor settlement requires the Agent in the DM role"
                )
            declared = dict(declaration or {})
            if set(declared) != {"allocations"}:
                raise _support.CombatEngineError(
                    "Preserve Life declaration requires only an allocations list"
                )
            raw_allocations = declared.get("allocations")
            if not isinstance(raw_allocations, list) or not raw_allocations:
                raise _support.CombatEngineError("Preserve Life requires at least one allocation")
            combatants_by_id = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            source_position = dict(dict(combatants_by_id.get(actor_id) or {}).get("position") or {})
            if set(source_position) != {"x", "y"}:
                raise _support.NeedsRulingError(
                    "Preserve Life requires the cleric's battle-map position",
                    missing=("preserve_life_source_position",),
                )
            for allocation in raw_allocations:
                if not isinstance(allocation, dict) or set(allocation) != {
                    "target_id",
                    "amount",
                }:
                    raise _support.CombatEngineError(
                        "each combat Preserve Life allocation requires target_id and amount"
                    )
                target_id = str(allocation.get("target_id") or "").strip()
                target_combatant = combatants_by_id.get(target_id)
                if target_combatant is None:
                    raise _support.CombatEngineError(
                        "Preserve Life targets must be current combatants"
                    )
                target_position = dict(target_combatant.get("position") or {})
                if set(target_position) != {"x", "y"}:
                    raise _support.NeedsRulingError(
                        "Preserve Life requires each target's battle-map position",
                        missing=(f"preserve_life_target_position:{target_id}",),
                    )
                distance = (
                    max(
                        abs(int(source_position["x"]) - int(target_position["x"])),
                        abs(int(source_position["y"]) - int(target_position["y"])),
                    )
                    * 5
                )
                if distance > 30:
                    raise _support.CombatEngineError(
                        f"Preserve Life target is outside 30 feet: {target_id}"
                    )
                target = self.characters.get(target_id)
                if target.campaign_id != campaign_id:
                    raise _support.CombatEngineError(
                        "Preserve Life targets must share the campaign"
                    )
                self.access.require_actor(campaign_id, target_id, principal_id, control=True)
                preserve_target_records[target_id] = target
                preserve_target_sheets[target_id] = target.sheet
                preserve_allocations.append(
                    {"target_id": target_id, "amount": allocation.get("amount")}
                )
            _support.resolve_preserve_life_to_sheets(
                current.sheet,
                preserve_target_sheets,
                allocations=preserve_allocations,
            )
        repair_settlement: dict[str, Any] | None = None
        if steel_defender_repair:
            assert repair_target_record is not None
            relation = next(
                item
                for item in _support.validate_dependent_actor_relations(
                    dict(campaign.state or {}).get("dependent_actor_relations", [])
                )
                if item["dependent_actor_id"] == actor_id and item["status"] == "active"
            )
            try:
                repair_settlement = _support.repair_steel_defender(
                    current.sheet,
                    (None if repair_target_record.id == actor_id else repair_target_record.sheet),
                    proficiency_bonus=int(
                        dict(relation["template_binding"])["numeric_parameters"][
                            "owner_proficiency_bonus"
                        ]
                    ),
                    distance_ft=repair_distance_ft,
                    target_kind=repair_target_kind,
                    activity_id=activity_id,
                )
            except _support.SteelDefenderError as error:
                raise _support.CombatEngineError(str(error)) from error
            applied = {
                "sheet": repair_settlement["defender_sheet"],
                "activation": _support.deepcopy(activity_card.get("activation") or {}),
                "payment": _support.deepcopy(repair_settlement["payment"]),
                "rule_receipts": [],
            }
        elif lay_on_hands:
            applied = {
                "sheet": _support.deepcopy(current.sheet),
                "activity_id": activity_id,
                "content_type": "features",
                "name": str(activity_card.get("name") or activity_id),
                "activation": _support.deepcopy(activity_card.get("activation") or {}),
                "payment": None,
                "choices": {},
                "requires_ruling": False,
                "ruling_requirement": None,
                "status": "committed",
                "rule_receipts": [],
            }
        elif scag_bladesong_dismiss:
            applied = {
                "sheet": _support.deepcopy(current.sheet),
                "activity_id": activity_id,
                "content_type": "features",
                "name": str(activity_card.get("name") or activity_id),
                "activation": {"type": "passive"},
                "payment": {"kind": "dismissal"},
                "choices": {},
                "requires_ruling": False,
                "ruling_requirement": None,
                "status": "committed",
                "rule_receipts": [],
            }
        else:
            try:
                applied = _support.consume_activity(
                    current.sheet,
                    activity_id=activity_id,
                    rules=rule_context,
                )
            except _support.ActivityError as exc:
                raise _support.CombatEngineError(str(exc)) from exc
        if applied.get("status") in _support.PENDING_RULE_RESULT_STATUSES:
            return {
                **_support._ruling_status(
                    applied["status"],
                    _support._pending_result_ruling_kind(applied),
                ),
                "result": {key: value for key, value in applied.items() if key != "sheet"},
                "campaign_revision": campaign.revision,
            }
        harmful_activity_target_ids: list[str] = []
        if dragonborn_breath:
            harmful_activity_target_ids.extend(
                str(context.get("target_id") or "")
                for context in breath_target_contexts
                if isinstance(context, dict)
            )
        if turn_undead:
            harmful_activity_target_ids.extend(str(target_id) for target_id in turn_targets)
        if divine_spark and str(dict(declaration or {}).get("mode") or "").casefold() == "damage":
            if divine_spark_target is not None:
                harmful_activity_target_ids.append(str(divine_spark_target.get("id") or ""))
        if bound_activity_plan is not None:
            harmful_activity_target_ids.extend(
                self.semantic_plan_harmful_target_ids(bound_activity_plan)
            )
        # Check every known harmful target before consuming the action or resource.
        _support.require_harmful_targeting_allowed(
            self.combat_actor_snapshot(actor_id),
            target_ids=harmful_activity_target_ids,
            known_actor_ids=self.encounter_actor_ids(encounter),
        )
        activation_type = str(applied["activation"].get("type") or "")
        if activation_type == "reaction":
            window = next(
                (
                    item
                    for item in encounter.get("pending", [])
                    if item.get("id") == choice_id
                    and item.get("kind") == "reaction"
                    and item.get("actor_id") == actor_id
                    and item.get("status", "pending") == "pending"
                ),
                None,
            )
            if window is None:
                raise _support.CombatEngineError(
                    "a reaction activity requires its owned pending reaction choice_id"
                )
            if any(
                item.get("status", "pending") == "pending" and item.get("id") != choice_id
                for item in encounter.get("pending", [])
            ):
                raise _support.CombatEngineError("resolve the earlier pending save or choice first")
        else:
            self.require_no_blocking_pending(encounter)
        if scag_bladesong_dismiss:
            ended = []
            for effect in applied["sheet"].get("effects", []):
                if (
                    effect.get("active")
                    and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
                ):
                    effect["active"] = False
                    effect["ended_reason"] = "voluntary_dismissal"
                    ended.append(str(effect.get("id") or ""))
            if not ended:
                raise _support.CombatEngineError("Bladesong is not active")
        engine_owned_special = activity_id in {
            "dnd5e.content.srd2014.feature.fighter-action-surge",
            "dnd5e.content.srd2024.feature.fighter-action-surge",
        }
        if (
            activation_type == "special"
            and not engine_owned_special
            and not self.is_dm(campaign_id, principal_id)
        ):
            raise _support.CombatEngineError("special activity triggers require a DM resolution")
        if legendary_spec is not None:
            next_encounter, activity_activation_payment = _support.pay_legendary_action(
                encounter,
                actor_id_value=actor_id,
                activity_id=activity_id,
                spec=legendary_spec,
            )
        elif scag_bladesong_dismiss:
            next_encounter = _support.deepcopy(encounter)
            activity_activation_payment = {"kind": "dismissal"}
        else:
            next_encounter = _support.pay_activity_activation(
                encounter,
                actor_id_value=actor_id,
                activation_type=activation_type,
                action_kind=str(dict(activity_card.get("choices") or {}).get("action_kind") or "")
                or None,
            )
            activity_activation_payment = {
                "kind": "activity",
                "activation_type": activation_type,
            }
        activity_ended_tethers = _support.newly_ended_witch_bolt_tethers(
            encounter,
            next_encounter,
            source_actor_id=actor_id,
        )
        if activity_ended_tethers:
            applied["sheet"] = _support.end_tether_concentrations(
                applied["sheet"],
                activity_ended_tethers,
            )["sheet"]
        next_encounter, core_effect = _support.settle_core_activity_effect(
            next_encounter,
            actor_id_value=actor_id,
            activity_id=activity_id,
            declaration=declaration,
            source_card=activity_card,
        )
        if lay_on_hands:
            assert lay_on_hands_target_record is not None
            settled_lay = _support.resolve_lay_on_hands_to_sheets(
                applied["sheet"],
                lay_on_hands_target_record.sheet,
                mode=str(dict(declaration or {}).get("mode") or ""),
                amount=dict(declaration or {}).get("amount"),
                effect_id=dict(declaration or {}).get("effect_id"),
            )
            if lay_on_hands_target_id == actor_id:
                self_sheet = _support.deepcopy(settled_lay["target_sheet"])
                self_sheet["resources"] = _support.deepcopy(
                    settled_lay["source_sheet"]["resources"]
                )
                applied["sheet"] = _support.validate_character_sheet(self_sheet)
            else:
                applied["sheet"] = settled_lay["source_sheet"]
                target_sheet = _support.validate_character_sheet(settled_lay["target_sheet"])
                self.sync_combatant_conditions(next_encounter, lay_on_hands_target_id, target_sheet)
                additional_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=lay_on_hands_target_id,
                        sheet=target_sheet,
                        notes=_support.validate_character_notes(lay_on_hands_target_record.notes),
                        expected_revision=lay_on_hands_target_record.revision,
                    )
                )
            core_effect = {
                key: value
                for key, value in settled_lay.items()
                if key not in {"source_sheet", "target_sheet"}
            }
            core_effect["activation_payment"] = activity_activation_payment
            core_effect["distance_ft"] = 0 if lay_on_hands_target_id == actor_id else distance
            core_effect["requires_ruling"] = False
        if scag_bladesong_activity and not scag_bladesong_dismiss:
            derived_before_song = self.derive_character_sheet(
                applied["sheet"], character_id=actor_id
            )
            intelligence = int(
                dict(derived_before_song.get("ability_modifiers") or {}).get("intelligence", 0) or 0
            )
            intelligence = max(1, intelligence)
            if any(
                effect.get("active")
                and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
                for effect in applied["sheet"].get("effects", [])
            ):
                raise _support.CombatEngineError("Bladesong is already active")
            bladesong_effect_id = "scag-bladesong-active"
            if any(
                str(effect.get("id") or "") == bladesong_effect_id
                for effect in applied["sheet"].get("effects", [])
            ):
                bladesong_effect_id = f"{bladesong_effect_id}-{_support.uuid4().hex}"
            applied["sheet"], effect = _support.add_effect(
                applied["sheet"],
                {
                    "id": bladesong_effect_id,
                    "name": "Bladesong",
                    "kind": "scag_bladesong",
                    "source": activity_id,
                    "active": True,
                    # SCAG/errata: Bladesong lasts one minute.  Keep this on
                    # the narrative minute clock so a 59-second advance does
                    # not expire it while the exact 60-second boundary does.
                    "duration": {"period": "minute", "remaining": 1},
                    "changes": [
                        {
                            "path": "derived.armor_class",
                            "mode": "add",
                            "value": intelligence,
                        },
                        {"path": "combat.speed.walk", "mode": "add", "value": 10},
                        {"path": "rolls.ability_check.advantage", "mode": "set", "value": True},
                        {"path": "rolls.saving_throw.bonus", "mode": "add", "value": intelligence},
                    ],
                    "metadata": {
                        "scag_bladesong": True,
                        "skill": "acrobatics",
                        "save_purpose": "concentration",
                    },
                },
            )
            core_effect = {
                "kind": "scag_bladesong",
                "bladesong_effect": _support.deepcopy(effect),
                "activation_payment": activity_activation_payment,
                "requires_ruling": False,
            }
        if repair_settlement is not None:
            assert repair_target_record is not None
            target_sheet = _support.validate_character_sheet(repair_settlement["target_sheet"])
            self.sync_combatant_conditions(next_encounter, repair_target_record.id, target_sheet)
            if repair_target_record.id != actor_id:
                additional_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=repair_target_record.id,
                        sheet=target_sheet,
                        notes=_support.validate_character_notes(repair_target_record.notes),
                        expected_revision=repair_target_record.revision,
                    )
                )
            core_effect = {
                "kind": "steel_defender_repair",
                "target_id": repair_target_record.id,
                "target_kind": repair_target_kind,
                "distance_ft": repair_distance_ft,
                "roll": _support.deepcopy(repair_settlement["roll"]),
                "healing": _support.deepcopy(repair_settlement["healing"]),
                "spatial_facts": _support.deepcopy(repair_spatial_facts),
                "requires_ruling": False,
            }
        if legendary_spec is not None:
            legendary_effect = dict(legendary_spec.get("effect") or {})
            legendary_kind = str(legendary_effect.get("kind") or "")
            if legendary_kind == "skill_check":
                source_actor = self.combat_actor_snapshot(actor_id)
                source_actor["sheet"] = applied["sheet"]
                source_actor["derived"] = self.derive_character_sheet(
                    applied["sheet"], character_id=actor_id
                )
                core_effect = {
                    "kind": "legendary_action",
                    "effect_kind": "skill_check",
                    "check": _support.resolve_actor_check(
                        source_actor,
                        kind="ability",
                        ability=str(legendary_effect["skill"]),
                        dc=0,
                        encounter=next_encounter,
                        ruleset=str(next_encounter.get("ruleset") or "2014"),
                        rules=rule_context,
                    ),
                    "activation_payment": activity_activation_payment,
                    "requires_ruling": False,
                }
            else:
                core_effect = {
                    "kind": "legendary_action",
                    "effect_kind": "weapon_attack",
                    "weapon_id": str(legendary_effect["weapon_id"]),
                    "attack_mode": str(legendary_effect["attack_mode"]),
                    "attack_pending": True,
                    "activation_payment": activity_activation_payment,
                    "requires_ruling": False,
                }
        actor_knowledge_transfers: list[_support.ActorKnowledgeTransfer] = []
        if dragonborn_breath:
            breath_results: list[dict[str, Any]] = []
            damage_roll = None
            if breath_targets:
                save_ability = str(breath_spec.get("save_ability") or "").casefold()
                save_bonuses = {
                    str(context["target_id"]): (
                        {"none": 0, "half": 2, "three_quarters": 5}[str(context["cover"])]
                        if save_ability == "dexterity"
                        else 0
                    )
                    for context in breath_target_contexts
                }
                combatants_by_id = {
                    str(item.get("actor_id") or ""): item
                    for item in next_encounter.get("combatants", [])
                }
                settled_breath = _support.resolve_save_damage_to_sheets(
                    breath_targets,
                    save_ability=save_ability,
                    save_dc=breath_save_dc,
                    damage_expression=breath_damage_expression,
                    damage_type=str(breath_spec.get("damage_type") or ""),
                    half_on_success=True,
                    source=f"standard-activity:{activity_id}",
                    encounter=next_encounter,
                    death_saves_by_actor_id={
                        target_id: self.combatant_zero_hp_buffered(combatants_by_id[target_id])
                        for target_id in breath_target_records
                    },
                    save_bonuses_by_actor_id=save_bonuses,
                    ruleset=self.encounter_rules_edition(campaign_id, next_encounter),
                    rules=_support.context_with_facts(
                        rule_context,
                        save_source_kind="nonmagical_effect",
                        save_effect_conditions=[],
                        # This is the validated Dragonborn ancestry breath
                        # table, not a generic poison-damage heuristic.
                        save_against_poison=breath_spec["damage_type"] == "poison",
                    ),
                )
                breath_results = list(settled_breath["result"]["targets"])
                damage_roll = _support.deepcopy(settled_breath["result"]["damage_roll"])
                for target_result in breath_results:
                    target_id = str(target_result["target_id"])
                    target_sheet = _support.validate_character_sheet(
                        settled_breath["sheets"][target_id]
                    )
                    self.sync_combatant_conditions(
                        next_encounter,
                        target_id,
                        target_sheet,
                    )
                    _support.reconcile_readied_spells(
                        next_encounter,
                        target_id,
                        target_sheet,
                    )
                    damage = dict(target_result.get("damage") or {})
                    self.add_concentration_window(
                        next_encounter,
                        target_id,
                        dict(damage.get("concentration") or {}) or None,
                        next_revision=campaign.revision + 1,
                    )
                    target = breath_target_records[target_id]
                    additional_updates.append(
                        _support.CharacterStateUpdate(
                            character_id=target_id,
                            sheet=target_sheet,
                            notes=_support.validate_character_notes(target.notes),
                            expected_revision=target.revision,
                        )
                    )
            core_effect = {
                "kind": "dragonborn_breath_weapon",
                "save_ability": str(breath_spec.get("save_ability") or ""),
                "save_dc": breath_save_dc,
                "damage_expression": breath_damage_expression,
                "damage_type": str(breath_spec.get("damage_type") or ""),
                "damage_roll": damage_roll,
                "target_contexts": breath_target_contexts,
                "targets": breath_results,
                "activation_payment": activity_activation_payment,
                "requires_ruling": False,
            }
        if turn_undead:
            source_actor = self.combat_actor_snapshot(actor_id)
            source_actor["sheet"] = applied["sheet"]
            source_actor["derived"] = self.derive_character_sheet(
                applied["sheet"], character_id=actor_id
            )
            settled_turn = _support.resolve_turn_undead_to_sheets(
                source_actor,
                turn_targets,
                rules=rule_context,
                sear_undead=bool(dict(declaration or {}).get("sear_undead", False)),
            )
            turn_dependencies: list[dict[str, Any]] = []
            for target_result in settled_turn["targets"]:
                target_id = str(target_result["target_id"])
                if not target_result["turned"] and not target_result.get("sear_damage"):
                    continue
                target_sheet = _support.validate_character_sheet(settled_turn["sheets"][target_id])
                self.sync_combatant_conditions(next_encounter, target_id, target_sheet)
                target_combatant = next(
                    item
                    for item in next_encounter["combatants"]
                    if item.get("actor_id") == target_id
                )
                if target_result["turned"]:
                    target_combatant["turned"] = {
                        "source_actor_id": actor_id,
                        "effect_id": target_result["effect_id"],
                    }
                    target_combatant.setdefault("turn_budget", {})["reaction"] = 0
                if settled_turn["edition"] == "2024" and target_result["turned"]:
                    dependency = {
                        "id": f"effect-dependency-{_support.uuid4().hex}",
                        "mechanic_id": "dnd5e.core.activity.turn_undead",
                        "dependency": "source_actor_capable",
                        "source_actor_id": actor_id,
                        "target_actor_id": target_id,
                        "target_effect_id": target_result["effect_id"],
                        "active": True,
                    }
                    next_encounter.setdefault("dependent_effects", []).append(dependency)
                    turn_dependencies.append(dependency)
                sear_damage = dict(target_result.get("sear_damage") or {})
                concentration = dict(sear_damage.get("concentration") or {})
                self.add_concentration_window(
                    next_encounter,
                    target_id,
                    concentration or None,
                    next_revision=campaign.revision + 1,
                )
                target = turn_target_records[target_id]
                additional_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=target_sheet,
                        notes=_support.validate_character_notes(target.notes),
                        expected_revision=target.revision,
                    )
                )
            core_effect = {
                "kind": "turn_undead",
                "save_dc": settled_turn["save_dc"],
                "duration": settled_turn["duration"],
                "sear_undead": settled_turn["sear_undead"],
                "targets": settled_turn["targets"],
                "dependencies": turn_dependencies,
                "requires_ruling": False,
            }
        if divine_spark:
            assert divine_spark_target_record is not None
            assert divine_spark_target is not None
            source_actor = self.combat_actor_snapshot(actor_id)
            source_actor["sheet"] = applied["sheet"]
            source_actor["derived"] = self.derive_character_sheet(
                applied["sheet"], character_id=actor_id
            )
            settled_spark = _support.resolve_divine_spark_to_sheet(
                source_actor,
                divine_spark_target,
                mode=str(dict(declaration or {}).get("mode") or ""),
                damage_type=dict(declaration or {}).get("damage_type"),
                rules=rule_context,
            )
            target_id = str(settled_spark["target_id"])
            target_sheet = _support.validate_character_sheet(settled_spark.pop("sheet"))
            self.sync_combatant_conditions(next_encounter, target_id, target_sheet)
            _support.reconcile_readied_spells(next_encounter, target_id, target_sheet)
            if settled_spark.get("damage") is not None:
                concentration = dict(
                    dict(settled_spark.get("damage") or {}).get("concentration") or {}
                )
                self.add_concentration_window(
                    next_encounter,
                    target_id,
                    concentration or None,
                    next_revision=campaign.revision + 1,
                )
            additional_updates.append(
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=target_sheet,
                    notes=_support.validate_character_notes(divine_spark_target_record.notes),
                    expected_revision=divine_spark_target_record.revision,
                )
            )
            core_effect = {
                **settled_spark,
                "activation_payment": activity_activation_payment,
                "requires_ruling": False,
            }
        if preserve_life:
            settled_inputs = {
                target_id: (applied["sheet"] if target_id == actor_id else target.sheet)
                for target_id, target in preserve_target_records.items()
            }
            settled_preserve = _support.resolve_preserve_life_to_sheets(
                applied["sheet"],
                settled_inputs,
                allocations=preserve_allocations,
            )
            if actor_id in settled_preserve["sheets"]:
                applied["sheet"] = settled_preserve["sheets"][actor_id]
            for target_id, target in preserve_target_records.items():
                if target_id == actor_id:
                    continue
                target_sheet = _support.validate_character_sheet(
                    settled_preserve["sheets"][target_id]
                )
                self.sync_combatant_conditions(next_encounter, target_id, target_sheet)
                additional_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=target_sheet,
                        notes=_support.validate_character_notes(target.notes),
                        expected_revision=target.revision,
                    )
                )
            core_effect = {
                "kind": "preserve_life",
                "edition": settled_preserve["edition"],
                "pool": settled_preserve["pool"],
                "allocated": settled_preserve["allocated"],
                "remaining_unallocated": settled_preserve["remaining_unallocated"],
                "targets": settled_preserve["targets"],
                "activation_payment": activity_activation_payment,
                "requires_ruling": False,
            }
        if activity_id in _support.SECOND_WIND_ACTIVITY_IDS:
            second_wind = _support.resolve_second_wind_to_sheet(applied["sheet"])
            applied["sheet"] = second_wind.pop("sheet")
            core_effect = second_wind
        if core_effect is not None:
            applied["requires_ruling"] = bool(core_effect.get("requires_ruling", False))
            applied["core_effect"] = core_effect
            core_effect_kind = str(core_effect["kind"])
            if core_effect_kind == "scag_bladesong":
                # The executable effect is defined by the exact locked SCAG
                # feature card already attached to this character.
                applied["rule_receipts"] = list(applied.get("rule_receipts") or [])
            elif core_effect_kind == "steel_defender_repair":
                assert steel_defender_contract is not None
                applied["rule_receipts"] = [
                    *list(applied.get("rule_receipts") or []),
                    {
                        "mechanic_id": "dnd5e.expansion.steel_defender.repair",
                        "event": "combat.activity.steel_defender_repair",
                        "operations": [{"op": "builtin.expansion_provider"}],
                        "citations": [
                            {
                                "source_artifact_id": steel_defender_contract["source_artifact_id"],
                                "source_pack_id": steel_defender_contract["source_pack_id"],
                                "source_pack_version": steel_defender_contract[
                                    "source_pack_version"
                                ],
                                "reviewed_expression_hash": steel_defender_contract[
                                    "reviewed_expression_hash"
                                ],
                            }
                        ],
                        "ruleset_fingerprint": rule_context.fingerprint,
                    },
                ]
            else:
                mechanic_id = {
                    "action_surge": "dnd5e.core.activity.action_surge",
                    "cunning_action": "dnd5e.core.activity.cunning_action",
                    "orc_aggressive": _support.CORE_ORC_AGGRESSIVE_MECHANIC_ID,
                    "divine_spark": "dnd5e.core.activity.divine_spark",
                    "dragonborn_breath_weapon": _support.CORE_DRAGONBORN_BREATH_MECHANIC_ID,
                    "legendary_action": ("dnd5e.core.activity.legendary_action"),
                    "second_wind": "dnd5e.core.activity.second_wind",
                    "preserve_life": "dnd5e.core.activity.preserve_life",
                    "lay_on_hands": "dnd5e.core.activity.lay_on_hands",
                    "turn_undead": "dnd5e.core.activity.turn_undead",
                }[core_effect_kind]
                applied["rule_receipts"] = [
                    *list(applied.get("rule_receipts") or []),
                    *(
                        _support.core_receipts(
                            rule_context,
                            [mechanic_id],
                            f"combat.activity.{core_effect_kind}",
                        )
                    ),
                    *(
                        _support.core_receipts(
                            rule_context,
                            ["dnd5e.core.activity.sear_undead"],
                            "combat.activity.sear_undead",
                        )
                        if core_effect_kind == "turn_undead"
                        and core_effect.get("sear_undead") is not None
                        else []
                    ),
                ]
        if compiled_activity_plan is not None:
            applied["requires_ruling"] = True
            applied["semantic_plan"] = {
                "status": "paid",
                "contract": _support.resolution_plan_contract(compiled_activity_plan),
                "commitment": _support.deepcopy(
                    dict(declaration or {}).get("agent_resolution_commitment")
                ),
            }
        if activation_type == "reaction":
            assert choice_id is not None
            next_encounter = _support.resolve_choice_window(
                next_encounter,
                choice_id=choice_id,
                actor_id_value=actor_id,
                selection={"id": activity_id, "kind": "reaction_activity"},
            )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "activity",
                "actor_id": actor_id,
                "activity_id": activity_id,
                "declaration": declaration or {},
                "requires_ruling": applied["requires_ruling"],
                "round": int(next_encounter.get("round", 1) or 1),
                "turn_index": int(next_encounter.get("turn_index", 0) or 0),
            },
        ][-100:]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        result = {key: value for key, value in applied.items() if key != "sheet"}
        result["declaration"] = declaration or {}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.activity.use",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                **_support._ruling_status(
                    "pending_ruling" if applied["requires_ruling"] else "committed",
                    (
                        "module_specific_procedure"
                        if compiled_activity_plan is not None
                        else "descriptive_activity"
                    ),
                ),
                "result": result,
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                ),
                *additional_updates,
            ],
            actor_knowledge_transfers=actor_knowledge_transfers,
            rule_receipts=list(applied.get("rule_receipts") or []),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_resolve_hide(
        self,
        campaign_id: str,
        actor_id: str,
        ruling: dict[str, Any],
        observer_ids: list[str] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a paid Cunning Action Hide attempt without another payment."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        raw_ruling = dict(ruling or {})
        if set(raw_ruling) - {"can_hide", "reason", "observers"}:
            raise _support.CombatEngineError(
                "Hide ruling accepts only can_hide, reason, and optional observers"
            )
        can_hide = raw_ruling.get("can_hide")
        if not isinstance(can_hide, bool):
            raise _support.CombatEngineError("Hide ruling can_hide must be boolean")
        reason = " ".join(str(raw_ruling.get("reason") or "").split())
        if not reason or len(reason) > 500:
            raise _support.CombatEngineError("Hide ruling requires a bounded reason")
        supplied_observer_ids = observer_ids
        embedded_observers = raw_ruling.get("observers")
        if supplied_observer_ids is not None and embedded_observers is not None:
            raise _support.CombatEngineError(
                "provide Hide observers either in ruling or observer_ids"
            )
        if supplied_observer_ids is None:
            supplied_observer_ids = embedded_observers
        if not isinstance(supplied_observer_ids, list):
            raise _support.CombatEngineError("Hide ruling requires an observers list")
        normalized_observer_ids: list[str] = []
        for item in supplied_observer_ids:
            if isinstance(item, dict):
                if set(item) - {"observer_id", "reason"}:
                    raise _support.CombatEngineError(
                        "Hide observer entries accept observer_id and optional reason"
                    )
                observer_id = str(item.get("observer_id") or "").strip()
            else:
                observer_id = str(item or "").strip()
            if not observer_id:
                raise _support.CombatEngineError("Hide observers require non-empty observer IDs")
            normalized_observer_ids.append(observer_id)
        if len(normalized_observer_ids) != len(set(normalized_observer_ids)):
            raise _support.CombatEngineError("Hide observers must be unique")
        normalized_ruling = {
            "can_hide": can_hide,
            "reason": reason,
            "observers": list(normalized_observer_ids),
        }
        payload = {
            "actor_id": actor_id,
            "ruling": normalized_ruling,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-resolve-hide:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        stream = _support.active_random_stream()
        if stream is None:
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign.state,
                operation="combat_resolve_hide",
                idempotency_key=idempotency_key,
                campaign_revision=campaign.revision,
            )
            with _support.use_random_stream(stream):
                return self.combat_resolve_hide(
                    campaign_id,
                    actor_id,
                    ruling=normalized_ruling,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                )
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
        )
        if (
            stream.campaign_id != campaign_id
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
        ):
            raise _support.CombatEngineError(
                "Hide settlement requires the current campaign random snapshot"
            )
        self.require_no_blocking_pending(encounter)
        self.require_encounter_combatant(encounter, actor_id, role="Hide actor")
        actor = self.combat_actor_snapshot(actor_id)
        observer_passive_perceptions: dict[str, int] = {}
        for observer_id in normalized_observer_ids:
            self.require_encounter_combatant(encounter, observer_id, role="Hide observer")
            observer = self.require_campaign_actor(campaign_id, observer_id)
            observer_snapshot = self.combat_actor_snapshot(observer.id)
            passive = dict(observer_snapshot.get("derived") or {}).get("passive_perception")
            if isinstance(passive, bool) or not isinstance(passive, int):
                raise _support.CombatEngineError(
                    f"observer {observer_id} has no authoritative passive Perception"
                )
            observer_passive_perceptions[observer_id] = int(passive)
        hide_rules = self.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": actor_id,
                "action": "hide",
                "kind": "ability",
                "ability": "stealth",
                "observers": list(normalized_observer_ids),
                "can_hide": can_hide,
            },
            branch_id=resolved_branch_id,
        )
        next_encounter, hide_effect = _support.settle_hide(
            encounter,
            actor=actor,
            actor_id_value=actor_id,
            observer_ids=normalized_observer_ids,
            observer_passive_perceptions=observer_passive_perceptions,
            can_hide=can_hide,
            ruling_reason=reason,
            rules=hide_rules,
            rng=stream,
        )
        stealth_check = dict(hide_effect.get("stealth_check") or {})
        receipts = [
            *list(stealth_check.get("rule_receipts") or []),
            *_support.core_receipts(
                hide_rules,
                ["dnd5e.core.activity.cunning_action"],
                "combat.activity.cunning_action.hide",
            ),
        ]
        prior_combatant = next(
            item
            for item in encounter.get("combatants", [])
            if str(item.get("actor_id") or "") == actor_id
        )
        source_activity_id = str(
            dict(dict(prior_combatant.get("turn_flags") or {}).get("hide_declared") or {}).get(
                "source_activity_id"
            )
            or ""
        )
        result = {
            "kind": "cunning_action_hide",
            "action": "hide",
            "activity_id": source_activity_id,
            "core_effect": hide_effect,
            "payment": {
                "kind": "activity",
                "activation_type": "bonus_action",
                "already_paid": True,
            },
            "requires_ruling": False,
            "ruling": normalized_ruling,
            "rule_receipts": receipts,
        }
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        next_state["resolution_log"] = [
            *list(next_state.get("resolution_log") or []),
            {
                "id": f"resolution-{_support.uuid4().hex}",
                "type": "combat_hide",
                "operation": "combat.activity.cunning_action.hide",
                "actor_id": actor_id,
                "audience": {
                    "scope": "actors",
                    "actor_refs": [actor_id, *normalized_observer_ids],
                    "disclosure": "private",
                },
                "branch_id": resolved_branch_id,
                "campaign_revision": campaign.revision + 1,
                "result": _support.deepcopy(result),
            },
        ][-100:]
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.activity.cunning_action.hide",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": result,
                "combat": next_encounter,
            },
            rule_receipts=receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_check(
        self,
        campaign_id: str,
        actor_id: str,
        kind: str,
        ability: str = "",
        target_id: str | None = None,
        action: str | None = None,
        dc: int = 0,
        proficient: _support.StrictBool = False,
        bonus: int = 0,
        advantage: _support.StrictBool = False,
        disadvantage: _support.StrictBool = False,
        rule_facts: dict[str, Any] | None = None,
        spatial_facts: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a check/save/death-save or an atomic Medicine stabilization.

        kind=stabilize pays the action and rolls DC10 Medicine atomically. In
        Agent positioning supply spatial_facts={decision_id,reason,within_5_ft:true}.
        Grid positioning uses recorded positions. Do not prepay common_action.
        """
        if spatial_facts is not None and kind != "stabilize":
            raise _support.CombatEngineError("spatial_facts is accepted only for stabilization")
        proficient = _support._strict_boolean(proficient, "proficient")
        advantage = _support._strict_boolean(advantage, "advantage")
        disadvantage = _support._strict_boolean(disadvantage, "disadvantage")
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        settlement_facts = self.checked_rule_facts(rule_facts)
        if not self.is_dm(campaign_id, principal_id):
            if kind != "death_save":
                raise _support.CombatEngineError(
                    "checks and saves require an Agent-as-DM issued resolution"
                )
            if advantage or disadvantage or proficient or bonus:
                raise _support.CombatEngineError(
                    "death-save modifiers require Agent-as-DM adjudication"
                )
            if settlement_facts:
                raise _support.CombatEngineError(
                    "rule facts require an Agent-as-DM issued resolution"
                )
        if kind == "death_save" and settlement_facts:
            raise _support.CombatEngineError("rule facts are not accepted for death saves")
        if kind == "death_save" and target_id is not None:
            raise _support.CombatEngineError("death saves do not accept a target_id")
        if kind in {"death_save", "stabilize"} and action is not None:
            raise _support.CombatEngineError(f"{kind} manages its own action boundary")
        if kind != "death_save" and not str(ability).strip():
            raise _support.CombatEngineError(
                "ability is required for checks, saves, and stabilization"
            )
        normalized_check_action = (
            str(action).strip().lower().replace("-", "_") if action is not None else None
        )
        normalized_ability = str(ability).strip().casefold().replace(" ", "_")
        social_ability_names = {
            "charisma",
            "deception",
            "intimidation",
            "performance",
            "persuasion",
        }
        if normalized_check_action not in {
            None,
            "escape",
            "hide",
            "improvise",
            "influence",
            "search",
            "study",
            "utilize",
            "use_object",
        }:
            raise _support.CombatEngineError("unsupported action-bound check")
        actor: dict[str, Any] | None = None
        if normalized_check_action == "search":
            if kind not in _support.ABILITY_CHECK_KINDS:
                raise _support.CombatEngineError("Search requires an ability check")
            search_ruleset = self.campaign_rules_edition(campaign_id)
            allowed_search_checks = (
                {"perception", "investigation"}
                if search_ruleset == "2014"
                else {"wisdom", "wis", "insight", "medicine", "perception", "survival"}
            )
            if normalized_ability not in allowed_search_checks:
                if search_ruleset == "2014":
                    raise _support.CombatEngineError(
                        "2014 Search requires Wisdom (Perception) or Intelligence (Investigation)"
                    )
                raise _support.CombatEngineError(
                    "2024 Search requires a Wisdom check, optionally using Insight, "
                    "Medicine, Perception, or Survival"
                )
            if proficient or bonus:
                raise _support.CombatEngineError(
                    "Search derives its skill proficiency and modifier from the actor card"
                )
            actor = self.combat_actor_snapshot(actor_id)
            if normalized_ability not in {"wisdom", "wis"} and normalized_ability not in dict(
                actor["derived"].get("skills") or {}
            ):
                raise _support.CombatEngineError("Search skill is missing from the actor card")
        if kind == "stabilize":
            if not target_id:
                raise _support.CombatEngineError("stabilize requires target_id")
            if target_id == actor_id:
                raise _support.CombatEngineError("an unconscious actor cannot stabilize itself")
            if str(ability).casefold() not in {"wisdom", "wis", "medicine"}:
                raise _support.CombatEngineError("stabilize uses a Wisdom (Medicine) check")
            if dc not in {0, 10} or proficient or bonus:
                raise _support.CombatEngineError(
                    "stabilize derives its DC and Medicine modifier from the Core rules "
                    "and actor card"
                )
        elif target_id is not None and kind not in _support.ABILITY_CHECK_KINDS:
            raise _support.CombatEngineError(
                "target_id is accepted for ability checks and kind=stabilize"
            )
        elif target_id is not None:
            target_id = str(target_id).strip()
            if not target_id:
                raise _support.CombatEngineError("target_id must be non-empty")
            self.require_campaign_actor(campaign_id, target_id)
            if target_id == actor_id:
                raise _support.CombatEngineError("a social check target must be another actor")
            if kind not in _support.ABILITY_CHECK_KINDS:
                raise _support.CombatEngineError("target_id is accepted only for ability checks")
            if not (
                normalized_check_action == "influence" or normalized_ability in social_ability_names
            ):
                raise _support.CombatEngineError(
                    "target_id is accepted only for social ability checks"
                )
        if normalized_check_action == "influence" and target_id is None:
            raise _support.CombatEngineError("an influence check requires target_id")
        social_charm_advantage = False
        if (
            target_id is not None
            and kind in _support.ABILITY_CHECK_KINDS
            and (
                normalized_check_action == "influence" or normalized_ability in social_ability_names
            )
        ):
            source_snapshot = self.combat_actor_snapshot(actor_id)
            target_snapshot = self.combat_actor_snapshot(target_id)
            social_charm_advantage = _support.charmed_social_check_advantage(
                source_snapshot,
                target_snapshot,
                known_actor_ids={
                    str(item.id) for item in self.characters.list(campaign_id=campaign_id)
                },
            )
            if social_charm_advantage:
                advantage = True
        payload = {
            "actor_id": actor_id,
            "target_id": target_id,
            "action": normalized_check_action,
            "kind": kind,
            "ability": ability,
            "dc": dc,
            "proficient": proficient,
            "bonus": bonus,
            "advantage": advantage,
            "disadvantage": disadvantage,
            "rule_facts": settlement_facts,
            **({"spatial_facts": spatial_facts} if spatial_facts is not None else {}),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-check:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        death_save_combatant: dict[str, Any] | None = None
        stabilize_target: dict[str, Any] | None = None
        active_state = dict(campaign.state or {}).get("combat")
        if isinstance(active_state, dict) and active_state.get("active", False):
            self.require_encounter_combatant(active_state, actor_id, role="check actor")
            if target_id is not None and kind in _support.ABILITY_CHECK_KINDS:
                self.require_encounter_combatant(
                    active_state,
                    target_id,
                    role="social check target",
                )
        prepaid_search_encounter: dict[str, Any] | None = None
        if normalized_check_action == "search":
            if not isinstance(active_state, dict) or not active_state.get("active", False):
                raise _support.CombatEngineError("an action-bound check requires active combat")
            self.require_no_blocking_pending(active_state)
            acting = _support.current_combatant(active_state)
            if acting is None or acting.get("actor_id") != actor_id:
                raise _support.CombatEngineError(
                    "an action-bound check can be made only on this actor's turn"
                )
            prepaid_search_encounter = _support.resolve_common_action(
                active_state,
                actor_id_value=actor_id,
                action="search",
                payload={
                    "kind": kind,
                    "ability": normalized_ability,
                    "dc": dc,
                },
            )
        if kind == "death_save":
            _campaign, active = self.active_encounter(campaign_id)
            self.require_no_blocking_pending(active)
            death_save_combatant = _support.current_combatant(active)
            if death_save_combatant is None or death_save_combatant.get("actor_id") != actor_id:
                raise _support.CombatEngineError(
                    "a death save is made only at the start of this actor's turn"
                )
            if not death_save_combatant.get("death_saves", False):
                raise _support.CombatEngineError(
                    "this combatant is not configured to make death saves"
                )
            if dict(death_save_combatant.get("turn_flags") or {}).get("death_save_used"):
                raise _support.CombatEngineError("this actor already made a death save this turn")
            if not _support.death_save_due(
                death_save_combatant, self.characters.get(actor_id).sheet
            ):
                raise _support.CombatEngineError(
                    "no death save is due from the start of this actor's turn"
                )
            if self.encounter_rules_edition(campaign_id, active) == "2014":
                # Local call_tool invocations can lack the request RNG wrapper.
                # Keep the roll, turn flag and receipt in the same settlement.
                stream = _support.active_random_stream()
                if stream is None:
                    stream = _support.CampaignRandomStream.from_campaign_state(
                        campaign_id,
                        campaign.state,
                        operation="combat_check",
                        idempotency_key=idempotency_key,
                        campaign_revision=campaign.revision,
                    )
                    with _support.use_random_stream(stream):
                        return self.combat_check(
                            campaign_id,
                            **payload,
                            principal_id=principal_id,
                            expected_revision=expected_revision,
                            idempotency_key=idempotency_key,
                        )
                random_state = _support.validate_random_stream_state(
                    dict(campaign.state or {}).get("random_stream")
                    or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
                )
                if (
                    stream.campaign_id != campaign_id
                    or (
                        stream.campaign_revision is not None
                        and stream.campaign_revision != campaign.revision
                    )
                    or stream.seed != random_state["seed"]
                    or stream.start_position != random_state["position"]
                ):
                    raise _support.CombatEngineError(
                        "death save requires the current campaign random snapshot"
                    )
        elif kind == "stabilize":
            assert target_id is not None
            _campaign, active = self.active_encounter(campaign_id)
            self.require_no_blocking_pending(active)
            acting = _support.current_combatant(active)
            if acting is None or acting.get("actor_id") != actor_id:
                raise _support.CombatEngineError(
                    "stabilization can be attempted only on this actor's turn"
                )
            self.require_campaign_actor(campaign_id, target_id)
            combatants = {
                str(item.get("actor_id") or ""): item for item in active.get("combatants", [])
            }
            source_combatant = combatants.get(actor_id)
            target_combatant = combatants.get(target_id)
            if source_combatant is None or target_combatant is None:
                raise _support.CombatEngineError("both actors must be present in the encounter")
            if active.get("positioning_mode") == "agent":
                facts = spatial_facts
                if (
                    not isinstance(facts, dict)
                    or set(facts) != {"decision_id", "reason", "within_5_ft"}
                    or any(
                        not isinstance(facts.get(key), str) or not facts[key].strip()
                        for key in ("decision_id", "reason")
                    )
                    or not isinstance(facts.get("within_5_ft"), bool)
                ):
                    raise _support.CombatEngineError(
                        "Agent stabilization spatial_facts require decision_id, reason, "
                        "and boolean within_5_ft"
                    )
                if not facts["within_5_ft"]:
                    raise _support.CombatEngineError(
                        "stabilization requires the target to be within 5 feet"
                    )
            else:
                source_position = source_combatant.get("position")
                target_position = target_combatant.get("position")
                if not (
                    isinstance(source_position, dict)
                    and isinstance(target_position, dict)
                    and "x" in source_position
                    and "y" in source_position
                    and "x" in target_position
                    and "y" in target_position
                ):
                    raise _support.CombatEngineError(
                        "stabilization requires recorded map positions"
                    )
                cell_ft = int(
                    dict(dict(active.get("battle_map") or {}).get("grid") or {}).get("cell_ft", 5)
                    or 5
                )
                distance = int(
                    max(
                        abs(float(source_position["x"]) - float(target_position["x"])),
                        abs(float(source_position["y"]) - float(target_position["y"])),
                    )
                    * cell_ft
                )
                if distance > 5:
                    raise _support.CombatEngineError(
                        "stabilization requires the target to be within 5 feet"
                    )
            stabilize_target = self.combat_actor_snapshot(target_id)
            _support.stabilize_sheet(stabilize_target["sheet"])
        if actor is None:
            actor = self.combat_actor_snapshot(actor_id)
        derived_skill = normalized_ability in dict(actor["derived"].get("skills") or {})
        if kind in _support.ABILITY_CHECK_KINDS and derived_skill and proficient:
            raise _support.CombatEngineError(
                "skill checks derive proficiency from the actor card; bonus is "
                "reserved for external rule or source modifiers"
            )
        next_state = dict(campaign.state or {})
        encounter = (
            prepaid_search_encounter
            if prepaid_search_encounter is not None
            else dict(next_state.get("combat") or {})
        )
        updates: list[_support.CharacterStateUpdate] = []
        if kind == "death_save":
            ruleset = self.encounter_rules_edition(campaign_id, encounter)
            updated = _support.resolve_death_save_to_sheet(
                actor["sheet"],
                advantage=advantage,
                disadvantage=disadvantage,
                ruleset=ruleset,
            )
            if ruleset == "2014":
                updated["sheet"].setdefault("combat", {})["last_death_save_elapsed_tick"] = int(
                    dict(next_state.get("game_time") or {}).get("elapsed_ticks", 0)
                )
                updated["rule_receipts"] = [
                    *list(updated.get("rule_receipts") or []),
                    *_support.core_receipts(
                        self.effective_rule_context(campaign_id, branch_id=resolved_branch_id),
                        ["dnd5e.core.mcp.death_save_turn_cadence"],
                        "death_save.turn_start",
                    ),
                ]
            result = {key: value for key, value in updated.items() if key != "sheet"}
            current = self.characters.get(actor_id)
            updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(updated["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            )
        elif kind == "stabilize":
            assert target_id is not None and stabilize_target is not None
            medicine_total = int(actor["derived"]["skills"]["medicine"])
            wisdom_modifier = int(actor["derived"]["ability_modifiers"]["wisdom"])
            stabilize_context = self.effective_rule_context(
                campaign_id,
                facts={
                    **settlement_facts,
                    "actor_id": actor_id,
                    "target_id": target_id,
                    "kind": "stabilize",
                    "ability": "wisdom",
                    "dc": 10,
                },
                branch_id=resolved_branch_id,
            )
            check = _support.resolve_actor_check(
                actor,
                kind="ability",
                ability="wisdom",
                dc=10,
                encounter=encounter,
                proficient=False,
                bonus=medicine_total - wisdom_modifier,
                advantage=advantage,
                disadvantage=disadvantage,
                ruleset=encounter.get("ruleset") if encounter else None,
                rules=stabilize_context,
            )
            encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="stabilize",
                target_id=target_id,
                payload={"method": "medicine", "dc": 10},
            )
            result = {
                **check,
                "kind": "stabilize",
                "skill": "medicine",
                "target_id": target_id,
                "stabilized": bool(check["success"]),
                "rule_receipts": [
                    *list(check.get("rule_receipts") or []),
                    *_support.core_receipts(
                        stabilize_context,
                        ["dnd5e.core.damage.zero_hp"],
                        "combat.stabilize",
                    ),
                ],
            }
            if check["success"]:
                applied = _support.stabilize_sheet(stabilize_target["sheet"])
                result["stabilization"] = {
                    key: value for key, value in applied.items() if key != "sheet"
                }
                current_target = self.characters.get(target_id)
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=_support.validate_character_sheet(applied["sheet"]),
                        notes=_support.validate_character_notes(current_target.notes),
                        expected_revision=current_target.revision,
                    )
                )
        else:
            result = _support.resolve_actor_check(
                actor,
                kind=kind,
                ability=normalized_ability,
                action=normalized_check_action,
                dc=dc,
                encounter=encounter,
                proficient=proficient,
                bonus=bonus,
                advantage=advantage,
                disadvantage=disadvantage,
                ruleset=encounter.get("ruleset") if encounter else None,
                rules=self.effective_rule_context(
                    campaign_id,
                    facts={
                        **settlement_facts,
                        "actor_id": actor_id,
                        "kind": kind,
                        "ability": ability,
                        "action": normalized_check_action,
                        "dc": dc,
                    },
                    branch_id=resolved_branch_id,
                ),
            )
            if social_charm_advantage:
                result = {
                    **result,
                    "charmed_social_advantage": True,
                }
            if target_id is not None:
                result = {**result, "target_id": target_id}
            if derived_skill:
                result = {**result, "skill": normalized_ability}
            if normalized_check_action is not None:
                if normalized_check_action != "search":
                    if not encounter:
                        raise _support.CombatEngineError(
                            "an action-bound check requires active combat"
                        )
                    self.require_no_blocking_pending(encounter)
                    acting = _support.current_combatant(encounter)
                    if acting is None or acting.get("actor_id") != actor_id:
                        raise _support.CombatEngineError(
                            "an action-bound check can be made only on this actor's turn"
                        )
                    encounter = _support.resolve_common_action(
                        encounter,
                        actor_id_value=actor_id,
                        action=normalized_check_action,
                        payload={
                            "kind": kind,
                            "ability": normalized_ability,
                            "dc": dc,
                        },
                    )
                result = {**result, "action": normalized_check_action}
        if encounter is not None and result.get("helped_by"):
            encounter = _support.consume_task_help(
                encounter,
                actor_id_value=actor_id,
                helper_id=str(result["helped_by"]),
            )
        if encounter:
            for update in updates:
                self.sync_combatant_conditions(encounter, update.character_id, update.sheet)
            if kind == "death_save":
                combatant = next(
                    item
                    for item in encounter.get("combatants", [])
                    if item.get("actor_id") == actor_id
                )
                flags = dict(combatant.get("turn_flags") or {})
                flags["death_save_used"] = True
                combatant["turn_flags"] = flags
            encounter["log"] = [
                *list(encounter.get("log") or []),
                {"type": kind, "actor_id": actor_id, "result": result},
            ][-100:]
            next_state["combat"] = encounter
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=f"combat.{kind}",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": result,
                "combat": next_state.get("combat"),
            },
            character_updates=updates,
            rule_receipts=list(result.get("rule_receipts") or []),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_source_stabilize(
        self,
        campaign_id: str,
        target_id: str,
        source_excerpt: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply an explicit module-authored stabilization without inventing an actor."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_excerpt = " ".join(str(source_excerpt or "").split()).strip()
        if not 8 <= len(normalized_excerpt) <= 4000:
            raise ValueError("source stabilization excerpt must contain 8 to 4000 characters")
        payload = {
            "target_id": target_id,
            "source_excerpt": normalized_excerpt,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-source-stabilize:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        self.require_no_blocking_pending(encounter)
        combatant = self.require_encounter_combatant(
            encounter,
            target_id,
            role="source stabilization target",
        )
        if not combatant.get("death_saves", False):
            raise _support.CombatEngineError(
                "source stabilization requires a combatant that uses death saves"
            )
        scene_id = str(encounter.get("scene_id") or "").strip()
        if not scene_id:
            raise _support.CombatEngineError("source stabilization requires an encounter scene")
        source_scene = self.modules.read_scene(campaign_id, scene_id)
        if _support._normalize_source_evidence_text(
            normalized_excerpt
        ) not in _support._normalize_source_evidence_text(source_scene.get("content")):
            raise ValueError("source stabilization excerpt is not present in the encounter scene")
        target = self.combat_actor_snapshot(target_id)
        applied = _support.stabilize_sheet(target["sheet"])
        updated_sheet = _support.validate_character_sheet(applied["sheet"])
        next_encounter = _support.deepcopy(encounter)
        self.sync_combatant_conditions(next_encounter, target_id, updated_sheet)
        _support.reconcile_readied_spells(next_encounter, target_id, updated_sheet)
        result = {key: value for key, value in applied.items() if key != "sheet"}
        result.update(
            {
                "kind": "source_stabilization",
                "target_id": target_id,
                "source_excerpt": normalized_excerpt,
                "source_ref": f"module-scene:{scene_id}",
            }
        )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {"type": "source_stabilization", "result": result},
        ][-100:]
        current = self.characters.get(target_id)
        response = self.commit_campaign_state(
            campaign,
            {**dict(campaign.state or {}), "combat": next_encounter},
            operation="combat.source.stabilize",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": result,
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=updated_sheet,
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=_support.core_receipts(
                self.effective_rule_context(
                    campaign_id,
                    branch_id=resolved_branch_id,
                    facts={
                        "target_id": target_id,
                        "source_ref": f"module-scene:{scene_id}",
                    },
                ),
                ["dnd5e.core.damage.zero_hp"],
                "combat.source.stabilize",
            ),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_resolution_plan(
        self,
        campaign_id: str,
        source_actor_id: str,
        commitment: dict[str, Any],
        *,
        principal_id: str,
        expected_revision: int | None,
        branch_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Execute one atomic segment of a paid, source-bound semantic plan."""

        runtime_services = self
        runtime_services.access.require_campaign(
            campaign_id,
            principal_id,
            roles=_support.CAMPAIGN_DM_ROLES,
        )
        runtime_services.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = runtime_services.require_current_branch(campaign_id, branch_id)
        request_payload = {
            "source_actor_id": source_actor_id,
            "commitment": _support.deepcopy(commitment),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-resolution-plan:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = runtime_services.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return runtime_services.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = runtime_services.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        source_record = runtime_services.require_campaign_actor(campaign_id, source_actor_id)
        continuations = encounter.get("semantic_state", {}).get("continuations", {})
        if continuations and str(commitment.get("application_id") or "") not in continuations:
            raise _support.CombatEngineError("resume the pending semantic application first")
        source_card_id = str(commitment.get("source_card_id") or "")
        source_card_kind = str(commitment.get("source_card_kind") or "")
        _source_card, compiled_plan = runtime_services.character_resolution_plan(
            source_record.sheet,
            source_card_id,
            source_card_kind,
        )
        save_facts_by_step = _support._semantic_plan_save_facts(_source_card, compiled_plan)
        normalized_commitment, bound_plan = runtime_services.validate_agent_resolution_commitment(
            campaign_id,
            commitment,
            encounter=encounter,
            source_actor_id=source_actor_id,
            source_card_id=source_card_id,
            source_card_kind=source_card_kind,
            compiled_plan=compiled_plan,
            allow_paid_revision=True,
        )
        _support.require_harmful_targeting_allowed(
            runtime_services.combat_actor_snapshot(source_actor_id),
            target_ids=runtime_services.semantic_plan_harmful_target_ids(bound_plan),
            known_actor_ids=runtime_services.encounter_actor_ids(encounter),
        )
        payment_entry = runtime_services.require_agent_resolution_payment(
            encounter,
            campaign_id=campaign_id,
            branch_id=resolved_branch_id,
            source_actor_id=source_actor_id,
            source_card_id=source_card_id,
            source_card_kind=source_card_kind,
            commitment=normalized_commitment,
            bound_plan=bound_plan,
        )
        if any(
            item.get("type") == "semantic_plan"
            and str(item.get("application_id") or "")
            == str(normalized_commitment.get("application_id") or "")
            for item in encounter.get("log", [])
        ):
            raise _support.CombatEngineError("this paid semantic plan has already been settled")


        from .semantic_execution import CombatPlanContext, CombatPlanRuntime

        runtime = CombatPlanRuntime(CombatPlanContext(
            encounter=encounter, runtime_services=self, campaign=campaign,
            campaign_id=campaign_id, resolved_branch_id=resolved_branch_id,
            bound_plan=bound_plan, compiled_plan=compiled_plan,
            save_facts_by_step=save_facts_by_step,
        ))
        try:
            settled = _support.execute_resolution_plan(bound_plan, runtime)
        except (
            _support.ResolutionPlanExecutionError,
            ValueError,
        ) as error:
            # The primitive executor has already rolled back. Preserve a source
            # ruling so the outer boundary rewinds RNG and returns a retryable
            # pause instead of erasing it into a generic tool failure.
            if isinstance(error, _support.NeedsRulingError):
                raise
            if isinstance(error.__cause__, _support.NeedsRulingError):
                raise error.__cause__
            raise _support.CombatEngineError(str(error)) from error
        next_encounter = runtime.encounter
        application_id = str(normalized_commitment.get("application_id") or "")
        completed = settled.status == "committed"
        if completed:
            next_encounter.get("semantic_state", {}).get("continuations", {}).pop(
                application_id, None,
            )
        if completed and source_card_kind == "item":
            next_encounter["pending"] = [
                item
                for item in next_encounter.get("pending", [])
                if str(item.get("id") or "") != application_id
            ]
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "semantic_plan" if completed else "semantic_plan_paused",
                "application_id": application_id,
                "actor_id": source_actor_id,
                "source_card_id": source_card_id,
                "source_card_kind": source_card_kind,
                "plan_id": compiled_plan.id,
                "plan_fingerprint": compiled_plan.fingerprint,
                "bound_plan_fingerprint": bound_plan.fingerprint,
                "payment": _support.deepcopy(payment_entry),
                "results": _support.deepcopy(settled.results),
                "round": int(next_encounter.get("round", 1) or 1),
                "turn_index": int(next_encounter.get("turn_index", 0) or 0),
            },
        ][-100:]
        character_updates = [
            _support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=_support.validate_character_sheet(sheet),
                notes=_support.validate_character_notes(runtime.record(actor_id).notes),
                expected_revision=runtime.record(actor_id).revision,
            )
            for actor_id, sheet in runtime.sheets.items()
        ]
        rules = runtime_services.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": source_actor_id,
                "source_card_id": source_card_id,
                "semantic_plan_id": compiled_plan.id,
            },
            branch_id=resolved_branch_id,
        )
        receipt = {
            **_support.deepcopy(settled.receipt),
            "ruleset_fingerprint": rules.fingerprint,
            "mechanic_id": compiled_plan.id,
            "event": f"combat.semantic_plan.{compiled_plan.trigger}",
        }
        response = runtime_services.commit_campaign_state(
            campaign,
            {**dict(campaign.state or {}), "combat": next_encounter},
            operation="combat.semantic_plan.execute",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=str(idempotency_key),
            scope=scope,
            payload=request_payload,
            response_fields={
                "status": settled.status,
                "result": {
                    "plan_id": compiled_plan.id,
                    "application_id": application_id,
                    "waiting_choice_ids": list(runtime.encounter.get("semantic_state", {}).get(
                        "continuations", {}
                    ).get(application_id, {}).get("waiting_ids", [])),
                    "plan_fingerprint": compiled_plan.fingerprint,
                    "bound_plan_fingerprint": bound_plan.fingerprint,
                    "results": _support.deepcopy(settled.results),
                    "receipt": _support.deepcopy(settled.receipt),
                },
                "combat": next_encounter,
            },
            character_updates=character_updates,
            actor_knowledge_transfers=runtime.knowledge_transfers,
            rule_receipts=[receipt],
        )
        return runtime_services.combat_response(campaign_id, principal_id, response)

    def combat_save_damage(
        self,
        campaign_id: str,
        target_ids: list[str],
        *,
        source_actor_id: str,
        source_card_id: str,
        source_card_kind: str,
        save_ability: str,
        save_dc: int,
        damage_expression: str,
        damage_type: str,
        half_on_success: bool,
        save_advantage: bool,
        save_disadvantage: bool,
        mechanic_source_excerpt: str,
        agent_ruling: dict[str, Any],
        spatial_facts: dict[str, Any] | None,
        principal_id: str,
        expected_revision: int | None,
        branch_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Resolve Agent-selected save damage through one generic Core mutation."""

        self.access.require_campaign(
            campaign_id,
            principal_id,
            roles=_support.CAMPAIGN_DM_ROLES,
        )
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_target_ids = [str(target_id or "").strip() for target_id in target_ids]
        if (
            not normalized_target_ids
            or any(not target_id for target_id in normalized_target_ids)
            or len(normalized_target_ids) != len(set(normalized_target_ids))
        ):
            raise _support.CombatEngineError("save damage requires one or more unique target_ids")
        request_payload = {
            "target_ids": normalized_target_ids,
            "source_actor_id": source_actor_id,
            "source_card_id": source_card_id,
            "source_card_kind": source_card_kind,
            "save_ability": save_ability,
            "save_dc": save_dc,
            "damage_expression": damage_expression,
            "damage_type": damage_type,
            "half_on_success": half_on_success,
            "save_advantage": save_advantage,
            "save_disadvantage": save_disadvantage,
            "mechanic_source_excerpt": mechanic_source_excerpt,
            "agent_ruling": _support.deepcopy(agent_ruling),
            "spatial_facts": _support.deepcopy(spatial_facts),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-save-damage:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        self.require_campaign_actor(campaign_id, source_actor_id)
        for target_id in normalized_target_ids:
            self.require_campaign_actor(campaign_id, target_id)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        # Direct local tool calls do not enter the request-scoped RNG wrapper.
        # Establish the same snapshot here, before any save or damage draw,
        # and let the existing atomic mutation persist its receipt and position.
        stream = _support.active_random_stream()
        if stream is None:
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign.state,
                operation="combat_hp_change",
                idempotency_key=idempotency_key,
                campaign_revision=campaign.revision,
            )
            with _support.use_random_stream(stream):
                return self.combat_save_damage(
                    campaign_id,
                    **request_payload,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                )
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
        )
        if (
            stream.campaign_id != campaign_id
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
        ):
            raise _support.CombatEngineError(
                "save damage requires the current campaign random snapshot"
            )
        self.require_no_blocking_pending(encounter)
        positioning_mode = str(encounter.get("positioning_mode") or "grid")
        normalized_spatial_facts: dict[str, Any] | None = None
        if positioning_mode == "agent":
            if not isinstance(spatial_facts, dict):
                raise _support.NeedsRulingError(
                    "agent-positioned area effects require an Agent spatial decision",
                    missing=("area.spatial_facts",),
                    ruling_kind="agent_dm_adjudication",
                )
            allowed_spatial_fields = {
                "decision_id",
                "reason",
                "affected_target_ids",
                "excluded_actor_ids",
                "line_of_effect_clear",
                "friendly_fire_included",
            }
            required_spatial_fields = {
                "decision_id",
                "reason",
                "affected_target_ids",
                "line_of_effect_clear",
            }
            if set(spatial_facts) - allowed_spatial_fields or required_spatial_fields - set(
                spatial_facts
            ):
                raise _support.CombatEngineError(
                    "Agent area spatial facts require decision_id, reason, affected_target_ids, "
                    "and line_of_effect_clear"
                )
            decision_id = str(spatial_facts.get("decision_id") or "").strip()
            reason = " ".join(str(spatial_facts.get("reason") or "").split())
            affected_ids = spatial_facts.get("affected_target_ids")
            excluded_ids = spatial_facts.get("excluded_actor_ids", [])
            participant_ids = {
                str(item.get("actor_id") or "") for item in encounter.get("combatants", [])
            }
            for field, actor_ids in (
                ("affected_target_ids", affected_ids),
                ("excluded_actor_ids", excluded_ids),
            ):
                if (
                    not isinstance(actor_ids, list)
                    or any(not isinstance(item, str) for item in actor_ids)
                    or len(set(actor_ids)) != len(actor_ids)
                    or set(actor_ids) - participant_ids
                ):
                    raise _support.CombatEngineError(
                        f"Agent area spatial fact {field} must contain unique current combatant IDs"
                    )
            if (
                not decision_id
                or not reason
                or affected_ids != normalized_target_ids
                or set(affected_ids) & set(excluded_ids)
                or not isinstance(spatial_facts.get("line_of_effect_clear"), bool)
                or (
                    "friendly_fire_included" in spatial_facts
                    and not isinstance(spatial_facts["friendly_fire_included"], bool)
                )
            ):
                raise _support.CombatEngineError(
                    "Agent area spatial facts must exactly justify the declared targets"
                )
            if spatial_facts["line_of_effect_clear"] is not True:
                raise _support.CombatEngineError(
                    "the Agent ruled that the area lacks line of effect"
                )
            normalized_spatial_facts = {
                **dict(spatial_facts),
                "decision_id": decision_id,
                "reason": reason,
                "affected_target_ids": list(affected_ids),
                "excluded_actor_ids": list(excluded_ids),
                "friendly_fire_included": bool(spatial_facts.get("friendly_fire_included", False)),
            }
        elif spatial_facts is not None:
            raise _support.CombatEngineError(
                "grid area effects derive spatial targets from encounter coordinates"
            )
        self.require_encounter_combatant(
            encounter,
            source_actor_id,
            role="save-damage source",
        )
        target_combatants = {
            target_id: self.require_encounter_combatant(
                encounter,
                target_id,
                role="save-damage target",
            )
            for target_id in normalized_target_ids
        }
        normalized_ruling = self.validate_current_scene_agent_ruling(
            campaign_id,
            agent_ruling,
            encounter=encounter,
            field="save damage",
            allowed_ruling_kinds={"agent_dm_adjudication"},
        )
        normalized_card_kind = str(source_card_kind or "").strip().casefold().replace("-", "_")
        normalized_card_id = str(source_card_id or "").strip()
        normalized_mechanic_excerpt = " ".join(str(mechanic_source_excerpt or "").split())
        if (
            source_actor_id in normalized_target_ids
            or normalized_card_kind != "scene_procedure"
            or not normalized_card_id
            or not normalized_mechanic_excerpt
        ):
            raise _support.CombatEngineError(
                "save damage requires distinct source/target actors and one "
                "reviewed scene procedure; actor-card mechanics use content_solution"
            )
        normalized_expression = "".join(str(damage_expression or "").split()).casefold()
        normalized_damage_type = str(damage_type or "").strip().casefold()
        normalized_save_ability = str(save_ability or "").strip().casefold()
        commitment = self.agent_save_damage_commitment(
            application_id=str(normalized_ruling["application_id"]),
            source_card_id=normalized_card_id,
            source_card_kind=normalized_card_kind,
            target_ids=normalized_target_ids,
            save_ability=normalized_save_ability,
            save_dc=save_dc,
            save_advantage=save_advantage,
            save_disadvantage=save_disadvantage,
            damage_expression=normalized_expression,
            damage_type=normalized_damage_type,
            half_on_success=half_on_success,
            mechanic_source_excerpt=normalized_mechanic_excerpt,
            agent_ruling=normalized_ruling,
        )
        self.validate_scene_save_damage_source(campaign_id, commitment, encounter=encounter)
        payment_entry = self.require_agent_save_damage_payment(
            encounter,
            source_actor_id=source_actor_id,
            source_card_id=normalized_card_id,
            source_card_kind=normalized_card_kind,
            commitment=commitment,
        )
        if any(
            item.get("type") == "agent_save_damage"
            and str(item.get("application_id") or "") == str(normalized_ruling["application_id"])
            for item in encounter.get("log", [])
            if isinstance(item, dict)
        ):
            raise _support.CombatEngineError(
                "this Agent save-damage application has already been settled"
            )
        target_actors = [
            self.combat_actor_snapshot(target_id) for target_id in normalized_target_ids
        ]
        rule_context = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_ids": normalized_target_ids,
                "source_actor_id": source_actor_id,
                "source_card_id": normalized_card_id,
                "kind": "save_damage",
                "ability": normalized_save_ability,
                "dc": save_dc,
                **({"save_source_kind": "spell"} if normalized_card_kind == "spell" else {}),
            },
        )
        settled = _support.resolve_save_damage_to_sheets(
            target_actors,
            save_ability=normalized_save_ability,
            save_dc=save_dc,
            damage_expression=normalized_expression,
            damage_type=normalized_damage_type,
            half_on_success=half_on_success,
            advantage=save_advantage,
            disadvantage=save_disadvantage,
            source=f"agent-ruling:{normalized_ruling['application_id']}",
            encounter=encounter,
            death_saves_by_actor_id={
                target_id: self.combatant_zero_hp_buffered(combatant)
                for target_id, combatant in target_combatants.items()
            },
            ruleset=self.encounter_rules_edition(campaign_id, encounter),
            rules=rule_context,
        )
        result = {
            **dict(settled["result"]),
            "source_actor_id": source_actor_id,
            "source_card_id": normalized_card_id,
            "source_card_kind": normalized_card_kind,
            "agent_ruling": normalized_ruling,
            "action_payment": payment_entry,
            "spatial_ruling": normalized_spatial_facts,
        }
        next_encounter = _support.deepcopy(encounter)
        updated_sheets = {
            target_id: _support.validate_character_sheet(sheet)
            for target_id, sheet in dict(settled["sheets"]).items()
        }
        for target_result in result["targets"]:
            target_id = str(target_result["target_id"])
            updated_sheet = updated_sheets[target_id]
            damage = dict(target_result.get("damage") or {})
            if damage:
                self.add_concentration_window(
                    next_encounter,
                    target_id,
                    damage.get("concentration"),
                    next_revision=campaign.revision + 1,
                )
            self.sync_combatant_conditions(
                next_encounter,
                target_id,
                updated_sheet,
            )
            _support.reconcile_readied_spells(
                next_encounter,
                target_id,
                updated_sheet,
            )
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "agent_save_damage",
                "application_id": str(normalized_ruling["application_id"]),
                "source_actor_id": source_actor_id,
                "target_ids": normalized_target_ids,
                "result": result,
                "spatial_ruling": normalized_spatial_facts,
            },
        ][-100:]
        current_records = {
            target_id: self.characters.get(target_id) for target_id in normalized_target_ids
        }
        response = self.commit_campaign_state(
            campaign,
            {**dict(campaign.state or {}), "combat": next_encounter},
            operation="combat.save_damage",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=request_payload,
            response_fields={
                "status": "committed",
                "result": result,
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=updated_sheets[target_id],
                    notes=_support.validate_character_notes(current_records[target_id].notes),
                    expected_revision=current_records[target_id].revision,
                )
                for target_id in normalized_target_ids
            ],
            rule_receipts=[
                *_support.core_receipts(
                    rule_context,
                    ["dnd5e.core.mcp.save_damage_atomicity"],
                    "combat.save_damage",
                ),
                *[
                    receipt
                    for target_result in result["targets"]
                    for receipt in dict(target_result.get("save") or {}).get("rule_receipts", [])
                ],
                *[
                    receipt
                    for target_result in result["targets"]
                    for receipt in target_result.get("rule_receipts", [])
                ],
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_apply_damage(
        self,
        campaign_id: str,
        target_id: str,
        parts: list[dict[str, Any]],
        critical: bool = False,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        knock_out: bool = False,
        melee: bool = False,
    ) -> dict[str, Any]:
        """Apply adjudicator-approved damage; automatic trait and HP settlement is deterministic."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        self.require_campaign_actor(campaign_id, target_id)
        payload = {
            "target_id": target_id,
            "parts": parts,
            "critical": critical,
            "knock_out": knock_out,
            "melee": melee,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-damage:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        target = self.combat_actor_snapshot(target_id)
        existing_encounter = dict(campaign.state or {}).get("combat")
        target_uses_death_saves = target.get("character_type") == "pc"
        ruleset = self.campaign_rules_edition(campaign_id)
        if isinstance(existing_encounter, dict) and existing_encounter.get("active", False):
            self.require_no_blocking_pending(existing_encounter)
            ruleset = self.encounter_rules_edition(campaign_id, existing_encounter)
            target_combatant = self.require_encounter_combatant(
                existing_encounter, target_id, role="damage target"
            )
            target_uses_death_saves = self.combatant_zero_hp_buffered(target_combatant)
        applied = _support.apply_damage_parts_to_sheet(
            target["sheet"],
            parts,
            source=principal_id,
            critical=critical,
            ruleset=ruleset,
            death_saves=target_uses_death_saves,
            knock_out=knock_out,
            melee=melee,
        )
        applied_result = {key: value for key, value in applied.items() if key != "sheet"}
        damage_receipts = _support.core_receipts(
            self.effective_rule_context(campaign_id, branch_id=resolved_branch_id),
            ["dnd5e.core.damage.zero_hp"] if int(applied["after_hp"]) == 0 else [],
            "damage.apply",
        )
        encounter = existing_encounter
        next_state = dict(campaign.state or {})
        if encounter:
            self.sync_combatant_conditions(encounter, target_id, applied["sheet"])
            _support.reconcile_readied_spells(encounter, target_id, applied["sheet"])
            self.add_concentration_window(
                encounter,
                target_id,
                applied.get("concentration"),
                next_revision=campaign.revision + 1,
            )
            encounter["log"] = [
                *list(encounter.get("log") or []),
                {"type": "damage", "target_id": target_id, "result": applied_result},
            ][-100:]
            next_state["combat"] = encounter
        current = self.characters.get(target_id)
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.damage.apply",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": applied_result,
                "combat": next_state.get("combat"),
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=damage_receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_apply_fall(
        self,
        campaign_id: str,
        target_id: str,
        distance_ft: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve source-derived 2014 falling damage atomically."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        self.require_campaign_actor(campaign_id, target_id)
        payload = {
            "target_id": target_id,
            "distance_ft": distance_ft,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-fall:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)

        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        # Direct in-process callers do not pass through the request-scoped
        # random-stream wrapper. Open the same campaign stream here so tests
        # and local hosts receive identical deterministic receipts.
        if _support.active_random_stream() is None:
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign.state,
                operation="combat.fall.apply",
                idempotency_key=str(idempotency_key or ""),
                campaign_revision=campaign.revision,
            )
            with _support.use_random_stream(stream):
                return self.combat_apply_fall(
                    campaign_id,
                    target_id,
                    distance_ft,
                    principal_id,
                    expected_revision,
                    resolved_branch_id,
                    idempotency_key,
                )

        target = self.combat_actor_snapshot(target_id)
        existing_encounter = dict(campaign.state or {}).get("combat")
        target_uses_death_saves = target.get("character_type") == "pc"
        ruleset = self.campaign_rules_edition(campaign_id)
        if isinstance(existing_encounter, dict) and existing_encounter.get("active", False):
            self.require_no_blocking_pending(existing_encounter)
            ruleset = self.encounter_rules_edition(campaign_id, existing_encounter)
            target_combatant = self.require_encounter_combatant(
                existing_encounter, target_id, role="fall target"
            )
            target_uses_death_saves = self.combatant_zero_hp_buffered(target_combatant)
        applied = _support.resolve_fall_to_sheet(
            target["sheet"],
            distance_ft=distance_ft,
            source=principal_id,
            ruleset=ruleset,
            death_saves=target_uses_death_saves,
        )
        applied_result = {key: value for key, value in applied.items() if key != "sheet"}
        next_state = dict(campaign.state or {})
        encounter = existing_encounter
        if isinstance(encounter, dict) and encounter.get("active", False):
            self.sync_combatant_conditions(encounter, target_id, applied["sheet"])
            _support.reconcile_readied_spells(encounter, target_id, applied["sheet"])
            self.add_concentration_window(
                encounter,
                target_id,
                dict(applied.get("damage") or {}).get("concentration"),
                next_revision=campaign.revision + 1,
            )
            encounter["log"] = [
                *list(encounter.get("log") or []),
                {"type": "fall", "target_id": target_id, "result": applied_result},
            ][-100:]
            next_state["combat"] = encounter
        current = self.characters.get(target_id)
        boundary_ids = ["dnd5e.core.movement.falling"]
        if int(dict(applied.get("damage") or {}).get("after_hp", 1) or 0) == 0:
            boundary_ids.append("dnd5e.core.damage.zero_hp")
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.fall.apply",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": applied_result,
                "combat": next_state.get("combat"),
                "rule_receipts": _support.core_receipts(
                    self.effective_rule_context(campaign_id, branch_id=resolved_branch_id),
                    boundary_ids,
                    "movement.falling",
                ),
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=_support.core_receipts(
                self.effective_rule_context(campaign_id, branch_id=resolved_branch_id),
                boundary_ids,
                "movement.falling",
            ),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_heal(
        self,
        campaign_id: str,
        target_id: str,
        amount: int,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        source_actor_id: str | None = None,
        spell_id: str | None = None,
        spell_level: int | None = None,
    ) -> dict[str, Any]:
        """Apply source-aware healing with feature modifiers and max-HP clamping."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        self.require_campaign_actor(campaign_id, target_id)
        if source_actor_id:
            self.require_campaign_actor(campaign_id, source_actor_id)
        if int(amount) <= 0:
            raise _support.CombatEngineError("healing amount must be positive")
        payload = {
            "target_id": target_id,
            "amount": amount,
            "branch_id": resolved_branch_id,
            "source_actor_id": source_actor_id,
            "spell_id": spell_id,
            "spell_level": spell_level,
        }
        scope = f"combat-heal:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        target = self.combat_actor_snapshot(target_id)
        active = dict(campaign.state or {}).get("combat")
        if isinstance(active, dict) and active.get("active", False):
            self.require_no_blocking_pending(active)
            self.require_encounter_combatant(active, target_id, role="healing target")
            if source_actor_id is not None:
                self.require_encounter_combatant(active, source_actor_id, role="healing source")
        self.require_healing_not_prevented(active, target_id=target_id)
        source = self.combat_actor_snapshot(source_actor_id) if source_actor_id else None
        applied = _support.apply_healing_to_sheet(
            target["sheet"],
            amount=amount,
            source_sheet=source["sheet"] if source else None,
            spell_id=spell_id,
            spell_level=spell_level,
        )
        if applied.get("source") is not None:
            applied["source"]["actor_id"] = source_actor_id
        current = self.characters.get(target_id)
        next_state: dict[str, Any] | None = None
        encounter = dict(campaign.state or {}).get("combat")
        if isinstance(encounter, dict) and encounter.get("active", False):
            self.sync_combatant_conditions(encounter, target_id, applied["sheet"])
            next_state = {**dict(campaign.state or {}), "combat": encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.heal.apply",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": {key: value for key, value in applied.items() if key != "sheet"},
                "combat": next_state.get("combat") if next_state else None,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_choice_open(
        self,
        campaign_id: str,
        actor_id: str,
        event: str,
        candidates: list[dict[str, Any]] | None = None,
        kind: str = "reaction",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Open a reaction/ruling window; the engine never guesses a narrative choice."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        self.require_campaign_actor(campaign_id, actor_id)
        payload = {
            "actor_id": actor_id,
            "event": event,
            "candidates": candidates or [],
            "kind": kind,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-choice-open:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        _, encounter = self.active_encounter(campaign_id)
        next_encounter = _support.add_choice_window(
            encounter,
            kind=kind,
            actor_id_value=actor_id,
            event=event,
            candidates=candidates or [],
        )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        window = next_encounter["pending"][-1]
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.choice.open",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "pending",
                "choice": window,
            },
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_choice_resolve(
        self,
        campaign_id: str,
        actor_id: str,
        choice_id: str,
        selection: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Commit one actor/DM choice and leave its downstream effect explicit."""
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "choice_id": choice_id,
            "selection": selection,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-choice-resolve:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        _, encounter = self.active_encounter(campaign_id)
        pending_choice = next(
            (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
            None,
        )
        if pending_choice and pending_choice.get("trigger") == "readied_spell":
            raise _support.CombatEngineError(
                "readied-spell windows must use combat_readied_spell_resolve"
            )
        if pending_choice and pending_choice.get("trigger") == "attack_hit_defense":
            raise _support.CombatEngineError(
                "attack-defense windows must use combat_choice(action=resolve_defense)"
            )
        if pending_choice and pending_choice.get("kind") == "concentration":
            raise _support.CombatEngineError("concentration windows require a concentration check")
        next_encounter = _support.resolve_choice_window(
            encounter,
            choice_id=choice_id,
            actor_id_value=actor_id,
            selection=selection,
        )
        selection_id = str(selection.get("id") or "").lower()
        if (
            pending_choice
            and pending_choice.get("kind") == "reaction"
            and pending_choice.get("trigger") == "opportunity_attack"
            and selection_id not in {"decline", "skip", "pass"}
        ):
            raise _support.CombatEngineError(
                "opportunity attacks must be resolved with combat_reaction_attack"
            )
        if (
            pending_choice
            and pending_choice.get("kind") == "reaction"
            and selection_id not in {"decline", "skip", "pass"}
        ):
            combatant = next(
                item
                for item in next_encounter.get("combatants", [])
                if item.get("actor_id") == actor_id
            )
            budget = dict(combatant.get("turn_budget") or {})
            if int(budget.get("reaction", 0) or 0) <= 0:
                raise _support.CombatEngineError("actor has no reaction remaining")
            budget["reaction"] = int(budget["reaction"]) - 1
            combatant["turn_budget"] = budget
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.choice.resolve",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "combat": next_encounter,
            },
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_map_patch(
        self,
        campaign_id: str,
        patches: list[dict[str, Any]],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record Agent-as-DM-confirmed changes from a temporary battle map."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized: list[dict[str, Any]] = []
        for patch in patches:
            if not isinstance(patch, dict) or not isinstance(patch.get("key"), str):
                raise ValueError("each map patch needs a string key")
            normalized.append({"key": patch["key"], "value": _support.deepcopy(patch.get("value"))})
        payload = {"patches": normalized, "branch_id": resolved_branch_id}
        scope = f"combat-map-patch:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        battle_map = dict(encounter.get("battle_map") or {})
        if not battle_map:
            raise _support.CombatEngineError("active encounter has no temporary battle map")
        next_encounter = _support.deepcopy(encounter)
        next_map = _support.patch_battle_map(dict(next_encounter["battle_map"]), normalized)
        next_encounter["battle_map"] = next_map
        participant_ids = {
            str(item.get("actor_id")) for item in next_encounter.get("combatants", [])
        }
        seen_visibility_actors: set[str] = set()
        seen_departure_actors: set[str] = set()
        for patch in normalized:
            if patch["key"] == "combatant_departure":
                departure = patch.get("value")
                if not isinstance(departure, dict) or set(departure) - {
                    "actor_id",
                    "reason",
                    "destination_location_key",
                }:
                    raise ValueError(
                        "combatant_departure requires actor_id, reason, and optional "
                        "destination_location_key"
                    )
                target_id = str(departure.get("actor_id") or "")
                if target_id not in participant_ids or target_id in seen_departure_actors:
                    raise ValueError(
                        "combatant_departure actor_id must be a unique encounter participant"
                    )
                seen_departure_actors.add(target_id)
                reason = str(departure.get("reason") or "").strip()
                if not reason:
                    raise ValueError(
                        "combatant_departure requires a source or Agent-as-DM ruling reason"
                    )
                destination = str(departure.get("destination_location_key") or "").strip()
                combatant = next(
                    item
                    for item in next_encounter["combatants"]
                    if str(item.get("actor_id")) == target_id
                )
                combatant["departed"] = {
                    "reason": reason,
                    "destination_location_key": destination,
                }
                combatant["hidden"] = True
                continue
            if patch["key"] != "combatant_visibility":
                continue
            visibility = patch.get("value")
            if not isinstance(visibility, dict) or set(visibility) - {
                "actor_id",
                "hidden",
                "visible_to_actor_ids",
                "reason",
            }:
                raise ValueError(
                    "combatant_visibility requires actor_id, reason, and optional "
                    "hidden/visible_to_actor_ids"
                )
            target_id = str(visibility.get("actor_id") or "")
            if target_id not in participant_ids or target_id in seen_visibility_actors:
                raise ValueError(
                    "combatant_visibility actor_id must be a unique encounter participant"
                )
            seen_visibility_actors.add(target_id)
            if not str(visibility.get("reason") or "").strip():
                raise ValueError("combatant_visibility requires an Agent-as-DM ruling reason")
            if "hidden" not in visibility and "visible_to_actor_ids" not in visibility:
                raise ValueError("combatant_visibility must change hidden or visible_to_actor_ids")
            if "hidden" in visibility and not isinstance(visibility["hidden"], bool):
                raise ValueError("combatant_visibility hidden must be boolean")
            visible_to = visibility.get("visible_to_actor_ids")
            if "visible_to_actor_ids" in visibility and visible_to is not None:
                if (
                    not isinstance(visible_to, list)
                    or len({str(item) for item in visible_to}) != len(visible_to)
                    or any(str(item) not in participant_ids for item in visible_to)
                ):
                    raise ValueError(
                        "combatant_visibility visible_to_actor_ids must be unique participants"
                    )
            combatant = next(
                item
                for item in next_encounter["combatants"]
                if str(item.get("actor_id")) == target_id
            )
            if "hidden" in visibility:
                combatant["hidden"] = visibility["hidden"]
            if "visible_to_actor_ids" in visibility:
                combatant["visible_to_actor_ids"] = (
                    None if visible_to is None else [str(item) for item in visible_to]
                )
        state = dict(campaign.state or {})
        state["combat"] = next_encounter
        runtime = dict(state.get("scene_runtime") or {})
        scene_id = str(dict(next_map.get("source") or {}).get("scene_id") or "")
        if scene_id:
            scene_state = dict(runtime.get(scene_id) or {})
            for patch in normalized:
                scene_state[patch["key"]] = patch["value"]
            runtime[scene_id] = scene_state
        state["scene_runtime"] = runtime
        response = self.commit_campaign_state(
            campaign,
            state,
            operation="combat.map.patch",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "battle_map": next_map,
                "world_patches": normalized,
                "combat": next_encounter,
            },
            include_revisions=False,
        )
        return response

    def combat_end(
        self,
        campaign_id: str,
        outcome: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Close an encounter atomically while preserving its final audit state.

        Optional outcome is {status, summary}; status is defeat, interrupted,
        surrender, truce, victory, or withdrawal. Requires campaign revision,
        current branch_id and idempotency_key. Settle pending choices first.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        campaign = self.campaigns.get(campaign_id)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        outcome_value = dict(outcome or {})
        if outcome_value:
            allowed = {"status", "summary"}
            unknown = set(outcome_value) - allowed
            if unknown:
                raise ValueError(f"unsupported combat outcome fields: {sorted(unknown)}")
            status = str(outcome_value.get("status") or "").strip().lower()
            if status not in _support.COMBAT_OUTCOME_STATUSES:
                raise ValueError("combat outcome status is invalid")
            summary = str(outcome_value.get("summary") or "").strip()
            if not summary or len(summary) > 2000:
                raise ValueError("combat outcome summary must contain 1 to 2000 characters")
            outcome_value = {"status": status, "summary": summary}
        payload = {"branch_id": resolved_branch_id, "outcome": outcome_value}
        scope = f"combat-end:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        retained_combat = dict(campaign.state or {}).get("combat")
        if not isinstance(retained_combat, dict) or not retained_combat:
            raise _support.CombatEngineError("campaign has no retained combat")
        combat = _support.deepcopy(retained_combat)
        self.encounter_rules_edition(campaign_id, combat)
        if not combat.get("active", False):
            raise _support.CombatEngineError("combat is already closed")
        self.require_no_blocking_pending(combat)
        ending_actor_ids = list(
            dict.fromkeys(
                str(item.get("actor_id") or "")
                for item in [
                    *list(combat.get("combatants") or []),
                    *list(combat.get("reinforcements") or []),
                ]
                if isinstance(item, dict) and str(item.get("actor_id") or "")
            )
        )
        ending_actors = {
            actor_id_value: self.characters.get(actor_id_value)
            for actor_id_value in ending_actor_ids
        }
        post_combat_recovery: list[dict[str, Any]] = []
        for combatant in combat.get("combatants", []):
            if not combatant.get("death_saves", False):
                continue
            actor = ending_actors[str(combatant["actor_id"])]
            actor_combat = dict(actor.sheet.get("combat") or {})
            hp = int(dict(actor_combat.get("hp") or {}).get("value", 0) or 0)
            conditions = _support.condition_ids(actor.sheet.get("conditions", []))
            if hp == 0 and not conditions & _support.DEATH_SAVE_SETTLED_CONDITIONS:
                post_combat_recovery.append(
                    {
                        "actor_id": actor.id,
                        "status": "dying",
                        "unresolved_at_end": True,
                        "death_saves": _support.deepcopy(
                            dict(actor_combat.get("death_saves") or {})
                        ),
                        "resolution_actions": ["heal", "stabilize", "death_save"],
                    }
                )
        combat["active"] = False
        combat["post_combat_recovery"] = post_combat_recovery
        if outcome_value:
            combat["outcome"] = outcome_value
        ending_readied = list(combat.get("readied", []))
        combat["readied"] = []
        updated_state = dict(campaign.state or {})
        updated_state["combat"] = combat
        updated_state["game_phase"] = _support.PROFILE_PLAY
        updated_state = _support._with_combat_mutation_lock(updated_state, active=False)
        character_updates: list[_support.CharacterStateUpdate] = []
        expired_effects: set[str] = set()
        for actor_id_value in ending_actor_ids:
            actor = ending_actors[actor_id_value]
            sheet = _support.deepcopy(actor.sheet)
            for source_condition in combat.get("source_conditions", []):
                if str(source_condition.get("actor_id") or "") != actor.id or not (
                    source_condition.get("active", True)
                    and source_condition.get("added_by_encounter", False)
                ):
                    continue
                condition = str(source_condition.get("condition") or "").casefold()
                _support.apply_condition_change(sheet, condition_id=condition, add=False)
            holding_ids = {
                str(item.get("holding_effect_id"))
                for item in ending_readied
                if item.get("kind") == "spell" and item.get("actor_id") == actor.id
            }
            for effect in sheet.get("effects", []):
                if str(effect.get("id")) in holding_ids:
                    effect["active"] = False
            advanced = _support.expire_combat_bound_effects(sheet)
            expired_effects.update(advanced["expired"])
            sheet = advanced["sheet"]
            normalized_sheet = _support.validate_character_sheet(sheet)
            self.sync_combatant_conditions(combat, actor_id_value, normalized_sheet)
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=actor.id,
                    sheet=normalized_sheet,
                    notes=_support.validate_character_notes(actor.notes),
                    expected_revision=actor.revision,
                )
            )
        updated_state["combat"] = combat
        response = self.commit_campaign_state(
            campaign,
            updated_state,
            operation="combat.end",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "ended": True,
                "combat": combat,
                "outcome": outcome_value or None,
                "tool_profile": _support.PROFILE_PLAY,
                "post_combat_recovery": post_combat_recovery,
                "effects_expired": sorted(expired_effects),
                "readied_spells_expired": sorted(
                    str(item.get("id")) for item in ending_readied if item.get("kind") == "spell"
                ),
            },
            character_updates=character_updates,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def chase(
        self,
        campaign_id: str,
        action: Literal["start", "query", "take_turn", "end"],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run the source-reviewed 2014 chase procedure only during Play."""
        self.require_facade_phase(campaign_id, f"chase({action})", _support.PROFILE_PLAY)
        if action == "query":
            data = self.facade_payload(payload)
            if data:
                raise ValueError("chase(query).payload must be empty")
            return self.facade_result(action, self.chase_query(campaign_id, principal_id))
        if action == "start":
            data = self.facade_payload(payload)
            result = self.chase_start(
                campaign_id,
                data["participant_ids"],
                data["quarry_ids"],
                data["initial_distance_ft"],
                data["scene_id"],
                data["source_ref"],
                data["source_excerpt"],
                data.get("name", "Chase"),
                data.get("participant_config"),
                data.get("close_transition"),
                principal_id,
                branch_id,
                expected_revision,
                idempotency_key,
            )
        elif action == "take_turn":
            data = self.facade_payload(payload)
            if "complication_choice" not in data:
                raise ValueError("payload.complication_choice is required")
            turn_action = data.get("turn_action", "dash")
            if turn_action not in {"dash", "move", "drop_out"}:
                raise ValueError("payload.turn_action must be dash, move, or drop_out")
            result = self.chase_take_turn(
                campaign_id,
                data["actor_id"],
                turn_action,
                data.get("complication_choice", ""),
                self.facade_bool(data, "stand_from_prone", default=True),
                data.get("quarry_visibility"),
                principal_id,
                branch_id,
                expected_revision,
                data.get("expected_actor_revision"),
                idempotency_key,
            )
        else:
            data = self.facade_payload(payload)
            status = data["status"]
            if status not in _support.CHASE_MANUAL_OUTCOME_STATUSES:
                raise ValueError(
                    "payload.status must be caught, destination_reached, "
                    "quarry_escaped, or pursuers_abandoned"
                )
            result = self.chase_end(
                campaign_id,
                status,
                data["summary"],
                data["source_ref"],
                data["source_excerpt"],
                principal_id,
                branch_id,
                expected_revision,
                idempotency_key,
            )
        return self.facade_result(action, result)

    def combat_query(
        self,
        campaign_id: str,
        view: Literal[
            "status",
            "available_actions",
            "reactions",
            "render",
            "transaction_history",
            "transaction_receipt",
        ] = "status",
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read combat state or DM-only transaction receipts.

        available_actions and reactions require top-level actor_id, not payload.
        status needs only campaign_id. transaction_receipt requires
        payload={idempotency_key, branch_id?}; render accepts audience_projection.
        """
        data = self.facade_payload(payload)
        if view == "status":
            result = self.combat_status(campaign_id, principal_id)
        elif view == "available_actions":
            if not actor_id:
                raise ValueError("top-level actor_id is required for available_actions")
            result = self.combat_available_actions(
                campaign_id, actor_id, principal_id
            )
        elif view == "reactions":
            if not actor_id:
                raise ValueError("top-level actor_id is required for reactions")
            result = self.combat_reactions(
                campaign_id, actor_id, principal_id
            )
        elif view == "render":
            audience_projection = str(data.get("audience_projection") or "caller")
            if audience_projection not in {"caller", "party_public"}:
                raise ValueError("audience_projection must be caller or party_public")
            return self.facade_render_result(
                self.render_combat_snapshot(
                    campaign_id,
                    principal_id,
                    audience_projection,
                )
            )
        elif view == "transaction_history":
            effective_query = query or str(data.get("query") or "")
            page_limit = _support._page_limit(data.get("limit", limit))
            page_scope = f"combat_query:{campaign_id}:{view}:{principal_id}"
            fingerprint, page_offset = _support._cursor_offset(
                scope=page_scope,
                query=effective_query,
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            result = self.state_history(campaign_id, page_limit + 1, principal_id, page_offset)
        else:
            receipt_key = str(self.required(data, "idempotency_key")).strip()
            if not receipt_key:
                raise ValueError("idempotency_key is required")
            result = self.state_idempotency_receipt(
                campaign_id,
                receipt_key,
                data.get("branch_id"),
                principal_id,
            )
        if view == "transaction_history":
            result, page = _support._authority_page(
                result,
                fingerprint=fingerprint,
                offset=page_offset,
                limit=page_limit,
                query=effective_query,
            )
            return self.facade_result(view, result, page=page)
        return self.facade_result(view, result)

    def combat_movement(
        self,
        campaign_id: str,
        actor_id: str,
        action: Literal["move", "stand"],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Move or stand using the current campaign revision and a request key.

        move payload={distance, destination?, path?, spatial_facts?}; distance is
        feet traveled, before difficult-terrain cost. In Agent positioning use
        spatial_facts={decision_id, reason, destination_legal, distance_ft}, with
        distance_ft equal to distance. Optional difficult_terrain_extra_ft adds
        cost; opportunity_attack_actor_ids lists actual threats. Do not invent
        grid coordinates when the encounter uses Agent positioning. stand uses {}.
        """
        data = self.facade_payload(payload)
        result = (
            self.combat_move(
                campaign_id,
                actor_id,
                self.required(data, "distance"),
                data.get("destination"),
                data.get("path"),
                data.get("movement_mode", "voluntary"),
                data.get("travel_mode", "walk"),
                self.facade_bool(data, "crawl"),
                data.get("spatial_facts"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
            if action == "move"
            else self.combat_stand(
                campaign_id, actor_id, principal_id, expected_revision, branch_id, idempotency_key
            )
        )
        return self.facade_result(action, result)

    def combat_hp_change(
        self,
        campaign_id: str,
        target_id: str,
        action: Literal["damage", "fall", "heal", "stabilize", "save_damage"],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply structured damage or healing; damage parts and healing amounts stay distinct."""
        data = self.facade_payload(payload)
        if action == "damage":
            result = self.combat_apply_damage(
                campaign_id,
                target_id,
                self.required(data, "parts"),
                self.facade_bool(data, "critical"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
                knock_out=self.facade_bool(data, "knock_out"),
                melee=self.facade_bool(data, "melee"),
            )
        elif action == "fall":
            result = self.combat_apply_fall(
                campaign_id,
                target_id,
                self.required(data, "distance_ft"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "heal":
            result = self.combat_heal(
                campaign_id,
                target_id,
                self.required(data, "amount"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
                source_actor_id=data.get("source_actor_id"),
                spell_id=data.get("spell_id"),
                spell_level=data.get("spell_level"),
            )
        elif action == "stabilize":
            result = self.combat_source_stabilize(
                campaign_id,
                target_id,
                self.required(data, "source_excerpt"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        else:
            data = self.facade_payload(payload)
            save_damage_target_ids = list(data.get("target_ids") or [target_id])
            if target_id not in save_damage_target_ids:
                raise _support.CombatEngineError(
                    "combat_hp_change target_id must be included in save_damage target_ids"
                )
            result = self.combat_save_damage(
                campaign_id,
                save_damage_target_ids,
                source_actor_id=self.required(data, "source_actor_id"),
                source_card_id=self.required(data, "source_card_id"),
                source_card_kind=self.required(data, "source_card_kind"),
                save_ability=self.required(data, "save_ability"),
                save_dc=self.required(data, "save_dc"),
                damage_expression=self.required(data, "damage_expression"),
                damage_type=self.required(data, "damage_type"),
                half_on_success=self.required_boolean(data, "half_on_success"),
                save_advantage=self.facade_bool(data, "save_advantage"),
                save_disadvantage=self.facade_bool(data, "save_disadvantage"),
                mechanic_source_excerpt=self.required(
                    data,
                    "mechanic_source_excerpt",
                ),
                agent_ruling=self.required(data, "agent_ruling"),
                spatial_facts=data.get("spatial_facts"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        return self.facade_result(action, result)

    def combat_choice(
        self,
        campaign_id: str,
        action: Literal[
            "open",
            "resolve",
            "resolve_defense",
            "on_hit_ruling",
            "execute_plan",
        ],
        payload: dict[str, Any],
        actor_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Open or resolve a validated choice window during active combat."""
        if action in {
            "on_hit_ruling",
            "execute_plan",
        }:
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
        self.require_facade_phase(
            campaign_id,
            f"combat_choice({action})",
            _support.PROFILE_COMBAT,
        )
        resolved_actor_id = str(self.required({"actor_id": actor_id}, "actor_id"))
        if action == "execute_plan":
            data = self.facade_payload(payload)
            result = self.combat_resolution_plan(
                campaign_id,
                resolved_actor_id,
                self.required(data, "commitment"),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
            )
        elif action == "open":
            data = self.facade_payload(payload)
            result = self.combat_choice_open(
                campaign_id,
                resolved_actor_id,
                data["event"],
                data.get("candidates"),
                data.get("kind", "reaction"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "resolve_defense":
            data = self.facade_payload(payload)
            choice_id = self.required(data, "choice_id")
            _campaign, encounter = self.active_encounter(campaign_id)
            window = next(
                (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
                None,
            )
            resolver = (
                self.combat_magic_missile_defense
                if isinstance(window, dict) and window.get("trigger") == "magic_missile_targeted"
                else self.combat_reaction_defense
            )
            result = resolver(
                campaign_id,
                resolved_actor_id,
                choice_id,
                self.required(data, "selection"),
                principal_id,
                branch_id,
                expected_revision,
                idempotency_key,
            )
        elif action == "resolve":
            data = self.facade_payload(payload)
            result = self.combat_choice_resolve(
                campaign_id,
                resolved_actor_id,
                data["choice_id"],
                data["selection"],
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        else:
            data = self.facade_payload(payload)
            result = self.combat_on_hit_ruling(
                campaign_id,
                resolved_actor_id,
                data["choice_id"],
                data["selection"],
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        return self.facade_result(action, result)

    def combat_ready(
        self,
        campaign_id: str,
        action: Literal[
            "ready_spell", "trigger_spell", "resolve_spell", "trigger_action", "resolve_action"
        ],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run readied spell/action transitions without bypassing trigger or release validation."""
        data = self.facade_payload(payload)
        if action == "ready_spell":
            result = self.combat_ready_spell(
                campaign_id,
                self.required(data, "actor_id"),
                self.required(data, "spell_id"),
                self.required(data, "trigger"),
                data.get("cast_level"),
                data.get("declaration"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "trigger_spell":
            result = self.combat_readied_spell_trigger(
                campaign_id,
                self.required(data, "readied_id"),
                self.required(data, "event"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "resolve_spell":
            result = self.combat_readied_spell_resolve(
                campaign_id,
                self.required(data, "actor_id"),
                self.required(data, "choice_id"),
                self.required_boolean(data, "release"),
                data.get("declaration"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        elif action == "trigger_action":
            result = self.combat_readied_action_trigger(
                campaign_id,
                self.required(data, "readied_id"),
                self.required(data, "event"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        else:
            result = self.combat_readied_action_resolve(
                campaign_id,
                self.required(data, "actor_id"),
                self.required(data, "choice_id"),
                self.required_boolean(data, "release"),
                data.get("declaration"),
                principal_id,
                expected_revision,
                branch_id,
                idempotency_key,
            )
        return self.facade_result(action, result)
