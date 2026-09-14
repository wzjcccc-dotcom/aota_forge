"""AF #55 M1/W2 — Trusted Project Registration & Reconciliation Lifecycle.

Deterministic, local-only proofs over the existing registry/resolver
foundation (no network, no real operator state mutation):

* trusted workspace + valid project → registration PASS
* unregistered workspace → denied
* candidate outside workspace → denied
* symlink project root → denied
* invalid manifest → denied
* duplicate project identity → fail closed
* write-through + read-back → PASS (atomic, schema-validated)
* reconciliation with unchanged project → PASS
* missing registered root → typed failure
* changed manifest identity → typed failure
* no manual path similarity fallback (auto-rebind markers absent)
* retirement removes authorization atomically
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aota_forge.core.contracts.errors import (
    GitNotFoundError,
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ProjectReconciliationDriftError,
    ProjectRegistryInvalidError,
    ProjectSourceRepositoryMismatchError,
)
from aota_forge.core.project.lifecycle import (
    AUTO_REBIND_BY_GIT_REPO_NAME,
    AUTO_REBIND_BY_NAME,
    AUTO_REBIND_BY_NEAREST_PATH,
    BACKGROUND_DAEMON_REQUIRED,
    CONTINUOUS_FILESYSTEM_WATCHER,
    MANUAL_REGISTRY_DRIFT_REQUIRED,
    MODEL_SELF_GRANTS_PROJECT_AUTHORITY,
    PROJECT_REGISTRY_HAS_LIFECYCLE_OWNER,
    PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATION,
    REGISTRY_WRITE_ATOMIC,
    REGISTRY_WRITE_READBACK_VERIFIED,
    REGISTRY_WRITE_SCHEMA_VALIDATED,
    register_project,
    reconcile_project,
    retire_workspace_authorization,
)
from aota_forge.core.project.lifecycle import _validate_candidate_checkout
from aota_forge.core.project.resolver import load_workspace_registry

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


def _create_checkout(parent: Path, folder_name: str, project_id: str, origin: str | None = None) -> Path:
    root = parent / folder_name
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    if origin is not None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def _registry(path: Path, data: dict | None = None) -> Path:
    path.write_text(json.dumps(data if data is not None else {}), encoding="utf-8")
    return path


def test_w2_trusted_workspace_valid_project_registration_pass(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkout = _create_checkout(workspace, "unrelated-folder-name", "odd-project")
    registry = _registry(tmp_path / "workspaces.json")

    evidence = register_project(
        workspace_id="odd-project",
        registry_path=registry,
        project_id="odd-project",
        workspace_root=workspace,
    )
    assert evidence.status == "REGISTERED"
    assert evidence.registry_updated is True
    assert evidence.project_root == str(checkout)
    assert evidence.workspace_id == "odd-project"
    stored = load_workspace_registry(registry)
    assert stored == {"odd-project": {"candidates": [str(workspace)]}}


def test_w2_registration_does_not_require_folder_name_match(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkout = _create_checkout(workspace, "chatgpt-hermes-mcp-poc", "aota-reader")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    evidence = register_project(
        workspace_id="ws", registry_path=registry, project_id="aota-reader"
    )
    assert evidence.project_root == str(checkout)
    assert evidence.registry_updated is False


def test_w2_unregistered_workspace_denied(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "workspaces.json")
    with pytest.raises(ProjectNotFoundError):
        register_project(
            workspace_id="not-authorized", registry_path=registry, project_id="p"
        )


def test_w2_candidate_outside_workspace_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = _create_checkout(tmp_path / "outside", "escape", "escape-project")
    with pytest.raises(ProjectRegistryInvalidError):
        _validate_candidate_checkout(outside, workspace)


def test_w2_symlink_project_root_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    real = _create_checkout(tmp_path / "real", "real-proj", "link-project")
    link = workspace / "linked"
    link.symlink_to(real, target_is_directory=True)
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectNotFoundError):
        register_project(workspace_id="ws", registry_path=registry, project_id="link-project")
    with pytest.raises(ProjectRegistryInvalidError):
        _validate_candidate_checkout(link, workspace)


def test_w2_invalid_manifest_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "broken" / ".aota").mkdir(parents=True)
    (workspace / "broken" / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id="Broken ID"), encoding="utf-8"
    )
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectNotFoundError):
        register_project(workspace_id="ws", registry_path=registry, project_id="broken-id")


def test_w2_duplicate_project_identity_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "one", "dup-project")
    _create_checkout(workspace, "two", "dup-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectAmbiguousError):
        register_project(workspace_id="ws", registry_path=registry, project_id="dup-project")


def test_w2_write_through_readback_and_idempotence(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "write-project")
    registry = _registry(tmp_path / "workspaces.json")
    first = register_project(
        workspace_id="write-project",
        registry_path=registry,
        project_id="write-project",
        workspace_root=workspace,
    )
    assert first.registry_updated is True
    assert REGISTRY_WRITE_ATOMIC is True
    assert REGISTRY_WRITE_SCHEMA_VALIDATED is True
    assert REGISTRY_WRITE_READBACK_VERIFIED is True
    snapshot = registry.read_text(encoding="utf-8")
    second = register_project(
        workspace_id="write-project",
        registry_path=registry,
        project_id="write-project",
        workspace_root=workspace,
    )
    assert second.registry_updated is False
    assert registry.read_text(encoding="utf-8") == snapshot


def test_w2_registration_conflict_requires_explicit_retirement(tmp_path: Path) -> None:
    first_ws = tmp_path / "first-ws"
    first_ws.mkdir()
    _create_checkout(first_ws, "checkout", "conflict-project")
    second_ws = tmp_path / "second-ws"
    second_ws.mkdir()
    _create_checkout(second_ws, "checkout", "conflict-project")
    registry = _registry(
        tmp_path / "workspaces.json", {"ws": {"candidates": [str(first_ws)]}}
    )
    with pytest.raises(ProjectAmbiguousError):
        register_project(
            workspace_id="ws",
            registry_path=registry,
            project_id="conflict-project",
            workspace_root=second_ws,
        )


def test_w2_reconcile_unchanged_project_pass(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkout = _create_checkout(
        workspace, "checkout", "stable-project", origin="https://github.com/owner/stable_repo.git"
    )
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    registered = register_project(
        workspace_id="ws",
        registry_path=registry,
        project_id="stable-project",
        source_repository="owner/stable_repo",
    )
    evidence = reconcile_project(
        workspace_id="ws",
        registry_path=registry,
        project_id="stable-project",
        source_repository="owner/stable_repo",
        expected_project_root=registered.project_root,
    )
    assert evidence.status == "RECONCILED"
    assert evidence.project_root == str(checkout)
    assert evidence.repository_identity is not None
    assert evidence.repository_identity["verified"] is True


def test_w2_reconcile_missing_registered_root_typed_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkout = _create_checkout(workspace, "checkout", "gone-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    import shutil

    shutil.rmtree(checkout)
    with pytest.raises(ProjectNotFoundError):
        reconcile_project(workspace_id="ws", registry_path=registry, project_id="gone-project")


def test_w2_reconcile_changed_manifest_identity_typed_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkout = _create_checkout(workspace, "checkout", "changing-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    (checkout / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id="renamed-project"), encoding="utf-8"
    )
    with pytest.raises(ProjectNotFoundError):
        reconcile_project(workspace_id="ws", registry_path=registry, project_id="changing-project")


def test_w2_reconcile_root_drift_typed_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "drift-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectReconciliationDriftError):
        reconcile_project(
            workspace_id="ws",
            registry_path=registry,
            project_id="drift-project",
            expected_project_root=str(tmp_path / "somewhere-else"),
        )


def test_w2_reconcile_wrong_repository_identity_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(
        workspace, "checkout", "repo-project", origin="https://github.com/owner/other_repo.git"
    )
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        reconcile_project(
            workspace_id="ws",
            registry_path=registry,
            project_id="repo-project",
            source_repository="owner/repo_project",
        )


def test_w2_registration_repository_identity_verified(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(
        workspace, "chatgpt-hermes-mcp-poc", "aota-reader",
        origin="git@github.com:wzjcccc-dotcom/aota_reader_mcp.git",
    )
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    evidence = register_project(
        workspace_id="ws",
        registry_path=registry,
        project_id="aota-reader",
        source_repository="wzjcccc-dotcom/aota_reader_mcp",
    )
    assert evidence.repository_identity is not None
    assert evidence.repository_identity["verified"] is True
    assert evidence.repository_identity["git_origin_normalized"] == "wzjcccc-dotcom/aota_reader_mcp"


def test_w2_registration_wrong_repository_identity_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(
        workspace, "checkout", "mismatch-project",
        origin="https://github.com/owner/actual_repo.git",
    )
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        register_project(
            workspace_id="ws",
            registry_path=registry,
            project_id="mismatch-project",
            source_repository="owner/declared_repo",
        )


def test_w2_no_manual_path_similarity_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "similar-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    # A similarly named folder with no manifest must be ignored entirely.
    (workspace / "similar-project").mkdir()
    evidence = reconcile_project(
        workspace_id="ws", registry_path=registry, project_id="similar-project"
    )
    assert evidence.project_root == str(workspace / "checkout")
    assert AUTO_REBIND_BY_NAME is False
    assert AUTO_REBIND_BY_NEAREST_PATH is False
    assert AUTO_REBIND_BY_GIT_REPO_NAME is False


def test_w2_retirement_updates_registry_atomically(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "retire-project")
    registry = _registry(tmp_path / "workspaces.json")
    register_project(
        workspace_id="retire-project",
        registry_path=registry,
        project_id="retire-project",
        workspace_root=workspace,
    )
    result = retire_workspace_authorization(
        workspace_id="retire-project", registry_path=registry, workspace_root=workspace
    )
    assert result["status"] == "RETIRED"
    assert result["workspace_entry_removed"] is True
    assert load_workspace_registry(registry) == {}
    with pytest.raises(ProjectNotFoundError):
        register_project(
            workspace_id="retire-project", registry_path=registry, project_id="retire-project"
        )


def test_w2_reconcile_dispatchable_through_canonical_core_ingress(tmp_path: Path) -> None:
    from aota_forge.core import execute
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "ingress-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    register_project(workspace_id="ws", registry_path=registry, project_id="ingress-project")
    assert "project.reconcile" in DEFAULT_REGISTRY.names()
    response = execute(
        "project.reconcile",
        {"workspace_id": "ws", "project_id": "ingress-project", "registry_path": str(registry)},
        principal="reg-operator",
    )
    assert response.get("ok") is True, response
    assert response["data"]["status"] == "RECONCILED"


def test_w2_registration_is_not_agent_visible_or_core_dispatchable(tmp_path: Path) -> None:
    from aota_forge.core import execute
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.project.lifecycle import (
        PROJECT_RECONCILE_IS_AGENT_VISIBLE_OPERATION,
        PROJECT_REGISTRATION_IS_AGENT_VISIBLE_OPERATION,
        PROJECT_REGISTRATION_IS_CORE_INGRESS_OPERATION,
        PROJECT_REGISTRATION_OPERATION_KIND,
        PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATOR_CHANNEL,
    )
    from aota_forge.mcp_transport import LOGICAL_OPERATIONS

    assert "project.register" not in DEFAULT_REGISTRY.names()
    assert "project.register" not in set(LOGICAL_OPERATIONS)
    assert "project.reconcile" not in set(LOGICAL_OPERATIONS)
    assert PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATOR_CHANNEL is True
    assert PROJECT_REGISTRATION_IS_CORE_INGRESS_OPERATION is False
    assert PROJECT_REGISTRATION_IS_AGENT_VISIBLE_OPERATION is False
    assert PROJECT_RECONCILE_IS_AGENT_VISIBLE_OPERATION is False
    assert PROJECT_REGISTRATION_OPERATION_KIND == "control_plane_operator_operation"
    registry = _registry(tmp_path / "workspaces.json")
    response = execute(
        "project.register",
        {"workspace_id": "ws", "project_id": "p", "registry_path": str(registry)},
        principal="reg-operator",
    )
    assert response.get("ok") is not True
    assert response.get("error", {}).get("code") == "UNSUPPORTED_OPERATION"


def test_w2_lifecycle_owner_markers() -> None:
    assert PROJECT_REGISTRY_HAS_LIFECYCLE_OWNER is True
    assert PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATION is True
    assert MODEL_SELF_GRANTS_PROJECT_AUTHORITY is False
    assert MANUAL_REGISTRY_DRIFT_REQUIRED is False
    assert BACKGROUND_DAEMON_REQUIRED is False
    assert CONTINUOUS_FILESYSTEM_WATCHER is False


def test_w2_git_absent_fails_closed_when_repository_declared(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "no-git-project")
    registry = _registry(tmp_path / "workspaces.json", {"ws": {"candidates": [str(workspace)]}})
    with pytest.raises(GitNotFoundError):
        register_project(
            workspace_id="ws",
            registry_path=registry,
            project_id="no-git-project",
            source_repository="owner/whatever",
        )


def test_w2_registry_symlink_write_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _create_checkout(workspace, "checkout", "sym-registry")
    real = _registry(tmp_path / "real-registry.json")
    link = tmp_path / "workspaces.json"
    link.symlink_to(real)
    with pytest.raises(ProjectRegistryInvalidError):
        register_project(
            workspace_id="sym-registry",
            registry_path=link,
            project_id="sym-registry",
            workspace_root=workspace,
        )
