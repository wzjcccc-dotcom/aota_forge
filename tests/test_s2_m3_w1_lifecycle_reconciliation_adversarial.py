"""S2/M3/W1 proof: Lifecycle / Reconciliation Adversarial Validation.

Bounded adversarial validation of the accepted S2 control plane on the first
real Hermes slice source base (S2/M2 known-good 0fb5b242). No production
repair is performed here; every observation is captured as empirical evidence
for M3/W2 finding classification.

Primary topology (Plan #19 M3/W1, SPEC section 9):

    canonical ingress / ExecutionPackage
    -> real ExecutionDispatcher
    -> real ExecutorRegistry
    -> real frozen HermesAdapter (accepted S3/M1 projection)
    -> bounded deterministic AdversarialHermesHost (offline host double)
    -> Core route / status / result reconciliation

A minimal Core-seam probe double is used ONLY where the frozen HermesAdapter
structurally cannot emit a contradicted response (it rebinds every projection
to the Core-supplied identity), so the accepted S2/M1 dispatcher identity
guards remain challengeable. Every double exercises real production Core
logic; no Core behavior is reimplemented in this file.

Case map (Plan-authoritative W1 case set):

    CASE_1  duplicate dispatch ................. W1DuplicateDispatchTestCase
    CASE_2  idempotent replay .................. W1IdempotentReplayTestCase
    CASE_2b idempotency conflict ............... W1IdempotencyConflictTestCase
    CASE_3  unknown runtime status ............. W1UnknownRuntimeStatusTestCase
    CASE_4  terminal reconciliation ............ W1TerminalReconciliationTestCase
    CASE_5  invalid task identity .............. W1InvalidIdentityTestCase
    CASE_5b invalid route identity ............. W1InvalidIdentityTestCase
    CASE_6  lost/unknown adapter handle ........ W1LostAdapterHandleTestCase
    CASE_7  malformed result ................... W1MalformedUnavailableResultTestCase
    CASE_7b unavailable result ................. W1MalformedUnavailableResultTestCase

Durability boundary (M1/M2 accepted, D0):

    All scenarios run inside ONE live process. No dispatcher/process restart
    is performed, and no route or idempotency loss is framed as a defect.
    Cross-process route recovery and cross-process idempotency are explicitly
    NOT validated and NOT claimed (SPEC sections 5, 15).

Empirical findings recorded by this file (full text in the W1 result report,
final taxonomy belongs to M3/W2):

    W1-F01 canonical ingress represents duplicate canonical_task_id dispatch
         as INTERNAL_MECHANICAL_ERROR instead of a typed duplicate code
         (dispatcher level is typed).
    W1-F02 ExecutionDispatcher.reconcile_status collapses adapter binding
         consistency violations (typed ADAPTER_PROTOCOL_ERROR from a
         partially-inconsistent Hermes handle registry) into UNKNOWN, while
         dispatcher.status fails closed on the same state.
    W1-F03 transient host result corruption or result-fetch transport failure
         is projected by the frozen HermesAdapter as a terminal FAILED
         CanonicalResult (RESULT_MALFORMED / ADAPTER_PROTOCOL_ERROR); Core
         records it as sticky terminal truth and a later genuine completion
         is permanently rejected.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapterError,
)
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.ingress import (
    bind_execution_dispatcher,
    execute,
    reset_execution_dispatcher,
)
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import (
    AdapterProtocolError,
    DispatcherError,
    ExecutionDispatcher,
    IdempotencyConflictError,
    TaskNotFoundError,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState

W1_INSTRUCTION = "S2/M3/W1 adversarial lifecycle probe"


class AdversarialHermesHost:
    """Bounded deterministic HermesHostClient double; no subprocess, no real host.

    Conforms to the frozen S3/M1 HermesHostClient protocol and records every
    handle-level interaction so the tests can prove fail-closed Core paths
    never broadcast, re-bind, or query alternate handles.
    """

    def __init__(self) -> None:
        self.dispatch_payloads: list[dict[str, Any]] = []
        self.status_calls: list[str] = []
        self.result_calls: list[str] = []
        self.status_by_handle: dict[str, Any] = {}
        self.result_by_handle: dict[str, Any] = {}

    @staticmethod
    def handle_for(canonical_task_id: str) -> str:
        return f"w1-host-handle-{canonical_task_id}"

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatch_payloads.append(dict(payload))
        task_id = payload["context"]["canonical_task_id"]
        return {
            "adapter_handle": self.handle_for(task_id),
            "status": "pending",
            "dispatch_time": "2026-08-29T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.status_calls.append(adapter_handle)
        scripted = self.status_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return dict(scripted)

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        self.result_calls.append(adapter_handle)
        scripted = self.result_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": False, "status": "running", "details": "not a W1 case"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "not a W1 case"}}


def w1_package(
    task_id: str,
    *,
    instruction: str = W1_INSTRUCTION,
    idempotency_key: str | None = None,
) -> ExecutionPackage:
    """Canonical task_dispatch package for the accepted production Hermes vector."""
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation="task_dispatch",
        capability_requirements={"execution_mode": "async"},
        idempotency_key=idempotency_key or f"w1-idem-{task_id}",
        correlation_id=f"w1-corr-{task_id}",
    )


def w1_start_params(
    task_id: str,
    *,
    instruction: str = W1_INSTRUCTION,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "canonical_task_id": task_id,
        "executor": HERMES_EXECUTOR_ID,
        "role": "coder",
        "instruction": instruction,
        "project_id": "aota_forge",
    }
    if idempotency_key is not None:
        params["idempotency_key"] = idempotency_key
    return params


class CoreGuardProbeAdapter(ExecutorAdapter):
    """Minimal Core-seam double used ONLY where the frozen Hermes adapter cannot
    emit a contradicted response by construction.

    The accepted HermesAdapter rebinds every status/result projection to the
    Core-supplied canonical task identity (proven in S2/M2/W2), so dispatcher
    identity guards for contradicted responses are unreachable through the
    Hermes vector. This probe exists solely to challenge those real production
    Core guards at the nearest reachable seam; it implements no reconciliation
    logic of its own.
    """

    def __init__(self, violation: str | None = None) -> None:
        self.violation = violation
        self.dispatch_count = 0
        self.status_count = 0
        self.result_count = 0
        self.raise_on_status = False
        self.status_state = CanonicalTaskState.RUNNING
        self._capabilities = ExecutorCapabilities(
            executor_id="w1-probe",
            adapter_kind="core_guard_probe",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=False,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("none",),
            supports_working_directory=False,
            supports_artifact_transport=False,
        )

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True, errors=())

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.dispatch_count += 1
        reported = (
            "w1-phantom-task" if self.violation == "dispatch_id" else package.canonical_task_id
        )
        return DispatchResult(
            canonical_task_id=reported,
            adapter_handle=f"w1-probe-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.QUEUED,
            dispatch_time="2026-08-29T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self.status_count += 1
        if self.violation == "status_error":
            raise RuntimeError("probe ordinary transport noise")
        if self.raise_on_status:
            raise RuntimeError("probe transport failure")
        reported = (
            "w1-phantom-task" if self.violation == "status_id" else canonical_task_id
        )
        return TaskStatusResult(canonical_task_id=reported, state=self.status_state)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self.result_count += 1
        reported = "w1-phantom-task" if self.violation == "result_id" else canonical_task_id
        executor = (
            "not-the-routed-executor" if self.violation == "result_executor" else "w1-probe"
        )
        return CanonicalResult.success(
            canonical_task_id=reported,
            executor_id=executor,
            correlation_id="w1-probe-corr",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        raise AssertionError("cancel is not part of the W1 probe surface")

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        raise AssertionError("resume is not part of the W1 probe surface")


def probe_package(task_id: str, *, instruction: str = W1_INSTRUCTION) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation="task_dispatch",
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=f"w1-probe-idem-{task_id}",
        correlation_id=f"w1-probe-corr-{task_id}",
    )


class W1ProductionHermesTestCase(unittest.TestCase):
    """Shared production-composition fixture: real adapter + registry + dispatcher."""

    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.host = AdversarialHermesHost()
        self.dispatcher = create_production_execution_dispatcher(host_client=self.host)
        self.adapter = self.dispatcher.registry.get(HERMES_EXECUTOR_ID)

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def _dispatch(self, task_id: str, **kwargs: Any) -> DispatchResult:
        result = self.dispatcher.dispatch(
            w1_package(task_id, **kwargs), target_executor_id=HERMES_EXECUTOR_ID
        )
        self.assertEqual(result.canonical_task_id, task_id)
        return result

    def _probe_dispatcher(
        self, violation: str | None
    ) -> tuple[ExecutionDispatcher, CoreGuardProbeAdapter]:
        probe = CoreGuardProbeAdapter(violation)
        registry = ExecutorRegistry()
        registry.register(probe)
        dispatcher = ExecutionDispatcher(registry)
        return dispatcher, probe


class W1DuplicateDispatchTestCase(W1ProductionHermesTestCase):
    """CASE_1: duplicate dispatch must not produce uncontrolled routes/side effects."""

    def test_duplicate_task_id_with_new_key_fails_closed(self) -> None:
        task_id = "s2-m3-w1-dup-01"
        first = self._dispatch(task_id)
        before = self.dispatcher.get_route(task_id)

        with self.assertRaises(DispatcherError) as ctx:
            self._dispatch(task_id, idempotency_key="w1-idem-different-key")
        self.assertIn("DUPLICATE_CANONICAL_TASK_ID", str(ctx.exception))

        # exactly one host dispatch, one route, one idempotency entry; original
        # dispatch result and route truth are untouched
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(len(self.dispatcher.list_routes()), 1)
        after = self.dispatcher.get_route(task_id)
        self.assertEqual(after.adapter_handle, first.adapter_handle)
        self.assertEqual(after.dispatch_attempt_id, before.dispatch_attempt_id)
        self.assertEqual(after.last_known_state, CanonicalTaskState.QUEUED)

        # the rejected duplicate key left no replay entry either: re-dispatching
        # that key for a different task is still a fresh (allowed) dispatch
        self._dispatch("s2-m3-w1-dup-02", idempotency_key="w1-idem-different-key")
        self.assertEqual(len(self.dispatcher.list_routes()), 2)

    def test_ingress_duplicate_task_start_rejected_without_second_host_dispatch(self) -> None:
        task_id = "s2-m3-w1-dup-ingress"
        bind_execution_dispatcher(self.dispatcher)
        try:
            first = execute("execution.task_start", w1_start_params(task_id))
            self.assertTrue(first["ok"], first)
            duplicate = execute("execution.task_start", w1_start_params(task_id))
            self.assertFalse(duplicate["ok"])
            # FINDING W1-F01 was: the ingress degraded the typed dispatcher
            # rejection (DUPLICATE_CANONICAL_TASK_ID) into
            # INTERNAL_MECHANICAL_ERROR. S2/M3/R1 repaired the projection to
            # the accepted canonical dispatch-rejection semantic; the
            # DUPLICATE_CANONICAL_TASK_ID detail survives only in the message.
            self.assertEqual(duplicate["error"]["code"], "DISPATCH_REJECTED")
            self.assertIn("DUPLICATE_CANONICAL_TASK_ID", duplicate["error"]["message"])
            self.assertEqual(len(self.host.dispatch_payloads), 1)
            self.assertEqual(len(self.dispatcher.list_routes()), 1)
            self.assertEqual(
                self.dispatcher.get_route(task_id).adapter_handle,
                first["data"]["adapter_handle"],
            )
        finally:
            reset_execution_dispatcher()


class W1IdempotentReplayTestCase(W1ProductionHermesTestCase):
    """CASE_2: same key + same fingerprint -> REPLAY with no second side effect."""

    def test_same_key_same_intent_replays_identical_dispatch_result(self) -> None:
        task_id = "s2-m3-w1-replay-01"
        first = self._dispatch(task_id, idempotency_key="w1-shared-key")
        replay = self._dispatch(task_id, idempotency_key="w1-shared-key")

        self.assertIs(replay, first)
        self.assertEqual(replay.adapter_handle, first.adapter_handle)
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(len(self.dispatcher.list_routes()), 1)
        # accepted replay identity: the same canonical task identity is reused
        self.assertEqual(replay.canonical_task_id, task_id)

    def test_ingress_replay_reuses_canonical_identity_without_host_side_effect(self) -> None:
        task_id = "s2-m3-w1-replay-ingress"
        bind_execution_dispatcher(self.dispatcher)
        try:
            first = execute(
                "execution.task_start",
                w1_start_params(task_id, idempotency_key="w1-ingress-shared"),
            )
            replay = execute(
                "execution.task_start",
                w1_start_params(task_id, idempotency_key="w1-ingress-shared"),
            )
            self.assertTrue(first["ok"], first)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(first["data"], replay["data"])
            self.assertEqual(len(self.host.dispatch_payloads), 1)
            self.assertEqual(len(self.dispatcher.list_routes()), 1)
        finally:
            reset_execution_dispatcher()

    def test_replay_scope_is_process_local_d0_documentation(self) -> None:
        """D0 boundary documentation, NOT a restart-recovery defect claim.

        The accepted idempotency index is an in-memory structure owned by one
        live dispatcher instance. A separate dispatcher instance in the same
        process starts with an empty index. This records the validated scope
        (process_local) without making or denying any cross-process claim.
        """
        task_id = "s2-m3-w1-scope-01"
        self._dispatch(task_id, idempotency_key="w1-scope-key")

        second_host = AdversarialHermesHost()
        second = create_production_execution_dispatcher(host_client=second_host)
        result = second.dispatch(
            w1_package(task_id, idempotency_key="w1-scope-key"),
            target_executor_id=HERMES_EXECUTOR_ID,
        )
        # fresh instance: no replay available (no durable claim either)
        self.assertEqual(len(second_host.dispatch_payloads), 1)
        self.assertEqual(result.adapter_handle, second_host.handle_for(task_id))
        self.assertEqual(len(self.host.dispatch_payloads), 1)


class W1IdempotencyConflictTestCase(W1ProductionHermesTestCase):
    """CASE_2b: same key + changed semantic fingerprint -> CONFLICT, fail closed."""

    def test_dispatcher_conflict_rejected_before_second_host_dispatch(self) -> None:
        task_id = "s2-m3-w1-conflict-01"
        self._dispatch(task_id, idempotency_key="w1-conflict-key")
        before = self.dispatcher.get_route(task_id)

        with self.assertRaises(IdempotencyConflictError):
            self._dispatch(
                "s2-m3-w1-conflict-other",
                instruction="a semantically different intent",
                idempotency_key="w1-conflict-key",
            )

        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertFalse(self.dispatcher.has_route("s2-m3-w1-conflict-other"))
        self.assertEqual(len(self.dispatcher.list_routes()), 1)
        # original route not corrupted by the rejected conflict
        after = self.dispatcher.get_route(task_id)
        self.assertEqual(after.adapter_handle, before.adapter_handle)
        self.assertEqual(after.intent_fingerprint, before.intent_fingerprint)
        self.assertEqual(after.last_known_state, CanonicalTaskState.QUEUED)

        # the poisoned key still replays the original committed result unchanged
        replayed = self._dispatch(task_id, idempotency_key="w1-conflict-key")
        self.assertEqual(replayed.adapter_handle, before.adapter_handle)
        self.assertEqual(len(self.host.dispatch_payloads), 1)

    def test_ingress_conflict_reports_typed_idempotency_conflict(self) -> None:
        task_id = "s2-m3-w1-conflict-ingress"
        bind_execution_dispatcher(self.dispatcher)
        try:
            first = execute(
                "execution.task_start",
                w1_start_params(task_id, idempotency_key="w1-ingress-conflict"),
            )
            self.assertTrue(first["ok"], first)
            conflict = execute(
                "execution.task_start",
                w1_start_params(
                    "s2-m3-w1-conflict-ingress-b",
                    instruction="changed intent for the same key",
                    idempotency_key="w1-ingress-conflict",
                ),
            )
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["error"]["code"], "IDEMPOTENCY_CONFLICT")
            self.assertEqual(len(self.host.dispatch_payloads), 1)
            self.assertEqual(len(self.dispatcher.list_routes()), 1)
        finally:
            reset_execution_dispatcher()


class W1UnknownRuntimeStatusTestCase(W1ProductionHermesTestCase):
    """CASE_3: ordinary runtime uncertainty reconciles to UNKNOWN, never completion."""

    def test_unreachable_projects_unknown_and_later_truth_recovers(self) -> None:
        task_id = "s2-m3-w1-unknown-01"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = {"status": "unreachable"}
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.state, CanonicalTaskState.UNKNOWN)
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.last_known_state, CanonicalTaskState.UNKNOWN)
        self.assertFalse(route.last_known_state.is_terminal)

        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )

        # unknown is reconcilable: recovery to terminal is allowed
        self.host.status_by_handle[handle] = {"status": "done"}
        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.COMPLETED
        )
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_host_transport_failure_is_ordinary_uncertainty_not_completion(self) -> None:
        task_id = "s2-m3-w1-unknown-02"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = RuntimeError("host connection refused")
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.state, CanonicalTaskState.UNKNOWN)
        self.assertNotEqual(status_res.state, CanonicalTaskState.COMPLETED)
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

    def test_protocol_identity_violation_is_not_swallowed_as_unknown(self) -> None:
        """SPEC 17: contradicted protocol identity fails closed; it is not uncertainty."""
        dispatcher, _probe = self._probe_dispatcher("status_id")
        dispatcher.dispatch(probe_package("s2-m3-w1-protocol-01"), target_executor_id="w1-probe")

        with self.assertRaises(AdapterProtocolError):
            dispatcher.status("s2-m3-w1-protocol-01")
        with self.assertRaises(AdapterProtocolError):
            dispatcher.reconcile_status("s2-m3-w1-protocol-01")
        self.assertEqual(
            dispatcher.get_route("s2-m3-w1-protocol-01").last_known_state,
            CanonicalTaskState.QUEUED,
        )

    def test_ordinary_probe_transport_failure_reconciles_to_unknown(self) -> None:
        dispatcher, _probe = self._probe_dispatcher("status_error")
        dispatcher.dispatch(probe_package("s2-m3-w1-protocol-02"), target_executor_id="w1-probe")
        self.assertEqual(
            dispatcher.reconcile_status("s2-m3-w1-protocol-02"), CanonicalTaskState.UNKNOWN
        )


class W1TerminalReconciliationTestCase(W1ProductionHermesTestCase):
    """CASE_4: terminal success/failure reconciliation and terminal stickiness."""

    def test_terminal_success_reconciles_through_status_and_result(self) -> None:
        task_id = "s2-m3-w1-terminal-ok"
        handle = self._dispatch(task_id).adapter_handle
        self.host.status_by_handle[handle] = {"status": "done"}
        self.assertEqual(
            self.dispatcher.status(task_id).state, CanonicalTaskState.COMPLETED
        )
        self.host.result_by_handle[handle] = {"status": "done", "exit_code": 0}
        result = self.dispatcher.result(task_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_terminal_failure_reconciles_as_definite_failed_not_unknown(self) -> None:
        task_id = "s2-m3-w1-terminal-fail"
        handle = self._dispatch(task_id).adapter_handle
        self.host.status_by_handle[handle] = {"status": "failed"}
        self.assertEqual(self.dispatcher.status(task_id).state, CanonicalTaskState.FAILED)

        self.host.result_by_handle[handle] = {
            "status": "failed",
            "exit_code": 9,
            "error": {"code": "WORKER_EXIT_NONZERO", "message": "bounded W1 failure"},
        }
        result = self.dispatcher.result(task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.FAILED.value)
        self.assertEqual(result.error["code"], "WORKER_EXIT_NONZERO")
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.FAILED,
        )

    def test_known_terminal_failure_is_sticky_against_contradictions(self) -> None:
        task_id = "s2-m3-w1-terminal-sticky"
        handle = self._dispatch(task_id).adapter_handle
        self.host.status_by_handle[handle] = {"status": "failed"}
        self.dispatcher.status(task_id)

        for contradictory in ("running", "pending", "done", "weird-status"):
            self.host.status_by_handle[handle] = {"status": contradictory}
            with self.assertRaises(AdapterProtocolError):
                self.dispatcher.status(task_id)
            self.assertEqual(
                self.dispatcher.get_route(task_id).last_known_state,
                CanonicalTaskState.FAILED,
            )
            with self.assertRaises(AdapterProtocolError):
                self.dispatcher.reconcile_status(task_id)
            self.assertEqual(
                self.dispatcher.get_route(task_id).last_known_state,
                CanonicalTaskState.FAILED,
            )

        self.host.result_by_handle[handle] = {"status": "done", "exit_code": 0}
        with self.assertRaises(AdapterProtocolError):
            self.dispatcher.result(task_id)
        with self.assertRaisesRegex(ValueError, "TASK_ALREADY_TERMINAL"):
            self.dispatcher.cancel(task_id)
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.last_known_state, CanonicalTaskState.FAILED)
        self.assertEqual(route.adapter_handle, handle)

    def test_terminal_truth_survives_generic_adapter_exception_on_reconcile(self) -> None:
        dispatcher, probe = self._probe_dispatcher(None)
        dispatcher.dispatch(
            probe_package("s2-m3-w1-terminal-probe"), target_executor_id="w1-probe"
        )
        probe.status_state = CanonicalTaskState.COMPLETED
        self.assertEqual(
            dispatcher.reconcile_status("s2-m3-w1-terminal-probe"),
            CanonicalTaskState.COMPLETED,
        )

        # once Core holds terminal truth, ordinary adapter failure cannot
        # regress the route: reconcile returns the preserved terminal state
        probe.raise_on_status = True
        self.assertEqual(
            dispatcher.reconcile_status("s2-m3-w1-terminal-probe"),
            CanonicalTaskState.COMPLETED,
        )
        self.assertEqual(
            dispatcher.get_route("s2-m3-w1-terminal-probe").last_known_state,
            CanonicalTaskState.COMPLETED,
        )


class W1InvalidIdentityTestCase(W1ProductionHermesTestCase):
    """CASE_5/5b: invalid task/route identity fails closed without route corruption."""

    def test_dispatch_identity_violation_commits_no_route_or_idempotency(self) -> None:
        dispatcher, probe = self._probe_dispatcher("dispatch_id")
        with self.assertRaises(AdapterProtocolError):
            dispatcher.dispatch(
                probe_package("s2-m3-w1-bad-dispatch"), target_executor_id="w1-probe"
            )
        self.assertFalse(dispatcher.has_route("s2-m3-w1-bad-dispatch"))
        self.assertFalse(dispatcher.has_route("w1-phantom-task"))
        self.assertEqual(dispatcher.list_routes(), [])
        self.assertEqual(probe.dispatch_count, 1)

    def test_result_executor_identity_violation_fails_closed_route_unchanged(self) -> None:
        dispatcher, _probe = self._probe_dispatcher("result_executor")
        dispatcher.dispatch(
            probe_package("s2-m3-w1-bad-executor"), target_executor_id="w1-probe"
        )
        with self.assertRaises(AdapterProtocolError):
            dispatcher.result("s2-m3-w1-bad-executor")
        self.assertEqual(
            dispatcher.get_route("s2-m3-w1-bad-executor").last_known_state,
            CanonicalTaskState.QUEUED,
        )

    def test_hermes_host_payload_cannot_override_core_identity(self) -> None:
        """Nearest production Hermes seam: hostile host payloads rebind nowhere."""
        task_id = "s2-m3-w1-identity-rebind"
        handle = self._dispatch(task_id).adapter_handle
        self.host.status_by_handle[handle] = {
            "status": "running",
            "canonical_task_id": "w1-phantom-task",
        }
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.canonical_task_id, task_id)

        self.host.result_by_handle[handle] = {
            "status": "done",
            "exit_code": 0,
            "canonical_task_id": "w1-phantom-task",
            "executor_id": "w1-evil-executor",
            "hermes_session": {"forbidden": True},
            "result_data": {"hermes_profile": "leak-attempt"},
        }
        result = self.dispatcher.result(task_id)
        self.assertEqual(result.canonical_task_id, task_id)
        self.assertEqual(result.executor_id, HERMES_EXECUTOR_ID)
        self.assertNotIn("hermes_session", result.result_data)
        self.assertNotIn("hermes_profile", result.result_data)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_crossed_task_handle_binding_fails_closed_without_poisoning(self) -> None:
        """CASE_5b: cross-task route/handle binding is rejected at the adapter seam."""
        task_a = "s2-m3-w1-cross-a"
        task_b = "s2-m3-w1-cross-b"
        handle_a = self._dispatch(task_a).adapter_handle
        handle_b = self._dispatch(task_b).adapter_handle
        self.host.status_by_handle[handle_a] = {"status": "done"}
        self.host.status_by_handle[handle_b] = {"status": "running"}

        before_a = self.dispatcher.get_route(task_a)
        before_b = self.dispatcher.get_route(task_b)

        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status(task_a, handle_b)
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.result(task_b, handle_a)
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status(task_a, "w1-never-bound-handle")
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")

        # rejection projected nothing: routes keep their own bindings
        self.assertEqual(self.dispatcher.get_route(task_a).adapter_handle, before_a.adapter_handle)
        self.assertEqual(self.dispatcher.get_route(task_b).adapter_handle, before_b.adapter_handle)
        self.assertEqual(
            self.dispatcher.get_route(task_a).last_known_state, before_a.last_known_state
        )
        self.assertEqual(
            self.dispatcher.get_route(task_b).last_known_state, before_b.last_known_state
        )

        # independent per-task reconciliation still returns each task's own truth
        self.assertEqual(
            self.dispatcher.status(task_a).state, CanonicalTaskState.COMPLETED
        )
        self.assertEqual(self.dispatcher.status(task_b).state, CanonicalTaskState.RUNNING)
        self.assertEqual(self.host.status_calls, [handle_a, handle_b])

    def test_ingress_wrong_executor_route_identity_rejected(self) -> None:
        task_id = "s2-m3-w1-route-executor"
        self._dispatch(task_id)
        bind_execution_dispatcher(self.dispatcher)
        try:
            mismatch = execute(
                "execution.task_status", {"task_id": task_id, "executor": "w1-not-routed"}
            )
            self.assertFalse(mismatch["ok"])
            self.assertEqual(mismatch["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")
            self.assertEqual(self.host.status_calls, [])
            route = self.dispatcher.get_route(task_id)
            self.assertEqual(route.last_known_state, CanonicalTaskState.QUEUED)
        finally:
            reset_execution_dispatcher()

    def test_never_dispatched_task_fails_closed_without_broadcast(self) -> None:
        self._dispatch("s2-m3-w1-unknown-route-live")
        with self.assertRaises(TaskNotFoundError):
            self.dispatcher.status("s2-m3-w1-never-dispatched")
        with self.assertRaises(TaskNotFoundError):
            self.dispatcher.result("s2-m3-w1-never-dispatched")
        self.assertEqual(self.host.status_calls, [])
        self.assertEqual(self.host.result_calls, [])
        self.assertEqual(len(self.dispatcher.list_routes()), 1)


class W1LostAdapterHandleTestCase(W1ProductionHermesTestCase):
    """CASE_6: adapter-side handle loss inside ONE live process (NOT restart recovery)."""

    def test_full_handle_loss_status_fails_closed_reconcile_is_uncertain(self) -> None:
        task_id = "s2-m3-w1-lost-01"
        self._dispatch(task_id)
        route_before = self.dispatcher.get_route(task_id)
        handle = route_before.adapter_handle
        # simulate the adapter-side registry losing its bounded fake handle
        # entirely (adapter forgets both sides of the binding, live process)
        self.adapter._handle_tasks.pop(handle)
        self.adapter._task_handles.pop(task_id)

        with self.assertRaises(HermesAdapterError) as ctx:
            self.dispatcher.status(task_id)
        self.assertEqual(ctx.exception.code, "TASK_HANDLE_NOT_FOUND")
        self.assertEqual(self.host.status_calls, [])

        # empirical contract: a lost handle reconciles as ordinary uncertainty
        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )
        route_after = self.dispatcher.get_route(task_id)
        self.assertEqual(route_after.canonical_task_id, route_before.canonical_task_id)
        self.assertEqual(route_after.adapter_handle, route_before.adapter_handle)
        self.assertEqual(
            route_after.dispatch_attempt_id, route_before.dispatch_attempt_id
        )
        self.assertEqual(route_after.executor_id, HERMES_EXECUTOR_ID)
        # Core never searched for an alternative handle nor queried the host
        self.assertEqual(self.host.status_calls, [])

    def test_partial_binding_inconsistency_fails_closed_on_status(self) -> None:
        task_id = "s2-m3-w1-lost-02"
        handle = self._dispatch(task_id).adapter_handle
        # adapter forgets only the handle->task side: an inconsistent binding
        self.adapter._handle_tasks.pop(handle)

        with self.assertRaises(HermesAdapterError) as ctx:
            self.dispatcher.status(task_id)
        self.assertEqual(ctx.exception.code, "ADAPTER_PROTOCOL_ERROR")
        self.assertEqual(self.host.status_calls, [])

        # FINDING W1-F02 was: reconcile_status collapsed this typed adapter
        # binding violation into UNKNOWN while dispatcher.status failed
        # closed on the identical state. S2/M3/R1 repaired reconcile_status
        # to fail closed on accepted canonical protocol codes; the route is
        # no longer mutated to UNKNOWN.
        route_before = self.dispatcher.get_route(task_id)
        with self.assertRaises(HermesAdapterError) as ctx:
            self.dispatcher.reconcile_status(task_id)
        self.assertEqual(ctx.exception.code, "ADAPTER_PROTOCOL_ERROR")
        route_after = self.dispatcher.get_route(task_id)
        self.assertNotEqual(
            route_after.last_known_state, CanonicalTaskState.UNKNOWN
        )
        self.assertEqual(route_after.last_known_state, route_before.last_known_state)
        self.assertEqual(route_after.adapter_handle, route_before.adapter_handle)
        self.assertEqual(self.host.status_calls, [])

    def test_never_bound_handle_fails_closed_on_adapter_seam(self) -> None:
        task_id = "s2-m3-w1-lost-03"
        self._dispatch(task_id)
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status("w1-never-task", "w1-never-handle")
        self.assertEqual(ctx.exception.code, "TASK_HANDLE_NOT_FOUND")


class W1MalformedUnavailableResultTestCase(W1ProductionHermesTestCase):
    """CASE_7/7b: malformed protocol data vs unavailable result semantics."""

    def test_canonical_result_type_rejects_malformed_envelopes(self) -> None:
        # type-level contract guards (real production CanonicalResult validators)
        with self.assertRaises(ValueError):
            CanonicalResult(
                ok=True,
                status="failed",
                canonical_task_id="t",
                executor_id="w1",
                canonical_task_state="FAILED",
                exit_code=None,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )
        with self.assertRaises(ValueError):
            CanonicalResult(
                ok=True,
                status="completed",
                canonical_task_id="t",
                executor_id="w1",
                canonical_task_state="RUNNING",
                exit_code=0,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )
        with self.assertRaises(ValueError):
            CanonicalResult(
                ok=False,
                status="completed",
                canonical_task_id="t",
                executor_id="w1",
                canonical_task_state="COMPLETED",
                exit_code=0,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )
        with self.assertRaises(ValueError):
            CanonicalResult(
                ok=True,
                status="unknown",
                canonical_task_id="t",
                executor_id="w1",
                canonical_task_state="UNKNOWN",
                exit_code=None,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )
        with self.assertRaises(ValueError):
            CanonicalResult.from_dict({"ok": True, "status": "completed"})

    def test_host_malformed_terminal_result_projects_result_malformed(self) -> None:
        """FINDING W1-F03 evidence (history preserved; repaired behavior).

        Pre-repair record: transient malformed projection became sticky
        terminal FAILED and poisoned the later genuine result. On the S3
        F03 repair frontier the same observation is recoverable UNKNOWN
        and the later genuine completion is accepted.
        """
        task_id = "s2-m3-w1-malformed-terminal"
        handle = self._dispatch(task_id).adapter_handle
        self.host.result_by_handle[handle] = {"status": "done", "exit_code": "not-an-integer"}
        result = self.dispatcher.result(task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "RESULT_MALFORMED")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertTrue(result.error["retryable"])
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

        # repaired consequence: a transient malformed poll stays
        # non-terminal uncertainty and the later genuine completion is
        # accepted instead of being rejected by poisoned terminal state
        self.host.result_by_handle[handle] = {"status": "done", "exit_code": 0}
        later = self.dispatcher.result(task_id)
        self.assertTrue(later.ok)
        self.assertEqual(
            later.canonical_task_state, CanonicalTaskState.COMPLETED.value
        )
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_host_non_mapping_result_projection(self) -> None:
        """FINDING W1-F03 evidence (history preserved; repaired behavior)."""
        task_id = "s2-m3-w1-malformed-nonmapping"
        handle = self._dispatch(task_id).adapter_handle
        self.host.result_by_handle[handle] = "junk-not-a-mapping"
        result = self.dispatcher.result(task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "RESULT_MALFORMED")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertTrue(result.error["retryable"])
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

    def test_host_result_transport_failure_projection(self) -> None:
        """FINDING W1-F03 companion evidence (history preserved; repaired
        behavior): transport failure during result fetch is now projected
        as a non-terminal UNKNOWN ADAPTER_PROTOCOL_ERROR envelope with the
        typed error retained and retryable=true, not a terminal FAILED.
        """
        task_id = "s2-m3-w1-malformed-transport"
        handle = self._dispatch(task_id).adapter_handle
        self.host.result_by_handle[handle] = RuntimeError("connection reset")
        result = self.dispatcher.result(task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "ADAPTER_PROTOCOL_ERROR")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertTrue(result.error["retryable"])
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

    def test_unavailable_result_is_distinct_from_malformed_protocol(self) -> None:
        """CASE_7b: unreachable/no-terminal-result semantics stay non-terminal."""
        task_unreachable = "s2-m3-w1-unreachable-result"
        task_running = "s2-m3-w1-active-result"
        handle_u = self._dispatch(task_unreachable).adapter_handle
        handle_r = self._dispatch(task_running).adapter_handle

        # runtime unreachable: UNKNOWN envelope, retryable, non-terminal
        self.host.result_by_handle[handle_u] = {
            "status": "unreachable",
            "error": {"message": "host unreachable"},
        }
        unreachable = self.dispatcher.result(task_unreachable)
        self.assertFalse(unreachable.ok)
        self.assertEqual(unreachable.status, "unknown")
        self.assertEqual(unreachable.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertEqual(unreachable.error["code"], "TASK_STATE_UNKNOWN")
        self.assertTrue(unreachable.error["retryable"])
        route_u = self.dispatcher.get_route(task_unreachable)
        self.assertFalse(route_u.last_known_state.is_terminal)
        self.assertEqual(route_u.last_known_state, CanonicalTaskState.UNKNOWN)

        # result not yet available (accepted M2 baseline, not reopened):
        # TASK_STILL_RUNNING with a coherent non-terminal route
        self.host.status_by_handle[handle_r] = {"status": "running"}
        self.dispatcher.status(task_running)
        self.host.result_by_handle[handle_r] = {
            "status": "running",
            "stdout_summary": "partial work",
        }
        active = self.dispatcher.result(task_running)
        self.assertFalse(active.ok)
        self.assertEqual(active.error["code"], "TASK_STILL_RUNNING")
        self.assertTrue(active.error["retryable"])
        self.assertEqual(active.canonical_task_state, CanonicalTaskState.RUNNING.value)
        route_r = self.dispatcher.get_route(task_running)
        self.assertEqual(route_r.last_known_state, CanonicalTaskState.RUNNING)
        self.assertFalse(route_r.last_known_state.is_terminal)

        # the unavailable paths never fabricate the terminal truth that the
        # malformed projection (above) adopts, and carry distinct error codes
        self.assertNotEqual(unreachable.error["code"], "RESULT_MALFORMED")
        self.assertNotEqual(active.error["code"], "RESULT_MALFORMED")
        self.assertFalse(route_u.last_known_state.is_terminal)
        self.assertFalse(route_r.last_known_state.is_terminal)

    def test_probe_result_identity_violation_does_not_mutate_route(self) -> None:
        dispatcher, _probe = self._probe_dispatcher("result_id")
        dispatcher.dispatch(
            probe_package("s2-m3-w1-result-id"), target_executor_id="w1-probe"
        )
        with self.assertRaises(AdapterProtocolError):
            dispatcher.result("s2-m3-w1-result-id")
        self.assertEqual(
            dispatcher.get_route("s2-m3-w1-result-id").last_known_state,
            CanonicalTaskState.QUEUED,
        )


class W1DurabilityBoundaryTestCase(unittest.TestCase):
    """D0 boundary guards: no unsupported durability may be claimed (SPEC 5, 15)."""

    def test_route_public_state_omits_adapter_binding(self) -> None:
        host = AdversarialHermesHost()
        dispatcher = create_production_execution_dispatcher(host_client=host)
        dispatcher.dispatch(w1_package("s2-m3-w1-d0-route"), target_executor_id=HERMES_EXECUTOR_ID)
        route = dispatcher.get_route("s2-m3-w1-d0-route")
        public = route.to_dict()
        self.assertNotIn("_adapter", public)
        self.assertNotIn("adapter", public)
        json_text = route.to_json()
        self.assertNotIn("ExecutorAdapter", json_text)
        self.assertNotIn(HERMES_EXECUTOR_ID + "_host_adapter", json_text)


if __name__ == "__main__":
    unittest.main()
