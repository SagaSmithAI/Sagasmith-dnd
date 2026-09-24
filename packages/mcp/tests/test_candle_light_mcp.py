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


def test_candle_light_advances_time_consumes_item_projects_and_expires(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Candle lifecycle", "edition": "2014", "idempotency_key": "campaign"},
            )
            missing_sheet = default_character_sheet()
            missing_sheet["edition"] = "2014"
            missing_sheet["inventory"]["items"] = [
                {
                    "id": "candle-missing-tinderbox",
                    "name": "Candle",
                    "source_key": "dnd5e.content.srd2014.item.candle",
                    "quantity": 1,
                }
            ]
            missing = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Missing tinderbox",
                        "sheet": missing_sheet,
                    },
                    "idempotency_key": "missing-actor",
                },
            )
            candle_sheet = default_character_sheet()
            candle_sheet["edition"] = "2014"
            candle_sheet["inventory"]["items"] = [
                {
                    "id": "candle-1",
                    "name": "Candle",
                    "source_key": "dnd5e.content.srd2014.item.candle",
                    "quantity": 1,
                },
                {
                    "id": "tinderbox-1",
                    "name": "Tinderbox",
                    "source_key": "dnd5e.content.srd2014.item.tinderbox",
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
                        "name": "Candle bearer",
                        "sheet": candle_sheet,
                    },
                    "idempotency_key": "candle-actor",
                },
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Observer"},
                    "idempotency_key": "target",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            initial_ticks = current["state"]["game_time"]["elapsed_ticks"]
            bad_request = {
                "campaign_id": campaign["id"],
                "action_id": "light-without-tinderbox",
                "item_id": "candle-missing-tinderbox",
                "intent": "light",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": missing["id"],
                "expected_actor_revision": missing["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "light-without-tinderbox",
            }
            with pytest.raises(ToolError, match="source-bound Tinderbox"):
                await _call(server, "adventuring_gear_action", bad_request)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == current["revision"]
            assert unchanged["state"]["game_time"]["elapsed_ticks"] == initial_ticks
            with pytest.raises(ToolError, match="source_ref"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {**bad_request, "source_ref": "forged:source", "idempotency_key": "bad-source"},
                )

            request = {
                "campaign_id": campaign["id"],
                "action_id": "light-candle",
                "item_id": "candle-1",
                "intent": "light",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "light-candle",
            }
            lit = await _call(server, "adventuring_gear_action", request)
            assert lit["game_time"]["elapsed_ticks"] == initial_ticks + 10
            assert lit["candle_lighting"]["remaining_fuel_ticks"] == 600
            assert lit["candle_lighting"]["inventory_quantity_spent"] == 1
            assert await _call(server, "adventuring_gear_action", request) == lit
            owner_after = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
            )
            candle = next(
                item
                for item in owner_after["sheet"]["inventory"]["items"]
                if item["id"] == "candle-1"
            )
            assert candle["quantity"] == 0
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            effect = next(
                item
                for item in current["state"]["world_effects"]
                if item["id"] == lit["candle_lighting"]["effect_id"]
            )
            assert effect["active"] is True
            assert effect["source_actor_id"] == actor["id"]
            assert effect["metadata"]["item_id"] == "candle-1"
            assert (
                next(
                    item for item in current["state"]["item_spends"] if item["id"] == "light-candle"
                )["quantity"]
                == 1
            )

            close_server(server)
            server = create_server(config)
            assert await _call(server, "adventuring_gear_action", request) == lit
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "participant_ids": [actor["id"], target["id"]],
                    "positioning_mode": "grid",
                    "battle_map": {
                        "width_cells": 8,
                        "height_cells": 4,
                        "ambient_illumination": "dark",
                    },
                    "battle_map_override_reason": "Use a dark map for carried candle lighting.",
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20, "position": {"x": 1, "y": 1}},
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 2, "y": 1}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-candle-combat",
                },
            )
            combat_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            light = next(
                item
                for item in combat_state["state"]["combat"]["adventuring_gear_lights"]
                if item["world_effect_id"] == effect["id"]
            )
            assert light["actor_id"] == actor["id"]
            assert light["item_id"] == "candle-1"
            assert light["remaining_fuel_ticks"] == 600

            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "victory", "summary": "Candle expires after combat."},
                    "expected_revision": combat_state["revision"],
                    "idempotency_key": "end-candle-combat",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            expired = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "hour",
                        "count": 1,
                        "expected_elapsed_ticks": initial_ticks + 610,
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "advance-candle-hour",
                },
            )
            assert effect["id"] in expired["world_expired"]
            assert f"candle-world:{effect['id']}" in expired["world_expired"]
            end_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            exhausted = next(
                item for item in end_state["state"]["world_effects"] if item["id"] == effect["id"]
            )
            assert exhausted["active"] is False and exhausted["ended_reason"] == "burned_out"
            projected = next(
                item
                for item in end_state["state"]["combat"]["adventuring_gear_lights"]
                if item["id"] == f"candle-world:{effect['id']}"
            )
            assert projected["active"] is False and projected["remaining_fuel_ticks"] == 0
            owner_final = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
            )
            assert (
                next(
                    item
                    for item in owner_final["sheet"]["inventory"]["items"]
                    if item["id"] == "candle-1"
                )["quantity"]
                == 0
            )
            with pytest.raises(ToolError, match="Candle is required"):
                await _call(
                    server,
                    "adventuring_gear_action",
                    {
                        **request,
                        "action_id": "relight-candle",
                        "idempotency_key": "relight-candle",
                        "expected_revision": end_state["revision"],
                        "expected_actor_revision": owner_final["revision"],
                    },
                )
        finally:
            close_server(server)

    asyncio.run(exercise())
