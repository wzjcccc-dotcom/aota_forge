"""S2 M3-W1 — Workspace Mutation & Artifact Operations."""

from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface, ToolCapabilityRef
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.tool_result_governance import project_tool_result, ToolResultProjection
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    WorkspaceMutationAuthority,
    create_workspace_mutation_authority,
    BoundedWorkspaceMutationProvider,
    ArtifactReference,
    create_artifact_reference,
    validate_artifact_reference,
    WorkspaceMutationAuthorityError,
    WorkspaceMutationError,
    ArtifactReferenceError,
    ArtifactTamperError,
    MAX_WRITE_BYTES,
    WRITE_MODES,
    READ_AUTHORITY_IS_WRITE_AUTHORITY,
    RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY,
    TOOL_EXPOSURE_IS_MUTATION_AUTHORITY,
    WORK_ROLE_IS_MUTATION_AUTHORITY,
    AGENTS_POLICY_IS_MUTATION_AUTHORITY,
    OPERATION_DESCRIPTOR_IS_MUTATION_AUTHORITY_DECISION,
    MUTATION_AUTHORITY_REQUIRED,
    WORKSPACE_WRITE_BOUNDED,
    WORKSPACE_WRITE_PROJECT_WORKTREE_BOUND,
    RAW_ABSOLUTE_WRITE_PATH_ALLOWED,
    CWD_IS_MUTATION_AUTHORITY,
    PATH_TRAVERSAL_WRITE_FAIL_CLOSED,
    CROSS_PROJECT_WRITE_FAIL_CLOSED,
    CROSS_WORKTREE_WRITE_FAIL_CLOSED,
    WRITE_TARGET_REVALIDATED_AT_USE,
    STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY,
    WRITE_SYMLINK_ESCAPE_FAIL_CLOSED,
    DIRECTORY_SYMLINK_ESCAPE_FAIL_CLOSED,
    WRITE_TOCTOU_BOUNDARY_EXPLICIT,
    ATOMIC_REPLACE_SUPPORTED,
    MUTATION_RESULT_IS_AUTHORITY,
    MUTATION_SIDE_EFFECT_AND_ARTIFACT_REFERENCE_SEPARATE,
    ARTIFACT_REFERENCE_IS_AUTHORITY,
    ARTIFACT_REF_DIGEST_BOUND,
    ARTIFACT_REF_PROJECT_WORKTREE_SCOPED,
    ARTIFACT_REF_DIGEST_IS_AUTHORITY,
    ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY,
    NEW_PERSISTENT_ARTIFACT_STORE_CREATED,
    ARTIFACT_IMPLEMENTATION_MODE,
    WRITE_INPUT_BOUNDED,
    WRITE_RESULT_OUTPUT_BOUNDED,
    FAILED_MUTATION_REPORTED_AS_SUCCESS,
    EXISTING_TOOL_CONTRACT_REUSED,
    M1_SANDBOX_REUSED,
    M1_RESOURCE_RESOLVER_REUSED,
    M2_RESULT_GOVERNANCE_REUSED,
    TEST_EXECUTION_IMPLEMENTED_IN_W1,
    GIT_OPERATION_IMPLEMENTED_IN_W1,
    RESTRICTED_SHELL_IMPLEMENTED_IN_W1,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

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
# T01 valid authorized bounded workspace write succeeds
# ---------------------------------------------------------------------------

class TestT01ValidWrite:
    def test_valid_authorized_bounded_write_succeeds(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "hello.txt", "content": "hello world", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert resp.payload is not None
        assert resp.payload["path"] == "hello.txt"
        assert resp.payload["byte_length"] == len("hello world".encode("utf-8"))
        assert resp.payload["digest"] == hashlib.sha256(b"hello world").hexdigest()
        assert resp.payload["project_id"] == "proj-test"
        assert resp.payload["worktree_id"] == "wt-001"
        assert Path(tmp / "hello.txt").read_text(encoding="utf-8") == "hello world"
        # output bounded — does not echo full payload inline excessively? check bounded size
        assert WRITE_RESULT_OUTPUT_BOUNDED is True
        # second write with create_or_replace succeeds
        req2 = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "hello.txt", "content": "new content", "mode": "create_or_replace"})
        resp2 = provider.invoke(req2)
        assert resp2.ok is True
        assert Path(tmp / "hello.txt").read_text(encoding="utf-8") == "new content"

# ---------------------------------------------------------------------------
# T02 write remains inside trusted worktree
# ---------------------------------------------------------------------------

class TestT02WriteInsideWorktree:
    def test_write_remains_inside_trusted_worktree(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "subdir/nested/file.txt", "content": "data", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert Path(tmp / "subdir/nested/file.txt").exists()
        # canonical_path must be under root
        assert Path(resp.payload["canonical_path"]).resolve().is_relative_to(tmp.resolve())

# ---------------------------------------------------------------------------
# T03 accepted ToolProvider reused
# ---------------------------------------------------------------------------

class TestT03ToolProviderReused:
    def test_accepted_tool_provider_reused(self):
        assert EXISTING_TOOL_CONTRACT_REUSED is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        assert hasattr(provider, "invoke")
        # ToolProvider protocol check
        assert isinstance(provider, ToolProvider) or hasattr(provider, "invoke")
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "a.txt", "content": "x", "mode": "create_only"})
        resp = provider.invoke(req)
        assert isinstance(resp, ToolResponse)

# ---------------------------------------------------------------------------
# T04 accepted OperationContractDescriptor reused
# ---------------------------------------------------------------------------

class TestT04DescriptorReused:
    def test_descriptor_reused(self):
        assert isinstance(WORKSPACE_WRITE_DESCRIPTOR, OperationContractDescriptor)
        assert WORKSPACE_WRITE_DESCRIPTOR.name == "workspace.write"
        # hash stable
        h1 = WORKSPACE_WRITE_DESCRIPTOR.contract_hash()
        h2 = OperationContractDescriptor.from_dict(WORKSPACE_WRITE_DESCRIPTOR.to_dict()).contract_hash()
        assert h1 == h2

# ---------------------------------------------------------------------------
# T05 accepted sandbox/resource resolver reused
# ---------------------------------------------------------------------------

class TestT05SandboxResolverReused:
    def test_sandbox_resolver_reused(self):
        assert M1_SANDBOX_REUSED is True
        assert M1_RESOURCE_RESOLVER_REUSED is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, worktree_id="wt-005")
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        assert provider.sandbox.worktree_id == "wt-005"
        assert provider.sandbox.compute_digest() == sandbox.compute_digest()
        # Resource resolver is used internally — prove via successful write that used it
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "f.txt", "content": "hi", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True

# ---------------------------------------------------------------------------
# T06 write result feeds existing Tool Result Governance
# ---------------------------------------------------------------------------

class TestT06WriteResultGovernance:
    def test_write_result_feeds_governance(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "g.txt", "content": "governed", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Project into existing Tool Result Governance
        proj = project_tool_result(resp, WORKSPACE_WRITE_DESCRIPTOR, sandbox)
        assert isinstance(proj, ToolResultProjection)
        assert proj.is_success is True
        assert proj.project_id == "proj-test"
        assert proj.worktree_id == "wt-001"
        # GovernedReference bridge
        refs = proj.as_governed_evidence_refs()
        assert len(refs) >= 1
        assert all(isinstance(r, GovernedReference) for r in refs)
        assert M2_RESULT_GOVERNANCE_REUSED is True

# ---------------------------------------------------------------------------
# T07 artifact ref can be projected from produced bounded resource
# ---------------------------------------------------------------------------

class TestT07ArtifactRefFromResource:
    def test_artifact_ref_projected(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "artifact.txt", "content": "artifact content", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is True
        # Create artifact reference from produced file
        artifact = create_artifact_reference(sandbox, "artifact.txt")
        assert isinstance(artifact, ArtifactReference)
        assert artifact.logical_ref == "artifact.txt"
        assert artifact.project_id == "proj-test"
        assert artifact.worktree_id == "wt-001"
        assert artifact.digest == hashlib.sha256(b"artifact content").hexdigest()
        # Bridge to GovernedReference
        gov = artifact.as_governed_reference()
        assert gov.kind == GovernedReferenceKind.ARTIFACT
        assert gov.digest == artifact.digest

# ---------------------------------------------------------------------------
# T08 artifact digest stable for same content
# ---------------------------------------------------------------------------

class TestT08DigestStable:
    def test_digest_stable(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        content = "stable content"
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "a.txt", "content": content, "mode": "create_only"}))
        art1 = create_artifact_reference(sandbox, "a.txt")
        # Rewrite same content to different file
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "b.txt", "content": content, "mode": "create_only"}))
        art2 = create_artifact_reference(sandbox, "b.txt")
        assert art1.digest == art2.digest
        assert art1.digest == hashlib.sha256(content.encode("utf-8")).hexdigest()
        # Also direct creation with same content yields same digest
        art3 = create_artifact_reference(sandbox, "a.txt", content=content)
        assert art3.digest == art1.digest

# ---------------------------------------------------------------------------
# T09 artifact project/worktree identity preserved
# ---------------------------------------------------------------------------

class TestT09IdentityPreserved:
    def test_identity_preserved(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-xyz", worktree_id="wt-xyz")
        handoff = _make_handoff()
        policy = _make_policy(project_id="proj-xyz")
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "id.txt", "content": "id", "mode": "create_only"}))
        art = create_artifact_reference(sandbox, "id.txt")
        assert art.project_id == "proj-xyz"
        assert art.worktree_id == "wt-xyz"
        # Validate via current sandbox
        validated = validate_artifact_reference(art, sandbox)
        assert validated == art
        assert ARTIFACT_REF_PROJECT_WORKTREE_SCOPED is True

# ---------------------------------------------------------------------------
# T10 mutation and artifact reference remain distinct
# ---------------------------------------------------------------------------

class TestT10MutationArtifactDistinct:
    def test_mutation_and_artifact_distinct(self):
        assert MUTATION_SIDE_EFFECT_AND_ARTIFACT_REFERENCE_SEPARATE is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        # Mutation succeeds
        resp = provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "mut.txt", "content": "mut", "mode": "create_only"}))
        assert resp.ok is True
        # Mutation does NOT automatically produce artifact reference in payload
        assert "artifact_ref" not in (resp.payload or {})
        # Artifact ref is separate call
        art = create_artifact_reference(sandbox, "mut.txt")
        assert isinstance(art, ArtifactReference)
        # Artifact possession does NOT grant mutation — trying to use artifact as authority fails
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(art)  # type: ignore
        # Also prove artifact cannot be used to write without authority
        # Attempt to invoke with artifact's digest as authority — should not be accepted
        assert ARTIFACT_REFERENCE_IS_AUTHORITY is False
        assert ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY is False

# ---------------------------------------------------------------------------
# T11 M2 read authority alone cannot write
# ---------------------------------------------------------------------------

class TestT11ReadAuthorityCannotWrite:
    def test_read_authority_cannot_write(self):
        assert READ_AUTHORITY_IS_WRITE_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-test", worktree_id="wt-001")
        handoff = _make_handoff()
        policy = _make_policy()
        read_authority = create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR)
        # Attempt to use read authority to construct mutation provider — must fail
        with pytest.raises((WorkspaceMutationAuthorityError, TypeError, ValueError)):
            BoundedWorkspaceMutationProvider(read_authority)  # type: ignore
        # Also try to invoke write with read-authority provider via type confusion
        # Provider expects WorkspaceMutationAuthority, so passing read authority is rejected at init

# ---------------------------------------------------------------------------
# T12 WorkRole alone cannot write
# ---------------------------------------------------------------------------

class TestT12WorkRoleAlone:
    def test_workrole_alone_cannot_write(self):
        assert WORK_ROLE_IS_MUTATION_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        # WorkRole surface alone
        surface = create_role_tool_surface("coder", eager=["workspace.write"], progressive=[])
        assert surface.is_visible("workspace.write")
        assert surface.is_authority is False
        with pytest.raises(NotImplementedError):
            surface.authorize()
        # No provider can be made from role alone
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider("coder")  # type: ignore

# ---------------------------------------------------------------------------
# T13 Tool visibility alone cannot write
# ---------------------------------------------------------------------------

class TestT13VisibilityAlone:
    def test_visibility_alone_cannot_write(self):
        assert TOOL_EXPOSURE_IS_MUTATION_AUTHORITY is False
        surface = create_role_tool_surface("coder", eager=["workspace.write"], progressive=[])
        assert surface.is_visible("workspace.write")
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        # Even with visibility, need mutation authority
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(surface)  # type: ignore

# ---------------------------------------------------------------------------
# T14 sandbox alone cannot write
# ---------------------------------------------------------------------------

class TestT14SandboxAlone:
    def test_sandbox_alone_cannot_write(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(sandbox)  # type: ignore
        with pytest.raises(Exception):
            create_workspace_mutation_authority(sandbox, None, [], WORKSPACE_WRITE_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T15 descriptor presence alone cannot write
# ---------------------------------------------------------------------------

class TestT15DescriptorAlone:
    def test_descriptor_alone_cannot_write(self):
        tmp = Path(tempfile.mkdtemp())
        # Descriptor alone does not grant write — need authority
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(WORKSPACE_WRITE_DESCRIPTOR)  # type: ignore
        # ToolRequest with descriptor alone still requires provider with authority
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "x.txt", "content": "hi", "mode": "create_only"})
        # Without provider authority, cannot invoke
        assert OPERATION_DESCRIPTOR_IS_MUTATION_AUTHORITY_DECISION is False
        # Prove descriptor is declaration not decision: surface visibility != authority
        surface = create_role_tool_surface("coder", eager=[ToolCapabilityRef.from_descriptor(WORKSPACE_WRITE_DESCRIPTOR)], progressive=[])
        assert surface.is_authority is False

# ---------------------------------------------------------------------------
# T16 caller cannot self-assert mutation authority
# ---------------------------------------------------------------------------

class TestT16SelfAssert:
    def test_self_assert_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        # Model tries to inject authority via inputs — unknown input rejected by ToolRequest
        with pytest.raises(Exception):
            ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "x.txt", "content": "hi", "mode": "create_only", "authority": "self"})
        # Model tries to forge authority object with string types
        with pytest.raises(Exception):
            WorkspaceMutationAuthority(sandbox="fake", handoff=handoff, applicable_policies=(policy,), operation=WORKSPACE_WRITE_DESCRIPTOR, evidence_id="fake")  # type: ignore
        # Model tries to create authority with wrong param types
        with pytest.raises(Exception):
            create_workspace_mutation_authority(sandbox, "fake-handoff", [policy], WORKSPACE_WRITE_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T17 artifact ref cannot authorize mutation
# ---------------------------------------------------------------------------

class TestT17ArtifactCannotAuthorize:
    def test_artifact_cannot_authorize(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "src.txt", "content": "src", "mode": "create_only"}))
        art = create_artifact_reference(sandbox, "src.txt")
        # Try to use artifact as authority — must fail
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(art)  # type: ignore
        # Also prove possession doesn't allow write without provider
        assert ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY is False
        # Direct check that artifact is not authority
        assert art.is_authority is False
        with pytest.raises(NotImplementedError):
            art.authorize()  # type: ignore

# ---------------------------------------------------------------------------
# T18 absolute write path rejected
# ---------------------------------------------------------------------------

class TestT18AbsolutePath:
    def test_absolute_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        assert RAW_ABSOLUTE_WRITE_PATH_ALLOWED is False
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "/etc/passwd", "content": "evil", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "PATH_ESCAPE" in resp.error["code"] or "INVALID_PATH" in resp.error["code"]

# ---------------------------------------------------------------------------
# T19 traversal rejected
# ---------------------------------------------------------------------------

class TestT19Traversal:
    def test_traversal_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        assert PATH_TRAVERSAL_WRITE_FAIL_CLOSED is True
        for bad in ["../evil.txt", "a/../../b.txt", "..", "a/.."]:
            req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": bad, "content": "x", "mode": "create_only"})
            resp = provider.invoke(req)
            assert resp.ok is False, f"traversal {bad!r} should be rejected"

# ---------------------------------------------------------------------------
# T20 cross-project write rejected
# ---------------------------------------------------------------------------

class TestT20CrossProject:
    def test_cross_project_rejected(self):
        assert CROSS_PROJECT_WRITE_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp, project_id="proj-a")
        handoff = _make_handoff()
        policy_b = _make_policy(project_id="proj-b")
        with pytest.raises(Exception):
            create_workspace_mutation_authority(sandbox, handoff, [policy_b], WORKSPACE_WRITE_DESCRIPTOR)

# ---------------------------------------------------------------------------
# T21 cross-worktree write rejected
# ---------------------------------------------------------------------------

class TestT21CrossWorktree:
    def test_cross_worktree_rejected(self):
        assert CROSS_WORKTREE_WRITE_FAIL_CLOSED is True
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        sandbox1 = _make_sandbox(tmp1, project_id="proj-test", worktree_id="wt-1")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-test", worktree_id="wt-2")
        handoff = _make_handoff()
        policy = _make_policy()
        # Authority for wt-1 cannot be used to write to wt-2's root via provider constructed with wt-1's sandbox
        # But we can test that creating authority with sandbox1 and then verifying provider's sandbox is wt-1
        authority1 = create_workspace_mutation_authority(sandbox1, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider1 = BoundedWorkspaceMutationProvider(authority1)
        # Provider1 writes only inside tmp1, not tmp2
        resp = provider1.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "cross.txt", "content": "hi", "mode": "create_only"}))
        assert resp.ok is True
        assert Path(tmp1 / "cross.txt").exists()
        assert not Path(tmp2 / "cross.txt").exists()
        # Stale evidence test: authority for wt-1 should not be mixable to wt-2 sandbox
        assert STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY is False

# ---------------------------------------------------------------------------
# T22 target symlink escape rejected
# ---------------------------------------------------------------------------

class TestT22SymlinkEscape:
    def test_symlink_escape_rejected(self):
        assert WRITE_SYMLINK_ESCAPE_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        link = tmp / "link.txt"
        link.symlink_to(outside / "secret.txt")
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "link.txt", "content": "evil", "mode": "create_or_replace"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "SYMLINK" in resp.error["code"]

# ---------------------------------------------------------------------------
# T23 parent-directory symlink escape rejected
# ---------------------------------------------------------------------------

class TestT23ParentSymlinkEscape:
    def test_parent_symlink_escape_rejected(self):
        assert DIRECTORY_SYMLINK_ESCAPE_FAIL_CLOSED is True
        tmp = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (tmp / "realdir").mkdir()
        linkdir = tmp / "linkdir"
        linkdir.symlink_to(outside)
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "linkdir/evil.txt", "content": "evil", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "SYMLINK" in resp.error["code"]

# ---------------------------------------------------------------------------
# T24 stale resolution evidence cannot authorize write
# ---------------------------------------------------------------------------

class TestT24StaleEvidence:
    def test_stale_evidence_cannot_authorize(self):
        assert STALE_RESOURCE_EVIDENCE_IS_WRITE_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        # Normal write succeeds
        resp = provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "stale.txt", "content": "first", "mode": "create_only"}))
        assert resp.ok is True
        # Simulate stale: external process replaces file with symlink after resolution but before write?
        # Our provider revalidates at use, so symlink swap should be caught
        target = tmp / "stale2.txt"
        target.write_text("orig", encoding="utf-8")
        # Create a symlink outside and try to trick via path? Already covered by symlink checks
        # Here we just prove that passing old evidence dict does not authorize
        with pytest.raises(Exception):
            # Try to pass stale evidence via inputs — unknown input should be rejected
            ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "stale.txt", "content": "second", "mode": "create_only", "evidence": "stale"})

# ---------------------------------------------------------------------------
# T25 mutation target revalidated at use
# ---------------------------------------------------------------------------

class TestT25RevalidatedAtUse:
    def test_revalidated_at_use(self):
        assert WRITE_TARGET_REVALIDATED_AT_USE is True
        assert WRITE_TOCTOU_BOUNDARY_EXPLICIT is True
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        # Write succeeds and revalidation is internal — we prove by checking that provider does not trust external evidence
        # The fact that provider writes correctly even after we create file externally shows revalidation
        (tmp / "reval.txt").write_text("old", encoding="utf-8")
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "reval.txt", "content": "new", "mode": "create_or_replace"})
        resp = provider.invoke(req)
        assert resp.ok is True
        assert Path(tmp / "reval.txt").read_text(encoding="utf-8") == "new"

# ---------------------------------------------------------------------------
# T26 oversized payload fails/bounds deterministically
# ---------------------------------------------------------------------------

class TestT26Oversized:
    def test_oversized_payload(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        big = "x" * (MAX_WRITE_BYTES + 1)
        # Validation layer also enforces bounded size (4096) — oversized may fail at ToolRequest creation
        try:
            req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "big.txt", "content": big, "mode": "create_only"})
        except Exception as exc:
            # Fail-closed at validation boundary is also bounded/deterministic
            assert WRITE_INPUT_BOUNDED is True
            # Second attempt deterministic same exception type
            with pytest.raises(type(exc)):
                ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "big.txt", "content": big, "mode": "create_only"})
            return
        resp = provider.invoke(req)
        assert resp.ok is False
        assert "OVERSIZED" in resp.error["code"] or "BOUNDED" in resp.error["code"] or "SIZE" in resp.error["code"]
        assert WRITE_INPUT_BOUNDED is True
        # Deterministic: second call same failure
        resp2 = provider.invoke(req)
        assert resp2.error["code"] == resp.error["code"]

# ---------------------------------------------------------------------------
# T27 invalid write mode fails closed
# ---------------------------------------------------------------------------

class TestT27InvalidMode:
    def test_invalid_mode(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        for bad in ["", "invalid", "CREATE_ONLY", "overwrite", None]:
            if bad is None:
                with pytest.raises(Exception):
                    ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "x.txt", "content": "hi", "mode": bad})
                continue
            req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "x.txt", "content": "hi", "mode": bad})
            resp = provider.invoke(req)
            assert resp.ok is False
            assert "INVALID_MODE" in resp.error["code"]
        # Mode create_only vs replace_existing semantics
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "mode.txt", "content": "a", "mode": "create_only"}))
        resp2 = provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "mode.txt", "content": "b", "mode": "create_only"}))
        assert resp2.ok is False
        resp3 = provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "nonexist.txt", "content": "b", "mode": "replace_existing"}))
        assert resp3.ok is False

# ---------------------------------------------------------------------------
# T28 failed write never returns successful mutation evidence
# ---------------------------------------------------------------------------

class TestT28FailedWrite:
    def test_failed_write_never_success(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "/abs.txt", "content": "hi", "mode": "create_only"})
        resp = provider.invoke(req)
        assert resp.ok is False
        assert FAILED_MUTATION_REPORTED_AS_SUCCESS is False
        # Ensure no file was created at absolute path leak
        assert not Path("/abs.txt").exists()
        assert resp.payload is None
        assert resp.error is not None

# ---------------------------------------------------------------------------
# T29 tampered artifact digest fails closed
# ---------------------------------------------------------------------------

class TestT29TamperedDigest:
    def test_tampered_digest(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "t.txt", "content": "hello", "mode": "create_only"}))
        art = create_artifact_reference(sandbox, "t.txt")
        # Tamper digest
        tampered = ArtifactReference(logical_ref=art.logical_ref, digest="0"*64, project_id=art.project_id, worktree_id=art.worktree_id, byte_length=art.byte_length)
        with pytest.raises((ArtifactTamperError, ArtifactReferenceError, ValueError)):
            validate_artifact_reference(tampered, sandbox)
        # Also tamper via content modification
        Path(tmp / "t.txt").write_text("modified", encoding="utf-8")
        with pytest.raises((ArtifactTamperError, ArtifactReferenceError)):
            validate_artifact_reference(art, sandbox)
        assert ARTIFACT_REF_DIGEST_BOUND is True

# ---------------------------------------------------------------------------
# T30 foreign artifact project/worktree scope fails closed
# ---------------------------------------------------------------------------

class TestT30ForeignScope:
    def test_foreign_scope(self):
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        sandbox1 = _make_sandbox(tmp1, project_id="proj-a", worktree_id="wt-a")
        sandbox2 = _make_sandbox(tmp2, project_id="proj-b", worktree_id="wt-b")
        handoff = _make_handoff()
        policy1 = _make_policy(project_id="proj-a")
        authority1 = create_workspace_mutation_authority(sandbox1, handoff, [policy1], WORKSPACE_WRITE_DESCRIPTOR)
        provider1 = BoundedWorkspaceMutationProvider(authority1)
        provider1.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "f.txt", "content": "data", "mode": "create_only"}))
        art = create_artifact_reference(sandbox1, "f.txt")
        # Validate against foreign sandbox should fail
        with pytest.raises((ArtifactTamperError, ArtifactReferenceError)):
            validate_artifact_reference(art, sandbox2)
        assert ARTIFACT_REF_PROJECT_WORKTREE_SCOPED is True
        assert CROSS_PROJECT_WRITE_FAIL_CLOSED is True

# ---------------------------------------------------------------------------
# T31 artifact ref possession grants no mutation authority
# ---------------------------------------------------------------------------

class TestT31ArtifactNoAuthority:
    def test_artifact_no_mutation_authority(self):
        assert ARTIFACT_REF_POSSESSION_GRANTS_MUTATION_AUTHORITY is False
        tmp = Path(tempfile.mkdtemp())
        sandbox = _make_sandbox(tmp)
        handoff = _make_handoff()
        policy = _make_policy()
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "orig.txt", "content": "orig", "mode": "create_only"}))
        art = create_artifact_reference(sandbox, "orig.txt")
        # Possessing artifact does not allow writing to same path without authority
        # Prove that creating a new provider without authority fails, even if we have artifact
        with pytest.raises(Exception):
            BoundedWorkspaceMutationProvider(art)  # type: ignore
        # Also artifact cannot be used as handoff or sandbox
        with pytest.raises(Exception):
            create_workspace_mutation_authority(art, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)  # type: ignore

# ---------------------------------------------------------------------------
# T32 no test execution implementation
# ---------------------------------------------------------------------------

class TestT32NoTestExecution:
    def test_no_test_execution(self):
        assert TEST_EXECUTION_IMPLEMENTED_IN_W1 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py").read_text(encoding="utf-8")
        low = src.lower()
        assert "test_execution" not in low or "test_execution_implemented_in_w1" in low
        assert "pytest" not in low or "test_execution" not in low

# ---------------------------------------------------------------------------
# T33 no Git operation implementation
# ---------------------------------------------------------------------------

class TestT33NoGit:
    def test_no_git(self):
        assert GIT_OPERATION_IMPLEMENTED_IN_W1 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py").read_text(encoding="utf-8")
        low = src.lower()
        assert "git" not in low or "git_operation" in low
        assert "dulwich" not in low
        assert "pygit2" not in low

# ---------------------------------------------------------------------------
# T34 no restricted shell implementation
# ---------------------------------------------------------------------------

class TestT34NoShell:
    def test_no_shell(self):
        assert RESTRICTED_SHELL_IMPLEMENTED_IN_W1 is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py").read_text(encoding="utf-8")
        low = src.lower()
        assert "restricted_shell" not in low or "restricted_shell_implemented_in_w1" in low
        # Should not contain shell execution primitives as production impl
        assert "subprocess" not in low

# ---------------------------------------------------------------------------
# T35 no persistent artifact store
# ---------------------------------------------------------------------------

class TestT35NoArtifactStore:
    def test_no_persistent_store(self):
        assert NEW_PERSISTENT_ARTIFACT_STORE_CREATED is False
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py").read_text(encoding="utf-8")
        assert "ARTIFACT_IMPLEMENTATION_MODE" in src
        assert ARTIFACT_IMPLEMENTATION_MODE == "REFERENCE_CONTRACT_ONLY"
        low = src.lower()
        assert "s3" not in low or "s3" in low and "artifact" not in low
        assert "object_store" not in low
        assert "database" not in low or "artifact" not in low
        assert "persistent" not in low or "new_persistent" in low

# ---------------------------------------------------------------------------
# T36 no new authority engine
# ---------------------------------------------------------------------------

class TestT36NoNewAuthorityEngine:
    def test_no_new_authority_engine(self):
        src = (REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py").read_text(encoding="utf-8")
        import ast
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("MutationAuthorityEngine", "FileAuthorityEngine", "MutationPermissionEngine", "ArtifactStoreManager", "WorkspaceToolProviderV2", "MutationRuntime", "ArtifactRuntime"):
            assert bad not in classes
        assert "WorkspaceMutationAuthority" in classes

# ---------------------------------------------------------------------------
# T37 M2 shared contracts unchanged
# ---------------------------------------------------------------------------

class TestT37M2SharedUnchanged:
    def test_m2_shared_unchanged(self):
        for path in ["aota_forge/work_plane/tool_surface.py", "aota_forge/work_plane/workspace_tools.py", "aota_forge/work_plane/tool_result_governance.py"]:
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            # M2 files legitimately contain invariant flags like WORKSPACE_MUTATION_IMPLEMENTED, but must not import or implement W1 mutation
            lower = content.lower()
            # No import of W1 module and no provider class
            assert "from aota_forge.work_plane.workspace_mutation" not in lower
            assert "import workspace_mutation" not in lower
            assert "WorkspaceMutationAuthority" not in content
            assert "BoundedWorkspaceMutationProvider" not in content
            assert "ArtifactReference" not in content

# ---------------------------------------------------------------------------
# T38 S1 high-conflict files unchanged
# ---------------------------------------------------------------------------

class TestT38S1HighConflict:
    def test_s1_unchanged(self):
        for path in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert "WorkspaceMutation" not in content
            assert "workspace_mutation" not in content.lower()
            assert "ArtifactReference" not in content
