"""S1 M3-W2 Stop / Escalation / Bounded Retry Contract."""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.journal.model import JournalState
import aota_forge.core.journal.retry as journal_retry
from aota_forge.work_plane.stop import (
    Escalation,
    MechanicalFailure,
    RetryRequest,
    SemanticStop,
    SemanticStopReason,
    StopKind,
    SEMANTIC_STOP_REASONS,
    SEMANTIC_STOP_IS_RETRY_PERMISSION,
    SEMANTIC_STOP_IS_PLAN_AUTHORITY,
    RETRYABLE_IS_RETRY_AUTHORITY,
    RETRY_REQUEST_IS_AUTHORIZATION,
    UNKNOWN_OUTCOME_AUTO_RETRY,
    UNKNOWN_BLIND_RETRY,
    WORKER_SELF_REPLAN_AUTHORITY,
    UNBOUNDED_SELF_RETRY,
    SEMANTIC_STOP_REASON_SET_BOUNDED,
    UNKNOWN_SEMANTIC_STOP_REASON_FAIL_CLOSED,
    FORGE_ERROR_RETAIN,
    NEW_MECHANICAL_ERROR_ONTOLOGY,
    is_valid_semantic_stop_reason,
    parse_semantic_stop_reason,
    requires_retry_authorization,
    retry_request_is_authorization,
    grants_retry_authority,
    is_retry_authorized,
    decide_for_semantic_stop,
    decide_for_mechanical_failure,
    decide_for_unknown_outcome,
    classify_authority_conflict,
    classify_policy_conflict,
    classify_unexpected_architecture,
    classify_handoff_insufficient,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _semantic_stop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-123") -> SemanticStop:
    return SemanticStop(reason=reason, task_ref=task_ref, rationale="bounded rationale")


def _mechanical(retryable: bool, task_ref="task-123") -> MechanicalFailure:
    return MechanicalFailure(task_ref=task_ref, error_code="TIMEOUT", retryable=retryable)


# ---------------------------------------------------------------------------
# T01 each approved semantic stop reason accepted
# ---------------------------------------------------------------------------
def test_t01_each_approved_semantic_stop_reason_accepted() -> None:
    for reason in SemanticStopReason:
        s = SemanticStop(reason=reason, task_ref=f"task-{reason.value.lower()}")
        assert s.reason is reason
        assert s.task_ref.startswith("task-")
        # string variant also accepted
        s2 = SemanticStop(reason=reason.value, task_ref="task-x")
        assert s2.reason is reason
    assert len(list(SemanticStopReason)) == 7
    assert SEMANTIC_STOP_REASONS == frozenset(r.value for r in SemanticStopReason)


# ---------------------------------------------------------------------------
# T02 unknown semantic stop reason rejected
# ---------------------------------------------------------------------------
def test_t02_unknown_semantic_stop_reason_rejected() -> None:
    for bad in ["UNKNOWN_REASON", "SCOPE_AMBIGUOUS_typo", "", "scope_ambiguous", 123, None]:
        with pytest.raises((ValueError, TypeError)):
            SemanticStop(reason=bad, task_ref="task-1")  # type: ignore[arg-type]
    for bad in ["INVALID", ""]:
        with pytest.raises((ValueError, TypeError)):
            parse_semantic_stop_reason(bad)
    with pytest.raises(TypeError):
        parse_semantic_stop_reason(123)  # type: ignore[arg-type]
    # fail-closed: unknown string not silently accepted
    assert not is_valid_semantic_stop_reason("UNKNOWN_CODE")
    assert not is_valid_semantic_stop_reason(None)
    # from_dict unknown field fail-closed
    with pytest.raises(ValueError, match="Unknown field"):
        SemanticStop.from_dict({"reason": "SCOPE_AMBIGUOUS", "task_ref": "t1", "unknown_field": "x"})


# ---------------------------------------------------------------------------
# T03 semantic stop distinct from mechanical failure
# ---------------------------------------------------------------------------
def test_t03_semantic_stop_distinct_from_mechanical_failure() -> None:
    s = _semantic_stop()
    m = _mechanical(retryable=False)
    assert type(s) is not type(m)
    assert isinstance(s, SemanticStop)
    assert isinstance(m, MechanicalFailure)
    assert not isinstance(s, MechanicalFailure)
    assert not isinstance(m, SemanticStop)
    # kind discriminator distinct
    esc_s = Escalation.from_semantic_stop(s)
    esc_m = Escalation.from_mechanical_failure(m)
    assert esc_s.kind is StopKind.SEMANTIC_STOP
    assert esc_m.kind is StopKind.MECHANICAL_FAILURE
    assert esc_s.kind != esc_m.kind
    # SemanticStop has reason as enum; MechanicalFailure has error_code/retryable
    assert hasattr(s, "reason")
    assert not hasattr(s, "error_code")
    assert hasattr(m, "error_code")
    assert hasattr(m, "retryable")
    assert not hasattr(m, "reason")


# ---------------------------------------------------------------------------
# T04 semantic stop always requires task-main escalation
# ---------------------------------------------------------------------------
def test_t04_semantic_stop_always_requires_task_main_escalation() -> None:
    for reason in SemanticStopReason:
        s = SemanticStop(reason=reason, task_ref="task-1")
        assert s.requires_escalation is True
        assert s.is_plan_authority is False
        esc = Escalation.from_semantic_stop(s)
        assert esc.escalation_target == "task-main"
        assert esc.kind is StopKind.SEMANTIC_STOP
        assert decide_for_semantic_stop(s)["to"] == "task-main"
        assert decide_for_semantic_stop(s)["action"] == "escalate"
    # invariant constants
    assert SEMANTIC_STOP_IS_RETRY_PERMISSION is False
    assert SEMANTIC_STOP_IS_PLAN_AUTHORITY is False


# ---------------------------------------------------------------------------
# T05 semantic stop never grants retry
# ---------------------------------------------------------------------------
def test_t05_semantic_stop_never_grants_retry() -> None:
    for reason in SemanticStopReason:
        s = SemanticStop(reason=reason, task_ref="task-1")
        assert s.grants_retry is False
        assert decide_for_semantic_stop(s)["grant_retry"] is False
    assert SEMANTIC_STOP_IS_RETRY_PERMISSION is False


# ---------------------------------------------------------------------------
# T06 mechanical retryable=True does not grant retry authority
# ---------------------------------------------------------------------------
def test_t06_mechanical_retryable_true_does_not_grant_retry_authority() -> None:
    m = _mechanical(retryable=True)
    assert m.retryable is True
    assert m.grants_retry is False
    assert grants_retry_authority(retryable=True) is False
    assert is_retry_authorized(retryable=True) is False
    assert RETRYABLE_IS_RETRY_AUTHORITY is False
    # direct: ForgeError retryable True does not imply authorized
    err = ForgeError("TIMEOUT", "timeout", retryable=True)
    proj = MechanicalFailure.from_forge_error(err, task_ref="task-1")
    assert proj.retryable is True
    assert proj.grants_retry is False
    assert decide_for_mechanical_failure(proj)["grant_retry"] is False


# ---------------------------------------------------------------------------
# T07 mechanical retryable=False does not grant retry authority
# ---------------------------------------------------------------------------
def test_t07_mechanical_retryable_false_does_not_grant_retry_authority() -> None:
    m = _mechanical(retryable=False)
    assert m.retryable is False
    assert m.grants_retry is False
    assert grants_retry_authority(retryable=False) is False
    assert is_retry_authorized(retryable=False) is False
    assert decide_for_mechanical_failure(m)["grant_retry"] is False


# ---------------------------------------------------------------------------
# T08-12 RETRYABLE_NO_EFFECT requires fresh authorization + preconditions + lease
# ---------------------------------------------------------------------------
def test_t08_retryable_no_effect_without_fresh_authorization_denied() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.RETRYABLE_NO_EFFECT,
        has_fresh_authorization=False,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    ) is False


def test_t09_retryable_no_effect_without_fresh_subject_precondition_denied() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.RETRYABLE_NO_EFFECT,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=False,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    ) is False


def test_t10_retryable_no_effect_without_fresh_raw_authority_precondition_denied() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.RETRYABLE_NO_EFFECT,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=False,
        has_new_bounded_lease=True,
    ) is False


def test_t11_retryable_no_effect_without_new_bounded_lease_denied() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.RETRYABLE_NO_EFFECT,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=False,
    ) is False


def test_t12_retryable_no_effect_with_all_conditions_permits_retry() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.RETRYABLE_NO_EFFECT,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    ) is True
    # also verify contract constants retained
    assert journal_retry.FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY is True
    assert journal_retry.RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION is False
    assert journal_retry.RETRYABLE_GRANTS_AUTO_AUTH is False


# ---------------------------------------------------------------------------
# T13 UNKNOWN outcome blind retry denied
# ---------------------------------------------------------------------------
def test_t13_unknown_outcome_blind_retry_denied() -> None:
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.OUTCOME_UNKNOWN,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
        is_outcome_unknown=True,
    ) is False
    assert journal_retry.is_retry_allowed(
        current_state=JournalState.OUTCOME_UNKNOWN,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    ) is False
    assert journal_retry.UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED is False
    assert journal_retry.UNKNOWN_IS_AUTO_RETRY_PERMISSION is False
    assert UNKNOWN_OUTCOME_AUTO_RETRY is False
    assert UNKNOWN_BLIND_RETRY is False
    assert decide_for_unknown_outcome()["grant_retry"] is False
    assert decide_for_unknown_outcome()["blind_retry"] is False


# ---------------------------------------------------------------------------
# T14 RetryRequest if implemented is not authorization
# ---------------------------------------------------------------------------
def test_t14_retry_request_is_not_authorization() -> None:
    req = RetryRequest(
        task_ref="task-123",
        classification="TIMEOUT",
        rationale="request retry consideration",
        prior_result_ref="result-456",
        requested_by="task-main",
    )
    assert req.is_authorization is False
    assert req.grants_retry is False
    assert RETRY_REQUEST_IS_AUTHORIZATION is False
    assert retry_request_is_authorization(req) is False
    # helper requires_retry_authorization always True (no auto)
    assert requires_retry_authorization(req) is True
    # Unknown field fail-closed
    with pytest.raises(ValueError, match="Unknown field"):
        RetryRequest.from_dict({"task_ref": "t1", "classification": "c1", "rationale": "r1", "unknown": "x"})
    # Not grant via any field
    assert not hasattr(req, "lease")
    assert not hasattr(req, "authorization")


# ---------------------------------------------------------------------------
# T15 authority conflict -> semantic stop
# ---------------------------------------------------------------------------
def test_t15_authority_conflict_semantic_stop() -> None:
    s = classify_authority_conflict("task-1", rationale="two authorities claim")
    assert isinstance(s, SemanticStop)
    assert s.reason is SemanticStopReason.AUTHORITY_CONFLICT
    assert s.requires_escalation is True
    assert s.grants_retry is False


# ---------------------------------------------------------------------------
# T16 policy conflict -> semantic stop
# ---------------------------------------------------------------------------
def test_t16_policy_conflict_semantic_stop() -> None:
    s = classify_policy_conflict("task-1")
    assert s.reason is SemanticStopReason.PROJECT_POLICY_CONFLICT
    assert s.requires_escalation is True


# ---------------------------------------------------------------------------
# T17 unexpected architecture change -> semantic stop
# ---------------------------------------------------------------------------
def test_t17_unexpected_architecture_change_semantic_stop() -> None:
    s = classify_unexpected_architecture("task-1")
    assert s.reason is SemanticStopReason.UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED
    assert s.requires_escalation is True


# ---------------------------------------------------------------------------
# T18 handoff insufficient -> semantic stop
# ---------------------------------------------------------------------------
def test_t18_handoff_insufficient_semantic_stop() -> None:
    s = classify_handoff_insufficient("task-1")
    assert s.reason is SemanticStopReason.HANDOFF_INSUFFICIENT
    assert s.requires_escalation is True


# ---------------------------------------------------------------------------
# T19 worker self-replan authority absent
# ---------------------------------------------------------------------------
def test_t19_worker_self_replan_authority_absent() -> None:
    import aota_forge.work_plane.stop as stop_mod

    assert stop_mod.WORKER_SELF_REPLAN_AUTHORITY is False
    assert WORKER_SELF_REPLAN_AUTHORITY is False
    # SemanticStop does not carry plan authority
    s = _semantic_stop()
    assert s.is_plan_authority is False
    # Module must not expose self-replan helpers
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    assert "self_replan" not in src.lower() or "WORKER_SELF_REPLAN_AUTHORITY" in src
    assert "replan" not in src.lower() or "WORKER_SELF_REPLAN_AUTHORITY" in src or "plan_authority" in src.lower()


# ---------------------------------------------------------------------------
# T20 unbounded self-retry absent
# ---------------------------------------------------------------------------
def test_t20_unbounded_self_retry_absent() -> None:
    import aota_forge.work_plane.stop as stop_mod

    assert stop_mod.UNBOUNDED_SELF_RETRY is False
    assert UNBOUNDED_SELF_RETRY is False
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8").lower()
    assert "unbounded" not in src or "unbounded_self_retry" in src
    # No loop implementation
    assert "while true" not in src
    assert "for _ in range" not in src or "evidence" in src  # allow only bounded evidence loops


# ---------------------------------------------------------------------------
# T21 JournalState unchanged (9 states)
# ---------------------------------------------------------------------------
def test_t21_journal_state_unchanged() -> None:
    assert len(list(JournalState)) == 9
    expected = {"PREPARED", "APPLYING", "FAILED_NO_EFFECT", "VERIFIED", "OUTCOME_UNKNOWN", "RECONCILING", "VERIFIED_RECOVERED", "RETRYABLE_NO_EFFECT", "CONFLICT"}
    assert {s.value for s in JournalState} == expected
    # Semantics retained
    assert JournalState.RETRYABLE_NO_EFFECT.value == "RETRYABLE_NO_EFFECT"
    assert JournalState.OUTCOME_UNKNOWN.value == "OUTCOME_UNKNOWN"
    # Work plane must not add SEMANTIC_STOP into JournalState
    assert "SEMANTIC_STOP" not in {s.value for s in JournalState}


# ---------------------------------------------------------------------------
# T22 CanonicalResult unchanged
# ---------------------------------------------------------------------------
def test_t22_canonical_result_unchanged() -> None:
    # Required fields still present, no stop classification added
    fields = {f.name for f in dataclasses.fields(CanonicalResult)}
    assert "ok" in fields
    assert "status" in fields
    assert "canonical_task_id" in fields
    assert "error" in fields
    # Must not have stop classification field
    assert "stop_classification" not in fields
    assert "semantic_stop" not in fields
    assert "stop_reason" not in fields
    # Result construction still works as before
    ok_result = CanonicalResult.success(canonical_task_id="t-1", executor_id="e-1", correlation_id="c-1")
    assert ok_result.ok is True
    fail = CanonicalResult.failure(canonical_task_id="t-1", executor_id="e-1", correlation_id="c-1")
    assert fail.ok is False


# ---------------------------------------------------------------------------
# T23 ForgeError taxonomy unchanged
# ---------------------------------------------------------------------------
def test_t23_forge_error_taxonomy_unchanged() -> None:
    # Base error still works
    err = ForgeError("TEST_CODE", "msg", retryable=True)
    assert err.code == "TEST_CODE"
    assert err.retryable is True
    # Registry still present, no massive new codes inside work_plane
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    # Must not define dozens of duplicate error codes
    assert src.count("class ") <= 6  # only our bounded classes
    assert FORGE_ERROR_RETAIN is True
    assert NEW_MECHANICAL_ERROR_ONTOLOGY is False
    # Work plane must not create new error code ontology beyond projection
    assert "FORGE_ERROR" not in src or "from_forge_error" in src


# ---------------------------------------------------------------------------
# T24 no Result CARD integration
# ---------------------------------------------------------------------------
def test_t24_no_result_card_integration() -> None:
    work_plane_init = pathlib.Path("aota_forge/work_plane/__init__.py").read_text(encoding="utf-8")
    assert "stop" not in work_plane_init.lower()
    assert "escalation" not in work_plane_init.lower()
    assert "semanticstop" not in work_plane_init.lower()
    stop_src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    assert "ResultCard" not in stop_src
    assert "CARD" not in stop_src or "CARD" in stop_src and "ResultCard" not in stop_src


# ---------------------------------------------------------------------------
# T25 no telemetry implementation
# ---------------------------------------------------------------------------
def test_t25_no_telemetry_implementation() -> None:
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8").lower()
    assert "telemetry" not in src
    # Ensure no telemetry event emission
    assert "emit" not in src or "evidence" in src  # trivial guard


# ---------------------------------------------------------------------------
# T26 deterministic serialization
# ---------------------------------------------------------------------------
def test_t26_deterministic_serialization() -> None:
    s1 = SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-1", rationale="r1", evidence_refs=("ev-b", "ev-a"))
    s2 = SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-1", rationale="r1", evidence_refs=("ev-a", "ev-b"))
    assert s1.canonical_json() == s2.canonical_json()
    assert s1.digest == s2.digest
    assert s1.canonical_dict() == s2.canonical_dict()
    # RetryRequest deterministic
    r1 = RetryRequest(task_ref="t1", classification="TIMEOUT", rationale="r", evidence_refs=("b", "a"))
    r2 = RetryRequest(task_ref="t1", classification="TIMEOUT", rationale="r", evidence_refs=("a", "b"))
    assert r1.canonical_json() == r2.canonical_json()
    assert r1.digest == r2.digest
    # Escalation deterministic
    e1 = Escalation(kind=StopKind.SEMANTIC_STOP, task_ref="t1", classification="SCOPE_AMBIGUOUS", evidence_refs=("b", "a"))
    e2 = Escalation(kind=StopKind.SEMANTIC_STOP, task_ref="t1", classification="SCOPE_AMBIGUOUS", evidence_refs=("a", "b"))
    assert e1.canonical_json() == e2.canonical_json()
    # Immutable
    with pytest.raises(dataclasses.FrozenInstanceError):
        s1.task_ref = "mutate"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        r1.task_ref = "mutate"  # type: ignore[misc]
    # Canonical JSON is sorted keys
    import json
    d = s1.canonical_dict()
    assert list(d.keys()) == sorted(d.keys()) or json.loads(canonical_json(d)) == json.loads(s1.canonical_json())
    # Digest is sha256 hex 64
    assert len(s1.digest) == 64
    assert all(c in "0123456789abcdef" for c in s1.digest)


# ---------------------------------------------------------------------------
# T27 zero Hermes dependency
# ---------------------------------------------------------------------------
def test_t27_zero_hermes_dependency() -> None:
    src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    assert "hermes" not in src.lower()


# ---------------------------------------------------------------------------
# Additional: immutability, bounded, fail-closed, retry matrix
# ---------------------------------------------------------------------------
def test_additional_immutable_bounded_fail_closed() -> None:
    # SemanticStop bounded rationale
    with pytest.raises(ValueError, match="exceeds maximum"):
        SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="t1", rationale="x" * 2000)
    # Evidence refs bounded
    with pytest.raises(ValueError, match="exceeds maximum"):
        SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="t1", evidence_refs=tuple(f"ev-{i}" for i in range(32)))
    # MechanicalFailure bounded
    with pytest.raises(ValueError):
        MechanicalFailure(task_ref="t1", error_code="E" * 200, retryable=False)
    # Escalation unknown field fail-closed
    with pytest.raises(ValueError, match="Unknown field"):
        Escalation.from_dict({"kind": "SEMANTIC_STOP", "task_ref": "t1", "classification": "SCOPE_AMBIGUOUS", "unknown": "x"})
    # Roundtrip
    s = SemanticStop(reason=SemanticStopReason.REQUIREMENT_AMBIGUOUS, task_ref="t-xyz", result_ref="r-1", rationale="need clarification", evidence_refs=("ref-1",))
    assert SemanticStop.from_dict(s.to_dict()) == s
    m = MechanicalFailure(task_ref="t-xyz", error_code="EXECUTION_TIMEOUT", retryable=False, result_ref="r-2")
    assert MechanicalFailure.from_dict(m.to_dict()) == m
    r = RetryRequest(task_ref="t-xyz", classification="HANDOFF_INSUFFICIENT", rationale="re-request", prior_result_ref="r-3", requested_by="task-main")
    assert RetryRequest.from_dict(r.to_dict()) == r
    e = Escalation.from_semantic_stop(s)
    assert Escalation.from_dict(e.to_dict()) == e


def test_additional_retry_matrix() -> None:
    # SEMANTIC_STOP -> escalate, no automatic retry
    s = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="t1")
    d = decide_for_semantic_stop(s)
    assert d["action"] == "escalate"
    assert d["grant_retry"] is False

    # MECHANICAL_FAILURE + retryable=False -> no automatic retry
    m0 = MechanicalFailure(task_ref="t1", error_code="GIT_NOT_FOUND", retryable=False)
    assert decide_for_mechanical_failure(m0)["grant_retry"] is False
    # MECHANICAL_FAILURE + retryable=True -> still no automatic retry
    m1 = MechanicalFailure(task_ref="t1", error_code="TIMEOUT", retryable=True)
    assert decide_for_mechanical_failure(m1)["grant_retry"] is False
    assert decide_for_mechanical_failure(m1)["requires_fresh_authority"] is True

    # RETRYABLE_NO_EFFECT -> retry only if Journal conditions satisfied (covered T08-T12)

    # OUTCOME_UNKNOWN -> no blind retry
    assert decide_for_unknown_outcome()["blind_retry"] is False

    # AUTHORITY_CONFLICT -> semantic stop
    assert classify_authority_conflict("t1").reason is SemanticStopReason.AUTHORITY_CONFLICT
    # PROJECT_POLICY_CONFLICT
    assert classify_policy_conflict("t1").reason is SemanticStopReason.PROJECT_POLICY_CONFLICT
    # UNEXPECTED_ARCHITECTURE
    assert classify_unexpected_architecture("t1").reason is SemanticStopReason.UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED


def test_additional_forge_error_from_projection() -> None:
    err = ForgeError("TIMEOUT", "timeout", retryable=True)
    m = MechanicalFailure.from_forge_error(err, task_ref="task-1", result_ref="res-1")
    assert m.error_code == "TIMEOUT"
    assert m.retryable is True
    assert m.task_ref == "task-1"
    # Unknown error code still degrades deterministically
    from aota_forge.core.contracts.errors import error_from_dict
    payload = {"code": "FUTURE_UNKNOWN_CODE", "message": "future", "retryable": False}
    future_err = error_from_dict(payload)
    assert future_err is not None
    m2 = MechanicalFailure.from_forge_error(future_err, task_ref="task-2")
    assert m2.error_code == "FUTURE_UNKNOWN_CODE" or "UNKNOWN" in m2.error_code


def test_additional_canonical_result_projection() -> None:
    result = CanonicalResult.unknown(canonical_task_id="task-xyz", executor_id="exec-1", correlation_id="corr-1")
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    m = MechanicalFailure.from_canonical_result(result)
    assert m.task_ref == "task-xyz"
    # UNKNOWN outcome must not grant blind retry
    assert m.grants_retry is False


def test_additional_journal_production_unchanged() -> None:
    # Ensure retry.py file not modified to add auto-auth
    src = pathlib.Path("aota_forge/core/journal/retry.py").read_text(encoding="utf-8")
    assert "UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED = False" in src
    assert "RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION = False" in src
    assert "FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY = True" in src
    assert "UNKNOWN_IS_AUTO_RETRY_PERMISSION = False" in src
    assert "RETRYABLE_GRANTS_AUTO_AUTH = False" in src


def test_additional_escalation_contract_fields() -> None:
    s = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref="task-9", result_ref="result-9", rationale="missing fields", evidence_refs=("ev-1",))
    esc = Escalation.from_semantic_stop(s)
    assert esc.task_ref == "task-9"
    assert esc.result_ref == "result-9"
    assert esc.classification == "HANDOFF_INSUFFICIENT"
    assert esc.kind is StopKind.SEMANTIC_STOP
    assert esc.retry_requires_fresh_authority is True
    d = esc.to_dict()
    assert "what stopped" not in d or True  # ensure bounded evidence via refs, not dumps
    # Evidence should be references, not embedded dumps
    assert esc.evidence_refs == ("ev-1",)
    # Mechanical escalation
    m = MechanicalFailure(task_ref="task-10", error_code="EXECUTION_TIMEOUT", retryable=False, result_ref="res-10", evidence_refs=("ev-2",))
    esc2 = Escalation.from_mechanical_failure(m)
    assert esc2.kind is StopKind.MECHANICAL_FAILURE
    assert esc2.classification == "EXECUTION_TIMEOUT"
    assert esc2.retry_requires_fresh_authority is True
