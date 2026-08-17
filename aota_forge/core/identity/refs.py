"""Typed ObjectRef (M3-B4, ``OBJECT_REF_IMPLEMENTED``).

One canonical typed reference: ``object_kind`` (the graph object kind) plus a
canonical internal ``InternalId``.  For a Subject the InternalId also carries
the subject-kind discriminator.

Elevation rules implemented here:

* construct / parse / validate / serialize / canonical round trip
* wrong-kind rejection — an ID of one kind cannot form a ref of another kind
* malformed ref rejection — a bare or badly-shaped ref is rejected
* unknown kind rejection — an unrecognized object kind is rejected

NOT implemented here (owned by sibling lanes):

* graph lookup / resolution authority — owned by M3-B3
* authority evaluation on refs — owned by M3-B5 (``OBJECT_REF_IS_AUTHORITY=no``)

An ObjectRef carries identity only; it never implies or grants authority (the
``authority()`` accessor is the single explicit way to observe that the value
is ``False``).
"""

from __future__ import annotations

from dataclasses import dataclass

from aota_forge.core.identity.errors import (
    ObjectRefError,
    WrongKindIdError,
)
from aota_forge.core.identity.ids import (
    InternalId,
    canonical_id_str,
    parse_internal_id,
)
from aota_forge.core.identity.kinds import (
    IdKind,
    require_graph_kind,
)

REF_PREFIX = "ref"


@dataclass(frozen=True)
class ObjectRef:
    """Canonical typed reference to a canonical graph object.

    ``object_kind`` is the graph object kind; ``internal_id`` is the typed
    internal ID.  ``internal_id.kind`` equals ``object_kind`` and, when it is
    a Subject, carries the ``sub_kind`` discriminator.
    """

    object_kind: str
    internal_id: InternalId
    OBJECT_REF_IS_AUTHORITY = False

    def __post_init__(self) -> None:
        require_graph_kind(self.object_kind)
        if not isinstance(self.internal_id, InternalId):
            raise ObjectRefError("internal_id must be an InternalId")
        if self.internal_id.kind != self.object_kind:
            raise WrongKindIdError(
                f"ref kind {self.object_kind!r} does not match id kind {self.internal_id.kind!r}"
            )

    def serialize(self) -> str:
        """Canonical serialization: ``ref:<internal_id_canonical>``."""
        return f"{REF_PREFIX}:{self.internal_id.to_canonical()}"

    def to_canonical(self) -> str:
        return self.serialize()

    def authority(self) -> bool:
        """A ref identifies only; it never grants authority."""
        return False

    def graph_lookup(self) -> object:  # pragma: no cover - explicitly unsupported
        """Unsupported at B4: graph lookup is owned by M3-B3."""
        raise ObjectRefError("graph lookup is owned by M3-B3; not implemented at B4")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ObjectRef({self.serialize()})"

    def __str__(self) -> str:
        return self.serialize()


def make_object_ref(object_kind: str, internal_id: InternalId) -> ObjectRef:
    return ObjectRef(object_kind=object_kind, internal_id=internal_id)


def object_ref_workflow(internal_id: InternalId) -> ObjectRef:
    return ObjectRef(object_kind=IdKind.WORKFLOW, internal_id=internal_id)


def object_ref_subject(internal_id: InternalId) -> ObjectRef:
    if internal_id.kind != IdKind.SUBJECT:
        raise WrongKindIdError("subject ref requires a Subject InternalId")
    return ObjectRef(object_kind=IdKind.SUBJECT, internal_id=internal_id)


def parse_object_ref(text: str, expected_kind: str | None = None) -> ObjectRef:
    """Parse a canonical ObjectRef string.

    ``expected_kind`` optionally enforces a specific object kind
    (``WRONG_KIND_ID_REJECTED=yes``); a bare or malformed string is rejected
    (``BARE_ID_IMPLICIT_KIND_ALLOWED=no``).
    """
    if not isinstance(text, str) or not text.startswith(REF_PREFIX + ":"):
        raise ObjectRefError("object ref must use the canonical 'ref:' prefix form")
    inner = text[len(REF_PREFIX) + 1 :]
    iid = parse_internal_id(inner)
    if expected_kind is not None and iid.kind != expected_kind:
        raise WrongKindIdError(
            f"expected ref kind {expected_kind!r} but got {iid.kind!r}"
        )
    return ObjectRef(object_kind=iid.kind, internal_id=iid)


def object_ref_value_to_canonical(object_kind: str, value: str, sub_kind: str | None = None) -> str:
    """Canonical ObjectRef string from raw parts (for structured construction)."""
    iid = InternalId(kind=object_kind, value=value, sub_kind=sub_kind)
    return ObjectRef(object_kind=object_kind, internal_id=iid).serialize()


def is_raw_ref_canonical_string(text: object) -> bool:
    """Structural membership test for a canonical ObjectRef string."""
    if not isinstance(text, str) or not text.startswith(REF_PREFIX + ":"):
        return False
    try:
        parse_object_ref(text)
        return True
    except (ObjectRefError, WrongKindIdError):
        return False


def canonical_id_of(ref: ObjectRef) -> str:
    return canonical_id_str(ref.internal_id.kind, ref.internal_id.value, ref.internal_id.sub_kind)