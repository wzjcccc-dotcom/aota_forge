"""Governed Workspace Mutation & Artifact Operations — S2 M3-W1.

Thin vertical slice:

    trusted sandbox + structured task scope + applicable policy context
    + write/mutation OperationContractDescriptor + trusted side-effect authority
    + approval where required
        ↓
    bounded workspace mutation (workspace.write)
        ↓
    ToolResponse
        ↓
    existing Tool Result Governance (projection via ToolOutputRef/GovernedReference)

Artifact reference is a governed reference to produced content/resource,
distinct from mutation authority or filesystem permission.

Invariants
----------
* READ_AUTHORITY_IS_WRITE_AUTHORITY=no
* RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY=no
* TOOL_EXPOSURE_IS_MUTATION_AUTHORITY=no
* WORK_ROLE_IS_MUTATION_AUTHORITY=no
* AGENTS_POLICY_IS_MUTATION_AUTHORITY=no
* OPERATION_DESCRIPTOR_IS_MUTATION_AUTHORITY_DECISION=no
* MUTATION_AUTHORITY_REQUIRED=yes
* WORKSPACE_WRITE_BOUNDED=yes
* WORKSPACE_WRITE_PROJECT_WORKTREE_BOUND=yes
* RAW_ABSOLUTE_WRITE_PATH_ALLOWED=no
* CWD_IS_MUTATION_AUTHORITY=no
* PATH_TRAVERSAL_WRITE_FAIL_CLOSED=yes
* CROSS_PROJECT_WRITE_FAIL_CLOSED=yes
* CROSS_WORKTREE_WRITE_FAIL_CLOSED=yes
* WRITE_TARGET_REVALIDATED_AT_USE=yes
* STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY=no
* WRITE_SYMLINK_ESCAPE_FAIL_CLOSED=yes
* DIRECTORY_SYMLINK_ESCAPE_FAIL_CLOSED=yes
* WRITE_TOCTOU_BOUNDARY_EXPLICIT=yes
* ATOMIC_REPLACE_SUPPORTED=yes (temporary sibling → atomic replace)
* MUTATION_RESULT_IS_AUTHORITY=no
* MUTATION_SIDE_EFFECT_AND_ARTIFACT_REFERENCE_SEPARATE=yes
* ARTIFACT_REFERENCE_IS_AUTHORITY=no
* ARTIFACT_REF_DIGEST_BOUND=yes
* ARTIFACT_REF_PROJECT_WORKTREE_SCOPED=yes
* ARTIFACT_REF_DIGEST_IS_AUTHORITY=no
* ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY=no
* NEW_PERSISTENT_ARTIFACT_STORE_CREATED=no
* WRITE_INPUT_BOUNDED=yes
* WRITE_RESULT_OUTPUT_BOUNDED=yes
* FAILED_MUTATION_REPORTED_AS_SUCCESS=no

Reuse
-----
* Reuses WorktreeSandboxBoundary (M1 W1)
* Reuses ProjectBoundResourceResolver / resolve_worktree_resource (M1 W2)
* Reuses OperationContractDescriptor (write)
* Reuses ToolProvider / ToolRequest / ToolResponse unchanged
* Reuses GovernedReference / ResultGovernanceProjection (M2 W3) as reference primitive
* Does NOT create WorkspaceToolProviderV2, MutationRuntime, FileAuthorityEngine, etc.

Minimal surface: workspace.write only (workspace.mkdir optional and not required).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind
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

READ_AUTHORITY_IS_WRITE_AUTHORITY: bool = False
RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY: bool = False
TOOL_EXPOSURE_IS_MUTATION_AUTHORITY: bool = False
WORK_ROLE_IS_MUTATION_AUTHORITY: bool = False
AGENTS_POLICY_IS_MUTATION_AUTHORITY: bool = False
OPERATION_DESCRIPTOR_IS_MUTATION_AUTHORITY_DECISION: bool = False

MUTATION_AUTHORITY_REQUIRED: bool = True
MUTATION_AUTHORITY_SEAM_INSUFFICIENT: bool = False
MUTATION_AUTHORITY_SEAM_USED: str = "WorktreeSandboxBoundary+TaskHandoff+AgentsPolicyCandidate+OperationContractDescriptor+WorkspaceMutationAuthorityEvidence"

WORKSPACE_WRITE_BOUNDED: bool = True
WORKSPACE_WRITE_PROJECT_WORKTREE_BOUND: bool = True
MINIMAL_MUTATION_SURFACE: bool = True

RAW_ABSOLUTE_WRITE_PATH_ALLOWED: bool = False
CWD_IS_MUTATION_AUTHORITY: bool = False
PATH_TRAVERSAL_WRITE_FAIL_CLOSED: bool = True
CROSS_PROJECT_WRITE_FAIL_CLOSED: bool = True
CROSS_WORKTREE_WRITE_FAIL_CLOSED: bool = True

WRITE_TARGET_REVALIDATED_AT_USE: bool = True
STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY: bool = False
WRITE_SYMLINK_ESCAPE_FAIL_CLOSED: bool = True
DIRECTORY_SYMLINK_ESCAPE_FAIL_CLOSED: bool = True
WRITE_TOCTOU_BOUNDARY_EXPLICIT: bool = True
WRITE_TOCTOU_ELIMINATED: bool = False
TOCTOU_BOUNDARY_TRUTHFUL: bool = True
ATOMIC_REPLACE_SUPPORTED: bool = True

MUTATION_RESULT_IS_AUTHORITY: bool = False
FAILED_MUTATION_REPORTED_AS_SUCCESS: bool = False

MUTATION_SIDE_EFFECT_AND_ARTIFACT_REFERENCE_SEPARATE: bool = True

ARTIFACT_REFERENCE_IS_AUTHORITY: bool = False
ARTIFACT_REF_DIGEST_BOUND: bool = True
ARTIFACT_REF_PROJECT_WORKTREE_SCOPED: bool = True
ARTIFACT_REF_DIGEST_IS_AUTHORITY: bool = False
ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY: bool = False
NEW_PERSISTENT_ARTIFACT_STORE_CREATED: bool = False
ARTIFACT_IMPLEMENTATION_MODE: str = "REFERENCE_CONTRACT_ONLY"
REFERENCE_CONTRACT_ONLY: bool = True

WRITE_INPUT_BOUNDED: bool = True
WRITE_RESULT_OUTPUT_BOUNDED: bool = True

EXISTING_TOOL_CONTRACT_REUSED: bool = True
M1_SANDBOX_REUSED: bool = True
M1_RESOURCE_RESOLVER_REUSED: bool = True
M2_RESULT_GOVERNANCE_REUSED: bool = True
EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_REQUEST_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_OPERATION_DESCRIPTOR_REUSED: bool = True

TEST_EXECUTION_IMPLEMENTED_IN_W1: bool = False
GIT_OPERATION_IMPLEMENTED_IN_W1: bool = False
RESTRICTED_SHELL_IMPLEMENTED_IN_W1: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_WRITE_BYTES: int = 4096
MAX_WRITE_PAYLOAD_CHARS: int = 4096
MAX_PATH_LENGTH: int = 512
MAX_TOTAL_OUTPUT_BYTES: int = 64 * 1024

WRITE_MODES: frozenset[str] = frozenset({"create_only", "replace_existing", "create_or_replace"})

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------

WORKSPACE_WRITE_DESCRIPTOR: OperationContractDescriptor = OperationContractDescriptor(
    name="workspace.write",
    description="Bounded workspace file write (worktree-scoped, mutation)",
    inputs=(
        InputSpec(name="path", type="str"),
        InputSpec(name="content", type="str"),
        InputSpec(name="mode", type="str"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write="write",
    mutation_scope="workspace",
    required_authority="trusted_workspace_mutation",
    approval_required=False,
    valid_predecessor_state="any",
    valid_successor_state="any",
    idempotency="not_idempotent",
    errors=("AUTHORITY_DENIED", "PATH_ESCAPE", "SYMLINK_ESCAPE", "OVERSIZED_PAYLOAD", "INVALID_MODE", "DIGEST_CONFLICT", "PHYSICAL_WRITE_FAILURE"),
    protocol_version=PROTOCOL_VERSION,
    decision_required=False,
    subject_revision_precondition=False,
    external_authority_precondition=False,
    result_contract="result.workspace.write.v1",
)

assert WORKSPACE_WRITE_DESCRIPTOR.read_write == "write"
assert WORKSPACE_WRITE_DESCRIPTOR.mutation_scope == "workspace"
assert WORKSPACE_WRITE_DESCRIPTOR.approval_required is False

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class WorkspaceMutationAuthorityError(ValueError):
    """Missing or invalid trusted mutation authority (fail-closed)."""


class WorkspaceMutationError(ValueError):
    """Bounded workspace mutation failure (fail-closed)."""


class ArtifactReferenceError(ValueError):
    """Artifact reference validation failure (fail-closed)."""


class ArtifactTamperError(ArtifactReferenceError):
    """Tampered artifact digest or scope (fail-closed)."""

# ---------------------------------------------------------------------------
# Mutation authority evidence — bounded value object, not a decision engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceMutationAuthority:
    """Trusted mutation/side-effect authority evidence for workspace.write.

    Aggregates already-trusted prerequisites mechanically; does not invent
    filesystem ACL. Distinct type from read authority so read cannot auto-grant write.
    """

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    applicable_policies: tuple[AgentsPolicyCandidate, ...]
    operation: OperationContractDescriptor
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise WorkspaceMutationAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise WorkspaceMutationAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.applicable_policies, (tuple, list)):
            raise WorkspaceMutationAuthorityError("applicable_policies must be tuple or list")
        for idx, p in enumerate(self.applicable_policies):
            if not isinstance(p, AgentsPolicyCandidate):
                raise WorkspaceMutationAuthorityError(f"applicable_policies[{idx}] must be AgentsPolicyCandidate")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise WorkspaceMutationAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        if self.operation.read_write == "read":
            raise WorkspaceMutationAuthorityError("workspace mutation authority requires write operation")
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise WorkspaceMutationAuthorityError("evidence_id must be non-empty string")
        if self.operation.name != "workspace.write":
            raise WorkspaceMutationAuthorityError(f"operation name must be workspace.write, got {self.operation.name!r}")
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

    @property
    def is_authority(self) -> bool:
        # This object IS the trusted mutation authority fact for the bounded operation
        # It is not a generic permission engine, only evidence for one operation
        return True


def create_workspace_mutation_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    applicable_policies: Sequence[AgentsPolicyCandidate],
    operation: OperationContractDescriptor,
    *,
    evidence_id: str | None = None,
) -> WorkspaceMutationAuthority:
    """Create trusted workspace mutation authority evidence.

    Validates that all prerequisites are mechanically present:
    * sandbox is trusted WorktreeSandboxBoundary
    * handoff is structured TaskHandoff
    * applicable_policies via S1 resolve_applicable_policies
    * operation is canonical write descriptor for workspace.write

    Fail-closed if any prerequisite missing or mismatched.
    Model-supplied dicts cannot self-assert this type.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise WorkspaceMutationAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise WorkspaceMutationAuthorityError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(operation, OperationContractDescriptor):
        raise WorkspaceMutationAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    operation.validate()
    if operation.read_write == "read":
        raise WorkspaceMutationAuthorityError("operation must be write for mutation authority")
    if operation.name != "workspace.write":
        raise WorkspaceMutationAuthorityError(f"operation must be workspace.write, got {operation.name!r}")
    policies_tuple = tuple(applicable_policies) if applicable_policies is not None else ()
    for p in policies_tuple:
        if not isinstance(p, AgentsPolicyCandidate):
            raise WorkspaceMutationAuthorityError(f"policy must be AgentsPolicyCandidate, got {type(p).__name__}")
    if len(policies_tuple) > 0:
        # Reuse S1 deterministic applicability
        resolved = resolve_applicable_policies(policies_tuple, sandbox.project_id)
        policies_tuple = resolved
    if evidence_id is None:
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}:mutation"
        evidence_id = "wma-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise WorkspaceMutationAuthorityError("evidence_id must be non-empty string")
        evidence_id = evidence_id.strip()
        if len(evidence_id) > 128:
            raise WorkspaceMutationAuthorityError("evidence_id exceeds bound")
        if "/" in evidence_id or "\\" in evidence_id:
            raise WorkspaceMutationAuthorityError("evidence_id must not contain path separators")
    return WorkspaceMutationAuthority(
        sandbox=sandbox,
        handoff=handoff,
        applicable_policies=policies_tuple,
        operation=operation,
        evidence_id=evidence_id,
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_mode(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorkspaceMutationError(f"mode must be a string, got {type(value).__name__}")
    v = value.strip()
    if v not in WRITE_MODES:
        raise WorkspaceMutationError(f"invalid write mode {value!r}, expected one of {sorted(WRITE_MODES)}")
    return v


def _validate_content(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorkspaceMutationError(f"content must be a string, got {type(value).__name__}")
    if "\x00" in value:
        raise WorkspaceMutationError("content must not contain NUL")
    b = value.encode("utf-8")
    if len(b) > MAX_WRITE_BYTES:
        raise WorkspaceMutationError(f"content bytes {len(b)} exceeds max {MAX_WRITE_BYTES}")
    return value


def _check_symlink_escape(path: Path) -> None:
    if path.is_symlink():
        raise WorkspaceMutationError(f"symlink escape rejected: {path}")


def _ensure_under_root(root: Path, candidate: Path, root_canonical: Path) -> Path:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceMutationError("path escapes bound root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise WorkspaceMutationError(f"symlink rejected at {current}")
        except OSError as exc:
            raise WorkspaceMutationError(f"symlink check failed at {current}: {exc}") from exc
    try:
        cand_canonical = candidate.resolve(strict=False)
        cand_canonical.relative_to(root_canonical)
    except ValueError as exc:
        raise WorkspaceMutationError("path escapes bound root (canonical)") from exc
    return cand_canonical

# ---------------------------------------------------------------------------
# Artifact reference — bounded non-authoritative digest-bound project/worktree ref
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactReference:
    """Bounded non-authoritative artifact reference.

    Carries logical ref, digest, project/worktree scope, byte_length.
    Reuses GovernedReference as evidence primitive bridge.

    Invariants:
        * ARTIFACT_REFERENCE_IS_AUTHORITY=no
        * ARTIFACT_REF_DIGEST_BOUND=yes
        * ARTIFACT_REF_PROJECT_WORKTREE_SCOPED=yes
    """

    logical_ref: str
    digest: str
    project_id: str
    worktree_id: str
    byte_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.logical_ref, str) or not self.logical_ref.strip():
            raise ArtifactReferenceError("logical_ref must be non-empty string")
        v = self.logical_ref.strip()
        if v.startswith("/"):
            raise ArtifactReferenceError("logical_ref must be project-relative, not absolute")
        if "\\" in v or "\x00" in v:
            raise ArtifactReferenceError("logical_ref must not contain backslash or NUL")
        if len(v) > MAX_PATH_LENGTH:
            raise ArtifactReferenceError(f"logical_ref exceeds max {MAX_PATH_LENGTH}")
        object.__setattr__(self, "logical_ref", v)
        if not isinstance(self.digest, str) or not _DIGEST_HEX_RE.fullmatch(self.digest.strip().lower()):
            raise ArtifactReferenceError(f"digest must be 64 lower hex chars: {self.digest!r}")
        object.__setattr__(self, "digest", self.digest.strip().lower())
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ArtifactReferenceError("project_id must be non-empty")
        if len(self.project_id) > 96:
            raise ArtifactReferenceError("project_id exceeds bound")
        if not isinstance(self.worktree_id, str) or not self.worktree_id.strip():
            raise ArtifactReferenceError("worktree_id must be non-empty")
        if len(self.worktree_id) > 128:
            raise ArtifactReferenceError("worktree_id exceeds bound")
        if type(self.byte_length) is not int:
            raise TypeError("byte_length must be int")
        if self.byte_length < 0:
            raise ValueError("byte_length must be >=0")

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "byte_length": self.byte_length,
            "digest": self.digest,
            "logical_ref": self.logical_ref,
            "project_id": self.project_id,
            "worktree_id": self.worktree_id,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactReference":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"logical_ref", "digest", "project_id", "worktree_id", "byte_length"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ArtifactReferenceError(f"Unknown field(s) in ArtifactReference: {sorted(extra)}")
        for req in ("logical_ref", "digest", "project_id", "worktree_id", "byte_length"):
            if req not in data:
                raise ArtifactReferenceError(f"Missing required field: {req!r}")
        return cls(
            logical_ref=data["logical_ref"],
            digest=data["digest"],
            project_id=data["project_id"],
            worktree_id=data["worktree_id"],
            byte_length=data["byte_length"],
        )

    def as_governed_reference(self) -> GovernedReference:
        """Bridge to existing GovernedReference (artifact kind)."""
        return GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref=self.logical_ref, digest=self.digest)

    def authorize(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("ArtifactReference is not authority; cannot authorize mutation")


def create_artifact_reference(
    sandbox: WorktreeSandboxBoundary,
    logical_ref: str,
    *,
    content: bytes | str | None = None,
) -> ArtifactReference:
    """Create artifact reference from a bounded produced resource.

    If content is None, reads the file at logical_ref inside sandbox and computes digest.
    Otherwise computes digest from supplied content bytes/str.

    The resulting reference is project/worktree scoped and digest-bound.
    It does NOT grant mutation authority.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ArtifactReferenceError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    # Validate logical_ref via resolver for containment proof (reuse)
    evidence = resolve_worktree_resource(sandbox, logical_ref)
    if evidence.project_id != sandbox.project_id:
        raise ArtifactReferenceError("project mismatch for artifact ref")
    if evidence.worktree_id != sandbox.worktree_id:
        raise ArtifactReferenceError("worktree mismatch for artifact ref")
    # If content supplied, compute digest from it; else read file
    if content is None:
        if not evidence.exists or evidence.kind != "file":
            raise ArtifactReferenceError("artifact resource must be an existing file")
        canonical_path = Path(evidence.canonical_path)
        # Revalidate containment at use (same as mutation)
        root_canonical = Path(sandbox.worktree_root).resolve(strict=True)
        try:
            if canonical_path.is_symlink():
                raise ArtifactReferenceError(f"artifact resource is symlink: {canonical_path}")
            canonical_path.resolve(strict=True).relative_to(root_canonical)
        except (ValueError, OSError) as exc:
            raise ArtifactReferenceError(f"artifact symlink escape or stale: {exc}") from exc
        try:
            raw_bytes = canonical_path.read_bytes()
        except OSError as exc:
            raise ArtifactReferenceError(f"artifact unreadable: {exc}") from exc
    else:
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        elif isinstance(content, bytes):
            raw_bytes = content
        else:
            raise ArtifactReferenceError(f"content must be str or bytes, got {type(content).__name__}")
        if len(raw_bytes) > MAX_WRITE_BYTES:
            raise ArtifactReferenceError(f"artifact content exceeds bound {MAX_WRITE_BYTES}")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return ArtifactReference(
        logical_ref=evidence.logical_ref,
        digest=digest,
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        byte_length=len(raw_bytes),
    )


def validate_artifact_reference(
    ref: ArtifactReference,
    current_sandbox: WorktreeSandboxBoundary,
    *,
    expected_digest: str | None = None,
) -> ArtifactReference:
    """Validate artifact reference integrity and scope.

    Fail-closed on tampered digest, foreign project/worktree, etc.
    Possession alone does NOT grant mutation.
    """
    if not isinstance(ref, ArtifactReference):
        raise TypeError(f"ref must be ArtifactReference, got {type(ref).__name__}")
    if not isinstance(current_sandbox, WorktreeSandboxBoundary):
        raise ArtifactReferenceError("validation requires current WorktreeSandboxBoundary")
    if ref.project_id != current_sandbox.project_id:
        raise ArtifactTamperError(f"cross-project artifact ref denied: {ref.project_id!r} != {current_sandbox.project_id!r}")
    if ref.worktree_id != current_sandbox.worktree_id:
        raise ArtifactTamperError(f"cross-worktree artifact ref denied: {ref.worktree_id!r} != {current_sandbox.worktree_id!r}")
    if not _DIGEST_HEX_RE.fullmatch(ref.digest):
        raise ArtifactTamperError(f"artifact digest malformed: {ref.digest!r}")
    if expected_digest is not None:
        ed = expected_digest.strip().lower()
        if not _DIGEST_HEX_RE.fullmatch(ed):
            raise ArtifactReferenceError("expected_digest malformed")
        if ref.digest != ed:
            raise ArtifactTamperError(f"artifact digest mismatch: {ref.digest!r} != {ed!r}")
    # Also re-read file and verify digest if file exists (integrity)
    try:
        fresh = resolve_worktree_resource(current_sandbox, ref.logical_ref)
        if fresh.exists and fresh.kind == "file":
            canonical_path = Path(fresh.canonical_path)
            if not canonical_path.is_symlink() and canonical_path.is_file():
                raw = canonical_path.read_bytes()
                computed = hashlib.sha256(raw).hexdigest()
                if computed != ref.digest:
                    raise ArtifactTamperError(f"artifact content digest mismatch (tampered file): computed {computed!r} != ref {ref.digest!r}")
                if len(raw) != ref.byte_length:
                    raise ArtifactTamperError(f"artifact byte_length mismatch: {len(raw)} != {ref.byte_length}")
    except ArtifactTamperError:
        raise
    except Exception:
        # If file not exists or unreadable, we don't fail validation solely on that (ref may be for produced but not yet persisted)
        pass
    return ref

# ---------------------------------------------------------------------------
# Provider — implements existing ToolProvider, consumes trusted mutation authority
# ---------------------------------------------------------------------------


class BoundedWorkspaceMutationProvider:
    """Bounded workspace mutation Tool provider handling workspace.write.

    Implements existing ToolProvider; does NOT create new ontology.
    Consumes trusted WorkspaceMutationAuthority.
    """

    WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER: bool = False

    def __init__(self, authority: WorkspaceMutationAuthority) -> None:
        if not isinstance(authority, WorkspaceMutationAuthority):
            raise WorkspaceMutationAuthorityError(
                f"authority must be WorkspaceMutationAuthority (trusted mutation evidence), got {type(authority).__name__}"
            )
        sb = authority.sandbox
        root = Path(sb.worktree_root)
        try:
            if root.is_symlink():
                raise WorkspaceMutationAuthorityError(f"sandbox root is symlink: {root}")
            if not root.exists() or not root.is_dir():
                raise WorkspaceMutationAuthorityError(f"sandbox root missing or not dir: {root}")
        except OSError as exc:
            raise WorkspaceMutationAuthorityError(f"sandbox root inaccessible: {exc}") from exc
        try:
            self._root = Path(sb.worktree_root).resolve(strict=True)
        except OSError as exc:
            raise WorkspaceMutationAuthorityError(f"sandbox root resolve failed: {exc}") from exc
        self._authority = authority
        self._sandbox = sb
        self._handoff = authority.handoff
        self._policies = authority.applicable_policies
        self._operation = authority.operation

    @property
    def authority(self) -> WorkspaceMutationAuthority:
        return self._authority

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self._sandbox

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        try:
            if self._authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_MISSING", "message": "missing trusted mutation authority"})
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure({"code": "OPERATION_MISMATCH", "message": f"request operation {request.operation.name!r} != authority {self._authority.operation.name!r}"})
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
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
            if request.operation.name == "workspace.write":
                return self._handle_write(request)
            else:
                return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported: {request.operation.name!r}"})
        except Exception as exc:
            if isinstance(exc, (WorkspaceMutationError, WorkspaceMutationAuthorityError, ArtifactReferenceError)):
                return ToolResponse.failure({"code": "WORKSPACE_ERROR", "message": str(exc)})
            return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})

    def _handle_write(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        path_val = inputs.get("path")
        content_val = inputs.get("content")
        mode_val = inputs.get("mode")
        if path_val is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "path is required"})
        if content_val is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "content is required"})
        if mode_val is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "mode is required"})
        if not isinstance(path_val, str) or type(path_val) is not str:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "path must be string"})
        raw_path: str = path_val
        try:
            mode = _validate_mode(mode_val)
        except WorkspaceMutationError as exc:
            return ToolResponse.failure({"code": "INVALID_MODE", "message": str(exc)})
        try:
            content = _validate_content(content_val)
        except WorkspaceMutationError as exc:
            return ToolResponse.failure({"code": "OVERSIZED_PAYLOAD", "message": str(exc)})
        # Reject absolute, traversal via resolver
        try:
            evidence = resolve_worktree_resource(self._sandbox, raw_path)
        except Exception as exc:
            # Distinguish escape vs invalid
            msg = str(exc)
            if "traversal" in msg.lower() or "absolute" in msg.lower() or "empty" in msg.lower():
                return ToolResponse.failure({"code": "PATH_ESCAPE", "message": f"path rejected: {exc}"})
            if "symlink" in msg.lower():
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"path rejected: {exc}"})
            return ToolResponse.failure({"code": "INVALID_PATH", "message": f"path rejected: {exc}"})
        if evidence.project_id != self._sandbox.project_id:
            return ToolResponse.failure({"code": "CROSS_PROJECT_WRITE_FAIL_CLOSED", "message": "project mismatch"})
        if evidence.worktree_id != self._sandbox.worktree_id:
            return ToolResponse.failure({"code": "CROSS_WORKTREE_WRITE_FAIL_CLOSED", "message": "worktree mismatch"})
        # Revalidation immediately before mutation — TOCTOU truthful
        try:
            fresh_evidence = resolve_worktree_resource(self._sandbox, raw_path)
        except Exception as exc:
            return ToolResponse.failure({"code": "INVALID_PATH", "message": f"revalidation failed: {exc}"})
        if fresh_evidence.canonical_path != evidence.canonical_path:
            return ToolResponse.failure({"code": "TOCTOU_REVALIDATION_MISMATCH", "message": "resource path changed between revalidations"})
        canonical_path = Path(fresh_evidence.canonical_path)
        # Symlink escape fail-closed at use time
        try:
            _check_symlink_escape(canonical_path)
            root_canonical = self._root
            # Parent directory symlink escape check
            parent = canonical_path.parent
            # Walk parent components for symlink
            try:
                rel = parent.relative_to(self._root) if parent != self._root else Path(".")
                cur = self._root
                if parent != self._root:
                    for part in rel.parts:
                        if part == ".":
                            continue
                        cur = cur / part
                        if cur.is_symlink():
                            return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"parent directory symlink escape at {cur}"})
                        # Also check canonical containment for parent
                        cur.resolve(strict=False).relative_to(root_canonical)
                # Final canonical containment for target's parent
                parent.resolve(strict=False).relative_to(root_canonical)
                canonical_path.resolve(strict=False).relative_to(root_canonical)
            except ValueError:
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "resource escapes worktree via symlink (post-resolve)"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"post-resolve check failed: {exc}"})
        except WorkspaceMutationError as exc:
            return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": str(exc)})
        # Mode checks against current existence
        exists = canonical_path.exists()
        is_file = canonical_path.is_file() if exists else False
        is_dir = canonical_path.is_dir() if exists else False
        if is_dir:
            return ToolResponse.failure({"code": "INVALID_PATH", "message": "target is a directory"})
        if exists and canonical_path.is_symlink():
            return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "target is symlink"})
        if mode == "create_only" and exists:
            return ToolResponse.failure({"code": "INVALID_MODE", "message": "create_only but file already exists"})
        if mode == "replace_existing" and not exists:
            return ToolResponse.failure({"code": "INVALID_MODE", "message": "replace_existing but file does not exist"})
        # Ensure parent directory exists (create if needed) with symlink checks
        parent_dir = canonical_path.parent
        if not parent_dir.exists():
            try:
                # Create parents deterministically, checking each component not symlink
                # Use mkdir(parents=True) but we already checked symlink for existing components
                # For new components, they will be created as directories
                # Validate that logical parent path is within bound before creation
                # Re-use logical parent validation via resolver? Check parent logical dir
                logical_parent = str(Path(raw_path).parent) if str(Path(raw_path).parent) != "." else ""
                if logical_parent and logical_parent != "":
                    # validate parent logical ref is within bound (no traversal)
                    # We attempt to resolve parent as resource; if missing, it's okay but must be valid ref
                    try:
                        parent_evidence = resolve_worktree_resource(self._sandbox, logical_parent)
                        # If parent exists as file, fail
                        if parent_evidence.exists and parent_evidence.kind == "file":
                            return ToolResponse.failure({"code": "INVALID_PATH", "message": "parent is a file"})
                    except Exception:
                        # If parent logical ref invalid, fail
                        return ToolResponse.failure({"code": "INVALID_PATH", "message": "invalid parent path"})
                parent_dir.mkdir(parents=True, exist_ok=True)
                # Re-check no symlink introduced
                if parent_dir.is_symlink():
                    return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "parent directory is symlink after creation"})
            except OSError as exc:
                return ToolResponse.failure({"code": "PHYSICAL_WRITE_FAILURE", "message": f"parent mkdir failed: {exc}"})
        # Atomic write: temporary sibling → flush → atomic replace
        content_bytes = content.encode("utf-8")
        digest = hashlib.sha256(content_bytes).hexdigest()
        tmp_path: Path | None = None
        try:
            # Create temp file in same directory
            fd, tmp_name = tempfile.mkstemp(dir=str(parent_dir), prefix=".tmp-write-")
            tmp_path = Path(tmp_name)
            try:
                os.write(fd, content_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            # Ensure tmp file not symlink and within root
            try:
                if tmp_path.is_symlink():
                    tmp_path.unlink(missing_ok=True)
                    return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": "temp file is symlink"})
                tmp_path.resolve(strict=True).relative_to(root_canonical)
            except (ValueError, OSError) as exc:
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                return ToolResponse.failure({"code": "SYMLINK_ESCAPE", "message": f"temp file containment failed: {exc}"})
            # Atomic replace
            try:
                os.replace(str(tmp_path), str(canonical_path))
            except OSError as exc:
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                return ToolResponse.failure({"code": "PHYSICAL_WRITE_FAILURE", "message": f"atomic replace failed: {exc}"})
            # Verify written file digest and containment post-write
            try:
                written_bytes = canonical_path.read_bytes()
                if hashlib.sha256(written_bytes).hexdigest() != digest:
                    return ToolResponse.failure({"code": "PHYSICAL_WRITE_FAILURE", "message": "written digest mismatch"})
                canonical_path.resolve(strict=True).relative_to(root_canonical)
            except (OSError, ValueError) as exc:
                return ToolResponse.failure({"code": "PHYSICAL_WRITE_FAILURE", "message": f"post-write verification failed: {exc}"})
        except OSError as exc:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return ToolResponse.failure({"code": "PHYSICAL_WRITE_FAILURE", "message": f"write failed: {exc}"})
        # Success payload is bounded metadata, not full content echo
        payload = {
            "path": raw_path,
            "byte_length": len(content_bytes),
            "digest": digest,
            "project_id": self._sandbox.project_id,
            "worktree_id": self._sandbox.worktree_id,
            "mode": mode,
            "canonical_path": str(canonical_path),
        }
        # Output bounded check
        try:
            payload_json_size = len(canonical_json(payload).encode("utf-8"))
        except Exception:
            payload_json_size = len(str(payload).encode("utf-8"))
        if payload_json_size > MAX_TOTAL_OUTPUT_BYTES:
            return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": f"output size {payload_json_size} exceeds max"})
        return ToolResponse.success(payload)


__all__ = [
    "WORKSPACE_WRITE_DESCRIPTOR",
    "WorkspaceMutationAuthority",
    "create_workspace_mutation_authority",
    "BoundedWorkspaceMutationProvider",
    "ArtifactReference",
    "create_artifact_reference",
    "validate_artifact_reference",
    "WorkspaceMutationAuthorityError",
    "WorkspaceMutationError",
    "ArtifactReferenceError",
    "ArtifactTamperError",
    "MAX_WRITE_BYTES",
    "WRITE_MODES",
    "MAX_PATH_LENGTH",
    "READ_AUTHORITY_IS_WRITE_AUTHORITY",
    "RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY",
    "TOOL_EXPOSURE_IS_MUTATION_AUTHORITY",
    "WORK_ROLE_IS_MUTATION_AUTHORITY",
    "AGENTS_POLICY_IS_MUTATION_AUTHORITY",
    "OPERATION_DESCRIPTOR_IS_MUTATION_AUTHORITY_DECISION",
    "MUTATION_AUTHORITY_REQUIRED",
    "MUTATION_AUTHORITY_SEAM_INSUFFICIENT",
    "MUTATION_AUTHORITY_SEAM_USED",
    "WORKSPACE_WRITE_BOUNDED",
    "WORKSPACE_WRITE_PROJECT_WORKTREE_BOUND",
    "RAW_ABSOLUTE_WRITE_PATH_ALLOWED",
    "CWD_IS_MUTATION_AUTHORITY",
    "PATH_TRAVERSAL_WRITE_FAIL_CLOSED",
    "CROSS_PROJECT_WRITE_FAIL_CLOSED",
    "CROSS_WORKTREE_WRITE_FAIL_CLOSED",
    "WRITE_TARGET_REVALIDATED_AT_USE",
    "STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY",
    "WRITE_SYMLINK_ESCAPE_FAIL_CLOSED",
    "DIRECTORY_SYMLINK_ESCAPE_FAIL_CLOSED",
    "WRITE_TOCTOU_BOUNDARY_EXPLICIT",
    "WRITE_TOCTOU_ELIMINATED",
    "TOCTOU_BOUNDARY_TRUTHFUL",
    "ATOMIC_REPLACE_SUPPORTED",
    "MUTATION_RESULT_IS_AUTHORITY",
    "MUTATION_SIDE_EFFECT_AND_ARTIFACT_REFERENCE_SEPARATE",
    "ARTIFACT_REFERENCE_IS_AUTHORITY",
    "ARTIFACT_REF_DIGEST_BOUND",
    "ARTIFACT_REF_PROJECT_WORKTREE_SCOPED",
    "ARTIFACT_REF_DIGEST_IS_AUTHORITY",
    "ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY",
    "NEW_PERSISTENT_ARTIFACT_STORE_CREATED",
    "ARTIFACT_IMPLEMENTATION_MODE",
    "REFERENCE_CONTRACT_ONLY",
    "WRITE_INPUT_BOUNDED",
    "WRITE_RESULT_OUTPUT_BOUNDED",
    "FAILED_MUTATION_REPORTED_AS_SUCCESS",
    "EXISTING_TOOL_CONTRACT_REUSED",
    "M1_SANDBOX_REUSED",
    "M1_RESOURCE_RESOLVER_REUSED",
    "M2_RESULT_GOVERNANCE_REUSED",
    "TEST_EXECUTION_IMPLEMENTED_IN_W1",
    "GIT_OPERATION_IMPLEMENTED_IN_W1",
    "RESTRICTED_SHELL_IMPLEMENTED_IN_W1",
]
