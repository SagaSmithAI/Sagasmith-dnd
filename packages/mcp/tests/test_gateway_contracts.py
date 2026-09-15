import asyncio
from contextlib import asynccontextmanager

import pytest
from mcp.types import CallToolResult, TextContent

import sagasmith_dnd_mcp.gateway as gateway
from sagasmith_dnd_mcp.gateway import (
    DndMcpClient,
    McpDispatchUnknownError,
    McpToolRejectedError,
)


def test_rejected_tool_preserves_all_recovery_fields() -> None:
    envelope = {"code": "revision_conflict", "retryable": False,
                "recovery": {"action": "read", "revision": 7}}
    result = CallToolResult(is_error=True, structured_content=envelope,
                            content=[TextContent(type="text", text="Conflict")])
    with pytest.raises(McpToolRejectedError) as caught:
        DndMcpClient._raise_tool_error(result)
    assert caught.value.result is result
    assert caught.value.structured_content == envelope


def test_stable_catalog_dispatch_does_not_use_exposure() -> None:
    async def exercise() -> None:
        calls = []

        class Session:
            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return CallToolResult(content=[])

        async def refresh(session):
            pytest.fail("stable catalog dispatch must not refresh exposure")

        await DndMcpClient("unused")._call_in_session(
            Session(), "combat_query", {"campaign_id": "one"}, refresh,
        )
        assert calls == [("combat_query", {"campaign_id": "one"})]

    asyncio.run(exercise())


def test_cancelled_queue_entry_never_dispatches(monkeypatch) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        class Session:
            async def initialize(self):
                pass

            async def discover(self):
                pass

            async def list_tools(self):
                pass

            async def call_tool(self, name, arguments):
                calls.append(name)
                if name == "first":
                    entered.set()
                    await release.wait()
                return CallToolResult(content=[])

        @asynccontextmanager
        async def transport(url):
            yield (None, None)

        @asynccontextmanager
        async def session(*args, **kwargs):
            yield Session()

        monkeypatch.setattr(gateway, "streamable_http_client", transport)
        monkeypatch.setattr(gateway, "ClientSession", session)
        client = DndMcpClient("unused")
        await client.start()
        try:
            first = asyncio.create_task(client.call_tool("first", {}))
            await entered.wait()
            cancelled = asyncio.create_task(client.call_tool("cancelled-write", {}))
            await asyncio.sleep(0)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            release.set()
            await first
            await client.call_tool("last", {})
            assert calls == ["first", "last"]
        finally:
            await client.stop()

    asyncio.run(exercise())


def test_transport_error_does_not_repeat_dispatched_write(monkeypatch) -> None:
    async def exercise() -> None:
        calls = []

        class Session:
            async def initialize(self):
                pass

            async def discover(self):
                pass

            async def list_tools(self):
                pass

            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                raise ConnectionError("response lost after commit")

        @asynccontextmanager
        async def transport(url):
            yield (None, None)

        @asynccontextmanager
        async def session(*args, **kwargs):
            yield Session()

        monkeypatch.setattr(gateway, "streamable_http_client", transport)
        monkeypatch.setattr(gateway, "ClientSession", session)
        client = DndMcpClient("unused")
        await client.start()
        try:
            with pytest.raises(McpDispatchUnknownError) as caught:
                await client.call_tool("write", {"idempotency_key": "original"})
            assert caught.value.recovery["idempotency_key"] == "original"
            assert len(calls) == 1
        finally:
            await client.stop()

    asyncio.run(exercise())


def test_queue_rejects_overload_and_stop_resolves_waiters() -> None:
    async def exercise() -> None:
        client = DndMcpClient("unused", queue_capacity=1)
        client._runner_task = asyncio.create_task(asyncio.Event().wait())
        waiting = asyncio.create_task(client.call_tool("write", {}))
        await asyncio.sleep(0)
        with pytest.raises(asyncio.QueueFull):
            await client.call_tool("write", {})
        await client.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            await waiting

    asyncio.run(exercise())


def test_explicit_legacy_adapter_loads_exposure() -> None:
    async def exercise() -> None:
        calls = []
        refreshes = []

        class Session:
            async def call_tool(self, name, arguments):
                calls.append((name, arguments.get("action")))
                result = {}
                if arguments.get("action") == "set":
                    result = {"loaded_tools": ["combat_query"]}
                return CallToolResult(content=[], structured_content=result)

        async def refresh(session):
            refreshes.append(True)

        await DndMcpClient("unused", legacy_exposure=True)._call_in_session(
            Session(), "combat_query", {"campaign_id": "one"}, refresh,
        )
        assert calls == [("exposure", "get"), ("exposure", "open"),
                         ("exposure", "set"), ("combat_query", None)]
        assert refreshes == [True]

    asyncio.run(exercise())


def test_deadline_cancels_queued_request_without_dispatch() -> None:
    async def exercise() -> None:
        client = DndMcpClient("unused", request_timeout=0.01)
        client._runner_task = asyncio.create_task(asyncio.Event().wait())
        try:
            with pytest.raises(TimeoutError):
                await client.call_tool("write", {"idempotency_key": "deadline"})
            request = client._queue.get_nowait()
            assert request.future.cancelled()
        finally:
            await client.stop()

    asyncio.run(exercise())


def test_pool_slow_start_does_not_block_another_browser(monkeypatch) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        clients = []

        class Client:
            def __init__(self, url):
                self.number = len(clients)
                self.stopped = False
                clients.append(self)

            async def start(self):
                if self.number == 0:
                    entered.set()
                    await release.wait()

            async def stop(self):
                self.stopped = True

        monkeypatch.setattr(gateway, "DndMcpClient", Client)
        pool = gateway.DndClientPool(gateway.GatewayConfig())
        first = asyncio.create_task(pool.session(None, "slow"))
        await entered.wait()
        try:
            token, client, created = await asyncio.wait_for(pool.session(None, "fast"), 1)
            assert token and created and client.number == 1
            await pool.close()
            release.set()
            with pytest.raises(RuntimeError, match="closed"):
                await first
            assert all(client.stopped for client in clients)
            assert pool.sessions == {}
        finally:
            release.set()
            await pool.close()

    asyncio.run(exercise())
