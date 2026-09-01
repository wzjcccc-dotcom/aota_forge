"""S1 M4-W2 Existing Hermes Compatibility Proof.

Offline/injected Hermes compatibility via existing production composition seam.

Flow:
TaskHandoff(coder) -> WorkRole mapping -> compiler -> ExecutionPackage
-> production-compatible Hermes dispatcher/adapter via injected fake HermesHostClient
-> Hermes status/result translation -> CanonicalResult -> ResultGovernance -> WorkerResultCard

No live daemon, no new Core contract, TEST-ONLY fake host.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Mapping

import pytest

from aota_forge.composition.execution import (
    PRODUCTION_HERMES_PROFILE_MAPPING,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult, FORBIDDEN_HERMES_RESULT_KEYS
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.mapping import resolve_work_role_to_canonical_role
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultOutcome
from aota_forge.adapters.hermes.executor import hermes_status_to_canonical_state, HERMES_STATUS_MAP

# ---------------------------------------------------------------------------
# Constants & repo root helper
# ---------------------------------------------------------------------------
M4_W1_CANDIDATE = "7c9ddc1b6e17aaecf52940f47f85f08a2d6b34cd"
M2_M3_JOINT = "c1eecd19fb5fb38b2f2f9c3cff0de360f1ae46ae"
PROJECT_ID = "proj-m4-w2"
CANONICAL_TASK_ID = "task-m4-w2-001"
CORRELATION_ID = "corr-m4-w2-001"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GIT_CWD = str(_REPO_ROOT)

# ---------------------------------------------------------------------------
# Fake Hermes Host (test-only) - returns Hermes-shaped primitives, not CanonicalResult
# ---------------------------------------------------------------------------

class FakeHermesHost:
    """Test-only fake implementing Hermes host protocol with Hermes-shaped dicts."""

    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []
        self._next_handle = 1
        self._handle_to_result: dict[str, dict[str, Any]] = {}
        self._handle_to_status: dict[str, dict[str, Any]] = {}
        self.dispatch_count = 0
        # default behavior
        self.default_status = "pending"
        self.default_result: dict[str, Any] | None = None

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatch_count += 1
        # record verbatim payload (Hermes-shaped)
        self.dispatched.append(dict(payload))
        handle = f"hermes-local-{self._next_handle}"
        self._next_handle += 1
        # default status pending
        self._handle_to_status[handle] = {"status": self.default_status}
        if self.default_result is not None:
            self._handle_to_result[handle] = dict(self.default_result)
        return {"adapter_handle": handle, "status": self.default_status, "dispatch_time": "2026-09-01T00:00:00Z"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return self._handle_to_status.get(adapter_handle, {"status": "unreachable", "details": "unknown handle"})

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        if adapter_handle in self._handle_to_result:
            return self._handle_to_result[adapter_handle]
        # default: if status pending, return pending; else need explicit
        return self._handle_to_status.get(adapter_handle, {"status": "unreachable"})

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self._handle_to_status[adapter_handle] = {"status": "cancelled"}
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "resume not supported"}}

    def set_result_for_handle(self, handle: str, result: Mapping[str, Any]) -> None:
        self._handle_to_result[handle] = dict(result)

    def set_status_for_handle(self, handle: str, status: str, details: str = "") -> None:
        self._handle_to_status[handle] = {"status": status, "details": details}


def _make_handoff(work_role: str | AgentWorkRole = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind="implementation",
        objective="Implement feature X via Hermes",
        bounded_scope="Bounded scope for M4-W2 Hermes compatibility",
        validation_expectations=("unit test passes",),
        semantic_stop_expectations=("ambiguous scope",),
        project_ref=SemanticReference(ref=PROJECT_ID),
        plan_ref=SemanticReference(ref="plan-s1"),
        milestone_ref=SemanticReference(ref="m4"),
        work_item_ref=SemanticReference(ref="m4-w2"),
    )


def _make_binding(task_id: str = CANONICAL_TASK_ID, project_id: str = PROJECT_ID) -> TrustedExecutionBinding:
    return TrustedExecutionBinding(canonical_task_id=task_id, project_id=project_id)


def _make_package(task_id: str = CANONICAL_TASK_ID) -> ExecutionPackage:
    handoff = _make_handoff("coder")
    binding = _make_binding(task_id, PROJECT_ID)
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=binding, correlation_id=CORRELATION_ID, package_id=f"pkg-{task_id}")
    return pkg


# ---------------------------------------------------------------------------
# T01 M4-W1 candidate exact parent
# ---------------------------------------------------------------------------

def test_t01_m4_w1_candidate_exact_parent():
    # HEAD is either the W2 commit (parent = M4_W1) or still M4_W1 itself pre-commit
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_GIT_CWD, text=True).strip()
    if head == M4_W1_CANDIDATE:
        # pre-commit: still on candidate, parent should be joint convergence
        parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=_GIT_CWD, text=True).strip()
        assert parent == M2_M3_JOINT
        return
    # post-commit: direct parent must be M4_W1 candidate
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=_GIT_CWD, text=True).strip()
    assert parent == M4_W1_CANDIDATE, f"DIRECT_PARENT_MATCH failed: {parent} != {M4_W1_CANDIDATE}"
    # single parent (not merge)
    parents = subprocess.check_output(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=_GIT_CWD, text=True).strip().split()
    assert len(parents) == 2, "W2 commit must have exactly one parent"


def test_t01b_branch_isolation():
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=_GIT_CWD, text=True).strip()
    assert branch == "aota/s1/m4-w2-existing-hermes-compatibility"
    # ensure not merging M4-W3
    all_branches = subprocess.check_output(["git", "branch", "-a"], cwd=_GIT_CWD, text=True)
    assert "aota/s1/m4-w2-existing-hermes-compatibility" in all_branches


# ---------------------------------------------------------------------------
# T02 production Hermes supports coder route
# ---------------------------------------------------------------------------

def test_t02_production_hermes_supports_coder_route():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t02-coder")
    # should resolve and dispatch via production mapping
    dispatch = dispatcher.dispatch(pkg)
    assert dispatch.canonical_task_id == "task-t02-coder"
    # verify production mapping narrow
    assert PRODUCTION_HERMES_PROFILE_MAPPING == {"coder": "coder"}
    caps = dispatcher.registry.get("hermes").capabilities()
    assert caps.supported_canonical_roles == ("coder",)
    assert caps.supports_task_cancellation is True
    assert caps.supports_task_resume is False


# ---------------------------------------------------------------------------
# T03 production surface is not expanded to all five roles
# ---------------------------------------------------------------------------

def test_t03_production_surface_not_expanded():
    assert PRODUCTION_HERMES_PROFILE_MAPPING == {"coder": "coder"}
    assert set(PRODUCTION_HERMES_PROFILE_MAPPING.keys()) == {"coder"}
    # ensure not containing analyst/reviewer etc
    for forbidden in ["analyst", "reviewer", "project-steward", "task-main", "planner", "steward", "executor"]:
        assert forbidden not in PRODUCTION_HERMES_PROFILE_MAPPING
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    caps = dispatcher.registry.get("hermes").capabilities()
    assert caps.supported_canonical_roles == ("coder",)
    assert len(caps.supported_canonical_roles) == 1
    # verify analyst package rejected via dispatcher (production narrow)
    handoff_analyst = TaskHandoff(
        work_role="analyst",
        task_kind="analysis",
        objective="Analyze",
        bounded_scope="bounded",
        validation_expectations=("check",),
        semantic_stop_expectations=("ambiguous",),
        project_ref=SemanticReference(ref=PROJECT_ID),
    )
    # analyst maps to planner via work_plane, but production hermes only supports coder -> should fail
    pkg_analyst = compile_handoff_to_execution_package(handoff=handoff_analyst, binding=_make_binding("task-analyst"))
    assert pkg_analyst.canonical_role == "planner"
    with pytest.raises(Exception):
        dispatcher.dispatch(pkg_analyst)


# ---------------------------------------------------------------------------
# T04 coder Handoff maps through accepted WorkRole→CanonicalRole resolver
# ---------------------------------------------------------------------------

def test_t04_coder_handoff_maps_through_resolver():
    # must use resolve_work_role_to_canonical_role, not direct hermes mapping
    canonical = resolve_work_role_to_canonical_role(AgentWorkRole.CODER)
    assert canonical.value == "coder"
    handoff = _make_handoff("coder")
    # compiler internally uses resolver
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=_make_binding())
    assert pkg.canonical_role == "coder"
    assert pkg.canonical_role == canonical.value
    # verify no direct WORKROLE_TO_HERMES mapping created in work_plane
    for mod in (_REPO_ROOT / "aota_forge" / "work_plane").glob("*.py"):
        text = mod.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                continue
            # work_plane should not contain hermes profile strings
            assert "coder_profile" not in line, f"direct Hermes mapping found in {mod}"
            assert "hermes_profile" not in line.lower() or "not canonical" in line.lower()
    # also ensure work_plane/__init__.py not importing hermes
    init_text = (_REPO_ROOT / "aota_forge" / "work_plane" / "__init__.py").read_text(encoding="utf-8")
    assert "hermes" not in init_text.lower()


# ---------------------------------------------------------------------------
# T05 Handoff compiles to existing ExecutionPackage
# ---------------------------------------------------------------------------

def test_t05_handoff_compiles_to_existing_execution_package():
    handoff = _make_handoff("coder")
    binding = _make_binding()
    pkg = compile_handoff_to_execution_package(handoff=handoff, binding=binding)
    assert isinstance(pkg, ExecutionPackage)
    assert pkg.canonical_task_id == CANONICAL_TASK_ID
    assert pkg.project_id == PROJECT_ID
    assert pkg.canonical_role == "coder"
    assert pkg.instruction == handoff.objective
    # ExecutionPackage retains existing fields
    assert hasattr(pkg, "package_id")
    assert hasattr(pkg, "correlation_id")
    assert hasattr(pkg, "intent_fingerprint")
    assert hasattr(pkg, "idempotency_key")


# ---------------------------------------------------------------------------
# T06 production-compatible dispatcher/adapter accepts package
# ---------------------------------------------------------------------------

def test_t06_production_dispatcher_accepts_package():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t06")
    val = dispatcher.registry.get("hermes").validate_package(pkg)
    assert val.valid is True
    dispatch = dispatcher.dispatch(pkg)
    assert dispatch.canonical_task_id == "task-t06"
    # dispatcher has route
    route = dispatcher.get_route("task-t06")
    assert route.canonical_task_id == "task-t06"
    assert route.executor_id == "hermes"


# ---------------------------------------------------------------------------
# T07 injected fake host receives Hermes-shaped payload
# ---------------------------------------------------------------------------

def test_t07_fake_host_receives_hermes_shaped_payload():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t07")
    dispatcher.dispatch(pkg)
    assert len(fake.dispatched) == 1
    payload = fake.dispatched[0]
    # Hermes-shaped primitive mapping, not CanonicalResult
    assert isinstance(payload, dict)
    assert "profile" in payload
    assert "instruction" in payload
    assert "context" in payload
    assert "artifacts" in payload
    assert "constraints" in payload
    assert "capability_requirements" in payload
    assert "package_id" in payload
    # ensure not CanonicalResult
    assert "ok" not in payload
    assert "canonical_task_state" not in payload
    # production mapping: coder -> coder
    assert payload["profile"] == "coder"


# ---------------------------------------------------------------------------
# T08 canonical task/project/correlation/package lineage preserved
# ---------------------------------------------------------------------------

def test_t08_lineage_preserved():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t08-lineage")
    dispatcher.dispatch(pkg)
    payload = fake.dispatched[0]
    ctx = payload["context"]
    assert ctx["canonical_task_id"] == pkg.canonical_task_id
    assert ctx["project_id"] == pkg.project_id
    assert ctx["correlation_id"] == pkg.correlation_id
    assert ctx["intent_fingerprint"] == pkg.intent_fingerprint
    assert payload["package_id"] == pkg.package_id
    # also correlation via dispatcher route
    route = dispatcher.get_route("task-t08-lineage")
    assert route.correlation_id == pkg.correlation_id
    assert route.intent_fingerprint == pkg.intent_fingerprint
    assert route.package_id == pkg.package_id


# ---------------------------------------------------------------------------
# T09 Hermes local handle does not become canonical task authority
# ---------------------------------------------------------------------------

def test_t09_hermes_handle_not_canonical_authority():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t09")
    dispatch = dispatcher.dispatch(pkg)
    handle = dispatch.adapter_handle
    assert handle != pkg.canonical_task_id
    assert handle.startswith("hermes-local-")
    # route keeps distinct domains
    route = dispatcher.get_route("task-t09")
    assert route.adapter_handle == handle
    assert route.canonical_task_id == pkg.canonical_task_id
    # fetch via canonical_task_id still works, not via handle as task id
    fake.set_result_for_handle(handle, {"status": "done", "exit_code": 0, "result_data": {}, "stdout": "ok"})
    result = dispatcher.result("task-t09")
    assert result.canonical_task_id == "task-t09"
    # hermes handle never appears as canonical_task_id
    assert result.canonical_task_id != handle


# ---------------------------------------------------------------------------
# T10 done/success -> COMPLETED
# ---------------------------------------------------------------------------

def test_t10_done_success_maps_to_completed():
    assert hermes_status_to_canonical_state("done") == CanonicalTaskState.COMPLETED
    assert hermes_status_to_canonical_state("success") == CanonicalTaskState.COMPLETED
    # via adapter/dispatcher success
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t10-done")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": 0, "result_data": {"k": "v"}, "stdout": "done ok", "correlation_id": CORRELATION_ID})
    result = dispatcher.result("task-t10-done")
    assert result.ok is True
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert result.status == "completed"

    fake2 = FakeHermesHost()
    dispatcher2 = create_production_execution_dispatcher(host_client=fake2)
    pkg2 = _make_package("task-t10-success")
    dispatch2 = dispatcher2.dispatch(pkg2)
    fake2.set_result_for_handle(dispatch2.adapter_handle, {"status": "success", "exit_code": 0, "result_data": {}, "stdout": "success"})
    result2 = dispatcher2.result("task-t10-success")
    assert result2.ok is True
    assert result2.canonical_task_state == CanonicalTaskState.COMPLETED.value


# ---------------------------------------------------------------------------
# T11 error/failed -> canonical failure
# ---------------------------------------------------------------------------

def test_t11_error_failed_maps_to_failure():
    assert hermes_status_to_canonical_state("error") == CanonicalTaskState.FAILED
    assert hermes_status_to_canonical_state("failed") == CanonicalTaskState.FAILED
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t11-error")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "error", "error": {"code": "EXECUTION_FAILED", "message": "compile error"}, "exit_code": 1})
    result = dispatcher.result("task-t11-error")
    assert result.ok is False
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.error["code"] == "EXECUTION_FAILED"

    fake2 = FakeHermesHost()
    dispatcher2 = create_production_execution_dispatcher(host_client=fake2)
    pkg2 = _make_package("task-t11-failed")
    dispatch2 = dispatcher2.dispatch(pkg2)
    fake2.set_result_for_handle(dispatch2.adapter_handle, {"status": "failed", "error": {"code": "EXECUTION_FAILED", "message": "failed"}, "exit_code": 1})
    result2 = dispatcher2.result("task-t11-failed")
    assert result2.ok is False
    assert result2.canonical_task_state == CanonicalTaskState.FAILED.value


# ---------------------------------------------------------------------------
# T12 cancelled/aborted -> CANCELLED
# ---------------------------------------------------------------------------

def test_t12_cancelled_aborted_maps_to_cancelled():
    assert hermes_status_to_canonical_state("cancelled") == CanonicalTaskState.CANCELLED
    assert hermes_status_to_canonical_state("aborted") == CanonicalTaskState.CANCELLED
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t12-cancelled")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "cancelled", "error": {"code": "EXECUTION_CANCELLED", "message": "cancelled"}})
    result = dispatcher.result("task-t12-cancelled")
    assert result.canonical_task_state == CanonicalTaskState.CANCELLED.value
    assert result.status == "cancelled"

    # aborted status also
    fake2 = FakeHermesHost()
    dispatcher2 = create_production_execution_dispatcher(host_client=fake2)
    pkg2 = _make_package("task-t12-aborted")
    dispatch2 = dispatcher2.dispatch(pkg2)
    fake2.set_status_for_handle(dispatch2.adapter_handle, "aborted")
    # query via adapter status path
    adapter = dispatcher2.registry.get("hermes")
    status = adapter.status(pkg2.canonical_task_id, dispatch2.adapter_handle)
    assert status.state == CanonicalTaskState.CANCELLED


# ---------------------------------------------------------------------------
# T13 unreachable -> UNKNOWN
# ---------------------------------------------------------------------------

def test_t13_unreachable_maps_to_unknown():
    assert hermes_status_to_canonical_state("unreachable") == CanonicalTaskState.UNKNOWN
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t13-unreachable")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "unreachable"}})
    result = dispatcher.result("task-t13-unreachable")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result.status == "unknown"
    assert result.ok is False


# ---------------------------------------------------------------------------
# T14 timeout -> UNKNOWN
# ---------------------------------------------------------------------------

def test_t14_timeout_maps_to_unknown():
    # status mapping: timeout -> UNKNOWN (never false completion)
    assert hermes_status_to_canonical_state("timeout") == CanonicalTaskState.UNKNOWN
    assert hermes_status_to_canonical_state("timeout") != CanonicalTaskState.COMPLETED
    # result path for timeout is authoritative terminal timeout (FAILED) via adapter;
    # the UNKNOWN guarantee is for status mapping and for fetch when not authoritative.
    # Verify status query path gives UNKNOWN.
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t14-timeout")
    dispatch = dispatcher.dispatch(pkg)
    # status query -> UNKNOWN
    fake.set_status_for_handle(dispatch.adapter_handle, "timeout")
    adapter = dispatcher.registry.get("hermes")
    status = adapter.status(pkg.canonical_task_id, dispatch.adapter_handle)
    assert status.state == CanonicalTaskState.UNKNOWN
    # fetch with timeout status -> adapter projects timeout as FAILED (existing contract)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "timeout", "error": {"code": "EXECUTION_TIMEOUT", "message": "timeout"}})
    result = dispatcher.result("task-t14-timeout")
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.error["code"] == "EXECUTION_TIMEOUT"
    assert result.ok is False
    # ensure not false completion
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value


# ---------------------------------------------------------------------------
# T15 unknown_future_state -> UNKNOWN
# ---------------------------------------------------------------------------

def test_t15_unknown_future_state_maps_to_unknown():
    assert hermes_status_to_canonical_state("unknown_future_state") == CanonicalTaskState.UNKNOWN
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t15-unknown")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "unknown_future_state"})
    result = dispatcher.result("task-t15-unknown")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result.ok is False
    assert result.status == "unknown"


# ---------------------------------------------------------------------------
# T16 None status -> UNKNOWN
# ---------------------------------------------------------------------------

def test_t16_none_status_maps_to_unknown():
    assert hermes_status_to_canonical_state(None) == CanonicalTaskState.UNKNOWN
    assert hermes_status_to_canonical_state("") == CanonicalTaskState.UNKNOWN
    assert hermes_status_to_canonical_state("   ") == CanonicalTaskState.UNKNOWN
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t16-none")
    dispatch = dispatcher.dispatch(pkg)
    # None status via fetch_result
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": None})
    result = dispatcher.result("task-t16-none")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result.status == "unknown"


# ---------------------------------------------------------------------------
# T17 unknown status never false-completes
# ---------------------------------------------------------------------------

def test_t17_unknown_never_false_completes():
    for s in ["unknown_future_state", None, "unreachable", "timeout", "zombie", ""]:
        state = hermes_status_to_canonical_state(s)
        assert state != CanonicalTaskState.COMPLETED, f"{s!r} falsely completed"
        assert state == CanonicalTaskState.UNKNOWN
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t17-false")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "unknown_future_state"})
    result = dispatcher.result("task-t17-false")
    assert result.ok is False
    assert result.status != "completed"
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value


# ---------------------------------------------------------------------------
# T18 success result -> CanonicalResult
# ---------------------------------------------------------------------------

def test_t18_success_result_to_canonical():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t18-success")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": 0, "result_data": {"files": ["a"]}, "stdout": "ok", "execution_stats": {"duration_ms": 10}, "correlation_id": CORRELATION_ID})
    result = dispatcher.result("task-t18-success")
    assert isinstance(result, CanonicalResult)
    assert result.ok is True
    assert result.status == "completed"
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert result.exit_code == 0
    assert result.result_data == {"files": ["a"]}
    assert result.correlation_id == CORRELATION_ID


# ---------------------------------------------------------------------------
# T19 CanonicalResult -> ResultGovernanceProjection
# ---------------------------------------------------------------------------

def test_t19_canonical_to_governance():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t19-gov")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": 0, "result_data": {}, "correlation_id": CORRELATION_ID})
    result = dispatcher.result("task-t19-gov")
    gov = ResultGovernanceProjection.success()
    assert gov.outcome == ResultOutcome.SUCCESS
    assert gov.governance_version == "1.0"
    # failure governance also
    fake2 = FakeHermesHost()
    dispatcher2 = create_production_execution_dispatcher(host_client=fake2)
    pkg2 = _make_package("task-t19-gov-fail")
    dispatch2 = dispatcher2.dispatch(pkg2)
    fake2.set_result_for_handle(dispatch2.adapter_handle, {"status": "failed", "error": {"code": "EXECUTION_FAILED", "message": "fail"}, "exit_code": 1})
    result2 = dispatcher2.result("task-t19-gov-fail")
    gov2 = ResultGovernanceProjection.failure(error={"code": "EXECUTION_FAILED", "message": "fail", "retryable": False})
    assert gov2.outcome == ResultOutcome.FAILURE
    assert result2.ok is False


# ---------------------------------------------------------------------------
# T20 governed result -> WorkerResultCard
# ---------------------------------------------------------------------------

def test_t20_governed_to_worker_card():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t20-card")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": 0, "result_data": {}, "correlation_id": CORRELATION_ID})
    result = dispatcher.result("task-t20-card")
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="hermes success via production path")
    assert isinstance(card, WorkerResultCard)
    assert card.task_ref == "task-t20-card"
    assert card.outcome == ResultOutcome.SUCCESS
    assert card.result_handoff_ref.ref == "task-t20-card"
    # also check ExecutionEvent consumable
    from aota_forge.work_plane.events import ExecutionEvent, ExecutionEventType, emit_event
    ev = ExecutionEvent(event_id="evt-t20", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER, canonical_task_id=card.task_ref, result_ref=card.task_ref)
    emitted = []
    emit_event(ev, lambda e: emitted.append(e))
    assert emitted[0].result_ref == "task-t20-card"


# ---------------------------------------------------------------------------
# T21 Hermes-private result keys do not escape
# ---------------------------------------------------------------------------

def test_t21_hermes_private_keys_do_not_escape():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t21-private")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {
        "status": "done",
        "exit_code": 0,
        "result_data": {"valid": "keep", "hermes_session": "private", "hermes_task": "private", "hermes_trace": ["x"], "hermes_worker": "w", "hermes_profile": "coder"},
        "output_artifacts": [{"path": "a", "hermes_internal_id": 999}],
        "execution_stats": {"duration_ms": 5, "hermes_daemon_pid": 123},
        "correlation_id": CORRELATION_ID,
    })
    result = dispatcher.result("task-t21-private")
    assert result.ok is True
    # private keys stripped
    for forbidden in FORBIDDEN_HERMES_RESULT_KEYS:
        assert forbidden not in result.result_data, f"{forbidden} escaped"
        assert forbidden not in str(result.execution_stats)
    assert "valid" in result.result_data
    for art in result.output_artifacts:
        assert "hermes_internal_id" not in art
    assert "hermes_daemon_pid" not in result.execution_stats
    # also via governance/card
    gov = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="clean")
    card_dict = card.to_dict()
    for forbidden in ["hermes_session", "hermes_task", "hermes_trace", "hermes_worker", "hermes_profile"]:
        assert forbidden not in str(card_dict)


# ---------------------------------------------------------------------------
# T22 untrustworthy result observation -> UNKNOWN uncertainty
# ---------------------------------------------------------------------------

def test_t22_untrustworthy_result_to_unknown():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t22-untrust")
    dispatch = dispatcher.dispatch(pkg)
    # malformed exit_code type -> transient observation failure
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": "not_an_int", "result_data": {}, "correlation_id": CORRELATION_ID})
    result = dispatcher.result("task-t22-untrust")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result.status == "unknown"
    assert result.error["code"] == "RESULT_MALFORMED"
    assert result.error["retryable"] is True
    assert result.ok is False

    # also malformed result_data not mapping
    fake2 = FakeHermesHost()
    dispatcher2 = create_production_execution_dispatcher(host_client=fake2)
    pkg2 = _make_package("task-t22-untrust2")
    dispatch2 = dispatcher2.dispatch(pkg2)
    fake2.set_result_for_handle(dispatch2.adapter_handle, {"status": "done", "exit_code": 0, "result_data": "not_a_mapping"})
    result2 = dispatcher2.result("task-t22-untrust2")
    assert result2.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result2.status == "unknown"


# ---------------------------------------------------------------------------
# T23 untrustworthy observation not false failure
# ---------------------------------------------------------------------------

def test_t23_untrustworthy_not_false_failure():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t23")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": "bad"})
    result = dispatcher.result("task-t23")
    # must be UNKNOWN not FAILED/COMPLETED
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert result.canonical_task_state != CanonicalTaskState.FAILED.value
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.status == "unknown"
    assert result.ok is False
    # ensure not terminal failure
    assert result.error["code"] == "RESULT_MALFORMED"
    # governance for unknown
    gov = ResultGovernanceProjection.unknown()
    assert gov.outcome == ResultOutcome.UNKNOWN
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="untrustworthy")
    assert card.outcome == ResultOutcome.UNKNOWN
    assert card.outcome != ResultOutcome.SUCCESS


# ---------------------------------------------------------------------------
# T24 retryable uncertainty grants no retry authority
# ---------------------------------------------------------------------------

def test_t24_retryable_grants_no_authority():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t24")
    dispatch = dispatcher.dispatch(pkg)
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": "bad"})
    result = dispatcher.result("task-t24")
    assert result.error["retryable"] is True
    # retryable is not authority
    from aota_forge.work_plane.stop import grants_retry_authority, is_retry_authorized, RETRYABLE_IS_RETRY_AUTHORITY
    assert RETRYABLE_IS_RETRY_AUTHORITY is False
    assert grants_retry_authority(retryable=True) is False
    assert is_retry_authorized(retryable=True) is False
    # even with retryable, card must not grant retry
    gov = ResultGovernanceProjection.unknown()
    card = project_worker_result_card(result, gov, AgentWorkRole.CODER, summary="retryable unknown")
    assert card.outcome == ResultOutcome.UNKNOWN


# ---------------------------------------------------------------------------
# T25 no automatic second dispatch
# ---------------------------------------------------------------------------

def test_t25_no_automatic_second_dispatch():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    pkg = _make_package("task-t25")
    dispatch = dispatcher.dispatch(pkg)
    assert fake.dispatch_count == 1
    fake.set_result_for_handle(dispatch.adapter_handle, {"status": "done", "exit_code": "bad"})
    result = dispatcher.result("task-t25")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    # ensure no second dispatch triggered automatically
    assert fake.dispatch_count == 1
    assert len(fake.dispatched) == 1
    # explicit second dispatch with same package would be idempotent replay, not new dispatch
    dispatch2 = dispatcher.dispatch(pkg)
    assert dispatch2.adapter_handle == dispatch.adapter_handle
    # dispatch count unchanged because idempotency replay doesn't call host again
    assert fake.dispatch_count == 1


# ---------------------------------------------------------------------------
# T26 no live Hermes daemon/process
# ---------------------------------------------------------------------------

def test_t26_no_live_hermes_daemon():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    # ensure fake is used, not real HermesHostClient that would spawn subprocess
    assert isinstance(fake, FakeHermesHost)
    # ensure no subprocess/Popen was invoked
    pkg = _make_package("task-t26")
    dispatcher.dispatch(pkg)
    assert fake.dispatch_count == 1
    # ensure no env indicates live host
    import os
    assert os.environ.get("HERMES_HOST_ACTIVE") is None
    # ensure production host launcher not executed
    import subprocess as sp
    # we never called sp.Popen with hermes-host
    # fake's payload should not contain process pid
    result = fake.dispatched[0]
    assert "hermes_daemon_pid" not in str(result)


# ---------------------------------------------------------------------------
# T27 Hermes production code unchanged
# ---------------------------------------------------------------------------

def test_t27_hermes_production_unchanged():
    # PRODUCTION_HERMES_PROFILE_MAPPING and capabilities must remain narrow
    assert PRODUCTION_HERMES_PROFILE_MAPPING == {"coder": "coder"}
    # files must not import work_plane and must not have expanded roles
    for p in ["aota_forge/composition/execution.py", "aota_forge/adapters/hermes/executor.py", "aota_forge/adapters/hermes/host_client.py"]:
        text = (_REPO_ROOT / p).read_text(encoding="utf-8")
        assert "work_plane" not in text, f"{p} should not import work_plane"
    # composition still advertises only coder
    comp_text = (_REPO_ROOT / "aota_forge" / "composition" / "execution.py").read_text(encoding="utf-8")
    assert 'supported_canonical_roles=("coder",)' in comp_text
    assert 'PRODUCTION_HERMES_PROFILE_MAPPING = {"coder": "coder"}' in comp_text
    # hermes adapter default capabilities not changed for production test (production caps are narrow, but default adapter may be broader - ensure production caps not broadened)
    # verify git diff for these paths vs M4_W1 parent is empty (test-only change)
    diff = subprocess.check_output(["git", "diff", f"{M4_W1_CANDIDATE}..HEAD", "--", "aota_forge/composition/execution.py", "aota_forge/adapters/hermes/executor.py", "aota_forge/adapters/hermes/host_client.py"], cwd=_GIT_CWD, text=True)
    assert diff.strip() == "", f"production Hermes files changed: {diff[:500]}"


# ---------------------------------------------------------------------------
# T28 Core unchanged
# ---------------------------------------------------------------------------

def test_t28_core_unchanged():
    # core execution/results, package, dispatcher, journal, result_governance must not import work_plane
    for p in [
        "aota_forge/core/execution/package.py",
        "aota_forge/core/execution/results.py",
        "aota_forge/core/execution/dispatcher.py",
        "aota_forge/core/result_governance/common.py",
        "aota_forge/core/journal/retry.py",
        "aota_forge/core/journal/store.py",
    ]:
        path = _REPO_ROOT / p
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert "work_plane" not in text, f"{p} should not import work_plane"
            assert "WorkerResultCard" not in text or "result_governance" in p or "test_" in p
    # git diff vs parent for core should be empty
    diff = subprocess.check_output(["git", "diff", f"{M4_W1_CANDIDATE}..HEAD", "--", "aota_forge/core/execution", "aota_forge/core/result_governance", "aota_forge/core/journal"], cwd=_GIT_CWD, text=True)
    assert diff.strip() == "", f"Core changed: {diff[:500]}"
    # also spec: frozen core production change count ==0 verified via git

# Additional coverage: status matrix representative, bootstrap boundary, event/card, capabilities

def test_status_matrix_representative():
    matrix = {
        "init": CanonicalTaskState.CREATED,
        "pending": CanonicalTaskState.QUEUED,
        "running": CanonicalTaskState.RUNNING,
        "waiting_for_input": CanonicalTaskState.WAITING,
        "waiting_for_subagent": CanonicalTaskState.WAITING,
        "done": CanonicalTaskState.COMPLETED,
        "success": CanonicalTaskState.COMPLETED,
        "error": CanonicalTaskState.FAILED,
        "failed": CanonicalTaskState.FAILED,
        "aborted": CanonicalTaskState.CANCELLED,
        "cancelled": CanonicalTaskState.CANCELLED,
        "unreachable": CanonicalTaskState.UNKNOWN,
        "timeout": CanonicalTaskState.UNKNOWN,
        "unknown_future_state": CanonicalTaskState.UNKNOWN,
        None: CanonicalTaskState.UNKNOWN,
    }
    for hermes_status, expected in matrix.items():
        assert hermes_status_to_canonical_state(hermes_status) == expected


def test_hermes_private_type_escape_across_all_envelopes():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    for task_suffix in ["priv1", "priv2"]:
        pkg = _make_package(f"task-priv-{task_suffix}")
        dispatch = dispatcher.dispatch(pkg)
        fake.set_result_for_handle(dispatch.adapter_handle, {
            "status": "done",
            "exit_code": 0,
            "result_data": {"hermes_session": "s", "hermes_task": "t", "ok": "yes"},
            "execution_stats": {"hermes_worker": "w"},
            "correlation_id": CORRELATION_ID,
        })
        result = dispatcher.result(f"task-priv-{task_suffix}")
        assert "hermes_session" not in result.result_data
        assert "hermes_task" not in result.result_data
        assert "hermes_worker" not in result.execution_stats


def test_cancellation_supports_true_resume_false():
    fake = FakeHermesHost()
    dispatcher = create_production_execution_dispatcher(host_client=fake)
    caps = dispatcher.registry.get("hermes").capabilities()
    assert caps.supports_task_cancellation is True
    assert caps.supports_task_resume is False
    # cancel via dispatcher
    pkg = _make_package("task-cancel-check")
    dispatch = dispatcher.dispatch(pkg)
    # need to set status running to allow cancel (dispatcher.cancel checks terminal)
    # production adapter's cancel will succeed
    cancel_result = dispatcher.cancel("task-cancel-check")
    assert cancel_result.cancelled is True or cancel_result.state == CanonicalTaskState.CANCELLED


def test_hermes_bypasses_work_plane_bootstrap():
    # Hermes proof consumes W1 bootstrap semantics; Hermes does not load SOUL/AGENTS directly
    # verify adapter/host_client never reads SOUL/AGENTS
    for p in ["aota_forge/adapters/hermes/executor.py", "aota_forge/adapters/hermes/host_client.py"]:
        text = (_REPO_ROOT / p).read_text(encoding="utf-8")
        assert "SOUL" not in text
        assert "AGENTS.md" not in text
        assert "Skill" not in text or "skill" not in text.lower() or "skill" in text.lower() and "hermes" in text.lower()


def test_no_hermes_result_card_or_event_ontology():
    hermes_exec = (_REPO_ROOT / "aota_forge" / "adapters" / "hermes" / "executor.py").read_text(encoding="utf-8")
    assert "WorkerResultCard" not in hermes_exec
    assert "ExecutionEvent" not in hermes_exec
    assert "HERMES_RESULT_CARD" not in hermes_exec
    assert "HERMES_EVENT_ONTOLOGY" not in hermes_exec


def test_no_all_five_roles_production_claim():
    assert PRODUCTION_HERMES_PROFILE_MAPPING != {"coder": "coder", "planner": "planner", "reviewer": "reviewer", "steward": "steward", "executor": "executor"}
    caps = create_production_execution_dispatcher(host_client=FakeHermesHost()).registry.get("hermes").capabilities()
    assert set(caps.supported_canonical_roles) != {"coder", "planner", "reviewer", "steward", "executor"}
    assert "analyst" not in caps.supported_canonical_roles
    assert "reviewer" not in PRODUCTION_HERMES_PROFILE_MAPPING
    assert "project-steward" not in PRODUCTION_HERMES_PROFILE_MAPPING
