"""M3-B6 executor-neutral atomic transaction / concurrency foundation (Issue #9).

Implements the accepted M3-A3 transaction boundary over the B3/B4/B5
foundations.  This is the mechanical mutation foundation B7 will later use; it
does NOT implement any public semantic transition (create_execution /
record_completion / record_decision / create_followup_subject are B7).

``TransactionStore`` is an ISOLATED, NON-AUTHORITATIVE in-memory transactional
repository (``B6_TEST_STORAGE_IS_AUTHORITY=no``,
``PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no``).  It provides:

* per-Subject aggregate bounded concurrency (no global lock):
  ``INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK=no``,
  ``INDEPENDENT_SUBJECT_CONCURRENCY_ALLOWED=yes``.
* CAS check before commit with fail-closed stale mutation.
* atomic commit: graph mutation + revision advance + lease consumption +
  idempotency success record are one atomic unit; a failed transaction commits
  none of them (``FAILED_TX_* = no``).
* one-time capability lease consumption inside the transaction boundary
  (``LEASE_CONSUMED_ON_FAILED_TRANSACTION=no``,
  ``REPLAY_OF_CONSUMED_LEASE_DENIED=yes``).
* ID Broker transaction integration: a candidate may be staged, accepted on
  commit, and retired on failure (``DURABLE_NO_REUSE_TRANSACTION_INTEGRATED=
  yes``; ``FAILED_MINT_REUSE_ALLOWED=no``; ``COLLISION_FAIL_CLOSED=yes``).
* a generic existing-Subject mutation transaction.
* a generic parent + new-child transaction foundation (FROZEN R1 capability)
  without exposing B7 ``create_followup_subject`` semantics
  (``B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED=no``,
  ``CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED=no``).

``TRANSACTION_SOURCE_IMPLEMENTED=yes``; ``TRANSACTION_RUNTIME_AUTHORITY_ACTIVE=
no``; ``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading

from aota_forge.core.authority import (
    AuthorityDecision,
    AuthorityEngine,
    AuthorityRequest,
)
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
from aota_forge.core.identity.errors import CollisionError, IdReuseError as B4IdReuseError
from aota_forge.core.identity.ids import InternalId
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
    IDEMPOTENCY_STATE_COMMITTED,
)
from aota_forge.core.revision import (
    AuthorityDeniedError,
    IdCollisionError,
    IdempotencyConflictError,
    IdReuseError,
    LeaseReplayDeniedError,
    StaleRevisionError,
    SubjectNotFoundError,
    SubjectRevision,
    TransactionNotCommittedError,
    advance_revision,
    initial_revision,
    revision_number_of,
    set_revision_number,
)

TRANSACTION_RUNTIME_AUTHORITY_ACTIVE = False
PRODUCTION_GRAPH_AUTHORITY_ACTIVE = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
B6_TEST_STORAGE_IS_AUTHORITY = False
PARTIAL_COMMIT_ALLOWED = False
FAILED_MINT_REUSE_ALLOWED = False
CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED = False
B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED = False
INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK = False
SAME_KEY_DIFFERENT_REQUEST = "conflict"


@dataclass(frozen=True)
class TransactionResult:
    """Bounded committed/replayed outcome of one transaction."""

    outcome: str
    idempotency_key: str
    subject_ref: str
    revision_after: int | None = None
    revision_token: str | None = None
    effect_refs: tuple = ()
    replayed: bool = False


def _bounded_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


class TransactionStore(InMemoryGraphRepository):
    """Isolated, non-authoritative transactional graph store (B6 fixture scope).

    Extends the B3 in-memory repository with per-Subject locking, a revision
    registry, an atomic lease-consumption registry, an idempotency registry,
    and a retired-ID set for durable no-reuse integration.  Direct
    non-transactional ``store`` writes are forbidden; all mutation flows
    through a transaction.
    """

    B6_TEST_STORAGE_IS_AUTHORITY = False
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE = False
    INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK = False
    GLOBAL_LOCK_IS_CANONICAL = False

    def __init__(self, *, broker=None, enforce_referential: bool = True) -> None:
        super().__init__(enforce_referential=enforce_referential)
        self._broker = broker
        self._revision_tokens: dict[str, str] = {}
        self._consumed_leases: dict[str, dict] = {}
        self._idempotency = IdempotencyStore()
        self._retired_ids: set[str] = set()
        self._accepted_ids: set[str] = set()
        self._locks: dict[str, threading.RLock] = {}
        self._registry_guard = threading.Lock()
        self._authority = AuthorityEngine(OwningSubjectResolver(self))

    # -- write path: direct writes are forbidden; use transactions ----------

    def store(self, record) -> None:
        raise TypeError("direct non-transactional writes are forbidden; use a transaction")

    def _put_staged(self, record) -> None:
        """Apply one staged record into the store (transaction commit path)."""
        self._put(record)

    # -- locking -------------------------------------------------------------

    def _subject_lock(self, subject_value: str) -> threading.RLock:
        with self._registry_guard:
            lock = self._locks.get(subject_value)
            if lock is None:
                lock = threading.RLock()
                self._locks[subject_value] = lock
            return lock

    @property
    def per_subject_lock_count(self) -> int:
        return len(self._locks)

    # -- reads ---------------------------------------------------------------

    def read_subject(self, ref: ObjectRef) -> records.Subject:
        from aota_forge.core.graph.repository import assert_object_ref_kind

        assert_object_ref_kind(ref, IdKind.SUBJECT, "subject")
        try:
            return self.subject(ref)
        except Exception as exc:  # noqa: BLE001
            from aota_forge.core.graph.repository import GraphNotFoundError

            if isinstance(exc, GraphNotFoundError):
                raise SubjectNotFoundError(f"owning subject not found: {ref.serialize()}") from exc
            raise

    def current_revision(self, ref: ObjectRef) -> SubjectRevision:
        subject = self.read_subject(ref)
        number = revision_number_of(subject)
        token = self._revision_tokens.get(ref.internal_id.value)
        if token is None:
            token = initial_revision(ref.internal_id, token_seed=ref.internal_id.value).revision_token
        return SubjectRevision(ref.internal_id, number, token)

    def is_lease_consumed(self, lease_id: str) -> bool:
        return lease_id in self._consumed_leases

    def idempotency_lookup(self, key: str) -> IdempotencyRecord | None:
        return self._idempotency.get(key)

    # -- broker integration --------------------------------------------------

    def _retire_staged_candidate(self, iid: InternalId) -> None:
        """Retire a staged/created ID so it is never reused (no silent reuse)."""
        key = self._broker_key(iid)
        self._retired_ids.add(key)
        if self._broker is not None:
            try:
                self._broker.retire(iid, reason="failed transaction no-reuse")
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _broker_key(iid: InternalId) -> str:
        return f"{iid.kind}::{iid.sub_kind}::{iid.value}"

    def is_retired(self, iid: InternalId) -> bool:
        return self._broker_key(iid) in self._retired_ids

    def _guard_not_retired(self, iid: InternalId) -> None:
        if self.is_retired(iid):
            raise IdReuseError(f"retired/failed minted id reused: {iid.to_canonical()}")

    def _accept_allocation(self, iid: InternalId) -> None:
        key = self._broker_key(iid)
        if key in self._retired_ids:
            raise IdReuseError(f"retired id cannot be accepted: {iid.to_canonical()}")
        if key in self._accepted_ids:
            raise IdCollisionError(f"id already accepted: {iid.to_canonical()}")
        self._accepted_ids.add(key)
        if self._broker is not None:
            try:
                self._broker.allocate(iid, reason="committed allocation")
            except B4IdReuseError:
                raise IdReuseError(f"retired id reused: {iid.to_canonical()}") from None
            except CollisionError:
                raise IdCollisionError(f"id collision: {iid.to_canonical()}") from None

    # -- commit atomics ------------------------------------------------------

    def _snapshot(self) -> dict:
        return {
            "workflows": dict(self._workflows),
            "subjects": dict(self._subjects),
            "executions": dict(self._executions),
            "completions": dict(self._completions),
            "decisions": dict(self._decisions),
            "edges": dict(self._edges),
            "revision_tokens": dict(self._revision_tokens),
            "consumed_leases": dict(self._consumed_leases),
            "idempotency": {r.idempotency_key: r for r in self._idempotency},
            "retired": set(self._retired_ids),
            "accepted": set(self._accepted_ids),
        }

    def _restore(self, snap: dict) -> None:
        self._workflows = snap["workflows"]
        self._subjects = snap["subjects"]
        self._executions = snap["executions"]
        self._completions = snap["completions"]
        self._decisions = snap["decisions"]
        self._edges = snap["edges"]
        self._revision_tokens = snap["revision_tokens"]
        self._consumed_leases = snap["consumed_leases"]
        self._idempotency = IdempotencyStore()
        for rec in snap["idempotency"].values():
            self._idempotency.put(rec)
        self._retired_ids = snap["retired"]
        self._accepted_ids = snap["accepted"]


class _TransactionBase:
    """Shared begin/commit/rollback mechanics over the store."""

    def __init__(self, store: TransactionStore, *, subject_ref: ObjectRef) -> None:
        self._store = store
        self._subject_ref = subject_ref
        self._lock = None
        self._staged: list = []
        self._active = False
        self._consumed_lease_id: str | None = None
        self._idem: IdempotencyRecord | None = None
        self._staged_ids: list[InternalId] = []
        self._result: TransactionResult | None = None
        self._replayed = False

    def _acquire(self) -> None:
        self._lock = self._store._subject_lock(self._subject_ref.internal_id.value)
        self._lock.acquire()

    def _release(self) -> None:
        if self._lock is not None and self._lock._is_owned():
            try:
                self._lock.release()
            except RuntimeError:
                pass
        self._lock = None
        self._active = False

    def add_record(self, record) -> None:
        if not self._active:
            raise TransactionNotCommittedError("transaction is not active; cannot stage a record")
        self._staged.append(record)

    def add_staged_id(self, iid: InternalId) -> None:
        self._staged_ids.append(iid)

    def stage_lease_consumption(self, lease_id: str) -> None:
        self._consumed_lease_id = lease_id

    def _check_idempotency(self, key: str, fingerprint: str, expected_state: str) -> None:
        existing = self._store.idempotency_lookup(key)
        if existing is None:
            return
        if existing.state == IDEMPOTENCY_STATE_COMMITTED:
            if existing.fingerprint == fingerprint:
                # Replay of an identical committed request: reuse the result.
                self._replayed = True
                return
            raise IdempotencyConflictError(
                f"idempotency key reused with a different request: {key}",
                details={"key": key},
            )
        # A failed slot (never a committed effect) does not poison the key;
        # the retry proceeds (bounded state model).
        return

    def commit(self) -> TransactionResult:
        if self._replayed:
            return self._replay_result()
        if not self._active:
            raise TransactionNotCommittedError("transaction is not active")
        snap = self._store._snapshot()
        try:
            for rec in self._staged:
                self._store._put_staged(rec)
                self._store._check_structural(rec)
            self._commit_apply()
            for iid in self._staged_ids:
                self._store._accept_allocation(iid)
            if self._consumed_lease_id is not None:
                self._store._consumed_leases[self._consumed_lease_id] = {
                    "subject_ref": self._subject_ref.serialize(),
                }
            self._result = self._build_result()
            if self._idem is not None:
                self._store._idempotency.put(self._idem)
        except Exception:
            self._store._restore(snap)
            for iid in self._staged_ids:
                self._store._retire_staged_candidate(iid)
            self._release()
            raise
        self._release()
        return self._result

    def rollback(self) -> None:
        if self._active:
            for iid in self._staged_ids:
                self._store._retire_staged_candidate(iid)
            self._staged = []
            self._staged_ids = []
            self._release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
            return False
        return False

    # -- subclasses implement these -----------------------------------------

    def _commit_apply(self) -> None:
        raise NotImplementedError

    def _replay_result(self) -> TransactionResult:
        raise NotImplementedError

    def _build_result(self) -> TransactionResult:
        raise NotImplementedError


class SubjectTransaction(_TransactionBase):
    """Generic single-existing-Subject mutation transaction (B6 foundation).

    B7 supplies the operation-specific payload/choreography; this transaction
    provides the mechanical commit boundary for one Subject aggregate.
    """

    def __init__(
        self,
        store: TransactionStore,
        *,
        subject_ref: ObjectRef,
        expected_revision: int,
        operation: str,
        trusted_context,
        requested_scope: dict,
        lease,
        idempotency_key: str,
        fingerprint: str,
        trusted_time,
        new_state: dict,
    ) -> None:
        super().__init__(store, subject_ref=subject_ref)
        self._expected_revision = expected_revision
        self._operation = _bounded_id(operation, "operation")
        self._trusted_context = trusted_context
        self._requested_scope = dict(requested_scope)
        self._lease = lease
        self._idempotency_key = _bounded_id(idempotency_key, "idempotency_key")
        self._fingerprint = fingerprint
        self._trusted_time = trusted_time
        self._new_state = dict(new_state)
        self._new_revision = None
        self._consumed_lease_id = lease.lease_id

    def begin(self) -> "SubjectTransaction":
        self._acquire()
        try:
            self._check_idempotency(self._idempotency_key, self._fingerprint, IDEMPOTENCY_STATE_COMMITTED)
            if self._replayed:
                self._release()
                return self
            subject = self._store.read_subject(self._subject_ref)
            current = revision_number_of(subject)
            if current != self._expected_revision:
                self._release()
                raise StaleRevisionError(
                    f"expected revision {self._expected_revision} != current {current}",
                    details={"subject": self._subject_ref.serialize()},
                )
            if self._store.is_lease_consumed(self._lease.lease_id):
                self._release()
                raise LeaseReplayDeniedError(f"one-time lease already consumed: {self._lease.lease_id}")
            result = self._store._authority.evaluate(
                AuthorityRequest(
                    principal=self._trusted_context.principal,
                    trusted_context=self._trusted_context,
                    operation=self._operation,
                    target=self._subject_ref,
                    requested_scope=self._requested_scope,
                    lease=self._lease,
                    current_revision=current,
                    trusted_time=self._trusted_time,
                )
            )
            if result.decision != AuthorityDecision.ALLOW:
                self._release()
                raise AuthorityDeniedError(
                    f"authority denied: {result.reason_code.value}",
                    details=dict(result.to_audit()),
                )
            self._active = True
            return self
        except Exception:
            self._release()
            raise

    def _commit_apply(self) -> None:
        current = self._store.current_revision(self._subject_ref)
        next_number = current.revision_number + 1
        subject = self._store._subjects[self._subject_ref.internal_id.value]
        next_subject = set_revision_number(subject, next_number)
        self._store._put_staged(next_subject)
        new_rev = advance_revision(
            current,
            aggregate_snapshot=self._new_state,
        )
        self._store._revision_tokens[self._subject_ref.internal_id.value] = new_rev.revision_token
        self._new_revision = new_rev

    def _replay_result(self) -> TransactionResult:
        existing = self._store.idempotency_lookup(self._idempotency_key)
        return TransactionResult(
            outcome="COMMITTED",
            idempotency_key=self._idempotency_key,
            subject_ref=self._subject_ref.serialize(),
            revision_after=existing.revision_after if existing else None,
            revision_token=existing.revision_token if existing else None,
            effect_refs=existing.effect_refs if existing else (),
            replayed=True,
        )

    def _build_result(self) -> TransactionResult:
        self._idem = IdempotencyRecord(
            idempotency_key=self._idempotency_key,
            operation=self._operation,
            target=self._subject_ref.serialize(),
            principal=self._trusted_context.principal.id,
            fingerprint=self._fingerprint,
            state=IDEMPOTENCY_STATE_COMMITTED,
            effect_refs=(),
            revision_after=self._new_revision.revision_number,
            revision_token=self._new_revision.revision_token,
        )
        return TransactionResult(
            outcome="COMMITTED",
            idempotency_key=self._idempotency_key,
            subject_ref=self._subject_ref.serialize(),
            revision_after=self._new_revision.revision_number,
            revision_token=self._new_revision.revision_token,
            effect_refs=(),
            replayed=False,
        )


class ParentChildTransaction(_TransactionBase):
    """FROZEN R1 parent + new-child atomic transaction FOUNDATION.

    Provides the generic primitive required by a later ``create_followup_subject``
    (B7): verify parent expected revision, verify a durable Decision,
    validate the parent-target lease, mint a child ID inside the transaction,
    stage the child at revision 1, stage the parent-child edge, advance the
    parent revision, consume the parent lease, persist idempotency, and commit
    atomically.  It does NOT expose B7 ``create_followup_subject`` semantics.
    """

    def __init__(
        self,
        store: TransactionStore,
        *,
        parent_ref: ObjectRef,
        expected_parent_revision: int,
        operation: str,
        trusted_context,
        requested_scope: dict,
        lease,
        idempotency_key: str,
        fingerprint: str,
        trusted_time,
        child_kind: str,
        child_iid: InternalId,
        source_decision: records.Decision,
        decision_evidence,
        edge_id: InternalId,
        child_creation_context: dict,
        child_id_derivation: str,
        rationale: str | None = None,
    ) -> None:
        super().__init__(store, subject_ref=parent_ref)
        self._expected_parent_revision = expected_parent_revision
        self._operation = _bounded_id(operation, "operation")
        self._trusted_context = trusted_context
        self._requested_scope = dict(requested_scope)
        self._lease = lease
        self._idempotency_key = _bounded_id(idempotency_key, "idempotency_key")
        self._fingerprint = fingerprint
        self._trusted_time = trusted_time
        self._child_kind = child_kind
        self._child_iid = child_iid
        self._source_decision = source_decision
        self._decision_evidence = decision_evidence
        self._edge_id = edge_id
        self._child_creation_context = dict(child_creation_context)
        self._child_id_derivation = child_id_derivation
        self._rationale = rationale
        self._new_parent_revision = None
        self._child_subject_ref = make_object_ref(IdKind.SUBJECT, child_iid)
        self._consumed_lease_id = lease.lease_id

    def begin(self) -> "ParentChildTransaction":
        self._acquire()
        try:
            self._check_idempotency(self._idempotency_key, self._fingerprint, IDEMPOTENCY_STATE_COMMITTED)
            if self._replayed:
                self._release()
                return self
            parent = self._store.read_subject(self._subject_ref)
            current = revision_number_of(parent)
            if current != self._expected_parent_revision:
                self._release()
                raise StaleRevisionError(
                    f"expected parent revision {self._expected_parent_revision} != current {current}"
                )
            if self._store.is_lease_consumed(self._lease.lease_id):
                self._release()
                raise LeaseReplayDeniedError(f"one-time parent lease already consumed: {self._lease.lease_id}")
            result = self._store._authority.evaluate(
                AuthorityRequest(
                    principal=self._trusted_context.principal,
                    trusted_context=self._trusted_context,
                    operation=self._operation,
                    target=self._subject_ref,
                    requested_scope=self._requested_scope,
                    lease=self._lease,
                    current_revision=current,
                    trusted_time=self._trusted_time,
                    decision=self._decision_evidence,
                )
            )
            if result.decision != AuthorityDecision.ALLOW:
                self._release()
                raise AuthorityDeniedError(
                    f"authority denied: {result.reason_code.value}",
                    details=dict(result.to_audit()),
                )
            self._store._guard_not_retired(self._child_iid)
            self._active = True
            return self
        except Exception:
            self._release()
            raise

    def stage_child(self) -> "ParentChildTransaction":
        if self._replayed:
            return self
        if not self._active:
            raise TransactionNotCommittedError("transaction is not active")
        child = records.subject(
            self._child_iid,
            self._child_kind,
            workflow_ref=parent_workflow_ref(self._store, self._subject_ref),
            mechanical_state={"revision": 1, "state": "open"},
            id_derivation=self._child_id_derivation,
            creation_context=self._child_creation_context,
        )
        self._staged.append(child)
        self._staged_ids.append(self._child_iid)
        self._store._revision_tokens[self._child_iid.value] = initial_revision(
            self._child_iid, token_seed=self._child_iid.value
        ).revision_token
        return self

    def stage_edge(self, *, parent_ref=None, edge_id=None, source_decision=None) -> "ParentChildTransaction":
        if self._replayed:
            return self
        if not self._active:
            raise TransactionNotCommittedError("transaction is not active")
        parent_ref = parent_ref or self._subject_ref
        edge_id = edge_id or self._edge_id
        source_decision = source_decision or self._source_decision
        edge = records.followup_edge(
            edge_id,
            parent_ref.internal_id,
            self._child_iid,
            source_decision.decision_id,
            rationale=self._rationale,
        )
        self._staged.append(edge)
        return self

    def _commit_apply(self) -> None:
        # The child was staged at rev 1 and the edge staged; the parent
        # revision advances.
        current = self._store.current_revision(self._subject_ref)
        next_parent_number = current.revision_number + 1
        parent = self._store._subjects[self._subject_ref.internal_id.value]
        next_parent = set_revision_number(parent, next_parent_number)
        self._store._put_staged(next_parent)
        self._new_parent_revision = advance_revision(
            current,
            aggregate_snapshot=self._child_subject_ref.serialize(),
        )
        self._store._revision_tokens[self._subject_ref.internal_id.value] = self._new_parent_revision.revision_token

    def _replay_result(self) -> TransactionResult:
        existing = self._store.idempotency_lookup(self._idempotency_key)
        return TransactionResult(
            outcome="COMMITTED",
            idempotency_key=self._idempotency_key,
            subject_ref=self._subject_ref.serialize(),
            revision_after=existing.revision_after if existing else None,
            revision_token=existing.revision_token if existing else None,
            effect_refs=existing.effect_refs if existing else (),
            replayed=True,
        )

    def _build_result(self) -> TransactionResult:
        self._idem = IdempotencyRecord(
            idempotency_key=self._idempotency_key,
            operation=self._operation,
            target=self._subject_ref.serialize(),
            principal=self._trusted_context.principal.id,
            fingerprint=self._fingerprint,
            state=IDEMPOTENCY_STATE_COMMITTED,
            effect_refs=(self._child_subject_ref.serialize(),),
            revision_after=self._new_parent_revision.revision_number,
            revision_token=self._new_parent_revision.revision_token,
        )
        return TransactionResult(
            outcome="COMMITTED",
            idempotency_key=self._idempotency_key,
            subject_ref=self._subject_ref.serialize(),
            revision_after=self._new_parent_revision.revision_number,
            revision_token=self._new_parent_revision.revision_token,
            effect_refs=(self._child_subject_ref.serialize(),),
            replayed=False,
        )


def parent_workflow_ref(store: TransactionStore, parent_ref: ObjectRef):
    parent = store.read_subject(parent_ref)
    return parent.workflow_ref


__all__ = [
    "TransactionStore",
    "SubjectTransaction",
    "ParentChildTransaction",
    "TransactionResult",
    "TRANSACTION_RUNTIME_AUTHORITY_ACTIVE",
    "PRODUCTION_GRAPH_AUTHORITY_ACTIVE",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED",
    "B6_TEST_STORAGE_IS_AUTHORITY",
    "PARTIAL_COMMIT_ALLOWED",
    "FAILED_MINT_REUSE_ALLOWED",
    "CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED",
    "B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED",
    "INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK",
    "SAME_KEY_DIFFERENT_REQUEST",
]
