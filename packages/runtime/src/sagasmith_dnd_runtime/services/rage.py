"""Authoritative 2014 Rage activity activation and source checks."""

from copy import deepcopy

from sagasmith_dnd import rage

from .. import application_support as s


def verify_activations(campaign, records):
    ledger = (campaign.state or {}).get("_rage_activations", {})
    tick = (campaign.state or {}).get("game_time", {}).get("elapsed_ticks", 0)
    for actor in records:
        effects = [
            e
            for e in actor.sheet.get("effects", [])
            if e.get("active") and e.get("kind") == rage.KIND
        ]
        if not effects:
            continue
        effect = rage.active(actor.sheet)
        grant = ledger.get(actor.id, {})
        if (
            effect is None
            or grant.get("effect_id") != effect["id"]
            or (grant.get("metadata") != effect["metadata"])
        ):
            raise ValueError("Rage requires its authoritative activation")
        elapsed = tick - grant["started_at_tick"]
        duration = effect.get("duration", {})
        if (
            not 0 <= elapsed < 10
            or duration.get("period") != "minute"
            or duration.get("remaining") != 1
            or duration.get("elapsed_ticks_remainder", 0) != elapsed
        ):
            raise ValueError("Rage duration no longer matches its activation")


def use(service, cid, actor_id, declaration, principal, revision, branch_id, key):
    actor = service.require_campaign_actor(cid, actor_id)
    service.require_character_control(actor, principal)
    service.require_write_contract(revision, key)
    branch = service.require_current_branch(cid, branch_id)
    request = {"actor_id": actor_id, "declaration": declaration}
    scope = f"rage:{cid}:{branch}:{principal}"
    replay = service.replay_idempotent(scope, key, request)
    if replay is not None:
        return replay
    campaign, encounter = service.active_encounter(cid)
    if campaign.revision != revision:
        raise ValueError("Rage campaign revision conflict")
    if service.campaign_rules_edition(cid) != "2014":
        raise ValueError("Rage requires the 2014 source")
    data = {} if declaration is None else declaration
    if (
        not isinstance(data, dict)
        or set(data) - {"end"}
        or ("end" in data and type(data["end"]) is not bool)
    ):
        raise ValueError("Rage declaration allows only end:bool")
    service.require_no_blocking_pending(encounter)
    paid_encounter = s.pay_activity_activation(
        encounter, actor_id_value=actor_id, activation_type="bonus_action"
    )
    rules = service.effective_rule_context(cid, branch_id=branch)
    state = deepcopy(campaign.state)
    state["combat"] = paid_encounter
    fields = {"status": "committed", "actor_id": actor_id, "activity_id": rage.FEATURE}
    receipts = s.core_receipts(rules, [rage.MECHANIC], "class.rage")
    if data.get("end"):
        if not rage.active(actor.sheet):
            raise ValueError("Rage is not active")
        sheet = deepcopy(actor.sheet)
        fields["ended_effect_ids"] = rage.end(sheet, "voluntary")
    else:
        feature = rage.feature(actor.sheet)
        sources = [
            (pid, version, a)
            for pid, version, a in service.available_content_artifacts(cid, branch_id=branch)
            if a.get("id") == rage.FEATURE
        ]
        if feature is None or len(sources) != 1:
            raise ValueError("Rage requires its uniquely available reviewed source feature")
        pid, version, artifact = sources[0]
        card = artifact["card"]
        expected = {
            "pack_id": pid,
            "pack_version": version,
            "description": card["description"],
            "rule_refs": artifact["rule_refs"],
            "mechanic_refs": artifact["mechanic_refs"],
            "resource_key": "",
            "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
            "resource_scaling": {
                "maximum_formula": {},
                "recovery_by_level": {},
                "recovery_amounts": {},
                "unlimited_at_level": 0,
                **card["resource_scaling"],
            },
        }
        mismatched = [k for k, v in expected.items() if feature.get(k) != v]
        if mismatched:
            raise ValueError(
                "Rage feature does not match its locked source: " + ", ".join(mismatched)
            )
        level = rage.level(actor.sheet)
        maximum = (
            0
            if level == 20
            else 6
            if level >= 17
            else 5
            if level >= 12
            else 4
            if level >= 6
            else 3
            if level >= 3
            else 2
        )
        uses = feature.get("uses", {})
        if (
            uses.get("max") != maximum
            or uses.get("recovers_on") != "long_rest"
            or (bool(uses.get("unlimited")) != (level == 20))
        ):
            raise ValueError("Rage uses do not match current Barbarian level")
        # Validate activation before any use is paid. Pure Domain transitions
        # remain speculative until the guarded campaign/actor commit below.
        result = rage.enter(actor.sheet, s.uuid4().hex)
        paid = s.consume_activity(result["sheet"], activity_id=rage.FEATURE, rules=rules)
        if paid["status"] != "committed":
            raise ValueError("Rage requires a settled source use")
        sheet = paid["sheet"]
        receipts.extend(paid["rule_receipts"])
        fields.update(
            effect=rage.active(sheet),
            ended_concentration_effect_ids=result["ended_concentration_effect_ids"],
            payment=paid.get("payment"),
        )
        state.setdefault("_rage_activations", {})[actor_id] = {
            "effect_id": result["effect"]["id"],
            "metadata": deepcopy(result["effect"]["metadata"]),
            "started_at_tick": state.get("game_time", {}).get("elapsed_ticks", 0),
        }
    fields["dissipated_readied_ids"] = s.reconcile_readied_spells(state["combat"], actor_id, sheet)
    state["combat"] = s.reconcile_witch_bolt_concentration(
        state["combat"],
        actor_id_value=actor_id,
        active_concentration_effect_ids={
            e["id"] for e in sheet.get("effects", []) if e.get("active") and e.get("concentration")
        },
    )["encounter"]
    response = service.commit_campaign_state(
        campaign,
        state,
        operation="class.rage.end" if data.get("end") else "class.rage.enter",
        principal_id=principal,
        branch_id=branch,
        idempotency_key=key,
        scope=scope,
        payload=request,
        character_updates=[
            s.CharacterStateUpdate(
                character_id=actor_id,
                sheet=s.validate_character_sheet(sheet),
                notes=actor.notes,
                expected_revision=actor.revision,
            )
        ],
        rule_receipts=receipts,
        response_fields=fields,
    )
    return service.combat_response(cid, principal, response)
