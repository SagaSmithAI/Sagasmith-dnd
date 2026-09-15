"""Typed application operation registry and in-process execution boundary."""

from __future__ import annotations

import inspect
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, get_type_hints

from pydantic import ConfigDict, create_model

from .tool_profiles import CORE_TOOLS, policy_for_tool


class OperationError(ValueError):
    """An anticipated failure with a transport-independent recovery envelope."""

    def __init__(
        self, message: str, *, code: str | None = None, retryable: bool = False,
        recovery: Any = "Correct the request before retrying.",
    ) -> None:
        super().__init__(message)
        self.has_explicit_contract = code is not None
        self.envelope = {
            "code": code or "invalid_request", "message": message, "retryable": retryable,
            "recovery": deepcopy(recovery),
        }


@dataclass(frozen=True)
class RequestIdentity:
    """Constructed by a trusted Host or authenticated transport, never from payload."""

    principal_id: str
    campaign_id: str | None = None


@dataclass(frozen=True)
class Image:
    data: bytes
    format: str = "png"


@dataclass(frozen=True)
class RenderResult:
    metadata: dict[str, Any]
    image: Image


@dataclass
class OperationHints:
    read_only_hint: bool
    destructive_hint: bool
    idempotent_hint: bool
    open_world_hint: bool


@dataclass
class Operation:
    name: str
    function: Callable[..., Any]
    input_model: Any
    parameters: dict[str, Any]
    annotations: OperationHints | None
    description: str
    meta: dict[str, Any] = field(default_factory=dict)
    fn_metadata: Any = field(default_factory=lambda: SimpleNamespace(output_schema=None))


class DndRuntime:
    """One registry shared by direct application calls and protocol adapters."""

    def __init__(self) -> None:
        self.operations: dict[str, Operation] = {}
        self.resources: list[tuple[str, dict[str, Any], Callable]] = []
        self.prompts: list[tuple[dict[str, Any], Callable]] = []
        self.ports: dict[str, Any] = {}
        self._tool_manager = self

    def tool(self, *, annotations: OperationHints | None = None):
        def register(function):
            if function.__name__ in self.operations:
                raise ValueError(f"duplicate operation {function.__name__}")
            hints = get_type_hints(function, include_extras=True)
            fields = {}
            for name, parameter in inspect.signature(function).parameters.items():
                default = ... if parameter.default is inspect.Parameter.empty else parameter.default
                fields[name] = (hints.get(name, Any), default)
            model = create_model(
                function.__name__ + "Input",
                __config__=ConfigDict(extra="forbid"),
                **fields,
            )
            self.operations[function.__name__] = Operation(
                function.__name__,
                function,
                model,
                model.model_json_schema(),
                annotations,
                inspect.getdoc(function) or "",
            )
            return function

        return register

    def resource(self, uri: str, **kwargs):
        def register(function):
            self.resources.append((uri, kwargs, function))
            return function

        return register

    def prompt(self, **kwargs):
        def register(function):
            self.prompts.append((kwargs, function))
            return function

        return register

    def list_tools(self) -> list[Operation]:
        return list(self.operations.values())

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a registered handler after an adapter's trusted request checks."""
        operation = self.operations[name]
        values = operation.input_model.model_validate(arguments)
        result = operation.function(**dict(values))
        return await result if inspect.isawaitable(result) else result

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RequestIdentity,
    ) -> Any:
        with self.command_scope(name, arguments, context=context) as (bound, _stream):
            return await self.invoke(name, bound)

    @contextmanager
    def command_scope(self, name: str, arguments: dict[str, Any], *, context: RequestIdentity):
        """Own application authorization and random progress for every adapter."""
        if not context.principal_id.strip():
            raise PermissionError("authenticated principal is required")
        arguments = deepcopy(arguments)
        self.ports["validate_arguments"](arguments)
        operation = self.operations[name]
        properties = operation.parameters.get("properties", {})
        for principal_field in ("auth_principal_id", "by_principal_id", "principal_id"):
            if principal_field in properties:
                arguments[principal_field] = context.principal_id
                break
        payload = arguments.get("payload")
        campaign_id = arguments.get("campaign_id") or (
            payload.get("campaign_id") if isinstance(payload, dict) else None
        )
        if not campaign_id:
            actor_id = arguments.get("character_id") or arguments.get("actor_id")
            if arguments.get("owner") == "party":
                campaign_id = arguments.get("owner_id")
            elif arguments.get("owner") == "character":
                actor_id = arguments.get("owner_id")
            if actor_id and not campaign_id:
                campaign_id = self.ports["character_campaign"](actor_id)
        if context.campaign_id and campaign_id and context.campaign_id != campaign_id:
            raise PermissionError("request identity belongs to another campaign")
        campaign_id = campaign_id or context.campaign_id or None
        # This facade nests its campaign selector in payload rather than the
        # top-level trusted arguments injected by MCP clients.
        if name == "character_query" and context.campaign_id and arguments.get(
            "view", "list"
        ) in {"list", "batch", "catalog"}:
            arguments["payload"] = {**(payload or {}), "campaign_id": campaign_id}
        self.ports["authorize_tool_policy"](name, context.principal_id, campaign_id)
        manager = (
            self.ports["campaign_random_context"](campaign_id, name, arguments)
            if campaign_id and name not in CORE_TOOLS
            else nullcontext(None)
        )
        with manager as stream:
            yield arguments, stream
            if stream is not None and stream.has_unpersisted_draws:
                raise RuntimeError("application returned without committing random progress")

    def contract(self) -> list[dict[str, Any]]:
        rows = []
        for operation in sorted(self.operations.values(), key=lambda item: item.name):
            policy = policy_for_tool(operation.name)
            rows.append(
                {
                    "id": operation.name,
                    "input_schema": operation.parameters,
                    "output_schema": operation.fn_metadata.output_schema,
                    "phases": sorted(policy.phases) if policy else [],
                    "roles": {phase: sorted(policy.roles(phase)) for phase in policy.phases}
                    if policy
                    else {},
                    "requires_campaign": bool(policy and policy.requires_campaign),
                    "read_only": bool(
                        operation.annotations and operation.annotations.read_only_hint
                    ),
                    "idempotent": bool(
                        operation.annotations and operation.annotations.idempotent_hint
                    ),
                    "revision_fields": [
                        key
                        for key in operation.parameters.get("properties", {})
                        if "revision" in key
                    ],
                    "error_envelope": {
                        "fields": ["code", "message", "retryable", "recovery"],
                        "unknown_dispatch": (
                            "Recover using the original request and idempotency key."
                        ),
                        "pending_choice": "Return the pending decision to its authorized owner.",
                    },
                    "description": operation.description,
                }
            )
        return rows

    def close(self) -> None:
        close = getattr(self, "_sagasmith_close", None)
        if close:
            close()
