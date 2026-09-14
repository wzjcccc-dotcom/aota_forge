"""AF #55 M1/W1 — Existing Registry & Project Identity Contract Freeze.

Proves the accepted project foundation is preserved, not replaced:

* canonical manifest validation still passes on valid manifests;
* duplicate project IDs remain ambiguous / fail-closed;
* symlink project roots and symlink workspace roots remain rejected;
* cwd is never consulted as project authority;
* a model-supplied root is not authority (no such parameter exists);
* registry fingerprint behavior remains deterministic;
* no second registry/resolver implementation is introduced anywhere.

This suite adds tests only. No new registry, resolver, database, daemon or
watcher is created or required.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from aota_forge.composition.project_binding import (
    PROJECT_RESOLUTION_HEURISTIC_FALLBACK,
    derive_canonical_project_evidence,
    resolve_trusted_project_binding,
    resolve_trusted_project_evidence,
)
from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectManifestInvalidError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.project.discovery import fingerprint_registry, scan_projects
from aota_forge.core.project.lifecycle import PROJECT_REGISTRY_REIMPLEMENTED
from aota_forge.core.project.manifest import load_project, validate_project
from aota_forge.core.project.resolver import (
    load_workspace_registry,
    resolve_project,
    resolve_project_candidates,
    resolve_workspace,
)
from aota_forge.core.regression.fixtures import TempWorkspaceFixture

REPO_ROOT = Path(__file__).resolve().parents[1]
AF_ROOT = REPO_ROOT / "aota_forge"

VALID_MANIFEST = (
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


def _write_project(root: Path, project_id: str, *, manifest: str | None = None) -> Path:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        manifest if manifest is not None else VALID_MANIFEST.format(project_id=project_id),
        encoding="utf-8",
    )
    return root


def test_w1_manifest_validation_remains_pass(tmp_path: Path) -> None:
    root = _write_project(tmp_path / "proj-a", "proj-a")
    loaded_root, data = load_project(root / ".aota" / "project.yaml")
    assert loaded_root == root
    assert data["project"]["id"] == "proj-a"
    assert validate_project(data, root) is data


def test_w1_duplicate_project_ids_remain_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_project(workspace / "one", "dup-id")
    _write_project(workspace / "two", "dup-id")
    with pytest.raises(ProjectAmbiguousError):
        resolve_project(workspace, "dup-id")
    registry = tmp_path / "workspaces.json"
    registry.write_text('{"ws": {"candidates": ["%s"]}}' % workspace, encoding="utf-8")
    evidence = resolve_project_candidates("ws", registry, "dup-id")
    assert evidence.status == "NEEDS_SEMANTIC_CHOICE"
    assert len(evidence.candidates) == 2


def test_w1_symlink_project_manifest_remains_rejected(tmp_path: Path) -> None:
    real = _write_project(tmp_path / "real", "sym-project")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "link").symlink_to(real, target_is_directory=True)
    scanned = scan_projects(workspace, limit=None)
    assert scanned["project_count"] == 0
    # Direct symlink manifest probe: replace the manifest with a symlink.
    manifest = real / ".aota" / "project.yaml"
    backing = real / "backing.yaml"
    backing.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(backing)
    with pytest.raises(ProjectManifestInvalidError):
        load_project(manifest)


def test_w1_symlink_workspace_root_remains_rejected(tmp_path: Path) -> None:
    real_ws = tmp_path / "real-ws"
    _write_project(real_ws, "ws-project")
    link_ws = tmp_path / "linked-ws"
    link_ws.symlink_to(real_ws, target_is_directory=True)
    registry = tmp_path / "workspaces.json"
    registry.write_text('{"ws": {"candidates": ["%s"]}}' % link_ws, encoding="utf-8")
    with pytest.raises(ProjectRegistryInvalidError):
        resolve_workspace("ws", registry)
    with pytest.raises(ProjectRegistryInvalidError):
        derive_canonical_project_evidence(workspace_root=link_ws, project_id="ws-project")


def test_w1_cwd_is_not_consulted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    _write_project(workspace / "checkout", "cwd-project")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    evidence = derive_canonical_project_evidence(
        workspace_root=workspace, project_id="cwd-project"
    )
    assert evidence.status == "RESOLVED"
    assert evidence.candidates[0].project_id == "cwd-project"
    # Registry-backed resolution also ignores cwd entirely.
    registry = tmp_path / "workspaces.json"
    registry.write_text('{"ws": {"candidates": ["%s"]}}' % workspace, encoding="utf-8")
    binding = resolve_trusted_project_binding(
        project_id="cwd-project",
        registry_path=registry,
    )
    assert binding.status == "RESOLVED"
    assert binding.candidate is not None
    assert binding.candidate.project_id == "cwd-project"


def test_w1_model_supplied_root_is_not_authority() -> None:
    params = set(inspect.signature(resolve_trusted_project_binding).parameters)
    assert "project_root" not in params
    assert "path" not in params
    assert "cwd" not in params
    assert PROJECT_RESOLUTION_HEURISTIC_FALLBACK is False
    with pytest.raises(TypeError):
        resolve_trusted_project_binding(project_id="p", project_root="/tmp/whatever")  # type: ignore[call-arg]


def test_w1_registry_fingerprint_remains_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_project(workspace / "a", "proj-a")
    _write_project(workspace / "b", "proj-b")
    first = scan_projects(workspace, limit=None)
    second = scan_projects(workspace, limit=None)
    fp1 = fingerprint_registry("ws", first["projects"], first["invalid"])
    fp2 = fingerprint_registry("ws", second["projects"], second["invalid"])
    assert fp1 == fp2
    assert len(fp1) == 64
    # Order-insensitive over records by construction.
    fp3 = fingerprint_registry("ws", list(reversed(first["projects"])), first["invalid"])
    assert fp3 == fp1


def test_w1_no_parallel_registry_or_resolver_created() -> None:
    assert PROJECT_REGISTRY_REIMPLEMENTED is False
    definitions = {
        "def scan_projects(": [],
        "def load_workspace_registry(": [],
        "def resolve_project_candidates(": [],
        "def resolve_project_across_workspaces(": [],
        "def bind_worktree_sandbox(": [],
    }
    for path in AF_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in definitions:
            if needle in text:
                definitions[needle].append(path.relative_to(REPO_ROOT).as_posix())
    assert definitions["def scan_projects("] == ["aota_forge/core/project/discovery.py"]
    assert definitions["def load_workspace_registry("] == ["aota_forge/core/project/resolver.py"]
    assert definitions["def resolve_project_candidates("] == ["aota_forge/core/project/resolver.py"]
    assert definitions["def resolve_project_across_workspaces("] == ["aota_forge/core/project/resolver.py"]
    assert definitions["def bind_worktree_sandbox("] == ["aota_forge/work_plane/worktree_sandbox.py"]


def test_w1_legacy_evidence_helper_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_project(workspace, "legacy-project")
    evidence = resolve_trusted_project_evidence(
        worktree_root=workspace, project_id="legacy-project"
    )
    assert evidence.status == "RESOLVED"
    assert len(evidence.candidates) == 1
    assert evidence.candidates[0].project_id == "legacy-project"


def test_w1_load_registry_validation_shared_for_write_path(tmp_path: Path) -> None:
    from aota_forge.core.project.resolver import validate_workspace_registry_data

    registry = tmp_path / "workspaces.json"
    registry.write_text('{"ws": {"candidates": []}}', encoding="utf-8")
    with pytest.raises(ProjectRegistryInvalidError):
        load_workspace_registry(registry)
    with pytest.raises(ProjectRegistryInvalidError):
        validate_workspace_registry_data({"ws": {"candidates": []}})
    assert validate_workspace_registry_data({"ws": {"candidates": ["/x"]}}) == {
        "ws": {"candidates": ["/x"]}
    }


def test_w1_temp_workspace_fixture_still_resolves() -> None:
    with TempWorkspaceFixture(prefix="af55-w1-") as fixture:
        fixture.create_project("fixture-project")
        registry = fixture.create_registry("fixture-ws")
        evidence = resolve_project_candidates("fixture-ws", registry, "fixture-project")
        assert evidence.status == "RESOLVED"
        assert evidence.candidates[0].project_id == "fixture-project"
