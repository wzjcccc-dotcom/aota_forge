"""AF #49 M1/W3 — Durable completion & real parent re-entry (focused V1).

Repairs I40-B003: PROCESS_LOCAL_COMPLETION != PRODUCTION_PARENT_REENTRY.

Covers the frozen W3 contract:

* production task-main composition wires the existing Hermes completion
  delivery transport into the existing DurableCompletionCoordinator (no
  ``transport=None`` on the normal wakeup-capable task-main path);
* the trusted parent session identity comes from the durable execution
  record's runtime-side ``origin_session_ref`` — never from model/Worker input;
* Worker-side ``task.return`` is a terminal SEMANTIC return: trusted identity +
  result binding validation, no process-local completion authority, no fake
  fallback, and NO direct mutation of the parent's durable ExecutionStateStore;
* parent-side ``DurableCompletionCoordinator`` reconciliation of durable
  adapter/supervisor evidence owns terminal execution truth and produces the
  existing deterministic WorkerResultCard;
* the bounded production lifecycle pass (``advance_milestone_once``) itself
  reconciles terminal evidence and delivers pending completions (parent
  re-entry) without any manual ``recover_once`` / ``deliver_pending_once`` call;
* ACK safety, retry, transport-error retention, non-wakeup fail-closed, and
  restart-continuation semantics are preserved;
* no new execution engine / coordinator / state machine / event bus / scheduler
  is introduced.

Proof boundary (honest):
  PROVES=deterministic component integration over real durable stores and the
         real production composition/runner code paths with explicitly
         injected test seams for the executor/transport boundaries.
  DOES_NOT_PROVE=real Hermes session wakeup/re-entry, real Worker completion,
         model visibility, M1 V3, M1 operational acceptance.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

import aota_forge.composition.execution as composition_execution
import aota_forge.runtime.task_main.runner as runner_module
import aota_forge.work_plane.task_facade as task_facade
from aota_forge.adapters.hermes.delivery import HermesCompletionDeliveryTransport
from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    HermesExactSessionReentry,
    HermesReentryResult,
)
from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
)
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPhase,
    FileBackedExecutionStateStore,
    OriginSessionRef,
    card_digest_for,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    DELIVER_DROPPED_SESSION_MISSING,
    DELIVER_RELEASED_ACK_NOT_PROVEN,
    DELIVER_RELEASED_RETRYABLE,
    DELIVER_RELEASED_TRANSPORT_ERROR,
    DELIVER_TRUTH_NOT_DURABLE,
    DurableCompletionCoordinator,
)
from aota_forge.runtime.config import load_runtime_config
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import GovernedWorkItemEvidence
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.progression import (
    FocusedValidationEvidence,
    FocusedValidationVerdict,
    MilestoneWorkItemGraph,
)
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from m2_w3_support import (
    ORIGIN_SESSION,
    TEST_SCOPE,
    ControlledTransport,
    DurableWorldAdapter,
    build_coordinator,
    build_dispatcher,
    completed_without_ack,
    make_package,
    not_found_missing_session,
    open_store,
    retryable_busy,
)

PROJECT_ID = "aota_forge"
MILESTONE_ID = "M1"
PLAN_AUTHORITY = "wzjcccc-dotcom/aota-hermes-tools#49"
ENTRY_BASE = "b" * 40
TRUSTED_TASK_MAIN_SESSION = "20260912_af49_w3_trusted_origin"
DISPATCHED = "af49-w3-task"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class AckingTransport:
    """Deterministic delivery transport that reconciles the exact completion.

    Parses the envelope's canonical_task_id/card_digest and returns the bound
    AOTA_COMPLETION_ACK_V1 line (a test seam, not a wakeup claim).
    """

    def __init__(self) -> None:
        self.attempts: list[dict[str, str]] = []

    def deliver(self, *, session_ref: str, envelope: str):
        from aota_forge.runtime.completion import DeliveryAttemptEvidence, DeliveryTransportOutcome

        self.attempts.append({"session_ref": session_ref, "envelope": envelope})
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
                "reconciled completion\n"
                f"AOTA_COMPLETION_ACK_V1 canonical_task_id={task_id} card_digest={digest}"
            ),
        )


def _dispatch_terminal(store: FileBackedExecutionStateStore):
    """Dispatch through the durable seam and reconcile adapter evidence."""
    dispatcher = build_dispatcher(store, DurableWorldAdapter())
    coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
    dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
    coordinator.recover_once()
    return dispatcher, coordinator


def _sandbox(project_id: str = "projAF49W3", worktree_id: str = "wtAF49W3") -> WorktreeSandboxBoundary:
    import tempfile

    td = Path(tempfile.mkdtemp(prefix="af49-w3-"))
    return WorktreeSandboxBoundary(
        workspace_id="ws-w3",
        workspace_root=str(td),
        project_id=project_id,
        project_root=str(td),
        worktree_id=worktree_id,
        worktree_root=str(td),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _work_item_handoff(sandbox: WorktreeSandboxBoundary, work_item_id: str = "W1"):
    return handoff_write(
        mode="work_item",
        semantic={
            "objective": "AF49 W3 lifecycle probe",
            "bounded_scope": "focused w3 probe",
            "validation_expectations": ["focused"],
            "semantic_stop_expectations": ["stop"],
            "work_role": "coder",
            "task_kind": "impl",
        },
        caller_role="task-main",
        sandbox=sandbox,
    )


def _result_handoff(sandbox: WorktreeSandboxBoundary, task_id: str):
    return handoff_write(
        mode="result",
        semantic={"summary": "w3 result", "work_done": "done", "validation": "ok"},
        caller_role="coder",
        sandbox=sandbox,
        task_id=task_id,
    )


def _runtime_config_path(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "w3_runtime.json"
    cfg_path.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    return cfg_path


def _milestone_view(work_items: tuple[str, ...] = ("W1",)) -> MilestonePlanView:
    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE_ID, work_items=list(work_items), dependencies=[])
    return MilestonePlanView(
        plan_authority=PLAN_AUTHORITY,
        plan_digest="d" * 64,
        plan_source_revision="rev-w3",
        milestone_id=MILESTONE_ID,
        entry_base=ENTRY_BASE,
        graph=graph,
        milestone_user_approval_satisfied=True,
    )


def _runner_world(tmp_path: Path, transport):
    coord_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    exec_store = FileBackedExecutionStateStore(tmp_path / "execution.json")
    dispatcher = build_dispatcher(
        exec_store, DurableWorldAdapter(), origin_session_ref=TRUSTED_TASK_MAIN_SESSION
    )
    coordinator = build_coordinator(dispatcher, exec_store, transport, limits={TEST_SCOPE: 4})
    view = _milestone_view(("W1",))
    handle = activate_milestone(
        store=coord_store,
        plan_view=view,
        origin_task_main_session_ref=TRUSTED_TASK_MAIN_SESSION,
        execution_dispatcher=dispatcher,
        completion_coordinator=coordinator,
        executor_id="durable-fake",
        project_id=PROJECT_ID,
    )
    return coord_store, exec_store, dispatcher, coordinator, view, handle


def _seed_terminal_record(
    store: FileBackedExecutionStateStore,
    task_id: str,
    *,
    origin: str = ORIGIN_SESSION,
    forged_result_data: dict | None = None,
):
    """Seed a terminal DISPATCHED record through the durable store contract."""
    record = DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="durable-fake",
        package_id=f"{task_id}:pkg",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"attempt-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint="f" * 32,
        execution_phase=ExecutionPhase.PREPARED,
        canonical_task_state=CanonicalTaskState.CREATED,
        origin_session_ref=OriginSessionRef(value=origin),
        admission_scope=TEST_SCOPE,
    )
    store.create(record)
    record = store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": "seed-handle",
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-09-12T00:00:00+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING,
        },
    )
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="durable-fake",
        result_data=forged_result_data or {"proof": "w3-seed"},
        correlation_id=f"corr-{task_id}",
    )
    card = project_worker_result_card(
        result, ResultGovernanceProjection.success(), "coder", summary="w3 seeded terminal"
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
    return card


def _handoff_resolver(work_item_id: str) -> TaskHandoff:
    return TaskHandoff(
        work_role="coder",
        task_kind="w3-trigger-proof",
        objective=f"w3 trigger objective for {work_item_id}",
        bounded_scope=f"w3 trigger scope for {work_item_id}",
        validation_expectations=("cheap validation",),
        semantic_stop_expectations=("semantic stop",),
        work_item_ref=SemanticReference(ref=work_item_id),
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
    )


def _evidence_resolver(work_item_id: str) -> GovernedWorkItemEvidence:
    return GovernedWorkItemEvidence(
        validation_evidence=FocusedValidationEvidence(
            work_item_ref=work_item_id,
            verdict=FocusedValidationVerdict.PASS,
            validation_evidence_ref=SemanticReference(ref=f"val:{work_item_id}"),
        ),
        risk_envelope=MilestoneRiskEnvelope(
            milestone_ref=MILESTONE_ID,
            default_process_depth=ProcessDepth.STANDARD,
            minimum_process_depth=ProcessDepth.STANDARD,
        ),
    )


@pytest.fixture(autouse=True)
def _clean_world():
    DurableWorldAdapter.reset_world()
    yield
    DurableWorldAdapter.reset_world()
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# 1. Production composition wires an existing transport (AC-W3-5)
# ---------------------------------------------------------------------------


class TestProductionCompositionWiring:
    def _binding_for_view(self, tmp_path: Path, view: MilestonePlanView):
        root = tmp_path / "wt"
        root.mkdir(parents=True, exist_ok=True)
        (root / ".aota").mkdir(parents=True, exist_ok=True)
        (root / ".aota" / "project.yaml").write_text(
            "schema_version: 1\nproject:\n"
            f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
            "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
            "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
            "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
            "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
            "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
            "plan:\n  active_plan_id: null\nconstraints: []\n",
            encoding="utf-8",
        )
        coord_path = root / ".aota" / "coord.json"
        exec_path = root / ".aota" / "exec.json"
        coord_path.write_text("{}", encoding="utf-8")
        exec_path.write_text("{}", encoding="utf-8")
        cfg_path = _runtime_config_path(tmp_path)
        write_bootstrap_file(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-1",
            coordinator_store_path=coord_path,
            execution_store_path=exec_path,
            runtime_config_path=cfg_path,
            origin_task_main_session_ref=TRUSTED_TASK_MAIN_SESSION,
            live_plan_view=view,
            next_milestone_view=None,
        )
        old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        try:
            binding = try_build_task_main_binding()
        finally:
            if old_root is None:
                os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
            else:
                os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        assert binding is not None
        return binding, exec_path

    def test_host_bootstrap_coordinator_receives_hermes_transport(self, tmp_path: Path) -> None:
        binding, _exec_path = self._binding_for_view(tmp_path, _milestone_view())
        service = binding.trusted_task_main_context.control_service
        coordinator = service._completion
        assert isinstance(coordinator, DurableCompletionCoordinator)
        transport = getattr(coordinator, "_transport", None)
        assert transport is not None, "production task-main coordinator must not be transport=None"
        assert isinstance(transport, HermesCompletionDeliveryTransport)
        reentry = getattr(transport, "_reentry", None)
        assert isinstance(reentry, HermesExactSessionReentry)
        assert getattr(reentry, "_profile", None) == "aota-task-main"
        # The trusted origin identity is durable runtime state, not factory input.
        assert binding.trusted_task_main_context.origin_task_main_session_ref == TRUSTED_TASK_MAIN_SESSION

    def test_host_bootstrap_source_no_transport_none_normal_path(self) -> None:
        source = inspect.getsource(try_build_task_main_binding)
        assert "transport=None" not in source
        assert "create_hermes_completion_delivery_transport" in source

    def test_factory_builds_exact_session_transport_and_delivers(self, tmp_path: Path, monkeypatch) -> None:
        recorded: dict[str, object] = {}

        class RecordingReentry(HermesExactSessionReentry):  # type: ignore[misc]
            def __init__(self, *args, **kwargs):  # noqa: D107
                super().__init__(*args, **kwargs)
                recorded["profile"] = getattr(self, "_profile", None)
                recorded["bin"] = getattr(self, "_hermes_bin", None)

            def reenter(self, session_id, payload_text, *, timeout_seconds=None):  # noqa: D102
                recorded["session_id"] = session_id
                recorded["payload"] = payload_text
                digest = ""
                for line in payload_text.splitlines():
                    if line.startswith("card_digest="):
                        digest = line.split("=", 1)[1].strip()
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=None,
                    error_code=None,
                    error_message=None,
                    response_excerpt=(
                        f"AOTA_COMPLETION_ACK_V1 canonical_task_id={DISPATCHED} card_digest={digest}"
                    ),
                    stderr_excerpt=None,
                )

        monkeypatch.setattr(composition_execution, "HermesExactSessionReentry", RecordingReentry)
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        transport = create_hermes_completion_delivery_transport(runtime_config=cfg)
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )
        recorded["profile"] = getattr(transport._reentry, "_profile", None)
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        coordinator.recover_once()
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert recorded["session_id"] == ORIGIN_SESSION
        assert recorded["profile"] == "aota-task-main"
        assert recorded["bin"] == str(Path(cfg.executable))


# ---------------------------------------------------------------------------
# 2. Trusted parent session identity; no model/Worker override (AC-W3-6)
# ---------------------------------------------------------------------------


class TestParentSessionIdentity:
    def test_delivery_targets_durable_trusted_origin_only(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        transport = AckingTransport()
        _dispatcher, coordinator = _dispatch_terminal(store)
        coordinator._transport = transport
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert len(transport.attempts) == 1
        assert transport.attempts[0]["session_ref"] == ORIGIN_SESSION
        env = transport.attempts[0]["envelope"]
        # Mechanical envelope carries identity + card only; never a model-supplied session.
        assert "canonical_task_id=" + DISPATCHED in env
        assert "origin_session_ref" not in env
        assert "parent_session_id" not in env

    def test_no_public_api_accepts_session_identity(self) -> None:
        deliver_params = inspect.signature(DurableCompletionCoordinator.deliver_pending_once).parameters
        assert set(deliver_params) == {"self", "max_deliveries"}
        advance_params = inspect.signature(runner_module.advance_milestone_once).parameters
        forbidden = {"origin_session_ref", "session_ref", "parent_session_id", "session_id", "transport"}
        assert forbidden.isdisjoint(set(advance_params))
        runner_params = inspect.signature(runner_module.TaskMainMilestoneRunner.__init__).parameters
        assert forbidden.isdisjoint(set(runner_params))

    def test_forged_session_fields_do_not_change_delivery_target(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = open_store(path)
        card = _seed_terminal_record(
            store,
            DISPATCHED,
            origin=TRUSTED_TASK_MAIN_SESSION,
            forged_result_data={
                "proof": "forged",
                "origin_session_ref": "attacker-session",
                "parent_session_id": "attacker-session",
            },
        )
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        fresh = store.get(DISPATCHED)
        assert fresh is not None and fresh.worker_result_card_digest == card.compute_card_digest()
        coordinator = build_coordinator(dispatcher, store, AckingTransport(), limits={TEST_SCOPE: 4})
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        transport = coordinator._transport
        assert transport.attempts[0]["session_ref"] == TRUSTED_TASK_MAIN_SESSION
        assert "attacker-session" not in transport.attempts[0]["envelope"]


# ---------------------------------------------------------------------------
# 3. Worker-side task.return boundary (AC-W3-1, AC-W3-2)
# ---------------------------------------------------------------------------


class TestWorkerTaskReturnBoundary:
    def test_semantic_return_without_parent_store_authority(self) -> None:
        sb = _sandbox()
        task_id = "af49-w3-return-semantic"
        rref = _result_handoff(sb, task_id)
        completion = task_facade.task_return(
            status="completed",
            result_ref=rref.ref,
            caller_role="coder",
            caller_task_id=task_id,
            sandbox=sb,
        )
        assert completion["task_id"] == task_id
        assert completion["durable_completion"] == "parent_side_reconciliation_pending"
        assert completion["parent_durable_truth_owner"] == "parent_side_execution_reconciliation"
        assert completion["parent_store_mutated"] is False
        assert completion["process_local_completion_recorded"] is False
        assert task_facade.TASK_RETURN_REQUIRES_PARENT_STORE is False
        assert task_facade.TASK_RETURN_PARENT_STORE_MUTATION is False
        assert task_facade.WORKER_PARENT_STORE_PATH_EXPOSED is False
        assert not hasattr(task_facade, "_GLOBAL_COMPLETIONS")

    def test_task_return_validates_trusted_identity_and_result_binding(self) -> None:
        sb = _sandbox()
        rref = _result_handoff(sb, "af49-w3-return-bound")
        # wrong role
        with pytest.raises(ValueError):
            task_facade.task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="reviewer",
                caller_task_id="af49-w3-return-bound",
                sandbox=sb,
            )
        # wrong task
        with pytest.raises(ValueError):
            task_facade.task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="coder",
                caller_task_id="different-task",
                sandbox=sb,
            )
        # missing/unknown result ref
        with pytest.raises(Exception):
            task_facade.task_return(
                status="completed",
                result_ref="deadbeef" * 8,
                caller_role="coder",
                caller_task_id="af49-w3-return-bound",
                sandbox=sb,
            )
        # invalid status
        with pytest.raises(ValueError):
            task_facade.task_return(
                status="maybe",
                result_ref=rref.ref,
                caller_role="coder",
                caller_task_id="af49-w3-return-bound",
                sandbox=sb,
            )

    def test_task_return_never_mutates_parent_store(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "return-store.json")
        adapter = DurableWorldAdapter()
        dispatcher = build_dispatcher(store, adapter)
        dispatcher.dispatch(make_package("af49-w3-return-store"), target_executor_id="durable-fake")
        before = store.get("af49-w3-return-store")
        assert before is not None and not before.canonical_task_state.is_terminal
        sb = _sandbox()
        rref = _result_handoff(sb, "af49-w3-return-store")
        completion = task_facade.task_return(
            status="completed",
            result_ref=rref.ref,
            caller_role="coder",
            caller_task_id="af49-w3-return-store",
            sandbox=sb,
            dispatcher=dispatcher,
        )
        assert completion["parent_store_mutated"] is False
        after = store.get("af49-w3-return-store")
        assert after is not None
        assert after.terminal_result is None
        assert not after.canonical_task_state.is_terminal
        assert after.record_revision == before.record_revision

    def test_worker_child_env_has_no_parent_store_path(self) -> None:
        from aota_forge.composition.task_main_host_bootstrap import BOOTSTRAP_EXPLICIT_ENV
        from aota_forge.composition.worker_vertical_slice import build_worker_child_environment
        from aota_forge.work_plane.roles import AgentWorkRole

        sb = _sandbox()
        handoff = TaskHandoff(
            work_role=AgentWorkRole.CODER,
            task_kind="w3-env-probe",
            objective="env probe",
            bounded_scope="env probe",
            validation_expectations=("v",),
            semantic_stop_expectations=("s",),
        )
        child_env = build_worker_child_environment(
            root=sb.worktree_root,
            project_id=sb.project_id,
            worktree_id=sb.worktree_id,
            canonical_task_id="af49-w3-env-probe",
            handoff=handoff,
            trace_path=None,
            repo_root=Path(__file__).resolve().parents[1],
            runtime_config_path=_runtime_config_path(Path(sb.worktree_root)),
        )
        joined = " ".join(str(value) for value in child_env.values())
        assert "execution.json" not in joined
        assert "coordinator.json" not in joined
        for key in child_env:
            assert "STORE" not in key.upper()
            assert BOOTSTRAP_EXPLICIT_ENV not in key
        assert "AOTA_TASK_MAIN_BOOTSTRAP" not in child_env


# ---------------------------------------------------------------------------
# 4. Parent-side durable reconciliation owns terminal truth (AC-W3-3)
# ---------------------------------------------------------------------------


class TestParentDurableReconciliation:
    def test_terminal_truth_and_card_survive_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = open_store(path)
        _dispatcher, coordinator = _dispatch_terminal(store)
        record = store.get(DISPATCHED)
        assert record is not None
        assert record.canonical_task_state.is_terminal
        assert record.terminal_result is not None
        assert record.worker_result_card is not None
        assert record.origin_session_ref is not None
        assert record.origin_session_ref.value == ORIGIN_SESSION
        store.close()

        reopened = open_store(path)
        fresh = reopened.get(DISPATCHED)
        assert fresh is not None
        assert fresh.canonical_task_state.is_terminal
        assert fresh.terminal_result is not None
        assert fresh.worker_result_card is not None
        assert fresh.worker_result_card_digest is not None
        assert card_digest_for(dict(fresh.worker_result_card)) == fresh.worker_result_card_digest

    def test_reconciliation_is_idempotent(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        digest_before = store.get(DISPATCHED).worker_result_card_digest
        coordinator.recover_once()
        assert store.get(DISPATCHED).worker_result_card_digest == digest_before


# ---------------------------------------------------------------------------
# 5. Deterministic Card (AC-W3-4)
# ---------------------------------------------------------------------------


class TestDeterministicCard:
    def test_parent_reconciliation_produces_existing_deterministic_card(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        record = store.get(DISPATCHED)
        assert record is not None and record.worker_result_card is not None
        card = WorkerResultCard.from_dict(dict(record.worker_result_card))
        assert card.task_ref == DISPATCHED
        assert card.compute_card_digest() == record.worker_result_card_digest
        assert card.result_handoff_ref.ref == DISPATCHED
        assert task_facade.WORKER_AUTHORS_RESULT_CARD is False
        assert task_facade.RESULT_CARD_DETERMINISTIC is True

    def test_identical_results_produce_identical_cards(self, tmp_path: Path) -> None:
        store_a = open_store(tmp_path / "a.json")
        store_b = open_store(tmp_path / "b.json")
        _d_a, c_a = _dispatch_terminal(store_a)
        _d_b, c_b = _dispatch_terminal(store_b)
        card_a = WorkerResultCard.from_dict(dict(store_a.get(DISPATCHED).worker_result_card))
        card_b = WorkerResultCard.from_dict(dict(store_b.get(DISPATCHED).worker_result_card))
        assert card_a.card_digest == card_b.card_digest


# ---------------------------------------------------------------------------
# 6. ACK / retry / transport safety (AC-W3-8) + non-wakeup (AC-W3-9)
# ---------------------------------------------------------------------------


class TestDeliverySafety:
    def test_valid_ack_is_acknowledged(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        transport = ControlledTransport(
            default=ControlledTransport.ack(DISPATCHED, digest)
        )
        coordinator._transport = transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert store.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED

    def test_missing_or_wrong_ack_never_acknowledged(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        coordinator._transport = ControlledTransport(default=completed_without_ack())
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        wrong = ControlledTransport(default=ControlledTransport.ack(DISPATCHED, "0" * 64))
        coordinator._transport = wrong
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        assert store.get(DISPATCHED).terminal_result is not None

    def test_retryable_delivery_remains_durably_pending(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        coordinator._transport = ControlledTransport(default=retryable_busy())
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_RETRYABLE
        record = store.get(DISPATCHED)
        assert record.delivery_state == DeliveryState.PENDING
        assert record.delivery_attempt == 1
        assert record.terminal_result is not None

    def test_transport_exception_retains_terminal_truth(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        coordinator._transport = ControlledTransport(raise_at={0})
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_TRANSPORT_ERROR
        record = store.get(DISPATCHED)
        assert record.terminal_result is not None and record.worker_result_card is not None
        assert record.delivery_state == DeliveryState.PENDING

    def test_non_wakeup_transport_does_not_fabricate_reentry(self, tmp_path: Path) -> None:
        from aota_forge.runtime.completion import DeliveryAttemptEvidence, DeliveryTransportOutcome

        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        coordinator._transport = ControlledTransport(
            default=DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.UNKNOWN,
                detail="NON_WAKEUP_TRANSPORT:supports_async_delivery=False",
            )
        )
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_RETRYABLE
        record = store.get(DISPATCHED)
        assert record.delivery_state == DeliveryState.PENDING
        assert record.delivery_state != DeliveryState.ACKNOWLEDGED
        assert record.terminal_result is not None and record.worker_result_card is not None

    def test_session_not_found_never_creates_new_session(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        transport = ControlledTransport(default=not_found_missing_session())
        coordinator._transport = transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_DROPPED_SESSION_MISSING
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.DROPPED
        assert final.terminal_result is not None
        assert len(transport.attempts) == 1

    def test_terminal_record_without_card_is_not_delivered(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(default=completed_without_ack()))
        # Force terminal result without the CARD (bypass coordinator CARD attach).
        record = store.get(DISPATCHED)
        result = CanonicalResult.success(
            canonical_task_id=DISPATCHED,
            executor_id="durable-fake",
            result_data={"proof": "no-card"},
            correlation_id="corr",
        )
        store.compare_and_swap(
            DISPATCHED,
            record.record_revision,
            {"terminal_result": result.to_dict(), "canonical_task_state": result.canonical_task_state},
        )
        transport = coordinator._transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_TRUTH_NOT_DURABLE
        assert transport.attempts == []


# ---------------------------------------------------------------------------
# 7. Restart continuation (AC-W3-10)
# ---------------------------------------------------------------------------


class TestRestartContinuation:
    def test_pending_completion_survives_fresh_objects(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = open_store(path)
        _dispatcher, coordinator = _dispatch_terminal(store)
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        store.close()

        # Fresh process/object reconstruction: no old Python object is authority.
        reopened = open_store(path)
        fresh_dispatcher = build_dispatcher(reopened, DurableWorldAdapter())
        fresh_coordinator = build_coordinator(fresh_dispatcher, reopened, AckingTransport(), limits={TEST_SCOPE: 4})
        report = fresh_coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert reopened.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED

    def test_duplicate_delivery_after_ack_is_not_repeated(self, tmp_path: Path) -> None:
        store = open_store(tmp_path / "state.json")
        _dispatcher, coordinator = _dispatch_terminal(store)
        transport = AckingTransport()
        coordinator._transport = transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert coordinator.deliver_pending_once().outcomes == {}
        assert len(transport.attempts) == 1


# ---------------------------------------------------------------------------
# 8. Production completion trigger (AC-W3-7)
# ---------------------------------------------------------------------------


class TestProductionCompletionTrigger:
    def test_bounded_progression_pass_reconciles_and_delivers(self, tmp_path: Path) -> None:
        transport = AckingTransport()
        coord_store, exec_store, dispatcher, coordinator, view, handle = _runner_world(tmp_path, transport)
        handle.dispatch_ready(_handoff_resolver, live_plan_view=view)
        task_id = handle.active_bindings()["W1"]["canonical_task_id"]
        record = exec_store.get(task_id)
        assert record is not None and not record.canonical_task_state.is_terminal

        # ONE production lifecycle call; the test never calls recover_once /
        # deliver_pending_once itself.
        outcome = runner_module.advance_milestone_once(
            coordinator_store=coord_store,
            execution_store=exec_store,
            execution_dispatcher=dispatcher,
            coordinator_id=handle.coordinator_id,
            live_plan_view=view,
            handoff_resolver=_handoff_resolver,
            governed_evidence_resolver=_evidence_resolver,
            completion_coordinator=coordinator,
        )
        assert outcome.disposition in runner_module.RUNNER_DISPOSITIONS
        final = exec_store.get(task_id)
        assert final is not None
        assert final.canonical_task_state.is_terminal
        assert final.terminal_result is not None
        assert final.worker_result_card is not None
        assert final.worker_result_card_digest is not None
        assert final.delivery_state == DeliveryState.ACKNOWLEDGED
        assert coordinator.recovered is True
        assert len(transport.attempts) == 1
        assert transport.attempts[0]["session_ref"] == TRUSTED_TASK_MAIN_SESSION

    def test_pass_is_bounded_no_background_loop_markers(self) -> None:
        assert runner_module.PRODUCTION_COMPLETION_DELIVERY_TRIGGER_IMPLEMENTED is True
        assert runner_module.MANUAL_HARNESS_COMPLETION_TRIGGER_REQUIRED is False
        assert runner_module.COMPLETION_TRIGGER_IS_BOUNDED_PASS is True
        assert runner_module.COMPLETION_TRIGGER_BACKGROUND_LOOP_CREATED is False
        source = inspect.getsource(runner_module._production_completion_pass)
        assert ".recover_once()" in source
        assert ".deliver_pending_once()" in source
        assert "while " not in source
        assert "sleep(" not in source

    def test_missing_transport_is_bounded_noop_not_fabrication(self, tmp_path: Path) -> None:
        transport = ControlledTransport()
        coord_store, exec_store, dispatcher, coordinator, view, handle = _runner_world(tmp_path, transport)
        handle.dispatch_ready(_handoff_resolver, live_plan_view=view)
        task_id = handle.active_bindings()["W1"]["canonical_task_id"]
        coordinator._transport = None
        runner_module.advance_milestone_once(
            coordinator_store=coord_store,
            execution_store=exec_store,
            execution_dispatcher=dispatcher,
            coordinator_id=handle.coordinator_id,
            live_plan_view=view,
            handoff_resolver=_handoff_resolver,
            governed_evidence_resolver=_evidence_resolver,
            completion_coordinator=coordinator,
        )
        record = exec_store.get(task_id)
        # Reconciliation of durable terminal truth still happened; delivery was
        # truthfully skipped (never fabricated) because no transport is wired.
        assert record is not None and record.canonical_task_state.is_terminal
        assert record.delivery_state == DeliveryState.PENDING


# ---------------------------------------------------------------------------
# 9. No new engine / coordinator / state machine (AC-W3, §35)
# ---------------------------------------------------------------------------


class TestNoNewFoundation:
    def test_no_second_coordinator_or_engine_introduced(self) -> None:
        import aota_forge.runtime.completion as completion_module

        coordinator_names = [
            name for name in dir(completion_module) if name.endswith("Coordinator")
        ]
        assert coordinator_names == ["DurableCompletionCoordinator"]
        assert completion_module.BACKGROUND_AUTONOMOUS_LOOP_IMPLEMENTED is False
        assert completion_module.WORKFLOW_ENGINE_CREATED is False
        assert completion_module.PERSISTENT_EVENT_BUS_CREATED is False
        assert completion_module.SCHEDULER_PLATFORM_CREATED is False
        assert runner_module.GENERIC_WORKFLOW_ENGINE_CREATED is False
        assert runner_module.GENERIC_EVENT_BUS_CREATED is False
        assert runner_module.GENERIC_QUEUE_CREATED is False

    def test_delivery_transport_is_the_existing_adapter(self, tmp_path: Path) -> None:
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        transport = create_hermes_completion_delivery_transport(runtime_config=cfg)
        assert isinstance(transport, HermesCompletionDeliveryTransport)
        assert isinstance(transport._reentry, HermesExactSessionReentry)
        assert transport.__class__ is HermesCompletionDeliveryTransport
        assert transport._reentry.__class__ is HermesExactSessionReentry
