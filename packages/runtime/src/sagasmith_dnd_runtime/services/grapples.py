"""Reconcile source-bound 2014 grapple effects with encounter state."""

from __future__ import annotations

from .. import application_support as support


def reconcile_grapples(runtime, campaign_state, character_updates, response_fields):
    if not isinstance(campaign_state, dict):
        return campaign_state, character_updates, response_fields
    original = campaign_state.get("combat")
    if not isinstance(original, dict) or original.get("ruleset") != "2014":
        return campaign_state, character_updates, response_fields
    sources = original.get("grapple_sources")
    if not isinstance(sources, list) or not sources:
        return campaign_state, character_updates, response_fields

    encounter = support.deepcopy(original)
    updates = list(character_updates or [])
    by_actor = {update.character_id: update for update in updates}
    combatants = {
        str(item.get("actor_id") or ""): item
        for item in [*encounter.get("combatants", []), *encounter.get("reinforcements", [])]
    }
    ended_ids: list[str] = []
    for source in encounter.get("grapple_sources", []):
        if not isinstance(source, dict) or not source.get("active", True):
            continue
        source_id = str(source.get("id") or "")
        attacker_id = str(source.get("source_actor_id") or "")
        target_id = str(source.get("target_actor_id") or "")
        attacker = combatants.get(attacker_id)
        target = combatants.get(target_id)
        reason = None
        if attacker is None or target is None:
            reason = "combatant_unavailable"
        elif int(attacker.get("hit_points", 1) or 0) <= 0 or (
            {str(item).casefold() for item in attacker.get("conditions", [])}
            & support.INCAPACITATING_STATE_IDS
        ):
            reason = "source_incapacitated"
        else:
            target_update = by_actor.get(target_id)
            target_record = runtime.characters.get(target_id)
            target_sheet = (
                target_update.sheet if target_update is not None else target_record.sheet
            )
            effect_id = str(source.get("effect_id") or source_id)
            effect = next(
                (item for item in target_sheet.get("effects", []) if item.get("id") == effect_id),
                None,
            )
            if effect is None or not effect.get("active", True):
                reason = "source_effect_missing"
            elif "grappled" not in support.condition_ids(target_sheet.get("conditions")):
                reason = "condition_removed"
            elif encounter.get("positioning_mode") == "grid":
                from sagasmith_dnd.character_schema import effective_size
                from sagasmith_dnd.spaces import SPACE_FT, distance_between

                attacker_record = runtime.characters.get(attacker_id)
                attacker_sheet = (
                    by_actor[attacker_id].sheet if attacker_id in by_actor
                    else attacker_record.sheet
                )
                attacker_position = attacker.get("position") or {}
                target_position = target.get("position") or {}
                if not all(key in attacker_position for key in ("x", "y")) or not all(
                    key in target_position for key in ("x", "y")
                ):
                    reason = "position_unavailable"
                else:
                    distance = distance_between(
                        (attacker_position["x"], attacker_position["y"]),
                        SPACE_FT[effective_size(attacker_sheet)],
                        (target_position["x"], target_position["y"]),
                        SPACE_FT[effective_size(target_sheet)],
                    )
                    if distance > int(source.get("reach_ft", 5) or 5):
                        reason = "separated"
        if reason is None:
            continue
        source["active"] = False
        source["ended_reason"] = reason
        source["ended_round"] = int(encounter.get("round", 1) or 1)
        ended_ids.append(source_id)
        if target is None:
            continue
        target_update = by_actor.get(target_id)
        target_record = runtime.characters.get(target_id)
        sheet = target_update.sheet if target_update is not None else target_record.sheet
        effect_id = str(source.get("effect_id") or source_id)
        if not any(item.get("id") == effect_id for item in sheet.get("effects", [])):
            continue
        updated_sheet = support.remove_effect(sheet, effect_id)
        runtime.sync_combatant_conditions(encounter, target_id, updated_sheet)
        replacement = (
            support.replace(target_update, sheet=support.validate_character_sheet(updated_sheet))
            if target_update is not None
            else support.CharacterStateUpdate(
                character_id=target_id,
                sheet=support.validate_character_sheet(updated_sheet),
                notes=support.validate_character_notes(target_record.notes),
                expected_revision=target_record.revision,
            )
        )
        if target_update is None:
            updates.append(replacement)
            by_actor[target_id] = replacement
        else:
            updates[updates.index(target_update)] = replacement
            by_actor[target_id] = replacement

    if not ended_ids:
        return campaign_state, list(character_updates or []), response_fields
    response = {
        **response_fields,
        "ended_grapple_ids": ended_ids,
        **({"combat": encounter} if "combat" in response_fields else {}),
    }
    return {**campaign_state, "combat": encounter}, updates, response
