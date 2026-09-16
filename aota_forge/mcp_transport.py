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

W5 agent runtime surface repair (AF #49 M1/W5):

* Canonical normal-path handoff/task lifecycle operations (handoff.write,
  handoff.open, task.start, task.return) join the Agent-visible exposure
  catalog through the same single ``aota.invoke`` transport.
* EXPOSURE_IS_NOT_AUTHORITY=yes — server-side role/authority validation still
  decides execution permission; visibility never grants authority.
* ROLE_SURFACE ⊆ CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG, unknown
  operations still fail closed with UNKNOWN_OPERATION.
* Governed by_ref results carry deterministic, bounded, model-visible
  hydration claims (canonical ToolOutputRef identity + result.hydrate
  instruction) so the model can consume them through the existing
  hydration operation without guessing (no new hydration protocol).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, TypedDict

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
    # W5: canonical normal-path lifecycle descriptors join the exposure catalog.
    # The canonical operation descriptor/registry remains the single semantic
    # authority; this map is an exposure-boundary projection only.
    HANDOFF_WRITE_DESCRIPTOR = _W2_CANONICAL_MAP.get("handoff.write")
    HANDOFF_OPEN_DESCRIPTOR = _W2_CANONICAL_MAP.get("handoff.open")
    TASK_START_DESCRIPTOR = _W2_CANONICAL_MAP.get("task.start")
    TASK_RETURN_DESCRIPTOR = _W2_CANONICAL_MAP.get("task.return")
    # Canonical test.run descriptor from map should match TEST_RUN_DESCRIPTOR
    _CANONICAL_TEST_RUN = _W2_CANONICAL_MAP.get("test.run")
    if _CANONICAL_TEST_RUN is not None and TEST_RUN_DESCRIPTOR is None:
        TEST_RUN_DESCRIPTOR = _CANONICAL_TEST_RUN
except Exception:  # pragma: no cover - fallback for isolated test discovery
    ROLE_BOOTSTRAP_DESCRIPTOR = None  # type: ignore
    SKILL_OPEN_DESCRIPTOR = None  # type: ignore
    HANDOFF_WRITE_DESCRIPTOR = None  # type: ignore
    HANDOFF_OPEN_DESCRIPTOR = None  # type: ignore
    TASK_START_DESCRIPTOR = None  # type: ignore
    TASK_RETURN_DESCRIPTOR = None  # type: ignore
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
# W5 (AF #49 M1/W5, I49-B001): canonical normal-path handoff/task lifecycle
# operations are Agent-visible through the same single aota.invoke transport.
# Exposure is an exposure boundary only; server-side role/authority validation
# still decides execution permission (EXPOSURE_IS_NOT_AUTHORITY=yes).
HANDOFF_TASK_OPERATIONS: tuple[str, ...] = (
    "handoff.write",
    "handoff.open",
    "task.start",
    "task.return",
)
# Internal task-main controls must never become canonical Agent-facing operations
INTERNAL_TASK_MAIN_OPERATIONS: tuple[str, ...] = (
    "task_main.reconcile_worker_completion",
    "task_main.reconcile_review_completion",
    "task_main.observe_terminal_completions",
    "task_main.dispatch_ready",
)
# AF #54 M5/W1: governed Git/project lifecycle operations are canonical
# Agent-visible through the same single aota.invoke transport. Exposure is an
# exposure boundary only; execution still requires server-side trusted Git
# operation-authority evidence (core_ingress + GitOperationAuthorityEvidence).
GIT_READ_OPERATIONS: tuple[str, ...] = ("git.status", "git.diff")
GIT_LIFECYCLE_OPERATIONS: tuple[str, ...] = ("git.checkpoint", "git.integrate", "git.push")
GIT_OPERATIONS: tuple[str, ...] = GIT_READ_OPERATIONS + GIT_LIFECYCLE_OPERATIONS
# AF #54 M5/W2: the four canonical Plan-bound GitHub governance operations
# ride the same single-entry transport. Raw gh / generic github.api remain
# unreachable to the model; authority is server-side evidence only.
GITHUB_READ_OPERATIONS: tuple[str, ...] = ("github.issue.read", "github.issue.comments.read")
GITHUB_MUTATION_OPERATIONS: tuple[str, ...] = ("github.issue.update", "github.issue.comment.update")
GITHUB_OPERATIONS: tuple[str, ...] = GITHUB_READ_OPERATIONS + GITHUB_MUTATION_OPERATIONS
LOGICAL_OPERATIONS: tuple[str, ...] = (
    WORKSPACE_OPERATIONS + M2_OPERATIONS + TASK_MAIN_OPERATIONS + W2_OPERATIONS + HANDOFF_TASK_OPERATIONS + GIT_OPERATIONS + GITHUB_OPERATIONS
)
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
# AF #53 M2/W2: the public ``create_aota_invoke_dispatch`` factory exposes the
# same canonical single-entry callable the MCP server uses; it creates no
# second transport, tool or dispatch plane.
AOTA_INVOKE_DISPATCH_FACTORY_IS_CANONICAL_SINGLE_ENTRY = True
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
# W5 canonical handoff/task lifecycle descriptors (exposure projection only)
if HANDOFF_WRITE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[HANDOFF_WRITE_DESCRIPTOR.name] = HANDOFF_WRITE_DESCRIPTOR
if HANDOFF_OPEN_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[HANDOFF_OPEN_DESCRIPTOR.name] = HANDOFF_OPEN_DESCRIPTOR
if TASK_START_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_START_DESCRIPTOR.name] = TASK_START_DESCRIPTOR
if TASK_RETURN_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_RETURN_DESCRIPTOR.name] = TASK_RETURN_DESCRIPTOR
SUPPORTED_OPERATIONS: frozenset[str] = frozenset(LOGICAL_OPERATIONS)
# AF #59 M1: the #58 session-carried interactive approval gate is removed.
# Approval is a current server-side Plan fact re-read at the operation
# boundary (see composition/ref_scoped_authority.py for the unbound host
# session path); it is never carried by a session/instance binding.
APPROVAL_IS_CURRENT_SERVER_SIDE_FACT = True
APPROVAL_IS_SESSION_STATE = False
# For backward compatibility, retain WORKSPACE_OPERATIONS alias but expanded set is canonical
CANONICAL_SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS
# W5 exposure/canonical relationship: role surfaces must remain a subset of the
# canonical Agent-visible catalog; unknown/unregistered operations fail closed.
CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG: frozenset[str] = SUPPORTED_OPERATIONS
ROLE_SURFACE_SUBSET_CANONICAL_AGENT_VISIBLE_CATALOG = True
EXPOSURE_IS_NOT_AUTHORITY = True


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
    # W5 model-visible by_ref hydration contract (present on by_ref results)
    hydration: dict[str, Any] | None


# Trusted binding types canonical in runtime.trusted_runtime_binding (removed from MCP: TRUSTED_BINDING_TYPE_OWNER_IS_MCP=no)


def _by_ref_hydration_instruction(output_ref: object) -> dict[str, Any] | None:
    """Deterministic model-visible hydration claims + instruction for by_ref.

    Reuses the exact existing hydration contract (canonical ToolOutputRef
    identity + result.hydrate operation); no new ref envelope, no new
    hydration protocol. The persisted payload was written by canonical Core
    dispatch, so following these exact claims deterministically hydrates the
    original payload. Returns None when the canonical claims are incomplete.
    """
    if not isinstance(output_ref, dict):
        return None
    ref = output_ref.get("ref")
    digest = output_ref.get("digest")
    project_id = output_ref.get("project_id")
    worktree_id = output_ref.get("worktree_id")
    if not all(isinstance(value, str) and value for value in (ref, digest, project_id, worktree_id)):
        return None
    arguments: dict[str, Any] = {
        "ref": ref,
        "digest": digest,
        "project_id": project_id,
        "worktree_id": worktree_id,
    }
    byte_length = output_ref.get("byte_length")
    if isinstance(byte_length, int) and not isinstance(byte_length, bool) and byte_length >= 0:
        arguments["byte_length"] = byte_length
    return {
        "operation": "result.hydrate",
        "arguments": arguments,
        "reason": "result exceeds the bounded inline projection and is stored by reference",
        "instruction": (
            "call aota.invoke(operation=\"result.hydrate\", arguments=<arguments>) "
            "with these exact trusted claims to obtain the bounded payload"
        ),
    }


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
    # M3/W1-R1 F2: preserve bounded governed Work context for task-main
    # repair (fail-closed missing-projection errors must carry the
    # already-authorized semantics). Only these explicit keys; oversize still
    # fails closed downstream via TOOL_ERROR_INLINE_MAX_BYTES (no truncation).
    # AF #49 M1/W2 adds the authoritative work_context so the fail-closed
    # advance path itself can carry the trusted bounded Work source (or its
    # trusted by-ref identity) instead of semantic guesses.
    for _ctx_key in (
        "projection_required",
        "missing_governed_work_semantics",
        "work_context",
    ):
        try:
            _v = error.get(_ctx_key)
        except Exception:
            continue
        if _v is None:
            continue
        if _ctx_key == "missing_governed_work_semantics":
            if isinstance(_v, (list, tuple)) and all(isinstance(x, str) for x in _v) and len(_v) <= 64:
                projected_error[_ctx_key] = list(_v)
        elif _ctx_key == "work_context":
            if isinstance(_v, dict) and len(_v) <= 64:
                projected_error[_ctx_key] = dict(_v)
        else:
            if isinstance(_v, (list, tuple)) and len(_v) <= 64:
                # Shallow-validate list of {work_item_id, governed_work_semantics}.
                ok_items: list[Any] = []
                valid = True
                for _it in _v:
                    if not isinstance(_it, dict):
                        valid = False
                        break
                    if "work_item_id" not in _it or "governed_work_semantics" not in _it:
                        valid = False
                        break
                    ok_items.append(dict(_it))
                if valid:
                    projected_error[_ctx_key] = ok_items
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
        "hydration": None,
    }
    if projection.output_mode == "by_ref":
        # W5: by_ref results must be deterministically consumable from the
        # model-visible representation alone (no guessed operation names, no
        # operator intervention). Claims reuse the canonical ToolOutputRef
        # identity and the existing result.hydrate operation.
        result["hydration"] = _by_ref_hydration_instruction(result.get("output_ref"))
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
        session_ref=getattr(binding, "session_ref", ""),
        parent_session_ref=getattr(binding, "parent_session_ref", ""),
        run_ref=getattr(binding, "run_ref", ""),
        trusted_context=getattr(binding, "trusted_context", None),
        handoff=getattr(binding, "handoff", None),
        sandbox=getattr(binding, "sandbox", None),
        tool_surface=getattr(binding, "tool_surface", None),
        read_authorities=tuple(getattr(binding, "read_authorities", ()) or ()),
        mutation_authority=getattr(binding, "mutation_authority", None),
        restricted_shell_authority=getattr(binding, "restricted_shell_authority", None),
        test_execution_authority=getattr(binding, "test_execution_authority", None),
        git_authorities=tuple(getattr(binding, "git_authorities", ()) or ()),
        github_authorities=tuple(getattr(binding, "github_authorities", ()) or ()),
        plan_binding=getattr(binding, "plan_binding", None),
        # AF #57 M1/W4: mechanical carrier copy of the source-neutral Plan
        # Authority binding (never authority by itself).
        plan_authority_binding=getattr(binding, "plan_authority_binding", None),
        trusted_task_main_context=getattr(binding, "trusted_task_main_context", None),
        allowed_operations=allowed,
        # AF #56 M3/W3: preserve the trusted project context carriers so the
        # MCP path exposes the same authorized root set / SOURCE_REPOSITORY as
        # the direct trusted binding (mechanical copies only).
        authorized_roots=getattr(binding, "authorized_roots", None),
        source_repository=str(getattr(binding, "source_repository", "") or ""),
    )


def _project_tool_response(binding: Any, operation: str, tool_response: ToolResponse) -> McpToolResult:
    """Canonical result projection with the Core-owned hydrate special case.

    ``result.hydrate`` success payloads are projected by the canonical
    Core-owned whole-object hydration projection (inline, bounded by
    HYDRATE_WHOLE_OBJECT_MAX_BYTES); every other response uses the governed
    bounded projection. Shared mechanically by the trusted and unbound
    transports (no second projection ontology).
    """
    if operation == "result.hydrate" and getattr(tool_response, "ok", False):
        try:
            from aota_forge.work_plane.result_hydrate import (
                project_hydrate_result_for_transport as _core_hydrate_project,
            )

            projected = _core_hydrate_project(tool_response)
            if isinstance(projected, dict) and "__governed_failure__" in projected:
                return _governed_from_response(
                    binding, operation, projected["__governed_failure__"]
                )
            if isinstance(projected, dict):
                return projected  # type: ignore[return-value]
        except Exception:
            pass
    return _governed_from_response(binding, operation, tool_response)


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
        return _project_tool_response(self.binding, operation, tool_response)

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


# ---------------------------------------------------------------------------
# AF #59 M1 — always-available unbound host transport.
#
# Ordinary OpenCode/OpenChamber sessions have no session/instance/Plan binding.
# The AOTA MCP must still start and expose the single entry ``aota.invoke``.
# MCP availability is NOT authority (MCP_AVAILABILITY_IS_NOT_AUTHORITY=yes):
# reads/discussion stay usable without a binding, and every side effect
# resolves its authority from the canonical refs at the operation boundary
# (composition/ref_scoped_authority.py). No session token, no lease, no second
# authority engine.
# ---------------------------------------------------------------------------

GLOBAL_MCP_ENV = "AOTA_GLOBAL_MCP"
UNBOUND_HOST_TARGET_DEFAULT = "opencode-unbound-chat"
MCP_AVAILABILITY_IS_NOT_AUTHORITY = True
SESSION_BINDING_REQUIRED_FOR_MCP = False
UNBOUND_OPERATIONS_REQUIRE_CANONICAL_REFS = True
# AF #59 M1 acceptance repair R2: the canonical read-only introspection
# operations (registered in the single YAML registry and bound to Core ingress
# handlers) are available in an ordinary task-main session without any
# Plan/session binding. They read host/registry state and grant nothing:
# EXPOSURE_IS_NOT_AUTHORITY=yes, READ_AUTHORITY_IS_WRITE_AUTHORITY=no.
UNBOUND_READ_INTROSPECTION_OPERATIONS: tuple[str, ...] = (
    "host.status",
    "operations.list",
    "runtime.status",
)


@dataclass(frozen=True)
class UnboundHostContext:
    """Mechanical carrier for an ordinary host session with no binding.

    Contains only operator/process construction inputs (repo root, operator
    registry/runtime-config locators, bounded origin transport destination).
    It grants nothing: authority is resolved per operation from canonical
    refs. Never model-supplied.
    """

    repo_root: str
    registry_path: str = ""
    runtime_config_path: str = ""
    origin_session_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.repo_root, str) or not self.repo_root.strip():
            raise TrustedBindingError("unbound host context requires a repo_root")
        if len(self.repo_root) > 4096:
            raise TrustedBindingError("unbound host context repo_root exceeds bound")


class RefScopedHydrateError(ValueError):
    """Typed fail-closed hydration scope error for unbound host sessions."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _unbound_governed_error(operation: str, code: str, message: str) -> McpToolResult:
    """Typed fail-closed result for unbound operations without a sandbox.

    Mirrors the canonical governed shape; no sandbox-dependent projection is
    possible or needed for a pre-dispatch denial.
    """
    err = {"code": code, "message": _bounded_failure_message(message)}
    return {
        "ok": False,
        "operation": operation,
        "payload": None,
        "error": err,
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
        "hydration": None,
    }


def _unbound_inline_success(operation: str, payload: dict[str, Any]) -> McpToolResult:
    """Bounded inline success result for unbound read-only operations.

    Mirrors the governed shape without a sandbox-dependent projection: these
    operations carry no result store scope (no project/worktree sandbox) and
    return already-bounded payloads only.
    """
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
        "output_byte_length": 0,
        "inline_output": None,
        "output_ref": None,
        "hydration": None,
    }


def _unbound_agent_visible_operations() -> tuple[str, ...]:
    """Unbound host operations the model can actually use (honest catalog).

    Ref-scoped Plan operations (Git reads + governed lifecycle included) +
    hydration + optional role.bootstrap + read-only Progress introspection +
    read-only Skill access. Legacy workflow-brain operations (task_main.*)
    stay off the thin normal path.
    """
    from aota_forge.composition.ref_scoped_authority import (
        PLAN_REF_SCOPED_OPERATIONS,
    )

    return tuple(
        sorted(
            set(PLAN_REF_SCOPED_OPERATIONS)
            | {"result.hydrate", "role.bootstrap", "skill.open"}
            | set(UNBOUND_READ_INTROSPECTION_OPERATIONS)
        )
    )


def _unbound_task_main_skill_metadata() -> dict[str, list[dict[str, Any]]]:
    """Trusted task-main Skill identity/ref metadata from the AF catalog.

    Reuses the existing single AF Role/Skill catalog
    (``_ROLE_SKILL_DEFS`` -> ``AllowedSkillUniverse`` / ``StaticSkillRegistry``
    via the canonical ``af_roles`` helpers). No second Skill registry is
    created; the bound role.bootstrap composes the same catalog.
    """
    from aota_forge.work_plane.af_roles import (
        AF_SKILL_REGISTRY,
        eager_skill_refs_for_role,
        progressive_skill_metadata,
    )

    role = "task-main"
    eager_entries: list[dict[str, Any]] = []
    for ref in eager_skill_refs_for_role(role):
        skill_id = ref.rsplit("@", 1)[0]
        entry = AF_SKILL_REGISTRY.get(role, skill_id, "1.0.0")
        eager_entries.append(
            {
                "skill_id": skill_id,
                "ref": ref,
                "digest": entry.identity.digest if entry is not None else "",
                "provenance": "aota_forge",
                "delivery": "eager",
            }
        )
    return {
        "BASE_SKILLS": eager_entries,
        "PROGRESSIVE_SKILLS": list(progressive_skill_metadata(role)),
    }


def _unbound_role_bootstrap_payload() -> dict[str, Any]:
    """Bounded unbound role.bootstrap payload (optional useful operation).

    No trusted role/task context exists without a binding; this payload
    reports the ordinary host session state, the Agent-visible operations and
    the trusted task-main Skill identity/ref metadata (from the one AF
    catalog) so the model can proceed with ref-scoped operations and open
    Skills via ``skill.open``. It carries no Plan/session approval state
    (never session-scoped authority).
    """
    skills = _unbound_task_main_skill_metadata()
    return {
        "ROLE": "task-main",
        "HOST_SESSION": "unbound",
        "AOTA_MCP": {
            "available": True,
            "tool": AGENT_FACING_AOTA_TOOL,
            "tool_count": MCP_PUBLIC_TOOL_COUNT,
            "operations": list(_unbound_agent_visible_operations()),
        },
        "BASE_SKILLS": skills["BASE_SKILLS"],
        "PROGRESSIVE_SKILLS": skills["PROGRESSIVE_SKILLS"],
        "SKILL_ACCESS": {
            "operation": "skill.open",
            "argument": "ref",
            "IS_AUTHORITY": False,
        },
        "SESSION_BINDING_PRESENT": False,
        "SESSION_BINDING_REQUIRED": False,
        "ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT": False,
        "LEGACY_WORKFLOW_OPERATIONS_EXPOSED": False,
        "AUTHORITY_SOURCE": "ref_scoped_operation_boundary",
        "GUIDANCE": (
            "Ordinary host session: no Plan/task binding is carried. Discussion, "
            "analysis and Plan review need no binding. For Plan-bound operations "
            "supply the canonical plan_ref (owner/repo#number); for execution "
            "supply the related handoff_ref. AOTA Forge revalidates authority, "
            "project, scope and approval from the authoritative record at the "
            "operation boundary."
        ),
        "IS_AUTHORITY": False,
    }


class _UnboundAotaAdapter:
    """Single-entry transport for ordinary unbound host sessions.

    Owns only: envelope checks, exposure gating, operation-time ref-scoped
    authority resolution (delegated to the canonical resolver) and protocol
    projection. Resolution/validation/provider selection stay in core_ingress.
    """

    def __init__(self, context: UnboundHostContext) -> None:
        self.context = context
        self._resolver: Any | None = None

    def _authority_resolver(self) -> Any:
        if self._resolver is None:
            from aota_forge.composition.ref_scoped_authority import (
                RefScopedAuthorityResolver,
            )

            self._resolver = RefScopedAuthorityResolver(
                repo_root=self.context.repo_root,
                registry_path=self.context.registry_path or None,
                runtime_config_path=self.context.runtime_config_path or None,
            )
        return self._resolver

    def _dispatch_with_binding(
        self, binding: TrustedWorkerBinding, operation: str, arguments: dict[str, Any]
    ) -> McpToolResult:
        try:
            from aota_forge.core_ingress import dispatch_tool_operation as _core_dispatch

            canonical = _to_canonical_binding(binding)
            tool_response = _core_dispatch(operation, arguments, canonical)
        except ForgeError as exc:
            return _governed_error(binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _governed_error(binding, operation, "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        return _project_tool_response(binding, operation, tool_response)

    def _invoke_ref_scoped(self, operation: str, arguments: dict[str, Any]) -> McpToolResult:
        from aota_forge.composition.ref_scoped_authority import (
            PLAN_REF_SCOPED_OPERATIONS,
            READ_CLASSIFIED_OPERATIONS,
            RefScopedAuthorityError,
        )

        plan_ref = arguments.get("plan_ref")
        if not isinstance(plan_ref, str) or not plan_ref.strip():
            return _unbound_governed_error(
                operation,
                "PLAN_REF_REQUIRED",
                "this operation requires a canonical plan_ref (owner/repo#number) to "
                "locate the authoritative Plan; no session/instance binding is used",
            )
        plan_ref = plan_ref.strip()
        resolver = self._authority_resolver()
        try:
            if operation not in READ_CLASSIFIED_OPERATIONS:
                # Operation boundary: approval is the CURRENT server-side Plan
                # fact, re-read now (never session-carried).
                resolver.require_plan_approval(plan_ref)
            # task.start uses the process-bound production dispatcher; refresh
            # the composition so the dispatcher/worker resolver provably match
            # the Plan being started (no cross-Plan stale binding reuse).
            binding = resolver.task_main_binding_for_plan_ref(
                plan_ref, refresh=(operation == "task.start")
            )
        except RefScopedAuthorityError as exc:
            return _unbound_governed_error(operation, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - resolution fails closed
            return _unbound_governed_error(
                operation, "AUTHORITY_RESOLUTION_FAILED", _bounded_failure_message(str(exc))
            )
        dispatched_arguments = dict(arguments)
        return self._dispatch_with_binding(binding, operation, dispatched_arguments)

    def _registry_sandbox(self, project_id: str, worktree_id: str) -> Any:
        """Canonical sandbox located by the hydrate ref's project/worktree ids.

        The ref carries the canonical ToolOutputRef identity (project_id +
        worktree_id); the operator workspace registry locates the trusted
        project root. Payload metadata (project/worktree/ref/digest) is still
        verified by result governance against this sandbox.
        """
        from aota_forge.composition.project_binding import resolve_trusted_project_binding
        from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

        if not _SAFE_ID.fullmatch(project_id) or not _SAFE_ID.fullmatch(worktree_id):
            raise RefScopedHydrateError("HYDRATE_SCOPE_INVALID", "project_id/worktree_id must be canonical identifiers")
        try:
            binding = resolve_trusted_project_binding(
                project_id=project_id,
                registry_path=self.context.registry_path or None,
                workspace_root=self.context.repo_root if not self.context.registry_path else None,
            )
        except Exception as exc:  # noqa: BLE001 - unknown project fails closed
            raise RefScopedHydrateError("UNKNOWN_PROJECT", f"project {project_id!r} could not be resolved") from exc
        evidence = getattr(binding, "resolution", None)
        candidates = getattr(evidence, "candidates", ()) or ()
        if getattr(evidence, "status", "") != "RESOLVED" or len(candidates) != 1:
            raise RefScopedHydrateError("UNKNOWN_PROJECT", f"project {project_id!r} resolution is not singular")
        return bind_worktree_sandbox(
            evidence,
            worktree_id,
            candidates[0].project_root,
            expected_project_id=project_id,
        )

    def _invoke_registry_hydrate(self, arguments: dict[str, Any]) -> McpToolResult:
        """result.hydrate in unbound mode: scope from the ref's own identity."""
        from types import SimpleNamespace

        from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_tool_operation

        project_id = arguments.get("project_id")
        worktree_id = arguments.get("worktree_id")
        if not isinstance(project_id, str) or not project_id.strip():
            return _unbound_governed_error("result.hydrate", "HYDRATE_SCOPE_INVALID", "project_id must be a canonical identifier")
        if not isinstance(worktree_id, str) or not worktree_id.strip():
            return _unbound_governed_error("result.hydrate", "HYDRATE_SCOPE_INVALID", "worktree_id must be a canonical identifier")
        try:
            sandbox = self._registry_sandbox(project_id.strip(), worktree_id.strip())
        except RefScopedHydrateError as exc:
            return _unbound_governed_error("result.hydrate", exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - resolution fails closed
            return _unbound_governed_error("result.hydrate", "HYDRATE_SCOPE_INVALID", _bounded_failure_message(str(exc)))
        canonical = CanonicalDispatchBinding(
            project_id=project_id.strip(),
            worktree_id=worktree_id.strip(),
            sandbox=sandbox,
            unbound_host_session=True,
        )
        try:
            response = dispatch_tool_operation("result.hydrate", arguments, canonical)
        except ForgeError as exc:
            return _unbound_governed_error("result.hydrate", getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _unbound_governed_error("result.hydrate", "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        return _project_tool_response(SimpleNamespace(sandbox=sandbox), "result.hydrate", response)

    def invoke(self, operation: str, arguments: dict[str, Any] | None) -> McpToolResult:
        from aota_forge.composition.ref_scoped_authority import (
            PLAN_REF_SCOPED_OPERATIONS,
        )

        if not isinstance(operation, str):
            return _unbound_governed_error(
                "unknown", "UNKNOWN_OPERATION", f"operation must be string, got {type(operation).__name__}"
            )
        try:
            from aota_forge.core_ingress import resolve_descriptor as _core_resolve

            _core_resolve(operation)
        except ForgeError as exc:
            return _unbound_governed_error(operation, getattr(exc, "code", "UNKNOWN_OPERATION"), str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _unbound_governed_error(operation, "GOVERNED_OPERATION_FAILURE", str(exc))
        if operation not in SUPPORTED_OPERATIONS and operation not in UNBOUND_READ_INTROSPECTION_OPERATIONS:
            return _unbound_governed_error(operation, "UNKNOWN_OPERATION", f"unknown operation: {operation!r}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _unbound_governed_error(
                operation, "INPUT_TYPE_INVALID", f"arguments must be object, got {type(arguments).__name__}"
            )
        # Optional useful operation: bounded unbound bootstrap (no binding).
        if operation == "role.bootstrap":
            return _unbound_inline_success(operation, _unbound_role_bootstrap_payload())
        # Read-only Skill guidance: same AF catalog/universe as bound sessions.
        if operation == "skill.open":
            return self._invoke_unbound_skill_open(arguments)
        # Read-only Progress introspection: canonical Core ingress handlers.
        if operation in UNBOUND_READ_INTROSPECTION_OPERATIONS:
            return self._invoke_read_introspection(operation, arguments)
        # Durable by_ref hydration in unbound mode: scope from the ref identity.
        if operation == "result.hydrate":
            return self._invoke_registry_hydrate(arguments)
        # Ref-scoped operations: PLAN_REF_SCOPED_OPERATIONS.
        if operation in PLAN_REF_SCOPED_OPERATIONS:
            return self._invoke_ref_scoped(operation, arguments)
        # Everything else has no unbound authority path: canonical Core
        # dispatch with an empty unbound binding fails closed per operation
        # (no sandbox/authority/role => AUTHORITY_DENIED or typed equivalent).
        from aota_forge.core_ingress import CanonicalDispatchBinding

        return self._dispatch_with_binding_unbound(operation, arguments, CanonicalDispatchBinding(unbound_host_session=True))

    def _invoke_unbound_skill_open(self, arguments: dict[str, Any]) -> McpToolResult:
        """Read-only skill.open in an ordinary task-main session.

        The ordinary host session has the task-main profile; the Role Skill
        universe and registry are exactly the existing AF catalog
        (``_ROLE_SKILL_DEFS`` -> ``AllowedSkillUniverse`` /
        ``StaticSkillRegistry``). No second Skill system exists and no
        Milestone construction approval is required: opening guidance grants
        nothing (SKILL_IS_AUTHORITY=no). The authorized reader is rooted at the
        operator-configured AF repo root (AOTA_FORGE_REPO_ROOT); no
        model-supplied path participates.
        """
        from types import SimpleNamespace

        from aota_forge.work_plane.role_bootstrap import handle_skill_open

        unbound_task_main_identity = SimpleNamespace(
            handoff=SimpleNamespace(work_role="task-main"),
            sandbox=SimpleNamespace(worktree_root=self.context.repo_root),
        )
        try:
            payload = handle_skill_open(unbound_task_main_identity, arguments)
        except Exception as exc:  # noqa: BLE001 - typed fail-closed
            return _unbound_governed_error(
                "skill.open",
                getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"),
                _bounded_failure_message(str(exc)),
            )
        return _unbound_inline_success("skill.open", dict(payload))

    def _invoke_read_introspection(self, operation: str, arguments: dict[str, Any]) -> McpToolResult:
        """Canonical read-only Progress introspection (no authority needed).

        ``operations.list`` / ``host.status`` / ``runtime.status`` are
        canonical general operations bound to Core ingress handlers and are
        read-only by descriptor; they resolve no provider authority and grant
        nothing. Delegates to the canonical ``dispatch_via_core`` seam (same
        resolution/validation/ingress path), never a second dispatch plane.
        """
        from aota_forge.core_ingress import dispatch_via_core

        try:
            envelope = dispatch_via_core(operation, dict(arguments))
        except ForgeError as exc:
            return _unbound_governed_error(operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _unbound_governed_error(operation, "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            error = envelope.get("error") if isinstance(envelope, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            return _unbound_governed_error(
                operation,
                str(code or "GOVERNED_OPERATION_FAILURE"),
                _bounded_failure_message(str(message or "governed operation failed")),
            )
        payload: dict[str, Any] = {}
        data = envelope.get("data")
        if isinstance(data, dict):
            payload["data"] = data
        evidence = envelope.get("evidence")
        if isinstance(evidence, dict) and evidence:
            payload["evidence"] = evidence
        warnings = envelope.get("warnings")
        if isinstance(warnings, list) and warnings:
            payload["warnings"] = list(warnings)
        return _unbound_inline_success(operation, payload)

    def _dispatch_with_binding_unbound(
        self, operation: str, arguments: dict[str, Any], canonical: Any
    ) -> McpToolResult:
        try:
            from aota_forge.core_ingress import dispatch_tool_operation as _core_dispatch

            response = _core_dispatch(operation, arguments, canonical)
        except ForgeError as exc:
            return _unbound_governed_error(operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            return _unbound_governed_error(operation, "GOVERNED_OPERATION_FAILURE", _bounded_failure_message(str(exc)))
        if response.ok:
            # Should not happen without authorities; fail closed on any
            # unexpected success so no unbound mutation can slip through.
            return _unbound_governed_error(
                operation,
                "AUTHORITY_DENIED",
                "operation requires a trusted Plan/task ref or an AF-bound session",
            )
        err = dict(response.error or {})
        return _unbound_governed_error(
            operation,
            str(err.get("code") or "GOVERNED_OPERATION_FAILURE"),
            str(err.get("message") or "governed operation failed"),
        )


def create_aota_invoke_dispatch(trusted_binding: TrustedWorkerBinding):
    """Public factory binding the canonical single-entry ``aota.invoke`` path.

    AF #53 M2/W2: trusted runtime composition (e.g. the thin task-main host)
    may bind the SAME canonical callable the MCP server exposes, without
    constructing a transport server and without duplicating operation
    resolution, typed validation, provider selection or result projection.
    Mechanical exposure only: no new tool, no second dispatch plane, no
    authority. The returned callable is exactly
    ``_SharedAotaMcpAdapter(trusted_binding).invoke``.

    AOTA_INVOKE_DISPATCH_IS_CANONICAL_SINGLE_ENTRY=yes.
    """
    if not isinstance(trusted_binding, TrustedWorkerBinding):
        raise TrustedBindingError("trusted server-side binding is required")
    return _SharedAotaMcpAdapter(trusted_binding).invoke


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
    return _build_shared_mcp_server(_SharedAotaMcpAdapter(trusted_binding).invoke)


def create_unbound_mcp_server(context: UnboundHostContext):
    """Create the single-entry MCP server for an ordinary unbound host session.

    The server is always available; operation authority is resolved per
    operation from canonical refs at the operation boundary. No session,
    instance directory, active_binding.json or preparation state participates
    in MCP availability (MCP_AVAILABILITY_IS_NOT_AUTHORITY=yes).
    """
    if MCPServer is None or ToolAnnotations is None:
        raise McpTransportUnavailable("restricted shared MCP transport requires the standard 'mcp' package")
    if not isinstance(context, UnboundHostContext):
        raise TrustedBindingError("unbound host context is required")
    return _build_shared_mcp_server(_UnboundAotaAdapter(context).invoke)


def _build_shared_mcp_server(invoke_callable: Callable[[str, dict[str, Any] | None], McpToolResult]):
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
                    text = json.dumps(summary, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
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
                            "next_action_guidance",
                            "review_dispatch_mode",
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
                        # AF #53 M3/W2-R2 (I53-B002): preserve the already-
                        # bounded/sanitized typed error message in the model-
                        # visible failure summary. The error envelope was
                        # bounded by sanitize+governed projection; re-bound and
                        # path-redact here. Never a raw exception/traceback.
                        if isinstance(err, dict):
                            raw_message = err.get("message")
                            if isinstance(raw_message, str):
                                bounded = _bounded_failure_message(raw_message)
                                if bounded:
                                    summary["error_message"] = bounded
                    if output_mode == "by_ref":
                        ref = mcp_result.get("output_ref")
                        if isinstance(ref, dict):
                            summary["output_ref"] = {
                                key: ref.get(key)
                                for key in ("ref", "digest", "project_id", "worktree_id", "byte_length")
                                if ref.get(key) is not None
                            }
                        # W5: model-visible by_ref hydration path (I49-B003).
                        hydration = mcp_result.get("hydration")
                        if isinstance(hydration, dict):
                            summary["hydration"] = hydration
                except Exception:
                    pass
                summary = {k: v for k, v in summary.items() if v is not None}
                text = json.dumps(summary, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
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
        mcp_result = invoke_callable(operation, arguments if arguments is not None else {})
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
    "HANDOFF_TASK_OPERATIONS",
    "INTERNAL_TASK_MAIN_OPERATIONS",
    "BOUNDED_MCP_OPERATIONS",
    "LOGICAL_CAPABILITY_SURFACE",
    "SUPPORTED_OPERATIONS",
    "CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG",
    "ROLE_SURFACE_SUBSET_CANONICAL_AGENT_VISIBLE_CATALOG",
    "EXPOSURE_IS_NOT_AUTHORITY",
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
    "AOTA_INVOKE_DISPATCH_FACTORY_IS_CANONICAL_SINGLE_ENTRY",
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
    "create_aota_invoke_dispatch",
    "create_shared_mcp_server",
    "create_unbound_mcp_server",
    "UnboundHostContext",
    "GLOBAL_MCP_ENV",
    "MCP_AVAILABILITY_IS_NOT_AUTHORITY",
    "SESSION_BINDING_REQUIRED_FOR_MCP",
    "UNBOUND_READ_INTROSPECTION_OPERATIONS",
    "run_shared_mcp_server",
]
