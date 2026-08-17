"""M3-B3 opaque validated ID value boundary (B4-safe strategy B).

M3-B4 owns ``SUBJECT_ID_PRIMITIVE`` / ``RECORD_ID_PRIMITIVES`` / the ID broker,
mint/collision/reuse/derivation rules and the ObjectRef parser.  M3-B3 MUST NOT
duplicate or redefine any of that.

B3 therefore defines only a minimal, validated, OPAQUE value object that is
sufficient to construct durable graph records.  Semantic properties of this
boundary:

* it validates that a value is a bounded plain token for its kind
* it carries NO minting, collision, reuse, derivation, or authority semantics
* an ID never confers authority (``SUBJECT_ID_IS_AUTHORITY=no``); IDs identify
  records only and must not be used to infer authority

Concrete M3-B4 integration is deferred; see the B4 interface boundary report in
the validator fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class IdKind:
    """Bounded opaque ID kinds (namespace only; no authority semantics)."""

    WORKFLOW = "workflow"
    SUBJECT = "subject"
    EXECUTION = "execution"
    COMPLETION = "completion"
    DECISION = "decision"
    EDGE = "edge"


ALL_KINDS = frozenset({
    IdKind.WORKFLOW,
    IdKind.SUBJECT,
    IdKind.EXECUTION,
    IdKind.COMPLETION,
    IdKind.DECISION,
    IdKind.EDGE,
})


@dataclass(frozen=True)
class CanonicalId:
    """An opaque, validated canonical identity value.

    Immutable and hashable.  Validation is bounded and format-only; no minting
    or authority semantics are implemented here (M3-B4 owns the durable ID
    implementation).  B3 must not mint Subject IDs (``B3_MAY_MINT_SUBJECT_IDS=no``);
    fixtures supply opaque values.
    """

    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in ALL_KINDS:
            raise ValueError(f"unknown id kind: {self.kind!r}")
        if not isinstance(self.value, str):
            raise TypeError(f"{self.kind} id value must be a string")
        if not ID_TOKEN_RE.fullmatch(self.value):
            raise ValueError(f"{self.kind} id value is not a bounded plain token: {self.value!r}")

    def __repr__(self) -> str:
        return f"CanonicalId({self.kind}:{self.value!r})"


def ref_of(kind: str, value: str) -> CanonicalId:
    """Construct an opaque validated ID from a bounded plain token."""
    return CanonicalId(kind=kind, value=value)


def subject_id(value: str) -> CanonicalId:
    return ref_of(IdKind.SUBJECT, value)