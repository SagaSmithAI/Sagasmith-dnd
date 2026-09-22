"""Atomic source-bound grants; recipient dice are backed by campaign receipts."""

from copy import deepcopy

from sagasmith_dnd import bardic_inspiration as bardic
from sagasmith_dnd.character_schema import effective_ability_modifier
from sagasmith_dnd.conditions import condition_ids
from sagasmith_dnd.spaces import distance_between, grid_space

from .. import application_support as s


def verify_grants(campaign, records):
    grants = (campaign.state or {}).get("bardic_inspiration_grants", {})
    tick = (campaign.state or {}).get("game_time", {}).get("elapsed_ticks", 0)
    for record in records:
        effect = bardic.held(record.sheet)
        if effect is None:
            continue
        grant = grants.get(record.id, {})
        if (
            grant.get("effect_id") != effect["id"]
            or grant.get("metadata") != effect["metadata"]
            or grant.get("spent") is not False
        ):
            raise ValueError("Bardic Inspiration requires its authoritative recipient grant")
        elapsed = tick - grant["granted_at_tick"]
        duration = effect.get("duration", {})
        if (
            not 0 <= elapsed < 100
            or duration.get("period") != "minute"
            or duration.get("remaining") != 10 - elapsed // 10
            or duration.get("elapsed_ticks_remainder", 0) != elapsed % 10
        ):
            raise ValueError("Bardic Inspiration duration no longer matches its grant")


def grant(
    service,
    actor,
    declaration,
    principal,
    expected_revision,
    key,
    *,
    combat=False,
    branch_id=None,
    expected_campaign_revision=None,
):
    cid = actor.campaign_id
    service.require_character_control(actor, principal)
    service.require_write_contract(
        expected_revision if not combat else expected_campaign_revision, key
    )
    if not cid or not service.is_dm(cid, principal):
        raise ValueError("Bardic Inspiration scene facts require the current campaign DM")
    branch = service.require_current_branch(cid, branch_id)
    scope = f"bardic-inspiration-grant:{cid}:{branch}:{principal}"
    request = {"actor_id": actor.id, "declaration": declaration, "combat": combat}
    cached = service.replay_idempotent(scope, key, request)
    if cached is not None:
        return cached
    campaign = service.campaigns.get(cid)
    if (not combat and actor.revision != expected_revision) or (
        combat and campaign.revision != expected_campaign_revision
    ):
        raise ValueError("Bardic Inspiration revision conflict")
    if service.campaign_rules_edition(cid) != "2014":
        raise ValueError("Bardic Inspiration requires the 2014 source")
    if not isinstance(declaration, dict) or set(declaration) != {"target_id", "scene_facts"}:
        raise ValueError("Bardic Inspiration requires target_id and scene_facts")
    target = service.require_campaign_actor(cid, declaration["target_id"])
    if target.id == actor.id:
        raise ValueError("Bardic Inspiration must target another creature")
    facts = declaration["scene_facts"]
    state = deepcopy(campaign.state)
    encounter = None
    if combat:
        _, encounter = service.active_encounter(cid)
        if encounter.get("pending"):
            raise ValueError("resolve pending combat choices before granting inspiration")
    else:
        service.require_outside_active_combat(actor, "Bardic Inspiration grant")
    grid = encounter is not None and encounter.get("positioning_mode") == "grid"
    fields = {"decision_id", "reason", "target_can_hear"} | (set() if grid else {"within_60_ft"})
    if (
        not isinstance(facts, dict)
        or set(facts) != fields
        or any(
            not isinstance(facts[k], str) or not facts[k].strip() for k in ("decision_id", "reason")
        )
        or type(facts["target_can_hear"]) is not bool
        or (not grid and type(facts["within_60_ft"]) is not bool)
    ):
        raise ValueError(
            "Bardic Inspiration requires exact reviewed scene facts with boolean values"
        )
    if not facts["target_can_hear"] or "deafened" in condition_ids(target.sheet.get("conditions")):
        raise ValueError("target cannot hear the Bard")
    if encounter is not None:
        participants = {p["actor_id"]: p for p in encounter["combatants"] if not p.get("departed")}
        if actor.id not in participants or target.id not in participants:
            raise ValueError("Bard and target must be current combatants")
        if grid:
            source, recipient = participants[actor.id], participants[target.id]
            p, q = source["position"], recipient["position"]
            pxy, qxy = (p["x"], p["y"]), (q["x"], q["y"])
            battle_map = encounter.get("battle_map") or {}
            within = (
                distance_between(
                    pxy,
                    grid_space(source, pxy, battle_map)["space_ft"],
                    qxy,
                    grid_space(recipient, qxy, battle_map)["space_ft"],
                )
                <= 60
            )
        else:
            within = facts["within_60_ft"]
    else:
        within = facts["within_60_ft"]
    if not within:
        raise ValueError("Bardic Inspiration target is outside 60 feet")
    feature = bardic.feature(actor.sheet)
    if not feature:
        raise ValueError("Bardic Inspiration requires its reviewed source feature")
    sources = [
        (pid, version, artifact)
        for pid, version, artifact in service.available_content_artifacts(cid, branch_id=branch)
        if artifact.get("id") == bardic.FEATURE
    ]
    if len(sources) != 1:
        raise ValueError("Bardic Inspiration source is not uniquely available")
    pid, version, artifact = sources[0]
    source = artifact["card"]
    expected = {
        "pack_id": pid,
        "pack_version": version,
        "description": source.get("description"),
        "rule_refs": artifact.get("rule_refs"),
        "mechanic_refs": artifact.get("mechanic_refs"),
        "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
        "resource_key": "",
        "resource_scaling": {
            "recovery_amounts": {},
            "unlimited_at_level": 0,
            **source["resource_scaling"],
        },
    }
    mismatched = {key for key, value in expected.items() if feature.get(key) != value}
    if mismatched:
        raise ValueError(
            "Bardic Inspiration feature does not match its locked source: "
            + ", ".join(sorted(mismatched))
        )
    level = sum(
        c["level"] for c in actor.sheet["progression"]["classes"] if c["name"].casefold() == "bard"
    )
    uses = feature.get("uses") or {}
    if (
        uses.get("unlimited")
        or uses.get("max") != max(1, effective_ability_modifier(actor.sheet, "charisma"))
        or uses.get("recovers_on") != ("short_rest" if level >= 5 else "long_rest")
    ):
        raise ValueError("Bardic Inspiration uses must match current class and ability scaling")
    effect = bardic.grant_effect(
        actor.sheet, target.sheet, source_actor_id=actor.id, effect_id=s.uuid4().hex
    )
    rules = service.effective_rule_context(cid, branch_id=branch)
    applied = s.consume_activity(actor.sheet, activity_id=bardic.FEATURE, rules=rules)
    if applied["status"] != "committed" or applied.get("payment", {}).get("amount") != 1:
        raise ValueError("Bardic Inspiration requires one settled source use")
    target_sheet, _ = s.add_effect(target.sheet, effect)
    # Store the normalized effect metadata and unique recipient in campaign
    # authority; an arbitrary effect_add or copied card cannot mint a die.
    effect = bardic.held(target_sheet)
    state.setdefault("bardic_inspiration_grants", {})[target.id] = {
        "effect_id": effect["id"],
        "metadata": deepcopy(effect["metadata"]),
        "spent": False,
        "scene_facts": deepcopy(facts),
        "principal_id": principal,
        "granted_at_tick": state.get("game_time", {}).get("elapsed_ticks", 0),
    }
    if encounter is not None:
        state["combat"] = s.pay_activity_activation(
            encounter,
            actor_id_value=actor.id,
            activation_type="bonus_action",
        )
    return service.commit_campaign_state(
        campaign,
        state,
        operation="class.bardic_inspiration.grant",
        principal_id=principal,
        branch_id=branch,
        idempotency_key=key,
        scope=scope,
        payload=request,
        character_updates=[
            s.CharacterStateUpdate(
                character_id=actor.id,
                sheet=applied["sheet"],
                notes=actor.notes,
                expected_revision=actor.revision,
            ),
            s.CharacterStateUpdate(
                character_id=target.id,
                sheet=target_sheet,
                notes=target.notes,
                expected_revision=target.revision,
            ),
        ],
        rule_receipts=[
            *applied["rule_receipts"],
            *s.core_receipts(rules, [bardic.MECHANIC], "class.bardic_inspiration.grant"),
        ],
        response_fields={
            "status": "committed",
            "target_id": target.id,
            "effect": effect,
            "source_actor_id": actor.id,
        },
    )
