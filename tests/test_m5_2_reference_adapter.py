"""Unit, contract, and invariant tests for M5-2 Reference Fake Executor Adapter.

Tests ReferenceFakeExecutorAdapter conformance to ExecutorAdapter interface,
in-memory execution lifecycle, idempotency, validation, role mapping, error taxonomy,
and negative matrix cases POS-M5-06, N08, N13, N14, N15, N16, N18, N20, N21.
"""

from __future__ import annotations

import inspect
from typing import Any
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
from aota_forge.core.execution import (
    ADAPTER_PROTOCOL_ERROR,
    CANCEL_UNSUPPORTED,
    CANONICAL_ROLES,
    CAPABILITY_MISMATCH,
    DISPATCH_REJECTED,
    EXECUTION_CANCELLED,
    EXECUTION_FAILED,
    PACKAGE_INVALID,
    RESUME_UNSUPPORTED,
    ROLE_MAPPING_NOT_FOUND,
    TASK_NOT_FOUND,
    TASK_STATE_UNKNOWN,
    TERMINAL_STATES,
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
    RoleMappingNotFoundError,
    TaskStatusResult,
    ValidationResult,
    is_terminal,
)


@pytest.fixture
def adapter() -> ReferenceFakeExecutorAdapter:
    """Fresh in-memory reference fake adapter."""
    return ReferenceFakeExecutorAdapter()


@pytest.fixture
def sample_package() -> ExecutionPackage:
    """Deterministic valid canonical execution package."""
    return ExecutionPackage.create(
        canonical_task_id="task-m5-2-test-001",
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Implement reference test double adapter",
        input_artifacts=({"path": "src/dummy.py", "digest": "sha256:abc"},),
        working_context={"repo_root": "/tmp/test-repo"},
        capability_requirements={
            "execution_mode": "sync",
            "isolation_mode": "process",
            "timeout_seconds": 60,
        },
        idempotency_key="idem-key-001",
        correlation_id="corr-m5-2-001",
    )


# ==============================================================================
# 1. Interface Conformance & Module Constants
# ==============================================================================

class TestInterfaceConformance:
    def test_implements_executor_adapter(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        assert issubclass(ReferenceFakeExecutorAdapter, ExecutorAdapter)
        assert isinstance(adapter, ExecutorAdapter)

    def test_all_abstract_methods_implemented(self) -> None:
        abstract_methods = ExecutorAdapter.__abstractmethods__
        for method_name in abstract_methods:
            method = getattr(ReferenceFakeExecutorAdapter, method_name, None)
            assert callable(method), f"Missing implementation for abstract method {method_name}"

    def test_module_constants_and_test_only_invariants(self) -> None:
        assert REFERENCE_EXECUTOR_TEST_ONLY is True
        assert REFERENCE_EXECUTOR_PRODUCTION_DEFAULT is False
        assert REFERENCE_EXECUTOR_ID == "reference-fake"
        assert REFERENCE_ADAPTER_KIND == "in_process_test_double"


# ==============================================================================
# 2. Capabilities & Role Mapping
# ==============================================================================

class TestCapabilitiesAndRoleMapping:
    def test_capabilities_deterministic_and_frozen(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        caps = adapter.capabilities()
        assert isinstance(caps, ExecutorCapabilities)
        assert caps.executor_id == "reference-fake"
        assert caps.adapter_kind == "in_process_test_double"
        assert set(caps.supported_canonical_roles) == set(CANONICAL_ROLES)
        assert "sync" in caps.supported_execution_modes
        assert "async" in caps.supported_execution_modes
        assert "batch" in caps.supported_execution_modes
        assert caps.supports_streaming_events is True
        assert caps.supports_task_cancellation is True
        assert caps.supports_task_resume is True
        assert caps.supports_structured_result is True
        assert caps.supports_working_directory is True
        assert caps.supports_artifact_transport is True
        assert caps.max_timeout_seconds == 3600

    def test_all_canonical_roles_map_deterministically(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        rm = adapter.role_mapping()
        assert isinstance(rm, RoleMapping)
        for role in CANONICAL_ROLES:
            target = rm.get_target_role(role)
            assert target == f"fake_{role}"

    def test_unmapped_role_fails_closed(self) -> None:
        partial_mapping = RoleMapping.create(
            "reference-fake",
            {"planner": "fake_planner", "coder": "fake_coder"},
        )
        assert partial_mapping.has_role("coder") is True
        assert partial_mapping.has_role("reviewer") is False
        with pytest.raises(RoleMappingNotFoundError):
            partial_mapping.get_target_role("reviewer")


# ==============================================================================
# 3. Validation Logic
# ==============================================================================

class TestValidation:
    def test_valid_package_validation_passes(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        val = adapter.validate_package(sample_package)
        assert isinstance(val, ValidationResult)
        assert val.valid is True
        assert val.errors == ()

    def test_unsupported_execution_mode_rejected(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        pkg = ExecutionPackage.create(
            canonical_task_id="task-mode-invalid",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Test unsupported mode",
            capability_requirements={"execution_mode": "quantum_hyperdrive"},
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err for err in val.errors)

    def test_unsupported_isolation_mode_rejected(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        pkg = ExecutionPackage.create(
            canonical_task_id="task-iso-invalid",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Test unsupported isolation",
            capability_requirements={"isolation_mode": "bare_metal_cluster"},
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err for err in val.errors)

    def test_timeout_exceeding_capability_rejected(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        pkg = ExecutionPackage.create(
            canonical_task_id="task-timeout-invalid",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Test timeout exceeded",
            capability_requirements={"timeout_seconds": 999999},
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err for err in val.errors)

    def test_unsupported_feature_flag_rejected(self) -> None:
        no_cancel_caps = ExecutorCapabilities(
            executor_id="limited-fake",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        limited_adapter = ReferenceFakeExecutorAdapter(capabilities=no_cancel_caps)
        pkg = ExecutionPackage.create(
            canonical_task_id="task-cancel-req",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Requires cancellation",
            capability_requirements={"requires_cancellation": True},
        )
        val = limited_adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err for err in val.errors)

    def test_unmapped_role_validation_rejected(self) -> None:
        limited_mapping = RoleMapping.create("reference-fake", {"coder": "fake_coder"})
        limited_caps = ExecutorCapabilities(
            executor_id="reference-fake",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        adapter = ReferenceFakeExecutorAdapter(capabilities=limited_caps, role_mapping=limited_mapping)
        pkg = ExecutionPackage.create(
            canonical_task_id="task-reviewer",
            project_id="aota_forge",
            canonical_role="reviewer",
            instruction="Review code",
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err or ROLE_MAPPING_NOT_FOUND in err for err in val.errors)


# ==============================================================================
# 4. Dispatch Flow & Idempotency
# ==============================================================================

class TestDispatchAndIdempotency:
    def test_dispatch_success_and_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        assert isinstance(dispatch_res, DispatchResult)
        assert dispatch_res.canonical_task_id == sample_package.canonical_task_id
        assert dispatch_res.adapter_handle.startswith("ref-handle-")
        assert dispatch_res.initial_state == CanonicalTaskState.ACCEPTED
        assert dispatch_res.dispatch_time == "2026-08-21T00:00:00Z"

    def test_dispatch_invalid_package_fails_closed(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        invalid_pkg = ExecutionPackage.create(
            canonical_task_id="task-invalid",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Test invalid",
            capability_requirements={"execution_mode": "unsupported_mode"},
        )
        with pytest.raises(ValueError, match=PACKAGE_INVALID):
            adapter.dispatch(invalid_pkg)

    def test_idempotent_redispatch_returns_same_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        res1 = adapter.dispatch(sample_package)
        assert adapter.dispatch_count == 1

        # Re-dispatch same package (same idempotency_key, same intent_fingerprint)
        res2 = adapter.dispatch(sample_package)
        assert adapter.dispatch_count == 2
        assert res1.canonical_task_id == res2.canonical_task_id
        assert res1.adapter_handle == res2.adapter_handle
        assert res1.initial_state == res2.initial_state
        assert res1.dispatch_time == res2.dispatch_time

    def test_idempotency_conflict_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        adapter.dispatch(sample_package)

        # Same idempotency_key, different instruction / intent
        conflicting_pkg = ExecutionPackage.create(
            canonical_task_id="task-m5-2-different",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Different instruction creating intent fingerprint conflict",
            idempotency_key=sample_package.idempotency_key,
        )
        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            adapter.dispatch(conflicting_pkg)

    def test_duplicate_canonical_task_id_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        adapter.dispatch(sample_package)

        # Different idempotency_key, but same canonical_task_id
        dup_pkg = ExecutionPackage.create(
            canonical_task_id=sample_package.canonical_task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Same task id different idempotency",
            idempotency_key="different-idem-key",
        )
        with pytest.raises(ValueError, match="DUPLICATE_CANONICAL_TASK_ID"):
            adapter.dispatch(dup_pkg)


# ==============================================================================
# 5. Status & Non-Terminal Result Flow
# ==============================================================================

class TestStatusAndResultFlow:
    def test_status_retrieval_success(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        status_res = adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert isinstance(status_res, TaskStatusResult)
        assert status_res.canonical_task_id == sample_package.canonical_task_id
        assert status_res.state == CanonicalTaskState.ACCEPTED
        assert isinstance(status_res.progress, dict)

    def test_status_task_not_found(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        with pytest.raises(KeyError, match=TASK_NOT_FOUND):
            adapter.status("non-existent-task", "handle-xyz")

    def test_status_handle_mismatch_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        with pytest.raises(ValueError, match=ADAPTER_PROTOCOL_ERROR):
            adapter.status(dispatch_res.canonical_task_id, "wrong-handle")

    def test_result_on_non_terminal_task_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        # Task is in ACCEPTED (non-terminal)
        with pytest.raises(ValueError, match="TASK_NOT_TERMINAL"):
            adapter.result(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)


# ==============================================================================
# 6. Lifecycle Transitions: Completed, Failed, Cancelled, Unknown
# ==============================================================================

class TestLifecycleTransitions:
    def test_completed_lifecycle_and_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_running(cid)
        status_running = adapter.status(cid, handle)
        assert status_running.state == CanonicalTaskState.RUNNING

        adapter.simulate_completion(cid, result_data={"output": "Done successfully"})
        status_done = adapter.status(cid, handle)
        assert status_done.state == CanonicalTaskState.COMPLETED

        result = adapter.result(cid, handle)
        assert isinstance(result, CanonicalResult)
        assert result.ok is True
        assert result.status == "completed"
        assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
        assert result.error is None
        assert result.result_data["output"] == "Done successfully"

    def test_failed_lifecycle_and_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_failure(
            cid,
            error_code=EXECUTION_FAILED,
            error_message="Simulated process crash",
            details={"signal": 9},
        )
        status_failed = adapter.status(cid, handle)
        assert status_failed.state == CanonicalTaskState.FAILED

        result = adapter.result(cid, handle)
        assert isinstance(result, CanonicalResult)
        assert result.ok is False
        assert result.status == "failed"
        assert result.canonical_task_state == CanonicalTaskState.FAILED.value
        assert result.error is not None
        assert result.error["code"] == EXECUTION_FAILED
        assert result.error["message"] == "Simulated process crash"
        assert result.error["details"] == {"signal": 9}

    def test_cancelled_lifecycle_and_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_running(cid)
        cancel_res = adapter.cancel(cid, handle)
        assert isinstance(cancel_res, CancelResult)
        assert cancel_res.cancelled is True
        assert cancel_res.state == CanonicalTaskState.CANCELLED

        status_cancelled = adapter.status(cid, handle)
        assert status_cancelled.state == CanonicalTaskState.CANCELLED

        result = adapter.result(cid, handle)
        assert isinstance(result, CanonicalResult)
        assert result.ok is False
        assert result.status == "cancelled"
        assert result.canonical_task_state == CanonicalTaskState.CANCELLED.value
        assert result.error is not None
        assert result.error["code"] == EXECUTION_CANCELLED

    def test_unknown_state_lifecycle_and_result(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_unknown(cid, details="Connection dropped to test runner")
        status_unk = adapter.status(cid, handle)
        assert status_unk.state == CanonicalTaskState.UNKNOWN
        assert is_terminal(status_unk.state) is False

        result = adapter.result(cid, handle)
        assert isinstance(result, CanonicalResult)
        assert result.ok is False
        assert result.status == "unknown"
        assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
        assert result.error is not None
        assert result.error["code"] == TASK_STATE_UNKNOWN


# ==============================================================================
# 7. Cancellation Constraints & Edge Cases
# ==============================================================================

class TestCancellationConstraints:
    def test_cancel_already_terminal_task_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_completion(cid)
        with pytest.raises(ValueError, match="TASK_ALREADY_TERMINAL"):
            adapter.cancel(cid, handle)

    def test_cancel_on_unsupported_adapter_fails_closed(self, sample_package: ExecutionPackage) -> None:
        limited_caps = ExecutorCapabilities(
            executor_id="no-cancel-fake",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync",),
            supports_streaming_events=True,
            supports_task_cancellation=False,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        adapter = ReferenceFakeExecutorAdapter(capabilities=limited_caps)
        dispatch_res = adapter.dispatch(sample_package)
        with pytest.raises(ValueError, match=CANCEL_UNSUPPORTED):
            adapter.cancel(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)


# ==============================================================================
# 8. Resume Flow & Constraints
# ==============================================================================

class TestResumeFlow:
    def test_resume_waiting_task_success(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_waiting(cid, details="Waiting for approval input")
        status_wait = adapter.status(cid, handle)
        assert status_wait.state == CanonicalTaskState.WAITING

        resume_pkg = ExecutionPackage.create(
            canonical_task_id=cid,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Resume instruction with input",
            operation="task_resume",
        )
        resume_res = adapter.resume(cid, handle, resume_pkg)
        assert isinstance(resume_res, ResumeResult)
        assert resume_res.canonical_task_id == cid
        assert resume_res.state == CanonicalTaskState.RUNNING

        status_running = adapter.status(cid, handle)
        assert status_running.state == CanonicalTaskState.RUNNING

    def test_resume_non_waiting_task_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        # Task is in ACCEPTED, not WAITING
        resume_pkg = ExecutionPackage.create(
            canonical_task_id=cid,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Resume non-waiting",
            operation="task_resume",
        )
        with pytest.raises(ValueError, match="TASK_NOT_WAITING"):
            adapter.resume(cid, handle, resume_pkg)

    def test_resume_task_id_mismatch_fails_closed(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        dispatch_res = adapter.dispatch(sample_package)
        cid = dispatch_res.canonical_task_id
        handle = dispatch_res.adapter_handle

        adapter.simulate_waiting(cid)
        mismatched_pkg = ExecutionPackage.create(
            canonical_task_id="different-task-id",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Resume with wrong task id",
            operation="task_resume",
        )
        with pytest.raises(ValueError, match="TASK_ID_MISMATCH"):
            adapter.resume(cid, handle, mismatched_pkg)

    def test_resume_on_unsupported_adapter_fails_closed(self, sample_package: ExecutionPackage) -> None:
        limited_caps = ExecutorCapabilities(
            executor_id="no-resume-fake",
            adapter_kind="in_process_test_double",
            supported_execution_modes=("sync",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        adapter = ReferenceFakeExecutorAdapter(capabilities=limited_caps)
        dispatch_res = adapter.dispatch(sample_package)
        adapter.simulate_waiting(dispatch_res.canonical_task_id)
        resume_pkg = ExecutionPackage.create(
            canonical_task_id=dispatch_res.canonical_task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Resume instruction",
            operation="task_resume",
        )
        with pytest.raises(ValueError, match=RESUME_UNSUPPORTED):
            adapter.resume(dispatch_res.canonical_task_id, dispatch_res.adapter_handle, resume_pkg)


# ==============================================================================
# 9. Acceptance Criteria & Negative Matrix Cases
# ==============================================================================

class TestAcceptanceAndNegativeMatrix:
    def test_pos_m5_06_in_memory_dispatch(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """POS-M5-06: Reference adapter in-memory dispatch completes deterministically."""
        auto_adapter = ReferenceFakeExecutorAdapter(auto_complete=True)
        dispatch_res = auto_adapter.dispatch(sample_package)
        assert dispatch_res.initial_state == CanonicalTaskState.COMPLETED
        status_res = auto_adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert status_res.state == CanonicalTaskState.COMPLETED
        result_res = auto_adapter.result(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert result_res.ok is True
        assert result_res.canonical_task_state == CanonicalTaskState.COMPLETED.value
        assert "Executed by fake_coder" in result_res.result_data["output"]

    def test_n08_silent_capability_downgrade_prohibited(self, adapter: ReferenceFakeExecutorAdapter) -> None:
        """N08: Requesting unsupported capabilities must fail validation, never silently ignore."""
        pkg = ExecutionPackage.create(
            canonical_task_id="task-n08",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Downgrade check",
            capability_requirements={"execution_mode": "unsupported_quantum"},
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any(CAPABILITY_MISMATCH in err for err in val.errors)
        with pytest.raises(ValueError, match=PACKAGE_INVALID):
            adapter.dispatch(pkg)

    def test_n13_semantic_role_decision_prohibited(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N13: Adapter must not perform heuristic role decision or rewrite canonical role."""
        dispatch_res = adapter.dispatch(sample_package)
        record = adapter.get_task_record(dispatch_res.canonical_task_id)
        assert record is not None
        assert record.package.canonical_role == "coder"

    def test_n14_task_intent_mutation_prohibited(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N14: Adapter must preserve exact task instruction and intent fingerprint."""
        dispatch_res = adapter.dispatch(sample_package)
        record = adapter.get_task_record(dispatch_res.canonical_task_id)
        assert record is not None
        assert record.package.instruction == sample_package.instruction
        assert record.package.intent_fingerprint == sample_package.intent_fingerprint

    def test_n15_automatic_semantic_retry_prohibited(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N15: Adapter must not automatically retry on failure."""
        dispatch_res = adapter.dispatch(sample_package)
        adapter.simulate_failure(dispatch_res.canonical_task_id, error_message="Fatal crash")
        status_res = adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert status_res.state == CanonicalTaskState.FAILED
        # Verify state did not silently bounce back to RUNNING
        assert status_res.state == CanonicalTaskState.FAILED

    def test_n16_local_id_becomes_canonical_prohibited(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N16: adapter_handle != canonical identity authority; canonical_task_id is preserved."""
        dispatch_res = adapter.dispatch(sample_package)
        assert dispatch_res.adapter_handle != sample_package.canonical_task_id
        status_res = adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert status_res.canonical_task_id == sample_package.canonical_task_id

    def test_n18_unknown_becomes_completed_prohibited(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N18: UNKNOWN state is non-terminal, not completed, ok=False."""
        dispatch_res = adapter.dispatch(sample_package)
        adapter.simulate_unknown(dispatch_res.canonical_task_id)
        status_res = adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert status_res.state == CanonicalTaskState.UNKNOWN
        assert is_terminal(status_res.state) is False

        res = adapter.result(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert res.ok is False
        assert res.status == "unknown"
        assert res.canonical_task_state == CanonicalTaskState.UNKNOWN.value

    def test_n20_reference_adapter_not_production_default(self) -> None:
        """N20: Reference fake adapter is strictly test-only and not production default."""
        assert REFERENCE_EXECUTOR_PRODUCTION_DEFAULT is False
        assert REFERENCE_EXECUTOR_TEST_ONLY is True

    def test_n21_zero_external_io_in_test_double(self, adapter: ReferenceFakeExecutorAdapter, sample_package: ExecutionPackage) -> None:
        """N21: Pure in-memory execution double with zero live external I/O."""
        assert adapter.external_io_count == 0
        dispatch_res = adapter.dispatch(sample_package)
        adapter.status(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        adapter.simulate_completion(dispatch_res.canonical_task_id)
        adapter.result(dispatch_res.canonical_task_id, dispatch_res.adapter_handle)
        assert adapter.external_io_count == 0
