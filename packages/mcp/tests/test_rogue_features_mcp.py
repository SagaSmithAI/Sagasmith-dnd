import asyncio

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_structured_spell_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools


def _rogue_sheet():
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["level"] = 20
    sheet["progression"]["classes"] = [{"name": "Rogue", "level": 20, "hit_die": 8}]
    sheet["content"]["features"] = [{
        "id": "dnd5e.content.srd2014.feature.rogue-stroke-of-luck",
        "name": "Stroke of Luck",
        "source_key": "Rogue",
        "mechanic_refs": ["dnd5e.core.rogue.stroke_of_luck"],
        "uses": {"max": 1, "value": 1, "recovers_on": "short_rest", "unlimited": False},
    }]
    return sheet


async def _prepare(server):
    campaign = await _call(server, "campaign_create", {
        "name": "Stroke of Luck", "edition": "2014", "random_seed": "stroke-1",
        "idempotency_key": "campaign",
    })
    character = await _call(server, "character_create_from", {
        "mode": "direct",
        "payload": {
            "campaign_id": campaign["id"],
            "name": "Level 20 Rogue",
            "sheet": _rogue_sheet(),
        },
        "idempotency_key": "rogue",
    })
    return campaign["id"], character["id"]


async def _campaign_revision(server, campaign_id):
    value = await _call(server, "campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })
    return value["revision"]


async def _character(server, character_id):
    return await _call(server, "character_query", {
        "view": "get", "payload": {"character_id": character_id},
    })


def test_stroke_of_luck_check_choice_survives_restart_and_replays_without_reroll(tmp_path):
    async def run():
        config = _config(tmp_path)
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign_id, actor_id = await _prepare(server)
                check = {
                    "campaign_id": campaign_id,
                    "actor_id": actor_id,
                    "kind": "check",
                    "ability": "athletics",
                    "dc": 30,
                    "expected_revision": await _campaign_revision(server, campaign_id),
                    "idempotency_key": "failed-athletics",
                }
                check_content, check_response = await server.call_tool("combat_check", check)
                offered = check_response["result"]
                assert offered["success"] is False
                assert check_response["random_stream_receipt"]["draw_count"] == 1
                assert await server.call_tool("combat_check", check) == (
                    check_content,
                    check_response,
                )
                choice = offered["post_roll_choice"]
                assert {item["id"] for item in choice["candidates"]} == {
                    "use_stroke_of_luck", "decline",
                }
                original_natural = offered["natural"]
                before_choice = await _character(server, actor_id)
                assert before_choice["sheet"]["content"]["features"][0]["uses"]["value"] == 1
                current_revision = await _campaign_revision(server, campaign_id)
                stale_resolution = {
                    "campaign_id": campaign_id,
                    "action": "resolve",
                    "actor_id": actor_id,
                    "payload": {
                        "choice_id": choice["id"],
                        "selection": {"id": "use_stroke_of_luck"},
                    },
                    "expected_revision": current_revision - 1,
                    "idempotency_key": "stale-use-stroke",
                }
                with pytest.raises(ToolError, match="campaign revision conflict"):
                    await _call(server, "combat_choice", stale_resolution)
                assert await _campaign_revision(server, campaign_id) == current_revision
            close_server(runtime)
            runtime = create_server(config)
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert await server.call_tool("combat_check", check) == (
                    check_content,
                    check_response,
                )
                resolution = {
                    "campaign_id": campaign_id,
                    "action": "resolve",
                    "actor_id": actor_id,
                    "payload": {
                        "choice_id": choice["id"],
                        "selection": {"id": "use_stroke_of_luck"},
                    },
                    "expected_revision": await _campaign_revision(server, campaign_id),
                    "idempotency_key": "use-stroke",
                }
                settled = await _call(server, "combat_choice", resolution)
                assert settled["result"]["stroke_of_luck_applied"] is True
                assert settled["result"]["natural"] == 20
                assert settled["result"]["stroke_of_luck_original_natural"] == original_natural
                after = await _character(server, actor_id)
                assert after["sheet"]["content"]["features"][0]["uses"]["value"] == 0
                assert await _call(server, "combat_choice", resolution) == settled
                campaign = await _call(server, "campaign_query", {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                })
                assert campaign["state"]["random_stream"]["position"] == (
                    check_response["random_stream_receipt"]["position_after"]
                )
                assert await _call(server, "combat_check", check) == offered
                assert (await _character(server, actor_id))["revision"] == after["revision"]
        finally:
            close_server(runtime)
    asyncio.run(run())


def test_stroke_of_luck_missed_attack_resolves_original_roll_as_hit(tmp_path):
    async def run():
        config = _config(tmp_path)
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign_id, actor_id = await _prepare(server)
                target_sheet = default_character_sheet()
                target_sheet["combat"]["ac"]["override"] = 40
                target = await _call(server, "character_create_from", {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "High AC target",
                        "sheet": target_sheet,
                    },
                    "idempotency_key": "target",
                })
                started = await _call(server, "combat_start", {
                    "campaign_id": campaign_id,
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 8},
                    "participant_ids": [actor_id, target["id"]],
                    "participant_config": [
                        {"actor_id": actor_id, "initiative": 20, "position": {"x": 0, "y": 0}},
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 1, "y": 0}},
                    ],
                    "expected_revision": await _campaign_revision(server, campaign_id),
                    "idempotency_key": "start-attack",
                })
                attack = {
                    "campaign_id": campaign_id,
                    "actor_id": actor_id,
                    "target_id": target["id"],
                    "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "missed-attack",
                }
                attack_content, attack_response = await server.call_tool(
                    "combat_resolve_attack", attack
                )
                offered = attack_response["result"]
                assert attack_response["random_stream_receipt"]["draw_count"] >= 1
                assert offered["hit"] is False
                original_natural = offered["natural"]
                choice = offered["post_roll_choice"]
                before = await _character(server, target["id"])

            close_server(runtime)
            runtime = create_server(config)
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert await server.call_tool("combat_resolve_attack", attack) == (
                    attack_content,
                    attack_response,
                )
                resolution = {
                    "campaign_id": campaign_id,
                    "action": "resolve",
                    "actor_id": actor_id,
                    "payload": {
                        "choice_id": choice["id"],
                        "selection": {"id": "use_stroke_of_luck"},
                    },
                    "expected_revision": await _campaign_revision(server, campaign_id),
                    "idempotency_key": "use-stroke-attack",
                }
                settled = await _call(server, "combat_choice", resolution)
                assert settled["result"]["hit"] is True
                assert settled["result"]["natural"] == original_natural
                assert settled["result"]["stroke_of_luck_applied"] is True
                after = await _character(server, target["id"])
                assert (
                    after["sheet"]["combat"]["hp"]["value"]
                    < before["sheet"]["combat"]["hp"]["value"]
                )
                actor = await _character(server, actor_id)
                assert actor["sheet"]["content"]["features"][0]["uses"]["value"] == 0
                assert await _call(server, "combat_choice", resolution) == settled
                assert await server.call_tool("combat_resolve_attack", attack) == (
                    attack_content,
                    attack_response,
                )
        finally:
            close_server(runtime)
    asyncio.run(run())
