"""Logical Rollover & Working-Truth Reconstruction (S5 M2 W2).

Bounded deterministic logical rollover seam that reconstructs task-main
working truth with current governance precedence without rewinding governance,
dispatching workers, retrying operations, replaying side effects,
hydrating context, or creating a physical session runtime.

W2 solves:
  Given an accepted SessionCheckpoint (M1) and already-observed
  CurrentGovernedWorkingTruth (W1), deterministically produce a bounded
  LogicalRolloverResult that either blocks or reconstructs a
  WorkingTruthProjection using current precedence and bounded passive
  continuity refs.

Invariants
----------
* Reuses W1 RecoveryAdmission contract and evaluator
* Reuses M1 SessionCheckpoint / WorkingTruthProjection / SemanticReference
* Current governance wins over checkpoint, no rewind
* Scope mismatch / stop / replan / unresolved effect remain blocking
* Stale same-project/Plan checkpoint can be reconciled forward to current
* Passive result/evidence/context refs carried only when scope compatible,
  remain non-authoritative, not hydrated, not replayed
* Deterministic, bounded, strict serialization fail-closed
* No session entity, no store, no generic framework, no journal/retry,
  no hydration, no physical session creation, agent neutral
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.recovery_admission import (
    CurrentGovernedWorkingTruth,
    RecoveryAdmission,
    RecoveryAdmissionDisposition,
    evaluate_recovery_admission,
)
from aota_forge.work_plane.session_checkpoint import SessionCheckpoint, WorkingTruthProjection

# ---------------------------------------------------------------------------
# Public flags — authority, scope, boundaries (reporting aid)
# ---------------------------------------------------------------------------

W2_CONTRACT_ONLY: bool = True

LOGICAL_ROLLOVER_RESULT_IS_AUTHORITY: bool = False
LOGICAL_ROLLOVER_IS_PHYSICAL_SESSION_CREATION: bool = False
PHYSICAL_MODEL_SESSION_CREATION_IS_FORGE_AUTHORITY: bool = False

LOGICAL_ROLLOVER_DETERMINISTIC: bool = True
BLOCKED_RECOVERY_PRODUCES_CONTINUATION: bool = False

RECOVERABLE_RECONSTRUCTION_SUPPORTED: bool = True
STALE_CHECKPOINT_CAN_BE_RECONCILED_TO_CURRENT_TRUTH: bool = True
STALE_CHECKPOINT_CAN_REWIND_CURRENT_TRUTH: bool = False
CURRENT_STATE_FIELDS_OVERRIDE_STALE_CHECKPOINT: bool = True

CROSS_PROJECT_ROLLOVER_CONTINUATION_ALLOWED: bool = False
CROSS_PLAN_ROLLOVER_CONTINUATION_ALLOWED: bool = False

M2_RECOVERS_RESULT_REFS: bool = True
M2_RECOVERS_EVIDENCE_REFS: bool = True
M2_RECOVERS_CONTEXT_REFS: bool = True
RECOVERED_RESULT_REF_IS_AUTHORITY: bool = False
RECOVERED_EVIDENCE_REF_IS_AUTHORITY: bool = False
RECOVERED_CONTEXT_REF_IS_AUTHORITY: bool = False
M2_HYDRATES_CONTEXT_REFS: bool = False

RECOVERY_PRESERVES_SEMANTIC_STOP: bool = True
RECOVERY_AUTO_CONTINUES_SEMANTIC_STOP: bool = False
RECOVERY_PRESERVES_REPLAN_REQUIRED: bool = True
RECOVERY_AUTO_CONTINUES_REPLAN_REQUIRED: bool = False
RECOVERY_AUTO_REPAIRS_REPLAN_REQUIRED: bool = False
TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT: bool = True

RECOVERY_SUCCESS_IS_RETRY_PERMISSION: bool = False
LOGICAL_ROLLOVER_SUCCESS_IS_RETRY_PERMISSION: bool = False
ACTIVE_WORK_IDENTITY_RECOVERY_SUPPORTED: bool = True
ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER: bool = False
LOGICAL_ROLLOVER_AUTO_DISPATCHES_WORKER: bool = False
RECOVERY_AUTO_REPLAYS_SIDE_EFFECT: bool = False
RECOVERED_RESULT_REF_AUTO_REPLAYS_WORK: bool = False

RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER: bool = False
RECOVERY_ESTABLISHES_REVIEWED_FRONTIER: bool = False
RECOVERY_ADVANCES_S4_WORKFLOW: bool = False

SOURCE_CHECKPOINT_ID_IS_AUTHORITY: bool = False
M1_WORKING_TRUTH_BOUNDS_REUSED: bool = True
ARBITRARY_ROLLOVER_METADATA_ALLOWED: bool = False

NEW_SESSION_ENTITY_CREATED_BY_W2: bool = False
NEW_SESSION_ENTITY_REQUIRED_FOR_M2: bool = False
CHECKPOINT_STORE_CREATED_BY_W2: bool = False
M2_CHECKPOINT_STORAGE_REQUIRED: bool = False
NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED: bool = False

W2_EXECUTES_JOURNAL_RECONCILIATION: bool = False
W2_RESOLVES_OPERATION_EFFECT: bool = False
W2_EXECUTES_RETRY: bool = False

M2_CONTEXT_PROVIDER_INTEGRATION_STARTED: bool = False
M2_SELECTIVE_HYDRATION_EXECUTION_STARTED: bool = False

M2_AGENT_NEUTRAL: bool = True
PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False
MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False

M2_TOTAL_NEW_PRODUCTION_MODULE_COUNT: int = 2
M2_NEW_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX: int = 2
W1_PREDECESSOR_REVISION_REQUIRED: bool = False
M1_PREDECESSOR_REVISION_REQUIRED: bool = False
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
AGGREGATOR_CHANGE_REQUIRED_FOR_W2: bool = False
W3_SCOPE_PULLED_FORWARD_BY_W2: bool = False
W2_RUNTIME_SURFACE_CREATED: bool = False

# Reuse flags
W1_RECOVERY_ADMISSION_CONTRACT_REUSED: bool = True
M1_SESSION_CHECKPOINT_CONTRACT_REUSED: bool = True
M1_WORKING_TRUTH_CONTRACT_REUSED: bool = True
SECOND_RECOVERY_ADMISSION_MODEL_CREATED: bool = False
SECOND_CURRENT_GOVERNANCE_MODEL_CREATED: bool = False
SECOND_CHECKPOINT_ONTOLOGY_CREATED: bool = False
SECOND_WORKING_TRUTH_ONTOLOGY_CREATED: bool = False

# ---------------------------------------------------------------------------
# Allowed fields — strict fail-closed
# ---------------------------------------------------------------------------

_ALLOWED_ROLLOVER_RESULT_FIELDS: frozenset[str] = frozenset({
    "admission",
    "reconstructed_working_truth",
    "source_checkpoint_id",
})

# ---------------------------------------------------------------------------
# LogicalRolloverResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LogicalRolloverResult:
    """Immutable typed logical rollover result.

    Fields
    ------
    admission: RecoveryAdmission
        Bounded deterministic admission disposition from W1 evaluator.
        Not authority, not retry permission.

    reconstructed_working_truth: WorkingTruthProjection | None
        Bounded reconstructed truth using current governance precedence
        plus passive refs when scope compatible. None when blocked.

    source_checkpoint_id: str
        Correlation/provenance identifier derived from checkpoint digest.
        Not authority, not freshness, not retry permission.

    Strict serialization: unknown fields fail closed, no arbitrary metadata,
    deterministic canonical JSON, no wall clock or random identity.
    """

    admission: RecoveryAdmission
    reconstructed_working_truth: WorkingTruthProjection | None
    source_checkpoint_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.admission, RecoveryAdmission):
            if isinstance(self.admission, Mapping):
                adm = RecoveryAdmission.from_dict(self.admission)  # type: ignore[arg-type]
                object.__setattr__(self, "admission", adm)
            else:
                raise TypeError(f"admission must be RecoveryAdmission, got {type(self.admission).__name__}")
        if self.reconstructed_working_truth is not None and not isinstance(self.reconstructed_working_truth, WorkingTruthProjection):
            if isinstance(self.reconstructed_working_truth, Mapping):
                proj = WorkingTruthProjection.from_dict(self.reconstructed_working_truth)  # type: ignore[arg-type]
                object.__setattr__(self, "reconstructed_working_truth", proj)
            else:
                raise TypeError(
                    f"reconstructed_working_truth must be WorkingTruthProjection or None, got {type(self.reconstructed_working_truth).__name__}"
                )
        if not isinstance(self.source_checkpoint_id, str) or type(self.source_checkpoint_id) is not str:
            raise TypeError(f"source_checkpoint_id must be str, got {type(self.source_checkpoint_id).__name__}")
        stripped = self.source_checkpoint_id.strip()
        if not stripped:
            raise ValueError("source_checkpoint_id must be a non-empty string")
        object.__setattr__(self, "source_checkpoint_id", stripped)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "admission": self.admission.canonical_dict(),
            "reconstructed_working_truth": self.reconstructed_working_truth.canonical_dict() if self.reconstructed_working_truth is not None else None,
            "source_checkpoint_id": self.source_checkpoint_id,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalRolloverResult":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_ROLLOVER_RESULT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in LogicalRolloverResult: {sorted(extra)}")
        if "admission" not in data:
            raise ValueError("Missing required field in LogicalRolloverResult: 'admission'")
        if "source_checkpoint_id" not in data:
            raise ValueError("Missing required field in LogicalRolloverResult: 'source_checkpoint_id'")
        # admission may be RecoveryAdmission or dict
        raw_adm = data["admission"]
        if isinstance(raw_adm, RecoveryAdmission):
            adm = raw_adm
        elif isinstance(raw_adm, Mapping):
            adm = RecoveryAdmission.from_dict(raw_adm)
        else:
            raise TypeError(f"admission must be RecoveryAdmission or mapping, got {type(raw_adm).__name__}")
        raw_proj = data.get("reconstructed_working_truth")
        proj: WorkingTruthProjection | None = None
        if raw_proj is not None:
            if isinstance(raw_proj, WorkingTruthProjection):
                proj = raw_proj
            elif isinstance(raw_proj, Mapping):
                proj = WorkingTruthProjection.from_dict(raw_proj)
            else:
                raise TypeError(f"reconstructed_working_truth must be WorkingTruthProjection, mapping or None, got {type(raw_proj).__name__}")
        raw_id = data["source_checkpoint_id"]
        if not isinstance(raw_id, str) or type(raw_id) is not str:
            raise TypeError(f"source_checkpoint_id must be str, got {type(raw_id).__name__}")
        return cls(admission=adm, reconstructed_working_truth=proj, source_checkpoint_id=raw_id)


# ---------------------------------------------------------------------------
# Pure deterministic evaluator — logical rollover
# ---------------------------------------------------------------------------

def perform_logical_rollover(
    checkpoint: SessionCheckpoint,
    current: CurrentGovernedWorkingTruth,
) -> LogicalRolloverResult:
    """Bounded deterministic logical rollover.

    Consumes SessionCheckpoint and already-observed CurrentGovernedWorkingTruth.
    Delegates admission to W1 evaluate_recovery_admission (no duplication).
    Reconstructs WorkingTruthProjection with current governance precedence
    when admission is RECOVERABLE or STALE_RECONCILIATION_REQUIRED.
    Returns blocked result with no reconstructed truth for scope mismatch,
    SemanticStop, REPLAN, or unresolved effect.

    Pure and deterministic: no wall clock, no random identity,
    stays bounded and side-effect free.
    """
    if not isinstance(checkpoint, SessionCheckpoint):
        raise TypeError(f"checkpoint must be SessionCheckpoint, got {type(checkpoint).__name__}")
    if not isinstance(current, CurrentGovernedWorkingTruth):
        raise TypeError(f"current must be CurrentGovernedWorkingTruth, got {type(current).__name__}")

    admission = evaluate_recovery_admission(checkpoint, current)
    source_checkpoint_id = checkpoint.checkpoint_id

    if admission.disposition in (
        RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH,
        RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP,
        RecoveryAdmissionDisposition.BLOCKED_BY_REPLAN,
        RecoveryAdmissionDisposition.BLOCKED_BY_UNRESOLVED_EFFECT,
    ):
        reconstructed: WorkingTruthProjection | None = None
        return LogicalRolloverResult(
            admission=admission,
            reconstructed_working_truth=reconstructed,
            source_checkpoint_id=source_checkpoint_id,
        )

    if admission.disposition in (
        RecoveryAdmissionDisposition.RECOVERABLE,
        RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED,
    ):
        wt = checkpoint.working_truth
        # Current governance precedence for identity fields
        # Passive refs carried only when scope compatible (already gated by admission)
        reconstructed = WorkingTruthProjection(
            project_ref=current.project_ref,
            plan_ref=current.plan_ref,
            milestone_ref=current.milestone_ref,
            active_work_item_ref=current.active_work_item_ref,
            accepted_frontier_ref=current.accepted_frontier_ref,
            reviewed_frontier_ref=current.reviewed_frontier_ref,
            workflow_disposition_ref=current.workflow_disposition_ref,
            result_refs=wt.result_refs,
            evidence_refs=wt.evidence_refs,
            context_refs=wt.context_refs,
        )
        return LogicalRolloverResult(
            admission=admission,
            reconstructed_working_truth=reconstructed,
            source_checkpoint_id=source_checkpoint_id,
        )

    # Defensive: treat unknown disposition as blocked
    return LogicalRolloverResult(
        admission=admission,
        reconstructed_working_truth=None,
        source_checkpoint_id=source_checkpoint_id,
    )


__all__ = [
    "W2_CONTRACT_ONLY",
    "LOGICAL_ROLLOVER_RESULT_IS_AUTHORITY",
    "LOGICAL_ROLLOVER_IS_PHYSICAL_SESSION_CREATION",
    "PHYSICAL_MODEL_SESSION_CREATION_IS_FORGE_AUTHORITY",
    "LOGICAL_ROLLOVER_DETERMINISTIC",
    "BLOCKED_RECOVERY_PRODUCES_CONTINUATION",
    "RECOVERABLE_RECONSTRUCTION_SUPPORTED",
    "STALE_CHECKPOINT_CAN_BE_RECONCILED_TO_CURRENT_TRUTH",
    "STALE_CHECKPOINT_CAN_REWIND_CURRENT_TRUTH",
    "CURRENT_STATE_FIELDS_OVERRIDE_STALE_CHECKPOINT",
    "CROSS_PROJECT_ROLLOVER_CONTINUATION_ALLOWED",
    "CROSS_PLAN_ROLLOVER_CONTINUATION_ALLOWED",
    "M2_RECOVERS_RESULT_REFS",
    "M2_RECOVERS_EVIDENCE_REFS",
    "M2_RECOVERS_CONTEXT_REFS",
    "RECOVERED_RESULT_REF_IS_AUTHORITY",
    "RECOVERED_EVIDENCE_REF_IS_AUTHORITY",
    "RECOVERED_CONTEXT_REF_IS_AUTHORITY",
    "M2_HYDRATES_CONTEXT_REFS",
    "RECOVERY_PRESERVES_SEMANTIC_STOP",
    "RECOVERY_PRESERVES_REPLAN_REQUIRED",
    "TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT",
    "RECOVERY_SUCCESS_IS_RETRY_PERMISSION",
    "LOGICAL_ROLLOVER_SUCCESS_IS_RETRY_PERMISSION",
    "ACTIVE_WORK_IDENTITY_RECOVERY_SUPPORTED",
    "ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER",
    "LOGICAL_ROLLOVER_AUTO_DISPATCHES_WORKER",
    "RECOVERY_AUTO_REPLAYS_SIDE_EFFECT",
    "RECOVERED_RESULT_REF_AUTO_REPLAYS_WORK",
    "RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER",
    "RECOVERY_ESTABLISHES_REVIEWED_FRONTIER",
    "RECOVERY_ADVANCES_S4_WORKFLOW",
    "SOURCE_CHECKPOINT_ID_IS_AUTHORITY",
    "M1_WORKING_TRUTH_BOUNDS_REUSED",
    "ARBITRARY_ROLLOVER_METADATA_ALLOWED",
    "NEW_SESSION_ENTITY_CREATED_BY_W2",
    "NEW_SESSION_ENTITY_REQUIRED_FOR_M2",
    "CHECKPOINT_STORE_CREATED_BY_W2",
    "M2_CHECKPOINT_STORAGE_REQUIRED",
    "NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED",
    "W2_EXECUTES_JOURNAL_RECONCILIATION",
    "W2_RESOLVES_OPERATION_EFFECT",
    "W2_EXECUTES_RETRY",
    "M2_CONTEXT_PROVIDER_INTEGRATION_STARTED",
    "M2_SELECTIVE_HYDRATION_EXECUTION_STARTED",
    "M2_AGENT_NEUTRAL",
    "PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "W1_RECOVERY_ADMISSION_CONTRACT_REUSED",
    "M1_SESSION_CHECKPOINT_CONTRACT_REUSED",
    "M1_WORKING_TRUTH_CONTRACT_REUSED",
    "SECOND_RECOVERY_ADMISSION_MODEL_CREATED",
    "SECOND_CURRENT_GOVERNANCE_MODEL_CREATED",
    "SECOND_CHECKPOINT_ONTOLOGY_CREATED",
    "SECOND_WORKING_TRUTH_ONTOLOGY_CREATED",
    "LogicalRolloverResult",
    "perform_logical_rollover",
]
