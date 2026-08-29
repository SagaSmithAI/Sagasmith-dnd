from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client
from sagasmith_core.auth_context import (
    AUTH_CONTEXT_META_KEY,
    AUTH_CONTEXT_RECEIPT_META_KEY,
    sign_delegated_auth_context,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server

SECRET = "test-modern-auth-context-secret-with-at-least-32-bytes"
SERVICE = "sagasmith-dnd-mcp"


def _server(tmp_path: Path):
    return create_server(
        McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=tmp_path / "dnd",
            modulegen_skills_dir=tmp_path / "modulegen",
            auto_seed_rules=False,
            auth_context_secret=SECRET,
        )
    )


def _meta(*, operation: str, nonce: str, base_revision: int = 0) -> dict[str, object]:
    return {
        AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
            secret=SECRET,
            issuer="sagasmith-agent",
            target_service=SERVICE,
            caller_principal="workload:sagasmith-agent",
            workload_identity="hosted-worker:test",
            requester_principal="system:local",
            resource_owner_principal="system:local",
            acting_host_principal="system:local",
            authorized_audience=SERVICE,
            allowed_operations=[operation],
            conversation_principal="room:test",
            campaign_id="campaign:lobby",
            room_turn_id="room-turn:test",
            base_revision=base_revision,
            nonce=nonce,
        )
    }


def test_modern_discover_catalog_and_request_scoped_delegation(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        async with Client(server, mode="2026-07-28") as client:
            assert client.protocol_version == "2026-07-28"
            first = await client.list_tools(cache_mode="reload")
            assert first.meta is not None
            server_info = first.meta["io.modelcontextprotocol/serverInfo"]
            assert server_info == {"name": "SagaSmith D&D", "version": "0.1.0"}
            names = [tool.name for tool in first.tools]
            assert names == sorted(names)
            assert len(names) == len(set(names))
            assert all(tool.annotations is not None for tool in first.tools)
            assert all(
                tool.annotations.read_only_hint is not None
                and tool.annotations.destructive_hint is not None
                and tool.annotations.idempotent_hint is not None
                and tool.annotations.open_world_hint is not None
                for tool in first.tools
            )

            denied = await client.call_tool(
                "exposure",
                {"action": "open", "principal_id": "system:local"},
                meta=_meta(operation="campaign_query", nonce="wrong-operation"),
            )
            assert denied.is_error
            assert "does not allow this operation" in denied.content[0].text
            assert denied.structured_content == {
                "error": {
                    "code": "authorization_denied",
                    "message": "auth context does not allow this operation",
                    "retryable": False,
                    "recovery": (
                        "Correct the request or obtain a new audience-bound "
                        "delegation before retrying."
                    ),
                }
            }

            opened = await client.call_tool(
                "exposure",
                {"action": "open", "principal_id": "system:local"},
                meta=_meta(operation="exposure", nonce="open"),
            )
            assert not opened.is_error
            assert opened.structured_content is not None
            handle = opened.structured_content["exposure_id"]
            assert handle.startswith("exp_")
            receipt = opened.content[0].meta[AUTH_CONTEXT_RECEIPT_META_KEY]
            assert receipt["tool"] == "exposure"
            assert receipt["room_turn_id"] == "room-turn:test"

            after = await client.list_tools(cache_mode="reload")
            assert [tool.name for tool in after.tools] == names

    asyncio.run(exercise())


def test_legacy_initialize_remains_available(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        async with Client(server, mode="legacy") as client:
            assert client.protocol_version != "2026-07-28"
            listed = await client.list_tools(cache_mode="reload")
            assert {tool.name for tool in listed.tools}

    asyncio.run(exercise())
