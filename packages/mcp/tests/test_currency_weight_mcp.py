import asyncio
from pathlib import Path

import pytest

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


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


def test_currency_weight_default_survives_creation_templates_replacement_and_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Currency weight", "idempotency_key": "campaign"},
        )
        direct = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Direct",
                    "sheet": {"inventory": {"wallet": {"gp": 10}}},
                },
                "principal_id": "system:local",
                "idempotency_key": "direct",
            },
        )
        assert direct["sheet"]["inventory"]["encumbrance"]["ignore_currency_weight"] is False
        assert direct["derived"]["inventory"]["total_weight_oz"] == pytest.approx(3.2)

        template = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "name": "Currency template",
                    "sheet": {"inventory": {"wallet": {"gp": 10}}},
                },
                "principal_id": "system:local",
                "idempotency_key": "template",
            },
        )
        instantiated = await _call(
            server,
            "character_create_from",
            {
                "mode": "template",
                "payload": {
                    "campaign_id": campaign["id"],
                    "template_id": template["id"],
                    "name": "Instantiated",
                },
                "principal_id": "system:local",
                "idempotency_key": "instantiate",
            },
        )
        assert instantiated["derived"]["inventory"]["total_weight_oz"] == pytest.approx(3.2)

        opted_out = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": direct["id"],
                "sheet": {
                    "inventory": {
                        "wallet": {"gp": 10},
                        "encumbrance": {"ignore_currency_weight": True},
                    }
                },
                "expected_revision": direct["revision"],
                "idempotency_key": "opt-out",
            },
        )
        assert opted_out["derived"]["inventory"]["total_weight_oz"] == 0

        restored_default = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": direct["id"],
                "sheet": {"inventory": {"wallet": {"gp": 10}}},
                "expected_revision": opted_out["revision"],
                "idempotency_key": "restore-default",
            },
        )
        assert restored_default["derived"]["inventory"]["total_weight_oz"] == pytest.approx(3.2)

        close_server(server)
        restarted = create_server(config)
        persisted = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": direct["id"]}},
        )
        assert persisted["sheet"]["inventory"]["encumbrance"] == {
            "mode": "standard",
            "ignore_currency_weight": False,
        }
        assert persisted["derived"]["inventory"]["total_weight_oz"] == pytest.approx(3.2)
        close_server(restarted)

    asyncio.run(exercise())
