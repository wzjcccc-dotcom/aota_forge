"""Focused M2/W1 proofs for pure context-switch eligibility derivation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    ExecutionStateStore,
    FileBackedExecutionStateStore,
    InMemoryExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.task_main_context import (
    TrustedProjectPlanIdentity,
    dispatch_task_main_context,
)
from aota_forge.runtime.task_main.context_binding import DurableTaskMainContextBinding
from aota_forge.runtime.task_main.context_switch_eligibility import (
    REASON_ACTIVE_EXECUTION,
    REASON_AMBIGUOUS_TRUSTED_CONTEXT,
    REASON_AUTHORIZED_ROOT_MISMATCH,
    REASON_CROSS_PROJECT_CONTRADICTION,
    REASON_HUMAN_BRAKE_ACTIVE,
    REASON_NON_TERMINAL_WORK,
    REASON_STALE_PLAN_AUTHORITY,
    REASON_UNKNOWN_OR_CORRUPT_STATE,
    REASON_USER_APPROVAL_REQUIRED,
    derive_switch_eligibility,
)
from aota_forge.runtime.task_main.coordinator_state import (
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

PROJECT_ID = "eligibility-project"
PLAN_ID = "plan_002_eligibility"
AUTHORITY_REF = "local-governance:eligibility-project/plan_002_eligibility"
PLAN_DIGEST = "a" * 64
PLAN_REVISION = "revision-10"
SESSION_REF = "ses_m2_w1_eligibility"


def _roots(tmp_path: Path) -> AuthorizedRootSet:
    project_root = str(tmp_path / "project")
    worktree_root = str(tmp_path / "worktree")
    return AuthorizedRootSet(
        session_kind=ROOT_SET_SESSION_TASK_MAIN,
        roots=(
            AuthorizedRoot(
                root_ref=ROOT_REF_PROJECT_MAIN,
                root_kind=ROOT_REF_PROJECT_MAIN,
                root_path=project_root,
                capabilities=frozenset({CAPABILITY_READ, CAPABILITY_SEARCH}),
                project_id=PROJECT_ID,
            ),
            AuthorizedRoot(
                root_ref=ROOT_REF_ACTIVE_WORKTREE,
                root_kind=ROOT_REF_ACTIVE_WORKTREE,
                root_path=worktree_root,
                capabilities=frozenset(
                    {CAPABILITY_READ, CAPABILITY_SEARCH, CAPABILITY_WRITE}
                ),
                project_id=PROJECT_ID,
                worktree_id="eligibility-worktree",
            ),
        ),
    )


def _state(
    tmp_path: Path, roots: AuthorizedRootSet | None = None
) -> TaskMainCoordinatorState:
    roots = roots or _roots(tmp_path)
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=AUTHORITY_REF,
        source_revision=PLAN_REVISION,
        source_digest=PLAN_DIGEST,
    )
    context = DurableTaskMainContextBinding(
        project_id=PROJECT_ID,
        project_fingerprint="b" * 64,
        plan_binding=binding,
        origin_task_main_session_ref=SESSION_REF,
        authorized_roots_digest=roots.digest(),
    )
    return TaskMainCoordinatorState(
        coordinator_id="eligibility-project:M2",
        plan_authority=AUTHORITY_REF,
        plan_digest=PLAN_DIGEST,
        plan_source_revision=PLAN_REVISION,
        milestone_id="M2",
        entry_base="871ffa87f623c75",
        origin_task_main_session_ref=SESSION_REF,
        project_id=PROJECT_ID,
        executor_id="opencode",
        work_items=("W1", "W2"),
        wi_status={"W1": "PENDING", "W2": "PENDING"},
        user_approval_satisfied=True,
        status=CoordinatorStatus.ACTIVE,
        context_binding=context,
    )


def _record(state: str, session_ref: str = SESSION_REF) -> DurableExecutionRecord:
    phase = (
        ExecutionPhase.PREPARED
        if state in {"CREATED", "UNKNOWN"}
        else ExecutionPhase.DISPATCHED
    )
    return DurableExecutionRecord(
        canonical_task_id="task-eligibility",
        executor_id="opencode",
        package_id="package-eligibility",
        correlation_id="correlation-eligibility",
        dispatch_attempt_id="attempt-eligibility",
        idempotency_key="idempotency-eligibility",
        intent_fingerprint="intent-eligibility",
        execution_phase=phase,
        canonical_task_state=state,
        adapter_handle=None
        if phase == ExecutionPhase.PREPARED
        else "adapter-eligibility",
        initial_state=None if phase == ExecutionPhase.PREPARED else "CREATED",
        dispatched_at=None if phase == ExecutionPhase.PREPARED else "now",
        origin_session_ref=OriginSessionRef(session_ref),
    )


def _evaluate(
    tmp_path: Path,
    state: TaskMainCoordinatorState | None = None,
    records=(),
    roots: AuthorizedRootSet | None = None,
):
    roots = roots or _roots(tmp_path)
    current = state or _state(tmp_path, roots)
    trusted_identity = TrustedProjectPlanIdentity(
        project_id=PROJECT_ID,
        plan_binding=current.context_binding.plan_binding,
    )
    return derive_switch_eligibility(trusted_identity, roots, current, records)


def test_clean_trusted_state_is_eligible(tmp_path: Path) -> None:
    result = _evaluate(tmp_path)
    assert result.eligible is True
    assert result.reason == "eligible"
    assert result.reason_codes == ()
    assert result.facts["trusted_state_checks_passed"] is True


def test_active_execution_blocks_switch(tmp_path: Path) -> None:
    result = _evaluate(tmp_path, records=(_record("RUNNING"),))
    assert result.eligible is False
    assert REASON_ACTIVE_EXECUTION in result.reason_codes


def test_non_terminal_work_blocks_switch(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state = replace(state, wi_status={"W1": "ACTIVE", "W2": "PENDING"})
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_NON_TERMINAL_WORK in result.reason_codes


def test_missing_user_approval_blocks_switch(tmp_path: Path) -> None:
    state = replace(_state(tmp_path), user_approval_satisfied=False)
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_USER_APPROVAL_REQUIRED in result.reason_codes


def test_human_brake_blocks_switch(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state = replace(
        state,
        human_brake={
            "state": "HUMAN_CHECKPOINT_REQUIRED",
            "scope": "WHOLE_MILESTONE",
            "affected_work": [],
            "reason": "operator checkpoint",
            "reported_at": "now",
        },
    )
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_HUMAN_BRAKE_ACTIVE in result.reason_codes


def test_stale_plan_binding_fails_closed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state = replace(state, plan_digest="c" * 64)
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_STALE_PLAN_AUTHORITY in result.reason_codes


def test_cross_project_contradiction_fails_closed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state = replace(state, project_id="another-project")
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_CROSS_PROJECT_CONTRADICTION in result.reason_codes


def test_authorized_root_mismatch_fails_closed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    context = replace(
        state.context_binding,
        authorized_roots_digest="d" * 64,
        binding_digest="",
    )
    state = replace(state, context_binding=context)
    result = _evaluate(tmp_path, state=state)
    assert result.eligible is False
    assert REASON_AUTHORIZED_ROOT_MISMATCH in result.reason_codes


def test_unknown_or_corrupt_state_fails_closed(tmp_path: Path) -> None:
    result = derive_switch_eligibility(
        object(),
        _roots(tmp_path),
        _state(tmp_path),
        (),
    )
    assert result.eligible is False
    assert REASON_UNKNOWN_OR_CORRUPT_STATE in result.reason_codes


def test_unknown_session_or_record_context_fails_closed(tmp_path: Path) -> None:
    result = _evaluate(tmp_path, records=(_record("COMPLETED", "other-session"),))
    assert result.eligible is False
    assert REASON_AMBIGUOUS_TRUSTED_CONTEXT in result.reason_codes


def test_foreign_authorized_root_fails_closed(tmp_path: Path) -> None:
    foreign_roots = _roots(tmp_path)
    foreign_roots = replace(
        foreign_roots,
        roots=tuple(
            replace(root, project_id="foreign-project") for root in foreign_roots.roots
        ),
    )
    result = _evaluate(
        tmp_path,
        records=(),
        state=_state(tmp_path, foreign_roots),
        roots=foreign_roots,
    )
    assert result.eligible is False
    assert REASON_CROSS_PROJECT_CONTRADICTION in result.reason_codes


def _completed_record(store: ExecutionStateStore) -> DurableExecutionRecord:
    prepared = store.create(_record("CREATED"))
    dispatched = store.compare_and_swap(
        prepared.canonical_task_id,
        prepared.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": "adapter-eligibility",
            "initial_state": "CREATED",
            "dispatched_at": "now",
            "canonical_task_state": "RUNNING",
        },
    )
    return store.compare_and_swap(
        dispatched.canonical_task_id,
        dispatched.record_revision,
        {
            "terminal_result": CanonicalResult.success(
                canonical_task_id=dispatched.canonical_task_id,
                executor_id="opencode",
                result_data={"ok": True},
                correlation_id=dispatched.correlation_id,
            ).to_dict(),
            "canonical_task_state": "COMPLETED",
        },
    )


def test_eligibility_does_not_mutate_durable_inputs(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = _state(tmp_path, roots)
    store = InMemoryExecutionStateStore()
    stored = _completed_record(store)
    before_state = state.to_dict()
    before_record = stored.to_dict()
    result = _evaluate(tmp_path, state=state, records=tuple(store.list_all()))
    assert result.eligible is True
    assert result.reason_codes == ()
    assert state.to_dict() == before_state
    assert store.get(stored.canonical_task_id).to_dict() == before_record


def test_file_backed_restart_readback_is_eligible(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    state = _state(tmp_path, roots)
    coordinator_store = FileBackedTaskMainCoordinatorStore(
        tmp_path / "coordinator.json"
    )
    execution_store = FileBackedExecutionStateStore(tmp_path / "execution.json")
    coordinator_store.create(state)
    _completed_record(execution_store)
    coordinator_store.close()
    execution_store.close()

    reopened_coordinator = FileBackedTaskMainCoordinatorStore(
        tmp_path / "coordinator.json"
    )
    reopened_execution = FileBackedExecutionStateStore(tmp_path / "execution.json")
    reloaded_state = reopened_coordinator.get(state.coordinator_id)
    assert reloaded_state is not None
    result = derive_switch_eligibility(
        TrustedProjectPlanIdentity(
            project_id=PROJECT_ID,
            plan_binding=reloaded_state.context_binding.plan_binding,
        ),
        roots,
        reloaded_state,
        tuple(reopened_execution.list_all()),
    )
    assert result.eligible is True
    assert result.reason_codes == ()
    reopened_coordinator.close()
    reopened_execution.close()


def test_task_main_switch_remains_unavailable() -> None:

    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=AUTHORITY_REF,
        source_revision=PLAN_REVISION,
        source_digest=PLAN_DIGEST,
    )
    trusted = SimpleNamespace(
        project_id=PROJECT_ID,
        plan_authority_binding=binding,
        sandbox=None,
    )
    response = dispatch_task_main_context(
        {"mode": "switch", "project_ref": PROJECT_ID, "plan_ref": AUTHORITY_REF},
        trusted,
    )
    assert response.ok is False
    assert response.error["code"] == "CONTEXT_SWITCH_UNAVAILABLE"
