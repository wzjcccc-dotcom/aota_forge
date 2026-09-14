"""AF #54 M3/W3 — Run-scoped ToolEffectivenessCard (bounded factual projection).

Thin deterministic projection over the existing S2/S3 observations (and,
where available, bounded Worker lifecycle facts) for one bounded run/session
scope. This is a factual evidence card, not an analytics subsystem and not a
semantic judgment:

    captured run observations (+ bounded lifecycle facts)
        ↓
    deterministic factual counts / rates / signals
        ↓
    bounded ToolEffectivenessCard (JSON-serializable)

Invariants
----------
* CARD_IS_AUTHORITY=no, CARD_SEMANTIC_JUDGMENT=no, CARD_CHOOSES_WORKFLOW=no
* METRIC_THRESHOLD_IS_POLICY=no, POSSIBLE_AGENT_LOOP_IS_DIAGNOSIS=no
* AUTOMATIC_ABORT=no, AUTOMATIC_WORKFLOW_INTERVENTION=no
* MISSING_TELEMETRY_IS_ZERO=no, UNKNOWN_DENOMINATOR_IS_ZERO=no
* EVERY_RATE_CARRIES_COMPLETENESS=yes
* RAW_PROMPT_CAPTURED=no, RAW_TOOL_INPUT_CAPTURED=no,
  RAW_TOOL_OUTPUT_CAPTURED=no, RAW_HERMES_TRANSCRIPT_REQUIRED=no
* CARD_BOUNDED=yes (hard caps on entries and serialized size)
* SECOND_METRIC_TAXONOMY_CREATED=no — operation/skill identities reuse
  existing S6 subjects and the existing observation contracts
* DOES_NOT_PROVE: whether a retry was justified, whether a Skill is good,
  whether the Agent is efficient, whether any policy should change (M4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.runtime_observation import ObservedSkill
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ToolMetricSubject,
    compute_aggregation_series_id,
)
from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation

# ---------------------------------------------------------------------------
# Contract identity / bounds
# ---------------------------------------------------------------------------

CARD_CONTRACT_VERSION: str = "s6-af54-m3-v1"
CARD_BOUNDED: bool = True
CARD_IS_AUTHORITY: bool = False
CARD_SEMANTIC_JUDGMENT: bool = False
CARD_CHOOSES_WORKFLOW: bool = False
METRIC_THRESHOLD_IS_POLICY: bool = False
POSSIBLE_AGENT_LOOP_IS_DIAGNOSIS: bool = False
AUTOMATIC_ABORT: bool = False
AUTOMATIC_WORKFLOW_INTERVENTION: bool = False
MISSING_TELEMETRY_IS_ZERO: bool = False
UNKNOWN_DENOMINATOR_IS_ZERO: bool = False
EVERY_RATE_CARRIES_COMPLETENESS: bool = True
RAW_PROMPT_CAPTURED: bool = False
RAW_TOOL_INPUT_CAPTURED: bool = False
RAW_TOOL_OUTPUT_CAPTURED: bool = False
RAW_HERMES_TRANSCRIPT_REQUIRED: bool = False
SKILL_DELIVERED_IS_OBSERVED_USED: bool = False
SECOND_METRIC_TAXONOMY_CREATED: bool = False

MAX_CARD_OPERATIONS: int = 32
MAX_CARD_ERRORS: int = 32
MAX_CARD_EVIDENCE_REFS: int = 64
MAX_CARD_SIGNALS: int = 32
MAX_CARD_REPEAT_GROUPS: int = 64
MAX_CARD_WORKERS: int = 32
MAX_CARD_SKILLS: int = 64
MAX_CARD_BYTES: int = 64 * 1024

DEFAULT_LOOP_REPEAT_THRESHOLD: int = 3
DEFAULT_DELEGATION_REPEAT_THRESHOLD: int = 2

COMPLETENESS_COMPLETE: str = "complete"
COMPLETENESS_INCOMPLETE: str = "incomplete"

REASON_MISSING_REQUIRED_OBSERVATIONS: str = "missing_required_observations"
REASON_ZERO_DENOMINATOR: str = "zero_denominator"
REASON_NO_OBSERVATIONS: str = "no_observations"
REASON_NO_SKILL_USAGE_EVIDENCE: str = "no_skill_usage_evidence"
REASON_MODEL_IDENTITY_UNAVAILABLE: str = "model_identity_unavailable"
REASON_TASK_CARD_TIMESTAMP_UNAVAILABLE: str = "task_card_timestamp_unavailable"
REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE: str = "worker_lifecycle_facts_unavailable"
REASON_TRUNCATED_OBSERVATION_SINK: str = "truncated_observation_sink"
REASON_INVALID_EVIDENCE_RECORDS: str = "invalid_evidence_records"

_SKILL_ANCHOR_BOOTSTRAP: str = "handoff_prepared"
_SKILL_ANCHOR_PROGRESSIVE_LOAD: str = "execution_materialized"

_INVALID_INPUT_CODES: frozenset[str] = frozenset(
    {
        "INVALID_INPUT",
        "INVALID_ARGUMENT",
        "INVALID_ROLE",
        "INVALID_STATUS",
        "INPUT_TYPE_INVALID",
        "MISSING_REQUIRED_INPUT",
        "UNKNOWN_INPUT",
        "UNKNOWN_REF",
        "UNKNOWN_OPERATION",
    }
)
_ROLE_HANDOFF_MISMATCH_CODES: frozenset[str] = frozenset({"ROLE_HANDOFF_MISMATCH", "WORK_SCOPE_INSUFFICIENT"})


class EffectivenessCardError(ValueError):
    """Typed fail-closed card contract error."""


class EffectivenessCardBoundsError(EffectivenessCardError):
    """Bounded card contract overflow (fail closed, never silent truncation)."""


# ---------------------------------------------------------------------------
# Metric fact — value + explicit completeness truth
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricFact:
    value: float | int | None
    completeness: str
    reason: str | None = None
    numerator: int | None = None
    denominator: int | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if self.completeness not in (COMPLETENESS_COMPLETE, COMPLETENESS_INCOMPLETE):
            raise EffectivenessCardError("completeness must be complete|incomplete")
        if self.completeness == COMPLETENESS_INCOMPLETE:
            if self.value is not None:
                raise EffectivenessCardError("incomplete metric must not carry a numeric value")
            if self.reason is None:
                raise EffectivenessCardError("incomplete metric requires an explicit reason")
        if self.completeness == COMPLETENESS_COMPLETE and self.value is None:
            raise EffectivenessCardError("complete metric requires a value")

    @property
    def is_authority(self) -> bool:
        return False

    @classmethod
    def complete(
        cls,
        value: float | int,
        *,
        numerator: int | None = None,
        denominator: int | None = None,
        unit: str | None = None,
    ) -> "MetricFact":
        return cls(
            value=value,
            completeness=COMPLETENESS_COMPLETE,
            numerator=numerator,
            denominator=denominator,
            unit=unit,
        )

    @classmethod
    def incomplete(cls, reason: str) -> "MetricFact":
        return cls(value=None, completeness=COMPLETENESS_INCOMPLETE, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"completeness": self.completeness}
        if self.value is not None:
            d["value"] = self.value
        if self.reason is not None:
            d["reason"] = self.reason
        if self.numerator is not None:
            d["numerator"] = self.numerator
        if self.denominator is not None:
            d["denominator"] = self.denominator
        if self.unit is not None:
            d["unit"] = self.unit
        return d


# ---------------------------------------------------------------------------
# Bounded lifecycle facts (read-only extraction; no durable store dependency)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkerLifecycleFact:
    """Bounded Worker/child lifecycle facts projected from a durable record."""

    canonical_task_id: str
    role: str | None = None
    milestone_ref: str | None = None
    work_item_ref: str | None = None
    created_at: str | None = None
    dispatched_at: str | None = None
    updated_at: str | None = None
    outcome: str | None = None
    result_card_present: bool | None = None
    task_return_status: str | None = None
    task_return_created_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise EffectivenessCardError("canonical_task_id must be non-empty")
        if len(self.canonical_task_id) > 512:
            raise EffectivenessCardError("canonical_task_id exceeds bound")
        for label in ("role", "milestone_ref", "work_item_ref", "outcome", "task_return_status"):
            value = getattr(self, label)
            if value is not None and (not isinstance(value, str) or len(value) > 256 or "\x00" in value):
                raise EffectivenessCardError(f"{label} must be a bounded string or None")
        for label in ("created_at", "dispatched_at", "updated_at", "task_return_created_at"):
            value = getattr(self, label)
            if value is not None and (not isinstance(value, str) or len(value) > 64):
                raise EffectivenessCardError(f"{label} must be a bounded timestamp string or None")

    @classmethod
    def from_durable_record(cls, record: object) -> "WorkerLifecycleFact":
        """Mechanical bounded extraction from an existing durable record.

        Reads only bounded lifecycle fields; never mutates and never requires
        the durable store type. No timestamps are invented.
        """
        def _s(name: str, max_len: int = 512) -> str | None:
            try:
                raw = getattr(record, name, None)
            except Exception:
                return None
            if isinstance(raw, str) and raw.strip() and len(raw) <= max_len:
                return raw.strip()
            return None

        canonical_task_id = _s("canonical_task_id") or ""
        telemetry_stamp = _s("telemetry_stamp", 256)
        if not canonical_task_id:
            canonical_task_id = "unknown-task"
        terminal = getattr(record, "terminal_result", None)
        outcome = None
        if terminal is not None:
            for attr in ("status", "state"):
                candidate = getattr(terminal, attr, None)
                if isinstance(candidate, str) and candidate.strip():
                    outcome = candidate.strip()[:64]
                    break
            if outcome is None:
                try:
                    outcome = str(getattr(terminal, "outcome_class", ""))[:64] or None
                except Exception:
                    outcome = None
        card_present: bool | None = None
        try:
            card_present = getattr(record, "worker_result_card", None) is not None
        except Exception:
            card_present = None
        return cls(
            canonical_task_id=canonical_task_id,
            role=(_s("admission_scope", 128) or None),
            milestone_ref=(_s("milestone_ref", 128) or None),
            work_item_ref=(_s("work_item_ref", 128) or None),
            created_at=_s("created_at", 64),
            dispatched_at=_s("dispatched_at", 64),
            updated_at=_s("updated_at", 64),
            outcome=outcome,
            result_card_present=card_present,
            task_return_status=(_s("task_return_status", 64)),
            task_return_created_at=_s("task_return_created_at", 64),
        )


# ---------------------------------------------------------------------------
# Signal / repeat value objects (factual only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepeatRequestFact:
    operation_name: str
    request_digest: str
    repeat_count: int
    correlation_ref: str | None = None
    result_digests: tuple[str, ...] = ()
    state_progression_observed: bool | None = None
    intervening_child_task_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "operation_name": self.operation_name,
            "request_digest": self.request_digest,
            "repeat_count": self.repeat_count,
        }
        if self.correlation_ref is not None:
            d["correlation_ref"] = self.correlation_ref
        if self.result_digests:
            d["result_digests"] = list(self.result_digests)
        if self.state_progression_observed is not None:
            d["state_progression_observed"] = self.state_progression_observed
        if self.intervening_child_task_ref is not None:
            d["intervening_child_task_ref"] = self.intervening_child_task_ref
        return d


@dataclass(frozen=True)
class LoopSignal:
    signal: str
    operation_name: str
    request_digest: str
    repeat_count: int
    correlation_ref: str | None = None
    state_progression_observed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "signal": self.signal,
            "operation_name": self.operation_name,
            "request_digest": self.request_digest,
            "repeat_count": self.repeat_count,
            "is_diagnosis": False,
        }
        if self.correlation_ref is not None:
            d["correlation_ref"] = self.correlation_ref
        if self.state_progression_observed is not None:
            d["state_progression_observed"] = self.state_progression_observed
        return d


@dataclass(frozen=True)
class ToolOperationCount:
    operation_name: str
    count: int
    success_count: int
    failure_count: int
    aggregation_series_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "operation_name": self.operation_name,
            "count": self.count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
        if self.aggregation_series_id is not None:
            d["aggregation_series_id"] = self.aggregation_series_id
        return d


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolEffectivenessCard:
    card_version: str = CARD_CONTRACT_VERSION
    project_id: str = ""
    run_ref: str | None = None
    session_ref: str | None = None
    parent_session_ref: str | None = None
    role: str | None = None
    milestone_ref: str | None = None
    work_item_ref: str | None = None
    model: str | None = None
    provider: str | None = None

    started_at_utc: str | None = None
    ended_at_utc: str | None = None
    wall_duration_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )

    tool_observation_count: int = 0
    skill_observation_count: int = 0
    excluded_missing_correlation: int = 0
    observation_sink_truncated: bool = False
    observation_sink_failures: int = 0
    invalid_evidence_records: int = 0

    tool_operations: tuple[ToolOperationCount, ...] = ()
    success_count: int = 0
    failure_count: int = 0
    typed_error_histogram: tuple[tuple[str, int], ...] = ()

    first_attempt_valid_call_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )
    invalid_argument_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_NO_OBSERVATIONS)
    )
    unknown_input_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )
    role_handoff_mismatch_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )
    authority_denied_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )
    timeout_rate: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )
    retry_to_success_count: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )
    task_start_attempts_per_successful_child: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )

    tool_calls_per_completed_work: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    )
    search_calls_per_work: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    )
    read_calls_per_work: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    )
    exact_duplicate_search_count: int | None = None
    repeated_file_read_count: int | None = None
    time_to_first_valid_dispatch_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )
    worker_startup_latency_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    )
    worker_active_duration_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    )
    task_return_to_card_latency_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_TASK_CARD_TIMESTAMP_UNAVAILABLE)
    )
    card_to_parent_reentry_latency_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )
    total_wall_time_ms: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    )

    child_task_start_attempts: int = 0
    successful_child_dispatches: int = 0
    worker_timeout_count: int | None = None
    task_return_count: int = 0
    parent_reentry_count: int = 0
    workers: tuple[WorkerLifecycleFact, ...] = ()

    skills_selected: int | None = None
    skills_delivered_eager: int | None = None
    skills_loaded_progressive: int | None = None
    skills_observed_used: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_NO_SKILL_USAGE_EVIDENCE)
    )

    repeat_requests: tuple[RepeatRequestFact, ...] = ()
    loop_signals: tuple[LoopSignal, ...] = ()
    repeated_semantic_delegation_signal: MetricFact = field(
        default_factory=lambda: MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    )

    evidence_refs: tuple[str, ...] = ()
    completeness_flags: tuple[str, ...] = ()

    @property
    def is_authority(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "card_version": self.card_version,
            "project_id": self.project_id,
            "tool_observation_count": self.tool_observation_count,
            "skill_observation_count": self.skill_observation_count,
            "excluded_missing_correlation": self.excluded_missing_correlation,
            "observation_sink_truncated": self.observation_sink_truncated,
            "observation_sink_failures": self.observation_sink_failures,
            "invalid_evidence_records": self.invalid_evidence_records,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "tool_operations": [o.to_dict() for o in self.tool_operations],
            "typed_error_histogram": [list(item) for item in self.typed_error_histogram],
            "first_attempt_valid_call_rate": self.first_attempt_valid_call_rate.to_dict(),
            "invalid_argument_rate": self.invalid_argument_rate.to_dict(),
            "unknown_input_rate": self.unknown_input_rate.to_dict(),
            "role_handoff_mismatch_rate": self.role_handoff_mismatch_rate.to_dict(),
            "authority_denied_rate": self.authority_denied_rate.to_dict(),
            "timeout_rate": self.timeout_rate.to_dict(),
            "retry_to_success_count": self.retry_to_success_count.to_dict(),
            "task_start_attempts_per_successful_child": self.task_start_attempts_per_successful_child.to_dict(),
            "tool_calls_per_completed_work": self.tool_calls_per_completed_work.to_dict(),
            "search_calls_per_work": self.search_calls_per_work.to_dict(),
            "read_calls_per_work": self.read_calls_per_work.to_dict(),
            "time_to_first_valid_dispatch_ms": self.time_to_first_valid_dispatch_ms.to_dict(),
            "worker_startup_latency_ms": self.worker_startup_latency_ms.to_dict(),
            "worker_active_duration_ms": self.worker_active_duration_ms.to_dict(),
            "task_return_to_card_latency_ms": self.task_return_to_card_latency_ms.to_dict(),
            "card_to_parent_reentry_latency_ms": self.card_to_parent_reentry_latency_ms.to_dict(),
            "total_wall_time_ms": self.total_wall_time_ms.to_dict(),
            "wall_duration_ms": self.wall_duration_ms.to_dict(),
            "child_task_start_attempts": self.child_task_start_attempts,
            "successful_child_dispatches": self.successful_child_dispatches,
            "task_return_count": self.task_return_count,
            "parent_reentry_count": self.parent_reentry_count,
            "skills_selected": self.skills_selected,
            "skills_delivered_eager": self.skills_delivered_eager,
            "skills_loaded_progressive": self.skills_loaded_progressive,
            "skills_observed_used": self.skills_observed_used.to_dict(),
            "repeat_requests": [r.to_dict() for r in self.repeat_requests],
            "loop_signals": [s.to_dict() for s in self.loop_signals],
            "repeated_semantic_delegation_signal": self.repeated_semantic_delegation_signal.to_dict(),
            "workers": [
                {
                    "canonical_task_id": w.canonical_task_id,
                    **({"role": w.role} if w.role else {}),
                    **({"milestone_ref": w.milestone_ref} if w.milestone_ref else {}),
                    **({"work_item_ref": w.work_item_ref} if w.work_item_ref else {}),
                    **({"created_at": w.created_at} if w.created_at else {}),
                    **({"dispatched_at": w.dispatched_at} if w.dispatched_at else {}),
                    **({"updated_at": w.updated_at} if w.updated_at else {}),
                    **({"outcome": w.outcome} if w.outcome else {}),
                    **(
                        {"result_card_present": w.result_card_present}
                        if w.result_card_present is not None
                        else {}
                    ),
                }
                for w in self.workers
            ],
            "evidence_refs": list(self.evidence_refs),
            "completeness_flags": list(self.completeness_flags),
            "is_authority": False,
            "semantic_judgment": False,
        }
        for key in ("run_ref", "session_ref", "parent_session_ref", "role", "milestone_ref", "work_item_ref"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        if self.model is not None:
            d["model"] = self.model
        if self.provider is not None:
            d["provider"] = self.provider
        if self.started_at_utc is not None:
            d["started_at_utc"] = self.started_at_utc
        if self.ended_at_utc is not None:
            d["ended_at_utc"] = self.ended_at_utc
        if self.exact_duplicate_search_count is not None:
            d["exact_duplicate_search_count"] = self.exact_duplicate_search_count
        if self.repeated_file_read_count is not None:
            d["repeated_file_read_count"] = self.repeated_file_read_count
        if self.worker_timeout_count is not None:
            d["worker_timeout_count"] = self.worker_timeout_count
        return d

    def canonical_json(self) -> str:
        text = canonical_json(self.to_dict())
        if len(text.encode("utf-8")) > MAX_CARD_BYTES:
            raise EffectivenessCardBoundsError("card exceeds bounded serialized size")
        return text

    def compute_card_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def card_digest(self) -> str:
        return self.compute_card_digest()


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _parse_utc(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except Exception:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _ms_between(start: str | None, end: str | None) -> int | None:
    a = _parse_utc(start)
    b = _parse_utc(end)
    if a is None or b is None or b < a:
        return None
    return int(round((b - a).total_seconds() * 1000))


def _rate(numerator: int, denominator: int) -> MetricFact:
    if denominator <= 0:
        return MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    return MetricFact.complete(
        round(numerator / denominator, 6),
        numerator=numerator,
        denominator=denominator,
    )


def _safe_series_id(operation_name: str) -> str | None:
    try:
        return compute_aggregation_series_id(
            MetricFamily.TOOL, ToolMetricSubject(operation_name=operation_name)
        )
    except Exception:
        return None


def _skill_identity(namespace: str, skill_id: str, version: str) -> str:
    return f"{namespace}:{skill_id}@{version}"


def _observation_timestamp(obs: ToolUsageObservation) -> str | None:
    return obs.started_at_utc or obs.ended_at_utc


@dataclass(frozen=True)
class _ObservationWindow:
    observations: tuple[ToolUsageObservation, ...]
    excluded_missing_correlation: int


def _filter_observations(
    observations: Sequence[ToolUsageObservation],
    *,
    session_ref: str | None,
    run_ref: str | None,
    milestone_ref: str | None,
    work_item_ref: str | None,
) -> _ObservationWindow:
    selected: list[ToolUsageObservation] = []
    excluded = 0
    for obs in observations:
        if not isinstance(obs, ToolUsageObservation):
            raise EffectivenessCardError("observations must be ToolUsageObservation instances")
        if session_ref is not None and obs.session_ref != session_ref:
            if obs.parent_session_ref == session_ref:
                pass  # worker-side observation of the same run session
            else:
                excluded += 1
                continue
        if milestone_ref is not None and obs.milestone_ref is not None and obs.milestone_ref != milestone_ref:
            excluded += 1
            continue
        if work_item_ref is not None and obs.work_item_ref is not None and obs.work_item_ref != work_item_ref:
            excluded += 1
            continue
        selected.append(obs)
    return _ObservationWindow(observations=tuple(selected), excluded_missing_correlation=excluded)


def build_tool_effectiveness_card(
    *,
    project_id: str,
    tool_observations: Sequence[ToolUsageObservation] = (),
    skill_observations: Sequence[ObservedSkill] = (),
    records: Sequence[Any] = (),
    session_ref: str | None = None,
    run_ref: str | None = None,
    role: str | None = None,
    milestone_ref: str | None = None,
    work_item_ref: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    worktree_id: str | None = None,
    ingestion_time: datetime | None = None,
    loop_repeat_threshold: int = DEFAULT_LOOP_REPEAT_THRESHOLD,
    delegation_repeat_threshold: int = DEFAULT_DELEGATION_REPEAT_THRESHOLD,
) -> ToolEffectivenessCard:
    """Deterministically project one bounded run-scoped effectiveness card.

    No semantic judgment, no scoring, no policy: factual counts, rates with
    explicit completeness/denominator truth, and deterministic repeat signals.
    """
    if not isinstance(project_id, str) or not project_id.strip():
        raise EffectivenessCardError("project_id must be a non-empty string")
    if type(loop_repeat_threshold) is not int or loop_repeat_threshold < 2:
        raise EffectivenessCardError("loop_repeat_threshold must be an int >= 2")
    if type(delegation_repeat_threshold) is not int or delegation_repeat_threshold < 2:
        raise EffectivenessCardError("delegation_repeat_threshold must be an int >= 2")

    window = _filter_observations(
        tool_observations,
        session_ref=session_ref,
        run_ref=run_ref,
        milestone_ref=milestone_ref,
        work_item_ref=work_item_ref,
    )
    obs = window.observations

    # ---- operation histogram (reuses S6 tool subject/series identity) ----
    op_counts: dict[str, list[int]] = {}
    error_counts: dict[str, int] = {}
    success_total = 0
    failure_total = 0
    for o in obs:
        counts = op_counts.setdefault(o.operation_name, [0, 0, 0])
        counts[0] += 1
        if o.is_success:
            counts[1] += 1
            success_total += 1
        else:
            counts[2] += 1
            failure_total += 1
            if o.error_code:
                error_counts[o.error_code] = error_counts.get(o.error_code, 0) + 1
    tool_operations = tuple(
        ToolOperationCount(
            operation_name=name,
            count=counts[0],
            success_count=counts[1],
            failure_count=counts[2],
            aggregation_series_id=_safe_series_id(name),
        )
        for name, counts in sorted(op_counts.items())[:MAX_CARD_OPERATIONS]
    )
    typed_error_histogram = tuple(sorted(error_counts.items())[:MAX_CARD_ERRORS])

    total_calls = len(obs)

    # ---- request identity grouping (deterministic capture order) ----
    groups: dict[tuple[str, str], list[ToolUsageObservation]] = {}
    digest_missing = 0
    for o in obs:
        if o.normalized_request_digest is None:
            digest_missing += 1
            continue
        groups.setdefault((o.operation_name, o.normalized_request_digest), []).append(o)

    first_attempt_groups = list(groups.values())
    first_valid = sum(
        1
        for group in first_attempt_groups
        if group and (group[0].is_success or group[0].outcome_class == "success_domain_failure")
    )

    if digest_missing > 0:
        first_attempt_fact = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
        retry_success_fact = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    elif not groups:
        first_attempt_fact = MetricFact.incomplete(REASON_NO_OBSERVATIONS)
        retry_success_fact = MetricFact.incomplete(REASON_NO_OBSERVATIONS)
    else:
        first_attempt_fact = MetricFact.complete(
            round(first_valid / len(groups), 6),
            numerator=first_valid,
            denominator=len(groups),
        )
        retries = 0
        for group in groups.values():
            seen_failure = False
            for o in group:
                if not o.is_success:
                    seen_failure = True
                elif seen_failure:
                    retries += 1
                    break
        retry_success_fact = MetricFact.complete(retries, unit="count")

    # ---- deterministic repeat / loop / delegation signals ----
    repeat_requests: list[RepeatRequestFact] = []
    loop_signals: list[LoopSignal] = []
    delegation_key = ("handoff.write", "work_item")
    delegation_repeats = 0
    delegation_children: list[str] = []
    for (operation, digest), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        result_digests = tuple(dict.fromkeys(o.result_digest for o in group if o.result_digest))
        progression: bool | None
        if not result_digests:
            progression = None
        else:
            progression = len(result_digests) > 1
        correlation_ref = next(
            (o.canonical_task_id for o in group if o.canonical_task_id), None
        )
        entered_child = None
        for i in range(1, len(group)):
            later_ref = group[i].canonical_task_id
            earlier_ref = group[i - 1].canonical_task_id
            if later_ref and earlier_ref and later_ref != earlier_ref:
                entered_child = later_ref
                break
        if len(repeat_requests) < MAX_CARD_REPEAT_GROUPS:
            repeat_requests.append(
                RepeatRequestFact(
                    operation_name=operation,
                    request_digest=digest,
                    repeat_count=len(group),
                    correlation_ref=correlation_ref,
                    result_digests=result_digests[:4],
                    state_progression_observed=progression,
                    intervening_child_task_ref=entered_child,
                )
            )
        if (
            len(group) >= loop_repeat_threshold
            and operation not in ("task.start", "task.return")
            and progression is False
            and len(loop_signals) < MAX_CARD_SIGNALS
        ):
            loop_signals.append(
                LoopSignal(
                    signal="POSSIBLE_AGENT_LOOP",
                    operation_name=operation,
                    request_digest=digest,
                    repeat_count=len(group),
                    correlation_ref=correlation_ref,
                    state_progression_observed=False,
                )
            )
        if operation == delegation_key[0] and len(group) >= delegation_repeat_threshold:
            delegation_repeats += 1
            for o in group:
                if o.canonical_task_id:
                    delegation_children.append(o.canonical_task_id)

    # ---- rates ----
    if total_calls == 0:
        invalid_argument_rate = MetricFact.incomplete(REASON_NO_OBSERVATIONS)
        unknown_input_rate = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
        role_mismatch_rate = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
        authority_denied_rate = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
        timeout_rate = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    else:
        invalid_count = sum(1 for o in obs if o.outcome_class == "failure_invalid_input")
        unknown_input_count = sum(
            1 for o in obs if (o.error_code or "").upper() in _INVALID_INPUT_CODES
        )
        role_mismatch_count = sum(
            1 for o in obs if (o.error_code or "").upper() in _ROLE_HANDOFF_MISMATCH_CODES
        )
        authority_count = sum(1 for o in obs if o.outcome_class == "failure_authority_denied")
        timeout_count = sum(1 for o in obs if o.outcome_class == "failure_timeout")
        invalid_argument_rate = _rate(invalid_count, total_calls)
        unknown_input_rate = _rate(unknown_input_count, total_calls)
        role_mismatch_rate = _rate(role_mismatch_count, total_calls)
        authority_denied_rate = _rate(authority_count, total_calls)
        timeout_rate = _rate(timeout_count, total_calls)

    # ---- child task facts ----
    child_start_attempts = sum(1 for o in obs if o.operation_name == "task.start")
    child_start_successes = sum(1 for o in obs if o.operation_name == "task.start" and o.is_success)
    worker_facts = tuple(
        WorkerLifecycleFact.from_durable_record(r) for r in list(records)[:MAX_CARD_WORKERS]
    )
    dispatched_workers = tuple(w for w in worker_facts if w.dispatched_at)
    if worker_facts:
        successful_children = len(dispatched_workers)
        if successful_children > 0 and child_start_attempts > 0:
            task_start_fact = MetricFact.complete(
                round(child_start_attempts / successful_children, 6),
                numerator=child_start_attempts,
                denominator=successful_children,
            )
        elif successful_children > 0:
            task_start_fact = MetricFact.complete(0, numerator=0, denominator=successful_children)
        else:
            task_start_fact = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    elif child_start_successes > 0:
        task_start_fact = MetricFact.complete(
            round(child_start_attempts / child_start_successes, 6),
            numerator=child_start_attempts,
            denominator=child_start_successes,
        )
    else:
        task_start_fact = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)

    completed_work = sum(1 for w in worker_facts if (w.outcome or "").lower() in ("completed", "complete", "success"))
    if not worker_facts:
        per_work_reason = REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE
        tool_calls_per_work = MetricFact.incomplete(per_work_reason)
        search_per_work = MetricFact.incomplete(per_work_reason)
        read_per_work = MetricFact.incomplete(per_work_reason)
    elif completed_work <= 0:
        tool_calls_per_work = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
        search_per_work = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
        read_per_work = MetricFact.incomplete(REASON_ZERO_DENOMINATOR)
    else:
        search_calls = sum(1 for o in obs if o.operation_name == "workspace.search")
        read_calls = sum(1 for o in obs if o.operation_name == "workspace.read")
        tool_calls_per_work = MetricFact.complete(
            round(total_calls / completed_work, 6), numerator=total_calls, denominator=completed_work
        )
        search_per_work = MetricFact.complete(
            round(search_calls / completed_work, 6), numerator=search_calls, denominator=completed_work
        )
        read_per_work = MetricFact.complete(
            round(read_calls / completed_work, 6), numerator=read_calls, denominator=completed_work
        )

    # ---- duplicates ----
    search_dupes = sum(
        len(group) - 1 for (operation, _), group in groups.items() if operation == "workspace.search"
    )
    read_dupes = sum(
        len(group) - 1 for (operation, _), group in groups.items() if operation == "workspace.read"
    )
    duplicates_valid = digest_missing == 0
    exact_duplicate_search_count = search_dupes if duplicates_valid else None
    repeated_file_read_count = read_dupes if duplicates_valid else None

    # ---- timings ----
    started_values = sorted(
        [ts for ts in (_observation_timestamp(o) for o in obs) if ts is not None]
    )
    ended_values = sorted(
        [ts for ts in (o.ended_at_utc or o.started_at_utc for o in obs) if ts is not None]
    )
    started_at = started_values[0] if started_values else None
    ended_at = ended_values[-1] if ended_values else None
    total_wall = _ms_between(started_at, ended_at)
    if total_wall is None:
        total_wall_fact = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
        wall_duration_fact = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    else:
        total_wall_fact = MetricFact.complete(total_wall, unit="ms")
        wall_duration_fact = MetricFact.complete(total_wall, unit="ms")

    first_valid_ts = None
    for o in obs:
        if (o.is_success or o.outcome_class == "success_domain_failure") and _observation_timestamp(o):
            first_valid_ts = _observation_timestamp(o)
            break
    if started_at and first_valid_ts and obs and _observation_timestamp(obs[0]) is not None:
        delta = _ms_between(started_at, first_valid_ts)
        time_to_first_valid = (
            MetricFact.complete(delta, unit="ms")
            if delta is not None
            else MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
        )
    else:
        time_to_first_valid = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)

    startup_values = [
        _ms_between(w.created_at, w.dispatched_at) for w in dispatched_workers
    ]
    startup_values = [v for v in startup_values if v is not None]
    if startup_values:
        worker_startup = MetricFact.complete(
            sum(startup_values) // len(startup_values),
            numerator=sum(startup_values),
            denominator=len(startup_values),
            unit="ms",
        )
    else:
        worker_startup = MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)

    active_values = [
        _ms_between(w.dispatched_at, w.updated_at) for w in dispatched_workers if w.updated_at
    ]
    active_values = [v for v in active_values if v is not None]
    if active_values:
        worker_active = MetricFact.complete(
            sum(active_values) // len(active_values),
            numerator=sum(active_values),
            denominator=len(active_values),
            unit="ms",
        )
    else:
        worker_active = MetricFact.incomplete(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)

    # Card-to-parent-reentry: bounded estimate — first captured Tool
    # observation after the last Worker terminal update (no fabricated exact
    # model-wait classification).
    reentry_latency: MetricFact
    last_worker_end = max(
        (w.updated_at for w in worker_facts if w.updated_at), default=None
    )
    if last_worker_end and ended_at:
        delta = _ms_between(last_worker_end, ended_at)
        if delta is not None and delta >= 0:
            reentry_latency = MetricFact.complete(delta, unit="ms")
        else:
            reentry_latency = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    else:
        reentry_latency = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)

    worker_timeouts = sum(
        1 for w in worker_facts if (w.outcome or "").lower() in ("timeout", "timed_out")
    )
    task_return_count = sum(1 for o in obs if o.operation_name == "task.return")
    parent_reentry_count = 0
    if worker_facts:
        terminal_ts = max((w.updated_at for w in worker_facts if w.updated_at), default=None)
        if terminal_ts:
            terminal_dt = _parse_utc(terminal_ts)
            for o in obs:
                ts = _observation_timestamp(o)
                dt = _parse_utc(ts)
                if dt is not None and terminal_dt is not None and dt >= terminal_dt:
                    parent_reentry_count += 1

    # ---- skills ----
    selected: set[str] = set()
    eager: set[str] = set()
    progressive_loaded: set[str] = set()
    for observed in skill_observations:
        if not isinstance(observed, ObservedSkill):
            raise EffectivenessCardError("skill_observations must be ObservedSkill instances")
        s = observed.observation
        identity = _skill_identity(s.namespace, s.skill_id, s.version)
        if s.event_type == _SKILL_ANCHOR_BOOTSTRAP:
            selected.add(identity)
            if s.delivery == "eager":
                eager.add(identity)
        elif s.event_type == _SKILL_ANCHOR_PROGRESSIVE_LOAD:
            progressive_loaded.add(identity)
    if len(selected) > MAX_CARD_SKILLS or len(progressive_loaded) > MAX_CARD_SKILLS:
        raise EffectivenessCardBoundsError("skill identity count exceeds card bound")

    # ---- evidence refs ----
    refs: list[str] = []
    for o in obs:
        if o.result_ref and o.result_ref not in refs:
            refs.append(o.result_ref)
        if len(refs) >= MAX_CARD_EVIDENCE_REFS:
            break
    evidence_refs = tuple(refs[:MAX_CARD_EVIDENCE_REFS])

    # ---- completeness flags ----
    flags: list[str] = []
    if digest_missing > 0:
        flags.append("request_digest_missing")
    if not obs:
        flags.append("no_tool_observations")
    if any(_observation_timestamp(o) is None for o in obs):
        flags.append("observation_timestamps_missing")
    if model is None or provider is None:
        flags.append(REASON_MODEL_IDENTITY_UNAVAILABLE)
    if not worker_facts:
        flags.append(REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE)
    if not skill_observations:
        flags.append(REASON_NO_SKILL_USAGE_EVIDENCE)

    # delegation signal fact
    if delegation_repeats > 0 and delegation_children:
        delegation_fact = MetricFact.complete(
            1,
            numerator=delegation_repeats,
            denominator=max(len(set(delegation_children)), 1),
        )
    elif digest_missing > 0:
        delegation_fact = MetricFact.incomplete(REASON_MISSING_REQUIRED_OBSERVATIONS)
    elif not obs:
        delegation_fact = MetricFact.incomplete(REASON_NO_OBSERVATIONS)
    else:
        delegation_fact = MetricFact.complete(0, numerator=0, denominator=1)

    return ToolEffectivenessCard(
        project_id=project_id,
        run_ref=run_ref,
        session_ref=session_ref,
        parent_session_ref=None,
        role=role,
        milestone_ref=milestone_ref,
        work_item_ref=work_item_ref,
        model=model,
        provider=provider,
        started_at_utc=started_at,
        ended_at_utc=ended_at,
        wall_duration_ms=wall_duration_fact,
        tool_observation_count=total_calls,
        skill_observation_count=len(skill_observations),
        excluded_missing_correlation=window.excluded_missing_correlation,
        observation_sink_truncated=False,
        observation_sink_failures=0,
        invalid_evidence_records=0,
        tool_operations=tool_operations,
        success_count=success_total,
        failure_count=failure_total,
        typed_error_histogram=typed_error_histogram,
        first_attempt_valid_call_rate=first_attempt_fact,
        invalid_argument_rate=invalid_argument_rate,
        unknown_input_rate=unknown_input_rate,
        role_handoff_mismatch_rate=role_mismatch_rate,
        authority_denied_rate=authority_denied_rate,
        timeout_rate=timeout_rate,
        retry_to_success_count=retry_success_fact,
        task_start_attempts_per_successful_child=task_start_fact,
        tool_calls_per_completed_work=tool_calls_per_work,
        search_calls_per_work=search_per_work,
        read_calls_per_work=read_per_work,
        exact_duplicate_search_count=exact_duplicate_search_count,
        repeated_file_read_count=repeated_file_read_count,
        time_to_first_valid_dispatch_ms=time_to_first_valid,
        worker_startup_latency_ms=worker_startup,
        worker_active_duration_ms=worker_active,
        task_return_to_card_latency_ms=MetricFact.incomplete(REASON_TASK_CARD_TIMESTAMP_UNAVAILABLE),
        card_to_parent_reentry_latency_ms=reentry_latency,
        total_wall_time_ms=total_wall_fact,
        child_task_start_attempts=child_start_attempts,
        successful_child_dispatches=len(dispatched_workers) if worker_facts else child_start_successes,
        worker_timeout_count=worker_timeouts if worker_facts else None,
        task_return_count=task_return_count,
        parent_reentry_count=parent_reentry_count,
        workers=worker_facts,
        skills_selected=len(selected) if skill_observations else None,
        skills_delivered_eager=len(eager) if skill_observations else None,
        skills_loaded_progressive=len(progressive_loaded) if skill_observations else None,
        skills_observed_used=MetricFact.incomplete(REASON_NO_SKILL_USAGE_EVIDENCE),
        repeat_requests=tuple(repeat_requests),
        loop_signals=tuple(loop_signals),
        repeated_semantic_delegation_signal=delegation_fact,
        evidence_refs=evidence_refs,
        completeness_flags=tuple(sorted(set(flags))),
    )


# ---------------------------------------------------------------------------
# Existing S6 evidence/adapters reuse (production observations → envelopes)
# ---------------------------------------------------------------------------

def build_run_telemetry_envelopes(
    *,
    tool_observations: Sequence[ToolUsageObservation] = (),
    skill_observations: Sequence[ObservedSkill] = (),
    ingestion_time: datetime,
    project_id: str,
    worktree_id: str | None = None,
) -> tuple[Any, ...]:
    """Adapt production observations into the existing S6 evidence envelopes.

    Pure reuse of ``telemetry_adapters`` (no second telemetry system): the
    card consumes factual observations; this helper exists so callers can
    persist/correlate the same facts through the accepted S6 seam.
    """
    from aota_forge.work_plane.telemetry_adapters import (
        adapt_skill_usage_observation,
        adapt_tool_usage_observation,
    )

    envelopes: list[Any] = []
    for o in tool_observations:
        envelopes.append(
            adapt_tool_usage_observation(
                o, ingestion_time, project_id=project_id, worktree_id=worktree_id
            )
        )
    for observed in skill_observations:
        envelopes.append(
            adapt_skill_usage_observation(
                observed.observation, ingestion_time, project_id=project_id, worktree_id=worktree_id
            )
        )
    return tuple(envelopes)


__all__ = [
    "CARD_CONTRACT_VERSION",
    "CARD_BOUNDED",
    "CARD_IS_AUTHORITY",
    "CARD_SEMANTIC_JUDGMENT",
    "CARD_CHOOSES_WORKFLOW",
    "METRIC_THRESHOLD_IS_POLICY",
    "POSSIBLE_AGENT_LOOP_IS_DIAGNOSIS",
    "AUTOMATIC_ABORT",
    "AUTOMATIC_WORKFLOW_INTERVENTION",
    "MISSING_TELEMETRY_IS_ZERO",
    "UNKNOWN_DENOMINATOR_IS_ZERO",
    "EVERY_RATE_CARRIES_COMPLETENESS",
    "RAW_PROMPT_CAPTURED",
    "RAW_TOOL_INPUT_CAPTURED",
    "RAW_TOOL_OUTPUT_CAPTURED",
    "RAW_HERMES_TRANSCRIPT_REQUIRED",
    "SKILL_DELIVERED_IS_OBSERVED_USED",
    "SECOND_METRIC_TAXONOMY_CREATED",
    "MAX_CARD_OPERATIONS",
    "MAX_CARD_ERRORS",
    "MAX_CARD_EVIDENCE_REFS",
    "MAX_CARD_SIGNALS",
    "MAX_CARD_REPEAT_GROUPS",
    "MAX_CARD_WORKERS",
    "MAX_CARD_SKILLS",
    "MAX_CARD_BYTES",
    "DEFAULT_LOOP_REPEAT_THRESHOLD",
    "DEFAULT_DELEGATION_REPEAT_THRESHOLD",
    "COMPLETENESS_COMPLETE",
    "COMPLETENESS_INCOMPLETE",
    "REASON_MISSING_REQUIRED_OBSERVATIONS",
    "REASON_ZERO_DENOMINATOR",
    "REASON_NO_OBSERVATIONS",
    "REASON_NO_SKILL_USAGE_EVIDENCE",
    "REASON_MODEL_IDENTITY_UNAVAILABLE",
    "REASON_TASK_CARD_TIMESTAMP_UNAVAILABLE",
    "REASON_WORKER_LIFECYCLE_FACTS_UNAVAILABLE",
    "REASON_TRUNCATED_OBSERVATION_SINK",
    "REASON_INVALID_EVIDENCE_RECORDS",
    "EffectivenessCardError",
    "EffectivenessCardBoundsError",
    "MetricFact",
    "WorkerLifecycleFact",
    "RepeatRequestFact",
    "LoopSignal",
    "ToolOperationCount",
    "ToolEffectivenessCard",
    "build_tool_effectiveness_card",
    "build_run_telemetry_envelopes",
]
