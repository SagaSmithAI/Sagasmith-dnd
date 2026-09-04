import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.breathing import BREATHING_EFFECT_ID
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


def test_breathing_snapshot_branch_restore_and_stale_cas_are_atomic(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Breathing persistence", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["abilities"]["constitution"]["score"] = 10
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Breather", "sheet": sheet},
                "idempotency_key": "actor",
            },
        )
        started = await _call(
            server,
            "character_state_change",
            {
                "character_id": actor["id"],
                "action": "breathing_transition",
                "payload": {"can_breathe": False},
                "expected_revision": actor["revision"],
                "idempotency_key": "no-air",
            },
        )
        assert (
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": False},
                    "expected_revision": actor["revision"],
                    "idempotency_key": "no-air",
                },
            )
        ) == started
        current = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        snapshot = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Breathing base",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": "",
                "idempotency_key": "snapshot",
            },
        )
        snapshot_campaign = await _call(
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
                "payload": {"period": "round", "count": 2},
                "expected_revision": snapshot_campaign["revision"],
                "idempotency_key": "advance",
            },
        )
        changed = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
        )
        timer = next(
            item for item in changed["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
        )
        assert timer["metadata"]["hold_remaining_rounds"] == 8
        changed_snapshot = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Breathing in progress",
                "expected_revision": advanced["campaign_revision"],
                "expected_head_snapshot_id": snapshot["id"],
                "idempotency_key": "changed-snapshot",
            },
        )
        after_snapshot = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 3},
                "expected_revision": after_snapshot["revision"],
                "idempotency_key": "advance-after-snapshot",
            },
        )
        before_fork = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        timer = next(
            item for item in before_fork["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
        )
        assert timer["metadata"]["hold_remaining_rounds"] == 5
        latest_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Breathing after divergence",
                "expected_revision": latest_campaign["revision"],
                "expected_head_snapshot_id": changed_snapshot["id"],
                "idempotency_key": "latest-snapshot",
            },
        )
        branches = await _call(
            server, "branch_query", {"campaign_id": campaign["id"], "view": "list", "payload": {}}
        )
        main = next(item for item in branches if item["is_current"])
        branch_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "branch_change",
            {
                "campaign_id": campaign["id"],
                "action": "create",
                "payload": {
                    "name": "earlier-breathing-state",
                    "from_snapshot_id": changed_snapshot["id"],
                    "checkout": True,
                },
                "expected_revision": branch_campaign["revision"],
                "expected_branch_id": main["id"],
                "idempotency_key": "fork",
            },
        )
        restored = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
        )
        restored_timer = next(
            item for item in restored["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
        )
        assert restored_timer["metadata"]["hold_remaining_rounds"] == 8
        assert restored["sheet"] == changed["sheet"]

        close_server(server)
        server = create_server(_config(tmp_path))
        restarted = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        restarted_timer = next(
            item for item in restarted["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
        )
        assert restarted_timer["metadata"]["hold_remaining_rounds"] == 8
        assert restarted["sheet"] == restored["sheet"]

        stale_revision = restarted["revision"]
        replacement_sheet = deepcopy(restarted["sheet"])
        replacement_sheet["abilities"]["constitution"]["score"] = 11
        mutated = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": actor["id"],
                "sheet": replacement_sheet,
                "expected_revision": stale_revision,
                "idempotency_key": "mutate",
            },
        )
        before = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
        )
        assert next(
            item for item in before["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
        ) == restarted_timer
        with pytest.raises(ToolError, match="revision"):
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": True},
                    "expected_revision": stale_revision,
                    "idempotency_key": "stale-air",
                },
            )
        after = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
        )
        assert mutated["revision"] == before["revision"]
        assert after == before
        close_server(server)

    asyncio.run(exercise())
