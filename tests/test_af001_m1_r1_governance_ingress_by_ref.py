"""AF #001 M1/R1 — ingress-backed Governance result durability.

These tests exercise the exact RV1 surface:

    _SharedAotaMcpAdapter -> dispatch_via_core -> governance intake
    -> Core result governance -> by_ref -> result.hydrate
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.core_ingress import dispatch_via_core
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeService
from aota_forge.mcp_transport import (
    TrustedWorkerBinding,
    _SharedAotaMcpAdapter,
    _to_canonical_binding,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


PROJECT_ID = "af001r1"
WORKTREE_ID = "af001r1-worktree"
PLAN_ID = "plan_af001_r1_target"
PLAN_COUNT = 10

MANIFEST = f"""schema_version: 1
project:
  id: {PROJECT_ID}
  name: ingress hydration fixture
  kind: test
  status: active
summary: bounded ingress hydration fixture
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph
plan:
  active_plan_id: null
constraints: []
"""

PLAN_BODY = """# [PLAN] AF #001 M1/R1 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_KIND=portable_plan
PROJECT_ID=af001r1
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1
Bounded governance ingress hydration fixture.
"""


def _governance_context(tmp_path: Path) -> tuple[GovernanceIntakeContext, Path]:
    workspace = tmp_path / "workspace"
    project_root = workspace / "checkout"
    (project_root / ".aota").mkdir(parents=True)
    (project_root / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text("{}", encoding="utf-8")
    context = GovernanceIntakeContext(
        workspace_id="af001-r1-workspace",
        governance_base=governance_base,
        registry_path=registry,
        workspace_root=workspace,
        project_root=project_root,
    )
    return context, project_root


def _seed_project_and_plans(
    tmp_path: Path, *, plan_count: int = PLAN_COUNT
) -> tuple[GovernanceIntakeContext, Path]:
    context, project_root = _governance_context(tmp_path)
    service = GovernanceIntakeService(context, principal="af001-r1-operator")
    project = service.project("init", project_id=PROJECT_ID)
    assert project["status"] == "VERIFIED"
    target = service.plan(
        "init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=PLAN_BODY,
    )
    assert target["status"] == "VERIFIED"
    for index in range(plan_count):
        plan_id = f"plan_af001_r1_{index:02d}"
        initialized = service.plan(
            "init",
            project_id=PROJECT_ID,
            plan_id=plan_id,
            candidate_body=PLAN_BODY,
        )
        assert initialized["status"] == "VERIFIED"
    return context, project_root


def _binding(context: GovernanceIntakeContext, project_root: Path) -> TrustedWorkerBinding:
    candidate = ProjectCandidateEvidence(
        workspace_id=context.workspace_id,
        workspace_root=str(context.workspace_root),
        project_id=PROJECT_ID,
        project_root=str(project_root),
        manifest_path=str(project_root / ".aota" / "project.yaml"),
        name="ingress hydration fixture",
        kind="test",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id=context.workspace_id,
        workspace_root=str(context.workspace_root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, WORKTREE_ID, project_root)
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="af001-m1-r1",
        objective="prove ingress-backed result durability",
        bounded_scope="one trusted project worktree",
        validation_expectations=("focused R1 tests",),
        semantic_stop_expectations=("stop after validation",),
    )
    return TrustedWorkerBinding(
        canonical_task_id="task-af001-r1",
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        trusted_context=bind_trusted_context(
            principal_id="af001-r1-operator",
            principal_type="task-main",
            channel="mcp",
            governance_intake_context=context,
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface(
            "coder",
            eager=("governance.project", "governance.plan", "result.hydrate"),
        ),
        read_authorities=(),
        mutation_authority=None,
    )


def _expected_ingress_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        key: envelope[key]
        for key in ("data", "evidence", "warnings")
        if envelope.get(key)
    }


def _hydrate_claim(adapter: _SharedAotaMcpAdapter, result: dict[str, Any]) -> dict[str, Any]:
    assert result["output_mode"] == "by_ref"
    assert result["output_ref"] is not None
    assert result["hydration"] is not None
    claim = result["hydration"]
    assert claim["operation"] == "result.hydrate"
    hydrated = adapter.invoke("result.hydrate", dict(claim["arguments"]))
    assert hydrated["ok"] is True, hydrated
    assert hydrated["output_mode"] == "inline"
    assert hydrated["payload"] is not None
    return json.loads(hydrated["payload"]["content"])


@pytest.mark.parametrize(
    ("operation", "arguments"),
    (
        (
            "governance.project",
            {"mode": "inspect", "project_id": PROJECT_ID},
        ),
        (
            "governance.plan",
            {"mode": "inspect", "project_id": PROJECT_ID, "plan_id": PLAN_ID},
        ),
    ),
)
def test_ingress_governance_large_result_is_durable_and_hydratable(
    tmp_path: Path, operation: str, arguments: dict[str, Any]
) -> None:
    context, project_root = _seed_project_and_plans(tmp_path)
    binding = _binding(context, project_root)
    adapter = _SharedAotaMcpAdapter(binding)

    canonical = dispatch_via_core(operation, arguments, _to_canonical_binding(binding))
    assert isinstance(canonical, dict) and canonical["ok"] is True

    result = adapter.invoke(operation, arguments)
    assert result["ok"] is True
    assert result["output_mode"] == "by_ref"
    assert result["output_byte_length"] > 4096
    ref = result["output_ref"]
    assert ref["project_id"] == PROJECT_ID
    assert ref["worktree_id"] == WORKTREE_ID
    payload_path = project_root / ".aota" / "durable_payloads" / f"{ref['digest']}.bin"
    assert payload_path.is_file()
    assert payload_path.stat().st_size == ref["byte_length"]

    hydrated = _hydrate_claim(adapter, result)
    assert hydrated == _expected_ingress_payload(canonical)


def test_small_ingress_result_remains_inline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context, project_root = _governance_context(tmp_path)
    adapter = _SharedAotaMcpAdapter(_binding(context, project_root))
    canonical = {
        "ok": True,
        "operation": "governance.project",
        "data": {"status": "ok"},
        "evidence": {},
        "warnings": [],
    }
    monkeypatch.setattr(
        "aota_forge.core_ingress.dispatch_via_core",
        lambda operation, arguments, binding: canonical,
    )

    result = adapter.invoke(
        "governance.project",
        {"mode": "inspect", "project_id": PROJECT_ID},
    )

    assert result["ok"] is True
    assert result["output_mode"] == "inline"
    assert result["output_ref"] is None
    assert result["hydration"] is None
    assert result["output_byte_length"] <= 4096
    assert result["payload"] == {"data": {"status": "ok"}}


def test_governance_hydration_reauthorizes_scope_and_ref_integrity(tmp_path: Path) -> None:
    context, project_root = _seed_project_and_plans(tmp_path)
    binding = _binding(context, project_root)
    adapter = _SharedAotaMcpAdapter(binding)
    result = adapter.invoke(
        "governance.project",
        {"mode": "inspect", "project_id": PROJECT_ID},
    )
    assert result["output_mode"] == "by_ref"
    arguments = dict(result["hydration"]["arguments"])

    for field, value in (
        ("project_id", "foreign-project"),
        ("worktree_id", "foreign-worktree"),
        ("digest", "0" * 64),
        ("ref", "/tmp/fabricated-payload"),
    ):
        tampered = dict(arguments)
        tampered[field] = value
        denied = adapter.invoke("result.hydrate", tampered)
        assert denied["ok"] is False
        assert denied["error"] is not None
        assert denied["error"]["code"] in {
            "CROSS_SCOPE_DENIED",
            "DIGEST_MISMATCH",
            "UNKNOWN_REF",
            "TAMPERED_REF",
            "TAMPERED_PAYLOAD",
        }


def test_mcp_transport_does_not_own_durable_persistence() -> None:
    source = Path(__file__).resolve().parents[1] / "aota_forge" / "mcp_transport.py"
    text = source.read_text(encoding="utf-8")
    assert "persist_durable_payload" not in text
    assert "persist_governed_tool_result_if_by_ref(" not in text
