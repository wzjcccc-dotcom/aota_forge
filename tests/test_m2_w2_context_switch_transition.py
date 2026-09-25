"""Focused M2/W2 proofs for coordinator-owned switch transition evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.task_main_context import TrustedProjectPlanIdentity
from aota_forge.runtime.task_main.context_binding import DurableTaskMainContextBinding
from aota_forge.runtime.task_main.context_switch_eligibility import (
    derive_switch_eligibility,
)
from aota_forge.runtime.task_main.context_switch_transition import (
    ContextSwitchAdmissionError,
    ContextSwitchConflictError,
    ContextSwitchIntent,
    ContextSwitchRecoveryRequiredError,
    apply_context_switch,
    prepare_context_switch,
    readback_context_switch,
    recover_context_switch,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    ContextSwitchTransitionPhase,
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

PROJECT_ID = "transition-project"
PLAN_ID = "plan_002_transition"
AUTHORITY_REF = "local-governance:transition-project/plan_002_transition"
PLAN_DIGEST = "a" * 64
PLAN_REVISION = "revision-10"
SESSION_REF = "ses_m2_w2_transition"


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
                worktree_id="transition-worktree",
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
        coordinator_id="transition-project:M2",
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


def _target_binding() -> DurableTaskMainContextBinding:
    return DurableTaskMainContextBinding(
        project_id="target-project",
        project_fingerprint="c" * 64,
        plan_binding=PlanAuthorityBinding(
            plan_id="plan_target",
            source_kind="local_governance",
            authority_ref="local-governance:target-project/plan_target",
            source_revision="revision-11",
            source_digest="d" * 64,
        ),
        origin_task_main_session_ref="ses_m2_w2_target",
    )


def _intent(
    store: FileBackedTaskMainCoordinatorStore,
    tmp_path: Path,
    *,
    transition_id: str = "transition-1",
) -> ContextSwitchIntent:
    source = store.get("transition-project:M2")
    assert source is not None
    roots = _roots(tmp_path)
    identity = TrustedProjectPlanIdentity(
        project_id=PROJECT_ID,
        plan_binding=source.context_binding.plan_binding,
    )
    eligibility = derive_switch_eligibility(identity, roots, source, ())
    return ContextSwitchIntent.from_trusted_state(
        source,
        _target_binding(),
        target_coordinator_id="target-project:M2",
        transition_id=transition_id,
        eligibility=eligibility,
    )


def test_prepare_apply_and_reopen_readback_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    store = FileBackedTaskMainCoordinatorStore(path)
    store.create(_state(tmp_path))
    intent = _intent(store, tmp_path)

    prepared = prepare_context_switch(store, intent)
    assert prepared.phase is ContextSwitchTransitionPhase.PREPARED
    prepared_revision = prepared.state.coordinator_revision
    store.close()

    reopened = FileBackedTaskMainCoordinatorStore(path)
    readback = readback_context_switch(
        reopened, "transition-project:M2", transition_id="transition-1"
    )
    assert readback.phase is ContextSwitchTransitionPhase.PREPARED
    applied = apply_context_switch(reopened, intent)
    assert applied.phase is ContextSwitchTransitionPhase.APPLIED
    assert applied.state.coordinator_revision == prepared_revision + 1
    replay = apply_context_switch(reopened, intent)
    assert replay.phase is ContextSwitchTransitionPhase.APPLIED
    assert replay.state.coordinator_revision == applied.state.coordinator_revision
    reopened.close()

    recovered_store = FileBackedTaskMainCoordinatorStore(path)
    recovered = recover_context_switch(recovered_store, "transition-project:M2")
    assert recovered.phase is ContextSwitchTransitionPhase.APPLIED


def test_ineligible_w1_result_never_writes_transition(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    store = FileBackedTaskMainCoordinatorStore(path)
    source = _state(tmp_path)
    store.create(source)
    blocked = replace(source, user_approval_satisfied=False)
    identity = TrustedProjectPlanIdentity(
        project_id=PROJECT_ID,
        plan_binding=source.context_binding.plan_binding,
    )
    eligibility = derive_switch_eligibility(identity, _roots(tmp_path), blocked, ())

    with pytest.raises(ContextSwitchAdmissionError) as exc_info:
        ContextSwitchIntent.from_trusted_state(
            source,
            _target_binding(),
            target_coordinator_id="target-project:M2",
            transition_id="blocked-transition",
            eligibility=eligibility,
        )
    assert exc_info.value.code == "SWITCH_INELIGIBLE"
    assert store.get(source.coordinator_id).context_switch_transition is None


def test_stale_source_cas_fails_closed_without_retry(tmp_path: Path) -> None:
    store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    created = store.create(_state(tmp_path))
    intent = _intent(store, tmp_path)
    store.compare_and_swap(
        created.coordinator_id,
        created.coordinator_revision,
        {"progression_revision": 1},
        expected_revision_token=created.revision_token,
    )

    with pytest.raises(ContextSwitchConflictError) as exc_info:
        prepare_context_switch(store, intent)
    assert exc_info.value.safe_to_retry is False
    current = store.get(created.coordinator_id)
    assert current.context_switch_transition is None


def test_prepared_recovery_and_unknown_outcome_are_distinct(tmp_path: Path) -> None:
    store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    store.create(_state(tmp_path))
    intent = _intent(store, tmp_path)
    prepared = prepare_context_switch(store, intent)
    unknown = dict(prepared.transition)
    unknown["phase"] = ContextSwitchTransitionPhase.UNKNOWN.value
    store.compare_and_swap(
        prepared.state.coordinator_id,
        prepared.state.coordinator_revision,
        {"context_switch_transition": unknown},
        expected_revision_token=prepared.state.revision_token,
    )

    with pytest.raises(ContextSwitchRecoveryRequiredError):
        recover_context_switch(store, prepared.state.coordinator_id)
