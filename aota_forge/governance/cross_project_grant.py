"""AF #57 M2/W4 — durable bounded CrossProjectGrant (Governed Read Extensions).

The smallest durable, typed, bounded capability record that can widen the
accepted #55 ``AuthorizedRootSet`` with exactly one foreign-project read
root.  It is a Governance 2.0 authority *record*, not an authority engine:

    trusted target Project resolution (existing trusted registry/binding)
  + trusted authority basis (active Plan record in the Project Governance
    Store, or an operator user-gate approval fact)
  + bounded scope / root kind / read-only capabilities
      -> CrossProjectGrant (durable, CAS revisioned)
      -> materialization into the existing AuthorizedRootSet (W4 composition)

Invariants
----------
* CROSS_PROJECT_GRANT_IMPLEMENTED=yes
* GRANT_IS_AUTHORITY_RECORD=yes
* MODEL_MINTS_GRANT=no
* CROSS_PROJECT_DEFAULT_DENIED=yes
* CROSS_PROJECT_GRANT_DEFAULT=read_only
* CROSS_PROJECT_WRITE_ALLOWED=no
* UNRELATED_SIBLING_PROJECT_REACHABLE=no (without an explicit grant)
* MODEL_CAN_SELF_GRANT_CROSS_PROJECT_ACCESS=no
* MODEL_PHYSICAL_PATH_AUTHORITY=no
* TARGET_PROJECT_RESOLVED_THROUGH_TRUSTED_REGISTRY=yes
* SECOND_GRANT_DATABASE=no (durability belongs to the Project Governance Store)
* STALE_GRANT_MUTATION_FAILS_CLOSED=yes
* PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT=no

There is deliberately no public Tool/operation here: grant creation is
trusted Control-Plane/governance activity that requires trusted objects
(the Project Governance Store, a trusted Project resolution evidence and an
active Plan record or typed operator approval), never model arguments.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from typing import Any, Mapping, Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.core.project.resolver import ProjectResolutionEvidence
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    ProjectPlanRecord,
)

CROSS_PROJECT_GRANT_IMPLEMENTED = True
GRANT_IS_AUTHORITY_RECORD = True
MODEL_MINTS_GRANT = False
MODEL_CAN_SELF_GRANT_CROSS_PROJECT_ACCESS = False
MODEL_PHYSICAL_PATH_AUTHORITY = False
CROSS_PROJECT_DEFAULT_DENIED = True
CROSS_PROJECT_GRANT_IS_READ_ONLY = True
CROSS_PROJECT_GRANT_DEFAULT = "read_only"
CROSS_PROJECT_WRITE_ALLOWED = False
SECOND_GRANT_DATABASE_CREATED = False
TARGET_PROJECT_RESOLVED_THROUGH_TRUSTED_REGISTRY = True
STALE_GRANT_MUTATION_FAILS_CLOSED = True
PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT = False
GRANT_IS_PUBLIC_AGENT_OPERATION = False

GRANT_STORE_OWNER = "project_governance_store"

CROSS_PROJECT_GRANT_ERROR = "CROSS_PROJECT_GRANT_ERROR"
CROSS_PROJECT_GRANT_RECORD_INVALID = "CROSS_PROJECT_GRANT_RECORD_INVALID"
CROSS_PROJECT_GRANT_SELF_GRANT_DENIED = "CROSS_PROJECT_GRANT_SELF_GRANT_DENIED"
CROSS_PROJECT_GRANT_TARGET_INVALID = "CROSS_PROJECT_GRANT_TARGET_INVALID"
CROSS_PROJECT_GRANT_CAPABILITY_DENIED = "CROSS_PROJECT_GRANT_CAPABILITY_DENIED"
CROSS_PROJECT_GRANT_AUTHORITY_INVALID = "CROSS_PROJECT_GRANT_AUTHORITY_INVALID"
CROSS_PROJECT_GRANT_INACTIVE = "CROSS_PROJECT_GRANT_INACTIVE"
CROSS_PROJECT_GRANT_NOT_FOUND = "CROSS_PROJECT_GRANT_NOT_FOUND"
CROSS_PROJECT_GRANT_EXISTS = "CROSS_PROJECT_GRANT_EXISTS"
CROSS_PROJECT_GRANT_STALE_REVISION = "CROSS_PROJECT_GRANT_STALE_REVISION"

CAPABILITY_READ = "read"
CAPABILITY_SEARCH = "search"
CROSS_PROJECT_GRANT_CAPABILITIES: frozenset[str] = frozenset(
    {CAPABILITY_READ, CAPABILITY_SEARCH}
)

ROOT_KIND_PROJECT_MAIN = "project-main"
GRANTABLE_ROOT_KINDS: tuple[str, ...] = (ROOT_KIND_PROJECT_MAIN,)

AUTHORITY_BASIS_PREAPPROVED_BY_PLAN = "preapproved_by_plan"
AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL = "explicit_user_approval"
AUTHORITY_BASES: frozenset[str] = frozenset(
    {AUTHORITY_BASIS_PREAPPROVED_BY_PLAN, AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL}
)

END_CONDITION_UNTIL_REVOKED = "until_revoked"
END_CONDITION_PLAN_RETIRED = "plan_retired"
END_CONDITIONS: frozenset[str] = frozenset(
    {END_CONDITION_UNTIL_REVOKED, END_CONDITION_PLAN_RETIRED}
)

GRANT_STATE_ACTIVE = "active"
GRANT_STATE_REVOKED = "revoked"
GRANT_STATES: frozenset[str] = frozenset({GRANT_STATE_ACTIVE, GRANT_STATE_REVOKED})

MAX_PROJECT_REFERENCE_LENGTH = 96
MAX_GRANT_ID_LENGTH = 64
MAX_SCOPE_LENGTH = 512
MAX_SCOPE_SEGMENTS = 64
MAX_SCOPE_SEGMENT_LENGTH = 128

_GRANT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{7,63}$")
_SCOPE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class CrossProjectGrantError(Exception):
    """Bounded deterministic base error for cross-project grants."""

    code = CROSS_PROJECT_GRANT_ERROR

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


class CrossProjectGrantRecordError(CrossProjectGrantError, ValueError):
    """A grant record or lifecycle request is invalid (fail closed)."""

    code = CROSS_PROJECT_GRANT_RECORD_INVALID


class CrossProjectGrantSelfGrantError(CrossProjectGrantError, ValueError):
    """A project can never grant cross-project access to itself."""

    code = CROSS_PROJECT_GRANT_SELF_GRANT_DENIED


class CrossProjectGrantTargetError(CrossProjectGrantError, ValueError):
    """The target project fact is not a singular trusted registry resolution."""

    code = CROSS_PROJECT_GRANT_TARGET_INVALID


class CrossProjectGrantCapabilityError(CrossProjectGrantError, ValueError):
    """Only read/search are grantable; write is never a normal grant."""

    code = CROSS_PROJECT_GRANT_CAPABILITY_DENIED


class CrossProjectGrantAuthorityError(CrossProjectGrantError, ValueError):
    """The authority basis is not a trusted typed Plan/user-gate fact."""

    code = CROSS_PROJECT_GRANT_AUTHORITY_INVALID


class CrossProjectGrantInactiveError(CrossProjectGrantError):
    """The grant is revoked or its bounded end condition has been reached."""

    code = CROSS_PROJECT_GRANT_INACTIVE


class CrossProjectGrantNotFoundError(CrossProjectGrantError):
    code = CROSS_PROJECT_GRANT_NOT_FOUND


class CrossProjectGrantAlreadyExistsError(CrossProjectGrantError):
    code = CROSS_PROJECT_GRANT_EXISTS


class StaleCrossProjectGrantRevisionError(CrossProjectGrantError):
    code = CROSS_PROJECT_GRANT_STALE_REVISION


def _canonical_project_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise CrossProjectGrantRecordError(f"{label} must be str, got {type(value).__name__}")
    if value != value.strip() or not value or len(value) > MAX_PROJECT_REFERENCE_LENGTH:
        raise CrossProjectGrantRecordError(f"{label} must be the canonical bounded project identity")
    if not PROJECT_ID_RE.fullmatch(value):
        raise CrossProjectGrantRecordError(
            f"{label} must use the canonical project identity grammar"
        )
    return value


def validate_bounded_scope(value: object) -> str:
    """Validate the mechanically enforceable relative grant scope.

    Empty scope means the whole granted root.  A non-empty scope is a
    relative slash-separated subtree with bounded segments; absolute paths,
    traversal (``.``/``..``), backslashes, NUL and oversized input fail
    closed.  No glob/DSL system is introduced.
    """
    if not isinstance(value, str) or type(value) is not str:
        raise CrossProjectGrantRecordError(
            f"bounded_scope must be str, got {type(value).__name__}"
        )
    if value != value.strip() or "\x00" in value or "\\" in value:
        raise CrossProjectGrantRecordError("bounded_scope must be a canonical relative subtree")
    if len(value) > MAX_SCOPE_LENGTH:
        raise CrossProjectGrantRecordError("bounded_scope exceeds the bounded scope length")
    if value.startswith("/"):
        raise CrossProjectGrantRecordError("bounded_scope must be relative, never absolute")
    if value == "":
        return ""
    segments = value.split("/")
    if len(segments) > MAX_SCOPE_SEGMENTS:
        raise CrossProjectGrantRecordError("bounded_scope exceeds the bounded segment count")
    for segment in segments:
        if not segment or segment in (".", ".."):
            raise CrossProjectGrantRecordError(
                "bounded_scope must not contain empty or traversal segments"
            )
        if len(segment) > MAX_SCOPE_SEGMENT_LENGTH or not _SCOPE_SEGMENT_RE.fullmatch(segment):
            raise CrossProjectGrantRecordError(
                f"bounded_scope segment {segment!r} is not a bounded relative segment"
            )
        if segment.startswith(".") or ".." == segment:
            raise CrossProjectGrantRecordError("bounded_scope must not use hidden/traversal segments")
    return value


@dataclass(frozen=True)
class UserGateApproval:
    """Trusted explicit user-approval fact from the operator Control Plane.

    Constructed only by trusted Control-Plane composition from an operator
    gate record; the digest is a bounded fact, never free model prose.
    """

    approval_ref: str
    approval_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.approval_ref, str) or type(self.approval_ref) is not str:
            raise CrossProjectGrantAuthorityError("approval_ref must be a bounded reference string")
        if (
            not self.approval_ref.strip()
            or self.approval_ref != self.approval_ref.strip()
            or len(self.approval_ref) > 256
            or any(ch in self.approval_ref for ch in ("\x00", "\n", "\r"))
        ):
            raise CrossProjectGrantAuthorityError(
                "approval_ref must be a bounded single-line operator-gate reference"
            )
        if (
            not isinstance(self.approval_digest, str)
            or not _DIGEST_RE.fullmatch(self.approval_digest)
        ):
            raise CrossProjectGrantAuthorityError("approval_digest must be a SHA-256 hex digest")


@dataclass(frozen=True)
class CrossProjectGrantAuthority:
    """Trusted typed authority basis of one grant.

    ``anchor_plan_id`` is the requesting project's active Plan record the
    authority was grounded in; the digest binds basis + anchor Plan authority
    + optional operator approval reference.  It is never an arbitrary string
    supplied by the caller.
    """

    basis: str
    anchor_plan_id: str
    authority_ref: str
    authority_digest: str

    def __post_init__(self) -> None:
        if self.basis not in AUTHORITY_BASES:
            raise CrossProjectGrantAuthorityError(
                f"authority basis must be one of {sorted(AUTHORITY_BASES)}"
            )
        if not is_plan_id(self.anchor_plan_id):
            raise CrossProjectGrantAuthorityError(
                "authority anchor must be one canonical internal Plan ID"
            )
        if (
            not isinstance(self.authority_ref, str)
            or not self.authority_ref.strip()
            or len(self.authority_ref) > 256
        ):
            raise CrossProjectGrantAuthorityError("authority_ref must be a bounded reference")
        if not isinstance(self.authority_digest, str) or not _DIGEST_RE.fullmatch(
            self.authority_digest
        ):
            raise CrossProjectGrantAuthorityError("authority_digest must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "anchor_plan_id": self.anchor_plan_id,
            "authority_ref": self.authority_ref,
            "authority_digest": self.authority_digest,
        }


@dataclass(frozen=True)
class PreapprovedByPlan:
    """Typed authority request: the requesting project's Plan preapproved it."""

    plan_id: str

    def __post_init__(self) -> None:
        if not is_plan_id(self.plan_id):
            raise CrossProjectGrantAuthorityError(
                "preapproved_by_plan requires one canonical internal Plan ID"
            )


@dataclass(frozen=True)
class ExplicitUserApproval:
    """Typed authority request: explicit operator user-gate approval."""

    plan_id: str
    approval: UserGateApproval

    def __post_init__(self) -> None:
        if not is_plan_id(self.plan_id):
            raise CrossProjectGrantAuthorityError(
                "explicit_user_approval requires the anchor canonical internal Plan ID"
            )
        if not isinstance(self.approval, UserGateApproval):
            raise CrossProjectGrantAuthorityError(
                "explicit_user_approval requires a UserGateApproval fact"
            )


def _compute_authority_digest(
    *,
    basis: str,
    anchor_plan_id: str,
    plan_authority: Mapping[str, Any],
    authority_ref: str,
    approval_digest: str = "",
) -> str:
    payload = {
        "basis": basis,
        "anchor_plan_id": anchor_plan_id,
        "plan_authority": dict(plan_authority),
        "authority_ref": authority_ref,
        "approval_digest": approval_digest,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _grounded_authority(
    *,
    requesting_project: str,
    request: PreapprovedByPlan | ExplicitUserApproval,
    store: "CrossProjectGrantStore",
) -> CrossProjectGrantAuthority:
    record = store.get_plan(requesting_project, request.plan_id)
    if record is None:
        raise CrossProjectGrantAuthorityError(
            "authority request does not reference a Plan record of the requesting project"
        )
    if not isinstance(record, ProjectPlanRecord):
        raise CrossProjectGrantAuthorityError("Plan record has an unexpected type")
    if record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
        raise CrossProjectGrantAuthorityError(
            "authority anchor Plan is not active; the grant authority is not grounded"
        )
    if isinstance(request, PreapprovedByPlan):
        basis = AUTHORITY_BASIS_PREAPPROVED_BY_PLAN
        authority_ref = record.authority.authority_ref
        approval_digest = ""
    else:
        basis = AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL
        authority_ref = request.approval.approval_ref
        approval_digest = request.approval.approval_digest
    return CrossProjectGrantAuthority(
        basis=basis,
        anchor_plan_id=record.plan_id,
        authority_ref=authority_ref,
        authority_digest=_compute_authority_digest(
            basis=basis,
            anchor_plan_id=record.plan_id,
            plan_authority=record.authority.to_dict(),
            authority_ref=authority_ref,
            approval_digest=approval_digest,
        ),
    )


@dataclass(frozen=True)
class TargetProjectResolution:
    """Minimal trusted target resolution fact (logical identity only).

    Built from the existing trusted registry/project-binding resolution
    evidence.  No host path is stored; the resolution digest records the
    observed trusted resolution facts.
    """

    project_id: str
    status: str
    candidate_count: int
    resolution_digest: str

    def __post_init__(self) -> None:
        _canonical_project_id(self.project_id, label="target project_id")
        if self.status != "RESOLVED":
            raise CrossProjectGrantTargetError(
                "target project must be a singular trusted RESOLVED resolution"
            )
        if type(self.candidate_count) is not int or self.candidate_count != 1:
            raise CrossProjectGrantTargetError("target project resolution must be singular")
        if not isinstance(self.resolution_digest, str) or not _DIGEST_RE.fullmatch(
            self.resolution_digest
        ):
            raise CrossProjectGrantTargetError("target resolution digest must be a SHA-256 hex digest")

    @classmethod
    def from_evidence(
        cls, evidence: object, *, expected_project_id: object
    ) -> "TargetProjectResolution":
        pid = _canonical_project_id(expected_project_id, label="target project_id")
        if not isinstance(evidence, ProjectResolutionEvidence):
            raise CrossProjectGrantTargetError(
                "target project requires the canonical trusted Project resolution evidence"
            )
        if evidence.status != "RESOLVED" or len(evidence.candidates) != 1:
            raise CrossProjectGrantTargetError(
                "target project resolution must be singular RESOLVED (no heuristic fallback)"
            )
        candidate = evidence.candidates[0]
        if candidate.project_id != pid:
            raise CrossProjectGrantTargetError(
                "resolved candidate project identity does not match the requested target"
            )
        digest = hashlib.sha256(
            canonical_json(evidence.to_dict()).encode("utf-8")
        ).hexdigest()
        return cls(
            project_id=pid,
            status="RESOLVED",
            candidate_count=1,
            resolution_digest=digest,
        )


@dataclass(frozen=True)
class CrossProjectGrant:
    """One durable bounded cross-project read capability record."""

    grant_id: str
    requesting_project: str
    target_project: str
    root_kind: str
    capabilities: frozenset[str]
    bounded_scope: str
    authority: CrossProjectGrantAuthority
    end_condition: str
    target_resolution: TargetProjectResolution
    bound_plan_id: str = ""
    revision: int = 1
    state: str = GRANT_STATE_ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.grant_id, str) or not _GRANT_ID_RE.fullmatch(self.grant_id):
            raise CrossProjectGrantRecordError(
                "grant_id must be a bounded trusted grant identity, never model supplied"
            )
        requesting = _canonical_project_id(self.requesting_project, label="requesting_project")
        target = _canonical_project_id(self.target_project, label="target_project")
        object.__setattr__(self, "requesting_project", requesting)
        object.__setattr__(self, "target_project", target)
        if requesting == target:
            raise CrossProjectGrantSelfGrantError(
                "a project cannot grant cross-project access to itself"
            )
        if self.root_kind not in GRANTABLE_ROOT_KINDS:
            raise CrossProjectGrantRecordError(
                f"root_kind must be one of {list(GRANTABLE_ROOT_KINDS)}"
            )
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise CrossProjectGrantRecordError("capabilities must be a non-empty frozenset")
        forbidden = set(self.capabilities) - CROSS_PROJECT_GRANT_CAPABILITIES
        if forbidden:
            raise CrossProjectGrantCapabilityError(
                f"cross-project grants are read/search only; forbidden: {sorted(forbidden)}"
            )
        object.__setattr__(self, "bounded_scope", validate_bounded_scope(self.bounded_scope))
        if not isinstance(self.authority, CrossProjectGrantAuthority):
            raise CrossProjectGrantAuthorityError(
                "authority must be a grounded CrossProjectGrantAuthority"
            )
        if self.end_condition not in END_CONDITIONS:
            raise CrossProjectGrantRecordError(
                f"end_condition must be one of {sorted(END_CONDITIONS)}"
            )
        if self.end_condition == END_CONDITION_PLAN_RETIRED:
            if not is_plan_id(self.bound_plan_id):
                raise CrossProjectGrantRecordError(
                    "plan_retired end condition requires a canonical bound Plan ID"
                )
        elif self.bound_plan_id:
            raise CrossProjectGrantRecordError(
                "bound_plan_id is only meaningful for the plan_retired end condition"
            )
        if not isinstance(self.target_resolution, TargetProjectResolution):
            raise CrossProjectGrantTargetError(
                "target_resolution must be the trusted target resolution fact"
            )
        if self.target_resolution.project_id != target:
            raise CrossProjectGrantTargetError(
                "target resolution identity must match the grant target project"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise CrossProjectGrantRecordError("revision must be a positive integer")
        if self.state not in GRANT_STATES:
            raise CrossProjectGrantRecordError(f"state must be one of {sorted(GRANT_STATES)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "requesting_project": self.requesting_project,
            "target_project": self.target_project,
            "root_kind": self.root_kind,
            "capabilities": sorted(self.capabilities),
            "bounded_scope": self.bounded_scope,
            "authority": self.authority.to_dict(),
            "end_condition": self.end_condition,
            "bound_plan_id": self.bound_plan_id,
            "target_resolution": {
                "project_id": self.target_resolution.project_id,
                "status": self.target_resolution.status,
                "candidate_count": self.target_resolution.candidate_count,
                "resolution_digest": self.target_resolution.resolution_digest,
            },
            "revision": self.revision,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CrossProjectGrant":
        if not isinstance(data, Mapping):
            raise CrossProjectGrantRecordError("grant payload must be a mapping")
        allowed = {item.name for item in fields(cls)}
        extra = set(data.keys()) - allowed
        if extra:
            raise CrossProjectGrantRecordError(f"unknown grant field(s): {sorted(extra)}")
        required = {
            "grant_id",
            "requesting_project",
            "target_project",
            "root_kind",
            "capabilities",
            "bounded_scope",
            "authority",
            "end_condition",
            "target_resolution",
        }
        missing = required - set(data.keys())
        if missing:
            raise CrossProjectGrantRecordError(f"missing grant field(s): {sorted(missing)}")
        authority_data = data["authority"]
        if not isinstance(authority_data, Mapping):
            raise CrossProjectGrantRecordError("authority must be a mapping")
        resolution_data = data["target_resolution"]
        if not isinstance(resolution_data, Mapping):
            raise CrossProjectGrantRecordError("target_resolution must be a mapping")
        capabilities = data["capabilities"]
        if not isinstance(capabilities, (list, tuple)):
            raise CrossProjectGrantRecordError("capabilities must be a list")
        return cls(
            grant_id=data["grant_id"],
            requesting_project=data["requesting_project"],
            target_project=data["target_project"],
            root_kind=data["root_kind"],
            capabilities=frozenset(capabilities),
            bounded_scope=data["bounded_scope"],
            authority=CrossProjectGrantAuthority(
                basis=authority_data.get("basis"),
                anchor_plan_id=authority_data.get("anchor_plan_id", ""),
                authority_ref=authority_data.get("authority_ref", ""),
                authority_digest=authority_data.get("authority_digest", ""),
            ),
            end_condition=data["end_condition"],
            bound_plan_id=data.get("bound_plan_id", ""),
            target_resolution=TargetProjectResolution(
                project_id=resolution_data.get("project_id"),
                status=resolution_data.get("status"),
                candidate_count=resolution_data.get("candidate_count"),
                resolution_digest=resolution_data.get("resolution_digest", ""),
            ),
            revision=data.get("revision", 1),
            state=data.get("state", GRANT_STATE_ACTIVE),
        )


@runtime_checkable
class CrossProjectGrantStore(Protocol):
    """Structural port over the existing Project Governance Store grant table.

    The SQLite Project Governance Store adapter satisfies this protocol; the
    storage-neutral ``ProjectGovernanceStore`` port surface is deliberately
    unchanged.
    """

    def put_cross_project_grant(self, record: CrossProjectGrant) -> CrossProjectGrant:
        ...

    def get_cross_project_grant(self, grant_id: str) -> CrossProjectGrant | None:
        ...

    def list_cross_project_grants(
        self, requesting_project: str, *, active_only: bool = False
    ) -> tuple[CrossProjectGrant, ...]:
        ...

    def revoke_cross_project_grant(
        self, grant_id: str, expected_revision: int
    ) -> CrossProjectGrant:
        ...


def _mint_grant_id(record_fields: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(record_fields).encode("utf-8")).hexdigest()
    return f"grant-{digest[:20]}"


def create_cross_project_grant(
    *,
    store: CrossProjectGrantStore,
    requesting_project: object,
    target: TargetProjectResolution,
    authority: PreapprovedByPlan | ExplicitUserApproval,
    root_kind: object = ROOT_KIND_PROJECT_MAIN,
    capabilities: object = CROSS_PROJECT_GRANT_CAPABILITIES,
    bounded_scope: object = "",
    end_condition: object = END_CONDITION_UNTIL_REVOKED,
    bound_plan_id: object = "",
) -> CrossProjectGrant:
    """Trusted creation seam for one durable bounded read-only grant.

    Authority, target and scope are typed trusted facts.  There is no
    parameter that accepts an arbitrary authority-basis string, no physical
    path parameter, and no write capability.  The grant identity is derived
    deterministically from the validated record content.
    """
    if not isinstance(store, CrossProjectGrantStore):
        raise CrossProjectGrantRecordError(
            "grant creation requires the Project Governance Store grant surface"
        )
    requesting = _canonical_project_id(requesting_project, label="requesting_project")
    if not isinstance(target, TargetProjectResolution):
        raise CrossProjectGrantTargetError(
            "target must be a trusted TargetProjectResolution fact"
        )
    if target.project_id == requesting:
        raise CrossProjectGrantSelfGrantError(
            "a project cannot grant cross-project access to itself"
        )
    if not isinstance(authority, (PreapprovedByPlan, ExplicitUserApproval)):
        raise CrossProjectGrantAuthorityError(
            "authority must be a typed trusted authority request "
            "(PreapprovedByPlan or ExplicitUserApproval); arbitrary basis strings are rejected"
        )
    if root_kind not in GRANTABLE_ROOT_KINDS:
        raise CrossProjectGrantRecordError(
            f"root_kind must be one of {list(GRANTABLE_ROOT_KINDS)}"
        )
    if isinstance(capabilities, bool) or not isinstance(capabilities, (tuple, list, set, frozenset)):
        raise CrossProjectGrantCapabilityError("capabilities must be a bounded iterable of names")
    normalized = frozenset(capabilities)
    if not normalized:
        raise CrossProjectGrantCapabilityError("a grant must carry at least one capability")
    forbidden = normalized - CROSS_PROJECT_GRANT_CAPABILITIES
    if forbidden:
        raise CrossProjectGrantCapabilityError(
            f"cross-project grants are read/search only; forbidden: {sorted(forbidden)}"
        )
    scope = validate_bounded_scope(bounded_scope)
    grounded = _grounded_authority(
        requesting_project=requesting, request=authority, store=store
    )
    if end_condition not in END_CONDITIONS:
        raise CrossProjectGrantRecordError(
            f"end_condition must be one of {sorted(END_CONDITIONS)}"
        )
    if end_condition == END_CONDITION_PLAN_RETIRED:
        bound = str(bound_plan_id).strip() if bound_plan_id else grounded.anchor_plan_id
        if not is_plan_id(bound):
            raise CrossProjectGrantRecordError(
                "plan_retired end condition requires a canonical bound Plan ID"
            )
        bound_record = store.get_plan(requesting, bound)
        if bound_record is None:
            raise CrossProjectGrantRecordError(
                "bound Plan must exist in the requesting project's governance store"
            )
        if bound_record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            raise CrossProjectGrantRecordError(
                "bound Plan is not active; a plan-bounded grant would be dead on arrival"
            )
    else:
        bound = ""
    record_seed = {
        "requesting_project": requesting,
        "target_project": target.project_id,
        "root_kind": root_kind,
        "capabilities": sorted(normalized),
        "bounded_scope": scope,
        "authority": grounded.to_dict(),
        "end_condition": end_condition,
        "bound_plan_id": bound,
        "target_resolution_digest": target.resolution_digest,
    }
    record = CrossProjectGrant(
        grant_id=_mint_grant_id(record_seed),
        requesting_project=requesting,
        target_project=target.project_id,
        root_kind=root_kind,
        capabilities=normalized,
        bounded_scope=scope,
        authority=grounded,
        end_condition=end_condition,
        bound_plan_id=bound,
        target_resolution=target,
    )
    return store.put_cross_project_grant(record)


def evaluate_cross_project_grant(
    grant: CrossProjectGrant, *, store: CrossProjectGrantStore
) -> tuple[bool, str]:
    """Lazy/on-access liveness evaluation (no scheduler).

    Returns ``(live, reason)``; unknown or missing state fails closed.
    """
    if not isinstance(grant, CrossProjectGrant):
        raise CrossProjectGrantRecordError("grant must be a CrossProjectGrant")
    if not isinstance(store, CrossProjectGrantStore):
        raise CrossProjectGrantRecordError("liveness evaluation requires the grant store surface")
    if grant.state != GRANT_STATE_ACTIVE:
        return (False, GRANT_STATE_REVOKED)
    if grant.end_condition == END_CONDITION_UNTIL_REVOKED:
        return (True, GRANT_STATE_ACTIVE)
    if grant.end_condition == END_CONDITION_PLAN_RETIRED:
        record = store.get_plan(grant.requesting_project, grant.bound_plan_id)
        if record is None:
            return (False, "bound_plan_missing")
        if record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return (False, END_CONDITION_PLAN_RETIRED)
        return (True, GRANT_STATE_ACTIVE)
    return (False, "unknown_end_condition")


def require_live_cross_project_grant(
    grant: CrossProjectGrant, *, store: CrossProjectGrantStore
) -> CrossProjectGrant:
    live, reason = evaluate_cross_project_grant(grant, store=store)
    if not live:
        raise CrossProjectGrantInactiveError(
            f"cross-project grant {grant.grant_id!r} is not live: {reason}"
        )
    return grant


__all__ = [
    "CROSS_PROJECT_GRANT_IMPLEMENTED",
    "GRANT_IS_AUTHORITY_RECORD",
    "MODEL_MINTS_GRANT",
    "MODEL_CAN_SELF_GRANT_CROSS_PROJECT_ACCESS",
    "MODEL_PHYSICAL_PATH_AUTHORITY",
    "CROSS_PROJECT_DEFAULT_DENIED",
    "CROSS_PROJECT_GRANT_IS_READ_ONLY",
    "CROSS_PROJECT_GRANT_DEFAULT",
    "CROSS_PROJECT_WRITE_ALLOWED",
    "SECOND_GRANT_DATABASE_CREATED",
    "TARGET_PROJECT_RESOLVED_THROUGH_TRUSTED_REGISTRY",
    "STALE_GRANT_MUTATION_FAILS_CLOSED",
    "PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT",
    "GRANT_IS_PUBLIC_AGENT_OPERATION",
    "GRANT_STORE_OWNER",
    "CROSS_PROJECT_GRANT_ERROR",
    "CROSS_PROJECT_GRANT_RECORD_INVALID",
    "CROSS_PROJECT_GRANT_SELF_GRANT_DENIED",
    "CROSS_PROJECT_GRANT_TARGET_INVALID",
    "CROSS_PROJECT_GRANT_CAPABILITY_DENIED",
    "CROSS_PROJECT_GRANT_AUTHORITY_INVALID",
    "CROSS_PROJECT_GRANT_INACTIVE",
    "CROSS_PROJECT_GRANT_NOT_FOUND",
    "CROSS_PROJECT_GRANT_EXISTS",
    "CROSS_PROJECT_GRANT_STALE_REVISION",
    "CAPABILITY_READ",
    "CAPABILITY_SEARCH",
    "CROSS_PROJECT_GRANT_CAPABILITIES",
    "ROOT_KIND_PROJECT_MAIN",
    "GRANTABLE_ROOT_KINDS",
    "AUTHORITY_BASIS_PREAPPROVED_BY_PLAN",
    "AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL",
    "AUTHORITY_BASES",
    "END_CONDITION_UNTIL_REVOKED",
    "END_CONDITION_PLAN_RETIRED",
    "END_CONDITIONS",
    "GRANT_STATE_ACTIVE",
    "GRANT_STATE_REVOKED",
    "GRANT_STATES",
    "MAX_PROJECT_REFERENCE_LENGTH",
    "MAX_GRANT_ID_LENGTH",
    "MAX_SCOPE_LENGTH",
    "MAX_SCOPE_SEGMENTS",
    "MAX_SCOPE_SEGMENT_LENGTH",
    "CrossProjectGrantError",
    "CrossProjectGrantRecordError",
    "CrossProjectGrantSelfGrantError",
    "CrossProjectGrantTargetError",
    "CrossProjectGrantCapabilityError",
    "CrossProjectGrantAuthorityError",
    "CrossProjectGrantInactiveError",
    "CrossProjectGrantNotFoundError",
    "CrossProjectGrantAlreadyExistsError",
    "StaleCrossProjectGrantRevisionError",
    "validate_bounded_scope",
    "UserGateApproval",
    "CrossProjectGrantAuthority",
    "PreapprovedByPlan",
    "ExplicitUserApproval",
    "TargetProjectResolution",
    "CrossProjectGrant",
    "CrossProjectGrantStore",
    "create_cross_project_grant",
    "evaluate_cross_project_grant",
    "require_live_cross_project_grant",
]
