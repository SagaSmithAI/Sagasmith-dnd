import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


def test_play_sheet_replace_cannot_bypass_attunement_rest_gate(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Attunement guard", "edition": "2014", "idempotency_key": "campaign"},
            )
            created = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Carrier",
                        "sheet": {
                            "inventory": {
                                "items": [
                                    {
                                        "id": "ring",
                                        "name": "Ring",
                                        "kind": "equipment",
                                        "attunement": "required",
                                    }
                                ]
                            }
                        },
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "create",
                },
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "play",
                },
            )
            before = deepcopy(created["sheet"])
            before_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": created["id"]}},
            )
            item_patch = {
                "item_id": "ring",
                "patch": {"attunement": "attuned"},
            }
            with pytest.raises(ToolError, match="short rest"):
                await _call(
                    server,
                    "inventory_change",
                    {
                        "owner": "character",
                        "action": "update",
                        "owner_id": created["id"],
                        "payload": item_patch,
                        "expected_revision": created["revision"],
                        "idempotency_key": "inventory-block",
                    },
                )

            forged = deepcopy(before)
            forged["inventory"]["items"][0]["attunement"] = "attuned"
            try:
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": created["id"],
                        "sheet": forged,
                        "expected_revision": created["revision"],
                        "idempotency_key": "sheet-bypass",
                    },
                )
            except ToolError as error:
                assert "short rest" in str(error)
            else:
                replaced = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": created["id"]}},
                )
                pytest.fail(
                    "sheet replacement bypassed rest gate: "
                    f"character_revision={replaced.get('revision')}, "
                    f"attunement={replaced['sheet']['inventory']['items'][0]['attunement']}"
                )
            current = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": created["id"]}},
            )
            assert current["sheet"] == before
            assert current["revision"] == created["revision"]
            assert current == before_actor
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                == before_campaign
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
