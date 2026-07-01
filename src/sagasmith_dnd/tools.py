"""Nanobot tools backed by sagasmith-core services."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from sagasmith_core import CampaignService, CharacterService, ModuleService, RuleService

from sagasmith_dnd.runtime import database, dense_components
from sagasmith_dnd.system import DND5E, validate_character_sheet


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "get", "update", "delete"]},
            "campaign_id": {"type": "string"},
            "name": {"type": "string"},
            "status": {"type": "string"},
            "description": {"type": "string"},
            "settings": {"type": "object"},
            "state": {"type": "object"},
        },
        "required": ["action"],
    }
)
class DndCampaignTool(Tool):
    name = "dnd_campaign"
    description = "Create, list, inspect, update, and delete D&D campaigns."

    def __init__(self) -> None:
        self.database = database()
        self.service = CampaignService(self.database)

    async def execute(self, action: str, **kwargs: Any) -> Any:
        if action == "create":
            return asdict(
                self.service.create(
                    system_id=DND5E.id,
                    name=kwargs["name"],
                    description=kwargs.get("description", ""),
                    settings={**DND5E.campaign_defaults, **kwargs.get("settings", {})},
                    state=kwargs.get("state"),
                )
            )
        if action == "list":
            return {
                "campaigns": [
                    asdict(item)
                    for item in self.service.list(
                        system_id=DND5E.id,
                        status=kwargs.get("status"),
                    )
                ]
            }
        if action == "get":
            return asdict(self.service.get(kwargs["campaign_id"]))
        if action == "update":
            return asdict(
                self.service.update(
                    kwargs["campaign_id"],
                    name=kwargs.get("name"),
                    status=kwargs.get("status"),
                    description=kwargs.get("description"),
                    settings=kwargs.get("settings"),
                    state=kwargs.get("state"),
                )
            )
        if action == "delete":
            self.service.delete(kwargs["campaign_id"])
            return {"deleted": kwargs["campaign_id"]}
        raise ValueError(f"unknown action {action!r}")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "get", "update", "bind"]},
            "character_id": {"type": "string"},
            "campaign_id": {"type": ["string", "null"]},
            "character_type": {"type": "string", "enum": ["pc", "npc", "monster"]},
            "name": {"type": "string"},
            "player_name": {"type": "string"},
            "summary": {"type": "string"},
            "sheet": {"type": "object"},
            "notes": {"type": "object"},
        },
        "required": ["action"],
    }
)
class DndCharacterTool(Tool):
    name = "dnd_character"
    description = "Manage D&D player characters, NPCs, monsters, and campaign bindings."

    def __init__(self) -> None:
        self.database = database()
        self.service = CharacterService(self.database)

    async def execute(self, action: str, **kwargs: Any) -> Any:
        if action == "create":
            return asdict(
                self.service.create(
                    system_id=DND5E.id,
                    name=kwargs["name"],
                    character_type=kwargs.get("character_type", "pc"),
                    campaign_id=kwargs.get("campaign_id"),
                    player_name=kwargs.get("player_name"),
                    summary=kwargs.get("summary", ""),
                    sheet=validate_character_sheet(kwargs.get("sheet", {})),
                    notes=kwargs.get("notes"),
                )
            )
        if action == "list":
            return {
                "characters": [
                    asdict(item)
                    for item in self.service.list(
                        system_id=DND5E.id,
                        campaign_id=kwargs.get("campaign_id"),
                        character_type=kwargs.get("character_type"),
                    )
                ]
            }
        if action == "get":
            return asdict(self.service.get(kwargs["character_id"]))
        if action == "update":
            sheet = kwargs.get("sheet")
            return asdict(
                self.service.update(
                    kwargs["character_id"],
                    name=kwargs.get("name"),
                    player_name=kwargs.get("player_name"),
                    summary=kwargs.get("summary"),
                    sheet=validate_character_sheet(sheet) if sheet is not None else None,
                    notes=kwargs.get("notes"),
                )
            )
        if action == "bind":
            return asdict(
                self.service.bind(
                    kwargs["character_id"],
                    kwargs.get("campaign_id"),
                )
            )
        raise ValueError(f"unknown action {action!r}")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["ingest", "search", "expand"]},
            "source_key": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "path": {"type": "string"},
            "query": {"type": "string"},
            "chunk_id": {"type": "string"},
            "locale": {"type": "string"},
            "version": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["action"],
    }
)
class DndRulesTool(Tool):
    name = "dnd_rules"
    description = "Ingest, search, and expand D&D rule documents."

    def __init__(self) -> None:
        self.database = database()
        self.service = RuleService(self.database)
        self.embedder, self.vectors = dense_components()

    async def execute(self, action: str, **kwargs: Any) -> Any:
        if action == "ingest":
            content = kwargs.get("content")
            path = kwargs.get("path")
            if content is None and path:
                content = Path(path).expanduser().read_text(encoding="utf-8")
            if content is None:
                raise ValueError("content or path is required")
            source_key = kwargs.get("source_key") or (
                Path(path).name if path else "inline-rules"
            )
            return asdict(
                self.service.ingest(
                    system_id=DND5E.id,
                    source_key=source_key,
                    title=kwargs.get("title")
                    or (Path(path).stem if path else source_key),
                    content=content,
                    locale=kwargs.get("locale", "en"),
                    version=kwargs.get("version", ""),
                    embedder=self.embedder,
                    vector_store=self.vectors,
                )
            )
        if action == "search":
            return {
                "hits": [
                    asdict(item)
                    for item in self.service.search(
                        system_id=DND5E.id,
                        query=kwargs["query"],
                        top_k=kwargs.get("top_k", 8),
                        embedder=self.embedder,
                        vector_store=self.vectors,
                    )
                ]
            }
        if action == "expand":
            return self.service.expand(kwargs["chunk_id"])
        raise ValueError(f"unknown action {action!r}")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["ingest", "search", "set_scene"]},
            "campaign_id": {"type": "string"},
            "source_key": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "path": {"type": "string"},
            "query": {"type": "string"},
            "scene_id": {"type": "string"},
            "progress": {"type": "integer", "minimum": 0, "maximum": 100},
            "status": {"type": "string"},
            "state": {"type": "object"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["action", "campaign_id"],
    }
)
class DndModuleTool(Tool):
    name = "dnd_module"
    description = "Ingest and search D&D adventures and track scene progress."

    def __init__(self) -> None:
        self.database = database()
        self.service = ModuleService(self.database)
        self.embedder, self.vectors = dense_components()

    async def execute(self, action: str, campaign_id: str, **kwargs: Any) -> Any:
        if action == "ingest":
            if path := kwargs.get("path"):
                return asdict(
                    self.service.ingest_path(
                        campaign_id=campaign_id,
                        path=path,
                        source_key=kwargs.get("source_key"),
                        title=kwargs.get("title"),
                        embedder=self.embedder,
                        vector_store=self.vectors,
                    )
                )
            return asdict(
                self.service.ingest(
                    campaign_id=campaign_id,
                    source_key=kwargs["source_key"],
                    title=kwargs.get("title", kwargs["source_key"]),
                    content=kwargs["content"],
                    embedder=self.embedder,
                    vector_store=self.vectors,
                )
            )
        if action == "search":
            return {
                "hits": [
                    asdict(item)
                    for item in self.service.search(
                        campaign_id=campaign_id,
                        query=kwargs["query"],
                        top_k=kwargs.get("top_k", 8),
                        embedder=self.embedder,
                        vector_store=self.vectors,
                    )
                ]
            }
        if action == "set_scene":
            return self.service.set_scene_progress(
                campaign_id=campaign_id,
                scene_id=kwargs["scene_id"],
                status=kwargs.get("status", "current"),
                progress=kwargs.get("progress", 0),
                state=kwargs.get("state"),
            )
        raise ValueError(f"unknown action {action!r}")
