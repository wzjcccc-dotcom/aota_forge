"""AF #005 M2 — headed thin slots own the bounded parent completion lifecycle."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
    OriginSessionRef,
    card_digest_for,
)
from aota_forge.core.execution.adapter import TaskStatusResult
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.mcp_transport import (
    SessionScopedContextRouter,
    UnboundHostContext,
    _SessionSlot,
)
from aota_forge.runtime.completion import (
    COMPLETION_ACK_TOKEN,
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
)
from aota_forge.runtime.config import load_runtime_config
from aota_forge.session_bridge import VerifiedInvocationIdentity
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.result_card import WorkerResultCard
from aota_forge.work_plane.task_facade import task_return, task_start
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
    read_task_return_receipt,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from test_m2_w1_dispatcher_recovery import StableHandleDurableFakeAdapter


PROJECT_ID = "aota_forge"
WORKTREE_ID = "plan005-headed-completion-test"
PLAN_ID = "plan_005_governance_self_hosting_routing_runtime_lifecycle"
PARENT_SESSION = "ses_plan005_headed_parent_exact"


class _DelayedReturnAdapter(StableHandleDurableFakeAdapter):
    """Mechanical executor double that stays RUNNING until task.return is durable."""

    def __init__(self, sandbox: Any) -> None:
        super().__init__()
        self.sandbox = sandbox

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        from aota_forge.work_plane.task_return_receipt import read_task_return_receipt

        self._verify_task(canonical_task_id, adapter_handle)
        state = (
            CanonicalTaskState.COMPLETED
            if read_task_return_receipt(self.sandbox, canonical_task_id) is not None
            else CanonicalTaskState.RUNNING
        )
        return TaskStatusResult(canonical_task_id=canonical_task_id, state=state)

    def _verify_task(self, canonical_task_id: str, adapter_handle: str) -> None:
        task_id, _correlation_id, _outcome = self._parse(adapter_handle)
        if task_id != canonical_task_id:
            raise AssertionError("fake executor received a foreign task handle")


class _ExactParentTransport:
    def __init__(self) -> None:
        self.deliveries: list[dict[str, str]] = []

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence:
        task_id = next(
            line.partition("=")[2]
            for line in envelope.splitlines()
            if line.startswith("canonical_task_id=")
        )
        digest = next(
            line.partition("=")[2]
            for line in envelope.splitlines()
            if line.startswith("card_digest=")
        )
        self.deliveries.append(
            {"session_ref": session_ref, "task_id": task_id, "card_digest": digest}
        )
        return DeliveryAttemptEvidence(
            outcome=DeliveryTransportOutcome.COMPLETED,
            response_text=(
                f"{COMPLETION_ACK_TOKEN} canonical_task_id={task_id} card_digest={digest}"
            ),
        )


def _sandbox(root: Path) -> Any:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-plan005-test",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        manifest_path=".aota/project.yaml",
        name=PROJECT_ID,
        kind="test-synthetic",
        status="active",
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-plan005-test",
        workspace_root=str(root),
        registry_fingerprint="0" * 64,
        listing_fingerprint="0" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, WORKTREE_ID, root)


def _thin_binding() -> Any:
    return SimpleNamespace(
        task_main_runtime_path="thin",
        handoff=SimpleNamespace(work_role=SimpleNamespace(value="task-main")),
    )


def _headed_router(
    tmp_path: Path,
    *,
    session_id: str,
    adapter: Any,
) -> tuple[SessionScopedContextRouter, _SessionSlot, VerifiedInvocationIdentity]:
    context = UnboundHostContext(repo_root=str(tmp_path), origin_session_ref=session_id)
    identity = VerifiedInvocationIdentity(
        session_id=session_id,
        call_id=f"call-{session_id}",
        bridge_id="bridge-test",
        tool="aota_aota_invoke",
        args_sha256="a" * 64,
        nonce="n" * 32,
    )
    slot = _SessionSlot(session_id, context, identity.bridge_id)
    slot.adapter = adapter
    router = object.__new__(SessionScopedContextRouter)
    router.context = context
    router._slots = {session_id: slot}
    router._lock = threading.RLock()
    router._idle = threading.Condition(router._lock)
    router._closed = False
    router._verify_invocation = lambda operation, arguments: (  # type: ignore[method-assign]
        dict(arguments or {}),
        identity,
    )
    return router, slot, identity


def test_headed_thin_task_start_runs_full_parent_completion_without_manual_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bind/bootstrap -> handoff -> task.start -> return receipt -> CARD + exact delivery."""
    root = tmp_path / "project"
    (root / ".aota").mkdir(parents=True)
    sandbox = _sandbox(root)
    store_path = root / ".aota" / "execution-sessions" / "parent.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = FileBackedExecutionStateStore(store_path)
    worker_adapter = _DelayedReturnAdapter(sandbox)
    registry = ExecutorRegistry()
    registry.register(worker_adapter)
    dispatcher = ExecutionDispatcher(
        registry,
        state_store=store,
        origin_session_ref=OriginSessionRef(PARENT_SESSION),
        admission_scope_resolver=lambda _package: "durable-fake:coder",
    )
    transport = _ExactParentTransport()
    completion = DurableCompletionCoordinator(
        dispatcher=dispatcher,
        store=store,
        transport=transport,
        semantic_return_provider=WorktreeSemanticReturnEvidenceProvider(sandbox),
    )
    runtime = SimpleNamespace(
        execution_store_path=store_path,
        execution_store=store,
        completion_coordinator=completion,
        worktree_root=root,
        runtime_config=load_runtime_config(
            config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"]
        ),
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        origin_task_main_session_ref=PARENT_SESSION,
    )

    class BoundThinAdapter:
        _bound_delegate = object()
        _bound_runtime = runtime
        _bound_binding = _thin_binding()

        def __init__(self) -> None:
            self.task_id: str | None = None

        def invoke(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if operation in {"role.bootstrap", "task_main.context"}:
                return {"ok": True, "operation": operation, "payload": {"state": "bound"}}
            if operation == "handoff.write":
                ref = handoff_write(
                    mode=arguments["mode"],
                    semantic=arguments["payload"],
                    caller_role="task-main",
                    sandbox=sandbox,
                    plan_ref=f"local-governance:{PROJECT_ID}/{PLAN_ID}",
                    milestone_id="M2",
                    work_item_id="W1",
                    target_role="coder",
                )
                return {"ok": True, "operation": operation, "payload": {"ref": ref.ref}}
            if operation == "task.start":
                started = task_start(
                    role=arguments["role"],
                    handoff_ref=arguments["handoff_ref"],
                    caller_role="task-main",
                    sandbox=sandbox,
                    dispatcher=dispatcher,
                    thin_task_lifecycle=True,
                    plan_id=PLAN_ID,
                )
                self.task_id = started["task_id"]
                return {"ok": True, "operation": operation, "payload": started}
            raise AssertionError(f"unexpected operation in headed lifecycle test: {operation}")

        def close(self) -> None:
            return None

    bound_adapter = BoundThinAdapter()
    router, slot, _identity = _headed_router(
        tmp_path, session_id=PARENT_SESSION, adapter=bound_adapter
    )

    execution_module = __import__("aota_forge.composition.execution", fromlist=["*"])
    original_continuation = execution_module.run_bounded_completion_continuation
    report_condition = threading.Condition()
    reports: list[Any] = []

    def fast_bounded_continuation(**kwargs: Any) -> Any:
        report = original_continuation(
            **kwargs,
            timeout_seconds=3.0,
            poll_interval_seconds=0.01,
            max_iterations=100,
        )
        with report_condition:
            reports.append(report)
            report_condition.notify_all()
        return report

    monkeypatch.setattr(
        execution_module,
        "run_bounded_completion_continuation",
        fast_bounded_continuation,
    )

    try:
        # The ordinary headed turn's first operation remains role.bootstrap.
        assert router.invoke("role.bootstrap", {})["ok"] is True
        assert slot.completion_recovery_checked is True
        with report_condition:
            assert report_condition.wait_for(lambda: len(reports) == 1, timeout=3)
        assert router.invoke("task_main.context", {"mode": "bind"})["ok"] is True
        written = router.invoke(
            "handoff.write",
            {
                "mode": "work_item",
                "payload": {
                    "work_role": "coder",
                    "task_kind": "plan005-m2-focused-test",
                    "objective": "exercise the headed completion owner",
                    "bounded_scope": "one isolated test fixture",
                    "validation_expectations": ["parent CARD is delivered"],
                    "semantic_stop_expectations": ["stop if evidence is missing"],
                    "milestone_ref": "M2",
                    "work_item_ref": "W1",
                },
            },
        )
        started = router.invoke(
            "task.start",
            {"role": "coder", "handoff_ref": written["payload"]["ref"]},
        )
        assert started["ok"] is True
        task_id = started["payload"]["task_id"]
        assert worker_adapter.physical_dispatch_count == 1

        result_ref = handoff_write(
            mode="result",
            semantic={
                "summary": "headed completion lifecycle test result",
                "work_done": "durable result handoff and task.return receipt written",
                "findings": 0,
            },
            caller_role="coder",
            sandbox=sandbox,
            plan_ref=f"local-governance:{PROJECT_ID}/{PLAN_ID}",
            milestone_id="M2",
            work_item_id="W1",
            task_id=task_id,
        )
        before_return = store.get(task_id)
        assert before_return is not None
        returned = task_return(
            status="completed",
            result_ref=result_ref.ref,
            caller_role="coder",
            caller_task_id=task_id,
            sandbox=sandbox,
        )
        assert returned["parent_store_mutated"] is False
        assert returned["durable_completion"] == "parent_side_reconciliation_pending"
        assert read_task_return_receipt(sandbox, task_id) is not None
        unchanged = store.get(task_id)
        assert unchanged is not None
        assert unchanged.record_revision == before_return.record_revision
        assert unchanged.terminal_result is None
        assert unchanged.worker_result_card is None

        with report_condition:
            assert report_condition.wait_for(lambda: len(reports) >= 2, timeout=4)
        final = store.get(task_id)
        assert final is not None
        assert final.canonical_task_state == CanonicalTaskState.COMPLETED
        assert final.terminal_result is not None
        assert final.worker_result_card is not None
        assert final.worker_result_card_digest == card_digest_for(
            dict(final.worker_result_card)
        )
        WorkerResultCard.from_dict(dict(final.worker_result_card))
        assert final.delivery_state == DeliveryState.ACKNOWLEDGED
        assert transport.deliveries == [
            {
                "session_ref": PARENT_SESSION,
                "task_id": task_id,
                "card_digest": final.worker_result_card_digest,
            }
        ]
        assert reports[-1].relevant_records_remaining == 0
        assert slot.completion_recovery_checked is True

        repeat = original_continuation(
            execution_store_path=store_path,
            worktree_root=root,
            runtime_config=runtime.runtime_config,
            relevant_origin_session_ref=PARENT_SESSION,
            execution_store=store,
            completion_coordinator=completion,
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
            max_iterations=2,
        )
        after_repeat = store.get(task_id)
        assert repeat.iterations == 0
        assert after_repeat is not None
        assert after_repeat.terminal_result == final.terminal_result
        assert after_repeat.worker_result_card == final.worker_result_card
        assert after_repeat.delivery_state == DeliveryState.ACKNOWLEDGED
        assert len(transport.deliveries) == 1
    finally:
        router.close()


def test_recreated_headed_slot_uses_first_bound_call_for_restart_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    calls: list[dict[str, Any]] = []

    def observe(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        started.set()
        return SimpleNamespace(stop_reason="no_relevant_active_execution")

    import aota_forge.composition.execution as execution_module

    monkeypatch.setattr(execution_module, "run_bounded_completion_continuation", observe)
    runtime = SimpleNamespace(
        execution_store_path=tmp_path / "session.json",
        execution_store=object(),
        completion_coordinator=object(),
        worktree_root=tmp_path,
        runtime_config=object(),
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        origin_task_main_session_ref=PARENT_SESSION,
    )

    class Adapter:
        _bound_delegate = object()
        _bound_runtime = runtime
        _bound_binding = _thin_binding()

        def invoke(self, operation: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "operation": operation}

        def close(self) -> None:
            return None

    router, _slot, _identity = _headed_router(
        tmp_path, session_id=PARENT_SESSION, adapter=Adapter()
    )
    try:
        # A new router has no process-local completion marker. Its first
        # successful bound operation resumes from the durable exact-session path.
        assert router.invoke("role.bootstrap", {})["ok"] is True
        assert _slot.completion_recovery_checked is True
        assert started.wait(timeout=3)
        assert calls[0]["relevant_origin_session_ref"] == PARENT_SESSION
        assert calls[0]["execution_store_path"] == runtime.execution_store_path
        assert calls[0]["execution_store"] is runtime.execution_store
        assert calls[0]["completion_coordinator"] is runtime.completion_coordinator
    finally:
        router.close()


def test_worker_task_return_cannot_start_parent_completion_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()

    def forbidden(**_kwargs: Any) -> None:
        started.set()
        raise AssertionError("Worker task.return must not start the parent owner")

    import aota_forge.composition.execution as execution_module

    monkeypatch.setattr(execution_module, "run_bounded_completion_continuation", forbidden)
    runtime = SimpleNamespace(
        execution_store_path=tmp_path / "worker.json",
        execution_store=object(),
        completion_coordinator=object(),
        worktree_root=tmp_path,
        runtime_config=object(),
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        origin_task_main_session_ref=PARENT_SESSION,
    )

    class WorkerBinding:
        task_main_runtime_path = "legacy"
        handoff = SimpleNamespace(work_role=SimpleNamespace(value="coder"))

    class Adapter:
        _bound_delegate = object()
        _bound_runtime = runtime
        _bound_binding = WorkerBinding()

        def invoke(self, operation: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "operation": operation, "parent_store_mutated": False}

        def close(self) -> None:
            return None

    router, _slot, _identity = _headed_router(
        tmp_path, session_id=PARENT_SESSION, adapter=Adapter()
    )
    try:
        result = router.invoke("task.return", {})
        assert result["parent_store_mutated"] is False
        assert started.wait(timeout=0.05) is False
    finally:
        router.close()


def test_completion_owners_are_isolated_by_verified_exact_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_a = "ses_plan005_completion_A"
    session_b = "ses_plan005_completion_B"
    started = {session_a: threading.Event(), session_b: threading.Event()}
    release = threading.Event()
    calls: list[tuple[str, Path]] = []
    calls_lock = threading.Lock()

    def observe(**kwargs: Any) -> SimpleNamespace:
        session = kwargs["relevant_origin_session_ref"]
        with calls_lock:
            calls.append((session, kwargs["execution_store_path"]))
        started[session].set()
        assert release.wait(timeout=3)
        return SimpleNamespace(stop_reason="no_relevant_active_execution")

    import aota_forge.composition.execution as execution_module

    monkeypatch.setattr(execution_module, "run_bounded_completion_continuation", observe)

    def runtime_for(session: str) -> Any:
        return SimpleNamespace(
            execution_store_path=tmp_path / f"{session}.json",
            execution_store=object(),
            completion_coordinator=object(),
            worktree_root=tmp_path,
            runtime_config=object(),
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            origin_task_main_session_ref=session,
        )

    class Adapter:
        def __init__(self, session: str) -> None:
            self._bound_delegate = object()
            self._bound_runtime = runtime_for(session)
            self._bound_binding = _thin_binding()

        def invoke(self, operation: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "operation": operation}

        def close(self) -> None:
            return None

    router, slot_a, identity_a = _headed_router(
        tmp_path, session_id=session_a, adapter=Adapter(session_a)
    )
    context_b = UnboundHostContext(repo_root=str(tmp_path), origin_session_ref=session_b)
    identity_b = VerifiedInvocationIdentity(
        session_id=session_b,
        call_id=f"call-{session_b}",
        bridge_id="bridge-test",
        tool="aota_aota_invoke",
        args_sha256="b" * 64,
        nonce="m" * 32,
    )
    slot_b = _SessionSlot(session_b, context_b, identity_b.bridge_id)
    slot_b.adapter = Adapter(session_b)
    router._slots[session_b] = slot_b
    identities = {session_a: identity_a, session_b: identity_b}
    router._verify_invocation = lambda _operation, arguments: (  # type: ignore[method-assign]
        dict(arguments or {}),
        identities[str((arguments or {}).get("session"))],
    )

    try:
        assert router.invoke("task.start", {"session": session_a})["ok"] is True
        assert router.invoke("task.start", {"session": session_b})["ok"] is True
        assert started[session_a].wait(timeout=3)
        assert started[session_b].wait(timeout=3)
        assert set(calls) == {
            (session_a, tmp_path / f"{session_a}.json"),
            (session_b, tmp_path / f"{session_b}.json"),
        }
    finally:
        release.set()
        router.close()
