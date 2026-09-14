import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    value = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


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


def test_petrified_poison_immunity_and_suspension_are_atomic_through_public_tools(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Petrified poison", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["conditions"] = ["petrified", "poisoned"]
        sheet["effects"] = [
            {
                "id": "old-poison",
                "name": "Old poison",
                "kind": "poison",
                "source": "test-source",
                "active": True,
                "concentration": False,
                "duration": {"period": "hour", "remaining": 3},
                "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
                "description": "",
            },
            {
                "id": "stone",
                "name": "Petrified",
                "kind": "timed_conditions",
                "source": "test-source",
                "active": True,
                "concentration": False,
                "duration": {"period": "hour", "remaining": 2},
                "changes": [{"path": "conditions", "mode": "add", "value": "petrified"}],
                "description": "",
            },
        ]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Stone", "sheet": sheet},
                "idempotency_key": "actor",
            },
        )
        before = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        with pytest.raises(ToolError, match="blocked while the creature is petrified"):
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "effect_add",
                    "payload": {
                        "effect": {
                            "id": "new-poison",
                            "name": "New poison",
                            "kind": "poison",
                            "active": True,
                            "duration": {"period": "hour", "remaining": 1},
                            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
                        }
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "new-poison",
                },
            )
        after_rejection = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        assert after_rejection == before

        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        advanced = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {
                    "period": "hour",
                    "count": 1,
                    "expected_elapsed_ticks": 600,
                },
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "advance-hour",
            },
        )
        assert advanced["expired"] == {actor["id"]: []}
        advance_arguments = {
            "campaign_id": campaign["id"],
            "action": "clock_advance",
            "payload": {
                "period": "hour",
                "count": 1,
                "expected_elapsed_ticks": 600,
            },
            "expected_revision": current_campaign["revision"],
            "idempotency_key": "advance-hour",
        }
        assert await _call(server, "campaign_change", advance_arguments) == advanced
        with pytest.raises(ToolError, match="revision conflict"):
            await _call(
                server,
                "campaign_change",
                {**advance_arguments, "idempotency_key": "stale-advance"},
            )
        suspended = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        effects = {item["id"]: item for item in suspended["sheet"]["effects"]}
        assert effects["old-poison"]["duration"]["remaining"] == 3
        assert effects["stone"]["duration"]["remaining"] == 1

        ended_arguments = {
            "character_id": actor["id"],
            "action": "effect_remove",
            "payload": {"effect_id": "stone"},
            "expected_revision": suspended["revision"],
            "idempotency_key": "end-stone",
        }
        ended = await _call(
            server,
            "character_state_change",
            ended_arguments,
        )
        assert ended["sheet"]["conditions"] == ["poisoned"]
        after_end = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        assert after_end["sheet"]["conditions"] == ["poisoned"]
        close_server(server)

    asyncio.run(exercise())
