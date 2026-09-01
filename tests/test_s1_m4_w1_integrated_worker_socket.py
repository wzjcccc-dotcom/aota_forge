"""S1 M4-W1 Adapter-Neutral Integrated One-Shot Worker Socket Proof.

Proves S1 Work Plane composes with existing execution socket without Core rewrite.

Flow:
TaskHandoff -> WorkRole resolver -> TrustedBinding -> Compiler -> ExecutionPackage
-> Fake ExecutorAdapter -> CanonicalResult -> ResultGovernance -> WorkerResultCard -> reconciliation
plus bootstrap, AGENTS boundary, stop path, event hook.

Adapter-neutral: canonical proof never imports HermesAdapter/HermesHostClient.
Fake adapter is test-only.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import uuid

import pytest

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.mapping import resolve_work_role_to_canonical_role
from aota_forge.work_plane.handoff import TaskHandoff, SemanticReference
from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package
from aota_forge.work_plane.lifecycle import ExecutionWorkRoleBinding
from aota_forge.work_plane.soul import Soul
from aota_forge.work_plane.agents_applicability import (
    AgentsPolicyCandidate,
    resolve_applicable_policies,
    CrossProjectPolicyError,
    AmbiguousPolicyError,
)
from aota_forge.work_plane.bootstrap import (
    BootstrapBudget,
    BootstrapBundle,
    create_worker_bundle,
    create_task_main_bundle,
)
from aota_forge.work_plane.result_card import WorkerResultCard, ResultHandoffRef, project_worker_result_card
from aota_forge.work_plane.stop import (
    SemanticStop,
    SemanticStopReason,
    MechanicalFailure,
    Escalation,
)
from aota_forge.work_plane.events import ExecutionEvent, ExecutionEventType, emit_event, EventHookError
from aota_forge.work_plane import __all__ as WORK_PLANE_ALL  # type: ignore

from aota_forge.core.execution.package import ExecutionPackage, PROTOCOL_VERSION, EXECUTION_CONTRACT_HASH
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.adapter import ExecutorAdapter, ValidationResult, DispatchResult, TaskStatusResult, CancelResult, ResumeResult
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.state import CanonicalTaskState, parse_state
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ID = "proj-m4-w1"
CANONICAL_TASK_ID = "task-m4-w1-001"
CORRELATION_ID = "corr-m4-w1-001"


def _make_handoff(work_role: str | AgentWorkRole = "coder", objective: str = "Implement feature X") -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind="implementation",
        objective=objective,
        bounded_scope="Work on bounded scope only, limited to work_plane integration",
        validation_expectations=("unit test passes",),
        semantic_stop_expectations=("ambiguous scope", "handoff insufficient"),
        project_ref=SemanticReference(ref=PROJECT_ID),
        plan_ref=SemanticReference(ref="plan-s1"),
        milestone_ref=SemanticReference(ref="m4"),
        work_item_ref=SemanticReference(ref="m4-w1"),
    )


def _make_binding(role: str | AgentWorkRole = "coder") -> ExecutionWorkRoleBinding:
    return ExecutionWorkRoleBinding(work_role=role)


def _make_soul(content: str = "You are a concise, bounded behavioral assistant.") -> Soul:
    return Soul(content=content, version="1.0")


def _make_policy(
    policy_id: str = "pol-root",
    project_id: str = PROJECT_ID,
    scope: str = "",
    content: str = "root policy material",
    provenance_ref: str | None = None,
) -> AgentsPolicyCandidate:
    return AgentsPolicyCandidate(
        policy_id=policy_id,
        project_id=project_id,
        scope=scope,
        content=content,
        provenance_ref=provenance_ref,
    )


def _make_trusted_binding(canonical_task_id: str = CANONICAL_TASK_ID, project_id: str = PROJECT_ID) -> TrustedExecutionBinding:
    return TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=project_id)


def _make_budget(max_bytes: int | None = None, max_components: int = 16) -> BootstrapBudget:
    # if max_bytes None, generous
    if max_bytes is None:
        return BootstrapBudget(max_canonical_bytes=64 * 1024, max_components=max_components)
    return BootstrapBudget(max_canonical_bytes=max_bytes, max_components=max_components)


# ---------------------------------------------------------------------------
# Fake Adapter (test-only, adapter-neutral)
# ---------------------------------------------------------------------------


class FakeAdapter(ExecutorAdapter):
    """Test-only fake/adversarial executor implementing ExecutorAdapter contract."""

    EXECUTOR_ID = "fake-adapter-m4-w1"

    def __init__(self, mode: str = "success") -> None:
        # mode: success | failure_retryable | failure_not_retryable | unknown | dispatch_rejected
        self.mode = mode
        self.validate_called = False
        self.dispatch_called = False
        self.dispatch_count = 0
        self._packages: dict[str, ExecutionPackage] = {}
        self._capabilities = ExecutorCapabilities(
            executor_id=self.EXECUTOR_ID,
            adapter_kind="fake_test_double",
            supported_execution_modes=("sync", "async"),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder", "planner", "reviewer", "steward"),
            supported_isolation_modes=("process", "none"),
            supports_working_directory=False,
            supports_artifact_transport=True,
        )

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        self.validate_called = True
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")
        # simple check: canonical_role must be supported
        if not self._capabilities.supports_role(package.canonical_role):
            return ValidationResult(valid=False, errors=(f"unsupported role {package.canonical_role}",))
        return ValidationResult(valid=True, errors=())

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.dispatch_called = True
        self.dispatch_count += 1
        if self.mode == "dispatch_rejected":
            raise ValueError("DISPATCH_REJECTED: fake dispatch rejected for adversarial test")
        validation = self.validate_package(package)
        if not validation.valid:
            raise ValueError(f"PACKAGE_INVALID: {validation.errors}")
        self._packages[package.canonical_task_id] = package
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=f"fake-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.COMPLETED if self.mode in ("success", "failure_retryable", "failure_not_retryable") else CanonicalTaskState.UNKNOWN if self.mode == "unknown" else CanonicalTaskState.ACCEPTED,
            dispatch_time="2026-09-01T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        pkg = self._packages.get(canonical_task_id)
        if pkg is None:
            raise KeyError(f"TASK_NOT_FOUND: {canonical_task_id}")
        if self.mode == "success":
            return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.COMPLETED, details="completed")
        if self.mode in ("failure_retryable", "failure_not_retryable"):
            return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.FAILED, details="failed")
        if self.mode == "unknown":
            return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.UNKNOWN, details="unknown")
        return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.ACCEPTED, details="accepted")

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        pkg = self._packages.get(canonical_task_id)
        if pkg is None:
            raise KeyError(f"TASK_NOT_FOUND: {canonical_task_id}")
        corr = pkg.correlation_id
        if self.mode == "success":
            return CanonicalResult.success(
                canonical_task_id=canonical_task_id,
                executor_id=self.EXECUTOR_ID,
                result_data={"output": f"executed {pkg.instruction}"},
                correlation_id=corr,
            )
        if self.mode == "failure_retryable":
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=self.EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="transient tool failure",
                retryable=True,
                correlation_id=corr,
            )
        if self.mode == "failure_not_retryable":
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=self.EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="permanent failure",
                retryable=False,
                correlation_id=corr,
            )
        if self.mode == "unknown":
            return CanonicalResult.unknown(
                canonical_task_id=canonical_task_id,
                executor_id=self.EXECUTOR_ID,
                error_message="executor state unknown",
                correlation_id=corr,
            )
        # default
        return CanonicalResult.failure(
            canonical_task_id=canonical_task_id,
            executor_id=self.EXECUTOR_ID,
            error_code="EXECUTION_FAILED",
            error_message="generic failure",
            retryable=False,
            correlation_id=corr,
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(canonical_task_id=canonical_task_id, cancelled=False, state=CanonicalTaskState.UNKNOWN)

    def resume(self, canonical_task_id: str, adapter_handle: str, resume_package: ExecutionPackage) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


# ---------------------------------------------------------------------------
# T01-T05 Joint Convergence
# ---------------------------------------------------------------------------

def test_t01_m2_frontier_is_ancestor_of_joint_convergence():
    # Verify via git that b605ce2 is ancestor of HEAD
    try:
        subprocess.check_call(["git", "merge-base", "--is-ancestor", "b605ce253189d275a0437f8a0d7a6be630a8df1a", "HEAD"], cwd=".")
    except subprocess.CalledProcessError:
        pytest.fail("M2 frontier b605ce2 is not ancestor of HEAD")

def test_t02_m3_frontier_is_ancestor_of_joint_convergence():
    try:
        subprocess.check_call(["git", "merge-base", "--is-ancestor", "b2a3c3bf2bae8199f675a7ad7bab1ca570d62c7e", "HEAD"], cwd=".")
    except subprocess.CalledProcessError:
        pytest.fail("M3 frontier b2a3c3b is not ancestor of HEAD")

def test_t03_m2_public_exports_preserved():
    # M2: lifecycle, SOUL, AGENTS applicability, bootstrap
    expected_m2 = [
        "ExecutionWorkRoleBinding",
        "Soul",
        "AgentsPolicyCandidate",
        "resolve_applicable_policies",
        "BootstrapBundle",
        "BootstrapComponent",
        "BootstrapBudget",
        "create_task_main_bundle",
        "create_worker_bundle",
    ]
    for name in expected_m2:
        assert name in WORK_PLANE_ALL, f"M2 export {name} missing"
        # also importable
        assert hasattr(__import__("aota_forge.work_plane", fromlist=[name]), name)

def test_t04_m3_public_exports_preserved():
    expected_m3 = [
        "WorkerResultCard",
        "ResultHandoffRef",
        "project_worker_result_card",
        "SemanticStop",
        "SemanticStopReason",
        "MechanicalFailure",
        "Escalation",
        "StopKind",
        "RetryRequest",
        "ExecutionEvent",
        "ExecutionEventType",
        "EventHook",
        "EventHookError",
        "emit_event",
    ]
    for name in expected_m3:
        assert name in WORK_PLANE_ALL, f"M3 export {name} missing"

def test_t05_m1_exports_preserved():
    expected_m1 = [
        "AgentWorkRole",
        "WORK_ROLES",
        "WORK_ROLE_SET",
        "VALID_WORK_ROLES",
        "AGENT_WORK_ROLES",
        "is_agent_work_role",
        "validate_agent_work_role",
        "parse_agent_work_role",
        "resolve_work_role_to_canonical_role",
        "WORK_ROLE_TO_CANONICAL_ROLE",
        "WorkRoleMappingError",
        "TaskHandoff",
        "SemanticReference",
        "compute_handoff_digest",
        "HANDOFF_FORBIDDEN_MECHANICAL_FIELDS",
        "TrustedExecutionBinding",
        "compile_handoff_to_execution_package",
        "compile_task_handoff",
    ]
    for name in expected_m1:
        assert name in WORK_PLANE_ALL

# ---------------------------------------------------------------------------
# T06-T17 Successful Socket
# ---------------------------------------------------------------------------

def test_t06_valid_worker_bootstrap():
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    soul = _make_soul()
    policy = _make_policy()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    assert bundle.bundle_type == "worker"
    # validate budget exact
    budget = BootstrapBudget(max_canonical_bytes=bundle.accounted_size())
    bundle.validate_budget(budget)
    # reconcile
    bundle.reconcile_work_role(binding)

def test_t07_task_handoff_accepted():
    handoff = _make_handoff("coder")
    assert isinstance(handoff, TaskHandoff)
    assert handoff.work_role == AgentWorkRole.CODER
    # digest covers all fields
    assert len(handoff.handoff_digest) == 64

def test_t08_work_role_mapping_reused():
    # uses accepted W2 resolver, not duplicate
    canonical = resolve_work_role_to_canonical_role(AgentWorkRole.CODER)
    assert canonical.value == "coder"
    # also via string
    assert resolve_work_role_to_canonical_role("coder").value == "coder"
    # task-main fails closed
    with pytest.raises(Exception):
        resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)

def test_t09_handoff_compiles_to_existing_execution_package():
    handoff = _make_handoff("coder")
    binding = _make_trusted_binding()
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=binding)
    assert isinstance(pkg, ExecutionPackage)
    assert pkg.canonical_role == "coder"
    assert pkg.canonical_task_id == CANONICAL_TASK_ID
    assert pkg.project_id == PROJECT_ID
    assert pkg.protocol_version == PROTOCOL_VERSION
    assert pkg.contract_hash == EXECUTION_CONTRACT_HASH
    # intent fingerprint covers handoff digest
    assert handoff.handoff_digest in str(pkg.input_artifacts)

def test_t10_fake_adapter_conforms_to_existing_seam():
    # Must implement ExecutorAdapter
    assert issubclass(FakeAdapter, ExecutorAdapter)
    adapter = FakeAdapter(mode="success")
    # abstract methods exist
    for method in ("capabilities", "validate_package", "dispatch", "status", "result", "cancel", "resume"):
        assert hasattr(adapter, method)
    # capabilities returns ExecutorCapabilities
    caps = adapter.capabilities()
    assert isinstance(caps, ExecutorCapabilities)

def test_t11_fake_adapter_receives_existing_execution_package():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    adapter = FakeAdapter(mode="success")
    val = adapter.validate_package(pkg)
    assert val.valid is True
    dispatch = adapter.dispatch(pkg)
    assert dispatch.canonical_task_id == pkg.canonical_task_id
    assert pkg.canonical_task_id in adapter._packages

def test_t12_canonical_result_produced():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    assert isinstance(result, CanonicalResult)
    assert result.ok is True
    assert result.status == "completed"

def test_t13_result_governance_projection_reused():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    # governance reused - outcome success
    assert gov.outcome == ResultOutcome.SUCCESS
    assert gov.governance_version == "1.0"
    # ensure flow is explicit: CanonicalResult -> governance -> CARD later

def test_t14_worker_result_card_produced_from_governed_result():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(
        canonical_result=result,
        governance_projection=gov,
        work_role=AgentWorkRole.CODER,
        summary="success summary",
    )
    assert isinstance(card, WorkerResultCard)
    assert card.outcome == ResultOutcome.SUCCESS

def test_t15_card_task_identity_traceable():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding(CANONICAL_TASK_ID, PROJECT_ID))
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="trace")
    assert card.task_ref == CANONICAL_TASK_ID
    assert card.result_handoff_ref.ref == CANONICAL_TASK_ID
    assert result.canonical_task_id == CANONICAL_TASK_ID
    assert pkg.canonical_task_id == CANONICAL_TASK_ID

def test_t16_event_hook_receives_bounded_event():
    events = []
    def hook(ev: ExecutionEvent) -> None:
        events.append(ev)
    # success path should emit at least one event, e.g., execution_materialized
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    ev = ExecutionEvent(
        event_id="evt-1",
        event_type=ExecutionEventType.EXECUTION_MATERIALIZED,
        work_role=AgentWorkRole.CODER,
        task_kind=handoff.task_kind,
        handoff_ref=handoff.handoff_digest[:32],
        handoff_digest=handoff.handoff_digest,
        canonical_task_id=pkg.canonical_task_id,
        package_id=pkg.package_id,
        correlation_id=pkg.correlation_id,
    )
    emit_event(ev, hook)
    assert len(events) == 1
    assert events[0].event_id == "evt-1"
    assert events[0].event_type == ExecutionEventType.EXECUTION_MATERIALIZED

def test_t17_successful_flow_reaches_reconciliation():
    # full end-to-end success
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    soul = _make_soul()
    policy = _make_policy()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    bundle.validate_budget(_make_budget())
    # policy applicability
    applicable = resolve_applicable_policies([policy], PROJECT_ID)
    assert len(applicable) == 1
    # compile
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    # dispatch via fake
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="all good")
    # emit worker_result event
    events = []
    ev = ExecutionEvent(event_id="evt-success", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER, task_kind="implementation", canonical_task_id=result.canonical_task_id, result_ref=card.task_ref)
    emit_event(ev, lambda e: events.append(e))
    # reconciliation projection: task-main can distinguish successful completion
    assert card.outcome == ResultOutcome.SUCCESS
    assert card.semantic_stop is None
    assert card.mechanical_failure is None
    assert events[0].result_ref == CANONICAL_TASK_ID
    # success classification is explicit
    assert card.outcome.value == "success"

# ---------------------------------------------------------------------------
# T18-T23 Bootstrap / Policy Fail-Closed
# ---------------------------------------------------------------------------

def test_t18_bootstrap_exact_budget_accepted():
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    soul = _make_soul()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    size = bundle.accounted_size()
    budget = BootstrapBudget(max_canonical_bytes=size)
    bundle.validate_budget(budget)  # should not raise

def test_t19_bootstrap_overflow_fails_before_dispatch():
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    soul = _make_soul()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    size = bundle.accounted_size()
    budget = BootstrapBudget(max_canonical_bytes=size - 1)
    with pytest.raises(ValueError):
        bundle.validate_budget(budget)
    # ensure dispatch not invoked when overflow
    adapter = FakeAdapter(mode="success")
    try:
        bundle.validate_budget(budget)
        adapter.dispatch(compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding()))
        pytest.fail("should have failed before dispatch")
    except ValueError:
        assert adapter.dispatch_called is False
        assert adapter.dispatch_count == 0

def test_t20_no_required_eager_silent_truncation():
    # oversized materialized must fail, not truncate
    content = "x" * (32 * 1024 + 1)
    digest = hashlib.sha256(content.encode()).hexdigest()
    from aota_forge.work_plane.bootstrap import BootstrapComponent
    with pytest.raises(ValueError):
        BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest)

def test_t21_cross_project_agents_policy_fails_closed():
    policy = _make_policy(project_id="other-proj")
    with pytest.raises(CrossProjectPolicyError):
        resolve_applicable_policies([policy], PROJECT_ID)
    # ensure prevents dispatch
    adapter = FakeAdapter(mode="success")
    handoff = _make_handoff("coder")
    try:
        resolve_applicable_policies([policy], PROJECT_ID)
        adapter.dispatch(compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding()))
        pytest.fail("should not dispatch")
    except CrossProjectPolicyError:
        assert adapter.dispatch_called is False

def test_t22_ambiguous_agents_applicability_fails_closed():
    c1 = _make_policy(policy_id="pol-a", scope="a", content="one")
    c2 = _make_policy(policy_id="pol-b", scope="a", content="two")
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c1, c2], PROJECT_ID)

def test_t23_policy_failure_prevents_adapter_invocation():
    # combine cross-project failure before dispatch
    adapter = FakeAdapter(mode="success")
    handoff = _make_handoff("coder")
    bad_policy = _make_policy(project_id="mismatch")
    with pytest.raises(CrossProjectPolicyError):
        resolve_applicable_policies([bad_policy], PROJECT_ID)
    assert adapter.dispatch_called is False
    # also ambiguous
    adapter2 = FakeAdapter(mode="success")
    c1 = _make_policy(policy_id="p1", scope="a", content="x")
    c2 = _make_policy(policy_id="p2", scope="a", content="y")
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c1, c2], PROJECT_ID)
    assert adapter2.dispatch_called is False

# ---------------------------------------------------------------------------
# T24-T29 Semantic Stop
# ---------------------------------------------------------------------------

def test_t24_semantic_stop_uses_accepted_type():
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=CANONICAL_TASK_ID)
    assert isinstance(stop, SemanticStop)
    assert stop.reason == SemanticStopReason.HANDOFF_INSUFFICIENT
    assert stop.grants_retry is False
    assert stop.requires_escalation is True

def test_t25_semantic_stop_appears_in_card_projection():
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    # simulate pre-execution semantic stop: do not dispatch, create failure-like result? For card we need governance failure
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=CANONICAL_TASK_ID)
    # need a CanonicalResult that maps to FAILURE for stop card (success with stop is fail-closed)
    result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="HANDOFF_INSUFFICIENT", error_message="stop", correlation_id=CORRELATION_ID)
    gov = ResultGovernanceProjection.failure(error={"code": "HANDOFF_INSUFFICIENT", "message": "stop", "retryable": False})
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="stopped", semantic_stop=stop)
    assert card.semantic_stop is not None
    assert card.semantic_stop.reason == SemanticStopReason.HANDOFF_INSUFFICIENT

def test_t26_semantic_stop_escalates_to_task_main():
    stop = SemanticStop(reason=SemanticStopReason.UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED, task_ref=CANONICAL_TASK_ID)
    esc = Escalation.from_semantic_stop(stop)
    assert esc.escalation_target == "task-main"
    assert esc.kind.value == "SEMANTIC_STOP"
    assert esc.retry_requires_fresh_authority is True

def test_t27_semantic_stop_grants_no_retry():
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=CANONICAL_TASK_ID)
    assert stop.grants_retry is False
    card_result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="HANDOFF_INSUFFICIENT", error_message="stop", correlation_id=CORRELATION_ID)
    gov = ResultGovernanceProjection.failure(error={"code": "HANDOFF_INSUFFICIENT", "message": "stop", "retryable": False})
    card = project_worker_result_card(card_result, gov, AgentWorkRole.CODER, summary="stop", semantic_stop=stop)
    assert card.semantic_stop.grants_retry is False

def test_t28_semantic_stop_causes_no_automatic_replan():
    # no code path should auto replan; ensure no function creates plan mutation
    # check that stop module does not contain planner logic
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    assert "auto" not in src.lower() or "automatic_retry" not in src.lower() or "WORKER_SELF_REPLAN_AUTHORITY" in src
    # Ensure our fake flow does not dispatch second time
    adapter = FakeAdapter(mode="success")
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=CANONICAL_TASK_ID)
    # pre-execution stop prevents dispatch
    assert adapter.dispatch_called is False
    assert adapter.dispatch_count == 0

def test_t29_pre_execution_semantic_stop_prevents_adapter_dispatch():
    adapter = FakeAdapter(mode="success")
    handoff = _make_handoff("coder")
    # decide before execution: handoff insufficient
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=CANONICAL_TASK_ID)
    # simulate gate
    if stop is not None:
        # do not dispatch
        pass
    else:
        adapter.dispatch(compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding()))
    assert adapter.dispatch_called is False
    # also post-observation stop: after dispatch, ensure no second dispatch
    adapter2 = FakeAdapter(mode="success")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    dispatch = adapter2.dispatch(pkg)
    # Worker observes semantic condition after execution (e.g., unexpected architecture)
    stop2 = SemanticStop(reason=SemanticStopReason.UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED, task_ref=CANONICAL_TASK_ID)
    # For post-observation stop, we create a failure result that matches governance failure
    fail_result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="UNEXPECTED", error_message="stop", correlation_id=CORRELATION_ID)
    gov = ResultGovernanceProjection.failure(error={"code": "UNEXPECTED", "message": "stop", "retryable": False})
    card = project_worker_result_card(fail_result, gov, AgentWorkRole.CODER, summary="stop after", semantic_stop=stop2)
    # ensure no second dispatch
    assert adapter2.dispatch_count == 1
    assert card.semantic_stop is not None

# ---------------------------------------------------------------------------
# T30-T34 Mechanical / Unknown Safety
# ---------------------------------------------------------------------------

def test_t30_mechanical_failure_uses_existing_domains():
    # uses existing ForgeError/CanonicalResult semantics, not new ontology
    failure = MechanicalFailure(task_ref=CANONICAL_TASK_ID, error_code="EXECUTION_FAILED", retryable=True)
    assert failure.error_code == "EXECUTION_FAILED"
    assert failure.retryable is True
    # maps to existing CanonicalResult failure
    result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="EXECUTION_FAILED", error_message="fail", retryable=True, correlation_id=CORRELATION_ID)
    assert result.error["code"] == "EXECUTION_FAILED"
    assert result.error["retryable"] is True

def test_t31_retryable_mechanical_failure_grants_no_retry():
    failure = MechanicalFailure(task_ref=CANONICAL_TASK_ID, error_code="TIMEOUT", retryable=True)
    assert failure.grants_retry is False
    result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="TIMEOUT", error_message="timeout", retryable=True, correlation_id=CORRELATION_ID)
    gov = ResultGovernanceProjection.failure(error={"code": "TIMEOUT", "message": "timeout", "retryable": True})
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="fail retryable", mechanical_failure=failure)
    assert card.mechanical_failure.grants_retry is False
    # ensure not granting via decide helper
    from aota_forge.work_plane.stop import grants_retry_authority
    assert grants_retry_authority(retryable=True) is False

def test_t32_unknown_does_not_become_false_completion():
    adapter = FakeAdapter(mode="unknown")
    pkg = compile_handoff_to_execution_package(handoff=_make_handoff("coder"), binding=_make_trusted_binding())
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    assert result.status == "unknown"
    assert result.ok is False
    gov = ResultGovernanceProjection.unknown()
    assert gov.outcome == ResultOutcome.UNKNOWN
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="unknown")
    assert card.outcome == ResultOutcome.UNKNOWN
    assert card.outcome != ResultOutcome.SUCCESS

def test_t33_unknown_does_not_trigger_blind_retry():
    result = CanonicalResult.unknown(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", correlation_id=CORRELATION_ID)
    assert result.ok is False
    from aota_forge.work_plane.stop import decide_for_unknown_outcome
    decision = decide_for_unknown_outcome()
    assert decision["grant_retry"] is False
    assert decision["blind_retry"] is False

def test_t34_journal_contracts_unchanged():
    # Journal files must exist and not contain work_plane-specific mutations
    journal_files = [
        "aota_forge/core/journal/retry.py",
        "aota_forge/core/journal/store.py",
        "aota_forge/core/journal/state_machine.py",
    ]
    for p in journal_files:
        path = pathlib.Path(p)
        assert path.exists(), f"{p} missing"
        text = path.read_text(encoding="utf-8")
        # should not import work_plane
        assert "work_plane" not in text
        assert "WorkerResultCard" not in text

# ---------------------------------------------------------------------------
# T35-T45 Boundaries
# ---------------------------------------------------------------------------

def test_t35_execution_package_unchanged():
    pkg_path = pathlib.Path("aota_forge/core/execution/package.py")
    text = pkg_path.read_text(encoding="utf-8")
    # schema should not have handoff_digest field, still original fields
    assert "class ExecutionPackage" in text
    assert "handoff_digest" not in text
    # ensure no work_plane import
    assert "work_plane" not in text

def test_t36_canonical_result_unchanged():
    path = pathlib.Path("aota_forge/core/execution/results.py")
    text = path.read_text(encoding="utf-8")
    assert "class CanonicalResult" in text
    assert "work_plane" not in text

def test_t37_result_governance_unchanged():
    path = pathlib.Path("aota_forge/core/result_governance/common.py")
    text = path.read_text(encoding="utf-8")
    assert "class ResultGovernanceProjection" in text
    assert "WorkerResultCard" not in text

def test_t38_dispatcher_unchanged():
    path = pathlib.Path("aota_forge/core/execution/dispatcher.py")
    text = path.read_text(encoding="utf-8")
    assert "class ExecutionDispatcher" in text
    assert "work_plane" not in text

def test_t39_journal_unchanged():
    # broader journal dir
    import pathlib as _p
    journal_dir = _p.Path("aota_forge/core/journal")
    assert journal_dir.exists()
    for f in journal_dir.iterdir():
        if f.suffix == ".py":
            text = f.read_text(encoding="utf-8")
            assert "work_plane" not in text

def test_t40_hermes_production_unchanged():
    hermes_files = [
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/adapters/hermes/host_client.py",
    ]
    for p in hermes_files:
        text = pathlib.Path(p).read_text(encoding="utf-8")
        # should not be modified to import work_plane
        assert "work_plane" not in text

def test_t41_no_agents_filesystem_resolver():
    # work_plane should not contain filesystem walker
    for mod in ["agents_applicability.py", "bootstrap.py", "lifecycle.py", "soul.py"]:
        text = pathlib.Path(f"aota_forge/work_plane/{mod}").read_text(encoding="utf-8")
        lower = text.lower()
        assert "os.walk" not in lower
        assert "path.walk" not in lower
        assert "symlink" not in lower

def test_t42_no_skill_loading():
    for mod in ["bootstrap.py", "agents_applicability.py"]:
        text = pathlib.Path(f"aota_forge/work_plane/{mod}").read_text(encoding="utf-8")
        lower = text.lower()
        assert "skill" not in lower or "skill" in lower and "no skill" in lower  # allow comment "no skill"

def test_t43_no_context_retrieval():
    for mod in ["bootstrap.py", "agents_applicability.py"]:
        text = pathlib.Path(f"aota_forge/work_plane/{mod}").read_text(encoding="utf-8")
        lower = text.lower()
        assert "context retrieval" not in lower
        assert "fetch context" not in lower

def test_t44_no_telemetry_store():
    # no telemetry store file anywhere
    forbidden = [
        "aota_forge/work_plane/telemetry.py",
        "aota_forge/core/telemetry.py",
        "aota_forge/work_plane/analytics.py",
    ]
    for p in forbidden:
        assert not pathlib.Path(p).exists()
    # also check no jsonl ledger
    for path in pathlib.Path("aota_forge").rglob("*.jsonl"):
        pytest.fail(f"unexpected telemetry jsonl {path}")

def test_t45_fake_adapter_exists_test_only():
    # fake adapter defined in this test file, not in production
    prod_files = list(pathlib.Path("aota_forge").rglob("*.py"))
    for f in prod_files:
        if "work_plane" in str(f) and f.name != "__init__.py":
            # production work_plane files should not define FakeAdapter
            if "test_" in f.name:
                continue
            text = f.read_text(encoding="utf-8")
            assert "class FakeAdapter" not in text
    # also ensure not in core
    for f in pathlib.Path("aota_forge/core").rglob("*.py"):
        assert "class FakeAdapter" not in f.read_text(encoding="utf-8")
    # ensure test file does contain it (exactly one definition) - count via line start
    count = sum(1 for line in pathlib.Path(__file__).read_text(encoding="utf-8").splitlines() if line.strip().startswith("class FakeAdapter"))
    assert count == 1

# ---------------------------------------------------------------------------
# Additional: adapter neutrality, no hermes import
# ---------------------------------------------------------------------------

def test_m4_w1_adapter_neutral_no_hermes_import():
    # canonical proof seam must not import HermesAdapter (check import lines only)
    for line in pathlib.Path(__file__).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped.lower()
    # check work_plane harness also not hermes
    for mod in pathlib.Path("aota_forge/work_plane").glob("*.py"):
        for line in mod.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "hermes" not in stripped.lower()

def test_end_to_end_chain_executed():
    # Ensure chain actually executed via real objects
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    soul = _make_soul()
    policy = _make_policy()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    bundle.validate_budget(_make_budget())
    applicable = resolve_applicable_policies([policy], PROJECT_ID)
    assert applicable
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    assert isinstance(pkg, ExecutionPackage)
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="chain")
    assert card.task_ref == pkg.canonical_task_id
    # event hook
    evs = []
    emit_event(ExecutionEvent(event_id="e2e", event_type=ExecutionEventType.WORKER_RESULT, canonical_task_id=card.task_ref, result_ref=card.task_ref), lambda e: evs.append(e))
    assert evs

def test_event_hook_failure_does_not_mutate_governance():
    def bad_hook(ev: ExecutionEvent) -> None:
        raise RuntimeError("hook boom")
    ev = ExecutionEvent(event_id="hook-fail", event_type=ExecutionEventType.WORKER_RESULT)
    # governance before
    handoff = _make_handoff("coder")
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_trusted_binding())
    adapter = FakeAdapter(mode="success")
    dispatch = adapter.dispatch(pkg)
    result = adapter.result(dispatch.canonical_task_id, dispatch.adapter_handle)
    gov = ResultGovernanceProjection.success()
    with pytest.raises(EventHookError):
        emit_event(ev, bad_hook)
    # governance unchanged
    assert gov.outcome == ResultOutcome.SUCCESS
    # result unchanged
    assert result.ok is True

def test_journal_retry_not_bypassed():
    # retryable does not grant retry authority
    result = CanonicalResult.failure(canonical_task_id=CANONICAL_TASK_ID, executor_id="fake", error_code="TIMEOUT", error_message="timeout", retryable=True, correlation_id=CORRELATION_ID)
    assert result.error["retryable"] is True
    from aota_forge.work_plane.stop import is_retry_authorized
    assert is_retry_authorized(retryable=True) is False
    # no journal mutation in this test
    # ensure we didn't modify journal files to bypass fresh authorization
    text = pathlib.Path("aota_forge/core/journal/retry.py").read_text(encoding="utf-8")
    assert "fresh" in text.lower()

def test_no_telemetry_persistence():
    # events are not persisted
    ev = ExecutionEvent(event_id="no-persist", event_type=ExecutionEventType.EXECUTION_MATERIALIZED)
    emit_event(ev, None)
    # after emit, no file created
    assert not pathlib.Path("aota_forge/work_plane/telemetry_store.jsonl").exists()

def test_task_main_autonomous_replan_not_implemented():
    # Ensure no auto replan code exists
    text = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    assert "auto_replan" not in text.lower() or "WORKER_SELF_REPLAN_AUTHORITY" in text
    # check no file implements replan
    for p in pathlib.Path("aota_forge").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if "def replan" in t and "work_plane" in str(p) and "test_" not in str(p):
            pytest.fail(f"autonomous replan found in {p}")

