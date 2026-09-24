"""Bounded review-only adoption of an already authorized Git frontier.

This is a transitional runtime seam for a Milestone whose historical Work Item
execution is outside the current coordinator.  It deliberately records only
the existing coordinator's review/frontier working fields.  It never invents
Work Item receipts, Work Item status, or execution history.

The caller is trusted runtime composition, not an agent-facing operation.  The
authorization facts and the current Git observation must agree exactly before
the existing integrated-review dispatch path may run.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.core.git.inspect import inspect_git
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    PlanDriftError,
    TaskMainCoordinatorError,
    _check_live_binding,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    StaleCoordinatorRevisionError,
    TaskMainCoordinatorStore,
)
from aota_forge.work_plane.progression import (
    ProgressionDisposition,
)
from aota_forge.work_plane.steward_finalizer import GitPort, SubprocessGitPort


REVIEW_ONLY_ADOPTION_ENABLED = True
REVIEW_ONLY_ADOPTION_STATUS = "REVIEW_ONLY_ADOPTION_READY"
REVIEW_ONLY_ADOPTION_STATE = "accepted_frontier_review_only"
REVIEW_ONLY_FRONTIER_STATUS = "REVIEW_ONLY_CANDIDATE"
REVIEW_ONLY_GATE_STATUS = "REVIEW_ONLY_ADOPTION_READY"

NO_FAKE_WORK_COMPLETION_RECEIPTS = True
NO_FAKE_WORK_COORDINATOR_STATE = True
NO_WORK_REDISPATCH_DURING_ADOPTION = True
NO_NEW_REVIEW_ADOPTION_PROTOCOL = True
NO_PUBLIC_REVIEW_ADOPTION_OPERATION = True

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_REF_LENGTH = 512
_EXPECTED_REVIEWER_ATTEMPT = 1


class ReviewAdoptionError(TaskMainCoordinatorError):
    """Fail-closed refusal to establish or consume review-only adoption."""


def _require_non_empty_str(value: Any, label: str, *, max_length: int = _MAX_REF_LENGTH) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    if "\x00" in stripped:
        raise ValueError(f"{label} must not contain NUL")
    return stripped


def _require_commit(value: Any, label: str) -> str:
    normalized = _require_non_empty_str(value, label, max_length=40).lower()
    if not _SHA1_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a full lowercase Git commit SHA")
    return normalized


def _require_sha256(value: Any, label: str) -> str:
    normalized = _require_non_empty_str(value, label, max_length=64).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a full lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class AcceptedFrontierAuthorization:
    """Trusted exact authorization for one review-only adoption.

    This value is minted by trusted composition from the current governed
    Plan/projection.  It is not accepted from model input or from a review
    result.
    """

    project_id: str
    plan_id: str
    plan_authority: str
    authority_source_kind: str
    plan_digest: str
    plan_source_revision: str
    milestone_id: str
    entry_base: str
    candidate_frontier_ref: str
    authorization_ref: str
    reviewer_canonical_task_id: str

    def __post_init__(self) -> None:
        for label in (
            "project_id",
            "plan_id",
            "plan_authority",
            "authority_source_kind",
            "plan_digest",
            "plan_source_revision",
            "milestone_id",
            "authorization_ref",
            "reviewer_canonical_task_id",
        ):
            object.__setattr__(self, label, _require_non_empty_str(getattr(self, label), label))
        object.__setattr__(self, "entry_base", _require_commit(self.entry_base, "entry_base"))
        object.__setattr__(
            self,
            "candidate_frontier_ref",
            _require_commit(self.candidate_frontier_ref, "candidate_frontier_ref"),
        )

    @property
    def frontier_ref(self) -> str:
        """Compatibility name for the authorized candidate frontier."""

        return self.candidate_frontier_ref


@dataclass(frozen=True)
class TrustedGitFrontier:
    """Read-only observation of the trusted current worktree frontier."""

    worktree_root: str
    entry_base: str
    frontier_ref: str
    tree_ref: str
    clean: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "worktree_root", _require_non_empty_str(self.worktree_root, "worktree_root"))
        object.__setattr__(self, "entry_base", _require_commit(self.entry_base, "entry_base"))
        object.__setattr__(self, "frontier_ref", _require_commit(self.frontier_ref, "frontier_ref"))
        object.__setattr__(self, "tree_ref", _require_commit(self.tree_ref, "tree_ref"))
        if type(self.clean) is not bool:
            raise TypeError("clean must be bool")
        if not self.clean:
            raise ValueError("trusted Git frontier must be clean")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "entry_base": self.entry_base,
            "frontier_ref": self.frontier_ref,
            "tree_ref": self.tree_ref,
            "worktree_root": self.worktree_root,
        }


@dataclass(frozen=True)
class ReviewOnlyAdoption:
    """Validated marker reconstructed from existing coordinator fields."""

    authorization_ref: str
    frontier_ref: str
    reviewer_canonical_task_id: str
    proof_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_ref", _require_non_empty_str(self.authorization_ref, "authorization_ref"))
        object.__setattr__(self, "frontier_ref", _require_commit(self.frontier_ref, "frontier_ref"))
        object.__setattr__(
            self,
            "reviewer_canonical_task_id",
            _require_non_empty_str(self.reviewer_canonical_task_id, "reviewer_canonical_task_id"),
        )
        object.__setattr__(self, "proof_digest", _require_sha256(self.proof_digest, "proof_digest"))


def read_trusted_git_frontier(
    worktree_root: str | Path,
    *,
    entry_base: str,
    git: GitPort | None = None,
) -> TrustedGitFrontier:
    """Observe a clean canonical Git HEAD and verify ``entry_base`` ancestry."""

    if isinstance(worktree_root, str):
        root = Path(worktree_root)
    elif isinstance(worktree_root, Path):
        root = worktree_root
    else:
        raise TypeError(f"worktree_root must be str or Path, got {type(worktree_root).__name__}")
    if root.is_symlink():
        raise ReviewAdoptionError("trusted worktree root must not be a symlink")
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ReviewAdoptionError(f"trusted worktree cannot be resolved: {exc}") from exc
    if not root.is_dir():
        raise ReviewAdoptionError("trusted worktree root must be a canonical directory")

    entry = _require_commit(entry_base, "entry_base")
    try:
        observation = inspect_git(root, boundary=root)
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted Git inspection failed: {exc}") from exc
    if observation.get("available") is not True:
        raise ReviewAdoptionError("trusted Git source is unavailable")
    if observation.get("is_dirty") is not False:
        raise ReviewAdoptionError("trusted Git source is dirty")
    head = observation.get("head_sha")
    try:
        head = _require_commit(head, "trusted Git HEAD")
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc

    port = git or SubprocessGitPort()
    if not isinstance(port, GitPort):
        raise TypeError(f"git must be GitPort or None, got {type(port).__name__}")
    try:
        if not port.is_ancestor(root, entry, head):
            raise ReviewAdoptionError(
                f"trusted Git HEAD {head!r} is not a descendant of entry_base {entry!r}"
            )
        tree = port.resolve_tree(root, head)
    except ReviewAdoptionError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted Git ancestry/tree verification failed: {exc}") from exc
    try:
        tree = _require_commit(tree, "trusted Git tree")
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    return TrustedGitFrontier(
        worktree_root=str(root),
        entry_base=entry,
        frontier_ref=head,
        tree_ref=tree,
        clean=True,
    )


def _proof_digest(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    payload = {
        "authorization": {
            "authorization_ref": authorization.authorization_ref,
            "authority_source_kind": authorization.authority_source_kind,
            "candidate_frontier_ref": authorization.candidate_frontier_ref,
            "entry_base": authorization.entry_base,
            "milestone_id": authorization.milestone_id,
            "plan_authority": authorization.plan_authority,
            "plan_digest": authorization.plan_digest,
            "plan_id": authorization.plan_id,
            "plan_source_revision": authorization.plan_source_revision,
            "project_id": authorization.project_id,
            "reviewer_canonical_task_id": authorization.reviewer_canonical_task_id,
        },
        "git": frontier.canonical_dict(),
    }
    return hashlib.sha256(
        canonical_json(canonicalize(payload, path="ReviewOnlyAdoptionProof")).encode("utf-8")
    ).hexdigest()


def _expected_reviewer_task_id(project_id: str, milestone_id: str) -> str:
    return f"{project_id}:{milestone_id}:RV1:attempt-{_EXPECTED_REVIEWER_ATTEMPT}"


def _validate_authorization(
    *,
    state: TaskMainCoordinatorState,
    view: MilestonePlanView,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> None:
    if authorization.authority_source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise ReviewAdoptionError("review-only adoption requires local_governance authority")
    try:
        expected_authority = local_plan_authority_reference(
            authorization.project_id, authorization.plan_id
        )
    except Exception as exc:
        raise ReviewAdoptionError(f"authorization Plan identity is invalid: {exc}") from exc
    if authorization.plan_authority != expected_authority:
        raise ReviewAdoptionError("authorization Plan authority is not the trusted local_governance authority")
    checks = (
        (authorization.project_id, state.project_id, "project identity"),
        (authorization.plan_authority, state.plan_authority, "Plan authority"),
        (authorization.plan_authority, view.plan_authority, "live Plan authority"),
        (authorization.plan_digest, state.plan_digest, "Plan digest"),
        (authorization.plan_digest, view.plan_digest, "live Plan digest"),
        (authorization.plan_source_revision, state.plan_source_revision, "Plan source revision"),
        (authorization.plan_source_revision, view.plan_source_revision, "live Plan source revision"),
        (authorization.milestone_id, state.milestone_id, "Milestone"),
        (authorization.milestone_id, view.milestone_id, "live Milestone"),
        (authorization.entry_base, state.entry_base, "entry base"),
        (authorization.entry_base, view.entry_base, "live entry base"),
        (authorization.entry_base, frontier.entry_base, "observed entry base"),
        (authorization.candidate_frontier_ref, frontier.frontier_ref, "candidate frontier"),
        (
            authorization.reviewer_canonical_task_id,
            _expected_reviewer_task_id(state.project_id, state.milestone_id),
            "reviewer task identity",
        ),
    )
    for supplied, expected, label in checks:
        if supplied != expected:
            raise ReviewAdoptionError(
                f"{label} mismatch: authorized {supplied!r}, trusted {expected!r}"
            )
    if not frontier.clean:
        raise ReviewAdoptionError("trusted Git frontier is not clean")


def _require_no_active_execution(execution_store: ExecutionStateStore) -> None:
    for record in execution_store.list_all():
        if not record.canonical_task_state.is_terminal:
            raise ReviewAdoptionError(
                f"active execution {record.canonical_task_id!r} blocks review-only adoption"
            )


def _require_no_conflicting_active_execution(
    execution_store: ExecutionStateStore,
    allowed_reviewer_task_id: str,
) -> None:
    for record in execution_store.list_all():
        if (
            not record.canonical_task_state.is_terminal
            and record.canonical_task_id != allowed_reviewer_task_id
        ):
            raise ReviewAdoptionError(
                f"active execution {record.canonical_task_id!r} conflicts with review-only adoption"
            )


def _adoption_updates(
    *,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
    proof_digest: str,
) -> dict[str, dict[str, Any]]:
    return {
        "review_gates": {
            "ref": authorization.authorization_ref,
            "digest": proof_digest,
            "freshness": "trusted_git_clean_descendant",
            "status": REVIEW_ONLY_GATE_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
        "integrated_review": {
            "ref": authorization.reviewer_canonical_task_id,
            "digest": proof_digest,
            "freshness": "exact_authorized_frontier",
            "status": REVIEW_ONLY_ADOPTION_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
        "frontier_ref": {
            "ref": authorization.candidate_frontier_ref,
            "digest": proof_digest,
            "freshness": "trusted_git_clean_descendant",
            "status": REVIEW_ONLY_FRONTIER_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
    }


def _existing_exact_adoption(
    state: TaskMainCoordinatorState,
    updates: Mapping[str, Mapping[str, Any]],
) -> bool:
    return all(getattr(state, field) == dict(value) for field, value in updates.items())


def adopt_accepted_frontier_for_review(
    *,
    store: TaskMainCoordinatorStore,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    authorization: AcceptedFrontierAuthorization,
    worktree_root: str | Path,
    git: GitPort | None = None,
) -> TaskMainCoordinatorState:
    """CAS-adopt an exact clean Git frontier for the existing RV1 path.

    Only the coordinator's existing review/frontier working fields are
    modified.  A repeated identical call returns the durable state unchanged;
    a conflicting marker or any execution/progression drift fails closed.
    """

    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(execution_store, ExecutionStateStore):
        raise TypeError(
            f"execution_store must be ExecutionStateStore, got {type(execution_store).__name__}"
        )
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(
            f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}"
        )
    if not isinstance(authorization, AcceptedFrontierAuthorization):
        raise TypeError(
            "authorization must be AcceptedFrontierAuthorization, "
            f"got {type(authorization).__name__}"
        )
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    state = store.get(coordinator_id)
    if state is None:
        raise ReviewAdoptionError(f"coordinator {coordinator_id!r} does not exist")
    try:
        _check_live_binding(state, live_plan_view)
    except PlanDriftError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"live Plan binding failed closed: {exc}") from exc
    if state.status != CoordinatorStatus.ACTIVE:
        raise ReviewAdoptionError(f"coordinator status {state.status.value!r} blocks adoption")
    if not state.user_approval_satisfied or live_plan_view.user_gate_blocked:
        raise ReviewAdoptionError("Milestone approval or live user gate blocks adoption")
    if state.human_brake is not None and state.human_brake.get("state") != "NONE":
        raise ReviewAdoptionError("Human Brake blocks adoption")

    if any(status != WorkItemCoordinatorStatus.PENDING.value for status in state.wi_status.values()):
        raise ReviewAdoptionError("existing Work Item progression blocks review-only adoption")
    if state.bindings or state.wi_semantic_status:
        raise ReviewAdoptionError("existing Work Item execution/reconciliation truth blocks adoption")
    if state.work_projections:
        raise ReviewAdoptionError("existing Work Item working truth blocks adoption")

    frontier = read_trusted_git_frontier(
        worktree_root,
        entry_base=authorization.entry_base,
        git=git,
    )
    _validate_authorization(
        state=state,
        view=live_plan_view,
        authorization=authorization,
        frontier=frontier,
    )
    proof_digest = _proof_digest(authorization, frontier)
    updates = _adoption_updates(
        authorization=authorization,
        frontier=frontier,
        proof_digest=proof_digest,
    )
    if state.integrated_review is not None or state.review_gates is not None or state.frontier_ref is not None:
        if _existing_exact_adoption(state, updates):
            _require_no_conflicting_active_execution(
                execution_store,
                authorization.reviewer_canonical_task_id,
            )
            for payload in state.reconciled_completions.values():
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("completion_kind") != "MILESTONE_REVIEW"
                    or payload.get("canonical_task_id") != authorization.reviewer_canonical_task_id
                ):
                    raise ReviewAdoptionError(
                        "review-only adoption contains non-review completion truth"
                    )
            return state
        raise ReviewAdoptionError("conflicting coordinator review/frontier state blocks adoption")

    if state.reconciled_completions or state.working_truth is not None:
        raise ReviewAdoptionError("existing Work Item working truth blocks adoption")

    _require_no_active_execution(execution_store)
    try:
        return store.compare_and_swap(
            coordinator_id,
            state.coordinator_revision,
            updates,
            state.revision_token,
        )
    except StaleCoordinatorRevisionError:
        refreshed = store.get(coordinator_id)
        if refreshed is not None and _existing_exact_adoption(refreshed, updates):
            return refreshed
        raise ReviewAdoptionError("review-only adoption CAS lost a conflicting race") from None
    except Exception as exc:
        raise ReviewAdoptionError(f"review-only adoption CAS failed: {exc}") from exc


def review_only_adoption_from_state(
    state: TaskMainCoordinatorState,
) -> ReviewOnlyAdoption | None:
    """Parse and cross-check the existing optional adoption marker."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    fields = (state.review_gates, state.integrated_review, state.frontier_ref)
    if all(value is None for value in fields):
        return None
    if any(not isinstance(value, Mapping) for value in fields):
        raise ReviewAdoptionError("review-only adoption marker is malformed")
    gates, review, frontier = fields
    assert gates is not None and review is not None and frontier is not None
    expected_state = REVIEW_ONLY_ADOPTION_STATE
    if gates.get("status") != REVIEW_ONLY_GATE_STATUS or review.get("status") != REVIEW_ONLY_ADOPTION_STATUS:
        raise ReviewAdoptionError("coordinator review marker is not a review-only adoption")
    if frontier.get("status") != REVIEW_ONLY_FRONTIER_STATUS:
        raise ReviewAdoptionError("coordinator frontier marker is not review-only")
    if any(
        value.get("integrated_state") != expected_state or value.get("required") is not True
        for value in (gates, review, frontier)
    ):
        raise ReviewAdoptionError("review-only adoption marker has conflicting state")
    if gates.get("freshness") != "trusted_git_clean_descendant":
        raise ReviewAdoptionError("review-only gate marker has invalid freshness")
    if review.get("freshness") != "exact_authorized_frontier":
        raise ReviewAdoptionError("review-only reviewer marker has invalid freshness")
    if frontier.get("freshness") != "trusted_git_clean_descendant":
        raise ReviewAdoptionError("review-only frontier marker has invalid freshness")
    refs = (gates.get("ref"), review.get("ref"), frontier.get("ref"))
    if any(not isinstance(value, str) or not value.strip() for value in refs):
        raise ReviewAdoptionError("review-only adoption marker has missing refs")
    digests = (gates.get("digest"), review.get("digest"), frontier.get("digest"))
    if any(not isinstance(value, str) or not value.strip() for value in digests):
        raise ReviewAdoptionError("review-only adoption marker has missing proof digest")
    if len(set(digests)) != 1 or not _SHA256_RE.fullmatch(digests[0]):
        raise ReviewAdoptionError("review-only adoption marker proof digests conflict")
    adoption = ReviewOnlyAdoption(
        authorization_ref=refs[0],
        reviewer_canonical_task_id=refs[1],
        frontier_ref=refs[2],
        proof_digest=digests[0],
    )
    if adoption.reviewer_canonical_task_id != _expected_reviewer_task_id(
        state.project_id, state.milestone_id
    ):
        raise ReviewAdoptionError("review-only marker reviewer identity is not canonical")
    return adoption


def review_only_progression_for(
    state: TaskMainCoordinatorState,
    adoption: ReviewOnlyAdoption,
) -> ProgressionDisposition:
    """Build a transient closure input without persisting Work completion."""

    if not isinstance(adoption, ReviewOnlyAdoption):
        raise TypeError("adoption must be ReviewOnlyAdoption")
    refs = tuple(sorted(state.work_items))
    return ProgressionDisposition(
        progression_complete_work_item_refs=refs,
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        milestone_review_ready=True,
        auto_progression_allowed=False,
        reasons=(
            "review-only frontier adoption supplies no Work Item history; "
            "completion is not persisted",
        ),
        evidence_refs=(adoption.proof_digest,),
    )


__all__ = [
    "NO_FAKE_WORK_COMPLETION_RECEIPTS",
    "NO_FAKE_WORK_COORDINATOR_STATE",
    "NO_NEW_REVIEW_ADOPTION_PROTOCOL",
    "NO_PUBLIC_REVIEW_ADOPTION_OPERATION",
    "NO_WORK_REDISPATCH_DURING_ADOPTION",
    "REVIEW_ONLY_ADOPTION_ENABLED",
    "REVIEW_ONLY_ADOPTION_STATE",
    "REVIEW_ONLY_ADOPTION_STATUS",
    "REVIEW_ONLY_FRONTIER_STATUS",
    "AcceptedFrontierAuthorization",
    "ReviewAdoptionError",
    "ReviewOnlyAdoption",
    "TrustedGitFrontier",
    "adopt_accepted_frontier_for_review",
    "read_trusted_git_frontier",
    "review_only_adoption_from_state",
    "review_only_progression_for",
]
