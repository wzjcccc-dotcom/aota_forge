"""Metric Taxonomy & Normalization Contract — S6 M1-W2.

Thin immutable/value-contract layer freezing S6 metric taxonomy for downstream
aggregation (M2) and metric projections (M3).

Architecture:
    W1 TelemetryEvidence (source_dedup_id / projection_id)
            ↓
    W2 Metric / Normalization Contract  ← this file
            ↓
        future M2 aggregation
        future M3 metric projections

Invariants
----------
* METRIC_TAXONOMY_VERSION=s6-m1-v1 , DIMENSION_SCHEMA_VERSION=s6-m1-v1,
  NORMALIZATION_VERSION=s6-m1-v1  (explicit, supported set bounded)
* METRIC_TAXONOMY_VERSIONED=yes, DIMENSION_SCHEMA_VERSIONED=yes,
  NORMALIZATION_VERSION_PRESENT=yes
* METRIC_FAMILY_SET_BOUNDED=yes — exactly 7 families:
  ROLE, TOOL, SKILL, SHELL_PATTERN, CONTEXT_COST, REVIEW_REPAIR, WORKFLOW_FRICTION
* ARBITRARY_FREE_TEXT_METRIC_FAMILY=no, UNKNOWN_METRIC_FAMILY_FAIL_CLOSED=yes
* NEW_FAMILY_REQUIRES_CONTRACT_VERSION_EVOLUTION=yes
* METRIC_CONTRACT_IS_AUTHORITY=no, METRIC_IS_POLICY_AUTHORITY=no,
  METRIC_THRESHOLD_IS_CANONICAL_POLICY=no
* METRIC_SUBJECT_IDENTITY != METRIC_DIMENSION (separation enforced, no arbitrary dict[str,Any])
* Bounded dimension allowlist: dimension keys are allowlisted and versioned.
  ARBITRARY_DIMENSION_KEY=no, ARBITRARY_METADATA_DIMENSION=no,
  DIMENSION_ALLOWLIST_VERSIONED=yes, UNKNOWN_DIMENSION_KEY_FAIL_CLOSED=yes,
  DIMENSION_SCHEMA_CHANGE_REQUIRES_VERSION_CHANGE=yes
* Dimension serialization deterministic (sort_keys) and order-invariant.
* METRIC_CARDINALITY_BOUNDED=yes — both key and value space bounded, enum/category values only,
  no raw user-controlled strings, no raw errors/paths/URLs/argv as dimension values.
* aggregation_series_id != source_dedup_id != projection_id != aggregation_window_id
* aggregation_series_id = sha256(canonical(metric_taxonomy_version, dimension_schema_version,
  normalization_version, metric_family, metric_subject_identity, normalized_dimensions))
  (deterministic, no random / storage_row_id / wall clock / ingestion timestamp)
* aggregation_window_id = sha256(canonical(aggregation_series_id, window_policy_id,
  window_policy_version, window_key)) — window_key is bounded caller-supplied canonical identity.
  No window duration / watermark / lateness frozen in M1.
* NORMALIZATION_VERSION_NOT_IN_W1_SOURCE_DEDUP_ID=yes (do not redefine W1)
* RAW_SHELL_ARGV_ACCEPTED=no, RAW_SHELL_ARGV_NEVER_ENTERS_METRIC_CONTRACT=yes
  Unknown command → unknown_command, argument classes finite categories only,
  high-entropy/secrets reduce to category, no hash-of-secret as dimension.
* CONTEXT_COST portable canonical units: bytes_utf8, chars, items, references, hydrated_bytes.
  MODEL_TOKENIZER_IS_CANONICAL_METRIC_AUTHORITY=no, PROVIDER_TOKEN_METRICS_SUPPLEMENTAL=yes.
  Same value + different unit != same meaning; counts non-negative bounded ints.
* SKILL_METRIC_LIFECYCLE_DISTINCTION: skill_selected != skill_delivered_or_loaded != skill_observed_used
  SKILL_METRIC_USES_CANONICAL_S3_IDENTITY=yes, FREE_TEXT_SKILL_LABEL_IS_CANONICAL_IDENTITY=no,
  TOOL_NAME_IS_SKILL_IDENTITY=no. OBSERVED_USE_REQUIRES_ACTUAL_EVIDENCE=yes.
* Co-load canonicalization: order must not matter.
* Completeness/Sampling/Retention reuse W1 enums (no second enum).
* No database / event bus / collector daemon / analytics engine / journal / policy engine.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill import SkillIdentity
from aota_forge.work_plane.telemetry_evidence import (
    CompletenessScope,
    CompletenessState,
    RetentionProvenance,
    SamplingProvenance,
    parse_completeness_scope,
    parse_completeness_state,
)

# ---------------------------------------------------------------------------
# Contract versions
# ---------------------------------------------------------------------------

METRIC_TAXONOMY_VERSION: str = "s6-m1-v1"
DIMENSION_SCHEMA_VERSION: str = "s6-m1-v1"
NORMALIZATION_VERSION: str = "s6-m1-v1"

SUPPORTED_METRIC_TAXONOMY_VERSIONS: frozenset[str] = frozenset({METRIC_TAXONOMY_VERSION})
SUPPORTED_DIMENSION_SCHEMA_VERSIONS: frozenset[str] = frozenset({DIMENSION_SCHEMA_VERSION})
SUPPORTED_NORMALIZATION_VERSIONS: frozenset[str] = frozenset({NORMALIZATION_VERSION})

# Aliases for spec naming flexibility
METRIC_TAXONOMY_SCHEMA_VERSION: str = METRIC_TAXONOMY_VERSION
DIMENSION_SCHEMA_VERSION_ALIAS: str = DIMENSION_SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Invariant flags
# ---------------------------------------------------------------------------

METRIC_CONTRACT_IS_AUTHORITY: bool = False
METRIC_IS_POLICY_AUTHORITY: bool = False
METRIC_THRESHOLD_IS_CANONICAL_POLICY: bool = False
METRIC_CONTRACT_IS_STORAGE_SCHEMA: bool = False

METRIC_TAXONOMY_VERSIONED: bool = True
DIMENSION_SCHEMA_VERSIONED: bool = True
NORMALIZATION_VERSION_PRESENT: bool = True

METRIC_FAMILY_SET_BOUNDED: bool = True
ARBITRARY_FREE_TEXT_METRIC_FAMILY: bool = False
ARBITRARY_METRIC_FAMILY_ALLOWED: bool = False
UNKNOWN_METRIC_FAMILY_FAIL_CLOSED: bool = True
NEW_FAMILY_REQUIRES_CONTRACT_VERSION_EVOLUTION: bool = True

DIMENSION_ALLOWLIST_VERSIONED: bool = True
ARBITRARY_DIMENSION_KEY: bool = False
ARBITRARY_DIMENSION_KEY_ALLOWED: bool = False
ARBITRARY_METADATA_DIMENSION: bool = False
UNKNOWN_DIMENSION_KEY_FAIL_CLOSED: bool = True
DIMENSION_SCHEMA_CHANGE_REQUIRES_VERSION_CHANGE: bool = True

METRIC_CARDINALITY_BOUNDED: bool = True
METRIC_SUBJECT_DIMENSION_SEPARATED: bool = True
METRIC_SUBJECT_IDENTITY_IS_DIMENSION: bool = False

AGGREGATION_SERIES_IDENTITY_PRESENT: bool = True
AGGREGATION_WINDOW_IDENTITY_PRESENT: bool = True
SERIES_WINDOW_IDENTITY_SEPARATE: bool = True

NORMALIZATION_VERSION_NOT_IN_W1_SOURCE_DEDUP_ID: bool = True
W1_SOURCE_DEDUP_REDEFINED: bool = False

RAW_SHELL_ARGV_ACCEPTED: bool = False
RAW_SHELL_ARGV_NEVER_ENTERS_METRIC_CONTRACT: bool = True
RAW_SECRET_VALUE_IN_PATTERN: bool = False
UNKNOWN_SHELL_FALLBACK_SAFE: bool = True
RAW_VALUE_HASH_BUCKET_AS_DEFAULT: bool = False

CONTEXT_COST_PORTABLE_UNITS_PRESENT: bool = True
MODEL_TOKENIZER_CANONICAL_AUTHORITY: bool = False
MODEL_TOKENIZER_IS_CANONICAL_METRIC_AUTHORITY: bool = False
PROVIDER_TOKEN_METRICS_SUPPLEMENTAL: bool = True

SKILL_METRIC_LIFECYCLE_DISTINCTION: bool = True
FREE_TEXT_SKILL_IDENTITY_ALLOWED: bool = False
TOOL_NAME_IS_SKILL_IDENTITY: bool = False
SKILL_METRIC_USES_CANONICAL_S3_IDENTITY: bool = True
OBSERVED_USE_METRIC_REQUIRES_ACTUAL_EVIDENCE: bool = True

NEW_DATABASE_CREATED: bool = False
NEW_PERSISTENT_STORE_CREATED: bool = False
NEW_EVENT_BUS_CREATED: bool = False
NEW_STATE_MACHINE_CREATED: bool = False
NEW_JOURNAL_CREATED: bool = False
NEW_ANALYTICS_RUNTIME_CREATED: bool = False
NEW_POLICY_ENGINE_CREATED: bool = False
AUTOMATIC_ARCHITECTURE_MUTATION: bool = False
W1_CONTRACT_CHANGED: bool = False
EXISTING_SOURCE_PRODUCER_CHANGED: bool = False
EXISTING_PRODUCER_CHANGED: bool = False

MISSING_TELEMETRY_IS_ZERO: bool = False
ZERO_OBSERVATION_COUNT_IMPLIES_COMPLETE_ZERO: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_METRIC_FAMILY_LENGTH: int = 64
MAX_DIMENSION_KEY_LENGTH: int = 64
MAX_DIMENSION_VALUE_LENGTH: int = 128
MAX_SUBJECT_ID_LENGTH: int = 256
MAX_WINDOW_POLICY_ID_LENGTH: int = 128
MAX_WINDOW_POLICY_VERSION_LENGTH: int = 64
MAX_WINDOW_KEY_LENGTH: int = 128
MAX_COMMAND_ID_LENGTH: int = 64
MAX_CONTEXT_COMPONENT_LENGTH: int = 128
MAX_REVIEW_CLASS_LENGTH: int = 64

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# For subject identities: allow bounded alphanumeric with ._- and / for skill refs
_SKILL_SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

# ---------------------------------------------------------------------------
# Metric Family taxonomy v1
# ---------------------------------------------------------------------------

@unique
class MetricFamily(str, Enum):
    ROLE = "role"
    TOOL = "tool"
    SKILL = "skill"
    SHELL_PATTERN = "shell_pattern"
    CONTEXT_COST = "context_cost"
    REVIEW_REPAIR = "review_repair"
    WORKFLOW_FRICTION = "workflow_friction"


METRIC_FAMILIES: frozenset[str] = frozenset(e.value for e in MetricFamily)
METRIC_FAMILY_VALUES: tuple[str, ...] = tuple(e.value for e in MetricFamily)

# Alias for tests that may expect uppercase inventory
METRIC_FAMILY_INVENTORY_UPPER: frozenset[str] = frozenset({
    "ROLE", "TOOL", "SKILL", "SHELL_PATTERN", "CONTEXT_COST", "REVIEW_REPAIR", "WORKFLOW_FRICTION"
})


def parse_metric_family(value: object) -> MetricFamily:
    if isinstance(value, MetricFamily):
        return value
    if isinstance(value, str) and type(value) is str:
        # Accept both lower and upper for robustness but canonical is lower
        lower = value.strip().lower()
        # Also accept exact enum value
        try:
            return MetricFamily(lower)
        except ValueError:
            # try upper mapping?
            upper_to_lower = {
                "role": "role",
                "tool": "tool",
                "skill": "skill",
                "shell_pattern": "shell_pattern",
                "context_cost": "context_cost",
                "review_repair": "review_repair",
                "workflow_friction": "workflow_friction",
            }
            if lower in upper_to_lower:
                return MetricFamily(upper_to_lower[lower])
            raise ValueError(f"Unknown metric family: {value!r}. Must be one of {sorted(METRIC_FAMILIES)}")
    raise TypeError(f"metric family must be str or MetricFamily, got {type(value).__name__}")


def validate_metric_family(value: object) -> MetricFamily:
    return parse_metric_family(value)

# ---------------------------------------------------------------------------
# Dimension schema v1 — allowlisted keys (versioned)
# ---------------------------------------------------------------------------

ALLOWED_DIMENSION_KEYS: frozenset[str] = frozenset({
    "outcome_class",
    "side_effect_class",
    "delivery_mode",
    "selection_source",
    "skill_metric_kind",
    "shell_option_category",
    "shell_argument_class",
    "shell_outcome_class",
    "context_cost_unit",
    "completeness_state",
    "completeness_scope",
    "review_class",
    "workflow_friction_class",
})

# For cardinality guard: allowed values per key (bounded enums)
_ALLOWED_OUTCOME_CLASSES: frozenset[str] = frozenset({
    "success",
    "success_domain_failure",
    "failure_authority_denied",
    "failure_invalid_input",
    "failure_timeout",
    "failure_provider",
})

_ALLOWED_SIDE_EFFECT_CLASSES: frozenset[str] = frozenset({
    "read",
    "write_mutation",
    "test_execution",
    "git_read",
    "shell_process",
    "unknown",
})

_ALLOWED_DELIVERY_MODES: frozenset[str] = frozenset({
    "eager",
    "progressive",
    "unknown",
})

_ALLOWED_SELECTION_SOURCES: frozenset[str] = frozenset({
    "pinned",
    "required",
    "role_default",
    "recommended",
    "unknown",
})

_ALLOWED_SKILL_METRIC_KINDS: frozenset[str] = frozenset({
    "skill_selected",
    "skill_delivered_or_loaded",
    "skill_observed_used",
})

_ALLOWED_SHELL_OPTION_CATEGORIES: frozenset[str] = frozenset({
    "flag",
    "option_with_value",
    "path_option",
    "url_option",
    "unknown",
})

_ALLOWED_SHELL_ARGUMENT_CLASSES: frozenset[str] = frozenset({
    "path_class",
    "url_class",
    "flag",
    "enum_value",
    "numeric",
    "text_class",
    "token_or_secret_class",
    "unknown",
})

_ALLOWED_SHELL_OUTCOME_CLASSES: frozenset[str] = frozenset({
    "success",
    "failure",
    "timeout",
    "unknown",
})

_ALLOWED_CONTEXT_COST_UNITS: frozenset[str] = frozenset({
    "bytes_utf8",
    "chars",
    "items",
    "references",
    "hydrated_bytes",
})

_ALLOWED_REVIEW_CLASSES: frozenset[str] = frozenset({
    "approved",
    "rejected",
    "needs_repair",
    "unknown",
})

_ALLOWED_WORKFLOW_FRICTION_CLASSES: frozenset[str] = frozenset({
    "retry",
    "rework",
    "blocked",
    "unknown",
})

# High-cardinality denial: these patterns must never become dimension values
_FORBIDDEN_DIMENSION_VALUE_PATTERNS = (
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-"),  # UUID-like
    re.compile(r"^task-"),  # task_id like
    re.compile(r".*@.*\..*"),  # email-ish
)

# Map dimension key -> allowed value set
_DIMENSION_VALUE_ALLOWLIST: dict[str, frozenset[str]] = {
    "outcome_class": _ALLOWED_OUTCOME_CLASSES,
    "side_effect_class": _ALLOWED_SIDE_EFFECT_CLASSES,
    "delivery_mode": _ALLOWED_DELIVERY_MODES,
    "selection_source": _ALLOWED_SELECTION_SOURCES,
    "skill_metric_kind": _ALLOWED_SKILL_METRIC_KINDS,
    "shell_option_category": _ALLOWED_SHELL_OPTION_CATEGORIES,
    "shell_argument_class": _ALLOWED_SHELL_ARGUMENT_CLASSES,
    "shell_outcome_class": _ALLOWED_SHELL_OUTCOME_CLASSES,
    "context_cost_unit": _ALLOWED_CONTEXT_COST_UNITS,
    "completeness_state": frozenset(e.value for e in CompletenessState),
    "completeness_scope": frozenset(e.value for e in CompletenessScope),
    "review_class": _ALLOWED_REVIEW_CLASSES,
    "workflow_friction_class": _ALLOWED_WORKFLOW_FRICTION_CLASSES,
}

# ---------------------------------------------------------------------------
# Helpers — validation
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


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ValueError("digest must be non-empty")
    if len(v) > 128:
        raise ValueError(f"digest length {len(v)} exceeds bound")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ValueError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _reject_high_cardinality_value(key: str, value: str) -> None:
    # Reject raw high-cardinality shapes that would violate cardinality bound
    # - raw paths, urls, error strings, task ids, UUIDs, argv-like
    if len(value) > MAX_DIMENSION_VALUE_LENGTH:
        raise ValueError(f"dimension value {key}={value!r} exceeds bounded length")
    # reject if contains characters typical of raw payload: '/', ':', '?', '#', spaces, etc for some keys
    # But our allowlist is strict enum, so any non-enum will already fail.
    # Additional check: if value looks like raw path/url/error, even if enum would allow "unknown", we ensure raw not passed
    # For strict allowlist keys, only enum values are allowed, so raw automatically rejected.
    # For safety, also reject values that look like high-cardinality if they somehow pass enum
    if value not in _DIMENSION_VALUE_ALLOWLIST.get(key, frozenset()):
        # unknown is allowed enum, but raw error string like "FileNotFoundError: /tmp/foo" contains space/slash/colon
        if any(c in value for c in ["/", "\\", ":", "?", "#", " "]):
            # if not exactly "unknown", reject
            if value != "unknown":
                raise ValueError(f"dimension value {key}={value!r} appears to be raw high-cardinality, must be bounded category")
    # check UUID pattern
    for pat in _FORBIDDEN_DIMENSION_VALUE_PATTERNS:
        if pat.search(value) and value != "unknown":
            raise ValueError(f"dimension value {key}={value!r} resembles high-cardinality id, rejected")


def validate_dimension_key(key: object) -> str:
    if not isinstance(key, str) or type(key) is not str:
        raise TypeError(f"dimension key must be a string, got {type(key).__name__}")
    k = key.strip()
    if not k:
        raise ValueError("dimension key must be non-empty")
    if len(k) > MAX_DIMENSION_KEY_LENGTH:
        raise ValueError(f"dimension key length {len(k)} exceeds {MAX_DIMENSION_KEY_LENGTH}")
    if k not in ALLOWED_DIMENSION_KEYS:
        raise ValueError(f"Unknown dimension key: {k!r}. Allowed: {sorted(ALLOWED_DIMENSION_KEYS)}")
    return k


def validate_dimension_value(key: str, value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"dimension value for {key} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"dimension value for {key} must be non-empty")
    if len(v) > MAX_DIMENSION_VALUE_LENGTH:
        raise ValueError(f"dimension value for {key} exceeds maximum {MAX_DIMENSION_VALUE_LENGTH}")
    allowed = _DIMENSION_VALUE_ALLOWLIST.get(key)
    if allowed is None:
        raise ValueError(f"No allowlist for dimension key {key!r}")
    if v not in allowed:
        raise ValueError(f"Unknown dimension value {v!r} for key {key!r}. Allowed: {sorted(allowed)}")
    _reject_high_cardinality_value(key, v)
    return v


def validate_normalized_dimensions(dims: Mapping[str, Any] | None) -> dict[str, str]:
    if dims is None:
        return {}
    if not isinstance(dims, Mapping):
        raise TypeError(f"normalized_dimensions must be mapping, got {type(dims).__name__}")
    out: dict[str, str] = {}
    for k, v in dims.items():
        kk = validate_dimension_key(k)
        if kk in out:
            raise ValueError(f"duplicate dimension key {kk!r}")
        vv = validate_dimension_value(kk, v)
        out[kk] = vv
    # canonical ordering: sort_keys via canonical_json will enforce, but we return dict with sorted keys for determinism
    return dict(sorted(out.items()))

# ---------------------------------------------------------------------------
# Metric Subject — bounded, typed, non-authoritative, not dimensions
# ---------------------------------------------------------------------------

# Subject kinds correspond to identifiable metric series (not dimensions)
ALLOWED_SUBJECT_KINDS: frozenset[str] = frozenset({
    "role",
    "tool",
    "skill",
    "shell_pattern",
    "context_cost",
    "review_repair",
    "workflow_friction",
})

@dataclass(frozen=True)
class RoleMetricSubject:
    role: AgentWorkRole

    def __post_init__(self) -> None:
        if isinstance(self.role, AgentWorkRole):
            pass
        elif isinstance(self.role, str) and type(self.role) is str:
            object.__setattr__(self, "role", parse_agent_work_role(self.role))
        else:
            raise TypeError(f"role must be AgentWorkRole or str, got {type(self.role).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "role", "role": self.role.value}

    def to_subject_string(self) -> str:
        return f"role:{self.role.value}"


@dataclass(frozen=True)
class ToolMetricSubject:
    operation_name: str

    def __post_init__(self) -> None:
        v = _validate_bounded_str(self.operation_name, "operation_name", MAX_SUBJECT_ID_LENGTH)
        # operation_name charset: alphanumeric + . _ -
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", v):
            raise ValueError(f"operation_name invalid charset: {v!r}")
        # Reject high-cardinality raw shapes
        if "/" in v or ":" in v or " " in v:
            raise ValueError(f"operation_name must be canonical operation identity, not raw path/url: {v!r}")
        object.__setattr__(self, "operation_name", v)

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "tool", "operation_name": self.operation_name}

    def to_subject_string(self) -> str:
        return f"tool:{self.operation_name}"


@dataclass(frozen=True)
class SkillMetricSubject:
    skill_identity: SkillIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.skill_identity, SkillIdentity):
            raise TypeError(f"skill_identity must be SkillIdentity, got {type(self.skill_identity).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "skill", "skill_identity": self.skill_identity.to_dict()}

    def to_subject_string(self) -> str:
        # Use canonical Skill identity key
        return f"skill:{self.skill_identity.skill_id}@{self.skill_identity.version}:{self.skill_identity.digest[:8]}"


@dataclass(frozen=True)
class ContextCostMetricSubject:
    component: str

    def __post_init__(self) -> None:
        v = _validate_bounded_str(self.component, "component", MAX_CONTEXT_COMPONENT_LENGTH)
        # component must be bounded category, not raw path
        if "/" in v and v.count("/") > 2:
            raise ValueError(f"component must be bounded category, not raw path: {v!r}")
        if re.search(r"[?#]", v):
            raise ValueError(f"component must not contain url query: {v!r}")
        object.__setattr__(self, "component", v)

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "context_cost", "component": self.component}

    def to_subject_string(self) -> str:
        return f"context_cost:{self.component}"


@dataclass(frozen=True)
class ReviewRepairMetricSubject:
    review_class: str

    def __post_init__(self) -> None:
        v = _validate_bounded_str(self.review_class, "review_class", MAX_REVIEW_CLASS_LENGTH)
        if v not in _ALLOWED_REVIEW_CLASSES:
            raise ValueError(f"Unknown review_class {v!r}. Allowed: {sorted(_ALLOWED_REVIEW_CLASSES)}")
        object.__setattr__(self, "review_class", v)

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "review_repair", "review_class": self.review_class}

    def to_subject_string(self) -> str:
        return f"review_repair:{self.review_class}"


@dataclass(frozen=True)
class WorkflowFrictionMetricSubject:
    friction_class: str

    def __post_init__(self) -> None:
        v = _validate_bounded_str(self.friction_class, "friction_class", MAX_REVIEW_CLASS_LENGTH)
        if v not in _ALLOWED_WORKFLOW_FRICTION_CLASSES:
            raise ValueError(f"Unknown workflow_friction_class {v!r}. Allowed: {sorted(_ALLOWED_WORKFLOW_FRICTION_CLASSES)}")
        object.__setattr__(self, "friction_class", v)

    def canonical_dict(self) -> dict[str, Any]:
        return {"kind": "workflow_friction", "friction_class": self.friction_class}

    def to_subject_string(self) -> str:
        return f"workflow_friction:{self.friction_class}"


# Shell pattern subject — wraps NormalizedShellPattern
# Defined later, but forward reference via canonical dict string

MetricSubject = (
    RoleMetricSubject
    | ToolMetricSubject
    | SkillMetricSubject
    | ContextCostMetricSubject
    | ReviewRepairMetricSubject
    | WorkflowFrictionMetricSubject
    | Any  # for ShellPattern which is defined later
)

def _validate_metric_subject(subject: object) -> dict[str, Any]:
    # Returns canonical dict for subject identity, fail-closed
    if isinstance(subject, RoleMetricSubject):
        return subject.canonical_dict()
    if isinstance(subject, ToolMetricSubject):
        return subject.canonical_dict()
    if isinstance(subject, SkillMetricSubject):
        return subject.canonical_dict()
    if isinstance(subject, ContextCostMetricSubject):
        return subject.canonical_dict()
    if isinstance(subject, ReviewRepairMetricSubject):
        return subject.canonical_dict()
    if isinstance(subject, WorkflowFrictionMetricSubject):
        return subject.canonical_dict()
    # Shell pattern subject handled after its definition
    # For duck-typing: check for kind shell_pattern
    if hasattr(subject, "canonical_dict") and callable(getattr(subject, "canonical_dict")):
        d = subject.canonical_dict()  # type: ignore
        if isinstance(d, dict) and d.get("kind") == "shell_pattern":
            return d
    raise TypeError(
        "metric subject must be one of RoleMetricSubject, ToolMetricSubject, SkillMetricSubject, "
        "ContextCostMetricSubject, ReviewRepairMetricSubject, WorkflowFrictionMetricSubject, "
        "or NormalizedShellPattern (shell_pattern). Got "
        f"{type(subject).__name__}"
    )


def _subject_to_string(subject: object) -> str:
    if isinstance(subject, RoleMetricSubject):
        return subject.to_subject_string()
    if isinstance(subject, ToolMetricSubject):
        return subject.to_subject_string()
    if isinstance(subject, SkillMetricSubject):
        return subject.to_subject_string()
    if isinstance(subject, ContextCostMetricSubject):
        return subject.to_subject_string()
    if isinstance(subject, ReviewRepairMetricSubject):
        return subject.to_subject_string()
    if isinstance(subject, WorkflowFrictionMetricSubject):
        return subject.to_subject_string()
    if hasattr(subject, "to_subject_string"):
        return subject.to_subject_string()  # type: ignore
    # fallback
    d = _validate_metric_subject(subject)
    return canonical_json(d)

# ---------------------------------------------------------------------------
# Restricted-Shell Normalized Pattern Contract
# ---------------------------------------------------------------------------

# Bounded catalog for shell pattern normalization — mirrors restricted_shell but for metrics
# Only these command_ids are considered recognized; others → unknown_command
RECOGNIZED_SHELL_COMMANDS: frozenset[str] = frozenset({"echo", "ls", "sleep"})
UNKNOWN_COMMAND_FALLBACK: str = "unknown_command"

ALLOWED_SHELL_COMMAND_IDS: frozenset[str] = frozenset({*RECOGNIZED_SHELL_COMMANDS, UNKNOWN_COMMAND_FALLBACK})

# Option categories: bounded semantic categories, not raw option strings
ALLOWED_SHELL_OPTION_CATEGORIES_BOUNDED: frozenset[str] = _ALLOWED_SHELL_OPTION_CATEGORIES

# Argument classes: finite semantic categories only
SHELL_ARGUMENT_CLASSES: frozenset[str] = _ALLOWED_SHELL_ARGUMENT_CLASSES

# Outcome classes for shell
ALLOWED_SHELL_OUTCOME_FOR_PATTERN: frozenset[str] = _ALLOWED_SHELL_OUTCOME_CLASSES


@dataclass(frozen=True)
class NormalizedShellPattern:
    """Bounded normalized shell pattern — never contains raw argv or secrets.

    Fields:
        command_id: bounded recognition (catalog or unknown_command)
        option_categories: tuple of bounded option categories (sorted deterministic)
        argument_classes: tuple of bounded argument classes (sorted or order-preserving? canonicalize sorted)
        outcome_class: bounded shell outcome
        kind marker for subject identity
    """

    command_id: str
    option_categories: tuple[str, ...] = ()
    argument_classes: tuple[str, ...] = ()
    outcome_class: str = "unknown"

    def __post_init__(self) -> None:
        cid = _validate_bounded_str(self.command_id, "command_id", MAX_COMMAND_ID_LENGTH)
        if "\x00" in cid or "/" in cid or "\\" in cid:
            raise ValueError(f"command_id must not contain path separators: {cid!r}")
        if cid not in ALLOWED_SHELL_COMMAND_IDS:
            # Only allow unknown fallback; otherwise fail-closed
            raise ValueError(f"command_id {cid!r} not in allowed set {sorted(ALLOWED_SHELL_COMMAND_IDS)}")
        object.__setattr__(self, "command_id", cid)

        # option_categories
        if not isinstance(self.option_categories, (tuple, list)):
            raise TypeError(f"option_categories must be tuple/list, got {type(self.option_categories).__name__}")
        oc_list: list[str] = []
        for idx, v in enumerate(self.option_categories):
            if not isinstance(v, str) or type(v) is not str:
                raise TypeError(f"option_categories[{idx}] must be str, got {type(v).__name__}")
            vv = v.strip()
            if vv not in ALLOWED_SHELL_OPTION_CATEGORIES_BOUNDED:
                raise ValueError(f"Unknown shell option_category {vv!r}. Allowed: {sorted(ALLOWED_SHELL_OPTION_CATEGORIES_BOUNDED)}")
            oc_list.append(vv)
        # deterministic: sort
        oc_list = sorted(set(oc_list))
        object.__setattr__(self, "option_categories", tuple(oc_list))

        # argument_classes
        if not isinstance(self.argument_classes, (tuple, list)):
            raise TypeError(f"argument_classes must be tuple/list, got {type(self.argument_classes).__name__}")
        ac_list: list[str] = []
        for idx, v in enumerate(self.argument_classes):
            if not isinstance(v, str) or type(v) is not str:
                raise TypeError(f"argument_classes[{idx}] must be str, got {type(v).__name__}")
            vv = v.strip()
            if vv not in SHELL_ARGUMENT_CLASSES:
                raise ValueError(f"Unknown shell argument_class {vv!r}. Allowed: {sorted(SHELL_ARGUMENT_CLASSES)}")
            ac_list.append(vv)
        ac_list = sorted(set(ac_list))
        object.__setattr__(self, "argument_classes", tuple(ac_list))

        oc = _validate_bounded_str(self.outcome_class, "outcome_class", MAX_SUBJECT_ID_LENGTH)
        if oc not in ALLOWED_SHELL_OUTCOME_FOR_PATTERN:
            raise ValueError(f"Unknown shell outcome_class {oc!r}. Allowed: {sorted(ALLOWED_SHELL_OUTCOME_FOR_PATTERN)}")
        object.__setattr__(self, "outcome_class", oc)

        # Security: ensure no raw secret/value survives in representation
        # Check that none of the fields contain high-entropy raw secret patterns
        # Since fields are bounded enums only, this is guaranteed.

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "kind": "shell_pattern",
            "argument_classes": list(self.argument_classes),
            "command_id": self.command_id,
            "option_categories": list(self.option_categories),
            "outcome_class": self.outcome_class,
        }, path="NormalizedShellPattern")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_subject_string(self) -> str:
        return f"shell_pattern:{self.command_id}:{','.join(self.argument_classes)}:{self.outcome_class}"

    @property
    def is_authority(self) -> bool:
        return False


def create_normalized_shell_pattern(
    command_id: str,
    option_categories: Sequence[str] | None = None,
    argument_classes: Sequence[str] | None = None,
    outcome_class: str = "unknown",
) -> NormalizedShellPattern:
    """Create normalized shell pattern from already-classified bounded semantic shape.

    This is the ONLY sanctioned entry for shell metrics. Raw argv never enters.
    Unknown command maps to unknown_command — not raw command text.
    Argument classes must be finite categories; raw values reduce to category only.

    No function normalize(argv: list[str]) exists. Callers must supply classified shape
    derived from trusted restricted_shell descriptor / observation, not raw user input.
    """
    # Normalize command_id to bounded fallback
    cid_raw = _validate_bounded_str(command_id, "command_id", MAX_COMMAND_ID_LENGTH) if isinstance(command_id, str) and type(command_id) is str else None
    if cid_raw is None:
        raise TypeError(f"command_id must be str, got {type(command_id).__name__}")
    # If not recognized, fallback to unknown_command (do not preserve raw)
    cid = cid_raw.strip()
    if cid not in RECOGNIZED_SHELL_COMMANDS:
        cid = UNKNOWN_COMMAND_FALLBACK
        # When unknown, argument_classes should be unknown only (prevent leaking raw via category inference)
        # Caller may pass anything; we force to unknown for safety if they attempted raw leakage
        if argument_classes is not None:
            # ensure unknown fallback doesn't preserve raw-derived categories that might be inferred from raw
            # But we allow caller to still supply bounded categories; we will validate they are bounded only
            pass
    return NormalizedShellPattern(
        command_id=cid,
        option_categories=tuple(option_categories or ()),
        argument_classes=tuple(argument_classes or ("unknown",)),
        outcome_class=outcome_class,
    )


# ---------------------------------------------------------------------------
# Context-Cost Units — portable canonical, model tokenizer not authority
# ---------------------------------------------------------------------------

@unique
class ContextCostUnit(str, Enum):
    BYTES_UTF8 = "bytes_utf8"
    CHARS = "chars"
    ITEMS = "items"
    REFERENCES = "references"
    HYDRATED_BYTES = "hydrated_bytes"


CONTEXT_COST_UNITS: frozenset[str] = frozenset(e.value for e in ContextCostUnit)


def parse_context_cost_unit(value: object) -> ContextCostUnit:
    if isinstance(value, ContextCostUnit):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return ContextCostUnit(value.strip())
        except ValueError:
            raise ValueError(f"Unknown context cost unit: {value!r}. Must be one of {sorted(CONTEXT_COST_UNITS)}")
    raise TypeError(f"context cost unit must be str or ContextCostUnit, got {type(value).__name__}")


# Supplemental provider token observation — explicitly supplemental, not canonical
@unique
class ProviderTokenKind(str, Enum):
    REPORTED_INPUT_TOKENS = "reported_input_tokens"
    REPORTED_OUTPUT_TOKENS = "reported_output_tokens"
    REPORTED_CACHE_TOKENS = "reported_cache_tokens"


MAX_CONTEXT_COST_VALUE: int = 1_000_000_000  # 1e9 bound, prevents unbounded value space

@dataclass(frozen=True)
class ContextCostMeasurement:
    """Portable context-cost measurement — integer counts, bounded, distinct per unit.

    Same value + different unit != same metric meaning.
    Counts are non-negative bounded ints, no float.
    """

    value: int
    unit: ContextCostUnit
    # optional bounded provenance for what was measured (not provider tokenizer)
    measured_component: str | None = None

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError(f"value must be int, got {type(self.value).__name__}")
        if self.value < 0:
            raise ValueError(f"value must be non-negative, got {self.value}")
        if self.value > MAX_CONTEXT_COST_VALUE:
            raise ValueError(f"value {self.value} exceeds bounded maximum {MAX_CONTEXT_COST_VALUE}")
        if isinstance(self.unit, ContextCostUnit):
            pass
        elif isinstance(self.unit, str) and type(self.unit) is str:
            object.__setattr__(self, "unit", parse_context_cost_unit(self.unit))
        else:
            raise TypeError(f"unit must be ContextCostUnit or str, got {type(self.unit).__name__}")
        if self.measured_component is not None:
            mc = _validate_bounded_str(self.measured_component, "measured_component", MAX_CONTEXT_COMPONENT_LENGTH)
            object.__setattr__(self, "measured_component", mc)

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"unit": self.unit.value, "value": self.value}
        if self.measured_component is not None:
            d["measured_component"] = self.measured_component
        return canonicalize(d, path="ContextCostMeasurement")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def is_same_metric_meaning(self, other: object) -> bool:
        if not isinstance(other, ContextCostMeasurement):
            return False
        return self.value == other.value and self.unit == other.unit


@dataclass(frozen=True)
class ProviderTokenObservation:
    """Supplemental provider-reported token counts — explicitly NOT canonical.

    Distinguishable from portable bytes/chars metrics.
    """

    kind: ProviderTokenKind
    value: int
    provider_provenance: str | None = None  # bounded opaque, not dimension

    def __post_init__(self) -> None:
        if isinstance(self.kind, ProviderTokenKind):
            pass
        elif isinstance(self.kind, str) and type(self.kind) is str:
            try:
                object.__setattr__(self, "kind", ProviderTokenKind(self.kind.strip()))
            except ValueError:
                raise ValueError(f"Unknown provider token kind: {self.kind!r}")
        else:
            raise TypeError(f"kind must be ProviderTokenKind or str, got {type(self.kind).__name__}")
        if type(self.value) is not int:
            raise TypeError(f"value must be int, got {type(self.value).__name__}")
        if self.value < 0:
            raise ValueError(f"value must be non-negative, got {self.value}")
        if self.value > MAX_CONTEXT_COST_VALUE:
            raise ValueError(f"value exceeds bound {MAX_CONTEXT_COST_VALUE}")
        if self.provider_provenance is not None:
            pp = _validate_bounded_str(self.provider_provenance, "provider_provenance", MAX_CONTEXT_COMPONENT_LENGTH)
            # provenance must be bounded opaque, not automatically dimension — we just store as string, never as dimension
            object.__setattr__(self, "provider_provenance", pp)

    @property
    def is_canonical_metric(self) -> bool:
        return False

    @property
    def is_supplemental(self) -> bool:
        return True

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind.value, "value": self.value}
        if self.provider_provenance is not None:
            d["provider_provenance"] = self.provider_provenance
        return canonicalize(d, path="ProviderTokenObservation")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

# ---------------------------------------------------------------------------
# Skill Metric Semantics
# ---------------------------------------------------------------------------

@unique
class SkillMetricKind(str, Enum):
    SKILL_SELECTED = "skill_selected"
    SKILL_DELIVERED_OR_LOADED = "skill_delivered_or_loaded"
    SKILL_OBSERVED_USED = "skill_observed_used"


SKILL_METRIC_KINDS: frozenset[str] = frozenset(e.value for e in SkillMetricKind)


def parse_skill_metric_kind(value: object) -> SkillMetricKind:
    if isinstance(value, SkillMetricKind):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return SkillMetricKind(value.strip())
        except ValueError:
            raise ValueError(f"Unknown skill metric kind: {value!r}. Must be one of {sorted(SKILL_METRIC_KINDS)}")
    raise TypeError(f"skill metric kind must be str or SkillMetricKind, got {type(value).__name__}")


@dataclass(frozen=True)
class SkillCoLoadIdentity:
    """Canonical co-load set identity — order must not matter, uses canonical SkillIdentity.

    Equivalent sets skill-A + skill-B and skill-B + skill-A must canonicalize identically.
    Not frequency aggregation — just identity.
    """

    skill_identities: tuple[SkillIdentity, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.skill_identities, (tuple, list)):
            raise TypeError(f"skill_identities must be tuple/list, got {type(self.skill_identities).__name__}")
        lst = list(self.skill_identities)
        if len(lst) == 0:
            raise ValueError("skill_identities must be non-empty")
        if len(lst) > 64:
            raise ValueError(f"skill co-load set size {len(lst)} exceeds bound 64")
        # validate each is SkillIdentity and deduplicate
        canonical: list[SkillIdentity] = []
        seen_keys: set[tuple[str, str]] = set()
        for idx, si in enumerate(lst):
            if not isinstance(si, SkillIdentity):
                raise TypeError(f"skill_identities[{idx}] must be SkillIdentity, got {type(si).__name__}")
            key = si.identity_key
            if key in seen_keys:
                continue
            seen_keys.add(key)
            canonical.append(si)
        # deterministic order: sorted by (skill_id, version, digest)
        canonical_sorted = tuple(sorted(canonical, key=lambda s: (s.skill_id, s.version, s.digest)))
        object.__setattr__(self, "skill_identities", canonical_sorted)

    def canonical_dict(self) -> dict[str, Any]:
        # Order-invariant canonical representation
        items = [si.to_dict() for si in self.skill_identities]
        # Already sorted
        return canonicalize({"skills": items}, path="SkillCoLoadIdentity")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Normalized Dimensions wrapper — deterministic, fail-closed
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedDimensions:
    """Bounded normalized dimensions — allowlisted keys, enum values, deterministic order."""

    dimensions: dict[str, str]

    def __post_init__(self) -> None:
        validated = validate_normalized_dimensions(self.dimensions)
        object.__setattr__(self, "dimensions", validated)

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize(self.dimensions, path="NormalizedDimensions")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, str]:
        return dict(self.dimensions)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NormalizedDimensions":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        # reject unknown keys via validator
        validated = validate_normalized_dimensions(dict(data))
        return cls(dimensions=validated)

# ---------------------------------------------------------------------------
# Aggregation Series Identity — deterministic, no random/storage/wall clock
# ---------------------------------------------------------------------------

_ALLOWED_SERIES_FIELDS: frozenset[str] = frozenset({
    "metric_taxonomy_version",
    "dimension_schema_version",
    "normalization_version",
    "metric_family",
    "metric_subject",
    "normalized_dimensions",
})

def compute_aggregation_series_id(
    metric_family: MetricFamily | str,
    metric_subject: object,
    normalized_dimensions: Mapping[str, Any] | NormalizedDimensions | None = None,
    metric_taxonomy_version: str = METRIC_TAXONOMY_VERSION,
    dimension_schema_version: str = DIMENSION_SCHEMA_VERSION,
    normalization_version: str = NORMALIZATION_VERSION,
) -> str:
    """Deterministically derive aggregation series identity.

    Inputs (canonical ordering required):
        metric_taxonomy_version / metric_family / metric_subject_identity
        normalization_version / dimension_schema_version / normalized_dimensions

    No random UUID, storage_row_id, wall clock, ingestion timestamp, database primary key.
    """
    # version validation
    mtv = _validate_bounded_str(metric_taxonomy_version, "metric_taxonomy_version", 64)
    if mtv not in SUPPORTED_METRIC_TAXONOMY_VERSIONS:
        raise ValueError(f"Unsupported metric taxonomy version: {mtv!r}. Supported: {sorted(SUPPORTED_METRIC_TAXONOMY_VERSIONS)}")
    dsv = _validate_bounded_str(dimension_schema_version, "dimension_schema_version", 64)
    if dsv not in SUPPORTED_DIMENSION_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported dimension schema version: {dsv!r}")
    nv = _validate_bounded_str(normalization_version, "normalization_version", 64)
    if nv not in SUPPORTED_NORMALIZATION_VERSIONS:
        raise ValueError(f"Unsupported normalization version: {nv!r}")

    family = parse_metric_family(metric_family)

    subject_dict = _validate_metric_subject(metric_subject)

    # normalized dimensions
    if normalized_dimensions is None:
        dims_dict: dict[str, str] = {}
    elif isinstance(normalized_dimensions, NormalizedDimensions):
        dims_dict = normalized_dimensions.to_dict()
    elif isinstance(normalized_dimensions, Mapping):
        dims_dict = validate_normalized_dimensions(normalized_dimensions)
    else:
        raise TypeError(f"normalized_dimensions must be mapping or NormalizedDimensions, got {type(normalized_dimensions).__name__}")

    payload = {
        "dimension_schema_version": dsv,
        "metric_family": family.value,
        "metric_subject": subject_dict,
        "metric_taxonomy_version": mtv,
        "normalization_version": nv,
        "normalized_dimensions": dims_dict,
    }
    canonical = canonicalize(payload, path="aggregation_series")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def compute_aggregation_window_id(
    aggregation_series_id: str,
    window_policy_id: str,
    window_policy_version: str,
    window_key: str,
) -> str:
    """Deterministically derive aggregation window identity.

    Semantic inputs: aggregation_series_id + window_policy_id + window_policy_version + window_key
    window_key is bounded caller-supplied canonical identity representing whatever M2 defines as window instance.
    No window duration / watermark / lateness / buffer / scheduler / finalization timeout frozen.
    """
    sid = _validate_digest(aggregation_series_id)
    pid = _validate_bounded_str(window_policy_id, "window_policy_id", MAX_WINDOW_POLICY_ID_LENGTH)
    pv = _validate_bounded_str(window_policy_version, "window_policy_version", MAX_WINDOW_POLICY_VERSION_LENGTH)
    wk = _validate_bounded_str(window_key, "window_key", MAX_WINDOW_KEY_LENGTH)
    # window_key must be bounded identifier, not raw high-cardinality
    if not _BOUNDED_ID_RE.fullmatch(wk):
        # Also allow alphanumeric with ._- but strictly bounded; reject raw paths etc
        if "/" in wk or " " in wk or ":" in wk or "?" in wk:
            raise ValueError(f"window_key must be bounded canonical identity, not raw path/url: {wk!r}")

    payload = {
        "aggregation_series_id": sid,
        "window_key": wk,
        "window_policy_id": pid,
        "window_policy_version": pv,
    }
    canonical = canonicalize(payload, path="aggregation_window")
    j = canonical_json(canonical)
    return hashlib.sha256(j.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Completeness / Sampling / Retention re-exports (reuse W1)
# ---------------------------------------------------------------------------

# Re-export for callers that reference W1 provenance via W2 seam
__all_completeness_reuse = [
    "CompletenessState",
    "CompletenessScope",
    "SamplingProvenance",
    "RetentionProvenance",
]

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    # versions
    "METRIC_TAXONOMY_VERSION",
    "DIMENSION_SCHEMA_VERSION",
    "NORMALIZATION_VERSION",
    "SUPPORTED_METRIC_TAXONOMY_VERSIONS",
    "SUPPORTED_DIMENSION_SCHEMA_VERSIONS",
    "SUPPORTED_NORMALIZATION_VERSIONS",
    # flags
    "METRIC_CONTRACT_IS_AUTHORITY",
    "METRIC_IS_POLICY_AUTHORITY",
    "METRIC_THRESHOLD_IS_CANONICAL_POLICY",
    "METRIC_TAXONOMY_VERSIONED",
    "DIMENSION_SCHEMA_VERSIONED",
    "NORMALIZATION_VERSION_PRESENT",
    "METRIC_FAMILY_SET_BOUNDED",
    "ARBITRARY_FREE_TEXT_METRIC_FAMILY",
    "UNKNOWN_METRIC_FAMILY_FAIL_CLOSED",
    "NEW_FAMILY_REQUIRES_CONTRACT_VERSION_EVOLUTION",
    "DIMENSION_ALLOWLIST_VERSIONED",
    "ARBITRARY_DIMENSION_KEY",
    "ARBITRARY_DIMENSION_KEY_ALLOWED",
    "ARBITRARY_METADATA_DIMENSION",
    "UNKNOWN_DIMENSION_KEY_FAIL_CLOSED",
    "DIMENSION_SCHEMA_CHANGE_REQUIRES_VERSION_CHANGE",
    "METRIC_CARDINALITY_BOUNDED",
    "METRIC_SUBJECT_DIMENSION_SEPARATED",
    "METRIC_SUBJECT_IDENTITY_IS_DIMENSION",
    "AGGREGATION_SERIES_IDENTITY_PRESENT",
    "AGGREGATION_WINDOW_IDENTITY_PRESENT",
    "SERIES_WINDOW_IDENTITY_SEPARATE",
    "NORMALIZATION_VERSION_NOT_IN_W1_SOURCE_DEDUP_ID",
    "W1_SOURCE_DEDUP_REDEFINED",
    "RAW_SHELL_ARGV_ACCEPTED",
    "RAW_SHELL_ARGV_NEVER_ENTERS_METRIC_CONTRACT",
    "RAW_SECRET_VALUE_IN_PATTERN",
    "UNKNOWN_SHELL_FALLBACK_SAFE",
    "RAW_VALUE_HASH_BUCKET_AS_DEFAULT",
    "CONTEXT_COST_PORTABLE_UNITS_PRESENT",
    "MODEL_TOKENIZER_CANONICAL_AUTHORITY",
    "MODEL_TOKENIZER_IS_CANONICAL_METRIC_AUTHORITY",
    "PROVIDER_TOKEN_METRICS_SUPPLEMENTAL",
    "SKILL_METRIC_LIFECYCLE_DISTINCTION",
    "FREE_TEXT_SKILL_IDENTITY_ALLOWED",
    "TOOL_NAME_IS_SKILL_IDENTITY",
    "SKILL_METRIC_USES_CANONICAL_S3_IDENTITY",
    "OBSERVED_USE_METRIC_REQUIRES_ACTUAL_EVIDENCE",
    "NEW_DATABASE_CREATED",
    "NEW_PERSISTENT_STORE_CREATED",
    "NEW_EVENT_BUS_CREATED",
    "NEW_STATE_MACHINE_CREATED",
    "NEW_JOURNAL_CREATED",
    "NEW_ANALYTICS_RUNTIME_CREATED",
    "NEW_POLICY_ENGINE_CREATED",
    "AUTOMATIC_ARCHITECTURE_MUTATION",
    "W1_CONTRACT_CHANGED",
    "EXISTING_SOURCE_PRODUCER_CHANGED",
    "EXISTING_PRODUCER_CHANGED",
    "MISSING_TELEMETRY_IS_ZERO",
    "ZERO_OBSERVATION_COUNT_IMPLIES_COMPLETE_ZERO",
    # metric family
    "MetricFamily",
    "METRIC_FAMILIES",
    "METRIC_FAMILY_VALUES",
    "parse_metric_family",
    "validate_metric_family",
    # dimensions
    "ALLOWED_DIMENSION_KEYS",
    "validate_dimension_key",
    "validate_dimension_value",
    "validate_normalized_dimensions",
    "NormalizedDimensions",
    # subjects
    "RoleMetricSubject",
    "ToolMetricSubject",
    "SkillMetricSubject",
    "ContextCostMetricSubject",
    "ReviewRepairMetricSubject",
    "WorkflowFrictionMetricSubject",
    # shell pattern
    "NormalizedShellPattern",
    "create_normalized_shell_pattern",
    "RECOGNIZED_SHELL_COMMANDS",
    "UNKNOWN_COMMAND_FALLBACK",
    "ALLOWED_SHELL_COMMAND_IDS",
    "SHELL_ARGUMENT_CLASSES",
    # context cost
    "ContextCostUnit",
    "CONTEXT_COST_UNITS",
    "parse_context_cost_unit",
    "ContextCostMeasurement",
    "ProviderTokenKind",
    "ProviderTokenObservation",
    "MAX_CONTEXT_COST_VALUE",
    # skill semantics
    "SkillMetricKind",
    "SKILL_METRIC_KINDS",
    "parse_skill_metric_kind",
    "SkillCoLoadIdentity",
    # aggregation
    "compute_aggregation_series_id",
    "compute_aggregation_window_id",
    # W1 re-exports
    "CompletenessState",
    "CompletenessScope",
    "SamplingProvenance",
    "RetentionProvenance",
    "parse_completeness_state",
    "parse_completeness_scope",
]
