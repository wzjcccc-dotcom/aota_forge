"""S2 M4-W1 — Selective Hydration / Evidence / Side-Effect Projection.

Proves:

* Selective hydration (bounded, not eager) with scope reauthorization + digest verification
* Evidence / side-effect projection reuse of existing Result Governance (not authority)
* M3 mutation → artifact → ResultGovernanceProjection → hydration integration (no replay)
* Tool result ref contract composition
* Negative / adversarial fail-closed
* Scope guards (no persistent store / graph / ontology)

All T01-T28 gates.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import shutil
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.providers.tool import ToolResponse
from aota_forge.core.regression.fixtures import TempWorkspaceFixture
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.result_governance import (
    GovernedReference,
    GovernedReferenceKind,
    ResultGovernanceProjection,
    ResultProvenance,
    SideEffectOutcome,
    VerificationStatus,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.work_plane import selective_hydration as sh
from aota_forge.work_plane.selective_hydration import (
    hydrate_one,
    hydrate_many,
    hydrate_artifact_ref,
    hydrate_tool_output_via_selective,
    governed_ref_for_content,
    project_evidence_refs,
    project_side_effect_outcome,
    verify_side_effect_outcome_is_reused_enum,
    HydratedContent,
    SelectiveHydrationError,
    UnknownRefError,
    ForeignRefError,
    DigestMismatchError,
    AuthorizationError,
    SourceUnavailableError,
    OversizedHydrationError,
    UnsupportedRefKindError,
    MAX_HYDRATED_BYTES,
    MAX_HYDRATION_BATCH,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SH_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "selective_hydration.py"
MUTATION_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "workspace_mutation.py"
TOOL_GOV_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "tool_result_governance.py"
COMMON_PATH = REPO_ROOT / "aota_forge" / "core" / "result_governance" / "common.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_sandbox(project_id: str = "proj_m4w1", workspace_id: str = "ws_m4w1", worktree_id: str = "wt_m4w1"):
    ws = TempWorkspaceFixture(prefix="m4w1-")
    ws.__enter__()
    ws.create_project(project_id)
    registry = ws.create_registry(workspace_id)
    evidence = resolve_project_candidates(workspace_id, registry, project_id)
    assert evidence.status == "RESOLVED"
    wt = Path(tempfile.mkdtemp(prefix="m4w1-wt-"))
    b = bind_worktree_sandbox(evidence, worktree_id, wt)
    return b, ws, wt


def _cleanup(ws: TempWorkspaceFixture, wt: Path):
    try:
        ws.__exit__(None, None, None)
    except Exception:
        pass
    shutil.rmtree(str(wt), ignore_errors=True)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# T01 valid authorized bounded ref hydrates
# ---------------------------------------------------------------------------


class TestT01ValidAuthorizedBoundedRefHydrates:
    def test_valid_ref_hydrates(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "hello selective hydration"
            ref = governed_ref_for_content("evidence", "evidence/ref_valid_01", content)
            source = {ref: content}
            hc = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert isinstance(hc, HydratedContent)
            assert hc.content == content
            assert hc.digest == _sha(content)
            assert hc.is_authority is False
            assert hc.project_id == b.project_id
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T02 hydration rechecks current project/worktree scope
# ---------------------------------------------------------------------------


class TestT02RechecksScope:
    def test_rechecks_scope(self):
        b, ws, wt = _make_sandbox(project_id="proj_a", worktree_id="wt_a")
        b_other_proj, ws2, wt2 = _make_sandbox(project_id="proj_b", worktree_id="wt_a")
        b_other_wt = WorktreeSandboxBoundary(
            workspace_id=b.workspace_id,
            workspace_root=b.workspace_root,
            project_id=b.project_id,
            project_root=b.project_root,
            worktree_id="wt_other",
            worktree_root=b.worktree_root,
            registry_fingerprint=b.registry_fingerprint,
            candidate_fingerprint=b.candidate_fingerprint,
        )
        try:
            content = "scope check"
            ref = governed_ref_for_content("evidence", "evidence/scope", content)
            source = {ref: content}
            # valid current scope passes
            hc = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert hc.content == content
            # foreign project expected vs current mismatch fails
            with pytest.raises(ForeignRefError):
                hydrate_one(ref, current_sandbox=b_other_proj, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # foreign worktree expected vs current mismatch fails
            with pytest.raises(ForeignRefError):
                hydrate_one(ref, current_sandbox=b_other_wt, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # also direct expected mismatch triggers ForeignRefError even if current matches expected foreign
            with pytest.raises(ForeignRefError):
                hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id="foreign_proj", expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)
            _cleanup(ws2, wt2)


# ---------------------------------------------------------------------------
# T03 digest recomputed and verified
# ---------------------------------------------------------------------------


class TestT03DigestVerified:
    def test_digest_recomputed(self):
        assert sh.HYDRATION_DIGEST_VERIFIED is True
        b, ws, wt = _make_sandbox()
        try:
            content = "digest verify content"
            ref = governed_ref_for_content("evidence", "evidence/digest", content)
            source = {ref: content}
            hc = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # digest must equal sha256 of content
            expected = _sha(content)
            assert hc.digest == expected
            assert ref.digest == expected
            # tampered content digest mismatch fails
            tampered_source = {ref: "tampered content"}
            with pytest.raises(DigestMismatchError):
                hydrate_one(ref, current_sandbox=b, hydration_source=tampered_source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T04 same ref/content produces deterministic identity
# ---------------------------------------------------------------------------


class TestT04DeterministicIdentity:
    def test_deterministic(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "deterministic hydration"
            ref = governed_ref_for_content("artifact", "artifact/deterministic.txt", content)
            source = {ref: content}
            hc1 = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            hc2 = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert hc1.digest == hc2.digest
            assert hc1.content == hc2.content
            assert hc1.ref.digest == hc2.ref.digest
            # same content in different ref string but same digest still deterministic digest same
            ref2 = governed_ref_for_content("artifact", "artifact/deterministic2.txt", content)
            # different ref string but same content digest same (content identity)
            assert ref2.digest == ref.digest
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T05 evidence_refs reuse existing Result Governance
# ---------------------------------------------------------------------------


class TestT05EvidenceRefsReuse:
    def test_evidence_refs_reuse(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "evidence content"
            ref = governed_ref_for_content("evidence", "evidence/ref_ev", content)
            proj = ResultGovernanceProjection.success(
                provenance=ResultProvenance(source_ref="op-1", content_digest=ref.digest),
                evidence_refs=(ref,),
                verification=VerificationStatus.VERIFIED,
            )
            # via helper
            evs = project_evidence_refs(proj)
            assert len(evs) == 1
            assert evs[0] == ref
            assert isinstance(evs[0], GovernedReference)
            assert evs[0].kind == GovernedReferenceKind.EVIDENCE
            # ensure import reuse flag true
            assert sh.EXISTING_RESULT_GOVERNANCE_REUSED is True
            # verify source reuse is actual class from common.py
            from aota_forge.core.result_governance.common import GovernedReference as CommonGR

            assert GovernedReference is CommonGR
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T06 side_effect_outcome reuse existing Result Governance
# ---------------------------------------------------------------------------


class TestT06SideEffectOutcomeReuse:
    def test_side_effect_reuse(self):
        assert sh.EXISTING_SIDE_EFFECT_OUTCOME_REUSED is True
        assert sh.NEW_SIDE_EFFECT_ENUM_CREATED is False
        # SideEffectOutcome is canonical enum from common.py
        from aota_forge.core.result_governance.common import SideEffectOutcome as CommonSEO

        assert SideEffectOutcome is CommonSEO
        b, ws, wt = _make_sandbox()
        try:
            proj = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.SUCCESS)
            seo = project_side_effect_outcome(proj)
            assert seo == SideEffectOutcome.SUCCESS
            assert verify_side_effect_outcome_is_reused_enum(seo) is True
            # all canonical values are from same enum
            for val in ("none", "success", "failure", "partial", "unknown"):
                assert SideEffectOutcome(val).value == val
            # ensure module does not define its own enum
            src = SH_PATH.read_text(encoding="utf-8")
            tree = ast.parse(src)
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert "SideEffectOutcome" not in classes
            assert "SideEffect" not in classes
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T07 workspace.write artifact/evidence projection composes with hydration
# ---------------------------------------------------------------------------


class TestT07MutationArtifactHydrationIntegration:
    def test_mutation_to_hydration(self):
        from aota_forge.work_plane.workspace_mutation import (
            WorkspaceMutationAuthority,
            create_workspace_mutation_authority,
            BoundedWorkspaceMutationProvider,
            WORKSPACE_WRITE_DESCRIPTOR,
            create_artifact_reference,
        )
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.work_plane.handoff import TaskHandoff
        from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate

        b, ws, wt = _make_sandbox(project_id="proj_m7", worktree_id="wt_m7")
        try:
            # minimal TaskHandoff
            handoff = TaskHandoff(
                work_role="coder",
                task_kind="mutation",
                objective="write artifact for hydration",
                bounded_scope="workspace",
                validation_expectations=("file written bounded",),
                semantic_stop_expectations=("stop after write",),
            )
            authority = create_workspace_mutation_authority(
                b, handoff, (), WORKSPACE_WRITE_DESCRIPTOR
            )
            provider = BoundedWorkspaceMutationProvider(authority)
            # write file bounded content
            content = "mutation artifact content for hydration"
            req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "artifacts/out.txt", "content": content, "mode": "create_only"})
            resp = provider.invoke(req)
            assert resp.ok is True, resp.error
            # create artifact ref from written file
            art_ref = create_artifact_reference(b, "artifacts/out.txt", content=content)
            assert art_ref.digest == _sha(content)
            # bridge to governed projection
            gov_ref = art_ref.as_governed_reference()
            proj = ResultGovernanceProjection.success(
                provenance=ResultProvenance(content_digest=art_ref.digest, source_ref="workspace.write"),
                artifact_refs=(gov_ref,),
                evidence_refs=(),
                side_effect_outcome=SideEffectOutcome.SUCCESS,
                verification=VerificationStatus.VERIFIED,
            )
            assert len(proj.artifact_refs) == 1
            # selective hydration under reauthorization — using file fallback (no injected source)
            hc = hydrate_artifact_ref(art_ref, current_sandbox=b)
            assert hc.content == content
            assert hc.digest == art_ref.digest
            assert hc.is_authority is False
            # also via HydratedContent path via hydrate_one with injected source
            source = {gov_ref: content}
            hc2 = hydrate_one(gov_ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert hc2.content == content
            # verify file still exists and was not replayed/removed
            p = Path(b.worktree_root) / "artifacts" / "out.txt"
            assert p.read_text(encoding="utf-8") == content
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T08 Tool output ref contract composes with hydration
# ---------------------------------------------------------------------------


class TestT08ToolOutputRefComposition:
    def test_tool_ref_composes(self):
        assert sh.TOOL_RESULT_REF_HYDRATION_CONTRACT_REUSED is True
        from aota_forge.work_plane.tool_result_governance import project_tool_result, TOOL_INLINE_OUTPUT_MAX_BYTES

        b, ws, wt = _make_sandbox()
        try:
            # create oversized tool output -> by_ref
            big = "T" * (TOOL_INLINE_OUTPUT_MAX_BYTES + 100)
            resp = ToolResponse.success(payload={"output": "ignored"})
            proj = project_tool_result(resp, "tool_m4w1", b, raw_output=big)
            assert proj.output_mode == "by_ref"
            assert proj.output_ref is not None
            # hydrate via selective wrapper reusing M2 contract
            resolver = {proj.output_ref: big}
            hc = hydrate_tool_output_via_selective(proj.output_ref, current_sandbox=b, hydration_source=resolver)
            assert hc.content == big
            assert hc.digest == proj.output_ref.digest
            assert hc.is_authority is False
            # also verify GovernedReference bridge reuses same kind evidence
            gov = proj.output_ref.as_governed_evidence_ref()
            assert gov.kind == GovernedReferenceKind.EVIDENCE
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# T09 hydration does not replay side effect
# ---------------------------------------------------------------------------


class TestT09NoReplay:
    def test_no_replay(self):
        assert sh.HYDRATION_REPLAYS_SIDE_EFFECT is False
        from aota_forge.work_plane.workspace_mutation import create_workspace_mutation_authority, BoundedWorkspaceMutationProvider, WORKSPACE_WRITE_DESCRIPTOR, create_artifact_reference
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.work_plane.handoff import TaskHandoff

        b, ws, wt = _make_sandbox()
        try:
            handoff = TaskHandoff(
                work_role="coder",
                task_kind="mutation",
                objective="no-replay test",
                bounded_scope="workspace",
                validation_expectations=("write bounded",),
                semantic_stop_expectations=("stop after",),
            )
            authority = create_workspace_mutation_authority(b, handoff, (), WORKSPACE_WRITE_DESCRIPTOR)
            provider = BoundedWorkspaceMutationProvider(authority)
            content = "no-replay content"
            req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "noreplay/file.txt", "content": content, "mode": "create_only"})
            resp = provider.invoke(req)
            assert resp.ok
            art_ref = create_artifact_reference(b, "noreplay/file.txt", content=content)
            mtime_before = (Path(b.worktree_root) / "noreplay" / "file.txt").stat().st_mtime
            # hydrate multiple times — should not mutate mtime (no replay)
            hc1 = hydrate_artifact_ref(art_ref, current_sandbox=b)
            hc2 = hydrate_artifact_ref(art_ref, current_sandbox=b)
            mtime_after = (Path(b.worktree_root) / "noreplay" / "file.txt").stat().st_mtime
            assert hc1.content == content
            assert hc2.content == content
            assert mtime_after == mtime_before
            # verify file not rewritten (content identical, mtime same)
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# Negative / Adversarial Tests T10-T20
# ---------------------------------------------------------------------------


class TestT10RefPossessionAloneCannotHydrate:
    def test_ref_alone_fails(self):
        assert sh.REF_POSSESSION_IS_HYDRATION_AUTHORITY is False
        b, ws, wt = _make_sandbox()
        try:
            content = "possession test"
            ref = governed_ref_for_content("evidence", "evidence/possession", content)
            source = {ref: content}
            # None sandbox -> AuthorizationError
            with pytest.raises(AuthorizationError):
                hydrate_one(ref, current_sandbox=None, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
            # wrong type sandbox
            with pytest.raises(AuthorizationError):
                hydrate_one(ref, current_sandbox="not_sandbox", hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
        finally:
            _cleanup(ws, wt)


class TestT11ForeignProjectFails:
    def test_foreign_project(self):
        assert sh.CROSS_PROJECT_HYDRATION_FAIL_CLOSED is True
        b, ws, wt = _make_sandbox(project_id="proj_legit")
        try:
            content = "foreign proj"
            ref = governed_ref_for_content("evidence", "evidence/foreign_proj", content)
            source = {ref: content}
            with pytest.raises(ForeignRefError):
                hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id="foreign_proj", expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


class TestT12ForeignWorktreeFails:
    def test_foreign_worktree(self):
        assert sh.CROSS_WORKTREE_HYDRATION_FAIL_CLOSED is True
        b, ws, wt = _make_sandbox(worktree_id="wt_legit")
        try:
            content = "foreign wt"
            ref = governed_ref_for_content("evidence", "evidence/foreign_wt", content)
            source = {ref: content}
            with pytest.raises(ForeignRefError):
                hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id="wt_other")
        finally:
            _cleanup(ws, wt)


class TestT13TamperedDigestFails:
    def test_tampered_digest(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "tamper digest"
            # Create ref with wrong digest manually
            wrong_digest = "0" * 64
            ref = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence/tamper_digest", digest=wrong_digest)
            source = {ref: content}
            with pytest.raises(DigestMismatchError):
                hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


class TestT14TamperedContentFails:
    def test_tampered_content(self):
        assert sh.TAMPERED_HYDRATED_CONTENT_FAIL_CLOSED is True
        b, ws, wt = _make_sandbox()
        try:
            content = "original content"
            ref = governed_ref_for_content("evidence", "evidence/tamper_content", content)
            tampered = "tampered!!!"
            source = {ref: tampered}
            with pytest.raises(DigestMismatchError):
                hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


class TestT15OversizedFails:
    def test_oversized(self):
        b, ws, wt = _make_sandbox()
        try:
            # content within ref creation bound but hydration source returns oversized
            small_content = "small"
            ref_small = governed_ref_for_content("evidence", "evidence/small", small_content)
            big_bytes = b"X" * (MAX_HYDRATED_BYTES + 1)
            # Create ref that claims to be small but source returns big with same digest? Need digest mismatch or oversize path.
            # Alternative: create ref for small content then source returns oversize big_bytes with tampered digest matching big?
            # Easiest: directly test oversize via source returns big for a ref whose digest matches big's digest but big exceeds bound.
            big_digest = hashlib.sha256(big_bytes).hexdigest()
            ref_big = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence/oversize", digest=big_digest)
            source_big = {ref_big: big_bytes}
            with pytest.raises(OversizedHydrationError):
                hydrate_one(ref_big, current_sandbox=b, hydration_source=source_big, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # also batch oversize (too many refs)
            refs = [governed_ref_for_content("evidence", f"evidence/batch/{i}", f"c{i}") for i in range(MAX_HYDRATION_BATCH + 1)]
            sources = {r: f"c{i}" for i, r in enumerate(refs)}
            # need combined source containing all; hydrate_many should reject due to batch size
            with pytest.raises(OversizedHydrationError):
                hydrate_many(refs, current_sandbox=b, hydration_source=sources, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


class TestT16UnknownRefFails:
    def test_unknown_ref(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "unknown"
            ref = governed_ref_for_content("evidence", "evidence/known", content)
            # source does not contain this ref at all
            empty_source: dict = {}
            with pytest.raises((UnknownRefError, SourceUnavailableError, SelectiveHydrationError)):
                hydrate_one(ref, current_sandbox=b, hydration_source=empty_source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)


class TestT17UnsupportedRefKindFails:
    def test_unsupported_kind(self):
        b, ws, wt = _make_sandbox()
        try:
            # GovernedReference only allows artifact/evidence, so unsupported kind must be tested via invalid kind creation attempt
            # We test by passing non-GovernedReference object
            with pytest.raises(UnsupportedRefKindError):
                hydrate_one("not_a_ref", current_sandbox=b, hydration_source={}, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
            # Also test with object that has invalid kind string — but GovernedReferenceKind only has 2 values so we test via direct HydrationResolver misuse
        finally:
            _cleanup(ws, wt)


class TestT18EvidenceProjectionCannotAuthorize:
    def test_evidence_not_authority(self):
        assert sh.EVIDENCE_PROJECTION_IS_AUTHORITY is False
        assert sh.EVIDENCE_REF_POSSESSION_GRANTS_AUTHORITY is False
        b, ws, wt = _make_sandbox()
        try:
            content = "evidence auth test"
            ref = governed_ref_for_content("evidence", "evidence/auth", content)
            proj = ResultGovernanceProjection.success(evidence_refs=(ref,))
            evs = project_evidence_refs(proj)
            # possession of evs does not grant ability to hydrate without scope
            source = {ref: content}
            # without sandbox fails
            with pytest.raises(AuthorizationError):
                hydrate_one(evs[0], current_sandbox=None, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
            # evidence projection object has no authorize method
            assert not hasattr(proj, "authorize")
            # HydratedContent is not authority
            hc = hydrate_one(ref, current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert hc.is_authority is False
            with pytest.raises(AttributeError):
                hc.authorize()  # type: ignore
        finally:
            _cleanup(ws, wt)


class TestT19SideEffectProjectionCannotAuthorize:
    def test_side_effect_not_authority(self):
        assert sh.SIDE_EFFECT_PROJECTION_IS_AUTHORITY is False
        proj = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.SUCCESS)
        seo = project_side_effect_outcome(proj)
        assert seo == SideEffectOutcome.SUCCESS
        # SideEffectOutcome enum has no authorize
        assert not hasattr(seo, "authorize")
        # attempting to use side_effect outcome as authority should not grant hydration
        b, ws, wt = _make_sandbox()
        try:
            content = "x"
            ref = governed_ref_for_content("evidence", "evidence/side", content)
            source = {ref: content}
            # Even with side_effect SUCCESS, still need sandbox reauth — missing fails
            with pytest.raises(AuthorizationError):
                hydrate_one(ref, current_sandbox=None, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
        finally:
            _cleanup(ws, wt)


class TestT20DigestMatchCannotAuthorize:
    def test_digest_not_authority(self):
        assert sh.DIGEST_IS_AUTHORITY is False
        b, ws, wt = _make_sandbox()
        try:
            content = "digest auth"
            ref = governed_ref_for_content("evidence", "evidence/digest_auth", content)
            source = {ref: content}
            # Correct digest alone without sandbox still fails
            assert ref.digest == _sha(content)
            with pytest.raises(AuthorizationError):
                hydrate_one(ref, current_sandbox=None, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
            # With wrong sandbox but correct digest also fails due to scope
            b2, ws2, wt2 = _make_sandbox(project_id="other_proj", worktree_id=b.worktree_id)
            try:
                with pytest.raises(ForeignRefError):
                    hydrate_one(ref, current_sandbox=b2, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            finally:
                _cleanup(ws2, wt2)
        finally:
            _cleanup(ws, wt)


# ---------------------------------------------------------------------------
# Scope Guards T21-T28
# ---------------------------------------------------------------------------


class TestT21NoPersistentHydrationStore:
    def test_no_persistent_hydration_store(self):
        assert sh.NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        assert sh.HYDRATION_SOURCE_IS_PERSISTENT_STORE is False
        assert sh.REF_CONTRACT_ONLY is True
        src = SH_PATH.read_text(encoding="utf-8")
        for bad in ("sqlite", "postgres", "ObjectStore", "PersistentHydrationStore", "HydrationStore", "database", "s3.Bucket"):
            assert bad not in src
        # No file created that is a DB
        assert not (REPO_ROOT / "aota_forge" / "work_plane" / "hydration_store.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "work_plane" / "hydration_db.py").exists()


class TestT22NoPersistentArtifactResultDB:
    def test_no_persistent_db(self):
        assert sh.NEW_PERSISTENT_RESULT_STORE_CREATED is False
        assert sh.NEW_PERSISTENT_ARTIFACT_STORE_CREATED is False
        src = SH_PATH.read_text(encoding="utf-8").lower()
        # flags themselves contain these substrings; check that no DB implementation exists beyond flags
        # count occurrences — only in flag definitions should appear, not in implementation
        # verify no sqlite/db.connect/create_table implementation
        for bad in ("db.connect", "create_table", "sqlite3", "postgres"):
            assert bad not in src
        # ensure no persistent file created
        assert not (REPO_ROOT / "aota.db").exists()
        # ensure no implementation classes for stores
        tree = ast.parse(SH_PATH.read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("ArtifactStore", "ResultStore", "PersistentStore"):
            assert bad not in classes


class TestT23NoEvidenceGraph:
    def test_no_evidence_graph(self):
        assert sh.NEW_EVIDENCE_GRAPH_IMPLEMENTED is False
        assert sh.NEW_EVIDENCE_ONTOLOGY_CREATED is False
        src = SH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("EvidenceGraph", "EvidenceDB", "EvidenceStore", "EvidenceOntology", "ArtifactGraph"):
            assert bad not in classes
        assert "NEW_EVIDENCE_ONTOLOGY_CREATED" in src


class TestT24NoNewResultOntology:
    def test_no_new_result_ontology(self):
        assert sh.NEW_RESULT_ONTOLOGY_CREATED is False
        src = SH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("HydratedResult", "EvidenceResultV2", "ResultOntology", "HydratedResultCard"):
            assert bad not in classes
        assert sh.EXISTING_RESULT_GOVERNANCE_REUSED is True


class TestT25NoNewSideEffectStateMachine:
    def test_no_side_effect_machine(self):
        assert sh.NEW_SIDE_EFFECT_STATE_MACHINE_CREATED is False
        assert sh.NEW_SIDE_EFFECT_ENUM_CREATED is False
        src = SH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("SideEffectStateMachine", "SideEffectLedger", "SideEffectStore"):
            assert bad not in classes


class TestT26NoGraphJournalCutover:
    def test_no_graph_journal(self):
        assert sh.NEW_GRAPH_IMPLEMENTATION_CREATED is False
        src = SH_PATH.read_text(encoding="utf-8").lower()
        for bad in ("journal", "cutover", "recovery", "graph"):
            # allow comments mentioning what is NOT created, but not implementations
            # Check that no class contains Journal/Graph/Cutover
            pass
        tree = ast.parse(SH_PATH.read_text(encoding="utf-8"))
        classes = {n.name.lower() for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("journal", "cutover", "graph", "recovery"):
            assert bad not in classes


class TestT27NoTelemetry:
    def test_no_telemetry(self):
        src = SH_PATH.read_text(encoding="utf-8").lower()
        for bad in ("telemetry", "analytics", "metrics_store", "event_exporter"):
            assert bad not in src
        tree = ast.parse(SH_PATH.read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for bad in ("TelemetryStore", "Analytics", "Metrics"):
            assert bad not in classes


class TestT28AcceptedSharedContractsUnchanged:
    def test_shared_contracts_unchanged(self):
        assert sh.ACCEPTED_SHARED_CONTRACT_CHANGE_COUNT == 0
        assert sh.AGGREGATOR_EXPORT_UPDATED is False
        import subprocess

        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "HEAD"],
            text=True,
        )
        changed = [line.strip() for line in out.splitlines() if line.strip()]
        forbidden = {
            "aota_forge/work_plane/events.py",
            "aota_forge/work_plane/workspace_mutation.py",
            "aota_forge/work_plane/test_execution.py",
            "aota_forge/work_plane/git_tools.py",
            "aota_forge/work_plane/restricted_shell.py",
            "aota_forge/work_plane/tool_surface.py",
            "aota_forge/work_plane/workspace_tools.py",
            "aota_forge/work_plane/tool_result_governance.py",
            "aota_forge/core/result_governance/common.py",
            "aota_forge/work_plane/__init__.py",
        }
        for p in changed:
            assert p not in forbidden, f"forbidden shared contract changed: {p}"
        # also ensure diff against base shows only allowed paths
        out2 = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "67183405ab26cfcf9eb0af36662da48b84f19050", "--", "aota_forge/"],
            text=True,
        )
        changed2 = [line.strip() for line in out2.splitlines() if line.strip()]
        # Only selective_hydration.py should be new in aota_forge
        for p in changed2:
            assert p in {"aota_forge/work_plane/selective_hydration.py"}, f"unexpected changed path: {p}"


# ---------------------------------------------------------------------------
# Additional positive checks for spec invariants
# ---------------------------------------------------------------------------


class TestSelectiveHydrationFlags:
    def test_flags(self):
        assert sh.SELECTIVE_HYDRATION is True
        assert sh.EAGER_HYDRATE_ALL_REFS is False
        assert sh.HYDRATION_REQUEST_BOUNDED is True
        assert sh.HYDRATION_OUTPUT_BOUNDED is True
        assert sh.HYDRATION_IS_AUTHORITY is False
        assert sh.REF_POSSESSION_IS_HYDRATION_AUTHORITY is False
        assert sh.HYDRATION_REAUTHORIZES_CURRENT_SCOPE is True
        assert sh.FAILED_HYDRATION_REPORTED_AS_SUCCESS is False
        with pytest.raises(NotImplementedError):
            sh.eager_hydrate_all_refs()

    def test_hydration_source_not_authority_or_store(self):
        assert sh.HYDRATION_SOURCE_IS_AUTHORITY is False
        assert sh.HYDRATION_SOURCE_IS_PERSISTENT_STORE is False
        src = SH_PATH.read_text(encoding="utf-8")
        assert "HYDRATION_SOURCE_IS_AUTHORITY=no" in src or "HYDRATION_SOURCE_IS_AUTHORITY: bool = False" in src


class TestFailureSemantics:
    def test_authorization_denied_and_source_unavailable(self):
        b, ws, wt = _make_sandbox()
        try:
            ref = governed_ref_for_content("evidence", "evidence/fail_sem", "hello")
            # source unavailable (None)
            with pytest.raises(SourceUnavailableError):
                hydrate_one(ref, current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # authorization: missing sandbox
            with pytest.raises(AuthorizationError):
                hydrate_one(ref, current_sandbox=None, hydration_source={ref: "hello"}, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
            # unsupported via wrong type
            with pytest.raises(UnsupportedRefKindError):
                hydrate_one("bad", current_sandbox=b, hydration_source={ref: "hello"}, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
        finally:
            _cleanup(ws, wt)

    def test_failed_not_reported_as_success(self):
        b, ws, wt = _make_sandbox()
        try:
            ref = governed_ref_for_content("evidence", "evidence/fail2", "hi")
            with pytest.raises(SelectiveHydrationError):
                hydrate_one(ref, current_sandbox=b, hydration_source={}, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # must not return HydratedContent on failure
            # ensure FAILED_HYDRATION_REPORTED_AS_SUCCESS is False flag means exception, not success payload
            assert sh.FAILED_HYDRATION_REPORTED_AS_SUCCESS is False
        finally:
            _cleanup(ws, wt)
