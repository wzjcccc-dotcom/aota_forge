"""AF #50 M1/W1 — Authoritative Worker Timeout Terminal Truth (I40-B005).

Repairs I40-B005: an authoritative Hermes execution timeout receipt
(status=timeout, exit_code=-15) was mapped by the STATUS observation layer to
``CanonicalTaskState.UNKNOWN`` (correctly fail-closed for untrustworthy
observation), but ``DurableCompletionCoordinator._recover_nonterminal`` only
consulted that observation. The durable record therefore stayed
recovery-eligible UNKNOWN forever: no terminal failure, no failure Card, no
delivery, no parent reentry — even though the EXISTING authoritative result
path (``ExecutionDispatcher.observe_result`` -> ``HermesAdapter.result`` ->
``CanonicalResult.timeout`` -> FAILED / EXECUTION_TIMEOUT) already proved the
terminal truth.

Frozen distinction under proof:

    UNTRUSTWORTHY_OBSERVATION_TIMEOUT_IS_UNKNOWN=yes
    AUTHORITATIVE_EXECUTION_TIMEOUT_IS_TERMINAL=yes
    HERMES_STATUS_MAP_GLOBAL_FLIP=no

The raw status vocabulary remains fail-closed (``HERMES_STATUS_MAP["timeout"]``
stays ``CanonicalTaskState.UNKNOWN``). Only a terminal result observed through
the authoritative result path may terminalize the durable record; result
unavailable / fetch failure / parse failure / transport uncertainty / unknown
host status / still-running remain retryable UNKNOWN. The AF #49 M1/W9
semantic-success gate is preserved (process exit 0 still requires a governed
``task.return``).

Proof boundary (honest):
  PROVES=deterministic V1 lifecycle matrix over the real production
         dispatcher/coordinator composition with an injected executor host
         double, plus a targeted fresh-process V2 over the real file-backed
         durable store and the existing Hermes adapter/result projection.
  DOES_NOT_PROVE=the real Hermes Worker timeout receipt, the real exact parent
         session completion delivery, or M1 acceptance; those are the bounded
         real production V3 vertical recorded in local evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
    card_digest_for,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.runtime.completion import (
    AUTHORITATIVE_EXECUTION_TIMEOUT_IS_TERMINAL,
    COMPLETION_CONTINUATION_BUDGET_IS_ROOT_CAUSE,
    DELIVER_ACKNOWLEDGED,
    HERMES_STATUS_MAP_GLOBAL_FLIP,
    PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS,
    RECOVER_SEMANTIC_RESULT_NOT_PROVEN,
    RECOVER_TERMINAL_ALREADY_DURABLE,
    RECOVER_TERMINAL_PERSISTED,
    RECOVER_UNKNOWN_PERSISTED,
    RUNTIME_AUTO_RETRY_ON_TIMEOUT,
    SEMANTIC_RESULT_NOT_PROVEN,
    SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN,
    UNTRUSTWORTHY_OBSERVATION_TIMEOUT_IS_UNKNOWN,
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
)
from aota_forge.runtime.config import load_runtime_config
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.task_facade import task_return
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

PROJECT_ID = "aota_forge_af50_w1_fixture"
WORKTREE_ID = "wt-af50-w1"
EXECUTOR_ID = "hermes"
TEST_SCOPE = "hermes:coder"
REAL_SESSION = "20260913_090000_af50w1real"

TIMEOUT_ENVELOPE = {
    "status": "timeout",
    "exit_code": -15,
    "stdout": "",
    "stderr": "",
    "execution_stats": {"duration_ms": 10000},
    "error": {"code": "EXECUTION_TIMEOUT", "message": "Hermes worker exceeded its bounded timeout"},
}
DONE_ENVELOPE = {
    "status": "done",
    "exit_code": 0,
    "stdout": "AOTA_AF50_W1_OK\n",
    "stderr": "",
    "execution_stats": {"duration_ms": 5},
}
FAILED_ENVELOPE = {
    "status": "failed",
    "exit_code": 3,
    "stdout": "",
    "stderr": "boom\n",
    "execution_stats": {"duration_ms": 5},
    "error": {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"},
}
CANCELLED_ENVELOPE = {
    "status": "cancelled",
    "exit_code": None,
    "stdout": "",
    "stderr": "",
    "execution_stats": {"duration_ms": 5},
    "error": {"code": "EXECUTION_CANCELLED", "message": "Hermes worker cancelled"},
}


# ---------------------------------------------------------------------------
# Fixture world (real durable stores over a trusted worktree root)
# ---------------------------------------------------------------------------


def _project_manifest() -> str:
    return (
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n"
    )


def _runtime_config_json() -> dict:
    return {
        "executor": "hermes",
        "executable": "/bin/false",
        "concurrency": 2,
        "provider": "opencode-go",
        "model": "m",
        "bindings": {
            "task-main": {"profile": "aota-task-main"},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
        },
    }


class _World:
    def __init__(self, root: Path, cfg_path: Path, exec_path: Path, sandbox) -> None:
        self.root = root
        self.cfg_path = cfg_path
        self.exec_path = exec_path
        self.sandbox = sandbox

    @property
    def runtime_config(self):
        return load_runtime_config(config_path=str(self.cfg_path))


def _make_world(tmp_path: Path, run_id: str) -> _World:
    root = tmp_path / f"wt_{run_id}"
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{run_id}.json"
    cfg_path.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
    exec_path = root / ".aota" / "execution.json"
    exec_path.write_text("{}", encoding="utf-8")
    evidence = resolve_trusted_project_evidence(worktree_root=root, project_id=PROJECT_ID)
    assert evidence.status == "RESOLVED", evidence.status
    sandbox = bind_worktree_sandbox(
        evidence, WORKTREE_ID, root, expected_project_id=PROJECT_ID
    )
    return _World(root, cfg_path, exec_path, sandbox)


def _make_package(
    task_id: str, *, constraints: dict | None = None
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=PROJECT_ID,
        canonical_role="coder",
        instruction=f"af50 w1 timeout truth proof {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints=constraints or {},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


class ProgrammableHermesHostClient:
    """Hermes-host-protocol double with separable status/result behaviors.

    ``query_status_status`` models the OBSERVATION layer (Hermes raw status ->
    CanonicalTaskState, where "timeout" is untrustworthy -> UNKNOWN).
    ``result_envelope`` models the AUTHORITATIVE result path: a mapping, a
    callable, an exception instance (fetch failure), or a non-mapping
    (malformed observation).
    """

    def __init__(
        self,
        *,
        query_status_status: str = "pending",
        result_envelope=None,
        handle_task_map: dict | None = None,
    ) -> None:
        self.query_status_status = query_status_status
        self.result_envelope = result_envelope
        self.dispatch_calls = 0
        self.fetch_result_calls = 0
        self._handle_task_map = dict(handle_task_map or {})

    def resolve_handle(self, adapter_handle):
        return self._handle_task_map.get(adapter_handle)

    def dispatch(self, payload):
        self.dispatch_calls += 1
        return {
            "adapter_handle": f"af50-handle-{self.dispatch_calls}",
            "status": "pending",
            "dispatch_time": "2026-09-13T00:00:00Z",
        }

    def query_status(self, adapter_handle):
        return {"status": self.query_status_status, "details": ""}

    def fetch_result(self, adapter_handle):
        self.fetch_result_calls += 1
        envelope = self.result_envelope
        if isinstance(envelope, BaseException):
            raise envelope
        if callable(envelope):
            return envelope(adapter_handle)
        return envelope

    def cancel_task(self, adapter_handle):
        return {"cancelled": False, "status": self.query_status_status}

    def resume_task(self, adapter_handle, payload):
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


class RecordingTransport:
    """Exact-session delivery double returning the bound reconciliation ACK."""

    def __init__(self, *, mode: str = "ack") -> None:
        self.mode = mode
        self.attempts: list[dict[str, str]] = []

    def deliver(self, *, session_ref: str, envelope: str):
        self.attempts.append({"session_ref": session_ref, "envelope": envelope})
        if self.mode == "no_ack":
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.COMPLETED, response_text="no ack line"
            )
        task_id = ""
        digest = ""
        for line in envelope.splitlines():
            if line.startswith("canonical_task_id="):
                task_id = line.split("=", 1)[1].strip()
            elif line.startswith("card_digest="):
                digest = line.split("=", 1)[1].strip()
        return DeliveryAttemptEvidence(
            outcome=DeliveryTransportOutcome.COMPLETED,
            response_text=(
                "reconciled\n"
                f"AOTA_COMPLETION_ACK_V1 canonical_task_id={task_id} card_digest={digest}"
            ),
        )


def _build_dispatcher(world: _World, host, store_path: Path, *, origin: str = REAL_SESSION):
    store = FileBackedExecutionStateStore(store_path)
    dispatcher = create_production_execution_dispatcher(
        default_cwd=world.root,
        host_client=host,
        runtime_config=world.runtime_config,
        state_store=store,
        origin_session_ref=origin,
    )
    return dispatcher, store


def _build_coordinator(dispatcher, store, *, transport=None, provider=None):
    return DurableCompletionCoordinator(
        dispatcher=dispatcher,
        store=store,
        transport=transport,
        admission_limits={TEST_SCOPE: 5},
        semantic_return_provider=provider,
    )


def _dispatch_task(dispatcher, task_id: str, *, constraints: dict | None = None):
    return dispatcher.dispatch(
        _make_package(task_id, constraints=constraints), target_executor_id=EXECUTOR_ID
    )


def _write_result_and_return(world: _World, task_id: str):
    rref = handoff_write(
        mode="result",
        semantic={"summary": "af50 w1 bounded artifact written", "work_done": "done"},
        caller_role="coder",
        sandbox=world.sandbox,
        task_id=task_id,
    )
    completion = task_return(
        status="completed",
        result_ref=rref.ref,
        caller_role="coder",
        caller_task_id=task_id,
        sandbox=world.sandbox,
    )
    return rref, completion


# ---------------------------------------------------------------------------
# Frozen invariant markers
# ---------------------------------------------------------------------------


def test_frozen_timeout_terminal_truth_markers() -> None:
    from aota_forge.adapters.hermes.executor import HERMES_STATUS_MAP

    # The raw status-observation vocabulary stays fail-closed.
    assert HERMES_STATUS_MAP["timeout"] is CanonicalTaskState.UNKNOWN
    assert UNTRUSTWORTHY_OBSERVATION_TIMEOUT_IS_UNKNOWN is True
    assert AUTHORITATIVE_EXECUTION_TIMEOUT_IS_TERMINAL is True
    assert HERMES_STATUS_MAP_GLOBAL_FLIP is False
    # W9 semantic-success truth unchanged.
    assert PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS is False
    assert SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN is True
    # No retry/budget workaround.
    assert RUNTIME_AUTO_RETRY_ON_TIMEOUT is False
    assert COMPLETION_CONTINUATION_BUDGET_IS_ROOT_CAUSE is False


# ---------------------------------------------------------------------------
# V1 — untrustworthy observations stay UNKNOWN (matrix items 1-3, 13-14)
# ---------------------------------------------------------------------------


class TestUntrustworthyTimeoutObservation:
    def test_timeout_observation_with_unavailable_result_stays_unknown(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "fetchfail")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=RuntimeError("result fetch failed (transient transport)"),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-fetchfail"
        _dispatch_task(dispatcher, task_id)

        report = coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None
        assert record.worker_result_card is None
        assert report.observations[task_id] == RECOVER_UNKNOWN_PERSISTED

    def test_unreachable_observation_stays_unknown(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "unreachable")
        host = ProgrammableHermesHostClient(
            query_status_status="unreachable",
            result_envelope={
                "status": "unreachable",
                "error": {"code": "TASK_NOT_FOUND", "message": "unknown adapter handle"},
            },
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-unreachable"
        _dispatch_task(dispatcher, task_id)

        report = coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None
        assert report.observations[task_id] == RECOVER_UNKNOWN_PERSISTED

    def test_unknown_raw_status_stays_unknown(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "rawunknown")
        host = ProgrammableHermesHostClient(
            query_status_status="some-new-host-state",
            result_envelope={
                "status": "some-new-host-state",
                "error": {"code": "HERMES_EXECUTION_UNKNOWN", "message": "unknown"},
            },
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-rawunknown"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None

    def test_result_parse_failure_does_not_false_terminalize(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "parsefail")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope="not-a-mapping-result-observation",
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-parsefail"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None
        assert record.worker_result_card is None

    def test_still_running_result_does_not_terminalize(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "stillrunning")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope={"status": "running"},
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-stillrunning"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None

    def test_unknown_observation_remains_recovery_eligible(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "eligible")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=RuntimeError("transient fetch failure"),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-eligible"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        ids = [r.canonical_task_id for r in store.scan_requiring_recovery()]
        assert task_id in ids
        assert store.get(task_id).canonical_task_state == CanonicalTaskState.UNKNOWN


# ---------------------------------------------------------------------------
# V1 — authoritative timeout terminalizes FAILED (matrix items 4-7)
# ---------------------------------------------------------------------------


class TestAuthoritativeTimeoutTerminalTruth:
    def _run_timeout(self, tmp_path: Path):
        world = _make_world(tmp_path, "authz")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(TIMEOUT_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-authz"
        dispatch = _dispatch_task(dispatcher, task_id, constraints={"timeout_seconds": 10})
        report = coordinator.recover_once()
        return world, host, dispatcher, store, coordinator, task_id, dispatch, report

    def test_authoritative_timeout_terminalizes_failed(self, tmp_path: Path) -> None:
        _, _, _, store, _, task_id, _, report = self._run_timeout(tmp_path)
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result is not None
        assert report.observations[task_id] == RECOVER_TERMINAL_PERSISTED

    def test_canonical_result_timeout_shape(self, tmp_path: Path) -> None:
        _, _, _, store, _, task_id, _, _ = self._run_timeout(tmp_path)
        result = store.get(task_id).terminal_result
        assert result.status == "failed"
        assert result.canonical_task_state == "FAILED"
        assert result.ok is False

    def test_execution_timeout_error_code_and_retryable(self, tmp_path: Path) -> None:
        _, _, _, store, _, task_id, _, _ = self._run_timeout(tmp_path)
        result = store.get(task_id).terminal_result
        assert result.error["code"] == "EXECUTION_TIMEOUT"
        assert result.error["retryable"] is False

    def test_timeout_failure_card_is_truthful_and_delivery_eligible(
        self, tmp_path: Path
    ) -> None:
        _, _, _, store, _, task_id, _, _ = self._run_timeout(tmp_path)
        record = store.get(task_id)
        assert record.worker_result_card is not None
        assert record.worker_result_card["outcome"] == "failure"
        assert record.worker_result_card_digest == card_digest_for(
            dict(record.worker_result_card)
        )
        assert record.delivery_state == DeliveryState.PENDING

    def test_timeout_terminalization_does_not_redispatch_or_retry(
        self, tmp_path: Path
    ) -> None:
        _, host, _, store, coordinator, task_id, _, _ = self._run_timeout(tmp_path)
        assert host.dispatch_calls == 1
        second = coordinator.recover_once()
        assert second.observations[task_id] == RECOVER_TERMINAL_ALREADY_DURABLE
        assert store.get(task_id).canonical_task_state == CanonicalTaskState.FAILED
        assert host.dispatch_calls == 1

    def test_timeout_terminalization_when_result_provider_is_absent(
        self, tmp_path: Path
    ) -> None:
        # Execution timeout is a mechanical terminal failure: no task.return
        # is required, even with the W9 provider wired.
        world = _make_world(tmp_path, "authzprovider")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(TIMEOUT_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)
        task_id = f"{PROJECT_ID}:M1:W1:af50-authz-provider"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == "EXECUTION_TIMEOUT"
        assert record.terminal_result.error["code"] != SEMANTIC_RESULT_NOT_PROVEN
        assert record.worker_result_card["outcome"] == "failure"


# ---------------------------------------------------------------------------
# V1 — unchanged semantics (matrix items 8-10)
# ---------------------------------------------------------------------------


class TestUnchangedSemantics:
    def test_ordinary_success_unchanged(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "success")
        host = ProgrammableHermesHostClient(
            query_status_status="done",
            result_envelope=dict(DONE_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-success"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.COMPLETED
        assert record.terminal_result.ok is True
        assert record.worker_result_card["outcome"] == "success"

    def test_ordinary_terminal_failure_unchanged(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "failure")
        host = ProgrammableHermesHostClient(
            query_status_status="error",
            result_envelope=dict(FAILED_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-failure"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == "EXECUTION_FAILED"
        assert record.worker_result_card["outcome"] == "failure"

    def test_cancelled_unchanged(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "cancelled")
        host = ProgrammableHermesHostClient(
            query_status_status="cancelled",
            result_envelope=dict(CANCELLED_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store)
        task_id = f"{PROJECT_ID}:M1:W1:af50-cancelled"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.CANCELLED
        assert record.terminal_result.error["code"] == "EXECUTION_CANCELLED"


# ---------------------------------------------------------------------------
# V1 — W9 semantic-success gate preserved through the new result path
# (matrix items 11-12)
# ---------------------------------------------------------------------------


class TestW9GatePreserved:
    def test_success_result_without_task_return_is_not_semantic_success(
        self, tmp_path: Path
    ) -> None:
        # Observation is nonterminal/UNKNOWN, authoritative result says done,
        # but no governed task.return exists: must reconcile to truthful
        # failure in the same first persist (never a durable success result).
        world = _make_world(tmp_path, "w9gate")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(DONE_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)
        task_id = f"{PROJECT_ID}:M1:W1:af50-w9gate"
        _dispatch_task(dispatcher, task_id)

        report = coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.ok is False
        assert record.terminal_result.status != "completed"
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN
        assert report.observations[task_id] == RECOVER_SEMANTIC_RESULT_NOT_PROVEN
        assert record.worker_result_card["outcome"] == "failure"

    def test_success_result_with_valid_task_return_is_success(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "w9success")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(DONE_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)
        task_id = f"{PROJECT_ID}:M1:W1:af50-w9success"
        _dispatch_task(dispatcher, task_id)
        rref, completion = _write_result_and_return(world, task_id)
        assert completion["status"] == "completed"

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.COMPLETED
        assert record.terminal_result.ok is True
        assert record.worker_result_card["outcome"] == "success"
        assert record.worker_result_card["result_handoff_ref"]["digest"] == rref.digest


# ---------------------------------------------------------------------------
# V1 — failure Card continuity / delivery envelope (§16, §19)
# ---------------------------------------------------------------------------


class TestTimeoutFailureDelivery:
    def test_timeout_failure_card_delivers_to_exact_origin_session(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "delivery")
        host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(TIMEOUT_ENVELOPE),
        )
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        transport = RecordingTransport()
        coordinator = _build_coordinator(dispatcher, store, transport=transport)
        task_id = f"{PROJECT_ID}:M1:W1:af50-delivery"
        _dispatch_task(dispatcher, task_id)

        coordinator.recover_once()
        delivery = coordinator.deliver_pending_once()
        assert delivery.outcomes[task_id] == DELIVER_ACKNOWLEDGED
        assert len(transport.attempts) == 1
        assert transport.attempts[0]["session_ref"] == REAL_SESSION
        envelope = transport.attempts[0]["envelope"]
        assert "AOTA_WORKER_COMPLETION_V1" in envelope
        assert task_id in envelope
        assert '"outcome":"failure"' in envelope
        final = store.get(task_id)
        assert final.delivery_state == DeliveryState.ACKNOWLEDGED
        assert final.canonical_task_state == CanonicalTaskState.FAILED


# ---------------------------------------------------------------------------
# Targeted V2 — fresh-process reconstruction over the real durable store
# (§21: FileBackedExecutionStateStore + real dispatcher + real coordinator +
# existing Hermes adapter/result projection)
# ---------------------------------------------------------------------------


class TestTargetedV2:
    def test_fresh_process_reconciles_authoritative_timeout_to_delivery(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "v2")
        host_a = ProgrammableHermesHostClient(
            query_status_status="pending",
            result_envelope=dict(TIMEOUT_ENVELOPE),
        )
        dispatcher_a, store_a = _build_dispatcher(world, host_a, world.exec_path)
        task_id = f"{PROJECT_ID}:M1:W1:af50-v2-timeout"
        dispatch = _dispatch_task(dispatcher_a, task_id, constraints={"timeout_seconds": 10})
        assert store_a.get(task_id).canonical_task_state == CanonicalTaskState.QUEUED
        del dispatcher_a, store_a

        # Fresh process: NEW store + dispatcher + coordinator + adapter object
        # over the same durable files. The route is rehydrated from durable
        # executor_id and the handle<->task binding from the adapter-private
        # locator echo (no process-local state is required for proof).
        fresh_host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=dict(TIMEOUT_ENVELOPE),
            handle_task_map={dispatch.adapter_handle: task_id},
        )
        fresh_store = FileBackedExecutionStateStore(world.exec_path)
        fresh_dispatcher = create_production_execution_dispatcher(
            default_cwd=world.root,
            host_client=fresh_host,
            runtime_config=world.runtime_config,
            state_store=fresh_store,
            origin_session_ref=REAL_SESSION,
        )
        transport = RecordingTransport()
        coordinator = _build_coordinator(fresh_dispatcher, fresh_store, transport=transport)
        coordinator.recover_once()

        record = fresh_store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result is not None
        assert record.terminal_result.error["code"] == "EXECUTION_TIMEOUT"
        assert record.terminal_result.error["retryable"] is False
        assert record.worker_result_card["outcome"] == "failure"
        assert record.worker_result_card_digest == card_digest_for(
            dict(record.worker_result_card)
        )
        assert record.delivery_state == DeliveryState.PENDING

        delivery = coordinator.deliver_pending_once()
        assert delivery.outcomes[task_id] == DELIVER_ACKNOWLEDGED
        assert transport.attempts[0]["session_ref"] == REAL_SESSION

        # A second fresh runtime observes ACKed terminal truth and performs no
        # redelivery and no state mutation.
        del coordinator, fresh_dispatcher, fresh_store
        store_c = FileBackedExecutionStateStore(world.exec_path)
        assert store_c.get(task_id).delivery_state == DeliveryState.ACKNOWLEDGED
        assert store_c.get(task_id).canonical_task_state == CanonicalTaskState.FAILED

    def test_fresh_process_untrustworthy_timeout_stays_unknown(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "v2unknown")
        host_a = ProgrammableHermesHostClient(query_status_status="pending")
        dispatcher_a, store_a = _build_dispatcher(world, host_a, world.exec_path)
        task_id = f"{PROJECT_ID}:M1:W1:af50-v2-unknown"
        dispatch = _dispatch_task(dispatcher_a, task_id)
        del dispatcher_a, store_a

        fresh_host = ProgrammableHermesHostClient(
            query_status_status="timeout",
            result_envelope=RuntimeError("transient result fetch failure"),
            handle_task_map={dispatch.adapter_handle: task_id},
        )
        fresh_store = FileBackedExecutionStateStore(world.exec_path)
        fresh_dispatcher = create_production_execution_dispatcher(
            default_cwd=world.root,
            host_client=fresh_host,
            runtime_config=world.runtime_config,
            state_store=fresh_store,
            origin_session_ref=REAL_SESSION,
        )
        coordinator = _build_coordinator(fresh_dispatcher, fresh_store)
        report = coordinator.recover_once()

        record = fresh_store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None
        assert report.observations[task_id] == RECOVER_UNKNOWN_PERSISTED
