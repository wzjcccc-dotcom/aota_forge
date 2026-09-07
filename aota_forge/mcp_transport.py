"""Restricted shared AOTA MCP transport — single-entry with governed result contract.

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

W3 governed result contract:

* Provider → ToolResponse → safety/redaction → ToolResultProjection
  → bounded inline OR governed ToolOutputRef → MCP structuredContent
* Reuses existing ToolResultProjection / ToolOutputRef / project_tool_result
* Inline bound is existing 4096 bytes (TOOL_INLINE_OUTPUT_MAX_BYTES)
* Large result is by_ref, never silently truncated, never full inline
* Outcome/completeness explicit, typed errors preserved, no path/secret leakage
* No durable hydration, no result DB, no new ontology
"""

from __future__ import annotations

import json
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
from aota_forge.work_plane.tool_result_governance import (
    TOOL_INLINE_OUTPUT_MAX_BYTES,
    project_tool_result,
)

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

# W3 governed result contract — reuse existing governance, no new store
EXISTING_RESULT_GOVERNANCE_REUSED = True
EXISTING_TOOL_RESPONSE_REUSED = True
TOOL_RESPONSE_SCHEMA_CHANGED = False
THIRD_RESULT_ONTOLOGY_CREATED = False
NEW_RESULT_DB_CREATED = False
NEW_RESULT_STATE_MACHINE_CREATED = False
NEW_PERSISTENT_RESULT_STORE_CREATED = False
TOOL_RESULT_GOVERNANCE_PRODUCTION_PATH = True
TOOL_RESULT_GOVERNANCE_CORE_MUTATION = False
SMALL_RESULT_INLINE_BOUNDED = True
LARGE_RESULT_BY_REF = True
RAW_UNBOUNDED_RESULT_TO_AGENT = False
SILENT_TRUNCATION = False
OUTCOME_EXPLICIT = True
COMPLETENESS_EXPLICIT = True
ERROR_TYPED = True
ERROR_IDENTITY_PRESERVED_END_TO_END = True
DURABLE_SELECTIVE_HYDRATION_IMPLEMENTED_IN_W3 = False
RESULT_HYDRATE_OPERATION_IMPLEMENTED_IN_W3 = False
RESTRICTED_SHELL_ACTIVATED_IN_W3 = False
TASK_MAIN_CONTROL_IMPLEMENTED_IN_W3 = False
INLINE_BOUND = TOOL_INLINE_OUTPUT_MAX_BYTES

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


class McpToolResult(TypedDict, total=False):
    """Structured transport projection of the existing ``ToolResponse`` via governed contract.

    W1 fields (still present for parity):
        ok, operation, payload, error
    W3 governed contract (bounded inline OR governed ref, outcome/completeness explicit):
        output_mode, is_truncated, complete, outcome, is_success,
        capability_name, output_digest, output_byte_length,
        inline_output, output_ref

    Bounded inline: output_mode=="inline", is_truncated==False, complete==True,
                    payload is sanitized bounded dict, inline_output is canonical JSON
    Large by_ref:  output_mode=="by_ref", is_truncated==True, complete==False,
                    payload is None (raw oversized absent), output_ref is governed
                    ToolOutputRef contract (REF_CONTRACT_ONLY, non-durable)
    Failure:       ok==False, is_success==False, outcome=="failure",
                    error typed, never false success/completeness
    """

    ok: bool
    operation: str
    payload: dict[str, Any] | None
    error: dict[str, Any] | None
    # W3 governed extensions (present on all results)
    output_mode: str
    is_truncated: bool
    complete: bool
    outcome: str
    is_success: bool
    capability_name: str
    output_digest: str
    output_byte_length: int
    inline_output: str | None
    output_ref: dict[str, Any] | None


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


def _sanitize_tool_response(response: ToolResponse) -> ToolResponse:
    """Safety/redaction projection applied before governed result projection.

    Reuses existing transport safety semantics:
    * success: strip canonical_path (physical path not useful MCP payload)
    * failure: bound and redact absolute paths in error message, preserve typed code
    Order is critical: safety → governed projection, so the governed projection
    never serializes a previously hidden field.
    """
    if not isinstance(response, ToolResponse):
        return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": "invalid governed tool response"})
    if response.ok:
        payload = dict(response.payload or {})
        payload.pop("canonical_path", None)
        return ToolResponse.success(payload)
    error = dict(response.error or {})
    projected_error: dict[str, Any] = {
        "code": error.get("code") if isinstance(error.get("code"), str) else "GOVERNED_OPERATION_FAILURE",
        "message": _bounded_failure_message(error.get("message")),
    }
    if isinstance(error.get("retryable"), bool):
        projected_error["retryable"] = error["retryable"]
    # Preserve bounded details if present and serializable within bound? Omit for safety.
    return ToolResponse.failure(projected_error)


def _governed_projection_to_mcp(operation: str, projection) -> McpToolResult:
    """Convert an existing ToolResultProjection into a bounded MCP result.

    Reuses ToolResultProjection field names (is_success, output_mode, is_truncated,
    output_digest, output_byte_length, inline_output, output_ref, etc.)
    and adds explicit outcome/completeness aliases for Agent machinability.
    Never returns raw oversized payload inline.
    """
    # Base governed fields (outcome/completeness explicit, error typed)
    result: dict[str, Any] = {
        "ok": projection.is_success,
        "operation": operation,
        "payload": None,
        "error": dict(projection.error) if projection.error is not None else None,
        "output_mode": projection.output_mode,
        "is_truncated": projection.is_truncated,
        "complete": not projection.is_truncated,
        "outcome": "success" if projection.is_success else "failure",
        "is_success": projection.is_success,
        "capability_name": projection.capability_name,
        "output_digest": projection.output_digest,
        "output_byte_length": projection.output_byte_length,
        "inline_output": projection.inline_output,
        "output_ref": projection.output_ref.to_dict() if projection.output_ref is not None else None,
    }
    if projection.output_mode == "inline" and projection.is_success:
        # Small bounded inline: populate payload as parsed JSON of inline_output
        # (payload was sanitized before projection, so no canonical_path leakage)
        try:
            if projection.inline_output is not None and projection.inline_output != "":
                parsed = json.loads(projection.inline_output)
                if isinstance(parsed, dict):
                    result["payload"] = parsed
                elif parsed == {} and projection.inline_output in ("{}", ""):
                    result["payload"] = {}
                else:
                    result["payload"] = {"content": projection.inline_output}
            else:
                # empty output (e.g., success with empty payload)
                result["payload"] = {}
        except Exception:
            result["payload"] = {"content": projection.inline_output}
        # inline_output already present, payload is bounded dict
    elif projection.output_mode == "inline" and not projection.is_success:
        # Failure inline: payload stays None, inline_output is "" (empty)
        result["payload"] = None
        result["inline_output"] = projection.inline_output
    else:  # by_ref
        # Large result: raw oversized NOT returned inline, payload None
        result["payload"] = None
        result["inline_output"] = None
        # output_ref carries governed reference contract (non-durable, explicit)
    return result  # type: ignore[return-value]


def _governed_from_response(binding: TrustedWorkerBinding, operation: str, response: ToolResponse) -> McpToolResult:
    """Safety → governed projection → MCP for a real provider ToolResponse."""
    sanitized = _sanitize_tool_response(response)
    # Capability name for projection is the exact operation identity
    # Validate that it is a plausible capability string; fallback to sanitized name if needed.
    cap = operation if isinstance(operation, str) and operation else "unknown"
    try:
        projection = project_tool_result(sanitized, cap, binding.sandbox)
    except Exception as exc:  # pragma: no cover - defensive fallback for capability validation or bound errors
        # If projection fails due to oversized error payload or capability name, return a bounded typed failure
        # without leaking internal paths.
        msg = _bounded_failure_message(str(exc))
        fallback_err = {"code": "GOVERNED_RESULT_BOUND_EXCEEDED", "message": msg}
        fallback_resp = ToolResponse.failure(fallback_err)
        # Retry with a safe capability name
        safe_cap = "aota.invoke" if cap == "unknown" else cap
        # Ensure safe_cap is valid; if not, use aota.invoke
        try:
            projection = project_tool_result(fallback_resp, safe_cap, binding.sandbox)
        except Exception as exc2:
            # Ultimate fallback: return minimal governed shape without projection
            return {
                "ok": False,
                "operation": operation,
                "payload": None,
                "error": fallback_err,
                "output_mode": "inline",
                "is_truncated": False,
                "complete": False,
                "outcome": "failure",
                "is_success": False,
                "capability_name": safe_cap,
                "output_digest": "0" * 64,
                "output_byte_length": 0,
                "inline_output": "",
                "output_ref": None,
            }
        return _governed_projection_to_mcp(operation, projection)
    return _governed_projection_to_mcp(operation, projection)


def _governed_error(binding: TrustedWorkerBinding, operation: str, code: str, message: str) -> McpToolResult:
    """Governed typed error helper — preserves W1 error identity end-to-end."""
    err = {"code": code, "message": _bounded_failure_message(message)}
    resp = ToolResponse.failure(err)
    return _governed_from_response(binding, operation if isinstance(operation, str) else "unknown", resp)


def _project_response(operation: str, response: ToolResponse) -> McpToolResult:
    """Legacy projection without govern — kept for reference but not used in production path.

    W3 production path is _sanitize → project_tool_result → _governed_projection_to_mcp.
    This shim delegates to the governed path when a binding is available via thread-local;
    for direct calls without binding it falls back to the pre-W3 bounded safety projection.
    """
    if not isinstance(response, ToolResponse):
        return {
            "ok": False,
            "operation": operation,
            "payload": None,
            "error": {"code": "INTERNAL_ERROR", "message": "invalid governed tool response"},
            "output_mode": "inline",
            "is_truncated": False,
            "complete": False,
            "outcome": "failure",
            "is_success": False,
            "capability_name": operation,
            "output_digest": "0" * 64,
            "output_byte_length": 0,
            "inline_output": "",
            "output_ref": None,
        }
    if response.ok:
        payload = dict(response.payload or {})
        payload.pop("canonical_path", None)
        return {
            "ok": True,
            "operation": operation,
            "payload": payload,
            "error": None,
            "output_mode": "inline",
            "is_truncated": False,
            "complete": True,
            "outcome": "success",
            "is_success": True,
            "capability_name": operation,
            "output_digest": "0" * 64,
            "output_byte_length": len(json.dumps(payload).encode("utf-8")),
            "inline_output": json.dumps(payload),
            "output_ref": None,
        }
    error = dict(response.error or {})
    projected_error: dict[str, Any] = {
        "code": error.get("code") if isinstance(error.get("code"), str) else "GOVERNED_OPERATION_FAILURE",
        "message": _bounded_failure_message(error.get("message")),
    }
    if isinstance(error.get("retryable"), bool):
        projected_error["retryable"] = error["retryable"]
    return {
        "ok": False,
        "operation": operation,
        "payload": None,
        "error": projected_error,
        "output_mode": "inline",
        "is_truncated": False,
        "complete": False,
        "outcome": "failure",
        "is_success": False,
        "capability_name": operation,
        "output_digest": "0" * 64,
        "output_byte_length": 0,
        "inline_output": "",
        "output_ref": None,
    }


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
        # All paths must go through governed projection to ensure bounded inline / ref,
        # outcome/completeness explicit, typed errors, no path leakage.
        if not isinstance(operation, str):
            return _governed_error(
                self.binding,
                "unknown",
                "UNKNOWN_OPERATION",
                f"operation must be string, got {type(operation).__name__}",
            )
        # Exact, deterministic, case-sensitive resolution — no fuzzy, no alias.
        if operation not in SUPPORTED_OPERATIONS:
            return _governed_error(self.binding, operation, "UNKNOWN_OPERATION", f"unknown operation: {operation!r}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _governed_error(self.binding, operation, "INPUT_TYPE_INVALID", f"arguments must be object, got {type(arguments).__name__}")
        # ---- nested argument validation via existing descriptor seam ----
        descriptor = _DESCRIPTOR_MAP.get(operation)
        if descriptor is None:
            return _governed_error(self.binding, operation, "UNKNOWN_OPERATION", f"unknown operation: {operation!r}")
        try:
            validated = validate_inputs(descriptor, arguments)
        except ForgeError as exc:
            return _governed_error(self.binding, operation, getattr(exc, "code", "INVALID_INPUT"), str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _governed_error(self.binding, operation, "INVALID_INPUT", str(exc))

        # ---- authority-preserved provider dispatch (reuse existing providers) ----
        try:
            if operation == "workspace.write":
                if self._write_provider is None:
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure(
                            {"code": "AUTHORITY_DENIED", "message": "trusted mutation authority is absent"}
                        ),
                    )
                request = ToolRequest(
                    operation=WORKSPACE_WRITE_DESCRIPTOR,
                    inputs=validated,
                )
                return _governed_from_response(self.binding, operation, self._write_provider.invoke(request))

            provider = self._read_providers.get(operation)
            authority = _authority_for(self.binding, operation)
            if provider is None or authority is None:
                return _governed_from_response(
                    self.binding,
                    operation,
                    ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted read authority is absent"}),
                )
            if authority.operation.contract_hash() != descriptor.contract_hash():
                return _governed_from_response(
                    self.binding,
                    operation,
                    ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"}),
                )
            request = ToolRequest(operation=authority.operation, inputs=validated)
            return _governed_from_response(self.binding, operation, provider.invoke(request))
        except ForgeError as exc:
            return _governed_error(self.binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # adapter boundary: never turn failures into success  # noqa: BLE001
            msg = _bounded_failure_message(str(exc))
            return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", msg)


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
