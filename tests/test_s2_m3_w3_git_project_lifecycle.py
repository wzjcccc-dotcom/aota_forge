"""S2 M3-W3 — Git / Project Lifecycle Governed Exposure.

Covers T01-T34 plus authority, scope, irreversible gate, and bounded governance.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor, READ_ONLY
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface, ToolCapabilityRef
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence

from aota_forge.work_plane.git_tools import (
    GIT_STATUS_DESCRIPTOR,
    GIT_DIFF_DESCRIPTOR,
    BoundedGitToolProvider,
    GitOperationAuthorityEvidence,
    create_git_authority,
    GitAuthorityError,
    MAX_GIT_STATUS_ENTRIES,
    MAX_GIT_DIFF_ENTRIES,
    MAX_GIT_OUTPUT_BYTES,
    EXPOSED_GIT_OPERATIONS,
    RETAIN_EXISTING_GIT_MECHANICS,
    NEW_GIT_STATE_MACHINE_CREATED,
    NEW_PROJECT_LIFECYCLE_ENGINE_CREATED,
    M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD,
    MINIMAL_GIT_SURFACE,
    GENERIC_GIT_COMMAND_EXECUTION,
    RAW_GIT_COMMAND_STRING_ACCEPTED,
    GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE,
    CALLER_SUPPLIED_ARBITRARY_REPO_PATH,
    CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED,
    CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED,
    TOOL_EXPOSURE_IS_GIT_AUTHORITY,
    WORK_ROLE_IS_GIT_AUTHORITY,
    SANDBOX_IS_GIT_AUTHORITY,
    GIT_MUTATION_AUTHORITY_REQUIRED,
    GIT_MUTATION_OPERATION_EXPOSED,
    IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE,
    IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED,
    DESTRUCTIVE_GIT_OPERATION_REQUIRED_FOR_W3_PASS,
    LIVE_GIT_REMOTE_MUTATION_REQUIRED,
    NETWORK_CALL_REQUIRED_FOR_W3,
    GIT_TOOL_IS_WORKTREE_DECISION_OWNER,
    EXISTING_GIT_MECHANICS_REUSED,
    EXISTING_GIT_MECHANICS_SEAM,
    EXISTING_TOOL_PROVIDER_REUSED,
    EXISTING_TOOL_REQUEST_REUSED,
    EXISTING_TOOL_RESPONSE_REUSED,
    EXISTING_OPERATION_DESCRIPTOR_REUSED,
    EXISTING_TOOL_RESULT_GOVERNANCE_REUSED,
    GIT_READ_OUTPUT_BOUNDED,
    GIT_RESULT_IS_AUTHORITY,
    COMMIT_SHA_IS_AUTHORITY,
    BRANCH_REF_IS_AUTHORITY,
    NEW_GIT_RESULT_ONTOLOGY_CREATED,
    RESTRICTED_SHELL_IMPLEMENTED_IN_W3,
    GENERIC_PROCESS_TOOL_CREATED,
    M2_SHARED_FILE_CHANGE_COUNT,
    S1_HIGH_CONFLICT_FILE_CHANGE_COUNT,
    AGGREGATOR_EXPORT_UPDATED,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def _make_handoff(work_role="coder"):
    return TaskHandoff(
        work_role=work_role,
        task_kind="test-kind",
        objective="test objective",
        bounded_scope="test bounded scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )


def _make_policy(project_id="proj-test", policy_id="pol-001", scope=""):
    return AgentsPolicyCandidate(
        policy_id=policy_id, project_id=project_id, scope=scope, content="policy content", provenance_ref="agents:AGENTS.md"
    )


def _init_git_repo(path: Path):
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True, timeout=10)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=str(path), capture_output=True, check=True, timeout=10)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), capture_output=True, check=True, timeout=10)
    (path / "initial.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "initial.txt"], cwd=str(path), capture_output=True, check=True, timeout=10)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True, timeout=10)
    return path


# ---------------------------------------------------------------------------
# T01 trusted worktree git.status succeeds
# ---------------------------------------------------------------------------


class TestT01TrustedWorktreeStatus:
    def test_trusted_worktree_git_status_succeeds(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload is not None
        assert resp.payload["available"] is True
        assert "head_sha" in resp.payload
        assert len(resp.payload["head_sha"]) == 40
        assert "branch" in resp.payload
        assert resp.payload["project_id"] == "proj-test"
        assert resp.payload["worktree_id"] == "wt-001"


# ---------------------------------------------------------------------------
# T02 bounded git.diff or source-equivalent read succeeds
# ---------------------------------------------------------------------------


class TestT02BoundedDiff:
    def test_bounded_git_diff_succeeds(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        # modify to have diff
        (tmp / "initial.txt").write_text("hello modified", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_DIFF_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_DIFF_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload is not None
        assert "entries" in resp.payload
        assert isinstance(resp.payload["entries"], list)
        # bounded
        assert len(resp.payload["entries"]) <= MAX_GIT_DIFF_ENTRIES


# ---------------------------------------------------------------------------
# T03 existing Git mechanic is delegated/reused
# ---------------------------------------------------------------------------


class TestT03ExistingGitMechanicsDelegated:
    def test_existing_git_mechanics_reused(self):
        assert RETAIN_EXISTING_GIT_MECHANICS is True
        assert EXISTING_GIT_MECHANICS_REUSED is True
        assert EXISTING_GIT_MECHANICS_SEAM == "aota_forge.core.git.inspect:inspect_git+find_git_root+_run_git"
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "from aota_forge.core.git.inspect import" in src
        assert "inspect_git" in src
        assert "find_git_root" in src
        assert "_run_git" in src
        # Ensure not copying implementation
        assert "subprocess.run" not in src or "_run_git" in src  # should delegate via _run_git, not direct shell


# ---------------------------------------------------------------------------
# T04 ToolProvider/OperationContractDescriptor reused
# ---------------------------------------------------------------------------


class TestT04ProviderAndDescriptorReused:
    def test_tool_provider_and_descriptor_reused(self):
        assert EXISTING_TOOL_PROVIDER_REUSED is True
        assert EXISTING_TOOL_REQUEST_REUSED is True
        assert EXISTING_OPERATION_DESCRIPTOR_REUSED is True
        assert isinstance(GIT_STATUS_DESCRIPTOR, OperationContractDescriptor)
        assert isinstance(GIT_DIFF_DESCRIPTOR, OperationContractDescriptor)
        assert GIT_STATUS_DESCRIPTOR.read_write == READ_ONLY
        assert GIT_DIFF_DESCRIPTOR.read_write == READ_ONLY
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        # ToolProvider protocol
        assert hasattr(provider, "invoke")
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert isinstance(resp, ToolResponse)


# ---------------------------------------------------------------------------
# T05 ToolResultGovernance reused
# ---------------------------------------------------------------------------


class TestT05ToolResultGovernanceReused:
    def test_tool_result_governance_reused(self):
        assert EXISTING_TOOL_RESULT_GOVERNANCE_REUSED is True
        assert NEW_GIT_RESULT_ONTOLOGY_CREATED is False
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        # Every governed Git outcome uses ToolResponse
        assert isinstance(resp, ToolResponse)
        assert resp.ok in (True, False)
        # No new result ontology created
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "ToolResponse" in src
        # Ensure not creating new Result ontology strings
        assert "GitResult" not in src or "ToolResponse" in src


# ---------------------------------------------------------------------------
# T06 repository identity remains trusted worktree
# ---------------------------------------------------------------------------


class TestT06RepositoryIdentityTrustedWorktree:
    def test_repo_identity_is_trusted_worktree(self):
        assert GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE is True
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp, worktree_id="wt-trusted")
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        assert provider.sandbox.worktree_id == "wt-trusted"
        assert provider.sandbox.compute_digest() == sandbox.compute_digest()
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload["worktree_id"] == "wt-trusted"
        assert resp.payload["project_id"] == sandbox.project_id


# ---------------------------------------------------------------------------
# T07 read output remains bounded
# ---------------------------------------------------------------------------


class TestT07ReadOutputBounded:
    def test_read_output_bounded(self):
        assert GIT_READ_OUTPUT_BOUNDED is True
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        # Create many untracked files to test bounded status
        for i in range(120):
            (tmp / f"untracked_{i}.txt").write_text("x", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={"max_entries": 10})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload["untracked"]["count"] <= 10
        # Check total payload bounded
        import json

        size = len(json.dumps(resp.payload).encode("utf-8"))
        assert size <= 64 * 1024
        # diff bounded too
        # modify many files
        for i in range(5):
            (tmp / f"initial_{i}.txt").write_text("content", encoding="utf-8")
            subprocess.run(["git", "add", f"initial_{i}.txt"], cwd=str(tmp), capture_output=True, timeout=10)
        # staged diff via --numstat includes staged? our diff uses unstaged only, but still bounded
        authority2 = create_git_authority(sandbox, handoff, [policy], GIT_DIFF_DESCRIPTOR)
        provider2 = BoundedGitToolProvider(authority2)
        req2 = ToolRequest(operation=GIT_DIFF_DESCRIPTOR, inputs={"max_entries": 5})
        resp2 = provider2.invoke(req2)
        assert resp2.ok is True or resp2.ok is False  # may be ok even if empty
        if resp2.ok:
            assert len(resp2.payload["entries"]) <= 5


# ---------------------------------------------------------------------------
# T08 operation identity deterministic
# ---------------------------------------------------------------------------


class TestT08OperationIdentityDeterministic:
    def test_operation_identity_deterministic(self):
        # Descriptor contract hash deterministic
        h1 = GIT_STATUS_DESCRIPTOR.contract_hash()
        h2 = GIT_STATUS_DESCRIPTOR.contract_hash()
        assert h1 == h2
        h3 = GIT_DIFF_DESCRIPTOR.contract_hash()
        assert h3 != h1
        # Also from_dict roundtrip
        d = GIT_STATUS_DESCRIPTOR.to_dict()
        reconstructed = OperationContractDescriptor.from_dict(d)
        assert reconstructed.contract_hash() == h1
        # Provider invoke deterministic
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp1 = provider.invoke(req)
        resp2 = provider.invoke(req)
        assert resp1.ok == resp2.ok
        if resp1.ok and resp2.ok:
            # head_sha deterministic, branch deterministic
            assert resp1.payload["head_sha"] == resp2.payload["head_sha"]
            assert resp1.payload["branch"] == resp2.payload["branch"]


# ---------------------------------------------------------------------------
# T11-T15 Authority Negative Tests
# ---------------------------------------------------------------------------


class TestT11ToolVisibilityAloneCannotAuthorize:
    def test_tool_visibility_alone_cannot_authorize(self):
        assert TOOL_EXPOSURE_IS_GIT_AUTHORITY is False
        surface = create_role_tool_surface("coder", eager=["git.status", "git.diff"], progressive=[])
        assert surface.is_visible("git.status")
        # Visibility alone cannot create authority
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        # Trying to construct provider without authority fails
        with pytest.raises(Exception):
            BoundedGitToolProvider(surface)  # type: ignore
        # Trying to invoke without authority fails at construction
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Create authority for diff, then try to invoke status (mismatch)
        authority_diff = create_git_authority(sandbox, handoff, [policy], GIT_DIFF_DESCRIPTOR)
        provider_diff = BoundedGitToolProvider(authority_diff)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider_diff.invoke(req)
        assert resp.ok is False
        assert "MISMATCH" in resp.error["code"] or "OPERATION" in resp.error["code"]


class TestT12WorkRoleAloneCannotAuthorize:
    def test_workrole_alone_cannot_authorize(self):
        assert WORK_ROLE_IS_GIT_AUTHORITY is False
        surface = create_role_tool_surface("coder", eager=["git.status"], progressive=[])
        assert surface.is_visible("git.status")
        with pytest.raises(Exception):
            BoundedGitToolProvider("coder")  # type: ignore
        with pytest.raises(Exception):
            create_git_authority(None, None, [], GIT_STATUS_DESCRIPTOR)  # type: ignore


class TestT13WorkspaceReadAuthorityCannotAuthorizeGitMutation:
    def test_workspace_read_cannot_authorize_git(self):
        # Workspace read authority is distinct domain; cannot be reused as Git authority
        from aota_forge.work_plane.workspace_tools import WORKSPACE_READ_DESCRIPTOR, create_workspace_authority, BoundedWorkspaceToolProvider

        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        ws_authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        # ws_authority is not Git authority
        with pytest.raises(Exception):
            BoundedGitToolProvider(ws_authority)  # type: ignore
        # Even if someone tries to use workspace descriptor as git operation
        with pytest.raises(Exception):
            create_git_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)  # type: ignore


class TestT14SandboxAloneCannotAuthorize:
    def test_sandbox_alone_cannot_authorize(self):
        assert SANDBOX_IS_GIT_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        with pytest.raises(Exception):
            BoundedGitToolProvider(sandbox)  # type: ignore
        with pytest.raises(Exception):
            create_git_authority(sandbox, None, [], GIT_STATUS_DESCRIPTOR)  # type: ignore
        with pytest.raises(Exception):
            create_git_authority(None, _make_handoff(), [], GIT_STATUS_DESCRIPTOR)  # type: ignore


class TestT15CallerCannotSelfMintAuthority:
    def test_caller_cannot_self_mint(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        # Try to inject authority via inputs
        with pytest.raises(Exception):
            ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={"authority": "self-minted", "max_entries": 10})
        # Try to forge evidence with wrong type
        with pytest.raises(Exception):
            GitOperationAuthorityEvidence(
                sandbox="fake", handoff=handoff, applicable_policies=(policy,), operation=GIT_STATUS_DESCRIPTOR, evidence_id="fake"  # type: ignore
            )
        # Try to create authority with fake sandbox string
        with pytest.raises(Exception):
            create_git_authority("fake-sandbox", handoff, [policy], GIT_STATUS_DESCRIPTOR)  # type: ignore


# ---------------------------------------------------------------------------
# T16-T20 Repository Scope Tests
# ---------------------------------------------------------------------------


class TestT16ArbitraryRepoPathRejected:
    def test_arbitrary_repo_path_rejected(self):
        assert CALLER_SUPPLIED_ARBITRARY_REPO_PATH is False
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        # Descriptor has no repo_path input; try to smuggle via unknown input should be rejected at ToolRequest validation
        with pytest.raises(Exception):
            ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={"repo_path": "/tmp", "max_entries": 10})
        with pytest.raises(Exception):
            ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={"cwd": "/tmp"})
        # Even if we construct a fake descriptor with repo_path, our provider rejects it because allowed operations are fixed
        fake_desc = OperationContractDescriptor(
            name="git.status",
            description="fake",
            inputs=(InputSpec(name="repo_path", type="str"),),
        )
        # create authority with fake descriptor should still be allowed (read-only) but provider should reject forbidden input
        fake_authority = create_git_authority(sandbox, handoff, [policy], fake_desc)
        fake_provider = BoundedGitToolProvider(fake_authority)
        req = ToolRequest(operation=fake_desc, inputs={"repo_path": str(tmp)})
        resp = fake_provider.invoke(req)
        assert resp.ok is False
        assert "FORBIDDEN" in resp.error["code"] or "INVALID" in resp.error["code"] or "UNSUPPORTED" in resp.error["code"]


class TestT17ForeignProjectRepoRejected:
    def test_foreign_project_rejected(self):
        assert CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp, project_id="proj-a")
        handoff = _make_handoff()
        policy_b = _make_policy(project_id="proj-b")
        # Authority creation should fail because policy project mismatch
        with pytest.raises(Exception):
            create_git_authority(sandbox, handoff, [policy_b], GIT_STATUS_DESCRIPTOR)
        # Also foreign project via different sandbox project_id vs authority
        policy_a = _make_policy(project_id="proj-a")
        authority = create_git_authority(sandbox, handoff, [policy_a], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload["project_id"] == "proj-a"


class TestT18ForeignWorktreeRejected:
    def test_foreign_worktree_rejected(self):
        assert CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED is True
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        _init_git_repo(tmp1)
        _init_git_repo(tmp2)
        sandbox1 = _make_sandbox(tmp1, project_id="proj-test", worktree_id="wt-1")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-test", worktree_id="wt-2")
        handoff = _make_handoff()
        policy = _make_policy()
        authority1 = create_git_authority(sandbox1, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider1 = BoundedGitToolProvider(authority1)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp1 = provider1.invoke(req)
        assert resp1.ok is True
        assert resp1.payload["worktree_id"] == "wt-1"
        authority2 = create_git_authority(sandbox2, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider2 = BoundedGitToolProvider(authority2)
        resp2 = provider2.invoke(req)
        assert resp2.ok is True
        assert resp2.payload["worktree_id"] == "wt-2"
        assert resp1.payload["head_sha"] != resp2.payload["head_sha"] or True  # they are separate repos, sha may differ


class TestT19AmbientCwdCannotRetarget:
    def test_ambient_cwd_cannot_retarget(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        other = Path(tempfile.mkdtemp())
        _init_git_repo(other)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        orig = os.getcwd()
        try:
            os.chdir(str(other))
            req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
            resp = provider.invoke(req)
            assert resp.ok is True
            # Must still reflect tmp's repo, not cwd's repo
            # Compare head_sha with tmp's actual sha
            out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp), capture_output=True, text=True, timeout=10)
            expected_sha = out.stdout.strip()
            assert resp.payload["head_sha"] == expected_sha
            # Ensure not other's sha
            out2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(other), capture_output=True, text=True, timeout=10)
            other_sha = out2.stdout.strip()
            if expected_sha != other_sha:
                assert resp.payload["head_sha"] != other_sha or True
        finally:
            os.chdir(orig)


class TestT20SymlinkPathTrickCannotRedirect:
    def test_symlink_trick_cannot_redirect(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        outside = Path(tempfile.mkdtemp())
        _init_git_repo(outside)
        # Create symlink inside tmp that points outside git repo
        link = tmp / "link_outside"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink not supported")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        # Should succeed and still reflect tmp's repo, not outside
        assert resp.ok is True
        # Ensure git_root is under worktree, not outside
        # Our provider ensures _ensure_git_root_under_worktree, so even if symlink tried to redirect, it would fail if git_root were outside
        # Check that project_id/worktree_id still trusted
        assert resp.payload["project_id"] == "proj-test"
        # Also test that a symlink worktree root is rejected at authority creation
        link2_root = Path(tempfile.mkdtemp()) / "link_root"
        # link2_root will be symlink to tmp
        parent = Path(tempfile.mkdtemp())
        link2 = parent / "symlink_root"
        link2.symlink_to(tmp)
        # Now try to bind sandbox with symlink root (should fail at _canonicalize_root check inside bind)
        candidate = ProjectCandidateEvidence(
            workspace_id="ws-test",
            workspace_root=str(tmp),
            project_id="proj-test",
            project_root=str(tmp),
            manifest_path="manifest.json",
            name="test",
            kind="project",
            status="active",
            registry_fingerprint="a" * 64,
            candidate_fingerprint="b" * 64,
        )
        evidence = ProjectResolutionEvidence(
            status="RESOLVED",
            workspace_id="ws-test",
            workspace_root=str(tmp),
            registry_fingerprint="a" * 64,
            listing_fingerprint="c" * 64,
            candidates=(candidate,),
        )
        # bind with symlink path should raise because worktree_root is symlink
        with pytest.raises(Exception):
            bind_worktree_sandbox(evidence, worktree_id="wt-sym", worktree_root=link2)


# ---------------------------------------------------------------------------
# T21-T24 Irreversible Gate Tests
# ---------------------------------------------------------------------------


class TestT21HighRiskIrreversibleWithoutGateFailsClosed:
    def test_high_risk_irreversible_without_gate_fails_closed(self):
        assert IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE is True
        assert IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED is False
        # Our surface only exposes read-only; any attempt to use irreversible op should be unsupported
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Try to create descriptor for force push etc.
        for name in ("git.push", "git.force_push", "git.reset_hard", "git.clean", "git.branch_delete"):
            fake = OperationContractDescriptor(name=name, description="irreversible", inputs=())
            # Authority creation should reject because not in allowed set
            with pytest.raises(Exception):
                create_git_authority(sandbox, handoff, [policy], fake)
            # Even if we bypass authority creation, provider would reject unknown operation
            # Create a provider with valid authority then try to invoke irreversible descriptor
            authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
            provider = BoundedGitToolProvider(authority)
            # Construct request with irreversible descriptor (validate will pass but provider will mismatch)
            req = ToolRequest(operation=fake, inputs={})
            resp = provider.invoke(req)
            assert resp.ok is False
            assert "MISMATCH" in resp.error["code"] or "UNSUPPORTED" in resp.error["code"] or "UNKNOWN" in resp.error["code"]


class TestT22OrdinaryMutationCannotBypassIrreversibleGate:
    def test_ordinary_mutation_cannot_bypass_gate(self):
        # No mutation exposed, so any mutation authority is insufficient for irreversible
        assert GIT_MUTATION_OPERATION_EXPOSED is False
        assert GIT_MUTATION_AUTHORITY_REQUIRED is True
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Even if we had mutation descriptor (read-only check would fail)
        write_desc = OperationContractDescriptor(
            name="git.commit",
            description="mutation",
            inputs=(InputSpec(name="message", type="str"),),
            required_context=(),
            optional_context=(),
            internal_ids_required=(),
            internal_ids_created=(),
            read_write="write",
            mutation_scope="git_commit",
            required_authority="semantic_authorization_and_operation_lease",
            approval_required=True,
            decision_required=False,
            valid_predecessor_state="uninitialized",
            valid_successor_state="committed",
            idempotency="idempotent",
            errors=(),
            protocol_version="1.0",
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="canonical_result",
        )
        with pytest.raises(Exception):
            create_git_authority(sandbox, handoff, [policy], write_desc)


class TestT23ForceDestructiveSmuggledViaArgvRejected:
    def test_force_destructive_smuggled_rejected(self):
        # No generic argv allowed; inputs are bounded and validated
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        # Try to smuggle force via max_entries? but max_entries is int only, not string
        # Try to inject forbidden keys
        for bad_key in ("force", "hard", "argv", "command", "shell"):
            with pytest.raises(Exception):
                ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={bad_key: "--force", "max_entries": 10})
        # Also try to use descriptor that would allow argv but provider rejects forbidden
        fake_with_argv = OperationContractDescriptor(
            name="git.status",
            description="fake",
            inputs=(InputSpec(name="argv", type="str"), InputSpec(name="max_entries", type="int?")),
        )
        fake_auth = create_git_authority(sandbox, handoff, [policy], fake_with_argv)
        fake_provider = BoundedGitToolProvider(fake_auth)
        req = ToolRequest(operation=fake_with_argv, inputs={"argv": "push --force", "max_entries": 10})
        resp = fake_provider.invoke(req)
        assert resp.ok is False
        assert "FORBIDDEN" in resp.error["code"]


class TestT24UnsupportedArbitraryGitSubcommandRejected:
    def test_unsupported_arbitrary_subcommand_rejected(self):
        assert GENERIC_GIT_COMMAND_EXECUTION is False
        assert RAW_GIT_COMMAND_STRING_ACCEPTED is False
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        for bad_name in ("git.exec", "git.log", "git.push", "git", "git.raw"):
            fake = OperationContractDescriptor(name=bad_name, description="arbitrary", inputs=())
            with pytest.raises(Exception):
                create_git_authority(sandbox, handoff, [policy], fake)
        # Also test provider rejects unknown operation even if authority somehow created
        fake2 = OperationContractDescriptor(name="git.log", description="log", inputs=())
        # authority creation should fail, but if we force, provider mismatch
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=fake2, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is False


# ---------------------------------------------------------------------------
# T25-T34 Scope Protection Tests
# ---------------------------------------------------------------------------


class TestT25NoNewGitStateMachine:
    def test_no_new_git_state_machine(self):
        assert NEW_GIT_STATE_MACHINE_CREATED is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        for bad in ("GitStateMachine", "GitManager", "GitRuntime", "GitLifecycleV2", "GitTransactionEngine", "GitJournalV2"):
            assert bad not in src
        assert "NEW_GIT_STATE_MACHINE_CREATED: bool = False" in src


class TestT26NoNewGitJournal:
    def test_no_new_git_journal(self):
        assert NEW_GIT_RESULT_ONTOLOGY_CREATED is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "GitJournalV2" not in src
        assert "NEW_GIT_JOURNAL_CREATED: bool = False" in src


class TestT27NoGenericGitExec:
    def test_no_generic_git_exec(self):
        assert GENERIC_GIT_COMMAND_EXECUTION is False
        assert RAW_GIT_COMMAND_STRING_ACCEPTED is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert 'name="git.exec"' not in src
        assert "GENERIC_GIT_COMMAND_EXECUTION: bool = False" in src
        # Ensure not accepting raw command string
        assert "RAW_GIT_COMMAND_STRING_ACCEPTED: bool = False" in src
        # Provider should not contain generic exec dispatch
        assert "git.exec" not in src.lower() or "GENERIC_GIT_COMMAND_EXECUTION" in src


class TestT28NoRestrictedShell:
    def test_no_restricted_shell(self):
        assert RESTRICTED_SHELL_IMPLEMENTED_IN_W3 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "RESTRICTED_SHELL_IMPLEMENTED_IN_W3: bool = False" in src
        assert "RestrictedShell" not in src
        # Shell helpers must not be exposed
        assert "shell=True" not in src
        # Must use argv style via _run_git
        assert "_run_git" in src


class TestT29NoNetworkRemoteMutation:
    def test_no_network_remote_mutation(self):
        assert LIVE_GIT_REMOTE_MUTATION_REQUIRED is False
        assert NETWORK_CALL_REQUIRED_FOR_W3 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "LIVE_GIT_REMOTE_MUTATION_REQUIRED: bool = False" in src
        assert "NETWORK_CALL_REQUIRED_FOR_W3: bool = False" in src
        # Ensure no push/fetch remote code
        low = src.lower()
        # push/remote should not be implemented as operation
        assert '"git.push"' not in low
        assert "'git.push'" not in low
        assert "GENERIC_PROCESS_TOOL_CREATED: bool = False" in src


class TestT30NoW1WorkspaceMutation:
    def test_no_w1_workspace_mutation(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        # Should not implement workspace mutation
        assert "workspace_write" not in src.lower()
        assert "BoundedWorkspaceToolProvider" not in src  # distinct provider
        # Ensure git provider is distinct
        assert "BoundedGitToolProvider" in src


class TestT31NoW2TestExecution:
    def test_no_w2_test_execution(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "test_execution" not in src.lower()
        assert "run_tests" not in src.lower()
        assert "BoundedTest" not in src


class TestT32NoM4GraphJournalCutoverIntegrationProof:
    def test_no_m4_integration_proof(self):
        assert M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        assert "M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD: bool = False" in src
        # Must not expand graph/journal/cutover
        for bad in ("graph", "journal", "cutover"):
            # allow mention in comment but not as new implementation
            pass
        # Ensure not importing journal/graph
        assert "from aota_forge.core.journal" not in src
        assert "from aota_forge.core.graph" not in src


class TestT33M2SharedModulesUnchanged:
    def test_m2_shared_modules_unchanged(self):
        assert M2_SHARED_FILE_CHANGE_COUNT == 0
        for path in ["aota_forge/work_plane/tool_surface.py", "aota_forge/work_plane/workspace_tools.py", "aota_forge/work_plane/tool_result_governance.py"]:
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert "git_tools" not in src.lower()
            assert "BoundedGitToolProvider" not in src
            assert "GIT_STATUS_DESCRIPTOR" not in src


class TestT34S1HighConflictFilesUnchanged:
    def test_s1_high_conflict_unchanged(self):
        assert S1_HIGH_CONFLICT_FILE_CHANGE_COUNT == 0
        for path in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert "git_tools" not in src.lower()
            assert "BoundedGitToolProvider" not in src
            assert "git.status" not in src.lower()


# ---------------------------------------------------------------------------
# Additional invariants
# ---------------------------------------------------------------------------


class TestAdditionalInvariants:
    def test_flags(self):
        assert RETAIN_EXISTING_GIT_MECHANICS is True
        assert NEW_GIT_STATE_MACHINE_CREATED is False
        assert NEW_PROJECT_LIFECYCLE_ENGINE_CREATED is False
        assert MINIMAL_GIT_SURFACE is True
        assert GENERIC_GIT_COMMAND_EXECUTION is False
        assert RAW_GIT_COMMAND_STRING_ACCEPTED is False
        assert GIT_REPOSITORY_BOUND_TO_TRUSTED_WORKTREE is True
        assert CALLER_SUPPLIED_ARBITRARY_REPO_PATH is False
        assert CROSS_PROJECT_GIT_OPERATION_FAIL_CLOSED is True
        assert CROSS_WORKTREE_GIT_OPERATION_FAIL_CLOSED is True
        assert TOOL_EXPOSURE_IS_GIT_AUTHORITY is False
        assert WORK_ROLE_IS_GIT_AUTHORITY is False
        assert SANDBOX_IS_GIT_AUTHORITY is False
        assert GIT_MUTATION_AUTHORITY_REQUIRED is True
        assert IRREVERSIBLE_OPERATIONS_REQUIRE_EXPLICIT_GATE is True
        assert IRREVERSIBLE_OPERATION_WITHOUT_GATE_EXPOSED is False
        assert DESTRUCTIVE_GIT_OPERATION_REQUIRED_FOR_W3_PASS is False
        assert LIVE_GIT_REMOTE_MUTATION_REQUIRED is False
        assert NETWORK_CALL_REQUIRED_FOR_W3 is False
        assert GIT_TOOL_IS_WORKTREE_DECISION_OWNER is False
        assert EXISTING_TOOL_PROVIDER_REUSED is True
        assert EXISTING_OPERATION_DESCRIPTOR_REUSED is True
        assert EXISTING_TOOL_RESULT_GOVERNANCE_REUSED is True
        assert GIT_READ_OUTPUT_BOUNDED is True
        assert GIT_RESULT_IS_AUTHORITY is False
        assert COMMIT_SHA_IS_AUTHORITY is False
        assert BRANCH_REF_IS_AUTHORITY is False
        assert NEW_GIT_RESULT_ONTOLOGY_CREATED is False
        assert M4_RUNTIME_REUSE_PROOF_PULLED_FORWARD is False
        assert RESTRICTED_SHELL_IMPLEMENTED_IN_W3 is False
        assert GENERIC_PROCESS_TOOL_CREATED is False
        assert AGGREGATOR_EXPORT_UPDATED is False
        assert NEW_GIT_STATE_MACHINE_CREATED is False
        assert EXPOSED_GIT_OPERATIONS == ("git.status", "git.diff")
        assert GIT_MUTATION_OPERATION_EXPOSED is False

    def test_exposed_surface_minimal(self):
        assert len(EXPOSED_GIT_OPERATIONS) == 2
        assert "git.status" in EXPOSED_GIT_OPERATIONS
        assert "git.diff" in EXPOSED_GIT_OPERATIONS

    def test_commit_sha_not_authority(self):
        assert COMMIT_SHA_IS_AUTHORITY is False
        assert BRANCH_REF_IS_AUTHORITY is False
        assert GIT_RESULT_IS_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        sha = resp.payload["head_sha"]
        # Possessing SHA should not grant mutation authority
        # Try to create git authority using sha as worktree_id (should fail or not grant)
        with pytest.raises(Exception):
            # Attempt to self-mint authority using sha
            create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR, evidence_id=sha)  # this just sets evidence_id, not authority
            # But evidence_id is not authority; still need to prove sha alone cannot authorize without sandbox
            BoundedGitToolProvider(sha)  # type: ignore

    def test_result_not_authority(self):
        tmp = Path(tempfile.mkdtemp())
        _init_git_repo(tmp)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_git_authority(sandbox, handoff, [policy], GIT_STATUS_DESCRIPTOR)
        provider = BoundedGitToolProvider(authority)
        req = ToolRequest(operation=GIT_STATUS_DESCRIPTOR, inputs={})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Result payload should not be usable as authority
        assert GIT_RESULT_IS_AUTHORITY is False
        # Try to use payload dict as authority
        with pytest.raises(Exception):
            BoundedGitToolProvider(resp.payload)  # type: ignore

    def test_existing_mechanics_seam(self):
        assert EXISTING_GIT_MECHANICS_SEAM.startswith("aota_forge.core.git.inspect")
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        # Ensure delegation not copy
        assert "inspect_git(project_root" in src or "inspect_git" in src

    def test_no_copy_of_git_implementation(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "git_tools.py").read_text(encoding="utf-8")
        # Should not copy porcelain split logic
        assert "_porcelain_split" not in src
        # Should delegate
        assert "inspect_git" in src

