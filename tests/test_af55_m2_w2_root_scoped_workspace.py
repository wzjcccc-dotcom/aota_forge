"""AF #55 M2/W2 — Authorized-root-scoped workspace read/search.

Deterministic local-only proofs:

* task-main reads the canonical project-main root explicitly and keeps the
  active-worktree as the default read root
* worker authority reaches only the assigned active-worktree (no inheritance)
* search defaults to the granted search-capable roots, tagged per result;
  explicit root_ref selects exactly one granted root; same-path roots dedupe
* unknown / path-shaped root_ref and ungranted Governance roots fail closed
* sibling projects and absolute host paths remain unreachable
* workspace.write stays active-worktree-only / role-gated (narrow write)
* Plan access is not via workspace.read (GitHub surface owns Plan reads)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aota_forge.core.providers.tool import ToolRequest
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.work_plane.authorized_roots import (
    authorized_roots_for_task_main,
    authorized_roots_for_worker,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    BoundedWorkspaceMutationProvider,
    create_workspace_mutation_authority,
)
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    BoundedWorkspaceToolProvider,
    create_broad_workspace_read_authority,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

PROJECT_ID = "aota-reader"
WORKTREE_ID = "wt-m2w2"


def _sandbox(project_root: Path, worktree_root: Path):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-m2w2",
        workspace_root=str(project_root),
        project_id=PROJECT_ID,
        project_root=str(project_root),
        manifest_path=str(project_root / ".aota" / "project.yaml"),
        name="test",
        kind="project",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-m2w2",
        workspace_root=str(project_root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id=WORKTREE_ID, worktree_root=worktree_root)


def _handoff(role: str) -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="test-kind",
        objective="test objective",
        bounded_scope="test bounded scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )


def _task_main_authority(sandbox, operation):
    return create_broad_workspace_read_authority(
        sandbox,
        operation,
        handoff=None,
        applicable_policies=(),
        authorized_roots=authorized_roots_for_task_main(sandbox),
    )


def _worker_authority(sandbox, operation, role="coder"):
    return create_workspace_authority(
        sandbox,
        _handoff(role),
        (),
        operation,
        authorized_roots=authorized_roots_for_worker(sandbox),
    )


def _read(provider, path, root_ref=None, **extra):
    inputs = {"path": path, **extra}
    if root_ref is not None:
        inputs["root_ref"] = root_ref
    return provider.invoke(ToolRequest(operation=provider.authority.operation, inputs=inputs))


def _search(provider, query, root_ref=None, **extra):
    inputs = {"query": query, **extra}
    if root_ref is not None:
        inputs["root_ref"] = root_ref
    return provider.invoke(ToolRequest(operation=provider.authority.operation, inputs=inputs))


def _fixture(tmp_root: Path):
    project_root = tmp_root / "chatgpt-hermes-mcp-poc"
    worktree_root = tmp_root / "active-worktree"
    project_root.mkdir(parents=True)
    worktree_root.mkdir(parents=True)
    (project_root / "project_only.txt").write_text("canonical project source needle", encoding="utf-8")
    (worktree_root / "worktree_only.txt").write_text("worktree change needle", encoding="utf-8")
    (project_root / "shared.txt").write_text("shared content needle", encoding="utf-8")
    sibling = tmp_root / "unrelated-sibling-project"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("sibling needle secret", encoding="utf-8")
    return project_root, worktree_root, sibling


def test_descriptors_carry_optional_root_ref() -> None:
    for descriptor in (WORKSPACE_READ_DESCRIPTOR, WORKSPACE_SEARCH_DESCRIPTOR):
        names = [spec.name for spec in descriptor.inputs]
        assert "root_ref" in names
        spec = next(s for s in descriptor.inputs if s.name == "root_ref")
        assert spec.type == "str?"
    write_names = [spec.name for spec in WORKSPACE_WRITE_DESCRIPTOR.inputs]
    assert "root_ref" not in write_names


def test_task_main_read_default_is_active_worktree() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        resp = _read(provider, "worktree_only.txt")
        assert resp.ok is True
        assert resp.payload["root_ref"] == "active-worktree"
        # protocol: project-main-only file is NOT reachable without explicit root_ref
        miss = _read(provider, "project_only.txt")
        assert miss.ok is False
        assert miss.error["code"] == "NOT_FOUND"


def test_task_main_read_project_main_explicit_root_ref() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        resp = _read(provider, "project_only.txt", root_ref="project-main")
        assert resp.ok is True
        assert resp.payload["root_ref"] == "project-main"
        assert "canonical project source needle" in resp.payload["content"]


def test_worker_cannot_read_project_main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_worker_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        denied = _read(provider, "project_only.txt", root_ref="project-main")
        assert denied.ok is False
        assert denied.error["code"] == "AUTHORIZED_ROOT_UNKNOWN"
        # the default (and only) granted root still works
        ok = _read(provider, "worktree_only.txt")
        assert ok.ok is True
        assert ok.payload["root_ref"] == "active-worktree"


def test_unknown_and_path_shaped_root_ref_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, sibling = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        unknown = _read(provider, "shared.txt", root_ref="sibling-project")
        assert unknown.ok is False
        assert unknown.error["code"] == "AUTHORIZED_ROOT_UNKNOWN"
        for bad in ("/etc", "../unrelated-sibling-project", str(sibling), "a/b"):
            resp = _read(provider, "shared.txt", root_ref=bad)
            assert resp.ok is False
            assert resp.error["code"] == "INVALID_ROOT_REF"


def test_prepared_governance_roots_not_granted() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        for ref in ("local-governance", "authorized-evidence", "plan"):
            resp = _read(provider, "shared.txt", root_ref=ref)
            assert resp.ok is False
            assert resp.error["code"] == "AUTHORIZED_ROOT_UNKNOWN"


def test_search_default_covers_granted_roots_tagged() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        resp = _search(provider, "needle")
        assert resp.ok is True
        refs = {item["root_ref"] for item in resp.payload["results"]}
        assert refs == {"project-main", "active-worktree"}
        assert resp.payload["root_refs"] == ["active-worktree", "project-main"]
        paths = {(item["root_ref"], item["path"]) for item in resp.payload["results"]}
        assert ("active-worktree", "worktree_only.txt") in paths
        assert ("project-main", "project_only.txt") in paths


def test_search_explicit_root_ref_selects_one_root() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        resp = _search(provider, "needle", root_ref="project-main")
        assert resp.ok is True
        assert resp.payload["root_refs"] == ["project-main"]
        assert {item["root_ref"] for item in resp.payload["results"]} == {"project-main"}
        assert all(item["path"] != "worktree_only.txt" for item in resp.payload["results"])


def test_search_same_path_roots_dedupe() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "checkout"
        root.mkdir()
        (root / "a.txt").write_text("needle", encoding="utf-8")
        sandbox = _sandbox(root, root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        resp = _search(provider, "needle")
        assert resp.ok is True
        assert resp.payload["root_refs"] == ["active-worktree"]
        assert len(resp.payload["results"]) == 1


def test_worker_search_sees_only_worktree() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_worker_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        resp = _search(provider, "needle")
        assert resp.ok is True
        assert resp.payload["root_refs"] == ["active-worktree"]
        assert {item["path"] for item in resp.payload["results"]} == {"worktree_only.txt"}


def test_known_but_read_denied_root_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        from aota_forge.work_plane.authorized_roots import AuthorizedRoot, AuthorizedRootSet

        search_only_set = AuthorizedRootSet(
            session_kind="task-main",
            roots=(
                AuthorizedRoot(
                    root_ref="project-main",
                    root_kind="project-main",
                    root_path=sandbox.project_root,
                    capabilities=frozenset({"search"}),
                    project_id=sandbox.project_id,
                ),
                AuthorizedRoot(
                    root_ref="active-worktree",
                    root_kind="active-worktree",
                    root_path=sandbox.worktree_root,
                    capabilities=frozenset({"search", "read", "write"}),
                    project_id=sandbox.project_id,
                    worktree_id=sandbox.worktree_id,
                ),
            ),
        )
        authority = create_broad_workspace_read_authority(
            sandbox,
            WORKSPACE_READ_DESCRIPTOR,
            handoff=None,
            applicable_policies=(),
            authorized_roots=search_only_set,
        )
        provider = BoundedWorkspaceToolProvider(authority)
        # known root, but read capability is not granted: fail closed
        denied = _read(provider, "project_only.txt", root_ref="project-main")
        assert denied.ok is False
        assert denied.error["code"] == "AUTHORIZED_ROOT_CAPABILITY_DENIED"
        # the trusted default root still works
        ok = _read(provider, "worktree_only.txt")
        assert ok.ok is True
        assert ok.payload["root_ref"] == "active-worktree"


def test_omitted_root_ref_without_read_default_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        from aota_forge.work_plane.authorized_roots import AuthorizedRoot, AuthorizedRootSet

        no_default_set = AuthorizedRootSet(
            session_kind="task-main",
            roots=(
                AuthorizedRoot(
                    root_ref="project-main",
                    root_kind="project-main",
                    root_path=sandbox.project_root,
                    capabilities=frozenset({"search", "read"}),
                    project_id=sandbox.project_id,
                ),
            ),
        )
        authority = create_broad_workspace_read_authority(
            sandbox,
            WORKSPACE_READ_DESCRIPTOR,
            handoff=None,
            applicable_policies=(),
            authorized_roots=no_default_set,
        )
        provider = BoundedWorkspaceToolProvider(authority)
        # no read-capable active-worktree -> omission is ambiguous; typed failure
        denied = _read(provider, "project_only.txt")
        assert denied.ok is False
        assert denied.error["code"] == "AUTHORIZED_ROOT_CAPABILITY_DENIED"
        # explicit root_ref remains the normal bounded read model
        ok = _read(provider, "project_only.txt", root_ref="project-main")
        assert ok.ok is True


def test_same_relative_path_distinguished_by_root_ref() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        (project_root / "shared.txt").write_text("project-main revision", encoding="utf-8")
        (worktree_root / "shared.txt").write_text("active-worktree revision", encoding="utf-8")
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        main = _read(provider, "shared.txt", root_ref="project-main")
        work = _read(provider, "shared.txt", root_ref="active-worktree")
        assert main.ok is True and work.ok is True
        assert main.payload["path"] == work.payload["path"] == "shared.txt"
        assert main.payload["root_ref"] == "project-main"
        assert work.payload["root_ref"] == "active-worktree"
        assert main.payload["content"] == "project-main revision"
        assert work.payload["content"] == "active-worktree revision"


def test_coder_write_outside_assigned_worktree_denied() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        authority = create_workspace_mutation_authority(
            sandbox,
            _handoff("coder"),
            (),
            WORKSPACE_WRITE_DESCRIPTOR,
            authorized_roots=authorized_roots_for_worker(sandbox),
        )
        provider = BoundedWorkspaceMutationProvider(authority)
        for bad in ("../project-only.txt", str(project_root / "x.txt"), "/tmp/x.txt"):
            resp = provider.invoke(
                ToolRequest(
                    operation=WORKSPACE_WRITE_DESCRIPTOR,
                    inputs={"path": bad, "content": "x", "mode": "create_only"},
                )
            )
            assert resp.ok is False
        assert not (project_root / "x.txt").exists()


def test_role_authority_alone_without_root_authority_denied() -> None:
    from aota_forge.work_plane.authorized_roots import AuthorizedRoot, AuthorizedRootSet
    from aota_forge.work_plane.workspace_mutation import WorkspaceMutationAuthorityError

    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        read_only_set = AuthorizedRootSet(
            session_kind="worker",
            roots=(
                AuthorizedRoot(
                    root_ref="active-worktree",
                    root_kind="active-worktree",
                    root_path=sandbox.worktree_root,
                    capabilities=frozenset({"search", "read"}),
                    project_id=sandbox.project_id,
                    worktree_id=sandbox.worktree_id,
                ),
            ),
        )
        # coder role authority alone (no write-capable root) cannot mint write
        with pytest.raises(WorkspaceMutationAuthorityError):
            create_workspace_mutation_authority(
                sandbox,
                _handoff("coder"),
                (),
                WORKSPACE_WRITE_DESCRIPTOR,
                authorized_roots=read_only_set,
            )


def test_root_authority_alone_without_role_authority_denied() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        # write-capable active-worktree root, but reviewer role must not mutate
        authority = create_workspace_mutation_authority(
            sandbox,
            _handoff("reviewer"),
            (),
            WORKSPACE_WRITE_DESCRIPTOR,
            authorized_roots=authorized_roots_for_worker(sandbox),
        )
        provider = BoundedWorkspaceMutationProvider(authority)
        resp = provider.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "reviewer.txt", "content": "x", "mode": "create_only"},
            )
        )
        assert resp.ok is False
        assert resp.error["code"] == "AUTHORITY_DENIED"
        assert not (worktree_root / "reviewer.txt").exists()


def test_unrelated_sibling_is_unreachable() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, sibling = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        read_provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        search_provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        # absolute host paths are rejected by the logical-ref grammar
        denied = _read(read_provider, str(sibling / "secret.txt"))
        assert denied.ok is False
        # .. traversal rejected
        denied = _read(read_provider, "../unrelated-sibling-project/secret.txt")
        assert denied.ok is False
        # scope traversal rejected
        denied = _search(search_provider, "needle", scope="../unrelated-sibling-project")
        assert denied.ok is False
        assert denied.error["code"] == "INVALID_SCOPE"
        # sibling content never appears in a default search
        resp = _search(search_provider, "sibling needle secret")
        assert resp.ok is True
        assert resp.payload["results"] == []


def test_search_scope_stays_inside_granted_root() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        (project_root / "docs").mkdir()
        (project_root / "docs" / "guide.md").write_text("scoped needle", encoding="utf-8")
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR))
        resp = _search(provider, "scoped needle", root_ref="project-main", scope="docs")
        assert resp.ok is True
        assert [item["path"] for item in resp.payload["results"]] == ["docs/guide.md"]
        missing = _search(provider, "scoped needle", root_ref="active-worktree", scope="docs")
        assert missing.ok is True
        assert missing.payload["results"] == []


def test_narrow_write_is_active_worktree_only() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        authority = create_workspace_mutation_authority(
            sandbox,
            _handoff("coder"),
            (),
            WORKSPACE_WRITE_DESCRIPTOR,
            authorized_roots=authorized_roots_for_worker(sandbox),
        )
        provider = BoundedWorkspaceMutationProvider(authority)
        resp = provider.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "new_file.txt", "content": "written", "mode": "create_only"},
            )
        )
        assert resp.ok is True
        assert resp.payload["root_ref"] == "active-worktree"
        assert (worktree_root / "new_file.txt").read_text(encoding="utf-8") == "written"
        assert not (project_root / "new_file.txt").exists()
        # the trusted root set still carries no project-main at all
        assert authority.authorized_root_set.get("project-main") is None
        assert authority.authorized_root_set.write_roots()[0].root_kind == "active-worktree"


def test_task_main_mutation_authority_is_role_denied() -> None:
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        authority = create_workspace_mutation_authority(
            sandbox,
            _handoff("task-main"),
            (),
            WORKSPACE_WRITE_DESCRIPTOR,
            authorized_roots=authorized_roots_for_task_main(sandbox),
        )
        provider = BoundedWorkspaceMutationProvider(authority)
        resp = provider.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "x.txt", "content": "x", "mode": "create_only"},
            )
        )
        assert resp.ok is False
        assert resp.error["code"] == "AUTHORITY_DENIED"
        assert not (project_root / "x.txt").exists()


def test_plan_read_is_not_workspace_read() -> None:
    from aota_forge.work_plane import workspace_tools

    assert workspace_tools.PLAN_READ_VIA_WORKSPACE_READ is False
    assert workspace_tools.GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED is False
    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, _ = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        provider = BoundedWorkspaceToolProvider(_task_main_authority(sandbox, WORKSPACE_READ_DESCRIPTOR))
        resp = _read(provider, "wzjcccc-dotcom/aota-hermes-tools#55")
        assert resp.ok is False
        assert resp.error["code"] == "INVALID_PATH"


def test_forged_root_set_cannot_enter_authority() -> None:
    from aota_forge.work_plane.authorized_roots import (
        AuthorizedRoot,
        AuthorizedRootSet,
        AuthorizedRootSetError,
    )

    with tempfile.TemporaryDirectory() as raw:
        project_root, worktree_root, sibling = _fixture(Path(raw))
        sandbox = _sandbox(project_root, worktree_root)
        forged = AuthorizedRootSet(
            session_kind="task-main",
            roots=(
                AuthorizedRoot(
                    root_ref="project-main",
                    root_kind="project-main",
                    root_path=str(sibling),
                    capabilities=frozenset({"read", "search"}),
                    project_id=sandbox.project_id,
                ),
            ),
        )
        with pytest.raises((AuthorizedRootSetError, ValueError)):
            create_broad_workspace_read_authority(
                sandbox,
                WORKSPACE_READ_DESCRIPTOR,
                handoff=None,
                applicable_policies=(),
                authorized_roots=forged,
            )
