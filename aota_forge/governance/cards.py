"""AF #57 M2/W1 — Governance Card contracts (derived projections only).

Five bounded, deterministic, non-authoritative Cards:

    PROJECT_CARD
    PLAN_CARD
    MILESTONE_CARD
    ARCHITECTURE_CARD
    PROGRESS_CARD

Cards are projections over existing truth owners (Project Governance Store,
``PlanAuthorityBinding`` + ``PortablePlanDocument``, accepted Architecture
facts, ``TaskMainCoordinatorState``, ``ExecutionStateStore``).  A Card owns no
state and is never authority:

    CARD_IS_AUTHORITY=no
    DIGEST_IS_AUTHORITY=no
    REF_IS_AUTHORITY=no
    GOVERNANCE_PROJECTION_DETERMINISTIC=yes
    PROGRESS_CARD_IS_AUTHORITY=no
    PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_STATE=no
    PROJECT_GOVERNANCE_STORE_OWNS_COORDINATOR_STATE=no
    READER_CARD_ONTOLOGY_IMPORTED=no
    READER_IMPLEMENTATION_IMPORTED=no

Card identity/digest exists for determinism, change detection, semantic reuse
and bounded provenance — not authorization.  Cards never contain raw host
paths, credentials, full source bodies, full historical evidence, or unbounded
results.  Completeness is explicit: a card is either ``complete`` or carries
the bounded missing-fact markers that made it incomplete; missing truth is
never fabricated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, ClassVar

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.plan.validation import is_milestone_id, is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE

CARD_IS_AUTHORITY = False
CARD_DIGEST_IS_AUTHORITY = False
CARD_REF_IS_AUTHORITY = False
GOVERNANCE_PROJECTION_DETERMINISTIC = True
PROGRESS_CARD_IS_AUTHORITY = False
PROGRESS_CARD_IS_DERIVED = True
SECOND_PROGRESS_STORE_CREATED = False
PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_STATE = False
PROJECT_GOVERNANCE_STORE_OWNS_COORDINATOR_STATE = False
PROJECT_GOVERNANCE_STATE_DUPLICATED = False
READER_CARD_ONTOLOGY_IMPORTED = False
READER_IMPLEMENTATION_IMPORTED = False

CARD_KIND_PROJECT = "project_card"
CARD_KIND_PLAN = "plan_card"
CARD_KIND_MILESTONE = "milestone_card"
CARD_KIND_ARCHITECTURE = "architecture_card"
CARD_KIND_PROGRESS = "progress_card"

CARD_KINDS = frozenset(
    {
        CARD_KIND_PROJECT,
        CARD_KIND_PLAN,
        CARD_KIND_MILESTONE,
        CARD_KIND_ARCHITECTURE,
        CARD_KIND_PROGRESS,
    }
)

MAX_CARD_REF_LENGTH = 256
MAX_CARD_TITLE_LENGTH = 512
MAX_CARD_SUMMARY_LENGTH = 512
MAX_CARD_OBJECTIVE_LENGTH = 1024
MAX_CARD_SOURCE_REFS = 16
MAX_CARD_NAVIGATION_REFS = 32
MAX_CARD_PLAN_REFS = 64
MAX_CARD_BLOCKERS = 32
MAX_CARD_WI_STATUS_ENTRIES = 64
MAX_CARD_EXECUTION_STATE_ENTRIES = 16

SOURCE_ROLE_PROJECT_GOVERNANCE_STORE = "project_governance_store"
SOURCE_ROLE_PLAN_AUTHORITY = "plan_authority"
SOURCE_ROLE_PLAN_DOCUMENT = "plan_document"
SOURCE_ROLE_ARCHITECTURE = "architecture"
SOURCE_ROLE_TASK_MAIN_COORDINATOR_STATE = "task_main_coordinator_state"
SOURCE_ROLE_EXECUTION_STATE_STORE = "execution_state_store"

NAVIGATION_KIND_PROJECT = "project"
NAVIGATION_KIND_PLAN = "plan"
NAVIGATION_KIND_MILESTONE = "milestone"
NAVIGATION_KIND_ARCHITECTURE = "architecture"
NAVIGATION_KIND_PROGRESS = "progress"

_NAVIGATION_KINDS = frozenset(
    {
        NAVIGATION_KIND_PROJECT,
        NAVIGATION_KIND_PLAN,
        NAVIGATION_KIND_MILESTONE,
        NAVIGATION_KIND_ARCHITECTURE,
        NAVIGATION_KIND_PROGRESS,
    }
)


class GovernanceCardError(ValueError):
    """A card contract is structurally invalid (fail closed)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _require_text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise GovernanceCardError("INVALID_FIELD", f"{label} must be a string")
    text = value.strip()
    if not text:
        raise GovernanceCardError("INVALID_FIELD", f"{label} must be a non-empty string")
    if len(text) > max_length:
        raise GovernanceCardError("INVALID_FIELD", f"{label} exceeds maximum {max_length}")
    if "\x00" in text:
        raise GovernanceCardError("INVALID_FIELD", f"{label} must not contain NUL")
    return text


def _optional_text(value: Any, label: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, label, max_length=max_length)


def _require_logical_ref(value: Any, label: str, *, max_length: int = MAX_CARD_REF_LENGTH) -> str:
    text = _require_text(value, label, max_length=max_length)
    if text.startswith("/") or text.startswith("~") or text.startswith("\\"):
        raise GovernanceCardError("HOST_PATH_REJECTED", f"{label} must be a logical ref, not a host path")
    if "\\" in text:
        raise GovernanceCardError("HOST_PATH_REJECTED", f"{label} must not contain Windows path separators")
    if len(text) > 2 and text[1] == ":" and text[2] in ("/", "\\"):
        raise GovernanceCardError("HOST_PATH_REJECTED", f"{label} must be a logical ref, not a drive path")
    return text


def require_logical_ref(value: Any, label: str, *, max_length: int = MAX_CARD_REF_LENGTH) -> str:
    """Public bounded logical-ref validator shared by the Governance layer.

    M2/W2: the Context Route and ephemeral Working Set use the same logical-ref
    contract as Cards (no host paths, no drive paths, bounded length).  This is
    the one shared validator — no second ref dialect is introduced.
    """
    return _require_logical_ref(value, label, max_length=max_length)


def _normalize_marker_tuple(value: Any, label: str, *, max_entries: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise GovernanceCardError("INVALID_FIELD", f"{label} must be a tuple/list of strings")
    items: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        text = _require_text(raw, f"{label}[{index}]", max_length=MAX_CARD_REF_LENGTH)
        if text not in seen:
            seen.add(text)
            items.append(text)
    if len(items) > max_entries:
        raise GovernanceCardError("INVALID_FIELD", f"{label} exceeds maximum {max_entries} entries")
    return tuple(sorted(items))


def require_marker_tuple(value: Any, label: str, *, max_entries: int) -> tuple[str, ...]:
    """Public bounded marker-tuple validator (same shape as card missing facts)."""
    return _normalize_marker_tuple(value, label, max_entries=max_entries)


@dataclass(frozen=True)
class CardSourceRef:
    """Bounded provenance ref: a stable logical ref + observed revision/digest."""

    role: str
    ref: str
    revision: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_text(self.role, "source_ref.role", max_length=64))
        object.__setattr__(self, "ref", _require_logical_ref(self.ref, "source_ref.ref"))
        if self.revision is not None:
            object.__setattr__(
                self,
                "revision",
                _require_text(self.revision, "source_ref.revision", max_length=128),
            )
        if self.digest is not None:
            digest = _require_text(self.digest, "source_ref.digest", max_length=64)
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise GovernanceCardError("INVALID_FIELD", "source_ref.digest must be 64 lowercase hex")
            object.__setattr__(self, "digest", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "ref": self.ref,
            "revision": self.revision,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class CardNavigationRef:
    """Bounded navigation ref to a governance object or card."""

    kind: str
    ref: str

    def __post_init__(self) -> None:
        if self.kind not in _NAVIGATION_KINDS:
            raise GovernanceCardError("INVALID_FIELD", f"nav kind must be one of {sorted(_NAVIGATION_KINDS)}")
        object.__setattr__(self, "ref", _require_logical_ref(self.ref, "navigation_ref.ref"))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref}


@dataclass(frozen=True)
class GovernanceCard:
    """Common Governance Card contract.

    Subclasses declare ``CARD_KIND`` and add only their bounded domain facts.
    ``complete`` is explicit: a card with missing required facts must carry the
    bounded missing-fact markers and can never claim completeness.
    """

    CARD_KIND: ClassVar[str] = ""

    project_id: str = ""
    plan_id: str | None = None
    milestone_id: str | None = None
    source_refs: tuple[CardSourceRef, ...] = ()
    navigation_refs: tuple[CardNavigationRef, ...] = ()
    complete: bool = False
    missing_facts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.CARD_KIND not in CARD_KINDS:
            raise GovernanceCardError("INVALID_CARD_KIND", f"card kind {self.CARD_KIND!r} is not a Governance Card kind")
        project_id = _require_text(self.project_id, "project_id", max_length=96)
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise GovernanceCardError("INVALID_PROJECT_ID", "project_id must match the canonical project identity grammar")
        object.__setattr__(self, "project_id", project_id)
        if self.plan_id is not None and not is_plan_id(self.plan_id):
            raise GovernanceCardError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        if self.milestone_id is not None and not is_milestone_id(self.milestone_id):
            raise GovernanceCardError("INVALID_MILESTONE_ID", "milestone_id must match M<digits>")
        if type(self.complete) is not bool:
            raise GovernanceCardError("INVALID_FIELD", "complete must be a bool")
        missing = _normalize_marker_tuple(self.missing_facts, "missing_facts", max_entries=MAX_CARD_SOURCE_REFS)
        warnings = _normalize_marker_tuple(self.warnings, "warnings", max_entries=MAX_CARD_SOURCE_REFS)
        if self.complete and missing:
            raise GovernanceCardError(
                "COMPLETENESS_CONTRADICTION",
                "a complete card must not carry missing-fact markers",
            )
        if not self.complete and not missing:
            raise GovernanceCardError(
                "COMPLETENESS_NOT_EXPLICIT",
                "an incomplete card must carry explicit bounded missing-fact markers",
            )
        object.__setattr__(self, "missing_facts", missing)
        object.__setattr__(self, "warnings", warnings)

        refs = self.source_refs
        if refs is None:
            refs = ()
        if not isinstance(refs, (tuple, list)) or len(refs) > MAX_CARD_SOURCE_REFS:
            raise GovernanceCardError("INVALID_FIELD", f"source_refs must be a bounded tuple (<= {MAX_CARD_SOURCE_REFS})")
        for ref in refs:
            if not isinstance(ref, CardSourceRef):
                raise GovernanceCardError("INVALID_FIELD", "source_refs entries must be CardSourceRef")
        object.__setattr__(self, "source_refs", tuple(refs))

        nav = self.navigation_refs
        if nav is None:
            nav = ()
        if not isinstance(nav, (tuple, list)) or len(nav) > MAX_CARD_NAVIGATION_REFS:
            raise GovernanceCardError(
                "INVALID_FIELD", f"navigation_refs must be a bounded tuple (<= {MAX_CARD_NAVIGATION_REFS})"
            )
        for ref in nav:
            if not isinstance(ref, CardNavigationRef):
                raise GovernanceCardError("INVALID_FIELD", "navigation_refs entries must be CardNavigationRef")
        object.__setattr__(self, "navigation_refs", tuple(nav))

    @property
    def card_kind(self) -> str:
        return self.CARD_KIND

    def is_authority(self) -> bool:
        """Cards are derived projections and never grant authority."""
        return False

    def _payload_fields(self) -> dict[str, Any]:
        return {}

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "card_kind": self.card_kind,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "milestone_id": self.milestone_id,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "navigation_refs": [ref.to_dict() for ref in self.navigation_refs],
            "complete": self.complete,
            "missing_facts": list(self.missing_facts),
            "warnings": list(self.warnings),
        }
        payload.update(self._payload_fields())
        return payload

    def projection_id(self) -> str:
        """Deterministic projection identity over the canonical card payload."""
        digest = hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()
        return f"gcard-{digest[:32]}"

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["projection_id"] = self.projection_id()
        payload["is_authority"] = False
        return payload


@dataclass(frozen=True)
class ProjectCard(GovernanceCard):
    """Project-level governance projection (trusted identity + Plan inventory)."""

    CARD_KIND: ClassVar[str] = CARD_KIND_PROJECT

    project_ref: str | None = None
    project_status: str | None = None
    plan_refs: tuple[str, ...] = ()
    plan_count: int = 0
    governance_root_refs: tuple[str, ...] = ()
    source_revision: str | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "project_ref", _optional_text(self.project_ref, "project_ref", max_length=MAX_CARD_REF_LENGTH))
        object.__setattr__(self, "project_status", _optional_text(self.project_status, "project_status", max_length=64))
        plan_refs = self.plan_refs or ()
        if not isinstance(plan_refs, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "plan_refs must be a tuple/list")
        for plan_ref in plan_refs:
            if not is_plan_id(plan_ref):
                raise GovernanceCardError("INVALID_PLAN_ID", f"plan_refs entry {plan_ref!r} is not a canonical Plan ID")
        if len(plan_refs) > MAX_CARD_PLAN_REFS:
            plan_refs = tuple(sorted(plan_refs)[:MAX_CARD_PLAN_REFS])
        object.__setattr__(self, "plan_refs", tuple(sorted(plan_refs)))
        if type(self.plan_count) is not int or self.plan_count < 0:
            raise GovernanceCardError("INVALID_FIELD", "plan_count must be a non-negative int")
        roots = self.governance_root_refs or ()
        if not isinstance(roots, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "governance_root_refs must be a tuple/list")
        for root_ref in roots:
            _require_logical_ref(root_ref, "governance_root_ref", max_length=64)
        object.__setattr__(self, "governance_root_refs", tuple(roots))
        object.__setattr__(self, "source_revision", _optional_text(self.source_revision, "source_revision", max_length=128))
        object.__setattr__(self, "source_digest", _optional_text(self.source_digest, "source_digest", max_length=64))

    def _payload_fields(self) -> dict[str, Any]:
        return {
            "project_ref": self.project_ref,
            "project_status": self.project_status,
            "plan_refs": list(self.plan_refs),
            "plan_count": self.plan_count,
            "governance_root_refs": list(self.governance_root_refs),
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class PlanCard(GovernanceCard):
    """Plan-level governance projection (authority + lifecycle + Plan semantics)."""

    CARD_KIND: ClassVar[str] = CARD_KIND_PLAN

    authority_source_kind: str | None = None
    authority_ref: str | None = None
    authority_revision: str | None = None
    authority_digest: str | None = None
    lifecycle_state: str | None = None
    record_revision: int | None = None
    plan_status: str | None = None
    current_milestone: str | None = None
    title: str | None = None
    goal_summary: str | None = None
    source_revision: str | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(
            self, "authority_source_kind", _optional_text(self.authority_source_kind, "authority_source_kind", max_length=64)
        )
        if self.authority_ref is not None:
            object.__setattr__(self, "authority_ref", _require_logical_ref(self.authority_ref, "authority_ref", max_length=512))
        object.__setattr__(self, "authority_revision", _optional_text(self.authority_revision, "authority_revision", max_length=128))
        if self.authority_digest is not None:
            digest = _require_text(self.authority_digest, "authority_digest", max_length=64)
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise GovernanceCardError("INVALID_FIELD", "authority_digest must be 64 lowercase hex")
        object.__setattr__(self, "lifecycle_state", _optional_text(self.lifecycle_state, "lifecycle_state", max_length=64))
        if self.record_revision is not None and (type(self.record_revision) is not int or self.record_revision < 1):
            raise GovernanceCardError("INVALID_FIELD", "record_revision must be a positive int")
        object.__setattr__(self, "plan_status", _optional_text(self.plan_status, "plan_status", max_length=64))
        if self.current_milestone is not None and not is_milestone_id(self.current_milestone):
            raise GovernanceCardError("INVALID_MILESTONE_ID", "current_milestone must match M<digits>")
        object.__setattr__(self, "title", _optional_text(self.title, "title", max_length=MAX_CARD_TITLE_LENGTH))
        object.__setattr__(self, "goal_summary", _optional_text(self.goal_summary, "goal_summary", max_length=MAX_CARD_SUMMARY_LENGTH))
        object.__setattr__(self, "source_revision", _optional_text(self.source_revision, "source_revision", max_length=128))
        object.__setattr__(self, "source_digest", _optional_text(self.source_digest, "source_digest", max_length=64))

    def _payload_fields(self) -> dict[str, Any]:
        return {
            "authority_source_kind": self.authority_source_kind,
            "authority_ref": self.authority_ref,
            "authority_revision": self.authority_revision,
            "authority_digest": self.authority_digest,
            "lifecycle_state": self.lifecycle_state,
            "record_revision": self.record_revision,
            "plan_status": self.plan_status,
            "current_milestone": self.current_milestone,
            "title": self.title,
            "goal_summary": self.goal_summary,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class MilestoneCard(GovernanceCard):
    """Milestone projection over canonical Plan semantics only.

    Runtime work progression stays in the coordinator/execution truth owners;
    it is aggregated by the Progress Card, never copied into this card.
    """

    CARD_KIND: ClassVar[str] = CARD_KIND_MILESTONE

    milestone_title: str | None = None
    milestone_status: str | None = None
    objective_summary: str | None = None
    objective_ref: str | None = None
    work_item_refs: tuple[str, ...] = ()
    dependency_edges: tuple[tuple[str, str], ...] = ()
    approval_satisfied: bool | None = None
    is_current: bool = False
    source_revision: str | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.milestone_id is None:
            raise GovernanceCardError("INVALID_FIELD", "milestone card requires a milestone_id")
        object.__setattr__(
            self, "milestone_title", _optional_text(self.milestone_title, "milestone_title", max_length=MAX_CARD_TITLE_LENGTH)
        )
        object.__setattr__(
            self, "milestone_status", _optional_text(self.milestone_status, "milestone_status", max_length=64)
        )
        object.__setattr__(
            self,
            "objective_summary",
            _optional_text(self.objective_summary, "objective_summary", max_length=MAX_CARD_OBJECTIVE_LENGTH),
        )
        object.__setattr__(self, "objective_ref", _optional_text(self.objective_ref, "objective_ref", max_length=MAX_CARD_REF_LENGTH))
        work_items = self.work_item_refs or ()
        if not isinstance(work_items, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "work_item_refs must be a tuple/list")
        for item in work_items:
            _require_text(item, "work_item_refs entry", max_length=64)
        object.__setattr__(self, "work_item_refs", tuple(work_items))
        edges = self.dependency_edges or ()
        if not isinstance(edges, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "dependency_edges must be a tuple/list")
        clean_edges: list[tuple[str, str]] = []
        for edge in edges:
            if not isinstance(edge, (tuple, list)) or len(edge) != 2:
                raise GovernanceCardError("INVALID_FIELD", "dependency_edges entries must be [from, to] pairs")
            clean_edges.append(
                (
                    _require_text(edge[0], "dependency edge source", max_length=64),
                    _require_text(edge[1], "dependency edge target", max_length=64),
                )
            )
        object.__setattr__(self, "dependency_edges", tuple(sorted(clean_edges)))
        if self.approval_satisfied is not None and type(self.approval_satisfied) is not bool:
            raise GovernanceCardError("INVALID_FIELD", "approval_satisfied must be bool or None")
        if type(self.is_current) is not bool:
            raise GovernanceCardError("INVALID_FIELD", "is_current must be a bool")
        object.__setattr__(self, "source_revision", _optional_text(self.source_revision, "source_revision", max_length=128))
        object.__setattr__(self, "source_digest", _optional_text(self.source_digest, "source_digest", max_length=64))

    def _payload_fields(self) -> dict[str, Any]:
        return {
            "milestone_title": self.milestone_title,
            "milestone_status": self.milestone_status,
            "objective_summary": self.objective_summary,
            "objective_ref": self.objective_ref,
            "work_item_refs": list(self.work_item_refs),
            "dependency_edges": [list(edge) for edge in self.dependency_edges],
            "approval_satisfied": self.approval_satisfied,
            "is_current": self.is_current,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class ArchitectureCard(GovernanceCard):
    """Accepted Architecture baseline/delta projection.

    Grounded strictly in accepted governance truth (an accepted Plan's frozen
    baseline facts and its authority ref).  No architecture history storage is
    invented; unavailable architecture truth yields an explicit incomplete card.
    """

    CARD_KIND: ClassVar[str] = CARD_KIND_ARCHITECTURE

    baseline_id: str | None = None
    baseline_status: str | None = None
    accepted_ref: str | None = None
    plan_delta_ref: str | None = None
    source_revision: str | None = None
    source_digest: str | None = None
    current_version: str | None = None
    current_digest: str | None = None
    promotion_receipt_ref: str | None = None
    promoted_by_plan_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "baseline_id", _optional_text(self.baseline_id, "baseline_id", max_length=128))
        object.__setattr__(self, "baseline_status", _optional_text(self.baseline_status, "baseline_status", max_length=64))
        if self.accepted_ref is not None:
            object.__setattr__(self, "accepted_ref", _require_logical_ref(self.accepted_ref, "accepted_ref", max_length=512))
        if self.plan_delta_ref is not None:
            object.__setattr__(self, "plan_delta_ref", _require_logical_ref(self.plan_delta_ref, "plan_delta_ref", max_length=512))
        object.__setattr__(self, "source_revision", _optional_text(self.source_revision, "source_revision", max_length=128))
        for label in ("source_digest", "current_digest"):
            value = getattr(self, label)
            if value is not None:
                digest = _require_text(value, label, max_length=64)
                if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                    raise GovernanceCardError("INVALID_FIELD", f"{label} must be 64 lowercase hex")
                object.__setattr__(self, label, digest)
        object.__setattr__(self, "current_version", _optional_text(self.current_version, "current_version", max_length=256))
        if self.promotion_receipt_ref is not None:
            object.__setattr__(
                self,
                "promotion_receipt_ref",
                _require_logical_ref(self.promotion_receipt_ref, "promotion_receipt_ref", max_length=512),
            )
        if self.promoted_by_plan_id is not None and not is_plan_id(self.promoted_by_plan_id):
            raise GovernanceCardError("INVALID_PLAN_ID", "promoted_by_plan_id must be a canonical Plan ID")

    def _payload_fields(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "baseline_status": self.baseline_status,
            "accepted_ref": self.accepted_ref,
            "plan_delta_ref": self.plan_delta_ref,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
            "current_version": self.current_version,
            "current_digest": self.current_digest,
            "promotion_receipt_ref": self.promotion_receipt_ref,
            "promoted_by_plan_id": self.promoted_by_plan_id,
        }


@dataclass(frozen=True)
class PlanLifecycleFact:
    plan_id: str
    lifecycle_state: str
    authority_source_kind: str | None = None

    def __post_init__(self) -> None:
        if not is_plan_id(self.plan_id):
            raise GovernanceCardError("INVALID_PLAN_ID", "plan lifecycle fact requires a canonical Plan ID")
        object.__setattr__(self, "lifecycle_state", _require_text(self.lifecycle_state, "lifecycle_state", max_length=64))
        object.__setattr__(
            self,
            "authority_source_kind",
            _optional_text(self.authority_source_kind, "authority_source_kind", max_length=64),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "lifecycle_state": self.lifecycle_state,
            "authority_source_kind": self.authority_source_kind,
        }


@dataclass(frozen=True)
class CoordinatorProgressFact:
    """Bounded projection of one TaskMainCoordinatorState (never a copy)."""

    coordinator_id: str
    plan_authority: str
    milestone_id: str
    status: str
    work_item_count: int
    wi_status_counts: tuple[tuple[str, int], ...] = ()
    progression_revision: int = 0
    human_brake_active: bool = False
    open_blockers: tuple[str, ...] = ()
    next_action: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinator_id", _require_text(self.coordinator_id, "coordinator_id", max_length=128))
        object.__setattr__(self, "plan_authority", _require_text(self.plan_authority, "plan_authority", max_length=512))
        object.__setattr__(self, "milestone_id", _require_text(self.milestone_id, "milestone_id", max_length=64))
        object.__setattr__(self, "status", _require_text(self.status, "status", max_length=64))
        if type(self.work_item_count) is not int or self.work_item_count < 0:
            raise GovernanceCardError("INVALID_FIELD", "work_item_count must be a non-negative int")
        counts = self.wi_status_counts or ()
        if not isinstance(counts, (tuple, list)) or len(counts) > MAX_CARD_WI_STATUS_ENTRIES:
            raise GovernanceCardError("INVALID_FIELD", "wi_status_counts must be a bounded tuple")
        clean_counts: list[tuple[str, int]] = []
        for entry in counts:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                raise GovernanceCardError("INVALID_FIELD", "wi_status_counts entries must be [status, count] pairs")
            status = _require_text(entry[0], "wi_status_counts status", max_length=64)
            if type(entry[1]) is not int or entry[1] < 0:
                raise GovernanceCardError("INVALID_FIELD", "wi_status_counts count must be a non-negative int")
            clean_counts.append((status, entry[1]))
        object.__setattr__(self, "wi_status_counts", tuple(sorted(clean_counts)))
        if type(self.progression_revision) is not int or self.progression_revision < 0:
            raise GovernanceCardError("INVALID_FIELD", "progression_revision must be a non-negative int")
        if type(self.human_brake_active) is not bool:
            raise GovernanceCardError("INVALID_FIELD", "human_brake_active must be a bool")
        blockers = _normalize_marker_tuple(self.open_blockers, "open_blockers", max_entries=MAX_CARD_BLOCKERS)
        object.__setattr__(self, "open_blockers", blockers)
        object.__setattr__(self, "next_action", _optional_text(self.next_action, "next_action", max_length=MAX_CARD_SUMMARY_LENGTH))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "plan_authority": self.plan_authority,
            "milestone_id": self.milestone_id,
            "status": self.status,
            "work_item_count": self.work_item_count,
            "wi_status_counts": [list(entry) for entry in self.wi_status_counts],
            "progression_revision": self.progression_revision,
            "human_brake_active": self.human_brake_active,
            "open_blockers": list(self.open_blockers),
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class ExecutionAggregateFact:
    """Bounded aggregate over DurableExecutionRecord facts (no new store)."""

    total: int
    by_canonical_task_state: tuple[tuple[str, int], ...] = ()
    by_delivery_state: tuple[tuple[str, int], ...] = ()
    terminal_ok: int = 0
    terminal_failed: int = 0
    requiring_recovery: int = 0

    def __post_init__(self) -> None:
        for label, value in (
            ("total", self.total),
            ("terminal_ok", self.terminal_ok),
            ("terminal_failed", self.terminal_failed),
            ("requiring_recovery", self.requiring_recovery),
        ):
            if type(value) is not int or value < 0:
                raise GovernanceCardError("INVALID_FIELD", f"{label} must be a non-negative int")
        for label, entries in (
            ("by_canonical_task_state", self.by_canonical_task_state),
            ("by_delivery_state", self.by_delivery_state),
        ):
            values = entries or ()
            if not isinstance(values, (tuple, list)) or len(values) > MAX_CARD_EXECUTION_STATE_ENTRIES:
                raise GovernanceCardError("INVALID_FIELD", f"{label} must be a bounded tuple")
            for entry in values:
                if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                    raise GovernanceCardError("INVALID_FIELD", f"{label} entries must be [state, count] pairs")
                _require_text(entry[0], f"{label} state", max_length=64)
                if type(entry[1]) is not int or entry[1] < 0:
                    raise GovernanceCardError("INVALID_FIELD", f"{label} count must be a non-negative int")
        object.__setattr__(self, "by_canonical_task_state", tuple(sorted(tuple(e) for e in (self.by_canonical_task_state or ()))))
        object.__setattr__(self, "by_delivery_state", tuple(sorted(tuple(e) for e in (self.by_delivery_state or ()))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_canonical_task_state": [list(entry) for entry in self.by_canonical_task_state],
            "by_delivery_state": [list(entry) for entry in self.by_delivery_state],
            "terminal_ok": self.terminal_ok,
            "terminal_failed": self.terminal_failed,
            "requiring_recovery": self.requiring_recovery,
        }


@dataclass(frozen=True)
class ProgressCard(GovernanceCard):
    """Derived aggregate progress projection.

    Consumes bounded snapshots from the Project Governance Store,
    TaskMainCoordinatorState and ExecutionStateStore.  It stores none of them
    and is never progress authority.
    """

    CARD_KIND: ClassVar[str] = CARD_KIND_PROGRESS

    plan_lifecycle: tuple[PlanLifecycleFact, ...] = ()
    coordinator_facts: tuple[CoordinatorProgressFact, ...] = ()
    execution_aggregate: ExecutionAggregateFact | None = None
    active_coordinator_count: int = 0
    open_blockers: tuple[str, ...] = ()
    human_brake_active: bool | None = None
    next_action: str | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        facts = self.plan_lifecycle or ()
        if not isinstance(facts, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "plan_lifecycle must be a tuple/list")
        for fact in facts:
            if not isinstance(fact, PlanLifecycleFact):
                raise GovernanceCardError("INVALID_FIELD", "plan_lifecycle entries must be PlanLifecycleFact")
        object.__setattr__(self, "plan_lifecycle", tuple(sorted(facts, key=lambda f: f.plan_id)))
        coordinators = self.coordinator_facts or ()
        if not isinstance(coordinators, (tuple, list)):
            raise GovernanceCardError("INVALID_FIELD", "coordinator_facts must be a tuple/list")
        for fact in coordinators:
            if not isinstance(fact, CoordinatorProgressFact):
                raise GovernanceCardError("INVALID_FIELD", "coordinator_facts entries must be CoordinatorProgressFact")
        object.__setattr__(self, "coordinator_facts", tuple(sorted(coordinators, key=lambda f: f.coordinator_id)))
        if self.execution_aggregate is not None and not isinstance(self.execution_aggregate, ExecutionAggregateFact):
            raise GovernanceCardError("INVALID_FIELD", "execution_aggregate must be ExecutionAggregateFact or None")
        if type(self.active_coordinator_count) is not int or self.active_coordinator_count < 0:
            raise GovernanceCardError("INVALID_FIELD", "active_coordinator_count must be a non-negative int")
        blockers = _normalize_marker_tuple(self.open_blockers, "open_blockers", max_entries=MAX_CARD_BLOCKERS)
        object.__setattr__(self, "open_blockers", blockers)
        if self.human_brake_active is not None and type(self.human_brake_active) is not bool:
            raise GovernanceCardError("INVALID_FIELD", "human_brake_active must be bool or None")
        object.__setattr__(self, "next_action", _optional_text(self.next_action, "next_action", max_length=MAX_CARD_SUMMARY_LENGTH))
        object.__setattr__(self, "source_digest", _optional_text(self.source_digest, "source_digest", max_length=64))

    def _payload_fields(self) -> dict[str, Any]:
        return {
            "plan_lifecycle": [fact.to_dict() for fact in self.plan_lifecycle],
            "coordinator_facts": [fact.to_dict() for fact in self.coordinator_facts],
            "execution_aggregate": self.execution_aggregate.to_dict() if self.execution_aggregate else None,
            "active_coordinator_count": self.active_coordinator_count,
            "open_blockers": list(self.open_blockers),
            "human_brake_active": self.human_brake_active,
            "next_action": self.next_action,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class GovernanceProjectionBundle:
    """The deterministic card set for one project projection rebuild."""

    project: ProjectCard
    plans: tuple[PlanCard, ...] = ()
    milestones: tuple[MilestoneCard, ...] = ()
    architecture: ArchitectureCard | None = None
    progress: ProgressCard | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project, ProjectCard):
            raise GovernanceCardError("INVALID_FIELD", "bundle.project must be a ProjectCard")
        plans = tuple(self.plans or ())
        for card in plans:
            if not isinstance(card, PlanCard):
                raise GovernanceCardError("INVALID_FIELD", "bundle.plans entries must be PlanCard")
        object.__setattr__(self, "plans", tuple(sorted(plans, key=lambda c: c.plan_id or "")))
        milestones = tuple(self.milestones or ())
        for card in milestones:
            if not isinstance(card, MilestoneCard):
                raise GovernanceCardError("INVALID_FIELD", "bundle.milestones entries must be MilestoneCard")
        object.__setattr__(
            self,
            "milestones",
            tuple(sorted(milestones, key=lambda c: (c.plan_id or "", c.milestone_id or ""))),
        )
        if self.architecture is not None and not isinstance(self.architecture, ArchitectureCard):
            raise GovernanceCardError("INVALID_FIELD", "bundle.architecture must be ArchitectureCard or None")
        if self.progress is not None and not isinstance(self.progress, ProgressCard):
            raise GovernanceCardError("INVALID_FIELD", "bundle.progress must be ProgressCard or None")

    def cards(self) -> tuple[GovernanceCard, ...]:
        items: list[GovernanceCard] = [self.project, *self.plans, *self.milestones]
        if self.architecture is not None:
            items.append(self.architecture)
        if self.progress is not None:
            items.append(self.progress)
        return tuple(items)

    def projection_digest(self) -> str:
        payload = [card.projection_id() for card in self.cards()]
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "plans": [card.to_dict() for card in self.plans],
            "milestones": [card.to_dict() for card in self.milestones],
            "architecture": self.architecture.to_dict() if self.architecture else None,
            "progress": self.progress.to_dict() if self.progress else None,
            "projection_digest": self.projection_digest(),
        }


__all__ = [
    "CARD_DIGEST_IS_AUTHORITY",
    "CARD_IS_AUTHORITY",
    "CARD_KINDS",
    "CARD_KIND_ARCHITECTURE",
    "CARD_KIND_MILESTONE",
    "CARD_KIND_PLAN",
    "CARD_KIND_PROGRESS",
    "CARD_KIND_PROJECT",
    "CARD_REF_IS_AUTHORITY",
    "GOVERNANCE_PROJECTION_DETERMINISTIC",
    "PROGRESS_CARD_IS_AUTHORITY",
    "PROGRESS_CARD_IS_DERIVED",
    "SECOND_PROGRESS_STORE_CREATED",
    "PROJECT_GOVERNANCE_STATE_DUPLICATED",
    "PROJECT_GOVERNANCE_STORE_OWNS_COORDINATOR_STATE",
    "PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_STATE",
    "READER_CARD_ONTOLOGY_IMPORTED",
    "READER_IMPLEMENTATION_IMPORTED",
    "ArchitectureCard",
    "CardNavigationRef",
    "CardSourceRef",
    "CoordinatorProgressFact",
    "ExecutionAggregateFact",
    "GovernanceCard",
    "GovernanceCardError",
    "GovernanceProjectionBundle",
    "MilestoneCard",
    "PlanCard",
    "PlanLifecycleFact",
    "ProgressCard",
    "ProjectCard",
    "require_logical_ref",
    "require_marker_tuple",
]
