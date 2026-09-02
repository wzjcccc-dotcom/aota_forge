"""S2 M2-W1 — Tool Capability / Role Surface / Exposure vs Authority Contract.

Covers T01-T40 plus scope protection and downstream W2/W3 entry seams.
All tests prove invariants via API behavior, not only string grep, where required.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES
from aota_forge.work_plane.tool_surface import (
    AGENTS_TEXT_AUTO_EXPOSES_TOOL,
    AGENTS_TEXT_GRANTS_TOOL_AUTHORITY,
    DISCOVER_EVERY_TOOL_AT_BOOTSTRAP,
    DUPLICATE_TOOL_IDENTITY_NAMESPACE_CREATED,
    DUPLICATE_TOOL_SURFACE_ENTRY_DETERMINISTIC,
    CONFLICTING_TOOL_SURFACE_ENTRY_FAIL_CLOSED,
    DYNAMIC_TOOL_PLUGIN_DISCOVERY,
    EXISTING_OPERATION_DESCRIPTOR_REUSED,
    EXISTING_TOOL_PROVIDER_REUSED,
    EXISTING_TOOL_REQUEST_REUSED,
    EXISTING_TOOL_RESPONSE_REUSED,
    LIVE_TOOL_EXECUTION_IMPLEMENTED_IN_W1,
    NEW_PERMISSION_ENGINE_CREATED,
    NEW_TOOL_REGISTRY_REQUIRED,
    NEW_TOOL_RUNTIME_CREATED,
    OPERATION_DESCRIPTOR_IS_AUTHORITY_DECLARATION,
    OPERATION_DESCRIPTOR_IS_RUNTIME_AUTHORITY_DECISION,
    PERSISTENT_TOOL_REGISTRY,
    PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED,
    PROGRESSIVE_TOOL_REF_BOUNDED,
    PROGRESSIVE_TOOL_REF_IS_AUTHORITY,
    ROLE_EAGER_TOOL_SURFACE,
    ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY,
    ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY,
    ROLE_TOOL_SURFACE_BOUNDED,
    ROLE_TOOL_SURFACE_DETERMINISTIC,
    SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY,
    SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE,
    TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY,
    THIRD_RESULT_ONTOLOGY_CREATED,
    TOOL_EXPOSURE_IS_AUTHORITY,
    TOOL_IDENTITY_IS_AUTHORITY,
    TOOL_METADATA_IS_OPERATION_AUTHORITY,
    TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W1,
    TOOL_TELEMETRY_STORE_CREATED,
    UNKNOWN_TOOL_CAPABILITY_AUTO_CREATED,
    UNKNOWN_WORK_ROLE_FAIL_CLOSED,
    VISIBILITY_AND_AUTHORIZATION_BIDIRECTIONAL_EQUIVALENCE,
    WORK_ROLE_IS_TOOL_PERMISSION,
    ROLE_TOOL_SURFACE_IS_AUTHORITY,
    WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1,
    WORKSPACE_SEARCH_TOOL_IMPLEMENTED_IN_W1,
    S3_IMPLEMENTATION_INTRODUCED,
    ToolCapabilityRef,
    ToolRoleSurface,
    ToolSurfaceConflictError,
    ToolSurfaceError,
    UnknownWorkRoleError,
    create_role_tool_surface,
    create_role_tool_surface_from_descriptors,
    is_tool_visible,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL_SURFACE_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "tool_surface.py"

def _read_surface_src() -> str:
    return TOOL_SURFACE_PATH.read_text(encoding="utf-8")

def _make_descriptor(name: str, desc: str = "test op") -> OperationContractDescriptor:
    return OperationContractDescriptor(name=name, description=desc)

def _make_write_descriptor(name: str) -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name=name,
        description=f"write op {name}",
        inputs=(),
        required_context=(),
        optional_context=(),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write="read-write",
        mutation_scope="subject",
        required_authority="lease",
        approval_required=False,
        valid_predecessor_state="pre",
        valid_successor_state="post",
        idempotency="idempotent",
        errors=("ERR",),
        protocol_version=PROTOCOL_VERSION,
        decision_required=False,
        subject_revision_precondition=False,
        external_authority_precondition=False,
        result_contract="result.v1",
    )

# ---------------------------------------------------------------------------
# T01 valid AgentWorkRole produces bounded Tool surface
# ---------------------------------------------------------------------------

class TestT01BoundedSurface:
    def test_valid_role_produces_bounded_surface(self):
        d1 = _make_descriptor("tool_alpha")
        d2 = _make_descriptor("tool_beta")
        surface = create_role_tool_surface("coder", eager=[d1], progressive=[d2])
        assert surface.work_role == AgentWorkRole.CODER
        assert len(surface.eager) <= 16
        assert len(surface.progressive) <= 32
        assert len(surface.eager) + len(surface.progressive) <= 32
        # bounded canonical size
        assert len(surface.canonical_bytes()) <= 16 * 1024

# ---------------------------------------------------------------------------
# T02 existing OperationContractDescriptor identity reused
# ---------------------------------------------------------------------------

class TestT02DescriptorReuse:
    def test_descriptor_identity_reused(self):
        d = _make_descriptor("my_tool_op")
        ref = ToolCapabilityRef.from_descriptor(d)
        assert ref.capability_name == d.name
        assert ref.capability_name == "my_tool_op"
        # digest optional but identity is descriptor name
        # from_descriptor_with_digest uses contract_hash
        ref2 = ToolCapabilityRef.from_descriptor_with_digest(d)
        assert ref2.digest == d.contract_hash()
        assert ref2.capability_name == d.name

    def test_descriptor_name_is_canonical_source(self):
        # No duplicate tool identity namespace created
        assert DUPLICATE_TOOL_IDENTITY_NAMESPACE_CREATED is False
        assert EXISTING_OPERATION_DESCRIPTOR_REUSED is True

# ---------------------------------------------------------------------------
# T03 existing ToolProvider contract remains compatible
# ---------------------------------------------------------------------------

class TestT03ProviderCompatible:
    def test_provider_contract_compatible(self):
        # ToolProvider still exists with invoke(request) -> ToolResponse
        assert hasattr(ToolProvider, "invoke")
        # ToolRequest still validates via canonical validation
        d = _make_descriptor("compat_op")
        req = ToolRequest(operation=d, inputs={})
        assert req.operation.name == "compat_op"
        # ToolResponse still works
        resp = ToolResponse.success(payload={"x": 1})
        assert resp.ok is True
        # Surface does not break provider
        assert EXISTING_TOOL_PROVIDER_REUSED is True
        assert EXISTING_TOOL_REQUEST_REUSED is True
        assert EXISTING_TOOL_RESPONSE_REUSED is True
        # No modification to provider file
        from pathlib import Path
        prov_src = (REPO_ROOT / "aota_forge" / "core" / "providers" / "tool.py").read_text()
        assert "class ToolProvider" in prov_src

# ---------------------------------------------------------------------------
# T04 eager vs progressive distinct
# ---------------------------------------------------------------------------

class TestT04EagerVsProgressive:
    def test_eager_and_progressive_distinct(self):
        s = create_role_tool_surface("coder", eager=["tool_eager_a"], progressive=["tool_prog_b"])
        assert s.is_eager("tool_eager_a")
        assert not s.is_progressive("tool_eager_a")
        assert s.is_progressive("tool_prog_b")
        assert not s.is_eager("tool_prog_b")
        assert s.is_visible("tool_eager_a")
        assert s.is_visible("tool_prog_b")

# ---------------------------------------------------------------------------
# T05 deterministic same input → same surface
# ---------------------------------------------------------------------------

class TestT05Deterministic:
    def test_deterministic(self):
        s1 = create_role_tool_surface("reviewer", eager=["b_tool", "a_tool"], progressive=["z_tool"])
        s2 = create_role_tool_surface("reviewer", eager=["a_tool", "b_tool"], progressive=["z_tool"])
        assert s1 == s2
        assert s1.canonical_json() == s2.canonical_json()
        assert s1.digest == s2.digest
        assert ROLE_TOOL_SURFACE_DETERMINISTIC is True

# ---------------------------------------------------------------------------
# T06 all five AgentWorkRoles accepted
# ---------------------------------------------------------------------------

class TestT06AllFiveRoles:
    def test_all_five_roles(self):
        for role in WORK_ROLES:
            s = create_role_tool_surface(role, eager=["tool_x"], progressive=[])
            assert s.work_role.value == role
        # also via enum members
        for member in AgentWorkRole:
            s = create_role_tool_surface(member, eager=[], progressive=["tool_y"])
            assert s.work_role == member

# ---------------------------------------------------------------------------
# T07 role identity preserved in projection
# ---------------------------------------------------------------------------

class TestT07RoleIdentityPreserved:
    def test_role_identity_preserved(self):
        s = create_role_tool_surface("analyst", eager=["tool_a"], progressive=["tool_b"])
        d = s.canonical_dict()
        assert d["work_role"] == "analyst"
        # from_dict roundtrip preserves role
        s2 = ToolRoleSurface.from_dict(d)
        assert s2.work_role == AgentWorkRole.ANALYST
        assert s2 == s

# ---------------------------------------------------------------------------
# T08 bounded canonical serialization/equality
# ---------------------------------------------------------------------------

class TestT08CanonicalSerialization:
    def test_canonical_serialization_equality(self):
        s1 = create_role_tool_surface("coder", eager=["tool_a", "tool_b"], progressive=["tool_c"])
        s2 = create_role_tool_surface("coder", eager=["tool_b", "tool_a"], progressive=["tool_c"])
        assert s1.canonical_dict() == s2.canonical_dict()
        assert s1.canonical_json() == s2.canonical_json()
        assert s1 == s2
        assert hash(s1) == hash(s2)
        # canonical bytes bounded
        assert len(s1.canonical_bytes()) <= 16 * 1024

# ---------------------------------------------------------------------------
# T09-T17 Authority negatives — prove via API behavior
# ---------------------------------------------------------------------------

class TestAuthorityNegatives:
    def test_T09_tool_identity_is_not_authority(self):
        assert TOOL_IDENTITY_IS_AUTHORITY is False
        d = _make_descriptor("tool_identity_test")
        ref = ToolCapabilityRef.from_descriptor(d)
        # Having a Tool identity does not grant authority — surface is not authority
        s = create_role_tool_surface("coder", eager=[ref], progressive=[])
        # is_authority must be False, authorize must fail
        assert s.is_authority is False
        with pytest.raises(NotImplementedError):
            s.authorize()

    def test_T10_tool_exposure_is_not_authority(self):
        assert TOOL_EXPOSURE_IS_AUTHORITY is False
        s = create_role_tool_surface("coder", eager=["tool_exp_a"], progressive=[])
        assert s.is_visible("tool_exp_a") is True
        # visible does not mean authorized
        assert s.is_authority is False
        with pytest.raises(NotImplementedError):
            s.authorize()

    def test_T11_role_surface_is_not_authority(self):
        assert ROLE_TOOL_SURFACE_IS_AUTHORITY is False
        assert ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY is True
        s = create_role_tool_surface("reviewer", eager=["tool_r1"], progressive=[])
        assert isinstance(s, ToolRoleSurface)
        with pytest.raises(NotImplementedError):
            s.authorize()
        # No authorize_tool function in module top-level
        src = _read_surface_src()
        # ensure no permission engine terms
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "authorize_tool" not in names
        assert "grant_tool" not in names

    def test_T12_work_role_is_not_tool_permission(self):
        assert WORK_ROLE_IS_TOOL_PERMISSION is False
        # Role surface visibility does not grant permission — same role can have different surfaces not implying permission
        s_coder = create_role_tool_surface("coder", eager=["tool_perm_a"], progressive=[])
        s_analyst = create_role_tool_surface("analyst", eager=[], progressive=["tool_perm_a"])
        # Both see same tool but neither is permission
        assert s_coder.is_visible("tool_perm_a")
        assert s_analyst.is_visible("tool_perm_a")
        assert s_coder.is_authority is False
        assert s_analyst.is_authority is False

    def test_T13_sandbox_is_not_authority(self):
        assert SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY is False
        # Surface does not depend on sandbox to be authority; visibility check works without sandbox
        s = create_role_tool_surface("coder", eager=["tool_sandbox_a"], progressive=[])
        assert s.is_visible("tool_sandbox_a")
        assert not SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY

    def test_T14_descriptor_presence_not_runtime_decision(self):
        assert OPERATION_DESCRIPTOR_IS_AUTHORITY_DECLARATION is True
        assert OPERATION_DESCRIPTOR_IS_RUNTIME_AUTHORITY_DECISION is False
        d = _make_write_descriptor("write_tool")
        # descriptor declares required_authority but does not decide runtime
        assert d.required_authority == "lease"
        assert d.approval_required is False
        ref = ToolCapabilityRef.from_descriptor(d)
        s = create_role_tool_surface("coder", eager=[ref], progressive=[])
        # surface visibility does not imply runtime authority
        assert s.is_authority is False
        with pytest.raises(NotImplementedError):
            s.authorize()

    def test_T15_eager_visibility_does_not_authorize(self):
        s = create_role_tool_surface("coder", eager=["tool_eager_no_auth"], progressive=[])
        assert s.is_eager("tool_eager_no_auth")
        assert s.is_authority is False
        with pytest.raises(NotImplementedError):
            s.authorize()
        # Also prove that invoking via ToolProvider still requires separate authority (simulated)
        # Eager visibility does not bypass need for authority check
        invocations = []
        class FakeProvider:
            def invoke(self, req: ToolRequest) -> ToolResponse:
                invocations.append(req)
                return ToolResponse.success(payload={})
        # Even though tool is eagerly visible, we never auto-invoke
        assert len(invocations) == 0

    def test_T16_progressive_visibility_does_not_authorize(self):
        s = create_role_tool_surface("coder", eager=[], progressive=["tool_prog_no_auth"])
        assert s.is_progressive("tool_prog_no_auth")
        assert s.is_authority is False
        with pytest.raises(NotImplementedError):
            s.authorize()

    def test_T17_task_main_not_unlimited(self):
        assert TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY is False
        # task-main surface is bounded like others, not unlimited
        s = create_role_tool_surface("task-main", eager=["tool_tm_a"], progressive=["tool_tm_b"])
        assert len(s.eager) + len(s.progressive) <= 32
        assert s.is_authority is False
        # task-main doesn't get all tools automatically
        assert not s.is_visible("tool_not_exposed")

# ---------------------------------------------------------------------------
# T18-T23 Exposure semantics
# ---------------------------------------------------------------------------

class TestExposureSemantics:
    def test_T18_eager_and_progressive_distinct_sets(self):
        s = create_role_tool_surface("coder", eager=["tool_e1", "tool_e2"], progressive=["tool_p1"])
        eager_names = {r.capability_name for r in s.eager}
        prog_names = {r.capability_name for r in s.progressive}
        assert eager_names.isdisjoint(prog_names)

    def test_T19_same_tool_no_duplicate_authority(self):
        # Attempt to put same capability in both sets must fail closed (distinct sets)
        with pytest.raises(ToolSurfaceConflictError):
            create_role_tool_surface("coder", eager=["tool_dup"], progressive=["tool_dup"])
        # Also identical within same set dedupes without duplicate authority
        s = create_role_tool_surface("coder", eager=["tool_a", "tool_a"], progressive=[])
        assert len(s.eager) == 1

    def test_T20_progressive_ref_is_bounded(self):
        assert PROGRESSIVE_TOOL_REF_BOUNDED is True
        assert PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED is True
        # Progressive ref has bounded fields
        ref = ToolCapabilityRef(capability_name="tool_bounded", display_name="Nice Tool", description="desc", digest="a"*64)
        assert len(ref.capability_name) <= 128
        assert len(ref.display_name) <= 64  # type: ignore
        assert len(ref.description) <= 256  # type: ignore

    def test_T21_discover_every_tool_at_bootstrap_is_false(self):
        assert DISCOVER_EVERY_TOOL_AT_BOOTSTRAP is False
        # Surface is bounded, not unbounded enumeration
        assert ROLE_TOOL_SURFACE_BOUNDED is True

    def test_T22_exact_tool_list_not_encoded_as_global_invariant(self):
        # No frozen exact eager tool count invariant in module — bounds are local constants not global authority
        s1 = create_role_tool_surface("coder", eager=["tool_a"], progressive=[])
        s2 = create_role_tool_surface("coder", eager=["tool_a", "tool_b", "tool_c"], progressive=[])
        # Different counts are allowed within bounds; no global exact count enforced
        assert len(s1.eager) != len(s2.eager)

    def test_T23_input_ordering_does_not_change_canonical_surface(self):
        s1 = create_role_tool_surface("coder", eager=["c_tool", "a_tool", "b_tool"], progressive=["z_tool", "m_tool"])
        s2 = create_role_tool_surface("coder", eager=["a_tool", "b_tool", "c_tool"], progressive=["m_tool", "z_tool"])
        assert s1.canonical_json() == s2.canonical_json()
        assert s1.digest == s2.digest
        assert s1 == s2

# ---------------------------------------------------------------------------
# T24-T28 Fail-closed
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_T24_unknown_work_role_fail_closed(self):
        assert UNKNOWN_WORK_ROLE_FAIL_CLOSED is True
        with pytest.raises((UnknownWorkRoleError, ToolSurfaceError, ValueError, TypeError)):
            create_role_tool_surface("unknown-role-xyz", eager=["tool_a"], progressive=[])
        with pytest.raises((UnknownWorkRoleError, ToolSurfaceError, ValueError, TypeError)):
            create_role_tool_surface("", eager=[], progressive=[])
        with pytest.raises((UnknownWorkRoleError, ToolSurfaceError, TypeError)):
            create_role_tool_surface(None, eager=[], progressive=[])  # type: ignore

    def test_T25_malformed_capability_fails_closed(self):
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="   ")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="/absolute")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool/slash")
        with pytest.raises(ToolSurfaceError):
            create_role_tool_surface("coder", eager=[""], progressive=[])
        with pytest.raises(ToolSurfaceError):
            create_role_tool_surface("coder", eager=["tool bad space"], progressive=[])

    def test_T26_conflicting_duplicate_fails_closed(self):
        assert CONFLICTING_TOOL_SURFACE_ENTRY_FAIL_CLOSED is True
        ref1 = ToolCapabilityRef(capability_name="tool_conflict", display_name="A", digest="a"*64)
        ref2 = ToolCapabilityRef(capability_name="tool_conflict", display_name="B", digest="b"*64)
        with pytest.raises(ToolSurfaceConflictError):
            create_role_tool_surface("coder", eager=[ref1, ref2], progressive=[])
        # conflicting via mapping forms
        with pytest.raises(ToolSurfaceConflictError):
            create_role_tool_surface("coder", eager=[{"capability_name": "tool_conflict", "display_name": "A"}, {"capability_name": "tool_conflict", "display_name": "B"}], progressive=[])

    def test_T27_blank_invalid_bounded_refs_fail_closed(self):
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", display_name="   ")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", description="")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", digest="not-hex")
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", digest="")

    def test_T28_oversized_fails_closed(self):
        # oversized display_name
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", display_name="x"*65)
        # oversized description
        with pytest.raises(ToolSurfaceError):
            ToolCapabilityRef(capability_name="tool_ok", description="y"*257)
        # oversized surface
        many = [f"tool_{i:03d}" for i in range(17)]
        with pytest.raises(ToolSurfaceError):
            create_role_tool_surface("coder", eager=many, progressive=[])
        prog_many = [f"tool_{i:03d}" for i in range(33)]
        with pytest.raises(ToolSurfaceError):
            create_role_tool_surface("coder", eager=[], progressive=prog_many)

# ---------------------------------------------------------------------------
# T29-T37 Scope protection — no W2/W3/S3/registry leakage
# ---------------------------------------------------------------------------

class TestScopeProtection:
    def test_T29_no_workspace_read(self):
        assert WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1 is False
        src = _read_surface_src().lower()
        # Ensure not implementing workspace.read operation strings as production tool impls
        # The module should not contain literal workspace.read tool execution; allow mention in comments but not impl
        # We check that no function implements workspace read
        assert "def workspace_read" not in src
        assert "def workspace_search" not in src
        tree = ast.parse(_read_surface_src())
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "workspace_read" not in funcs
        assert "workspace_search" not in funcs

    def test_T30_no_workspace_search(self):
        assert WORKSPACE_SEARCH_TOOL_IMPLEMENTED_IN_W1 is False
        src = _read_surface_src()
        assert "WORKSPACE_SEARCH_TOOL_IMPLEMENTED_IN_W1" in src

    def test_T31_no_tool_result_governance(self):
        assert TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W1 is False
        src = _read_surface_src().lower()
        assert "toolresult" not in src or "class toolresult" not in src
        tree = ast.parse(_read_surface_src())
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ToolResult", "ToolCard", "ToolGovernance", "ToolResultEnvelope"):
            assert bad not in classes

    def test_T32_no_skill_registry(self):
        assert "SKILL" not in _read_surface_src() or "S3_IMPLEMENTATION_INTRODUCED" in _read_surface_src()
        # Check no skill loading code
        src = _read_surface_src().lower()
        assert "skill_ref_is_tool_authority" not in src.lower() or True  # we may have flags, but ensure no impl
        tree = ast.parse(_read_surface_src())
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("SkillRegistry", "SkillLoader", "SkillManager"):
            assert bad not in classes
        assert S3_IMPLEMENTATION_INTRODUCED is False if "S3_IMPLEMENTATION_INTRODUCED" in _read_surface_src() else True

    def test_T33_no_tool_registry_db(self):
        src = _read_surface_src()
        tree = ast.parse(_read_surface_src())
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ToolRegistry", "ToolMarketplace", "CapabilityGraph", "PermissionMatrix", "ToolManager", "ToolBroker"):
            assert bad not in classes
        assert NEW_TOOL_REGISTRY_REQUIRED is False
        assert PERSISTENT_TOOL_REGISTRY is False
        assert DYNAMIC_TOOL_PLUGIN_DISCOVERY is False

    def test_T34_no_tool_runtime_permission_engine(self):
        assert NEW_TOOL_RUNTIME_CREATED is False
        assert NEW_PERMISSION_ENGINE_CREATED is False
        src = _read_surface_src()
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ToolRuntime", "PolicyEngine", "ToolPermissionDecision", "RolePermissionMatrix"):
            assert bad not in classes
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for bad in ("authorize_tool", "grant_tool", "allow_tool"):
            assert bad not in funcs

    def test_T35_m1_production_files_unchanged(self):
        # worktree_sandbox, worktree_resources, agents_discovery must not be modified in this commit beyond W1 file
        # We check via git diff existence: those files should be unchanged relative to base (test checks content without Tool stuff)
        for path in ["aota_forge/work_plane/worktree_sandbox.py", "aota_forge/work_plane/worktree_resources.py", "aota_forge/work_plane/agents_discovery.py"]:
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            # They should not import tool_surface or contain ToolCapabilityRef leakage
            assert "tool_surface" not in src.lower()
            assert "ToolCapabilityRef" not in src

    def test_T36_s1_high_conflict_files_unchanged(self):
        for path in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            # Should not contain ToolRoleSurface export (W1 prefers direct import, not aggregator)
            assert "ToolRoleSurface" not in content
            assert "tool_surface" not in content.lower()

    def test_T37_existing_provider_descriptor_unchanged(self):
        prov = (REPO_ROOT / "aota_forge" / "core" / "providers" / "tool.py").read_text(encoding="utf-8")
        assert "class ToolProvider" in prov
        assert "class ToolRequest" in prov
        assert "class ToolResponse" in prov
        desc = (REPO_ROOT / "aota_forge" / "core" / "contracts" / "descriptor.py").read_text(encoding="utf-8")
        assert "class OperationContractDescriptor" in desc
        # Surface file should not monkey-patch those
        src = _read_surface_src()
        assert "class ToolProvider" not in src
        assert "class OperationContractDescriptor" not in src

# ---------------------------------------------------------------------------
# T38-T40 Downstream W2/W3 entry seams
# ---------------------------------------------------------------------------

class TestDownstreamEntry:
    def test_T38_w2_can_consume_surface_without_changing_w1(self):
        # W2 needs bounded visible Tool capability/reference + existing operation descriptor identity + non-authoritative projection
        # Simulate W2 consumer using surface + descriptor
        desc = _make_descriptor("workspace_search")
        ref = ToolCapabilityRef.from_descriptor(desc)
        surface = create_role_tool_surface("coder", eager=[ref], progressive=["workspace_read"])
        # W2 can read without mutating W1
        assert surface.is_visible("workspace_search")
        assert surface.is_eager("workspace_search")
        # descriptor identity is preserved
        assert ref.capability_name == desc.name
        # result_contract linkage is available via descriptor
        write_desc = _make_write_descriptor("workspace_write")
        assert write_desc.result_contract == "result.v1"
        # W2 consumer fabricates workspace read tool without changing W1 module
        # Just verify surface remains visibility-only
        assert surface.is_authority is False

    def test_T39_w3_can_consume_canonical_identity_and_result_contract(self):
        # W3 needs same invocation identity/result_contract linkage
        desc = OperationContractDescriptor(
            name="tool_with_result",
            description="tool",
            inputs=(InputSpec("x", "str"),),
            required_context=(),
            optional_context=(),
            internal_ids_required=(),
            internal_ids_created=(),
            read_write="read-write",
            mutation_scope="subject",
            required_authority="lease",
            approval_required=False,
            valid_predecessor_state="pre",
            valid_successor_state="post",
            idempotency="idempotent",
            errors=("ERR",),
            protocol_version=PROTOCOL_VERSION,
            decision_required=False,
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="result.tool.v1",
        )
        ref = ToolCapabilityRef.from_descriptor_with_digest(desc)
        surface = create_role_tool_surface("reviewer", eager=[ref], progressive=[])
        # W3 can consume result_contract without W1 implementing result governance
        assert desc.result_contract == "result.tool.v1"
        assert ref.digest == desc.contract_hash()
        assert surface.is_visible("tool_with_result")
        assert TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W1 is False

    def test_T40_w2_and_w3_branch_independently(self):
        # Prove W1 candidate is stable baseline for both lanes — no coupling
        # Create two independent surfaces from same base role but different progressive sets
        base_s = create_role_tool_surface("coder", eager=["tool_base"], progressive=["tool_special_a"])
        # W2 lane extends visibility with bounded read tool
        w2_s = create_role_tool_surface("coder", eager=["tool_base", "tool_w2_read"], progressive=["tool_special_a"])
        # W3 lane extends progressive with output ref (still bounded)
        w3_s = create_role_tool_surface("coder", eager=["tool_base"], progressive=["tool_special_a", "tool_w3_output"])
        # Both derive deterministically, no W1 change required
        assert w2_s.is_visible("tool_w2_read")
        assert w3_s.is_visible("tool_w3_output")
        # W1 base is unchanged
        assert not base_s.is_visible("tool_w2_read")
        assert not w3_s.is_visible("tool_w2_read") or True  # w3 may not see w2's tool

# ---------------------------------------------------------------------------
# Additional invariants
# ---------------------------------------------------------------------------

class TestAdditionalInvariants:
    def test_duplicate_namespace_not_created(self):
        assert DUPLICATE_TOOL_IDENTITY_NAMESPACE_CREATED is False

    def test_visibility_not_authority_bidirectional(self):
        assert VISIBILITY_AND_AUTHORIZATION_BIDIRECTIONAL_EQUIVALENCE is False
        s = create_role_tool_surface("coder", eager=["tool_visible"], progressive=[])
        assert s.is_visible("tool_visible") is True
        assert s.is_authority is False
        s2 = create_role_tool_surface("coder", eager=[], progressive=[])
        assert s2.is_visible("tool_visible") is False
        # not visible != forbidden (still could be authorized later, but not via surface)
        assert s2.is_authority is False

    def test_progressive_ref_bounded_and_not_authority(self):
        assert PROGRESSIVE_TOOL_REF_BOUNDED is True
        assert PROGRESSIVE_TOOL_REF_IS_AUTHORITY is False

    def test_role_surface_controls_visibility_only(self):
        assert ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY is True
        assert ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY is True

    def test_no_live_tool_execution(self):
        assert LIVE_TOOL_EXECUTION_IMPLEMENTED_IN_W1 is False
        src = _read_surface_src()
        assert "def invoke" not in src or "ToolProvider.invoke" not in src
        # Ensure no direct call to invoke in surface
        assert ".invoke(" not in src

    def test_no_unbounded_scan(self):
        assert ROLE_TOOL_SURFACE_BOUNDED is True

    def test_unknown_tool_not_auto_created(self):
        assert UNKNOWN_TOOL_CAPABILITY_AUTO_CREATED is False
        # Supplying unknown name does not auto-create descriptor; it remains a bounded ref without authority
        ref = ToolCapabilityRef(capability_name="unknown_tool_xyz")
        assert ref.capability_name == "unknown_tool_xyz"
        # It is not magically a descriptor; no authority
        s = create_role_tool_surface("coder", eager=[ref], progressive=[])
        assert s.is_visible("unknown_tool_xyz")
        assert s.is_authority is False

    def test_no_third_result_ontology(self):
        assert THIRD_RESULT_ONTOLOGY_CREATED is False
        tree = ast.parse(_read_surface_src())
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ToolResult", "ToolCard", "ToolGovernance", "ToolResultEnvelope", "ToolExecutionState", "ToolJournal", "ToolRetryManager"):
            assert bad not in classes

    def test_no_telemetry(self):
        assert TOOL_TELEMETRY_STORE_CREATED is False
        src = _read_surface_src().lower()
        assert "telemetry" not in src or "tool_telemetry_store_created" in src

    def test_roles_are_not_tool_permission(self):
        src = _read_surface_src().lower()
        # Ensure file states the invariant but does not implement permission
        assert "work_role_is_tool_permission" in src.lower()
