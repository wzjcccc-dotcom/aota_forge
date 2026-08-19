"""M3-B7 executor-neutral canonical transition layer (Issue #9, lane M3-B7).

This module composes the accepted B3-B6 foundations into four canonical
lifecycle transitions:

    create_execution
    record_completion
    record_decision
    create_followup_subject

B7 is a MECHANICAL transition orchestrator.  It REUSES, never reinvents:

* B3 — Subject graph, record ownership, owning-Subject resolution
* B4 — ``InternalId``, ``ObjectRef``, ID Broker
* B5 — Authority Engine + Capability Lease
* B6 — Subject revision/CAS, transaction, idempotency, atomic lease
  consumption, and the parent + new-child transaction foundation

It is NOT a semantic reasoner and invents no scope, approval, decision,
identity policy, or Subject selection (``B7_IS_SEMANTIC_REASONER=no``,
``B7_SELECTS_SUBJECT_HEURISTICALLY=no``, ``B7_INVENTS_SCOPE=no``,
``B7_INVENTS_APPROVAL=no``, ``B7_INVENTS_DECISION=no``,
``B7_INVENTS_IDENTITY_POLICY=no``).

Canonical record authority root (preserved for every child-record mutation):

    record -> owning Subject -> authority -> Subject revision/CAS

``TRANSITION_SOURCE_IMPLEMENTED=yes``;
``TRANSITION_RUNTIME_AUTHORITY_ACTIVE=no``;
``PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no``;
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``.  These transitions run only against
the isolated, NON-AUTHORITATIVE ``TransactionStore`` (B6 fixture scope).  No
CLI / Hermes / production ingress / catalog wiring is activated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import os

from aota_forge.core.authority import (
    MaterializedDecisionEvidence,
)
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import (
    ProjectBinding,
    TrustedContext,
    prepare_project_binding,
)
from aota_forge.core.contracts.mutation import MutationEffect, MutationIntent, MutationPreconditions
from aota_forge.core.contracts.results import LifecycleResult, lifecycle_result
from aota_forge.core.contracts.validation import validate_lifecycle_preconditions
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    OwningSubjectResolver,
    assert_object_ref_kind,
)
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import mint_subject_value
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.revision import (
    AuthorityDeniedError,
    IdempotencyConflictError,
    LeaseReplayDeniedError,
    MutationError,
    StaleRevisionError,
    revision_number_of,
)
from aota_forge.core.transaction import (
    ParentChildTransaction,
    SubjectTransaction,
    TransactionStore,
)

# ---------------------------------------------------------------------------
# Module boundary flags (see plan sections 26, 30, 47).
# ---------------------------------------------------------------------------

FORGE_CORE_TRANSITIONS_CALLABLE_DIRECTLY = True
CLI_TRANSITION_WIRING_IMPLEMENTED = False
HERMES_TRANSITION_WIRING_IMPLEMENTED = False
PRODUCTION_INGRESS_TRANSITION_WIRING_IMPLEMENTED = False

TRANSITION_SOURCE_IMPLEMENTED = True
TRANSITION_RUNTIME_AUTHORITY_ACTIVE = False
PRODUCTION_GRAPH_AUTHORITY_ACTIVE = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
CUTOVER_PERFORMED = False
SHADOW_GRAPH_MATERIALIZATION_PERFORMED = False
PROJECTION_RECONSTRUCTION_IMPLEMENTED = False
BINDING_RECOVERY_IMPLEMENTED = False

# M4-4 owns direct mechanical lifecycle behavior only.  These remain disabled
# until the later authorization, ingress, and external-authority lanes close.
PLAN_INIT_REENTRY_DENIED = True
RETIRED_PLAN_IMPLICIT_RESURRECTION_ALLOWED = False
M4_4_CAPABILITY_LEASE_ISSUER_IMPLEMENTED = False
M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED = False
M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED = False
M4_4_DURABLE_JOURNAL_IMPLEMENTED = False

# ---------------------------------------------------------------------------
# Reuse signals (B4/B5/B6).
# ---------------------------------------------------------------------------

B7_REUSES_B5_AUTHORITY_ENGINE = True
B7_DUPLICATE_AUTHORITY_ENGINE_CREATED = False
B7_REUSES_B6_CAS = True
B7_DUPLICATE_CAS_IMPLEMENTATION_CREATED = False
B7_REUSES_B6_TRANSACTION = True
B7_DUPLICATE_TRANSACTION_ENGINE_CREATED = False
B7_REUSES_B6_IDEMPOTENCY = True
B7_DUPLICATE_IDEMPOTENCY_SYSTEM_CREATED = False
B7_REUSES_B4_INTERNAL_ID = True
B7_REUSES_B4_OBJECT_REF = True
B7_REUSES_B4_ID_BROKER = True
B7_NEW_IDENTITY_POLICY_CREATED = False
B7_DUPLICATE_GRAPH_RECORD_MODEL_CREATED = False

B7_SILENT_REBASE_ALLOWED = False
B7_LAST_WRITE_WINS_ALLOWED = False

# ---------------------------------------------------------------------------
# Canonical transition role signals (plan sections 4, 24).
# ---------------------------------------------------------------------------

B7_IS_CANONICAL_TRANSITION_LAYER = True
B7_IS_SEMANTIC_REASONER = False
B7_SELECTS_SUBJECT_HEURISTICALLY = False
B7_INVENTS_SCOPE = False
B7_INVENTS_APPROVAL = False
B7_INVENTS_DECISION = False
B7_INVENTS_IDENTITY_POLICY = False

CREATE_EXECUTION_AUTHORITY_ROOT = "Subject"
RECORD_COMPLETION_AUTHORITY_ROOT = "owning_Subject"
RECORD_DECISION_AUTHORITY_ROOT = "owning_Subject"
EXECUTION_IS_INDEPENDENT_AUTHORITY_ROOT = False
COMPLETION_IS_INDEPENDENT_AUTHORITY_ROOT = False
DECISION_IS_INDEPENDENT_AUTHORITY_ROOT = False

COMPLETION_DIRECT_SUBJECT_REF_REQUIRED = False
COMPLETION_SUBJECT_REF_FIELD_CREATED = False

FOLLOWUP_REQUIRES_MATERIALIZED_DECISION = True
FOLLOWUP_CHILD_ID_MINTED_INSIDE_TRANSACTION = True
FOLLOWUP_CHILD_INITIAL_REVISION = 1
FOLLOWUP_EDGE_REQUIRES_DECISION_REF = True
FOLLOWUP_DECISION_MISMATCH_DENIED = True
FOLLOWUP_FOREIGN_DECISION_DENIED = True
FOLLOWUP_MISSING_DECISION_DENIED = True
FOLLOWUP_IDEMPOTENT_REPLAY_CREATES_SECOND_CHILD = False
FOLLOWUP_IDEMPOTENT_REPLAY_CREATES_SECOND_EDGE = False
FOLLOWUP_IDEMPOTENT_REPLAY_ADVANCES_PARENT_REVISION_AGAIN = False
PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY = False
CHILD_LEASE_REQUIRED_PRE_TRANSACTION = False

READONLY_DIAGNOSTIC_PLANE_PRESERVED = True

WRITE_SCOPE = {"mode": "write"}
_MINTED_ID_PREFIXES = {
    IdKind.EXECUTION: "exec",
    IdKind.COMPLETION: "comp",
    IdKind.DECISION: "dec",
    IdKind.EDGE: "edge",
}


# ---------------------------------------------------------------------------
# Deterministic bounded transition errors.
# ---------------------------------------------------------------------------


class TransitionError(MutationError):
    """Base class for B7 canonical transition failures (bounded code)."""

    code = "B7_TRANSITION_ERROR"
    default_message = "canonical transition error"


class TransitionInvalidError(TransitionError):
    """The canonical request is malformed or carries an unsupported shape."""

    code = "B7_INVALID_REQUEST"
    default_message = "invalid canonical transition request"


class TransitionDeniedError(TransitionError):
    """A canonical referential / decision guard denied the transition."""

    code = "B7_DENIED"
    default_message = "canonical transition denied"


class TransitionNotFoundError(TransitionError):
    """A required canonical reference could not be resolved."""

    code = "B7_REF_NOT_FOUND"
    default_message = "canonical reference not found"


# ---------------------------------------------------------------------------
# Canonical result model (executor-neutral; plan section 7).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    """Bounded executor-neutral result of one canonical transition.

    ``code`` is a deterministic bounded result code (``COMMITTED`` |
    ``REPLAYED``).  It never exposes credentials, private host paths, storage
    internals, or executor-private state.
    """

    transition: str
    owning_subject: str
    revision_after: int
    revision_token: str
    created_objects: tuple = ()
    replayed: bool = False
    code: str = "COMMITTED"


# ---------------------------------------------------------------------------
# Canonical request model (bounded typed input; plan section 6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateExecutionRequest:
    trusted_context: TrustedContext
    subject_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    executor_kind: str
    mechanical_status: str = "running"
    correlation_id: str | None = None
    executor_execution_ref: str | None = None


@dataclass(frozen=True)
class RecordCompletionRequest:
    trusted_context: TrustedContext
    execution_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    outcome: str
    evidence_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class RecordDecisionRequest:
    trusted_context: TrustedContext
    subject_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    decision_kind: str
    statement: str
    target_refs: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class CreateFollowupSubjectRequest:
    trusted_context: TrustedContext
    parent_ref: ObjectRef
    decision_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    child_kind: str
    child_sub_kind: str
    rationale: str | None = None
    child_creation_context: dict = field(default_factory=dict)
    child_id_derivation: str = "minted"


@dataclass(frozen=True)
class PlanInitRequest:
    """Already-decided PLAN_INIT request for one exact Plan Subject."""

    trusted_context: TrustedContext
    plan_ref: ObjectRef
    intent: MutationIntent
    preconditions: MutationPreconditions
    trusted_time: datetime
    project_evidence: object | None = None
    project_binding: ProjectBinding | None = None
    lease: CapabilityLease | None = None


@dataclass(frozen=True)
class RetirementCandidateSnapshot:
    """Bounded stage-one retirement evidence bound to one observed Plan state."""

    snapshot_identity: str
    source_revision: int
    source_digest: str
    plan_ref: str
    plan_state: str
    active_current_protection: bool
    running_task_protection: bool
    eligible_retirement_kinds: tuple[str, ...]
    successor_eligibility: tuple[str, ...]
    candidate_identity: str
    authority_source_revision: str | int | None
    authority_observed_raw_digest: str | None

    def to_dict(self) -> dict:
        return {
            "snapshot_identity": self.snapshot_identity,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
            "plan_ref": self.plan_ref,
            "plan_state": self.plan_state,
            "active_current_protection": self.active_current_protection,
            "running_task_protection": self.running_task_protection,
            "eligible_retirement_kinds": list(self.eligible_retirement_kinds),
            "successor_eligibility": list(self.successor_eligibility),
            "candidate_identity": self.candidate_identity,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
        }


@dataclass(frozen=True)
class RetirementCandidateResolution:
    """Stage-one result; no graph or authority mutation occurs."""

    result: LifecycleResult
    candidates: tuple[RetirementCandidateSnapshot, ...] = ()

    @property
    def code(self) -> str:
        return self.result.code

    @property
    def mutation_effect(self) -> MutationEffect:
        return self.result.mutation_effect


@dataclass(frozen=True)
class PlanRetirementRequest:
    """Stage-two exact retirement request bound to a prior snapshot."""

    trusted_context: TrustedContext
    plan_ref: ObjectRef
    snapshot: RetirementCandidateSnapshot
    intent: MutationIntent
    preconditions: MutationPreconditions
    trusted_time: datetime
    retirement_kind: str
    successor_ref: ObjectRef | None = None
    lease: CapabilityLease | None = None


# ---------------------------------------------------------------------------
# Validation helpers (fail closed, never heuristic).
# ---------------------------------------------------------------------------


def _require_store(store: object) -> TransactionStore:
    if not isinstance(store, TransactionStore):
        raise TransitionInvalidError(
            "canonical transitions require the isolated B6 TransactionStore"
        )
    return store


def _require_lease(lease: object) -> CapabilityLease:
    if not isinstance(lease, CapabilityLease):
        raise TransitionInvalidError("a Capability Lease is required")
    return lease


def _require_object_ref(ref: object, kind: str, label: str) -> ObjectRef:
    if not isinstance(ref, ObjectRef):
        raise TransitionInvalidError(f"{label} must be a B4 ObjectRef")
    assert_object_ref_kind(ref, kind, label)
    return ref


def _require_subject_ref(ref: object, label: str) -> ObjectRef:
    return _require_object_ref(ref, IdKind.SUBJECT, label)


def _bounded(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise TransitionInvalidError(f"{label} must be a bounded non-empty string")
    return value


def _bounded_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise TransitionInvalidError(f"{label} must be a bounded idempotency token")
    return value


def _bounded_scope(scope: object) -> dict:
    if not isinstance(scope, dict) or not scope:
        raise TransitionInvalidError("requested scope must be a non-empty mapping")
    return dict(scope)


def _iso(value: datetime) -> str:
    return value.astimezone().isoformat()


def _mint_id(kind: str, prefix: str) -> InternalId:
    return make_id(kind, f"{prefix}_{os.urandom(12).hex()}")


def _resolve_subject(store: TransactionStore, ref: ObjectRef) -> records.Subject:
    try:
        return store.read_subject(ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"subject not found: {ref.serialize()}") from exc


def _resolve_decision(store: TransactionStore, ref: ObjectRef) -> records.Decision:
    if not isinstance(ref, ObjectRef) or ref.object_kind != IdKind.DECISION:
        raise TransitionInvalidError("decision_ref must be a Decision ObjectRef")
    try:
        return store.decision(ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"decision not found: {ref.serialize()}") from exc


def _result(
    transition: str,
    subject_ref: ObjectRef,
    tx_result,
    created: tuple,
) -> TransitionResult:
    effect_refs = tuple(tx_result.effect_refs) if tx_result.effect_refs else tuple(created)
    return TransitionResult(
        transition=transition,
        owning_subject=subject_ref.serialize(),
        revision_after=tx_result.revision_after,
        revision_token=tx_result.revision_token,
        created_objects=effect_refs,
        replayed=bool(tx_result.replayed),
        code="REPLAYED" if tx_result.replayed else "COMMITTED",
    )


def _lifecycle_error(
    operation: str,
    code: str,
    message: str,
    *,
    correlation_id: str | None = None,
    data: dict | None = None,
    effect: MutationEffect = MutationEffect.NO_EFFECT,
) -> LifecycleResult:
    return lifecycle_result(
        operation,
        code,
        effect,
        message=message,
        correlation_id=correlation_id,
        data=data,
    )


def _lifecycle_choice(
    operation: str,
    code: str,
    choices: tuple[dict, ...],
    *,
    correlation_id: str | None = None,
    data: dict | None = None,
) -> LifecycleResult:
    return lifecycle_result(
        operation,
        code,
        MutationEffect.NEEDS_SEMANTIC_CHOICE,
        correlation_id=correlation_id,
        semantic_choices=choices,
        next_action="provide one exact semantic choice",
        data=data,
    )


def _principal_id(context: object) -> str | None:
    if not isinstance(context, TrustedContext) or not context.is_bound or context.principal is None:
        return None
    return context.principal.id


def _request_identity_result(
    store: TransactionStore,
    *,
    operation: str,
    target: ObjectRef,
    intent: MutationIntent,
    principal: str,
    replay_code: str,
    correlation_id: str | None,
) -> LifecycleResult | None:
    existing = store.lifecycle_idempotency_lookup(
        key=intent.idempotency_key,
        operation=operation,
        target=target,
        principal=principal,
        fingerprint=intent.intent_fingerprint(),
    )
    if existing == "conflict":
        return _lifecycle_error(
            operation,
            "CONFLICT",
            "idempotency identity is bound to a different semantic intent",
            correlation_id=correlation_id,
            effect=MutationEffect.CONFLICT,
        )
    if existing is not None:
        return lifecycle_result(
            operation,
            replay_code,
            MutationEffect.REPLAYED_VERIFIED,
            correlation_id=correlation_id,
            data={
                "replayed": True,
                "revision_after": existing.revision_after,
                "revision_token": existing.revision_token,
            },
        )
    return None


def _require_lifecycle_intent(
    intent: object,
    *,
    operation: str,
    target: ObjectRef,
) -> MutationIntent:
    if not isinstance(intent, MutationIntent):
        raise ValueError("lifecycle request requires MutationIntent")
    if intent.operation != operation or intent.logical_target != target.serialize():
        raise ValueError("lifecycle intent operation or target is invalid")
    return intent


def _precondition_failure(
    subject: records.Subject,
    preconditions: object,
    *,
    expected_subject_revision: int | None = None,
    authority_code: str,
    subject_code: str,
) -> str | None:
    if not isinstance(preconditions, MutationPreconditions):
        return authority_code
    expected = preconditions.subject_expected_revision
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        return subject_code
    if expected_subject_revision is not None and expected != expected_subject_revision:
        return subject_code
    try:
        validate_lifecycle_preconditions(preconditions, expected_revision=expected_subject_revision)
    except (TypeError, ValueError):
        return authority_code
    current = revision_number_of(subject)
    if current != expected:
        return subject_code
    state = subject.mechanical_state
    stored_source = state.get("authority_source_revision")
    stored_digest = state.get("authority_observed_raw_digest")
    if (stored_source is not None or stored_digest is not None) and (
        stored_source != preconditions.authority_source_revision
        or stored_digest != preconditions.authority_observed_raw_digest
    ):
        return authority_code
    return None


def _is_plan_subject(subject: records.Subject) -> bool:
    return subject.subject_id.sub_kind == SubjectKind.PLAN or subject.kind == SubjectKind.PLAN


def _plan_state(subject: records.Subject) -> str:
    value = subject.mechanical_state.get("state")
    return value if isinstance(value, str) and value else "unknown"


def _is_retired_state(state: dict) -> bool:
    return state.get("state") in {"cancelled", "superseded", "retired"} or state.get("retirement_kind") in {
        "abandoned",
        "superseded",
    }


def _active_current_protected(state: dict) -> bool:
    return bool(
        state.get("active")
        or state.get("current")
        or state.get("is_current")
        or state.get("active_plan")
        or state.get("current_plan")
        or state.get("state") in {"active", "current"}
    )


def _running_task_protected(state: dict) -> bool:
    running_tasks = state.get("running_tasks")
    return bool(
        state.get("running_task")
        or state.get("task_running")
        or (isinstance(running_tasks, int) and not isinstance(running_tasks, bool) and running_tasks > 0)
        or state.get("state") == "running"
    )


def _eligible_plan(subject: records.Subject) -> bool:
    state = subject.mechanical_state
    return (
        _is_plan_subject(subject)
        and not _is_retired_state(state)
        and _plan_state(subject) not in {"uninitialized", "unknown"}
        and not _active_current_protected(state)
        and not _running_task_protected(state)
    )


def _successor_refs(store: TransactionStore, target: ObjectRef) -> tuple[str, ...]:
    refs = []
    for subject in store.subjects():
        candidate = make_object_ref(IdKind.SUBJECT, subject.subject_id)
        if candidate == target or not _is_plan_subject(subject):
            continue
        if _is_retired_state(subject.mechanical_state) or _plan_state(subject) in {"uninitialized", "unknown"}:
            continue
        refs.append(candidate.serialize())
    return tuple(refs)


def _snapshot_identity(snapshot: RetirementCandidateSnapshot) -> str:
    payload = snapshot.to_dict()
    payload.pop("snapshot_identity", None)
    return canonical_fingerprint(payload)


def _capture_retirement_snapshot(
    store: TransactionStore,
    plan_ref: ObjectRef,
) -> RetirementCandidateSnapshot:
    subject = _resolve_subject(store, plan_ref)
    if not _is_plan_subject(subject):
        raise TransitionInvalidError("retirement target must be a Plan Subject")
    state = dict(subject.mechanical_state)
    active_current = _active_current_protected(state)
    running_task = _running_task_protected(state)
    eligible = (
        ("abandoned", "superseded")
        if _eligible_plan(subject)
        else ()
    )
    snapshot = RetirementCandidateSnapshot(
        snapshot_identity="",
        source_revision=revision_number_of(subject),
        source_digest=canonical_fingerprint({"subject": subject.canonical_fields()}),
        plan_ref=plan_ref.serialize(),
        plan_state=_plan_state(subject),
        active_current_protection=active_current,
        running_task_protection=running_task,
        eligible_retirement_kinds=eligible,
        successor_eligibility=_successor_refs(store, plan_ref),
        candidate_identity=plan_ref.serialize(),
        authority_source_revision=state.get("authority_source_revision"),
        authority_observed_raw_digest=state.get("authority_observed_raw_digest"),
    )
    return replace(snapshot, snapshot_identity=_snapshot_identity(snapshot))


def _snapshot_matches_current(
    snapshot: RetirementCandidateSnapshot,
    current: RetirementCandidateSnapshot,
) -> bool:
    return snapshot.to_dict() == current.to_dict()


# ---------------------------------------------------------------------------
# create_execution
# ---------------------------------------------------------------------------


def create_execution(store: TransactionStore, req: CreateExecutionRequest) -> TransitionResult:
    """Create one Execution owned by an existing Subject.

    Authority root is the owning Subject; a retry or executor change produces a
    NEW Execution under the SAME Subject (``CREATE_EXECUTION_CREATES_NEW_SUBJECT=no``).
    """
    store = _require_store(store)
    subject_ref = _require_subject_ref(req.subject_ref, "subject_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    executor_kind = _bounded(req.executor_kind, "executor_kind")
    mechanical_status = _bounded(req.mechanical_status or "running", "mechanical_status")
    execution_id = _mint_id(IdKind.EXECUTION, _MINTED_ID_PREFIXES[IdKind.EXECUTION])

    execution = records.execution(
        execution_id,
        subject_ref.internal_id,
        executor_kind,
        mechanical_status=mechanical_status,
        correlation_id=req.correlation_id,
        executor_execution_ref=req.executor_execution_ref,
        started_at=_iso(req.trusted_time),
        requested_principal=(req.trusted_context.principal.id if req.trusted_context.principal else None),
    )
    execution_ref = make_object_ref(IdKind.EXECUTION, execution_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "create_execution",
            "target": subject_ref.serialize(),
            "executor_kind": executor_kind,
            "mechanical_status": mechanical_status,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=subject_ref,
        expected_revision=req.expected_revision,
        operation="create_execution",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(execution_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(execution)
        tx.add_staged_id(execution_id)
    result = tx.commit()
    return _result("create_execution", subject_ref, result, (execution_ref.serialize(),))


# ---------------------------------------------------------------------------
# record_completion
# ---------------------------------------------------------------------------


def record_completion(store: TransactionStore, req: RecordCompletionRequest) -> TransitionResult:
    """Record an immutable Completion for an Execution of an owning Subject.

    ``Completion`` carries NO direct ``subject_ref`` (``COMPLETION_SUBJECT_REF_FIELD_CREATED=no``);
    the owning Subject is reached transitively via Completion -> Execution ->
    Subject.  Authority root is the owning Subject.
    """
    store = _require_store(store)
    execution_ref = _require_object_ref(req.execution_ref, IdKind.EXECUTION, "execution_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    outcome = _bounded(req.outcome, "outcome")
    resolver = OwningSubjectResolver(store)
    try:
        owning = resolver.owning_subject_of_execution(execution_ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"owning subject of execution not found: {execution_ref.serialize()}") from exc
    owning_ref = make_object_ref(IdKind.SUBJECT, owning.subject_id)
    completion_id = _mint_id(IdKind.COMPLETION, _MINTED_ID_PREFIXES[IdKind.COMPLETION])

    completion = records.completion(
        completion_id,
        execution_ref.internal_id,
        outcome,
        evidence_refs=list(req.evidence_refs or []),
        recorded_at=_iso(req.trusted_time),
    )
    completion_ref = make_object_ref(IdKind.COMPLETION, completion_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "record_completion",
            "execution": execution_ref.serialize(),
            "outcome": outcome,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=owning_ref,
        expected_revision=req.expected_revision,
        operation="record_completion",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(completion_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(completion)
        tx.add_staged_id(completion_id)
    result = tx.commit()
    return _result("record_completion", owning_ref, result, (completion_ref.serialize(),))


# ---------------------------------------------------------------------------
# record_decision
# ---------------------------------------------------------------------------


def record_decision(store: TransactionStore, req: RecordDecisionRequest) -> TransitionResult:
    """Create a durable Materialized Decision owned by exactly one Subject.

    The Decision is a canonical child record, not an independent mutation or
    revision root; it is committed inside the isolated transaction foundation.
    """
    store = _require_store(store)
    subject_ref = _require_subject_ref(req.subject_ref, "subject_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    decision_kind = _bounded(req.decision_kind, "decision_kind")
    statement = _bounded(req.statement, "statement")
    decision_id = _mint_id(IdKind.DECISION, _MINTED_ID_PREFIXES[IdKind.DECISION])

    decision = records.decision(
        decision_id,
        subject_ref.internal_id,
        decision_kind,
        statement,
        target_refs=list(req.target_refs or []),
        evidence_refs=list(req.evidence_refs or []),
        decision_time=_iso(req.trusted_time),
    )
    decision_ref = make_object_ref(IdKind.DECISION, decision_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "record_decision",
            "target": subject_ref.serialize(),
            "decision_kind": decision_kind,
            "statement": statement,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=subject_ref,
        expected_revision=req.expected_revision,
        operation="record_decision",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(decision_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(decision)
        tx.add_staged_id(decision_id)
    result = tx.commit()
    return _result("record_decision", subject_ref, result, (decision_ref.serialize(),))


# ---------------------------------------------------------------------------
# create_followup_subject
# ---------------------------------------------------------------------------


def create_followup_subject(store: TransactionStore, req: CreateFollowupSubjectRequest) -> TransitionResult:
    """Create a new child Subject backed by an exact Materialized Decision.

    Uses the B6 parent + new-child transaction foundation: the child Subject ID
    is minted inside the transaction, the child is created at revision 1, a
    Decision-backed FollowupEdge is created, the parent Subject revision
    advances once, the parent lease is consumed, and everything commits
    atomically.
    """
    store = _require_store(store)
    parent_ref = _require_subject_ref(req.parent_ref, "parent_ref")
    decision_ref = _require_object_ref(req.decision_ref, IdKind.DECISION, "decision_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    child_kind = _bounded(req.child_kind, "child_kind")
    child_sub_kind = _bounded(req.child_sub_kind, "child_sub_kind")

    _resolve_subject(store, parent_ref)
    decision = _resolve_decision(store, decision_ref)

    if decision.subject_ref != parent_ref.internal_id:
        raise TransitionDeniedError(
            f"foreign decision {decision_ref.serialize()} does not belong to parent {parent_ref.serialize()}",
            details={"code": "FOLLOWUP_FOREIGN_DECISION_DENIED"},
        )

    evidence = MaterializedDecisionEvidence.from_decision(
        decision,
        operation="create_followup_subject",
        target=parent_ref,
        expected_revision=req.expected_revision,
        scope=dict(WRITE_SCOPE),
    )

    child_iid = make_id(
        IdKind.SUBJECT,
        mint_subject_value(child_sub_kind, "wid"),
        sub_kind=child_sub_kind,
    )
    edge_id = _mint_id(IdKind.EDGE, _MINTED_ID_PREFIXES[IdKind.EDGE])

    fingerprint = canonical_fingerprint(
        {
            "operation": "create_followup_subject",
            "parent": parent_ref.serialize(),
            "decision": decision_ref.serialize(),
            "expected_revision": req.expected_revision,
            "child_sub_kind": child_sub_kind,
            "rationale": req.rationale,
        }
    )
    tx = ParentChildTransaction(
        store,
        parent_ref=parent_ref,
        expected_parent_revision=req.expected_revision,
        operation="create_followup_subject",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        child_kind=child_kind,
        child_iid=child_iid,
        source_decision=decision,
        decision_evidence=evidence,
        edge_id=edge_id,
        child_creation_context=dict(req.child_creation_context or {}),
        child_id_derivation=req.child_id_derivation or "minted",
        rationale=req.rationale,
    ).begin()
    tx.stage_child().stage_edge()
    result = tx.commit()
    return _result("create_followup_subject", parent_ref, result, (child_iid.to_canonical(),))


def _project_binding_gate(req: PlanInitRequest) -> tuple[ProjectBinding | None, LifecycleResult | None]:
    evidence = req.project_evidence
    if evidence is None:
        return None, _lifecycle_error(
            "plan_init",
            "PROJECT_BINDING_REQUIRED",
            "an exact Project Binding and unique relationship evidence are required",
            correlation_id=req.intent.correlation_id,
        )
    status = getattr(evidence, "status", None)
    candidates = tuple(getattr(evidence, "candidates", ()))
    if status == "PROJECT_NOT_FOUND" or not candidates:
        return None, _lifecycle_error(
            "plan_init",
            "PROJECT_NOT_FOUND",
            "relationship evidence contains no canonical Project candidate",
            correlation_id=req.intent.correlation_id,
        )
    if status == "NEEDS_SEMANTIC_CHOICE" or len(candidates) > 1:
        choices = tuple({"candidate": candidate.to_dict()} for candidate in candidates)
        return None, _lifecycle_choice(
            "plan_init",
            "NEEDS_SEMANTIC_CHOICE",
            choices,
            correlation_id=req.intent.correlation_id,
        )
    if req.project_binding is None:
        return None, _lifecycle_error(
            "plan_init",
            "PROJECT_BINDING_REQUIRED",
            "relationship evidence is not a semantic Project Binding",
            correlation_id=req.intent.correlation_id,
        )
    requested_project_id = req.intent.semantic_inputs.get("project_id")
    if requested_project_id is not None and requested_project_id != req.project_binding.project_id:
        return None, _lifecycle_error(
            "plan_init",
            "PROJECT_BINDING_REQUIRED",
            "Project Binding does not match the decided semantic project",
            correlation_id=req.intent.correlation_id,
        )
    try:
        return prepare_project_binding(evidence, req.project_binding), None
    except ValueError as exc:
        code = str(exc)
        if code not in {"PROJECT_NOT_FOUND", "NEEDS_SEMANTIC_CHOICE", "PROJECT_BINDING_REQUIRED"}:
            code = "PROJECT_BINDING_REQUIRED"
        if code == "NEEDS_SEMANTIC_CHOICE":
            choices = tuple({"candidate": candidate.to_dict()} for candidate in candidates)
            return None, _lifecycle_choice(
                "plan_init",
                code,
                choices,
                correlation_id=req.intent.correlation_id,
            )
        return None, _lifecycle_error(
            "plan_init",
            code,
            "exact Project Binding validation failed",
            correlation_id=req.intent.correlation_id,
        )


def plan_init(store: TransactionStore, req: PlanInitRequest) -> LifecycleResult:
    """Apply the bounded mechanical ``uninitialized -> initialized`` transition."""
    store = _require_store(store)
    try:
        plan_ref = _require_subject_ref(req.plan_ref, "plan_ref")
        intent = _require_lifecycle_intent(req.intent, operation="plan_init", target=plan_ref)
    except (AttributeError, TypeError, ValueError, TransitionError) as exc:
        return _lifecycle_error("plan_init", "PLAN_INIT_INVALID_PREDECESSOR", str(exc))
    principal = _principal_id(req.trusted_context)
    if principal is None:
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_AUTHORIZATION_REQUIRED",
            "a runtime-bound trusted principal is required",
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    identity_result = _request_identity_result(
        store,
        operation="plan_init",
        target=plan_ref,
        intent=intent,
        principal=principal,
        replay_code="PLAN_INIT_REPLAYED",
        correlation_id=intent.correlation_id,
    )
    if identity_result is not None:
        return identity_result
    try:
        subject = _resolve_subject(store, plan_ref)
    except TransitionNotFoundError as exc:
        return _lifecycle_error("plan_init", "PLAN_INIT_INVALID_PREDECESSOR", str(exc), correlation_id=intent.correlation_id)
    if not _is_plan_subject(subject):
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_INVALID_PREDECESSOR",
            "PLAN_INIT target is not a Plan Subject",
            correlation_id=intent.correlation_id,
        )
    precondition_code = _precondition_failure(
        subject,
        req.preconditions,
        authority_code="PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
        subject_code="PLAN_INIT_STALE_SUBJECT_REVISION",
    )
    if precondition_code is not None:
        return _lifecycle_error(
            "plan_init",
            precondition_code,
            "PLAN_INIT precondition failed closed",
            correlation_id=intent.correlation_id,
        )

    state = dict(subject.mechanical_state)
    state_value = _plan_state(subject)
    if state_value in {"initialized", "initialized_unbound", "initialized_bound"}:
        phase = (
            "post_binding"
            if state_value == "initialized_bound"
            or state.get("binding_state") == "bound"
            or state.get("project_binding")
            else "pre_binding"
        )
        code = "PLAN_INIT_ALREADY_INITIALIZED"
        result = _lifecycle_error(
            "plan_init",
            code,
            "PLAN_INIT re-entry is denied and has no new semantic effect",
            correlation_id=intent.correlation_id,
            data={
                "reentry_phase": phase,
                "revision_after": revision_number_of(subject),
                "reentry_denied": True,
            },
        )
        store.record_lifecycle_no_effect(
            key=intent.idempotency_key,
            operation="plan_init",
            target=plan_ref,
            principal=principal,
            fingerprint=intent.intent_fingerprint(),
            result_code=code,
        )
        return result
    if state_value != "uninitialized":
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_INVALID_PREDECESSOR",
            f"PLAN_INIT requires uninitialized predecessor, got {state_value}",
            correlation_id=intent.correlation_id,
        )

    binding, binding_result = _project_binding_gate(req)
    if binding_result is not None:
        return binding_result
    if not isinstance(binding, ProjectBinding):
        return _lifecycle_error(
            "plan_init",
            "PROJECT_BINDING_REQUIRED",
            "exact Project Binding is required",
            correlation_id=intent.correlation_id,
        )
    if not isinstance(req.lease, CapabilityLease):
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_AUTHORIZATION_REQUIRED",
            "M4-4 consumes an operation lease but does not issue one",
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )

    state_patch = {
        "state": "initialized",
        "binding_state": "bound",
        "project_binding": binding.to_dict(),
        "authority_source_revision": req.preconditions.authority_source_revision,
        "authority_observed_raw_digest": req.preconditions.authority_observed_raw_digest,
    }
    try:
        tx = SubjectTransaction(
            store,
            subject_ref=plan_ref,
            expected_revision=req.preconditions.subject_expected_revision,
            operation="plan_init",
            trusted_context=req.trusted_context,
            requested_scope=dict(WRITE_SCOPE),
            lease=req.lease,
            idempotency_key=intent.idempotency_key,
            fingerprint=intent.intent_fingerprint(),
            trusted_time=req.trusted_time,
            new_state={"lifecycle": "plan_init", "state_patch": state_patch},
            state_patch=state_patch,
        ).begin()
        result = tx.commit()
    except StaleRevisionError:
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_STALE_SUBJECT_REVISION",
            "PLAN_INIT CAS became stale before commit",
            correlation_id=intent.correlation_id,
        )
    except IdempotencyConflictError as exc:
        return _lifecycle_error(
            "plan_init",
            "CONFLICT",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.CONFLICT,
        )
    except LeaseReplayDeniedError as exc:
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_AUTHORIZATION_REPLAY",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    except AuthorityDeniedError as exc:
        return _lifecycle_error(
            "plan_init",
            "PLAN_INIT_AUTHORIZATION_DENIED",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    return lifecycle_result(
        "plan_init",
        "PLAN_INIT_APPLIED",
        MutationEffect.APPLIED_VERIFIED,
        correlation_id=intent.correlation_id,
        data={
            "plan_ref": plan_ref.serialize(),
            "predecessor_state": "uninitialized",
            "successor_state": "initialized",
            "binding_state": "bound",
            "revision_after": result.revision_after,
            "revision_token": result.revision_token,
            "external_authority_write": False,
        },
    )


def capture_retirement_snapshot(
    store: TransactionStore,
    plan_ref: ObjectRef,
) -> RetirementCandidateSnapshot:
    """Capture stage-one evidence for one exact Plan without mutation."""
    store = _require_store(store)
    plan_ref = _require_subject_ref(plan_ref, "plan_ref")
    return _capture_retirement_snapshot(store, plan_ref)


def resolve_retirement_candidates(
    store: TransactionStore,
    candidate_refs: tuple[ObjectRef, ...] | list[ObjectRef] | None = None,
) -> RetirementCandidateResolution:
    """Resolve zero/one/many eligible retirement candidates deterministically."""
    store = _require_store(store)
    refs: list[ObjectRef] = []
    if candidate_refs is None:
        refs = [
            make_object_ref(IdKind.SUBJECT, subject.subject_id)
            for subject in store.subjects()
            if _is_plan_subject(subject)
        ]
    else:
        for ref in candidate_refs:
            refs.append(_require_subject_ref(ref, "candidate_ref"))
    seen: set[str] = set()
    snapshots: list[RetirementCandidateSnapshot] = []
    for ref in refs:
        if ref.serialize() in seen:
            continue
        seen.add(ref.serialize())
        try:
            snapshot = _capture_retirement_snapshot(store, ref)
        except (TransitionError, LookupError):
            continue
        if snapshot.eligible_retirement_kinds:
            snapshots.append(snapshot)
    candidates = tuple(snapshots)
    if not candidates:
        result = _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_NO_CANDIDATE",
            "no eligible Plan retirement candidate exists",
        )
    elif len(candidates) == 1:
        result = lifecycle_result(
            "plan_retirement",
            "RETIREMENT_CANDIDATE_UNIQUE",
            MutationEffect.NO_EFFECT,
            data={"candidate": candidates[0].to_dict(), "mutation": False},
        )
    else:
        choices = tuple(
            {
                "candidate_ref": candidate.plan_ref,
                "snapshot_identity": candidate.snapshot_identity,
            }
            for candidate in candidates
        )
        result = _lifecycle_choice(
            "plan_retirement",
            "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
            choices,
            data={"candidate_snapshots": [candidate.to_dict() for candidate in candidates]},
        )
    return RetirementCandidateResolution(result=result, candidates=candidates)


def retire_plan(store: TransactionStore, req: PlanRetirementRequest) -> LifecycleResult:
    """Apply one exact retirement decision from a fresh bounded snapshot."""
    store = _require_store(store)
    try:
        plan_ref = _require_subject_ref(req.plan_ref, "plan_ref")
        intent = _require_lifecycle_intent(req.intent, operation="plan_retirement", target=plan_ref)
    except (AttributeError, TypeError, ValueError, TransitionError) as exc:
        return _lifecycle_error("plan_retirement", "RETIREMENT_STALE_SNAPSHOT", str(exc))
    principal = _principal_id(req.trusted_context)
    if principal is None:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_AUTHORIZATION_REQUIRED",
            "a runtime-bound trusted principal is required",
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    identity_result = _request_identity_result(
        store,
        operation="plan_retirement",
        target=plan_ref,
        intent=intent,
        principal=principal,
        replay_code="RETIREMENT_REPLAYED",
        correlation_id=intent.correlation_id,
    )
    if identity_result is not None:
        return identity_result
    snapshot = req.snapshot
    if not isinstance(snapshot, RetirementCandidateSnapshot) or snapshot.plan_ref != plan_ref.serialize():
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_STALE_SNAPSHOT",
            "retirement request is not bound to the selected snapshot",
            correlation_id=intent.correlation_id,
        )
    if _snapshot_identity(snapshot) != snapshot.snapshot_identity:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_STALE_SNAPSHOT",
            "retirement snapshot identity is invalid",
            correlation_id=intent.correlation_id,
        )
    try:
        subject = _resolve_subject(store, plan_ref)
        current_snapshot = _capture_retirement_snapshot(store, plan_ref)
    except (TransitionError, LookupError) as exc:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_STALE_SNAPSHOT",
            str(exc),
            correlation_id=intent.correlation_id,
        )
    if not _snapshot_matches_current(snapshot, current_snapshot):
        authority_changed = (
            snapshot.authority_source_revision != current_snapshot.authority_source_revision
            or snapshot.authority_observed_raw_digest != current_snapshot.authority_observed_raw_digest
        )
        code = "RETIREMENT_STALE_AUTHORITY_PRECONDITION" if authority_changed else "RETIREMENT_STALE_SNAPSHOT"
        return _lifecycle_error(
            "plan_retirement",
            code,
            "retirement snapshot is stale; no snapshot refresh is permitted",
            correlation_id=intent.correlation_id,
        )
    if (
        not isinstance(req.preconditions, MutationPreconditions)
        or req.preconditions.authority_source_revision != snapshot.authority_source_revision
        or req.preconditions.authority_observed_raw_digest != snapshot.authority_observed_raw_digest
    ):
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
            "retirement authority precondition does not match the selected snapshot",
            correlation_id=intent.correlation_id,
        )
    precondition_code = _precondition_failure(
        subject,
        req.preconditions,
        expected_subject_revision=snapshot.source_revision,
        authority_code="RETIREMENT_STALE_AUTHORITY_PRECONDITION",
        subject_code="RETIREMENT_STALE_SNAPSHOT",
    )
    if precondition_code is not None:
        return _lifecycle_error(
            "plan_retirement",
            precondition_code,
            "retirement precondition failed closed",
            correlation_id=intent.correlation_id,
        )
    if current_snapshot.active_current_protection:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_TARGET_PROTECTED",
            "active/current Plan retirement is protected",
            correlation_id=intent.correlation_id,
        )
    if current_snapshot.running_task_protection:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_RUNNING_TASK_PROTECTED",
            "Plan with a running task is protected",
            correlation_id=intent.correlation_id,
        )
    if not current_snapshot.eligible_retirement_kinds:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_TARGET_PROTECTED",
            "target is no longer eligible for retirement",
            correlation_id=intent.correlation_id,
        )
    if req.retirement_kind not in {"abandoned", "superseded"}:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_KIND_INVALID",
            "retirement_kind must be abandoned or superseded",
            correlation_id=intent.correlation_id,
        )
    successor_ref = req.successor_ref
    if req.retirement_kind == "superseded":
        if successor_ref is None:
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_SUCCESSOR_REQUIRED",
                "superseded retirement requires one exact successor_ref",
                correlation_id=intent.correlation_id,
            )
        if successor_ref == plan_ref:
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_SELF_SUCCESSOR",
                "successor_ref must not equal the retired Plan",
                correlation_id=intent.correlation_id,
            )
        if not isinstance(successor_ref, ObjectRef) or successor_ref.object_kind != IdKind.SUBJECT:
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_SUCCESSOR_INVALID",
                "successor_ref must be an exact canonical Plan Subject reference",
                correlation_id=intent.correlation_id,
            )
        try:
            successor = store.read_subject(successor_ref)
        except LookupError:
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_SUCCESSOR_INVALID",
                "successor_ref does not resolve to a canonical Plan",
                correlation_id=intent.correlation_id,
            )
        if not _is_plan_subject(successor) or _is_retired_state(successor.mechanical_state):
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_SUCCESSOR_INVALID",
                "successor_ref is not a valid canonical successor",
                correlation_id=intent.correlation_id,
            )
        successor_value = successor_ref.serialize()
        if successor_value not in snapshot.successor_eligibility:
            return _lifecycle_error(
                "plan_retirement",
                "RETIREMENT_STALE_SNAPSHOT",
                "successor validity was not present in the selected snapshot",
                correlation_id=intent.correlation_id,
            )
    if not isinstance(req.lease, CapabilityLease):
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_AUTHORIZATION_REQUIRED",
            "M4-4 consumes an operation lease but does not issue one",
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )

    state_patch = {
        "state": "cancelled" if req.retirement_kind == "abandoned" else "superseded",
        "retirement_kind": req.retirement_kind,
        "retired_at": req.trusted_time.isoformat(),
    }
    if successor_ref is not None:
        state_patch["successor_ref"] = successor_ref.serialize()
    try:
        tx = SubjectTransaction(
            store,
            subject_ref=plan_ref,
            expected_revision=snapshot.source_revision,
            operation="plan_retirement",
            trusted_context=req.trusted_context,
            requested_scope=dict(WRITE_SCOPE),
            lease=req.lease,
            idempotency_key=intent.idempotency_key,
            fingerprint=intent.intent_fingerprint(),
            trusted_time=req.trusted_time,
            new_state={"lifecycle": "plan_retirement", "state_patch": state_patch},
            state_patch=state_patch,
        ).begin()
        result = tx.commit()
    except StaleRevisionError:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_STALE_SNAPSHOT",
            "retirement CAS became stale before commit",
            correlation_id=intent.correlation_id,
        )
    except IdempotencyConflictError as exc:
        return _lifecycle_error(
            "plan_retirement",
            "CONFLICT",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.CONFLICT,
        )
    except LeaseReplayDeniedError as exc:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_AUTHORIZATION_REPLAY",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    except AuthorityDeniedError as exc:
        return _lifecycle_error(
            "plan_retirement",
            "RETIREMENT_AUTHORIZATION_DENIED",
            str(exc),
            correlation_id=intent.correlation_id,
            effect=MutationEffect.BLOCKED,
        )
    return lifecycle_result(
        "plan_retirement",
        "RETIREMENT_APPLIED",
        MutationEffect.APPLIED_VERIFIED,
        correlation_id=intent.correlation_id,
        data={
            "plan_ref": plan_ref.serialize(),
            "retirement_kind": req.retirement_kind,
            "successor_ref": successor_ref.serialize() if successor_ref is not None else None,
            "resulting_plan_state": state_patch["state"],
            "historical_state_preserved": True,
            "external_authority_write": False,
            "revision_after": result.revision_after,
            "revision_token": result.revision_token,
        },
    )


__all__ = [
    "CreateExecutionRequest",
    "RecordCompletionRequest",
    "RecordDecisionRequest",
    "CreateFollowupSubjectRequest",
    "PlanInitRequest",
    "RetirementCandidateSnapshot",
    "RetirementCandidateResolution",
    "PlanRetirementRequest",
    "TransitionResult",
    "LifecycleResult",
    "TransitionError",
    "TransitionInvalidError",
    "TransitionDeniedError",
    "TransitionNotFoundError",
    "create_execution",
    "record_completion",
    "record_decision",
    "create_followup_subject",
    "plan_init",
    "capture_retirement_snapshot",
    "resolve_retirement_candidates",
    "retire_plan",
]
