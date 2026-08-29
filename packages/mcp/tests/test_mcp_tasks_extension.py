from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.extension import (
    ClaimContext,
    ClientExtension,
    ResultClaim,
    advertise,
)
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    Request,
    Result,
)
from pydantic import ConfigDict, Field
from sagasmith_core.auth_context import (
    AUTH_CONTEXT_META_KEY,
    sign_delegated_auth_context,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.mcp_tasks import (
    TASKS_EXTENSION_ID,
    DurableTaskStore,
    TaskExpiredError,
    TaskIdentity,
    TaskIdParams,
    UpdateTaskParams,
)
from sagasmith_dnd_mcp.server import create_server

SECRET = "test-modern-task-secret-with-at-least-32-bytes"
SERVICE = "sagasmith-dnd-mcp"


class TaskResult(Result):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    result_type: Literal["task", "complete"] = Field(alias="resultType")
    task_id: str | None = Field(alias="taskId", default=None)
    status: str | None = None
    status_message: str | None = Field(alias="statusMessage", default=None)
    created_at: str | None = Field(alias="createdAt", default=None)
    last_updated_at: str | None = Field(alias="lastUpdatedAt", default=None)
    ttl_ms: int | None = Field(alias="ttlMs", default=None)
    poll_interval_ms: int | None = Field(alias="pollIntervalMs", default=None)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CreateTaskResult(Result):
    """Exact extension result claimed by the hosted Agent task adapter."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    result_type: Literal["task"] = Field(alias="resultType")
    task_id: str = Field(alias="taskId")
    status: str
    status_message: str | None = Field(alias="statusMessage", default=None)
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int = Field(alias="ttlMs")
    poll_interval_ms: int = Field(alias="pollIntervalMs")


async def _resolve_task_result(
    task: CreateTaskResult,
    ctx: ClaimContext,
) -> CallToolResult:
    """Exercise the same strict discovery gate and claim flow as Agent."""

    extensions = ctx.session.server_capabilities.extensions or {}
    if TASKS_EXTENSION_ID not in extensions:
        raise RuntimeError("server did not negotiate the MCP Tasks extension")
    for _ in range(100):
        status = await ctx.session.send_request(
            GetTaskRequest(params=TaskIdParams(taskId=task.task_id)),
            TaskResult,
        )
        if status.status == "completed":
            assert status.result is not None
            return CallToolResult.model_validate(status.result)
        if status.status in {"failed", "cancelled"}:
            raise RuntimeError(f"task terminated as {status.status}")
        await asyncio.sleep(0.02)
    raise TimeoutError("task did not complete within the test polling window")


class TasksClientExtension(ClientExtension):
    """Minimal Agent-compatible task result claim used across real transports."""

    identifier = TASKS_EXTENSION_ID

    def claims(self) -> tuple[ResultClaim[CreateTaskResult], ...]:
        return (
            ResultClaim(
                result_type="task",
                model=CreateTaskResult,
                resolve=_resolve_task_result,
                protocol_versions=frozenset({"2026-07-28"}),
            ),
        )


class GetTaskRequest(Request[TaskIdParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskIdParams
    name_param = "taskId"


class CancelTaskRequest(Request[TaskIdParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: TaskIdParams
    name_param = "taskId"


class UpdateTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams
    name_param = "taskId"


def _config(tmp_path: Path, *, auth: bool = False) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        auth_context_secret=SECRET if auth else None,
    )


def _unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"D&D MCP exited during startup ({process.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("D&D MCP Streamable HTTP endpoint did not start")


async def _direct(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await server.call_tool(name, arguments)
    structured = result[1] if isinstance(result, tuple) else result.structured_content
    assert isinstance(structured, dict)
    return dict(structured.get("result") or structured)


async def _campaign(server: Any, key: str = "campaign") -> str:
    created = await _direct(
        server,
        "campaign_create",
        {"name": "Task Table", "idempotency_key": key},
    )
    return str(created["id"])


async def _create_task(
    client: Client,
    arguments: dict[str, Any],
    *,
    meta: dict[str, object] | None = None,
) -> TaskResult:
    return await client.session.send_request(
        CallToolRequest(
            params=CallToolRequestParams(
                name="module_draft",
                arguments=arguments,
                meta=meta,
            )
        ),
        TaskResult,
    )


def _delegation(
    *,
    operation: str,
    nonce: str,
    campaign_id: str,
    requester: str = "system:local",
    resource_owner: str = "owner:campaign",
    acting_host: str = "workload:sagasmith-agent",
    room_turn_id: str = "room-turn:tasks",
    base_revision: int = 0,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    return {
        AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
            secret=SECRET,
            issuer="sagasmith-agent",
            target_service=SERVICE,
            caller_principal="workload:sagasmith-agent",
            workload_identity="workload:sagasmith-agent",
            requester_principal=requester,
            resource_owner_principal=resource_owner,
            acting_host_principal=acting_host,
            authorized_audience=SERVICE,
            allowed_operations=[operation],
            conversation_principal="room:tasks",
            campaign_id=campaign_id,
            room_turn_id=room_turn_id,
            base_revision=base_revision,
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    }


def _identity(**updates: Any) -> TaskIdentity:
    values = {
        "owner_principal": "player:one",
        "authority_principal": "workload:sagasmith-agent",
        "resource_owner_principal": "owner:campaign",
        "campaign_id": "campaign:one",
        "room_turn_id": "room-turn:one",
        "base_revision": 7,
        "auth_meta": {},
    }
    values.update(updates)
    return TaskIdentity(**values)


def _store_task(
    store: DurableTaskStore,
    *,
    key: str,
    now: float,
    identity: TaskIdentity | None = None,
    ttl_ms: int = 1_000,
) -> tuple[Any, bool]:
    return store.create_or_get(
        tool_name="module_draft",
        arguments={"campaign_id": "campaign:one", "action": "start", "idempotency_key": key},
        request_hash=f"hash:{key}",
        identity=identity or _identity(),
        idempotency_key=key,
        ttl_ms=ttl_ms,
        poll_interval_ms=100,
        now=now,
    )


def test_store_expires_terminal_results_and_leaves_bounded_tombstone(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3", tombstone_ttl_seconds=60)
    record, _ = _store_task(store, key="expire", now=100, ttl_ms=1_000)
    assert store.claim(record.task_id, "lease", lease_seconds=10, now=100)
    store.complete(record.task_id, "lease", {"secret": "result"}, now=100.1)
    assert store.get(record.task_id, now=100.9) is not None
    with pytest.raises(TaskExpiredError):
        store.get(record.task_id, now=101.1)
    assert store.tombstone_reason(record.task_id, now=101.1) == "expired"
    assert store.tombstone_reason(record.task_id, now=162) is None

    active, _ = _store_task(store, key="active-expiry", now=200, ttl_ms=1_000)
    assert store.claim(active.task_id, "crashed", lease_seconds=10, now=200)
    with pytest.raises(TaskExpiredError):
        store.get(active.task_id, now=201.1)
    assert store.tombstone_reason(active.task_id, now=201.1) == "expired"


def test_store_bounds_expiry_tombstones(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3", max_rows=2)
    for index in range(4):
        record, _ = _store_task(
            store,
            key=f"expired-{index}",
            now=100 + index * 2,
            ttl_ms=1_000,
        )
        with pytest.raises(TaskExpiredError):
            store.get(record.task_id, now=101.1 + index * 2)
        store.maintain(now=101.2 + index * 2)
    assert store.stats()["tombstones"] <= 2


def test_store_recovers_expired_lease_and_bounds_capacity(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3", max_rows=2)
    first, _ = _store_task(store, key="first", now=100, ttl_ms=100_000)
    assert store.claim(first.task_id, "dead-worker", lease_seconds=1, now=100)
    assert not store.claim(first.task_id, "new-worker", lease_seconds=1, now=100.5)
    assert store.claim(first.task_id, "new-worker", lease_seconds=1, now=101.1)
    store.complete(first.task_id, "new-worker", {"ok": True}, now=101.2)
    _store_task(store, key="second", now=102, ttl_ms=100_000)
    third, _ = _store_task(store, key="third", now=103, ttl_ms=100_000)
    assert store.get(third.task_id, now=103) is not None
    assert store.get(first.task_id, now=103) is None
    assert store.tombstone_reason(first.task_id, now=103) == "evicted"


def test_store_cancellation_is_cooperative_and_discards_claimed_result(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3")
    record, _ = _store_task(store, key="cancel-store", now=100, ttl_ms=100_000)
    assert store.claim(record.task_id, "worker", lease_seconds=10, now=100)
    requested = store.request_cancel(record.task_id, now=100.1)
    assert requested.status == "working"
    assert requested.cancel_requested is True
    store.cancel_claimed(record.task_id, "worker", now=100.2)
    cancelled = store.get(record.task_id, now=100.3)
    assert cancelled is not None and cancelled.status == "cancelled"


def test_idempotency_reuse_requires_identical_trusted_binding(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3")
    original, created = _store_task(store, key="same", now=100)
    replay, replay_created = _store_task(store, key="same", now=100.1)
    assert created and not replay_created and replay.task_id == original.task_id
    with pytest.raises(ValueError, match="trusted identity binding"):
        _store_task(
            store,
            key="same",
            now=100.2,
            identity=_identity(resource_owner_principal="owner:other"),
        )


def test_store_rejects_new_work_beyond_byte_capacity(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3", max_database_bytes=1024 * 1024)
    with pytest.raises(RuntimeError, match="at capacity"):
        store.create_or_get(
            tool_name="module_draft",
            arguments={"payload": {"content": "x" * (2 * 1024 * 1024)}},
            request_hash="oversized",
            identity=_identity(),
            idempotency_key="oversized",
            ttl_ms=60_000,
            poll_interval_ms=100,
        )


def test_capacity_rejects_new_work_but_preserves_idempotent_replay(tmp_path: Path) -> None:
    store = DurableTaskStore(tmp_path / "tasks.sqlite3", max_rows=1)
    first, _ = _store_task(store, key="first", now=100, ttl_ms=100_000)
    replay, created = _store_task(store, key="first", now=100.1, ttl_ms=100_000)
    assert not created and replay.task_id == first.task_id
    with pytest.raises(RuntimeError, match="at capacity"):
        _store_task(store, key="second", now=100.2, ttl_ms=100_000)
    assert store.get(first.task_id, now=100.3) is not None


def test_modern_negotiation_and_legacy_sync_fallback(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id = await _campaign(server)
        arguments = {
            "campaign_id": campaign_id,
            "action": "start",
            "payload": {"name": "Fallback", "content": "# Fallback\n\nA bounded room."},
            "idempotency_key": "fallback",
        }
        async with Client(server, mode="2026-07-28") as modern_without_tasks:
            synchronous = await modern_without_tasks.call_tool("module_draft", arguments)
            assert isinstance(synchronous, CallToolResult)
            assert synchronous.is_error is False
        legacy_arguments = {**arguments, "idempotency_key": "legacy"}
        legacy_arguments["payload"] = {
            "name": "Legacy",
            "content": "# Legacy\n\nA synchronous room.",
        }
        async with Client(
            server,
            mode="legacy",
            extensions=[advertise(TASKS_EXTENSION_ID)],
        ) as legacy:
            synchronous = await legacy.call_tool("module_draft", legacy_arguments)
            assert isinstance(synchronous, CallToolResult)
            # Legacy still receives the ordinary synchronous result shape. Its
            # compatibility exposure workflow remains unchanged.
            assert synchronous.result_type == "complete"

    asyncio.run(exercise())


def test_modern_task_success_and_cancel_use_standard_methods(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id = await _campaign(server)
        async with Client(
            server,
            mode="auto",
            extensions=[advertise(TASKS_EXTENSION_ID)],
        ) as client:
            assert client.session.protocol_version == "2026-07-28"
            adopted_extensions = client.session.server_capabilities.extensions or {}
            assert TASKS_EXTENSION_ID in adopted_extensions
            task = await _create_task(
                client,
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "payload": {"name": "Async", "content": "# Async\n\nA durable room."},
                    "idempotency_key": "async",
                },
            )
            assert task.result_type == "task"
            assert task.task_id is not None
            update_ack = await client.session.send_request(
                UpdateTaskRequest(
                    params=UpdateTaskParams(taskId=task.task_id, inputResponses={})
                ),
                TaskResult,
            )
            assert update_ack.result_type == "complete"
            for _ in range(50):
                polled = await client.session.send_request(
                    GetTaskRequest(params=TaskIdParams(taskId=task.task_id)),
                    TaskResult,
                )
                if polled.status != "working":
                    break
                await asyncio.sleep(0.02)
            assert polled.status == "completed"
            result = CallToolResult.model_validate(polled.result)
            assert result.is_error is False
            assert result.meta is not None
            assert result.meta["sagasmith_task_authority"]["requester_principal"] == (
                "system:local"
            )

            cancel_task = await _create_task(
                client,
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "payload": {"name": "Cancel", "content": "# Cancel\n\nCancel safely."},
                    "idempotency_key": "cancel",
                },
            )
            assert cancel_task.task_id is not None
            ack = await client.session.send_request(
                CancelTaskRequest(params=TaskIdParams(taskId=cancel_task.task_id)),
                TaskResult,
            )
            assert ack.result_type == "complete"

    asyncio.run(exercise())


def test_inprocess_agent_style_claim_uses_adopted_tasks_capability(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id = await _campaign(server)
        async with Client(
            server,
            mode="auto",
            extensions=[TasksClientExtension()],
        ) as client:
            adopted_extensions = client.session.server_capabilities.extensions or {}
            assert TASKS_EXTENSION_ID in adopted_extensions
            result = await client.call_tool(
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "payload": {
                        "name": "In-process claim",
                        "content": "# In-process\n\nResolve through ResultClaim.",
                    },
                    "idempotency_key": "inprocess-claim",
                },
            )
            assert result.is_error is False
            assert result.structured_content is not None

    asyncio.run(exercise())


def test_task_handle_never_authorizes_cross_requester_or_expired_delegation(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        bootstrap = create_server(_config(tmp_path))
        campaign_id = await _campaign(bootstrap)
        server = create_server(_config(tmp_path, auth=True))
        async with Client(
            server,
            mode="2026-07-28",
            extensions=[advertise(TASKS_EXTENSION_ID)],
        ) as client:
            task = await _create_task(
                client,
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "payload": {"name": "Secure", "content": "# Secure\n\nPrivate."},
                    "principal_id": "model:forged",
                    "idempotency_key": "secure",
                },
                meta=_delegation(
                    operation="module_draft", nonce="create", campaign_id=campaign_id
                ),
            )
            assert task.task_id is not None
            replay = await _create_task(
                client,
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "payload": {"name": "Secure", "content": "# Secure\n\nPrivate."},
                    "principal_id": "model:a-different-forgery",
                    "idempotency_key": "secure",
                },
                meta=_delegation(
                    operation="module_draft",
                    nonce="create-retry",
                    campaign_id=campaign_id,
                ),
            )
            assert replay.task_id == task.task_id
            with pytest.raises(MCPError, match="authorization denied"):
                await client.session.send_request(
                    GetTaskRequest(
                        params=TaskIdParams(
                            taskId=task.task_id,
                            meta=_delegation(
                                operation="tasks/get",
                                nonce="other-requester",
                                campaign_id=campaign_id,
                                requester="player:other",
                            ),
                        )
                    ),
                    TaskResult,
                )
            old = datetime.now(UTC) - timedelta(minutes=10)
            with pytest.raises(MCPError, match="authorization denied"):
                await client.session.send_request(
                    GetTaskRequest(
                        params=TaskIdParams(
                            taskId=task.task_id,
                            meta=_delegation(
                                operation="tasks/get",
                                nonce="expired",
                                campaign_id=campaign_id,
                                issued_at=old,
                                expires_at=old + timedelta(minutes=1),
                            ),
                        )
                    ),
                    TaskResult,
                )

    asyncio.run(exercise())


def test_restart_recovery_requires_fresh_authorization(tmp_path: Path) -> None:
    async def exercise() -> None:
        bootstrap = create_server(_config(tmp_path))
        campaign_id = await _campaign(bootstrap, "restart-campaign")
        store = DurableTaskStore(tmp_path / "home" / "mcp-tasks.sqlite3")
        identity = _identity(
            owner_principal="system:local",
            campaign_id=campaign_id,
            room_turn_id="room-turn:tasks",
            base_revision=0,
        )
        record = store.create_or_get(
            tool_name="module_draft",
            arguments={
                "campaign_id": campaign_id,
                "action": "start",
                "payload": {"name": "Restarted", "content": "# Restarted\n\nResume."},
                "principal_id": "model:forged",
                "idempotency_key": "restart",
            },
            request_hash="restart-request",
            identity=identity,
            idempotency_key="restart",
            ttl_ms=60_000,
            poll_interval_ms=20,
        )[0]

        # A newly constructed server sees the durable row but cannot resume it
        # until a new task-method delegation is verified.
        restarted = create_server(_config(tmp_path, auth=True))
        async with Client(
            restarted,
            mode="2026-07-28",
            extensions=[advertise(TASKS_EXTENSION_ID)],
        ) as client:
            with pytest.raises(MCPError, match="authorization denied"):
                await client.session.send_request(
                    GetTaskRequest(params=TaskIdParams(taskId=record.task_id)),
                    TaskResult,
                )
            status = await client.session.send_request(
                GetTaskRequest(
                    params=TaskIdParams(
                        taskId=record.task_id,
                        meta=_delegation(
                            operation="tasks/get",
                            nonce="restart-resume",
                            campaign_id=campaign_id,
                        ),
                    )
                ),
                TaskResult,
            )
            assert status.status in {"working", "completed"}
            for attempt in range(50):
                if status.status == "completed":
                    break
                status = await client.session.send_request(
                    GetTaskRequest(
                        params=TaskIdParams(
                            taskId=record.task_id,
                            meta=_delegation(
                                operation="tasks/get",
                                nonce=f"restart-poll-{attempt}",
                                campaign_id=campaign_id,
                            ),
                        )
                    ),
                    TaskResult,
                )
                await asyncio.sleep(0.02)
            assert status.status == "completed"
            result = CallToolResult.model_validate(status.result)
            authority = dict(result.meta or {})["sagasmith_task_authority"]
            assert authority["requester_principal"] == "system:local"
            assert authority["acting_host_principal"] == "workload:sagasmith-agent"

    asyncio.run(exercise())


def test_task_request_models_mirror_task_id_into_mcp_name() -> None:
    assert GetTaskRequest.name_param == "taskId"
    assert UpdateTaskRequest.name_param == "taskId"
    assert CancelTaskRequest.name_param == "taskId"


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_real_transport_task_parity_and_mcp_name(tmp_path: Path, transport: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "SAGASMITH_DND_MCP_HOME": str(tmp_path / f"home-{transport}"),
            "SAGASMITH_DND_MCP_AUTO_SEED": "0",
            "SAGASMITH_DND_SKILLS_DIR": str(tmp_path / "dnd"),
            "SAGASMITH_MODULEGEN_SKILLS_DIR": str(tmp_path / "modulegen"),
        }
    )
    process: subprocess.Popen[str] | None = None
    if transport == "stdio":
        endpoint: Any = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sagasmith_dnd_mcp.server"],
            env=environment,
        )
    else:
        port = _unused_loopback_port()
        environment.update(
            {
                "SAGASMITH_DND_MCP_TRANSPORT": "streamable-http",
                "SAGASMITH_DND_MCP_HTTP_HOST": "127.0.0.1",
                "SAGASMITH_DND_MCP_HTTP_PORT": str(port),
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "sagasmith_dnd_mcp.server"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait_for_port(port, process)
        endpoint = f"http://127.0.0.1:{port}/mcp"

    async def exercise() -> None:
        async with Client(
            endpoint,
            mode="auto",
            extensions=[TasksClientExtension()],
        ) as client:
            assert client.session.protocol_version == "2026-07-28"
            adopted_extensions = client.session.server_capabilities.extensions or {}
            assert TASKS_EXTENSION_ID in adopted_extensions
            created = await client.call_tool(
                "campaign_create",
                {"name": f"{transport} Task", "idempotency_key": f"campaign-{transport}"},
            )
            campaign = dict(created.structured_content or {})
            campaign = dict(campaign.get("result") or campaign)
            result = await client.call_tool(
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": transport,
                        "content": f"# {transport}\n\nTransport parity.",
                    },
                    "idempotency_key": f"module-{transport}",
                },
            )
            # The ResultClaim performed a strict discovered-capability gate,
            # sent tasks/get with Mcp-Name=<taskId> over HTTP, and converted
            # the terminal embedded payload back to the standard result.
            assert result.is_error is False
            assert result.structured_content is not None

    try:
        asyncio.run(exercise())
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
