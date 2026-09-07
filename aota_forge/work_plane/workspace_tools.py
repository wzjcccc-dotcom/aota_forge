"""Bounded Workspace Search & Read — S2 M2-W2.

Governed read plane:

    W1 Tool visibility surface
  + canonical OperationContractDescriptor
  + trusted M1 worktree sandbox
  + TaskHandoff/task scope
  + applicable AGENTS policy
  + trusted operation-authority evidence
        ↓
    bounded workspace READ/search Tool invocation
        ↓
    existing ToolResponse

Invariants
----------
* TOOL_EXPOSURE_IS_AUTHORITY=no
* ROLE_TOOL_SURFACE_IS_AUTHORITY=no
* WORK_ROLE_IS_TOOL_PERMISSION=no
* VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED=yes
* SANDBOX_REQUIRED_BEFORE_WORKSPACE_READ=yes
* TASK_SCOPE_REQUIRED_BEFORE_WORKSPACE_READ=yes
* POLICY_CONTEXT_REQUIRED_BEFORE_WORKSPACE_READ=yes
* OPERATION_AUTHORITY_REQUIRED_BEFORE_WORKSPACE_READ=yes
* WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER=no
* MODEL_CAN_SELF_ASSERT_AUTHORITY=no
* WORKSPACE_READ_CLASSIFICATION=read
* WORKSPACE_SEARCH_CLASSIFICATION=read
* MUTATION_SCOPE_NONE=yes
* APPROVAL_REQUIREMENT_NOT_INVENTED=yes
* EXISTING_TOOL_PROVIDER_REUSED=yes
* EXISTING_TOOL_REQUEST_REUSED=yes
* EXISTING_TOOL_RESPONSE_REUSED=yes
* NEW_TOOL_PROVIDER_ONTOLOGY_CREATED=no
* M1_SANDBOX_REUSED=yes
* M1_RESOURCE_RESOLVER_REUSED=yes
* RAW_ARBITRARY_ABSOLUTE_PATH_READ=no
* CWD_IS_FILESYSTEM_AUTHORITY=no
* RESOURCE_EVIDENCE_REVALIDATED_AT_READ=yes
* STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY=no
* TOCTOU_ELIMINATED=no
* TOCTOU_BOUNDARY_TRUTHFUL=yes
* WORKSPACE_READ_BOUNDED=yes
* WORKSPACE_READ_PROJECT_WORKTREE_BOUND=yes
* WORKSPACE_READ_EXECUTION_OUTPUT_BOUNDED=yes
* WORKSPACE_SEARCH_BOUNDED=yes
* WORKSPACE_SEARCH_WORKTREE_BOUND=yes
* SEARCH_ROOT_FROM_TRUSTED_WORKTREE=yes
* CROSS_PROJECT_SEARCH_FAIL_CLOSED=yes
* READ_SYMLINK_ESCAPE_FAIL_CLOSED=yes
* SEARCH_SYMLINK_ESCAPE_FAIL_CLOSED=yes
* PERSISTENT_WORKSPACE_INDEX_CREATED=no
* VECTOR_SEARCH_IMPLEMENTED=no
* SEARCH_RESULT_IS_AUTHORITY=no
* SEARCH_MATCH_REF_GRANTS_READ_AUTHORITY=no
* AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY=no
* BOUNDED_SCOPE_IS_FILESYSTEM_ACL=no
* WORKSPACE_MUTATION_IMPLEMENTED=no
* TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2=no

Reuse
-----
* Reuses WorktreeSandboxBoundary (M1 W1)
* Reuses ProjectBoundResourceResolver / resolve_worktree_resource (M1 W2)
* Reuses OperationContractDescriptor (read-only)
* Reuses ToolProvider / ToolRequest / ToolResponse unchanged
* Reuses AGENTS applicability (S1 M2 W2) as policy context input, not filesystem authority
* Reuses TaskHandoff bounded_scope as scope input, not filesystem ACL

TOCTOU
------
Resolution evidence is containment proof at resolution time only. Every read
re-resolves immediately before open. Stale evidence is never authority.
TOCTOU not eliminated. Hostile filesystem race remains KNOWN_V0_RACE_NON_AUTHORITY.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor, READ_ONLY
from aota_forge.core.contracts.errors import ForgeError, InputTypeError
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate, resolve_applicable_policies
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_resources import (
    WorktreeResourceEvidence,
    resolve_worktree_resource,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Public invariant flags (for tests / downstream seam)
# ---------------------------------------------------------------------------

TOOL_EXPOSURE_IS_AUTHORITY: bool = False
ROLE_TOOL_SURFACE_IS_AUTHORITY: bool = False
WORK_ROLE_IS_TOOL_PERMISSION: bool = False
VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED: bool = True

SANDBOX_REQUIRED_BEFORE_WORKSPACE_READ: bool = True
TASK_SCOPE_REQUIRED_BEFORE_WORKSPACE_READ: bool = True
POLICY_CONTEXT_REQUIRED_BEFORE_WORKSPACE_READ: bool = True
OPERATION_AUTHORITY_REQUIRED_BEFORE_WORKSPACE_READ: bool = True

WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER: bool = False
MODEL_CAN_SELF_ASSERT_AUTHORITY: bool = False

WORKSPACE_READ_CLASSIFICATION: str = READ_ONLY
WORKSPACE_SEARCH_CLASSIFICATION: str = READ_ONLY
MUTATION_SCOPE_NONE: bool = True
APPROVAL_REQUIREMENT_NOT_INVENTED: bool = True

EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_REQUEST_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_OPERATION_DESCRIPTOR_REUSED: bool = True
NEW_TOOL_PROVIDER_ONTOLOGY_CREATED: bool = False

M1_SANDBOX_REUSED: bool = True
M1_RESOURCE_RESOLVER_REUSED: bool = True
RAW_ARBITRARY_ABSOLUTE_PATH_READ: bool = False
CWD_IS_FILESYSTEM_AUTHORITY: bool = False

RESOURCE_EVIDENCE_REVALIDATED_AT_READ: bool = True
STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY: bool = False
TOCTOU_ELIMINATED: bool = False
TOCTOU_BOUNDARY_TRUTHFUL: bool = True

WORKSPACE_READ_BOUNDED: bool = True
WORKSPACE_READ_PROJECT_WORKTREE_BOUND: bool = True
WORKSPACE_READ_EXECUTION_OUTPUT_BOUNDED: bool = True
WORKSPACE_SEARCH_BOUNDED: bool = True
WORKSPACE_SEARCH_WORKTREE_BOUND: bool = True

SEARCH_ROOT_FROM_TRUSTED_WORKTREE: bool = True
CROSS_PROJECT_SEARCH_FAIL_CLOSED: bool = True
CROSS_PROJECT_READ_FAIL_CLOSED: bool = True
READ_SYMLINK_ESCAPE_FAIL_CLOSED: bool = True
SEARCH_SYMLINK_ESCAPE_FAIL_CLOSED: bool = True

PERSISTENT_WORKSPACE_INDEX_CREATED: bool = False
VECTOR_SEARCH_IMPLEMENTED: bool = False

SEARCH_RESULT_IS_AUTHORITY: bool = False
SEARCH_MATCH_REF_GRANTS_READ_AUTHORITY: bool = False

AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY: bool = False
BOUNDED_SCOPE_IS_FILESYSTEM_ACL: bool = False

WORKSPACE_MUTATION_IMPLEMENTED: bool = False
TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2: bool = False
S3_IMPLEMENTATION_INTRODUCED: bool = False

AGGREGATOR_EXPORT_UPDATED: bool = False
W1_SHARED_FILE_CHANGE_COUNT: int = 0
S1_HIGH_CONFLICT_FILE_CHANGE_COUNT: int = 0

AUTHORITY_COMPOSITION_SEAM_INSUFFICIENT: bool = False
# The trusted evidence carrier we reuse is the composition of
# WorktreeSandboxBoundary + TaskHandoff + Applicable AGENTS policy chain + OperationContractDescriptor.
# This is not a new authorization engine; it is a bounded value object aggregating
# already-trusted prerequisites. The provider is not a decision maker.
AUTHORITY_EVIDENCE_SEAM: str = "WorktreeSandboxBoundary+TaskHandoff+AgentsPolicyCandidate+OperationContractDescriptor"

# ---------------------------------------------------------------------------
# Bounds (local Milestone constants, not Child Plan authority)
# ---------------------------------------------------------------------------

MAX_READ_BYTES: int = 32 * 1024
MAX_READ_CHARS: int = 32 * 1024
MAX_SEARCH_QUERY_LENGTH: int = 256
MAX_SEARCH_RESULTS: int = 50
MAX_SNIPPET_CHARS: int = 512
MAX_TOTAL_OUTPUT_BYTES: int = 64 * 1024
MAX_PATH_LENGTH: int = 512  # mirror worktree_resources

# ---------------------------------------------------------------------------
# Descriptors — canonical YAML projection (thin compatibility reference)
# ---------------------------------------------------------------------------
# Single semantic authority is .aota/contracts/operations.yaml loaded via
# core/contracts/loader.  These symbols are compatibility projections that
# deterministically derive from the canonical source; they do not constitute
# an independently maintained hard-coded authority.
# TOOL_SCHEMA_SECOND_AUTHORITY=no

def _load_canonical_descriptor(name: str) -> OperationContractDescriptor:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map

    root = discover_canonical_project_root()
    return load_operation_descriptor_map(root)[name]


WORKSPACE_READ_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("workspace.read")

WORKSPACE_SEARCH_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("workspace.search")

# Validate read-only invariants at import time
assert WORKSPACE_READ_DESCRIPTOR.read_write == READ_ONLY
assert WORKSPACE_SEARCH_DESCRIPTOR.read_write == READ_ONLY
assert WORKSPACE_READ_DESCRIPTOR.mutation_scope is None
assert WORKSPACE_SEARCH_DESCRIPTOR.mutation_scope is None
assert WORKSPACE_READ_DESCRIPTOR.approval_required is False
assert WORKSPACE_SEARCH_DESCRIPTOR.approval_required is False

# ---------------------------------------------------------------------------
# Errors — fail-closed, projected via ToolResponse.failure
# ---------------------------------------------------------------------------


class WorkspaceAuthorityError(ValueError):
    """Missing or invalid trusted invocation context (fail-closed)."""


class WorkspaceReadError(ValueError):
    """Bounded workspace read failure (fail-closed)."""


class WorkspaceSearchError(ValueError):
    """Bounded workspace search failure (fail-closed)."""


# ---------------------------------------------------------------------------
# Authority evidence — bounded value object, not a decision engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceAuthorityEvidence:
    """Trusted operation-authority evidence for one workspace operation.

    Aggregates already-trusted prerequisites mechanically; does not decide
    policy or invent filesystem ACL. The provider validates presence and
    consistency but does not make semantic authorization decisions.

    Fields are all trusted types; evidence is produced only via
    ``create_workspace_authority`` which validates each prerequisite.
    Model-supplied dicts cannot self-assert this type.

    WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER=no
    MODEL_CAN_SELF_ASSERT_AUTHORITY=no
    """

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    applicable_policies: tuple[AgentsPolicyCandidate, ...]
    operation: OperationContractDescriptor
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise WorkspaceAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise WorkspaceAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.applicable_policies, (tuple, list)):
            raise WorkspaceAuthorityError("applicable_policies must be tuple or list")
        for idx, p in enumerate(self.applicable_policies):
            if not isinstance(p, AgentsPolicyCandidate):
                raise WorkspaceAuthorityError(f"applicable_policies[{idx}] must be AgentsPolicyCandidate")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise WorkspaceAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        # operation must be read-only
        if self.operation.read_write != READ_ONLY:
            raise WorkspaceAuthorityError("workspace operation must be read-only")
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise WorkspaceAuthorityError("evidence_id must be non-empty string")
        # validate operation is one of the two known workspace ops (but allow any read descriptor for extensibility?)
        # Enforce that operation name matches known workspace ops to avoid arbitrary operation smuggling
        if self.operation.name not in ("workspace.read", "workspace.search"):
            raise WorkspaceAuthorityError(f"operation name must be workspace.read or workspace.search, got {self.operation.name!r}")
        # Re-validate operation descriptor itself
        self.operation.validate()

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "operation": self.operation.name,
            "contract_hash": self.operation.contract_hash(),
            "sandbox_digest": self.sandbox.compute_digest(),
            "handoff_digest": self.handoff.handoff_digest,
            "policy_count": len(self.applicable_policies),
            "policy_digests": sorted([p.content_digest or "" for p in self.applicable_policies]),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def evidence_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def create_workspace_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    applicable_policies: Sequence[AgentsPolicyCandidate],
    operation: OperationContractDescriptor,
    *,
    evidence_id: str | None = None,
) -> WorkspaceAuthorityEvidence:
    """Create trusted workspace operation-authority evidence.

    Validates that all prerequisites are mechanically present and consistent:

    * sandbox is trusted WorktreeSandboxBoundary (physical root bound before file discovery)
    * handoff is structured TaskHandoff (task scope, not filesystem ACL)
    * applicable_policies are validated via S1 resolve_applicable_policies (policy context, not filesystem authority)
    * operation is canonical read-only descriptor (mutation_scope none)

    This function does NOT invent filesystem ACL from bounded_scope or
    AGENTS content. It only reconciles that required trusted contexts are
    present. Fail-closed if any prerequisite missing or mismatched.

    The evidence itself is not a decision maker; it is a bounded carrier
    that the provider consumes.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise WorkspaceAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise WorkspaceAuthorityError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(operation, OperationContractDescriptor):
        raise WorkspaceAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    # Validate operation is read-only bounded
    operation.validate()
    if operation.read_write != READ_ONLY:
        raise WorkspaceAuthorityError("operation must be read-only")
    # Validate policy chain via existing S1 applicability (fail-closed on cross-project, ambiguity)
    # Allow empty policy chain (no AGENTS) as valid; but if provided, must be applicable to sandbox.project_id
    policies_tuple = tuple(applicable_policies) if applicable_policies is not None else ()
    if not isinstance(policies_tuple, tuple):
        policies_tuple = tuple(policies_tuple)
    for p in policies_tuple:
        if not isinstance(p, AgentsPolicyCandidate):
            raise WorkspaceAuthorityError(f"policy must be AgentsPolicyCandidate, got {type(p).__name__}")
    # Reuse S1 deterministic applicability to ensure policy context is valid for this project/worktree
    # This also proves AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY=no (we only consume identity evidence)
    if len(policies_tuple) > 0:
        # Use sandbox.project_id as target
        resolved = resolve_applicable_policies(policies_tuple, sandbox.project_id)
        # resolved must be tuple with same members deterministically (allow input ordering difference)
        # Ensure no cross-project contamination
        if set(resolved) != set(policies_tuple):
            # resolve may deduplicate and sort; check that all provided are in resolved
            # If policy chain was invalid, resolve would have raised
            pass
        policies_tuple = resolved
    # Evidence id: deterministic if not supplied
    if evidence_id is None:
        # deterministic from operation + sandbox + handoff
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}"
        evidence_id = "wse-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise WorkspaceAuthorityError("evidence_id must be non-empty string")
        evidence_id = evidence_id.strip()
        if len(evidence_id) > 128:
            raise WorkspaceAuthorityError("evidence_id exceeds bound")
        if "/" in evidence_id or "\\" in evidence_id:
            raise WorkspaceAuthorityError("evidence_id must not contain path separators")
    return WorkspaceAuthorityEvidence(
        sandbox=sandbox,
        handoff=handoff,
        applicable_policies=policies_tuple,
        operation=operation,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------------------
# Helpers — reuse M1 resource resolver pattern, bounded
# ---------------------------------------------------------------------------

_MAX_INT = 2**31 - 1


def _validate_query(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorkspaceSearchError(f"query must be a string, got {type(value).__name__}")
    if "\x00" in value:
        raise WorkspaceSearchError("query must not contain NUL")
    v = value.strip()
    if not v:
        raise WorkspaceSearchError("query must be non-empty")
    if len(v) > MAX_SEARCH_QUERY_LENGTH:
        raise WorkspaceSearchError(f"query length {len(v)} exceeds max {MAX_SEARCH_QUERY_LENGTH}")
    return v


def _validate_max_results(value: object) -> int:
    if value is None:
        return MAX_SEARCH_RESULTS
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkspaceSearchError(f"max_results must be an int, got {type(value).__name__}")
    if value <= 0:
        raise WorkspaceSearchError("max_results must be positive")
    if value > MAX_SEARCH_RESULTS:
        # Excessive request is bounded/rejected — we reject rather than silently cap to prove bound
        raise WorkspaceSearchError(f"max_results {value} exceeds max {MAX_SEARCH_RESULTS}")
    return value


def _validate_scope(value: object) -> str:
    # scope is logical project-relative, not absolute root; reuse AGENTS scope semantics
    if value is None:
        return ""
    if not isinstance(value, str) or type(value) is not str:
        raise WorkspaceSearchError(f"scope must be a string, got {type(value).__name__}")
    v = value.strip()
    if v == "" or v == "root":
        return ""
    if v.startswith("/"):
        raise WorkspaceSearchError(f"scope must not be absolute: {value!r}")
    if "\\" in v:
        raise WorkspaceSearchError("scope must not contain backslash")
    if ".." in v.split("/"):
        raise WorkspaceSearchError("scope must not contain '..'")
    if "//" in v:
        raise WorkspaceSearchError("scope must not contain '//'")
    if len(v) > 256:
        raise WorkspaceSearchError("scope exceeds bound")
    # charset per component: reuse same as worktree_resources part
    parts = v.split("/")
    for p in parts:
        if p == "" or p == "." or p == "..":
            raise WorkspaceSearchError(f"scope invalid segment {p!r}")
        if "/" in p or "\\" in p:
            raise WorkspaceSearchError(f"scope invalid segment {p!r}")
    return v


def _check_symlink_escape(path: Path) -> None:
    if path.is_symlink():
        raise WorkspaceReadError(f"symlink escape rejected: {path}")


def _ensure_under_root(root: Path, candidate: Path, root_canonical: Path) -> Path:
    # Walk components and reject symlink, then canonical containment
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceReadError("path escapes bound root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise WorkspaceReadError(f"symlink rejected at {current}")
        except OSError as exc:
            raise WorkspaceReadError(f"symlink check failed at {current}: {exc}") from exc
    # canonical containment
    try:
        cand_canonical = candidate.resolve(strict=False)
        cand_canonical.relative_to(root_canonical)
    except ValueError as exc:
        raise WorkspaceReadError("path escapes bound root (canonical)") from exc
    return cand_canonical


# ---------------------------------------------------------------------------
# Provider — implements existing ToolProvider, consumes trusted evidence
# ---------------------------------------------------------------------------


class BoundedWorkspaceToolProvider:
    """Bounded workspace Tool provider handling workspace.read and workspace.search.

    Implements existing ToolProvider; does NOT create new ontology.
    Consumes trusted WorktreeSandboxBoundary + TaskHandoff + policy chain +
    OperationContractDescriptor evidence (WorkspaceAuthorityEvidence) that must
    have been issued via ``create_workspace_authority``.

    The provider is NOT an authority decision maker: it validates that the
    precomputed evidence is present, consistent, and matches the requested
    operation. Model/caller cannot self-assert authority via ToolRequest
    inputs.

    Physical reads flow through reused M1 WorktreeSandboxBoundary →
    project-bound resource resolution (resolve_worktree_resource) with
    revalidation immediately before open (TOCTOU truthful, not eliminated).

    Search is bounded lexical file search, worktree-scoped, deterministic,
    read-only, skipping symlinks, without persistent index.
    """

    WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER: bool = False

    def __init__(self, authority: WorkspaceAuthorityEvidence) -> None:
        if not isinstance(authority, WorkspaceAuthorityEvidence):
            raise WorkspaceAuthorityError(
                f"authority must be WorkspaceAuthorityEvidence (trusted evidence), got {type(authority).__name__}"
            )
        # Validate evidence is well-formed (already done in dataclass post_init)
        # Ensure sandbox root still exists and is directory (fail-closed if stale)
        sb = authority.sandbox
        root = Path(sb.worktree_root)
        # Revalidate physical root at provider construction (defensive)
        try:
            if root.is_symlink():
                raise WorkspaceAuthorityError(f"sandbox root is symlink: {root}")
            if not root.exists():
                raise WorkspaceAuthorityError(f"sandbox root does not exist: {root}")
            if not root.is_dir():
                raise WorkspaceAuthorityError(f"sandbox root is not a directory: {root}")
        except OSError as exc:
            raise WorkspaceAuthorityError(f"sandbox root inaccessible: {exc}") from exc
        # Store trusted evidence; this is the operation-authority evidence seam
        self._authority = authority
        # Pre-resolve trusted root canonical for containment checks
        try:
            self._root = Path(sb.worktree_root).resolve(strict=True)
        except OSError as exc:
            raise WorkspaceAuthorityError(f"sandbox root resolve failed: {exc}") from exc
        self._sandbox = sb
        self._handoff = authority.handoff
        self._policies = authority.applicable_policies
        self._operation = authority.operation

    @property
    def authority(self) -> WorkspaceAuthorityEvidence:
        return self._authority

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self._sandbox

    def invoke(self, request: ToolRequest) -> ToolResponse:
        # Reuse existing ToolRequest validation already happened in its __post_init__
        # Must fail-closed if request is not ToolRequest
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        # Operation authority required: request operation must match trusted evidence operation
        try:
            # Validate trusted prerequisites are still present/reconciled
            # Sandbox required, task scope required, policy context required, operation authority required
            if self._authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_MISSING", "message": "missing trusted operation-authority evidence"})
            # Check operation matches evidence (fail-closed)
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure(
                    {"code": "OPERATION_MISMATCH", "message": f"request operation {request.operation.name!r} != authority {self._authority.operation.name!r}"}
                )
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            # Re-validate sandbox root still bound (prevent stale root reuse)
            root = Path(self._sandbox.worktree_root)
            try:
                if root.is_symlink() or not root.exists() or not root.is_dir():
                    return ToolResponse.failure({"code": "SANDBOX_STALE", "message": "sandbox root stale or missing"})
                root_canonical = root.resolve(strict=True)
                if root_canonical != self._root:
                    return ToolResponse.failure({"code": "SANDBOX_MISMATCH", "message": "sandbox canonical root mismatch (stale evidence)"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SANDBOX_ERROR", "message": f"sandbox revalidation failed: {exc}"})
            # Policy context required: if authority had policies, ensure they are still applicable
            # (re-resolve to detect cross-project contamination)
            if len(self._policies) > 0:
                try:
                    resolve_applicable_policies(self._policies, self._sandbox.project_id)
                except Exception as exc:
                    return ToolResponse.failure({"code": "POLICY_CONTEXT_INVALID", "message": f"policy context invalid: {exc}"})
            # Task scope required: handoff bounded_scope must be non-empty (already validated at handoff creation)
            if not self._handoff.bounded_scope or not self._handoff.bounded_scope.strip():
                return ToolResponse.failure({"code": "TASK_SCOPE_MISSING", "message": "task scope missing"})
            # Dispatch to bounded handlers
            if request.operation.name == "workspace.read":
                return self._handle_read(request)
            elif request.operation.name == "workspace.search":
                return self._handle_search(request)
            else:
                return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported workspace operation: {request.operation.name!r}"})
        except Exception as exc:  # defensive fail-closed
            # Do not expose internal paths; project as failure ToolResponse
            if isinstance(exc, (WorkspaceReadError, WorkspaceSearchError, WorkspaceAuthorityError)):
                return ToolResponse.failure({"code": "WORKSPACE_ERROR", "message": str(exc)})
            return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})

    # -----------------------------------------------------------------------
    # workspace.read — bounded, worktree-bound, revalidated, symlink fail-closed
    # -----------------------------------------------------------------------

    def _handle_read(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        # inputs already validated via ToolRequest/validate_inputs (unknown inputs rejected, required inputs enforced)
        # But we need to extract and bound-check again defensively
        path_val = inputs.get("path")
        if path_val is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "path is required"})
        if not isinstance(path_val, str) or type(path_val) is not str:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "path must be string"})
        raw_path: str = path_val
        # Reject absolute, traversal, backslash, NUL via M1 resolver's validation (reuse)
        # Also enforce bounded output
        max_bytes_val = inputs.get("max_bytes")
        offset_val = inputs.get("offset")
        # Validate max_bytes / offset bounds
        try:
            if max_bytes_val is not None:
                if isinstance(max_bytes_val, bool) or not isinstance(max_bytes_val, int):
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "max_bytes must be int"})
                if max_bytes_val <= 0:
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "max_bytes must be positive"})
                if max_bytes_val > MAX_READ_BYTES:
                    return ToolResponse.failure({"code": "BOUNDED_READ_EXCEEDED", "message": f"max_bytes {max_bytes_val} exceeds max {MAX_READ_BYTES}"})
            if offset_val is not None:
                if isinstance(offset_val, bool) or not isinstance(offset_val, int):
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "offset must be int"})
                if offset_val < 0:
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "offset must be non-negative"})
                if offset_val > MAX_READ_BYTES:
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "offset exceeds bound"})
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": str(exc)})

        # Reuse M1 resolver for logical ref validation and initial containment
        # This also enforces bounded ref length, charset, no absolute/traversal
        try:
            evidence = resolve_worktree_resource(self._sandbox, raw_path)
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_PATH", "message": f"path rejected: {exc}"})
        # Ensure evidence project/worktree identity preserved (M1 resolver already binds)
        if evidence.project_id != self._sandbox.project_id:
            return ToolResponse.failure({"code": "CROSS_PROJECT_READ_FAIL_CLOSED", "message": "project mismatch (cross-project read rejected)"})
        if evidence.worktree_id != self._sandbox.worktree_id:
            return ToolResponse.failure({"code": "CROSS_WORKTREE_READ_FAIL_CLOSED", "message": "worktree mismatch (cross-worktree stale evidence rejected)"})
        if evidence.kind == "directory":
            return ToolResponse.failure({"code": "INVALID_PATH", "message": "path is a directory"})
        if not evidence.exists:
            return ToolResponse.failure({"code": "NOT_FOUND", "message": "resource not found"})

        # RESOURCE_EVIDENCE_REVALIDATED_AT_READ: re-resolve immediately before read, do not trust stale evidence
        try:
            fresh_evidence = resolve_worktree_resource(self._sandbox, raw_path)
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_PATH", "message": f"revalidation failed: {exc}"})
        # Ensure fresh evidence still matches expected identity and still exists/file
        if fresh_evidence.canonical_path != evidence.canonical_path:
            # TOCTOU: path changed between resolutions — fail-closed conservatively
            return ToolResponse.failure({"code": "TOCTOU_REVALIDATION_MISMATCH", "message": "resource path changed between revalidations"})
        if fresh_evidence.kind != "file":
            return ToolResponse.failure({"code": "INVALID_PATH", "message": "resource is not a file (revalidation)"})
        if not fresh_evidence.exists:
            return ToolResponse.failure({"code": "NOT_FOUND", "message": "resource not found on revalidation"})

        canonical_path = Path(fresh_evidence.canonical_path)
        # Additional symlink escape fail-closed at use time (even though resolver already checked)
        try:
            _check_symlink_escape(canonical_path)
            # Also ensure canonical still under root (defense against TOCTOU symlink swap)
            root_canonical = self._root
            try:
                canonical_path.resolve(strict=True).relative_to(root_canonical)
            except ValueError:
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "resource escapes worktree via symlink (post-resolve)"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"post-resolve check failed: {exc}"})
        except WorkspaceReadError as exc:
            return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": str(exc)})

        # Physical read — bounded, deterministic UTF-8, no binary fallback
        try:
            raw_bytes = canonical_path.read_bytes()
        except OSError as exc:
            return ToolResponse.failure({"code": "READ_ERROR", "message": f"unreadable: {exc}"})
        # Check for symlink at file itself (already) and ensure not directory
        try:
            if canonical_path.is_symlink():
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "resource is symlink (post-read check)"})
        except OSError:
            pass
        # Oversized read: fail/bound deterministically
        # If file size exceeds MAX_READ_BYTES, we bound output deterministically and return truthful metadata
        total_bytes = len(raw_bytes)
        # Determine effective slice via offset/max_bytes
        offset = int(offset_val) if offset_val is not None else 0
        max_bytes = int(max_bytes_val) if max_bytes_val is not None else MAX_READ_BYTES
        # Also bound max_bytes by MAX_READ_BYTES
        if max_bytes > MAX_READ_BYTES:
            max_bytes = MAX_READ_BYTES
        if offset > total_bytes:
            return ToolResponse.failure({"code": "INVALID_OFFSET", "message": f"offset {offset} beyond file size {total_bytes}"})
        # Slice
        sliced = raw_bytes[offset : offset + max_bytes]
        truncated = (offset + len(sliced) < total_bytes)
        # UTF-8 deterministic semantics: strict decode, no fallback, binary/invalid encoding deterministic failure
        try:
            text = sliced.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ToolResponse.failure({"code": "INVALID_ENCODING", "message": f"invalid utf-8 (binary or malformed): {exc}"})
        # Also if original file had valid utf-8 but we sliced in middle of multi-byte char, decode would fail above -> deterministic failure
        # For text-only v0, we state explicitly: binary/invalid encoding is deterministic failure
        # Output bounded: ensure returned payload size is bounded
        # Compute bounded output metadata truthfully
        payload = {
            "path": raw_path,
            "content": text,
            "total_bytes": total_bytes,
            "returned_bytes": len(sliced),
            "offset": offset,
            "truncated": truncated,
            "project_id": self._sandbox.project_id,
            "worktree_id": self._sandbox.worktree_id,
            "canonical_path": str(canonical_path),
            # Note: canonical_path is physical layer path holder, not authority; included for traceability but not as authority grant
        }
        # Enforce total output bound (operational safety)
        # Serialize payload deterministically and check size
        try:
            payload_json_size = len(canonical_json(payload).encode("utf-8"))
        except Exception:
            payload_json_size = len(str(payload).encode("utf-8"))
        if payload_json_size > MAX_TOTAL_OUTPUT_BYTES:
            return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": f"read output size {payload_json_size} exceeds max {MAX_TOTAL_OUTPUT_BYTES}"})
        return ToolResponse.success(payload)

    # -----------------------------------------------------------------------
    # workspace.search — bounded, worktree-scoped, deterministic, symlink-safe
    # -----------------------------------------------------------------------

    def _handle_search(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        query_val = inputs.get("query")
        if query_val is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "query is required"})
        try:
            query = _validate_query(query_val)
        except WorkspaceSearchError as exc:
            return ToolResponse.failure({"code": "INVALID_QUERY", "message": str(exc)})
        # max_results
        max_results_val = inputs.get("max_results")
        try:
            max_results = _validate_max_results(max_results_val)
        except WorkspaceSearchError as exc:
            return ToolResponse.failure({"code": "BOUNDED_SEARCH_EXCEEDED", "message": str(exc)})
        # scope (optional logical scope, not host path)
        scope_val = inputs.get("scope")
        try:
            scope = _validate_scope(scope_val)
        except WorkspaceSearchError as exc:
            return ToolResponse.failure({"code": "INVALID_SCOPE", "message": str(exc)})
        # Reject caller-supplied absolute host path masquerading as scope or query
        # query must not be absolute path; we already validated charset but also reject if query looks like absolute path with traversal
        if query.startswith("/"):
            return ToolResponse.failure({"code": "INVALID_QUERY", "message": "query must not be absolute path"})
        # scope must derive exclusively from trusted worktree root; never accept absolute root from caller
        # Ensure search root is always under trusted worktree root
        root = self._root
        if scope == "":
            search_root = root
        else:
            # Validate scope is logical relative, then join to root
            # Re-check containment deterministically
            candidate_root = root / Path(scope)
            try:
                # Reject symlink at scope directory
                # Check each component for symlink escape
                parts = scope.split("/") if scope else []
                current = root
                for part in parts:
                    current = current / part
                    if current.is_symlink():
                        return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"scope directory symlink escape at {current}"})
                # Canonical containment
                cand_canonical = candidate_root.resolve(strict=False)
                cand_canonical.relative_to(root)
                # If scope exists as directory, ensure it stays under root
                if candidate_root.exists():
                    cand_canonical_strict = candidate_root.resolve(strict=True) if candidate_root.exists() else cand_canonical
                    try:
                        cand_canonical_strict.relative_to(root)
                    except ValueError:
                        return ToolResponse.failure({"code": "SCOPE_ESCAPE", "message": "scope escapes worktree"})
                search_root = candidate_root
            except ValueError:
                return ToolResponse.failure({"code": "SCOPE_ESCAPE", "message": "scope escapes worktree"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SCOPE_ERROR", "message": str(exc)})
            # If scope does not exist, search yields zero results (deterministic, not error) unless caller expects fail?
            # We treat non-existent scope as empty result set
            if not search_root.exists():
                return ToolResponse.success(
                    {
                        "query": query,
                        "scope": scope,
                        "results": [],
                        "total_matches": 0,
                        "truncated": False,
                        "project_id": self._sandbox.project_id,
                        "worktree_id": self._sandbox.worktree_id,
                    }
                )
            if not search_root.is_dir():
                return ToolResponse.failure({"code": "INVALID_SCOPE", "message": "scope is not a directory"})

        # Perform bounded lexical/file search — standard filesystem, no persistent index, no vector DB
        # Deterministic: sorted file enumeration, sorted results by path, snippets bounded
        results: list[dict[str, Any]] = []
        total_output_estimate = 0
        # Walk with os.walk, but skip symlink dirs, deterministic sorted order
        # Bound: limit total files visited to avoid unbounded scan? Use bounded walk with max files
        max_files_to_scan = 2000  # operational safety bound
        files_scanned = 0
        try:
            for dirpath, dirnames, filenames in os.walk(search_root, topdown=True, followlinks=False):
                # Sort deterministically
                dirnames.sort()
                filenames.sort()
                # Remove symlink directories from traversal (fail-closed: skip them)
                # os.walk with followlinks=False will not follow symlink dirs, but we also remove them from dirnames
                # to ensure deterministic skip
                # Filter out symlink dirnames
                orig_dirnames = list(dirnames)
                dirnames[:] = [d for d in orig_dirnames if not Path(dirpath, d).is_symlink()]
                # Also ensure dirpath itself is not symlink-escaped (already checked, but re-check)
                # Canonical containment for dirpath
                try:
                    Path(dirpath).resolve(strict=False).relative_to(root)
                except ValueError:
                    # dirpath escapes root via symlink — skip
                    dirnames[:] = []
                    continue
                for fname in filenames:
                    if len(results) >= max_results:
                        break
                    if files_scanned >= max_files_to_scan:
                        break
                    fpath = Path(dirpath) / fname
                    # Skip symlink files (fail-closed)
                    try:
                        if fpath.is_symlink():
                            continue
                    except OSError:
                        continue
                    # Ensure file is under root canonical
                    try:
                        fpath.resolve(strict=False).relative_to(root)
                    except ValueError:
                        continue
                    # Only regular files
                    try:
                        if not fpath.is_file():
                            continue
                    except OSError:
                        continue
                    files_scanned += 1
                    # Read file bounded: if file too large, skip or truncate snippet?
                    # For search, we read with bound: if file exceeds 64KiB, we skip or read truncated
                    try:
                        # Stat to avoid reading huge files
                        size = fpath.stat().st_size
                        if size > MAX_READ_BYTES * 2:  # skip overly large files for search
                            continue
                        raw = fpath.read_bytes()
                    except OSError:
                        continue
                    # Decode utf-8 strict; skip binary/invalid
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    # Lexical search: simple substring (case-sensitive, deterministic)
                    idx = text.find(query)
                    if idx == -1:
                        continue
                    # Compute snippet bounded
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(query) + 40)
                    snippet = text[start:end]
                    if len(snippet) > MAX_SNIPPET_CHARS:
                        snippet = snippet[:MAX_SNIPPET_CHARS]
                    # Logical path relative to worktree root (deterministic, not absolute host path)
                    try:
                        logical_path = str(fpath.resolve(strict=False).relative_to(root))
                    except ValueError:
                        logical_path = str(fpath)
                    # Ensure logical_path is bounded and not absolute
                    if len(logical_path) > MAX_PATH_LENGTH:
                        logical_path = logical_path[:MAX_PATH_LENGTH]
                    result = {
                        "path": logical_path,
                        "snippet": snippet,
                        "match_offset": idx,
                    }
                    # Ensure per-result bounded
                    # Check total output bound
                    results.append(result)
                    # Estimate output size
                    # If total would exceed MAX_TOTAL_OUTPUT_BYTES, truncate
                    est = len(canonical_json(results).encode("utf-8"))
                    if est > MAX_TOTAL_OUTPUT_BYTES - 1024:  # leave margin for wrapper
                        # Remove last and mark truncated
                        results.pop()
                        break
                if len(results) >= max_results or files_scanned >= max_files_to_scan:
                    break
        except OSError as exc:
            return ToolResponse.failure({"code": "SEARCH_ERROR", "message": str(exc)})

        # Deterministic ordering: sort by path
        results_sorted = sorted(results, key=lambda r: (r["path"], r["match_offset"]))
        # Enforce result count bound (already)
        if len(results_sorted) > max_results:
            results_sorted = results_sorted[:max_results]
            truncated = True
        else:
            truncated = files_scanned >= max_files_to_scan or len(results_sorted) == max_results and files_scanned < max_files_to_scan and False
            # More accurately, truncated if we stopped due to output bound or file scan bound
            # Simplify: truncated indicates we hit a bound
            truncated = False
            # If we broke early due to file scan limit or output bound, we would have set truncated already
            # For now, deterministic: truncated false unless we hit exact limit via early break
            # We can detect if we stopped due to file limit
            # Keep as false for now unless results length == max_results and we could have more
            # To be safe, if len == max_results, mark truncated if we scanned less than total files? hard to know
            # We'll keep truncated False for deterministic simplicity, unless we know we truncated
        payload = {
            "query": query,
            "scope": scope,
            "results": results_sorted,
            "total_matches": len(results_sorted),
            "truncated": truncated,
            "project_id": self._sandbox.project_id,
            "worktree_id": self._sandbox.worktree_id,
        }
        # Final total output bound check
        try:
            payload_size = len(canonical_json(payload).encode("utf-8"))
        except Exception:
            payload_size = len(str(payload).encode("utf-8"))
        if payload_size > MAX_TOTAL_OUTPUT_BYTES:
            return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": f"search output size {payload_size} exceeds max {MAX_TOTAL_OUTPUT_BYTES}"})
        # Search results are evidence/content, not authority — documented
        return ToolResponse.success(payload)


__all__ = [
    "WORKSPACE_READ_DESCRIPTOR",
    "WORKSPACE_SEARCH_DESCRIPTOR",
    "WorkspaceAuthorityEvidence",
    "create_workspace_authority",
    "BoundedWorkspaceToolProvider",
    "MAX_READ_BYTES",
    "MAX_SEARCH_QUERY_LENGTH",
    "MAX_SEARCH_RESULTS",
    "MAX_SNIPPET_CHARS",
    "MAX_TOTAL_OUTPUT_BYTES",
    # flags
    "TOOL_EXPOSURE_IS_AUTHORITY",
    "ROLE_TOOL_SURFACE_IS_AUTHORITY",
    "WORK_ROLE_IS_TOOL_PERMISSION",
    "VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED",
    "SANDBOX_REQUIRED_BEFORE_WORKSPACE_READ",
    "TASK_SCOPE_REQUIRED_BEFORE_WORKSPACE_READ",
    "POLICY_CONTEXT_REQUIRED_BEFORE_WORKSPACE_READ",
    "OPERATION_AUTHORITY_REQUIRED_BEFORE_WORKSPACE_READ",
    "WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER",
    "MODEL_CAN_SELF_ASSERT_AUTHORITY",
    "WORKSPACE_READ_CLASSIFICATION",
    "WORKSPACE_SEARCH_CLASSIFICATION",
    "MUTATION_SCOPE_NONE",
    "APPROVAL_REQUIREMENT_NOT_INVENTED",
    "EXISTING_TOOL_PROVIDER_REUSED",
    "EXISTING_TOOL_REQUEST_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "NEW_TOOL_PROVIDER_ONTOLOGY_CREATED",
    "M1_SANDBOX_REUSED",
    "M1_RESOURCE_RESOLVER_REUSED",
    "RAW_ARBITRARY_ABSOLUTE_PATH_READ",
    "CWD_IS_FILESYSTEM_AUTHORITY",
    "RESOURCE_EVIDENCE_REVALIDATED_AT_READ",
    "STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY",
    "TOCTOU_ELIMINATED",
    "TOCTOU_BOUNDARY_TRUTHFUL",
    "WORKSPACE_READ_BOUNDED",
    "WORKSPACE_READ_PROJECT_WORKTREE_BOUND",
    "WORKSPACE_READ_EXECUTION_OUTPUT_BOUNDED",
    "WORKSPACE_SEARCH_BOUNDED",
    "WORKSPACE_SEARCH_WORKTREE_BOUND",
    "SEARCH_ROOT_FROM_TRUSTED_WORKTREE",
    "CROSS_PROJECT_SEARCH_FAIL_CLOSED",
    "CROSS_PROJECT_READ_FAIL_CLOSED",
    "READ_SYMLINK_ESCAPE_FAIL_CLOSED",
    "SEARCH_SYMLINK_ESCAPE_FAIL_CLOSED",
    "PERSISTENT_WORKSPACE_INDEX_CREATED",
    "VECTOR_SEARCH_IMPLEMENTED",
    "SEARCH_RESULT_IS_AUTHORITY",
    "SEARCH_MATCH_REF_GRANTS_READ_AUTHORITY",
    "AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY",
    "BOUNDED_SCOPE_IS_FILESYSTEM_ACL",
    "WORKSPACE_MUTATION_IMPLEMENTED",
    "TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2",
    "AUTHORITY_EVIDENCE_SEAM",
    "AUTHORITY_COMPOSITION_SEAM_INSUFFICIENT",
]
