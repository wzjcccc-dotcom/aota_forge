"""Bounded durable context-switch transition over the existing coordinator.

This module consumes the pure W1 eligibility result and writes only transition
evidence into the existing ``TaskMainCoordinatorState`` record. It does not
change the live context, create a second store, or mint Project/Plan authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    DurableTaskMainContextBinding,
)
from aota_forge.runtime.task_main.context_switch_eligibility import SwitchEligibility
from aota_forge.runtime.task_main.coordinator_state import (
    ContextSwitchTransitionPhase,
    TaskMainCoordinatorState,
    _compute_coordinator_revision_token,
)
from aota_forge.runtime.task_main.coordinator_store import (
    CoordinatorPersistenceFailureError,
    StaleCoordinatorRevisionError,
    TaskMainCoordinatorStore,
)

CONTEXT_SWITCH_TRANSITION_IMPLEMENTED = True
CONTEXT_SWITCH_PUBLIC_OPERATION_AVAILABLE = False


class ContextSwitchTransitionError(ValueError):
    """Base error for fail-closed transition admission and recovery."""


class ContextSwitchAdmissionError(ContextSwitchTransitionError):
    """The trusted source snapshot or W1 result does not permit preparation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ContextSwitchConflictError(ContextSwitchTransitionError):
    """A durable transition or CAS precondition conflicts with the request."""

    safe_to_retry = False


class ContextSwitchRecoveryRequiredError(ContextSwitchTransitionError):
    """Durable evidence is absent, ambiguous, or explicitly unknown."""

    safe_to_retry = False


@dataclass(frozen=True)
class ContextSwitchIntent:
    """Deterministic, non-authoritative intent assembled from trusted inputs."""

    transition_id: str
    source_coordinator_id: str
    source_coordinator_revision: int
    source_revision_token: str
    source_project_id: str
    source_plan_authority: str
    source_plan_digest: str
    source_plan_source_revision: str | None
    source_session_ref: str
    source_binding_digest: str
    target_coordinator_id: str
    target_binding: DurableTaskMainContextBinding
    intent_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.target_binding, DurableTaskMainContextBinding):
            raise ContextSwitchAdmissionError(
                "UNKNOWN_OR_CORRUPT_STATE", "target context binding is not trusted binding evidence"
            )
        expected_digest = _digest_payload(
            _intent_payload(
                transition_id=self.transition_id,
                source_coordinator_id=self.source_coordinator_id,
                source_coordinator_revision=self.source_coordinator_revision,
                source_revision_token=self.source_revision_token,
                source_project_id=self.source_project_id,
                source_plan_authority=self.source_plan_authority,
                source_plan_digest=self.source_plan_digest,
                source_plan_source_revision=self.source_plan_source_revision,
                source_session_ref=self.source_session_ref,
                source_binding_digest=self.source_binding_digest,
                target_coordinator_id=self.target_coordinator_id,
                target_binding=self.target_binding,
            )
        )
        if self.intent_digest != expected_digest:
            raise ContextSwitchAdmissionError(
                "UNKNOWN_OR_CORRUPT_STATE",
                "intent_digest does not match the trusted input bundle",
            )

    @classmethod
    def from_trusted_state(
        cls,
        source: TaskMainCoordinatorState,
        target_binding: DurableTaskMainContextBinding,
        *,
        target_coordinator_id: str,
        transition_id: str,
        eligibility: SwitchEligibility,
    ) -> ContextSwitchIntent:
        if not isinstance(source, TaskMainCoordinatorState):
            raise ContextSwitchAdmissionError(
                "UNKNOWN_OR_CORRUPT_STATE", "source coordinator state is not durable coordinator truth"
            )
        if not isinstance(target_binding, DurableTaskMainContextBinding):
            raise ContextSwitchAdmissionError(
                "UNKNOWN_OR_CORRUPT_STATE", "target context binding is not trusted binding evidence"
            )
        _assert_w1_eligibility(eligibility)
        if source.context_binding is None:
            raise ContextSwitchAdmissionError(
                "AMBIGUOUS_TRUSTED_CONTEXT", "source coordinator has no durable context binding"
            )
        try:
            source.context_binding.assert_live_plan(
                project_id=source.project_id,
                plan_authority=source.plan_authority,
                plan_digest=source.plan_digest,
                plan_source_revision=source.plan_source_revision,
                origin_task_main_session_ref=source.origin_task_main_session_ref,
            )
        except ContextBindingError as exc:
            raise ContextSwitchAdmissionError(exc.code, str(exc)) from exc
        _text(transition_id, "transition_id")
        _text(target_coordinator_id, "target_coordinator_id")
        payload = _intent_payload(
            transition_id=transition_id,
            source_coordinator_id=source.coordinator_id,
            source_coordinator_revision=source.coordinator_revision,
            source_revision_token=source.revision_token,
            source_project_id=source.project_id,
            source_plan_authority=source.plan_authority,
            source_plan_digest=source.plan_digest,
            source_plan_source_revision=source.plan_source_revision,
            source_session_ref=source.origin_task_main_session_ref,
            source_binding_digest=source.context_binding.binding_digest,
            target_coordinator_id=target_coordinator_id,
            target_binding=target_binding,
        )
        return cls(
            transition_id=transition_id,
            source_coordinator_id=source.coordinator_id,
            source_coordinator_revision=source.coordinator_revision,
            source_revision_token=source.revision_token,
            source_project_id=source.project_id,
            source_plan_authority=source.plan_authority,
            source_plan_digest=source.plan_digest,
            source_plan_source_revision=source.plan_source_revision,
            source_session_ref=source.origin_task_main_session_ref,
            source_binding_digest=source.context_binding.binding_digest,
            target_coordinator_id=target_coordinator_id,
            target_binding=target_binding,
            intent_digest=_digest_payload(payload),
        )

    @classmethod
    def from_transition(cls, transition: Mapping[str, Any]) -> ContextSwitchIntent:
        """Rebuild only the already-durable intent for prepared recovery."""

        try:
            intent = cls(
                transition_id=transition["transition_id"],
                source_coordinator_id=transition["source_coordinator_id"],
                source_coordinator_revision=transition["source_coordinator_revision"],
                source_revision_token=transition["source_revision_token"],
                source_project_id=transition["source_project_id"],
                source_plan_authority=transition["source_plan_authority"],
                source_plan_digest=transition["source_plan_digest"],
                source_plan_source_revision=transition.get("source_plan_source_revision"),
                source_session_ref=transition["source_session_ref"],
                source_binding_digest=transition["source_binding_digest"],
                target_coordinator_id=transition["target_coordinator_id"],
                target_binding=DurableTaskMainContextBinding.from_dict(
                    transition["target_binding"]
                ),
                intent_digest=transition["intent_digest"],
            )
            return intent
        except (KeyError, TypeError, ValueError, ContextBindingError) as exc:
            raise ContextSwitchRecoveryRequiredError(
                f"durable prepared context switch is corrupt: {exc}"
            ) from exc

    def to_transition(
        self,
        *,
        phase: ContextSwitchTransitionPhase,
        prepared_revision: int,
        prepared_revision_token: str,
        applied_revision: int | None = None,
        applied_revision_token: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "phase": phase.value,
            "transition_id": self.transition_id,
            "intent_digest": self.intent_digest,
            "source_coordinator_id": self.source_coordinator_id,
            "source_coordinator_revision": self.source_coordinator_revision,
            "source_revision_token": self.source_revision_token,
            "source_project_id": self.source_project_id,
            "source_plan_authority": self.source_plan_authority,
            "source_plan_digest": self.source_plan_digest,
            "source_plan_source_revision": self.source_plan_source_revision,
            "source_session_ref": self.source_session_ref,
            "source_binding_digest": self.source_binding_digest,
            "target_coordinator_id": self.target_coordinator_id,
            "target_binding": self.target_binding.to_dict(),
            "prepared_revision": prepared_revision,
            "prepared_revision_token": prepared_revision_token,
            "applied_revision": applied_revision,
            "applied_revision_token": applied_revision_token,
        }


@dataclass(frozen=True)
class ContextSwitchReadback:
    """Read-only durable evidence returned after prepare/apply/recovery."""

    state: TaskMainCoordinatorState
    transition: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.state, TaskMainCoordinatorState):
            raise TypeError("state must be TaskMainCoordinatorState")
        if not isinstance(self.transition, Mapping):
            raise TypeError("transition must be a mapping")
        object.__setattr__(self, "transition", MappingProxyType(dict(self.transition)))

    @property
    def phase(self) -> ContextSwitchTransitionPhase:
        return ContextSwitchTransitionPhase(self.transition["phase"])


def prepare_context_switch(
    store: TaskMainCoordinatorStore,
    intent: ContextSwitchIntent,
    *,
    eligibility: SwitchEligibility,
) -> ContextSwitchReadback:
    """Durably prepare one intent with the source coordinator CAS token."""

    _assert_w1_eligibility(eligibility)
    current = _load_state(store, intent.source_coordinator_id)
    existing = _matching_existing_transition(current, intent)
    if existing is not None:
        _assert_source_snapshot(current, intent, require_source_revision=False)
        return ContextSwitchReadback(current, existing)
    _assert_source_snapshot(current, intent, require_source_revision=True)
    next_revision = current.coordinator_revision + 1
    transition = intent.to_transition(
        phase=ContextSwitchTransitionPhase.PREPARED,
        prepared_revision=next_revision,
        prepared_revision_token=_compute_coordinator_revision_token(
            current.coordinator_id, next_revision, current.status
        ),
    )
    candidate = _cas_transition(store, current, transition)
    persisted = candidate.context_switch_transition
    if persisted is None or persisted["phase"] != ContextSwitchTransitionPhase.PREPARED.value:
        raise ContextSwitchRecoveryRequiredError(
            "prepare CAS returned without durable PREPARED evidence"
        )
    return ContextSwitchReadback(candidate, persisted)


def apply_context_switch(
    store: TaskMainCoordinatorStore, intent: ContextSwitchIntent
) -> ContextSwitchReadback:
    """Durably apply a previously prepared intent; never retries a failed CAS."""

    current = _load_state(store, intent.source_coordinator_id)
    transition = _require_matching_transition(current, intent)
    _assert_source_snapshot(current, intent, require_source_revision=False)
    phase = ContextSwitchTransitionPhase(transition["phase"])
    if phase is ContextSwitchTransitionPhase.APPLIED:
        return ContextSwitchReadback(current, transition)
    if phase is ContextSwitchTransitionPhase.UNKNOWN:
        raise ContextSwitchRecoveryRequiredError(
            "durable context switch outcome is UNKNOWN; manual recovery is required"
        )
    if (
        current.coordinator_revision != transition["prepared_revision"]
        or current.revision_token != transition["prepared_revision_token"]
    ):
        raise ContextSwitchRecoveryRequiredError(
            "prepared context switch CAS evidence no longer matches durable coordinator"
        )
    next_revision = current.coordinator_revision + 1
    applied = intent.to_transition(
        phase=ContextSwitchTransitionPhase.APPLIED,
        prepared_revision=transition["prepared_revision"],
        prepared_revision_token=transition["prepared_revision_token"],
        applied_revision=next_revision,
        applied_revision_token=_compute_coordinator_revision_token(
            current.coordinator_id, next_revision, current.status
        ),
    )
    candidate = _cas_transition(store, current, applied)
    persisted = candidate.context_switch_transition
    if persisted is None or persisted["phase"] != ContextSwitchTransitionPhase.APPLIED.value:
        raise ContextSwitchRecoveryRequiredError(
            "apply CAS returned without durable APPLIED evidence"
        )
    return ContextSwitchReadback(candidate, persisted)


def readback_context_switch(
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    *,
    transition_id: str | None = None,
) -> ContextSwitchReadback:
    """Read transition evidence without changing durable state."""

    current = _load_state(store, coordinator_id)
    transition = current.context_switch_transition
    if transition is None:
        raise ContextSwitchRecoveryRequiredError(
            "no durable context switch transition exists for coordinator"
        )
    if transition_id is not None and transition["transition_id"] != transition_id:
        raise ContextSwitchConflictError("durable transition_id conflicts with readback request")
    _assert_phase_revision_evidence(current, transition)
    return ContextSwitchReadback(current, transition)


def recover_context_switch(
    store: TaskMainCoordinatorStore, coordinator_id: str, *, transition_id: str | None = None
) -> ContextSwitchReadback:
    """Recover only from durable PREPARED evidence; unknown outcomes stay denied."""

    readback = readback_context_switch(store, coordinator_id, transition_id=transition_id)
    phase = readback.phase
    if phase is ContextSwitchTransitionPhase.APPLIED:
        return readback
    if phase is ContextSwitchTransitionPhase.UNKNOWN:
        raise ContextSwitchRecoveryRequiredError(
            "durable context switch outcome is UNKNOWN; recovery cannot guess"
        )
    intent = ContextSwitchIntent.from_transition(readback.transition)
    return apply_context_switch(store, intent)


def _load_state(store: TaskMainCoordinatorStore, coordinator_id: str) -> TaskMainCoordinatorState:
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError("store must implement TaskMainCoordinatorStore")
    current = store.get(coordinator_id)
    if current is None:
        raise ContextSwitchRecoveryRequiredError(
            f"coordinator is not durably readable: {coordinator_id!r}"
        )
    return current


def _assert_source_snapshot(
    current: TaskMainCoordinatorState,
    intent: ContextSwitchIntent,
    *,
    require_source_revision: bool,
) -> None:
    if current.coordinator_id != intent.source_coordinator_id:
        raise ContextSwitchConflictError("source coordinator identity differs")
    if require_source_revision and (
        current.coordinator_revision != intent.source_coordinator_revision
        or current.revision_token != intent.source_revision_token
    ):
        raise ContextSwitchConflictError("source coordinator CAS precondition is stale")
    if (
        current.project_id != intent.source_project_id
        or current.plan_authority != intent.source_plan_authority
        or current.plan_digest != intent.source_plan_digest
        or current.plan_source_revision != intent.source_plan_source_revision
        or current.origin_task_main_session_ref != intent.source_session_ref
    ):
        raise ContextSwitchConflictError("source trusted identity differs from durable coordinator")
    if current.context_binding is None or current.context_binding.binding_digest != intent.source_binding_digest:
        raise ContextSwitchConflictError("source context binding evidence differs")


def _matching_existing_transition(
    current: TaskMainCoordinatorState, intent: ContextSwitchIntent
) -> Mapping[str, Any] | None:
    existing = current.context_switch_transition
    if existing is None:
        return None
    if (
        existing["transition_id"] != intent.transition_id
        or existing["intent_digest"] != intent.intent_digest
    ):
        raise ContextSwitchConflictError(
            "coordinator already holds a different context switch transition"
        )
    phase = ContextSwitchTransitionPhase(existing["phase"])
    if phase is ContextSwitchTransitionPhase.UNKNOWN:
        raise ContextSwitchRecoveryRequiredError(
            "durable context switch outcome is UNKNOWN; recovery cannot guess"
        )
    return existing


def _require_matching_transition(
    current: TaskMainCoordinatorState, intent: ContextSwitchIntent
) -> Mapping[str, Any]:
    transition = current.context_switch_transition
    if transition is None:
        raise ContextSwitchRecoveryRequiredError(
            "context switch must be PREPARED before apply"
        )
    if (
        transition["transition_id"] != intent.transition_id
        or transition["intent_digest"] != intent.intent_digest
    ):
        raise ContextSwitchConflictError("durable transition does not match apply intent")
    return transition


def _assert_phase_revision_evidence(
    state: TaskMainCoordinatorState, transition: Mapping[str, Any]
) -> None:
    phase = ContextSwitchTransitionPhase(transition["phase"])
    if phase is ContextSwitchTransitionPhase.PREPARED:
        expected_revision = transition["prepared_revision"]
        expected_token = transition["prepared_revision_token"]
    elif phase is ContextSwitchTransitionPhase.APPLIED:
        expected_revision = transition["applied_revision"]
        expected_token = transition["applied_revision_token"]
    else:
        return
    if state.coordinator_revision != expected_revision or state.revision_token != expected_token:
        raise ContextSwitchRecoveryRequiredError(
            "durable context switch phase does not match coordinator CAS evidence"
        )


def _cas_transition(
    store: TaskMainCoordinatorStore,
    current: TaskMainCoordinatorState,
    transition: Mapping[str, Any],
) -> TaskMainCoordinatorState:
    try:
        return store.compare_and_swap(
            current.coordinator_id,
            current.coordinator_revision,
            {"context_switch_transition": dict(transition)},
            expected_revision_token=current.revision_token,
        )
    except StaleCoordinatorRevisionError as exc:
        raise ContextSwitchConflictError(
            "context switch CAS precondition became stale; no retry was attempted"
        ) from exc
    except CoordinatorPersistenceFailureError as exc:
        raise ContextSwitchRecoveryRequiredError(
            "context switch durability outcome is unknown; no retry was attempted"
        ) from exc


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str or not value.strip():
        raise ContextSwitchAdmissionError("UNKNOWN_OR_CORRUPT_STATE", f"{label} must be non-empty text")
    if len(value) > 512 or "\x00" in value:
        raise ContextSwitchAdmissionError("UNKNOWN_OR_CORRUPT_STATE", f"{label} is unbounded")
    return value


def _assert_w1_eligibility(eligibility: SwitchEligibility) -> None:
    if not isinstance(eligibility, SwitchEligibility):
        raise ContextSwitchAdmissionError(
            "UNKNOWN_OR_CORRUPT_STATE", "W1 eligibility result is missing or corrupt"
        )
    if not eligibility.eligible:
        raise ContextSwitchAdmissionError(
            "SWITCH_INELIGIBLE",
            "W1 eligibility blocks context switch: "
            + ",".join(eligibility.reason_codes),
        )
    if eligibility.facts.get("trusted_state_checks_passed") is not True:
        raise ContextSwitchAdmissionError(
            "UNKNOWN_OR_CORRUPT_STATE",
            "W1 eligibility lacks trusted_state_checks_passed=true",
        )


def _intent_payload(
    *,
    transition_id: str,
    source_coordinator_id: str,
    source_coordinator_revision: int,
    source_revision_token: str,
    source_project_id: str,
    source_plan_authority: str,
    source_plan_digest: str,
    source_plan_source_revision: str | None,
    source_session_ref: str,
    source_binding_digest: str,
    target_coordinator_id: str,
    target_binding: DurableTaskMainContextBinding,
) -> dict[str, Any]:
    return {
        "transition_id": transition_id,
        "source_coordinator_id": source_coordinator_id,
        "source_coordinator_revision": source_coordinator_revision,
        "source_revision_token": source_revision_token,
        "source_project_id": source_project_id,
        "source_plan_authority": source_plan_authority,
        "source_plan_digest": source_plan_digest,
        "source_plan_source_revision": source_plan_source_revision,
        "source_session_ref": source_session_ref,
        "source_binding_digest": source_binding_digest,
        "target_coordinator_id": target_coordinator_id,
        "target_binding": target_binding.to_dict(),
    }


def _digest_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "CONTEXT_SWITCH_PUBLIC_OPERATION_AVAILABLE",
    "CONTEXT_SWITCH_TRANSITION_IMPLEMENTED",
    "ContextSwitchAdmissionError",
    "ContextSwitchConflictError",
    "ContextSwitchIntent",
    "ContextSwitchReadback",
    "ContextSwitchRecoveryRequiredError",
    "ContextSwitchTransitionError",
    "apply_context_switch",
    "prepare_context_switch",
    "readback_context_switch",
    "recover_context_switch",
]
