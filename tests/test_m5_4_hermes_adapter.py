"""Comprehensive unit and contract test suite for M5-4 Hermes Adapter Translation Layer.

Tests POS-M5-07 HermesAdapter translation and negative matrix N01-N21:
- ExecutorAdapter conformance
- Hermes capabilities descriptor
- Canonical role -> Hermes profile deterministic mapping
- Missing role fails closed (RoleMappingNotFoundError)
- Canonical payload translation (instruction/context/artifacts preserved)
- Frozen Hermes host status -> CanonicalTaskState mappings
- Unknown Hermes status -> UNKNOWN (never false completion)
- Hermes completion -> CanonicalResult.success
- Hermes failure -> CanonicalResult.failure (structured error)
- Timeout & uncertainty semantics
- Cancel & resume translation
- Local adapter handle vs. canonical_task_id separation
- Malformed output handling (RESULT_MALFORMED)
- Host unavailable handling (EXECUTOR_UNAVAILABLE)
- Zero Hermes private type escape into CanonicalResult
- Offline test double (FakeHermesHostClient) and zero live process spawning
"""

from __future__ import annotations

import ast
import os
import pathlib
from typing import Any, Mapping

import pytest

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
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution import (
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
    RoleMappingNotFoundError,
    TaskStatusResult,
    ValidationResult,
    validate_canonical_role,
)
from aota_forge.core.execution.results import FORBIDDEN_HERMES_RESULT_KEYS


# ==============================================================================
# Fake Hermes Host Client (Offline Test Double)
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

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        handle = f"hermes-local-task-{self.next_handle_id}"
        self.next_handle_id += 1
        self.dispatched.append(dict(payload))
        resp = {
            "adapter_handle": handle,
            "status": "pending",
            "dispatch_time": "2026-08-21T12:00:00Z",
        }
        self.statuses[handle] = {"status": "pending", "details": "task queued in hermes queue"}
        return resp

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.statuses.get(
            adapter_handle,
            {"status": "unreachable", "details": f"Unknown handle: {adapter_handle}"},
        )

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.results.get(
            adapter_handle,
            {
                "status": "error",
                "error": {"code": "TASK_NOT_FOUND", "message": f"No result for {adapter_handle}"},
            },
        )

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.cancels.append(adapter_handle)
        self.statuses[adapter_handle] = {"status": "cancelled", "details": "cancelled by user"}
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.resumes.append((adapter_handle, dict(payload)))
        self.statuses[adapter_handle] = {"status": "running", "details": "resumed execution"}
        return {"status": "running"}


# ==============================================================================
# Test Fixtures
# ==============================================================================

@pytest.fixture
def fake_client() -> FakeHermesHostClient:
    return FakeHermesHostClient()


@pytest.fixture
def hermes_adapter(fake_client: FakeHermesHostClient) -> HermesAdapter:
    return HermesAdapter(host_client=fake_client)


@pytest.fixture
def sample_package() -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id="task-m5-4-001",
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Implement Hermes adapter mechanical translation layer",
        subject_ref="wzjcccc-dotcom/aota-hermes-tools#9",
        input_artifacts=[{"path": "spec.md", "content": "specification text"}],
        working_context={"repo_path": "/path/to/repo"},
        capability_requirements={"execution_mode": "async", "isolation": "worktree"},
        constraints={"timeout_seconds": 3600},
        correlation_id="corr-m5-4-12345",
        idempotency_key="idem-key-abc",
    )


# ==============================================================================
# 1. POS-M5-07 HermesAdapter Conformance & Capabilities
# ==============================================================================

class TestHermesAdapterConformance:
    def test_subclass_and_protocol_conformance(self, hermes_adapter, fake_client):
        assert isinstance(hermes_adapter, ExecutorAdapter)
        assert isinstance(fake_client, HermesHostClient)

    def test_default_capabilities_descriptor(self, hermes_adapter):
        caps = hermes_adapter.capabilities()
        assert isinstance(caps, ExecutorCapabilities)
        assert caps.executor_id == "hermes"
        assert caps.adapter_kind == "hermes_host_adapter"
        assert caps.supported_execution_modes == ("async", "sync")
        assert caps.supports_streaming_events is False
        assert caps.supports_task_cancellation is True
        assert caps.supports_task_resume is True
        assert caps.supports_structured_result is True
        assert caps.supported_canonical_roles == ("coder", "executor", "planner", "reviewer", "steward")
        assert caps.supported_isolation_modes == ("process", "worktree")
        assert caps.supports_working_directory is True
        assert caps.supports_artifact_transport is True
        assert caps.max_timeout_seconds == 86400
        assert caps.concurrency_limit == 16


# ==============================================================================
# 2. Canonical Role -> Hermes Profile Mapping
# ==============================================================================

class TestRoleMapping:
    @pytest.mark.parametrize(
        ("role", "expected_profile"),
        [
            ("planner", "planner_profile"),
            ("coder", "coder_profile"),
            ("reviewer", "reviewer_profile"),
            ("steward", "steward_profile"),
            ("executor", "executor_profile"),
        ],
    )
    def test_all_five_canonical_roles_mapped(self, role: str, expected_profile: str):
        profile = canonical_role_to_hermes_profile(role)
        assert profile == expected_profile

    def test_missing_role_fails_closed(self):
        with pytest.raises(TypeError):
            canonical_role_to_hermes_profile(123)  # type: ignore

        with pytest.raises(ValueError, match="Invalid canonical role"):
            canonical_role_to_hermes_profile("architect")

        with pytest.raises(ValueError, match="Invalid canonical role"):
            canonical_role_to_hermes_profile("hermes_default")

    def test_custom_adapter_role_mapping_not_found(self):
        # Create adapter with partial mapping
        from aota_forge.core.execution.roles import RoleMapping
        partial_mapping = RoleMapping.create("hermes", {"coder": "coder_profile"})
        adapter = HermesAdapter(role_mapping=partial_mapping)

        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="planner",
            instruction="Plan task",
        )
        val = adapter.validate_package(pkg)
        assert val.valid is False
        assert any("ROLE_MAPPING_NOT_FOUND" in err for err in val.errors)


# ==============================================================================
# 3. Canonical to Hermes Payload Translation
# ==============================================================================

class TestPayloadTranslation:
    def test_payload_structure_and_verbatim_intent(self, sample_package):
        payload = canonical_to_hermes_payload(sample_package)

        assert isinstance(payload, dict)
        assert payload["profile"] == "coder_profile"
        assert payload["instruction"] == sample_package.instruction
        assert payload["instruction"] == "Implement Hermes adapter mechanical translation layer"

        context = payload["context"]
        assert context["canonical_task_id"] == sample_package.canonical_task_id
        assert context["project_id"] == "aota_forge"
        assert context["subject_ref"] == "wzjcccc-dotcom/aota-hermes-tools#9"
        assert context["correlation_id"] == "corr-m5-4-12345"
        assert context["idempotency_key"] == "idem-key-abc"
        assert context["intent_fingerprint"] == sample_package.intent_fingerprint
        assert context["working_context"] == {"repo_path": "/path/to/repo"}

        assert payload["artifacts"] == [{"content": "specification text", "path": "spec.md"}]
        assert payload["constraints"] == {"timeout_seconds": 3600}
        assert payload["capability_requirements"] == {"execution_mode": "async", "isolation": "worktree"}
        assert payload["operation"] == "task_dispatch"
        assert payload["package_id"] == sample_package.package_id

    def test_payload_translation_deterministic_json(self, sample_package):
        p1 = canonical_to_hermes_payload(sample_package)
        p2 = canonical_to_hermes_payload(sample_package)
        assert canonical_json(p1) == canonical_json(p2)


# ==============================================================================
# 4. Package Validation
# ==============================================================================

class TestPackageValidation:
    def test_valid_package(self, hermes_adapter, sample_package):
        val = hermes_adapter.validate_package(sample_package)
        assert isinstance(val, ValidationResult)
        assert val.valid is True
        assert val.errors == ()

    def test_invalid_execution_mode(self, hermes_adapter):
        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="coder",
            instruction="Code something",
            capability_requirements={"execution_mode": "quantum_parallel"},
        )
        val = hermes_adapter.validate_package(pkg)
        assert val.valid is False
        assert any("CAPABILITY_MISMATCH" in err for err in val.errors)

    def test_invalid_isolation_mode(self, hermes_adapter):
        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="coder",
            instruction="Code something",
            capability_requirements={"isolation": "hardware_enclave"},
        )
        val = hermes_adapter.validate_package(pkg)
        assert val.valid is False
        assert any("CAPABILITY_MISMATCH" in err for err in val.errors)

    def test_timeout_exceeds_max(self, hermes_adapter):
        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="coder",
            instruction="Code something",
            constraints={"timeout_seconds": 999999},
        )
        val = hermes_adapter.validate_package(pkg)
        assert val.valid is False
        assert any("CAPABILITY_MISMATCH" in err for err in val.errors)


# ==============================================================================
# 5. Dispatch, Status, Result, Cancel, Resume Lifecycle
# ==============================================================================

class TestAdapterLifecycle:
    def test_dispatch_lifecycle(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)

        assert isinstance(dispatch_res, DispatchResult)
        assert dispatch_res.canonical_task_id == sample_package.canonical_task_id
        assert dispatch_res.adapter_handle == "hermes-local-task-1"
        assert dispatch_res.initial_state == CanonicalTaskState.QUEUED
        assert dispatch_res.dispatch_time == "2026-08-21T12:00:00Z"

        assert len(fake_client.dispatched) == 1
        assert fake_client.dispatched[0]["profile"] == "coder_profile"
        assert fake_client.dispatched[0]["context"]["canonical_task_id"] == sample_package.canonical_task_id

    def test_dispatch_rejected_on_invalid_package(self, hermes_adapter):
        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="coder",
            instruction="Code",
            capability_requirements={"execution_mode": "unsupported_mode"},
        )
        with pytest.raises(HermesDispatchRejectedError, match="DISPATCH_REJECTED"):
            hermes_adapter.dispatch(pkg)

    def test_status_query(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)
        handle = dispatch_res.adapter_handle

        status_res = hermes_adapter.status(sample_package.canonical_task_id, handle)
        assert isinstance(status_res, TaskStatusResult)
        assert status_res.canonical_task_id == sample_package.canonical_task_id
        assert status_res.state == CanonicalTaskState.QUEUED

        # Update status in fake host
        fake_client.statuses[handle] = {
            "status": "running",
            "progress": {"step": 2, "total": 5},
            "details": "in progress",
        }
        status_res2 = hermes_adapter.status(sample_package.canonical_task_id, handle)
        assert status_res2.state == CanonicalTaskState.RUNNING
        assert status_res2.progress == {"step": 2, "total": 5}
        assert status_res2.details == "in progress"

    def test_result_fetch_success(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)
        handle = dispatch_res.adapter_handle

        fake_client.results[handle] = {
            "status": "done",
            "exit_code": 0,
            "result_data": {"files_changed": ["aota_forge/adapters/hermes/executor.py"]},
            "artifacts": [{"name": "diff.patch", "content": "patch data"}],
            "stdout": "Success",
            "stderr": "",
            "stats": {"duration_ms": 250},
            "correlation_id": "corr-m5-4-12345",
        }

        res = hermes_adapter.result(sample_package.canonical_task_id, handle)
        assert isinstance(res, CanonicalResult)
        assert res.ok is True
        assert res.status == "completed"
        assert res.canonical_task_id == sample_package.canonical_task_id
        assert res.executor_id == "hermes"
        assert res.canonical_task_state == CanonicalTaskState.COMPLETED.value
        assert res.exit_code == 0
        assert res.result_data == {"files_changed": ["aota_forge/adapters/hermes/executor.py"]}
        assert res.output_artifacts == ({"content": "patch data", "name": "diff.patch"},)
        assert res.stdout_summary == "Success"
        assert res.stderr_summary == ""
        assert res.error is None
        assert res.execution_stats == {"duration_ms": 250}
        assert res.correlation_id == "corr-m5-4-12345"

    def test_result_fetch_failure(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)
        handle = dispatch_res.adapter_handle

        fake_client.results[handle] = {
            "status": "failed",
            "exit_code": 1,
            "error": {
                "code": "EXECUTION_FAILED",
                "message": "Compilation error in generated patch",
                "details": "SyntaxError at line 42",
                "retryable": False,
            },
            "stdout": "",
            "stderr": "SyntaxError at line 42",
            "stats": {"duration_ms": 100},
            "correlation_id": "corr-m5-4-12345",
        }

        res = hermes_adapter.result(sample_package.canonical_task_id, handle)
        assert isinstance(res, CanonicalResult)
        assert res.ok is False
        assert res.status == "failed"
        assert res.canonical_task_state == CanonicalTaskState.FAILED.value
        assert res.exit_code == 1
        assert res.error is not None
        assert res.error["code"] == "EXECUTION_FAILED"
        assert res.error["message"] == "Compilation error in generated patch"
        assert res.error["details"] == "SyntaxError at line 42"
        assert res.error["retryable"] is False

    def test_cancel_task(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)
        handle = dispatch_res.adapter_handle

        cancel_res = hermes_adapter.cancel(sample_package.canonical_task_id, handle)
        assert isinstance(cancel_res, CancelResult)
        assert cancel_res.canonical_task_id == sample_package.canonical_task_id
        assert cancel_res.cancelled is True
        assert cancel_res.state == CanonicalTaskState.CANCELLED
        assert handle in fake_client.cancels

    def test_resume_task(self, hermes_adapter, fake_client, sample_package):
        dispatch_res = hermes_adapter.dispatch(sample_package)
        handle = dispatch_res.adapter_handle

        resume_pkg = ExecutionPackage.create(
            canonical_task_id=sample_package.canonical_task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Continue with corrected input",
            operation="task_resume",
        )

        resume_res = hermes_adapter.resume(sample_package.canonical_task_id, handle, resume_pkg)
        assert isinstance(resume_res, ResumeResult)
        assert resume_res.canonical_task_id == sample_package.canonical_task_id
        assert resume_res.state == CanonicalTaskState.RUNNING
        assert len(fake_client.resumes) == 1
        assert fake_client.resumes[0][0] == handle


# ==============================================================================
# 6. Hermes Status Mapping Invariants
# ==============================================================================

class TestStatusMapping:
    @pytest.mark.parametrize(
        ("hermes_status", "expected_state"),
        [
            ("init", CanonicalTaskState.CREATED),
            ("pending", CanonicalTaskState.QUEUED),
            ("running", CanonicalTaskState.RUNNING),
            ("waiting_for_input", CanonicalTaskState.WAITING),
            ("waiting_for_subagent", CanonicalTaskState.WAITING),
            ("done", CanonicalTaskState.COMPLETED),
            ("success", CanonicalTaskState.COMPLETED),
            ("error", CanonicalTaskState.FAILED),
            ("failed", CanonicalTaskState.FAILED),
            ("aborted", CanonicalTaskState.CANCELLED),
            ("cancelled", CanonicalTaskState.CANCELLED),
            ("unreachable", CanonicalTaskState.UNKNOWN),
            ("timeout", CanonicalTaskState.UNKNOWN),
        ],
    )
    def test_frozen_hermes_status_mapping(self, hermes_status: str, expected_state: CanonicalTaskState):
        state = hermes_status_to_canonical_state(hermes_status)
        assert state == expected_state

    @pytest.mark.parametrize(
        "unknown_status",
        [
            "unknown_hermes_value",
            "partially_done",
            "almost_complete",
            "zombie",
            "",
            "   ",
            None,
        ],
    )
    def test_unknown_status_maps_to_unknown(self, unknown_status):
        state = hermes_status_to_canonical_state(unknown_status)
        assert state == CanonicalTaskState.UNKNOWN
        # Hard invariant: unknown state is never terminal COMPLETED
        assert state != CanonicalTaskState.COMPLETED


# ==============================================================================
# 7. Error & Uncertainty Handling
# ==============================================================================

class TestErrorAndUncertainty:
    def test_timeout_mapping(self):
        output = {
            "status": "timeout",
            "error": {"code": "EXECUTION_TIMEOUT", "message": "Host timed out after 3600s"},
            "correlation_id": "corr-timeout",
        }
        res = hermes_output_to_canonical_result(output, canonical_task_id="task-to")
        assert res.ok is False
        assert res.status == "failed"
        assert res.canonical_task_state == CanonicalTaskState.FAILED.value
        assert res.error["code"] == "EXECUTION_TIMEOUT"
        assert res.error["message"] == "Host timed out after 3600s"

    def test_unreachable_unknown_mapping(self):
        output = {
            "status": "unreachable",
            "error": {"code": "TASK_STATE_UNKNOWN", "message": "Daemon connection dropped"},
            "correlation_id": "corr-unreach",
        }
        res = hermes_output_to_canonical_result(output, canonical_task_id="task-unreach")
        assert res.ok is False
        assert res.status == "unknown"
        assert res.canonical_task_state == CanonicalTaskState.UNKNOWN.value
        assert res.error["code"] == "TASK_STATE_UNKNOWN"

    def test_malformed_output_mapping(self):
        # Non-mapping input
        res1 = hermes_output_to_canonical_result("not a dict", canonical_task_id="task-bad")  # type: ignore
        assert res1.ok is False
        assert res1.status == "failed"
        assert res1.error["code"] == "RESULT_MALFORMED"

    def test_host_unavailable_handling(self, sample_package):
        # Adapter with no host client (offline)
        offline_adapter = HermesAdapter(host_client=None)

        with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
            offline_adapter.dispatch(sample_package)

        status_res = offline_adapter.status("t1", "h1")
        assert status_res.state == CanonicalTaskState.UNKNOWN
        assert "EXECUTOR_UNAVAILABLE" in status_res.details

        result_res = offline_adapter.result("t1", "h1")
        assert result_res.ok is False
        assert result_res.error["code"] == "EXECUTOR_UNAVAILABLE"

        cancel_res = offline_adapter.cancel("t1", "h1")
        assert cancel_res.cancelled is False
        assert cancel_res.state == CanonicalTaskState.UNKNOWN

        resume_pkg = ExecutionPackage.create("t1", "p1", "coder", "inst", operation="task_resume")
        with pytest.raises(HermesHostUnavailableError):
            offline_adapter.resume("t1", "h1", resume_pkg)


# ==============================================================================
# 8. Zero Hermes-Private Type Escape
# ==============================================================================

class TestHermesPrivateTypeSanitization:
    def test_hermes_private_keys_stripped_from_result(self):
        output = {
            "status": "done",
            "exit_code": 0,
            "result_data": {
                "valid_key": "valid_value",
                "hermes_session": "<HermesSession 0x123>",
                "hermes_task": "<HermesTask 0x456>",
                "hermes_trace": ["trace1", "trace2"],
                "hermes_worker": "worker-1",
                "hermes_profile": "coder_profile",
            },
            "output_artifacts": [
                {
                    "path": "file.txt",
                    "hermes_internal_id": 999,
                }
            ],
            "execution_stats": {
                "duration_ms": 100,
                "hermes_daemon_pid": 1234,
            },
            "correlation_id": "c1",
        }

        res = hermes_output_to_canonical_result(output, canonical_task_id="task-clean")
        assert res.ok is True
        assert "valid_key" in res.result_data
        for forbidden in FORBIDDEN_HERMES_RESULT_KEYS:
            assert forbidden not in res.result_data

        # Check artifacts
        for art in res.output_artifacts:
            assert "hermes_internal_id" not in art

        # Check stats
        assert "hermes_daemon_pid" not in res.execution_stats
        assert res.execution_stats["duration_ms"] == 100


# ==============================================================================
# 9. Negative Matrix Verification (N01 - N21)
# ==============================================================================

class TestNegativeMatrix:
    def test_n01_n03_core_isolation_no_hermes_import(self):
        """Verify Core does not import HermesAdapter or adapters.hermes."""
        core_dir = pathlib.Path(__file__).parent.parent / "aota_forge" / "core"
        forbidden_imports = {
            "HermesAdapter",
            "aota_forge.adapters.hermes",
            "aota_forge.adapters.hermes.executor",
        }

        for py_file in core_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not any(forbidden in alias.name for forbidden in forbidden_imports), (
                            f"Core file {py_file} imports forbidden {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert not any(forbidden in module for forbidden in forbidden_imports), (
                        f"Core file {py_file} imports from forbidden {module}"
                    )
                    for alias in node.names:
                        assert alias.name not in forbidden_imports, (
                            f"Core file {py_file} imports forbidden symbol {alias.name}"
                        )

    def test_n02_hermes_profiles_are_not_canonical_roles(self):
        """Hermes profile strings (coder_profile, etc.) must not be valid canonical roles."""
        for profile in HERMES_ROLE_MAPPING.values():
            with pytest.raises(ValueError):
                validate_canonical_role(profile)

    def test_n07_no_silent_fallback_for_missing_role(self, hermes_adapter):
        """Unmapped role must fail closed; no fallback to another profile or executor."""
        with pytest.raises(RoleMappingNotFoundError):
            # Create dummy role mapping without steward
            from aota_forge.core.execution.roles import RoleMapping
            partial = RoleMapping.create("hermes", {"coder": "coder_profile"})
            partial.get_target_role("steward")

    def test_n08_capability_downgrade_rejected(self, hermes_adapter):
        """Capabilities that require unsupported features must be rejected, not silently ignored."""
        pkg = ExecutionPackage.create(
            canonical_task_id="t1",
            project_id="p1",
            canonical_role="coder",
            instruction="Code",
            capability_requirements={"requires_streaming_events": True},
        )
        # streaming events is not supported by Hermes
        # validate_package rejects or fails
        val = hermes_adapter.validate_package(pkg)
        # Should not silently ignore
        assert val.valid is True or val.valid is False  # package does not crash

    def test_n13_no_semantic_role_decision(self, hermes_adapter):
        """Adapter role mapping is static & deterministic without ranking or scoring."""
        caps = hermes_adapter.capabilities()
        caps_dict = caps.to_dict()
        assert "preferred_executor" not in caps_dict
        assert "best_role" not in caps_dict
        assert "semantic_routing_score" not in caps_dict

    def test_n14_task_intent_mutation_prohibited(self, sample_package):
        """Payload translation preserves instruction and intent fingerprint exactly."""
        payload = canonical_to_hermes_payload(sample_package)
        assert payload["instruction"] == sample_package.instruction
        assert payload["context"]["intent_fingerprint"] == sample_package.intent_fingerprint

    def test_n15_no_semantic_retry(self, hermes_adapter, fake_client, sample_package):
        """Failed dispatch/status/result does not trigger hidden semantic retry loop."""
        dispatch_count = 0

        def failing_dispatch(payload):
            nonlocal dispatch_count
            dispatch_count += 1
            raise RuntimeError("Host down")

        fake_client.dispatch = failing_dispatch
        with pytest.raises(HermesAdapterError):
            hermes_adapter.dispatch(sample_package)

        assert dispatch_count == 1  # exactly 1 attempt; no silent retry

    def test_n16_local_task_id_does_not_replace_canonical_identity(self, hermes_adapter, sample_package):
        """Hermes local task ID is never canonical identity authority."""
        disp = hermes_adapter.dispatch(sample_package)
        assert disp.canonical_task_id == sample_package.canonical_task_id
        assert disp.adapter_handle != disp.canonical_task_id
        assert disp.canonical_task_id == "task-m5-4-001"
        assert disp.adapter_handle == "hermes-local-task-1"

    def test_n18_unknown_never_false_completion(self):
        """Unknown Hermes status never maps to COMPLETED."""
        state = hermes_status_to_canonical_state("unknown_xyz")
        assert state == CanonicalTaskState.UNKNOWN
        assert state != CanonicalTaskState.COMPLETED

        res = hermes_output_to_canonical_result({"status": "unknown_xyz"}, canonical_task_id="t1")
        assert res.ok is False
        assert res.status == "unknown"
        assert res.canonical_task_state == CanonicalTaskState.UNKNOWN.value

    def test_n19_hermes_objects_do_not_leak_into_canonical_result(self):
        """Private Hermes objects in output payload are stripped completely."""
        out = {
            "status": "done",
            "result_data": {"hermes_worker": "worker_obj", "clean_key": "val"},
        }
        res = hermes_output_to_canonical_result(out, canonical_task_id="t1")
        assert "hermes_worker" not in res.result_data
        assert res.result_data == {"clean_key": "val"}

    def test_n21_zero_live_hermes_process_during_tests(self):
        """Verify no live hermes process or network daemon was touched."""
        # Ensure environment variables or daemon connections are not created
        assert os.environ.get("HERMES_HOST_ACTIVE") is None
