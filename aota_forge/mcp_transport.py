"""Restricted shared AOTA MCP transport — single-entry.

This module is an adapter boundary, not an authority boundary.  A trusted
runtime constructs :class:`TrustedWorkerBinding` and passes it to
``create_shared_mcp_server``.  The MCP caller receives exactly one
Agent-facing tool ``aota.invoke(operation, arguments)`` and cannot supply or
replace the binding fields.

The optional ``mcp`` import is intentionally confined to this module.  Core
and the existing tool providers remain usable when the MCP extra is absent.

W1 single-entry invariants:

* HERMES_AGENT_FACING_AOTA_TOOL_COUNT=1, AGENT_FACING_AOTA_TOOL=aota.invoke
* TRANSPORT_OPERATION_IDENTITY_SEPARATED=yes
* TOOL_VISIBILITY_IS_AUTHORITY=no
* Reuses existing OperationContractDescriptor / validate_inputs / ToolProvider
* Exact operation resolution (case-sensitive, no fuzzy) → UNKNOWN_OPERATION
* Unknown input → UNKNOWN_INPUT, oversized/deep → INPUT_SIZE_EXCEEDED,
  missing authority → AUTHORITY_DENIED, error identity preserved end-to-end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypedDict

from aota_forge.core.context import TrustedContext
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import ToolRoleSurface
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    BoundedWorkspaceMutationProvider,
    WorkspaceMutationAuthority,
)
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
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

# Transport surface (Agent-facing MCP tools) — W1 single-entry.
MCP_PUBLIC_TOOLS: tuple[str, ...] = ("aota.invoke",)
MCP_PUBLIC_TOOL_COUNT = len(MCP_PUBLIC_TOOLS)
AGENT_FACING_AOTA_TOOL = "aota.invoke"
HERMES_AGENT_FACING_AOTA_TOOL_COUNT = 1

# Logical capability surface (existing typed operations, not MCP tool names).
WORKSPACE_OPERATIONS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "workspace.write",
)
# Back-compat alias: the bounded logical operations before transport convergence.
BOUNDED_MCP_OPERATIONS = WORKSPACE_OPERATIONS
LOGICAL_CAPABILITY_SURFACE = WORKSPACE_OPERATIONS

TYPED_OPERATION_SURFACE = True
TRUSTED_SERVER_BINDING_PRESENT = True
VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED = True
READ_AUTHORITY_IS_WRITE_AUTHORITY = False
NEW_PERMISSION_ENGINE = False
NEW_TOOL_REGISTRY = False
NEW_RESULT_ONTOLOGY = False
TRANSPORT_OPERATION_IDENTITY_SEPARATED = True
TOOL_VISIBILITY_IS_AUTHORITY = False
SINGLE_ENTRY_TRANSPORT = True
MCP_TRANSPORT_TOOL_COUNT = 1
NEW_OPERATION_AUTHORITY_REGISTRY_CREATED = False
DEFAULT_REGISTRY_MIGRATION_REQUIRED = False

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_FAILURE_MESSAGE = 512

# Exact deterministic operation -> descriptor map (no fuzzy, no alias).
_DESCRIPTOR_MAP: dict[str, Any] = {
    WORKSPACE_SEARCH_DESCRIPTOR.name: WORKSPACE_SEARCH_DESCRIPTOR,
    WORKSPACE_READ_DESCRIPTOR.name: WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_WRITE_DESCRIPTOR.name: WORKSPACE_WRITE_DESCRIPTOR,
}
SUPPORTED_OPERATIONS: frozenset[str] = frozenset(WORKSPACE_OPERATIONS)


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
    ``mutation_authority`` is optional deliberately: the write operation
    remains callable via aota.invoke while rejecting calls when mutation
    authority is absent (AUTHORITY_DENIED).

    Transport vs capability separation (W1):
    * MCP transport surface is ``aota.invoke`` (exactly one).
    * Logical capability surface is ``workspace.search/read/write`` carried
      by ``ToolRoleSurface``.  Visibility (eager/progressive) never grants
      authority; authority lives in Workspace*AuthorityEvidence.
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
        # W1 separation: tool_surface carries logical capabilities, not MCP transport names.
        # It must contain exactly the bounded workspace operations, not the transport tool.
        if set(self.tool_surface.all_capability_names()) != set(WORKSPACE_OPERATIONS):
            raise TrustedBindingError(
                f"tool surface must contain exactly the bounded workspace operations "
                f"(expected {sorted(WORKSPACE_OPERATIONS)}, got {sorted(self.tool_surface.all_capability_names())})"
            )
        if "aota.invoke" in set(self.tool_surface.all_capability_names()):
            raise TrustedBindingError("tool surface must not contain transport name aota.invoke (transport != capability)")
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
    """Transport-local dispatch using only existing provider seams.

    Single-entry: ``aota.invoke(operation, arguments)`` -> exact operation
    resolution -> existing OperationContractDescriptor -> validate_inputs ->
    existing authority -> existing ToolProvider -> bounded transport response.

    No new registry, permission engine, or generic gateway is created.
    """

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

    def invoke(self, operation: str, arguments: dict[str, Any] | None) -> McpToolResult:  # noqa: C901
        # ---- outer envelope validation (operation, arguments) ----
        if not isinstance(operation, str):
            return {
                "ok": False,
                "operation": str(operation) if isinstance(operation, (str, bytes)) else "unknown",
                "payload": None,
                "error": {"code": "UNKNOWN_OPERATION", "message": f"operation must be string, got {type(operation).__name__}"},
            }
        # Exact, deterministic, case-sensitive resolution — no fuzzy, no alias.
        if operation not in SUPPORTED_OPERATIONS:
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "UNKNOWN_OPERATION", "message": f"unknown operation: {operation!r}"},
            }
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "INPUT_TYPE_INVALID", "message": f"arguments must be object, got {type(arguments).__name__}"},
            }
        # ---- nested argument validation via existing descriptor seam ----
        descriptor = _DESCRIPTOR_MAP.get(operation)
        if descriptor is None:
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "UNKNOWN_OPERATION", "message": f"unknown operation: {operation!r}"},
            }
        try:
            validated = validate_inputs(descriptor, arguments)
        except ForgeError as exc:
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": getattr(exc, "code", "INVALID_INPUT"), "message": _bounded_failure_message(str(exc))},
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "INVALID_INPUT", "message": _bounded_failure_message(str(exc))},
            }

        # ---- authority-preserved provider dispatch (reuse existing providers) ----
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
                    inputs=validated,
                )
                return _project_response(operation, self._write_provider.invoke(request))

            provider = self._read_providers.get(operation)
            authority = _authority_for(self.binding, operation)
            if provider is None or authority is None:
                return _project_response(
                    operation,
                    ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted read authority is absent"}),
                )
            if authority.operation.contract_hash() != descriptor.contract_hash():
                return _project_response(
                    operation,
                    ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"}),
                )
            request = ToolRequest(operation=authority.operation, inputs=validated)
            return _project_response(operation, provider.invoke(request))
        except ForgeError as exc:
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), "message": _bounded_failure_message(str(exc))},
            }
        except Exception as exc:  # adapter boundary: never turn failures into success  # noqa: BLE001
            msg = _bounded_failure_message(str(exc))
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": {"code": "GOVERNED_OPERATION_FAILURE", "message": msg},
            }


def create_shared_mcp_server(trusted_binding: TrustedWorkerBinding):
    """Create one shared standard MCP server with exactly one Agent-facing tool.

    After W1 the Agent-facing surface is:

    * ``aota.invoke(operation: string, arguments: object)``

    ``workspace.search``, ``workspace.read``, and ``workspace.write`` are
    logical operation identities dispatched through that single transport.
    They are NOT separate MCP Tools (MCP_TRANSPORT_TOOL_COUNT=1).
    """
    if MCPServer is None or ToolAnnotations is None:
        raise McpTransportUnavailable("restricted shared MCP transport requires the standard 'mcp' package")
    if not isinstance(trusted_binding, TrustedWorkerBinding):
        raise TrustedBindingError("trusted server-side binding is required")

    adapter = _SharedAotaMcpAdapter(trusted_binding)
    server = MCPServer(
        "aota",
        instructions="Restricted AOTA workspace transport. MCP provides transport only; AF remains authority. Single entry: aota.invoke(operation, arguments).",
    )

    @server.tool(
        name="aota.invoke",
        description="Typed AOTA dispatch. operation is exact canonical name (e.g. workspace.search); arguments is the operation's typed input object.",
        structured_output=True,
    )
    def aota_invoke(operation: str, arguments: dict[str, Any] | None = None) -> McpToolResult:  # type: ignore[no-redef]
        """Single-entry AOTA dispatch: exact operation resolution → existing descriptor → validate_inputs → existing authority → existing provider."""
        return adapter.invoke(operation, arguments if arguments is not None else {})

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
    "AGENT_FACING_AOTA_TOOL",
    "HERMES_AGENT_FACING_AOTA_TOOL_COUNT",
    "WORKSPACE_OPERATIONS",
    "BOUNDED_MCP_OPERATIONS",
    "LOGICAL_CAPABILITY_SURFACE",
    "SUPPORTED_OPERATIONS",
    "MCP_TRANSPORT_TOOL_COUNT",
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
    "TRANSPORT_OPERATION_IDENTITY_SEPARATED",
    "TOOL_VISIBILITY_IS_AUTHORITY",
    "SINGLE_ENTRY_TRANSPORT",
    "DEFAULT_REGISTRY_MIGRATION_REQUIRED",
    "NEW_OPERATION_AUTHORITY_REGISTRY_CREATED",
    "McpToolResult",
    "McpTransportUnavailable",
    "TrustedBindingError",
    "TrustedWorkerBinding",
    "create_shared_mcp_server",
    "run_shared_mcp_server",
]
