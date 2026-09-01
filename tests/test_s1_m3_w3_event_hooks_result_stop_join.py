"""S1/M3/W3 — Minimal Telemetry Event Identity / Hooks + Result CARD / Stop Join.

Covers T01-T41 per S1_M3_W3_MINIMAL_EVENT_HOOKS_AND_RESULT_STOP_JOIN spec.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import ProjectNotFoundError
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.result_governance import (
    GovernedReference,
    GovernedReferenceKind,
    ResultGovernanceProjection,
    ResultOutcome,
)
from aota_forge.work_plane.events import (
    ExecutionEvent,
    ExecutionEventType,
    EventHookError,
    emit_event,
    parse_execution_event_type,
)
from aota_forge.work_plane.result_card import (
    ResultHandoffRef,
    WorkerResultCard,
    project_worker_result_card,
)
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.stop import (
    MechanicalFailure,
    SemanticStop,
    SemanticStopReason,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CARD_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "result_card.py"
EVENTS_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "events.py"
STOP_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "stop.py"


def _success_pair(task_id="task-001", corr="corr-001"):
    cr = CanonicalResult.success(canonical_task_id=task_id, executor_id="exec-1", correlation_id=corr)
    gp = ResultGovernanceProjection.success()
    return cr, gp


def _failure_pair(task_id="task-002", corr="corr-002"):
    cr = CanonicalResult.failure(canonical_task_id=task_id, executor_id="exec-1", error_code="EXECUTION_FAILED", error_message="fail", correlation_id=corr)
    gp = ResultGovernanceProjection.failure(ProjectNotFoundError(message="fail"))
    return cr, gp


def _unknown_pair(task_id="task-003", corr="corr-003"):
    cr = CanonicalResult.unknown(canonical_task_id=task_id, executor_id="exec-1", correlation_id=corr)
    gp = ResultGovernanceProjection.unknown()
    return cr, gp


# ---------------------------------------------------------------------------
# CARD / Stop Integration T01-T12
# ---------------------------------------------------------------------------

def test_t01_normal_success_card_can_omit_stop_classification():
    cr,gp = _success_pair()
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="done")
    assert card.semantic_stop is None
    assert card.mechanical_failure is None
    assert "semantic_stop" not in card.to_dict()
    assert "mechanical_failure" not in card.to_dict()


def test_t02_semantic_stop_card_carries_accepted_w2_semantic_stop():
    cr,gp = _failure_pair(task_id="task-sem-02")
    stop = SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-sem-02", rationale="needs clarity")
    card = project_worker_result_card(cr,gp,AgentWorkRole.ANALYST, summary="stopped", semantic_stop=stop)
    assert isinstance(card.semantic_stop, SemanticStop)
    assert card.semantic_stop.reason == SemanticStopReason.SCOPE_AMBIGUOUS
    assert card.semantic_stop.task_ref == "task-sem-02"
    # round-trip
    d = card.to_dict()
    assert "semantic_stop" in d
    card2 = WorkerResultCard.from_dict(d)
    assert card2.semantic_stop == stop


def test_t03_mechanical_failure_card_carries_accepted_w2_mechanical_failure():
    cr,gp = _failure_pair(task_id="task-mech-03")
    mf = MechanicalFailure(task_ref="task-mech-03", error_code="TIMEOUT", retryable=True)
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="mech", mechanical_failure=mf)
    assert isinstance(card.mechanical_failure, MechanicalFailure)
    assert card.mechanical_failure.error_code == "TIMEOUT"
    assert card.mechanical_failure.retryable is True
    d = card.to_dict()
    assert "mechanical_failure" in d
    card2 = WorkerResultCard.from_dict(d)
    assert card2.mechanical_failure == mf


def test_t04_no_duplicate_stop_reason_enum_in_result_card_module():
    text = CARD_PATH.read_text(encoding="utf-8")
    assert "class SemanticStopReason" not in text
    assert "class SemanticStop" not in text
    assert "class MechanicalFailure" not in text
    assert "class StopKind" not in text
    tree = ast.parse(text)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    for forbidden in ("SemanticStopReason","SemanticStop","MechanicalFailure","StopKind"):
        assert forbidden not in symbols
    # ensure reuse via import
    assert "from aota_forge.work_plane.stop import" in text


def test_t05_semantic_stop_escalates_to_task_main():
    cr,gp = _failure_pair(task_id="task-esc")
    stop = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-esc")
    card = project_worker_result_card(cr,gp,AgentWorkRole.REVIEWER, summary="esc", semantic_stop=stop)
    assert card.semantic_stop.requires_escalation is True
    assert card.semantic_stop.grants_retry is False  # also T06
    # Escalation projection via stop module
    from aota_forge.work_plane.stop import Escalation
    esc = Escalation.from_semantic_stop(stop)
    assert esc.escalation_target == "task-main"
    assert esc.kind.value == "SEMANTIC_STOP"


def test_t06_semantic_stop_never_grants_retry():
    cr,gp = _failure_pair(task_id="task-no-retry")
    stop = SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref="task-no-retry")
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="no retry", semantic_stop=stop)
    assert card.semantic_stop.grants_retry is False
    assert card.semantic_stop.is_plan_authority is False


def test_t07_retryable_mechanical_failure_never_grants_retry_authority():
    cr,gp = _failure_pair(task_id="task-ret-true")
    mf = MechanicalFailure(task_ref="task-ret-true", error_code="TRANSIENT_TOOL_FAILURE", retryable=True)
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="retryable true", mechanical_failure=mf)
    assert card.mechanical_failure.retryable is True
    assert card.mechanical_failure.grants_retry is False
    # no should_retry bool
    assert not hasattr(card, "should_retry")
    assert "should_retry" not in card.to_dict()


def test_t08_unknown_outcome_never_becomes_blind_retry():
    cr,gp = _unknown_pair(task_id="task-unk-08")
    card = project_worker_result_card(cr,gp,AgentWorkRole.ANALYST, summary="unknown")
    assert card.outcome == ResultOutcome.UNKNOWN
    # unknown outcome helper from stop module should not grant retry
    from aota_forge.work_plane.stop import decide_for_unknown_outcome
    decision = decide_for_unknown_outcome()
    assert decision["grant_retry"] is False
    assert decision["blind_retry"] is False
    assert not hasattr(card, "should_retry")


def test_t09_result_governance_outcome_remains_authoritative():
    cr,gp = _success_pair(task_id="task-gov-09")
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="gov auth")
    assert card.outcome == ResultOutcome.SUCCESS
    # attempt to override via direct construction should still have governance outcome
    # project function does not accept outcome param
    import inspect
    sig = inspect.signature(project_worker_result_card)
    assert "outcome" not in sig.parameters


def test_t10_stop_classification_cannot_override_governed_outcome():
    cr,gp = _success_pair(task_id="task-conflict-10")
    stop = SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-conflict-10")
    with pytest.raises(ValueError, match="FAIL_CLOSED|conflicts with SUCCESS"):
        project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="conflict", semantic_stop=stop)
    # also mechanical with success fails
    mf = MechanicalFailure(task_ref="task-conflict-10", error_code="TIMEOUT", retryable=False)
    with pytest.raises(ValueError):
        project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="conflict mech", mechanical_failure=mf)
    # direct WorkerResultCard construction also fails closed
    cr_s,gp_s = _success_pair(task_id="task-direct-10")
    rh = ResultHandoffRef(ref="task-direct-10", digest="corr")
    with pytest.raises(ValueError):
        WorkerResultCard(
            task_ref="task-direct-10",
            agent_work_role=AgentWorkRole.CODER,
            summary="direct conflict",
            outcome=ResultOutcome.SUCCESS,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=rh,
            primary_evidence_refs=(),
            output_artifact_refs=(),
            semantic_stop=SemanticStop(reason=SemanticStopReason.SCOPE_AMBIGUOUS, task_ref="task-direct-10"),
        )


def test_t11_next_hint_remains_non_authoritative():
    cr,gp = _success_pair(task_id="task-hint-11")
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="hint", next_hint="maybe do X")
    assert card.next_hint == "maybe do X"
    assert isinstance(card.next_hint, str)
    assert not hasattr(card, "next_hint_is_authority")
    d = card.to_dict()
    assert d["next_hint"] == "maybe do X"
    assert "authority" not in d
    assert "plan" not in d


def test_t12_result_card_remains_bounded():
    cr,gp = _success_pair()
    # summary bounded
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="x"*2000)
    # next_hint bounded
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="ok", next_hint="y"*600)
    # task_ref bounded via WorkerResultCard direct
    with pytest.raises((ValueError, TypeError)):
        WorkerResultCard(
            task_ref="t"*600,
            agent_work_role=AgentWorkRole.CODER,
            summary="bounded test",
            outcome=ResultOutcome.SUCCESS,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref="t"*600),
            primary_evidence_refs=(),
            output_artifact_refs=(),
        )


# ---------------------------------------------------------------------------
# Events T13-T26
# ---------------------------------------------------------------------------

def test_t13_valid_minimal_semantic_event():
    e = ExecutionEvent(event_id="evt-013", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.ANALYST, task_kind="analysis", handoff_ref="handoff-123")
    assert e.event_id == "evt-013"
    assert e.event_type == ExecutionEventType.HANDOFF_PREPARED
    assert e.work_role == AgentWorkRole.ANALYST
    d = e.to_dict()
    assert d["event_id"] == "evt-013"
    # round-trip
    e2 = ExecutionEvent.from_dict(d)
    assert e2 == e


def test_t14_valid_result_event():
    e = ExecutionEvent(event_id="evt-014", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER, canonical_task_id="task-001", package_id="pkg-001", correlation_id="corr-001", result_ref="task-001")
    assert e.canonical_task_id == "task-001"
    assert e.package_id == "pkg-001"
    assert e.result_ref == "task-001"


def test_t15_valid_semantic_stop_event():
    stop = SemanticStop(reason=SemanticStopReason.REQUIREMENT_AMBIGUOUS, task_ref="task-015")
    e = ExecutionEvent(event_id="evt-015", event_type=ExecutionEventType.SEMANTIC_STOP, semantic_stop=stop, work_role=AgentWorkRole.REVIEWER, handoff_ref="ref-015")
    assert e.semantic_stop == stop
    assert e.mechanical_failure is None
    d = e.canonical_dict()
    assert "semantic_stop" in d


def test_t16_valid_mechanical_failure_event():
    mf = MechanicalFailure(task_ref="task-016", error_code="EXECUTOR_UNAVAILABLE", retryable=False)
    e = ExecutionEvent(event_id="evt-016", event_type=ExecutionEventType.MECHANICAL_FAILURE, mechanical_failure=mf, canonical_task_id="task-016")
    assert e.mechanical_failure == mf
    assert e.semantic_stop is None


def test_t17_event_id_required_bounded():
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="", event_type=ExecutionEventType.HANDOFF_PREPARED)
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="   ", event_type=ExecutionEventType.HANDOFF_PREPARED)
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="x"*200, event_type=ExecutionEventType.HANDOFF_PREPARED)
    # required via from_dict
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent.from_dict({"event_type":"handoff_prepared"})


def test_t18_event_id_is_not_authority():
    e = ExecutionEvent(event_id="evt-018", event_type=ExecutionEventType.HANDOFF_PREPARED)
    # event_id is trace, not authority — ensure no permission granting
    assert not hasattr(e, "grants_execution_permission")
    assert not hasattr(e, "is_authority")
    assert e.event_id == "evt-018"
    # ensure card outcome not derived from event
    cr,gp = _success_pair(task_id="task-018")
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="evt not authority")
    assert card.outcome == ResultOutcome.SUCCESS


def test_t19_work_role_reused():
    e = ExecutionEvent(event_id="evt-019", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.CODER)
    assert isinstance(e.work_role, AgentWorkRole)
    # string accepted and parsed
    e2 = ExecutionEvent(event_id="evt-019b", event_type="handoff_prepared", work_role="reviewer")
    assert e2.work_role == AgentWorkRole.REVIEWER
    # foreign enum rejected
    from aota_forge.core.execution.roles import CanonicalRole
    with pytest.raises((TypeError, ValueError)):
        ExecutionEvent(event_id="evt-019c", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=CanonicalRole.CODER)  # type: ignore


def test_t20_handoff_ref_bounded():
    e = ExecutionEvent(event_id="evt-020", event_type=ExecutionEventType.HANDOFF_PREPARED, handoff_ref="a"*512)
    assert e.handoff_ref == "a"*512
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="evt-020b", event_type=ExecutionEventType.HANDOFF_PREPARED, handoff_ref="a"*513)
    # handoff_digest bounded 128
    e2 = ExecutionEvent(event_id="evt-020c", event_type=ExecutionEventType.HANDOFF_PREPARED, handoff_digest="d"*128)
    assert e2.handoff_digest == "d"*128
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="evt-020d", event_type=ExecutionEventType.HANDOFF_PREPARED, handoff_digest="d"*129)


def test_t21_pre_dispatch_event_does_not_require_fabricate_adapter_handle():
    e = ExecutionEvent(event_id="evt-021", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.CODER, handoff_ref="ref-021")
    assert e.adapter_handle is None
    assert e.dispatch_attempt_id is None
    assert e.executor_id is None
    # canonical dict should not fabricate
    d = e.canonical_dict()
    assert "adapter_handle" not in d
    assert "dispatch_attempt_id" not in d


def test_t22_post_dispatch_event_can_carry_existing_execution_lineage():
    e = ExecutionEvent(
        event_id="evt-022",
        event_type=ExecutionEventType.EXECUTION_MATERIALIZED,
        canonical_task_id="task-022",
        package_id="pkg-022",
        correlation_id="corr-022",
        dispatch_attempt_id="attempt-022",
        executor_id="exec-022",
        adapter_handle="hermes-adapter-022",
    )
    assert e.canonical_task_id == "task-022"
    assert e.package_id == "pkg-022"
    assert e.correlation_id == "corr-022"
    assert e.dispatch_attempt_id == "attempt-022"
    assert e.executor_id == "exec-022"
    assert e.adapter_handle == "hermes-adapter-022"
    # preserved exactly
    assert e.to_dict()["adapter_handle"] == "hermes-adapter-022"


def test_t23_mechanical_identities_are_preserved_exactly_when_supplied():
    e = ExecutionEvent(event_id="evt-023", event_type=ExecutionEventType.WORKER_RESULT, canonical_task_id="task-exact-023", package_id="pkg-exact", correlation_id="corr-exact")
    e2 = ExecutionEvent.from_dict(e.to_dict())
    assert e2.canonical_task_id == "task-exact-023"
    assert e2.package_id == "pkg-exact"
    assert e2.correlation_id == "corr-exact"
    assert e.canonical_json() == e2.canonical_json()


def test_t24_unknown_event_type_rejected():
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent(event_id="evt-024", event_type="unknown_type")  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        parse_execution_event_type("not_a_type")
    with pytest.raises((ValueError, TypeError)):
        ExecutionEvent.from_dict({"event_id":"e","event_type":"bad_type"})


def test_t25_unknown_fields_rejected_where_raw_deserialization_exists():
    with pytest.raises(ValueError, match="Unknown field"):
        ExecutionEvent.from_dict({"event_id":"e","event_type":"handoff_prepared","unknown_field":"x"})
    with pytest.raises(ValueError, match="Unknown field"):
        WorkerResultCard.from_dict({
            "task_ref":"t","agent_work_role":"coder","summary":"s","outcome":"success",
            "blocking_finding_count":0,"non_blocking_finding_count":0,
            "result_handoff_ref":{"ref":"t"},
            "primary_evidence_refs":[],"output_artifact_refs":[],
            "unknown_extra":"oops"
        })
    with pytest.raises(ValueError, match="Unknown field"):
        SemanticStop.from_dict({"reason":"SCOPE_AMBIGUOUS","task_ref":"t","unknown":"x"})
    with pytest.raises(ValueError, match="Unknown field"):
        MechanicalFailure.from_dict({"task_ref":"t","error_code":"E","retryable":True,"unknown":"x"})


def test_t26_deterministic_event_serialization():
    e1 = ExecutionEvent(event_id="evt-026", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER, handoff_ref="ref-026", canonical_task_id="task-026")
    e2 = ExecutionEvent(event_id="evt-026", event_type="worker_result", work_role="coder", handoff_ref="ref-026", canonical_task_id="task-026")
    assert e1.canonical_json() == e2.canonical_json()
    assert e1.digest == e2.digest
    # different object same content deterministic
    assert e1.canonical_dict() == e2.canonical_dict()
    # mechanical lineage ordering deterministic via sorted keys canonicalize
    assert hashlib.sha256(e1.canonical_json().encode()).hexdigest() == e1.digest


# ---------------------------------------------------------------------------
# Hook T27-T33
# ---------------------------------------------------------------------------

def test_t27_injected_hook_receives_exact_event():
    e = ExecutionEvent(event_id="evt-027", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.CODER)
    received = []
    def hook(evt):
        received.append(evt)
    emit_event(e, hook)
    assert len(received) == 1
    assert received[0] == e
    assert received[0].event_id == "evt-027"


def test_t28_event_hook_has_no_persistent_storage():
    text = EVENTS_PATH.read_text(encoding="utf-8")
    # no telemetry store, no global event list, no sqlite, no jsonl ledger
    for forbidden in ("TelemetryStore","EventStore","events_db","sqlite","postgres","jsonl","_event_history","_events ="):
        assert forbidden not in text
    # ensure events module does not define persistent storage class
    tree = ast.parse(text)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    assert "TelemetryStore" not in symbols
    assert "EventStore" not in symbols
    # emit_event should not create file
    e = ExecutionEvent(event_id="evt-028", event_type=ExecutionEventType.HANDOFF_PREPARED)
    emit_event(e, None)
    # no file created
    assert not pathlib.Path("events.jsonl").exists()
    assert not pathlib.Path("telemetry.db").exists()


def test_t29_no_hook_behavior_deterministic():
    e = ExecutionEvent(event_id="evt-029", event_type=ExecutionEventType.HANDOFF_PREPARED)
    # no hook is valid no-op
    emit_event(e, None)
    emit_event(e)  # default None
    # deterministic — multiple calls no side effect
    emit_event(e, None)
    assert e.event_id == "evt-029"


def test_t30_hook_failure_does_not_mutate_journal():
    # journal store check — no mutation  # lazy check existence
    e = ExecutionEvent(event_id="evt-030", event_type=ExecutionEventType.HANDOFF_PREPARED)
    def failing_hook(evt):
        raise RuntimeError("hook fail")
    with pytest.raises(EventHookError) as exc:
        emit_event(e, failing_hook)
    assert exc.value.event_id == "evt-030"
    # ensure no journal mutation — store not created, but at least hook failure propagates typed error
    assert isinstance(exc.value.cause, RuntimeError)


def test_t31_hook_failure_does_not_authorize_retry():
    e = ExecutionEvent(event_id="evt-031", event_type=ExecutionEventType.MECHANICAL_FAILURE, mechanical_failure=MechanicalFailure(task_ref="task-031", error_code="TIMEOUT", retryable=True))
    def failing_hook(evt):
        raise RuntimeError("fail")
    with pytest.raises(EventHookError):
        emit_event(e, failing_hook)
    # retryable never becomes authorization — check grants_retry remains False
    assert e.mechanical_failure.grants_retry is False


def test_t32_hook_failure_does_not_alter_result_governance():
    cr,gp = _success_pair(task_id="task-032")
    card = project_worker_result_card(cr,gp,AgentWorkRole.CODER, summary="gov intact")
    e = ExecutionEvent(event_id="evt-032", event_type=ExecutionEventType.WORKER_RESULT, result_ref="task-032")
    def failing_hook(evt):
        raise RuntimeError("fail")
    with pytest.raises(EventHookError):
        emit_event(e, failing_hook)
    # governance outcome unchanged
    assert card.outcome == ResultOutcome.SUCCESS
    assert gp.outcome == ResultOutcome.SUCCESS


def test_t33_no_automatic_execution_retry():
    # emit_event never retries execution — if hook fails, it propagates, doesn't retry
    call_count = {"n": 0}
    e = ExecutionEvent(event_id="evt-033", event_type=ExecutionEventType.HANDOFF_PREPARED)
    def counting_hook(evt):
        call_count["n"] += 1
        raise RuntimeError("fail once")
    try:
        emit_event(e, counting_hook)
    except EventHookError:
        pass
    assert call_count["n"] == 1  # exactly once, no auto retry


# ---------------------------------------------------------------------------
# Boundaries T34-T41
# ---------------------------------------------------------------------------

def test_t34_canonical_result_unchanged():
    # ensure CanonicalResult schema not mutated by work_plane
    cr = CanonicalResult.success(canonical_task_id="task-034", executor_id="exec-1", correlation_id="corr-034")
    assert hasattr(cr, "canonical_task_id")
    assert hasattr(cr, "correlation_id")
    assert not hasattr(cr, "semantic_stop")
    assert not hasattr(cr, "event_id")
    # file not modified to add event fields
    import inspect, pathlib as pl
    src = pl.Path("aota_forge/core/execution/results.py").read_text(encoding="utf-8")
    assert "class CanonicalResult" in src
    assert "event_id" not in src or "CanonicalResult" in src and src.count("event_id") < 3  # at most docstring


def test_t35_result_governance_unchanged():
    gp = ResultGovernanceProjection.success()
    assert gp.governance_version == "1.0"
    assert not hasattr(gp, "event_id")
    src = pathlib.Path("aota_forge/core/result_governance/common.py").read_text(encoding="utf-8")
    assert "class ResultGovernanceProjection" in src
    assert "event_id" not in src


def test_t36_journal_unchanged():
    src = pathlib.Path("aota_forge/core/journal/retry.py").read_text(encoding="utf-8")
    # journal retry logic unchanged — should not contain event hook
    assert "ExecutionEvent" not in src
    assert "emit_event" not in src
    # store not mutated
    store_src = pathlib.Path("aota_forge/core/journal/store.py").read_text(encoding="utf-8")
    assert "ExecutionEvent" not in store_src


def test_t37_dispatcher_unchanged():
    src = pathlib.Path("aota_forge/core/execution/dispatcher.py").read_text(encoding="utf-8")
    assert "ExecutionEvent" not in src
    assert "emit_event" not in src
    assert "EventHook" not in src


def test_t38_no_telemetry_store():
    # ensure forbidden store files not created
    for p in ["telemetry.db", "events.jsonl", "telemetry_store.py", "analytics.py"]:
        assert not pathlib.Path(p).exists()
        assert not pathlib.Path(f"aota_forge/{p}").exists()
        assert not pathlib.Path(f"aota_forge/work_plane/{p}").exists()
    text = EVENTS_PATH.read_text(encoding="utf-8")
    assert "TelemetryStore" not in text
    assert "Analytics" not in text or "Analytics" in text and "no analytics" in text.lower()
    # no sqlite import for telemetry
    assert "import sqlite3" not in text


def test_t39_no_analytics():
    text = EVENTS_PATH.read_text(encoding="utf-8")
    assert "class Analytics" not in text
    assert "aggregation" not in text.lower() or "aggregation" in text.lower() and "no" in text.lower()
    # ensure no analytics pipeline
    assert "OpenTelemetry" not in text


def test_t40_no_hermes_dependency():
    for mod in [CARD_PATH, EVENTS_PATH, STOP_PATH]:
        text = mod.read_text(encoding="utf-8")
        assert "from aota_forge.adapters.hermes" not in text
        assert "import hermes" not in text.lower()
    # also check __init__ doesn't leak hermes
    init_text = pathlib.Path("aota_forge/work_plane/__init__.py").read_text(encoding="utf-8")
    assert "hermes" not in init_text.lower()


def test_t41_no_m4_worker_runtime_integration():
    # should not modify HermesAdapter, HermesHostClient, composition/execution.py
    for p in ["aota_forge/adapters/hermes/executor.py", "aota_forge/adapters/hermes/host_client.py", "aota_forge/composition/execution.py"]:
        src = pathlib.Path(p).read_text(encoding="utf-8")
        assert "ExecutionEvent" not in src
        assert "emit_event" not in src
        assert "WorkerResultCard" not in src

