"""Focused M2/W3 proofs for pure context-switch admission guards."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    OriginSessionRef,
)
from aota_forge.core.task_main_context import TrustedProjectPlanIdentity
from aota_forge.runtime.task_main.context_binding import DurableTaskMainContextBinding
from aota_forge.runtime.task_main.context_switch_eligibility import (
    REASON_ACTIVE_EXECUTION,
    REASON_AMBIGUOUS_TRUSTED_CONTEXT,
    REASON_CROSS_PROJECT_CONTRADICTION,
    REASON_HUMAN_BRAKE_ACTIVE,
    REASON_NON_TERMINAL_WORK,
    REASON_STALE_PLAN_AUTHORITY,
    REASON_USER_APPROVAL_REQUIRED,
    derive_switch_eligibility,
)
from aota_forge.runtime.task_main.context_switch_guard import (
    ContextSwitchAdmissionDeniedError,
    assert_context_switch_admitted,
    context_switch_admission_reasons,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
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

PROJECT_ID = "guard-project"
PLAN_ID = "plan_002_guard"
AUTHORITY_REF = "local-governance:guard-project/plan_002_guard"
PLAN_DIGEST = "a" * 64
PLAN_REVISION = "revision-10"
SESSION_REF = "ses_m2_w3_guard"


def _roots(tmp_path: Path) -> AuthorizedRootSet:
    return AuthorizedRootSet(
        session_kind=ROOT_SET_SESSION_TASK_MAIN,
        roots=(
            AuthorizedRoot(
                root_ref=ROOT_REF_PROJECT_MAIN,
                root_kind=ROOT_REF_PROJECT_MAIN,
                root_path=str(tmp_path / "project"),
                capabilities=frozenset({CAPABILITY_READ, CAPABILITY_SEARCH}),
                project_id=PROJECT_ID,
            ),
            AuthorizedRoot(
                root_ref=ROOT_REF_ACTIVE_WORKTREE,
                root_kind=ROOT_REF_ACTIVE_WORKTREE,
                root_path=str(tmp_path / "worktree"),
                capabilities=frozenset(
                    {CAPABILITY_READ, CAPABILITY_SEARCH, CAPABILITY_WRITE}
                ),
                project_id=PROJECT_ID,
                worktree_id="guard-worktree",
            ),
        ),
    )


def _state(tmp_path: Path) -> TaskMainCoordinatorState:
    roots = _roots(tmp_path)
    plan_binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=AUTHORITY_REF,
        source_revision=PLAN_REVISION,
        source_digest=PLAN_DIGEST,
    )
    context = DurableTaskMainContextBinding(
        project_id=PROJECT_ID,
        project_fingerprint="b" * 64,
        plan_binding=plan_binding,
        origin_task_main_session_ref=SESSION_REF,
        authorized_roots_digest=roots.digest(),
    )
    return TaskMainCoordinatorState(
        coordinator_id="guard-project:M2",
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
        canonical_task_id="task-guard",
        executor_id="opencode",
        package_id="package-guard",
        correlation_id="correlation-guard",
        dispatch_attempt_id="attempt-guard",
        idempotency_key="idempotency-guard",
        intent_fingerprint="intent-guard",
        execution_phase=phase,
        canonical_task_state=state,
        adapter_handle=None
        if phase == ExecutionPhase.PREPARED
        else "adapter-guard",
        initial_state=None if phase == ExecutionPhase.PREPARED else "CREATED",
        dispatched_at=None if phase == ExecutionPhase.PREPARED else "now",
        origin_session_ref=OriginSessionRef(session_ref),
    )


def _eligibility(
    tmp_path: Path,
    state: TaskMainCoordinatorState,
    records=(),
):
    identity = TrustedProjectPlanIdentity(
        project_id=PROJECT_ID,
        plan_binding=state.context_binding.plan_binding,
    )
    return derive_switch_eligibility(identity, _roots(tmp_path), state, records)


def test_clean_w1_result_is_admitted_without_mutation(tmp_path: Path) -> None:
    state = _state(tmp_path)
    records = ()
    eligibility = _eligibility(tmp_path, state, records)
    before = state.to_dict()

    assert context_switch_admission_reasons(eligibility, state, records) == ()
    assert_context_switch_admitted(eligibility, state, records)
    assert state.to_dict() == before


@pytest.mark.parametrize(
    ("state_change", "records", "expected_reason"),
    [
        (
            lambda state: replace(
                state,
                wi_status={
                    "W1": "COMPLETION_PENDING_RECONCILIATION",
                    "W2": "PENDING",
                },
            ),
            (),
            REASON_NON_TERMINAL_WORK,
        ),
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
            REASON_HUMAN_BRAKE_ACTIVE,
        ),
        (
            lambda state: replace(state, user_approval_satisfied=False),
            (),
            REASON_USER_APPROVAL_REQUIRED,
        ),
        (
            lambda state: state,
            (_record("RUNNING"),),
            REASON_ACTIVE_EXECUTION,
        ),
        (
            lambda state: state,
            (_record("UNKNOWN"),),
            REASON_ACTIVE_EXECUTION,
        ),
    ],
)
def test_existing_w1_blockers_remain_admission_denials(
    tmp_path: Path, state_change, records, expected_reason: str
) -> None:
    state = state_change(_state(tmp_path))
    eligibility = _eligibility(tmp_path, state, records)

    reasons = context_switch_admission_reasons(eligibility, state, records)
    assert expected_reason in reasons
    with pytest.raises(ContextSwitchAdmissionDeniedError) as exc_info:
        assert_context_switch_admitted(eligibility, state, records)
    assert expected_reason in exc_info.value.reason_codes


def test_stale_w1_snapshot_is_not_reused_after_state_changes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    eligibility = _eligibility(tmp_path, state)
    changed = replace(state, user_approval_satisfied=False)

    reasons = context_switch_admission_reasons(eligibility, changed, ())
    assert REASON_USER_APPROVAL_REQUIRED in reasons
    with pytest.raises(ContextSwitchAdmissionDeniedError):
        assert_context_switch_admitted(eligibility, changed, ())


def test_ambiguous_session_and_context_binding_are_denied(tmp_path: Path) -> None:
    state = _state(tmp_path)
    eligibility = _eligibility(tmp_path, state)
    foreign_record = _record("COMPLETED", session_ref="ses_foreign")

    reasons = context_switch_admission_reasons(eligibility, state, (foreign_record,))
    assert REASON_AMBIGUOUS_TRUSTED_CONTEXT in reasons

    no_binding = replace(state, context_binding=None)
    no_binding_eligibility = derive_switch_eligibility(
        TrustedProjectPlanIdentity(
            project_id=PROJECT_ID,
            plan_binding=state.context_binding.plan_binding,
        ),
        _roots(tmp_path),
        no_binding,
        (),
    )
    assert REASON_AMBIGUOUS_TRUSTED_CONTEXT in context_switch_admission_reasons(
        no_binding_eligibility, no_binding, ()
    )


def test_stale_and_cross_project_authority_denials_use_w1_reasons(tmp_path: Path) -> None:
    state = _state(tmp_path)
    stale = replace(state, plan_digest="c" * 64)
    stale_eligibility = _eligibility(tmp_path, stale)
    assert REASON_STALE_PLAN_AUTHORITY in context_switch_admission_reasons(
        stale_eligibility, stale, ()
    )

    cross_project = replace(state, project_id="foreign-project")
    cross_eligibility = _eligibility(tmp_path, cross_project)
    assert REASON_CROSS_PROJECT_CONTRADICTION in context_switch_admission_reasons(
        cross_eligibility, cross_project, ()
    )
