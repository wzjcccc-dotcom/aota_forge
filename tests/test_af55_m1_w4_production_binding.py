"""AF #55 M1/W4 — Production Trusted Project Binding Integration.

Deterministic, local-only proofs (no network, no real Hermes session):

* trusted Plan project identity (PROJECT_ID + SOURCE_REPOSITORY)
  → canonical resolver (registry-backed)
  → one local project
  → repository identity verified
  → trusted production binding (thin task-main host)
* 0 matches → fail closed
* >1 matches → semantic ambiguity / no automatic selection
* wrong origin → fail closed
* unregistered workspace → fail closed
* cwd sibling repo → ignored
* similarly named repo → ignored
* real #39 aota-reader checkout resolves mechanically (bounded, read-only)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aota_forge.composition.project_binding import resolve_trusted_project_binding
from aota_forge.composition.task_main_runtime_selection import (
    materialize_thin_task_main_bootstrap,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ProjectSourceRepositoryMismatchError,
)

OWNER = "wzjcccc-dotcom"
REPO = "aota_reader_mcp"
REAL_39_CHECKOUT = Path("/home/latios/workspace/chatgpt-hermes-mcp-poc")

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


def _checkout(parent: Path, folder: str, project_id: str, origin: str) -> Path:
    root = parent / folder
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def _registry(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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


class _FakeHostClient:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def dispatch(self, payload: Any) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "adapter_handle": f"fake-{len(self.payloads)}",
            "status": "running",
            "dispatch_time": "t",
        }


def _compose(
    tmp_path: Path,
    *,
    worktree_root: Path,
    project_id: str,
    registry_path: Path | None = None,
    source_repository: str | None = None,
):
    return compose_thin_task_main_host(
        worktree_root=worktree_root,
        project_id=project_id,
        worktree_id="wt-m1w4",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260914_af55_m1w4_session",
        host_client=_FakeHostClient(),
        source_repository=source_repository,
        registry_path=registry_path,
    )


def test_w4_production_binding_chain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkout = _checkout(
        workspace, "chatgpt-hermes-mcp-poc", "aota-reader",
        f"https://github.com/{OWNER}/{REPO}.git",
    )
    registry = _registry(
        tmp_path / "workspaces.json", {"aota-reader": {"candidates": [str(workspace)]}}
    )
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()

    host = _compose(
        tmp_path,
        worktree_root=worktree,
        project_id="aota-reader",
        registry_path=registry,
        source_repository=f"{OWNER}/{REPO}",
    )
    candidate = host.project_evidence.candidates[0]
    assert candidate.project_id == "aota-reader"
    assert candidate.project_root == str(checkout)
    assert host.project_evidence.status == "RESOLVED"
    assert host.source_repository == f"{OWNER}/{REPO}"
    assert host.repository_identity_verified is True
    assert host.repository_identity is not None
    assert host.repository_identity["git_origin_normalized"] == f"{OWNER}/{REPO}"
    assert host.sandbox.worktree_root == str(worktree)


def test_w4_zero_matches_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _checkout(workspace, "other", "other-project", f"https://github.com/{OWNER}/{REPO}.git")
    registry = _registry(
        tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}}
    )
    with pytest.raises(ProjectNotFoundError):
        resolve_trusted_project_binding(
            project_id="aota-reader",
            source_repository=f"{OWNER}/{REPO}",
            registry_path=registry,
        )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(ProjectNotFoundError):
        _compose(
            tmp_path,
            worktree_root=worktree,
            project_id="aota-reader",
            registry_path=registry,
            source_repository=f"{OWNER}/{REPO}",
        )


def test_w4_multiple_matches_fail_closed_no_auto_selection(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    _checkout(first, "one", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git")
    second = tmp_path / "second"
    second.mkdir()
    _checkout(second, "two", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git")
    registry = _registry(
        tmp_path / "workspaces.json",
        {
            "ws-one": {"candidates": [str(first)]},
            "ws-two": {"candidates": [str(second)]},
        },
    )
    with pytest.raises(ProjectAmbiguousError):
        resolve_trusted_project_binding(
            project_id="aota-reader",
            source_repository=f"{OWNER}/{REPO}",
            registry_path=registry,
        )


def test_w4_wrong_origin_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _checkout(workspace, "checkout", "aota-reader", "https://github.com/evil/wrong_repo.git")
    registry = _registry(
        tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}}
    )
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        resolve_trusted_project_binding(
            project_id="aota-reader",
            source_repository=f"{OWNER}/{REPO}",
            registry_path=registry,
        )


def test_w4_unregistered_workspace_fail_closed(tmp_path: Path) -> None:
    checkout_parent = tmp_path / "not-registered"
    checkout_parent.mkdir()
    _checkout(
        checkout_parent, "checkout", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git"
    )
    registry = _registry(tmp_path / "workspaces.json", {})
    with pytest.raises(ProjectNotFoundError):
        resolve_trusted_project_binding(
            project_id="aota-reader",
            source_repository=f"{OWNER}/{REPO}",
            registry_path=registry,
        )


def test_w4_cwd_sibling_repo_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd_dir = tmp_path / "cwd-sibling"
    cwd_dir.mkdir()
    _checkout(cwd_dir, "not-this-one", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git")
    monkeypatch.chdir(cwd_dir)
    registry = _registry(tmp_path / "workspaces.json", {})
    with pytest.raises(ProjectNotFoundError):
        resolve_trusted_project_binding(
            project_id="aota-reader",
            source_repository=f"{OWNER}/{REPO}",
            registry_path=registry,
        )


def test_w4_similarly_named_repo_ignored(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Folder named exactly like the project, but its manifest declares another id.
    _checkout(workspace, "aota-reader", "not-aota-reader", "https://github.com/evil/wrong.git")
    _checkout(
        workspace, "actual-checkout", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git"
    )
    registry = _registry(
        tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}}
    )
    binding = resolve_trusted_project_binding(
        project_id="aota-reader",
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
    )
    assert binding.candidate is not None
    assert binding.candidate.project_root == str(workspace / "actual-checkout")


def test_w4_thin_bootstrap_carries_trusted_plan_identity(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _checkout(workspace, "checkout", "aota-reader", f"https://github.com/{OWNER}/{REPO}.git")
    registry = _registry(
        tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}}
    )
    cfg = _runtime_config(tmp_path)
    dest = materialize_thin_task_main_bootstrap(
        worktree_root=worktree,
        project_id="aota-reader",
        worktree_id="wt-boot",
        runtime_config_path=cfg,
        origin_task_main_session_ref="20260914_af55_m1w4_boot",
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
    )
    payload = json.loads(Path(dest).read_text(encoding="utf-8"))
    assert payload["source_repository"] == f"{OWNER}/{REPO}"
    assert payload["registry_path"] == str(registry)
    assert payload["runtime_path"] == "thin"


def test_w4_broken_registry_entry_fails_closed(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path / "workspaces.json",
        {"missing-ws": {"candidates": [str(tmp_path / "does-not-exist")]}},
    )
    with pytest.raises(ProjectNotFoundError):
        resolve_trusted_project_binding(project_id="aota-reader", registry_path=registry)


@pytest.mark.skipif(
    not (REAL_39_CHECKOUT / ".aota" / "project.yaml").is_file(),
    reason="real #39 aota-reader checkout not present on this host",
)
def test_w4_real_39_checkout_resolves_mechanically(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path / "workspaces.json",
        {"aota-reader": {"candidates": [str(REAL_39_CHECKOUT)]}},
    )
    binding = resolve_trusted_project_binding(
        project_id="aota-reader",
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
    )
    assert binding.status == "RESOLVED"
    assert binding.candidate is not None
    assert binding.candidate.project_root == str(REAL_39_CHECKOUT)
    assert binding.candidate.project_id == "aota-reader"
    assert binding.repository_identity is not None
    assert binding.repository_identity["git_origin_normalized"] == f"{OWNER}/{REPO}"
    # folder-name inference is impossible here: the folder name differs
    assert Path(binding.candidate.project_root).name != "aota-reader"
