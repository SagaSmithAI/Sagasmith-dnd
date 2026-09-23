from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


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


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def test_source_defined_madness_choice_is_typed_persisted_and_replay_safe(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Madness typed choice",
                    "edition": "2014",
                    "random_seed": "madness-typed-choice",
                    "idempotency_key": "choice-campaign",
                },
            )
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Witness"},
                    "principal_id": "system:local",
                    "idempotency_key": "choice-witness",
                },
            )
            applied = None
            for attempt in range(100):
                current = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                applied = await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "madness_apply",
                        "payload": {
                            "category": "long_term",
                            "trigger_reason": "The DM recorded a source-defined horror event.",
                        },
                        "principal_id": "system:local",
                        "expected_revision": current["revision"],
                        "idempotency_key": f"choice-madness-{attempt}",
                    },
                )
                if applied["madness"]["effect_key"] == "repetitive_activity":
                    break
            else:
                raise AssertionError("seeded campaign did not produce a typed-choice result")

            replacement = deepcopy(applied["character"]["sheet"])
            replacement["effects"] = [
                item for item in replacement["effects"] if item["id"] != applied["effect_id"]
            ]
            with pytest.raises(ToolError, match="source-owned madness lifecycle"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": actor["id"],
                        "sheet": replacement,
                        "principal_id": "system:local",
                        "expected_revision": applied["character"]["revision"],
                        "idempotency_key": "cannot-remove-madness-through-sheet-replace",
                    },
                )

            request = {
                "character_id": actor["id"],
                "action": "madness_choose",
                "payload": {
                    "effect_id": applied["effect_id"],
                    "choice": {"kind": "activity", "activity_id": "counting floor tiles"},
                },
                "principal_id": "system:local",
                "expected_revision": applied["character"]["revision"],
                "idempotency_key": "choose-repetitive-activity",
            }
            chosen = await _call(server, "character_state_change", request)
            assert chosen["madness_choice"] == {
                "effect_id": applied["effect_id"],
                "kind": "activity",
                "activity_id": "counting floor tiles",
            }
            assert await _call(server, "character_state_change", request) == chosen

            close_server(server)
            server = create_server(config)
            assert await _call(server, "character_state_change", request) == chosen
            restored = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in restored["sheet"]["effects"]
                if item["id"] == applied["effect_id"]
            )
            assert effect["metadata"]["madness"]["choice"] == request["payload"]["choice"]
            request["payload"]["choice"] = {"kind": "goal", "goal_id": "different"}
            request["idempotency_key"] = "conflicting-choice"
            request["expected_revision"] = restored["revision"]
            with pytest.raises(ToolError, match="kind or fields|does not require"):
                await _call(server, "character_state_change", request)
        finally:
            close_server(server)

    asyncio.run(exercise())
