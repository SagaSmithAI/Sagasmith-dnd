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


def _gear_item(name: str, source_key: str, item_id: str) -> dict:
    return {"id": item_id, "name": name, "source_key": source_key, "quantity": 1}


def test_rope_burst_action_persists_source_state_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Rope burst",
                    "edition": "2014",
                    "random_seed": "gear-rope-burst",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["strength"]["score"] = 30
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Rope, hempen (50 feet)",
                    "dnd5e.content.srd2014.item.rope-hempen-50-feet",
                    "rope-1",
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Rope user", "sheet": sheet},
                    "idempotency_key": "actor",
                },
            )
            current = await _call(
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
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "action_id": "burst-rope",
                "item_id": "rope-1",
                "intent": "burst",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "burst-rope",
            }
            result = await _call(server, "adventuring_gear_action", request)
            assert result["rule_plan"]["check"] == {"ability": "strength", "dc": 17}
            assert result["rule_plan"]["object_hit_points"] == 2
            assert result["check"]["rolls"]
            assert result["success"] is True
            assert result["resulting_state"]["state"] == "broken"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            object_key = f"{actor['id']}:rope-1"
            assert current["state"]["adventuring_gear_objects"][object_key]["state"] == "broken"
            assert current["revision"] == request["expected_revision"] + 1
            assert current["state"]["random_stream"]["position"] > 0
            assert any(spend.get("id") == "burst-rope" for spend in current["state"]["item_spends"])
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "adventuring_gear_action", request) == result
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_lamps_lanterns_and_oil_fail_closed_without_dynamic_light_lifecycle(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Unsupported lights",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            gear = [
                ("lamp", "Lamp", "dnd5e.content.srd2014.item.lamp", "light"),
                (
                    "bullseye",
                    "Lantern, bullseye",
                    "dnd5e.content.srd2014.item.lantern-bullseye",
                    "light",
                ),
                (
                    "hooded",
                    "Lantern, hooded",
                    "dnd5e.content.srd2014.item.lantern-hooded",
                    "light",
                ),
                (
                    "oil",
                    "Oil (flask)",
                    "dnd5e.content.srd2014.item.oil-flask",
                    "pour_ground",
                ),
            ]
            sheet["inventory"]["items"] = [
                _gear_item(name, source_key, item_id) for item_id, name, source_key, _intent in gear
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Lamp bearer",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            current = await _call(
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
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            for item_id, _name, _source_key, intent in gear:
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                request = {
                    "campaign_id": campaign["id"],
                    "action_id": f"use-{item_id}",
                    "item_id": item_id,
                    "intent": intent,
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": actor["id"],
                    "target_actor_id": actor["id"],
                    "expected_actor_revision": actor["revision"],
                    "expected_target_revision": actor["revision"],
                    "expected_revision": current["revision"],
                    "idempotency_key": f"use-{item_id}",
                }
                with pytest.raises(ToolError, match="no atomic settlement"):
                    await _call(server, "adventuring_gear_action", request)
            unchanged_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged_actor["revision"] == actor["revision"]
            assert all(
                item["quantity"] == 1 for item in unchanged_actor["sheet"]["inventory"]["items"]
            )
            assert unchanged_campaign["state"].get("item_spends", []) == []
            assert "adventuring_gear_objects" not in unchanged_campaign["state"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_manacles_require_owned_binding_state_and_reject_forged_receipts(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Manacles boundaries",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            source_sheet = default_character_sheet()
            source_sheet["edition"] = "2014"
            source_sheet["inventory"]["items"] = [
                _gear_item(
                    "Manacles",
                    "dnd5e.content.srd2014.item.manacles",
                    "manacles-1",
                )
            ]
            source = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Manacles holder",
                        "sheet": source_sheet,
                    },
                    "idempotency_key": "source",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["edition"] = "2014"
            target_sheet["conditions"] = ["restrained"]
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Restrained target",
                        "sheet": target_sheet,
                    },
                    "idempotency_key": "target",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 3, "height_cells": 1},
                    "participant_ids": [source["id"], target["id"]],
                    "participant_config": [
                        {
                            "actor_id": source["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": target["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                        },
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "combat-start",
                },
            )
            source = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": source["id"]}},
            )
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            actions = ["escape", "break", "pick"]
            for intent in actions:
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                request = {
                    "campaign_id": campaign["id"],
                    "action_id": f"manacles-{intent}",
                    "item_id": "manacles-1",
                    "intent": intent,
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": source["id"],
                    "target_actor_id": target["id"],
                    "expected_actor_revision": source["revision"],
                    "expected_target_revision": target["revision"],
                    "expected_revision": current["revision"],
                    "idempotency_key": f"manacles-{intent}",
                }
                if intent == "escape":
                    with pytest.raises(ToolError, match="caller-supplied binding"):
                        await _call(
                            server,
                            "adventuring_gear_action",
                            {
                                **request,
                                "action_context": {
                                    "binding_receipt": {"source_ref": ADVENTURING_GEAR_SOURCE_REF},
                                    "dc": 1,
                                    "success": True,
                                },
                            },
                        )
                with pytest.raises(ToolError, match="source-owned binding receipt"):
                    await _call(server, "adventuring_gear_action", request)
            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            source_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": source["id"]}},
            )
            target_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": target["id"]}},
            )
            assert after["revision"] == started["campaign_revision"]
            assert after["state"].get("item_spends", []) == []
            assert source_after["revision"] == source["revision"]
            assert target_after["revision"] == target["revision"]
            assert "restrained" in target_after["sheet"]["conditions"]
            close_server(server)
            server = create_server(_config(tmp_path))
            with pytest.raises(ToolError, match="source-owned binding receipt"):
                await _call(server, "adventuring_gear_action", request)
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_lock_pick_uses_sheet_tool_proficiency_and_persists_open_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Lock pick", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["dexterity"]["score"] = 30
            sheet["traits"]["proficiencies"]["tools"] = ["Thieves' Tools"]
            sheet["inventory"]["items"] = [
                _gear_item(
                    "Lock",
                    "dnd5e.content.srd2014.item.lock",
                    "lock-1",
                )
            ]
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Lock picker",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            current = await _call(
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
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "action_id": "pick-lock",
                "item_id": "lock-1",
                "intent": "pick",
                "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                "actor_id": actor["id"],
                "target_actor_id": actor["id"],
                "expected_actor_revision": actor["revision"],
                "expected_target_revision": actor["revision"],
                "expected_revision": current["revision"],
                "idempotency_key": "pick-lock",
            }
            try:
                await _call(
                    server,
                    "adventuring_gear_action",
                    {**request, "action_context": {"check": {"dc": 1}}},
                )
            except Exception as exc:
                assert "do not accept caller rule or outcome context" in str(exc)
            else:
                raise AssertionError("caller-computed Lock rules must be rejected")
            result = await _call(server, "adventuring_gear_action", request)
            assert result["rule_plan"]["check"] == {"ability": "dexterity", "dc": 15}
            assert result["success"] is True
            assert result["resulting_state"]["state"] == "open"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            object_key = f"{actor['id']}:lock-1"
            assert current["state"]["adventuring_gear_objects"][object_key]["state"] == "open"
            assert any(spend.get("id") == "pick-lock" for spend in current["state"]["item_spends"])
            assert await _call(server, "adventuring_gear_action", request) == result
        finally:
            close_server(server)

    asyncio.run(exercise())
