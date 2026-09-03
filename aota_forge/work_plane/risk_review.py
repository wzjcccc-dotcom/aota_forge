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


# ---------------------------------------------------------------------------
# W2 — Review / Escalation Policy Contract
# Thin deterministic policy on top of MilestoneRiskEnvelope + WorkItemRiskDelta.
# Controls workflow/validation/review depth and escalation requirement.
# Does NOT grant workspace/test/git/shell/network authority.
# ---------------------------------------------------------------------------

# Bounded capacities for W2
MAX_JUSTIFICATION_LENGTH: int = 1024
MAX_END_CONDITION_LENGTH: int = 1024
MAX_REASONS: int = 16
MAX_REASON_LENGTH: int = 512

# Governance invariants for W2
DEFAULT_W_FORMAL_REVIEW: bool = False
RISK_TRIGGERED_W_REVIEW: bool = True
USER_ESCALATION_SEMANTIC_ONLY: bool = True
WORK_ITEM_USER_APPROVAL_DEFAULT: bool = False
USER_MILESTONE_APPROVAL_REQUIRED: bool = True
RISK_TRIGGERED_W_REVIEW_ENABLED: bool = True
EVERY_W_FORMAL_REVIEW: bool = False

HIGH_RISK_NO_SEMANTIC_CHOICE_AUTO_WITH_DEEP_VALIDATION: bool = True
SEMANTIC_CHOICE_REQUIRES_ESCALATION: bool = True
UNRESOLVED_RISK_UNCERTAINTY_FAILS_CLOSED: bool = True
CALLER_SELF_DOWNGRADE_BLOCKED_W2: bool = True

# Challenge role boundary — only frozen Agent Work Roles
NO_SIXTH_WORK_ROLE: bool = True
AGENT_WORK_ROLE_COUNT: int = 5
CHALLENGE_ROUTING_IS_OPERATION_AUTHORITY: bool = False
REVIEW_DISPOSITION_IS_OPERATION_AUTHORITY: bool = False
CHALLENGE_ROUTING_IS_PLAN_AUTHORITY: bool = False

# Frontier / cross-subplan invariants
EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED: bool = True
NEW_ACCEPTANCE_ONTOLOGY_CREATED: bool = False
S1_STOP_ONTOLOGY_CHANGE_REQUIRED: bool = False
S1_STOP_ONTOLOGY_CHANGED: bool = False
S1_HANDOFF_SCHEMA_CHANGED: bool = False
S2_AUTHORITY_MODEL_CHANGED: bool = False

# Process depth is NOT operation authority (re-affirm)
REVIEW_ESCALATION_DISPOSITION_IS_OPERATION_AUTHORITY: bool = False
SELECTED_PROCESS_DEPTH_IS_OPERATION_AUTHORITY: bool = False


@unique
class ReviewTrigger(str, Enum):
    CORE_CONTRACT_OR_PROTOCOL_CHANGE = "CORE_CONTRACT_OR_PROTOCOL_CHANGE"
    AUTHORITY_OR_SECURITY_BOUNDARY = "AUTHORITY_OR_SECURITY_BOUNDARY"
    LARGE_DOWNSTREAM_DEPENDENCY_FANOUT = "LARGE_DOWNSTREAM_DEPENDENCY_FANOUT"
    IRREVERSIBLE_EFFECT = "IRREVERSIBLE_EFFECT"
    UNEXPECTED_DEFECT = "UNEXPECTED_DEFECT"
    RISK_ENVELOPE_VIOLATION = "RISK_ENVELOPE_VIOLATION"
    CROSS_SUBPLAN_CONTRACT_CHANGE = "CROSS_SUBPLAN_CONTRACT_CHANGE"
    ACCEPTED_FRONTIER_ANCESTRY_ANOMALY = "ACCEPTED_FRONTIER_ANCESTRY_ANOMALY"
    UNRESOLVED_RISK_UNCERTAINTY = "UNRESOLVED_RISK_UNCERTAINTY"


REVIEW_TRIGGER_VALUES: frozenset[str] = frozenset(v.value for v in ReviewTrigger)
REVIEW_TRIGGER_FAIL_CLOSED: bool = True
UNKNOWN_REVIEW_TRIGGER_FAIL_CLOSED: bool = True


@unique
class UserEscalationTrigger(str, Enum):
    NEW_MILESTONE_APPROVAL = "NEW_MILESTONE_APPROVAL"
    MATERIAL_PLAN_SCOPE_CHANGE = "MATERIAL_PLAN_SCOPE_CHANGE"
    NEW_PRODUCT_REQUIREMENT = "NEW_PRODUCT_REQUIREMENT"
    REQUIREMENT_CONTRADICTION = "REQUIREMENT_CONTRADICTION"
    ARCHITECTURE_CHANGE = "ARCHITECTURE_CHANGE"
    AUTHORITY_CHANGE = "AUTHORITY_CHANGE"
    HIGH_RISK_IRREVERSIBLE_POLICY_DECISION = "HIGH_RISK_IRREVERSIBLE_POLICY_DECISION"
    REPLAN_CROSSES_APPROVED_MILESTONE_BOUNDARY = "REPLAN_CROSSES_APPROVED_MILESTONE_BOUNDARY"


USER_ESCALATION_TRIGGER_VALUES: frozenset[str] = frozenset(v.value for v in UserEscalationTrigger)
USER_ESCALATION_FAIL_CLOSED: bool = True
UNKNOWN_ESCALATION_TRIGGER_FAIL_CLOSED: bool = True


@unique
class ChallengeRole(str, Enum):
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    TASK_MAIN = "task-main"


CHALLENGE_ROLE_VALUES: frozenset[str] = frozenset(v.value for v in ChallengeRole)
# project-steward is NOT normal W risk challenger
CHALLENGE_ROLE_PROJECT_STEWARD_IS_NORMAL_CHALLENGER: bool = False
CHALLENGE_ROLE_IS_OPERATION_AUTHORITY: bool = False


@unique
class ChallengeKind(str, Enum):
    ARCHITECTURE_FEASIBILITY = "ARCHITECTURE_FEASIBILITY"
    TECHNICAL_ACCEPTANCE = "TECHNICAL_ACCEPTANCE"
    SEMANTIC_RECONCILIATION = "SEMANTIC_RECONCILIATION"


CHALLENGE_KIND_VALUES: frozenset[str] = frozenset(v.value for v in ChallengeKind)


def parse_review_trigger(value: object) -> ReviewTrigger:
    if isinstance(value, ReviewTrigger):
        return value
    if isinstance(value, str) and type(value) is str:
        if value in REVIEW_TRIGGER_VALUES:
            return ReviewTrigger(value)
        raise ValueError(f"Unknown ReviewTrigger: {value!r}. Must be one of {sorted(REVIEW_TRIGGER_VALUES)}")
    if isinstance(value, Enum):
        raise TypeError(f"ReviewTrigger must be bounded ReviewTrigger or string, got foreign Enum {type(value).__name__}")
    raise TypeError(f"ReviewTrigger must be a string or ReviewTrigger, got {type(value).__name__}")


def is_valid_review_trigger(value: object) -> bool:
    if isinstance(value, ReviewTrigger):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in REVIEW_TRIGGER_VALUES
    return False


def parse_user_escalation_trigger(value: object) -> UserEscalationTrigger:
    if isinstance(value, UserEscalationTrigger):
        return value
    if isinstance(value, str) and type(value) is str:
        if value in USER_ESCALATION_TRIGGER_VALUES:
            return UserEscalationTrigger(value)
        raise ValueError(f"Unknown UserEscalationTrigger: {value!r}. Must be one of {sorted(USER_ESCALATION_TRIGGER_VALUES)}")
    if isinstance(value, Enum):
        raise TypeError(f"UserEscalationTrigger must be bounded UserEscalationTrigger or string, got foreign Enum {type(value).__name__}")
    raise TypeError(f"UserEscalationTrigger must be a string or UserEscalationTrigger, got {type(value).__name__}")


def is_valid_user_escalation_trigger(value: object) -> bool:
    if isinstance(value, UserEscalationTrigger):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in USER_ESCALATION_TRIGGER_VALUES
    return False


def parse_challenge_role(value: object) -> ChallengeRole:
    if isinstance(value, ChallengeRole):
        return value
    if isinstance(value, str) and type(value) is str:
        if value in CHALLENGE_ROLE_VALUES:
            return ChallengeRole(value)
        raise ValueError(f"Unknown ChallengeRole: {value!r}. Must be one of {sorted(CHALLENGE_ROLE_VALUES)}")
    if isinstance(value, Enum):
        # Reject foreign Enum including AgentWorkRole.project_steward as normal challenger via this path
        raise TypeError(f"ChallengeRole must be analyst/reviewer/task-main, got foreign Enum {type(value).__name__}")
    raise TypeError(f"ChallengeRole must be a string or ChallengeRole, got {type(value).__name__}")


def is_valid_challenge_role(value: object) -> bool:
    if isinstance(value, ChallengeRole):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in CHALLENGE_ROLE_VALUES
    return False


def parse_challenge_kind(value: object) -> ChallengeKind:
    if isinstance(value, ChallengeKind):
        return value
    if isinstance(value, str) and type(value) is str:
        if value in CHALLENGE_KIND_VALUES:
            return ChallengeKind(value)
        raise ValueError(f"Unknown ChallengeKind: {value!r}. Must be one of {sorted(CHALLENGE_KIND_VALUES)}")
    if isinstance(value, Enum):
        raise TypeError(f"ChallengeKind must be bounded ChallengeKind or string, got foreign Enum {type(value).__name__}")
    raise TypeError(f"ChallengeKind must be a string or ChallengeKind, got {type(value).__name__}")


# Deterministic challenge routing table (ReviewTrigger -> ChallengeRole/Kind)
_REVIEW_TRIGGER_CHALLENGE_MAP: dict[ReviewTrigger, tuple[ChallengeRole, ChallengeKind]] = {
    ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE: (ChallengeRole.ANALYST, ChallengeKind.ARCHITECTURE_FEASIBILITY),
    ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY: (ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE),
    ReviewTrigger.LARGE_DOWNSTREAM_DEPENDENCY_FANOUT: (ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE),
    ReviewTrigger.IRREVERSIBLE_EFFECT: (ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE),
    ReviewTrigger.UNEXPECTED_DEFECT: (ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE),
    ReviewTrigger.RISK_ENVELOPE_VIOLATION: (ChallengeRole.ANALYST, ChallengeKind.ARCHITECTURE_FEASIBILITY),
    ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE: (ChallengeRole.ANALYST, ChallengeKind.ARCHITECTURE_FEASIBILITY),
    ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY: (ChallengeRole.TASK_MAIN, ChallengeKind.SEMANTIC_RECONCILIATION),
    ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY: (ChallengeRole.ANALYST, ChallengeKind.ARCHITECTURE_FEASIBILITY),
}

# User escalation always routes to task-main semantic reconciliation
_USER_ESCALATION_CHALLENGE: tuple[ChallengeRole, ChallengeKind] = (ChallengeRole.TASK_MAIN, ChallengeKind.SEMANTIC_RECONCILIATION)


def _validate_justification(value: object, label: str = "justification") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be string or None, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ValueError(f"{label} must be non-empty when provided")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace")
    if len(s) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError(f"{label} length ({len(s)}) exceeds maximum {MAX_JUSTIFICATION_LENGTH}")
    if "\x00" in s:
        raise ValueError(f"{label} must not contain NUL")
    return s


def _validate_end_condition(value: object, label: str = "end_condition") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be string or None, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ValueError(f"{label} must be non-empty when provided")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace")
    if len(s) > MAX_END_CONDITION_LENGTH:
        raise ValueError(f"{label} length ({len(s)}) exceeds maximum {MAX_END_CONDITION_LENGTH}")
    if "\x00" in s:
        raise ValueError(f"{label} must not contain NUL")
    return s


def _validate_reasons(reasons: object) -> tuple[str, ...]:
    if reasons is None:
        return ()
    if not isinstance(reasons, (tuple, list)):
        raise TypeError(f"reasons must be tuple/list, got {type(reasons).__name__}")
    if len(reasons) > MAX_REASONS:
        raise ValueError(f"reasons count ({len(reasons)}) exceeds maximum {MAX_REASONS}")
    out: list[str] = []
    seen: set[str] = set()
    for idx, r in enumerate(reasons):
        if not isinstance(r, str) or type(r) is not str:
            raise TypeError(f"reasons[{idx}] must be string, got {type(r).__name__}")
        s = r.strip()
        if not s:
            raise ValueError(f"reasons[{idx}] must be non-empty")
        if r != r.strip():
            raise ValueError(f"reasons[{idx}] must not contain leading/trailing whitespace")
        if len(s) > MAX_REASON_LENGTH:
            raise ValueError(f"reasons[{idx}] length ({len(s)}) exceeds maximum {MAX_REASON_LENGTH}")
        if s in seen:
            raise ValueError(f"reasons[{idx}] duplicate reason: {s!r}")
        seen.add(s)
        out.append(s)
    return tuple(sorted(out))


def _deepen_one(depth: ProcessDepth) -> ProcessDepth:
    if depth == ProcessDepth.FAST:
        return ProcessDepth.STANDARD
    if depth == ProcessDepth.STANDARD:
        return ProcessDepth.DEEP
    return ProcessDepth.DEEP


def _select_process_depth_deterministic(
    envelope: MilestoneRiskEnvelope,
    delta: WorkItemRiskDelta | None,
    review_triggers: tuple[ReviewTrigger, ...],
    has_escalation: bool,
    cross_subplan: bool,
    frontier_anomaly: bool,
) -> ProcessDepth:
    # Start from deterministic effective depth via W1 helper (handles floor + unresolved)
    baseline = resolve_effective_depth(envelope, delta, None)
    # Determine if meaningful added risk exists
    meaningful_risk = False
    if delta is not None:
        if delta.observed_dimensions:
            meaningful_risk = True
        if delta.architecture_delta or delta.authority_delta or delta.irreversible_delta:
            meaningful_risk = True
        # semantic_choice itself is escalation but also meaningful risk for deepening
        if delta.semantic_choice:
            meaningful_risk = True
    # Any trigger or cross/anomaly implies meaningful risk
    if review_triggers or cross_subplan or frontier_anomaly:
        meaningful_risk = True

    # Unresolved uncertainty forces at least baseline (already) and not shallower
    # If meaningful risk, deepen monotonically: FAST->STANDARD, STANDARD->DEEP, DEEP stays
    # If review_triggers or cross/anomaly, force DEEP or no-shallower
    if review_triggers or cross_subplan or frontier_anomaly:
        # Deterministic: deepen to DEEP if not already DEEP
        if baseline != ProcessDepth.DEEP:
            # If baseline is FAST, we need to go to DEEP via deterministic steps (no weights)
            # Choose DEEP directly for review triggers (meets spec DEEP or no-shallower)
            baseline = ProcessDepth.DEEP
        # if already DEEP, stays DEEP
        return baseline
    if meaningful_risk:
        # Higher execution risk without semantic choice still deepens
        # Deterministically deepen one level (FAST->STANDARD, STANDARD->DEEP)
        # This satisfies monotonic deepening without numeric weights
        if baseline != ProcessDepth.DEEP:
            # If envelope default is FAST and delta adds risk, go to STANDARD at least
            # For higher execution risk (multiple dimensions), we may need DEEP
            # Simple deterministic: if any delta flag irreversible or authority, go directly DEEP
            if delta is not None and (delta.irreversible_delta or delta.authority_delta or delta.architecture_delta):
                baseline = ProcessDepth.DEEP
            else:
                baseline = _deepen_one(baseline)
        return baseline
    # No meaningful risk: respect envelope floor/baseline
    return baseline


@dataclass(frozen=True)
class ReviewEscalationDisposition:
    """Thin deterministic disposition — no execution authority.

    Fields bound to carry equivalent semantics to IDENTIFIED_RISK / JUSTIFICATION / END_CONDITION
    as bounded refs/strings, not unbounded dumps.
    """

    selected_process_depth: ProcessDepth
    formal_review_required: bool
    challenge_role: ChallengeRole | None
    challenge_kind: ChallengeKind | None
    semantic_escalation_required: bool
    auto_continuation_eligible: bool
    identified_risk: ReviewTrigger | UserEscalationTrigger | None
    justification: str | None
    end_condition: str | None
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    cross_subplan_contract_change: bool = False
    accepted_frontier_anomaly: bool = False

    def __post_init__(self) -> None:
        # selected_process_depth fail-closed
        pd = parse_process_depth(self.selected_process_depth)
        object.__setattr__(self, "selected_process_depth", pd)
        # bools strict
        for flag_name in ("formal_review_required", "semantic_escalation_required", "auto_continuation_eligible", "cross_subplan_contract_change", "accepted_frontier_anomaly"):
            val = getattr(self, flag_name)
            if type(val) is not bool:
                raise TypeError(f"{flag_name} must be bool, got {type(val).__name__}")
        # challenge_role / kind may be None or valid
        if self.challenge_role is not None:
            cr = parse_challenge_role(self.challenge_role)
            object.__setattr__(self, "challenge_role", cr)
        if self.challenge_kind is not None:
            ck = parse_challenge_kind(self.challenge_kind)
            object.__setattr__(self, "challenge_kind", ck)
        # identified_risk may be ReviewTrigger or UserEscalationTrigger or None
        if self.identified_risk is not None:
            if isinstance(self.identified_risk, ReviewTrigger):
                pass
            elif isinstance(self.identified_risk, UserEscalationTrigger):
                pass
            elif isinstance(self.identified_risk, str) and type(self.identified_risk) is str:
                # Try both triggers
                if is_valid_review_trigger(self.identified_risk):
                    object.__setattr__(self, "identified_risk", parse_review_trigger(self.identified_risk))
                elif is_valid_user_escalation_trigger(self.identified_risk):
                    object.__setattr__(self, "identified_risk", parse_user_escalation_trigger(self.identified_risk))
                else:
                    raise ValueError(f"Unknown identified_risk: {self.identified_risk!r}")
            elif isinstance(self.identified_risk, Enum):
                raise TypeError(f"identified_risk must be ReviewTrigger/UserEscalationTrigger or None, got foreign Enum {type(self.identified_risk).__name__}")
            else:
                raise TypeError(f"identified_risk must be ReviewTrigger/UserEscalationTrigger or None, got {type(self.identified_risk).__name__}")
        # justification / end_condition bounded
        if self.justification is not None:
            object.__setattr__(self, "justification", _validate_justification(self.justification, "justification"))
        if self.end_condition is not None:
            object.__setattr__(self, "end_condition", _validate_end_condition(self.end_condition, "end_condition"))
        object.__setattr__(self, "reasons", _validate_reasons(self.reasons))
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs, "evidence_refs"))
        # Authority-negative: disposition never grants operation authority
        # No field may be interpreted as AuthorityEvidence

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_plan_authority(self) -> bool:
        return False

    @property
    def grants_workspace_mutation(self) -> bool:
        return False

    @property
    def grants_test_execution(self) -> bool:
        return False

    @property
    def grants_git_operation(self) -> bool:
        return False

    @property
    def grants_restricted_shell(self) -> bool:
        return False

    @property
    def grants_network(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "accepted_frontier_anomaly": self.accepted_frontier_anomaly,
            "auto_continuation_eligible": self.auto_continuation_eligible,
            "cross_subplan_contract_change": self.cross_subplan_contract_change,
            "evidence_refs": sorted(self.evidence_refs),
            "formal_review_required": self.formal_review_required,
            "reasons": sorted(self.reasons),
            "selected_process_depth": self.selected_process_depth.value,
            "semantic_escalation_required": self.semantic_escalation_required,
        }
        if self.challenge_role is not None:
            d["challenge_role"] = self.challenge_role.value
        if self.challenge_kind is not None:
            d["challenge_kind"] = self.challenge_kind.value
        if self.identified_risk is not None:
            d["identified_risk"] = self.identified_risk.value
        if self.justification is not None:
            d["justification"] = self.justification
        if self.end_condition is not None:
            d["end_condition"] = self.end_condition
        return canonicalize(d, path="ReviewEscalationDisposition")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_process_depth": self.selected_process_depth.value,
            "formal_review_required": self.formal_review_required,
            "challenge_role": self.challenge_role.value if self.challenge_role else None,
            "challenge_kind": self.challenge_kind.value if self.challenge_kind else None,
            "semantic_escalation_required": self.semantic_escalation_required,
            "auto_continuation_eligible": self.auto_continuation_eligible,
            "identified_risk": self.identified_risk.value if self.identified_risk else None,
            "justification": self.justification,
            "end_condition": self.end_condition,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
            "cross_subplan_contract_change": self.cross_subplan_contract_change,
            "accepted_frontier_anomaly": self.accepted_frontier_anomaly,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewEscalationDisposition":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {
            "selected_process_depth", "formal_review_required", "challenge_role", "challenge_kind",
            "semantic_escalation_required", "auto_continuation_eligible", "identified_risk",
            "justification", "end_condition", "reasons", "evidence_refs",
            "cross_subplan_contract_change", "accepted_frontier_anomaly",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in ReviewEscalationDisposition: {sorted(extra)}")
        for req in ("selected_process_depth", "formal_review_required", "semantic_escalation_required", "auto_continuation_eligible"):
            if req not in data:
                raise ValueError(f"Missing required field in ReviewEscalationDisposition: {req!r}")
        # identified_risk may be string, parse via is_valid
        ir = data.get("identified_risk")
        if isinstance(ir, str) and type(ir) is str:
            if is_valid_review_trigger(ir):
                ir = parse_review_trigger(ir)
            elif is_valid_user_escalation_trigger(ir):
                ir = parse_user_escalation_trigger(ir)
            elif ir is not None:
                raise ValueError(f"Unknown identified_risk: {ir!r}")
        cr = data.get("challenge_role")
        if isinstance(cr, str) and type(cr) is str:
            cr = parse_challenge_role(cr)
        ck = data.get("challenge_kind")
        if isinstance(ck, str) and type(ck) is str:
            ck = parse_challenge_kind(ck)
        return cls(
            selected_process_depth=data["selected_process_depth"],
            formal_review_required=bool(data["formal_review_required"]) if isinstance(data["formal_review_required"], bool) else data["formal_review_required"],
            challenge_role=cr,
            challenge_kind=ck,
            semantic_escalation_required=bool(data["semantic_escalation_required"]) if isinstance(data["semantic_escalation_required"], bool) else data["semantic_escalation_required"],
            auto_continuation_eligible=bool(data["auto_continuation_eligible"]) if isinstance(data["auto_continuation_eligible"], bool) else data["auto_continuation_eligible"],
            identified_risk=ir,
            justification=data.get("justification"),
            end_condition=data.get("end_condition"),
            reasons=tuple(data.get("reasons") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            cross_subplan_contract_change=bool(data.get("cross_subplan_contract_change", False)) if isinstance(data.get("cross_subplan_contract_change", False), bool) else data.get("cross_subplan_contract_change"),
            accepted_frontier_anomaly=bool(data.get("accepted_frontier_anomaly", False)) if isinstance(data.get("accepted_frontier_anomaly", False), bool) else data.get("accepted_frontier_anomaly"),
        )


def _resolve_challenge_for_triggers(
    review_triggers: tuple[ReviewTrigger, ...],
    escalation_triggers: tuple[UserEscalationTrigger, ...],
    cross_subplan: bool,
    frontier_anomaly: bool,
    formal_review_required: bool,
    semantic_escalation_required: bool,
) -> tuple[ChallengeRole | None, ChallengeKind | None, ReviewTrigger | UserEscalationTrigger | None]:
    # Deterministic priority: escalation > frontier anomaly > cross-subplan > first review trigger
    if escalation_triggers:
        # first escalation trigger deterministically sorted
        first = sorted(escalation_triggers, key=lambda x: x.value)[0]
        role, kind = _USER_ESCALATION_CHALLENGE
        return role, kind, first
    if frontier_anomaly:
        # frontier anomaly maps to task-main
        return ChallengeRole.TASK_MAIN, ChallengeKind.SEMANTIC_RECONCILIATION, ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY
    if cross_subplan:
        return ChallengeRole.ANALYST, ChallengeKind.ARCHITECTURE_FEASIBILITY, ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE
    if review_triggers:
        # deterministic: sorted by value, pick first
        first = sorted(review_triggers, key=lambda x: x.value)[0]
        role, kind = _REVIEW_TRIGGER_CHALLENGE_MAP.get(first, (ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE))
        return role, kind, first
    if formal_review_required:
        # fallback if formal review without explicit trigger (should not happen)
        return ChallengeRole.REVIEWER, ChallengeKind.TECHNICAL_ACCEPTANCE, None
    return None, None, None


def evaluate_work_item_risk_policy(
    envelope: MilestoneRiskEnvelope,
    delta: WorkItemRiskDelta | None = None,
    review_triggers: tuple[ReviewTrigger | str, ...] | list[ReviewTrigger | str] | None = None,
    escalation_triggers: tuple[UserEscalationTrigger | str, ...] | list[UserEscalationTrigger | str] | None = None,
    *,
    cross_subplan_contract_change: bool = False,
    accepted_frontier_anomaly: bool = False,
    evidence_refs: tuple[str, ...] | list[str] | None = None,
    justification: str | None = None,
    end_condition: str | None = None,
    reasons: tuple[str, ...] | list[str] | None = None,
) -> ReviewEscalationDisposition:
    """Deterministically evaluate risk policy to disposition. Fail-closed, no authority grant.

    Inputs are bounded and fail-closed on unknown values. Selected depth is
    monotonic and never shallower than envelope floor. Unresolved uncertainty
    cannot select shallower depth.
    """
    if not isinstance(envelope, MilestoneRiskEnvelope):
        raise TypeError(f"envelope must be MilestoneRiskEnvelope, got {type(envelope).__name__}")
    if delta is not None and not isinstance(delta, WorkItemRiskDelta):
        raise TypeError(f"delta must be WorkItemRiskDelta or None, got {type(delta).__name__}")
    if type(cross_subplan_contract_change) is not bool:
        raise TypeError(f"cross_subplan_contract_change must be bool, got {type(cross_subplan_contract_change).__name__}")
    if type(accepted_frontier_anomaly) is not bool:
        raise TypeError(f"accepted_frontier_anomaly must be bool, got {type(accepted_frontier_anomaly).__name__}")

    # Parse review_triggers fail-closed
    parsed_review: tuple[ReviewTrigger, ...]
    if review_triggers is None:
        parsed_review = ()
    else:
        if not isinstance(review_triggers, (tuple, list)):
            raise TypeError(f"review_triggers must be tuple/list, got {type(review_triggers).__name__}")
        tmp: list[ReviewTrigger] = []
        seen: set[str] = set()
        for idx, t in enumerate(review_triggers):
            rt = parse_review_trigger(t)
            if rt.value in seen:
                raise ValueError(f"review_triggers[{idx}] duplicate trigger: {rt.value!r}")
            seen.add(rt.value)
            tmp.append(rt)
        parsed_review = tuple(sorted(tmp, key=lambda x: x.value))

    # Parse escalation_triggers fail-closed
    parsed_escalation: tuple[UserEscalationTrigger, ...]
    if escalation_triggers is None:
        parsed_escalation = ()
    else:
        if not isinstance(escalation_triggers, (tuple, list)):
            raise TypeError(f"escalation_triggers must be tuple/list, got {type(escalation_triggers).__name__}")
        tmp2: list[UserEscalationTrigger] = []
        seen2: set[str] = set()
        for idx, t in enumerate(escalation_triggers):
            et = parse_user_escalation_trigger(t)
            if et.value in seen2:
                raise ValueError(f"escalation_triggers[{idx}] duplicate trigger: {et.value!r}")
            seen2.add(et.value)
            tmp2.append(et)
        parsed_escalation = tuple(sorted(tmp2, key=lambda x: x.value))

    # Also implicitly handle delta unresolved uncertainty as review trigger
    # If delta has unresolved uncertainty and no explicit UNRESOLVED_RISK_UNCERTAINTY trigger, we treat it as implicit review trigger for fail-closed
    implicit_review_triggers = list(parsed_review)
    if delta is not None and is_unresolved_uncertainty(delta):
        if ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY not in parsed_review:
            implicit_review_triggers.append(ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY)
            # keep deterministic sorted
            implicit_review_triggers = sorted(set(implicit_review_triggers), key=lambda x: x.value)
            parsed_review = tuple(implicit_review_triggers)

    # Cross-subplan and frontier anomaly also imply review triggers if not already
    if cross_subplan_contract_change and ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE not in parsed_review:
        parsed_review = tuple(sorted(list(parsed_review) + [ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE], key=lambda x: x.value))
    if accepted_frontier_anomaly and ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY not in parsed_review:
        parsed_review = tuple(sorted(list(parsed_review) + [ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY], key=lambda x: x.value))

    # Determine semantic escalation required
    # Semantic-only escalation: true if any escalation_trigger, or delta indicates semantic choice/architecture/authority
    semantic_escalation_required = False
    if parsed_escalation:
        semantic_escalation_required = True
    if delta is not None:
        if delta.semantic_choice or delta.architecture_delta or delta.authority_delta:
            semantic_escalation_required = True

    # Formal review required: default False, true if any review trigger, cross, anomaly, unresolved
    formal_review_required = False
    if parsed_review:
        formal_review_required = True
    if cross_subplan_contract_change or accepted_frontier_anomaly:
        formal_review_required = True

    # Select process depth deterministically (never shallower than floor)
    selected_depth = _select_process_depth_deterministic(
        envelope, delta, parsed_review, semantic_escalation_required, cross_subplan_contract_change, accepted_frontier_anomaly
    )
    # Ensure never shallower than minimum (already ensured) and if unresolved, not shallower than default
    if delta is not None and is_unresolved_uncertainty(delta):
        if is_shallower(selected_depth, envelope.default_process_depth):
            selected_depth = envelope.default_process_depth
        if is_shallower(selected_depth, envelope.minimum_process_depth):
            selected_depth = envelope.minimum_process_depth

    # Challenge routing
    challenge_role, challenge_kind, identified_risk = _resolve_challenge_for_triggers(
        parsed_review, parsed_escalation, cross_subplan_contract_change, accepted_frontier_anomaly,
        formal_review_required, semantic_escalation_required
    )
    # If semantic escalation without review trigger, identified_risk may be escalation trigger
    if semantic_escalation_required and identified_risk is None and parsed_escalation:
        identified_risk = parsed_escalation[0]
    elif semantic_escalation_required and delta is not None and identified_risk is None:
        # Map delta architecture/authority to escalation triggers for identified_risk
        if delta.architecture_delta:
            identified_risk = UserEscalationTrigger.ARCHITECTURE_CHANGE
        elif delta.authority_delta:
            identified_risk = UserEscalationTrigger.AUTHORITY_CHANGE
        elif delta.semantic_choice:
            identified_risk = UserEscalationTrigger.MATERIAL_PLAN_SCOPE_CHANGE
    # Semantic escalation via delta must still route challenge to task-main even without explicit triggers
    if semantic_escalation_required and challenge_role is None:
        # Deterministic: semantic escalation always routes to task-main reconciliation
        challenge_role, challenge_kind = ChallengeRole.TASK_MAIN, ChallengeKind.SEMANTIC_RECONCILIATION

    # Auto continuation eligible: true when no semantic escalation, regardless of depth
    # But cross-subplan cannot routine-auto -> auto_continuation_eligible False when cross_subplan
    # Frontier anomaly also not auto
    # Formal review with semantic escalation -> not auto
    auto_continuation_eligible = False
    if not semantic_escalation_required:
        if not cross_subplan_contract_change and not accepted_frontier_anomaly:
            auto_continuation_eligible = True
        else:
            auto_continuation_eligible = False
    else:
        auto_continuation_eligible = False

    # Build justification/end_condition: if formal review required, must carry bounded justification/end_condition or defaults
    # For tests we allow caller-provided values; otherwise derive bounded defaults deterministically
    if formal_review_required:
        if justification is None:
            # Derive deterministic bounded justification from identified_risk
            if identified_risk is not None:
                justification = f"review required for {identified_risk.value}"
            else:
                justification = "review required for risk trigger"
        if end_condition is None:
            end_condition = "review passes and risk reconciled"
    else:
        # No formal review: justification/end_condition should be None unless caller provided (but we keep as provided if any)
        if justification is not None and not formal_review_required:
            # Allow caller-provided but still bounded; keep it
            pass
        if end_condition is not None and not formal_review_required:
            pass

    # Validate justification/end_condition now (fail-closed if provided invalid)
    if justification is not None:
        justification = _validate_justification(justification, "justification")
    if end_condition is not None:
        end_condition = _validate_end_condition(end_condition, "end_condition")

    # Reasons / evidence_refs bounded
    parsed_reasons = _validate_reasons(reasons)
    # If no reasons provided but triggers exist, derive deterministic reasons
    if not parsed_reasons and (parsed_review or parsed_escalation or cross_subplan_contract_change or accepted_frontier_anomaly):
        derived: list[str] = []
        for rt in parsed_review:
            derived.append(rt.value)
        for et in parsed_escalation:
            derived.append(et.value)
        if cross_subplan_contract_change:
            derived.append("cross_subplan_contract_change")
        if accepted_frontier_anomaly:
            derived.append("accepted_frontier_anomaly")
        # bound and dedup deterministically
        parsed_reasons = _validate_reasons(sorted(set(derived))[:MAX_REASONS])

    parsed_evidence = _validate_evidence_refs(evidence_refs, "evidence_refs") if evidence_refs is not None else ()
    # also merge delta evidence and envelope evidence deterministically? Keep bounded, but include for traceability
    # For simplicity, evidence_refs are as provided; caller can include delta/envelope refs explicitly

    return ReviewEscalationDisposition(
        selected_process_depth=selected_depth,
        formal_review_required=formal_review_required,
        challenge_role=challenge_role,
        challenge_kind=challenge_kind,
        semantic_escalation_required=semantic_escalation_required,
        auto_continuation_eligible=auto_continuation_eligible,
        identified_risk=identified_risk,
        justification=justification,
        end_condition=end_condition,
        reasons=parsed_reasons,
        evidence_refs=parsed_evidence,
        cross_subplan_contract_change=cross_subplan_contract_change,
        accepted_frontier_anomaly=accepted_frontier_anomaly,
    )


def disposition_to_semantic_stop_reason(
    disposition: ReviewEscalationDisposition,
    task_ref: str = "S4/M1/W2",
) -> object | None:
    """Project disposition semantic escalation to existing SemanticStop taxonomy (compat mapping).

    Does not create new taxonomy; returns SemanticStopReason value if mapping exists, else None.
    """
    if not isinstance(disposition, ReviewEscalationDisposition):
        raise TypeError(f"disposition must be ReviewEscalationDisposition, got {type(disposition).__name__}")
    if not disposition.semantic_escalation_required:
        return None
    # Map identified_risk escalation triggers to SemanticStopReason
    ir = disposition.identified_risk
    if ir is None:
        return None
    val = ir.value if isinstance(ir, (ReviewTrigger, UserEscalationTrigger)) else str(ir)
    mapping: dict[str, str] = {
        UserEscalationTrigger.ARCHITECTURE_CHANGE.value: "UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED",
        UserEscalationTrigger.AUTHORITY_CHANGE.value: "AUTHORITY_CONFLICT",
        UserEscalationTrigger.REQUIREMENT_CONTRADICTION.value: "REQUIREMENT_AMBIGUOUS",
        UserEscalationTrigger.MATERIAL_PLAN_SCOPE_CHANGE.value: "SCOPE_AMBIGUOUS",
        UserEscalationTrigger.NEW_PRODUCT_REQUIREMENT.value: "REQUIREMENT_AMBIGUOUS",
        UserEscalationTrigger.NEW_MILESTONE_APPROVAL.value: "SCOPE_AMBIGUOUS",
        ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY.value: "AUTHORITY_CONFLICT",
        ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE.value: "UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED",
        ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE.value: "AUTHORITY_CONFLICT",
        ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY.value: "AUTHORITY_CONFLICT",
    }
    return mapping.get(val)


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
    # W2 additions
    "MAX_JUSTIFICATION_LENGTH",
    "MAX_END_CONDITION_LENGTH",
    "MAX_REASONS",
    "MAX_REASON_LENGTH",
    "DEFAULT_W_FORMAL_REVIEW",
    "RISK_TRIGGERED_W_REVIEW",
    "USER_ESCALATION_SEMANTIC_ONLY",
    "WORK_ITEM_USER_APPROVAL_DEFAULT",
    "USER_MILESTONE_APPROVAL_REQUIRED",
    "RISK_TRIGGERED_W_REVIEW_ENABLED",
    "EVERY_W_FORMAL_REVIEW",
    "HIGH_RISK_NO_SEMANTIC_CHOICE_AUTO_WITH_DEEP_VALIDATION",
    "SEMANTIC_CHOICE_REQUIRES_ESCALATION",
    "UNRESOLVED_RISK_UNCERTAINTY_FAILS_CLOSED",
    "CALLER_SELF_DOWNGRADE_BLOCKED_W2",
    "NO_SIXTH_WORK_ROLE",
    "AGENT_WORK_ROLE_COUNT",
    "CHALLENGE_ROUTING_IS_OPERATION_AUTHORITY",
    "REVIEW_DISPOSITION_IS_OPERATION_AUTHORITY",
    "CHALLENGE_ROUTING_IS_PLAN_AUTHORITY",
    "EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED",
    "NEW_ACCEPTANCE_ONTOLOGY_CREATED",
    "S1_STOP_ONTOLOGY_CHANGE_REQUIRED",
    "S1_STOP_ONTOLOGY_CHANGED",
    "S1_HANDOFF_SCHEMA_CHANGED",
    "S2_AUTHORITY_MODEL_CHANGED",
    "REVIEW_ESCALATION_DISPOSITION_IS_OPERATION_AUTHORITY",
    "SELECTED_PROCESS_DEPTH_IS_OPERATION_AUTHORITY",
    "ReviewTrigger",
    "REVIEW_TRIGGER_VALUES",
    "REVIEW_TRIGGER_FAIL_CLOSED",
    "UNKNOWN_REVIEW_TRIGGER_FAIL_CLOSED",
    "UserEscalationTrigger",
    "USER_ESCALATION_TRIGGER_VALUES",
    "USER_ESCALATION_FAIL_CLOSED",
    "UNKNOWN_ESCALATION_TRIGGER_FAIL_CLOSED",
    "ChallengeRole",
    "CHALLENGE_ROLE_VALUES",
    "CHALLENGE_ROLE_PROJECT_STEWARD_IS_NORMAL_CHALLENGER",
    "CHALLENGE_ROLE_IS_OPERATION_AUTHORITY",
    "ChallengeKind",
    "CHALLENGE_KIND_VALUES",
    "parse_review_trigger",
    "is_valid_review_trigger",
    "parse_user_escalation_trigger",
    "is_valid_user_escalation_trigger",
    "parse_challenge_role",
    "is_valid_challenge_role",
    "parse_challenge_kind",
    "ReviewEscalationDisposition",
    "evaluate_work_item_risk_policy",
    "disposition_to_semantic_stop_reason",
]
