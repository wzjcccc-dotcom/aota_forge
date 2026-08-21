"""Unit and invariant contract tests for M5-1 Canonical Execution Contracts.

Tests ExecutorCapabilities, ExecutionPackage, CanonicalTaskState, CanonicalResult,
RoleMapping, and ExecutorAdapter abstract class, including negative matrix cases N01-N24.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any

import pytest

from aota_forge.core.contracts.canonical import canonical_json
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


# ==============================================================================
# 1. ExecutorCapabilities Tests
# ==============================================================================

class TestExecutorCapabilities:
    @pytest.fixture
    def valid_caps_dict(self) -> dict[str, Any]:
        return {
            "executor_id": "reference-fake",
            "adapter_kind": "in_process_test_double",
            "supported_execution_modes": ["sync", "async"],
            "supports_streaming_events": True,
            "supports_task_cancellation": True,
            "supports_task_resume": True,
            "supports_structured_result": True,
            "supported_canonical_roles": ["coder", "executor", "planner", "reviewer", "steward"],
            "supported_isolation_modes": ["process", "worktree"],
            "supports_working_directory": True,
            "supports_artifact_transport": True,
            "max_timeout_seconds": 3600,
            "concurrency_limit": 8,
        }

    def test_capabilities_construction_and_normalization(self, valid_caps_dict):
        caps = ExecutorCapabilities.from_dict(valid_caps_dict)
        assert caps.executor_id == "reference-fake"
        assert caps.adapter_kind == "in_process_test_double"
        assert caps.supported_execution_modes == ("async", "sync")
        assert caps.supports_streaming_events is True
        assert caps.supports_task_cancellation is True
        assert caps.supports_task_resume is True
        assert caps.supports_structured_result is True
        assert caps.supported_canonical_roles == ("coder", "executor", "planner", "reviewer", "steward")
        assert caps.supported_isolation_modes == ("process", "worktree")
        assert caps.supports_working_directory is True
        assert caps.supports_artifact_transport is True
        assert caps.max_timeout_seconds == 3600
        assert caps.concurrency_limit == 8

    def test_capabilities_deterministic_serialization(self, valid_caps_dict):
        caps1 = ExecutorCapabilities.from_dict(valid_caps_dict)
        # Change input list ordering
        shuffled = dict(valid_caps_dict)
        shuffled["supported_execution_modes"] = ["async", "sync"]
        shuffled["supported_canonical_roles"] = ["steward", "coder", "reviewer", "planner", "executor"]
        caps2 = ExecutorCapabilities.from_dict(shuffled)

        assert caps1 == caps2
        assert caps1.to_json() == caps2.to_json()
        assert json.loads(caps1.to_json()) == json.loads(caps2.to_json())

    def test_capabilities_no_callable_identity(self, valid_caps_dict):
        caps = ExecutorCapabilities.from_dict(valid_caps_dict)
        caps_dict = caps.to_dict()
        for k, v in caps_dict.items():
            assert not callable(v), f"Field {k} must not be callable"
            assert not hasattr(v, "__call__") or isinstance(v, (str, bool, int, list, tuple, dict, type(None)))

    def test_capabilities_forbidden_semantic_fields_rejected(self, valid_caps_dict):
        for forbidden in FORBIDDEN_SEMANTIC_FIELDS:
            bad = dict(valid_caps_dict)
            bad[forbidden] = "some_value"
            with pytest.raises(ValueError, match="Forbidden semantic decision field rejected"):
                ExecutorCapabilities.from_dict(bad)

    def test_capabilities_invalid_timeout_concurrency(self, valid_caps_dict):
        # Non-positive max_timeout_seconds
        bad1 = dict(valid_caps_dict, max_timeout_seconds=0)
        with pytest.raises(ValueError, match="max_timeout_seconds"):
            ExecutorCapabilities.from_dict(bad1)

        bad2 = dict(valid_caps_dict, max_timeout_seconds=-10)
        with pytest.raises(ValueError, match="max_timeout_seconds"):
            ExecutorCapabilities.from_dict(bad2)

        # Non-positive concurrency_limit
        bad3 = dict(valid_caps_dict, concurrency_limit=0)
        with pytest.raises(ValueError, match="concurrency_limit"):
            ExecutorCapabilities.from_dict(bad3)

        bad4 = dict(valid_caps_dict, concurrency_limit=-5)
        with pytest.raises(ValueError, match="concurrency_limit"):
            ExecutorCapabilities.from_dict(bad4)

    def test_capabilities_strict_type_validation(self, valid_caps_dict):
        # Empty executor_id
        with pytest.raises(ValueError, match="executor_id"):
            ExecutorCapabilities.from_dict(dict(valid_caps_dict, executor_id="  "))

        # Empty adapter_kind
        with pytest.raises(ValueError, match="adapter_kind"):
            ExecutorCapabilities.from_dict(dict(valid_caps_dict, adapter_kind=""))

        # Non-boolean bool field (e.g., int 1 or string "true")
        with pytest.raises(TypeError, match="supports_streaming_events"):
            ExecutorCapabilities(
                executor_id="test",
                adapter_kind="test",
                supported_execution_modes=("sync",),
                supports_streaming_events=1,
                supports_task_cancellation=True,
                supports_task_resume=True,
                supports_structured_result=True,
                supported_canonical_roles=("coder",),
                supported_isolation_modes=("process",),
                supports_working_directory=True,
                supports_artifact_transport=True,
            )

        # Invalid canonical role in capabilities
        with pytest.raises(ValueError, match="Invalid canonical role"):
            ExecutorCapabilities.from_dict(dict(valid_caps_dict, supported_canonical_roles=["super_coder"]))

    def test_capabilities_helpers(self, valid_caps_dict):
        caps = ExecutorCapabilities.from_dict(valid_caps_dict)
        assert caps.supports_mode("sync") is True
        assert caps.supports_mode("batch") is False
        assert caps.supports_role("coder") is True
        assert caps.supports_role("hacker") is False
        assert caps.supports_isolation("worktree") is True
        assert caps.supports_isolation("container") is False


# ==============================================================================
# 2. ExecutionPackage Tests
# ==============================================================================

class TestExecutionPackage:
    @pytest.fixture
    def valid_package(self) -> ExecutionPackage:
        return ExecutionPackage.create(
            canonical_task_id="task-1001",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Implement canonical execution contracts",
            operation="task_dispatch",
            input_artifacts=[{"path": "spec.json", "digest": "sha256:abc", "role": "input"}],
            working_context={"working_dir": "/tmp/work", "isolation": "worktree"},
            capability_requirements={"mode": "sync", "isolation": "worktree"},
            constraints={"timeout_seconds": 300},
            result_expectations={"schema": "CanonicalResult"},
        )

    def test_package_valid_construction_and_roundtrip(self, valid_package):
        data = valid_package.to_dict()
        rebuilt = ExecutionPackage.from_dict(data)
        assert rebuilt == valid_package
        assert rebuilt.package_fingerprint() == valid_package.package_fingerprint()
        assert json.loads(rebuilt.to_json()) == json.loads(valid_package.to_json())

    def test_package_deterministic_intent_fingerprint(self):
        fp1 = compute_intent_fingerprint(
            canonical_role="planner",
            instruction="Plan milestone 5",
            input_artifacts=[{"path": "a.txt", "digest": "123"}, {"path": "b.txt", "digest": "456"}],
            project_id="aota_forge",
        )
        fp2 = compute_intent_fingerprint(
            canonical_role="planner",
            instruction="Plan milestone 5",
            input_artifacts=[{"path": "a.txt", "digest": "123"}, {"path": "b.txt", "digest": "456"}],
            project_id="aota_forge",
        )
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_package_key_order_invariance(self):
        art1 = [{"b": 2, "a": 1}]
        art2 = [{"a": 1, "b": 2}]
        fp1 = compute_intent_fingerprint("coder", "do work", art1, "proj")
        fp2 = compute_intent_fingerprint("coder", "do work", art2, "proj")
        assert fp1 == fp2

    def test_package_intent_fingerprint_semantic_diff(self):
        base_fp = compute_intent_fingerprint("coder", "do work", [], "proj")
        # Change role
        fp_role = compute_intent_fingerprint("reviewer", "do work", [], "proj")
        assert base_fp != fp_role
        # Change instruction
        fp_inst = compute_intent_fingerprint("coder", "do different work", [], "proj")
        assert base_fp != fp_inst
        # Change artifacts
        fp_art = compute_intent_fingerprint("coder", "do work", [{"path": "new.txt"}], "proj")
        assert base_fp != fp_art
        # Change project
        fp_proj = compute_intent_fingerprint("coder", "do work", [], "other_proj")
        assert base_fp != fp_proj

    def test_package_identity_domain_separation(self, valid_package):
        ids = {
            valid_package.package_id,
            valid_package.canonical_task_id,
            valid_package.idempotency_key,
            valid_package.correlation_id,
            valid_package.intent_fingerprint,
        }
        # All 5 identity domains must be distinct
        assert len(ids) == 5

    def test_package_forbidden_hermes_fields_rejected(self, valid_package):
        data = valid_package.to_dict()
        for forbidden in FORBIDDEN_HERMES_FIELDS:
            bad = dict(data)
            bad[forbidden] = {"profile_name": "coder_profile"}
            with pytest.raises(ValueError, match="Forbidden Hermes-private field rejected"):
                ExecutionPackage.from_dict(bad)

    def test_package_allowed_operations_only(self, valid_package):
        data = valid_package.to_dict()
        for bad_op in ("execute_shell", "run_command", "raw_exec", "eval", "hermes_invoke"):
            bad = dict(data, operation=bad_op)
            with pytest.raises(ValueError, match="operation must be one of"):
                ExecutionPackage.from_dict(bad)

    def test_package_validation_fail_closed(self, valid_package):
        data = valid_package.to_dict()
        # Invalid canonical role
        bad_role = dict(data, canonical_role="hermes_coder")
        with pytest.raises(ValueError, match="Invalid canonical role"):
            ExecutionPackage.from_dict(bad_role)

        # Mismatched intent fingerprint
        bad_fp = dict(data, intent_fingerprint="0" * 64)
        with pytest.raises(ValueError, match="intent_fingerprint mismatch"):
            ExecutionPackage.from_dict(bad_fp)

        # Empty canonical_task_id
        bad_task = dict(data, canonical_task_id="")
        with pytest.raises(ValueError, match="canonical_task_id"):
            ExecutionPackage.from_dict(bad_task)


# ==============================================================================
# 3. CanonicalTaskState Tests
# ==============================================================================

class TestCanonicalTaskState:
    def test_all_nine_states_exist(self):
        assert len(CANONICAL_TASK_STATES) == 9
        expected_names = {
            "CREATED", "ACCEPTED", "QUEUED", "RUNNING", "WAITING",
            "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN",
        }
        actual_names = {s.value for s in CANONICAL_TASK_STATES}
        assert actual_names == expected_names

    def test_terminal_states(self):
        assert len(TERMINAL_STATES) == 3
        assert TERMINAL_STATE_STRINGS == {"COMPLETED", "FAILED", "CANCELLED"}
        for s in (CanonicalTaskState.COMPLETED, CanonicalTaskState.FAILED, CanonicalTaskState.CANCELLED):
            assert is_terminal(s) is True
            assert is_terminal(s.value) is True
            assert s.is_terminal is True

        for s in (
            CanonicalTaskState.CREATED,
            CanonicalTaskState.ACCEPTED,
            CanonicalTaskState.QUEUED,
            CanonicalTaskState.RUNNING,
            CanonicalTaskState.WAITING,
            CanonicalTaskState.UNKNOWN,
        ):
            assert is_terminal(s) is False
            assert s.is_terminal is False

    def test_unknown_is_non_terminal_and_not_success(self):
        assert is_terminal(CanonicalTaskState.UNKNOWN) is False
        assert CanonicalTaskState.UNKNOWN not in TERMINAL_STATES
        # UNKNOWN can transition to COMPLETED, FAILED, RUNNING (reconciliation)
        assert can_transition(CanonicalTaskState.UNKNOWN, CanonicalTaskState.COMPLETED) is True
        assert can_transition(CanonicalTaskState.UNKNOWN, CanonicalTaskState.FAILED) is True
        assert can_transition(CanonicalTaskState.UNKNOWN, CanonicalTaskState.RUNNING) is True
        # UNKNOWN cannot transition directly to CREATED, QUEUED, WAITING, CANCELLED
        assert can_transition(CanonicalTaskState.UNKNOWN, CanonicalTaskState.CREATED) is False
        assert can_transition(CanonicalTaskState.UNKNOWN, CanonicalTaskState.QUEUED) is False

    def test_valid_state_transitions(self):
        # CREATED -> ACCEPTED, FAILED
        assert can_transition(CanonicalTaskState.CREATED, CanonicalTaskState.ACCEPTED) is True
        assert can_transition(CanonicalTaskState.CREATED, CanonicalTaskState.FAILED) is True

        # ACCEPTED -> QUEUED, RUNNING, UNKNOWN
        assert can_transition(CanonicalTaskState.ACCEPTED, CanonicalTaskState.QUEUED) is True
        assert can_transition(CanonicalTaskState.ACCEPTED, CanonicalTaskState.RUNNING) is True
        assert can_transition(CanonicalTaskState.ACCEPTED, CanonicalTaskState.UNKNOWN) is True

        # QUEUED -> RUNNING, CANCELLED
        assert can_transition(CanonicalTaskState.QUEUED, CanonicalTaskState.RUNNING) is True
        assert can_transition(CanonicalTaskState.QUEUED, CanonicalTaskState.CANCELLED) is True

        # RUNNING -> WAITING, COMPLETED, FAILED, CANCELLED, UNKNOWN
        assert can_transition(CanonicalTaskState.RUNNING, CanonicalTaskState.WAITING) is True
        assert can_transition(CanonicalTaskState.RUNNING, CanonicalTaskState.COMPLETED) is True
        assert can_transition(CanonicalTaskState.RUNNING, CanonicalTaskState.FAILED) is True
        assert can_transition(CanonicalTaskState.RUNNING, CanonicalTaskState.CANCELLED) is True
        assert can_transition(CanonicalTaskState.RUNNING, CanonicalTaskState.UNKNOWN) is True

        # WAITING -> RUNNING, CANCELLED
        assert can_transition(CanonicalTaskState.WAITING, CanonicalTaskState.RUNNING) is True
        assert can_transition(CanonicalTaskState.WAITING, CanonicalTaskState.CANCELLED) is True

    def test_invalid_transitions_fail_closed(self):
        # Terminal states have 0 allowed transitions
        for term in (CanonicalTaskState.COMPLETED, CanonicalTaskState.FAILED, CanonicalTaskState.CANCELLED):
            for target in CANONICAL_TASK_STATES:
                assert can_transition(term, target) is False
                with pytest.raises(InvalidStateTransitionError):
                    validate_transition(term, target)

        # Illegal skip: CREATED -> RUNNING or COMPLETED
        assert can_transition(CanonicalTaskState.CREATED, CanonicalTaskState.RUNNING) is False
        assert can_transition(CanonicalTaskState.CREATED, CanonicalTaskState.COMPLETED) is False
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(CanonicalTaskState.CREATED, CanonicalTaskState.COMPLETED)

    def test_parse_state_and_allowed_transitions(self):
        assert parse_state("running") == CanonicalTaskState.RUNNING
        assert parse_state("RUNNING") == CanonicalTaskState.RUNNING
        assert parse_state(CanonicalTaskState.RUNNING) == CanonicalTaskState.RUNNING
        with pytest.raises(ValueError, match="Unknown CanonicalTaskState"):
            parse_state("NON_EXISTENT_STATE")

        transitions = allowed_transitions("RUNNING")
        assert CanonicalTaskState.COMPLETED in transitions
        assert CanonicalTaskState.FAILED in transitions


# ==============================================================================
# 4. CanonicalResult Tests
# ==============================================================================

class TestCanonicalResult:
    def test_result_success_category(self):
        res = CanonicalResult.success(
            canonical_task_id="task-1",
            executor_id="reference-fake",
            result_data={"output": "all good"},
            output_artifacts=[{"path": "out.json", "sha256": "123", "size_bytes": 10}],
            stdout_summary="Success output",
            correlation_id="corr-1",
        )
        assert res.ok is True
        assert res.status == "completed"
        assert res.canonical_task_state == "COMPLETED"
        assert res.exit_code == 0
        assert res.error is None
        assert res.result_data == {"output": "all good"}

        # Round-trip
        rebuilt = CanonicalResult.from_dict(res.to_dict())
        assert rebuilt == res
        assert json.loads(res.to_json()) == json.loads(rebuilt.to_json())

    def test_result_failure_category(self):
        res = CanonicalResult.failure(
            canonical_task_id="task-2",
            executor_id="reference-fake",
            error_code=EXECUTION_FAILED,
            error_message="Runtime exception in script",
            details={"exception": "ZeroDivisionError"},
            stdout_summary="some output",
            stderr_summary="Traceback...",
            correlation_id="corr-2",
        )
        assert res.ok is False
        assert res.status == "failed"
        assert res.canonical_task_state == "FAILED"
        assert res.error["code"] == "EXECUTION_FAILED"
        assert res.error["details"] == {"exception": "ZeroDivisionError"}

    def test_result_rejected_category(self):
        res = CanonicalResult.rejected(
            canonical_task_id="task-3",
            executor_id="reference-fake",
            error_code=CAPABILITY_MISMATCH,
            error_message="Executor does not support batch mode",
            correlation_id="corr-3",
        )
        assert res.ok is False
        assert res.status == "rejected"
        assert res.error["code"] == "CAPABILITY_MISMATCH"

    def test_result_timeout_category(self):
        res = CanonicalResult.timeout(
            canonical_task_id="task-4",
            executor_id="reference-fake",
            error_message="Task exceeded 300s limit",
            correlation_id="corr-4",
        )
        assert res.ok is False
        assert res.status == "failed"
        assert res.error["code"] == "EXECUTION_TIMEOUT"

    def test_result_cancelled_category(self):
        res = CanonicalResult.cancelled(
            canonical_task_id="task-5",
            executor_id="reference-fake",
            error_message="Task was cancelled by operator",
            correlation_id="corr-5",
        )
        assert res.ok is False
        assert res.status == "cancelled"
        assert res.canonical_task_state == "CANCELLED"
        assert res.error["code"] == "EXECUTION_CANCELLED"

    def test_result_unknown_category(self):
        res = CanonicalResult.unknown(
            canonical_task_id="task-6",
            executor_id="reference-fake",
            error_message="Host disconnected unexpectedly",
            correlation_id="corr-6",
        )
        assert res.ok is False
        assert res.status == "unknown"
        assert res.canonical_task_state == "UNKNOWN"
        assert res.error["code"] == "TASK_STATE_UNKNOWN"
        assert res.error["retryable"] is True

    def test_result_consistency_invariants(self):
        # ok=True cannot have non-completed status or error
        with pytest.raises(ValueError, match="ok=True requires status='completed'"):
            CanonicalResult(
                ok=True,
                status="failed",
                canonical_task_id="t",
                executor_id="e",
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

        with pytest.raises(ValueError, match="ok=True must have error=None"):
            CanonicalResult(
                ok=True,
                status="completed",
                canonical_task_id="t",
                executor_id="e",
                canonical_task_state="COMPLETED",
                exit_code=0,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error={"code": "ERR", "message": "msg"},
                execution_stats={},
                correlation_id="c",
            )

        # ok=False requires error envelope
        with pytest.raises(ValueError, match="ok=False requires a structured error envelope"):
            CanonicalResult(
                ok=False,
                status="failed",
                canonical_task_id="t",
                executor_id="e",
                canonical_task_state="FAILED",
                exit_code=1,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )

        # UNKNOWN or CANCELLED state cannot have ok=True
        with pytest.raises(ValueError, match="requires canonical_task_state='COMPLETED'"):
            CanonicalResult(
                ok=True,
                status="completed",
                canonical_task_id="t",
                executor_id="e",
                canonical_task_state="UNKNOWN",
                exit_code=0,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )

    def test_result_forbidden_hermes_objects_rejected(self):
        for bad_key in ("hermes_session", "hermes_task", "hermes_trace", "hermes_worker", "hermes_profile"):
            payload = {
                "ok": True,
                "status": "completed",
                "canonical_task_id": "t",
                "executor_id": "e",
                "canonical_task_state": "COMPLETED",
                "result_data": {},
                "output_artifacts": [],
                "execution_stats": {},
                "correlation_id": "c",
                bad_key: {"raw": "leak"},
            }
            with pytest.raises(ValueError, match="Forbidden Hermes-private object in result"):
                CanonicalResult.from_dict(payload)


# ==============================================================================
# 5. Canonical Roles and RoleMapping Tests
# ==============================================================================

class TestRoleMapping:
    def test_exact_five_canonical_roles(self):
        assert len(CANONICAL_ROLES) == 5
        assert CANONICAL_ROLE_SET == {"planner", "coder", "reviewer", "steward", "executor"}
        for role in ("planner", "coder", "reviewer", "steward", "executor"):
            assert is_canonical_role(role) is True
            assert validate_canonical_role(role) == role

    def test_rejects_non_canonical_roles_and_hermes_profiles(self):
        for invalid_role in (
            "hermes_coder",
            "coder_profile",
            "planner_v2",
            "developer",
            "architect",
            "lead",
            "",
            "UNKNOWN",
        ):
            assert is_canonical_role(invalid_role) is False
            with pytest.raises((ValueError, TypeError)):
                validate_canonical_role(invalid_role)

    def test_role_mapping_deterministic_construction_and_sorting(self):
        mapping = RoleMapping.create(
            executor_id="reference-fake",
            mapping_dict={
                "steward": "fake_steward",
                "planner": "fake_planner",
                "coder": "fake_coder",
                "executor": "fake_executor",
                "reviewer": "fake_reviewer",
            },
        )
        assert mapping.executor_id == "reference-fake"
        # Must be sorted alphabetically by canonical role
        assert mapping.mapped_roles() == ("coder", "executor", "planner", "reviewer", "steward")
        assert mapping.get_target_role("coder") == "fake_coder"
        assert mapping.get_target_role("reviewer") == "fake_reviewer"

    def test_role_mapping_missing_role_fails_closed(self):
        mapping = RoleMapping.create(
            executor_id="partial-executor",
            mapping_dict={"coder": "only_coder"},
        )
        assert mapping.has_role("coder") is True
        assert mapping.has_role("planner") is False
        with pytest.raises(RoleMappingNotFoundError) as exc_info:
            mapping.get_target_role("planner")
        assert exc_info.value.code == "ROLE_MAPPING_NOT_FOUND"
        assert exc_info.value.to_dict()["code"] == "ROLE_MAPPING_NOT_FOUND"

    def test_role_mapping_forbidden_semantic_decision_fields_rejected(self):
        for forbidden in ("preferred_executor", "best_role", "default_profile", "similarity_threshold"):
            payload = {
                "executor_id": "test-exec",
                "mappings": {"coder": "target_coder"},
                forbidden: "hack",
            }
            with pytest.raises(ValueError, match="Forbidden role decision field rejected"):
                RoleMapping.from_dict(payload)

    def test_role_mapping_serialization_roundtrip(self):
        m1 = RoleMapping.create(
            executor_id="test",
            mapping_dict={"coder": "c", "planner": "p"},
        )
        data = m1.to_dict()
        m2 = RoleMapping.from_dict(data)
        assert m1 == m2
        assert m1.to_json() == m2.to_json()


# ==============================================================================
# 6. ExecutorAdapter Interface and Support Models Tests
# ==============================================================================

class TestExecutorAdapterInterface:
    def test_executor_adapter_abstract_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            ExecutorAdapter()

    def test_subclass_must_implement_all_seven_abstract_methods(self):
        class IncompleteAdapter(ExecutorAdapter):
            def capabilities(self):
                pass

        with pytest.raises(TypeError):
            IncompleteAdapter()

        # Complete implementation can be instantiated
        class DummyAdapter(ExecutorAdapter):
            def capabilities(self) -> ExecutorCapabilities:
                pass
            def validate_package(self, package: ExecutionPackage) -> ValidationResult:
                pass
            def dispatch(self, package: ExecutionPackage) -> DispatchResult:
                pass
            def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
                pass
            def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
                pass
            def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
                pass
            def resume(self, canonical_task_id: str, adapter_handle: str, resume_package: ExecutionPackage) -> ResumeResult:
                pass

        adapter = DummyAdapter()
        assert isinstance(adapter, ExecutorAdapter)

    def test_adapter_has_no_generic_shell_or_execute_command(self):
        forbidden_methods = {"execute", "run_shell", "raw_command", "invoke_anything", "bash", "exec"}
        adapter_attrs = set(dir(ExecutorAdapter))
        leaked = forbidden_methods.intersection(adapter_attrs)
        assert len(leaked) == 0, f"ExecutorAdapter leaked forbidden command methods: {leaked}"

    def test_validation_result_model(self):
        vr = ValidationResult(valid=True, errors=())
        assert vr.valid is True
        assert vr.errors == ()
        assert vr.to_dict() == {"valid": True, "errors": []}

        vr_invalid = ValidationResult(valid=False, errors=("Missing package_id", "Bad role"))
        assert vr_invalid.valid is False
        assert len(vr_invalid.errors) == 2
        assert ValidationResult.from_dict(vr_invalid.to_dict()) == vr_invalid

    def test_dispatch_result_model(self):
        dr = DispatchResult(
            canonical_task_id="t-1",
            adapter_handle="handle-123",
            initial_state=CanonicalTaskState.ACCEPTED,
            dispatch_time="2026-08-21T12:00:00Z",
        )
        assert dr.canonical_task_id == "t-1"
        assert dr.adapter_handle == "handle-123"
        assert dr.initial_state == CanonicalTaskState.ACCEPTED
        assert DispatchResult.from_dict(dr.to_dict()) == dr

    def test_task_status_result_model(self):
        ts = TaskStatusResult(
            canonical_task_id="t-2",
            state=CanonicalTaskState.RUNNING,
            progress={"step": 2, "total": 5},
            details="Executing step 2",
        )
        assert ts.canonical_task_id == "t-2"
        assert ts.state == CanonicalTaskState.RUNNING
        assert ts.progress == {"step": 2, "total": 5}
        assert TaskStatusResult.from_dict(ts.to_dict()) == ts

    def test_cancel_result_model(self):
        cr = CancelResult(
            canonical_task_id="t-3",
            cancelled=True,
            state=CanonicalTaskState.CANCELLED,
        )
        assert cr.canonical_task_id == "t-3"
        assert cr.cancelled is True
        assert cr.state == CanonicalTaskState.CANCELLED
        assert CancelResult.from_dict(cr.to_dict()) == cr

    def test_resume_result_model(self):
        rr = ResumeResult(
            canonical_task_id="t-4",
            state=CanonicalTaskState.RUNNING,
        )
        assert rr.canonical_task_id == "t-4"
        assert rr.state == CanonicalTaskState.RUNNING
        assert ResumeResult.from_dict(rr.to_dict()) == rr


# ==============================================================================
# 7. Negative Acceptance Tests (N01 - N24) and Invariants
# ==============================================================================

class TestNegativeAcceptanceAndInvariants:
    def test_n01_no_hermes_imports_in_core_execution(self):
        """N01: No file under aota_forge/core/execution may import or reference Hermes."""
        exec_dir = "aota_forge/core/execution"
        assert os.path.isdir(exec_dir), f"Directory {exec_dir} must exist"

        for filename in os.listdir(exec_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(exec_dir, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert "hermes" not in alias.name.lower(), (
                                f"Forbidden hermes import in {filepath}: {alias.name}"
                            )
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        assert "hermes" not in module.lower(), (
                            f"Forbidden hermes import from {filepath}: {module}"
                        )

    def test_n02_hermes_profile_not_canonical_role(self):
        """N02: Hermes profile names cannot be used as canonical roles."""
        hermes_profiles = ["planner_profile", "coder_profile", "reviewer_profile", "steward_profile", "executor_profile"]
        for profile in hermes_profiles:
            assert is_canonical_role(profile) is False
            with pytest.raises(ValueError):
                validate_canonical_role(profile)

    def test_n08_no_silent_capability_downgrade(self):
        """N08: Capabilities must strictly validate and fail closed."""
        with pytest.raises(ValueError):
            ExecutorCapabilities(
                executor_id="test",
                adapter_kind="test",
                supported_execution_modes=(),
                supports_streaming_events=True,
                supports_task_cancellation=True,
                supports_task_resume=True,
                supports_structured_result=True,
                supported_canonical_roles=("coder",),
                supported_isolation_modes=("process",),
                supports_working_directory=True,
                supports_artifact_transport=True,
            )

    def test_n13_no_semantic_role_decision_in_core(self):
        """N13: RoleMapping cannot heuristically fall back or invent roles."""
        mapping = RoleMapping.create("test", {"coder": "target_coder"})
        with pytest.raises(RoleMappingNotFoundError) as exc:
            mapping.get_target_role("reviewer")
        assert exc.value.code == ROLE_MAPPING_NOT_FOUND

    def test_n14_no_intent_mutation_without_fingerprint_change(self):
        """N14: Intent fingerprint deterministically binds instruction, role, artifacts, project."""
        fp1 = compute_intent_fingerprint("coder", "do step 1", [{"k": "v"}], "p1")
        fp2 = compute_intent_fingerprint("coder", "do step 1 and step 2", [{"k": "v"}], "p1")
        assert fp1 != fp2, "Intent mutation must produce different fingerprint"

    def test_n16_executor_local_id_not_canonical_identity(self):
        """N16: package_id, canonical_task_id, adapter_handle are distinct."""
        pkg = ExecutionPackage.create(
            canonical_task_id="CANONICAL-42",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="build",
        )
        assert pkg.package_id != pkg.canonical_task_id
        assert pkg.canonical_task_id == "CANONICAL-42"
        dr = DispatchResult(
            canonical_task_id=pkg.canonical_task_id,
            adapter_handle="hermes-worker-proc-99",
            initial_state=CanonicalTaskState.ACCEPTED,
            dispatch_time="now",
        )
        assert dr.canonical_task_id != dr.adapter_handle

    def test_n17_malformed_package_fails(self):
        """N17: Invalid package fails structural validation."""
        with pytest.raises(ValueError):
            ExecutionPackage.from_dict({
                "package_id": "pkg-1",
                "protocol_version": "1.0.0",
                "contract_hash": EXECUTION_CONTRACT_HASH,
                "operation": "invalid_op",
                "canonical_task_id": "t-1",
                "project_id": "p",
                "canonical_role": "coder",
                "instruction": "i",
                "input_artifacts": [],
                "working_context": {},
                "capability_requirements": {},
                "constraints": {},
                "idempotency_key": "k",
                "intent_fingerprint": "bad",
                "correlation_id": "c",
                "result_expectations": {},
            })

    def test_n18_unknown_state_never_completed_or_ok(self):
        """N18: UNKNOWN state cannot be treated as completion or ok=True."""
        assert is_terminal(CanonicalTaskState.UNKNOWN) is False
        with pytest.raises(ValueError):
            CanonicalResult(
                ok=True,
                status="unknown",
                canonical_task_id="t",
                executor_id="e",
                canonical_task_state="UNKNOWN",
                exit_code=0,
                result_data={},
                output_artifacts=(),
                stdout_summary=None,
                stderr_summary=None,
                error=None,
                execution_stats={},
                correlation_id="c",
            )

    def test_n19_no_hermes_objects_in_canonical_result(self):
        """N19: Hermes private object rejected from CanonicalResult."""
        with pytest.raises(ValueError, match="Forbidden Hermes-private object in result"):
            CanonicalResult.from_dict({
                "ok": False,
                "status": "failed",
                "canonical_task_id": "t",
                "executor_id": "e",
                "canonical_task_state": "FAILED",
                "result_data": {},
                "output_artifacts": [],
                "error": {"code": "ERR", "message": "fail"},
                "execution_stats": {},
                "correlation_id": "c",
                "hermes_session": "escaped_session",
            })

    def test_n24_no_semantic_todo_or_tbd_in_core_execution(self):
        """N24: Core execution contracts contain 0 semantic TODO / TBD markers."""
        exec_dir = "aota_forge/core/execution"
        todo_pattern = re.compile(r"\b(TODO|TBD|FIXME)\b", re.IGNORECASE)
        for filename in os.listdir(exec_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(exec_dir, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                matches = todo_pattern.findall(content)
                assert len(matches) == 0, f"Found {matches} in {filepath}"

    def test_error_taxonomy_codes_completeness(self):
        """All 17 required canonical machine error codes are defined."""
        required_codes = {
            "EXECUTOR_NOT_FOUND",
            "EXECUTOR_UNAVAILABLE",
            "CAPABILITY_MISMATCH",
            "PACKAGE_INVALID",
            "ROLE_MAPPING_NOT_FOUND",
            "DISPATCH_REJECTED",
            "DISPATCH_TIMEOUT",
            "TASK_NOT_FOUND",
            "TASK_STATE_UNKNOWN",
            "EXECUTION_FAILED",
            "EXECUTION_TIMEOUT",
            "EXECUTION_CANCELLED",
            "CANCEL_UNSUPPORTED",
            "RESUME_UNSUPPORTED",
            "ADAPTER_PROTOCOL_ERROR",
            "RESULT_MALFORMED",
            "INTERNAL_MECHANICAL_ERROR",
        }
        enum_codes = {e.value for e in ExecutionErrorCode}
        assert required_codes.issubset(enum_codes)
        assert len(enum_codes) == 17
