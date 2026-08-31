"""S5/M2/W1 — Existing Forge Execution Result Mapping Stabilization.

Test-only proof that ResultGovernanceProjection remains stable across richer
CanonicalResult variants without absorbing execution-private metadata or
requiring common-core redesign.

Architecture:
    CanonicalResult (domain-native, execution-owned)
    +
    execution-neutral ResultGovernanceProjection (common core)
    -> TEST-LOCAL mapping helper is proof-only

W1 is strictly TEST_ONLY. No production mapper/registry created.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import (
    ForgeError,
    HostResourceDeniedError,
    OutcomeUnknownError,
    ProjectNotFoundError,
    SourceParityMismatchError,
    UnknownFutureError,
    error_from_dict,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
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
# TEST-LOCAL mapping helper (proof witness only, NOT production)
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
    """TEST-LOCAL witness: CanonicalResult -> ResultGovernanceProjection.

    Only maps governance semantics (outcome/error + explicit optional refs).
    NEVER copies execution-private identities, runtime metadata, result_data,
    or output_artifacts. Output artifacts are not auto-promoted.
    """
    if cr.ok:
        # success must not carry error
        return ResultGovernanceProjection.success(
            provenance=explicit_provenance,
            completeness=explicit_completeness,
            artifact_refs=explicit_artifact_refs if explicit_artifact_refs is not None else (),
            evidence_refs=explicit_evidence_refs if explicit_evidence_refs is not None else (),
            verification=explicit_verification,
            side_effect_outcome=explicit_side_effect,
        )
    # ok == False covers failed/rejected/cancelled/unknown etc.
    # unknown maps to UNKNOWN outcome if status==unknown or state==UNKNOWN
    assert cr.error is not None
    # validate ForgeError reuse is preserved
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


# ---------------------------------------------------------------------------
# Helpers to build matrix variants
# ---------------------------------------------------------------------------

def _success(**overrides) -> CanonicalResult:
    base = dict(
        canonical_task_id="task-success-base",
        executor_id="executor-base",
        correlation_id="corr-base",
    )
    base.update(overrides)
    return CanonicalResult.success(**base)  # type: ignore[arg-type]


def _failure(**overrides) -> CanonicalResult:
    base = dict(
        canonical_task_id="task-failure-base",
        executor_id="executor-base",
        correlation_id="corr-base",
    )
    base.update(overrides)
    return CanonicalResult.failure(**base)  # type: ignore[arg-type]


# Track tested variants for reporting
TESTED_VARIANTS: list[str] = []


# ---------------------------------------------------------------------------
# A — Representative execution variant matrix
# ---------------------------------------------------------------------------

class TestExecutionVariantMatrix:
    """Prove matrix coverage over accepted CanonicalResult contract."""

    def test_successful_scalar_simple_result_data(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-scalar-1",
            executor_id="exec-1",
            result_data={"value": 42, "flag": True, "name": "hello"},
            correlation_id="corr-scalar-1",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert cr.result_data["value"] == 42
        assert "result_data" not in proj.to_dict()
        TESTED_VARIANTS.append("successful+scalar/simple result_data")

    def test_successful_nested_structured_result_data(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-nested-1",
            executor_id="exec-1",
            result_data={"nested": {"a": {"b": [1, 2, {"c": 3}]}, "x": 99}, "list": [1, 2, 3]},
            correlation_id="corr-nested-1",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert cr.result_data["nested"]["a"]["b"][2]["c"] == 3
        assert "result_data" not in proj.to_dict()
        TESTED_VARIANTS.append("successful+nested/structured result_data")

    def test_successful_one_output_artifact(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-artifact-one",
            executor_id="exec-1",
            output_artifacts=[{"path": "out.txt", "digest": "sha256:aaa"}],
            correlation_id="corr-art1",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert len(cr.output_artifacts) == 1
        assert proj.artifact_refs == ()
        assert "artifact_refs" not in proj.to_dict()
        TESTED_VARIANTS.append("successful+one output_artifact")

    def test_successful_multiple_output_artifacts(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-artifact-multi",
            executor_id="exec-1",
            output_artifacts=[
                {"path": "out1.txt", "digest": "sha256:aaa"},
                {"path": "out2.txt", "digest": "sha256:bbb"},
                {"path": "out3.bin", "digest": "sha256:ccc", "size": 123},
            ],
            correlation_id="corr-art-multi",
        )
        proj = _map_execution_result(cr)
        assert len(cr.output_artifacts) == 3
        assert proj.artifact_refs == ()
        TESTED_VARIANTS.append("successful+multiple output_artifacts")

    def test_successful_stdout_stderr_summaries(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-summaries-1",
            executor_id="exec-1",
            stdout_summary="stdout hello world\nline2",
            stderr_summary="stderr warning: something",
            correlation_id="corr-summ-1",
        )
        proj = _map_execution_result(cr)
        assert cr.stdout_summary == "stdout hello world\nline2"
        assert cr.stderr_summary.startswith("stderr warning")
        d = proj.to_dict()
        assert "stdout_summary" not in d
        assert "stderr_summary" not in d
        TESTED_VARIANTS.append("successful+stdout/stderr summaries")

    def test_successful_execution_stats(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-stats-1",
            executor_id="exec-1",
            execution_stats={"duration_ms": 1234, "cpu_ms": 100, "retries": 0},
            correlation_id="corr-stats-1",
        )
        proj = _map_execution_result(cr)
        assert cr.execution_stats["duration_ms"] == 1234
        assert "execution_stats" not in proj.to_dict()
        TESTED_VARIANTS.append("successful+execution_stats")

    def test_exit_code_variants_where_accepted(self):
        # success with exit_code 0
        cr0 = CanonicalResult.success(
            canonical_task_id="task-exit-0",
            executor_id="exec-1",
            exit_code=0,
            correlation_id="corr-exit-0",
        )
        assert cr0.exit_code == 0
        assert _map_execution_result(cr0).outcome == ResultOutcome.SUCCESS
        # success with exit_code None (allowed)
        cr_none = CanonicalResult.success(
            canonical_task_id="task-exit-none",
            executor_id="exec-1",
            exit_code=None,
            correlation_id="corr-exit-none",
        )
        assert cr_none.exit_code is None
        assert _map_execution_result(cr_none).outcome == ResultOutcome.SUCCESS
        # failure with exit_code 1
        cr_fail1 = CanonicalResult.failure(
            canonical_task_id="task-exit-fail1",
            executor_id="exec-1",
            exit_code=1,
            correlation_id="corr-exit-fail1",
        )
        assert cr_fail1.exit_code == 1
        assert _map_execution_result(cr_fail1).outcome == ResultOutcome.FAILURE
        # failure with exit_code None (rejected/timeout style)
        cr_fail_none = CanonicalResult.failure(
            canonical_task_id="task-exit-fail-none",
            executor_id="exec-1",
            exit_code=None,
            correlation_id="corr-exit-fail-none",
        )
        assert cr_fail_none.exit_code is None
        # failure with non-zero alternative
        cr_fail2 = CanonicalResult.failure(
            canonical_task_id="task-exit-fail2",
            executor_id="exec-1",
            exit_code=2,
            correlation_id="corr-exit-fail2",
        )
        assert cr_fail2.exit_code == 2
        d = _map_execution_result(cr_fail2).to_dict()
        assert "exit_code" not in d
        TESTED_VARIANTS.append("exit_code variants where accepted")

    def test_failure_known_non_retryable_forge_error(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-fail-noretry",
            executor_id="exec-1",
            error_code=ProjectNotFoundError.code,
            error_message="project not found",
            retryable=False,
            correlation_id="corr-fail-noretry",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error is not None
        assert proj.error["code"] == "PROJECT_NOT_FOUND"
        assert proj.error["retryable"] is False
        TESTED_VARIANTS.append("failure+known ForgeError non-retryable")

    def test_failure_retryable_forge_error(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-fail-retry",
            executor_id="exec-1",
            error_code=SourceParityMismatchError.code,
            error_message="parity mismatch",
            retryable=True,
            correlation_id="corr-fail-retry",
        )
        proj = _map_execution_result(cr)
        assert proj.error["code"] == "SOURCE_PARITY_MISMATCH"
        assert proj.error["retryable"] is True
        recovered = error_from_dict(proj.error)
        assert isinstance(recovered, SourceParityMismatchError)
        assert recovered.retryable is True
        TESTED_VARIANTS.append("failure+retryable ForgeError")

    def test_failure_retryable_via_outcome_unknown(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-fail-retry2",
            executor_id="exec-1",
            error_code=OutcomeUnknownError.code,
            error_message="outcome unknown",
            retryable=True,
            correlation_id="corr-fail-retry2",
        )
        proj = _map_execution_result(cr)
        assert proj.error["code"] == "OUTCOME_UNKNOWN"
        assert proj.error["retryable"] is True
        TESTED_VARIANTS.append("failure+retryable OutcomeUnknownError")

    def test_unknown_future_error_compatibility(self):
        # Future code not in registry must degrade to UnknownFutureError but preserve original
        cr = CanonicalResult.failure(
            canonical_task_id="task-future-err",
            executor_id="exec-1",
            error_code="FUTURE_UNKNOWN_CODE_9999",
            error_message="future error",
            retryable=False,
            correlation_id="corr-future",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        # raw code preserved
        assert proj.error["code"] == "FUTURE_UNKNOWN_CODE_9999"
        recovered = error_from_dict(proj.error)
        assert isinstance(recovered, UnknownFutureError)
        assert recovered.original_code == "FUTURE_UNKNOWN_CODE_9999"
        # also direct UnknownFutureError instance preserved via details
        uf = UnknownFutureError(original_code="MY_FUTURE_X", message="future", retryable=True, details={"x": 1})
        cr2 = CanonicalResult.failure(
            canonical_task_id="task-future-2",
            executor_id="exec-1",
            error_code=uf.code,
            error_message=str(uf.message),
            correlation_id="corr-future2",
        )
        # inject original_code via details to simulate preserved envelope
        cr2_dict = cr2.to_dict()
        cr2_dict["error"]["original_code"] = "MY_FUTURE_X"
        cr2b = CanonicalResult.from_dict(cr2_dict)
        proj2 = _map_execution_result(cr2b)
        assert proj2.error["original_code"] == "MY_FUTURE_X"
        TESTED_VARIANTS.append("UnknownFutureError / future-error compatibility")

    def test_canonical_unknown_state_path(self):
        cr = CanonicalResult.unknown(
            canonical_task_id="task-unknown-1",
            executor_id="exec-1",
            correlation_id="corr-unknown-1",
        )
        assert cr.status == "unknown"
        assert cr.canonical_task_state == CanonicalTaskState.UNKNOWN.value
        assert cr.ok is False
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.UNKNOWN
        d = proj.to_dict()
        assert d["outcome"] == "unknown"
        assert d["governance_version"] == "1.0"
        TESTED_VARIANTS.append("CanonicalResult.unknown() or actual unknown-state path")

    def test_canonical_task_id_pressure(self):
        for tid in ["task-pressure-1", "task/with/slashes", "task-long-" + "x" * 100]:
            cr = CanonicalResult.success(
                canonical_task_id=tid,
                executor_id="exec-1",
                correlation_id="corr-pressure",
            )
            proj = _map_execution_result(cr)
            assert "canonical_task_id" not in proj.to_dict()
            assert cr.canonical_task_id == tid
        TESTED_VARIANTS.append("canonical_task_id pressure")

    def test_executor_id_pressure(self):
        for eid in ["hermes", "exec-xyz-123", "executor/with/special"]:
            cr = CanonicalResult.success(
                canonical_task_id="task-exec-pressure",
                executor_id=eid,
                correlation_id="corr-exec-pressure",
            )
            proj = _map_execution_result(cr)
            assert "executor_id" not in proj.to_dict()
        TESTED_VARIANTS.append("executor_id pressure")

    def test_canonical_task_state_pressure(self):
        # COMPLETED via success
        cr_c = CanonicalResult.success(
            canonical_task_id="task-state-comp",
            executor_id="exec-1",
            correlation_id="corr-state-comp",
        )
        assert cr_c.canonical_task_state == "COMPLETED"
        assert _map_execution_result(cr_c).outcome == ResultOutcome.SUCCESS
        # FAILED via failure
        cr_f = CanonicalResult.failure(
            canonical_task_id="task-state-fail",
            executor_id="exec-1",
            correlation_id="corr-state-fail",
        )
        assert cr_f.canonical_task_state == "FAILED"
        assert _map_execution_result(cr_f).outcome == ResultOutcome.FAILURE
        # CANCELLED
        cr_cancel = CanonicalResult.cancelled(
            canonical_task_id="task-state-cancel",
            executor_id="exec-1",
            correlation_id="corr-state-cancel",
        )
        assert cr_cancel.canonical_task_state == "CANCELLED"
        assert _map_execution_result(cr_cancel).outcome == ResultOutcome.FAILURE
        # UNKNOWN
        cr_unk = CanonicalResult.unknown(
            canonical_task_id="task-state-unknown",
            executor_id="exec-1",
            correlation_id="corr-state-unk",
        )
        assert cr_unk.canonical_task_state == "UNKNOWN"
        assert _map_execution_result(cr_unk).outcome == ResultOutcome.UNKNOWN
        # REJECTED (status rejected, state FAILED)
        cr_rej = CanonicalResult.rejected(
            canonical_task_id="task-state-rejected",
            executor_id="exec-1",
            correlation_id="corr-state-rej",
        )
        assert cr_rej.status == "rejected"
        assert _map_execution_result(cr_rej).outcome == ResultOutcome.FAILURE
        # TIMEOUT (status failed)
        cr_to = CanonicalResult.timeout(
            canonical_task_id="task-state-timeout",
            executor_id="exec-1",
            correlation_id="corr-state-to",
        )
        assert _map_execution_result(cr_to).outcome == ResultOutcome.FAILURE
        d = _map_execution_result(cr_c).to_dict()
        assert "canonical_task_state" not in d
        TESTED_VARIANTS.append("canonical_task_state pressure")

    def test_correlation_id_pressure(self):
        for corr in ["corr-1", "corr-with-dashes-999", "corr-" + "y" * 50]:
            cr = CanonicalResult.success(
                canonical_task_id="task-corr",
                executor_id="exec-1",
                correlation_id=corr,
            )
            proj = _map_execution_result(cr)
            assert "correlation_id" not in proj.to_dict()
            assert cr.correlation_id == corr
        TESTED_VARIANTS.append("correlation_id pressure")

    def test_artifact_looking_result_data(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-artifact-looking-data",
            executor_id="exec-1",
            result_data={
                "path": "/tmp/artifact.txt",
                "digest": "sha256:deadbeef",
                "artifact": "something",
                "evidence": {"ref": "x"},
                "source": "forge",
                "url": "http://example.com/file",
            },
            correlation_id="corr-art-looking-data",
        )
        proj = _map_execution_result(cr)
        # must NOT be auto-promoted
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert "artifact_refs" not in proj.to_dict()
        assert "evidence_refs" not in proj.to_dict()
        TESTED_VARIANTS.append("artifact-looking result_data")

    def test_artifact_looking_output_artifacts(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-artifact-looking-out",
            executor_id="exec-1",
            output_artifacts=[
                {"path": "a.txt", "digest": "sha256:aaa", "url": "http://example.com/a"},
                {"path": "b.txt", "digest": "sha256:bbb", "evidence": "something"},
            ],
            correlation_id="corr-art-looking-out",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        TESTED_VARIANTS.append("artifact-looking output_artifacts")

    def test_rich_execution_metadata_outside_common_core(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-rich-meta",
            executor_id="exec-rich",
            result_data={"key": "value", "nested": {"path": "x", "digest": "y"}},
            output_artifacts=[{"path": "p", "digest": "d"}],
            stdout_summary="stdout rich",
            stderr_summary="stderr rich",
            execution_stats={"duration_ms": 999, "retries": 2, "queue_ms": 10},
            exit_code=0,
            correlation_id="corr-rich",
        )
        # mutate canonical_task_state present is COMPLETED
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        for forbidden in (
            "canonical_task_id",
            "executor_id",
            "canonical_task_state",
            "correlation_id",
            "exit_code",
            "result_data",
            "output_artifacts",
            "execution_stats",
            "stdout_summary",
            "stderr_summary",
        ):
            assert forbidden not in d
        TESTED_VARIANTS.append("rich execution metadata that must remain outside common core")


# ---------------------------------------------------------------------------
# B — Outcome mapping
# ---------------------------------------------------------------------------

class TestExecutionOutcomeMapping:
    def test_successful_maps_to_success(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-out-success",
            executor_id="exec-1",
            correlation_id="corr-out-success",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.SUCCESS
        assert proj.to_dict()["outcome"] == "success"
        assert proj.error is None

    def test_failed_maps_to_failure(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-out-fail",
            executor_id="exec-1",
            correlation_id="corr-out-fail",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.to_dict()["outcome"] == "failure"
        assert proj.error is not None

    def test_unknown_maps_to_unknown(self):
        cr = CanonicalResult.unknown(
            canonical_task_id="task-out-unknown",
            executor_id="exec-1",
            correlation_id="corr-out-unk",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.UNKNOWN
        assert proj.to_dict()["outcome"] == "unknown"
        # unknown may carry error; check present but not requiring failure code shape
        assert proj.error is not None
        assert proj.error["code"] == "TASK_STATE_UNKNOWN"

    def test_cancelled_maps_to_failure_not_unknown(self):
        cr = CanonicalResult.cancelled(
            canonical_task_id="task-cancel-proj",
            executor_id="exec-1",
            correlation_id="corr-cancel",
        )
        proj = _map_execution_result(cr)
        # cancelled is a failure variant, not unknown
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "EXECUTION_CANCELLED"

    def test_rejected_maps_to_failure(self):
        cr = CanonicalResult.rejected(
            canonical_task_id="task-rej-proj",
            executor_id="exec-1",
            correlation_id="corr-rej",
        )
        proj = _map_execution_result(cr)
        assert proj.outcome == ResultOutcome.FAILURE
        assert proj.error["code"] == "DISPATCH_REJECTED"


# ---------------------------------------------------------------------------
# C — ForgeError reuse
# ---------------------------------------------------------------------------

class TestForgeErrorReuse:
    def test_known_non_retryable_error_preserved(self):
        err = ProjectNotFoundError(message="not found non-retryable")
        assert err.retryable is False
        cr = CanonicalResult.failure(
            canonical_task_id="task-err-nr",
            executor_id="exec-1",
            error_code=err.code,
            error_message=err.message,
            retryable=err.retryable,
            correlation_id="corr-err-nr",
        )
        proj = _map_execution_result(cr)
        assert proj.error["code"] == "PROJECT_NOT_FOUND"
        assert proj.error["retryable"] is False
        rec = error_from_dict(proj.error)
        assert isinstance(rec, ProjectNotFoundError)

    def test_known_retryable_error_preserved(self):
        err = SourceParityMismatchError(message="retryable mismatch")
        assert err.retryable is True
        cr = CanonicalResult.failure(
            canonical_task_id="task-err-r",
            executor_id="exec-1",
            error_code=err.code,
            error_message=err.message,
            retryable=err.retryable,
            correlation_id="corr-err-r",
        )
        proj = _map_execution_result(cr)
        assert proj.error["code"] == "SOURCE_PARITY_MISMATCH"
        assert proj.error["retryable"] is True
        rec = error_from_dict(proj.error)
        assert isinstance(rec, SourceParityMismatchError)
        assert rec.retryable is True

    def test_unknown_future_error_preserved_original_code(self):
        cr = CanonicalResult.failure(
            canonical_task_id="task-err-future",
            executor_id="exec-1",
            error_code="FUTURE_CODE_XYZ",
            error_message="future",
            correlation_id="corr-future2",
        )
        proj = _map_execution_result(cr)
        assert proj.error["code"] == "FUTURE_CODE_XYZ"
        rec = error_from_dict(proj.error)
        assert isinstance(rec, UnknownFutureError)
        assert rec.original_code == "FUTURE_CODE_XYZ"
        # dict retains original_code after from_dict round-trip if present
        payload = {"code": "FUTURE_CODE_XYZ", "message": "future", "retryable": False, "original_code": "FUTURE_CODE_XYZ"}
        rec2 = error_from_dict(payload)
        assert isinstance(rec2, UnknownFutureError)
        assert rec2.to_dict()["original_code"] == "FUTURE_CODE_XYZ"

    def test_no_new_common_error_authority_created(self):
        # Ensure no new error class was introduced in result_governance
        import ast

        found = set()
        for py in RG_ROOT.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    if node.name.endswith("Error"):
                        found.add(node.name)
        for forbidden in ("CommonError", "ResultGovernanceError", "ResultError", "ExecutionMappingError"):
            assert forbidden not in found


# ---------------------------------------------------------------------------
# D — Identity isolation
# ---------------------------------------------------------------------------

class TestExecutionIdentityIsolation:
    def test_identity_fields_not_in_projection(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-id-iso",
            executor_id="exec-iso",
            correlation_id="corr-iso",
            result_data={"x": 1},
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id"):
            assert forbidden not in d
            assert forbidden not in json.dumps(d)

    def test_identity_remains_outside_even_when_all_populated(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-all-id",
            executor_id="executor-all",
            correlation_id="corr-all-id",
            result_data={"payload": "rich"},
            output_artifacts=[{"path": "a", "digest": "d"}],
            execution_stats={"dur": 10},
            stdout_summary="out",
            stderr_summary="err",
            exit_code=0,
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id"):
            assert forbidden not in d
        # also not in provenance fields
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id"):
            assert forbidden not in prov_fields
        # projection field names must not contain execution identity
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "execution_stats", "result_data", "output_artifacts", "stdout_summary", "stderr_summary", "exit_code"):
            assert forbidden not in proj_fields

    def test_provenance_is_execution_neutral(self):
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        assert prov_fields == {"source_ref", "operation_ref", "content_digest", "observed_at"}
        for forbidden in ("canonical_task_id", "executor_id", "correlation_id", "canonical_task_state"):
            assert forbidden not in prov_fields


# ---------------------------------------------------------------------------
# E — Runtime metadata isolation
# ---------------------------------------------------------------------------

class TestRuntimeMetadataIsolation:
    def test_exit_code_isolated(self):
        cr = CanonicalResult.success(canonical_task_id="t-exit-iso", executor_id="e1", exit_code=0, correlation_id="c-exit-iso")
        d = _map_execution_result(cr).to_dict()
        assert "exit_code" not in d

    def test_stdout_stderr_isolated(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-std-iso",
            executor_id="e1",
            stdout_summary="hello",
            stderr_summary="warn",
            correlation_id="c-std-iso",
        )
        d = _map_execution_result(cr).to_dict()
        assert "stdout_summary" not in d
        assert "stderr_summary" not in d
        assert "stdout" not in json.dumps(d).lower() or "hello" not in json.dumps(d)

    def test_execution_stats_isolated(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-stats-iso",
            executor_id="e1",
            execution_stats={"duration_ms": 100, "extra": "stats"},
            correlation_id="c-stats-iso",
        )
        d = _map_execution_result(cr).to_dict()
        assert "execution_stats" not in d
        assert "duration_ms" not in json.dumps(d)

    def test_runtime_metadata_not_in_provenance(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-runtime-prov",
            executor_id="e1",
            stdout_summary="out",
            execution_stats={"x": 1},
            correlation_id="c-runtime-prov",
        )
        proj = _map_execution_result(cr)
        if proj.provenance is not None:
            prov_d = proj.provenance.to_dict()
            for forbidden in ("exit_code", "stdout_summary", "stderr_summary", "execution_stats"):
                assert forbidden not in prov_d
        d = proj.to_dict()
        for forbidden in ("exit_code", "stdout_summary", "stderr_summary", "execution_stats"):
            assert forbidden not in d


# ---------------------------------------------------------------------------
# F — result_data boundary
# ---------------------------------------------------------------------------

class TestResultDataBoundary:
    def test_result_data_remains_domain_native(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-rd-native",
            executor_id="e1",
            result_data={"domain_payload": {"key": "value"}, "number": 123},
            correlation_id="c-rd-native",
        )
        proj = _map_execution_result(cr)
        assert "result_data" not in proj.to_dict()
        assert cr.result_data["domain_payload"]["key"] == "value"
        assert cr.result_data["number"] == 123

    def test_artifact_looking_result_data_not_auto_promoted(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-rd-artifact",
            executor_id="e1",
            result_data={
                "path": "/tmp/x",
                "digest": "sha256:abc",
                "evidence": "ev",
                "artifact": "art",
                "source": "forge",
                "url": "http://example.com",
            },
            correlation_id="c-rd-art",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        d = proj.to_dict()
        assert "artifact_refs" not in d
        assert "evidence_refs" not in d

    def test_result_data_boundary_even_with_nested_artifact_keys(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-rd-nested-art",
            executor_id="e1",
            result_data={"output": {"artifact": {"path": "a", "digest": "d"}}, "list": [{"path": "b"}]},
            correlation_id="c-rd-nested",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()


# ---------------------------------------------------------------------------
# G — output_artifacts boundary
# ---------------------------------------------------------------------------

class TestOutputArtifactsBoundary:
    def test_output_artifacts_not_automatically_governed_reference(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-oa-auto",
            executor_id="e1",
            output_artifacts=[{"path": "artifact.txt", "digest": "sha256:aaa"}],
            correlation_id="c-oa-auto",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        assert "artifact_refs" not in proj.to_dict()

    def test_multiple_artifacts_no_auto_promotion(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-oa-multi-auto",
            executor_id="e1",
            output_artifacts=[
                {"path": "a.txt", "digest": "sha256:aaa"},
                {"path": "b.txt", "digest": "sha256:bbb"},
            ],
            correlation_id="c-oa-multi",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert "output_artifacts" not in proj.to_dict()

    def test_without_explicit_classification_projection_remains_empty(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-oa-empty",
            executor_id="e1",
            output_artifacts=[{"path": "x", "digest": "y", "url": "http://example.com"}],
            correlation_id="c-oa-empty",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()


# ---------------------------------------------------------------------------
# H — Explicit artifact classification
# ---------------------------------------------------------------------------

class TestExplicitArtifactClassification:
    def test_explicit_classification_creates_governed_reference(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-explicit-art",
            executor_id="e1",
            output_artifacts=[{"path": "out.txt", "digest": "sha256:aaa"}],
            correlation_id="c-explicit-art",
        )
        ref = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:opaque:out.txt", digest="sha256:aaa")
        proj = _map_execution_result(cr, explicit_artifact_refs=(ref,))
        assert len(proj.artifact_refs) == 1
        assert proj.artifact_refs[0].kind == GovernedReferenceKind.ARTIFACT
        assert proj.artifact_refs[0].ref == "artifact:opaque:out.txt"
        d = proj.to_dict()
        assert d["artifact_refs"][0]["kind"] == "artifact"

    def test_explicit_evidence_classification(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-explicit-ev",
            executor_id="e1",
            correlation_id="c-explicit-ev",
        )
        ev = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:opaque:123", digest="sha256:ev")
        proj = _map_execution_result(cr, explicit_evidence_refs=(ev,))
        assert proj.evidence_refs[0].ref == "evidence:opaque:123"
        assert proj.evidence_refs[0].kind == GovernedReferenceKind.EVIDENCE

    def test_no_path_heuristic_auto_promotion(self):
        # even though dict has path/digest/url, mapping does not create artifact via heuristic
        cr = CanonicalResult.success(
            canonical_task_id="t-no-heuristic",
            executor_id="e1",
            output_artifacts=[{"path": "heuristic.txt", "digest": "sha256:xyz", "url": "http://example.com"}],
            result_data={"path": "also heuristic", "digest": "sha256:abc"},
            correlation_id="c-no-heur",
        )
        proj = _map_execution_result(cr)
        assert proj.artifact_refs == ()
        # explicit still works when caller supplies evidence-backed ref
        explicit = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:explicit:heuristic.txt", digest="sha256:xyz")
        proj2 = _map_execution_result(cr, explicit_artifact_refs=(explicit,))
        assert proj2.artifact_refs[0].ref == "artifact:explicit:heuristic.txt"


# ---------------------------------------------------------------------------
# I — No evidence auto-promotion
# ---------------------------------------------------------------------------

class TestNoEvidenceAutoPromotion:
    def test_artifact_looking_data_not_auto_evidence(self):
        cr = CanonicalResult.success(
            canonical_task_id="t-no-auto-ev",
            executor_id="e1",
            result_data={"evidence": "looks like evidence", "digest": "sha256:ev"},
            output_artifacts=[{"path": "a", "digest": "sha256:aaa", "evidence": "x"}],
            correlation_id="c-no-auto-ev",
        )
        proj = _map_execution_result(cr)
        assert proj.evidence_refs == ()
        assert proj.artifact_refs == ()
        assert "evidence_refs" not in proj.to_dict()

    def test_evidence_semantics_remain_distinct(self):
        a = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:1")
        e = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:1")
        assert a.kind != e.kind
        cr = CanonicalResult.success(canonical_task_id="t-ev-distinct", executor_id="e1", correlation_id="c-ev-distinct")
        proj = _map_execution_result(cr, explicit_artifact_refs=(a,), explicit_evidence_refs=(e,))
        assert proj.artifact_refs[0].kind == GovernedReferenceKind.ARTIFACT
        assert proj.evidence_refs[0].kind == GovernedReferenceKind.EVIDENCE
        # mismatch rejected
        bad = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence:bad")
        with pytest.raises((ValueError, TypeError)):
            _map_execution_result(cr, explicit_artifact_refs=(bad,))  # kind mismatch


# ---------------------------------------------------------------------------
# J — Rich metadata common-core pressure
# ---------------------------------------------------------------------------

class TestRichMetadataPressure:
    def test_maximally_rich_valid_canonical_result_maps_without_core_expansion(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-rich-max",
            executor_id="executor-rich-max",
            result_data={
                "scalar": 1,
                "nested": {"deep": {"value": [1, 2, 3]}},
                "artifact_looking": {"path": "p", "digest": "d", "url": "http://example.com"},
                "evidence_looking": {"evidence": "ev", "ref": "r"},
            },
            output_artifacts=[
                {"path": "out1.txt", "digest": "sha256:aaa", "size": 100},
                {"path": "out2.bin", "digest": "sha256:bbb", "url": "http://example.com/b"},
            ],
            stdout_summary="rich stdout with lots of content " * 10,
            stderr_summary="rich stderr",
            execution_stats={"duration_ms": 5000, "cpu_ms": 1000, "retries": 3, "queue_ms": 50, "custom": {"a": 1}},
            exit_code=0,
            correlation_id="corr-rich-max",
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        # provenance/completeness not auto-filled from rich metadata
        assert "provenance" not in d or set(d.get("provenance", {}).keys()).issubset({"source_ref", "operation_ref", "content_digest", "observed_at"})
        # domain richness does not increase common field count
        allowed_keys = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        for k in d:
            assert k in allowed_keys, f"unexpected key {k} suggests core expansion"
        # source field richness != common governance field count
        assert len(d.keys()) <= len(allowed_keys)
        # explicit classification still isolated
        assert proj.artifact_refs == ()

    def test_domain_specific_execution_metadata_isolated(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-rich-iso",
            executor_id="exec-rich-iso",
            result_data={"x": 1},
            output_artifacts=[{"path": "a"}],
            execution_stats={"a": 1, "b": 2},
            correlation_id="corr-rich-iso",
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id", "exit_code", "result_data", "output_artifacts", "execution_stats", "stdout_summary", "stderr_summary"):
            assert forbidden not in d

    def test_increasing_result_data_richness_does_not_increase_projection_keys(self):
        cr_simple = CanonicalResult.success(canonical_task_id="t-simple", executor_id="e1", result_data={"a": 1}, correlation_id="c-simple")
        cr_rich = CanonicalResult.success(
            canonical_task_id="t-rich2",
            executor_id="e1",
            result_data={"a": 1, "b": {"nested": [1, 2, 3, {"deep": True}]}, "c": "x" * 100},
            output_artifacts=[{"path": "p", "digest": "d"}, {"path": "p2", "digest": "d2"}],
            execution_stats={"dur": 10, "extra": {"x": 1}},
            correlation_id="c-rich2",
        )
        proj_simple = _map_execution_result(cr_simple)
        proj_rich = _map_execution_result(cr_rich)
        # both have same projection key set (governance_version + outcome only)
        assert set(proj_simple.to_dict().keys()) == set(proj_rich.to_dict().keys())
        assert proj_simple.to_dict().keys() == {"governance_version", "outcome"}


# ---------------------------------------------------------------------------
# K — Common-core expansion gate
# ---------------------------------------------------------------------------

class TestCommonCoreExpansionGate:
    def test_no_common_core_field_required_for_any_accepted_variant(self):
        # All accepted execution variants from matrix map without adding fields
        variants = [
            CanonicalResult.success(canonical_task_id="t-gate-1", executor_id="e1", result_data={"x": 1}, correlation_id="c-gate-1"),
            CanonicalResult.success(canonical_task_id="t-gate-2", executor_id="e1", result_data={"nested": {"a": 1}}, correlation_id="c-gate-2"),
            CanonicalResult.success(canonical_task_id="t-gate-3", executor_id="e1", output_artifacts=[{"path": "a"}], correlation_id="c-gate-3"),
            CanonicalResult.success(canonical_task_id="t-gate-4", executor_id="e1", stdout_summary="out", stderr_summary="err", correlation_id="c-gate-4"),
            CanonicalResult.success(canonical_task_id="t-gate-5", executor_id="e1", execution_stats={"dur": 1}, correlation_id="c-gate-5"),
            CanonicalResult.failure(canonical_task_id="t-gate-6", executor_id="e1", correlation_id="c-gate-6"),
            CanonicalResult.unknown(canonical_task_id="t-gate-7", executor_id="e1", correlation_id="c-gate-7"),
            CanonicalResult.cancelled(canonical_task_id="t-gate-8", executor_id="e1", correlation_id="c-gate-8"),
            CanonicalResult.rejected(canonical_task_id="t-gate-9", executor_id="e1", correlation_id="c-gate-9"),
        ]
        allowed_keys = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        for cr in variants:
            d = _map_execution_result(cr).to_dict()
            for k in d:
                assert k in allowed_keys, f"variant {cr.canonical_task_id} introduced unexpected key {k}"
            # version remains 1.0
            assert d["governance_version"] == "1.0"

    def test_projection_fields_are_frozen(self):
        fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        expected = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        assert fields == expected
        prov_fields = {f.name for f in dataclasses.fields(ResultProvenance)}
        assert prov_fields == {"source_ref", "operation_ref", "content_digest", "observed_at"}
        comp_fields = {f.name for f in dataclasses.fields(ResultCompleteness)}
        assert comp_fields == {"complete", "reason", "scope"}


# ---------------------------------------------------------------------------
# L — Deterministic replay
# ---------------------------------------------------------------------------

class TestDeterministicReplay:
    def test_mapping_replay_deterministic_to_json(self):
        cr1 = CanonicalResult.success(
            canonical_task_id="task-replay-1",
            executor_id="exec-1",
            result_data={"a": 1, "b": 2},
            output_artifacts=[{"path": "a", "digest": "d"}],
            execution_stats={"dur": 10},
            correlation_id="corr-replay",
        )
        cr2 = CanonicalResult.success(
            canonical_task_id="task-replay-1",
            executor_id="exec-1",
            result_data={"a": 1, "b": 2},
            output_artifacts=[{"path": "a", "digest": "d"}],
            execution_stats={"dur": 10},
            correlation_id="corr-replay",
        )
        p1 = _map_execution_result(cr1)
        p2 = _map_execution_result(cr2)
        assert p1.to_json() == p2.to_json()
        assert p1.to_dict() == p2.to_dict()

    def test_mapping_replay_deterministic_failure(self):
        cr1 = CanonicalResult.failure(
            canonical_task_id="task-replay-fail",
            executor_id="exec-1",
            error_code=ProjectNotFoundError.code,
            error_message="not found",
            correlation_id="corr-replay-fail",
        )
        cr2 = CanonicalResult.failure(
            canonical_task_id="task-replay-fail",
            executor_id="exec-1",
            error_code=ProjectNotFoundError.code,
            error_message="not found",
            correlation_id="corr-replay-fail",
        )
        p1 = _map_execution_result(cr1)
        p2 = _map_execution_result(cr2)
        assert p1.to_json() == p2.to_json()
        assert canonical_json(p1.to_dict()) == canonical_json(p2.to_dict())

    def test_mapping_replay_with_explicit_refs(self):
        cr = CanonicalResult.success(canonical_task_id="t-replay-ref", executor_id="e1", correlation_id="c-replay-ref")
        ref = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:opaque:1", digest="sha256:aaa")
        p1 = _map_execution_result(cr, explicit_artifact_refs=(ref,))
        p2 = _map_execution_result(cr, explicit_artifact_refs=(ref,))
        assert p1.to_json() == p2.to_json()

    def test_unknown_replay_deterministic(self):
        cr1 = CanonicalResult.unknown(canonical_task_id="t-replay-unk", executor_id="e1", correlation_id="c-replay-unk")
        cr2 = CanonicalResult.unknown(canonical_task_id="t-replay-unk", executor_id="e1", correlation_id="c-replay-unk")
        assert _map_execution_result(cr1).to_json() == _map_execution_result(cr2).to_json()


# ---------------------------------------------------------------------------
# M — Order independence
# ---------------------------------------------------------------------------

class TestOrderIndependence:
    def test_mapping_order_independent_where_semantically_equivalent(self):
        # result_data insertion order different but semantically equivalent
        cr1 = CanonicalResult.success(
            canonical_task_id="t-order-1",
            executor_id="exec-1",
            result_data={"a": 1, "b": 2, "c": 3},
            correlation_id="c-order",
        )
        cr2 = CanonicalResult.success(
            canonical_task_id="t-order-1",
            executor_id="exec-1",
            result_data={"c": 3, "a": 1, "b": 2},
            correlation_id="c-order",
        )
        p1 = _map_execution_result(cr1)
        p2 = _map_execution_result(cr2)
        assert p1.to_json() == p2.to_json()

        # execution_stats order independence
        cr3 = CanonicalResult.success(
            canonical_task_id="t-order-stats1",
            executor_id="e1",
            execution_stats={"dur": 10, "cpu": 5},
            correlation_id="c-order-stats",
        )
        cr4 = CanonicalResult.success(
            canonical_task_id="t-order-stats1",
            executor_id="e1",
            execution_stats={"cpu": 5, "dur": 10},
            correlation_id="c-order-stats",
        )
        assert _map_execution_result(cr3).to_json() == _map_execution_result(cr4).to_json()

        # provenance dict ordering independence via canonical_json
        d1 = {"governance_version": "1.0", "outcome": "success", "provenance": {"source_ref": "a", "operation_ref": "b"}}
        d2 = {"outcome": "success", "governance_version": "1.0", "provenance": {"operation_ref": "b", "source_ref": "a"}}
        assert canonical_json(d1) == canonical_json(d2)

    def test_ordered_artifact_list_semantics_preserved(self):
        # ordered artifact lists where order matters must be preserved — do NOT reorder
        ref1 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:1")
        ref2 = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact:2")
        cr = CanonicalResult.success(canonical_task_id="t-order-art", executor_id="e1", correlation_id="c-order-art")
        p_ordered = _map_execution_result(cr, explicit_artifact_refs=(ref1, ref2))
        p_reversed = _map_execution_result(cr, explicit_artifact_refs=(ref2, ref1))
        # reversed order is semantically different, must NOT be equal
        assert p_ordered.to_dict()["artifact_refs"][0]["ref"] == "artifact:1"
        assert p_reversed.to_dict()["artifact_refs"][0]["ref"] == "artifact:2"
        assert p_ordered.to_json() != p_reversed.to_json()


# ---------------------------------------------------------------------------
# N — Governance version & YAML & production abstraction guards
# ---------------------------------------------------------------------------

class TestGovernanceVersion:
    def test_result_governance_version_is_1_0(self):
        assert RESULT_GOVERNANCE_VERSION == "1.0"
        cr = CanonicalResult.success(canonical_task_id="t-ver", executor_id="e1", correlation_id="c-ver")
        proj = _map_execution_result(cr)
        assert proj.governance_version == "1.0"
        assert proj.to_dict()["governance_version"] == "1.0"

    def test_no_version_bump_required(self):
        cr = CanonicalResult.success(canonical_task_id="t-ver2", executor_id="e1", correlation_id="c-ver2")
        proj = _map_execution_result(cr)
        # from_dict must accept 1.0, reject 2.0
        with pytest.raises((ValueError, TypeError)):
            ResultGovernanceProjection.from_dict({"governance_version": "2.0", "outcome": "success"})
        with pytest.raises((ValueError, TypeError)):
            ResultGovernanceProjection(governance_version="2.0", outcome=ResultOutcome.SUCCESS)


class TestNoYamlAndNoProductionAbstraction:
    def test_results_yaml_not_changed(self):
        yaml_path = REPO_ROOT / ".aota" / "contracts" / "results.yaml"
        assert yaml_path.exists()
        content = yaml_path.read_text(encoding="utf-8")
        # Must still be foundational, not S5 governance
        assert "canonical_mutation_result" in content or "canonical_dispatch_result" in content
        assert "result_governance" not in content.lower()
        assert "artifact_ref" not in content.lower()

    def test_no_new_result_governance_yaml_created(self):
        new_yaml = REPO_ROOT / ".aota" / "contracts" / "result_governance.yaml"
        alt_yaml = REPO_ROOT / ".aota" / "result_governance.yml"
        assert not new_yaml.exists()
        assert not alt_yaml.exists()

    def test_no_production_mapping_abstraction_created(self):
        # Guard against creating ExecutionResultMapper etc. in core
        import ast

        forbidden = {"ExecutionResultMapper", "ResultMapper", "GovernanceMapper", "ResultAdapter", "ResultMapperRegistry", "GenericResultMapper"}
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in forbidden:
                    pytest.fail(f"{py} defines forbidden production mapping {node.name}")
                if isinstance(node, ast.FunctionDef) and node.name in forbidden:
                    pytest.fail(f"{py} defines forbidden function {node.name}")

    def test_mapping_helper_is_test_local_only(self):
        # Ensure production helpers do not exist
        import importlib.util

        assert importlib.util.find_spec("aota_forge.core.result_governance.mapper") is None
        assert importlib.util.find_spec("aota_forge.core.execution.mapper") is None

    def test_no_cli_mcp_transport_import_in_result_governance(self):
        for py in RG_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8").lower()
            assert "import cli" not in text
            assert "import mcp" not in text
            assert "from cli" not in text
            assert "from mcp" not in text
            assert "import http" not in text


# ---------------------------------------------------------------------------
# O — Execution still domain-native and lifecycle owned
# ---------------------------------------------------------------------------

class TestExecutionRemainsDomainNative:
    def test_canonical_result_fields_unchanged(self):
        fields = {f.name for f in dataclasses.fields(CanonicalResult)}
        for required in ("canonical_task_id", "executor_id", "canonical_task_state", "exit_code", "correlation_id", "result_data", "output_artifacts", "execution_stats", "stdout_summary", "stderr_summary", "error"):
            assert required in fields

    def test_execution_result_lifecycle_remains_execution_owned(self):
        # Success/failure constructors remain on CanonicalResult, not on ResultGovernanceProjection
        assert hasattr(CanonicalResult, "success")
        assert hasattr(CanonicalResult, "failure")
        assert hasattr(CanonicalResult, "unknown")
        # ResultGovernanceProjection does not create execution results
        assert not hasattr(ResultGovernanceProjection, "from_canonical_result")

    def test_execution_identity_is_common_core_no(self):
        proj_fields = {f.name for f in dataclasses.fields(ResultGovernanceProjection)}
        for forbidden in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id"):
            assert forbidden not in proj_fields


# ---------------------------------------------------------------------------
# P — Canonical domain isolation summary
# ---------------------------------------------------------------------------

class TestDomainExtensionIsolationSummary:
    def test_all_isolation_invariants_together(self):
        cr = CanonicalResult.success(
            canonical_task_id="task-summary",
            executor_id="exec-summary",
            result_data={"payload": "domain"},
            output_artifacts=[{"path": "a", "digest": "d"}],
            execution_stats={"dur": 1},
            stdout_summary="out",
            stderr_summary="err",
            exit_code=0,
            correlation_id="corr-summary",
        )
        proj = _map_execution_result(cr)
        d = proj.to_dict()
        # identity isolated
        for f in ("canonical_task_id", "executor_id", "canonical_task_state", "correlation_id"):
            assert f not in d
        # runtime isolated
        for f in ("exit_code", "stdout_summary", "stderr_summary", "execution_stats"):
            assert f not in d
        # result_data/output_artifacts isolated
        for f in ("result_data", "output_artifacts"):
            assert f not in d
        # artifact not auto-promoted
        assert proj.artifact_refs == ()
        assert proj.evidence_refs == ()
        # common fields only
        allowed = {"governance_version", "outcome", "error", "provenance", "completeness", "artifact_refs", "evidence_refs", "verification", "side_effect_outcome"}
        for k in d:
            assert k in allowed
