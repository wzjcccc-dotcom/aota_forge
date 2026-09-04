"""Recovery Admission & Current-Governance Reconciliation Contract (S5 M2 W1).

Bounded deterministic pure evaluator for context recovery admission without
rewinding governance, minting authority, retrying work, replaying effects,
or executing rollover.

W1 solves:
  Given a valid SessionCheckpoint and already-observed CurrentGovernedWorkingTruth,
  determine purely and deterministically whether context recovery may be admitted.

Invariants
----------
* W1_CONTRACT_ONLY=yes
* CURRENT_GOVERNED_WORKING_TRUTH_IS_AUTHORITY=no
* RECOVERY_ADMISSION_IS_AUTHORITY=no
* RECOVERY_ADMISSION_IS_RETRY_AUTHORITY=no
* RECOVERY_ADMISSION_EVALUATOR_PURE=yes
* RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY=no
* CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT=yes
* RECOVERY_CAN_REWIND_GOVERNANCE=no
* CROSS_PROJECT_RECOVERY_FAILS_CLOSED=yes
* CROSS_PLAN_RECOVERY_FAILS_CLOSED=yes
* MILESTONE_MISMATCH_DOES_NOT_REWIND_GOVERNANCE=yes
* STALE_ACTIVE_WORK_RESTART_ALLOWED=no
* RECOVERY_PRESERVES_SEMANTIC_STOP=yes
* RECOVERY_PRESERVES_REPLAN_REQUIRED=yes
* TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT=yes
* EXISTING_JOURNAL_RECONCILIATION_REUSED=yes
* EXISTING_RETRY_AUTHORITY_MODEL_REUSED=yes
* No Session entity, no checkpoint store, no ContextProvider integration,
  no selective hydration execution, no RecoveryEngine, no generic framework.
* Agent-neutral, no provider/model/session identifiers.
* Deterministic precedence, bounded vocabulary, strict serialization fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.session_checkpoint import SessionCheckpoint

# ---------------------------------------------------------------------------
# Public flags — authority, scope, boundaries
# ---------------------------------------------------------------------------

W1_CONTRACT_ONLY: bool = True

CURRENT_GOVERNED_WORKING_TRUTH_IS_AUTHORITY: bool = False
RECOVERY_ADMISSION_IS_AUTHORITY: bool = False
RECOVERY_ADMISSION_IS_RETRY_AUTHORITY: bool = False
RECOVERY_ADMISSION_EVALUATOR_PURE: bool = True
RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY: bool = False

CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT: bool = True
RECOVERY_CAN_REWIND_GOVERNANCE: bool = False

CROSS_PROJECT_RECOVERY_FAILS_CLOSED: bool = True
CROSS_PLAN_RECOVERY_FAILS_CLOSED: bool = True
MILESTONE_MISMATCH_DOES_NOT_REWIND_GOVERNANCE: bool = True
STALE_ACTIVE_WORK_RESTART_ALLOWED: bool = False

RECOVERY_PRESERVES_SEMANTIC_STOP: bool = True
RECOVERY_AUTO_CONTINUES_SEMANTIC_STOP: bool = False
RECOVERY_PRESERVES_REPLAN_REQUIRED: bool = True
RECOVERY_AUTO_REPAIRS_REPLAN_REQUIRED: bool = False
RECOVERY_AUTO_CONTINUES_REPLAN_REQUIRED: bool = False

TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT: bool = True

EXISTING_JOURNAL_RECONCILIATION_REUSED: bool = True
NEW_MUTATION_RECONCILIATION_MODEL_CREATED: bool = False
EXISTING_RETRY_AUTHORITY_MODEL_REUSED: bool = True
RECOVERY_SUCCESS_IS_RETRY_PERMISSION: bool = False
RECOVERY_AUTO_REPLAYS_SIDE_EFFECT: bool = False
ROLLOVER_REPLAYS_SIDE_EFFECT: bool = False

ACTIVE_WORK_IDENTITY_RECOVERY_SUPPORTED: bool = True
ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER: bool = False

LOGICAL_ROLLOVER_IMPLEMENTED_BY_W1: bool = False
WORKING_TRUTH_RECONSTRUCTION_IMPLEMENTED_BY_W1: bool = False
NEW_SESSION_ENTITY_CREATED_BY_W1: bool = False
NEW_SESSION_ENTITY_REQUIRED_FOR_M2: bool = False
CHECKPOINT_STORE_CREATED_BY_W1: bool = False
M2_CHECKPOINT_STORAGE_REQUIRED: bool = False
M2_CONTEXT_PROVIDER_INTEGRATION_STARTED: bool = False
M2_SELECTIVE_HYDRATION_EXECUTION_STARTED: bool = False
M2_HYDRATES_CONTEXT_REFS: bool = False
CHECKPOINT_CONTEXT_REF_IS_AUTHORITY: bool = False

NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED: bool = False
NEW_GENERIC_RECOVERY_ENGINE_REQUIRED_FOR_M2: bool = False
EXISTING_SEMANTIC_REFERENCE_REUSED: bool = True
NEW_GOVERNANCE_IDENTITY_ONTOLOGY_CREATED: bool = False
NEW_WORKFLOW_ONTOLOGY_CREATED: bool = False
ARBITRARY_RECOVERY_METADATA_ALLOWED: bool = False
M2_AGENT_NEUTRAL: bool = True
PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False
MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False

RECOVERY_ADMISSION_VOCABULARY_BOUNDED: bool = True
RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER: bool = False
RECOVERY_ESTABLISHES_REVIEWED_FRONTIER: bool = False
RECOVERY_ADVANCES_S4_WORKFLOW: bool = False

CHECKPOINT_INTEGRITY_CONTRACT_REUSED: bool = True
SECOND_CHECKPOINT_INTEGRITY_IMPLEMENTATION_CREATED: bool = False
CHECKPOINT_INTEGRITY_IS_RECOVERY_ADMISSION: bool = False

M1_PREDECESSOR_CONTRACT_REVISION_REQUIRED: bool = False
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
AGGREGATOR_CHANGE_REQUIRED_FOR_W1: bool = False
W2_SCOPE_PULLED_FORWARD_BY_W1: bool = False

# Module count preference
NEW_PRODUCTION_MODULE_COUNT: int = 1
M2_NEW_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX: int = 2

# ---------------------------------------------------------------------------
# Allowed fields — strict fail-closed
# ---------------------------------------------------------------------------

_ALLOWED_CURRENT_FIELDS: frozenset[str] = frozenset({
    "project_ref",
    "plan_ref",
    "milestone_ref",
    "active_work_item_ref",
    "accepted_frontier_ref",
    "reviewed_frontier_ref",
    "workflow_disposition_ref",
    "semantic_stop_present",
    "replan_required",
    "unresolved_effect",
})

_ALLOWED_RECOVERY_ADMISSION_FIELDS: frozenset[str] = frozenset({
    "disposition",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_semantic_ref(value: Any, label: str) -> SemanticReference:
    try:
        return SemanticReference.from_value(value)
    except (TypeError, ValueError) as exc:
        raise type(exc)(f"{label}: {exc}") from exc


def _normalize_optional_ref(value: Any, label: str) -> SemanticReference | None:
    if value is None:
        return None
    return _ensure_semantic_ref(value, label)


def _validate_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


def _refs_equal(a: SemanticReference | None, b: SemanticReference | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.ref == b.ref and a.digest == b.digest


# ---------------------------------------------------------------------------
# CurrentGovernedWorkingTruth
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentGovernedWorkingTruth:
    """Bounded immutable observation of current governed working truth.

    Required core identity:
      project_ref, plan_ref, milestone_ref (non-optional, SemanticReference)

    Optional current observations:
      active_work_item_ref, accepted_frontier_ref, reviewed_frontier_ref,
      workflow_disposition_ref

    Bounded blocking observations (strict bool, not coercible):
      semantic_stop_present, replan_required, unresolved_effect

    This object is already-observed/reconciled input. It does NOT itself
    establish Milestone approval, accepted frontier, reviewed frontier,
    Work Item completion, operation authority, or retry authority.
    Authority remains the underlying governance source.
    No new project/Plan/Milestone/W identity types; reuses SemanticReference.
    Unknown/mechanical/authority fields fail closed; no arbitrary metadata.
    Agent-neutral.
    """

    project_ref: SemanticReference
    plan_ref: SemanticReference
    milestone_ref: SemanticReference
    active_work_item_ref: SemanticReference | None = None
    accepted_frontier_ref: SemanticReference | None = None
    reviewed_frontier_ref: SemanticReference | None = None
    workflow_disposition_ref: SemanticReference | None = None
    semantic_stop_present: bool = False
    replan_required: bool = False
    unresolved_effect: bool = False

    def __post_init__(self) -> None:
        # Required core refs — non-optional, must be SemanticReference
        for label in ("project_ref", "plan_ref", "milestone_ref"):
            val = getattr(self, label)
            if val is None:
                raise ValueError(f"{label} is required (non-optional) in CurrentGovernedWorkingTruth")
            norm = _ensure_semantic_ref(val, label)
            object.__setattr__(self, label, norm)

        # Optional single refs
        for label in (
            "active_work_item_ref",
            "accepted_frontier_ref",
            "reviewed_frontier_ref",
            "workflow_disposition_ref",
        ):
            val = getattr(self, label)
            norm = _normalize_optional_ref(val, label)
            object.__setattr__(self, label, norm)

        # Strict bool validation — no coercible truth values
        for label in ("semantic_stop_present", "replan_required", "unresolved_effect"):
            val = getattr(self, label)
            norm = _validate_strict_bool(val, label)
            object.__setattr__(self, label, norm)

    def canonical_dict(self) -> dict[str, Any]:
        def _ref_dict(r: SemanticReference | None) -> dict[str, Any] | None:
            return r.to_dict() if r is not None else None

        return {
            "accepted_frontier_ref": _ref_dict(self.accepted_frontier_ref),
            "active_work_item_ref": _ref_dict(self.active_work_item_ref),
            "milestone_ref": _ref_dict(self.milestone_ref),
            "plan_ref": _ref_dict(self.plan_ref),
            "project_ref": _ref_dict(self.project_ref),
            "replan_required": self.replan_required,
            "reviewed_frontier_ref": _ref_dict(self.reviewed_frontier_ref),
            "semantic_stop_present": self.semantic_stop_present,
            "unresolved_effect": self.unresolved_effect,
            "workflow_disposition_ref": _ref_dict(self.workflow_disposition_ref),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CurrentGovernedWorkingTruth":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_CURRENT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in CurrentGovernedWorkingTruth: {sorted(extra)}")
        for req in ("project_ref", "plan_ref", "milestone_ref"):
            if req not in data:
                raise ValueError(f"Missing required field in CurrentGovernedWorkingTruth: {req!r}")
            if data[req] is None:
                raise ValueError(f"{req} must not be None in CurrentGovernedWorkingTruth")
        # Ensure booleans present — missing treated as fail-closed? Require explicit.
        # But allow default False if not provided for ergonomic construction via dict?
        # Strict: if not present, default to False is allowed only when constructing via dataclass directly.
        # For from_dict, we require explicit booleans to prevent silent default injection.
        # However spec allows canonical_dict to always emit them, so roundtrip will have them.
        # Enforce that all three booleans must be present in dict.
        for bfield in ("semantic_stop_present", "replan_required", "unresolved_effect"):
            if bfield not in data:
                raise ValueError(f"Missing required field in CurrentGovernedWorkingTruth: {bfield!r}")
        return cls(
            project_ref=data["project_ref"],
            plan_ref=data["plan_ref"],
            milestone_ref=data["milestone_ref"],
            active_work_item_ref=data.get("active_work_item_ref"),
            accepted_frontier_ref=data.get("accepted_frontier_ref"),
            reviewed_frontier_ref=data.get("reviewed_frontier_ref"),
            workflow_disposition_ref=data.get("workflow_disposition_ref"),
            semantic_stop_present=data["semantic_stop_present"],
            replan_required=data["replan_required"],
            unresolved_effect=data["unresolved_effect"],
        )


# ---------------------------------------------------------------------------
# RecoveryAdmissionDisposition
# ---------------------------------------------------------------------------

@unique
class RecoveryAdmissionDisposition(str, Enum):
    RECOVERABLE = "RECOVERABLE"
    STALE_RECONCILIATION_REQUIRED = "STALE_RECONCILIATION_REQUIRED"
    BLOCKED_BY_SCOPE_MISMATCH = "BLOCKED_BY_SCOPE_MISMATCH"
    BLOCKED_BY_SEMANTIC_STOP = "BLOCKED_BY_SEMANTIC_STOP"
    BLOCKED_BY_REPLAN = "BLOCKED_BY_REPLAN"
    BLOCKED_BY_UNRESOLVED_EFFECT = "BLOCKED_BY_UNRESOLVED_EFFECT"


RECOVERY_ADMISSION_DISPOSITIONS: frozenset[str] = frozenset(v.value for v in RecoveryAdmissionDisposition)

# ---------------------------------------------------------------------------
# RecoveryAdmission
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecoveryAdmission:
    """Immutable typed recovery admission result.

    Contains only bounded disposition. No retry, worker dispatch,
    mutation, replay, approval, or acceptance authority fields.
    Not authority, not retry authority.
    Unknown fields fail closed; no arbitrary metadata.
    """

    disposition: RecoveryAdmissionDisposition

    def __post_init__(self) -> None:
        if isinstance(self.disposition, RecoveryAdmissionDisposition):
            return
        if isinstance(self.disposition, str) and type(self.disposition) is str:
            try:
                parsed = RecoveryAdmissionDisposition(self.disposition)
            except ValueError as exc:
                raise ValueError(f"Unknown RecoveryAdmissionDisposition: {self.disposition!r}") from exc
            object.__setattr__(self, "disposition", parsed)
            return
        if isinstance(self.disposition, Enum):
            raise TypeError(
                f"disposition must be RecoveryAdmissionDisposition or valid string, "
                f"got foreign Enum {type(self.disposition).__name__}"
            )
        raise TypeError(f"disposition must be RecoveryAdmissionDisposition or string, got {type(self.disposition).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return {"disposition": self.disposition.value}

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"disposition": self.disposition.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecoveryAdmission":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_RECOVERY_ADMISSION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in RecoveryAdmission: {sorted(extra)}")
        if "disposition" not in data:
            raise ValueError("Missing required field in RecoveryAdmission: 'disposition'")
        return cls(disposition=data["disposition"])


# ---------------------------------------------------------------------------
# Pure evaluator
# ---------------------------------------------------------------------------

def evaluate_recovery_admission(
    checkpoint: SessionCheckpoint,
    current: CurrentGovernedWorkingTruth,
) -> RecoveryAdmission:
    """Pure deterministic evaluator for recovery admission.

    Consumes already-observed checkpoint and current governance truth.
    Does not call remote governance API, filesystem, database, network,
    Git, Tool execution, Worker execution, thread, timer, background loop,
    session creation, or context hydration.

    Deterministic precedence (fail-closed, current governance wins):
      1. project mismatch or Plan mismatch -> BLOCKED_BY_SCOPE_MISMATCH
      2. semantic_stop_present true -> BLOCKED_BY_SEMANTIC_STOP
      3. replan_required true -> BLOCKED_BY_REPLAN
      4. unresolved_effect true -> BLOCKED_BY_UNRESOLVED_EFFECT
      5. Milestone / active W / frontier / workflow disposition differs
         -> STALE_RECONCILIATION_REQUIRED
      6. otherwise -> RECOVERABLE

    Hard requirements preserved:
      scope mismatch never becomes recoverable
      SemanticStop never becomes recoverable
      REPLAN never becomes recoverable
      unresolved effect never becomes recoverable
      stale checkpoint never rewinds current truth
    No authority, no retry permission, no Worker dispatch, no frontier
    establishment, no S4 workflow advancement, no side-effect handling.
    """
    if not isinstance(checkpoint, SessionCheckpoint):
        raise TypeError(f"checkpoint must be SessionCheckpoint, got {type(checkpoint).__name__}")
    if not isinstance(current, CurrentGovernedWorkingTruth):
        raise TypeError(f"current must be CurrentGovernedWorkingTruth, got {type(current).__name__}")

    wt = checkpoint.working_truth

    # 1. Scope mismatch — project or Plan
    if not _refs_equal(wt.project_ref, current.project_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH)
    if not _refs_equal(wt.plan_ref, current.plan_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH)

    # 2-4. Blocking observations — deterministic precedence
    if current.semantic_stop_present is True:
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP)
    if current.replan_required is True:
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.BLOCKED_BY_REPLAN)
    if current.unresolved_effect is True:
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.BLOCKED_BY_UNRESOLVED_EFFECT)

    # 5. Stale / drift — current governance wins, no rewind
    if not _refs_equal(wt.milestone_ref, current.milestone_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED)
    if not _refs_equal(wt.active_work_item_ref, current.active_work_item_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED)
    if not _refs_equal(wt.accepted_frontier_ref, current.accepted_frontier_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED)
    if not _refs_equal(wt.reviewed_frontier_ref, current.reviewed_frontier_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED)
    if not _refs_equal(wt.workflow_disposition_ref, current.workflow_disposition_ref):
        return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED)

    # 6. Recoverable
    return RecoveryAdmission(disposition=RecoveryAdmissionDisposition.RECOVERABLE)


__all__ = [
    # flags
    "W1_CONTRACT_ONLY",
    "CURRENT_GOVERNED_WORKING_TRUTH_IS_AUTHORITY",
    "RECOVERY_ADMISSION_IS_AUTHORITY",
    "RECOVERY_ADMISSION_IS_RETRY_AUTHORITY",
    "RECOVERY_ADMISSION_EVALUATOR_PURE",
    "RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY",
    "CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT",
    "RECOVERY_CAN_REWIND_GOVERNANCE",
    "CROSS_PROJECT_RECOVERY_FAILS_CLOSED",
    "CROSS_PLAN_RECOVERY_FAILS_CLOSED",
    "MILESTONE_MISMATCH_DOES_NOT_REWIND_GOVERNANCE",
    "STALE_ACTIVE_WORK_RESTART_ALLOWED",
    "RECOVERY_PRESERVES_SEMANTIC_STOP",
    "RECOVERY_AUTO_CONTINUES_SEMANTIC_STOP",
    "RECOVERY_PRESERVES_REPLAN_REQUIRED",
    "RECOVERY_AUTO_REPAIRS_REPLAN_REQUIRED",
    "RECOVERY_AUTO_CONTINUES_REPLAN_REQUIRED",
    "TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT",
    "EXISTING_JOURNAL_RECONCILIATION_REUSED",
    "NEW_MUTATION_RECONCILIATION_MODEL_CREATED",
    "EXISTING_RETRY_AUTHORITY_MODEL_REUSED",
    "RECOVERY_SUCCESS_IS_RETRY_PERMISSION",
    "RECOVERY_AUTO_REPLAYS_SIDE_EFFECT",
    "ROLLOVER_REPLAYS_SIDE_EFFECT",
    "ACTIVE_WORK_IDENTITY_RECOVERY_SUPPORTED",
    "ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER",
    "LOGICAL_ROLLOVER_IMPLEMENTED_BY_W1",
    "WORKING_TRUTH_RECONSTRUCTION_IMPLEMENTED_BY_W1",
    "NEW_SESSION_ENTITY_CREATED_BY_W1",
    "NEW_SESSION_ENTITY_REQUIRED_FOR_M2",
    "CHECKPOINT_STORE_CREATED_BY_W1",
    "M2_CHECKPOINT_STORAGE_REQUIRED",
    "M2_CONTEXT_PROVIDER_INTEGRATION_STARTED",
    "M2_SELECTIVE_HYDRATION_EXECUTION_STARTED",
    "M2_HYDRATES_CONTEXT_REFS",
    "CHECKPOINT_CONTEXT_REF_IS_AUTHORITY",
    "NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED",
    "NEW_GENERIC_RECOVERY_ENGINE_REQUIRED_FOR_M2",
    "EXISTING_SEMANTIC_REFERENCE_REUSED",
    "NEW_GOVERNANCE_IDENTITY_ONTOLOGY_CREATED",
    "NEW_WORKFLOW_ONTOLOGY_CREATED",
    "ARBITRARY_RECOVERY_METADATA_ALLOWED",
    "M2_AGENT_NEUTRAL",
    "PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "RECOVERY_ADMISSION_VOCABULARY_BOUNDED",
    "RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER",
    "RECOVERY_ESTABLISHES_REVIEWED_FRONTIER",
    "RECOVERY_ADVANCES_S4_WORKFLOW",
    "CHECKPOINT_INTEGRITY_CONTRACT_REUSED",
    "SECOND_CHECKPOINT_INTEGRITY_IMPLEMENTATION_CREATED",
    "CHECKPOINT_INTEGRITY_IS_RECOVERY_ADMISSION",
    "M1_PREDECESSOR_CONTRACT_REVISION_REQUIRED",
    "SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "AGGREGATOR_CHANGE_REQUIRED_FOR_W1",
    "W2_SCOPE_PULLED_FORWARD_BY_W1",
    "NEW_PRODUCTION_MODULE_COUNT",
    "M2_NEW_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX",
    # types
    "CurrentGovernedWorkingTruth",
    "RecoveryAdmissionDisposition",
    "RECOVERY_ADMISSION_DISPOSITIONS",
    "RecoveryAdmission",
    "evaluate_recovery_admission",
]
