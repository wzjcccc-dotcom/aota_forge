"""Calibration Evidence & Statistical Finding Contract — S6 M4 W1.

W1 is the shared contract node for W2 and W3.

Architecture:
    existing M2 bounded query result
    +
    existing M3 metric semantics
            ↓
    W1 evidence / compatibility validation
            ↓
    bounded deterministic empirical finding
            ↓
    optional threshold / recommendation candidate contract
            ↓
    W2 or W3 domain-specific analysis

Invariants (observable flags for tests):
    M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS=yes
    M4_SECOND_METRIC_PROJECTION_LAYER_CREATED=no
    SECOND_ANALYTICS_STORE_CREATED=no
    SECOND_QUERY_ENGINE_CREATED=no
    SECOND_METRIC_RUNTIME_CREATED=no
    W1_PRIVATE_TELEMETRY_STORE_INTERNALS_ACCESSED=no
    EXISTING_TELEMETRY_QUERY_CONTRACT_REUSED=yes
    EMPIRICAL_FINDING_CONTRACT_PRESENT=yes
    FINDING_PROVENANCE_EXPLICIT=yes
    EMPIRICAL_FINDING_DETERMINISTIC=yes
    EMPIRICAL_FINDING_IS_CANONICAL_POLICY=no
    FINDING_ID_DETERMINISTIC=yes
    SOURCE_DEDUP_ID_REDEFINED=no
    PROJECTION_ID_REDEFINED=no
    AGGREGATION_SERIES_ID_REDEFINED=no
    AGGREGATION_WINDOW_ID_REDEFINED=no
    SECOND_METRIC_TAXONOMY_CREATED=no
    SECOND_METRIC_SUBJECT_ONTOLOGY_CREATED=no
    GENERAL_STATISTICS_FRAMEWORK_CREATED=no
    CALIBRATION_DENOMINATOR_COMPLETENESS_REQUIRED=yes
    NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR=yes
    INCOMPLETE_DENOMINATOR_NUMERIC_RATE_AVAILABLE=no
    ZERO_DENOMINATOR_NUMERIC_RATE_AVAILABLE=no
    EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO=no
    MISSING_TELEMETRY_IS_ZERO=no
    UNKNOWN_DENOMINATOR_IS_ZERO=no
    QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS=yes
    RESULT_COUNT_USED_AS_DEFAULT_EMPIRICAL_DENOMINATOR=no
    NON_FINITE_NUMERIC_FINDING_ALLOWED=no
    INCOMPATIBLE_SERIES_COMPARISON_FAILS_CLOSED=yes
    CROSS_PROJECT_FINDING_FAIL_CLOSED=yes
    THRESHOLD_CANDIDATE_CONTRACT_PRESENT=yes
    THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY=no
    THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED=no
    HARDCODED_UNIVERSAL_CALIBRATION_THRESHOLD_CREATED=no
    UNIVERSAL_MIN_SUPPORT_THRESHOLD=no
    OPAQUE_CONFIDENCE_SCORE_CREATED=no
    GENERIC_RECOMMENDATION_CONTRACT_PRESENT=yes
    DOMAIN_SPECIFIC_RECOMMENDATION_IMPLEMENTED_IN_W1=no
    RECOMMENDATION_ID_DETERMINISTIC=yes
    EMPIRICAL_FINDING_IS_AUTHORITY=no
    THRESHOLD_CANDIDATE_IS_AUTHORITY=no
    RECOMMENDATION_CANDIDATE_IS_AUTHORITY=no
    FINDING_CARDINALITY_BOUNDED=yes
    RAW_ARGV_IS_FINDING_DIMENSION=no
    RAW_COMMAND_IS_FINDING_DIMENSION=no
    RAW_SECRET_CAPTURED=no
    RAW_TOOL_PAYLOAD_CAPTURED=no
    PRIVATE_REASONING_CAPTURED=no
    W2_SCOPE_IMPLEMENTED_IN_W1=no
    W3_SCOPE_IMPLEMENTED_IN_W1=no
    W4_SCOPE_IMPLEMENTED_IN_W1=no
    GLOBAL_RECOMMENDATION_REGISTRY_CREATED=no
    DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W1=no
    M4_AUTOMATIC_TUNING_LOOP=no
    M4_BACKGROUND_OPTIMIZER_REQUIRED=no
    S4_TYPED_SOURCE_USED_BY_W1=no
    S5_POLICY_MUTATED_BY_W1=no
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT=yes
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE=yes
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT=yes
    IMPLICIT_WALL_CLOCK_USED=no
    RANDOMNESS_USED_IN_FINDING_ID=no
    WORKTREE_USED_IN_FINDING_ID=no

Pure deterministic functions only, no durable store, no background runtime.
Frozen dataclasses + bounded enums + pure functions, no large OO framework.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

from aota_forge.work_plane.telemetry_query import (
    TelemetryQueryResult,
    TelemetryQueryItem,
    TelemetryRef,
    QueryCoverage,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ContextCostUnit,
    parse_metric_family,
    parse_context_cost_unit,
)
from aota_forge.work_plane.telemetry_evidence import (
    CompletenessState,
    CompletenessScope,
)

# ---------------------------------------------------------------------------
# Invariant flags — observable for behavioural / negative architecture proof
# ---------------------------------------------------------------------------

M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS: bool = True
M4_SECOND_METRIC_PROJECTION_LAYER_CREATED: bool = False
SECOND_ANALYTICS_STORE_CREATED: bool = False
SECOND_QUERY_ENGINE_CREATED: bool = False
SECOND_METRIC_RUNTIME_CREATED: bool = False

W1_PRIVATE_TELEMETRY_STORE_INTERNALS_ACCESSED: bool = False
EXISTING_TELEMETRY_QUERY_CONTRACT_REUSED: bool = True

EMPIRICAL_FINDING_CONTRACT_PRESENT: bool = True
FINDING_PROVENANCE_EXPLICIT: bool = True
EMPIRICAL_FINDING_DETERMINISTIC: bool = True
EMPIRICAL_FINDING_IS_CANONICAL_POLICY: bool = False
FINDING_ID_DETERMINISTIC: bool = True

SOURCE_DEDUP_ID_REDEFINED: bool = False
PROJECTION_ID_REDEFINED: bool = False
AGGREGATION_SERIES_ID_REDEFINED: bool = False
AGGREGATION_WINDOW_ID_REDEFINED: bool = False

SECOND_METRIC_TAXONOMY_CREATED: bool = False
SECOND_METRIC_SUBJECT_ONTOLOGY_CREATED: bool = False
GENERAL_STATISTICS_FRAMEWORK_CREATED: bool = False

CALIBRATION_DENOMINATOR_COMPLETENESS_REQUIRED: bool = True
NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR: bool = True
INCOMPLETE_DENOMINATOR_NUMERIC_RATE_AVAILABLE: bool = False
ZERO_DENOMINATOR_NUMERIC_RATE_AVAILABLE: bool = False

EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO: bool = False
MISSING_TELEMETRY_IS_ZERO: bool = False
UNKNOWN_DENOMINATOR_IS_ZERO: bool = False

QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS: bool = True
RESULT_COUNT_USED_AS_DEFAULT_EMPIRICAL_DENOMINATOR: bool = False
NON_FINITE_NUMERIC_FINDING_ALLOWED: bool = False

INCOMPATIBLE_SERIES_COMPARISON_FAILS_CLOSED: bool = True
CROSS_PROJECT_FINDING_FAIL_CLOSED: bool = True

THRESHOLD_CANDIDATE_CONTRACT_PRESENT: bool = True
THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY: bool = False
THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED: bool = False
HARDCODED_UNIVERSAL_CALIBRATION_THRESHOLD_CREATED: bool = False
UNIVERSAL_MIN_SUPPORT_THRESHOLD: bool = False
OPAQUE_CONFIDENCE_SCORE_CREATED: bool = False

GENERIC_RECOMMENDATION_CONTRACT_PRESENT: bool = True
DOMAIN_SPECIFIC_RECOMMENDATION_IMPLEMENTED_IN_W1: bool = False
RECOMMENDATION_ID_DETERMINISTIC: bool = True

EMPIRICAL_FINDING_IS_AUTHORITY: bool = False
THRESHOLD_CANDIDATE_IS_AUTHORITY: bool = False
RECOMMENDATION_CANDIDATE_IS_AUTHORITY: bool = False

FINDING_CARDINALITY_BOUNDED: bool = True
RAW_ARGV_IS_FINDING_DIMENSION: bool = False
RAW_COMMAND_IS_FINDING_DIMENSION: bool = False
RAW_SECRET_CAPTURED: bool = False
RAW_TOOL_PAYLOAD_CAPTURED: bool = False
PRIVATE_REASONING_CAPTURED: bool = False

W2_SCOPE_IMPLEMENTED_IN_W1: bool = False
W3_SCOPE_IMPLEMENTED_IN_W1: bool = False
W4_SCOPE_IMPLEMENTED_IN_W1: bool = False
GLOBAL_RECOMMENDATION_REGISTRY_CREATED: bool = False
DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W1: bool = False
M4_AUTOMATIC_TUNING_LOOP: bool = False
M4_BACKGROUND_OPTIMIZER_REQUIRED: bool = False

S4_TYPED_SOURCE_USED_BY_W1: bool = False
S5_POLICY_MUTATED_BY_W1: bool = False
S5_SOURCE_CONSUMED_BY_W1: bool = False

ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT: bool = True
ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE: bool = True
ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT: bool = True

IMPLICIT_WALL_CLOCK_USED: bool = False
RANDOMNESS_USED_IN_FINDING_ID: bool = False
WORKTREE_USED_IN_FINDING_ID: bool = False

# Compatibility with earlier naming
FINDING_PROVENANCE_EXPLICIT_FLAG: bool = True

# Version
CALIBRATION_CONTRACT_VERSION: str = "s6-m4-w1-v1"
SUPPORTED_CALIBRATION_VERSIONS: frozenset[str] = frozenset({CALIBRATION_CONTRACT_VERSION})

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_PROJECT_ID_LENGTH: int = 96
MAX_METHOD_VERSION_LENGTH: int = 64
MAX_TARGET_REF_LENGTH: int = 128
MAX_SERIES_ID_LENGTH: int = 128
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Helper validators (pure, no wall clock)
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
    if not _DIGEST_RE.fullmatch(v):
        raise ValueError(f"{label} must be 64 lower hex chars: {value!r}")
    return v

def _validate_finite_number(value: object, label: str) -> float | int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must not be bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be int or float, got {type(value).__name__}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite, got {value!r}")
    return value

# ---------------------------------------------------------------------------
# Enums — bounded method identity and dispositions
# ---------------------------------------------------------------------------

@unique
class FindingMethod(str, Enum):
    DIRECT_OBSERVED_AGGREGATE = "direct_observed_aggregate"
    COMPLETE_DENOMINATOR_RATE = "complete_denominator_rate"
    COMPATIBLE_SERIES_BOUNDED_COMPARISON = "compatible_series_bounded_comparison"

@unique
class FindingDisposition(str, Enum):
    COMPLETE = "complete"
    PARTIAL_EVIDENCE = "partial_evidence"
    ZERO_DENOMINATOR = "zero_denominator"
    INCOMPLETE_DENOMINATOR = "incomplete_denominator"
    TRUNCATED_COVERAGE = "truncated_coverage"
    EMPTY_EVIDENCE = "empty_evidence"
    INCOMPATIBLE_SERIES = "incompatible_series"
    MISSING_DENOMINATOR = "missing_denominator"
    UNKNOWN_DENOMINATOR = "unknown_denominator"
    CORRUPT_EVIDENCE = "corrupt_evidence"
    UNKNOWN_VERSION = "unknown_version"
    PARTIAL_COVERAGE = "partial_coverage"
    EXPIRED_EVIDENCE = "expired_evidence"

@unique
class RecommendationKind(str, Enum):
    GENERIC_CALIBRATION = "generic_calibration"

# ---------------------------------------------------------------------------
# Thin analysis descriptor — only where query projection insufficient
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationSeriesDescriptor:
    """Thin immutable descriptor that references accepted types.

    It references MetricFamily, canonical MetricSubject (as opaque digest-backed string
    for W1; subject identity itself is not redefined), ContextCostUnit,
    aggregation_series_id/window_id, projection/normalization version where public,
    window policy identity/version where public. It does NOT redefine taxonomy.
    """

    project_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None = None
    metric_family: MetricFamily | None = None
    # metric_subject stored as canonical string; we accept any object that has canonical representation
    # but we validate it is bounded and does not contain raw payload. For W1 we store subject identity string.
    metric_subject_identity: str | None = None
    context_cost_unit: ContextCostUnit | None = None
    normalization_version: str | None = None
    projection_namespace: str | None = None
    projection_version: str | None = None
    window_policy_id: str | None = None
    window_policy_version: str | None = None
    # window_key for time comparability (bounded)
    window_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))
        if self.metric_family is not None:
            if isinstance(self.metric_family, MetricFamily):
                pass
            elif isinstance(self.metric_family, str) and type(self.metric_family) is str:
                mf = parse_metric_family(self.metric_family)
                object.__setattr__(self, "metric_family", mf)
            else:
                raise TypeError("metric_family must be MetricFamily or str")
        if self.metric_subject_identity is not None:
            v = _validate_bounded_str(self.metric_subject_identity, "metric_subject_identity", 256)
            # forbid raw high-cardinality patterns like task_id, raw command, etc.
            for forbidden in ("task-", "attempt-", "raw_argv", "raw_command", "argv", "secret"):
                if forbidden in v.lower():
                    raise ValueError(f"metric_subject_identity contains forbidden high-cardinality {forbidden!r}: {v!r}")
            object.__setattr__(self, "metric_subject_identity", v)
        if self.context_cost_unit is not None:
            if isinstance(self.context_cost_unit, ContextCostUnit):
                pass
            elif isinstance(self.context_cost_unit, str) and type(self.context_cost_unit) is str:
                object.__setattr__(self, "context_cost_unit", parse_context_cost_unit(self.context_cost_unit))
            else:
                raise TypeError("context_cost_unit must be ContextCostUnit or str")
        if self.normalization_version is not None:
            object.__setattr__(self, "normalization_version", _validate_bounded_str(self.normalization_version, "normalization_version", 32))
        if self.projection_namespace is not None:
            object.__setattr__(self, "projection_namespace", _validate_bounded_str(self.projection_namespace, "projection_namespace", 64))
        if self.projection_version is not None:
            object.__setattr__(self, "projection_version", _validate_bounded_str(self.projection_version, "projection_version", 32))
        if self.window_policy_id is not None:
            object.__setattr__(self, "window_policy_id", _validate_bounded_str(self.window_policy_id, "window_policy_id", 128))
        if self.window_policy_version is not None:
            object.__setattr__(self, "window_policy_version", _validate_bounded_str(self.window_policy_version, "window_policy_version", 64))
        if self.window_key is not None:
            object.__setattr__(self, "window_key", _validate_bounded_str(self.window_key, "window_key", 128))

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "aggregation_series_id": self.aggregation_series_id,
            "project_id": self.project_id,
        }
        if self.aggregation_window_id is not None:
            d["aggregation_window_id"] = self.aggregation_window_id
        if self.metric_family is not None:
            d["metric_family"] = self.metric_family.value if isinstance(self.metric_family, MetricFamily) else str(self.metric_family)
        if self.metric_subject_identity is not None:
            d["metric_subject_identity"] = self.metric_subject_identity
        if self.context_cost_unit is not None:
            d["context_cost_unit"] = self.context_cost_unit.value if isinstance(self.context_cost_unit, ContextCostUnit) else str(self.context_cost_unit)
        if self.normalization_version is not None:
            d["normalization_version"] = self.normalization_version
        if self.projection_namespace is not None:
            d["projection_namespace"] = self.projection_namespace
        if self.projection_version is not None:
            d["projection_version"] = self.projection_version
        if self.window_policy_id is not None:
            d["window_policy_id"] = self.window_policy_id
        if self.window_policy_version is not None:
            d["window_policy_version"] = self.window_policy_version
        if self.window_key is not None:
            d["window_key"] = self.window_key
        return canonicalize(d, path="CalibrationSeriesDescriptor")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FindingProvenance:
    """Bounded provenance for finding: query digest + evidence refs + series ids."""

    project_id: str
    query_digest: str
    evidence_refs: tuple[TelemetryRef, ...]
    series_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "query_digest", _validate_digest(self.query_digest, "query_digest"))
        # validate refs are TelemetryRef
        for idx, r in enumerate(self.evidence_refs):
            if not isinstance(r, TelemetryRef):
                raise TypeError(f"evidence_refs[{idx}] must be TelemetryRef, got {type(r).__name__}")
            if r.project_id != self.project_id:
                raise ValueError(f"cross-project evidence ref {r.project_id!r} != provenance {self.project_id!r} -> FAIL_CLOSED")
        for sid in self.series_ids:
            _validate_digest(sid, "series_id")

    @property
    def is_authority(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# Empirical Finding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmpiricalFinding:
    """Minimal immutable empirical finding contract."""

    project_id: str
    finding_id: str
    method: FindingMethod
    method_version: str
    provenance: FindingProvenance
    series_descriptor: CalibrationSeriesDescriptor | None
    disposition: FindingDisposition
    numeric_value: float | int | None
    numerator: int | None
    denominator: int | None
    support_count: int
    completeness_complete: bool
    coverage: QueryCoverage
    is_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "finding_id", _validate_digest(self.finding_id, "finding_id"))
        if isinstance(self.method, FindingMethod):
            pass
        elif isinstance(self.method, str) and type(self.method) is str:
            try:
                object.__setattr__(self, "method", FindingMethod(self.method))
            except ValueError:
                raise ValueError(f"Unknown finding method: {self.method!r}")
        else:
            raise TypeError("method must be FindingMethod or str")
        object.__setattr__(self, "method_version", _validate_bounded_str(self.method_version, "method_version", MAX_METHOD_VERSION_LENGTH))
        if self.method_version not in SUPPORTED_CALIBRATION_VERSIONS:
            # allow any bounded version but prefer supported; fail closed only if not in allowed? For W1 we allow only supported
            # To keep determinism, require supported
            raise ValueError(f"Unsupported method version: {self.method_version!r}")
        if not isinstance(self.provenance, FindingProvenance):
            raise TypeError("provenance must be FindingProvenance")
        if self.provenance.project_id != self.project_id:
            raise ValueError(f"provenance project {self.provenance.project_id!r} != finding {self.project_id!r} -> FAIL_CLOSED")
        if self.series_descriptor is not None and not isinstance(self.series_descriptor, CalibrationSeriesDescriptor):
            raise TypeError("series_descriptor must be CalibrationSeriesDescriptor or None")
        if isinstance(self.disposition, FindingDisposition):
            pass
        elif isinstance(self.disposition, str) and type(self.disposition) is str:
            try:
                object.__setattr__(self, "disposition", FindingDisposition(self.disposition))
            except ValueError:
                raise ValueError(f"Unknown disposition: {self.disposition!r}")
        else:
            raise TypeError("disposition must be FindingDisposition or str")
        if self.numeric_value is not None:
            _validate_finite_number(self.numeric_value, "numeric_value")
        if self.numerator is not None:
            if isinstance(self.numerator, bool) or not isinstance(self.numerator, int):
                raise TypeError("numerator must be int or None")
        if self.denominator is not None:
            if isinstance(self.denominator, bool) or not isinstance(self.denominator, int):
                raise TypeError("denominator must be int or None")
        if isinstance(self.support_count, bool) or not isinstance(self.support_count, int):
            raise TypeError("support_count must be int")
        if self.support_count < 0:
            raise ValueError("support_count must be non-negative")
        if not isinstance(self.completeness_complete, bool):
            raise TypeError("completeness_complete must be bool")
        if not isinstance(self.coverage, QueryCoverage):
            raise TypeError("coverage must be QueryCoverage")
        if self.is_authority is not False:
            raise ValueError("finding is_authority must be False")

    @property
    def is_canonical_policy(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# Threshold Candidate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThresholdCandidate:
    """Bounded threshold-candidate representation, not policy."""

    project_id: str
    candidate_id: str
    method: FindingMethod
    method_version: str
    finding_ref: str
    provenance: FindingProvenance
    support_count: int
    completeness_complete: bool
    coverage: QueryCoverage
    boundary_value: int | float
    is_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "candidate_id", _validate_digest(self.candidate_id, "candidate_id"))
        if isinstance(self.method, FindingMethod):
            pass
        elif isinstance(self.method, str) and type(self.method) is str:
            try:
                object.__setattr__(self, "method", FindingMethod(self.method))
            except ValueError:
                raise ValueError(f"Unknown method: {self.method!r}")
        else:
            raise TypeError("method must be FindingMethod")
        object.__setattr__(self, "method_version", _validate_bounded_str(self.method_version, "method_version", MAX_METHOD_VERSION_LENGTH))
        if self.method_version not in SUPPORTED_CALIBRATION_VERSIONS:
            raise ValueError(f"Unsupported method version: {self.method_version!r}")
        object.__setattr__(self, "finding_ref", _validate_digest(self.finding_ref, "finding_ref"))
        if not isinstance(self.provenance, FindingProvenance):
            raise TypeError("provenance must be FindingProvenance")
        if self.provenance.project_id != self.project_id:
            raise ValueError("cross-project threshold candidate -> FAIL_CLOSED")
        if isinstance(self.support_count, bool) or not isinstance(self.support_count, int):
            raise TypeError("support_count must be int")
        if self.support_count < 0:
            raise ValueError("support_count must be non-negative")
        if not isinstance(self.completeness_complete, bool):
            raise TypeError("completeness_complete must be bool")
        if not isinstance(self.coverage, QueryCoverage):
            raise TypeError("coverage must be QueryCoverage")
        _validate_finite_number(self.boundary_value, "boundary_value")
        if self.is_authority is not False:
            raise ValueError("threshold candidate is_authority must be False")

    @property
    def is_canonical_policy(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# Generic Recommendation Candidate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecommendationCandidate:
    """Smallest shared non-authoritative recommendation contract for W2/W3."""

    project_id: str
    recommendation_id: str
    kind: RecommendationKind
    finding_refs: tuple[str, ...]
    method: FindingMethod
    method_version: str
    target_ref: str
    is_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "recommendation_id", _validate_digest(self.recommendation_id, "recommendation_id"))
        if isinstance(self.kind, RecommendationKind):
            pass
        elif isinstance(self.kind, str) and type(self.kind) is str:
            try:
                object.__setattr__(self, "kind", RecommendationKind(self.kind))
            except ValueError:
                raise ValueError(f"Unknown recommendation kind: {self.kind!r}")
        else:
            raise TypeError("kind must be RecommendationKind")
        # finding_refs are finding ids
        for fid in self.finding_refs:
            _validate_digest(fid, "finding_ref")
        if isinstance(self.method, FindingMethod):
            pass
        elif isinstance(self.method, str) and type(self.method) is str:
            try:
                object.__setattr__(self, "method", FindingMethod(self.method))
            except ValueError:
                raise ValueError(f"Unknown method: {self.method!r}")
        else:
            raise TypeError("method must be FindingMethod")
        object.__setattr__(self, "method_version", _validate_bounded_str(self.method_version, "method_version", MAX_METHOD_VERSION_LENGTH))
        if self.method_version not in SUPPORTED_CALIBRATION_VERSIONS:
            raise ValueError(f"Unsupported method version: {self.method_version!r}")
        object.__setattr__(self, "target_ref", _validate_bounded_str(self.target_ref, "target_ref", MAX_TARGET_REF_LENGTH))
        if self.is_authority is not False:
            raise ValueError("recommendation is_authority must be False")

# ---------------------------------------------------------------------------
# Deterministic identity helpers
# ---------------------------------------------------------------------------

def compute_finding_id(
    *,
    project_id: str,
    method: FindingMethod | str,
    method_version: str,
    evidence_digests: tuple[str, ...] | list[str],
    series_id: str | None,
    numeric_repr: str,
) -> str:
    """Deterministic finding identity.

    finding_id = hash(project_id + bounded method/version + canonical evidence digests + series + normalized result)
    No wall clock, no random, no worktree.
    """
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    m = FindingMethod(method) if isinstance(method, str) else method
    if not isinstance(m, FindingMethod):
        raise TypeError("method must be FindingMethod")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    if mv not in SUPPORTED_CALIBRATION_VERSIONS:
        raise ValueError(f"Unsupported method version: {mv!r}")
    # evidence digests sorted deterministic
    if not isinstance(evidence_digests, (list, tuple)):
        raise TypeError("evidence_digests must be list or tuple")
    digests: list[str] = []
    for d in evidence_digests:
        digests.append(_validate_digest(d, "evidence_digest"))
    digests_sorted = tuple(sorted(digests))
    if series_id is not None:
        sid = _validate_digest(series_id, "series_id")
    else:
        sid = None
    # numeric_repr must be bounded string
    nr = _validate_bounded_str(numeric_repr, "numeric_repr", 256)
    # canonical payload
    payload = {
        "method": m.value,
        "method_version": mv,
        "evidence_digests": list(digests_sorted),
        "numeric_repr": nr,
        "project_id": pid,
    }
    if sid is not None:
        payload["series_id"] = sid
    canonical = canonicalize(payload, path="finding_id")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

def compute_threshold_candidate_id(
    *,
    project_id: str,
    method: FindingMethod | str,
    method_version: str,
    finding_ref: str,
    boundary_repr: str,
) -> str:
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    m = FindingMethod(method) if isinstance(method, str) else method
    if not isinstance(m, FindingMethod):
        raise TypeError("method must be FindingMethod")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    fr = _validate_digest(finding_ref, "finding_ref")
    br = _validate_bounded_str(boundary_repr, "boundary_repr", 128)
    payload = {
        "boundary_repr": br,
        "finding_ref": fr,
        "method": m.value,
        "method_version": mv,
        "project_id": pid,
    }
    canonical = canonicalize(payload, path="threshold_candidate_id")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

def compute_recommendation_id(
    *,
    project_id: str,
    kind: RecommendationKind | str,
    finding_refs: tuple[str, ...] | list[str],
    target_ref: str,
    method_version: str,
) -> str:
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    k = RecommendationKind(kind) if isinstance(kind, str) else kind
    if not isinstance(k, RecommendationKind):
        raise TypeError("kind must be RecommendationKind")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    tr = _validate_bounded_str(target_ref, "target_ref", MAX_TARGET_REF_LENGTH)
    if not isinstance(finding_refs, (list, tuple)):
        raise TypeError("finding_refs must be list/tuple")
    digests: list[str] = []
    for d in finding_refs:
        digests.append(_validate_digest(d, "finding_ref"))
    digests_sorted = tuple(sorted(digests))
    payload = {
        "finding_refs": list(digests_sorted),
        "kind": k.value,
        "method_version": mv,
        "project_id": pid,
        "target_ref": tr,
    }
    canonical = canonicalize(payload, path="recommendation_id")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Compatibility validator
# ---------------------------------------------------------------------------

def validate_series_compatibility(
    a: CalibrationSeriesDescriptor,
    b: CalibrationSeriesDescriptor,
) -> bool:
    """Validate series semantic compatibility before comparison.

    Checks project_id, MetricFamily, MetricSubject, normalization version,
    projection semantics/version, context unit, window policy, window comparability.
    Returns True if compatible, raises ValueError fail-closed if incompatible.
    """
    if not isinstance(a, CalibrationSeriesDescriptor) or not isinstance(b, CalibrationSeriesDescriptor):
        raise TypeError("both arguments must be CalibrationSeriesDescriptor")
    if a.project_id != b.project_id:
        raise ValueError(f"incompatible project: {a.project_id!r} != {b.project_id!r} -> FAIL_CLOSED")
    # metric family
    if a.metric_family is not None and b.metric_family is not None:
        if a.metric_family != b.metric_family:
            raise ValueError(f"incompatible metric_family: {a.metric_family!r} != {b.metric_family!r} -> FAIL_CLOSED")
    # metric subject
    if a.metric_subject_identity is not None and b.metric_subject_identity is not None:
        if a.metric_subject_identity != b.metric_subject_identity:
            raise ValueError(f"incompatible metric_subject: {a.metric_subject_identity!r} != {b.metric_subject_identity!r} -> FAIL_CLOSED")
    # context unit
    if a.context_cost_unit is not None and b.context_cost_unit is not None:
        if a.context_cost_unit != b.context_cost_unit:
            raise ValueError(f"incompatible context_cost_unit: {a.context_cost_unit!r} != {b.context_cost_unit!r} -> FAIL_CLOSED")
    # normalization version
    if a.normalization_version is not None and b.normalization_version is not None:
        if a.normalization_version != b.normalization_version:
            raise ValueError(f"incompatible normalization_version: {a.normalization_version!r} != {b.normalization_version!r} -> FAIL_CLOSED")
    # projection semantics
    if a.projection_namespace is not None and b.projection_namespace is not None:
        if a.projection_namespace != b.projection_namespace:
            raise ValueError(f"incompatible projection_namespace: {a.projection_namespace!r} != {b.projection_namespace!r} -> FAIL_CLOSED")
    if a.projection_version is not None and b.projection_version is not None:
        if a.projection_version != b.projection_version:
            raise ValueError(f"incompatible projection_version: {a.projection_version!r} != {b.projection_version!r} -> FAIL_CLOSED")
    # window policy
    if a.window_policy_id is not None and b.window_policy_id is not None:
        if a.window_policy_id != b.window_policy_id:
            raise ValueError(f"incompatible window_policy_id: {a.window_policy_id!r} != {b.window_policy_id!r} -> FAIL_CLOSED")
    if a.window_policy_version is not None and b.window_policy_version is not None:
        if a.window_policy_version != b.window_policy_version:
            raise ValueError(f"incompatible window_policy_version: {a.window_policy_version!r} != {b.window_policy_version!r} -> FAIL_CLOSED")
    # window comparability: if both have window_id and they differ, consider incompatible unless explicit scope allows?
    # For W1 we require exact match for rate denominators where window matters.
    if a.aggregation_window_id is not None and b.aggregation_window_id is not None:
        if a.aggregation_window_id != b.aggregation_window_id:
            # Check window_key comparability as well
            if a.window_key is not None and b.window_key is not None:
                if a.window_key != b.window_key:
                    raise ValueError(f"incompatible window_key: {a.window_key!r} != {b.window_key!r} -> FAIL_CLOSED")
            else:
                # window ids differ -> incompatible
                raise ValueError(f"incompatible aggregation_window_id: {a.aggregation_window_id!r} != {b.aggregation_window_id!r} -> FAIL_CLOSED")
    # analysis scope time comparability: if window keys differ and not both None, already handled
    return True

def is_series_compatible(a: CalibrationSeriesDescriptor, b: CalibrationSeriesDescriptor) -> bool:
    try:
        validate_series_compatibility(a, b)
        return True
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Empirical finding creation — direct observation
# ---------------------------------------------------------------------------

def create_direct_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
    series_descriptor: CalibrationSeriesDescriptor | None = None,
    value: int | float | None = None,
) -> EmpiricalFinding:
    """Create deterministic direct observed aggregate finding preserving completeness.

    Inputs: bounded query result + bounded item (projection). Does not access private store.
    Validates project isolation, does not reinterpret empty as zero, preserves coverage/completeness separation.
    """
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if not isinstance(query_result, TelemetryQueryResult):
        raise TypeError("query_result must be TelemetryQueryResult")
    if not isinstance(item, TelemetryQueryItem):
        raise TypeError("item must be TelemetryQueryItem")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    if mv not in SUPPORTED_CALIBRATION_VERSIONS:
        raise ValueError(f"Unsupported method version: {mv!r}")
    # Cross-project fail closed
    if query_result.project_id != pid:
        raise ValueError(f"cross-project query_result: {query_result.project_id!r} != {pid!r} -> FAIL_CLOSED")
    if item.project_id != pid:
        raise ValueError(f"cross-project item: {item.project_id!r} != {pid!r} -> FAIL_CLOSED")
    if query_result.project_id != item.project_id:
        raise ValueError(f"query_result project {query_result.project_id!r} != item project {item.project_id!r} -> FAIL_CLOSED")
    # Check descriptor project if provided
    if series_descriptor is not None:
        if not isinstance(series_descriptor, CalibrationSeriesDescriptor):
            raise TypeError("series_descriptor must be CalibrationSeriesDescriptor")
        if series_descriptor.project_id != pid:
            raise ValueError(f"descriptor project {series_descriptor.project_id!r} != {pid!r} -> FAIL_CLOSED")

    # Empty query does not become zero: if query_result.coverage == EMPTY and item list empty? But we have an item, so not empty.
    # If coverage EMPTY and no items, caller shouldn't provide an item; we check coverage EMPTY should not produce numeric rate but direct observation can still be EMPTY disposition with no value.
    # For direct observation, we allow creation even if coverage EMPTY but disposition indicates empty.

    # Determine numeric value: if caller supplies, validate; else derive from item
    if value is not None:
        _validate_finite_number(value, "value")
        numeric = value
    else:
        # Use item's sum_value if present else count; for generic we use count if sum == 0? Choose count for COUNT, sum for SUM.
        # For deterministic, if sum_value != 0 use sum, else count? To keep simple, use count if item.count else sum
        # Actually item has both count and sum; we need to choose one deterministically.
        # For W1 v0, direct observed aggregate finding: expose observed COUNT or SUM etc. We expose count as primary.
        # We'll use item.count as numeric if item.count is not None else sum
        # But item always has count and sum; we will use count if method is direct observed? Let's use count.
        # However if count is 0 and sum is non-zero, count would be misleading. We'll prioritize sum if sum !=0 and count is small? Better to expose sum when available?
        # For test determinism, we will expose item.count when provided and sum when count is 0? Let's define rule: if item.count != 0, use count else use sum_value
        # Simpler: use item.count as observed COUNT
        numeric = item.count
        # Validate finite
        _validate_finite_number(numeric, "item.count")

    # Determine disposition based on coverage and completeness
    # If coverage is not COMPLETE, disposition reflects that
    coverage = query_result.coverage
    if coverage == QueryCoverage.EMPTY:
        disposition = FindingDisposition.EMPTY_EVIDENCE
        # numeric should still be reported? For empty, there is no item, but we have item, so treat as partial? The spec says empty query does not become zero, so if we have empty coverage but have item, something inconsistent.
        # We'll keep numeric but disposition empty indicates not zero-rate
    elif coverage == QueryCoverage.TRUNCATED_BY_LIMIT:
        disposition = FindingDisposition.TRUNCATED_COVERAGE
    elif coverage == QueryCoverage.PARTIAL:
        disposition = FindingDisposition.PARTIAL_COVERAGE
    elif coverage == QueryCoverage.EXPIRED:
        disposition = FindingDisposition.EXPIRED_EVIDENCE
    elif coverage == QueryCoverage.CORRUPT:
        raise ValueError("corrupt coverage -> FAIL_CLOSED")
    elif coverage == QueryCoverage.UNKNOWN_VERSION:
        raise ValueError("unknown version coverage -> FAIL_CLOSED")
    elif coverage == QueryCoverage.COMPLETE:
        # Check item completeness
        if item.completeness_complete:
            disposition = FindingDisposition.COMPLETE
        else:
            disposition = FindingDisposition.PARTIAL_EVIDENCE
    else:
        disposition = FindingDisposition.PARTIAL_EVIDENCE

    # provenance
    # Use query_result's query_digest and item's ref/series
    # Need to compute query_digest from result? result has query_digest field. Use that.
    qd = query_result.query_digest
    # Ensure digest valid
    _validate_digest(qd, "query_digest")
    # Evidence digests: use item's content_digest and ref's content_digest
    evidence_digests = (item.content_digest, item.ref.content_digest)
    # For finding_id, use canonical representation of numeric
    # Prefer exact preservation: use str(numeric) but ensure deterministic for int vs float
    if isinstance(numeric, float):
        # Use canonical JSON representation for float to avoid formatting instability
        numeric_repr = canonical_json(numeric)
    else:
        numeric_repr = str(numeric)

    # series id for finding: use item's aggregation_series_id
    series_id = item.aggregation_series_id
    # compute finding id deterministically
    fid = compute_finding_id(
        project_id=pid,
        method=FindingMethod.DIRECT_OBSERVED_AGGREGATE,
        method_version=mv,
        evidence_digests=evidence_digests,
        series_id=series_id,
        numeric_repr=numeric_repr,
    )

    # provenance
    prov = FindingProvenance(
        project_id=pid,
        query_digest=qd,
        evidence_refs=(item.ref,),
        series_ids=(series_id,),
    )

    # support count: use item.count as support
    support = item.count
    # completeness_complete from item
    comp_complete = item.completeness_complete

    # Create finding (copy objects not mutated)
    finding = EmpiricalFinding(
        project_id=pid,
        finding_id=fid,
        method=FindingMethod.DIRECT_OBSERVED_AGGREGATE,
        method_version=mv,
        provenance=prov,
        series_descriptor=series_descriptor,
        disposition=disposition,
        numeric_value=numeric,
        numerator=None,
        denominator=None,
        support_count=support,
        completeness_complete=comp_complete,
        coverage=coverage,
        is_authority=False,
    )
    return finding

# ---------------------------------------------------------------------------
# Complete-denominator rate helper
# ---------------------------------------------------------------------------

def create_rate_finding(
    *,
    project_id: str,
    numerator_value: int,
    denominator_value: int,
    numerator_descriptor: CalibrationSeriesDescriptor,
    denominator_descriptor: CalibrationSeriesDescriptor,
    query_result: TelemetryQueryResult,
    denominator_item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Create numeric rate where denominator completeness is required.

    Validates project, series compatibility, coverage, denominator completeness, support, non-zero.
    Returns EmpiricalFinding with numeric_value = numerator/denominator (deterministic float) or
    with disposition indicating why numeric unavailable and numeric_value=None.
    Fail-closed cases raise ValueError.
    """
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if not isinstance(query_result, TelemetryQueryResult):
        raise TypeError("query_result must be TelemetryQueryResult")
    if not isinstance(denominator_item, TelemetryQueryItem):
        raise TypeError("denominator_item must be TelemetryQueryItem")
    if not isinstance(numerator_descriptor, CalibrationSeriesDescriptor) or not isinstance(denominator_descriptor, CalibrationSeriesDescriptor):
        raise TypeError("descriptors must be CalibrationSeriesDescriptor")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    if mv not in SUPPORTED_CALIBRATION_VERSIONS:
        raise ValueError(f"Unsupported method version: {mv!r}")
    if isinstance(numerator_value, bool) or not isinstance(numerator_value, int):
        raise TypeError("numerator_value must be int")
    if isinstance(denominator_value, bool) or not isinstance(denominator_value, int):
        raise TypeError("denominator_value must be int")
    # Validate finite (int always finite)
    # Cross-project fail closed
    if query_result.project_id != pid:
        raise ValueError(f"cross-project query_result {query_result.project_id!r} != {pid!r} -> FAIL_CLOSED")
    if denominator_item.project_id != pid:
        raise ValueError(f"cross-project denominator_item {denominator_item.project_id!r} != {pid!r} -> FAIL_CLOSED")
    if numerator_descriptor.project_id != pid or denominator_descriptor.project_id != pid:
        raise ValueError("cross-project descriptor -> FAIL_CLOSED")
    # Also check that query_result project matches denominator item project already done

    # Series compatibility (fail closed)
    validate_series_compatibility(numerator_descriptor, denominator_descriptor)

    # Coverage gate: must be COMPLETE to produce numeric rate
    coverage = query_result.coverage
    if coverage == QueryCoverage.CORRUPT:
        raise ValueError("corrupt coverage -> FAIL_CLOSED")
    if coverage == QueryCoverage.UNKNOWN_VERSION:
        raise ValueError("unknown version coverage -> FAIL_CLOSED")
    # For these, numeric unavailable but not necessarily fail-closed exception? spec says at minimum TRUNCATED_BY_LIMIT -> numeric rate unavailable, EMPTY -> not zero-rate
    # We will return finding with appropriate disposition and numeric None, not raise, except for corrupt/unknown.
    # However we should treat TRUNCATED, PARTIAL, EXPIRED, EMPTY as no numeric.

    # Aggregate completeness gate
    denom_complete = denominator_item.completeness_complete
    # Need also check observed_states for partial etc: completeness_complete False indicates incomplete
    # But also need to check denominator_item lifecycle? Already captured.

    # Support and non-zero denominator checks
    # Missing denominator vs zero denominator distinction
    # If denominator_item is missing (we wouldn't have an item), caller would not provide item; but if they provide item with count 0 and completeness incomplete vs complete distinction?

    # Determine disposition and whether numeric available
    disposition: FindingDisposition
    numeric: float | int | None = None

    # Check coverage first
    if coverage != QueryCoverage.COMPLETE:
        if coverage == QueryCoverage.TRUNCATED_BY_LIMIT:
            disposition = FindingDisposition.TRUNCATED_COVERAGE
        elif coverage == QueryCoverage.EMPTY:
            disposition = FindingDisposition.EMPTY_EVIDENCE
        elif coverage == QueryCoverage.PARTIAL:
            disposition = FindingDisposition.PARTIAL_COVERAGE
        elif coverage == QueryCoverage.EXPIRED:
            disposition = FindingDisposition.EXPIRED_EVIDENCE
        else:
            disposition = FindingDisposition.PARTIAL_COVERAGE
        # numeric remains None
        numeric = None
    elif not denom_complete:
        # Even with complete coverage, denominator incomplete
        disposition = FindingDisposition.INCOMPLETE_DENOMINATOR
        numeric = None
    elif denominator_value == 0:
        # Zero denominator distinct
        disposition = FindingDisposition.ZERO_DENOMINATOR
        numeric = None
    else:
        # Valid complete non-zero denominator, produce numeric rate
        disposition = FindingDisposition.COMPLETE
        # Use deterministic float via canonical_json? But we prefer exact numerator/denominator preservation.
        # Compute float deterministically but also store numerator/denominator for identity.
        # Ensure non-finite not produced
        if denominator_value == 0:
            raise ValueError("zero denominator should have been handled")
        numeric = numerator_value / denominator_value
        # Validate finite
        if not math.isfinite(numeric):
            raise ValueError(f"non-finite rate {numeric!r}")
        # Also reject if numeric is inf/nan already checked via isfinite

    # For finding id, numeric_repr must be deterministic: use numerator/denominator if numeric available else disposition
    if numeric is not None:
        # Prefer exact representation: "numerator/denominator"
        # Use canonical string f"{numerator_value}/{denominator_value}"
        numeric_repr = f"{numerator_value}/{denominator_value}"
        # Also canonicalize float? We'll use this exact string for identity, not float formatting
    else:
        numeric_repr = f"no_numeric:{disposition.value}"

    # Compute finding id deterministically
    # Evidence digests: use query_digest + denominator_item digests + numerator descriptor digests?
    # Use query_digest, denominator_item content_digest, and series ids
    evidence_digests = (query_result.query_digest, denominator_item.content_digest, denominator_item.ref.content_digest)
    # series id for rate: use denominator series? Or combination? Use numerator series for canonical? To keep deterministic, use numerator descriptor series
    series_id = numerator_descriptor.aggregation_series_id
    fid = compute_finding_id(
        project_id=pid,
        method=FindingMethod.COMPLETE_DENOMINATOR_RATE,
        method_version=mv,
        evidence_digests=evidence_digests,
        series_id=series_id,
        numeric_repr=numeric_repr,
    )

    prov = FindingProvenance(
        project_id=pid,
        query_digest=query_result.query_digest,
        evidence_refs=(denominator_item.ref,),
        series_ids=(numerator_descriptor.aggregation_series_id, denominator_descriptor.aggregation_series_id),
    )

    # support_count: use denominator_item.count
    support = denominator_item.count

    finding = EmpiricalFinding(
        project_id=pid,
        finding_id=fid,
        method=FindingMethod.COMPLETE_DENOMINATOR_RATE,
        method_version=mv,
        provenance=prov,
        series_descriptor=numerator_descriptor,
        disposition=disposition,
        numeric_value=numeric,
        numerator=numerator_value,
        denominator=denominator_value,
        support_count=support,
        completeness_complete=denom_complete,
        coverage=coverage,
        is_authority=False,
    )
    return finding

# ---------------------------------------------------------------------------
# Threshold candidate creation
# ---------------------------------------------------------------------------

def create_threshold_candidate(
    *,
    project_id: str,
    finding: EmpiricalFinding,
    boundary_value: int | float,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> ThresholdCandidate:
    """Create bounded threshold candidate from explicit finding provenance."""
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if not isinstance(finding, EmpiricalFinding):
        raise TypeError("finding must be EmpiricalFinding")
    if finding.project_id != pid:
        raise ValueError(f"cross-project finding {finding.project_id!r} != {pid!r} -> FAIL_CLOSED")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    if mv not in SUPPORTED_CALIBRATION_VERSIONS:
        raise ValueError(f"Unsupported method version: {mv!r}")
    _validate_finite_number(boundary_value, "boundary_value")
    # No universal threshold: we do not enforce a hardcoded threshold; caller supplies boundary explicitly
    # Require explicit provenance from finding
    prov = finding.provenance
    # finding_ref is finding id
    fid = finding.finding_id
    # boundary repr deterministic
    if isinstance(boundary_value, float):
        boundary_repr = canonical_json(boundary_value)
    else:
        boundary_repr = str(boundary_value)
    cid = compute_threshold_candidate_id(
        project_id=pid,
        method=finding.method,
        method_version=mv,
        finding_ref=fid,
        boundary_repr=boundary_repr,
    )
    candidate = ThresholdCandidate(
        project_id=pid,
        candidate_id=cid,
        method=finding.method,
        method_version=mv,
        finding_ref=fid,
        provenance=prov,
        support_count=finding.support_count,
        completeness_complete=finding.completeness_complete,
        coverage=finding.coverage,
        boundary_value=boundary_value,
        is_authority=False,
    )
    return candidate

# ---------------------------------------------------------------------------
# Recommendation candidate creation
# ---------------------------------------------------------------------------

def create_recommendation_candidate(
    *,
    project_id: str,
    kind: RecommendationKind | str = RecommendationKind.GENERIC_CALIBRATION,
    finding_refs: tuple[str, ...] | list[str],
    target_ref: str,
    method: FindingMethod | str = FindingMethod.DIRECT_OBSERVED_AGGREGATE,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> RecommendationCandidate:
    """Create generic non-authoritative recommendation candidate."""
    pid = _validate_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if isinstance(kind, RecommendationKind):
        k = kind
    elif isinstance(kind, str) and type(kind) is str:
        try:
            k = RecommendationKind(kind)
        except ValueError:
            raise ValueError(f"Unknown recommendation kind: {kind!r} -> FAIL_CLOSED (generic only)")
    else:
        raise TypeError("kind must be RecommendationKind")
    # Domain-specific kinds are not allowed: only GENERIC_CALIBRATION is permitted for W1
    if k != RecommendationKind.GENERIC_CALIBRATION:
        raise ValueError(f"domain-specific recommendation kind {k.value!r} not allowed in W1 -> FAIL_CLOSED")
    if not isinstance(finding_refs, (list, tuple)):
        raise TypeError("finding_refs must be list/tuple")
    fids: list[str] = []
    for fid in finding_refs:
        fids.append(_validate_digest(fid, "finding_ref"))
    if len(fids) == 0:
        raise ValueError("finding_refs must be non-empty")
    tr = _validate_bounded_str(target_ref, "target_ref", MAX_TARGET_REF_LENGTH)
    # forbid raw high-cardinality target refs containing paths, secrets, etc.
    for forbidden in ("task-", "secret", "argv", "raw"):
        if forbidden in tr.lower():
            raise ValueError(f"target_ref contains forbidden {forbidden!r}")
    m = FindingMethod(method) if isinstance(method, str) else method
    if not isinstance(m, FindingMethod):
        raise TypeError("method must be FindingMethod")
    mv = _validate_bounded_str(method_version, "method_version", MAX_METHOD_VERSION_LENGTH)
    if mv not in SUPPORTED_CALIBRATION_VERSIONS:
        raise ValueError(f"Unsupported method version: {mv!r}")
    rid = compute_recommendation_id(
        project_id=pid,
        kind=k,
        finding_refs=tuple(fids),
        target_ref=tr,
        method_version=mv,
    )
    candidate = RecommendationCandidate(
        project_id=pid,
        recommendation_id=rid,
        kind=k,
        finding_refs=tuple(sorted(fids)),
        method=m,
        method_version=mv,
        target_ref=tr,
        is_authority=False,
    )
    return candidate

# ---------------------------------------------------------------------------
# Utility for tests / validation helpers
# ---------------------------------------------------------------------------

def is_empty_query_result(query_result: TelemetryQueryResult) -> bool:
    return query_result.coverage == QueryCoverage.EMPTY and query_result.result_count == 0 and len(query_result.items) == 0

def is_truncated_query_result(query_result: TelemetryQueryResult) -> bool:
    return query_result.coverage == QueryCoverage.TRUNCATED_BY_LIMIT

__all__ = [
    "M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS",
    "M4_SECOND_METRIC_PROJECTION_LAYER_CREATED",
    "SECOND_ANALYTICS_STORE_CREATED",
    "SECOND_QUERY_ENGINE_CREATED",
    "SECOND_METRIC_RUNTIME_CREATED",
    "W1_PRIVATE_TELEMETRY_STORE_INTERNALS_ACCESSED",
    "EXISTING_TELEMETRY_QUERY_CONTRACT_REUSED",
    "EMPIRICAL_FINDING_CONTRACT_PRESENT",
    "FINDING_PROVENANCE_EXPLICIT",
    "EMPIRICAL_FINDING_DETERMINISTIC",
    "EMPIRICAL_FINDING_IS_CANONICAL_POLICY",
    "FINDING_ID_DETERMINISTIC",
    "SOURCE_DEDUP_ID_REDEFINED",
    "PROJECTION_ID_REDEFINED",
    "AGGREGATION_SERIES_ID_REDEFINED",
    "AGGREGATION_WINDOW_ID_REDEFINED",
    "SECOND_METRIC_TAXONOMY_CREATED",
    "SECOND_METRIC_SUBJECT_ONTOLOGY_CREATED",
    "GENERAL_STATISTICS_FRAMEWORK_CREATED",
    "CALIBRATION_DENOMINATOR_COMPLETENESS_REQUIRED",
    "NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR",
    "INCOMPLETE_DENOMINATOR_NUMERIC_RATE_AVAILABLE",
    "ZERO_DENOMINATOR_NUMERIC_RATE_AVAILABLE",
    "EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO",
    "MISSING_TELEMETRY_IS_ZERO",
    "UNKNOWN_DENOMINATOR_IS_ZERO",
    "QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS",
    "RESULT_COUNT_USED_AS_DEFAULT_EMPIRICAL_DENOMINATOR",
    "NON_FINITE_NUMERIC_FINDING_ALLOWED",
    "INCOMPATIBLE_SERIES_COMPARISON_FAILS_CLOSED",
    "CROSS_PROJECT_FINDING_FAIL_CLOSED",
    "THRESHOLD_CANDIDATE_CONTRACT_PRESENT",
    "THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY",
    "THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED",
    "HARDCODED_UNIVERSAL_CALIBRATION_THRESHOLD_CREATED",
    "UNIVERSAL_MIN_SUPPORT_THRESHOLD",
    "OPAQUE_CONFIDENCE_SCORE_CREATED",
    "GENERIC_RECOMMENDATION_CONTRACT_PRESENT",
    "DOMAIN_SPECIFIC_RECOMMENDATION_IMPLEMENTED_IN_W1",
    "RECOMMENDATION_ID_DETERMINISTIC",
    "EMPIRICAL_FINDING_IS_AUTHORITY",
    "THRESHOLD_CANDIDATE_IS_AUTHORITY",
    "RECOMMENDATION_CANDIDATE_IS_AUTHORITY",
    "FINDING_CARDINALITY_BOUNDED",
    "RAW_ARGV_IS_FINDING_DIMENSION",
    "RAW_COMMAND_IS_FINDING_DIMENSION",
    "RAW_SECRET_CAPTURED",
    "RAW_TOOL_PAYLOAD_CAPTURED",
    "PRIVATE_REASONING_CAPTURED",
    "W2_SCOPE_IMPLEMENTED_IN_W1",
    "W3_SCOPE_IMPLEMENTED_IN_W1",
    "W4_SCOPE_IMPLEMENTED_IN_W1",
    "GLOBAL_RECOMMENDATION_REGISTRY_CREATED",
    "DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W1",
    "M4_AUTOMATIC_TUNING_LOOP",
    "M4_BACKGROUND_OPTIMIZER_REQUIRED",
    "S4_TYPED_SOURCE_USED_BY_W1",
    "S5_POLICY_MUTATED_BY_W1",
    "S5_SOURCE_CONSUMED_BY_W1",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT",
    "IMPLICIT_WALL_CLOCK_USED",
    "RANDOMNESS_USED_IN_FINDING_ID",
    "WORKTREE_USED_IN_FINDING_ID",
    "CALIBRATION_CONTRACT_VERSION",
    "SUPPORTED_CALIBRATION_VERSIONS",
    "FindingMethod",
    "FindingDisposition",
    "RecommendationKind",
    "CalibrationSeriesDescriptor",
    "FindingProvenance",
    "EmpiricalFinding",
    "ThresholdCandidate",
    "RecommendationCandidate",
    "compute_finding_id",
    "compute_threshold_candidate_id",
    "compute_recommendation_id",
    "validate_series_compatibility",
    "is_series_compatible",
    "create_direct_observation_finding",
    "create_rate_finding",
    "create_threshold_candidate",
    "create_recommendation_candidate",
    "is_empty_query_result",
    "is_truncated_query_result",
]
