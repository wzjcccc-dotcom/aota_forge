"""M2/W3 static/structural acceptance gates (cheap construction validation).

Prove ARCHITECTURE boundaries hold in source, not just behavior:

- zero Hermes imports in the neutral durable/completion/runtime-core seam; the
  Hermes delivery adapter is a thin wrapper over the accepted W2 re-entry
  class only (no second CLI construction)
- no background daemon / scheduler / workflow engine / event bus
- TaskHandoff & model surface cannot carry origin_session_ref or admission
  scope; the durable record stores them as runtime evidence only
- production composition wires an EXPLICIT ExecutionStateStore dependency;
  no implicit global store; PRODUCTION_STORAGE_ENGINE_FROZEN stays false
- Hermes ledger / locator remain mechanical evidence, never AF authority
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
NEUTRAL_MODULES = [
    REPO / "aota_forge/core/execution/dispatcher.py",
    REPO / "aota_forge/core/execution/durable_state.py",
    REPO / "aota_forge/runtime/completion.py",
]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestNeutralSeamsHaveNoHermes:
    @pytest.mark.parametrize("module", NEUTRAL_MODULES)
    def test_zero_hermes_imports(self, module):
        assert module.is_file()
        offenders = {m for m in _imported_modules(module) if "hermes" in m}
        assert offenders == set(), offenders


class TestNoAutonomousFramework:
    def test_coordinator_module_shape_is_bounded(self):
        path = REPO / "aota_forge/runtime/completion.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        code = ast.unparse(tree)  # comments/docstrings excluded: code-level only
        for forbidden in ("while True", "threading", "asyncio", "Timer", "fork"):
            assert forbidden not in code, forbidden
        import aota_forge.runtime.completion as completion

        assert completion.BACKGROUND_AUTONOMOUS_LOOP_IMPLEMENTED is False
        assert completion.WORKFLOW_ENGINE_CREATED is False
        assert completion.COMPLETION_INBOX_FRAMEWORK_CREATED is False
        assert completion.PERSISTENT_EVENT_BUS_CREATED is False
        assert completion.SCHEDULER_PLATFORM_CREATED is False

    def test_authority_markers(self):
        import aota_forge.core.execution.durable_state as durable
        import aota_forge.runtime.completion as completion

        assert completion.AF_DURABLE_RECORD_IS_CANONICAL_RUNTIME_TRUTH is True
        assert completion.HERMES_LOCATOR_IS_AF_AUTHORITY is False
        assert completion.HERMES_LEDGER_IS_AF_AUTHORITY is False
        assert durable.ADMISSION_SCOPE_IS_AUTHORITY is False
        assert durable.ADMISSION_SCOPE_MODEL_SETTABLE is False
        assert durable.PRODUCTION_STORAGE_ENGINE_FROZEN is False
        assert durable.FILE_BACKED_IS_PRODUCTION_DEFAULT is False

    def test_recovery_and_delivery_are_bounded_once_operations(self):
        from aota_forge.runtime.completion import DurableCompletionCoordinator

        for name in ("recover_once", "deliver_pending_once", "admit_dispatch"):
            assert callable(getattr(DurableCompletionCoordinator, name))
        loopish = [n for n in dir(DurableCompletionCoordinator) if n in {"run_forever", "start", "run", "loop", "watch"}]
        assert loopish == []


class TestTaskHandoffNotContaminated:
    def test_handoff_keys_have_no_session_or_scope_fields(self):
        from aota_forge.work_plane.handoff import TaskHandoff
        from aota_forge.work_plane.roles import AgentWorkRole

        handoff = TaskHandoff(
            work_role=AgentWorkRole.CODER,
            task_kind="gate",
            objective="prove the handoff cannot carry runtime session binding",
            bounded_scope="tests",
            validation_expectations=("none",),
            semantic_stop_expectations=("none",),
        )
        keys = {k.lower() for k in handoff.to_dict()}
        for forbidden in ("origin_session_ref", "session", "admission", "scope_ref"):
            assert not any(forbidden in k for k in keys), (forbidden, sorted(keys))

    def test_package_has_no_admission_or_session_surface(self):
        import dataclasses

        from aota_forge.core.execution.package import ExecutionPackage

        field_names = {f.name for f in dataclasses.fields(ExecutionPackage)}
        assert "admission_scope" not in field_names
        assert "origin_session_ref" not in field_names
        # A package round-trip never gains the accounting field:
        pkg = ExecutionPackage.create(
            canonical_task_id="t",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="i",
        )
        assert "admission_scope" not in pkg.to_dict()
        with pytest.raises(TypeError):
            ExecutionPackage.__init__(  # type: ignore[call-arg]
                **{f.name: getattr(pkg, f.name) for f in dataclasses.fields(pkg)},
                admission_scope="model-chosen",
            )

    def test_dispatcher_origin_ref_is_constructor_runtime_side(self):
        import aota_forge.core.execution.dispatcher as dispatcher_module

        signature = inspect.signature(dispatcher_module.ExecutionDispatcher.__init__)
        assert "origin_session_ref" in signature.parameters
        dispatch_params = set(inspect.signature(dispatcher_module.ExecutionDispatcher.dispatch).parameters)
        assert "origin_session_ref" not in dispatch_params
        assert "admission_scope" not in dispatch_params

    def test_handoff_source_has_no_session_binding_field(self):
        source = (REPO / "aota_forge/work_plane/handoff.py").read_text(encoding="utf-8")
        assert "origin_session_ref" not in source


class TestHermesTransportIsThinWrapper:
    def test_no_second_cli_construction(self):
        path = REPO / "aota_forge/adapters/hermes/delivery.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        code = ast.unparse(tree)
        for forbidden in ("subprocess", "Popen", "--resume", "query-file", "build_exact_reentry_argv"):
            assert forbidden not in code, forbidden
        assert "HermesExactSessionReentry" in code  # the accepted seam is used

    def test_outcome_map_is_bounded(self):
        from aota_forge.adapters.hermes.delivery import _HERMES_OUTCOME_MAP
        from aota_forge.runtime.completion import DeliveryTransportOutcome

        assert set(_HERMES_OUTCOME_MAP) == {"completed", "retryable", "not_found", "failed", "unknown"}
        assert set(_HERMES_OUTCOME_MAP.values()) <= set(DeliveryTransportOutcome)


class TestProductionCompositionWiring:
    def test_explicit_store_and_origin_params(self):
        import aota_forge.composition.execution as execution

        signature = inspect.signature(execution.create_production_execution_dispatcher)
        assert "state_store" in signature.parameters
        assert "origin_session_ref" in signature.parameters
        assert signature.parameters["state_store"].default is None  # no implicit global
        source = inspect.getsource(execution)
        assert source.count("return ExecutionDispatcher(") == 1  # one dispatcher only

    def test_admission_mapping_deterministic(self):
        from aota_forge.composition.execution import (
            admission_limits_from_runtime_config,
            admission_scope_for_package,
        )
        from aota_forge.core.execution.package import ExecutionPackage
        from aota_forge.runtime.config import _parse_bindings_dict, RuntimeConfig

        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "hermes"
            exe.write_text("#!/bin/sh\nexit 0\n")
            exe.chmod(0o755)
            config = RuntimeConfig(
                executor="hermes",
                executable=str(exe),
                concurrency=2,
                provider=None,
                model=None,
                bindings=_parse_bindings_dict(
                    {
                        "analyst": {"profile": "aota-worker"},
                        "coder": {"profile": "aota-worker", "concurrency": 3},
                        "reviewer": {"profile": "aota-worker"},
                        "project-steward": {"profile": "aota-worker"},
                        "task-main": {"profile": "aota-task-main"},
                    },
                    executor="hermes",
                    executable=str(exe),
                    default_concurrency=2,
                    default_provider=None,
                    default_model=None,
                    default_toolsets=["aota"],
                ),
            )
        limits = admission_limits_from_runtime_config(config)
        assert limits["hermes:coder"] == 3
        assert limits["hermes:analyst"] == 2
        package = ExecutionPackage.create(
            canonical_task_id="wire-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="resolve",
        )
        assert admission_scope_for_package(config)(package) == "hermes:coder"
