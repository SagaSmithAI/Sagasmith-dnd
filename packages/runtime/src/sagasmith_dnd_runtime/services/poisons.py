"""Atomic, source-bound 2014 poison dose and delivery operations."""

from __future__ import annotations

from typing import Any, Literal

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.conditions import effect_is_suspended_by_petrification
from sagasmith_dnd.engine import roll
from sagasmith_dnd.poisons import (
    BASIC_POISON_MECHANIC_ID,
    POISON_ITEM_SOURCE_PREFIX,
    POISON_SOURCE_REF,
    PoisonProfile,
    build_midnight_tears_effect,
    build_poison_effect,
    contact_exposes,
    filter_poison_condition_immunities,
    injury_exposes,
    poison_profile,
    record_poison_healing_lock,
    settle_poison_repeat_save,
    validate_ingested_delivery,
)

from .. import application_support as support

POISON_MECHANIC_ID = "dnd5e.core.gamemastering.poisons_2014"


def _mechanic_id(profile: PoisonProfile) -> str:
    return BASIC_POISON_MECHANIC_ID if profile.id == "basic_poison" else POISON_MECHANIC_ID


def _find_inventory_item(sheet: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in sheet.get("inventory", {}).get("items", [])
            if str(item.get("id") or "") == str(item_id)
        ),
        None,
    )


def _dose_profile(sheet: dict[str, Any], item_id: str) -> tuple[dict[str, Any], PoisonProfile]:
    item = _find_inventory_item(sheet, item_id)
    if item is None:
        raise support.CombatEngineError("source-bound poison dose is not in the actor inventory")
    identity = dict(dict(item.get("mechanics") or {}).get("poison_dose") or {})
    profile = poison_profile(identity.get("poison_id"))
    if (
        item.get("kind") != "consumable"
        or str(item.get("source_key") or "") != POISON_ITEM_SOURCE_PREFIX + profile.id
        or identity.get("delivery") != profile.delivery
        or identity.get("edition") != "2014"
        or identity.get("source_ref") != profile.source_ref
        or int(item.get("quantity", 0) or 0) < 1
    ):
        raise support.CombatEngineError("poison dose identity or quantity is invalid")
    return item, profile


def _consume_one_item(sheet: dict[str, Any], item_id: str) -> None:
    item = _find_inventory_item(sheet, item_id)
    if item is None:
        raise support.CombatEngineError("poison dose disappeared before delivery")
    quantity = int(item.get("quantity", 0) or 0)
    if quantity < 1:
        raise support.CombatEngineError("poison dose quantity is empty")
    if quantity == 1:
        sheet["inventory"]["items"].remove(item)
    else:
        item["quantity"] = quantity - 1


def _active_coating(state: dict[str, Any], coating_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in dict(state.get("poison_coatings") or {}).get("coatings", [])
            if item.get("active") and str(item.get("id") or "") == str(coating_id)
        ),
        None,
    )


def _target_records(runtime, campaign_id: str, target_ids: list[str]):
    if not target_ids or len(target_ids) != len(set(target_ids)):
        raise support.CombatEngineError("poison delivery requires unique target actors")
    records = []
    for actor_id in target_ids:
        record = runtime.require_campaign_actor(campaign_id, actor_id)
        if str(record.sheet.get("edition") or "") != "2014":
            raise support.CombatEngineError("2014 poison cannot affect a non-2014 actor")
        records.append(record)
    return records


def _source_poison_state(effect: dict[str, Any]) -> dict[str, Any] | None:
    if (
        effect.get("kind") != "poison"
        or effect.get("source") != POISON_SOURCE_REF
        or not effect.get("active")
    ):
        return None
    state = dict(dict(effect.get("metadata") or {}).get("poison_state") or {})
    if state.get("source_ref") != POISON_SOURCE_REF or state.get("edition") != "2014":
        return None
    return state


def _rules(
    runtime, campaign_id: str, branch_id: str, source_actor_id: str, target_id: str, profile
):
    return runtime.effective_rule_context(
        campaign_id,
        branch_id=branch_id,
        facts={
            "actor_id": target_id,
            "source_actor_id": source_actor_id,
            "kind": "saving_throw",
            "ability": "constitution",
            "save_against_poison": True,
            "poison_id": profile.id,
            "save_dc": profile.save_dc,
        },
    )


def _settle_target(
    runtime,
    campaign,
    encounter: dict[str, Any] | None,
    record,
    sheet: dict[str, Any],
    *,
    profile: PoisonProfile,
    source_actor_id: str,
    branch_id: str,
    partial_ruling: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    actor = runtime.combat_actor_snapshot(record.id)
    actor["sheet"] = support.deepcopy(sheet)
    actor["derived"] = derive_character_sheet(sheet)
    rule_context = _rules(runtime, campaign.id, branch_id, source_actor_id, record.id, profile)
    save = support.resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=profile.save_dc,
        advantage=partial_ruling == "advantage_on_save",
        encounter=encounter,
        rules=rule_context,
        rng=support.active_random_stream(),
        ruleset="2014",
    )
    save_succeeded = bool(save.get("success"))
    effect_id = support.uuid4().hex
    result: dict[str, Any] = {
        "target_actor_id": record.id,
        "poison_id": profile.id,
        "save": support.deepcopy(save),
        "damage": None,
        "effect": None,
    }
    next_sheet = support.deepcopy(sheet)
    hp_damage = 0
    # A successful save only rolls damage when the source explicitly deals
    # half damage on success. Otherwise the poison's damage is failure-only,
    # so do not consume damage dice or emit a zero-damage settlement.
    if profile.initial_damage and (not save_succeeded or profile.half_on_success):
        damage_roll = roll(profile.initial_damage, rng=support.active_random_stream())
        amount = int(damage_roll.total)
        if save_succeeded and profile.half_on_success:
            amount //= 2
        if not save_succeeded and partial_ruling == "half_damage_on_failure":
            amount //= 2
        target_uses_death_saves = actor.get("character_type") == "pc"
        if encounter:
            combatant = next(
                (
                    item
                    for item in encounter.get("combatants", [])
                    if str(item.get("actor_id") or "") == record.id
                ),
                None,
            )
            if combatant is not None:
                target_uses_death_saves = runtime.combatant_zero_hp_buffered(combatant)
        damage = support.apply_damage_to_sheet(
            next_sheet,
            amount=amount,
            damage_type="poison",
            source=f"poison:{effect_id}",
            ruleset="2014",
            death_saves=target_uses_death_saves,
        )
        next_sheet = damage["sheet"]
        hp_damage = int(damage.get("hp_damage", 0) or 0)
        result["damage"] = {
            **{key: value for key, value in damage.items() if key != "sheet"},
            "expression": profile.initial_damage,
            "rolls": list(damage_roll.rolls),
            "detail": damage_roll.detail,
            "source": f"poison:{effect_id}",
        }

    save_total = int(save.get("total", 0) or 0)
    effect = build_poison_effect(
        profile,
        effect_id=effect_id,
        source_actor_id=source_actor_id,
        elapsed_ticks=int(dict(campaign.state or {}).get("game_time", {}).get("elapsed_ticks", 0)),
        save_succeeded=save_succeeded,
        save_failed_by=max(0, profile.save_dc - save_total),
        partial_ruling=partial_ruling,
        duration_roll_hours=(
            int(roll("4d6", rng=support.active_random_stream()).total)
            if not save_succeeded and profile.rolled_duration
            else None
        ),
    )
    receipts = support.core_receipts(
        rule_context,
        [POISON_MECHANIC_ID],
        "combat.poison.delivery",
    )
    if effect is not None:
        effect = filter_poison_condition_immunities(next_sheet, effect)
        try:
            next_sheet, _ = support.add_effect(next_sheet, effect)
        except ValueError as error:
            raise support.CombatEngineError(str(error)) from error
        if profile.healing_locked_damage and hp_damage:
            effects = next_sheet.get("effects", [])
            current = next(item for item in effects if item.get("id") == effect_id)
            locked = record_poison_healing_lock(current, hp_damage=hp_damage)
            effects[effects.index(current)] = locked
            next_sheet = support.validate_character_sheet(next_sheet)
            effect = locked
        result["effect"] = support.deepcopy(effect)
    result["rule_receipts"] = support.deepcopy(receipts)
    return support.validate_character_sheet(next_sheet), result, receipts


def _settle_basic_poison_hit(
    runtime,
    campaign,
    encounter: dict[str, Any],
    record,
    sheet: dict[str, Any],
    *,
    source_actor_id: str,
    branch_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Resolve Basic Poison on a hit, independently of physical damage dealt."""

    profile = poison_profile("basic_poison")
    actor = runtime.combat_actor_snapshot(record.id)
    actor["sheet"] = support.deepcopy(sheet)
    actor["derived"] = derive_character_sheet(sheet)
    rule_context = _rules(runtime, campaign.id, branch_id, source_actor_id, record.id, profile)
    save = support.resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=profile.save_dc,
        encounter=encounter,
        rules=rule_context,
        rng=support.active_random_stream(),
        ruleset="2014",
    )
    result: dict[str, Any] = {
        "target_actor_id": record.id,
        "poison_id": profile.id,
        "trigger": "weapon_hit",
        "save": support.deepcopy(save),
        "damage": None,
        "effect": None,
        "condition_applied": False,
    }
    next_sheet = support.deepcopy(sheet)
    if not save.get("success"):
        damage_roll = roll("1d4", rng=support.active_random_stream())
        target_uses_death_saves = actor.get("character_type") == "pc"
        combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if str(item.get("actor_id") or "") == record.id
            ),
            None,
        )
        if combatant is not None:
            target_uses_death_saves = runtime.combatant_zero_hp_buffered(combatant)
        damage = support.apply_damage_to_sheet(
            next_sheet,
            amount=int(damage_roll.total),
            damage_type="poison",
            source=f"poison:basic_poison:{support.uuid4().hex}",
            ruleset="2014",
            death_saves=target_uses_death_saves,
        )
        next_sheet = damage["sheet"]
        result["damage"] = {
            **{key: value for key, value in damage.items() if key != "sheet"},
            "expression": "1d4",
            "rolls": list(damage_roll.rolls),
            "detail": damage_roll.detail,
            "damage_type": "poison",
        }
    receipts = support.core_receipts(
        rule_context,
        [BASIC_POISON_MECHANIC_ID],
        "combat.poison.basic_hit",
    )
    result["rule_receipts"] = support.deepcopy(receipts)
    return support.validate_character_sheet(next_sheet), result, receipts


class PoisonsService:
    def poison_clock_events_due(self, campaign_id: str, elapsed_ticks: int) -> bool:
        campaign = self.campaigns.get(campaign_id)
        for coating in dict(dict(campaign.state or {}).get("poison_coatings") or {}).get(
            "coatings", []
        ):
            if not coating.get("active"):
                continue
            profile = poison_profile(coating.get("poison_id"))
            if profile.duration_ticks and (
                int(coating.get("created_at_elapsed_ticks", 0) or 0) + profile.duration_ticks
                <= elapsed_ticks
            ):
                return True
        for record in self.characters.list(campaign_id=campaign_id):
            for effect in record.sheet.get("effects", []):
                state = _source_poison_state(effect)
                if state is None or effect_is_suspended_by_petrification(record.sheet, effect):
                    continue
                profile = poison_profile(state.get("poison_id"))
                due = (
                    state.get("midnight_due_elapsed_ticks")
                    if profile.delayed_until_midnight
                    else state.get("next_due_elapsed_ticks")
                    if profile.recurring_period == "every_24_hours"
                    else None
                )
                if due is not None and int(due) <= elapsed_ticks:
                    return True
        return False

    def settle_poison_clock_events(
        self,
        campaign,
        campaign_id: str,
        state: dict[str, Any],
        updates: list[Any],
        *,
        branch_id: str,
    ) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Settle crossed midnight and Pale Tincture boundaries in stable order."""

        elapsed_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0))
        records = {record.id: record for record in self.characters.list(campaign_id=campaign_id)}
        sheets = {
            actor_id: support.deepcopy(update.sheet)
            for actor_id, update in ((item.character_id, item) for item in updates)
        }
        pending: dict[tuple[str, str], int] = {}

        def collect_due() -> None:
            for actor_id, record in records.items():
                sheet = sheets.get(actor_id, record.sheet)
                for effect in sheet.get("effects", []):
                    poison_state = _source_poison_state(effect)
                    if poison_state is None or effect_is_suspended_by_petrification(sheet, effect):
                        continue
                    profile = poison_profile(poison_state.get("poison_id"))
                    if profile.delayed_until_midnight:
                        due_value = poison_state.get("midnight_due_elapsed_ticks")
                    elif profile.recurring_period == "every_24_hours":
                        due_value = poison_state.get("next_due_elapsed_ticks")
                    else:
                        continue
                    if (
                        isinstance(due_value, bool)
                        or not isinstance(due_value, int)
                        or due_value < 0
                    ):
                        raise support.CombatEngineError(
                            f"{profile.name} has an invalid authoritative due time"
                        )
                    key = (actor_id, str(effect.get("id") or ""))
                    if due_value <= elapsed_ticks and key not in pending:
                        pending[key] = due_value

        collect_due()
        events: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        changed: set[str] = set()
        coatings = dict(state.get("poison_coatings") or {}).get("coatings", [])
        for coating in coatings:
            if not coating.get("active"):
                continue
            profile = poison_profile(coating.get("poison_id"))
            due = int(coating.get("created_at_elapsed_ticks", 0) or 0) + profile.duration_ticks
            if profile.duration_ticks and due <= elapsed_ticks:
                coating["active"] = False
                events.append(
                    {
                        "kind": "coating_expired",
                        "coating_id": coating["id"],
                        "poison_id": profile.id,
                        "object_actor_id": coating["object_actor_id"],
                        "object_item_id": coating["object_item_id"],
                        "due_elapsed_ticks": due,
                        "ended": True,
                    }
                )
                expiry_rules = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "poison_id": profile.id,
                        "coating_id": coating["id"],
                        "object_actor_id": coating["object_actor_id"],
                        "object_item_id": coating["object_item_id"],
                    },
                )
                receipts.extend(
                    support.core_receipts(
                        expiry_rules,
                        [_mechanic_id(profile)],
                        "combat.poison.coating_expiry",
                    )
                )
        while pending:
            (actor_id, effect_id), due = min(
                pending.items(), key=lambda item: (item[1], item[0][0], item[0][1])
            )
            del pending[(actor_id, effect_id)]
            record = records[actor_id]
            sheet = support.deepcopy(sheets.get(actor_id, record.sheet))
            index = next(
                (
                    i
                    for i, effect in enumerate(sheet.get("effects", []))
                    if effect.get("id") == effect_id
                ),
                None,
            )
            if index is None:
                continue
            effect = sheet["effects"][index]
            poison_state = _source_poison_state(effect)
            if poison_state is None or effect_is_suspended_by_petrification(sheet, effect):
                continue
            profile = poison_profile(poison_state.get("poison_id"))
            source_actor_id = str(poison_state.get("source_actor_id") or actor_id)
            rule_context = _rules(
                self,
                campaign_id,
                branch_id,
                source_actor_id,
                actor_id,
                profile,
            )
            actor = self.combat_actor_snapshot(actor_id)
            actor["sheet"] = support.deepcopy(sheet)
            actor["derived"] = derive_character_sheet(sheet)
            save = support.resolve_actor_check(
                actor,
                kind="save",
                ability="constitution",
                dc=profile.save_dc,
                encounter=None,
                rules=rule_context,
                rng=support.active_random_stream(),
                ruleset="2014",
            )
            damage_result = None
            if profile.delayed_until_midnight:
                damage_roll = roll("9d6", rng=support.active_random_stream())
                amount = int(damage_roll.total)
                if save.get("success"):
                    amount //= 2
                damage = support.apply_damage_to_sheet(
                    sheet,
                    amount=amount,
                    damage_type="poison",
                    source=f"poison:{effect_id}:midnight",
                    ruleset="2014",
                    death_saves=actor.get("character_type") == "pc",
                )
                sheet = damage["sheet"]
                sheet = support.remove_effect(sheet, effect_id)
                damage_result = {
                    **{key: value for key, value in damage.items() if key != "sheet"},
                    "expression": "9d6",
                    "rolls": list(damage_roll.rolls),
                    "detail": damage_roll.detail,
                }
                events.append(
                    {
                        "actor_id": actor_id,
                        "effect_id": effect_id,
                        "poison_id": profile.id,
                        "due_elapsed_ticks": due,
                        "save": support.deepcopy(save),
                        "damage": damage_result,
                        "ended": True,
                    }
                )
            else:
                outcome = settle_poison_repeat_save(
                    effect,
                    save_succeeded=bool(save.get("success")),
                    elapsed_ticks=due,
                )
                next_effect = outcome["effect"]
                if not save.get("success"):
                    damage_roll = roll(profile.recurring_damage, rng=support.active_random_stream())
                    damage = support.apply_damage_to_sheet(
                        sheet,
                        amount=int(damage_roll.total),
                        damage_type="poison",
                        source=f"poison:{effect_id}:recurring:{due}",
                        ruleset="2014",
                        death_saves=actor.get("character_type") == "pc",
                    )
                    sheet = damage["sheet"]
                    if profile.healing_locked_damage:
                        next_effect = record_poison_healing_lock(
                            next_effect,
                            hp_damage=int(damage.get("hp_damage", 0) or 0),
                        )
                    damage_result = {
                        **{key: value for key, value in damage.items() if key != "sheet"},
                        "expression": profile.recurring_damage,
                        "rolls": list(damage_roll.rolls),
                        "detail": damage_roll.detail,
                    }
                sheet["effects"][index] = next_effect
                events.append(
                    {
                        "actor_id": actor_id,
                        "effect_id": effect_id,
                        "poison_id": profile.id,
                        "due_elapsed_ticks": due,
                        "save": support.deepcopy(save),
                        "damage": damage_result,
                        "successes": int(
                            dict(
                                dict(next_effect.get("metadata") or {}).get("poison_state") or {}
                            ).get("successes", 0)
                        ),
                        "ended": not bool(next_effect.get("active")),
                    }
                )
            sheets[actor_id] = support.validate_character_sheet(sheet)
            changed.add(actor_id)
            receipts.extend(
                support.core_receipts(
                    rule_context,
                    [POISON_MECHANIC_ID],
                    "combat.poison.clock_settlement",
                )
            )
            collect_due()

        updated = {item.character_id: item for item in updates}
        for actor_id in changed:
            record = records[actor_id]
            updated[actor_id] = support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=sheets[actor_id],
                notes=support.validate_character_notes(record.notes),
                expected_revision=record.revision,
            )
        return [updated[actor_id] for actor_id in sorted(updated)], events, receipts

    def poison_turn_events_due(self, actor_id: str, phase: str) -> bool:
        record = self.characters.get(actor_id)
        expected_period = {
            "start_of_turn": "start_of_turn",
            "end_of_turn": "end_of_turn_save",
        }.get(phase)
        if expected_period is None:
            return False
        for effect in record.sheet.get("effects", []):
            state = _source_poison_state(effect)
            if state is None or effect_is_suspended_by_petrification(record.sheet, effect):
                continue
            if poison_profile(state.get("poison_id")).recurring_period == expected_period:
                return True
        return False

    def settle_poison_turn_events(
        self,
        campaign,
        campaign_id: str,
        actor_id: str,
        sheet: dict[str, Any],
        *,
        phase: str,
        branch_id: str,
        encounter: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        expected_period = {
            "start_of_turn": "start_of_turn",
            "end_of_turn": "end_of_turn_save",
        }.get(phase)
        if expected_period is None:
            return sheet, [], []
        current = support.deepcopy(sheet)
        events: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        for effect_id in sorted(str(item.get("id") or "") for item in current.get("effects", [])):
            index = next(
                (
                    i
                    for i, item in enumerate(current.get("effects", []))
                    if str(item.get("id") or "") == effect_id
                ),
                None,
            )
            if index is None:
                continue
            effect = current["effects"][index]
            poison_state = _source_poison_state(effect)
            if poison_state is None or effect_is_suspended_by_petrification(current, effect):
                continue
            profile = poison_profile(poison_state.get("poison_id"))
            if profile.recurring_period != expected_period:
                continue
            source_actor_id = str(poison_state.get("source_actor_id") or actor_id)
            rule_context = _rules(
                self,
                campaign_id,
                branch_id,
                source_actor_id,
                actor_id,
                profile,
            )
            actor = self.combat_actor_snapshot(actor_id)
            actor["sheet"] = support.deepcopy(current)
            actor["derived"] = derive_character_sheet(current)
            save = support.resolve_actor_check(
                actor,
                kind="save",
                ability="constitution",
                dc=profile.save_dc,
                encounter=encounter,
                rules=rule_context,
                rng=support.active_random_stream(),
                ruleset="2014",
            )
            outcome = settle_poison_repeat_save(
                effect,
                save_succeeded=bool(save.get("success")),
                elapsed_ticks=int(
                    dict(campaign.state or {}).get("game_time", {}).get("elapsed_ticks", 0)
                ),
            )
            next_effect = outcome["effect"]
            damage_result = None
            if not save.get("success") and profile.recurring_damage:
                damage_roll = roll(profile.recurring_damage, rng=support.active_random_stream())
                damage = support.apply_damage_to_sheet(
                    current,
                    amount=int(damage_roll.total),
                    damage_type="poison",
                    source=f"poison:{effect_id}:turn:{phase}",
                    ruleset="2014",
                    death_saves=actor.get("character_type") == "pc",
                )
                current = damage["sheet"]
                if profile.healing_locked_damage:
                    next_effect = record_poison_healing_lock(
                        next_effect,
                        hp_damage=int(damage.get("hp_damage", 0) or 0),
                    )
                damage_result = {
                    **{key: value for key, value in damage.items() if key != "sheet"},
                    "expression": profile.recurring_damage,
                    "rolls": list(damage_roll.rolls),
                    "detail": damage_roll.detail,
                }
            if not save.get("success") or profile.id != "crawler_mucus":
                current["effects"][index] = next_effect
            else:
                current = support.remove_effect(current, effect_id)
            next_state = dict(dict(next_effect.get("metadata") or {}).get("poison_state") or {})
            events.append(
                {
                    "actor_id": actor_id,
                    "effect_id": effect_id,
                    "poison_id": profile.id,
                    "phase": phase,
                    "save": support.deepcopy(save),
                    "damage": damage_result,
                    "successes": int(next_state.get("successes", 0) or 0),
                    "ended": outcome["ended"],
                }
            )
            receipts.extend(
                support.core_receipts(
                    rule_context,
                    [POISON_MECHANIC_ID],
                    f"combat.poison.{phase}",
                )
            )
        return support.validate_character_sheet(current), events, receipts

    def combat_poison_neutralize(
        self,
        campaign_id: str,
        actor_id: str,
        poison_effect_id: str,
        neutralizer_source_ref: str,
        neutralizer_source_excerpt: str,
        ruling: dict[str, Any],
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Remove one exact poison instance after a recorded DM neutralization ruling."""

        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        source_ref = str(neutralizer_source_ref or "").strip()
        excerpt = " ".join(str(neutralizer_source_excerpt or "").split())
        normalized_ruling = dict(ruling or {})
        if not source_ref or len(source_ref) > 500:
            raise support.CombatEngineError("neutralization requires an exact source reference")
        if not excerpt or len(excerpt) > 5_000:
            raise support.CombatEngineError("neutralization requires a bounded source excerpt")
        if set(normalized_ruling) != {
            "default_resolver",
            "ruling_kind",
            "decision",
            "reason",
        }:
            raise support.CombatEngineError(
                "neutralization ruling requires exactly resolver, kind, decision, and reason"
            )
        decision = " ".join(str(normalized_ruling.get("decision") or "").split())
        reason = " ".join(str(normalized_ruling.get("reason") or "").split())
        if (
            normalized_ruling.get("default_resolver") != "agent"
            or normalized_ruling.get("ruling_kind") != "agent_dm_adjudication"
            or decision != "neutralize_exact_poison_instance"
            or not reason
            or len(reason) > 1_000
        ):
            raise support.CombatEngineError(
                "neutralization requires a committed Agent adjudication for this exact instance"
            )
        normalized_ruling = {
            "default_resolver": "agent",
            "ruling_kind": "agent_dm_adjudication",
            "decision": decision,
            "reason": reason,
            "committed": True,
        }
        payload = {
            "actor_id": actor_id,
            "poison_effect_id": poison_effect_id,
            "neutralizer_source_ref": source_ref,
            "neutralizer_source_excerpt": excerpt,
            "ruling": normalized_ruling,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-poison-neutralize:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        encounter = dict(state.get("combat") or {})
        if encounter.get("active"):
            self.require_no_blocking_pending(encounter)
            self.require_encounter_combatant(encounter, actor_id, role="poison target")
        record = self.require_campaign_actor(campaign_id, actor_id)
        effect = next(
            (
                item
                for item in record.sheet.get("effects", [])
                if str(item.get("id") or "") == poison_effect_id
            ),
            None,
        )
        poison_state = _source_poison_state(effect or {})
        if effect is None or poison_state is None:
            raise support.CombatEngineError(
                "neutralization must name one active source-bound 2014 poison instance"
            )
        profile = poison_profile(poison_state.get("poison_id"))
        sheet = support.remove_effect(record.sheet, poison_effect_id)
        if encounter.get("active"):
            self.sync_combatant_conditions(encounter, actor_id, sheet)
            state["combat"] = encounter
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_id": actor_id,
                "poison_id": profile.id,
                "poison_effect_id": poison_effect_id,
                "neutralizer_source_ref": source_ref,
                "neutralization_decision": decision,
            },
        )
        receipts = support.core_receipts(
            rules,
            [POISON_MECHANIC_ID],
            "combat.poison.neutralization",
        )
        response = self.commit_campaign_state(
            campaign,
            state,
            operation="combat.poison.neutralization",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "neutralized_effect_id": poison_effect_id,
                "poison_id": profile.id,
                "neutralization_source": {
                    "source_ref": source_ref,
                    "source_excerpt": excerpt,
                    "ruling": normalized_ruling,
                },
            },
            character_updates=[
                support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=support.validate_character_sheet(sheet),
                    notes=support.validate_character_notes(record.notes),
                    expected_revision=record.revision,
                )
            ],
            rule_receipts=receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_poison_coat(
        self,
        campaign_id: str,
        actor_id: str,
        dose_item_id: str,
        object_item_id: str,
        ammunition_count: int = 1,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Consume one source-bound contact/injury dose and coat one owned object."""

        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "dose_item_id": dose_item_id,
            "object_item_id": object_item_id,
            "ammunition_count": ammunition_count,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-poison-coat:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        encounter = dict(dict(campaign.state or {}).get("combat") or {})
        if encounter.get("active"):
            self.require_no_blocking_pending(encounter)
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError(
                "source-bound 2014 poison coating requires a 2014 campaign"
            )
        record = self.require_campaign_actor(campaign_id, actor_id)
        if str(record.sheet.get("edition") or "") != "2014":
            raise support.CombatEngineError(
                "source-bound 2014 poison coating requires a 2014 actor"
            )
        dose, profile = _dose_profile(record.sheet, dose_item_id)
        if profile.delivery not in {"contact", "injury"}:
            raise support.CombatEngineError("only contact or injury poison can coat an object")
        if dose_item_id == object_item_id:
            raise support.CombatEngineError("a poison dose cannot coat itself")
        sheet = support.deepcopy(record.sheet)
        coated = _find_inventory_item(sheet, object_item_id)
        if coated is None:
            raise support.CombatEngineError("coated object must be in the applying actor inventory")
        if (
            isinstance(ammunition_count, bool)
            or not isinstance(ammunition_count, int)
            or ammunition_count < 1
        ):
            raise support.CombatEngineError("ammunition_count must be a positive integer")
        if profile.id != "basic_poison" and ammunition_count != 1:
            raise support.CombatEngineError("sample poisons coat exactly one object per dose")
        if profile.delivery == "injury" and coated.get("kind") not in {"weapon", "ammunition"}:
            raise support.CombatEngineError("injury poison must coat a piercing/slashing object")
        if profile.id == "basic_poison" and coated.get("kind") == "weapon":
            if ammunition_count != 1:
                raise support.CombatEngineError("Basic Poison coats one weapon at a time")
        if profile.id == "basic_poison" and coated.get("kind") == "ammunition":
            if ammunition_count > 3:
                raise support.CombatEngineError(
                    "Basic Poison can coat at most three ammunition pieces"
                )
            if int(coated.get("quantity", 1) or 0) < ammunition_count:
                raise support.CombatEngineError(
                    "ammunition stack does not contain the requested pieces"
                )
        if profile.delivery == "injury" and coated.get("kind") == "weapon":
            if str(dict(coated.get("mechanics") or {}).get("damage_type") or "").casefold() not in {
                "piercing",
                "slashing",
            }:
                raise support.CombatEngineError(
                    "injury poison weapon must deal piercing or slashing damage"
                )

        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        coatings = state["poison_coatings"]["coatings"]
        elapsed_ticks = int(state["game_time"]["elapsed_ticks"])
        for existing in coatings:
            if not existing["active"]:
                continue
            existing_profile = poison_profile(existing["poison_id"])
            if (
                existing_profile.duration_ticks
                and elapsed_ticks
                >= int(existing["created_at_elapsed_ticks"]) + existing_profile.duration_ticks
            ):
                existing["active"] = False
        if any(
            item["active"]
            and item["object_actor_id"] == actor_id
            and item["object_item_id"] == object_item_id
            for item in coatings
        ):
            raise support.CombatEngineError("object already has an active poison coating")

        object_ids = [object_item_id]
        stack_quantity = int(coated.get("quantity", 1) or 0)
        if coated.get("kind") == "ammunition" and stack_quantity > 1:
            pieces = ammunition_count if profile.id == "basic_poison" else 1
            if stack_quantity == pieces:
                coated["quantity"] = 1
                object_ids = [object_item_id]
                for _ in range(pieces - 1):
                    split = support.deepcopy(coated)
                    split.update(id=support.uuid4().hex, quantity=1)
                    sheet["inventory"]["items"].append(split)
                    object_ids.append(split["id"])
            else:
                coated["quantity"] = stack_quantity - pieces
                object_ids = []
                for _ in range(pieces):
                    split = support.deepcopy(coated)
                    split.update(id=support.uuid4().hex, quantity=1)
                    sheet["inventory"]["items"].append(split)
                    object_ids.append(split["id"])
        applied_coatings = [
            {
                "id": support.uuid4().hex,
                "poison_id": profile.id,
                "object_actor_id": actor_id,
                "object_item_id": actual_object_id,
                "applied_by_actor_id": actor_id,
                "dose_item_id": dose_item_id,
                "source_key": str(dose["source_key"]),
                "source_ref": profile.source_ref,
                "created_at_elapsed_ticks": elapsed_ticks,
                "active": True,
            }
            for actual_object_id in object_ids
        ]
        coatings.extend(applied_coatings)
        _consume_one_item(sheet, dose_item_id)
        normalized_sheet = support.validate_character_sheet(sheet)
        state["poison_coatings"] = {"schema_version": 1, "coatings": coatings}
        action_paid = False
        if profile.id == "basic_poison" and encounter.get("active"):
            state["combat"] = support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="use_object",
                payload={"source": "basic_poison", "dose_item_id": dose_item_id},
            )
            action_paid = True
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_id": actor_id,
                "poison_id": profile.id,
                "object_item_ids": object_ids,
            },
        )
        receipts = support.core_receipts(
            rules,
            [_mechanic_id(profile)],
            "combat.poison.coating",
        )
        response = self.commit_campaign_state(
            campaign,
            state,
            operation="combat.poison.coating",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "coating": applied_coatings[0],
                "coatings": applied_coatings,
                "object_item_ids": object_ids,
                "consumed_dose_item_id": dose_item_id,
                **(
                    {"action_cost": "action", "action_paid": action_paid}
                    if profile.id == "basic_poison"
                    else {}
                ),
            },
            character_updates=[
                support.CharacterStateUpdate(
                    character_id=actor_id,
                    sheet=normalized_sheet,
                    notes=support.validate_character_notes(record.notes),
                    expected_revision=record.revision,
                )
            ],
            rule_receipts=receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_poison_wash(
        self,
        campaign_id: str,
        actor_id: str,
        object_item_id: str,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Wash one source-bound poison coating from its exact owned object."""

        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "object_item_id": object_item_id,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-poison-wash:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        encounter = dict(dict(campaign.state or {}).get("combat") or {})
        if encounter.get("active"):
            self.require_no_blocking_pending(encounter)
        record = self.require_campaign_actor(campaign_id, actor_id)
        if _find_inventory_item(record.sheet, object_item_id) is None:
            raise support.CombatEngineError("washed object is not in the named actor inventory")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        matches = [
            item
            for item in state["poison_coatings"]["coatings"]
            if item["active"]
            and item["object_actor_id"] == actor_id
            and item["object_item_id"] == object_item_id
        ]
        if len(matches) != 1:
            raise support.CombatEngineError(
                "exactly one active poison coating must match the object"
            )
        coating = matches[0]
        profile = poison_profile(coating["poison_id"])
        if profile.id == "basic_poison":
            raise support.CombatEngineError("Basic Poison has no source-defined wash-off rule")
        coating["active"] = False
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_id": actor_id,
                "poison_id": coating["poison_id"],
                "object_item_id": object_item_id,
            },
        )
        receipts = support.core_receipts(
            rules,
            [_mechanic_id(profile)],
            "combat.poison.wash_off",
        )
        response = self.commit_campaign_state(
            campaign,
            state,
            operation="combat.poison.wash_off",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={"status": "committed", "washed_coating_id": coating["id"]},
            rule_receipts=receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)

    def combat_poison_expose(
        self,
        campaign_id: str,
        source_actor_id: str,
        target_ids: list[str] | None = None,
        dose_item_id: str | None = None,
        coating_id: str | None = None,
        swallowed_entire_dose: bool | None = None,
        partial_ruling: Literal["advantage_on_save", "half_damage_on_failure"] | None = None,
        exposed_skin_touch: bool | None = None,
        cube_origin: list[int] | None = None,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Deliver one ingested/contact dose or release one inhaled 5-foot cloud."""

        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        normalized_targets = [str(value) for value in list(target_ids or [])]
        payload = {
            "source_actor_id": source_actor_id,
            "target_ids": normalized_targets,
            "dose_item_id": dose_item_id,
            "coating_id": coating_id,
            "swallowed_entire_dose": swallowed_entire_dose,
            "partial_ruling": partial_ruling,
            "exposed_skin_touch": exposed_skin_touch,
            "cube_origin": cube_origin,
            "branch_id": resolved_branch_id,
        }
        scope = f"combat-poison-expose:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return self.combat_response(campaign_id, principal_id, replay)
        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("source-bound 2014 poison requires a 2014 campaign")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        encounter = dict(state.get("combat") or {})
        if encounter.get("active"):
            self.require_no_blocking_pending(encounter)
        source = self.require_campaign_actor(campaign_id, source_actor_id)
        if str(source.sheet.get("edition") or "") != "2014":
            raise support.CombatEngineError("source-bound 2014 poison requires a 2014 source actor")
        partial = None
        coating = None
        dose_copy = support.deepcopy(source.sheet)
        dose_item = None
        if coating_id:
            if dose_item_id:
                raise support.CombatEngineError("contact delivery uses a coating, not a new dose")
            coating = _active_coating(state, coating_id)
            if coating is None or coating["object_actor_id"] != source_actor_id:
                raise support.CombatEngineError(
                    "active coating is not owned by the named source actor"
                )
            profile = poison_profile(coating["poison_id"])
            if profile.delivery != "contact":
                raise support.CombatEngineError(
                    "only contact poison can be delivered by skin touch"
                )
            if len(normalized_targets) != 1 or exposed_skin_touch is None:
                raise support.CombatEngineError(
                    "contact poison requires one target and an exposed-skin fact"
                )
            if not contact_exposes(same_smeared_object=True, exposed_skin_touch=exposed_skin_touch):
                response = self.remember_idempotent(
                    scope,
                    idempotency_key,
                    payload,
                    {
                        "status": "no_exposure",
                        "poison_id": profile.id,
                        "dose_consumed": False,
                    },
                    campaign_id=campaign_id,
                )
                return self.combat_response(campaign_id, principal_id, response)
            targets = normalized_targets
        else:
            if not dose_item_id:
                raise support.CombatEngineError("poison delivery requires a source-bound dose item")
            dose_item, profile = _dose_profile(source.sheet, dose_item_id)
            if profile.delivery == "ingested":
                if len(normalized_targets) != 1:
                    raise support.CombatEngineError("ingested poison requires exactly one target")
                partial = validate_ingested_delivery(
                    swallowed_entire_dose=swallowed_entire_dose,
                    partial_ruling=partial_ruling,
                )
                if partial == "whole_dose":
                    partial = None
                targets = normalized_targets
            elif profile.delivery == "inhaled":
                if normalized_targets or cube_origin is None or len(cube_origin) != 2:
                    raise support.CombatEngineError(
                        "inhaled poison requires a grid cube origin; "
                        "target IDs are derived from the map"
                    )
                if (
                    not encounter
                    or encounter.get("ruleset") != "2014"
                    or encounter.get("positioning_mode") != "grid"
                ):
                    raise support.NeedsRulingError(
                        "inhaled poison needs an authoritative 2014 grid encounter",
                        missing=("poison.inhaled.grid",),
                        ruling_kind="source_or_scene_fact",
                    )
                origin = tuple(cube_origin)
                if any(isinstance(value, bool) or not isinstance(value, int) for value in origin):
                    raise support.CombatEngineError(
                        "inhaled cube origin must use integer grid cells"
                    )
                targets = []
                for combatant in encounter.get("combatants", []):
                    actor_id = str(combatant.get("actor_id") or "")
                    coordinates = self.combat_coordinates(combatant.get("position"))
                    if not actor_id or coordinates is None:
                        continue
                    if (
                        int(coordinates[0]) == origin[0]
                        and int(coordinates[1]) == origin[1]
                        and coordinates[0].is_integer()
                        and coordinates[1].is_integer()
                    ):
                        targets.append(actor_id)
                targets = sorted(set(targets))
            else:
                raise support.CombatEngineError(
                    "contact and injury poisons must be coated before exposure"
                )

        target_records = _target_records(self, campaign_id, targets) if targets else []
        dose_update = None
        if dose_item is not None:
            _consume_one_item(dose_copy, dose_item_id)
            dose_update = support.CharacterStateUpdate(
                character_id=source_actor_id,
                sheet=support.validate_character_sheet(dose_copy),
                notes=support.validate_character_notes(source.notes),
                expected_revision=source.revision,
            )
        if coating is not None:
            coating["active"] = False

        if profile.id == "midnight_tears":
            if len(target_records) != 1:
                raise support.CombatEngineError("Midnight Tears requires exactly one target")
            target_record = target_records[0]
            target_sheet = (
                dose_copy
                if dose_item is not None and source_actor_id == target_record.id
                else target_record.sheet
            )
            effect = build_midnight_tears_effect(
                effect_id=support.uuid4().hex,
                target_actor_id=target_record.id,
                elapsed_ticks=int(state["game_time"]["elapsed_ticks"]),
                world_time=dict(state.get("world_time") or {}),
            )
            next_sheet, _ = support.add_effect(target_sheet, effect)
            midnight_update = support.CharacterStateUpdate(
                character_id=target_record.id,
                sheet=next_sheet,
                notes=support.validate_character_notes(target_record.notes),
                expected_revision=target_record.revision,
            )
            results = [
                {
                    "target_actor_id": target_record.id,
                    "status": "pending_midnight",
                    "effect": effect,
                }
            ]
            receipts = support.core_receipts(
                self.effective_rule_context(campaign_id, branch_id=resolved_branch_id),
                [POISON_MECHANIC_ID],
                "combat.poison.ingestion",
            )
        else:
            needs_roll = bool(target_records)
            if needs_roll and support.active_random_stream() is None:
                with self.campaign_random_context(
                    campaign_id,
                    "combat.poison.exposure",
                    {"idempotency_key": idempotency_key},
                ):
                    return self.combat_poison_expose(
                        campaign_id,
                        source_actor_id,
                        target_ids=target_ids,
                        dose_item_id=dose_item_id,
                        coating_id=coating_id,
                        swallowed_entire_dose=swallowed_entire_dose,
                        partial_ruling=partial_ruling,
                        exposed_skin_touch=exposed_skin_touch,
                        cube_origin=cube_origin,
                        principal_id=principal_id,
                        expected_revision=expected_revision,
                        branch_id=resolved_branch_id,
                        idempotency_key=idempotency_key,
                    )
            results = []
            receipts = []
            updates_by_actor = {}
            for record in target_records:
                base_sheet = (
                    dose_copy
                    if dose_item is not None and source_actor_id == record.id
                    else record.sheet
                )
                next_sheet, result, target_receipts = _settle_target(
                    self,
                    campaign,
                    encounter if encounter else None,
                    record,
                    base_sheet,
                    profile=profile,
                    source_actor_id=source_actor_id,
                    branch_id=resolved_branch_id,
                    partial_ruling=partial,
                )
                updates_by_actor[record.id] = support.CharacterStateUpdate(
                    character_id=record.id,
                    sheet=next_sheet,
                    notes=support.validate_character_notes(record.notes),
                    expected_revision=record.revision,
                )
                results.append(result)
                receipts.extend(target_receipts)
            if dose_update is not None and source_actor_id not in updates_by_actor:
                updates_by_actor[source_actor_id] = dose_update
            target_records = [updates_by_actor[key] for key in sorted(updates_by_actor)]

        if profile.id == "midnight_tears":
            updates = [midnight_update]
            if dose_update is not None and source_actor_id != midnight_update.character_id:
                updates.append(dose_update)
        else:
            updates = target_records
        response_fields = {
            "status": "committed",
            "poison_id": profile.id,
            "delivery": profile.delivery,
            "targets": results,
            "dose_consumed": dose_item is not None or coating is not None,
            "cloud_dissipated": profile.delivery == "inhaled",
        }
        response = self.commit_campaign_state(
            campaign,
            state,
            operation="combat.poison.delivery",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields=response_fields,
            character_updates=updates,
            rule_receipts=receipts,
        )
        return self.combat_response(campaign_id, principal_id, response)


def settle_injury_coating(
    runtime,
    campaign,
    encounter: dict[str, Any],
    *,
    attacker_record,
    target_record,
    updated_attacker: dict[str, Any],
    updated_target: dict[str, Any],
    plan: dict[str, Any],
    attack: dict[str, Any],
    result: dict[str, Any],
    branch_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Consume an injury coating only after its exact object causes a wound."""

    if not attack.get("hit"):
        return updated_attacker, updated_target, None, []
    damage = dict(result.get("damage") or {})
    damage_type = str(plan.get("damage_type") or "").casefold()
    actual = int(damage.get("applied_amount", 0) or 0)
    if damage.get("parts"):
        actual = sum(
            int(part.get("applied_amount", 0) or 0)
            for part in damage["parts"]
            if str(part.get("damage_type") or "").casefold() == damage_type
        )
    object_id = str(plan.get("ammunition_item_id") or plan.get("weapon_id") or "")
    if not object_id:
        return updated_attacker, updated_target, None, []
    state = support.validate_party_state(support.deepcopy(campaign.state or {}))
    coatings = state["poison_coatings"]["coatings"]
    matches = [
        item
        for item in coatings
        if item["active"]
        and item["object_actor_id"] == attacker_record.id
        and item["object_item_id"] == object_id
    ]
    if not matches:
        return updated_attacker, updated_target, None, []
    if len(matches) != 1:
        raise support.CombatEngineError("attack object has ambiguous injury poison coatings")
    coating = matches[0]
    profile = poison_profile(coating["poison_id"])
    if profile.delivery != "injury":
        return updated_attacker, updated_target, None, []
    elapsed_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0))
    if profile.duration_ticks and elapsed_ticks >= (
        int(coating["created_at_elapsed_ticks"]) + profile.duration_ticks
    ):
        coating["active"] = False
        state["poison_coatings"] = {"schema_version": 1, "coatings": coatings}
        return updated_attacker, updated_target, state, []
    if profile.id == "basic_poison":
        if str(plan.get("ammunition_item_id") or "") == object_id:
            coating["active"] = False
        target_sheet, poison_result, receipts = _settle_basic_poison_hit(
            runtime,
            campaign,
            encounter,
            target_record,
            updated_target["sheet"],
            source_actor_id=attacker_record.id,
            branch_id=branch_id,
        )
        updated_target["sheet"] = target_sheet
        result["poison"] = poison_result
        result.setdefault("rule_receipts", []).extend(receipts)
        state["poison_coatings"] = {"schema_version": 1, "coatings": coatings}
        return updated_attacker, updated_target, state, receipts
    if damage_type not in {"piercing", "slashing"} or actual <= 0:
        return updated_attacker, updated_target, None, []
    if not injury_exposes(
        coated_object_id=object_id,
        damaging_object_id=object_id,
        damage_type=damage_type,
        damage_applied=actual,
    ):
        return updated_attacker, updated_target, None, []
    coating["active"] = False
    target_sheet, poison_result, receipts = _settle_target(
        runtime,
        campaign,
        encounter,
        target_record,
        updated_target["sheet"],
        profile=profile,
        source_actor_id=attacker_record.id,
        branch_id=branch_id,
    )
    updated_target["sheet"] = target_sheet
    result["poison"] = poison_result
    result.setdefault("rule_receipts", []).extend(receipts)
    state["poison_coatings"] = {"schema_version": 1, "coatings": coatings}
    return updated_attacker, updated_target, state, receipts


def settle_missed_ammunition_coating(
    runtime,
    campaign,
    *,
    attacker_id: str,
    ammunition_item_id: str,
    branch_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Retire poison attached to the exact ammunition consumed by a missed attack."""

    if not ammunition_item_id:
        return None, None, []
    state = support.validate_party_state(support.deepcopy(campaign.state or {}))
    matches = [
        item
        for item in state["poison_coatings"]["coatings"]
        if item["active"]
        and item["object_actor_id"] == attacker_id
        and item["object_item_id"] == ammunition_item_id
    ]
    if not matches:
        return None, None, []
    if len(matches) != 1:
        raise support.CombatEngineError("consumed ammunition has ambiguous poison coatings")
    coating = matches[0]
    coating["active"] = False
    profile = poison_profile(coating["poison_id"])
    rules = runtime.effective_rule_context(
        campaign.id,
        branch_id=branch_id,
        facts={
            "poison_id": profile.id,
            "coating_id": coating["id"],
            "object_actor_id": attacker_id,
            "object_item_id": ammunition_item_id,
            "attack_hit": False,
            "ammunition_consumed": True,
        },
    )
    receipts = support.core_receipts(
        rules,
        [_mechanic_id(profile)],
        "combat.poison.ammunition_miss",
    )
    event = {
        "status": "not_delivered",
        "reason": "ammunition_consumed_on_miss",
        "poison_id": profile.id,
        "coating_id": coating["id"],
        "object_item_id": ammunition_item_id,
    }
    state["poison_coatings"] = {
        "schema_version": 1,
        "coatings": state["poison_coatings"]["coatings"],
    }
    return state, event, receipts
