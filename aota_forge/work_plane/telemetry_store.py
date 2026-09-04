"""Telemetry Storage Boundary & Lifecycle Foundation — S6 M2 W2.

Storage-neutral seam around accepted W1 AggregationState.

W1 AggregationState
      -> TelemetryStore protocol
      -> EphemeralTelemetryStore (in-memory v1)

No durable engine, no query DSL, no hydration, no filesystem/DB/network,
no background worker, no TTL scheduler. All retention is explicit
caller-driven. Capability indicates ephemeral with restart_safe=False.
"""

from __future__ import annotations

import hashlib
import re
import copy
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

from aota_forge.work_plane.telemetry_evidence import (
    RetentionProvenance,
    TELEMETRY_EVIDENCE_CONTRACT_VERSION,
    SUPPORTED_ENVELOPE_VERSIONS,
)
from aota_forge.work_plane.telemetry_metrics import (
    METRIC_TAXONOMY_VERSION,
    DIMENSION_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
)
from aota_forge.work_plane.telemetry_aggregation import (
    AggregationState,
    AggregateCompleteness,
    WindowLifecycle,
)

# ---------------------------------------------------------------------------
# Version and capability boundary
# ---------------------------------------------------------------------------

STORAGE_CONTRACT_VERSION: str = "s6-m2-v1"
SUPPORTED_STORAGE_VERSIONS: frozenset[str] = frozenset({STORAGE_CONTRACT_VERSION})

# Provenance version exposure (distinct, not merged)
TELEMETRY_EVIDENCE_VERSION_EXPOSED: str = TELEMETRY_EVIDENCE_CONTRACT_VERSION
METRIC_TAXONOMY_VERSION_EXPOSED: str = METRIC_TAXONOMY_VERSION
DIMENSION_SCHEMA_VERSION_EXPOSED: str = DIMENSION_SCHEMA_VERSION
NORMALIZATION_VERSION_EXPOSED: str = NORMALIZATION_VERSION

# Flags observable for negative architecture proof
STORAGE_NEUTRAL_TRANSITIONS: bool = True
STORAGE_PROTOCOL_STORAGE_NEUTRAL: bool = True
W1_AGGREGATION_STATE_REDEFINED: bool = False
SOURCE_DEDUP_ID_REDEFINED: bool = False
PROJECTION_ID_REDEFINED: bool = False
AGGREGATION_SERIES_ID_REDEFINED: bool = False
AGGREGATION_WINDOW_ID_REDEFINED: bool = False
DURABLE_STORAGE_IMPLEMENTED: bool = False
STORAGE_ENGINE_SELECTED: bool = False
FILESYSTEM_IO_CREATED: bool = False
DATABASE_CREATED: bool = False
BACKGROUND_WORKER_CREATED: bool = False
TTL_RUNTIME_CREATED: bool = False
QUERY_LAYER_CREATED: bool = False
W3_QUERY_CONTRACT_PREEMPTED: bool = False
SELECTIVE_HYDRATION_IMPLEMENTED: bool = False
SECOND_HYDRATION_AUTHORITY_CREATED: bool = False
NEW_EXECUTION_JOURNAL_CREATED: bool = False
SECOND_RESULT_STORE_CREATED: bool = False
NEW_WORKFLOW_STATE_MACHINE_CREATED: bool = False

M2_STORAGE_STRATEGY: str = "STORAGE_NEUTRAL_WITH_EPHEMERAL_DEFAULT"
M2_WRITER_MODEL: str = "single_writer"

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_PROJECT_ID_LENGTH: int = 96
MAX_SERIES_ID_LENGTH: int = 128
MAX_WINDOW_ID_LENGTH: int = 128
MAX_RETENTION_POLICY_ID_LENGTH: int = 128
MAX_RETENTION_POLICY_VERSION_LENGTH: int = 64
MAX_STORAGE_VERSION_LENGTH: int = 32

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

MAX_RECORDS_LIMIT: int = 10000
MAX_PROJECTS_LIMIT: int = 1000
MAX_TOMBSTONES_LIMIT: int = 10000

# ---------------------------------------------------------------------------
# Validation helpers (no wall clock, no filesystem)
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

def _validate_storage_version(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"storage_contract_version must be string, got {type(value).__name__}")
    v = value.strip()
    if v not in SUPPORTED_STORAGE_VERSIONS:
        raise ValueError(f"Unsupported storage schema version: {v!r}. Supported: {sorted(SUPPORTED_STORAGE_VERSIONS)}")
    return v

# ---------------------------------------------------------------------------
# Storage key — derived from W1 identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StorageKey:
    project_id: str
    aggregation_series_id: str
    aggregation_window_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "aggregation_series_id", _validate_digest(self.aggregation_series_id, "aggregation_series_id"))
        if self.aggregation_window_id is not None:
            object.__setattr__(self, "aggregation_window_id", _validate_digest(self.aggregation_window_id, "aggregation_window_id"))

    def canonical_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "project_id": self.project_id,
            "aggregation_series_id": self.aggregation_series_id,
        }
        if self.aggregation_window_id is not None:
            d["aggregation_window_id"] = self.aggregation_window_id
        return canonicalize(d, path="StorageKey")  # type: ignore[return-value]

def derive_storage_key(state: AggregationState) -> StorageKey:
    if not isinstance(state, AggregationState):
        raise TypeError(f"state must be AggregationState, got {type(state).__name__}")
    return StorageKey(
        project_id=state.project_id,
        aggregation_series_id=state.aggregation_series_id,
        aggregation_window_id=state.aggregation_window_id,
    )

# ---------------------------------------------------------------------------
# Digest helper for integrity (not source identity)
# ---------------------------------------------------------------------------

def _state_canonical_dict(state: AggregationState) -> dict[str, object]:
    """Bounded canonical serialization for digest (without raw payload)."""
    # Build deterministic representation from W1 state fields
    d: dict[str, object] = {
        "aggregation_series_id": state.aggregation_series_id,
        "aggregation_window_id": state.aggregation_window_id,
        "completeness": {
            "complete": state.completeness.complete,
            "coverage_known": state.completeness.coverage_known,
            "observed_states": sorted(state.completeness.observed_states.__class__.__name__ if False else [s.value for s in state.completeness.observed_states]),  # type: ignore
        },
        "count": state.count,
        "max_value": state.max_value,
        "min_value": state.min_value,
        "project_id": state.project_id,
        "seen_projection_ids": sorted(state.seen_projection_ids),
        "seen_source_dedup_ids": sorted(state.seen_source_dedup_ids),
        "sum_value": state.sum_value,
        "window_lifecycle": state.window_lifecycle.value if isinstance(state.window_lifecycle, WindowLifecycle) else str(state.window_lifecycle),
        "window_policy_id": state.window_policy_id,
        "window_policy_version": state.window_policy_version,
        "max_order_per_domain": {k: state.max_order_per_domain[k] for k in sorted(state.max_order_per_domain)},
    }
    # Fix observed_states canonical: need string values deterministic
    # Use stored completeness observed set directly
    # Rebuild correctly
    observed = sorted(s.value for s in state.completeness.observed_states)
    d2: dict[str, object] = {
        "aggregation_series_id": state.aggregation_series_id,
        "aggregation_window_id": state.aggregation_window_id,
        "completeness": {
            "complete": state.completeness.complete,
            "coverage_known": state.completeness.coverage_known,
            "observed_states": observed,
        },
        "count": state.count,
        "max_value": state.max_value,
        "min_value": state.min_value,
        "project_id": state.project_id,
        "seen_projection_ids": sorted(state.seen_projection_ids),
        "seen_source_dedup_ids": sorted(state.seen_source_dedup_ids),
        "sum_value": state.sum_value,
        "window_lifecycle": state.window_lifecycle.value if isinstance(state.window_lifecycle, WindowLifecycle) else str(state.window_lifecycle),
        "window_policy_id": state.window_policy_id,
        "window_policy_version": state.window_policy_version,
        "max_order_per_domain": {k: state.max_order_per_domain[k] for k in sorted(state.max_order_per_domain)},
    }
    return canonicalize(d2, path="AggregationStateDigest")  # type: ignore[return-value]

def compute_state_digest(state: AggregationState) -> str:
    canonical = _state_canonical_dict(state)
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

def _copy_state(state: AggregationState) -> AggregationState:
    """Defensive copy for alias safety. AggregationState is frozen but contains dict."""
    # Since frozen, we reconstruct with defensive copies
    return AggregationState(
        project_id=state.project_id,
        aggregation_series_id=state.aggregation_series_id,
        aggregation_window_id=state.aggregation_window_id,
        window_policy_id=state.window_policy_id,
        window_policy_version=state.window_policy_version,
        window_lifecycle=state.window_lifecycle,
        count=state.count,
        sum_value=state.sum_value,
        min_value=state.min_value,
        max_value=state.max_value,
        seen_projection_ids=frozenset(state.seen_projection_ids),
        seen_source_dedup_ids=frozenset(state.seen_source_dedup_ids),
        completeness=AggregateCompleteness(
            complete=state.completeness.complete,
            observed_states=frozenset(state.completeness.observed_states),
            coverage_known=state.completeness.coverage_known,
        ),
        max_order_per_domain=dict(state.max_order_per_domain),
    )

# ---------------------------------------------------------------------------
# Storage envelope — thin, immutable, bounded
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StorageEnvelope:
    storage_contract_version: str
    storage_key: StorageKey
    aggregation_state: AggregationState
    content_digest: str
    # bounded last replacement provenance (prior digest)
    prior_content_digest: str | None = None
    # retention provenance if applicable at store time? Usually none for active.
    # storage lifecycle provenance
    storage_lifecycle: str = "active"  # active

    def __post_init__(self) -> None:
        object.__setattr__(self, "storage_contract_version", _validate_storage_version(self.storage_contract_version))
        if not isinstance(self.storage_key, StorageKey):
            raise TypeError(f"storage_key must be StorageKey, got {type(self.storage_key).__name__}")
        if not isinstance(self.aggregation_state, AggregationState):
            raise TypeError(f"aggregation_state must be AggregationState, got {type(self.aggregation_state).__name__}")
        object.__setattr__(self, "content_digest", _validate_digest(self.content_digest, "content_digest"))
        if self.prior_content_digest is not None:
            object.__setattr__(self, "prior_content_digest", _validate_digest(self.prior_content_digest, "prior_content_digest"))
        # Verify key/state agreement
        derived = derive_storage_key(self.aggregation_state)
        if derived != self.storage_key:
            raise ValueError(f"key/state identity disagreement: key {self.storage_key!r} != derived {derived!r}")
        # Verify project scope
        if self.storage_key.project_id != self.aggregation_state.project_id:
            raise ValueError(f"project scope mismatch: key project {self.storage_key.project_id!r} != state project {self.aggregation_state.project_id!r}")
        # Verify digest matches state
        expected = compute_state_digest(self.aggregation_state)
        if self.content_digest != expected:
            raise ValueError(f"digest mismatch: expected {expected!r}, got {self.content_digest!r}")
        if self.storage_lifecycle not in ("active",):
            raise ValueError(f"storage_lifecycle must be 'active', got {self.storage_lifecycle!r}")
        # Defensive copy already done via frozen state? Ensure stored state's copy isolated later

    def canonical_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "storage_contract_version": self.storage_contract_version,
            "storage_key": self.storage_key.canonical_dict(),
            "aggregation_state": _state_canonical_dict(self.aggregation_state),
            "content_digest": self.content_digest,
            "storage_lifecycle": self.storage_lifecycle,
        }
        if self.prior_content_digest is not None:
            d["prior_content_digest"] = self.prior_content_digest
        return canonicalize(d, path="StorageEnvelope")  # type: ignore[return-value]

# ---------------------------------------------------------------------------
# Retention tombstone — bounded, non-authoritative
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetentionTombstone:
    storage_contract_version: str
    storage_key: StorageKey
    retention_policy_id: str
    retention_policy_version: str
    retention_class: str
    prior_content_digest: str
    expired_disposition: str = "expired"

    def __post_init__(self) -> None:
        object.__setattr__(self, "storage_contract_version", _validate_storage_version(self.storage_contract_version))
        if not isinstance(self.storage_key, StorageKey):
            raise TypeError(f"storage_key must be StorageKey, got {type(self.storage_key).__name__}")
        object.__setattr__(self, "retention_policy_id", _validate_bounded_str(self.retention_policy_id, "retention_policy_id", MAX_RETENTION_POLICY_ID_LENGTH))
        object.__setattr__(self, "retention_policy_version", _validate_bounded_str(self.retention_policy_version, "retention_policy_version", MAX_RETENTION_POLICY_VERSION_LENGTH))
        object.__setattr__(self, "retention_class", _validate_bounded_str(self.retention_class, "retention_class", 32))
        object.__setattr__(self, "prior_content_digest", _validate_digest(self.prior_content_digest, "prior_content_digest"))
        if self.expired_disposition != "expired":
            raise ValueError(f"expired_disposition must be 'expired', got {self.expired_disposition!r}")

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoreCapabilities:
    storage_contract_version: str
    is_ephemeral: bool
    is_durable: bool
    is_restart_safe: bool
    max_records: int
    max_projects: int
    max_tombstones: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "storage_contract_version", _validate_storage_version(self.storage_contract_version))
        if not isinstance(self.is_ephemeral, bool) or not isinstance(self.is_durable, bool) or not isinstance(self.is_restart_safe, bool):
            raise TypeError("capability flags must be bool")

# ---------------------------------------------------------------------------
# Typed error model — bounded, no raw payload
# ---------------------------------------------------------------------------

class StorageError(Exception):
    """Base bounded telemetry storage error."""

class UnsupportedSchemaError(StorageError):
    pass

class ScopeMismatchError(StorageError):
    pass

class IntegrityMismatchError(StorageError):
    pass

class CapacityExceededError(StorageError):
    pass

class ExpiredError(StorageError):
    pass

class StorageUnavailableError(StorageError):
    pass

class MalformedKeyError(StorageError):
    pass

# ---------------------------------------------------------------------------
# Protocol — narrow, typing.Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class TelemetryStore(Protocol):
    @property
    def storage_contract_version(self) -> str:
        ...

    @property
    def capabilities(self) -> StoreCapabilities:
        ...

    def put(self, state: AggregationState) -> StorageEnvelope:
        """Store validated W1 aggregation state under derived key. Idempotent for same content; conflicting content fails closed."""
        ...

    def get(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> AggregationState:
        """Retrieve exact stored state by key. Fail closed on cross-project, expired, missing, or corruption."""
        ...

    def get_envelope(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> StorageEnvelope:
        """Inspect bounded lifecycle/integrity envelope (for tests)."""
        ...

    def expire(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None, retention_provenance: RetentionProvenance) -> RetentionTombstone:
        """Explicit caller-driven retention expiration. Removes payload, retains bounded tombstone."""
        ...

    def put_reprocessed(self, new_state: AggregationState) -> StorageEnvelope:
        """Explicit validated telemetry reprocessing replacement. Same project scope, valid identity, new digest."""
        ...

    def get_tombstone(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> RetentionTombstone:
        ...

# ---------------------------------------------------------------------------
# Ephemeral implementation — bounded in-memory v1, single_writer
# ---------------------------------------------------------------------------

class EphemeralTelemetryStore:
    """Bounded in-memory v1 adapter. Deterministic under single_writer."""

    def __init__(
        self,
        max_records: int = 64,
        max_projects: int = 8,
        max_tombstones: int = 64,
    ) -> None:
        # Caller-supplied validated limits, not globally frozen arbitrary constants
        if not isinstance(max_records, int) or type(max_records) is not int or isinstance(max_records, bool):
            raise TypeError("max_records must be int")
        if not isinstance(max_projects, int) or type(max_projects) is not int or isinstance(max_projects, bool):
            raise TypeError("max_projects must be int")
        if not isinstance(max_tombstones, int) or type(max_tombstones) is not int or isinstance(max_tombstones, bool):
            raise TypeError("max_tombstones must be int")
        if not (1 <= max_records <= MAX_RECORDS_LIMIT):
            raise ValueError(f"max_records must be in [1,{MAX_RECORDS_LIMIT}], got {max_records}")
        if not (1 <= max_projects <= MAX_PROJECTS_LIMIT):
            raise ValueError(f"max_projects must be in [1,{MAX_PROJECTS_LIMIT}], got {max_projects}")
        if not (1 <= max_tombstones <= MAX_TOMBSTONES_LIMIT):
            raise ValueError(f"max_tombstones must be in [1,{MAX_TOMBSTONES_LIMIT}], got {max_tombstones}")
        self._max_records: int = max_records
        self._max_projects: int = max_projects
        self._max_tombstones: int = max_tombstones
        self._store: dict[StorageKey, StorageEnvelope] = {}
        self._tombstones: dict[StorageKey, RetentionTombstone] = {}
        self._storage_contract_version: str = STORAGE_CONTRACT_VERSION
        self._capabilities = StoreCapabilities(
            storage_contract_version=self._storage_contract_version,
            is_ephemeral=True,
            is_durable=False,
            is_restart_safe=False,
            max_records=self._max_records,
            max_projects=self._max_projects,
            max_tombstones=self._max_tombstones,
        )

    @property
    def storage_contract_version(self) -> str:
        return self._storage_contract_version

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    # -----------------------------------------------------------------------
    # internal validation helpers
    # -----------------------------------------------------------------------

    def _validate_key_params(self, project_id: str, series_id: str, window_id: str | None) -> StorageKey:
        try:
            return StorageKey(project_id=project_id, aggregation_series_id=series_id, aggregation_window_id=window_id)
        except (TypeError, ValueError) as exc:
            raise MalformedKeyError(str(exc)) from exc

    def _check_integrity(self, envelope: StorageEnvelope) -> None:
        # Re-verify digest and version and key/state agreement (fail closed)
        try:
            _validate_storage_version(envelope.storage_contract_version)
        except Exception as exc:
            raise UnsupportedSchemaError(str(exc)) from exc
        # key/state agreement already in envelope __post_init__, but double-check after potential tampering
        derived = derive_storage_key(envelope.aggregation_state)
        if derived != envelope.storage_key:
            raise IntegrityMismatchError(f"key/state identity disagreement: {envelope.storage_key!r} != {derived!r}")
        if envelope.storage_key.project_id != envelope.aggregation_state.project_id:
            raise IntegrityMismatchError("project binding mismatch")
        expected = compute_state_digest(envelope.aggregation_state)
        if envelope.content_digest != expected:
            raise IntegrityMismatchError(f"digest mismatch: expected {expected!r}, got {envelope.content_digest!r}")

    def _project_count_after(self, new_key: StorageKey | None = None) -> int:
        projects = set(k.project_id for k in self._store.keys())
        if new_key is not None and new_key.project_id not in projects:
            projects.add(new_key.project_id)
        return len(projects)

    # -----------------------------------------------------------------------
    # public API
    # -----------------------------------------------------------------------

    def put(self, state: AggregationState) -> StorageEnvelope:
        if not isinstance(state, AggregationState):
            raise IntegrityMismatchError(f"state must be AggregationState, got {type(state).__name__}")
        # Validate state already via its own __post_init__; derive key
        try:
            key = derive_storage_key(state)
        except Exception as exc:
            raise MalformedKeyError(str(exc)) from exc
        # Compute digest
        digest = compute_state_digest(state)
        # Copy for alias safety
        state_copy = _copy_state(state)

        # Check existing
        existing = self._store.get(key)
        if existing is not None:
            # integrity check existing before comparison
            try:
                self._check_integrity(existing)
            except StorageError as exc:
                raise IntegrityMismatchError(f"existing record corrupted: {exc}") from exc
            if existing.content_digest == digest:
                # Idempotent duplicate exact write — deterministic unchanged
                # Ensure semantic equality beyond digest? digest already deterministic equality
                return existing
            # Conflicting write without explicit reprocessing -> fail closed
            raise IntegrityMismatchError(f"conflicting write for key {key!r}: existing digest {existing.content_digest!r} != new {digest!r}. Use put_reprocessed for authorized replacement.")
        # Check tombstone? If key was expired, put should allow recreation by removing tombstone? For v1, allow replacement after expiration if caller puts new state
        # But if expired, we require explicit handling: allow put to overwrite tombstone (since caller explicitly puts)
        # However if tombstone exists, we remove it before put (bounded)
        was_tombstone = key in self._tombstones

        # Capacity checks (explicit, fail closed, no silent eviction)
        if len(self._store) >= self._max_records and not was_tombstone:
            # If not already containing key, capacity full
            raise CapacityExceededError(f"capacity exceeded: max_records {self._max_records} reached")
        # Also check tombstone capacity not relevant for put (but expiration already bounded)
        # Project partitions bound
        proj_after = self._project_count_after(key)
        if proj_after > self._max_projects:
            raise CapacityExceededError(f"capacity exceeded: max_projects {self._max_projects} would be exceeded by project {key.project_id!r}")

        # Atomicity: validation succeeded, now prepare envelope and publish copy-before-publish
        # No filesystem, so copy-before-publish is dict replacement

        # Determine prior digest if replacing after expiration? Use tombstone's prior if present
        prior: str | None = None
        if was_tombstone:
            # Use tombstone prior for provenance? But new write not reprocessed, so no prior linking
            prior = None

        envelope = StorageEnvelope(
            storage_contract_version=self._storage_contract_version,
            storage_key=key,
            aggregation_state=state_copy,
            content_digest=digest,
            prior_content_digest=prior,
            storage_lifecycle="active",
        )
        # Verify envelope integrity before publishing
        self._check_integrity(envelope)

        # Publish atomically (single_writer, so direct assignment is atomic for readers)
        # Use copy of dict to simulate copy-before-publish
        new_store = dict(self._store)
        new_store[key] = envelope
        self._store = new_store
        # If was tombstone, remove tombstone (expired replaced)
        if was_tombstone:
            new_tomb = dict(self._tombstones)
            del new_tomb[key]
            self._tombstones = new_tomb
        return envelope

    def get(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> AggregationState:
        key = self._validate_key_params(project_id, aggregation_series_id, aggregation_window_id)
        # Check tombstone first: expired -> ExpiredError (not zero)
        if key in self._tombstones:
            raise ExpiredError(f"record expired for key {key!r}")
        envelope = self._store.get(key)
        if envelope is None:
            raise StorageUnavailableError(f"record not found for key {key!r}")
        # Corruption checks
        try:
            self._check_integrity(envelope)
        except StorageError as exc:
            raise IntegrityMismatchError(str(exc)) from exc
        # Verify project binding matches request (already via key)
        if envelope.aggregation_state.project_id != project_id:
            raise ScopeMismatchError(f"project scope mismatch: requested {project_id!r} != stored {envelope.aggregation_state.project_id!r}")
        # Alias safety: return defensive copy
        return _copy_state(envelope.aggregation_state)

    def get_envelope(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> StorageEnvelope:
        key = self._validate_key_params(project_id, aggregation_series_id, aggregation_window_id)
        if key in self._tombstones:
            raise ExpiredError(f"record expired for key {key!r}")
        envelope = self._store.get(key)
        if envelope is None:
            raise StorageUnavailableError(f"record not found for key {key!r}")
        self._check_integrity(envelope)
        # Return copy with defensive state copy (envelope is frozen but contains mutable dict inside state)
        # Reconstruct envelope with copied state to avoid alias mutation of max_order dict
        state_copy = _copy_state(envelope.aggregation_state)
        # Create new envelope with same digest (will validate)
        return StorageEnvelope(
            storage_contract_version=envelope.storage_contract_version,
            storage_key=envelope.storage_key,
            aggregation_state=state_copy,
            content_digest=envelope.content_digest,
            prior_content_digest=envelope.prior_content_digest,
            storage_lifecycle=envelope.storage_lifecycle,
        )

    def expire(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None, retention_provenance: RetentionProvenance) -> RetentionTombstone:
        if not isinstance(retention_provenance, RetentionProvenance):
            raise IntegrityMismatchError(f"retention_provenance must be RetentionProvenance, got {type(retention_provenance).__name__}")
        key = self._validate_key_params(project_id, aggregation_series_id, aggregation_window_id)
        envelope = self._store.get(key)
        if envelope is None:
            # If already tombstone, idempotent?
            if key in self._tombstones:
                return self._tombstones[key]
            raise StorageUnavailableError(f"cannot expire missing record for key {key!r}")
        # Verify integrity before removing
        self._check_integrity(envelope)
        # Verify project scope matches
        if envelope.aggregation_state.project_id != project_id:
            raise ScopeMismatchError(f"project scope mismatch on expire: {project_id!r} != {envelope.aggregation_state.project_id!r}")
        # Check tombstone capacity
        if len(self._tombstones) >= self._max_tombstones and key not in self._tombstones:
            raise CapacityExceededError(f"tombstone capacity exceeded: max_tombstones {self._max_tombstones}")
        # Create bounded tombstone (no raw payload, only identity + prior digest)
        tombstone = RetentionTombstone(
            storage_contract_version=self._storage_contract_version,
            storage_key=key,
            retention_policy_id=retention_provenance.retention_policy_id,
            retention_policy_version=retention_provenance.retention_policy_version,
            retention_class=retention_provenance.retention_class.value if hasattr(retention_provenance.retention_class, "value") else str(retention_provenance.retention_class),
            prior_content_digest=envelope.content_digest,
            expired_disposition="expired",
        )
        # Atomic removal + tombstone publish
        new_store = dict(self._store)
        del new_store[key]
        self._store = new_store
        new_tomb = dict(self._tombstones)
        new_tomb[key] = tombstone
        self._tombstones = new_tomb
        return tombstone

    def get_tombstone(self, project_id: str, aggregation_series_id: str, aggregation_window_id: str | None) -> RetentionTombstone:
        key = self._validate_key_params(project_id, aggregation_series_id, aggregation_window_id)
        tomb = self._tombstones.get(key)
        if tomb is None:
            raise StorageUnavailableError(f"tombstone not found for key {key!r}")
        return tomb

    def put_reprocessed(self, new_state: AggregationState) -> StorageEnvelope:
        """Explicit reprocessing replacement. Same project scope, valid identity, new digest."""
        if not isinstance(new_state, AggregationState):
            raise IntegrityMismatchError(f"new_state must be AggregationState, got {type(new_state).__name__}")
        try:
            key = derive_storage_key(new_state)
        except Exception as exc:
            raise MalformedKeyError(str(exc)) from exc
        # Check existing envelope or tombstone (if expired, reprocessing should recreate? For simplicity require existing store entry or tombstone)
        existing = self._store.get(key)
        tomb = self._tombstones.get(key)
        prior_digest: str | None = None
        if existing is not None:
            self._check_integrity(existing)
            prior_digest = existing.content_digest
        elif tomb is not None:
            prior_digest = tomb.prior_content_digest
        else:
            # No prior record: reprocessing requires prior existence (fail closed)
            raise StorageUnavailableError(f"reprocessing requires existing record for key {key!r}, none found")
        # Ensure project scope matches (key derived ensures)
        # Compute new digest
        new_digest = compute_state_digest(new_state)
        # If same digest as prior, treat as idempotent (no change)
        if existing is not None and existing.content_digest == new_digest:
            return existing
        # Capacity not needed as replacement, but check if moving from tombstone to store needs record capacity
        if existing is None and tomb is not None:
            if len(self._store) >= self._max_records:
                raise CapacityExceededError(f"capacity exceeded on reprocessed recreate: max_records {self._max_records}")
            proj_after = self._project_count_after(key)
            if proj_after > self._max_projects:
                raise CapacityExceededError(f"capacity exceeded: max_projects {self._max_projects}")

        state_copy = _copy_state(new_state)
        envelope = StorageEnvelope(
            storage_contract_version=self._storage_contract_version,
            storage_key=key,
            aggregation_state=state_copy,
            content_digest=new_digest,
            prior_content_digest=prior_digest,
            storage_lifecycle="active",
        )
        self._check_integrity(envelope)
        # Publish atomically
        new_store = dict(self._store)
        new_store[key] = envelope
        self._store = new_store
        if tomb is not None:
            new_tomb = dict(self._tombstones)
            del new_tomb[key]
            self._tombstones = new_tomb
        return envelope

    # -----------------------------------------------------------------------
    # Inspection helpers for tests / deterministic iteration (not public query)
    # -----------------------------------------------------------------------

    def _list_keys_canonical(self) -> list[StorageKey]:
        # Deterministic ordering
        return sorted(self._store.keys(), key=lambda k: (k.project_id, k.aggregation_series_id, k.aggregation_window_id or ""))

    def _list_tombstone_keys_canonical(self) -> list[StorageKey]:
        return sorted(self._tombstones.keys(), key=lambda k: (k.project_id, k.aggregation_series_id, k.aggregation_window_id or ""))

    def __len__(self) -> int:
        return len(self._store)

    def tombstone_count(self) -> int:
        return len(self._tombstones)

__all__ = [
    "STORAGE_CONTRACT_VERSION",
    "SUPPORTED_STORAGE_VERSIONS",
    "TELEMETRY_EVIDENCE_VERSION_EXPOSED",
    "METRIC_TAXONOMY_VERSION_EXPOSED",
    "DIMENSION_SCHEMA_VERSION_EXPOSED",
    "NORMALIZATION_VERSION_EXPOSED",
    "StorageKey",
    "StorageEnvelope",
    "RetentionTombstone",
    "StoreCapabilities",
    "StorageError",
    "UnsupportedSchemaError",
    "ScopeMismatchError",
    "IntegrityMismatchError",
    "CapacityExceededError",
    "ExpiredError",
    "StorageUnavailableError",
    "MalformedKeyError",
    "TelemetryStore",
    "EphemeralTelemetryStore",
    "derive_storage_key",
    "compute_state_digest",
    # flags
    "STORAGE_NEUTRAL_TRANSITIONS",
    "STORAGE_PROTOCOL_STORAGE_NEUTRAL",
    "W1_AGGREGATION_STATE_REDEFINED",
    "SOURCE_DEDUP_ID_REDEFINED",
    "PROJECTION_ID_REDEFINED",
    "AGGREGATION_SERIES_ID_REDEFINED",
    "AGGREGATION_WINDOW_ID_REDEFINED",
    "DURABLE_STORAGE_IMPLEMENTED",
    "STORAGE_ENGINE_SELECTED",
    "FILESYSTEM_IO_CREATED",
    "DATABASE_CREATED",
    "BACKGROUND_WORKER_CREATED",
    "TTL_RUNTIME_CREATED",
    "QUERY_LAYER_CREATED",
    "W3_QUERY_CONTRACT_PREEMPTED",
    "SELECTIVE_HYDRATION_IMPLEMENTED",
    "SECOND_HYDRATION_AUTHORITY_CREATED",
    "NEW_EXECUTION_JOURNAL_CREATED",
    "SECOND_RESULT_STORE_CREATED",
    "NEW_WORKFLOW_STATE_MACHINE_CREATED",
]
