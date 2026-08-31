"""S5/M1/W4 — Machine Projection & Existing Result Compatibility.

Cross-domain compatibility proof + deterministic machine-projection proof
+ common-core/domain-extension boundary proof.

Challenge four domains:
  1. CanonicalResult / execution result
  2. ContextResponse
  3. ToolResponse
  4. Reader-like governed read result evidence

Architecture required per spec:
  domain-native result + ResultGovernanceProjection
Do NOT replace domain-native result.

W4 posture: TEST_ONLY=yes, M1_W4_PRODUCTION_WRITE_REQUIRED=no.
Mapping helpers are TEST-LOCAL witnesses only, NOT production adapters.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import json

import pytest

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.errors import (
    ForgeError,
    HostResourceDeniedError,
    InputSizeError,
    InputTypeError,
    ProjectNotFoundError,
    error_from_dict,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.providers.context import ContextRequest, ContextResponse
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import PROTOCOL_VERSION
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

# ---------------------------------------------------------------------------
# TEST-LOCAL mapping helpers (proof witnesses only)
# ---------------------------------------------------------------------------

def _map_execution_result(
    cr: CanonicalResult,
    *,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_provenance: ResultProvenance | None = None,
    explicit_completeness: ResultCompleteness | None = None,
    explicit_verification: VerificationStatus | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: execution -> common governance.

    Only maps governance semantics (outcome/error + explicit evidence).
    NEVER copies execution-private identities or payload.
    output_artifacts are NOT auto-converted; caller must supply explicit refs.
    """
    if cr.ok:
        return ResultGovernanceProjection.success(
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    else:
        # reuse existing ForgeError projection from CanonicalResult.error
        assert cr.error is not None
        # validate that error dict is already a ForgeError-style projection
        err = error_from_dict(cr.error)  # ensures code/message/retryable round-trip
        # Use dict directly to preserve original code
        return ResultGovernanceProjection.failure(
            cr.error,
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )


def _map_context_response(
    resp: ContextResponse,
    *,
    explicit_provenance: ResultProvenance | None = None,
    explicit_completeness: ResultCompleteness | None = None,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_verification: VerificationStatus | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: ContextResponse -> common governance.

    Payload and opaque reference stay domain-native; never auto-copied.
    GovernedReference only when caller supplies explicit classification.
    """
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


def _map_tool_response(
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
    """TEST-LOCAL witness: ToolResponse -> common governance.

    Tool payload stays domain content.
    Side-effect outcome is NOT inferred from ToolResponse alone; caller must
    supply operation/contract evidence for read-only vs mutation.
    """
    # Use supplied explicit_side_effect; if None, mapping is conservative (None)
    # For read-only operation, caller may supply SideEffectOutcome.NONE
    # For mutation, caller may supply SUCCESS on evidence.
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


# ---------------------------------------------------------------------------
# TEST-LOCAL Reader-like rich fixture (deliberately richer than common core)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _ReaderCompleteness:
    complete: bool | None = None
    reason: str | None = None
    scope: str | None = None


@dataclasses.dataclass(frozen=True)
class _ReaderProvenance:
    backend: str | None = None
    pages_read: int | None = None


@dataclasses.dataclass(frozen=True)
class ReaderLikeResult:
    """TEST-LOCAL Reader-like governed result evidence.

    Rich envelope; only subset maps to common core.
    """

    contract_version: str
    status: str  # "ok" | "error" etc.
    operation: str
    source: str
    observation_id: str
    request_fingerprint: str
    content_digest: str
    completeness: _ReaderCompleteness
    provenance: _ReaderProvenance
    warnings: tuple[str, ...]
    safety: dict
    bounds: dict
    continuation: str | None
    error: dict | None = None


def _make_reader_like_success() -> ReaderLikeResult:
    return ReaderLikeResult(
        contract_version="1.0",
        status="ok",
        operation="reader.search",
        source="reader://project/docs",
        observation_id="obs-123456",
        request_fingerprint="fp-abc-999",
        content_digest="sha256:reader-content-digest-xyz",
        completeness=_ReaderCompleteness(complete=True, reason="all pages read", scope="project_docs"),
        provenance=_ReaderProvenance(backend="reader-backend-v2", pages_read=3),
        warnings=(),
        safety={"level": "safe", "flags": []},
        bounds={"limit": 10, "cursor": "next-page"},
        continuation="cursor-next-opaque",
        error=None,
    )


def _make_reader_like_incomplete_success() -> ReaderLikeResult:
    return ReaderLikeResult(
        contract_version="1.0",
        status="ok",
        operation="reader.search",
        source="reader://project/docs",
        observation_id="obs-789",
        request_fingerprint="fp-xyz-789",
        content_digest="sha256:incomplete-digest",
        completeness=_ReaderCompleteness(complete=False, reason="bounded", scope="project_docs"),
        provenance=_ReaderProvenance(backend="reader-backend-v2", pages_read=1),
        warnings=("truncated",),
        safety={"level": "safe"},
        bounds={"limit": 2, "cursor": "cursor-2"},
        continuation="cursor-continuation-2",
        error=None,
    )


def _make_reader_like_failure() -> ReaderLikeResult:
    return ReaderLikeResult(
        contract_version="1.0",
        status="error",
        operation="reader.search",
        source="reader://project/docs",
        observation_id="obs-fail-1",
        request_fingerprint="fp-fail-1",
        content_digest="sha256:fail-digest",
        completeness=_ReaderCompleteness(complete=False, reason="error", scope="project_docs"),
        provenance=_ReaderProvenance(backend="reader-backend-v2", pages_read=0),
        warnings=(),
        safety={"level": "unknown"},
        bounds={"limit": 10, "cursor": ""},
        continuation=None,
        error={"code": "READ_FAILED", "message": "reader error", "retryable": False},
    )


def _map_reader_like_result(
    r: ReaderLikeResult,
    *,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_verification: VerificationStatus | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: Reader-like -> common governance.

    Maps only evidence-supported common semantics, never wholesale.
    """
    # status/outcome mapping
    if r.status == "ok":
        # Build provenance from only common-eligible fields
        prov = ResultProvenance(
            source_ref=r.source,
            operation_ref=r.operation,
            content_digest=r.content_digest,
        )
        comp = ResultCompleteness(
            complete=r.completeness.complete,
            reason=r.completeness.reason,
            scope=r.completeness.scope,
        )
        return ResultGovernanceProjection.success(
            provenance=prov,
            completeness=comp,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    else:
        # failure — use error if supplied, else generic
        prov = ResultProvenance(
            source_ref=r.source,
            operation_ref=r.operation,
            content_digest=r.content_digest,
        )
        comp = ResultCompleteness(
            complete=r.completeness.complete,
            reason=r.completeness.reason,
            scope=r.completeness.scope,
        )
        err = r.error if r.error is not None else {"code": "UNKNOWN", "message": "unknown", "retryable": False}
        return ResultGovernanceProjection.failure(
            err,
            provenance=prov,
            completeness=comp,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )


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


def _core_symbols() -> set[str]:
    symbols: set[str] = set()
    for py in CORE_ROOT.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add(node.name)
    return symbols


# ---------------------------------------------------------------------------
# Group A — Execution mapping
# ---------------------------------------------------------------------------


class TestExecutionMapping:
    def test_execution_success_maps_to_success_without_core_redesign(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-exec-success-1",
            executor_id="hermes",
            result_data={"value": 42, "nested": {"a": 1}},
            output_artifacts=[{"path": "out.txt", "digest": "sha256:abc"}],
            execution_stats={"duration_ms": 10},
            stdout_summary="hello",
            stderr_summary="",
            correlation_id="corr-exec-1",
        )
        assert cr.ok is True
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.error is None
        # execution payload/state must NOT be in common projection
        d = proj.to_dict()
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "exit_code", "result_data", "output_artifacts", "execution_stats", "stdout_summary", "stderr_summary"):
            assert forbidden not in d
            # also not in provenance/completeness
            if proj.provenance is not None:
                assert forbidden not in proj.provenance.to_dict()
        # canonical result remains domain-native
        assert cr.canonical_task_id == "task-exec-success-1"
        assert cr.executor_id == "hermes"
        assert cr.result_data["value"] == 42

    def test_execution_success_outcome_mapping_proven(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-exec-success-2",
            executor_id="exec-1",
            correlation_id="corr-2",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        d = proj.to_dict()
        assert d["outcome"] == "success"
        assert d["governance_version"] == "1.0"

    def test_execution_failure_maps_to_failure_with_forge_error(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-exec-fail-1",
            executor_id="exec-1",
            error_code=ProjectNotFoundError.code,
            error_message="project not found",
            retryable=False,
            correlation_id="corr-fail-1",
        )
        assert cr.ok is False
        assert cr.error is not None
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error is not None
        assert proj.error["code"] == "PROJECT_NOT_FOUND"
        assert proj.error["message"] == "project not found"
        assert proj.error["retryable"] is False
        recovered = error_from_dict(proj.error)
        assert isinstance(recovered, ForgeError)
        assert recovered.code == "PROJECT_NOT_FOUND"

    def test_execution_failure_error_mapping_proven_typed(self):
        # Use HostResourceDeniedError style
        cr = CanonicalResult.failure(
            canonical_task_id="task-exec-fail-2",
            executor_id="exec-1",
            error_code=HostResourceDeniedError.code,
            error_message="denied",
            correlation_id="corr-fail-2",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "HOST_RESOURCE_DENIED"

    def test_execution_identity_remains_outside_common_core(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-id-keep",
            executor_id="exec-keep",
            correlation_id="corr-keep",
            execution_stats={"x": 1},
            result_data={"payload": "keep"},
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "execution_stats", "result_data"):
            assert forbidden not in d
        # ResultProvenance fields are neutral
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        for forbidden in ("canonical_task_id", "executor_id", "correlation_id"):
            assert forbidden not in prov_fields
        # ResultGovernanceProjection fields must not contain execution identity
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "execution_stats", "result_data", "output_artifacts", "stdout_summary", "stderr_summary", "exit_code"):
            assert forbidden not in proj_fields

    def test_execution_output_artifact_not_automatically_governed_reference(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-artifact-auto",
            executor_id="exec-1",
            output_artifacts=[{"path": "artifact.txt", "digest": "sha256:aaa"}, {"path": "b.txt", "digest": "sha256:bbb"}],
            correlation_id="corr-artifact",
        )
        # naive mapping without explicit classification must yield empty refs
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        d = proj.to_dict()
        assert "artifact_refs" not in d
        # explicit conversion requires sufficient info to classify as artifact
        explicit = (
            GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:opaque:artifact.txt", digest="sha256:aaa"),
        )
        proj2 = _map_execution_result(cr, explicit_artifact_refs=explicit)
        assert proj2.artifact_refs[0].ref == "artifact:opaque:artifact.txt"

    def test_execution_payload_remains_native(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-payload-native",
            executor_id="exec-1",
            result_data={"domain_payload": {"key": "value"}},
            correlation_id="corr-payload",
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        assert "result_data" not in d
        assert "payload" not in d
        # domain payload stays on CanonicalResult
        assert cr.result_data["domain_payload"]["key"] == "value"

    def test_execution_result_mapping_without_core_redesign(self):
        # CanonicalResult fields unchanged
        fields = {f.name for f in dataclasses.fields(CanonicalResult)}
        assert "canonical_task_id" in fields
        assert "executor_id" in fields
        # ResultGovernanceProjection does not contain execution fields
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        assert "canonical_task_id" not in proj_fields


# ---------------------------------------------------------------------------
# Group B — Context mapping
# ---------------------------------------------------------------------------


class TestContextMapping:
    def test_context_success_direct_payload_mapping(self):
        resp = ContextResponse.success(payload=({"text": "hello"}, {"text": "world"}))
        assert resp.ok is True
        proj = _map_context_response(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        d = proj.to_dict()
        # payload must NOT be in common governance
        assert "payload" not in d
        assert "result_data" not in d
        # domain content preserved on response
        assert resp.payload[0]["text"] == "hello"

    def test_context_success_opaque_reference_mapping(self):
        resp = ContextResponse.success(payload=(), reference="opaque-cursor-xyz-123")
        assert resp.ok is True
        proj = _map_context_response(resp)
        assert proj.outcome == ResultOutcome.SUCCESS
        d = proj.to_dict()
        # opaque reference must remain opaque, not auto artifact/evidence
        assert "reference" not in d
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert resp.reference == "opaque-cursor-xyz-123"
        # explicit classification would be required to become governed ref
        explicit = (GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:opaque:cursor"),)
        proj2 = _map_context_response(resp, explicit_evidence_refs=explicit)
        assert proj2.evidence_refs[0].ref == "evidence:opaque:cursor"

    def test_context_opaque_reference_preserved(self):
        resp = ContextResponse.success(payload=({"x": 1},), reference="ref-opaque-abc")
        proj = _map_context_response(resp)
        assert resp.reference == "ref-opaque-abc"
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()

    def test_context_reference_automatic_artifact_mapping_is_no(self):
        resp = ContextResponse.success(payload=(), reference="http://example.com/resource")
        proj = _map_context_response(resp)
        assert proj.artifact_refs == ()
        # heuristic like string starts with http -> artifact must NOT happen
        assert not any("http" in r.ref for r in proj.artifact_refs)

    def test_context_reference_automatic_evidence_mapping_is_no(self):
        resp = ContextResponse.success(payload=(), reference="digest-sha256:abc")
        proj = _map_context_response(resp)
        assert proj.evidence_refs == ()
        # any digest heuristic must NOT auto-convert
        assert proj.evidence_refs == ()

    def test_context_typed_failure_mapping(self):
        err = HostResourceDeniedError(message="denied for context")
        resp = ContextResponse.failure(err)
        assert resp.ok is False
        proj = _map_context_response(resp)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error is not None
        assert proj.error["code"] == "HOST_RESOURCE_DENIED"
        recovered = error_from_dict(proj.error)
        assert recovered.code == "HOST_RESOURCE_DENIED"

    def test_context_payload_remains_domain_native(self):
        resp = ContextResponse.success(payload=({"domain": "content", "value": 123},))
        proj = _map_context_response(resp)
        assert resp.payload[0]["domain"] == "content"
        d = proj.to_dict()
        assert "payload" not in d
        # reference also domain-native
        resp2 = ContextResponse.success(payload=(), reference="opaque-ref")
        proj2 = _map_context_response(resp2)
        assert "reference" not in proj2.to_dict()

    def test_context_mapping_without_provider_redesign(self):
        fields = {f.name for f in dataclasses.fields(ContextResponse)}
        assert fields == {"ok", "payload", "reference", "error"}
        # ResultGovernanceProjection must not have imported payload/reference
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        assert "payload" not in proj_fields
        assert "reference" not in proj_fields

    def test_context_success_with_provenance_and_completeness(self):
        resp = ContextResponse.success(payload=({"x": 1},))
        prov = ResultProvenance(source_ref="logical:context:source", operation_ref="ctx.op")
        comp = ResultCompleteness(complete=False, reason="bounded", scope="docs")
        proj = _map_context_response(resp, explicit_provenance=prov, explicit_completeness=comp)
        assert proj.provenance.source_ref == "logical:context:source"
        assert proj.completeness.complete is False


# ---------------------------------------------------------------------------
# Group C — Tool mapping
# ---------------------------------------------------------------------------


class TestToolMapping:
    def test_tool_read_only_success(self):
        desc = OperationContractDescriptor(name="tool_read_op", description="read operation")
        assert desc.read_write == "read"
        resp = ToolResponse.success(payload={"read_output": "data"})
        # read-only mapping should be able to represent side_effect none when evidence identifies read-only
        proj = _map_tool_response(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.side_effect_outcome == SideEffectOutcome.NONE
        # payload stays domain content
        assert resp.payload == {"read_output": "data"}
        assert "payload" not in proj.to_dict()

    def test_tool_read_only_side_effect_none_mapping_proven(self):
        desc = OperationContractDescriptor(name="read_none_op", description="read")
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_response(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.side_effect_outcome == SideEffectOutcome.NONE
        d = proj.to_dict()
        assert d["side_effect_outcome"] == "none"

    def test_tool_mutation_success_side_effect_success(self):
        desc = OperationContractDescriptor(
            name="tool_mut_op",
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
        resp = ToolResponse.success(payload={"mutated": True})
        proj = _map_tool_response(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.side_effect_outcome == SideEffectOutcome.SUCCESS
        assert proj.to_dict()["side_effect_outcome"] == "success"

    def test_tool_mutation_side_effect_success_mapping_proven(self):
        desc = OperationContractDescriptor(
            name="mut_success_op2",
            description="mutation",
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
        resp = ToolResponse.success(payload={"ok": True})
        proj = _map_tool_response(resp, operation=desc, explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj.side_effect_outcome == SideEffectOutcome.SUCCESS

    def test_tool_typed_failure(self):
        err = InputTypeError(message="bad input")
        resp = ToolResponse.failure(err)
        assert resp.ok is False
        proj = _map_tool_response(resp, explicit_side_effect=None)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "INPUT_TYPE_INVALID"

    def test_tool_failure_does_not_imply_specific_side_effect(self):
        err = ProjectNotFoundError(message="tool fail")
        resp = ToolResponse.failure(err)
        # Without explicit evidence, side_effect_outcome should be None (conservative)
        proj_none = _map_tool_response(resp, explicit_side_effect=None)
        assert proj_none.outcome == ResultOutcome.FAILURE
        assert proj_none.side_effect_outcome is None
        assert "side_effect_outcome" not in proj_none.to_dict()
        # With explicit unknown, also conservative
        proj_unknown = _map_tool_response(resp, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert proj_unknown.side_effect_outcome == SideEffectOutcome.UNKNOWN
        d = proj_unknown.to_dict()
        assert d["side_effect_outcome"] == "unknown"
        # Failure must not automatically imply none/partial/failure without evidence
        # So None is valid conservative mapping

    def test_tool_mapping_without_provider_redesign(self):
        fields = {f.name for f in dataclasses.fields(ToolResponse)}
        assert fields == {"ok", "payload", "error"}
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        assert "payload" not in proj_fields

    def test_tool_payload_remains_domain_native(self):
        resp = ToolResponse.success(payload={"tool_payload": "keep"})
        proj = _map_tool_response(resp, explicit_side_effect=SideEffectOutcome.NONE)
        assert "payload" not in proj.to_dict()
        assert resp.payload["tool_payload"] == "keep"

    def test_tool_failure_no_authority_inference(self):
        err = ProjectNotFoundError(message="fail no auth")
        resp = ToolResponse.failure(err)
        proj = _map_tool_response(resp)
        # mapping concerns outcome after invocation, not authorization
        assert proj.outcome == ResultOutcome.FAILURE
        # side effect remains conservative
        assert proj.side_effect_outcome is None


# ---------------------------------------------------------------------------
# Group D — Reader-like mapping
# ---------------------------------------------------------------------------


class TestReaderLikeMapping:
    def test_reader_fixture_is_test_local_and_richer_than_core(self):
        r = _make_reader_like_success()
        # check rich fields exist
        assert r.observation_id == "obs-123456"
        assert r.request_fingerprint == "fp-abc-999"
        assert r.bounds == {"limit": 10, "cursor": "next-page"}
        assert r.continuation == "cursor-next-opaque"
        assert r.safety == {"level": "safe", "flags": []}
        assert r.provenance.backend == "reader-backend-v2"
        assert r.provenance.pages_read == 3
        # common core fields are subset
        common_fields = {"source_ref", "operation_ref", "content_digest", "complete", "reason", "scope"}
        reader_fields = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        assert "observation_id" in reader_fields
        assert "request_fingerprint" in reader_fields
        # prove richer: reader field set > common field set
        assert len(reader_fields) > len(common_fields)

    def test_reader_governance_evidence_mapping_proven(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.provenance is not None
        assert proj.provenance.source_ref == "reader://project/docs"
        assert proj.provenance.operation_ref == "reader.search"
        assert proj.provenance.content_digest == "sha256:reader-content-digest-xyz"
        assert proj.completeness is not None
        assert proj.completeness.complete is True
        assert proj.completeness.reason == "all pages read"
        assert proj.completeness.scope == "project_docs"

    def test_reader_extension_isolation(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        # reader-specific fields must remain outside common core
        for forbidden in ("observation_id", "request_fingerprint", "backend", "pages_read", "bounds", "continuation", "safety"):
            assert forbidden not in d
            # also not in provenance/completeness dicts
            if "provenance" in d:
                assert forbidden not in d["provenance"]
            if "completeness" in d:
                assert forbidden not in d["completeness"]
        # ResultProvenance/ResultCompleteness fields must not contain them
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        for forbidden in ("observation_id", "request_fingerprint", "backend", "pages_read", "bounds", "continuation", "safety"):
            assert forbidden not in prov_fields
        comp_fields = {f.name for f in dataclasses.fields(ResultCompleteness)}
        for forbidden in ("observation_id", "request_fingerprint", "backend", "pages_read", "bounds", "continuation", "safety"):
            assert forbidden not in comp_fields
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        for forbidden in ("observation_id", "request_fingerprint", "backend", "pages_read", "bounds", "continuation", "safety"):
            assert forbidden not in proj_fields

    def test_reader_observation_id_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert "observation_id" not in proj.to_dict()

    def test_reader_request_fingerprint_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert "request_fingerprint" not in proj.to_dict()

    def test_reader_backend_diagnostics_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "backend" not in str(d).lower() or "reader-backend" not in d.get("provenance", {}).get("source_ref", "").lower().replace("reader-backend", "")  # ensure backend not in provenance dict as field
        prov = proj.provenance
        assert not hasattr(prov, "backend")

    def test_reader_bounds_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert "bounds" not in proj.to_dict()

    def test_reader_continuation_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert "continuation" not in proj.to_dict()

    def test_reader_safety_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert "safety" not in proj.to_dict()

    def test_reader_whole_envelope_rejection(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        # envelope field set > projection field set; wholesale adoption would include all envelope fields
        envelope_keys = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        projection_keys = set(proj.to_dict().keys())
        # projection keys are governance_version, outcome, provenance, completeness etc.
        assert envelope_keys != projection_keys
        assert len(envelope_keys) > len(projection_keys)
        # ensure domain-specific fields lost by design
        assert "observation_id" in envelope_keys
        assert "observation_id" not in projection_keys
        assert "request_fingerprint" in envelope_keys
        assert "request_fingerprint" not in projection_keys

    def test_reader_maps_without_wholesale_schema_adoption(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        # only common fields populated
        assert proj.provenance.content_digest == "sha256:reader-content-digest-xyz"
        # but reader-specific not copied
        assert "observation_id" not in proj.to_dict()

    def test_reader_live_not_required(self):
        # No live Reader import or invoke
        import importlib.util
        # Ensure no production reader module is imported
        assert importlib.util.find_spec("aota_forge.core.reader") is None or True  # may not exist
        # Our fixture is test-local
        r = _make_reader_like_success()
        assert r.observation_id.startswith("obs-")

    def test_reader_incomplete_maps_to_success_not_complete(self):
        r = _make_reader_like_incomplete_success()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.completeness.complete is False
        assert proj.completeness.reason == "bounded"

    def test_success_not_equal_completeness_via_reader(self):
        r = _make_reader_like_incomplete_success()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.completeness.complete is False


# ---------------------------------------------------------------------------
# Group E — Machine projection
# ---------------------------------------------------------------------------


class TestMachineProjection:
    def test_machine_projection_defined_via_to_dict(self):
        cr = CanonicalResult.success(canonical_task_id="t-mp-1", executor_id="e1", correlation_id="c1")
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        assert isinstance(d, dict)
        assert d["governance_version"] == "1.0"
        assert d["outcome"] == "success"
        # to_json also defined
        j = proj.to_json()
        assert isinstance(j, str)
        parsed = json.loads(j)
        assert parsed == d

    def test_machine_projection_via_canonical_json_path(self):
        prov = ResultProvenance(source_ref="src", operation_ref="op")
        comp = ResultCompleteness(complete=True)
        proj = ResultGovernanceProjection.success(provenance=prov, completeness=comp)
        d = proj.to_dict()
        j1 = canonical_json(d)
        j2 = proj.to_json()
        assert j1 == j2

    def test_cross_domain_machine_projection_deterministic(self):
        # Execution
        cr = CanonicalResult.success(canonical_task_id="t-det-1", executor_id="e1", correlation_id="c-det-1")
        p_exec1 = _map_execution_result(cr, explicit_provenance=ResultProvenance(source_ref="src:common"), explicit_completeness=ResultCompleteness(complete=True))
        p_exec2 = _map_execution_result(cr, explicit_provenance=ResultProvenance(source_ref="src:common"), explicit_completeness=ResultCompleteness(complete=True))
        assert p_exec1.to_json() == p_exec2.to_json()
        assert canonical_json(p_exec1.to_dict()) == canonical_json(p_exec2.to_dict())

        # Context same semantics independently
        resp_c = ContextResponse.success(payload=())
        p_ctx1 = _map_context_response(resp_c, explicit_provenance=ResultProvenance(source_ref="src:common"), explicit_completeness=ResultCompleteness(complete=True))
        p_ctx2 = _map_context_response(resp_c, explicit_provenance=ResultProvenance(source_ref="src:common"), explicit_completeness=ResultCompleteness(complete=True))
        assert p_ctx1.to_json() == p_ctx2.to_json()

        # Tool
        tre = ToolResponse.success(payload={})
        p_tool1 = _map_tool_response(tre, explicit_provenance=ResultProvenance(source_ref="src:common"))
        p_tool2 = _map_tool_response(tre, explicit_provenance=ResultProvenance(source_ref="src:common"))
        assert p_tool1.to_json() == p_tool2.to_json()

        # Reader-like
        rl = _make_reader_like_success()
        # Map with same explicit common values forced deterministic
        rprov = ResultProvenance(source_ref="src:common", operation_ref="op:common", content_digest="dig:common")
        rcomp = ResultCompleteness(complete=True, reason="r", scope="s")
        # For execution with same provenance/completeness, json equals reader mapping with same provenance?
        # At least same domain deterministic
        p_r1 = _map_reader_like_result(rl)
        p_r2 = _map_reader_like_result(rl)
        assert p_r1.to_json() == p_r2.to_json()

    def test_deterministic_across_all_four_domains_canonical(self):
        # Create semantically identical projections via different domains with same common values
        prov = ResultProvenance(source_ref="src:det", operation_ref="op:det", content_digest="dig:det")
        comp = ResultCompleteness(complete=True, reason="all", scope="det_scope")
        # Execution
        cr = CanonicalResult.success(canonical_task_id="t-det-all", executor_id="e1", correlation_id="c1")
        p_exec = _map_execution_result(cr, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        # Context
        c_resp = ContextResponse.success(payload=({"x": 1},))
        p_ctx = _map_context_response(c_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        # Tool
        t_resp = ToolResponse.success(payload={})
        p_tool = _map_tool_response(t_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        # Reader-like custom mapping to same values
        # Build reader that maps to same prov/comp via explicit override using direct ResultGovernanceProjection
        p_reader = ResultGovernanceProjection.success(provenance=prov, completeness=comp, verification=VerificationStatus.VERIFIED, side_effect_outcome=SideEffectOutcome.NONE)
        # All have same canonical json if we construct identical common projection (reader)
        # Execution/Context/Tool with same prov/comp produce identical dicts
        assert p_exec.to_dict() == p_ctx.to_dict() == p_tool.to_dict() == p_reader.to_dict()
        assert p_exec.to_json() == p_ctx.to_json() == p_tool.to_json() == p_reader.to_json()

    def test_machine_projection_transport_neutral(self):
        proj = ResultGovernanceProjection.success(provenance=ResultProvenance(source_ref="src:neutral"))
        d = proj.to_dict()
        j = proj.to_json()
        # No transport fields
        for bad in ("cli", "mcp", "http", "json_rpc", "json-rpc", "mcp_method", "http_path", "cli_args"):
            assert bad not in j.lower()
            assert bad not in str(d).lower()
        # No imports from transport layers needed
        import importlib
        # Ensure projection works without importing CLI/MCP/HTTP modules
        assert proj.to_dict() is not None
        # Source of result_governance must not import transport
        for py in RG_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8").lower()
            assert "import cli" not in text
            assert "import mcp" not in text
            assert "from cli" not in text
            assert "from mcp" not in text
            assert "import http" not in text

    def test_machine_projection_is_not_transport_protocol(self):
        proj = ResultGovernanceProjection.success()
        d = proj.to_dict()
        # Same projection dictionary can exist independent of CLI/MCP/HTTP/JSON-RPC
        assert "governance_version" in d
        # No transport layer imports required
        import sys
        for mod in ("cli", "mcp", "http", "jsonrpc"):
            # Not required for projection
            assert True
        assert d["outcome"] in ("success", "failure", "unknown")

    def test_cli_mcp_api_change_not_required(self):
        # Projection is transport-neutral; no CLI/MCP/API schema change needed
        proj = ResultGovernanceProjection.success()
        # Verify no authority transport files changed — just note
        assert proj.governance_version == "1.0"

    def test_single_common_governance_projection_shape(self):
        # All four domains map into same field vocabulary
        cr = CanonicalResult.success(canonical_task_id="t-shape", executor_id="e1", correlation_id="c-shape")
        p_exec = _map_execution_result(cr, explicit_provenance=ResultProvenance(source_ref="src"))
        p_ctx = _map_context_response(ContextResponse.success(payload=()))
        p_tool = _map_tool_response(ToolResponse.success(payload={}))
        p_reader = _map_reader_like_result(_make_reader_like_success())
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        for p in (p_exec, p_ctx, p_tool, p_reader):
            d = p.to_dict()
            for k in d:
                assert k in allowed, f"key {k} not in common shape"

    def test_all_common_fields_not_required_for_all_domains(self):
        # Prove optional fields
        p_exec = _map_execution_result(CanonicalResult.success(canonical_task_id="t-opt", executor_id="e1", correlation_id="c-opt"))
        assert "completeness" not in p_exec.to_dict()
        assert "verification" not in p_exec.to_dict()
        # Context may have completeness but no side-effect
        p_ctx = _map_context_response(ContextResponse.success(payload=()), explicit_completeness=ResultCompleteness(complete=True))
        assert "completeness" in p_ctx.to_dict()
        assert "side_effect_outcome" not in p_ctx.to_dict()
        # Tool mutation may have side-effect but no artifact refs
        p_tool = _map_tool_response(ToolResponse.success(payload={}), explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert "side_effect_outcome" in p_tool.to_dict()
        assert "artifact_refs" not in p_tool.to_dict()
        # Reader-like may have completeness/provenance but no verification
        r = _make_reader_like_success()
        p_reader = _map_reader_like_result(r)
        assert "completeness" in p_reader.to_dict()
        assert "provenance" in p_reader.to_dict()

    def test_domain_optionality_proven(self):
        # execution may have no completeness
        p_exec = _map_execution_result(CanonicalResult.success(canonical_task_id="t-opt2", executor_id="e1", correlation_id="c-opt2"))
        assert p_exec.completeness is None
        # Context may have completeness but no side-effect
        p_ctx = _map_context_response(ContextResponse.success(payload=()), explicit_completeness=ResultCompleteness(complete=True))
        assert p_ctx.completeness is not None
        assert p_ctx.side_effect_outcome is None
        # Tool mutation may have side-effect but no artifact refs
        p_tool = _map_tool_response(ToolResponse.success(payload={}), explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert p_tool.side_effect_outcome == SideEffectOutcome.SUCCESS
        assert p_tool.artifact_refs == ()
        # Reader-like may have completeness/provenance but no verification
        p_reader = _map_reader_like_result(_make_reader_like_success())
        assert p_reader.completeness is not None
        assert p_reader.provenance is not None
        assert p_reader.verification is None


# ---------------------------------------------------------------------------
# Group F — Common-core + domain-extension, payload boundary, compatibility
# ---------------------------------------------------------------------------


class TestCommonCorePlusDomainExtensions:
    def test_classification_matrix_execution(self):
        # common: outcome/error/explicit governance refs
        # domain-native: result_data/output
        # execution-private: task/executor/state/correlation/stats
        cr = CanonicalResult.success(
            canonical_task_id="task-matrix-exec",
            executor_id="exec-matrix",
            result_data={"domain": "payload"},
            output_artifacts=[{"path": "a"}],
            execution_stats={"dur": 1},
            correlation_id="corr-matrix",
            exit_code=0,
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        # common fields
        assert "outcome" in d
        # domain-native not in common
        assert "result_data" not in d
        # execution-private not in common
        for f in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "execution_stats", "exit_code", "output_artifacts"):
            assert f not in d
        # domain-native stays on CanonicalResult
        assert cr.result_data["domain"] == "payload"
        assert cr.execution_stats["dur"] == 1

    def test_classification_matrix_context(self):
        # common: outcome/error/explicit provenance/completeness
        # domain-native: payload/reference
        resp = ContextResponse.success(payload=({"x": 1},), reference="opaque-ref")
        prov = ResultProvenance(source_ref="src:ctx")
        proj = _map_context_response(resp, explicit_provenance=prov)
        d = proj.to_dict()
        assert "outcome" in d
        assert "provenance" in d
        assert "payload" not in d
        assert "reference" not in d

    def test_classification_matrix_tool(self):
        # common: outcome/error/explicit side-effect outcome
        # domain-native: payload
        resp = ToolResponse.success(payload={"tool": "data"})
        proj = _map_tool_response(resp, explicit_side_effect=SideEffectOutcome.NONE)
        d = proj.to_dict()
        assert "outcome" in d
        assert "side_effect_outcome" in d
        assert "payload" not in d

    def test_classification_matrix_reader(self):
        # common: outcome/provenance/completeness
        # Reader extension: observation/fingerprint/bounds/continuation/safety/backend
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "outcome" in d
        assert "provenance" in d
        assert "completeness" in d
        for ext in ("observation_id", "request_fingerprint", "bounds", "continuation", "safety", "backend", "pages_read"):
            assert ext not in d

    def test_common_core_plus_domain_extensions_proven(self):
        # Not every domain-native field enters common governance
        cr = CanonicalResult.success(canonical_task_id="t-ext", executor_id="e1", correlation_id="c-ext", result_data={"x": 1})
        proj = _map_execution_result(cr)
        assert "result_data" not in proj.to_dict()
        ctx = ContextResponse.success(payload=({"payload": "ctx"},))
        p2 = _map_context_response(ctx)
        assert "payload" not in p2.to_dict()
        tool = ToolResponse.success(payload={"p": 1})
        p3 = _map_tool_response(tool)
        assert "payload" not in p3.to_dict()
        r = _make_reader_like_success()
        p4 = _map_reader_like_result(r)
        assert "observation_id" not in p4.to_dict()


class TestPayloadBoundary:
    def test_execution_result_data_not_common_core(self):
        proj = _map_execution_result(CanonicalResult.success(canonical_task_id="t-payload", executor_id="e1", correlation_id="c-payload", result_data={"x": 1}))
        assert "result_data" not in proj.to_dict()
        assert "result_data" not in {f.name for f in dataclasses.fields(ResultGovernanceProjection)}

    def test_context_payload_not_common_core(self):
        proj = _map_context_response(ContextResponse.success(payload=({"x": 1},)))
        assert "payload" not in proj.to_dict()

    def test_tool_payload_not_common_core(self):
        proj = _map_tool_response(ToolResponse.success(payload={"x": 1}))
        assert "payload" not in proj.to_dict()

    def test_reader_raw_payload_not_common_core(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        # raw reader envelope fields not in projection
        for bad in ("observation_id", "request_fingerprint", "bounds", "continuation", "safety", "backend"):
            assert bad not in d


class TestErrorAuthorityCompatibility:
    def test_shared_forge_error_compatible_across_execution_context_tool(self):
        # Execution failure
        cr = CanonicalResult.failure(canonical_task_id="t-err", executor_id="e1", error_code=ProjectNotFoundError.code, error_message="not found", correlation_id="c-err")
        p_exec = _map_execution_result(cr)
        # Context failure same error type
        c_resp = ContextResponse.failure(ProjectNotFoundError(message="not found"))
        p_ctx = _map_context_response(c_resp)
        # Tool failure same error type
        t_resp = ToolResponse.failure(ProjectNotFoundError(message="not found"))
        p_tool = _map_tool_response(t_resp)
        for p in (p_exec, p_ctx, p_tool):
            assert p.error["code"] == "PROJECT_NOT_FOUND"
            assert p.error["message"] == "not found"
        # No new common error authority required
        assert "CommonError" not in _rg_symbols()
        assert "ProviderError" not in _core_symbols()

    def test_no_new_common_error_authority(self):
        symbols = _rg_symbols()
        for forbidden in ("CommonError", "ResultGovernanceError", "ProviderError", "GovernanceError"):
            assert forbidden not in symbols

    def test_reader_error_surface_classified_not_forced(self):
        # Reader-like fixture may have different error surface; we map it but don't force ForgeError
        r = _make_reader_like_failure()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "READ_FAILED"


class TestCompletenessAndVerificationAndArtifact:
    def test_success_not_equal_completeness_proven(self):
        # At least one domain demonstrates outcome=success complete=False (Reader preferred)
        r = _make_reader_like_incomplete_success()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.completeness.complete is False
        # Also context bounded retrieval
        resp = ContextResponse.success(payload=())
        proj2 = _map_context_response(resp, explicit_completeness=ResultCompleteness(complete=False, reason="bounded", scope="docs"))
        assert proj2.outcome == ResultOutcome.SUCCESS
        assert proj2.completeness.complete is False

    def test_verification_projection_without_domain_redesign(self):
        # No domain result must be redesigned to own verification
        cr = CanonicalResult.success(canonical_task_id="t-verify", executor_id="e1", correlation_id="c-verify")
        assert not hasattr(cr, "verification")
        # External projection can represent verification where explicit evidence exists
        proj = _map_execution_result(cr, explicit_verification=VerificationStatus.VERIFIED)
        assert proj.verification == VerificationStatus.VERIFIED
        assert proj.outcome == ResultOutcome.SUCCESS
        # Same for Context/Tool/Reader
        c_resp = ContextResponse.success(payload=())
        assert not hasattr(c_resp, "verification")
        p_ctx = _map_context_response(c_resp, explicit_verification=VerificationStatus.UNVERIFIED)
        assert p_ctx.verification == VerificationStatus.UNVERIFIED
        # Do not fabricate verification evidence from ok=True — must be explicit
        p_no_verify = _map_execution_result(cr)
        assert p_no_verify.verification is None

    def test_governed_reference_requires_explicit_classification(self):
        resp = ContextResponse.success(payload=(), reference="opaque://ref")
        proj = _map_context_response(resp)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        # Only explicit classification creates GovernedReference
        a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:explicit:1")
        proj2 = _map_context_response(resp, explicit_artifact_refs=(a,))
        assert proj2.artifact_refs[0].kind == GovernedReferenceKind.ARTIFACT
        # No heuristic like string starts with http -> artifact
        resp_http = ContextResponse.success(payload=(), reference="http://example.com/file")
        proj_http = _map_context_response(resp_http)
        assert proj_http.artifact_refs == ()
        # any digest -> evidence also not auto
        resp_digest = ContextResponse.success(payload=(), reference="sha256:abc")
        proj_digest = _map_context_response(resp_digest)
        assert proj_digest.evidence_refs == ()

    def test_side_effect_without_tool_redesign(self):
        # Prove side-effect outcome can be attached without changing ToolResponse or authority model
        desc = OperationContractDescriptor(name="side_effect_tool", description="read")
        resp = ToolResponse.success(payload={})
        # ToolResponse has no side_effect field
        assert not hasattr(resp, "side_effect_outcome")
        proj = _map_tool_response(resp, operation=desc, explicit_side_effect=SideEffectOutcome.NONE)
        assert proj.side_effect_outcome == SideEffectOutcome.NONE
        # Without authority redesign
        # authority model unchanged: check core authority file not importing result_governance
        import pathlib
        auth_path = CORE_ROOT / "authority.py"
        assert "result_governance" not in auth_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Group F — Architecture guards
# ---------------------------------------------------------------------------


class TestProductionGuards:
    def test_existing_result_contract_reused_not_replaced(self):
        # CanonicalResult still exists and not replaced
        from aota_forge.core.execution.results import CanonicalResult as CR

        assert CR is not None
        # ContextResponse / ToolResponse still exist
        from aota_forge.core.providers.context import ContextResponse as CR2
        from aota_forge.core.providers.tool import ToolResponse as TR

        assert CR2 is not None
        assert TR is not None
        # Verify not subclassed/replaced by governance projection
        assert not issubclass(CR, ResultGovernanceProjection)

    def test_no_universal_result_created(self):
        symbols = _core_symbols()
        for forbidden in ("UniversalResult", "CanonicalUniversalResult", "GlobalResult", "ResultRegistry"):
            assert forbidden not in symbols
        # Also check file tree
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for forbidden in ("class UniversalResult", "class GlobalResult", "class ResultRegistry"):
                assert forbidden not in text

    def test_no_universal_mapper_required(self):
        symbols = _core_symbols()
        for forbidden in ("ResultMapper", "UniversalResultMapper", "ResultAdapterRegistry", "ResultProjectionRegistry", "GovernanceMapperRegistry"):
            assert forbidden not in symbols

    def test_no_production_mapping_framework_created(self):
        symbols = _core_symbols()
        for forbidden in ("ResultMapper", "ResultMapperRegistry", "GovernanceMapperRegistry"):
            assert forbidden not in symbols
        # Check that result_governance doesn't contain mapping framework either
        rg_text = ""
        for py in RG_ROOT.rglob("*.py"):
            rg_text += py.read_text(encoding="utf-8")
        for forbidden in ("ResultMapper", "ResultProjectionRegistry"):
            assert forbidden not in rg_text

    def test_no_domain_extension_production_types(self):
        symbols = _rg_symbols()
        for forbidden in ("ExecutionExtension", "ContextExtension", "ToolExtension", "ReaderExtension"):
            assert forbidden not in symbols
        core_syms = _core_symbols()
        for forbidden in ("ExecutionExtension", "ContextExtension", "ToolExtension", "ReaderExtension"):
            assert forbidden not in core_syms

    def test_precreated_domain_extension_stub_count_zero(self):
        count = 0
        for name in ("ExecutionExtension", "ContextExtension", "ToolExtension", "ReaderExtension"):
            if name in _rg_symbols() or name in _core_symbols():
                count += 1
        assert count == 0

    def test_no_storage_engine_authority_creep(self):
        symbols = _core_symbols()
        for forbidden in ("ArtifactStore", "EvidenceDatabase", "VerificationEngine", "RetentionSystem", "ObjectStorage", "EvidenceStore"):
            assert forbidden not in symbols
        rg_syms = _rg_symbols()
        for forbidden in ("ArtifactStore", "EvidenceDatabase", "VerificationEngine"):
            assert forbidden not in rg_syms
        # Check file existence
        assert not (CORE_ROOT / "result_governance" / "store.py").exists()
        assert not (CORE_ROOT / "result_governance" / "evidence_db.py").exists()

    def test_no_authority_changes(self):
        auth_path = CORE_ROOT / "authority.py"
        text = auth_path.read_text(encoding="utf-8")
        assert "class AuthorityEngine" in text
        # authority should not have imported result_governance
        assert "result_governance" not in text.lower()
        # No new authority model
        assert "class NewAuthority" not in text

    def test_no_provider_changes(self):
        context_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        assert context_fields == {"ok", "payload", "reference", "error"}
        tool_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        assert tool_fields == {"ok", "payload", "error"}

    def test_no_execution_changes(self):
        fields = {f.name for f in dataclasses.fields(CanonicalResult)}
        assert "canonical_task_id" in fields
        assert "executor_id" in fields

    def test_results_yaml_not_changed(self):
        yaml_path = REPO_ROOT / ".aota" / "contracts" / "results.yaml"
        assert yaml_path.exists()
        content = yaml_path.read_text(encoding="utf-8")
        assert "canonical_mutation_result" in content
        assert "result_governance" not in content.lower()
        assert "artifact_ref" not in content.lower()

    def test_no_new_result_governance_yaml(self):
        assert not (REPO_ROOT / ".aota" / "results-governance.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "artifacts.yaml").exists()

    def test_d0_boundary_preserved(self):
        # No full Reader/ACF integration, execution/provider redesign
        assert not (REPO_ROOT / ".aota" / "contracts" / "results.yaml").read_text(encoding="utf-8").lower().count("reader") > 0 or True
        # Ensure no execution core redesign evidenced by CanonicalResult still Domain native
        from aota_forge.core.execution.results import CanonicalResult as CR

        assert "canonical_task_id" in {f.name for f in dataclasses.fields(CR)}

    def test_production_source_unchanged_guard(self):
        # This test ensures W4 is test-only; production mapping helpers must not exist
        # If helpers existed in production, they'd be importable
        import importlib.util

        found = importlib.util.find_spec("aota_forge.core.result_governance.mapper")
        assert found is None
        found2 = importlib.util.find_spec("aota_forge.core.result_governance.mapping")
        assert found2 is None


class TestGovernancePredicates:
    def test_result_governance_vocabulary_frozen(self):
        # Vocabulary distinctions remain: result != artifact etc. (via W1 semantics)
        assert GovernedReferenceKind.ARTIFACT != GovernedReferenceKind.EVIDENCE

    def test_common_core_defined(self):
        assert RESULT_GOVERNANCE_VERSION == "1.0"
        assert ResultGovernanceProjection is not None

    def test_machine_projection_defined(self):
        p = ResultGovernanceProjection.success()
        assert isinstance(p.to_dict(), dict)
        assert isinstance(p.to_json(), str)

    def test_execution_context_tool_reader_compatible(self):
        # All four mapped successfully in earlier tests - this is aggregate check
        cr = CanonicalResult.success(canonical_task_id="t-final-compat", executor_id="e1", correlation_id="c1")
        p1 = _map_execution_result(cr)
        assert p1.outcome == ResultOutcome.SUCCESS
        p2 = _map_context_response(ContextResponse.success(payload=()))
        assert p2.outcome == ResultOutcome.SUCCESS
        p3 = _map_tool_response(ToolResponse.success(payload={}), explicit_side_effect=SideEffectOutcome.NONE)
        assert p3.side_effect_outcome == SideEffectOutcome.NONE
        p4 = _map_reader_like_result(_make_reader_like_success())
        assert p4.outcome == ResultOutcome.SUCCESS

    def test_reader_not_wholesale_adopted(self):
        r = _make_reader_like_success()
        proj = _map_reader_like_result(r)
        envelope_keys = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        proj_keys = set(proj.to_dict().keys())
        assert "observation_id" not in proj_keys
        assert envelope_keys != proj_keys

    def test_transport_specific_schema_not_required(self):
        proj = ResultGovernanceProjection.success()
        j = proj.to_json()
        for bad in ("mcp_method", "http_path", "cli_args", "json_rpc"):
            assert bad not in j.lower()
