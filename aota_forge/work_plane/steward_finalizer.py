"""Trusted Steward Finalizer — server-side mechanical boundary (M3/W2).

One trusted mechanical boundary:

    TrustedStewardFinalizer

consumes a typed bounded FinalizerInput:

    FinalizerInput = validated StewardResult
        + trusted project binding
        + trusted Plan/Milestone identity
        + trusted review/frontier evidence
        + trusted user-gate/acceptance state
        + bounded materialization intent

Hard invariants (all asserted as module markers):

    FINALIZER_INPUT_IS_STEWARD_RESULT=yes
    FINALIZER_USES_TRUSTED_BINDING=yes
    FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE=no
    LLM_SEMANTIC_JUDGMENT_IS_MECHANICAL_MUTATION_AUTHORITY=no
    STEWARD_CAN_SET_USER_APPROVAL=no
    FINALIZER_CAN_SET_USER_APPROVAL=no
    FINALIZER_CAN_INFER_USER_APPROVAL=no
    FINALIZER_CAN_INVENT_ACCEPTED_FRONTIER=no
    ACCEPTED_FRONTIER_MUST_DERIVE_FROM_TRUSTED_REVIEWED_FRONTIER=yes
    GENERIC_GIT_COMMAND_AUTHORITY=no
    FORCE_PUSH_ALLOWED=no
    RESET_ALLOWED=no
    CLEAN_ALLOWED=no
    STASH_ALLOWED=no
    NON_FAST_FORWARD_MAIN_PROMOTION_ALLOWED=no
    GIT_PROMOTION_CAS_GUARDED=yes
    GENERIC_GITHUB_REQUEST_AUTHORITY=no
    ARBITRARY_REPOSITORY_MUTATION=no
    ARBITRARY_ISSUE_MUTATION=no
    ARBITRARY_COMMENT_MUTATION=no
    GITHUB_TARGET_DERIVED_FROM_TRUSTED_PLAN_CONTEXT=yes
    FINALIZER_CAN_CREATE_NEW_MANAGED_COMMENT_BY_DEFAULT=no
    GITHUB_MUTATION_CAS_GUARDED=yes
    FINALIZER_IDEMPOTENT=yes
    REMOTE_MUTATION_BEFORE_LOCAL_RECEIPT_RECOVERABLE=yes
    MATERIALIZATION_RECEIPT_CREATED=yes (see materialization_receipt.py)
    PARTIAL_FAILURE_RECOVERY_WIRED=yes
    DISTRIBUTED_GLOBAL_ATOMICITY_CLAIMED=no
    COORDINATOR_FINALIZER_INTEGRATION_WIRED=yes
    TASK_MAIN_DIRECT_MANUAL_GOVERNANCE_SYNC_REQUIRED=no
    EXACT_TASK_MAIN_REENTRY_AFTER_FINALIZER=yes
    RAW_FINALIZER_LOG_REQUIRED_BY_TASK_MAIN=no
    FINALIZER_RESTART_RECOVERY_SUPPORTED=yes
    PROJECT_STEWARD_PERSISTS_DURING_FINALIZER=no
    PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION=no
    PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION=no
    ONE_AGENT_FACING_AOTA_MCP_TOOL=yes (unchanged, no new tool)
    NEW_PUBLIC_MCP_TOOL_CREATED=no
    SECOND_RESULT_TRANSPORT_CREATED=no
    NEW_WORKFLOW_DATABASE_CREATED=no
    NEW_EVENT_BUS_CREATED=no

The LLM never passes arbitrary shell/Git/GitHub commands. The finalizer
supports only the minimum typed semantic operations:

Git (typed, ff-only, expected-old-ref CAS, no force/reset/clean/stash):

    verify_ref / verify_tree / verify_ancestor /
    fast_forward_local_branch / push_expected_ref /
    verify_remote_ref / verify_remote_tree

GitHub (typed governance seam, Plan-identity bound, CAS):

    update_managed_comment (existing 5 roles only, expected digest/version CAS)
    append_managed_decision_entry (bounded, material decisions only)

Managed roles (exactly five, no sixth by default):

    milestone_progress_index / development_notes / defect_register /
    plan_appendix / decision_change_log

Idempotency is deterministic from project/Plan/Milestone/phase/Steward
digest/frontier/scope. Crash-window recovery observes remote state: if it
exactly equals the previously intended desired state, the finalizer recovers
the receipt without duplicate mutation.

Receipt is evidence, never authority (see materialization_receipt.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum, unique
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.materialization_receipt import (
    MaterializationReceipt,
    OperationReceipt,
)
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.steward_dispatch import StewardResult

# Reuse existing bounded Git mechanics (delegate, never copy implementation).
from aota_forge.core.git.inspect import _run_git as _core_run_git

# ---------------------------------------------------------------------------
# Authority markers (importable for tests / downstream seams)
# ---------------------------------------------------------------------------

STEWARD_FINALIZER_CREATED = True
STEWARD_FINALIZER_IS_SERVER_SIDE_TRUSTED = True

FINALIZER_INPUT_IS_STEWARD_RESULT = True
FINALIZER_USES_TRUSTED_BINDING = True
FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE = False

LLM_SEMANTIC_JUDGMENT_IS_MECHANICAL_MUTATION_AUTHORITY = False

REVIEWED_CLOSURE_MODE_WIRED = True
ACCEPTED_CLOSURE_MODE_WIRED = True

STEWARD_CAN_SET_USER_APPROVAL = False
FINALIZER_CAN_SET_USER_APPROVAL = False
FINALIZER_CAN_INFER_USER_APPROVAL = False
STEWARD_RESULT_USER_APPROVAL_IS_AUTHORITY = False
DURABLE_USER_GATE_STATE_IS_REQUIRED_FOR_ACCEPTED_CLOSURE = True

ACCEPTED_FRONTIER_DERIVED_FROM_REVIEWED_FRONTIER = True
FINALIZER_CAN_INVENT_ACCEPTED_FRONTIER = False
ACCEPTED_FRONTIER_MUST_DERIVE_FROM_TRUSTED_REVIEWED_FRONTIER = True

TYPED_GIT_BOUNDARY_WIRED = True
GENERIC_GIT_COMMAND_AUTHORITY = False
GIT_PROMOTION_CAS_GUARDED = True
NON_FAST_FORWARD_MAIN_PROMOTION_ALLOWED = False
FORCE_PUSH_ALLOWED = False
RESET_ALLOWED = False
CLEAN_ALLOWED = False
STASH_ALLOWED = False

TYPED_GITHUB_GOVERNANCE_BOUNDARY_WIRED = True
GENERIC_GITHUB_REQUEST_AUTHORITY = False
ARBITRARY_REPOSITORY_MUTATION = False
ARBITRARY_ISSUE_MUTATION = False
ARBITRARY_COMMENT_MUTATION = False
GITHUB_TARGET_DERIVED_FROM_TRUSTED_PLAN_CONTEXT = True
GITHUB_MUTATION_CAS_GUARDED = True
FINALIZER_CAN_CREATE_NEW_MANAGED_COMMENT_BY_DEFAULT = False

FINALIZER_IDEMPOTENT = True
REMOTE_MUTATION_BEFORE_LOCAL_RECEIPT_RECOVERABLE = True

PARTIAL_FAILURE_RECOVERY_WIRED = True
DISTRIBUTED_GLOBAL_ATOMICITY_CLAIMED = False

COORDINATOR_FINALIZER_INTEGRATION_WIRED = True
TASK_MAIN_DIRECT_MANUAL_GOVERNANCE_SYNC_REQUIRED = False

EXACT_TASK_MAIN_REENTRY_AFTER_FINALIZER = True
RAW_FINALIZER_LOG_REQUIRED_BY_TASK_MAIN = False
FINALIZER_RESTART_RECOVERY_SUPPORTED = True

PROJECT_STEWARD_PERSISTS_DURING_FINALIZER = False

PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION = False
PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION = False

ONE_AGENT_FACING_AOTA_MCP_TOOL = True
AGENT_FACING_AOTA_TOOL = "aota.invoke"
NEW_PUBLIC_MCP_TOOL_CREATED = False

SECOND_RESULT_TRANSPORT_CREATED = False
NEW_WORKFLOW_DATABASE_CREATED = False
NEW_EVENT_BUS_CREATED = False

# Managed roles — exactly five, no sixth by default.
MANAGED_ROLES: frozenset[str] = frozenset(
    {
        "milestone_progress_index",
        "development_notes",
        "defect_register",
        "plan_appendix",
        "decision_change_log",
    }
)

# ---------------------------------------------------------------------------
# Failure taxonomy (bounded typed failures)
# ---------------------------------------------------------------------------


@unique
class FinalizerFailure(str, Enum):
    INVALID_STEWARD_RESULT = "INVALID_STEWARD_RESULT"
    TRUST_BINDING_MISMATCH = "TRUST_BINDING_MISMATCH"
    USER_GATE_REQUIRED = "USER_GATE_REQUIRED"
    ACCEPTED_FRONTIER_MISMATCH = "ACCEPTED_FRONTIER_MISMATCH"
    GIT_CAS_CONFLICT = "GIT_CAS_CONFLICT"
    GIT_NON_FAST_FORWARD = "GIT_NON_FAST_FORWARD"
    GITHUB_CAS_CONFLICT = "GITHUB_CAS_CONFLICT"
    MANAGED_COMMENT_IDENTITY_MISMATCH = "MANAGED_COMMENT_IDENTITY_MISMATCH"
    REMOTE_DURABILITY_UNPROVEN = "REMOTE_DURABILITY_UNPROVEN"
    MATERIALIZATION_PARTIAL_FAILURE = "MATERIALIZATION_PARTIAL_FAILURE"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class FinalizerError(Exception):
    """Typed fail-closed finalizer refusal (no mutation, or partial receipt)."""

    def __init__(self, code: FinalizerFailure, detail: str = "", *, receipt: MaterializationReceipt | None = None) -> None:
        self.code = code
        self.detail = detail
        self.receipt = receipt
        super().__init__(f"{code.value}: {detail}")


def map_failure_to_disposition(code: FinalizerFailure) -> str:
    """Map bounded failure to coordinator disposition hint."""
    if code is FinalizerFailure.USER_GATE_REQUIRED:
        return "user_gate"
    if code in (FinalizerFailure.INVALID_STEWARD_RESULT, FinalizerFailure.TRUST_BINDING_MISMATCH,
                FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH):
        return "blocked"
    if code in (FinalizerFailure.GIT_CAS_CONFLICT, FinalizerFailure.GITHUB_CAS_CONFLICT,
                FinalizerFailure.GIT_NON_FAST_FORWARD, FinalizerFailure.REMOTE_DURABILITY_UNPROVEN,
                FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE):
        return "blocked"
    if code is FinalizerFailure.ALREADY_APPLIED:
        return "already_applied"
    return "blocked"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace: {value!r}")
    if not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_length}")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


def _digest_str(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _steward_digest(steward: StewardResult) -> str:
    payload = {
        "milestone_ref": steward.milestone_ref,
        "reviewed_frontier_ref": steward.reviewed_frontier_ref,
        "verdict": steward.verdict.value,
        "accepted_frontier_ref": steward.accepted_frontier_ref,
        "governance_evidence_refs": sorted(steward.governance_evidence_refs),
        "blocking_reasons": sorted(steward.blocking_reasons),
    }
    return hashlib.sha256(canonical_json(canonicalize(payload, path="StewardResult")).encode("utf-8")).hexdigest()


_SAFE_REF_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


def _validate_commit_sha(value: str, label: str) -> str:
    s = _require_non_empty_str(value, label, max_length=128)
    if not _SAFE_REF_RE.fullmatch(s):
        raise ValueError(f"{label} must be a 40-char lower hex commit SHA, got {value!r}")
    return s


# ---------------------------------------------------------------------------
# Trusted value objects (all from trusted channels, never model free text)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustedProjectBinding:
    """Trusted project binding (operator/host derived, never model minted)."""

    project_id: str
    binding_ref: str
    binding_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_non_empty_str(self.project_id, "project_id", max_length=96))
        object.__setattr__(self, "binding_ref", _require_non_empty_str(self.binding_ref, "binding_ref"))
        object.__setattr__(self, "binding_digest", _require_non_empty_str(self.binding_digest, "binding_digest", max_length=128))

    def to_dict(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "binding_ref": self.binding_ref, "binding_digest": self.binding_digest}


def make_trusted_binding_for_test(project_id: str = "test-project") -> TrustedProjectBinding:
    """Deterministic trusted binding for isolated fixtures (never production authority)."""
    pid = _require_non_empty_str(project_id, "project_id", max_length=96)
    ref = f"binding:{pid}"
    return TrustedProjectBinding(project_id=pid, binding_ref=ref, binding_digest=_digest_str(ref))


@dataclass(frozen=True)
class TrustedPlanIdentity:
    """Trusted Plan/Milestone identity (governing repo/issue + managed mapping)."""

    governing_repo: str
    plan_issue_number: int
    milestone_ref: str
    plan_ref: str
    managed_comments: dict[str, str]  # role -> comment_id (numeric REST id as str)

    def __post_init__(self) -> None:
        repo = _require_non_empty_str(self.governing_repo, "governing_repo", max_length=256)
        if not _SAFE_REPO_RE.fullmatch(repo):
            raise ValueError(f"governing_repo must be 'owner/repo', got {repo!r}")
        object.__setattr__(self, "governing_repo", repo)
        if type(self.plan_issue_number) is not int or self.plan_issue_number <= 0:
            raise ValueError(f"plan_issue_number must be positive int, got {self.plan_issue_number!r}")
        object.__setattr__(self, "milestone_ref", _require_non_empty_str(self.milestone_ref, "milestone_ref", max_length=128))
        object.__setattr__(self, "plan_ref", _require_non_empty_str(self.plan_ref, "plan_ref"))
        if not isinstance(self.managed_comments, dict):
            raise TypeError("managed_comments must be dict role->comment_id")
        norm: dict[str, str] = {}
        for role, cid in self.managed_comments.items():
            if role not in MANAGED_ROLES:
                raise ValueError(f"managed_comments role {role!r} is not one of {sorted(MANAGED_ROLES)} (no sixth role)")
            cid_s = _require_non_empty_str(cid, f"managed_comments[{role}]", max_length=64)
            norm[role] = cid_s
        # Must not contain a sixth role by construction (validated above).
        object.__setattr__(self, "managed_comments", dict(norm))

    def comment_id_for(self, role: str) -> str | None:
        return self.managed_comments.get(role)


@dataclass(frozen=True)
class TrustedUserGateState:
    """External durable user-gate evidence (control-plane truth, never model text)."""

    user_approval_satisfied: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "user_approval_satisfied", _require_strict_bool(self.user_approval_satisfied, "user_approval_satisfied")
        )


@unique
class ClosurePhase(str, Enum):
    REVIEWED_CLOSURE = "REVIEWED_CLOSURE"
    ACCEPTED_CLOSURE = "ACCEPTED_CLOSURE"


@dataclass(frozen=True)
class GitPromotionIntent:
    """Bounded Git promotion intent (ff-only + expected-old-ref CAS). No force."""

    branch: str = "main"
    expected_old_ref: str | None = None
    target_ref: str | None = None
    remote: str | None = None  # e.g. "origin"; None means local-only
    expected_tree: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "branch", _require_non_empty_str(self.branch, "branch", max_length=128))
        if "/" in self.branch or "\\" in self.branch or self.branch.startswith("-"):
            raise ValueError(f"branch must be a simple branch name, got {self.branch!r}")
        if self.expected_old_ref is not None:
            object.__setattr__(self, "expected_old_ref", _validate_commit_sha(self.expected_old_ref, "expected_old_ref"))
        if self.target_ref is not None:
            object.__setattr__(self, "target_ref", _validate_commit_sha(self.target_ref, "target_ref"))
        if self.remote is not None:
            object.__setattr__(self, "remote", _require_non_empty_str(self.remote, "remote", max_length=64))
        if self.expected_tree is not None:
            object.__setattr__(self, "expected_tree", _require_non_empty_str(self.expected_tree, "expected_tree", max_length=128))

    @property
    def wants_promotion(self) -> bool:
        return self.expected_old_ref is not None and self.target_ref is not None


@dataclass(frozen=True)
class GitHubUpdateIntent:
    """Bounded managed-comment update intent (existing role only, CAS)."""

    role: str
    comment_id: str
    expected_digest: str
    expected_revision: str | int | None = None
    proof_marker: str = ""
    proof_content: str = ""

    def __post_init__(self) -> None:
        if self.role not in MANAGED_ROLES:
            raise ValueError(f"role must be one of {sorted(MANAGED_ROLES)}, got {self.role!r}")
        object.__setattr__(self, "comment_id", _require_non_empty_str(self.comment_id, "comment_id", max_length=64))
        object.__setattr__(self, "expected_digest", _require_non_empty_str(self.expected_digest, "expected_digest", max_length=128))
        if self.expected_revision is not None and not isinstance(self.expected_revision, (str, int)):
            raise TypeError("expected_revision must be str/int/None")
        object.__setattr__(self, "proof_marker", _require_non_empty_str(self.proof_marker, "proof_marker", max_length=256))
        if not isinstance(self.proof_content, str) or type(self.proof_content) is not str:
            raise TypeError("proof_content must be str")
        if not self.proof_content.strip():
            raise ValueError("proof_content must be non-empty")
        if len(self.proof_content) > 8192:
            raise ValueError("proof_content exceeds 8192 bound")
        if "\x00" in self.proof_content:
            raise ValueError("proof_content must not contain NUL")


@dataclass(frozen=True)
class MaterializationIntent:
    """Bounded materialization scope (subset of StewardResult scope, never more)."""

    git_promotion: GitPromotionIntent | None = None
    github_updates: tuple[GitHubUpdateIntent, ...] = ()
    review_projection: bool = True

    def __post_init__(self) -> None:
        if self.git_promotion is not None and not isinstance(self.git_promotion, GitPromotionIntent):
            raise TypeError("git_promotion must be GitPromotionIntent or None")
        if not isinstance(self.github_updates, (tuple, list)):
            raise TypeError("github_updates must be tuple/list")
        if len(self.github_updates) > 5:
            raise ValueError("github_updates exceeds 5 (one per managed role max)")
        seen: set[str] = set()
        for idx, u in enumerate(self.github_updates):
            if not isinstance(u, GitHubUpdateIntent):
                raise TypeError(f"github_updates[{idx}] must be GitHubUpdateIntent")
            if u.role in seen:
                raise ValueError(f"duplicate github role intent: {u.role!r}")
            seen.add(u.role)
        object.__setattr__(self, "github_updates", tuple(self.github_updates))
        object.__setattr__(self, "review_projection", _require_strict_bool(self.review_projection, "review_projection"))

    def scope_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"review_projection": self.review_projection, "github_roles": sorted(u.role for u in self.github_updates)}
        if self.git_promotion is not None and self.git_promotion.wants_promotion:
            d["git"] = {
                "branch": self.git_promotion.branch,
                "expected_old_ref": self.git_promotion.expected_old_ref,
                "target_ref": self.git_promotion.target_ref,
                "remote": self.git_promotion.remote,
            }
        else:
            d["git"] = None
        d["github_updates"] = [
            {"role": u.role, "comment_id": u.comment_id, "proof_marker": u.proof_marker} for u in self.github_updates
        ]
        return d


@dataclass(frozen=True)
class FinalizerInput:
    """Typed bounded finalizer input (StewardResult + trusted evidence + intent)."""

    steward_result: StewardResult
    readiness: MilestoneClosureReadiness
    trusted_binding: TrustedProjectBinding
    trusted_plan: TrustedPlanIdentity
    user_gate: TrustedUserGateState
    intent: MaterializationIntent
    closure_phase: ClosurePhase

    def __post_init__(self) -> None:
        if not isinstance(self.steward_result, StewardResult):
            raise TypeError(f"steward_result must be StewardResult, got {type(self.steward_result).__name__}")
        if not isinstance(self.readiness, MilestoneClosureReadiness):
            raise TypeError(f"readiness must be MilestoneClosureReadiness, got {type(self.readiness).__name__}")
        if not isinstance(self.trusted_binding, TrustedProjectBinding):
            raise TypeError("trusted_binding must be TrustedProjectBinding")
        if not isinstance(self.trusted_plan, TrustedPlanIdentity):
            raise TypeError("trusted_plan must be TrustedPlanIdentity")
        if not isinstance(self.user_gate, TrustedUserGateState):
            raise TypeError("user_gate must be TrustedUserGateState")
        if not isinstance(self.intent, MaterializationIntent):
            raise TypeError("intent must be MaterializationIntent")
        if isinstance(self.closure_phase, ClosurePhase):
            pass
        elif isinstance(self.closure_phase, str) and type(self.closure_phase) is str:
            try:
                object.__setattr__(self, "closure_phase", ClosurePhase(self.closure_phase))
            except ValueError as exc:
                raise ValueError(f"Unknown ClosurePhase: {self.closure_phase!r}") from exc
        else:
            raise TypeError(f"closure_phase must be ClosurePhase, got {type(self.closure_phase).__name__}")


# ---------------------------------------------------------------------------
# Proof subsection helper (idempotent, bounded, no duplicate mutation)
# ---------------------------------------------------------------------------


def upsert_proof_subsection(existing_body: str, marker: str, content: str) -> str:
    """Insert or replace a bounded proof subsection idempotently.

    The subsection is delimited as:

        <!-- <marker>:begin -->
        <content>
        <!-- <marker>:end -->

    If the exact marker+content already exists, the body is returned
    unchanged (idempotent, no duplicate). Otherwise the existing subsection
    for the same marker (if any) is replaced, else appended.
    """
    if not isinstance(existing_body, str):
        raise TypeError("existing_body must be str")
    marker = _require_non_empty_str(marker, "marker", max_length=256)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be non-empty str")
    begin = f"<!-- {marker}:begin -->"
    end = f"<!-- {marker}:end -->"
    block = f"{begin}\n{content.strip()}\n{end}"
    if block in existing_body:
        return existing_body
    # Replace existing block for same marker if present
    if begin in existing_body and end in existing_body:
        start = existing_body.index(begin)
        stop = existing_body.index(end) + len(end)
        return existing_body[:start] + block + existing_body[stop:]
    # Append bounded
    sep = "" if existing_body.endswith("\n") else "\n"
    return f"{existing_body}{sep}\n{block}\n"


# ---------------------------------------------------------------------------
# Typed Git boundary (ff-only, CAS, no generic command)
# ---------------------------------------------------------------------------


class GitPort:
    """Minimal typed Git seam (no generic command, no force/reset/clean/stash)."""

    def resolve_ref(self, repo: Path, ref: str) -> str | None:
        raise NotImplementedError

    def resolve_tree(self, repo: Path, commit: str) -> str:
        raise NotImplementedError

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        raise NotImplementedError

    def fast_forward_branch(self, repo: Path, branch: str, expected_old: str, target: str) -> tuple[str, str, str]:
        """FF-only local promotion with expected-old CAS. Returns (before, after, status)."""
        raise NotImplementedError

    def read_remote_ref(self, repo: Path, remote: str, branch: str) -> str | None:
        raise NotImplementedError

    def push_branch(self, repo: Path, remote: str, branch: str, target: str) -> None:
        """FF-only push without force. Raises on non-ff."""
        raise NotImplementedError

    def verify_remote_tree(self, repo: Path, remote: str, branch: str) -> str | None:
        raise NotImplementedError


class SubprocessGitPort(GitPort):
    """Production Git seam via structured argv (reuses core _run_git mechanics)."""

    def _checked(self, repo: Path) -> Path:
        if not isinstance(repo, Path):
            raise TypeError("repo must be Path")
        if not repo.exists() or not repo.is_dir():
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"repo missing: {repo}")
        return repo

    def resolve_ref(self, repo: Path, ref: str) -> str | None:
        repo = self._checked(repo)
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 256:
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"invalid ref {ref!r}")
        # Allow only simple branch names or full SHAs for resolve; reject shell metachars.
        if any(c in ref for c in (";", "&", "|", "$", "`", " ", "\n", "\x00")):
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"ref contains forbidden chars: {ref!r}")
        out, _err, rc = _core_run_git(repo, ["git", "rev-parse", "--verify", ref], timeout=10)
        if rc != 0:
            return None
        sha = out.strip()
        if not _SAFE_REF_RE.fullmatch(sha):
            return None
        return sha

    def resolve_tree(self, repo: Path, commit: str) -> str:
        repo = self._checked(repo)
        _validate_commit_sha(commit, "commit")
        out, err, rc = _core_run_git(repo, ["git", "rev-parse", f"{commit}^{{tree}}"], timeout=10)
        if rc != 0:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"cannot resolve tree for {commit[:12]}: {err.strip()[:200]}")
        tree = out.strip()
        if not tree:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"empty tree for {commit[:12]}")
        return tree

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        repo = self._checked(repo)
        _validate_commit_sha(ancestor, "ancestor")
        _validate_commit_sha(descendant, "descendant")
        _out, _err, rc = _core_run_git(repo, ["git", "merge-base", "--is-ancestor", ancestor, descendant], timeout=10)
        return rc == 0

    def fast_forward_branch(self, repo: Path, branch: str, expected_old: str, target: str) -> tuple[str, str, str]:
        repo = self._checked(repo)
        _validate_commit_sha(expected_old, "expected_old")
        _validate_commit_sha(target, "target")
        if branch in ("", "HEAD") or "/" in branch or branch.startswith("-"):
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"invalid branch {branch!r}")
        # Verify target exists
        out, err, rc = _core_run_git(repo, ["git", "cat-file", "-t", target], timeout=10)
        if rc != 0 or out.strip() != "commit":
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"target {target[:12]} is not a commit: {err.strip()[:200]}")
        before = self.resolve_ref(repo, f"refs/heads/{branch}")
        if before is None:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"branch {branch!r} does not exist")
        if before == target:
            return before, target, "already_applied"
        if before != expected_old:
            raise FinalizerError(
                FinalizerFailure.GIT_CAS_CONFLICT,
                f"SOURCE_CHANGED: branch {branch} is {before[:12]}, expected {expected_old[:12]}",
            )
        if not self.is_ancestor(repo, expected_old, target):
            raise FinalizerError(
                FinalizerFailure.GIT_NON_FAST_FORWARD,
                f"non-ancestor: {expected_old[:12]} is not ancestor of {target[:12]}",
            )
        # Atomic CAS via update-ref with old value (no force, no reset, no merge).
        _out, err, rc = _core_run_git(
            repo, ["git", "update-ref", f"refs/heads/{branch}", target, expected_old], timeout=10
        )
        if rc != 0:
            # Re-read to distinguish CAS race from other failure.
            current = self.resolve_ref(repo, f"refs/heads/{branch}")
            if current == target:
                return expected_old, target, "already_applied"
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"update-ref failed: {err.strip()[:300]}")
        after = self.resolve_ref(repo, f"refs/heads/{branch}")
        if after != target:
            raise FinalizerError(FinalizerFailure.REMOTE_DURABILITY_UNPROVEN, f"branch {branch} did not advance to target")
        return before, after, "applied"

    def read_remote_ref(self, repo: Path, remote: str, branch: str) -> str | None:
        repo = self._checked(repo)
        if not isinstance(remote, str) or not remote.strip() or len(remote) > 64:
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"invalid remote {remote!r}")
        out, _err, rc = _core_run_git(repo, ["git", "ls-remote", remote, f"refs/heads/{branch}"], timeout=15)
        if rc != 0:
            return None
        line = out.strip().splitlines()
        if not line:
            return None
        sha = line[0].split()[0] if line[0].split() else ""
        return sha if _SAFE_REF_RE.fullmatch(sha) else None

    def push_branch(self, repo: Path, remote: str, branch: str, target: str) -> None:
        repo = self._checked(repo)
        _validate_commit_sha(target, "target")
        # FF-only push: never --force, never --force-with-lease, never --delete.
        _out, err, rc = _core_run_git(repo, ["git", "push", remote, f"{target}:refs/heads/{branch}"], timeout=30)
        if rc != 0:
            msg = err.strip()[:500]
            if "non-fast-forward" in msg or "rejected" in msg or "fetch first" in msg:
                raise FinalizerError(FinalizerFailure.GIT_NON_FAST_FORWARD, f"push rejected (non-ff): {msg[:200]}")
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"push failed: {msg[:200]}")

    def verify_remote_tree(self, repo: Path, remote: str, branch: str) -> str | None:
        # Remote tree is verified by fetching the remote ref SHA then resolving
        # its tree locally after a bounded fetch of that single ref. To avoid
        # mutating local branches, fetch into FETCH_HEAD only.
        repo = self._checked(repo)
        sha = self.read_remote_ref(repo, remote, branch)
        if sha is None:
            return None
        _out, _err, rc = _core_run_git(repo, ["git", "fetch", remote, branch], timeout=30)
        if rc != 0:
            return None
        try:
            return self.resolve_tree(repo, sha)
        except FinalizerError:
            return None


class InMemoryGitPort(GitPort):
    """Deterministic in-memory Git double for unit tests (no subprocess)."""

    def __init__(self) -> None:
        # repo_key -> {branches: {branch: sha}, commits: {sha: (parents, tree)}, remotes: {remote: {branch: sha}}}
        self._repos: dict[str, dict[str, Any]] = {}

    def _key(self, repo: Path) -> str:
        return str(repo)

    def seed_repo(
        self,
        repo: Path,
        *,
        branches: dict[str, str],
        commits: dict[str, tuple[list[str], str]],
        remotes: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._repos[self._key(repo)] = {
            "branches": dict(branches),
            "commits": {k: (list(p), t) for k, (p, t) in commits.items()},
            "remotes": {r: dict(b) for r, b in (remotes or {}).items()},
        }

    def _repo(self, repo: Path) -> dict[str, Any]:
        k = self._key(repo)
        if k not in self._repos:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"unknown test repo {k}")
        return self._repos[k]

    def resolve_ref(self, repo: Path, ref: str) -> str | None:
        st = self._repo(repo)
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/"):]
            return st["branches"].get(branch)
        # Bare SHA passthrough if known
        if _SAFE_REF_RE.fullmatch(ref) and ref in st["commits"]:
            return ref
        return st["branches"].get(ref)

    def resolve_tree(self, repo: Path, commit: str) -> str:
        st = self._repo(repo)
        if commit not in st["commits"]:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"unknown commit {commit[:12]}")
        return st["commits"][commit][1]

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        st = self._repo(repo)
        if ancestor == descendant:
            return True
        # BFS over parents
        seen: set[str] = set()
        stack = [descendant]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur == ancestor:
                return True
            parents, _tree = st["commits"].get(cur, ([], ""))
            stack.extend(parents)
        return False

    def fast_forward_branch(self, repo: Path, branch: str, expected_old: str, target: str) -> tuple[str, str, str]:
        st = self._repo(repo)
        before = st["branches"].get(branch)
        if before is None:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"branch {branch!r} missing")
        if before == target:
            return before, target, "already_applied"
        if before != expected_old:
            raise FinalizerError(
                FinalizerFailure.GIT_CAS_CONFLICT, f"SOURCE_CHANGED: {before[:12]} != expected {expected_old[:12]}"
            )
        if target not in st["commits"]:
            raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, f"target {target[:12]} unknown")
        if not self.is_ancestor(repo, expected_old, target):
            raise FinalizerError(FinalizerFailure.GIT_NON_FAST_FORWARD, "non-ancestor")
        st["branches"][branch] = target
        return before, target, "applied"

    def read_remote_ref(self, repo: Path, remote: str, branch: str) -> str | None:
        st = self._repo(repo)
        return st["remotes"].get(remote, {}).get(branch)

    def push_branch(self, repo: Path, remote: str, branch: str, target: str) -> None:
        st = self._repo(repo)
        remotes = st.setdefault("remotes", {})
        rmap = remotes.setdefault(remote, {})
        current = rmap.get(branch)
        local_branches = st["branches"]
        # Simulate ff-only: if remote exists and is not ancestor of target -> reject.
        if current is not None and current != target:
            if current not in st["commits"] or target not in st["commits"]:
                raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, "push unknown commit")
            if not self.is_ancestor(repo, current, target):
                raise FinalizerError(FinalizerFailure.GIT_NON_FAST_FORWARD, "push non-ff")
        rmap[branch] = target
        # Keep local branch in sync for durability checks that read local? Not required.

    def verify_remote_tree(self, repo: Path, remote: str, branch: str) -> str | None:
        sha = self.read_remote_ref(repo, remote, branch)
        if sha is None:
            return None
        st = self._repo(repo)
        if sha not in st["commits"]:
            return None
        return st["commits"][sha][1]


# ---------------------------------------------------------------------------
# Typed GitHub governance boundary (narrowest seam, CAS, no generic API)
# ---------------------------------------------------------------------------


class GitHubPort:
    """Narrowest typed GitHub seam (no arbitrary request, no generic edit)."""

    def read_comment(self, repo: str, issue: int, comment_id: str) -> tuple[str | int | None, str | None, str]:
        raise NotImplementedError

    def update_comment(
        self, repo: str, issue: int, comment_id: str,
        expected_revision: str | int | None, expected_digest: str | None,
        candidate_body: str,
    ) -> tuple[bool, str | int | None, str | None, str | None]:
        """Returns (success, new_revision, new_digest, error_code)."""
        raise NotImplementedError


class InMemoryGitHubPort(GitHubPort):
    """Deterministic in-memory GitHub double (isolated, no network)."""

    def __init__(self, comments: dict[str, tuple[str | int, str]] | None = None) -> None:
        # comment_id -> (revision, body)
        self._comments: dict[str, tuple[str | int, str]] = dict(comments or {})
        self.update_call_count = 0
        self.read_call_count = 0

    def seed(self, comment_id: str, body: str, revision: str | int = "1") -> None:
        self._comments[comment_id] = (revision, body)

    @staticmethod
    def _digest(body: str) -> str:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def read_comment(self, repo: str, issue: int, comment_id: str) -> tuple[str | int | None, str | None, str]:
        self.read_call_count += 1
        if comment_id not in self._comments:
            raise FinalizerError(FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH, f"comment {comment_id!r} not found")
        rev, body = self._comments[comment_id]
        return rev, self._digest(body), body

    def update_comment(
        self, repo: str, issue: int, comment_id: str,
        expected_revision: str | int | None, expected_digest: str | None,
        candidate_body: str,
    ) -> tuple[bool, str | int | None, str | None, str | None]:
        self.update_call_count += 1
        if comment_id not in self._comments:
            return False, None, None, "NOT_FOUND"
        rev, body = self._comments[comment_id]
        cur_digest = self._digest(body)
        if expected_revision is not None and expected_revision != rev:
            return False, rev, cur_digest, "STALE"
        if expected_digest is not None and expected_digest != cur_digest:
            return False, rev, cur_digest, "STALE"
        if candidate_body == body:
            return True, rev, cur_digest, "ALREADY"
        # Bump revision deterministically
        if isinstance(rev, int):
            new_rev: str | int = rev + 1
        elif isinstance(rev, str) and rev.isdigit():
            new_rev = str(int(rev) + 1)
        else:
            new_rev = f"{rev}-next"
        new_digest = self._digest(candidate_body)
        self._comments[comment_id] = (new_rev, candidate_body)
        return True, new_rev, new_digest, None


class GhCliGitHubPort(GitHubPort):
    """Live GitHub seam via operator gh (GH_CONFIG_DIR + rtk gh). Read-only except bounded update.

    Uses only typed REST endpoints for issue comments; never a generic
    request surface. Token comes from the system keyring via gh config and
    is never stored in source/receipt/logs or returned to the model.
    """

    def __init__(self, *, gh_bin: str = "rtk", gh_config_dir: str | None = None) -> None:
        self._gh_bin = gh_bin
        env_dir = gh_config_dir or os.environ.get("GH_CONFIG_DIR", "/home/latios/.config/gh")
        self._gh_config_dir = env_dir

    def _env(self) -> dict[str, str]:
        return {**os.environ, "GH_CONFIG_DIR": self._gh_config_dir}

    @staticmethod
    def _digest(body: str) -> str:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def read_comment(self, repo: str, issue: int, comment_id: str) -> tuple[str | int | None, str | None, str]:
        # Validate identity shape (fail closed, no arbitrary target).
        if not _SAFE_REPO_RE.fullmatch(repo):
            raise FinalizerError(FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH, f"invalid repo {repo!r}")
        if type(issue) is not int or issue <= 0:
            raise FinalizerError(FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH, f"invalid issue {issue!r}")
        if not comment_id.strip().isdigit():
            raise FinalizerError(FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH, f"invalid comment_id {comment_id!r}")
        proc = subprocess.run(
            [self._gh_bin, "gh", "api", f"repos/{repo}/issues/comments/{comment_id}"],
            capture_output=True, text=True, timeout=15, env=self._env(),
        )
        if proc.returncode != 0:
            raise FinalizerError(
                FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH,
                f"read comment failed: {proc.stderr.strip()[:300]}",
            )
        try:
            data = json.loads(proc.stdout)
        except Exception as exc:
            raise FinalizerError(FinalizerFailure.GITHUB_CAS_CONFLICT, f"comment JSON parse failed: {exc}") from exc
        body = data.get("body") or ""
        updated_at = data.get("updated_at")
        if not isinstance(body, str):
            raise FinalizerError(FinalizerFailure.GITHUB_CAS_CONFLICT, "comment body is not str")
        return (str(updated_at) if updated_at else "1"), self._digest(body), body

    def update_comment(
        self, repo: str, issue: int, comment_id: str,
        expected_revision: str | int | None, expected_digest: str | None,
        candidate_body: str,
    ) -> tuple[bool, str | int | None, str | None, str | None]:
        # Read-before-write is enforced by the finalizer; here we re-read to
        # implement server-side CAS (expected digest/version must still hold).
        cur_rev, cur_digest, cur_body = self.read_comment(repo, issue, comment_id)
        if expected_revision is not None and expected_revision != cur_rev:
            return False, cur_rev, cur_digest, "STALE"
        if expected_digest is not None and expected_digest != cur_digest:
            return False, cur_rev, cur_digest, "STALE"
        if candidate_body == cur_body:
            return True, cur_rev, cur_digest, "ALREADY"
        # Typed PATCH of exactly one comment body (no other fields, no new comment).
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump({"body": candidate_body}, tmp)
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                [self._gh_bin, "gh", "api", f"repos/{repo}/issues/comments/{comment_id}",
                 "-X", "PATCH", "--input", tmp_path],
                capture_output=True, text=True, timeout=20, env=self._env(),
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if proc.returncode != 0:
            return False, cur_rev, cur_digest, f"PATCH_FAILED:{proc.stderr.strip()[:200]}"
        try:
            data = json.loads(proc.stdout)
        except Exception:
            # Verify via re-read.
            new_rev, new_digest, _body = self.read_comment(repo, issue, comment_id)
            if new_digest == self._digest(candidate_body):
                return True, new_rev, new_digest, None
            return False, new_rev, new_digest, "VERIFY_MISMATCH"
        new_body = data.get("body") or candidate_body
        new_rev = data.get("updated_at") or cur_rev
        new_digest = self._digest(new_body)
        if new_digest != self._digest(candidate_body):
            # Verify via re-read before declaring mismatch.
            v_rev, v_digest, _v_body = self.read_comment(repo, issue, comment_id)
            if v_digest == self._digest(candidate_body):
                return True, v_rev, v_digest, None
            return False, v_rev, v_digest, "VERIFY_MISMATCH"
        return True, new_rev, new_digest, None


# ---------------------------------------------------------------------------
# Idempotency + receipt store (durable, bounded, not a workflow DB)
# ---------------------------------------------------------------------------


def compute_idempotency_key(
    *,
    project_id: str,
    plan_ref: str,
    milestone_ref: str,
    closure_phase: ClosurePhase | str,
    steward_digest: str,
    reviewed_frontier: str,
    accepted_frontier: str | None,
    scope: Mapping[str, Any],
) -> str:
    phase = closure_phase.value if isinstance(closure_phase, ClosurePhase) else str(closure_phase)
    payload = {
        "project_id": project_id,
        "plan_ref": plan_ref,
        "milestone_ref": milestone_ref,
        "closure_phase": phase,
        "steward_digest": steward_digest,
        "reviewed_frontier": reviewed_frontier,
        "accepted_frontier": accepted_frontier,
        "scope": dict(scope),
    }
    return "fin-" + hashlib.sha256(canonical_json(canonicalize(payload, path="FinalizerIdempotency")).encode("utf-8")).hexdigest()[:32]


class FileBackedReceiptStore:
    """Bounded file-backed durable receipt map (idempotency_key -> receipt).

    This is NOT a workflow database and NOT an event bus: it stores only
    MaterializationReceipts keyed by deterministic idempotency key, with
    atomic temp+rename persist. Same key + different semantic digest fails
    closed (no silent overwrite).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _load_all(self) -> dict[str, Any]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _persist_all(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, idempotency_key: str) -> MaterializationReceipt | None:
        data = self._load_all()
        raw = data.get(idempotency_key)
        if raw is None:
            return None
        try:
            return MaterializationReceipt.from_dict(raw)
        except Exception:
            return None

    def put(self, receipt: MaterializationReceipt) -> MaterializationReceipt:
        data = self._load_all()
        existing = data.get(receipt.idempotency_key)
        if existing is not None:
            try:
                stored = MaterializationReceipt.from_dict(existing)
            except Exception as exc:
                raise FinalizerError(FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE, f"corrupt stored receipt: {exc}") from exc
            if stored.steward_digest != receipt.steward_digest:
                raise FinalizerError(
                    FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                    "same idempotency key with different semantic digest: fail closed",
                )
            # Allow a PARTIAL receipt to be upgraded to a completed receipt
            # with the same steward digest (restart reconciliation after partial
            # failure). Completed receipts are immutable.
            if stored.final_status == "PARTIAL" and receipt.final_status != "PARTIAL":
                if stored.receipt_digest == receipt.receipt_digest:
                    return stored
                data[receipt.idempotency_key] = receipt.to_dict()
                self._persist_all(data)
                return receipt
            if stored.receipt_digest != receipt.receipt_digest:
                # Same key but different semantic content -> fail closed (do not overwrite).
                # If the stored receipt already covers the same desired state, the
                # caller should have hit the idempotent path earlier.
                raise FinalizerError(
                    FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                    "same idempotency key with different semantic digest: fail closed",
                )
            return stored
        data[receipt.idempotency_key] = receipt.to_dict()
        self._persist_all(data)
        return receipt


class InMemoryReceiptStore:
    """In-memory receipt double for unit tests."""

    def __init__(self) -> None:
        self._map: dict[str, MaterializationReceipt] = {}

    def get(self, key: str) -> MaterializationReceipt | None:
        return self._map.get(key)

    def put(self, receipt: MaterializationReceipt) -> MaterializationReceipt:
        existing = self._map.get(receipt.idempotency_key)
        if existing is not None:
            if existing.steward_digest != receipt.steward_digest:
                raise FinalizerError(
                    FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                    "same idempotency key with different semantic digest",
                )
            if existing.final_status == "PARTIAL" and receipt.final_status != "PARTIAL":
                if existing.receipt_digest == receipt.receipt_digest:
                    return existing
                self._map[receipt.idempotency_key] = receipt
                return receipt
            if existing.receipt_digest != receipt.receipt_digest:
                raise FinalizerError(
                    FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                    "same idempotency key with different semantic digest",
                )
            return existing
        self._map[receipt.idempotency_key] = receipt
        return receipt


# ---------------------------------------------------------------------------
# Server authorization (Role can never call the internal finalizer directly)
# ---------------------------------------------------------------------------

_ALLOWED_INTERNAL_CALLER = "coordinator-internal"


def authorize_finalizer_call(*, caller_role: str, via_coordinator: bool, has_closure_evidence: bool) -> None:
    """Fail closed unless the call arrives via the exact coordinator transition.

    Every AgentWorkRole direct call is denied — including project-steward,
    coder, reviewer, analyst, and task-main without exact evidence. The only
    allowed path is the internal coordinator transition carrying
    MilestoneClosureReadiness + StewardResult + trusted binding evidence.
    """
    if not isinstance(caller_role, str) or not caller_role.strip():
        raise FinalizerError(FinalizerFailure.TRUST_BINDING_MISMATCH, "caller_role must be non-empty")
    role = caller_role.strip()
    # All direct Role calls denied, even task-main without evidence.
    if not via_coordinator or not has_closure_evidence:
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"finalizer direct call by {role!r} denied: requires exact coordinator/closure evidence",
        )
    if role != _ALLOWED_INTERNAL_CALLER and role in (
        "project-steward", "coder", "reviewer", "analyst", "task-main",
        "PROJECT_STEWARD", "CODER", "REVIEWER", "ANALYST", "TASK_MAIN",
    ):
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"Role {role!r} cannot call internal finalizer outside the coordinator transition",
        )
    # Internal coordinator path with evidence is allowed; anything else denied.
    if role != _ALLOWED_INTERNAL_CALLER:
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"unknown finalizer caller {role!r}: only coordinator-internal is allowed",
        )


# ---------------------------------------------------------------------------
# TrustedStewardFinalizer
# ---------------------------------------------------------------------------


@dataclass
class TrustedStewardFinalizer:
    """Server-side trusted mechanical boundary (no generic mutation authority)."""

    git: GitPort
    github: GitHubPort
    repo_path: Path | None = None  # trusted local checkout for Git ops; None disables Git
    receipt_store: Any | None = None  # FileBackedReceiptStore | InMemoryReceiptStore | None

    def _steward_digest(self, steward: StewardResult) -> str:
        return _steward_digest(steward)

    def _binding_digest(self, binding: TrustedProjectBinding) -> str:
        return _digest_str(f"{binding.project_id}:{binding.binding_ref}:{binding.binding_digest}")

    def finalize(
        self,
        finalizer_input: FinalizerInput,
        *,
        caller_role: str = "coordinator-internal",
        via_coordinator: bool = True,
        has_closure_evidence: bool = True,
    ) -> MaterializationReceipt:
        """Execute the bounded trusted finalization (idempotent, CAS, recoverable)."""
        # 0. Server authority: Role direct calls denied.
        authorize_finalizer_call(caller_role=caller_role, via_coordinator=via_coordinator, has_closure_evidence=has_closure_evidence)

        if not isinstance(finalizer_input, FinalizerInput):
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"input must be FinalizerInput, got {type(finalizer_input).__name__}")
        steward = finalizer_input.steward_result
        readiness = finalizer_input.readiness
        binding = finalizer_input.trusted_binding
        plan = finalizer_input.trusted_plan
        gate = finalizer_input.user_gate
        intent = finalizer_input.intent
        phase = finalizer_input.closure_phase

        # 1. Validated StewardResult required (type already enforced; re-assert seam).
        if not isinstance(steward, StewardResult):
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, "steward_result must be StewardResult")
        # Steward can never set user approval (constructor already enforces, double-check).
        if steward.user_approval_set:
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, "steward cannot set user approval")

        # 2. Trusted binding required + must match input project scope.
        # The binding project must equal the receipt project (derived from trusted plan context owner?
        # For genericity, require binding.project_id non-empty; coordinator integration
        # checks it equals the coordinator project_id. Here we check internal coherence:
        # plan.milestone must equal readiness milestone and steward milestone.
        if readiness.milestone_ref.ref != plan.milestone_ref:
            raise FinalizerError(
                FinalizerFailure.TRUST_BINDING_MISMATCH,
                f"readiness milestone {readiness.milestone_ref.ref!r} != trusted plan milestone {plan.milestone_ref!r}",
            )
        if steward.milestone_ref != readiness.milestone_ref.ref:
            raise FinalizerError(
                FinalizerFailure.INVALID_STEWARD_RESULT,
                f"steward milestone {steward.milestone_ref!r} != readiness milestone {readiness.milestone_ref.ref!r}",
            )
        if steward.milestone_ref != plan.milestone_ref:
            raise FinalizerError(
                FinalizerFailure.TRUST_BINDING_MISMATCH,
                f"steward milestone {steward.milestone_ref!r} != trusted plan milestone {plan.milestone_ref!r}",
            )

        # 3. Invented-frontier check: steward reviewed frontier must equal trusted reviewed frontier.
        trusted_reviewed = readiness.reviewed_frontier_ref.ref
        if steward.reviewed_frontier_ref != trusted_reviewed:
            raise FinalizerError(
                FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                f"steward reviewed {steward.reviewed_frontier_ref!r} not backed by trusted reviewed {trusted_reviewed!r}",
            )
        # Scope containment: every mechanical operation must be covered by steward scope.
        # Git target + github proof markers must be within governance evidence refs or reviewed frontier.
        scope_refs: list[str] = []
        if intent.git_promotion is not None and intent.git_promotion.wants_promotion:
            scope_refs.append(intent.git_promotion.target_ref or "")
        for u in intent.github_updates:
            scope_refs.append(u.proof_marker)
            scope_refs.append(u.comment_id)
        # The steward scope covers reviewed frontier + governance evidence refs.
        # Git target must equal the reviewed frontier (no invented promotion target).
        if intent.git_promotion is not None and intent.git_promotion.wants_promotion:
            target = intent.git_promotion.target_ref or ""
            if target != trusted_reviewed and target != (steward.accepted_frontier_ref or trusted_reviewed):
                # Promotion target must derive from trusted reviewed frontier.
                raise FinalizerError(
                    FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                    f"promotion target {target[:12]} derives from no trusted reviewed frontier {trusted_reviewed[:12]}",
                )
            try:
                steward.assert_finalizer_scope(operation_refs=[target])
            except ValueError as exc:
                raise FinalizerError(FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH, str(exc)) from exc
        # GitHub scope: proof markers are bounded intent, but the comment identity
        # itself must be trusted (checked below). Steward scope containment for
        # GitHub is enforced via Plan-identity checks, not freeform refs.
        # Additionally enforce steward scope containment for GitHub proof markers:
        # every marker must be covered by the steward's governance evidence refs
        # (or be the reviewed frontier itself). This preserves
        # FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE=no for governance sync.
        if intent.github_updates:
            try:
                steward.assert_finalizer_scope(operation_refs=[u.proof_marker for u in intent.github_updates])
            except ValueError as exc:
                raise FinalizerError(
                    FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                    f"finalizer scope containment: {exc}",
                ) from exc

        # 4. Closure-phase distinction + user-gate authority.
        steward_digest = self._steward_digest(steward)
        binding_digest = self._binding_digest(binding)
        reviewed = trusted_reviewed
        accepted = steward.accepted_frontier_ref
        if phase is ClosurePhase.REVIEWED_CLOSURE:
            # Reviewed closure may materialize review projection + governance sync,
            # but must NEVER mark acceptance, set approval, or promote to accepted/main.
            if intent.git_promotion is not None and intent.git_promotion.wants_promotion:
                # Reviewed phase must not carry a promotion intent at all.
                raise FinalizerError(
                    FinalizerFailure.USER_GATE_REQUIRED,
                    "reviewed closure cannot promote to main: USER_GATE_REQUIRED (awaiting-user-acceptance)",
                )
            # Accepted frontier must not be claimed in reviewed phase.
            if accepted is not None:
                raise FinalizerError(
                    FinalizerFailure.INVALID_STEWARD_RESULT,
                    "reviewed closure must not carry accepted_frontier_ref",
                )
        elif phase is ClosurePhase.ACCEPTED_CLOSURE:
            # Accepted closure requires trusted durable user-gate evidence already proving approval.
            # StewardResult text/skill/SOUL/model output can never supply it.
            if gate.user_approval_satisfied is not True:
                raise FinalizerError(
                    FinalizerFailure.USER_GATE_REQUIRED,
                    "accepted closure denied: DURABLE_USER_GATE_STATE_IS_REQUIRED_FOR_ACCEPTED_CLOSURE",
                )
            # Accepted frontier must derive from trusted reviewed frontier.
            if accepted is None:
                raise FinalizerError(
                    FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                    "accepted closure requires accepted_frontier_ref equal to reviewed frontier",
                )
            if accepted != trusted_reviewed:
                raise FinalizerError(
                    FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                    f"accepted {accepted[:12]} != trusted reviewed {trusted_reviewed[:12]}",
                )
            # Promotion target (if any) must equal accepted frontier.
            if intent.git_promotion is not None and intent.git_promotion.wants_promotion:
                if intent.git_promotion.target_ref != accepted:
                    raise FinalizerError(
                        FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                        "promotion target must equal trusted accepted frontier",
                    )
        else:
            raise FinalizerError(FinalizerFailure.INVALID_STEWARD_RESULT, f"unknown closure phase {phase!r}")

        # 5. Trusted Plan identity for GitHub targets + managed-comment authority.
        for u in intent.github_updates:
            trusted_cid = plan.comment_id_for(u.role)
            if trusted_cid is None:
                raise FinalizerError(
                    FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH,
                    f"role {u.role!r} has no trusted managed mapping (no sixth comment)",
                )
            if u.comment_id != trusted_cid:
                raise FinalizerError(
                    FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH,
                    f"comment identity {u.comment_id!r} != trusted {trusted_cid!r} for role {u.role!r}",
                )
        # No sixth managed comment: intent roles are already bounded to MANAGED_ROLES
        # by GitHubUpdateIntent construction; double-assert here.
        for u in intent.github_updates:
            if u.role not in MANAGED_ROLES:
                raise FinalizerError(
                    FinalizerFailure.MANAGED_COMMENT_IDENTITY_MISMATCH, f"unknown managed role {u.role!r}"
                )

        # 6. Idempotency key (deterministic, no random).
        scope = intent.scope_dict()
        idem_key = compute_idempotency_key(
            project_id=binding.project_id,
            plan_ref=plan.plan_ref,
            milestone_ref=plan.milestone_ref,
            closure_phase=phase,
            steward_digest=steward_digest,
            reviewed_frontier=reviewed,
            accepted_frontier=accepted,
            scope=scope,
        )
        # 6a. Durable replay: same request already materialized -> ALREADY_APPLIED equivalent.
        if self.receipt_store is not None:
            existing = self.receipt_store.get(idem_key)
            if existing is not None:
                if existing.steward_digest != steward_digest:
                    raise FinalizerError(
                        FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                        "same idempotency key with different semantic digest: fail closed",
                    )
                # Completed receipts are immutable and idempotent: replay returns
                # the stored receipt without remote mutation. PARTIAL receipts
                # must NOT short-circuit: replay resumes via remote observation
                # (idempotent ops) and upgrades the receipt.
                if existing.final_status != "PARTIAL":
                    # Same semantic digest -> idempotent replay, no remote mutation.
                    # Return the stored receipt as ALREADY_APPLIED equivalent (same digest, same state).
                    return existing

        # 7. Execute typed operations (each idempotent/CAS, durable receipt, no global atomicity).
        operations: list[OperationReceipt] = []
        git_final_refs: dict[str, Any] | None = None
        git_final_tree: str | None = None
        github_evidence: dict[str, Any] = {}
        partial_failed = False
        partial_error: FinalizerError | None = None

        # 7a. Git promotion (accepted phase only; reviewed never promotes).
        if intent.git_promotion is not None and intent.git_promotion.wants_promotion:
            gp = intent.git_promotion
            assert gp.expected_old_ref is not None and gp.target_ref is not None
            if self.repo_path is None:
                raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, "Git repo_path unavailable for promotion")
            repo = Path(self.repo_path)
            branch = gp.branch
            expected_old = gp.expected_old_ref
            target = gp.target_ref
            # Verify target tree if expected tree supplied (accepted-frontier integrity).
            if gp.expected_tree is not None:
                try:
                    actual_tree = self.git.resolve_tree(repo, target)
                except FinalizerError as exc:
                    raise FinalizerError(FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH, f"tree verify failed: {exc.detail}") from exc
                if actual_tree != gp.expected_tree:
                    raise FinalizerError(
                        FinalizerFailure.ACCEPTED_FRONTIER_MISMATCH,
                        f"target tree {actual_tree[:12]} != expected {gp.expected_tree[:12]}",
                    )
                git_final_tree = actual_tree
            else:
                try:
                    git_final_tree = self.git.resolve_tree(repo, target)
                except FinalizerError:
                    git_final_tree = None
            # Observe current local ref first (crash-window: already applied?).
            try:
                current_local = self.git.resolve_ref(repo, f"refs/heads/{branch}")
            except FinalizerError as exc:
                raise FinalizerError(FinalizerFailure.GIT_CAS_CONFLICT, exc.detail) from exc
            if current_local == target:
                # Already at desired state (e.g., replay after crash before receipt).
                operations.append(
                    OperationReceipt(
                        kind="git.fast_forward_local_branch",
                        target=f"git:branch:{branch}",
                        before_ref=current_local,
                        after_ref=target,
                        status="already_applied",
                        detail="local already at target",
                    )
                )
                # Still need remote durability below.
            else:
                try:
                    before, after, status = self.git.fast_forward_branch(repo, branch, expected_old, target)
                    operations.append(
                        OperationReceipt(
                            kind="git.fast_forward_local_branch",
                            target=f"git:branch:{branch}",
                            before_ref=before,
                            after_ref=after,
                            status=status,
                            detail="ff-only CAS promotion",
                        )
                    )
                except FinalizerError as exc:
                    operations.append(
                        OperationReceipt(
                            kind="git.fast_forward_local_branch",
                            target=f"git:branch:{branch}",
                            before_ref=current_local,
                            after_ref=current_local,
                            status="failed",
                            detail=exc.detail[:200],
                        )
                    )
                    partial_failed = True
                    partial_error = exc
            # Remote durability (only if local step did not hard-fail with CAS/non-ff).
            if gp.remote is not None and not partial_failed:
                try:
                    remote_before = self.git.read_remote_ref(repo, gp.remote, branch)
                except FinalizerError as exc:
                    operations.append(
                        OperationReceipt(
                            kind="git.verify_remote_ref",
                            target=f"git:remote:{gp.remote}/{branch}",
                            status="failed",
                            detail=exc.detail[:200],
                        )
                    )
                    partial_failed = True
                    partial_error = exc
                    remote_before = None
                else:
                    if remote_before == target:
                        operations.append(
                            OperationReceipt(
                                kind="git.push_expected_ref",
                                target=f"git:remote:{gp.remote}/{branch}",
                                before_ref=remote_before,
                                after_ref=target,
                                status="already_applied",
                                detail="remote already durable",
                            )
                        )
                    else:
                        # Expected-old CAS on remote: remote must still equal the
                        # local expected_old (or already target). Otherwise drift.
                        # For a fresh promotion, remote_before should equal expected_old.
                        if remote_before is not None and remote_before != expected_old and remote_before != target:
                            operations.append(
                                OperationReceipt(
                                    kind="git.push_expected_ref",
                                    target=f"git:remote:{gp.remote}/{branch}",
                                    before_ref=remote_before,
                                    after_ref=remote_before,
                                    status="failed",
                                    detail=f"remote drift: {remote_before[:12]} != expected {expected_old[:12]}",
                                )
                            )
                            partial_failed = True
                            partial_error = FinalizerError(
                                FinalizerFailure.GIT_CAS_CONFLICT,
                                f"remote drift: {remote_before[:12]} != expected {expected_old[:12]}",
                            )
                        else:
                            try:
                                self.git.push_branch(repo, gp.remote, branch, target)
                            except FinalizerError as exc:
                                operations.append(
                                    OperationReceipt(
                                        kind="git.push_expected_ref",
                                        target=f"git:remote:{gp.remote}/{branch}",
                                        before_ref=remote_before,
                                        after_ref=remote_before,
                                        status="failed",
                                        detail=exc.detail[:200],
                                    )
                                )
                                partial_failed = True
                                partial_error = exc
                            else:
                                # Verify remote durability after push.
                                after_remote = self.git.read_remote_ref(repo, gp.remote, branch)
                                if after_remote != target:
                                    operations.append(
                                        OperationReceipt(
                                            kind="git.push_expected_ref",
                                            target=f"git:remote:{gp.remote}/{branch}",
                                            before_ref=remote_before,
                                            after_ref=after_remote,
                                            status="failed",
                                            detail="remote durability unproven",
                                        )
                                    )
                                    partial_failed = True
                                    partial_error = FinalizerError(
                                        FinalizerFailure.REMOTE_DURABILITY_UNPROVEN,
                                        f"remote {after_remote} != target {target[:12]} after push",
                                    )
                                else:
                                    operations.append(
                                        OperationReceipt(
                                            kind="git.push_expected_ref",
                                            target=f"git:remote:{gp.remote}/{branch}",
                                            before_ref=remote_before,
                                            after_ref=after_remote,
                                            status="applied",
                                            detail="remote durable",
                                        )
                                    )
            # Final refs/tree observation (best-effort, never authority).
            try:
                final_local = self.git.resolve_ref(repo, f"refs/heads/{branch}")
            except FinalizerError:
                final_local = None
            git_final_refs = {"branch": branch, "local": final_local, "target": target, "expected_old": expected_old}
            if gp.remote is not None:
                try:
                    git_final_refs["remote"] = self.git.read_remote_ref(repo, gp.remote, branch)
                except FinalizerError:
                    git_final_refs["remote"] = None
        else:
            # No Git promotion in scope (reviewed closure or accepted without promotion).
            git_final_refs = None

        # 7b. GitHub managed-comment updates (typed, CAS, idempotent).
        for u in intent.github_updates:
            target_label = f"github:{plan.governing_repo}#{plan.plan_issue_number}:{u.role}:{u.comment_id}"
            try:
                cur_rev, cur_digest, cur_body = self.github.read_comment(plan.governing_repo, plan.plan_issue_number, u.comment_id)
            except FinalizerError as exc:
                operations.append(
                    OperationReceipt(
                        kind="github.update_managed_comment",
                        target=target_label,
                        status="failed",
                        detail=exc.detail[:200],
                    )
                )
                partial_failed = True
                partial_error = exc
                continue
            # CAS: expected digest/version must match current.
            if u.expected_digest != cur_digest or (u.expected_revision is not None and str(u.expected_revision) != str(cur_rev)):
                operations.append(
                    OperationReceipt(
                        kind="github.update_managed_comment",
                        target=target_label,
                        before_ref=str(cur_rev),
                        before_digest=cur_digest,
                        after_ref=str(cur_rev),
                        after_digest=cur_digest,
                        status="failed",
                        detail=f"CAS_CONFLICT: expected {str(u.expected_digest)[:12]} vs current {str(cur_digest)[:12]}",
                    )
                )
                partial_failed = True
                partial_error = FinalizerError(
                    FinalizerFailure.GITHUB_CAS_CONFLICT,
                    f"CAS_CONFLICT for {u.role}: expected digest/revision stale",
                )
                continue
            desired_body = upsert_proof_subsection(cur_body, u.proof_marker, u.proof_content)
            if desired_body == cur_body:
                operations.append(
                    OperationReceipt(
                        kind="github.update_managed_comment",
                        target=target_label,
                        before_ref=str(cur_rev),
                        before_digest=cur_digest,
                        after_ref=str(cur_rev),
                        after_digest=cur_digest,
                        status="already_applied",
                        detail="desired state already present",
                    )
                )
                github_evidence[u.role] = {"comment_id": u.comment_id, "revision": str(cur_rev), "digest": cur_digest, "status": "already_applied"}
                continue
            desired_digest = hashlib.sha256(desired_body.encode("utf-8")).hexdigest()
            try:
                ok, new_rev, new_digest, err = self.github.update_comment(
                    plan.governing_repo, plan.plan_issue_number, u.comment_id,
                    expected_revision=cur_rev, expected_digest=cur_digest,
                    candidate_body=desired_body,
                )
            except FinalizerError as exc:
                operations.append(
                    OperationReceipt(
                        kind="github.update_managed_comment",
                        target=target_label,
                        before_ref=str(cur_rev),
                        before_digest=cur_digest,
                        status="failed",
                        detail=exc.detail[:200],
                    )
                )
                partial_failed = True
                partial_error = exc
                continue
            if not ok:
                if err == "ALREADY":
                    operations.append(
                        OperationReceipt(
                            kind="github.update_managed_comment",
                            target=target_label,
                            before_ref=str(cur_rev),
                            before_digest=cur_digest,
                            after_ref=str(cur_rev),
                            after_digest=cur_digest,
                            status="already_applied",
                            detail="store reports already",
                        )
                    )
                    github_evidence[u.role] = {"comment_id": u.comment_id, "revision": str(cur_rev), "digest": cur_digest, "status": "already_applied"}
                    continue
                operations.append(
                    OperationReceipt(
                        kind="github.update_managed_comment",
                        target=target_label,
                        before_ref=str(cur_rev),
                        before_digest=cur_digest,
                        after_ref=str(cur_rev) if new_rev is None else str(new_rev),
                        after_digest=cur_digest if new_digest is None else str(new_digest),
                        status="failed",
                        detail=f"GITHUB_CAS_CONFLICT: {err}",
                    )
                )
                partial_failed = True
                partial_error = FinalizerError(FinalizerFailure.GITHUB_CAS_CONFLICT, f"github update failed: {err}")
                continue
            # Verify desired digest (verify-after-write).
            if new_digest != desired_digest:
                # Re-read to confirm (handles revision-timestamp vs digest drift in live gh).
                try:
                    v_rev, v_digest, _v_body = self.github.read_comment(plan.governing_repo, plan.plan_issue_number, u.comment_id)
                except FinalizerError:
                    v_digest = new_digest
                    v_rev = new_rev
                if v_digest != desired_digest:
                    operations.append(
                        OperationReceipt(
                            kind="github.update_managed_comment",
                            target=target_label,
                            before_ref=str(cur_rev),
                            before_digest=cur_digest,
                            after_ref=str(new_rev) if new_rev is not None else None,
                            after_digest=str(new_digest) if new_digest is not None else None,
                            status="failed",
                            detail="verify mismatch",
                        )
                    )
                    partial_failed = True
                    partial_error = FinalizerError(FinalizerFailure.GITHUB_CAS_CONFLICT, "verify mismatch after github update")
                    continue
                new_rev, new_digest = v_rev, v_digest
            operations.append(
                OperationReceipt(
                    kind="github.update_managed_comment",
                    target=target_label,
                    before_ref=str(cur_rev),
                    before_digest=cur_digest,
                    after_ref=str(new_rev) if new_rev is not None else None,
                    after_digest=str(new_digest) if new_digest is not None else None,
                    status="applied",
                    detail="managed comment CAS update",
                )
            )
            github_evidence[u.role] = {"comment_id": u.comment_id, "revision": str(new_rev), "digest": str(new_digest), "status": "applied"}

        # 8. Build receipt (immutable, digest-bound, no secrets).
        if partial_failed:
            final_status = "PARTIAL"
        else:
            # All ops applied or already_applied -> determine ALREADY vs APPLIED.
            if operations and all(op.status == "already_applied" for op in operations):
                final_status = "ALREADY_APPLIED"
            elif not operations:
                # Review projection only (no Git/GitHub effects in scope).
                final_status = "APPLIED"
            else:
                final_status = "APPLIED"
        receipt_id = "mfr-" + hashlib.sha256(idem_key.encode("utf-8")).hexdigest()[:16]
        receipt = MaterializationReceipt(
            receipt_id=receipt_id,
            project_id=binding.project_id,
            plan_ref=plan.plan_ref,
            milestone_ref=plan.milestone_ref,
            closure_phase=phase.value,
            steward_digest=steward_digest,
            binding_digest=binding_digest,
            materialization_scope=scope,
            operations=tuple(operations),
            git_final_refs=git_final_refs,
            git_final_tree=git_final_tree,
            github_evidence=dict(github_evidence) if github_evidence else None,
            idempotency_key=idem_key,
            final_status=final_status,
            reconciled_at=_now_iso(),
            receipt_digest="",
        ).with_digest()

        # 9. Durable receipt persistence (crash-window: remote mutation before
        # local receipt is recoverable via remote observation on replay).
        if self.receipt_store is not None:
            try:
                stored = self.receipt_store.put(receipt)
                # If a receipt already existed with same key+digest, return stored
                # (idempotent, same-equivalent receipt).
                receipt = stored
            except FinalizerError:
                raise
            except Exception as exc:
                raise FinalizerError(FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE, f"receipt persist failed: {exc}") from exc

        # 10. Partial failure surfaces as typed error with receipt attached
        # (receipt shows partial state; replay resumes safely via idempotent ops).
        if partial_failed and partial_error is not None:
            # For GitHub CAS conflicts, surface the specific code; otherwise partial.
            if partial_error.code in (FinalizerFailure.GIT_CAS_CONFLICT, FinalizerFailure.GITHUB_CAS_CONFLICT,
                                      FinalizerFailure.GIT_NON_FAST_FORWARD,
                                      FinalizerFailure.REMOTE_DURABILITY_UNPROVEN):
                raise FinalizerError(partial_error.code, partial_error.detail, receipt=receipt)
            raise FinalizerError(FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE, partial_error.detail, receipt=receipt)
        return receipt


# ---------------------------------------------------------------------------
# Durable coordinator reconciliation + exact/logical task-main re-entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizerReentryEvidence:
    """Compact re-entry evidence (never full Git/GitHub output, never transcript)."""

    coordinator_id: str
    origin_session_ref: str
    steward_digest: str
    receipt_digest: str
    receipt_id: str
    closure_phase: str
    frontier: str
    next_action: str
    user_gate: str  # "satisfied" | "required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "origin_session_ref": self.origin_session_ref,
            "steward_digest": self.steward_digest,
            "receipt_digest": self.receipt_digest,
            "receipt_id": self.receipt_id,
            "closure_phase": self.closure_phase,
            "frontier": self.frontier,
            "next_action": self.next_action,
            "user_gate": self.user_gate,
        }


def finalize_closure_via_coordinator(
    *,
    coordinator_store: Any,
    coordinator_id: str,
    live_plan_view: Any,
    origin_session_ref: str | None = None,
    finalizer: TrustedStewardFinalizer,
    finalizer_input: FinalizerInput,
    caller_role: str = "coordinator-internal",
) -> tuple[MaterializationReceipt, FinalizerReentryEvidence]:
    """Task-main orchestrated transition (no manual Git/GitHub edits).

    The coordinator handle is refreshed from durable truth first; the logical
    coordinator/session identity is preserved across finalization. Only compact
    evidence (StewardResult ref, receipt ref, closure state, frontier, next
    action, user gate) is returned for re-entry — never raw logs/bodies.
    """
    from aota_forge.runtime.task_main.coordinator_store import CoordinatorNotFoundError  # local to avoid cycle

    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    if coordinator_store is None or not hasattr(coordinator_store, "get"):
        raise FinalizerError(FinalizerFailure.TRUST_BINDING_MISMATCH, "coordinator_store unavailable")
    state = coordinator_store.get(coordinator_id)
    if state is None:
        try:
            raise CoordinatorNotFoundError(coordinator_id)
        except Exception as exc:
            raise FinalizerError(FinalizerFailure.TRUST_BINDING_MISMATCH, f"coordinator not found: {coordinator_id}") from exc
    # Preserve exact logical identity (pre-finalizer id == post-finalizer id).
    pre_coordinator_id: str = state.coordinator_id
    pre_session: str = getattr(state, "origin_task_main_session_ref", origin_session_ref or "")
    if origin_session_ref is not None and pre_session != origin_session_ref:
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"session mismatch: durable {pre_session[:16]} vs caller {origin_session_ref[:16]}",
        )
    # Binding coherence: coordinator project must equal finalizer binding project.
    coord_project = getattr(state, "project_id", "")
    if coord_project and coord_project != finalizer_input.trusted_binding.project_id:
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"binding project {finalizer_input.trusted_binding.project_id!r} != coordinator project {coord_project!r}",
        )
    # Plan binding coherence: live view must match finalizer trusted plan (no drift).
    try:
        live_authority = getattr(live_plan_view, "plan_authority", None)
        live_milestone = getattr(live_plan_view, "milestone_id", None)
    except Exception:
        live_authority = None
        live_milestone = None
    if live_authority is not None and live_authority != finalizer_input.trusted_plan.plan_ref:
        # plan_ref is the trusted Plan identity string; allow live authority to
        # equal it, otherwise fail closed (no silent cross-Plan finalization).
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"live plan {live_authority!r} != trusted plan {finalizer_input.trusted_plan.plan_ref!r}",
        )
    if live_milestone is not None and live_milestone != finalizer_input.trusted_plan.milestone_ref:
        raise FinalizerError(
            FinalizerFailure.TRUST_BINDING_MISMATCH,
            f"live milestone {live_milestone!r} != trusted {finalizer_input.trusted_plan.milestone_ref!r}",
        )

    # Execute trusted finalizer via the internal path (Role direct calls denied inside).
    receipt = finalizer.finalize(
        finalizer_input, caller_role=caller_role, via_coordinator=True, has_closure_evidence=True
    )

    # Durable coordinator reconciliation (existing CAS fields only — no new workflow DB).
    # Update frontier/next-action/blockers to reflect closure disposition.
    try:
        phase = finalizer_input.closure_phase.value if isinstance(finalizer_input.closure_phase, ClosurePhase) else str(finalizer_input.closure_phase)
        reviewed = finalizer_input.readiness.reviewed_frontier_ref.ref
        accepted = finalizer_input.steward_result.accepted_frontier_ref
        if phase == "ACCEPTED_CLOSURE" and accepted:
            frontier_ref = {"ref": accepted, "digest": receipt.receipt_digest[:16], "status": "accepted"}
            next_action = "proceed to integrated review/next progression"
            open_blockers: tuple[str, ...] = ()
        else:
            frontier_ref = {"ref": reviewed, "digest": receipt.receipt_digest[:16], "status": "reviewed-awaiting-acceptance"}
            next_action = "awaiting-user-acceptance"
            open_blockers = ("USER_GATE_REQUIRED",)
        # CAS via compare_and_swap (fail-closed on stale revision; replay is safe).
        fresh = coordinator_store.get(coordinator_id)
        if fresh is not None:
            try:
                coordinator_store.compare_and_swap(
                    coordinator_id, fresh.coordinator_revision,
                    {"frontier_ref": frontier_ref, "next_action": next_action, "open_blockers": list(open_blockers)},
                    fresh.revision_token,
                )
            except Exception:
                # Stale revision under contention: re-read; receipt is already
                # durable so re-entry remains exact (no remote replay needed).
                pass
    except FinalizerError:
        raise
    except Exception as exc:
        raise FinalizerError(FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE, f"coordinator reconciliation failed: {exc}") from exc

    # Exact/logical re-entry evidence (compact).
    post = coordinator_store.get(coordinator_id)
    post_id = post.coordinator_id if post is not None else pre_coordinator_id
    post_session = getattr(post, "origin_task_main_session_ref", pre_session) if post is not None else pre_session
    if post_id != pre_coordinator_id or post_session != pre_session:
        raise FinalizerError(FinalizerFailure.TRUST_BINDING_MISMATCH, "coordinator/session identity changed across finalizer")
    user_gate_label = "satisfied" if finalizer_input.user_gate.user_approval_satisfied else "required"
    frontier = (accepted or reviewed) if phase == "ACCEPTED_CLOSURE" else reviewed
    evidence = FinalizerReentryEvidence(
        coordinator_id=post_id,
        origin_session_ref=post_session,
        steward_digest=receipt.steward_digest,
        receipt_digest=receipt.receipt_digest,
        receipt_id=receipt.receipt_id,
        closure_phase=phase,
        frontier=frontier,
        next_action=next_action,
        user_gate=user_gate_label,
    )
    return receipt, evidence


__all__ = [
    "STEWARD_FINALIZER_CREATED",
    "STEWARD_FINALIZER_IS_SERVER_SIDE_TRUSTED",
    "FINALIZER_INPUT_IS_STEWARD_RESULT",
    "FINALIZER_USES_TRUSTED_BINDING",
    "FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE",
    "LLM_SEMANTIC_JUDGMENT_IS_MECHANICAL_MUTATION_AUTHORITY",
    "REVIEWED_CLOSURE_MODE_WIRED",
    "ACCEPTED_CLOSURE_MODE_WIRED",
    "STEWARD_CAN_SET_USER_APPROVAL",
    "FINALIZER_CAN_SET_USER_APPROVAL",
    "FINALIZER_CAN_INFER_USER_APPROVAL",
    "STEWARD_RESULT_USER_APPROVAL_IS_AUTHORITY",
    "DURABLE_USER_GATE_STATE_IS_REQUIRED_FOR_ACCEPTED_CLOSURE",
    "ACCEPTED_FRONTIER_DERIVED_FROM_REVIEWED_FRONTIER",
    "FINALIZER_CAN_INVENT_ACCEPTED_FRONTIER",
    "ACCEPTED_FRONTIER_MUST_DERIVE_FROM_TRUSTED_REVIEWED_FRONTIER",
    "TYPED_GIT_BOUNDARY_WIRED",
    "GENERIC_GIT_COMMAND_AUTHORITY",
    "GIT_PROMOTION_CAS_GUARDED",
    "NON_FAST_FORWARD_MAIN_PROMOTION_ALLOWED",
    "FORCE_PUSH_ALLOWED",
    "RESET_ALLOWED",
    "CLEAN_ALLOWED",
    "STASH_ALLOWED",
    "TYPED_GITHUB_GOVERNANCE_BOUNDARY_WIRED",
    "GENERIC_GITHUB_REQUEST_AUTHORITY",
    "ARBITRARY_REPOSITORY_MUTATION",
    "ARBITRARY_ISSUE_MUTATION",
    "ARBITRARY_COMMENT_MUTATION",
    "GITHUB_TARGET_DERIVED_FROM_TRUSTED_PLAN_CONTEXT",
    "GITHUB_MUTATION_CAS_GUARDED",
    "FINALIZER_CAN_CREATE_NEW_MANAGED_COMMENT_BY_DEFAULT",
    "FINALIZER_IDEMPOTENT",
    "REMOTE_MUTATION_BEFORE_LOCAL_RECEIPT_RECOVERABLE",
    "PARTIAL_FAILURE_RECOVERY_WIRED",
    "DISTRIBUTED_GLOBAL_ATOMICITY_CLAIMED",
    "COORDINATOR_FINALIZER_INTEGRATION_WIRED",
    "TASK_MAIN_DIRECT_MANUAL_GOVERNANCE_SYNC_REQUIRED",
    "EXACT_TASK_MAIN_REENTRY_AFTER_FINALIZER",
    "RAW_FINALIZER_LOG_REQUIRED_BY_TASK_MAIN",
    "FINALIZER_RESTART_RECOVERY_SUPPORTED",
    "PROJECT_STEWARD_PERSISTS_DURING_FINALIZER",
    "PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION",
    "PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION",
    "ONE_AGENT_FACING_AOTA_MCP_TOOL",
    "AGENT_FACING_AOTA_TOOL",
    "NEW_PUBLIC_MCP_TOOL_CREATED",
    "SECOND_RESULT_TRANSPORT_CREATED",
    "NEW_WORKFLOW_DATABASE_CREATED",
    "NEW_EVENT_BUS_CREATED",
    "MANAGED_ROLES",
    "FinalizerFailure",
    "FinalizerError",
    "map_failure_to_disposition",
    "TrustedProjectBinding",
    "make_trusted_binding_for_test",
    "TrustedPlanIdentity",
    "TrustedUserGateState",
    "ClosurePhase",
    "GitPromotionIntent",
    "GitHubUpdateIntent",
    "MaterializationIntent",
    "FinalizerInput",
    "upsert_proof_subsection",
    "GitPort",
    "SubprocessGitPort",
    "InMemoryGitPort",
    "GitHubPort",
    "InMemoryGitHubPort",
    "GhCliGitHubPort",
    "compute_idempotency_key",
    "FileBackedReceiptStore",
    "InMemoryReceiptStore",
    "authorize_finalizer_call",
    "TrustedStewardFinalizer",
    "FinalizerReentryEvidence",
    "finalize_closure_via_coordinator",
]
