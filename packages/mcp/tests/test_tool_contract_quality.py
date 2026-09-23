from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolRequestParams
from sagasmith_dnd_runtime.skills import SkillCatalog

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


def _server(tmp_path: Path):
    return create_server(
        McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=tmp_path / "dnd-skills",
            modulegen_skills_dir=tmp_path / "modulegen-skills",
            auto_seed_rules=False,
        )
    )


def test_every_public_tool_has_model_usable_contract_metadata(tmp_path: Path) -> None:
    async def exercise() -> None:
        tools = await _server(tmp_path).list_tools()
        assert len(tools) == 84
        for tool in tools:
            assert tool.description.strip(), tool.name
            properties = tool.input_schema.get("properties") or {}
            assert all(
                str(schema.get("description") or "").strip() for schema in properties.values()
            ), tool.name
            assert tool.output_schema is not None, tool.name
            assert (tool.output_schema.get("properties") or {}).get("error"), tool.name
            assert len(tool.output_schema.get("properties") or {}) > 2, tool.name
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is not None, tool.name
            assert tool.annotations.destructive_hint is not None, tool.name
            assert tool.annotations.idempotent_hint is not None, tool.name
            assert tool.annotations.open_world_hint is False, tool.name

        by_name = {tool.name: tool for tool in tools}
        workspace = Path(__file__).resolve().parents[3]
        catalog = SkillCatalog(
            dnd_root=workspace / "skills",
            modulegen_root=workspace / "skills/dnd-module-generator",
        )
        for name in by_name:
            if name == "exposure":
                # Transport-owned compatibility guidance is documented separately;
                # it is intentionally not a Runtime application operation.
                continue
            section = catalog.section(
                kind="asset", identifier="dnd:full/references/generated-operations.md",
                heading=name, max_chars=20_000,
            )
            assert not section["truncated"], name
            schema = json.loads(section["content"].split("```json\n", 1)[1].split("```", 1)[0])
            assert schema["properties"].keys() == by_name[name].input_schema["properties"].keys()
        for name in ("character_content_apply", "character_ability_apply",
                     "character_metadata_update", "character_spell_prepare", "combat_end",
                     "module_set_progress"):
            schema = by_name[name].input_schema
            revision = (
                "expected_state_version" if name == "module_set_progress" else "expected_revision"
            )
            assert {revision, "idempotency_key"} <= set(schema["required"])
            assert schema["properties"][revision]["type"] == "integer"

    asyncio.run(exercise())


def test_advertised_bounds_are_enforced_before_dispatch(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        with pytest.raises(ToolError, match="limit must be an integer between 1 and 100"):
            await server.call_tool("campaign_query", {"view": "list", "limit": 101})
        with pytest.raises(ToolError, match="65536 characters"):
            await server.call_tool(
                "campaign_change",
                {"action": "update", "payload": {"description": "x" * 65_537}},
            )

    asyncio.run(exercise())


def test_modern_validation_error_is_structured_and_actionable(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(_server(tmp_path), mode="2026-07-28") as client:
            result = await client.call_tool("campaign_query", {"view": "unsupported"})
            assert result.is_error is True
            assert result.content
            error = result.structured_content["error"]
            assert error["code"] == "invalid_request"
            assert error["message"]
            assert isinstance(error["retryable"], bool)
            assert error["recovery"]

    asyncio.run(exercise())


def test_catalog_and_tool_metrics_have_only_bounded_dimensions(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        async with Client(server, mode="2026-07-28") as client:
            await client.list_tools(cache_mode="reload")
            await client.call_tool("storage_status")
        snapshot = server.metrics_snapshot()
        assert {row["stage"] for row in snapshot} == {"catalog", "tool"}
        assert {row["protocol_era"] for row in snapshot} == {"modern"}
        assert all(
            set(row) == {"stage", "protocol_era", "operation", "outcome", "count"}
            for row in snapshot
        )

    asyncio.run(exercise())


def test_transport_propagates_standard_trace_context(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        request_context = SimpleNamespace(
            protocol_version="2026-07-28",
            meta={},
            request=SimpleNamespace(
                headers={
                    "traceparent": (
                        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                    ),
                    "tracestate": "vendor=opaque",
                    "baggage": "deployment=test",
                }
            ),
        )
        result = await server._handle_call_tool(
            request_context,
            CallToolRequestParams(name="storage_status", arguments={}),
        )
        assert result.meta["sagasmith_trace_context"] == {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "tracestate": "vendor=opaque",
            "baggage": "deployment=test",
        }

    asyncio.run(exercise())
