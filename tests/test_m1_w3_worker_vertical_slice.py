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
from aota_forge.runtime.config import RuntimeBinding, RuntimeConfig, RuntimeConfigError
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.work_plane.roles import AgentWorkRole


def _test_runtime_config(tmp_path: Path) -> RuntimeConfig:
    """Explicit test-owned operator config over a bounded temp executable."""
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    import stat

    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    executable = str(exe)
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor="hermes",
            profile="aota-worker" if role != "task-main" else "aota-task-main",
            provider="aota-test-provider",
            model="aota-test-model",
            concurrency=1,
            executable=executable,
            toolsets=("aota",) if role != "task-main" else None,
        )
        for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
    )
    return RuntimeConfig(
        executor="hermes",
        executable=executable,
        concurrency=1,
        provider="aota-test-provider",
        model="aota-test-model",
        bindings=bindings,
    )


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


def test_w3_coder_compiles_to_explicit_w1_binding(tmp_path: Path):
    runtime = _test_runtime_config(tmp_path)
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
    runtime = _test_runtime_config(tmp_path)
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


def _ensure_project(tmp_path: Path, project_id: str = "aota_forge") -> None:
    (tmp_path / ".aota").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".aota" / "project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {project_id}\n"
        "  name: t\n"
        "  kind: test\n"
        "  status: active\n"
        "summary: test\n"
        "capabilities: []\n"
        "paths:\n"
        "  source_root: .\n"
        "  source: []\n"
        "  docs: []\n"
        "  scripts: []\n"
        "  profiles: []\n"
        "  skills: []\n"
        "  tests: []\n"
        "commands:\n"
        "  validate: []\n"
        "  deploy: []\n"
        "  verify_deploy: []\n"
        "runtime:\n"
        "  deployment_type: manual\n"
        "  requires_human_checkpoint: false\n"
        "codegraph:\n"
        "  enabled: false\n"
        "  index_location: .codegraph\n"
        "plan:\n"
        "  active_plan_id: null\n"
        "constraints: []\n",
        encoding="utf-8",
    )


def test_w3_trusted_binding_pins_project_worktree_and_role(tmp_path: Path):
    _ensure_project(tmp_path, "aota_forge")
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
    _ensure_project(tmp_path, "aota_forge")
    binding = build_worker_binding(
        root=tmp_path,
        project_id="aota_forge",
        worktree_id="w3-test-worktree",
        canonical_task_id="w3-task",
        handoff=_handoff(),
    )
    server = create_shared_mcp_server(binding)
    assert [tool.name for tool in server._tool_manager.list_tools()] == list(MCP_PUBLIC_TOOLS)


def test_w3_missing_runtime_binding_fails_closed(tmp_path: Path):
    full = _test_runtime_config(tmp_path)
    partial_doc = dict(
        executor=full.executor,
        executable=full.executable,
        concurrency=full.concurrency,
        provider=full.provider,
        model=full.model,
        bindings=(full.get_binding("coder"),),
    )

    # Fail-closed at the earliest seam: an incomplete operator config can
    # never be constructed, let alone reach composition/dispatch.
    with pytest.raises(RuntimeConfigError, match="incomplete"):
        type(full)(**partial_doc)


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
