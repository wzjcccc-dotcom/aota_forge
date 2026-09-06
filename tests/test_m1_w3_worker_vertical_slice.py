"""M1/W3 convergence and one-shot Worker composition tests."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from aota_forge.adapters.hermes.executor import canonical_to_hermes_payload
from aota_forge.adapters.hermes.host_client import (
    HermesHostClient,
    HermesHostClientError,
)
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.mcp_transport import MCP_PUBLIC_TOOLS, create_shared_mcp_server
from aota_forge.runtime.config import RuntimeConfigError, get_default_runtime_config
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.work_plane.roles import AgentWorkRole


def _handoff() -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="m1-w3-test",
        objective="bounded worker objective",
        bounded_scope="runtime-smoke only",
        validation_expectations=("output matches",),
        semantic_stop_expectations=("stop on denial",),
    )


def test_w3_semantic_handoff_excludes_runtime_authority():
    names = {field.name for field in fields(TaskHandoff)}
    assert {"provider", "model", "profile", "project", "worktree"}.isdisjoint(names)
    assert {"provider", "model", "profile"}.isdisjoint(_handoff().to_dict())


def test_w3_coder_compiles_to_explicit_w1_binding():
    runtime = get_default_runtime_config(validate_executable=False)
    package = compile_handoff_to_execution_package(
        _handoff(),
        TrustedExecutionBinding(canonical_task_id="w3-task", project_id="aota_forge"),
    )
    payload = canonical_to_hermes_payload(package, runtime_config=runtime)
    binding = runtime.get_binding(AgentWorkRole.CODER)
    assert binding.profile == "aota-worker"
    assert payload["profile"] == "aota-worker"
    assert payload["provider"] == binding.provider
    assert payload["model"] == binding.model


def test_w3_host_accepts_compiler_metadata_but_rejects_real_artifacts(tmp_path: Path):
    runtime = get_default_runtime_config(validate_executable=False)
    package = compile_handoff_to_execution_package(
        _handoff(),
        TrustedExecutionBinding(canonical_task_id="w3-task", project_id="aota_forge"),
    )
    payload = canonical_to_hermes_payload(package, runtime_config=runtime)
    host = HermesHostClient("/unused", default_cwd=tmp_path, validate_launcher=False)
    host._validate_payload(payload)
    payload["artifacts"] = [{"path": "secret.txt"}]
    with pytest.raises(HermesHostClientError, match="artifact transport"):
        host._validate_payload(payload)


def test_w3_trusted_binding_pins_project_worktree_and_role(tmp_path: Path):
    handoff = _handoff()
    binding = build_worker_binding(
        root=tmp_path,
        project_id="aota_forge",
        worktree_id="w3-test-worktree",
        canonical_task_id="w3-task",
        handoff=handoff,
    )
    assert binding.project_id == "aota_forge"
    assert binding.worktree_id == "w3-test-worktree"
    assert binding.handoff == handoff
    assert binding.tool_surface.work_role == AgentWorkRole.CODER
    assert {authority.operation.name for authority in binding.read_authorities} == {
        "workspace.search",
        "workspace.read",
    }
    assert binding.mutation_authority is not None


def test_w3_shared_server_is_existing_restricted_surface(tmp_path: Path):
    binding = build_worker_binding(
        root=tmp_path,
        project_id="aota_forge",
        worktree_id="w3-test-worktree",
        canonical_task_id="w3-task",
        handoff=_handoff(),
    )
    server = create_shared_mcp_server(binding)
    assert [tool.name for tool in server._tool_manager.list_tools()] == list(MCP_PUBLIC_TOOLS)


def test_w3_missing_runtime_binding_fails_closed():
    full = get_default_runtime_config(validate_executable=False)
    partial = type(full)(
        executor=full.executor,
        executable=full.executable,
        concurrency=full.concurrency,
        provider=full.provider,
        model=full.model,
        bindings=(full.get_binding("coder"),),
    )

    class FakeHost:
        def dispatch(self, payload):
            return {"adapter_handle": "w3-fake", "status": "pending"}

    with pytest.raises(RuntimeConfigError, match="no runtime binding"):
        create_production_execution_dispatcher(host_client=FakeHost(), runtime_config=partial)


def test_w3_result_uses_canonical_governance_and_card():
    result = CanonicalResult.success(
        canonical_task_id="w3-task",
        executor_id="hermes",
        result_data={"output": "AOTA_M1_W3_OUTPUT=token"},
        correlation_id="w3-correlation",
    )
    governance = ResultGovernanceProjection.success()
    card = project_worker_result_card(result, governance, AgentWorkRole.CODER, summary="bounded success")
    assert card.task_ref == "w3-task"
    assert card.agent_work_role == AgentWorkRole.CODER
    assert card.outcome.value == "success"
    assert card.result_handoff_ref.ref == "w3-task"
