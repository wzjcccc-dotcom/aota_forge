"""S1/M3/W1 — Result Handoff / Worker Result CARD Projection."""

from __future__ import annotations

import dataclasses
import pathlib
import ast

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
from aota_forge.work_plane.roles import AgentWorkRole

from aota_forge.work_plane.result_card import (
    MAX_ARTIFACT_REFS,
    MAX_EVIDENCE_REFS,
    MAX_NEXT_HINT_LENGTH,
    MAX_SUMMARY_LENGTH,
    ResultHandoffRef,
    WorkerResultCard,
    project_worker_result_card,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CARD_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "result_card.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _success_pair(task_id: str = "task-001", corr: str = "corr-001"):
    cr = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="exec-1",
        correlation_id=corr,
    )
    gp = ResultGovernanceProjection.success()
    return cr, gp


def _failure_pair(task_id: str = "task-002", corr: str = "corr-002"):
    cr = CanonicalResult.failure(
        canonical_task_id=task_id,
        executor_id="exec-1",
        error_code="EXECUTION_FAILED",
        error_message="fail",
        correlation_id=corr,
    )
    gp = ResultGovernanceProjection.failure(ProjectNotFoundError(message="fail"))
    return cr, gp


def _unknown_pair(task_id: str = "task-003", corr: str = "corr-003"):
    cr = CanonicalResult.unknown(
        canonical_task_id=task_id,
        executor_id="exec-1",
        correlation_id=corr,
    )
    gp = ResultGovernanceProjection.unknown()
    return cr, gp


# ---------------------------------------------------------------------------
# T01-T03 outcome projection
# ---------------------------------------------------------------------------


def test_t01_success_governed_result_to_success_card():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="done ok")
    assert card.outcome == ResultOutcome.SUCCESS
    assert card.task_ref == cr.canonical_task_id


def test_t02_failure_governed_result_to_failure_card():
    cr, gp = _failure_pair()
    card = project_worker_result_card(cr, gp, "coder", summary="failed as expected")
    assert card.outcome == ResultOutcome.FAILURE


def test_t03_unknown_governed_result_to_unknown_card():
    cr, gp = _unknown_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.ANALYST, summary="unknown state")
    assert card.outcome == ResultOutcome.UNKNOWN


# ---------------------------------------------------------------------------
# T04 AgentWorkRole retained
# ---------------------------------------------------------------------------


def test_t04_agent_work_role_retained():
    cr, gp = _success_pair()
    for role in AgentWorkRole:
        card = project_worker_result_card(cr, gp, role, summary=f"role {role.value}")
        assert isinstance(card.agent_work_role, AgentWorkRole)
        assert card.agent_work_role == role
    # string accepted
    card2 = project_worker_result_card(cr, gp, "reviewer", summary="reviewer summary")
    assert card2.agent_work_role == AgentWorkRole.REVIEWER
    # foreign enum rejected
    from aota_forge.core.execution.roles import CanonicalRole

    with pytest.raises((TypeError, ValueError)):
        project_worker_result_card(cr, gp, CanonicalRole.CODER, summary="bad")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T05 TASK_REF traceable
# ---------------------------------------------------------------------------


def test_t05_task_ref_traceable():
    cr, gp = _success_pair(task_id="task-trace-123", corr="corr-x")
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="trace test")
    assert card.task_ref == "task-trace-123"
    assert card.task_ref == cr.canonical_task_id
    assert card.result_handoff_ref.ref == cr.canonical_task_id


# ---------------------------------------------------------------------------
# T06 bounded summary accepted, T07 oversized rejected
# ---------------------------------------------------------------------------


def test_t06_bounded_summary_accepted():
    cr, gp = _success_pair()
    summary = "a" * MAX_SUMMARY_LENGTH
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary=summary)
    assert card.summary == summary
    small = "ok summary"
    card2 = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary=small)
    assert card2.summary == small


def test_t07_oversized_summary_rejected():
    cr, gp = _success_pair()
    oversized = "x" * (MAX_SUMMARY_LENGTH + 1)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary=oversized)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="   ")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="")


# ---------------------------------------------------------------------------
# T08/T09 blocking/non-blocking >=0, T10 negative rejected
# ---------------------------------------------------------------------------


def test_t08_blocking_finding_count():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="counts", blocking_finding_count=0)
    assert card.blocking_finding_count == 0
    card2 = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="counts", blocking_finding_count=3)
    assert card2.blocking_finding_count == 3


def test_t09_non_blocking_finding_count():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="counts", non_blocking_finding_count=5)
    assert card.non_blocking_finding_count == 5


def test_t10_negative_finding_count_rejected():
    cr, gp = _success_pair()
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="neg", blocking_finding_count=-1)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="neg", non_blocking_finding_count=-1)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="neg", blocking_finding_count="bad")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T11 evidence refs bounded, T12 artifact refs bounded
# ---------------------------------------------------------------------------


def test_t11_evidence_refs_bounded():
    cr, gp = _success_pair()
    ev = [GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref=f"ev:{i}") for i in range(2)]
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="ev", primary_evidence_refs=ev)
    assert len(card.primary_evidence_refs) == 2
    # wrong kind rejected
    bad = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="bad", primary_evidence_refs=[bad])
    # oversized rejected
    many = [GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref=f"ev:{i}") for i in range(MAX_EVIDENCE_REFS + 1)]
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="many", primary_evidence_refs=many)


def test_t12_artifact_refs_bounded():
    cr, gp = _success_pair()
    arts = [GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref=f"art:{i}") for i in range(1)]
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="art", output_artifact_refs=arts)
    assert len(card.output_artifact_refs) == 1
    bad = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="bad", output_artifact_refs=[bad])
    many = [GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref=f"art:{i}") for i in range(MAX_ARTIFACT_REFS + 1)]
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="many", output_artifact_refs=many)


# ---------------------------------------------------------------------------
# T13 RESULT_HANDOFF_REF bounded/traceable
# ---------------------------------------------------------------------------


def test_t13_result_handoff_ref_bounded_traceable():
    cr, gp = _success_pair(task_id="task-hand-1", corr="corr-hand-1")
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="handoff")
    assert card.result_handoff_ref.ref == "task-hand-1"
    assert card.result_handoff_ref.digest == "corr-hand-1"
    # explicit ref must be traceable (must equal canonical_task_id)
    explicit = ResultHandoffRef(ref="task-hand-1", digest="corr-hand-1")
    card2 = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="explicit", result_handoff_ref=explicit)
    assert card2.result_handoff_ref.ref == "task-hand-1"
    # mismatched ref fails closed
    bad_ref = ResultHandoffRef(ref="other-task", digest="d")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="bad", result_handoff_ref=bad_ref)
    # empty ref rejected
    with pytest.raises((ValueError, TypeError)):
        ResultHandoffRef(ref="")
    with pytest.raises((ValueError, TypeError)):
        ResultHandoffRef(ref="  ")


# ---------------------------------------------------------------------------
# T14 NEXT_HINT optional, T15 bounded, T16 no authority
# ---------------------------------------------------------------------------


def test_t14_next_hint_optional():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no hint")
    assert card.next_hint is None
    assert "next_hint" not in card.canonical_dict() or card.canonical_dict().get("next_hint") is None
    card2 = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="with hint", next_hint="consider retry manually")
    assert card2.next_hint == "consider retry manually"


def test_t15_next_hint_bounded():
    cr, gp = _success_pair()
    ok_hint = "a" * MAX_NEXT_HINT_LENGTH
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="hint ok", next_hint=ok_hint)
    assert card.next_hint == ok_hint
    bad_hint = "x" * (MAX_NEXT_HINT_LENGTH + 1)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="hint bad", next_hint=bad_hint)
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="hint bad", next_hint="   ")


def test_t16_next_hint_has_no_authority():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="hint auth", next_hint="maybe do X")
    # next_hint is plain string, not authority object, not plan
    assert isinstance(card.next_hint, str)
    assert not hasattr(card, "next_hint_is_authority")
    # ensure card does not expose plan mutation or decision authority
    d = card.to_dict()
    assert d.get("next_hint") == "maybe do X"
    # no fields like plan, task-main decision
    assert "plan" not in d
    assert "authority" not in d


# ---------------------------------------------------------------------------
# T17 governance outcome cannot be caller-overridden
# ---------------------------------------------------------------------------


def test_t17_governance_outcome_cannot_be_overridden():
    cr, gp = _success_pair()
    # ensure function signature does not accept outcome param
    import inspect

    sig = inspect.signature(project_worker_result_card)
    assert "outcome" not in sig.parameters
    # even if caller tries to trick via summary containing PASS, outcome remains governance-backed
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="PASS semantic review")
    assert card.outcome == ResultOutcome.SUCCESS
    # failure pair remains failure regardless of summary
    cr2, gp2 = _failure_pair()
    card2 = project_worker_result_card(cr2, gp2, AgentWorkRole.CODER, summary="PASS text but failed")
    assert card2.outcome == ResultOutcome.FAILURE


# ---------------------------------------------------------------------------
# T18 identity contradiction fails closed
# ---------------------------------------------------------------------------


def test_t18_canonical_governance_identity_contradiction_fails_closed():
    # success canonical + failure governance => fail
    cr_success, _ = _success_pair(task_id="t-1")
    _, gp_failure = _failure_pair(task_id="t-1")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr_success, gp_failure, AgentWorkRole.CODER, summary="mismatch")
    # failure canonical + success governance => fail
    cr_fail, _ = _failure_pair(task_id="t-2")
    _, gp_success = _success_pair(task_id="t-2")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr_fail, gp_success, AgentWorkRole.CODER, summary="mismatch2")
    # unknown vs success mismatch
    cr_unknown, _ = _unknown_pair(task_id="t-3")
    with pytest.raises((ValueError, TypeError)):
        project_worker_result_card(cr_unknown, gp_success, AgentWorkRole.CODER, summary="mismatch3")


# ---------------------------------------------------------------------------
# T19-T22 no duplicate ontology
# ---------------------------------------------------------------------------


def test_t19_no_duplicate_error_ontology():
    card_text = CARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(card_text)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    for forbidden in ("ForgeError", "DuplicateError", "CardError", "WorkerError"):
        # CARD must not define its own error envelope class
        assert forbidden not in symbols or forbidden == "ForgeError" and "from aota_forge.core.contracts.errors import" in card_text
    # actual file should not define class error ontology
    assert "class ForgeError" not in card_text
    assert "class CardError" not in card_text
    # card fields must not contain error dict
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no err")
    assert not hasattr(card, "error")
    assert "error" not in card.to_dict()


def test_t20_no_duplicate_completeness_ontology():
    card_text = CARD_PATH.read_text(encoding="utf-8")
    assert "class ResultCompleteness" not in card_text
    assert "class Completeness" not in card_text
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no comp")
    assert not hasattr(card, "completeness")
    assert "completeness" not in card.to_dict()


def test_t21_no_duplicate_verification_ontology():
    card_text = CARD_PATH.read_text(encoding="utf-8")
    assert "VerificationStatus" not in card_text or "from aota_forge.core.result_governance import" in card_text and card_text.count("VerificationStatus") <= 2
    # ensure not defining Verification enum
    assert "class Verification" not in card_text
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no verify")
    assert not hasattr(card, "verification")
    assert "verification" not in card.to_dict()


def test_t22_no_duplicate_side_effect_ontology():
    card_text = CARD_PATH.read_text(encoding="utf-8")
    assert "class SideEffect" not in card_text
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no side")
    assert not hasattr(card, "side_effect_outcome")
    assert "side_effect_outcome" not in card.to_dict()


# ---------------------------------------------------------------------------
# T23 no STOP_CLASSIFICATION taxonomy, T24 no retry, T25 no telemetry
# ---------------------------------------------------------------------------


def test_t23_no_stop_classification_taxonomy():
    text = CARD_PATH.read_text(encoding="utf-8")
    for forbidden in ("class SemanticStopClass", "class MechanicalFailureClass", "class StopClassification"):
        assert forbidden not in text
    # ensure enum definition not present
    assert "SemanticStopClass" not in text or "No SemanticStopClass" in text
    # no stop_classification field in CARD
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no stop")
    assert not hasattr(card, "stop_classification")
    assert "stop_classification" not in card.to_dict()
    # ensure file does not define STOP_CLASSIFICATION enum equality
    tree = ast.parse(text)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    for forbidden in ("SemanticStopClass", "MechanicalFailureClass", "StopClassification"):
        assert forbidden not in symbols


def test_t24_no_retry_logic():
    text = CARD_PATH.read_text(encoding="utf-8").lower()
    # module must not contain retry implementation signals
    assert "def retry" not in text
    assert "auto_retry" not in text
    # card object has no retry attribute
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no retry")
    assert not hasattr(card, "retry")
    assert not hasattr(card, "retryable")


def test_t25_no_telemetry_hook_store():
    text = CARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    for forbidden in ("TelemetryStore", "TelemetryEvent", "AnalyticsEngine"):
        assert forbidden not in symbols
    # ensure no telemetry class defined, docstring mention allowed
    assert "class Telemetry" not in text
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="no tele")
    assert not hasattr(card, "telemetry")
    assert "telemetry" not in card.to_dict()


# ---------------------------------------------------------------------------
# T26 deterministic serialization
# ---------------------------------------------------------------------------


def test_t26_deterministic_serialization():
    cr, gp = _success_pair(task_id="task-det", corr="corr-det")
    ev1 = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:2")
    ev2 = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="ev:1")
    art = GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="art:1")
    card1 = project_worker_result_card(
        cr,
        gp,
        AgentWorkRole.CODER,
        summary="det test",
        primary_evidence_refs=[ev1, ev2],
        output_artifact_refs=[art],
        next_hint="hint",
    )
    card2 = project_worker_result_card(
        cr,
        gp,
        AgentWorkRole.CODER,
        summary="det test",
        primary_evidence_refs=[ev2, ev1],
        output_artifact_refs=[art],
        next_hint="hint",
    )
    # different insertion order but deterministic canonical_json
    assert card1.canonical_json() == card2.canonical_json()
    assert card1.card_digest == card2.card_digest
    # to_dict round-trip deterministic via from_dict
    d = card1.to_dict()
    card3 = WorkerResultCard.from_dict(d)
    assert card3.canonical_json() == card1.canonical_json()
    assert card3.card_digest == card1.card_digest


# ---------------------------------------------------------------------------
# T27 zero Hermes dependency
# ---------------------------------------------------------------------------


def test_t27_zero_hermes_dependency():
    text = CARD_PATH.read_text(encoding="utf-8")
    # ensure no hermes import (docstring mention allowed)
    assert "from aota_forge.adapters.hermes" not in text
    assert "import hermes" not in text
    tree = ast.parse(text)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    for imp in imports:
        assert "hermes" not in imp.lower()


# ---------------------------------------------------------------------------
# Additional: bounded checks for imports/regression
# ---------------------------------------------------------------------------


def test_card_frozen_immutable():
    cr, gp = _success_pair()
    card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="frozen")
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        card.summary = "mutated"  # type: ignore[misc]


def test_result_governance_not_changed():
    # ensure common.py still has expected version and not modified to add CARD
    assert ResultGovernanceProjection.success().governance_version == "1.0"


def test_canonical_result_not_changed():
    cr = CanonicalResult.success(canonical_task_id="t", executor_id="e", correlation_id="c")
    assert cr.canonical_task_id == "t"


def test_task_handoff_not_imported_as_dependency():
    text = CARD_PATH.read_text(encoding="utf-8")
    # card should not embed entire handoff, only use work_role/result types
    assert "TaskHandoff" not in text
