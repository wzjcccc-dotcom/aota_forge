"""M3/W2 CARD-first Autonomous Progression — governed semantic reconciliation.

Closes M2/F03 in source construction: Worker completion -> CARD-first exact
validation -> trusted CanonicalResult/CARD hydration -> existing
progression/review evaluators -> durable working truth + receipt -> ONLY
then ACK eligibility.

Proves (fail-closed, deterministic, restart-safe):

* exact completion binding (WI/task/CARD-digest/result-identity) before any
  semantic apply; cross-WI / cross-Milestone / cross-coordinator /
  cross-authority / out-of-order / contradictory completions rejected
* CARD is evidence only: PASS alone never advances semantics; validation
  verdicts + risk policy + progression gates all still enforced; Worker
  next-hints never dispatch
* duplicate delivery replays the same receipt (idempotent, ACK-safe)
* ACK string alone insufficient; ACK eligibility requires the durable receipt
* crash windows S1-S8 incl. M2 delivery ACK end-to-end (existing M2 delivery
  machine reused, no second delivery state machine)
* source-ready progression -> INTEGRATED_REVIEW_REQUIRED (no auto-dispatch)
* reviewer path: PASS / SOURCE_REPAIR / PLAN_CHANGE(user gate) /
  ENVIRONMENT(revalidation, not repair) / repeated-failure bounded
* derivation only: zero physical dispatches, no closure automation, no Plan
  mutation, no next-Milestone activation

``PERSISTENCE_REQUIRES_FOREVER_PROCESS=no``: every restart test closes the
stores and rebuilds all runtime objects from the same files.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aota_forge.composition.task_main import (
    reconcile_task_main_completion,
    reconcile_task_main_review,
)
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.roles import CANONICAL_ROLES
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
    parse_completion_ack,
)
from aota_forge.runtime.task_main import reconciliation as R
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    PlanDriftError,
    TaskMainCoordinator,
    activate_milestone,
    dispatch_identity_for,
    recover_coordinator,
)
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATE_SCHEMA_VERSION,
    WI_SEMANTIC_RECONCILED,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    CoordinatorNotFoundError,
    CoordinatorPersistenceFailureError,
    FileBackedTaskMainCoordinatorStore,
)
from aota_forge.runtime.task_main.reconciliation import (
    CompletionReconciliationReceipt,
    ContradictoryCompletionError,
    GovernedReviewEvidence,
    GovernedWorkItemEvidence,
    ReconciliationError,
    ack_eligible_for,
    build_ack_token,
    reconcile_review_completion,
    reconcile_worker_completion,
)
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.milestone_review import (
    MilestoneReviewEvidence,
    ReviewCycle,
    ReviewFindingClassification,
    ReviewFindingEvidence,
)
from aota_forge.work_plane.milestone_review_workflow import (
    FailureFingerprint,
    WorkflowDisposition,
)
from aota_forge.work_plane.progression import (
    FocusedValidationEvidence,
    FocusedValidationVerdict,
    MilestoneWorkItemGraph,
)
from aota_forge.work_plane.result_card import (
    WorkerResultCard,
    project_worker_result_card,
)
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.stop import MechanicalFailure, StopKind

FAKE_EXECUTOR_ID = "m3w2-fake"
TEST_SCOPE = "m3w2-fake:coder"
ORIGIN_SESSION = "20260907_taskmain_m3w2_disposable"
PLAN_AUTHORITY = "wzjcccc-dotcom/aota-hermes-tools#36"
ENTRY_BASE = "94206e90f0769c60127c5fbb0cb9a8ef2b88fa64"
PROJECT_ID = "aota_forge"
DISPATCH_STAMP = "2026-09-07T00:00:00+00:00"
MILESTONE_ID = "M3"


def _body(marker: str) -> str:
    return (
        "# [PLAN] M3/W2 fixture body\n\n"
        "## 1. Current State\n"
        "```text\n"
        "PLAN_STATUS=in-progress\n"
        "CURRENT_MILESTONE=M3\n"
        "M3_STATUS=in_progress\n"
        f"CURRENT_BLOCKER={marker}\n"
        "```\n"
    )


def _plan_digest(marker: str = "none", *, revision: str = "rev-m3w2-a") -> tuple[str, str | None]:
    doc = normalize_portable_plan(_body(marker), source_revision=revision)
    return portable_plan_digest(doc), doc.source_revision


def _graph(work_items: list[str], dependencies: list[list[str]] | None = None) -> MilestoneWorkItemGraph:
    return MilestoneWorkItemGraph(
        milestone_ref=MILESTONE_ID,
        work_items=list(work_items),
        dependencies=[list(edge) for edge in (dependencies or [])],
    )


def _view(
    work_items: list[str],
    dependencies: list[list[str]] | None = None,
    *,
    approved: bool = True,
    marker: str = "none",
    amendment: bool = False,
) -> MilestonePlanView:
    digest, revision = _plan_digest(marker)
    return MilestonePlanView(
        plan_authority=PLAN_AUTHORITY,
        plan_digest=digest,
        plan_source_revision=revision,
        milestone_id=MILESTONE_ID,
        entry_base=ENTRY_BASE,
        graph=_graph(work_items, dependencies),
        milestone_user_approval_satisfied=approved,
        plan_amendment_required=amendment,
    )


def _handoff(work_item_id: str) -> TaskHandoff:
    return TaskHandoff(
        work_role="coder",
        task_kind="m3w2-proof",
        objective=f"m3w2 governed proof objective for {work_item_id}",
        bounded_scope=f"m3w2 bounded scope for {work_item_id}",
        validation_expectations=(f"cheap validation for {work_item_id}",),
        semantic_stop_expectations=(f"semantic stop for {work_item_id}",),
        work_item_ref=SemanticReference(ref=work_item_id),
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
    )


def _reviewer_handoff() -> TaskHandoff:
    return TaskHandoff(
        work_role="reviewer",
        task_kind="m3w2-review",
        objective="m3w2 governed integrated review objective",
        bounded_scope="m3w2 bounded review scope",
        validation_expectations=("review binding validation",),
        semantic_stop_expectations=("review semantic stop",),
        work_item_ref=SemanticReference(ref="M3/RV1"),
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
    )


def _resolver(work_items: list[str]) -> Callable[[str], TaskHandoff]:
    table = {wi: _handoff(wi) for wi in work_items}

    def _resolve(work_item_id: str) -> TaskHandoff:
        return table[work_item_id]

    return _resolve


def _neutral_envelope() -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(
        milestone_ref=MILESTONE_ID,
        default_process_depth=ProcessDepth.STANDARD,
        minimum_process_depth=ProcessDepth.STANDARD,
    )


def _evidence(
    work_item_id: str, *, verdict: FocusedValidationVerdict = FocusedValidationVerdict.PASS
) -> GovernedWorkItemEvidence:
    return GovernedWorkItemEvidence(
        validation_evidence=FocusedValidationEvidence(
            work_item_ref=work_item_id,
            verdict=verdict,
            validation_evidence_ref=SemanticReference(ref=f"val:{work_item_id}"),
        ),
        risk_envelope=_neutral_envelope(),
    )


def _cid(work_item_id: str) -> str:
    cid, _, _ = dispatch_identity_for(
        project_id=PROJECT_ID,
        plan_authority=PLAN_AUTHORITY,
        milestone_id=MILESTONE_ID,
        work_item_id=work_item_id,
    )
    return cid


class M3W2FakeAdapter(ExecutorAdapter):
    """Executor-neutral stable-handle fake; class counter proves no redispatch."""

    total_dispatches = 0
    forced: ClassVar[dict[str, str]] = {}

    def __init__(self) -> None:
        self.physical_dispatch_count = 0

    @classmethod
    def reset_world(cls) -> None:
        cls.total_dispatches = 0
        cls.forced = {}

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id=FAKE_EXECUTOR_ID,
            adapter_kind="m3w2_fake_test_double",
            supported_execution_modes=("async",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    @classmethod
    def handle_for(cls, package: ExecutionPackage) -> str:
        outcome = cls.forced.get(package.canonical_task_id, package.constraints.get("fake_outcome", "running"))
        return f"m3w2-fake||{package.canonical_task_id}||{outcome}"

    @staticmethod
    def _parse(handle: str) -> tuple[str, str]:
        _, task_id, outcome = handle.split("||")
        return task_id, outcome

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.physical_dispatch_count += 1
        type(self).total_dispatches += 1
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=self.handle_for(package),
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time=DISPATCH_STAMP,
        )

    def _state(self, outcome: str) -> CanonicalTaskState:
        if outcome in {"running", "slow"}:
            return CanonicalTaskState.RUNNING
        if outcome == "failed":
            return CanonicalTaskState.FAILED
        return CanonicalTaskState.COMPLETED

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        task_id, outcome = self._parse(adapter_handle)
        return TaskStatusResult(canonical_task_id=task_id, state=self._state(outcome))

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        task_id, outcome = self._parse(adapter_handle)
        state = self._state(outcome)
        if outcome == "failed":
            return CanonicalResult.failure(
                canonical_task_id=task_id,
                executor_id=FAKE_EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="m3w2 fake scripted failure",
                correlation_id=f"corr-{task_id}",
                canonical_task_state=state.value,
            )
        return CanonicalResult.success(
            canonical_task_id=task_id,
            executor_id=FAKE_EXECUTOR_ID,
            result_data={"proof": "m3-w2-durable"},
            stdout_summary="raw-worker-stdout-stays-mechanical",
            stderr_summary="raw-worker-stderr-stays-mechanical",
            execution_stats={"duration_ms": 5},
            correlation_id=f"corr-{task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(
            canonical_task_id=canonical_task_id, cancelled=True, state=CanonicalTaskState.CANCELLED
        )

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


class ScriptedTransport:
    """Bounded fake delivery transport: returns a scripted ACK line per task."""

    def __init__(self) -> None:
        self.ack_lines: dict[str, str] = {}
        self.attempts: list[dict[str, str]] = []

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence:
        self.attempts.append({"session_ref": session_ref})
        task_id = ""
        for line in envelope.splitlines():
            if line.startswith("canonical_task_id="):
                task_id = line.split("=", 1)[1].strip()
        line = self.ack_lines.get(task_id)
        if line is None:
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.COMPLETED, response_text=""
            )
        return DeliveryAttemptEvidence(
            outcome=DeliveryTransportOutcome.COMPLETED, response_text=line
        )


class World:
    """Fresh-object world: file stores + fresh dispatcher per construction."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        name: str = "default",
        limits: dict[str, int] | None = None,
        use_completion: bool = False,
        transport: ScriptedTransport | None = None,
    ) -> None:
        self.coord_path = tmp_path / f"{name}.coordinator.json"
        self.exec_path = tmp_path / f"{name}.execution.json"
        self.coord_store = FileBackedTaskMainCoordinatorStore(self.coord_path)
        self.exec_store = FileBackedExecutionStateStore(self.exec_path)
        self.adapter = M3W2FakeAdapter()
        registry = ExecutorRegistry()
        registry.register(self.adapter)
        from aota_forge.core.execution.durable_state import OriginSessionRef

        self.dispatcher = ExecutionDispatcher(
            registry,
            state_store=self.exec_store,
            origin_session_ref=OriginSessionRef(value=ORIGIN_SESSION),
            admission_scope_resolver=(lambda package: TEST_SCOPE),
        )
        self.transport = transport
        self.completion: DurableCompletionCoordinator | None = None
        if use_completion:
            self.completion = DurableCompletionCoordinator(
                dispatcher=self.dispatcher,
                store=self.exec_store,
                transport=self.transport,
                admission_limits=limits if limits is not None else {TEST_SCOPE: 10},
            )

    def close(self) -> None:
        self.coord_store.close()
        self.exec_store.close()

    def reopen(
        self,
        *,
        limits: dict[str, int] | None = None,
        use_completion: bool = False,
        transport: ScriptedTransport | None = None,
    ) -> World:
        """Simulate process restart: close files, rebuild every object fresh."""
        tmp_path = self.coord_path.parent
        name = self.coord_path.stem.replace(".coordinator", "")
        self.close()
        return World(tmp_path, name=name, limits=limits, use_completion=use_completion, transport=transport)

    def activate(self, view: MilestonePlanView, **kwargs: Any) -> TaskMainCoordinator:
        return activate_milestone(
            store=self.coord_store,
            plan_view=view,
            origin_task_main_session_ref=ORIGIN_SESSION,
            execution_dispatcher=self.dispatcher,
            completion_coordinator=self.completion,
            executor_id=FAKE_EXECUTOR_ID,
            project_id=PROJECT_ID,
            **kwargs,
        )

    def recover(self, view: MilestonePlanView, coordinator_id: str, **kwargs: Any) -> TaskMainCoordinator:
        return recover_coordinator(
            store=self.coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=view,
            execution_dispatcher=self.dispatcher,
            completion_coordinator=self.completion,
            session_available=True,
            **kwargs,
        )


@pytest.fixture(autouse=True)
def _reset_fake_world() -> None:
    M3W2FakeAdapter.reset_world()


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    yield w
    w.close()


def _run_to_terminal(
    world: World,
    handle: TaskMainCoordinator,
    work_items: list[str],
    work_item_id: str,
    *,
    fail: bool = False,
    role: str = "coder",
    next_hint: str | None = None,
    mechanical_failure: MechanicalFailure | None = None,
) -> tuple[str, WorkerResultCard]:
    if fail:
        M3W2FakeAdapter.forced[_cid(work_item_id)] = "failed"
    handle.dispatch_ready(_resolver(work_items))
    cid = handle.active_bindings()[work_item_id]["canonical_task_id"]
    canonical = world.dispatcher.result(cid)
    if fail:
        assert canonical.error is not None
        governance = ResultGovernanceProjection.failure(canonical.error)
    else:
        governance = ResultGovernanceProjection.success()
    card = project_worker_result_card(
        canonical,
        governance,
        role,
        summary=f"m3w2 terminal proof for {work_item_id}",
        next_hint=next_hint,
        mechanical_failure=mechanical_failure,
    )
    world.dispatcher.attach_worker_result_card(cid, card)
    observed = handle.observe_terminal_completions()
    assert work_item_id in observed
    return cid, card


def _reconcile(
    world: World,
    view: MilestonePlanView,
    cid: str,
    digest: str,
    work_item_id: str,
    *,
    verdict: FocusedValidationVerdict = FocusedValidationVerdict.PASS,
    with_handoffs: bool = True,
    work_items: list[str] | None = None,
):
    return reconcile_worker_completion(
        store=world.coord_store,
        execution_store=world.exec_store,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
        canonical_task_id=cid,
        card_digest=digest,
        live_plan_view=view,
        governed_evidence=_evidence(work_item_id, verdict=verdict),
        handoff_resolver=_resolver(work_items or [work_item_id]) if with_handoffs else None,
    )


REVIEWER_TASK_ID = f"{PROJECT_ID}:{MILESTONE_ID}:RV1:attempt-1"


def _reviewer_completion(
    world: World, *, fail_env: bool = False
) -> tuple[str, WorkerResultCard]:
    if fail_env:
        M3W2FakeAdapter.forced[REVIEWER_TASK_ID] = "failed"
    handoff = _reviewer_handoff()
    package = compile_handoff_to_execution_package(
        handoff,
        TrustedExecutionBinding(canonical_task_id=REVIEWER_TASK_ID, project_id=PROJECT_ID),
        package_id=f"{REVIEWER_TASK_ID}:pkg",
        idempotency_key=f"reviewer|{REVIEWER_TASK_ID}",
        correlation_id=f"corr-{REVIEWER_TASK_ID}",
    )
    world.dispatcher.dispatch(package, FAKE_EXECUTOR_ID)
    canonical = world.dispatcher.result(REVIEWER_TASK_ID)
    if fail_env:
        assert canonical.error is not None
        governance = ResultGovernanceProjection.failure(canonical.error)
        mech = MechanicalFailure(
            task_ref=REVIEWER_TASK_ID,
            error_code="HERMES_SESSION_REENTRY_FAILED",
            retryable=True,
            result_ref=REVIEWER_TASK_ID,
        )
    else:
        governance = ResultGovernanceProjection.success()
        mech = None
    card = project_worker_result_card(
        canonical,
        governance,
        "reviewer",
        summary="m3w2 governed integrated review",
        mechanical_failure=mech,
    )
    assert card.agent_work_role == AgentWorkRole.REVIEWER
    world.dispatcher.attach_worker_result_card(REVIEWER_TASK_ID, card)
    return REVIEWER_TASK_ID, card


def _finding(ref: str, *, blocking: bool = True) -> ReviewFindingEvidence:
    return ReviewFindingEvidence(
        finding_ref=ref,
        classification=(
            ReviewFindingClassification.BLOCKING
            if blocking
            else ReviewFindingClassification.NON_BLOCKING
        ),
        supporting_evidence_ref=SemanticReference(ref=f"sup:{ref}"),
        supporting_evidence_digest=f"digest-{ref}",
    )


def _review_evidence(
    card: WorkerResultCard,
    findings: tuple[ReviewFindingEvidence, ...] = (),
    *,
    cycle: ReviewCycle = ReviewCycle.RV1,
    frontier: str = "frontier-m3-rv1",
) -> MilestoneReviewEvidence:
    return MilestoneReviewEvidence(
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
        review_cycle=cycle,
        reviewed_frontier_ref=SemanticReference(ref=frontier),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.compute_card_digest(),
        finding_refs=tuple(f.finding_ref for f in findings),
    )


def _governed_review(
    card: WorkerResultCard,
    findings: tuple[ReviewFindingEvidence, ...] = (),
    *,
    cycle: ReviewCycle = ReviewCycle.RV1,
    frontier: str = "frontier-m3-rv1",
    expected_final_frontier: Any = None,
) -> GovernedReviewEvidence:
    return GovernedReviewEvidence(
        review_evidence=_review_evidence(card, findings, cycle=cycle, frontier=frontier),
        review_findings=findings,
        review_task_handoff=_reviewer_handoff(),
        expected_final_frontier=expected_final_frontier,
    )


def _review_reconcile(world: World, view: MilestonePlanView, cid: str, digest: str, governed: GovernedReviewEvidence):
    return reconcile_review_completion(
        store=world.coord_store,
        execution_store=world.exec_store,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
        reviewer_canonical_task_id=cid,
        card_digest=digest,
        live_plan_view=view,
        governed_review=governed,
    )


# ---------------------------------------------------------------------------
# CARD-first happy path + evidence-not-authority
# ---------------------------------------------------------------------------


class TestCardFirstReconciliation:
    def test_reconcile_progresses_and_gates_ack(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1", "W2"], "W1")
        digest = card.compute_card_digest()

        outcome = _reconcile(world, view, cid, digest, "W1", work_items=["W1", "W2"])

        assert outcome.receipt.progression_disposition == R.DISPOSITION_PROGRESSION_COMPLETE
        assert outcome.ack_eligible is True
        assert outcome.ack_token is not None
        assert parse_completion_ack(outcome.ack_token, canonical_task_id=cid, card_digest=digest)
        assert outcome.replayed is False
        assert outcome.ready_work_items == ("W2",)
        assert outcome.progression_complete == ("W1",)
        assert outcome.physical_dispatch_attempts == 0
        assert M3W2FakeAdapter.total_dispatches == 1, "reconciliation never dispatches"
        # Receipt binds every governed identity.
        receipt = outcome.receipt
        assert receipt.coordinator_id == f"{PROJECT_ID}:{MILESTONE_ID}"
        assert receipt.plan_authority == PLAN_AUTHORITY
        assert receipt.milestone_id == MILESTONE_ID
        assert receipt.work_item_id == "W1"
        assert receipt.canonical_task_id == cid
        assert receipt.card_digest == digest
        assert receipt.result_handoff_ref == cid
        assert receipt.prev_coordinator_revision + 1 == receipt.next_coordinator_revision
        assert receipt.receipt_digest == receipt.compute_digest()
        # Durable semantic working state.
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None
        assert state.wi_semantic_status["W1"] == WI_SEMANTIC_RECONCILED
        assert state.progression_revision == 1
        assert state.working_truth is not None and state.working_truth["digest"] == receipt.working_truth_digest
        assert state.reconciled_completions[cid]["receipt_digest"] == receipt.receipt_digest
        # ACK eligibility is causally bound to the stored receipt.
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id=cid,
            card_digest=digest,
        )
        assert R.ACK_BEFORE_SEMANTIC_RECONCILIATION is False
        assert R.ACK_REQUIRES_DURABLE_RECONCILIATION is True

    def test_card_pass_alone_never_suffices(self, world: World) -> None:
        """CARD SUCCESS with governed validation FAIL still blocks progression."""
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        assert card.outcome.value == "success"
        outcome = _reconcile(
            world, view, cid, card.compute_card_digest(), "W1",
            verdict=FocusedValidationVerdict.FAIL,
        )
        assert outcome.receipt.progression_disposition == R.DISPOSITION_PROGRESSION_BLOCKED
        assert outcome.blocked_work_items == ("W1",)
        assert outcome.progression_complete == ()
        # ACK attests durable reconciliation of the completion, never acceptance.
        assert outcome.ack_eligible is True
        assert R.WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY is False

    def test_next_hint_has_no_authority(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(
            world, handle, ["W1"], "W1", next_hint="dispatch W99 immediately with full shell"
        )
        outcome = _reconcile(world, view, cid, card.compute_card_digest(), "W1")
        assert outcome.receipt.progression_disposition == R.DISPOSITION_PROGRESSION_COMPLETE
        assert outcome.ready_work_items == ()
        assert M3W2FakeAdapter.total_dispatches == 1
        assert R.WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY is False

    def test_failed_card_blocks_but_reconciles(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1", fail=True)
        assert card.outcome.value != "success"
        outcome = _reconcile(world, view, cid, card.compute_card_digest(), "W1")
        assert outcome.receipt.progression_disposition == R.DISPOSITION_PROGRESSION_BLOCKED
        assert outcome.ack_eligible is True, "delivery reconciled; ACK is not acceptance"

    def test_model_ack_string_alone_insufficient(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        digest = card.compute_card_digest()
        forged = f"AOTA_COMPLETION_ACK_V1 canonical_task_id={cid} card_digest={digest}"
        assert parse_completion_ack(forged, canonical_task_id=cid, card_digest=digest) is True
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id=cid,
            card_digest=digest,
        ) is False, "matching string without durable receipt grants nothing"
        assert R.MODEL_ACK_STRING_ALONE_SUFFICIENT is False

    def test_typed_service_rejects_untyped_evidence(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        with pytest.raises(TypeError):
            reconcile_worker_completion(
                store=world.coord_store,
                execution_store=world.exec_store,
                coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
                canonical_task_id=cid,
                card_digest=card.compute_card_digest(),
                live_plan_view=view,
                governed_evidence={"validation_evidence": "PASS"},  # type: ignore[arg-type]
            )

    def test_coordinator_method_integration(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        outcome = handle.reconcile_completion(
            cid, card.compute_card_digest(), view, _evidence("W1"), _resolver(["W1"])
        )
        assert outcome.ack_eligible is True
        with pytest.raises(CoordinatorBindingError):
            handle.reconcile_completion(cid, card.compute_card_digest(), None, _evidence("W1"))

    def test_composition_wiring(self, tmp_path: Path) -> None:
        w = World(tmp_path, name="composition")
        try:
            view = _view(["W1"])
            handle = w.activate(view)
            cid, card = _run_to_terminal(w, handle, ["W1"], "W1")
            outcome = reconcile_task_main_completion(
                coordinator_store=w.coord_store,
                execution_store=w.exec_store,
                coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
                canonical_task_id=cid,
                card_digest=card.compute_card_digest(),
                live_plan_view=view,
                governed_evidence=_evidence("W1"),
                handoff_resolver=_resolver(["W1"]),
            )
            assert outcome.ack_eligible is True
            assert outcome.receipt.receipt_digest == outcome.receipt.compute_digest()
        finally:
            w.close()


# ---------------------------------------------------------------------------
# Exact binding: cross / out-of-order / contradictory fail closed
# ---------------------------------------------------------------------------


class TestExactBinding:
    def test_out_of_order_completion_rejected(self, world: World) -> None:
        view = _view(["W1"])
        world.activate(view)
        with pytest.raises(ReconciliationError, match="no dispatched Work Item"):
            _reconcile(world, view, "aota_forge:M3:GHOST:attempt-1", "d" * 64, "W1")
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id="aota_forge:M3:GHOST:attempt-1",
            card_digest="d" * 64,
        ) is False

    def test_non_terminal_completion_rejected(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        handle.dispatch_ready(_resolver(["W1"]))
        cid = handle.active_bindings()["W1"]["canonical_task_id"]
        with pytest.raises(ReconciliationError, match="not pending reconciliation"):
            _reconcile(world, view, cid, "d" * 64, "W1")

    def test_wrong_digest_rejected(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, _ = _run_to_terminal(world, handle, ["W1"], "W1")
        with pytest.raises(ReconciliationError, match="digest"):
            _reconcile(world, view, cid, "0" * 64, "W1")

    def test_cross_work_item_evidence_rejected(self, world: World) -> None:
        view = _view(["W1", "W2"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1", "W2"], "W1")
        # Governed validation evidence addressing another Work Item is refused.
        with pytest.raises(ReconciliationError, match="not reconciled Work Item"):
            reconcile_worker_completion(
                store=world.coord_store,
                execution_store=world.exec_store,
                coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
                canonical_task_id=cid,
                card_digest=card.compute_card_digest(),
                live_plan_view=view,
                governed_evidence=_evidence("W2"),
                handoff_resolver=_resolver(["W1", "W2"]),
            )
        assert world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}").reconciled_completions == {}

    def test_cross_milestone_view_rejected(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        other = _view(["W1"])
        object.__setattr__(other, "milestone_id", "M9")
        with pytest.raises(PlanDriftError):
            _reconcile(world, other, cid, card.compute_card_digest(), "W1")

    def test_cross_coordinator_rejected(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        with pytest.raises(CoordinatorNotFoundError):
            reconcile_worker_completion(
                store=world.coord_store,
                execution_store=world.exec_store,
                coordinator_id="aota_forge:M9",
                canonical_task_id=cid,
                card_digest=card.compute_card_digest(),
                live_plan_view=view,
                governed_evidence=_evidence("W1"),
            )

    def test_cross_plan_authority_rejected(self, tmp_path: Path) -> None:
        w = World(tmp_path, name="xauthority")
        try:
            view = _view(["W1"])
            handle = w.activate(view)
            cid, card = _run_to_terminal(w, handle, ["W1"], "W1")
            digest, revision = _plan_digest("other-authority")
            drifted = MilestonePlanView(
                plan_authority="other/repo#1",
                plan_digest=digest,
                plan_source_revision=revision,
                milestone_id=MILESTONE_ID,
                entry_base=ENTRY_BASE,
                graph=_graph(["W1"]),
                milestone_user_approval_satisfied=True,
            )
            with pytest.raises(PlanDriftError):
                _reconcile(w, drifted, cid, card.compute_card_digest(), "W1")
        finally:
            w.close()

    def test_contradictory_replay_fails_closed(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        first = _reconcile(world, view, cid, card.compute_card_digest(), "W1")
        assert first.ack_eligible is True
        with pytest.raises(ContradictoryCompletionError):
            _reconcile(world, view, cid, "f" * 64, "W1")
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None
        assert state.reconciled_completions[cid]["card_digest"] == card.compute_card_digest()
        assert state.progression_revision == 1, "no second semantic mutation"
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id=cid,
            card_digest="f" * 64,
        ) is False

    def test_duplicate_delivery_replays_identically(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        digest = card.compute_card_digest()
        first = _reconcile(world, view, cid, digest, "W1")
        second = _reconcile(world, view, cid, digest, "W1")
        assert second.replayed is True
        assert second.receipt.receipt_digest == first.receipt.receipt_digest
        assert second.ack_eligible is True and second.ack_token == first.ack_token
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None and state.progression_revision == 1
        assert R.SEMANTIC_RECONCILIATION_IDEMPOTENT is True


# ---------------------------------------------------------------------------
# Restart windows S1-S8
# ---------------------------------------------------------------------------


class TestRestartWindows:
    def test_s1_crash_before_reconciliation(self, tmp_path: Path) -> None:
        w1 = World(tmp_path, name="s1")
        try:
            view = _view(["W1", "W2"], [["W1", "W2"]])
            first = w1.activate(view)
            cid, card = _run_to_terminal(w1, first, ["W1", "W2"], "W1")
            coordinator_id = first.coordinator_id
            w2 = w1.reopen()
            try:
                recovered = w2.recover(view, coordinator_id)
                assert recovered.refresh().wi_status["W1"] == (
                    WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value
                )
                outcome = _reconcile(w2, view, cid, card.compute_card_digest(), "W1", work_items=["W1", "W2"])
                assert outcome.ack_eligible is True and outcome.replayed is False
                assert outcome.ready_work_items == ("W2",)
            finally:
                w2.close()
        finally:
            with suppress(Exception):
                w1.close()

    def test_s2_crash_before_cas_retries_safely(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        digest = card.compute_card_digest()
        original = world.coord_store.compare_and_swap
        calls: list[str] = []

        def _flaky(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            if not calls:
                calls.append("crash")
                raise CoordinatorPersistenceFailureError("simulated crash before CAS")
            return original(*args, **kwargs)

        monkeypatch.setattr(world.coord_store, "compare_and_swap", _flaky)
        with pytest.raises(CoordinatorPersistenceFailureError):
            _reconcile(world, view, cid, digest, "W1")
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id=cid,
            card_digest=digest,
        ) is False, "no durable mutation, ACK false"
        outcome = _reconcile(world, view, cid, digest, "W1")
        assert outcome.ack_eligible is True and outcome.replayed is False

    def test_s3_post_reconciliation_pre_ack_crash(self, tmp_path: Path) -> None:
        transport = ScriptedTransport()
        w1 = World(tmp_path, name="s3", use_completion=True, transport=transport)
        try:
            view = _view(["W1"])
            first = w1.activate(view)
            cid, card = _run_to_terminal(w1, first, ["W1"], "W1")
            digest = card.compute_card_digest()
            outcome = _reconcile(w1, view, cid, digest, "W1")
            assert outcome.ack_eligible is True
            # Crash after semantic CAS, before M2 delivery ACK CAS.
            w2 = w1.reopen(use_completion=True, transport=transport)
            try:
                recovered = w2.recover(view, first.coordinator_id)
                assert recovered.refresh().wi_status["W1"] == (
                    WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value
                )
                replay = _reconcile(w2, view, cid, digest, "W1")
                assert replay.replayed is True
                assert replay.receipt.receipt_digest == outcome.receipt.receipt_digest
                state = w2.coord_store.get(first.coordinator_id)
                assert state is not None and state.progression_revision == 1
                # ACK can now safely complete through the existing M2 machine.
                transport.ack_lines[cid] = replay.ack_token or outcome.ack_token or ""
                report = w2.completion.deliver_pending_once()
                assert report.outcomes[cid] == DELIVER_ACKNOWLEDGED
                final = w2.exec_store.get(cid)
                assert final is not None and final.delivery_state == DeliveryState.ACKNOWLEDGED
                assert R.M2_DELIVERY_STATE_REUSED is True
                assert R.NEW_DELIVERY_STATE_MACHINE_CREATED is False
            finally:
                w2.close()
        finally:
            with suppress(Exception):
                w1.close()

    def test_s4_post_ack_restart(self, tmp_path: Path) -> None:
        transport = ScriptedTransport()
        w1 = World(tmp_path, name="s4", use_completion=True, transport=transport)
        try:
            view = _view(["W1"])
            first = w1.activate(view)
            cid, card = _run_to_terminal(w1, first, ["W1"], "W1")
            digest = card.compute_card_digest()
            outcome = _reconcile(w1, view, cid, digest, "W1")
            transport.ack_lines[cid] = outcome.ack_token or ""
            assert w1.completion.deliver_pending_once().outcomes[cid] == DELIVER_ACKNOWLEDGED
            w2 = w1.reopen(use_completion=True, transport=transport)
            try:
                w2.recover(view, first.coordinator_id)
                record = w2.exec_store.get(cid)
                assert record is not None and record.delivery_state == DeliveryState.ACKNOWLEDGED
                report = w2.completion.deliver_pending_once()
                assert cid not in report.outcomes, "acknowledged records are never redelivered"
                replay = _reconcile(w2, view, cid, digest, "W1")
                assert replay.replayed is True
                assert replay.receipt.receipt_digest == outcome.receipt.receipt_digest
                state = w2.coord_store.get(first.coordinator_id)
                assert state is not None and state.progression_revision == 1
            finally:
                w2.close()
        finally:
            with suppress(Exception):
                w1.close()

    def test_s5_duplicate_card(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        digest = card.compute_card_digest()
        first = _reconcile(world, view, cid, digest, "W1")
        second = _reconcile(world, view, cid, digest, "W1")
        assert second.replayed is True
        assert second.receipt.receipt_digest == first.receipt.receipt_digest
        assert second.ack_token == first.ack_token
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None and state.progression_revision == 1

    def test_s6_contradictory_card(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        _reconcile(world, view, cid, card.compute_card_digest(), "W1")
        with pytest.raises(ContradictoryCompletionError):
            _reconcile(world, view, cid, "e" * 64, "W1")

    def test_s7_out_of_order_card(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        world.activate(view)
        with pytest.raises(ReconciliationError):
            _reconcile(world, view, _cid("W2"), "a" * 64, "W2", work_items=["W1", "W2"])

    def test_s8_plan_drift(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        drifted_digest, _ = _plan_digest("drifted-marker")
        drifted = MilestonePlanView(
            plan_authority=PLAN_AUTHORITY,
            plan_digest=drifted_digest,
            plan_source_revision="rev-m3w2-drift",
            milestone_id=MILESTONE_ID,
            entry_base=ENTRY_BASE,
            graph=_graph(["W1"]),
            milestone_user_approval_satisfied=True,
        )
        with pytest.raises(PlanDriftError):
            _reconcile(world, drifted, cid, card.compute_card_digest(), "W1")
        assert ack_eligible_for(
            store=world.coord_store,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
            canonical_task_id=cid,
            card_digest=card.compute_card_digest(),
        ) is False, "stale semantic apply refused; ACK false"


# ---------------------------------------------------------------------------
# Source-ready progression -> integrated review (no auto-dispatch)
# ---------------------------------------------------------------------------


class TestSourceReadyProgression:
    def test_all_complete_yields_integrated_review_required(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        handle = world.activate(view)
        cid1, card1 = _run_to_terminal(world, handle, ["W1", "W2"], "W1")
        first = _reconcile(world, view, cid1, card1.compute_card_digest(), "W1", work_items=["W1", "W2"])
        assert first.integrated_review_required is False
        assert first.ready_work_items == ("W2",)
        cid2, card2 = _run_to_terminal(world, handle, ["W1", "W2"], "W2")
        second = _reconcile(world, view, cid2, card2.compute_card_digest(), "W2", work_items=["W1", "W2"])
        assert second.receipt.progression_disposition == R.DISPOSITION_PROGRESSION_COMPLETE
        assert second.progression_complete == ("W1", "W2")
        assert second.integrated_review_required is True
        assert second.user_gate_required is False
        assert second.milestone_closure_ready is False, "review still required; no direct acceptance"
        assert M3W2FakeAdapter.total_dispatches == 2, "reviewer is not auto-dispatched"
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None and state.progression_revision == 2


# ---------------------------------------------------------------------------
# Reviewer path: PASS / repair / plan-change / environment / repeated
# ---------------------------------------------------------------------------


class TestReviewDispositions:
    def _all_source_ready(self, world: World, view: MilestonePlanView, items: list[str]) -> TaskMainCoordinator:
        handle = world.activate(view)
        for wi in items:
            cid, card = _run_to_terminal(world, handle, items, wi)
            _reconcile(world, view, cid, card.compute_card_digest(), wi, work_items=items)
        return handle

    def test_review_pass_toward_closure(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        self._all_source_ready(world, view, ["W1", "W2"])
        rcid, rcard = _reviewer_completion(world)
        governed = _governed_review(rcard, (), expected_final_frontier=SemanticReference(ref="frontier-m3-rv1"))
        outcome = _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)
        assert outcome.receipt.progression_disposition == R.DISPOSITION_REVIEW_READY_FOR_STEWARD
        assert outcome.milestone_closure_ready is True
        assert outcome.user_gate_required is True, "Steward/user decision still required"
        assert outcome.rv2_required is False
        assert outcome.physical_dispatch_attempts == 0
        assert outcome.ack_eligible is True
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None and state.status == CoordinatorStatus.ACTIVE
        assert M3W2FakeAdapter.total_dispatches == 3, "no steward/closure side effects"

    def test_source_repair_finding_derives_no_execution(self, world: World) -> None:
        view = _view(["W1"])
        self._all_source_ready(world, view, ["W1"])
        rcid, rcard = _reviewer_completion(world)
        before = M3W2FakeAdapter.total_dispatches
        finding = _finding("F-SRC-001", blocking=True)
        governed = _governed_review(rcard, (finding,))
        outcome = _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)
        assert outcome.receipt.progression_disposition == R.DISPOSITION_REVIEW_REPAIR_REQUIRED
        assert outcome.user_gate_required is False
        assert outcome.rv2_required is False
        assert M3W2FakeAdapter.total_dispatches == before, "repair execution count is 0"
        assert outcome.physical_dispatch_attempts == 0
        assert R.REPAIR_AUTOMATION_IMPLEMENTED is False

    def test_plan_change_stops_at_user_gate(self, world: World) -> None:
        view = _view(["W1"])
        self._all_source_ready(world, view, ["W1"])
        rcid, rcard = _reviewer_completion(world)
        finding = _finding("F-PLAN-001", blocking=True)
        governed = GovernedReviewEvidence(
            review_evidence=_review_evidence(rcard, (finding,)),
            review_findings=(finding,),
            review_task_handoff=_reviewer_handoff(),
            semantic_boundary_exceeded=True,
        )
        plan_before = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}").plan_digest  # type: ignore[union-attr]
        outcome = _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)
        assert outcome.receipt.progression_disposition == R.DISPOSITION_REVIEW_REPLAN_REQUIRED
        assert outcome.user_gate_required is True
        assert outcome.physical_dispatch_attempts == 0
        plan_after = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}").plan_digest  # type: ignore[union-attr]
        assert plan_after == plan_before, "no automatic Plan mutation"

    def test_environment_failure_is_not_source_defect(self, world: World) -> None:
        view = _view(["W1"])
        self._all_source_ready(world, view, ["W1"])
        rcid, rcard = _reviewer_completion(world, fail_env=True)
        before = M3W2FakeAdapter.total_dispatches
        assert rcard.mechanical_failure is not None
        governed = _governed_review(rcard, ())
        outcome = _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)
        assert outcome.receipt.progression_disposition == R.DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT
        assert outcome.user_gate_required is False
        assert M3W2FakeAdapter.total_dispatches == before, "no source repair dispatched"
        assert any("environment" in reason for reason in outcome.reasons)

    def test_repeated_failure_bounded_no_rv_loop(self, world: World) -> None:
        view = _view(["W1"])
        self._all_source_ready(world, view, ["W1"])
        rcid, rcard = _reviewer_completion(world)
        fingerprint = FailureFingerprint(
            milestone_ref=SemanticReference(ref=MILESTONE_ID),
            work_item_ref="W1",
            failure_domain=StopKind.MECHANICAL_FAILURE,
            failure_classification="flaky-hermes-reentry",
            task_identity=rcid,
        )
        governed = GovernedReviewEvidence(
            review_evidence=_review_evidence(rcard, ()),
            review_findings=(),
            review_task_handoff=_reviewer_handoff(),
            pre_failure_fingerprints=(fingerprint,),
            post_failure_fingerprints=(fingerprint,),
        )
        outcome = _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)
        assert outcome.receipt.review_workflow_disposition == WorkflowDisposition.REPLAN_REQUIRED.value
        assert outcome.user_gate_required is True
        assert outcome.rv2_required is False, "no unbounded RV3/RV4 loop"
        assert outcome.physical_dispatch_attempts == 0

    def test_raw_reviewer_prose_never_authority(self, world: World) -> None:
        view = _view(["W1"])
        self._all_source_ready(world, view, ["W1"])
        rcid, rcard = _reviewer_completion(world)
        tampered = MilestoneReviewEvidence(
            milestone_ref=SemanticReference(ref=MILESTONE_ID),
            review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=SemanticReference(ref="frontier-m3-rv1"),
            review_result_ref=rcard.result_handoff_ref,
            review_result_digest="0" * 64,
            finding_refs=(),
        )
        governed = GovernedReviewEvidence(
            review_evidence=tampered,
            review_findings=(),
            review_task_handoff=_reviewer_handoff(),
        )
        with pytest.raises(ReconciliationError, match="digest"):
            _review_reconcile(world, view, rcid, rcard.compute_card_digest(), governed)

    def test_composition_review_wiring(self, tmp_path: Path) -> None:
        w = World(tmp_path, name="review-wire")
        try:
            view = _view(["W1"])
            handle = w.activate(view)
            cid, card = _run_to_terminal(w, handle, ["W1"], "W1")
            _reconcile(w, view, cid, card.compute_card_digest(), "W1")
            rcid, rcard = _reviewer_completion(w)
            governed = _governed_review(rcard, ())
            outcome = reconcile_task_main_review(
                coordinator_store=w.coord_store,
                execution_store=w.exec_store,
                coordinator_id=f"{PROJECT_ID}:{MILESTONE_ID}",
                reviewer_canonical_task_id=rcid,
                card_digest=rcard.compute_card_digest(),
                live_plan_view=view,
                governed_review=governed,
            )
            assert outcome.receipt.progression_disposition == R.DISPOSITION_REVIEW_READY_FOR_STEWARD
            assert outcome.ack_eligible is True
        finally:
            w.close()


# ---------------------------------------------------------------------------
# Receipt authority + schema + control surface
# ---------------------------------------------------------------------------


class TestReceiptAndAuthority:
    def test_receipt_roundtrip_and_tamper_fails_closed(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        outcome = _reconcile(world, view, cid, card.compute_card_digest(), "W1")
        payload = outcome.receipt.to_dict()
        assert CompletionReconciliationReceipt.from_dict(payload) == outcome.receipt
        tampered = dict(payload)
        tampered["progression_disposition"] = R.DISPOSITION_PROGRESSION_BLOCKED
        with pytest.raises(ValueError, match="digest mismatch"):
            CompletionReconciliationReceipt.from_dict(tampered)
        assert R.RECONCILIATION_RECEIPT_IS_PLAN_AUTHORITY is False
        assert R.RECONCILIATION_RECEIPT_IS_RESULT_AUTHORITY is False

    def test_schema_v2_strict_no_silent_migration(self, tmp_path: Path) -> None:
        assert COORDINATOR_STATE_SCHEMA_VERSION == 2
        v1_payload = {
            "schema_version": 1,
            "coordinator_id": "c",
            "plan_authority": "p",
            "plan_digest": "d",
            "milestone_id": "M3",
            "entry_base": "b",
            "origin_task_main_session_ref": "s",
            "project_id": "pr",
            "executor_id": "e",
            "work_items": ["W1"],
            "wi_status": {"W1": "PENDING"},
        }
        with pytest.raises(ValueError, match="schema_version must be exactly 2"):
            TaskMainCoordinatorState.from_dict(v1_payload)

    def test_no_worker_control_surface(self) -> None:
        assert R.WORKER_CAN_CALL_TASK_MAIN_CONTROL is False
        assert R.TASK_MAIN_RAW_SHELL_REQUIRED is False
        assert R.TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED is False
        assert R.TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE is False
        assert R.MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED is False
        assert R.NEW_GENERIC_WORKFLOW_STORE_CREATED is False
        assert R.CARD_FIRST is True
        assert R.RAW_WORKER_TRANSCRIPT_PRIMARY is False

    def test_ack_token_shape_reuses_m2_contract(self, world: World) -> None:
        view = _view(["W1"])
        handle = world.activate(view)
        cid, card = _run_to_terminal(world, handle, ["W1"], "W1")
        digest = card.compute_card_digest()
        token = build_ack_token(canonical_task_id=cid, card_digest=digest)
        assert token.startswith("AOTA_COMPLETION_ACK_V1 ")
        assert "ACK_V2" not in token
        assert parse_completion_ack(token, canonical_task_id=cid, card_digest=digest) is True
        assert parse_completion_ack(token, canonical_task_id=cid, card_digest="0" * 64) is False

    def test_working_truth_projection_reused(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        handle = world.activate(view)
        cid1, card1 = _run_to_terminal(world, handle, ["W1", "W2"], "W1")
        _reconcile(world, view, cid1, card1.compute_card_digest(), "W1", work_items=["W1", "W2"])
        cid2, card2 = _run_to_terminal(world, handle, ["W1", "W2"], "W2")
        outcome = _reconcile(world, view, cid2, card2.compute_card_digest(), "W2", work_items=["W1", "W2"])
        state = world.coord_store.get(f"{PROJECT_ID}:{MILESTONE_ID}")
        assert state is not None and state.working_truth is not None
        projection = state.working_truth["projection"]
        assert projection["project_ref"] == {"ref": PROJECT_ID}
        assert projection["milestone_ref"] == {"ref": MILESTONE_ID}
        result_refs = {entry["ref"] for entry in projection["result_refs"]}
        assert {cid1, cid2} <= result_refs
        assert state.working_truth["digest"] == outcome.receipt.working_truth_digest
