from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


def test_external_inventory_refs_are_engine_owned(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path / "seed"),
            auto_seed_rules=False,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "External refs", "edition": "2014", "idempotency_key": "campaign"},
            )
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Ref target",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "actor",
                },
            )
            before_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            before_list = await _call(
                server,
                "character_query",
                {
                    "view": "list",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            forged = deepcopy(before_actor["sheet"])
            forged["inventory"]["external_items"] = [
                {
                    "id": "ghost",
                    "name": "Ghost",
                    "attunement": "attuned",
                    "location": {"kind": "ground", "ground_id": "fake", "item_id": "ghost"},
                }
            ]
            with pytest.raises(ToolError, match="external_items is engine-owned"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": actor["id"],
                        "sheet": forged,
                        "expected_revision": actor["revision"],
                        "idempotency_key": "forge-replace",
                    },
                )
            with pytest.raises(ToolError, match="external_items is engine-owned"):
                await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": "Forged ref",
                            "sheet": forged,
                        },
                        "idempotency_key": "forge-create",
                    },
                )
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                == before_campaign
            )
            assert (
                await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                == before_actor
            )
            assert (
                await _call(
                    server,
                    "character_query",
                    {"view": "list", "payload": {"campaign_id": campaign["id"]}},
                )
                == before_list
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
