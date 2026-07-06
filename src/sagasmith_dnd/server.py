"""HTTP API server for sagasmith-dnd — bridges frontend to SQLite + ChromaDB.

Usage:
    pip install "sagasmith-dnd[serve]"
    sagasmith-dnd serve --port 3000
"""

from __future__ import annotations

from typing import Any

from sagasmith_core import (
    CampaignService,
    CharacterService,
    EventService,
    MemoryService,
    ModuleService,
    RuleService,
    SnapshotService,
    VectorStore,
)

from sagasmith_dnd.engine import resolve_check, roll as roll_dice
from sagasmith_dnd.runtime import database as _database, dense_components
from sagasmith_dnd.system import DND5E, validate_character_sheet

_db = _database()
_embedder, _vector_store = dense_components()


def _services():
    return {
        "campaigns": CampaignService(_db),
        "characters": CharacterService(_db),
        "events": EventService(_db),
        "memories": MemoryService(_db),
        "modules": ModuleService(_db),
        "rules": RuleService(_db),
        "snapshots": SnapshotService(_db),
    }


def _app():
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError(
            "HTTP server requires `pip install 'sagasmith-dnd[serve]'`"
        ) from exc

    app = FastAPI(title="sagasmith-dnd API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    svc = _services()

    # ── Health ────────────────────────────────────────────────────
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.2.0", "dense": _vector_store.enabled if _vector_store else False}

    # ── Campaigns ─────────────────────────────────────────────────
    @app.get("/api/campaigns")
    def list_campaigns():
        return svc["campaigns"].list()

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str):
        try:
            return svc["campaigns"].get(campaign_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/campaigns")
    def create_campaign(name: str, system_id: str = "dnd5e", slug: str = ""):
        return svc["campaigns"].create(system_id=system_id, name=name, slug=slug or None)

    # ── Characters ────────────────────────────────────────────────
    @app.get("/api/campaigns/{campaign_id}/characters")
    def list_characters(campaign_id: str):
        return svc["characters"].list(campaign_id)

    @app.get("/api/characters/{character_id}")
    def get_character(character_id: str):
        try:
            return svc["characters"].get(character_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ── Modules ───────────────────────────────────────────────────
    @app.get("/api/campaigns/{campaign_id}/modules")
    def list_modules(campaign_id: str):
        return svc["modules"].list(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/scenes")
    def scene_index(campaign_id: str):
        return svc["modules"].scene_index(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/current-scene")
    def current_scene(campaign_id: str, scope: str = "party"):
        return svc["modules"].current_scene(campaign_id, scope_id=scope)

    @app.get("/api/campaigns/{campaign_id}/search")
    def search_modules(campaign_id: str, query: str = Query(...), limit: int = 8):
        return svc["modules"].search(
            campaign_id=campaign_id,
            query=query,
            top_k=limit,
            embedder=_embedder,
            vector_store=_vector_store,
        )

    # ── Rules ─────────────────────────────────────────────────────
    @app.get("/api/rules")
    def list_rules(system_id: str = "dnd5e"):
        return svc["rules"].sources(system_id=system_id)

    @app.get("/api/rules/search")
    def search_rules(
        system_id: str = "dnd5e",
        query: str = Query(...),
        edition: str | None = None,
        locale: str | None = None,
        limit: int = 8,
    ):
        return svc["rules"].search(
            system_id=system_id,
            query=query,
            edition=edition,
            locale=locale,
            top_k=limit,
            embedder=_embedder,
            vector_store=_vector_store,
        )

    # ── Events ────────────────────────────────────────────────────
    @app.get("/api/campaigns/{campaign_id}/events")
    def list_events(campaign_id: str, limit: int = 50):
        return svc["events"].list(campaign_id)[:limit]

    # ── Memories ──────────────────────────────────────────────────
    @app.get("/api/campaigns/{campaign_id}/memories")
    def list_memories(campaign_id: str):
        return svc["memories"].list(campaign_id)

    # ── Snapshots ─────────────────────────────────────────────────
    @app.get("/api/campaigns/{campaign_id}/saves")
    def list_saves(campaign_id: str):
        return svc["snapshots"].list(campaign_id)

    @app.get("/api/campaigns/{campaign_id}/lineage")
    def save_lineage(campaign_id: str):
        return svc["snapshots"].lineage(campaign_id)

    # ── Dice ──────────────────────────────────────────────────────
    class RollRequest(BaseModel):
        expression: str
        advantage: bool = False
        disadvantage: bool = False

    @app.post("/api/roll")
    def roll(req: RollRequest):
        return roll_dice(req.expression)

    @app.post("/api/check")
    def check(req: RollRequest):
        return resolve_check(req.expression, req.advantage, req.disadvantage)

    return app


def serve(host: str = "127.0.0.1", port: int = 3000, **kwargs) -> None:
    """Start the HTTP API server."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "HTTP server requires `pip install 'sagasmith-dnd[serve]'`"
        ) from exc
    uvicorn.run(_app(), host=host, port=port, **kwargs)
