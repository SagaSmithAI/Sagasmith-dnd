"""Portable JSON CLI used by every SagaSmith agent platform."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sagasmith_core import (
    CampaignService,
    CharacterService,
    CharacterStateUpdate,
    EventService,
    MemoryService,
    ModuleService,
    RevisionService,
    RuleProfileService,
    RuleService,
    SnapshotService,
    StateMutationService,
)
from sagasmith_core.documents import converter_for
from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd import __version__
from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    add_memory,
    adjust_wallet,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    receive_inventory_item,
    remove_effect,
    remove_inventory_item,
    resolve_memory,
    set_resource_value,
    set_spell_prepared,
    update_inventory_item,
    validate_character_notes,
    validate_character_sheet,
    validate_party_state,
)
from sagasmith_dnd.engine import resolve_check, roll
from sagasmith_dnd.module_profile import DndModuleProfile
from sagasmith_dnd.runtime import database, dense_components
from sagasmith_dnd.system import DND5E


class CliError(RuntimeError):
    def __init__(self, code: str, message: str, *, exit_code: int = 5) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def _json_value(raw: str | None, default: Any = None) -> Any:
    if raw is None:
        return default
    if raw.startswith("@"):
        raw = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("invalid_json", str(exc), exit_code=2) from exc


def _dict(raw: str | None) -> dict[str, Any]:
    value = _json_value(raw, {})
    if not isinstance(value, dict):
        raise CliError("object_required", "expected a JSON object", exit_code=2)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sagasmith-dnd")
    parser.add_argument("group")
    parser.add_argument("action", nargs="?")
    parser.add_argument("subaction", nargs="?")
    parser.add_argument("--campaign")
    parser.add_argument("--id")
    parser.add_argument("--name")
    parser.add_argument("--slug")
    parser.add_argument("--status")
    parser.add_argument("--description")
    parser.add_argument("--edition", choices=("2014", "2024"))
    parser.add_argument("--locale")
    parser.add_argument("--publications", nargs="*")
    parser.add_argument("--options")
    parser.add_argument("--settings")
    parser.add_argument("--state")
    parser.add_argument("--payload")
    parser.add_argument("--metadata")
    parser.add_argument("--sheet")
    parser.add_argument("--notes")
    parser.add_argument("--type")
    parser.add_argument("--player")
    parser.add_argument("--summary")
    parser.add_argument("--subject")
    parser.add_argument("--content")
    parser.add_argument("--path")
    parser.add_argument("--output")
    parser.add_argument("--source-key")
    parser.add_argument("--title")
    parser.add_argument("--version")
    parser.add_argument("--publication")
    parser.add_argument("--authority", default="primary")
    parser.add_argument("--canonical-source")
    parser.add_argument("--query")
    parser.add_argument("--chunk")
    parser.add_argument("--scene")
    parser.add_argument("--module")
    parser.add_argument("--slot", type=int)
    parser.add_argument("--label", default="")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--progress", type=int, default=0)
    parser.add_argument("--room")
    parser.add_argument("--scope", default="party")
    parser.add_argument("--expression")
    parser.add_argument("--dc", type=int)
    parser.add_argument("--score", type=int, default=10)
    parser.add_argument("--level", type=int, default=1)
    parser.add_argument("--bonus", type=int, default=0)
    parser.add_argument("--proficient", action="store_true")
    parser.add_argument("--advantage", action="store_true")
    parser.add_argument("--disadvantage", action="store_true")
    parser.add_argument("--dense", action="store_true")
    parser.add_argument("--item")
    parser.add_argument("--target")
    parser.add_argument("--amount", type=int)
    parser.add_argument("--denomination")
    parser.add_argument("--slot-name")
    parser.add_argument("--effect")
    parser.add_argument("--memory-id")
    parser.add_argument("--spell")
    parser.add_argument("--resource")
    return parser


def _require(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise CliError("argument_required", f"--{name} is required", exit_code=2)
    return value


def _profile_for(args, profiles: RuleProfileService) -> tuple[str | None, str | None, list[str]]:
    edition = args.edition
    locale = args.locale
    publications = list(args.publications or [])
    if args.campaign:
        profile = profiles.get(args.campaign)
        if profile:
            edition = edition or profile.edition
            locale = locale or profile.locale
            publications = publications or list(profile.publications)
    return edition, locale, publications


def _campaign_revision(revisions, before, after, operation: str) -> None:
    fields = ("name", "status", "description", "settings", "state", "revision")
    revisions.record(
        before.id,
        operation=operation,
        entity_type="campaign",
        entity_id=before.id,
        before={name: getattr(before, name) for name in fields},
        after={name: getattr(after, name) for name in fields},
    )


def _character_revision(revisions, before, after, operation: str) -> None:
    if before.campaign_id is None:
        return
    fields = ("name", "player_name", "summary", "sheet", "notes", "revision")
    revisions.record(
        before.campaign_id,
        operation=operation,
        entity_type="character",
        entity_id=before.id,
        before={name: getattr(before, name) for name in fields},
        after={name: getattr(after, name) for name in fields},
    )


def _character_view(character) -> dict[str, Any]:
    sheet = validate_character_sheet(character.sheet)
    notes = validate_character_notes(character.notes, character_type=character.character_type)
    result = asdict(character)
    result["sheet"] = sheet
    result["notes"] = notes
    result["derived"] = derive_character_sheet(sheet)
    return result


def _party_sheet(inventory: dict[str, Any]) -> dict[str, Any]:
    sheet = default_character_sheet()
    sheet["inventory"] = inventory
    return validate_character_sheet(sheet)


def _party_state_with_sheet(state: dict[str, Any], sheet: dict[str, Any]) -> dict[str, Any]:
    value = validate_party_state(state)
    value["party"]["inventory"] = sheet["inventory"]
    return validate_party_state(value)


def _persist_character(characters, revisions, before, *, sheet=None, notes=None, operation: str):
    updated = characters.update(
        before.id,
        sheet=validate_character_sheet(sheet) if sheet is not None else None,
        notes=(
            validate_character_notes(notes, character_type=before.character_type)
            if notes is not None
            else None
        ),
    )
    _character_revision(revisions, before, updated, operation)
    return updated


def _dispatch(args) -> Any:
    if args.group == "version":
        return {"version": __version__, "system_id": DND5E.id}
    if args.group == "capabilities":
        return {
            "system_id": DND5E.id,
            "editions": ["2014", "2024"],
            "default_edition": "2024",
            "commands": [
                "campaign",
                "character",
                "party",
                "event",
                "rules",
                "module",
                "save",
                "memory",
                "state",
                "roll",
                "combat",
            ],
            "agent_interface": "skill+json-cli",
        }

    db = database()
    try:
        if args.group == "database" and args.action == "upgrade":
            db.upgrade_schema()
            return {"upgraded": True, "database_url": db.url}
        if args.group == "doctor":
            embedder, vectors = dense_components()
            return {
                "ok": True,
                "version": __version__,
                "database_url": db.url,
                "database_ready": True,
                "dense_enabled": embedder is not None,
                "embedding_model": embedder.model_name if embedder else None,
                "vector_enabled": bool(vectors and vectors.enabled),
                "ocr_provider": None,
            }

        campaigns = CampaignService(db)
        characters = CharacterService(db)
        profiles = RuleProfileService(db)
        events = EventService(db)
        rules = RuleService(db)
        modules = ModuleService(db)
        saves = SnapshotService(db)
        memories = MemoryService(db)
        revisions = RevisionService(db)

        if args.group == "campaign":
            if args.action in {"create", "start"}:
                campaign = campaigns.create(
                    system_id=DND5E.id,
                    name=_require(args.name, "name"),
                    slug=args.slug,
                    description=args.description or "",
                    settings={**DND5E.campaign_defaults, **_dict(args.settings)},
                    state=validate_party_state(_dict(args.state)),
                )
                profile = profiles.set(
                    campaign.id,
                    edition=args.edition or "2024",
                    locale=args.locale or "en",
                    publications=args.publications or [],
                    options=_dict(args.options),
                )
                result = {"campaign": asdict(campaign), "rule_profile": asdict(profile)}
                if args.action == "start":
                    result["snapshot"] = asdict(saves.create(campaign.id, label="Initial state"))
                return result
            if args.action == "list":
                return {
                    "campaigns": [
                        asdict(item)
                        for item in campaigns.list(system_id=DND5E.id, status=args.status)
                    ]
                }
            if args.action == "show":
                campaign_id = _require(args.campaign or args.id, "campaign")
                return {
                    "campaign": asdict(campaigns.get(campaign_id)),
                    "rule_profile": (
                        asdict(profile) if (profile := profiles.get(campaign_id)) else None
                    ),
                }
            if args.action in {"update", "archive"}:
                campaign_id = _require(args.campaign or args.id, "campaign")
                before = campaigns.get(campaign_id)
                updated = campaigns.update(
                    campaign_id,
                    name=args.name,
                    status="archived" if args.action == "archive" else args.status,
                    description=args.description,
                    settings=_dict(args.settings) if args.settings else None,
                    state=validate_party_state(_dict(args.state)) if args.state else None,
                )
                _campaign_revision(revisions, before, updated, "campaign.update")
                return asdict(updated)
            if args.action == "delete":
                campaign_id = _require(args.campaign or args.id, "campaign")
                campaigns.delete(campaign_id)
                return {"deleted": campaign_id}
            if args.action == "rules-get":
                value = profiles.get(_require(args.campaign, "campaign"))
                return asdict(value) if value else None
            if args.action == "rules-set":
                return asdict(
                    profiles.set(
                        _require(args.campaign, "campaign"),
                        edition=_require(args.edition, "edition"),
                        locale=args.locale or "en",
                        publications=args.publications or [],
                        options=_dict(args.options),
                    )
                )

        if args.group == "character":
            if args.action == "create":
                character_type = args.type or "pc"
                if character_type not in DND5E.character_types:
                    raise CliError(
                        "invalid_value", "--type must be pc, npc, or monster", exit_code=2
                    )
                created = characters.create(
                    system_id=DND5E.id,
                    campaign_id=args.campaign,
                    name=_require(args.name, "name"),
                    character_type=character_type,
                    player_name=args.player,
                    summary=args.summary or "",
                    sheet=validate_character_sheet(_dict(args.sheet)),
                    notes=validate_character_notes(
                        _dict(args.notes), character_type=character_type
                    ),
                )
                return _character_view(created)
            if args.action == "build":
                character_type = args.type or "pc"
                if character_type not in DND5E.character_types:
                    raise CliError(
                        "invalid_value", "--type must be pc, npc, or monster", exit_code=2
                    )
                template, instance = characters.create_with_instance(
                    system_id=DND5E.id,
                    campaign_id=_require(args.campaign, "campaign"),
                    name=_require(args.name, "name"),
                    character_type=character_type,
                    player_name=args.player,
                    summary=args.summary or "",
                    sheet=validate_character_sheet(_dict(args.sheet)),
                    notes=validate_character_notes(
                        _dict(args.notes), character_type=character_type
                    ),
                )
                return {
                    "template": _character_view(template),
                    "instance": _character_view(instance),
                }
            if args.action == "library" and args.subaction == "list":
                return {
                    "characters": [
                        asdict(item)
                        for item in characters.list_library(
                            system_id=DND5E.id,
                            character_type=args.type,
                        )
                    ]
                }
            if args.action == "instantiate":
                template = characters.get(_require(args.id, "id"))
                if template.system_id != DND5E.id:
                    raise CliError("invalid_value", "template must be a D&D character", exit_code=2)
                if template.campaign_id is not None:
                    raise CliError(
                        "template_required",
                        "only a library PC or NPC can be instantiated",
                        exit_code=2,
                    )
                created = characters.instantiate(
                    template.id,
                    campaign_id=_require(args.campaign, "campaign"),
                    name=args.name,
                    player_name=args.player,
                )
                return _character_view(created)
            if args.action == "list":
                campaign_id = _require(args.campaign, "campaign")
                return {
                    "characters": [
                        asdict(item)
                        for item in characters.list(
                            system_id=DND5E.id,
                            campaign_id=campaign_id,
                            character_type=args.type,
                        )
                    ]
                }
            if args.action == "show":
                return _character_view(characters.get(_require(args.id, "id")))
            if args.action == "update":
                sheet = _dict(args.sheet) if args.sheet else None
                before = characters.get(_require(args.id, "id"))
                updated = characters.update(
                    _require(args.id, "id"),
                    name=args.name,
                    player_name=args.player,
                    summary=args.summary,
                    sheet=validate_character_sheet(sheet) if sheet is not None else None,
                    notes=(
                        validate_character_notes(
                            _dict(args.notes), character_type=before.character_type
                        )
                        if args.notes
                        else None
                    ),
                )
                _character_revision(revisions, before, updated, "character.update")
                return _character_view(updated)
            if args.action == "inventory":
                before = characters.get(_require(args.id, "id"))
                if args.subaction == "list":
                    return before.sheet["inventory"]
                if args.subaction == "add":
                    sheet, item_id = add_inventory_item(before.sheet, _dict(args.payload))
                    updated = _persist_character(
                        characters,
                        revisions,
                        before,
                        sheet=sheet,
                        operation="character.inventory.add",
                    )
                    return {"character": _character_view(updated), "item_id": item_id}
                if args.subaction == "update":
                    sheet = update_inventory_item(
                        before.sheet, _require(args.item, "item"), _dict(args.payload)
                    )
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation="character.inventory.update",
                        )
                    )
                if args.subaction == "remove":
                    sheet, removed = remove_inventory_item(
                        before.sheet, _require(args.item, "item"), args.amount
                    )
                    updated = _persist_character(
                        characters,
                        revisions,
                        before,
                        sheet=sheet,
                        operation="character.inventory.remove",
                    )
                    return {"character": _character_view(updated), "removed": removed}
                if args.subaction == "transfer":
                    target = characters.get(_require(args.target, "target"))
                    if before.id == target.id:
                        raise CliError(
                            "invalid_value", "source and target must differ", exit_code=2
                        )
                    if before.campaign_id is None or before.campaign_id != target.campaign_id:
                        raise CliError(
                            "invalid_value", "characters must share a campaign", exit_code=2
                        )
                    source_sheet, moved = remove_inventory_item(
                        before.sheet, _require(args.item, "item"), args.amount
                    )
                    target_sheet = receive_inventory_item(target.sheet, moved)
                    StateMutationService(db).replace(
                        before.campaign_id,
                        character_updates=[
                            CharacterStateUpdate(
                                before.id, source_sheet, before.notes, before.revision
                            ),
                            CharacterStateUpdate(
                                target.id, target_sheet, target.notes, target.revision
                            ),
                        ],
                    )
                    source_after = characters.get(before.id)
                    target_after = characters.get(target.id)
                    _character_revision(
                        revisions, before, source_after, "character.inventory.transfer"
                    )
                    _character_revision(
                        revisions, target, target_after, "character.inventory.receive"
                    )
                    return {
                        "source": _character_view(source_after),
                        "target": _character_view(target_after),
                        "item": moved,
                    }
            if args.action == "wallet":
                before = characters.get(_require(args.id, "id"))
                denomination = _require(args.denomination, "denomination")
                amount = _require(args.amount, "amount")
                if amount <= 0:
                    raise CliError("invalid_value", "--amount must be positive", exit_code=2)
                if args.subaction == "credit":
                    sheet = adjust_wallet(before.sheet, denomination, amount)
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation="character.wallet.credit",
                        )
                    )
                if args.subaction == "debit":
                    sheet = adjust_wallet(before.sheet, denomination, -amount)
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation="character.wallet.debit",
                        )
                    )
                if args.subaction == "transfer":
                    target = characters.get(_require(args.target, "target"))
                    if (
                        before.id == target.id
                        or before.campaign_id is None
                        or before.campaign_id != target.campaign_id
                    ):
                        raise CliError(
                            "invalid_value",
                            "characters must share a campaign and differ",
                            exit_code=2,
                        )
                    source_sheet = adjust_wallet(before.sheet, denomination, -amount)
                    target_sheet = adjust_wallet(target.sheet, denomination, amount)
                    StateMutationService(db).replace(
                        before.campaign_id,
                        character_updates=[
                            CharacterStateUpdate(
                                before.id, source_sheet, before.notes, before.revision
                            ),
                            CharacterStateUpdate(
                                target.id, target_sheet, target.notes, target.revision
                            ),
                        ],
                    )
                    source_after = characters.get(before.id)
                    target_after = characters.get(target.id)
                    _character_revision(
                        revisions, before, source_after, "character.wallet.transfer"
                    )
                    _character_revision(revisions, target, target_after, "character.wallet.receive")
                    return {
                        "source": _character_view(source_after),
                        "target": _character_view(target_after),
                    }
            if args.action == "equipment":
                before = characters.get(_require(args.id, "id"))
                if args.subaction == "equip":
                    sheet = equip_inventory_item(
                        before.sheet,
                        _require(args.item, "item"),
                        _require(args.slot_name, "slot-name"),
                    )
                elif args.subaction == "unequip":
                    sheet = equip_inventory_item(before.sheet, _require(args.item, "item"), None)
                else:
                    sheet = None
                if sheet is not None:
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation=f"character.equipment.{args.subaction}",
                        )
                    )
            if args.action == "effect":
                before = characters.get(_require(args.id, "id"))
                if args.subaction == "list":
                    return before.sheet["effects"]
                if args.subaction == "add":
                    sheet, effect_id = add_effect(before.sheet, _dict(args.payload))
                    updated = _persist_character(
                        characters, revisions, before, sheet=sheet, operation="character.effect.add"
                    )
                    return {"character": _character_view(updated), "effect_id": effect_id}
                if args.subaction == "remove":
                    sheet = remove_effect(before.sheet, _require(args.effect, "effect"))
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation="character.effect.remove",
                        )
                    )
            if args.action == "memory":
                before = characters.get(_require(args.id, "id"))
                if args.subaction == "list":
                    return validate_character_notes(
                        before.notes, character_type=before.character_type
                    )["memories"]
                if args.subaction == "add":
                    notes, memory_id = add_memory(before.notes, _dict(args.payload))
                    updated = _persist_character(
                        characters, revisions, before, notes=notes, operation="character.memory.add"
                    )
                    return {"character": _character_view(updated), "memory_id": memory_id}
                if args.subaction == "resolve":
                    notes = resolve_memory(before.notes, _require(args.memory_id, "memory-id"))
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            notes=notes,
                            operation="character.memory.resolve",
                        )
                    )
            if args.action == "spell":
                before = characters.get(_require(args.id, "id"))
                if args.subaction == "list":
                    return before.sheet["content"]["spells"]
                if args.subaction in {"prepare", "unprepare"}:
                    sheet = set_spell_prepared(
                        before.sheet, _require(args.spell, "spell"), args.subaction == "prepare"
                    )
                    return _character_view(
                        _persist_character(
                            characters,
                            revisions,
                            before,
                            sheet=sheet,
                            operation=f"character.spell.{args.subaction}",
                        )
                    )
            if args.action == "resource" and args.subaction == "set":
                before = characters.get(_require(args.id, "id"))
                sheet = set_resource_value(
                    before.sheet,
                    _require(args.resource, "resource"),
                    _require(args.amount, "amount"),
                )
                return _character_view(
                    _persist_character(
                        characters,
                        revisions,
                        before,
                        sheet=sheet,
                        operation="character.resource.set",
                    )
                )

        if args.group == "party":
            campaign_id = _require(args.campaign, "campaign")
            campaign = campaigns.get(campaign_id)
            state = validate_party_state(campaign.state)
            party_sheet = _party_sheet(state["party"]["inventory"])
            if args.action == "show":
                return {
                    "inventory": party_sheet["inventory"],
                    "derived": derive_character_sheet(party_sheet)["inventory"],
                    "notes": state["party"]["notes"],
                }
            if args.action == "inventory":
                if args.subaction == "list":
                    return party_sheet["inventory"]
                if args.subaction == "add":
                    updated_sheet, item_id = add_inventory_item(party_sheet, _dict(args.payload))
                    updated_state = _party_state_with_sheet(state, updated_sheet)
                    after = campaigns.update(campaign_id, state=updated_state)
                    _campaign_revision(revisions, campaign, after, "party.inventory.add")
                    return {"inventory": updated_sheet["inventory"], "item_id": item_id}
                if args.subaction == "remove":
                    updated_sheet, removed = remove_inventory_item(
                        party_sheet, _require(args.item, "item"), args.amount
                    )
                    updated_state = _party_state_with_sheet(state, updated_sheet)
                    after = campaigns.update(campaign_id, state=updated_state)
                    _campaign_revision(revisions, campaign, after, "party.inventory.remove")
                    return {"inventory": updated_sheet["inventory"], "removed": removed}
                if args.subaction in {"deposit", "withdraw"}:
                    character = characters.get(_require(args.id, "id"))
                    if character.campaign_id != campaign_id:
                        raise CliError(
                            "invalid_value", "character must belong to the campaign", exit_code=2
                        )
                    if args.subaction == "deposit":
                        character_sheet, moved = remove_inventory_item(
                            character.sheet, _require(args.item, "item"), args.amount
                        )
                        updated_party_sheet = receive_inventory_item(party_sheet, moved)
                    else:
                        updated_party_sheet, moved = remove_inventory_item(
                            party_sheet, _require(args.item, "item"), args.amount
                        )
                        character_sheet = receive_inventory_item(character.sheet, moved)
                    updated_state = _party_state_with_sheet(state, updated_party_sheet)
                    StateMutationService(db).replace(
                        campaign_id,
                        campaign_state=updated_state,
                        character_updates=[
                            CharacterStateUpdate(
                                character.id,
                                character_sheet,
                                character.notes,
                                character.revision,
                            )
                        ],
                    )
                    campaign_after = campaigns.get(campaign_id)
                    character_after = characters.get(character.id)
                    _campaign_revision(
                        revisions, campaign, campaign_after, f"party.inventory.{args.subaction}"
                    )
                    _character_revision(
                        revisions, character, character_after, f"party.inventory.{args.subaction}"
                    )
                    return {
                        "inventory": updated_party_sheet["inventory"],
                        "character": _character_view(character_after),
                        "item": moved,
                    }
            if args.action == "wallet":
                denomination = _require(args.denomination, "denomination")
                amount = _require(args.amount, "amount")
                if amount <= 0:
                    raise CliError("invalid_value", "--amount must be positive", exit_code=2)
                if args.subaction in {"credit", "debit"}:
                    updated_sheet = adjust_wallet(
                        party_sheet, denomination, amount if args.subaction == "credit" else -amount
                    )
                    updated_state = _party_state_with_sheet(state, updated_sheet)
                    after = campaigns.update(campaign_id, state=updated_state)
                    _campaign_revision(revisions, campaign, after, f"party.wallet.{args.subaction}")
                    return updated_sheet["inventory"]["wallet"]
                if args.subaction in {"deposit", "withdraw"}:
                    character = characters.get(_require(args.id, "id"))
                    if character.campaign_id != campaign_id:
                        raise CliError(
                            "invalid_value", "character must belong to the campaign", exit_code=2
                        )
                    if args.subaction == "deposit":
                        character_sheet = adjust_wallet(character.sheet, denomination, -amount)
                        updated_party_sheet = adjust_wallet(party_sheet, denomination, amount)
                    else:
                        updated_party_sheet = adjust_wallet(party_sheet, denomination, -amount)
                        character_sheet = adjust_wallet(character.sheet, denomination, amount)
                    updated_state = _party_state_with_sheet(state, updated_party_sheet)
                    StateMutationService(db).replace(
                        campaign_id,
                        campaign_state=updated_state,
                        character_updates=[
                            CharacterStateUpdate(
                                character.id,
                                character_sheet,
                                character.notes,
                                character.revision,
                            )
                        ],
                    )
                    campaign_after = campaigns.get(campaign_id)
                    character_after = characters.get(character.id)
                    _campaign_revision(
                        revisions, campaign, campaign_after, f"party.wallet.{args.subaction}"
                    )
                    _character_revision(
                        revisions, character, character_after, f"party.wallet.{args.subaction}"
                    )
                    return {
                        "wallet": updated_party_sheet["inventory"]["wallet"],
                        "character": _character_view(character_after),
                    }

        if args.group == "event":
            if args.action == "add":
                return asdict(
                    events.add(
                        _require(args.campaign, "campaign"),
                        event_type=args.type or "narrative",
                        summary=_require(args.summary, "summary"),
                        payload=_dict(args.payload),
                    )
                )
            if args.action == "list":
                return {
                    "events": [
                        asdict(item)
                        for item in events.list(
                            _require(args.campaign, "campaign"),
                            limit=args.limit,
                        )
                    ]
                }

        if args.group == "rules":
            embedder, vectors = dense_components() if args.dense else (None, None)
            if args.action in {"sources", "status"}:
                return {"sources": rules.sources(system_id=DND5E.id, edition=args.edition)}
            if args.action == "ingest":
                path = Path(_require(args.path, "path")).expanduser().resolve()
                paths = sorted(path.rglob("*.md")) if path.is_dir() else [path]
                results = []
                canonical_sources = (
                    rules.sources(system_id=DND5E.id, edition=args.edition or "2024")
                    if (args.locale or "en") != "en"
                    else []
                )
                for item in paths:
                    document = converter_for(item).convert(item)
                    relative = item.relative_to(path).as_posix() if path.is_dir() else item.name
                    suffix = "/".join(Path(relative).parts[-2:])
                    canonical_source = args.canonical_source or next(
                        (
                            source["id"]
                            for source in canonical_sources
                            if source["locale"] == "en"
                            and source["source_key"].replace("\\", "/").endswith(suffix)
                        ),
                        None,
                    )
                    results.append(
                        asdict(
                            rules.ingest(
                                system_id=DND5E.id,
                                source_key=args.source_key or relative,
                                title=args.title or item.stem,
                                content=document.content,
                                edition=args.edition or "2024",
                                locale=args.locale or "en",
                                version=args.version or "",
                                publication_id=args.publication or "",
                                authority=args.authority,
                                canonical_source_id=canonical_source,
                                metadata={
                                    "source_path": str(item),
                                    "media_type": document.media_type,
                                    **document.metadata,
                                },
                                embedder=embedder,
                                vector_store=vectors,
                            )
                        )
                    )
                if not path.is_dir():
                    return results[0]
                return {
                    "files": len(paths),
                    "skipped": sum(bool(item["skipped"]) for item in results),
                    "chunks": sum(int(item["chunks"]) for item in results),
                    "embeddings": sum(int(item["embeddings"]) for item in results),
                }
            if args.action == "search":
                edition, locale, publications = _profile_for(args, profiles)
                return {
                    "hits": [
                        asdict(item)
                        for item in rules.search(
                            system_id=DND5E.id,
                            query=_require(args.query, "query"),
                            edition=edition,
                            locale=locale,
                            publications=publications,
                            top_k=args.limit,
                            embedder=embedder,
                            vector_store=vectors,
                        )
                    ]
                }
            if args.action == "expand":
                return rules.expand(_require(args.chunk, "chunk"))

        if args.group == "module":
            embedder, vectors = dense_components() if args.dense else (None, None)
            parser = MarkdownModuleParser(profile=DndModuleProfile())
            if args.action == "inspect":
                path = Path(_require(args.path, "path")).expanduser().resolve()
                document = converter_for(path).convert(path)
                parsed = parser.parse(document.content)
                return {
                    "source_path": str(path),
                    "warnings": list(document.warnings),
                    "metadata": document.metadata,
                    "chapters": len(parsed),
                    "scenes": sum(len(item.scenes) for item in parsed),
                    "chunks": sum(len(scene.chunks) for item in parsed for scene in item.scenes),
                }
            if args.action == "convert":
                path = Path(_require(args.path, "path")).expanduser().resolve()
                output = Path(_require(args.output, "output")).expanduser().resolve()
                document = converter_for(path).convert(path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(document.content, encoding="utf-8")
                return {"output": str(output), "warnings": list(document.warnings)}
            if args.action == "ingest":
                return asdict(
                    modules.ingest_path(
                        campaign_id=_require(args.campaign, "campaign"),
                        path=_require(args.path, "path"),
                        source_key=args.source_key,
                        title=args.title,
                        parser=parser,
                        embedder=embedder,
                        vector_store=vectors,
                    )
                )
            if args.action == "list":
                return {"modules": modules.list(_require(args.campaign, "campaign"))}
            if args.action == "search":
                return {
                    "hits": [
                        asdict(item)
                        for item in modules.search(
                            campaign_id=_require(args.campaign, "campaign"),
                            query=_require(args.query, "query"),
                            top_k=args.limit,
                            embedder=embedder,
                            vector_store=vectors,
                        )
                    ]
                }
            if args.action == "expand":
                return modules.expand(_require(args.chunk, "chunk"))
            if args.action == "read-scene":
                return modules.read_scene(
                    _require(args.campaign, "campaign"),
                    _require(args.scene, "scene"),
                )
            if args.action == "current":
                return {
                    "scene": modules.current_scene(
                        _require(args.campaign, "campaign"),
                        scope_id=args.scope,
                    )
                }
            if args.action in {"index", "export-scenes"}:
                scenes = modules.scene_index(
                    _require(args.campaign, "campaign"),
                    module_id=args.module,
                )
                result = {"campaign_id": args.campaign, "scenes": scenes}
                if args.output:
                    target = Path(args.output).expanduser().resolve()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    result["output"] = str(target)
                return result
            if args.action in {"set-scene", "set-progress"}:
                return modules.set_scene_progress(
                    campaign_id=_require(args.campaign, "campaign"),
                    scene_id=_require(args.scene, "scene"),
                    status=args.status or "current",
                    progress=args.progress,
                    current_room=args.room,
                    state=None if args.state is None else _dict(args.state),
                    scope_id=args.scope,
                )
            if args.action == "activate":
                return modules.set_active(
                    _require(args.campaign, "campaign"),
                    _require(args.module, "module"),
                    active=args.status != "inactive",
                )
            if args.action == "rename":
                return modules.rename(
                    _require(args.campaign, "campaign"),
                    _require(args.module, "module"),
                    _require(args.title, "title"),
                )
            if args.action == "delete":
                modules.delete(
                    _require(args.campaign, "campaign"),
                    _require(args.module, "module"),
                )
                return {"deleted": args.module}

        if args.group == "save":
            campaign_id = _require(args.campaign, "campaign")
            if args.action == "create":
                return asdict(saves.create(campaign_id, label=args.label))
            if args.action == "list":
                return {"snapshots": [asdict(item) for item in saves.list(campaign_id)]}
            if args.action == "show":
                return saves.get(campaign_id, _require(args.slot, "slot"))
            if args.action == "verify":
                return {"valid": saves.verify(campaign_id, _require(args.slot, "slot"))}
            if args.action == "restore":
                return asdict(saves.restore(campaign_id, _require(args.slot, "slot")))
            if args.action == "regenerate-recap":
                return saves.regenerate_recap(
                    campaign_id,
                    _require(args.slot, "slot"),
                )
            if args.action == "lineage":
                return {"lineage": [asdict(item) for item in saves.lineage(campaign_id, args.slot)]}
            if args.action == "export":
                return saves.export(
                    campaign_id,
                    _require(args.slot, "slot"),
                    _require(args.output, "output"),
                )
            if args.action == "delete":
                saves.delete(campaign_id, _require(args.slot, "slot"))
                return {"deleted": args.slot}

        if args.group == "memory":
            campaign_id = _require(args.campaign, "campaign")
            if args.action == "add":
                return asdict(
                    memories.add(
                        campaign_id,
                        content=_require(args.content, "content"),
                        kind=args.type or "fact",
                        subject=args.subject or "",
                        metadata=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "memories": [
                        asdict(item) for item in memories.list(campaign_id, kind=args.type)
                    ]
                }
            if args.action == "search":
                return {
                    "memories": [
                        asdict(item)
                        for item in memories.search(
                            campaign_id,
                            _require(args.query, "query"),
                            limit=args.limit,
                        )
                    ]
                }
            if args.action in {"scope", "status"}:
                values = memories.list(campaign_id, kind=args.type)
                return {
                    "campaign_id": campaign_id,
                    "count": len(values),
                    "memories": [asdict(item) for item in values],
                }

        if args.group == "state":
            campaign_id = _require(args.campaign, "campaign")
            if args.action == "undo":
                return asdict(revisions.undo(campaign_id))
            if args.action == "redo":
                return asdict(revisions.redo(campaign_id))
            if args.action == "history":
                return {
                    "revisions": [
                        asdict(item) for item in revisions.history(campaign_id, limit=args.limit)
                    ]
                }

        if args.group == "roll":
            if args.action == "dice":
                return asdict(roll(_require(args.expression, "expression")))
            if args.action in {"check", "attack"}:
                return resolve_check(
                    dc=_require(args.dc, "dc"),
                    ability_score=args.score,
                    proficient=args.proficient,
                    level=args.level,
                    bonus=args.bonus,
                    advantage=args.advantage,
                    disadvantage=args.disadvantage,
                )

        if args.group == "combat":
            campaign_id = _require(args.campaign, "campaign")
            campaign = campaigns.get(campaign_id)
            state = dict(campaign.state)
            if args.action == "start":
                before = campaign
                state["combat"] = {
                    "active": True,
                    "round": 1,
                    "turn": 0,
                    **_dict(args.payload),
                }
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "combat.start")
                return state["combat"]
            if args.action == "status":
                return state.get("combat")
            if args.action == "act":
                before = campaign
                combat = dict(state.get("combat") or {})
                if not combat.get("active"):
                    raise CliError("combat_not_active", "combat is not active", exit_code=4)
                combat.update(_dict(args.payload))
                state["combat"] = combat
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "combat.act")
                return combat
            if args.action == "end":
                before = campaign
                result = state.get("combat")
                state["combat"] = None
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "combat.end")
                return {"ended": True, "combat": result}

        raise CliError(
            "unknown_command",
            f"unknown command: {args.group} {args.action or ''}".strip(),
            exit_code=2,
        )
    finally:
        db.dispose()


def _error(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, CliError):
        return exc.code, exc.exit_code
    if isinstance(exc, LookupError):
        return "not_found", 3
    if isinstance(exc, ValueError):
        return "invalid_value", 2
    return "internal_error", 10


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    compact = "--json" in values
    values = [value for value in values if value != "--json"]
    command = ".".join(values[:2])
    try:
        args = _parser().parse_args(values)
        data = _dispatch(args)
        envelope = {
            "ok": True,
            "data": data,
            "error": None,
            "meta": {"command": command, "version": __version__},
        }
        code = 0
    except SystemExit:
        raise
    except Exception as exc:
        error_code, code = _error(exc)
        envelope = {
            "ok": False,
            "data": None,
            "error": {"code": error_code, "message": str(exc)},
            "meta": {"command": command, "version": __version__},
        }
    print(
        json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
