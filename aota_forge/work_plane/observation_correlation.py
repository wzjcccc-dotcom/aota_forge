"""AF #54 M3/W1 — Production observation correlation & timing contract.

Bounded, privacy-safe correlation/timing facts for production Tool/Skill
observations. This module defines the minimum data contract required to
derive run-scoped effectiveness evidence from the existing S6 observation
foundation. It creates no second telemetry system and no metric taxonomy.

    governed Tool invocation
        ↓
    bounded correlation facts (this module)
        ↓
    existing ToolUsageObservation / SkillUsageObservation (S2/S3)
        ↓
    existing S6 evidence / projection semantics

Invariants
----------
* OBSERVATION_CORRELATION_IS_AUTHORITY=no
* NORMALIZED_REQUEST_DIGEST_IS_AUTHORITY=no
* WALL_CLOCK_IS_IDENTITY_SOURCE=no  (wall stamps are durable correlation
  facts only; identity is the deterministic normalized digest)
* RAW_TOOL_INPUT_CAPTURED=no, RAW_PROMPT_CAPTURED=no,
  RAW_TOOL_OUTPUT_CAPTURED=no, SECRET_ENV_CAPTURED=no
* KNOWN_SECRET_FIELDS_EXCLUDED=yes — secret-bearing input fields are
  removed from request identity, never hashed as a masking claim
* MONOTONIC_DURATION_PREFERRED=yes — elapsed Tool duration uses a
  monotonic clock when available; UTC stamps are supplementary and never
  an identity source
* SECOND_TELEMETRY_SYSTEM_CREATED=no, SECOND_METRIC_TAXONOMY_CREATED=no
* OBSERVATION_CORRELATION_BOUNDED=yes, OBSERVATION_CORRELATION_FAIL_CLOSED=yes
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Contract version — the enriched correlation/timing schema revision
# ---------------------------------------------------------------------------

OBSERVATION_CORRELATION_CONTRACT_VERSION: str = "s6-af54-m3-v1"

# Legacy (pre-M3) bounded observation identity — still valid, carries no
# correlation/timing facts.
LEGACY_OBSERVATION_CONTRACT_VERSION: str = "s6-m1-w3-tool-usage-v1"

# ---------------------------------------------------------------------------
# Public invariant flags
# ---------------------------------------------------------------------------

OBSERVATION_CORRELATION_IS_AUTHORITY: bool = False
NORMALIZED_REQUEST_DIGEST_IS_AUTHORITY: bool = False
WALL_CLOCK_IS_IDENTITY_SOURCE: bool = False
CORRELATION_FACTS_CHANGE_EXECUTION_RESULT: bool = False

RAW_TOOL_INPUT_CAPTURED: bool = False
RAW_PROMPT_CAPTURED: bool = False
RAW_TOOL_OUTPUT_CAPTURED: bool = False
SECRET_ENV_CAPTURED: bool = False
KNOWN_SECRET_FIELDS_EXCLUDED: bool = True
SECRET_VALUE_HASHED: bool = False

MONOTONIC_DURATION_PREFERRED: bool = True
DURATION_IS_IDENTITY_SOURCE: bool = False

OBSERVATION_CORRELATION_BOUNDED: bool = True
OBSERVATION_CORRELATION_FAIL_CLOSED: bool = True
SECOND_TELEMETRY_SYSTEM_CREATED: bool = False
SECOND_METRIC_TAXONOMY_CREATED: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_SESSION_REF_LENGTH: int = 512
MAX_PARENT_SESSION_REF_LENGTH: int = 512
MAX_CANONICAL_TASK_ID_LENGTH: int = 512
MAX_MILESTONE_REF_LENGTH: int = 256
MAX_WORK_ITEM_REF_LENGTH: int = 256
MAX_RUN_REF_LENGTH: int = 128
MAX_DURATION_NS: int = 7 * 24 * 60 * 60 * 1_000_000_000  # 7 days

# Canonical normalized-input bounds
MAX_NORMALIZATION_DEPTH: int = 4
MAX_NORMALIZATION_KEYS: int = 64
MAX_NORMALIZATION_ITEMS: int = 64
MAX_NORMALIZATION_STRING: int = 512
MAX_NORMALIZATION_NODES: int = 512
MAX_NORMALIZATION_REPRESENTATION: int = 16_384

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

_BOUNDED_REF_RE = re.compile(r"^[^\x00\r\n]{1,512}$")

# Known secret-bearing input field names. These fields are REMOVED from
# request identity entirely. They are never hashed; hashing a known secret
# does not make retaining its digest a safe identity practice, and removal
# is deterministic and sufficient for bounded repeat detection.
_SECRET_KEY_RE = re.compile(
    r"(^|[_-])("
    r"token|secret|password|passwd|api[_-]?key|apikey|authorization|auth|"
    r"credential|credentials|private[_-]?key|access[_-]?key|session[_-]?key|"
    r"bearer|cookie|passphrase|client[_-]?secret"
    r")([_-]|$)",
    re.IGNORECASE,
)

# Canonical representation markers for bounded long values. Long free-text
# values participate in request identity only through a one-way digest.
_LONG_VALUE_DIGEST_KEY: str = "__sha256__"
_LONG_VALUE_LENGTH_KEY: str = "__len__"


class ObservationCorrelationError(ValueError):
    """Typed fail-closed correlation/timing contract error."""


# ---------------------------------------------------------------------------
# Bounded validation helpers
# ---------------------------------------------------------------------------

def _validate_bounded_optional_str(value: object, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise ObservationCorrelationError(f"{label} must be a string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ObservationCorrelationError(f"{label} when provided must be non-empty")
    if len(v) > max_len:
        raise ObservationCorrelationError(f"{label} length {len(v)} exceeds max {max_len}")
    if "\x00" in v:
        raise ObservationCorrelationError(f"{label} must not contain NUL")
    return v


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ObservationCorrelationError(f"{label} must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ObservationCorrelationError(f"{label} must be 64 lowercase hex chars")
    return v


def _validate_utc_iso(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ObservationCorrelationError(f"{label} must be an ISO UTC string")
    s = value.strip()
    if not s:
        raise ObservationCorrelationError(f"{label} must be non-empty")
    candidate = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(candidate)
    except Exception as exc:
        raise ObservationCorrelationError(f"{label} invalid ISO datetime: {exc}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ObservationCorrelationError(f"{label} must be timezone-aware")
    return s


def utc_now_iso() -> str:
    """UTC wall timestamp (durable correlation fact only, never identity)."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Normalized request identity (privacy-safe, bounded, deterministic)
# ---------------------------------------------------------------------------

class _NormalizationBudget:
    def __init__(self) -> None:
        self.nodes = 0
        self.redacted = 0


def _normalize_value(value: object, depth: int, budget: _NormalizationBudget) -> Any:
    budget.nodes += 1
    if budget.nodes > MAX_NORMALIZATION_NODES:
        raise ObservationCorrelationError("normalized request exceeds node bound")
    if depth > MAX_NORMALIZATION_DEPTH:
        raise ObservationCorrelationError("normalized request exceeds depth bound")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > 2**63 - 1:
            raise ObservationCorrelationError("integer input exceeds bounded range")
        return value
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ObservationCorrelationError("non-finite float input rejected")
        return value
    if isinstance(value, str):
        if len(value) > MAX_NORMALIZATION_STRING:
            return {
                _LONG_VALUE_DIGEST_KEY: hashlib.sha256(value.encode("utf-8")).hexdigest(),
                _LONG_VALUE_LENGTH_KEY: len(value),
            }
        return value
    if isinstance(value, bytes):
        return {
            _LONG_VALUE_DIGEST_KEY: hashlib.sha256(value).hexdigest(),
            _LONG_VALUE_LENGTH_KEY: len(value),
        }
    if isinstance(value, Mapping):
        if len(value) > MAX_NORMALIZATION_KEYS:
            raise ObservationCorrelationError("mapping exceeds key bound")
        out: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            if not isinstance(raw_key, str):
                raise ObservationCorrelationError("non-string mapping key rejected")
            key = raw_key.strip()
            if not key or len(key) > MAX_NORMALIZATION_KEYS:
                raise ObservationCorrelationError("mapping key out of bounds")
            if _SECRET_KEY_RE.search(key):
                budget.redacted += 1
                continue
            out[key] = _normalize_value(raw_val, depth + 1, budget)
        return out
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_NORMALIZATION_ITEMS:
            raise ObservationCorrelationError("sequence exceeds item bound")
        return [_normalize_value(item, depth + 1, budget) for item in value]
    raise ObservationCorrelationError(
        f"unsupported input value type for request identity: {type(value).__name__}"
    )


def compute_normalized_request_digest(
    operation_name: object,
    inputs: object,
) -> str | None:
    """Deterministic privacy-safe request identity digest.

    Returns ``sha256(canonical_json(normalized bounded input))`` or ``None``
    when the input cannot be safely normalized (fail closed — an unavailable
    digest is a completeness fact, never a fabricated value).

    Guarantees:
    * raw inputs never retained; long free-text participates only through a
      one-way digest;
    * known secret-bearing field names are removed entirely (never hashed);
    * mapping key order does not affect the digest;
    * same logical input → same digest.
    """
    if not isinstance(operation_name, str) or type(operation_name) is not str:
        return None
    op = operation_name.strip()
    if not op or len(op) > 128:
        return None
    payload = inputs if isinstance(inputs, Mapping) else inputs
    try:
        budget = _NormalizationBudget()
        normalized = _normalize_value(payload, 0, budget)
        canonical = canonicalize(
            {"input": normalized, "operation_name": op},
            path="normalized_request",
        )
        j = canonical_json(canonical)
        if len(j) > MAX_NORMALIZATION_REPRESENTATION:
            return None
        return hashlib.sha256(j.encode("utf-8")).hexdigest()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tool observation correlation context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolObservationContext:
    """Bounded optional correlation facts for one production observation.

    All fields are optional: operations that legitimately occur outside a
    Work task carry no forced child-task identity.
    """

    project_id: str | None = None
    worktree_id: str | None = None
    work_role: AgentWorkRole | None = None
    session_ref: str | None = None
    parent_session_ref: str | None = None
    canonical_task_id: str | None = None
    milestone_ref: str | None = None
    work_item_ref: str | None = None
    run_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_bounded_optional_str(self.project_id, "project_id", 96))
        object.__setattr__(self, "worktree_id", _validate_bounded_optional_str(self.worktree_id, "worktree_id", 128))
        if self.work_role is not None:
            if isinstance(self.work_role, AgentWorkRole):
                pass
            elif isinstance(self.work_role, str) and type(self.work_role) is str:
                object.__setattr__(self, "work_role", parse_agent_work_role(self.work_role))
            else:
                raise ObservationCorrelationError(
                    f"work_role must be AgentWorkRole or string, got {type(self.work_role).__name__}"
                )
        object.__setattr__(self, "session_ref", _validate_bounded_optional_str(self.session_ref, "session_ref", MAX_SESSION_REF_LENGTH))
        object.__setattr__(
            self,
            "parent_session_ref",
            _validate_bounded_optional_str(self.parent_session_ref, "parent_session_ref", MAX_PARENT_SESSION_REF_LENGTH),
        )
        object.__setattr__(
            self,
            "canonical_task_id",
            _validate_bounded_optional_str(self.canonical_task_id, "canonical_task_id", MAX_CANONICAL_TASK_ID_LENGTH),
        )
        object.__setattr__(self, "milestone_ref", _validate_bounded_optional_str(self.milestone_ref, "milestone_ref", MAX_MILESTONE_REF_LENGTH))
        object.__setattr__(self, "work_item_ref", _validate_bounded_optional_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        object.__setattr__(self, "run_ref", _validate_bounded_optional_str(self.run_ref, "run_ref", MAX_RUN_REF_LENGTH))

    @property
    def is_authority(self) -> bool:
        return False

    def merged(self, base: "ToolObservationContext") -> "ToolObservationContext":
        """Field-wise fallback merge: explicit facts win over process scope."""
        def pick(a: Any, b: Any) -> Any:
            return a if a is not None else b

        return ToolObservationContext(
            project_id=pick(self.project_id, base.project_id),
            worktree_id=pick(self.worktree_id, base.worktree_id),
            work_role=pick(self.work_role, base.work_role),
            session_ref=pick(self.session_ref, base.session_ref),
            parent_session_ref=pick(self.parent_session_ref, base.parent_session_ref),
            canonical_task_id=pick(self.canonical_task_id, base.canonical_task_id),
            milestone_ref=pick(self.milestone_ref, base.milestone_ref),
            work_item_ref=pick(self.work_item_ref, base.work_item_ref),
            run_ref=pick(self.run_ref, base.run_ref),
        )


def _semantic_ref_value(ref: object) -> str | None:
    if ref is None:
        return None
    value = getattr(ref, "ref", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return None


def context_from_binding(binding: object) -> ToolObservationContext:
    """Mechanically extract bounded correlation facts from a trusted binding.

    No policy, no authority, no semantic interpretation: reads already
    trusted identity carriers with defensive attribute access. Missing facts
    stay ``None`` (never invented).
    """
    def _safe_attr(obj: object, name: str) -> Any:
        try:
            return getattr(obj, name, None)
        except Exception:
            return None

    project_id = _safe_attr(binding, "project_id")
    worktree_id = _safe_attr(binding, "worktree_id")
    canonical_task_id = _safe_attr(binding, "canonical_task_id")

    handoff = _safe_attr(binding, "handoff")
    work_role = None
    milestone_ref = None
    work_item_ref = None
    if handoff is not None:
        work_role = _safe_attr(handoff, "work_role")
        milestone_ref = _semantic_ref_value(_safe_attr(handoff, "milestone_ref"))
        work_item_ref = _semantic_ref_value(_safe_attr(handoff, "work_item_ref"))
    if work_role is None:
        surface = _safe_attr(binding, "tool_surface")
        if surface is not None:
            work_role = _safe_attr(surface, "work_role")

    session_ref = _safe_attr(binding, "session_ref")
    parent_session_ref = _safe_attr(binding, "parent_session_ref")
    run_ref = _safe_attr(binding, "run_ref")

    def _str_or_none(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    try:
        return ToolObservationContext(
            project_id=_str_or_none(project_id),
            worktree_id=_str_or_none(worktree_id),
            work_role=work_role if isinstance(work_role, (AgentWorkRole, str)) else None,
            session_ref=_str_or_none(session_ref),
            parent_session_ref=_str_or_none(parent_session_ref),
            canonical_task_id=_str_or_none(canonical_task_id),
            milestone_ref=milestone_ref,
            work_item_ref=work_item_ref,
            run_ref=_str_or_none(run_ref),
        )
    except Exception:
        return ToolObservationContext()


# ---------------------------------------------------------------------------
# Tool observation timing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolObservationTiming:
    """Bounded Tool execution timing facts.

    Duration is measured from a monotonic clock when available
    (``timing_basis="monotonic"``); wall UTC stamps are durable correlation
    facts only and never an identity source.
    """

    duration_ns: int | None = None
    started_at_utc: str | None = None
    ended_at_utc: str | None = None
    timing_basis: str | None = None

    def __post_init__(self) -> None:
        if self.duration_ns is not None:
            if type(self.duration_ns) is not int:
                raise ObservationCorrelationError("duration_ns must be int or None")
            if self.duration_ns < 0:
                raise ObservationCorrelationError("duration_ns must be >= 0")
            if self.duration_ns > MAX_DURATION_NS:
                raise ObservationCorrelationError("duration_ns exceeds bound")
        if self.started_at_utc is not None:
            object.__setattr__(self, "started_at_utc", _validate_utc_iso(self.started_at_utc, "started_at_utc"))
        if self.ended_at_utc is not None:
            object.__setattr__(self, "ended_at_utc", _validate_utc_iso(self.ended_at_utc, "ended_at_utc"))
        if self.timing_basis is not None:
            if self.timing_basis not in ("monotonic", "wall"):
                raise ObservationCorrelationError("timing_basis must be monotonic|wall|None")
            if self.timing_basis == "monotonic" and self.duration_ns is None:
                raise ObservationCorrelationError("monotonic timing requires duration_ns")

    @property
    def is_authority(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.duration_ns is not None:
            d["duration_ns"] = self.duration_ns
        if self.started_at_utc is not None:
            d["started_at_utc"] = self.started_at_utc
        if self.ended_at_utc is not None:
            d["ended_at_utc"] = self.ended_at_utc
        if self.timing_basis is not None:
            d["timing_basis"] = self.timing_basis
        return d


def build_tool_timing(
    *,
    started_monotonic_ns: int | None,
    ended_monotonic_ns: int | None,
    started_at_utc: str | None = None,
    ended_at_utc: str | None = None,
) -> ToolObservationTiming:
    """Deterministic timing assembly from caller-supplied clocks.

    Callers own clock reads; this function performs no implicit clock read,
    keeping the value contract pure and testable.
    """
    duration_ns: int | None = None
    basis: str | None = None
    if started_monotonic_ns is not None and ended_monotonic_ns is not None:
        if type(started_monotonic_ns) is not int or type(ended_monotonic_ns) is not int:
            raise ObservationCorrelationError("monotonic clock values must be int nanoseconds")
        if ended_monotonic_ns < started_monotonic_ns:
            raise ObservationCorrelationError("monotonic end precedes start")
        duration_ns = ended_monotonic_ns - started_monotonic_ns
        basis = "monotonic"
    return ToolObservationTiming(
        duration_ns=duration_ns,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        timing_basis=basis,
    )


# ---------------------------------------------------------------------------
# Process-scoped observation scope (run/session identity where available)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeObservationScope:
    """Process-scoped run/session correlation defaults (non-authoritative)."""

    run_ref: str | None = None
    session_ref: str | None = None
    parent_session_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_ref", _validate_bounded_optional_str(self.run_ref, "run_ref", MAX_RUN_REF_LENGTH))
        object.__setattr__(self, "session_ref", _validate_bounded_optional_str(self.session_ref, "session_ref", MAX_SESSION_REF_LENGTH))
        object.__setattr__(
            self,
            "parent_session_ref",
            _validate_bounded_optional_str(self.parent_session_ref, "parent_session_ref", MAX_PARENT_SESSION_REF_LENGTH),
        )

    def as_context(self) -> ToolObservationContext:
        return ToolObservationContext(
            run_ref=self.run_ref,
            session_ref=self.session_ref,
            parent_session_ref=self.parent_session_ref,
        )


_SCOPE_LOCK = threading.Lock()
_PROCESS_SCOPE: RuntimeObservationScope = RuntimeObservationScope()
_PROCESS_SCOPE_INSTALLED: bool = False


def install_runtime_observation_scope(scope: RuntimeObservationScope | None) -> None:
    """Install the process-scoped observation correlation defaults."""
    global _PROCESS_SCOPE, _PROCESS_SCOPE_INSTALLED
    if scope is not None and not isinstance(scope, RuntimeObservationScope):
        raise TypeError(f"scope must be RuntimeObservationScope or None, got {type(scope).__name__}")
    with _SCOPE_LOCK:
        _PROCESS_SCOPE = scope if scope is not None else RuntimeObservationScope()
        _PROCESS_SCOPE_INSTALLED = scope is not None


def get_runtime_observation_scope() -> RuntimeObservationScope:
    with _SCOPE_LOCK:
        return _PROCESS_SCOPE


def runtime_observation_scope_installed() -> bool:
    with _SCOPE_LOCK:
        return _PROCESS_SCOPE_INSTALLED


def clear_runtime_observation_scope() -> None:
    install_runtime_observation_scope(None)


__all__ = [
    "OBSERVATION_CORRELATION_CONTRACT_VERSION",
    "LEGACY_OBSERVATION_CONTRACT_VERSION",
    "OBSERVATION_CORRELATION_IS_AUTHORITY",
    "NORMALIZED_REQUEST_DIGEST_IS_AUTHORITY",
    "WALL_CLOCK_IS_IDENTITY_SOURCE",
    "CORRELATION_FACTS_CHANGE_EXECUTION_RESULT",
    "RAW_TOOL_INPUT_CAPTURED",
    "RAW_PROMPT_CAPTURED",
    "RAW_TOOL_OUTPUT_CAPTURED",
    "SECRET_ENV_CAPTURED",
    "KNOWN_SECRET_FIELDS_EXCLUDED",
    "SECRET_VALUE_HASHED",
    "MONOTONIC_DURATION_PREFERRED",
    "DURATION_IS_IDENTITY_SOURCE",
    "OBSERVATION_CORRELATION_BOUNDED",
    "OBSERVATION_CORRELATION_FAIL_CLOSED",
    "SECOND_TELEMETRY_SYSTEM_CREATED",
    "SECOND_METRIC_TAXONOMY_CREATED",
    "MAX_SESSION_REF_LENGTH",
    "MAX_PARENT_SESSION_REF_LENGTH",
    "MAX_CANONICAL_TASK_ID_LENGTH",
    "MAX_MILESTONE_REF_LENGTH",
    "MAX_WORK_ITEM_REF_LENGTH",
    "MAX_RUN_REF_LENGTH",
    "MAX_DURATION_NS",
    "MAX_NORMALIZATION_DEPTH",
    "MAX_NORMALIZATION_KEYS",
    "MAX_NORMALIZATION_ITEMS",
    "MAX_NORMALIZATION_STRING",
    "MAX_NORMALIZATION_NODES",
    "MAX_NORMALIZATION_REPRESENTATION",
    "ObservationCorrelationError",
    "utc_now_iso",
    "compute_normalized_request_digest",
    "ToolObservationContext",
    "context_from_binding",
    "ToolObservationTiming",
    "build_tool_timing",
    "RuntimeObservationScope",
    "install_runtime_observation_scope",
    "get_runtime_observation_scope",
    "runtime_observation_scope_installed",
    "clear_runtime_observation_scope",
]
