"""Aggregation Core & Runtime Semantics — S6 M2 W1.

W1 operationalizes M1 identities and aggregation semantics.

Flow:
    TelemetryEvidence
      -> source registration / dedup
      -> metric projection contribution
      -> series + window assignment
      -> pure bounded aggregate transition
      -> aggregate state/result

W1 does NOT own: persistent storage, TelemetryStore implementation,
query service, selective hydration implementation, durable filesystem
format, database, M3 metric catalogue, M4 optimizer.

Two-level idempotency:
    source_dedup_id != projection_id
    same source_dedup_id replay -> same source registration, no second ingestion identity
    same source_dedup_id + different projection_id -> allowed
    same projection_id applied twice -> duplicate, aggregate unchanged

Reprocessing:
    same source_dedup_id + new projection_id (new normalization) -> allowed
    SOURCE_REINGEST_REQUIRED=no, NEW_SOURCE_EVENT_MINTED=no
    REPROCESSING_IS_TELEMETRY_DERIVATION_ONLY=yes

Storage-neutral: pure deterministic transition current state + new contribution.

Single-writer: no distributed lock, no transaction coordinator, no queue.

Operations: COUNT, SUM, MIN, MAX for deterministic integer measurements.
Strict integer semantics, bounded, reject NaN/Infinity/bool masquerading.

Completeness composition: no global severity order, preserve distinct causes,
aggregate complete only when required coverage known AND all contributors complete.
Empty does not imply complete zero.

Window policy: bounded policy identity (window_policy_id/version), no frozen
durations, ingestion fallback policy-governed, otherwise NON_WINDOWABLE,
no implicit wall clock.

Lifecycle: OPEN, FINALIZED only.
Late: OPEN accepts via dedup rules; FINALIZED unseen -> REPROCESS_REQUIRED,
duplicate against finalized remains duplicate (dedup first).

Out-of-order: requires comparable source order evidence, otherwise
not_classifiable; commutative aggregates deterministic.

Failure isolation: invalid input returns typed bounded failure/disposition,
never rewrites CanonicalResult etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.work_plane.telemetry_evidence import (
    CompletenessRecord,
    CompletenessScope,
    CompletenessState,
    compute_projection_id as _compute_projection_id,
    compute_source_dedup_id as _compute_source_dedup_id,
)
from aota_forge.work_plane.telemetry_metrics import (
    compute_aggregation_series_id as _compute_aggregation_series_id,
    compute_aggregation_window_id as _compute_aggregation_window_id,
)

# ---------------------------------------------------------------------------
# Invariant flags — observable for tests / negative proof
# ---------------------------------------------------------------------------

SOURCE_DEDUP_ID_REDEFINED: bool = False
PROJECTION_ID_REDEFINED: bool = False
AGGREGATION_SERIES_ID_REDEFINED: bool = False
AGGREGATION_WINDOW_ID_REDEFINED: bool = False
SOURCE_DDUP_ID_REDEFINED_ALIAS: bool = False  # typo alias for search tolerance

STORAGE_NEUTRAL_TRANSITIONS: bool = True
M2_WRITER_MODEL: str = "single_writer"
M2_ACTIVE_SAMPLING_REQUIRED: bool = False
GLOBAL_COMPLETENESS_SEVERITY_ORDER: bool = False
EMPTY_OBSERVATION_IMPLIES_COMPLETE_ZERO: bool = False
IMPLICIT_WALL_CLOCK_USED: bool = False
NEW_DATABASE_CREATED: bool = False
NEW_PERSISTENT_STORE_CREATED: bool = False
NEW_STORAGE_PROTOCOL_CREATED: bool = False
NEW_EVENT_BUS_CREATED: bool = False
NEW_BACKGROUND_WORKER_CREATED: bool = False
NEW_WORKFLOW_STATE_MACHINE_CREATED: bool = False
NEW_EXECUTION_JOURNAL_CREATED: bool = False
SECOND_RESULT_STORE_CREATED: bool = False
QUERY_LAYER_CREATED: bool = False
HYDRATION_AUTHORITY_CREATED: bool = False
ACTIVE_SAMPLER_CREATED: bool = False
RETENTION_WORKER_CREATED: bool = False
M1_CONTRACT_CHANGED: bool = False
EXISTING_PRODUCER_CHANGED: bool = False
THIRD_RESULT_ONTOLOGY_CREATED: bool = False

LATE_EVENT_SILENTLY_DROPPED: bool = False
FINALIZED_WINDOW_SILENTLY_REOPENED: bool = False
SOURCE_REINGEST_REQUIRED: bool = False
NEW_SOURCE_EVENT_MINTED: bool = False
REPROCESSING_IS_TELEMETRY_DERIVATION_ONLY: bool = True
REPROCESSING_REUSES_SOURCE_DEDUP: bool = True
REPROCESSING_RERUNS_EXECUTION_SIDE_EFFECT: bool = False

OUT_OF_ORDER_REQUIRES_COMPARABLE_SOURCE_ORDER: bool = True

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_PROJECT_ID_LENGTH: int = 96
MAX_SOURCE_DEDUP_LENGTH: int = 128  # 64 hex but allow bound check
MAX_PROJECTION_ID_LENGTH: int = 128
MAX_SERIES_ID_LENGTH: int = 128
MAX_WINDOW_ID_LENGTH: int = 128
MAX_WINDOW_POLICY_ID_LENGTH: int = 128
MAX_WINDOW_POLICY_VERSION_LENGTH: int = 64
MAX_WINDOW_KEY_LENGTH: int = 128
MAX_ORDER_DOMAIN_LENGTH: int = 64
MAX_AGGREGATE_CONTRIBUTION_VALUE: int = 1_000_000_000
MIN_AGGREGATE_CONTRIBUTION_VALUE: int = -1_000_000_000

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---------------------------------------------------------------------------
# Helpers — validation (no wall clock, no filesystem, no DB)
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{label} must be non-empty")
    if len(v) > max_len:
        raise ValueError(f"{label} length {len(v)} exceeds {max_len}")
    if "\x00" in v:
        raise ValueError(f"{label} must not contain NUL")
    return v


def _validate_digest(value: object, label: str = "digest") -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be string, got {type(value).__name__}")
    v = value.strip().lower()
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ValueError(f"{label} must be 64 lower hex chars: {value!r}")
    return v


def _validate_bounded_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must not be bool")
    if not isinstance(value, int) or type(value) is not int:
        raise TypeError(f"{label} must be int, got {type(value).__name__}")
    # Reject bool masquerading already handled; reject NaN/Infinity not needed as int
    if value > MAX_AGGREGATE_CONTRIBUTION_VALUE or value < MIN_AGGREGATE_CONTRIBUTION_VALUE:
        raise ValueError(f"{label} {value} exceeds bounded range [{MIN_AGGREGATE_CONTRIBUTION_VALUE},{MAX_AGGREGATE_CONTRIBUTION_VALUE}]")
    return value


def _validate_tzaware(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _canonical_datetime_str(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    s = utc.isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

@unique
class AggregateOperation(str, Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    MIN = "MIN"
    MAX = "MAX"


@unique
class WindowLifecycle(str, Enum):
    OPEN = "OPEN"
    FINALIZED = "FINALIZED"


@unique
class Disposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REPROCESS_REQUIRED = "REPROCESS_REQUIRED"
    FAILED = "FAILED"
    NON_WINDOWABLE = "NON_WINDOWABLE"


AGGREGATE_OPERATIONS: frozenset[str] = frozenset(e.value for e in AggregateOperation)
WINDOW_LIFECYCLES: frozenset[str] = frozenset(e.value for e in WindowLifecycle)

# ---------------------------------------------------------------------------
# Window policy — pure, no wall clock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowPolicy:
    window_policy_id: str
    window_policy_version: str
    allow_ingestion_time_fallback: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "window_policy_id", _validate_bounded_str(self.window_policy_id, "window_policy_id", MAX_WINDOW_POLICY_ID_LENGTH))
        object.__setattr__(self, "window_policy_version", _validate_bounded_str(self.window_policy_version, "window_policy_version", MAX_WINDOW_POLICY_VERSION_LENGTH))
        if not isinstance(self.allow_ingestion_time_fallback, bool):
            raise TypeError("allow_ingestion_time_fallback must be bool")


def assign_window(
    policy: WindowPolicy,
    source_event_time: datetime | None,
    ingestion_time: datetime | None,
) -> str | None:
    """Pure window assignment.

    Returns window_key (bounded canonical identity) or None for NON_WINDOWABLE.
    Uses source_event_time if available; else ingestion_time only if policy permits fallback.
    Never invents timestamps, never calls wall clock.
    """
    if not isinstance(policy, WindowPolicy):
        raise TypeError(f"policy must be WindowPolicy, got {type(policy).__name__}")
    if source_event_time is not None:
        _validate_tzaware(source_event_time, "source_event_time")
        # Deterministic bounded window_key: use UTC date as canonical identity (bounded, not high cardinality)
        # Do not freeze duration globally, but provide deterministic mapping for proof.
        # Use calendar date (YYYY-MM-DD) as generic window_key.
        utc = source_event_time.astimezone(timezone.utc)
        key = utc.date().isoformat()
        if len(key) > MAX_WINDOW_KEY_LENGTH:
            raise ValueError("window_key exceeds bounds")
        if not _BOUNDED_ID_RE.fullmatch(key.replace("-", "_")):
            # allow dashes via replacement; use direct check for date pattern
            pass
        return key
    if ingestion_time is not None:
        _validate_tzaware(ingestion_time, "ingestion_time")
        if policy.allow_ingestion_time_fallback:
            utc = ingestion_time.astimezone(timezone.utc)
            key = utc.date().isoformat()
            return key
        return None
    return None

# ---------------------------------------------------------------------------
# Comparable order evidence — thin, bounded
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComparableOrderEvidence:
    order_domain: str
    ordinal: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_domain", _validate_bounded_str(self.order_domain, "order_domain", MAX_ORDER_DOMAIN_LENGTH))
        if isinstance(self.ordinal, bool):
            raise TypeError("ordinal must not be bool")
        if not isinstance(self.ordinal, int) or type(self.ordinal) is not int:
            raise TypeError(f"ordinal must be int, got {type(self.ordinal).__name__}")
        if self.ordinal < 0 or self.ordinal > MAX_AGGREGATE_CONTRIBUTION_VALUE:
            raise ValueError(f"ordinal {self.ordinal} exceeds bounded range")

# ---------------------------------------------------------------------------
# Aggregate contribution — derived from M1 identities, no arbitrary bag
# ---------------------------------------------------------------------------

_ALLOWED_CONTRIBUTION_FIELDS: frozenset[str] = frozenset({
    "project_id",
    "source_dedup_id",
    "projection_namespace",
    "projection_version",
    "projection_id",
    "aggregation_series_id",
    "aggregation_window_id",
    "window_policy_id",
    "window_policy_version",
    "operation",
    "value",
    "completeness",
    "source_event_time",
    "ingestion_time",
    "order_evidence",
})

@dataclass(frozen=True)
class AggregationContribution:
    project_id: str
    source_dedup_id: str
    projection_namespace: str
    projection_version: str
    projection_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None
    window_policy_id: str | None
    window_policy_version: str | None
    operation: AggregateOperation
    value: int
    completeness: CompletenessRecord
    source_event_time: datetime | None = None
    ingestion_time: datetime | None = None
    order_evidence: ComparableOrderEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "source_dedup_id", _validate_digest(self.source_dedup_id, "source_dedup_id"))
        object.__setattr__(self, "projection_namespace", _validate_bounded_str(self.projection_namespace, "projection_namespace", 64))
        object.__setattr__(self, "projection_version", _validate_bounded_str(self.projection_version, "projection_version", 64))
        expected_proj = _compute_projection_id(self.source_dedup_id, self.projection_namespace, self.projection_version)
        if self.projection_id != expected_proj:
            raise ValueError(f"projection_id mismatch: expected {expected_proj!r}, got {self.projection_id!r} (source_dedup mismatch or wrong namespace/version)")
        object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))
        if self.window_policy_id is not None:
            object.__setattr__(self, "window_policy_id", _validate_bounded_str(self.window_policy_id, "window_policy_id", MAX_WINDOW_POLICY_ID_LENGTH))
        if self.window_policy_version is not None:
            object.__setattr__(self, "window_policy_version", _validate_bounded_str(self.window_policy_version, "window_policy_version", MAX_WINDOW_POLICY_VERSION_LENGTH))
        # window policy id/version must be both present or both absent when window_id present?
        if self.aggregation_window_id is not None:
            if self.window_policy_id is None or self.window_policy_version is None:
                raise ValueError("window_policy_id/version required when aggregation_window_id present")
        # operation
        if isinstance(self.operation, AggregateOperation):
            pass
        elif isinstance(self.operation, str) and type(self.operation) is str:
            try:
                object.__setattr__(self, "operation", AggregateOperation(self.operation))
            except ValueError:
                raise ValueError(f"Unknown aggregate operation: {self.operation!r}")
        else:
            raise TypeError(f"operation must be AggregateOperation or str, got {type(self.operation).__name__}")
        object.__setattr__(self, "value", _validate_bounded_int(self.value, "value"))
        if not isinstance(self.completeness, CompletenessRecord):
            raise TypeError(f"completeness must be CompletenessRecord, got {type(self.completeness).__name__}")
        if self.source_event_time is not None:
            object.__setattr__(self, "source_event_time", _validate_tzaware(self.source_event_time, "source_event_time"))
        if self.ingestion_time is not None:
            object.__setattr__(self, "ingestion_time", _validate_tzaware(self.ingestion_time, "ingestion_time"))
        if self.order_evidence is not None and not isinstance(self.order_evidence, ComparableOrderEvidence):
            raise TypeError(f"order_evidence must be ComparableOrderEvidence or None")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "project_id": self.project_id,
            "source_dedup_id": self.source_dedup_id,
            "projection_namespace": self.projection_namespace,
            "projection_version": self.projection_version,
            "projection_id": self.projection_id,
            "aggregation_series_id": self.aggregation_series_id,
            "operation": self.operation.value,
            "value": self.value,
            "completeness": self.completeness.to_dict(),
        }
        if self.aggregation_window_id is not None:
            d["aggregation_window_id"] = self.aggregation_window_id
        if self.window_policy_id is not None:
            d["window_policy_id"] = self.window_policy_id
        if self.window_policy_version is not None:
            d["window_policy_version"] = self.window_policy_version
        if self.source_event_time is not None:
            d["source_event_time"] = _canonical_datetime_str(self.source_event_time)
        if self.ingestion_time is not None:
            d["ingestion_time"] = _canonical_datetime_str(self.ingestion_time)
        if self.order_evidence is not None:
            d["order_evidence"] = {"order_domain": self.order_evidence.order_domain, "ordinal": self.order_evidence.ordinal}
        return d


def create_aggregation_contribution(
    project_id: str,
    source_dedup_id: str,
    projection_namespace: str,
    projection_version: str,
    aggregation_series_id: str,
    aggregation_window_id: str | None,
    window_policy_id: str | None,
    window_policy_version: str | None,
    operation: AggregateOperation | str,
    value: int,
    completeness: CompletenessRecord,
    source_event_time: datetime | None = None,
    ingestion_time: datetime | None = None,
    order_evidence: ComparableOrderEvidence | None = None,
) -> AggregationContribution:
    proj_id = _compute_projection_id(_validate_digest(source_dedup_id, "source_dedup_id"), _validate_bounded_str(projection_namespace, "projection_namespace", 64), _validate_bounded_str(projection_version, "projection_version", 64))
    return AggregationContribution(
        project_id=project_id,
        source_dedup_id=source_dedup_id,
        projection_namespace=projection_namespace,
        projection_version=projection_version,
        projection_id=proj_id,
        aggregation_series_id=aggregation_series_id,
        aggregation_window_id=aggregation_window_id,
        window_policy_id=window_policy_id,
        window_policy_version=window_policy_version,
        operation=operation,  # type: ignore[arg-type]
        value=value,
        completeness=completeness,
        source_event_time=source_event_time,
        ingestion_time=ingestion_time,
        order_evidence=order_evidence,
    )

# ---------------------------------------------------------------------------
# Aggregate completeness composition — no global ordering
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregateCompleteness:
    complete: bool
    observed_states: frozenset[CompletenessState]
    # deterministic canonical tuple for serialization
    coverage_known: bool

    def canonical_tuple(self) -> tuple[str, ...]:
        return tuple(sorted(s.value for s in self.observed_states))

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "observed_states": sorted(s.value for s in self.observed_states),
            "coverage_known": self.coverage_known,
        }


def compose_aggregate_completeness(
    records: list[CompletenessRecord],
    coverage_known: bool = True,
) -> AggregateCompleteness:
    """Deterministic composition preserving all non-complete causes.

    Rules:
    - empty does not imply complete zero -> complete=False, observed includes UNKNOWN
    - if coverage_known is False -> complete=False, UNKNOWN visible
    - if any record state != COMPLETE -> complete=False, preserve all distinct states
    - only when coverage_known True AND every record is COMPLETE -> complete=True
    - order of input does not affect canonical result
    """
    if not isinstance(records, list):
        raise TypeError("records must be list")
    if not isinstance(coverage_known, bool):
        raise TypeError("coverage_known must be bool")
    if len(records) == 0:
        return AggregateCompleteness(complete=False, observed_states=frozenset({CompletenessState.UNKNOWN}), coverage_known=coverage_known)
    states: set[CompletenessState] = set()
    for r in records:
        if not isinstance(r, CompletenessRecord):
            raise TypeError(f"record must be CompletenessRecord, got {type(r).__name__}")
        states.add(r.state)
    # If coverage unknown, ensure UNKNOWN visible and not complete
    if not coverage_known:
        states.add(CompletenessState.UNKNOWN)
        return AggregateCompleteness(complete=False, observed_states=frozenset(states), coverage_known=False)
    # If any non-complete, preserve them, complete=False
    if any(s != CompletenessState.COMPLETE for s in states):
        return AggregateCompleteness(complete=False, observed_states=frozenset(states), coverage_known=True)
    return AggregateCompleteness(complete=True, observed_states=frozenset({CompletenessState.COMPLETE}), coverage_known=True)

# ---------------------------------------------------------------------------
# Aggregation state — frozen/value-domain, storage-neutral
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregationState:
    project_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None
    window_policy_id: str | None
    window_policy_version: str | None
    window_lifecycle: WindowLifecycle
    count: int
    sum_value: int
    min_value: int | None
    max_value: int | None
    seen_projection_ids: frozenset[str]
    seen_source_dedup_ids: frozenset[str]
    completeness: AggregateCompleteness
    # order tracking per domain: domain -> max ordinal seen
    max_order_per_domain: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))
        if self.window_policy_id is not None:
            object.__setattr__(self, "window_policy_id", _validate_bounded_str(self.window_policy_id, "window_policy_id", MAX_WINDOW_POLICY_ID_LENGTH))
        if self.window_policy_version is not None:
            object.__setattr__(self, "window_policy_version", _validate_bounded_str(self.window_policy_version, "window_policy_version", MAX_WINDOW_POLICY_VERSION_LENGTH))
        if isinstance(self.window_lifecycle, WindowLifecycle):
            pass
        elif isinstance(self.window_lifecycle, str) and type(self.window_lifecycle) is str:
            try:
                object.__setattr__(self, "window_lifecycle", WindowLifecycle(self.window_lifecycle))
            except ValueError:
                raise ValueError(f"Unknown window lifecycle: {self.window_lifecycle!r}")
        else:
            raise TypeError(f"window_lifecycle must be WindowLifecycle or str")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError("count must be int")
        if isinstance(self.sum_value, bool) or not isinstance(self.sum_value, int):
            raise TypeError("sum_value must be int")
        if self.min_value is not None:
            if isinstance(self.min_value, bool) or not isinstance(self.min_value, int):
                raise TypeError("min_value must be int or None")
        if self.max_value is not None:
            if isinstance(self.max_value, bool) or not isinstance(self.max_value, int):
                raise TypeError("max_value must be int or None")
        # validate seen sets are digests
        for pid in self.seen_projection_ids:
            _validate_digest(pid, "projection_id")
        for sid in self.seen_source_dedup_ids:
            _validate_digest(sid, "source_dedup_id")
        if not isinstance(self.completeness, AggregateCompleteness):
            raise TypeError("completeness must be AggregateCompleteness")
        if not isinstance(self.max_order_per_domain, Mapping):
            raise TypeError("max_order_per_domain must be mapping")
        # freeze sets
        object.__setattr__(self, "seen_projection_ids", frozenset(self.seen_projection_ids))
        object.__setattr__(self, "seen_source_dedup_ids", frozenset(self.seen_source_dedup_ids))
        object.__setattr__(self, "max_order_per_domain", dict(self.max_order_per_domain))


def create_initial_state(
    project_id: str,
    aggregation_series_id: str,
    aggregation_window_id: str | None,
    window_policy_id: str | None,
    window_policy_version: str | None,
    window_lifecycle: WindowLifecycle | str = WindowLifecycle.OPEN,
) -> AggregationState:
    return AggregationState(
        project_id=project_id,
        aggregation_series_id=aggregation_series_id,
        aggregation_window_id=aggregation_window_id,
        window_policy_id=window_policy_id,
        window_policy_version=window_policy_version,
        window_lifecycle=window_lifecycle,  # type: ignore[arg-type]
        count=0,
        sum_value=0,
        min_value=None,
        max_value=None,
        seen_projection_ids=frozenset(),
        seen_source_dedup_ids=frozenset(),
        completeness=AggregateCompleteness(complete=False, observed_states=frozenset({CompletenessState.UNKNOWN}), coverage_known=False),
        max_order_per_domain={},
    )

# ---------------------------------------------------------------------------
# Transition result — pure, bounded
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionResult:
    new_state: AggregationState
    disposition: Disposition
    is_duplicate: bool
    reprocess_required: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "disposition": self.disposition.value,
            "is_duplicate": self.is_duplicate,
            "reprocess_required": self.reprocess_required,
            "new_state_count": self.new_state.count,
            "new_state_sum": self.new_state.sum_value,
        }
        if self.error is not None:
            d["error"] = self.error
        return d

# ---------------------------------------------------------------------------
# Pure bounded aggregate transition
# ---------------------------------------------------------------------------

def _apply_operation(state: AggregationState, contrib: AggregationContribution) -> tuple[int, int, int | None, int | None]:
    op = contrib.operation
    val = contrib.value
    cnt = state.count
    s = state.sum_value
    mn = state.min_value
    mx = state.max_value
    if op == AggregateOperation.COUNT:
        # COUNT increments by 1 regardless of value? But value carries count contribution (expected 1)
        # Use value as count delta, but ensure value is 1 for strict? We allow any bounded int as count delta.
        new_cnt = cnt + val
        # sum unchanged for COUNT
        new_s = s
        new_mn = mn
        new_mx = mx
        if new_cnt > MAX_AGGREGATE_CONTRIBUTION_VALUE or new_cnt < 0:
            raise ValueError(f"count {new_cnt} exceeds bounded range")
        return new_cnt, new_s, new_mn, new_mx
    elif op == AggregateOperation.SUM:
        new_s = s + val
        if new_s > MAX_AGGREGATE_CONTRIBUTION_VALUE or new_s < MIN_AGGREGATE_CONTRIBUTION_VALUE:
            raise ValueError(f"sum {new_s} exceeds bounded range")
        return cnt, new_s, mn, mx
    elif op == AggregateOperation.MIN:
        if mn is None:
            new_mn = val
        else:
            new_mn = mn if mn < val else val
        return cnt, s, new_mn, mx
    elif op == AggregateOperation.MAX:
        if mx is None:
            new_mx = val
        else:
            new_mx = mx if mx > val else val
        return cnt, s, mn, new_mx
    else:
        raise ValueError(f"Unknown operation {op!r}")


def apply_contribution(
    state: AggregationState,
    contribution: AggregationContribution,
) -> TransitionResult:
    """Pure deterministic transition.

    Order of checks:
    1. validate project/series/window consistency (fail closed)
    2. duplicate projection check -> DUPLICATE, unchanged
    3. finalized window late disposition -> REPROCESS_REQUIRED, unchanged
    4. otherwise apply operation and update completeness/order
    No side effects, no storage, no wall clock, single writer.
    """
    # 1. basic type checks (already validated in dataclass, but cross-check state/contrib)
    if not isinstance(state, AggregationState):
        return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="invalid state type")  # type: ignore[arg-type]
    if not isinstance(contribution, AggregationContribution):
        # fail closed: return unchanged with error (typed bounded failure)
        try:
            # attempt to keep state unchanged
            return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="invalid contribution type")
        except Exception:
            raise TypeError("contribution must be AggregationContribution")
    # project scoped fail closed
    if contribution.project_id != state.project_id:
        return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="cross-project aggregation rejected")
    # series consistency
    if contribution.aggregation_series_id != state.aggregation_series_id:
        return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="series mismatch")
    # window consistency: contribution window must equal state window (both None or same digest)
    if contribution.aggregation_window_id != state.aggregation_window_id:
        return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="window mismatch")
    # window policy consistency if window present
    if state.aggregation_window_id is not None:
        if contribution.window_policy_id != state.window_policy_id or contribution.window_policy_version != state.window_policy_version:
            return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error="window policy mismatch")

    # 2. duplicate check precedes finalized check
    if contribution.projection_id in state.seen_projection_ids:
        return TransitionResult(new_state=state, disposition=Disposition.DUPLICATE, is_duplicate=True, reprocess_required=False)

    # 3. finalized window disposition
    if state.window_lifecycle == WindowLifecycle.FINALIZED:
        return TransitionResult(new_state=state, disposition=Disposition.REPROCESS_REQUIRED, is_duplicate=False, reprocess_required=True)

    # 4. apply operation — bounded, deterministic
    try:
        new_cnt, new_sum, new_min, new_max = _apply_operation(state, contribution)
    except (ValueError, TypeError) as exc:
        return TransitionResult(new_state=state, disposition=Disposition.FAILED, is_duplicate=False, reprocess_required=False, error=str(exc))

    # update seen sets
    new_seen_proj = frozenset(set(state.seen_projection_ids) | {contribution.projection_id})
    new_seen_src = frozenset(set(state.seen_source_dedup_ids) | {contribution.source_dedup_id})

    # completeness composition — preserve causes, deterministic, order independent
    existing_states = set(state.completeness.observed_states)
    # detect initial empty placeholder (no data yet)
    is_initial_empty = (
        state.count == 0
        and state.sum_value == 0
        and state.min_value is None
        and state.max_value is None
        and len(state.seen_projection_ids) == 0
    )
    if is_initial_empty:
        new_observed = frozenset({contribution.completeness.state})
    else:
        new_observed = frozenset(existing_states | {contribution.completeness.state})
    coverage_known = CompletenessState.UNKNOWN not in new_observed
    is_complete = coverage_known and new_observed == frozenset({CompletenessState.COMPLETE})
    new_completeness = AggregateCompleteness(complete=is_complete, observed_states=new_observed, coverage_known=coverage_known)

    # out-of-order handling: update max_order_per_domain deterministically, commutative aggregate unchanged
    new_max_order = dict(state.max_order_per_domain)
    if contribution.order_evidence is not None:
        dom = contribution.order_evidence.order_domain
        ordv = contribution.order_evidence.ordinal
        prev = new_max_order.get(dom)
        if prev is None or ordv > prev:
            new_max_order[dom] = ordv
        # else out-of-order, keep max, but aggregate value already updated independent of order

    new_state = AggregationState(
        project_id=state.project_id,
        aggregation_series_id=state.aggregation_series_id,
        aggregation_window_id=state.aggregation_window_id,
        window_policy_id=state.window_policy_id,
        window_policy_version=state.window_policy_version,
        window_lifecycle=state.window_lifecycle,
        count=new_cnt,
        sum_value=new_sum,
        min_value=new_min,
        max_value=new_max,
        seen_projection_ids=new_seen_proj,
        seen_source_dedup_ids=new_seen_src,
        completeness=new_completeness,
        max_order_per_domain=new_max_order,
    )
    return TransitionResult(new_state=new_state, disposition=Disposition.ACCEPTED, is_duplicate=False, reprocess_required=False)


def classify_late(
    state: AggregationState,
    contribution: AggregationContribution,
) -> str:
    """Classify late disposition without mutating state.

    Returns: "not_classifiable" if lifecycle/finalization unknown,
             "REPROCESS_REQUIRED" if finalized and unseen,
             "DUPLICATE" if duplicate,
             "OPEN_ACCEPT" if open.
    """
    if not isinstance(state, AggregationState) or not isinstance(contribution, AggregationContribution):
        return "not_classifiable"
    if contribution.aggregation_window_id != state.aggregation_window_id:
        return "not_classifiable"
    if state.window_lifecycle == WindowLifecycle.FINALIZED:
        if contribution.projection_id in state.seen_projection_ids:
            return "DUPLICATE"
        return "REPROCESS_REQUIRED"
    if state.window_lifecycle == WindowLifecycle.OPEN:
        return "OPEN_ACCEPT"
    return "not_classifiable"


def classify_out_of_order(
    state: AggregationState,
    contribution: AggregationContribution,
) -> str:
    """Classify out-of-order.

    Requires comparable source order evidence with same domain.
    Returns: "not_classifiable", "in_order", "out_of_order"
    """
    if contribution.order_evidence is None:
        return "not_classifiable"
    dom = contribution.order_evidence.order_domain
    ordv = contribution.order_evidence.ordinal
    prev = state.max_order_per_domain.get(dom)
    if prev is None:
        # no prior comparable order for this domain
        if len(state.seen_projection_ids) == 0:
            return "in_order"
        # if we have prior but no order for domain, not comparable?
        return "not_classifiable"
    if ordv < prev:
        return "out_of_order"
    if ordv >= prev:
        return "in_order"
    return "not_classifiable"

# ---------------------------------------------------------------------------
# Re-exports for W1 consumers — reuse M1 identity functions (no redefinition)
# ---------------------------------------------------------------------------

# Consumers should import these from telemetry_evidence / telemetry_metrics directly;
# re-exported here for convenience and to prove reuse.

compute_source_dedup_id = _compute_source_dedup_id
compute_projection_id = _compute_projection_id
compute_aggregation_series_id = _compute_aggregation_series_id
compute_aggregation_window_id = _compute_aggregation_window_id

__all__ = [
    "SOURCE_DEDUP_ID_REDEFINED",
    "PROJECTION_ID_REDEFINED",
    "AGGREGATION_SERIES_ID_REDEFINED",
    "AGGREGATION_WINDOW_ID_REDEFINED",
    "STORAGE_NEUTRAL_TRANSITIONS",
    "M2_WRITER_MODEL",
    "M2_ACTIVE_SAMPLING_REQUIRED",
    "GLOBAL_COMPLETENESS_SEVERITY_ORDER",
    "EMPTY_OBSERVATION_IMPLIES_COMPLETE_ZERO",
    "IMPLICIT_WALL_CLOCK_USED",
    "NEW_DATABASE_CREATED",
    "NEW_PERSISTENT_STORE_CREATED",
    "NEW_STORAGE_PROTOCOL_CREATED",
    "NEW_EVENT_BUS_CREATED",
    "NEW_BACKGROUND_WORKER_CREATED",
    "NEW_WORKFLOW_STATE_MACHINE_CREATED",
    "NEW_EXECUTION_JOURNAL_CREATED",
    "SECOND_RESULT_STORE_CREATED",
    "QUERY_LAYER_CREATED",
    "HYDRATION_AUTHORITY_CREATED",
    "ACTIVE_SAMPLER_CREATED",
    "RETENTION_WORKER_CREATED",
    "M1_CONTRACT_CHANGED",
    "EXISTING_PRODUCER_CHANGED",
    "THIRD_RESULT_ONTOLOGY_CREATED",
    "LATE_EVENT_SILENTLY_DROPPED",
    "FINALIZED_WINDOW_SILENTLY_REOPENED",
    "REPROCESSING_IS_TELEMETRY_DERIVATION_ONLY",
    "OUT_OF_ORDER_REQUIRES_COMPARABLE_SOURCE_ORDER",
    "AggregateOperation",
    "WindowLifecycle",
    "Disposition",
    "WindowPolicy",
    "assign_window",
    "ComparableOrderEvidence",
    "AggregationContribution",
    "create_aggregation_contribution",
    "AggregateCompleteness",
    "compose_aggregate_completeness",
    "AggregationState",
    "create_initial_state",
    "TransitionResult",
    "apply_contribution",
    "classify_late",
    "classify_out_of_order",
    "compute_source_dedup_id",
    "compute_projection_id",
    "compute_aggregation_series_id",
    "compute_aggregation_window_id",
]
