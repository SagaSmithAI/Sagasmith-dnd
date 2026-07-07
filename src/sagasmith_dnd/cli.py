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
    EventService,
    FoundryDocumentService,
    InventoryService,
    MapService,
    MemoryService,
    ModuleService,
    RevisionService,
    RuleProfileService,
    RuleService,
    SnapshotService,
)
from sagasmith_core.documents import converter_for
from sagasmith_core.items import normalize_inventory
from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd import __version__
from sagasmith_dnd.advancement import apply_advancement
from sagasmith_dnd.activities import execute_document_activity
from sagasmith_dnd.checks import resolve_character_check
from sagasmith_dnd.combat import (
    apply_damage,
    apply_effect,
    attack as combat_attack,
    combat_status,
    death_save,
    end_turn,
    execute_activity,
    heal as combat_heal,
    recover_period,
    remove_effect,
    set_condition,
    start_combat,
)
from sagasmith_dnd.conditions import add_actor_condition, remove_actor_condition
from sagasmith_dnd.concentration import resolve_concentration
from sagasmith_dnd.damage import apply_actor_damage
from sagasmith_dnd.derived import prepare_actor_derived
from sagasmith_dnd.durations import advance_effect_durations
from sagasmith_dnd.effects import recalculate_actor_effects
from sagasmith_dnd.engine import resolve_check, roll
from sagasmith_dnd.module_profile import DndModuleProfile
from sagasmith_dnd.pack_importer import import_foundry_pack
from sagasmith_dnd.rulesets import get_ruleset, list_rulesets, validate_ruleset
from sagasmith_dnd.ready import clear_ready_actions, set_ready_action, trigger_ready_action
from sagasmith_dnd.rests import recover_document_rest
from sagasmith_dnd.rolls import roll_actor_d20
from sagasmith_dnd.server import serve as _serve
from sagasmith_dnd.spatial import cover_between_tokens, move_token_with_movement_cost
from sagasmith_dnd.templates import place_activity_template
from sagasmith_dnd.runtime import database, dense_components
from sagasmith_dnd.system import DND5E, validate_character_sheet


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


def _list(raw: str | None, name: str) -> list[Any]:
    value = _json_value(raw, [])
    if not isinstance(value, list):
        raise CliError("array_required", f"--{name} must be a JSON array", exit_code=2)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sagasmith-dnd")
    parser.add_argument("group")
    parser.add_argument("action", nargs="?")
    parser.add_argument("target", nargs="?")
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
    parser.add_argument("--participants")
    parser.add_argument("--environment")
    parser.add_argument("--effects")
    parser.add_argument("--target-id")
    parser.add_argument("--attack-bonus", type=int)
    parser.add_argument("--amount", type=int)
    parser.add_argument("--damage-type")
    parser.add_argument("--weapon")
    parser.add_argument("--item")
    parser.add_argument("--template")
    parser.add_argument("--owner-type")
    parser.add_argument("--owner-id")
    parser.add_argument("--container")
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--actor", default="runtime")
    parser.add_argument("--reason", default="")
    parser.add_argument("--category")
    parser.add_argument("--rarity", default="")
    parser.add_argument("--tags")
    parser.add_argument("--weight", type=int, default=0)
    parser.add_argument("--value")
    parser.add_argument("--rules")
    parser.add_argument("--identified")
    parser.add_argument("--attunement")
    parser.add_argument("--charges")
    parser.add_argument("--condition")
    parser.add_argument("--custom")
    parser.add_argument("--character")
    parser.add_argument("--ability")
    parser.add_argument("--skill")
    parser.add_argument("--tool")
    parser.add_argument("--source")
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
    parser.add_argument("--token")
    parser.add_argument("--region")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--elevation", type=int)
    parser.add_argument("--direction", type=int, default=0)
    parser.add_argument("--grid-size", type=int)
    parser.add_argument("--grid-units")
    parser.add_argument("--background")
    parser.add_argument("--disposition")
    parser.add_argument("--hidden")
    parser.add_argument("--vision")
    parser.add_argument("--actor-type")
    parser.add_argument("--actor-id")
    parser.add_argument("--shape")
    parser.add_argument("--behavior")
    parser.add_argument("--duration")
    parser.add_argument("--activity")
    parser.add_argument("--payment")
    parser.add_argument("--period")
    parser.add_argument("--minutes", type=int)
    parser.add_argument("--hours", type=int)
    parser.add_argument("--module")
    parser.add_argument("--slot")
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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    return parser


def _require(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise CliError("argument_required", f"--{name} is required", exit_code=2)
    return value


def _bool_value(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _int_value(value: Any, name: str) -> int:
    try:
        return int(_require(value, name))
    except (TypeError, ValueError) as exc:
        raise CliError("invalid_value", f"--{name} must be an integer", exit_code=2) from exc


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


def _item_revision(revisions, before, after, operation: str) -> None:
    value = after or before
    if not value:
        return
    revisions.record(
        value["campaign_id"],
        operation=operation,
        entity_type="item_instance",
        entity_id=value["id"],
        before=before,
        after=after,
    )


def _sheet_for_storage(sheet: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inventory = normalize_inventory(sheet.get("inventory", []))
    stored = dict(sheet)
    if inventory:
        stored["inventory"] = []
        stored["inventory_managed"] = True
    return stored, inventory


def _character_payload(character: dict[str, Any], inventory: InventoryService) -> dict[str, Any]:
    value = dict(character)
    if value.get("campaign_id"):
        sheet = dict(value.get("sheet") or {})
        sheet["inventory"] = inventory.character_inventory(value["id"])
        sheet["inventory_managed"] = True
        value["sheet"] = sheet
    return value


def _scene_token_participants(maps, documents, scene_id: str) -> list[dict[str, Any]]:
    participants = []
    for token in maps.list_tokens(scene_id):
        if token.hidden or not token.actor_id:
            continue
        actor = documents.get_actor(token.actor_id)
        system = dict((actor.derived or {}).get("effective_system") or actor.system or {})
        attributes = dict(system.get("attributes") or {})
        hp = attributes.get("hp", 1)
        ac = attributes.get("ac", 10)
        participants.append(
            {
                "id": actor.id,
                "actor_id": actor.id,
                "actor_type": actor.actor_type,
                "token_id": token.id,
                "name": token.name or actor.name,
                "kind": actor.actor_type,
                "ac": _actor_ac_value(ac),
                "hp": _actor_hp_value(hp),
                "max_hp": _actor_hp_max(hp),
                "speed": _actor_speed(system),
                "features": system.get("features") or [],
                "class_levels": system.get("class_levels") or {},
                "position": {"x": token.x, "y": token.y, "elevation": token.elevation},
            }
        )
    return participants


def _actor_ac_value(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("value", 10) or 10) + int(value.get("bonus", 0) or 0)
    return int(value or 10)


def _actor_hp_value(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("value", 1) or 0)
    return int(value or 1)


def _actor_hp_max(value: Any) -> int:
    if isinstance(value, dict):
        return int(value.get("max", value.get("value", 1)) or 1)
    return int(value or 1)


def _actor_speed(system: dict[str, Any]) -> int:
    attributes = dict(system.get("attributes") or {})
    movement = attributes.get("movement") or system.get("movement") or {}
    if isinstance(movement, dict):
        return int(movement.get("walk", movement.get("value", 30)) or 30)
    return int(system.get("speed", 30) or 30)


def _dispatch(args) -> Any:
    if args.group == "serve":
        _serve(host=args.host, port=args.port)
        return {"status": "stopped"}
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
                "event",
                "rules",
                "module",
                "item",
                "save",
                "memory",
                "state",
                "roll",
                "check",
                "combat",
                "ruleset",
                "scene",
                "token",
                "region",
                "time",
                "effect",
                "rest",
                "activity",
                "game-activity",
                "game-item",
                "reaction",
                "pack",
                "condition",
                "damage",
                "actor",
                "concentration",
                "template",
                "cover",
                "ready",
                "advancement",
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
        inventory = InventoryService(db)
        maps = MapService(db)
        foundry_documents = FoundryDocumentService(db)
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
                    state=_dict(args.state),
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
                    result["snapshot"] = asdict(
                        saves.create(campaign.id, label="Initial state")
                    )
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
                        state=_dict(args.state) if args.state else None,
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
                sheet, initial_inventory = _sheet_for_storage(
                    validate_character_sheet(_dict(args.sheet))
                )
                created = characters.create(
                    system_id=DND5E.id,
                    campaign_id=args.campaign,
                    name=_require(args.name, "name"),
                    character_type=args.type or "pc",
                    player_name=args.player,
                    summary=args.summary or "",
                    sheet=sheet,
                    notes=_dict(args.notes),
                )
                if args.campaign and initial_inventory:
                    inventory.import_inventory(
                        campaign_id=args.campaign,
                        character_id=created.id,
                        inventory=initial_inventory,
                        actor=args.actor,
                    )
                return _character_payload(asdict(created), inventory)
            if args.action == "list":
                return {
                    "characters": [
                        _character_payload(asdict(item), inventory)
                        for item in characters.list(
                            system_id=DND5E.id,
                            campaign_id=args.campaign,
                            character_type=args.type,
                        )
                    ]
                }
            if args.action == "show":
                return _character_payload(
                    asdict(characters.get(_require(args.id, "id"))),
                    inventory,
                )
            if args.action == "update":
                sheet = _dict(args.sheet) if args.sheet else None
                imported_inventory: list[dict[str, Any]] = []
                if sheet is not None:
                    sheet, imported_inventory = _sheet_for_storage(
                        validate_character_sheet(sheet)
                    )
                before = characters.get(_require(args.id, "id"))
                updated = characters.update(
                        _require(args.id, "id"),
                        name=args.name,
                        player_name=args.player,
                        summary=args.summary,
                        sheet=sheet,
                        notes=_dict(args.notes) if args.notes else None,
                    )
                if updated.campaign_id and imported_inventory:
                    inventory.import_inventory(
                        campaign_id=updated.campaign_id,
                        character_id=updated.id,
                        inventory=imported_inventory,
                        replace=True,
                        actor=args.actor,
                    )
                _character_revision(revisions, before, updated, "character.update")
                return _character_payload(asdict(updated), inventory)
            if args.action in {"bind", "unbind"}:
                return asdict(
                    characters.bind(
                        _require(args.id, "id"),
                        args.campaign if args.action == "bind" else None,
                    )
                )

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
                    "chunks": sum(
                        len(scene.chunks) for item in parsed for scene in item.scenes
                    ),
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

        if args.group == "ruleset":
            if args.action == "list":
                return {"rulesets": list_rulesets()}
            if args.action == "show":
                return get_ruleset(args.id or args.edition)
            if args.action == "validate":
                return validate_ruleset(args.id or args.edition)

        if args.group == "pack":
            if args.action != "import":
                raise CliError("unknown_command", f"unknown pack command: {args.action}", exit_code=2)
            return import_foundry_pack(
                foundry_documents,
                campaign_id=_require(args.campaign, "campaign"),
                system_id=DND5E.id,
                path=_require(args.path, "path"),
                actor_id=None if args.actor == "runtime" else args.actor,
            )

        if args.group == "actor":
            if args.action == "create":
                return asdict(
                    foundry_documents.create_actor(
                        campaign_id=_require(args.campaign, "campaign"),
                        system_id=DND5E.id,
                        actor_type=args.type or args.actor_type or "character",
                        name=_require(args.name, "name"),
                        img=args.path or "",
                        system=_dict(args.payload),
                        prototype_token=_dict(args.settings),
                        flags=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "actors": [
                        asdict(item)
                        for item in foundry_documents.list_actors(
                            _require(args.campaign, "campaign"),
                            actor_type=args.type or args.actor_type,
                        )
                    ]
                }
            if args.action == "show":
                actor_id = _require(args.actor if args.actor != "runtime" else args.id, "actor")
                actor = asdict(foundry_documents.get_actor(actor_id))
                items = []
                for item in foundry_documents.list_items(actor["campaign_id"], actor_id=actor_id):
                    value = asdict(item)
                    value["activities"] = [
                        asdict(activity)
                        for activity in foundry_documents.list_activities(item.id)
                    ]
                    items.append(value)
                actor["items"] = items
                actor["effects"] = [
                    asdict(effect)
                    for effect in foundry_documents.list_effects(actor["campaign_id"], actor_id=actor_id)
                ]
                return actor
            if args.action == "prepare":
                return prepare_actor_derived(
                    foundry_documents,
                    campaign_id=_require(args.campaign, "campaign"),
                    actor_id=_require(args.actor if args.actor != "runtime" else args.id, "actor"),
                )
            if args.action == "update":
                return asdict(
                    foundry_documents.update_actor(
                        _require(args.actor if args.actor != "runtime" else args.id, "actor"),
                        system=_dict(args.payload) if args.payload is not None else None,
                        flags=_dict(args.metadata) if args.metadata is not None else None,
                    )
                )
            raise CliError("unknown_command", f"unknown actor command: {args.action}", exit_code=2)

        if args.group == "game-item":
            if args.action == "create":
                payload = _dict(args.payload)
                return asdict(
                    foundry_documents.create_item(
                        campaign_id=_require(args.campaign, "campaign"),
                        system_id=DND5E.id,
                        actor_id=None if args.actor == "runtime" else args.actor,
                        container_id=args.container,
                        item_type=args.type or args.category or "loot",
                        name=_require(args.name, "name"),
                        source_key=args.source_key or "",
                        img=args.path or "",
                        system=dict(payload.get("system") or payload),
                        effects=_list(args.effects, "effects") or list(payload.get("effects") or []),
                        flags=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "items": [
                        asdict(item)
                        for item in foundry_documents.list_items(
                            _require(args.campaign, "campaign"),
                            actor_id=None if args.actor == "runtime" else args.actor,
                            item_type=args.type or args.category,
                        )
                    ]
                }
            if args.action == "show":
                item = asdict(foundry_documents.get_item(_require(args.item or args.id, "item")))
                item["activities"] = [
                    asdict(activity)
                    for activity in foundry_documents.list_activities(item["id"])
                ]
                return item
            if args.action == "update":
                payload = _dict(args.payload)
                return asdict(
                    foundry_documents.update_item(
                        _require(args.item or args.id, "item"),
                        system=dict(payload.get("system") or payload) if payload else None,
                        effects=_list(args.effects, "effects") if args.effects is not None else None,
                        flags=_dict(args.metadata) if args.metadata is not None else None,
                    )
                )
            raise CliError("unknown_command", f"unknown game-item command: {args.action}", exit_code=2)

        if args.group == "game-activity":
            payload = _dict(args.payload)
            if args.action == "create":
                return asdict(
                    foundry_documents.create_activity(
                        item_id=_require(args.item, "item"),
                        activity_type=args.type or args.category or payload.get("type") or "utility",
                        name=args.name or payload.get("name") or "Activity",
                        activation=dict(payload.get("activation") or {}),
                        consumption=dict(payload.get("consumption") or {}),
                        duration=_dict(args.duration) or dict(payload.get("duration") or {}),
                        effects=_list(args.effects, "effects") or list(payload.get("effects") or []),
                        range=dict(payload.get("range") or {}),
                        target=dict(payload.get("target") or {}),
                        uses=dict(payload.get("uses") or {}),
                        system=dict(payload.get("system") or {}),
                        flags=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "activities": [
                        asdict(activity)
                        for activity in foundry_documents.list_activities(_require(args.item, "item"))
                    ]
                }
            if args.action == "show":
                return asdict(foundry_documents.get_activity(_require(args.activity or args.id, "activity")))
            if args.action == "update":
                return asdict(
                    foundry_documents.update_activity(
                        _require(args.activity or args.id, "activity"),
                        activation=dict(payload.get("activation")) if "activation" in payload else None,
                        consumption=dict(payload.get("consumption")) if "consumption" in payload else None,
                        duration=_dict(args.duration) if args.duration is not None else payload.get("duration"),
                        effects=_list(args.effects, "effects") if args.effects is not None else payload.get("effects"),
                        range=dict(payload.get("range")) if "range" in payload else None,
                        target=dict(payload.get("target")) if "target" in payload else None,
                        uses=dict(payload.get("uses")) if "uses" in payload else None,
                        system=dict(payload.get("system")) if "system" in payload else None,
                        flags=_dict(args.metadata) if args.metadata is not None else None,
                    )
                )
            raise CliError("unknown_command", f"unknown game-activity command: {args.action}", exit_code=2)

        if args.group == "advancement":
            if args.action != "apply":
                raise CliError("unknown_command", f"unknown advancement command: {args.action}", exit_code=2)
            return apply_advancement(
                foundry_documents,
                campaign_id=_require(args.campaign, "campaign"),
                actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                advancement=_dict(args.payload),
            )

        if args.group == "scene":
            if args.action == "create":
                return asdict(
                    maps.create_scene(
                        _require(args.campaign, "campaign"),
                        name=_require(args.name, "name"),
                        grid_size=args.grid_size or 70,
                        grid_units=args.grid_units or "ft",
                        width=args.width or 0,
                        height=args.height or 0,
                        background=args.background or "",
                        metadata=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "scenes": [
                        asdict(item)
                        for item in maps.list_scenes(_require(args.campaign, "campaign"))
                    ]
                }
            if args.action == "show":
                scene = asdict(maps.get_scene(_require(args.scene or args.id, "scene")))
                scene["tokens"] = [
                    asdict(item) for item in maps.list_tokens(scene["id"])
                ]
                scene["regions"] = [
                    asdict(item) for item in maps.list_regions(scene["id"])
                ]
                return scene

        if args.group == "token":
            if args.action == "create":
                return asdict(
                    maps.create_token(
                        _require(args.scene, "scene"),
                        actor_type=args.actor_type or args.type or "character",
                        actor_id=args.actor_id or args.character or "",
                        name=_require(args.name, "name"),
                        x=args.x or 0,
                        y=args.y or 0,
                        width=args.width or 1,
                        height=args.height or 1,
                        elevation=args.elevation or 0,
                        disposition=args.disposition or "neutral",
                        hidden=_bool_value(args.hidden, False),
                        vision=_dict(args.vision),
                        actor_delta=_dict(args.payload),
                        metadata=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "tokens": [
                        asdict(item)
                        for item in maps.list_tokens(_require(args.scene, "scene"))
                    ]
                }
            if args.action == "show":
                return asdict(maps.get_token(_require(args.token or args.id, "token")))
            if args.action == "update":
                before = asdict(maps.get_token(_require(args.token or args.id, "token")))
                updated = asdict(
                    maps.update_token(
                        before["id"],
                        actor_type=args.actor_type or args.type,
                        actor_id=args.actor_id or args.character,
                        name=args.name,
                        width=args.width,
                        height=args.height,
                        disposition=args.disposition,
                        hidden=_bool_value(args.hidden) if args.hidden is not None else None,
                        vision=_dict(args.vision) if args.vision is not None else None,
                        actor_delta=_dict(args.payload) if args.payload is not None else None,
                        metadata=_dict(args.metadata) if args.metadata is not None else None,
                    )
                )
                revisions.record(
                    updated["campaign_id"],
                    operation="token.update",
                    entity_type="scene_token",
                    entity_id=updated["id"],
                    before=before,
                    after=updated,
                )
                return updated
            if args.action == "move":
                before = asdict(maps.get_token(_require(args.token or args.id, "token")))
                result = move_token_with_movement_cost(
                    maps,
                    documents=foundry_documents,
                    token_id=before["id"],
                    x=_int_value(args.x, "x"),
                    y=_int_value(args.y, "y"),
                    elevation=args.elevation,
                    metadata=_dict(args.metadata),
                )
                after = {key: value for key, value in result.items() if key != "movement"}
                revisions.record(
                    after["campaign_id"],
                    operation="token.move",
                    entity_type="scene_token",
                    entity_id=after["id"],
                    before=before,
                    after=after,
                )
                return result

        if args.group == "region":
            if args.action == "create":
                return asdict(
                    maps.create_region(
                        _require(args.scene, "scene"),
                        name=_require(args.name, "name"),
                        shape=_dict(args.shape),
                        behavior=args.behavior or args.type or "area",
                        origin_activity_id=args.activity or "",
                        attached_token_id=args.token,
                        duration=_dict(args.duration),
                        metadata=_dict(args.metadata),
                    )
                )
            if args.action == "list":
                return {
                    "regions": [
                        asdict(item)
                        for item in maps.list_regions(_require(args.scene, "scene"))
                    ]
                }

        if args.group == "template":
            if args.action != "place":
                raise CliError("unknown_command", f"unknown template command: {args.action}", exit_code=2)
            return place_activity_template(
                foundry_documents,
                maps,
                scene_id=_require(args.scene, "scene"),
                item_id=_require(args.item, "item"),
                activity_id=_require(args.activity, "activity"),
                x=_int_value(args.x, "x"),
                y=_int_value(args.y, "y"),
                name=args.name,
                actor_id=None if args.actor == "runtime" else args.actor,
                direction=args.direction,
                duration=_dict(args.duration),
            )

        if args.group == "cover":
            if args.action != "check":
                raise CliError("unknown_command", f"unknown cover command: {args.action}", exit_code=2)
            return cover_between_tokens(
                maps,
                scene_id=_require(args.scene, "scene"),
                attacker_token_id=_require(args.token, "token"),
                target_token_id=_require(args.target_id or args.target, "target-id"),
            )

        if args.group == "item":
            if args.action == "template":
                if args.target == "create":
                    return asdict(
                        inventory.create_template(
                            system_id=DND5E.id,
                            name=_require(args.name, "name"),
                            source_key=args.source_key,
                            category=args.category or "gear",
                            rarity=args.rarity or "",
                            tags=list(_json_value(args.tags, [])),
                            weight=args.weight,
                            value=_dict(args.value),
                            rules=_dict(args.rules),
                            description=args.description or "",
                            metadata=_dict(args.metadata),
                        )
                    )
                if args.target == "list":
                    return {
                        "templates": [
                            asdict(item)
                            for item in inventory.list_templates(
                                system_id=DND5E.id,
                                category=args.category,
                            )
                        ]
                    }
                if args.target == "show":
                    return asdict(inventory.get_template(_require(args.template or args.id, "template")))
            if args.action == "add":
                item = inventory.add_item(
                    campaign_id=_require(args.campaign, "campaign"),
                    name=_require(args.name, "name"),
                    template_id=args.template,
                    owner_type=args.owner_type or "party",
                    owner_id=args.owner_id or "party",
                    container_id=args.container,
                    quantity=args.quantity or 1,
                    equipped_slot=args.slot,
                    attunement=args.attunement or "none",
                    identified=_bool_value(args.identified, True),
                    charges=_dict(args.charges),
                    condition=args.condition or "normal",
                    state=_dict(args.custom),
                    actor=args.actor,
                    reason=args.reason,
                )
                data = asdict(item)
                _item_revision(revisions, None, data, "item.add")
                return data
            if args.action == "list":
                return {
                    "items": [
                        asdict(item)
                        for item in inventory.list_items(
                            campaign_id=_require(args.campaign, "campaign"),
                            owner_type=args.owner_type,
                            owner_id=args.owner_id,
                            container_id=args.container,
                        )
                    ]
                }
            if args.action == "show":
                return asdict(inventory.get_item(_require(args.item or args.id, "item")))
            if args.action == "update":
                item_id = _require(args.item or args.id, "item")
                before = asdict(inventory.get_item(item_id))
                updates: dict[str, Any] = {}
                for key, value in {
                    "name": args.name,
                    "quantity": args.quantity,
                    "equipped_slot": args.slot,
                    "attunement": args.attunement,
                    "identified": None if args.identified is None else _bool_value(args.identified),
                    "charges": _dict(args.charges) if args.charges else None,
                    "condition": args.condition,
                    "state": _dict(args.custom) if args.custom else None,
                    "container_id": args.container,
                }.items():
                    if value is not None:
                        updates[key] = value
                after = asdict(
                    inventory.update_item(
                        item_id,
                        actor=args.actor,
                        reason=args.reason,
                        **updates,
                    )
                )
                _item_revision(revisions, before, after, "item.update")
                return after
            if args.action == "move":
                item_id = _require(args.item or args.id, "item")
                before = asdict(inventory.get_item(item_id))
                after = asdict(
                    inventory.move_item(
                        item_id,
                        owner_type=_require(args.owner_type, "owner-type"),
                        owner_id=_require(args.owner_id, "owner-id"),
                        container_id=args.container,
                        actor=args.actor,
                        reason=args.reason,
                    )
                )
                _item_revision(revisions, before, after, "item.move")
                return after
            if args.action in {"equip", "unequip"}:
                item_id = _require(args.item or args.id, "item")
                before = asdict(inventory.get_item(item_id))
                after = asdict(
                    inventory.equip_item(
                        item_id,
                        slot=args.slot if args.action == "equip" else None,
                        actor=args.actor,
                        reason=args.reason,
                    )
                )
                _item_revision(revisions, before, after, f"item.{args.action}")
                return after
            if args.action == "use":
                item_id = _require(args.item or args.id, "item")
                before = asdict(inventory.get_item(item_id))
                after = asdict(
                    inventory.use_item(
                        item_id,
                        quantity=args.quantity or 1,
                        actor=args.actor,
                        reason=args.reason,
                    )
                )
                _item_revision(revisions, before, after, "item.use")
                return after
            if args.action == "delete":
                before = inventory.delete_item(
                    _require(args.item or args.id, "item"),
                    actor=args.actor,
                    reason=args.reason,
                )
                _item_revision(revisions, before, None, "item.delete")
                return {"deleted": before["id"]}
            if args.action == "history":
                return {
                    "entries": [
                        asdict(item)
                        for item in inventory.history(
                            campaign_id=_require(args.campaign, "campaign"),
                            item_id=args.item,
                        )
                    ]
                }

        if args.group == "save":
            campaign_id = _require(args.campaign, "campaign")
            if args.action == "create":
                return asdict(saves.create(campaign_id, label=args.label))
            if args.action == "list":
                return {"snapshots": [asdict(item) for item in saves.list(campaign_id)]}
            if args.action == "show":
                return saves.get(campaign_id, _int_value(args.slot, "slot"))
            if args.action == "verify":
                return {"valid": saves.verify(campaign_id, _int_value(args.slot, "slot"))}
            if args.action == "restore":
                return asdict(saves.restore(campaign_id, _int_value(args.slot, "slot")))
            if args.action == "regenerate-recap":
                return saves.regenerate_recap(
                    campaign_id,
                    _int_value(args.slot, "slot"),
                )
            if args.action == "lineage":
                return {
                    "lineage": [
                        asdict(item)
                        for item in saves.lineage(
                            campaign_id,
                            int(args.slot) if args.slot is not None else None,
                        )
                    ]
                }
            if args.action == "export":
                return saves.export(
                    campaign_id,
                    _int_value(args.slot, "slot"),
                    _require(args.output, "output"),
                )
            if args.action == "delete":
                saves.delete(campaign_id, _int_value(args.slot, "slot"))
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

        if args.group == "check":
            if args.action not in {"ability", "skill", "save", "tool", "initiative"}:
                raise CliError("unknown_command", f"unknown check type: {args.action}", exit_code=2)
            character = characters.get(_require(args.character or args.id, "character"))
            dc = args.dc if args.dc is not None else 0 if args.action == "initiative" else None
            return resolve_character_check(
                sheet=character.sheet,
                check_type=args.action,
                dc=_require(dc, "dc"),
                ability=args.ability or args.target,
                skill=args.skill or args.target,
                tool=args.tool or args.target,
                bonus=args.bonus,
                advantage=args.advantage,
                disadvantage=args.disadvantage,
                source=args.source or args.reason,
            )

        if args.group == "roll":
            if args.action in {"ability", "skill", "save", "initiative"} and args.campaign and args.actor != "runtime":
                dc = args.dc if args.dc is not None else 0 if args.action == "initiative" else None
                return roll_actor_d20(
                    foundry_documents,
                    campaign_id=args.campaign,
                    actor_id=args.actor,
                    roll_type=args.action,
                    dc=_require(dc, "dc"),
                    ability=args.ability or args.target,
                    skill=args.skill or args.target,
                    bonus=args.bonus,
                    advantage=args.advantage,
                    disadvantage=args.disadvantage,
                    source=args.source or args.reason,
                )
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
                payload = _dict(args.payload)
                participants = _list(args.participants, "participants") if args.participants else payload.pop("participants", [])
                scene_id = args.scene or payload.pop("scene_id", None)
                if not participants and scene_id:
                    participants = _scene_token_participants(maps, foundry_documents, scene_id)
                environment = _dict(args.environment) if args.environment else payload.pop("environment", {})
                state["combat"] = start_combat(
                    name=args.name or payload.pop("name", "Combat"),
                    participants=participants,
                    scene_id=scene_id,
                    environment={**environment, **payload},
                )
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "combat.start")
                return combat_status(state["combat"])
            if args.action == "status":
                return combat_status(state.get("combat"))
            if args.action in {"attack", "damage", "heal", "condition", "death-save", "end-turn"}:
                before = campaign
                combat = state.get("combat")
                if args.action == "attack":
                    combat, result = combat_attack(
                        combat,
                        actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                        target_id=_require(args.target_id or args.target, "target-id"),
                        attack_bonus=args.attack_bonus if args.attack_bonus is not None else args.bonus,
                        damage_expression=args.expression,
                        damage_type=args.damage_type,
                        advantage=args.advantage,
                        disadvantage=args.disadvantage,
                        label=args.weapon or args.name or "",
                    )
                elif args.action == "damage":
                    amount = args.amount
                    roll_result = None
                    if amount is None:
                        rolled = roll(_require(args.expression, "expression"))
                        amount = rolled.total
                        roll_result = asdict(rolled)
                    combat, result = apply_damage(
                        combat,
                        target_id=_require(args.target_id or args.target, "target-id"),
                        amount=amount,
                        damage_type=args.damage_type or "",
                        source=args.reason,
                        roll_result=roll_result,
                    )
                elif args.action == "heal":
                    amount = args.amount
                    if amount is None:
                        amount = roll(_require(args.expression, "expression")).total
                    combat, result = combat_heal(
                        combat,
                        target_id=_require(args.target_id or args.target, "target-id"),
                        amount=amount,
                        source=args.reason,
                    )
                elif args.action == "condition":
                    mode = (args.target or "").lower()
                    if mode not in {"add", "remove"}:
                        raise CliError("invalid_value", "combat condition target must be add or remove", exit_code=2)
                    combat, result = set_condition(
                        combat,
                        target_id=_require(args.target_id, "target-id"),
                        condition=_require(args.condition, "condition"),
                        present=mode == "add",
                    )
                elif args.action == "death-save":
                    combat, result = death_save(
                        combat,
                        target_id=_require(args.target_id or args.target, "target-id"),
                        advantage=args.advantage,
                        disadvantage=args.disadvantage,
                    )
                else:
                    combat, result = end_turn(
                        combat,
                        actor_id=None if args.actor == "runtime" else args.actor,
                    )
                state["combat"] = combat
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, f"combat.{args.action}")
                return {"result": result, "combat": combat_status(combat)}
            if args.action == "act":
                raise CliError(
                    "runtime_authority_required",
                    "combat act is disabled; use activity, combat, token, effect, time, or rest commands",
                    exit_code=2,
                )
            if args.action == "end":
                before = campaign
                result = state.get("combat")
                state["combat"] = None
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "combat.end")
                return {"ended": True, "combat": result}

        if args.group == "activity":
            if args.action != "use":
                raise CliError("unknown_command", f"unknown activity command: {args.action}", exit_code=2)
            campaign_id = _require(args.campaign, "campaign")
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            if args.item:
                state, result = execute_document_activity(
                    foundry_documents,
                    campaign_id=campaign_id,
                    state=state,
                    actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                    item_id=args.item,
                    activity_id=_require(args.activity or args.target, "activity"),
                    target_id=args.target_id,
                    payment=args.payment,
                    payload=_dict(args.payload),
                )
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "activity.use")
                return result
            combat, result = execute_activity(
                state.get("combat"),
                actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                activity_id=_require(args.activity or args.target, "activity"),
                target_id=args.target_id,
                payment=args.payment,
                payload=_dict(args.payload),
            )
            state["combat"] = combat
            updated = campaigns.update(campaign_id, state=state)
            _campaign_revision(revisions, before, updated, "activity.use")
            return {"result": result, "combat": combat_status(combat)}

        if args.group == "reaction":
            campaign_id = _require(args.campaign, "campaign")
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            runtime = dict(state.get("runtime") or {})
            pending = list(runtime.get("pending") or [])
            if args.action in {None, "list"}:
                actor_id = args.actor if args.actor != "runtime" else None
                return {
                    "pending": [
                        item
                        for item in pending
                        if item.get("status", "pending") == "pending"
                        and (actor_id is None or item.get("actor_id") == actor_id)
                    ]
                }
            if args.action in {"resolve", "decline"}:
                window_id = _require(args.id or args.target, "id")
                changed = False
                updated_pending = []
                for item in pending:
                    value = dict(item)
                    if value.get("id") == window_id and value.get("status", "pending") == "pending":
                        value["status"] = "resolved" if args.action == "resolve" else "declined"
                        value["response"] = _dict(args.payload)
                        changed = True
                    updated_pending.append(value)
                if not changed:
                    raise CliError("not_found", f"reaction window not found: {window_id}", exit_code=5)
                runtime["pending"] = updated_pending
                state["runtime"] = runtime
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, f"reaction.{args.action}")
                return {"pending": updated_pending}
            raise CliError("unknown_command", f"unknown reaction command: {args.action}", exit_code=2)

        if args.group == "ready":
            campaign_id = _require(args.campaign, "campaign")
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            if args.action == "set":
                state, result = set_ready_action(
                    state,
                    actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                    trigger=_require(args.condition or args.reason, "condition"),
                    payload=_dict(args.payload),
                )
            elif args.action == "trigger":
                state, result = trigger_ready_action(
                    state,
                    ready_id=_require(args.id or args.target, "id"),
                )
            elif args.action == "clear":
                state, result = clear_ready_actions(
                    state,
                    actor_id=None if args.actor == "runtime" else args.actor,
                )
            else:
                raise CliError("unknown_command", f"unknown ready command: {args.action}", exit_code=2)
            updated = campaigns.update(campaign_id, state=state)
            _campaign_revision(revisions, before, updated, f"ready.{args.action}")
            return result

        if args.group == "condition":
            campaign_id = _require(args.campaign, "campaign")
            actor_id = _require(args.actor if args.actor != "runtime" else None, "actor")
            if args.action == "add":
                return add_actor_condition(
                    foundry_documents,
                    campaign_id=campaign_id,
                    actor_id=actor_id,
                    condition=_require(args.condition or args.target, "condition"),
                    duration=_dict(args.duration),
                )
            if args.action == "remove":
                return remove_actor_condition(
                    foundry_documents,
                    campaign_id=campaign_id,
                    actor_id=actor_id,
                    condition=_require(args.condition or args.target, "condition"),
                )
            raise CliError("unknown_command", f"unknown condition command: {args.action}", exit_code=2)

        if args.group == "damage":
            if args.action != "apply":
                raise CliError("unknown_command", f"unknown damage command: {args.action}", exit_code=2)
            return apply_actor_damage(
                foundry_documents,
                campaign_id=_require(args.campaign, "campaign"),
                actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                amount=_int_value(args.amount, "amount"),
                damage_type=args.damage_type or args.type or "",
                source=args.source or args.reason,
            )

        if args.group == "concentration":
            if args.action not in {"pass", "fail"}:
                raise CliError("unknown_command", f"unknown concentration command: {args.action}", exit_code=2)
            return resolve_concentration(
                foundry_documents,
                campaign_id=_require(args.campaign, "campaign"),
                actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                success=args.action == "pass",
            )

        if args.group == "time":
            campaign_id = _require(args.campaign, "campaign")
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            clock = dict(state.get("time") or {})
            if args.action == "status":
                return clock
            if args.action == "advance":
                minutes = int(args.minutes or 0) + (int(args.hours or 0) * 60)
                clock["declared_minutes"] = int(clock.get("declared_minutes", 0)) + minutes
                clock["advances"] = int(clock.get("advances", 0)) + 1
                period_result = None
                if args.period:
                    period_result = advance_effect_durations(
                        foundry_documents,
                        campaign_id=campaign_id,
                        period=args.period,
                        actor_id=None if args.actor == "runtime" else args.actor,
                    )
                if args.reason:
                    clock["last_reason"] = args.reason
                state["time"] = clock
                if state.get("combat") and minutes:
                    combat, _ = recover_period(
                        state["combat"],
                        period="declared_minute",
                        actor_id=None,
                    )
                    state["combat"] = combat
                updated = campaigns.update(campaign_id, state=state)
                _campaign_revision(revisions, before, updated, "time.advance")
                return {"clock": clock, "period": period_result}

        if args.group == "effect":
            campaign_id = _require(args.campaign, "campaign")
            if args.action == "recalculate":
                return recalculate_actor_effects(
                    foundry_documents,
                    campaign_id=campaign_id,
                    actor_id=_require(args.actor if args.actor != "runtime" else None, "actor"),
                )
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            if args.action == "list":
                return {"effects": list((state.get("combat") or {}).get("effects") or [])}
            if args.action == "add":
                combat, result = apply_effect(
                    state.get("combat"),
                    target_id=_require(args.target_id or args.target, "target-id"),
                    effect=_dict(args.payload),
                    source=args.source or args.reason,
                )
            elif args.action == "remove":
                combat, result = remove_effect(
                    state.get("combat"),
                    effect_id=_require(args.id or args.target, "id"),
                )
            else:
                raise CliError("unknown_command", f"unknown effect command: {args.action}", exit_code=2)
            state["combat"] = combat
            updated = campaigns.update(campaign_id, state=state)
            _campaign_revision(revisions, before, updated, f"effect.{args.action}")
            return {"result": result, "combat": combat_status(combat)}

        if args.group == "rest":
            campaign_id = _require(args.campaign, "campaign")
            before = campaigns.get(campaign_id)
            state = dict(before.state)
            period = {"short": "short_rest", "long": "long_rest"}.get(args.action or "")
            if not period:
                raise CliError("unknown_command", f"unknown rest type: {args.action}", exit_code=2)
            if state.get("combat"):
                combat, result = recover_period(
                    state["combat"],
                    period=period,
                    actor_id=None if args.actor == "runtime" else args.actor,
                )
                state["combat"] = combat
            else:
                result = {"type": "period.recover", "period": period, "recovered": []}
            document_recovery = recover_document_rest(
                foundry_documents,
                campaign_id=campaign_id,
                period=period,
                actor_id=None if args.actor == "runtime" else args.actor,
            )
            rest_state = dict(state.get("rests") or {})
            rest_state[period] = int(rest_state.get(period, 0)) + 1
            state["rests"] = rest_state
            updated = campaigns.update(campaign_id, state=state)
            _campaign_revision(revisions, before, updated, f"rest.{args.action}")
            return {
                "result": result,
                "document_recovery": document_recovery,
                "rests": rest_state,
                "combat": combat_status(state.get("combat")),
            }

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
