"""Trusted single-user execution policy; rules and commits remain shared services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from copy import deepcopy
from pathlib import Path

from .operations import OperationError, RequestIdentity
from .tool_profiles import campaign_phase


class LocalSession:
    """Serialize commands and durably freeze their protocol inputs before dispatch.

    A journal entry is not a commit receipt. Pending entries replay the *same*
    operation through the existing atomic idempotency service after a crash.
    """

    def __init__(self, runtime, services):
        self.runtime = runtime
        self.services = services
        self.principal = services.config.bound_principal_id
        self.lock = asyncio.Lock()
        self.journal = services.config.home / "runtime" / "local-commands"
        self.journal.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _save(path: Path, value: dict) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def prepare(self, name, arguments, campaign_id):
        args = deepcopy(arguments)
        properties = self.runtime.operations[name].parameters.get("properties", {})
        data = args.get("payload")
        data = data if isinstance(data, dict) else {}
        requested = args.get("campaign_id") or data.get("campaign_id")
        if campaign_id and requested and campaign_id != requested:
            raise PermissionError("local command belongs to another campaign")
        campaign_id = requested or campaign_id
        # An owned continuation belongs to its persisted source, even on another
        # actor's turn. Resolve that authority before falling back to the active actor.
        owned_actor = None
        action = args.get("action")
        choice_id = args.get("choice_id") or data.get("choice_id")
        has_owned_continuation = (
            name == "combat_resolve_attack" and isinstance(action, dict)
            and bool(action.get("spell_resolution_id"))
        ) or (name in {"combat_choice", "combat_reaction_attack", "combat_ready"} and choice_id)
        if campaign_id and has_owned_continuation:
            encounter = self.services.campaigns.get(campaign_id).state.get("combat") or {}
            if name == "combat_resolve_attack" and isinstance(action, dict):
                resolution = dict(encounter.get("spell_resolutions") or {}).get(
                    action.get("spell_resolution_id")
                )
                if isinstance(resolution, dict):
                    owned_actor = resolution.get("caster_id")
            elif name in {"combat_choice", "combat_reaction_attack", "combat_ready"}:
                window = next((item for item in encounter.get("pending", [])
                               if choice_id and item.get("id") == choice_id), None)
                if window:
                    owned_actor = window.get("actor_id")
            if (owned_actor and name == "combat_ready"
                    and action in {"resolve_spell", "resolve_action"} and not data.get("actor_id")):
                data["actor_id"] = owned_actor
                args["payload"] = data
        if campaign_id and "actor_id" in properties and not args.get("actor_id"):
            current = owned_actor or self.context(campaign_id, {}).get("actor_id")
            if current:
                args["actor_id"] = current
        actor_keys = ("character_id", "actor_id", "source_character_id", "target_character_id")
        actors = {}
        for key in actor_keys:
            value = args.get(key) or data.get(key)
            if value:
                actor = self.services.characters.get(value)
                if campaign_id and actor.campaign_id != campaign_id:
                    raise PermissionError("local actor belongs to another campaign")
                campaign_id = campaign_id or actor.campaign_id
                actors[key] = actor
        if args.get("owner") == "party":
            owner = args.get("owner_id")
            if campaign_id and owner != campaign_id:
                raise PermissionError("local owner belongs to another campaign")
            campaign_id = owner
        elif args.get("owner") == "character":
            actor = self.services.characters.get(args["owner_id"])
            if campaign_id and actor.campaign_id != campaign_id:
                raise PermissionError("local owner belongs to another campaign")
            campaign_id = actor.campaign_id
            actors["character_id"] = actor
        campaign = self.services.campaigns.get(campaign_id) if campaign_id else None
        if campaign:
            self.services.access.require_campaign(campaign_id, self.principal)
            if ("expected_state_version" in properties
                    and args.get("expected_state_version") is None):
                progress = self.services.modules.scene_progress_index(
                    campaign_id, scope_id=args.get("scope_id", "party"), fallback_to_party=False,
                )
                args["expected_state_version"] = next(
                    (row["state_version"] for row in progress
                     if row["scene_id"] == args.get("scene_id")), 0,
                )
            if "campaign_id" in properties:
                args["campaign_id"] = campaign_id
            if name in {"campaign_query", "character_query"} and not data.get("campaign_id"):
                data["campaign_id"] = campaign_id
                args["payload"] = data
            if "branch_id" in properties and not args.get("branch_id"):
                args["branch_id"] = self.services.current_branch_id(campaign_id)
            if "expected_branch_id" in properties and not args.get("expected_branch_id"):
                args["expected_branch_id"] = self.services.current_branch_id(campaign_id)
            if ("expected_head_snapshot_id" in properties
                    and "expected_head_snapshot_id" not in args):
                args["expected_head_snapshot_id"] = (
                    self.services.branches.current(campaign_id).head_snapshot_id or ""
                )
        character = actors.get("character_id") or actors.get("actor_id")
        revisions = {
            "expected_campaign_revision": campaign.revision if campaign else None,
            "expected_character_revision": character.revision if character else None,
            "expected_source_revision": (
                actors["source_character_id"].revision if "source_character_id" in actors else None
            ),
            "expected_target_revision": (
                actors["target_character_id"].revision if "target_character_id" in actors else None
            ),
            "expected_revision": (
                campaign.revision if campaign and ("campaign_id" in properties or
                    args.get("owner") == "party") else character.revision if character else None
            ),
        }
        for key, value in revisions.items():
            if key in properties and args.get(key) is None and value is not None:
                args[key] = value
        if campaign_id:
            from .services.sunlight import bind_local_contexts

            bind_local_contexts(self.services, name, args, campaign_id)
        if (name == "character_action" and args.get("action") == "attack_source_object"
                and campaign and data.get("expected_campaign_revision") is None):
            data["expected_campaign_revision"] = campaign.revision
            args["payload"] = data
        if name == "campaign_change":
            action = args.get("action", "update")
            if action in {"short_rest_hit_die", "item_spend", "consumable_use"}:
                recipient = character or actors.get("target_character_id")
                if recipient and data.get("expected_character_revision") is None:
                    data["expected_character_revision"] = recipient.revision
            collections = {
                "party_rest": ("members",),
                "stable_recovery": ("members", "resting_members"),
                "experience_award": ("awards",),
            }.get(action, ())
            for field in collections:
                for member in data.get(field) or []:
                    if not isinstance(member, dict) or not member.get("character_id"):
                        continue  # The shared operation reports the malformed request.
                    actor = self.services.characters.get(member["character_id"])
                    if actor.campaign_id != campaign_id:
                        raise PermissionError("local member belongs to another campaign")
                    if member.get("expected_revision") is None:
                        member["expected_revision"] = actor.revision
            args["payload"] = data
        if name == "inventory_transfer" or (name == "wallet_change" and
                args.get("action") != "adjust"):
            fields = {"expected_campaign_revision"}
            fields |= ({"expected_source_revision", "expected_target_revision"}
                       if args.get("mode") == "character_to_character"
                       else {"expected_character_revision"})
            for key in fields:
                if data.get(key) is None and revisions[key] is not None:
                    data[key] = revisions[key]
            if name == "inventory_transfer" and args.get("mode") != "character_to_character":
                data.setdefault("campaign_id", campaign_id)
            args["payload"] = data
        return args, campaign_id

    async def execute(self, name, arguments, context):
        if context.principal_id != self.principal:
            raise PermissionError("local authority is bound to another principal")
        started = time.perf_counter()
        with self.services.storage.database.measure() as database_metrics:
            async with self.lock:
                queue_ms = (time.perf_counter() - started) * 1000
                operation = self.runtime.operations[name]
                key = arguments.get("idempotency_key")
                write = not operation.annotations.read_only_hint
                if (write and "idempotency_key" in operation.parameters.get("properties", {})
                        and not key):
                    raise OperationError("Host must supply a stable operation ID",
                                         code="operation_id_required")
                intent = {"operation": name, "arguments": arguments,
                          "campaign_id": context.campaign_id, "principal_id": self.principal}
                path = (self.journal / (hashlib.sha256(str(key).encode()).hexdigest() + ".json")
                        if key else None)
                entry = (json.loads(path.read_text(encoding="utf-8"))
                         if path and path.exists() else None)
                if entry:
                    if entry["intent"] != intent:
                        raise OperationError("operation ID already belongs to another intent",
                                             code="idempotency_conflict")
                    args, campaign_id = entry["arguments"], entry["campaign_id"]
                    if campaign_id:
                        self.services.access.require_campaign(campaign_id, self.principal)
                    restore_replay = (
                        name == "snapshot_restore" and "result" not in entry
                        and self.services.idempotency.lookup(
                            f"snapshot-restore:{campaign_id}:{self.principal}", key,
                            {"slot": args["slot"],
                             "expected_branch_id": args["expected_branch_id"]},
                        ) is not None
                    )
                    current_binding = self.binding(campaign_id, args) if campaign_id else None
                    if restore_replay:
                        restore_replay = all(
                            entry["binding"].get(field) == current_binding.get(field)
                            for field in ("authorization_fingerprint", "audience", "role")
                        )
                    if (campaign_id and entry["binding"] != current_binding and not restore_replay):
                        raise OperationError("pending operation belongs to an invalidated timeline",
                                             code="stale_local_operation")
                    if "result" in entry and self._no_write_ruling(entry["result"]):
                        # A confirmed no-write ruling can be reconsidered after
                        # its missing facts change. Unknown writes still keep
                        # their exact original arguments and revision below.
                        args, campaign_id = self.prepare(name, arguments, context.campaign_id)
                        entry = {"intent": intent, "arguments": args, "campaign_id": campaign_id,
                                 "binding": (self.binding(campaign_id, args)
                                             if campaign_id else None)}
                        self._save(path, entry)
                    elif "result" in entry:
                        result = deepcopy(entry["result"])
                        if campaign_id:
                            current = self.context(campaign_id, args)
                            result["receipt_is_current"] = (
                                result.get("local_context", {}).get("state_token")
                                == current["state_token"] and current["state_token"] is not None
                            )
                            result["local_context"] = current
                            result["host_context_binding"] = current["binding"]
                        result["local_execution"] = {
                            "operation_id": key, "replayed": True,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                            "queue_ms": round(queue_ms, 3), "database": database_metrics,
                            "protocol_inputs_managed": True,
                        }
                        return result
                else:
                    args, campaign_id = self.prepare(name, arguments, context.campaign_id)
                    entry = {"intent": intent, "arguments": args, "campaign_id": campaign_id,
                             "binding": (self.binding(campaign_id, args)
                                         if campaign_id and path else None)}
                    if path:
                        self._save(path, entry)
                result = await self.runtime.execute_shared(
                    name, args, context=RequestIdentity(self.principal, campaign_id)
                )
                if isinstance(result, dict):
                    result = deepcopy(result)
                    if name == "campaign_create":
                        campaign_id = campaign_id or result.get("id")
                    if campaign_id:
                        result["local_context"] = self.context(campaign_id, args)
                        result["host_context_binding"] = result["local_context"]["binding"]
                    result["local_execution"] = {
                        "operation_id": key,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "queue_ms": round(queue_ms, 3),
                        "database": database_metrics,
                        "protocol_inputs_managed": True,
                    }
                    if path and self._no_write_ruling(result):
                        path.unlink(missing_ok=True)
                    elif path:
                        entry["result"] = result
                        entry["campaign_id"] = campaign_id
                        # Reuse only this command's freshly read post-commit scope.
                        # Never reuse a pre-write or cross-command authorization boundary.
                        entry["binding"] = (
                            self._journal_binding(result["host_context_binding"])
                            if campaign_id else None
                        )
                        self._save(path, entry)
                return result

    @staticmethod
    def _no_write_ruling(result):
        value = result
        while isinstance(value, dict):
            if value.get("status") not in {None, "pending_ruling"}:
                return False
            if "committed" in value:
                return value.get("status") == "pending_ruling" and value["committed"] is False
            value = value.get("result")
        return False

    def binding(self, campaign_id, arguments=None):
        scope = self.services.authoritative_host_context_binding(
            campaign_id, self.principal, arguments or {},
        )
        return self._journal_binding(scope)

    @staticmethod
    def _journal_binding(scope):
        if scope is None:
            raise PermissionError("local campaign access is no longer available")
        return {"branch_id": scope["branch_id"],
                "timeline_epoch": int(scope["timeline_epoch"]),
                **{key: scope.get(key) for key in (
                    "authorization_fingerprint", "audience", "role", "rules_fingerprint",
                )}}

    def context(self, campaign_id, arguments):
        campaign = self.services.campaigns.get(campaign_id)
        binding = self.services.authoritative_host_context_binding(
            campaign_id, self.principal, arguments,
        )
        value = {
            "campaign_id": campaign_id, "revision": campaign.revision,
            "phase": campaign_phase(campaign.state), "binding": binding,
            "narration_policy": "ambient_only_without_mutation",
        }
        # Private slices are only materialized for the trusted DM audience.
        if binding["audience"] != "dm" or not self.services.is_dm(campaign_id, self.principal):
            value["state_token"] = None
            return value
        revisions = self.services.characters.revision_index(campaign_id=campaign_id)
        encounter = campaign.state.get("combat") or {}
        combatants = encounter.get("combatants") or []
        index = encounter.get("turn_index")
        current = None
        if encounter.get("active") and type(index) is int and 0 <= index < len(combatants):
            current = combatants[index].get("actor_id")
        elif len(revisions) == 1:
            current = next(iter(revisions))
        value["actor_id"] = current
        value["state_token"] = hashlib.sha256(json.dumps({
            "binding": binding, "revision": campaign.revision,
            "actors": revisions,
        }, sort_keys=True).encode()).hexdigest()
        payload = arguments.get("payload")
        values = {**(payload if isinstance(payload, dict) else {}), **arguments}
        selected = {current, *(values.get(key) for key in (
            "actor_id", "target_id", "character_id", "source_character_id", "target_character_id",
        ))}
        if arguments.get("owner") == "character":
            selected.add(arguments.get("owner_id"))
        actors = self.services.characters.list(
            campaign_id=campaign_id, character_ids=[key for key in selected if key in revisions],
        )
        value["actors"] = [{"id": actor.id, "name": actor.name, "revision": actor.revision,
                            "sheet": {key: deepcopy(actor.sheet[key]) for key in (
                                "combat", "abilities", "resources", "conditions", "effects",
                                "inventory", "proficiencies", "spellcasting",
                            ) if key in actor.sheet}}
                           for actor in actors]
        return value
