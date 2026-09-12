"""AF #49 M1/W6 — parent session identity & autonomous completion continuity repair.

Repairs:
  I49-B002: the first task-main turn durably bound the pre-session placeholder
            (``pending-<ts>-<pid>``) instead of the real exact Hermes task-main
            session identity, so child completions could never target the real
            parent session.
  I49-B004: after the parent turn ended, no production lifecycle owner
            consumed the Worker terminal receipt and drove parent-side
            reconciliation / deterministic CARD / exact-session delivery.

Target production shape:

    phase 1: launch real Hermes task-main -> real exact session identity S
             (unbound placeholder origin; child dispatch / coordinator
             activation fails closed mechanically)

    phase 2: bind S into the trusted bootstrap -> continue the SAME session S
             through the accepted exact-session reentry seam with the
             operator-owned startup prompt -> child dispatch now allowed and
             every durable child record carries origin_session_ref == S

    completion: Worker terminal durable receipt
             -> runtime-owned bounded continuation (launcher, no model call)
             -> ExecutionDispatcher reconciliation -> DurableCompletionCoordinator
             -> deterministic WorkerResultCard -> Hermes completion transport
             -> exact parent session S re-entry

Proof boundary (honest):
  PROVES=deterministic component integration across the real production
         dispatcher/coordinator/transport composition, the canonical ingress
         fail-closed projection, the two-phase launcher seam with a scripted
         Hermes host/reentry boundary, and durable-store fresh-process
         continuation.
  DOES_NOT_PROVE=real Hermes session creation, real Worker execution, real
         parent re-entry, or the M1 V3 rerun. Those require the bounded real
         V3 rerun after W6.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    HermesReentryResult,
)
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition.execution import (
    COMPLETION_CONTINUATION_BOUNDED,
    COMPLETION_CONTINUATION_SCANS_FOREVER,
    COMPLETION_CONTINUATION_STOP_ITERATION_BUDGET,
    COMPLETION_CONTINUATION_STOP_LIFECYCLE_TIMEOUT,
    COMPLETION_CONTINUATION_STOP_NO_RELEVANT,
    MANUAL_ADVANCE_ONCE_REQUIRED,
    MODEL_CALL_REQUIRED_FOR_COMPLETION_TRIGGER,
    NEW_GENERIC_BACKGROUND_SCHEDULER_CREATED,
    NEW_GENERIC_EVENT_BUS_CREATED,
    OPERATOR_POLLING_REQUIRED,
    PRODUCTION_COMPLETION_RUNTIME_OWNER,
    create_durable_completion_coordinator,
    create_production_execution_dispatcher,
    run_bounded_completion_continuation,
)
from aota_forge.composition.task_main_daily_launcher import (
    PHASE1_SESSION_BOOTSTRAP_PROMPT,
    TWO_PHASE_SESSION_BINDING,
    DailyTaskMainLauncher,
)
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_RELPATH,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.execution.dispatcher import DispatcherError
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPersistenceFailureError,
    ExecutionPhase,
    FileBackedExecutionStateStore,
    OriginSessionRef,
    UnboundOriginSessionError,
    UNBOUND_ORIGIN_SESSION_REF_PREFIX,
    UNBOUND_ORIGIN_CAN_CREATE_CHILD_EXECUTION,
    card_digest_for,
    is_bound_origin_session_ref,
    is_placeholder_origin_session_ref,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.runtime.config import load_runtime_config
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    DELIVER_DROPPED_ORIGIN_MISSING,
    DELIVER_DROPPED_SESSION_MISSING,
    DELIVER_DROPPED_UNBOUND_ORIGIN,
    DELIVER_RELEASED_ACK_NOT_PROVEN,
    DELIVER_RELEASED_TRANSPORT_ERROR,
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    governance_projection_for_result,
)
from aota_forge.work_plane.result_card import project_worker_result_card

PROJECT_ID = "aota_forge_w6_fixture"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
ENTRY_BASE = "a" * 40
MILESTONE_ID = "M1"
REAL_SESSION = "20260912_120000_w6real"
PLACEHOLDER = f"{UNBOUND_ORIGIN_SESSION_REF_PREFIX}1700000000-4242"
EXECUTOR_ID = "hermes"
TEST_SCOPE = "hermes:coder"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W6 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1

#### M1/W1 — Authoritative alpha slice
Create the bounded W6 alpha artifact.

#### M1/W2 — Authoritative beta slice
Create the bounded W6 beta artifact.
"""


def _live_view():
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af49w6")
    live, _next = project_milestone_views(
        doc, plan_authority=PLAN_AUTH, plan_digest="d" * 64, plan_source_revision="rev-af49w6"
    )
    return live


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


def _make_worktree(tmp_path: Path, *, origin: str = REAL_SESSION, run_id: str = "w6") -> tuple[Path, Path]:
    root = tmp_path / f"wt_{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{run_id}.json"
    cfg_path.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
    coord_path = root / ".aota" / "coordinator.json"
    exec_path = root / ".aota" / "execution.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-w6",
        coordinator_store_path=coord_path,
        execution_store_path=exec_path,
        runtime_config_path=cfg_path,
        origin_task_main_session_ref=origin,
        live_plan_view=_live_view(),
        next_milestone_view=None,
    )
    return root, cfg_path


def make_package(task_id: str = "w6-task-1") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=PROJECT_ID,
        canonical_role="coder",
        instruction=f"w6 parent session continuation proof {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


class FakeHermesHostClient:
    """Minimal Hermes-host-protocol double (component boundary injection)."""

    def __init__(self, *, terminal_after: int = 1, handle_task_map: dict | None = None) -> None:
        self.terminal_after = max(1, int(terminal_after))
        self.status_calls = 0
        self.dispatch_calls = 0
        self.handles: list[str] = []
        self._handle_task_map = dict(handle_task_map or {})

    def resolve_handle(self, adapter_handle):
        """Durable handle<->task echo (fresh-process route rehydration seam)."""
        return self._handle_task_map.get(adapter_handle)

    def dispatch(self, payload):
        self.dispatch_calls += 1
        handle = f"w6-handle-{self.dispatch_calls}"
        self.handles.append(handle)
        return {"adapter_handle": handle, "status": "pending", "dispatch_time": "2026-09-12T00:00:00Z"}

    def query_status(self, adapter_handle):
        self.status_calls += 1
        if self.status_calls < self.terminal_after:
            return {"status": "running", "details": "worker active"}
        return {"status": "done", "details": ""}

    def fetch_result(self, adapter_handle):
        return {
            "status": "done",
            "exit_code": 0,
            "stdout": "AOTA_AF49_W6_OK\n",
            "stderr": "",
            "execution_stats": {"duration_ms": 7},
        }

    def cancel_task(self, adapter_handle):
        return {"cancelled": False, "status": "done"}

    def resume_task(self, adapter_handle, payload):
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


class RecordingTransport:
    """Exact-session delivery seam double with a configurable mechanical outcome."""

    def __init__(self, *, mode: str = "ack") -> None:
        self.mode = mode
        self.attempts: list[dict[str, str]] = []

    def deliver(self, *, session_ref: str, envelope: str):
        self.attempts.append({"session_ref": session_ref, "envelope": envelope})
        if self.mode == "exception":
            raise RuntimeError("simulated transport failure")
        if self.mode == "not_found":
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.NOT_FOUND,
                response_text=None,
                detail="exact session missing",
            )
        task_id = ""
        digest = ""
        for line in envelope.splitlines():
            if line.startswith("canonical_task_id="):
                task_id = line.split("=", 1)[1].strip()
            elif line.startswith("card_digest="):
                digest = line.split("=", 1)[1].strip()
        if self.mode == "no_ack":
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.COMPLETED,
                response_text="I read the completion card but produced no ack line.",
            )
        if self.mode == "wrong_ack":
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.COMPLETED,
                response_text=(
                    "AOTA_COMPLETION_ACK_V1 canonical_task_id=foreign-task "
                    "card_digest=" + "0" * 64
                ),
            )
        return DeliveryAttemptEvidence(
            outcome=DeliveryTransportOutcome.COMPLETED,
            response_text=(
                "reconciled\n"
                f"AOTA_COMPLETION_ACK_V1 canonical_task_id={task_id} card_digest={digest}"
            ),
        )


def _seed_terminal_record(
    store_path: Path,
    task_id: str,
    *,
    origin: str | None,
):
    """Seed a terminal DISPATCHED record through the durable store contract."""
    store = FileBackedExecutionStateStore(store_path)
    record = DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id=EXECUTOR_ID,
        package_id=f"{task_id}:pkg",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"attempt-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint="f" * 32,
        execution_phase=ExecutionPhase.PREPARED,
        canonical_task_state=CanonicalTaskState.CREATED,
        origin_session_ref=OriginSessionRef(value=origin) if origin is not None else None,
        admission_scope=TEST_SCOPE,
    )
    store.create(record)
    record = store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": f"seed-handle-{task_id}",
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-09-12T00:00:00+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING,
        },
    )
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id=EXECUTOR_ID,
        result_data={"proof": "w6-seed"},
        correlation_id=f"corr-{task_id}",
    )
    card = project_worker_result_card(
        result, governance_projection_for_result(result), "coder", summary="w6 seeded terminal"
    )
    store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "canonical_task_state": CanonicalTaskState.COMPLETED,
            "terminal_result": result.to_dict(),
            "worker_result_card": card.canonical_dict(),
            "worker_result_card_digest": card.compute_card_digest(),
        },
    )
    return store


@pytest.fixture(autouse=True)
def _clean_env():
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_synth = os.environ.get("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE")
    yield
    for key, old in ((BOOTSTRAP_ENV_ROOT, old_root), ("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", old_synth)):
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


# ---------------------------------------------------------------------------
# 1/W6-1/2 — unbound placeholder can never create a durable child execution
# ---------------------------------------------------------------------------


class TestUnboundOriginDispatchGate:
    def test_placeholder_origin_dispatch_fails_closed(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=PLACEHOLDER, run_id="unbound")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=PLACEHOLDER,
        )
        assert dispatcher.origin_session_is_placeholder is True
        with pytest.raises(UnboundOriginSessionError) as excinfo:
            dispatcher.dispatch(make_package("unbound-child"), target_executor_id=EXECUTOR_ID)
        assert excinfo.value.code == "UNBOUND_ORIGIN_SESSION"

    def test_blocked_dispatch_creates_no_durable_child_record(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=PLACEHOLDER, run_id="unbound-empty")
        exec_path = root / ".aota" / "execution.json"
        store = FileBackedExecutionStateStore(exec_path)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=PLACEHOLDER,
        )
        with pytest.raises(UnboundOriginSessionError):
            dispatcher.dispatch(make_package("unbound-child-2"), target_executor_id=EXECUTOR_ID)
        assert store.list_all() == []
        reopened = FileBackedExecutionStateStore(exec_path)
        assert reopened.list_all() == []

    def test_real_bound_session_permits_dispatch_and_stores_exact_origin(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="bound")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        host = FakeHermesHostClient()
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=host,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=REAL_SESSION,
        )
        package = make_package("bound-child")
        result = dispatcher.dispatch(package, target_executor_id=EXECUTOR_ID)
        assert result.canonical_task_id == package.canonical_task_id
        record = store.get(package.canonical_task_id)
        assert record is not None
        assert record.origin_session_ref is not None
        assert record.origin_session_ref.value == REAL_SESSION
        assert not is_placeholder_origin_session_ref(record.origin_session_ref)
        assert is_bound_origin_session_ref(record.origin_session_ref)

    def test_task_start_path_returns_typed_fail_closed(self, tmp_path: Path, monkeypatch) -> None:
        root, cfg = _make_worktree(tmp_path, origin=PLACEHOLDER, run_id="facade")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=PLACEHOLDER,
        )
        import aota_forge.work_plane.task_facade as facade
        from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff

        monkeypatch.setattr(
            facade,
            "_handoff_store_handoff_open",
            lambda ref, sandbox, view="full": {
                "mode": "work_item",
                "envelope": {"artifact_id": "artifact-1", "milestone_id": "M1", "work_item_id": "W1"},
                "semantic": {},
                "digest": "d" * 64,
            },
        )
        monkeypatch.setattr(
            facade,
            "_load_work_item_task_handoff",
            lambda semantic, sandbox=None, envelope=None: TaskHandoff(
                work_role="coder",
                task_kind="w6-facade",
                objective="bounded w6 facade probe",
                bounded_scope="bounded scope",
                validation_expectations=("v",),
                semantic_stop_expectations=("s",),
                work_item_ref=SemanticReference(ref="W1"),
                milestone_ref=SemanticReference(ref="M1"),
            ),
        )
        sandbox = SimpleNamespace(project_id=PROJECT_ID)
        with pytest.raises(UnboundOriginSessionError):
            facade.task_start(
                role="coder",
                handoff_ref="durable-work-item-ref",
                caller_role="task-main",
                sandbox=sandbox,
                dispatcher=dispatcher,
            )
        assert store.list_all() == []

    def test_production_bootstrap_placeholder_refuses_dispatch_and_activation(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=PLACEHOLDER, run_id="binding")
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        binding = try_build_task_main_binding()
        assert binding is not None
        control = binding.trusted_task_main_context.control_service
        assert control._dispatcher.origin_session_is_placeholder is True
        with pytest.raises(UnboundOriginSessionError):
            control._dispatcher.dispatch(make_package("bootstrap-child"), target_executor_id=EXECUTOR_ID)
        with pytest.raises(UnboundOriginSessionError):
            control.activate_milestone(
                profile="task-main",
                plan_view=_live_view(),
                origin_task_main_session_ref=PLACEHOLDER,
                executor_id=EXECUTOR_ID,
                project_id=PROJECT_ID,
            )
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        assert store.list_all() == []


# ---------------------------------------------------------------------------
# 5/6 — durable origin immutability; model/Worker cannot supply or override it
# ---------------------------------------------------------------------------


class TestDurableOriginImmutability:
    def test_durable_origin_remains_immutable(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="immutable")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=REAL_SESSION,
        )
        package = make_package("immutable-child")
        dispatcher.dispatch(package, target_executor_id=EXECUTOR_ID)
        record = store.get(package.canonical_task_id)
        assert record is not None
        with pytest.raises(ExecutionPersistenceFailureError):
            store.compare_and_swap(
                package.canonical_task_id,
                record.record_revision,
                {"origin_session_ref": "attacker-session"},
            )
        with pytest.raises(DispatcherError):
            dispatcher.bind_origin_session_ref(package.canonical_task_id, "attacker-session")
        after = store.get(package.canonical_task_id)
        assert after.origin_session_ref.value == REAL_SESSION

    def test_placeholder_cannot_be_bound_as_origin(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="noplaceholder")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        record = DurableExecutionRecord(
            canonical_task_id="unbound-origin-record",
            executor_id=EXECUTOR_ID,
            package_id="pkg",
            correlation_id="corr",
            dispatch_attempt_id="attempt",
            idempotency_key="idem-unbound",
            intent_fingerprint="f" * 32,
            execution_phase=ExecutionPhase.PREPARED,
            canonical_task_state=CanonicalTaskState.CREATED,
            admission_scope=TEST_SCOPE,
        )
        store.create(record)
        fresh = store.get("unbound-origin-record")
        with pytest.raises(ExecutionPersistenceFailureError):
            store.compare_and_swap(
                "unbound-origin-record",
                fresh.record_revision,
                {"origin_session_ref": PLACEHOLDER},
            )

    def test_model_and_worker_cannot_supply_origin(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="nosupply")
        store = FileBackedExecutionStateStore(root / ".aota" / "execution.json")
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=REAL_SESSION,
        )
        package = make_package("forged-origin")
        # The ExecutionPackage has no origin/session surface at all, and even a
        # forged semantic payload cannot change the durable trusted origin.
        assert "origin_session_ref" not in package.to_dict()
        assert "session_id" not in package.to_dict()
        dispatcher.dispatch(package, target_executor_id=EXECUTOR_ID)
        record = store.get(package.canonical_task_id)
        assert record.origin_session_ref.value == REAL_SESSION


# ---------------------------------------------------------------------------
# 7 — two-phase session binding: S before == S after; no replacement session
# ---------------------------------------------------------------------------


class TestTwoPhaseSessionBinding:
    def test_phase1_bootstrap_is_placeholder_and_phase2_binds_same_session(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "wt_two_phase"
        root.mkdir(parents=True, exist_ok=True)
        cfg = tmp_path / "runtime_two_phase.json"
        cfg.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
        adapter = StaticPlanAuthorityAdapter(
            body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af49w6"
        )
        launcher = DailyTaskMainLauncher(plan_adapter=adapter, hermes_bin="/bin/false")

        phase1_origins: list[str] = []
        subprocess_calls: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=True, timeout=None, env=None, **kwargs):
            subprocess_calls.append(list(cmd))
            bootstrap_path = Path(env["AOTA_TASK_MAIN_BOOTSTRAP"])
            phase1_origins.append(
                json.loads(bootstrap_path.read_text(encoding="utf-8"))["origin_task_main_session_ref"]
            )
            # While PHASE 1 is live (placeholder origin), the trusted production
            # task-main binding must refuse child dispatch; no durable record.
            old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
            os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
            try:
                phase1_binding = try_build_task_main_binding()
            finally:
                if old_root is None:
                    os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
                else:
                    os.environ[BOOTSTRAP_ENV_ROOT] = old_root
            assert phase1_binding is not None
            phase1_dispatcher = phase1_binding.trusted_task_main_context.control_service._dispatcher
            assert phase1_dispatcher.origin_session_is_placeholder is True
            with pytest.raises(UnboundOriginSessionError):
                phase1_dispatcher.dispatch(make_package("phase1-rejected"), target_executor_id=EXECUTOR_ID)
            assert FileBackedExecutionStateStore(
                root / ".aota" / "execution.json"
            ).list_all() == []
            usage_path = Path(cmd[cmd.index("--usage-file") + 1])
            usage_path.write_text(
                json.dumps({"session_id": REAL_SESSION, "completed": True}), encoding="utf-8"
            )
            import subprocess as _subprocess

            return _subprocess.CompletedProcess(cmd, 0, stdout="session ready", stderr="")

        import aota_forge.composition.task_main_daily_launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.subprocess, "run", fake_run)

        reentry_calls: list[dict] = []
        bootstrap_path = root / BOOTSTRAP_RELPATH

        class RecordingReentry:
            def reenter(self, session_id, payload):
                reentry_calls.append(
                    {
                        "session_id": session_id,
                        "payload": payload,
                        "bound_origin": json.loads(
                            bootstrap_path.read_text(encoding="utf-8")
                        )["origin_task_main_session_ref"],
                    }
                )
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt="continued",
                    stderr_excerpt=None,
                )

        monkeypatch.setattr(
            launcher,
            "_build_exact_session_reentry",
            lambda *, ctx, timeout_seconds: RecordingReentry(),
        )

        ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w6",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W6 PHASE2 OPERATOR STARTUP KICKOFF",
            timeout_seconds=30,
        )

        # exact same real session identity before/after continuation
        assert session_id == REAL_SESSION
        assert TWO_PHASE_SESSION_BINDING is True
        assert len(subprocess_calls) == 1, "phase 1 must be the only session creation call"
        assert PHASE1_SESSION_BOOTSTRAP_PROMPT in subprocess_calls[0]
        assert "W6 PHASE2 OPERATOR STARTUP KICKOFF" not in subprocess_calls[0]
        assert is_placeholder_origin_session_ref(phase1_origins[0])
        assert len(reentry_calls) == 1
        assert reentry_calls[0]["session_id"] == REAL_SESSION
        assert reentry_calls[0]["payload"] == "W6 PHASE2 OPERATOR STARTUP KICKOFF"
        assert reentry_calls[0]["bound_origin"] == REAL_SESSION
        assert json.loads(bootstrap_path.read_text(encoding="utf-8"))[
            "origin_task_main_session_ref"
        ] == REAL_SESSION
        assert ctx.execution_store_path.name == "execution.json"

        # After PHASE 2 the trusted production binding reports the REAL bound
        # origin (fresh build from the post-launch bootstrap; the same path the
        # resumed session's MCP child uses). No placeholder remains anywhere.
        old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        try:
            phase2_binding = try_build_task_main_binding()
        finally:
            if old_root is None:
                os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
            else:
                os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        assert phase2_binding is not None
        phase2_dispatcher = phase2_binding.trusted_task_main_context.control_service._dispatcher
        assert phase2_dispatcher.origin_session_is_placeholder is False
        assert phase2_dispatcher.origin_session_ref.value == REAL_SESSION
        assert (
            phase2_binding.trusted_task_main_context.origin_task_main_session_ref == REAL_SESSION
        )


# ---------------------------------------------------------------------------
# 8-12 — runtime-owned autonomous completion continuation
# ---------------------------------------------------------------------------


class TestRuntimeOwnedCompletionContinuation:
    def test_idle_parent_terminal_receipt_reconciles_without_advance_once(self, tmp_path: Path, monkeypatch) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="autonomous")
        exec_path = root / ".aota" / "execution.json"

        # Parent turn exists and has ended; a child was dispatched and the
        # supervisor receipt arrives LATER (running -> running -> done).
        host_a = FakeHermesHostClient(terminal_after=999)
        store_a = FileBackedExecutionStateStore(exec_path)
        dispatcher_a = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=host_a,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store_a,
            origin_session_ref=REAL_SESSION,
        )
        package = make_package("autonomous-child")
        dispatch_result = dispatcher_a.dispatch(package, target_executor_id=EXECUTOR_ID)
        del dispatcher_a, store_a

        import aota_forge.runtime.task_main.runner as runner_mod

        def _forbidden_advance(*args, **kwargs):
            raise AssertionError("advance_milestone_once must never be required for completion")

        monkeypatch.setattr(runner_mod, "advance_milestone_once", _forbidden_advance)

        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=10,
            host_client=FakeHermesHostClient(
                terminal_after=1,
                handle_task_map={dispatch_result.adapter_handle: package.canonical_task_id},
            ),
            transport=transport,
        )

        assert report.stop_reason == COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        assert report.iterations >= 1
        assert "recover_once" not in json.dumps(report.to_dict())

        reopened = FileBackedExecutionStateStore(exec_path)
        record = reopened.get(package.canonical_task_id)
        assert record.canonical_task_state.is_terminal
        assert record.terminal_result is not None
        assert record.worker_result_card is not None
        assert record.worker_result_card_digest == card_digest_for(dict(record.worker_result_card))
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED
        assert record.origin_session_ref.value == REAL_SESSION

        # delivery targeted the durable real origin, exactly.
        assert len(transport.attempts) == 1
        assert transport.attempts[0]["session_ref"] == REAL_SESSION
        assert "AOTA_WORKER_COMPLETION_V1" in transport.attempts[0]["envelope"]

    def test_initial_worker_receipt_appearing_later_is_observed(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="later")
        exec_path = root / ".aota" / "execution.json"
        store = FileBackedExecutionStateStore(exec_path)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(terminal_after=999),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
            origin_session_ref=REAL_SESSION,
        )
        package = make_package("later-child")
        dispatch_result = dispatcher.dispatch(package, target_executor_id=EXECUTOR_ID)

        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=10,
            host_client=FakeHermesHostClient(
                terminal_after=3,
                handle_task_map={dispatch_result.adapter_handle: package.canonical_task_id},
            ),
            transport=transport,
        )
        assert report.iterations >= 2
        assert report.stop_reason == COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        record = FileBackedExecutionStateStore(exec_path).get(package.canonical_task_id)
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED

    def test_fresh_process_reconstruction_continues_delivery(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="fresh")
        exec_path = root / ".aota" / "execution.json"
        _seed_terminal_record(exec_path, "fresh-terminal", origin=REAL_SESSION)
        # No original dispatcher/coordinator/transport objects exist here.
        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        assert report.stop_reason == COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        record = FileBackedExecutionStateStore(exec_path).get("fresh-terminal")
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED
        assert transport.attempts[0]["session_ref"] == REAL_SESSION
        assert record.worker_result_card_digest == card_digest_for(dict(record.worker_result_card))

    def test_duplicate_terminal_receipt_is_idempotent(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="dupe")
        exec_path = root / ".aota" / "execution.json"
        _seed_terminal_record(exec_path, "dupe-terminal", origin=REAL_SESSION)
        transport = RecordingTransport(mode="ack")
        first = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        second = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        assert first.iterations >= 1
        assert second.iterations == 0
        assert second.stop_reason == COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        assert len(transport.attempts) == 1
        record = FileBackedExecutionStateStore(exec_path).get("dupe-terminal")
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED

    def test_duplicate_delivery_attempt_does_not_duplicate_authority(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="dupedeliver")
        exec_path = root / ".aota" / "execution.json"
        _seed_terminal_record(exec_path, "dupe-delivery", origin=REAL_SESSION)
        transport = RecordingTransport(mode="ack")
        run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        # Direct negative probe: an explicit second delivery pass over the
        # shared stores must not duplicate the accepted ACK authority.
        store = FileBackedExecutionStateStore(exec_path)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
        )

        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            transport=transport,
        )
        report = coordinator.deliver_pending_once()
        assert report.outcomes == {}
        assert len(transport.attempts) == 1
        assert store.get("dupe-delivery").delivery_state == DeliveryState.ACKNOWLEDGED

    def test_wrong_and_missing_ack_stays_unacknowledged_then_acknowledges(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="ack")
        exec_path = root / ".aota" / "execution.json"
        _seed_terminal_record(exec_path, "ack-terminal", origin=REAL_SESSION)

        wrong = RecordingTransport(mode="wrong_ack")
        first = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=1,
            host_client=FakeHermesHostClient(),
            transport=wrong,
        )
        assert first.delivery_outcomes == (DELIVER_RELEASED_ACK_NOT_PROVEN,)
        record = FileBackedExecutionStateStore(exec_path).get("ack-terminal")
        assert record.delivery_state == DeliveryState.PENDING
        assert record.terminal_result is not None

        missing = RecordingTransport(mode="no_ack")
        second = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            max_iterations=1,
            host_client=FakeHermesHostClient(),
            transport=missing,
        )
        assert second.delivery_outcomes == (DELIVER_RELEASED_ACK_NOT_PROVEN,)
        assert FileBackedExecutionStateStore(exec_path).get("ack-terminal").delivery_state == DeliveryState.PENDING

        good = RecordingTransport(mode="ack")
        third = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=good,
        )
        assert third.delivery_outcomes == (DELIVER_ACKNOWLEDGED,)
        assert FileBackedExecutionStateStore(exec_path).get("ack-terminal").delivery_state == DeliveryState.ACKNOWLEDGED

    def test_no_relevant_execution_is_a_bounded_noop(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="noop")
        exec_path = root / ".aota" / "execution.json"
        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        assert report.iterations == 0
        assert report.stop_reason == COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        assert transport.attempts == []

    def test_foreign_session_execution_is_not_relevant(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="foreign")
        exec_path = root / ".aota" / "execution.json"
        _seed_terminal_record(exec_path, "foreign-terminal", origin="other-parent-session")
        transport = RecordingTransport(mode="ack")
        report = run_bounded_completion_continuation(
            execution_store_path=exec_path,
            worktree_root=root,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            relevant_origin_session_ref=REAL_SESSION,
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            host_client=FakeHermesHostClient(),
            transport=transport,
        )
        assert report.iterations == 0
        assert transport.attempts == []
        assert FileBackedExecutionStateStore(exec_path).get("foreign-terminal").delivery_state == DeliveryState.PENDING


# ---------------------------------------------------------------------------
# 15/16 — negative fail-closed matrix
# ---------------------------------------------------------------------------


class TestNegativeFailClosedMatrix:
    def test_placeholder_continuation_target_fails_closed(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=PLACEHOLDER, run_id="negplaceholder")
        exec_path = root / ".aota" / "execution.json"
        with pytest.raises(UnboundOriginSessionError):
            run_bounded_completion_continuation(
                execution_store_path=exec_path,
                worktree_root=root,
                runtime_config=load_runtime_config(config_path=str(cfg)),
                relevant_origin_session_ref=PLACEHOLDER,
                host_client=FakeHermesHostClient(),
                transport=RecordingTransport(mode="ack"),
            )

    def test_empty_origin_bootstrap_fails_closed(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="emptyorigin")
        write_bootstrap_file(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w6",
            coordinator_store_path=root / ".aota" / "coordinator.json",
            execution_store_path=root / ".aota" / "execution.json",
            runtime_config_path=cfg,
            origin_task_main_session_ref="",
            live_plan_view=_live_view(),
            next_milestone_view=None,
        )
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError

        with pytest.raises(TrustedBindingError):
            try_build_task_main_binding()

    def test_missing_origin_delivery_fails_closed_with_truth_retained(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="negmissing")
        exec_path = root / ".aota" / "execution.json"
        store = _seed_terminal_record(exec_path, "missing-origin", origin=None)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
        )

        transport = RecordingTransport(mode="ack")
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            transport=transport,
        )
        report = coordinator.deliver_pending_once()
        assert report.outcomes["missing-origin"] == DELIVER_DROPPED_ORIGIN_MISSING
        record = store.get("missing-origin")
        assert record.terminal_result is not None
        assert record.worker_result_card is not None
        assert transport.attempts == []

    def test_placeholder_origin_delivery_fails_closed(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="negpending")
        exec_path = root / ".aota" / "execution.json"
        store = _seed_terminal_record(exec_path, "pending-origin", origin=PLACEHOLDER)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
        )

        transport = RecordingTransport(mode="ack")
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            transport=transport,
        )
        report = coordinator.deliver_pending_once()
        assert report.outcomes["pending-origin"] == DELIVER_DROPPED_UNBOUND_ORIGIN
        assert transport.attempts == []
        assert store.get("pending-origin").terminal_result is not None

    def test_session_not_found_does_not_fabricate_replacement(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="negsession")
        exec_path = root / ".aota" / "execution.json"
        store = _seed_terminal_record(exec_path, "session-missing", origin=REAL_SESSION)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
        )

        transport = RecordingTransport(mode="not_found")
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            transport=transport,
        )
        report = coordinator.deliver_pending_once()
        assert report.outcomes["session-missing"] == DELIVER_DROPPED_SESSION_MISSING
        record = store.get("session-missing")
        assert record.delivery_state == DeliveryState.DROPPED
        assert record.terminal_result is not None
        assert record.worker_result_card is not None
        assert len(transport.attempts) == 1

    def test_transport_exception_keeps_truth_pending(self, tmp_path: Path) -> None:
        root, cfg = _make_worktree(tmp_path, origin=REAL_SESSION, run_id="negtransport")
        exec_path = root / ".aota" / "execution.json"
        store = _seed_terminal_record(exec_path, "transport-error", origin=REAL_SESSION)
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=FakeHermesHostClient(),
            runtime_config=load_runtime_config(config_path=str(cfg)),
            state_store=store,
        )

        transport = RecordingTransport(mode="exception")
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=load_runtime_config(config_path=str(cfg)),
            transport=transport,
        )
        report = coordinator.deliver_pending_once()
        assert report.outcomes["transport-error"] == DELIVER_RELEASED_TRANSPORT_ERROR
        record = store.get("transport-error")
        assert record.delivery_state == DeliveryState.PENDING
        assert record.terminal_result is not None
        assert record.worker_result_card is not None


# ---------------------------------------------------------------------------
# 17 — no generic scheduler / event bus; owner exists outside advance_once
# ---------------------------------------------------------------------------


class TestRuntimeOwnershipShape:
    def test_owner_exists_outside_advance_once(self) -> None:
        assert PRODUCTION_COMPLETION_RUNTIME_OWNER.endswith("run_bounded_completion_continuation")
        assert MODEL_CALL_REQUIRED_FOR_COMPLETION_TRIGGER is False
        assert OPERATOR_POLLING_REQUIRED is False
        assert MANUAL_ADVANCE_ONCE_REQUIRED is False
        assert UNBOUND_ORIGIN_CAN_CREATE_CHILD_EXECUTION is False

    def test_no_generic_scheduler_or_event_bus(self) -> None:
        assert NEW_GENERIC_BACKGROUND_SCHEDULER_CREATED is False
        assert NEW_GENERIC_EVENT_BUS_CREATED is False
        assert COMPLETION_CONTINUATION_BOUNDED is True
        assert COMPLETION_CONTINUATION_SCANS_FOREVER is False

    def test_continuation_entrypoint_owns_the_sequence_and_is_bounded(self) -> None:
        source = inspect.getsource(run_bounded_completion_continuation)
        assert "recover_once()" in source
        assert "deliver_pending_once()" in source
        assert "max_iterations" in source
        assert "deadline" in source
        # Bounded lifecycle reasons exist; no scheduler/event-bus construction.
        assert COMPLETION_CONTINUATION_STOP_NO_RELEVANT
        assert COMPLETION_CONTINUATION_STOP_LIFECYCLE_TIMEOUT
        assert COMPLETION_CONTINUATION_STOP_ITERATION_BUDGET
