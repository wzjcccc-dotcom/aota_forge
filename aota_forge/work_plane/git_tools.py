"""Governed Git / Project Lifecycle Exposure — S2 M3-W3.

Wraps existing Git mechanics (core/git/inspect) through governed Tool
architecture without creating a new Git state machine or lifecycle engine.

Invariants
----------
* RETAIN_EXISTING_GIT_MECHANICS=yes — delegates to aota_forge.core.git.inspect
* NEW_GIT_STATE_MACHINE=no
* NEW_PROJECT_LIFECYCLE_ENGINE_CREATED=no
* M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD=no
* MINIMAL_GIT_SURFACE=yes — only git.status + git.diff (read-only, bounded)
* GENERIC_GIT_COMMAND_EXECUTION=no
* RAW_GIT_COMMAND_STRING_ACCEPTED=no
* GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE=yes
* CALLER_SUPPLIED_ARBITRARY_REPO_PATH=no
* CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED=yes
* CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED=yes
* TOOL_EXPOSURE_IS_GIT_AUTHORITY=no
* WORK_ROLE_IS_GIT_AUTHORITY=no
* SANDBOX_IS_GIT_AUTHORITY=no
* GIT_MUTATION_AUTHORITY_REQUIRED=yes (read-only v0 has no mutation exposed)
* IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE=yes
* IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED=no
* DESTRUCTIVE_GIT_OPERATION_REQUIRED_FOR_W3_PASS=no
* LIVE_GIT_REMOTE_MUTATION_REQUIRED=no
* NETWORK_CALL_REQUIRED_FOR_W3=no
* GIT_TOOL_IS_WORKTREE_DECISION_OWNER=no
* EXISTING_TOOL_RESULT_GOVERNANCE_REUSED=yes — ToolResponse
* NEW_GIT_RESULT_ONTOLOGY_CREATED=no
* GIT_READ_OUTPUT_BOUNDED=yes
* GIT_RESULT_IS_AUTHORITY=no
* COMMIT_SHA_IS_AUTHORITY=no
* BRANCH_REF_IS_AUTHORITY=no
* RESTRICTED_SHELL_IMPLEMENTED_IN_W3=no
* GENERIC_PROCESS_TOOL_CREATED=no
* No ambient cwd authority; no shell fallback.

Reuse
-----
* Reuses WorktreeSandboxBoundary (M1 W1)
* Reuses TaskHandoff bounded_scope (S1)
* Reuses AgentsPolicyCandidate applicability (S1 M2 W2)
* Reuses OperationContractDescriptor (existing, read-only)
* Reuses ToolProvider / ToolRequest / ToolResponse unchanged
* Reuses core/git/inspect mechanics: inspect_git, find_git_root, _run_git
* Delegates, never copies Git implementation logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor, READ_ONLY
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate, resolve_applicable_policies
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# Existing Git mechanics reuse — delegate, do not copy
from aota_forge.core.git.inspect import (
    GIT_TIMEOUT,
    HARD_MAX_ENTRIES,
    _run_git,
    find_git_root,
    inspect_git,
)

# ---------------------------------------------------------------------------
# Public invariant flags (for tests / downstream seam)
# ---------------------------------------------------------------------------

RETAIN_EXISTING_GIT_MECHANICS: bool = True
NEW_GIT_STATE_MACHINE_CREATED: bool = False
NEW_PROJECT_LIFECYCLE_ENGINE_CREATED: bool = False
M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD: bool = False

MINIMAL_GIT_SURFACE: bool = True

GENERIC_GIT_COMMAND_EXECUTION: bool = False
RAW_GIT_COMMAND_STRING_ACCEPTED: bool = False
GENERIC_GIT_EXEC_EXPOSED: bool = False

GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE: bool = True
CALLER_SUPPLIED_ARBITRARY_REPO_PATH: bool = False
CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED: bool = True
CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED: bool = True

TOOL_EXPOSURE_IS_GIT_AUTHORITY: bool = False
WORK_ROLE_IS_GIT_AUTHORITY: bool = False
SANDBOX_IS_GIT_AUTHORITY: bool = False
TOOL_EXPOSURE_IS_AUTHORITY: bool = False  # alias for shared checks

GIT_MUTATION_AUTHORITY_REQUIRED: bool = True
GIT_MUTATION_OPERATION_EXPOSED: bool = False

IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE: bool = True
IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED: bool = False

DESTRUCTIVE_GIT_OPERATION_REQUIRED_FOR_W3_PASS: bool = False

LIVE_GIT_REMOTE_MUTATION_REQUIRED: bool = False
NETWORK_CALL_REQUIRED_FOR_W3: bool = False

GIT_TOOL_IS_WORKTREE_DECISION_OWNER: bool = False

EXISTING_GIT_MECHANICS_REUSED: bool = True
EXISTING_GIT_MECHANICS_SEAM: str = "aota_forge.core.git.inspect:inspect_git+find_git_root+_run_git"
EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_REQUEST_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_OPERATION_DESCRIPTOR_REUSED: bool = True
EXISTING_TOOL_RESULT_GOVERNANCE_REUSED: bool = True
EXISTING_TOOL_RESULT_GOVERNANCE_SEAM: str = "aota_forge.core.providers.tool.ToolResponse"

GIT_READ_OUTPUT_BOUNDED: bool = True
GIT_RESULT_IS_AUTHORITY: bool = False
COMMIT_SHA_IS_AUTHORITY: bool = False
BRANCH_REF_IS_AUTHORITY: bool = False

NEW_GIT_RESULT_ONTOLOGY_CREATED: bool = False
RESTRICTED_SHELL_IMPLEMENTED_IN_W3: bool = False
GENERIC_PROCESS_TOOL_CREATED: bool = False

M2_SHARED_FILE_CHANGE_COUNT: int = 0
S1_HIGH_CONFLICT_FILE_CHANGE_COUNT: int = 0
AGGREGATOR_EXPORT_UPDATED: bool = False

# Negative scope markers
NEW_GIT_JOURNAL_CREATED: bool = False
NEW_GIT_TRANSACTION_ENGINE_CREATED: bool = False
TOOL_HYDRATION_REQUIRED_FOR_GIT: bool = False

# ---------------------------------------------------------------------------
# Bounds (local Milestone constants, not global Plan authority)
# ---------------------------------------------------------------------------

MAX_GIT_STATUS_ENTRIES: int = 100
MAX_GIT_DIFF_ENTRIES: int = 100
MAX_GIT_OUTPUT_BYTES: int = 32 * 1024
MAX_GIT_DIFF_BYTES: int = 32 * 1024
HARD_MAX_GIT_OUTPUT_BYTES: int = 64 * 1024

EXPOSED_GIT_OPERATIONS: tuple[str, ...] = ("git.status", "git.diff")

# ---------------------------------------------------------------------------
# Descriptors — canonical YAML projection (thin compatibility reference)
# ---------------------------------------------------------------------------
# Single semantic authority is .aota/contracts/operations.yaml via loader.
# These symbols are compatibility projections deterministically derived from
# the canonical source.
# TOOL_SCHEMA_SECOND_AUTHORITY=no

def _load_canonical_descriptor(name: str) -> OperationContractDescriptor:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map

    root = discover_canonical_project_root()
    return load_operation_descriptor_map(root)[name]


GIT_STATUS_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("git.status")

GIT_DIFF_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("git.diff")

assert GIT_STATUS_DESCRIPTOR.read_write == READ_ONLY
assert GIT_DIFF_DESCRIPTOR.read_write == READ_ONLY
assert GIT_STATUS_DESCRIPTOR.mutation_scope is None
assert GIT_DIFF_DESCRIPTOR.mutation_scope is None
assert GIT_STATUS_DESCRIPTOR.approval_required is False
assert GIT_DIFF_DESCRIPTOR.approval_required is False

_ALLOWED_GIT_OPERATIONS: frozenset[str] = frozenset({"git.status", "git.diff"})
_DESCRIPTOR_BY_NAME: dict[str, OperationContractDescriptor] = {
    "git.status": GIT_STATUS_DESCRIPTOR,
    "git.diff": GIT_DIFF_DESCRIPTOR,
}

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class GitAuthorityError(ValueError):
    """Missing or invalid trusted Git invocation context (fail-closed)."""


class GitOperationError(ValueError):
    """Bounded Git operation failure (fail-closed)."""


# ---------------------------------------------------------------------------
# Authority evidence — bounded value object, not a decision engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitOperationAuthorityEvidence:
    """Trusted operation-authority evidence for one Git operation.

    Aggregates already-trusted prerequisites mechanically; does not decide
    policy or invent authority. Model-supplied dicts cannot self-assert.
    """

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    applicable_policies: tuple[AgentsPolicyCandidate, ...]
    operation: OperationContractDescriptor
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise GitAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise GitAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.applicable_policies, (tuple, list)):
            raise GitAuthorityError("applicable_policies must be tuple or list")
        for idx, p in enumerate(self.applicable_policies):
            if not isinstance(p, AgentsPolicyCandidate):
                raise GitAuthorityError(f"applicable_policies[{idx}] must be AgentsPolicyCandidate")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise GitAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        if self.operation.read_write != READ_ONLY:
            raise GitAuthorityError("git operation must be read-only")
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise GitAuthorityError("evidence_id must be non-empty string")
        if self.operation.name not in _ALLOWED_GIT_OPERATIONS:
            raise GitAuthorityError(f"operation name must be one of {sorted(_ALLOWED_GIT_OPERATIONS)}, got {self.operation.name!r}")
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


def create_git_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    applicable_policies: Sequence[AgentsPolicyCandidate],
    operation: OperationContractDescriptor,
    *,
    evidence_id: str | None = None,
) -> GitOperationAuthorityEvidence:
    """Create trusted Git operation-authority evidence.

    Validates that all prerequisites are mechanically present:
    sandbox, handoff bounded_scope, applicable policies are valid for project_id,
    operation is canonical read-only git operation.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise GitAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise GitAuthorityError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(operation, OperationContractDescriptor):
        raise GitAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    operation.validate()
    if operation.read_write != READ_ONLY:
        raise GitAuthorityError("operation must be read-only")
    if operation.name not in _ALLOWED_GIT_OPERATIONS:
        raise GitAuthorityError(f"unsupported git operation: {operation.name!r}")
    policies_tuple = tuple(applicable_policies) if applicable_policies is not None else ()
    for p in policies_tuple:
        if not isinstance(p, AgentsPolicyCandidate):
            raise GitAuthorityError(f"policy must be AgentsPolicyCandidate, got {type(p).__name__}")
    if len(policies_tuple) > 0:
        resolved = resolve_applicable_policies(policies_tuple, sandbox.project_id)
        # keep deterministic ordering as per S1
        policies_tuple = resolved
    if evidence_id is None:
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}"
        evidence_id = "gse-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise GitAuthorityError("evidence_id must be non-empty string")
        evidence_id = evidence_id.strip()
        if len(evidence_id) > 128:
            raise GitAuthorityError("evidence_id exceeds bound")
        if "/" in evidence_id or "\\" in evidence_id:
            raise GitAuthorityError("evidence_id must not contain path separators")
    return GitOperationAuthorityEvidence(
        sandbox=sandbox,
        handoff=handoff,
        applicable_policies=policies_tuple,
        operation=operation,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------------------
# Helpers — bounded, deterministic, no shell
# ---------------------------------------------------------------------------


def _validate_max_entries(value: object) -> int:
    if value is None:
        return MAX_GIT_STATUS_ENTRIES
    if isinstance(value, bool) or not isinstance(value, int):
        raise GitOperationError(f"max_entries must be int, got {type(value).__name__}")
    if value <= 0:
        raise GitOperationError("max_entries must be positive")
    if value > HARD_MAX_ENTRIES:
        raise GitOperationError(f"max_entries {value} exceeds hard max {HARD_MAX_ENTRIES}")
    if value > MAX_GIT_STATUS_ENTRIES and value <= HARD_MAX_ENTRIES:
        # allow up to HARD_MAX but enforce bound; reject excessive beyond soft bound
        # For W3 we bound soft to 100, hard 500 — cap rather than silent
        if value > MAX_GIT_STATUS_ENTRIES:
            raise GitOperationError(f"max_entries {value} exceeds soft max {MAX_GIT_STATUS_ENTRIES}")
    return value


def _ensure_git_root_under_worktree(sandbox: WorktreeSandboxBoundary, git_root: Path, worktree_canonical: Path) -> None:
    try:
        git_canonical = git_root.resolve(strict=True)
    except OSError as exc:
        raise GitOperationError(f"git root resolve failed: {exc}") from exc
    try:
        git_canonical.relative_to(worktree_canonical)
    except ValueError as exc:
        raise GitOperationError(f"git repository escapes trusted worktree: {git_canonical} not under {worktree_canonical}") from exc
    # Also reject symlink at git root itself (already checked at sandbox but double-check)
    try:
        if git_root.is_symlink():
            raise GitOperationError(f"git root is symlink: {git_root}")
    except OSError as exc:
        raise GitOperationError(f"symlink check failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider — implements existing ToolProvider, consumes trusted evidence
# ---------------------------------------------------------------------------


class BoundedGitToolProvider:
    """Bounded Git Tool provider handling git.status and git.diff.

    Implements existing ToolProvider; does NOT create new ontology.
    Consumes trusted WorktreeSandboxBoundary + TaskHandoff + policy chain +
    OperationContractDescriptor evidence (GitOperationAuthorityEvidence).

    Delegates to existing core/git/inspect mechanics (inspect_git, find_git_root,
    _run_git) with structured argv, shell=False, timeout, bounded output.

    No mutation, no irreversible gate, no network, no generic git.exec.
    """

    WORK_PROVIDER_IS_AUTHORITY_DECISION_MAKER: bool = False

    def __init__(self, authority: GitOperationAuthorityEvidence) -> None:
        if not isinstance(authority, GitOperationAuthorityEvidence):
            raise GitAuthorityError(
                f"authority must be GitOperationAuthorityEvidence (trusted evidence), got {type(authority).__name__}"
            )
        sb = authority.sandbox
        root = Path(sb.worktree_root)
        try:
            if root.is_symlink():
                raise GitAuthorityError(f"sandbox root is symlink: {root}")
            if not root.exists():
                raise GitAuthorityError(f"sandbox root does not exist: {root}")
            if not root.is_dir():
                raise GitAuthorityError(f"sandbox root is not a directory: {root}")
        except OSError as exc:
            raise GitAuthorityError(f"sandbox root inaccessible: {exc}") from exc
        try:
            self._root = Path(sb.worktree_root).resolve(strict=True)
        except OSError as exc:
            raise GitAuthorityError(f"sandbox root resolve failed: {exc}") from exc
        # Also store project_root canonical for boundary
        try:
            self._project_root = Path(sb.project_root).resolve(strict=True)
        except OSError as exc:
            raise GitAuthorityError(f"project root resolve failed: {exc}") from exc
        self._authority = authority
        self._sandbox = sb
        self._handoff = authority.handoff
        self._policies = authority.applicable_policies
        self._operation = authority.operation

    @property
    def authority(self) -> GitOperationAuthorityEvidence:
        return self._authority

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self._sandbox

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        try:
            if self._authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_MISSING", "message": "missing trusted operation-authority evidence"})
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure(
                    {"code": "OPERATION_MISMATCH", "message": f"request operation {request.operation.name!r} != authority {self._authority.operation.name!r}"}
                )
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            if request.operation.name not in _ALLOWED_GIT_OPERATIONS:
                return ToolResponse.failure({"code": "UNSUPPORTED_GIT_OPERATION", "message": f"unsupported git operation: {request.operation.name!r}"})
            # Revalidate sandbox root
            root = Path(self._sandbox.worktree_root)
            try:
                if root.is_symlink() or not root.exists() or not root.is_dir():
                    return ToolResponse.failure({"code": "SANDBOX_STALE", "message": "sandbox root stale or missing"})
                root_canonical = root.resolve(strict=True)
                if root_canonical != self._root:
                    return ToolResponse.failure({"code": "SANDBOX_MISMATCH", "message": "sandbox canonical root mismatch"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SANDBOX_ERROR", "message": f"sandbox revalidation failed: {exc}"})
            if len(self._policies) > 0:
                try:
                    resolve_applicable_policies(self._policies, self._sandbox.project_id)
                except Exception as exc:
                    return ToolResponse.failure({"code": "POLICY_CONTEXT_INVALID", "message": f"policy context invalid: {exc}"})
            if not self._handoff.bounded_scope or not self._handoff.bounded_scope.strip():
                return ToolResponse.failure({"code": "TASK_SCOPE_MISSING", "message": "task scope missing"})
            # Dispatch — only structured read operations, no generic exec
            if request.operation.name == "git.status":
                return self._handle_status(request)
            elif request.operation.name == "git.diff":
                return self._handle_diff(request)
            else:
                return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported git operation: {request.operation.name!r}"})
        except Exception as exc:
            if isinstance(exc, (GitAuthorityError, GitOperationError)):
                return ToolResponse.failure({"code": "GIT_ERROR", "message": str(exc)})
            return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})

    def _handle_status(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        max_entries_val = inputs.get("max_entries")
        # Validate max_entries bound, but allow None -> default
        try:
            # reuse _validate but also let ToolRequest validation catch type mismatch? inputs already validated via descriptor (int? only)
            # We still defensively check
            if max_entries_val is not None:
                if isinstance(max_entries_val, bool) or not isinstance(max_entries_val, int):
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "max_entries must be int"})
                if max_entries_val <= 0 or max_entries_val > HARD_MAX_ENTRIES:
                    return ToolResponse.failure({"code": "BOUNDED_OUTPUT_EXCEEDED", "message": f"max_entries {max_entries_val} exceeds bound"})
                if max_entries_val > MAX_GIT_STATUS_ENTRIES:
                    return ToolResponse.failure({"code": "BOUNDED_OUTPUT_EXCEEDED", "message": f"max_entries {max_entries_val} exceeds soft max {MAX_GIT_STATUS_ENTRIES}"})
                max_entries = min(int(max_entries_val), MAX_GIT_STATUS_ENTRIES)
            else:
                max_entries = MAX_GIT_STATUS_ENTRIES
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": str(exc)})
        # Reject any unexpected inputs that look like arbitrary repo path / cwd injection attempts
        # Known descriptor only has max_entries; ToolRequest already rejected unknown inputs deterministically via validate_inputs.
        # Additional fail-closed for path tricks encoded in max_entries? Not needed.
        # Also explicitly reject caller-supplied arbitrary repo path keys if somehow smuggled via extra mapping (defensive)
        for forbidden in ("repo_path", "cwd", "git_dir", "worktree", "project_root", "argv", "command", "shell"):
            if forbidden in inputs:
                return ToolResponse.failure({"code": "FORBIDDEN_INPUT", "message": f"input {forbidden!r} not allowed for git.status"})
        project_root = Path(self._sandbox.project_root)
        boundary = Path(self._sandbox.project_root)
        # Ensure project_root under worktree root containment (canonical)
        try:
            project_root.resolve(strict=True).relative_to(self._root)
        except ValueError:
            return ToolResponse.failure({"code": "CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED", "message": "project root escapes trusted worktree"})
        except OSError as exc:
            return ToolResponse.failure({"code": "PROJECT_ROOT_ERROR", "message": f"project root resolve failed: {exc}"})
        # Delegate to existing mechanics — inspect_git
        try:
            result = inspect_git(project_root, boundary=boundary, max_entries=max_entries)
        except ForgeError as exc:
            return ToolResponse.failure({"code": exc.code, "message": exc.message})
        except Exception as exc:
            return ToolResponse.failure({"code": "GIT_ERROR", "message": f"git inspection failed: {exc}"})
        # Ensure git_root under worktree containment (re-resolve find_git_root)
        try:
            git_root = find_git_root(project_root, boundary)
            _ensure_git_root_under_worktree(self._sandbox, git_root, self._root)
        except ForgeError as exc:
            return ToolResponse.failure({"code": exc.code, "message": exc.message})
        except Exception as exc:
            return ToolResponse.failure({"code": "GIT_BOUNDARY_ERROR", "message": str(exc)})
        # Bound output bytes
        try:
            payload_bytes = len(canonical_json(result).encode("utf-8"))
        except Exception:
            payload_bytes = len(str(result).encode("utf-8"))
        if payload_bytes > HARD_MAX_GIT_OUTPUT_BYTES:
            return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": f"git status output size {payload_bytes} exceeds max {HARD_MAX_GIT_OUTPUT_BYTES}"})
        # Add trusted worktree identity without mutating Git result authority
        payload = dict(result)
        payload["project_id"] = self._sandbox.project_id
        payload["worktree_id"] = self._sandbox.worktree_id
        # Truthful that SHA is not authority — just evidence
        return ToolResponse.success(payload)

    def _handle_diff(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        max_entries_val = inputs.get("max_entries")
        try:
            if max_entries_val is not None:
                if isinstance(max_entries_val, bool) or not isinstance(max_entries_val, int):
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": "max_entries must be int"})
                if max_entries_val <= 0 or max_entries_val > HARD_MAX_ENTRIES:
                    return ToolResponse.failure({"code": "BOUNDED_OUTPUT_EXCEEDED", "message": f"max_entries {max_entries_val} exceeds bound"})
                if max_entries_val > MAX_GIT_DIFF_ENTRIES:
                    return ToolResponse.failure({"code": "BOUNDED_OUTPUT_EXCEEDED", "message": f"max_entries {max_entries_val} exceeds soft max {MAX_GIT_DIFF_ENTRIES}"})
                max_entries = min(int(max_entries_val), MAX_GIT_DIFF_ENTRIES)
            else:
                max_entries = MAX_GIT_DIFF_ENTRIES
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": str(exc)})
        for forbidden in ("repo_path", "cwd", "git_dir", "argv", "command", "shell", "force", "hard", "clean"):
            if forbidden in inputs:
                return ToolResponse.failure({"code": "FORBIDDEN_INPUT", "message": f"input {forbidden!r} not allowed for git.diff"})
        project_root = Path(self._sandbox.project_root)
        boundary = Path(self._sandbox.project_root)
        try:
            project_root.resolve(strict=True).relative_to(self._root)
        except ValueError:
            return ToolResponse.failure({"code": "CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED", "message": "project root escapes trusted worktree"})
        except OSError as exc:
            return ToolResponse.failure({"code": "PROJECT_ROOT_ERROR", "message": str(exc)})
        try:
            git_root = find_git_root(project_root, boundary)
            _ensure_git_root_under_worktree(self._sandbox, git_root, self._root)
        except ForgeError as exc:
            return ToolResponse.failure({"code": exc.code, "message": exc.message})
        except Exception as exc:
            return ToolResponse.failure({"code": "GIT_BOUNDARY_ERROR", "message": str(exc)})
        # Delegates to existing git mechanic via structured argv, shell=False, bounded
        # Use deterministic bounded diff: --numstat (structured) plus optional --stat filtered
        # We expose only bounded stat; not raw diff content unbounded
        try:
            # --numstat gives lines: <added> <deleted> <path>  bounded and parseable
            out, err, rc = _run_git(git_root, ["git", "diff", "--numstat"], timeout=GIT_TIMEOUT)
            if rc != 0:
                # diff may be empty (rc 0) or fail; treat non-zero as error unless empty
                # git diff returns 0 even when diff exists; non-zero indicates error
                return ToolResponse.failure({"code": "GIT_DIFF_FAILED", "message": err.strip() or "git diff failed"})
            lines = [ln for ln in out.split("\n") if ln.strip()]
            # Also get name-only to cross-check? but numstat is sufficient for bounded evidence
            # Truncate deterministically
            truncated = len(lines) > max_entries
            lines_bounded = lines[:max_entries]
            # Parse to structured but keep bounded output
            entries: list[dict[str, Any]] = []
            total_bytes_est = 0
            for ln in lines_bounded:
                parts = ln.split("\t")
                if len(parts) < 3:
                    continue
                added, deleted, path = parts[0], parts[1], parts[2]
                # bound path length
                if len(path) > 512:
                    path = path[:512]
                entries.append({"path": path, "added": added, "deleted": deleted})
                total_bytes_est += len(path)
                if total_bytes_est > MAX_GIT_DIFF_BYTES:
                    break
            # Also fetch git status head SHA for truthful ref evidence (not authority)
            head_sha, _, rc2 = _run_git(git_root, ["git", "rev-parse", "HEAD"], timeout=GIT_TIMEOUT)
            head_sha = head_sha.strip() if rc2 == 0 else ""
            branch, _, rc3 = _run_git(git_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=GIT_TIMEOUT)
            branch = branch.strip() if rc3 == 0 else "unknown"
            payload = {
                "available": True,
                "git_root": str(git_root.relative_to(boundary.resolve())) if git_root != boundary.resolve() else ".",
                "branch": branch,
                "head_sha": head_sha,
                "head_short": head_sha[:12] if len(head_sha) == 40 else head_sha,
                "entries": entries,
                "total_entries": len(lines),
                "truncated": truncated,
                "boundary": str(boundary.resolve()),
                "project_id": self._sandbox.project_id,
                "worktree_id": self._sandbox.worktree_id,
            }
            pb = len(canonical_json(payload).encode("utf-8"))
            if pb > HARD_MAX_GIT_OUTPUT_BYTES:
                return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": f"git diff output size {pb} exceeds max"})
            return ToolResponse.success(payload)
        except ForgeError as exc:
            return ToolResponse.failure({"code": exc.code, "message": exc.message})
        except Exception as exc:
            return ToolResponse.failure({"code": "GIT_ERROR", "message": f"git diff failed: {exc}"})


__all__ = [
    "GIT_STATUS_DESCRIPTOR",
    "GIT_DIFF_DESCRIPTOR",
    "GitOperationAuthorityEvidence",
    "create_git_authority",
    "BoundedGitToolProvider",
    "GitAuthorityError",
    "GitOperationError",
    "MAX_GIT_STATUS_ENTRIES",
    "MAX_GIT_DIFF_ENTRIES",
    "MAX_GIT_OUTPUT_BYTES",
    "EXPOSED_GIT_OPERATIONS",
    # flags
    "RETAIN_EXISTING_GIT_MECHANICS",
    "NEW_GIT_STATE_MACHINE_CREATED",
    "NEW_PROJECT_LIFECYCLE_ENGINE_CREATED",
    "M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD",
    "MINIMAL_GIT_SURFACE",
    "GENERIC_GIT_COMMAND_EXECUTION",
    "RAW_GIT_COMMAND_STRING_ACCEPTED",
    "GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE",
    "CALLER_SUPPLIED_ARBITRARY_REPO_PATH",
    "CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED",
    "CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED",
    "TOOL_EXPOSURE_IS_GIT_AUTHORITY",
    "WORK_ROLE_IS_GIT_AUTHORITY",
    "SANDBOX_IS_GIT_AUTHORITY",
    "GIT_MUTATION_AUTHORITY_REQUIRED",
    "GIT_MUTATION_OPERATION_EXPOSED",
    "IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE",
    "IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED",
    "DESTRUCTIVE_GIT_OPERATION_REQUIRED_FOR_W3_PASS",
    "LIVE_GIT_REMOTE_MUTATION_REQUIRED",
    "NETWORK_CALL_REQUIRED_FOR_W3",
    "GIT_TOOL_IS_WORKTREE_DECISION_OWNER",
    "EXISTING_GIT_MECHANICS_REUSED",
    "EXISTING_GIT_MECHANICS_SEAM",
    "EXISTING_TOOL_PROVIDER_REUSED",
    "EXISTING_TOOL_REQUEST_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "EXISTING_OPERATION_DESCRIPTOR_REUSED",
    "EXISTING_TOOL_RESULT_GOVERNANCE_REUSED",
    "GIT_READ_OUTPUT_BOUNDED",
    "GIT_RESULT_IS_AUTHORITY",
    "COMMIT_SHA_IS_AUTHORITY",
    "BRANCH_REF_IS_AUTHORITY",
    "NEW_GIT_RESULT_ONTOLOGY_CREATED",
    "RESTRICTED_SHELL_IMPLEMENTED_IN_W3",
    "GENERIC_PROCESS_TOOL_CREATED",
    "M2_SHARED_FILE_CHANGE_COUNT",
    "S1_HIGH_CONFLICT_FILE_CHANGE_COUNT",
    "AGGREGATOR_EXPORT_UPDATED",
]
