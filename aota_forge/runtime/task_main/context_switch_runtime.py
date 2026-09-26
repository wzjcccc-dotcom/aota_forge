"""Executor-neutral M2/W4 context-switch composition over existing stores.

The coordinator record remains the only durable switch evidence.  This module
only composes the accepted W1 eligibility, W3 admission, and W2 transition
seams with a trusted target resolver and exact-session re-entry checks.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.execution.durable_state import (
    ExecutionStateStore,
    is_bound_origin_session_ref,
)
from aota_forge.core.task_main_context import ContextLocator, TrustedProjectPlanIdentity
from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    DurableTaskMainContextBinding,
)
from aota_forge.runtime.task_main.context_switch_eligibility import (
    derive_switch_eligibility,
)
from aota_forge.runtime.task_main.context_switch_guard import (
    ContextSwitchAdmissionDeniedError,
    assert_context_switch_admitted,
)
from aota_forge.runtime.task_main.context_switch_transition import (
    ContextSwitchAdmissionError,
    ContextSwitchConflictError,
    ContextSwitchIntent,
    ContextSwitchRecoveryRequiredError,
    ContextSwitchTransitionError,
    apply_context_switch,
    prepare_context_switch,
    readback_context_switch,
)
from aota_forge.runtime.task_main.coordinator_state import (
    ContextSwitchTransitionPhase,
    TaskMainCoordinatorState,
)
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.work_plane.authorized_roots import (
    ROOT_REF_ACTIVE_WORKTREE,
    AuthorizedRootSet,
    validate_root_set_against_sandbox,
)

CONTEXT_SWITCH_RUNTIME_IMPLEMENTED = True
SECOND_CONTEXT_STORE_CREATED = False
SECOND_SESSION_STORE_CREATED = False
SESSION_ID_IS_AUTHORITY = False
ORIGIN_SESSION_REF_IS_AUTHORITY = False
MODEL_MINTS_TARGET_AUTHORITY = False


class ContextSwitchRuntimeError(ValueError):
    """Bounded fail-closed error from switch orchestration or re-entry."""

    safe_to_retry = False

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class TrustedContextSwitchTarget:
    """Server-derived target facts, never constructed from model arguments."""

    project_id: str
    project_fingerprint: str
    plan_binding: PlanAuthorityBinding
    origin_task_main_session_ref: str
    authorized_roots: AuthorizedRootSet
    governance_context: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ContextSwitchRuntimeError("UNAUTHORIZED_TARGET", "trusted target Project is missing")
        if not isinstance(self.plan_binding, PlanAuthorityBinding):
            raise ContextSwitchRuntimeError(
                "UNAUTHORIZED_TARGET", "trusted target Plan authority binding is required"
            )
        if self.plan_binding.source_digest is None:
            raise ContextSwitchRuntimeError(
                "STALE_TARGET_AUTHORITY", "trusted target Plan has no observed source digest"
            )
        if not is_bound_origin_session_ref(self.origin_task_main_session_ref):
            raise ContextSwitchRuntimeError(
                "SESSION_RECOVERY_REQUIRED", "trusted target requires an exact origin session"
            )
        if not isinstance(self.authorized_roots, AuthorizedRootSet):
            raise ContextSwitchRuntimeError(
                "AUTHORIZED_ROOT_MISMATCH", "trusted target AuthorizedRootSet is invalid"
            )
        if self.authorized_roots.get(ROOT_REF_ACTIVE_WORKTREE) is None:
            raise ContextSwitchRuntimeError(
                "AUTHORIZED_ROOT_MISMATCH", "trusted target lacks an active-worktree root"
            )
        native_project_ids = {
            root.project_id
            for root in self.authorized_roots.roots
            if root.source != "explicit_grant"
        }
        if native_project_ids != {self.project_id}:
            raise ContextSwitchRuntimeError(
                "AUTHORIZED_ROOT_MISMATCH",
                "trusted target roots do not belong to the target Project",
            )

    def to_context_binding(self) -> DurableTaskMainContextBinding:
        return DurableTaskMainContextBinding(
            project_id=self.project_id,
            project_fingerprint=self.project_fingerprint,
            plan_binding=self.plan_binding,
            origin_task_main_session_ref=self.origin_task_main_session_ref,
            authorized_roots_digest=self.authorized_roots.digest(),
        )

    def identity(self) -> TrustedProjectPlanIdentity:
        return TrustedProjectPlanIdentity(
            project_id=self.project_id,
            plan_binding=self.plan_binding,
        )


@dataclass(frozen=True)
class ContextSwitchView:
    """Rehydrated current context plus the non-authoritative projection inputs."""

    target: TrustedContextSwitchTarget
    binding: DurableTaskMainContextBinding
    phase: ContextSwitchTransitionPhase | None

    @property
    def identity(self) -> TrustedProjectPlanIdentity:
        return self.target.identity()


TargetResolver = Callable[[ContextLocator], TrustedContextSwitchTarget | None]


def local_governance_target_resolver(
    *,
    project_id: str,
    project_fingerprint: str,
    origin_task_main_session_ref: str,
    authorized_roots: AuthorizedRootSet,
    governance_store: Any,
) -> TargetResolver:
    """Resolve same-project Plan targets from the existing trusted store."""

    authority_prefix = f"local-governance:{project_id}/"

    def resolve(locator: ContextLocator) -> TrustedContextSwitchTarget | None:
        if locator.project_ref is not None and locator.project_ref != project_id:
            return None
        plan_ref = locator.plan_ref
        if plan_ref is None:
            return None
        plan_id = plan_ref.removeprefix(authority_prefix)
        try:
            record = governance_store.get_plan(project_id, plan_id)
        except Exception:
            return None
        if record is None or getattr(record, "lifecycle_state", None) != "active":
            return None
        authority = getattr(record, "authority", None)
        if not isinstance(authority, PlanAuthorityBinding):
            return None
        return TrustedContextSwitchTarget(
            project_id=project_id,
            project_fingerprint=project_fingerprint,
            plan_binding=authority,
            origin_task_main_session_ref=origin_task_main_session_ref,
            authorized_roots=authorized_roots,
        )

    return resolve


class ContextSwitchRuntime:
    """One bounded switch/re-entry composition over one existing coordinator."""

    def __init__(
        self,
        *,
        coordinator_store: TaskMainCoordinatorStore,
        execution_store: ExecutionStateStore,
        coordinator_id: str,
        target_resolver: TargetResolver | None = None,
    ) -> None:
        if not isinstance(coordinator_store, TaskMainCoordinatorStore):
            raise TypeError("coordinator_store must be TaskMainCoordinatorStore")
        if not isinstance(execution_store, ExecutionStateStore):
            raise TypeError("execution_store must be ExecutionStateStore")
        if not isinstance(coordinator_id, str) or not coordinator_id.strip():
            raise TypeError("coordinator_id must be non-empty string")
        if target_resolver is not None and not callable(target_resolver):
            raise TypeError("target_resolver must be callable")
        self._coordinator_store = coordinator_store
        self._execution_store = execution_store
        self._coordinator_id = coordinator_id.strip()
        self._target_resolver = target_resolver
        self._source_target: TrustedContextSwitchTarget | None = None
        self._sandbox: Any | None = None
        self._lock = threading.RLock()

    def configure_target_resolver(self, target_resolver: TargetResolver) -> None:
        """Install one trusted composition resolver when construction left it unset."""

        if not callable(target_resolver):
            raise TypeError("target_resolver must be callable")
        with self._lock:
            if self._target_resolver is None:
                self._target_resolver = target_resolver

    @property
    def execution_store(self) -> ExecutionStateStore:
        """Expose the existing execution store for composition identity checks."""

        return self._execution_store

    @property
    def target_resolver_configured(self) -> bool:
        """Whether trusted target resolution has been composed."""

        return self._target_resolver is not None

    def configure_local_governance_target_resolver(
        self,
        *,
        project_id: str,
        project_fingerprint: str,
        origin_task_main_session_ref: str,
        authorized_roots: AuthorizedRootSet,
        governance_store: Any,
    ) -> None:
        """Compose the local-governance resolver without widening the host seam."""

        self.configure_target_resolver(
            local_governance_target_resolver(
                project_id=project_id,
                project_fingerprint=project_fingerprint,
                origin_task_main_session_ref=origin_task_main_session_ref,
                authorized_roots=authorized_roots,
                governance_store=governance_store,
            )
        )

    def bind_source(
        self,
        *,
        project_id: str,
        project_fingerprint: str,
        plan_binding: PlanAuthorityBinding | None,
        origin_task_main_session_ref: str,
        authorized_roots: AuthorizedRootSet,
        governance_context: Mapping[str, Any] | None = None,
        sandbox: Any | None = None,
    ) -> None:
        """Bind trusted host composition facts; no model input reaches here."""

        if plan_binding is None:
            raise ContextSwitchRuntimeError(
                "AUTHORITY_DENIED", "context switching requires one trusted Plan authority binding"
            )
        target = TrustedContextSwitchTarget(
            project_id=project_id,
            project_fingerprint=project_fingerprint,
            plan_binding=plan_binding,
            origin_task_main_session_ref=origin_task_main_session_ref,
            authorized_roots=authorized_roots,
            governance_context=governance_context,
        )
        if sandbox is not None:
            try:
                validate_root_set_against_sandbox(target.authorized_roots, sandbox)
            except Exception as exc:
                raise ContextSwitchRuntimeError(
                    "AUTHORIZED_ROOT_MISMATCH",
                    "trusted source AuthorizedRootSet is not sandbox-derived",
                ) from exc
        with self._lock:
            if self._source_target is not None and self._source_target != target:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_CONFLICT", "trusted source context was already bound differently"
                )
            self._source_target = target
            self._sandbox = sandbox

    def current_context(self) -> ContextSwitchView:
        """Read and mechanically rehydrate the current durable context."""

        with self._lock:
            return self._current_context()

    def rehydrate(self, session_ref: str) -> ContextSwitchView:
        """Re-enter only the exact durable session recorded by the coordinator."""

        with self._lock:
            state = self._load_state()
            if session_ref != state.origin_task_main_session_ref:
                raise ContextSwitchRuntimeError(
                    "SESSION_RECOVERY_REQUIRED",
                    "re-entry session differs from the durable origin session",
                )
            return self._current_context()

    def switch(self, locator: ContextLocator) -> dict[str, Any]:
        """Run target resolution -> W1 -> W3 -> W2 -> readback exactly once."""

        if not isinstance(locator, ContextLocator):
            raise TypeError("locator must be ContextLocator")
        with self._lock:
            state = self._load_state()
            current = self._current_context()
            target = self._resolve_target(locator)
            self._validate_target(target, state=state, locator=locator)
            target_binding = target.to_context_binding()

            if self._same_context(current, target_binding, target):
                return self._project_result("already-bound", current)

            if state.context_switch_transition is not None:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_CONFLICT",
                    "a different durable context switch is already APPLIED",
                )

            eligibility = self._derive_eligibility(state, current)
            try:
                assert_context_switch_admitted(
                    eligibility,
                    state,
                    tuple(self._execution_store.list_all()),
                )
            except ContextSwitchAdmissionDeniedError as exc:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_ADMISSION_DENIED",
                    "context switch admission denied: " + ",".join(exc.reason_codes),
                ) from exc

            try:
                intent = ContextSwitchIntent.from_trusted_state(
                    state,
                    target_binding,
                    target_coordinator_id=state.coordinator_id,
                    transition_id=self._transition_id(state, target_binding),
                    eligibility=eligibility,
                )
                prepare_context_switch(
                    self._coordinator_store,
                    intent,
                    eligibility=eligibility,
                )
                applied = apply_context_switch(self._coordinator_store, intent)
                readback = readback_context_switch(
                    self._coordinator_store,
                    state.coordinator_id,
                    transition_id=intent.transition_id,
                )
            except ContextSwitchAdmissionError as exc:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_ADMISSION_DENIED", str(exc)
                ) from exc
            except ContextSwitchConflictError as exc:
                raise ContextSwitchRuntimeError("CONTEXT_SWITCH_CONFLICT", str(exc)) from exc
            except ContextSwitchRecoveryRequiredError as exc:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_RECOVERY_REQUIRED", str(exc)
                ) from exc
            except ContextSwitchTransitionError as exc:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_RECOVERY_REQUIRED", str(exc)
                ) from exc

            if applied.phase is not ContextSwitchTransitionPhase.APPLIED or readback.phase is not ContextSwitchTransitionPhase.APPLIED:
                raise ContextSwitchRuntimeError(
                    "CONTEXT_SWITCH_RECOVERY_REQUIRED",
                    "durable switch readback is not APPLIED",
                )
            switched = self._current_context()
            return self._project_result("switched", switched)

    def _current_context(self) -> ContextSwitchView:
        state = self._load_state()
        source = self._source_target
        if source is None:
            raise ContextSwitchRuntimeError(
                "AUTHORITY_DENIED", "trusted source context is not composed"
            )
        if source.origin_task_main_session_ref != state.origin_task_main_session_ref:
            raise ContextSwitchRuntimeError(
                "SESSION_RECOVERY_REQUIRED",
                "trusted source session differs from durable coordinator session",
            )

        transition = state.context_switch_transition
        if transition is None:
            durable = state.context_binding
            if durable is None:
                raise ContextSwitchRuntimeError(
                    "AMBIGUOUS_TRUSTED_CONTEXT", "durable source context binding is absent"
                )
            source_binding = source.to_context_binding()
            try:
                durable.assert_live_plan(
                    project_id=state.project_id,
                    plan_authority=state.plan_authority,
                    plan_digest=state.plan_digest,
                    plan_source_revision=state.plan_source_revision,
                    origin_task_main_session_ref=state.origin_task_main_session_ref,
                )
            except ContextBindingError as exc:
                code = getattr(exc, "code", "CONTEXT_SWITCH_RECOVERY_REQUIRED")
                raise ContextSwitchRuntimeError(code, str(exc)) from exc
            self._assert_binding_compatible(source_binding, durable)
            self._assert_roots_compatible(source, durable)
            return ContextSwitchView(source, durable, None)

        phase = self._phase(transition)
        if phase is ContextSwitchTransitionPhase.PREPARED:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED",
                "durable switch is PREPARED; restart cannot guess whether it applied",
            )
        if phase is ContextSwitchTransitionPhase.UNKNOWN:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED",
                "durable switch outcome is UNKNOWN; recovery is required",
            )
        if phase is not ContextSwitchTransitionPhase.APPLIED:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED", "durable switch phase is unsupported"
            )

        try:
            durable_target = DurableTaskMainContextBinding.from_dict(
                transition["target_binding"]
            )
        except (KeyError, TypeError, ValueError, ContextBindingError) as exc:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED", "durable target binding is corrupt"
            ) from exc
        target = self._resolve_target_for_binding(durable_target)
        self._validate_target(target, state=state, expected_binding=durable_target)
        return ContextSwitchView(target, durable_target, phase)

    def _derive_eligibility(self, state: TaskMainCoordinatorState, current: ContextSwitchView):
        try:
            records = tuple(self._execution_store.list_all())
            return derive_switch_eligibility(
                current.identity,
                current.target.authorized_roots,
                state,
                records,
            )
        except Exception as exc:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_ADMISSION_DENIED",
                "trusted eligibility snapshot could not be read",
            ) from exc

    def _resolve_target(self, locator: ContextLocator) -> TrustedContextSwitchTarget:
        resolver = self._target_resolver
        if resolver is None:
            raise ContextSwitchRuntimeError(
                "UNAUTHORIZED_TARGET", "trusted target resolver is not composed"
            )
        try:
            target = resolver(locator)
        except ContextSwitchRuntimeError:
            raise
        except Exception as exc:
            raise ContextSwitchRuntimeError(
                "UNAUTHORIZED_TARGET", "trusted target resolution failed"
            ) from exc
        if target is None:
            raise ContextSwitchRuntimeError(
                "UNAUTHORIZED_TARGET", "target Project/Plan is not in the trusted registry"
            )
        if not isinstance(target, TrustedContextSwitchTarget):
            raise ContextSwitchRuntimeError(
                "UNAUTHORIZED_TARGET", "target resolver returned untrusted authority facts"
            )
        return target

    def _resolve_target_for_binding(
        self, expected_binding: DurableTaskMainContextBinding
    ) -> TrustedContextSwitchTarget:
        locator = ContextLocator(
            project_ref=expected_binding.project_id,
            plan_ref=expected_binding.plan_binding.authority_ref,
        )
        return self._resolve_target(locator)

    def _validate_target(
        self,
        target: TrustedContextSwitchTarget,
        *,
        state: TaskMainCoordinatorState,
        locator: ContextLocator | None = None,
        expected_binding: DurableTaskMainContextBinding | None = None,
    ) -> None:
        if locator is not None:
            if locator.project_ref is not None and locator.project_ref != target.project_id:
                raise ContextSwitchRuntimeError(
                    "PROJECT_CONTEXT_MISMATCH", "target locator does not match trusted Project"
                )
            if locator.plan_ref is not None and locator.plan_ref not in {
                target.plan_binding.plan_id,
                target.plan_binding.authority_ref,
            }:
                raise ContextSwitchRuntimeError(
                    "PLAN_CONTEXT_MISMATCH", "target locator does not match trusted Plan authority"
                )
        if target.project_id != state.project_id:
            raise ContextSwitchRuntimeError(
                "CROSS_PROJECT_SWITCH_DENIED",
                "current canonical switch contract permits same-project Plan switching only",
            )
        if target.origin_task_main_session_ref != state.origin_task_main_session_ref:
            raise ContextSwitchRuntimeError(
                "SESSION_RECOVERY_REQUIRED",
                "target context is not bound to the exact current task-main session",
            )
        if self._sandbox is not None:
            try:
                validate_root_set_against_sandbox(target.authorized_roots, self._sandbox)
            except Exception as exc:
                raise ContextSwitchRuntimeError(
                    "AUTHORIZED_ROOT_MISMATCH",
                    "target AuthorizedRootSet is not derived from the trusted sandbox",
                ) from exc
        target_binding = target.to_context_binding()
        if expected_binding is not None:
            self._assert_binding_compatible(expected_binding, target_binding)
        if target_binding.authorized_roots_digest != target.authorized_roots.digest():
            raise ContextSwitchRuntimeError(
                "AUTHORIZED_ROOT_MISMATCH", "target root evidence digest does not match"
            )
        try:
            from aota_forge.core.task_main_context import _trusted_governance_context

            _trusted_governance_context(
                target,
                target.identity(),
                carrier_override=target.governance_context,
            )
        except Exception as exc:
            code = getattr(exc, "code", "AUTHORITY_DENIED")
            raise ContextSwitchRuntimeError(
                code if isinstance(code, str) else "AUTHORITY_DENIED",
                "trusted target governance projection is invalid",
            ) from exc

    @staticmethod
    def _assert_binding_compatible(
        expected: DurableTaskMainContextBinding,
        current: DurableTaskMainContextBinding,
    ) -> None:
        try:
            expected.assert_compatible(current)
        except ContextBindingError as exc:
            code = getattr(exc, "code", "STALE_CONTEXT")
            if code == "AUTHORITY_DENIED":
                code = "AUTHORIZED_ROOT_MISMATCH"
            raise ContextSwitchRuntimeError(code, str(exc)) from exc

    @staticmethod
    def _assert_roots_compatible(
        target: TrustedContextSwitchTarget,
        binding: DurableTaskMainContextBinding,
    ) -> None:
        if binding.authorized_roots_digest != target.authorized_roots.digest():
            raise ContextSwitchRuntimeError(
                "AUTHORIZED_ROOT_MISMATCH", "durable root evidence differs from trusted roots"
            )

    @staticmethod
    def _same_context(
        current: ContextSwitchView,
        target_binding: DurableTaskMainContextBinding,
        target: TrustedContextSwitchTarget,
    ) -> bool:
        return (
            current.binding.project_id == target_binding.project_id
            and current.binding.project_fingerprint == target_binding.project_fingerprint
            and current.binding.plan_binding == target_binding.plan_binding
            and current.binding.origin_task_main_session_ref
            == target_binding.origin_task_main_session_ref
            and current.binding.authorized_roots_digest
            == target_binding.authorized_roots_digest
            and current.target.authorized_roots.digest() == target.authorized_roots.digest()
        )

    @staticmethod
    def _phase(transition: Mapping[str, Any]) -> ContextSwitchTransitionPhase:
        try:
            return ContextSwitchTransitionPhase(transition["phase"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED", "durable switch phase is corrupt"
            ) from exc

    def _load_state(self) -> TaskMainCoordinatorState:
        state = self._coordinator_store.get(self._coordinator_id)
        if not isinstance(state, TaskMainCoordinatorState):
            raise ContextSwitchRuntimeError(
                "CONTEXT_SWITCH_RECOVERY_REQUIRED", "durable coordinator state is unavailable"
            )
        return state

    @staticmethod
    def _transition_id(
        state: TaskMainCoordinatorState,
        target_binding: DurableTaskMainContextBinding,
    ) -> str:
        return f"{state.coordinator_id}:context-switch:{target_binding.binding_digest[:16]}"

    @staticmethod
    def _project_result(state: str, view: ContextSwitchView) -> dict[str, Any]:
        context = view.identity.to_dict()
        context["authorized_roots"] = view.target.authorized_roots.public_roots()
        payload: dict[str, Any] = {
            "mode": "switch",
            "state": state,
            "context": context,
            "status": {
                "transition_phase": view.phase.value if view.phase is not None else "NONE",
                "exact_session_reentry": True,
                "is_authority": False,
            },
        }
        if view.target.governance_context is not None:
            payload["governance_context"] = dict(view.target.governance_context)
        return payload


__all__ = [
    "CONTEXT_SWITCH_RUNTIME_IMPLEMENTED",
    "MODEL_MINTS_TARGET_AUTHORITY",
    "ORIGIN_SESSION_REF_IS_AUTHORITY",
    "SECOND_CONTEXT_STORE_CREATED",
    "SECOND_SESSION_STORE_CREATED",
    "SESSION_ID_IS_AUTHORITY",
    "ContextSwitchRuntime",
    "ContextSwitchRuntimeError",
    "ContextSwitchView",
    "local_governance_target_resolver",
    "TargetResolver",
    "TrustedContextSwitchTarget",
]
