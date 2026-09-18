"""AF #57 M3/W4 production governed-read activation proof."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aota_forge.composition.governed_read import (
    create_bound_cross_project_grant,
    resolve_cross_project_target_project,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.governance.cross_project_grant import PreapprovedByPlan
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.work_plane.durable_result_store import persist_durable_payload


NATIVE = "native-governed"
TARGET = "target-governed"
PLAN_ID = "plan_af57_m3_w4"
WORKTREE_ID = "wt-af57-m3-w4"
SOURCE_NATIVE = "wzjcccc-dotcom/native-governed"
SOURCE_TARGET = "wzjcccc-dotcom/target-governed"

MANIFEST = """schema_version: 1
project:
  id: {project_id}
  name: governed test project
  kind: test
  status: active
summary: bounded test project
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
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""


class _FakeHostClient:
    def dispatch(self, payload):  # pragma: no cover - dispatcher construction only
        return {"adapter_handle": "fake", "status": "running", "dispatch_time": "now"}


def _checkout(parent: Path, name: str, project_id: str, source: str) -> Path:
    root = parent / name
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{source}.git"], cwd=root, check=True)
    return root


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


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    native_root = _checkout(workspace, "native", NATIVE, SOURCE_NATIVE)
    target_root = _checkout(workspace, "target", TARGET, SOURCE_TARGET)
    (target_root / "foreign.txt").write_text("foreign production needle", encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    (worktree / "native.txt").write_text("native production needle", encoding="utf-8")
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"ws": {"candidates": [str(workspace)]}}), encoding="utf-8")
    evidence_base = tmp_path / ".aota-evidence"
    (evidence_base / NATIVE).mkdir(parents=True)
    (evidence_base / NATIVE / "receipt.json").write_text("native evidence", encoding="utf-8")
    return native_root, target_root, worktree, registry, evidence_base


def _plan() -> ProjectPlanRecord:
    return ProjectPlanRecord(
        project_id=NATIVE,
        plan_id=PLAN_ID,
        lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
        authority=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=f"{NATIVE}/plans/{PLAN_ID}",
        ),
    )


def test_thin_production_activation_materializes_live_grant_evidence_and_identity(
    tmp_path: Path,
):
    _native_root, _target_root, worktree, registry, evidence_base = _fixture(tmp_path)
    database = tmp_path / "governance.sqlite3"
    governance = SQLiteProjectGovernanceStore(database)
    governance.put_plan(_plan())
    target_binding = resolve_cross_project_target_project(
        project_id=TARGET,
        registry_path=registry,
    )
    grant = create_bound_cross_project_grant(
        store=governance,
        requesting_project=NATIVE,
        target_binding=target_binding,
        authority=PreapprovedByPlan(plan_id=PLAN_ID),
    )
    governance.close()

    host = None
    try:
        host = compose_thin_task_main_host(
            worktree_root=worktree,
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=_runtime_config(tmp_path),
            origin_task_main_session_ref="session:af57-m3-w4",
            host_client=_FakeHostClient(),
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
            governance_store_path=database,
            evidence_base=evidence_base,
        )

        roots = host.authorized_roots
        assert roots is not None
        assert roots.get(grant.grant_id) is not None
        assert roots.get(grant.grant_id).project_id == TARGET
        assert roots.get(grant.grant_id).has_capability("write") is False
        assert roots.get("authorized-evidence") is not None

        foreign_read = host.invoke(
            "workspace.read",
            {"path": "foreign.txt", "root_ref": grant.grant_id},
        )
        assert foreign_read["ok"] is True
        foreign_payload = foreign_read["payload"]
        assert foreign_payload["project_id"] == NATIVE
        assert foreign_payload["worktree_id"] == WORKTREE_ID
        assert foreign_payload["source_project_id"] == TARGET
        assert foreign_payload["source_worktree_id"] == ""
        assert foreign_payload["source_root_kind"] == "project-main"
        assert foreign_payload["root_ref"] == grant.grant_id

        foreign_write = host.invoke(
            "workspace.write",
            {"path": "foreign-write.txt", "content": "denied", "mode": "create_only"},
        )
        assert foreign_write["ok"] is False
        assert foreign_write["error"]["code"] == "AUTHORITY_DENIED"
        assert not (_target_root / "foreign-write.txt").exists()

        foreign_search = host.invoke(
            "workspace.search",
            {"query": "foreign production needle", "root_ref": grant.grant_id},
        )
        assert foreign_search["ok"] is True
        assert foreign_search["payload"]["source_identities"] == [
            {
                "root_ref": grant.grant_id,
                "root_kind": "project-main",
                "project_id": TARGET,
                "worktree_id": "",
            }
        ]
        assert foreign_search["payload"]["results"][0]["project_id"] == TARGET

        evidence_read = host.invoke(
            "workspace.read",
            {"path": "receipt.json", "root_ref": "authorized-evidence"},
        )
        assert evidence_read["ok"] is True
        assert evidence_read["payload"]["source_root_kind"] == "authorized-evidence"
        assert evidence_read["payload"]["source_project_id"] == NATIVE

        metadata = persist_durable_payload(
            host.sandbox,
            "result identity proof",
            ref="tool_output:workspace.read:identity",
            kind="evidence",
        )
        hydrated = host.invoke(
            "result.hydrate",
            {
                "ref": metadata["ref"],
                "digest": metadata["digest"],
                "project_id": NATIVE,
                "worktree_id": WORKTREE_ID,
                "kind": "evidence",
                "byte_length": metadata["byte_length"],
            },
        )
        assert hydrated["ok"] is True
        assert hydrated["payload"]["project_id"] == NATIVE
        assert hydrated["payload"]["worktree_id"] == WORKTREE_ID
    finally:
        if host is not None and host.governance_store is not None:
            host.governance_store.close()
        reset_execution_dispatcher()


def test_thin_bootstrap_round_trip_keeps_evidence_and_live_grant_inputs(
    tmp_path: Path,
):
    _native_root, _target_root, worktree, registry, evidence_base = _fixture(tmp_path)
    database = tmp_path / "governance.sqlite3"
    governance = SQLiteProjectGovernanceStore(database)
    governance.put_plan(_plan())
    target_binding = resolve_cross_project_target_project(
        project_id=TARGET,
        registry_path=registry,
    )
    grant = create_bound_cross_project_grant(
        store=governance,
        requesting_project=NATIVE,
        target_binding=target_binding,
        authority=PreapprovedByPlan(plan_id=PLAN_ID),
    )
    governance.close()

    from aota_forge.composition.task_main_runtime_selection import (
        build_thin_task_main_binding_from_envelope_bootstrap,
        materialize_thin_task_main_bootstrap,
    )

    try:
        bootstrap = materialize_thin_task_main_bootstrap(
            worktree_root=worktree,
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=_runtime_config(tmp_path),
            origin_task_main_session_ref="session:af57-m3-w4-bootstrap",
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
            governance_store_path=database,
            evidence_base=evidence_base,
        )
        payload = json.loads(bootstrap.read_text(encoding="utf-8"))
        assert payload["evidence_base"] == str(evidence_base.resolve())

        binding = build_thin_task_main_binding_from_envelope_bootstrap(
            payload,
            envelope_worktree_root=worktree,
        )
        assert binding.authorized_roots is not None
        assert binding.authorized_roots.get("authorized-evidence") is not None
        assert binding.authorized_roots.get(grant.grant_id) is not None
    finally:
        reset_execution_dispatcher()
