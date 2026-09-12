"""AF #50 M1/W2 — Generic Project-relative Workspace Path Grammar Repair (I40-B006).

Repairs I40-B006: the shared governed workspace resource grammar rejected valid
project-relative path segments beginning with ``_`` (``__init__.py``,
``__main__.py``, ``_private.py``, ``src/_internal/module.py``) before any
containment/authority evaluation.

Frozen contract under proof:

    UNDERSCORE_LEADING_SEGMENTS_ALLOWED=yes
    LEADING_DOT_PATH_POLICY=unchanged_fail_closed
    WRITE_ONLY_UNDERSCORE_EXCEPTION_ALLOWED=no
    PYTHON_SPECIFIC_EXCEPTION_ALLOWED=no
    CALCULATOR_SPECIFIC_EXCEPTION_ALLOWED=no

This is generic project-path compatibility through the ONE canonical shared
resource grammar (``resolve_worktree_resource``) consumed by workspace.read,
workspace.search, workspace.write, restricted shell/test resource resolution,
and selective hydration. No containment, traversal, symlink, cross-project or
cross-worktree protection is weakened.

Proof boundary (honest):
  PROVES=deterministic V1 shared-grammar matrix (positive + fail-closed
         negatives + symlink/cross-project containment) and a targeted V2 over
         the real governed workspace.read/workspace.write providers on a
         bounded fixture worktree.
  DOES_NOT_PROVE=real Hermes Worker execution or integrated RV1 acceptance.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.core.providers.tool import ToolRequest
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_mutation import (
    BoundedWorkspaceMutationProvider,
    WORKSPACE_WRITE_DESCRIPTOR,
    create_workspace_mutation_authority,
)
from aota_forge.work_plane.workspace_tools import (
    BoundedWorkspaceToolProvider,
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_resources import (
    B006_SHARED_RESOURCE_GRAMMAR_REPAIR,
    CALCULATOR_SPECIFIC_EXCEPTION_ALLOWED,
    HIDDEN_DOT_PATH_POLICY_CHANGE,
    LEADING_DOT_PATH_POLICY,
    LEADING_DOT_SEGMENT_ALLOWED,
    PYTHON_SPECIFIC_EXCEPTION_ALLOWED,
    UNDERSCORE_LEADING_SEGMENTS_ALLOWED,
    WRITE_ONLY_UNDERSCORE_EXCEPTION_ALLOWED,
    WorktreeResourceContainmentError,
    WorktreeResourceReferenceError,
    WorktreeResourceSymlinkError,
    resolve_worktree_resource,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

PROJECT_ID = "proj-af50-w2"
WORKSPACE_ID = "ws-af50-w2"
WORKTREE_ID = "wt-af50-w2"

UNDERSCORE_PATHS = (
    "__init__.py",
    "__main__.py",
    "_private.py",
    "src/pkg/__init__.py",
    "src/_internal/module.py",
)

# fail-closed negative matrix (grammar/reference level)
NEGATIVE_REFS = (
    ".",
    "..",
    "../x",
    "a/../b",
    "/x",
    "a//b",
    "a/b/",
    "a\\b",
    "a\x00b",
    "a\x01b",
    " leading",
    "trailing ",
    "x" * 129,
    "/".join(["a"] * 65),
    "x" * 513,
)
LEADING_DOT_REFS = (
    ".github",
    ".env",
    ".editorconfig",
    ".github/workflows/ci.yml",
    "src/.hidden/file.py",
    "src/pkg/.cache/x.pyc",
)


# ---------------------------------------------------------------------------
# Fixture world
# ---------------------------------------------------------------------------


def _make_sandbox(
    tmp_root: Path,
    *,
    workspace_id: str = WORKSPACE_ID,
    project_id: str = PROJECT_ID,
    worktree_id: str = WORKTREE_ID,
):
    candidate = ProjectCandidateEvidence(
        workspace_id=workspace_id,
        workspace_root=str(tmp_root),
        project_id=project_id,
        project_root=str(tmp_root),
        manifest_path="manifest.json",
        name="test",
        kind="project",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id=workspace_id,
        workspace_root=str(tmp_root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id=worktree_id, worktree_root=tmp_root)


def _make_handoff(work_role: str = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind="test-kind",
        objective="af50 w2 objective",
        bounded_scope="af50 w2 bounded scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )


def _make_policy(project_id: str = PROJECT_ID, policy_id: str = "pol-af50-w2"):
    return AgentsPolicyCandidate(
        policy_id=policy_id,
        project_id=project_id,
        scope="",
        content="policy content",
        provenance_ref="agents:AGENTS.md",
    )


def _read_provider(sandbox):
    authority = create_workspace_authority(
        sandbox, _make_handoff(), [_make_policy()], WORKSPACE_READ_DESCRIPTOR
    )
    return BoundedWorkspaceToolProvider(authority)


def _write_provider(sandbox):
    authority = create_workspace_mutation_authority(
        sandbox, _make_handoff(), [_make_policy()], WORKSPACE_WRITE_DESCRIPTOR
    )
    return BoundedWorkspaceMutationProvider(authority)


# ---------------------------------------------------------------------------
# Frozen markers
# ---------------------------------------------------------------------------


def test_frozen_shared_grammar_markers() -> None:
    assert UNDERSCORE_LEADING_SEGMENTS_ALLOWED is True
    assert B006_SHARED_RESOURCE_GRAMMAR_REPAIR is True
    assert LEADING_DOT_SEGMENT_ALLOWED is False
    assert LEADING_DOT_PATH_POLICY == "unchanged_fail_closed"
    assert HIDDEN_DOT_PATH_POLICY_CHANGE is False
    assert WRITE_ONLY_UNDERSCORE_EXCEPTION_ALLOWED is False
    assert PYTHON_SPECIFIC_EXCEPTION_ALLOWED is False
    assert CALCULATOR_SPECIFIC_EXCEPTION_ALLOWED is False


# ---------------------------------------------------------------------------
# V1 positive matrix — underscore-leading project-relative paths
# ---------------------------------------------------------------------------


class TestV1PositiveUnderscorePaths:
    @pytest.mark.parametrize("logical_ref", UNDERSCORE_PATHS)
    def test_resolve_underscore_leading_paths(self, logical_ref: str) -> None:
        tmp = Path(tempfile.mkdtemp())
        target = tmp / logical_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content:{logical_ref}", encoding="utf-8")
        sandbox = _make_sandbox(tmp)

        evidence = resolve_worktree_resource(sandbox, logical_ref)
        assert evidence.exists is True
        assert evidence.kind == "file"
        assert evidence.logical_ref == logical_ref
        assert Path(evidence.canonical_path).is_relative_to(tmp.resolve())
        assert Path(evidence.canonical_path).read_text(encoding="utf-8") == f"content:{logical_ref}"

    def test_underscore_leading_nested_missing_target_resolves_missing(self) -> None:
        # Write-resolution contract: a not-yet-existing underscore-leading
        # target must resolve (kind=missing) so create_only can proceed.
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        evidence = resolve_worktree_resource(sandbox, "src/pkg/__init__.py")
        assert evidence.exists is False
        assert evidence.kind == "missing"
        assert evidence.logical_ref == "src/pkg/__init__.py"

    def test_ordinary_paths_unchanged(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "src" / "pkg").mkdir(parents=True)
        (tmp / "src" / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        evidence = resolve_worktree_resource(sandbox, "src/pkg/core.py")
        assert evidence.exists is True and evidence.kind == "file"


# ---------------------------------------------------------------------------
# V1 negative matrix — fail-closed grammar/security boundaries
# ---------------------------------------------------------------------------


class TestV1NegativeMatrix:
    @pytest.mark.parametrize("bad_ref", NEGATIVE_REFS)
    def test_grammar_negatives_fail_closed(self, bad_ref: str) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, bad_ref)

    @pytest.mark.parametrize("bad_ref", LEADING_DOT_REFS)
    def test_leading_dot_paths_still_rejected(self, bad_ref: str) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / bad_ref).parent.mkdir(parents=True, exist_ok=True)
        sandbox = _make_sandbox(tmp)
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, bad_ref)

    def test_absolute_path_rejected(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, str(tmp / "abs.txt"))

    def test_symlink_file_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (root / "link.txt").symlink_to(outside / "secret.txt")
        sandbox = _make_sandbox(root)
        with pytest.raises(WorktreeResourceSymlinkError):
            resolve_worktree_resource(sandbox, "link.txt")

    def test_directory_symlink_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (root / "linkdir").symlink_to(outside)
        sandbox = _make_sandbox(root)
        with pytest.raises(WorktreeResourceSymlinkError):
            resolve_worktree_resource(sandbox, "linkdir/secret.txt")

    def test_cross_project_and_cross_worktree_isolated(self, tmp_path: Path) -> None:
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "shared.txt").write_text("A", encoding="utf-8")
        (root_b / "shared.txt").write_text("B", encoding="utf-8")
        sandbox_a = _make_sandbox(
            root_a, workspace_id="ws-a", project_id="proj-a", worktree_id="wt-a"
        )
        sandbox_b = _make_sandbox(
            root_b, workspace_id="ws-b", project_id="proj-b", worktree_id="wt-b"
        )
        evidence_a = resolve_worktree_resource(sandbox_a, "shared.txt")
        evidence_b = resolve_worktree_resource(sandbox_b, "shared.txt")
        assert Path(evidence_a.canonical_path).is_relative_to(root_a.resolve())
        assert Path(evidence_b.canonical_path).is_relative_to(root_b.resolve())
        assert Path(evidence_a.canonical_path).read_text(encoding="utf-8") == "A"
        assert Path(evidence_b.canonical_path).read_text(encoding="utf-8") == "B"
        # A symlink across the project boundary is rejected, not followed.
        (root_a / "to_b").symlink_to(root_b)
        with pytest.raises(WorktreeResourceSymlinkError):
            resolve_worktree_resource(sandbox_a, "to_b/shared.txt")

    def test_containment_error_is_fail_closed_subclass(self, tmp_path: Path) -> None:
        # Defense-in-depth relationship: symlink errors remain containment errors.
        assert issubclass(WorktreeResourceSymlinkError, WorktreeResourceContainmentError)


# ---------------------------------------------------------------------------
# V2 — real governed workspace.write / workspace.read on a bounded worktree
# ---------------------------------------------------------------------------


class TestV2GovernedWorkspaceUnderscorePaths:
    def test_write_then_read_init_py_exact_content(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        write = _write_provider(sandbox)
        read = _read_provider(sandbox)

        content = "VALUE = 42\n"
        resp = write.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "src/pkg/__init__.py", "content": content, "mode": "create_only"},
            )
        )
        assert resp.ok is True, resp.error
        assert resp.payload["path"] == "src/pkg/__init__.py"
        assert resp.payload["digest"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert (tmp / "src" / "pkg" / "__init__.py").read_text(encoding="utf-8") == content

        read_resp = read.invoke(
            ToolRequest(
                operation=WORKSPACE_READ_DESCRIPTOR,
                inputs={"path": "src/pkg/__init__.py"},
            )
        )
        assert read_resp.ok is True, read_resp.error
        assert read_resp.payload["content"] == content
        assert read_resp.payload["project_id"] == PROJECT_ID
        assert read_resp.payload["worktree_id"] == WORKTREE_ID

    def test_write_underscore_private_nested(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        write = _write_provider(sandbox)
        resp = write.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "src/_internal/module.py", "content": "x = 1\n", "mode": "create_only"},
            )
        )
        assert resp.ok is True, resp.error
        assert (tmp / "src" / "_internal" / "module.py").is_file()
        read = _read_provider(sandbox)
        read_resp = read.invoke(
            ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "src/_internal/module.py"})
        )
        assert read_resp.ok is True
        assert read_resp.payload["content"] == "x = 1\n"

    def test_ordinary_path_still_works(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        write = _write_provider(sandbox)
        resp = write.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "src/pkg/core.py", "content": "ok\n", "mode": "create_only"},
            )
        )
        assert resp.ok is True, resp.error
        read = _read_provider(sandbox)
        read_resp = read.invoke(
            ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "src/pkg/core.py"})
        )
        assert read_resp.ok is True
        assert read_resp.payload["content"] == "ok\n"

    @pytest.mark.parametrize(
        "bad_path",
        ("../escape.txt", "/abs.txt", ".github/workflows/ci.yml", "a//b.txt", "a/b/"),
    )
    def test_write_negatives_fail_closed(self, bad_path: str) -> None:
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        write = _write_provider(sandbox)
        resp = write.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": bad_path, "content": "x", "mode": "create_only"},
            )
        )
        assert resp.ok is False, f"write {bad_path!r} must fail closed"
        assert "INVALID_PATH" in resp.error["code"] or "PATH_ESCAPE" in resp.error["code"]

    def test_read_leading_dot_fails_closed(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".env").write_text("SECRET=1\n", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        read = _read_provider(sandbox)
        resp = read.invoke(
            ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": ".env"})
        )
        assert resp.ok is False

    def test_write_symlink_escape_fails_closed(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "linkdir").symlink_to(outside)
        sandbox = _make_sandbox(root)
        write = _write_provider(sandbox)
        resp = write.invoke(
            ToolRequest(
                operation=WORKSPACE_WRITE_DESCRIPTOR,
                inputs={"path": "linkdir/escape.txt", "content": "x", "mode": "create_only"},
            )
        )
        assert resp.ok is False
        assert not (outside / "escape.txt").exists()

    def test_search_grammar_parity_underscore_paths(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "src" / "pkg").mkdir(parents=True)
        (tmp / "src" / "pkg" / "__init__.py").write_text("NEEDLE_AF50_W2\n", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        authority = create_workspace_authority(
            sandbox, _make_handoff(), [_make_policy()], WORKSPACE_SEARCH_DESCRIPTOR
        )
        search = BoundedWorkspaceToolProvider(authority)
        resp = search.invoke(
            ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "NEEDLE_AF50_W2"})
        )
        assert resp.ok is True, resp.error
        paths = [r["path"] for r in resp.payload["results"]]
        assert "src/pkg/__init__.py" in paths


# ---------------------------------------------------------------------------
# Shared grammar identity / consumer parity
# ---------------------------------------------------------------------------


def test_read_write_grammar_parity_markers() -> None:
    # The one canonical grammar is shared by both consumers: read/write/search
    # all resolve through resolve_worktree_resource (asserted behaviorally above).
    read_surface = create_role_tool_surface(
        "coder", eager=("workspace.read", "workspace.write", "workspace.search"), progressive=()
    )
    assert read_surface.is_visible("workspace.read")
    assert read_surface.is_visible("workspace.write")
    assert read_surface.is_visible("workspace.search")
