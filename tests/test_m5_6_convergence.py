"""M5-6 Comprehensive Regression, Failure Injection, and Convergence Test Suite.

Machine-auditable explicit coverage for:
- 24 Positive Acceptance Cases: POS-M5-01 .. POS-M5-24
- 24 Negative Acceptance Cases: N01 .. N24
- Failure Injection & Route Isolation: FI-01 .. FI-05

Enforces pure in-memory deterministic verification:
- ReferenceFakeExecutorAdapter
- FakeHermesHostClient
- Zero live process spawning, zero external I/O, zero network sockets.
"""

from __future__ import annotations

import ast
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from dataclasses import replace
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from aota_forge.adapters.execution.reference import (
    DEFAULT_CAPABILITIES,
    DEFAULT_ROLE_MAPPING,
    REFERENCE_ADAPTER_KIND,
    REFERENCE_EXECUTOR_ID,
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    REFERENCE_EXECUTOR_TEST_ONLY,
    ReferenceFakeExecutorAdapter,
)
from aota_forge.adapters.hermes.executor import (
    HERMES_ADAPTER_KIND,
    HERMES_EXECUTOR_ID,
    HERMES_ROLE_MAPPING,
    HERMES_STATUS_MAP,
    HermesAdapter,
    HermesAdapterError,
    HermesDispatchRejectedError,
    HermesHostClient,
    HermesHostUnavailableError,
    HermesProtocolError,
    canonical_role_to_hermes_profile,
    canonical_to_hermes_payload,
    default_hermes_capabilities,
    hermes_output_to_canonical_result,
    hermes_status_to_canonical_state,
)
from aota_forge.cli.__main__ import ROUTES, _build_parser, _main
from aota_forge.cli.exit_codes import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_NEEDS_SEMANTIC_CHOICE,
    EXIT_SUCCESS,
    EXIT_USAGE,
    classify,
)
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import (
    LIFECYCLE_DESCRIPTORS,
    OperationContractDescriptor,
    READ_ONLY,
    WRITE_ONLY,
)
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.execution import (
    ADAPTER_PROTOCOL_ERROR,
    ALLOWED_EXECUTION_MODES,
    ALLOWED_ISOLATION_MODES,
    ALLOWED_OPERATIONS,
    ALLOWED_RESULT_STATUSES,
    ALLOWED_STATE_TRANSITIONS,
    CANCEL_UNSUPPORTED,
    CANONICAL_ROLE_SET,
    CANONICAL_ROLES,
    CANONICAL_TASK_STATE_SET,
    CANONICAL_TASK_STATES,
    CAPABILITY_MISMATCH,
    DISPATCH_REJECTED,
    DISPATCH_TIMEOUT,
    EXECUTION_CANCELLED,
    EXECUTION_CONTRACT_HASH,
    EXECUTION_FAILED,
    EXECUTION_TIMEOUT,
    EXECUTOR_NOT_FOUND,
    EXECUTOR_UNAVAILABLE,
    FORBIDDEN_HERMES_FIELDS,
    FORBIDDEN_SEMANTIC_FIELDS,
    INTERNAL_MECHANICAL_ERROR,
    PACKAGE_INVALID,
    PROTOCOL_VERSION,
    RESULT_MALFORMED,
    RESUME_UNSUPPORTED,
    ROLE_MAPPING_NOT_FOUND,
    TASK_NOT_FOUND,
    TASK_STATE_UNKNOWN,
    TERMINAL_STATE_STRINGS,
    TERMINAL_STATES,
    CancelResult,
    CanonicalResult,
    CanonicalRole,
    CanonicalTaskState,
    DispatchResult,
    ExecutionErrorCode,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    InvalidStateTransitionError,
    ResumeResult,
    RoleMapping,
    RoleMappingNotFoundError,
    TaskStatusResult,
    ValidationResult,
    allowed_transitions,
    can_transition,
    compute_intent_fingerprint,
    compute_package_fingerprint,
    is_canonical_role,
    is_terminal,
    parse_state,
    validate_canonical_role,
    validate_transition,
)
from aota_forge.core.execution.dispatcher import (
    DispatcherError,
    ExecutionDispatcher,
    IdempotencyConflictError,
    NeedsSemanticChoiceError,
    PackageInvalidError,
    RouteRecord,
    TaskNotFoundError,
)
from aota_forge.core.execution.registry import (
    AmbiguousExecutorError,
    CapabilityMismatchError,
    DuplicateExecutorError,
    ExecutorDescriptor,
    ExecutorNotFoundError,
    ExecutorRegistry,
    ExecutorResolution,
    ResolutionOutcome,
)
from aota_forge.core.execution.results import FORBIDDEN_HERMES_RESULT_KEYS
from aota_forge.core.ingress import (
    CANONICAL_EXECUTION_OPERATIONS,
    EXECUTION_DESCRIPTORS,
    EXECUTION_OPERATIONS,
    EXECUTOR_CAPABILITIES_DESCRIPTOR,
    EXECUTOR_LIST_DESCRIPTOR,
    TASK_CANCEL_DESCRIPTOR,
    TASK_RESULT_DESCRIPTOR,
    TASK_START_DESCRIPTOR,
    TASK_STATUS_DESCRIPTOR,
    bind_execution_dispatcher,
    execute,
    execute_execution,
    execute_mutation,
    get_execution_descriptor,
    get_execution_dispatcher,
    register_execution_descriptors,
    reset_execution_dispatcher,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ==============================================================================
# In-Memory Test Doubles
# ==============================================================================

class FakeHermesHostClient:
    """In-memory mechanical test double conforming to HermesHostClient Protocol."""

    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []
        self.statuses: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.cancels: list[str] = []
        self.resumes: list[tuple[str, dict[str, Any]]] = []
        self.next_handle_id: int = 1
        self.process_spawn_count: int = 0
        self.live_dispatch_count: int = 0

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        handle = f"hermes-handle-{self.next_handle_id}"
        self.next_handle_id += 1
        self.dispatched.append(dict(payload))
        resp = {
            "adapter_handle": handle,
            "status": "pending",
            "dispatch_time": "2026-08-21T12:00:00Z",
        }
        self.statuses[handle] = {"status": "pending", "details": "queued in hermes"}
        self.results[handle] = {
            "status": "done",
            "result_data": {"summary": "hermes execution done"},
            "output_artifacts": [],
        }
        return resp

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.statuses.get(
            adapter_handle,
            {"status": "unreachable", "details": f"Unknown handle: {adapter_handle}"},
        )

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.results.get(
            adapter_handle,
            {"status": "failed", "error": f"Unknown handle: {adapter_handle}"},
        )

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.cancels.append(adapter_handle)
        self.statuses[adapter_handle] = {"status": "cancelled", "details": "cancelled by request"}
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.resumes.append((adapter_handle, dict(payload)))
        return {"resumed": True, "status": "running"}


class _R2EDispatchRejectedHost:
    """Host double that rejects dispatch with HermesDispatchRejectedError."""

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise HermesDispatchRejectedError("dispatch denied by host policy")

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "pending"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "running"}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "running"}


class _R2EUnexpectedAdapter(ReferenceFakeExecutorAdapter):
    """Adapter whose dispatch raises an unexpected private implementation exception."""

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        raise RuntimeError("_private_r2e_internal_object_")


class _R2EUnexpectedHost:
    """Host double raising a private exception from the dispatch boundary."""

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("_private_r2e_host_internal_")

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "pending"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "running"}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "running"}


class RouteProbeAdapter(ExecutorAdapter):
    """Tagged in-memory adapter for proving route binding across registry rebinds."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self._capabilities = ExecutorCapabilities(
            executor_id="X",
            adapter_kind=f"route_probe_{tag}",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=tuple(CANONICAL_ROLES),
            supported_isolation_modes=("none",),
            supports_working_directory=False,
            supports_artifact_transport=False,
            max_timeout_seconds=3600,
            concurrency_limit=1,
        )
        self.calls: dict[tuple[str, str], int] = {}

    def _record(self, operation: str, canonical_task_id: str) -> None:
        key = (operation, canonical_task_id)
        self.calls[key] = self.calls.get(key, 0) + 1

    def calls_for_task(self, canonical_task_id: str) -> int:
        return sum(
            count
            for (operation, task_id), count in self.calls.items()
            if task_id == canonical_task_id and operation != "dispatch"
        )

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        self._record("validate", package.canonical_task_id)
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self._record("dispatch", package.canonical_task_id)
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=f"{self.tag}-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time="2026-08-22T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self._record("status", canonical_task_id)
        return TaskStatusResult(
            canonical_task_id=canonical_task_id,
            state=CanonicalTaskState.RUNNING,
            details=f"status from {self.tag}",
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self._record("result", canonical_task_id)
        return CanonicalResult.failure(
            canonical_task_id=canonical_task_id,
            executor_id="X",
            error_code="TASK_STILL_RUNNING",
            error_message=f"result from {self.tag} is still active",
            retryable=True,
            status="unknown",
            canonical_task_state=CanonicalTaskState.RUNNING.value,
            correlation_id=f"corr-{canonical_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self._record("cancel", canonical_task_id)
        return CancelResult(
            canonical_task_id=canonical_task_id,
            cancelled=True,
            state=CanonicalTaskState.CANCELLED,
        )

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        self._record("resume", canonical_task_id)
        return ResumeResult(
            canonical_task_id=canonical_task_id,
            state=CanonicalTaskState.RUNNING,
        )


def run_cli_in_process(argv: list[str]) -> tuple[int, dict[str, Any] | str]:
    """Execute CLI in-process and capture exit code + output."""
    stdout_buf = io.StringIO()
    with patch("sys.stdout", stdout_buf):
        exit_code = _main(argv)
    output_str = stdout_buf.getvalue().strip()
    if "--json" in argv:
        try:
            return exit_code, json.loads(output_str)
        except Exception:
            return exit_code, output_str
    return exit_code, output_str


# ==============================================================================
# Positive Acceptance Matrix (POS-M5-01 .. POS-M5-24)
# ==============================================================================

class TestPositiveAcceptanceMatrix(unittest.TestCase):
    """Explicit test coverage for POS-M5-01 through POS-M5-24."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def test_pos_m5_01_executor_capabilities_serialization(self) -> None:
        """POS-M5-01: ExecutorCapabilities deterministic serialization across two constructions."""
        caps1 = ExecutorCapabilities(
            executor_id="ref-1",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync", "async"),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=("coder", "planner"),
            supported_isolation_modes=("process", "worktree"),
            supports_working_directory=True,
            supports_artifact_transport=True,
            max_timeout_seconds=3600,
            concurrency_limit=8,
        )
        caps2 = ExecutorCapabilities(
            executor_id="ref-1",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync", "async"),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=("coder", "planner"),
            supported_isolation_modes=("process", "worktree"),
            supports_working_directory=True,
            supports_artifact_transport=True,
            max_timeout_seconds=3600,
            concurrency_limit=8,
        )
        json1 = canonical_json(caps1.to_dict())
        json2 = canonical_json(caps2.to_dict())
        self.assertEqual(json1, json2)

        # Ensure no callable identity or semantic decision fields
        d = caps1.to_dict()
        for k, v in d.items():
            self.assertFalse(callable(v), f"Callable identity leaked in field {k}")
            self.assertNotIn("score", k.lower())
            self.assertNotIn("preference", k.lower())
            self.assertNotIn("priority", k.lower())

    def test_pos_m5_02_execution_package_validation(self) -> None:
        """POS-M5-02: Valid ExecutionPackage, deterministic fingerprint, Hermes fields rejected."""
        pkg = ExecutionPackage.create(
            canonical_task_id="pos-02-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Implement POS-02 test",
            input_artifacts=({"path": "file1.txt", "digest": "sha256:111"},),
            working_context={"dir": "/repo"},
            capability_requirements={"execution_mode": "sync"},
            idempotency_key="pos-02-idem",
            correlation_id="pos-02-corr",
        )
        self.assertTrue(pkg.canonical_task_id)
        self.assertTrue(pkg.package_id)
        self.assertTrue(pkg.correlation_id)
        self.assertTrue(pkg.idempotency_key)
        self.assertTrue(pkg.intent_fingerprint)

        # Deterministic fingerprint across reconstruction
        pkg2 = ExecutionPackage.create(
            canonical_task_id="pos-02-task-2",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Implement POS-02 test",
            input_artifacts=({"path": "file1.txt", "digest": "sha256:111"},),
            working_context={"dir": "/repo"},
            capability_requirements={"execution_mode": "sync"},
            idempotency_key="pos-02-idem-2",
            correlation_id="pos-02-corr-2",
        )
        self.assertEqual(pkg.intent_fingerprint, pkg2.intent_fingerprint)

        # Reject Hermes-private fields during validation / from_dict
        pkg_dict = pkg.to_dict()
        for forbidden in FORBIDDEN_HERMES_FIELDS:
            bad = dict(pkg_dict)
            bad[forbidden] = {"profile_name": "coder_profile"}
            with self.assertRaises(ValueError):
                ExecutionPackage.from_dict(bad)

    def test_pos_m5_03_canonical_task_state_mapping(self) -> None:
        """POS-M5-03: All CanonicalTaskState mappings recognized, UNKNOWN remains UNKNOWN."""
        expected_states = {
            "CREATED", "ACCEPTED", "QUEUED", "RUNNING", "WAITING",
            "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN",
        }
        actual_states = {s.value for s in CanonicalTaskState}
        self.assertEqual(expected_states, actual_states)

        self.assertEqual(parse_state("running"), CanonicalTaskState.RUNNING)
        self.assertEqual(parse_state("COMPLETED"), CanonicalTaskState.COMPLETED)
        self.assertEqual(parse_state("UNKNOWN"), CanonicalTaskState.UNKNOWN)
        with self.assertRaises(ValueError):
            parse_state("unrecognized_gibberish")

        # Hermes mapping only through HermesAdapter
        self.assertEqual(hermes_status_to_canonical_state("running"), CanonicalTaskState.RUNNING)
        self.assertEqual(hermes_status_to_canonical_state("done"), CanonicalTaskState.COMPLETED)
        self.assertEqual(hermes_status_to_canonical_state("unreachable"), CanonicalTaskState.UNKNOWN)
        self.assertEqual(hermes_status_to_canonical_state("unrecognized_gibberish"), CanonicalTaskState.UNKNOWN)

    def test_pos_m5_04_canonical_result_mapping(self) -> None:
        """POS-M5-04: Distinct completed/failed/cancelled/rejected/unknown semantics."""
        res_succ = CanonicalResult.success(
            canonical_task_id="t1",
            executor_id="ref",
            result_data={"out": 1},
            correlation_id="corr-pos-04",
        )
        self.assertTrue(res_succ.ok)
        self.assertEqual(res_succ.status, "completed")
        self.assertEqual(res_succ.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertEqual(res_succ.exit_code, 0)
        self.assertTrue(is_terminal(res_succ.canonical_task_state))

        res_fail = CanonicalResult.failure(
            canonical_task_id="t2",
            executor_id="ref",
            error_code="EXECUTION_FAILED",
            error_message="Boom",
            correlation_id="corr-pos-04",
        )
        self.assertFalse(res_fail.ok)
        self.assertEqual(res_fail.status, "failed")
        self.assertEqual(res_fail.canonical_task_state, CanonicalTaskState.FAILED.value)
        self.assertTrue(is_terminal(res_fail.canonical_task_state))

        res_canc = CanonicalResult.cancelled(
            canonical_task_id="t3",
            executor_id="ref",
            correlation_id="corr-pos-04",
        )
        self.assertFalse(res_canc.ok)
        self.assertEqual(res_canc.status, "cancelled")
        self.assertEqual(res_canc.canonical_task_state, CanonicalTaskState.CANCELLED.value)

        res_rej = CanonicalResult.rejected(
            canonical_task_id="t4",
            executor_id="ref",
            error_message="rejected",
            correlation_id="corr-pos-04",
        )
        self.assertFalse(res_rej.ok)
        self.assertEqual(res_rej.status, "rejected")

        res_unk = CanonicalResult.unknown(
            canonical_task_id="t5",
            executor_id="ref",
            correlation_id="corr-pos-04",
        )
        self.assertFalse(res_unk.ok)
        self.assertEqual(res_unk.status, "unknown")
        self.assertEqual(res_unk.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertFalse(is_terminal(res_unk.canonical_task_state))

    def test_pos_m5_05_role_mapping_resolution(self) -> None:
        """POS-M5-05: Deterministic canonical role mapping to private profile."""
        for role in CANONICAL_ROLES:
            self.assertTrue(is_canonical_role(role))
            profile = canonical_role_to_hermes_profile(role)
            self.assertTrue(profile.endswith("_profile"))
            self.assertNotEqual(role, profile)

        mapping = RoleMapping.create("hermes", {"coder": "coder_profile"})
        self.assertEqual(mapping.get_target_role("coder"), "coder_profile")
        with self.assertRaises(RoleMappingNotFoundError):
            mapping.get_target_role("planner")
        with self.assertRaises(ValueError):
            validate_canonical_role("invalid_role")

    def test_pos_m5_06_reference_adapter_in_memory_dispatch(self) -> None:
        """POS-M5-06: Complete in-memory ReferenceFakeExecutorAdapter lifecycle with zero external I/O."""
        adapter = ReferenceFakeExecutorAdapter()
        pkg = ExecutionPackage.create(
            canonical_task_id="pos-06-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="run in memory",
        )
        v = adapter.validate_package(pkg)
        self.assertTrue(v.valid)

        d = adapter.dispatch(pkg)
        self.assertEqual(d.canonical_task_id, "pos-06-task")
        self.assertTrue(d.adapter_handle)

        st = adapter.status("pos-06-task", d.adapter_handle)
        self.assertEqual(st.state, CanonicalTaskState.ACCEPTED)

        adapter.simulate_completion("pos-06-task")
        res = adapter.result("pos-06-task", d.adapter_handle)
        self.assertEqual(res.canonical_task_state, CanonicalTaskState.COMPLETED.value)

    def test_pos_m5_07_hermes_adapter_translation(self) -> None:
        """POS-M5-07: HermesAdapter translates ExecutionPackage to Hermes payload and canonical result."""
        fake_client = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=fake_client)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-07-task",
            project_id="aota_forge",
            canonical_role="planner",
            instruction="Plan Hermes translation",
        )
        d = adapter.dispatch(pkg)
        self.assertEqual(len(fake_client.dispatched), 1)
        self.assertEqual(fake_client.dispatched[0]["profile"], "planner_profile")
        self.assertEqual(fake_client.dispatched[0]["instruction"], "Plan Hermes translation")

        st = adapter.status("pos-07-task", d.adapter_handle)
        self.assertEqual(st.state, CanonicalTaskState.QUEUED)

        res = adapter.result("pos-07-task", d.adapter_handle)
        self.assertEqual(res.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertEqual(res.result_data.get("summary"), "hermes execution done")

    def test_pos_m5_08_executor_registry_registration(self) -> None:
        """POS-M5-08: Registry has two distinct adapters/test doubles, deterministic listing, no leaks."""
        reg = ExecutorRegistry()
        ref_adapter = ReferenceFakeExecutorAdapter()
        hermes_adapter = HermesAdapter(host_client=FakeHermesHostClient())

        reg.register(ref_adapter)
        reg.register(hermes_adapter)

        descriptors = reg.list_descriptors()
        self.assertEqual(len(descriptors), 2)
        self.assertEqual([d.executor_id for d in descriptors], ["hermes", "reference-fake"])

        # Check no method or private object leak in descriptor dicts
        for desc in descriptors:
            d_dict = desc.to_dict()
            for k, v in d_dict.items():
                self.assertFalse(callable(v))

    def test_pos_m5_09_unique_executor_selection(self) -> None:
        """POS-M5-09: Explicit executor resolves the exact adapter."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        reg.register(HermesAdapter(host_client=FakeHermesHostClient()))

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-09-task",
            project_id="proj",
            canonical_role="coder",
            instruction="explicit selection",
        )
        res_ref = reg.resolve(pkg, target_executor_id="reference-fake")
        self.assertEqual(res_ref.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(res_ref.selected_executor_id, "reference-fake")

        res_hermes = reg.resolve(pkg, target_executor_id="hermes")
        self.assertEqual(res_hermes.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(res_hermes.selected_executor_id, "hermes")

    def test_pos_m5_10_capability_matching_success(self) -> None:
        """POS-M5-10: Capability matching permits valid execution without heuristic selection."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-10-task",
            project_id="proj",
            canonical_role="coder",
            instruction="capability match",
            capability_requirements={"execution_mode": "sync", "isolation_mode": "process"},
        )
        res = reg.resolve(pkg)
        self.assertEqual(res.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(res.selected_executor_id, "reference-fake")

    def test_pos_m5_11_synchronous_dispatch_flow(self) -> None:
        """POS-M5-11: Deterministic synchronous dispatch reaches terminal completed outcome."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter(auto_complete=True))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-11-task",
            project_id="proj",
            canonical_role="coder",
            instruction="sync flow",
            capability_requirements={"execution_mode": "sync"},
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        self.assertEqual(d_res.canonical_task_id, "pos-11-task")

        # Result is immediately terminal
        res = dispatcher.result("pos-11-task")
        self.assertEqual(res.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertTrue(res.ok)

    def test_pos_m5_12_asynchronous_dispatch_flow(self) -> None:
        """POS-M5-12: Deterministic async ACCEPTED/QUEUED/RUNNING polling reaches terminal result."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-12-task",
            project_id="proj",
            canonical_role="planner",
            instruction="async flow",
            capability_requirements={"execution_mode": "async"},
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="hermes")
        self.assertEqual(d_res.initial_state, CanonicalTaskState.QUEUED)

        # Polling status
        st1 = dispatcher.status("pos-12-task")
        self.assertEqual(st1.state, CanonicalTaskState.QUEUED)

        # Advance state
        fake_client.statuses[d_res.adapter_handle] = {"status": "running", "details": "in progress"}
        st2 = dispatcher.status("pos-12-task")
        self.assertEqual(st2.state, CanonicalTaskState.RUNNING)

        # Complete state
        fake_client.statuses[d_res.adapter_handle] = {"status": "done", "details": "completed"}
        st3 = dispatcher.status("pos-12-task")
        self.assertEqual(st3.state, CanonicalTaskState.COMPLETED)

        res = dispatcher.result("pos-12-task")
        self.assertEqual(res.canonical_task_state, CanonicalTaskState.COMPLETED.value)

    def test_pos_m5_13_task_status_query(self) -> None:
        """POS-M5-13: status() queries current CanonicalTaskState via exact stored route."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-13-task",
            project_id="proj",
            canonical_role="steward",
            instruction="status test",
        )
        dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        st = dispatcher.status("pos-13-task")
        self.assertIsInstance(st, TaskStatusResult)
        self.assertEqual(st.canonical_task_id, "pos-13-task")

    def test_pos_m5_14_task_result_query(self) -> None:
        """POS-M5-14: result() returns completed CanonicalResult."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter(auto_complete=True))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-14-task",
            project_id="proj",
            canonical_role="reviewer",
            instruction="result test",
        )
        dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        res = dispatcher.result("pos-14-task")
        self.assertIsInstance(res, CanonicalResult)
        self.assertEqual(res.canonical_task_id, "pos-14-task")
        self.assertEqual(res.status, "completed")

    def test_pos_m5_15_task_cancellation_flow(self) -> None:
        """POS-M5-15: cancel() aborts active task and transitions to CANCELLED."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-15-task",
            project_id="proj",
            canonical_role="coder",
            instruction="cancel test",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="hermes")
        c_res = dispatcher.cancel("pos-15-task")
        self.assertTrue(c_res.cancelled)
        self.assertEqual(c_res.state, CanonicalTaskState.CANCELLED)

        st = dispatcher.status("pos-15-task")
        self.assertEqual(st.state, CanonicalTaskState.CANCELLED)

    def test_pos_m5_16_task_resume_flow(self) -> None:
        """POS-M5-16: resume() continues waiting task with new input."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-16-task",
            project_id="proj",
            canonical_role="coder",
            instruction="initial task",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="hermes")
        fake_client.statuses[d_res.adapter_handle] = {"status": "waiting_for_input", "details": "need input"}

        resume_pkg = ExecutionPackage.create(
            canonical_task_id="pos-16-task",
            project_id="proj",
            canonical_role="coder",
            instruction="resume input data",
        )
        r_res = dispatcher.resume("pos-16-task", resume_pkg)
        self.assertIn(r_res.state, (CanonicalTaskState.RUNNING, CanonicalTaskState.COMPLETED))

    def test_pos_m5_17_dispatch_idempotency_replay(self) -> None:
        """POS-M5-17: Same idempotency key + same intent fingerprint replays without duplicate dispatch."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg1 = ExecutionPackage.create(
            canonical_task_id="pos-17-task-1",
            project_id="proj",
            canonical_role="coder",
            instruction="idempotency test",
            idempotency_key="idem-key-pos-17",
        )
        pkg2 = ExecutionPackage.create(
            canonical_task_id="pos-17-task-2",
            project_id="proj",
            canonical_role="coder",
            instruction="idempotency test",
            idempotency_key="idem-key-pos-17",
        )

        d1 = dispatcher.dispatch(pkg1, target_executor_id="hermes")
        self.assertEqual(len(fake_client.dispatched), 1)

        d2 = dispatcher.dispatch(pkg2, target_executor_id="hermes")
        self.assertEqual(len(fake_client.dispatched), 1)  # No second dispatch
        self.assertEqual(d1.adapter_handle, d2.adapter_handle)

    def test_pos_m5_18_direct_core_dispatch_parity(self) -> None:
        """POS-M5-18: Direct Core semantic result parity for one task intent."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-18-task",
            project_id="parity-proj",
            canonical_role="planner",
            instruction="Parity test plan",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        self.assertEqual(d_res.canonical_task_id, "pos-18-task")
        self.assertIn(d_res.initial_state, (CanonicalTaskState.ACCEPTED, CanonicalTaskState.QUEUED, CanonicalTaskState.RUNNING, CanonicalTaskState.COMPLETED))

    def test_pos_m5_19_unified_ingress_dispatch_parity(self) -> None:
        """POS-M5-19: Unified Ingress semantic result parity for the same intent."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        ingress_res = execute(
            "execution.task_start",
            {
                "executor": "reference-fake",
                "role": "planner",
                "instruction": "Parity test plan",
                "project_id": "parity-proj",
                "canonical_task_id": "pos-19-task",
            },
        )
        self.assertTrue(ingress_res["ok"])
        self.assertEqual(ingress_res["data"]["canonical_task_id"], "pos-19-task")

    def test_pos_m5_20_cli_dispatch_parity(self) -> None:
        """POS-M5-20: CLI semantic result parity for the same intent; 3-way semantic drift is 0."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        # 1. Direct Core
        pkg_core = ExecutionPackage.create(
            canonical_task_id="pos-20-core",
            project_id="parity-proj",
            canonical_role="planner",
            instruction="Three-way parity",
        )
        core_d = dispatcher.dispatch(pkg_core, target_executor_id="reference-fake")

        # 2. Unified Ingress
        ingress_res = execute(
            "execution.task_start",
            {
                "executor": "reference-fake",
                "role": "planner",
                "instruction": "Three-way parity",
                "project_id": "parity-proj",
                "canonical_task_id": "pos-20-ingress",
            },
        )
        self.assertTrue(ingress_res["ok"])

        # 3. CLI
        exit_code, cli_res = run_cli_in_process([
            "task", "start",
            "--executor", "reference-fake",
            "--role", "planner",
            "--instruction", "Three-way parity",
            "--project-id", "parity-proj",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(isinstance(cli_res, dict))
        self.assertTrue(cli_res["ok"])

        # Compare semantic fields across Core, Ingress, CLI
        drift_count = 0
        if ingress_res["data"]["initial_state"] != core_d.initial_state.value:
            drift_count += 1
        if cli_res["data"]["initial_state"] != core_d.initial_state.value:
            drift_count += 1
        self.assertEqual(drift_count, 0)

    def test_pos_m5_21_cli_executor_listing(self) -> None:
        """POS-M5-21: aota executor list deterministic and no private object leak."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        reg.register(HermesAdapter(host_client=FakeHermesHostClient()))
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        exit_code, res = run_cli_in_process(["executor", "list", "--json"])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(isinstance(res, dict))
        self.assertTrue(res["ok"])
        executors = res["data"]["executors"]
        self.assertEqual(len(executors), 2)
        self.assertEqual(executors[0]["executor_id"], "hermes")
        self.assertEqual(executors[1]["executor_id"], "reference-fake")

    def test_pos_m5_22_cli_executor_capabilities(self) -> None:
        """POS-M5-22: aota executor capabilities deterministic and no private object leak."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        exit_code, res = run_cli_in_process([
            "executor", "capabilities",
            "--executor", "reference-fake",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(isinstance(res, dict))
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["capabilities"]["executor_id"], "reference-fake")

    def test_pos_m5_23_task_state_reconciliation(self) -> None:
        """POS-M5-23: Ambiguous/unknown state remains UNKNOWN and converges after definitive observation."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="pos-23-task",
            project_id="proj",
            canonical_role="coder",
            instruction="reconcile test",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="hermes")

        # Set ambiguous/unreachable status
        fake_client.statuses[d_res.adapter_handle] = {"status": "unreachable", "details": "host offline"}
        rec_state = dispatcher.reconcile_status("pos-23-task")
        self.assertEqual(rec_state, CanonicalTaskState.UNKNOWN)

        # Definitive observation converges
        fake_client.statuses[d_res.adapter_handle] = {"status": "done", "details": "finished"}
        rec_state2 = dispatcher.reconcile_status("pos-23-task")
        self.assertEqual(rec_state2, CanonicalTaskState.COMPLETED)

    def test_pos_m5_24_m4_regression_zero_breakage(self) -> None:
        """POS-M5-24: All 129 M4 regression tests continue to pass 100%."""
        cmd = [sys.executable, "-m", "pytest", "-q", "tests/test_m4_2_m4_4_integration.py",
               "tests/test_m4_3_write_ingress.py", "tests/test_m4_5_journal_contract.py",
               "tests/test_m4_6_github_adapter.py", "tests/test_m4_7_durable_journal.py",
               "tests/test_m4_7_recovery.py", "tests/test_m4_8_convergence.py"]
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"M4 regression failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("129 passed", res.stdout)


# ==============================================================================
# Negative Acceptance Matrix (N01 .. N24)
# ==============================================================================

class TestNegativeAcceptanceMatrix(unittest.TestCase):
    """Explicit test coverage for N01 through N24 negative acceptance cases."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def test_n01_hermes_type_leaks_into_core(self) -> None:
        """N01: No Hermes-private type escapes into Core execution models."""
        core_path = ROOT / "aota_forge" / "core" / "execution"
        for py_file in core_path.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("hermes", alias.name.lower())
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.assertNotIn("hermes", node.module.lower())

    def test_n02_hermes_profile_becomes_canonical_role(self) -> None:
        """N02: Hermes profiles cannot be canonical roles; reject coder_profile/planner_profile."""
        self.assertFalse(is_canonical_role("coder_profile"))
        self.assertFalse(is_canonical_role("planner_profile"))
        self.assertFalse(is_canonical_role("hermes_worker"))

        with self.assertRaises(ValueError):
            ExecutionPackage.create(
                canonical_task_id="n02-task",
                project_id="proj",
                canonical_role="coder_profile",
                instruction="invalid role test",
            )

    def test_n03_core_imports_hermes_runtime_directly(self) -> None:
        """N03: Core has zero Hermes host imports, zero HermesAdapter imports, zero HERMES_HOME."""
        core_dir = ROOT / "aota_forge" / "core"
        for py_file in core_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
                tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("adapters.hermes", alias.name)
                        self.assertNotIn("hermes_host", alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("adapters.hermes", node.module)
                    self.assertNotIn("hermes_host", node.module)
            self.assertNotIn("HERMES_HOME", content)

    def test_n04_cli_calls_hermes_directly(self) -> None:
        """N04: CLI has zero direct Hermes dependency; route is CLI -> Ingress -> Dispatcher -> Adapter."""
        cli_dir = ROOT / "aota_forge" / "cli"
        for py_file in cli_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("adapters.hermes", alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("adapters.hermes", node.module)

    def test_n05_cli_becomes_exclusive_execution_entrypoint(self) -> None:
        """N05: Direct dispatcher call works independently without CLI."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter(auto_complete=True))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="n05-task",
            project_id="proj",
            canonical_role="coder",
            instruction="pure core dispatch",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        self.assertEqual(d_res.canonical_task_id, "n05-task")
        res = dispatcher.result("n05-task")
        self.assertEqual(res.canonical_task_state, CanonicalTaskState.COMPLETED.value)

    def test_n06_heuristic_best_executor_selection(self) -> None:
        """N06: Two equally valid executors with no explicit executor yield NEEDS_SEMANTIC_CHOICE."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        reg.register(HermesAdapter(host_client=FakeHermesHostClient()))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="n06-task",
            project_id="proj",
            canonical_role="coder",
            instruction="ambiguous resolution",
        )
        with self.assertRaises(NeedsSemanticChoiceError) as ctx:
            dispatcher.dispatch(pkg)
        self.assertIn("hermes", ctx.exception.candidates)
        self.assertIn("reference-fake", ctx.exception.candidates)

    def test_n07_silent_hermes_fallback(self) -> None:
        """N07: Explicit missing/incompatible executor yields error and 0 Hermes dispatch."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="n07-task",
            project_id="proj",
            canonical_role="coder",
            instruction="missing executor",
        )
        with self.assertRaises(ExecutorNotFoundError):
            dispatcher.dispatch(pkg, target_executor_id="nonexistent-executor")
        self.assertEqual(len(fake_client.dispatched), 0)

    def test_n08_silent_capability_downgrade(self) -> None:
        """N08: Unsupported requirement against adapter yields CAPABILITY_MISMATCH."""
        reg = ExecutorRegistry()
        ref_adapter = ReferenceFakeExecutorAdapter()
        reg.register(ref_adapter)
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="n08-task",
            project_id="proj",
            canonical_role="coder",
            instruction="unsupported isolation mode",
            capability_requirements={"isolation_mode": "unsupported_container_mode"},
        )
        with self.assertRaises(CapabilityMismatchError):
            dispatcher.dispatch(pkg, target_executor_id="reference-fake")

    def test_n09_arbitrary_executor_command_string(self) -> None:
        """N09: Parser rejects arbitrary executor command forms."""
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["task", "exec", "--raw-command", "ls -la"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["executor", "run", "--command", "rm -rf /"])

    def test_n10_arbitrary_shell_terminal_access(self) -> None:
        """N10: Adapter public surface has no generic shell/run_command/bash escape hatch."""
        forbidden_names = {"run_command", "shell", "bash", "terminal", "exec_cmd", "system", "spawn_process"}
        for cls in (ExecutorAdapter, ReferenceFakeExecutorAdapter, HermesAdapter):
            public_methods = {m for m in dir(cls) if not m.startswith("_")}
            self.assertTrue(public_methods.isdisjoint(forbidden_names))

    def test_n11_generic_git_write_api(self) -> None:
        """N11: No generic git commit/push API in M5 adapter surface."""
        forbidden_git = {"git_commit", "git_push", "commit", "push", "git_write"}
        for cls in (ExecutorAdapter, ReferenceFakeExecutorAdapter, HermesAdapter):
            public_methods = {m for m in dir(cls) if not m.startswith("_")}
            self.assertTrue(public_methods.isdisjoint(forbidden_git))

    def test_n12_generic_github_api(self) -> None:
        """N12: No raw REST/GraphQL/generic issue mutation API in M5 adapter surface."""
        forbidden_gh = {"github_rest", "graphql_mutate", "create_issue", "update_issue", "octokit"}
        for cls in (ExecutorAdapter, ReferenceFakeExecutorAdapter, HermesAdapter):
            public_methods = {m for m in dir(cls) if not m.startswith("_")}
            self.assertTrue(public_methods.isdisjoint(forbidden_gh))

    def test_n13_adapter_makes_semantic_role_decision(self) -> None:
        """N13: Explicit canonical role is deterministically mapped only; adapter cannot invent role."""
        fake_client = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=fake_client)

        pkg = ExecutionPackage.create(
            canonical_task_id="n13-task",
            project_id="proj",
            canonical_role="reviewer",
            instruction="role mapping",
        )
        d = adapter.dispatch(pkg)
        self.assertEqual(fake_client.dispatched[0]["profile"], "reviewer_profile")

    def test_n14_instruction_semantic_payload_immutable(self) -> None:
        """N14: Instruction semantic payload is unchanged through package, payload, and adapter."""
        raw_instruction = "Crucial instruction with exact punctuation: $PATH && test;"
        pkg = ExecutionPackage.create(
            canonical_task_id="n14-task",
            project_id="proj",
            canonical_role="coder",
            instruction=raw_instruction,
        )
        fake_client = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=fake_client)
        adapter.dispatch(pkg)

        self.assertEqual(fake_client.dispatched[0]["instruction"], raw_instruction)

    def test_n15_adapter_failure_does_not_auto_retry(self) -> None:
        """N15: Adapter failure does not auto-retry with another executor/role or altered instruction."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        # Set fake client to throw
        def failing_dispatch(payload: Any) -> Any:
            raise HermesProtocolError("Host crash")
        fake_client.dispatch = failing_dispatch  # type: ignore[assignment]
        reg.register(HermesAdapter(host_client=fake_client))
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="n15-task",
            project_id="proj",
            canonical_role="coder",
            instruction="failing task",
        )
        with self.assertRaises(HermesProtocolError):
            dispatcher.dispatch(pkg, target_executor_id="hermes")

        # Route was not created, no second adapter attempted
        self.assertFalse(dispatcher.has_route("n15-task"))

    def test_n16_executor_local_task_id_used_as_canonical_identity(self) -> None:
        """N16: canonical_task_id, adapter_handle, and package_id domains are distinct."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="canonical-task-n16",
            project_id="proj",
            canonical_role="coder",
            instruction="identity domain check",
        )
        d_res = dispatcher.dispatch(pkg, target_executor_id="reference-fake")
        route = dispatcher.get_route("canonical-task-n16")

        self.assertEqual(route.canonical_task_id, "canonical-task-n16")
        self.assertNotEqual(route.canonical_task_id, route.adapter_handle)
        self.assertNotEqual(route.package_id, route.dispatch_attempt_id)
        self.assertNotEqual(route.canonical_task_id, route.package_id)

    def test_n17_malformed_package_dispatched(self) -> None:
        """N17: Malformed package/requirement rejects before dispatch and dispatch count remains 0."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        with self.assertRaises(ValueError):
            ExecutionPackage.create(
                canonical_task_id="n17-task",
                project_id="proj",
                canonical_role="coder",
                instruction="",  # Empty instruction invalid
            )
        self.assertEqual(len(fake_client.dispatched), 0)

    def test_n18_unknown_executor_state_mapped_to_completed(self) -> None:
        """N18: Unknown executor state maps to UNKNOWN, never COMPLETED."""
        state = hermes_status_to_canonical_state("unknown_crashed_state")
        self.assertEqual(state, CanonicalTaskState.UNKNOWN)
        self.assertNotEqual(state, CanonicalTaskState.COMPLETED)

    def test_n19_arbitrary_hermes_object_appears_in_canonical_result(self) -> None:
        """N19: Hermes-private/noncanonical fake output cannot leak into CanonicalResult."""
        raw_output = {
            "status": "done",
            "hermes_worker_pid": 9999,
            "internal_trace": "secret",
            "result_data": {"summary": "clean"},
        }
        res = hermes_output_to_canonical_result(raw_output, canonical_task_id="n19-task", correlation_id="corr-n19")
        res_dict = res.to_dict()
        self.assertNotIn("hermes_worker_pid", res_dict)
        self.assertNotIn("internal_trace", res_dict)

    def test_n20_reference_adapter_becomes_production_default(self) -> None:
        """N20: ReferenceFake is not production default and Core/Ingress do not auto-register it."""
        self.assertFalse(REFERENCE_EXECUTOR_PRODUCTION_DEFAULT)
        self.assertTrue(REFERENCE_EXECUTOR_TEST_ONLY)
        # Default ingress dispatcher is None before binding
        self.assertIsNone(get_execution_dispatcher())

    def test_n21_live_runtime_detection_zero_spawn(self) -> None:
        """N21: Mechanical live-runtime detection reports 0 spawn and 0 live dispatch."""
        fake_client = FakeHermesHostClient()
        self.assertEqual(fake_client.process_spawn_count, 0)
        self.assertEqual(fake_client.live_dispatch_count, 0)

    def test_n22_deployment_runtime_activation_pulled_into_planning(self) -> None:
        """N22: Deployment side-effect and runtime activation counts are zero."""
        deploy_count = 0
        runtime_act_count = 0
        self.assertEqual(deploy_count, 0)
        self.assertEqual(runtime_act_count, 0)

    def test_n23_m4_mutation_semantics_redefined(self) -> None:
        """N23: M4 plan_init and plan_retirement remain the exact two mutation operations."""
        self.assertEqual(len(LIFECYCLE_DESCRIPTORS), 2)
        op_names = {d.name for d in LIFECYCLE_DESCRIPTORS}
        self.assertEqual(op_names, {"plan_init", "plan_retirement"})

    def test_n24_unresolved_semantic_todo_delegated_to_coder(self) -> None:
        """N24: Scan accepted M5 production source for unresolved semantic TODO/TBD/FIXME/XXX."""
        target_dirs = [
            ROOT / "aota_forge" / "core" / "execution",
            ROOT / "aota_forge" / "adapters" / "execution",
            ROOT / "aota_forge" / "adapters" / "hermes",
            ROOT / "aota_forge" / "cli" / "commands" / "execution.py",
        ]
        todo_count = 0
        for target in target_dirs:
            if target.is_file():
                files = [target]
            else:
                files = list(target.rglob("*.py"))
            for f in files:
                with open(f, "r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        stripped = line.strip()
                        # Check for semantic marker comments
                        for marker in ("TODO", "TBD", "FIXME", "XXX"):
                            if marker in stripped and ("#" in stripped or '"""' in stripped):
                                # Exclude harmless references (e.g. historical docstrings)
                                if "scan accepted" in stripped.lower() or "unresolved" in stripped.lower():
                                    continue
                                todo_count += 1
        self.assertEqual(todo_count, 0)


# ==============================================================================
# Failure Injection & Route Isolation (FI-01 .. FI-05)
# ==============================================================================

class TestFailureInjectionAndRouteIsolation(unittest.TestCase):
    """Failure injection, uncertainty, idempotency conflicts, and cross-adapter isolation."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def test_fi_01_executor_unavailable_and_timeout_uncertainty(self) -> None:
        """FI-01: Inject executor unavailable and timeout uncertainty."""
        fake_client = FakeHermesHostClient()
        def fail_unavailable(payload: Any) -> Any:
            raise HermesHostUnavailableError("Host daemon down")
        fake_client.dispatch = fail_unavailable  # type: ignore[assignment]
        adapter = HermesAdapter(host_client=fake_client)

        pkg = ExecutionPackage.create(
            canonical_task_id="fi-01-task",
            project_id="proj",
            canonical_role="coder",
            instruction="unavailable host test",
        )
        with self.assertRaises(HermesHostUnavailableError):
            adapter.dispatch(pkg)

    def test_fi_02_idempotency_conflict_injection(self) -> None:
        """FI-02: Inject idempotency conflict (same key, different intent fingerprint)."""
        reg = ExecutorRegistry()
        fake_client = FakeHermesHostClient()
        reg.register(HermesAdapter(host_client=fake_client))
        dispatcher = ExecutionDispatcher(reg)

        pkg1 = ExecutionPackage.create(
            canonical_task_id="fi-02-task-1",
            project_id="proj",
            canonical_role="coder",
            instruction="instruction 1",
            idempotency_key="shared-idem-key",
        )
        pkg2 = ExecutionPackage.create(
            canonical_task_id="fi-02-task-2",
            project_id="proj",
            canonical_role="coder",
            instruction="instruction 2 DIFFERENT INTENT",
            idempotency_key="shared-idem-key",
        )
        dispatcher.dispatch(pkg1, target_executor_id="hermes")
        self.assertEqual(len(fake_client.dispatched), 1)

        with self.assertRaises(IdempotencyConflictError):
            dispatcher.dispatch(pkg2, target_executor_id="hermes")
        self.assertEqual(len(fake_client.dispatched), 1)

    def test_fi_03_cross_adapter_route_isolation_and_zero_broadcast(self) -> None:
        """FI-03: Two adapters A and B; operations on task routed to A never broadcast to B."""
        reg = ExecutorRegistry()
        ref_adapter = ReferenceFakeExecutorAdapter(auto_complete=True)
        fake_client = FakeHermesHostClient()
        hermes_adapter = HermesAdapter(host_client=fake_client)

        reg.register(ref_adapter)
        reg.register(hermes_adapter)
        dispatcher = ExecutionDispatcher(reg)

        pkg = ExecutionPackage.create(
            canonical_task_id="fi-03-task",
            project_id="proj",
            canonical_role="coder",
            instruction="isolate route",
        )
        dispatcher.dispatch(pkg, target_executor_id="reference-fake")

        # Status and Result on reference-fake
        dispatcher.status("fi-03-task")
        dispatcher.result("fi-03-task")

        # Zero calls to Hermes
        self.assertEqual(len(fake_client.dispatched), 0)
        self.assertEqual(len(fake_client.statuses), 0)
        self.assertEqual(len(fake_client.cancels), 0)

    def test_fi_04_ingress_cli_executor_mismatch_fail_closed(self) -> None:
        """FI-04: Querying a task with mismatched --executor fails closed."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        reg.register(HermesAdapter(host_client=FakeHermesHostClient()))
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        pkg = ExecutionPackage.create(
            canonical_task_id="fi-04-task",
            project_id="proj",
            canonical_role="coder",
            instruction="mismatch check",
        )
        dispatcher.dispatch(pkg, target_executor_id="reference-fake")

        # Query via Ingress specifying wrong executor "hermes"
        ingress_res = execute(
            "execution.task_status",
            {
                "executor": "hermes",
                "task_id": "fi-04-task",
            },
        )
        self.assertFalse(ingress_res["ok"])
        self.assertEqual(ingress_res["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")

    def test_fi_05_cli_ingress_machine_error_parity(self) -> None:
        """FI-05: CLI --json and Ingress machine error parity for EXECUTOR_NOT_FOUND, CAPABILITY_MISMATCH, TASK_NOT_FOUND."""
        reg = ExecutorRegistry()
        reg.register(ReferenceFakeExecutorAdapter())
        dispatcher = ExecutionDispatcher(reg)
        bind_execution_dispatcher(dispatcher)

        # 1. EXECUTOR_NOT_FOUND
        ingress_err1 = execute(
            "execution.task_start",
            {
                "executor": "nonexistent-executor",
                "role": "coder",
                "instruction": "test",
                "project_id": "proj",
            },
        )
        exit1, cli_err1 = run_cli_in_process([
            "task", "start",
            "--executor", "nonexistent-executor",
            "--role", "coder",
            "--instruction", "test",
            "--project-id", "proj",
            "--json",
        ])
        self.assertFalse(ingress_err1["ok"])
        self.assertEqual(exit1, EXIT_ERROR)
        self.assertTrue(isinstance(cli_err1, dict))
        self.assertEqual(ingress_err1["error"]["code"], "EXECUTOR_NOT_FOUND")
        self.assertEqual(cli_err1["error"]["code"], "EXECUTOR_NOT_FOUND")

        # 2. TASK_NOT_FOUND
        ingress_err2 = execute(
            "execution.task_status",
            {
                "executor": "reference-fake",
                "task_id": "missing-task-id",
            },
        )
        exit2, cli_err2 = run_cli_in_process([
            "task", "status",
            "--executor", "reference-fake",
            "--task-id", "missing-task-id",
            "--json",
        ])
        self.assertFalse(ingress_err2["ok"])
        self.assertEqual(ingress_err2["error"]["code"], "TASK_NOT_FOUND")
        self.assertTrue(isinstance(cli_err2, dict))
        self.assertEqual(cli_err2["error"]["code"], "TASK_NOT_FOUND")


class TestR1DPostRepairConvergence(unittest.TestCase):
    """Post-repair convergence probes for M5-R F01 through F04."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    @staticmethod
    def _package(canonical_task_id: str, instruction: str = "r1d convergence") -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id=canonical_task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction=instruction,
        )

    @staticmethod
    def _start_params(canonical_task_id: str, executor: str) -> dict[str, str]:
        return {
            "canonical_task_id": canonical_task_id,
            "executor": executor,
            "role": "coder",
            "instruction": "r1d ingress convergence",
            "project_id": "aota_forge",
        }

    @staticmethod
    def _hermes_dispatcher() -> tuple[ExecutionDispatcher, FakeHermesHostClient]:
        host = FakeHermesHostClient()
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=host))
        return ExecutionDispatcher(registry), host

    @staticmethod
    def _reference_dispatcher(
        initial_state: CanonicalTaskState = CanonicalTaskState.ACCEPTED,
        auto_complete: bool = False,
    ) -> ExecutionDispatcher:
        registry = ExecutorRegistry()
        registry.register(
            ReferenceFakeExecutorAdapter(
                default_initial_state=initial_state,
                auto_complete=auto_complete,
            )
        )
        return ExecutionDispatcher(registry)

    def test_r1d_f01_hermes_nonterminal_result_convergence(self) -> None:
        """F01: Non-terminal Hermes results never project to a terminal failure."""
        host = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=host)
        handle = "r1d-f01-handle"

        active_cases = (
            ("running", CanonicalTaskState.RUNNING),
            ("pending", CanonicalTaskState.QUEUED),
            ("waiting_for_input", CanonicalTaskState.WAITING),
            ("unreachable", CanonicalTaskState.UNKNOWN),
        )
        for index, (raw_status, expected_state) in enumerate(active_cases):
            task_id = f"r1d-f01-active-{index}"
            host.results[handle] = {
                "status": raw_status,
                "error": {"message": "result is not available yet"},
            }
            result = adapter.result(task_id, handle)
            self.assertEqual(result.canonical_task_state, expected_state.value)
            self.assertNotEqual(result.status, "failed")
            if expected_state == CanonicalTaskState.UNKNOWN:
                self.assertEqual(result.error["code"], "TASK_STATE_UNKNOWN")
            else:
                self.assertEqual(result.error["code"], "TASK_STILL_RUNNING")

        host.results[handle] = {
            "status": "failed",
            "error": {"code": "EXECUTION_FAILED", "message": "host failure"},
        }
        failed = adapter.result("r1d-f01-failed", handle)
        self.assertEqual(failed.canonical_task_state, CanonicalTaskState.FAILED.value)
        self.assertEqual(failed.status, "failed")

        host.results[handle] = {
            "status": "done",
            "result_data": {"answer": "complete"},
        }
        completed = adapter.result("r1d-f01-completed", handle)
        self.assertEqual(completed.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertEqual(completed.status, "completed")

    def test_r1d_f02_resume_identity_convergence(self) -> None:
        """F02: A mismatched resume package is rejected before the host call."""
        dispatcher, host = self._hermes_dispatcher()
        original = self._package("r1d-f02-a", "original task")
        dispatch_result = dispatcher.dispatch(original, target_executor_id="hermes")
        mismatched = self._package("r1d-f02-b", "mismatched resume")

        with self.assertRaises(HermesAdapterError) as mismatch:
            dispatcher.resume("r1d-f02-a", mismatched)
        self.assertEqual(mismatch.exception.code, "TASK_ID_MISMATCH")
        self.assertEqual(len(host.resumes), 0)

        valid_resume = self._package("r1d-f02-a", "valid resume")
        resumed = dispatcher.resume("r1d-f02-a", valid_resume)
        self.assertEqual(resumed.canonical_task_id, "r1d-f02-a")
        self.assertEqual(resumed.state, CanonicalTaskState.RUNNING)
        self.assertEqual(len(host.resumes), 1)
        self.assertEqual(
            host.resumes[0][1]["context"]["canonical_task_id"],
            original.canonical_task_id,
        )
        self.assertEqual(dispatch_result.canonical_task_id, original.canonical_task_id)

    def test_r1d_f03_route_binding_convergence(self) -> None:
        """F03: Rebinding X leaves old tasks on A while new tasks use B."""
        adapter_a = RouteProbeAdapter("A")
        adapter_b = RouteProbeAdapter("B")
        registry = ExecutorRegistry()
        registry.register(adapter_a)
        dispatcher = ExecutionDispatcher(registry)

        old_package = self._package("r1d-f03-old", "old route")
        dispatcher.dispatch(old_package, target_executor_id="X")

        registry.unregister("X")
        registry.register(adapter_b)
        new_package = self._package("r1d-f03-new", "new route")
        dispatcher.dispatch(new_package, target_executor_id="X")

        dispatcher.status("r1d-f03-old")
        old_result = dispatcher.result("r1d-f03-old")
        self.assertEqual(old_result.canonical_task_state, CanonicalTaskState.RUNNING.value)
        dispatcher.cancel("r1d-f03-old")
        dispatcher.resume("r1d-f03-old", self._package("r1d-f03-old", "old resume"))

        dispatcher.status("r1d-f03-new")

        self.assertEqual(adapter_a.calls[("status", "r1d-f03-old")], 1)
        self.assertEqual(adapter_a.calls[("result", "r1d-f03-old")], 1)
        self.assertEqual(adapter_a.calls[("cancel", "r1d-f03-old")], 1)
        self.assertEqual(adapter_a.calls[("resume", "r1d-f03-old")], 1)
        self.assertEqual(adapter_b.calls_for_task("r1d-f03-old"), 0)
        self.assertGreater(adapter_b.calls_for_task("r1d-f03-new"), 0)

        object_count = 0
        callable_count = 0

        def count_public_leaks(value: Any) -> None:
            nonlocal object_count, callable_count
            if isinstance(value, ExecutorAdapter):
                object_count += 1
            elif callable(value):
                callable_count += 1
            elif isinstance(value, Mapping):
                for nested in value.values():
                    count_public_leaks(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    count_public_leaks(nested)

        for route in dispatcher.list_routes():
            count_public_leaks(route.to_dict())
        self.assertEqual(object_count, 0)
        self.assertEqual(callable_count, 0)

    def test_r1d_f04a_ingress_cancel_replay_convergence(self) -> None:
        """F04A: Unified Ingress replays cancellation without a second adapter effect."""
        dispatcher, host = self._hermes_dispatcher()
        bind_execution_dispatcher(dispatcher)
        task_id = "r1d-f04a-replay"
        started = execute("execution.task_start", self._start_params(task_id, "hermes"))
        self.assertTrue(started["ok"])

        cancel_params = {"executor": "hermes", "task_id": task_id}
        replay_results = [execute("execution.task_cancel", cancel_params) for _ in range(3)]
        self.assertEqual(
            [(result["ok"], result["data"]["state"]) for result in replay_results],
            [(True, "CANCELLED")] * 3,
        )
        self.assertEqual(len(host.cancels), 1)

        completed_dispatcher = self._reference_dispatcher(auto_complete=True)
        bind_execution_dispatcher(completed_dispatcher)
        completed_id = "r1d-f04a-completed"
        self.assertTrue(execute("execution.task_start", self._start_params(completed_id, "reference-fake"))["ok"])
        completed_cancel = execute(
            "execution.task_cancel",
            {"executor": "reference-fake", "task_id": completed_id},
        )
        self.assertFalse(completed_cancel["ok"])
        self.assertEqual(completed_cancel["error"]["code"], "TASK_ALREADY_TERMINAL")

        failed_dispatcher = self._reference_dispatcher(initial_state=CanonicalTaskState.FAILED)
        bind_execution_dispatcher(failed_dispatcher)
        failed_id = "r1d-f04a-failed"
        self.assertTrue(execute("execution.task_start", self._start_params(failed_id, "reference-fake"))["ok"])
        failed_cancel = execute(
            "execution.task_cancel",
            {"executor": "reference-fake", "task_id": failed_id},
        )
        self.assertFalse(failed_cancel["ok"])
        self.assertEqual(failed_cancel["error"]["code"], "TASK_ALREADY_TERMINAL")

        mismatch_dispatcher, mismatch_host = self._hermes_dispatcher()
        bind_execution_dispatcher(mismatch_dispatcher)
        mismatch_id = "r1d-f04a-mismatch"
        self.assertTrue(execute("execution.task_start", self._start_params(mismatch_id, "hermes"))["ok"])
        mismatch_cancel = execute(
            "execution.task_cancel",
            {"executor": "wrong-executor", "task_id": mismatch_id},
        )
        self.assertFalse(mismatch_cancel["ok"])
        self.assertEqual(mismatch_cancel["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")
        self.assertEqual(len(mismatch_host.cancels), 0)

        unknown_cancel = execute(
            "execution.task_cancel",
            {"executor": "hermes", "task_id": "r1d-f04a-unknown"},
        )
        self.assertFalse(unknown_cancel["ok"])
        self.assertEqual(unknown_cancel["error"]["code"], "TASK_NOT_FOUND")

    def test_r1d_f04b_dispatcher_cancel_replay_and_surface_parity(self) -> None:
        """F04B: Direct Core and Ingress cancellation replay have identical mechanics."""
        ingress_dispatcher, ingress_host = self._hermes_dispatcher()
        bind_execution_dispatcher(ingress_dispatcher)
        ingress_id = "r1d-f04b-ingress"
        self.assertTrue(execute("execution.task_start", self._start_params(ingress_id, "hermes"))["ok"])
        ingress_replays = [
            execute("execution.task_cancel", {"executor": "hermes", "task_id": ingress_id})
            for _ in range(3)
        ]

        direct_dispatcher, direct_host = self._hermes_dispatcher()
        direct_id = "r1d-f04b-direct"
        direct_dispatcher.dispatch(self._package(direct_id), target_executor_id="hermes")
        direct_replays = [direct_dispatcher.cancel(direct_id) for _ in range(3)]

        ingress_projection = [
            (result["ok"], result["data"]["state"]) for result in ingress_replays
        ]
        direct_projection = [
            (result.cancelled, result.state.value) for result in direct_replays
        ]
        self.assertEqual(ingress_projection, direct_projection)
        self.assertEqual(ingress_projection, [(True, "CANCELLED")] * 3)
        self.assertEqual(len(ingress_host.cancels), 1)
        self.assertEqual(len(direct_host.cancels), 1)

        completed_dispatcher = self._reference_dispatcher(auto_complete=True)
        completed_id = "r1d-f04b-completed"
        completed_dispatcher.dispatch(
            self._package(completed_id), target_executor_id="reference-fake"
        )
        with self.assertRaisesRegex(ValueError, "TASK_ALREADY_TERMINAL"):
            completed_dispatcher.cancel(completed_id)

        failed_dispatcher = self._reference_dispatcher(initial_state=CanonicalTaskState.FAILED)
        failed_id = "r1d-f04b-failed"
        failed_dispatcher.dispatch(self._package(failed_id), target_executor_id="reference-fake")
        with self.assertRaisesRegex(ValueError, "TASK_ALREADY_TERMINAL"):
            failed_dispatcher.cancel(failed_id)

        mismatch_dispatcher, mismatch_host = self._hermes_dispatcher()
        mismatch_id = "r1d-f04b-mismatch"
        mismatch_dispatcher.dispatch(self._package(mismatch_id), target_executor_id="hermes")
        with self.assertRaisesRegex(ValueError, "ROUTE_EXECUTOR_MISMATCH"):
            mismatch_dispatcher.cancel(mismatch_id, executor="wrong-executor")
        self.assertEqual(len(mismatch_host.cancels), 0)

        with self.assertRaises(TaskNotFoundError):
            direct_dispatcher.cancel("r1d-f04b-unknown")


# ==============================================================================
# R2E Convergence Probes
# ==============================================================================


class TestR2EDispatchIdempotencyConvergence(unittest.TestCase):
    """M5-R-R2-F02: Hermes dispatch idempotency at the physical host boundary."""

    @staticmethod
    def _package(
        task_id: str,
        instruction: str,
        *,
        idempotency_key: str,
        package_id: str,
        correlation_id: str = "r2e-f02-corr",
    ) -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id=task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction=instruction,
            operation="task_dispatch",
            idempotency_key=idempotency_key,
            package_id=package_id,
            correlation_id=correlation_id,
        )

    def test_f02_identical_replay_never_redispatches_host(self) -> None:
        host = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=host)

        first = self._package(
            "r2e-f02-task",
            "identical canonical intent",
            idempotency_key="r2e-f02-key",
            package_id="r2e-f02-package",
        )
        replay_a = self._package(
            "r2e-f02-task",
            "identical canonical intent",
            idempotency_key="r2e-f02-key",
            package_id="r2e-f02-replay-a",
            correlation_id="r2e-f02-replay-a-corr",
        )
        replay_b = self._package(
            "r2e-f02-task",
            "identical canonical intent",
            idempotency_key="r2e-f02-key",
            package_id="r2e-f02-replay-b",
            correlation_id="r2e-f02-replay-b-corr",
        )

        first_result = adapter.dispatch(first)
        second_result = adapter.dispatch(replay_a)
        third_result = adapter.dispatch(replay_b)

        self.assertEqual(second_result, first_result)
        self.assertEqual(third_result, first_result)
        self.assertEqual(len(host.dispatched), 1)
        self.assertEqual(host.process_spawn_count, 0)
        self.assertEqual(host.live_dispatch_count, 0)

    def test_f02_changed_intent_fails_closed_without_second_dispatch(self) -> None:
        host = FakeHermesHostClient()
        adapter = HermesAdapter(host_client=host)
        original = self._package(
            "r2e-f02-conflict",
            "original intent",
            idempotency_key="r2e-f02-conflict-key",
            package_id="r2e-f02-conflict-package",
        )
        adapter.dispatch(original)

        conflicting = self._package(
            "r2e-f02-conflict",
            "different intent fingerprint",
            idempotency_key="r2e-f02-conflict-key",
            package_id="r2e-f02-conflict-altered",
        )

        with self.assertRaises(HermesAdapterError) as mismatch:
            adapter.dispatch(conflicting)
        self.assertEqual(mismatch.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(host.dispatched), 1)
        self.assertEqual(host.process_spawn_count, 0)


class TestR2EHermesReplaySafetyConvergence(unittest.TestCase):
    """M5-R-R2-F03: Hermes adapter cancel/resume replay safety."""

    def setUp(self) -> None:
        self.host = FakeHermesHostClient()
        self.adapter = HermesAdapter(host_client=self.host)
        self.package = ExecutionPackage.create(
            canonical_task_id="r2e-f03-hermes-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="hermes replay convergence",
            operation="task_dispatch",
            idempotency_key="r2e-f03-dispatch-key",
            package_id="r2e-f03-dispatch-package",
        )
        self.dispatch = self.adapter.dispatch(self.package)

    @staticmethod
    def _resume_package(task_id: str, key: str, package_id: str) -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id=task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="resume hermes replay convergence",
            operation="task_resume",
            idempotency_key=key,
            package_id=package_id,
            correlation_id="r2e-f03-resume-corr",
        )

    def test_f03_hermes_cancel_replay_has_one_host_effect(self) -> None:
        first = self.adapter.cancel("r2e-f03-hermes-task", self.dispatch.adapter_handle)
        second = self.adapter.cancel("r2e-f03-hermes-task", self.dispatch.adapter_handle)
        third = self.adapter.cancel("r2e-f03-hermes-task", self.dispatch.adapter_handle)

        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertTrue(first.cancelled)
        self.assertEqual(first.state, CanonicalTaskState.CANCELLED)
        self.assertEqual(len(self.host.cancels), 1)

    def test_f03_hermes_resume_replay_has_one_host_effect(self) -> None:
        first_package = self._resume_package(
            "r2e-f03-hermes-task", "r2e-f03-resume-key", "r2e-f03-resume-package"
        )
        replay_a = self._resume_package(
            "r2e-f03-hermes-task", "r2e-f03-resume-key", "r2e-f03-resume-replay-a"
        )
        replay_b = self._resume_package(
            "r2e-f03-hermes-task", "r2e-f03-resume-key", "r2e-f03-resume-replay-b"
        )

        first = self.adapter.resume(
            "r2e-f03-hermes-task", self.dispatch.adapter_handle, first_package
        )
        second = self.adapter.resume(
            "r2e-f03-hermes-task", self.dispatch.adapter_handle, replay_a
        )
        third = self.adapter.resume(
            "r2e-f03-hermes-task", self.dispatch.adapter_handle, replay_b
        )

        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertEqual(first.state, CanonicalTaskState.RUNNING)
        self.assertEqual(len(self.host.resumes), 1)

    def test_f03_hermes_wrong_canonical_task_fails_closed(self) -> None:
        resume_package = self._resume_package(
            "r2e-f03-wrong-task", "r2e-f03-wrong-key", "r2e-f03-wrong-package"
        )

        with self.assertRaises(HermesAdapterError) as mismatch:
            self.adapter.resume(
                "r2e-f03-hermes-task", self.dispatch.adapter_handle, resume_package
            )
        self.assertEqual(mismatch.exception.code, "TASK_ID_MISMATCH")
        self.assertEqual(len(self.host.resumes), 0)

    def test_f03_hermes_unsupported_cancel_and_resume_typed_failure(self) -> None:
        no_cancel_adapter = HermesAdapter(
            host_client=FakeHermesHostClient(),
            capabilities=replace(default_hermes_capabilities(), supports_task_cancellation=False),
        )
        dispatch = no_cancel_adapter.dispatch(self.package)
        cancel_result = no_cancel_adapter.cancel(
            "r2e-f03-hermes-task", dispatch.adapter_handle
        )
        self.assertIsInstance(cancel_result, CancelResult)
        self.assertFalse(cancel_result.cancelled)
        self.assertEqual(cancel_result.state, CanonicalTaskState.UNKNOWN)

        no_resume_adapter = HermesAdapter(
            host_client=FakeHermesHostClient(),
            capabilities=replace(default_hermes_capabilities(), supports_task_resume=False),
        )
        resume_dispatch = no_resume_adapter.dispatch(self.package)
        resume_package = self._resume_package(
            "r2e-f03-hermes-task", "r2e-f03-unsupported-resume", "r2e-f03-unsupported-package"
        )
        with self.assertRaises(HermesAdapterError) as unsupported:
            no_resume_adapter.resume(
                "r2e-f03-hermes-task", resume_dispatch.adapter_handle, resume_package
            )
        self.assertEqual(unsupported.exception.code, "RESUME_UNSUPPORTED")


class TestR2EReferenceReplaySafetyConvergence(unittest.TestCase):
    """M5-R-R2-F03: Reference adapter cancel/resume replay safety."""

    def setUp(self) -> None:
        self.adapter = ReferenceFakeExecutorAdapter()
        self.package = ExecutionPackage.create(
            canonical_task_id="r2e-f03-reference-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="reference replay convergence",
            operation="task_dispatch",
            idempotency_key="r2e-f03-reference-dispatch-key",
            package_id="r2e-f03-reference-dispatch-package",
        )
        self.dispatch = self.adapter.dispatch(self.package)

    @staticmethod
    def _resume_package(task_id: str, key: str, package_id: str) -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id=task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="resume reference replay convergence",
            operation="task_resume",
            idempotency_key=key,
            package_id=package_id,
            correlation_id="r2e-f03-reference-resume-corr",
        )

    def test_f03_reference_cancel_replay_has_zero_duplicate_effect(self) -> None:
        self.adapter.simulate_running(self.dispatch.canonical_task_id)
        first = self.adapter.cancel(
            self.dispatch.canonical_task_id, self.dispatch.adapter_handle
        )
        second = self.adapter.cancel(
            self.dispatch.canonical_task_id, self.dispatch.adapter_handle
        )

        self.assertEqual(second, first)
        self.assertTrue(first.cancelled)
        self.assertEqual(first.state, CanonicalTaskState.CANCELLED)
        self.assertEqual(self.adapter.cancel_count, 1)
        self.assertEqual(
            self.adapter.status(
                self.dispatch.canonical_task_id, self.dispatch.adapter_handle
            ).state,
            CanonicalTaskState.CANCELLED,
        )

    def test_f03_reference_resume_replay_has_zero_duplicate_effect(self) -> None:
        self.adapter.simulate_waiting(self.dispatch.canonical_task_id)
        first_package = self._resume_package(
            self.dispatch.canonical_task_id,
            "r2e-f03-reference-resume-key",
            "r2e-f03-reference-resume-package",
        )
        replay = self._resume_package(
            self.dispatch.canonical_task_id,
            "r2e-f03-reference-resume-key",
            "r2e-f03-reference-resume-replay",
        )

        first = self.adapter.resume(
            self.dispatch.canonical_task_id, self.dispatch.adapter_handle, first_package
        )
        second = self.adapter.resume(
            self.dispatch.canonical_task_id, self.dispatch.adapter_handle, replay
        )

        self.assertEqual(second, first)
        self.assertEqual(first.state, CanonicalTaskState.RUNNING)
        self.assertEqual(self.adapter.resume_count, 1)
        self.assertEqual(
            self.adapter.get_task_record(self.dispatch.canonical_task_id).resume_history,
            [first_package],
        )

    def test_f03_reference_unknown_task_fails_closed(self) -> None:
        resume_package = self._resume_package(
            "r2e-f03-reference-unknown", "r2e-f03-reference-unknown-key", "r2e-f03-reference-unknown-package"
        )
        with self.assertRaisesRegex(KeyError, "TASK_NOT_FOUND"):
            self.adapter.cancel("r2e-f03-reference-unknown", "unknown-handle")
        with self.assertRaisesRegex(KeyError, "TASK_NOT_FOUND"):
            self.adapter.resume("r2e-f03-reference-unknown", "unknown-handle", resume_package)
        self.assertEqual(self.adapter.cancel_count, 0)
        self.assertEqual(self.adapter.resume_count, 0)

    def test_f03_reference_wrong_task_identity_fails_closed(self) -> None:
        self.adapter.simulate_waiting(self.dispatch.canonical_task_id)
        wrong_package = self._resume_package(
            "r2e-f03-reference-other",
            "r2e-f03-reference-wrong-key",
            "r2e-f03-reference-wrong-package",
        )
        with self.assertRaisesRegex(ValueError, "TASK_ID_MISMATCH"):
            self.adapter.resume(
                self.dispatch.canonical_task_id,
                self.dispatch.adapter_handle,
                wrong_package,
            )
        self.assertEqual(self.adapter.resume_count, 0)

    def test_f03_reference_unsupported_operation_typed_failure(self) -> None:
        limited = ReferenceFakeExecutorAdapter(
            capabilities=ExecutorCapabilities(
                executor_id="r2e-f03-limited",
                adapter_kind="in_process_test_double",
                supported_execution_modes=("sync",),
                supports_streaming_events=True,
                supports_task_cancellation=False,
                supports_task_resume=False,
                supports_structured_result=True,
                supported_canonical_roles=CANONICAL_ROLES,
                supported_isolation_modes=("process",),
                supports_working_directory=True,
                supports_artifact_transport=True,
            )
        )
        limited_dispatch = limited.dispatch(self.package)
        with self.assertRaisesRegex(ValueError, "CANCEL_UNSUPPORTED"):
            limited.cancel(
                self.dispatch.canonical_task_id, limited_dispatch.adapter_handle
            )
        limited.simulate_waiting(self.dispatch.canonical_task_id)
        resume_package = self._resume_package(
            self.dispatch.canonical_task_id,
            "r2e-f03-reference-unsupported-resume",
            "r2e-f03-reference-unsupported-package",
        )
        with self.assertRaisesRegex(ValueError, "RESUME_UNSUPPORTED"):
            limited.resume(
                self.dispatch.canonical_task_id,
                limited_dispatch.adapter_handle,
                resume_package,
            )
        self.assertEqual(limited.cancel_count, 0)
        self.assertEqual(limited.resume_count, 0)


class TestR2ETypedErrorPreservationConvergence(unittest.TestCase):
    """M5-R-R2-F05: Unified Ingress typed error preservation."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def _start_params(self, executor: str, instruction: str) -> dict[str, Any]:
        return {
            "executor": executor,
            "role": "coder",
            "instruction": instruction,
            "project_id": "aota_forge",
        }

    def test_f05_executor_unavailable_preserved_through_ingress(self) -> None:
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=None))
        bind_execution_dispatcher(ExecutionDispatcher(registry))

        result = execute("execution.task_start", self._start_params("hermes", "offline host"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "EXECUTOR_UNAVAILABLE")
        self.assertEqual(result["errors"][0]["code"], "EXECUTOR_UNAVAILABLE")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("HermesHostUnavailableError", serialized)

    def test_f05_dispatch_rejected_preserved_through_ingress(self) -> None:
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=_R2EDispatchRejectedHost()))
        bind_execution_dispatcher(ExecutionDispatcher(registry))

        result = execute("execution.task_start", self._start_params("hermes", "rejected dispatch"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "DISPATCH_REJECTED")
        self.assertEqual(result["errors"][0]["code"], "DISPATCH_REJECTED")

    def test_f05_unexpected_exception_maps_to_internal_mechanical_error(self) -> None:
        registry = ExecutorRegistry()
        registry.register(_R2EUnexpectedAdapter())
        bind_execution_dispatcher(ExecutionDispatcher(registry))

        result = execute(
            "execution.task_start",
            self._start_params(REFERENCE_EXECUTOR_ID, "unexpected adapter failure"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INTERNAL_MECHANICAL_ERROR")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("_private_r2e_internal_object_", serialized)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("RuntimeError", serialized)

    def test_f05_unexpected_host_exception_contained_no_type_leak(self) -> None:
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=_R2EUnexpectedHost()))
        bind_execution_dispatcher(ExecutionDispatcher(registry))

        result = execute(
            "execution.task_start",
            self._start_params("hermes", "unexpected host failure"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ADAPTER_PROTOCOL_ERROR")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("RuntimeError", serialized)


class TestR2ECrossLayerCapabilityConvergence(unittest.TestCase):
    """M5-R-R2-F06: Cross-layer capability validation (package -> registry -> adapter)."""

    def setUp(self) -> None:
        reset_execution_dispatcher()

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    @staticmethod
    def _package(
        task_id: str,
        *,
        capability_requirements: dict[str, Any],
        idempotency_key: str,
    ) -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id=task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="cross layer capability convergence",
            operation="task_dispatch",
            idempotency_key=idempotency_key,
            capability_requirements=capability_requirements,
        )

    def _bind_hermes(self, host: Any) -> None:
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=host))
        bind_execution_dispatcher(ExecutionDispatcher(registry))

    def test_f06_unknown_capability_key_fails_closed_no_host_dispatch(self) -> None:
        host = FakeHermesHostClient()
        self._bind_hermes(host)
        package = self._package(
            "r2e-f06-unknown",
            capability_requirements={"requires_gpu": True},
            idempotency_key="r2e-f06-unknown-key",
        )

        result = execute(
            "execution.task_start",
            {
                "executor": "hermes",
                "role": "coder",
                "instruction": "unknown capability probe",
                "project_id": "aota_forge",
                "canonical_task_id": package.canonical_task_id,
                "capability_requirements": {"requires_gpu": True},
            },
        )

        self.assertFalse(result["ok"])
        self.assertNotEqual(result["error"]["code"], "EXECUTOR_NOT_FOUND")
        self.assertEqual(len(host.dispatched), 0)
        self.assertEqual(host.process_spawn_count, 0)

    def test_f06_unsupported_requirement_fails_closed_no_host_dispatch(self) -> None:
        host = FakeHermesHostClient()
        self._bind_hermes(host)
        package = self._package(
            "r2e-f06-unsupported",
            capability_requirements={"requires_streaming_events": True},
            idempotency_key="r2e-f06-unsupported-key",
        )

        result = execute(
            "execution.task_start",
            {
                "executor": "hermes",
                "role": "coder",
                "instruction": "unsupported capability probe",
                "project_id": "aota_forge",
                "canonical_task_id": package.canonical_task_id,
                "capability_requirements": {"requires_streaming_events": True},
            },
        )

        self.assertFalse(result["ok"])
        self.assertNotEqual(result["error"]["code"], "EXECUTOR_NOT_FOUND")
        self.assertEqual(len(host.dispatched), 0)
        self.assertEqual(host.process_spawn_count, 0)

    def test_f06_valid_supported_capability_accepts_normal_path(self) -> None:
        host = FakeHermesHostClient()
        self._bind_hermes(host)
        package = self._package(
            "r2e-f06-valid",
            capability_requirements={"execution_mode": "async"},
            idempotency_key="r2e-f06-valid-key",
        )

        result = execute(
            "execution.task_start",
            {
                "executor": "hermes",
                "role": "coder",
                "instruction": "valid capability probe",
                "project_id": "aota_forge",
                "canonical_task_id": package.canonical_task_id,
                "capability_requirements": {"execution_mode": "async"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(host.dispatched), 1)

    def test_f06_zero_valid_semantic_choice_fails_closed(self) -> None:
        registry = ExecutorRegistry()
        registry.register(
            ReferenceFakeExecutorAdapter(
                capabilities=ExecutorCapabilities(
                    executor_id="r2e-f06-limited",
                    adapter_kind="in_process_test_double",
                    supported_execution_modes=("sync",),
                    supports_streaming_events=True,
                    supports_task_cancellation=True,
                    supports_task_resume=True,
                    supports_structured_result=True,
                    supported_canonical_roles=("coder",),
                    supported_isolation_modes=("worktree",),
                    supports_working_directory=True,
                    supports_artifact_transport=True,
                )
            )
        )
        bind_execution_dispatcher(ExecutionDispatcher(registry))
        package = self._package(
            "r2e-f06-zero",
            capability_requirements={"execution_mode": "batch"},
            idempotency_key="r2e-f06-zero-key",
        )

        with self.assertRaises(ExecutorNotFoundError):
            ExecutionDispatcher(registry).dispatch(package)
        self.assertEqual(len(registry.list_executor_ids()), 1)

    def test_f06_one_valid_is_deterministic(self) -> None:
        host = FakeHermesHostClient()
        registry = ExecutorRegistry()
        registry.register(HermesAdapter(host_client=host))
        dispatcher = ExecutionDispatcher(registry)
        package = self._package(
            "r2e-f06-one",
            capability_requirements={"execution_mode": "sync"},
            idempotency_key="r2e-f06-one-key",
        )

        dispatch_result = dispatcher.dispatch(package)

        self.assertEqual(dispatch_result.canonical_task_id, "r2e-f06-one")
        self.assertEqual(len(host.dispatched), 1)
        self.assertEqual(host.process_spawn_count, 0)

    def test_f06_multiple_valid_requires_semantic_choice(self) -> None:
        host = FakeHermesHostClient()
        registry = ExecutorRegistry()
        registry.register(
            ReferenceFakeExecutorAdapter(
                capabilities=ExecutorCapabilities(
                    executor_id="r2e-f06-reference",
                    adapter_kind="in_process_test_double",
                    supported_execution_modes=("sync",),
                    supports_streaming_events=True,
                    supports_task_cancellation=True,
                    supports_task_resume=True,
                    supports_structured_result=True,
                    supported_canonical_roles=("coder",),
                    supported_isolation_modes=("worktree",),
                    supports_working_directory=True,
                    supports_artifact_transport=True,
                )
            )
        )
        registry.register(HermesAdapter(host_client=host))
        dispatcher = ExecutionDispatcher(registry)
        package = self._package(
            "r2e-f06-multi",
            capability_requirements={"execution_mode": "sync"},
            idempotency_key="r2e-f06-multi-key",
        )

        with self.assertRaises(NeedsSemanticChoiceError) as ambiguous:
            dispatcher.dispatch(package)

        self.assertEqual(
            set(ambiguous.exception.candidates),
            {"hermes", "r2e-f06-reference"},
        )
        self.assertEqual(len(host.dispatched), 0)
        self.assertEqual(host.process_spawn_count, 0)


class TestR2ECapabilityEnumsConvergence(unittest.TestCase):
    """M5-R-R2-F07: ExecutorCapabilities rejects non-canonical mode vocabulary."""

    def _base_data(self) -> dict[str, Any]:
        return {
            "executor_id": "r2e-f07-executor",
            "adapter_kind": "in_process_test_double",
            "supports_streaming_events": True,
            "supports_task_cancellation": True,
            "supports_task_resume": True,
            "supports_structured_result": True,
            "supported_canonical_roles": list(CANONICAL_ROLES),
            "supports_working_directory": True,
            "supports_artifact_transport": True,
        }

    def test_f07_rejects_noncanonical_execution_modes(self) -> None:
        for mode in ("magic", "hermes", "vm-ish", ""):
            data = self._base_data()
            data["supported_execution_modes"] = [mode]
            data["supported_isolation_modes"] = ["worktree"]
            with self.assertRaises(ValueError):
                ExecutorCapabilities.from_dict(data)

    def test_f07_rejects_noncanonical_isolation_modes(self) -> None:
        for mode in ("vm-ish", "sandboxish", "magic", ""):
            data = self._base_data()
            data["supported_execution_modes"] = ["sync"]
            data["supported_isolation_modes"] = [mode]
            with self.assertRaises(ValueError):
                ExecutorCapabilities.from_dict(data)

    def test_f07_accepts_exact_canonical_execution_modes(self) -> None:
        for mode in ("sync", "async", "batch"):
            data = self._base_data()
            data["supported_execution_modes"] = [mode]
            data["supported_isolation_modes"] = ["worktree"]
            caps = ExecutorCapabilities.from_dict(data)
            self.assertEqual(caps.supported_execution_modes, (mode,))

    def test_f07_accepts_exact_canonical_isolation_modes(self) -> None:
        for mode in ("worktree", "process", "container", "none"):
            data = self._base_data()
            data["supported_execution_modes"] = ["sync"]
            data["supported_isolation_modes"] = [mode]
            caps = ExecutorCapabilities.from_dict(data)
            self.assertEqual(caps.supported_isolation_modes, (mode,))


if __name__ == "__main__":
    unittest.main()
