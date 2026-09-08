from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


def test_character_check_rejects_string_booleans_before_actor_lookup(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Strict checks", "edition": "2014", "idempotency_key": "campaign"},
        )
        current = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "enter-play",
            },
        )
        cases = (
            {
                "action": "check",
                "payload": {
                    "actor_id": "missing",
                    "kind": "ability",
                    "ability": "strength",
                    "advantage": "false",
                },
            },
            {
                "action": "group",
                "payload": {
                    "actor_ids": ["missing"],
                    "ability": "strength",
                    "dc": 10,
                    "advantage": "false",
                },
            },
            {
                "action": "contest",
                "payload": {
                    "source_actor_id": "missing-source",
                    "target_actor_id": "missing-target",
                    "source_ability": "strength",
                    "target_ability": "strength",
                    "source_advantage": "false",
                },
            },
        )
        for index, case in enumerate(cases):
            with pytest.raises(
                Exception, match=r"payload\.(?:advantage|source_advantage) must be a boolean"
            ):
                await _call(
                    server,
                    "character_check",
                    {
                        "campaign_id": campaign["id"],
                        **case,
                        "expected_revision": current["revision"],
                        "idempotency_key": f"invalid-bool-{index}",
                    },
                )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        (
            "character_state_change",
            {
                "character_id": "missing",
                "action": "damage",
                "payload": {"parts": [], "critical": "false"},
            },
            "critical",
        ),
        (
            "character_action",
            {
                "character_id": "missing",
                "action": "cast_spell",
                "payload": {"spell_id": "missing", "ritual": "false"},
            },
            "ritual",
        ),
        (
            "combat_movement",
            {
                "campaign_id": "missing",
                "actor_id": "missing",
                "action": "move",
                "payload": {"distance": 5, "crawl": "false"},
            },
            "crawl",
        ),
        (
            "combat_hp_change",
            {
                "campaign_id": "missing",
                "target_id": "missing",
                "action": "damage",
                "payload": {"parts": [], "critical": "false"},
            },
            "critical",
        ),
        (
            "branch_change",
            {
                "campaign_id": "missing",
                "action": "create",
                "payload": {"name": "branch", "checkout": "false"},
            },
            "checkout",
        ),
    ],
)
def test_public_facades_reject_string_booleans_before_settlement(
    tmp_path: Path,
    tool: str,
    arguments: dict,
    field: str,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        with pytest.raises(Exception, match=rf"payload\.{field} must be a boolean"):
            await _call(server, tool, arguments)

    asyncio.run(exercise())


def test_required_boolean_fields_reject_strings(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        cases = (
            (
                "character_state_change",
                {
                    "character_id": "missing",
                    "action": "breathing_transition",
                    "payload": {"can_breathe": "false"},
                },
                "can_breathe",
            ),
            (
                "combat_hp_change",
                {
                    "campaign_id": "missing",
                    "target_id": "missing",
                    "action": "save_damage",
                    "payload": {
                        "target_ids": ["missing"],
                        "source_actor_id": "source",
                        "source_card_id": "card",
                        "source_card_kind": "feature",
                        "save_ability": "dexterity",
                        "save_dc": 10,
                        "damage_expression": "1d4",
                        "damage_type": "fire",
                        "half_on_success": "false",
                        "mechanic_source_excerpt": "source excerpt",
                        "agent_ruling": {},
                    },
                },
                "half_on_success",
            ),
        )
        for tool, arguments, field in cases:
            with pytest.raises(Exception, match=rf"payload\.{field} must be a boolean"):
                await _call(server, tool, arguments)

    asyncio.run(exercise())
