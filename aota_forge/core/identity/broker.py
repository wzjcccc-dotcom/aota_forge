"""ID Broker primitives (M3-B4, ``ID_BROKER_PRIMITIVES``).

A stateful primitive that issues, accepts and retires identity values while
enforcing the collision and no-reuse contracts at the primitive/interface
level:

* candidate issuance  — an identity value is proposed but not yet persisted
* accepted/persisted allocation — the candidate is bound to one record
* retired/failed reservation — the value is retired and may never be re-used

``COLLISION_FAIL_CLOSED=yes``: colliding with an already-issued/accepted value
raises ``CollisionError``; there is no silent merge and no last-write-wins.

``ID_REUSE_ALLOWED=no``: a value that has been accepted or retired may not be
issued or accepted again.

IMPORTANT boundary: B4 exposes this *primitive* contract only.  B4 does NOT
provide crash-safe durable atomic allocation, idempotent replay, or
transactional persistence of a retired ID coupled to a failed mint — those are
deferred to M3-B6 (``B4_ATOMIC_DURABLE_ALLOCATION_IMPLEMENTED=no``,
``DURABLE_NO_REUSE_ENFORCEMENT_DEFERRED_TO_B6=yes``).  Persistent state is
session-local by default; B6 will make the allocation interface authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from aota_forge.core.identity.ids import InternalId
from aota_forge.core.identity.errors import (
    CollisionError,
    IdReuseError,
    WrongKindIdError,
)
from aota_forge.core.identity.subject import mint_work_subject_value


class IdState(Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    RETIRED = "retired"


@dataclass
class ReservationRecord:
    """One broker reservation entry for a single identity value."""

    internal_id: InternalId
    state: IdState = IdState.CANDIDATE
    reason: str = ""


@dataclass
class IdBroker:
    """Session-local ID broker primitive enforcing collision/no-reuse.

    ``persisted_durable`` is always ``False`` at the B4 level: durability is
    the M3-B6 authority.  An authoritative backend can later back this
    interface without changing its collision/reuse semantics.
    """

    B4_ATOMIC_DURABLE_ALLOCATION_IMPLEMENTED = False
    DURABLE_NO_REUSE_ENFORCEMENT_DEFERRED_TO_B6 = True
    persisted_durable: bool = False
    _records: dict[str, ReservationRecord] = field(default_factory=dict)

    def _key(self, iid: InternalId) -> str:
        # value alone is the binding key; a value is never reused across kinds.
        return f"{iid.kind}::{iid.sub_kind}::{iid.value}"

    def _ensure_issuable(self, iid: InternalId) -> None:
        key = self._key(iid)
        existing = self._records.get(key)
        if existing is None:
            return
        if existing.state is IdState.RETIRED:
            raise IdReuseError(f"retired id reused: {iid.to_canonical()}", details={"key": key})
        if existing.state is IdState.ACCEPTED:
            raise CollisionError(f"id already accepted by another record: {iid.to_canonical()}", details={"key": key})
        raise CollisionError(f"id collision: {iid.to_canonical()}", details={"key": key})

    def propose(self, iid: InternalId, reason: str = "") -> InternalId:
        """Register a candidate issuance (not yet persisted)."""
        key = self._key(iid)
        if key in self._records:
            self._ensure_issuable(iid)
        self._records[key] = ReservationRecord(internal_id=iid, state=IdState.CANDIDATE, reason=reason)
        return iid

    def allocate(self, iid: InternalId, reason: str = "") -> InternalId:
        """Accept/persist a candidate: bind the value to exactly one record.

        Combining a ready value binding with the collision and reuse rules
        makes collisions and reuse fail closed at the acceptance boundary too.
        """
        key = self._key(iid)
        existing = self._records.get(key)
        if existing is not None and existing.state is IdState.RETIRED:
            raise IdReuseError(f"retired id reused: {iid.to_canonical()}", details={"key": key})
        if existing is not None and existing.state is IdState.ACCEPTED:
            raise CollisionError(f"id already accepted: {iid.to_canonical()}", details={"key": key})
        self._records[key] = ReservationRecord(internal_id=iid, state=IdState.ACCEPTED, reason=reason)
        return iid

    def retire(self, iid: InternalId, reason: str = "") -> InternalId:
        """Retire a value (failed mint / superseded reservation): never re-used."""
        key = self._key(iid)
        self._records[key] = ReservationRecord(internal_id=iid, state=IdState.RETIRED, reason=reason)
        return iid

    def state_of(self, iid: InternalId) -> IdState | None:
        rec = self._records.get(self._key(iid))
        return rec.state if rec is not None else None

    def is_known(self, iid: InternalId) -> bool:
        return self._key(iid) in self._records

    def is_retired(self, iid: InternalId) -> bool:
        rec = self._records.get(self._key(iid))
        return rec is not None and rec.state is IdState.RETIRED

    def is_accepted(self, iid: InternalId) -> bool:
        rec = self._records.get(self._key(iid))
        return rec is not None and rec.state is IdState.ACCEPTED


def allocate_minted_subject(broker: IdBroker, kind: str, sub_kind: str, reason: str = "") -> InternalId:
    """Allocate a freshly minted internal ID for a matching subject kind.

    Only valid for minted kinds (e.g. WorkSubject); deterministic kinds must
    go through their own derivation and are rejected here
    (``WRONG_KIND_ID_REJECTED=yes``).
    """
    from aota_forge.core.identity.kinds import DETERMINISTIC_SUBJECT_KINDS

    if sub_kind not in DETERMINISTIC_SUBJECT_KINDS:
        value = mint_work_subject_value()
        iid = InternalId(kind=kind, sub_kind=sub_kind, value=value)
        return broker.allocate(iid, reason=reason)
    raise WrongKindIdError(f"subject kind is deterministic, not minted: {sub_kind!r}")