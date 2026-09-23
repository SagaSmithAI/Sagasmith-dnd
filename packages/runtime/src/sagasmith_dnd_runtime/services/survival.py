"""Campaign-day settlement for source-bound 2014 food and water rules."""

from __future__ import annotations

from typing import Any

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.game_time import TICKS_PER_DAY
from sagasmith_dnd.survival import needs_water_save, settle_survival_day

from .. import application_support as support


def _settlement_day(campaign, campaign_state: dict[str, Any] | None) -> int | None:
    if campaign_state is None:
        return None
    before = int(dict(campaign.state or {}).get("game_time", {}).get("elapsed_ticks", 0))
    after = int(dict(campaign_state.get("game_time") or {}).get("elapsed_ticks", 0))
    if after < before:
        raise support.CombatEngineError("campaign survival cannot reverse game time")
    if after == before:
        return None
    return after // TICKS_PER_DAY - 1


def needs_survival_save(runtime, campaign, campaign_state, character_updates) -> bool:
    day = _settlement_day(campaign, campaign_state)
    if day is None:
        return False
    state = support.validate_party_state(campaign_state)
    survival = state["survival"]
    if day <= survival["last_settled_day"]:
        return False
    if day - survival["last_settled_day"] != 1:
        return False
    day_entries = survival["daily_intakes"].get(str(day), {})
    updates = {str(item.character_id): item for item in character_updates or []}
    for actor in runtime.characters.list(campaign_id=campaign.id):
        update = updates.get(actor.id)
        sheet = update.sheet if update is not None else actor.sheet
        if sheet.get("edition") != "2014":
            continue
        intake = day_entries.get(actor.id)
        if intake is not None and needs_water_save(intake):
            return True
    return False


def reconcile_survival_days(
    runtime, campaign, campaign_state, character_updates, response_fields, branch_id
):
    day = _settlement_day(campaign, campaign_state)
    if day is None:
        return campaign_state, list(character_updates or []), response_fields, []
    next_state = support.validate_party_state(support.deepcopy(campaign_state))
    survival = next_state["survival"]
    if day <= survival["last_settled_day"]:
        return campaign_state, list(character_updates or []), response_fields, []
    if day - survival["last_settled_day"] != 1:
        raise support.NeedsRulingError(
            "campaign time skipped an unsettled survival day; settle each day separately",
            missing=("survival.daily_intake",),
            ruling_kind="source_or_scene_fact",
        )

    day_entries = survival["daily_intakes"].get(str(day), {})
    updates = list(character_updates or [])
    update_by_actor = {str(item.character_id): item for item in updates}
    records = {item.id: item for item in runtime.characters.list(campaign_id=campaign.id)}
    actors = [item for item in records.values() if item.sheet.get("edition") == "2014"]
    missing = sorted(item.id for item in actors if item.id not in day_entries)
    if missing:
        raise support.NeedsRulingError(
            "every 2014 actor needs a reported daily food and water intake",
            missing=tuple(f"survival.daily_intake.{actor_id}" for actor_id in missing),
            ruling_kind="source_or_scene_fact",
        )

    settlements = []
    for actor in actors:
        current = records[actor.id]
        update = update_by_actor.get(actor.id)
        sheet = support.deepcopy(update.sheet if update is not None else current.sheet)
        intake = day_entries[actor.id]
        water_save = None
        if needs_water_save(intake):
            stream = support.active_random_stream()
            if stream is None:
                raise support.CombatEngineError(
                    "half-water survival save requires the campaign random stream"
                )
            snapshot = runtime.combat_actor_snapshot(actor.id)
            snapshot["sheet"] = support.deepcopy(sheet)
            snapshot["derived"] = derive_character_sheet(sheet)
            rules = runtime.effective_rule_context(
                campaign.id,
                facts={"actor_id": actor.id, "game_day": day + 1},
                branch_id=branch_id,
            )
            water_save = support.resolve_actor_check(
                snapshot,
                kind="save",
                ability="constitution",
                dc=15,
                encounter=next_state.get("combat"),
                rules=rules,
                rng=stream,
                ruleset="2014",
            )
        actor_state = survival["actors"].get(actor.id, {})
        result = settle_survival_day(
            sheet,
            actor_state,
            intake,
            day_index=day,
            water_save=water_save,
        )
        survival["actors"][actor.id] = result["actor_state"]
        settlements.append({
            "actor_id": actor.id,
            **{key: value for key, value in result.items()
               if key not in {"sheet", "actor_state"}},
        })
        if result["sheet"] != (update.sheet if update is not None else current.sheet):
            replacement = support.CharacterStateUpdate(
                character_id=actor.id,
                sheet=result["sheet"],
                notes=(update.notes if update is not None else current.notes),
                expected_revision=(
                    update.expected_revision if update is not None else current.revision
                ),
            )
            if update is None:
                updates.append(replacement)
            else:
                updates[updates.index(update)] = replacement
            update_by_actor[actor.id] = replacement
    survival["last_settled_day"] = day
    next_state["survival"] = survival
    response = {
        **dict(response_fields),
        "survival_settlements": settlements,
    }
    rules = runtime.effective_rule_context(
        campaign.id,
        facts={"game_day": day + 1, "settlement": "food_and_water"},
        branch_id=branch_id,
    )
    receipts = support.core_receipts(
        rules,
        ["dnd5e.core.adventuring.food_water_2014"],
        "campaign.survival.day_settlement",
    )
    response["survival_rule_receipts"] = receipts
    return next_state, updates, response, receipts
