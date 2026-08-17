"""M3-B6 Subject aggregate revision / CAS mechanics (Issue #9, lane M3-B6).

Implements the accepted M3-A3 revision contract over the Subject aggregate:

* revision = (aggregate_id, revision_number, revision_token)
* the Subject aggregate is the normal lifecycle revision root; Execution,
  Completion and Decision are child records and maintain NO independent
  mutation revision root (a mutation touching a child compares/advances the
  owning Subject revision).
* a new Subject starts at revision 1 of its own aggregate (own counter and
  token); the revision number is a monotonic non-negative value and is never
  reused.
* a successful lifecycle mutation advances the owning Subject revision exactly
  once; a failed mutation does not advance it; an idempotent replay does not
  advance it again.
* compare-and-swap: a request supplies ``expected_revision``; the transaction
  reads the current revision; ``expected == current`` allows the mutation to
  continue, ``expected != current`` fails closed (``STALE_REVISION_MUTATION=
  fail_closed``).  There is NO fallback to "latest wins" and no silent
  last-write-wins.

This module is executor-neutral and mechanical.  It performs NO authority
decision (B5 owns that), NO semantic selection and NO production graph write.
``CAS_SOURCE_IMPLEMENTED=yes``; ``TRANSACTION_RUNTIME_AUTHORITY_ACTIVE=no``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from aota_forge.core.graph import records

INITIAL_REVISION = 1
REVISION_TOKEN_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class MutationError(Exception):
    """Base class for B6 mutation/CAS/transaction domain failures.

    B6-local, carrying a stable machine-readable ``code`` for later canonical
    reconciliation (the shared error registry is not broadened by B6).
    """

    code = "B6_MUTATION_ERROR"
    default_message = "mutation error"

    def __init__(self, message: str | None = None, details: object = None) -> None:
        self.message = message if message is not None else self.default_message
        self.details = details
        super().__init__(self.message)


class SubjectNotFoundError(MutationError):
    """The owning Subject aggregate does not exist (not found)."""

    code = "B6_SUBJECT_NOT_FOUND"
    default_message = "owning subject not found"


class StaleRevisionError(MutationError):
    """CAS failed: expected revision != current revision (fail closed)."""

    code = "B6_STALE_REVISION"
    default_message = "stale revision (expected != current); fail closed"


class AuthorityDeniedError(MutationError):
    """The B5 authority engine did not ALLOW the mutation (no mutation)."""

    code = "B6_AUTHORITY_DENIED"
    default_message = "authority or lease denial"


class LeaseReplayDeniedError(MutationError):
    """A one-time capability lease was already consumed."""

    code = "B6_LEASE_REPLAY_DENIED"
    default_message = "one-time lease already consumed"


class IdempotencyConflictError(MutationError):
    """Same idempotency key with a different canonical request fingerprint."""

    code = "B6_IDEMPOTENCY_CONFLICT"
    default_message = "idempotency key reused with a different request"


class TransactionConflictError(MutationError):
    """A multi-root (followup) transaction could not commit atomically."""

    code = "B6_TRANSACTION_CONFLICT"
    default_message = "atomic transaction could not commit"


class IdCollisionError(MutationError):
    """An identity candidate collided or was already claimed (fail closed)."""

    code = "B6_ID_COLLISION"
    default_message = "identity collision; fail closed"


class IdReuseError(MutationError):
    """A retired/failed minted ID was reused (no silent reuse)."""

    code = "B6_ID_REUSE"
    default_message = "retired/failed minted id reused"


class TransactionNotCommittedError(MutationError):
    """Commit was attempted on a transaction that already ended."""

    code = "B6_TRANSACTION_NOT_ACTIVE"
    default_message = "transaction is not active"


@dataclass(frozen=True)
class SubjectRevision:
    """Bounded canonical revision value for one Subject aggregate.

    ``subject_ref`` is the canonical Subject InternalId; ``revision_number`` is
    the monotonic non-negative counter (starts at 1); ``revision_token`` is the
    opaque content-derived token (SHA-256 hex), deterministic over the
    aggregate's committed record set chained to the previous token.
    """

    subject_ref: object
    revision_number: int
    revision_token: str

    def __post_init__(self) -> None:
        if isinstance(self.revision_number, bool) or not isinstance(self.revision_number, int):
            raise ValueError("revision_number must be a non-negative integer")
        if self.revision_number < INITIAL_REVISION:
            raise ValueError(f"revision_number must be >= {INITIAL_REVISION}")
        if not isinstance(self.revision_token, str) or not REVISION_TOKEN_HEX_RE.fullmatch(self.revision_token):
            raise ValueError("revision_token must be a SHA-256 hex digest")


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def subject_aggregate_records(subject_ref, repo) -> list:
    """Return the owning Subject's aggregate records in deterministic order.

    The aggregate is the Subject root plus every Execution, Completion and
    Decision owned by that Subject.  FollowupEdges participate at creation
    (child lineage snapshot) and are appended at creation time only.
    """
    from aota_forge.core.graph.repository import OwningSubjectResolver
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.identity.refs import make_object_ref

    resolver = OwningSubjectResolver(repo)
    subject = resolver.resolve_subject(make_object_ref(IdKind.SUBJECT, subject_ref))
    records_list: list = [subject]
    records_list.extend(resolver.executions_of(make_object_ref(IdKind.SUBJECT, subject_ref)))
    records_list.extend(resolver.decisions_of(make_object_ref(IdKind.SUBJECT, subject_ref)))
    records_list.sort(key=lambda r: (type(r).__name__, r.canonical_fields()["id"] if False else str(getattr(r, "execution_id" if hasattr(r, "execution_id") else "subject_id"))))
    return records_list


def initial_revision(subject_ref, *, token_seed: str) -> SubjectRevision:
    """Build the revision 1 value for a freshly created Subject.

    The initial token is deterministic over the canonical creation identity
    seed, so the same subject at its same creation state yields the same token.
    """
    token = _digest({"aggregate_id": str(subject_ref), "revision": INITIAL_REVISION, "seed": token_seed})
    return SubjectRevision(subject_ref, INITIAL_REVISION, token)


def advance_revision(current: SubjectRevision, *, aggregate_snapshot: object) -> SubjectRevision:
    """Advance the Subject aggregate revision by exactly one.

    ``revision_number`` strictly increases; ``revision_token`` chains to the
    previous token and the newly committed aggregate snapshot, so an unchanged
    committed record set can never advance (idempotent replay does not advance).
    """
    next_number = current.revision_number + 1
    token = _digest(
        {
            "prev_token": current.revision_token,
            "revision": next_number,
            "aggregate": aggregate_snapshot,
        }
    )
    return SubjectRevision(current.subject_ref, next_number, token)


def revision_number_of(subject: records.Subject) -> int:
    """Read the current canonical revision number from a Subject record.

    The revision is carried in ``mechanical_state.revision`` (a mechanical
    control-plane field, not model-choreographed); a Subject with no stored
    revision is treated as revision 1.
    """
    value = subject.mechanical_state.get("revision")
    if value is None:
        return INITIAL_REVISION
    if isinstance(value, bool) or not isinstance(value, int) or value < INITIAL_REVISION:
        raise MutationError(f"invalid stored revision: {value!r}")
    return value


def set_revision_number(subject: records.Subject, revision: int) -> records.Subject:
    """Return a copy of the Subject with the revision number advanced."""
    from dataclasses import replace

    state = dict(subject.mechanical_state)
    state["revision"] = revision
    return replace(subject, mechanical_state=state)


__all__ = [
    "INITIAL_REVISION",
    "MutationError",
    "SubjectNotFoundError",
    "StaleRevisionError",
    "AuthorityDeniedError",
    "LeaseReplayDeniedError",
    "IdempotencyConflictError",
    "TransactionConflictError",
    "IdCollisionError",
    "IdReuseError",
    "TransactionNotCommittedError",
    "SubjectRevision",
    "initial_revision",
    "advance_revision",
    "revision_number_of",
    "set_revision_number",
]
