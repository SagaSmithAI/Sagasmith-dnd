"""Confirmed-area Collapsing Roof settlement and durable replay."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_collapsing_roof_uses_only_explicit_area_targets_and_replays(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.collapsing_roof"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = (
        "When the trap is triggered, the unstable ceiling collapses. Any creature in the "
        "area beneath the unstable section must succeed on a DC 15 Dexterity saving throw, "
        "taking 22 (4d10) bludgeoning damage on a failed save, or half as much damage on a "
        "successful one. Once the trap is triggered, the floor of the area is filled with "
        "rubble and becomes difficult terrain.\n\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "collapsing-roof.md"
    source.write_text(f"# Traps\n\n{excerpt}\n", encoding="utf-8")
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
                    "name": "Collapsing Roof",
                    "edition": "2014",
                    "random_seed": "collapsing-roof-trap",
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
                        "source_key": "roof-source",
                        "title": "Collapsing Roof source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="roof-source",
                title="Collapsing Roof source",
                portable_id="dnd5e.module.collapsing-roof",
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            sheet["abilities"]["dexterity"]["score"] = 3
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Under Roof", "sheet": sheet},
                    "idempotency_key": "under-roof",
                },
            )
            hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "Collapsing Roof",
                    "top_k": 3,
                },
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = json.dumps(expanded["source_ref"], sort_keys=True, separators=(",", ":"))
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            before_actor = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                },
            )
            args = {
                "campaign_id": campaign_id,
                "trap_id": "roof-section-1",
                "action": "trigger",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": actor["id"],
                "target_ids": [actor["id"]],
                "area_confirmed": True,
                "trigger_fact": {
                    "kind": "knock_wedged_beam",
                    "scene_id": expanded["scene"]["id"],
                    "beam_id": "roof-section-1",
                    "action_spent": True,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "roof-trigger-1",
            }
            with pytest.raises(ToolError, match="confirmed source-defined area"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **args,
                        "area_confirmed": False,
                        "idempotency_key": "roof-area-unconfirmed",
                    },
                )
            with pytest.raises(ToolError, match="explicitly confirmed targets"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **args,
                        "target_ids": [],
                        "idempotency_key": "roof-no-targets",
                    },
                )
            with pytest.raises(ToolError, match="action was spent"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **args,
                        "trigger_fact": {**args["trigger_fact"], "action_spent": False},
                        "idempotency_key": "roof-beam-no-action",
                    },
                )
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            unchanged_actor = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                },
            )
            assert unchanged_campaign["revision"] == current["revision"]
            assert unchanged_actor["revision"] == before_actor["revision"]

            result = await _call(server, "trap_state_transition", args)
            assert result["trap"]["status"] == "spent"
            assert result["trap"]["trigger_fact"] == args["trigger_fact"]
            assert result["affected_actor_ids"] == [actor["id"]]
            assert isinstance(result["targets"][0]["save"]["success"], bool)
            assert result["targets"][0]["damage_amount"] == (
                result["damage_roll"]["total"]
                if result["targets"][0]["success"] is False
                else result["damage_roll"]["total"] // 2
            )
            assert result["random_stream_receipt"]["draw_count"] >= 5
            assert result["terrain_effect"] == {
                "kind": "difficult_terrain",
                "effect": "rubble",
                "area": "beneath_unstable_ceiling",
                "source_ref": source_ref,
                "trap_id": "roof-section-1",
                "active": True,
            }
            assert await _call(server, "trap_state_transition", args) == result
            after_actor = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                },
            )
            assert after_actor["sheet"]["combat"]["hp"]["value"] < 100
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "trap_state_transition", args) == result
            after_restart = await _call(
                restarted,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                },
            )
            assert after_restart["revision"] == after_actor["revision"]
            assert after_restart["sheet"] == after_actor["sheet"]
        finally:
            close_server(restarted)

    asyncio.run(exercise())
