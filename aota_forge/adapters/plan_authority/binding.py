"""W1-C source-neutral Plan Authority binding contract (AF #57 M1/W1).

States one internal Plan identity bound to exactly one current authority
source, while keeping identity, authority reference, and observed source
revision/digest as separate concerns:

    internal Plan identity  ->  one bound authority source
    plan_id                     source_kind + authority_ref
                                source_revision / source_digest (observed)

Hard invariants (frozen Governance 2.0 baseline):

    PLAN_ID_IS_AUTHORITY=no
    AUTHORITY_REF_IS_AUTHORITY=no
    OBJECT_REF_IS_AUTHORITY=no
    BOUND_AUTHORITY_SOURCE_REQUIRED_FOR_AUTHORITATIVE_READ=yes
    ONE_CURRENT_BOUND_PLAN_AUTHORITY_PER_PLAN=yes

The binding is executor-neutral: it carries no GitHub Issue number, comment
ID, filesystem path, or SQLite schema field.  The ``authority_ref`` is an
opaque operator/source reference; it never grants authority by itself and it
is never a mutable write API.  Source kinds:

    github_issue      Governance 1.x GitHub Issue authority (current accepted)
    local_governance  Governance 2.0 Local Governance authority (W2 adapter)

This module defines the contract only.  It does not implement local storage,
does not materialize files, and does not create a second Plan mutation
protocol (``PlanAuthorityMutationPort`` remains the only external Plan
mutation protocol).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Mapping

from aota_forge.core.plan.validation import is_plan_id

PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE = "github_issue"
PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE = "local_governance"

PLAN_AUTHORITY_SOURCE_KINDS = frozenset(
    {
        PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
        PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    }
)

# Source kinds available under each governance generation.
GOVERNANCE_1_X_SOURCE_KINDS = frozenset({PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE})
GOVERNANCE_2_0_SOURCE_KINDS = frozenset({PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE})

# Identity / reference values are never authority.
PLAN_ID_IS_AUTHORITY = False
AUTHORITY_REF_IS_AUTHORITY = False
OBJECT_REF_IS_AUTHORITY = False

# An authoritative Plan read requires one explicit bound source; one Plan has
# at most one current binding (absence is None, never an empty binding).
BOUND_AUTHORITY_SOURCE_REQUIRED_FOR_AUTHORITATIVE_READ = True
ONE_CURRENT_BOUND_PLAN_AUTHORITY_PER_PLAN = True

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_AUTHORITY_REF_LENGTH = 512
_MAX_SOURCE_REVISION_LENGTH = 128


class PlanAuthorityBindingError(ValueError):
    """Fail-closed Plan Authority binding error."""


@dataclass(frozen=True)
class PlanAuthorityBinding:
    """One internal Plan identity and its one current bound authority source.

    ``plan_id`` is identity only.  ``source_kind`` + ``authority_ref`` state
    which external/source authority is currently bound.  ``source_revision``
    and ``source_digest`` are observed source facts when available; they are
    never execution authority by themselves.
    """

    plan_id: str
    source_kind: str
    authority_ref: str
    source_revision: str | int | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        if not is_plan_id(self.plan_id):
            raise PlanAuthorityBindingError(
                "plan_id must be one canonical internal Plan ID (identity, never authority)"
            )
        if self.source_kind not in PLAN_AUTHORITY_SOURCE_KINDS:
            raise PlanAuthorityBindingError(
                f"source_kind must be one of {sorted(PLAN_AUTHORITY_SOURCE_KINDS)}"
            )
        if (
            not isinstance(self.authority_ref, str)
            or not self.authority_ref.strip()
            or len(self.authority_ref) > _MAX_AUTHORITY_REF_LENGTH
            or "\x00" in self.authority_ref
        ):
            raise PlanAuthorityBindingError(
                "authority_ref must be a bounded non-empty opaque source reference"
            )
        object.__setattr__(self, "authority_ref", self.authority_ref.strip())

        if self.source_revision is not None:
            if isinstance(self.source_revision, bool) or not isinstance(self.source_revision, (str, int)):
                raise PlanAuthorityBindingError("source_revision must be str|int when observed")
            if isinstance(self.source_revision, str):
                if not self.source_revision or len(self.source_revision) > _MAX_SOURCE_REVISION_LENGTH:
                    raise PlanAuthorityBindingError(
                        "source_revision must be a bounded non-empty string when observed as str"
                    )
            elif self.source_revision < 0:
                raise PlanAuthorityBindingError("source_revision must be non-negative when observed as int")

        if self.source_digest is not None and (
            not isinstance(self.source_digest, str) or not _SHA256_RE.fullmatch(self.source_digest)
        ):
            raise PlanAuthorityBindingError(
                "source_digest must be a SHA-256 hex digest when observed"
            )

    def is_authority(self) -> bool:
        """The binding identifies a bound source; it is not authority itself."""
        return False

    def authority(self) -> bool:
        """Explicit authority observation (mirrors ObjectRef.authority())."""
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source_kind": self.source_kind,
            "authority_ref": self.authority_ref,
            "source_revision": self.source_revision,
            "source_digest": self.source_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanAuthorityBinding":
        if not isinstance(data, Mapping):
            raise PlanAuthorityBindingError("binding payload must be a mapping")
        allowed = {item.name for item in fields(cls)}
        extra = set(data.keys()) - allowed
        if extra:
            raise PlanAuthorityBindingError(f"unknown binding field(s): {sorted(extra)}")
        missing = {"plan_id", "source_kind", "authority_ref"} - set(data.keys())
        if missing:
            raise PlanAuthorityBindingError(f"missing binding field(s): {sorted(missing)}")
        return cls(
            plan_id=data["plan_id"],
            source_kind=data["source_kind"],
            authority_ref=data["authority_ref"],
            source_revision=data.get("source_revision"),
            source_digest=data.get("source_digest"),
        )


def require_bound_authority(binding: PlanAuthorityBinding | None) -> PlanAuthorityBinding:
    """Fail closed unless an explicit current authority binding is supplied.

    Authoritative Plan reads must present exactly one bound authority source;
    missing binding is an error, never an implicit fallback.
    """
    if binding is None:
        raise PlanAuthorityBindingError(
            "an authoritative Plan read requires one explicitly bound authority source"
        )
    if not isinstance(binding, PlanAuthorityBinding):
        raise PlanAuthorityBindingError("binding must be a PlanAuthorityBinding")
    return binding
