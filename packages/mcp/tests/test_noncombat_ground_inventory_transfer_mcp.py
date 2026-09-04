from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


def _sword() -> dict:
    return {
        "id": "noncombat-sword",
        "name": "Noncombat Sword",
        "kind": "weapon",
        "mechanics": {
            "category": "simple",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "1d8",
            "damage_type": "slashing",
            "properties": [],
        },
    }


async def _raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def test_noncombat_agent_ground_transfer_requires_current_dm_reach_facts(tmp_path: Path) -> None:
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
                {"name": "Noncombat ground", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet = default_character_sheet()
            sheet, sword_id = add_inventory_item(sheet, _sword())
            sheet = equip_inventory_item(sheet, sword_id, "main_hand")
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Carrier", "sheet": sheet},
                    "idempotency_key": "actor",
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            dropped = await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_ground",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": actor["id"],
                        "expected_campaign_revision": current["revision"],
                        "expected_character_revision": actor["revision"],
                    },
                    "idempotency_key": "drop",
                },
            )
            assert dropped["status"] == "committed"
            after_drop = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            ground = after_drop["state"]["ground_items"]
            assert len(ground) == 1
            ground_id = ground[0]["id"]
            actor_after_drop = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert actor_after_drop["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
            before_rejections = {
                "campaign": deepcopy(after_drop),
                "actor": deepcopy(actor_after_drop),
                "list": await _call(
                    server,
                    "character_query",
                    {"view": "list", "payload": {"campaign_id": campaign["id"]}},
                ),
            }
            base = {
                "mode": "ground_to_character",
                "payload": {
                    "campaign_id": campaign["id"],
                    "character_id": actor["id"],
                    "ground_id": ground_id,
                    "slot": "main_hand",
                    "expected_campaign_revision": after_drop["revision"],
                    "expected_character_revision": actor_after_drop["revision"],
                },
            }
            invalid = [
                ({}, "pending"),
                (
                    {
                        "spatial_facts": {
                            "decision_id": "d",
                            "reason": "The DM cannot reach this item.",
                            "campaign_revision": after_drop["revision"],
                            "can_reach_ground_item": False,
                        }
                    },
                    "affirmative",
                ),
                (
                    {
                        "spatial_facts": {
                            "decision_id": "d",
                            "reason": "The DM could reach this item earlier.",
                            "campaign_revision": after_drop["revision"] - 1,
                            "can_reach_ground_item": True,
                        }
                    },
                    "current",
                ),
                (
                    {
                        "spatial_facts": {
                            "decision_id": "d",
                            "reason": "The DM can reach this item now.",
                            "campaign_revision": after_drop["revision"],
                            "can_reach_ground_item": True,
                            "origin": {"x": 1, "y": 1},
                        }
                    },
                    "exact",
                ),
            ]
            for index, (extra, message) in enumerate(invalid):
                request = {
                    **base,
                    "payload": {**base["payload"], **extra},
                    "idempotency_key": f"invalid-{index}",
                }
                if message == "pending":
                    pending = await _call(server, "inventory_transfer", request)
                    assert pending["status"] == "pending_ruling"
                else:
                    with pytest.raises(ToolError, match=message):
                        await _call(server, "inventory_transfer", request)
                assert (
                    await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    == before_rejections["campaign"]
                )
                assert (
                    await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actor["id"]}},
                    )
                    == before_rejections["actor"]
                )
            valid = {
                **base,
                "payload": {
                    **base["payload"],
                    "spatial_facts": {
                        "decision_id": "reach-1",
                        "reason": "The DM confirms the actor can reach the ground item.",
                        "campaign_revision": after_drop["revision"],
                        "can_reach_ground_item": True,
                    },
                },
                "idempotency_key": "pickup",
            }
            picked = await _raw(server, "inventory_transfer", valid)
            assert picked["status"] == "committed"
            picked_snapshot = {
                "campaign": await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                ),
                "actor": await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                ),
            }
            assert (
                picked_snapshot["actor"]["sheet"]["inventory"]["equipment_slots"]["main_hand"]
                == sword_id
            )
            assert picked_snapshot["campaign"]["state"].get("ground_items", []) == []
            assert await _raw(server, "inventory_transfer", valid) == picked
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "inventory_transfer", valid) == picked
            assert {
                "campaign": await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                ),
                "actor": await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                ),
            } == picked_snapshot
        finally:
            close_server(server)

    asyncio.run(exercise())
