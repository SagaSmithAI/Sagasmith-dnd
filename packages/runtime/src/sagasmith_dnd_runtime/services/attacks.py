"""Attacks application operations with explicit shared services."""

from __future__ import annotations

from typing import Any

from .. import application_support as _support


class AttacksService:
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
            value["context"] = {}
            value["rulings"] = [
                item
                for item in value.get("rulings", [])
                if isinstance(item, dict) and item.get("source") == "dm_ruling"
            ]
        return value

    def validate_agent_attack_context(
        self,
        campaign_id: str,
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
            if not isinstance(raw_spatial_facts, dict):
                raise _support.NeedsRulingError(
                    "agent-positioned attacks require an Agent spatial decision",
                    missing=("attack.spatial_facts",),
                    ruling_kind="agent_dm_adjudication",
                )
            allowed_spatial_fields = {
                "decision_id",
                "reason",
                "targetable",
                "in_range",
                "long_range",
                "cover_degree",
                "attacker_can_see_target",
                "target_can_see_attacker",
                "target_within_5_ft",
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
                "attacker_can_see_target",
                "target_can_see_attacker",
            }
            unknown = set(raw_spatial_facts) - allowed_spatial_fields
            missing = required_spatial_fields - set(raw_spatial_facts)
            decision_id = str(raw_spatial_facts.get("decision_id") or "").strip()
            reason = " ".join(str(raw_spatial_facts.get("reason") or "").split())
            if unknown or missing or not decision_id or not reason:
                raise _support.CombatEngineError(
                    "Agent spatial facts require one decision_id, reason, and the attack "
                    "facts needed to determine targetability"
                )
            for field in {
                "targetable",
                "in_range",
                "attacker_can_see_target",
                "target_can_see_attacker",
            }:
                if not isinstance(raw_spatial_facts.get(field), bool):
                    raise _support.CombatEngineError(f"Agent spatial fact {field} must be boolean")
            for field in {"long_range", "target_within_5_ft"}:
                if field in raw_spatial_facts and not isinstance(raw_spatial_facts[field], bool):
                    raise _support.CombatEngineError(f"Agent spatial fact {field} must be boolean")
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
                "target_within_5_ft": bool(raw_spatial_facts.get("target_within_5_ft", False)),
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
        """Validate an attack and return a non-mutating resolution plan."""
        self.require_combat_actor_or_steel_defender_owner_control(
            campaign_id, actor_id, principal_id
        )
        campaign, encounter = self.active_encounter(campaign_id)
        resolved_branch_id = self.require_current_branch(campaign_id, None)
        self.require_campaign_actor(campaign_id, target_id)
        action = self.sanitize_attack_action(campaign_id, principal_id, dict(action or {}))
        deflect_declaration = action.pop("deflect_attack", None)
        self.validate_agent_attack_context(
            campaign_id,
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
        """Resolve an attack and atomically update the attacker, target and encounter."""
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
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
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
        self.validate_agent_attack_context(
            campaign_id,
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
        attacker = self.character_view(attacker_record, rules_context=rule_context)
        target = self.character_view(target_record, rules_context=rule_context)
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
        if defenses:
            result = {
                **attack_roll,
                "attack_payment": attack_payment,
                "pending_reaction": True,
            }
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

            def pending_attack_response(revisions: list[Any]) -> dict[str, Any]:
                response = {
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
                rule_receipts=_support.core_receipts(
                    rule_context,
                    ["dnd5e.core.reaction.post_hit_defense"],
                    "attack.hit.before_damage",
                )
                + attack_payment_receipts,
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

        def attack_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
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
                rule_receipts=list(result.get("rule_receipts") or []),
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
            or window.get("target_id") != target_id
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
        if window.get("target_visible"):
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
        plan = _support.preflight_attack(
            attacker,
            target,
            action=action_payload,
            encounter=trigger_encounter,
            allow_out_of_turn=True,
            rules=rule_context,
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
        if weapon is not None and weapon.get("attack_type") != "melee":
            raise _support.CombatEngineError("opportunity attacks require a melee attack")
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
        next_encounter = _support.resolve_choice_window(
            encounter,
            choice_id=choice_id,
            actor_id_value=actor_id,
            selection={"id": "opportunity_attack"},
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
                "trigger": "opportunity_attack",
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
                operation="combat.reaction.attack.roll",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "pending_reaction",
                    "result": result,
                    "choice": defense_window,
                    "combat": next_encounter,
                },
                character_updates=updates,
                rule_receipts=_support.core_receipts(
                    rule_context,
                    [
                        "dnd5e.core.mcp.opportunity_melee_only",
                        "dnd5e.core.reaction.post_hit_defense",
                    ],
                    "reaction.opportunity_attack.hit",
                ),
            )
            return self.combat_response(campaign_id, principal_id, response)
        updated_attacker, updated_target, result = _support.resolve_attack_damage(
            updated_attacker,
            target,
            plan=plan,
            attack=attack_roll,
            rules=rule_context,
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
            operation="combat.reaction.attack",
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
                *_support.core_receipts(
                    self.effective_rule_context(campaign_id),
                    ["dnd5e.core.mcp.opportunity_melee_only"],
                    "reaction.opportunity_attack",
                ),
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
    ) -> dict[str, Any]:
        """Attack a source-defined destructible scene object outside combat."""
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
        requested = _support.deepcopy(dict(object_state or {}))
        unknown = set(requested) - {
            "id",
            "name",
            "scene_id",
            "armor_class",
            "hit_points",
            "damage_immunities",
            "damage_filter",
        }
        if unknown:
            raise ValueError(f"unsupported source object fields: {sorted(unknown)}")
        object_id = str(requested.get("id") or "").strip()
        object_name = str(requested.get("name") or "").strip()
        scene_id = str(requested.get("scene_id") or "").strip()
        armor_class = requested.get("armor_class")
        hit_point_maximum = requested.get("hit_points")
        immunities = sorted(
            {
                str(item).strip().casefold()
                for item in requested.get("damage_immunities") or []
                if str(item).strip()
            }
        )
        raw_damage_filter = requested.get("damage_filter") or {}
        if not isinstance(raw_damage_filter, dict):
            raise ValueError("source object damage_filter must be an object")
        damage_filter_unknown = set(raw_damage_filter) - {
            "allowed_damage_types",
            "required_any_weapon_traits",
        }
        if damage_filter_unknown:
            raise ValueError(
                f"unsupported source object damage_filter fields: {sorted(damage_filter_unknown)}"
            )
        allowed_damage_types = sorted(
            {
                str(item).strip().casefold()
                for item in raw_damage_filter.get("allowed_damage_types") or []
                if str(item).strip()
            }
        )
        required_any_weapon_traits = sorted(
            {
                str(item).strip().casefold()
                for item in raw_damage_filter.get("required_any_weapon_traits") or []
                if str(item).strip()
            }
        )
        invalid_damage_types = sorted(set(allowed_damage_types) - set(_support.DAMAGE_TYPES))
        if invalid_damage_types:
            raise ValueError(
                f"source object damage_filter has invalid damage types: {invalid_damage_types}"
            )
        invalid_weapon_traits = sorted(set(required_any_weapon_traits) - {"adamantine", "magical"})
        if invalid_weapon_traits:
            raise ValueError(
                "source object damage_filter has unsupported weapon traits: "
                f"{invalid_weapon_traits}"
            )
        damage_filter = (
            {
                "allowed_damage_types": allowed_damage_types,
                "required_any_weapon_traits": required_any_weapon_traits,
            }
            if allowed_damage_types or required_any_weapon_traits
            else {}
        )
        if not object_id or not object_name or not scene_id:
            raise ValueError("source object requires id, name, and scene_id")
        if (
            isinstance(armor_class, bool)
            or not isinstance(armor_class, int)
            or not 1 <= armor_class <= 30
        ):
            raise ValueError("source object armor_class must be an integer from 1 to 30")
        if (
            isinstance(hit_point_maximum, bool)
            or not isinstance(hit_point_maximum, int)
            or hit_point_maximum < 1
        ):
            raise ValueError("source object hit_points must be a positive integer")
        _, exact_source, _ = self.managed_module_source_ref(
            campaign_id,
            source_ref,
            require_exact=True,
            expected_scene_id=scene_id,
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
        }
        scope = f"source-object-attack:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
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
        immutable = {
            "id": object_id,
            "name": object_name,
            "scene_id": scene_id,
            "armor_class": armor_class,
            "hit_point_maximum": hit_point_maximum,
            "damage_immunities": immunities,
            "damage_filter": damage_filter,
            "source_ref": exact_source,
        }
        if existing:
            if any(existing.get(key) != value for key, value in immutable.items()):
                raise ValueError(
                    "source object id already exists with different source-defined data"
                )
            if existing.get("destroyed"):
                raise _support.CombatEngineError("source object is already destroyed")
            hit_points_before = int(existing["hit_points"])
        else:
            hit_points_before = hit_point_maximum

        campaign_edition = self.campaign_rules_edition(campaign_id)
        object_sheet = _support.default_character_sheet()
        object_sheet["edition"] = campaign_edition
        object_sheet["combat"]["hp"] = {
            "value": hit_points_before,
            "max": hit_point_maximum,
            "temp": 0,
        }
        object_sheet["combat"]["ac"]["override"] = armor_class
        weapon = next(
            (
                item
                for item in attacker["sheet"]["inventory"]["items"]
                if str(item.get("id") or "") == str(weapon_id)
                and str(item.get("kind") or "") == "weapon"
            ),
            None,
        )
        if weapon is None:
            raise ValueError("source object attack weapon is absent from the actor")
        weapon_mechanics = dict(weapon.get("mechanics") or {})
        weapon_traits = set()
        if (
            str(weapon.get("attunement") or "") == "attuned"
            or int(weapon_mechanics.get("magic_bonus", 0) or 0) != 0
            or bool(weapon_mechanics.get("additional_damage"))
            or bool(weapon_mechanics.get("on_hit_effect"))
        ):
            weapon_traits.add("magical")
        if "adamantine" in {
            str(item).strip().casefold() for item in weapon_mechanics.get("materials") or []
        }:
            weapon_traits.add("adamantine")
        weapon_trait_requirement_met = not required_any_weapon_traits or bool(
            weapon_traits.intersection(required_any_weapon_traits)
        )
        effective_immunities = set(immunities)
        if allowed_damage_types:
            effective_immunities.update(set(_support.DAMAGE_TYPES) - set(allowed_damage_types))
        if not weapon_trait_requirement_met:
            effective_immunities.update(allowed_damage_types or _support.DAMAGE_TYPES)
        object_sheet["traits"]["immunities"] = sorted(effective_immunities)
        target = {
            "id": f"scene-object:{scene_id}:{object_id}",
            "name": object_name,
            "kind": "object",
            "sheet": _support.validate_character_sheet(object_sheet),
            "derived": self.derive_character_sheet(object_sheet),
            "death_saves": False,
        }
        rules = self.effective_rule_context(
            campaign_id,
            facts={
                "actor_id": character_id,
                "target_kind": "source_object",
                "target_id": object_id,
                "scene_id": scene_id,
            },
            branch_id=resolved_branch_id,
        )
        plan = _support.preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": weapon_id,
                "context": {
                    "advantage": bool(advantage),
                    "disadvantage": bool(disadvantage),
                },
            },
            require_attack_action=False,
            rules=rules,
        )
        attack_roll = _support.roll_attack_action(plan=plan)
        updated_attacker, updated_target, settled = _support.resolve_attack_damage(
            attacker,
            target,
            plan=plan,
            attack=attack_roll,
            rules=rules,
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
        hit_points_after = int(updated_target["sheet"]["combat"]["hp"]["value"])
        object_after = {
            **immutable,
            "hit_points": hit_points_after,
            "destroyed": hit_points_after <= 0,
            "last_attack": {
                "character_id": character_id,
                "weapon_id": str(weapon_id),
                "reason": normalized_reason,
                "attack": _support.deepcopy(attack_roll),
                "damage": _support.deepcopy(settled.get("damage")),
                "damage_filter": _support.deepcopy(damage_filter),
                "weapon_traits": sorted(weapon_traits),
                "weapon_trait_requirement_met": weapon_trait_requirement_met,
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
        character_updates = []
        if next_attacker_sheet != current.sheet:
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=character_id,
                    sheet=_support.validate_character_sheet(next_attacker_sheet),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            )
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

        def source_object_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
                "status": "committed",
                "character": updated_character_view,
                "object": object_after,
                "attack": attack_roll,
                "damage": settled.get("damage"),
                "ammunition": ammunition,
                "limited_use": limited_use,
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }
            stream = _support.active_random_stream()
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

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
            rule_receipts=list(settled.get("rule_receipts") or []),
        )
        return source_object_response(list(revisions_result or []))
