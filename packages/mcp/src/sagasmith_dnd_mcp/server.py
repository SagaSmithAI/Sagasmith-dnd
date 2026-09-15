"""MCP surface for the SagaSmith D&D runtime and bundled skill packs."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys as _sys
import types as _types
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from functools import wraps
from typing import Annotated, Any, Literal, Mapping
from uuid import uuid4
from weakref import WeakValueDictionary

import sagasmith_dnd_runtime.application as _application
from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    ToolAnnotations,
)
from pydantic import Field
from sagasmith_core.access import (
    LOCAL_SYSTEM_PRINCIPAL_ID,
)
from sagasmith_core.auth_context import (
    AUTH_CONTEXT_DELEGATION_SCHEMA,
    AUTH_CONTEXT_META_KEY,
    AUTH_CONTEXT_RECEIPT_META_KEY,
    AuthContext,
    AuthContextNonceGuard,
    verify_auth_context,
)
from sagasmith_dnd.random_stream import (
    CampaignRandomStream,
)
from sagasmith_dnd_runtime.application import (
    _auth_receipt_revision,
    _bounded_page,
    _create_application,
    _preload_optional_pdf_runtime,
    _ServerResourceStack,
    _validate_contract_arguments,
)
from sagasmith_dnd_runtime.operations import (
    Image as RuntimeImage,
)
from sagasmith_dnd_runtime.operations import (
    OperationError,
)
from sagasmith_dnd_runtime.operations import (
    RenderResult as RuntimeRenderResult,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.exposure import Exposure, ExposureError, ExposureRegistry
from sagasmith_dnd_mcp.mcp_tasks import (
    DurableTaskStore,
    TaskIdentity,
    TaskRecord,
    TasksExtension,
)
from sagasmith_dnd_mcp.tool_profiles import (
    CORE_TOOLS,
    HOST_PRIVATE_TOOLS,
    PROFILE_LOBBY,
    policy_for_tool,
)


def __getattr__(name):
    return getattr(_application, name)


class _CompatibilityModule(_types.ModuleType):
    def __setattr__(self, name, value):
        if hasattr(_application, name) and name not in {"create_server", "main", "close_server"}:
            setattr(_application, name, value)
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _CompatibilityModule


def _attach_auth_receipt(result: Any, context: AuthContext | None, tool: str) -> Any:
    if context is None:
        return result
    if isinstance(result, CallToolResult):
        content, structured = result.content, result.structured_content
    elif isinstance(result, tuple) and len(result) == 2:
        content, structured = result
    else:
        return result
    receipt = context.audit_receipt(tool=tool, revision=_auth_receipt_revision(structured))
    updated = []
    attached = False
    for item in content:
        if not attached and isinstance(item, TextContent):
            metadata = dict(item.meta or {})
            metadata[AUTH_CONTEXT_RECEIPT_META_KEY] = receipt
            updated.append(item.model_copy(update={"meta": metadata}))
            attached = True
        else:
            updated.append(item)
    if isinstance(result, CallToolResult):
        return result.model_copy(update={"content": updated})
    return updated, structured


def _safe_tool_error_message(exc: ToolError) -> str:
    """Expose only bounded, caller-repairable causes from SDK wrappers."""

    cause = exc.__cause__
    if isinstance(exc, UnexpectedToolError) and isinstance(
        cause, (ValueError, LookupError, PermissionError)
    ):
        return str(cause)[:2000]
    return str(exc)[:2000]


class RequestScopedMCPServer(MCPServer):
    """Dual-era MCP server with request-scoped identity and a stable catalog.

    The SDK owns protocol negotiation: v2 serves legacy initialize/session clients
    and 2026-07-28 server/discover clients from the same stdio/HTTP handlers.  The
    application never treats a transport session as an authorization boundary.
    """

    def __init__(
        self,
        *args: Any,
        exposure_registry: ExposureRegistry,
        phase_lookup: Any,
        allowed_tools_lookup: Any,
        scope_validator: Any,
        tool_policy_authorizer: Any,
        random_context_factory: Any,
        context_binding_factory: Any,
        authorization_fingerprint_lookup: Any,
        bound_principal_id: str | None = None,
        auth_context_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.exposure_registry = exposure_registry
        self._phase_lookup = phase_lookup
        self._allowed_tools_lookup = allowed_tools_lookup
        self._scope_validator = scope_validator
        self._tool_policy_authorizer = tool_policy_authorizer
        self._random_context_factory = random_context_factory
        self._context_binding_factory = context_binding_factory
        self._authorization_fingerprint_lookup = authorization_fingerprint_lookup
        self._bound_principal_id = bound_principal_id.strip() if bound_principal_id else None
        self._auth_context_secret = auth_context_secret
        self._auth_context_nonces = AuthContextNonceGuard() if auth_context_secret else None
        self._exposure_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._sessions: WeakValueDictionary[str, Any] = WeakValueDictionary()
        self._metric_counts: Counter[tuple[str, str, str, str]] = Counter()
        super().__init__(*args, **kwargs)
        original_initialization_options = self._lowlevel_server.create_initialization_options

        def initialization_options(
            notification_options: NotificationOptions | None = None,
            experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        ):
            return original_initialization_options(
                notification_options
                or NotificationOptions(
                    tools_changed=True,
                    prompts_changed=False,
                    resources_changed=False,
                ),
                experimental_capabilities,
            )

        self._lowlevel_server.create_initialization_options = initialization_options  # type: ignore[method-assign]

    def _request_session(self, context: Context | None = None) -> tuple[str, Any] | None:
        """Return a legacy compatibility key, never an authority identity."""

        if context is None:
            return None
        try:
            session = context.session
        except (LookupError, ValueError):
            return None
        connection = getattr(session, "_connection", None)
        transport_session_id = getattr(connection, "session_id", None)
        key = transport_session_id or f"legacy:{id(connection)}"
        self._sessions[key] = session
        return key, session

    def _exposure_lock(self, exposure_id: str) -> asyncio.Lock:
        return self._exposure_locks.setdefault(exposure_id, asyncio.Lock())

    @staticmethod
    def _attach_random_receipt(result: Any, receipt: dict[str, Any] | None) -> Any:
        if receipt is None:
            return result
        if isinstance(result, CallToolResult):
            content, structured = result.content, result.structured_content
        elif isinstance(result, tuple) and len(result) == 2:
            content, structured = result
        else:
            return result

        def attach(value: Any) -> Any:
            if not isinstance(value, dict):
                return value
            updated = deepcopy(value)
            payload = updated.get("result")
            # Atomic response builders already retain the receipt at their
            # public result level. Adding another copy only on the first call
            # would diverge from the persisted, draw-free idempotent replay.
            if "random_stream_receipt" in updated or (
                isinstance(payload, dict) and "random_stream_receipt" in payload
            ):
                return updated
            if isinstance(payload, dict):
                payload["random_stream_receipt"] = deepcopy(receipt)
            else:
                updated["random_stream_receipt"] = deepcopy(receipt)
            return updated

        updated_content = []
        for item in content:
            if not isinstance(item, TextContent):
                updated_content.append(item)
                continue
            try:
                decoded = json.loads(item.text)
            except json.JSONDecodeError:
                updated_content.append(item)
                continue
            attached = attach(decoded)
            if attached == decoded:
                updated_content.append(item)
                continue
            updated_content.append(
                item.model_copy(
                    update={
                        "text": json.dumps(
                            attached,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    }
                )
            )
        updated_structured = attach(structured)
        if isinstance(result, CallToolResult):
            return result.model_copy(
                update={
                    "content": updated_content,
                    "structured_content": updated_structured,
                }
            )
        return updated_content, updated_structured

    @staticmethod
    def _canonicalize_structured_text(result: Any) -> Any:
        """Render the JSON mirror independently of persisted dictionary order."""
        if isinstance(result, CallToolResult):
            content, structured = result.content, result.structured_content
        elif isinstance(result, tuple) and len(result) == 2:
            content, structured = result
        else:
            return result
        if not isinstance(structured, dict):
            return result
        updated = []
        for item in content:
            if isinstance(item, TextContent):
                try:
                    decoded = json.loads(item.text)
                except json.JSONDecodeError:
                    decoded = None
                # Only normalize the structured JSON mirror, never narrative
                # text, images, resource blocks, or unrelated JSON content.
                if decoded == structured:
                    item = item.model_copy(
                        update={
                            "text": json.dumps(
                                structured,
                                ensure_ascii=False,
                                sort_keys=True,
                                indent=2,
                            ),
                        }
                    )
            updated.append(item)
        if isinstance(result, CallToolResult):
            return result.model_copy(update={"content": updated})
        return updated, structured

    @staticmethod
    def _ensure_text_fallback(result: Any) -> Any:
        """Keep legacy clients useful when a structured result is empty/list-shaped."""

        if not isinstance(result, CallToolResult) or result.content:
            return result
        if result.structured_content is None:
            return result
        return result.model_copy(
            update={
                "content": [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            result.structured_content,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                ]
            }
        )

    @staticmethod
    def _structured_tool_error(message: str, cause: BaseException | None = None) -> CallToolResult:
        visited: set[int] = set()
        while cause is not None and id(cause) not in visited:
            visited.add(id(cause))
            if isinstance(cause, OperationError) and cause.has_explicit_contract:
                return CallToolResult(
                    is_error=True,
                    content=[TextContent(type="text", text=str(cause))],
                    structured_content={"error": deepcopy(cause.envelope)},
                )
            cause = cause.__cause__
        text = message.strip() or "The tool request was rejected."
        lowered = text.casefold()
        retryable = any(
            marker in lowered for marker in ("stale", "expired", "timeout", "temporar", "conflict")
        )
        code = (
            "stale_revision"
            if "stale" in lowered and "revision" in lowered
            else "expired_handle"
            if "expired" in lowered and ("handle" in lowered or "exposure" in lowered)
            else "authorization_denied"
            if any(marker in lowered for marker in ("auth", "principal", "permission", "access"))
            else "invalid_request"
        )
        error = {
            "code": code,
            "message": text,
            "retryable": retryable,
            "recovery": (
                "Refresh the authoritative revision or handle and retry with "
                "the same idempotency key."
                if retryable
                else "Correct the request or obtain a new audience-bound "
                "delegation before retrying."
            ),
        }
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=text)],
            structured_content={"error": error},
        )

    @staticmethod
    def _attach_host_context_binding(
        result: Any,
        binding: dict[str, Any] | None,
    ) -> Any:
        """Attach one authoritative binding to both MCP result representations."""

        if binding is None:
            return result
        if isinstance(result, CallToolResult):
            content, structured = result.content, result.structured_content
        elif isinstance(result, tuple) and len(result) == 2:
            content, structured = result
        else:
            return result

        def attach(value: Any) -> Any:
            if not isinstance(value, dict):
                return value
            updated = deepcopy(value)
            payload = updated.get("result")
            if isinstance(payload, dict):
                payload["host_context_binding"] = deepcopy(binding)
            else:
                updated["host_context_binding"] = deepcopy(binding)
            return updated

        updated_content = []
        for item in content:
            text_value = getattr(item, "text", None)
            if not isinstance(text_value, str):
                updated_content.append(item)
                continue
            try:
                decoded = json.loads(text_value)
            except json.JSONDecodeError:
                updated_content.append(item)
                continue
            updated_content.append(
                item.model_copy(
                    update={
                        "text": json.dumps(
                            attach(decoded),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    }
                )
            )
        updated_structured = attach(structured)
        if isinstance(result, CallToolResult):
            return result.model_copy(
                update={
                    "content": updated_content,
                    "structured_content": updated_structured,
                }
            )
        return updated_content, updated_structured

    @staticmethod
    def _result_campaign_id(
        name: str,
        result: Any,
        arguments: dict[str, Any] | None = None,
    ) -> str | None:
        argument_values = dict(arguments or {})
        campaign_id = argument_values.get("campaign_id")
        nested_arguments = argument_values.get("payload")
        if not campaign_id and isinstance(nested_arguments, dict):
            campaign_id = nested_arguments.get("campaign_id")
        if campaign_id:
            return str(campaign_id).strip() or None
        if isinstance(result, CallToolResult):
            structured = result.structured_content
        elif isinstance(result, tuple) and len(result) == 2:
            structured = result[1]
        else:
            return None
        if not isinstance(structured, dict):
            return None
        payload = structured.get("result", structured)
        if not isinstance(payload, dict):
            return None
        campaign_id = payload.get("campaign_id")
        if not campaign_id and name in {"campaign_create", "campaign_query"}:
            campaign_id = payload.get("id")
        value = str(campaign_id or "").strip()
        return value or None

    @staticmethod
    def _finalize_random_stream(
        stream: CampaignRandomStream | None,
    ) -> dict[str, Any] | None:
        if stream is None or stream.draw_count == 0:
            return None
        if stream.has_unpersisted_draws:
            raise RuntimeError(
                f"Tool {stream.operation!r} consumed campaign randomness without "
                "atomically persisting the random-stream position."
            )
        return stream.receipt()

    def _principal_argument(self, tool_id: str) -> str | None:
        tool = self._tool_manager.get_tool(tool_id)
        properties = dict((tool.parameters if tool else {}).get("properties") or {})
        for name in ("auth_principal_id", "by_principal_id", "principal_id"):
            if name in properties:
                return name
        return None

    def _bind_exposure_principal(
        self,
        exposure: Exposure,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        inject_missing: bool,
    ) -> dict[str, Any]:
        """Keep an exposure bound to the principal that opened it.

        Access-management facades use ``principal_id`` for the target and
        ``by_principal_id`` for their authenticated writer.
        """
        result = dict(arguments)
        principal_argument = self._principal_argument(tool_id)
        if principal_argument is None:
            return result
        if (
            tool_id == "exposure"
            and result.get("action") == "open"
            and self._auth_context_secret is not None
            and self._bound_principal_id is None
        ):
            return result
        supplied = result.get(principal_argument)
        if supplied is not None and supplied != exposure.principal_id:
            raise ExposureError(
                "Tool principal_id does not match the principal that opened this session exposure."
            )
        if inject_missing:
            result[principal_argument] = exposure.principal_id
        return result

    def _bind_configured_principal(
        self,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace model-authored identity when this process is principal-bound."""

        result = dict(arguments)
        if self._bound_principal_id is None:
            return result
        principal_argument = self._principal_argument(tool_id)
        if principal_argument is not None:
            result[principal_argument] = self._bound_principal_id
        return result

    @staticmethod
    def _argument_campaign_id(arguments: dict[str, Any]) -> str:
        campaign_id = str(arguments.get("campaign_id") or "").strip()
        if campaign_id:
            return campaign_id
        for key in ("payload", "data"):
            nested = arguments.get(key)
            if isinstance(nested, dict) and (value := str(nested.get("campaign_id") or "").strip()):
                return value
        return ""

    def _verify_request_auth_context(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        context: Context | None,
        exposure: Exposure | None,
    ) -> AuthContext | None:
        principal_argument = self._principal_argument(name)
        if self._auth_context_secret is None:
            return None
        supplied_principal = (
            str(arguments.get(principal_argument) or "").strip()
            if principal_argument is not None
            else ""
        )
        try:
            if context is None:
                raise ValueError("signed auth context requires an MCP request context")
            metadata = context.request_context.meta
            envelope = (
                metadata.get(AUTH_CONTEXT_META_KEY)
                if isinstance(metadata, Mapping)
                else getattr(metadata, AUTH_CONTEXT_META_KEY, None)
            )
            verified = verify_auth_context(envelope, self._auth_context_secret)
            is_modern_request = context.protocol_version == "2026-07-28"
            if is_modern_request and verified.schema != AUTH_CONTEXT_DELEGATION_SCHEMA:
                raise ValueError("delegated auth context v2 is required on the 2026-07-28 path")
            is_delegated = verified.schema == AUTH_CONTEXT_DELEGATION_SCHEMA
            if (
                not is_delegated
                and supplied_principal
                and supplied_principal != verified.actor_principal
            ):
                raise ValueError(f"{principal_argument} does not match the signed actor_principal")
            authorization_principal = (
                verified.authorization_principal if is_delegated else verified.actor_principal
            )
            authority_principal = (
                verified.authority_principal if is_delegated else verified.actor_principal
            )
            # Authorization follows the requesting player while audit authority
            # follows the acting Host. Model-authored identity never selects either.
            if principal_argument is not None:
                arguments[principal_argument] = authorization_principal
            expected_campaign = self._argument_campaign_id(arguments)
            if (
                not expected_campaign
                and exposure is not None
                and not (name == "exposure" and arguments.get("action") == "open")
            ):
                expected_campaign = exposure.campaign_id or ""
            expected_revision = arguments.get("expected_revision", arguments.get("base_revision"))
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                expected_revision = None
            expected_resource_owner = (
                str(arguments.get("resource_owner_principal") or "").strip() or None
            )
            expected_acting_character = (
                str(arguments.get("acting_character_id") or "").strip() or None
            )
            context = verify_auth_context(
                envelope,
                self._auth_context_secret,
                expected_actor=authority_principal,
                expected_requester=authorization_principal if is_delegated else None,
                expected_campaign=expected_campaign or None,
                expected_service="sagasmith-dnd-mcp" if is_modern_request else None,
                expected_operation=name if is_modern_request else None,
                expected_audience="sagasmith-dnd-mcp" if is_modern_request else None,
                expected_room_turn=verified.room_turn_id if is_modern_request else None,
                expected_base_revision=expected_revision if is_modern_request else None,
                expected_resource_owner=(expected_resource_owner if is_modern_request else None),
                expected_acting_character=(
                    expected_acting_character if is_modern_request else None
                ),
            )
        except ValueError as exc:
            raise ExposureError(str(exc)) from exc
        if context.schema != AUTH_CONTEXT_DELEGATION_SCHEMA:
            expected_epoch = (
                exposure.revision
                if exposure is not None
                and not (name == "exposure" and arguments.get("action") == "open")
                else 0
            )
            if context.authorization_epoch != expected_epoch:
                raise ExposureError("auth context authorization_epoch is stale")
        assert self._auth_context_nonces is not None
        try:
            self._auth_context_nonces.remember(context)
        except (RuntimeError, ValueError) as exc:
            raise ExposureError(str(exc)) from exc
        return context

    async def _refresh(self, session_key: str, campaign_id: str | None = None) -> bool:
        changed_session_keys: set[str] = set()
        exposure_items = (
            [(session_key, self.exposure_registry.active(session_key))]
            if campaign_id is None
            else list(self.exposure_registry.active_items(campaign_id))
        )
        for key, exposure in exposure_items:
            if exposure is None or exposure.campaign_id is None:
                continue
            phase = self._phase_lookup(exposure.campaign_id)
            if self.exposure_registry.refresh_phase(
                exposure,
                phase,
                allowed_tools=self._allowed_tools_lookup(exposure, phase),
            ):
                changed_session_keys.add(key)
            fingerprint = self._authorization_fingerprint_lookup(
                exposure.campaign_id, exposure.principal_id
            )
            if self.exposure_registry.refresh_authorization(exposure, fingerprint):
                changed_session_keys.add(key)
        for key in changed_session_keys:
            session = self._sessions.get(key)
            if session is not None:
                await session.send_tool_list_changed()
        return bool(changed_session_keys)

    async def list_tools(self):  # type: ignore[override]
        """Return one deterministic catalog; Host-side selection is not MCP state."""

        public_tools = (
            tool for tool in await super().list_tools() if tool.name not in HOST_PRIVATE_TOOLS
        )
        return sorted(public_tools, key=lambda tool: tool.name)

    async def _handle_list_tools(
        self,
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        """Keep the legacy adapter while making modern catalogs stateless."""

        tools = await self.list_tools()
        era = "modern" if ctx.protocol_version == "2026-07-28" else "legacy"
        self._metric_counts[("catalog", era, "tools/list", "success")] += 1
        if ctx.protocol_version != "2026-07-28":
            context = Context(
                request_context=ctx, mcp_server=self, subscriptions=self._subscriptions
            )
            request = self._request_session(context)
            if request is not None:
                session_key, _session = request
                await self._refresh(session_key)
                visible = self.exposure_registry.visible_tools(
                    self.exposure_registry.active(session_key)
                )
                tools = [tool for tool in tools if tool.name in visible]
        return ListToolsResult(tools=tools)

    async def _handle_call_tool(
        self,
        ctx: ServerRequestContext,
        params: CallToolRequestParams,
    ):
        """Attach bounded telemetry and standard trace context at the transport boundary."""

        result = await super()._handle_call_tool(ctx, params)
        if (
            isinstance(result, CallToolResult)
            and result.is_error
            and result.structured_content is None
        ):
            message = next(
                (
                    item.text
                    for item in result.content
                    if isinstance(item, TextContent) and item.text.strip()
                ),
                "The tool request was rejected.",
            )
            structured = self._structured_tool_error(message)
            result = result.model_copy(update={"structured_content": structured.structured_content})
        era = "modern" if ctx.protocol_version == "2026-07-28" else "legacy"
        outcome = "error" if isinstance(result, CallToolResult) and result.is_error else "success"
        self._metric_counts[("tool", era, params.name, outcome)] += 1
        if not isinstance(result, CallToolResult):
            return result
        try:
            context = Context(
                request_context=ctx,
                mcp_server=self,
                input_params=params,
                subscriptions=self._subscriptions,
            )
            headers = context.headers
        except (AttributeError, LookupError, TypeError, ValueError):
            return result
        if not isinstance(headers, Mapping):
            return result
        propagated = {
            key: value
            for key in ("traceparent", "tracestate", "baggage")
            if isinstance((value := headers.get(key)), str) and 0 < len(value) <= 2048
        }
        if not propagated:
            return result
        metadata = dict(result.meta or {})
        metadata["sagasmith_trace_context"] = propagated
        return result.model_copy(update={"meta": metadata})

    def metrics_snapshot(self) -> list[dict[str, Any]]:
        """Return bounded protocol/tool counters to the embedding Host."""

        return [
            {
                "stage": stage,
                "protocol_era": era,
                "operation": operation,
                "outcome": outcome,
                "count": count,
            }
            for (stage, era, operation, outcome), count in sorted(self._metric_counts.items())
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ):  # type: ignore[override]
        """Execute one request with fresh identity/role/phase/revision checks."""

        arguments = dict(arguments or {})
        try:
            _validate_contract_arguments(arguments)
        except ValueError as exc:
            if context is None:
                raise ToolError(str(exc)) from exc
            return self._structured_tool_error(str(exc))
        # Preserve the historical in-process API used by domain tests and local
        # application code.  It has no MCP request metadata, so it cannot be a
        # modern authorization boundary and must not synthesize request-scoped
        # receipts after an idempotent result has already been persisted.
        if context is None and self._auth_context_secret is None:
            try:
                direct_result = await super().call_tool(name, arguments, context)
            except ToolError as exc:
                if isinstance(exc, UnexpectedToolError) and exc.__cause__ is not None:
                    raise ToolError(str(exc.__cause__)) from exc.__cause__
                raise
            if isinstance(direct_result, CallToolResult) and all(
                isinstance(item, TextContent) for item in direct_result.content
            ):
                return direct_result.content, direct_result.structured_content
            return direct_result
        arguments = self._bind_configured_principal(name, arguments)
        if name in HOST_PRIVATE_TOOLS:
            try:
                private_result = await super().call_tool(name, arguments, context)
            except ToolError as exc:
                if context is None:
                    if isinstance(exc, UnexpectedToolError) and exc.__cause__ is not None:
                        raise ToolError(str(exc.__cause__)) from exc.__cause__
                    raise
                return self._structured_tool_error(_safe_tool_error_message(exc))
            if (
                context is None
                and isinstance(private_result, CallToolResult)
                and all(isinstance(item, TextContent) for item in private_result.content)
            ):
                return private_result.content, private_result.structured_content
            return private_result
        legacy_request = (
            self._request_session(context)
            if context is not None and context.protocol_version != "2026-07-28"
            else None
        )
        legacy_session_key = legacy_request[0] if legacy_request else None
        if legacy_session_key is not None:
            await self._refresh(legacy_session_key)
        exposure = None
        if name == "exposure" and arguments.get("action") != "open":
            handle = str(arguments.get("exposure_handle") or "").strip()
            if handle:
                exposure = self.exposure_registry.get(handle)
            elif legacy_session_key is not None:
                exposure = self.exposure_registry.active(legacy_session_key)
        elif legacy_session_key is not None:
            exposure = self.exposure_registry.active(legacy_session_key)
        if legacy_session_key is not None:
            if name not in CORE_TOOLS and exposure is None:
                raise ExposureError(
                    "No active compatibility exposure. Call exposure(action='open')."
                )
            if exposure is not None:
                arguments = self._bind_exposure_principal(
                    exposure, name, arguments, inject_missing=True
                )
                if name != "exposure" or arguments.get("action") != "open":
                    self._scope_validator(exposure, name, arguments)
            if name not in CORE_TOOLS:
                assert exposure is not None
                self.exposure_registry.require_tool(exposure, name)
        try:
            auth_context = self._verify_request_auth_context(
                name=name,
                arguments=arguments,
                context=context,
                exposure=exposure,
            )
            if auth_context is not None and legacy_session_key is None:
                policy_campaign_id = self._argument_campaign_id(arguments) or None
                policy = policy_for_tool(name)
                if policy_campaign_id is None and policy is not None and policy.requires_campaign:
                    policy_campaign_id = auth_context.campaign_id
                self._tool_policy_authorizer(
                    name,
                    auth_context.authorization_principal,
                    policy_campaign_id,
                )
        except ExposureError as exc:
            if context is None:
                raise
            return self._structured_tool_error(str(exc))
        campaign_id = self._argument_campaign_id(arguments) or (
            exposure.campaign_id if exposure is not None else None
        )
        context_manager = (
            self._random_context_factory(campaign_id, name, arguments)
            if campaign_id and name not in CORE_TOOLS
            else nullcontext(None)
        )
        from sagasmith_dnd_runtime.operations import RequestIdentity

        trusted_principal = (
            auth_context.authorization_principal
            if auth_context is not None
            else exposure.principal_id
            if exposure is not None
            else self._bound_principal_id
        )
        application_scope = bool(trusted_principal and name in self.runtime.operations)
        if application_scope:
            context_manager = self.runtime.command_scope(
                name,
                arguments,
                context=RequestIdentity(str(trusted_principal), campaign_id),
            )
        try:
            with context_manager as scoped:
                if application_scope:
                    arguments, random_stream = scoped
                else:
                    random_stream = scoped
                result = await super().call_tool(name, arguments, context)
                random_receipt = self._finalize_random_stream(random_stream)
        except ToolError as exc:
            if context is None:
                if isinstance(exc, UnexpectedToolError) and exc.__cause__ is not None:
                    raise ToolError(str(exc.__cause__)) from exc.__cause__
                raise
            message = _safe_tool_error_message(exc)
            if message.startswith("Unknown tool") or "validation error" in message.casefold():
                raise
            return self._structured_tool_error(message, exc)
        result = self._ensure_text_fallback(result)
        result = self._attach_random_receipt(result, random_receipt)
        result = self._canonicalize_structured_text(result)
        if (
            legacy_request is not None
            and name == "exposure"
            and arguments.get("action") in {"open", "set"}
        ):
            await legacy_request[1].send_tool_list_changed()
        campaign_id = campaign_id or self._result_campaign_id(name, result, arguments)
        if campaign_id:
            if legacy_session_key is not None:
                await self._refresh(legacy_session_key, campaign_id)
            principal_argument = self._principal_argument(name)
            principal_id = str(
                arguments.get(principal_argument) if principal_argument else ""
            ).strip()
            principal_id = (
                principal_id
                or (exposure.principal_id if exposure is not None else "")
                or self._bound_principal_id
                or "system:local"
            )
            binding = self._context_binding_factory(
                campaign_id,
                principal_id,
                arguments,
            )
            if binding is not None:
                current_exposure = (
                    self.exposure_registry.active(legacy_session_key)
                    if legacy_session_key is not None
                    else None
                )
                binding["authorization_epoch"] = (
                    current_exposure.revision if current_exposure is not None else 0
                )
            result = self._attach_host_context_binding(result, binding)
        result = _attach_auth_receipt(result, auth_context, name)
        # Preserve the historical direct-Python testing API. Network requests
        # always supply Context and therefore always receive the SDK v2
        # CallToolResult expected by both protocol eras.
        if (
            context is None
            and isinstance(result, CallToolResult)
            and all(isinstance(item, TextContent) for item in result.content)
        ):
            return result.content, result.structured_content
        return result


# Transitional import alias for downstream tests and callers.  The behaviour is
# request scoped despite the historical name.
SessionExposureFastMCP = RequestScopedMCPServer


def close_server(server: MCPServer) -> None:
    """Release resources owned by a server created for direct Python use."""
    close = getattr(server, "_sagasmith_close", None)
    if callable(close):
        close()


def create_server(config: McpConfig | None = None) -> MCPServer:
    """Create one server and clean up every opened resource if initialization fails."""

    resources = _ServerResourceStack()
    try:
        server = _create_server(config, resources=resources)
        setattr(server, "_sagasmith_close", resources.close)
        return server
    except BaseException as error:
        resources.close_after_failure(error)
        raise


def _create_server(config, *, resources):
    application = _create_application(config, resources=resources)
    config = config or McpConfig.from_environment()
    exposures = application.ports["exposures"]
    authoritative_phase = application.ports["authoritative_phase"]
    allowed_tools_for_exposure = application.ports["allowed_tools_for_exposure"]
    validate_exposure_scope = application.ports["validate_exposure_scope"]
    authorize_tool_policy = application.ports["authorize_tool_policy"]
    campaign_random_context = application.ports["campaign_random_context"]
    authoritative_host_context_binding = application.ports["authoritative_host_context_binding"]
    access = application.ports["access"]
    task_store = resources.track(
        "durable MCP task store",
        DurableTaskStore(config.home / "mcp-tasks.sqlite3"),
    )
    task_create_nonces = (
        AuthContextNonceGuard(store=application.ports["auth_nonce_store"])
        if config.auth_context_secret
        else None
    )
    task_followup_nonces = (
        AuthContextNonceGuard(store=application.ports["auth_nonce_store"])
        if config.auth_context_secret
        else None
    )

    def task_request_meta(ctx: ServerRequestContext[Any, Any]) -> dict[str, Any]:
        metadata = ctx.meta
        if metadata is None:
            return {}
        if isinstance(metadata, Mapping):
            return deepcopy(dict(metadata))
        model_dump = getattr(metadata, "model_dump", None)
        if callable(model_dump):
            return deepcopy(model_dump(by_alias=True, exclude_none=True))
        return {}

    def task_envelope(ctx: ServerRequestContext[Any, Any]) -> Any:
        return task_request_meta(ctx).get(AUTH_CONTEXT_META_KEY)

    def local_task_identity(arguments: dict[str, Any]) -> TaskIdentity:
        principal = config.bound_principal_id or LOCAL_SYSTEM_PRINCIPAL_ID
        campaign_id = RequestScopedMCPServer._argument_campaign_id(arguments)
        revision = arguments.get("expected_revision", arguments.get("base_revision", 0))
        if isinstance(revision, bool) or not isinstance(revision, int):
            revision = 0
        arguments["principal_id"] = principal
        return TaskIdentity(
            owner_principal=principal,
            authority_principal=principal,
            resource_owner_principal=principal,
            campaign_id=campaign_id,
            room_turn_id=f"local:{campaign_id or 'unbound'}",
            base_revision=revision,
            auth_meta={},
        )

    def authorize_task_create(
        ctx: ServerRequestContext[Any, Any],
        params: CallToolRequestParams,
    ) -> TaskIdentity:
        arguments = dict(params.arguments or {})
        if config.auth_context_secret is None:
            identity = local_task_identity(arguments)
            params.arguments = arguments
            return identity
        envelope = task_envelope(ctx)
        verified = verify_auth_context(envelope, config.auth_context_secret)
        if verified.schema != AUTH_CONTEXT_DELEGATION_SCHEMA:
            raise ValueError("delegated auth context v2 is required for MCP task creation")
        campaign_id = RequestScopedMCPServer._argument_campaign_id(arguments)
        verified = verify_auth_context(
            envelope,
            config.auth_context_secret,
            expected_actor=verified.authority_principal,
            expected_requester=verified.authorization_principal,
            expected_campaign=campaign_id,
            expected_service="sagasmith-dnd-mcp",
            expected_operation=params.name,
            expected_audience="sagasmith-dnd-mcp",
            expected_room_turn=verified.room_turn_id,
            expected_base_revision=verified.base_revision,
            expected_resource_owner=verified.resource_owner_principal,
        )
        if verified.allowed_operations != (params.name,):
            raise ValueError("task creation delegation must allow only the requested tool")
        assert task_create_nonces is not None
        task_create_nonces.remember(verified)
        # The requesting player is authoritative for campaign access. The
        # model cannot select this identity through tool arguments.
        arguments["principal_id"] = verified.authorization_principal
        params.arguments = arguments
        return TaskIdentity(
            owner_principal=verified.authorization_principal,
            authority_principal=verified.authority_principal,
            resource_owner_principal=verified.resource_owner_principal,
            campaign_id=verified.campaign_id,
            room_turn_id=verified.room_turn_id,
            base_revision=verified.base_revision,
            # Ephemeral only: the store intentionally never persists signatures.
            auth_meta={AUTH_CONTEXT_META_KEY: deepcopy(envelope)},
        )

    def authorize_task_request(
        ctx: ServerRequestContext[Any, Any],
        operation: str,
        record: TaskRecord | None,
    ) -> TaskIdentity:
        if record is None:
            raise ValueError("task record is required")
        if config.auth_context_secret is None:
            principal = config.bound_principal_id or LOCAL_SYSTEM_PRINCIPAL_ID
            if principal != record.owner_principal or principal != record.authority_principal:
                raise ValueError("local task principal does not own this task")
            return TaskIdentity(
                owner_principal=record.owner_principal,
                authority_principal=record.authority_principal,
                resource_owner_principal=record.resource_owner_principal,
                campaign_id=record.campaign_id,
                room_turn_id=record.room_turn_id,
                base_revision=record.base_revision,
                auth_meta={},
            )
        envelope = task_envelope(ctx)
        verified = verify_auth_context(
            envelope,
            config.auth_context_secret,
            expected_actor=record.authority_principal,
            expected_requester=record.owner_principal,
            expected_campaign=record.campaign_id,
            expected_service="sagasmith-dnd-mcp",
            expected_operation=operation,
            expected_audience="sagasmith-dnd-mcp",
            expected_room_turn=record.room_turn_id,
            expected_base_revision=record.base_revision,
            expected_resource_owner=record.resource_owner_principal,
        )
        if verified.allowed_operations != (operation,):
            raise ValueError(f"task delegation must allow only {operation}")
        assert task_followup_nonces is not None
        task_followup_nonces.remember(verified)
        return TaskIdentity(
            owner_principal=verified.authorization_principal,
            authority_principal=verified.authority_principal,
            resource_owner_principal=verified.resource_owner_principal,
            campaign_id=verified.campaign_id,
            room_turn_id=verified.room_turn_id,
            base_revision=verified.base_revision,
            auth_meta={AUTH_CONTEXT_META_KEY: deepcopy(envelope)},
        )

    tasks_extension = TasksExtension(
        store=task_store,
        authorize_create=authorize_task_create,
        authorize_task=authorize_task_request,
    )

    mcp = RequestScopedMCPServer(
        "SagaSmith D&D",
        version="0.1.0",
        instructions=(
            "SagaSmith is a source-bound D&D 5e campaign runtime. Cold start: call "
            "skill_query(kind='skill', action='read', identifier='dnd.full'), then use "
            "outline/section/search for task-specific depth. Call "
            "storage_status and server_capabilities. "
            "Call campaign_query to find a campaign. tools/list is stable and cacheable; "
            "the Host selects a phase/task-appropriate facade subset for its model. The optional "
            "exposure workflow returns an explicit opaque handle for catalog guidance only and "
            "never changes tools/list or grants authority. Never invent a successful write, "
            "bypass phase/role/revision/idempotency requirements, or treat module search "
            "summaries as exact evidence. Read sagasmith://bootstrap for the compact "
            "host-independent workflow. Standard mechanics are engine-owned; unstructured "
            "module semantics default to Agent DM reasoning from exact source evidence; "
            "player choices, permission/owner approvals, and missing/conflicting sources "
            "remain external boundaries."
        ),
        exposure_registry=exposures,
        phase_lookup=authoritative_phase,
        allowed_tools_lookup=allowed_tools_for_exposure,
        scope_validator=validate_exposure_scope,
        tool_policy_authorizer=authorize_tool_policy,
        random_context_factory=campaign_random_context,
        context_binding_factory=lambda campaign_id, principal_id, arguments: (
            authoritative_host_context_binding(campaign_id, principal_id, arguments)
        ),
        authorization_fingerprint_lookup=access.authorization_fingerprint,
        bound_principal_id=config.bound_principal_id,
        auth_context_secret=config.auth_context_secret,
        cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="private")},
        extensions=[tasks_extension],
    )

    async def execute_durable_task(
        record: TaskRecord,
        request_context: ServerRequestContext[Any, Any],
        identity: TaskIdentity,
    ) -> CallToolResult:
        # A fresh task-method delegation was already verified against every
        # stored identity fact before this executor is scheduled. Invoke the
        # registered tool directly so an expired creation signature is never
        # persisted or replayed after restart.
        arguments = deepcopy(record.arguments)
        arguments["principal_id"] = identity.owner_principal
        context = Context(
            request_context=request_context,
            mcp_server=mcp,
            input_params=CallToolRequestParams(
                name=record.tool_name,
                arguments=arguments,
                meta=identity.auth_meta,
            ),
            subscriptions=mcp._subscriptions,
        )
        try:
            result = await MCPServer.call_tool(
                mcp,
                record.tool_name,
                arguments,
                context,
            )
        except ToolError as exc:
            message = _safe_tool_error_message(exc)
            result = mcp._structured_tool_error(message)
        if not isinstance(result, CallToolResult):
            raise RuntimeError("durable task tool returned an unsupported result type")
        result = mcp._ensure_text_fallback(result)
        binding = authoritative_host_context_binding(
            record.campaign_id,
            identity.owner_principal,
            arguments,
        )
        result = mcp._attach_host_context_binding(result, binding)
        task_audit = {
            "requester_principal": identity.owner_principal,
            "resource_owner_principal": identity.resource_owner_principal,
            "acting_host_principal": identity.authority_principal,
            "campaign_id": identity.campaign_id,
            "room_turn_id": identity.room_turn_id,
            "base_revision": identity.base_revision,
            "authorized_operation": request_context.method,
        }
        return result.model_copy(
            update={"meta": {**dict(result.meta or {}), "sagasmith_task_authority": task_audit}}
        )

    tasks_extension.set_executor(execute_durable_task)

    def adapt_value(value):
        if isinstance(value, RuntimeRenderResult):
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(value.metadata)),
                    Image(data=value.image.data, format=value.image.format).to_image_content(),
                ],
                structured_content=value.metadata,
            )
        if isinstance(value, RuntimeImage):
            return Image(data=value.data, format=value.format)
        if isinstance(value, list):
            return [adapt_value(item) for item in value]
        return value

    def adapt_function(function):
        @wraps(function)
        async def adapted(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return adapt_value(result)
            except OperationError as error:
                raise ToolError(str(error)) from error

        return adapted

    for operation in application.list_tools():
        mcp.tool(annotations=ToolAnnotations(**asdict(operation.annotations)))(
            adapt_function(operation.function)
        )
        registered = mcp._tool_manager.get_tool(operation.name)
        registered.parameters = deepcopy(operation.parameters)
        registered.fn_metadata.output_schema = operation.fn_metadata.output_schema
        registered.__dict__.pop("output_schema", None)
        registered.meta = deepcopy(operation.meta)
    for uri, options, function in application.resources:
        mcp.resource(uri, **options)(function)
    for options, function in application.prompts:
        mcp.prompt(**options)(function)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    async def exposure(
        action: Literal["open", "get", "search", "set"],
        ctx: Context,
        exposure_handle: str | None = None,
        campaign_id: str | None = None,
        query: Annotated[str, Field(max_length=200)] = "",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
        cursor: Annotated[str | None, Field(max_length=1024)] = None,
        add_tool_ids: list[str] | None = None,
        remove_tool_ids: list[str] | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Open or use an explicit, owner-bound catalog guidance handle.

        Search requires every whitespace-delimited term to match one tool. Use
        one short capability phrase or one exact tool id per search. This facade
        never changes the protocol tools/list result and never grants authority.
        """

        campaign_id = str(campaign_id or "").strip() or None
        if config.bound_principal_id is not None:
            principal_id = config.bound_principal_id
        request = mcp._request_session(ctx)
        modern = ctx.protocol_version == "2026-07-28"
        session_key = request[0] if request is not None else f"direct:{principal_id}"
        if modern:
            session_key = f"handle:{uuid4().hex}"
        if action == "open":
            current = exposures.active(session_key) if not modern else None
            if (
                current is not None
                and current.principal_id == principal_id
                and current.campaign_id == campaign_id
            ):
                raise ExposureError(
                    "This MCP session is already bound to that campaign. "
                    "Keep the exposure and use action='get', 'search', or 'set'; "
                    "phase and recovery refreshes must not reopen it."
                )
            phase = PROFILE_LOBBY
            if campaign_id:
                access.require_campaign(campaign_id, principal_id)
                phase = authoritative_phase(campaign_id)
            opened = exposures.open(
                session_key=session_key,
                principal_id=principal_id,
                campaign_id=campaign_id,
                phase=phase,
                authorization_fingerprint=(
                    access.authorization_fingerprint(campaign_id, principal_id)
                    if campaign_id
                    else ""
                ),
            )
            return {
                **exposures.status(opened),
                "exposure_handle": opened.id,
                "native_dynamic_tools": False,
                "catalog_effect": "guidance_only",
                "next": "Pass exposure_handle to exposure(get|search|set).",
            }

        handle = str(exposure_handle or "").strip()
        if modern and not handle:
            raise ExposureError("exposure_handle is required on the 2026-07-28 path")
        current = exposures.get(handle) if handle else exposures.active(session_key)
        if current is None:
            raise ExposureError("Unknown or expired exposure_handle. Use action='open'.")
        if current.principal_id != principal_id:
            raise ExposureError("The active exposure belongs to another principal.")
        if campaign_id is not None and campaign_id != current.campaign_id:
            raise ExposureError("Open a new handle to bind a different campaign.")
        if current.campaign_id:
            current_phase = authoritative_phase(current.campaign_id)
            exposures.refresh_phase(
                current,
                current_phase,
                allowed_tools=allowed_tools_for_exposure(current, current_phase),
            )
        if action == "get":
            return exposures.status(current)

        if action == "search":
            terms = {term.casefold() for term in query.split() if term.strip()}
            matches: list[dict[str, Any]] = []
            for tool in mcp._tool_manager.list_tools():
                policy = policy_for_tool(tool.name)
                if policy is None or current.phase not in policy.phases:
                    continue
                if policy.requires_campaign and current.campaign_id is None:
                    continue
                if policy.local_only and current.principal_id != LOCAL_SYSTEM_PRINCIPAL_ID:
                    continue
                roles = policy.roles(current.phase)
                if roles:
                    if current.campaign_id is None:
                        continue
                    try:
                        access.require_campaign(
                            current.campaign_id,
                            current.principal_id,
                            roles=set(roles),
                        )
                    except PermissionError:
                        continue
                haystack = f"{tool.name} {tool.description or ''}".casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                matches.append(
                    {
                        "tool_id": tool.name,
                        "description": tool.description or "",
                        "loaded": tool.name in current.loaded_tools,
                        "roles": sorted(roles),
                    }
                )
            matches = sorted(matches, key=lambda item: str(item["tool_id"]))
            page_items, page = _bounded_page(
                matches,
                scope=(
                    f"exposure:search:{current.id}:{current.revision}:{' '.join(sorted(terms))}"
                ),
                limit=limit,
                cursor=cursor,
                offset=offset,
            )
            result = {
                **exposures.status(current),
                "query_semantics": "all_terms_match_one_tool",
                "matches": page_items,
                "page": page,
                "next_cursor": page["next_cursor"],
            }
            if terms and not matches:
                result["next"] = (
                    "No single current-phase tool matched every query term. "
                    "Retry with one short capability phrase or one exact tool id; "
                    "an empty match list does not mean the phase has no tools."
                )
            return result

        additions = list(add_tool_ids or [])
        removals = list(remove_tool_ids or [])
        if not additions and not removals:
            raise ValueError("exposure(set) requires add_tool_ids or remove_tool_ids")
        for tool_id in additions:
            policy = policy_for_tool(tool_id)
            if policy is not None and policy.roles(current.phase):
                if current.campaign_id is None:
                    raise ExposureError(f"Tool {tool_id!r} requires a campaign.")
                access.require_campaign(
                    current.campaign_id,
                    current.principal_id,
                    roles=set(policy.roles(current.phase)),
                )
        async with mcp._exposure_lock(current.id):
            changed = exposures.set_tools(current, add=additions, remove=removals)
        return {**exposures.status(current), "changed": changed}

    exposure_operation = mcp._tool_manager.get_tool("exposure")
    exposure_operation.meta = {"sagasmith_domain_context": "sagasmith-dnd"}
    mcp.runtime = application
    if config.auth_context_secret:
        mcp._auth_context_nonces = AuthContextNonceGuard(
            store=application.ports["auth_nonce_store"]
        )
    return mcp


def main() -> None:
    # pypdfium2 imports NumPy-backed bitmap helpers lazily. On Windows that
    # native import can stall when first attempted from FastMCP's running
    # asyncio loop, so warm it on the main thread when the documents extra is
    # installed. A text-only base wheel must still start without that extra.
    _preload_optional_pdf_runtime()

    config = McpConfig.from_environment()
    transport = os.environ.get("SAGASMITH_DND_MCP_TRANSPORT", "stdio").strip().casefold()
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("SAGASMITH_DND_MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    if (
        transport == "streamable-http"
        and config.http_host.strip().casefold() not in {"127.0.0.1", "::1", "localhost"}
        and config.auth_context_secret is None
    ):
        raise ValueError("D&D non-loopback Streamable HTTP requires SAGASMITH_AUTH_CONTEXT_SECRET")
    server = create_server(config)
    try:
        if transport == "streamable-http":
            server.run(
                transport="streamable-http",
                host=config.http_host,
                port=config.http_port,
                streamable_http_path=config.http_path,
            )
        else:
            server.run(transport="stdio")
    finally:
        close_server(server)


if __name__ == "__main__":
    main()
