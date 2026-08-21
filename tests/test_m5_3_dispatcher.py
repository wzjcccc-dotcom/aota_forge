"""Comprehensive unit and contract test suite for M5-3 Executor Registry and Core Dispatcher.

Tests M5-3 components:
- ExecutorRegistry registration, duplicate failure closed, deterministic listing, public serialization
- ExecutorDescriptor zero-leak invariant (no callables, bound methods, memory addresses)
- Mechanical capability matching (modes, roles, timeouts, features, no silent downgrade)
- Mechanical executor resolution:
  * Explicit target: compatible -> RESOLVED; incompatible -> CAPABILITY_MISMATCH (no fallback); unknown -> EXECUTOR_NOT_FOUND
  * Capability-only: 0 -> EXECUTOR_NOT_FOUND; 1 -> RESOLVED; >1 -> NEEDS_SEMANTIC_CHOICE (stable candidates)
  * Dedicated ambiguity resolution and explicit override probes
- ExecutionDispatcher dispatch flow, route binding, identity domain separation
- Idempotency replay (same key + same fingerprint) and conflict (same key + changed fingerprint)
- Lifecycle operations (status, result, cancel, resume) routed strictly via stored route
- Cross-adapter route isolation (broadcast count = 0)
- Task state reconciliation (UNKNOWN preserved, never false completion)
- Negative matrix and architectural invariants:
  * Zero direct Hermes dependency (M5_3_IMPORTS_HERMES_ADAPTER=no, CORE_DIRECT_HERMES_DEPENDENCY_COUNT=0)
  * No heuristic / best executor guessing
  * No silent Hermes fallback
  * No silent capability downgrade
  * No auto-semantic retry
  * Reference adapter is not production default
  * Unresolved items / pending placeholder count zero
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import uuid
from typing import Any

import pytest

from aota_forge.adapters.execution.reference import (
    DEFAULT_CAPABILITIES,
    DEFAULT_ROLE_MAPPING,
    REFERENCE_EXECUTOR_ID,
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    ReferenceFakeExecutorAdapter,
)
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution import (
    ALLOWED_EXECUTION_MODES,
    ALLOWED_ISOLATION_MODES,
    CANONICAL_ROLES,
    CancelResult,
    CanonicalResult,
    CanonicalRole,
    CanonicalTaskState,
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    ResumeResult,
    RoleMapping,
    TaskStatusResult,
    ValidationResult,
    is_terminal,
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


# ==============================================================================
# Helper Factories & Test Doubles
# ==============================================================================

def make_adapter(
    executor_id: str,
    supported_roles: tuple[str, ...] = CANONICAL_ROLES,
    supported_modes: tuple[str, ...] = ("async", "sync"),
    supported_isolation: tuple[str, ...] = ("process", "worktree"),
    supports_cancellation: bool = True,
    supports_resume: bool = True,
    supports_structured_result: bool = True,
    supports_streaming_events: bool = True,
    supports_working_directory: bool = True,
    supports_artifact_transport: bool = True,
    max_timeout_seconds: int | None = 3600,
    concurrency_limit: int | None = 8,
) -> ReferenceFakeExecutorAdapter:
    """Create a configured ReferenceFakeExecutorAdapter double with a custom executor_id."""
    caps = ExecutorCapabilities(
        executor_id=executor_id,
        adapter_kind="in_process_test_double",
        supported_execution_modes=supported_modes,
        supports_streaming_events=supports_streaming_events,
        supports_task_cancellation=supports_cancellation,
        supports_task_resume=supports_resume,
        supports_structured_result=supports_structured_result,
        supported_canonical_roles=supported_roles,
        supported_isolation_modes=supported_isolation,
        supports_working_directory=supports_working_directory,
        supports_artifact_transport=supports_artifact_transport,
        max_timeout_seconds=max_timeout_seconds,
        concurrency_limit=concurrency_limit,
    )
    role_mapping = RoleMapping.create(
        executor_id=executor_id,
        mapping_dict={role: f"{executor_id}_{role}" for role in supported_roles},
    )
    return ReferenceFakeExecutorAdapter(capabilities=caps, role_mapping=role_mapping)


def make_package(
    canonical_task_id: str = "task-test-001",
    canonical_role: str = "coder",
    instruction: str = "Run test workload",
    execution_mode: str = "sync",
    isolation_mode: str = "worktree",
    timeout_seconds: int = 60,
    capability_requirements: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    project_id: str = "aota_forge",
) -> ExecutionPackage:
    """Create a valid ExecutionPackage with optional overrides."""
    cap_reqs: dict[str, Any] = {
        "execution_mode": execution_mode,
        "isolation_mode": isolation_mode,
        "timeout_seconds": timeout_seconds,
    }
    if capability_requirements:
        cap_reqs.update(capability_requirements)

    return ExecutionPackage.create(
        canonical_task_id=canonical_task_id,
        project_id=project_id,
        canonical_role=canonical_role,
        instruction=instruction,
        input_artifacts=({"path": "src/module.py", "digest": "sha256:1111"},),
        working_context={"repo_root": "/workspace/aota_forge"},
        capability_requirements=cap_reqs,
        constraints=constraints or {},
        idempotency_key=idempotency_key or f"idem-{canonical_task_id}",
        correlation_id=correlation_id or f"corr-{canonical_task_id}",
    )


# ==============================================================================
# 1. ExecutorRegistry Unit and Invariant Tests
# ==============================================================================

class TestExecutorRegistryRegistration:
    def test_register_and_lookup_exact(self) -> None:
        registry = ExecutorRegistry()
        adapter_a = make_adapter("executor-a")
        registry.register(adapter_a)

        assert registry.count() == 1
        assert registry.has("executor-a") is True
        assert registry.has("executor-b") is False
        assert registry.get("executor-a") is adapter_a
        assert registry.list_executor_ids() == ["executor-a"]

    def test_register_duplicate_fails_closed_different_instance(self) -> None:
        registry = ExecutorRegistry()
        adapter_1 = make_adapter("executor-dup")
        adapter_2 = make_adapter("executor-dup")
        registry.register(adapter_1)

        with pytest.raises(DuplicateExecutorError) as exc_info:
            registry.register(adapter_2)
        assert "executor-dup" in str(exc_info.value)
        assert registry.count() == 1
        assert registry.get("executor-dup") is adapter_1

    def test_register_duplicate_fails_closed_same_instance(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("executor-dup")
        registry.register(adapter)

        with pytest.raises(DuplicateExecutorError) as exc_info:
            registry.register(adapter)
        assert "executor-dup" in str(exc_info.value)
        assert registry.count() == 1

    def test_register_invalid_types(self) -> None:
        registry = ExecutorRegistry()
        with pytest.raises(TypeError):
            registry.register("not an adapter")  # type: ignore

    def test_unregister_and_missing_lookup(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("executor-temp")
        registry.register(adapter)
        assert registry.has("executor-temp") is True

        registry.unregister("executor-temp")
        assert registry.has("executor-temp") is False
        assert registry.count() == 0

        with pytest.raises(ExecutorNotFoundError):
            registry.get("executor-temp")

        with pytest.raises(ExecutorNotFoundError):
            registry.unregister("executor-temp")

    def test_clear(self) -> None:
        registry = ExecutorRegistry()
        registry.register(make_adapter("executor-1"))
        registry.register(make_adapter("executor-2"))
        assert registry.count() == 2
        registry.clear()
        assert registry.count() == 0


class TestExecutorRegistryDeterministicListingAndDescriptors:
    def test_deterministic_sorting_independent_of_registration_order(self) -> None:
        registry = ExecutorRegistry()
        # Register in reverse/scrambled order
        for eid in ("z-exec", "a-exec", "m-exec", "c-exec"):
            registry.register(make_adapter(eid))

        assert registry.list_executor_ids() == ["a-exec", "c-exec", "m-exec", "z-exec"]
        descriptors = registry.list_descriptors()
        assert [d.executor_id for d in descriptors] == ["a-exec", "c-exec", "m-exec", "z-exec"]

    def test_descriptor_serialization_zero_handler_leak(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("executor-inspect")
        registry.register(adapter)

        descriptor = registry.get_descriptor("executor-inspect")
        d_dict = descriptor.to_dict()
        d_json = descriptor.to_json()

        # Verify parsed back correctly
        parsed_dict = json.loads(d_json)
        assert parsed_dict["executor_id"] == "executor-inspect"
        assert parsed_dict["adapter_kind"] == "in_process_test_double"
        assert isinstance(parsed_dict["capabilities"], dict)

        # Invariant: zero callable / adapter object leaks
        json_str = json.dumps(d_dict)
        assert "<" not in json_str
        assert "object at" not in json_str
        assert "bound method" not in json_str
        assert "function" not in json_str

        # Test from_dict roundtrip
        restored = ExecutorDescriptor.from_dict(d_dict)
        assert restored.executor_id == descriptor.executor_id
        assert restored.capabilities == descriptor.capabilities


# ==============================================================================
# 2. Capability Matching and Resolution Cardinality (0, 1, Many)
# ==============================================================================

class TestCapabilityMatching:
    def test_matching_success(self) -> None:
        adapter = make_adapter("exec-good", supported_modes=("async", "sync"), supported_isolation=("worktree",))
        package = make_package(execution_mode="sync", isolation_mode="worktree")
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is True
        assert len(errors) == 0

    def test_mismatch_unsupported_role(self) -> None:
        adapter = make_adapter("exec-planner-only", supported_roles=("planner",))
        package = make_package(canonical_role="coder")
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is False
        assert any("Canonical role" in e for e in errors)

    def test_mismatch_execution_mode_no_silent_downgrade(self) -> None:
        adapter = make_adapter("exec-sync-only", supported_modes=("sync",))
        package = make_package(execution_mode="async")
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is False
        assert any("Execution mode" in e for e in errors)

    def test_mismatch_isolation_mode_no_silent_downgrade(self) -> None:
        adapter = make_adapter("exec-process-only", supported_isolation=("process",))
        package = make_package(isolation_mode="worktree")
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is False
        assert any("Isolation mode" in e for e in errors)

    def test_mismatch_timeout_exceeded(self) -> None:
        adapter = make_adapter("exec-timeout", max_timeout_seconds=300)
        package = make_package(timeout_seconds=600)
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is False
        assert any("timeout" in e.lower() for e in errors)

    def test_mismatch_feature_requirements(self) -> None:
        adapter = make_adapter("exec-no-cancel", supports_cancellation=False, supports_resume=False)
        package = make_package(capability_requirements={"requires_cancellation": True})
        is_compat, errors = ExecutorRegistry.check_compatibility(adapter.capabilities(), package)
        assert is_compat is False
        assert any("requires_cancellation" in e for e in errors)


class TestResolutionCardinality:
    def test_cardinality_zero_executor_not_found(self) -> None:
        registry = ExecutorRegistry()
        # Empty registry
        package = make_package()
        res = registry.resolve(package)
        assert res.outcome == ResolutionOutcome.EXECUTOR_NOT_FOUND
        assert res.is_resolved is False
        assert res.selected_executor_id is None
        assert res.candidate_executor_ids == ()

    def test_cardinality_zero_incompatible_registered_executors(self) -> None:
        registry = ExecutorRegistry()
        # Registered adapter only supports planner, package needs coder
        registry.register(make_adapter("planner-exec", supported_roles=("planner",)))
        package = make_package(canonical_role="coder")
        res = registry.resolve(package)
        assert res.outcome == ResolutionOutcome.EXECUTOR_NOT_FOUND
        assert res.is_resolved is False
        assert res.selected_executor_id is None
        assert res.candidate_executor_ids == ()

    def test_cardinality_one_resolved(self) -> None:
        registry = ExecutorRegistry()
        registry.register(make_adapter("exec-coder", supported_roles=("coder",)))
        registry.register(make_adapter("exec-reviewer", supported_roles=("reviewer",)))
        package = make_package(canonical_role="coder")
        res = registry.resolve(package)
        assert res.outcome == ResolutionOutcome.RESOLVED
        assert res.is_resolved is True
        assert res.selected_executor_id == "exec-coder"
        assert res.candidate_executor_ids == ("exec-coder",)

    def test_cardinality_many_needs_semantic_choice(self) -> None:
        registry = ExecutorRegistry()
        # Two compatible adapters for coder role
        registry.register(make_adapter("exec-b", supported_roles=("coder",)))
        registry.register(make_adapter("exec-a", supported_roles=("coder",)))
        package = make_package(canonical_role="coder")
        res = registry.resolve(package)

        assert res.outcome == ResolutionOutcome.NEEDS_SEMANTIC_CHOICE
        assert res.is_resolved is False
        assert res.selected_executor_id is None
        # Must be sorted deterministically
        assert res.candidate_executor_ids == ("exec-a", "exec-b")


class TestAmbiguityAndExplicitOverride:
    def test_dedicated_ambiguity_and_explicit_override(self) -> None:
        registry = ExecutorRegistry()
        # Adapter A and B both support coder + worktree + async
        adapter_a = make_adapter("executor-a", supported_roles=("coder",), supported_modes=("async", "sync"), supported_isolation=("worktree",))
        adapter_b = make_adapter("executor-b", supported_roles=("coder",), supported_modes=("async", "sync"), supported_isolation=("worktree",))
        registry.register(adapter_a)
        registry.register(adapter_b)

        package = make_package(canonical_role="coder", execution_mode="async", isolation_mode="worktree")

        # 1. Capability-only resolution: returns NEEDS_SEMANTIC_CHOICE with stable sorted candidates
        res_ambiguous = registry.resolve(package)
        assert res_ambiguous.outcome == ResolutionOutcome.NEEDS_SEMANTIC_CHOICE
        assert res_ambiguous.candidate_executor_ids == ("executor-a", "executor-b")
        assert res_ambiguous.selected_executor_id is None

        # 2. Explicit target executor-b overrides ambiguity
        res_explicit = registry.resolve(package, target_executor_id="executor-b")
        assert res_explicit.outcome == ResolutionOutcome.RESOLVED
        assert res_explicit.selected_executor_id == "executor-b"

        # 3. Explicit target executor-a overrides ambiguity
        res_explicit_a = registry.resolve(package, target_executor_id="executor-a")
        assert res_explicit_a.outcome == ResolutionOutcome.RESOLVED
        assert res_explicit_a.selected_executor_id == "executor-a"

    def test_explicit_unknown_executor_gives_not_found(self) -> None:
        registry = ExecutorRegistry()
        registry.register(make_adapter("executor-a"))
        package = make_package()
        res = registry.resolve(package, target_executor_id="executor-unknown")
        assert res.outcome == ResolutionOutcome.EXECUTOR_NOT_FOUND
        assert res.selected_executor_id is None

    def test_explicit_incompatible_executor_does_not_fall_back(self) -> None:
        registry = ExecutorRegistry()
        # executor-a only supports planner; executor-b supports coder
        registry.register(make_adapter("executor-a", supported_roles=("planner",)))
        registry.register(make_adapter("executor-b", supported_roles=("coder",)))

        # Package requires coder role
        package = make_package(canonical_role="coder")

        # Request explicit executor-a (which cannot do coder)
        res = registry.resolve(package, target_executor_id="executor-a")

        # Must fail closed with CAPABILITY_MISMATCH; MUST NOT silently fall back to executor-b!
        assert res.outcome == ResolutionOutcome.CAPABILITY_MISMATCH
        assert res.selected_executor_id is None
        assert len(res.mismatch_reasons) > 0


# ==============================================================================
# 3. ExecutionDispatcher Core Lifecycle & Routing Tests
# ==============================================================================

class TestExecutionDispatcherDispatchFlow:
    def test_dispatch_flow_success(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-primary")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-disp-001")
        dispatch_res = dispatcher.dispatch(package)

        assert dispatch_res.canonical_task_id == "task-disp-001"
        assert dispatch_res.adapter_handle == "ref-handle-task-disp-001"
        assert dispatch_res.initial_state in (CanonicalTaskState.ACCEPTED, CanonicalTaskState.COMPLETED)
        assert dispatcher.has_route("task-disp-001") is True

        route = dispatcher.get_route("task-disp-001")
        assert route.canonical_task_id == "task-disp-001"
        assert route.executor_id == "exec-primary"
        assert route.adapter_handle == "ref-handle-task-disp-001"
        assert route.package_id == package.package_id
        assert route.correlation_id == package.correlation_id
        assert route.idempotency_key == package.idempotency_key
        assert route.intent_fingerprint == package.intent_fingerprint

    def test_dispatch_package_validation_failure_fails_closed(self) -> None:
        registry = ExecutorRegistry()
        # Create adapter with custom validator or failing condition
        adapter = make_adapter("exec-strict", supported_roles=("coder",))
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        # Package with invalid/unsupported parameter at adapter level
        # E.g. role unsupported
        package = make_package("task-invalid-001", canonical_role="planner")
        with pytest.raises(ExecutorNotFoundError):
            dispatcher.dispatch(package)

        assert dispatcher.has_route("task-invalid-001") is False

    def test_dispatch_ambiguous_fails_closed(self) -> None:
        registry = ExecutorRegistry()
        registry.register(make_adapter("exec-1", supported_roles=("coder",)))
        registry.register(make_adapter("exec-2", supported_roles=("coder",)))
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-amb-001", canonical_role="coder")
        with pytest.raises(NeedsSemanticChoiceError) as exc_info:
            dispatcher.dispatch(package)
        assert "exec-1" in exc_info.value.candidates
        assert "exec-2" in exc_info.value.candidates
        assert dispatcher.has_route("task-amb-001") is False


class TestExecutionDispatcherIdempotency:
    def test_idempotent_replay_same_key_same_fingerprint(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-idem")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        pkg1 = make_package("task-idem-001", idempotency_key="key-same-01")
        res1 = dispatcher.dispatch(pkg1)

        # Re-dispatch with same package / same idempotency key & fingerprint
        res2 = dispatcher.dispatch(pkg1)

        assert res1 == res2
        assert adapter.dispatch_count == 1  # Replayed from cached dispatch result

    def test_idempotent_conflict_same_key_different_fingerprint(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-idem")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        pkg1 = make_package("task-idem-001", instruction="Instruction 1", idempotency_key="key-conflict-01")
        dispatcher.dispatch(pkg1)

        # Mutate instruction -> different intent fingerprint, but same idempotency key
        pkg2 = make_package("task-idem-002", instruction="Instruction 2 (mutated)", idempotency_key="key-conflict-01")

        with pytest.raises(IdempotencyConflictError):
            dispatcher.dispatch(pkg2)


class TestExecutionDispatcherLifecycleOperations:
    def test_status_result_cancel_resume_flow(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-lifecycle", supported_modes=("async", "sync"))
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-life-001", execution_mode="async")
        dispatcher.dispatch(package)

        # 1. Status query
        status_res = dispatcher.status("task-life-001")
        assert isinstance(status_res, TaskStatusResult)
        assert status_res.canonical_task_id == "task-life-001"
        assert status_res.state == CanonicalTaskState.ACCEPTED

        # 2. Simulate running & waiting on adapter
        adapter.simulate_waiting("task-life-001")
        status_waiting = dispatcher.status("task-life-001")
        assert status_waiting.state == CanonicalTaskState.WAITING

        # 3. Resume waiting task
        resume_pkg = make_package(
            "task-life-001",
            instruction="Provide requested feedback",
            idempotency_key="resume-key-001",
        )
        resume_res = dispatcher.resume("task-life-001", resume_pkg)
        assert isinstance(resume_res, ResumeResult)
        assert resume_res.state == CanonicalTaskState.RUNNING

        # 4. Simulate completion
        adapter.simulate_completion("task-life-001", result_data={"summary": "Success"})
        result_res = dispatcher.result("task-life-001")
        assert isinstance(result_res, CanonicalResult)
        assert result_res.ok is True
        assert result_res.status == "completed"
        assert result_res.result_data["summary"] == "Success"

    def test_cancellation_flow(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-cancel")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-cancel-001")
        dispatcher.dispatch(package)
        adapter.simulate_running("task-cancel-001")

        cancel_res = dispatcher.cancel("task-cancel-001")
        assert isinstance(cancel_res, CancelResult)
        assert cancel_res.cancelled is True
        assert cancel_res.state == CanonicalTaskState.CANCELLED

        # Status reflects cancelled
        status_res = dispatcher.status("task-cancel-001")
        assert status_res.state == CanonicalTaskState.CANCELLED

    def test_unknown_task_fails_closed(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-1")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        with pytest.raises(TaskNotFoundError):
            dispatcher.status("task-nonexistent")

        with pytest.raises(TaskNotFoundError):
            dispatcher.result("task-nonexistent")

        with pytest.raises(TaskNotFoundError):
            dispatcher.cancel("task-nonexistent")

        with pytest.raises(TaskNotFoundError):
            dispatcher.resume("task-nonexistent", make_package("task-nonexistent"))


class TestCrossAdapterIsolationAndZeroBroadcast:
    def test_cross_adapter_route_isolation(self) -> None:
        registry = ExecutorRegistry()
        adapter_a = make_adapter("exec-a", supported_roles=("coder",))
        adapter_b = make_adapter("exec-b", supported_roles=("reviewer",))
        registry.register(adapter_a)
        registry.register(adapter_b)
        dispatcher = ExecutionDispatcher(registry)

        # Dispatch task A to exec-a
        pkg_a = make_package("task-a", canonical_role="coder")
        dispatcher.dispatch(pkg_a)

        # Query status and result on task-a
        status_a = dispatcher.status("task-a")
        assert status_a.canonical_task_id == "task-a"

        # Invariant: exec-b must receive 0 calls!
        assert adapter_b.status_count == 0
        assert adapter_b.result_count == 0
        assert adapter_b.cancel_count == 0
        assert adapter_b.resume_count == 0
        assert adapter_b.dispatch_count == 0


class TestTaskStateReconciliation:
    def test_reconcile_status_definitive_states(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-reconcile")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-recon-001")
        dispatcher.dispatch(package)

        state = dispatcher.reconcile_status("task-recon-001")
        assert state == CanonicalTaskState.ACCEPTED

        adapter.simulate_completion("task-recon-001")
        state_completed = dispatcher.reconcile_status("task-recon-001")
        assert state_completed == CanonicalTaskState.COMPLETED

    def test_reconcile_status_unknown_preserved_never_false_success(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-reconcile-unknown")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("task-recon-unknown")
        dispatcher.dispatch(package)

        # Simulate transition to UNKNOWN (e.g. host disconnect / crash)
        adapter.simulate_unknown("task-recon-unknown")

        reconciled = dispatcher.reconcile_status("task-recon-unknown")
        assert reconciled == CanonicalTaskState.UNKNOWN

        # Invariant: UNKNOWN is not terminal, not success
        assert is_terminal(reconciled) is False
        assert is_terminal(CanonicalTaskState.UNKNOWN) is False

        # Status and result also report UNKNOWN, never success
        status_res = dispatcher.status("task-recon-unknown")
        assert status_res.state == CanonicalTaskState.UNKNOWN

        res = dispatcher.result("task-recon-unknown")
        assert res.ok is False
        assert res.status == "unknown"
        assert res.canonical_task_state == CanonicalTaskState.UNKNOWN.value


# ==============================================================================
# 4. Negative Probes and Architectural Invariants
# ==============================================================================

class TestNegativeProbesAndInvariants:
    def test_core_zero_hermes_imports(self) -> None:
        """Verify registry.py and dispatcher.py contain zero imports of hermes, subprocess, or HERMES_HOME."""
        target_files = [
            "aota_forge/core/execution/registry.py",
            "aota_forge/core/execution/dispatcher.py",
        ]
        repo_root = pathlib.Path(__file__).resolve().parent.parent

        for rel_path in target_files:
            file_path = repo_root / rel_path
            assert file_path.exists(), f"Missing file {rel_path}"
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("hermes"), f"Forbidden import {alias.name} in {rel_path}"
                        assert alias.name != "subprocess", f"Forbidden import {alias.name} in {rel_path}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert not node.module.startswith("hermes"), f"Forbidden import from {node.module} in {rel_path}"
                        assert "hermes" not in node.module.split("."), f"Forbidden hermes import in {rel_path}"
                        assert node.module != "subprocess", f"Forbidden subprocess import in {rel_path}"

            assert "HERMES_HOME" not in content

    def test_identity_domains_strictly_separated(self) -> None:
        registry = ExecutorRegistry()
        adapter = make_adapter("exec-id-test")
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(registry)

        package = make_package("canonical-id-999")
        dispatch_res = dispatcher.dispatch(package)

        route = dispatcher.get_route("canonical-id-999")
        # Invariant: canonical_task_id is NOT adapter_handle
        assert route.canonical_task_id != route.adapter_handle
        assert route.canonical_task_id == "canonical-id-999"
        assert route.adapter_handle == "ref-handle-canonical-id-999"
        assert route.package_id != route.canonical_task_id
        assert route.dispatch_attempt_id != route.canonical_task_id

    def test_reference_executor_not_production_default(self) -> None:
        assert REFERENCE_EXECUTOR_PRODUCTION_DEFAULT is False

    def test_n24_semantic_todo_count_zero(self) -> None:
        """Verify zero unfinished TODO/TBD comments exist in core execution modules."""
        target_files = [
            "aota_forge/core/execution/registry.py",
            "aota_forge/core/execution/dispatcher.py",
        ]
        repo_root = pathlib.Path(__file__).resolve().parent.parent

        for rel_path in target_files:
            file_path = repo_root / rel_path
            assert file_path.exists(), f"Missing file {rel_path}"
            content = file_path.read_text(encoding="utf-8")
            for idx, line in enumerate(content.splitlines(), start=1):
                comment_part = line.split("#", 1)[1] if "#" in line else ""
                upper_comment = comment_part.upper()
                assert "TODO" not in upper_comment, f"TODO found at {rel_path}:{idx}: {line}"
                assert "TBD" not in upper_comment, f"TBD found at {rel_path}:{idx}: {line}"
