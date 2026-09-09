"""Conditional ProjectState refresh / reuse lifecycle (M2/W2 runtime).

M1 frozen (see #44 M1/W1):

    PROJECT_STATE_REFRESH_IS_CONDITIONAL=yes
    PROJECT_STATE_REFRESH_EVERY_MILESTONE=no
    PROJECT_STATE_REUSE_ALLOWED=yes

Refresh conditions (any one triggers a bounded Steward Mode A dispatch):

    new task-main logical session, new project, project identity
    ambiguity, missing prior project-state/closure evidence, stale
    evidence, external repository state changed, material continuity
    uncertainty, trusted binding unresolved.

When prior ProjectState/closure evidence is fresh and sufficient: reuse.
Do NOT unconditionally dispatch Steward at every Milestone.

Planning JOIN (M1/W2 frozen sequence):

    task-main preliminary decomposition/risk
            ||
    conditional ProjectState inspection
            ↓
          JOIN
            ↓
    final DAG / risk / review gates

    WORKER_DISPATCH_BEFORE_REQUIRED_PROJECT_STATE=no, but when fresh
    ProjectState is reusable NEW_STEWARD_DISPATCH_REQUIRED=no.

Uses the W1 durable project_state_ref foundation (coordinator
set_project_state); no second store is created.

Hard:

    PROJECT_STATE_CONDITIONAL_REFRESH_WIRED=yes
    PROJECT_STATE_REUSE_WIRED=yes
    DUPLICATE_STEWARD_STATE_INSPECTION_REQUIRED=no
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

PROJECT_STATE_CONDITIONAL_REFRESH_WIRED = True
PROJECT_STATE_REUSE_WIRED = True
DUPLICATE_STEWARD_STATE_INSPECTION_REQUIRED = False
PROJECT_STATE_REFRESH_EVERY_MILESTONE = False
NEW_PROJECT_STATE_STORE_CREATED = False
WORKER_DISPATCH_BEFORE_REQUIRED_PROJECT_STATE = False


@unique
class ProjectStateRefreshReason(str, Enum):
    """Bounded refresh triggers (frozen M1 vocabulary)."""

    NEW_LOGICAL_SESSION = "NEW_LOGICAL_SESSION"
    NEW_PROJECT = "NEW_PROJECT"
    IDENTITY_AMBIGUITY = "IDENTITY_AMBIGUITY"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    EXTERNAL_STATE_CHANGED = "EXTERNAL_STATE_CHANGED"
    CONTINUITY_UNCERTAINTY = "CONTINUITY_UNCERTAINTY"
    BINDING_UNRESOLVED = "BINDING_UNRESOLVED"


@unique
class ProjectStateDisposition(str, Enum):
    REFRESH = "REFRESH"
    REUSE = "REUSE"


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class ProjectStateEvaluation:
    """Deterministic outcome of the conditional refresh policy."""

    disposition: ProjectStateDisposition
    reason: ProjectStateRefreshReason | None
    prior_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ProjectStateDisposition):
            raise TypeError(f"disposition must be ProjectStateDisposition, got {type(self.disposition).__name__}")
        if self.disposition is ProjectStateDisposition.REFRESH:
            if not isinstance(self.reason, ProjectStateRefreshReason):
                raise ValueError("REFRESH disposition requires a refresh reason")
        else:
            if self.reason is not None:
                raise ValueError("REUSE disposition must not carry a refresh reason")


@dataclass(frozen=True)
class ProjectStateFreshnessInput:
    """Typed input to the refresh/reuse evaluator.

    All flags are caller-observed trusted signals (coordinator durable
    project_state + trusted binding evidence). No LLM judgment, no string
    policy heuristics: each flag maps 1:1 to a frozen refresh condition.
    """

    has_prior_ref: bool = False
    prior_sufficient: bool = False
    new_logical_session: bool = False
    new_project: bool = False
    identity_ambiguous: bool = False
    evidence_stale: bool = False
    external_state_changed: bool = False
    continuity_uncertain: bool = False
    binding_unresolved: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "has_prior_ref",
            "prior_sufficient",
            "new_logical_session",
            "new_project",
            "identity_ambiguous",
            "evidence_stale",
            "external_state_changed",
            "continuity_uncertain",
            "binding_unresolved",
        ):
            _require_strict_bool(getattr(self, field_name), field_name)


def evaluate_project_state_need(
    observed: ProjectStateFreshnessInput,
    *,
    prior_ref: str | None = None,
) -> ProjectStateEvaluation:
    """Pure conditional refresh/reuse decision (no dispatch, no store).

    Priority order follows the frozen condition list; the first matching
    condition wins deterministically. Fresh + sufficient prior evidence
    reuses without any Steward dispatch.
    """
    if not isinstance(observed, ProjectStateFreshnessInput):
        raise TypeError(f"observed must be ProjectStateFreshnessInput, got {type(observed).__name__}")
    if prior_ref is not None and (not isinstance(prior_ref, str) or not prior_ref.strip()):
        raise ValueError("prior_ref must be a non-empty string when provided")

    if not observed.has_prior_ref or prior_ref is None:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.MISSING_EVIDENCE,
        )
    if observed.new_logical_session:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.NEW_LOGICAL_SESSION,
            prior_ref=prior_ref,
        )
    if observed.new_project:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.NEW_PROJECT,
            prior_ref=prior_ref,
        )
    if observed.identity_ambiguous:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.IDENTITY_AMBIGUITY,
            prior_ref=prior_ref,
        )
    if observed.evidence_stale:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.STALE_EVIDENCE,
            prior_ref=prior_ref,
        )
    if observed.external_state_changed:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.EXTERNAL_STATE_CHANGED,
            prior_ref=prior_ref,
        )
    if observed.continuity_uncertain:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.CONTINUITY_UNCERTAINTY,
            prior_ref=prior_ref,
        )
    if observed.binding_unresolved:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REFRESH,
            reason=ProjectStateRefreshReason.BINDING_UNRESOLVED,
            prior_ref=prior_ref,
        )
    if observed.prior_sufficient:
        return ProjectStateEvaluation(
            disposition=ProjectStateDisposition.REUSE,
            reason=None,
            prior_ref=prior_ref,
        )
    # Prior exists but is neither fresh/sufficient nor explicitly stale:
    # fail toward a bounded refresh rather than guessing on weak evidence.
    return ProjectStateEvaluation(
        disposition=ProjectStateDisposition.REFRESH,
        reason=ProjectStateRefreshReason.STALE_EVIDENCE,
        prior_ref=prior_ref,
    )


@dataclass(frozen=True)
class PlanningJoinGate:
    """JOIN gate for final DAG / risk / review gates.

    Preliminary decomposition and risk analysis may parallelize with the
    conditional ProjectState inspection, but the final DAG, final risk
    matrix and Worker dispatch require the required ProjectState:

        FINAL_DAG_BEFORE_REQUIRED_PROJECT_STATE=no
        WORKER_DISPATCH_BEFORE_REQUIRED_PROJECT_STATE=no
    """

    preliminary_planning_done: bool
    required_project_state_satisfied: bool

    def __post_init__(self) -> None:
        _require_strict_bool(self.preliminary_planning_done, "preliminary_planning_done")
        _require_strict_bool(self.required_project_state_satisfied, "required_project_state_satisfied")

    @property
    def final_dag_allowed(self) -> bool:
        return self.preliminary_planning_done and self.required_project_state_satisfied

    @property
    def worker_dispatch_allowed(self) -> bool:
        return self.final_dag_allowed

    def assert_final_dag_allowed(self) -> None:
        if not self.final_dag_allowed:
            raise ValueError(
                "final DAG requires preliminary planning AND required ProjectState "
                "(preliminary work may parallelize state inspection, final DAG may not precede it)"
            )

    def assert_worker_dispatch_allowed(self) -> None:
        if not self.worker_dispatch_allowed:
            raise ValueError("Worker dispatch before required ProjectState is denied")


def project_state_input_from_coordinator_state(state: Mapping[str, Any]) -> ProjectStateFreshnessInput:
    """Derive the evaluator input from W1 durable coordinator project_state.

    A present durable ref with freshness == "fresh" and status ==
    "sufficient" reuses; anything else (missing, stale, ambiguous) refreshes.
    The coordinator mapping shape follows TaskMainCoordinatorState
    project_state (ref/digest/freshness/status).
    """
    if not isinstance(state, Mapping):
        raise TypeError(f"state must be a mapping, got {type(state).__name__}")
    ps = state.get("project_state")
    if not isinstance(ps, Mapping) or not ps.get("ref"):
        return ProjectStateFreshnessInput(has_prior_ref=False)
    freshness = ps.get("freshness")
    status = ps.get("status")
    sufficient = freshness == "fresh" and status == "sufficient"
    stale = freshness == "stale"
    return ProjectStateFreshnessInput(
        has_prior_ref=True,
        prior_sufficient=sufficient,
        evidence_stale=stale,
    )


__all__ = [
    "PROJECT_STATE_CONDITIONAL_REFRESH_WIRED",
    "PROJECT_STATE_REUSE_WIRED",
    "DUPLICATE_STEWARD_STATE_INSPECTION_REQUIRED",
    "PROJECT_STATE_REFRESH_EVERY_MILESTONE",
    "NEW_PROJECT_STATE_STORE_CREATED",
    "WORKER_DISPATCH_BEFORE_REQUIRED_PROJECT_STATE",
    "ProjectStateRefreshReason",
    "ProjectStateDisposition",
    "ProjectStateEvaluation",
    "ProjectStateFreshnessInput",
    "PlanningJoinGate",
    "evaluate_project_state_need",
    "project_state_input_from_coordinator_state",
]
