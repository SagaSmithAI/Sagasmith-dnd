"""Durable MCP 2026-07-28 Tasks extension for one bounded long workflow.

This implements SEP-2663 through the Python SDK's public ``Extension`` API.
It intentionally does not reuse the incompatible 2025-11-25 task types.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver import require_client_extension
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolRequestParams, CallToolResult, RequestParams, TextContent
from pydantic import ConfigDict, Field

TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
MODERN_PROTOCOL_VERSION = "2026-07-28"
TASK_NOT_FOUND = -32602
TASK_AUTHORIZATION_DENIED = -32001
TASK_EXPIRED = -32602
DEFAULT_TASK_TTL_MS = 15 * 60 * 1000
DEFAULT_POLL_INTERVAL_MS = 500
DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_MAX_TASK_ROWS = 10_000
DEFAULT_MAX_DATABASE_BYTES = 64 * 1024 * 1024
DEFAULT_TOMBSTONE_TTL_SECONDS = 15 * 60
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TaskIdParams(RequestParams):
    """Parameters shared by task status and cancellation requests."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    task_id: str = Field(alias="taskId", min_length=16, max_length=160)


class UpdateTaskParams(TaskIdParams):
    """Responses to outstanding task input requests (none are emitted today)."""

    input_responses: dict[str, Any] = Field(alias="inputResponses", default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    """Verified task owner and execution identity captured outside model arguments."""

    owner_principal: str
    authority_principal: str
    resource_owner_principal: str
    campaign_id: str
    room_turn_id: str
    base_revision: int
    auth_meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    status: str
    tool_name: str
    arguments: dict[str, Any]
    request_hash: str
    owner_principal: str
    authority_principal: str
    resource_owner_principal: str
    campaign_id: str
    room_turn_id: str
    base_revision: int
    idempotency_key: str
    created_at: float
    updated_at: float
    ttl_ms: int
    poll_interval_ms: int
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    lease_token: str | None
    lease_expires_at: float | None
    cancel_requested: bool

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_ms / 1000


TaskAuthorizer = Callable[[ServerRequestContext[Any, Any], str, TaskRecord | None], TaskIdentity]
TaskCreator = Callable[[ServerRequestContext[Any, Any], CallToolRequestParams], TaskIdentity]
TaskExecutor = Callable[
    [TaskRecord, ServerRequestContext[Any, Any], TaskIdentity],
    Awaitable[CallToolResult | dict[str, Any]],
]


class TaskExpiredError(LookupError):
    """Raised after a task TTL elapses and its result has been destroyed."""


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DurableTaskStore:
    """Small SQLite task store independent from campaign authority tables."""

    def __init__(
        self,
        path: Path,
        *,
        max_rows: int = DEFAULT_MAX_TASK_ROWS,
        max_database_bytes: int = DEFAULT_MAX_DATABASE_BYTES,
        tombstone_ttl_seconds: int = DEFAULT_TOMBSTONE_TTL_SECONDS,
    ) -> None:
        self.path = path.expanduser().resolve(strict=False)
        self.max_rows = max(1, int(max_rows))
        self.max_database_bytes = max(1024 * 1024, int(max_database_bytes))
        self.tombstone_ttl_seconds = max(60, int(tombstone_ttl_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _prepare(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    authority_principal TEXT NOT NULL,
                    resource_owner_principal TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    room_turn_id TEXT NOT NULL,
                    base_revision INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    ttl_ms INTEGER NOT NULL,
                    poll_interval_ms INTEGER NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    lease_token TEXT,
                    lease_expires_at REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(owner_principal, campaign_id, tool_name, idempotency_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_mcp_tasks_recovery
                ON mcp_tasks(status, lease_expires_at, updated_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_task_tombstones (
                    task_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            self._maintain(connection, now=time.time())

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            status=str(row["status"]),
            tool_name=str(row["tool_name"]),
            arguments=json.loads(str(row["arguments_json"])),
            request_hash=str(row["request_hash"]),
            owner_principal=str(row["owner_principal"]),
            authority_principal=str(row["authority_principal"]),
            resource_owner_principal=str(row["resource_owner_principal"]),
            campaign_id=str(row["campaign_id"]),
            room_turn_id=str(row["room_turn_id"]),
            base_revision=int(row["base_revision"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ttl_ms=int(row["ttl_ms"]),
            poll_interval_ms=int(row["poll_interval_ms"]),
            result=json.loads(str(row["result_json"])) if row["result_json"] else None,
            error=json.loads(str(row["error_json"])) if row["error_json"] else None,
            lease_token=str(row["lease_token"]) if row["lease_token"] else None,
            lease_expires_at=(
                float(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            cancel_requested=bool(row["cancel_requested"]),
        )

    def _database_size(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
            if candidate.exists()
        )

    def _expire_task(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        *,
        now: float,
        reason: str = "expired",
    ) -> None:
        connection.execute("DELETE FROM mcp_tasks WHERE task_id = ?", (task_id,))
        connection.execute(
            """
            INSERT INTO mcp_task_tombstones(task_id, reason, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                reason=excluded.reason, expires_at=excluded.expires_at
            """,
            (task_id, reason, now + self.tombstone_ttl_seconds),
        )

    def _maintain(self, connection: sqlite3.Connection, *, now: float) -> None:
        connection.execute("DELETE FROM mcp_task_tombstones WHERE expires_at <= ?", (now,))
        expired = connection.execute(
            "SELECT task_id FROM mcp_tasks WHERE created_at + (ttl_ms / 1000.0) <= ?",
            (now,),
        ).fetchall()
        for row in expired:
            self._expire_task(connection, str(row["task_id"]), now=now)
        count = int(connection.execute("SELECT COUNT(*) FROM mcp_tasks").fetchone()[0])
        excess = max(0, count - self.max_rows)
        if excess:
            evictable = connection.execute(
                """
                SELECT task_id FROM mcp_tasks
                WHERE status IN ('completed', 'failed', 'cancelled')
                ORDER BY updated_at ASC LIMIT ?
                """,
                (excess,),
            ).fetchall()
            for row in evictable:
                self._expire_task(connection, str(row["task_id"]), now=now, reason="evicted")
        if self._database_size() > self.max_database_bytes:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if self._database_size() > self.max_database_bytes:
            # Byte pressure evicts only terminal state, oldest first. Active
            # work is never silently discarded; a new create is rejected if
            # active rows alone consume the configured budget.
            evictable = connection.execute(
                """
                SELECT task_id FROM mcp_tasks
                WHERE status IN ('completed', 'failed', 'cancelled')
                ORDER BY updated_at ASC
                """
            ).fetchall()
            for row in evictable:
                self._expire_task(connection, str(row["task_id"]), now=now, reason="evicted")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
                if self._database_size() <= self.max_database_bytes:
                    break
        tombstone_count = int(
            connection.execute("SELECT COUNT(*) FROM mcp_task_tombstones").fetchone()[0]
        )
        if tombstone_count > self.max_rows:
            connection.execute(
                """
                DELETE FROM mcp_task_tombstones WHERE task_id IN (
                    SELECT task_id FROM mcp_task_tombstones
                    ORDER BY expires_at ASC LIMIT ?
                )
                """,
                (tombstone_count - self.max_rows,),
            )

    def maintain(self, *, now: float | None = None) -> None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._maintain(connection, now=instant)

    def stats(self) -> dict[str, int]:
        """Return bounded operational counts without identity-bearing labels."""

        with self._lock, self._connect() as connection:
            return {
                "tasks": int(connection.execute("SELECT COUNT(*) FROM mcp_tasks").fetchone()[0]),
                "tombstones": int(
                    connection.execute("SELECT COUNT(*) FROM mcp_task_tombstones").fetchone()[0]
                ),
                "bytes": self._database_size(),
            }

    def get(self, task_id: str, *, now: float | None = None) -> TaskRecord | None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            record = self._record(row)
            if instant >= record.expires_at:
                self._expire_task(connection, task_id, now=instant)
                raise TaskExpiredError(task_id)
            return record

    def tombstone_reason(self, task_id: str, *, now: float | None = None) -> str | None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._maintain(connection, now=instant)
            row = connection.execute(
                "SELECT reason FROM mcp_task_tombstones WHERE task_id = ?", (task_id,)
            ).fetchone()
            return str(row["reason"]) if row is not None else None

    def create_or_get(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request_hash: str,
        identity: TaskIdentity,
        idempotency_key: str,
        ttl_ms: int,
        poll_interval_ms: int,
        now: float | None = None,
    ) -> tuple[TaskRecord, bool]:
        instant = time.time() if now is None else float(now)
        task_id = f"task_{uuid4().hex}"
        values = (
            task_id,
            "working",
            tool_name,
            _canonical_json(arguments),
            request_hash,
            identity.owner_principal,
            identity.authority_principal,
            identity.resource_owner_principal,
            identity.campaign_id,
            identity.room_turn_id,
            identity.base_revision,
            idempotency_key,
            instant,
            instant,
            ttl_ms,
            poll_interval_ms,
        )
        with self._lock, self._connect() as connection:
            self._maintain(connection, now=instant)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO mcp_tasks (
                        task_id, status, tool_name, arguments_json,
                        request_hash, owner_principal, authority_principal,
                        resource_owner_principal, campaign_id, room_turn_id,
                        base_revision, idempotency_key,
                        created_at, updated_at, ttl_ms, poll_interval_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                connection.execute("COMMIT")
                created = True
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                created = False
            row = connection.execute(
                """
                SELECT * FROM mcp_tasks
                WHERE owner_principal = ? AND campaign_id = ?
                  AND tool_name = ? AND idempotency_key = ?
                """,
                (
                    identity.owner_principal,
                    identity.campaign_id,
                    tool_name,
                    idempotency_key,
                ),
            ).fetchone()
            assert row is not None
            record = self._record(row)
            if record.request_hash != request_hash:
                raise ValueError("idempotency_key was already used with different task arguments")
            expected_binding = (
                identity.authority_principal,
                identity.resource_owner_principal,
                identity.room_turn_id,
                identity.base_revision,
            )
            actual_binding = (
                record.authority_principal,
                record.resource_owner_principal,
                record.room_turn_id,
                record.base_revision,
            )
            if actual_binding != expected_binding:
                raise ValueError(
                    "idempotency_key was already used with a different trusted identity binding"
                )
            self._maintain(connection, now=instant)
            row_count = int(connection.execute("SELECT COUNT(*) FROM mcp_tasks").fetchone()[0])
            if created and (
                row_count > self.max_rows or self._database_size() > self.max_database_bytes
            ):
                connection.execute("DELETE FROM mcp_tasks WHERE task_id = ?", (task_id,))
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
                raise RuntimeError("durable MCP task store is at capacity")
            return record, created

    def claim(
        self,
        task_id: str,
        lease_token: str,
        *,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            result = connection.execute(
                """
                UPDATE mcp_tasks
                SET lease_token = ?, lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND cancel_requested = 0
                  AND (lease_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                (lease_token, instant + lease_seconds, instant, task_id, instant),
            )
            return result.rowcount == 1

    def heartbeat(
        self,
        task_id: str,
        lease_token: str,
        *,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            result = connection.execute(
                """
                UPDATE mcp_tasks
                SET lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND lease_token = ?
                """,
                (instant + lease_seconds, instant, task_id, lease_token),
            )
            return result.rowcount == 1

    def release(self, task_id: str, lease_token: str, *, now: float | None = None) -> None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE mcp_tasks
                SET lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND lease_token = ?
                """,
                (instant, task_id, lease_token),
            )

    def complete(
        self,
        task_id: str,
        lease_token: str,
        result: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE mcp_tasks
                SET status = 'completed', result_json = ?, error_json = NULL,
                    lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND lease_token = ?
                """,
                (_canonical_json(result), instant, task_id, lease_token),
            )

    def fail(
        self,
        task_id: str,
        lease_token: str,
        error: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE mcp_tasks
                SET status = 'failed', error_json = ?, result_json = NULL,
                    lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND lease_token = ?
                """,
                (_canonical_json(error), instant, task_id, lease_token),
            )

    def cancel_claimed(
        self,
        task_id: str,
        lease_token: str,
        *,
        now: float | None = None,
    ) -> None:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE mcp_tasks
                SET status = 'cancelled', cancel_requested = 1,
                    lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'working' AND lease_token = ?
                """,
                (instant, task_id, lease_token),
            )

    def request_cancel(self, task_id: str, *, now: float | None = None) -> TaskRecord:
        instant = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            record = self._record(row)
            if record.status not in TERMINAL_STATUSES:
                no_live_lease = (
                    record.lease_token is None
                    or record.lease_expires_at is None
                    or record.lease_expires_at <= instant
                )
                connection.execute(
                    """
                    UPDATE mcp_tasks
                    SET cancel_requested = 1,
                        status = CASE WHEN ? THEN 'cancelled' ELSE status END,
                        lease_token = CASE WHEN ? THEN NULL ELSE lease_token END,
                        lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (no_live_lease, no_live_lease, no_live_lease, instant, task_id),
                )
            updated = connection.execute(
                "SELECT * FROM mcp_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def is_cancel_requested(self, task_id: str) -> bool:
        record = self.get(task_id)
        return record is None or record.cancel_requested or record.status == "cancelled"


class TasksExtension(Extension):
    """SEP-2663 Tasks for ``module_draft(action='start')`` only."""

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self,
        *,
        store: DurableTaskStore,
        authorize_create: TaskCreator,
        authorize_task: TaskAuthorizer,
        ttl_ms: int = DEFAULT_TASK_TTL_MS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self.store = store
        self.authorize_create = authorize_create
        self.authorize_task = authorize_task
        self.ttl_ms = ttl_ms
        self.poll_interval_ms = poll_interval_ms
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._executor: TaskExecutor | None = None
        self._workers: dict[str, asyncio.Task[None]] = {}

    def set_executor(self, executor: TaskExecutor) -> None:
        if self._executor is not None:
            raise RuntimeError("MCP task executor was already configured")
        self._executor = executor

    def methods(self) -> tuple[MethodBinding, ...]:
        modern = frozenset({MODERN_PROTOCOL_VERSION})
        return (
            MethodBinding("tasks/get", TaskIdParams, self._get, modern),
            MethodBinding("tasks/update", UpdateTaskParams, self._update, modern),
            MethodBinding("tasks/cancel", TaskIdParams, self._cancel, modern),
        )

    @staticmethod
    def _is_long_tool(params: CallToolRequestParams) -> bool:
        arguments = dict(params.arguments or {})
        return params.name == "module_draft" and arguments.get("action") == "start"

    @staticmethod
    def _tool_error(
        message: str,
        *,
        code: str = "invalid_request",
        retryable: bool = False,
        recovery: str = "Correct the request and retry with the same idempotency key.",
    ) -> CallToolResult:
        error = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "recovery": recovery,
        }
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=message)],
            structured_content={"error": error},
        )

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.protocol_version != MODERN_PROTOCOL_VERSION or not self._is_long_tool(params):
            return await call_next(ctx)
        capabilities = ctx.session.client_capabilities
        declared = capabilities.extensions if capabilities else None
        if not declared or self.identifier not in declared:
            # Modern clients that do not opt in retain the synchronous CallToolResult.
            return await call_next(ctx)
        arguments = dict(params.arguments or {})
        idempotency_key = str(arguments.get("idempotency_key") or "").strip()
        if not idempotency_key:
            return self._tool_error("idempotency_key is required for a durable module task")
        try:
            identity = self.authorize_create(ctx, params)
            # Authorization may replace model-authored identity fields. Hash
            # and persist only the canonical, trusted arguments afterward.
            arguments = dict(params.arguments or {})
            request_hash = hashlib.sha256(
                _canonical_json({"name": params.name, "arguments": arguments}).encode("utf-8")
            ).hexdigest()
            record, _created = self.store.create_or_get(
                tool_name=params.name,
                arguments=arguments,
                request_hash=request_hash,
                identity=identity,
                idempotency_key=idempotency_key,
                ttl_ms=self.ttl_ms,
                poll_interval_ms=self.poll_interval_ms,
            )
        except RuntimeError as exc:
            return self._tool_error(
                str(exc),
                code="task_store_capacity",
                retryable=True,
                recovery="Retry later with the same idempotency key after task cleanup.",
            )
        except ValueError as exc:
            code = "idempotency_conflict" if "idempotency_key" in str(exc) else "invalid_request"
            return self._tool_error(str(exc), code=code)
        self._schedule(record.task_id, ctx, identity)
        return self._task_view(record, create=True)

    def _task_view(self, record: TaskRecord, *, create: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resultType": "task" if create else "complete",
            "taskId": record.task_id,
            "status": record.status,
            "createdAt": _timestamp(record.created_at),
            "lastUpdatedAt": _timestamp(record.updated_at),
            "ttlMs": record.ttl_ms,
            "pollIntervalMs": record.poll_interval_ms,
        }
        if not create and record.status == "completed":
            result["result"] = record.result or {}
        elif not create and record.status == "failed":
            result["statusMessage"] = "The underlying MCP request failed at protocol level."
            result["error"] = record.error or {
                "code": -32603,
                "message": "MCP task failed without an error receipt",
            }
        return result

    @staticmethod
    def _validate_http_routing(ctx: ServerRequestContext[Any, Any], task_id: str) -> None:
        headers = getattr(ctx.request, "headers", None)
        if not isinstance(headers, Mapping):
            return
        method_header = headers.get("mcp-method") or headers.get("Mcp-Method")
        if method_header != ctx.method:
            raise MCPError(
                code=-32012,
                message="Mcp-Method header must match the task request method",
                data={"retryable": False, "recovery": "Send the exact JSON-RPC method name."},
            )
        name_header = headers.get("mcp-name") or headers.get("Mcp-Name")
        if name_header != task_id:
            raise MCPError(
                code=-32012,
                message="Mcp-Name header must match params.taskId for task requests",
                data={"retryable": False, "recovery": "Copy params.taskId into Mcp-Name."},
            )

    def _require_record(
        self,
        ctx: ServerRequestContext[Any, Any],
        operation: str,
        task_id: str,
    ) -> tuple[TaskRecord, TaskIdentity]:
        require_client_extension(ctx, self.identifier)
        self._validate_http_routing(ctx, task_id)
        try:
            record = self.store.get(task_id)
        except TaskExpiredError:
            raise MCPError(
                code=TASK_EXPIRED,
                message="MCP task expired and its stored result was destroyed",
                data={
                    "retryable": False,
                    "recovery": "Start a new module_draft task with a new idempotency key.",
                },
            ) from None
        if record is None:
            reason = self.store.tombstone_reason(task_id)
            if reason in {"expired", "evicted"}:
                raise MCPError(
                    code=TASK_EXPIRED,
                    message="MCP task expired and its stored result was destroyed",
                    data={
                        "retryable": False,
                        "recovery": "Start a new module_draft task with a new idempotency key.",
                    },
                )
            raise MCPError(
                code=TASK_NOT_FOUND,
                message="MCP task not found",
                data={"retryable": False, "recovery": "Verify the task ID with the Host."},
            )
        try:
            identity = self.authorize_task(ctx, operation, record)
        except (RuntimeError, ValueError) as exc:
            raise MCPError(
                code=TASK_AUTHORIZATION_DENIED,
                message="MCP task authorization denied",
                data={
                    "reason": str(exc),
                    "retryable": False,
                    "recovery": (
                        "Mint a fresh delegation for this task method with the original "
                        "requester, owner, Host, campaign, room turn, and revision."
                    ),
                },
            ) from None
        return record, identity

    async def _get(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: TaskIdParams,
    ) -> dict[str, Any]:
        record, identity = self._require_record(ctx, "tasks/get", params.task_id)
        if record.status == "working":
            self._schedule(record.task_id, ctx, identity)
            record = self.store.get(record.task_id) or record
        return self._task_view(record)

    async def _update(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: UpdateTaskParams,
    ) -> dict[str, Any]:
        self._require_record(ctx, "tasks/update", params.task_id)
        # This workflow has no input_required phase. SEP-2663 requires unknown
        # or already-satisfied response keys to be ignored.
        return {"resultType": "complete"}

    async def _cancel(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: TaskIdParams,
    ) -> dict[str, Any]:
        self._require_record(ctx, "tasks/cancel", params.task_id)
        self.store.request_cancel(params.task_id)
        return {"resultType": "complete"}

    def _schedule(
        self,
        task_id: str,
        ctx: ServerRequestContext[Any, Any],
        identity: TaskIdentity,
    ) -> None:
        existing = self._workers.get(task_id)
        if existing is not None and not existing.done():
            return
        worker = asyncio.create_task(
            self._run(task_id, ctx, identity), name=f"mcp-task:{task_id}"
        )
        self._workers[task_id] = worker
        worker.add_done_callback(lambda _task: self._workers.pop(task_id, None))

    async def _heartbeat(self, task_id: str, lease_token: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            if not self.store.heartbeat(
                task_id,
                lease_token,
                lease_seconds=self.lease_seconds,
            ):
                return

    async def _run(
        self,
        task_id: str,
        ctx: ServerRequestContext[Any, Any],
        identity: TaskIdentity,
    ) -> None:
        executor = self._executor
        if executor is None:
            return
        lease_token = uuid4().hex
        if not self.store.claim(
            task_id,
            lease_token,
            lease_seconds=self.lease_seconds,
        ):
            return
        heartbeat = asyncio.create_task(
            self._heartbeat(task_id, lease_token),
            name=f"mcp-task-heartbeat:{task_id}",
        )
        try:
            record = self.store.get(task_id)
            if record is None or self.store.is_cancel_requested(task_id):
                self.store.request_cancel(task_id)
                return
            result = await executor(record, ctx, identity)
            if isinstance(result, CallToolResult):
                payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
            elif isinstance(result, dict):
                payload = dict(result)
            else:  # pragma: no cover - the executor contract prevents this
                raise TypeError("MCP task executor returned an unsupported result")
            if self.store.is_cancel_requested(task_id):
                self.store.cancel_claimed(task_id, lease_token)
            else:
                self.store.complete(task_id, lease_token, payload)
        except asyncio.CancelledError:
            self.store.release(task_id, lease_token)
            raise
        except MCPError as exc:
            self.store.fail(
                task_id,
                lease_token,
                {"code": exc.error.code, "message": exc.error.message, "data": exc.error.data},
            )
        except Exception:
            self.store.fail(
                task_id,
                lease_token,
                {
                    "code": -32603,
                    "message": "MCP task execution failed",
                    "data": {
                        "retryable": False,
                        "recovery": "Inspect server logs, then start a new task if appropriate.",
                    },
                },
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


__all__ = [
    "DEFAULT_TASK_TTL_MS",
    "DurableTaskStore",
    "MODERN_PROTOCOL_VERSION",
    "TASKS_EXTENSION_ID",
    "TaskExpiredError",
    "TaskIdentity",
    "TaskIdParams",
    "TaskRecord",
    "TasksExtension",
    "UpdateTaskParams",
]
