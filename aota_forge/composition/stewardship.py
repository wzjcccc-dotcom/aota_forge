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
    build_finalizer_input,
    build_materialization_intent,
    evaluate_checkpoint,
    run_governance_checkpoint,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.materialization_receipt import MaterializationReceipt
from aota_forge.work_plane.steward_dispatch import (
    StewardClosureVerdict,
    StewardResult,
)
from aota_forge.work_plane.steward_finalizer import (
    FinalizerError,
    FinalizerInput,
    FinalizerReentryEvidence,
    TrustedStewardFinalizer,
    finalize_closure_via_coordinator,
)

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
            resolved: StewardResult | None = None
            resolver_error: StewardshipProductionError | None = None
            if self.result_resolver is not None:
                try:
                    resolved = self._resolve_result(record)
                except StewardshipProductionError as exc:
                    resolver_error = exc
            return LegacyStewardshipOutcome(
                checkpoint_outcome=base_outcome,
                execution_state=StewardshipExecutionState.COMPLETED,
                replay=record,
                steward_result=resolved,
                error_code=resolver_error.code if resolver_error is not None else None,
                error_detail=resolver_error.detail[:512] if resolver_error is not None else None,
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
        checkpoint_digest = checkpoint.digest()
        request_digest = hashlib.sha256(
            canonical_json(
                {
                    "project_id": checkpoint.project_id,
                    "plan_id": plan_id,
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "checkpoint_digest": checkpoint_digest,
                }
            ).encode("utf-8")
        ).hexdigest()
        return StewardLogicalReplayRecord(
            project_id=checkpoint.project_id,
            plan_id=plan_id,
            lineage_id=f"checkpoint:{checkpoint.checkpoint_id}",
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

    def _resolve_result(self, record: StewardLogicalReplayRecord) -> StewardResult | None:
        if self.result_resolver is None:
            return None
        try:
            result = self.result_resolver(record)
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
        return result

    def _complete_semantic_result(
        self,
        checkpoint: StewardshipCheckpoint,
        checkpoint_outcome: StewardshipOutcome,
        record: StewardLogicalReplayRecord,
        result: StewardResult,
    ) -> LegacyStewardshipOutcome:
        if result.verdict is StewardClosureVerdict.GOVERNANCE_BLOCKED:
            completed = self._transition(record, STEWARD_REPLAY_STATE_COMPLETED)
            return LegacyStewardshipOutcome(
                checkpoint_outcome=replace(checkpoint_outcome, steward_result=result),
                execution_state=StewardshipExecutionState.COMPLETED,
                replay=completed,
                steward_result=result,
            )

        if checkpoint.readiness is None or checkpoint.closure_phase is None:
            completed = self._transition(record, STEWARD_REPLAY_STATE_COMPLETED)
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

        completed = self._transition(record, STEWARD_REPLAY_STATE_COMPLETED)
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
    origin_session_ref: str | None = None,
) -> LegacyStewardshipExecutor:
    """Build the explicit trusted legacy bridge; thin never falls back here."""
    if runtime_path != TASK_MAIN_RUNTIME_PATH_LEGACY:
        raise StewardshipProductionError(
            "LEGACY_RUNTIME_REQUIRED",
            f"legacy-only stewardship composition requires runtime_path='legacy', got {runtime_path!r}",
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
    "THIN_STEWARD_PRODUCTION_EXECUTION",
    "THIN_TASK_MAIN_HOST_MODIFIED",
    "create_legacy_stewardship_executor",
    "run_legacy_stewardship_checkpoint",
]
