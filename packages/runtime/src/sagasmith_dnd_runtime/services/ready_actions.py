"""Validate fixed Ready responses and settle their non-attack state updates."""

from sagasmith_dnd.ready_actions import execute_non_attack, validate_response

from .. import application_support as support


def prepare_response(runtime, campaign_id, encounter, actor_id, payload, principal_id):
    response = validate_response(payload)
    target_id = response.get("target_id")
    if target_id is not None:
        runtime.require_encounter_combatant(encounter, target_id, role="Ready target")
    if response["action"] == "attack":
        if target_id == actor_id:
            raise support.CombatEngineError("an actor cannot ready an attack against itself")
        response["attack"] = runtime.sanitize_attack_action(
            campaign_id, principal_id, response["attack"]
        )
        response["attack"].pop("rulings", None)
        actor = runtime.combat_actor_snapshot(actor_id)
        weapon_ids = {item["item_id"] for item in actor["derived"]["inventory"]["weapon_attacks"]}
        if response["attack"]["weapon_id"] not in weapon_ids | {"unarmed-strike"}:
            raise support.CombatEngineError("readied Attack requires a current weapon option")
    if response["action"] == "move":
        runtime.validate_agent_movement_facts(encounter, response.get("spatial_facts"))
    return response


def settle_non_attack(runtime, campaign_id, encounter, actor_id, choice_id):
    value, readied = execute_non_attack(encounter, actor_id, choice_id)
    ended = support.newly_ended_witch_bolt_tethers(encounter, value)
    updates = []
    for caster_id in sorted({str(item["source_actor_id"]) for item in ended}):
        record = runtime.characters.get(caster_id)
        result = support.end_tether_concentrations(
            record.sheet, [item for item in ended if item["source_actor_id"] == caster_id]
        )
        if result["sheet"] != record.sheet:
            runtime.sync_combatant_conditions(value, caster_id, result["sheet"])
            updates.append(
                support.CharacterStateUpdate(
                    character_id=caster_id,
                    sheet=support.validate_character_sheet(result["sheet"]),
                    notes=support.validate_character_notes(record.notes),
                    expected_revision=record.revision,
                )
            )
    receipts = support.core_receipts(
        runtime.effective_rule_context(campaign_id),
        ["dnd5e.core.ready.action", *([support.CORE_WITCH_BOLT_MECHANIC_ID] if ended else [])],
        "ready.release",
    )
    return value, readied, updates, receipts
