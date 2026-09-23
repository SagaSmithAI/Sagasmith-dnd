"""Durable failed-save decisions, with transactional command replay and recorded dice.

The suspended attempt rolls back all game effects. Only its random prefix and an
owned decision commit. Resumption feeds those exact dice to the original command;
it never rewinds the campaign stream or accepts replacement action arguments.
"""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace

from sagasmith_dnd import bardic_inspiration as bardic
from sagasmith_dnd import divine_smite as smite
from sagasmith_dnd.legendary_resistance import (
    MECHANIC,
    SaveDecisionRequiredError,
    feature,
    saving_throw_decisions,
    succeed,
)

from .. import application_support as s

STATE_KEY = "_saving_throw_continuation"
_ACTIVE = ContextVar("dnd_save_command", default=None)
SAVE_COMMANDS = frozenset(
    {
        "character_check",
        "character_state_change",
        "character_action",
        "combat_check",
        "combat_cast_spell",
        "combat_concentration_check",
        "combat_resolve_attack",
        "combat_reaction_attack",
        "combat_ready",
        "combat_choice",
        "combat_use_activity",
        "combat_hp_change",
        "combat_end_turn",
        "combat_use_official_item",
        "combat_start",
        "combat_join",
        "combat_common_action",
        "combat_resolve_hide",
        "combat_movement",
    }
)


def _campaign_id(service, arguments):
    payload = arguments.get("payload") or {}
    cid = arguments.get("campaign_id") or payload.get("campaign_id")
    actor_id = arguments.get("character_id") or arguments.get("actor_id")
    if cid:
        return cid
    if actor_id:
        return service.characters.get(actor_id).campaign_id
    stream = s.active_random_stream()
    return stream.campaign_id if stream else None


def _scope(campaign_id, principal, name):
    return f"saving-throw-command:{campaign_id}:{principal}:{name}"


def _request(service, campaign, principal, arguments):
    return {
        "arguments": deepcopy(arguments),
        "authorization_fingerprint": service.access.authorization_fingerprint(
            campaign.id,
            principal,
        ),
        "branch_id": service.current_branch_id(campaign.id),
        "timeline_epoch": campaign.timeline_epoch,
    }


def guard_request(service, campaign_id, name, arguments):
    if _ACTIVE.get() is not None:
        return
    operation = service.mcp.operations.get(name)
    if operation and operation.annotations.read_only_hint:
        return
    try:
        pending = (service.campaigns.get(campaign_id).state or {}).get(STATE_KEY)
    except LookupError:
        # The operation owns validation of unresolved campaign references.
        return
    if not pending:
        return
    if name in {"access_grant", "access_revoke"}:
        return
    if name == "character_state_change" and arguments.get("action") in {
        "legendary_resistance",
        "bardic_inspiration",
        "divine_smite",
    }:
        return
    if name == pending["name"] and arguments.get("idempotency_key") == pending["arguments"].get(
        "idempotency_key"
    ):
        return
    raise s.CombatEngineError(
        "resolve the owned Legendary Resistance/Bardic Inspiration decision first"
    )


def run(service, name, function, arguments, *, read_only=False):
    if _ACTIVE.get() is not None or read_only:
        return function(**arguments)
    try:
        cid = _campaign_id(service, arguments)
        campaign = service.campaigns.get(cid) if cid else None
    except LookupError:
        # Preserve the operation's parameter validation before missing-entity
        # errors. No pending decision can belong to an absent entity.
        return function(**arguments)
    if not cid:
        return function(**arguments)
    principal = arguments.get("principal_id") or arguments.get("by_principal_id")
    principal = principal or s.LOCAL_SYSTEM_PRINCIPAL_ID
    pending = (campaign.state or {}).get(STATE_KEY)
    choice = name == "character_state_change" and arguments.get("action") in {
        "legendary_resistance",
        "bardic_inspiration",
        "divine_smite",
    }
    if choice:
        if s.active_random_stream() is None:
            with service.campaign_random_context(cid, name, arguments):
                return function(**arguments)
        return function(**arguments)
    key = arguments.get("idempotency_key")
    scope = _scope(cid, principal, name)
    request = _request(service, campaign, principal, arguments) if name in SAVE_COMMANDS else None
    if key and request is not None:
        cached = service.idempotency.lookup(scope, key, request)
        if cached:
            service.access.require_campaign(cid, principal)
            return deepcopy(cached.response)
    if pending and name not in {"access_grant", "access_revoke"}:
        raise s.CombatEngineError(
            "resolve the owned Legendary Resistance/Bardic Inspiration decision first"
        )
    if name not in SAVE_COMMANDS:
        return function(**arguments)
    records = service.characters.list(campaign_id=cid)
    from .inspiration import verify_grants
    from .rage import verify_activations

    verify_grants(campaign, records)
    verify_activations(campaign, records)
    if not any(
        feature(record.sheet) or bardic.held(record.sheet) or smite.feature(record.sheet)
        for record in records
    ):
        return function(**arguments)
    if not key:
        raise ValueError("saving throws require an idempotency key")
    command = {
        "name": name,
        "arguments": deepcopy(arguments),
        "principal_id": principal,
        "branch_id": service.current_branch_id(cid),
        "decisions": [],
        "draws": [],
    }
    if s.active_random_stream() is None:
        # The embedded legacy MCP test/caller path has no transport request
        # context. It still uses the same authoritative campaign dice stream.
        with service.campaign_random_context(cid, name, arguments):
            return _attempt(
                service,
                campaign,
                records,
                command,
                function,
                arguments,
                scope=scope,
                key=key,
                public_arguments=request,
                caller=principal,
            )
    return _attempt(
        service,
        campaign,
        records,
        command,
        function,
        arguments,
        scope=scope,
        key=key,
        public_arguments=request,
        caller=principal,
    )


class _Command:
    def __init__(self, command):
        self.value = deepcopy(command)
        self.index = 0
        self.spent = Counter()
        self.applied = Counter()
        self.inspired = set()
        self.inspiration_applied = set()
        self.finalized = False

    def smite(self, actor_id, sheet, result, entry):
        identity = {
            "actor_id": actor_id,
            "feature_id": entry["id"],
            "result": result,
            "kind": "divine_smite",
        }
        decisions = self.value["decisions"]
        if self.index < len(decisions):
            recorded = decisions[self.index]
            if any(recorded.get(k) != v for k, v in identity.items()):
                raise ValueError("saved Divine Smite hit no longer matches authoritative inputs")
        else:
            recorded = {
                **deepcopy(identity),
                "id": s.uuid4().hex,
                "accept": None,
                "source_key": entry["id"],
                "rule_refs": deepcopy(entry["rule_refs"]),
            }
            decisions.append(recorded)
        self.index += 1
        if recorded["accept"] is None:
            raise SaveDecisionRequiredError(actor_id, sheet, result)
        return recorded["slot"] if recorded["accept"] else None

    def save(self, actor_id, sheet, result, entry):
        decisions = self.value["decisions"]
        identity = {"actor_id": actor_id, "feature_id": entry["id"], "result": result}
        if self.index < len(decisions):
            recorded = decisions[self.index]
            if any(recorded[k] != identity[k] for k in identity):
                raise ValueError("saved saving throw no longer matches its authoritative inputs")
        else:
            if int(entry["uses"]["value"]) <= self.spent[actor_id] - self.applied[actor_id]:
                return result
            recorded = {
                **deepcopy(identity),
                "id": s.uuid4().hex,
                "accept": None,
                "kind": "legendary_resistance",
                "source_key": entry["source_key"],
                "rule_refs": entry["rule_refs"],
            }
            decisions.append(recorded)
        self.index += 1
        if recorded["accept"] is None:
            raise SaveDecisionRequiredError(actor_id, sheet, result)
        if recorded["accept"]:
            self.spent[actor_id] += 1
            value = succeed(result, sheet)
            value["legendary_resistance"].update(
                choice_id=recorded["id"],
                source_key=entry["source_key"],
                rule_refs=deepcopy(entry["rule_refs"]),
            )
            return value
        return result

    def inspire(self, actor_id, sheet, result, effect, rng):
        if effect["id"] in self.inspired:
            return result
        identity = {
            "actor_id": actor_id,
            "feature_id": effect["id"],
            "result": result,
            "kind": "bardic_inspiration",
        }
        decisions = self.value["decisions"]
        if self.index < len(decisions):
            recorded = decisions[self.index]
            if any(recorded.get(k) != identity[k] for k in identity):
                raise ValueError(
                    "saved inspiration roll no longer matches its authoritative inputs"
                )
        else:
            recorded = {
                **deepcopy(identity),
                "id": s.uuid4().hex,
                "accept": None,
                "source_key": effect["source"],
                "rule_refs": effect["metadata"]["rule_refs"],
                "die_size": effect["metadata"]["die_size"],
            }
            decisions.append(recorded)
        self.index += 1
        if recorded["accept"] is None:
            raise bardic.InspirationDecisionRequiredError(actor_id, sheet, result)
        if recorded["accept"]:
            self.inspired.add(effect["id"])
            result = bardic.add_die(result, sheet, effect, rng=rng)
        recorded["settled_result"] = deepcopy(result)
        return result


@contextmanager
def _speculative_work(database):
    """Roll back an offered decision without poisoning an outer phase transition."""
    decision = None
    with database.unit_of_work(immediate=True) as work:
        try:
            with database.savepoint(work):
                yield
        except SaveDecisionRequiredError as error:
            decision = error
    if decision is not None:
        raise decision


def _attempt(
    service,
    campaign,
    records,
    command,
    function,
    arguments,
    *,
    scope,
    key,
    public_arguments,
    caller,
):
    state = _Command(command)
    stream = s.active_random_stream()
    stream.replay_prefix = deepcopy(command["draws"])
    stream.replay_index = 0
    stream.recorded_draws = []
    capture_before = stream.capture_draws
    stream.capture_draws = True
    persisted_position = stream.persisted_position
    token = _ACTIVE.set(state)
    try:
        try:
            with _speculative_work(service.storage.database):
                if service.campaigns.get(campaign.id).revision != campaign.revision:
                    raise ValueError("campaign revision conflict before saving throw settlement")
                if {r.id: r.revision for r in service.characters.list(campaign_id=campaign.id)} != {
                    r.id: r.revision for r in records
                }:
                    raise ValueError("actor revision conflict before saving throw settlement")
                with (
                    saving_throw_decisions(state.save),
                    bardic.inspiration_decisions(state.inspire),
                    smite.decisions(state.smite),
                ):
                    result = function(**arguments)
                if stream.replay_index != len(stream.replay_prefix):
                    raise ValueError("saved command did not consume its complete random prefix")
                if state.index != len(state.value["decisions"]):
                    raise ValueError("saved command did not settle every recorded save")
                if not state.finalized and state.value["decisions"]:
                    raise RuntimeError("saving throw command returned without an atomic settlement")
                if command["decisions"]:
                    resolved = state.value["decisions"][len(command["decisions"]) - 1]
                    result = {
                        "status": "committed",
                        "resolved_save": command["decisions"][-1]["id"],
                        **(
                            {
                                "resolved_roll": {
                                    k: deepcopy(v)
                                    for k, v in resolved["settled_result"].items()
                                    if k
                                    in {
                                        "kind",
                                        "natural",
                                        "rolls",
                                        "rerolls",
                                        "total",
                                        "roll_mode",
                                        "bardic_inspiration",
                                    }
                                }
                            }
                            if resolved.get("kind") == "bardic_inspiration"
                            else {}
                        ),
                        "campaign_revision": service.campaigns.get(campaign.id).revision,
                        **(
                            {"operation_result": result}
                            if (
                                caller == command["principal_id"]
                                or service.is_dm(campaign.id, caller)
                            )
                            else {}
                        ),
                    }
                    service.idempotency.remember(
                        scope, key, public_arguments, result, campaign_id=campaign.id
                    )
                return result
        except SaveDecisionRequiredError:
            # The entire attempted settlement, including nested Core writes and
            # receipts, has rolled back. Persist only the dice and owned choice.
            stream.persisted_position = persisted_position
            state.value["draws"] = deepcopy(stream.recorded_draws)
    finally:
        _ACTIVE.reset(token)
        stream.replay_prefix = []
        stream.replay_index = 0
        stream.capture_draws = capture_before
    state.value["campaign_revision"] = campaign.revision + 1
    state.value["actor_revisions"] = {r.id: r.revision + 1 for r in records}
    state.value["random_position"] = stream.position
    state.value["random_seed"] = stream.seed
    next_state = {**deepcopy(campaign.state), STATE_KEY: state.value}
    guards = [
        s.CharacterStateUpdate(
            character_id=r.id, sheet=r.sheet, notes=r.notes, expected_revision=r.revision
        )
        for r in records
    ]
    service.replay_idempotent(scope, key, public_arguments)
    pending = state.value["decisions"][-1]
    response = service.commit_campaign_state(
        campaign,
        next_state,
        operation="save.legendary_resistance.offer",
        principal_id=caller,
        branch_id=command["branch_id"],
        idempotency_key=key,
        scope=scope,
        payload=public_arguments,
        character_updates=guards,
        rule_receipts=_receipts(service, campaign.id, command["branch_id"], state.value),
        response_fields={
            "status": (
                "pending_roll"
                if pending.get("kind") == "bardic_inspiration"
                else "pending_hit"
                if pending.get("kind") == "divine_smite"
                else "pending_save"
            ),
            "choice": public_choice(service, campaign.id, caller, pending),
        },
    )
    return response


def public_choice(service, campaign_id, principal, pending):
    value = {
        "id": pending["id"],
        "actor_id": pending["actor_id"],
        "kind": pending.get("kind", "legendary_resistance"),
        "status": "pending",
    }
    try:
        service.access.require_actor(campaign_id, pending["actor_id"], principal, control=True)
    except s.AccessDeniedError:
        return value
    result = deepcopy(pending["result"])
    if value["kind"] == "bardic_inspiration":
        # Deliberately exclude DC/AC, success/hit, death counters, conditions,
        # targets and other actors' private facts until the player decides.
        result = {
            k: result[k]
            for k in ("kind", "natural", "rolls", "rerolls", "total", "roll_mode")
            if k in result
        }
        value["die_size"] = pending["die_size"]
    value.update(
        result=result,
        source_key=pending["source_key"],
        rule_refs=deepcopy(pending["rule_refs"]),
        resolve={
            "tool": "character_state_change",
            "character_id": pending["actor_id"],
            "action": value["kind"],
            "payload": {"choice_id": pending["id"], "accept": True},
            "alternatives": [{"accept": True}, {"accept": False}],
        },
    )
    if value["kind"] == "divine_smite":
        alternatives = [{"accept": True, "slot": v["slot"]} for v in result["slots"]]
        value["resolve"]["payload"] = {"choice_id": pending["id"], **alternatives[0]}
        value["resolve"]["alternatives"] = [*alternatives, {"accept": False}]
    return value


def resolve(
    service, actor, payload, principal, expected_revision, key, *, kind="legendary_resistance"
):
    service.require_character_control(actor, principal)
    service.require_write_contract(expected_revision, key)
    expected_fields = {"choice_id", "accept"}
    if kind == "divine_smite" and payload.get("accept") is True:
        expected_fields.add("slot")
    if set(payload) != expected_fields or type(payload.get("accept")) is not bool:
        raise ValueError("owned roll decision requires choice_id and a boolean accept")
    cid = actor.campaign_id
    campaign = service.campaigns.get(cid)
    public_arguments = _request(
        service,
        campaign,
        principal,
        {
            "character_id": actor.id,
            "payload": payload,
            "expected_revision": expected_revision,
        },
    )
    scope = _scope(cid, principal, kind)
    replay = service.replay_idempotent(scope, key, public_arguments)
    if replay is not None:
        return replay
    command = deepcopy((campaign.state or {}).get(STATE_KEY))
    if not command:
        raise ValueError("no pending Legendary Resistance decision")
    pending = command["decisions"][-1]
    if pending.get("kind", "legendary_resistance") != kind:
        raise ValueError("roll decision kind does not match its owned choice")
    if pending["id"] != payload["choice_id"] or pending["actor_id"] != actor.id:
        raise ValueError("Legendary Resistance choice belongs to another actor or save")
    if kind == "divine_smite" and payload["accept"]:
        selected = payload["slot"]
        if not isinstance(selected, str) or selected not in {
            v["slot"] for v in pending["result"]["slots"]
        }:
            raise ValueError("Divine Smite slot is not one of the offered choices")
        pending["slot"] = selected
    if actor.revision != expected_revision:
        raise ValueError("character revision conflict for Legendary Resistance")
    if campaign.revision != command["campaign_revision"]:
        raise ValueError("campaign changed during the saving throw decision")
    service.require_current_branch(cid, command["branch_id"])
    records = service.characters.list(campaign_id=cid)
    if {r.id: r.revision for r in records} != command["actor_revisions"]:
        raise ValueError("actor state changed during the saving throw decision")
    stream = s.active_random_stream()
    if (stream.position, stream.seed) != (command["random_position"], command["random_seed"]):
        raise ValueError("random stream changed during the saving throw decision")
    pending["accept"] = payload["accept"]
    name = command["name"]
    arguments = deepcopy(command["arguments"])
    original_principal = command["principal_id"]
    # A current DM may finish a suspended declaration after its initiating DM
    # loses access. A player decides only their own save; they cannot take over
    # the initiating command's authority.
    executor = principal if service.is_dm(cid, principal) else original_principal
    service.authorize_tool_policy(name, executor, cid)
    arguments["principal_id"] = executor
    command["resolution_principal_id"] = executor
    service.validate_request_scope(cid, name, arguments)
    # The original CAS succeeded before suspension. Only our own guarded offer
    # revisions have changed; bind the same command to those verified successors.
    if "expected_revision" in arguments:
        arguments["expected_revision"] = (
            service.characters.get(arguments["character_id"]).revision
            if name in {"character_state_change", "character_action"}
            and arguments.get("character_id")
            else campaign.revision
        )
    if "expected_campaign_revision" in arguments:
        arguments["expected_campaign_revision"] = campaign.revision
    if "expected_campaign_revision" in (arguments.get("payload") or {}):
        arguments["payload"]["expected_campaign_revision"] = campaign.revision
    arguments["idempotency_key"] = key
    function = service.mcp.operations[name].function
    return _attempt(
        service,
        campaign,
        records,
        command,
        function,
        arguments,
        scope=scope,
        key=key,
        public_arguments=public_arguments,
        caller=principal,
    )


def _receipts(service, cid, branch, command):
    mechanics = {
        bardic.MECHANIC
        if d.get("kind") == "bardic_inspiration"
        else smite.MECHANIC
        if d.get("kind") == "divine_smite"
        else MECHANIC
        for d in command["decisions"]
    }
    return s.core_receipts(
        service.effective_rule_context(cid, branch_id=branch),
        sorted(mechanics),
        "roll.owned_decision",
    )


def finalize(service, campaign, campaign_state, updates, response, receipts):
    command = _ACTIVE.get()
    if command is None or not command.value["decisions"]:
        return campaign_state, updates, response, receipts
    rows = list(updates or [])
    response = deepcopy(response)
    for actor_id, count in command.spent.items():
        amount = count - command.applied[actor_id]
        if not amount:
            continue
        existing = next((row for row in rows if row.character_id == actor_id), None)
        actor = service.characters.get(actor_id)
        sheet = deepcopy(existing.sheet if existing else actor.sheet)
        entry = feature(sheet)
        if not entry or int(entry["uses"]["value"]) < amount:
            raise ValueError("Legendary Resistance uses no longer available")
        entry["uses"]["value"] -= amount
        # Character mutations can already have constructed their response card.
        # Refresh only the disclosed resource, preserving its audience projection.
        card = response.get("character")
        if isinstance(card, dict) and card.get("id") == actor_id and card.get("sheet"):
            shown = feature(card["sheet"])
            if shown:
                shown["uses"]["value"] = entry["uses"]["value"]
        updated = (
            replace(existing, sheet=sheet)
            if existing
            else s.CharacterStateUpdate(
                character_id=actor_id,
                sheet=sheet,
                notes=actor.notes,
                expected_revision=actor.revision,
            )
        )
        rows = [row for row in rows if row.character_id != actor_id] + [updated]
        command.applied[actor_id] = count
    state = deepcopy(campaign_state if campaign_state is not None else campaign.state)
    for decision in command.value["decisions"]:
        if (
            decision.get("kind") != "bardic_inspiration"
            or not decision.get("accept")
            or decision["feature_id"] not in command.inspired
            or decision["feature_id"] in command.inspiration_applied
        ):
            continue
        actor_id = decision["actor_id"]
        existing = next((row for row in rows if row.character_id == actor_id), None)
        actor = service.characters.get(actor_id)
        sheet = deepcopy(existing.sheet if existing else actor.sheet)
        effect = next(
            (e for e in sheet.get("effects", []) if e["id"] == decision["feature_id"]), None
        )
        original_effect = bardic.held(actor.sheet)
        if effect is None or original_effect is None or original_effect["id"] != effect["id"]:
            raise ValueError("Bardic Inspiration die no longer available")
        effect.update(active=False, ended_reason="bardic_inspiration_spent")
        state["bardic_inspiration_grants"][actor_id]["spent"] = True
        updated = (
            replace(existing, sheet=sheet)
            if existing
            else s.CharacterStateUpdate(
                character_id=actor_id,
                sheet=sheet,
                notes=actor.notes,
                expected_revision=actor.revision,
            )
        )
        rows = [row for row in rows if row.character_id != actor_id] + [updated]
        command.inspiration_applied.add(decision["feature_id"])
        card = response.get("character")
        if isinstance(card, dict) and card.get("id") == actor_id and card.get("sheet"):
            for shown in card["sheet"].get("effects", []):
                if shown["id"] == effect["id"]:
                    shown.update(active=False, ended_reason="bardic_inspiration_spent")
    state.pop(STATE_KEY, None)
    command.finalized = True
    for kind in ("legendary_resistance", "bardic_inspiration", "divine_smite"):
        decisions = [
            d for d in command.value["decisions"] if d.get("kind", "legendary_resistance") == kind
        ]
        if decisions:
            response[kind] = deepcopy(decisions)
    return (
        state,
        rows,
        response,
        [
            *list(receipts or []),
            *_receipts(service, campaign.id, command.value["branch_id"], command.value),
        ],
    )
