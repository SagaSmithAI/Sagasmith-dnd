"""Resume an interrupted move inside the reaction's existing atomic commit."""

from sagasmith_dnd.movement_continuations import resume_pending_movement

from .. import application_support as support


def reconcile_movement(runtime, campaign, campaign_state, character_updates, response_fields):
    encounter = (campaign_state or {}).get("combat")
    if not isinstance(encounter, dict) or not (
        encounter.get("movement_continuation") or encounter.get("jump_landing_check_due")
    ):
        return campaign_state, character_updates, response_fields
    original = encounter
    updates = list(character_updates or [])
    changed_actor_ids = {
        update.character_id for update in updates
        if update.sheet != runtime.characters.get(update.character_id).sheet
    }
    encounter = support.deepcopy(encounter)
    if encounter.get("ruleset") == "2014":
        by_actor = {update.character_id: update for update in updates}
        for participant in encounter["combatants"]:
            actor_id = participant["actor_id"]
            if actor_id not in by_actor:
                current = runtime.characters.get(actor_id)
                updates.append(support.CharacterStateUpdate(
                    character_id=actor_id, sheet=support.deepcopy(current.sheet),
                    notes=current.notes, expected_revision=current.revision,
                ))
    for update in updates:
        if update.character_id in changed_actor_ids:
            runtime.sync_combatant_conditions(encounter, update.character_id, update.sheet)
        else:
            runtime.sync_combatant_spaces(encounter, update.character_id, update.sheet)
    resumed = (
        resume_pending_movement(encounter)
        if encounter.get("movement_continuation")
        else encounter
    )
    if resumed == original and not resumed.get("jump_landing_check_due"):
        return campaign_state, list(character_updates or []), response_fields
    by_actor = {update.character_id: update for update in updates}
    landing_settled = next(
        (
            item for item in reversed(resumed.get("log", []))
            if item.get("type") == "jump_landing_settled"
        ),
        None,
    )
    if landing_settled is not None:
        actor_id = str(landing_settled.get("actor_id") or "")
        current = runtime.characters.get(actor_id)
        existing = by_actor.get(actor_id)
        sheet = support.deepcopy(existing.sheet if existing is not None else current.sheet)
        support.apply_condition_change(sheet, condition_id="prone", add=True)
        sheet = support.validate_character_sheet(sheet)
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
        by_actor[actor_id] = replacement
    landing_check_due = resumed.pop("jump_landing_check_due", None)
    landing_check_result = None
    if landing_check_due is not None:
        actor_id = str(landing_check_due.get("actor_id") or "")
        if (
            not actor_id
            or landing_check_due.get("kind") != "check"
            or landing_check_due.get("ability") != "acrobatics"
            or landing_check_due.get("dc") != 10
            or landing_check_due.get("ruleset") != "2014"
        ):
            raise support.CombatEngineError("deferred jump landing check is malformed")
        stream = support.active_random_stream()
        if stream is None:
            raise support.CombatEngineError(
                "deferred jump landing requires the original campaign random stream"
            )
        actor = runtime.combat_actor_snapshot(actor_id)
        landing_check_result = support.resolve_actor_check(
            actor,
            kind="check",
            ability="acrobatics",
            dc=10,
            encounter=resumed,
            rules=runtime.effective_rule_context(campaign.id),
            rng=stream,
            ruleset="2014",
        )
        failed = not bool(landing_check_result.get("success"))
        for item in reversed(resumed.get("log") or []):
            if (
                item.get("type") == "jump_resolved"
                and str(item.get("actor_id") or "") == actor_id
            ):
                resolution = dict(item.get("resolution") or {})
                resolution.update(
                    landing_check=support.deepcopy(landing_check_result),
                    landing_check_pending=None,
                    prone=failed,
                    outcome="landed_prone" if failed else "landed",
                )
                item["resolution"] = resolution
                break
        resumed["log"] = [
            *list(resumed.get("log") or []),
            {
                "type": "jump_landing_settled",
                "actor_id": actor_id,
                "check": support.deepcopy(landing_check_result),
                "condition": "prone" if failed else None,
                "after_reactions": True,
            },
        ][-100:]
        resumed["jump_landing_check_result"] = support.deepcopy(landing_check_result)
        if failed:
            current = runtime.characters.get(actor_id)
            existing = by_actor.get(actor_id)
            sheet = support.deepcopy(existing.sheet if existing is not None else current.sheet)
            support.apply_condition_change(sheet, condition_id="prone", add=True)
            sheet = support.validate_character_sheet(sheet)
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
            by_actor[actor_id] = replacement
        if isinstance(response_fields.get("jump_resolution"), dict):
            response_fields = {
                **response_fields,
                "jump_resolution": {
                    **support.deepcopy(response_fields["jump_resolution"]),
                    "landing_check": support.deepcopy(landing_check_result),
                    "landing_check_pending": None,
                    "prone": failed,
                    "outcome": "landed_prone" if failed else "landed",
                },
            }
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
                *(["dnd5e.core.movement.jump_2014"]
                  if landing_settled or landing_check_result is not None else []),
                *(["dnd5e.core.movement.creature_spaces"]
                  if resumed.get("ruleset") == "2014" else []),
                *([support.CORE_WITCH_BOLT_MECHANIC_ID] if ended else []),
            ], "movement.resume",
        )
    return {**campaign_state, "combat": resumed}, updates, response
