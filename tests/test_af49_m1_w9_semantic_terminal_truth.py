"""AF #49 M1/W9 — Semantic Terminal Truth & Success-Card Integrity.

Repairs I49-B007: a Worker process exit 0 was accepted as a successful
semantic result without a durable result handoff or a governed ``task.return``.
Reconciliation derived a success Card whose ``result_handoff_ref.digest`` was
None and delivered it for parent ACK.

Frozen invariant under proof:

    PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS=no

Mechanical Worker process termination and governed semantic task completion are
different facts. Success requires trusted durable evidence: a valid result
handoff for the EXACT canonical task plus a valid ``task.return`` for that exact
active execution. A result handoff write alone is explicitly non-terminal by the
accepted agent-facing contract.

Proof boundary (honest):
  PROVES=deterministic V1 coordinator/receipt/ingress contract proof and a
         targeted fresh-process V2 over the real durable store, the real
         governed handoff store, the real trusted task.return path, and the
         real production completion continuation composition (with an injected
         executor host double and delivery transport double).
  DOES_NOT_PROVE=real Hermes Worker/model execution, the M1 V3 rerun 3, RV1
         acceptance, or #40 dogfood.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aota_forge.composition.execution import (
    build_semantic_return_evidence_provider,
    create_production_execution_dispatcher,
    run_bounded_completion_continuation,
)
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
    card_digest_for,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS,
    RECOVER_SEMANTIC_RESULT_NOT_PROVEN,
    SEMANTIC_RESULT_NOT_PROVEN,
    SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN,
    SUCCESS_CARD_WITH_NULL_OR_NONE_RESULT_DIGEST_ALLOWED,
    TASK_RETURN_REQUIRES_VALID_RESULT_HANDOFF,
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
)
from aota_forge.runtime.config import load_runtime_config
from aota_forge.work_plane.handoff_store import handoff_open, handoff_write
from aota_forge.work_plane.task_facade import task_return
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
    read_task_return_receipt,
    resolve_semantic_return_evidence,
    write_task_return_receipt,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

PROJECT_ID = "aota_forge_w9_fixture"
WORKTREE_ID = "wt-w9"
EXECUTOR_ID = "hermes"
TEST_SCOPE = "hermes:coder"
REAL_SESSION = "20260912_170000_w9real"


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


def _make_package(task_id: str) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=PROJECT_ID,
        canonical_role="coder",
        instruction=f"w9 semantic terminal truth proof {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


class FakeHermesHostClient:
    """Hermes-host-protocol double: mechanical terminal observation only."""

    def __init__(
        self,
        *,
        status: str = "done",
        exit_code: int = 0,
        read_failure: bool = False,
        handle_task_map: dict | None = None,
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.read_failure = read_failure
        self.dispatch_calls = 0
        self._handle_task_map = dict(handle_task_map or {})

    def resolve_handle(self, adapter_handle):
        """Durable handle<->task echo (fresh-process route rehydration seam)."""
        return self._handle_task_map.get(adapter_handle)

    def dispatch(self, payload):
        self.dispatch_calls += 1
        return {
            "adapter_handle": f"w9-handle-{self.dispatch_calls}",
            "status": "pending",
            "dispatch_time": "2026-09-12T00:00:00Z",
        }

    def query_status(self, adapter_handle):
        return {"status": self.status, "details": ""}

    def fetch_result(self, adapter_handle):
        response = {
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": "AOTA_AF49_W9_OK\n",
            "stderr": "",
            "execution_stats": {"duration_ms": 3},
        }
        if self.status == "failed":
            if self.read_failure and self.exit_code == 0:
                response["error"] = {
                    "code": "RESULT_UNAVAILABLE",
                    "message": "Hermes worker output capture failed before the result stream stabilized",
                }
            else:
                response["error"] = {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"}
        elif self.status == "timeout":
            response["error"] = {"code": "EXECUTION_TIMEOUT", "message": "Hermes worker exceeded its bounded timeout"}
        elif self.status == "cancelled":
            response["error"] = {"code": "EXECUTION_CANCELLED", "message": "Hermes worker cancelled"}
        return response

    def cancel_task(self, adapter_handle):
        return {"cancelled": False, "status": self.status}

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


def _build_dispatcher(world: _World, host: FakeHermesHostClient, store_path: Path):
    store = FileBackedExecutionStateStore(store_path)
    dispatcher = create_production_execution_dispatcher(
        default_cwd=world.root,
        host_client=host,
        runtime_config=world.runtime_config,
        state_store=store,
        origin_session_ref=REAL_SESSION,
    )
    return dispatcher, store


def _build_coordinator(
    dispatcher,
    store,
    *,
    transport=None,
    provider=None,
) -> DurableCompletionCoordinator:
    return DurableCompletionCoordinator(
        dispatcher=dispatcher,
        store=store,
        transport=transport,
        admission_limits={TEST_SCOPE: 5},
        semantic_return_provider=provider,
    )


def _dispatch_task(dispatcher, task_id: str):
    return dispatcher.dispatch(_make_package(task_id), target_executor_id=EXECUTOR_ID)


def _write_result_and_return(
    world: _World,
    task_id: str,
    *,
    status: str = "completed",
    semantic: dict | None = None,
):
    sem = semantic or {"summary": "w9 bounded artifact written", "work_done": "done", "validation": "ok"}
    rref = handoff_write(
        mode="result",
        semantic=sem,
        caller_role="coder",
        sandbox=world.sandbox,
        task_id=task_id,
    )
    completion = task_return(
        status=status,
        result_ref=rref.ref,
        caller_role="coder",
        caller_task_id=task_id,
        sandbox=world.sandbox,
    )
    return rref, completion


# ---------------------------------------------------------------------------
# Frozen invariant markers
# ---------------------------------------------------------------------------


def test_frozen_semantic_terminal_truth_markers() -> None:
    assert PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS is False
    assert SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN is True
    assert TASK_RETURN_REQUIRES_VALID_RESULT_HANDOFF is True
    assert SUCCESS_CARD_WITH_NULL_OR_NONE_RESULT_DIGEST_ALLOWED is False
    assert SEMANTIC_RESULT_NOT_PROVEN == "SEMANTIC_RESULT_NOT_PROVEN"


# ---------------------------------------------------------------------------
# V1 — false success base case (§33) / result write only (§34)
# ---------------------------------------------------------------------------


class TestFalseSuccessGate:
    def test_exit0_without_result_handoff_or_task_return_is_not_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "base")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-base"
        _dispatch_task(dispatcher, task_id)
        report = coordinator.recover_once()

        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.canonical_task_state != CanonicalTaskState.COMPLETED
        assert record.terminal_result is not None
        assert record.terminal_result.ok is False
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN
        assert record.terminal_result.canonical_task_state == "FAILED"
        assert record.worker_result_card is not None
        assert record.worker_result_card["outcome"] == "failure"
        assert record.worker_result_card_digest == card_digest_for(dict(record.worker_result_card))
        assert report.observations[task_id] == RECOVER_SEMANTIC_RESULT_NOT_PROVEN
        # The false success was gated BEFORE any sticky COMPLETED persist: the
        # record never carries a success result.
        assert record.terminal_result.status != "completed"

    def test_result_handoff_only_without_task_return_is_not_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "resultonly")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-resultonly"
        _dispatch_task(dispatcher, task_id)
        # Worker wrote a valid full result handoff but did NOT terminate
        # (handoff.write(mode=result) alone is explicitly non-terminal).
        rref = handoff_write(
            mode="result",
            semantic={"summary": "done but not returned"},
            caller_role="coder",
            sandbox=world.sandbox,
            task_id=task_id,
        )
        assert resolve_semantic_return_evidence(world.sandbox, task_id) is None
        assert read_task_return_receipt(world.sandbox, task_id) is None

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN
        assert record.worker_result_card["outcome"] == "failure"
        assert rref.mode == "result"


# ---------------------------------------------------------------------------
# V1 — valid success (§21/§35) and success-card integrity (§25/§39)
# ---------------------------------------------------------------------------


class TestValidSuccess:
    def test_valid_result_plus_task_return_is_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "success")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-success"
        _dispatch_task(dispatcher, task_id)
        rref, completion = _write_result_and_return(world, task_id)
        assert completion["status"] == "completed"

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.COMPLETED
        assert record.terminal_result is not None
        assert record.terminal_result.ok is True
        assert record.terminal_result.status == "completed"
        assert record.worker_result_card["outcome"] == "success"
        assert record.worker_result_card_digest == card_digest_for(dict(record.worker_result_card))

    def test_success_card_carries_validated_result_handoff_digest(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "digest")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-digest"
        _dispatch_task(dispatcher, task_id)
        rref, _ = _write_result_and_return(world, task_id)
        coordinator.recover_once()

        record = store.get(task_id)
        handoff_ref = record.worker_result_card["result_handoff_ref"]
        assert handoff_ref["digest"] == rref.digest
        assert handoff_ref["digest"] is not None
        assert handoff_ref["digest"] != "None"
        assert len(handoff_ref["digest"]) == 64
        evidence = resolve_semantic_return_evidence(world.sandbox, task_id)
        assert evidence is not None
        assert evidence.permits_success is True
        assert evidence.result_digest == rref.digest


# ---------------------------------------------------------------------------
# V1 — task.return status semantics (§22/§37)
# ---------------------------------------------------------------------------


class TestTaskReturnStatus:
    @pytest.mark.parametrize(
        ("status", "expected_code"),
        [("blocked", "SEMANTIC_STOP"), ("failed", "EXECUTION_FAILED")],
    )
    def test_non_completed_governed_return_never_becomes_success(
        self, tmp_path: Path, status: str, expected_code: str
    ) -> None:
        world = _make_world(tmp_path, f"status-{status}")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-status-{status}"
        _dispatch_task(dispatcher, task_id)
        _write_result_and_return(
            world,
            task_id,
            status=status,
            semantic={"summary": f"worker reported {status}"},
        )
        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.ok is False
        assert record.terminal_result.error["code"] == expected_code
        assert record.worker_result_card["outcome"] == "failure"


# ---------------------------------------------------------------------------
# V1 — wrong / tampered / non-result / missing result fail-closed (§20/§36)
# ---------------------------------------------------------------------------


class TestWrongResultFailClosed:
    def test_task_return_wrong_task_fails_closed(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "wrongtask")
        rref = handoff_write(
            mode="result",
            semantic={"summary": "done"},
            caller_role="coder",
            sandbox=world.sandbox,
            task_id="task-A",
        )
        with pytest.raises(ValueError, match="wrong-task"):
            task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="coder",
                caller_task_id="task-B",
                sandbox=world.sandbox,
            )

    def test_task_return_wrong_role_fails_closed(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "wrongrole")
        rref = handoff_write(
            mode="result",
            semantic={"summary": "done"},
            caller_role="coder",
            sandbox=world.sandbox,
            task_id="task-A",
        )
        with pytest.raises(ValueError, match="wrong-role"):
            task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="reviewer",
                caller_task_id="task-A",
                sandbox=world.sandbox,
            )

    def test_task_return_requires_result_mode_handoff(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "wrongmode")
        wref = handoff_write(
            mode="work_item",
            semantic={"objective": "obj", "bounded_scope": "scope"},
            caller_role="task-main",
            sandbox=world.sandbox,
        )
        with pytest.raises(ValueError, match="requires result handoff"):
            task_return(
                status="completed",
                result_ref=wref.ref,
                caller_role="coder",
                caller_task_id="task-A",
                sandbox=world.sandbox,
            )

    def test_missing_result_handoff_fails_closed(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "missing")
        with pytest.raises(ValueError):
            task_return(
                status="completed",
                result_ref=f"handoff:result:{'0' * 64}",
                caller_role="coder",
                caller_task_id="task-A",
                sandbox=world.sandbox,
            )

    def test_removed_result_handoff_after_receipt_is_not_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "removed")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-removed"
        _dispatch_task(dispatcher, task_id)
        rref, _ = _write_result_and_return(world, task_id)
        # Remove the durable result handoff after the receipt was written:
        # the parent proof must fail closed (no success).
        handoff_path = world.root / ".aota" / "handoffs" / f"{rref.digest}.json"
        assert handoff_path.is_file()
        handoff_path.unlink()
        assert resolve_semantic_return_evidence(world.sandbox, task_id) is None

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN

    def test_tampered_result_handoff_is_not_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "tampered")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-tampered"
        _dispatch_task(dispatcher, task_id)
        rref, _ = _write_result_and_return(world, task_id)
        handoff_path = world.root / ".aota" / "handoffs" / f"{rref.digest}.json"
        data = json.loads(handoff_path.read_text(encoding="utf-8"))
        data["semantic"]["summary"] = "tampered"
        handoff_path.write_text(json.dumps(data), encoding="utf-8")
        assert resolve_semantic_return_evidence(world.sandbox, task_id) is None

        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN


# ---------------------------------------------------------------------------
# V1 — mechanical failures preserved (§23/§38)
# ---------------------------------------------------------------------------


class TestMechanicalFailuresPreserved:
    def test_nonzero_exit_is_existing_mechanical_failure(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "nonzero")
        host = FakeHermesHostClient(status="failed", exit_code=3)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-nonzero"
        _dispatch_task(dispatcher, task_id)
        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == "EXECUTION_FAILED"
        assert record.terminal_result.error["code"] != SEMANTIC_RESULT_NOT_PROVEN
        assert record.worker_result_card["outcome"] == "failure"

    def test_cancelled_is_existing_mechanical_failure(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "cancelled")
        host = FakeHermesHostClient(status="cancelled", exit_code=None)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-cancelled"
        _dispatch_task(dispatcher, task_id)
        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.CANCELLED
        assert record.terminal_result.error["code"] == "EXECUTION_CANCELLED"
        assert record.worker_result_card["outcome"] == "failure"

    def test_read_failure_with_exit0_preserves_result_unavailable(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "readfail")
        host = FakeHermesHostClient(status="failed", exit_code=0, read_failure=True)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-readfail"
        _dispatch_task(dispatcher, task_id)
        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == "RESULT_UNAVAILABLE"
        assert record.worker_result_card["outcome"] == "failure"

    def test_timeout_observation_stays_nonterminal_not_semantic_failure(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "timeout")
        host = FakeHermesHostClient(status="timeout", exit_code=None)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-timeout"
        _dispatch_task(dispatcher, task_id)
        coordinator.recover_once()
        record = store.get(task_id)
        # Existing governance maps timeout to nonterminal UNKNOWN (truthful
        # uncertainty); it is not collapsed into a semantic-return failure.
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None
        assert record.worker_result_card is None


# ---------------------------------------------------------------------------
# V1 — truthful failure Card + delivery eligibility (§24/§41 component)
# ---------------------------------------------------------------------------


class TestFailureDelivery:
    def test_exit0_without_semantic_return_delivers_failure_card(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "faildelivery")
        host = FakeHermesHostClient(status="done", exit_code=0)
        transport = RecordingTransport(mode="ack")
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        provider = WorktreeSemanticReturnEvidenceProvider(world.sandbox)
        coordinator = _build_coordinator(dispatcher, store, transport=transport, provider=provider)

        task_id = f"{PROJECT_ID}:M1:W1:w9-faildelivery"
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
        record = store.get(task_id)
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED
        assert record.canonical_task_state == CanonicalTaskState.FAILED


# ---------------------------------------------------------------------------
# V1 — ingress trusted task binding for result handoffs (success-path integrity)
# ---------------------------------------------------------------------------


class TestResultHandoffTaskBinding:
    def test_ingress_result_handoff_is_bound_to_trusted_task_and_returnable(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="w9-ingress-"))
        (tmp / ".aota").mkdir(parents=True, exist_ok=True)
        (tmp / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
        evidence = resolve_trusted_project_evidence(worktree_root=tmp, project_id=PROJECT_ID)
        sandbox = bind_worktree_sandbox(evidence, WORKTREE_ID, tmp, expected_project_id=PROJECT_ID)

        task_id = f"{PROJECT_ID}:M1:W1:w9-ingress"
        worker_surface = create_role_tool_surface(
            "coder", eager=("handoff.open", "handoff.write", "task.return"), progressive=()
        )
        from aota_forge.work_plane.handoff import TaskHandoff

        work_handoff = TaskHandoff(
            work_role="coder",
            task_kind="test",
            objective="obj",
            bounded_scope="scope",
            validation_expectations=("v",),
            semantic_stop_expectations=("s",),
        )
        binding = CanonicalDispatchBinding(
            canonical_task_id=task_id,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            handoff=work_handoff,
            sandbox=sandbox,
            tool_surface=worker_surface,
        )
        reset_execution_dispatcher()
        try:
            write = dispatch_via_core(
                "handoff.write",
                {"mode": "result", "payload": {"summary": "done via ingress"}},
                binding,
            )
            assert write.ok, write.error
            rref = write.payload
            opened = handoff_open(rref["ref"], "full", sandbox=sandbox)
            assert opened["mode"] == "result"
            # The Control Plane mechanically bound the trusted canonical task;
            # the Worker/model never supplied this control field.
            assert opened["envelope"]["task_id"] == task_id

            returned = dispatch_via_core(
                "task.return",
                {"status": "completed", "result_ref": rref["ref"]},
                binding,
            )
            assert returned.ok, returned.error
            assert returned.payload["task_id"] == task_id
            evidence = resolve_semantic_return_evidence(sandbox, task_id)
            assert evidence is not None
            assert evidence.permits_success is True
        finally:
            reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# Receipt durability / trust boundary
# ---------------------------------------------------------------------------


class TestTaskReturnReceipt:
    def test_receipt_is_durable_idempotent_and_contradiction_fails_closed(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "receipt")
        rref = handoff_write(
            mode="result",
            semantic={"summary": "done"},
            caller_role="coder",
            sandbox=world.sandbox,
            task_id="task-R",
        )
        first = write_task_return_receipt(
            world.sandbox,
            canonical_task_id="task-R",
            result_ref=rref.ref,
            result_digest=rref.digest,
            status="completed",
        )
        second = write_task_return_receipt(
            world.sandbox,
            canonical_task_id="task-R",
            result_ref=rref.ref,
            result_digest=rref.digest,
            status="completed",
        )
        assert first.to_dict() == second.to_dict()
        loaded = read_task_return_receipt(world.sandbox, "task-R")
        assert loaded is not None
        assert loaded.result_digest == rref.digest
        with pytest.raises(ValueError, match="contradicts"):
            write_task_return_receipt(
                world.sandbox,
                canonical_task_id="task-R",
                result_ref=rref.ref,
                result_digest=rref.digest,
                status="failed",
            )

    def test_read_receipt_absent_for_wrong_task(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "receipt-miss")
        assert read_task_return_receipt(world.sandbox, "task-none") is None

    def test_receipt_tamper_fails_closed(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "receipt-tamper")
        rref = handoff_write(
            mode="result",
            semantic={"summary": "done"},
            caller_role="coder",
            sandbox=world.sandbox,
            task_id="task-T",
        )
        write_task_return_receipt(
            world.sandbox,
            canonical_task_id="task-T",
            result_ref=rref.ref,
            result_digest=rref.digest,
            status="blocked",
        )
        import hashlib

        receipt_path = (
            world.root
            / ".aota"
            / "task_return_receipts"
            / f"{hashlib.sha256(b'task-T').hexdigest()}.json"
        )
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
        data["status"] = "completed"
        receipt_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            read_task_return_receipt(world.sandbox, "task-T")
        assert resolve_semantic_return_evidence(world.sandbox, "task-T") is None


# ---------------------------------------------------------------------------
# Targeted V2 — fresh-process terminal reconciliation (§40/§42)
# ---------------------------------------------------------------------------


class TestTargetedFreshProcessV2:
    def test_fresh_process_proves_valid_success_and_no_false_success(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "v2")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)

        good_task = f"{PROJECT_ID}:M1:W1:w9-v2-good"
        bad_task = f"{PROJECT_ID}:M1:W1:w9-v2-bad"
        good_dispatch = _dispatch_task(dispatcher, good_task)
        bad_dispatch = _dispatch_task(dispatcher, bad_task)
        good_ref, _ = _write_result_and_return(world, good_task)
        # bad_task: exit 0, no result handoff, no task.return.
        del dispatcher, store

        # Fresh process reconstruction: NEW dispatcher/coordinator/store objects
        # over the same durable files. No process-local task.return memory and
        # no injected in-memory evidence.
        fresh_host = FakeHermesHostClient(
            status="done",
            exit_code=0,
            handle_task_map={
                good_dispatch.adapter_handle: good_task,
                bad_dispatch.adapter_handle: bad_task,
            },
        )
        fresh_store = FileBackedExecutionStateStore(world.exec_path)
        fresh_dispatcher = create_production_execution_dispatcher(
            default_cwd=world.root,
            host_client=fresh_host,
            runtime_config=world.runtime_config,
            state_store=fresh_store,
            origin_session_ref=REAL_SESSION,
        )
        provider = build_semantic_return_evidence_provider(
            worktree_root=world.root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
        )
        coordinator = _build_coordinator(fresh_dispatcher, fresh_store, provider=provider)
        coordinator.recover_once()

        good = fresh_store.get(good_task)
        assert good.canonical_task_state == CanonicalTaskState.COMPLETED
        assert good.terminal_result.ok is True
        assert good.worker_result_card["outcome"] == "success"
        assert good.worker_result_card["result_handoff_ref"]["digest"] == good_ref.digest

        bad = fresh_store.get(bad_task)
        assert bad.canonical_task_state == CanonicalTaskState.FAILED
        assert bad.terminal_result.ok is False
        assert bad.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN
        assert bad.worker_result_card["outcome"] == "failure"

    def test_production_continuation_delivers_truthful_failure_for_missing_return(
        self, tmp_path: Path
    ) -> None:
        world = _make_world(tmp_path, "v2delivery")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        task_id = f"{PROJECT_ID}:M1:W1:w9-v2delivery"
        dispatch_result = _dispatch_task(dispatcher, task_id)
        del dispatcher, store

        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=world.exec_path,
            worktree_root=world.root,
            runtime_config=world.runtime_config,
            relevant_origin_session_ref=REAL_SESSION,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=5,
            host_client=FakeHermesHostClient(
                status="done",
                exit_code=0,
                handle_task_map={dispatch_result.adapter_handle: task_id},
            ),
            transport=transport,
        )
        assert report.stop_reason == "no_relevant_active_execution"
        record = FileBackedExecutionStateStore(world.exec_path).get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result.error["code"] == SEMANTIC_RESULT_NOT_PROVEN
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED
        assert len(transport.attempts) == 1
        assert '"outcome":"failure"' in transport.attempts[0]["envelope"]

    def test_production_continuation_delivers_success_for_valid_return(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "v2success")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        task_id = f"{PROJECT_ID}:M1:W1:w9-v2success"
        dispatch_result = _dispatch_task(dispatcher, task_id)
        rref, _ = _write_result_and_return(world, task_id)
        del dispatcher, store

        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=world.exec_path,
            worktree_root=world.root,
            runtime_config=world.runtime_config,
            relevant_origin_session_ref=REAL_SESSION,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=5,
            host_client=FakeHermesHostClient(
                status="done",
                exit_code=0,
                handle_task_map={dispatch_result.adapter_handle: task_id},
            ),
            transport=transport,
        )
        assert report.stop_reason == "no_relevant_active_execution"
        record = FileBackedExecutionStateStore(world.exec_path).get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.COMPLETED
        assert record.worker_result_card["outcome"] == "success"
        assert record.worker_result_card["result_handoff_ref"]["digest"] == rref.digest
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED


# ---------------------------------------------------------------------------
# Legacy component boundary — no provider configured keeps historical behavior
# ---------------------------------------------------------------------------


class TestUnconfiguredComponentBoundary:
    def test_no_provider_keeps_component_success_behavior(self, tmp_path: Path) -> None:
        world = _make_world(tmp_path, "legacy")
        host = FakeHermesHostClient(status="done", exit_code=0)
        dispatcher, store = _build_dispatcher(world, host, world.exec_path)
        coordinator = _build_coordinator(dispatcher, store, provider=None)
        task_id = f"{PROJECT_ID}:M1:W1:w9-legacy"
        _dispatch_task(dispatcher, task_id)
        coordinator.recover_once()
        record = store.get(task_id)
        assert record.canonical_task_state == CanonicalTaskState.COMPLETED
        assert record.worker_result_card["outcome"] == "success"
        # Production composition always wires the provider (see host bootstrap
        # and run_bounded_completion_continuation); this documents that the
        # gate is an explicit trusted seam, not an ambient behavior change for
        # component-level test doubles.
