"""Owned, durable Protection choices before any attack roll or action payment."""

from sagasmith_dnd.fighting_styles import (
    STYLE_RULE,
    apply_protection,
    protection_candidates,
)

from .. import application_support as s


def resume(encounter, binding):
    intent = encounter.get("protection_intent")
    if not intent:
        return encounter, None
    if intent["binding"] != binding:
        raise s.CombatEngineError("resume the original Protection attack declaration first")
    if any(w["id"] in intent["window_ids"] for w in encounter.get("pending", [])):
        raise s.CombatEngineError("resolve every owned Protection choice before the attack")
    value = s.deepcopy(encounter)
    value.pop("protection_intent")
    value["pending"] = [w for w in value.get("pending", [])
                        if w.get("id") != intent.get("resume_id")]
    return value, list(intent["accepted"])


def create_windows(encounter, binding, candidates, *, facts=None, semantic=False):
    value = s.deepcopy(encounter)
    windows = []
    for identifier in candidates:
        value = s.add_choice_window(
            value, kind="reaction", actor_id_value=identifier, event="attack.before_roll",
            candidates=[{"id": "protection", "name": "Protection"},
                        {"id": "decline", "name": "Decline"}],
        )
        window = value["pending"][-1]
        window.update(trigger="protection", attacker_id=binding["actor_id"],
                      target_id=binding["target_id"])
        windows.append(window["id"])
    intent = {
        "binding": s.deepcopy(binding), "window_ids": windows, "accepted": [],
        "facts": s.deepcopy(facts), "semantic": semantic,
    }
    if not semantic:
        value = s.add_choice_window(
            value, kind="attack_continuation", actor_id_value=binding["actor_id"],
            event="attack.before_roll.resume",
            candidates=[{"id": "cancel", "name": "Cancel the declared attack"}],
        )
        barrier = value["pending"][-1]
        barrier.update(trigger="protection_resume", attacker_id=binding["actor_id"],
                       target_id=binding["target_id"],
                       resume="Repeat the original attack with a new operation ID after choices.")
        intent["resume_id"] = barrier["id"]
    value["protection_intent"] = intent
    return value


def receipts(service, campaign_id, branch_id):
    return s.core_receipts(
        service.effective_rule_context(campaign_id, branch_id=branch_id),
        [STYLE_RULE], "attack.before_roll.protection",
    )


def actor_guards(service, campaign_id, encounter, *, sheet_override=None):
    sheets, updates = {}, []
    for actor in encounter.get("combatants", []):
        record = service.require_campaign_actor(campaign_id, actor["actor_id"])
        sheet = s.deepcopy((sheet_override or {}).get(record.id, record.sheet))
        sheets[record.id] = sheet
        # As with movement, current Core represents read guards as unchanged writes.
        updates.append(s.CharacterStateUpdate(
            character_id=record.id, sheet=sheet, notes=record.notes,
            expected_revision=record.revision,
        ))
        actor["conditions"] = list(sheet.get("conditions") or [])
        service.sync_combatant_spaces(encounter, record.id, sheet)
    return sheets, updates


def trigger_geometry(encounter, binding, target_position=None):
    value = s.deepcopy(encounter)
    if target_position is not None:
        next(a for a in value["combatants"] if a["actor_id"] == binding["target_id"])[
            "position"
        ] = s.deepcopy(target_position)
    if binding["kind"] == "reaction_attack":
        # The attacker's one reaction is already committed to this declaration.
        # Do not offer the same budget to protect against its own reaction attack.
        next(a for a in value["combatants"] if a["actor_id"] == binding["actor_id"])[
            "turn_budget"
        ]["reaction"] = 0
    return value


def resume_request(campaign_id, binding):
    payload = s.deepcopy(binding["payload"])
    payload.pop("branch_id", None)
    if binding["kind"] == "combat_attack":
        action = payload.pop("action")
        for key in ("cantrip_spell_id", "spell_resolution_id", "deflect_attack"):
            value = payload.pop(key)
            if value:
                action[key] = value
        return {"tool": "combat_resolve_attack",
                "arguments": {"campaign_id": campaign_id, **payload, "action": action}}
    if "release" in payload:
        return {"tool": "combat_ready", "arguments": {
            "campaign_id": campaign_id, "action": "resolve_action", "payload": payload,
        }}
    return {"tool": "combat_reaction_attack",
            "arguments": {"campaign_id": campaign_id, **payload}}


def offer(service, campaign, encounter, binding, *, principal_id, branch_id,
          idempotency_key, scope, payload, facts=None, sheet_override=None,
          extra_receipts=(), response_fields=None, target_position=None):
    value = s.deepcopy(encounter)
    sheets, guards = actor_guards(service, campaign.id, value, sheet_override=sheet_override)
    geometry = trigger_geometry(value, binding, target_position)
    candidates = protection_candidates(
        geometry, sheets, binding["actor_id"], binding["target_id"], facts=facts,
    )
    if not candidates:
        return None
    if value.get("positioning_mode") == "agent":
        service.access.require_campaign(campaign.id, principal_id, roles=s.CAMPAIGN_DM_ROLES)
    value = create_windows(value, binding, candidates, facts=facts)
    value["protection_intent"]["target_position"] = s.deepcopy(target_position)
    rule_receipts = [*receipts(service, campaign.id, branch_id), *extra_receipts]
    result = service.commit_campaign_state(
        campaign, {**dict(campaign.state or {}), "combat": value},
        operation="combat.attack.protection.offer", principal_id=principal_id,
        branch_id=branch_id, idempotency_key=idempotency_key, scope=scope, payload=payload,
        character_updates=guards, rule_receipts=rule_receipts,
        response_fields={**(response_fields or {}), "status": "pending_reaction",
                         "combat": value, "rule_receipts": rule_receipts,
                         "result": {"pending_reaction": True, "attack_rolled": False},
                         "resume_attack": resume_request(campaign.id, binding)},
    )
    return service.combat_response(campaign.id, principal_id, result)


def apply(plan, accepted, service, campaign_id, branch_id):
    value = apply_protection(plan, accepted or [])
    if accepted:
        value["rule_receipts"] = [*value.get("rule_receipts", []),
                                  *receipts(service, campaign_id, branch_id)]
    return value


def resolve(service, campaign, encounter, window, selection, *, principal_id,
            branch_id, idempotency_key, scope, payload):
    intent = encounter.get("protection_intent")
    if not intent:
        raise s.CombatEngineError("Protection has no active attack declaration")
    actor_id = window["actor_id"]
    if actor_id != payload["actor_id"]:
        raise s.CombatEngineError("Protection choice belongs to another actor")
    selection_id = selection.get("id")
    value = s.resolve_choice_window(
        encounter, choice_id=window["id"], actor_id_value=actor_id, selection=selection,
    )
    guards = []
    if window.get("trigger") == "protection_resume":
        if selection_id != "cancel":
            raise s.CombatEngineError("resume by repeating the original attack declaration")
        if any(w["id"] in intent["window_ids"] for w in value.get("pending", [])):
            raise s.CombatEngineError("resolve Protection choices before cancelling the attack")
        value.pop("protection_intent")
    elif selection_id == "protection":
        sheets, guards = actor_guards(service, campaign.id, value)
        binding = intent["binding"]
        # Other protectors may already have spent their reaction. Validate this
        # choice against its original DM facts without requiring them again.
        facts = s.deepcopy(intent.get("facts"))
        if facts:
            from sagasmith_dnd.fighting_styles import protection_ready

            geometry = trigger_geometry(value, binding, intent.get("target_position"))
            ready = {a["actor_id"] for a in geometry["combatants"]
                     if a["actor_id"] != binding["target_id"]
                     and protection_ready(sheets[a["actor_id"]], a)}
            facts["actors"] = [f for f in facts["actors"] if f["actor_id"] in ready]
        geometry = trigger_geometry(value, binding, intent.get("target_position"))
        eligible = protection_candidates(
            geometry, sheets, binding["actor_id"], binding["target_id"], facts=facts,
        )
        if actor_id not in eligible:
            raise s.CombatEngineError("Protection prerequisites no longer hold; decline the choice")
        protector = next(a for a in value["combatants"] if a["actor_id"] == actor_id)
        protector["turn_budget"]["reaction"] -= 1
        value["protection_intent"]["accepted"].append(actor_id)
    rule_receipts = receipts(service, campaign.id, branch_id)
    response = service.commit_campaign_state(
        campaign, {**dict(campaign.state or {}), "combat": value},
        operation="combat.protection.resolve", principal_id=principal_id, branch_id=branch_id,
        idempotency_key=idempotency_key, scope=scope, payload=payload,
        character_updates=guards, rule_receipts=rule_receipts,
        response_fields={"status": "committed", "combat": value, "rule_receipts": rule_receipts},
    )
    return service.combat_response(campaign.id, principal_id, response)
