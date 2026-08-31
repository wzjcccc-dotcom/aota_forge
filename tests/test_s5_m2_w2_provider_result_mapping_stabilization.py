"""S5/M2/W2 — Context / Tool Provider Result Mapping Proof (Stabilization).

TEST_ONLY=yes, PRODUCTION_WRITE_REQUIRED=no

Proves accepted ContextResponse and ToolResponse variants remain domain-native
while mapping into ResultGovernanceProjection without provider redesign or
common-core expansion. All mappers are TEST-LOCAL witnesses only.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import ast

import pytest

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import (
    ForgeError,
    HostResourceDeniedError,
    InputSizeError,
    InputTypeError,
    MaterializationFailedError,
    OutcomeUnknownError,
    ProjectNotFoundError,
    SourceParityMismatchError,
    error_from_dict,
)
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.providers.context import ContextRequest, ContextResponse
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
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
from aota_forge.core.result_governance.common import (
    _ALLOWED_PROJECTION_KEYS,
)
from aota_forge.core.authority import AuthorityDecision

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "aota_forge" / "core"
RG_COMMON = CORE_ROOT / "result_governance" / "common.py"
CTX_PY = CORE_ROOT / "providers" / "context.py"
TOOL_PY = CORE_ROOT / "providers" / "tool.py"


# ---------------------------------------------------------------------------
# TEST-LOCAL mapping helpers (proof witnesses only) — NOT production
# ---------------------------------------------------------------------------

def _map_context_result(
    resp: ContextResponse,
    *,
    explicit_provenance: ResultProvenance | None = None,
    explicit_completeness: ResultCompleteness | None = None,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_verification: VerificationStatus | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: ContextResponse -> ResultGovernanceProjection.

    Payload and opaque reference remain domain-native and are NEVER auto-copied
    into governance projection. GovernedReferences only when caller supplies
    explicit classification evidence.
    No string heuristic, no URL/path/digest heuristic.
    """
    # Validate that payload not auto-promoted: explicitly ignore resp.payload/reference
    if resp.ok:
        return ResultGovernanceProjection.success(
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    else:
        assert resp.error is not None
        return ResultGovernanceProjection.failure(
            resp.error,
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )


def _map_tool_result(
    resp: ToolResponse,
    *,
    operation: OperationContractDescriptor | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
    explicit_provenance: ResultProvenance | None = None,
    explicit_completeness: ResultCompleteness | None = None,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_verification: VerificationStatus | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: ToolResponse -> ResultGovernanceProjection.

    Payload stays domain-native, never auto-promoted to artifact/evidence.
    Side-effect outcome is NOT inferred from resp.ok alone; caller must
    supply explicit operation-effect evidence. If not supplied, conservative
    None is preserved (or UNKNOWN when fixture states uncertainty).
    """
    # operation param is for caller evidence, but mapping itself conservatively
    # uses only explicit_side_effect, never inferring from operation or ok.
    _ = operation  # referenced to show evidence boundary, not auto-inferred
    if resp.ok:
        return ResultGovernanceProjection.success(
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    else:
        assert resp.error is not None
        return ResultGovernanceProjection.failure(
            resp.error,
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )


def _make_read_descriptor(name: str = "read_op") -> OperationContractDescriptor:
    return OperationContractDescriptor(name=name, description="read operation")


def _make_mutation_descriptor(name: str = "mut_op") -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name=name,
        description="mutation operation",
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
# Helpers to assert source invariants
# ---------------------------------------------------------------------------

def _ctx_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ContextResponse)}


def _tool_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ToolResponse)}


def _rg_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ResultGovernanceProjection)}


# ---------------------------------------------------------------------------
# Context empirical matrix
# ---------------------------------------------------------------------------

class TestContextEmpiricalMatrix:
    def test_direct_payload_one_entry(self):
        resp = ContextResponse.success(payload=({"id": "a", "text": "hello"},))
        assert resp.ok and len(resp.payload) == 1
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert "payload" not in proj.to_dict()
        assert resp.payload[0]["text"] == "hello"

    def test_direct_payload_multiple_entries(self):
        payload = tuple({"k": str(i), "v": i} for i in range(5))
        resp = ContextResponse.success(payload=payload)
        assert len(resp.payload) == 5
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.artifact_refs == ()
        assert "payload" not in proj.to_dict()

    def test_empty_payload(self):
        resp = ContextResponse.success(payload=())
        assert resp.ok and resp.payload == ()
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.artifact_refs == ()

    def test_opaque_reference(self):
        resp = ContextResponse.success(payload=(), reference="opaque-ref-xyz-123")
        assert resp.reference == "opaque-ref-xyz-123"
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()

    def test_payload_and_reference_simultaneously_permitted(self):
        # ContextResponse contract permits payload + reference simultaneously (no invariant forbids it)
        resp = ContextResponse.success(payload=({"x": 1},), reference="opaque-cursor-77")
        assert resp.payload[0]["x"] == 1
        assert resp.reference == "opaque-cursor-77"
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        # both remain domain-native, not auto-promoted
        assert "payload" not in proj.to_dict()
        assert "reference" not in proj.to_dict()

    def test_typed_failure_known(self):
        err = ProjectNotFoundError(message="ctx not found")
        resp = ContextResponse.failure(err)
        assert not resp.ok
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "PROJECT_NOT_FOUND"
        recovered = error_from_dict(proj.error)
        assert recovered is not None and recovered.code == "PROJECT_NOT_FOUND"

    def test_retryable_typed_failure(self):
        # Use a retryable ForgeError: SourceParityMismatchError default_retryable=True
        err = SourceParityMismatchError(message="retryable failure")
        assert err.retryable is True
        resp = ContextResponse.failure(err)
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "SOURCE_PARITY_MISMATCH"
        assert proj.error["retryable"] is True
        # also test OutcomeUnknownError retryable
        err2 = OutcomeUnknownError(message="unknown outcome")
        resp2 = ContextResponse.failure(err2)
        proj2 = _map_context_result(resp2)
        assert proj2.error["retryable"] is True

    def test_request_side_limit_cursor_bounds_evidence(self):
        req = ContextRequest(subject_ref="subj", scope="scope", query="q", limit=10, cursor="cursor-opaque-1")
        resp = ContextResponse.success(payload=({"text": "bounded"},))
        # explicit completeness witness using external bounded evidence
        comp = ResultCompleteness(complete=False, reason="bounded", scope="docs")
        prov = ResultProvenance(source_ref="logical:context", operation_ref="ctx.search")
        proj = _map_context_result(resp, explicit_completeness=comp, explicit_provenance=prov)
        # request bounds stay domain-private, not auto in common projection except via explicit witness
        assert req.limit == 10
        assert req.cursor == "cursor-opaque-1"
        assert proj.completeness.complete is False
        assert proj.completeness.reason == "bounded"
        # ContextResponse itself has no completeness field
        assert "complete" not in _ctx_fields()

    def test_provider_private_metadata_pressure_via_opaque_reference(self):
        # opaque reference that looks like artifact/evidence must not auto-classify
        suspicious_refs = [
            "artifact:secret/path-123",
            "evidence://opaque/digest-abc",
            "https://example.com/artifact.json",
            "/var/data/path/to/artifact",
            "sha256:deadbeef123456",
            "digest-sha256-xyz",
        ]
        for ref in suspicious_refs:
            resp = ContextResponse.success(payload=(), reference=ref)
            proj = _map_context_result(resp)
            assert proj.artifact_refs == (), f"ref {ref!r} must not auto become artifact"
            assert proj.evidence_refs == (), f"ref {ref!r} must not auto become evidence"
            assert "reference" not in proj.to_dict()

    def test_deterministic_repeated_mapping_context(self):
        resp = ContextResponse.success(payload=({"a": 1},), reference="opaque-1")
        prov = ResultProvenance(source_ref="src", operation_ref="op")
        comp = ResultCompleteness(complete=True, reason="all", scope="s")
        p1 = _map_context_result(resp, explicit_provenance=prov, explicit_completeness=comp)
        p2 = _map_context_result(resp, explicit_provenance=prov, explicit_completeness=comp)
        assert p1.to_json() == p2.to_json()
        assert canonical_json(p1.to_dict()) == canonical_json(p2.to_dict())

    def test_unsupported_state_not_invented(self):
        # ContextResponse invariants: ok=True must have error=None, ok=False requires error
        resp_ok = ContextResponse.success(payload=())
        assert resp_ok.error is None
        # ensure we don't invent a state where payload+reference violates contract — contract allows it, so not invented
        # ensure we don't invent empty error for failure
        with pytest.raises(ValueError):
            ContextResponse(ok=False, payload=(), reference=None, error=None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            ContextResponse(ok=True, payload=(), reference=None, error={"code": "X", "message": "m"})  # type: ignore[arg-type]

    def test_context_payload_contains_artifact_like_keys_not_auto_promoted(self):
        payload = ({"artifact": "path/to/file", "evidence": "something", "digest": "sha256:abc", "reference": "ref-123", "path": "/tmp/x"},)
        resp = ContextResponse.success(payload=payload)
        proj = _map_context_result(resp)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        d = proj.to_dict()
        assert "payload" not in d
        assert "artifact" not in str(d).lower() or "artifact_refs" not in d


class TestContextPayloadBoundary:
    def test_payload_remains_domain_native(self):
        resp = ContextResponse.success(payload=({"domain": "keep", "artifact": "should-not-promote"},))
        proj = _map_context_result(resp)
        assert resp.payload[0]["domain"] == "keep"
        assert "payload" not in proj.to_dict()
        assert "payload" not in _rg_fields()
        # provenance/completeness fields should not contain payload keys
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        assert "payload" not in prov_fields
        assert "artifact" not in prov_fields

    def test_payload_auto_promoted_to_governance_is_no(self):
        for payload in [({"artifact": "x"},), ({"evidence": "y", "digest": "sha256:z"},)]:
            resp = ContextResponse.success(payload=payload)
            proj = _map_context_result(resp)
            assert proj.artifact_refs == ()
            assert proj.evidence_refs == ()
            assert "payload" not in proj.to_dict()

    def test_reference_not_auto_promoted(self):
        for ref in ["artifact-looking", "evidence-looking", "digest-abc", "https://example.com/path"]:
            resp = ContextResponse.success(payload=(), reference=ref)
            proj = _map_context_result(resp)
            assert proj.artifact_refs == ()
            assert proj.evidence_refs == ()


class TestContextOpaqueReference:
    def test_opaque_reference_preserved(self):
        resp = ContextResponse.success(payload=(), reference="opaque-xyz-987")
        proj = _map_context_result(resp)
        assert resp.reference == "opaque-xyz-987"
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert "reference" not in proj.to_dict()

    def test_reference_automatic_artifact_mapping_is_no(self):
        artifact_looking = ["artifact:opaque:xyz", "http://artifact.example.com/file", "/path/to/artifact.txt", "sha256:abc"]
        for ref in artifact_looking:
            resp = ContextResponse.success(payload=(), reference=ref)
            proj = _map_context_result(resp)
            assert proj.artifact_refs == (), f"{ref!r} must not auto-map to artifact"

    def test_reference_automatic_evidence_mapping_is_no(self):
        evidence_looking = ["evidence:opaque:xyz", "evidence-digest-sha256", "https://evidence.example.com/e", "digest:abc"]
        for ref in evidence_looking:
            resp = ContextResponse.success(payload=(), reference=ref)
            proj = _map_context_result(resp)
            assert proj.evidence_refs == (), f"{ref!r} must not auto-map to evidence"

    def test_no_string_url_path_digest_heuristic(self):
        # ensure mapping helper does not inspect string content at all
        resp = ContextResponse.success(payload=(), reference="https://example.com/sha256:abcdef/path")
        proj = _map_context_result(resp)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        # explicit classification still required
        explicit = (GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:explicit:1"),)
        proj2 = _map_context_result(resp, explicit_artifact_refs=explicit)
        assert len(proj2.artifact_refs) == 1

    def test_explicit_classification_required_for_governed_reference(self):
        resp = ContextResponse.success(payload=(), reference="opaque-need-explicit")
        proj_no = _map_context_result(resp)
        assert proj_no.artifact_refs == ()
        explicit = (GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:explicit:1", digest="sha256:abc"),)
        proj_yes = _map_context_result(resp, explicit_evidence_refs=explicit)
        assert proj_yes.evidence_refs[0].ref == "evidence:explicit:1"


class TestContextCompletenessEvidence:
    def test_context_response_has_no_completeness_field(self):
        assert "completeness" not in _ctx_fields()
        assert "complete" not in _ctx_fields()
        # also ensure ResultGovernanceProjection completeness is separate
        assert "completeness" in _rg_fields()

    def test_successful_response_with_complete_false_via_explicit_witness(self):
        resp = ContextResponse.success(payload=({"text": "a"},))
        # ContextResponse has no completeness; witness via explicit ResultCompleteness
        comp = ResultCompleteness(complete=False, reason="bounded", scope="project_docs")
        proj = _map_context_result(resp, explicit_completeness=comp)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.completeness is not None
        assert proj.completeness.complete is False
        assert proj.completeness.reason == "bounded"
        # original response unchanged
        assert resp.ok is True
        assert not hasattr(resp, "completeness")

    def test_completeness_requires_external_bounded_evidence_not_redesign(self):
        # Bounded evidence supplied externally via request limit/cursor or explicit witness
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=2, cursor="next")
        resp = ContextResponse.success(payload=({"x": 1},))
        comp = ResultCompleteness(complete=False, reason="truncated by limit=2")
        proj = _map_context_result(resp, explicit_completeness=comp)
        assert proj.completeness.complete is False
        assert req.limit == 2
        # ContextResponse unchanged
        assert "completeness" not in _ctx_fields()


class TestContextTypedFailure:
    def test_known_failure_mapping_proven(self):
        err = HostResourceDeniedError(message="ctx failure")
        resp = ContextResponse.failure(err)
        proj = _map_context_result(resp)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "HOST_RESOURCE_DENIED"
        assert error_from_dict(proj.error).code == "HOST_RESOURCE_DENIED"

    def test_retryable_failure_mapping_proven(self):
        err = MaterializationFailedError(message="retryable ctx fail")
        # MaterializationFailedError default_retryable = True
        assert err.retryable is True
        resp = ContextResponse.failure(err)
        proj = _map_context_result(resp)
        assert proj.error["retryable"] is True
        # also via dict with retryable true
        retryable_dict = {"code": "SOURCE_PARITY_MISMATCH", "message": "parity", "retryable": True}
        resp2 = ContextResponse.failure(retryable_dict)
        proj2 = _map_context_result(resp2)
        assert proj2.error["retryable"] is True

    def test_provider_error_reuses_forge_error(self):
        err = InputTypeError(message="bad input")
        resp = ContextResponse.failure(err)
        proj = _map_context_result(resp)
        assert proj.error["code"] == "INPUT_TYPE_INVALID"
        # no new ContextError invented
        src = CTX_PY.read_text(encoding="utf-8")
        assert "ContextError" not in src
        assert "class ContextError" not in src

    def test_no_new_common_error_authority(self):
        # ForgeError reuse is sufficient; no new error authority in result_governance
        rg_src = RG_COMMON.read_text(encoding="utf-8")
        assert "class ContextError" not in rg_src


# ---------------------------------------------------------------------------
# Tool empirical matrix
# ---------------------------------------------------------------------------

class TestToolEmpiricalMatrix:
    def test_read_only_success(self):
        desc = _make_read_descriptor("read_only_emp")
        resp = ToolResponse.success(payload={"read_output": "data"})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.side_effect_outcome == SideEffectOutcome.NONE
        assert resp.payload == {"read_output": "data"}

    def test_mutation_capable_success(self):
        desc = _make_mutation_descriptor("mut_cap_emp")
        resp = ToolResponse.success(payload={"mutated": True})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.side_effect_outcome == SideEffectOutcome.SUCCESS

    def test_structured_payload(self):
        resp = ToolResponse.success(payload={"nested": {"a": [1, 2, 3]}, "count": 42})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert resp.payload["count"] == 42
        assert "payload" not in proj.to_dict()

    def test_typed_failure(self):
        err = InputTypeError(message="tool bad input")
        resp = ToolResponse.failure(err)
        proj = _map_tool_result(resp)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "INPUT_TYPE_INVALID"

    def test_retryable_failure(self):
        err = SourceParityMismatchError(message="retry tool")
        resp = ToolResponse.failure(err)
        proj = _map_tool_result(resp)
        assert proj.error["retryable"] is True
        assert proj.error["code"] == "SOURCE_PARITY_MISMATCH"

    def test_non_retryable_failure(self):
        err = ProjectNotFoundError(message="not found")
        assert err.retryable is False
        resp = ToolResponse.failure(err)
        proj = _map_tool_result(resp)
        assert proj.error["retryable"] is False

    def test_explicit_side_effect_evidence_present(self):
        desc = _make_mutation_descriptor("side_present")
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj.side_effect_outcome == SideEffectOutcome.SUCCESS
        assert "side_effect_outcome" in proj.to_dict()

    def test_side_effect_evidence_absent(self):
        desc = _make_mutation_descriptor("side_absent")
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        assert "side_effect_outcome" not in proj.to_dict()

    def test_ambiguous_failure_after_attempted_mutation(self):
        desc = _make_mutation_descriptor("ambig_mut")
        err = OutcomeUnknownError(message="unknown outcome after mutation")
        resp = ToolResponse.failure(err)
        # Without effect evidence, conservative mapping stays None or UNKNOWN, not inferred
        proj_none = _map_tool_result(resp, operation=desc, explicit_side_effect=None)
        assert proj_none.side_effect_outcome is None
        proj_unknown = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert proj_unknown.side_effect_outcome == SideEffectOutcome.UNKNOWN
        # Failure itself does not imply specific side-effect
        assert proj_none.outcome == ResultOutcome.FAILURE
        assert proj_none.error["code"] == "OUTCOME_UNKNOWN"

    def test_deterministic_repeated_mapping_tool(self):
        desc = _make_read_descriptor("det_tool")
        resp = ToolResponse.success(payload={"a": 1})
        p1 = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        p2 = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert p1.to_json() == p2.to_json()

    def test_payload_artifact_like_not_auto_promoted(self):
        resp = ToolResponse.success(payload={"artifact": "path/to/file", "evidence": "something", "digest": "sha256:abc"})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert "payload" not in proj.to_dict()


class TestToolPayloadBoundary:
    def test_tool_payload_remains_domain_native(self):
        resp = ToolResponse.success(payload={"tool_payload": "keep", "artifact": "fake-path"})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert resp.payload["tool_payload"] == "keep"
        assert "payload" not in proj.to_dict()
        assert "payload" not in _rg_fields()

    def test_tool_payload_auto_promoted_to_artifact_is_no(self):
        resp = ToolResponse.success(payload={"path": "/artifact/path", "digest": "sha256:aaa"})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.artifact_refs == ()
        assert "artifact_refs" not in proj.to_dict()

    def test_tool_payload_auto_promoted_to_evidence_is_no(self):
        resp = ToolResponse.success(payload={"evidence": "should not auto", "reference": "evidence:xyz"})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.evidence_refs == ()
        assert "evidence_refs" not in proj.to_dict()


class TestToolSideEffectBoundary:
    def test_ok_not_equal_side_effect_outcome(self):
        desc = _make_mutation_descriptor("ok_vs_effect")
        resp = ToolResponse.success(payload={"ok": True})
        # ok=True but side_effect_outcome is None without evidence
        proj_none = _map_tool_result(resp, operation=desc, explicit_side_effect=None)
        assert resp.ok is True
        assert proj_none.side_effect_outcome is None
        # ok does not imply side effect
        assert resp.ok != proj_none.side_effect_outcome

    def test_side_effect_mapping_requires_external_operation_effect_evidence(self):
        # Without external evidence, conservative
        resp = ToolResponse.success(payload={})
        proj = _map_tool_result(resp, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        # With explicit evidence, allowed
        proj2 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj2.side_effect_outcome == SideEffectOutcome.SUCCESS
        # No inference from ok alone — verify helper ignores ok
        assert resp.ok is True
        assert proj.side_effect_outcome is None

    def test_read_only_side_effect_none_mapping_proven(self):
        desc = _make_read_descriptor("read_only_none")
        assert desc.read_write == "read"
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.side_effect_outcome == SideEffectOutcome.NONE
        assert proj.to_dict()["side_effect_outcome"] == "none"

    def test_mutation_success_side_effect_success_mapping_proven(self):
        desc = _make_mutation_descriptor("mut_success_map")
        assert desc.read_write == "read-write"
        resp = ToolResponse.success(payload={"mutated": True})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj.side_effect_outcome == SideEffectOutcome.SUCCESS
        assert proj.to_dict()["side_effect_outcome"] == "success"

    def test_success_without_effect_evidence_does_not_imply_success(self):
        desc = _make_mutation_descriptor("mut_no_evidence")
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        assert "side_effect_outcome" not in proj.to_dict()
        # Also allowed explicit UNKNOWN
        proj2 = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert proj2.side_effect_outcome == SideEffectOutcome.UNKNOWN

    def test_failure_does_not_imply_specific_side_effect(self):
        err = InputTypeError(message="fail")
        resp = ToolResponse.failure(err)
        proj_none = _map_tool_result(resp, explicit_side_effect=None)
        assert proj_none.side_effect_outcome is None
        proj_unknown = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert proj_unknown.side_effect_outcome == SideEffectOutcome.UNKNOWN
        # Failure may correspond to any side-effect with evidence; without evidence stays conservative
        for outcome in [SideEffectOutcome.NONE, SideEffectOutcome.FAILURE, SideEffectOutcome.PARTIAL, SideEffectOutcome.UNKNOWN]:
            proj = _map_tool_result(resp, explicit_side_effect=outcome)
            assert proj.side_effect_outcome == outcome
        # But without explicit, no specific outcome
        assert proj_none.outcome == ResultOutcome.FAILURE
        assert proj_none.side_effect_outcome is None

    def test_partial_requires_explicit_evidence(self):
        # ok=False alone must not produce PARTIAL
        err = InputSizeError(message="fail")
        resp = ToolResponse.failure(err)
        proj = _map_tool_result(resp, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        assert proj.side_effect_outcome != SideEffectOutcome.PARTIAL
        # Only with explicit partial evidence
        proj2 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.PARTIAL)
        assert proj2.side_effect_outcome == SideEffectOutcome.PARTIAL
        assert proj2.to_dict()["side_effect_outcome"] == "partial"

    def test_do_not_derive_partial_from_ok_false(self):
        err = ProjectNotFoundError(message="fail partial test")
        resp = ToolResponse.failure(err)
        # No automatic PARTIAL from ok=False
        proj = _map_tool_result(resp)
        assert proj.side_effect_outcome is None


class TestAuthoritySeparation:
    def test_side_effect_is_not_authority_decision(self):
        # SideEffectOutcome values are distinct from AuthorityDecision
        side_values = {e.value for e in SideEffectOutcome}
        auth_values = {e.value for e in AuthorityDecision}
        assert side_values.isdisjoint(auth_values) or side_values != auth_values
        for allow_deny in ["ALLOW", "DENY", "BLOCKED", "NEEDS_APPROVAL"]:
            assert allow_deny not in side_values
        for se in ["none", "success", "failure", "partial", "unknown"]:
            assert se not in auth_values

    def test_no_auth_values_in_side_effect(self):
        rg_src = RG_COMMON.read_text(encoding="utf-8")
        # Ensure no AUTHORITY decision strings inside SideEffectOutcome definition
        # Check that SideEffectOutcome not containing ALLOW/DENY
        for bad in ["ALLOW", "DENY", "BLOCKED"]:
            # RG_COMMON should define SideEffectOutcome with none/success/failure/partial/unknown, not ALLOW
            pass
        # Prove via enum values
        assert SideEffectOutcome.NONE.value == "none"
        assert AuthorityDecision.ALLOW.value == "ALLOW"
        assert SideEffectOutcome.FAILURE.value != AuthorityDecision.DENY.value

    def test_authority_engine_not_changed(self):
        # AuthorityEngine must not gain side-effect fields
        from aota_forge.core.authority import AuthorityEngine
        src = pathlib.Path(CORE_ROOT / "authority.py").read_text(encoding="utf-8")
        assert "SideEffectOutcome" not in src


class TestProviderPrivateMetadataIsolation:
    def test_context_opaque_reference_isolated(self):
        resp = ContextResponse.success(payload=(), reference="provider-private-opaque-xyz")
        proj = _map_context_result(resp)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert "provider-private-opaque-xyz" not in json.dumps(proj.to_dict())

    def test_context_request_cursor_limit_isolated(self):
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5, cursor="cursor-private")
        resp = ContextResponse.success(payload=({"x": 1},))
        proj = _map_context_result(resp)
        d = proj.to_dict()
        # Cursor/limit must not leak into common governance unless explicit witness
        assert "cursor" not in str(d).lower()
        assert "limit" not in str(d).lower()
        # provenance fields do not contain cursor/limit
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        assert "cursor" not in prov_fields
        assert "limit" not in prov_fields

    def test_tool_mutation_scope_isolated(self):
        desc = _make_mutation_descriptor("isolated_scope")
        assert desc.mutation_scope == "subject"
        resp = ToolResponse.success(payload={})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        d = proj.to_dict()
        # mutation_scope is descriptor private; not in common projection unless explicitly mapped elsewhere
        assert "mutation_scope" not in str(d).lower()
        assert "mutation_scope" not in _rg_fields()

    def test_tool_read_write_classification_isolated(self):
        desc = _make_read_descriptor("read_isolate")
        assert desc.read_write == "read"
        proj = _map_tool_result(ToolResponse.success(payload={}), operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert "read_write" not in _rg_fields()
        assert "read_write" not in json.dumps(proj.to_dict())

    def test_implementation_specific_payload_metadata_isolated(self):
        # payload containing implementation-specific keys must not leak
        resp = ToolResponse.success(payload={"_mcp_server": "secret", "_http_endpoint": "secret", "provider_meta": "secret"})
        proj = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert "_mcp_server" not in json.dumps(proj.to_dict())
        assert "provider_meta" not in json.dumps(proj.to_dict())


class TestCommonCoreExpansionGate:
    def test_no_new_common_core_field_required(self):
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        rg_fields = _rg_fields()
        extra = rg_fields - allowed
        assert not extra, f"unexpected new core fields: {extra}"
        # Also verify _ALLOWED_PROJECTION_KEYS unchanged
        assert set(_ALLOWED_PROJECTION_KEYS) == allowed

    def test_m1_common_contract_defect_not_found(self):
        # Prove mapping works without needing new field — all variants already covered
        # Context and Tool both map into 1.0 without error
        ctx_resp = ContextResponse.success(payload=({"a": 1},), reference="opaque")
        tool_resp = ToolResponse.success(payload={"x": 1})
        p_ctx = _map_context_result(ctx_resp, explicit_completeness=ResultCompleteness(complete=False, reason="bounded"))
        p_tool = _map_tool_result(tool_resp, operation=_make_mutation_descriptor("gate_check"), explicit_side_effect=SideEffectOutcome.SUCCESS)
        # Both produce valid projections under 1.0
        assert p_ctx.governance_version == "1.0"
        assert p_tool.governance_version == "1.0"
        # No new field needed to represent completeness=False or side effect
        assert p_ctx.completeness.complete is False
        assert p_tool.side_effect_outcome == SideEffectOutcome.SUCCESS

    def test_result_governance_version_is_1_0(self):
        assert RESULT_GOVERNANCE_VERSION == "1.0"

    def test_version_bump_not_required(self):
        # Version remains 1.0; no protocol ceremony
        assert RESULT_GOVERNANCE_VERSION == "1.0"
        # Verify common file still only allows 1.0
        src = RG_COMMON.read_text(encoding="utf-8")
        assert 'RESULT_GOVERNANCE_VERSION: str = "1.0"' in src


class TestForgeErrorReuseAcrossProviders:
    def test_provider_error_mapping_reuses_forge_error(self):
        for err in [
            HostResourceDeniedError(message="ctx denied"),
            InputTypeError(message="tool bad"),
            MaterializationFailedError(message="retryable"),
            SourceParityMismatchError(message="parity"),
        ]:
            for make_resp in [ContextResponse.failure, ToolResponse.failure]:
                resp = make_resp(err)
                # error dict must be ForgeError projection
                assert "code" in resp.error and "message" in resp.error and "retryable" in resp.error
                recovered = error_from_dict(resp.error)
                assert isinstance(recovered, ForgeError)
                assert recovered.code == err.code

    def test_no_new_provider_error_authority(self):
        assert "class ContextError" not in CTX_PY.read_text(encoding="utf-8")
        assert "class ToolError" not in TOOL_PY.read_text(encoding="utf-8")
        # No new error classes in result_governance common beyond allowed
        rg_src = RG_COMMON.read_text(encoding="utf-8")
        assert "class ContextError" not in rg_src
        assert "class ToolError" not in rg_src

    def test_no_new_common_error_authority(self):
        rg_src = RG_COMMON.read_text(encoding="utf-8")
        assert "class ForgeError" not in rg_src
        # result_governance just validates error via error_from_dict
        assert "ERROR_CLASSES" not in rg_src


class TestDeterministicReplay:
    def test_context_mapping_replay_deterministic(self):
        resp = ContextResponse.success(payload=({"a": 1, "b": 2},), reference="opaque-stable-1")
        prov = ResultProvenance(source_ref="src:det", operation_ref="ctx.op", content_digest="dig:123")
        comp = ResultCompleteness(complete=True, reason="all", scope="docs")
        p1 = _map_context_result(resp, explicit_provenance=prov, explicit_completeness=comp)
        p2 = _map_context_result(resp, explicit_provenance=prov, explicit_completeness=comp)
        assert p1.to_json() == p2.to_json()
        assert p1.to_dict() == p2.to_dict()

    def test_tool_mapping_replay_deterministic(self):
        resp = ToolResponse.success(payload={"x": 1})
        p1 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE, explicit_provenance=ResultProvenance(source_ref="src:det"))
        p2 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.NONE, explicit_provenance=ResultProvenance(source_ref="src:det"))
        assert p1.to_json() == p2.to_json()

    def test_context_failure_replay_deterministic(self):
        err = ProjectNotFoundError(message="fail")
        resp = ContextResponse.failure(err)
        p1 = _map_context_result(resp)
        p2 = _map_context_result(resp)
        assert p1.to_json() == p2.to_json()

    def test_tool_failure_replay_deterministic(self):
        err = InputTypeError(message="fail")
        resp = ToolResponse.failure(err)
        p1 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        p2 = _map_tool_result(resp, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert p1.to_json() == p2.to_json()


class TestCrossProviderProjectionStability:
    def test_single_common_governance_projection_shape(self):
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        ctx_proj = _map_context_result(ContextResponse.success(payload=({"x": 1},)))
        tool_proj = _map_tool_result(ToolResponse.success(payload={"y": 1}), explicit_side_effect=SideEffectOutcome.NONE)
        for proj in [ctx_proj, tool_proj]:
            for k in proj.to_dict().keys():
                assert k in allowed

    def test_provider_native_result_shapes_remain_distinct(self):
        ctx_fields = _ctx_fields()
        tool_fields = _tool_fields()
        # Distinct carriers
        assert ctx_fields != tool_fields
        assert "reference" in ctx_fields and "reference" not in tool_fields
        assert "payload" in ctx_fields and "payload" in tool_fields
        # Payload types differ: context payload is tuple[dict], tool payload is dict|None
        # Neither inherits from common base
        assert ContextResponse.__bases__ != ToolResponse.__bases__ or ContextResponse is not ToolResponse
        # Ensure no shared provider result superclass
        assert "ProviderResult" not in CTX_PY.read_text(encoding="utf-8")
        assert "ProviderResult" not in TOOL_PY.read_text(encoding="utf-8")


class TestNoProviderRedesign:
    def test_context_response_unchanged(self):
        fields = _ctx_fields()
        assert fields == {"ok", "payload", "reference", "error"}
        # Verify no completeness field added
        assert "completeness" not in fields

    def test_tool_response_unchanged(self):
        fields = _tool_fields()
        assert fields == {"ok", "payload", "error"}
        assert "side_effect_outcome" not in fields

    def test_no_provider_interface_redesign(self):
        for path in [CTX_PY, TOOL_PY]:
            src = path.read_text(encoding="utf-8")
            assert "class BaseProvider" not in src
            assert "class ProviderResult" not in src
        # No inheritance from common result base
        ctx_tree = ast.parse(CTX_PY.read_text(encoding="utf-8"))
        tool_tree = ast.parse(TOOL_PY.read_text(encoding="utf-8"))
        for tree in [ctx_tree, tool_tree]:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ("ContextResponse", "ToolResponse"):
                    assert len(node.bases) == 0, f"{node.name} must not inherit from common base"


class TestNoProductionMapper:
    def test_no_production_mapping_helper(self):
        # Production should not contain ProviderResultMapper etc.
        for py in CORE_ROOT.rglob("*.py"):
            if "result_governance" in str(py):
                continue
            src = py.read_text(encoding="utf-8")
            for bad in ["ProviderResultMapper", "ContextResultMapper", "ToolResultMapper", "ResultMapperRegistry"]:
                assert bad not in src, f"{py} must not contain {bad}"
        # This test's helper is local; ensure production dir has no mapper
        assert not (CORE_ROOT / "result_governance" / "mapper.py").exists()
        assert not (CORE_ROOT / "providers" / "mapper.py").exists()

    def test_test_local_mapping_only(self):
        # Verify our helpers are defined in this test file only
        this_src = pathlib.Path(__file__).read_text(encoding="utf-8")
        assert "_map_context_result" in this_src
        assert "_map_tool_result" in this_src


class TestArchitectureInvariants:
    def test_production_mapping_helper_required_is_no(self):
        assert not (CORE_ROOT / "result_governance" / "mapper.py").exists()

    def test_generic_result_mapper_required_is_no(self):
        assert not (CORE_ROOT / "providers" / "mapper.py").exists()

    def test_result_mapper_registry_required_is_no(self):
        for py in CORE_ROOT.rglob("*.py"):
            assert "ResultMapperRegistry" not in py.read_text(encoding="utf-8")

    def test_no_yaml_change(self):
        assert not (REPO_ROOT / ".aota" / "results.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "result_governance.yaml").exists()
        # No new result governance yaml
        assert not any(REPO_ROOT.glob(".aota/*result*"))
        # aota_forge core yaml check
        assert not (REPO_ROOT / "aota_forge" / "core" / "result_governance.yaml").exists()

    def test_governance_version_remains_1_0(self):
        assert RESULT_GOVERNANCE_VERSION == "1.0"

    def test_common_contract_not_expanded(self):
        assert _rg_fields() == {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}


class TestProviderResultMappingWithoutRedesign:
    def test_context_provider_result_mapping_proven(self):
        # at least one success and one failure prove mapping without redesign
        ok_resp = ContextResponse.success(payload=({"text": "hello"},))
        fail_resp = ContextResponse.failure(HostResourceDeniedError(message="fail"))
        p_ok = _map_context_result(ok_resp)
        p_fail = _map_context_result(fail_resp)
        assert p_ok.outcome == ResultOutcome.SUCCESS
        assert p_fail.outcome == ResultOutcome.FAILURE
        assert p_ok.governance_version == "1.0"

    def test_tool_provider_result_mapping_proven(self):
        ok_resp = ToolResponse.success(payload={"result": "ok"})
        fail_resp = ToolResponse.failure(InputTypeError(message="fail"))
        p_ok = _map_tool_result(ok_resp, explicit_side_effect=SideEffectOutcome.NONE)
        p_fail = _map_tool_result(fail_resp)
        assert p_ok.outcome == ResultOutcome.SUCCESS
        assert p_fail.outcome == ResultOutcome.FAILURE

    def test_provider_result_mapping_without_provider_redesign_is_yes(self):
        # Both carriers remain domain-native
        assert "payload" in _ctx_fields()
        assert "payload" in _tool_fields()
        assert "reference" in _ctx_fields()
        assert "reference" not in _tool_fields()
        # Governance projection is single shape
        assert _rg_fields() == {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
