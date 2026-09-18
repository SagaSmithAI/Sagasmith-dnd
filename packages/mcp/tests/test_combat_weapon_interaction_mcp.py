import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet
from test_ground_items_mcp import _raw, _snapshot, _weapon
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("mode", ["agent", "grid"])
def test_draw_stow_atomic_payment_and_replay(tmp_path: Path, mode: str):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Weapon interaction", "edition": "2014", "idempotency_key": "campaign"})
            sheet, weapon_id = add_inventory_item(default_character_sheet(), _weapon())
            second = _weapon() | {"id": "second-sword"}
            sheet, second_id = add_inventory_item(sheet, second)
            actors = []
            for index in range(2):
                actors.append(await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {"campaign_id": campaign["id"],
                    "name": f"Actor {index}", "sheet": sheet}, "idempotency_key": f"actor{index}"}))
            current, _ = await _snapshot(server, campaign["id"], [])
            phase = await _call(server, "game_phase", {"campaign_id": campaign["id"],
                "action": "set", "tool_profile": "play", "expected_revision": current["revision"],
                "idempotency_key": "phase"})
            config = [{"actor_id": a["id"], "initiative": 20 - i,
                       "disposition": "friendly" if i == 0 else "hostile",
                       **({"position": {"x": i, "y": 0}} if mode == "grid" else {})}
                      for i, a in enumerate(actors)]
            await _call(server, "combat_start", {"campaign_id": campaign["id"],
                "positioning_mode": mode, "participant_ids": [a["id"] for a in actors],
                "participant_config": config,
                **({"battle_map": {"width_cells": 10, "height_cells": 10}}
                   if mode == "grid" else {}),
                "expected_revision": phase["campaign_revision"], "idempotency_key": "start"})

            async def args(action, payload, key):
                current, _ = await _snapshot(server, campaign["id"], [])
                return {"campaign_id": campaign["id"], "actor_id": actors[0]["id"],
                        "action": action, "payload": payload, "idempotency_key": key,
                        "expected_revision": current["revision"]}

            draw_args = await args(
                "draw_weapon", {"item_id": weapon_id, "slot": "main_hand"}, "draw")
            drawn = await _raw(server, "combat_common_action", draw_args)
            assert await _raw(server, "combat_common_action", draw_args) == drawn
            before = await _snapshot(server, campaign["id"], [actors[0]["id"]])
            assert before[1][0]["sheet"]["inventory"]["equipment_slots"]["main_hand"] == weapon_id
            with pytest.raises(ToolError):
                await _call(server, "combat_common_action", await args(
                    "draw_weapon", {"item_id": second_id, "slot": "main_hand"}, "occupied"))
            assert await _snapshot(server, campaign["id"], [actors[0]["id"]]) == before
            await _call(server, "combat_common_action", await args(
                "stow_weapon", {"item_id": weapon_id}, "stow"))
            after = await _snapshot(server, campaign["id"], [actors[0]["id"]])
            assert after[1][0]["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
            budget = after[0]["state"]["combat"]["combatants"][0]["turn_budget"]
            assert budget["object_interaction"] == 0
            assert budget["main_action"] == 0
            full = await _call(server, "combat_query", {
                "campaign_id": campaign["id"], "view": "status"})
            summary = await _call(server, "combat_query", {
                "campaign_id": campaign["id"], "view": "status",
                "payload": {"detail": "summary"}})
            assert full["log"]
            assert summary == {key: value for key, value in full.items() if key != "log"}
            assert await _snapshot(server, campaign["id"], [actors[0]["id"]]) == after
            with pytest.raises(ToolError):
                await _call(server, "combat_common_action", await args(
                    "draw_weapon", {"item_id": weapon_id, "slot": "main_hand"}, "exhausted"))
            assert await _snapshot(server, campaign["id"], [actors[0]["id"]]) == after
        finally:
            close_server(server)
    asyncio.run(exercise())
