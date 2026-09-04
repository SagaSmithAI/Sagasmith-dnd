from __future__ import annotations

import asyncio
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


def _sword(item_id: str, *, attunement: str = "none") -> dict:
    return {
        "id": item_id,
        "name": "Attuned Transfer Sword",
        "kind": "weapon",
        "attunement": attunement,
        "description": "A source-preserved blade with a unique provenance.",
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


def test_attuned_ground_item_preserves_custody_across_actor_transfer(tmp_path: Path) -> None:
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
                {"name": "Attuned custody", "edition": "2014", "idempotency_key": "campaign"},
            )

            def prepared(item_id: str | None = None) -> dict:
                value = default_character_sheet()
                if item_id:
                    value, added = add_inventory_item(value, _sword(item_id))
                    value = equip_inventory_item(value, added, "main_hand")
                return value

            a_sheet = default_character_sheet()
            a_sheet, sword_id = add_inventory_item(
                a_sheet, _sword("attuned-sword", attunement="attuned")
            )
            a_sheet = equip_inventory_item(a_sheet, sword_id, "main_hand")
            actors = []
            for name, sheet, key in (
                ("A", a_sheet, "a"),
                ("B", prepared("b-collision"), "b"),
                ("C", prepared("attuned-sword"), "c"),
            ):
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": sheet,
                            },
                            "idempotency_key": key,
                        },
                    )
                )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_ground",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": actors[0]["id"],
                        "expected_campaign_revision": current["revision"],
                        "expected_character_revision": actors[0]["revision"],
                    },
                    "idempotency_key": "drop",
                },
            )
            after_drop = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            ground = after_drop["state"]["ground_items"][0]
            item_record = next(item for item in ground["items"] if item["id"] == sword_id)
            assert item_record["attunement"] == "attuned"
            b_after_drop = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[1]["id"]}},
            )
            facts = {
                "decision_id": "reach-attuned",
                "reason": "The DM confirms B can reach the detached ground item.",
                "campaign_revision": after_drop["revision"],
                "can_reach_ground_item": True,
            }
            picked = await _raw(
                server,
                "inventory_transfer",
                {
                    "mode": "ground_to_character",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": actors[1]["id"],
                        "ground_id": ground["id"],
                        "expected_campaign_revision": after_drop["revision"],
                        "expected_character_revision": b_after_drop["revision"],
                        "spatial_facts": facts,
                    },
                    "idempotency_key": "pickup-b",
                },
            )
            assert picked["status"] == "committed"
            b = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[1]["id"]}},
            )
            after_pickup_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            b_item = next(
                item for item in b["sheet"]["inventory"]["items"] if item["id"] == sword_id
            )
            assert b_item["attunement"] == "required"
            assert b_item["id"] == sword_id
            a_after_pickup = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[0]["id"]}},
            )
            a_external = next(
                ref
                for ref in a_after_pickup["sheet"]["inventory"]["external_items"]
                if ref["id"] == sword_id
            )
            assert a_external["attunement"] == "attuned"
            assert a_external["location"] == {
                "kind": "actor",
                "actor_id": actors[1]["id"],
                "item_id": sword_id,
            }
            c = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[2]["id"]}},
            )
            transfer_payload = {
                "source_character_id": actors[1]["id"],
                "target_character_id": actors[2]["id"],
                "item_id": sword_id,
                "expected_campaign_revision": after_pickup_campaign["revision"],
                "expected_source_revision": b["revision"],
                "expected_target_revision": c["revision"],
            }
            before_transfer = {
                "campaign": await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                ),
                "actors": [
                    await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": item["id"]}},
                    )
                    for item in actors
                ],
            }
            for field in (
                "expected_campaign_revision",
                "expected_source_revision",
                "expected_target_revision",
            ):
                stale = dict(transfer_payload)
                stale[field] -= 1
                with pytest.raises(ToolError, match="revision"):
                    await _call(
                        server,
                        "inventory_transfer",
                        {
                            "mode": "character_to_character",
                            "payload": stale,
                            "idempotency_key": f"stale-{field}",
                        },
                    )
                assert {
                    "campaign": await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    ),
                    "actors": [
                        await _call(
                            server,
                            "character_query",
                            {"view": "get", "payload": {"character_id": item["id"]}},
                        )
                        for item in actors
                    ],
                } == before_transfer
            transfer = await _raw(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_character",
                    "payload": transfer_payload,
                    "idempotency_key": "b-to-c",
                },
            )
            assert transfer["status"] == "committed"
            moved_id = transfer["item"]["id"]
            assert moved_id != sword_id
            assert transfer["item"]["attunement"] == "attuned"
            c_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[2]["id"]}},
            )
            after_transfer_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert (
                next(
                    item
                    for item in c_after["sheet"]["inventory"]["items"]
                    if item["id"] == moved_id
                )["name"]
                == item_record["name"]
            )
            a_after_transfer = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[0]["id"]}},
            )
            a_external_after_transfer = next(
                ref
                for ref in a_after_transfer["sheet"]["inventory"]["external_items"]
                if ref["id"] == sword_id
            )
            assert a_external_after_transfer["location"] == {
                "kind": "actor",
                "actor_id": actors[2]["id"],
                "item_id": moved_id,
            }
            a_return = await _raw(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_character",
                    "payload": {
                        "source_character_id": actors[2]["id"],
                        "target_character_id": actors[0]["id"],
                        "item_id": moved_id,
                        "expected_campaign_revision": after_transfer_campaign["revision"],
                        "expected_source_revision": c_after["revision"],
                        "expected_target_revision": a_after_transfer["revision"],
                    },
                    "idempotency_key": "c-to-a",
                },
            )
            assert a_return["status"] == "committed"
            a_final = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[0]["id"]}},
            )
            returned = next(
                item
                for item in a_final["sheet"]["inventory"]["items"]
                if item["name"] == item_record["name"]
            )
            assert returned["id"] == sword_id
            assert returned["attunement"] == "attuned"
            for field in (
                "name",
                "description",
                "mechanics",
                "attunement",
                "weight_oz",
                "price_cp",
                "source_key",
                "condition",
            ):
                assert returned[field] == item_record[field]
            assert all(
                ref["id"] != sword_id for ref in a_final["sheet"]["inventory"]["external_items"]
            )
            return_payload = {
                "source_character_id": actors[2]["id"],
                "target_character_id": actors[0]["id"],
                "item_id": moved_id,
                "expected_campaign_revision": after_transfer_campaign["revision"],
                "expected_source_revision": c_after["revision"],
                "expected_target_revision": a_after_transfer["revision"],
            }
            assert (
                await _raw(
                    server,
                    "inventory_transfer",
                    {
                        "mode": "character_to_character",
                        "payload": return_payload,
                        "idempotency_key": "c-to-a",
                    },
                )
                == a_return
            )
            final_snapshot = {
                "campaign": await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                ),
                "actors": [
                    await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": item["id"]}},
                    )
                    for item in actors
                ],
            }
            close_server(server)
            server = create_server(config)
            assert (
                await _raw(
                    server,
                    "inventory_transfer",
                    {
                        "mode": "character_to_character",
                        "payload": return_payload,
                        "idempotency_key": "c-to-a",
                    },
                )
                == a_return
            )
            assert {
                "campaign": await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                ),
                "actors": [
                    await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": item["id"]}},
                    )
                    for item in actors
                ],
            } == final_snapshot
            with pytest.raises(ToolError, match="revision"):
                await _call(
                    server,
                    "inventory_transfer",
                    {
                        "mode": "character_to_character",
                        "payload": {
                            "source_character_id": actors[2]["id"],
                            "target_character_id": actors[0]["id"],
                            "item_id": moved_id,
                            "expected_campaign_revision": after_transfer_campaign["revision"] - 1,
                            "expected_source_revision": c_after["revision"],
                            "expected_target_revision": actors[0]["revision"],
                        },
                        "idempotency_key": "stale-return",
                    },
                )
        finally:
            close_server(server)

    asyncio.run(exercise())
