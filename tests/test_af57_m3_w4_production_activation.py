"""AF #57 M3/W4 production governed-read activation proof."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
from aota_forge.composition.governed_read import (
    create_bound_cross_project_grant,
    resolve_cross_project_target_project,
)
from aota_forge.composition.task_main_daily_launcher import (
    DailyTaskMainLauncher,
    launch_daily_task_main,
)
from aota_forge.composition.task_main_runtime_selection import TaskMainRuntimeSelectionError
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.governance.cross_project_grant import PreapprovedByPlan
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.mcp_transport import create_aota_invoke_dispatch
from aota_forge.adapters.hermes.session_reentry import OUTCOME_COMPLETED
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    load_binding_from_envelope,
)
from aota_forge.work_plane.durable_result_store import persist_durable_payload
from aota_forge.work_plane.authorized_roots import (
    AuthorizedEvidenceRootError,
    ROOT_REF_AUTHORIZED_EVIDENCE as AUTHORIZED_EVIDENCE_ROOT_REF,
)


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


class _ResumeProbeLauncher(DailyTaskMainLauncher):
    """Keep the launcher re-entry seam, replacing only the external host call."""

    def __init__(self) -> None:
        super().__init__()
        self.reentry_context = None
        self.reentry_envelope = None

    def _require_task_main_tool_surface(self, *, ctx, session_id, phase, env=None):
        return ("aota.invoke",)

    def _run_autonomous_completion_continuation(
        self, *, ctx, session_id, timeout_seconds=None, trace_path=None
    ):
        return object()

    def _build_exact_session_reentry(self, *, ctx, timeout_seconds):
        self.reentry_context = ctx

        class _Reentry:
            def reenter(inner_self, session_id, payload):
                self.reentry_envelope = os.environ[PRE_RESOLVED_BINDING_ENV]
                return SimpleNamespace(
                    outcome=OUTCOME_COMPLETED,
                    session_id=session_id,
                    payload=payload,
                    delivery_outcomes=(),
                )

        return _Reentry()


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
    executable.write_text(
        "#!/bin/sh\n"
        "usage=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--usage-file\" ]; then\n"
        "    usage=\"$2\"\n"
        "    shift 2\n"
        "  else\n"
        "    shift\n"
        "  fi\n"
        "done\n"
        "if [ -n \"$usage\" ]; then\n"
        "  printf '%s\\n' '{\"session_id\":\"session:af57-hermes\",\"completed\":true}' > \"$usage\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
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


def _binding_from_launcher_context(launcher: DailyTaskMainLauncher, ctx):
    env = launcher.build_env(ctx)
    return load_binding_from_envelope(env[PRE_RESOLVED_BINDING_ENV])


def _active_grants(database: Path):
    governance = SQLiteProjectGovernanceStore(database)
    try:
        return governance.list_cross_project_grants(NATIVE, active_only=True)
    finally:
        governance.close()


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


def test_launch_daily_task_main_forwards_trusted_evidence_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _native_root, _target_root, worktree, registry, evidence_base = _fixture(tmp_path)
    cfg = _runtime_config(tmp_path)
    captured = {}

    def fake_launch(self, **kwargs):
        captured.update(kwargs)
        ctx = self.prepare(
            worktree_root=kwargs["worktree_root"],
            project_id=kwargs["project_id"],
            worktree_id=kwargs["worktree_id"],
            runtime_config_path=kwargs["runtime_config_path"],
            origin_task_main_session_ref="session:af57-wrapper",
            evidence_base=kwargs["evidence_base"],
            source_repository=kwargs["source_repository"],
            registry_path=kwargs["registry_path"],
        )
        captured["binding"] = _binding_from_launcher_context(self, ctx)
        return ctx, "session:af57-wrapper"

    monkeypatch.setattr(DailyTaskMainLauncher, "launch", fake_launch)
    try:
        ctx, session_id = launch_daily_task_main(
            worktree,
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=cfg,
            plan_adapter=object(),
            evidence_base=evidence_base,
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
        )
        assert session_id == "session:af57-wrapper"
        assert captured["evidence_base"] == evidence_base
        binding = captured["binding"]
        assert binding.authorized_roots is not None
        assert binding.authorized_roots.evidence_binding is not None
        assert binding.authorized_roots.get(AUTHORIZED_EVIDENCE_ROOT_REF) is not None
        assert ctx.runtime_path == "thin"
    finally:
        reset_execution_dispatcher()


def test_daily_task_main_resume_reconstructs_evidence_and_reloads_live_grants(
    tmp_path: Path,
):
    _native_root, _target_root, worktree, registry, evidence_base = _fixture(tmp_path)
    cfg = _runtime_config(tmp_path)
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

    try:
        launcher_a = _ResumeProbeLauncher()
        ctx_a, launch_session_id = launcher_a.launch(
            worktree_root=worktree,
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=cfg,
            initial_prompt="operator launch",
            timeout_seconds=5,
            completion_timeout_seconds=1,
            max_productive_continuations=0,
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
            governance_store_path=database,
            evidence_base=evidence_base,
        )
        assert launch_session_id == "session:af57-hermes"
        binding_a = _binding_from_launcher_context(launcher_a, ctx_a)
        invoke_a = create_aota_invoke_dispatch(binding_a)

        assert len(_active_grants(database)) == 1
        local_before = invoke_a(
            "workspace.read",
            {"path": "receipt.json", "root_ref": AUTHORIZED_EVIDENCE_ROOT_REF},
        )
        assert local_before["ok"] is True
        assert binding_a.authorized_roots is not None
        assert binding_a.authorized_roots.evidence_binding is not None

        foreign_read_before = invoke_a(
            "workspace.read",
            {"path": "foreign.txt", "root_ref": grant.grant_id},
        )
        assert foreign_read_before["ok"] is True
        assert foreign_read_before["payload"]["source_project_id"] == TARGET
        foreign_search_before = invoke_a(
            "workspace.search",
            {"query": "foreign production needle", "root_ref": grant.grant_id},
        )
        assert foreign_search_before["ok"] is True

        revoked = SQLiteProjectGovernanceStore(database)
        try:
            revoked.revoke_cross_project_grant(grant.grant_id, grant.revision)
        finally:
            revoked.close()
        assert len(_active_grants(database)) == 0

        launcher_b = _ResumeProbeLauncher()
        resumed = launcher_b.resume(
            worktree_root=worktree,
            session_id="session:af57-reentry",
            payload="operator re-entry",
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=cfg,
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
            governance_store_path=database,
            evidence_base=evidence_base,
        )
        assert resumed.outcome == OUTCOME_COMPLETED
        assert launcher_b.reentry_context is not None
        assert launcher_b.reentry_envelope is not None

        # The real resume path refreshed the trusted bootstrap; this load is the
        # same verified child-side reconstruction used by the production MCP.
        binding_b = load_binding_from_envelope(launcher_b.reentry_envelope)
        invoke_b = create_aota_invoke_dispatch(binding_b)
        assert binding_b is not binding_a
        assert binding_b.authorized_roots is not binding_a.authorized_roots
        assert binding_b.authorized_roots is not None
        assert binding_b.authorized_roots.evidence_binding is not None
        assert binding_b.authorized_roots.get(AUTHORIZED_EVIDENCE_ROOT_REF) is not None
        assert binding_b.authorized_roots.get(grant.grant_id) is None

        local_after = invoke_b(
            "workspace.read",
            {"path": "receipt.json", "root_ref": AUTHORIZED_EVIDENCE_ROOT_REF},
        )
        assert local_after["ok"] is True
        foreign_read_after = invoke_b(
            "workspace.read",
            {"path": "foreign.txt", "root_ref": grant.grant_id},
        )
        assert foreign_read_after["ok"] is False
        assert foreign_read_after["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
        foreign_search_after = invoke_b(
            "workspace.search",
            {"query": "foreign production needle", "root_ref": grant.grant_id},
        )
        assert foreign_search_after["ok"] is False
        assert foreign_search_after["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
        assert len(_active_grants(database)) == 0
    finally:
        reset_execution_dispatcher()


def test_daily_task_main_invalid_evidence_base_fails_closed(tmp_path: Path):
    _native_root, _target_root, worktree, registry, evidence_base = _fixture(tmp_path)
    cfg = _runtime_config(tmp_path)
    launcher = DailyTaskMainLauncher()
    missing = tmp_path / "missing-evidence"
    with pytest.raises(TaskMainRuntimeSelectionError):
        launcher.prepare(
            worktree_root=worktree,
            project_id=NATIVE,
            worktree_id=WORKTREE_ID,
            runtime_config_path=cfg,
            origin_task_main_session_ref="session:af57-invalid-missing",
            source_repository=SOURCE_NATIVE,
            registry_path=registry,
            evidence_base=missing,
        )

    wrong_scope = tmp_path / "wrong-scope-evidence"
    (wrong_scope / TARGET).mkdir(parents=True)
    ctx = launcher.prepare(
        worktree_root=worktree,
        project_id=NATIVE,
        worktree_id=WORKTREE_ID,
        runtime_config_path=cfg,
        origin_task_main_session_ref="session:af57-invalid-scope",
        source_repository=SOURCE_NATIVE,
        registry_path=registry,
        evidence_base=wrong_scope,
    )
    env = launcher.build_env(ctx)
    with pytest.raises(AuthorizedEvidenceRootError):
        load_binding_from_envelope(env[PRE_RESOLVED_BINDING_ENV])
    assert evidence_base.is_dir()
