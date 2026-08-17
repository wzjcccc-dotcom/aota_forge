"""Typed durable internal ID primitive (M3-B4, ``INTERNAL_ID_PRIMITIVES``).

An ``InternalId`` is an immutable, hashable, typed identity value for a
canonical graph object.  It is never a bare string and never an untyped
value: every instance carries an explicit ``kind`` (and, for any Subject, the
``sub_kind`` discriminator).  This is the primitive M3-B6 will later make
authoritative for durable atomic allocation; B4 itself exposes shape and
identity semantics only.

``ID_IS_AUTHORITY=no``: possessing or constructing an InternalId grants no
authority.  These objects identify and validate shape only.

Canonical serialized form (unambiguous):

    forge:<kind>:<value>                      (non-subject kinds)
    forge:subject:<sub_kind>:<value>          (Subject)

A value is a bounded plain token matching ``[A-Za-z0-9][A-Za-z0-9._:-]{0,127}``,
consistent with the M2/M3 bounded-token convention used by B3's opaque boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aota_forge.core.identity.errors import (
    MalformedIdError,
    UnknownIdKindError,
    WrongKindIdError,
)
from aota_forge.core.identity.kinds import (
    IdKind,
    require_graph_kind,
    require_subject_kind,
)

ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CANONICAL_PREFIX = "forge"


@dataclass(frozen=True)
class InternalId:
    """Immutable typed internal identity for one canonical graph object.

    Fields:
      kind     — graph object kind (workflow/subject/execution/completion/
                 decision/edge); required, never inferred from a bare value.
      value    — the durable identity value (derived or minted).
      sub_kind — required subject-kind discriminator when ``kind == subject``,
                 else ``None``.
    """

    kind: str
    value: str
    sub_kind: str | None = None

    def __post_init__(self) -> None:
        require_graph_kind(self.kind)
        if self.kind == IdKind.SUBJECT:
            if not isinstance(self.sub_kind, str):
                raise WrongKindIdError("subject id requires a sub_kind discriminator")
            require_subject_kind(self.sub_kind)
        else:
            if self.sub_kind is not None:
                raise WrongKindIdError(f"non-subject id must not carry sub_kind: {self.sub_kind!r}")
        if not isinstance(self.value, str):
            raise MalformedIdError("id value must be a string")
        if not ID_TOKEN_RE.fullmatch(self.value):
            raise MalformedIdError(f"id value is not a bounded plain token: {self.value!r}")

    def to_canonical(self) -> str:
        return canonical_id_str(self.kind, self.value, self.sub_kind)

    def is_subject(self) -> bool:
        return self.kind == IdKind.SUBJECT

    def authority(self) -> bool:
        """Identity never grants authority (``ID_IS_AUTHORITY=no``)."""
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"InternalId({self.to_canonical()})"

    def __str__(self) -> str:
        return self.to_canonical()


def canonical_id_str(kind: str, value: str, sub_kind: str | None = None) -> str:
    require_graph_kind(kind)
    if kind == IdKind.SUBJECT:
        if sub_kind is None:
            raise WrongKindIdError("subject id requires a sub_kind discriminator")
        require_subject_kind(sub_kind)
        return f"{_CANONICAL_PREFIX}:subject:{sub_kind}:{value}"
    if sub_kind is not None:
        raise WrongKindIdError(f"non-subject id must not carry sub_kind: {sub_kind!r}")
    return f"{_CANONICAL_PREFIX}:{kind}:{value}"


def parse_internal_id(text: str) -> InternalId:
    """Parse the canonical internal ID string into a typed ``InternalId``.

    A bare value with no kind is rejected (``BARE_ID_IMPLICIT_KIND_ALLOWED=no``).
    """
    if not isinstance(text, str) or not text.startswith(_CANONICAL_PREFIX + ":"):
        raise MalformedIdError("internal id must use the canonical forge:// prefix form")
    parts = text.split(":")
    if len(parts) == 3 and parts[0] == _CANONICAL_PREFIX:
        _, kind, value = parts
        if kind == IdKind.SUBJECT:
            raise MalformedIdError("subject id requires a sub_kind discriminator")
        return InternalId(kind=kind, value=value)
    if len(parts) == 4 and parts[0] == _CANONICAL_PREFIX and parts[1] == IdKind.SUBJECT:
        _, _kind, sub_kind, value = parts
        return InternalId(kind=IdKind.SUBJECT, sub_kind=sub_kind, value=value)
    raise MalformedIdError(f"malformed internal id: {text!r}")


def make_id(kind: str, value: str, sub_kind: str | None = None) -> InternalId:
    return InternalId(kind=kind, value=value, sub_kind=sub_kind)


def is_raw_canonical_id_string(text: object) -> bool:
    """True if ``text`` structurally matches a canonical internal ID string.

    Used by the model boundary to reject raw internal IDs as semantic refs,
    not to approve them.
    """
    if not isinstance(text, str) or not text.startswith(_CANONICAL_PREFIX + ":"):
        return False
    try:
        parse_internal_id(text)
        return True
    except (MalformedIdError, UnknownIdKindError, WrongKindIdError):
        return False


def subject_id_from_value(sub_kind: str, value: str) -> InternalId:
    """Build a typed Subject InternalId from a discriminated value."""
    return InternalId(kind=IdKind.SUBJECT, sub_kind=sub_kind, value=value)