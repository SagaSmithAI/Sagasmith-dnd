from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_SPELL_ID
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
def test_sleep_noncombat_missing_agent_spatial_facts_is_pending_without_payment(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Sleep agent", "edition": "2014", "idempotency_key": "campaign"},
            )
            spells = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "spell",
                        "query": "Sleep",
                    },
                },
            )
            sleep = next(item for item in spells if item["id"] == CORE_SLEEP_SPELL_ID)
            sheet = default_character_sheet()
            sheet["progression"]["level"] = 3
            sheet["progression"]["classes"] = [
                {"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}
            ]
            sheet["spellcasting"]["spell_slots"] = {
                "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"}
            }
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Bard", "sheet": sheet},
                    "idempotency_key": "caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "sleep",
                },
            )
            before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            pending = await _call(
                server,
                "character_action",
                {
                    "character_id": caster["id"],
                    "action": "cast_spell",
                    "payload": {
                        "spell_id": sleep["id"],
                        "cast_level": 1,
                        "declaration": {},
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "agent-sleep",
                },
            )
            assert pending["status"] == "pending_ruling"
            assert pending["missing"] == ["sleep.spatial_facts"]
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            assert after == before
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
@pytest.mark.parametrize(("cast_level", "expression"), [(1, "5d8"), (2, "7d8")])
def test_sleep_noncombat_agent_self_target_replay_and_incapacitated_guard(
    tmp_path: Path, cast_level: int, expression: str
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        server = create_server(
            replace(_config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills")
        )
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Sleep self", "edition": "2014", "idempotency_key": "campaign"},
            )
            sleep = next(
                item
                for item in await _call(
                    server,
                    "character_query",
                    {
                        "view": "catalog",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "kind": "spell",
                            "query": "Sleep",
                        },
                    },
                )
                if item["id"] == CORE_SLEEP_SPELL_ID
            )
            sheet = default_character_sheet()
            sheet["progression"].update(
                level=3, classes=[{"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}]
            )
            sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            sheet["spellcasting"]["spell_slots"] = {
                str(level): {
                    "label": f"Level {level}",
                    "value": 2,
                    "max": 2,
                    "recovers_on": "long_rest",
                }
                for level in (1, 2)
            }
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Bard", "sheet": sheet},
                    "idempotency_key": "caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "spell",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Target",
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
            caster_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            facts = {
                "decision_id": "self-sleep",
                "reason": "The reviewed point includes both creatures in the sphere.",
                "origin_description": "A point within ninety feet of the caster.",
                "campaign_revision": current["revision"],
                "origin_in_range": True,
                "line_of_effect_clear": True,
                "affected_target_ids": [caster["id"], target["id"]],
                "excluded_actor_ids": [],
            }
            arguments = {
                "character_id": caster["id"],
                "action": "cast_spell",
                "payload": {
                    "spell_id": sleep["id"],
                    "cast_level": cast_level,
                    "declaration": {"spatial_facts": facts},
                },
                "expected_revision": caster_state["revision"],
                "idempotency_key": f"self-{cast_level}",
            }
            raw = await server.call_tool("character_action", arguments)
            result = raw[1]
            assert result["status"] == "committed", result
            settled = result["result"]["result"]
            assert settled["pool_roll"]["expression"] == expression
            assert result.get("random_stream_receipt") or settled.get("random_stream_receipt")
            assert await server.call_tool("character_action", arguments) == raw
            with pytest.raises(Exception, match="incapacitated"):
                await server.call_tool(
                    "character_action",
                    {
                        **arguments,
                        "idempotency_key": f"new-{cast_level}",
                        "expected_revision": result["result"]["character_revision"],
                    },
                )
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_sleep_agent_combat_coordinate_free_partition_commits_and_replays(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Agent Sleep", "edition": "2014", "idempotency_key": "campaign"},
            )
            catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {"campaign_id": campaign["id"], "kind": "spell", "query": "Sleep"},
                },
            )
            sleep = next(item for item in catalog if item["id"] == CORE_SLEEP_SPELL_ID)
            sheet = default_character_sheet()
            sheet["progression"].update(
                level=3, classes=[{"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}]
            )
            sheet["spellcasting"]["spell_slots"] = {
                "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"}
            }
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Bard", "sheet": sheet},
                    "idempotency_key": "caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "sleep",
                },
            )
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Target",
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
            phase = await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "play",
                },
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "agent",
                    "campaign_id": campaign["id"],
                    "participant_ids": [caster["id"], target["id"]],
                    "participant_config": [
                        {"actor_id": caster["id"], "initiative": 20, "disposition": "friendly"},
                        {"actor_id": target["id"], "initiative": 10, "disposition": "hostile"},
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start",
                },
            )
            facts = {
                "decision_id": "sleep-agent-1",
                "reason": (
                    "The reviewed room contains both creatures within the twenty-foot sphere."
                ),
                "origin_description": "The chamber center is within ninety feet of the caster.",
                "campaign_revision": started["campaign_revision"],
                "origin_in_range": True,
                "line_of_effect_clear": True,
                "affected_target_ids": [caster["id"], target["id"]],
                "excluded_actor_ids": [],
            }
            arguments = {
                "campaign_id": campaign["id"],
                "actor_id": caster["id"],
                "spell_id": sleep["id"],
                "cast_level": 1,
                "declaration": {"spatial_facts": facts},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "agent-sleep",
            }
            raw = await server.call_tool("combat_cast_spell", arguments)
            result = raw[1]
            assert result["status"] == "committed"
            assert result["result"]["pool_roll"]["expression"] == "5d8"
            assert {item["target_id"] for item in result["result"]["targets"]} == {
                caster["id"],
                target["id"],
            }
            assert await server.call_tool("combat_cast_spell", arguments) == raw
        finally:
            close_server(server)

    asyncio.run(exercise())
