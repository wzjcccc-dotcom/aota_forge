"""AF #54 M3/W3 — Run-scoped bounded ToolEffectivenessCard tests.

Focused deterministic projection proof:
* bounded size and bounded entries
* deterministic projection (same input → same card digest)
* explicit completeness; missing telemetry is never zero; unknown
  denominators are never zero
* no raw payload / prompt retention
* repeat request + no-progress signal detection (factual only)
* cross-session isolation
* Skill selection/delivery/load facts; observed-use stays incomplete
* S6 metric subject/series identity reuse
"""

from __future__ import annotations

import json

import pytest

from aota_forge.work_plane.runtime_observation import ObservedSkill
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from aota_forge.work_plane.telemetry_effectiveness_card import (
    AUTOMATIC_ABORT,
    CARD_IS_AUTHORITY,
    CARD_SEMANTIC_JUDGMENT,
    MetricFact,
    ToolEffectivenessCard,
    WorkerLifecycleFact,
    build_run_telemetry_envelopes,
    build_tool_effectiveness_card,
)
from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation

_CONTRACT_HASH = "a" * 64


def _hex(tag: str) -> str:
    import hashlib

    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _obs(
    operation: str,
    digest: str,
    *,
    success: bool = True,
    error_code: str | None = None,
    outcome_class: str | None = None,
    session_ref: str | None = "sess-1",
    parent_session_ref: str | None = None,
    task_id: str | None = "aota_forge:M3:W1:abcd:12345678",
    milestone: str | None = "M3",
    work_item: str | None = "W1",
    started: str | None = "2026-09-14T06:00:00+00:00",
    ended: str | None = "2026-09-14T06:00:01+00:00",
    result_digest: str | None = None,
) -> ToolUsageObservation:
    if outcome_class is None:
        outcome_class = "success" if success else "failure_invalid_input"
    return ToolUsageObservation(
        observation_id=f"tuo-{operation}-{digest[:8]}-{started}",
        operation_name=operation,
        contract_hash=_CONTRACT_HASH,
        is_success=success,
        outcome_class=outcome_class,
        side_effect="read",
        session_ref=session_ref,
        parent_session_ref=parent_session_ref,
        canonical_task_id=task_id,
        milestone_ref=milestone,
        work_item_ref=work_item,
        error_code=error_code,
        result_digest=result_digest or ("b" * 64 if success else "c" * 64),
        normalized_request_digest=digest,
        started_at_utc=started,
        ended_at_utc=ended,
        duration_ns=1_000_000,
        timing_basis="monotonic",
    )


def _skill_obs(
    *,
    skill_id: str = "aota-workspace-operations",
    version: str = "1.0.0",
    namespace: str = "coder",
    event_type: str = "handoff_prepared",
    delivery: str = "eager",
) -> ObservedSkill:
    return ObservedSkill(
        observation=SkillUsageObservation(
            event_id="skillboot-abc",
            event_type=event_type,
            namespace=namespace,
            skill_id=skill_id,
            version=version,
            digest=_CONTRACT_HASH,
            delivery=delivery,
            selection_source="required" if delivery == "eager" else "recommended",
            ref=None if delivery == "eager" else f"{skill_id}@{version}",
        ),
        correlation={"session_ref": "sess-1", "work_role": namespace},
    )


class _FakeRecord:
    def __init__(
        self,
        task_id: str,
        *,
        created_at: str = "2026-09-14T06:00:00+00:00",
        dispatched_at: str = "2026-09-14T06:00:02+00:00",
        updated_at: str = "2026-09-14T06:00:10+00:00",
        outcome: str = "completed",
    ) -> None:
        self.canonical_task_id = task_id
        self.created_at = created_at
        self.dispatched_at = dispatched_at
        self.updated_at = updated_at
        self.terminal_result = type("T", (), {"status": outcome})()
        self.worker_result_card = {"task_ref": task_id}


class TestCardContractFlags:
    def test_non_authority_flags(self) -> None:
        assert CARD_IS_AUTHORITY is False
        assert CARD_SEMANTIC_JUDGMENT is False
        assert AUTOMATIC_ABORT is False

    def test_metric_fact_completeness_truth(self) -> None:
        fact = MetricFact.incomplete("missing_required_observations")
        assert fact.value is None
        assert fact.completeness == "incomplete"
        with pytest.raises(Exception):
            MetricFact(value=0.5, completeness="incomplete", reason=None)
        with pytest.raises(Exception):
            MetricFact(value=None, completeness="complete")


class TestCardProjection:
    def test_deterministic_projection(self) -> None:
        obs = [
            _obs("workspace.search", _hex("d1"), started="2026-09-14T06:00:00+00:00", ended="2026-09-14T06:00:01+00:00"),
            _obs("workspace.read", _hex("d2"), started="2026-09-14T06:00:01+00:00", ended="2026-09-14T06:00:02+00:00"),
            _obs("task.start", _hex("d3"), started="2026-09-14T06:00:02+00:00", ended="2026-09-14T06:00:03+00:00"),
        ]
        skills = [_skill_obs()]
        card_a = build_tool_effectiveness_card(
            project_id="p1", tool_observations=obs, skill_observations=skills, session_ref="sess-1"
        )
        card_b = build_tool_effectiveness_card(
            project_id="p1", tool_observations=obs, skill_observations=skills, session_ref="sess-1"
        )
        assert card_a.canonical_json() == card_b.canonical_json()
        assert card_a.card_digest == card_b.card_digest

    def test_counts_and_histogram(self) -> None:
        obs = [
            _obs("workspace.search", _hex("d1")),
            _obs("workspace.search", _hex("d2")),
            _obs("workspace.read", _hex("d3"), success=False, error_code="NOT_FOUND", outcome_class="failure_provider"),
            _obs("workspace.write", _hex("d4"), success=False, error_code="AUTHORITY_DENIED", outcome_class="failure_authority_denied"),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.tool_observation_count == 4
        assert card.success_count == 2
        assert card.failure_count == 2
        assert dict(card.typed_error_histogram) == {"NOT_FOUND": 1, "AUTHORITY_DENIED": 1}
        by_op = {o.operation_name: o for o in card.tool_operations}
        assert by_op["workspace.search"].count == 2
        assert by_op["workspace.read"].failure_count == 1
        # S6 metric subject/series identity reuse
        assert all(o.aggregation_series_id and len(o.aggregation_series_id) == 64 for o in card.tool_operations)

    def test_rates_and_first_attempt(self) -> None:
        obs = [
            # digest g1: first attempt fails, retry succeeds
            _obs("workspace.search", _hex("g1"), success=False, error_code="UNKNOWN_INPUT"),
            _obs("workspace.search", _hex("g1"), success=True, started="2026-09-14T06:00:05+00:00"),
            # digest g2: first attempt valid
            _obs("workspace.read", _hex("g2"), success=True),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.first_attempt_valid_call_rate.completeness == "complete"
        assert card.first_attempt_valid_call_rate.numerator == 1
        assert card.first_attempt_valid_call_rate.denominator == 2
        assert card.retry_to_success_count.value == 1
        assert card.unknown_input_rate.completeness == "complete"
        assert card.unknown_input_rate.numerator == 1

    def test_missing_digest_makes_rate_incomplete_not_zero(self) -> None:
        obs = [
            ToolUsageObservation(
                observation_id="tuo-x",
                operation_name="workspace.search",
                contract_hash=_CONTRACT_HASH,
                is_success=True,
                outcome_class="success",
                side_effect="read",
            )
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.first_attempt_valid_call_rate.completeness == "incomplete"
        assert card.first_attempt_valid_call_rate.value is None
        assert card.retry_to_success_count.value is None
        assert "request_digest_missing" in card.completeness_flags

    def test_zero_denominator_not_zero(self) -> None:
        empty = build_tool_effectiveness_card(project_id="p1")
        assert empty.unknown_input_rate.completeness == "incomplete"
        assert empty.unknown_input_rate.reason == "zero_denominator"
        assert empty.unknown_input_rate.value is None
        assert empty.invalid_argument_rate.reason == "no_observations"
        single = build_tool_effectiveness_card(
            project_id="p1", tool_observations=[_obs("role.bootstrap", _hex("r1"))]
        )
        assert single.unknown_input_rate.completeness == "complete"
        assert single.unknown_input_rate.value == 0.0

    def test_no_observations_truthful(self) -> None:
        card = build_tool_effectiveness_card(project_id="p1")
        assert card.tool_observation_count == 0
        assert card.first_attempt_valid_call_rate.value is None
        assert card.skills_observed_used.completeness == "incomplete"
        assert "no_tool_observations" in card.completeness_flags

    def test_cross_session_isolation(self) -> None:
        obs = [
            _obs("workspace.search", _hex("s1"), session_ref="sess-A"),
            _obs("workspace.search", _hex("s2"), session_ref="sess-B"),
        ]
        card = build_tool_effectiveness_card(
            project_id="p1", tool_observations=obs, session_ref="sess-A"
        )
        assert card.tool_observation_count == 1
        assert card.excluded_missing_correlation == 1
        assert card.success_count == 1

    def test_worker_parent_session_correlation_included(self) -> None:
        obs = [
            _obs("workspace.search", _hex("s1"), session_ref="sess-A"),
            _obs("task.return", _hex("s2"), session_ref=None, parent_session_ref="sess-A",
                 task_id="aota_forge:M3:W1:abcd:child001"),
        ]
        card = build_tool_effectiveness_card(
            project_id="p1", tool_observations=obs, session_ref="sess-A"
        )
        assert card.tool_observation_count == 2
        assert card.task_return_count == 1

    def test_repeat_request_and_loop_signal(self) -> None:
        obs = [
            _obs("workspace.search", _hex("same"), success=False, error_code="UNKNOWN_INPUT"),
            _obs("workspace.search", _hex("same"), success=False, error_code="UNKNOWN_INPUT",
                 started="2026-09-14T06:00:05+00:00"),
            _obs("workspace.search", _hex("same"), success=False, error_code="UNKNOWN_INPUT",
                 started="2026-09-14T06:00:10+00:00"),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert len(card.repeat_requests) == 1
        repeat = card.repeat_requests[0]
        assert repeat.repeat_count == 3
        assert repeat.state_progression_observed is False
        assert len(card.loop_signals) == 1
        assert card.loop_signals[0].signal == "POSSIBLE_AGENT_LOOP"
        assert card.loop_signals[0].to_dict()["is_diagnosis"] is False

    def test_no_loop_signal_with_progression(self) -> None:
        obs = [
            _obs("workspace.search", _hex("same"), result_digest="1" * 64),
            _obs("workspace.search", _hex("same"), result_digest="2" * 64),
            _obs("workspace.search", _hex("same"), result_digest="3" * 64),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.repeat_requests[0].state_progression_observed is True
        assert not card.loop_signals

    def test_repeated_semantic_delegation_signal(self) -> None:
        obs = [
            _obs("handoff.write", _hex("h1"), task_id="aota_forge:M3:W1:abcd:parent001"),
            _obs("handoff.write", _hex("h1"), task_id="aota_forge:M3:W1:abcd:parent001",
                 started="2026-09-14T06:01:00+00:00"),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.repeated_semantic_delegation_signal.completeness == "complete"
        assert card.repeated_semantic_delegation_signal.value == 1
        assert card.repeated_semantic_delegation_signal.numerator == 1
        # never a judgment label
        assert "watseful" not in card.canonical_json()
        assert "BAD_REVIEW" not in card.canonical_json()

    def test_no_delegation_signal_without_repeat(self) -> None:
        obs = [_obs("handoff.write", _hex("h1"))]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.repeated_semantic_delegation_signal.value == 0

    def test_skill_lifecycle_facts(self) -> None:
        skills = [
            _skill_obs(skill_id="s1", delivery="eager"),
            _skill_obs(skill_id="s2", delivery="progressive"),
            _skill_obs(skill_id="s2", event_type="execution_materialized", delivery="progressive"),
        ]
        card = build_tool_effectiveness_card(project_id="p1", skill_observations=skills)
        assert card.skills_selected == 2
        assert card.skills_delivered_eager == 1
        assert card.skills_loaded_progressive == 1
        assert card.skills_observed_used.completeness == "incomplete"
        assert card.skills_observed_used.reason == "no_skill_usage_evidence"

    def test_worker_lifecycle_facts_and_efficiency(self) -> None:
        obs = [
            _obs("workspace.search", _hex("w1")),
            _obs("workspace.read", _hex("w2")),
            _obs("task.start", _hex("w3")),
            _obs("task.return", _hex("w4"), session_ref=None, parent_session_ref="sess-1",
                 task_id="aota_forge:M3:W1:abcd:child001", started="2026-09-14T06:00:11+00:00"),
        ]
        records = [_FakeRecord("aota_forge:M3:W1:abcd:child001")]
        card = build_tool_effectiveness_card(
            project_id="p1", tool_observations=obs, records=records, session_ref="sess-1"
        )
        assert card.child_task_start_attempts == 1
        assert card.successful_child_dispatches == 1
        assert card.task_start_attempts_per_successful_child.value == 1.0
        assert card.tool_calls_per_completed_work.completeness == "complete"
        assert card.worker_startup_latency_ms.completeness == "complete"
        assert card.worker_startup_latency_ms.value == 2000
        assert card.worker_active_duration_ms.value == 8000
        assert card.worker_timeout_count == 0
        assert card.parent_reentry_count >= 1
        assert card.workers[0].canonical_task_id == "aota_forge:M3:W1:abcd:child001"

    def test_worker_timeout_fact(self) -> None:
        records = [_FakeRecord("child-timeout", outcome="timeout")]
        card = build_tool_effectiveness_card(project_id="p1", records=records)
        assert card.worker_timeout_count == 1

    def test_task_card_latency_unavailable_not_fabricated(self) -> None:
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=[_obs("workspace.read", _hex("x"))])
        assert card.task_return_to_card_latency_ms.completeness == "incomplete"
        assert card.task_return_to_card_latency_ms.reason == "task_card_timestamp_unavailable"

    def test_timings_and_wall_duration(self) -> None:
        obs = [
            _obs("workspace.read", _hex("x"), started="2026-09-14T06:00:00+00:00", ended="2026-09-14T06:00:02+00:00"),
            _obs("workspace.read", _hex("y"), started="2026-09-14T06:00:10+00:00", ended="2026-09-14T06:00:12+00:00"),
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert card.wall_duration_ms.value == 12000
        assert card.time_to_first_valid_dispatch_ms.value == 0
        assert card.started_at_utc == "2026-09-14T06:00:00+00:00"
        assert card.ended_at_utc == "2026-09-14T06:00:12+00:00"

    def test_no_raw_payload_or_prompt(self) -> None:
        obs = [_obs("workspace.search", _hex("q"))]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        payload = card.canonical_json()
        assert "RAW" not in payload
        assert "prompt" not in payload
        assert card.to_dict()["is_authority"] is False

    def test_bounded_card_size(self) -> None:
        obs = [
            _obs(f"op.{i}.x", _hex(f"op-{i}"), started=f"2026-09-14T06:{i:02d}:00+00:00")
            for i in range(40)
        ]
        card = build_tool_effectiveness_card(project_id="p1", tool_observations=obs)
        assert len(card.tool_operations) <= 32
        assert len(card.canonical_json().encode("utf-8")) < 64 * 1024

    def test_s6_envelope_reuse(self) -> None:
        from datetime import datetime, timezone

        obs = [_obs("workspace.read", _hex("e"))]
        skills = [_skill_obs()]
        envelopes = build_run_telemetry_envelopes(
            tool_observations=obs,
            skill_observations=skills,
            ingestion_time=datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc),
            project_id="p1",
            worktree_id="wt1",
        )
        assert len(envelopes) == 2
        kinds = {e.source.source_kind for e in envelopes}
        assert kinds == {"tool_usage_observation", "skill_usage_observation"}
        for e in envelopes:
            assert e.is_authority is False
