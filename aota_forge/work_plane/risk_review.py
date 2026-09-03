"""Risk Semantic Contract Foundation — S4 M1 W1.

Thin semantic contract freezing ProcessDepth, MilestoneRiskEnvelope,
WorkItemRiskDelta with bounded fail-closed validation, deterministic
serialization and authority-negative invariants.

Invariants
----------
* ProcessDepth is bounded exact set FAST/STANDARD/DEEP, immutable, deterministic, fail-closed.
* FAST/STANDARD/DEEP are NOT operation authority.
* Risk dimensions bounded set of 9, no numeric thresholds frozen.
* MilestoneRiskEnvelope immutable bounded, no operation authority.
* WorkItemRiskDelta evidence-only, cannot self-downgrade, unresolved uncertainty cannot select shallower depth.
* RISK_MODEL_INTERFACE_FIRST=yes, RISK_CLASS_IS_AUTHORITY=no.
* S4 process automation cannot bypass S2 authority; risk objects cannot satisfy S2 AuthorityEvidence.
* No numeric risk scoring/calibration, no telemetry, no new runtime.

Dependency direction: work_plane.risk_review is semantic input contract only; core never imports it.
Reuse: TaskHandoff.process_depth_or_risk_projection_ref seam, SemanticStop taxonomy unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.handoff import SemanticReference

# ---------------------------------------------------------------------------
# Bounded capacity
# ---------------------------------------------------------------------------
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_EVIDENCE_REFS: int = 16
MAX_EVIDENCE_REF_LENGTH: int = 512
MAX_MILESTONE_REF_LENGTH: int = 512
MAX_WORK_ITEM_REF_LENGTH: int = 512
MAX_RATIONALE_LENGTH: int = 1024
MAX_DIMENSIONS: int = 16

# ---------------------------------------------------------------------------
# ProcessDepth — bounded exact set
# ---------------------------------------------------------------------------

@unique
class ProcessDepth(str, Enum):
    FAST = "FAST"
    STANDARD = "STANDARD"
    DEEP = "DEEP"


PROCESS_DEPTH_VALUES: frozenset[str] = frozenset(v.value for v in ProcessDepth)
PROCESS_DEPTH_ORDER: dict[str, int] = {"FAST": 1, "STANDARD": 2, "DEEP": 3}

# Invariants: depth is NOT operation authority
FAST_IS_OPERATION_AUTHORITY: bool = False
STANDARD_IS_OPERATION_AUTHORITY: bool = False
DEEP_IS_OPERATION_AUTHORITY: bool = False
PROCESS_DEPTH_IS_OPERATION_AUTHORITY: bool = False
PROCESS_DEPTH_IS_PLAN_AUTHORITY: bool = False

RISK_MODEL_INTERFACE_FIRST: bool = True
RISK_CLASS_IS_AUTHORITY: bool = False
MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY: bool = False
MILESTONE_RISK_ENVELOPE_IS_PLAN_AUTHORITY: bool = False
WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY: bool = False

# Risk threshold freeze flags
RISK_THRESHOLDS_EMPIRICAL: bool = True
NUMERIC_RISK_THRESHOLD_FROZEN: bool = False
NUMERIC_RISK_WEIGHT_FROZEN: bool = False

# Evidence-only invariants
WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY: bool = True
CALLER_CAN_SELF_DOWNGRADE_RISK: bool = False
MODEL_SELF_REPORT_IS_RISK_AUTHORITY: bool = False
WORKER_SELF_REPORT_IS_RISK_AUTHORITY: bool = False
UNRESOLVED_RISK_UNCERTAINTY_CANNOT_SELECT_SHALLOWER_PROCESS_DEPTH: bool = True

# S2 firewall
S4_PROCESS_AUTOMATION_IS_OPERATION_AUTHORITY: bool = False
AUTO_WITH_DEEP_VALIDATION_IS_OPERATION_AUTHORITY: bool = False
S4_PROCESS_AUTOMATION_CANNOT_BYPASS_S2_AUTHORITY: bool = True
S2_AUTHORITY_MODEL_RETAINED: bool = True


def parse_process_depth(value: object) -> ProcessDepth:
    if isinstance(value, ProcessDepth):
        return value
    if isinstance(value, str) and type(value) is str:
        # fail-closed: no whitespace tolerance, exact match only
        if value in PROCESS_DEPTH_VALUES:
            return ProcessDepth(value)
        raise ValueError(f"Unknown ProcessDepth: {value!r}. Must be one of {sorted(PROCESS_DEPTH_VALUES)}")
    if isinstance(value, Enum):
        raise TypeError(f"ProcessDepth must be FAST/STANDARD/DEEP string or ProcessDepth, got foreign Enum {type(value).__name__}")
    raise TypeError(f"ProcessDepth must be a string or ProcessDepth, got {type(value).__name__}")


def is_valid_process_depth(value: object) -> bool:
    if isinstance(value, ProcessDepth):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in PROCESS_DEPTH_VALUES
    return False


def process_depth_order(depth: ProcessDepth | str) -> int:
    pd = parse_process_depth(depth)
    return PROCESS_DEPTH_ORDER[pd.value]


def is_shallower(a: ProcessDepth | str, b: ProcessDepth | str) -> bool:
    return process_depth_order(a) < process_depth_order(b)


def is_deeper(a: ProcessDepth | str, b: ProcessDepth | str) -> bool:
    return process_depth_order(a) > process_depth_order(b)


def can_downgrade_approved_floor(approved: ProcessDepth | str, requested: ProcessDepth | str) -> bool:
    """Whether requested depth is shallower than approved floor — always False for downgrade."""
    return False if is_shallower(requested, approved) else True


# ---------------------------------------------------------------------------
# Risk Dimensions — bounded 9
# ---------------------------------------------------------------------------

@unique
class RiskDimension(str, Enum):
    blast_radius = "blast_radius"
    reversibility = "reversibility"
    uncertainty = "uncertainty"
    architecture_impact = "architecture_impact"
    authority_impact = "authority_impact"
    external_effects = "external_effects"
    data_integrity = "data_integrity"
    runtime_impact = "runtime_impact"
    shared_state = "shared_state"


RISK_DIMENSIONS: frozenset[str] = frozenset(v.value for v in RiskDimension)
RISK_DIMENSION_COUNT: int = len(RiskDimension)


def parse_risk_dimension(value: object) -> RiskDimension:
    if isinstance(value, RiskDimension):
        return value
    if isinstance(value, str) and type(value) is str:
        # strict exact match, no whitespace tolerance
        if value in RISK_DIMENSIONS:
            return RiskDimension(value)
        raise ValueError(f"Unknown RiskDimension: {value!r}. Must be one of {sorted(RISK_DIMENSIONS)}")
    if isinstance(value, Enum):
        raise TypeError(f"RiskDimension must be bounded RiskDimension or string, got foreign Enum {type(value).__name__}")
    raise TypeError(f"RiskDimension must be a string or RiskDimension, got {type(value).__name__}")


def is_valid_risk_dimension(value: object) -> bool:
    if isinstance(value, RiskDimension):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in RISK_DIMENSIONS
    return False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    # fail-closed whitespace: reject if stripped != original and original contains leading/trailing whitespace
    # also reject empty after strip
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace: {value!r}")
    if not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_len}")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _validate_optional_bounded_str(value: object, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    return _validate_bounded_str(value, label, max_len)


def _validate_evidence_refs(refs: object, label: str = "evidence_refs") -> tuple[str, ...]:
    if refs is None:
        return ()
    if not isinstance(refs, (tuple, list)):
        raise TypeError(f"{label} must be tuple/list, got {type(refs).__name__}")
    if len(refs) > MAX_EVIDENCE_REFS:
        raise ValueError(f"{label} count ({len(refs)}) exceeds maximum {MAX_EVIDENCE_REFS}")
    out: list[str] = []
    seen: set[str] = set()
    for idx, r in enumerate(refs):
        if not isinstance(r, str) or type(r) is not str:
            raise TypeError(f"{label}[{idx}] must be string, got {type(r).__name__}")
        s = r.strip()
        if not s:
            raise ValueError(f"{label}[{idx}] must be non-empty")
        if len(s) > MAX_EVIDENCE_REF_LENGTH:
            raise ValueError(f"{label}[{idx}] length ({len(s)}) exceeds maximum {MAX_EVIDENCE_REF_LENGTH}")
        if r != r.strip():
            raise ValueError(f"{label}[{idx}] must not contain leading/trailing whitespace")
        if s in seen:
            raise ValueError(f"{label}[{idx}] duplicate ref: {s!r}")
        seen.add(s)
        out.append(s)
    return tuple(out)


def _validate_dimensions(dims: object) -> tuple[RiskDimension, ...]:
    if dims is None:
        return ()
    if not isinstance(dims, (tuple, list)):
        raise TypeError(f"dimensions must be tuple/list, got {type(dims).__name__}")
    if len(dims) > MAX_DIMENSIONS:
        raise ValueError(f"dimensions count ({len(dims)}) exceeds maximum {MAX_DIMENSIONS}")
    out: list[RiskDimension] = []
    seen: set[str] = set()
    for idx, d in enumerate(dims):
        rd = parse_risk_dimension(d)
        if rd.value in seen:
            raise ValueError(f"dimensions[{idx}] duplicate dimension: {rd.value!r}")
        seen.add(rd.value)
        out.append(rd)
    # deterministic order: sorted by value
    out_sorted = sorted(out, key=lambda x: x.value)
    return tuple(out_sorted)


_ALLOWED_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "milestone_ref",
    "default_process_depth",
    "minimum_process_depth",
    "dimensions",
    "review_policy_refs",
    "escalation_boundary_ref",
    "uncertainty",
    "evidence_refs",
    "provenance_refs",
})

_ALLOWED_DELTA_FIELDS: frozenset[str] = frozenset({
    "work_item_ref",
    "milestone_ref",
    "observed_dimensions",
    "uncertainty",
    "semantic_choice",
    "architecture_delta",
    "authority_delta",
    "irreversible_delta",
    "declared_process_depth",
    "evidence_refs",
})

# ---------------------------------------------------------------------------
# MilestoneRiskEnvelope — immutable bounded
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MilestoneRiskEnvelope:
    """Bounded immutable milestone risk envelope (interface-first, not authority)."""

    milestone_ref: str
    default_process_depth: ProcessDepth
    minimum_process_depth: ProcessDepth
    dimensions: tuple[RiskDimension, ...] = ()
    review_policy_refs: tuple[str, ...] = ()
    escalation_boundary_ref: str | None = None
    uncertainty: str | None = None
    evidence_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "milestone_ref", _validate_bounded_str(self.milestone_ref, "milestone_ref", MAX_MILESTONE_REF_LENGTH))
        # depths: parse and validate
        default_pd = parse_process_depth(self.default_process_depth)
        minimum_pd = parse_process_depth(self.minimum_process_depth)
        object.__setattr__(self, "default_process_depth", default_pd)
        object.__setattr__(self, "minimum_process_depth", minimum_pd)
        # minimum cannot be deeper than default? Actually minimum is floor, default may be >= minimum
        # Enforce that minimum is not deeper than default (otherwise ambiguous)
        if is_deeper(minimum_pd, default_pd):
            raise ValueError(f"minimum_process_depth {minimum_pd.value} cannot be deeper than default {default_pd.value}")

        object.__setattr__(self, "dimensions", _validate_dimensions(self.dimensions))
        object.__setattr__(self, "review_policy_refs", _validate_evidence_refs(self.review_policy_refs, "review_policy_refs"))
        if self.escalation_boundary_ref is not None:
            object.__setattr__(self, "escalation_boundary_ref", _validate_bounded_str(self.escalation_boundary_ref, "escalation_boundary_ref", MAX_REF_LENGTH))
        if self.uncertainty is not None:
            # bounded uncertainty: must be non-empty string without whitespace padding, limited length
            object.__setattr__(self, "uncertainty", _validate_bounded_str(self.uncertainty, "uncertainty", MAX_RATIONALE_LENGTH))
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "provenance_refs", _validate_evidence_refs(self.provenance_refs, "provenance_refs"))

    # authority-negative
    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_plan_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "default_process_depth": self.default_process_depth.value,
            "dimensions": sorted([dim.value for dim in self.dimensions]),
            "evidence_refs": sorted(self.evidence_refs),
            "milestone_ref": self.milestone_ref,
            "minimum_process_depth": self.minimum_process_depth.value,
            "provenance_refs": sorted(self.provenance_refs),
            "review_policy_refs": sorted(self.review_policy_refs),
        }
        if self.escalation_boundary_ref is not None:
            d["escalation_boundary_ref"] = self.escalation_boundary_ref
        if self.uncertainty is not None:
            d["uncertainty"] = self.uncertainty
        return canonicalize(d, path="MilestoneRiskEnvelope")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_ref": self.milestone_ref,
            "default_process_depth": self.default_process_depth.value,
            "minimum_process_depth": self.minimum_process_depth.value,
            "dimensions": [d.value for d in self.dimensions],
            "review_policy_refs": list(self.review_policy_refs),
            "escalation_boundary_ref": self.escalation_boundary_ref,
            "uncertainty": self.uncertainty,
            "evidence_refs": list(self.evidence_refs),
            "provenance_refs": list(self.provenance_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneRiskEnvelope":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_ENVELOPE_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in MilestoneRiskEnvelope: {sorted(extra)}")
        for req in ("milestone_ref", "default_process_depth", "minimum_process_depth"):
            if req not in data:
                raise ValueError(f"Missing required field in MilestoneRiskEnvelope: {req!r}")
        # reject numeric threshold authority: if any numeric threshold field sneaked, it would be extra -> already rejected
        # also explicitly reject if caller tries to provide numeric fields via dimensions as numbers
        return cls(
            milestone_ref=data["milestone_ref"],
            default_process_depth=data["default_process_depth"],
            minimum_process_depth=data["minimum_process_depth"],
            dimensions=tuple(data.get("dimensions") or ()),
            review_policy_refs=tuple(data.get("review_policy_refs") or ()),
            escalation_boundary_ref=data.get("escalation_boundary_ref"),
            uncertainty=data.get("uncertainty"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            provenance_refs=tuple(data.get("provenance_refs") or ()),
        )

    def to_risk_projection_ref(self) -> SemanticReference:
        """Stable bounded semantic ref for Handoff seam — not authority."""
        # ref format: risk-envelope:<milestone_ref>:<digest[:16]>
        short = self.digest[:16]
        ref_str = f"risk-envelope:{self.milestone_ref}:{short}"
        return SemanticReference(ref=ref_str, digest=self.digest)

# ---------------------------------------------------------------------------
# WorkItemRiskDelta — lightweight bounded delta
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkItemRiskDelta:
    """Bounded immutable Work Item risk delta (evidence only)."""

    work_item_ref: str
    milestone_ref: str
    observed_dimensions: tuple[RiskDimension, ...] = ()
    uncertainty: str | None = None  # e.g., "unresolved" or "resolved" or None
    semantic_choice: bool = False
    architecture_delta: bool = False
    authority_delta: bool = False
    irreversible_delta: bool = False
    declared_process_depth: ProcessDepth | None = None  # evidence only
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_ref", _validate_bounded_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        object.__setattr__(self, "milestone_ref", _validate_bounded_str(self.milestone_ref, "milestone_ref", MAX_MILESTONE_REF_LENGTH))
        object.__setattr__(self, "observed_dimensions", _validate_dimensions(self.observed_dimensions))
        if self.uncertainty is not None:
            object.__setattr__(self, "uncertainty", _validate_bounded_str(self.uncertainty, "uncertainty", MAX_RATIONALE_LENGTH))
        for flag_name in ("semantic_choice", "architecture_delta", "authority_delta", "irreversible_delta"):
            val = getattr(self, flag_name)
            if type(val) is not bool:
                raise TypeError(f"{flag_name} must be bool, got {type(val).__name__}")
        if self.declared_process_depth is not None:
            pd = parse_process_depth(self.declared_process_depth)
            object.__setattr__(self, "declared_process_depth", pd)
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs, "evidence_refs"))

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def declared_depth_is_evidence_only(self) -> bool:
        return True

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "architecture_delta": self.architecture_delta,
            "authority_delta": self.authority_delta,
            "evidence_refs": sorted(self.evidence_refs),
            "irreversible_delta": self.irreversible_delta,
            "milestone_ref": self.milestone_ref,
            "observed_dimensions": sorted([dim.value for dim in self.observed_dimensions]),
            "semantic_choice": self.semantic_choice,
            "work_item_ref": self.work_item_ref,
        }
        if self.uncertainty is not None:
            d["uncertainty"] = self.uncertainty
        if self.declared_process_depth is not None:
            d["declared_process_depth"] = self.declared_process_depth.value
        return canonicalize(d, path="WorkItemRiskDelta")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_ref": self.work_item_ref,
            "milestone_ref": self.milestone_ref,
            "observed_dimensions": [d.value for d in self.observed_dimensions],
            "uncertainty": self.uncertainty,
            "semantic_choice": self.semantic_choice,
            "architecture_delta": self.architecture_delta,
            "authority_delta": self.authority_delta,
            "irreversible_delta": self.irreversible_delta,
            "declared_process_depth": self.declared_process_depth.value if self.declared_process_depth else None,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkItemRiskDelta":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_DELTA_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in WorkItemRiskDelta: {sorted(extra)}")
        for req in ("work_item_ref", "milestone_ref"):
            if req not in data:
                raise ValueError(f"Missing required field in WorkItemRiskDelta: {req!r}")
        return cls(
            work_item_ref=data["work_item_ref"],
            milestone_ref=data["milestone_ref"],
            observed_dimensions=tuple(data.get("observed_dimensions") or ()),
            uncertainty=data.get("uncertainty"),
            semantic_choice=bool(data.get("semantic_choice", False)) if isinstance(data.get("semantic_choice", False), bool) else data.get("semantic_choice"),
            architecture_delta=bool(data.get("architecture_delta", False)) if isinstance(data.get("architecture_delta", False), bool) else data.get("architecture_delta"),
            authority_delta=bool(data.get("authority_delta", False)) if isinstance(data.get("authority_delta", False), bool) else data.get("authority_delta"),
            irreversible_delta=bool(data.get("irreversible_delta", False)) if isinstance(data.get("irreversible_delta", False), bool) else data.get("irreversible_delta"),
            declared_process_depth=data.get("declared_process_depth"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )

    def to_risk_projection_ref(self) -> SemanticReference:
        short = self.digest[:16]
        ref_str = f"risk-delta:{self.work_item_ref}:{short}"
        return SemanticReference(ref=ref_str, digest=self.digest)


# ---------------------------------------------------------------------------
# Deterministic helpers — enforce invariants without granting authority
# ---------------------------------------------------------------------------

def resolve_effective_depth(
    envelope: MilestoneRiskEnvelope,
    delta: WorkItemRiskDelta | None = None,
    requested_depth: ProcessDepth | str | None = None,
) -> ProcessDepth:
    """Deterministically resolve effective process depth without authority grant.

    Rules:
    - If unresolved uncertainty in delta, cannot select shallower than envelope.minimum_process_depth.
    - Requested depth is evidence only; effective depth is at least envelope minimum.
    - If requested is None, use envelope default.
    """
    if not isinstance(envelope, MilestoneRiskEnvelope):
        raise TypeError(f"envelope must be MilestoneRiskEnvelope, got {type(envelope).__name__}")
    if delta is not None and not isinstance(delta, WorkItemRiskDelta):
        raise TypeError(f"delta must be WorkItemRiskDelta or None, got {type(delta).__name__}")

    # start from envelope default
    effective = envelope.default_process_depth

    # if requested provided, treat as evidence only, but cannot self-downgrade below minimum
    if requested_depth is not None:
        req = parse_process_depth(requested_depth)
        # caller cannot self-downgrade: if req shallower than minimum, ignore and keep minimum/default deeper
        if is_shallower(req, envelope.minimum_process_depth):
            # downgrade attempt fails closed: return deeper of effective and minimum
            effective = envelope.minimum_process_depth if is_deeper(envelope.minimum_process_depth, effective) else effective
            # if effective shallower than minimum, bump to minimum
            if is_shallower(effective, envelope.minimum_process_depth):
                effective = envelope.minimum_process_depth
            return effective
        # otherwise requested may be deeper, but still effective is at least requested if deeper?
        # Policy: effective is max(requested, minimum, default?) but we keep deterministic: choose deeper between effective and requested if requested deeper
        if is_deeper(req, effective):
            effective = req
        else:
            # requested is not shallower than minimum but may be shallower than default; cannot go shallower than default if uncertainty?
            # keep default as effective unless delta allows shallower
            pass

    # unresolved uncertainty prevents shallower than minimum (already) and also prevents shallower than default
    if delta is not None and delta.uncertainty is not None:
        # consider "unresolved" semantics case-insensitive contains unresolved
        u = delta.uncertainty.strip().lower()
        if "unresolved" in u or "uncertainty" in u:
            if is_shallower(effective, envelope.minimum_process_depth):
                effective = envelope.minimum_process_depth
            # also cannot select shallower than default when unresolved — keep at least default if default deeper than minimum
            if is_deeper(envelope.default_process_depth, effective):
                effective = envelope.default_process_depth

    # final floor: never shallower than minimum
    if is_shallower(effective, envelope.minimum_process_depth):
        effective = envelope.minimum_process_depth
    return effective


def can_select_shallower_depth(
    envelope: MilestoneRiskEnvelope,
    delta: WorkItemRiskDelta | None,
    requested: ProcessDepth | str,
) -> bool:
    """Whether requested depth may be selected given envelope and delta.

    Returns False if requested is shallower than approved floor or unresolved uncertainty blocks.
    """
    req = parse_process_depth(requested)
    if is_shallower(req, envelope.minimum_process_depth):
        return False
    if delta is not None and delta.uncertainty is not None:
        u = delta.uncertainty.strip().lower()
        if "unresolved" in u:
            # cannot select shallower than default when unresolved
            if is_shallower(req, envelope.default_process_depth):
                return False
    return True


def is_unresolved_uncertainty(delta: WorkItemRiskDelta) -> bool:
    if delta.uncertainty is None:
        return False
    return "unresolved" in delta.uncertainty.strip().lower()


# ---------------------------------------------------------------------------
# Handoff compatibility helpers
# ---------------------------------------------------------------------------

def envelope_to_handoff_ref(envelope: MilestoneRiskEnvelope) -> SemanticReference:
    return envelope.to_risk_projection_ref()


def delta_to_handoff_ref(delta: WorkItemRiskDelta) -> SemanticReference:
    return delta.to_risk_projection_ref()


__all__ = [
    "ProcessDepth",
    "PROCESS_DEPTH_VALUES",
    "PROCESS_DEPTH_ORDER",
    "FAST_IS_OPERATION_AUTHORITY",
    "STANDARD_IS_OPERATION_AUTHORITY",
    "DEEP_IS_OPERATION_AUTHORITY",
    "PROCESS_DEPTH_IS_OPERATION_AUTHORITY",
    "PROCESS_DEPTH_IS_PLAN_AUTHORITY",
    "RISK_MODEL_INTERFACE_FIRST",
    "RISK_CLASS_IS_AUTHORITY",
    "MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY",
    "MILESTONE_RISK_ENVELOPE_IS_PLAN_AUTHORITY",
    "WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY",
    "RISK_THRESHOLDS_EMPIRICAL",
    "NUMERIC_RISK_THRESHOLD_FROZEN",
    "NUMERIC_RISK_WEIGHT_FROZEN",
    "WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY",
    "CALLER_CAN_SELF_DOWNGRADE_RISK",
    "MODEL_SELF_REPORT_IS_RISK_AUTHORITY",
    "WORKER_SELF_REPORT_IS_RISK_AUTHORITY",
    "UNRESOLVED_RISK_UNCERTAINTY_CANNOT_SELECT_SHALLOWER_PROCESS_DEPTH",
    "S4_PROCESS_AUTOMATION_IS_OPERATION_AUTHORITY",
    "AUTO_WITH_DEEP_VALIDATION_IS_OPERATION_AUTHORITY",
    "S4_PROCESS_AUTOMATION_CANNOT_BYPASS_S2_AUTHORITY",
    "S2_AUTHORITY_MODEL_RETAINED",
    "parse_process_depth",
    "is_valid_process_depth",
    "process_depth_order",
    "is_shallower",
    "is_deeper",
    "can_downgrade_approved_floor",
    "RiskDimension",
    "RISK_DIMENSIONS",
    "RISK_DIMENSION_COUNT",
    "parse_risk_dimension",
    "is_valid_risk_dimension",
    "MilestoneRiskEnvelope",
    "WorkItemRiskDelta",
    "resolve_effective_depth",
    "can_select_shallower_depth",
    "is_unresolved_uncertainty",
    "envelope_to_handoff_ref",
    "delta_to_handoff_ref",
    "MAX_REF_LENGTH",
]
