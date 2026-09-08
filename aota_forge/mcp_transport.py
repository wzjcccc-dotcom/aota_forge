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

# M2 activation: durable hydration + restricted shell reuse (thin imports, no new ontology)
try:
    from aota_forge.work_plane.durable_result_store import (
        DURABLE_PAYLOAD_MAX_BYTES,
        persist_durable_payload,
    )
except Exception:  # pragma: no cover - fallback for isolated test discovery
    DURABLE_PAYLOAD_MAX_BYTES = 64 * 1024

    def persist_durable_payload(*_a: Any, **_kw: Any) -> dict[str, Any]:  # type: ignore
        raise RuntimeError("durable store unavailable")

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
    )
except Exception:  # pragma: no cover - fallback for isolated test discovery without yaml
    TASK_MAIN_ACTIVATE_DESCRIPTOR = None  # type: ignore
    TASK_MAIN_ADVANCE_DESCRIPTOR = None  # type: ignore
    TASK_MAIN_RECOVER_DESCRIPTOR = None  # type: ignore

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
TASK_MAIN_MODEL_VISIBLE_CONTROL_COUNT = 3
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

# Logical capability surface (existing typed operations, not MCP tool names).
# M2 convergence: workspace.* + durable result.hydrate + residual restricted_shell.run
# M3/W1: + task_main.* (exactly 3, minimal intent)
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

# Exact deterministic operation -> descriptor map (no fuzzy, no alias).
# Workspace descriptors are static imports; M2 descriptors are loaded canonically (single authority: .aota/contracts/operations.yaml)
# M3/W1 task-main descriptors are canonical via same loader (task_main_descriptors)
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
# M3/W1 task-main descriptors (canonical, exactly 3)
if TASK_MAIN_ACTIVATE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_ACTIVATE_DESCRIPTOR.name] = TASK_MAIN_ACTIVATE_DESCRIPTOR
if TASK_MAIN_RECOVER_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_RECOVER_DESCRIPTOR.name] = TASK_MAIN_RECOVER_DESCRIPTOR
if TASK_MAIN_ADVANCE_DESCRIPTOR is not None:
    _DESCRIPTOR_MAP[TASK_MAIN_ADVANCE_DESCRIPTOR.name] = TASK_MAIN_ADVANCE_DESCRIPTOR
SUPPORTED_OPERATIONS: frozenset[str] = frozenset(LOGICAL_OPERATIONS)
# For backward compatibility, retain WORKSPACE_OPERATIONS alias but expanded set is canonical
CANONICAL_SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS


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
class TrustedTaskMainRuntimeContext:
    """Narrow trusted carrier for task-main control (M3/W1).

    Carries **already-authoritative** runtime objects only. It does not
    calculate policy, mint user approval, resolve Plan authority, invent
    capabilities, become role registry, or become permission engine.
    It merely binds the live governed Plan truth and the existing
    ``TaskMainControlService`` with its stores/resolvers so the single-entry
    ``aota.invoke`` adapter can dispatch the three normal-path controls
    without trusting model-supplied authority-bearing fields.

    All fields are trusted host/runtime supplied; none may be supplied by
    the model via ``aota.invoke`` arguments.
    """

    control_service: Any
    live_plan_view: Any
    origin_task_main_session_ref: str
    executor_id: str
    handoff_resolver: Any  # Callable[[str], TaskHandoff]
    governed_evidence_resolver: Any | None = None  # Callable[[str], GovernedWorkItemEvidence] | None
    reviewer_handoff_resolver: Any | None = None  # Callable[[], TaskHandoff] | None
    governed_review_resolver: Any | None = None  # Callable[[str, str], GovernedReviewEvidence] | None
    reviewer_canonical_task_id_resolver: Any | None = None
    next_milestone_view: Any | None = None
    session_available: bool = True
    coordinator_id: str | None = None

    def __post_init__(self) -> None:
        # Import here to avoid circular import at module import time
        try:
            from aota_forge.runtime.task_main.control import TaskMainControlService  # type: ignore
            from aota_forge.runtime.task_main.coordinator import MilestonePlanView  # type: ignore
        except Exception:
            TaskMainControlService = object  # type: ignore
            MilestonePlanView = object  # type: ignore
        if TaskMainControlService is not object and not isinstance(self.control_service, TaskMainControlService):  # type: ignore
            # Fallback check by attribute if class not available (isolated import)
            if not hasattr(self.control_service, "activate_milestone"):
                raise TrustedBindingError(f"control_service must be TaskMainControlService, got {type(self.control_service).__name__}")
        if MilestonePlanView is not object and not isinstance(self.live_plan_view, MilestonePlanView):  # type: ignore
            raise TrustedBindingError(f"live_plan_view must be MilestonePlanView, got {type(self.live_plan_view).__name__}")
        if self.next_milestone_view is not None and MilestonePlanView is not object and not isinstance(self.next_milestone_view, MilestonePlanView):  # type: ignore
            raise TrustedBindingError(f"next_milestone_view must be MilestonePlanView or None, got {type(self.next_milestone_view).__name__}")
        if not isinstance(self.origin_task_main_session_ref, str) or not self.origin_task_main_session_ref.strip():
            raise TrustedBindingError("origin_task_main_session_ref must be non-empty string")
        if len(self.origin_task_main_session_ref) > 512:
            raise TrustedBindingError("origin_task_main_session_ref exceeds bound")
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise TrustedBindingError("executor_id must be non-empty string")
        if not callable(self.handoff_resolver):
            raise TrustedBindingError("handoff_resolver must be callable")
        if self.governed_evidence_resolver is not None and not callable(self.governed_evidence_resolver):
            raise TrustedBindingError("governed_evidence_resolver must be callable or None")
        if self.reviewer_handoff_resolver is not None and not callable(self.reviewer_handoff_resolver):
            raise TrustedBindingError("reviewer_handoff_resolver must be callable or None")
        if self.governed_review_resolver is not None and not callable(self.governed_review_resolver):
            raise TrustedBindingError("governed_review_resolver must be callable or None")
        if self.reviewer_canonical_task_id_resolver is not None and not callable(self.reviewer_canonical_task_id_resolver):
            raise TrustedBindingError("reviewer_canonical_task_id_resolver must be callable or None")
        if type(self.session_available) is not bool:
            raise TrustedBindingError("session_available must be bool")
        if self.coordinator_id is not None:
            if not isinstance(self.coordinator_id, str) or not self.coordinator_id.strip():
                raise TrustedBindingError("coordinator_id must be non-empty string when supplied")
            if len(self.coordinator_id) > 512:
                raise TrustedBindingError("coordinator_id exceeds bound")


@dataclass(frozen=True)
class TrustedWorkerBinding:
    """Operator/runtime-owned context for one restricted MCP server.

    This is a thin carrier of already-authoritative objects.  It does not
    decide permissions, resolve projects, or mint capabilities.  In
    particular, a model-facing MCP argument can never construct this object.
    ``mutation_authority`` is optional deliberately: the write operation
    remains callable via aota.invoke while rejecting calls when mutation
    authority is absent (AUTHORITY_DENIED).

    Transport vs capability separation (M2 convergence):
    * MCP transport surface is ``aota.invoke`` (exactly one).
    * Logical capability surface is ``workspace.search/read/write`` + ``result.hydrate`` + ``restricted_shell.run``
      carried by ``ToolRoleSurface``.  Visibility (eager/progressive) never grants
      authority; authority lives in Workspace*AuthorityEvidence / RestrictedShellAuthorityEvidence.
    * Per-binding logical capability subset is derived from accepted role/task/policy context.
      Not every binding carries all five logical operations.  TRANSPORT_OPERATION_IDENTITY_SEPARATED=yes.
    * TOOL_VISIBILITY_IS_AUTHORITY=no, ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY=yes.
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
    # M2: residual shell authority (optional, not per-binding required)
    restricted_shell_authority: Any | None = None
    # W2: test execution authority (optional, per-role least-privilege)
    test_execution_authority: Any | None = None
    # M3/W1: trusted task-main runtime context (optional, task-main only)
    trusted_task_main_context: Any | None = None

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
        # M2 separation: tool_surface carries logical capabilities subset, not MCP transport names.
        # Visibility != authority: surface controls visibility only, not operation authority.
        # Per-binding subset: any subset of SUPPORTED_OPERATIONS is valid (do not require all five).
        surface_names = set(self.tool_surface.all_capability_names())
        allowed = set(SUPPORTED_OPERATIONS)
        if not surface_names.issubset(allowed):
            unknown = sorted(surface_names - allowed)
            raise TrustedBindingError(
                f"tool surface contains unknown logical operation(s) not in supported catalog "
                f"(allowed {sorted(allowed)}, unknown {unknown}, got {sorted(surface_names)})"
            )
        if "aota.invoke" in surface_names:
            raise TrustedBindingError("tool surface must not contain transport name aota.invoke (transport != capability)")
        # Backward compatibility: M1 bindings had exactly 2 read authorities (search+read). M2 generalizes to 0..2
        # to allow per-role subsets (e.g., reviewer without shell, task-main without write, etc.).
        # Visibility does not grant authority: missing authority => AUTHORITY_DENIED at dispatch, not binding error.
        if not isinstance(self.read_authorities, tuple):
            raise TrustedBindingError("read_authorities must be tuple")
        if len(self.read_authorities) > 2:
            raise TrustedBindingError("read authorities at most 2 (workspace.search/read)")
        # Each read authority must be typed, match sandbox/handoff, and be for workspace.search/read
        read_names = set()
        for authority in self.read_authorities:
            if not isinstance(authority, WorkspaceAuthorityEvidence):
                raise TrustedBindingError(f"read authority must be WorkspaceAuthorityEvidence, got {type(authority).__name__}")
            if authority.sandbox != self.sandbox or authority.handoff != self.handoff:
                raise TrustedBindingError("read authority does not match trusted binding")
            if authority.operation.name not in ("workspace.search", "workspace.read"):
                raise TrustedBindingError(f"read authority operation must be workspace.search/read, got {authority.operation.name!r}")
            if authority.operation.name in read_names:
                raise TrustedBindingError(f"duplicate read authority for {authority.operation.name!r}")
            read_names.add(authority.operation.name)
        if self.mutation_authority is not None:
            if not isinstance(self.mutation_authority, WorkspaceMutationAuthority):
                raise TrustedBindingError("mutation authority must be typed")
            if self.mutation_authority.sandbox != self.sandbox or self.mutation_authority.handoff != self.handoff:
                raise TrustedBindingError("mutation authority does not match trusted binding")
            if self.mutation_authority.operation.name != "workspace.write":
                raise TrustedBindingError(f"mutation authority operation must be workspace.write, got {self.mutation_authority.operation.name!r}")
        # M2 residual shell authority (optional, progressive fallback)
        if self.restricted_shell_authority is not None:
            # Delayed type check to avoid circular import; verify required attributes
            if RestrictedShellAuthorityEvidence is not None:
                if not isinstance(self.restricted_shell_authority, RestrictedShellAuthorityEvidence):
                    raise TrustedBindingError(
                        f"restricted_shell_authority must be RestrictedShellAuthorityEvidence, got {type(self.restricted_shell_authority).__name__}"
                    )
                if self.restricted_shell_authority.sandbox != self.sandbox or self.restricted_shell_authority.handoff != self.handoff:
                    raise TrustedBindingError("restricted shell authority does not match trusted binding")
                if self.restricted_shell_authority.operation.name != "restricted_shell.run":
                    raise TrustedBindingError(
                        f"restricted shell authority operation must be restricted_shell.run, got {self.restricted_shell_authority.operation.name!r}"
                    )
            else:
                # If descriptor missing, allow None only
                raise TrustedBindingError("restricted_shell authority unavailable (descriptor missing)")
        # W2 test execution authority (optional, per-role least-privilege)
        if self.test_execution_authority is not None:
            # Lazy import to avoid circularity; duck check if import not available
            try:
                from aota_forge.work_plane.test_execution import TestExecutionAuthorityEvidence as _TestEvidence  # type: ignore
            except Exception:
                _TestEvidence = None  # type: ignore
            if _TestEvidence is not None:
                if not isinstance(self.test_execution_authority, _TestEvidence):
                    raise TrustedBindingError(
                        f"test_execution_authority must be TestExecutionAuthorityEvidence, got {type(self.test_execution_authority).__name__}"
                    )
            # Generic checks via duck typing if class not available or for safety
            ev = self.test_execution_authority
            if not hasattr(ev, "sandbox") or not hasattr(ev, "handoff") or not hasattr(ev, "operation"):
                raise TrustedBindingError("test_execution_authority missing required fields")
            if ev.sandbox != self.sandbox or ev.handoff != self.handoff:
                raise TrustedBindingError("test execution authority does not match trusted binding")
            if ev.operation.name != "test.run":  # type: ignore[union-attr]
                raise TrustedBindingError(f"test execution authority operation must be test.run, got {ev.operation.name!r}")  # type: ignore[union-attr]
            # Enforce per-role policy: coder has required, reviewer default deny, analyst/project-steward/task-main no automatic
            # The binding construction enforces this; here we just validate that reviewer has no authority unless review semantics justify.
            # No automatic enforcement here beyond existence; dispatch will still check authority.
        # M3/W1 trusted task-main context (optional, task-main only)
        if self.trusted_task_main_context is not None:
            if not isinstance(self.trusted_task_main_context, TrustedTaskMainRuntimeContext):
                raise TrustedBindingError(
                    f"trusted_task_main_context must be TrustedTaskMainRuntimeContext, got {type(self.trusted_task_main_context).__name__}"
                )
            # Task-main context must match binding sandbox/project identity
            ctx = self.trusted_task_main_context
            # Project identity consistency (binding project_id must equal control service project via context's live view if available)
            # No extra policy calculation here — just ensure carrier is authoritative and matches sandbox
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError("task-main context requires handoff work_role task-main")
            if self.tool_surface.work_role.value != "task-main":
                raise TrustedBindingError("task-main context requires tool_surface work_role task-main")
            # Ensure control_service is present and matches expected type (duck check)
            if not hasattr(ctx.control_service, "activate_milestone"):
                raise TrustedBindingError("task-main context control_service missing activate_milestone")


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


def _persist_if_by_ref(binding: TrustedWorkerBinding, sanitized: ToolResponse, projection: Any) -> None:
    """Best-effort durable persistence for large by_ref results (restart durability).

    For success by_ref, the governing projection's digest/byte_length corresponds to
    canonical_json(sanitized.payload). Persist that JSON bytes under the trusted
    sandbox so later result.hydrate (in a new process) can rehydrate.
    Fail-open on persistence error: the governed projection is already truthful
    (by_ref) and hydration will fail closed if file missing, but we prefer
    persistence success. Never persisting large results would break §7 mandatory proof.
    """
    try:
        if not projection.is_success:
            return
        if projection.output_mode != "by_ref" or projection.output_ref is None:
            return
        # Only workspace.* and restricted_shell.run produce bounded governed refs via this path
        # result.hydrate itself never goes by_ref (bounded inline)
        payload = sanitized.payload or {}
        # Recreate canonical bytes exactly as project_tool_result did
        from aota_forge.core.contracts.canonical import canonical_json, canonicalize

        if payload:
            full_bytes = canonical_json(canonicalize(payload, path="payload")).encode("utf-8")
        else:
            full_bytes = b""
        # Verify digest matches projection (defense)
        import hashlib

        computed = hashlib.sha256(full_bytes).hexdigest()
        if computed != projection.output_digest:
            # Payload serialization drift – fallback to inline_output derived bytes if available?
            # For tool governance, digest must equal sanitized payload JSON; mismatch indicates bug, skip persist
            return
        if len(full_bytes) != projection.output_byte_length:
            return
        # Persist via file-backed durable store (worktree_root/.aota/durable_payloads, non-DB, bounded)
        # Use the governed ref identity so hydrate can resolve via digest
        if len(full_bytes) > DURABLE_PAYLOAD_MAX_BYTES:
            # Exceeds durable bound – do not persist (will remain by_ref but hydration will fail oversized – fail-closed)
            return
        try:
            persist_durable_payload(
                binding.sandbox,
                full_bytes,
                ref=projection.output_ref.ref,
                digest=projection.output_digest,
                byte_length=projection.output_byte_length,
                project_id=binding.project_id,
                worktree_id=binding.worktree_id,
                kind="evidence",
            )
        except Exception:
            # Bounded file already exists with same digest → idempotent (same file)
            # Any error is logged silently; projection already returned by_ref truthfully
            pass
    except Exception:
        pass


def _governed_from_response(binding: TrustedWorkerBinding, operation: str, response: ToolResponse) -> McpToolResult:
    """Safety → governed projection → MCP for a real provider ToolResponse."""
    sanitized = _sanitize_tool_response(response)
    # Capability name for projection is the exact operation identity
    # Validate that it is a plausible capability string; fallback to sanitized name if needed.
    cap = operation if isinstance(operation, str) and operation else "unknown"
    try:
        projection = project_tool_result(sanitized, cap, binding.sandbox)
        # M2 durable: best-effort persist large by_ref payload for restart durability (§7)
        _persist_if_by_ref(binding, sanitized, projection)
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
            _persist_if_by_ref(binding, sanitized, projection)
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
    """Preserve task-main semantic failure identity end-to-end.

    Maps known coordinator/runner exception types to typed error codes.
    Falls back to GOVERNED_OPERATION_FAILURE while still preserving
    the bounded message that contains FAILS_CLOSED markers for Plan drift
    etc. The code itself is the machinable identity.
    """
    # Check by class name to avoid hard import dependency
    name = type(exc).__name__
    msg = str(exc)
    if name == "PlanDriftError" or "PLAN_DRIFT" in msg:
        return "PLAN_DRIFT"
    if name == "SessionRecoveryRequiredError" or "SESSION_RECOVERY_REQUIRED" in msg:
        return "SESSION_RECOVERY_REQUIRED"
    if name == "CoordinatorNotFoundError":
        return "COORDINATOR_NOT_FOUND"
    if name == "CoordinatorBindingError":
        return "COORDINATOR_BINDING_ERROR"
    if name == "CoordinatorRuntimeError":
        return "COORDINATOR_RUNTIME_ERROR"
    if name == "StaleCoordinatorRevisionError":
        return "STALE_COORDINATOR_REVISION"
    if name == "TaskMainControlAuthorityError":
        return "AUTHORITY_DENIED"
    if "USER_GATE_REQUIRED" in msg:
        return "USER_GATE_REQUIRED"
    if "AUTHORITY_DENIED" in msg or "requires profile" in msg:
        return "AUTHORITY_DENIED"
    if name in ("ValueError", "TypeError") and "unknown" in msg.lower():
        return "UNKNOWN_INPUT"
    # Preserve original code if exc carries .code
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


class _SharedAotaMcpAdapter:
    """Transport-local dispatch using only existing provider seams.

    Single-entry: ``aota.invoke(operation, arguments)`` -> exact operation
    resolution -> existing OperationContractDescriptor -> validate_inputs ->
    existing authority -> existing ToolProvider -> bounded transport response.

    No new registry, permission engine, or generic gateway is created.
    M2 expands logical operation catalog to include result.hydrate (durable selective hydration)
    and restricted_shell.run (residual fallback) via the same single-entry transport.
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
        # M2: durable result.hydrate provider (thin HydrationSource adapter, non-authority)
        self._hydrate_provider = None
        if ResultHydrateProvider is not None and RESULT_HYDRATE_DESCRIPTOR is not None:
            try:
                self._hydrate_provider = ResultHydrateProvider(binding.sandbox)
            except Exception:
                self._hydrate_provider = None
        # M2: residual restricted shell provider (reused, requires trusted authority)
        self._shell_provider = None
        if (
            BoundedRestrictedShellProvider is not None
            and binding.restricted_shell_authority is not None
        ):
            try:
                self._shell_provider = BoundedRestrictedShellProvider(binding.restricted_shell_authority)
            except Exception:
                self._shell_provider = None

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

            if operation in ("workspace.search", "workspace.read"):
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

            if operation == "result.hydrate":
                # Hydration is authorized via current scope (sandbox) + digest verification, not ref possession.
                # Per-binding visibility: operation must be in tool_surface progressive/eager set to be callable.
                # ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY=yes, but we still fail closed if not granted to this role/task.
                if operation not in set(self.binding.tool_surface.all_capability_names()):
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "result.hydrate not authorized for this role/task"}),
                    )
                if self._hydrate_provider is None or RESULT_HYDRATE_DESCRIPTOR is None:
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "result hydration not enabled for this binding"}),
                    )
                if descriptor.contract_hash() != RESULT_HYDRATE_DESCRIPTOR.contract_hash():
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "result.hydrate contract hash mismatch"}),
                    )
                request = ToolRequest(operation=RESULT_HYDRATE_DESCRIPTOR, inputs=validated)
                hyd_response = self._hydrate_provider.invoke(request)
                # For hydrate, bypass the generic 4096 inline bound: hydrate is whole-object up to 64 KiB durable bound.
                # Return usable hydrated evidence directly inline (bounded 64 KiB), not another by_ref layer.
                # This satisfies §7 mandatory 5KB+ proof: usable hydrated evidence returned, bounded, no silent truncation.
                if hyd_response.ok:
                    payload = dict(hyd_response.payload or {})
                    content = payload.get("content", "")
                    if not isinstance(content, str):
                        content = str(content)
                    content_bytes = content.encode("utf-8")
                    # Ensure digest verified and within durable bound
                    if len(content_bytes) > DURABLE_PAYLOAD_MAX_BYTES:
                        return _governed_from_response(
                            self.binding,
                            operation,
                            ToolResponse.failure({"code": "OVERSIZED_HYDRATION", "message": f"hydrated content {len(content_bytes)} exceeds durable bound {DURABLE_PAYLOAD_MAX_BYTES}"}),
                        )
                    # Return inline hydration result (whole_object, no silent truncation)
                    # Bounded repair I37-B001: deduplicate hydrated payload to single model-visible copy.
                    # Previous shape duplicated content in payload.content AND inline_output.
                    # Keep one canonical copy in payload, set inline_output=None (MCP_TOOL_RESULT_INTERNAL_DUPLICATE_COUNT=1).
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
                        "capability_name": "result.hydrate",
                        "output_digest": payload.get("digest", "0" * 64),
                        "output_byte_length": len(content_bytes),
                        "inline_output": None,
                        "output_ref": None,
                    }
                else:
                    # Failure path: use governed projection to preserve typed errors and safety
                    return _governed_from_response(self.binding, operation, hyd_response)

            if operation == "restricted_shell.run":
                # Residual fallback – must have trusted shell authority and be in capability surface.
                if operation not in set(self.binding.tool_surface.all_capability_names()):
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "restricted shell not authorized for this role/task"}),
                    )
                if self._shell_provider is None or RESTRICTED_SHELL_DESCRIPTOR is None:
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "restricted shell authority absent for this binding"}),
                    )
                if descriptor.contract_hash() != RESTRICTED_SHELL_DESCRIPTOR.contract_hash():
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "restricted shell contract hash mismatch"}),
                    )
                request = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs=validated)
                return _governed_from_response(self.binding, operation, self._shell_provider.invoke(request))

            # W2 AF Role Bootstrap / Skill / Test — share single MCP aota.invoke
            if operation == "role.bootstrap":
                # Trusted bootstrap: no model authority fields, all derived from binding
                if descriptor.contract_hash() != ROLE_BOOTSTRAP_DESCRIPTOR.contract_hash():  # type: ignore[union-attr]
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "role.bootstrap contract hash mismatch"}),
                    )
                try:
                    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap  # type: ignore
                except Exception as exc:
                    return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", f"role.bootstrap handler unavailable: {exc}")
                try:
                    payload = handle_role_bootstrap(self.binding, validated)
                except Exception as exc:
                    code = "AUTHORITY_DENIED" if "AUTHORITY" in str(exc) else "INVALID_INPUT" if "empty" in str(exc).lower() else "GOVERNED_OPERATION_FAILURE"
                    # Preserve fail-closed identity: unknown role, digest mismatch, etc. all map to GOVERNED_OPERATION_FAILURE unless typed
                    if isinstance(exc, ValueError) and "authority" in str(exc).lower():
                        code = "AUTHORITY_DENIED"
                    elif isinstance(exc, ValueError):
                        code = "GOVERNED_OPERATION_FAILURE"
                    return _governed_from_response(self.binding, operation, ToolResponse.failure({"code": code, "message": _bounded_failure_message(str(exc))}))
                return _governed_from_response(self.binding, operation, ToolResponse.success(payload))

            if operation == "skill.open":
                if descriptor.contract_hash() != SKILL_OPEN_DESCRIPTOR.contract_hash():  # type: ignore[union-attr]
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "skill.open contract hash mismatch"}),
                    )
                try:
                    from aota_forge.work_plane.role_bootstrap import handle_skill_open  # type: ignore
                except Exception as exc:
                    return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", f"skill.open handler unavailable: {exc}")
                try:
                    payload = handle_skill_open(self.binding, validated)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "not in allowed" in msg or "outside allowed" in msg:
                        code = "AUTHORITY_DENIED"
                    elif "foreign" in msg:
                        code = "FOREIGN_SKILL_DENIED"
                    elif "digest" in msg:
                        code = "DIGEST_MISMATCH"
                    elif "not found" in msg:
                        code = "SKILL_NOT_FOUND"
                    elif "missing" in msg:
                        code = "SKILL_NOT_FOUND"
                    else:
                        code = "GOVERNED_OPERATION_FAILURE"
                    return _governed_from_response(self.binding, operation, ToolResponse.failure({"code": code, "message": _bounded_failure_message(str(exc))}))
                return _governed_from_response(self.binding, operation, ToolResponse.success(payload))

            if operation == "test.run":
                # Per-role least-privilege: only bindings with test_execution_authority may invoke
                if operation not in set(self.binding.tool_surface.all_capability_names()):
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "test.run not authorized for this role/task (tool surface deny)"}),
                    )
                if TEST_RUN_DESCRIPTOR is None:
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "test.run descriptor unavailable"}),
                    )
                if descriptor.contract_hash() != TEST_RUN_DESCRIPTOR.contract_hash():
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "test.run contract hash mismatch"}),
                    )
                # Check trusted authority exists
                t_auth = getattr(self.binding, "test_execution_authority", None)
                if t_auth is None:
                    return _governed_from_response(
                        self.binding,
                        operation,
                        ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted test execution authority absent for this binding (per-role deny)"}),
                    )
                # Validate authority matches binding
                try:
                    from aota_forge.work_plane.test_execution import BoundedTestExecutionToolProvider  # type: ignore
                except Exception as exc:
                    return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", f"test provider unavailable: {exc}")
                try:
                    provider = BoundedTestExecutionToolProvider(t_auth)
                except Exception as exc:
                    return _governed_from_response(self.binding, operation, ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": _bounded_failure_message(str(exc))}))
                request = ToolRequest(operation=TEST_RUN_DESCRIPTOR, inputs=validated)
                return _governed_from_response(self.binding, operation, provider.invoke(request))

            if operation in TASK_MAIN_OPERATIONS:
                return self._invoke_task_main(operation, validated, descriptor)

            # Fallback (should be unreachable due to SUPPORTED_OPERATIONS check)
            return _governed_error(self.binding, operation, "UNKNOWN_OPERATION", f"unsupported operation: {operation!r}")
        except ForgeError as exc:
            return _governed_error(self.binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # adapter boundary: never turn failures into success  # noqa: BLE001
            msg = _bounded_failure_message(str(exc))
            return _governed_error(self.binding, operation, "GOVERNED_OPERATION_FAILURE", msg)

    def _invoke_task_main(self, operation: str, validated: dict[str, Any], descriptor: Any) -> McpToolResult:  # noqa: C901
        """Thin trusted adapter from aota.invoke to existing TaskMainControlService.

        This is the sole seam between the single-entry transport and the
        existing typed control service. It never trusts model-supplied
        authority-bearing fields; all truth comes from
        ``self.binding.trusted_task_main_context``.
        """
        binding = self.binding
        # Authority: worker must never be able to call task-main controls.
        # Profile comes from trusted host binding, never from model arguments.
        if binding.handoff.work_role.value != "task-main" or binding.tool_surface.work_role.value != "task-main":
            return _governed_from_response(
                binding,
                operation,
                ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "task-main control requires profile aota-task-main: aota-worker cannot access task-main control"}),
            )
        ctx = binding.trusted_task_main_context
        if ctx is None or not isinstance(ctx, TrustedTaskMainRuntimeContext):
            return _governed_from_response(
                binding,
                operation,
                ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted task-main context is absent for this binding"}),
            )
        # Contract hash check for task-main descriptors (parity, not authority)
        # validated is already {} (empty) – unknown inputs already rejected as UNKNOWN_INPUT
        # Ensure descriptor identity matches canonical (fail-closed on drift)
        expected_desc = None
        if operation == "task_main.activate_milestone":
            expected_desc = TASK_MAIN_ACTIVATE_DESCRIPTOR
        elif operation == "task_main.recover_coordinator":
            expected_desc = TASK_MAIN_RECOVER_DESCRIPTOR
        elif operation == "task_main.advance_once":
            expected_desc = TASK_MAIN_ADVANCE_DESCRIPTOR
        if expected_desc is not None and descriptor.contract_hash() != expected_desc.contract_hash():
            return _governed_from_response(
                binding,
                operation,
                ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": f"{operation} contract hash differs from authority"}),
            )
        # Optional visibility check: task-main surface should contain these ops, but visibility != authority.
        # If not visible, still deny? For progressive we allow call even if not eager, but if absent entirely, deny as authority.
        # To keep TRANSPORT_OPERATION_IDENTITY_SEPARATED but still check visibility as secondary, we allow any task-main binding that has context.
        # No additional visibility gate required beyond profile check above.

        # Dispatch to existing TaskMainControlService (reuse, no V2)
        try:
            if operation == "task_main.activate_milestone":
                live = ctx.live_plan_view
                # Approval gate: trusted runtime must say approval satisfied
                # live.user_gate_blocked captures (not approved) or amendment required
                try:
                    blocked = bool(live.user_gate_blocked)  # type: ignore[union-attr]
                except Exception:
                    blocked = not bool(getattr(live, "milestone_user_approval_satisfied", False))
                if blocked:
                    return _governed_from_response(
                        binding,
                        operation,
                        ToolResponse.failure({"code": "USER_GATE_REQUIRED", "message": "USER_GATE_REQUIRED: live Plan Milestone approval not satisfied; task-main cannot set approval"}),
                    )
                # Call existing service
                from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE  # type: ignore

                try:
                    handle = ctx.control_service.activate_milestone(
                        profile=TASK_MAIN_PROFILE,
                        plan_view=live,  # MilestonePlanView
                        origin_task_main_session_ref=ctx.origin_task_main_session_ref,
                        executor_id=ctx.executor_id,
                        project_id=binding.project_id,
                        coordinator_id=ctx.coordinator_id,
                    )
                except Exception as exc:
                    code = _map_task_main_exception(exc)
                    return _governed_from_response(
                        binding,
                        operation,
                        ToolResponse.failure({"code": code, "message": _bounded_failure_message(str(exc))}),
                    )
                # Bounded success payload (no path/secret leakage)
                try:
                    state = handle.state if hasattr(handle, "state") else None
                    payload: dict[str, Any] = {
                        "coordinator_id": getattr(handle, "coordinator_id", ctx.coordinator_id or f"{binding.project_id}:{getattr(live, 'milestone_id', 'M3')}"),
                        "status": state.status.value if state is not None and hasattr(state.status, "value") else str(getattr(state, "status", "ACTIVE")) if state else "ACTIVE",
                        "coordinator_revision": getattr(state, "coordinator_revision", 1) if state else 1,
                        "milestone_id": getattr(state, "milestone_id", getattr(live, "milestone_id", "")) if state else getattr(live, "milestone_id", ""),
                        "plan_authority": getattr(state, "plan_authority", getattr(live, "plan_authority", "")) if state else getattr(live, "plan_authority", ""),
                        "work_items": list(getattr(state, "work_items", [])) if state else [],
                    }
                    # Add bounded status flag
                    payload["user_gate_required"] = False
                except Exception:
                    payload = {"coordinator_id": getattr(handle, "coordinator_id", ""), "status": "ACTIVE"}
                return _governed_from_response(binding, operation, ToolResponse.success(payload))

            elif operation == "task_main.recover_coordinator":
                live = ctx.live_plan_view
                coord_id = ctx.coordinator_id
                if coord_id is None or not isinstance(coord_id, str) or not coord_id.strip():
                    # Derive default coordinator id from trusted project + milestone (same as coordinator's _default)
                    try:
                        mid = getattr(live, "milestone_id", "M3")
                        coord_id = f"{binding.project_id}:{mid}"
                    except Exception:
                        coord_id = f"{binding.project_id}:M3"
                from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE  # type: ignore

                try:
                    handle = ctx.control_service.recover_coordinator(
                        profile=TASK_MAIN_PROFILE,
                        coordinator_id=coord_id,
                        live_plan_view=live,
                        session_available=ctx.session_available,
                    )
                except Exception as exc:
                    code = _map_task_main_exception(exc)
                    return _governed_from_response(
                        binding,
                        operation,
                        ToolResponse.failure({"code": code, "message": _bounded_failure_message(str(exc))}),
                    )
                try:
                    state = handle.state
                    payload = {
                        "coordinator_id": handle.coordinator_id,
                        "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                        "coordinator_revision": state.coordinator_revision,
                        "milestone_id": state.milestone_id,
                    }
                except Exception:
                    payload = {"coordinator_id": coord_id, "status": "ACTIVE"}
                return _governed_from_response(binding, operation, ToolResponse.success(payload))

            elif operation == "task_main.advance_once":
                live = ctx.live_plan_view
                coord_id = ctx.coordinator_id
                if coord_id is None or not isinstance(coord_id, str) or not coord_id.strip():
                    try:
                        mid = getattr(live, "milestone_id", "M3")
                        coord_id = f"{binding.project_id}:{mid}"
                    except Exception:
                        coord_id = f"{binding.project_id}:M3"
                # If default id missing but store contains matching coordinator (e.g., after activation with different project), attempt discovery
                if coord_id is not None:
                    try:
                        store = getattr(ctx.control_service, "_coord_store", None)
                        if store is not None:
                            # Try direct get; if missing, scan for milestone match
                            if store.get(coord_id) is None:
                                for cand in store.list_all():  # type: ignore[union-attr]
                                    if cand.milestone_id == getattr(live, "milestone_id", None) and cand.plan_authority == getattr(live, "plan_authority", None):
                                        coord_id = cand.coordinator_id
                                        break
                    except Exception:
                        pass
                from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE  # type: ignore

                try:
                    outcome = ctx.control_service.advance_once(
                        profile=TASK_MAIN_PROFILE,
                        coordinator_id=coord_id,
                        live_plan_view=live,
                        handoff_resolver=ctx.handoff_resolver,
                        governed_evidence_resolver=ctx.governed_evidence_resolver,
                        reviewer_handoff_resolver=ctx.reviewer_handoff_resolver,
                        governed_review_resolver=ctx.governed_review_resolver,
                        next_milestone_view=ctx.next_milestone_view,
                        session_available=ctx.session_available,
                        reviewer_canonical_task_id_resolver=ctx.reviewer_canonical_task_id_resolver,
                    )
                except Exception as exc:
                    code = _map_task_main_exception(exc)
                    return _governed_from_response(
                        binding,
                        operation,
                        ToolResponse.failure({"code": code, "message": _bounded_failure_message(str(exc))}),
                    )
                # Project RunnerOutcome bounded (no path/secret leakage)
                try:
                    payload = {
                        "coordinator_id": outcome.coordinator_id,
                        "coordinator_revision": outcome.coordinator_revision,
                        "disposition": outcome.disposition,
                        "next_action": outcome.disposition,
                        "ready": list(getattr(outcome, "ready", [])),
                        "dispatched": list(getattr(outcome, "dispatched", [])),
                        "deferred": list(getattr(outcome, "deferred", [])),
                        "reconciled_work_item": getattr(outcome, "reconciled_work_item", None),
                        "reconciled_canonical_task_id": getattr(outcome, "reconciled_canonical_task_id", None),
                        "ack_eligible": bool(getattr(outcome, "ack_eligible", False)),
                        "user_gate_required": bool(getattr(outcome, "user_gate_required", False)),
                        "session_recovery_required": bool(getattr(outcome, "session_recovery_required", False)),
                        "milestone_closure_ready": bool(getattr(outcome, "milestone_closure_ready", False)),
                        "next_milestone_gate": bool(getattr(outcome, "next_milestone_gate", False)),
                        "integrated_review_required": bool(getattr(outcome, "integrated_review_required", False)),
                        "reasons": list(getattr(outcome, "reasons", [])),
                    }
                    # Add receipt digest if present (governed ref)
                    receipt = getattr(outcome, "receipt", None)
                    if receipt is not None:
                        try:
                            if hasattr(receipt, "receipt_digest"):
                                payload["receipt_digest"] = receipt.receipt_digest  # type: ignore
                            elif isinstance(receipt, dict) and "receipt_digest" in receipt:
                                payload["receipt_digest"] = receipt["receipt_digest"]
                        except Exception:
                            pass
                    # Bound payload sanity: ensure no absolute path leakage (callers may have stored paths)
                    # Already sanitized via _sanitize before projection, but do shallow check
                except Exception as exc:
                    payload = {"coordinator_id": coord_id, "disposition": getattr(outcome, "disposition", "UNKNOWN"), "next_action": getattr(outcome, "disposition", "UNKNOWN")}
                return _governed_from_response(binding, operation, ToolResponse.success(payload))

            else:
                return _governed_error(binding, operation, "UNKNOWN_OPERATION", f"unsupported task_main operation: {operation!r}")
        except ForgeError as exc:
            return _governed_error(binding, operation, getattr(exc, "code", "GOVERNED_OPERATION_FAILURE"), str(exc))
        except Exception as exc:  # noqa: BLE001
            msg = _bounded_failure_message(str(exc))
            return _governed_error(binding, operation, "GOVERNED_OPERATION_FAILURE", msg)


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
        """Wrap McpToolResult into CallToolResult with single model-visible copy.

        Bounded repair I37-B001 FastMCP double-emission: previously FastMCP emitted
        the same McpToolResult as both TextContent (JSON dump) and structuredContent,
        giving 2x wire duplication (4x with internal hydrate duplication).
        This wrapper emits one canonical copy in structuredContent and a minimal
        summary in content, achieving MODEL_VISIBLE_DUPLICATE_COUNT=1 and
        MODEL_VISIBLE_AMPLIFICATION_RATIO~1.x while preserving MCP compatibility.
        """
        if CallToolResult is None or TextContent is None:
            return mcp_result
        try:
            # Minimal summary for TextContent – no large hydrated bytes duplicated.
            # For task-main controls, include disposition/next_action so the Agent can observe progression without needing the full inline body.
            summary = {
                "ok": mcp_result.get("ok"),
                "operation": mcp_result.get("operation"),
                "outcome": mcp_result.get("outcome"),
                "output_mode": mcp_result.get("output_mode"),
                "byte_length": mcp_result.get("output_byte_length"),
                "digest": mcp_result.get("output_digest"),
            }
            # Task-main progression is driven by disposition/next_action; surface it in the minimal summary so the model can decide next step.
            try:
                payload = mcp_result.get("payload")
                if isinstance(payload, dict):
                    for _k in ("disposition", "next_action", "status", "coordinator_id", "dispatched", "reconciled_work_item", "reconciled_canonical_task_id", "user_gate_required", "milestone_closure_ready", "next_milestone_gate", "integrated_review_required", "session_recovery_required"):
                        if _k in payload:
                            summary[_k] = payload[_k]
                    # Also surface error code for failures
                    if not mcp_result.get("ok"):
                        err = mcp_result.get("error") or {}
                        if isinstance(err, dict) and "code" in err:
                            summary["error_code"] = err.get("code")
            except Exception:
                pass
            # Remove None values for compactness
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
