"""AF #57 M2/W1 — narrow, domain-specific Governance Projection Engine.

    governance truth adapters/readers
            v
    narrow Governance Projection Engine
            v
    Governance Cards
            +-- MAP
            +-- STATUS

This engine is deliberately not a generic projection framework.  It has no
plugin registry, no projection DSL, no event bus, no persistent projection
database, no filesystem watcher, and no LLM retrieval-strategy decisions.
It consumes bounded snapshots from existing truth owners and returns
deterministic derived Cards:

    Project Governance Store   -> project / Plan lifecycle + authority
    PortablePlanDocument       -> Plan / Milestone semantics
    accepted Architecture facts-> accepted baseline reference only
    TaskMainCoordinatorState   -> task-main working/progression truth
    ExecutionStateStore        -> execution durability facts

The existing canonical-graph ``ProjectionRebuildService``
(``aota_forge.core.projection``) owns Subject projection and is not touched,
imported, or repurposed here.

Gate facts:

    SECOND_GENERIC_PROJECTION_FRAMEWORK=no
    GENERIC_WATCHER_CREATED=no
    EVENT_BUS_CREATED=no
    PROJECTION_DATABASE_CREATED=no
    W1_DECIDES_LLM_RETRIEVAL_STRATEGY=no
    CONSUMER_REQUIRED_BEFORE_TRIGGER_WIRING=yes
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import DeliveryState, DurableExecutionRecord
from aota_forge.core.plan.read_model import PortablePlanDocument
from aota_forge.core.plan.validation import is_milestone_id, is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.cards import (
    CARD_KIND_ARCHITECTURE,
    CARD_KIND_MILESTONE,
    CARD_KIND_PLAN,
    CARD_KIND_PROGRESS,
    CARD_KIND_PROJECT,
    CARD_KINDS,
    MAX_CARD_NAVIGATION_REFS,
    NAVIGATION_KIND_ARCHITECTURE,
    NAVIGATION_KIND_MILESTONE,
    NAVIGATION_KIND_PLAN,
    NAVIGATION_KIND_PROGRESS,
    NAVIGATION_KIND_PROJECT,
    SOURCE_ROLE_ARCHITECTURE,
    SOURCE_ROLE_EXECUTION_STATE_STORE,
    SOURCE_ROLE_PLAN_AUTHORITY,
    SOURCE_ROLE_PLAN_DOCUMENT,
    SOURCE_ROLE_PROJECT_GOVERNANCE_STORE,
    SOURCE_ROLE_TASK_MAIN_COORDINATOR_STATE,
    ArchitectureCard,
    CardNavigationRef,
    CardSourceRef,
    CoordinatorProgressFact,
    ExecutionAggregateFact,
    GovernanceCardError,
    GovernanceProjectionBundle,
    MilestoneCard,
    PlanCard,
    PlanLifecycleFact,
    ProgressCard,
    ProjectCard,
)
from aota_forge.governance.project_store import ProjectPlanRecord
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState

SECOND_GENERIC_PROJECTION_FRAMEWORK = False
GENERIC_WATCHER_CREATED = False
EVENT_BUS_CREATED = False
PROJECTION_DATABASE_CREATED = False
CONSUMER_REQUIRED_BEFORE_TRIGGER_WIRING = True
W1_DECIDES_LLM_RETRIEVAL_STRATEGY = False
OLD_GRAPH_PROJECTION_OWNERSHIP_CHANGED = False
NEW_TELEMETRY_SYSTEM = False
PROGRESS_AGGREGATION_IS_BOUNDED = True

REFRESH_TRIGGER_PLAN_MUTATION = "plan_mutation"
REFRESH_TRIGGER_MILESTONE_TRANSITION = "milestone_transition"
REFRESH_TRIGGER_WORK_RECONCILIATION = "work_reconciliation"
REFRESH_TRIGGER_ARCHITECTURE_PROMOTION = "architecture_promotion"
REFRESH_TRIGGER_DEFECT_FACT_DECISION_MUTATION = "defect_fact_decision_mutation"
REFRESH_TRIGGER_PROJECT_ROOT_BINDING_CHANGE = "project_root_binding_change"

REFRESH_TRIGGERS = frozenset(
    {
        REFRESH_TRIGGER_PLAN_MUTATION,
        REFRESH_TRIGGER_MILESTONE_TRANSITION,
        REFRESH_TRIGGER_WORK_RECONCILIATION,
        REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
        REFRESH_TRIGGER_DEFECT_FACT_DECISION_MUTATION,
        REFRESH_TRIGGER_PROJECT_ROOT_BINDING_CHANGE,
    }
)

ARCHITECTURE_BASELINE_ID_KEYS = ("FROZEN_BASELINE_ID", "ARCHITECTURE_BASELINE_ID")
ARCHITECTURE_BASELINE_STATUS_KEYS = ("FROZEN_BASELINE_STATUS", "ARCHITECTURE_BASELINE_STATUS")
ARCHITECTURE_DELTA_REF_KEYS = ("ARCHITECTURE_DELTA_REF", "PLAN_ARCHITECTURE_DELTA_REF")
GOAL_SUMMARY_KEYS = ("PRIMARY_GOAL", "PLAN_TITLE", "GOAL")

MAX_TITLE_LENGTH = 512
MAX_GOAL_SUMMARY_LENGTH = 512
MAX_OBJECTIVE_LENGTH = 1024
MAX_ARCHITECTURE_FIELD_LENGTH = 128
MAX_BLOCKERS = 32
MAX_EXECUTION_STATE_ENTRIES = 16
EXECUTION_TERMINAL_DELIVERY_STATES = frozenset({DeliveryState.ACKNOWLEDGED, DeliveryState.DROPPED})

ALL_CARD_KINDS = CARD_KINDS


class GovernanceProjectionError(ValueError):
    """A governance projection input cannot be grounded (fail closed)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clean_text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise GovernanceProjectionError("INVALID_INPUT", f"{label} must be a string")
    text = value.strip()
    if not text:
        raise GovernanceProjectionError("INVALID_INPUT", f"{label} must be non-empty")
    if len(text) > max_length:
        raise GovernanceProjectionError("INVALID_INPUT", f"{label} exceeds maximum {max_length}")
    if "\x00" in text:
        raise GovernanceProjectionError("INVALID_INPUT", f"{label} must not contain NUL")
    return text


def _clean_optional(value: Any, label: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _clean_text(value, label, max_length=max_length)


def _clean_logical_ref(value: Any, label: str, *, max_length: int = 512) -> str:
    text = _clean_text(value, label, max_length=max_length)
    if text.startswith("/") or text.startswith("~") or text.startswith("\\"):
        raise GovernanceProjectionError("HOST_PATH_REJECTED", f"{label} must be a logical ref, not a host path")
    if "\\" in text:
        raise GovernanceProjectionError("HOST_PATH_REJECTED", f"{label} must not contain Windows path separators")
    if len(text) > 2 and text[1] == ":" and text[2] in ("/", "\\"):
        raise GovernanceProjectionError("HOST_PATH_REJECTED", f"{label} must be a logical ref, not a drive path")
    return text


def _clean_project_id(value: Any) -> str:
    project_id = _clean_text(value, "project_id", max_length=96)
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise GovernanceProjectionError("INVALID_PROJECT_ID", "project_id must match the canonical project identity grammar")
    return project_id


@dataclass(frozen=True)
class ArchitectureStateInput:
    """Storage-neutral accepted-architecture snapshot.

    Grounded in accepted governance truth only.  No architecture history
    storage is introduced; absent facts stay ``None`` so the Architecture Card
    can report explicit incompleteness.
    """

    project_id: str
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
        object.__setattr__(self, "project_id", _clean_project_id(self.project_id))
        object.__setattr__(
            self, "baseline_id", _clean_optional(self.baseline_id, "baseline_id", max_length=MAX_ARCHITECTURE_FIELD_LENGTH)
        )
        object.__setattr__(
            self,
            "baseline_status",
            _clean_optional(self.baseline_status, "baseline_status", max_length=MAX_ARCHITECTURE_FIELD_LENGTH),
        )
        if self.accepted_ref is not None:
            object.__setattr__(self, "accepted_ref", _clean_logical_ref(self.accepted_ref, "accepted_ref"))
        if self.plan_delta_ref is not None:
            object.__setattr__(self, "plan_delta_ref", _clean_logical_ref(self.plan_delta_ref, "plan_delta_ref"))
        object.__setattr__(
            self, "source_revision", _clean_optional(self.source_revision, "source_revision", max_length=128)
        )
        if self.source_digest is not None:
            digest = _clean_text(self.source_digest, "source_digest", max_length=64)
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise GovernanceProjectionError("INVALID_INPUT", "source_digest must be 64 lowercase hex")
            object.__setattr__(self, "source_digest", digest)
        object.__setattr__(
            self,
            "current_version",
            _clean_optional(self.current_version, "current_version", max_length=256),
        )
        if self.current_digest is not None:
            digest = _clean_text(self.current_digest, "current_digest", max_length=64)
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise GovernanceProjectionError("INVALID_INPUT", "current_digest must be 64 lowercase hex")
            object.__setattr__(self, "current_digest", digest)
        if self.promotion_receipt_ref is not None:
            object.__setattr__(
                self,
                "promotion_receipt_ref",
                _clean_logical_ref(self.promotion_receipt_ref, "promotion_receipt_ref"),
            )
        if self.promoted_by_plan_id is not None and not is_plan_id(self.promoted_by_plan_id):
            raise GovernanceProjectionError(
                "INVALID_PLAN_ID", "promoted_by_plan_id must be one canonical internal Plan ID"
            )


@dataclass(frozen=True)
class PlanDocumentInput:
    """A normalized accepted Plan document paired with optional read facts.

    ``current_milestone`` is an authoritative read input (the trusted runtime
    or governance reader may know the current Milestone even when the Plan
    body's current-section projection is not machine-classified); it is never
    inferred from folder names, branches, or titles.
    """

    plan_id: str
    document: PortablePlanDocument
    title: str | None = None
    current_milestone: str | None = None
    authority: PlanAuthorityBinding | None = None

    def __post_init__(self) -> None:
        if not is_plan_id(self.plan_id):
            raise GovernanceProjectionError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        if not isinstance(self.document, PortablePlanDocument):
            raise GovernanceProjectionError("INVALID_INPUT", "document must be a PortablePlanDocument")
        object.__setattr__(self, "title", _clean_optional(self.title, "title", max_length=MAX_TITLE_LENGTH))
        if self.current_milestone is not None and not is_milestone_id(self.current_milestone):
            raise GovernanceProjectionError("INVALID_MILESTONE_ID", "current_milestone must match M<digits>")
        if self.authority is not None and not isinstance(self.authority, PlanAuthorityBinding):
            raise GovernanceProjectionError("INVALID_INPUT", "authority must be a PlanAuthorityBinding or None")


@dataclass(frozen=True)
class GovernanceProjectionInput:
    """Storage-neutral projection snapshot for one project.

    ``plan_records`` / ``coordinator_states`` / ``execution_records`` use
    ``None`` to mean "the truth source was not read" (explicit missing-fact
    marker) versus an empty tuple meaning "read; empty truth".
    """

    project_id: str
    project_ref: str | None = None
    project_status: str | None = None
    governance_root_refs: tuple[str, ...] = ()
    plan_records: tuple[ProjectPlanRecord, ...] | None = ()
    plan_documents: tuple[PlanDocumentInput, ...] = ()
    architecture: ArchitectureStateInput | None = None
    coordinator_states: tuple[TaskMainCoordinatorState, ...] | None = ()
    execution_records: tuple[DurableExecutionRecord, ...] | None = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _clean_project_id(self.project_id))
        if self.project_ref is not None:
            object.__setattr__(self, "project_ref", _clean_logical_ref(self.project_ref, "project_ref"))
        object.__setattr__(self, "project_status", _clean_optional(self.project_status, "project_status", max_length=64))
        roots = tuple(self.governance_root_refs or ())
        for root_ref in roots:
            _clean_logical_ref(root_ref, "governance_root_ref", max_length=64)
        object.__setattr__(self, "governance_root_refs", roots)

        if self.plan_records is not None:
            records = tuple(self.plan_records)
            for record in records:
                if not isinstance(record, ProjectPlanRecord):
                    raise GovernanceProjectionError("INVALID_INPUT", "plan_records entries must be ProjectPlanRecord")
            plan_ids = [record.plan_id for record in records]
            if len(plan_ids) != len(set(plan_ids)):
                raise GovernanceProjectionError("DUPLICATE_PLAN_RECORD", "plan_records contains duplicate plan ids")
            object.__setattr__(self, "plan_records", tuple(sorted(records, key=lambda r: r.plan_id)))

        documents = tuple(self.plan_documents or ())
        for plan_input in documents:
            if not isinstance(plan_input, PlanDocumentInput):
                raise GovernanceProjectionError("INVALID_INPUT", "plan_documents entries must be PlanDocumentInput")
            if plan_input.document.source_digest and not plan_input.document.source_digest.strip():
                raise GovernanceProjectionError("INVALID_INPUT", "plan document digest must be non-empty when present")
        plan_ids = [plan_input.plan_id for plan_input in documents]
        if len(plan_ids) != len(set(plan_ids)):
            raise GovernanceProjectionError("DUPLICATE_PLAN_DOCUMENT", "plan_documents contains duplicate plan ids")
        object.__setattr__(self, "plan_documents", tuple(sorted(documents, key=lambda d: d.plan_id)))

        if self.architecture is not None:
            if not isinstance(self.architecture, ArchitectureStateInput):
                raise GovernanceProjectionError("INVALID_INPUT", "architecture must be ArchitectureStateInput or None")
            if self.architecture.project_id != self.project_id:
                raise GovernanceProjectionError("PROJECT_MISMATCH", "architecture snapshot belongs to another project")

        if self.coordinator_states is not None:
            states = tuple(self.coordinator_states)
            for state in states:
                if not isinstance(state, TaskMainCoordinatorState):
                    raise GovernanceProjectionError("INVALID_INPUT", "coordinator_states entries must be TaskMainCoordinatorState")
                if state.project_id != self.project_id:
                    raise GovernanceProjectionError("PROJECT_MISMATCH", "coordinator state belongs to another project")
            coordinator_ids = [state.coordinator_id for state in states]
            if len(coordinator_ids) != len(set(coordinator_ids)):
                raise GovernanceProjectionError("DUPLICATE_COORDINATOR_STATE", "coordinator_states contains duplicate ids")
            object.__setattr__(self, "coordinator_states", tuple(sorted(states, key=lambda s: s.coordinator_id)))

        if self.execution_records is not None:
            records = tuple(self.execution_records)
            for record in records:
                if not isinstance(record, DurableExecutionRecord):
                    raise GovernanceProjectionError("INVALID_INPUT", "execution_records entries must be DurableExecutionRecord")
            task_ids = [record.canonical_task_id for record in records]
            if len(task_ids) != len(set(task_ids)):
                raise GovernanceProjectionError("DUPLICATE_EXECUTION_RECORD", "execution_records contains duplicate task ids")
            object.__setattr__(self, "execution_records", tuple(sorted(records, key=lambda r: r.canonical_task_id)))


@dataclass(frozen=True)
class ProjectionRefreshRequest:
    """Smallest deterministic refresh contract for later M2 consumers.

    W1 only defines the bounded trigger vocabulary and the deterministic
    rebuild entry point.  No trigger is wired until a real consumer exists
    (``CONSUMER_REQUIRED_BEFORE_TRIGGER_WIRING=yes``); there is no watcher and
    no daemon.
    """

    trigger: str
    source: GovernanceProjectionInput

    def __post_init__(self) -> None:
        if self.trigger not in REFRESH_TRIGGERS:
            raise GovernanceProjectionError("UNKNOWN_REFRESH_TRIGGER", f"trigger must be one of {sorted(REFRESH_TRIGGERS)}")
        if not isinstance(self.source, GovernanceProjectionInput):
            raise GovernanceProjectionError("INVALID_INPUT", "source must be a GovernanceProjectionInput")


def plan_document_input(
    *,
    plan_id: str,
    document: PortablePlanDocument,
    title: str | None = None,
    current_milestone: str | None = None,
    authority: PlanAuthorityBinding | None = None,
) -> PlanDocumentInput:
    """Bounded read adapter: pair a normalized Plan document with read facts."""
    return PlanDocumentInput(
        plan_id=plan_id,
        document=document,
        title=title,
        current_milestone=current_milestone,
        authority=authority,
    )


def architecture_state_from_plan_document(
    *,
    project_id: str,
    plan_id: str,
    document: PortablePlanDocument,
    authority_ref: str | None = None,
) -> ArchitectureStateInput:
    """Derive accepted architecture facts from an accepted Plan document.

    This is the bounded proof that the Architecture Card needs no schema
    extension: accepted baseline facts are already carried by accepted Plan
    authority (governance/current fields).  Absent facts stay ``None``.
    """
    if not is_plan_id(plan_id):
        raise GovernanceProjectionError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
    if not isinstance(document, PortablePlanDocument):
        raise GovernanceProjectionError("INVALID_INPUT", "document must be a PortablePlanDocument")

    def _field(keys: tuple[str, ...]) -> str | None:
        for key in keys:
            for source in (document.governance, document.current_fields):
                value = source.get(key) if isinstance(source, dict) else None
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    delta_ref = _field(ARCHITECTURE_DELTA_REF_KEYS)
    return ArchitectureStateInput(
        project_id=project_id,
        baseline_id=_field(ARCHITECTURE_BASELINE_ID_KEYS),
        baseline_status=_field(ARCHITECTURE_BASELINE_STATUS_KEYS),
        accepted_ref=authority_ref,
        plan_delta_ref=delta_ref,
        source_revision=document.source_revision,
        source_digest=document.source_digest or None,
    )


def _derive_goal_summary(document: PortablePlanDocument) -> str | None:
    for key in GOAL_SUMMARY_KEYS:
        for source in (document.current_fields, document.governance):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                text = value.strip()
                return text[:MAX_GOAL_SUMMARY_LENGTH]
    return None


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


class GovernanceProjectionEngine:
    """Narrow deterministic projection seam (cards in; cards/MAP/STATUS out)."""

    CARD_KINDS_IMPLEMENTED = CARD_KINDS

    def rebuild(self, source: GovernanceProjectionInput) -> GovernanceProjectionBundle:
        """Deterministic rebuild over one governance truth snapshot."""
        if not isinstance(source, GovernanceProjectionInput):
            raise GovernanceProjectionError("INVALID_INPUT", "source must be a GovernanceProjectionInput")
        plans = self.plan_cards(source)
        milestones = self.current_milestone_cards(source)
        return GovernanceProjectionBundle(
            project=self.project_card(source),
            plans=plans,
            milestones=milestones,
            architecture=self.architecture_card(source),
            progress=self.progress_card(source),
        )

    def rebuild_refresh(self, request: ProjectionRefreshRequest) -> GovernanceProjectionBundle:
        """Deterministic rebuild entry point for a bounded refresh trigger."""
        if not isinstance(request, ProjectionRefreshRequest):
            raise GovernanceProjectionError("INVALID_INPUT", "request must be a ProjectionRefreshRequest")
        return self.rebuild(request.source)

    # -- project -------------------------------------------------------------

    def project_card(self, source: GovernanceProjectionInput) -> ProjectCard:
        records = source.plan_records
        missing: list[str] = []
        warnings: list[str] = []
        source_refs: list[CardSourceRef] = []
        if records is None:
            missing.append("plan_records")
            plan_refs: tuple[str, ...] = ()
            plan_count = 0
            records_digest = None
        else:
            plan_refs = tuple(record.plan_id for record in records)
            plan_count = len(records)
            records_digest = _digest([record.to_dict() for record in records])
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PROJECT_GOVERNANCE_STORE,
                    ref=f"project-governance:{source.project_id}",
                    revision=str(max((record.revision for record in records), default=0)),
                    digest=records_digest,
                )
            )
        navigation: list[CardNavigationRef] = [
            CardNavigationRef(
                kind=NAVIGATION_KIND_PROJECT,
                ref=source.project_ref or f"project:{source.project_id}",
            )
        ]
        for plan_ref in plan_refs[: MAX_CARD_NAVIGATION_REFS - 1]:
            navigation.append(CardNavigationRef(kind=NAVIGATION_KIND_PLAN, ref=f"plan:{plan_ref}"))
        if len(plan_refs) > MAX_CARD_NAVIGATION_REFS - 1:
            warnings.append("plan_navigation_refs_truncated")
        return ProjectCard(
            project_id=source.project_id,
            project_ref=source.project_ref,
            project_status=source.project_status,
            plan_refs=plan_refs,
            plan_count=plan_count,
            governance_root_refs=source.governance_root_refs,
            source_revision=None,
            source_digest=records_digest,
            source_refs=tuple(source_refs),
            navigation_refs=tuple(navigation),
            complete=records is not None,
            missing_facts=tuple(missing),
            warnings=tuple(warnings),
        )

    # -- plans / milestones --------------------------------------------------

    def plan_cards(self, source: GovernanceProjectionInput) -> tuple[PlanCard, ...]:
        record_ids = {record.plan_id for record in (source.plan_records or ())}
        document_ids = {plan_input.plan_id for plan_input in source.plan_documents}
        return tuple(self.plan_card(source, plan_id) for plan_id in sorted(record_ids | document_ids))

    def plan_card(self, source: GovernanceProjectionInput, plan_id: str) -> PlanCard:
        if not is_plan_id(plan_id):
            raise GovernanceProjectionError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        records = {record.plan_id: record for record in (source.plan_records or ())}
        documents = {plan_input.plan_id: plan_input for plan_input in source.plan_documents}
        record = records.get(plan_id)
        plan_input = documents.get(plan_id)
        if record is None and plan_input is None:
            raise GovernanceProjectionError("PLAN_NOT_FOUND", f"plan {plan_id!r} is not present in the projection input")

        authority: PlanAuthorityBinding | None = record.authority if record is not None else None
        if (
            record is not None
            and plan_input is not None
            and plan_input.authority is not None
            and plan_input.authority != record.authority
        ):
            raise GovernanceProjectionError(
                "PLAN_AUTHORITY_CONFLICT",
                "Plan document authority disagrees with the Project Governance Store record (no silent dual authority)",
            )
        if authority is None and plan_input is not None:
            authority = plan_input.authority

        document = plan_input.document if plan_input is not None else None
        missing: list[str] = []
        if authority is None:
            missing.append("plan_authority")
        if document is None:
            missing.append("plan_document")
        if record is None:
            missing.append("plan_lifecycle_state")

        title = plan_input.title if plan_input is not None else None
        current_milestone = None
        if plan_input is not None:
            current_milestone = plan_input.current_milestone
        if current_milestone is None and document is not None:
            current_milestone = document.current_milestone

        source_refs: list[CardSourceRef] = []
        navigation: list[CardNavigationRef] = []
        if record is not None:
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PROJECT_GOVERNANCE_STORE,
                    ref=f"project-governance:{source.project_id}",
                    revision=str(record.revision),
                    digest=_digest(record.to_dict()),
                )
            )
        if authority is not None:
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PLAN_AUTHORITY,
                    ref=authority.authority_ref,
                    revision=str(authority.source_revision) if authority.source_revision is not None else None,
                    digest=authority.source_digest,
                )
            )
            navigation.append(CardNavigationRef(kind=NAVIGATION_KIND_PLAN, ref=authority.authority_ref))
        else:
            navigation.append(CardNavigationRef(kind=NAVIGATION_KIND_PLAN, ref=f"plan:{plan_id}"))
        if document is not None:
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PLAN_DOCUMENT,
                    ref=authority.authority_ref if authority is not None else f"plan:{plan_id}",
                    revision=document.source_revision,
                    digest=document.source_digest or None,
                )
            )
        if current_milestone is not None:
            navigation.append(
                CardNavigationRef(kind=NAVIGATION_KIND_MILESTONE, ref=f"milestone:{plan_id}:{current_milestone}")
            )

        return PlanCard(
            project_id=source.project_id,
            plan_id=plan_id,
            authority_source_kind=authority.source_kind if authority is not None else None,
            authority_ref=authority.authority_ref if authority is not None else None,
            authority_revision=str(authority.source_revision) if authority is not None and authority.source_revision is not None else None,
            authority_digest=authority.source_digest if authority is not None else None,
            lifecycle_state=record.lifecycle_state if record is not None else None,
            record_revision=record.revision if record is not None else None,
            plan_status=document.plan_status if document is not None else None,
            current_milestone=current_milestone,
            title=title,
            goal_summary=_derive_goal_summary(document) if document is not None else None,
            source_revision=document.source_revision if document is not None else None,
            source_digest=(document.source_digest or None) if document is not None else None,
            source_refs=tuple(source_refs),
            navigation_refs=tuple(navigation),
            complete=not missing,
            missing_facts=tuple(missing),
        )

    def current_milestone_cards(self, source: GovernanceProjectionInput) -> tuple[MilestoneCard, ...]:
        cards: list[MilestoneCard] = []
        for plan_input in source.plan_documents:
            current = plan_input.current_milestone or plan_input.document.current_milestone
            if current is None:
                continue
            cards.append(self.milestone_card(source, plan_input.plan_id, current))
        return tuple(cards)

    def milestone_card(self, source: GovernanceProjectionInput, plan_id: str, milestone_id: str) -> MilestoneCard:
        if not is_plan_id(plan_id):
            raise GovernanceProjectionError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        if not is_milestone_id(milestone_id):
            raise GovernanceProjectionError("INVALID_MILESTONE_ID", "milestone_id must match M<digits>")
        documents = {plan_input.plan_id: plan_input for plan_input in source.plan_documents}
        plan_input = documents.get(plan_id)
        current_milestone = None
        if plan_input is not None:
            current_milestone = plan_input.current_milestone or plan_input.document.current_milestone
        if plan_input is None:
            return MilestoneCard(
                project_id=source.project_id,
                plan_id=plan_id,
                milestone_id=milestone_id,
                complete=False,
                missing_facts=("plan_document",),
            )

        document = plan_input.document
        spec = document.milestone_specs.get(milestone_id)
        status = document.milestone_status.get(milestone_id) or (spec.get("status") if spec else None)
        if spec is None and milestone_id not in document.milestone_status:
            raise GovernanceProjectionError(
                "MILESTONE_NOT_FOUND",
                f"milestone {milestone_id!r} is not declared by the accepted Plan document",
            )

        missing: list[str] = []
        warnings: list[str] = []
        if status is None:
            missing.append("milestone_status")
        prose = (document.milestone_section_prose.get(milestone_id) or "").strip()
        objective_summary = None
        objective_ref = None
        if prose:
            objective_summary, truncated = _truncate(prose, MAX_OBJECTIVE_LENGTH)
            if truncated:
                warnings.append("milestone_objective_truncated")
            objective_ref = f"milestone:{plan_id}:{milestone_id}"
        else:
            missing.append("milestone_objective")

        source_refs = [
            CardSourceRef(
                role=SOURCE_ROLE_PLAN_DOCUMENT,
                ref=(plan_input.authority.authority_ref if plan_input.authority is not None else f"plan:{plan_id}"),
                revision=document.source_revision,
                digest=document.source_digest or None,
            )
        ]
        if plan_input.authority is not None:
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PLAN_AUTHORITY,
                    ref=plan_input.authority.authority_ref,
                    revision=(
                        str(plan_input.authority.source_revision)
                        if plan_input.authority.source_revision is not None
                        else None
                    ),
                    digest=plan_input.authority.source_digest,
                )
            )
        navigation = [CardNavigationRef(kind=NAVIGATION_KIND_MILESTONE, ref=f"milestone:{plan_id}:{milestone_id}")]
        if plan_input.authority is not None:
            navigation.append(CardNavigationRef(kind=NAVIGATION_KIND_PLAN, ref=plan_input.authority.authority_ref))

        return MilestoneCard(
            project_id=source.project_id,
            plan_id=plan_id,
            milestone_id=milestone_id,
            milestone_title=spec.get("title") if isinstance(spec, dict) else None,
            milestone_status=status,
            objective_summary=objective_summary,
            objective_ref=objective_ref,
            work_item_refs=document.milestone_work_items.get(milestone_id, ()),
            dependency_edges=document.milestone_dependencies.get(milestone_id, ()),
            approval_satisfied=document.milestone_approvals.get(milestone_id),
            is_current=current_milestone == milestone_id,
            source_revision=document.source_revision,
            source_digest=document.source_digest or None,
            source_refs=tuple(source_refs),
            navigation_refs=tuple(navigation),
            complete=not missing,
            missing_facts=tuple(missing),
            warnings=tuple(warnings),
        )

    # -- architecture --------------------------------------------------------

    def architecture_card(self, source: GovernanceProjectionInput) -> ArchitectureCard:
        state = source.architecture
        if state is None:
            for plan_input in source.plan_documents:
                authority_ref = (
                    plan_input.authority.authority_ref if plan_input.authority is not None else None
                )
                candidate = architecture_state_from_plan_document(
                    project_id=source.project_id,
                    plan_id=plan_input.plan_id,
                    document=plan_input.document,
                    authority_ref=authority_ref,
                )
                if candidate.baseline_id is not None:
                    state = candidate
                    break

        if state is None:
            return ArchitectureCard(
                project_id=source.project_id,
                complete=False,
                missing_facts=("architecture_baseline", "accepted_architecture_ref"),
            )

        missing: list[str] = []
        if state.baseline_id is None:
            missing.append("architecture_baseline")
        if state.accepted_ref is None:
            missing.append("accepted_architecture_ref")

        source_refs: list[CardSourceRef] = []
        navigation: list[CardNavigationRef] = []
        if state.accepted_ref is not None:
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_ARCHITECTURE,
                    ref=state.accepted_ref,
                    revision=state.source_revision,
                    digest=state.source_digest,
                )
            )
            navigation.append(CardNavigationRef(kind=NAVIGATION_KIND_ARCHITECTURE, ref=state.accepted_ref))

        return ArchitectureCard(
            project_id=source.project_id,
            baseline_id=state.baseline_id,
            baseline_status=state.baseline_status,
            accepted_ref=state.accepted_ref,
            plan_delta_ref=state.plan_delta_ref,
            source_revision=state.source_revision,
            source_digest=state.source_digest,
            current_version=state.current_version,
            current_digest=state.current_digest,
            promotion_receipt_ref=state.promotion_receipt_ref,
            promoted_by_plan_id=state.promoted_by_plan_id,
            source_refs=tuple(source_refs),
            navigation_refs=tuple(navigation),
            complete=not missing,
            missing_facts=tuple(missing),
        )

    # -- progress ------------------------------------------------------------

    def progress_card(self, source: GovernanceProjectionInput) -> ProgressCard:
        missing: list[str] = []
        source_refs: list[CardSourceRef] = []

        plan_lifecycle: tuple[PlanLifecycleFact, ...] = ()
        if source.plan_records is None:
            missing.append("plan_lifecycle_records")
        else:
            plan_lifecycle = tuple(
                PlanLifecycleFact(
                    plan_id=record.plan_id,
                    lifecycle_state=record.lifecycle_state,
                    authority_source_kind=record.authority.source_kind,
                )
                for record in source.plan_records
            )
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_PROJECT_GOVERNANCE_STORE,
                    ref=f"project-governance:{source.project_id}",
                    revision=str(max((record.revision for record in source.plan_records), default=0)),
                    digest=_digest([record.to_dict() for record in source.plan_records]),
                )
            )

        coordinator_facts: tuple[CoordinatorProgressFact, ...] = ()
        active_coordinator_count = 0
        open_blockers: list[str] = []
        human_brake_active: bool | None = None
        next_action: str | None = None
        coordinator_digest: str | None = None
        if source.coordinator_states is None:
            missing.append("task_main_coordinator_state")
        else:
            states = source.coordinator_states
            coordinator_digest = _digest([state.to_dict() for state in states])
            facts: list[CoordinatorProgressFact] = []
            for state in states:
                counts = Counter(state.wi_status.values())
                facts.append(
                    CoordinatorProgressFact(
                        coordinator_id=state.coordinator_id,
                        plan_authority=state.plan_authority,
                        milestone_id=state.milestone_id,
                        status=state.status.value,
                        work_item_count=len(state.work_items),
                        wi_status_counts=tuple(counts.items()),
                        progression_revision=state.progression_revision,
                        human_brake_active=state.human_brake is not None,
                        open_blockers=state.open_blockers,
                        next_action=state.next_action,
                    )
                )
            coordinator_facts = tuple(facts)
            active_coordinator_count = sum(
                1 for state in states if str(getattr(state.status, "value", state.status)) == "ACTIVE"
            )
            open_blockers = sorted({blocker for state in states for blocker in state.open_blockers})
            human_brake_active = any(state.human_brake is not None for state in states)
            if len(states) == 1:
                next_action = states[0].next_action
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_TASK_MAIN_COORDINATOR_STATE,
                    ref=f"task-main-coordinator:{source.project_id}",
                    revision=str(max((state.coordinator_revision for state in states), default=0)),
                    digest=coordinator_digest,
                )
            )

        execution_aggregate: ExecutionAggregateFact | None = None
        execution_digest: str | None = None
        if source.execution_records is None:
            missing.append("execution_state_store")
        else:
            records = source.execution_records
            execution_digest = _digest(
                [
                    {
                        "canonical_task_id": record.canonical_task_id,
                        "execution_phase": record.execution_phase.value,
                        "canonical_task_state": record.canonical_task_state.value,
                        "delivery_state": record.delivery_state.value,
                        "record_revision": record.record_revision,
                    }
                    for record in records
                ]
            )
            by_state = Counter(record.canonical_task_state.value for record in records)
            by_delivery = Counter(record.delivery_state.value for record in records)
            terminal_records = [record for record in records if record.canonical_task_state.is_terminal]
            terminal_ok = sum(1 for record in terminal_records if record.terminal_result is not None and record.terminal_result.ok)
            terminal_failed = sum(
                1 for record in terminal_records if record.terminal_result is not None and not record.terminal_result.ok
            )
            requiring_recovery = sum(
                1
                for record in records
                if (not record.canonical_task_state.is_terminal)
                or record.delivery_state not in EXECUTION_TERMINAL_DELIVERY_STATES
            )
            execution_aggregate = ExecutionAggregateFact(
                total=len(records),
                by_canonical_task_state=tuple(by_state.items()),
                by_delivery_state=tuple(by_delivery.items()),
                terminal_ok=terminal_ok,
                terminal_failed=terminal_failed,
                requiring_recovery=requiring_recovery,
            )
            source_refs.append(
                CardSourceRef(
                    role=SOURCE_ROLE_EXECUTION_STATE_STORE,
                    ref=f"execution-state:{source.project_id}",
                    revision=None,
                    digest=execution_digest,
                )
            )

        combined_digest = _digest(
            {
                "plan_records_digest": _digest([record.to_dict() for record in source.plan_records])
                if source.plan_records is not None
                else None,
                "coordinator_digest": coordinator_digest,
                "execution_digest": execution_digest,
            }
        )

        return ProgressCard(
            project_id=source.project_id,
            plan_lifecycle=plan_lifecycle,
            coordinator_facts=coordinator_facts,
            execution_aggregate=execution_aggregate,
            active_coordinator_count=active_coordinator_count,
            open_blockers=tuple(open_blockers[:MAX_BLOCKERS]),
            human_brake_active=human_brake_active,
            next_action=next_action,
            source_digest=combined_digest,
            source_refs=tuple(source_refs),
            navigation_refs=(CardNavigationRef(kind=NAVIGATION_KIND_PROGRESS, ref=f"progress:{source.project_id}"),),
            complete=not missing,
            missing_facts=tuple(missing),
        )


__all__ = [
    "ALL_CARD_KINDS",
    "CONSUMER_REQUIRED_BEFORE_TRIGGER_WIRING",
    "EVENT_BUS_CREATED",
    "GENERIC_WATCHER_CREATED",
    "NEW_TELEMETRY_SYSTEM",
    "OLD_GRAPH_PROJECTION_OWNERSHIP_CHANGED",
    "PROGRESS_AGGREGATION_IS_BOUNDED",
    "PROJECTION_DATABASE_CREATED",
    "REFRESH_TRIGGERS",
    "REFRESH_TRIGGER_ARCHITECTURE_PROMOTION",
    "REFRESH_TRIGGER_DEFECT_FACT_DECISION_MUTATION",
    "REFRESH_TRIGGER_MILESTONE_TRANSITION",
    "REFRESH_TRIGGER_PLAN_MUTATION",
    "REFRESH_TRIGGER_PROJECT_ROOT_BINDING_CHANGE",
    "REFRESH_TRIGGER_WORK_RECONCILIATION",
    "SECOND_GENERIC_PROJECTION_FRAMEWORK",
    "W1_DECIDES_LLM_RETRIEVAL_STRATEGY",
    "ArchitectureStateInput",
    "GovernanceProjectionEngine",
    "GovernanceProjectionError",
    "GovernanceProjectionInput",
    "PlanDocumentInput",
    "ProjectionRefreshRequest",
    "architecture_state_from_plan_document",
    "plan_document_input",
]
