"""Source-bound Chain burst checks against authoritative Strength and campaign state."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(path: Path) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
    )


async def _snapshot(server, campaign_id: str, actor_id: str) -> tuple[dict, dict]:
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    actor = await _call(
        server,
        "character_query",
        {"view": "get", "payload": {"character_id": actor_id}},
    )
    return campaign, actor


def test_chain_burst_uses_srd_strength_check_and_commits_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Chain burst",
                    "edition": "2014",
                    "random_seed": "chain-burst",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 30
            sheet["inventory"]["items"] = [
                {
                    "id": "chain-ready",
                    "name": "Chain (10 feet)",
                    "source_key": "dnd5e.content.srd2014.item.chain-10-feet",
                    "quantity": 1,
                },
                {
                    "id": "chain-forged-context",
                    "name": "Chain (10 feet)",
                    "source_key": "dnd5e.content.srd2014.item.chain-10-feet",
                    "quantity": 1,
                },
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Strong actor",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            before, current_actor = await _snapshot(server, campaign["id"], actor["id"])
            request = {
                "campaign_id": campaign["id"],
                "action_id": "chain-burst-success",
                "item_id": "chain-ready",
                "intent": "burst",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": current_actor["revision"],
                "expected_target_revision": current_actor["revision"],
                "expected_revision": before["revision"],
                "idempotency_key": "chain-burst-success",
            }

            forged = {
                **request,
                "action_id": "chain-forged-outcome",
                "item_id": "chain-forged-context",
                "idempotency_key": "chain-forged-outcome",
                "action_context": {"dc": 1, "success": True, "hit_points": 0},
            }
            with pytest.raises(ToolError, match="do not accept caller rule or outcome context"):
                await _call(server, "adventuring_gear_action", forged)
            assert await _snapshot(server, campaign["id"], actor["id"]) == (before, current_actor)

            result = await _call(server, "adventuring_gear_action", request)
            assert result["success"] is True
            assert result["check"]["dc"] == 20
            assert result["rule_plan"]["check"]["ability"] == "strength"
            assert result["rule_plan"]["object_hit_points"] == 10
            assert result["rule_plan"]["action_economy"] is None
            assert result["resulting_state"]["state"] == "broken"
            assert result["resulting_state"]["object_hit_points"] == 0
            assert result["resulting_state"]["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            after, after_actor = await _snapshot(server, campaign["id"], actor["id"])
            assert after["revision"] == before["revision"] + 1
            assert after_actor["revision"] == current_actor["revision"] + 1
            assert await _call(server, "adventuring_gear_action", request) == result
            assert await _snapshot(server, campaign["id"], actor["id"]) == (after, after_actor)

            stale = {
                **request,
                "action_id": "chain-burst-stale",
                "item_id": "chain-forged-context",
                "idempotency_key": "chain-burst-stale",
            }
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(server, "adventuring_gear_action", stale)
            assert await _snapshot(server, campaign["id"], actor["id"]) == (after, after_actor)
        finally:
            close_server(server)

    asyncio.run(exercise())
