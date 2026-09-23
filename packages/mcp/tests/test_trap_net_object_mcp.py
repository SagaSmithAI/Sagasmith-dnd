"""Falling Net destruction releases restrained actors in the object attack CAS."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_destroyed_source_bound_falling_net_releases_actors_atomically(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.falling_net"}
    marker = '{"profile_id":"srd5.1.falling_net"}'
    excerpt = (
        "A net covers a ten-foot square. It has AC 10 and 20 hit points; "
        "slashing damage can destroy it.\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "net.md"
    source.write_text(f"# Traps\n\n## Falling Net\n\n{excerpt}\n", encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        module_import_roots=(tmp_path,),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Falling Net Object",
                    "edition": "2014",
                    "random_seed": "falling-net-object",
                    "idempotency_key": "campaign",
                },
            )
            campaign_id = campaign["id"]
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "idempotency_key": "draft",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "net-source",
                        "title": "Net source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            activation = await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="net-source",
                title="Net source",
                portable_id="dnd5e.module.net-source",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Falling Net AC 10 hit points", "top_k": 3},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = {
                "module_id": activation["activated"]["activation"]["module_id"],
                "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"],
                "page_start": expanded["page_start"],
                "page_end": expanded["page_end"],
                "heading_path": expanded["heading_path"],
                "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
            }
            source_ref_json = json.dumps(source_ref, sort_keys=True, separators=(",", ":"))

            trapped_sheet = default_character_sheet()
            trapped_sheet["edition"] = "2014"
            trapped_sheet["abilities"]["dexterity"]["score"] = 3
            trapped_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            trapped = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Trapped Scout",
                        "sheet": trapped_sheet,
                    },
                    "idempotency_key": "trapped-scout",
                },
            )
            breaker_sheet = default_character_sheet()
            breaker_sheet["edition"] = "2014"
            breaker_sheet["abilities"]["strength"]["score"] = 30
            breaker_sheet["inventory"]["items"] = [
                {
                    "id": "longsword",
                    "name": "Longsword",
                    "kind": "weapon",
                    "equipped": True,
                    "equipped_slot": "main_hand",
                    "mechanics": {
                        "attack_type": "melee",
                        "attack_ability": "strength",
                        "damage_formula": "1d8",
                        "damage_type": "slashing",
                        "properties": [],
                        "proficient": True,
                    },
                }
            ]
            breaker_sheet["inventory"]["equipment_slots"]["main_hand"] = "longsword"
            breaker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Net Breaker",
                        "sheet": breaker_sheet,
                    },
                    "idempotency_key": "net-breaker",
                },
            )
            campaign_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            trigger_args = {
                "campaign_id": campaign_id,
                "trap_id": "net-object-1",
                "action": "trigger",
                "source_ref": source_ref_json,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": trapped["id"],
                "area_confirmed": True,
                "expected_revision": campaign_state["revision"],
                "idempotency_key": "trigger-net",
            }
            trigger = await _call(server, "trap_state_transition", trigger_args)
            assert trigger["check"]["success"] is False
            assert trigger["trap"]["status"] == "triggered"
            assert trigger["trap"]["object_id"] == "net-object-1"
            assert trigger["trap"]["scene_id"] == expanded["scene"]["id"]
            assert trigger["trap"]["source_ref"] == source_ref_json
            trapped_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" in trapped_state["sheet"]["conditions"]

            object_profile = {
                "id": "net-object-1",
                "name": "Falling Net",
                "scene_id": expanded["scene"]["id"],
                "material": "rope",
                "size": "large",
                "resilience": "fragile",
                "armor_class": 10,
                "hit_points": 20,
                "damage_filter": {"allowed_damage_types": ["slashing"]},
            }
            final_attack_args = None
            final_attack = None
            for attempt in range(1, 21):
                campaign_state = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                )
                breaker_state = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": breaker["id"]}},
                )
                final_attack_args = {
                    "character_id": breaker["id"],
                    "action": "attack_source_object",
                    "payload": {
                        "object": object_profile,
                        "object_ruling": {
                            "reason": (
                                "DM confirms the netting profile for this source-defined trap."
                            ),
                            "source_excerpt": excerpt,
                        },
                        "weapon_id": "longsword",
                        "source_ref": source_ref,
                        "reason": "The breaker confirms the net is within melee reach.",
                        "expected_campaign_revision": campaign_state["revision"],
                    },
                    "expected_revision": breaker_state["revision"],
                    "idempotency_key": f"attack-net-{attempt}",
                }
                final_attack = await _call(server, "character_action", final_attack_args)
                assert final_attack["status"] == "committed"
                if final_attack["object"]["destroyed"]:
                    break
                assert final_attack["object"]["hit_points"] > 0
                assert "released_actors" not in final_attack
                still_trapped = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": trapped["id"]}},
                )
                assert "restrained" in still_trapped["sheet"]["conditions"]
            else:
                raise AssertionError("20 real object attacks did not destroy the Falling Net")

            assert final_attack["object"]["armor_class"] == 10
            assert final_attack["object"]["hit_point_maximum"] == 20
            assert final_attack["object"]["hit_points"] == 0
            assert final_attack["object"]["destroyed"] is True
            assert final_attack["object"]["source_ref"] == source_ref
            assert final_attack["object"]["last_attack"]["attack"]["hit"] is True
            assert final_attack["object"]["last_attack"]["damage"]["applied_amount"] > 0
            assert final_attack["object"]["last_attack"]["damage"]["parts"][0][
                "damage_type"
            ] == "slashing"
            assert final_attack["trap"]["status"] == "spent"
            assert final_attack["trap"]["object_destroyed"] is True
            assert final_attack["trap"]["object_hit_points"] == 0
            assert final_attack["trap"]["released_actor_ids"] == [trapped["id"]]
            assert final_attack["released_actors"] == [trapped["id"]]
            assert final_attack["campaign_revision"] > trigger["campaign_revision"]
            assert "random_stream_receipt" in final_attack
            replay = await _call(server, "character_action", final_attack_args)
            assert replay == final_attack
            with pytest.raises(ToolError, match="character revision conflict"):
                await _call(
                    server,
                    "character_action",
                    {**final_attack_args, "idempotency_key": "stale-net-attack"},
                )
            trapped_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" not in trapped_state["sheet"]["conditions"]
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "character_action", final_attack_args) == final_attack
            trapped_state = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" not in trapped_state["sheet"]["conditions"]
        finally:
            close_server(restarted)

    asyncio.run(exercise())
