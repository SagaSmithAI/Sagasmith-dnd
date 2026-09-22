"""Spells application operations with explicit shared services."""

from __future__ import annotations

from typing import Any, Literal

from .. import application_support as _support


class SpellsService:
    def validate_spell_spatial_facts(self, facts: Any) -> dict[str, Any]:
        """Validate an explicit DM targeting decision without inventing coordinates."""
        required = {"decision_id", "reason", "targetable", "in_range", "attacker_can_see_target"}
        optional = {"cover_degree", "target_can_see_attacker"}
        if not isinstance(facts, dict) or required - set(facts) or set(facts) - required - optional:
            raise _support.CombatEngineError(
                "Agent spell spatial_facts require decision_id, reason, targetable, "
                "in_range, attacker_can_see_target; optional cover_degree, target_can_see_attacker"
            )
        value = _support.deepcopy(facts)
        for key in ("decision_id", "reason"):
            if not isinstance(value[key], str) or not value[key].strip():
                raise _support.CombatEngineError(f"spell spatial fact {key} must be non-empty text")
        for key in ("targetable", "in_range", "attacker_can_see_target", "target_can_see_attacker"):
            if key in value and not isinstance(value[key], bool):
                raise _support.CombatEngineError(f"spell spatial fact {key} must be boolean")
        if value.get("cover_degree", "none") not in {"none", "half", "three_quarters", "total"}:
            raise _support.CombatEngineError("invalid spell spatial cover_degree")
        if not value["targetable"] or value.get("cover_degree") == "total":
            raise _support.CombatEngineError("spell target is not targetable")
        if not value["in_range"]:
            raise _support.CombatEngineError("spell target is outside range")
        return value

    def persisted_standard_spell_ruling_requirement(
        self,
        source_card: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the exact persisted Agent clause for one standard spell card."""

        return _support.source_cards.persisted_standard_spell_ruling_requirement(
            source_card,
            standard_pack_ids=frozenset(
                {
                    _support.CORE_CONTENT_PACK_ID,
                    _support.CORE_2024_CONTENT_PACK_ID,
                    _support.STANDARD_2014_CONTENT_PACK_ID,
                }
            ),
        )

    def validate_persisted_standard_spell_ruling(
        self,
        raw_ruling: Any,
        *,
        source_card: dict[str, Any],
        requirement: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate Agent judgment against the immutable standard-card clause."""

        try:
            return _support.source_cards.validate_persisted_standard_spell_ruling(
                raw_ruling,
                source_card=source_card,
                requirement=requirement,
            )
        except _support.source_cards.CharacterSourceCardError as error:
            raise _support.CombatEngineError(str(error)) from error

    def apply_standard_spell_on_hit_mechanics(
        self,
        encounter: dict[str, Any],
        *,
        result: dict[str, Any],
        attacker_id: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        """Commit locked standard-spell effects without interpreting source prose."""

        mechanics = list(result.pop("standard_on_hit_mechanics", []) or [])
        if not result.get("hit") or not mechanics:
            return []
        allowed = {
            "next_attack_advantage_until_source_turn_end",
            "healing_prevention_until_source_turn_start",
            "healing_prevention_until_source_turn_end",
            "undead_attack_disadvantage_against_source_until_source_turn_start",
        }
        unknown = set(mechanics) - allowed
        if unknown:
            raise _support.CombatEngineError(
                f"unsupported locked standard spell on-hit mechanics: {sorted(unknown)}"
            )
        target_sheet = self.combat_actor_snapshot(target_id)["sheet"]
        creature_type = str(
            dict(target_sheet.get("progression") or {}).get("species") or ""
        ).casefold()
        activated_turn_token = self.encounter_turn_token(encounter)
        committed: list[dict[str, Any]] = []
        for mechanic in mechanics:
            if (
                mechanic == "undead_attack_disadvantage_against_source_until_source_turn_start"
                and "undead" not in creature_type
            ):
                continue
            effect: dict[str, Any] = {
                "id": f"standard-spell-on-hit-{_support.uuid4().hex}",
                "mechanic_id": _support.SPELL_RESOLUTION_MECHANIC_ID,
                "standard_on_hit_mechanic": mechanic,
                "source_actor_id": attacker_id,
                "target_id": target_id,
                "active": True,
                "activated_turn_token": activated_turn_token,
            }
            if mechanic == "next_attack_advantage_until_source_turn_end":
                effect.update(
                    kind="next_attack_advantage",
                    expires_on_actor_id=attacker_id,
                    expires_at="source_turn_end",
                )
            elif mechanic.startswith("healing_prevention_until_source_turn_"):
                effect.update(
                    kind="healing_prevention",
                    expires_on_actor_id=attacker_id,
                    expires_at=(
                        "source_turn_start" if mechanic.endswith("_start") else "source_turn_end"
                    ),
                )
            else:
                effect.update(
                    kind="attack_disadvantage_against_source",
                    protected_actor_id=attacker_id,
                    expires_on_actor_id=attacker_id,
                    expires_at="source_turn_start",
                )
            encounter.setdefault("ongoing_effects", []).append(effect)
            committed.append(_support.deepcopy(effect))
        if committed:
            result["standard_on_hit_effects"] = committed
        return committed

    def add_concentration_window(
        self,
        encounter: dict[str, Any],
        target_id: str,
        concentration: dict[str, Any] | None,
        *,
        next_revision: int,
    ) -> None:
        """Persist one immediate concentration save without silently replacing another."""
        if not concentration:
            return
        if any(
            item.get("status", "pending") == "pending"
            and item.get("kind") == "concentration"
            and item.get("actor_id") == target_id
            for item in encounter.get("pending", [])
        ):
            raise _support.CombatEngineError(
                "the actor already has a pending concentration save; resolve it first"
            )
        pending = dict(concentration)
        pending.update(
            {
                "id": f"concentration:{target_id}:{next_revision}",
                "kind": "concentration",
                "actor_id": target_id,
            }
        )
        encounter["pending"] = [*list(encounter.get("pending") or []), pending]

    def require_combat_spell_turn_legal(
        self,
        encounter: dict[str, Any],
        *,
        actor_id: str,
        payment: str,
        spell_level: int,
        casting_time: str,
        spent_slot: bool,
    ) -> list[dict[str, Any]]:
        """Enforce the edition's per-turn spell limit before any resource is spent."""
        turn_casts = list(dict(encounter.get("turn_spell_casts") or {}).get(actor_id, []))
        from sagasmith_dnd.edition_policy import edition_policy

        try:
            edition_policy(encounter.get("ruleset")).validate_spell_turn(
                turn_casts, payment=payment, spell_level=spell_level,
                casting_time=casting_time, spent_slot=spent_slot,
            )
        except ValueError as error:
            raise _support.CombatEngineError(str(error)) from error
        return turn_casts

    def record_combat_spell_cast(
        self,
        encounter: dict[str, Any],
        *,
        actor_id: str,
        spell_id: str,
        spell_level: int,
        payment: str,
        casting_time: str,
        spent_slot: bool,
        **extra: Any,
    ) -> None:
        casts_by_actor = dict(encounter.get("turn_spell_casts") or {})
        casts_by_actor[actor_id] = [
            *list(casts_by_actor.get(actor_id, [])),
            {
                "spell_id": spell_id,
                "spell_level": spell_level,
                "payment": payment,
                "casting_time": casting_time,
                "spent_slot": spent_slot,
                **extra,
            },
        ]
        encounter["turn_spell_casts"] = casts_by_actor

    def magic_missile_shield_defenses(
        self,
        campaign_id: str,
        target_id: str,
        encounter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return Shield casts legal for this exact targeting reaction."""
        combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if str(item.get("actor_id") or "") == target_id
            ),
            None,
        )
        if combatant is None:
            raise _support.CombatEngineError(f"combatant not found: {target_id}")
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0 or _support.INCAPACITATING_STATE_IDS & {
            str(item).casefold() for item in combatant.get("conditions", [])
        }:
            return []
        target = self.combat_actor_snapshot(target_id)
        result: list[dict[str, Any]] = []
        for candidate in _support.available_shield_magic_missile_defenses(
            target["sheet"],
            rules=self.effective_rule_context(
                campaign_id,
                facts={
                    "actor_id": target_id,
                    "spell_id": "",
                    "kind": "spell_magic_missile_immunity",
                },
            ),
        ):
            legal_casts: list[dict[str, Any]] = []
            for option in candidate.get("cast_options", []):
                payment = dict(option.get("payment") or {})
                try:
                    self.require_combat_spell_turn_legal(
                        encounter,
                        actor_id=target_id,
                        payment="reaction",
                        spell_level=1,
                        casting_time="reaction",
                        spent_slot=payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
                    )
                except _support.CombatEngineError:
                    continue
                legal_casts.append(_support.deepcopy(option))
            if legal_casts:
                result.append(
                    {
                        **candidate,
                        "cast_levels": [int(item["cast_level"]) for item in legal_casts],
                        "cast_options": legal_casts,
                    }
                )
        return result

    def reconcile_actor_witch_bolt_concentration(
        self,
        encounter: dict[str, Any],
        actor_id_value: str,
        sheet: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Synchronize a caster tether with its exact character concentration."""

        active_ids = {
            str(effect.get("id") or "")
            for effect in sheet.get("effects", [])
            if effect.get("active") and effect.get("concentration")
        }
        reconciled = _support.reconcile_witch_bolt_concentration(
            encounter,
            actor_id_value=actor_id_value,
            active_concentration_effect_ids=active_ids,
        )
        if reconciled["ended"]:
            encounter.clear()
            encounter.update(reconciled["encounter"])
        return list(reconciled["ended"])

    def validate_spell_creature_target(
        self,
        encounter: dict[str, Any],
        *,
        caster_id: str,
        target_id: str,
        spell: dict[str, Any],
        resolution: dict[str, Any],
        spatial_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        combatants = {
            str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
        }
        caster = combatants.get(caster_id)
        target = combatants.get(target_id)
        if caster is None:
            raise _support.CombatEngineError("spell caster is not in this encounter")
        if target is None:
            raise _support.CombatEngineError(f"spell target is not in this encounter: {target_id}")
        target_conditions = {str(item).casefold() for item in target.get("conditions", [])}
        if "dead" in target_conditions:
            raise _support.CombatEngineError("a dead combatant is not a creature target")
        agent_mode = encounter.get("positioning_mode") == "agent"
        spatial = self.validate_spell_spatial_facts(spatial_facts) if agent_mode else None
        distance = (
            None if agent_mode
            else self.combat_distance(caster.get("position"), target.get("position"))
        )
        if distance is None and not agent_mode:
            raise _support.CombatEngineError("spell targeting requires recorded map positions")
        spell_range = dict(dict(spell.get("definition") or {}).get("range") or {})
        range_kind = str(spell_range.get("kind") or "special")
        maximum = 5 if range_kind == "touch" else int(spell_range.get("normal_ft", 0) or 0)
        attack = dict(resolution.get("attack") or {})
        if attack.get("range_ft_override") is not None:
            maximum = int(attack["range_ft_override"])
        if range_kind == "self" and target_id != caster_id:
            raise _support.CombatEngineError("self-range spell must target its caster")
        if range_kind not in {"self", "touch"} and maximum <= 0:
            raise _support.CombatEngineError("spell has no executable target range")
        if range_kind != "self" and distance is not None and distance > maximum:
            raise _support.CombatEngineError("spell target is outside range")
        targeting = dict(resolution.get("targeting") or {})
        visible = (
            spatial["attacker_can_see_target"] if spatial is not None
            else _support.can_see(caster, target)
        )
        if targeting.get("requires_sight") and not visible:
            raise _support.CombatEngineError("spell requires a target the caster can see")
        creature_type = str(
            self.characters.get(target_id).sheet.get("progression", {}).get("species") or ""
        ).casefold()
        for excluded in targeting.get("excluded_creature_types", []):
            if str(excluded).casefold() in creature_type:
                raise _support.CombatEngineError(
                    f"spell has no effect on the target creature type: {excluded}"
                )
        return {
            "target_id": target_id, "distance_ft": distance,
            **({"spatial_facts": spatial} if spatial is not None else {}),
        }

    def advance_spell_attack_resolution(
        self,
        encounter: dict[str, Any],
        *,
        resolution_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        resolutions = dict(encounter.get("spell_resolutions") or {})
        resolution = _support.deepcopy(dict(resolutions.get(resolution_id) or {}))
        if resolution.get("kind") != "spell_attack":
            raise _support.CombatEngineError("spell attack resolution is unavailable")
        remaining = int(resolution.get("remaining_attacks", 0) or 0)
        if remaining < 1:
            raise _support.CombatEngineError("spell attack resolution has no attacks remaining")
        resolution["remaining_attacks"] = remaining - 1
        resolution["results"] = [
            *list(resolution.get("results") or []),
            _support.deepcopy(result),
        ]
        if resolution["remaining_attacks"] == 0:
            resolutions.pop(resolution_id, None)
            encounter["pending"] = [
                item
                for item in encounter.get("pending", [])
                if str(item.get("id") or "") != resolution_id
            ]
        else:
            resolutions[resolution_id] = resolution
            for item in encounter.get("pending", []):
                if str(item.get("id") or "") == resolution_id:
                    item["remaining_attacks"] = resolution["remaining_attacks"]
        if resolutions:
            encounter["spell_resolutions"] = resolutions
        else:
            encounter.pop("spell_resolutions", None)
        return {
            "id": resolution_id,
            "remaining_attacks": resolution["remaining_attacks"],
            "completed": resolution["remaining_attacks"] == 0,
        }

    def validate_magic_missile_targets(
        self,
        encounter: dict[str, Any],
        *,
        caster_id: str,
        allocations: list[dict[str, Any]],
        cast_level: int,
        target_spatial_facts: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate source-rule targeting against current map and visibility facts."""
        normalized = _support.validate_magic_missile_allocations(allocations, cast_level=cast_level)
        combatants = {
            str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
        }
        caster = combatants.get(caster_id)
        if caster is None:
            raise _support.CombatEngineError("Magic Missile caster is not in this encounter")

        agent_mode = encounter.get("positioning_mode") == "agent"
        if agent_mode and (
            not isinstance(target_spatial_facts, dict)
            or set(target_spatial_facts) != {str(item["target_id"]) for item in normalized}
        ):
            raise _support.CombatEngineError(
                "Magic Missile declaration requires target_spatial_facts "
                "keyed by every allocated target_id"
            )
        caster_position = self.combat_coordinates(caster.get("position"))
        if caster_position is None and not agent_mode:
            raise _support.CombatEngineError(
                "Magic Missile range requires the caster's map position"
            )
        for allocation in normalized:
            target_id = str(allocation["target_id"])
            target = combatants.get(target_id)
            if target is None:
                raise _support.CombatEngineError(
                    f"Magic Missile target is not in this encounter: {target_id}"
                )
            conditions = {str(item).casefold() for item in target.get("conditions", [])}
            if "dead" in conditions:
                raise _support.CombatEngineError("Magic Missile cannot target a dead creature")
            if agent_mode:
                spatial = self.validate_spell_spatial_facts(target_spatial_facts[target_id])
                if not spatial["attacker_can_see_target"]:
                    raise _support.CombatEngineError(
                        "Magic Missile requires a target the caster can see"
                    )
                allocation["spatial_facts"] = spatial
                allocation["distance_ft"] = None
                continue
            target_position = self.combat_coordinates(target.get("position"))
            if target_position is None:
                raise _support.CombatEngineError(
                    "Magic Missile range requires every target's map position"
                )
            distance = int(
                max(
                    abs(float(caster_position[0]) - float(target_position[0])),
                    abs(float(caster_position[1]) - float(target_position[1])),
                )
                * 5
            )
            if distance > 120:
                raise _support.CombatEngineError(
                    "Magic Missile target is outside its 120-foot range"
                )
            concealed = bool(target.get("hidden", False)) or "invisible" in conditions
            visible_to = {str(item) for item in target.get("visible_to_actor_ids") or []}
            if concealed and caster_id not in visible_to:
                raise _support.CombatEngineError(
                    "Magic Missile requires a target the caster can see"
                )
            allocation["distance_ft"] = distance
        return normalized

    def settle_magic_missile_damage(
        self,
        campaign_id: str,
        encounter: dict[str, Any],
        resolution: dict[str, Any],
        *,
        next_revision: int,
        sheet_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
        """Roll and apply every dart separately after all target reactions settle."""
        value = _support.deepcopy(encounter)
        sheets = {
            str(key): _support.deepcopy(item) for key, item in (sheet_overrides or {}).items()
        }
        shielded = {str(item) for item in resolution.get("shielded_target_ids", [])}
        target_results: list[dict[str, Any]] = []
        concentration_windows: list[dict[str, Any]] = []
        resolution_id = str(resolution["id"])
        spell_id = str(resolution["spell_id"])
        for allocation in resolution.get("allocations", []):
            target_id = str(allocation["target_id"])
            if target_id in shielded:
                target_results.append(
                    {
                        "target_id": target_id,
                        "darts": int(allocation["darts"]),
                        "shielded": True,
                        "dart_results": [],
                    }
                )
                continue
            sheet = sheets.get(target_id)
            if sheet is None:
                sheet = _support.deepcopy(self.characters.get(target_id).sheet)
            combatant = next(
                item
                for item in value.get("combatants", [])
                if str(item.get("actor_id") or "") == target_id
            )
            dart_results: list[dict[str, Any]] = []
            for dart_index in range(int(allocation["darts"])):
                dice = _support.asdict(_support.roll("1d4+1"))
                applied = _support.apply_damage_to_sheet(
                    sheet,
                    amount=int(dice["total"]),
                    damage_type="force",
                    source=spell_id,
                    ruleset=str(value.get("ruleset") or "2014"),
                    death_saves=self.combatant_zero_hp_buffered(combatant),
                )
                sheet = applied["sheet"]
                concentration = applied.get("concentration")
                if concentration:
                    concentration_windows.append(
                        {
                            **_support.deepcopy(concentration),
                            "id": (
                                f"concentration:{target_id}:{next_revision}:"
                                f"{resolution_id}:{dart_index}"
                            ),
                            "kind": "concentration",
                            "actor_id": target_id,
                            "source_resolution_id": resolution_id,
                            "dart_index": dart_index,
                        }
                    )
                dart_results.append(
                    {
                        "dart_index": dart_index,
                        "roll": dice,
                        **{key: item for key, item in applied.items() if key != "sheet"},
                    }
                )
            sheets[target_id] = sheet
            self.sync_combatant_conditions(value, target_id, sheet)
            _support.reconcile_readied_spells(value, target_id, sheet)
            target_results.append(
                {
                    "target_id": target_id,
                    "darts": int(allocation["darts"]),
                    "shielded": False,
                    "dart_results": dart_results,
                }
            )
        value["pending"] = [
            item
            for item in value.get("pending", [])
            if str(item.get("spell_resolution_id") or "") != resolution_id
        ]
        value["pending"] = [*list(value.get("pending") or []), *concentration_windows]
        resolutions = dict(value.get("spell_resolutions") or {})
        resolutions.pop(resolution_id, None)
        if resolutions:
            value["spell_resolutions"] = resolutions
        else:
            value.pop("spell_resolutions", None)
        result = {
            "kind": "magic_missile",
            "spell_id": spell_id,
            "caster_id": str(resolution["caster_id"]),
            "cast_level": int(resolution["cast_level"]),
            "dart_count": sum(int(item["darts"]) for item in resolution["allocations"]),
            "targets": target_results,
            "concentration_windows": len(concentration_windows),
        }
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "magic_missile", "result": _support.deepcopy(result)},
        ][-100:]
        return value, sheets, result

    def completed_spell_cast_ticks(
        self,
        spell: dict[str, Any],
        *,
        ritual: bool,
    ) -> int:
        """Resolve common source casting times onto the canonical 6-second scale."""

        casting_time = str(
            dict(spell.get("definition") or {}).get("casting_time") or "1 action"
        ).strip()
        normalized = casting_time.casefold()
        if normalized in {
            "action",
            "1 action",
            "bonus action",
            "1 bonus action",
            "reaction",
            "1 reaction",
        }:
            ticks = 1
        else:
            match = _support.re.fullmatch(r"(\d+)\s+(minute|minutes|hour|hours)", normalized)
            if match is None:
                raise _support.NeedsRulingError(
                    "this spell's casting time needs an Agent ruling before time can advance",
                    missing=("casting_time",),
                    ruling_kind="source_or_scene_fact",
                )
            amount = int(match.group(1))
            unit = "minute" if match.group(2).startswith("minute") else "hour"
            ticks = _support.game_time_ticks(unit, amount)
        if ritual:
            ticks += _support.game_time_ticks("minute", 10)
        return ticks

    def combat_magic_missile_defense(
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
        """Resolve one Shield targeting reaction, then settle all darts after the last choice."""
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "choice_id": choice_id,
            "selection": selection,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-magic-missile-defense:{campaign_id}:{resolved_branch_id}:{principal_id}"
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
            or window.get("trigger") != "magic_missile_targeted"
            or str(window.get("actor_id") or "") != actor_id
            or str(window.get("target_id") or "") != actor_id
        ):
            raise _support.CombatEngineError(
                "choice_id is not this actor's Magic Missile defense window"
            )
        resolution_id = str(window.get("spell_resolution_id") or "")
        resolution = _support.deepcopy(
            dict(dict(encounter.get("spell_resolutions") or {}).get(resolution_id) or {})
        )
        if resolution.get("kind") != "magic_missile":
            raise _support.CombatEngineError("Magic Missile resolution state is missing")
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
            raise _support.CombatEngineError("selection is not one of the Magic Missile defenses")
        used = selection_id not in {"decline", "skip", "pass"}
        next_encounter = _support.deepcopy(encounter)
        sheet_override: dict[str, dict[str, Any]] = {}
        spell_result: dict[str, Any] | None = None
        if used:
            if str(candidate.get("kind") or "") != "spell_magic_missile_immunity":
                raise _support.CombatEngineError("Magic Missile defense is not executable")
            cast_level = selection.get("cast_level")
            if isinstance(cast_level, bool) or not isinstance(cast_level, int):
                raise _support.CombatEngineError("Shield selection requires an integer cast_level")
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
            payment = dict(cast_option.get("payment") or {})
            self.require_combat_spell_turn_legal(
                next_encounter,
                actor_id=actor_id,
                payment="reaction",
                spell_level=1,
                casting_time="reaction",
                spent_slot=payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
            )
            next_encounter = _support.pay_activity_activation(
                next_encounter,
                actor_id_value=actor_id,
                activation_type="reaction",
            )
            target = self.characters.get(actor_id)
            spell_result = _support.consume_shield_reaction(
                target.sheet,
                spell_id=str(candidate.get("spell_id") or selection_id),
                cast_level=cast_level,
                trigger="magic_missile",
                rules=self.effective_rule_context(
                    campaign_id,
                    facts={
                        "actor_id": actor_id,
                        "spell_id": str(candidate.get("spell_id") or selection_id),
                        "cast_level": cast_level,
                        "trigger": "magic_missile_targeted",
                    },
                ),
            )
            if spell_result.get("status") != "committed":
                raise _support.CombatEngineError("Shield has an unresolved rule choice")
            sheet_override[actor_id] = spell_result["sheet"]
            self.record_combat_spell_cast(
                next_encounter,
                actor_id=actor_id,
                spell_id=str(candidate.get("spell_id") or selection_id),
                spell_level=1,
                payment="reaction",
                casting_time="reaction",
                spent_slot=payment.get("economy") in _support.SLOT_PAYMENT_ECONOMIES,
                cast_level=cast_level,
            )
            resolution["shielded_target_ids"] = sorted(
                {*map(str, resolution.get("shielded_target_ids", [])), actor_id}
            )
        next_encounter = _support.resolve_choice_window(
            next_encounter,
            choice_id=choice_id,
            actor_id_value=actor_id,
            selection={"id": selection_id},
        )
        resolutions = dict(next_encounter.get("spell_resolutions") or {})
        resolutions[resolution_id] = resolution
        next_encounter["spell_resolutions"] = resolutions
        remaining = [
            item
            for item in next_encounter.get("pending", [])
            if str(item.get("spell_resolution_id") or "") == resolution_id
            and item.get("status", "pending") == "pending"
        ]
        rule_receipts = [
            *list((spell_result or {}).get("rule_receipts") or []),
            *_support.core_receipts(
                self.effective_rule_context(campaign_id),
                ["dnd5e.core.mcp.magic_missile_atomicity"],
                "combat.spell.magic_missile.defense",
            ),
        ]
        if remaining:
            if sheet_override:
                self.sync_combatant_conditions(next_encounter, actor_id, sheet_override[actor_id])
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.magic_missile.defense",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "pending_reaction",
                    "result": {
                        "kind": "magic_missile",
                        "spell_id": resolution["spell_id"],
                        "reaction_defense": {
                            "used": used,
                            "spell_id": selection_id if used else None,
                            "effect_id": (spell_result or {}).get("effect_id"),
                        },
                    },
                    "choices": remaining,
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_id).notes
                        ),
                        expected_revision=self.characters.get(target_id).revision,
                    )
                    for target_id, sheet in sheet_override.items()
                ],
                rule_receipts=rule_receipts,
            )
        else:
            next_encounter, resolved_sheets, result = self.settle_magic_missile_damage(
                campaign_id,
                next_encounter,
                resolution,
                next_revision=campaign.revision + 1,
                sheet_overrides=sheet_override,
            )
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.magic_missile.resolve",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "committed",
                    "result": {
                        **result,
                        "reaction_defense": {
                            "used": used,
                            "spell_id": selection_id if used else None,
                            "effect_id": (spell_result or {}).get("effect_id"),
                        },
                    },
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_id).notes
                        ),
                        expected_revision=self.characters.get(target_id).revision,
                    )
                    for target_id, sheet in resolved_sheets.items()
                ],
                rule_receipts=rule_receipts,
            )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_cast_spell(
        self,
        campaign_id: str,
        actor_id: str,
        spell_id: str,
        cast_level: int | None = None,
        ritual: _support.StrictBool = False,
        signature_free_cast: _support.StrictBool = False,
        feature_cast_source: str | None = None,
        component_ruling: dict[str, Any] | None = None,
        source_item_id: str | None = None,
        choice_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        target_allocations: list[dict[str, Any]] | None = None,
        declaration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cast the exact spell_id recorded on the caster card, on a legal turn.

        Requires current campaign expected_revision and idempotency_key. The card
        determines action/slot cost; never spend them separately. target_allocations
        is only for source-bound Magic Missile, not ordinary spell targets.
        In Agent positioning, native single-target spells use declaration
        {target_id, spatial_facts:{decision_id,reason,targetable,in_range,
        attacker_can_see_target}}. Magic Missile uses target_allocations plus
        declaration={target_spatial_facts:{target_id: facts}}. Grid uses positions.
        For an Agent-resolved standard spell, omit declaration to obtain the
        agent_ruling_contract, then copy its submission_shape under declaration,
        filling application_id, decision and reason and preserving source_excerpt.
        payment_required=true means nothing has been paid yet. After payment is
        recorded, do not cast again to finish the effect or use another attack
        action. Follow the returned resolution/owned choice contract; combat_choice
        needs an actual choice_id, not an application_id. Agent-ruling commitment
        records payment and adjudication, not automatic target HP/condition changes.
        Resolve remaining source-grounded consequences through their public tools.
        """
        return self._settle_combat_spell(
            campaign_id=campaign_id,
            actor_id=actor_id,
            spell_id=spell_id,
            cast_level=cast_level,
            ritual=ritual,
            signature_free_cast=signature_free_cast,
            feature_cast_source=feature_cast_source,
            component_ruling=component_ruling,
            source_item_id=source_item_id,
            choice_id=choice_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            target_allocations=target_allocations,
            declaration=declaration,
        )

    def _settle_combat_spell(
        self,
        campaign_id: str,
        actor_id: str,
        spell_id: str,
        cast_level: int | None = None,
        ritual: _support.StrictBool = False,
        signature_free_cast: _support.StrictBool = False,
        feature_cast_source: str | None = None,
        component_ruling: dict[str, Any] | None = None,
        source_item_id: str | None = None,
        choice_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        target_allocations: list[dict[str, Any]] | None = None,
        declaration: dict[str, Any] | None = None,
        *,
        ready_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate a normal cast or settle a verified paid Ready source through one resolver."""
        ritual = _support._strict_boolean(ritual, "ritual")
        signature_free_cast = _support._strict_boolean(signature_free_cast, "signature_free_cast")
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "spell_id": spell_id,
            "cast_level": cast_level,
            "ritual": ritual,
            "signature_free_cast": signature_free_cast,
            "feature_cast_source": feature_cast_source,
            "component_ruling": component_ruling or {},
            "source_item_id": source_item_id,
            "choice_id": choice_id,
            "target_allocations": target_allocations,
            "declaration": declaration or {},
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-cast:{campaign_id}:{resolved_branch_id}:{principal_id}"
        if ready_context and not ready_context.get("validate_only"):
            scope, payload = ready_context["scope"], ready_context["payload"]
        replay = None if ready_context else self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        if source_item_id and (signature_free_cast or feature_cast_source):
            raise _support.CombatEngineError(
                "a magic item spell cannot use a character feature casting source"
            )
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        current = self.characters.get(actor_id)
        if ready_context:
            from dataclasses import replace

            from .readied_spells import bound_sheet

            current = replace(
                current, sheet=bound_sheet(current.sheet, ready_context["spell_card"])
            )
            if not ready_context.get("validate_only"):
                encounter = ready_context["encounter"]
        ready_fields = (
            {}
            if not ready_context or ready_context.get("validate_only")
            else {
                "released": True,
                "readied_id": ready_context["id"],
                "declaration": _support.deepcopy(declaration or {}),
            }
        )
        spell_entry = (
            _support.magic_item_spell_card(
                current.sheet,
                source_item_id=source_item_id,
                spell_id=spell_id,
            )
            if source_item_id
            else next(
                (
                    item
                    for item in current.sheet.get("content", {}).get("spells", [])
                    if item.get("id") == spell_id
                ),
                None,
            )
        )
        if spell_entry is None:
            raise _support.CombatEngineError("spell is not recorded on the caster card")
        magic_missile = _support.is_core_magic_missile_spell(spell_entry)
        fly = _support.is_core_fly_spell(spell_entry)
        invisibility = _support.is_core_invisibility_spell(spell_entry)
        hypnotic_pattern = _support.is_core_hypnotic_pattern_spell(spell_entry)
        sleep = spell_entry.get(
            "id"
        ) == _support.CORE_SLEEP_SPELL_ID and _support.CORE_SLEEP_MECHANIC_ID in spell_entry.get(
            "mechanic_refs", []
        )
        structured_resolution = (
            (
                _support.deepcopy(dict(spell_entry["resolution"]))
                if source_item_id
                else _support.source_spell_resolution(current.sheet, spell_id)
            )
            if isinstance(spell_entry.get("resolution"), dict)
            else _support.effective_spell_resolution(spell_entry)
        )
        compiled_spell_plan = None
        bound_spell_plan: _support.BoundResolutionPlan | None = None
        standard_spell_agent_ruling: dict[str, Any] | None = None
        if isinstance(spell_entry.get("resolution_plan"), dict):
            if source_item_id:
                raise _support.CombatEngineError(
                    "magic-item spell resolution plans must first be materialized "
                    "onto the actor spell card"
                )
            try:
                _spell_card, compiled_spell_plan = self.character_resolution_plan(
                    current.sheet,
                    spell_id,
                    "spell",
                )
            except (
                _support.CombatEngineError,
                _support.ResolutionPlanCompilationError,
            ) as error:
                raise _support.CombatEngineError(
                    f"recorded spell resolution plan is invalid: {error}"
                ) from error
            if (
                structured_resolution is not None
                or magic_missile
                or fly
                or invisibility
                or hypnotic_pattern
                or sleep
            ):
                raise _support.CombatEngineError(
                    "a spell card cannot combine a semantic plan with another "
                    "effect-settlement path"
                )
            if compiled_spell_plan.trigger != "action":
                raise _support.CombatEngineError(
                    "a combat-cast spell resolution plan must use the action trigger"
                )
        if magic_missile and target_allocations is None:
            raise _support.CombatEngineError(
                "Magic Missile requires target_allocations at cast time"
            )
        if not magic_missile and target_allocations is not None:
            raise _support.CombatEngineError(
                "target_allocations are currently executable only for source-bound Magic Missile"
            )
        agent_positioning = encounter.get("positioning_mode") == "agent"
        if agent_positioning and (magic_missile or "spatial_facts" in dict(declaration or {})):
            self.access.require_campaign(
                campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
        if (
            magic_missile
            and declaration
            and not (agent_positioning and set(declaration) == {"target_spatial_facts"})
        ):
            raise _support.CombatEngineError(
                "Magic Missile uses target_allocations, not declaration"
            )
        if hypnotic_pattern and structured_resolution is not None:
            raise _support.CombatEngineError(
                "Hypnotic Pattern cannot combine its Core mechanic with "
                "structured damage resolution"
            )
        if sleep and structured_resolution is not None:
            raise _support.CombatEngineError(
                "Sleep cannot combine its Core mechanic with damage resolution"
            )
        semantic_plan_commitment: dict[str, Any] | None = None
        if compiled_spell_plan is not None:
            if "agent_resolution_commitment" not in dict(declaration or {}):
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "generic_spell_effect",
                    ),
                    "result": {
                        "spell_id": spell_id,
                        "resolution_plan_contract": _support.resolution_plan_contract(
                            compiled_spell_plan
                        ),
                        "payment_required": True,
                    },
                    "campaign_revision": campaign.revision,
                }
            if set(dict(declaration or {})) != {"agent_resolution_commitment"}:
                raise _support.CombatEngineError(
                    "a planned spell accepts only agent_resolution_commitment"
                )
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
            semantic_plan_commitment, bound_spell_plan = self.validate_agent_resolution_commitment(
                campaign_id,
                dict(declaration or {}).get("agent_resolution_commitment"),
                encounter=encounter,
                source_actor_id=actor_id,
                source_card_id=spell_id,
                source_card_kind="spell",
                compiled_plan=compiled_spell_plan,
            )
            declaration = {"agent_resolution_commitment": semantic_plan_commitment}
        if (
            source_item_id is None
            and structured_resolution is None
            and compiled_spell_plan is None
            and not hypnotic_pattern
            and not sleep
            and str(
                spell_entry.get("effect")
                or dict(spell_entry.get("definition") or {}).get("effect")
                or ""
            ).strip()
            and not fly
            and not invisibility
            and not self.source_card_has_executable_mechanic(
                campaign_id,
                spell_entry,
            )
        ):
            persisted_requirement = self.persisted_standard_spell_ruling_requirement(spell_entry)
            if persisted_requirement is None:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "generic_spell_effect",
                    ),
                    "result": {
                        "spell_id": spell_id,
                        "semantic_solution": self.unresolved_content_solution(
                            spell_entry,
                            source_card_id=spell_id,
                            source_card_kind="spell",
                            character_revision=current.revision,
                        ),
                        "payment_required": False,
                    },
                    "campaign_revision": campaign.revision,
                }
            declared = dict(declaration or {})
            if component_ruling and not declared:
                raise _support.CombatEngineError(
                    "an Agent-adjudicated standard spell effect must be submitted as "
                    'declaration={"agent_ruling": {...}}; component_ruling only '
                    "describes casting-component evidence"
                )
            if not declared:
                source_excerpt = str(persisted_requirement["source_excerpt"])
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "generic_spell_effect",
                    ),
                    "result": {
                        "spell_id": spell_id,
                        "payment_required": True,
                        "agent_ruling_contract": {
                            "submission_parameter": "declaration",
                            "submission_shape": {
                                "agent_ruling": {
                                    "application_id": "<unique stable application id>",
                                    "default_resolver": "agent",
                                    "ruling_kind": "generic_spell_effect",
                                    "decision": "<bounded Agent decision>",
                                    "reason": "<source-grounded reason>",
                                    "source_excerpt": source_excerpt,
                                }
                            },
                            "required_fields": [
                                "application_id",
                                "default_resolver",
                                "ruling_kind",
                                "decision",
                                "reason",
                                "source_excerpt",
                            ],
                            "default_resolver": "agent",
                            "ruling_kind": "generic_spell_effect",
                            "source_excerpt": source_excerpt,
                            "source_card_id": spell_id,
                            "casting_source": {
                                "grant_method": str(
                                    dict(spell_entry.get("grant") or {}).get("method") or ""
                                ),
                                "instruction": (
                                    "for grant_method=innate, omit signature_free_cast so "
                                    "the engine consumes the recorded innate resource; for "
                                    "other grants, signature_free_cast is legal only when the "
                                    "actor card records this spell as a Signature Spell"
                                ),
                            },
                            "rule_refs": [
                                str(item)
                                for item in list(spell_entry.get("rule_refs") or [])
                                if str(item)
                            ],
                        },
                    },
                    "campaign_revision": campaign.revision,
                }
            if set(declared) != {"agent_ruling"}:
                raise _support.CombatEngineError(
                    "an Agent-adjudicated standard spell accepts exactly "
                    'declaration={"agent_ruling": {...}}; do not place ruling fields '
                    "directly under declaration or send them through component_ruling"
                )
            self.access.require_campaign(
                campaign_id,
                principal_id,
                roles=_support.CAMPAIGN_DM_ROLES,
            )
            standard_spell_agent_ruling = self.validate_persisted_standard_spell_ruling(
                declared.get("agent_ruling"),
                source_card=spell_entry,
                requirement=persisted_requirement,
            )
            declaration = {"agent_ruling": standard_spell_agent_ruling}
        structured_target: dict[str, Any] | None = None
        fly_target: dict[str, Any] | None = None
        invisibility_target: dict[str, Any] | None = None
        hypnotic_pattern_target: dict[str, Any] | None = None
        sleep_target: dict[str, Any] | None = None
        if fly:
            if source_item_id is not None or spell_id not in _support.CORE_FLY_SPELL_IDS:
                raise _support.CombatEngineError(
                    "the Core Fly path requires its exact actor spell card"
                )
            if str(encounter.get("ruleset") or "") != "2014":
                raise _support.CombatEngineError("the source-bound Fly mechanic is a 2014 rule")
            declared = dict(declaration or {})
            if set(declared) != {"target_ids", "willing_target_ids"}:
                raise _support.CombatEngineError(
                    "Fly declaration requires target_ids and willing_target_ids"
                )
            if not isinstance(declared.get("target_ids"), list) or not isinstance(
                declared.get("willing_target_ids"), list
            ):
                raise _support.CombatEngineError(
                    "Fly target_ids and willing_target_ids must be lists"
                )
            fly_target_ids = [str(item).strip() for item in list(declared.get("target_ids") or [])]
            fly_willing_ids = [
                str(item).strip() for item in list(declared.get("willing_target_ids") or [])
            ]
            preview_cast_level = int(
                cast_level if cast_level is not None else spell_entry.get("level", 0) or 0
            )
            if (
                not fly_target_ids
                or any(not item for item in fly_target_ids)
                or len(fly_target_ids) != len(set(fly_target_ids))
                or set(fly_willing_ids) != set(fly_target_ids)
                or len(fly_willing_ids) != len(set(fly_willing_ids))
                or len(fly_target_ids) > _support.fly_target_limit(preview_cast_level)
            ):
                raise _support.CombatEngineError(
                    "Fly requires unique willing targets within its cast-level limit"
                )
            combatants = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            caster_combatant = combatants.get(actor_id)
            if caster_combatant is None:
                raise _support.CombatEngineError("Fly caster is not in this encounter")
            contexts: list[dict[str, Any]] = []
            for target_id in fly_target_ids:
                target_combatant = combatants.get(target_id)
                if target_combatant is None:
                    raise _support.CombatEngineError(
                        f"Fly target is not in this encounter: {target_id}"
                    )
                if "dead" in {
                    str(item).casefold() for item in target_combatant.get("conditions", [])
                }:
                    raise _support.CombatEngineError("Fly cannot target a dead creature")
                distance = self.combat_distance(
                    caster_combatant.get("position"),
                    target_combatant.get("position"),
                )
                if distance is None or distance > 5:
                    raise _support.CombatEngineError("every Fly target must be within touch range")
                self.access.require_actor(
                    campaign_id,
                    target_id,
                    principal_id,
                    control=True,
                )
                contexts.append({"target_id": target_id, "distance_ft": distance})
            fly_target = {
                "target_ids": fly_target_ids,
                "willing_target_ids": fly_willing_ids,
                "contexts": contexts,
            }
        if invisibility:
            if source_item_id is not None or spell_id not in _support.CORE_INVISIBILITY_SPELL_IDS:
                raise _support.CombatEngineError(
                    "the Core Invisibility path requires its exact actor spell card"
                )
            if str(encounter.get("ruleset") or "") != "2014":
                raise _support.CombatEngineError(
                    "the source-bound Invisibility mechanic is a 2014 rule"
                )
            declared = dict(declaration or {})
            if set(declared) != {"target_ids"}:
                raise _support.CombatEngineError(
                    "Invisibility declaration requires only target_ids"
                )
            if not isinstance(declared.get("target_ids"), list):
                raise _support.CombatEngineError("Invisibility target_ids must be a list")
            invisibility_target_ids = [
                str(item).strip() for item in list(declared.get("target_ids") or [])
            ]
            preview_cast_level = int(
                cast_level if cast_level is not None else spell_entry.get("level", 0) or 0
            )
            if (
                not invisibility_target_ids
                or any(not item for item in invisibility_target_ids)
                or len(invisibility_target_ids) != len(set(invisibility_target_ids))
                or len(invisibility_target_ids)
                > _support.invisibility_target_limit(preview_cast_level)
            ):
                raise _support.CombatEngineError(
                    "Invisibility requires unique targets within its cast-level limit"
                )
            combatants = {
                str(item.get("actor_id") or ""): item for item in encounter.get("combatants", [])
            }
            caster_combatant = combatants.get(actor_id)
            if caster_combatant is None:
                raise _support.CombatEngineError("Invisibility caster is not in this encounter")
            contexts: list[dict[str, Any]] = []
            for target_id in invisibility_target_ids:
                target_combatant = combatants.get(target_id)
                if target_combatant is None:
                    raise _support.CombatEngineError(
                        f"Invisibility target is not in this encounter: {target_id}"
                    )
                if "dead" in {
                    str(item).casefold() for item in target_combatant.get("conditions", [])
                }:
                    raise _support.CombatEngineError("Invisibility cannot target a dead creature")
                distance = self.combat_distance(
                    caster_combatant.get("position"),
                    target_combatant.get("position"),
                )
                if distance is None or distance > 5:
                    raise _support.CombatEngineError(
                        "every Invisibility target must be within touch range"
                    )
                target_record = self.characters.get(target_id)
                if target_record.campaign_id != campaign_id:
                    raise _support.CombatEngineError(
                        "every Invisibility target must belong to the caster's campaign"
                    )
                contexts.append({"target_id": target_id, "distance_ft": distance})
            invisibility_target = {
                "target_ids": invisibility_target_ids,
                "contexts": contexts,
            }
        if hypnotic_pattern:
            if source_item_id is not None:
                raise _support.CombatEngineError(
                    "the Core Hypnotic Pattern path currently requires its "
                    "source-bound actor spell card"
                )
            if str(encounter.get("ruleset") or "") != "2014":
                raise _support.CombatEngineError(
                    "the source-bound Hypnotic Pattern mechanic is a 2014 rule"
                )
            hypnotic_pattern_target = self.normalize_hypnotic_pattern_declaration(
                encounter,
                caster_id=actor_id,
                spell=spell_entry,
                declaration=declaration,
            )
        if sleep:
            if str(encounter.get("ruleset") or "") != "2014":
                raise _support.CombatEngineError("the source-bound Sleep mechanic is a 2014 rule")
            if str(encounter.get("positioning_mode") or "grid") == "agent":
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                sleep_target = _support._normalize_sleep_spatial_facts(
                    declaration,
                    actor_ids={
                        str(item["actor_id"])
                        for item in encounter.get("combatants", [])
                        if "dead"
                        not in {str(value).casefold() for value in item.get("conditions", [])}
                    },
                    campaign_revision=campaign.revision,
                )
            else:
                sleep_target = self.normalize_area_declaration(
                    encounter,
                    source_id=actor_id,
                    area={"shape": "sphere", "radius_ft": 20},
                    origin_range_ft=90,
                    declaration=declaration,
                )
        if structured_resolution is not None:
            kind = str(structured_resolution.get("kind") or "")
            if kind == "spell_attack":
                if ready_context:
                    from .readied_spells import validate_attacks

                    validate_attacks(
                        self,
                        campaign_id,
                        encounter,
                        actor_id,
                        current.sheet,
                        spell_entry,
                        structured_resolution,
                        int(ready_context["applied"]["cast_level"]),
                        declaration,
                        principal_id,
                    )
                elif declaration:
                    raise _support.CombatEngineError(
                        "spell attack targets are selected one attack at a time after casting"
                    )
            elif kind == "healing":
                structured_target = self.normalize_single_target_declaration(
                    encounter,
                    caster_id=actor_id,
                    spell=spell_entry,
                    resolution=structured_resolution,
                    declaration=declaration,
                )
            elif kind == "saving_throw":
                save = dict(structured_resolution.get("save") or {})
                if dict(structured_resolution.get("targeting") or {}).get("mode") == "area":
                    spell_area = dict(
                        dict(structured_resolution.get("targeting") or {}).get("area") or {}
                    )
                    spell_range = dict(dict(spell_entry.get("definition") or {}).get("range") or {})
                    structured_target = self.normalize_area_declaration(
                        encounter,
                        source_id=actor_id,
                        area=spell_area,
                        declaration=declaration,
                        origin_range_ft=int(spell_range.get("normal_ft", 0) or 0),
                    )
                else:
                    structured_target = self.normalize_single_target_declaration(
                        encounter,
                        caster_id=actor_id,
                        spell=spell_entry,
                        resolution=structured_resolution,
                        declaration=declaration,
                        cover_required=(
                            str(save.get("ability") or "") == "dexterity"
                            and not bool(save.get("ignores_cover"))
                        ),
                    )
        harmful_target_ids: list[str] = []
        if magic_missile:
            harmful_target_ids.extend(
                str(allocation.get("target_id") or "")
                for allocation in target_allocations or []
                if isinstance(allocation, dict)
            )
        elif (
            structured_resolution is not None
            and str(structured_resolution.get("kind") or "") == "saving_throw"
            and structured_target is not None
            and bool(dict(structured_resolution.get("save") or {}).get("damage"))
        ):
            target_contexts = (
                list(structured_target["targets"])
                if "targets" in structured_target
                else [structured_target]
            )
            harmful_target_ids.extend(
                str(context.get("target_id") or "") for context in target_contexts
            )
        elif hypnotic_pattern and hypnotic_pattern_target is not None:
            harmful_target_ids.extend(
                str(context.get("target_id") or "")
                for context in hypnotic_pattern_target.get("targets", [])
                if isinstance(context, dict)
            )
        elif sleep and sleep_target is not None:
            harmful_target_ids.extend(
                str(context.get("target_id") or "")
                for context in sleep_target.get("targets", [])
                if isinstance(context, dict)
            )
        if bound_spell_plan is not None:
            harmful_target_ids.extend(self.semantic_plan_harmful_target_ids(bound_spell_plan))
        # Harmful spell targets are cleared before the slot or charge is spent.
        _support.require_harmful_targeting_allowed(
            self.combat_actor_snapshot(actor_id),
            target_ids=harmful_target_ids,
            known_actor_ids=self.encounter_actor_ids(encounter),
        )
        if ready_context:
            applied = _support.deepcopy(ready_context["applied"])
            rules = self.effective_rule_context(
                campaign_id,
                facts={
                    "actor_id": actor_id,
                    "spell_id": spell_id,
                    "cast_level": cast_level,
                    "readied_release": True,
                },
            )
        else:
            from .spell_components import preflight

            component_check = preflight(
                self,
                campaign_id=campaign_id,
                principal_id=principal_id,
                sheet=current.sheet,
                spell=spell_entry,
                component_ruling=component_ruling,
                feature_cast_source=feature_cast_source,
                source_item_id=source_item_id,
            )
            perception_spell = _support.deepcopy(spell_entry)
            if component_check.get("status") == "satisfied":
                perception_spell.setdefault("definition", {})["components"] = component_check[
                    "required"
                ]
                perception_spell["custom_definition"] = {
                    key: value
                    for key, value in dict(perception_spell.get("custom_definition") or {}).items()
                    if key != "component_details"
                }
            visibility_preview = _support.deepcopy(encounter)
            self.apply_cast_visibility_ruling(
                visibility_preview,
                campaign_id,
                actor_id,
                perception_spell,
                component_ruling,
                principal_id,
            )
            rules = self.effective_rule_context(
                campaign_id,
                facts={
                    "actor_id": actor_id,
                    "spell_id": spell_id,
                    "cast_level": cast_level,
                    "source_item_id": source_item_id,
                    "feature_cast_source": feature_cast_source,
                },
            )
            applied = (
                _support.consume_magic_item_spell_cast(
                    current.sheet,
                    source_item_id=source_item_id,
                    spell_id=spell_id,
                    cast_level=cast_level,
                    ritual=ritual,
                    rules=rules,
                    component_ruling=component_ruling,
                )
                if source_item_id
                else _support.consume_spell_cast(
                    current.sheet,
                    spell_id=spell_id,
                    cast_level=cast_level,
                    ritual=ritual,
                    signature_free_cast=signature_free_cast,
                    feature_cast_source=feature_cast_source,
                    component_ruling=component_ruling,
                    rules=rules,
                )
            )
            applied = self.settle_magic_item_last_charge(
                applied,
                source_item_id=source_item_id,
                rules=rules,
            )
        if applied.get("status") in _support.PENDING_RULE_RESULT_STATUSES:
            return {
                **_support._ruling_status(
                    applied["status"],
                    _support._pending_result_ruling_kind(applied),
                ),
                "result": {key: value for key, value in applied.items() if key != "sheet"},
                "campaign_revision": campaign.revision,
            }
        casting_time = str(spell_entry.get("definition", {}).get("casting_time") or "1 action")
        normalized_casting_time = casting_time.casefold().strip()
        if ritual:
            raise _support.CombatEngineError(
                "ritual casting cannot be completed inside an active encounter"
            )
        if normalized_casting_time.startswith(("bonus action", "1 bonus action")):
            payment = "bonus_action"
        elif normalized_casting_time.startswith(("reaction", "1 reaction")):
            payment = "reaction"
        elif normalized_casting_time == "action" or normalized_casting_time.startswith("1 action"):
            payment = "main_action"
        else:
            raise _support.NeedsRulingError(
                "this spell's casting time requires an explicit out-of-combat time ruling",
                missing=("casting_time",),
                ruling_kind="source_or_scene_fact",
            )
        if not ready_context:
            if payment == "reaction":
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
                        "a reaction spell requires its owned pending reaction choice_id"
                    )
                if any(
                    item.get("status", "pending") == "pending" and item.get("id") != choice_id
                    for item in encounter.get("pending", [])
                ):
                    raise _support.CombatEngineError(
                        "resolve the earlier pending save or choice first"
                    )
            else:
                self.require_no_blocking_pending(encounter)

        normalized_allocations: list[dict[str, Any]] | None = None
        if magic_missile:
            normalized_allocations = self.validate_magic_missile_targets(
                encounter,
                caster_id=actor_id,
                allocations=list(target_allocations or []),
                cast_level=int(applied.get("cast_level", cast_level or 1) or 1),
                target_spatial_facts=dict(declaration or {}).get("target_spatial_facts"),
            )

        if ready_context and ready_context.get("validate_only"):
            return {"status": "ready_validated"}
        spell_level = int(spell_entry.get("level", 0) or 0)
        if ready_context:
            next_encounter = _support.deepcopy(encounter)
            self.require_no_blocking_pending(next_encounter)
        else:
            spell_level = int(spell_entry.get("level", 0) or 0)
            spent_slot = applied["payment"].get("economy") in _support.SLOT_PAYMENT_ECONOMIES
            self.require_combat_spell_turn_legal(
                encounter,
                actor_id=actor_id,
                payment=payment,
                spell_level=spell_level,
                casting_time=normalized_casting_time,
                spent_slot=spent_slot,
            )
            next_encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="cast",
                payload={
                    "spell_id": spell_id,
                    "cast_level": cast_level,
                    "ritual": ritual,
                    "source_item_id": source_item_id,
                    **(
                        {"agent_resolution_commitment": (semantic_plan_commitment)}
                        if semantic_plan_commitment is not None
                        else {}
                    ),
                    **(
                        {"agent_ruling": _support.deepcopy(standard_spell_agent_ruling)}
                        if standard_spell_agent_ruling is not None
                        else {}
                    ),
                },
                payment=payment,
            )
            cast_ended_tethers = _support.newly_ended_witch_bolt_tethers(
                encounter,
                next_encounter,
                source_actor_id=actor_id,
            )
            if cast_ended_tethers:
                applied["sheet"] = _support.end_tether_concentrations(
                    applied["sheet"],
                    cast_ended_tethers,
                )["sheet"]
            self.apply_cast_visibility_ruling(
                next_encounter,
                campaign_id,
                actor_id,
                perception_spell,
                component_ruling,
                principal_id,
            )
            if payment == "reaction":
                assert choice_id is not None
                next_encounter = _support.resolve_choice_window(
                    next_encounter,
                    choice_id=choice_id,
                    actor_id_value=actor_id,
                    selection={"id": spell_id, "kind": "reaction_spell"},
                )
            self.record_combat_spell_cast(
                next_encounter,
                actor_id=actor_id,
                spell_id=spell_id,
                spell_level=spell_level,
                payment=payment,
                casting_time=normalized_casting_time,
                spent_slot=spent_slot,
                source_item_id=source_item_id,
            )
        resolved_cast_level = int(applied.get("cast_level", cast_level or spell_level) or 0)
        if fly:
            assert fly_target is not None
            concentration_effect = next(
                (
                    effect
                    for effect in applied["sheet"].get("effects", [])
                    if effect.get("active")
                    and effect.get("concentration")
                    and str(effect.get("source_spell_id") or "") in _support.CORE_FLY_SPELL_IDS
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Fly did not create its required concentration effect"
                )
            fly_sheets = {
                target_id: _support.deepcopy(self.characters.get(target_id).sheet)
                for target_id in fly_target["target_ids"]
            }
            fly_sheets[actor_id] = _support.deepcopy(applied["sheet"])
            applied_fly = _support.apply_core_fly_effects(
                fly_sheets,
                caster_id=actor_id,
                target_ids=fly_target["target_ids"],
                willing_target_ids=fly_target["willing_target_ids"],
                spell_id=spell_id,
                cast_level=resolved_cast_level,
                concentration_effect_id=str(concentration_effect["id"]),
            )
            final_sheets = applied_fly["sheets"]
            dependent_effects = list(next_encounter.get("dependent_effects") or [])
            dependencies: list[dict[str, Any]] = []
            for target_id in applied_fly["target_ids"]:
                dependency = {
                    "id": f"effect-dependency-{_support.uuid4().hex}",
                    "mechanic_id": _support.CORE_FLY_MECHANIC_ID,
                    "dependency": "source_effect_active",
                    "source_actor_id": actor_id,
                    "source_effect_id": str(concentration_effect["id"]),
                    "target_actor_id": target_id,
                    "target_effect_id": applied_fly["effect_ids"][target_id],
                    "active": True,
                }
                dependent_effects.append(dependency)
                dependencies.append(dependency)
                self.sync_combatant_conditions(
                    next_encounter,
                    target_id,
                    final_sheets[target_id],
                )
            next_encounter["dependent_effects"] = dependent_effects
            result = {
                "kind": "fly",
                "spell_id": spell_id,
                "cast_level": resolved_cast_level,
                "target_limit": applied_fly["target_limit"],
                "targets": [
                    {
                        **context,
                        "effect_id": applied_fly["effect_ids"][context["target_id"]],
                        "flying_speed_ft": 60,
                    }
                    for context in fly_target["contexts"]
                ],
                "concentration_effect_id": str(concentration_effect["id"]),
                "dependencies": dependencies,
                "payment": _support.deepcopy(applied.get("payment") or {}),
                "component_receipt": _support.deepcopy(applied.get("component_receipt")),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "fly",
                    "actor_id": actor_id,
                    "result": _support.deepcopy(result),
                },
            ][-100:]
            next_state = {
                **dict(campaign.state or {}),
                "combat": next_encounter,
            }
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.fly",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    **ready_fields,
                    "status": "committed",
                    "result": result,
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_actor_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_actor_id).notes
                        ),
                        expected_revision=self.characters.get(target_actor_id).revision,
                    )
                    for target_actor_id, sheet in final_sheets.items()
                ],
                rule_receipts=[
                    *list(applied.get("rule_receipts") or []),
                    *_support.core_receipts(
                        self.effective_rule_context(campaign_id),
                        [
                            _support.CORE_FLY_MECHANIC_ID,
                            "dnd5e.core.mcp.combat_spell_boundary",
                        ],
                        "combat.spell.fly",
                    ),
                ],
            )
            return self.combat_response(campaign_id, principal_id, response)
        if invisibility:
            assert invisibility_target is not None
            concentration_effect = next(
                (
                    effect
                    for effect in applied["sheet"].get("effects", [])
                    if effect.get("active")
                    and effect.get("concentration")
                    and str(effect.get("source_spell_id") or "")
                    in _support.CORE_INVISIBILITY_SPELL_IDS
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Invisibility did not create its required concentration effect"
                )
            invisibility_sheets = {
                target_id: _support.deepcopy(self.characters.get(target_id).sheet)
                for target_id in invisibility_target["target_ids"]
            }
            invisibility_sheets[actor_id] = _support.deepcopy(applied["sheet"])
            applied_invisibility = _support.apply_core_invisibility_effects(
                invisibility_sheets,
                caster_id=actor_id,
                target_ids=invisibility_target["target_ids"],
                spell_id=spell_id,
                cast_level=resolved_cast_level,
                concentration_effect_id=str(concentration_effect["id"]),
            )
            final_sheets = applied_invisibility["sheets"]
            dependent_effects = list(next_encounter.get("dependent_effects") or [])
            dependencies: list[dict[str, Any]] = []
            for target_id in applied_invisibility["target_ids"]:
                dependency = {
                    "id": f"effect-dependency-{_support.uuid4().hex}",
                    "mechanic_id": _support.CORE_INVISIBILITY_MECHANIC_ID,
                    "dependency": "source_effect_active",
                    "source_actor_id": actor_id,
                    "source_effect_id": str(concentration_effect["id"]),
                    "target_actor_id": target_id,
                    "target_effect_id": applied_invisibility["effect_ids"][target_id],
                    "active": True,
                }
                dependent_effects.append(dependency)
                dependencies.append(dependency)
                self.sync_combatant_conditions(
                    next_encounter,
                    target_id,
                    final_sheets[target_id],
                )
            next_encounter["dependent_effects"] = dependent_effects
            result = {
                "kind": "invisibility",
                "spell_id": spell_id,
                "cast_level": resolved_cast_level,
                "target_limit": applied_invisibility["target_limit"],
                "targets": [
                    {
                        **context,
                        "effect_id": applied_invisibility["effect_ids"][context["target_id"]],
                        "condition": "invisible",
                    }
                    for context in invisibility_target["contexts"]
                ],
                "concentration_effect_id": str(concentration_effect["id"]),
                "dependencies": dependencies,
                "payment": _support.deepcopy(applied.get("payment") or {}),
                "component_receipt": _support.deepcopy(applied.get("component_receipt")),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "invisibility",
                    "actor_id": actor_id,
                    "result": _support.deepcopy(result),
                },
            ][-100:]
            next_state = {
                **dict(campaign.state or {}),
                "combat": next_encounter,
            }
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.invisibility",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    **ready_fields,
                    "status": "committed",
                    "result": result,
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_actor_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_actor_id).notes
                        ),
                        expected_revision=self.characters.get(target_actor_id).revision,
                    )
                    for target_actor_id, sheet in final_sheets.items()
                ],
                rule_receipts=[
                    *list(applied.get("rule_receipts") or []),
                    *_support.core_receipts(
                        self.effective_rule_context(campaign_id),
                        [
                            _support.CORE_INVISIBILITY_MECHANIC_ID,
                            "dnd5e.core.mcp.combat_spell_boundary",
                        ],
                        "combat.spell.invisibility",
                    ),
                ],
            )
            return self.combat_response(campaign_id, principal_id, response)
        if sleep:
            assert sleep_target is not None
            if not 1 <= resolved_cast_level <= 9:
                raise _support.CombatEngineError("Sleep cast level must be between 1 and 9")
            pool_roll = _support.asdict(_support.roll(f"{5 + 2 * (resolved_cast_level - 1)}d8"))
            final_sheets: dict[str, dict[str, Any]] = {
                actor_id: _support.deepcopy(applied["sheet"])
            }
            targets: list[dict[str, Any]] = []
            for target_context in sleep_target["targets"]:
                target_id = str(target_context["target_id"])
                target_actor = self.combat_actor_snapshot(target_id)
                if target_id == actor_id:
                    target_actor["sheet"] = _support.deepcopy(applied["sheet"])
                targets.append(target_actor)
            settled_sleep = _support.resolve_sleep_targets(
                targets,
                pool=int(pool_roll["total"]),
                source_actor_id=actor_id,
                source_spell_id=spell_id,
                source_rule_refs=spell_entry.get("rule_refs", []),
                ruleset="2014",
            )
            final_sheets.update(settled_sleep["sheets"])
            for target_id, target_sheet in final_sheets.items():
                self.sync_combatant_conditions(next_encounter, target_id, target_sheet)
                _support.reconcile_readied_spells(next_encounter, target_id, target_sheet)
                self.reconcile_actor_witch_bolt_concentration(
                    next_encounter, target_id, target_sheet
                )
            resolution_receipts = [
                *list(applied.get("rule_receipts") or []),
                *_support.core_receipts(
                    rules,
                    [_support.CORE_SLEEP_MECHANIC_ID, "dnd5e.core.mcp.combat_spell_boundary"],
                    "combat.spell.sleep",
                ),
            ]
            for target_result in settled_sleep["targets"]:
                if target_result["skip_reason"] == "immune_to_magical_sleep":
                    target_result["rule_receipts"] = _support.core_receipts(
                        rules,
                        [_support.CORE_FEY_ANCESTRY_MECHANIC_ID],
                        "combat.spell.sleep.immunity",
                    )
                    resolution_receipts.extend(target_result["rule_receipts"])
            result = {
                "kind": "sleep",
                "spell_id": spell_id,
                "cast_level": resolved_cast_level,
                "pool_roll": pool_roll,
                "pool_remaining": settled_sleep["pool_remaining"],
                "area": sleep_target,
                "targets": settled_sleep["targets"],
                "payment": _support.deepcopy(applied.get("payment") or {}),
                "component_receipt": _support.deepcopy(applied.get("component_receipt")),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {"type": "sleep", "actor_id": actor_id, "result": _support.deepcopy(result)},
            ][-100:]
            response = self.commit_campaign_state(
                campaign,
                {**dict(campaign.state or {}), "combat": next_encounter},
                operation="combat.spell.sleep",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={"status": "committed", "result": result, "combat": next_encounter},
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=_support.validate_character_sheet(target_sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_id).notes
                        ),
                        expected_revision=self.characters.get(target_id).revision,
                    )
                    for target_id, target_sheet in final_sheets.items()
                    if target_sheet != self.characters.get(target_id).sheet
                ],
                rule_receipts=resolution_receipts,
            )
            return self.combat_response(campaign_id, principal_id, response)
        if hypnotic_pattern:
            assert hypnotic_pattern_target is not None
            derived_caster = self.derive_character_sheet(applied["sheet"], character_id=actor_id)
            save_dc = dict(derived_caster.get("spellcasting") or {}).get("save_dc")
            if save_dc is None:
                raise _support.CombatEngineError(
                    "Hypnotic Pattern save DC is not derivable from the caster card"
                )
            concentration_effect = next(
                (
                    effect
                    for effect in applied["sheet"].get("effects", [])
                    if effect.get("active")
                    and effect.get("concentration")
                    and str(effect.get("source_spell_id") or "")
                    in _support.CORE_HYPNOTIC_PATTERN_SPELL_IDS
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Hypnotic Pattern did not create its required concentration effect"
                )
            final_sheets: dict[str, dict[str, Any]] = {
                actor_id: _support.deepcopy(applied["sheet"])
            }
            target_results: list[dict[str, Any]] = []
            resolution_receipts = [
                *list(applied.get("rule_receipts") or []),
                *_support.core_receipts(
                    self.effective_rule_context(campaign_id),
                    [
                        _support.CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
                        "dnd5e.core.mcp.combat_spell_boundary",
                    ],
                    "combat.spell.hypnotic_pattern",
                ),
            ]
            dependent_effects = list(next_encounter.get("dependent_effects") or [])
            for target_context in sorted(
                hypnotic_pattern_target["targets"],
                key=lambda item: (
                    str(item.get("target_id") or "") != actor_id,
                    str(item.get("target_id") or ""),
                ),
            ):
                target_id = str(target_context["target_id"])
                target_record = self.characters.get(target_id)
                target_sheet = _support.deepcopy(final_sheets.get(target_id, target_record.sheet))
                target_actor = self.combat_actor_snapshot(target_id)
                target_actor["sheet"] = target_sheet
                target_actor["derived"] = self.derive_character_sheet(
                    target_sheet, character_id=target_id
                )
                resolved_target = _support.resolve_hypnotic_pattern_target(
                    target_actor,
                    caster_id=actor_id,
                    spell_id=spell_id,
                    save_dc=int(save_dc),
                    rules=self.effective_rule_context(
                        campaign_id,
                        facts={
                            "actor_id": target_id,
                            "caster_id": actor_id,
                            "spell_id": spell_id,
                            "kind": "hypnotic_pattern_save",
                        },
                    ),
                )
                final_sheets[target_id] = resolved_target["sheet"]
                target_result = {
                    **resolved_target["result"],
                    "context": _support.deepcopy(target_context),
                }
                save = target_result.get("save")
                if isinstance(save, dict):
                    resolution_receipts.extend(save.get("rule_receipts") or [])
                effect_id = str(target_result.get("effect_id") or "")
                if effect_id:
                    dependency_id = f"effect-dependency-{_support.uuid4().hex}"
                    dependent_effects.append(
                        {
                            "id": dependency_id,
                            "mechanic_id": (_support.CORE_HYPNOTIC_PATTERN_MECHANIC_ID),
                            "dependency": "source_effect_active",
                            "source_actor_id": actor_id,
                            "source_effect_id": str(concentration_effect["id"]),
                            "target_actor_id": target_id,
                            "target_effect_id": effect_id,
                            "active": True,
                        }
                    )
                    target_result["dependency_id"] = dependency_id
                    target_result["effect_active_after_commit"] = True
                target_results.append(target_result)
                self.sync_combatant_conditions(
                    next_encounter,
                    target_id,
                    resolved_target["sheet"],
                )
            next_encounter["dependent_effects"] = dependent_effects
            result = {
                "kind": "hypnotic_pattern",
                "spell_id": spell_id,
                "cast_level": resolved_cast_level,
                "save_dc": int(save_dc),
                "area": hypnotic_pattern_target,
                "targets": target_results,
                "concentration_effect_id": str(concentration_effect["id"]),
                "payment": _support.deepcopy(applied.get("payment") or {}),
                "component_receipt": _support.deepcopy(applied.get("component_receipt")),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "hypnotic_pattern",
                    "actor_id": actor_id,
                    "result": _support.deepcopy(result),
                },
            ][-100:]
            next_state = {
                **dict(campaign.state or {}),
                "combat": next_encounter,
            }
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.hypnotic_pattern",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    **ready_fields,
                    "status": "committed",
                    "result": result,
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_actor_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_actor_id).notes
                        ),
                        expected_revision=self.characters.get(target_actor_id).revision,
                    )
                    for target_actor_id, sheet in final_sheets.items()
                ],
                rule_receipts=resolution_receipts,
            )
            return self.combat_response(campaign_id, principal_id, response)
        if structured_resolution is not None:
            structured_kind = str(structured_resolution.get("kind") or "")
            structured_receipts = [
                *list(applied.get("rule_receipts") or []),
                *_support.core_receipts(
                    self.effective_rule_context(campaign_id),
                    [
                        _support.SPELL_RESOLUTION_MECHANIC_ID,
                        "dnd5e.core.mcp.combat_spell_boundary",
                    ],
                    f"combat.spell.{structured_kind}",
                ),
            ]
            self.sync_combatant_conditions(next_encounter, actor_id, applied["sheet"])
            if structured_kind == "spell_attack":
                total_attacks = _support.spell_attack_count(
                    structured_resolution, cast_level=resolved_cast_level
                )
                resolution_id = f"spell-resolution-{_support.uuid4().hex}"
                if ready_context:
                    resolution_id = (
                        "spell-resolution-"
                        + _support.hashlib.sha256(
                            f"{campaign_id}:{resolved_branch_id}:{ready_context['id']}:{idempotency_key}".encode(
                                "utf-8"
                            )
                        ).hexdigest()[:32]
                    )
                resolution = {
                    "id": resolution_id,
                    "kind": "spell_attack",
                    "caster_id": actor_id,
                    "spell_id": spell_id,
                    "cast_level": resolved_cast_level,
                    "total_attacks": total_attacks,
                    "remaining_attacks": total_attacks,
                    "results": [],
                }
                if ready_context:
                    resolution.update(
                        readied_id=ready_context["id"],
                        spell_card=_support.deepcopy(spell_entry),
                        attacks=_support.deepcopy(declaration["attacks"]),
                    )
                resolutions = dict(next_encounter.get("spell_resolutions") or {})
                resolutions[resolution_id] = resolution
                next_encounter["spell_resolutions"] = resolutions
                next_encounter["pending"] = [
                    *list(next_encounter.get("pending") or []),
                    {
                        "id": resolution_id,
                        "kind": "spell_attack_resolution",
                        "status": "pending",
                        "actor_id": actor_id,
                        "spell_id": spell_id,
                        "remaining_attacks": total_attacks,
                    },
                ]
                next_encounter["log"] = [
                    *list(next_encounter.get("log") or []),
                    {
                        "type": "spell_attack_cast",
                        "resolution_id": resolution_id,
                        "actor_id": actor_id,
                        "spell_id": spell_id,
                        "cast_level": resolved_cast_level,
                        "attack_count": total_attacks,
                    },
                ][-100:]
                if ready_context:
                    first = resolution["attacks"][0]
                    return self._settle_combat_attack(
                        campaign_id,
                        actor_id,
                        first["target_id"],
                        action={
                            "spell_resolution_id": resolution_id,
                            "context": _support.deepcopy(first.get("context") or {}),
                        },
                        principal_id=principal_id,
                        branch_id=resolved_branch_id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                        spell_release={
                            "encounter": next_encounter,
                            "sheet": applied["sheet"],
                            "scope": scope,
                            "payload": payload,
                            "fields": ready_fields,
                            "receipts": structured_receipts,
                        },
                    )
                next_state = {**dict(campaign.state or {}), "combat": next_encounter}
                response = self.commit_campaign_state(
                    campaign,
                    next_state,
                    operation="combat.spell.attack.cast",
                    principal_id=principal_id,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    response_fields={
                        **ready_fields,
                        "status": "pending_resolution",
                        "result": {
                            "kind": structured_kind,
                            "resolution_id": resolution_id,
                            "spell_id": spell_id,
                            "cast_level": resolved_cast_level,
                            "attack_count": total_attacks,
                            "remaining_attacks": total_attacks,
                            "payment": _support.deepcopy(applied.get("payment") or {}),
                            "component_receipt": _support.deepcopy(
                                applied.get("component_receipt")
                            ),
                        },
                        "combat": next_encounter,
                    },
                    character_updates=[
                        _support.CharacterStateUpdate(
                            character_id=actor_id,
                            sheet=_support.validate_character_sheet(applied["sheet"]),
                            notes=_support.validate_character_notes(current.notes),
                            expected_revision=current.revision,
                        )
                    ],
                    rule_receipts=structured_receipts,
                )
                return self.combat_response(campaign_id, principal_id, response)

            if structured_kind == "healing":
                assert structured_target is not None
                target_id = str(structured_target["target_id"])
                target_record = self.characters.get(target_id)
                target_sheet = (
                    _support.deepcopy(applied["sheet"])
                    if target_id == actor_id
                    else _support.deepcopy(target_record.sheet)
                )
                healing = dict(structured_resolution.get("healing") or {})
                ability_modifier = 0
                if healing.get("add_spellcasting_modifier"):
                    derived_caster = self.derive_character_sheet(
                        applied["sheet"], character_id=actor_id
                    )
                    ability = str(
                        dict(derived_caster.get("spellcasting") or {}).get("ability") or ""
                    )
                    ability_modifier = int(
                        dict(derived_caster.get("ability_modifiers") or {}).get(ability, 0) or 0
                    )
                expression = _support.scaled_roll_expression(
                    healing,
                    cast_level=resolved_cast_level,
                    actor_level=int(applied["sheet"].get("progression", {}).get("level", 1) or 1),
                    flat_modifier=ability_modifier,
                )
                dice = _support.asdict(_support.roll(expression))
                rolled_amount = max(0, int(dice["total"]))
                self.require_healing_not_prevented(next_encounter, target_id=target_id)
                healed = _support.apply_healing_to_sheet(
                    target_sheet,
                    amount=rolled_amount,
                    source_sheet=applied["sheet"],
                    spell_id=spell_id,
                    spell_level=resolved_cast_level,
                )
                if healed.get("source") is not None:
                    healed["source"]["actor_id"] = actor_id
                final_sheets = {
                    actor_id: _support.deepcopy(applied["sheet"]),
                    target_id: healed["sheet"],
                }
                self.sync_combatant_conditions(next_encounter, target_id, healed["sheet"])
                result = {
                    "kind": structured_kind,
                    "spell_id": spell_id,
                    "cast_level": resolved_cast_level,
                    "target_id": target_id,
                    "amount": int(healed["amount"]),
                    "target": structured_target,
                    "roll": dice,
                    "spellcasting_modifier": ability_modifier,
                    "rolled_amount": rolled_amount,
                    "healing": {key: item for key, item in healed.items() if key != "sheet"},
                    "payment": _support.deepcopy(applied.get("payment") or {}),
                    "component_receipt": _support.deepcopy(applied.get("component_receipt")),
                }
                next_encounter["log"] = [
                    *list(next_encounter.get("log") or []),
                    {"type": "spell_healing", "actor_id": actor_id, "result": result},
                ][-100:]
                next_state = {**dict(campaign.state or {}), "combat": next_encounter}
                response = self.commit_campaign_state(
                    campaign,
                    next_state,
                    operation="combat.spell.healing",
                    principal_id=principal_id,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    response_fields={
                        **ready_fields,
                        "status": "committed",
                        "result": result,
                        "combat": next_encounter,
                    },
                    character_updates=[
                        _support.CharacterStateUpdate(
                            character_id=target_actor_id,
                            sheet=_support.validate_character_sheet(sheet),
                            notes=_support.validate_character_notes(
                                self.characters.get(target_actor_id).notes
                            ),
                            expected_revision=self.characters.get(target_actor_id).revision,
                        )
                        for target_actor_id, sheet in final_sheets.items()
                    ],
                    rule_receipts=structured_receipts,
                )
                return self.combat_response(campaign_id, principal_id, response)

            assert structured_kind == "saving_throw" and structured_target is not None
            save_spec = dict(structured_resolution.get("save") or {})
            damage_spec = dict(save_spec.get("damage") or {})
            damage_expression = _support.scaled_roll_expression(
                damage_spec,
                cast_level=resolved_cast_level,
                actor_level=int(applied["sheet"].get("progression", {}).get("level", 1) or 1),
            )
            damage_roll = _support.asdict(_support.roll(damage_expression))
            derived_caster = self.derive_character_sheet(applied["sheet"], character_id=actor_id)
            save_dc = save_spec.get("save_dc_override")
            if save_dc is None:
                save_dc = dict(derived_caster.get("spellcasting") or {}).get("save_dc")
            if save_dc is None:
                raise _support.CombatEngineError(
                    "spell save DC is not derivable from the caster card"
                )
            target_contexts = (
                list(structured_target["targets"])
                if "targets" in structured_target
                else [structured_target]
            )
            final_sheets: dict[str, dict[str, Any]] = {
                actor_id: _support.deepcopy(applied["sheet"])
            }
            target_results: list[dict[str, Any]] = []
            pending_rulings: list[dict[str, Any]] = []
            resolution_receipts = list(structured_receipts)
            for target_context in target_contexts:
                target_id = str(target_context["target_id"])
                target_record = self.characters.get(target_id)
                target_sheet = _support.deepcopy(final_sheets.get(target_id, target_record.sheet))
                target_actor = self.combat_actor_snapshot(target_id)
                target_actor["sheet"] = target_sheet
                target_actor["derived"] = self.derive_character_sheet(
                    target_sheet, character_id=target_id
                )
                cover_bonus = {
                    "half": 2,
                    "three_quarters": 5,
                }.get(str(target_context.get("cover") or "none"), 0)
                target_rule_context = self.effective_rule_context(
                    campaign_id,
                    facts={
                        "actor_id": target_id,
                        "caster_id": actor_id,
                        "spell_id": spell_id,
                        "kind": "spell_save",
                        **_support._structured_spell_save_facts(spell_entry, structured_resolution),
                    },
                )
                saved = _support.resolve_actor_check(
                    target_actor,
                    kind="save",
                    ability=str(save_spec["ability"]),
                    dc=int(save_dc),
                    encounter=next_encounter,
                    bonus=cover_bonus,
                    save_source_kind="spell",
                    ruleset=str(next_encounter.get("ruleset") or "2014"),
                    rules=target_rule_context,
                )
                resolution_receipts.extend(saved.get("rule_receipts") or [])
                reduction_settlement = _support.standard_save_damage_reduction(
                    target_actor,
                    ability=str(save_spec["ability"]),
                    success=bool(saved["success"]),
                    ordinary_successful_save=str(save_spec["success"]),
                    rules=target_rule_context,
                )
                resolution_receipts.extend(reduction_settlement.get("rule_receipts") or [])
                damage_amount = _support.damage_amount_after_reduction(
                    int(damage_roll["total"]),
                    str(reduction_settlement["damage_reduction"]),
                )
                damage_result: dict[str, Any] | None = None
                if damage_amount > 0:
                    combatant = self.require_encounter_combatant(
                        next_encounter, target_id, role="spell target"
                    )
                    damaged = _support.apply_damage_to_sheet(
                        target_sheet,
                        amount=damage_amount,
                        damage_type=str(damage_spec["damage_type"]),
                        source=spell_id,
                        ruleset=str(next_encounter.get("ruleset") or "2014"),
                        death_saves=self.combatant_zero_hp_buffered(combatant),
                    )
                    final_sheets[target_id] = damaged["sheet"]
                    self.sync_combatant_conditions(next_encounter, target_id, damaged["sheet"])
                    _support.reconcile_readied_spells(next_encounter, target_id, damaged["sheet"])
                    self.add_concentration_window(
                        next_encounter,
                        target_id,
                        damaged.get("concentration"),
                        next_revision=campaign.revision + 1,
                    )
                    damage_result = {key: item for key, item in damaged.items() if key != "sheet"}
                else:
                    final_sheets.setdefault(target_id, target_sheet)
                if not saved["success"] and save_spec.get("on_failed_save_ruling"):
                    pending_rulings.append(
                        {
                            "target_id": target_id,
                            "effect": str(save_spec["on_failed_save_ruling"]),
                            **_support._ruling_resolution_for_kind("generic_spell_effect"),
                        }
                    )
                target_results.append(
                    {
                        "target_id": target_id,
                        "context": target_context,
                        "save": saved,
                        "damage_reduction": str(reduction_settlement["damage_reduction"]),
                        "rule_receipts": list(reduction_settlement.get("rule_receipts") or []),
                        "damage_amount": damage_amount,
                        "damage": damage_result,
                    }
                )
            result = {
                "kind": structured_kind,
                "spell_id": spell_id,
                "cast_level": resolved_cast_level,
                "save_dc": int(save_dc),
                "damage_roll": damage_roll,
                "area": structured_target if "targets" in structured_target else None,
                "targets": target_results,
                "pending_rulings": pending_rulings,
                "payment": _support.deepcopy(applied.get("payment") or {}),
                "component_receipt": _support.deepcopy(applied.get("component_receipt")),
            }
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {"type": "spell_save", "actor_id": actor_id, "result": result},
            ][-100:]
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.save",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    **ready_fields,
                    **_support._ruling_status(
                        "pending_ruling" if pending_rulings else "committed",
                        "generic_spell_effect",
                    ),
                    "result": result,
                    "combat": next_encounter,
                },
                character_updates=[
                    _support.CharacterStateUpdate(
                        character_id=target_actor_id,
                        sheet=_support.validate_character_sheet(sheet),
                        notes=_support.validate_character_notes(
                            self.characters.get(target_actor_id).notes
                        ),
                        expected_revision=self.characters.get(target_actor_id).revision,
                    )
                    for target_actor_id, sheet in final_sheets.items()
                ],
                rule_receipts=resolution_receipts,
            )
            return self.combat_response(campaign_id, principal_id, response)
        if magic_missile:
            assert normalized_allocations is not None
            resolution_id = f"spell-resolution-{_support.uuid4().hex}"
            resolution = {
                "id": resolution_id,
                "kind": "magic_missile",
                "caster_id": actor_id,
                "spell_id": spell_id,
                "cast_level": int(applied.get("cast_level", cast_level or 1) or 1),
                "allocations": _support.deepcopy(normalized_allocations),
                "shielded_target_ids": [],
            }
            defense_windows: list[dict[str, Any]] = []
            for allocation in normalized_allocations:
                target_id = str(allocation["target_id"])
                target_sheet = self.characters.get(target_id).sheet
                if any(
                    effect.get("active") and effect.get("kind") == "spell_shield"
                    for effect in target_sheet.get("effects", [])
                ):
                    resolution["shielded_target_ids"].append(target_id)
                    continue
                candidates = self.magic_missile_shield_defenses(
                    campaign_id, target_id, next_encounter
                )
                if not candidates:
                    continue
                next_encounter = _support.add_choice_window(
                    next_encounter,
                    kind="reaction",
                    actor_id_value=target_id,
                    event="spell.magic_missile.targeted",
                    candidates=[*candidates, {"id": "decline", "name": "Decline"}],
                )
                window = next_encounter["pending"][-1]
                window.update(
                    trigger="magic_missile_targeted",
                    caster_id=actor_id,
                    target_id=target_id,
                    spell_id=spell_id,
                    spell_resolution_id=resolution_id,
                    darts=int(allocation["darts"]),
                )
                defense_windows.append(_support.deepcopy(window))
            self.sync_combatant_conditions(next_encounter, actor_id, applied["sheet"])
            if defense_windows:
                resolutions = dict(next_encounter.get("spell_resolutions") or {})
                resolutions[resolution_id] = resolution
                next_encounter["spell_resolutions"] = resolutions
                next_encounter["log"] = [
                    *list(next_encounter.get("log") or []),
                    {
                        "type": "magic_missile_targeted",
                        "resolution_id": resolution_id,
                        "caster_id": actor_id,
                        "spell_id": spell_id,
                        "allocations": _support.deepcopy(normalized_allocations),
                        "choice_ids": [item["id"] for item in defense_windows],
                    },
                ][-100:]
                next_state = {**dict(campaign.state or {}), "combat": next_encounter}
                response = self.commit_campaign_state(
                    campaign,
                    next_state,
                    operation="combat.spell.magic_missile.target",
                    principal_id=principal_id,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    response_fields={
                        **ready_fields,
                        "status": "pending_reaction",
                        "result": {
                            "kind": "magic_missile",
                            "spell_id": spell_id,
                            "cast_level": resolution["cast_level"],
                            "dart_count": sum(item["darts"] for item in normalized_allocations),
                            "allocations": normalized_allocations,
                            "payment": _support.deepcopy(applied.get("payment") or {}),
                            "component_receipt": _support.deepcopy(
                                applied.get("component_receipt")
                            ),
                        },
                        "choices": defense_windows,
                        "combat": next_encounter,
                    },
                    character_updates=[
                        _support.CharacterStateUpdate(
                            character_id=actor_id,
                            sheet=_support.validate_character_sheet(applied["sheet"]),
                            notes=_support.validate_character_notes(current.notes),
                            expected_revision=current.revision,
                        )
                    ],
                    rule_receipts=[
                        *list(applied.get("rule_receipts") or []),
                        *_support.core_receipts(
                            self.effective_rule_context(campaign_id),
                            [
                                "dnd5e.core.spell.magic_missile_darts",
                                "dnd5e.core.mcp.magic_missile_atomicity",
                            ],
                            "combat.spell.magic_missile.target",
                        ),
                    ],
                )
                return self.combat_response(campaign_id, principal_id, response)
            next_encounter, resolved_sheets, result = self.settle_magic_missile_damage(
                campaign_id,
                next_encounter,
                resolution,
                next_revision=campaign.revision + 1,
                sheet_overrides={actor_id: applied["sheet"]},
            )
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            updates = [
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(sheet),
                    notes=_support.validate_character_notes(self.characters.get(target_id).notes),
                    expected_revision=self.characters.get(target_id).revision,
                )
                for target_id, sheet in resolved_sheets.items()
            ]
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.magic_missile.resolve",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    **ready_fields,
                    "status": "committed",
                    "result": {
                        **result,
                        "payment": _support.deepcopy(applied.get("payment") or {}),
                        "component_receipt": _support.deepcopy(applied.get("component_receipt")),
                    },
                    "combat": next_encounter,
                },
                character_updates=updates,
                rule_receipts=[
                    *list(applied.get("rule_receipts") or []),
                    *_support.core_receipts(
                        self.effective_rule_context(campaign_id),
                        [
                            "dnd5e.core.spell.magic_missile_darts",
                            "dnd5e.core.mcp.magic_missile_atomicity",
                        ],
                        "combat.spell.magic_missile.resolve",
                    ),
                ],
            )
            return self.combat_response(campaign_id, principal_id, response)
        if (
            ready_context
            and not applied.get("automatic_effect")
            and standard_spell_agent_ruling is None
        ):
            return {
                **_support._ruling_status("pending_ruling", "ready_release_effect"),
                "committed": False,
                "released": False,
                "readied": _support.deepcopy(ready_context["record"]),
                "declaration": _support.deepcopy(declaration or {}),
                "campaign_revision": campaign.revision,
            }
        self.sync_combatant_conditions(next_encounter, actor_id, applied["sheet"])
        if compiled_spell_plan is not None:
            applied["semantic_plan"] = {
                "status": "paid",
                "contract": _support.resolution_plan_contract(compiled_spell_plan),
                "commitment": _support.deepcopy(semantic_plan_commitment),
            }
        if standard_spell_agent_ruling is not None:
            applied["semantic_solution"] = {
                "status": "agent_ruling_committed",
                "payment_recorded": True,
                "source_card_id": spell_id,
                "agent_ruling": _support.deepcopy(standard_spell_agent_ruling),
            }
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=("combat.magic_item.spell.cast" if source_item_id else "combat.spell.cast"),
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                **ready_fields,
                **_support._ruling_status(
                    (
                        "committed"
                        if applied.get("automatic_effect")
                        or standard_spell_agent_ruling is not None
                        else "pending_ruling"
                    ),
                    "generic_spell_effect",
                ),
                "result": {key: value for key, value in applied.items() if key != "sheet"},
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=[
                *list(applied.get("rule_receipts") or []),
                *_support.core_receipts(
                    self.effective_rule_context(campaign_id),
                    ["dnd5e.core.mcp.combat_spell_boundary"],
                    "combat.spell.cast",
                ),
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_ready_spell(
        self,
        campaign_id: str,
        actor_id: str,
        spell_id: str,
        trigger: str,
        cast_level: int | None = None,
        declaration: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        component_ruling: dict[str, Any] | None = None,
        target_allocations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Cast and hold a one-action spell, paying its action, slot, and concentration now."""
        self.access.require_actor(campaign_id, actor_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "spell_id": spell_id,
            "trigger": trigger,
            "cast_level": cast_level,
            "component_ruling": component_ruling or {},
            "target_allocations": target_allocations,
            "declaration": declaration or {},
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-ready-spell:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        self.require_no_blocking_pending(encounter)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        current = self.characters.get(actor_id)
        spell_entry = next(
            item
            for item in current.sheet.get("content", {}).get("spells", [])
            if item.get("id") == spell_id
        )
        from .spell_components import preflight

        component_check = preflight(
            self,
            campaign_id=campaign_id,
            principal_id=principal_id,
            sheet=current.sheet,
            spell=spell_entry,
            component_ruling=component_ruling,
        )
        perception_spell = _support.deepcopy(spell_entry)
        if component_check.get("status") == "satisfied":
            perception_spell.setdefault("definition", {})["components"] = component_check[
                "required"
            ]
            perception_spell["custom_definition"] = {
                key: value
                for key, value in dict(perception_spell.get("custom_definition") or {}).items()
                if key != "component_details"
            }
        visibility_preview = _support.deepcopy(encounter)
        self.apply_cast_visibility_ruling(
            visibility_preview,
            campaign_id,
            actor_id,
            perception_spell,
            component_ruling,
            principal_id,
        )
        applied = _support.consume_readied_spell(
            current.sheet,
            spell_id=spell_id,
            cast_level=cast_level,
            component_ruling=component_ruling,
            rules=self.effective_rule_context(campaign_id),
        )
        if applied.get("status") != "committed":
            return {
                **_support._ruling_status(applied["status"], "ready_cast"),
                "committed": False,
                "campaign_revision": campaign.revision,
                "result": {key: item for key, item in applied.items() if key != "sheet"},
            }
        validation = self._settle_combat_spell(
            campaign_id,
            actor_id,
            spell_id,
            cast_level=int(applied["cast_level"]),
            principal_id=principal_id,
            expected_revision=expected_revision,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            target_allocations=target_allocations,
            declaration=declaration,
            ready_context={"validate_only": True, "spell_card": spell_entry, "applied": applied},
        )
        if validation.get("status") != "ready_validated":
            return {**validation, "committed": False}
        spell_level = int(spell_entry.get("level", 0) or 0)
        spent_slot = applied["payment"].get("economy") in _support.SLOT_PAYMENT_ECONOMIES
        self.require_combat_spell_turn_legal(
            encounter,
            actor_id=actor_id,
            payment="main_action",
            spell_level=spell_level,
            casting_time=applied["casting_time"],
            spent_slot=spent_slot,
        )
        next_encounter = _support.resolve_common_action(
            encounter,
            actor_id_value=actor_id,
            action="cast",
            payload={"spell_id": spell_id, "cast_level": cast_level, "readied": True},
            payment="main_action",
        )
        next_encounter = _support.arm_readied_spell(
            next_encounter,
            actor_id_value=actor_id,
            spell_id=spell_id,
            trigger=trigger,
            holding_effect_id=applied["holding_effect_id"],
            release_concentration=applied["release_concentration"],
            release_duration=applied["release_duration"],
            release_effect_kind=applied["release_effect_kind"],
            declaration=declaration,
        )
        readied = next_encounter["readied"][-1]
        readied.update(
            spell_card=_support.deepcopy(spell_entry),
            paid_cast={
                key: _support.deepcopy(item) for key, item in applied.items() if key != "sheet"
            },
            target_allocations=_support.deepcopy(target_allocations),
        )
        self.apply_cast_visibility_ruling(
            next_encounter,
            campaign_id,
            actor_id,
            perception_spell,
            component_ruling,
            principal_id,
        )
        self.record_combat_spell_cast(
            next_encounter,
            actor_id=actor_id,
            spell_id=spell_id,
            spell_level=spell_level,
            payment="main_action",
            casting_time=applied["casting_time"],
            spent_slot=spent_slot,
            readied=True,
        )
        self.sync_combatant_conditions(next_encounter, actor_id, applied["sheet"])
        _support.reconcile_readied_spells(next_encounter, actor_id, applied["sheet"])
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        readied = next_encounter["readied"][-1]
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.spell.ready",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "armed",
                "readied": readied,
                "result": {key: item for key, item in applied.items() if key != "sheet"},
                "combat": next_encounter,
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=_support.validate_character_sheet(applied["sheet"]),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
            rule_receipts=list(applied.get("rule_receipts") or []),
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_readied_spell_trigger(
        self,
        campaign_id: str,
        readied_id: str,
        event: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Confirm that a readied spell's perceivable trigger occurred and open its reaction."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {"readied_id": readied_id, "event": event, "branch_id": resolved_branch_id}
        scope = f"combat-ready-trigger:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        readied = next(
            (item for item in encounter.get("readied", []) if item.get("id") == readied_id),
            None,
        )
        if readied is None:
            raise _support.CombatEngineError("readied spell not found")
        actor = self.characters.get(str(readied["actor_id"]))
        active_effect_ids = {
            str(effect.get("id"))
            for effect in actor.sheet.get("effects", [])
            if effect.get("active") and effect.get("concentration")
        }
        if str(readied.get("holding_effect_id")) not in active_effect_ids:
            next_encounter = _support.deepcopy(encounter)
            expired = _support.reconcile_readied_spells(next_encounter, actor.id, actor.sheet)
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.ready.dissipate",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "dissipated",
                    "readied_spells_expired": expired,
                    "combat": next_encounter,
                },
            )
            return self.combat_response(campaign_id, principal_id, response)
        next_encounter = _support.trigger_readied_spell(
            encounter, readied_id=readied_id, event=event
        )
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.spell.ready.trigger",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "pending",
                "choice": next_encounter["pending"][-1],
                "combat": next_encounter,
            },
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_readied_spell_resolve(
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
        """Release a held spell with a reaction or ignore this occurrence of its trigger."""
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
        scope = f"combat-ready-resolve:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign, encounter = self.active_encounter(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        window = next(
            (item for item in encounter.get("pending", []) if item.get("id") == choice_id),
            None,
        )
        readied = next(
            (
                item
                for item in encounter.get("readied", [])
                if window is not None and item.get("id") == window.get("readied_id")
            ),
            None,
        )
        if readied is None or readied.get("actor_id") != actor_id:
            raise _support.CombatEngineError("choice_id is not this actor's readied spell")
        if declaration is not None and declaration != readied.get("declaration", {}):
            raise _support.CombatEngineError("release cannot replace the stored spell declaration")
        actor = self.characters.get(actor_id)
        sheet = _support.deepcopy(actor.sheet)
        holding_effect = next(
            (
                effect
                for effect in sheet.get("effects", [])
                if effect.get("id") == readied.get("holding_effect_id")
            ),
            None,
        )
        if (
            holding_effect is None
            or not holding_effect.get("active")
            or not holding_effect.get("concentration")
        ):
            next_encounter = _support.deepcopy(encounter)
            expired = _support.reconcile_readied_spells(next_encounter, actor_id, sheet)
            next_state = {**dict(campaign.state or {}), "combat": next_encounter}
            response = self.commit_campaign_state(
                campaign,
                next_state,
                operation="combat.spell.ready.dissipate",
                principal_id=principal_id,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=payload,
                response_fields={
                    "status": "dissipated",
                    "readied_spells_expired": expired,
                    "combat": next_encounter,
                },
            )
            return self.combat_response(campaign_id, principal_id, response)
        if release and choice_id not in {
            str(item.get("id")) for item in _support.available_reactions(encounter, actor_id)
        }:
            raise _support.CombatEngineError("actor cannot take this reaction")
        if release and (not readied.get("spell_card") or not readied.get("paid_cast")):
            return {
                **_support._ruling_status("pending_ruling", "ready_release_effect"),
                "committed": False,
                "released": False,
                "readied": _support.deepcopy(readied),
                "declaration": _support.deepcopy(readied.get("declaration") or {}),
                "campaign_revision": campaign.revision,
            }
        next_encounter, resolved = _support.resolve_readied_spell_window(
            encounter,
            actor_id_value=actor_id,
            choice_id=choice_id,
            release=release,
        )
        updates: list[_support.CharacterStateUpdate] = []
        if release:
            from .readied_spells import release_effects

            active = encounter["combatants"][int(encounter.get("turn_index", 0))]
            applied = release_effects(sheet, resolved, off_turn=active["actor_id"] != actor_id)
            applied["rule_receipts"] = _support.core_receipts(
                self.effective_rule_context(campaign_id),
                ["dnd5e.core.ready.spell_release"],
                "combat.spell.ready.release",
            )
            next_encounter["log"] = [
                *list(next_encounter.get("log") or []),
                {
                    "type": "readied_spell_released",
                    "actor_id": actor_id,
                    "readied_id": resolved["id"],
                    "spell_id": resolved["spell_id"],
                    "declaration": _support.deepcopy(resolved.get("declaration") or {}),
                },
            ][-100:]
            response = self._settle_combat_spell(
                campaign_id,
                actor_id,
                resolved["spell_id"],
                cast_level=int(applied["cast_level"]),
                principal_id=principal_id,
                expected_revision=expected_revision,
                branch_id=resolved_branch_id,
                idempotency_key=idempotency_key,
                declaration=_support.deepcopy(resolved.get("declaration") or {}),
                target_allocations=_support.deepcopy(resolved.get("target_allocations")),
                ready_context={
                    **resolved,
                    "record": resolved,
                    "encounter": next_encounter,
                    "applied": applied,
                    "scope": scope,
                    "payload": payload,
                },
            )
            if not response.get("released"):
                response.update(
                    committed=False,
                    released=False,
                    readied=_support.deepcopy(readied),
                    declaration=_support.deepcopy(readied.get("declaration") or {}),
                )
            return response
        next_encounter["log"] = [
            *list(next_encounter.get("log") or []),
            {
                "type": "readied_spell_released" if release else "readied_spell_declined",
                "actor_id": actor_id,
                "readied_id": resolved.get("id"),
                "spell_id": resolved.get("spell_id"),
                "declaration": _support.deepcopy(resolved.get("declaration") or {}),
            },
        ][-100:]
        next_state = {**dict(campaign.state or {}), "combat": next_encounter}
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation=("combat.spell.ready.release" if release else "combat.spell.ready.decline"),
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
                "spell_id": resolved.get("spell_id"),
                "declaration": _support.deepcopy(resolved.get("declaration") or {}),
                "combat": next_encounter,
            },
            character_updates=updates,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_concentration_check(
        self,
        campaign_id: str,
        target_id: str,
        dc: int,
        effect_ids: list[str],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending concentration save and deactivate effects only on failure."""
        self.access.require_actor(campaign_id, target_id, principal_id, control=True)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "target_id": target_id,
            "dc": dc,
            "effect_ids": list(effect_ids),
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-concentration:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        encounter = dict(campaign.state or {}).get("combat")
        pending = next(
            (
                item
                for item in (encounter or {}).get("pending", [])
                if item.get("kind") == "concentration"
                and item.get("actor_id") == target_id
                and item.get("status") == "pending"
            ),
            None,
        )
        if pending is None:
            raise _support.CombatEngineError("no pending concentration save for this actor")
        if int(pending.get("dc", 0)) != int(dc) or set(pending.get("effect_ids", [])) != set(
            effect_ids
        ):
            raise _support.CombatEngineError(
                "concentration request does not match the pending damage window"
            )
        actor = self.combat_actor_snapshot(target_id)
        result = _support.resolve_actor_check(
            actor,
            kind="save",
            ability="constitution",
            dc=dc,
            rules=self.effective_rule_context(
                campaign_id,
                facts={
                    "actor_id": target_id,
                    "kind": "save",
                    "ability": "constitution",
                    "dc": dc,
                    "save_purpose": "concentration",
                },
                branch_id=resolved_branch_id,
            ),
        )
        updated_sheet = _support.apply_concentration_result(
            actor["sheet"], effect_ids=effect_ids, success=result["success"]
        )
        current = self.characters.get(target_id)
        next_state = dict(campaign.state or {})
        if isinstance(encounter, dict):
            self.reconcile_actor_witch_bolt_concentration(
                encounter,
                target_id,
                updated_sheet,
            )
            _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            active_effect_ids = {
                str(effect.get("id"))
                for effect in updated_sheet.get("effects", [])
                if effect.get("active") and effect.get("concentration")
            }
            encounter["pending"] = [
                item
                for item in encounter.get("pending", [])
                if item.get("id") != pending.get("id")
                and not (
                    item.get("kind") == "concentration"
                    and item.get("actor_id") == target_id
                    and not active_effect_ids.intersection(
                        {str(effect_id) for effect_id in item.get("effect_ids", [])}
                    )
                )
            ]
            encounter["log"] = [
                *list(encounter.get("log") or []),
                {"type": "concentration", "actor_id": target_id, "result": result},
            ][-100:]
            next_state["combat"] = encounter
        response = self.commit_campaign_state(
            campaign,
            next_state,
            operation="combat.concentration.check",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "result": result,
                "effects_active": result["success"],
            },
            character_updates=[
                _support.CharacterStateUpdate(
                    character_id=target_id,
                    sheet=_support.validate_character_sheet(updated_sheet),
                    notes=_support.validate_character_notes(current.notes),
                    expected_revision=current.revision,
                )
            ],
        )
        return self.combat_response(campaign_id, principal_id, response)

    def character_cast_spell(
        self,
        character_id: str,
        spell_id: str,
        cast_level: int | None = None,
        ritual: bool = False,
        signature_free_cast: bool = False,
        feature_cast_source: str | None = None,
        component_ruling: dict[str, Any] | None = None,
        source_item_id: str | None = None,
        target_character_ids: list[str] | None = None,
        willing_target_ids: list[str] | None = None,
        declaration: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Pay canonical spell resources and start concentration from a v2 spell card."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "spell casting")
        if current.campaign_id is None:
            raise ValueError("spell casting requires a campaign-bound character")
        if expected_revision is None or not idempotency_key:
            raise ValueError("expected_revision and idempotency_key are required for spell casting")
        branch_id = self.require_current_branch(current.campaign_id, None)
        payload = {
            "character_id": character_id,
            "spell_id": spell_id,
            "cast_level": cast_level,
            "ritual": ritual,
            "signature_free_cast": signature_free_cast,
            "feature_cast_source": feature_cast_source,
            "component_ruling": component_ruling or {},
            "source_item_id": source_item_id,
            "target_character_ids": target_character_ids,
            "willing_target_ids": willing_target_ids,
            **({"declaration": declaration} if declaration is not None else {}),
        }
        scope = f"character-cast:{current.campaign_id}:{branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if current.revision != expected_revision:
            raise ValueError(
                f"character revision conflict: expected {expected_revision}, "
                f"found {current.revision}"
            )
        if (
            _support.condition_ids(current.sheet.get("conditions"))
            & _support.INCAPACITATING_STATE_IDS
        ):
            raise _support.CombatEngineError("an incapacitated character cannot cast a spell")
        if source_item_id and (signature_free_cast or feature_cast_source):
            raise _support.CombatEngineError(
                "a magic item spell cannot use a character feature casting source"
            )
        campaign = self.campaigns.get(current.campaign_id)
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        self.require_resolved_short_rest_hit_dice(
            current.campaign_id,
            next_state,
            operation="casting a spell that advances campaign time",
        )
        spell_entry = (
            _support.magic_item_spell_card(
                current.sheet,
                source_item_id=source_item_id,
                spell_id=spell_id,
            )
            if source_item_id
            else next(
                (
                    item
                    for item in current.sheet.get("content", {}).get("spells", [])
                    if item.get("id") == spell_id
                ),
                None,
            )
        )
        if spell_entry is None:
            raise _support.CombatEngineError("spell is not recorded on the caster card")
        from .spell_components import preflight

        preflight(
            self, campaign_id=current.campaign_id, principal_id=principal_id,
            sheet=current.sheet, spell=spell_entry, component_ruling=component_ruling,
            feature_cast_source=feature_cast_source, source_item_id=source_item_id,
        )
        fly = _support.is_core_fly_spell(spell_entry)
        invisibility = _support.is_core_invisibility_spell(spell_entry)
        sleep = spell_entry.get(
            "id"
        ) == _support.CORE_SLEEP_SPELL_ID and _support.CORE_SLEEP_MECHANIC_ID in spell_entry.get(
            "mechanic_refs", []
        )
        mending = spell_entry.get(
            "id"
        ) == _support.CORE_MENDING_SPELL_ID and _support.CORE_MENDING_MECHANIC_ID in {
            str(item) for item in spell_entry.get("mechanic_refs", [])
        }
        sleep_target = None
        mending_target_id: str | None = None
        mending_spatial_facts: dict[str, Any] | None = None
        mending_contract: dict[str, str] | None = None
        all_characters = self.characters.list(campaign_id=current.campaign_id)
        if sleep or mending:
            stream = _support.active_random_stream()
            if stream is None:
                stream = _support.CampaignRandomStream.from_campaign_state(
                    current.campaign_id,
                    campaign.state,
                    operation="character_action",
                    idempotency_key=idempotency_key,
                    campaign_revision=campaign.revision,
                )
                with _support.use_random_stream(stream):
                    return self.character_cast_spell(
                        character_id=character_id,
                        spell_id=spell_id,
                        cast_level=cast_level,
                        ritual=ritual,
                        signature_free_cast=signature_free_cast,
                        feature_cast_source=feature_cast_source,
                        component_ruling=component_ruling,
                        source_item_id=source_item_id,
                        target_character_ids=target_character_ids,
                        willing_target_ids=willing_target_ids,
                        declaration=declaration,
                        principal_id=principal_id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                    )
            random_state = _support.validate_random_stream_state(
                dict(campaign.state or {}).get("random_stream")
                or _support.initial_random_stream(f"sagasmith-dnd:{current.campaign_id}")
            )
            if (
                stream.campaign_id != current.campaign_id
                or (
                    stream.campaign_revision is not None
                    and stream.campaign_revision != campaign.revision
                )
                or stream.seed != random_state["seed"]
                or stream.start_position != random_state["position"]
            ):
                raise _support.CombatEngineError(
                    f"{'Sleep' if sleep else 'Mending'} requires the current campaign "
                    "random snapshot"
                )
        if sleep:
            self.access.require_campaign(
                current.campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            if current.sheet.get("edition") != "2014":
                raise _support.CombatEngineError("the source-bound Sleep mechanic is a 2014 rule")
            if spell_entry.get("resolution_plan") or spell_entry.get("resolution"):
                raise _support.CombatEngineError(
                    "Sleep cannot combine native and other settlement paths"
                )
            if target_character_ids is not None or willing_target_ids is not None:
                raise _support.CombatEngineError(
                    "Sleep selects its complete area through declaration"
                )
            sleep_target = _support._normalize_sleep_spatial_facts(
                declaration,
                actor_ids={
                    character.id
                    for character in all_characters
                    if "dead"
                    not in {
                        str(value).casefold() for value in character.sheet.get("conditions", [])
                    }
                },
                campaign_revision=campaign.revision,
            )
        elif mending:
            self.access.require_campaign(
                current.campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            definition = dict(spell_entry.get("definition") or {})
            if str(current.sheet.get("edition") or "") != "2014":
                raise _support.CombatEngineError(
                    "the Steel Defender Mending mechanic is a 2014 rule"
                )
            if (
                int(spell_entry.get("level", -1)) != 0
                or str(definition.get("casting_time") or "").strip().casefold()
                not in {"1 minute", "one minute"}
                or dict(definition.get("range") or {}).get("kind") != "touch"
            ):
                raise _support.CombatEngineError(
                    "Steel Defender Mending requires the exact source casting contract"
                )
            if willing_target_ids is not None:
                raise _support.CombatEngineError(
                    "Steel Defender Mending does not use willing_target_ids"
                )
            normalized_targets = [str(item).strip() for item in list(target_character_ids or [])]
            if len(normalized_targets) != 1 or not normalized_targets[0]:
                raise _support.CombatEngineError(
                    "Steel Defender Mending requires exactly one target"
                )
            mending_target_id = normalized_targets[0]
            target = self.require_campaign_actor(current.campaign_id, mending_target_id)
            contracts = self._steel_defender_turn_contracts(
                current.campaign_id,
                branch_id,
                {character.id for character in all_characters},
            )
            if mending_target_id not in contracts:
                raise _support.CombatEngineError(
                    "Steel Defender Mending requires a verified active defender target"
                )
            mending_contract = contracts[mending_target_id]
            declared = dict(declaration or {})
            if set(declared) != {"spatial_facts"}:
                raise _support.CombatEngineError(
                    "Steel Defender Mending declaration requires only spatial_facts"
                )
            spatial = declared.get("spatial_facts")
            if not isinstance(spatial, dict) or set(spatial) != {
                "distance_ft",
                "default_resolver",
                "ruling_kind",
                "reason",
            }:
                raise _support.CombatEngineError(
                    "Steel Defender Mending requires exact touch spatial_facts"
                )
            distance_ft = spatial.get("distance_ft")
            reason = " ".join(str(spatial.get("reason") or "").split())
            if (
                isinstance(distance_ft, bool)
                or not isinstance(distance_ft, (int, float))
                or not _support.math.isfinite(float(distance_ft))
                or not 0 <= float(distance_ft) <= 5
                or spatial.get("default_resolver") != "agent"
                or spatial.get("ruling_kind") != "agent_dm_adjudication"
                or not reason
                or len(reason) > 500
            ):
                raise _support.CombatEngineError(
                    "Steel Defender Mending requires a bounded touch-range Agent ruling"
                )
            if target.campaign_id != current.campaign_id:
                raise _support.CombatEngineError("Steel Defender Mending target crosses campaigns")
            mending_spatial_facts = {
                **spatial,
                "distance_ft": float(distance_ft),
                "reason": reason,
                "committed": True,
            }
        elif declaration is not None:
            raise _support.CombatEngineError(
                "noncombat spell declaration is supported only for native Sleep or "
                "Steel Defender Mending"
            )
        normalized_fly_targets: list[str] = []
        normalized_willing_targets: list[str] = []
        normalized_invisibility_targets: list[str] = []
        if fly:
            if str(current.sheet.get("edition") or "") != "2014":
                raise _support.CombatEngineError("the source-bound Fly mechanic is a 2014 rule")
            if spell_id not in _support.CORE_FLY_SPELL_IDS:
                raise _support.CombatEngineError(
                    "the Core Fly path requires its exact source-bound SRD spell id"
                )
            normalized_fly_targets = [str(item).strip() for item in (target_character_ids or [])]
            normalized_willing_targets = [str(item).strip() for item in (willing_target_ids or [])]
            resolved_fly_level = int(
                cast_level if cast_level is not None else spell_entry.get("level", 0) or 0
            )
            if (
                not normalized_fly_targets
                or any(not item for item in normalized_fly_targets)
                or len(normalized_fly_targets) != len(set(normalized_fly_targets))
                or set(normalized_willing_targets) != set(normalized_fly_targets)
                or len(normalized_willing_targets) != len(set(normalized_willing_targets))
                or len(normalized_fly_targets) > _support.fly_target_limit(resolved_fly_level)
            ):
                raise _support.CombatEngineError(
                    "Fly requires unique willing targets within its cast-level limit"
                )
            for target_id in normalized_fly_targets:
                target = self.characters.get(target_id)
                if target.campaign_id != current.campaign_id:
                    raise _support.CombatEngineError(
                        "every Fly target must belong to the caster's campaign"
                    )
                self.access.require_actor(
                    current.campaign_id,
                    target_id,
                    principal_id,
                    control=True,
                )
        elif invisibility:
            if source_item_id is not None:
                raise _support.CombatEngineError(
                    "the Core Invisibility path requires its actor spell card"
                )
            if str(current.sheet.get("edition") or "") != "2014":
                raise _support.CombatEngineError(
                    "the source-bound Invisibility mechanic is a 2014 rule"
                )
            if spell_id not in _support.CORE_INVISIBILITY_SPELL_IDS:
                raise _support.CombatEngineError(
                    "the Core Invisibility path requires its exact source-bound SRD spell id"
                )
            if willing_target_ids is not None:
                raise _support.CombatEngineError("Invisibility does not use willing_target_ids")
            normalized_invisibility_targets = [
                str(item).strip() for item in (target_character_ids or [])
            ]
            resolved_invisibility_level = int(
                cast_level if cast_level is not None else spell_entry.get("level", 0) or 0
            )
            if (
                not normalized_invisibility_targets
                or any(not item for item in normalized_invisibility_targets)
                or len(normalized_invisibility_targets) != len(set(normalized_invisibility_targets))
                or len(normalized_invisibility_targets)
                > _support.invisibility_target_limit(resolved_invisibility_level)
            ):
                raise _support.CombatEngineError(
                    "Invisibility requires unique targets within its cast-level limit"
                )
            for target_id in normalized_invisibility_targets:
                target = self.characters.get(target_id)
                if target.campaign_id != current.campaign_id:
                    raise _support.CombatEngineError(
                        "every Invisibility target must belong to the caster's campaign"
                    )
        elif mending:
            pass
        elif target_character_ids is not None or willing_target_ids is not None:
            raise _support.CombatEngineError(
                "target_character_ids and willing_target_ids are currently "
                "reserved for engine-settled target mechanics"
            )
        compiled_spell_plan = None
        if source_item_id is None and isinstance(spell_entry.get("resolution_plan"), dict):
            _spell_card, compiled_spell_plan = self.character_resolution_plan(
                current.sheet,
                spell_id,
                "spell",
            )
        elif (
            source_item_id is None
            and str(
                spell_entry.get("effect")
                or dict(spell_entry.get("definition") or {}).get("effect")
                or ""
            ).strip()
            and not fly
            and not invisibility
            and not mending
            and not self.source_card_has_executable_mechanic(
                current.campaign_id,
                spell_entry,
            )
        ):
            return {
                **_support._ruling_status(
                    "pending_ruling",
                    "generic_spell_effect",
                ),
                "result": {
                    "spell_id": spell_id,
                    "semantic_solution": self.unresolved_content_solution(
                        spell_entry,
                        source_card_id=spell_id,
                        source_card_kind="spell",
                        character_revision=current.revision,
                    ),
                    "payment_required": False,
                },
                "character": self.character_view(current),
                "campaign_revision": campaign.revision,
            }
        elapsed_ticks = self.completed_spell_cast_ticks(spell_entry, ritual=ritual)
        next_state, time_transition = self.advance_state_game_time(
            next_state,
            elapsed_ticks=elapsed_ticks,
        )
        elapsed_minutes = int(time_transition["elapsed_minutes"])
        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": elapsed_ticks},
        )
        next_state = world_duration["state"]
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]
        rules = self.effective_rule_context(
            current.campaign_id,
            facts={
                "actor_id": character_id,
                "spell_id": spell_id,
                "cast_level": cast_level,
                "source_item_id": source_item_id,
                "feature_cast_source": feature_cast_source,
            },
        )
        timed_sheets: dict[str, dict[str, Any]] = {}
        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        rule_receipts: list[dict[str, Any]] = []
        for character in all_characters:
            round_duration = _support.advance_effect_durations(
                character.sheet,
                period="round",
                amount=elapsed_ticks,
            )
            sheet = round_duration["sheet"]
            actor_advanced = list(round_duration["advanced"])
            actor_expired = list(round_duration["expired"])
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
                    rules,
                    actor_id=character.id,
                    period="round",
                    amount=elapsed_ticks,
                    elapsed_ticks=elapsed_ticks,
                    elapsed_minutes=elapsed_minutes,
                ),
            )
            timed_sheets[character.id] = duration_extension.sheet
            rule_receipts.extend(duration_extension.receipts)
            if actor_advanced:
                advanced[character.id] = list(dict.fromkeys(actor_advanced))
            if actor_expired:
                expired[character.id] = list(dict.fromkeys(actor_expired))
        applied = (
            _support.consume_magic_item_spell_cast(
                timed_sheets[current.id],
                source_item_id=source_item_id,
                spell_id=spell_id,
                cast_level=cast_level,
                ritual=ritual,
                rules=rules,
                component_ruling=component_ruling,
            )
            if source_item_id
            else _support.consume_spell_cast(
                timed_sheets[current.id],
                spell_id=spell_id,
                cast_level=cast_level,
                ritual=ritual,
                signature_free_cast=signature_free_cast,
                feature_cast_source=feature_cast_source,
                component_ruling=component_ruling,
                rules=rules,
            )
        )
        applied = self.settle_magic_item_last_charge(
            applied,
            source_item_id=source_item_id,
            rules=rules,
        )
        if applied.get("status") in _support.PENDING_RULE_RESULT_STATUSES:
            pending_result = {key: value for key, value in applied.items() if key != "sheet"}
            if compiled_spell_plan is not None:
                pending_result["resolution_plan_contract"] = _support.resolution_plan_contract(
                    compiled_spell_plan
                )
            return {
                **_support._ruling_status(
                    applied["status"],
                    _support._pending_result_ruling_kind(applied),
                ),
                "result": pending_result,
                "character": self.character_view(current),
            }
        timed_sheets[current.id] = applied["sheet"]
        if fly:
            concentration_effect = next(
                (
                    effect
                    for effect in applied["sheet"].get("effects", [])
                    if effect.get("active")
                    and effect.get("concentration")
                    and str(effect.get("source_spell_id") or "") in _support.CORE_FLY_SPELL_IDS
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Fly did not create its required concentration effect"
                )
            fly_result = _support.apply_core_fly_effects(
                timed_sheets,
                caster_id=current.id,
                target_ids=normalized_fly_targets,
                willing_target_ids=normalized_willing_targets,
                spell_id=spell_id,
                cast_level=int(applied.get("cast_level", cast_level or 3) or 3),
                concentration_effect_id=str(concentration_effect["id"]),
            )
            reconciled = _support.reconcile_source_effect_dependencies(fly_result["sheets"])
            timed_sheets = reconciled["sheets"]
            applied["sheet"] = timed_sheets[current.id]
            applied["automatic_effect"] = "fly"
            applied["effect_ids"] = fly_result["effect_ids"]
            applied["target_ids"] = fly_result["target_ids"]
            applied["target_limit"] = fly_result["target_limit"]
            applied["concentration_effect_id"] = fly_result["concentration_effect_id"]
            applied["ruling_required"] = [
                item
                for item in applied.get("ruling_required") or []
                if item != "targets_and_effect"
            ]
            applied["ruling_requirements"] = [
                item
                for item in applied.get("ruling_requirements") or []
                if item.get("kind") != "targets_and_effect"
            ]
            rule_receipts.extend(
                _support.core_receipts(
                    rules,
                    [_support.CORE_FLY_MECHANIC_ID],
                    "spell.fly",
                )
            )
        if invisibility:
            concentration_effect = next(
                (
                    effect
                    for effect in applied["sheet"].get("effects", [])
                    if effect.get("active")
                    and effect.get("concentration")
                    and str(effect.get("source_spell_id") or "")
                    in _support.CORE_INVISIBILITY_SPELL_IDS
                ),
                None,
            )
            if concentration_effect is None:
                raise _support.CombatEngineError(
                    "Invisibility did not create its required concentration effect"
                )
            invisibility_result = _support.apply_core_invisibility_effects(
                timed_sheets,
                caster_id=current.id,
                target_ids=normalized_invisibility_targets,
                spell_id=spell_id,
                cast_level=int(applied.get("cast_level", cast_level or 2) or 2),
                concentration_effect_id=str(concentration_effect["id"]),
            )
            timed_sheets = invisibility_result["sheets"]
            applied["sheet"] = timed_sheets[current.id]
            applied["automatic_effect"] = "invisibility"
            applied["effect_ids"] = invisibility_result["effect_ids"]
            applied["target_ids"] = invisibility_result["target_ids"]
            applied["target_limit"] = invisibility_result["target_limit"]
            applied["concentration_effect_id"] = invisibility_result["concentration_effect_id"]
            applied["ruling_required"] = [
                item
                for item in applied.get("ruling_required") or []
                if item != "targets_and_effect"
            ]
            applied["ruling_requirements"] = [
                item
                for item in applied.get("ruling_requirements") or []
                if item.get("kind") != "targets_and_effect"
            ]
            rule_receipts.extend(
                _support.core_receipts(
                    rules,
                    [_support.CORE_INVISIBILITY_MECHANIC_ID],
                    "spell.invisibility",
                )
            )
        if mending:
            assert mending_target_id is not None
            assert mending_contract is not None
            try:
                mending_result = _support.mending_steel_defender(timed_sheets[mending_target_id])
            except _support.SteelDefenderError as error:
                raise _support.CombatEngineError(str(error)) from error
            timed_sheets[mending_target_id] = _support.validate_character_sheet(
                mending_result["sheet"]
            )
            applied["sheet"] = timed_sheets[current.id]
            applied["automatic_effect"] = "steel_defender_mending"
            applied["target_id"] = mending_target_id
            applied["roll"] = _support.deepcopy(mending_result["roll"])
            applied["healing"] = _support.deepcopy(mending_result["healing"])
            applied["spatial_facts"] = _support.deepcopy(mending_spatial_facts)
            applied["ruling_required"] = [
                item
                for item in applied.get("ruling_required") or []
                if item != "targets_and_effect"
            ]
            applied["ruling_requirements"] = [
                item
                for item in applied.get("ruling_requirements") or []
                if item.get("kind") != "targets_and_effect"
            ]
            rule_receipts.append(
                {
                    "mechanic_id": "dnd5e.expansion.steel_defender.mending",
                    "event": "spell.steel_defender_mending",
                    "operations": [{"op": "builtin.expansion_provider"}],
                    "citations": [
                        {
                            "source_artifact_id": mending_contract["source_artifact_id"],
                            "source_pack_id": mending_contract["source_pack_id"],
                            "source_pack_version": mending_contract["source_pack_version"],
                            "reviewed_expression_hash": mending_contract[
                                "reviewed_expression_hash"
                            ],
                        }
                    ],
                    "ruleset_fingerprint": rules.fingerprint,
                }
            )
            rule_receipts.extend(
                _support.core_receipts(
                    rules,
                    [_support.CORE_MENDING_MECHANIC_ID],
                    "spell.mending.steel_defender",
                )
            )
        if sleep:
            assert sleep_target is not None
            resolved_level = int(applied.get("cast_level", cast_level or 1))
            if not 1 <= resolved_level <= 9:
                raise _support.CombatEngineError("Sleep cast level must be between 1 and 9")
            pool_roll = _support.asdict(_support.roll(f"{5 + 2 * (resolved_level - 1)}d8"))
            settled = _support.resolve_sleep_targets(
                [
                    {"id": target["target_id"], "sheet": timed_sheets[target["target_id"]]}
                    for target in sleep_target["targets"]
                ],
                pool=int(pool_roll["total"]),
                source_actor_id=current.id,
                source_spell_id=spell_id,
                source_rule_refs=spell_entry.get("rule_refs", []),
                ruleset="2014",
            )
            timed_sheets.update(settled["sheets"])
            applied.update(
                automatic_effect="sleep",
                area=sleep_target,
                pool_roll=pool_roll,
                pool_remaining=settled["pool_remaining"],
                targets=settled["targets"],
            )
            applied["ruling_required"] = [
                item
                for item in applied.get("ruling_required") or []
                if item != "targets_and_effect"
            ]
            applied["ruling_requirements"] = [
                item
                for item in applied.get("ruling_requirements") or []
                if item.get("kind") != "targets_and_effect"
            ]
            rule_receipts.extend(
                _support.core_receipts(rules, [_support.CORE_SLEEP_MECHANIC_ID], "spell.sleep")
            )
            for target_result in settled["targets"]:
                if target_result["skip_reason"] == "immune_to_magical_sleep":
                    target_result["rule_receipts"] = _support.core_receipts(
                        rules, [_support.CORE_FEY_ANCESTRY_MECHANIC_ID], "spell.sleep.immunity"
                    )
                    rule_receipts.extend(target_result["rule_receipts"])
        reconciled_dependencies = _support.reconcile_source_effect_dependencies(timed_sheets)
        timed_sheets = reconciled_dependencies["sheets"]
        applied["sheet"] = timed_sheets[current.id]
        rule_receipts.extend(applied.get("rule_receipts") or [])
        applied["rule_receipts"] = _support.deepcopy(rule_receipts)
        updates = [
            _support.CharacterStateUpdate(
                character_id=character.id,
                sheet=_support.validate_character_sheet(timed_sheets[character.id]),
                notes=_support.validate_character_notes(character.notes),
                expected_revision=(
                    expected_revision if character.id == current.id else character.revision
                ),
            )
            for character in all_characters
            if timed_sheets[character.id] != character.sheet
        ]
        current_after = _support.replace(
            current,
            sheet=_support.validate_character_sheet(timed_sheets[current.id]),
            notes=_support.validate_character_notes(current.notes),
            revision=current.revision + 1,
        )
        current_after_view = self.character_view(current_after)

        applied_result = {key: value for key, value in applied.items() if key != "sheet"}
        if compiled_spell_plan is not None:
            applied_result["resolution_plan_contract"] = _support.resolution_plan_contract(
                compiled_spell_plan
            )
            applied_result["semantic_solution"] = {
                "status": "compiled",
                "payment_recorded": True,
            }

        def cast_response(revisions: list[Any]) -> dict[str, Any]:
            response = {
                **_support._ruling_status(
                    "committed" if applied.get("automatic_effect") else "pending_ruling",
                    "generic_spell_effect",
                ),
                "result": _support.deepcopy(applied_result),
                "character": current_after_view,
                "game_time": time_transition["after"],
                "world_time": time_transition["world_time_after"],
                "elapsed_ticks": elapsed_ticks,
                "elapsed_minutes": elapsed_minutes,
                "advanced": advanced,
                "expired": expired,
                "world_advanced": list(dict.fromkeys(world_advanced)),
                "world_expired": list(dict.fromkeys(world_expired)),
                "campaign_revision": campaign.revision + 1,
                "revisions": [_support.asdict(item) for item in revisions],
            }
            stream = _support.active_random_stream()
            if stream is not None and stream.draw_count > 0:
                response["random_stream_receipt"] = stream.receipt()
            return response

        revisions_result = _support.StateMutationService(self.storage.database).replace(
            current.campaign_id,
            campaign_state=next_state,
            character_updates=updates,
            expected_campaign_revision=campaign.revision,
            operation=(
                "character.magic_item.spell.cast" if source_item_id else "character.spell.cast"
            ),
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=cast_response,
            ),
            rule_receipts=rule_receipts,
        )
        return cast_response(list(revisions_result or []))

    def _character_spell_prepare_v1(
        self,
        character_id: str,
        spell_id: str,
        prepared: bool,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Prepare or unprepare a spell under the D&D spellcasting constraints."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        if current.campaign_id is not None:
            campaign = self.campaigns.get(current.campaign_id)
            phase = self.authoritative_phase(current.campaign_id)
            if phase == _support.PROFILE_COMBAT:
                raise _support.CombatEngineError("prepared spells cannot be changed during combat")
            if phase != _support.PROFILE_LOBBY:
                raise _support.CombatEngineError(
                    "live prepared-spell changes must be submitted atomically with party_rest"
                )
            if self.preparation_setup_closed(
                campaign
            ) and not self.initial_preparation_allowed_for_new_actor(campaign, current):
                raise _support.CombatEngineError(
                    "individual preparation edits are initial setup only; after play starts, "
                    "submit the complete list with the edition's legal timing event"
                )
        preparation_rules = (
            self.effective_rule_context(current.campaign_id) if current.campaign_id else None
        )
        hydrated = (
            self.hydrate_class_prepared_spell_cards(
                current.campaign_id,
                current.sheet,
                spell_ids=[spell_id],
                branch_id=self.require_current_branch(current.campaign_id, None),
            )
            if prepared and current.campaign_id is not None
            else {"sheet": current.sheet, "materialized_spell_ids": []}
        )
        return self.update_sheet(
            character_id,
            _support.set_spell_prepared(hydrated["sheet"], spell_id, prepared),
            operation="character.spell.prepare" if prepared else "character.spell.unprepare",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"spell_id": spell_id, "prepared": prepared},
            response_extra={
                "materialized_spell_ids": hydrated["materialized_spell_ids"],
            },
            rule_receipts=_support.core_receipts(
                preparation_rules,
                ["dnd5e.core.spell.preparation"],
                "spell.prepare.setup",
            ),
        )

    def character_spell_prepare_list(
        self,
        character_id: str,
        spell_ids: list[str],
        event: str = "setup",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set the complete prepared list atomically under the edition timing rules."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        normalized_event = str(event).strip().lower().replace("-", "_")
        if current.campaign_id is not None:
            campaign = self.campaigns.get(current.campaign_id)
            phase = self.authoritative_phase(current.campaign_id)
            if phase == _support.PROFILE_COMBAT:
                raise _support.CombatEngineError("prepared spells cannot be changed during combat")
            if phase != _support.PROFILE_LOBBY:
                raise _support.CombatEngineError(
                    "switch to lobby for setup or level-up preparation changes"
                )
            if (
                normalized_event == "setup"
                and self.preparation_setup_closed(campaign)
                and not self.initial_preparation_allowed_for_new_actor(campaign, current)
            ):
                raise _support.CombatEngineError(
                    "prepared-spell setup is closed after live play starts; use the edition's "
                    "legal long-rest or level-up workflow"
                )
        if normalized_event not in {"setup", "level_up"}:
            raise _support.CombatEngineError(
                "this tool accepts setup or level_up; long-rest changes belong in party_rest"
            )
        hydrated = (
            self.hydrate_class_prepared_spell_cards(
                current.campaign_id,
                current.sheet,
                spell_ids=list(spell_ids),
                branch_id=self.require_current_branch(current.campaign_id, None),
            )
            if current.campaign_id is not None
            else {"sheet": current.sheet, "materialized_spell_ids": []}
        )
        result = _support.replace_prepared_spells(
            hydrated["sheet"],
            spell_ids=list(spell_ids),
            event=normalized_event,
        )
        preparation_rules = (
            self.effective_rule_context(current.campaign_id) if current.campaign_id else None
        )
        return self.update_sheet(
            character_id,
            _support.validate_character_sheet(result["sheet"]),
            operation=f"character.spell.prepare.{normalized_event}",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={"spell_ids": list(spell_ids), "event": normalized_event},
            response_extra={
                "preparation": {
                    **{key: value for key, value in result.items() if key != "sheet"},
                    "materialized_spell_ids": hydrated["materialized_spell_ids"],
                }
            },
            rule_receipts=_support.core_receipts(
                preparation_rules,
                ["dnd5e.core.spell.preparation"],
                f"spell.prepare.{normalized_event}",
            ),
        )

    def hydrate_class_prepared_spell_cards(
        self,
        campaign_id: str,
        sheet: dict[str, Any],
        *,
        spell_ids: list[str],
        branch_id: str,
    ) -> dict[str, Any]:
        """Materialize legal catalog choices before strict prepared-list validation."""
        value = _support.deepcopy(sheet)
        preparation_mode = str(
            value.get("spellcasting", {}).get("preparation", {}).get("mode") or "known"
        )
        if preparation_mode != "prepared":
            return {"sheet": value, "materialized_spell_ids": []}

        existing = {
            str(item.get("id") or "") for item in value.get("content", {}).get("spells", [])
        }
        requested = list(dict.fromkeys(str(item).strip() for item in spell_ids))
        missing = [item for item in requested if item and item not in existing]
        if not missing:
            return {"sheet": value, "materialized_spell_ids": []}

        catalog: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        for candidate in self.available_content_artifacts(
            campaign_id,
            kind="spell",
            branch_id=branch_id,
        ):
            catalog.setdefault(str(candidate[2].get("id") or ""), []).append(candidate)

        class_names = list(
            dict.fromkeys(
                str(item.get("name") or "").strip()
                for item in value.get("progression", {}).get("classes", [])
                if str(item.get("name") or "").strip()
            )
        )
        materialized: list[str] = []
        for spell_id in missing:
            matches = catalog.get(spell_id, [])
            if not matches:
                raise _support.CombatEngineError(
                    f"prepared spell is not available from the campaign's active "
                    f"content packs: {spell_id}"
                )
            if len(matches) != 1:
                raise _support.CombatEngineError(
                    f"prepared spell resolves to multiple active content artifacts: {spell_id}"
                )
            pack_id, version, artifact = matches[0]
            if str(artifact.get("application_state") or "selection_ready") != "selection_ready":
                raise _support.CombatEngineError(
                    f"prepared spell requires reviewed selection-ready content: {spell_id}"
                )
            card = _support.deepcopy(dict(artifact.get("card") or {}))
            eligible_sources: list[str] = []
            eligibility_errors: list[_support.CombatEngineError] = []
            for class_name in class_names:
                try:
                    eligible_sources.append(
                        _support.validate_spell_grant(
                            value,
                            card,
                            source_class=class_name,
                            artifact_id=spell_id,
                        )
                    )
                except _support.CombatEngineError as error:
                    eligibility_errors.append(error)
            eligible_sources = list(dict.fromkeys(eligible_sources))
            if not eligible_sources:
                if len(class_names) == 1 and eligibility_errors:
                    raise eligibility_errors[0]
                raise _support.CombatEngineError(
                    f"prepared spell has no legal recorded source class: {spell_id}"
                )
            if len(eligible_sources) != 1:
                raise _support.CombatEngineError(
                    f"prepared spell needs one unambiguous source class: {spell_id}"
                )
            source_class = eligible_sources[0]
            card.pop("classes", None)
            card["grant"] = {
                "source_type": "class",
                "source_key": source_class,
                "method": "class_prepared",
            }
            access_state = card.setdefault("access", {})
            access_state["known"] = False
            access_state["prepared"] = False
            card.update(
                id=spell_id,
                pack_id=pack_id,
                pack_version=version,
                rule_refs=list(artifact.get("rule_refs") or []),
                mechanic_refs=list(artifact.get("mechanic_refs") or []),
            )
            value.setdefault("content", {}).setdefault("spells", []).append(card)
            materialized.append(spell_id)
        return {"sheet": value, "materialized_spell_ids": materialized}

    def hydrate_statblock_spellcasting(
        self,
        campaign_id: str,
        parsed: Any,
        *,
        source_key: str,
        rule_refs: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Authorize catalog access, then delegate D&D normalization to the system package."""
        return _support.hydrate_dnd_statblock_spellcasting(
            parsed,
            self.available_content_artifacts(campaign_id, kind="spell"),
            source_key=source_key,
            rule_refs=rule_refs,
        )

    def hydrate_statblock_variant_spells(
        self,
        campaign_id: str,
        sheet: dict[str, Any],
        variant: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Hydrate source-cited replacement spells from active content.

        Public variants supply only canonical spell ids. They cannot inject
        arbitrary spell cards; every replacement target must resolve exactly
        once in the campaign's active D&D content.
        """

        if variant is None or "spell_replacements" not in variant:
            return sheet
        replacements = variant["spell_replacements"]
        if not isinstance(replacements, list) or not replacements:
            raise ValueError("spell_replacements must be a non-empty list")
        add_ids: list[str] = []
        for index, raw in enumerate(replacements):
            if not isinstance(raw, dict):
                raise ValueError(f"spell_replacements[{index}] must be an object")
            add_spell_id = str(raw.get("add_spell_id") or "").strip()
            if not add_spell_id:
                raise ValueError(f"spell_replacements[{index}].add_spell_id is required")
            add_ids.append(add_spell_id)
        if len(add_ids) != len(set(add_ids)):
            raise ValueError("spell replacement targets must be unique")

        hydrated = _support.deepcopy(sheet)
        spells = list(dict(hydrated.get("content") or {}).get("spells") or [])
        existing_ids = {str(item.get("id") or "") for item in spells}
        artifacts = self.available_content_artifacts(campaign_id, kind="spell")
        variant_source = f"variant:{self.statblock_variant_source_label(variant)}"
        for add_spell_id in add_ids:
            if add_spell_id in existing_ids:
                continue
            matches = [item for item in artifacts if str(item[2].get("id") or "") == add_spell_id]
            if len(matches) != 1:
                raise ValueError(
                    "statblock replacement spell must resolve exactly once in active "
                    f"content: {add_spell_id}"
                )
            pack_id, version, artifact = matches[0]
            card = _support.deepcopy(dict(artifact.get("card") or {}))
            card.pop("classes", None)
            card.update(
                {
                    "id": str(artifact["id"]),
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                    "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                    "grant": {
                        "source_type": "statblock_variant",
                        "source_key": variant_source,
                        "method": "known",
                    },
                    "access": {
                        "known": True,
                        "prepared": False,
                        "always_prepared": False,
                        "in_spellbook": False,
                        "ritual_available": False,
                        "at_will": False,
                    },
                }
            )
            spells.append(card)
            existing_ids.add(add_spell_id)
        hydrated["content"]["spells"] = spells
        return _support.validate_character_sheet(hydrated)

    def hydrate_magic_item_spell_artifacts(
        self,
        campaign_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind declared magic-item spell ids to exact active catalog cards."""
        hydrated = _support.deepcopy(item)
        mechanics = _support.deepcopy(dict(hydrated.get("mechanics") or {}))
        spellcasting = mechanics.get("spellcasting")
        if spellcasting is None:
            return hydrated
        if hydrated.get("kind") != "magic_item":
            raise ValueError("item spellcasting is supported only for kind=magic_item")
        if not str(hydrated.get("source_key") or "").strip():
            raise ValueError("magic item spellcasting requires a source_key")
        if not isinstance(spellcasting, dict):
            raise ValueError("magic item mechanics.spellcasting must be an object")
        allowed_spellcasting = {
            "requires_attunement",
            "requires_class_spell_list",
            "components_required",
            "spells",
        }
        unexpected = sorted(set(spellcasting) - allowed_spellcasting)
        if unexpected:
            raise ValueError(f"magic item spellcasting has unsupported fields: {unexpected}")
        raw_spells = spellcasting.get("spells")
        if not isinstance(raw_spells, list) or not raw_spells:
            raise ValueError("magic item spellcasting requires at least one spell")
        available = self.available_content_artifacts(campaign_id, kind="spell")
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, specification in enumerate(raw_spells):
            if not isinstance(specification, dict):
                raise ValueError(f"magic item spellcasting.spells[{index}] must be an object")
            allowed_specification = {"artifact_id", "charge_cost", "casting_time"}
            unknown = sorted(set(specification) - allowed_specification)
            if unknown:
                raise ValueError(
                    f"magic item spellcasting.spells[{index}] has unsupported fields: {unknown}"
                )
            artifact_id = str(specification.get("artifact_id") or "").strip()
            if not artifact_id or artifact_id in seen:
                raise ValueError("magic item spell artifact ids must be present and unique")
            seen.add(artifact_id)
            charge_cost = specification.get("charge_cost")
            if (
                isinstance(charge_cost, bool)
                or not isinstance(charge_cost, int)
                or charge_cost <= 0
            ):
                raise ValueError("magic item spell charge_cost must be a positive integer")
            matches = [entry for entry in available if str(entry[2].get("id")) == artifact_id]
            if len(matches) != 1:
                raise ValueError(
                    f"magic item spell artifact must resolve exactly once: {artifact_id}"
                )
            pack_id, version, artifact = matches[0]
            card = _support.deepcopy(dict(artifact.get("card") or {}))
            card.update(
                {
                    "id": artifact_id,
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                    "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                }
            )
            casting_time = str(specification.get("casting_time") or "").strip()
            resolved.append(
                {
                    "artifact_id": artifact_id,
                    "charge_cost": charge_cost,
                    **({"casting_time": casting_time} if casting_time else {}),
                    "card": card,
                }
            )
        charges = dict(hydrated.get("charges") or {})
        maximum = charges.get("max")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ValueError("magic item spellcasting requires a positive charges.max")
        if max(item["charge_cost"] for item in resolved) > maximum:
            raise ValueError("magic item spell charge_cost exceeds charges.max")
        mechanics["spellcasting"] = {
            "requires_attunement": bool(spellcasting.get("requires_attunement")),
            "requires_class_spell_list": bool(spellcasting.get("requires_class_spell_list")),
            "components_required": bool(spellcasting.get("components_required", True)),
            "spells": resolved,
        }
        hydrated["mechanics"] = mechanics
        return hydrated

    def refresh_level_unlocked_subclass_spells(
        self,
        campaign_id: str,
        sheet: dict[str, Any],
        *,
        class_name: str,
        branch_id: str,
    ) -> list[dict[str, Any]]:
        """Materialize always-prepared subclass spells unlocked by the new class level."""
        target_class = next(
            item
            for item in sheet["progression"]["classes"]
            if str(item.get("name") or "").casefold() == class_name.casefold()
        )
        subclass_name = str(target_class.get("subclass") or "")
        if not subclass_name:
            return []

        def exact_recorded_card(
            record: dict[str, Any],
            *,
            required: bool = True,
        ) -> dict[str, Any] | None:
            artifact_id = str(record.get("artifact_id") or record.get("id") or "")
            pack_id = str(record.get("pack_id") or "")
            version = str(record.get("pack_version") or "")
            if not artifact_id or not pack_id or not version:
                raise ValueError(
                    "recorded subclass content must include artifact id, pack id, and pack version"
                )
            try:
                pack = self.rule_packs.get_version(pack_id, version)
            except LookupError as error:
                raise _support.RulesetUnavailableError(
                    f"recorded subclass content pack is unavailable: {pack_id}@{version}"
                ) from error
            artifact = next(
                (item for item in pack.artifacts if str(item.get("id") or "") == artifact_id),
                None,
            )
            if artifact is None:
                if not required:
                    return None
                raise _support.RulesetUnavailableError(
                    f"recorded subclass content is unavailable: "
                    f"{artifact_id} in {pack_id}@{version}"
                )
            return dict(artifact.get("card") or {})

        grant_sources: list[tuple[str, str, str, list[dict[str, Any]]]] = []
        recorded_subclass = next(
            (
                item
                for item in sheet.get("content", {}).get("selections", [])
                if str(item.get("kind") or "") == "subclass"
                and str(item.get("name") or "").casefold() == subclass_name.casefold()
            ),
            None,
        )
        if recorded_subclass is not None:
            subclass_card = exact_recorded_card(recorded_subclass)
            assert subclass_card is not None
            if str(subclass_card.get("class_name") or "").casefold() != class_name.casefold():
                raise ValueError("recorded subclass does not belong to the advanced class")
            grant_sources.append(
                (
                    subclass_name,
                    str(recorded_subclass["pack_id"]),
                    str(recorded_subclass["pack_version"]),
                    _support._subclass_spell_grants(subclass_card),
                )
            )
        for feature_record in sheet.get("content", {}).get("features", []):
            if not feature_record.get("pack_id") or not feature_record.get("pack_version"):
                continue
            feature_card = exact_recorded_card(feature_record, required=False)
            if feature_card is None:
                continue
            if (
                str(feature_card.get("class_name") or "").casefold() != class_name.casefold()
                or str(feature_card.get("subclass_name") or "").casefold()
                != subclass_name.casefold()
            ):
                continue
            spell_options = dict(feature_card.get("always_prepared_spell_options") or {})
            if not spell_options:
                continue
            choices = dict(feature_record.get("choices") or {})
            if not choices:
                grants = list(feature_record.get("advancement_grants") or [])
                if grants:
                    choices = dict(grants[-1].get("choices") or {})
            option = str(choices.get("option") or "")
            if option not in spell_options:
                raise ValueError("recorded subclass spell option is missing or unavailable")
            grant_sources.append(
                (
                    subclass_name,
                    str(feature_record["pack_id"]),
                    str(feature_record["pack_version"]),
                    [
                        {**_support.deepcopy(dict(item)), "method": "always_prepared"}
                        for item in spell_options[option]
                    ],
                )
            )

        candidates = self.available_content_artifacts(campaign_id, branch_id=branch_id)
        class_level = int(target_class.get("level", 0) or 0)
        unlocked: list[dict[str, Any]] = []
        always_prepared_ids: set[str] = set()
        for source_name, source_pack_id, source_pack_version, grants in grant_sources:
            for grant in grants:
                minimum_level = int(grant.get("minimum_level", 1) or 1)
                if minimum_level > class_level:
                    continue
                method = str(grant.get("method") or "always_prepared")
                spell_name = str(grant.get("name") or "").strip()
                matches = self.source_scoped_content_matches(
                    [
                        item
                        for item in candidates
                        if item[2].get("kind") == "spell"
                        and str(dict(item[2].get("card") or {}).get("name") or "").casefold()
                        == spell_name.casefold()
                    ],
                    source_pack_id=source_pack_id,
                    source_pack_version=source_pack_version,
                )
                if len(matches) != 1:
                    raise _support.RulesetUnavailableError(
                        f"subclass spell is unavailable at level {class_level}: {spell_name}"
                    )
                match = matches[0]
                spell_pack_id, spell_version, spell_artifact = match
                spell_id = str(spell_artifact["id"])
                if method == "always_prepared":
                    always_prepared_ids.add(spell_id)
                spell_card = next(
                    (
                        item
                        for item in sheet["content"]["spells"]
                        if str(item.get("id") or "") == spell_id
                    ),
                    None,
                )
                runtime_method = "class_prepared" if method == "always_prepared" else method
                was_granted = bool(
                    spell_card
                    and dict(spell_card.get("grant") or {}).get("source_type") == "subclass"
                    and str(dict(spell_card.get("grant") or {}).get("source_key") or "")
                    == source_name
                    and dict(spell_card.get("grant") or {}).get("method") == runtime_method
                )
                if spell_card is None:
                    spell_card = _support._character_spell_card(
                        dict(spell_artifact.get("card") or {})
                    )
                    sheet["content"]["spells"].append(spell_card)
                spell_card["grant"] = {
                    "source_type": "subclass",
                    "source_key": source_name,
                    "method": runtime_method,
                }
                access = spell_card.setdefault("access", {})
                access["known"] = method == "known"
                access["prepared"] = method == "always_prepared"
                access["always_prepared"] = method == "always_prepared"
                if method == "spellbook":
                    spellbook = sheet["spellcasting"]["spellbook"]
                    if not spellbook.get("enabled"):
                        raise _support.RulesetUnavailableError(
                            "subclass spellbook grant requires an enabled spellbook"
                        )
                    if spell_id not in spellbook.get("spell_ids", []):
                        spellbook["spell_ids"].append(spell_id)
                spell_card.update(
                    id=spell_id,
                    pack_id=spell_pack_id,
                    pack_version=spell_version,
                    rule_refs=list(spell_artifact.get("rule_refs") or []),
                    mechanic_refs=list(spell_artifact.get("mechanic_refs") or []),
                )
                if not was_granted:
                    unlocked.append(
                        {
                            "artifact_id": spell_id,
                            "name": str(spell_card.get("name") or spell_name),
                            "minimum_level": minimum_level,
                            "method": method,
                            "source_subclass": source_name,
                        }
                    )
        if always_prepared_ids:
            preparation = sheet["spellcasting"]["preparation"]
            preparation["selected_spell_ids"] = [
                spell_id
                for spell_id in preparation.get("selected_spell_ids", [])
                if spell_id not in always_prepared_ids
            ]
        return unlocked

    def settle_spellbook_copy(
        self,
        *,
        current: Any,
        sheet: dict[str, Any],
        artifact_id: str,
        pack_id: str,
        version: str,
        level: int,
        school: str,
        selection: dict[str, Any],
        principal_id: str,
        expected_revision: int,
        idempotency_key: str,
        content_context: dict[str, Any] | None = None,
        content_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pay, wait, expire effects, and record one discovered spell atomically."""
        assert current.campaign_id is not None
        campaign_id = current.campaign_id
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if level < 1:
            raise ValueError("cantrips cannot be copied from a spellbook")
        campaign = self.campaigns.get(campaign_id)
        next_state = _support.validate_party_state(_support.deepcopy(campaign.state or {}))
        self.require_resolved_short_rest_hit_dice(
            campaign_id,
            next_state,
            operation="copying a spell into a spellbook",
        )
        if self.authoritative_phase(campaign_id) != _support.PROFILE_PLAY:
            raise _support.CombatEngineError("spellbook copying is available only during play")
        source_owner = str(selection.get("source_owner") or "party").strip().casefold()
        if source_owner not in _support.INVENTORY_OWNER_SCOPES:
            raise ValueError("spellbook copy source_owner must be party or character")
        source_item_id = str(selection.get("source_item_id") or "").strip()
        if not source_item_id:
            raise ValueError("spellbook copy requires source_item_id")
        source_inventory = (
            next_state["party"]["inventory"] if source_owner == "party" else sheet["inventory"]
        )
        source_item = next(
            (
                item
                for item in source_inventory.get("items", [])
                if str(item.get("id") or "") == source_item_id
            ),
            None,
        )
        if source_item is None:
            raise ValueError("spellbook copy source item is not in the selected inventory")
        mechanics = dict(source_item.get("mechanics") or {})
        if source_item.get("kind") != "spellbook":
            raise ValueError("spellbook copy source item must have kind=spellbook")
        campaign_edition = self.campaign_rules_edition(campaign.id)
        if str(mechanics.get("edition") or "") != campaign_edition:
            raise ValueError("spellbook copy source edition does not match the campaign")
        if not mechanics.get("copyable", False):
            raise ValueError("spellbook copy source is not marked copyable")
        if artifact_id not in set(mechanics.get("spell_ids") or []):
            raise ValueError("requested spell is not recorded in the source spellbook")

        feature_ids = {
            str(feature.get("id") or "") for feature in sheet.get("content", {}).get("features", [])
        }
        normalized_school = school.strip().casefold().split(" ", 1)[0]
        rule_facts = {
            "actor_id": current.id,
            "spell_id": artifact_id,
            "spell_level": level,
            "spell_school": normalized_school,
            "source_item_id": source_item_id,
            "source_was_previously_deciphered": bool(mechanics.get("deciphered", False)),
            **{f"has_feature:{feature_id}": True for feature_id in feature_ids if feature_id},
        }
        rule_context = self.effective_rule_context(campaign_id, facts=rule_facts)
        copy_rules = _support.apply_rule_event(sheet, "spellbook.copy.before", rule_context)
        if copy_rules.status != "committed":
            return {
                "status": copy_rules.status,
                "pending": list(copy_rules.pending),
                "rule_receipts": list(copy_rules.receipts),
            }
        cost_percent = 100
        time_percent = 100
        for modifier in copy_rules.modifiers:
            if modifier.get("target") == "copy_cost_percent":
                cost_percent += int(modifier.get("value", 0) or 0)
            elif modifier.get("target") == "copy_time_percent":
                time_percent += int(modifier.get("value", 0) or 0)
        core_boundaries = ["dnd5e.core.spell.spellbook_copy"]
        if (
            normalized_school == "evocation"
            and "dnd5e.content.srd2014.feature.school-of-evocation-evocation-savant" in feature_ids
        ):
            cost_percent -= 50
            time_percent -= 50
            core_boundaries.append("dnd5e.core.spell.evocation_savant")
        if cost_percent <= 0 or time_percent <= 0:
            raise ValueError("spellbook copy modifiers must leave positive cost and time")
        base_cost_cp = level * 5000
        base_minutes = level * 120
        cost_cp = (base_cost_cp * cost_percent + 99) // 100
        minutes = (base_minutes * time_percent + 99) // 100
        hours = minutes / 60
        payment_owner = str(selection.get("payment_owner") or "character").strip().casefold()
        if payment_owner not in _support.INVENTORY_OWNER_SCOPES:
            raise ValueError("spellbook copy payment_owner must be party or character")
        payment_wallet = (
            next_state["party"]["inventory"]["wallet"]
            if payment_owner == "party"
            else sheet["inventory"]["wallet"]
        )
        payment = self.spend_exact_wallet_payment(
            payment_wallet, selection.get("payment"), required_cp=cost_cp
        )

        next_state, time_transition = self.advance_state_game_time(
            next_state,
            period="minute",
            count=minutes,
        )
        next_world_time = time_transition["world_time_after"]
        elapsed_ticks = int(time_transition["elapsed_ticks"])

        world_duration = self.advance_world_effect_clocks(
            next_state,
            elapsed_ticks=elapsed_ticks,
            period_steps={"round": elapsed_ticks},
        )
        next_state = world_duration["state"]
        world_advanced = world_duration["advanced"]
        world_expired = world_duration["expired"]

        branch_id = self.require_current_branch(campaign_id, None)
        request_payload = {
            "operation": "character.spellbook.copy",
            "character_id": current.id,
            "artifact_id": artifact_id,
            "pack_id": pack_id,
            "version": version,
            "selection": selection,
        }
        scope = f"character-write:{campaign_id}:{branch_id}:{principal_id}:{current.id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        rule_context = _support.context_with_facts(
            rule_context,
            copy_hours=hours,
            copy_minutes=minutes,
            copy_cost_cp=cost_cp,
            copy_cost_percent=cost_percent,
            copy_time_percent=time_percent,
        )
        receipts: list[dict[str, Any]] = list(copy_rules.receipts)
        updates: list[_support.CharacterStateUpdate] = []
        advanced: dict[str, list[str]] = {}
        expired: dict[str, list[str]] = {}
        for character in self.characters.list(campaign_id=campaign_id):
            updated_sheet = sheet if character.id == current.id else character.sheet
            character_advanced: list[str] = []
            character_expired: list[str] = []
            duration = _support.advance_elapsed_effect_durations(
                updated_sheet,
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
                    actor_id=character.id,
                    period="minute",
                    amount=minutes,
                ),
            )
            receipts.extend(extension.receipts)
            updated_sheet = extension.sheet
            character_advanced.extend(duration["advanced"])
            character_expired.extend(duration["expired"])
            character_advanced.extend(round_duration["advanced"])
            character_expired.extend(round_duration["expired"])
            if updated_sheet != character.sheet:
                updates.append(
                    _support.CharacterStateUpdate(
                        character_id=character.id,
                        sheet=_support.validate_character_sheet(updated_sheet),
                        notes=_support.validate_character_notes(character.notes),
                        expected_revision=(
                            expected_revision if character.id == current.id else character.revision
                        ),
                    )
                )
            if character_advanced:
                advanced[character.id] = list(dict.fromkeys(character_advanced))
            if character_expired:
                expired[character.id] = list(dict.fromkeys(character_expired))
        receipts.extend(
            _support.core_receipts(
                rule_context,
                core_boundaries,
                "character.spellbook.copy",
            )
        )
        if content_receipt is not None:
            receipts.append(_support.deepcopy(content_receipt))
        next_state, updates, _ = self.reconcile_actor_effect_dependencies(
            campaign, next_state, updates, {}
        )
        current_update = next(item for item in updates if item.character_id == current.id)
        response = self.character_view(
            _support.replace(
                current,
                sheet=current_update.sheet,
                notes=current_update.notes,
                revision=current.revision + 1,
            )
        )
        response["spellbook_copy"] = {
            "spell_id": artifact_id,
            "source_owner": source_owner,
            "source_item_id": source_item_id,
            "deciphered_during_copy": not bool(mechanics.get("deciphered", False)),
            "payment_owner": payment_owner,
            "payment": payment,
            "cost_cp": cost_cp,
            "base_cost_cp": base_cost_cp,
            "cost_percent": cost_percent,
            "minutes": minutes,
            "hours": hours,
            "base_minutes": base_minutes,
            "time_percent": time_percent,
            "game_time": time_transition["after"],
            "world_time": next_world_time,
            "advanced": advanced,
            "expired": expired,
            "world_advanced": list(dict.fromkeys(world_advanced)),
            "world_expired": list(dict.fromkeys(world_expired)),
            "rule_receipts": receipts,
        }
        if content_context is not None:
            response["content_context"] = _support.deepcopy(content_context)
        _support.StateMutationService(self.storage.database).replace(
            campaign_id,
            campaign_state=next_state,
            character_updates=updates,
            expected_campaign_revision=campaign.revision,
            operation="character.spellbook.copy",
            actor=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=response,
            ),
            rule_receipts=receipts,
        )
        return response

    def _character_spell_prepare_v2(
        self,
        character_id: str,
        mode: Literal["set", "replace_all"],
        payload: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set one prepared spell or replace the validated prepared-spell list.

        Requires the character's expected_revision and idempotency_key. Payload
        for set: {spell_id, prepared}; replace_all: {spell_ids, event?}. Spell ids
        must be learned on this actor first, not merely listed in the catalog.
        """
        data = self.facade_payload(payload)
        if mode == "set":
            result = self.character_spell_prepare_impl(
                character_id,
                self.required(data, "spell_id"),
                self.required_boolean(data, "prepared"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        else:
            result = self.character_spell_prepare_list(
                character_id,
                self.required(data, "spell_ids"),
                data.get("event", "setup"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        return self.facade_result(mode, result)
