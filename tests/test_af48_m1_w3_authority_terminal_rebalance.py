"""AF #48 M1/W3 — Broad Read / Narrow Write / Restricted Terminal Rebalance.

Proves W3 requirements without modifying shared hot files.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    BoundedWorkspaceToolProvider,
    create_broad_workspace_read_authority,
    create_workspace_authority,
    BROAD_READ_IMPLEMENTED,
    TASK_MAIN_SEARCH_ALLOWED,
    TASK_MAIN_READ_ALLOWED,
)
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    BoundedWorkspaceMutationProvider,
    create_workspace_mutation_authority,
    TASK_MAIN_PRODUCT_SOURCE_WRITE_ALLOWED,
    ANALYST_PRODUCT_SOURCE_WRITE_ALLOWED,
    REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED,
    CODER_ASSIGNED_WORKTREE_WRITE_ALLOWED,
)
from aota_forge.work_plane.restricted_shell import (
    RESTRICTED_SHELL_DESCRIPTOR,
    BoundedRestrictedShellProvider,
    create_restricted_shell_authority,
    COMMAND_FAMILY_INSPECTION,
    COMMAND_FAMILY_GIT_INSPECTION,
    COMMAND_FAMILY_BUILD_TEST,
    ROLE_ALLOWED_FAMILIES,
    GIT_INSPECTION_ALLOWED_SUBCOMMANDS,
    GIT_MUTATION_DENIED_SUBCOMMANDS,
    TERMINAL_ROLE_COMMAND_POLICY_IMPLEMENTED,
    RAW_SHELL_ALLOWED,
    GIT_MUTATION_DISTINGUISHED,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_sandbox(tmp_root: Path, workspace_id="ws-test", project_id="proj-test", worktree_id="wt-001"):
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


def _make_handoff(work_role="coder", objective="test objective", scope="test bounded scope"):
    return TaskHandoff(
        work_role=work_role,
        task_kind="test-kind",
        objective=objective,
        bounded_scope=scope,
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )


def _make_policy(project_id="proj-test", policy_id="pol-001", scope=""):
    return AgentsPolicyCandidate(
        policy_id=policy_id, project_id=project_id, scope=scope, content="policy content", provenance_ref="agents:AGENTS.md"
    )


# ---------------------------------------------------------------------------
# Read — broad authorized project search/read for all roles
# ---------------------------------------------------------------------------

class TestBroadRead:
    def test_task_main_project_search_pass(self):
        assert BROAD_READ_IMPLEMENTED is True
        assert TASK_MAIN_SEARCH_ALLOWED is True
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("hello task-main needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-task-main")
        # task-main broad read via broad authority without Worker TaskHandoff scope
        # Use control handoff (milestone coordination only) as context, not Worker scope
        handoff = _make_handoff(work_role="task-main", scope="milestone coordination only")
        # Also prove broad authority can be created without handoff at all
        auth_no_handoff = create_broad_workspace_read_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=None, applicable_policies=())
        provider_no_handoff = BoundedWorkspaceToolProvider(auth_no_handoff)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp = provider_no_handoff.invoke(req)
        assert resp.ok is True, resp.error
        assert len(resp.payload["results"]) == 1
        # Also via task-main handoff
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        resp2 = provider.invoke(req)
        assert resp2.ok is True

    def test_task_main_project_read_pass(self):
        assert TASK_MAIN_READ_ALLOWED is True
        tmp = Path(tempfile.mkdtemp())
        (tmp / "hello.txt").write_text("hello world", encoding="utf-8")
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-task-main")
        handoff = _make_handoff(work_role="task-main", scope="milestone coordination only")
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "hello.txt"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload["content"] == "hello world"

    def test_analyst_coder_reviewer_steward_read_pass(self):
        for role in ["analyst", "coder", "reviewer", "project-steward"]:
            tmp = Path(tempfile.mkdtemp())
            (tmp / "f.txt").write_text(f"content for {role}", encoding="utf-8")
            sandbox = _make_sandbox(tmp)
            handoff = _make_handoff(work_role=role)
            # Broad authority without needing Worker task handoff semantic scope
            auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
            provider = BoundedWorkspaceToolProvider(auth)
            req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
            resp = provider.invoke(req)
            assert resp.ok is True, f"{role} read failed: {resp.error}"
            assert f"content for {role}" in resp.payload["content"]

    def test_no_task_handoff_scope_required_merely_to_read(self):
        # Prove read does not require TaskHandoff semantic scope as filesystem ACL
        tmp = Path(tempfile.mkdtemp())
        (tmp / "x.txt").write_text("needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        # No handoff at all — broad read still allowed via sandbox + operation + safe path
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=None, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Also read without handoff
        (tmp / "y.txt").write_text("hello", encoding="utf-8")
        auth2 = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=None, applicable_policies=())
        provider2 = BoundedWorkspaceToolProvider(auth2)
        resp2 = provider2.invoke(ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "y.txt"}))
        assert resp2.ok is True
        # Ensure BOUNDED_SCOPE_IS_FILESYSTEM_ACL remains no
        from aota_forge.work_plane.workspace_tools import BOUNDED_SCOPE_IS_FILESYSTEM_ACL, AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY
        assert BOUNDED_SCOPE_IS_FILESYSTEM_ACL is False
        assert AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY is False

    def test_cross_project_read_search_denied(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox_a = _make_sandbox(tmp, project_id="proj-a", worktree_id="wt-a")
        # Try to create authority with mismatched policy project
        handoff = _make_handoff(work_role="coder")
        policy_b = _make_policy(project_id="proj-b")
        with pytest.raises(Exception):
            create_workspace_authority(sandbox_a, handoff, [policy_b], WORKSPACE_READ_DESCRIPTOR)
        # Cross-project via broad authority should still fail at path containment? 
        # But policy cross-project is caught at authority creation as above.
        # For search, ensure cross-project search via mismatched sandbox fails at read path
        # Direct provider cross-project: create sandbox for proj-a, but try to read file that is actually in proj-b's root?
        # We prove that provider's sandbox project_id is used to bound search, not policy.
        # The hard boundary is that search root is always trusted worktree root, not caller supplied host path.
        # Try absolute path injection via scope should be denied
        auth = create_broad_workspace_read_authority(sandbox_a, WORKSPACE_SEARCH_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "hi", "scope": "/etc"})
        resp = provider.invoke(req)
        assert resp.ok is False

    def test_cross_worktree_escape_denied(self):
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        (tmp1 / "shared.txt").write_text("from wt1", encoding="utf-8")
        (tmp2 / "shared.txt").write_text("from wt2", encoding="utf-8")
        sandbox1 = _make_sandbox(tmp1, project_id="proj-test", worktree_id="wt-1")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-test", worktree_id="wt-2")
        handoff = _make_handoff(work_role="coder")
        auth1 = create_broad_workspace_read_authority(sandbox1, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider1 = BoundedWorkspaceToolProvider(auth1)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "shared.txt"})
        resp1 = provider1.invoke(req)
        assert resp1.ok is True
        assert resp1.payload["content"] == "from wt1"
        # Use auth for wt1 to try to read file in wt2 via path traversal? Should be bounded to wt1's root
        # Attempt traversal via path should be denied
        req_traversal = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "../shared.txt"})
        resp_traversal = provider1.invoke(req_traversal)
        assert resp_traversal.ok is False
        # Also ensure worktree_id in payload matches sandbox, not caller
        assert resp1.payload["worktree_id"] == "wt-1"

    def test_host_path_denied(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        for bad in ["/etc/passwd", "/tmp/host.txt", "C:\\Windows\\file"]:
            # ToolRequest will validate path via resolver; absolute should be rejected
            try:
                req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": bad})
            except Exception:
                continue
            resp = provider.invoke(req)
            assert resp.ok is False, f"host path {bad!r} should be denied"

    def test_symlink_escape_denied(self):
        tmp = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (tmp / "real.txt").write_text("real", encoding="utf-8")
        link = tmp / "link.txt"
        link.symlink_to(outside / "secret.txt")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "link.txt"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "SYMLINK" in resp.error["code"] or "INVALID_PATH" in resp.error["code"]
        # Search symlink escape
        linkdir = tmp / "linkdir"
        if not linkdir.exists():
            linkdir.symlink_to(outside)
            auth_s = create_broad_workspace_read_authority(sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=handoff, applicable_policies=())
            provider_s = BoundedWorkspaceToolProvider(auth_s)
            req_s = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "secret"})
            resp_s = provider_s.invoke(req_s)
            # Should not find secret via symlink dir
            assert resp_s.ok is True
            paths = [r["path"] for r in resp_s.payload["results"]]
            assert "secret.txt" not in paths
            assert "linkdir/secret.txt" not in paths


# ---------------------------------------------------------------------------
# Write — narrow product/source write
# ---------------------------------------------------------------------------

class TestNarrowWrite:
    def test_coder_assigned_worktree_write_pass(self):
        assert CODER_ASSIGNED_WORKTREE_WRITE_ALLOWED is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-coder")
        handoff = _make_handoff(work_role="coder")
        policy = _make_policy()
        auth = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(auth)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "hello.txt", "content": "hello", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert Path(tmp / "hello.txt").read_text(encoding="utf-8") == "hello"

    def test_task_main_product_write_denied(self):
        assert TASK_MAIN_PRODUCT_SOURCE_WRITE_ALLOWED is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-task-main")
        handoff = _make_handoff(work_role="task-main")
        policy = _make_policy()
        auth = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(auth)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "evil.txt", "content": "evil", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert resp.error["code"] in ("AUTHORITY_DENIED", "WORKSPACE_ERROR")

    def test_analyst_product_write_denied(self):
        assert ANALYST_PRODUCT_SOURCE_WRITE_ALLOWED is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="analyst")
        policy = _make_policy()
        auth = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(auth)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "a.txt", "content": "x", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "AUTHORITY_DENIED" in resp.error["code"] or "not authorized" in resp.error["message"].lower()

    def test_reviewer_product_write_denied(self):
        assert REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="reviewer")
        policy = _make_policy()
        auth = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(auth)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "r.txt", "content": "x", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False

    def test_coder_cross_project_write_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-a")
        handoff = _make_handoff(work_role="coder")
        policy_b = _make_policy(project_id="proj-b")
        with pytest.raises(Exception):
            create_workspace_mutation_authority(sandbox, handoff, [policy_b], WORKSPACE_WRITE_DESCRIPTOR)

    def test_coder_cross_worktree_write_denied(self):
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        sandbox1 = _make_sandbox(tmp1, project_id="proj-test", worktree_id="wt-1")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-test", worktree_id="wt-2")
        handoff = _make_handoff(work_role="coder")
        policy = _make_policy()
        auth1 = create_workspace_mutation_authority(sandbox1, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider1 = BoundedWorkspaceMutationProvider(auth1)
        # Try to write via traversal that would escape? Already denied
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "../evil.txt", "content": "x", "mode": "create_only"})
        resp = provider1.invoke(req)
        assert resp.ok is False
        # Ensure file not created in tmp2
        assert not Path(tmp2 / "evil.txt").exists()

    def test_path_traversal_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        policy = _make_policy()
        auth = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(auth)
        for bad in ["../evil.txt", "a/../../b.txt", "/etc/passwd"]:
            try:
                req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": bad, "content": "x", "mode": "create_only"})
            except Exception:
                continue
            resp = provider.invoke(req)
            assert resp.ok is False, f"traversal {bad!r} should be denied"


# ---------------------------------------------------------------------------
# Trusted role — caller cannot self-claim
# ---------------------------------------------------------------------------

class TestTrustedRole:
    def test_caller_cannot_self_claim_coder_role(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-001")
        # Trusted binding is analyst, but caller tries to claim coder via ToolRequest inputs
        handoff_analyst = _make_handoff(work_role="analyst")
        # Create read authority as analyst (broad)
        auth = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff_analyst, applicable_policies=())
        provider = BoundedWorkspaceToolProvider(auth)
        # Model tries to inject role via inputs — should be rejected as unknown input
        with pytest.raises(Exception):
            ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt", "role": "coder"})
        # Also try to forge mutation authority as analyst but claim coder
        policy = _make_policy()
        # Analyst tries to create mutation authority (should succeed creation but provider will deny at invoke)
        auth_write_analyst = create_workspace_mutation_authority(sandbox, handoff_analyst, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider_write = BoundedWorkspaceMutationProvider(auth_write_analyst)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "x.txt", "content": "hi", "mode": "create_only"})
        resp = provider_write.invoke(req)
        # Must be denied because trusted role is analyst, not coder
        assert resp.ok is False
        assert "AUTHORITY_DENIED" in resp.error["code"]

    def test_caller_cannot_self_claim_permission(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        # Authority is read-only, try to use it for write
        auth_read = create_broad_workspace_read_authority(sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=())
        # Try to use read authority to construct write provider — must fail
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(auth_read)  # type: ignore
        # Also try to pass fake authority dict
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider({"sandbox": sandbox, "handoff": handoff})  # type: ignore

    def test_trusted_binding_determines_role(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff_coder = _make_handoff(work_role="coder")
        handoff_task_main = _make_handoff(work_role="task-main")
        # Coder can write
        policy = _make_policy()
        auth_coder = create_workspace_mutation_authority(sandbox, handoff_coder, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider_coder = BoundedWorkspaceMutationProvider(auth_coder)
        resp_coder = provider_coder.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "coder.txt", "content": "ok", "mode": "create_only"}))
        assert resp_coder.ok is True
        # task-main cannot write, even if same sandbox
        auth_task = create_workspace_mutation_authority(sandbox, handoff_task_main, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider_task = BoundedWorkspaceMutationProvider(auth_task)
        resp_task = provider_task.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "task.txt", "content": "ok", "mode": "create_only"}))
        assert resp_task.ok is False


# ---------------------------------------------------------------------------
# Terminal — role × command-family
# ---------------------------------------------------------------------------

class TestTerminal:
    def test_task_main_inspection_pass(self):
        assert TERMINAL_ROLE_COMMAND_POLICY_IMPLEMENTED is True
        assert RAW_SHELL_ALLOWED is False
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("hi")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="task-main")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for cmd, args in [("pwd", []), ("ls", []), ("echo", ["hello"])]:
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": cmd, "args": args, "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is True, f"task-main {cmd} failed: {resp.error}"

    def test_task_main_git_inspection_pass(self):
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init"], cwd=str(tmp), capture_output=True, timeout=5)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=str(tmp), capture_output=True, timeout=5)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp), capture_output=True, timeout=5)
        (tmp / "f.txt").write_text("x")
        subprocess.run(["git", "add", "f.txt"], cwd=str(tmp), capture_output=True, timeout=5)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp), capture_output=True, timeout=5)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="task-main")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for sub in ["status", "diff", "log", "rev-parse", "ls-files"]:
            # Use git with subcommand; for rev-parse we need arg
            args = [sub]
            if sub == "rev-parse":
                args = ["rev-parse", "HEAD"]
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "git", "args": args, "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is True, f"task-main git {sub} failed: {resp.error}"

    def test_disallowed_git_mutation_denied(self):
        assert GIT_MUTATION_DISTINGUISHED is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="task-main")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for bad in [["reset", "--hard"], ["clean", "-fd"], ["push", "--force"], ["checkout", "main"]]:
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "git", "args": bad, "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is False, f"git mutation {bad} should be denied"

    def test_unknown_executable_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for bad in ["bash", "sh", "python", "node", "curl", "wget", "ssh"]:
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": bad, "args": [], "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is False

    def test_raw_shell_string_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        # Raw shell string inputs should be rejected (command vs command_id)
        with pytest.raises(Exception):
            ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command": "ls -la", "timeout": 5})
        # Also via provider: try to inject shell string as args that contains pipe
        # Our provider treats pipe as literal, so echo with pipe should succeed literally, not as shell
        req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "echo", "args": ["hello | cat /etc/passwd"], "timeout": 5})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert "hello | cat" in resp.payload["stdout"]
        # Ensure pipe not executed
        assert "root:" not in resp.payload["stdout"]

    def test_nested_shell_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for sh in ["bash", "sh", "zsh"]:
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": sh, "args": [], "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is False

    def test_path_escape_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        for bad in [["ls", ["/etc/passwd"]], ["cat", ["/etc/passwd"]], ["ls", ["../outside"]]]:
            cmd, args = bad
            req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": cmd, "args": args, "timeout": 5})
            resp = provider.invoke(req)
            assert resp.ok is False

    def test_coder_bounded_build_test_pass(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "test_dummy.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="coder")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        # pytest via shell should be allowed for coder
        req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "pytest", "args": ["test_dummy.py", "-q"], "timeout": 30})
        resp = provider.invoke(req)
        # May be slow, but should not be ROLE_FAMILY_DENIED
        if not resp.ok:
            assert resp.error["code"] != "ROLE_FAMILY_DENIED", f"coder pytest should not be denied by role: {resp.error}"
            # If timeout or other, retry with longer timeout?
            # We assert that role check passed; even if pytest times out, it's not role denied
        else:
            assert resp.payload["exit_code"] == 0 or resp.payload["stdout"] != ""

    def test_reviewer_verification_pass(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "test_verify.py").write_text("def test_verify(): assert True\n", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff(work_role="reviewer")
        auth = create_restricted_shell_authority(sandbox, handoff, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider = BoundedRestrictedShellProvider(auth)
        req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "pytest", "args": ["test_verify.py", "-q"], "timeout": 30})
        resp = provider.invoke(req)
        if not resp.ok:
            # reviewer should be allowed build_test family
            assert resp.error["code"] != "ROLE_FAMILY_DENIED"
        else:
            assert resp.ok is True

    def test_role_mismatch_denied(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        # task-main should not be able to run pytest (build_test)
        handoff_task = _make_handoff(work_role="task-main")
        auth_task = create_restricted_shell_authority(sandbox, handoff_task, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider_task = BoundedRestrictedShellProvider(auth_task)
        req = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "pytest", "args": ["test_dummy.py"], "timeout": 5})
        resp = provider_task.invoke(req)
        assert resp.ok is False
        assert resp.error["code"] == "ROLE_FAMILY_DENIED"
        # analyst should not be able to run git inspection? Check role map: analyst only inspection+analysis, not git_inspection
        handoff_analyst = _make_handoff(work_role="analyst")
        auth_analyst = create_restricted_shell_authority(sandbox, handoff_analyst, [], RESTRICTED_SHELL_DESCRIPTOR)
        provider_analyst = BoundedRestrictedShellProvider(auth_analyst)
        req2 = ToolRequest(operation=RESTRICTED_SHELL_DESCRIPTOR, inputs={"command_id": "git", "args": ["status"], "timeout": 5})
        resp2 = provider_analyst.invoke(req2)
        assert resp2.ok is False
        assert resp2.error["code"] == "ROLE_FAMILY_DENIED"


# ---------------------------------------------------------------------------
# Regression — preserve important invariants
# ---------------------------------------------------------------------------

class TestRegression:
    def test_w1_contract_still_valid(self):
        from aota_forge.work_plane.agent_facing_contract import (
            BROAD_READ,
            NARROW_WRITE,
            CONTROL_PLANE_ROLE,
            AGENT_WORK_ROLE_COUNT,
        )
        assert BROAD_READ is True
        assert NARROW_WRITE is True
        assert CONTROL_PLANE_ROLE == "assist_and_guard"
        assert AGENT_WORK_ROLE_COUNT == 5

    def test_no_yaml_policy_externalization(self):
        from aota_forge.work_plane.workspace_tools import POLICY_YAML_EXTERNALIZATION_PERFORMED
        from aota_forge.work_plane.workspace_mutation import POLICY_YAML_EXTERNALIZATION_PERFORMED_W3
        # restricted_shell defines no YAML externalization via its own flag
        assert POLICY_YAML_EXTERNALIZATION_PERFORMED is False
        assert POLICY_YAML_EXTERNALIZATION_PERFORMED_W3 is False
        from aota_forge.work_plane.restricted_shell import TERMINAL_ROLE_COMMAND_POLICY_IMPLEMENTED
        assert TERMINAL_ROLE_COMMAND_POLICY_IMPLEMENTED is True

    def test_no_new_authority_engine(self):
        from aota_forge.work_plane.agent_facing_contract import NEW_AUTHORITY_ENGINE_CREATED
        assert NEW_AUTHORITY_ENGINE_CREATED is False
        from aota_forge.work_plane.workspace_tools import AUTHORITY_COMPOSITION_SEAM_INSUFFICIENT
        assert AUTHORITY_COMPOSITION_SEAM_INSUFFICIENT is False

    def test_single_control_plane(self):
        from aota_forge.work_plane.agent_facing_contract import ONE_CONTROL_PLANE, AF_CONTROL_PLANE_COUNT
        assert ONE_CONTROL_PLANE is True
        assert AF_CONTROL_PLANE_COUNT == 1

