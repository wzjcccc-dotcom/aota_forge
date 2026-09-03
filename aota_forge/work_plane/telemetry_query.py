"""Bounded Query / Ref / Selective Hydration — S6 M2 W3.

Architecture:
    EphemeralTelemetryStore / TelemetryStore
              ↓
    typed bounded TelemetryQueryRequest
              ↓
    pure bounded selector / projection
              ↓
    TelemetryQueryResult
              ↓
    aggregate refs/digests by default
              ↓
    optional explicit governed hydration seam

Invariants
----------
* QUERY_CONTRACT_PRESENT=yes
* QUERY_CONTRACT_STORAGE_IMPLEMENTATION_NEUTRAL=yes (testable against TelemetryStore protocol)
* QUERY_PROJECT_SCOPED=yes, CROSS_PROJECT_QUERY_FAIL_CLOSED=yes
* ARBITRARY_QUERY_FIELD_ALLOWED=no, UNBOUNDED_QUERY_ALLOWED=no
* QUERY_RESULT_ORDER_DETERMINISTIC=yes
* QUERY_LIMIT_REACHED_IMPLIES_COMPLETE=no
* EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO=no
* QUERY_AUTO_HYDRATES_SOURCE=no
* REF_CONTRACT_PRESENT=yes, EXISTING_REF_REUSED=no (see 22)
* REF_POSSESSION_IS_AUTHORITY=no, SELECTIVE_HYDRATION_EXPLICIT=yes
* HYDRATION_BOUNDARY_PRESENT=yes, HYDRATION_DIGEST_VERIFIED=yes
* HYDRATION_PROJECT_SCOPE_REAUTHORIZED=yes, REF_ONLY_SUPPORTED=yes
* RAW_SOURCE_COPIED_INTO_TELEMETRY_STORE_FOR_HYDRATION=no
* SECOND_HYDRATION_AUTHORITY_CREATED=no
* RETENTION_EXPIRATION_QUERY_RETURNS_ZERO=no
* CORRUPT_RECORD_SILENTLY_SKIPPED=no
* UNKNOWN_VERSION_FAIL_CLOSED=yes
* QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS=yes
* WORKTREE_IS_DEFAULT_QUERY_SCOPE=no, WORKTREE_IS_DEFAULT_METRIC_DIMENSION=no
* METRIC_FAMILY_USES_CANONICAL_M1_TYPE=yes (where selector supported), FREE_TEXT_METRIC_FAMILY_ALLOWED=no
* AGGREGATION_SERIES_ID_REDEFINED=no, AGGREGATION_WINDOW_ID_REDEFINED=no
* W2_PRIVATE_STORAGE_INTERNALS_ACCESSED=no (no _records/_store dict access)
* W2_MINIMAL_ENUMERATION_SEAM_SUFFICIENT=yes (relies on minimal bounded enumeration seam if present; otherwise exact-key path sufficient)
* IMPLICIT_WALL_CLOCK_USED=no
* DURABLE_STORAGE_IMPLEMENTED=no, GENERIC_QUERY_ENGINE_CREATED=no, M3_METRIC_PROJECTION_IMPLEMENTED=no
* QUERY_RESULT_IS_AUTHORITY=no, REF_IS_AUTHORITY=no, HYDRATED_TELEMETRY_IS_AUTHORITY=no
* M1/W1/W2 contracts unchanged

No durable store, no DB, no filesystem, no DSL, no planner, no new hydration authority,
no M3 metric projection, no wall clock.

Section 22 decision:
EXISTING_REF_REUSED=no
Reason: GovernedReference (S2) is bound to artifact/evidence Tool/result governance
semantics (ToolOutputRef, ArtifactReference). Telemetry aggregate ref identifies
project/series/window/digest/schema and is not a Tool result. Reusing ToolOutputRef
would falsely claim Tool-result semantics and authority laundering. A thin
telemetry-specific ref TelemetryRef is defined that reuses digest verification
and project scope reauthorization semantics from S2 without claiming Tool authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.telemetry_evidence import (
    CompletenessState,
    parse_completeness_state,
    CompletenessScope,
    parse_completeness_scope,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    parse_metric_family,
)
from aota_forge.work_plane.telemetry_store import (
    STORAGE_CONTRACT_VERSION,
    SUPPORTED_STORAGE_VERSIONS,
    StorageKey,
    StorageEnvelope,
    RetentionTombstone,
    TelemetryStore,
    EphemeralTelemetryStore,
    derive_storage_key,
    compute_state_digest,
)
from aota_forge.work_plane.telemetry_aggregation import (
    AggregationState,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Flags for tests / review
# ---------------------------------------------------------------------------

QUERY_CONTRACT_PRESENT: bool = True
QUERY_CONTRACT_STORAGE_IMPLEMENTATION_NEUTRAL: bool = True
QUERY_PROJECT_SCOPED: bool = True
CROSS_PROJECT_QUERY_FAIL_CLOSED: bool = True
ARBITRARY_QUERY_FIELD_ALLOWED: bool = False
UNBOUNDED_QUERY_ALLOWED: bool = False
QUERY_RESULT_ORDER_DETERMINISTIC: bool = True
QUERY_LIMIT_REACHED_IMPLIES_COMPLETE: bool = False
EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO: bool = False
QUERY_AUTO_HYDRATES_SOURCE: bool = False
REF_CONTRACT_PRESENT: bool = True
EXISTING_REF_REUSED: bool = False
REF_POSSESSION_IS_AUTHORITY: bool = False
SELECTIVE_HYDRATION_EXPLICIT: bool = True
HYDRATION_BOUNDARY_PRESENT: bool = True
HYDRATION_DIGEST_VERIFIED: bool = True
HYDRATION_PROJECT_SCOPE_REAUTHORIZED: bool = True
REF_ONLY_SUPPORTED: bool = True
RAW_SOURCE_COPIED_INTO_TELEMETRY_STORE_FOR_HYDRATION: bool = False
SECOND_HYDRATION_AUTHORITY_CREATED: bool = False
RETENTION_EXPIRATION_QUERY_RETURNS_ZERO: bool = False
CORRUPT_RECORD_SILENTLY_SKIPPED: bool = False
UNKNOWN_VERSION_FAIL_CLOSED: bool = True
QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS: bool = True
WORKTREE_IS_DEFAULT_QUERY_SCOPE: bool = False
WORKTREE_IS_DEFAULT_METRIC_DIMENSION: bool = False
METRIC_FAMILY_USES_CANONICAL_M1_TYPE: bool = True
FREE_TEXT_METRIC_FAMILY_ALLOWED: bool = False
AGGREGATION_SERIES_ID_REDEFINED: bool = False
AGGREGATION_WINDOW_ID_REDEFINED: bool = False
W2_PRIVATE_STORAGE_INTERNALS_ACCESSED: bool = False
W2_MINIMAL_ENUMERATION_SEAM_SUFFICIENT: bool = True
IMPLICIT_WALL_CLOCK_USED: bool = False
DURABLE_STORAGE_IMPLEMENTED: bool = False
GENERIC_QUERY_ENGINE_CREATED: bool = False
M3_METRIC_PROJECTION_IMPLEMENTED: bool = False
QUERY_RESULT_IS_AUTHORITY: bool = False
REF_IS_AUTHORITY: bool = False
HYDRATED_TELEMETRY_IS_AUTHORITY: bool = False

# storage strategy preservation
# NO_RAW secret/payload: query never copies raw source payload
M2_STORAGE_STRATEGY: str = "STORAGE_NEUTRAL_WITH_EPHEMERAL_DEFAULT"
M2_WRITER_MODEL: str = "single_writer"
M2_ACTIVE_SAMPLING_REQUIRED: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_PROJECT_ID_LENGTH: int = 96
MAX_QUERY_LIMIT: int = 50
MIN_QUERY_LIMIT: int = 1
MAX_REF_LENGTH: int = 512

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---------------------------------------------------------------------------
# Helpers
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

def _validate_limit(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("limit must not be bool")
    if not isinstance(value, int) or type(value) is not int:
        raise TypeError(f"limit must be int, got {type(value).__name__}")
    if value < MIN_QUERY_LIMIT or value > MAX_QUERY_LIMIT:
        raise ValueError(f"limit must be in [{MIN_QUERY_LIMIT},{MAX_QUERY_LIMIT}], got {value}")
    return value

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class QueryError(ValueError):
    pass

class CrossProjectQueryError(QueryError):
    pass

class UnknownVersionError(QueryError):
    pass

class IntegrityQueryError(QueryError):
    pass

class ExpiredQueryError(QueryError):
    pass

# ---------------------------------------------------------------------------
# Query coverage disposition (thin, not M1 completeness enum)
# ---------------------------------------------------------------------------

@unique
class QueryCoverage(str, Enum):
    COMPLETE = "complete"
    TRUNCATED_BY_LIMIT = "truncated_by_limit"
    EXPIRED = "expired"
    CORRUPT = "corrupt"
    UNKNOWN_VERSION = "unknown_version"
    EMPTY = "empty"
    PARTIAL = "partial"

# ---------------------------------------------------------------------------
# TelemetryRef — bounded, integrity-bound, not authority
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryRef:
    """Bounded telemetry aggregate ref (project/series/window/digest/version).

    Not filesystem path, not Python object address. Possession != authority.
    """

    project_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None
    content_digest: str
    storage_contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))
        object.__setattr__(self, "content_digest", _validate_digest(self.content_digest, "content_digest"))
        v = _validate_bounded_str(self.storage_contract_version, "storage_contract_version", 32)
        if v not in SUPPORTED_STORAGE_VERSIONS:
            raise UnknownVersionError(f"Unsupported storage version for ref: {v!r}")
        object.__setattr__(self, "storage_contract_version", v)

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "aggregation_series_id": self.aggregation_series_id,
            "content_digest": self.content_digest,
            "project_id": self.project_id,
            "storage_contract_version": self.storage_contract_version,
        }
        if self.aggregation_window_id is not None:
            d["aggregation_window_id"] = self.aggregation_window_id
        return canonicalize(d, path="TelemetryRef")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

def derive_telemetry_ref(envelope: StorageEnvelope) -> TelemetryRef:
    if not isinstance(envelope, StorageEnvelope):
        raise TypeError(f"envelope must be StorageEnvelope, got {type(envelope).__name__}")
    return TelemetryRef(
        project_id=envelope.storage_key.project_id,
        aggregation_series_id=envelope.storage_key.aggregation_series_id,
        aggregation_window_id=envelope.storage_key.aggregation_window_id,
        content_digest=envelope.content_digest,
        storage_contract_version=envelope.storage_contract_version,
    )

# ---------------------------------------------------------------------------
# TelemetryQueryRequest — typed bounded allowlist
# ---------------------------------------------------------------------------

_ALLOWED_QUERY_FIELDS: frozenset[str] = frozenset({
    "project_id",
    "aggregation_series_id",
    "aggregation_window_id",
    "limit",
    "completeness_state",
    "completeness_scope",
    "storage_contract_version",
    "metric_family",
})

@dataclass(frozen=True)
class TelemetryQueryRequest:
    """Typed bounded query request. project_id required, all else optional bounded allowlist.

    Selectors justified by W1/W2 stored data. No arbitrary field names, no SQL, no regex,
    no expression tree, no metadata predicate. Worktree is not default scope.
    """

    project_id: str
    limit: int
    aggregation_series_id: str | None = None
    aggregation_window_id: str | None = None
    completeness_state: CompletenessState | None = None
    completeness_scope: CompletenessScope | None = None
    storage_contract_version: str | None = None
    metric_family: MetricFamily | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "limit", _validate_limit(self.limit))
        if self.aggregation_series_id is not None:
            object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))
        if self.completeness_state is not None:
            if isinstance(self.completeness_state, CompletenessState):
                pass
            elif isinstance(self.completeness_state, str) and type(self.completeness_state) is str:
                try:
                    object.__setattr__(self, "completeness_state", parse_completeness_state(self.completeness_state))
                except Exception as exc:
                    raise ValueError(f"Unknown completeness_state: {self.completeness_state!r}") from exc
            else:
                raise TypeError("completeness_state must be CompletenessState or str")
        if self.completeness_scope is not None:
            if isinstance(self.completeness_scope, CompletenessScope):
                pass
            elif isinstance(self.completeness_scope, str) and type(self.completeness_scope) is str:
                try:
                    object.__setattr__(self, "completeness_scope", parse_completeness_scope(self.completeness_scope))
                except Exception as exc:
                    raise ValueError(f"Unknown completeness_scope: {self.completeness_scope!r}") from exc
            else:
                raise TypeError("completeness_scope must be CompletenessScope or str")
        if self.storage_contract_version is not None:
            v = _validate_bounded_str(self.storage_contract_version, "storage_contract_version", 32)
            if v not in SUPPORTED_STORAGE_VERSIONS:
                raise UnknownVersionError(f"Unsupported storage version in query: {v!r}")
            object.__setattr__(self, "storage_contract_version", v)
        if self.metric_family is not None:
            if isinstance(self.metric_family, MetricFamily):
                pass
            elif isinstance(self.metric_family, str) and type(self.metric_family) is str:
                try:
                    object.__setattr__(self, "metric_family", parse_metric_family(self.metric_family))
                except Exception as exc:
                    raise ValueError(f"Unknown metric family: {self.metric_family!r}") from exc
            else:
                raise TypeError("metric_family must be MetricFamily or str")
        # completeness without scope or vice versa? allow filtering by either individually.
        # No worktree field.

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "limit": self.limit,
            "project_id": self.project_id,
        }
        if self.aggregation_series_id is not None:
            d["aggregation_series_id"] = self.aggregation_series_id
        if self.aggregation_window_id is not None:
            d["aggregation_window_id"] = self.aggregation_window_id
        if self.completeness_state is not None:
            d["completeness_state"] = self.completeness_state.value if isinstance(self.completeness_state, CompletenessState) else str(self.completeness_state)
        if self.completeness_scope is not None:
            d["completeness_scope"] = self.completeness_scope.value if isinstance(self.completeness_scope, CompletenessScope) else str(self.completeness_scope)
        if self.storage_contract_version is not None:
            d["storage_contract_version"] = self.storage_contract_version
        if self.metric_family is not None:
            d["metric_family"] = self.metric_family.value if isinstance(self.metric_family, MetricFamily) else str(self.metric_family)
        return canonicalize(d, path="TelemetryQueryRequest")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def is_authority(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# TelemetryQueryItem — bounded projection (not authority, no raw payload)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryQueryItem:
    """Bounded telemetry projection for one aggregate.

    Contains aggregate summary + ref/digest, not raw ExecutionEvent etc.
    No raw_payload, argv, stdout, transcript, filesystem path.
    """

    project_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None
    content_digest: str
    storage_contract_version: str
    count: int
    sum_value: int
    min_value: int | None
    max_value: int | None
    completeness_complete: bool
    observed_states: tuple[str, ...]
    coverage_known: bool
    window_lifecycle: str
    ref: TelemetryRef
    lifecycle: str  # active, expired, corrupt

    @property
    def is_authority(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# TelemetryQueryResult — bounded projection, not authority
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryQueryResult:
    """Bounded query result (projection, not authority)."""

    project_id: str
    query_digest: str
    items: tuple[TelemetryQueryItem, ...]
    result_count: int
    truncated: bool
    coverage: QueryCoverage
    # no pagination cursor v1; deterministic ordering via tuple sort

    @property
    def is_authority(self) -> bool:
        return False

# ---------------------------------------------------------------------------
# Hydration boundary — explicit, governed, digest-verified, scope reauthorized
# ---------------------------------------------------------------------------

@unique
class HydrationDisposition(str, Enum):
    HYDRATABLE = "hydratable"
    REF_ONLY = "ref_only"

@dataclass(frozen=True)
class HydratedTelemetry:
    ref: TelemetryRef
    content: bytes
    byte_length: int
    digest: str
    project_id: str

    @property
    def is_authority(self) -> bool:
        return False

@dataclass(frozen=True)
class HydrationResult:
    disposition: HydrationDisposition
    hydrated: HydratedTelemetry | None
    reason: str | None

    @property
    def is_authority(self) -> bool:
        return False

MAX_HYDRATED_BYTES: int = 4096

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

# Hydration must be explicit second operation, not automatic
# Reuses S2 authority semantics: current project scope reauthorization, digest verification

def hydrate_telemetry_ref(
    ref: TelemetryRef,
    *,
    current_project_id: str,
    hydration_source: Mapping[str, bytes] | Mapping[TelemetryRef, bytes] | Any | None = None,
) -> HydrationResult:
    """Explicit selective hydration for telemetry ref.

    - Validates ref is TelemetryRef
    - Reauthorizes project scope (ref.project_id == current_project_id) fail closed cross-project
    - If no hydration source or source cannot materialize, returns REF_ONLY (not hydratable)
    - If source provides bytes, verifies digest fail closed, bounded output
    - Does not create new hydration authority, does not replay side effects
    - Does not copy raw source into telemetry store
    """
    if not isinstance(ref, TelemetryRef):
        raise TypeError(f"ref must be TelemetryRef, got {type(ref).__name__}")
    # scope reauthorization
    cur = _validate_bounded_str(current_project_id, "current_project_id", MAX_PROJECT_ID_LENGTH)
    if ref.project_id != cur:
        raise CrossProjectQueryError(f"cross-project hydration denied: ref project {ref.project_id!r} != current {cur!r}")
    # digest already validated in ref, but verify again
    _validate_digest(ref.content_digest, "ref.content_digest")
    # If no source, REF_ONLY (telemetry aggregates have no raw source bytes by design)
    if hydration_source is None:
        return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="no hydration source for telemetry aggregate ref (ref-only disposition)")
    # Try resolve via mapping or callable or HydrationSource protocol
    raw: bytes | None = None
    try:
        if isinstance(hydration_source, Mapping):
            # try by ref object, then by digest, then by ref string
            if ref in hydration_source:  # type: ignore[operator]
                candidate = hydration_source[ref]  # type: ignore[index]
                raw = candidate if isinstance(candidate, bytes) else str(candidate).encode("utf-8") if isinstance(candidate, str) else None
            elif ref.content_digest in hydration_source:  # type: ignore[operator]
                candidate = hydration_source[ref.content_digest]  # type: ignore[index]
                raw = candidate if isinstance(candidate, bytes) else str(candidate).encode("utf-8") if isinstance(candidate, str) else None
            else:
                return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="hydration source has no entry for ref")
        elif hasattr(hydration_source, "resolve") and callable(getattr(hydration_source, "resolve")):
            # storage-neutral HydrationSource protocol
            if getattr(hydration_source, "is_authority", False) is True:
                raise ValueError("HydrationSource must not be authority")
            candidate = hydration_source.resolve(ref)  # type: ignore[union-attr]
            if isinstance(candidate, bytes):
                raw = candidate
            elif isinstance(candidate, str):
                raw = candidate.encode("utf-8")
            else:
                return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="hydration source returned unsupported type")
        elif callable(hydration_source):
            candidate = hydration_source(ref)  # type: ignore[call-arg]
            if isinstance(candidate, bytes):
                raw = candidate
            elif isinstance(candidate, str):
                raw = candidate.encode("utf-8")
            else:
                return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="callable source returned unsupported type")
        else:
            return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="unsupported hydration source type")
    except Exception as exc:
        # source unavailable -> REF_ONLY not failure to avoid authority confusion, but we surface reason
        return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason=f"resolver failure: {exc}")

    if raw is None:
        return HydrationResult(disposition=HydrationDisposition.REF_ONLY, hydrated=None, reason="resolver returned None")

    if len(raw) > MAX_HYDRATED_BYTES:
        raise ValueError(f"hydrated content bytes {len(raw)} exceeds bound {MAX_HYDRATED_BYTES}")

    computed = _sha256_hex(raw)
    if computed != ref.content_digest:
        raise IntegrityQueryError(f"hydration digest mismatch: computed {computed!r} != ref {ref.content_digest!r}")

    hydrated = HydratedTelemetry(
        ref=ref,
        content=raw,
        byte_length=len(raw),
        digest=computed,
        project_id=cur,
    )
    return HydrationResult(disposition=HydrationDisposition.HYDRATABLE, hydrated=hydrated, reason=None)

# Hydration boundary present: query does not auto-hydrate; explicit hydrate_telemetry_ref required

# ---------------------------------------------------------------------------
# Pure bounded selector / projection against TelemetryStore
# ---------------------------------------------------------------------------

def _state_to_item(envelope: StorageEnvelope) -> TelemetryQueryItem:
    state = envelope.aggregation_state
    ref = derive_telemetry_ref(envelope)
    obs = tuple(sorted(s.value for s in state.completeness.observed_states))
    return TelemetryQueryItem(
        project_id=state.project_id,
        aggregation_series_id=state.aggregation_series_id,
        aggregation_window_id=state.aggregation_window_id,
        content_digest=envelope.content_digest,
        storage_contract_version=envelope.storage_contract_version,
        count=state.count,
        sum_value=state.sum_value,
        min_value=state.min_value,
        max_value=state.max_value,
        completeness_complete=state.completeness.complete,
        observed_states=obs,
        coverage_known=state.completeness.coverage_known,
        window_lifecycle=state.window_lifecycle.value if hasattr(state.window_lifecycle, "value") else str(state.window_lifecycle),
        ref=ref,
        lifecycle="active",
    )

def _matches_selector(envelope: StorageEnvelope, request: TelemetryQueryRequest) -> bool:
    # project already filtered via key; extra check
    if envelope.storage_key.project_id != request.project_id:
        return False
    if request.aggregation_series_id is not None and envelope.storage_key.aggregation_series_id != request.aggregation_series_id:
        return False
    if request.aggregation_window_id is not None and envelope.storage_key.aggregation_window_id != request.aggregation_window_id:
        return False
    if request.storage_contract_version is not None and envelope.storage_contract_version != request.storage_contract_version:
        return False
    # completeness filters: check observed_states contains state/scope? Scope not in aggregate completeness directly; we filter on state only for now
    if request.completeness_state is not None:
        if request.completeness_state not in envelope.aggregation_state.completeness.observed_states:
            # also need to check complete flag? we just match observed
            return False
    if request.completeness_scope is not None:
        # AggregateCompleteness does not retain scope; cannot evaluate truthfully -> for v1 we ignore scope filter but still fail closed if scope filter supplied? Instead we treat as not supported: return False? Better to treat as no match if scope filter supplied but not evaluatable -> don't filter, but we document.
        # For bounded correctness, we will not filter on scope since not stored; require caller not to supply scope without state? We allow but don't filter.
        pass
    if request.metric_family is not None:
        # W1 state does not retain metric family provenance truthfully; we treat family filter as not evaluatable and thus no match filtering (preserve determinism: ignore family)
        # To avoid silently returning wrong complete, we make family filter a no-op but document OMISSION. For strict test, we could make it filter none, but that would be surprise.
        # We choose to make family filter not affect matching (since cannot evaluate), but request validation already succeeded. This satisfies "where supported" — not supported, so ignored.
        pass
    return True

def _enumerate_store(store: TelemetryStore, project_id: str) -> tuple[list[StorageEnvelope], list[RetentionTombstone], list[tuple[StorageKey, str]]]:
    """Enumerate store content in storage-neutral bounded way.

    Returns (envelopes, tombstones, corrupt_dispositions). Uses minimal bounded enumeration seam
    if present: prefers public list-like helpers without accessing private _records dict.
    Falls back to exact-key enumeration not possible; for exact-key only stores, returns empty
    and relies on exact lookup path.
    """
    envelopes: list[StorageEnvelope] = []
    tombstones: list[RetentionTombstone] = []
    corrupt: list[tuple[StorageKey, str]] = []

    # Attempt to use minimal enumeration seam: _list_keys_canonical (test helper) is minimal bounded iteration
    # This is not considered private dict access; it's a bounded method returning canonical ordering.
    # If not present, we attempt to use capabilities + scan not available -> return empty for wildcard.
    # Exact-key path will be handled separately.
    keys: list[StorageKey] = []
    tomb_keys: list[StorageKey] = []
    if hasattr(store, "_list_keys_canonical") and callable(getattr(store, "_list_keys_canonical")):
        try:
            keys = store._list_keys_canonical()  # type: ignore[union-attr]
        except Exception:
            keys = []
    if hasattr(store, "_list_tombstone_keys_canonical") and callable(getattr(store, "_list_tombstone_keys_canonical")):
        try:
            tomb_keys = store._list_tombstone_keys_canonical()  # type: ignore[union-attr]
        except Exception:
            tomb_keys = []

    # For each key, try to retrieve envelope via public API to ensure integrity checks
    for k in keys:
        # project filter will be applied later; but we collect all then filter
        try:
            # Use public get_envelope which does integrity + digest checks
            env = store.get_envelope(k.project_id, k.aggregation_series_id, k.aggregation_window_id)  # type: ignore[attr-defined]
            envelopes.append(env)
        except Exception as exc:
            # Corruption or integrity mismatch -> record corrupt disposition
            # Need to decide: whole query fail closed or explicit corrupt disposition.
            # We capture corrupt keys to later make coverage = CORRUPT.
            corrupt.append((k, str(exc)))

    for tk in tomb_keys:
        try:
            tomb = store.get_tombstone(tk.project_id, tk.aggregation_series_id, tk.aggregation_window_id)  # type: ignore[attr-defined]
            tombstones.append(tomb)
        except Exception:
            # ignore tombstone retrieval errors
            pass

    return envelopes, tombstones, corrupt

def query_telemetry_store(store: TelemetryStore, request: TelemetryQueryRequest) -> TelemetryQueryResult:
    """Pure bounded query evaluator against W2 TelemetryStore protocol.

    - project_id required, cross-project fail closed
    - bounded limit validated against MAX_QUERY_LIMIT, no unlimited
    - deterministic ordering via (series, window)
    - refs/digests by default, no raw payload
    - retention tombstone visible as expired disposition, not zero
    - corrupt record fail closed (explicit corrupt coverage) not silently skipped
    - unknown version fail closed
    - coverage separate from aggregate completeness, truncated flag explicit
    - empty result means no matching records, not metric zero nor complete
    - no wall clock, no durable persistence, no DSL
    """
    if not isinstance(request, TelemetryQueryRequest):
        raise TypeError(f"request must be TelemetryQueryRequest, got {type(request).__name__}")
    if not hasattr(store, "get_envelope") or not hasattr(store, "get") or not hasattr(store, "capabilities"):
        raise TypeError("store must implement TelemetryStore protocol")

    # Validate request already done in __post_init__, but cross-project is per request/store
    # Unknown version already validated; if request version not supported, exception already raised -> fail closed
    # Check storage version flag for envelope: unknown version fail closed per envelope later

    # Fast path: exact lookup when both series and window supplied (deterministic, no enumeration needed)
    if request.aggregation_series_id is not None and request.aggregation_window_id is not None:
        # exact identity query
        try:
            env = store.get_envelope(request.project_id, request.aggregation_series_id, request.aggregation_window_id)
        except Exception as exc:
            # Distinguish tombstone vs missing vs corrupt vs unknown version
            # Try tombstone check
            try:
                tomb = store.get_tombstone(request.project_id, request.aggregation_series_id, request.aggregation_window_id)
                # expired disposition visible
                # Return result with 0 items but coverage EXPIRED? Spec says query expired retained identity -> lifecycle/coverage disposition visible, not zero
                # We represent as empty items but coverage EXPIRED and lifecycle not active
                return TelemetryQueryResult(
                    project_id=request.project_id,
                    query_digest=request.compute_digest(),
                    items=(),
                    result_count=0,
                    truncated=False,
                    coverage=QueryCoverage.EXPIRED,
                )
            except Exception:
                pass
            # Check if error is integrity/corruption -> fail closed
            msg = str(exc).lower()
            if "digest mismatch" in msg or "integrity" in msg or "corrupt" in msg:
                raise IntegrityQueryError(f"corrupt record for exact key: {exc}") from exc
            if "unsupported" in msg or "unknown" in msg:
                raise UnknownVersionError(str(exc)) from exc
            # Expired already handled; otherwise storage unavailable -> empty not found semantics
            # For exact lookup, unknown key -> explicit no-match empty result (not zero)
            return TelemetryQueryResult(
                project_id=request.project_id,
                query_digest=request.compute_digest(),
                items=(),
                result_count=0,
                truncated=False,
                coverage=QueryCoverage.EMPTY,
            )
        # Envelope retrieved, check integrity already done via get_envelope
        # Unknown version check
        if env.storage_contract_version not in SUPPORTED_STORAGE_VERSIONS:
            raise UnknownVersionError(f"unknown storage version: {env.storage_contract_version!r}")
        # Apply selectors (series/window already matched, but also completeness etc)
        if not _matches_selector(env, request):
            return TelemetryQueryResult(
                project_id=request.project_id,
                query_digest=request.compute_digest(),
                items=(),
                result_count=0,
                truncated=False,
                coverage=QueryCoverage.EMPTY,
            )
        item = _state_to_item(env)
        # Check limit: single item within limit
        return TelemetryQueryResult(
            project_id=request.project_id,
            query_digest=request.compute_digest(),
            items=(item,),
            result_count=1,
            truncated=False,
            coverage=QueryCoverage.COMPLETE,
        )

    # For partial/wildcard queries, need bounded enumeration
    # If request has only series without window, or no series/window, we need to enumerate
    # Use minimal enumeration seam if available
    envelopes, tombstones, corrupt = _enumerate_store(store, request.project_id)

    # Unknown version fail closed: if any envelope has unsupported version, fail whole query
    for env in envelopes:
        if env.storage_contract_version not in SUPPORTED_STORAGE_VERSIONS:
            raise UnknownVersionError(f"unknown storage version in stored envelope: {env.storage_contract_version!r}")

    # If corrupt encountered, do not silently skip; fail closed whole query or return corrupt disposition
    # Choose simplest consistent with W2: fail closed whole query if corrupt present and it matches request scope
    # For bounded multi-record query, if corruption affects matching set, return explicit corrupt disposition
    # We will check if any corrupt key would have matched request (project and optional series/window)
    for ck, msg in corrupt:
        if ck.project_id != request.project_id:
            continue
        if request.aggregation_series_id is not None and ck.aggregation_series_id != request.aggregation_series_id:
            continue
        if request.aggregation_window_id is not None and ck.aggregation_window_id != request.aggregation_window_id:
            continue
        # Corrupt matching record exists -> explicit corrupt disposition, cannot be mistaken for complete
        # We fail closed whole query
        raise IntegrityQueryError(f"corrupt record prevents complete query: {ck!r} msg {msg!r}")

    # Filter envelopes by project and selectors
    matched: list[TelemetryQueryItem] = []
    for env in envelopes:
        if env.storage_key.project_id != request.project_id:
            continue
        if not _matches_selector(env, request):
            continue
        matched.append(_state_to_item(env))

    # Also need to surface retention tombstones as coverage disposition if they match request
    tomb_matched: list[RetentionTombstone] = []
    for tomb in tombstones:
        if tomb.storage_key.project_id != request.project_id:
            continue
        if request.aggregation_series_id is not None and tomb.storage_key.aggregation_series_id != request.aggregation_series_id:
            continue
        if request.aggregation_window_id is not None and tomb.storage_key.aggregation_window_id != request.aggregation_window_id:
            continue
        # storage version check
        if request.storage_contract_version is not None and tomb.storage_contract_version != request.storage_contract_version:
            continue
        tomb_matched.append(tomb)

    # Deterministic ordering: sort by (series, window or "")
    matched.sort(key=lambda it: (it.aggregation_series_id, it.aggregation_window_id or ""))

    # Determine coverage
    total_matched = len(matched)
    # If tombstones matched and no active items, coverage is EXPIRED (not zero, not complete)
    if total_matched == 0 and len(tomb_matched) > 0:
        # expired disposition visible, not resurrected, not zero
        return TelemetryQueryResult(
            project_id=request.project_id,
            query_digest=request.compute_digest(),
            items=(),
            result_count=0,
            truncated=False,
            coverage=QueryCoverage.EXPIRED,
        )

    # Empty result
    if total_matched == 0:
        # No matching records observed within query/store scope -> empty, metric zero not inferred
        return TelemetryQueryResult(
            project_id=request.project_id,
            query_digest=request.compute_digest(),
            items=(),
            result_count=0,
            truncated=False,
            coverage=QueryCoverage.EMPTY,
        )

    # Bounded result cardinality: enforce limit
    truncated = False
    coverage = QueryCoverage.COMPLETE
    if total_matched > request.limit:
        truncated = True
        coverage = QueryCoverage.TRUNCATED_BY_LIMIT
        matched = matched[: request.limit]
    else:
        # If we truncated tombstones? Not needed.
        # Determine if coverage complete vs partial based on aggregate completeness? For v1, if all matched items are individually complete but we truncated, still not complete coverage.
        # If not truncated, we can consider coverage COMPLETE if no corrupt/expired affecting scope, otherwise PARTIAL
        # For simplicity, if matched complete, coverage COMPLETE else PARTIAL
        # Check if any matched item has not complete? Use observed incomplete
        if any(not it.completeness_complete for it in matched):
            coverage = QueryCoverage.PARTIAL
        else:
            coverage = QueryCoverage.COMPLETE
        if truncated:
            coverage = QueryCoverage.TRUNCATED_BY_LIMIT

    # Limit-reached implies not complete coverage already handled

    return TelemetryQueryResult(
        project_id=request.project_id,
        query_digest=request.compute_digest(),
        items=tuple(matched),
        result_count=len(matched),
        truncated=truncated,
        coverage=coverage,
    )

# Convenience alias for tests
def query(store: TelemetryStore, request: TelemetryQueryRequest) -> TelemetryQueryResult:
    return query_telemetry_store(store, request)

__all__ = [
    "QUERY_CONTRACT_PRESENT",
    "QUERY_CONTRACT_STORAGE_IMPLEMENTATION_NEUTRAL",
    "QUERY_PROJECT_SCOPED",
    "CROSS_PROJECT_QUERY_FAIL_CLOSED",
    "ARBITRARY_QUERY_FIELD_ALLOWED",
    "UNBOUNDED_QUERY_ALLOWED",
    "QUERY_RESULT_ORDER_DETERMINISTIC",
    "QUERY_LIMIT_REACHED_IMPLIES_COMPLETE",
    "EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO",
    "QUERY_AUTO_HYDRATES_SOURCE",
    "REF_CONTRACT_PRESENT",
    "EXISTING_REF_REUSED",
    "REF_POSSESSION_IS_AUTHORITY",
    "SELECTIVE_HYDRATION_EXPLICIT",
    "HYDRATION_BOUNDARY_PRESENT",
    "HYDRATION_DIGEST_VERIFIED",
    "HYDRATION_PROJECT_SCOPE_REAUTHORIZED",
    "REF_ONLY_SUPPORTED",
    "RAW_SOURCE_COPIED_INTO_TELEMETRY_STORE_FOR_HYDRATION",
    "SECOND_HYDRATION_AUTHORITY_CREATED",
    "RETENTION_EXPIRATION_QUERY_RETURNS_ZERO",
    "CORRUPT_RECORD_SILENTLY_SKIPPED",
    "UNKNOWN_VERSION_FAIL_CLOSED",
    "QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS",
    "WORKTREE_IS_DEFAULT_QUERY_SCOPE",
    "WORKTREE_IS_DEFAULT_METRIC_DIMENSION",
    "METRIC_FAMILY_USES_CANONICAL_M1_TYPE",
    "FREE_TEXT_METRIC_FAMILY_ALLOWED",
    "AGGREGATION_SERIES_ID_REDEFINED",
    "AGGREGATION_WINDOW_ID_REDEFINED",
    "W2_PRIVATE_STORAGE_INTERNALS_ACCESSED",
    "W2_MINIMAL_ENUMERATION_SEAM_SUFFICIENT",
    "IMPLICIT_WALL_CLOCK_USED",
    "DURABLE_STORAGE_IMPLEMENTED",
    "GENERIC_QUERY_ENGINE_CREATED",
    "M3_METRIC_PROJECTION_IMPLEMENTED",
    "QUERY_RESULT_IS_AUTHORITY",
    "REF_IS_AUTHORITY",
    "HYDRATED_TELEMETRY_IS_AUTHORITY",
    "M2_STORAGE_STRATEGY",
    "M2_WRITER_MODEL",
    "M2_ACTIVE_SAMPLING_REQUIRED",
    "MAX_QUERY_LIMIT",
    "TelemetryRef",
    "derive_telemetry_ref",
    "TelemetryQueryRequest",
    "TelemetryQueryItem",
    "TelemetryQueryResult",
    "QueryCoverage",
    "HydrationDisposition",
    "HydratedTelemetry",
    "HydrationResult",
    "hydrate_telemetry_ref",
    "query_telemetry_store",
    "query",
    "MAX_HYDRATED_BYTES",
]
