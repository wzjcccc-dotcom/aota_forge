"""S5/M1/W3 — Artifact / Evidence / Verification & Side-Effect Outcome Contract."""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import ForgeError, ProjectNotFoundError, error_from_dict
from aota_forge.core.result_governance import (
    RESULT_GOVERNANCE_VERSION,
    GovernedReference,
    GovernedReferenceKind,
    ResultCompleteness,
    ResultGovernanceProjection,
    ResultOutcome,
    ResultProvenance,
    SideEffectOutcome,
    VerificationStatus,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "aota_forge" / "core"
RG_ROOT = CORE_ROOT / "result_governance"


def _rg_symbols() -> set[str]:
    symbols: set[str] = set()
    for py in RG_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add(node.name)
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        symbols.add(t.id)
    return symbols


# ---------------------------------------------------------------------------
# GovernedReference artifact / evidence construction
# ---------------------------------------------------------------------------


def test_governed_reference_artifact_construction():
    ref = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:opaque:123")
    assert ref.kind == GovernedReferenceKind.ARTIFACT
    assert ref.ref == "artifact:opaque:123"
    assert ref.digest is None
    d = ref.to_dict()
    assert d["kind"] == "artifact"
    assert d["ref"] == "artifact:opaque:123"
    # round-trip
    ref2 = GovernedReference.from_dict(d)
    assert ref2 == ref
    # frozen
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        ref.ref = "mutated"  # type: ignore[misc]


def test_governed_reference_evidence_construction():
    ref = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:opaque:xyz", digest="sha256:abc")
    assert ref.kind == GovernedReferenceKind.EVIDENCE
    assert ref.digest == "sha256:abc"
    d = ref.to_dict()
    assert d["digest"] == "sha256:abc"
    ref2 = GovernedReference.from_dict(d)
    assert ref2 == ref


def test_governed_reference_kind_values():
    kinds = {k.value for k in GovernedReferenceKind}
    assert kinds == {"artifact", "evidence"}


def test_artifact_evidence_are_distinct_kinds():
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a1")
    e = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="e1")
    assert a.kind != e.kind
    assert a.kind.value == "artifact"
    assert e.kind.value == "evidence"
    # same shape, distinct kind
    assert type(a) is type(e)


def test_unified_reference_shape_with_distinct_kind():
    # both reuse GovernedReference
    assert GovernedReference.__name__ == "GovernedReference"
    # no separate ArtifactRef/EvidenceRef classes required
    symbols = _rg_symbols()
    # ensure we have NOT created separate classes for symmetry
    assert "ArtifactRef" not in symbols
    assert "EvidenceRef" not in symbols


def test_opaque_ref_preserved():
    raw_ref = "opaque:://custom/ref?x=1&y=2"
    r = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref=raw_ref)
    assert r.ref == raw_ref
    d = r.to_dict()
    assert d["ref"] == raw_ref
    # transport-neutral: not interpreted as path/url
    assert r.ref == raw_ref


def test_optional_digest_preserved():
    r1 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a")
    assert r1.digest is None
    assert "digest" not in r1.to_dict()
    r2 = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="e", digest="opaque-digest-123")
    assert r2.digest == "opaque-digest-123"
    assert r2.to_dict()["digest"] == "opaque-digest-123"
    # digest is opaque, no algorithm requirement
    r3 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a", digest="custom:digest:xyz")
    assert r3.digest == "custom:digest:xyz"


def test_reference_collection_defaults_immutable():
    p = ResultGovernanceProjection.success()
    assert p.artifact_refs == ()
    assert p.evidence_refs == ()
    assert isinstance(p.artifact_refs, tuple)
    # immutable default: attempt to mutate fails
    with pytest.raises((AttributeError, TypeError)):
        p.artifact_refs = (GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="x"),)  # type: ignore[misc]
    # new instance also empty tuple, not shared mutable list
    p2 = ResultGovernanceProjection.success()
    assert p2.artifact_refs == ()


def test_reference_serialization_round_trip():
    a1 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1", digest="d1")
    a2 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:2")
    e1 = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1", digest="d2")
    p = ResultGovernanceProjection.success(artifact_refs=(a1, a2), evidence_refs=(e1,))
    d = p.to_dict()
    assert "artifact_refs" in d
    assert "evidence_refs" in d
    assert len(d["artifact_refs"]) == 2
    assert len(d["evidence_refs"]) == 1
    # ordering preserved
    assert d["artifact_refs"][0]["ref"] == "art:1"
    assert d["artifact_refs"][1]["ref"] == "art:2"
    p2 = ResultGovernanceProjection.from_dict(d)
    assert p2.artifact_refs == p.artifact_refs
    assert p2.evidence_refs == p.evidence_refs
    assert p2.to_dict() == d


# ---------------------------------------------------------------------------
# artifact/evidence kind mismatch rejection
# ---------------------------------------------------------------------------


def test_artifact_evidence_kind_mismatch_fails_closed_via_constructor():
    bad_artifact = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1")
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.success(artifact_refs=(bad_artifact,))
    bad_evidence = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1")
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.success(evidence_refs=(bad_evidence,))


def test_artifact_evidence_kind_mismatch_fails_closed_via_from_dict():
    # artifact_refs containing evidence kind
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {
                "governance_version": "1.0",
                "outcome": "success",
                "artifact_refs": [{"kind": "evidence", "ref": "ev:1"}],
            }
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {
                "governance_version": "1.0",
                "outcome": "success",
                "evidence_refs": [{"kind": "artifact", "ref": "art:1"}],
            }
        )


# ---------------------------------------------------------------------------
# malformed reference fails closed
# ---------------------------------------------------------------------------


def test_malformed_reference_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        GovernedReference(kind="artifact", ref="a")  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError)):
        GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="")  # empty
    with pytest.raises((ValueError, TypeError)):
        GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="   ")
    with pytest.raises((ValueError, TypeError)):
        GovernedReference.from_dict({"kind": "bad", "ref": "a"})
    with pytest.raises((ValueError, TypeError)):
        GovernedReference.from_dict({"kind": "artifact"})  # missing ref
    with pytest.raises((ValueError, TypeError)):
        GovernedReference.from_dict({"kind": "artifact", "ref": "a", "digest": ""})
    with pytest.raises((ValueError, TypeError)):
        GovernedReference.from_dict({"kind": "artifact", "ref": "a", "unknown": 1})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "artifact_refs": "bad"}
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "artifact_refs": [{"kind": "artifact", "ref": ""}]}
        )


# ---------------------------------------------------------------------------
# verification states
# ---------------------------------------------------------------------------


def test_verification_values():
    vals = {v.value for v in VerificationStatus}
    assert vals == {"not_applicable", "unverified", "verified", "failed", "unknown"}


def test_verification_states_representable():
    for val in VerificationStatus:
        p = ResultGovernanceProjection.success(verification=val)
        assert p.verification == val
        d = p.to_dict()
        assert d["verification"] == val.value
        p2 = ResultGovernanceProjection.from_dict(d)
        assert p2.verification == val


def test_verification_default_none():
    p = ResultGovernanceProjection.success()
    assert p.verification is None
    assert "verification" not in p.to_dict()


def test_success_with_unverified_representable():
    p = ResultGovernanceProjection.success(verification=VerificationStatus.UNVERIFIED)
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.verification == VerificationStatus.UNVERIFIED


def test_success_with_failed_verification_representable():
    p = ResultGovernanceProjection.success(verification=VerificationStatus.FAILED)
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.verification == VerificationStatus.FAILED


def test_malformed_verification_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "verification": "bad"}
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "verification": ""}
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "verification": "ok"}
        )
    # direct construction with string should fail type check
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection(
            governance_version="1.0",
            outcome=ResultOutcome.SUCCESS,
            verification="verified",  # type: ignore[arg-type]
        )


def test_verification_not_execution_success():
    # verification != execution success
    p_ok = ResultGovernanceProjection.success(verification=VerificationStatus.UNVERIFIED)
    assert p_ok.outcome == ResultOutcome.SUCCESS
    assert p_ok.verification == VerificationStatus.UNVERIFIED
    # ensure we didn't reuse execution result "ok"
    assert not hasattr(p_ok, "ok")


# ---------------------------------------------------------------------------
# side-effect outcome
# ---------------------------------------------------------------------------


def test_side_effect_values():
    vals = {v.value for v in SideEffectOutcome}
    assert vals == {"none", "success", "failure", "partial", "unknown"}


def test_side_effect_outcome_all_values():
    for val in SideEffectOutcome:
        p = ResultGovernanceProjection.success(side_effect_outcome=val)
        assert p.side_effect_outcome == val
        d = p.to_dict()
        assert d["side_effect_outcome"] == val.value
        p2 = ResultGovernanceProjection.from_dict(d)
        assert p2.side_effect_outcome == val


def test_side_effect_default_none():
    p = ResultGovernanceProjection.success()
    assert p.side_effect_outcome is None
    assert "side_effect_outcome" not in p.to_dict()


def test_side_effect_none_representable():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.NONE)
    assert p.side_effect_outcome == SideEffectOutcome.NONE


def test_side_effect_success_representable():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.SUCCESS)
    assert p.side_effect_outcome == SideEffectOutcome.SUCCESS


def test_side_effect_failure_representable():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.FAILURE)
    assert p.side_effect_outcome == SideEffectOutcome.FAILURE


def test_side_effect_partial_representable():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.PARTIAL)
    assert p.side_effect_outcome == SideEffectOutcome.PARTIAL


def test_side_effect_unknown_representable():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.UNKNOWN)
    assert p.side_effect_outcome == SideEffectOutcome.UNKNOWN


def test_read_only_result_with_no_side_effect():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.NONE)
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.side_effect_outcome == SideEffectOutcome.NONE


def test_successful_mutation_side_effect():
    p = ResultGovernanceProjection.success(side_effect_outcome=SideEffectOutcome.SUCCESS)
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.side_effect_outcome == SideEffectOutcome.SUCCESS


def test_partial_side_effect_independent_from_result_outcome():
    err = ProjectNotFoundError(message="x")
    p = ResultGovernanceProjection.failure(err, side_effect_outcome=SideEffectOutcome.PARTIAL)
    assert p.outcome == ResultOutcome.FAILURE
    assert p.side_effect_outcome == SideEffectOutcome.PARTIAL


def test_malformed_side_effect_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "side_effect_outcome": "bad"}
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "side_effect_outcome": "ALLOW"}
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection(
            governance_version="1.0",
            outcome=ResultOutcome.SUCCESS,
            side_effect_outcome="none",  # type: ignore[arg-type]
        )


def test_authority_decision_is_not_side_effect_outcome():
    # SideEffectOutcome must NOT reuse authority decision values
    authority_vals = {"ALLOW", "DENY", "BLOCKED", "APPROVED", "NEEDS_APPROVAL"}
    side_vals = {v.value for v in SideEffectOutcome}
    assert authority_vals.isdisjoint(side_vals)
    # also verify enum values are lower-case
    for v in SideEffectOutcome:
        assert v.value not in authority_vals
    # construction with authority-like string must fail
    for bad in ["ALLOW", "DENY", "BLOCKED"]:
        with pytest.raises((ValueError, TypeError)):
            ResultGovernanceProjection.from_dict(
                {"governance_version": "1.0", "outcome": "success", "side_effect_outcome": bad}
            )


# ---------------------------------------------------------------------------
# orthogonal dimensions
# ---------------------------------------------------------------------------


def test_governance_dimensions_orthogonal():
    # success + complete=True + verified + none
    p1 = ResultGovernanceProjection.success(
        completeness=ResultCompleteness(complete=True),
        verification=VerificationStatus.VERIFIED,
        side_effect_outcome=SideEffectOutcome.NONE,
    )
    assert p1.outcome == ResultOutcome.SUCCESS
    assert p1.completeness.complete is True
    assert p1.verification == VerificationStatus.VERIFIED
    assert p1.side_effect_outcome == SideEffectOutcome.NONE

    # success + complete=False + unverified + none
    p2 = ResultGovernanceProjection.success(
        completeness=ResultCompleteness(complete=False, reason="bounded"),
        verification=VerificationStatus.UNVERIFIED,
        side_effect_outcome=SideEffectOutcome.NONE,
    )
    assert p2.completeness.complete is False
    assert p2.verification == VerificationStatus.UNVERIFIED

    # success + complete=True + verified + success
    p3 = ResultGovernanceProjection.success(
        completeness=ResultCompleteness(complete=True),
        verification=VerificationStatus.VERIFIED,
        side_effect_outcome=SideEffectOutcome.SUCCESS,
    )
    assert p3.side_effect_outcome == SideEffectOutcome.SUCCESS

    # failure + complete=None + unknown + partial
    err = ProjectNotFoundError(message="fail")
    p4 = ResultGovernanceProjection.failure(
        err,
        verification=VerificationStatus.UNKNOWN,
        side_effect_outcome=SideEffectOutcome.PARTIAL,
    )
    assert p4.outcome == ResultOutcome.FAILURE
    assert p4.completeness is None
    assert p4.verification == VerificationStatus.UNKNOWN
    assert p4.side_effect_outcome == SideEffectOutcome.PARTIAL

    # ensure they are distinct fields, not collapsed
    for p in (p1, p2, p3, p4):
        fields = {f.name for f in dataclasses.fields(p)}
        assert "outcome" in fields
        assert "completeness" in fields
        assert "verification" in fields
        assert "side_effect_outcome" in fields


def test_no_giant_status_enum():
    symbols = _rg_symbols()
    for forbidden in [
        "SUCCESS_VERIFIED_COMPLETE",
        "COMPOSITE_STATUS",
        "GiantStatus",
        "UnifiedStatus",
        "ResultStatus",
    ]:
        assert forbidden not in symbols
    # ensure no combined giant enum defined
    for py in RG_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "SUCCESS_VERIFIED" not in text
        assert "FAILED_SIDE_EFFECT" not in text


# ---------------------------------------------------------------------------
# W2 fields preserved
# ---------------------------------------------------------------------------


def test_w2_fields_preserved():
    prov = ResultProvenance(source_ref="src1", operation_ref="op1")
    comp = ResultCompleteness(complete=True, reason="r", scope="s")
    err = ProjectNotFoundError(message="w2 test")
    p = ResultGovernanceProjection.failure(err, provenance=prov, completeness=comp)
    d = p.to_dict()
    assert d["provenance"]["source_ref"] == "src1"
    assert d["completeness"]["complete"] is True
    # add W3 fields alongside W2
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a1")
    e = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="e1")
    p2 = ResultGovernanceProjection.failure(
        err,
        provenance=prov,
        completeness=comp,
        artifact_refs=(a,),
        evidence_refs=(e,),
        verification=VerificationStatus.VERIFIED,
        side_effect_outcome=SideEffectOutcome.SUCCESS,
    )
    d2 = p2.to_dict()
    assert d2["provenance"]["source_ref"] == "src1"
    assert d2["completeness"]["complete"] is True
    assert d2["artifact_refs"][0]["ref"] == "a1"


# ---------------------------------------------------------------------------
# full projection serialization round-trip
# ---------------------------------------------------------------------------


def test_full_projection_round_trip():
    prov = ResultProvenance(source_ref="src", content_digest="dig")
    comp = ResultCompleteness(complete=False, reason="partial", scope="s")
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1", digest="d1")
    e = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1")
    p = ResultGovernanceProjection.success(
        provenance=prov,
        completeness=comp,
        artifact_refs=(a,),
        evidence_refs=(e,),
        verification=VerificationStatus.VERIFIED,
        side_effect_outcome=SideEffectOutcome.SUCCESS,
    )
    d = p.to_dict()
    p2 = ResultGovernanceProjection.from_dict(d)
    assert p2.to_dict() == d
    assert p2.artifact_refs == p.artifact_refs
    assert p2.evidence_refs == p.evidence_refs
    # also with failure
    err = ProjectNotFoundError(message="fail")
    pf = ResultGovernanceProjection.failure(
        err, provenance=prov, completeness=comp, artifact_refs=(a,), evidence_refs=(e,), verification=VerificationStatus.FAILED, side_effect_outcome=SideEffectOutcome.PARTIAL
    )
    df = pf.to_dict()
    pf2 = ResultGovernanceProjection.from_dict(df)
    assert pf2.to_dict() == df


def test_w2_w3_combined_round_trip():
    prov = ResultProvenance(source_ref="s", operation_ref="op")
    # W2 only
    p_w2 = ResultGovernanceProjection.success(provenance=prov)
    d_w2 = p_w2.to_dict()
    assert "artifact_refs" not in d_w2
    p_w2_rt = ResultGovernanceProjection.from_dict(d_w2)
    assert p_w2_rt.to_dict() == d_w2
    # W3 extended still round-trips
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a")
    p_w3 = ResultGovernanceProjection.success(provenance=prov, artifact_refs=(a,), verification=VerificationStatus.UNVERIFIED)
    d_w3 = p_w3.to_dict()
    assert ResultGovernanceProjection.from_dict(d_w3).to_dict() == d_w3


def test_deterministic_canonical_json():
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1")
    e = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1")
    p1 = ResultGovernanceProjection.success(
        artifact_refs=(a,), evidence_refs=(e,), verification=VerificationStatus.VERIFIED, side_effect_outcome=SideEffectOutcome.NONE
    )
    p2 = ResultGovernanceProjection.success(
        artifact_refs=(a,), evidence_refs=(e,), verification=VerificationStatus.VERIFIED, side_effect_outcome=SideEffectOutcome.NONE
    )
    assert p1.to_json() == p2.to_json()
    assert canonical_json(p1.to_dict()) == canonical_json(p2.to_dict())
    # different insertion order in raw dict still canonical
    raw1 = {"outcome": "success", "governance_version": "1.0", "verification": "verified", "artifact_refs": [{"ref": "art:1", "kind": "artifact"}]}
    raw2 = {"governance_version": "1.0", "artifact_refs": [{"kind": "artifact", "ref": "art:1"}], "verification": "verified", "outcome": "success"}
    assert canonical_json(raw1) == canonical_json(raw2)


def test_unknown_fields_rejected():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "unknown_field": 1})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "artifact_refs": [{"kind": "artifact", "ref": "a", "extra": 1}]})


# ---------------------------------------------------------------------------
# no payload takeover, no leakage
# ---------------------------------------------------------------------------


def test_no_payload_in_common_governance():
    fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    for forbidden in ("payload", "result_data", "output", "stdout", "stderr"):
        assert forbidden not in fields
    for cls in (GovernedReference, ResultProvenance, ResultCompleteness):
        cfields = {f.name for f in dataclasses.fields(cls)}
        for forbidden in ("payload", "result_data", "output"):
            assert forbidden not in cfields


def test_context_opaque_reference_not_automatically_artifact_or_evidence():
    from aota_forge.core.providers.context import ContextResponse
    resp = ContextResponse.success(payload=({"x": 1},), reference="opaque-cursor")
    assert resp.reference == "opaque-cursor"
    # Must NOT automatically become artifact/evidence
    p = ResultGovernanceProjection.success()
    assert p.artifact_refs == ()
    assert p.evidence_refs == ()
    # manual mapping would be required, not automatic
    assert resp.reference not in [r.ref for r in p.artifact_refs]


def test_value_objects_immutable():
    a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="a")
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        a.ref = "b"  # type: ignore[misc]
    p = ResultGovernanceProjection.success(artifact_refs=(a,))
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        p.verification = VerificationStatus.VERIFIED  # type: ignore[misc]


def test_no_storage_engine_runtime_leakage():
    symbols = _rg_symbols()
    for forbidden in ("ArtifactStore", "EvidenceStore", "ResultStore", "BlobStore", "ObjectStore", "VerificationEngine", "Verifier", "ArtifactVerifier", "EvidenceVerifier"):
        assert forbidden not in symbols
    # inspect file content for forbidden strings
    for py in RG_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for forbidden in ["class ArtifactStore", "class EvidenceDatabase", "class VerificationEngine", "def verify("]:
            assert forbidden not in text
    # no execution-specific fields
    fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "adapter_handle", "dispatch_attempt_id", "correlation_id", "execution_stats"):
        assert forbidden not in fields
    for forbidden in ("provider_id", "provider_name", "mcp_server", "http_endpoint", "database_url", "vector_index"):
        assert forbidden not in fields
    for forbidden in ("observation_id", "request_fingerprint", "bounds", "continuation", "safety", "backend", "page_size", "pages_read"):
        assert forbidden not in fields
    # authority not imported
    for py in RG_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "from aota_forge.core.authority" not in text
        assert "import authority" not in text


def test_no_new_canonical_error_code():
    # ensure no new error codes were added for W3; error handling reuses ForgeError
    symbols = _rg_symbols()
    for forbidden in ("VerificationError", "ArtifactError", "EvidenceError", "SideEffectError"):
        assert forbidden not in symbols
    # also check core/contracts/errors.py not modified to add W3 codes — reuse existing
    err = ProjectNotFoundError(message="x")
    p = ResultGovernanceProjection.failure(err)
    recovered = error_from_dict(p.error)
    assert recovered.code == "PROJECT_NOT_FOUND"


def test_no_domain_extension_stubs():
    symbols = _rg_symbols()
    for forbidden in ("ExecutionExtension", "ContextExtension", "ToolExtension", "ReaderExtension"):
        assert forbidden not in symbols


def test_result_governance_version_remains_1_0():
    assert RESULT_GOVERNANCE_VERSION == "1.0"
    p = ResultGovernanceProjection.success()
    assert p.governance_version == "1.0"
    d = p.to_dict()
    assert d["governance_version"] == "1.0"


def test_w2_public_api_preserved():
    # W2 exports still available
    from aota_forge.core.result_governance import ResultOutcome, ResultProvenance, ResultCompleteness
    assert ResultOutcome.SUCCESS.value == "success"
    assert ResultProvenance is not None
    assert ResultCompleteness is not None
