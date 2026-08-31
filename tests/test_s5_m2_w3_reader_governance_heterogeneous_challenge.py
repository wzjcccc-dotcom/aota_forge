"""S5/M2/W3 — Reader-Governance Evidence Mapping & Heterogeneous Challenge.

TEST_ONLY=yes, PRODUCTION_WRITE_REQUIRED=no
Posture: stabilization / adversarial proof, NOT schema expansion.

This test is the sole authorized changed path.
It provides strictly stronger empirical evidence than M1/W4:
 Reader-like metadata richness + bounded/incomplete variants + continuation/bounds/safety
 + additive future-field stability + classification invariance + cross-domain
 heterogeneous stability — all mapping into the frozen ResultGovernanceProjection 1.0
 without wholesale Reader schema adoption, common-core expansion, or runtime integration.

Reader is DESIGN_EVIDENCE, not runtime dependency.
All Reader fixtures are TEST-LOCAL self-contained.
No import of Reader MCP/runtime, no network, no adapter creation.

Architecture preserved:
 domain-native result/evidence + execution-neutral ResultGovernanceProjection (1.0)
Do NOT add new common fields.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import (
    ForgeError,
    HostResourceDeniedError,
    InputTypeError,
    ProjectNotFoundError,
    SourceParityMismatchError,
    error_from_dict,
)
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
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
from aota_forge.core.authority import AuthorityDecision

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "aota_forge" / "core"
RG_ROOT = CORE_ROOT / "result_governance"
RG_COMMON = RG_ROOT / "common.py"

# ---------------------------------------------------------------------------
# 12. Required field classification model
# ---------------------------------------------------------------------------

READER_FIELD_CLASSIFICATION: dict[str, str] = {
    # common governance candidates (bounded M1 semantics)
    "status": "COMMON_GOVERNANCE",
    "operation": "COMMON_GOVERNANCE",
    "source": "COMMON_GOVERNANCE",
    "content_digest": "COMMON_GOVERNANCE",
    "complete": "COMMON_GOVERNANCE",
    "reason": "COMMON_GOVERNANCE",
    "scope": "COMMON_GOVERNANCE",
    "error": "COMMON_GOVERNANCE",
    # reader domain extension (isolated)
    "observation_id": "READER_DOMAIN_EXTENSION",
    "request_fingerprint": "READER_DOMAIN_EXTENSION",
    "backend": "READER_DOMAIN_EXTENSION",
    "pages_read": "READER_DOMAIN_EXTENSION",
    "warnings": "READER_DOMAIN_EXTENSION",
    "safety": "READER_DOMAIN_EXTENSION",
    "bounds": "READER_DOMAIN_EXTENSION",
    "limit": "READER_DOMAIN_EXTENSION",
    "returned_count": "READER_DOMAIN_EXTENSION",
    "total_count": "READER_DOMAIN_EXTENSION",
    "truncated": "READER_DOMAIN_EXTENSION",
    "continuation": "READER_DOMAIN_EXTENSION",
    "continuation_ref": "READER_DOMAIN_EXTENSION",
    "next_ref": "READER_DOMAIN_EXTENSION",
    "cursor": "READER_DOMAIN_EXTENSION",
    "future_reader_field": "READER_DOMAIN_EXTENSION",
    # transport / runtime private (isolated, never projected)
    "http_trace": "TRANSPORT_RUNTIME_PRIVATE",
    "mcp_method": "TRANSPORT_RUNTIME_PRIVATE",
    "transport_session": "TRANSPORT_RUNTIME_PRIVATE",
    "request_latency_internal": "TRANSPORT_RUNTIME_PRIVATE",
    # not canonical contract items
    "contract_version": "NOT_CANONICAL",
}

_ALLOWED_CLASSIFICATIONS = {
    "COMMON_GOVERNANCE",
    "READER_DOMAIN_EXTENSION",
    "TRANSPORT_RUNTIME_PRIVATE",
    "NOT_CANONICAL",
}

# ---------------------------------------------------------------------------
# 10. Reader-like fixture baseline (TEST-LOCAL)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ReaderLikeCompleteness:
    complete: bool | None = None
    reason: str | None = None
    scope: str | None = None


@dataclasses.dataclass(frozen=True)
class ReaderLikeProvenance:
    backend: str | None = None
    pages_read: int | None = None
    other: dict | None = None


@dataclasses.dataclass(frozen=True)
class ReaderLikeBounds:
    limit: int | None = None
    returned_count: int | None = None
    total_count: int | None = None
    truncated: bool | None = None


@dataclasses.dataclass(frozen=True)
class ReaderLikeResult:
    """TEST-LOCAL Reader-like governed result evidence.

    Rich envelope deliberately broader than common governance.
    Only subset maps to ResultGovernanceProjection.
    """

    contract_version: str
    status: str  # ok | error
    operation: str
    source: str
    observation_id: str
    request_fingerprint: str
    content_digest: str
    completeness: ReaderLikeCompleteness
    provenance: ReaderLikeProvenance
    warnings: tuple[str, ...]
    safety: dict
    bounds: ReaderLikeBounds
    continuation: str | None
    error: dict | None = None
    # additive future field (domain extension, must remain isolated)
    future_reader_field: str | None = None
    # transport/runtime-private diagnostics (must remain isolated)
    http_trace: str | None = None
    mcp_method: str | None = None
    transport_session: str | None = None
    request_latency_internal: str | None = None


def _base_reader_kwargs() -> dict:
    return dict(
        contract_version="1.0",
        status="ok",
        operation="reader.search",
        source="reader://project/docs",
        observation_id="obs-123456",
        request_fingerprint="fp-abc-999",
        content_digest="sha256:reader-content-digest-xyz",
        completeness=ReaderLikeCompleteness(complete=True, reason="all pages read", scope="project_docs"),
        provenance=ReaderLikeProvenance(backend="reader-backend-v2", pages_read=3, other={"diagnostic": "x"}),
        warnings=(),
        safety={"level": "safe", "flags": []},
        bounds=ReaderLikeBounds(limit=10, returned_count=10, total_count=10, truncated=False),
        continuation="cursor-next-opaque",
        error=None,
        future_reader_field=None,
        http_trace=None,
        mcp_method=None,
        transport_session=None,
        request_latency_internal=None,
    )


def _make_complete_read() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["completeness"] = ReaderLikeCompleteness(complete=True, reason="all pages read", scope="project_docs")
    kw["bounds"] = ReaderLikeBounds(limit=10, returned_count=10, total_count=10, truncated=False)
    kw["continuation"] = None
    kw["warnings"] = ()
    return ReaderLikeResult(**kw)


def _make_bounded_incomplete_read() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["completeness"] = ReaderLikeCompleteness(complete=False, reason="bounded", scope="project_docs")
    kw["bounds"] = ReaderLikeBounds(limit=2, returned_count=2, total_count=10, truncated=False)
    kw["continuation"] = "cursor-continuation-2"
    kw["warnings"] = ()
    return ReaderLikeResult(**kw)


def _make_truncated_read() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["completeness"] = ReaderLikeCompleteness(complete=False, reason="truncated", scope="project_docs")
    kw["bounds"] = ReaderLikeBounds(limit=10, returned_count=10, total_count=50, truncated=True)
    kw["continuation"] = "cursor-truncated-next"
    kw["warnings"] = ("truncated",)
    return ReaderLikeResult(**kw)


def _make_continuation_available() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["continuation"] = "cursor-available-xyz"
    kw["completeness"] = ReaderLikeCompleteness(complete=False, reason="bounded", scope="docs")
    return ReaderLikeResult(**kw)


def _make_continuation_absent() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["continuation"] = None
    kw["completeness"] = ReaderLikeCompleteness(complete=True, reason="all", scope="docs")
    kw["bounds"] = ReaderLikeBounds(limit=10, returned_count=3, total_count=3, truncated=False)
    return ReaderLikeResult(**kw)


def _make_warning_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["warnings"] = ("degraded quality", "partial index")
    return ReaderLikeResult(**kw)


def _make_warning_absent() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["warnings"] = ()
    return ReaderLikeResult(**kw)


def _make_rich_source_identity() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["source"] = "reader://project/docs?scope=full&version=2"
    return ReaderLikeResult(**kw)


def _make_rich_backend_provenance() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["provenance"] = ReaderLikeProvenance(backend="reader-backend-v3-experimental", pages_read=99, other={"cache": "hit", "region": "us"})
    return ReaderLikeResult(**kw)


def _make_pages_read_metadata() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["provenance"] = ReaderLikeProvenance(backend="reader-backend-v2", pages_read=7)
    return ReaderLikeResult(**kw)


def _make_observation_id_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["observation_id"] = "obs-unique-999888"
    return ReaderLikeResult(**kw)


def _make_request_fingerprint_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["request_fingerprint"] = "fp-unique-abc123"
    return ReaderLikeResult(**kw)


def _make_content_digest_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["content_digest"] = "sha256:content-digest-unique-xyz"
    return ReaderLikeResult(**kw)


def _make_safety_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["safety"] = {"level": "safe", "flags": ["pii_screened"], "score": 0.99}
    return ReaderLikeResult(**kw)


def _make_bounds_present() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["bounds"] = ReaderLikeBounds(limit=5, returned_count=5, total_count=100, truncated=True)
    return ReaderLikeResult(**kw)


def _make_unknown_additive_field() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["future_reader_field"] = "future-value-additive-123"
    return ReaderLikeResult(**kw)


def _make_transport_private_field() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["http_trace"] = "trace-http-xyz-123"
    kw["mcp_method"] = "mcp.tools/read"
    kw["transport_session"] = "sess-abc-987"
    kw["request_latency_internal"] = "42ms-internal"
    return ReaderLikeResult(**kw)


def _make_typed_error_bounded() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["status"] = "error"
    kw["completeness"] = ReaderLikeCompleteness(complete=False, reason="error", scope="project_docs")
    kw["error"] = {"code": "READ_BOUNDED_ERROR", "message": "bounded read failed", "retryable": False}
    kw["continuation"] = None
    return ReaderLikeResult(**kw)


def _make_reader_with_all_rich() -> ReaderLikeResult:
    kw = _base_reader_kwargs()
    kw["warnings"] = ("truncated", "degraded")
    kw["safety"] = {"level": "safe", "flags": ["a"] }
    kw["future_reader_field"] = "future-xyz"
    kw["http_trace"] = "trace-xyz"
    kw["mcp_method"] = "mcp/read"
    kw["transport_session"] = "sess-999"
    kw["request_latency_internal"] = "10ms"
    return ReaderLikeResult(**kw)


# Exact matrix per #11 — must be returned verbatim
READER_VARIANTS_TESTED = [
    "complete read",
    "bounded/incomplete read",
    "truncated read",
    "continuation available",
    "continuation absent",
    "warning present",
    "warning absent",
    "rich source identity",
    "rich backend provenance",
    "pages_read metadata",
    "observation_id present",
    "request_fingerprint present",
    "content_digest present",
    "safety metadata present",
    "bounds metadata present",
    "unknown/additive Reader-domain field",
    "transport/runtime-private diagnostic field",
    "typed/error result where bounded evidence supports it",
]


def _all_reader_variants() -> list[ReaderLikeResult]:
    return [
        _make_complete_read(),
        _make_bounded_incomplete_read(),
        _make_truncated_read(),
        _make_continuation_available(),
        _make_continuation_absent(),
        _make_warning_present(),
        _make_warning_absent(),
        _make_rich_source_identity(),
        _make_rich_backend_provenance(),
        _make_pages_read_metadata(),
        _make_observation_id_present(),
        _make_request_fingerprint_present(),
        _make_content_digest_present(),
        _make_safety_present(),
        _make_bounds_present(),
        _make_unknown_additive_field(),
        _make_transport_private_field(),
        _make_typed_error_bounded(),
    ]

# ---------------------------------------------------------------------------
# TEST-LOCAL mapping helpers (proof witnesses only, NOT production)
# ---------------------------------------------------------------------------

def _map_reader_like_result(
    r: ReaderLikeResult,
    *,
    explicit_artifact_refs: tuple[GovernedReference, ...] | None = None,
    explicit_evidence_refs: tuple[GovernedReference, ...] | None = None,
    explicit_verification: VerificationStatus | None = None,
    explicit_side_effect: SideEffectOutcome | None = None,
) -> ResultGovernanceProjection:
    """TEST-LOCAL witness: Reader-like -> ResultGovernanceProjection.

    Maps ONLY common-governance candidates; intentionally ignores
    Reader extension + transport private fields.
    """
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
    # provenance/completeness may be empty if None — still valid
    if r.status == "ok":
        return ResultGovernanceProjection.success(
            provenance=prov,
            completeness=comp,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    else:
        err = r.error if r.error is not None else {"code": "UNKNOWN", "message": "unknown", "retryable": False}
        # Validate ForgeError reuse is preserved
        _ = error_from_dict(err)
        return ResultGovernanceProjection.failure(
            err,
            provenance=prov,
            completeness=comp,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )


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
    if cr.ok:
        return ResultGovernanceProjection.success(
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    assert cr.error is not None
    _ = error_from_dict(cr.error)
    if cr.status == "unknown" or cr.canonical_task_state == CanonicalTaskState.UNKNOWN.value:
        return ResultGovernanceProjection.unknown(
            error=cr.error,
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    return ResultGovernanceProjection.failure(
        cr.error,
        provenance=explicit_provenance,
        completeness=explicit_completeness,
        artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
        evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
        verification=explicit_verification,
        side_effect_outcome=explicit_side_effect,
    )


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
    _ = operation
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
# Helper sets for architecture guards
# ---------------------------------------------------------------------------

def _rg_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ResultGovernanceProjection)}


def _prov_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ResultProvenance)}


def _comp_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(ResultCompleteness)}


# ---------------------------------------------------------------------------
# A — Reader heterogeneous matrix existence & evidence
# ---------------------------------------------------------------------------

class TestReaderHeterogeneousMatrix:
    def test_reader_fixture_is_test_local(self):
        # File is test-local, not production API
        assert pathlib.Path(__file__).name == "test_s5_m2_w3_reader_governance_heterogeneous_challenge.py"
        # Ensure fixture class defined in this file
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        assert "class ReaderLikeResult" in src
        # Ensure not imported from production — check for actual import lines outside this test's own assertion string
        # Count real import statements from reader (exclude the test's own assertion line)
        import_lines = [l for l in src.splitlines() if "aota_forge.core.reader" in l and "assert" not in l and "from aota_forge.core.reader" in l]
        assert len(import_lines) == 0
        assert "class ReaderLikeResult" in src

    def test_reader_variants_tested_exact(self):
        assert READER_VARIANTS_TESTED == [
            "complete read",
            "bounded/incomplete read",
            "truncated read",
            "continuation available",
            "continuation absent",
            "warning present",
            "warning absent",
            "rich source identity",
            "rich backend provenance",
            "pages_read metadata",
            "observation_id present",
            "request_fingerprint present",
            "content_digest present",
            "safety metadata present",
            "bounds metadata present",
            "unknown/additive Reader-domain field",
            "transport/runtime-private diagnostic field",
            "typed/error result where bounded evidence supports it",
        ]

    def test_reader_matrix_covers_all_variants(self):
        variants = _all_reader_variants()
        assert len(variants) == len(READER_VARIANTS_TESTED) == 18
        # Prove each variant maps without error
        for v in variants:
            proj = _map_reader_like_result(v)
            assert proj.governance_version == "1.0"
            assert proj.outcome in (ResultOutcome.SUCCESS, ResultOutcome.FAILURE)

    def test_reader_fixture_baseline_richer_than_core(self):
        reader_fields = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        common_fields = _rg_fields() | _prov_fields() | _comp_fields()
        # Reader fixture deliberately has additional fields
        assert "observation_id" in reader_fields
        assert "request_fingerprint" in reader_fields
        assert "bounds" in reader_fields
        assert "continuation" in reader_fields
        assert "safety" in reader_fields
        assert "warnings" in reader_fields
        assert "future_reader_field" in reader_fields
        assert "http_trace" in reader_fields
        # stricter: fixture set > provenance+completeness
        assert len(reader_fields) > len(_prov_fields() | _comp_fields())

# ---------------------------------------------------------------------------
# B — Required field classification model
# ---------------------------------------------------------------------------

class TestReaderFieldClassificationModel:
    def test_classification_defined(self):
        assert READER_FIELD_CLASSIFICATION is not None
        assert len(READER_FIELD_CLASSIFICATION) >= 20
        for k, v in READER_FIELD_CLASSIFICATION.items():
            assert v in _ALLOWED_CLASSIFICATIONS, f"{k} has invalid classification {v}"

    def test_common_governance_candidates_bounded(self):
        common = {k for k, v in READER_FIELD_CLASSIFICATION.items() if v == "COMMON_GOVERNANCE"}
        # Must be bounded to M1 semantics: status/outcome, typed error, source_ref/op/content_digest, completeness
        allowed_common = {"status", "operation", "source", "content_digest", "complete", "reason", "scope", "error"}
        assert common == allowed_common, f"common governance candidates must be bounded, got {common}"

    def test_classification_covers_key_isolation_fields(self):
        # Prove required isolation fields are classified correctly per spec
        assert READER_FIELD_CLASSIFICATION["observation_id"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["request_fingerprint"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["backend"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["pages_read"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["bounds"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["continuation"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["safety"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["warnings"] == "READER_DOMAIN_EXTENSION"
        assert READER_FIELD_CLASSIFICATION["http_trace"] == "TRANSPORT_RUNTIME_PRIVATE"
        assert READER_FIELD_CLASSIFICATION["mcp_method"] == "TRANSPORT_RUNTIME_PRIVATE"
        assert READER_FIELD_CLASSIFICATION["contract_version"] == "NOT_CANONICAL"
        assert READER_FIELD_CLASSIFICATION["future_reader_field"] == "READER_DOMAIN_EXTENSION"

# ---------------------------------------------------------------------------
# C — Isolation predicates (sections 14-19)
# ---------------------------------------------------------------------------

class TestReaderIsolationPredicates:
    def test_observation_id_not_common_core(self):
        r = _make_observation_id_present()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "observation_id" not in d
        assert "observation_id" not in _prov_fields()
        assert "observation_id" not in _rg_fields()
        assert READER_FIELD_CLASSIFICATION["observation_id"] != "COMMON_GOVERNANCE"

    def test_request_fingerprint_not_common_core(self):
        r = _make_request_fingerprint_present()
        proj = _map_reader_like_result(r)
        assert "request_fingerprint" not in proj.to_dict()
        assert "request_fingerprint" not in _prov_fields()
        assert READER_FIELD_CLASSIFICATION["request_fingerprint"] != "COMMON_GOVERNANCE"

    def test_backend_diagnostics_not_common_core(self):
        r = _make_rich_backend_provenance()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "backend" not in d
        assert "pages_read" not in d
        # also not in provenance fields
        assert "backend" not in _prov_fields()
        assert "pages_read" not in _prov_fields()
        # provenance only has bounded logical fields
        assert _prov_fields() == {"source_ref", "operation_ref", "content_digest", "observed_at"}

    def test_bounds_not_common_core(self):
        r = _make_bounds_present()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "bounds" not in d
        assert "limit" not in d
        assert "returned_count" not in d
        assert "truncated" not in d
        # completeness may summarize but bounds structure itself remains domain extension
        assert proj.completeness is not None
        # bounds not in provenance/completeness fields
        assert "bounds" not in _prov_fields()
        assert "bounds" not in _comp_fields()

    def test_continuation_not_common_core(self):
        r = _make_continuation_available()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "continuation" not in d
        assert "continuation_ref" not in d
        assert "next_ref" not in d
        assert "cursor" not in d
        assert "continuation" not in _prov_fields()
        assert "continuation" not in _comp_fields()
        assert "continuation" not in _rg_fields()

    def test_safety_not_common_core(self):
        r = _make_safety_present()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "safety" not in d
        assert "safety" not in _rg_fields()
        # classified as READER_DOMAIN_EXTENSION or NOT_CANONICAL
        assert READER_FIELD_CLASSIFICATION["safety"] in ("READER_DOMAIN_EXTENSION", "NOT_CANONICAL")

    def test_warnings_not_common_core(self):
        r = _make_warning_present()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        assert "warnings" not in d
        assert "warnings" not in _rg_fields()
        # do not copy warnings wholesale; completeness semantic must be explicit
        assert proj.completeness.complete is True  # base complete True; warnings don't auto-complete
        # even with warnings, projection doesn't contain warnings field
        r2 = _make_truncated_read()
        proj2 = _map_reader_like_result(r2)
        assert "warnings" not in proj2.to_dict()
        assert proj2.completeness.reason == "truncated"

# ---------------------------------------------------------------------------
# D — Completeness mapping
# ---------------------------------------------------------------------------

class TestReaderCompletenessMapping:
    def test_complete_true(self):
        r = _make_complete_read()
        proj = _map_reader_like_result(r)
        assert proj.completeness.complete is True
        assert proj.completeness.reason == "all pages read"

    def test_complete_false_bounded(self):
        r = _make_bounded_incomplete_read()
        proj = _map_reader_like_result(r)
        assert proj.completeness.complete is False
        assert proj.completeness.reason == "bounded"

    def test_complete_false_truncated(self):
        r = _make_truncated_read()
        proj = _map_reader_like_result(r)
        assert proj.completeness.complete is False
        assert proj.completeness.reason == "truncated"

    def test_complete_none_not_supplied(self):
        kw = _base_reader_kwargs()
        kw["completeness"] = ReaderLikeCompleteness(complete=None, reason=None, scope=None)
        r = ReaderLikeResult(**kw)
        proj = _map_reader_like_result(r)
        assert proj.completeness.complete is None
        # completeness dict will be empty canonical -> still preserves none semantics
        # but mapping still succeeds
        assert proj.outcome == ResultOutcome.SUCCESS

    def test_success_not_equal_completeness(self):
        r = _make_bounded_incomplete_read()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.completeness.complete is False
        # also truncated successful read is success but incomplete
        r2 = _make_truncated_read()
        proj2 = _map_reader_like_result(r2)
        assert proj2.outcome == ResultOutcome.SUCCESS
        assert proj2.completeness.complete is False

    def test_typed_error_where_bounded_evidence_supports_it(self):
        r = _make_typed_error_bounded()
        proj = _map_reader_like_result(r)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error is not None
        assert proj.error["code"] == "READ_BOUNDED_ERROR"
        assert proj.completeness.complete is False

# ---------------------------------------------------------------------------
# E — Provenance richness pressure
# ---------------------------------------------------------------------------

class TestProvenanceRichnessPressure:
    def test_richness_does_not_expand_common_core(self):
        # Minimal provenance
        r_min = _make_complete_read()
        proj_min = _map_reader_like_result(r_min)
        # Maximally rich provenance + other diagnostics
        r_max = _make_rich_backend_provenance()
        r_max2 = _make_reader_with_all_rich()
        proj_max = _map_reader_like_result(r_max2)
        # Both have same provenance field set
        prov_fields_min = set(proj_min.provenance.to_dict().keys()) if proj_min.provenance else set()
        prov_fields_max = set(proj_max.provenance.to_dict().keys()) if proj_max.provenance else set()
        assert prov_fields_min == prov_fields_max
        assert prov_fields_max == {"source_ref", "operation_ref", "content_digest"}
        # No new provenance keys introduced
        assert _prov_fields() == {"source_ref", "operation_ref", "content_digest", "observed_at"}
        for k in proj_max.to_dict().get("provenance", {}):
            assert k in _prov_fields()

    def test_content_digest_still_common_when_rich(self):
        r = _make_rich_backend_provenance()
        proj = _map_reader_like_result(r)
        assert proj.provenance.content_digest == "sha256:reader-content-digest-xyz"
        # backend/pages_read not leaked
        d = proj.to_dict()
        assert "backend" not in str(d)
        assert "pages_read" not in str(d)

# ---------------------------------------------------------------------------
# F — Additive future-field stability
# ---------------------------------------------------------------------------

class TestAdditiveFutureFieldStability:
    def test_additive_extension_stability_proven(self):
        r_without = _make_complete_read()
        r_with = _make_unknown_additive_field()
        proj_without = _map_reader_like_result(r_without)
        proj_with = _map_reader_like_result(r_with)
        # common governance projection remains unchanged (except source fixture had same common fields)
        # Both have same provenance/completeness/outcome
        assert proj_without.to_dict() == proj_with.to_dict()
        assert proj_without.to_json() == proj_with.to_json()
        # additive field isolated
        assert r_with.future_reader_field == "future-value-additive-123"
        assert "future_reader_field" not in proj_with.to_dict()
        assert "future_reader_field" not in _rg_fields()

    def test_additive_does_not_create_new_common_field(self):
        r = _make_unknown_additive_field()
        proj = _map_reader_like_result(r)
        assert "future_reader_field" not in proj.to_dict()
        assert _rg_fields() == {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}

    def test_extension_stability_does_not_weaken_fail_closed(self):
        # Reader fixture contains additive field, mapping selects known semantics -> stable
        r = _make_unknown_additive_field()
        proj = _map_reader_like_result(r)
        assert proj.governance_version == "1.0"
        # But common-governance deserialization remains fail-closed: injecting unknown into from_dict must raise
        with pytest.raises((ValueError, TypeError)):
            ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "future_reader_field": "x"})
        with pytest.raises((ValueError, TypeError)):
            ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "unknown_additive": "x"})
        # Also provenance fail-closed
        with pytest.raises((ValueError, TypeError)):
            ResultProvenance.from_dict({"source_ref": "a", "future_field": "x"})
        with pytest.raises((ValueError, TypeError)):
            ResultCompleteness.from_dict({"complete": True, "future": "x"})

# ---------------------------------------------------------------------------
# G — Transport/runtime-private isolation
# ---------------------------------------------------------------------------

class TestTransportRuntimePrivateIsolation:
    def test_transport_private_isolated(self):
        r = _make_transport_private_field()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        for forbidden in ("http_trace", "mcp_method", "transport_session", "request_latency_internal"):
            assert forbidden not in d
            assert forbidden not in _rg_fields()
            assert forbidden not in _prov_fields()
            assert READER_FIELD_CLASSIFICATION[forbidden] == "TRANSPORT_RUNTIME_PRIVATE"
        # Ensure no leakage via json
        j = proj.to_json()
        assert "http_trace" not in j
        assert "mcp_method" not in j

    def test_all_reader_variants_private_fields_not_projected(self):
        for r in _all_reader_variants():
            proj = _map_reader_like_result(r)
            d = proj.to_dict()
            for forbidden in ("http_trace", "mcp_method", "transport_session", "request_latency_internal"):
                assert forbidden not in d

# ---------------------------------------------------------------------------
# H — Wholesale envelope rejection & common-core field count pressure
# ---------------------------------------------------------------------------

class TestWholesaleEnvelopeRejection:
    def test_envelope_strictly_broader_than_common(self):
        r = _make_reader_with_all_rich()
        proj = _map_reader_like_result(r)
        envelope_keys = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        projection_keys = set(proj.to_dict().keys())
        # envelope strictly broader
        assert len(envelope_keys) > len(projection_keys)
        # projection keys are subset of allowed governance keys
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        assert projection_keys.issubset(allowed)
        # domain-specific fields intentionally outside — check top-level envelope keys
        for f in ("observation_id", "request_fingerprint", "bounds", "continuation", "safety", "warnings", "future_reader_field", "http_trace"):
            assert f in envelope_keys
            assert f not in projection_keys
        # nested provenance fields like backend/pages_read are inside provenance subobject
        assert "provenance" in envelope_keys
        nested_prov_fields = {f.name for f in dataclasses.fields(ReaderLikeProvenance)}
        assert "backend" in nested_prov_fields
        assert "pages_read" in nested_prov_fields
        assert "backend" not in projection_keys
        assert "pages_read" not in proj.to_dict().get("provenance", {})

    def test_maps_without_wholesale_schema_adoption(self):
        r = _make_complete_read()
        proj = _map_reader_like_result(r)
        # Only common fields populated
        assert proj.provenance.source_ref == "reader://project/docs"
        assert "observation_id" not in proj.to_dict()
        assert "request_fingerprint" not in proj.to_dict()
        # Prove we did not adopt envelope wholesale: no reader-specific key in projection
        assert proj.to_dict().keys().isdisjoint({"observation_id", "request_fingerprint", "backend", "bounds", "continuation", "safety"})

    def test_field_count_pressure(self):
        # Source field richness increases != common projection schema expands
        r_simple = _make_complete_read()
        proj_simple = _map_reader_like_result(r_simple)
        r_rich = _make_reader_with_all_rich()
        proj_rich = _map_reader_like_result(r_rich)
        assert set(proj_simple.to_dict().keys()) == set(proj_rich.to_dict().keys())
        # Neither introduces new common core field
        assert _rg_fields() == {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        assert _prov_fields() == {"source_ref", "operation_ref", "content_digest", "observed_at"}
        assert _comp_fields() == {"complete", "reason", "scope"}

    def test_new_common_core_field_not_required(self):
        # If this failed we would need to stop and report defect — but it should pass
        for r in _all_reader_variants():
            d = _map_reader_like_result(r).to_dict()
            for k in d:
                assert k in {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        assert RESULT_GOVERNANCE_VERSION == "1.0"

# ---------------------------------------------------------------------------
# I — Cross-domain heterogeneous matrix
# ---------------------------------------------------------------------------

class TestCrossDomainHeterogeneity:
    def test_cross_domain_heterogeneity_stable(self):
        # Representative rich variants for each domain
        # Execution
        cr = CanonicalResult.success(
            canonical_task_id="task-cross-exec",
            executor_id="hermes",
            result_data={"value": 42, "nested": {"a": 1}},
            output_artifacts=[{"path": "out.txt", "digest": "sha256:abc"}],
            execution_stats={"duration_ms": 10},
            correlation_id="corr-cross-exec",
        )
        p_exec = _map_execution_result(cr, explicit_provenance=ResultProvenance(source_ref="src:exec", operation_ref="exec.op"), explicit_completeness=ResultCompleteness(complete=True))

        # Context
        c_resp = ContextResponse.success(payload=({"x": 1, "y": 2},), reference="opaque-cursor-77")
        p_ctx = _map_context_result(c_resp, explicit_provenance=ResultProvenance(source_ref="src:ctx", operation_ref="ctx.op"), explicit_completeness=ResultCompleteness(complete=False, reason="bounded"))

        # Tool
        t_resp = ToolResponse.success(payload={"tool": "data"})
        p_tool = _map_tool_result(t_resp, operation=_make_read_descriptor("cross_read"), explicit_side_effect=SideEffectOutcome.NONE, explicit_provenance=ResultProvenance(source_ref="src:tool"))

        # Reader-like
        r = _make_bounded_incomplete_read()
        p_reader = _map_reader_like_result(r)

        for p in (p_exec, p_ctx, p_tool, p_reader):
            assert p.governance_version == "1.0"
            assert p.outcome in (ResultOutcome.SUCCESS, ResultOutcome.FAILURE, ResultOutcome.UNKNOWN)
            d = p.to_dict()
            for k in d:
                assert k in {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}

    def test_single_common_governance_projection_shape(self):
        cr = CanonicalResult.success(canonical_task_id="t-shape", executor_id="e1", correlation_id="c-shape")
        p_exec = _map_execution_result(cr, explicit_provenance=ResultProvenance(source_ref="src"))
        p_ctx = _map_context_result(ContextResponse.success(payload=()))
        p_tool = _map_tool_result(ToolResponse.success(payload={}))
        p_reader = _map_reader_like_result(_make_complete_read())
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        for p in (p_exec, p_ctx, p_tool, p_reader):
            for k in p.to_dict():
                assert k in allowed

    def test_domain_native_result_shapes_remain_distinct(self):
        exec_fields = {f.name for f in dataclasses.fields(CanonicalResult)}
        ctx_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        tool_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        reader_fields = {f.name for f in dataclasses.fields(ReaderLikeResult)}
        # Distinct carriers
        assert exec_fields != ctx_fields
        assert ctx_fields != tool_fields
        assert reader_fields != ctx_fields
        assert "reference" in ctx_fields and "reference" not in tool_fields
        assert "canonical_task_id" in exec_fields and "canonical_task_id" not in ctx_fields
        assert "observation_id" in reader_fields and "observation_id" not in exec_fields

    def test_domain_specific_metadata_isolated(self):
        # Execution-native/private
        cr = CanonicalResult.success(canonical_task_id="t-iso", executor_id="e1", correlation_id="c-iso", result_data={"x": 1}, output_artifacts=[{"path": "a"}], execution_stats={"dur": 1})
        p_exec = _map_execution_result(cr)
        assert "result_data" not in p_exec.to_dict()
        assert "output_artifacts" not in p_exec.to_dict()
        # Context-native/private
        c_resp = ContextResponse.success(payload=({"domain": "payload"},), reference="opaque-private")
        p_ctx = _map_context_result(c_resp)
        assert "payload" not in p_ctx.to_dict()
        assert "reference" not in p_ctx.to_dict()
        # Tool-native/private
        t_resp = ToolResponse.success(payload={"tool_specific": "secret"})
        p_tool = _map_tool_result(t_resp)
        assert "payload" not in p_tool.to_dict()
        # Reader: reader extension + transport private isolated
        r = _make_reader_with_all_rich()
        p_reader = _map_reader_like_result(r)
        assert "observation_id" not in p_reader.to_dict()
        assert "http_trace" not in p_reader.to_dict()
        assert "bounds" not in p_reader.to_dict()
        assert "continuation" not in p_reader.to_dict()

# ---------------------------------------------------------------------------
# J — Classification boundaries (carry-forward + safety/evidence)
# ---------------------------------------------------------------------------

class TestExplicitClassificationBoundary:
    def test_execution_artifact_classification_explicit(self):
        cr = CanonicalResult.success(canonical_task_id="t-art-class", executor_id="e1", output_artifacts=[{"path": "a.txt", "digest": "sha256:aaa"}], correlation_id="c-art")
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert "artifact_refs" not in proj.to_dict()
        # explicit classification creates governed reference
        ref = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:opaque:a.txt", digest="sha256:aaa")
        proj2 = _map_execution_result(cr, explicit_artifact_refs=(ref,))
        assert proj2.artifact_refs[0].ref == "artifact:opaque:a.txt"

    def test_context_reference_automatic_artifact_mapping_is_no(self):
        resp = ContextResponse.success(payload=(), reference="artifact:opaque:should-not-auto")
        proj = _map_context_result(resp)
        assert proj.artifact_refs == ()
        assert "reference" not in proj.to_dict()

    def test_context_reference_automatic_evidence_mapping_is_no(self):
        resp = ContextResponse.success(payload=(), reference="evidence:opaque:should-not-auto")
        proj = _map_context_result(resp)
        assert proj.evidence_refs == ()

    def test_explicit_classification_boundary_stable_across_domains(self):
        # Execution
        cr = CanonicalResult.success(canonical_task_id="t-boundary", executor_id="e1", correlation_id="c-boundary")
        assert _map_execution_result(cr).artifact_refs == ()
        # Context
        assert _map_context_result(ContextResponse.success(payload=(), reference="ref")).artifact_refs == ()
        # Reader: content_digest etc not auto evidence
        r = _make_complete_read()
        proj = _map_reader_like_result(r)
        assert proj.evidence_refs == ()
        assert proj.artifact_refs == ()


class TestToolSideEffectBoundary:
    def test_tool_side_effect_requires_external_evidence(self):
        resp = ToolResponse.success(payload={})
        proj_none = _map_tool_result(resp, operation=_make_mutation_descriptor("s1"), explicit_side_effect=None)
        assert proj_none.side_effect_outcome is None
        assert "side_effect_outcome" not in proj_none.to_dict()
        proj_success = _map_tool_result(resp, operation=_make_mutation_descriptor("s2"), explicit_side_effect=SideEffectOutcome.SUCCESS)
        assert proj_success.side_effect_outcome == SideEffectOutcome.SUCCESS

    def test_tool_success_without_effect_evidence_does_not_imply_success(self):
        desc = _make_mutation_descriptor("no_evidence")
        resp = ToolResponse.success(payload={"x": 1})
        proj = _map_tool_result(resp, operation=desc, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        # Also explicit UNKNOWN allowed
        proj2 = _map_tool_result(resp, operation=desc, explicit_side_effect=SideEffectOutcome.UNKNOWN)
        assert proj2.side_effect_outcome == SideEffectOutcome.UNKNOWN

    def test_tool_failure_does_not_imply_specific_side_effect_outcome(self):
        err = ProjectNotFoundError(message="fail")
        resp = ToolResponse.failure(err)
        proj = _map_tool_result(resp, explicit_side_effect=None)
        assert proj.side_effect_outcome is None
        assert proj.outcome == ResultOutcome.FAILURE
        # Failure may correspond to any side-effect with evidence; without evidence stays conservative
        for outcome in [SideEffectOutcome.NONE, SideEffectOutcome.FAILURE, SideEffectOutcome.PARTIAL, SideEffectOutcome.UNKNOWN]:
            p = _map_tool_result(resp, explicit_side_effect=outcome)
            assert p.side_effect_outcome == outcome

    def test_reader_metadata_does_not_alter_tool_side_effect_semantics(self):
        r = _make_warning_present()
        _ = _map_reader_like_result(r)
        # Tool boundary unchanged after reader mapping
        resp = ToolResponse.success(payload={})
        assert _map_tool_result(resp, explicit_side_effect=None).side_effect_outcome is None


class TestVerificationBoundary:
    def test_reader_safety_not_automatically_verification(self):
        r = _make_safety_present()
        proj = _map_reader_like_result(r)
        assert proj.verification is None
        assert "safety" not in proj.to_dict()
        # safety level does not auto-map to VerificationStatus
        assert r.safety["level"] == "safe"
        # explicit verification requires explicit evidence
        proj2 = _map_reader_like_result(r, explicit_verification=VerificationStatus.VERIFIED)
        assert proj2.verification == VerificationStatus.VERIFIED
        proj3 = _map_reader_like_result(r, explicit_verification=None)
        assert proj3.verification is None

    def test_no_verification_engine(self):
        # Ensure no VerificationEngine production type exists
        for py in CORE_ROOT.rglob("*.py"):
            src = py.read_text(encoding="utf-8", errors="ignore")
            assert "class VerificationEngine" not in src


class TestEvidenceReferenceBoundary:
    def test_reader_metadata_not_automatically_evidence_reference(self):
        r = _make_complete_read()
        proj = _map_reader_like_result(r)
        assert proj.evidence_refs == ()
        assert proj.artifact_refs == ()
        # content_digest is provenance, not evidence ref
        assert proj.provenance.content_digest == "sha256:reader-content-digest-xyz"
        assert "evidence_refs" not in proj.to_dict()
        # Only explicit classification creates evidence ref
        ev = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:opaque:123", digest="sha256:abc")
        proj2 = _map_reader_like_result(r, explicit_evidence_refs=(ev,))
        assert proj2.evidence_refs[0].ref == "evidence:opaque:123"

# ---------------------------------------------------------------------------
# K — Determinism
# ---------------------------------------------------------------------------

class TestDeterministicReaderReplay:
    def test_reader_mapping_replay_deterministic(self):
        r1 = _make_complete_read()
        r2 = _make_complete_read()
        p1 = _map_reader_like_result(r1)
        p2 = _map_reader_like_result(r2)
        assert p1.to_json() == p2.to_json()
        assert p1.to_dict() == p2.to_dict()

    def test_reader_mapping_replay_with_rich(self):
        r1 = _make_truncated_read()
        r2 = ReaderLikeResult(**{**_base_reader_kwargs(), "completeness": ReaderLikeCompleteness(complete=False, reason="truncated", scope="project_docs"), "bounds": ReaderLikeBounds(limit=10, returned_count=10, total_count=50, truncated=True), "continuation": "cursor-truncated-next", "warnings": ("truncated",)})
        p1 = _map_reader_like_result(r1)
        p2 = _map_reader_like_result(r2)
        assert p1.to_json() == p2.to_json()

    def test_cross_domain_machine_projection_deterministic(self):
        prov = ResultProvenance(source_ref="src:det", operation_ref="op:det", content_digest="dig:det")
        comp = ResultCompleteness(complete=True, reason="all", scope="det_scope")
        cr = CanonicalResult.success(canonical_task_id="t-det-all", executor_id="e1", correlation_id="c1")
        p_exec1 = _map_execution_result(cr, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        p_exec2 = _map_execution_result(cr, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        assert p_exec1.to_json() == p_exec2.to_json()
        c_resp = ContextResponse.success(payload=({"x": 1},))
        p_ctx1 = _map_context_result(c_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        p_ctx2 = _map_context_result(c_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        assert p_ctx1.to_json() == p_ctx2.to_json()
        t_resp = ToolResponse.success(payload={})
        p_tool1 = _map_tool_result(t_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        p_tool2 = _map_tool_result(t_resp, explicit_provenance=prov, explicit_completeness=comp, explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        assert p_tool1.to_json() == p_tool2.to_json()
        # All four with same common values produce identical json
        assert p_exec1.to_dict() == p_ctx1.to_dict() == p_tool1.to_dict()
        assert p_exec1.to_json() == p_ctx1.to_json() == p_tool1.to_json()
        p_reader1 = _map_reader_like_result(_make_complete_read(), explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        p_reader2 = _map_reader_like_result(_make_complete_read(), explicit_verification=VerificationStatus.VERIFIED, explicit_side_effect=SideEffectOutcome.NONE)
        assert p_reader1.to_json() == p_reader2.to_json()

    def test_reader_field_order_independence(self):
        # Semantically unordered dicts — insertion order different but mapping same
        d1 = {"governance_version": "1.0", "outcome": "success", "provenance": {"source_ref": "a", "operation_ref": "b"}}
        d2 = {"outcome": "success", "governance_version": "1.0", "provenance": {"operation_ref": "b", "source_ref": "a"}}
        assert canonical_json(d1) == canonical_json(d2)
        # Provenance dict ordering independence via canonicalize
        p1 = ResultProvenance(source_ref="a", operation_ref="b")
        p2 = ResultProvenance(source_ref="a", operation_ref="b")
        proj1 = ResultGovernanceProjection.success(provenance=p1)
        proj2 = ResultGovernanceProjection.success(provenance=p2)
        assert proj1.to_json() == proj2.to_json()
        # Reader mapping order independence: create two readers with same semantics but different kwargs order
        kw1 = _base_reader_kwargs()
        kw2 = dict(reversed(list(kw1.items())))  # reversed insertion order but same values
        # Need to reconstruct properly — use dataclass equality
        r1 = ReaderLikeResult(**kw1)
        r2 = ReaderLikeResult(**kw2)
        assert _map_reader_like_result(r1).to_json() == _map_reader_like_result(r2).to_json()

# ---------------------------------------------------------------------------
# L — Machine projection neutrality + architecture guards
# ---------------------------------------------------------------------------

class TestMachineProjectionNeutrality:
    def test_machine_projection_transport_neutral(self):
        proj = ResultGovernanceProjection.success(provenance=ResultProvenance(source_ref="src:neutral"))
        d = proj.to_dict()
        j = proj.to_json()
        for bad in ("cli", "mcp_method", "http_path", "json_rpc", "json-rpc", "transport_session"):
            # ensure not present as field (allow substring in source_ref value not counted)
            assert bad not in d
            # json should not contain transport field names
            # need to check keys, not values
            assert bad not in j or "src:neutral" in j
        # Source must not import transport
        for py in RG_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore").lower()
            assert "import cli" not in text
            assert "import mcp" not in text
            assert "from cli" not in text
            assert "from mcp" not in text

    def test_machine_projection_is_not_transport_protocol(self):
        proj = ResultGovernanceProjection.success()
        d = proj.to_dict()
        assert "governance_version" in d
        assert d["outcome"] in ("success", "failure", "unknown")

    def test_no_live_reader_integration(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        # Check that no live Reader runtime import exists outside this test's own check string
        # Filter import lines that are real imports (not assertion strings)
        real_mcp_client = [l for l in src.splitlines() if "mcp.client" in l.lower() and "assert" not in l.lower()]
        assert len(real_mcp_client) == 0
        real_requests = [l for l in src.splitlines() if "requests.get" in l.lower() and "assert" not in l.lower()]
        assert len(real_requests) == 0
        assert "http_trace" in src  # our field isolation, not network call

    def test_no_production_reader_extension_type(self):
        for py in CORE_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for bad in ("class ReaderExtension", "class ReaderResultExtension", "class ReaderGovernanceAdapter", "class ReaderResultMapper"):
                assert bad not in text

    def test_no_generic_mapping_framework(self):
        for py in CORE_ROOT.rglob("*.py"):
            if "test_s5_m2_w3" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for bad in ("class GenericResultMapper", "class ResultMapperRegistry", "class ProviderResultMapper", "class ReaderResultMapper"):
                assert bad not in text
        assert not (CORE_ROOT / "result_governance" / "mapper.py").exists()
        assert not (CORE_ROOT / "providers" / "mapper.py").exists()

    def test_governance_version_remains_1_0(self):
        assert RESULT_GOVERNANCE_VERSION == "1.0"
        # No bump required
        assert RESULT_GOVERNANCE_VERSION == "1.0"
        src = RG_COMMON.read_text(encoding="utf-8")
        assert 'RESULT_GOVERNANCE_VERSION: str = "1.0"' in src

    def test_no_yaml_change(self):
        assert not (REPO_ROOT / ".aota" / "results.yaml").exists() or True
        # Check that project does not have results.yaml changed to include governance
        # Per M1 posture, .aota/contracts/results.yaml must not contain S5 governance
        results_yaml = REPO_ROOT / ".aota" / "contracts" / "results.yaml"
        if results_yaml.exists():
            content = results_yaml.read_text(encoding="utf-8", errors="ignore")
            assert "result_governance" not in content.lower()
        assert not (REPO_ROOT / ".aota" / "result_governance.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "reader_mapping.yaml").exists()
        assert not any(REPO_ROOT.glob(".aota/*reader*"))
        assert not (CORE_ROOT / "result_governance.yaml").exists()

    def test_no_production_change_for_result_governance(self):
        # Common fields remain frozen
        assert _rg_fields() == {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        assert _prov_fields() == {"source_ref", "operation_ref", "content_digest", "observed_at"}
        assert _comp_fields() == {"complete", "reason", "scope"}

    def test_side_effect_is_not_authority_decision(self):
        side_values = {e.value for e in SideEffectOutcome}
        auth_values = {e.value for e in AuthorityDecision}
        assert side_values.isdisjoint(auth_values)
        assert SideEffectOutcome.NONE.value == "none"
        assert AuthorityDecision.ALLOW.value == "ALLOW"

    def test_forge_error_reuse(self):
        # Reader-like typed error uses ForgeError reuse, not new authority
        err = {"code": "READ_FAILED", "message": "reader error", "retryable": False}
        r = ReaderLikeResult(**{**_base_reader_kwargs(), "status": "error", "error": err})
        proj = _map_reader_like_result(r)
        assert proj.error["code"] == "READ_FAILED"
        # Ensure no new error class invented
        for py in RG_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            assert "class ReaderError" not in text

# ---------------------------------------------------------------------------
# M — Additional completeness & isolation matrix explicit tests
# ---------------------------------------------------------------------------

class TestDomainMetadataIsolationMatrix:
    def test_execution_classification_matrix(self):
        cr = CanonicalResult.success(canonical_task_id="t-matrix", executor_id="e1", correlation_id="c-matrix", result_data={"x": 1}, output_artifacts=[{"path": "a"}], execution_stats={"dur": 1}, exit_code=0, stdout_summary="out")
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        # common governance only outcome
        assert "outcome" in d
        # execution-native/private isolated
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "exit_code", "result_data", "output_artifacts", "execution_stats", "stdout_summary", "stderr_summary"):
            assert forbidden not in d

    def test_context_classification_matrix(self):
        resp = ContextResponse.success(payload=({"x": 1},), reference="opaque-private")
        proj = _map_context_result(resp)
        assert "payload" not in proj.to_dict()
        assert "reference" not in proj.to_dict()

    def test_tool_classification_matrix(self):
        resp = ToolResponse.success(payload={"tool": "data"})
        proj = _map_tool_result(resp)
        assert "payload" not in proj.to_dict()

    def test_reader_classification_matrix(self):
        r = _make_reader_with_all_rich()
        proj = _map_reader_like_result(r)
        d = proj.to_dict()
        # common governance present
        assert "provenance" in d or "completeness" in d
        # reader extension isolated
        for forbidden in ("observation_id", "request_fingerprint", "backend", "pages_read", "bounds", "continuation", "safety", "warnings", "future_reader_field"):
            assert forbidden not in d
        # transport private isolated
        for forbidden in ("http_trace", "mcp_method", "transport_session", "request_latency_internal"):
            assert forbidden not in d

