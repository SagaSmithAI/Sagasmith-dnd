from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def test_torch_light_pays_action_keeps_item_and_expires_after_one_hour(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=tmp_path / "dnd",
            modulegen_skills_dir=tmp_path / "modulegen",
            auto_seed_rules=False,
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Torch lifecycle", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["inventory"]["items"] = [
                {
                    "id": "torch-1",
                    "name": "Torch",
                    "source_key": "dnd5e.content.srd2014.item.torch",
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
                        "name": "Torch bearer",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Target"},
                    "idempotency_key": "target",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {
                        "width_cells": 8,
                        "height_cells": 4,
                        "ambient_illumination": "dark",
                    },
                    "battle_map_override_reason": "The room is dark for torch verification.",
                    "campaign_id": campaign["id"],
                    "participant_ids": [actor["id"], target["id"]],
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20, "position": {"x": 1, "y": 1}},
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 2, "y": 1}},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "action_id": "light-torch",
                "item_id": "torch-1",
                "intent": "light",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "light-torch",
            }
            lit = await _call(server, "adventuring_gear_action", request)
            assert lit["action_cost"] == "action" and lit["action_paid"] is True
            assert lit["light"]["remaining_fuel_ticks"] == 600
            assert lit["light"]["source_key"] == "dnd5e.content.srd2014.item.torch"
            assert lit["fuel"]["quantity_spent"] == 0
            assert await _call(server, "adventuring_gear_action", request) == lit
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            paid_combatant = next(
                entry
                for entry in current["state"]["combat"]["combatants"]
                if entry["actor_id"] == actor["id"]
            )
            assert paid_combatant["turn_budget"]["main_action"] == 0
            current_actor = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
            )
            torch = next(
                item
                for item in current_actor["sheet"]["inventory"]["items"]
                if item["id"] == "torch-1"
            )
            assert torch["quantity"] == 1

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
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "victory", "summary": "Torch light remains timed."},
                    "expected_revision": current["revision"],
                    "idempotency_key": "end-combat",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            advanced = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                        "payload": {"period": "hour", "count": 1, "expected_elapsed_ticks": 600},
                    "expected_revision": current["revision"],
                    "idempotency_key": "advance-hour",
                },
            )
            expired_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            light = next(
                item
                for item in expired_state["state"]["combat"]["adventuring_gear_lights"]
                if item["item_id"] == "torch-1"
            )
            assert light["active"] is False and light["remaining_fuel_ticks"] == 0
            assert advanced["campaign_revision"] == expired_state["revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())
