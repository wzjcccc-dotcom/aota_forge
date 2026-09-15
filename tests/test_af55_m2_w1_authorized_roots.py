"""AF #55 M2/W1 — Authorized root_ref capability model.

Deterministic local-only proofs:

* task-main default roots = project-main (read/search) + active-worktree
  (read/search/write); project-main never carries write (narrow write)
* worker default roots = the assigned active-worktree only; no inheritance
* legacy single-root set preserves the accepted single-worktree behavior
* unknown / path-shaped root_ref fail closed with typed codes
* capability enforcement: write on project-main denied
* forged sibling roots fail mechanical sandbox validation
* prepared Governance 2.0 refs (authorized-evidence / local-governance) are
  not instantiable and no local Plan store is implemented
* §8 trusted projection separates plan / project / worktree and carries
  bounded root refs only (no physical paths)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.composition.project_binding import derive_canonical_project_evidence
from aota_forge.work_plane import authorized_roots as ar
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

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


def _project(root: Path, project_id: str = "demo") -> Path:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    return root


def _sandbox(tmp_path: Path, *, shared_root: bool):
    project = _project(tmp_path / "checkout")
    if shared_root:
        worktree = project
    else:
        worktree = tmp_path / "active-worktree"
        worktree.mkdir()
    evidence = derive_canonical_project_evidence(workspace_root=project, project_id="demo")
    return bind_worktree_sandbox(evidence, "wt-1", worktree)


def test_flags_freeze() -> None:
    assert ar.AUTHORIZED_MULTI_ROOT_READ_MODEL_MATERIALIZED is True
    assert ar.ROOT_REF_CAPABILITY_MECHANICALLY_ENFORCED is True
    assert ar.ROOT_REF_IS_BOUNDED_NAME_NOT_PATH is True
    assert ar.ROOTS_DERIVED_FROM_TRUSTED_SANDBOX_ONLY is True
    assert ar.MODEL_NOMINATES_ROOT_PATH is False
    assert ar.MODEL_CAN_MINT_ROOT_SET is False
    assert ar.WORKSPACE_ROOTS_ARE_AF_AUTHORIZED_SET is True
    assert ar.WORKSPACE_ROOTS_ARE_CWD is False
    assert ar.ARBITRARY_HOST_PATH_REACHABLE is False
    assert ar.UNRELATED_SIBLING_PROJECT_REACHABLE is False
    assert ar.CROSS_ROOT_ACCESS_REQUIRES_EXPLICIT_GRANT is True
    assert ar.WORKER_INHERITS_TASK_MAIN_ROOTS is False
    assert ar.WORKER_EXTRA_ROOT_REQUIRES_EXPLICIT_GRANT is True
    assert ar.NARROW_WRITE is True
    assert ar.SECOND_SANDBOX_SUBSYSTEM_CREATED is False
    assert ar.SECOND_AUTHORITY_ENGINE_CREATED is False
    assert ar.GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED is False
    assert ar.LOCAL_GOVERNANCE_ROOT_SUPPORTED is False
    assert ar.PREPARED_ROOT_REFS_INSTANTIATED is False


def test_task_main_roots(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_for_task_main(sandbox)
    assert roots.session_kind == "task-main"
    assert roots.names() == ("project-main", "active-worktree")
    project_main = roots.get("project-main")
    assert project_main is not None
    assert project_main.root_path == sandbox.project_root
    assert project_main.capabilities == frozenset({"search", "read"})
    assert project_main.has_capability("write") is False
    active = roots.get("active-worktree")
    assert active is not None
    assert active.root_path == sandbox.worktree_root
    assert active.capabilities == frozenset({"search", "read", "write"})
    assert active.worktree_id == sandbox.worktree_id
    # project-main is the physical project root, not the worktree
    assert project_main.root_path != active.root_path


def test_task_main_shared_root_same_path_two_refs(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_task_main(sandbox)
    assert roots.names() == ("project-main", "active-worktree")
    assert roots.get("project-main").root_path == roots.get("active-worktree").root_path
    # default read root is the active worktree (accepted single-root behavior)
    assert roots.default_read_root().root_ref == "active-worktree"


def test_worker_roots_do_not_inherit_task_main(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_for_worker(sandbox)
    assert roots.session_kind == "worker"
    assert roots.names() == ("active-worktree",)
    assert roots.get("project-main") is None
    assert roots.get("active-worktree").root_path == sandbox.worktree_root
    with pytest.raises(ar.AuthorizedRootUnknownError):
        ar.resolve_authorized_root(roots, "project-main", capability="read")


def test_legacy_single_root_set(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_single_root(sandbox)
    assert roots.session_kind == "single"
    assert roots.names() == ("active-worktree",)
    assert roots.default_read_root().root_ref == "active-worktree"
    assert roots.default_write_root().root_ref == "active-worktree"


def test_unknown_root_ref_fails_closed(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_task_main(sandbox)
    with pytest.raises(ar.AuthorizedRootUnknownError) as exc:
        ar.resolve_authorized_root(roots, "sibling-project")
    assert exc.value.code == "AUTHORIZED_ROOT_UNKNOWN"


def test_path_shaped_root_ref_fails_closed(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_task_main(sandbox)
    for bad in ("/tmp/anything", "../sibling", "a/b", "project main", "", " root ", "ROOT"):
        with pytest.raises(ar.AuthorizedRootReferenceError) as exc:
            ar.resolve_authorized_root(roots, bad)
        assert exc.value.code == "INVALID_ROOT_REF"


def test_non_string_root_ref_fails_closed(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_task_main(sandbox)
    for bad in (None, 1, ["project-main"], {"root_ref": "project-main"}):
        with pytest.raises(ar.AuthorizedRootReferenceError):
            ar.resolve_authorized_root(roots, bad)  # type: ignore[arg-type]


def test_capability_enforcement(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_for_task_main(sandbox)
    # read is fine on both roots
    assert ar.resolve_authorized_root(roots, "project-main", capability="read").root_ref == "project-main"
    assert ar.resolve_authorized_root(roots, "active-worktree", capability="read").root_ref == "active-worktree"
    # write is only granted on the active worktree (narrow write)
    assert ar.resolve_authorized_root(roots, "active-worktree", capability="write")
    with pytest.raises(ar.AuthorizedRootCapabilityError) as exc:
        ar.resolve_authorized_root(roots, "project-main", capability="write")
    assert exc.value.code == "AUTHORIZED_ROOT_CAPABILITY_DENIED"


def test_write_capability_not_constructible_on_project_main(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.AuthorizedRoot(
            root_ref="project-main",
            root_kind="project-main",
            root_path=sandbox.project_root,
            capabilities=frozenset({"read", "write"}),
            project_id=sandbox.project_id,
        )


def test_prepared_governance_roots_not_instantiable(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    for prepared in ("local-governance", "authorized-evidence"):
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRoot(
                root_ref=prepared,
                root_kind=prepared,
                root_path=sandbox.worktree_root,
                capabilities=frozenset({"read"}),
                project_id=sandbox.project_id,
            )


def test_forged_sibling_root_fails_sandbox_validation(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    sibling = tmp_path / "unrelated-sibling"
    sibling.mkdir()
    forged = ar.AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=str(sibling),
        capabilities=frozenset({"read", "search"}),
        project_id=sandbox.project_id,
    )
    root_set = ar.AuthorizedRootSet(session_kind="task-main", roots=(forged,))
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.validate_root_set_against_sandbox(root_set, sandbox)
    # and the trusted factory never produces it
    trusted = ar.authorized_roots_for_task_main(sandbox)
    assert trusted.get("project-main").root_path == sandbox.project_root
    assert str(sibling) not in trusted.canonical_json()


def test_worker_root_set_rejects_project_main(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    project_main = ar.AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=sandbox.project_root,
        capabilities=frozenset({"read", "search"}),
        project_id=sandbox.project_id,
    )
    active = ar.AuthorizedRoot(
        root_ref="active-worktree",
        root_kind="active-worktree",
        root_path=sandbox.worktree_root,
        capabilities=frozenset({"read", "search", "write"}),
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
    )
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.AuthorizedRootSet(session_kind="worker", roots=(project_main, active))


def test_duplicate_root_ref_rejected(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    first = ar.AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=sandbox.project_root,
        capabilities=frozenset({"search", "read"}),
        project_id=sandbox.project_id,
    )
    duplicate = ar.AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=sandbox.worktree_root,
        capabilities=frozenset({"search", "read"}),
        project_id=sandbox.project_id,
    )
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.AuthorizedRootSet(session_kind="task-main", roots=(first, duplicate))


def test_default_read_root_requires_active_worktree(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    project_main_only = ar.AuthorizedRoot(
        root_ref="project-main",
        root_kind="project-main",
        root_path=sandbox.project_root,
        capabilities=frozenset({"search", "read"}),
        project_id=sandbox.project_id,
    )
    roots = ar.AuthorizedRootSet(session_kind="task-main", roots=(project_main_only,))
    ar.validate_root_set_against_sandbox(roots, sandbox)
    # No heuristic fallback: omission without a read-capable active-worktree
    # is ambiguous and fails closed (an explicit root_ref is required).
    with pytest.raises(ar.AuthorizedRootCapabilityError):
        roots.default_read_root()


def test_root_model_never_consults_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots_before = ar.authorized_roots_for_task_main(sandbox)
    elsewhere = tmp_path / "elsewhere-cwd"
    (elsewhere / ".aota").mkdir(parents=True)
    (elsewhere / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id="demo"), encoding="utf-8"
    )
    monkeypatch.chdir(elsewhere)
    roots_after = ar.authorized_roots_for_task_main(sandbox)
    assert roots_before.digest() == roots_after.digest()
    assert str(elsewhere) not in roots_after.canonical_json()
    assert all(root.root_path != str(elsewhere) for root in roots_after.roots)


def test_root_set_digest_is_deterministic(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    a = ar.authorized_roots_for_task_main(sandbox)
    b = ar.authorized_roots_for_task_main(sandbox)
    assert a.digest() == b.digest()
    assert ar.authorized_roots_for_worker(sandbox).digest() != a.digest()


def test_public_roots_hide_physical_paths(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_for_task_main(sandbox)
    public = roots.public_roots()
    assert all(set(item) == {"root_ref", "kind", "capabilities"} for item in public)
    serialized = str(public)
    assert sandbox.project_root not in serialized
    assert sandbox.worktree_root not in serialized


def test_projection_separates_plan_project_worktree(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=False)
    roots = ar.authorized_roots_for_task_main(sandbox)
    projection = ar.build_trusted_project_context_projection(
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        roots=roots,
        plan_ref="wzjcccc-dotcom/aota-hermes-tools#55",
        governing_repository="wzjcccc-dotcom/aota-hermes-tools",
        source_repository="wzjcccc-dotcom/aota_reader_mcp",
    )
    assert projection["plan"] == {
        "plan_ref": "wzjcccc-dotcom/aota-hermes-tools#55",
        "governing_repository": "wzjcccc-dotcom/aota-hermes-tools",
    }
    assert projection["project"] == {
        "project_id": "demo",
        "source_repository": "wzjcccc-dotcom/aota_reader_mcp",
        "root_ref": "project-main",
    }
    assert projection["worktree"] == {"worktree_id": "wt-1", "root_ref": "active-worktree"}
    assert projection["roots"] == {
        "project-main": ["read", "search"],
        "active-worktree": ["read", "search", "write"],
    }
    # governing repository is never the project root revision
    assert projection["project"]["source_repository"] != projection["plan"]["governing_repository"]
    serialized = str(projection)
    assert sandbox.project_root not in serialized
    assert sandbox.worktree_root not in serialized


def test_projection_without_plan_is_honest(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_worker(sandbox)
    projection = ar.build_trusted_project_context_projection(
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        roots=roots,
    )
    assert projection["plan"] is None
    assert projection["project"]["root_ref"] == "active-worktree"
    assert projection["worktree"]["root_ref"] == "active-worktree"


def test_projection_partial_plan_identity_fails_closed(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path, shared_root=True)
    roots = ar.authorized_roots_for_task_main(sandbox)
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.build_trusted_project_context_projection(
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            roots=roots,
            plan_ref="wzjcccc-dotcom/aota-hermes-tools#55",
        )
    with pytest.raises(ar.AuthorizedRootSetError):
        ar.build_trusted_project_context_projection(
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            roots=roots,
            governing_repository="wzjcccc-dotcom/aota-hermes-tools",
        )
