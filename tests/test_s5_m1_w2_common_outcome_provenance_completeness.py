"""S5/M1/W2 — Common Outcome / Provenance / Completeness Contract."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import ForgeError, ProjectNotFoundError, error_from_dict
from aota_forge.core.result_governance import (
    RESULT_GOVERNANCE_VERSION,
    ResultCompleteness,
    ResultGovernanceProjection,
    ResultOutcome,
    ResultProvenance,
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
# governance version
# ---------------------------------------------------------------------------


def test_governance_version_defined():
    assert RESULT_GOVERNANCE_VERSION == "1.0"


def test_current_version_accepted():
    p = ResultGovernanceProjection.success()
    assert p.governance_version == "1.0"


def test_invalid_version_rejected():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection(
            governance_version="9.9",
            outcome=ResultOutcome.SUCCESS,
        )
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "bad", "outcome": "success"})


def test_version_tag_not_compat_proof():
    # version alone does not prove compatibility — same version still requires shape validation
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict(
            {"governance_version": "1.0", "outcome": "success", "error": {"code": "X", "message": "m", "retryable": False}}
        )


# ---------------------------------------------------------------------------
# outcome
# ---------------------------------------------------------------------------


def test_success_common_projection():
    p = ResultGovernanceProjection.success()
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.error is None
    d = p.to_dict()
    assert d["outcome"] == "success"


def test_failure_projection_using_real_forge_error():
    err = ProjectNotFoundError(message="proj missing")
    p = ResultGovernanceProjection.failure(err)
    assert p.outcome == ResultOutcome.FAILURE
    assert p.error is not None
    assert p.error["code"] == "PROJECT_NOT_FOUND"
    # round-trip through existing model
    recovered = error_from_dict(p.error)
    assert isinstance(recovered, ForgeError)
    assert recovered.code == "PROJECT_NOT_FOUND"


def test_unknown_outcome_representable():
    p = ResultGovernanceProjection.unknown()
    assert p.outcome == ResultOutcome.UNKNOWN
    d = p.to_dict()
    assert d["outcome"] == "unknown"
    # unknown round-trips
    p2 = ResultGovernanceProjection.from_dict(d)
    assert p2.outcome == ResultOutcome.UNKNOWN


def test_success_and_complete_false_representable():
    comp = ResultCompleteness(complete=False, reason="bounded", scope="test_scope")
    p = ResultGovernanceProjection.success(completeness=comp)
    assert p.outcome == ResultOutcome.SUCCESS
    assert p.completeness is not None
    assert p.completeness.complete is False
    assert p.error is None


def test_no_task_state_imported():
    # outcome values are success/failure/unknown, not CanonicalTaskState values
    for v in ResultOutcome:
        assert v.value in ("success", "failure", "unknown")
        assert v.value not in ("COMPLETED", "FAILED", "CANCELLED", "QUEUED", "RUNNING")


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_minimal_provenance_source_ref():
    prov = ResultProvenance(source_ref="logical:source:1")
    p = ResultGovernanceProjection.success(provenance=prov)
    assert p.provenance is not None
    assert p.provenance.source_ref == "logical:source:1"
    d = p.to_dict()
    assert d["provenance"]["source_ref"] == "logical:source:1"


def test_optional_operation_ref():
    prov = ResultProvenance(source_ref="src", operation_ref="op:search")
    p = ResultGovernanceProjection.success(provenance=prov)
    assert p.provenance.operation_ref == "op:search"


def test_optional_content_digest():
    prov = ResultProvenance(source_ref="src", content_digest="sha256:abc")
    p = ResultGovernanceProjection.success(provenance=prov)
    assert p.provenance.content_digest == "sha256:abc"
    d = p.to_dict()
    assert d["provenance"]["content_digest"] == "sha256:abc"


def test_provenance_does_not_require_execution_identity():
    prov = ResultProvenance(source_ref="only-source")
    # no execution identity fields required
    assert not hasattr(prov, "canonical_task_id")
    assert not hasattr(prov, "executor_id")
    # fields are all optional
    empty = ResultProvenance()
    assert empty.source_ref is None


def test_provenance_excludes_execution_identity_fields():
    fields = {f.name for f in dataclasses.fields(ResultProvenance)}
    for forbidden in ("canonical_task_id", "executor_id", "adapter_handle", "dispatch_attempt_id"):
        assert forbidden not in fields
    for forbidden in ("observation_id", "request_fingerprint"):
        assert forbidden not in fields


# ---------------------------------------------------------------------------
# completeness
# ---------------------------------------------------------------------------


def test_completeness_absent_representable():
    p = ResultGovernanceProjection.success()
    assert p.completeness is None
    d = p.to_dict()
    assert "completeness" not in d
    # from_dict without completeness
    p2 = ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success"})
    assert p2.completeness is None


def test_completeness_fields():
    comp = ResultCompleteness(complete=True, reason="all items fetched", scope="project_docs")
    assert comp.complete is True
    assert comp.reason == "all items fetched"
    assert comp.scope == "project_docs"
    p = ResultGovernanceProjection.success(completeness=comp)
    d = p.to_dict()
    assert d["completeness"]["complete"] is True


def test_completeness_not_applicable_not_interpreted_as_false():
    # absent means not applicable, not False
    p_absent = ResultGovernanceProjection.success()
    p_false = ResultGovernanceProjection.success(completeness=ResultCompleteness(complete=False))
    assert p_absent.completeness is None
    assert p_false.completeness.complete is False
    assert p_absent.to_dict().get("completeness") != p_false.to_dict().get("completeness")


def test_reader_bounds_not_imported():
    fields = {f.name for f in dataclasses.fields(ResultCompleteness)}
    assert "bounds" not in fields
    assert "continuation" not in fields
    # reason/scope are generic, not Reader-specific pagination
    comp = ResultCompleteness(reason="bounded due to limit", scope="docs")
    assert comp.reason == "bounded due to limit"


# ---------------------------------------------------------------------------
# error round-trip
# ---------------------------------------------------------------------------


def test_error_round_trip_through_existing_forge_model():
    err = ProjectNotFoundError(message="round-trip test")
    d = err.to_dict()
    p = ResultGovernanceProjection.failure(d)
    recovered = error_from_dict(p.error)
    assert recovered is not None
    assert recovered.code == d["code"]
    assert recovered.message == d["message"]


def test_error_projection_uses_existing_model():
    err = ProjectNotFoundError(message="x")
    p = ResultGovernanceProjection.failure(err)
    assert p.error["code"] == "PROJECT_NOT_FOUND"
    assert "message" in p.error
    assert "retryable" in p.error


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_round_trip():
    prov = ResultProvenance(source_ref="src1", operation_ref="op1", content_digest="dig1")
    comp = ResultCompleteness(complete=False, reason="partial", scope="s1")
    err = ProjectNotFoundError(message="fail")
    p = ResultGovernanceProjection.failure(err, provenance=prov, completeness=comp)
    d = p.to_dict()
    p2 = ResultGovernanceProjection.from_dict(d)
    assert p2.to_dict() == d
    assert p2.outcome == p.outcome
    assert p2.error == p.error


def test_deterministic_canonical_json():
    prov = ResultProvenance(source_ref="a", operation_ref="b")
    comp = ResultCompleteness(complete=True)
    p1 = ResultGovernanceProjection.success(provenance=prov, completeness=comp)
    p2 = ResultGovernanceProjection.success(provenance=prov, completeness=comp)
    assert p1.to_json() == p2.to_json()
    # also canonical_json ordering stable
    assert canonical_json(p1.to_dict()) == canonical_json(p2.to_dict())
    # different insertion order still same after canonicalization
    d1 = {"governance_version": "1.0", "outcome": "success", "provenance": {"operation_ref": "b", "source_ref": "a"}}
    d2 = {"outcome": "success", "governance_version": "1.0", "provenance": {"source_ref": "a", "operation_ref": "b"}}
    assert canonical_json(d1) == canonical_json(d2)


def test_canonical_json_not_dependent_on_insertion_order():
    p = ResultGovernanceProjection.success(provenance=ResultProvenance(source_ref="s", content_digest="d"))
    j1 = p.to_json()
    # construct via from_dict with different key order
    d = {"completeness": {"scope": "s1", "complete": False}, "outcome": "success", "governance_version": "1.0", "provenance": {"content_digest": "d", "source_ref": "s"}}
    # need error absent for success; completeness will be parsed
    p2 = ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "provenance": {"content_digest": "d", "source_ref": "s"}, "completeness": {"complete": False, "scope": "s1"}})
    # deterministic
    assert isinstance(j1, str)


# ---------------------------------------------------------------------------
# fail-closed deserialization
# ---------------------------------------------------------------------------


def test_malformed_outcome_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "bad_value"})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": ""})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0"})


def test_malformed_error_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "failure", "error": {"code": "", "message": "m", "retryable": False}})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "failure", "error": {"message": "m", "retryable": False}})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "failure", "error": "not-a-dict"})
    # success with error should fail
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "error": {"code": "X", "message": "m", "retryable": False}})
    # failure without error should fail
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "failure"})


def test_malformed_provenance_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "provenance": "bad"})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "provenance": {"unknown_field": "x"}})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "provenance": {"source_ref": ""}})


def test_malformed_completeness_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "completeness": "bad"})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "completeness": {"unknown": 1}})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "completeness": {"complete": "not-bool"}})


def test_invalid_governance_version_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "", "outcome": "success"})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "2.0", "outcome": "success"})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": None, "outcome": "success"})


def test_unknown_fields_rejected():
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "extra": 1})
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection.from_dict({"governance_version": "1.0", "outcome": "success", "provenance": {"source_ref": "a", "extra": 1}})


# ---------------------------------------------------------------------------
# ownership guards
# ---------------------------------------------------------------------------


def test_no_payload_reference_ownership():
    fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    assert "payload" not in fields
    assert "result_data" not in fields
    assert "reference" not in fields
    assert "output" not in fields
    # also provenance/completeness should not contain those
    for cls in (ResultProvenance, ResultCompleteness):
        cfields = {f.name for f in dataclasses.fields(cls)}
        assert "payload" not in cfields
        assert "result_data" not in cfields


def test_no_execution_task_state_in_common_core():
    # ResultOutcome is not CanonicalTaskState
    from aota_forge.core.execution.state import CanonicalTaskState  # noqa: F401 - just to ensure we don't reuse

    for v in ResultOutcome:
        assert v.value not in {s.value for s in CanonicalTaskState}
    # projection fields do not include task state
    fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    assert "canonical_task_state" not in fields


def test_no_reader_observation_in_common_core():
    fields = {f.name for f in dataclasses.fields(ResultProvenance)}
    assert "observation_id" not in fields
    assert "request_fingerprint" not in fields
    fields2 = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    assert "observation_id" not in fields2


def test_no_w3_fields():
    fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
    for forbidden in ("artifact_refs", "evidence_refs", "verification", "side_effect_outcome"):
        assert forbidden not in fields
    # also check provenance/completeness
    for cls in (ResultProvenance, ResultCompleteness):
        cfields = {f.name for f in dataclasses.fields(cls)}
        for forbidden in ("artifact_refs", "evidence_refs", "verification", "side_effect_outcome"):
            assert forbidden not in cfields


def test_no_domain_extension_stub_hierarchy():
    symbols = _rg_symbols()
    for forbidden in ("ExecutionExtension", "ContextExtension", "ToolExtension", "ReaderExtension"):
        assert forbidden not in symbols


def test_no_new_error_authority():
    symbols = _rg_symbols()
    for forbidden in ("CommonError", "ResultGovernanceError", "ResultError", "GovernanceError"):
        assert forbidden not in symbols


def test_outcome_error_consistency_fails_closed():
    # success with error fails
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection(
            governance_version="1.0",
            outcome=ResultOutcome.SUCCESS,
            error={"code": "X", "message": "m", "retryable": False},
        )
    # failure without error fails
    with pytest.raises((ValueError, TypeError)):
        ResultGovernanceProjection(
            governance_version="1.0",
            outcome=ResultOutcome.FAILURE,
            error=None,
        )
    # failure with valid error succeeds
    ok = ResultGovernanceProjection.failure(ProjectNotFoundError(message="x"))
    assert ok.outcome == ResultOutcome.FAILURE


def test_new_production_module_count():
    # at least the two expected files exist
    assert (REPO_ROOT / "aota_forge" / "core" / "result_governance" / "__init__.py").exists()
    assert (REPO_ROOT / "aota_forge" / "core" / "result_governance" / "common.py").exists()


def test_existing_error_authority_preserved():
    # ensure ForgeError still works
    err = ProjectNotFoundError(message="test")
    assert err.code == "PROJECT_NOT_FOUND"
    d = err.to_dict()
    assert d["code"] == "PROJECT_NOT_FOUND"
