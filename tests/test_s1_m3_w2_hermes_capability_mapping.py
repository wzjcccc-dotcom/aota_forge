"""S1/M3/W2 - Hermes capability mapping and Core neutrality proof.

This module exercises the existing Hermes adapter through the existing Core
execution seam.  It deliberately does not add a second executor lifecycle or
change any production contract.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from aota_forge.adapters.hermes.executor import (
    HERMES_ADAPTER_KIND,
    HERMES_EXECUTOR_ID,
    HermesAdapter,
    canonical_role_to_hermes_profile,
)
from aota_forge.core.contracts.loader import (
    discover_canonical_project_root,
    load_capabilities,
    load_operations,
    load_results,
)
from aota_forge.core.execution import (
    CancelResult,
    CanonicalResult,
    CanonicalTaskState,
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry


PRIVATE_IMPLEMENTATION_FIELDS = frozenset(
    {
        "hermes_profile",
        "hermes_z",
        "profile_object",
        "profile_path",
        "profile_session",
        "persistent_task_main",
        "hermes_session",
        "task_main_session_id",
        "session_reentry",
        "profile_task_state",
        "one_shot_worker",
        "worker_invocation_type",
        "worker_profile",
        "worker_pid",
        "terminal_background",
        "process_registry",
        "process_registry_id",
        "notify_on_complete",
        "watch_pattern",
        "terminal_session",
        "pid",
        "hermes_session_id",
    }
)


# This is an evidence index, not an authority or a runtime registry.
HERMES_MAPPING_MATRIX: tuple[dict[str, str], ...] = (
    {
        "hermes_private_fact": "Hermes profile",
        "canonical_semantic": "canonical_role / advertised canonical roles",
        "adapter_private_mechanism": "Hermes role-to-profile mapping",
        "core_field_required": "no",
        "core_runtime_dependency": "no",
        "proof": "role projection through HermesAdapter and dispatcher",
        "result": "PASS",
    },
    {
        "hermes_private_fact": "persistent task-main",
        "canonical_semantic": "generic dispatch/resume lifecycle",
        "adapter_private_mechanism": "persistent orchestration/session re-entry",
        "core_field_required": "no",
        "core_runtime_dependency": "no",
        "proof": "canonical package fields and adapter lifecycle surface",
        "result": "PASS",
    },
    {
        "hermes_private_fact": "one-shot worker",
        "canonical_semantic": "canonical task identity, dispatch, status, result",
        "adapter_private_mechanism": "worker launcher and process lifetime",
        "core_field_required": "no",
        "core_runtime_dependency": "no",
        "proof": "same Core package/adapter types have no worker field",
        "result": "PASS",
    },
    {
        "hermes_private_fact": "background execution",
        "canonical_semantic": "supported async mode and canonical task states",
        "adapter_private_mechanism": "host transport/background process",
        "core_field_required": "no",
        "core_runtime_dependency": "no",
        "proof": "registry compatibility plus dispatcher status transition",
        "result": "PASS",
    },
    {
        "hermes_private_fact": "ProcessRegistry completion",
        "canonical_semantic": "adapter status/result boundary",
        "adapter_private_mechanism": "private process registry and notification queue",
        "core_field_required": "no",
        "core_runtime_dependency": "no",
        "proof": "injected host completion projected to TaskStatusResult/CanonicalResult",
        "result": "PASS",
    },
)


class MappingProofHermesHost:
    """Small injected host double; no Hermes process or daemon is started."""

    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []
        self.statuses: dict[str, Mapping[str, Any]] = {}
        self.results: dict[str, Mapping[str, Any]] = {}
        self.resume_calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatched.append(dict(payload))
        handle = "hermes-process-registry:proc-7"
        self.statuses[handle] = {"status": "pending"}
        return {
            "adapter_handle": handle,
            "status": "pending",
            "dispatch_time": "2026-08-27T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.statuses[adapter_handle]

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.results[adapter_handle]

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.resume_calls.append((adapter_handle, dict(payload)))
        self.statuses[adapter_handle] = {"status": "running"}
        return {"status": "running"}


def _package(
    task_id: str = "w2-task-1",
    *,
    operation: str = "task_dispatch",
    instruction: str = "prove adapter mapping",
    execution_mode: str = "async",
) -> ExecutionPackage:
    return ExecutionPackage.create(
        package_id=f"package-{task_id}-{operation}",
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation=operation,
        capability_requirements={"execution_mode": execution_mode, "isolation": "process"},
        idempotency_key=f"idempotency-{task_id}-{operation}",
        correlation_id=f"correlation-{task_id}",
    )


def _dispatcher(host: MappingProofHermesHost) -> tuple[ExecutionDispatcher, HermesAdapter]:
    adapter = HermesAdapter(host_client=host)
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry), adapter


def test_mapping_matrix_is_complete_and_deterministic() -> None:
    assert tuple(row["hermes_private_fact"] for row in HERMES_MAPPING_MATRIX) == (
        "Hermes profile",
        "persistent task-main",
        "one-shot worker",
        "background execution",
        "ProcessRegistry completion",
    )
    assert all(row["core_field_required"] == "no" for row in HERMES_MAPPING_MATRIX)
    assert all(row["core_runtime_dependency"] == "no" for row in HERMES_MAPPING_MATRIX)
    assert all(row["result"] == "PASS" for row in HERMES_MAPPING_MATRIX)


def test_canonical_schema_and_yaml_have_no_private_required_fields() -> None:
    canonical_types = (
        ExecutionPackage,
        ExecutorCapabilities,
        DispatchResult,
        TaskStatusResult,
        CanonicalResult,
    )
    schema_fields = {field.name for model in canonical_types for field in fields(model)}
    assert schema_fields.isdisjoint(PRIVATE_IMPLEMENTATION_FIELDS)
    assert "adapter_handle" in schema_fields
    assert "canonical_task_id" in schema_fields

    root = discover_canonical_project_root()
    capability_entries = load_capabilities(root)["contracts"]
    yaml_capability_fields = {key for entry in capability_entries for key in entry}
    assert not yaml_capability_fields.intersection(PRIVATE_IMPLEMENTATION_FIELDS)
    assert sum(
        len(set(entry).intersection(PRIVATE_IMPLEMENTATION_FIELDS))
        for entry in capability_entries
    ) == 0
    assert "adapter_kind" in yaml_capability_fields
    assert "supported_execution_modes" in yaml_capability_fields

    # The other canonical documents are loaded as a semantic cross-check, not
    # as a text denylist: private mechanics do not define operations/results.
    operation_fields = {key for entry in load_operations(root)["contracts"] for key in entry}
    result_fields = {key for entry in load_results(root)["contracts"] for key in entry}
    assert not operation_fields.intersection(PRIVATE_IMPLEMENTATION_FIELDS)
    assert not result_fields.intersection(PRIVATE_IMPLEMENTATION_FIELDS)


def test_hermes_role_profile_projection_uses_generic_core_role() -> None:
    host = MappingProofHermesHost()
    dispatcher, adapter = _dispatcher(host)
    package = _package()

    assert canonical_role_to_hermes_profile(package.canonical_role) == "coder_profile"
    assert adapter.capabilities().adapter_kind == HERMES_ADAPTER_KIND
    assert adapter.capabilities().executor_id == HERMES_EXECUTOR_ID

    dispatch = dispatcher.dispatch(package, target_executor_id=HERMES_EXECUTOR_ID)

    assert host.dispatched[0]["profile"] == "coder_profile"
    assert host.dispatched[0]["context"]["canonical_task_id"] == package.canonical_task_id
    assert package.canonical_role == "coder"
    assert package.canonical_role != host.dispatched[0]["profile"]
    assert dispatch.canonical_task_id == package.canonical_task_id
    assert dispatch.adapter_handle != dispatch.canonical_task_id


def test_persistent_and_one_shot_lifecycles_need_no_core_private_field() -> None:
    host = MappingProofHermesHost()
    dispatcher, adapter = _dispatcher(host)

    persistent_package = _package(task_id="persistent-task")
    persistent_dispatch = dispatcher.dispatch(persistent_package)
    assert persistent_dispatch.initial_state == CanonicalTaskState.QUEUED

    host.statuses[persistent_dispatch.adapter_handle] = {"status": "waiting_for_input"}
    assert dispatcher.status(persistent_package.canonical_task_id).state == CanonicalTaskState.WAITING

    resume_package = _package(
        task_id="persistent-task",
        operation="task_resume",
        instruction="re-enter with the next canonical input",
    )
    resumed = dispatcher.resume(persistent_package.canonical_task_id, resume_package)
    assert isinstance(resumed, ResumeResult)
    assert resumed.state == CanonicalTaskState.RUNNING
    assert host.resume_calls[0][1]["profile"] == "coder_profile"

    # A separate one-shot dispatch uses the same canonical package/dispatch
    # shape; no persistent/session/worker field is needed by Core.
    one_shot = _package(task_id="one-shot-task", instruction="one finite invocation")
    one_shot_dispatch = adapter.dispatch(one_shot)
    assert isinstance(one_shot_dispatch, DispatchResult)
    assert one_shot_dispatch.canonical_task_id == "one-shot-task"
    assert one_shot_dispatch.adapter_handle != one_shot_dispatch.canonical_task_id


def test_background_async_and_process_registry_completion_use_status_result_boundary() -> None:
    host = MappingProofHermesHost()
    dispatcher, _adapter = _dispatcher(host)
    package = _package(task_id="background-task")

    capabilities = dispatcher.registry.get_capabilities(HERMES_EXECUTOR_ID)
    assert capabilities.supports_mode("async")
    compatible, reasons = dispatcher.registry.check_compatibility(capabilities, package)
    assert compatible is True, reasons

    dispatch = dispatcher.dispatch(package, target_executor_id=HERMES_EXECUTOR_ID)
    host.statuses[dispatch.adapter_handle] = {
        "status": "done",
        "process_registry_id": "registry-entry-7",
        "pid": 731,
        "notify_on_complete": True,
    }
    status = dispatcher.status(package.canonical_task_id)
    assert isinstance(status, TaskStatusResult)
    assert status.state == CanonicalTaskState.COMPLETED
    assert status.canonical_task_id == package.canonical_task_id

    host.results[dispatch.adapter_handle] = {
        "status": "done",
        "result_data": {"answer": "completed through adapter"},
        "process_registry_id": "registry-entry-7",
        "terminal_background": True,
        "pid": 731,
        "correlation_id": package.correlation_id,
    }
    result = dispatcher.result(package.canonical_task_id)
    assert isinstance(result, CanonicalResult)
    assert result.ok is True
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert result.result_data == {"answer": "completed through adapter"}
    assert result.canonical_task_id == package.canonical_task_id


def test_hermes_private_output_is_projected_without_private_canonical_fields() -> None:
    output = {
        "status": "done",
        "result_data": {
            "answer": "public result",
            "hermes_session": {"id": "session-1"},
            "nested": {"hermes_profile": "coder_profile", "value": 1},
        },
        "output_artifacts": [
            {"path": "result.json", "hermes_worker": "worker-1"},
        ],
        "execution_stats": {"duration_ms": 4, "hermes_daemon_pid": 731},
        # These are deliberately top-level host metadata.  The adapter
        # projects only the canonical result envelope and does not require
        # Core to interpret these transport controls.
        "hermes_profile": "coder_profile",
        "hermes_session": "session-1",
        "process_registry": "registry-entry-7",
        "terminal_background": True,
        "pid": 731,
    }

    host = MappingProofHermesHost()
    dispatcher, _adapter = _dispatcher(host)
    package = _package(task_id="projection-task")
    dispatch = dispatcher.dispatch(package)
    host.results[dispatch.adapter_handle] = output

    result = dispatcher.result(package.canonical_task_id)
    assert result.ok is True
    assert result.result_data["answer"] == "public result"
    assert "hermes_session" not in result.result_data
    assert "hermes_profile" not in result.result_data["nested"]
    assert "hermes_worker" not in result.output_artifacts[0]
    assert "hermes_daemon_pid" not in result.execution_stats
    assert all(key not in result.to_dict() for key in PRIVATE_IMPLEMENTATION_FIELDS)
    assert result.canonical_task_id == package.canonical_task_id


def test_core_import_boundary_has_no_hermes_implementation_dependency() -> None:
    core_root = Path(__file__).resolve().parent.parent / "aota_forge" / "core"
    forbidden_module = ("aota_forge", "adapters", "hermes")
    violations: list[str] = []

    for source_path in sorted(core_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or "").split(".")]
            else:
                continue
            if any(tuple(module[:3]) == forbidden_module for module in modules):
                violations.append(str(source_path))

    assert violations == []


class ProviderPrivateAdapter(ExecutorAdapter):
    """Adversarial local provider object, not a remote executor lifecycle."""

    def __init__(self, private_values: Mapping[str, Any]) -> None:
        self.remote_provider_session = private_values["remote_provider_session"]
        self.provider_request_id = private_values["provider_request_id"]
        self.remote_poll_cursor = private_values["remote_poll_cursor"]
        self.remote_queue_internal_state = private_values["remote_queue_internal_state"]
        self.provider_worker_handle = private_values["provider_worker_handle"]
        self._capabilities = ExecutorCapabilities(
            executor_id="hypothetical-provider",
            adapter_kind="provider_adapter",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=False,
            supports_artifact_transport=True,
        )

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle="provider-private-handle",
            initial_state=CanonicalTaskState.ACCEPTED,
            dispatch_time="2026-08-27T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        return TaskStatusResult(canonical_task_id, CanonicalTaskState.ACCEPTED)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        return CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id=self._capabilities.executor_id,
            result_data={"provider_output": "opaque"},
            correlation_id=f"correlation-{canonical_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(canonical_task_id, False, CanonicalTaskState.UNKNOWN)

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        return ResumeResult(canonical_task_id, CanonicalTaskState.RUNNING)


def test_non_hermes_provider_private_fields_do_not_drive_core_semantics() -> None:
    private_a = {
        "remote_provider_session": "session-a",
        "provider_request_id": "request-a",
        "remote_poll_cursor": "cursor-a",
        "remote_queue_internal_state": {"queued": True},
        "provider_worker_handle": "worker-a",
    }
    private_b = {
        "remote_provider_session": "session-b",
        "provider_request_id": "request-b",
        "remote_poll_cursor": "cursor-b",
        "remote_queue_internal_state": {"queued": False},
        "provider_worker_handle": "worker-b",
    }
    adapter_a = ProviderPrivateAdapter(private_a)
    adapter_b = ProviderPrivateAdapter(private_b)
    package = _package(task_id="provider-neutrality", execution_mode="sync")

    registry_a = ExecutorRegistry()
    registry_a.register(adapter_a)
    registry_b = ExecutorRegistry()
    registry_b.register(adapter_b)

    caps_a = registry_a.get_capabilities("hypothetical-provider")
    caps_b = registry_b.get_capabilities("hypothetical-provider")
    assert caps_a == caps_b
    assert registry_a.check_compatibility(caps_a, package) == registry_b.check_compatibility(caps_b, package)
    assert registry_a.resolve(package).to_dict() == registry_b.resolve(package).to_dict()

    dispatcher_a = ExecutionDispatcher(registry_a)
    dispatcher_b = ExecutionDispatcher(registry_b)
    dispatch_a = dispatcher_a.dispatch(package)
    dispatch_b = dispatcher_b.dispatch(package)
    assert dispatch_a.to_dict() == dispatch_b.to_dict()
    assert dispatcher_a.status(package.canonical_task_id).to_dict() == dispatcher_b.status(
        package.canonical_task_id
    ).to_dict()
    assert dispatcher_a.result(package.canonical_task_id).to_dict() == dispatcher_b.result(
        package.canonical_task_id
    ).to_dict()


def test_identity_and_capability_domains_remain_separate() -> None:
    host = MappingProofHermesHost()
    dispatcher, _adapter = _dispatcher(host)
    package = _package(task_id="identity-task")
    dispatch = dispatcher.dispatch(package, target_executor_id=HERMES_EXECUTOR_ID)
    route = dispatcher.get_route(package.canonical_task_id)

    assert route.canonical_task_id == "identity-task"
    assert route.executor_id == HERMES_EXECUTOR_ID
    assert route.adapter_handle == "hermes-process-registry:proc-7"
    assert route.adapter_handle != route.canonical_task_id
    assert route.dispatch_attempt_id != route.canonical_task_id
    assert route.package_id != route.canonical_task_id
    assert route.correlation_id != route.canonical_task_id
    assert route.to_dict()["canonical_task_id"] != route.to_dict()["adapter_handle"]
    assert "process_registry_id" not in route.to_dict()
    assert "pid" not in route.to_dict()

    caps = dispatcher.registry.get_capabilities(HERMES_EXECUTOR_ID)
    assert caps.executor_id == HERMES_EXECUTOR_ID
    assert caps.adapter_kind == HERMES_ADAPTER_KIND
    assert caps.executor_id != "aota.capability.executor"
    assert "coder" in caps.supported_canonical_roles
    assert not hasattr(caps, "hermes_profile")
    assert not hasattr(caps, "profile")
    assert dispatch.canonical_task_id == route.canonical_task_id
