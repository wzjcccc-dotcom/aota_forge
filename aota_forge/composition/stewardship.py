"""AF #57 M3/W3 legacy-only Project Stewardship production composition.

This module is a typed composition seam, not a second governance engine.  It
reuses the deterministic checkpoint evaluator, the existing Project Steward
handoff, the W1 logical replay record, and the coordinator-owned trusted
finalizer transition.  The thin task-main host is deliberately not imported.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, unique
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.governance.project_store import (
    GovernanceMetadataAlreadyExistsError,
    ProjectGovernanceStore,
    STEWARD_REPLAY_STATE_ACTIVE,
    STEWARD_REPLAY_STATE_COMPLETED,
    STEWARD_REPLAY_STATE_FAILED_CLOSED,
    StewardLogicalReplayRecord,
)
from aota_forge.governance.stewardship import (
    StewardshipCheckpoint,
    StewardshipDisposition,
    StewardshipEvaluation,
    StewardshipOutcome,
    SemanticResidual,
    build_finalizer_input,
    build_materialization_intent,
    evaluate_checkpoint,
    run_governance_checkpoint,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.handoff_store import handoff_open
from aota_forge.work_plane.materialization_receipt import MaterializationReceipt
from aota_forge.work_plane.steward_dispatch import (
    StewardClosureVerdict,
    StewardResult,
)
from aota_forge.work_plane.steward_finalizer import (
    FileBackedReceiptStore,
    FinalizerError,
    FinalizerInput,
    FinalizerReentryEvidence,
    TrustedStewardFinalizer,
    finalize_closure_via_coordinator,
)
from aota_forge.work_plane.task_return_receipt import (
    read_task_return_receipt,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Composition markers
# ---------------------------------------------------------------------------

STEWARDSHIP_PRODUCTION_COMPOSITION_IMPLEMENTED = True
LEGACY_STEWARD_PRODUCTION_EXECUTION = True
LEGACY_STEWARD_PRODUCTION_PATH_ONLY = True
THIN_STEWARD_PRODUCTION_EXECUTION = False
THIN_TASK_MAIN_HOST_MODIFIED = False

STEWARDSHIP_CHECKPOINT_INGRESS_TYPED = True
STEWARDSHIP_DETERMINISTIC_FIRST = True
STEWARDSHIP_REUSES_PROJECT_STEWARD_DISPATCH = True
STEWARDSHIP_REUSES_COORDINATOR_FINALIZER = True
STEWARDSHIP_REPLAY_IS_DURABLE = True
NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH = True
NO_BLIND_REDISPATCH_AFTER_UNKNOWN = True
SEMANTIC_RESIDUAL_BOUND_TO_REPLAY_IDENTITY = True
COMPLETED_REPLAY_HYDRATES_STEWARD_RESULT = True
COMPLETED_REPLAY_HYDRATES_RECEIPT = True
RECOVERABLE_PARTIAL_FINALIZATION_REENTERS = True

SECOND_STEWARD_RESULT_TRANSPORT_CREATED = False
SECOND_FINALIZATION_PROTOCOL = False
SECOND_MUTATION_PROTOCOL = False
NEW_WORKFLOW_DATABASE_CREATED = False
NEW_EVENT_BUS_CREATED = False


class StewardshipProductionError(RuntimeError):
    """Fail-closed composition or replay error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def semantic_residual_digest(residual: SemanticResidual) -> str:
    """Return the canonical digest of the accepted semantic residual contract."""
    if not isinstance(residual, SemanticResidual):
        raise TypeError("residual must be SemanticResidual")
    payload = {
        "kind": residual.kind.value,
        "reason": residual.reason,
        "checkpoint_id": residual.checkpoint_id,
        "required_input_refs": list(residual.required_input_refs),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DurableStewardResultOwner:
    """Adapter over the existing durable result handoff and task.return seams.

    This class owns no storage or terminal-return authority.  It gives the
    replay executor a stable task identity and reuses the existing governed
    ``task_return_receipt`` plus ``handoff_store`` to hydrate a result that was
    already written by the trusted ``task_facade.task_return`` path.
    """

    sandbox: WorktreeSandboxBoundary
    task_id_resolver: Callable[[StewardLogicalReplayRecord], str]

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise TypeError("sandbox must be WorktreeSandboxBoundary")
        if not callable(self.task_id_resolver):
            raise TypeError("task_id_resolver must be callable")

    @staticmethod
    def default_task_id(record: StewardLogicalReplayRecord) -> str:
        return f"steward:{record.project_id}:{record.plan_id}:{record.lineage_id}"

    def task_id(self, record: StewardLogicalReplayRecord) -> str:
        try:
            task_id = self.task_id_resolver(record)
        except Exception as exc:  # noqa: BLE001 - identity resolution fails closed
            raise StewardshipProductionError(
                "RESULT_TASK_ID_RESOLUTION_FAILED",
                f"trusted result task identity resolver raised {type(exc).__name__}",
            ) from exc
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id.strip()) > 256:
            raise StewardshipProductionError(
                "RESULT_TASK_ID_INVALID",
                "trusted result task identity resolver returned an invalid task id",
            )
        return task_id.strip()

    def resolve_steward_result(
        self,
        record: StewardLogicalReplayRecord,
    ) -> StewardResult | None:
        if not isinstance(record, StewardLogicalReplayRecord):
            raise TypeError("record must be StewardLogicalReplayRecord")
        if record.project_id != self.sandbox.project_id:
            raise StewardshipProductionError(
                "RESULT_OWNER_PROJECT_MISMATCH",
                "durable result owner is bound to another project",
            )
        task_id = self.task_id(record)
        receipt = read_task_return_receipt(self.sandbox, task_id)
        if receipt is None:
            return None
        if receipt.canonical_task_id != task_id:
            raise StewardshipProductionError(
                "RESULT_RECEIPT_BINDING_MISMATCH",
                "durable task.return receipt does not bind the replay task",
            )
        opened = handoff_open(receipt.result_ref, "full", sandbox=self.sandbox)
        envelope = opened.get("envelope")
        semantic = opened.get("semantic")
        if opened.get("mode") != "result" or not isinstance(envelope, dict) or not isinstance(semantic, dict):
            raise StewardshipProductionError(
                "RESULT_HANDOFF_INVALID",
                "durable StewardResult handoff is malformed",
            )
        if receipt.status == "failed":
            raise StewardshipProductionError(
                "RESULT_RETURN_FAILED",
                "durable task.return evidence reports a failed semantic return",
            )
        if (
            opened.get("digest") != receipt.result_digest
            or envelope.get("digest") != receipt.result_digest
            or envelope.get("project_id") != self.sandbox.project_id
            or envelope.get("task_id") != task_id
            or envelope.get("source_role") != "project-steward"
        ):
            raise StewardshipProductionError(
                "RESULT_HANDOFF_BINDING_MISMATCH",
                "durable StewardResult handoff or receipt binding is stale/foreign",
            )
        try:
            result_payload = semantic.get("steward_result", semantic)
            if not isinstance(result_payload, dict):
                raise TypeError("steward_result wrapper must be a mapping")
            return StewardResult.from_dict(result_payload)
        except Exception as exc:  # noqa: BLE001 - persisted evidence fails closed
            raise StewardshipProductionError(
                "RESULT_HANDOFF_INVALID",
                f"durable StewardResult could not be decoded: {type(exc).__name__}",
            ) from exc


@unique
class StewardshipExecutionState(str, Enum):
    """Bounded production outcome states; no independent workflow state machine."""

    DETERMINISTIC_SATISFIED = "DETERMINISTIC_SATISFIED"
    DETERMINISTIC_BLOCKED = "DETERMINISTIC_BLOCKED"
    FINALIZED = "FINALIZED"
    COMPLETED = "COMPLETED"
    IN_FLIGHT = "IN_FLIGHT"
    FAILED_CLOSED = "FAILED_CLOSED"


# Short compatibility name for callers that describe the physical dispatch
# state rather than the full checkpoint execution state.
StewardDispatchState = StewardshipExecutionState


@dataclass(frozen=True)
class LegacyStewardshipOutcome:
    """Bounded composition result with compact replay/finalizer evidence."""

    checkpoint_outcome: StewardshipOutcome
    execution_state: StewardshipExecutionState
    replay: StewardLogicalReplayRecord | None = None
    steward_result: StewardResult | None = None
    receipt: MaterializationReceipt | None = None
    reentry_evidence: FinalizerReentryEvidence | None = None
    error_code: str | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_outcome, StewardshipOutcome):
            raise TypeError("checkpoint_outcome must be StewardshipOutcome")
        if not isinstance(self.execution_state, StewardshipExecutionState):
            raise TypeError("execution_state must be StewardshipExecutionState")
        if self.replay is not None and not isinstance(self.replay, StewardLogicalReplayRecord):
            raise TypeError("replay must be StewardLogicalReplayRecord or None")
        if self.steward_result is not None and not isinstance(self.steward_result, StewardResult):
            raise TypeError("steward_result must be StewardResult or None")
        if self.receipt is not None and not isinstance(self.receipt, MaterializationReceipt):
            raise TypeError("receipt must be MaterializationReceipt or None")
        if self.reentry_evidence is not None and not isinstance(self.reentry_evidence, FinalizerReentryEvidence):
            raise TypeError("reentry_evidence must be FinalizerReentryEvidence or None")
        if self.error_code is not None and (not isinstance(self.error_code, str) or not self.error_code.strip()):
            raise ValueError("error_code must be a non-empty string or None")
        if self.error_detail is not None and len(self.error_detail) > 512:
            raise ValueError("error_detail exceeds 512 characters")

    @property
    def checkpoint_id(self) -> str:
        return self.checkpoint_outcome.checkpoint_id

    @property
    def semantic_handoff(self) -> TaskHandoff | None:
        return self.checkpoint_outcome.semantic_handoff

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "execution_state": self.execution_state.value,
            "checkpoint_outcome": self.checkpoint_outcome.to_dict(),
            "replay_id": self.replay.replay_id if self.replay is not None else None,
            "steward_result": self.steward_result.to_dict() if self.steward_result is not None else None,
            "receipt_id": self.receipt.receipt_id if self.receipt is not None else None,
            "reentry": self.reentry_evidence.to_dict() if self.reentry_evidence is not None else None,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


@dataclass(frozen=True)
class LegacyStewardshipExecutor:
    """Legacy trusted bridge over the existing stewardship and finalizer seams.

    ``semantic_dispatch`` is the already-wired Project Steward dispatch hook.
    It returns a typed ``StewardResult`` when the one-shot dispatch has a
    durable result, or ``None`` when the dispatch is accepted but still
    in-flight.  The hook never receives mutation authority.
    """

    runtime_path: str
    governance_store: ProjectGovernanceStore
    coordinator_store: Any
    coordinator_id: str
    live_plan_view: Any
    finalizer: TrustedStewardFinalizer
    semantic_dispatch: Callable[[TaskHandoff], StewardResult | None]
    result_resolver: Callable[[StewardLogicalReplayRecord], StewardResult | None] | None = None
    result_owner: Any | None = None
    result_sandbox: WorktreeSandboxBoundary | None = None
    origin_session_ref: str | None = None

    def __post_init__(self) -> None:
        if self.runtime_path != TASK_MAIN_RUNTIME_PATH_LEGACY:
            raise StewardshipProductionError(
                "LEGACY_RUNTIME_REQUIRED",
                f"stewardship production bridge is legacy-only, got {self.runtime_path!r}",
            )
        if not _non_empty(self.coordinator_id):
            raise ValueError("coordinator_id must be a non-empty string")
        if not isinstance(self.finalizer, TrustedStewardFinalizer):
            raise TypeError("finalizer must be TrustedStewardFinalizer")
        if not callable(self.semantic_dispatch):
            raise TypeError("semantic_dispatch must be callable")
        if self.result_resolver is not None and not callable(self.result_resolver):
            raise TypeError("result_resolver must be callable or None")
        if self.result_owner is not None and not any(
            callable(getattr(self.result_owner, method, None))
            for method in ("resolve_steward_result", "resolve_result", "get_steward_result")
        ):
            raise TypeError("result_owner must expose an existing result resolver seam")
        if self.result_sandbox is not None and not isinstance(
            self.result_sandbox, WorktreeSandboxBoundary
        ):
            raise TypeError("result_sandbox must be WorktreeSandboxBoundary or None")
        if self.origin_session_ref is not None and not _non_empty(self.origin_session_ref):
            raise ValueError("origin_session_ref must be a non-empty string when supplied")
        for label, store, methods in (
            (
                "governance_store",
                self.governance_store,
                ("put_steward_replay", "get_steward_replay", "compare_and_swap_steward_replay"),
            ),
            ("coordinator_store", self.coordinator_store, ("get", "compare_and_swap")),
        ):
            if store is None or any(not callable(getattr(store, method, None)) for method in methods):
                raise TypeError(f"{label} does not implement the required typed store seam")

    def execute(self, checkpoint: StewardshipCheckpoint) -> LegacyStewardshipOutcome:
        """Execute exactly one trusted checkpoint through the legacy path."""
        if not isinstance(checkpoint, StewardshipCheckpoint):
            raise TypeError("checkpoint must be StewardshipCheckpoint")
        if self.result_sandbox is not None and self.result_sandbox.project_id != checkpoint.project_id:
            raise StewardshipProductionError(
                "RESULT_OWNER_PROJECT_MISMATCH",
                "durable result owner is bound to another project",
            )

        evaluation = evaluate_checkpoint(checkpoint)
        if evaluation.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE:
            return self._execute_deterministic_finalization(checkpoint, evaluation)
        if evaluation.disposition in (
            StewardshipDisposition.DETERMINISTIC_SATISFIED,
            StewardshipDisposition.DETERMINISTIC_BLOCKED,
        ):
            checkpoint_outcome = run_governance_checkpoint(checkpoint)
            state = (
                StewardshipExecutionState.DETERMINISTIC_SATISFIED
                if evaluation.disposition is StewardshipDisposition.DETERMINISTIC_SATISFIED
                else StewardshipExecutionState.DETERMINISTIC_BLOCKED
            )
            return LegacyStewardshipOutcome(
                checkpoint_outcome=checkpoint_outcome,
                execution_state=state,
            )
        return self._execute_semantic_residual(checkpoint)

    def _execute_deterministic_finalization(
        self,
        checkpoint: StewardshipCheckpoint,
        evaluation: StewardshipEvaluation,
    ) -> LegacyStewardshipOutcome:
        finalizer_input = build_finalizer_input(checkpoint)
        receipt, evidence = finalize_closure_via_coordinator(
            coordinator_store=self.coordinator_store,
            coordinator_id=self.coordinator_id,
            live_plan_view=self.live_plan_view,
            origin_session_ref=self.origin_session_ref,
            finalizer=self.finalizer,
            finalizer_input=finalizer_input,
        )
        checkpoint_outcome = StewardshipOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=checkpoint.kind,
            disposition=evaluation.disposition,
            evaluation=evaluation,
            receipt=receipt,
            steward_result=finalizer_input.steward_result,
            materialization_intent=finalizer_input.intent,
        )
        return LegacyStewardshipOutcome(
            checkpoint_outcome=checkpoint_outcome,
            execution_state=StewardshipExecutionState.FINALIZED,
            steward_result=finalizer_input.steward_result,
            receipt=receipt,
            reentry_evidence=evidence,
        )

    def _execute_semantic_residual(
        self,
        checkpoint: StewardshipCheckpoint,
    ) -> LegacyStewardshipOutcome:
        plan_id = checkpoint.plan_id
        if plan_id is None:
            raise StewardshipProductionError(
                "REPLAY_PLAN_ID_REQUIRED",
                "semantic production execution requires a canonical checkpoint plan_id",
            )
        base_outcome = run_governance_checkpoint(checkpoint)
        expected = self._new_replay_record(checkpoint, plan_id)
        record, created = self._validate_or_create_replay(expected)
        if record is None:
            return LegacyStewardshipOutcome(
                checkpoint_outcome=base_outcome,
                execution_state=StewardshipExecutionState.IN_FLIGHT,
                error_code="REPLAY_ALREADY_ACTIVE",
                error_detail="another active logical Steward replay exists for this Plan",
            )

        if created:
            result_holder: dict[str, StewardResult | None] = {}

            def dispatch(handoff: TaskHandoff) -> StewardResult | None:
                result = self.semantic_dispatch(handoff)
                if result is not None and not isinstance(result, StewardResult):
                    raise TypeError("semantic_dispatch must return StewardResult or None")
                result_holder["result"] = result
                return result

            try:
                dispatched = run_governance_checkpoint(checkpoint, semantic_dispatch=dispatch)
            except Exception as exc:  # noqa: BLE001 - unknown dispatch outcome is terminal
                failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=base_outcome,
                    execution_state=StewardshipExecutionState.FAILED_CLOSED,
                    replay=failed,
                    error_code="SEMANTIC_DISPATCH_UNKNOWN_OUTCOME",
                    error_detail=f"dispatch raised {type(exc).__name__}",
                )
            result = result_holder.get("result")
            if result is None:
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=dispatched,
                    execution_state=StewardshipExecutionState.IN_FLIGHT,
                    replay=record,
                )
            try:
                self._persist_result(record, result)
            except StewardshipProductionError as exc:
                failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=dispatched,
                    execution_state=StewardshipExecutionState.FAILED_CLOSED,
                    replay=failed,
                    steward_result=result,
                    error_code=exc.code,
                    error_detail=exc.detail[:512],
                )
            return self._complete_semantic_result(checkpoint, dispatched, record, result)

        if record.state == STEWARD_REPLAY_STATE_ACTIVE:
            try:
                resolved = self._resolve_result(record)
            except StewardshipProductionError as exc:
                failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=base_outcome,
                    execution_state=StewardshipExecutionState.FAILED_CLOSED,
                    replay=failed,
                    error_code=exc.code,
                    error_detail=exc.detail[:512],
                )
            if resolved is None:
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=base_outcome,
                    execution_state=StewardshipExecutionState.IN_FLIGHT,
                    replay=record,
                )
            return self._complete_semantic_result(checkpoint, base_outcome, record, resolved)
        if record.state == STEWARD_REPLAY_STATE_COMPLETED:
            try:
                resolved = self._resolve_result(record)
                if resolved is None:
                    raise StewardshipProductionError(
                        "COMPLETED_RESULT_UNAVAILABLE",
                        "completed replay has no durable StewardResult owner evidence",
                    )
            except StewardshipProductionError as exc:
                failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=base_outcome,
                    execution_state=StewardshipExecutionState.FAILED_CLOSED,
                    replay=failed,
                    error_code=exc.code,
                    error_detail=exc.detail[:512],
                )
            return self._complete_semantic_result(
                checkpoint,
                base_outcome,
                record,
                resolved,
                transition_record=False,
            )
        return LegacyStewardshipOutcome(
            checkpoint_outcome=base_outcome,
            execution_state=StewardshipExecutionState.FAILED_CLOSED,
            replay=record,
            error_code="REPLAY_TERMINAL_FAIL_CLOSED",
            error_detail=f"durable replay state is {record.state!r}; no redispatch is permitted",
        )

    def _new_replay_record(
        self,
        checkpoint: StewardshipCheckpoint,
        plan_id: str,
    ) -> StewardLogicalReplayRecord:
        evaluation = evaluate_checkpoint(checkpoint)
        residual = evaluation.residual
        if residual is None:
            raise StewardshipProductionError(
                "REPLAY_RESIDUAL_REQUIRED",
                "semantic replay identity requires a typed semantic residual",
            )
        checkpoint_digest = checkpoint.digest()
        residual_digest = semantic_residual_digest(residual)
        readiness = checkpoint.readiness
        target_lifecycle_revision = readiness.digest if readiness is not None else checkpoint_digest
        accepted_frontier = (
            readiness.reviewed_frontier_ref.ref
            if readiness is not None and checkpoint.closure_phase is not None
            and checkpoint.closure_phase.value == "ACCEPTED_CLOSURE"
            else None
        )
        identity_material = {
            "project_id": checkpoint.project_id,
            "plan_id": plan_id,
            "checkpoint_kind": checkpoint.kind.value,
            "closure_phase": checkpoint.closure_phase.value if checkpoint.closure_phase is not None else None,
            "checkpoint_digest": checkpoint_digest,
            "target_lifecycle_revision": target_lifecycle_revision,
            "accepted_frontier": accepted_frontier,
            "semantic_residual_digest": residual_digest,
        }
        request_digest = hashlib.sha256(
            canonical_json(identity_material).encode("utf-8")
        ).hexdigest()
        return StewardLogicalReplayRecord(
            project_id=checkpoint.project_id,
            plan_id=plan_id,
            lineage_id=(
                f"{checkpoint.kind.value}:residual:{residual_digest}:"
                f"identity:{request_digest[:16]}"
            ),
            request_digest=request_digest,
            checkpoint_ref=f"stewardship-checkpoint:{checkpoint.checkpoint_id}",
            checkpoint_digest=checkpoint_digest,
        )

    def _validate_or_create_replay(
        self,
        expected: StewardLogicalReplayRecord,
    ) -> tuple[StewardLogicalReplayRecord | None, bool]:
        current = self.governance_store.get_steward_replay(
            expected.project_id, expected.plan_id, expected.lineage_id
        )
        if current is not None:
            if (
                current.request_digest != expected.request_digest
                or current.checkpoint_digest != expected.checkpoint_digest
            ):
                raise StewardshipProductionError(
                    "REPLAY_IDENTITY_CONFLICT",
                    "durable replay identity does not match the trusted checkpoint",
                )
            return current, False
        try:
            return self.governance_store.put_steward_replay(expected), True
        except GovernanceMetadataAlreadyExistsError:
            # A different lineage is active.  Do not retry or select it as the
            # current request; the caller must re-enter after that lineage ends.
            current = self.governance_store.get_steward_replay(
                expected.project_id, expected.plan_id, expected.lineage_id
            )
            if current is None:
                return None, False
            if (
                current.request_digest != expected.request_digest
                or current.checkpoint_digest != expected.checkpoint_digest
            ):
                raise StewardshipProductionError(
                    "REPLAY_IDENTITY_CONFLICT",
                    "durable replay identity does not match the trusted checkpoint",
                )
            return current, False

    def _result_owner_candidates(self) -> tuple[Any, ...]:
        candidates: list[Any] = []
        if self.result_owner is not None:
            candidates.append(self.result_owner)
        for source in (self.semantic_dispatch, self.finalizer):
            candidates.append(source)
            nested = getattr(source, "result_owner", None)
            if nested is not None:
                candidates.append(nested)
            nested = getattr(source, "result_store", None)
            if nested is not None:
                candidates.append(nested)
        return tuple(candidates)

    def _result_resolvers(
        self,
    ) -> tuple[Callable[[StewardLogicalReplayRecord], StewardResult | None], ...]:
        resolvers: list[Callable[[StewardLogicalReplayRecord], StewardResult | None]] = []
        if self.result_resolver is not None:
            resolvers.append(self.result_resolver)
        for owner in self._result_owner_candidates():
            for method in ("resolve_steward_result", "resolve_result", "get_steward_result"):
                resolver = getattr(owner, method, None)
                if callable(resolver):
                    if resolver not in resolvers:
                        resolvers.append(resolver)
        return tuple(resolvers)

    def _persist_result(self, record: StewardLogicalReplayRecord, result: StewardResult) -> None:
        for owner in self._result_owner_candidates():
            for method in (
                "persist_steward_result",
                "put_steward_result",
                "store_steward_result",
            ):
                persistor = getattr(owner, method, None)
                if callable(persistor):
                    try:
                        persistor(record, result)
                    except StewardshipProductionError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - owner failure is fail-closed
                        raise StewardshipProductionError(
                            "RESULT_PERSISTENCE_FAILED",
                            f"durable StewardResult owner raised {type(exc).__name__}",
                        ) from exc
                    return

    def _resolve_result(self, record: StewardLogicalReplayRecord) -> StewardResult | None:
        for resolver in self._result_resolvers():
            try:
                result = resolver(record)
            except Exception as exc:  # noqa: BLE001 - resolver failure is fail-closed
                raise StewardshipProductionError(
                    "RESULT_RESOLUTION_FAILED",
                    f"durable StewardResult resolver raised {type(exc).__name__}",
                ) from exc
            if result is not None and not isinstance(result, StewardResult):
                raise StewardshipProductionError(
                    "RESULT_RESOLUTION_INVALID",
                    "durable result resolver returned a non-StewardResult value",
                )
            if result is not None:
                return result
        return None

    @staticmethod
    def _steward_result_digest(result: StewardResult) -> str:
        payload = {
            "milestone_ref": result.milestone_ref,
            "reviewed_frontier_ref": result.reviewed_frontier_ref,
            "verdict": result.verdict.value,
            "accepted_frontier_ref": result.accepted_frontier_ref,
            "governance_evidence_refs": sorted(result.governance_evidence_refs),
            "blocking_reasons": sorted(result.blocking_reasons),
        }
        return hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()

    def _validate_result_binding(
        self,
        checkpoint: StewardshipCheckpoint,
        result: StewardResult,
    ) -> None:
        expected_milestone = checkpoint.trusted_plan.milestone_ref
        if checkpoint.readiness is not None:
            expected_milestone = checkpoint.readiness.milestone_ref.ref
            if result.reviewed_frontier_ref != checkpoint.readiness.reviewed_frontier_ref.ref:
                raise StewardshipProductionError(
                    "RESULT_CHECKPOINT_MISMATCH",
                    "StewardResult reviewed frontier does not match the checkpoint",
                )
        if result.milestone_ref != expected_milestone:
            raise StewardshipProductionError(
                "RESULT_CHECKPOINT_MISMATCH",
                "StewardResult milestone does not match the checkpoint",
            )
        if checkpoint.closure_phase is not None:
            if checkpoint.closure_phase.value == "REVIEWED_CLOSURE" and result.accepted_frontier_ref is not None:
                raise StewardshipProductionError(
                    "RESULT_CHECKPOINT_MISMATCH",
                    "reviewed checkpoint cannot hydrate an accepted frontier",
                )
            if checkpoint.closure_phase.value == "ACCEPTED_CLOSURE" and (
                result.accepted_frontier_ref != result.reviewed_frontier_ref
            ):
                raise StewardshipProductionError(
                    "RESULT_CHECKPOINT_MISMATCH",
                    "accepted checkpoint result does not bind the reviewed frontier",
                )

    def _validate_receipt_binding(
        self,
        checkpoint: StewardshipCheckpoint,
        result: StewardResult,
        receipt: MaterializationReceipt,
    ) -> None:
        if not isinstance(receipt, MaterializationReceipt):
            raise StewardshipProductionError(
                "RECEIPT_INVALID",
                "finalizer returned a non-MaterializationReceipt",
            )
        expected_milestone = checkpoint.trusted_plan.milestone_ref
        if checkpoint.readiness is not None:
            expected_milestone = checkpoint.readiness.milestone_ref.ref
        if (
            receipt.project_id != checkpoint.project_id
            or receipt.plan_ref != checkpoint.trusted_plan.plan_ref
            or receipt.milestone_ref != expected_milestone
            or receipt.closure_phase != checkpoint.closure_phase.value
            or receipt.steward_digest != self._steward_result_digest(result)
            or receipt.receipt_digest != receipt.compute_digest()
        ):
            raise StewardshipProductionError(
                "RECEIPT_CHECKPOINT_MISMATCH",
                "persisted finalizer receipt does not bind the checkpoint/result",
            )

    def _complete_semantic_result(
        self,
        checkpoint: StewardshipCheckpoint,
        checkpoint_outcome: StewardshipOutcome,
        record: StewardLogicalReplayRecord,
        result: StewardResult,
        *,
        transition_record: bool = True,
    ) -> LegacyStewardshipOutcome:
        try:
            self._validate_result_binding(checkpoint, result)
        except StewardshipProductionError as exc:
            failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
            return LegacyStewardshipOutcome(
                checkpoint_outcome=checkpoint_outcome,
                execution_state=StewardshipExecutionState.FAILED_CLOSED,
                replay=failed,
                steward_result=result,
                error_code=exc.code,
                error_detail=exc.detail[:512],
            )

        def completed_record() -> StewardLogicalReplayRecord:
            return self._transition(record, STEWARD_REPLAY_STATE_COMPLETED) if transition_record else record

        if result.verdict is StewardClosureVerdict.GOVERNANCE_BLOCKED:
            completed = completed_record()
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.COMPLETED,
                replay=completed,
                steward_result=result,
            )

        if checkpoint.readiness is None or checkpoint.closure_phase is None:
            completed = completed_record()
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.COMPLETED,
                replay=completed,
                steward_result=result,
            )

        try:
            intent = build_materialization_intent(checkpoint)
            finalizer_input = FinalizerInput(
                steward_result=result,
                readiness=checkpoint.readiness,
                trusted_binding=checkpoint.trusted_binding,
                trusted_plan=checkpoint.trusted_plan,
                user_gate=checkpoint.user_gate,
                intent=intent,
                closure_phase=checkpoint.closure_phase,
            )
            receipt, evidence = finalize_closure_via_coordinator(
                coordinator_store=self.coordinator_store,
                coordinator_id=self.coordinator_id,
                live_plan_view=self.live_plan_view,
                origin_session_ref=self.origin_session_ref,
                finalizer=self.finalizer,
                finalizer_input=finalizer_input,
            )
        except FinalizerError as exc:
            if exc.receipt is not None and exc.receipt.final_status == "PARTIAL":
                try:
                    self._validate_receipt_binding(checkpoint, result, exc.receipt)
                except StewardshipProductionError as binding_error:
                    failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
                    return LegacyStewardshipOutcome(
                        checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                        execution_state=StewardshipExecutionState.FAILED_CLOSED,
                        replay=failed,
                        steward_result=result,
                        receipt=exc.receipt,
                        error_code=binding_error.code,
                        error_detail=binding_error.detail[:512],
                    )
                return LegacyStewardshipOutcome(
                    checkpoint_outcome=replace(
                        checkpoint_outcome,
                        steward_result=result,
                        receipt=exc.receipt,
                        materialization_intent=intent,
                    ),
                    execution_state=StewardshipExecutionState.IN_FLIGHT,
                    replay=record,
                    steward_result=result,
                    receipt=exc.receipt,
                    error_code=exc.code.value,
                    error_detail=f"finalizer partial receipt is recoverable: {exc.code.value}",
                )
            failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.FAILED_CLOSED,
                replay=failed,
                steward_result=result,
                receipt=exc.receipt,
                error_code=exc.code.value,
                error_detail=f"finalizer rejected with {exc.code.value}",
            )
        except Exception as exc:  # noqa: BLE001 - typed production boundary
            failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.FAILED_CLOSED,
                replay=failed,
                steward_result=result,
                error_code="STEWARD_FINALIZATION_FAILED_CLOSED",
                error_detail=f"finalization raised {type(exc).__name__}",
            )

        try:
            self._validate_receipt_binding(checkpoint, result, receipt)
        except StewardshipProductionError as exc:
            failed = self._transition(record, STEWARD_REPLAY_STATE_FAILED_CLOSED)
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.FAILED_CLOSED,
                replay=failed,
                steward_result=result,
                receipt=receipt,
                error_code=exc.code,
                error_detail=exc.detail[:512],
            )
        completed = completed_record()
        return LegacyStewardshipOutcome(
            checkpoint_outcome=replace(
                checkpoint_outcome,
                steward_result=result,
                receipt=receipt,
                materialization_intent=intent,
            ),
            execution_state=StewardshipExecutionState.FINALIZED,
            replay=completed,
            steward_result=result,
            receipt=receipt,
            reentry_evidence=evidence,
        )

    def _transition(self, record: StewardLogicalReplayRecord, state: str) -> StewardLogicalReplayRecord:
        try:
            return self.governance_store.compare_and_swap_steward_replay(
                record.project_id,
                record.plan_id,
                record.lineage_id,
                record.revision,
                state=state,
            )
        except Exception as exc:  # noqa: BLE001 - durable state failure is fail-closed
            current = self.governance_store.get_steward_replay(
                record.project_id, record.plan_id, record.lineage_id
            )
            if current is not None and current.state == state:
                return current
            raise StewardshipProductionError(
                "REPLAY_PERSISTENCE_FAILED",
                f"could not transition replay to {state!r}: {exc}",
            ) from exc


def create_legacy_stewardship_executor(
    *,
    runtime_path: str,
    governance_store: ProjectGovernanceStore,
    coordinator_store: Any,
    coordinator_id: str,
    live_plan_view: Any,
    finalizer: TrustedStewardFinalizer,
    semantic_dispatch: Callable[[TaskHandoff], StewardResult | None],
    result_resolver: Callable[[StewardLogicalReplayRecord], StewardResult | None] | None = None,
    result_owner: Any | None = None,
    result_sandbox: WorktreeSandboxBoundary | None = None,
    result_task_id_resolver: Callable[[StewardLogicalReplayRecord], str] | None = None,
    origin_session_ref: str | None = None,
) -> LegacyStewardshipExecutor:
    """Build the explicit trusted legacy bridge; thin never falls back here."""
    if runtime_path != TASK_MAIN_RUNTIME_PATH_LEGACY:
        raise StewardshipProductionError(
            "LEGACY_RUNTIME_REQUIRED",
            f"legacy-only stewardship composition requires runtime_path='legacy', got {runtime_path!r}",
        )
    if result_sandbox is not None:
        if result_owner is None:
            if result_task_id_resolver is None:
                raise StewardshipProductionError(
                    "RESULT_OWNER_REQUIRED",
                    "result_sandbox requires a trusted existing result owner or task identity resolver",
                )
            result_owner = DurableStewardResultOwner(result_sandbox, result_task_id_resolver)
        if finalizer.receipt_store is None:
            finalizer.receipt_store = FileBackedReceiptStore(
                Path(result_sandbox.worktree_root) / ".aota" / "materialization_receipts.json"
            )
    return LegacyStewardshipExecutor(
        runtime_path=runtime_path,
        governance_store=governance_store,
        coordinator_store=coordinator_store,
        coordinator_id=coordinator_id,
        live_plan_view=live_plan_view,
        finalizer=finalizer,
        semantic_dispatch=semantic_dispatch,
        result_resolver=result_resolver,
        result_owner=result_owner,
        result_sandbox=result_sandbox,
        origin_session_ref=origin_session_ref,
    )


def run_legacy_stewardship_checkpoint(
    checkpoint: StewardshipCheckpoint,
    **executor_options: Any,
) -> LegacyStewardshipOutcome:
    """One-shot convenience wrapper around the explicit legacy composition."""
    return create_legacy_stewardship_executor(**executor_options).execute(checkpoint)


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and type(value) is str and bool(value.strip())


__all__ = [
    "LEGACY_STEWARD_PRODUCTION_EXECUTION",
    "LEGACY_STEWARD_PRODUCTION_PATH_ONLY",
    "LegacyStewardshipExecutor",
    "LegacyStewardshipOutcome",
    "DurableStewardResultOwner",
    "NEW_EVENT_BUS_CREATED",
    "NEW_WORKFLOW_DATABASE_CREATED",
    "NO_BLIND_REDISPATCH_AFTER_UNKNOWN",
    "NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH",
    "SECOND_FINALIZATION_PROTOCOL",
    "SECOND_MUTATION_PROTOCOL",
    "SECOND_STEWARD_RESULT_TRANSPORT_CREATED",
    "STEWARDSHIP_CHECKPOINT_INGRESS_TYPED",
    "STEWARDSHIP_DETERMINISTIC_FIRST",
    "STEWARDSHIP_PRODUCTION_COMPOSITION_IMPLEMENTED",
    "STEWARDSHIP_REPLAY_IS_DURABLE",
    "STEWARDSHIP_REUSES_COORDINATOR_FINALIZER",
    "STEWARDSHIP_REUSES_PROJECT_STEWARD_DISPATCH",
    "StewardDispatchState",
    "StewardshipExecutionState",
    "StewardshipProductionError",
    "semantic_residual_digest",
    "THIN_STEWARD_PRODUCTION_EXECUTION",
    "THIN_TASK_MAIN_HOST_MODIFIED",
    "create_legacy_stewardship_executor",
    "run_legacy_stewardship_checkpoint",
]
