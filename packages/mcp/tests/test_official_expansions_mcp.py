from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd-skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=False,
    )


def test_official_expansion_registry_is_core_visible_but_unmounted_by_default(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_2014 = await _call(
            server,
            "campaign_create",
            {
                "name": "2014 registry",
                "edition": "2014",
                "idempotency_key": "official-registry-2014",
            },
        )
        profile_2014 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2014["id"], "action": "get_profile"},
        )
        campaign_2024 = await _call(
            server,
            "campaign_create",
            {
                "name": "2024 registry",
                "edition": "2024",
                "idempotency_key": "official-registry-2024",
            },
        )
        profile_2024 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2024["id"], "action": "get_profile"},
        )

        assert len(profile_2014["available_official_expansions"]) == 10
        assert {
            tuple(item["editions"])
            for item in profile_2014["available_official_expansions"]
        } == {
            ("2014",)
        }
        assert profile_2014["official_expansion_mount"] == {
            "configured": False,
            "installed": 0,
            "available": 10,
            "support_installed": 0,
            "support_available": 1,
        }
        assert profile_2024["available_official_expansions"] == []

    asyncio.run(exercise())
