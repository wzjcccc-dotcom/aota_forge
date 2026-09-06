"""Restricted shared AOTA MCP transport.

This module is an adapter boundary, not an authority boundary.  A trusted
runtime constructs :class:`TrustedWorkerBinding` and passes it to
``create_shared_mcp_server``.  The MCP caller receives only the three typed
workspace operations and cannot supply or replace the binding fields.

The optional ``mcp`` import is intentionally confined to this module.  Core
and the existing tool providers remain usable when the MCP extra is absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypedDict

from aota_forge.core.context import TrustedContext
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import ToolRoleSurface
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    BoundedWorkspaceMutationProvider,
    WorkspaceMutationAuthority,
)
from aota_forge.work_plane.workspace_tools import (
    BoundedWorkspaceToolProvider,
    WorkspaceAuthorityEvidence,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

try:  # The MCP SDK is an adapter dependency, never a Core dependency.
    from mcp.types import ToolAnnotations
    try:
        from mcp.server import MCPServer
    except ImportError:  # mcp 1.x exposes the same stdio server as FastMCP.
        from mcp.server.fastmcp import FastMCP as MCPServer
except ImportError:  # pragma: no cover - exercised in installations without [mcp]
    MCPServer = None  # type: ignore[assignment,misc]
    ToolAnnotations = None  # type: ignore[assignment,misc]


ONE_SHARED_AOTA_MCP = True
PER_ROLE_MCP_SERVER = False
MCP_IS_AUTHORITY_ENGINE = False
NEW_AUTHORITY_PLANE_REQUIRED = False
RAW_TERMINAL = False
RAW_SHELL = False
GENERIC_STRINGLY_AOTA_CLI_TOOL = False
MCP_RESULT_IS_AUTHORITY = False
CORE_MCP_CONTAMINATION = False

MCP_PUBLIC_TOOLS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "workspace.write",
)
MCP_PUBLIC_TOOL_COUNT = len(MCP_PUBLIC_TOOLS)
TYPED_OPERATION_SURFACE = True
TRUSTED_SERVER_BINDING_PRESENT = True
VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED = True
READ_AUTHORITY_IS_WRITE_AUTHORITY = False
NEW_PERMISSION_ENGINE = False
NEW_TOOL_REGISTRY = False
NEW_RESULT_ONTOLOGY = False

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_FAILURE_MESSAGE = 512


class McpTransportUnavailable(RuntimeError):
    """The optional standard MCP SDK is not installed."""


class TrustedBindingError(ValueError):
    """Malformed or incomplete server-side worker binding."""


class McpToolResult(TypedDict):
    """Structured transport projection of the existing ``ToolResponse``."""

    ok: bool
    operation: str
    payload: dict[str, Any] | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class TrustedWorkerBinding:
    """Operator/runtime-owned context for one restricted MCP server.

    This is a thin carrier of already-authoritative objects.  It does not
    decide permissions, resolve projects, or mint capabilities.  In
    particular, a model-facing MCP argument can never construct this object.
    ``mutation_authority`` is optional deliberately: the write tool remains
    visible while rejecting calls when mutation authority is absent.
    """

    canonical_task_id: str
    project_id: str
    worktree_id: str
    trusted_context: TrustedContext
    handoff: TaskHandoff
    sandbox: WorktreeSandboxBoundary
    tool_surface: ToolRoleSurface
    read_authorities: tuple[WorkspaceAuthorityEvidence, ...]
    mutation_authority: WorkspaceMutationAuthority | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not _SAFE_ID.fullmatch(self.canonical_task_id):
            raise TrustedBindingError("canonical_task_id must be a bounded trusted identifier")
        if not isinstance(self.project_id, str) or not _SAFE_ID.fullmatch(self.project_id):
            raise TrustedBindingError("project_id must be a bounded trusted identifier")
        if not isinstance(self.worktree_id, str) or not _SAFE_ID.fullmatch(self.worktree_id):
            raise TrustedBindingError("worktree_id must be a bounded trusted identifier")
        if not isinstance(self.trusted_context, TrustedContext) or not self.trusted_context.is_bound:
            raise TrustedBindingError("trusted runtime context is required")
        if self.trusted_context.principal is None:
            raise TrustedBindingError("trusted principal is required")
        if not isinstance(self.handoff, TaskHandoff):
            raise TrustedBindingError("typed TaskHandoff is required")
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise TrustedBindingError("trusted WorktreeSandboxBoundary is required")
        if self.sandbox.project_id != self.project_id or self.sandbox.worktree_id != self.worktree_id:
            raise TrustedBindingError("binding project/worktree does not match sandbox")
        if not isinstance(self.tool_surface, ToolRoleSurface):
            raise TrustedBindingError("typed ToolRoleSurface is required")
        if self.tool_surface.work_role != self.handoff.work_role:
            raise TrustedBindingError("tool surface role does not match TaskHandoff role")
        if set(self.tool_surface.all_capability_names()) != set(MCP_PUBLIC_TOOLS):
            raise TrustedBindingError("tool surface must contain exactly the bounded MCP v1 tools")
        if not isinstance(self.read_authorities, tuple) or len(self.read_authorities) != 2:
            raise TrustedBindingError("read authorities for workspace.search and workspace.read are required")
        read_names = {authority.operation.name for authority in self.read_authorities}
        if read_names != {"workspace.search", "workspace.read"}:
            raise TrustedBindingError("read authorities must cover workspace.search and workspace.read")
        for authority in self.read_authorities:
            if authority.sandbox != self.sandbox or authority.handoff != self.handoff:
                raise TrustedBindingError("read authority does not match trusted binding")
        if self.mutation_authority is not None:
            if not isinstance(self.mutation_authority, WorkspaceMutationAuthority):
                raise TrustedBindingError("mutation authority must be typed")
            if self.mutation_authority.sandbox != self.sandbox or self.mutation_authority.handoff != self.handoff:
                raise TrustedBindingError("mutation authority does not match trusted binding")


def _authority_for(binding: TrustedWorkerBinding, operation: str) -> WorkspaceAuthorityEvidence | None:
    return next(
        (authority for authority in binding.read_authorities if authority.operation.name == operation),
        None,
    )


def _bounded_failure_message(value: object) -> str:
    """Keep governed error identity while preventing traceback/path leakage."""
    if not isinstance(value, str) or not value.strip():
        return "governed operation failed"
    message = value.replace("\x00", "").strip()
    # Providers may include a physical path in diagnostics.  Logical MCP
    # results must not disclose the trusted worktree root.
    message = re.sub(r"(?:/[^\s:'\"]+)+", "<bounded-path>", message)
    return message[:_MAX_FAILURE_MESSAGE]


def _project_response(operation: str, response: ToolResponse) -> McpToolResult:
    """Project the existing response carrier without creating result ontology."""
    if not isinstance(response, ToolResponse):
        return {
            "ok": False,
            "operation": operation,
            "payload": None,
            "error": {"code": "INTERNAL_ERROR", "message": "invalid governed tool response"},
        }
    if response.ok:
        payload = dict(response.payload or {})
        # Physical path is provider evidence, not useful MCP payload and not
        # a binding value that should cross the transport boundary.
        payload.pop("canonical_path", None)
        return {"ok": True, "operation": operation, "payload": payload, "error": None}
    error = dict(response.error or {})
    projected_error: dict[str, Any] = {
        "code": error.get("code") if isinstance(error.get("code"), str) else "GOVERNED_OPERATION_FAILURE",
        "message": _bounded_failure_message(error.get("message")),
    }
    if isinstance(error.get("retryable"), bool):
        projected_error["retryable"] = error["retryable"]
    return {"ok": False, "operation": operation, "payload": None, "error": projected_error}


class _SharedAotaMcpAdapter:
    """Transport-local dispatch using only existing provider seams."""

    def __init__(self, binding: TrustedWorkerBinding) -> None:
        self.binding = binding
        self._read_providers = {
            operation: BoundedWorkspaceToolProvider(authority)
            for operation in ("workspace.search", "workspace.read")
            if (authority := _authority_for(binding, operation)) is not None
        }
        self._write_provider = (
            BoundedWorkspaceMutationProvider(binding.mutation_authority)
            if binding.mutation_authority is not None
            else None
        )

    def invoke(self, operation: str, inputs: dict[str, Any]) -> McpToolResult:
        try:
            if operation == "workspace.write":
                if self._write_provider is None:
                    return _project_response(
                        operation,
                        ToolResponse.failure(
                            {"code": "AUTHORITY_DENIED", "message": "trusted mutation authority is absent"}
                        ),
                    )
                request = ToolRequest(
                    operation=WORKSPACE_WRITE_DESCRIPTOR,
                    inputs=inputs,
                )
                return _project_response(operation, self._write_provider.invoke(request))

            provider = self._read_providers.get(operation)
            authority = _authority_for(self.binding, operation)
            if provider is None or authority is None:
                return _project_response(
                    operation,
                    ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted read authority is absent"}),
                )
            request = ToolRequest(operation=authority.operation, inputs=inputs)
            return _project_response(operation, provider.invoke(request))
        except Exception as exc:  # adapter boundary: never turn failures into success  # noqa: BLE001
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "INVALID_INPUT", "message": _bounded_failure_message(str(exc))},
            }


def create_shared_mcp_server(trusted_binding: TrustedWorkerBinding):
    """Create one shared standard MCP server with exactly three typed tools."""
    if MCPServer is None or ToolAnnotations is None:
        raise McpTransportUnavailable("restricted shared MCP transport requires the standard 'mcp' package")
    if not isinstance(trusted_binding, TrustedWorkerBinding):
        raise TrustedBindingError("trusted server-side binding is required")

    adapter = _SharedAotaMcpAdapter(trusted_binding)
    server = MCPServer(
        "aota",
        instructions="Restricted AOTA workspace transport. MCP provides transport only; AF remains authority.",
    )

    @server.tool(
        name="workspace.search",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        structured_output=True,
    )
    def workspace_search(query: str, max_results: int | None = None, scope: str | None = None) -> McpToolResult:
        """Search the trusted worker worktree with bounded lexical matching."""
        return adapter.invoke(
            "workspace.search",
            {"query": query, "max_results": max_results, "scope": scope},
        )

    @server.tool(
        name="workspace.read",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        structured_output=True,
    )
    def workspace_read(path: str, max_bytes: int | None = None, offset: int | None = None) -> McpToolResult:
        """Read bounded UTF-8 content from the trusted worker worktree."""
        return adapter.invoke(
            "workspace.read",
            {"path": path, "max_bytes": max_bytes, "offset": offset},
        )

    @server.tool(
        name="workspace.write",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False),
        structured_output=True,
    )
    def workspace_write(path: str, content: str, mode: str) -> McpToolResult:
        """Write bounded content only when trusted mutation authority is bound."""
        return adapter.invoke(
            "workspace.write",
            {"path": path, "content": content, "mode": mode},
        )

    return server


async def run_shared_mcp_server(trusted_binding: TrustedWorkerBinding) -> None:
    """Run the shared server over standard MCP stdio for Hermes v0.21."""
    server = create_shared_mcp_server(trusted_binding)
    await server.run_stdio_async()


__all__ = [
    "CORE_MCP_CONTAMINATION",
    "GENERIC_STRINGLY_AOTA_CLI_TOOL",
    "MCP_IS_AUTHORITY_ENGINE",
    "MCP_PUBLIC_TOOLS",
    "MCP_PUBLIC_TOOL_COUNT",
    "MCP_RESULT_IS_AUTHORITY",
    "NEW_AUTHORITY_PLANE_REQUIRED",
    "NEW_PERMISSION_ENGINE",
    "NEW_RESULT_ONTOLOGY",
    "NEW_TOOL_REGISTRY",
    "ONE_SHARED_AOTA_MCP",
    "PER_ROLE_MCP_SERVER",
    "RAW_SHELL",
    "RAW_TERMINAL",
    "READ_AUTHORITY_IS_WRITE_AUTHORITY",
    "TRUSTED_SERVER_BINDING_PRESENT",
    "TYPED_OPERATION_SURFACE",
    "VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED",
    "McpToolResult",
    "McpTransportUnavailable",
    "TrustedBindingError",
    "TrustedWorkerBinding",
    "create_shared_mcp_server",
    "run_shared_mcp_server",
]
