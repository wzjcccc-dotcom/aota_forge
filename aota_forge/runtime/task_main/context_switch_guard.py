"""Pure W3 admission guard for the not-yet-live context-switch seam.

The guard consumes W1's bounded eligibility result and rechecks only the
existing durable truth vocabularies that can change between derivation and
admission. It has no store dependency and cannot mutate coordinator or
execution state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    is_bound_origin_session_ref,
    origin_session_ref_text,
)
from aota_forge.core.execution.state import parse_state
from aota_forge.runtime.task_main.context_binding import ContextBindingError
from aota_forge.runtime.task_main.context_switch_eligibility import (
    REASON_ACTIVE_EXECUTION,
    REASON_AMBIGUOUS_TRUSTED_CONTEXT,
    REASON_AUTHORIZED_ROOT_MISMATCH,
    REASON_COORDINATOR_NOT_ACTIVE,
    REASON_CROSS_PROJECT_CONTRADICTION,
    REASON_HUMAN_BRAKE_ACTIVE,
    REASON_NON_TERMINAL_WORK,
    REASON_STALE_PLAN_AUTHORITY,
    REASON_UNKNOWN_OR_CORRUPT_STATE,
    REASON_USER_APPROVAL_REQUIRED,
    SwitchEligibility,
)
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATUSES,
    HUMAN_BRAKE_SCOPES,
    HUMAN_BRAKE_STATES,
    WORK_ITEM_STATUSES,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.work_plane.human_brake import parse_brake_scope, parse_brake_state

CONTEXT_SWITCH_ADMISSION_GUARD_IMPLEMENTED = True
CONTEXT_SWITCH_GUARD_IS_PURE = True
CONTEXT_SWITCH_GUARD_PERSISTS_STATE = False
CONTEXT_SWITCH_PUBLIC_OPERATION_AVAILABLE = False
SECOND_STATE_STORE_CREATED = False

_MAX_REASON_CODES = 10


class ContextSwitchAdmissionDeniedError(ValueError):
    """A bounded guard decision denied admission without changing state."""

    code = "CONTEXT_SWITCH_ADMISSION_DENIED"

    def __init__(self, reason_codes: Sequence[str]) -> None:
        self.reason_codes = tuple(reason_codes)
        super().__init__(
            "context switch admission denied: " + ",".join(self.reason_codes)
        )


def context_switch_admission_reasons(
    eligibility: SwitchEligibility,
    coordinator_state: TaskMainCoordinatorState,
    execution_records: Sequence[DurableExecutionRecord],
) -> tuple[str, ...]:
    """Return existing W1 vocabulary reasons for a pure admission decision.

    The W1 result remains the eligibility authority. The additional checks are
    snapshot freshness and lifecycle checks only; they do not derive a second
    eligibility model or persist a guard result.
    """

    reasons: list[str] = []

    def deny(code: str) -> None:
        if code not in reasons and len(reasons) < _MAX_REASON_CODES:
            reasons.append(code)

    if not isinstance(eligibility, SwitchEligibility):
        return (REASON_UNKNOWN_OR_CORRUPT_STATE,)
    if not isinstance(coordinator_state, TaskMainCoordinatorState):
        return (REASON_UNKNOWN_OR_CORRUPT_STATE,)
    if not isinstance(execution_records, (tuple, list)):
        return (REASON_UNKNOWN_OR_CORRUPT_STATE,)

    if eligibility.eligible:
        if eligibility.reason_codes:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
    else:
        for code in eligibility.reason_codes:
            deny(code)
    facts = eligibility.facts
    if facts.get("trusted_state_checks_passed") is not eligibility.eligible:
        deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

    try:
        status = coordinator_state.status.value
        if status not in COORDINATOR_STATUSES:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        elif status != CoordinatorStatus.ACTIVE.value:
            deny(REASON_COORDINATOR_NOT_ACTIVE)
        if facts.get("coordinator_status") != status:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

        approval = coordinator_state.user_approval_satisfied
        if type(approval) is not bool:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        elif not approval:
            deny(REASON_USER_APPROVAL_REQUIRED)
        if facts.get("user_approval_satisfied") is not approval:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

        _check_context_binding(coordinator_state, deny)
        brake_state = _check_human_brake(coordinator_state.human_brake, deny)
        if facts.get("human_brake_state") != brake_state:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

        non_pending_work = 0
        for work_item in coordinator_state.work_items:
            work_status = coordinator_state.wi_status.get(work_item)
            if work_status not in WORK_ITEM_STATUSES:
                deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
            elif work_status != WorkItemCoordinatorStatus.PENDING.value:
                # This includes COMPLETION_PENDING_RECONCILIATION: semantic
                # reconciliation does not make the Work Item terminal.
                non_pending_work += 1
                deny(REASON_NON_TERMINAL_WORK)
        if facts.get("non_pending_work_count") != non_pending_work:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

        if facts.get("execution_record_count") != len(execution_records):
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        non_terminal_execution = 0
        seen_task_ids: set[str] = set()
        for record in execution_records:
            if not isinstance(record, DurableExecutionRecord):
                deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
                continue
            if record.canonical_task_id in seen_task_ids:
                deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
            seen_task_ids.add(record.canonical_task_id)
            record_session = origin_session_ref_text(record.origin_session_ref)
            if (
                not is_bound_origin_session_ref(record_session)
                or record_session != coordinator_state.origin_task_main_session_ref
            ):
                deny(REASON_AMBIGUOUS_TRUSTED_CONTEXT)
            parsed_state = parse_state(record.canonical_task_state)
            if not parsed_state.is_terminal:
                non_terminal_execution += 1
                deny(REASON_ACTIVE_EXECUTION)
        if facts.get("non_terminal_execution_count") != non_terminal_execution:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        deny(REASON_UNKNOWN_OR_CORRUPT_STATE)

    if eligibility.eligible and facts.get("authorized_root_digest_bound") is not True:
        deny(REASON_AUTHORIZED_ROOT_MISMATCH)
    return tuple(reasons)


def assert_context_switch_admitted(
    eligibility: SwitchEligibility,
    coordinator_state: TaskMainCoordinatorState,
    execution_records: Sequence[DurableExecutionRecord],
) -> None:
    """Raise on any guard failure; return ``None`` only for a clean snapshot."""

    reasons = context_switch_admission_reasons(
        eligibility, coordinator_state, execution_records
    )
    if reasons:
        raise ContextSwitchAdmissionDeniedError(reasons)


def _check_context_binding(
    state: TaskMainCoordinatorState, deny: Any
) -> None:
    binding = state.context_binding
    if binding is None:
        deny(REASON_AMBIGUOUS_TRUSTED_CONTEXT)
        return
    try:
        binding.assert_live_plan(
            project_id=state.project_id,
            plan_authority=state.plan_authority,
            plan_digest=state.plan_digest,
            plan_source_revision=state.plan_source_revision,
            origin_task_main_session_ref=state.origin_task_main_session_ref,
        )
    except ContextBindingError as exc:
        code = getattr(exc, "code", "")
        if code == "PROJECT_CONTEXT_MISMATCH":
            deny(REASON_CROSS_PROJECT_CONTRADICTION)
        elif code in {"PLAN_CONTEXT_MISMATCH", "STALE_CONTEXT"}:
            deny(REASON_STALE_PLAN_AUTHORITY)
        elif code == "SESSION_RECOVERY_REQUIRED":
            deny(REASON_AMBIGUOUS_TRUSTED_CONTEXT)
        else:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)


def _check_human_brake(brake: Any, deny: Any) -> str:
    if brake is None:
        return "NONE"
    if not isinstance(brake, Mapping):
        deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        return "UNKNOWN"
    try:
        state = parse_brake_state(brake.get("state", "NONE"))
        scope = parse_brake_scope(brake.get("scope", "NONE"))
        affected = brake.get("affected_work", ())
        if not isinstance(affected, (tuple, list)):
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
            return state.value
        if state.value == "NONE" and (scope.value != "NONE" or affected):
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        elif state.value != "NONE":
            deny(REASON_HUMAN_BRAKE_ACTIVE)
        if scope.value not in HUMAN_BRAKE_SCOPES or state.value not in HUMAN_BRAKE_STATES:
            deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        return state.value
    except (TypeError, ValueError):
        deny(REASON_UNKNOWN_OR_CORRUPT_STATE)
        return "UNKNOWN"


__all__ = [
    "CONTEXT_SWITCH_ADMISSION_GUARD_IMPLEMENTED",
    "CONTEXT_SWITCH_GUARD_IS_PURE",
    "CONTEXT_SWITCH_GUARD_PERSISTS_STATE",
    "CONTEXT_SWITCH_PUBLIC_OPERATION_AVAILABLE",
    "SECOND_STATE_STORE_CREATED",
    "ContextSwitchAdmissionDeniedError",
    "assert_context_switch_admitted",
    "context_switch_admission_reasons",
]
