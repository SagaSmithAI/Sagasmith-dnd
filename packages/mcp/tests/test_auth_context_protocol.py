from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from sagasmith_core.auth_context import AUTH_CONTEXT_META_KEY, sign_auth_context

from sagasmith_dnd_mcp.config import McpConfig

SECRET = "test-auth-context-secret-with-at-least-32-bytes"


def _meta(*, actor: str, nonce: str, authorization_epoch: int = 0) -> dict[str, object]:
    return {
        AUTH_CONTEXT_META_KEY: sign_auth_context(
            secret=SECRET,
            host="test-host",
            channel="discord",
            actor_principal=actor,
            conversation_principal="discord:group:shared-room",
            session_id="test-session",
            authorization_epoch=authorization_epoch,
            nonce=nonce,
        )
    }


def test_short_auth_context_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGASMITH_AUTH_CONTEXT_SECRET", "short")

    with pytest.raises(ValueError, match="at least 32 bytes"):
        McpConfig.from_environment()


def test_stdio_rejects_missing_tampered_and_replayed_auth_context(tmp_path: Path) -> None:
    async def exercise() -> None:
        env = dict(os.environ)
        env.update(
            {
                "SAGASMITH_DND_MCP_HOME": str(tmp_path / "home"),
                "SAGASMITH_DND_MCP_AUTO_SEED": "0",
                "SAGASMITH_AUTH_CONTEXT_SECRET": SECRET,
            }
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sagasmith_dnd_mcp.server"],
            cwd=Path(__file__).parents[1],
            env=env,
        )
        arguments = {"action": "open", "principal_id": "discord:user:member-1"}
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                missing = await session.call_tool("exposure", arguments)
                assert missing.isError

                tampered = await session.call_tool(
                    "exposure",
                    arguments,
                    meta=_meta(actor="discord:user:member-2", nonce="tampered"),
                )
                assert tampered.isError

                metadata = _meta(actor="discord:user:member-1", nonce="accepted")
                accepted = await session.call_tool("exposure", arguments, meta=metadata)
                assert not accepted.isError
                receipt = accepted.content[0].meta["sagasmith_auth_context_receipt"]
                assert receipt["actor_principal"] == "discord:user:member-1"
                assert receipt["conversation_principal"] == "discord:group:shared-room"
                assert receipt["tool"] == "exposure"

                replayed = await session.call_tool("exposure", arguments, meta=metadata)
                assert replayed.isError
                assert "already used" in replayed.content[0].text

                opened_epoch = int(receipt["revision"])
                updated = await session.call_tool(
                    "exposure",
                    {
                        "action": "set",
                        "principal_id": "discord:user:member-1",
                        "add_tool_ids": ["campaign_create"],
                    },
                    meta=_meta(
                        actor="discord:user:member-1",
                        nonce="set-tools",
                        authorization_epoch=opened_epoch,
                    ),
                )
                assert not updated.isError
                stale = await session.call_tool(
                    "exposure",
                    {
                        "action": "search",
                        "principal_id": "discord:user:member-1",
                        "query": "campaign_create",
                    },
                    meta=_meta(
                        actor="discord:user:member-1",
                        nonce="stale-epoch",
                        authorization_epoch=opened_epoch,
                    ),
                )
                assert stale.isError
                assert "authorization_epoch is stale" in stale.content[0].text

                rebound = await session.call_tool(
                    "exposure",
                    {"action": "open", "principal_id": "discord:user:member-2"},
                    meta=_meta(actor="discord:user:member-2", nonce="actor-rebind"),
                )
                assert not rebound.isError
                rebound_receipt = rebound.content[0].meta["sagasmith_auth_context_receipt"]
                assert rebound_receipt["actor_principal"] == "discord:user:member-2"
                assert rebound_receipt["conversation_principal"] == (
                    "discord:group:shared-room"
                )

    asyncio.run(exercise())
