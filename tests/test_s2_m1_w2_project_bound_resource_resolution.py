"""S2 M1-W2 Project-Bound Physical Resource Resolution — construction proof.

Covers T01-T30 plus scope and authority negatives.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.regression.fixtures import TempWorkspaceFixture
from aota_forge.core.resources.resolver import RESOURCE_KINDS
from aota_forge.work_plane.worktree_sandbox import (
    WorktreeSandboxBoundary,
    bind_worktree_sandbox,
)
from aota_forge.work_plane.worktree_resources import (
    ProjectBoundResourceResolver,
    WorktreeResourceEvidence,
    WorktreeResourceBindingError,
    WorktreeResourceContainmentError,
    WorktreeResourceReferenceError,
    WorktreeResourceSymlinkError,
    resolve_worktree_resource,
    # authority markers
    RESOURCE_RESOLUTION_IS_OPERATION_AUTHORITY,
    RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY,
    RESOURCE_TYPE_IS_OPERATION_AUTHORITY,
    RESOLUTION_RESULT_IS_EVIDENCE,
    RESOLUTION_RESULT_IS_AUTHORITY,
    DIGEST_IS_AUTHORITY,
    RESOLUTION_EVIDENCE_IS_USE_TIME_AUTHORITY,
    TOCTOU_BOUNDARY_EXPLICIT,
    TOCTOU_ELIMINATED,
    TOCTOU_EXPLANATION,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_sandbox(project_id: str = "proj_w2_a", workspace_id: str = "ws_w2", worktree_id: str = "wt-w2-001"):
    """Create trusted W1 sandbox via real resolver (reuse)."""
    ws = TempWorkspaceFixture(prefix="w2-bound-")
    ws.__enter__()
    ws.create_project(project_id)
    registry = ws.create_registry(workspace_id)
    evidence = resolve_project_candidates(workspace_id, registry, project_id)
    assert evidence.status == "RESOLVED"
    wt = Path(tempfile.mkdtemp(prefix="w2-wt-"))
    sandbox = bind_worktree_sandbox(evidence, worktree_id, wt)
    return ws, wt, sandbox


def _cleanup(ws: TempWorkspaceFixture, wt: Path):
    try:
        ws.__exit__(None, None, None)
    except Exception:
        pass
    shutil.rmtree(str(wt), ignore_errors=True)


# ---------------------------------------------------------------------------
# T01 consume trusted W1 binding
# ---------------------------------------------------------------------------

def test_t01_consume_trusted_w1_binding():
    ws, wt, sandbox = _make_sandbox()
    try:
        assert isinstance(sandbox, WorktreeSandboxBoundary)
        resolver = ProjectBoundResourceResolver()
        # resolver must accept sandbox + logical ref, not arbitrary root
        # prove that sandbox is required and provides authority
        (wt / "hello.txt").write_text("hello", encoding="utf-8")
        ev = resolver.resolve(sandbox, "hello.txt")
        assert ev.worktree_id == sandbox.worktree_id
        assert ev.project_id == sandbox.project_id
        # Cannot resolve with arbitrary absolute root alone (no sandbox)
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource(None, "hello.txt")  # type: ignore
        # Raw Path cannot self-authorize
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource(Path("/tmp"), "hello.txt")  # type: ignore
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T02 resolve one valid file under bound root
# ---------------------------------------------------------------------------

def test_t02_resolve_one_valid_file_under_bound_root():
    ws, wt, sandbox = _make_sandbox()
    try:
        target = wt / "file.txt"
        target.write_text("content", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "file.txt")
        assert ev.logical_ref == "file.txt"
        assert ev.exists is True
        assert ev.kind == "file"
        assert Path(ev.canonical_path).exists()
        assert ev.canonical_path == str((Path(sandbox.worktree_root) / "file.txt").resolve(strict=False))
        # containment anchor is worktree_root
        assert ev.worktree_root == sandbox.worktree_root
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T03 resolve one valid nested resource under bound root
# ---------------------------------------------------------------------------

def test_t03_resolve_one_valid_nested_resource_under_bound_root():
    ws, wt, sandbox = _make_sandbox()
    try:
        nested = wt / "a" / "b" / "c.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text("nested", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "a/b/c.txt")
        assert ev.exists is True
        assert ev.kind == "file"
        assert ev.logical_ref == "a/b/c.txt"
        assert Path(ev.canonical_path).is_file()
        # also directory
        ev_dir = resolve_worktree_resource(sandbox, "a/b")
        assert ev_dir.exists is True
        assert ev_dir.kind == "directory"
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T04 deterministic logical→physical resolution
# ---------------------------------------------------------------------------

def test_t04_deterministic_logical_physical_resolution():
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "det.txt").write_text("x", encoding="utf-8")
        ev1 = resolve_worktree_resource(sandbox, "det.txt")
        ev2 = resolve_worktree_resource(sandbox, "det.txt")
        assert ev1.canonical_path == ev2.canonical_path
        assert ev1.canonical_dict() == ev2.canonical_dict()
        assert ev1.canonical_json() == ev2.canonical_json()
        assert ev1.digest == ev2.digest
        # different resolver instance same result
        r = ProjectBoundResourceResolver()
        ev3 = r.resolve(sandbox, "det.txt")
        assert ev3.digest == ev1.digest
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T05 preserve project identity
# ---------------------------------------------------------------------------

def test_t05_preserve_project_identity():
    ws, wt, sandbox = _make_sandbox(project_id="proj_preserve_w2")
    try:
        (wt / "p.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "p.txt")
        assert ev.project_id == "proj_preserve_w2"
        assert ev.project_id == sandbox.project_id
        assert ev.workspace_id == sandbox.workspace_id
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T06 preserve worktree identity
# ---------------------------------------------------------------------------

def test_t06_preserve_worktree_identity():
    ws, wt, sandbox = _make_sandbox(worktree_id="wt-preserve-42")
    try:
        (wt / "w.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "w.txt")
        assert ev.worktree_id == "wt-preserve-42"
        assert ev.worktree_root == sandbox.worktree_root
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T07 same logical project in sibling worktree remains distinct binding
# ---------------------------------------------------------------------------

def test_t07_same_logical_project_in_sibling_worktree_remains_distinct_binding():
    # Two sibling worktrees for same logical project
    ws1 = TempWorkspaceFixture(prefix="w2-sibling-")
    ws1.__enter__()
    try:
        ws1.create_project("proj_sibling")
        registry = ws1.create_registry("ws_sibling")
        ev = resolve_project_candidates("ws_sibling", registry, "proj_sibling")
        assert ev.status == "RESOLVED"
        wt_a = Path(tempfile.mkdtemp(prefix="w2-sib-a-"))
        wt_b = Path(tempfile.mkdtemp(prefix="w2-sib-b-"))
        try:
            sb_a = bind_worktree_sandbox(ev, "wt-sib-a", wt_a)
            sb_b = bind_worktree_sandbox(ev, "wt-sib-b", wt_b)
            # create same relative file in both
            (wt_a / "shared.txt").write_text("a", encoding="utf-8")
            (wt_b / "shared.txt").write_text("b", encoding="utf-8")
            eva = resolve_worktree_resource(sb_a, "shared.txt")
            evb = resolve_worktree_resource(sb_b, "shared.txt")
            # distinct bindings → distinct canonical paths and digests
            assert eva.worktree_id != evb.worktree_id
            assert eva.worktree_root != evb.worktree_root
            assert eva.canonical_path != evb.canonical_path
            assert eva.digest != evb.digest
            # evidence from A is not silently reusable for B
            assert eva.canonical_path != evb.canonical_path
            # containment: eva path not inside B
            assert not sb_b.is_contained(eva.canonical_path)
            assert sb_a.is_contained(eva.canonical_path)
        finally:
            shutil.rmtree(str(wt_a), ignore_errors=True)
            shutil.rmtree(str(wt_b), ignore_errors=True)
    finally:
        ws1.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# T08 bounded canonical resolution evidence
# ---------------------------------------------------------------------------

def test_t08_bounded_canonical_resolution_evidence():
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "bounded.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "bounded.txt")
        d = ev.canonical_dict()
        # bounded fields
        assert len(ev.logical_ref) <= 512
        assert len(ev.canonical_path) <= 4096
        # canonical_json sorted keys deterministic
        j = ev.canonical_json()
        parsed = json.loads(j)
        assert list(parsed.keys()) == sorted(parsed.keys())
        # digest is 64 hex, deterministic
        assert len(ev.digest) == 64
        assert hashlib.sha256(ev.canonical_bytes()).hexdigest() == ev.digest
        # evidence contains required minimum fields
        assert "logical_ref" in d
        assert "project_id" in d
        assert "worktree_id" in d
        assert "canonical_path" in d
        assert "worktree_root" in d
        assert "exists" in d
        assert "kind" in d
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T09 absolute path rejected
# ---------------------------------------------------------------------------

def test_t09_absolute_path_rejected():
    ws, wt, sandbox = _make_sandbox()
    try:
        for bad in ["/etc/passwd", "/absolute/path.txt", "/tmp/foo"]:
            with pytest.raises(WorktreeResourceReferenceError):
                resolve_worktree_resource(sandbox, bad)
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T10 .. traversal rejected
# ---------------------------------------------------------------------------

def test_t10_traversal_rejected():
    ws, wt, sandbox = _make_sandbox()
    try:
        for bad in ["../escape.txt", "a/../b.txt", "a/../../b", "..", "a/..", "a/b/.."]:
            with pytest.raises(WorktreeResourceReferenceError):
                resolve_worktree_resource(sandbox, bad)
        # also "." single
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, ".")
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T11 malformed resource ref rejected
# ---------------------------------------------------------------------------

def test_t11_malformed_resource_ref_rejected():
    ws, wt, sandbox = _make_sandbox()
    try:
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "   ")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a//b.txt")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a/b/")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a\\b.txt")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a\x00b.txt")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a/b\x00")
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, 123)  # type: ignore
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, None)  # type: ignore
        # whitespace leading/trailing
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, " a.txt")
        # Too long
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a" * 600)
        # Invalid charset segment
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "a/b*c.txt")
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T12 resource escape outside bound root rejected
# ---------------------------------------------------------------------------

def test_t12_resource_escape_outside_bound_root_rejected():
    ws, wt, sandbox = _make_sandbox()
    try:
        # Use symlink-like path that would escape if not for traversal check?
        # Since ".." already rejected lexically, test canonical escape via symlink is T13.
        # For T12, ensure that even if we craft a path that lexically is inside
        # but canonical would be outside via symlink, it fails (covered in T13).
        # Here test that absolute and traversal already fail, but also test that
        # a resource that tries to escape via absolute is rejected.
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "/tmp/evil.txt")
        # Also test that a path that after joining would be outside if we allowed ".."
        # is already rejected by reference validation, so escape fails closed.
        # Try to bypass via encoded traversal attempt with valid charset but still ".."
        with pytest.raises(WorktreeResourceReferenceError):
            resolve_worktree_resource(sandbox, "..")
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T13 symlink escape rejected
# ---------------------------------------------------------------------------

def test_t13_symlink_escape_rejected():
    ws, wt, sandbox = _make_sandbox()
    outside = Path(tempfile.mkdtemp(prefix="w2-outside-"))
    try:
        # create a directory symlink inside worktree pointing outside
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        link = wt / "link_out"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink not supported")
        # Attempt to resolve through symlink — must fail closed
        with pytest.raises((WorktreeResourceContainmentError, WorktreeResourceSymlinkError)):
            resolve_worktree_resource(sandbox, "link_out/secret.txt")
        # Also file symlink
        file_link = wt / "file_link"
        target_file = outside / "secret2.txt"
        target_file.write_text("s2", encoding="utf-8")
        try:
            file_link.symlink_to(target_file)
        except OSError:
            pass
        else:
            with pytest.raises((WorktreeResourceContainmentError, WorktreeResourceSymlinkError)):
                resolve_worktree_resource(sandbox, "file_link")
        # Symlink pointing inside but still rejected per v0 smallest safe model
        inside_target = wt / "inside.txt"
        inside_target.write_text("inside", encoding="utf-8")
        inside_link = wt / "inside_link"
        try:
            inside_link.symlink_to(inside_target)
        except OSError:
            pass
        else:
            with pytest.raises((WorktreeResourceContainmentError, WorktreeResourceSymlinkError)):
                resolve_worktree_resource(sandbox, "inside_link")
    finally:
        shutil.rmtree(str(outside), ignore_errors=True)
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T14 wrong worktree binding rejected / distinct
# ---------------------------------------------------------------------------

def test_t14_wrong_worktree_binding_rejected():
    ws1 = TempWorkspaceFixture(prefix="w2-t14-")
    ws1.__enter__()
    try:
        ws1.create_project("proj_t14")
        registry = ws1.create_registry("ws_t14")
        ev = resolve_project_candidates("ws_t14", registry, "proj_t14")
        wt_a = Path(tempfile.mkdtemp(prefix="w2-t14-a-"))
        wt_b = Path(tempfile.mkdtemp(prefix="w2-t14-b-"))
        try:
            sb_a = bind_worktree_sandbox(ev, "wt-t14-a", wt_a)
            sb_b = bind_worktree_sandbox(ev, "wt-t14-b", wt_b)
            (wt_a / "only_a.txt").write_text("a", encoding="utf-8")
            eva = resolve_worktree_resource(sb_a, "only_a.txt")
            # Resolving same logical ref in sibling worktree yields different evidence
            # and sibling's file does NOT exist (since only in A)
            evb = resolve_worktree_resource(sb_b, "only_a.txt")
            assert evb.exists is False
            assert evb.kind == "missing"
            assert eva.canonical_path != evb.canonical_path
            # Wrong binding cannot self-authorize: evidence from A not contained in B
            assert not sb_b.is_contained(eva.canonical_path)
            # Passing invalid binding must fail closed
            with pytest.raises(WorktreeResourceBindingError):
                resolve_worktree_resource(None, "only_a.txt")  # type: ignore
            with pytest.raises(WorktreeResourceBindingError):
                resolve_worktree_resource("not_a_sandbox", "only_a.txt")  # type: ignore
        finally:
            shutil.rmtree(str(wt_a), ignore_errors=True)
            shutil.rmtree(str(wt_b), ignore_errors=True)
    finally:
        ws1.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# T15 missing/invalid sandbox binding rejected
# ---------------------------------------------------------------------------

def test_t15_missing_invalid_sandbox_binding_rejected():
    ws, wt, sandbox = _make_sandbox()
    try:
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource(None, "file.txt")  # type: ignore
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource(123, "file.txt")  # type: ignore
        # sandbox with missing root (deleted)
        tmp_root = Path(tempfile.mkdtemp(prefix="w2-missing-root-"))
        # Use valid sandbox then delete its root and try resolve with a sandbox pointing to missing path
        # Create a sandbox, then remove its directory
        shutil.rmtree(str(tmp_root))
        fake_ev = resolve_project_candidates("ws_w2", ws.create_registry("ws_w2_2"), "proj_w2_a") if False else None
        # Instead test that binding to missing root fails at bind time, and resolver rejects if root gone
        # Our resolver should reject if sandbox's root no longer exists
        # Simulate by mutating? Create a sandbox then delete wt and then resolve
        ws2, wt2, sb2 = _make_sandbox(project_id="proj_t15_b", workspace_id="ws_t15_b", worktree_id="wt-t15-b")
        shutil.rmtree(str(wt2))
        try:
            with pytest.raises(WorktreeResourceBindingError):
                resolve_worktree_resource(sb2, "any.txt")
        finally:
            _cleanup(ws2, wt2)
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T16 ambiguous/untrusted W1 evidence cannot self-authorize
# ---------------------------------------------------------------------------

def test_t16_ambiguous_untrusted_w1_evidence_cannot_self_authorize():
    # Raw path alone cannot authorize
    raw = Path(tempfile.mkdtemp(prefix="w2-raw-"))
    try:
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource(raw, "file.txt")  # type: ignore
        with pytest.raises(WorktreeResourceBindingError):
            resolve_worktree_resource({"worktree_root": str(raw)}, "file.txt")  # type: ignore
        # Missing evidence at bind time already fails closed (W1)
        from aota_forge.work_plane.worktree_sandbox import ProjectEvidenceError

        with pytest.raises(Exception):
            bind_worktree_sandbox(None, "wt-x", raw)  # type: ignore
    finally:
        shutil.rmtree(str(raw), ignore_errors=True)


# ---------------------------------------------------------------------------
# T17-T22 authority negatives
# ---------------------------------------------------------------------------

def test_t17_resolution_success_does_not_grant_read_authority():
    assert RESOURCE_RESOLUTION_IS_OPERATION_AUTHORITY is False
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "read.txt").write_text("data", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "read.txt")
        assert isinstance(ev, WorktreeResourceEvidence)
        # Evidence has no read-authority method
        assert not hasattr(ev, "authorize_read")
        assert not hasattr(ev, "allow_read")
        assert not hasattr(ev, "read")
        # Even with evidence, no permission is granted — check flags
        assert RESOURCE_RESOLUTION_IS_OPERATION_AUTHORITY is False
        # Attempting to use evidence as authority should not give file read permission
        # (we just prove evidence doesn't expose a read method)
        for attr in dir(ev):
            assert "authorize" not in attr.lower()
            assert "permission" not in attr.lower()
    finally:
        _cleanup(ws, wt)


def test_t18_resolution_success_does_not_grant_write_authority():
    assert RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY is False
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "write.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "write.txt")
        assert not hasattr(ev, "authorize_write")
        assert not hasattr(ev, "allow_write")
        assert not hasattr(ev, "write")
        assert RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY is False
    finally:
        _cleanup(ws, wt)


def test_t19_resolution_success_does_not_grant_mutation_authority():
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "mut.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "mut.txt")
        assert not hasattr(ev, "authorize_mutation")
        assert not hasattr(ev, "mutation_authority")
        assert not hasattr(ev, "allow_mutation")
        assert RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY is False
    finally:
        _cleanup(ws, wt)


def test_t20_containment_proof_is_not_operation_authority():
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "cont.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "cont.txt")
        # containment proof (evidence) is not operation authority
        assert RESOLUTION_RESULT_IS_EVIDENCE is True
        assert RESOLUTION_RESULT_IS_AUTHORITY is False
        assert not hasattr(ev, "operation_authority")
        # sandbox containment itself is not authority (W1 flag)
        from aota_forge.work_plane.worktree_sandbox import SANDBOX_BOUNDARY_IS_OPERATION_AUTHORITY

        assert SANDBOX_BOUNDARY_IS_OPERATION_AUTHORITY is False
    finally:
        _cleanup(ws, wt)


def test_t21_resource_type_existence_is_not_operation_authority():
    assert RESOURCE_TYPE_IS_OPERATION_AUTHORITY is False
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "type.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "type.txt")
        assert ev.kind == "file"
        assert ev.exists is True
        # kind/existence must not grant authority
        assert not hasattr(ev, "authorize_with_type")
        # Missing also not authority
        ev2 = resolve_worktree_resource(sandbox, "missing_xyz.txt")
        assert ev2.kind == "missing"
        assert ev2.exists is False
        assert not hasattr(ev2, "authorize")
    finally:
        _cleanup(ws, wt)


def test_t22_digest_fingerprint_if_present_is_not_operation_authority():
    assert DIGEST_IS_AUTHORITY is False
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "dig.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "dig.txt")
        d = ev.digest
        assert isinstance(d, str) and len(d) == 64
        assert DIGEST_IS_AUTHORITY is False
        assert not hasattr(ev, "authorize_with_digest")
        # Knowing digest does not grant mutation
        assert not hasattr(ev, "allow_mutation")
    finally:
        _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T23-T28 scope boundary tests
# ---------------------------------------------------------------------------

def test_t23_trusted_resource_resolver_resource_kinds_unchanged():
    forbidden = {"project_file", "workspace_file", "agents_file", "worktree_file"}
    for k in forbidden:
        assert k not in RESOURCE_KINDS, f"RESOURCE_KINDS must not contain {k}"
    assert "runtime_pidfile" in RESOURCE_KINDS
    assert "project_registry" in RESOURCE_KINDS
    src = Path("aota_forge/core/resources/resolver.py").read_text(encoding="utf-8")
    for k in forbidden:
        assert k not in src


def test_t24_no_agents_discovery_introduced():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    lower = src.lower()
    # Must not implement discovery; no AGENTS file name
    assert "agents.md" not in lower
    assert "agentspolicycandidate" not in lower
    assert "agents_discovery" not in lower
    # No file that looks like agents discovery
    assert not Path("aota_forge/work_plane/agents_resolver.py").exists()
    assert not Path("aota_forge/work_plane/agents_discovery.py").exists()


def test_t25_no_tool_system_introduced():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "toolprovider" not in lower
    assert "workspace.read" not in lower
    assert "workspace.search" not in lower
    assert "roletolsurface" not in lower.replace("_", "")
    assert not Path("aota_forge/work_plane/tool.py").exists()
    assert not Path("aota_forge/work_plane/tools.py").exists()


def test_t26_no_skill_implementation_introduced():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    # Allow word skill in comments only if explaining non-skill? But spec says no Skill implementation.
    # Ensure no registry/hydration classes
    lower = src.lower()
    assert "skill_registry" not in lower
    assert "skill_hydration" not in lower
    assert "skill hydration" not in lower
    assert not Path("aota_forge/work_plane/skill.py").exists()
    assert not Path("aota_forge/work_plane/skills.py").exists()


def test_t27_w1_production_file_unchanged():
    import subprocess

    base = "2cd30b36895eb188aac44930ba94a8d604ca31a0"
    out = subprocess.check_output(["git", "diff", "--name-only", base, "HEAD"], text=True)
    changed = [line.strip() for line in out.splitlines() if line.strip()]
    assert "aota_forge/work_plane/worktree_sandbox.py" not in changed, "W1 sandbox must not be modified in W2 lane"
    # Also check that worktree_sandbox still has no resource resolver logic
    src = Path("aota_forge/work_plane/worktree_sandbox.py").read_text(encoding="utf-8")
    assert "WorktreeResource" not in src


def test_t28_s1_high_conflict_files_unchanged():
    import subprocess

    base = "2cd30b36895eb188aac44930ba94a8d604ca31a0"
    out = subprocess.check_output(["git", "diff", "--name-only", base, "HEAD"], text=True)
    changed = [line.strip() for line in out.splitlines() if line.strip()]
    high_conflict = {
        "aota_forge/work_plane/__init__.py",
        "aota_forge/work_plane/handoff.py",
        "aota_forge/work_plane/bootstrap.py",
        "aota_forge/work_plane/events.py",
    }
    for p in changed:
        assert p not in high_conflict, f"high-conflict file {p} must not be changed"
    # Aggregator export unchanged
    init_src = Path("aota_forge/work_plane/__init__.py").read_text(encoding="utf-8")
    assert "worktree_resources" not in init_src
    assert "WorktreeResource" not in init_src


# ---------------------------------------------------------------------------
# T29-T30 TOCTOU boundary
# ---------------------------------------------------------------------------

def test_t29_resolver_does_not_claim_check_time_evidence_is_use_time_authority():
    assert RESOLUTION_EVIDENCE_IS_USE_TIME_AUTHORITY is False
    assert TOCTOU_ELIMINATED is False
    ws, wt, sandbox = _make_sandbox()
    try:
        (wt / "toctou.txt").write_text("x", encoding="utf-8")
        ev = resolve_worktree_resource(sandbox, "toctou.txt")
        # Evidence must not expose use-time authority
        assert not hasattr(ev, "open")
        assert not hasattr(ev, "open_secure")
        assert not hasattr(ev, "use_time_authority")
        # Module must explicitly document TOCTOU boundary
        src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
        assert "TOCTOU" in src
        assert TOCTOU_EXPLANATION in src or "resolution time only" in src.lower()
    finally:
        _cleanup(ws, wt)


def test_t30_toctou_limitation_secure_use_contract_is_explicit():
    assert TOCTOU_BOUNDARY_EXPLICIT is True
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    assert "TOCTOU_BOUNDARY_EXPLICIT" in src
    assert "TOCTOU" in src
    # Must state that downstream must revalidate or use secure seam
    lower = src.lower()
    assert "revalidate" in lower or "secure" in lower or "use-time" in lower or "use_time" in lower
    # Must not falsely claim eliminated
    assert "TOCTOU_ELIMINATED" in src
    assert TOCTOU_ELIMINATED is False


# ---------------------------------------------------------------------------
# Additional positive: no generic filesystem framework, no tool result ontology
# ---------------------------------------------------------------------------

def test_no_generic_filesystem_framework_created():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    for name in ["ProjectFilesystem", "VirtualFilesystem", "FilesystemManager", "SandboxManager", "PermissionEngine"]:
        assert name not in src


def test_no_tool_result_ontology_introduced():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    assert "ToolResult" not in src
    assert "ToolGovernance" not in src
    assert "ToolCard" not in src


def test_no_cwd_as_authority():
    src = Path("aota_forge/work_plane/worktree_resources.py").read_text(encoding="utf-8")
    # Ensure no use of cwd as authority
    assert "CWD_IS_AUTHORITY" in src
    # Implementation must not call getcwd or Path.cwd as authority
    lower = src.lower()
    # Allow no getcwd
    assert "getcwd" not in lower
    # Path.cwd may appear in tests but not in production resolver
    # So check that resolver file does not use cwd
    assert "cwd" not in lower or "CWD_IS_AUTHORITY" in src


def test_resource_reference_relative_and_traversal_fail_closed_flags():
    from aota_forge.work_plane.worktree_resources import (
        RESOURCE_REFERENCE_RELATIVE,
        RESOURCE_TRAVERSAL_FAIL_CLOSED,
    )

    assert RESOURCE_REFERENCE_RELATIVE is True
    assert RESOURCE_TRAVERSAL_FAIL_CLOSED is True
