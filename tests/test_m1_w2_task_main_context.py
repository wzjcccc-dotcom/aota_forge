"""M1/W2 Project/Plan and AuthorizedRootSet context integration proof."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.task_main_context import dispatch_task_main_context
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeService
from aota_forge.mcp_transport import _to_canonical_binding
from aota_forge.work_plane.authorized_roots import AuthorizedRoot, AuthorizedRootSet


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_m1_w2_context"

MANIFEST = """schema_version: 1
project:
  id: aota_forge
  name: context integration fixture
  kind: test
  status: active
summary: bounded context integration fixture
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

PLAN_BODY = """# M1/W2 context integration fixture

```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=ready
M1_USER_APPROVAL_SATISFIED=yes
```
"""


class _FakeHostClient:
    def dispatch(self, payload):  # pragma: no cover - host never spawns here
        return {"adapter_handle": "fake-1", "status": "running", "dispatch_time": "t"}


def _runtime_config(tmp_path: Path) -> Path:
    executable = tmp_path / "hermes-stub"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    config = tmp_path / "runtime.json"
    config.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": str(executable),
                "concurrency": 1,
                "provider": "test-provider",
                "model": "test-model",
                "bindings": {
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "task-main": {"profile": "aota-task-main"},
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def _host_fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    checkout = workspace / "checkout"
    (checkout / ".aota").mkdir(parents=True)
    (checkout / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"workspace": {"candidates": [str(workspace)]}}), encoding="utf-8")
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    store_path = tmp_path / "governance.sqlite3"
    journal_path = tmp_path / "governance-journal.json"
    intake_context = GovernanceIntakeContext(
        workspace_id="workspace",
        governance_base=governance_base,
        registry_path=registry,
        workspace_root=workspace,
        project_root=checkout,
        governance_store_path=store_path,
        journal_path=journal_path,
    )
    service = GovernanceIntakeService(intake_context, principal="m1-w2-test")
    assert service.project("init", project_id=PROJECT_ID)["status"] == "VERIFIED"
    assert (
        service.plan(
            "init",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            candidate_body=PLAN_BODY,
        )["status"]
        == "VERIFIED"
    )
    host = compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-m1-w2",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="m1-w2-context-session",
        host_client=_FakeHostClient(),
        registry_path=registry,
        governance_base=governance_base,
        plan_id=PLAN_ID,
        governance_store_path=store_path,
    )
    return host


def test_real_local_binding_projects_plan_and_bounded_authorized_roots(
    tmp_path: Path,
) -> None:
    host = _host_fixture(tmp_path)
    try:
        inspected = host.invoke("task_main.context", {"mode": "inspect"})
        assert inspected["ok"] is True, inspected
        context = inspected["payload"]["context"]
        assert context["project_id"] == PROJECT_ID
        assert context["plan_id"] == PLAN_ID
        assert context["authority_binding"]["source_kind"] == "local_governance"
        assert context["authority_binding"]["authority_ref"] == (
            f"local-governance:{PROJECT_ID}/{PLAN_ID}"
        )

        roots = {item["root_ref"]: item for item in context["authorized_roots"]}
        assert set(roots) == {"project-main", "active-worktree", "local-governance"}
        assert set(roots["local-governance"]["capabilities"]) == {"read", "search"}
        assert "write" not in roots["local-governance"]["capabilities"]
        assert str(tmp_path) not in json.dumps(context)

        bound = host.invoke(
            "task_main.context",
            {"mode": "bind", "project_ref": PROJECT_ID, "plan_ref": PLAN_ID},
        )
        assert bound["ok"] is True, bound
        assert bound["payload"]["context"]["authorized_roots"] == context["authorized_roots"]
    finally:
        if host.governance_store is not None:
            host.governance_store.close()


def test_real_context_keeps_cross_project_and_cross_plan_guards(tmp_path: Path) -> None:
    host = _host_fixture(tmp_path)
    try:
        foreign_project = host.invoke(
            "task_main.context",
            {"mode": "bind", "project_ref": "other_project"},
        )
        assert foreign_project["ok"] is False
        assert foreign_project["error"]["code"] == "PROJECT_CONTEXT_MISMATCH"

        foreign_plan = host.invoke(
            "task_main.context",
            {"mode": "bind", "plan_ref": "plan_other"},
        )
        assert foreign_plan["ok"] is False
        assert foreign_plan["error"]["code"] == "PLAN_CONTEXT_MISMATCH"
    finally:
        if host.governance_store is not None:
            host.governance_store.close()


def test_context_rejects_root_set_not_derived_from_sandbox(tmp_path: Path) -> None:
    host = _host_fixture(tmp_path)
    try:
        canonical = _to_canonical_binding(host.trusted_binding)
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        forged_root = AuthorizedRoot(
            root_ref="project-main",
            root_kind="project-main",
            root_path=str(sibling),
            capabilities=frozenset({"read", "search"}),
            project_id=PROJECT_ID,
        )
        forged_roots = AuthorizedRootSet(session_kind="task-main", roots=(forged_root,))
        forged_binding = dataclasses.replace(canonical, authorized_roots=forged_roots)

        response = dispatch_task_main_context({"mode": "inspect"}, forged_binding)
        assert response.ok is False
        assert response.error["code"] == "AUTHORITY_DENIED"
    finally:
        if host.governance_store is not None:
            host.governance_store.close()
