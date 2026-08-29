from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
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


def _meta(
    *,
    operation: str,
    nonce: str,
    base_revision: int = 0,
    campaign_id: str = "campaign:lobby",
    requester_principal: str = "system:local",
    resource_owner_principal: str = "system:local",
    acting_host_principal: str = "system:local",
) -> dict[str, object]:
    return {
        AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
            secret=SECRET,
            issuer="sagasmith-agent",
            target_service=SERVICE,
            caller_principal="workload:sagasmith-agent",
            workload_identity="hosted-worker:test",
            requester_principal=requester_principal,
            resource_owner_principal=resource_owner_principal,
            acting_host_principal=acting_host_principal,
            authorized_audience=SERVICE,
            allowed_operations=[operation],
            conversation_principal="room:test",
            campaign_id=campaign_id,
            room_turn_id="room-turn:test",
            base_revision=base_revision,
            nonce=nonce,
        )
    }


async def _direct(server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = await server.call_tool(name, arguments)
    structured = result[1] if isinstance(result, tuple) else result.structured_content
    assert isinstance(structured, dict)
    return structured


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


def test_modern_requester_authorizes_and_acting_host_owns_audit(tmp_path: Path) -> None:
    async def exercise() -> None:
        bootstrap = create_server(
            McpConfig(
                home=tmp_path / "home",
                database_url=None,
                chroma_url=None,
                chroma_path_override=None,
                dnd_skills_dir=tmp_path / "dnd",
                modulegen_skills_dir=tmp_path / "modulegen",
                auto_seed_rules=False,
            )
        )
        campaign = await _direct(
            bootstrap,
            "campaign_create",
            {"name": "Delegated Table", "idempotency_key": "delegated-campaign"},
        )
        await _direct(
            bootstrap,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:authorized",
                "payload": {"role": "player"},
                "by_principal_id": "system:local",
            },
        )

        server = _server(tmp_path)
        async with Client(server, mode="2026-07-28") as client:
            allowed = await client.call_tool(
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                    "principal_id": "model:forged-admin",
                },
                meta=_meta(
                    operation="campaign_query",
                    nonce="identity-allowed",
                    campaign_id=str(campaign["id"]),
                    requester_principal="player:authorized",
                    resource_owner_principal="owner:campaign",
                    acting_host_principal="workload:sagasmith-agent",
                ),
            )
            assert allowed.is_error is False
            receipt = allowed.content[0].meta[AUTH_CONTEXT_RECEIPT_META_KEY]
            assert receipt["requester_principal"] == "player:authorized"
            assert receipt["resource_owner_principal"] == "owner:campaign"
            assert receipt["acting_host_principal"] == "workload:sagasmith-agent"
            # Core derives authority from acting_host_principal and authorization
            # from requester_principal; the receipt retains the original facts.

            denied = await client.call_tool(
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                    "principal_id": "system:local",
                },
                meta=_meta(
                    operation="campaign_query",
                    nonce="identity-denied",
                    campaign_id=str(campaign["id"]),
                    requester_principal="player:denied",
                    resource_owner_principal="owner:campaign",
                    acting_host_principal="workload:sagasmith-agent",
                ),
            )
            assert denied.is_error is True
            assert denied.structured_content["error"]["code"] == "authorization_denied"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("mode", "expected_count"),
    [("legacy", 7), ("2026-07-28", 77)],
)
def test_real_stdio_legacy_modern_contract_matrix(
    tmp_path: Path,
    mode: str,
    expected_count: int,
) -> None:
    async def exercise() -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "SAGASMITH_DND_MCP_HOME": str(tmp_path / f"home-{mode}"),
                "SAGASMITH_DND_MCP_AUTO_SEED": "0",
                "SAGASMITH_DND_SKILLS_DIR": str(tmp_path / "dnd-skills"),
                "SAGASMITH_MODULEGEN_SKILLS_DIR": str(tmp_path / "modulegen-skills"),
            }
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sagasmith_dnd_mcp.server"],
            env=environment,
        )
        async with Client(parameters, mode=mode) as client:
            catalog = await client.list_tools(cache_mode="reload")
            names = [tool.name for tool in catalog.tools]
            assert names == sorted(names)
            assert len(names) == expected_count
            status = await client.call_tool("storage_status")
            assert status.is_error is False
            assert status.content
            assert status.structured_content is not None

    asyncio.run(exercise())
