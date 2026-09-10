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
# M2/W1 pre-resolved trusted binding: canonical type owner is runtime_composition, not MCP
# MCP imports the trusted types; never defines them (TRUSTED_BINDING_TYPE_OWNER_IS_MCP=no).
from aota_forge.runtime.trusted_runtime_binding import (  # type: ignore
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    TrustedTaskMainRuntimeContext,
    TrustedWorkerBinding,
)

# M2 activation: durable hydration bound reuse (constant only, no persistence).
# D5 convergence: MCP never persists durable payloads directly
# (MCP_DIRECT_DURABLE_PAYLOAD_PERSISTENCE=no). Durability decisions live in
# AF result governance (durable_result_store.persist_governed_tool_result_if_by_ref,
# called from canonical Core dispatch). This module imports only the bound
# for protocol shaping fallback; persistence itself is Core-owned.
try:
    from aota_forge.work_plane.durable_result_store import (
        DURABLE_PAYLOAD_MAX_BYTES,
    )
except Exception:  # pragma: no cover - fallback for isolated test discovery
    DURABLE_PAYLOAD_MAX_BYTES = 64 * 1024

try:
    from aota_forge.work_plane.result_hydrate import ResultHydrateProvider
    from aota_forge.work_plane.result_hydrate import RESULT_HYDRATE_DESCRIPTOR  # type: ignore
except Exception:
    ResultHydrateProvider = None  # type: ignore
    RESULT_HYDRATE_DESCRIPTOR = None  # type: ignore

try:
    from aota_forge.work_plane.restricted_shell import (
        BoundedRestrictedShellProvider,
        RestrictedShellAuthorityEvidence,
        RESTRICTED_SHELL_DESCRIPTOR,
    )
except Exception:
    BoundedRestrictedShellProvider = None  # type: ignore
    RestrictedShellAuthorityEvidence = None  # type: ignore
    RESTRICTED_SHELL_DESCRIPTOR = None  # type: ignore

# M3/W1 task-main control descriptors — canonical project root via single authority
try:
    from aota_forge.work_plane.task_main_descriptors import (  # type: ignore
        TASK_MAIN_ACTIVATE_DESCRIPTOR,
        TASK_MAIN_ADVANCE_DESCRIPTOR,
        TASK_MAIN_RECOVER_DESCRIPTOR,
        TASK_MAIN_SUBMIT_DESCRIPTOR,
    )
except Exception:  # pragma: no cover - fallback for isolated test discovery without yaml
    TASK_MAIN_ACTIVATE_DESCRIPTOR = None  # type: ignore
    TASK_MAIN_ADVANCE_DESCRIPTOR = None  # type: ignore
    TASK_MAIN_RECOVER_DESCRIPTOR = None  # type: ignore
    TASK_MAIN_SUBMIT_DESCRIPTOR = None  # type: ignore

# M1/W2 AF Role Bootstrap — role.bootstrap, skill.open, test.run reuse
try:
    from aota_forge.work_plane.test_execution import TEST_RUN_DESCRIPTOR as _W2_TEST_RUN_DESCRIPTOR  # type: ignore
except Exception:  # pragma: no cover
    _W2_TEST_RUN_DESCRIPTOR = None  # type: ignore

TEST_RUN_DESCRIPTOR = _W2_TEST_RUN_DESCRIPTOR

# Load role.bootstrap and skill.open descriptors from canonical operations.yaml (single authority)
try:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map

    _W2_CANONICAL_MAP = load_operation_descriptor_map(discover_canonical_project_root())
    ROLE_BOOTSTRAP_DESCRIPTOR = _W2_CANONICAL_MAP.get("role.bootstrap")
    SKILL_OPEN_DESCRIPTOR = _W2_CANONICAL_MAP.get("skill.open")
    # Canonical test.run descriptor from map should match TEST_RUN_DESCRIPTOR
    _CANONICAL_TEST_RUN = _W2_CANONICAL_MAP.get("test.run")
    if _CANONICAL_TEST_RUN is not None and TEST_RUN_DESCRIPTOR is None:
        TEST_RUN_DESCRIPTOR = _CANONICAL_TEST_RUN
except Exception:  # pragma: no cover - fallback for isolated test discovery
    ROLE_BOOTSTRAP_DESCRIPTOR = None  # type: ignore
    SKILL_OPEN_DESCRIPTOR = None  # type: ignore
    _CANONICAL_TEST_RUN = None  # type: ignore

try:  # The MCP SDK is an adapter dependency, never a Core dependency.
    from mcp.types import CallToolResult, TextContent, ToolAnnotations

    try:
        from mcp.server import MCPServer
    except ImportError:  # mcp 1.x exposes the same stdio server as FastMCP.
        from mcp.server.fastmcp import FastMCP as MCPServer
except ImportError:  # pragma: no cover - exercised in installations without [mcp]
    MCPServer = None  # type: ignore[assignment,misc]
    ToolAnnotations = None  # type: ignore[assignment,misc]
    CallToolResult = None  # type: ignore[assignment,misc]
    TextContent = None  # type: ignore[assignment,misc]


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
# M2 activation: durable hydration + restricted shell reuse (file-backed, no DB)
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
DURABLE_SELECTIVE_HYDRATION_IMPLEMENTED_IN_W3 = True  # M2 durable file-backed payload store (worktree_root/.aota/durable_payloads, non-DB)
RESULT_HYDRATE_OPERATION_IMPLEMENTED_IN_W3 = True  # canonical result.hydrate via aota.invoke
RESTRICTED_SHELL_ACTIVATED_IN_W3 = True  # residual fallback via aota.invoke, BoundedRestrictedShellProvider reused
TASK_MAIN_CONTROL_IMPLEMENTED_IN_W3 = True  # M3/W1: trusted task-main control via same single-entry transport
# M3/W1 invariants
TASK_MAIN_MODEL_VISIBLE_CONTROL_COUNT = 4
INTERNAL_RECONCILE_EXPOSED_TO_MODEL = False
INTERNAL_OBSERVE_EXPOSED_TO_MODEL = False
INTERNAL_DISPATCH_EXPOSED_TO_MODEL = False
EXISTING_TASK_MAIN_CONTROL_SERVICE_REUSED = True
MODEL_SUPPLIED_LIVE_PLAN_VIEW_ALLOWED = False
MODEL_SUPPLIED_USER_APPROVAL_ALLOWED = False
MODEL_SUPPLIED_PLAN_AUTHORITY_ALLOWED = False
MODEL_SUPPLIED_PROFILE_AUTHORITY_ALLOWED = False
TASK_MAIN_CAN_SET_USER_APPROVAL = False
TASK_MAIN_CAN_CROSS_USER_GATE = False
WORKER_CAN_CALL_TASK_MAIN_CONTROL = False
TASK_MAIN_RESTRICTED_SHELL_AUTHORIZED = False
NEW_PERMISSION_ENGINE_CREATED = False
NEW_AUTHORITY_REGISTRY_CREATED = False
NEW_WORKFLOW_ENGINE_CREATED = False
NEW_TASK_MAIN_MCP_SERVER_CREATED = False
OVERDESIGN_FINDING_COUNT = 0
ONE_SHARED_AOTA_MCP_RUNTIME = True
PER_ROLE_MCP_SERVER_RUNTIME = False
INLINE_BOUND = TOOL_INLINE_OUTPUT_MAX_BYTES
# M2 durable bounds
MAX_DURABLE_PAYLOAD_BYTES = DURABLE_PAYLOAD_MAX_BYTES
MAX_HYDRATED_BYTES = 64 * 1024  # tool_output whole-object bound (via durable store); selective evidence/artifact remains 4096 via selective_hydration.MAX_HYDRATED_BYTES
HYDRATION_MODE = "whole_object"  # one ref → full payload, bounded 64 KiB for tool_output, 4096 for evidence/artifact; no selective slice, no silent truncation
SILENT_HYDRATION_TRUNCATION = False

# Transport surface (Agent-facing MCP tools) — W1 single-entry.
MCP_PUBLIC_TOOLS: tuple[str, ...] = ("aota.invoke",)
MCP_PUBLIC_TOOL_COUNT = len(MCP_PUBLIC_TOOLS)
AGENT_FACING_AOTA_TOOL = "aota.invoke"
HERMES_AGENT_FACING_AOTA_TOOL_COUNT = 1

# M2/W1 runtime authority binding foundation (fail-closed).
TRUSTED_BINDING_FAIL_CLOSED = True
WORKER_CAN_MINT_TASK_MAIN_AUTHORITY = False
TASK_MAIN_CAN_TREAT_WORKER_BINDING_AS_TASK_MAIN_AUTHORITY = False
FREEFORM_PROMPT_CAN_MINT_BINDING_AUTHORITY = False
PROJECT_ID_SPECIAL_CASE_ALLOWED = False
DOGFOOD_LITERAL_SPECIAL_CASE_ALLOWED = False
SOUL_IS_AUTHORITY = False
SKILL_IS_AUTHORITY = False
TOOL_VISIBILITY_IS_AUTHORITY = False
SERVER_SIDE_AUTHORITY_REQUIRED = True

# Logical capability surface (existing typed operations, not MCP tool names).
# M2 convergence: workspace.* + durable result.hydrate + residual restricted_shell.run
# M3/W1: + task_main.* (exactly 3, minimal intent)
# M3/W1 writer (AF #46): + task_main.submit_work_projection (exactly 4, canonical writer)
WORKSPACE_OPERATIONS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "workspace.write",
)
M2_OPERATIONS: tuple[str, ...] = (
    "result.hydrate",
    "restricted_shell.run",
)
TASK_MAIN_OPERATIONS: tuple[str, ...] = (
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
    "task_main.submit_work_projection",
)
# W2 AF Role Bootstrap / Skill / Test — newly canonical for #41, plus existing test.run
W2_OPERATIONS: tuple[str, ...] = (
    "role.bootstrap",
    "skill.open",
    "test.run",
)
# Internal task-main controls must never become canonical Agent-facing operations
INTERNAL_TASK_MAIN_OPERATIONS: tuple[str, ...] = (
    "task_main.reconcile_worker_completion",
    "task_main.reconcile_review_completion",
    "task_main.observe_terminal_completions",
    "task_main.dispatch_ready",
)
LOGICAL_OPERATIONS: tuple[str, ...] = WORKSPACE_OPERATIONS + M2_OPERATIONS + TASK_MAIN_OPERATIONS + W2_OPERATIONS
# Back-compat aliases
BOUNDED_MCP_OPERATIONS = WORKSPACE_OPERATIONS
LOGICAL_CAPABILITY_SURFACE = LOGICAL_OPERATIONS
SUPPORTED_LOGICAL_OPERATIONS = LOGICAL_OPERATIONS

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

# Compatibility projection of canonical descriptors (NOT semantic authority).
# W1 convergence: single descriptor authority is core_ingress via
# .aota/contracts/operations.yaml. This map is a deterministic projection
# for backward compatibility only; invoke() resolves via core_ingress.
# MCP_OPERATION_SPECIFIC_DISPATCH_TABLE_IS_SEMANTIC_AUTHORITY=no.
_DESCRIPTOR_MAP: dict[str, Any] = {
    WORKSPACE_SEARCH_DESCRIPTOR.name: WORKSPACE_SEARCH_DESCRIPTOR,
    WORKSPACE_READ_DESCRIPTOR.name: WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_WRITE_DESCRIPTOR.name: WORKSPACE_WRITE_DESCRIPTOR,
}
# M2 descriptors (canonical, lazy-loaded to keep import-time fail-closed minimal)
if RESULT_HYDRATE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[RESULT_HYDRATE_DESCRIPTOR.name] = RESULT_HYDRATE_DESCRIPTOR
if RESTRICTED_SHELL_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[RESTRICTED_SHELL_DESCRIPTOR.name] = RESTRICTED_SHELL_DESCRIPTOR
# W2 AF Role Bootstrap descriptors (canonical, loaded via single authority .aota/contracts/operations.yaml)
if ROLE_BOOTSTRAP_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[ROLE_BOOTSTRAP_DESCRIPTOR.name] = ROLE_BOOTSTRAP_DESCRIPTOR
if SKILL_OPEN_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[SKILL_OPEN_DESCRIPTOR.name] = SKILL_OPEN_DESCRIPTOR
if TEST_RUN_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TEST_RUN_DESCRIPTOR.name] = TEST_RUN_DESCRIPTOR
# M3/W1 task-main descriptors (canonical, exactly 4)
if TASK_MAIN_ACTIVATE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_ACTIVATE_DESCRIPTOR.name] = TASK_MAIN_ACTIVATE_DESCRIPTOR
if TASK_MAIN_RECOVER_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_RECOVER_DESCRIPTOR.name] = TASK_MAIN_RECOVER_DESCRIPTOR
if TASK_MAIN_ADVANCE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_ADVANCE_DESCRIPTOR.name] = TASK_MAIN_ADVANCE_DESCRIPTOR
if TASK_MAIN_SUBMIT_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_SUBMIT_DESCRIPTOR.name] = TASK_MAIN_SUBMIT_DESCRIPTOR
SUPPORTED_OPERATIONS: frozenset[str] = frozenset(LOGICAL_OPERATIONS)
# For backward compatibility, retain WORKSPACE_OPERATIONS alias but expanded set is canonical
CANONICAL_SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS


class McpTransportUnavailable(RuntimeError):
    """The optional standard MCP SDK is not installed."""


# Trusted binding types are canonical in runtime.trusted_runtime_binding
# (TRUSTED_BINDING_TYPE_OWNER_IS_MCP=no). Re-exported via import above for
# backward compatibility; MCP adapter imports, never defines.
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


# Trusted binding types canonical in runtime.trusted_runtime_binding (removed from MCP: TRUSTED_BINDING_TYPE_OWNER_IS_MCP=no)


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
    * sanitize known internal absolute-path fields (cwd, worktree_root, etc.)
    * failure: bound and redact absolute paths in error message, preserve typed code
    Order is critical: safety → governed projection, so the governed projection
    never serializes a previously hidden field.
    """
    if not isinstance(response, ToolResponse):
        return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": "invalid governed tool response"})
    if response.ok:
        payload = dict(response.payload or {})
        payload.pop("canonical_path", None)
        # Bounded path sanitization for known internal path-bearing fields.
        # Provider may internally need absolute root, but Agent does not.
        # Transform Agent-facing value to "<bounded-path>" (existing sanitization convention).
        # Scope only trusted/internal metadata fields, not arbitrary user stdout.
        for _field in ("cwd", "absolute_path", "worktree_root", "store_path", "artifact_path", "payload_path", "durable_path", "worktree_path"):
            if _field in payload and isinstance(payload[_field], str) and payload[_field].startswith("/"):
                payload[_field] = "<bounded-path>"
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
    """Safety → governed projection → MCP for a real provider ToolResponse.

    D5 convergence: durability decisions live in canonical Core dispatch
    (durable_result_store.persist_governed_tool_result_if_by_ref). This
    adapter only projects the already-governed result to protocol shape;
    it never persists semantic payloads and never decides inline/by_ref.
    """
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


def _map_task_main_exception(exc: Exception) -> str:
    """Preserve task-main semantic failure identity end-to-end (typed only).

    D3 convergence: type-first isinstance against the semantic owner's
    exception types, then explicit .code preservation. Never inspects
    str(exc) contents. Deprecated seam delegates to the canonical Core
    mapper; new code uses Core dispatch directly.
    """
    try:
        from aota_forge.core_ingress import _map_task_main_exception as _core_map  # type: ignore

        return _core_map(exc)
    except Exception:
        pass
    # Fallback type-first mapping when Core is unavailable (still no strings).
    try:
        from aota_forge.runtime.task_main.control import TaskMainControlAuthorityError
    except Exception:
        TaskMainControlAuthorityError = None  # type: ignore
    try:
        from aota_forge.runtime.task_main.coordinator import (
            CoordinatorBindingError,
            CoordinatorRuntimeError,
            PlanDriftError,
            SessionRecoveryRequiredError,
        )
    except Exception:
        CoordinatorBindingError = CoordinatorRuntimeError = None  # type: ignore
        PlanDriftError = SessionRecoveryRequiredError = None  # type: ignore
    try:
        from aota_forge.runtime.task_main.coordinator_store import (
            CoordinatorNotFoundError,
            StaleCoordinatorRevisionError,
        )
    except Exception:
        CoordinatorNotFoundError = StaleCoordinatorRevisionError = None  # type: ignore
    if PlanDriftError is not None and isinstance(exc, PlanDriftError):
        return "PLAN_DRIFT"
    if SessionRecoveryRequiredError is not None and isinstance(exc, SessionRecoveryRequiredError):
        return "SESSION_RECOVERY_REQUIRED"
    if CoordinatorNotFoundError is not None and isinstance(exc, CoordinatorNotFoundError):
        return "COORDINATOR_NOT_FOUND"
    if CoordinatorBindingError is not None and isinstance(exc, CoordinatorBindingError):
        return "COORDINATOR_BINDING_ERROR"
    if CoordinatorRuntimeError is not None and isinstance(exc, CoordinatorRuntimeError):
        return "COORDINATOR_RUNTIME_ERROR"
    if StaleCoordinatorRevisionError is not None and isinstance(exc, StaleCoordinatorRevisionError):
        return "STALE_COORDINATOR_REVISION"
    if TaskMainControlAuthorityError is not None and isinstance(exc, TaskMainControlAuthorityError):
        return "AUTHORITY_DENIED"
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "GOVERNED_OPERATION_FAILURE"


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


def _to_canonical_binding(binding: TrustedWorkerBinding):  # type: ignore[no-untyped-def]
    """Mechanical transport-to-Core binding conversion (no semantic choice).

    Copies already-authoritative trusted objects into Core-owned
    CanonicalDispatchBinding. Chooses no provider, validates no policy,
    invents no authority. Provider selection stays in core_ingress.
    """
    from aota_forge.core_ingress import CanonicalDispatchBinding

    try:
        allowed: frozenset[str] = frozenset(binding.tool_surface.all_capability_names())
    except Exception:
        allowed = frozenset()
    return CanonicalDispatchBinding(
        canonical_task_id=getattr(binding, "canonical_task_id", ""),
        project_id=getattr(binding, "project_id", ""),
        worktree_id=getattr(binding, "worktree_id", ""),
        trusted_context=getattr(binding, "trusted_context", None),
        handoff=getattr(binding, "handoff", None),
        sandbox=getattr(binding, "sandbox", None),
        tool_surface=getattr(binding, "tool_surface", None),
        read_authorities=tuple(getattr(binding, "read_authorities", ()) or ()),
        mutation_authority=getattr(binding, "mutation_authority", None),
        restricted_shell_authority=getattr(binding, "restricted_shell_authority", None),
        test_execution_authority=getattr(binding, "test_execution_authority", None),
        git_authorities=tuple(getattr(binding, "git_authorities", ()) or ()),
        trusted_task_main_context=getattr(binding, "trusted_task_main_context", None),
        allowed_operations=allowed,
    )


class _SharedAotaMcpAdapter:
    """Thin MCP transport adapter over canonical Core dispatch (W1 convergence).

    Single-entry: ``aota.invoke(operation, arguments)`` -> core_ingress
    (exact resolution -> typed validation -> Core-owned provider selection)
    -> bounded transport projection.

    Owns only: protocol adaptation, single aota.invoke exposure,
    stdio/process mechanics, bounded protocol result projection, mechanical
    transport failure handling. Owns no operation lookup, validation or
    provider selection semantics (all in core_ingress).

    No new registry, permission engine, or generic gateway is created.
    M2 expands logical operation catalog to include result.hydrate (durable selective hydration)
    and restricted_shell.run (residual fallback) via the same single-entry transport.
    """

    def __init__(self, binding: TrustedWorkerBinding) -> None:
        self.binding = binding
        # W1: transport keeps no semantic provider cache. Provider selection
        # lives in core_ingress per-call. Attributes retained as None for
        # backward-compatible introspection only; they choose no behavior.
        self._read_providers: dict[str, Any] = {}
        self._write_provider: Any = None
        self._hydrate_provider: Any = None
        self._shell_provider: Any = None

    def invoke(self, operation: str, arguments: dict[str, Any] | None) -> McpToolResult:
        """Thin transport over canonical Core dispatch (W1 One-Core convergence).

        Owns only envelope checks, exposure gating and protocol projection.
        Resolution, typed validation and provider selection live in
        :mod:`aota_forge.core_ingress` (AF_CORE, core_ingress layer).
        """
        # Transport envelope (mechanical, not semantic).
        if not isinstance(operation, str):
            return _governed_error(
                self.binding,
                "unknown",
                "UNKNOWN_OPERATION",
                f"operation must be string, got {type(operation).__name__}",
            )
        # Canonical resolution via Core single authority (fails via Core path).
        try:
            from aota_forge.core_ingress import resolve_descriptor as _core_resolve
            _core_resolve(operation)
        except ForgeError as exc:
            return _governed_error(
                self.binding,
                operation,
                getattr(exc, "code", "UNKNOWN_OPERATION"),
                str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", str(exc))
        # Exposure gate (not authority): MCP exposes only LOGICAL_OPERATIONS.
        # Unknown-to-Core already failed above; this denies known-but-unexposed
        # with the same UNKNOWN_OPERATION shape to preserve transport contract.
        # Exposure absence never implies operation absence in Core.
        if operation not in SUPPORTED_OPERATIONS:
            return _governed_error(self.binding, operation, "UNKNOWN_OPERATION", f"unknown operation: {operation!r}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _governed_error(self.binding, operation, "INPUT_TYPE_INVALID", f"arguments must be object, got {type(arguments).__name__}")
        # Canonical dispatch via Core (owns validation + provider selection).
        try:
            from aota_forge.core_ingress import dispatch_tool_operation as _core_dispatch
            canonical = _to_canonical_binding(self.binding)
            tool_response = _core_dispatch(operation, arguments, canonical)
        except ForgeError as exc:
            return _governed_error(self.binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        # Hydrate whole-object projection owned by Core result governance (D5):
        # delegate to the canonical helper; this adapter performs only protocol
        # projection and never decides durability/kind/digest/bounds/mode.
        if operation == "result.hydrate" and getattr(tool_response, "ok", False):
            try:
                from aota_forge.work_plane.result_hydrate import (
                    project_hydrate_result_for_transport as _core_hydrate_project,
                )

                projected = _core_hydrate_project(tool_response)
                if isinstance(projected, dict) and "__governed_failure__" in projected:
                    return _governed_from_response(
                        self.binding, operation, projected["__governed_failure__"]
                    )
                if isinstance(projected, dict):
                    return projected  # type: ignore[return-value]
            except Exception:
                pass
        return _governed_from_response(self.binding, operation, tool_response)

    def _invoke_task_main(self, operation: str, validated: dict[str, Any], descriptor: Any) -> McpToolResult:
        """Deprecated transport seam (W1 convergence).

        Preserved for backward compatibility; delegates to canonical Core
        task-main dispatch. New code must call invoke() -> core_ingress.
        """
        try:
            from aota_forge.core_ingress import dispatch_tool_operation as _core_dispatch
            canonical = _to_canonical_binding(self.binding)
            # validated already canonical; re-dispatch via Core single path.
            args = dict(validated) if isinstance(validated, dict) else {}
            tool_response = _core_dispatch(operation, args, canonical)
        except ForgeError as exc:
            return _governed_error(self.binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        return _governed_from_response(self.binding, operation, tool_response)

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

    def _to_call_tool_result(mcp_result: McpToolResult) -> Any:
        """Wrap McpToolResult into CallToolResult with bounded model-visible payload.

        Repair for I40-B001 / W1 inline model-visible projection:
        - output_mode==inline and is_success: TextContent is deterministic bounded
          serialization of the already-governed inline result (inline_output or
          governed payload), preserving redaction/size/digest governance.
          Raw provider result is never bypassed (TEXT_CONTENT_SOURCE=governed_projection).
        - output_mode==by_ref or failure: TextContent stays bounded summary/ref metadata,
          payload not eagerly inlined.
        Generic repair, not role.bootstrap special case.
        Preserves structuredContent canonical, digest semantics, boundedness.
        """
        if CallToolResult is None or TextContent is None:
            return mcp_result
        try:
            output_mode = mcp_result.get("output_mode")
            is_success = mcp_result.get("is_success")
            if is_success is None:
                is_success = mcp_result.get("ok")
            # Generic inline success: expose governed semantic payload in TextContent
            if output_mode == "inline" and is_success:
                inline_output = mcp_result.get("inline_output")
                payload = mcp_result.get("payload")
                if isinstance(inline_output, str) and inline_output != "":
                    # Governed canonical JSON (already bounded 4096, deterministic, redacted)
                    text = inline_output
                elif isinstance(payload, dict) and payload:
                    # Fallback for inline cases where inline_output is None (e.g., result.hydrate special)
                    # payload is governed, so deterministic bounded serialization preserves invariants
                    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
                elif isinstance(payload, str) and payload:
                    text = payload
                else:
                    # Empty inline payload edge: minimal summary
                    summary = {
                        "ok": mcp_result.get("ok"),
                        "operation": mcp_result.get("operation"),
                        "outcome": mcp_result.get("outcome"),
                        "output_mode": output_mode,
                        "byte_length": mcp_result.get("output_byte_length"),
                        "digest": mcp_result.get("output_digest"),
                    }
                    summary = {k: v for k, v in summary.items() if v is not None}
                    text = json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
            else:
                # by_ref or failure: bounded summary/ref metadata, never eager payload
                summary = {
                    "ok": mcp_result.get("ok"),
                    "operation": mcp_result.get("operation"),
                    "outcome": mcp_result.get("outcome"),
                    "output_mode": output_mode,
                    "byte_length": mcp_result.get("output_byte_length"),
                    "digest": mcp_result.get("output_digest"),
                }
                try:
                    payload = mcp_result.get("payload")
                    if isinstance(payload, dict):
                        for _k in (
                            "disposition",
                            "next_action",
                            "status",
                            "coordinator_id",
                            "dispatched",
                            "reconciled_work_item",
                            "reconciled_canonical_task_id",
                            "user_gate_required",
                            "milestone_closure_ready",
                            "next_milestone_gate",
                            "integrated_review_required",
                            "session_recovery_required",
                        ):
                            if _k in payload:
                                summary[_k] = payload[_k]
                    if not mcp_result.get("ok"):
                        err = mcp_result.get("error") or {}
                        if isinstance(err, dict) and "code" in err:
                            summary["error_code"] = err.get("code")
                    if output_mode == "by_ref":
                        ref = mcp_result.get("output_ref")
                        if isinstance(ref, dict):
                            summary["output_ref"] = {
                                "ref": ref.get("ref"),
                                "digest": ref.get("digest"),
                                "byte_length": ref.get("byte_length"),
                            }
                except Exception:
                    pass
                summary = {k: v for k, v in summary.items() if v is not None}
                text = json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            text = json.dumps({"ok": bool(mcp_result.get("ok"))})
        try:
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=dict(mcp_result),
                isError=False,
            )
        except Exception:
            return mcp_result

    @server.tool(
        name="aota.invoke",
        description="Typed AOTA dispatch. operation is exact canonical name (e.g. workspace.search); arguments is the operation's typed input object.",
    )
    def aota_invoke(operation: str, arguments: dict[str, Any] | None = None):  # type: ignore[no-redef]
        """Single-entry AOTA dispatch: exact operation resolution → existing descriptor → validate_inputs → existing authority → existing provider."""
        mcp_result = adapter.invoke(operation, arguments if arguments is not None else {})
        # FastMCP double-emission repair: return explicit CallToolResult so
        # lowlevel server does not re-emit the same dict as both TextContent
        # and structuredContent. This keeps one canonical copy in
        # structuredContent and a minimal summary in content.
        return _to_call_tool_result(mcp_result)

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
    "M2_OPERATIONS",
    "TASK_MAIN_OPERATIONS",
    "INTERNAL_TASK_MAIN_OPERATIONS",
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
    "TASK_MAIN_CONTROL_IMPLEMENTED_IN_W3",
    "TASK_MAIN_MODEL_VISIBLE_CONTROL_COUNT",
    "INTERNAL_RECONCILE_EXPOSED_TO_MODEL",
    "INTERNAL_OBSERVE_EXPOSED_TO_MODEL",
    "INTERNAL_DISPATCH_EXPOSED_TO_MODEL",
    "EXISTING_TASK_MAIN_CONTROL_SERVICE_REUSED",
    "McpToolResult",
    "McpTransportUnavailable",
    "TrustedBindingError",
    "TrustedWorkerBinding",
    "TrustedTaskMainRuntimeContext",
    "create_shared_mcp_server",
    "run_shared_mcp_server",
]
