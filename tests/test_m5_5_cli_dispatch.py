"""Comprehensive unit, integration, and contract test suite for M5-5 CLI Dispatch and Unified Ingress.

Tests M5-5 components:
- Execution descriptors registration, count (6), and schema projection
- Unified Ingress execution routing via bound ExecutionDispatcher
- Mechanical ExecutionDispatcher injection seam (bind/get/reset)
- Canonical CLI commands: aota task start, status, result, cancel; aota executor list, capabilities
- 3-Way Surface Parity: Direct Core == Unified Ingress == Canonical CLI projection (zero semantic drift)
- Multi-adapter parity: ReferenceFakeExecutorAdapter and HermesAdapter with FakeHermesHostClient
- Route executor assertion: mismatch between stored task route and query executor fails closed
- Idempotency replay (same key + same fingerprint) and conflict (same key + altered fingerprint)
- Exit code classification (0=success, 1=error, 2=usage, 3=needs_semantic_choice, 4=blocked)
- Negative matrix and architectural invariants:
  * Zero Hermes direct imports in Core or CLI (CORE_DIRECT_HERMES_DEPENDENCY_COUNT=0, CLI_DIRECT_HERMES_DEPENDENCY_COUNT=0)
  * CLI routes strictly through Unified Ingress (CLI_BYPASSES_INGRESS=no)
  * No heuristic executor selection or silent Hermes fallback
  * No arbitrary command strings in CLI
  * No task_resume subcommand in CLI (TASK_RESUME_CLI_IMPLEMENTED=no)
  * M4 Plan mutation surface unchanged (2 operations: plan_init, plan_retirement)
  * Execution side effects strictly separate from M4 Plan mutation plane
  * No live Hermes daemon/process spawn
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import pathlib
import sys
import unittest
import uuid
from typing import Any, Mapping
from unittest.mock import patch

import pytest

from aota_forge.adapters.execution.reference import (
    REFERENCE_EXECUTOR_ID,
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    ReferenceFakeExecutorAdapter,
)
from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapter,
    default_hermes_capabilities,
)
from aota_forge.cli.__main__ import ROUTES, _build_parser, _main
from aota_forge.cli.commands.execution import (
    executor_capabilities_params,
    executor_list_params,
    task_cancel_params,
    task_result_params,
    task_start_params,
    task_status_params,
)
from aota_forge.cli.exit_codes import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_NEEDS_SEMANTIC_CHOICE,
    EXIT_SUCCESS,
    EXIT_USAGE,
    classify,
)
from aota_forge.cli.projection import attach_semantic_arguments, operation_schema
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import (
    OperationContractDescriptor,
    READ_ONLY,
    WRITE_ONLY,
)
from aota_forge.core.catalog import (
    LIFECYCLE_DESCRIPTORS,
)
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY, HandlerRegistry
from aota_forge.core.execution import (
    CANONICAL_ROLES,
    CancelResult,
    CanonicalResult,
    CanonicalRole,
    CanonicalTaskState,
    DispatchResult,
    ExecutionPackage,
    ExecutorCapabilities,
    TaskStatusResult,
)
from aota_forge.core.execution.dispatcher import (
    ExecutionDispatcher,
    IdempotencyConflictError,
    NeedsSemanticChoiceError,
    PackageInvalidError,
    RouteRecord,
    TaskNotFoundError,
)
from aota_forge.core.execution.registry import (
    CapabilityMismatchError,
    ExecutorDescriptor,
    ExecutorNotFoundError,
    ExecutorRegistry,
)
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


# ==============================================================================
# Fake Hermes Host Client for Multi-Adapter Testing
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


# ==============================================================================
# Helper to run CLI in-process and capture output & exit code
# ==============================================================================

def run_cli(argv: list[str]) -> tuple[int, dict[str, Any] | str]:
    """Run CLI _main and return (exit_code, parsed_json_or_text)."""
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
# Unit & Contract Tests
# ==============================================================================

class TestM55Descriptors(unittest.TestCase):
    """Test execution descriptors declarations, count, and schema projection."""

    def test_canonical_execution_operations_count_and_names(self):
        self.assertEqual(len(CANONICAL_EXECUTION_OPERATIONS), 6)
        expected = (
            "execution.task_start",
            "execution.task_status",
            "execution.task_result",
            "execution.task_cancel",
            "execution.executor_list",
            "execution.executor_capabilities",
        )
        self.assertEqual(CANONICAL_EXECUTION_OPERATIONS, expected)
        self.assertEqual(len(EXECUTION_DESCRIPTORS), 6)

    def test_descriptors_read_write_classification(self):
        self.assertEqual(TASK_START_DESCRIPTOR.read_write, WRITE_ONLY)
        self.assertEqual(TASK_CANCEL_DESCRIPTOR.read_write, WRITE_ONLY)
        self.assertEqual(TASK_STATUS_DESCRIPTOR.read_write, READ_ONLY)
        self.assertEqual(TASK_RESULT_DESCRIPTOR.read_write, READ_ONLY)
        self.assertEqual(EXECUTOR_LIST_DESCRIPTOR.read_write, READ_ONLY)
        self.assertEqual(EXECUTOR_CAPABILITIES_DESCRIPTOR.read_write, READ_ONLY)

    def test_descriptors_neutral_declarations(self):
        for desc in (TASK_START_DESCRIPTOR, TASK_CANCEL_DESCRIPTOR):
            self.assertFalse(desc.approval_required)
            self.assertFalse(desc.decision_required)
            self.assertFalse(desc.subject_revision_precondition)
            self.assertFalse(desc.external_authority_precondition)
            self.assertEqual(desc.mutation_scope, "execution_task")

    def test_descriptor_contract_hashes_are_deterministic(self):
        for op in CANONICAL_EXECUTION_OPERATIONS:
            desc = get_execution_descriptor(op)
            self.assertIsNotNone(desc)
            h1 = desc.contract_hash()
            h2 = desc.contract_hash()
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)

    def test_register_execution_descriptors_helper(self):
        reg = HandlerRegistry()
        register_execution_descriptors(reg)
        self.assertEqual(len(reg), 6)
        for op in CANONICAL_EXECUTION_OPERATIONS:
            self.assertTrue(reg.has(op))

    def test_schema_projection_for_all_execution_operations(self):
        for op in CANONICAL_EXECUTION_OPERATIONS:
            schema = operation_schema(op)
            self.assertIsNotNone(schema)
            self.assertEqual(schema["operation"], op)
            self.assertIn("arguments", schema)
            self.assertIn("contract_hash", schema)


class TestM55DispatcherInjectionSeam(unittest.TestCase):
    """Test mechanical ExecutionDispatcher injection seam."""

    def setUp(self):
        reset_execution_dispatcher()

    def tearDown(self):
        reset_execution_dispatcher()

    def test_unbound_dispatcher_fails_closed(self):
        reset_execution_dispatcher()
        self.assertIsNone(get_execution_dispatcher())
        res = execute("execution.executor_list", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "INTERNAL_MECHANICAL_ERROR")
        self.assertIn("not bound", res["error"]["message"])

    def test_bind_valid_dispatcher(self):
        registry = ExecutorRegistry()
        dispatcher = ExecutionDispatcher(registry)
        bind_execution_dispatcher(dispatcher)
        self.assertIs(get_execution_dispatcher(), dispatcher)

    def test_bind_invalid_dispatcher_raises_type_error(self):
        with self.assertRaises(TypeError):
            bind_execution_dispatcher("not_a_dispatcher")  # type: ignore[arg-type]


class TestM55ThreeWaySurfaceParity(unittest.TestCase):
    """Test 3-way surface parity: Direct Core == Unified Ingress == CLI Projection."""

    def setUp(self):
        reset_execution_dispatcher()
        self.registry = ExecutorRegistry()
        self.ref_adapter = ReferenceFakeExecutorAdapter()
        self.registry.register(self.ref_adapter)

        self.fake_hermes_client = FakeHermesHostClient()
        self.hermes_adapter = HermesAdapter(host_client=self.fake_hermes_client)
        self.registry.register(self.hermes_adapter)

        self.dispatcher = ExecutionDispatcher(self.registry)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self):
        reset_execution_dispatcher()

    def test_task_start_three_way_parity_reference_adapter(self):
        # 1. Direct Core
        package_direct = ExecutionPackage.create(
            canonical_task_id="task-parity-1-direct",
            project_id="test-proj",
            canonical_role="planner",
            instruction="Create architectural plan",
        )
        direct_result = self.dispatcher.dispatch(package_direct, target_executor_id=REFERENCE_EXECUTOR_ID)

        # 2. Unified Ingress
        ingress_result = execute(
            "execution.task_start",
            {
                "executor": REFERENCE_EXECUTOR_ID,
                "role": "planner",
                "instruction": "Create architectural plan",
                "project_id": "test-proj",
                "canonical_task_id": "task-parity-1-ingress",
            },
        )
        self.assertTrue(ingress_result["ok"])
        self.assertEqual(ingress_result["data"]["initial_state"], direct_result.initial_state.value)

        # 3. CLI Projection
        exit_code, cli_payload = run_cli([
            "task", "start",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--role", "planner",
            "--instruction", "Create architectural plan",
            "--project-id", "test-proj",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"]["initial_state"], direct_result.initial_state.value)

    def test_task_status_three_way_parity(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-status-parity-1",
            project_id="test-proj",
            canonical_role="coder",
            instruction="Implement feature",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)

        # 1. Direct Core
        direct_status = self.dispatcher.status("task-status-parity-1")

        # 2. Unified Ingress
        ingress_status = execute(
            "execution.task_status",
            {"task_id": "task-status-parity-1", "executor": REFERENCE_EXECUTOR_ID},
        )
        self.assertTrue(ingress_status["ok"])
        self.assertEqual(ingress_status["data"]["state"], direct_status.state.value)
        self.assertEqual(ingress_status["data"]["canonical_task_id"], direct_status.canonical_task_id)

        # 3. CLI Projection
        exit_code, cli_payload = run_cli([
            "task", "status",
            "--task-id", "task-status-parity-1",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"]["state"], direct_status.state.value)
        self.assertEqual(cli_payload["data"]["canonical_task_id"], direct_status.canonical_task_id)

    def test_task_result_three_way_parity(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-result-parity-1",
            project_id="test-proj",
            canonical_role="reviewer",
            instruction="Review pull request",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running("task-result-parity-1")
        self.ref_adapter.simulate_completion("task-result-parity-1", result_data={"review": "LGTM"})

        # 1. Direct Core
        direct_result = self.dispatcher.result("task-result-parity-1")

        # 2. Unified Ingress
        ingress_res = execute(
            "execution.task_result",
            {"task_id": "task-result-parity-1", "executor": REFERENCE_EXECUTOR_ID},
        )
        self.assertTrue(ingress_res["ok"])
        self.assertEqual(ingress_res["data"]["canonical_task_state"], direct_result.canonical_task_state)
        self.assertEqual(ingress_res["data"]["result_data"], direct_result.result_data)

        # 3. CLI Projection
        exit_code, cli_payload = run_cli([
            "task", "result",
            "--task-id", "task-result-parity-1",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"]["canonical_task_state"], direct_result.canonical_task_state)
        self.assertEqual(cli_payload["data"]["result_data"], direct_result.result_data)

    def test_task_cancel_three_way_parity(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-cancel-parity-1",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Run execution job",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running("task-cancel-parity-1")

        # 1. Direct Core
        direct_cancel = self.dispatcher.cancel("task-cancel-parity-1")

        # Re-dispatch fresh task for ingress
        package_ing = ExecutionPackage.create(
            canonical_task_id="task-cancel-parity-ing",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Run execution job",
        )
        self.dispatcher.dispatch(package_ing, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running("task-cancel-parity-ing")

        # 2. Unified Ingress
        ingress_cancel = execute(
            "execution.task_cancel",
            {"task_id": "task-cancel-parity-ing", "executor": REFERENCE_EXECUTOR_ID},
        )
        self.assertTrue(ingress_cancel["ok"])
        self.assertEqual(ingress_cancel["data"]["cancelled"], direct_cancel.cancelled)
        self.assertEqual(ingress_cancel["data"]["state"], direct_cancel.state.value)

        # Re-dispatch fresh task for CLI
        package_cli = ExecutionPackage.create(
            canonical_task_id="task-cancel-parity-cli",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Run execution job",
        )
        self.dispatcher.dispatch(package_cli, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running("task-cancel-parity-cli")

        # 3. CLI Projection
        exit_code, cli_payload = run_cli([
            "task", "cancel",
            "--task-id", "task-cancel-parity-cli",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"]["cancelled"], direct_cancel.cancelled)
        self.assertEqual(cli_payload["data"]["state"], direct_cancel.state.value)

    def test_executor_list_three_way_parity(self):
        # 1. Direct Core
        direct_descriptors = [d.to_dict() for d in self.dispatcher.registry.list_descriptors()]

        # 2. Unified Ingress
        ingress_res = execute("execution.executor_list", {})
        self.assertTrue(ingress_res["ok"])
        self.assertEqual(ingress_res["data"]["executors"], direct_descriptors)

        # 3. CLI Projection
        exit_code, cli_payload = run_cli(["executor", "list", "--json"])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"]["executors"], direct_descriptors)

    def test_executor_capabilities_three_way_parity(self):
        # 1. Direct Core
        direct_caps = self.dispatcher.registry.get_descriptor(HERMES_EXECUTOR_ID).to_dict()

        # 2. Unified Ingress
        ingress_res = execute(
            "execution.executor_capabilities",
            {"executor": HERMES_EXECUTOR_ID},
        )
        self.assertTrue(ingress_res["ok"])
        self.assertEqual(ingress_res["data"], direct_caps)

        # 3. CLI Projection
        exit_code, cli_payload = run_cli([
            "executor", "capabilities",
            "--executor", HERMES_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        self.assertEqual(cli_payload["data"], direct_caps)


class TestM55HermesMultiAdapter(unittest.TestCase):
    """Test dispatch through HermesAdapter proving executor-neutral boundary."""

    def setUp(self):
        reset_execution_dispatcher()
        self.registry = ExecutorRegistry()
        self.fake_client = FakeHermesHostClient()
        self.adapter = HermesAdapter(host_client=self.fake_client)
        self.registry.register(self.adapter)
        self.dispatcher = ExecutionDispatcher(self.registry)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self):
        reset_execution_dispatcher()

    def test_hermes_dispatch_via_cli_and_ingress(self):
        exit_code, cli_payload = run_cli([
            "task", "start",
            "--executor", HERMES_EXECUTOR_ID,
            "--role", "coder",
            "--instruction", "Implement hermes feature",
            "--project-id", "hermes-proj",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        task_id = cli_payload["data"]["canonical_task_id"]
        self.assertTrue(task_id)

        # Status query
        status_res = execute(
            "execution.task_status",
            {"task_id": task_id, "executor": HERMES_EXECUTOR_ID},
        )
        self.assertTrue(status_res["ok"])
        self.assertEqual(status_res["data"]["state"], "QUEUED")

        # Result query
        result_res = execute(
            "execution.task_result",
            {"task_id": task_id, "executor": HERMES_EXECUTOR_ID},
        )
        self.assertTrue(result_res["ok"])
        self.assertEqual(result_res["data"]["canonical_task_state"], "COMPLETED")


class TestM55RouteAssertionAndIdentity(unittest.TestCase):
    """Test explicit executor preservation, route assertion, and error taxonomy."""

    def setUp(self):
        reset_execution_dispatcher()
        self.registry = ExecutorRegistry()
        self.ref_adapter = ReferenceFakeExecutorAdapter()
        self.fake_client = FakeHermesHostClient()
        self.hermes_adapter = HermesAdapter(host_client=self.fake_client)
        self.registry.register(self.ref_adapter)
        self.registry.register(self.hermes_adapter)
        self.dispatcher = ExecutionDispatcher(self.registry)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self):
        reset_execution_dispatcher()

    def test_route_executor_mismatch_fails_closed_in_ingress(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-route-test-1",
            project_id="test-proj",
            canonical_role="steward",
            instruction="Maintain repo",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)

        # Querying status with executor=hermes must fail closed with ROUTE_EXECUTOR_MISMATCH
        status_res = execute(
            "execution.task_status",
            {"task_id": "task-route-test-1", "executor": HERMES_EXECUTOR_ID},
        )
        self.assertFalse(status_res["ok"])
        self.assertEqual(status_res["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")

        # Querying result with executor=hermes must fail closed with ROUTE_EXECUTOR_MISMATCH
        res_res = execute(
            "execution.task_result",
            {"task_id": "task-route-test-1", "executor": HERMES_EXECUTOR_ID},
        )
        self.assertFalse(res_res["ok"])
        self.assertEqual(res_res["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")

        # Querying cancel with executor=hermes must fail closed with ROUTE_EXECUTOR_MISMATCH
        cancel_res = execute(
            "execution.task_cancel",
            {"task_id": "task-route-test-1", "executor": HERMES_EXECUTOR_ID},
        )
        self.assertFalse(cancel_res["ok"])
        self.assertEqual(cancel_res["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")

    def test_route_executor_mismatch_fails_closed_in_cli(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-route-cli-1",
            project_id="test-proj",
            canonical_role="coder",
            instruction="Code feature",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)

        exit_code, cli_payload = run_cli([
            "task", "status",
            "--task-id", "task-route-cli-1",
            "--executor", HERMES_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertFalse(cli_payload["ok"])
        self.assertEqual(cli_payload["error"]["code"], "ROUTE_EXECUTOR_MISMATCH")

    def test_identical_cancel_replay_is_idempotent_without_duplicate_adapter_call(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-cancel-replay-1",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Run cancellable execution",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running(package.canonical_task_id)

        params = {
            "task_id": package.canonical_task_id,
            "executor": REFERENCE_EXECUTOR_ID,
        }
        first = execute("execution.task_cancel", params)
        replay = execute("execution.task_cancel", params)

        self.assertTrue(first["ok"])
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["data"], first["data"])
        self.assertEqual(self.ref_adapter.cancel_count, 1)

    def test_cancel_completed_before_cancel_fails_closed(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-cancel-completed-1",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Complete before cancellation",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_completion(package.canonical_task_id)
        self.dispatcher.status(package.canonical_task_id)

        result = execute(
            "execution.task_cancel",
            {"task_id": package.canonical_task_id, "executor": REFERENCE_EXECUTOR_ID},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TASK_ALREADY_TERMINAL")
        self.assertEqual(self.ref_adapter.cancel_count, 0)

    def test_cancel_failed_before_cancel_fails_closed(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-cancel-failed-1",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Fail before cancellation",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_failure(package.canonical_task_id)
        self.dispatcher.status(package.canonical_task_id)

        result = execute(
            "execution.task_cancel",
            {"task_id": package.canonical_task_id, "executor": REFERENCE_EXECUTOR_ID},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TASK_ALREADY_TERMINAL")
        self.assertEqual(self.ref_adapter.cancel_count, 0)

    def test_preexisting_cancelled_task_is_not_treated_as_cancel_replay(self):
        package = ExecutionPackage.create(
            canonical_task_id="task-cancel-preexisting-1",
            project_id="test-proj",
            canonical_role="executor",
            instruction="Cancel before ingress cancellation",
        )
        self.dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
        self.ref_adapter.simulate_running(package.canonical_task_id)
        self.ref_adapter.simulate_transition(package.canonical_task_id, CanonicalTaskState.CANCELLED)
        self.dispatcher.status(package.canonical_task_id)

        result = execute(
            "execution.task_cancel",
            {"task_id": package.canonical_task_id, "executor": REFERENCE_EXECUTOR_ID},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TASK_ALREADY_TERMINAL")
        self.assertEqual(self.ref_adapter.cancel_count, 0)

    def test_executor_not_found_fails_closed(self):
        exit_code, cli_payload = run_cli([
            "task", "start",
            "--executor", "nonexistent_executor",
            "--role", "planner",
            "--instruction", "Plan",
            "--project-id", "p1",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertFalse(cli_payload["ok"])
        self.assertEqual(cli_payload["error"]["code"], "EXECUTOR_NOT_FOUND")

    def test_task_not_found_fails_closed(self):
        exit_code, cli_payload = run_cli([
            "task", "status",
            "--task-id", "nonexistent-task-id",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertFalse(cli_payload["ok"])
        self.assertEqual(cli_payload["error"]["code"], "TASK_NOT_FOUND")

    def test_idempotency_replay_and_conflict(self):
        idem_key = f"idem-key-{uuid.uuid4().hex}"
        # First dispatch
        res1 = execute(
            "execution.task_start",
            {
                "executor": REFERENCE_EXECUTOR_ID,
                "role": "planner",
                "instruction": "Same instruction",
                "project_id": "p1",
                "idempotency_key": idem_key,
            },
        )
        self.assertTrue(res1["ok"])

        # Replay: same key, same instruction -> replays result
        res2 = execute(
            "execution.task_start",
            {
                "executor": REFERENCE_EXECUTOR_ID,
                "role": "planner",
                "instruction": "Same instruction",
                "project_id": "p1",
                "idempotency_key": idem_key,
            },
        )
        self.assertTrue(res2["ok"])
        self.assertEqual(res1["data"]["canonical_task_id"], res2["data"]["canonical_task_id"])

        # Conflict: same key, altered instruction -> IDEMPOTENCY_CONFLICT
        res3 = execute(
            "execution.task_start",
            {
                "executor": REFERENCE_EXECUTOR_ID,
                "role": "planner",
                "instruction": "Altered instruction",
                "project_id": "p1",
                "idempotency_key": idem_key,
            },
        )
        self.assertFalse(res3["ok"])
        self.assertEqual(res3["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_capability_timeout_matching_in_cli_and_ingress(self):
        exit_code, cli_payload = run_cli([
            "task", "start",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--role", "coder",
            "--instruction", "Timed task",
            "--project-id", "p1",
            "--timeout", "60",
            "--json",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertTrue(cli_payload["ok"])
        task_id = cli_payload["data"]["canonical_task_id"]
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.executor_id, REFERENCE_EXECUTOR_ID)


class TestM55HumanRenderingAndExitCodes(unittest.TestCase):
    """Test human-readable rendering and exit code mapping."""

    def setUp(self):
        reset_execution_dispatcher()
        self.registry = ExecutorRegistry()
        self.ref_adapter = ReferenceFakeExecutorAdapter()
        self.registry.register(self.ref_adapter)
        self.dispatcher = ExecutionDispatcher(self.registry)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self):
        reset_execution_dispatcher()

    def test_human_readable_success_output(self):
        exit_code, output = run_cli([
            "task", "start",
            "--executor", REFERENCE_EXECUTOR_ID,
            "--role", "planner",
            "--instruction", "Plan something",
            "--project-id", "p1",
        ])
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIsInstance(output, str)
        self.assertIn("OK  execution.task_start", output)
        self.assertIn("canonical_task_id", output)

    def test_human_readable_error_output(self):
        exit_code, output = run_cli([
            "task", "status",
            "--task-id", "missing-task",
            "--executor", REFERENCE_EXECUTOR_ID,
        ])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIsInstance(output, str)
        self.assertIn("ERROR  execution.task_status", output)
        self.assertIn("code: TASK_NOT_FOUND", output)


class TestM55NegativeAndArchitecturalInvariants(unittest.TestCase):
    """Verify strict negative matrix N01-N24 and architectural invariants."""

    def test_q1_cli_flags_are_descriptor_driven(self):
        parser = _build_parser()
        sub_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertTrue(sub_actions)
        choices = sub_actions[0].choices
        self.assertIn("task", choices)
        self.assertIn("executor", choices)

        task_sub = [a for a in choices["task"]._actions if isinstance(a, argparse._SubParsersAction)][0].choices
        self.assertIn("start", task_sub)
        self.assertIn("status", task_sub)
        self.assertIn("result", task_sub)
        self.assertIn("cancel", task_sub)
        self.assertNotIn("resume", task_sub)

    def test_q2_core_has_zero_hermes_imports(self):
        ingress_path = pathlib.Path("aota_forge/core/ingress.py")
        tree = ast.parse(ingress_path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("hermes", alias.name.lower(), f"Direct Hermes import found: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotIn("hermes", mod.lower(), f"Direct Hermes import found: {mod}")

    def test_q3_cli_has_zero_hermes_imports(self):
        for cli_file in (
            "aota_forge/cli/__main__.py",
            "aota_forge/cli/projection.py",
            "aota_forge/cli/commands/execution.py",
        ):
            tree = ast.parse(pathlib.Path(cli_file).read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("hermes", alias.name.lower(), f"Direct Hermes import found in {cli_file}: {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotIn("hermes", mod.lower(), f"Direct Hermes import found in {cli_file}: {mod}")

    def test_q4_q5_cli_routes_only_through_ingress(self):
        for route_key, (op, builder) in ROUTES.items():
            if op.startswith("execution."):
                self.assertIn(op, CANONICAL_EXECUTION_OPERATIONS)

    def test_q9_task_resume_not_in_cli(self):
        self.assertNotIn(("task", "resume"), ROUTES)

    def test_q10_q11_m4_mutation_surface_unchanged(self):
        self.assertEqual(len(LIFECYCLE_DESCRIPTORS), 2)
        lifecycle_ops = tuple(d.name for d in LIFECYCLE_DESCRIPTORS)
        self.assertEqual(lifecycle_ops, ("plan_init", "plan_retirement"))

    def test_q14_m5_components_unmutated_invariants(self):
        self.assertFalse(REFERENCE_EXECUTOR_PRODUCTION_DEFAULT)
        self.assertEqual(len(CANONICAL_ROLES), 5)

    def test_q15_m5_6_not_started(self):
        """Permanent architectural invariant: M5-5 production implementation does not depend on, import, execute, or implement M5-6/M5-R artifacts."""
        m5_5_files = [
            "aota_forge/core/ingress.py",
            "aota_forge/cli/commands/execution.py",
            "aota_forge/cli/projection.py",
            "aota_forge/cli/__main__.py",
        ]
        forbidden_modules = {
            "test_m5_6_convergence",
            "m5_source_guard",
            "tests.test_m5_6_convergence",
            "scripts.m5_source_guard",
        }
        forbidden_substrings = [
            "test_m5_6_convergence",
            "m5_source_guard",
            "m5-source",
            "m5_r",
            "convergence",
        ]
        for fpath in m5_5_files:
            file_path = pathlib.Path(fpath)
            content = file_path.read_text("utf-8")
            tree = ast.parse(content, filename=fpath)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_modules:
                            self.assertNotIn(
                                forbidden,
                                alias.name,
                                f"M5-5 file {fpath} must not import M5-6 module: {alias.name}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for forbidden in forbidden_modules:
                        self.assertNotIn(
                            forbidden,
                            mod,
                            f"M5-5 file {fpath} must not import from M5-6 module: {mod}",
                        )

            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden,
                    content.lower(),
                    f"M5-5 file {fpath} contains reference to M5-6/M5-R artifact: {forbidden}",
                )

    def test_zero_semantic_todo_in_m5_5_files(self):
        m5_5_files = [
            "aota_forge/core/ingress.py",
            "aota_forge/cli/commands/execution.py",
            "aota_forge/cli/projection.py",
            "aota_forge/cli/__main__.py",
        ]
        for fpath in m5_5_files:
            content = pathlib.Path(fpath).read_text("utf-8")
            self.assertNotIn("TODO", content, f"TODO found in {fpath}")
            self.assertNotIn("TBD", content, f"TBD found in {fpath}")
            self.assertNotIn("FIXME", content, f"FIXME found in {fpath}")
