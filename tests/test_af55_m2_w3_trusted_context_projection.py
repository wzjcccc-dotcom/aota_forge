"""AF #55 M2/W3 — Trusted binding carriers, §8 context projection & role split.

Deterministic local-only proofs:

* thin task-main binding carries the trusted SOURCE_REPOSITORY fact and the
  task-main authorized root set; every authority's root set matches
* role.bootstrap exposes the §8 projection with PLAN_AUTHORITY,
  IMPLEMENTATION_PROJECT and ACTIVE_WORKTREE separated mechanically
* worker bindings carry only the assigned active-worktree; no inheritance
* the trusted thin bootstrap declares the canonical root_ref contract and a
  drifted declaration fails closed
* forged / mismatched root sets and worker SOURCE_REPOSITORY fail closed at
  binding construction
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aota_forge.composition.task_main_runtime_selection import (
    build_thin_task_main_binding_from_envelope_bootstrap,
    materialize_thin_task_main_bootstrap,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError
from aota_forge.work_plane.authorized_roots import (
    AuthorizedRoot,
    AuthorizedRootSet,
    authorized_roots_for_task_main,
)
from aota_forge.work_plane.handoff import TaskHandoff

OWNER = "wzjcccc-dotcom"
REPO = "aota_reader_mcp"
PROJECT_ID = "aota-reader"

MANIFEST = (
    "schema_version: 1\n"
    "project:\n"
    "  id: {project_id}\n  name: test project\n  kind: test\n  status: active\n"
    "summary: bounded test project\n"
    "capabilities: []\n"
    "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n"
    "  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph/\n"
    "plan:\n  active_plan_id: null\n"
    "constraints: []\n"
)


class _FakeHostClient:
    def dispatch(self, payload):  # pragma: no cover - trivial fake
        return {"adapter_handle": "fake-1", "status": "running", "dispatch_time": "t"}


def _checkout(parent: Path, folder: str, project_id: str, origin: str) -> Path:
    root = parent / folder
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def _runtime_config(tmp_path: Path) -> Path:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    cfg = tmp_path / "runtime.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": str(exe),
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
    return cfg


def _handoff(role: str) -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="test-kind",
        objective="objective",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )


def _compose(tmp_path: Path, worktree: Path, registry: Path, *, plan_ref: str | None = None):
    return compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-m2w3",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260915_af55_m2w3_session",
        host_client=_FakeHostClient(),
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
        plan_ref=plan_ref,
    )


def _workspace_fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkout = _checkout(workspace, "chatgpt-hermes-mcp-poc", PROJECT_ID, f"https://github.com/{OWNER}/{REPO}.git")
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"aota-reader": {"candidates": [str(workspace)]}}), encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    return checkout, registry, worktree


def test_task_main_binding_carries_trusted_roots_and_identity(tmp_path: Path) -> None:
    checkout, registry, worktree = _workspace_fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    binding = host.trusted_binding
    assert binding.source_repository == f"{OWNER}/{REPO}"
    assert binding.authorized_roots is not None
    assert binding.authorized_roots.names() == ("project-main", "active-worktree")
    assert binding.effective_authorized_roots.digest() == host.authorized_roots.digest()
    for authority in binding.read_authorities:
        assert authority.authorized_root_set.digest() == binding.authorized_roots.digest()
    assert host.authorized_roots.get("project-main").root_path == str(checkout)
    assert host.authorized_roots.get("active-worktree").root_path == str(worktree)


def test_task_main_projection_separates_plan_project_worktree(tmp_path: Path) -> None:
    checkout, registry, worktree = _workspace_fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    projection = host.context_projection
    assert projection is not None
    assert projection["plan"] == {
        "plan_ref": "wzjcccc-dotcom/aota-hermes-tools#55",
        "governing_repository": "wzjcccc-dotcom/aota-hermes-tools",
    }
    assert projection["project"] == {
        "project_id": PROJECT_ID,
        "source_repository": f"{OWNER}/{REPO}",
        "root_ref": "project-main",
    }
    assert projection["worktree"] == {"worktree_id": "wt-m2w3", "root_ref": "active-worktree"}
    assert projection["roots"] == {
        "project-main": ["read", "search"],
        "active-worktree": ["read", "search", "write"],
    }
    # governing repository != source repository: separation is explicit
    assert projection["project"]["source_repository"] != projection["plan"]["governing_repository"]
    serialized = json.dumps(projection)
    assert str(checkout) not in serialized
    assert str(worktree) not in serialized


def test_task_main_role_bootstrap_exposes_projection(tmp_path: Path) -> None:
    _checkout, registry, worktree = _workspace_fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    guidance = host.role_guidance()
    assert guidance["TRUSTED_PROJECT_CONTEXT"] == host.context_projection
    assert guidance["CURRENT_EXECUTION_CONTEXT"]["plan_ref"] == "wzjcccc-dotcom/aota-hermes-tools#55"
    assert guidance["TRUSTED_PROJECT_CONTEXT"]["project"]["source_repository"] == f"{OWNER}/{REPO}"


def test_folder_name_is_not_source_identity(tmp_path: Path) -> None:
    from aota_forge.core.contracts.errors import ProjectSourceRepositoryMismatchError

    canonical, registry, worktree = _workspace_fixture(tmp_path)
    # folder name (chatgpt-hermes-mcp-poc) matches neither project_id nor repo;
    # resolution is by project_id + mechanically verified Git origin only.
    host = _compose(tmp_path, worktree, registry)
    assert host.authorized_roots is not None
    assert host.repository_identity_verified is True
    assert str(canonical) not in json.dumps(host.context_projection)
    # a folder-name-similar checkout with the wrong origin fails closed
    wrong = _checkout(
        tmp_path / "wrong-workspace",
        "aota-reader",  # folder name equals project_id, still not identity
        PROJECT_ID,
        "https://github.com/wzjcccc-dotcom/not-the-source-repo.git",
    )
    wrong_registry = tmp_path / "wrong-workspaces.json"
    wrong_registry.write_text(
        json.dumps({"aota-reader": {"candidates": [str(wrong.parent)]}}), encoding="utf-8"
    )
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        _compose(tmp_path, worktree, wrong_registry)


def test_cwd_is_not_projected_as_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _checkout, registry, worktree = _workspace_fixture(tmp_path)
    elsewhere = tmp_path / "project-with-similar-name"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    projection = host.context_projection
    assert projection is not None
    assert projection["project"]["root_ref"] == "project-main"
    serialized = json.dumps(projection)
    assert str(elsewhere) not in serialized
    assert str(Path.cwd()) not in serialized


def test_worker_binding_carries_only_active_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=_handoff("coder"),
    )
    assert binding.authorized_roots is not None
    assert binding.authorized_roots.names() == ("active-worktree",)
    assert binding.authorized_roots.get("project-main") is None
    assert binding.source_repository == ""
    for authority in binding.read_authorities:
        assert authority.authorized_root_set.get("project-main") is None
    if binding.mutation_authority is not None:
        assert binding.mutation_authority.authorized_root_set.get("project-main") is None


def test_worker_role_bootstrap_projection_has_no_inheritance(tmp_path: Path) -> None:
    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=_handoff("coder"),
    )
    result = handle_role_bootstrap(binding, {})
    projection = result["TRUSTED_PROJECT_CONTEXT"]
    assert projection["plan"] is None
    assert projection["project"]["project_id"] == "aota_forge"
    assert projection["project"]["root_ref"] == "active-worktree"
    assert projection["worktree"]["root_ref"] == "active-worktree"
    assert projection["roots"] == {"active-worktree": ["read", "search", "write"]}
    assert "project-main" not in projection["roots"]


def test_bootstrap_declares_root_ref_contract_and_rejects_drift(tmp_path: Path) -> None:
    _checkout, registry, worktree = _workspace_fixture(tmp_path)
    cfg = _runtime_config(tmp_path)
    dest = materialize_thin_task_main_bootstrap(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-m2w3",
        runtime_config_path=cfg,
        origin_task_main_session_ref="20260915_af55_m2w3_boot",
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
    )
    payload = json.loads(Path(dest).read_text(encoding="utf-8"))
    assert payload["authorized_root_refs"] == {
        "project": "project-main",
        "worktree": "active-worktree",
    }
    # correct declaration builds the trusted binding with the full task-main roots
    binding = build_thin_task_main_binding_from_envelope_bootstrap(
        payload, envelope_worktree_root=worktree
    )
    assert binding.authorized_roots is not None
    assert binding.authorized_roots.names() == ("project-main", "active-worktree")
    assert binding.source_repository == f"{OWNER}/{REPO}"
    # drifted declaration fails closed before any host composition
    drift = dict(payload)
    drift["authorized_root_refs"] = {"project": "sibling", "worktree": "active-worktree"}
    with pytest.raises(TrustedBindingError):
        build_thin_task_main_binding_from_envelope_bootstrap(
            drift, envelope_worktree_root=worktree
        )
    malformed = dict(payload)
    malformed["authorized_root_refs"] = ["project-main"]
    with pytest.raises(TrustedBindingError):
        build_thin_task_main_binding_from_envelope_bootstrap(
            malformed, envelope_worktree_root=worktree
        )


def test_binding_rejects_root_authority_mismatch(tmp_path: Path) -> None:
    import dataclasses

    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=_handoff("coder"),
    )
    task_main_roots = authorized_roots_for_task_main(binding.sandbox)
    with pytest.raises(TrustedBindingError):
        dataclasses.replace(binding, authorized_roots=task_main_roots)


def test_binding_rejects_forged_sibling_root(tmp_path: Path) -> None:
    import dataclasses

    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=_handoff("coder"),
    )
    forged = AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=str(sibling),
        capabilities=frozenset({"read", "search"}),
        project_id="aota_forge",
    )
    forged_set = AuthorizedRootSet(session_kind="task-main", roots=(forged,))
    with pytest.raises(TrustedBindingError):
        dataclasses.replace(binding, authorized_roots=forged_set)


def test_worker_binding_rejects_source_repository(tmp_path: Path) -> None:
    import dataclasses

    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=_handoff("coder"),
    )
    with pytest.raises(TrustedBindingError):
        dataclasses.replace(binding, source_repository=f"{OWNER}/{REPO}")
