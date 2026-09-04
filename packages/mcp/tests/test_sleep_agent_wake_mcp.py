from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_SPELL_ID
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
def test_agent_sleep_wake_requires_contact_facts_and_one_helper_action(tmp_path: Path) -> None:
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
                {"name": "Agent wake", "edition": "2014", "idempotency_key": "campaign"},
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

            caster_sheet = default_character_sheet()
            caster_sheet["progression"].update(
                level=3,
                classes=[{"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}],
            )
            caster_sheet["spellcasting"]["spell_slots"] = {
                "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"}
            }

            async def create(name: str, sheet: dict | None = None) -> dict:
                return await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": name,
                            "sheet": sheet or default_character_sheet(),
                        },
                        "idempotency_key": name,
                    },
                )

            caster = await create("Caster", caster_sheet)
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "apply-sleep",
                },
            )
            helper = await create("Helper")
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            sleeper_one = await create("Sleeper One", target_sheet)
            sleeper_two = await create("Sleeper Two", target_sheet)

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
                    "campaign_id": campaign["id"],
                    "positioning_mode": "agent",
                    "participant_ids": [
                        caster["id"],
                        helper["id"],
                        sleeper_one["id"],
                        sleeper_two["id"],
                    ],
                    "participant_config": [
                        {"actor_id": caster["id"], "initiative": 30, "disposition": "friendly"},
                        {"actor_id": helper["id"], "initiative": 20, "disposition": "friendly"},
                        {"actor_id": sleeper_one["id"], "initiative": 10, "disposition": "hostile"},
                        {"actor_id": sleeper_two["id"], "initiative": 9, "disposition": "hostile"},
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start",
                },
            )
            cast_args = {
                "campaign_id": campaign["id"],
                "actor_id": caster["id"],
                "spell_id": sleep["id"],
                "cast_level": 1,
                "declaration": {
                    "spatial_facts": {
                        "decision_id": "sleep-area",
                        "reason": "Reviewed area includes both sleepers and excludes the two out-of-area allies.",
                        "origin_description": "The caster's reviewed origin is within ninety feet of the area.",
                        "campaign_revision": started["campaign_revision"],
                        "origin_in_range": True,
                        "line_of_effect_clear": True,
                        "affected_target_ids": [sleeper_one["id"], sleeper_two["id"]],
                        "excluded_actor_ids": [caster["id"], helper["id"]],
                    }
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "cast-sleep",
            }
            cast_raw = await server.call_tool("combat_cast_spell", cast_args)
            assert cast_raw[1]["status"] == "committed"
            cast_revision = cast_raw[1]["campaign_revision"]
            ended = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": caster["id"],
                    "expected_revision": cast_revision,
                    "idempotency_key": "end-caster",
                },
            )

            async def snapshot() -> dict:
                return {
                    "campaign": await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    ),
                    "actors": [
                        await _call(
                            server,
                            "character_query",
                            {"view": "get", "payload": {"character_id": actor["id"]}},
                        )
                        for actor in (caster, helper, sleeper_one, sleeper_two)
                    ],
                }

            before_missing = await snapshot()
            with pytest.raises(ToolError, match="contact-range decision"):
                await server.call_tool(
                    "combat_common_action",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": helper["id"],
                        "action": "shake_sleep",
                        "target_id": sleeper_one["id"],
                        "expected_revision": ended["campaign_revision"],
                        "idempotency_key": "wake-missing",
                    },
                )
            assert await snapshot() == before_missing

            wake_args = {
                "campaign_id": campaign["id"],
                "actor_id": helper["id"],
                "action": "shake_sleep",
                "target_id": sleeper_one["id"],
                "payload": {
                    "spatial_facts": {
                        "decision_id": "touch-one",
                        "reason": "The helper can reach the sleeping ally at contact range.",
                        "campaign_revision": ended["campaign_revision"],
                        "can_touch_target": True,
                    }
                },
                "expected_revision": ended["campaign_revision"],
                "idempotency_key": "wake-one",
            }
            for suffix, facts in (
                ("false", {"can_touch_target": False}),
                ("one", {"can_touch_target": 1}),
                ("stale", {"campaign_revision": ended["campaign_revision"] - 1}),
                ("coordinates", {"coordinates": [{"x": 0, "y": 0}]}),
            ):
                invalid_facts = dict(wake_args["payload"]["spatial_facts"])
                invalid_facts.update(facts)
                with pytest.raises(ToolError):
                    await server.call_tool(
                        "combat_common_action",
                        {
                            **wake_args,
                            "payload": {"spatial_facts": invalid_facts},
                            "idempotency_key": f"wake-{suffix}",
                        },
                    )
                assert await snapshot() == before_missing
            raw_wake = await server.call_tool("combat_common_action", wake_args)
            assert raw_wake[1]["status"] == "committed"
            awakened = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": sleeper_one["id"]}},
            )
            assert "unconscious" not in awakened["sheet"]["conditions"]
            assert all(
                not effect["active"]
                for effect in awakened["sheet"]["effects"]
                if effect.get("source_spell_id") == CORE_SLEEP_SPELL_ID
            )
            assert await server.call_tool("combat_common_action", wake_args) == raw_wake
            no_action_args = {
                **wake_args,
                "target_id": sleeper_two["id"],
                "expected_revision": raw_wake[1]["campaign_revision"],
                "payload": {
                    "spatial_facts": {
                        **wake_args["payload"]["spatial_facts"],
                        "campaign_revision": raw_wake[1]["campaign_revision"],
                    }
                },
                "idempotency_key": "wake-two",
            }
            with pytest.raises(ToolError, match="legal action payment"):
                await server.call_tool(
                    "combat_common_action",
                    no_action_args,
                )
            after_reject = await snapshot()
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("combat_common_action", wake_args) == raw_wake
            assert await snapshot() == after_reject
        finally:
            close_server(server)

    asyncio.run(exercise())
