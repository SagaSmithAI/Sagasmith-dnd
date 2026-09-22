"""Resume an interrupted move inside the reaction's existing atomic commit."""

from sagasmith_dnd.movement_continuations import resume_pending_movement

from .. import application_support as support


def reconcile_movement(runtime, campaign, campaign_state, character_updates, response_fields):
    encounter = (campaign_state or {}).get("combat")
    if not isinstance(encounter, dict) or not encounter.get("movement_continuation"):
        return campaign_state, character_updates, response_fields
    original = encounter
    updates = list(character_updates or [])
    encounter = support.deepcopy(encounter)
    for update in updates:
        runtime.sync_combatant_conditions(encounter, update.character_id, update.sheet)
    resumed = resume_pending_movement(encounter)
    if resumed == original:
        return campaign_state, updates, response_fields
    by_actor = {update.character_id: update for update in updates}
    ended = support.newly_ended_witch_bolt_tethers(encounter, resumed)
    for actor_id in sorted({str(item["source_actor_id"]) for item in ended}):
        current = runtime.characters.get(actor_id)
        existing = by_actor.get(actor_id)
        sheet = existing.sheet if existing is not None else current.sheet
        result = support.end_tether_concentrations(
            sheet, [item for item in ended if item["source_actor_id"] == actor_id]
        )
        if result["sheet"] == sheet:
            continue
        sheet = support.validate_character_sheet(result["sheet"])
        runtime.sync_combatant_conditions(resumed, actor_id, sheet)
        replacement = (
            support.replace(existing, sheet=sheet)
            if existing is not None
            else support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=sheet,
                notes=support.validate_character_notes(current.notes),
                expected_revision=current.revision,
            )
        )
        if existing is not None:
            updates[updates.index(existing)] = replacement
        else:
            updates.append(replacement)
    response = {**response_fields, "combat": resumed}
    if any(
        item.get("trigger") == "opportunity_attack"
        and item.get("id") == (resumed.get("movement_continuation") or {}).get("choice_id")
        for item in resumed.get("pending", [])
    ):
        response["status"] = "pending_reaction"
    response["movement_outcome"] = next(
        (
            item
            for item in reversed(resumed.get("log", []))
            if item.get("type") == "movement_continuation"
        ),
        None,
    )
    if response["movement_outcome"] is not None:
        response["movement_rule_receipts"] = support.core_receipts(
            runtime.effective_rule_context(campaign.id),
            [
                "dnd5e.core.reaction.opportunity_path",
                *([support.CORE_WITCH_BOLT_MECHANIC_ID] if ended else []),
            ], "movement.resume",
        )
    return {**campaign_state, "combat": resumed}, updates, response
