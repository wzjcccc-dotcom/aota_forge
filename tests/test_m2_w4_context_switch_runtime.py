"""Focused M2/W4 guarded switch, re-entry, and thin-host proofs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    InMemoryExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.task_main_context import (
    CONTEXT_MODE_INSPECT,
    ContextLocator,
    dispatch_task_main_context,
)
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_tool_operation
from aota_forge.governance.intake import (
    GovernanceIntakeContext,
    GovernanceIntakeService,
)
from aota_forge.mcp_transport import MCP_PUBLIC_TOOL_COUNT, MCP_PUBLIC_TOOLS
from aota_forge.runtime.task_main.context_switch_eligibility import (
    derive_switch_eligibility,
)
from aota_forge.runtime.task_main.context_switch_runtime import (
    ContextSwitchRuntime,
    ContextSwitchRuntimeError,
    TrustedContextSwitchTarget,
)
from aota_forge.runtime.task_main.context_switch_transition import (
    ContextSwitchIntent,
    prepare_context_switch,
)
from aota_forge.runtime.task_main.coordinator_state import (
    ContextSwitchTransitionPhase,
    CoordinatorStatus,
    TaskMainCoordinatorState,
)
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
)
from aota_forge.work_plane.authorized_roots import (
    CAPABILITY_READ,
    CAPABILITY_SEARCH,
    CAPABILITY_WRITE,
    ROOT_REF_ACTIVE_WORKTREE,
    ROOT_REF_PROJECT_MAIN,
    ROOT_SET_SESSION_TASK_MAIN,
    AuthorizedRoot,
    AuthorizedRootSet,
)

PROJECT_ID = "w4_switch_project"
PLAN_A = "plan_002_w4_a"
PLAN_B = "plan_002_w4_b"
PLAN_C = "plan_002_w4_c"
AUTHORITY_A = f"local-governance:{PROJECT_ID}/{PLAN_A}"
AUTHORITY_B = f"local-governance:{PROJECT_ID}/{PLAN_B}"
AUTHORITY_C = f"local-governance:{PROJECT_ID}/{PLAN_C}"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
REVISION_A = "revision-a"
REVISION_B = "revision-b"
REVISION_C = "revision-c"
SESSION = "ses_m2_w4_exact"


def _roots(tmp_path: Path, project_id: str = PROJECT_ID) -> AuthorizedRootSet:
    return AuthorizedRootSet(
        session_kind=ROOT_SET_SESSION_TASK_MAIN,
        roots=(
            AuthorizedRoot(
                root_ref=ROOT_REF_PROJECT_MAIN,
                root_kind=ROOT_REF_PROJECT_MAIN,
                root_path=str(tmp_path / f"{project_id}-project"),
                capabilities=frozenset({CAPABILITY_READ, CAPABILITY_SEARCH}),
                project_id=project_id,
            ),
            AuthorizedRoot(
                root_ref=ROOT_REF_ACTIVE_WORKTREE,
                root_kind=ROOT_REF_ACTIVE_WORKTREE,
                root_path=str(tmp_path / f"{project_id}-worktree"),
                capabilities=frozenset(
                    {CAPABILITY_READ, CAPABILITY_SEARCH, CAPABILITY_WRITE}
                ),
                project_id=project_id,
                worktree_id="w4-worktree",
            ),
        ),
    )


def _plan_binding(plan_id: str, authority: str, digest: str, revision: str) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind="local_governance",
        authority_ref=authority,
        source_revision=revision,
        source_digest=digest,
    )


def _target(
    tmp_path: Path,
    plan_id: str,
    authority: str,
    digest: str,
    revision: str,
    *,
    project_id: str = PROJECT_ID,
    session: str = SESSION,
    roots: AuthorizedRootSet | None = None,
    fingerprint: str = "f" * 64,
) -> TrustedContextSwitchTarget:
    return TrustedContextSwitchTarget(
        project_id=project_id,
        project_fingerprint=fingerprint,
        plan_binding=_plan_binding(plan_id, authority, digest, revision),
        origin_task_main_session_ref=session,
        authorized_roots=roots or _roots(tmp_path, project_id),
    )


def _state(tmp_path: Path, *, target: TrustedContextSwitchTarget | None = None) -> TaskMainCoordinatorState:
    source = target or _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    binding = source.to_context_binding()
    return TaskMainCoordinatorState(
        coordinator_id=f"{PROJECT_ID}:M2",
        plan_authority=source.plan_binding.authority_ref,
        plan_digest=source.plan_binding.source_digest or "",
        plan_source_revision=str(source.plan_binding.source_revision),
        milestone_id="M2",
        entry_base="871ffa87f623c7510f5637fdb7f6911d99f46722",
        origin_task_main_session_ref=SESSION,
        project_id=PROJECT_ID,
        executor_id="opencode",
        work_items=("W1", "W2"),
        wi_status={"W1": "PENDING", "W2": "PENDING"},
        user_approval_satisfied=True,
        status=CoordinatorStatus.ACTIVE,
        context_binding=binding,
    )


def _runtime(
    tmp_path: Path,
    *,
    state: TaskMainCoordinatorState | None = None,
    targets: tuple[TrustedContextSwitchTarget, ...] = (),
) -> tuple[ContextSwitchRuntime, FileBackedTaskMainCoordinatorStore, InMemoryExecutionStateStore]:
    coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    current = state or _state(tmp_path)
    coordinator_store.create(current)
    execution_store = InMemoryExecutionStateStore()
    target_map = {
        target.plan_binding.plan_id: target
        for target in targets
    }
    target_map.update({target.plan_binding.authority_ref: target for target in targets})

    def resolve(locator: ContextLocator) -> TrustedContextSwitchTarget | None:
        return target_map.get(locator.plan_ref or "")

    runtime = ContextSwitchRuntime(
        coordinator_store=coordinator_store,
        execution_store=execution_store,
        coordinator_id=current.coordinator_id,
        target_resolver=resolve,
    )
    source = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    runtime.bind_source(
        project_id=PROJECT_ID,
        project_fingerprint=source.project_fingerprint,
        plan_binding=source.plan_binding,
        origin_task_main_session_ref=SESSION,
        authorized_roots=source.authorized_roots,
    )
    return runtime, coordinator_store, execution_store


def _invoke(runtime: ContextSwitchRuntime, arguments: dict[str, object]):
    return dispatch_task_main_context(
        arguments,
        SimpleNamespace(context_switch_runtime=runtime, plan_binding=None),
    )


def _execution_record(state: str, session: str = SESSION) -> DurableExecutionRecord:
    phase = (
        ExecutionPhase.PREPARED
        if state in {"CREATED", "UNKNOWN"}
        else ExecutionPhase.DISPATCHED
    )
    return DurableExecutionRecord(
        canonical_task_id="task-w4",
        executor_id="opencode",
        package_id="package-w4",
        correlation_id="correlation-w4",
        dispatch_attempt_id="attempt-w4",
        idempotency_key="idempotency-w4",
        intent_fingerprint="intent-w4",
        execution_phase=phase,
        canonical_task_state=state,
        adapter_handle=None if phase is ExecutionPhase.PREPARED else "adapter-w4",
        initial_state=None if phase is ExecutionPhase.PREPARED else "CREATED",
        dispatched_at=None if phase is ExecutionPhase.PREPARED else "now",
        origin_session_ref=OriginSessionRef(session),
    )


def test_guarded_eligible_switch_projects_target_and_keeps_single_model(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    runtime, store, _ = _runtime(tmp_path, targets=(target_a, target_b))

    switched = _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B})
    assert switched.ok is True, switched
    assert switched.payload["state"] == "switched"
    assert switched.payload["context"]["plan_id"] == PLAN_B
    assert switched.payload["status"]["exact_session_reentry"] is True
    assert store.get(f"{PROJECT_ID}:M2").context_switch_transition["phase"] == "APPLIED"

    inspected = _invoke(runtime, {"mode": CONTEXT_MODE_INSPECT})
    assert inspected.ok is True
    assert inspected.payload["context"]["plan_id"] == PLAN_B
    assert inspected.payload["context"]["authorized_roots"] == target_b.authorized_roots.public_roots()

    bound = _invoke(runtime, {"mode": "bind", "plan_ref": PLAN_B})
    assert bound.ok is True
    assert bound.payload["context"]["plan_id"] == PLAN_B


@pytest.mark.parametrize(
    ("state_change", "records", "code"),
    [
        (
            lambda state: replace(
                state,
                human_brake={
                    "state": "USER_GATE_REQUIRED",
                    "scope": "WHOLE_MILESTONE",
                    "affected_work": [],
                    "reason": "approval required",
                    "reported_at": "now",
                },
            ),
            (),
            "CONTEXT_SWITCH_ADMISSION_DENIED",
        ),
        (lambda state: state, ("RUNNING",), "CONTEXT_SWITCH_ADMISSION_DENIED"),
        (lambda state: state, ("UNKNOWN",), "CONTEXT_SWITCH_ADMISSION_DENIED"),
        (
            lambda state: replace(
                state,
                wi_status={"W1": "COMPLETION_PENDING_RECONCILIATION", "W2": "PENDING"},
            ),
            (),
            "CONTEXT_SWITCH_ADMISSION_DENIED",
        ),
    ],
)
def test_w3_denials_do_not_mutate_work_execution_or_context(
    tmp_path: Path, state_change, records, code: str
) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    state = state_change(_state(tmp_path, target=target_a))
    runtime, store, execution_store = _runtime(
        tmp_path, state=state, targets=(target_a, target_b)
    )
    for record_state in records:
        execution_store.create(_execution_record("UNKNOWN" if record_state == "UNKNOWN" else "CREATED"))
        if record_state == "RUNNING":
            execution_store.compare_and_swap(
                "task-w4",
                1,
                {
                    "execution_phase": ExecutionPhase.DISPATCHED,
                    "canonical_task_state": "RUNNING",
                    "adapter_handle": "adapter-w4",
                    "initial_state": "CREATED",
                    "dispatched_at": "now",
                },
            )
    before = store.get(state.coordinator_id).to_dict()
    execution_before = [record.to_dict() for record in execution_store.list_all()]

    response = _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B})
    assert response.ok is False
    assert response.error["code"] == code
    assert store.get(state.coordinator_id).to_dict() == before
    assert [record.to_dict() for record in execution_store.list_all()] == execution_before


def test_unauthorized_cross_project_and_stale_targets_fail_closed(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    foreign = _target(
        tmp_path,
        PLAN_C,
        "local-governance:foreign_project/plan_c",
        DIGEST_C,
        REVISION_C,
        project_id="foreign_project",
    )
    runtime, store, _ = _runtime(tmp_path, targets=(target_a, target_b, foreign))
    before = store.get(f"{PROJECT_ID}:M2").to_dict()

    unauthorized = _invoke(runtime, {"mode": "switch", "plan_ref": "plan_missing"})
    assert unauthorized.error["code"] == "UNAUTHORIZED_TARGET"
    assert store.get(f"{PROJECT_ID}:M2").to_dict() == before

    cross_project = _invoke(runtime, {"mode": "switch", "project_ref": "foreign_project", "plan_ref": PLAN_C})
    assert cross_project.error["code"] == "CROSS_PROJECT_SWITCH_DENIED"
    assert store.get(f"{PROJECT_ID}:M2").to_dict() == before


def test_current_context_rejects_live_plan_drift_and_untrusted_projection(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    drifted_state = replace(_state(tmp_path, target=target_a), plan_digest=DIGEST_C)
    runtime, store, _ = _runtime(tmp_path, state=drifted_state, targets=(target_a, target_b))
    before = store.get(f"{PROJECT_ID}:M2").to_dict()
    drift = _invoke(runtime, {"mode": "inspect"})
    assert drift.error["code"] == "STALE_CONTEXT"
    assert store.get(f"{PROJECT_ID}:M2").to_dict() == before

    tainted = replace(
        target_b,
        governance_context={"IS_AUTHORITY": True, "GOVERNANCE_CONTEXT_IS_AUTHORITY": True},
    )
    tainted_runtime, tainted_store, _ = _runtime(
        tmp_path / "tainted",
        targets=(target_a, tainted),
    )
    response = _invoke(tainted_runtime, {"mode": "switch", "plan_ref": PLAN_B})
    assert response.error["code"] == "AUTHORITY_DENIED"
    assert tainted_store.get(f"{PROJECT_ID}:M2").context_switch_transition is None


def test_same_context_is_deterministic_noop_and_failed_locator_does_nothing(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    runtime, store, _ = _runtime(tmp_path, targets=(target_a,))
    before = store.get(f"{PROJECT_ID}:M2").to_dict()

    no_op = _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_A})
    assert no_op.ok is True
    assert no_op.payload["state"] == "already-bound"
    assert store.get(f"{PROJECT_ID}:M2").to_dict() == before

    failed = _invoke(runtime, {"mode": "switch", "plan_ref": "not-authorized"})
    assert failed.ok is False
    assert store.get(f"{PROJECT_ID}:M2").to_dict() == before


def test_model_cannot_mint_target_authority_at_canonical_validation_boundary(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    runtime, store, _ = _runtime(tmp_path, targets=(target_a, target_b))
    response = dispatch_tool_operation(
        "task_main.context",
        {
            "mode": "switch",
            "plan_ref": PLAN_B,
            "target_project_root": "/forged",
            "target_plan_digest": DIGEST_B,
            "authorized_roots": ["forged"],
            "safe_to_switch": True,
            "expected_revision": 1,
        },
        CanonicalDispatchBinding(context_switch_runtime=runtime),
    )
    assert response.ok is False
    assert response.error["code"] in {"UNKNOWN_INPUT", "INPUT_SIZE_EXCEEDED"}
    assert store.get(f"{PROJECT_ID}:M2").context_switch_transition is None


def test_applied_reentry_rehydrates_exact_session_and_rejects_wrong_session(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    runtime, store, execution_store = _runtime(tmp_path, targets=(target_a, target_b))
    assert _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B}).ok
    applied_revision = store.get(f"{PROJECT_ID}:M2").coordinator_revision
    store.close()
    execution_store.close()

    reopened_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    reopened_execution = InMemoryExecutionStateStore()
    runtime_reopened = ContextSwitchRuntime(
        coordinator_store=reopened_store,
        execution_store=reopened_execution,
        coordinator_id=f"{PROJECT_ID}:M2",
        target_resolver=lambda locator: target_b
        if locator.plan_ref in {PLAN_B, AUTHORITY_B}
        else target_a
        if locator.plan_ref in {PLAN_A, AUTHORITY_A}
        else None,
    )
    runtime_reopened.bind_source(
        project_id=PROJECT_ID,
        project_fingerprint=target_a.project_fingerprint,
        plan_binding=target_a.plan_binding,
        origin_task_main_session_ref=SESSION,
        authorized_roots=target_a.authorized_roots,
    )
    rehydrated = runtime_reopened.rehydrate(SESSION)
    assert rehydrated.identity.plan_binding.plan_id == PLAN_B
    assert rehydrated.binding.origin_task_main_session_ref == SESSION
    assert reopened_store.get(f"{PROJECT_ID}:M2").coordinator_revision == applied_revision
    with pytest.raises(ContextSwitchRuntimeError) as exc_info:
        runtime_reopened.rehydrate("ses_wrong_session")
    assert exc_info.value.code == "SESSION_RECOVERY_REQUIRED"


def test_prepared_and_unknown_restart_never_fabricate_context(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    _runtime_obj, store, execution_store = _runtime(tmp_path, targets=(target_a, target_b))
    source = store.get(f"{PROJECT_ID}:M2")
    eligibility = derive_switch_eligibility(
        target_a.identity(), target_a.authorized_roots, source, ()
    )
    intent = ContextSwitchIntent.from_trusted_state(
        source,
        target_b.to_context_binding(),
        target_coordinator_id=source.coordinator_id,
        transition_id="prepared-w4",
        eligibility=eligibility,
    )
    prepared = prepare_context_switch(store, intent, eligibility=eligibility)
    store.close()
    execution_store.close()

    reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    runtime_reopened = ContextSwitchRuntime(
        coordinator_store=reopened,
        execution_store=InMemoryExecutionStateStore(),
        coordinator_id=source.coordinator_id,
        target_resolver=lambda locator: target_b,
    )
    runtime_reopened.bind_source(
        project_id=PROJECT_ID,
        project_fingerprint=target_a.project_fingerprint,
        plan_binding=target_a.plan_binding,
        origin_task_main_session_ref=SESSION,
        authorized_roots=target_a.authorized_roots,
    )
    with pytest.raises(ContextSwitchRuntimeError) as prepared_error:
        runtime_reopened.current_context()
    assert prepared_error.value.code == "CONTEXT_SWITCH_RECOVERY_REQUIRED"
    unknown = dict(prepared.transition)
    unknown["phase"] = ContextSwitchTransitionPhase.UNKNOWN.value
    current = reopened.get(source.coordinator_id)
    reopened.compare_and_swap(
        source.coordinator_id,
        current.coordinator_revision,
        {"context_switch_transition": unknown},
        expected_revision_token=current.revision_token,
    )
    with pytest.raises(ContextSwitchRuntimeError) as unknown_error:
        runtime_reopened.current_context()
    assert unknown_error.value.code == "CONTEXT_SWITCH_RECOVERY_REQUIRED"


def test_applied_replay_is_idempotent_conflicting_replay_is_denied(tmp_path: Path) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    target_c = _target(tmp_path, PLAN_C, AUTHORITY_C, DIGEST_C, REVISION_C)
    runtime, store, _ = _runtime(tmp_path, targets=(target_a, target_b, target_c))
    assert _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B}).ok
    applied_revision = store.get(f"{PROJECT_ID}:M2").coordinator_revision
    replay = _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B})
    assert replay.ok is True
    assert replay.payload["state"] == "already-bound"
    assert store.get(f"{PROJECT_ID}:M2").coordinator_revision == applied_revision
    conflicting = _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_C})
    assert conflicting.ok is False
    assert conflicting.error["code"] == "CONTEXT_SWITCH_CONFLICT"
    assert store.get(f"{PROJECT_ID}:M2").coordinator_revision == applied_revision


@pytest.mark.parametrize("drift_kind", ["authority", "roots"])
def test_applied_reentry_fails_closed_on_target_drift(tmp_path: Path, drift_kind: str) -> None:
    target_a = _target(tmp_path, PLAN_A, AUTHORITY_A, DIGEST_A, REVISION_A)
    target_b = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_B, REVISION_B)
    runtime, store, _ = _runtime(tmp_path, targets=(target_a, target_b))
    assert _invoke(runtime, {"mode": "switch", "plan_ref": PLAN_B}).ok
    if drift_kind == "authority":
        drifted = _target(tmp_path, PLAN_B, AUTHORITY_B, DIGEST_C, REVISION_B)
    else:
        drifted = _target(
            tmp_path,
            PLAN_B,
            AUTHORITY_B,
            DIGEST_B,
            REVISION_B,
            roots=_roots(tmp_path / "drift"),
        )
    store.close()
    reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    runtime_reopened = ContextSwitchRuntime(
        coordinator_store=reopened,
        execution_store=InMemoryExecutionStateStore(),
        coordinator_id=f"{PROJECT_ID}:M2",
        target_resolver=lambda locator: drifted,
    )
    runtime_reopened.bind_source(
        project_id=PROJECT_ID,
        project_fingerprint=target_a.project_fingerprint,
        plan_binding=target_a.plan_binding,
        origin_task_main_session_ref=SESSION,
        authorized_roots=target_a.authorized_roots,
    )
    with pytest.raises(ContextSwitchRuntimeError) as exc_info:
        runtime_reopened.current_context()
    assert exc_info.value.code in {"STALE_CONTEXT", "AUTHORIZED_ROOT_MISMATCH"}
    evidence = reopened.get(f"{PROJECT_ID}:M2").context_switch_transition
    assert evidence["target_binding"]["plan_binding"]["source_digest"] == DIGEST_B


def test_thin_host_inspect_switch_inspect_uses_one_aota_invoke_surface(tmp_path: Path) -> None:
    manifest = """schema_version: 1
project:
  id: aota_forge
  name: W4 component fixture
  kind: test
  status: active
summary: bounded W4 fixture
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph
plan:
  active_plan_id: null
constraints: []
"""
    plan_body = """# W4 component plan\n\n```text\nPLAN_STATUS=active\nCURRENT_MILESTONE=M2\nM2_STATUS=in_progress\nM2_USER_APPROVAL_SATISFIED=yes\n```\n"""
    workspace = tmp_path / "workspace"
    checkout = workspace / "checkout"
    (checkout / ".aota").mkdir(parents=True)
    (checkout / ".aota" / "project.yaml").write_text(manifest, encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps({"workspace": {"candidates": [str(workspace)]}}), encoding="utf-8"
    )
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    governance_store_path = tmp_path / "governance.sqlite3"
    intake = GovernanceIntakeService(
        GovernanceIntakeContext(
            workspace_id="workspace",
            governance_base=governance_base,
            registry_path=registry,
            workspace_root=workspace,
            project_root=checkout,
            governance_store_path=governance_store_path,
            journal_path=tmp_path / "governance-journal.json",
        ),
        principal="w4-test",
    )
    assert intake.project("init", project_id="aota_forge")["status"] == "VERIFIED"
    assert intake.plan("init", project_id="aota_forge", plan_id=PLAN_A, candidate_body=plan_body)["status"] == "VERIFIED"
    assert intake.plan("init", project_id="aota_forge", plan_id=PLAN_B, candidate_body=plan_body.replace("W4 component plan", "W4 target plan"))["status"] == "VERIFIED"
    runtime_config = tmp_path / "runtime.json"
    executable = tmp_path / "hermes-stub"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    runtime_config.write_text(
        json.dumps(
            {
                    "executor": "hermes",
                    "executable": str(executable),
                    "concurrency": 1,
                    "provider": "test-provider",
                    "model": "test-model",
                    "bindings": {
                        "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "task-main": {"profile": "aota-task-main"},
                    },
                }
            ),
        encoding="utf-8",
    )

    class FakeHost:
        def dispatch(self, payload):
            return {"adapter_handle": "fake", "status": "running", "dispatch_time": "now"}

    seed = compose_thin_task_main_host(
        worktree_root=worktree,
        project_id="aota_forge",
        worktree_id="w4-component",
        runtime_config_path=runtime_config,
        origin_task_main_session_ref=SESSION,
        host_client=FakeHost(),
        registry_path=registry,
        governance_base=governance_base,
        plan_id=PLAN_A,
        governance_store_path=governance_store_path,
    )
    source_binding = seed.plan_authority_binding
    fingerprint = seed.project_evidence.candidates[0].candidate_fingerprint
    source_target = TrustedContextSwitchTarget(
        project_id="aota_forge",
        project_fingerprint=fingerprint,
        plan_binding=source_binding,
        origin_task_main_session_ref=SESSION,
        authorized_roots=seed.authorized_roots,
    )
    coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "component-coordinator.json")
    coordinator_store.create(
        TaskMainCoordinatorState(
            coordinator_id="aota_forge:M2",
            plan_authority=source_binding.authority_ref,
            plan_digest=source_binding.source_digest,
            plan_source_revision=str(source_binding.source_revision),
            milestone_id="M2",
            entry_base="871ffa87f623c7510f5637fdb7f6911d99f46722",
            origin_task_main_session_ref=SESSION,
            project_id="aota_forge",
            executor_id="opencode",
            work_items=("W1",),
            wi_status={"W1": "PENDING"},
            user_approval_satisfied=True,
            context_binding=source_target.to_context_binding(),
        )
    )
    execution_store = InMemoryExecutionStateStore()
    runtime = ContextSwitchRuntime(
        coordinator_store=coordinator_store,
        execution_store=execution_store,
        coordinator_id="aota_forge:M2",
        target_resolver=None,
    )
    seed.governance_store.close()
    host = compose_thin_task_main_host(
        worktree_root=worktree,
        project_id="aota_forge",
        worktree_id="w4-component",
        runtime_config_path=runtime_config,
        origin_task_main_session_ref=SESSION,
        execution_store=execution_store,
        host_client=FakeHost(),
        registry_path=registry,
        governance_base=governance_base,
        plan_id=PLAN_A,
        governance_store_path=governance_store_path,
        context_switch_runtime=runtime,
    )
    try:
        before = host.invoke("task_main.context", {"mode": "inspect"})
        assert before["ok"] is True
        assert before["payload"]["context"]["plan_id"] == PLAN_A
        switched = host.invoke("task_main.context", {"mode": "switch", "plan_ref": PLAN_B})
        assert switched["ok"] is True, switched
        assert switched["payload"]["context"]["plan_id"] == PLAN_B
        after = host.invoke("task_main.context", {"mode": "inspect"})
        assert after["ok"] is True
        assert after["payload"]["context"]["plan_id"] == PLAN_B
        assert host.invoke("task_main.context", {"mode": "bind", "plan_ref": PLAN_B})["ok"]
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1
    finally:
        if host.governance_store is not None:
            host.governance_store.close()
        coordinator_store.close()
