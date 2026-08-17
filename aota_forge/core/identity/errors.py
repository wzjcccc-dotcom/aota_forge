"""B4-local domain errors for internal identity and typed references.

These errors are intentionally NOT registered in the shared canonical
``aota_forge.core.contracts.errors`` registry.  That file is governed as an
integration-only shared hot file (M3-B4 must not modify it).  Each B4 domain
error therefore carries its own stable ``code`` so a later integration lane
(B12/B34) can reconcile a failing condition to the public canonical error
registry without B4 inventing shared-side changes now.

``ID_IS_AUTHORITY=no``: none of these errors or the identities they guard
carry authority semantics; they bound shape, kind, collision and reuse only.
"""

from __future__ import annotations


class IdentityError(Exception):
    """Base class for B4 identity/ref domain failures.

    B4-local, never imported into the shared error registry.  Carries a
    stable machine-readable ``code`` for later canonical reconciliation.
    """

    code = "B4_IDENTITY_ERROR"
    default_message = "identity error"

    def __init__(self, message: str | None = None, details: object = None) -> None:
        self.message = message if message is not None else self.default_message
        self.details = details
        super().__init__(self.message)


class UnknownIdKindError(IdentityError):
    """A graph object kind is not a recognized canonical kind."""

    code = "B4_UNKNOWN_ID_KIND"
    default_message = "unknown id kind"


class MalformedIdError(IdentityError):
    """An internal ID value has invalid shape (unbounded, wrong token, ...)."""

    code = "B4_MALFORMED_ID"
    default_message = "malformed id"


class WrongKindIdError(IdentityError):
    """An ID of one kind was used where another kind is required."""

    code = "B4_WRONG_KIND_ID"
    default_message = "wrong kind id"


class BareIdError(IdentityError):
    """A bare/kindless ID was supplied where a typed canonical ref is required."""

    code = "B4_BARE_ID"
    default_message = "bare id has no kind"


class CollisionError(IdentityError):
    """Two distinct subjects/records derived or claimed the same ID.

    Fails closed: collision never silently merges and never last-write-wins.
    It always surfaces a distinct error and requires one explicit semantic
    choice at an integration boundary (``COLLISION_FAIL_CLOSED=yes``).
    """

    code = "B4_ID_COLLISION"
    default_message = "identity collision"


class IdReuseError(IdentityError):
    """A retired or already-accepted ID was re-issued or re-claimed.

    ``ID_REUSE_ALLOWED=no``: an ID binds once.  Reuse attempts are rejected
    by the primitive contract even though durable cross-transaction
    enforcement is deferred to M3-B6.
    """

    code = "B4_ID_REUSE"
    default_message = "id reuse rejected"


class SemanticRefRejectedError(IdentityError):
    """A raw internal ID or legacy ID was supplied as a semantic model ref.

    ``MODEL_INTERNAL_IDS_NORMAL_INPUT=no``: normal model-facing input must
    not accept raw internal graph IDs; ``RAW_INTERNAL_ID_ACCEPTED_AS_SEMANTIC_REF
    =no``.
    """

    code = "B4_SEMANTIC_REF_REJECTED"
    default_message = "raw or legacy id rejected as semantic reference"


class ObjectRefError(IdentityError):
    """A typed ObjectRef could not be constructed/parsed/validated."""

    code = "B4_OBJECT_REF"
    default_message = "invalid object reference"


CLASSES = {
    cls.code: cls
    for cls in (
        IdentityError,
        UnknownIdKindError,
        MalformedIdError,
        WrongKindIdError,
        BareIdError,
        CollisionError,
        IdReuseError,
        SemanticRefRejectedError,
        ObjectRefError,
    )
}