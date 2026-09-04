"""Telemetry Evidence Identity & Envelope Contract — S6 M1-W1.

Thin immutable/value-contract layer freezing S6 telemetry evidence semantics for
downstream W2/W3/M2/M3.

Architecture:
    existing S1/S2/S3 observations
            ↓
    W1 Telemetry Evidence Contract
            ↓
          ┌─┴─┐
          ↓   ↓
         W2   W3

Invariants
----------
* TELEMETRY_EVIDENCE_CONTRACT_VERSION=s6-m1-v1 (explicit, supported set bounded)
* SOURCE_EVIDENCE_IDENTITY_IS_AUTHORITY=no
* SOURCE_DIGEST_IS_AUTHORITY=no
* SOURCE_REF_POSSESSION_GRANTS_AUTHORITY=no
* SOURCE_EVIDENCE_IDENTITY_IS_AUTHORITY=no (telemetry provenance only)
* source_dedup_id != projection_id
* source_dedup_id excludes normalization_version/metric_family/projection_version/storage_row_id/ingestion_timestamp/random_uuid
* Same source replay → same source_dedup_id
* New normalization version may create new projection_id but not new source_dedup_id
* completeness = (state, scope) both required; MISSING_TELEMETRY_IS_ZERO=no
* collection sampled → sampling provenance required
* temporal provenance: ingestion_time required, timezone-aware, no wall clock
* LATE_EVENT_CLASSIFICATION_REQUIRES_WINDOW_POLICY=yes
* OUT_OF_ORDER_CLASSIFICATION_REQUIRES_COMPARABLE_SOURCE_ORDER=yes
* NO_ARBITRARY_RAW_PAYLOAD_FIELD=yes, NO_ARBITRARY_METADATA_BAG=yes
* BOUNDED_OBSERVATION_PAYLOAD=yes, NO_RAW_SECRET_CAPTURE=yes, NO_RAW_TOOL_PAYLOAD_CAPTURE_BY_DEFAULT=yes
* TELEMETRY_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT=yes (isolated health evidence)
* NEW_PERSISTENT_STORE_CREATED=no, NEW_EVENT_TYPE_CREATED=no, THIRD_RESULT_ONTOLOGY_CREATED=no

No database / clock service / filesystem IO / network / subprocess / background worker / runtime registration.
Caller supplies ingestion_time; no implicit wall clock read inside value contract.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------

TELEMETRY_EVIDENCE_CONTRACT_VERSION: str = "s6-m1-v1"
SUPPORTED_ENVELOPE_VERSIONS: frozenset[str] = frozenset({TELEMETRY_EVIDENCE_CONTRACT_VERSION})
ENVELOPE_CONTRACT_VERSION_KEY: str = "envelope_version"

# ---------------------------------------------------------------------------
# Invariant flags
# ---------------------------------------------------------------------------

SOURCE_EVIDENCE_IDENTITY_IS_AUTHORITY: bool = False
SOURCE_DIGEST_IS_AUTHORITY: bool = False
SOURCE_REF_POSSESSION_GRANTS_AUTHORITY: bool = False
SOURCE_REPLAY_DOES_NOT_CREATE_NEW_DEDUP_ID: bool = True
NEW_NORMALIZATION_VERSION_MAY_CREATE_NEW_PROJECTION_ID: bool = True
NEW_NORMALIZATION_VERSION_DOES_NOT_CREATE_NEW_SOURCE_DEDUP_ID: bool = True

# Completeness invariants
MISSING_TELEMETRY_IS_ZERO: bool = False
ZERO_OBSERVATION_COUNT_IMPLIES_COMPLETE_ZERO: bool = False
COMPLETENESS_STATE_WITHOUT_SCOPE_INVALID: bool = True

# Temporal invariants
LATE_EVENT_CLASSIFICATION_REQUIRES_WINDOW_POLICY: bool = True
OUT_OF_ORDER_CLASSIFICATION_REQUIRES_COMPARABLE_SOURCE_ORDER: bool = True
IMPLICIT_WALL_CLOCK_USED: bool = False

# Bounded payload invariants
NO_ARBITRARY_RAW_PAYLOAD_FIELD: bool = True
NO_ARBITRARY_METADATA_BAG: bool = True
BOUNDED_OBSERVATION_PAYLOAD: bool = True
NO_RAW_SECRET_CAPTURE: bool = True
NO_RAW_TOOL_PAYLOAD_CAPTURE_BY_DEFAULT: bool = True
PRIVATE_REASONING_CAPTURE: bool = False
FULL_TRANSCRIPT_CAPTURE: bool = False

# Failure isolation
TELEMETRY_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT: bool = True
ANALYTICS_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT: bool = True
STORAGE_FAILURE_DOES_NOT_REWRITE_CANONICAL_RESULT: bool = True
OBSERVABILITY_IS_EXECUTION_AUTHORITY: bool = False
TELEMETRY_HEALTH_ISOLATED: bool = True
TELEMETRY_HEALTH_IS_EXECUTION_ERROR: bool = False
TELEMETRY_HEALTH_IS_CANONICAL_RESULT: bool = False
TELEMETRY_HEALTH_IS_POLICY_AUTHORITY: bool = False

# Negative architecture invariants
NEW_PERSISTENT_STORE_CREATED: bool = False
NEW_DATABASE_CREATED: bool = False
NEW_EVENT_BUS_CREATED: bool = False
NEW_EVENT_TYPE_CREATED: bool = False
NEW_RESULT_ONTOLOGY_CREATED: bool = False
NEW_STATE_MACHINE_CREATED: bool = False
NEW_JOURNAL_CREATED: bool = False
NEW_POLICY_ENGINE_CREATED: bool = False
AUTOMATIC_ARCHITECTURE_MUTATION: bool = False
EXISTING_OBSERVATION_PRODUCER_CHANGED: bool = False

THIRD_RESULT_ONTOLOGY_CREATED: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_PROJECT_ID_LENGTH: int = 96
MAX_SOURCE_KIND_LENGTH: int = 64
MAX_SOURCE_OBSERVATION_ID_LENGTH: int = 256
MAX_SOURCE_CONTRACT_VERSION_LENGTH: int = 64
MAX_SOURCE_DIGEST_LENGTH: int = 128
MAX_WORKTREE_ID_LENGTH: int = 128
MAX_EXECUTION_REF_LENGTH: int = 512
MAX_PROJECTION_NAMESPACE_LENGTH: int = 64
MAX_PROJECTION_VERSION_LENGTH: int = 64
MAX_SAMPLING_POLICY_ID_LENGTH: int = 128
MAX_SAMPLING_POLICY_VERSION_LENGTH: int = 64
MAX_SAMPLING_MODE_LENGTH: int = 32
MAX_RETENTION_POLICY_ID_LENGTH: int = 128
MAX_RETENTION_POLICY_VERSION_LENGTH: int = 64
MAX_COMPONENT_LENGTH: int = 64
MAX_FAILURE_CODE_LENGTH: int = 64
MAX_ENVELOPE_VERSION_LENGTH: int = 32

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# allow alphanumeric plus . _ - for most bounded identities; strict but not overly restrictive

# ---------------------------------------------------------------------------
# Helpers — bounded strings, digest, datetime
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{label} must be a non-empty string")
    if len(v) > max_len:
        raise ValueError(f"{label} length ({len(v)}) exceeds maximum {max_len}")
    if "\x00" in v:
        raise ValueError(f"{label} must not contain NUL")
    return v


def _validate_optional_bounded_str(value: object, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    return _validate_bounded_str(value, label, max_len)


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ValueError("digest must be non-empty")
    if len(v) > MAX_SOURCE_DIGEST_LENGTH:
        raise ValueError(f"digest length {len(v)} exceeds {MAX_SOURCE_DIGEST_LENGTH}")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ValueError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _validate_optional_digest(value: object) -> str | None:
    if value is None:
        return None
    return _validate_digest(value)


def _validate_tzaware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware (got naive)")
    return value


def _canonical_datetime_str(dt: datetime) -> str:
    # Normalize to UTC and produce deterministic ISO8601 with Z
    utc = dt.astimezone(timezone.utc)
    # Use isoformat with milliseconds? Keep full microsecond but deterministic
    # Use isoformat timespec milliseconds to avoid trailing zeros variance? Use isoformat()
    s = utc.isoformat()
    # isoformat for UTC gives "+00:00"; replace with Z for canonical
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def _parse_datetime_string(value: object, label: str) -> datetime:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be datetime string, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ValueError(f"{label} must be non-empty datetime string")
    # Support Z suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception as exc:
        raise ValueError(f"{label} invalid datetime string {value!r}: {exc}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return dt


# ---------------------------------------------------------------------------
# Enums — Completeness, Retention
# ---------------------------------------------------------------------------

@unique
class CompletenessState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    SAMPLED = "sampled"
    MISSING = "missing"
    TRUNCATED = "truncated"
    UNKNOWN = "unknown"


@unique
class CompletenessScope(str, Enum):
    SOURCE = "source"
    COLLECTION = "collection"
    PROJECTION = "projection"


@unique
class RetentionClass(str, Enum):
    EPHEMERAL = "ephemeral"
    BOUNDED_PERSISTED = "bounded_persisted"
    UNKNOWN = "unknown"


COMPLETENESS_STATES: frozenset[str] = frozenset(e.value for e in CompletenessState)
COMPLETENESS_SCOPES: frozenset[str] = frozenset(e.value for e in CompletenessScope)
RETENTION_CLASSES: frozenset[str] = frozenset(e.value for e in RetentionClass)


def parse_completeness_state(value: object) -> CompletenessState:
    if isinstance(value, CompletenessState):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return CompletenessState(value)
        except ValueError:
            raise ValueError(f"Unknown completeness state: {value!r}. Must be one of {sorted(COMPLETENESS_STATES)}")
    raise TypeError(f"completeness state must be str or CompletenessState, got {type(value).__name__}")


def parse_completeness_scope(value: object) -> CompletenessScope:
    if isinstance(value, CompletenessScope):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return CompletenessScope(value)
        except ValueError:
            raise ValueError(f"Unknown completeness scope: {value!r}. Must be one of {sorted(COMPLETENESS_SCOPES)}")
    raise TypeError(f"completeness scope must be str or CompletenessScope, got {type(value).__name__}")


def parse_retention_class(value: object) -> RetentionClass:
    if isinstance(value, RetentionClass):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return RetentionClass(value)
        except ValueError:
            raise ValueError(f"Unknown retention class: {value!r}. Must be one of {sorted(RETENTION_CLASSES)}")
    raise TypeError(f"retention class must be str or RetentionClass, got {type(value).__name__}")

# ---------------------------------------------------------------------------
# Source Evidence Identity
# ---------------------------------------------------------------------------

_ALLOWED_SOURCE_IDENTITY_FIELDS: frozenset[str] = frozenset({
    "project_id",
    "source_kind",
    "source_observation_id",
    "source_contract_version",
    "source_digest",
    "worktree_id",
    "execution_ref",
})


@dataclass(frozen=True)
class SourceEvidenceIdentity:
    """Thin source evidence provenance identity (telemetry provenance only).

    Fields:
        project_id: bounded required
        source_kind: bounded required
        source_observation_id: bounded required (W3 adapter supplied, not raw payload)
        source_contract_version: bounded required
        source_digest: 64 hex sha256 of source observation payload (non-authoritative)
        worktree_id: optional bounded provenance scope
        execution_ref: optional bounded correlation-like ref
    """

    project_id: str
    source_kind: str
    source_observation_id: str
    source_contract_version: str
    source_digest: str
    worktree_id: str | None = None
    execution_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "source_kind", _validate_bounded_str(self.source_kind, "source_kind", MAX_SOURCE_KIND_LENGTH))
        object.__setattr__(self, "source_observation_id", _validate_bounded_str(self.source_observation_id, "source_observation_id", MAX_SOURCE_OBSERVATION_ID_LENGTH))
        object.__setattr__(self, "source_contract_version", _validate_bounded_str(self.source_contract_version, "source_contract_version", MAX_SOURCE_CONTRACT_VERSION_LENGTH))
        object.__setattr__(self, "source_digest", _validate_digest(self.source_digest))
        if self.worktree_id is not None:
            object.__setattr__(self, "worktree_id", _validate_bounded_str(self.worktree_id, "worktree_id", MAX_WORKTREE_ID_LENGTH))
        if self.execution_ref is not None:
            object.__setattr__(self, "execution_ref", _validate_bounded_str(self.execution_ref, "execution_ref", MAX_EXECUTION_REF_LENGTH))

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "project_id": self.project_id,
            "source_contract_version": self.source_contract_version,
            "source_digest": self.source_digest,
            "source_kind": self.source_kind,
            "source_observation_id": self.source_observation_id,
        }
        if self.worktree_id is not None:
            d["worktree_id"] = self.worktree_id
        if self.execution_ref is not None:
            d["execution_ref"] = self.execution_ref
        return canonicalize(d, path="SourceEvidenceIdentity")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "project_id": self.project_id,
            "source_kind": self.source_kind,
            "source_observation_id": self.source_observation_id,
            "source_contract_version": self.source_contract_version,
            "source_digest": self.source_digest,
        }
        if self.worktree_id is not None:
            d["worktree_id"] = self.worktree_id
        if self.execution_ref is not None:
            d["execution_ref"] = self.execution_ref
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceEvidenceIdentity":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_SOURCE_IDENTITY_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in SourceEvidenceIdentity: {sorted(extra)}")
        for req in ("project_id", "source_kind", "source_observation_id", "source_contract_version", "source_digest"):
            if req not in data:
                raise ValueError(f"Missing required field in SourceEvidenceIdentity: {req!r}")
        return cls(
            project_id=data["project_id"],
            source_kind=data["source_kind"],
            source_observation_id=data["source_observation_id"],
            source_contract_version=data["source_contract_version"],
            source_digest=data["source_digest"],
            worktree_id=data.get("worktree_id"),
            execution_ref=data.get("execution_ref"),
        )


# ---------------------------------------------------------------------------
# Source dedup / Projection identity primitives
# ---------------------------------------------------------------------------

def compute_source_dedup_id(identity: SourceEvidenceIdentity) -> str:
    """Deterministically derive source_dedup_id from source identity.

    Canonical input excludes normalization_version/metric_family/projection_version/
    storage_row_id/ingestion_timestamp/random_uuid.
    Optional provenance scope (worktree_id, execution_ref) does NOT participate
    in dedup to preserve replay identity stability — same source observation
    replay yields same dedup. If exact source scope required, caller should
    include it in source_observation_id or project_id boundary.
    """
    if not isinstance(identity, SourceEvidenceIdentity):
        raise TypeError(f"identity must be SourceEvidenceIdentity, got {type(identity).__name__}")
    # Strict 5-field canonical per spec; extra provenance excluded from dedup
    payload = {
        "project_id": identity.project_id,
        "source_contract_version": identity.source_contract_version,
        "source_digest": identity.source_digest,
        "source_kind": identity.source_kind,
        "source_observation_id": identity.source_observation_id,
    }
    canonical = canonicalize(payload, path="source_dedup")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def compute_projection_id(
    source_dedup_id: str,
    projection_namespace: str,
    projection_version: str,
) -> str:
    """Deterministically derive projection identity primitive.

    projection_id = sha256(canonical(source_dedup_id, projection_namespace, projection_version))
    W2 may bind namespace → metric family / normalization namespace and version → schema version.
    W1 does NOT define metric families.
    """
    sd = _validate_digest(source_dedup_id)
    ns = _validate_bounded_str(projection_namespace, "projection_namespace", MAX_PROJECTION_NAMESPACE_LENGTH)
    pv = _validate_bounded_str(projection_version, "projection_version", MAX_PROJECTION_VERSION_LENGTH)
    payload = {
        "projection_namespace": ns,
        "projection_version": pv,
        "source_dedup_id": sd,
    }
    canonical = canonicalize(payload, path="projection")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Completeness record
# ---------------------------------------------------------------------------

_ALLOWED_COMPLETENESS_FIELDS: frozenset[str] = frozenset({"state", "scope"})


@dataclass(frozen=True)
class CompletenessRecord:
    """Completeness with state + scope (both required).

    state ∈ {complete, partial, sampled, missing, truncated, unknown}
    scope ∈ {source, collection, projection}
    """

    state: CompletenessState
    scope: CompletenessScope

    def __post_init__(self) -> None:
        # state
        if isinstance(self.state, CompletenessState):
            pass
        elif isinstance(self.state, str) and type(self.state) is str:
            object.__setattr__(self, "state", parse_completeness_state(self.state))
        else:
            raise TypeError(f"state must be CompletenessState or str, got {type(self.state).__name__}")
        # scope
        if isinstance(self.scope, CompletenessScope):
            pass
        elif isinstance(self.scope, str) and type(self.scope) is str:
            object.__setattr__(self, "scope", parse_completeness_scope(self.scope))
        else:
            raise TypeError(f"scope must be CompletenessScope or str, got {type(self.scope).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({"scope": self.scope.value, "state": self.state.value}, path="CompletenessRecord")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "scope": self.scope.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompletenessRecord":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_COMPLETENESS_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in CompletenessRecord: {sorted(extra)}")
        if "state" not in data or "scope" not in data:
            raise ValueError("Missing required field in CompletenessRecord: 'state' and 'scope' required")
        return cls(state=data["state"], scope=data["scope"])


# ---------------------------------------------------------------------------
# Sampling provenance
# ---------------------------------------------------------------------------

_ALLOWED_SAMPLING_FIELDS: frozenset[str] = frozenset({
    "sampling_policy_id",
    "sampling_policy_version",
    "sampling_mode",
})


@dataclass(frozen=True)
class SamplingProvenance:
    """Bounded sampling provenance reference (no probability/engine/config blob)."""

    sampling_policy_id: str
    sampling_policy_version: str
    sampling_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sampling_policy_id", _validate_bounded_str(self.sampling_policy_id, "sampling_policy_id", MAX_SAMPLING_POLICY_ID_LENGTH))
        object.__setattr__(self, "sampling_policy_version", _validate_bounded_str(self.sampling_policy_version, "sampling_policy_version", MAX_SAMPLING_POLICY_VERSION_LENGTH))
        if self.sampling_mode is not None:
            object.__setattr__(self, "sampling_mode", _validate_bounded_str(self.sampling_mode, "sampling_mode", MAX_SAMPLING_MODE_LENGTH))

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "sampling_policy_id": self.sampling_policy_id,
            "sampling_policy_version": self.sampling_policy_version,
        }
        if self.sampling_mode is not None:
            d["sampling_mode"] = self.sampling_mode
        return canonicalize(d, path="SamplingProvenance")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "sampling_policy_id": self.sampling_policy_id,
            "sampling_policy_version": self.sampling_policy_version,
        }
        if self.sampling_mode is not None:
            d["sampling_mode"] = self.sampling_mode
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SamplingProvenance":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_SAMPLING_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in SamplingProvenance: {sorted(extra)}")
        for req in ("sampling_policy_id", "sampling_policy_version"):
            if req not in data:
                raise ValueError(f"Missing required field in SamplingProvenance: {req!r}")
        return cls(
            sampling_policy_id=data["sampling_policy_id"],
            sampling_policy_version=data["sampling_policy_version"],
            sampling_mode=data.get("sampling_mode"),
        )


# ---------------------------------------------------------------------------
# Retention provenance
# ---------------------------------------------------------------------------

_ALLOWED_RETENTION_FIELDS: frozenset[str] = frozenset({
    "retention_policy_id",
    "retention_policy_version",
    "retention_class",
})


@dataclass(frozen=True)
class RetentionProvenance:
    """Bounded retention lifecycle identity (no TTL/bytes/engine)."""

    retention_policy_id: str
    retention_policy_version: str
    retention_class: RetentionClass

    def __post_init__(self) -> None:
        object.__setattr__(self, "retention_policy_id", _validate_bounded_str(self.retention_policy_id, "retention_policy_id", MAX_RETENTION_POLICY_ID_LENGTH))
        object.__setattr__(self, "retention_policy_version", _validate_bounded_str(self.retention_policy_version, "retention_policy_version", MAX_RETENTION_POLICY_VERSION_LENGTH))
        if isinstance(self.retention_class, RetentionClass):
            pass
        elif isinstance(self.retention_class, str) and type(self.retention_class) is str:
            object.__setattr__(self, "retention_class", parse_retention_class(self.retention_class))
        else:
            raise TypeError(f"retention_class must be RetentionClass or str, got {type(self.retention_class).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "retention_class": self.retention_class.value,
            "retention_policy_id": self.retention_policy_id,
            "retention_policy_version": self.retention_policy_version,
        }, path="RetentionProvenance")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "retention_policy_id": self.retention_policy_id,
            "retention_policy_version": self.retention_policy_version,
            "retention_class": self.retention_class.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RetentionProvenance":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_RETENTION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in RetentionProvenance: {sorted(extra)}")
        for req in ("retention_policy_id", "retention_policy_version", "retention_class"):
            if req not in data:
                raise ValueError(f"Missing required field in RetentionProvenance: {req!r}")
        return cls(
            retention_policy_id=data["retention_policy_id"],
            retention_policy_version=data["retention_policy_version"],
            retention_class=data["retention_class"],
        )


# ---------------------------------------------------------------------------
# Temporal provenance
# ---------------------------------------------------------------------------

_ALLOWED_TEMPORAL_FIELDS: frozenset[str] = frozenset({
    "source_event_time",
    "observation_time",
    "ingestion_time",
})


@dataclass(frozen=True)
class TemporalProvenance:
    """Bounded pure time provenance.

    source_event_time optional
    observation_time optional
    ingestion_time required when materializing S6 evidence (caller-supplied, tz-aware)
    No wall clock read inside contract.
    """

    ingestion_time: datetime
    source_event_time: datetime | None = None
    observation_time: datetime | None = None

    def __post_init__(self) -> None:
        # ingestion_time required tz-aware
        object.__setattr__(self, "ingestion_time", _validate_tzaware_datetime(self.ingestion_time, "ingestion_time"))
        if self.source_event_time is not None:
            object.__setattr__(self, "source_event_time", _validate_tzaware_datetime(self.source_event_time, "source_event_time"))
        if self.observation_time is not None:
            object.__setattr__(self, "observation_time", _validate_tzaware_datetime(self.observation_time, "observation_time"))

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ingestion_time": _canonical_datetime_str(self.ingestion_time),
        }
        if self.source_event_time is not None:
            d["source_event_time"] = _canonical_datetime_str(self.source_event_time)
        if self.observation_time is not None:
            d["observation_time"] = _canonical_datetime_str(self.observation_time)
        return canonicalize(d, path="TemporalProvenance")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ingestion_time": _canonical_datetime_str(self.ingestion_time),
        }
        if self.source_event_time is not None:
            d["source_event_time"] = _canonical_datetime_str(self.source_event_time)
        if self.observation_time is not None:
            d["observation_time"] = _canonical_datetime_str(self.observation_time)
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemporalProvenance":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_TEMPORAL_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in TemporalProvenance: {sorted(extra)}")
        if "ingestion_time" not in data:
            raise ValueError("Missing required field in TemporalProvenance: 'ingestion_time'")
        # ingestion_time may be datetime or string
        raw_ing = data["ingestion_time"]
        if isinstance(raw_ing, datetime):
            ing = _validate_tzaware_datetime(raw_ing, "ingestion_time")
        elif isinstance(raw_ing, str):
            ing = _parse_datetime_string(raw_ing, "ingestion_time")
        else:
            raise TypeError(f"ingestion_time must be datetime or ISO string, got {type(raw_ing).__name__}")
        src = None
        obs = None
        if data.get("source_event_time") is not None:
            raw = data["source_event_time"]
            if isinstance(raw, datetime):
                src = _validate_tzaware_datetime(raw, "source_event_time")
            elif isinstance(raw, str):
                src = _parse_datetime_string(raw, "source_event_time")
            else:
                raise TypeError(f"source_event_time must be datetime or string, got {type(raw).__name__}")
        if data.get("observation_time") is not None:
            raw = data["observation_time"]
            if isinstance(raw, datetime):
                obs = _validate_tzaware_datetime(raw, "observation_time")
            elif isinstance(raw, str):
                obs = _parse_datetime_string(raw, "observation_time")
            else:
                raise TypeError(f"observation_time must be datetime or string, got {type(raw).__name__}")
        return cls(ingestion_time=ing, source_event_time=src, observation_time=obs)


# ---------------------------------------------------------------------------
# Telemetry Health Evidence (isolated, not authority)
# ---------------------------------------------------------------------------

_ALLOWED_HEALTH_FIELDS: frozenset[str] = frozenset({
    "component",
    "failure_code",
    "completeness",
    "source_dedup_id",
})


@dataclass(frozen=True)
class TelemetryHealthEvidence:
    """Bounded telemetry-health value object (isolated, non-authoritative)."""

    component: str
    failure_code: str
    completeness: CompletenessRecord
    source_dedup_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "component", _validate_bounded_str(self.component, "component", MAX_COMPONENT_LENGTH))
        object.__setattr__(self, "failure_code", _validate_bounded_str(self.failure_code, "failure_code", MAX_FAILURE_CODE_LENGTH))
        if not isinstance(self.completeness, CompletenessRecord):
            raise TypeError(f"completeness must be CompletenessRecord, got {type(self.completeness).__name__}")
        if self.source_dedup_id is not None:
            object.__setattr__(self, "source_dedup_id", _validate_digest(self.source_dedup_id))

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_execution_error(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "component": self.component,
            "completeness": self.completeness.to_dict(),
            "failure_code": self.failure_code,
        }
        if self.source_dedup_id is not None:
            d["source_dedup_id"] = self.source_dedup_id
        return canonicalize(d, path="TelemetryHealthEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "component": self.component,
            "failure_code": self.failure_code,
            "completeness": self.completeness.to_dict(),
        }
        if self.source_dedup_id is not None:
            d["source_dedup_id"] = self.source_dedup_id
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TelemetryHealthEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_HEALTH_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in TelemetryHealthEvidence: {sorted(extra)}")
        for req in ("component", "failure_code", "completeness"):
            if req not in data:
                raise ValueError(f"Missing required field in TelemetryHealthEvidence: {req!r}")
        comp = data["completeness"]
        if isinstance(comp, CompletenessRecord):
            cr = comp
        elif isinstance(comp, Mapping):
            cr = CompletenessRecord.from_dict(comp)  # type: ignore[arg-type]
        else:
            raise TypeError(f"completeness must be CompletenessRecord or dict, got {type(comp).__name__}")
        return cls(
            component=data["component"],
            failure_code=data["failure_code"],
            completeness=cr,
            source_dedup_id=data.get("source_dedup_id"),
        )


# ---------------------------------------------------------------------------
# Telemetry Evidence Envelope — main bounded evidence contract
# ---------------------------------------------------------------------------

_ALLOWED_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "envelope_version",
    "source",
    "source_dedup_id",
    "completeness",
    "sampling_provenance",
    "retention_provenance",
    "temporal_provenance",
})


@dataclass(frozen=True)
class TelemetryEvidenceEnvelope:
    """Bounded immutable S6 telemetry evidence envelope.

    Thin contract; storage/analytics not owned; envelope is the bounded payload.
    """

    envelope_version: str
    source: SourceEvidenceIdentity
    source_dedup_id: str
    completeness: CompletenessRecord
    temporal_provenance: TemporalProvenance
    sampling_provenance: SamplingProvenance | None = None
    retention_provenance: RetentionProvenance | None = None

    def __post_init__(self) -> None:
        # envelope_version bounded + supported
        v = _validate_bounded_str(self.envelope_version, "envelope_version", MAX_ENVELOPE_VERSION_LENGTH)
        if v not in SUPPORTED_ENVELOPE_VERSIONS:
            raise ValueError(f"Unsupported envelope contract version: {v!r}. Supported: {sorted(SUPPORTED_ENVELOPE_VERSIONS)}")
        object.__setattr__(self, "envelope_version", v)

        if not isinstance(self.source, SourceEvidenceIdentity):
            raise TypeError(f"source must be SourceEvidenceIdentity, got {type(self.source).__name__}")
        # source_dedup must be valid digest and deterministic
        dedup = _validate_digest(self.source_dedup_id)
        expected = compute_source_dedup_id(self.source)
        if dedup != expected:
            raise ValueError(f"source_dedup_id mismatch: expected {expected!r}, got {dedup!r}")
        object.__setattr__(self, "source_dedup_id", dedup)

        if not isinstance(self.completeness, CompletenessRecord):
            raise TypeError(f"completeness must be CompletenessRecord, got {type(self.completeness).__name__}")

        if not isinstance(self.temporal_provenance, TemporalProvenance):
            raise TypeError(f"temporal_provenance must be TemporalProvenance, got {type(self.temporal_provenance).__name__}")

        if self.sampling_provenance is not None and not isinstance(self.sampling_provenance, SamplingProvenance):
            raise TypeError(f"sampling_provenance must be SamplingProvenance or None, got {type(self.sampling_provenance).__name__}")
        if self.retention_provenance is not None and not isinstance(self.retention_provenance, RetentionProvenance):
            raise TypeError(f"retention_provenance must be RetentionProvenance or None, got {type(self.retention_provenance).__name__}")

        # Rule: collection completeness = sampled → sampling provenance required
        if self.completeness.scope == CompletenessScope.COLLECTION and self.completeness.state == CompletenessState.SAMPLED:
            if self.sampling_provenance is None:
                raise ValueError("collection completeness = sampled requires sampling provenance (sampling_policy_id/version)")

        # Also if any sampled at collection scope? Already covered. If state sampled but scope not collection, we allow without sampling? But spec says collection completeness sampled → required. Keep as above.

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "completeness": self.completeness.to_dict(),
            "envelope_version": self.envelope_version,
            "source": self.source.to_dict(),
            "source_dedup_id": self.source_dedup_id,
            "temporal_provenance": self.temporal_provenance.to_dict(),
        }
        if self.sampling_provenance is not None:
            d["sampling_provenance"] = self.sampling_provenance.to_dict()
        if self.retention_provenance is not None:
            d["retention_provenance"] = self.retention_provenance.to_dict()
        return canonicalize(d, path="TelemetryEvidenceEnvelope")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "envelope_version": self.envelope_version,
            "source": self.source.to_dict(),
            "source_dedup_id": self.source_dedup_id,
            "completeness": self.completeness.to_dict(),
            "temporal_provenance": self.temporal_provenance.to_dict(),
        }
        if self.sampling_provenance is not None:
            d["sampling_provenance"] = self.sampling_provenance.to_dict()
        if self.retention_provenance is not None:
            d["retention_provenance"] = self.retention_provenance.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TelemetryEvidenceEnvelope":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_ENVELOPE_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in TelemetryEvidenceEnvelope: {sorted(extra)}")
        for req in ("envelope_version", "source", "source_dedup_id", "completeness", "temporal_provenance"):
            if req not in data:
                raise ValueError(f"Missing required field in TelemetryEvidenceEnvelope: {req!r}")
        # version fail-closed
        v = data["envelope_version"]
        if not isinstance(v, str) or type(v) is not str:
            raise TypeError(f"envelope_version must be str, got {type(v).__name__}")
        if v.strip() not in SUPPORTED_ENVELOPE_VERSIONS:
            raise ValueError(f"Unsupported envelope contract version: {v!r}")

        src_raw = data["source"]
        if isinstance(src_raw, SourceEvidenceIdentity):
            src = src_raw
        elif isinstance(src_raw, Mapping):
            src = SourceEvidenceIdentity.from_dict(src_raw)  # type: ignore[arg-type]
        else:
            raise TypeError(f"source must be SourceEvidenceIdentity or dict, got {type(src_raw).__name__}")

        dedup = data["source_dedup_id"]
        # completeness
        comp_raw = data["completeness"]
        if isinstance(comp_raw, CompletenessRecord):
            comp = comp_raw
        elif isinstance(comp_raw, Mapping):
            comp = CompletenessRecord.from_dict(comp_raw)  # type: ignore[arg-type]
        else:
            raise TypeError(f"completeness must be CompletenessRecord or dict, got {type(comp_raw).__name__}")

        # temporal
        temp_raw = data["temporal_provenance"]
        if isinstance(temp_raw, TemporalProvenance):
            temp = temp_raw
        elif isinstance(temp_raw, Mapping):
            temp = TemporalProvenance.from_dict(temp_raw)  # type: ignore[arg-type]
        else:
            raise TypeError(f"temporal_provenance must be TemporalProvenance or dict, got {type(temp_raw).__name__}")

        samp = None
        if data.get("sampling_provenance") is not None:
            raw = data["sampling_provenance"]
            if isinstance(raw, SamplingProvenance):
                samp = raw
            elif isinstance(raw, Mapping):
                samp = SamplingProvenance.from_dict(raw)  # type: ignore[arg-type]
            else:
                raise TypeError(f"sampling_provenance must be SamplingProvenance or dict, got {type(raw).__name__}")

        retain = None
        if data.get("retention_provenance") is not None:
            raw = data["retention_provenance"]
            if isinstance(raw, RetentionProvenance):
                retain = raw
            elif isinstance(raw, Mapping):
                retain = RetentionProvenance.from_dict(raw)  # type: ignore[arg-type]
            else:
                raise TypeError(f"retention_provenance must be RetentionProvenance or dict, got {type(raw).__name__}")

        return cls(
            envelope_version=v.strip(),
            source=src,
            source_dedup_id=dedup,
            completeness=comp,
            temporal_provenance=temp,
            sampling_provenance=samp,
            retention_provenance=retain,
        )


def create_telemetry_evidence_envelope(
    source: SourceEvidenceIdentity,
    completeness: CompletenessRecord,
    temporal_provenance: TemporalProvenance,
    sampling_provenance: SamplingProvenance | None = None,
    retention_provenance: RetentionProvenance | None = None,
    envelope_version: str = TELEMETRY_EVIDENCE_CONTRACT_VERSION,
) -> TelemetryEvidenceEnvelope:
    """Factory that deterministically derives source_dedup_id and creates envelope.

    Caller supplies source identity, completeness, temporal, optional sampling/retention.
    Dedup is derived via compute_source_dedup_id; no random/ingestion timestamp participates.
    """
    dedup = compute_source_dedup_id(source)
    return TelemetryEvidenceEnvelope(
        envelope_version=envelope_version,
        source=source,
        source_dedup_id=dedup,
        completeness=completeness,
        temporal_provenance=temporal_provenance,
        sampling_provenance=sampling_provenance,
        retention_provenance=retention_provenance,
    )


__all__ = [
    "TELEMETRY_EVIDENCE_CONTRACT_VERSION",
    "SUPPORTED_ENVELOPE_VERSIONS",
    "SOURCE_EVIDENCE_IDENTITY_IS_AUTHORITY",
    "SOURCE_DIGEST_IS_AUTHORITY",
    "SOURCE_REF_POSSESSION_GRANTS_AUTHORITY",
    "SOURCE_REPLAY_DOES_NOT_CREATE_NEW_DEDUP_ID",
    "NEW_NORMALIZATION_VERSION_MAY_CREATE_NEW_PROJECTION_ID",
    "NEW_NORMALIZATION_VERSION_DOES_NOT_CREATE_NEW_SOURCE_DEDUP_ID",
    "MISSING_TELEMETRY_IS_ZERO",
    "ZERO_OBSERVATION_COUNT_IMPLIES_COMPLETE_ZERO",
    "COMPLETENESS_STATE_WITHOUT_SCOPE_INVALID",
    "LATE_EVENT_CLASSIFICATION_REQUIRES_WINDOW_POLICY",
    "OUT_OF_ORDER_CLASSIFICATION_REQUIRES_COMPARABLE_SOURCE_ORDER",
    "IMPLICIT_WALL_CLOCK_USED",
    "NO_ARBITRARY_RAW_PAYLOAD_FIELD",
    "NO_ARBITRARY_METADATA_BAG",
    "BOUNDED_OBSERVATION_PAYLOAD",
    "NO_RAW_SECRET_CAPTURE",
    "NO_RAW_TOOL_PAYLOAD_CAPTURE_BY_DEFAULT",
    "TELEMETRY_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT",
    "ANALYTICS_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT",
    "STORAGE_FAILURE_DOES_NOT_REWRITE_CANONICAL_RESULT",
    "OBSERVABILITY_IS_EXECUTION_AUTHORITY",
    "TELEMETRY_HEALTH_ISOLATED",
    "NEW_PERSISTENT_STORE_CREATED",
    "NEW_EVENT_TYPE_CREATED",
    "THIRD_RESULT_ONTOLOGY_CREATED",
    "CompletenessState",
    "CompletenessScope",
    "RetentionClass",
    "parse_completeness_state",
    "parse_completeness_scope",
    "parse_retention_class",
    "SourceEvidenceIdentity",
    "compute_source_dedup_id",
    "compute_projection_id",
    "CompletenessRecord",
    "SamplingProvenance",
    "RetentionProvenance",
    "TemporalProvenance",
    "TelemetryHealthEvidence",
    "TelemetryEvidenceEnvelope",
    "create_telemetry_evidence_envelope",
    "COMPLETENESS_STATES",
    "COMPLETENESS_SCOPES",
    "RETENTION_CLASSES",
]
