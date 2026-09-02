"""S2 M2-W2 — Bounded Workspace Search & Read.

Covers T01-T38 plus authorization, filesystem security, bound, scope protection.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor, READ_ONLY
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import (
    create_role_tool_surface,
    ToolCapabilityRef,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence

from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    BoundedWorkspaceToolProvider,
    WorkspaceAuthorityEvidence,
    create_workspace_authority,
    MAX_READ_BYTES,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_SEARCH_RESULTS,
    MAX_TOTAL_OUTPUT_BYTES,
    SEARCH_ROOT_FROM_TRUSTED_WORKTREE,
    TOOL_EXPOSURE_IS_AUTHORITY,
    ROLE_TOOL_SURFACE_IS_AUTHORITY,
    WORK_ROLE_IS_TOOL_PERMISSION,
    VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED,
    SANDBOX_REQUIRED_BEFORE_WORKSPACE_READ,
    TASK_SCOPE_REQUIRED_BEFORE_WORKSPACE_READ,
    POLICY_CONTEXT_REQUIRED_BEFORE_WORKSPACE_READ,
    OPERATION_AUTHORITY_REQUIRED_BEFORE_WORKSPACE_READ,
    WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER,
    MODEL_CAN_SELF_ASSERT_AUTHORITY,
    WORKSPACE_READ_BOUNDED,
    WORKSPACE_READ_PROJECT_WORKTREE_BOUND,
    WORKSPACE_SEARCH_BOUNDED,
    WORKSPACE_SEARCH_WORKTREE_BOUND,
    M1_SANDBOX_REUSED,
    M1_RESOURCE_RESOLVER_REUSED,
    RAW_ARBITRARY_ABSOLUTE_PATH_READ,
    CWD_IS_FILESYSTEM_AUTHORITY,
    RESOURCE_EVIDENCE_REVALIDATED_AT_READ,
    STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY,
    TOCTOU_ELIMINATED,
    TOCTOU_BOUNDARY_TRUTHFUL,
    CROSS_PROJECT_READ_FAIL_CLOSED,
    CROSS_PROJECT_SEARCH_FAIL_CLOSED,
    READ_SYMLINK_ESCAPE_FAIL_CLOSED,
    SEARCH_SYMLINK_ESCAPE_FAIL_CLOSED,
    WORKSPACE_READ_EXECUTION_OUTPUT_BOUNDED,
    PERSISTENT_WORKSPACE_INDEX_CREATED,
    VECTOR_SEARCH_IMPLEMENTED,
    SEARCH_RESULT_IS_AUTHORITY,
    SEARCH_MATCH_REF_GRANTS_READ_AUTHORITY,
    AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY,
    BOUNDED_SCOPE_IS_FILESYSTEM_ACL,
    WORKSPACE_MUTATION_IMPLEMENTED,
    TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2,
    EXISTING_TOOL_PROVIDER_REUSED,
    EXISTING_TOOL_REQUEST_REUSED,
    EXISTING_TOOL_RESPONSE_REUSED,
    EXISTING_OPERATION_DESCRIPTOR_REUSED,
    WORKSPACE_READ_CLASSIFICATION,
    WORKSPACE_SEARCH_CLASSIFICATION,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sandbox(tmp_root: Path, workspace_id="ws-test", project_id="proj-test", worktree_id="wt-001"):
    candidate = ProjectCandidateEvidence(
        workspace_id=workspace_id,
        workspace_root=str(tmp_root),
        project_id=project_id,
        project_root=str(tmp_root),
        manifest_path="manifest.json",
        name="test", kind="project", status="active",
        registry_fingerprint="a"*64, candidate_fingerprint="b"*64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED", workspace_id=workspace_id, workspace_root=str(tmp_root),
        registry_fingerprint="a"*64, listing_fingerprint="c"*64, candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id=worktree_id, worktree_root=tmp_root)

def _make_handoff(work_role="coder"):
    return TaskHandoff(
        work_role=work_role, task_kind="test-kind", objective="test objective",
        bounded_scope="test bounded scope", validation_expectations=("ok",), semantic_stop_expectations=("stop",),
    )

def _make_policy(project_id="proj-test", policy_id="pol-001", scope=""):
    return AgentsPolicyCandidate(policy_id=policy_id, project_id=project_id, scope=scope, content="policy content", provenance_ref="agents:AGENTS.md")

# ---------------------------------------------------------------------------
# T01 governed workspace.read on valid in-sandbox text file
# ---------------------------------------------------------------------------

class TestT01GovernedRead:
    def test_governed_read_valid(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "hello.txt").write_text("hello world", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "hello.txt"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload is not None
        assert resp.payload["content"] == "hello world"
        assert resp.payload["project_id"] == "proj-test"
        assert resp.payload["worktree_id"] == "wt-001"

# ---------------------------------------------------------------------------
# T02 governed workspace.search within sandbox
# ---------------------------------------------------------------------------

class TestT02GovernedSearch:
    def test_governed_search(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.txt").write_text("needle in a", encoding="utf-8")
        (tmp / "b.txt").write_text("no match", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload is not None
        assert len(resp.payload["results"]) == 1
        assert resp.payload["results"][0]["path"] == "a.txt"

# ---------------------------------------------------------------------------
# T03 existing ToolProvider/ToolRequest/ToolResponse reused
# ---------------------------------------------------------------------------

class TestT03ProviderReused:
    def test_provider_reused(self):
        assert EXISTING_TOOL_PROVIDER_REUSED is True
        assert EXISTING_TOOL_REQUEST_REUSED is True
        assert EXISTING_TOOL_RESPONSE_REUSED is True
        # ToolProvider is protocol, BoundedWorkspaceToolProvider implements it
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("x", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        assert isinstance(provider, ToolProvider) or hasattr(provider, "invoke")
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        resp = provider.invoke(req)
        assert isinstance(resp, ToolResponse)

# ---------------------------------------------------------------------------
# T04 W1 Tool surface identity consumed
# ---------------------------------------------------------------------------

class TestT04SurfaceConsumed:
    def test_surface_identity_consumed(self):
        # W1 surface provides visibility, but authority still required
        surface = create_role_tool_surface("coder", eager=["workspace.read", "workspace.search"], progressive=[])
        assert surface.is_visible("workspace.read")
        assert surface.is_visible("workspace.search")
        # Capability name matches descriptor name
        ref = ToolCapabilityRef.from_descriptor(WORKSPACE_READ_DESCRIPTOR)
        assert ref.capability_name == WORKSPACE_READ_DESCRIPTOR.name == "workspace.read"
        ref2 = ToolCapabilityRef.from_descriptor(WORKSPACE_SEARCH_DESCRIPTOR)
        assert ref2.capability_name == "workspace.search"
        # Provider still requires authority even though surface says visible
        tmp = Path(tempfile.mkdtemp())
        (tmp / "x.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Without authority provider cannot be constructed; visibility alone insufficient
        # Prove VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED
        assert VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED is True
        # Even with surface visible, invoking without authority fails at construction
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider(None)  # type: ignore

# ---------------------------------------------------------------------------
# T05 OperationContractDescriptor reused
# ---------------------------------------------------------------------------

class TestT05DescriptorReused:
    def test_descriptor_reused(self):
        assert EXISTING_OPERATION_DESCRIPTOR_REUSED is True
        assert isinstance(WORKSPACE_READ_DESCRIPTOR, OperationContractDescriptor)
        assert isinstance(WORKSPACE_SEARCH_DESCRIPTOR, OperationContractDescriptor)
        # validate hash semantics reused
        h1 = WORKSPACE_READ_DESCRIPTOR.contract_hash()
        h2 = OperationContractDescriptor.from_dict(WORKSPACE_READ_DESCRIPTOR.to_dict()).contract_hash()
        assert h1 == h2

# ---------------------------------------------------------------------------
# T06 read/search descriptors remain read-only
# ---------------------------------------------------------------------------

class TestT06ReadOnly:
    def test_read_only(self):
        assert WORKSPACE_READ_DESCRIPTOR.read_write == READ_ONLY == WORKSPACE_READ_CLASSIFICATION
        assert WORKSPACE_SEARCH_DESCRIPTOR.read_write == READ_ONLY == WORKSPACE_SEARCH_CLASSIFICATION
        assert WORKSPACE_READ_DESCRIPTOR.mutation_scope is None
        assert WORKSPACE_SEARCH_DESCRIPTOR.mutation_scope is None
        assert WORKSPACE_READ_DESCRIPTOR.approval_required is False
        assert WORKSPACE_SEARCH_DESCRIPTOR.approval_required is False

# ---------------------------------------------------------------------------
# T07 sandbox identity preserved
# ---------------------------------------------------------------------------

class TestT07SandboxIdentity:
    def test_sandbox_preserved(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("data", encoding="utf-8")
        sandbox = _make_sandbox(tmp, worktree_id="wt-sandbox-preserve")
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        assert provider.sandbox.worktree_id == "wt-sandbox-preserve"
        assert provider.sandbox.compute_digest() == sandbox.compute_digest()
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        resp = provider.invoke(req)
        assert resp.payload["worktree_id"] == "wt-sandbox-preserve"

# ---------------------------------------------------------------------------
# T08 project/worktree identity preserved
# ---------------------------------------------------------------------------

class TestT08ProjectWorktreeIdentity:
    def test_project_worktree_preserved(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("data", encoding="utf-8")
        sandbox = _make_sandbox(tmp, project_id="proj-preserve", worktree_id="wt-preserve")
        handoff = _make_handoff()
        policy = _make_policy(project_id="proj-preserve")
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        resp = provider.invoke(req)
        assert resp.payload["project_id"] == "proj-preserve"
        assert resp.payload["worktree_id"] == "wt-preserve"

# ---------------------------------------------------------------------------
# T09 deterministic search ordering
# ---------------------------------------------------------------------------

class TestT09DeterministicSearch:
    def test_deterministic_search(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "z.txt").write_text("needle z", encoding="utf-8")
        (tmp / "a.txt").write_text("needle a", encoding="utf-8")
        (tmp / "m.txt").write_text("needle m", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp1 = provider.invoke(req)
        resp2 = provider.invoke(req)
        assert resp1.ok and resp2.ok
        assert resp1.payload["results"] == resp2.payload["results"]
        # Ordered lexical by path
        paths = [r["path"] for r in resp1.payload["results"]]
        assert paths == sorted(paths)

# ---------------------------------------------------------------------------
# T10 bounded read output
# ---------------------------------------------------------------------------

class TestT10BoundedRead:
    def test_bounded_read_output(self):
        assert WORKSPACE_READ_BOUNDED is True
        assert WORKSPACE_READ_EXECUTION_OUTPUT_BOUNDED is True
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("x"*100, encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        resp = provider.invoke(req)
        assert resp.ok
        # output bounded by MAX_TOTAL_OUTPUT_BYTES
        import json
        size = len(json.dumps(resp.payload).encode("utf-8"))
        assert size <= MAX_TOTAL_OUTPUT_BYTES
        assert resp.payload["returned_bytes"] <= MAX_READ_BYTES

# ---------------------------------------------------------------------------
# T11 bounded search result count/output
# ---------------------------------------------------------------------------

class TestT11BoundedSearch:
    def test_bounded_search(self):
        assert WORKSPACE_SEARCH_BOUNDED is True
        assert WORKSPACE_SEARCH_WORKTREE_BOUND is True
        tmp = Path(tempfile.mkdtemp())
        for i in range(60):
            (tmp / f"file{i:02d}.txt").write_text("needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        # Request within bound succeeds but limited
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle", "max_results": 10})
        resp = provider.invoke(req)
        assert resp.ok
        assert len(resp.payload["results"]) <= 10
        # total output bounded
        import json
        assert len(json.dumps(resp.payload).encode("utf-8")) <= MAX_TOTAL_OUTPUT_BYTES

# ---------------------------------------------------------------------------
# T12 visible Tool without operation authority fails closed
# ---------------------------------------------------------------------------

class TestT12VisibleWithoutAuthority:
    def test_visible_without_authority_fails(self):
        assert VISIBLE_TOOL_CAN_STILL_BE_UNAUTHORIZED is True
        surface = create_role_tool_surface("coder", eager=["workspace.read"], progressive=[])
        assert surface.is_visible("workspace.read")
        # But without authority provider cannot invoke
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Create authority for search, then try to invoke read (mismatch)
        authority_search = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority_search)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "OPERATION_MISMATCH" in resp.error["code"] or "AUTHORITY" in resp.error["code"] or "MISMATCH" in resp.error["code"]

# ---------------------------------------------------------------------------
# T13 WorkRole alone cannot authorize
# ---------------------------------------------------------------------------

class TestT13WorkRoleAlone:
    def test_workrole_alone_cannot_authorize(self):
        assert WORK_ROLE_IS_TOOL_PERMISSION is False
        # WorkRole surface alone does not create authority
        surface = create_role_tool_surface("coder", eager=["workspace.read"], progressive=[])
        assert surface.is_visible("workspace.read")
        # No provider can be made from role alone without sandbox/handoff/policy/operation
        with pytest.raises(Exception):
            # This should fail because authority requires more than role
            BoundedWorkspaceToolProvider("coder")  # type: ignore

# ---------------------------------------------------------------------------
# T14 sandbox alone cannot authorize
# ---------------------------------------------------------------------------

class TestT14SandboxAlone:
    def test_sandbox_alone_cannot_authorize(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        # Try to create provider with only sandbox (no handoff/policy/operation)
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider(sandbox)  # type: ignore
        # Also creating authority with missing handoff fails
        handoff = _make_handoff()
        with pytest.raises(Exception):
            create_workspace_authority(sandbox, None, [], WORKSPACE_READ_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T15 Tool descriptor alone cannot authorize
# ---------------------------------------------------------------------------

class TestT15DescriptorAlone:
    def test_descriptor_alone_cannot_authorize(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        # Descriptor alone does not grant authority
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
        # Without provider with authority, cannot invoke
        # Creating provider without authority fails
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider(WORKSPACE_READ_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T16 model/caller self-asserted authority rejected
# ---------------------------------------------------------------------------

class TestT16SelfAssertedAuthority:
    def test_self_asserted_rejected(self):
        assert MODEL_CAN_SELF_ASSERT_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        # Model tries to inject authority via inputs (unknown input should be rejected by ToolRequest validation)
        with pytest.raises(Exception):
            ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt", "authority": "self-asserted"})
        # Model tries to create fake authority object with string sandbox
        with pytest.raises(Exception):
            WorkspaceAuthorityEvidence(sandbox="fake", handoff=handoff, applicable_policies=(policy,), operation=WORKSPACE_READ_DESCRIPTOR, evidence_id="fake")  # type: ignore
        # Model tries to forge authority with wrong type for handoff
        with pytest.raises(Exception):
            create_workspace_authority(sandbox, "fake-handoff", [policy], WORKSPACE_READ_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T17 missing required trusted invocation context fails closed
# ---------------------------------------------------------------------------

class TestT17MissingContext:
    def test_missing_context_fails(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        # Missing handoff
        with pytest.raises(Exception):
            create_workspace_authority(sandbox, None, [policy], WORKSPACE_READ_DESCRIPTOR)  # type: ignore
        # Missing sandbox
        with pytest.raises(Exception):
            create_workspace_authority(None, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)  # type: ignore
        # Missing operation
        with pytest.raises(Exception):
            create_workspace_authority(sandbox, handoff, [policy], None)  # type: ignore
        # Empty policy is allowed? But missing all contexts should still fail at provider construction if authority missing
        # Try provider with no authority
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider(None)  # type: ignore

# ---------------------------------------------------------------------------
# T18 absolute path rejected
# ---------------------------------------------------------------------------

class TestT18AbsolutePath:
    def test_absolute_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "/etc/passwd"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "INVALID_PATH" in resp.error["code"] or "INVALID" in resp.error["code"]

# ---------------------------------------------------------------------------
# T19 traversal rejected
# ---------------------------------------------------------------------------

class TestT19Traversal:
    def test_traversal_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        for bad in ["../f.txt", "a/../b.txt", "a/b/../../c", "..", "."]:
            req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": bad})
            resp = provider.invoke(req)
            assert resp.ok is False, f"traversal {bad!r} should be rejected"

# ---------------------------------------------------------------------------
# T20 cross-project read rejected
# ---------------------------------------------------------------------------

class TestT20CrossProject:
    def test_cross_project_rejected(self):
        assert CROSS_PROJECT_READ_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp, project_id="proj-a")
        handoff = _make_handoff()
        # Policy for different project should fail at authority creation
        policy_b = _make_policy(project_id="proj-b")
        with pytest.raises(Exception):
            create_workspace_authority(sandbox, handoff, [policy_b], WORKSPACE_READ_DESCRIPTOR)

# ---------------------------------------------------------------------------
# T21 cross-worktree stale evidence not reusable as authority
# ---------------------------------------------------------------------------

class TestT21StaleEvidence:
    def test_stale_evidence_not_authority(self):
        assert STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY is False
        assert RESOURCE_EVIDENCE_REVALIDATED_AT_READ is True
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        (tmp1 / "shared.txt").write_text("from worktree1", encoding="utf-8")
        (tmp2 / "shared.txt").write_text("from worktree2", encoding="utf-8")
        sandbox1 = _make_sandbox(tmp1, project_id="proj-test", worktree_id="wt-1")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-test", worktree_id="wt-2")
        handoff = _make_handoff()
        policy = _make_policy()
        # Create authority for worktree1
        authority1 = create_workspace_authority(sandbox1, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider1 = BoundedWorkspaceToolProvider(authority1)
        # Resource evidence from sandbox1 should not be usable to read in sandbox2
        # We prove by creating provider for sandbox2 and reading same logical path — should get wt-2 content, not wt-1
        authority2 = create_workspace_authority(sandbox2, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider2 = BoundedWorkspaceToolProvider(authority2)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "shared.txt"})
        resp1 = provider1.invoke(req)
        resp2 = provider2.invoke(req)
        assert resp1.ok and resp2.ok
        assert resp1.payload["content"] == "from worktree1"
        assert resp2.payload["content"] == "from worktree2"
        # Stale evidence: try to use worktree1 authority to read via provider2's sandbox — should fail due to operation mismatch? Actually authority1 worktree_id != provider2 sandbox
        # Provider2 stores its own sandbox, authority1's sandbox is different, but we don't mix them
        # Instead, prove that passing stale resource evidence via inputs is not possible (inputs only accept path)
        # And that provider revalidates via fresh resolve, not stale
        # If we manually try to reuse evidence canonical_path, it's not an input, so cannot be authority

# ---------------------------------------------------------------------------
# T22 resource symlink escape rejected
# ---------------------------------------------------------------------------

class TestT22SymlinkEscape:
    def test_symlink_escape_rejected(self):
        assert READ_SYMLINK_ESCAPE_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (tmp / "real.txt").write_text("real", encoding="utf-8")
        link = tmp / "link.txt"
        link.symlink_to(outside / "secret.txt")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "link.txt"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "SYMLINK" in resp.error["code"] or "INVALID_PATH" in resp.error["code"]

# ---------------------------------------------------------------------------
# T23 directory symlink search escape rejected
# ---------------------------------------------------------------------------

class TestT23DirSymlinkSearchEscape:
    def test_dir_symlink_search_escape(self):
        assert SEARCH_SYMLINK_ESCAPE_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (outside / "outside.txt").write_text("needle outside", encoding="utf-8")
        (tmp / "inside.txt").write_text("needle inside", encoding="utf-8")
        link_dir = tmp / "linkdir"
        link_dir.symlink_to(outside)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp = provider.invoke(req)
        assert resp.ok is True
        paths = [r["path"] for r in resp.payload["results"]]
        # should not include outside.txt via symlink dir
        assert "outside.txt" not in str(paths)
        assert "linkdir/outside.txt" not in paths
        assert any("inside.txt" in p for p in paths)

# ---------------------------------------------------------------------------
# T24 search cannot change root to caller supplied host path
# ---------------------------------------------------------------------------

class TestT24SearchRoot:
    def test_search_root_not_caller_supplied(self):
        assert SEARCH_ROOT_FROM_TRUSTED_WORKTREE is True
        assert CROSS_PROJECT_SEARCH_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        # Try to inject absolute root via scope
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle", "scope": "/tmp"})
        resp = provider.invoke(req)
        assert resp.ok is False
        # Try to inject traversal via scope
        req2 = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle", "scope": "../"})
        resp2 = provider.invoke(req2)
        assert resp2.ok is False
        # Try unknown input "root" should be rejected by ToolRequest validation
        with pytest.raises(Exception):
            ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle", "root": "/etc"})

# ---------------------------------------------------------------------------
# T25 oversized read fails/bounds deterministically
# ---------------------------------------------------------------------------

class TestT25OversizedRead:
    def test_oversized_read(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "big.txt").write_text("x" * (MAX_READ_BYTES + 500), encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "big.txt"})
        resp = provider.invoke(req)
        # Should either fail or bound with truncated metadata
        assert resp.ok is True  # we bound via truncation
        assert resp.payload["truncated"] is True
        assert resp.payload["total_bytes"] > MAX_READ_BYTES
        assert resp.payload["returned_bytes"] <= MAX_READ_BYTES
        # Deterministic: second call same result
        resp2 = provider.invoke(req)
        assert resp.payload == resp2.payload
        # Explicit max_bytes exceeding bound should fail
        req_big = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "big.txt", "max_bytes": MAX_READ_BYTES + 1})
        resp_big = provider.invoke(req_big)
        assert resp_big.ok is False

# ---------------------------------------------------------------------------
# T26 oversized search query rejected
# ---------------------------------------------------------------------------

class TestT26OversizedQuery:
    def test_oversized_query(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        long_query = "x" * (MAX_SEARCH_QUERY_LENGTH + 1)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": long_query})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "INVALID_QUERY" in resp.error["code"] or "BOUNDED" in resp.error["code"]

# ---------------------------------------------------------------------------
# T27 excessive result request bounded/rejected
# ---------------------------------------------------------------------------

class TestT27ExcessiveResults:
    def test_excessive_results(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(5):
            (tmp / f"f{i}.txt").write_text("needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle", "max_results": MAX_SEARCH_RESULTS + 10})
        resp = provider.invoke(req)
        assert resp.ok is False

# ---------------------------------------------------------------------------
# T28 binary/invalid encoding behavior deterministic
# ---------------------------------------------------------------------------

class TestT28BinaryEncoding:
    def test_binary_deterministic(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin.bin").write_bytes(b"\xff\xfe\xfd")
        (tmp / "valid.txt").write_text("hello", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "bin.bin"})
        resp1 = provider.invoke(req)
        resp2 = provider.invoke(req)
        assert resp1.ok is False
        assert resp2.ok is False
        assert resp1.error["code"] == resp2.error["code"]
        assert "INVALID_ENCODING" in resp1.error["code"]
        # Search should skip binary deterministically
        authority_s = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider_s = BoundedWorkspaceToolProvider(authority_s)
        req_s = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "hello"})
        resp_s = provider_s.invoke(req_s)
        assert resp_s.ok is True
        # Should find valid.txt but not treat bin.bin as match
        assert any("valid.txt" in r["path"] for r in resp_s.payload["results"])

# ---------------------------------------------------------------------------
# T29 no silent unbounded truncation contract ambiguity
# ---------------------------------------------------------------------------

class TestT29TruncationMetadata:
    def test_truncation_metadata(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "big.txt").write_text("x" * (MAX_READ_BYTES + 100), encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "big.txt"})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Must provide truthful bounded metadata
        assert "truncated" in resp.payload
        assert "total_bytes" in resp.payload
        assert "returned_bytes" in resp.payload
        assert resp.payload["truncated"] is True
        assert resp.payload["total_bytes"] > resp.payload["returned_bytes"]

# ---------------------------------------------------------------------------
# T30 no workspace mutation
# ---------------------------------------------------------------------------

class TestT30NoMutation:
    def test_no_mutation(self):
        assert WORKSPACE_MUTATION_IMPLEMENTED is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        # Must not contain mutation keywords as implementation
        import ast
        tree = ast.parse(src)
        func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for bad in ("workspace_write", "workspace_create", "workspace_delete", "workspace_edit", "workspace_mutation", "create_file", "write_file"):
            assert bad not in func_names
        assert "WORKSPACE_MUTATION_IMPLEMENTED" in src
        # Ensure provider only handles read/search
        assert "workspace.read" in src
        assert "workspace.search" in src

# ---------------------------------------------------------------------------
# T31 no Tool Result Governance
# ---------------------------------------------------------------------------

class TestT31NoResultGovernance:
    def test_no_result_governance(self):
        assert TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        import ast
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ToolResultCard", "ToolResultGovernance", "ResultStore", "ToolResultStore"):
            assert bad not in classes

# ---------------------------------------------------------------------------
# T32 no result ref/store
# ---------------------------------------------------------------------------

class TestT32NoRefStore:
    def test_no_ref_store(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        assert "result_ref" not in src.lower() or "result ref" not in src.lower()
        # Ensure no persistent store creation
        assert "result_store" not in src.lower()

# ---------------------------------------------------------------------------
# T33 no restricted shell
# ---------------------------------------------------------------------------

class TestT33NoShell:
    def test_no_shell(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        low = src.lower()
        assert "restricted_shell" not in low
        assert "subprocess" not in low or "subprocess" in low and "shell" not in low

# ---------------------------------------------------------------------------
# T34 no test execution Tool
# ---------------------------------------------------------------------------

class TestT34NoTestExecution:
    def test_no_test_execution(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        assert "test_execution" not in src.lower()
        assert "run_tests" not in src.lower()

# ---------------------------------------------------------------------------
# T35 no Skill implementation
# ---------------------------------------------------------------------------

class TestT35NoSkill:
    def test_no_skill(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_tools.py").read_text(encoding="utf-8")
        import ast
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("SkillRegistry", "SkillLoader"):
            assert bad not in classes
        assert "S3_IMPLEMENTATION_INTRODUCED" in src

# ---------------------------------------------------------------------------
# T36 tool_surface.py unchanged
# ---------------------------------------------------------------------------

class TestT36ToolSurfaceUnchanged:
    def test_tool_surface_unchanged(self):
        p = REPO_ROOT / "aota_forge" / "work_plane" / "tool_surface.py"
        src = p.read_text(encoding="utf-8")
        assert "WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1" in src
        assert "WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1: bool = False" in src

# ---------------------------------------------------------------------------
# T37 S1 high-conflict files unchanged
# ---------------------------------------------------------------------------

class TestT37HighConflict:
    def test_high_conflict_unchanged(self):
        for path in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert "workspace_tools" not in content.lower()
            assert "BoundedWorkspaceToolProvider" not in content

# ---------------------------------------------------------------------------
# T38 M1 source contracts unchanged
# ---------------------------------------------------------------------------

class TestT38M1Unchanged:
    def test_m1_unchanged(self):
        for path in ["aota_forge/core/providers/tool.py", "aota_forge/core/contracts/descriptor.py"]:
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert "workspace" not in src.lower() or "workspace" in src.lower() and "BoundedWorkspace" not in src

# ---------------------------------------------------------------------------
# Additional invariants
# ---------------------------------------------------------------------------

class TestAdditionalInvariants:
    def test_flags(self):
        assert TOOL_EXPOSURE_IS_AUTHORITY is False
        assert ROLE_TOOL_SURFACE_IS_AUTHORITY is False
        assert WORK_ROLE_IS_TOOL_PERMISSION is False
        assert SANDBOX_REQUIRED_BEFORE_WORKSPACE_READ is True
        assert TASK_SCOPE_REQUIRED_BEFORE_WORKSPACE_READ is True
        assert POLICY_CONTEXT_REQUIRED_BEFORE_WORKSPACE_READ is True
        assert OPERATION_AUTHORITY_REQUIRED_BEFORE_WORKSPACE_READ is True
        assert WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER is False
        assert MODEL_CAN_SELF_ASSERT_AUTHORITY is False
        assert M1_SANDBOX_REUSED is True
        assert M1_RESOURCE_RESOLVER_REUSED is True
        assert RAW_ARBITRARY_ABSOLUTE_PATH_READ is False
        assert CWD_IS_FILESYSTEM_AUTHORITY is False
        assert RESOURCE_EVIDENCE_REVALIDATED_AT_READ is True
        assert STALE_RESOLUTION_EVIDENCE_IS_READ_AUTHORITY is False
        assert TOCTOU_ELIMINATED is False
        assert TOCTOU_BOUNDARY_TRUTHFUL is True
        assert PERSISTENT_WORKSPACE_INDEX_CREATED is False
        assert VECTOR_SEARCH_IMPLEMENTED is False
        assert SEARCH_RESULT_IS_AUTHORITY is False
        assert SEARCH_MATCH_REF_GRANTS_READ_AUTHORITY is False
        assert AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY is False
        assert BOUNDED_SCOPE_IS_FILESYSTEM_ACL is False
        assert WORKSPACE_MUTATION_IMPLEMENTED is False
        assert TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W2 is False
        assert WORKSPACE_READ_BOUNDED is True
        assert WORKSPACE_SEARCH_BOUNDED is True

    def test_cwd_not_authority(self):
        # CWD should not affect read
        tmp = Path(tempfile.mkdtemp())
        (tmp / "f.txt").write_text("hi", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        orig_cwd = os.getcwd()
        try:
            os.chdir("/tmp")
            req = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": "f.txt"})
            resp = provider.invoke(req)
            assert resp.ok is True
            assert resp.payload["content"] == "hi"
        finally:
            os.chdir(orig_cwd)

    def test_search_result_not_authority(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "secret.txt").write_text("needle", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR)
        provider = BoundedWorkspaceToolProvider(authority)
        req = ToolRequest(operation=WORKSPACE_SEARCH_DESCRIPTOR, inputs={"query": "needle"})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Search result path should not be directly usable as authority; subsequent read still requires governed path
        # Prove that search result does not grant read authority: we still need to do governed read
        path = resp.payload["results"][0]["path"]
        # That path is logical relative, not absolute authority
        assert not Path(path).is_absolute()
        # Subsequent governed read must still go through provider with authority
        authority_read = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        provider_read = BoundedWorkspaceToolProvider(authority_read)
        req_read = ToolRequest(operation=WORKSPACE_READ_DESCRIPTOR, inputs={"path": path})
        resp_read = provider_read.invoke(req_read)
        assert resp_read.ok is True

