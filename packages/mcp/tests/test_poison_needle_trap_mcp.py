"""Source-bound Poison Needle runtime settlement and durable replay."""

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


def test_poison_needle_trigger_is_source_bound_atomic_and_replayable(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.poison_needle"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = (
        "When the trap is triggered, the needle extends 3 inches straight out from the lock. "
        "A creature within range takes 1 piercing damage and 11 (2d10) poison damage, and "
        "must succeed on a DC 15 Constitution saving throw or be poisoned for 1 hour.\n\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "poison-needle.md"
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
                    "name": "Poison Needle",
                    "edition": "2014",
                    "random_seed": "poison-needle-trap",
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
                        "source_key": "needle-source",
                        "title": "Poison Needle source",
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
                source_key="needle-source",
                title="Poison Needle source",
                portable_id="dnd5e.module.poison-needle",
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            sheet["abilities"]["constitution"]["score"] = 3
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Victim", "sheet": sheet},
                    "idempotency_key": "victim",
                },
            )
            hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "Poison Needle",
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
                "trap_id": "needle-lock-1",
                "action": "trigger",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": actor["id"],
                "area_confirmed": True,
                "expected_revision": current["revision"],
                "idempotency_key": "needle-trigger-1",
            }
            with pytest.raises(ToolError, match="confirmed source-defined area"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **args,
                        "area_confirmed": False,
                        "idempotency_key": "needle-out-of-range",
                    },
                )
            with pytest.raises(ToolError, match="only profile_id"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **args,
                        "profile": {**profile, "trigger": {"poison_expression": "99d99"}},
                        "idempotency_key": "needle-caller-rules",
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
            assert result["trap"]["range_confirmed"] is True
            assert "attack" not in result
            assert result["piercing"]["damage_type"] == "piercing"
            assert result["piercing"]["hp_damage"] == 1
            assert result["poison"]["success"] is False
            assert result["poison"]["damage_amount"] > 0
            assert result["poison"]["damage_expression"] == "2d10"
            assert result["poison"]["damage_amount"] == result["poison"]["damage_roll"]["total"]
            assert result["condition_effect"]["duration"] == {
                "period": "hour",
                "remaining": 1,
            }
            assert result["random_stream_receipt"]["draw_count"] >= 3
            assert await _call(server, "trap_state_transition", args) == result
            after_actor = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                },
            )
            effect = next(
                item
                for item in after_actor["sheet"]["effects"]
                if item["id"] == result["condition_effect"]["id"]
            )
            assert "poisoned" in after_actor["sheet"]["conditions"]
            assert effect["kind"] == "poison"
            assert effect["metadata"]["trap_state"] == {
                "profile_id": "srd5.1.poison_needle",
                "trap_id": "needle-lock-1",
                "source_ref": source_ref,
                "target_actor_id": actor["id"],
            }
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
