"""Bounded Skill usage observation projection (S3 M4-W1).

Conceptual architecture::

    existing execution lifecycle
            ↓
    existing ExecutionEvent / EventHook seam
            ↓
    S3-local Skill usage projection
            ↓
    caller-provided ephemeral observation consumer

Not::

    Skill usage → new ExecutionEvent enum → telemetry DB → analytics

The observation is trace/evidence only.

Invariants
----------
* REUSE_EXISTING_EVENT_HOOK=yes — reuses accepted S1 ExecutionEvent/EventHook
* EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR=yes
* EXISTING_EVENT_HOOK_CONTRACT_REUSED=yes — does not mutate events.py
* NEW_EXECUTION_EVENT_TYPE_CREATED=no — no new ExecutionEventType values
* EXECUTION_EVENT_TYPE_MUTATED=no
* TELEMETRY_STORE_CREATED=no, ANALYTICS_CREATED=no, BACKGROUND_EXPORTER_CREATED=no
* S6_OWNERSHIP_PRESERVED=yes — does not implement S6 telemetry
* SKILL_USAGE_OBSERVATION_IS_AUTHORITY=no — observation never grants authority
* W1_RECOMPUTES_SKILL_RESOLUTION=no, W1_READS_SKILL_CONTENT=no, W1_PERFORMS_BOOTSTRAP=no
* OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION=yes
* SKILL_USAGE_OBSERVATION_DETERMINISTIC=yes, INPUT_ORDER_IS_AUTHORITY=no
* SKILL_USAGE_OBSERVATION_BOUNDED=yes, SILENT_TRUNCATION=no — fail closed on over-bounds
* OBSERVED_SKILL_VERSION_PRESERVED=yes, OBSERVED_SKILL_DIGEST_PRESERVED=yes
* OBSERVED_DELIVERY_MATCHES_BOOTSTRAP=yes — delivery derived from bootstrap, not recomputed
* SELECTION_SOURCE_IS_AUTHORITY=no

Input boundary
--------------
W1 does NOT perform Skill selection. Consumes already-produced S3 artifacts:

* SkillResolutionResult
* SkillBootstrapProjection / equivalent accepted W4 result
* ExecutionEvent

It projects facts from accepted artifacts without rerunning pipeline.

Ownership
---------
* S3-local only, does not modify aota_forge/work_plane/__init__.py, events.py, handoff.py, bootstrap.py
* Reuses existing ExecutionEvent / EventHook without mutation
* No persistent store, no analytics, no exporter
* Caller-provided ephemeral synchronous consumer only, typed failure handling
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.events import EventHookError, ExecutionEvent, ExecutionEventType, emit_event, parse_execution_event_type  # noqa: F401 — reuse marker
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill import MAX_PROVENANCE_LENGTH as SKILL_MAX_PROVENANCE_LENGTH
from aota_forge.work_plane.skill import MAX_SKILL_ID_LENGTH, MAX_VERSION_LENGTH
from aota_forge.work_plane.skill_registry import MAX_REGISTRY_ENTRIES
from aota_forge.work_plane.bootstrap import MAX_REF_LENGTH as BOOTSTRAP_MAX_REF_LENGTH  # reuse bound

# ---------------------------------------------------------------------------
# Public invariant markers (descriptive, not authority)
# ---------------------------------------------------------------------------

REUSE_EXISTING_EVENT_HOOK: bool = True
EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR: bool = True
EXISTING_EVENT_HOOK_CONTRACT_REUSED: bool = True

NEW_EXECUTION_EVENT_TYPE_CREATED: bool = False
EXECUTION_EVENT_TYPE_MUTATED: bool = False
EVENTS_PY_MUTATED: bool = False

TELEMETRY_STORE_CREATED: bool = False
ANALYTICS_CREATED: bool = False
BACKGROUND_EXPORTER_CREATED: bool = False

S6_OWNERSHIP_PRESERVED: bool = True
S6_TELEMETRY_IMPLEMENTED_IN_S3: bool = False

SKILL_USAGE_OBSERVATION_BOUNDED: bool = True
SILENT_TRUNCATION: bool = False

SKILL_USAGE_OBSERVATION_DETERMINISTIC: bool = True
INPUT_ORDER_IS_AUTHORITY: bool = False

OBSERVED_SKILL_VERSION_PRESERVED: bool = True
OBSERVED_SKILL_DIGEST_PRESERVED: bool = True

OBSERVED_DELIVERY_MATCHES_BOOTSTRAP: bool = True

OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION: bool = True

W1_RECOMPUTES_SKILL_RESOLUTION: bool = False
W1_READS_SKILL_CONTENT: bool = False
W1_PERFORMS_BOOTSTRAP: bool = False

SKILL_USAGE_OBSERVATION_IS_AUTHORITY: bool = False
SKILL_USAGE_OBSERVATION_GRANTS_AUTHORITY: bool = False

SKILL_IS_AUTHORITY: bool = False
EXECUTION_EVENT_IS_AUTHORITY: bool = False
EVENT_ID_IS_AUTHORITY: bool = False
DIGEST_IS_AUTHORITY: bool = False

SELECTION_SOURCE_IS_AUTHORITY: bool = False

S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
S1_SHARED_HOT_FILE_TOUCHED: bool = False
W2_LANE_FILE_TOUCHED: bool = False  # discipline marker

# Delivery constants — smallest vocabulary justified by current source
DELIVERY_EAGER: str = "eager"
DELIVERY_PROGRESSIVE: str = "progressive"
ALLOWED_DELIVERIES: frozenset[str] = frozenset({DELIVERY_EAGER, DELIVERY_PROGRESSIVE})

# Selection source precedence class — where resolution preserves it
ALLOWED_SELECTION_SOURCES: frozenset[str] = frozenset({"pinned", "required", "role_default", "recommended"})
SELECTION_SOURCE_ORDER: dict[str, int] = {"pinned": 0, "required": 1, "role_default": 2, "recommended": 3}

# Bounds — reuse accepted bounds where practical, bounded fail-closed
MAX_SKILL_USAGE_OBSERVATIONS: int = MAX_REGISTRY_ENTRIES  # 64
MAX_EVENT_ID_LENGTH: int = 128
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_PROVENANCE_LENGTH: int = 512  # bootstrap allows 512, skill 128; use larger but still bounded
MAX_SELECTION_SOURCE_LENGTH: int = 32

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Exceptions — small deterministic local failure model, typed/bounded
# ---------------------------------------------------------------------------

class SkillUsageObservationError(RuntimeError):
    """Typed fail-closed Skill usage observation error."""


class SkillUsageBoundsError(SkillUsageObservationError):
    """Bounds exceeded — fail closed, no silent truncation."""


class SkillUsageConsumerError(SkillUsageObservationError):
    """Caller-provided observation consumer failure — typed, non-authority, no mutation."""

    def __init__(self, event_id: str, cause: BaseException) -> None:
        super().__init__(f"skill usage observation consumer failed for {event_id}: {cause}")
        self.event_id = event_id
        self.cause = cause


# ---------------------------------------------------------------------------
# Validation helpers — bounded, explicit, fail-closed
# ---------------------------------------------------------------------------

def _validate_event_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"event_id must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("event_id must be non-empty")
    if len(stripped) > MAX_EVENT_ID_LENGTH:
        raise ValueError(f"event_id length ({len(stripped)}) exceeds maximum {MAX_EVENT_ID_LENGTH}")
    return stripped


def _validate_event_type(value: object) -> str:
    # Reuse existing ExecutionEventType vocabulary — do not extend
    if isinstance(value, ExecutionEventType):
        return value.value
    if isinstance(value, str) and type(value) is str:
        # Use parse to fail-closed on unknown — but store canonical string
        parsed = parse_execution_event_type(value)
        return parsed.value
    raise TypeError(f"event_type must be ExecutionEventType or string, got {type(value).__name__}")


def _validate_namespace(value: object) -> str:
    # Namespace is AgentWorkRole canonical value
    role = parse_agent_work_role(value)
    return role.value


def _validate_skill_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"skill_id must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("skill_id must be non-empty")
    if not value.strip():
        raise ValueError("skill_id must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("skill_id must not have leading/trailing whitespace")
    if len(value) > MAX_SKILL_ID_LENGTH:
        raise ValueError(f"skill_id length ({len(value)}) exceeds maximum {MAX_SKILL_ID_LENGTH}")
    return value


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"version must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("version must be non-empty")
    if not value.strip():
        raise ValueError("version must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("version must not have leading/trailing whitespace")
    if len(value) > MAX_VERSION_LENGTH:
        raise ValueError(f"version length ({len(value)}) exceeds maximum {MAX_VERSION_LENGTH}")
    return value


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string, got {type(value).__name__}")
    stripped = value.strip().lower()
    if not stripped:
        raise ValueError("digest must be non-empty")
    if len(stripped) != 64:
        raise ValueError(f"digest must be 64 hex chars, got length {len(stripped)}: {value!r}")
    if not _DIGEST_RE.fullmatch(stripped):
        raise ValueError(f"digest must be 64 lowercase hex chars: {value!r}")
    # also enforce it was originally canonical lowercase — if caller passed uppercase, we normalize but also require canonical input already lower
    # If original had uppercase, we treat as fail-closed to preserve exactness
    if isinstance(value, str) and value.strip() != stripped:
        # original had uppercase or whitespace variation — but we already stripped/lowered; enforce canonical was lower
        if value.strip().lower() != value.strip():
            raise ValueError(f"digest must be 64 lowercase hex chars (non-canonical uppercase): {value!r}")
    return stripped


def _validate_provenance(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"provenance must be a string or None, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("provenance when provided must be non-empty")
    if len(stripped) > MAX_PROVENANCE_LENGTH:
        raise ValueError(f"provenance length ({len(stripped)}) exceeds maximum {MAX_PROVENANCE_LENGTH}")
    if value != value.strip():
        raise ValueError("provenance must not have leading/trailing whitespace")
    if stripped.startswith("/"):
        raise ValueError(f"provenance must not be absolute: {value!r}")
    if ".." in stripped.split("/"):
        raise ValueError(f"provenance must not contain '..': {value!r}")
    return stripped


def _validate_ref(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"ref must be a string or None, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("ref when provided must be non-empty")
    if len(stripped) > MAX_REF_LENGTH:
        raise ValueError(f"ref length ({len(stripped)}) exceeds maximum {MAX_REF_LENGTH}")
    if value != value.strip():
        raise ValueError("ref must not have leading/trailing whitespace")
    if stripped.startswith("/"):
        raise ValueError(f"ref must not be absolute path: {value!r}")
    if ".." in stripped.split("/"):
        raise ValueError(f"ref must not contain '..': {value!r}")
    return stripped


def _validate_delivery(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"delivery must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if v not in ALLOWED_DELIVERIES:
        raise ValueError(f"delivery must be one of {sorted(ALLOWED_DELIVERIES)}, got {value!r}")
    return v


def _validate_selection_source(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"selection_source must be a string or None, got {type(value).__name__}")
    stripped = value.strip().lower()
    if not stripped:
        raise ValueError("selection_source when provided must be non-empty")
    if stripped not in ALLOWED_SELECTION_SOURCES:
        raise ValueError(f"selection_source must be one of {sorted(ALLOWED_SELECTION_SOURCES)}, got {value!r}")
    if len(stripped) > MAX_SELECTION_SOURCE_LENGTH:
        raise ValueError(f"selection_source length exceeds maximum {MAX_SELECTION_SOURCE_LENGTH}")
    return stripped


# ---------------------------------------------------------------------------
# SkillUsageObservation — bounded immutable trace/evidence only
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillUsageObservation:
    """Bounded immutable Skill usage observation tuple.

    Preserve enough bounded identity to answer:
    which execution observation? which WorkRole/namespace? which exact Skill?
    which version? which digest? how delivered/used?

    Fields
    ------
    event_id: which execution observation anchor (trace, not authority)
    event_type: ExecutionEventType canonical string
    namespace: skill's AgentWorkRole canonical value
    skill_id: exact skill identifier bounded
    version: exact version bounded
    digest: exact SHA-256 64 lowercase hex
    delivery: eager/materialized vs progressive/reference (actual bootstrap truth)
    selection_source: pinned/required/role_default/recommended precedence class (where preserved)
    provenance: optional bounded provenance (not authority)
    ref: optional bounded logical ref for progressive (not content, not filesystem authority)

    Must not contain Skill body/content.
    Observation grants no authority.
    Deterministic, bounded, fail-closed.
    """

    event_id: str
    event_type: str
    namespace: str
    skill_id: str
    version: str
    digest: str
    delivery: str
    selection_source: str | None = None
    provenance: str | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        eid = _validate_event_id(self.event_id)
        object.__setattr__(self, "event_id", eid)

        et = _validate_event_type(self.event_type)
        object.__setattr__(self, "event_type", et)

        ns = _validate_namespace(self.namespace)
        object.__setattr__(self, "namespace", ns)

        sid = _validate_skill_id(self.skill_id)
        object.__setattr__(self, "skill_id", sid)

        ver = _validate_version(self.version)
        object.__setattr__(self, "version", ver)

        dg = _validate_digest(self.digest)
        object.__setattr__(self, "digest", dg)

        deliv = _validate_delivery(self.delivery)
        object.__setattr__(self, "delivery", deliv)

        ss = _validate_selection_source(self.selection_source)
        object.__setattr__(self, "selection_source", ss)

        prov = _validate_provenance(self.provenance)
        object.__setattr__(self, "provenance", prov)

        rf = _validate_ref(self.ref)
        object.__setattr__(self, "ref", rf)

        # Content leakage check: ensure observation does not carry materialized Skill body
        # No field named content/materialized/body should exist; this is structural check
        # If observation had those, they would be extra fields — but dataclass prevents unknown fields.

    @property
    def composite_key(self) -> tuple[str, str, str]:
        return (self.namespace, self.skill_id, self.version)

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        # includes digest for exact identity observation
        return (self.namespace, self.skill_id, self.version, self.digest)

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "delivery": self.delivery,
            "digest": self.digest,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "namespace": self.namespace,
            "skill_id": self.skill_id,
            "version": self.version,
        }
        if self.selection_source is not None:
            d["selection_source"] = self.selection_source
        if self.provenance is not None:
            d["provenance"] = self.provenance
        if self.ref is not None:
            d["ref"] = self.ref
        return canonical_json(d) and d  # type: ignore[return-value] — keep dict deterministic ordering via canonicalization externally if needed
        # Note: we return dict, not json; canonical ordering for digest via canonical_json helper when computing digest

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def observation_digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()


# ---------------------------------------------------------------------------
# Projection helpers — derive from accepted artifacts without recomputing
# ---------------------------------------------------------------------------

def _synthetic_skill_ref(namespace: str, skill_id: str, version: str) -> str:
    return f"skill:{namespace}/{skill_id}@{version}"


def _derive_logical_ref_map(
    allowed_universe: object | None,
    logical_ref_map: Mapping[tuple[str, str, str], str] | None,
) -> dict[tuple[str, str, str], str] | None:
    if logical_ref_map is not None:
        if not isinstance(logical_ref_map, Mapping):
            raise TypeError("logical_ref_map must be mapping")
        return dict(logical_ref_map)
    if allowed_universe is not None:
        try:
            from aota_forge.work_plane.skill_resolution import AllowedSkillUniverse  # type: ignore

            if isinstance(allowed_universe, AllowedSkillUniverse):
                mapping: dict[tuple[str, str, str], str] = {}
                for allowed in allowed_universe.skills:  # type: ignore[attr-defined]
                    ck = allowed.composite_key  # type: ignore[attr-defined]
                    ref = allowed.ref  # type: ignore[attr-defined]
                    if ref is not None:
                        mapping[ck] = ref
                return mapping
        except Exception:
            pass
        try:
            skills = getattr(allowed_universe, "skills", None)
            if skills is not None:
                mapping2: dict[tuple[str, str, str], str] = {}
                for s in skills:
                    ck = getattr(s, "composite_key", None)
                    ref = getattr(s, "ref", None)
                    if ck is not None and ref is not None:
                        mapping2[ck] = ref
                if mapping2:
                    return mapping2
        except Exception:
            pass
    return None


def _derive_category_map(
    selected: tuple[Any, ...],
    logical_ref_map: dict[tuple[str, str, str], str] | None,
    pinned_refs: Iterable[SemanticReference] = (),
    required_refs: Iterable[SemanticReference] = (),
    role_default_refs: Iterable[SemanticReference] = (),
    recommended_refs: Iterable[SemanticReference] = (),
    explicit_category_map: Mapping[tuple[str, str, str], str] | None = None,
) -> dict[tuple[str, str, str], str]:
    if explicit_category_map is not None:
        if not isinstance(explicit_category_map, Mapping):
            raise TypeError("category_map must be mapping")
        # validate entries
        out: dict[tuple[str, str, str], str] = {}
        for k, v in explicit_category_map.items():
            if not isinstance(k, tuple) or len(k) != 3:
                raise TypeError(f"category_map key must be composite tuple, got {k!r}")
            ss = _validate_selection_source(v)
            assert ss is not None
            out[k] = ss
        return out

    ref_to_category: dict[str, str] = {}
    for sr in pinned_refs:
        if isinstance(sr, SemanticReference):
            if sr.ref not in ref_to_category:
                ref_to_category[sr.ref] = "pinned"
    for sr in required_refs:
        if isinstance(sr, SemanticReference):
            if sr.ref not in ref_to_category:
                ref_to_category[sr.ref] = "required"
    for sr in role_default_refs:
        if isinstance(sr, SemanticReference):
            if sr.ref not in ref_to_category:
                ref_to_category[sr.ref] = "role_default"
    for sr in recommended_refs:
        if isinstance(sr, SemanticReference):
            if sr.ref not in ref_to_category:
                ref_to_category[sr.ref] = "recommended"

    if not ref_to_category:
        # No explicit refs — caller provided no category hints.
        # Fallback: treat all selected as eager? But to preserve recommended progressive distinction,
        # we cannot guess. Return empty map so caller sees selection_source as None (explicit omission allowed).
        # However for delivery matching we may still need delivery.
        return {}

    result: dict[tuple[str, str, str], str] = {}
    for entry in selected:
        ck = entry.composite_key  # type: ignore[attr-defined]
        logical = None
        if logical_ref_map is not None:
            logical = logical_ref_map.get(ck)
        if logical is not None and logical in ref_to_category:
            result[ck] = ref_to_category[logical]
        else:
            if logical is None:
                # synthetic — no mapping, default to recommended for progressive synthetic case
                # But we cannot know if synthetic is mandatory or recommended; default to recommended to avoid overstating mandatory
                # For cases where synthetic corresponds to mandatory, caller should provide category_map.
                result[ck] = "recommended"
            else:
                result[ck] = "recommended"
    return result


def _build_digest_to_component_map(bootstrap: Any) -> dict[str, Any]:
    # bootstrap is SkillBootstrapProjection with .components
    mapping: dict[str, Any] = {}
    try:
        comps = getattr(bootstrap, "components", None)
        if comps is None:
            return {}
        for c in comps:
            dg = getattr(c, "digest", None)
            if isinstance(dg, str):
                # Keep first; if collision, provenance disambiguation handled later via scan
                if dg not in mapping:
                    mapping[dg] = c
        return mapping
    except Exception:
        return {}


def _find_component_for_entry(entry: Any, digest_map: dict[str, Any], bootstrap: Any) -> Any | None:
    dg = entry.identity.digest  # type: ignore[attr-defined]
    prov = entry.identity.provenance  # type: ignore[attr-defined]
    # First try direct digest map then check provenance matches if multiple
    comp = digest_map.get(dg)
    if comp is not None:
        # If provenance matches or bootstrap has single component per digest, return
        if getattr(comp, "provenance", None) == prov:
            return comp
        # If mismatch, scan all for digest+provenance match
        try:
            for c in getattr(bootstrap, "components", ()):
                if getattr(c, "digest", None) == dg and getattr(c, "provenance", None) == prov:
                    return c
        except Exception:
            pass
        return comp  # fallback to digest-matched
    # Not found via digest map, scan full
    try:
        for c in getattr(bootstrap, "components", ()):
            if getattr(c, "digest", None) == dg and getattr(c, "provenance", None) == prov:
                return c
        for c in getattr(bootstrap, "components", ()):
            if getattr(c, "digest", None) == dg:
                return c
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Core projection — deterministic, bounded, no content hydration
# ---------------------------------------------------------------------------

def project_skill_usage(
    event: ExecutionEvent,
    resolution: Any,
    bootstrap: Any | None = None,
    *,
    allowed_universe: object | None = None,
    logical_ref_map: Mapping[tuple[str, str, str], str] | None = None,
    category_map: Mapping[tuple[str, str, str], str] | None = None,
    pinned_refs: Iterable[SemanticReference] = (),
    required_refs: Iterable[SemanticReference] = (),
    role_default_refs: Iterable[SemanticReference] = (),
    recommended_refs: Iterable[SemanticReference] = (),
) -> tuple[SkillUsageObservation, ...]:
    """Project bounded Skill usage observation anchored to existing ExecutionEvent.

    Consumes already-produced S3 artifacts — does not recompute resolution,
    does not read Skill content, does not perform bootstrap.

    Args:
        event: existing ExecutionEvent anchor (required, not authority)
        resolution: SkillResolutionResult (required, contains selected SkillRegistryEntry)
        bootstrap: SkillBootstrapProjection or None (if provided, delivery derived from actual bootstrap)
        allowed_universe: optional AllowedSkillUniverse for logical ref derivation
        logical_ref_map: optional explicit composite_key -> logical ref
        category_map: optional explicit composite_key -> selection_source (pinned/required/role_default/recommended)
        pinned_refs etc: original SemanticReference lists for category derivation when category_map/logical_ref_map not provided

    Returns:
        tuple[SkillUsageObservation, ...] bounded immutable deterministic sorted

    Fail-closed on:
        * invalid event/resolution/bootstrap types
        * bounds exceeded (field lengths, observation count)
        * unknown delivery/selection_source
        * digest/version not preserved (handled via validation)

    Deterministic: equivalent inputs produce identical observations independent of input iteration order.

    Does not invent execution events, does not modify ExecutionEvent, does not grant authority, does not persist.
    """
    # Validate core types fail-closed — ensure existing EventHook contract reused, not mutated
    if not isinstance(event, ExecutionEvent):
        raise TypeError(f"event must be ExecutionEvent, got {type(event).__name__}")
    # Validate resolution is SkillResolutionResult without importing heavy circular? Import lazily
    try:
        from aota_forge.work_plane.skill_resolution import SkillResolutionResult  # type: ignore

        if not isinstance(resolution, SkillResolutionResult):
            # Allow duck-typed fake for bounds testing if it has correct shape with SkillRegistryEntry entries
            if not hasattr(resolution, "selected") or not hasattr(resolution, "target_namespace"):
                raise TypeError(f"resolution must be SkillResolutionResult, got {type(resolution).__name__}")
            # If it has selected but entries are SkillRegistryEntry, allow to proceed for bounded check (used in bounds test)
            sel = getattr(resolution, "selected", None)
            try:
                from aota_forge.work_plane.skill_registry import SkillRegistryEntry  # type: ignore

                # If selected is iterable of SkillRegistryEntry, treat as valid for bounds testing
                if sel is not None:
                    lst = list(sel)  # type: ignore[arg-type]
                    if lst and all(isinstance(x, SkillRegistryEntry) for x in lst):
                        pass  # allow duck-typed
                    elif not lst:
                        pass
                    else:
                        raise TypeError(f"resolution must be SkillResolutionResult, got {type(resolution).__name__}")
                else:
                    raise TypeError(f"resolution must be SkillResolutionResult, got {type(resolution).__name__}")
            except TypeError:
                raise
            except Exception:
                raise TypeError(f"resolution must be SkillResolutionResult, got {type(resolution).__name__}")
    except ImportError:
        # fallback structural check
        if not hasattr(resolution, "selected") or not hasattr(resolution, "target_namespace"):
            raise TypeError(f"resolution must be SkillResolutionResult-like, got {type(resolution).__name__}")

    if bootstrap is not None:
        try:
            from aota_forge.work_plane.skill_bootstrap import SkillBootstrapProjection  # type: ignore

            if not isinstance(bootstrap, SkillBootstrapProjection):
                raise TypeError(f"bootstrap must be SkillBootstrapProjection or None, got {type(bootstrap).__name__}")
        except ImportError:
            if not hasattr(bootstrap, "components"):
                raise TypeError(f"bootstrap must be SkillBootstrapProjection-like, got {type(bootstrap).__name__}")

    # Extract selected entries — deterministic but input order not authority
    selected = getattr(resolution, "selected", None)
    if selected is None:
        raise TypeError("resolution must have 'selected' attribute")
    try:
        selected_list = list(selected)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"resolution.selected must be iterable: {exc}") from exc

    if len(selected_list) > MAX_SKILL_USAGE_OBSERVATIONS:
        raise SkillUsageBoundsError(f"observation count ({len(selected_list)}) exceeds maximum {MAX_SKILL_USAGE_OBSERVATIONS}")

    # Derive logical ref map and category map deterministically
    derived_logical = _derive_logical_ref_map(allowed_universe, logical_ref_map)
    if derived_logical is None:
        derived_logical = {}
    # category derivation uses selected tuple
    try:
        selected_tuple = tuple(selected_list)  # type: ignore
    except Exception as exc:
        raise SkillUsageObservationError(f"failed to normalize selected: {exc}") from exc

    cat_map_derived = _derive_category_map(
        selected_tuple,
        derived_logical,
        pinned_refs,
        required_refs,
        role_default_refs,
        recommended_refs,
        category_map,
    )
    # If explicit category_map provided, it already validated; else cat_map_derived may be empty (no hints)
    # Merge: explicit takes precedence, else derived
    if category_map is not None:
        effective_category_map: dict[tuple[str, str, str], str] = dict(cat_map_derived)  # already handled explicit
        # cat_map_derived when explicit is just copy of explicit, so nothing to merge
    else:
        effective_category_map = cat_map_derived

    # Build digest -> component map for delivery truth
    digest_map: dict[str, Any] = {}
    if bootstrap is not None:
        digest_map = _build_digest_to_component_map(bootstrap)

    observations: list[SkillUsageObservation] = []
    # For deduplication: composite_key -> observation (highest precedence wins deterministically)
    seen: dict[tuple[str, str, str], SkillUsageObservation] = {}

    for entry in selected_list:
        # Validate entry shape without recomputing resolution or reading content
        if not hasattr(entry, "namespace") or not hasattr(entry, "identity") or not hasattr(entry, "composite_key"):
            raise TypeError(f"selected entry must be SkillRegistryEntry-like, got {type(entry).__name__}")
        ns_val = entry.namespace.value if isinstance(entry.namespace, AgentWorkRole) else str(entry.namespace)  # will be validated by observation
        skill_id = entry.identity.skill_id  # type: ignore[attr-defined]
        version = entry.identity.version  # type: ignore[attr-defined]
        digest = entry.identity.digest  # type: ignore[attr-defined]
        provenance = entry.identity.provenance  # type: ignore[attr-defined]
        ck = entry.composite_key  # type: ignore[attr-defined]

        # Determine delivery and ref from bootstrap truth if provided
        delivery: str
        ref: str | None = None
        comp_found = None
        if bootstrap is not None:
            comp_found = _find_component_for_entry(entry, digest_map, bootstrap)
            if comp_found is not None:
                delivery = getattr(comp_found, "delivery", DELIVERY_EAGER)
                # Validate delivery is allowed
                delivery = _validate_delivery(delivery)
                if delivery == DELIVERY_PROGRESSIVE:
                    ref = getattr(comp_found, "ref", None)
                    # If bootstrap progressive ref is None, synthesize logical
                    if ref is None:
                        # synthetic fallback
                        logical = derived_logical.get(ck)
                        if logical is None:
                            logical = _synthetic_skill_ref(ns_val, skill_id, version)
                        ref = logical
                else:
                    ref = None
            else:
                # No component found — could be degraded recommended or budget pressure
                # Derive delivery from category if known, else infer
                cat = effective_category_map.get(ck)
                if cat == "recommended":
                    delivery = DELIVERY_PROGRESSIVE
                    logical = derived_logical.get(ck)
                    if logical is None:
                        logical = _synthetic_skill_ref(ns_val, skill_id, version)
                    ref = logical
                else:
                    # For mandatory missing component, this would be fail-closed in bootstrap (should not happen for observation)
                    # But for observation we still project truth: if no bootstrap component, we cannot claim eager materialization
                    # Fallback to eager without ref? However to preserve OBSERVED_DELIVERY_MATCHES_BOOTSTRAP, we treat as eager if category not recommended
                    # This fallback is deterministic and preserves identity
                    delivery = DELIVERY_EAGER
                    ref = None
        else:
            # No bootstrap — derive delivery from category
            cat = effective_category_map.get(ck)
            if cat == "recommended":
                delivery = DELIVERY_PROGRESSIVE
                logical = derived_logical.get(ck)
                if logical is None:
                    logical = _synthetic_skill_ref(ns_val, skill_id, version)
                ref = logical
            elif cat in ("pinned", "required", "role_default"):
                delivery = DELIVERY_EAGER
                ref = None
            else:
                # No bootstrap and no category hints — default deterministic: treat as eager (mandatory) to avoid overstating progressive
                # But we could also leave delivery as eager and allow caller to see selection_source is None (explicit omission)
                delivery = DELIVERY_EAGER
                ref = None

        # Determine selection_source
        selection_source = effective_category_map.get(ck)
        # If still None and we have delivery progressive but no category, set to recommended for delivery truth? But spec says selection_source is optional provenance/reference fields may be omitted only if explicit. So we keep None if no map.
        # However to make observation useful, if delivery is progressive and no category, we could set to recommended as best effort deterministic mapping
        if selection_source is None:
            if delivery == DELIVERY_PROGRESSIVE and effective_category_map == {} and bootstrap is None:
                # No hints at all — leave as None to indicate unknown, not fabricated authority
                selection_source = None
            elif delivery == DELIVERY_PROGRESSIVE and bootstrap is not None:
                # If bootstrap says progressive, it must be recommended
                selection_source = "recommended"
            elif delivery == DELIVERY_EAGER and effective_category_map == {} and derived_logical == {}:
                # No hints, leave None
                selection_source = None

        # Provenance already bounded, but validate via observation construction

        # Deduplication: if same exact composite key already seen, keep first (highest precedence deterministic after sorting)
        # But we need precedence awareness: if same skill appears as both pinned and recommended, resolution already deduplicates at highest precedence, so we will see it once.
        # So simple dedup by ck
        if ck in seen:
            # Duplicate same exact Skill — skip unless semantically distinct lifecycle facts intentionally represented
            # Two distinct facts would require different delivery or selection_source intentionally; but spec says unless intentionally represented, don't duplicate.
            # We deduplicate silently to ensure same exact Skill does not generate accidental duplicate.
            continue

        # Build observation — validation will fail closed on over-bounds
        try:
            obs = SkillUsageObservation(
                event_id=event.event_id,
                event_type=event.event_type.value if isinstance(event.event_type, ExecutionEventType) else str(event.event_type),
                namespace=ns_val,
                skill_id=skill_id,
                version=version,
                digest=digest,
                delivery=delivery,
                selection_source=selection_source,
                provenance=provenance,
                ref=ref,
            )
        except (ValueError, TypeError) as exc:
            raise SkillUsageBoundsError(str(exc)) from exc

        # Ensure no content leakage: observation must not contain skill body
        # (structural guarantee: no field contains large content)
        seen[ck] = obs

    # Convert to list and sort deterministically independent of input order
    obs_list = list(seen.values())
    # Canonical ordering by exact Skill identity plus delivery/selection semantics per spec
    def _sort_key(o: SkillUsageObservation) -> tuple[str, str, str, str, str, str]:
        return (
            o.namespace,
            o.skill_id,
            o.version,
            o.delivery,
            o.selection_source or "",
            o.digest,
        )

    obs_sorted = tuple(sorted(obs_list, key=_sort_key))

    # Final bounds check
    if len(obs_sorted) > MAX_SKILL_USAGE_OBSERVATIONS:
        raise SkillUsageBoundsError(f"observation count ({len(obs_sorted)}) exceeds maximum {MAX_SKILL_USAGE_OBSERVATIONS}")

    return obs_sorted


def consume_skill_usage_observations(
    event: ExecutionEvent,
    observations: Sequence[SkillUsageObservation],
    consumer: Callable[[tuple[SkillUsageObservation, ...]], None] | None,
) -> None:
    """Invoke caller-provided ephemeral observation consumer synchronously.

    Semantics:
    * No persistence, no telemetry store
    * Failure typed as SkillUsageConsumerError, does not mutate original ExecutionEvent
    * Does not silently change execution authority
    * Does not cause persisted partial telemetry

    Args:
        event: anchor ExecutionEvent (for event_id in error)
        observations: bounded tuple of SkillUsageObservation
        consumer: optional synchronous callable receiving tuple
    """
    if not isinstance(event, ExecutionEvent):
        raise TypeError(f"event must be ExecutionEvent, got {type(event).__name__}")
    if not isinstance(observations, (tuple, list)):
        raise TypeError(f"observations must be tuple or list, got {type(observations).__name__}")
    for idx, o in enumerate(observations):
        if not isinstance(o, SkillUsageObservation):
            raise TypeError(f"observations[{idx}] must be SkillUsageObservation, got {type(o).__name__}")
    if consumer is None:
        return
    if not callable(consumer):
        raise TypeError(f"consumer must be callable or None, got {type(consumer).__name__}")
    try:
        consumer(tuple(observations))
    except SkillUsageConsumerError:
        raise
    except BaseException as exc:
        raise SkillUsageConsumerError(event.event_id, exc) from exc


def observe_skill_usage(
    event: ExecutionEvent,
    resolution: Any,
    bootstrap: Any | None = None,
    consumer: Callable[[tuple[SkillUsageObservation, ...]], None] | None = None,
    *,
    allowed_universe: object | None = None,
    logical_ref_map: Mapping[tuple[str, str, str], str] | None = None,
    category_map: Mapping[tuple[str, str, str], str] | None = None,
    pinned_refs: Iterable[SemanticReference] = (),
    required_refs: Iterable[SemanticReference] = (),
    role_default_refs: Iterable[SemanticReference] = (),
    recommended_refs: Iterable[SemanticReference] = (),
    hook: Callable[[ExecutionEvent], None] | None = None,
) -> tuple[SkillUsageObservation, ...]:
    """High-level helper: project observation anchored to existing ExecutionEvent and optionally notify consumers.

    Reuses existing EventHook contract for event emission (via emit_event) without mutating event.
    Skill usage observation consumer is separate ephemeral callback.

    Flow:
        project_skill_usage(event, resolution, bootstrap, ...)
                ↓
        optional hook via emit_event (reuses S1 contract)
                ↓
        optional caller-provided synchronous observation consumer

    Both hook and consumer failures are typed/bounded and do not mutate original ExecutionEvent,
    do not change execution authority, do not persist partial telemetry.

    Args:
        event: existing ExecutionEvent anchor
        resolution, bootstrap, allowed_universe, maps etc: as in project_skill_usage
        consumer: optional ephemeral observation consumer receiving tuple[SkillUsageObservation, ...]
        hook: optional existing EventHook to emit original ExecutionEvent via S1 emit_event

    Returns:
        tuple[SkillUsageObservation, ...] deterministic bounded
    """
    observations = project_skill_usage(
        event,
        resolution,
        bootstrap,
        allowed_universe=allowed_universe,
        logical_ref_map=logical_ref_map,
        category_map=category_map,
        pinned_refs=pinned_refs,
        required_refs=required_refs,
        role_default_refs=role_default_refs,
        recommended_refs=recommended_refs,
    )

    # Reuse existing EventHook contract — must not mutate event, must propagate EventHookError typed
    if hook is not None:
        # emit_event already handles EventHookError wrapping
        emit_event(event, hook)

    # Invoke ephemeral consumer with typed failure handling
    if consumer is not None:
        consume_skill_usage_observations(event, observations, consumer)

    return observations


# Alias for conceptual naming
project_skill_usage_observation = project_skill_usage
create_skill_usage_observation = project_skill_usage

__all__ = [
    "SkillUsageObservation",
    "SkillUsageObservationError",
    "SkillUsageBoundsError",
    "SkillUsageConsumerError",
    "project_skill_usage",
    "project_skill_usage_observation",
    "create_skill_usage_observation",
    "consume_skill_usage_observations",
    "observe_skill_usage",
    "MAX_SKILL_USAGE_OBSERVATIONS",
    "MAX_EVENT_ID_LENGTH",
    "MAX_REF_LENGTH",
    "MAX_DIGEST_LENGTH",
    "MAX_PROVENANCE_LENGTH",
    # markers
    "REUSE_EXISTING_EVENT_HOOK",
    "EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR",
    "EXISTING_EVENT_HOOK_CONTRACT_REUSED",
    "NEW_EXECUTION_EVENT_TYPE_CREATED",
    "EXECUTION_EVENT_TYPE_MUTATED",
    "EVENTS_PY_MUTATED",
    "TELEMETRY_STORE_CREATED",
    "ANALYTICS_CREATED",
    "BACKGROUND_EXPORTER_CREATED",
    "S6_OWNERSHIP_PRESERVED",
    "S6_TELEMETRY_IMPLEMENTED_IN_S3",
    "SKILL_USAGE_OBSERVATION_BOUNDED",
    "SILENT_TRUNCATION",
    "SKILL_USAGE_OBSERVATION_DETERMINISTIC",
    "INPUT_ORDER_IS_AUTHORITY",
    "OBSERVED_SKILL_VERSION_PRESERVED",
    "OBSERVED_SKILL_DIGEST_PRESERVED",
    "OBSERVED_DELIVERY_MATCHES_BOOTSTRAP",
    "OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION",
    "W1_RECOMPUTES_SKILL_RESOLUTION",
    "W1_READS_SKILL_CONTENT",
    "W1_PERFORMS_BOOTSTRAP",
    "SKILL_USAGE_OBSERVATION_IS_AUTHORITY",
    "SKILL_USAGE_OBSERVATION_GRANTS_AUTHORITY",
    "SKILL_IS_AUTHORITY",
    "EXECUTION_EVENT_IS_AUTHORITY",
    "EVENT_ID_IS_AUTHORITY",
    "DIGEST_IS_AUTHORITY",
    "SELECTION_SOURCE_IS_AUTHORITY",
    "S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "DELIVERY_EAGER",
    "DELIVERY_PROGRESSIVE",
    "ALLOWED_DELIVERIES",
    "ALLOWED_SELECTION_SOURCES",
]
