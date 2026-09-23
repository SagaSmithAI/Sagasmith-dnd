"""Authoritative mounted-condition reconciliation for 2014 encounters."""

from __future__ import annotations

import uuid

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.mounted_combat import (
    active_mount_relations,
    forced_dismount_2014,
)

from .. import application_support as support


def needs_mounted_rider_save(runtime, campaign_state, character_updates) -> bool:
    encounter = (campaign_state or {}).get("combat")
    if not isinstance(encounter, dict) or encounter.get("ruleset") != "2014":
        return False
    updates = {
        str(update.character_id): update
        for update in character_updates or []
    }
    for relation in active_mount_relations(encounter):
        rider_id = str(relation.get("rider_actor_id") or "")
        update = updates.get(rider_id)
        if update is None:
            continue
        before = runtime.characters.get(rider_id).sheet
        if (
            "prone" not in support.condition_ids(before.get("conditions"))
            and "prone" in support.condition_ids(update.sheet.get("conditions"))
        ):
            return True
    return False


def reconcile_mounted_conditions(
    runtime, campaign, campaign_state, character_updates, response_fields
):
    encounter = (campaign_state or {}).get("combat")
    if not isinstance(encounter, dict) or encounter.get("ruleset") != "2014":
        return campaign_state, character_updates, response_fields
    relations = active_mount_relations(encounter)
    if not relations:
        return campaign_state, character_updates, response_fields

    updates = list(character_updates or [])
    update_by_actor = {str(item.character_id): item for item in updates}
    next_encounter = support.deepcopy(encounter)
    participant_ids = {
        str(item.get("actor_id") or "") for item in next_encounter.get("combatants", [])
    }
    for update in updates:
        actor_id = str(update.character_id)
        if actor_id in participant_ids:
            runtime.sync_combatant_conditions(next_encounter, actor_id, update.sheet)
    events = []
    for original_relation in relations:
        relation = next(
            item for item in active_mount_relations(next_encounter)
            if item.get("id") == original_relation.get("id")
        )
        rider_id = str(relation.get("rider_actor_id") or "")
        mount_id = str(relation.get("mount_actor_id") or "")
        rider_record = runtime.characters.get(rider_id)
        mount_record = runtime.characters.get(mount_id)
        rider_update = update_by_actor.get(rider_id)
        mount_update = update_by_actor.get(mount_id)
        rider_sheet = rider_update.sheet if rider_update is not None else rider_record.sheet
        mount_sheet = mount_update.sheet if mount_update is not None else mount_record.sheet
        rider_before = support.condition_ids(rider_record.sheet.get("conditions"))
        mount_before = support.condition_ids(mount_record.sheet.get("conditions"))
        rider_after = support.condition_ids(rider_sheet.get("conditions"))
        mount_after = support.condition_ids(mount_sheet.get("conditions"))
        rider_became_prone = "prone" not in rider_before and "prone" in rider_after
        mount_became_prone = "prone" not in mount_before and "prone" in mount_after
        rider_unavailable = bool(
            {"dead", "unconscious", "incapacitated"} & rider_after
        )
        mount_unavailable = bool(
            {"dead", "unconscious", "incapacitated"} & mount_after
        )
        if rider_became_prone and not rider_unavailable:
            stream = support.active_random_stream()
            if stream is None:
                raise support.CombatEngineError(
                    "a mounted rider's Dexterity save requires the campaign random stream"
                )
            actor = runtime.combat_actor_snapshot(rider_id)
            actor["sheet"] = support.deepcopy(rider_sheet)
            actor["derived"] = derive_character_sheet(rider_sheet)
            save = support.resolve_actor_check(
                actor,
                kind="save",
                ability="dexterity",
                dc=10,
                encounter=next_encounter,
                rules=runtime.effective_rule_context(campaign.id),
                rng=stream,
                ruleset="2014",
            )
            events.append({
                "type": "mounted_rider_prone_save",
                "rider_actor_id": rider_id,
                "mount_actor_id": mount_id,
                "save": support.deepcopy(save),
                "fell": not bool(save.get("success")),
            })
            if not save.get("success"):
                next_encounter = forced_dismount_2014(
                    next_encounter,
                    rider_actor_id=rider_id,
                    reason="rider_knocked_prone",
                    prone=True,
                )
                events.extend(next_encounter.get("log", [])[-1:])
        elif mount_became_prone or rider_unavailable or mount_unavailable:
            rider = next(
                item for item in next_encounter.get("combatants", [])
                if str(item.get("actor_id") or "") == rider_id
            )
            reaction = int(dict(rider.get("turn_budget") or {}).get("reaction", 0) or 0)
            if mount_became_prone and not rider_unavailable and reaction > 0:
                choice_id = f"mount-fall-{uuid.uuid4().hex}"
                window = {
                    "id": choice_id,
                    "kind": "reaction",
                    "actor_id": rider_id,
                    "event": "mount_knocked_prone",
                    "trigger": "mounted_mount_prone",
                    "relation_id": relation["id"],
                    "mount_actor_id": mount_id,
                    "candidates": [
                        {"id": "dismount_steady"},
                        {"id": "fall_prone"},
                    ],
                    "deadline": "before_commit",
                    "status": "pending",
                }
                next_encounter.setdefault("pending", []).append(window)
                events.append({"type": "mount_prone_reaction_pending", "choice_id": choice_id})
                continue
            next_encounter = forced_dismount_2014(
                next_encounter,
                rider_actor_id=rider_id,
                reason=(
                    "rider_unavailable" if rider_unavailable else
                    "mount_knocked_prone" if mount_became_prone else "mount_unavailable"
                ),
                prone=rider_became_prone or mount_became_prone or mount_unavailable,
            )
            events.extend(next_encounter.get("log", [])[-1:])

        ended = next(
            (item for item in next_encounter.get("mount_relations", [])
             if item.get("id") == relation.get("id")),
            relation,
        )
        if ended.get("active", True):
            continue
        rider = next(
            item for item in next_encounter.get("combatants", [])
            if str(item.get("actor_id") or "") == rider_id
        )
        next_sheet = support.deepcopy(rider_sheet)
        if "prone" in support.condition_ids(rider.get("conditions")):
            support.apply_condition_change(next_sheet, condition_id="prone", add=True)
        else:
            support.apply_condition_change(next_sheet, condition_id="prone", add=False)
        replacement = (
            support.replace(rider_update, sheet=support.validate_character_sheet(next_sheet))
            if rider_update is not None
            else support.CharacterStateUpdate(
                character_id=rider_id,
                sheet=support.validate_character_sheet(next_sheet),
                notes=support.validate_character_notes(rider_record.notes),
                expected_revision=rider_record.revision,
            )
        )
        if rider_update is not None:
            updates[updates.index(rider_update)] = replacement
        else:
            updates.append(replacement)
        update_by_actor[rider_id] = replacement
        runtime.sync_combatant_conditions(next_encounter, rider_id, replacement.sheet)

    if next_encounter == encounter:
        return campaign_state, updates, response_fields
    next_encounter["log"] = [*list(next_encounter.get("log") or []), *events][-100:]
    response = dict(response_fields)
    if "combat" in response:
        response["combat"] = next_encounter
    pending_fall = next(
        (item for item in reversed(next_encounter.get("pending", []))
         if item.get("trigger") == "mounted_mount_prone"),
        None,
    )
    if pending_fall is not None:
        response.update(status="pending_reaction", choice=support.deepcopy(pending_fall))
    if events:
        response["mounted_fall_events"] = support.deepcopy(events)
    response["mounted_rule_receipts"] = support.core_receipts(
        runtime.effective_rule_context(campaign.id),
        ["dnd5e.core.combat.mounted_2014"],
        "combat.mounted_fall",
    )
    return {**campaign_state, "combat": next_encounter}, updates, response
