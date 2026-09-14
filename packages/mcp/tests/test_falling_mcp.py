from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


def test_public_fall_settlement_is_atomic_and_idempotent(tmp_path: Path) -> None:
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )

    async def call(server, name: str, arguments: dict):
        _, result = await server.call_tool(name, arguments)
        if isinstance(result, dict) and "action" in result and "result" in result:
            return result["result"]
        return result.get("result", result) if isinstance(result, dict) else result

    async def exercise() -> None:
        server = create_server(config)
        campaign = await call(
            server,
            "campaign_create",
            {"name": "Falling", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        actor = await call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "name": "Falling target",
                    "campaign_id": campaign["id"],
                    "character_type": "pc",
                    "sheet": sheet,
                },
                "idempotency_key": "actor",
            },
        )
        current_campaign = await call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        started = await call(
            server,
            "combat_start",
            {
                "campaign_id": campaign["id"],
                "positioning_mode": "agent",
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {"actor_id": actor["id"], "initiative": 10, "death_saves": True}
                ],
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "start",
            },
        )
        request = {
            "campaign_id": campaign["id"],
            "target_id": actor["id"],
            "action": "fall",
            "payload": {"distance_ft": 10},
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "fall-once",
        }
        settled = await call(server, "combat_hp_change", request)
        replay = await call(server, "combat_hp_change", request)

        assert replay == settled
        assert settled["result"]["dice_count"] == 1
        assert 1 <= settled["result"]["damage_roll"]["total"] <= 6
        assert settled["result"]["damage"]["damage_type"] == "bludgeoning"
        assert settled["result"]["prone_added"] is True
        assert "prone" in settled["combat"]["combatants"][0]["conditions"]
        assert any(
            receipt["mechanic_id"] == "dnd5e.core.movement.falling"
            for receipt in settled["rule_receipts"]
        )

        actor_after = await call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        with pytest.raises(Exception, match="campaign revision conflict"):
            await call(
                server,
                "combat_hp_change",
                {
                    **request,
                    "idempotency_key": "fall-stale",
                    "expected_revision": current_campaign["revision"],
                },
            )
        actor_unchanged = await call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        assert actor_unchanged == actor_after

    asyncio.run(exercise())
