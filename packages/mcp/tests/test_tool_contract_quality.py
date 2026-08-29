from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolRequestParams

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
        assert len(tools) == 77
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
