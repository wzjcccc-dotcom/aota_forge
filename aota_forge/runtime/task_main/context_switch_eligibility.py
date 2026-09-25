"""Pure M2/W1 derivation of safe context-switch eligibility.

This module reads trusted snapshots owned by the existing Project/Plan,
coordinator, context-binding, authorized-root, and execution seams. It does
not persist eligibility or perform a context transition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    is_bound_origin_session_ref,
    origin_session_ref_text,
)
from aota_forge.core.task_main_context import TrustedProjectPlanIdentity
from aota_forge.runtime.task_main.context_binding import ContextBindingError
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATUSES,
    HUMAN_BRAKE_SCOPES,
    HUMAN_BRAKE_STATES,
    WORK_ITEM_STATUSES,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.work_plane.authorized_roots import (
    ROOT_REF_ACTIVE_WORKTREE,
    AuthorizedRootSet,
)

CONTEXT_SWITCH_ELIGIBILITY_IMPLEMENTED = True
ELIGIBILITY_IS_PERSISTED = False
SECOND_STATE_STORE_CREATED = False
MODEL_MINTS_SWITCH_AUTHORITY = False
LIVE_CONTEXT_SWITCH_IMPLEMENTED = False

REASON_ACTIVE_EXECUTION = "ACTIVE_EXECUTION"
REASON_NON_TERMINAL_WORK = "NON_TERMINAL_WORK"
REASON_HUMAN_BRAKE_ACTIVE = "HUMAN_BRAKE_ACTIVE"
REASON_COORDINATOR_NOT_ACTIVE = "COORDINATOR_NOT_ACTIVE"
REASON_USER_APPROVAL_REQUIRED = "USER_APPROVAL_REQUIRED"
REASON_STALE_PLAN_AUTHORITY = "STALE_PLAN_AUTHORITY"
REASON_AUTHORIZED_ROOT_MISMATCH = "AUTHORIZED_ROOT_MISMATCH"
REASON_CROSS_PROJECT_CONTRADICTION = "CROSS_PROJECT_CONTRADICTION"
REASON_AMBIGUOUS_TRUSTED_CONTEXT = "AMBIGUOUS_TRUSTED_CONTEXT"
REASON_UNKNOWN_OR_CORRUPT_STATE = "UNKNOWN_OR_CORRUPT_STATE"

_MAX_REASON_CODES = 8
_MAX_FACTS = 16


@dataclass(frozen=True)
class SwitchEligibility:
    """Bounded, non-authoritative result of one mechanical derivation."""

    eligible: bool
    reason_codes: tuple[str, ...] = ()
    facts: Mapping[str, bool | int | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise TypeError("eligible must be bool")
        codes = tuple(self.reason_codes)
        if len(codes) > _MAX_REASON_CODES:
            raise ValueError("reason_codes exceeds the bounded result size")
        if self.eligible and codes:
            raise ValueError("eligible result cannot carry blocking reason codes")
        if not self.eligible and not codes:
            raise ValueError("ineligible result requires a blocking reason code")
        for code in codes:
            if not isinstance(code, str) or not code:
                raise ValueError("reason_codes must contain non-empty strings")
        if not isinstance(self.facts, Mapping):
            raise TypeError("facts must be a mapping")
        facts = dict(self.facts)
        if len(facts) > _MAX_FACTS:
            raise ValueError("facts exceeds the bounded result size")
        for key, value in facts.items():
            if not isinstance(key, str) or not key:
                raise ValueError("fact names must be non-empty strings")
            if not isinstance(value, (bool, int, str)):
                raise TypeError("facts must contain only bounded scalar values")
        object.__setattr__(self, "reason_codes", codes)
        object.__setattr__(self, "facts", MappingProxyType(facts))

    @property
    def reason(self) -> str:
        return "eligible" if self.eligible else self.reason_codes[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "facts": dict(sorted(self.facts.items())),
        }


def _append_reason(reasons: list[str], code: str) -> None:
    if code not in reasons and len(reasons) < _MAX_REASON_CODES:
        reasons.append(code)


def _blocked(
    reasons: list[str], facts: Mapping[str, bool | int | str]
) -> SwitchEligibility:
    return SwitchEligibility(
        eligible=not reasons, reason_codes=tuple(reasons), facts=facts
    )


def derive_switch_eligibility(
    identity: TrustedProjectPlanIdentity,
    authorized_roots: AuthorizedRootSet,
    coordinator_state: TaskMainCoordinatorState,
    execution_records: Sequence[DurableExecutionRecord],
) -> SwitchEligibility:
    """Derive eligibility from trusted durable snapshots without side effects.

    ``execution_records`` is intentionally a snapshot, normally obtained from
    the existing ``ExecutionStateStore`` by the caller. The evaluator never
    opens a store, performs CAS, changes coordinator state, or changes context.
    """

    facts: dict[str, bool | int | str] = {}
    reasons: list[str] = []
    required_types = (
        isinstance(identity, TrustedProjectPlanIdentity),
        isinstance(authorized_roots, AuthorizedRootSet),
        isinstance(coordinator_state, TaskMainCoordinatorState),
        isinstance(execution_records, (tuple, list)),
    )
    if not all(required_types):
        return _blocked([REASON_UNKNOWN_OR_CORRUPT_STATE], facts)

    try:
        plan_binding = identity.plan_binding
        context_binding = coordinator_state.context_binding
        if context_binding is None:
            _append_reason(reasons, REASON_AMBIGUOUS_TRUSTED_CONTEXT)
        else:
            try:
                context_binding.assert_live_plan(
                    project_id=coordinator_state.project_id,
                    plan_authority=coordinator_state.plan_authority,
                    plan_digest=coordinator_state.plan_digest,
                    plan_source_revision=coordinator_state.plan_source_revision,
                    origin_task_main_session_ref=coordinator_state.origin_task_main_session_ref,
                )
            except ContextBindingError as exc:
                code = getattr(exc, "code", "")
                if code == "PROJECT_CONTEXT_MISMATCH":
                    _append_reason(reasons, REASON_CROSS_PROJECT_CONTRADICTION)
                elif code in {"PLAN_CONTEXT_MISMATCH", "STALE_CONTEXT"}:
                    _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)
                elif code == "SESSION_RECOVERY_REQUIRED":
                    _append_reason(reasons, REASON_AMBIGUOUS_TRUSTED_CONTEXT)
                else:
                    _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)

        if plan_binding.source_digest is None:
            _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)
        if coordinator_state.project_id != identity.project_id:
            _append_reason(reasons, REASON_CROSS_PROJECT_CONTRADICTION)
        if (
            context_binding is not None
            and context_binding.project_id != identity.project_id
        ):
            _append_reason(reasons, REASON_CROSS_PROJECT_CONTRADICTION)
        if (
            plan_binding.plan_id != context_binding.plan_binding.plan_id
            if context_binding is not None
            else False
        ):
            _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)
        if coordinator_state.plan_authority != plan_binding.authority_ref:
            _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)
        if coordinator_state.plan_digest != plan_binding.source_digest:
            _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)
        if (coordinator_state.plan_source_revision is None) != (
            plan_binding.source_revision is None
        ) or (
            coordinator_state.plan_source_revision is not None
            and str(coordinator_state.plan_source_revision)
            != str(plan_binding.source_revision)
        ):
            _append_reason(reasons, REASON_STALE_PLAN_AUTHORITY)

        root_digest = authorized_roots.digest()
        facts["authorized_root_digest_bound"] = bool(
            context_binding is not None
            and context_binding.authorized_roots_digest is not None
            and context_binding.authorized_roots_digest == root_digest
        )
        if not facts["authorized_root_digest_bound"]:
            _append_reason(reasons, REASON_AUTHORIZED_ROOT_MISMATCH)
        if authorized_roots.get(ROOT_REF_ACTIVE_WORKTREE) is None:
            _append_reason(reasons, REASON_AMBIGUOUS_TRUSTED_CONTEXT)
        for root in authorized_roots.roots:
            if root.project_id != identity.project_id:
                _append_reason(reasons, REASON_CROSS_PROJECT_CONTRADICTION)
        for grant in authorized_roots.cross_project_bindings:
            if grant.requesting_project != identity.project_id:
                _append_reason(reasons, REASON_CROSS_PROJECT_CONTRADICTION)

        status = coordinator_state.status.value
        facts["coordinator_status"] = status
        if status not in COORDINATOR_STATUSES:
            _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
        elif status != CoordinatorStatus.ACTIVE.value:
            _append_reason(reasons, REASON_COORDINATOR_NOT_ACTIVE)
        approval = coordinator_state.user_approval_satisfied
        if type(approval) is not bool:
            _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
            facts["user_approval_satisfied"] = str(approval)
        else:
            facts["user_approval_satisfied"] = approval
            if not approval:
                _append_reason(reasons, REASON_USER_APPROVAL_REQUIRED)

        brake = coordinator_state.human_brake
        brake_state = "NONE"
        if brake is not None:
            if not isinstance(brake, Mapping):
                _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
            else:
                brake_state = brake.get("state")
                scope = brake.get("scope")
                affected = brake.get("affected_work", ())
                if (
                    brake_state not in HUMAN_BRAKE_STATES
                    or scope not in HUMAN_BRAKE_SCOPES
                    or not isinstance(affected, (tuple, list))
                ):
                    _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
                elif brake_state == "NONE":
                    if scope != "NONE" or affected:
                        _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
                else:
                    _append_reason(reasons, REASON_HUMAN_BRAKE_ACTIVE)
        facts["human_brake_state"] = str(brake_state)

        non_pending_work = 0
        for work_item in coordinator_state.work_items:
            status = coordinator_state.wi_status.get(work_item)
            if status not in WORK_ITEM_STATUSES:
                _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
            elif status != WorkItemCoordinatorStatus.PENDING.value:
                non_pending_work += 1
        facts["non_pending_work_count"] = non_pending_work
        if non_pending_work:
            _append_reason(reasons, REASON_NON_TERMINAL_WORK)

        session_ref = coordinator_state.origin_task_main_session_ref
        if not is_bound_origin_session_ref(session_ref):
            _append_reason(reasons, REASON_AMBIGUOUS_TRUSTED_CONTEXT)

        seen_task_ids: set[str] = set()
        non_terminal_execution = 0
        for record in execution_records:
            if not isinstance(record, DurableExecutionRecord):
                _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
                continue
            if record.canonical_task_id in seen_task_ids:
                _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
            seen_task_ids.add(record.canonical_task_id)
            record_session = origin_session_ref_text(record.origin_session_ref)
            if not is_bound_origin_session_ref(record_session):
                _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)
            elif record_session != session_ref:
                _append_reason(reasons, REASON_AMBIGUOUS_TRUSTED_CONTEXT)
            if not record.canonical_task_state.is_terminal:
                non_terminal_execution += 1
        facts["execution_record_count"] = len(execution_records)
        facts["non_terminal_execution_count"] = non_terminal_execution
        if non_terminal_execution:
            _append_reason(reasons, REASON_ACTIVE_EXECUTION)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        _append_reason(reasons, REASON_UNKNOWN_OR_CORRUPT_STATE)

    facts["trusted_state_checks_passed"] = not reasons
    return _blocked(reasons, facts)


evaluate_switch_eligibility = derive_switch_eligibility


__all__ = [
    "CONTEXT_SWITCH_ELIGIBILITY_IMPLEMENTED",
    "ELIGIBILITY_IS_PERSISTED",
    "LIVE_CONTEXT_SWITCH_IMPLEMENTED",
    "MODEL_MINTS_SWITCH_AUTHORITY",
    "REASON_ACTIVE_EXECUTION",
    "REASON_AMBIGUOUS_TRUSTED_CONTEXT",
    "REASON_AUTHORIZED_ROOT_MISMATCH",
    "REASON_COORDINATOR_NOT_ACTIVE",
    "REASON_CROSS_PROJECT_CONTRADICTION",
    "REASON_HUMAN_BRAKE_ACTIVE",
    "REASON_NON_TERMINAL_WORK",
    "REASON_STALE_PLAN_AUTHORITY",
    "REASON_UNKNOWN_OR_CORRUPT_STATE",
    "REASON_USER_APPROVAL_REQUIRED",
    "SECOND_STATE_STORE_CREATED",
    "SwitchEligibility",
    "derive_switch_eligibility",
    "evaluate_switch_eligibility",
]
