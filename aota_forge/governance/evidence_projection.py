"""AF #57 M2/W4 — authorized evidence projection (Governed Read Extensions).

The smallest typed projection seam over evidence/result truth that already
exists.  It is *not* an Evidence Store: it creates no bytes, no database, no
directory and no canonical evidence copy.  Each item names the existing
physical owner and routes access to that owner's original mechanism:

    evidence tree (.aota-evidence/<project_id>/)   -> workspace.read/search
    worktree durable payloads                      -> result.hydrate
    ExecutionStateStore result refs                -> existing result refs
    materialization / validation receipts          -> existing receipt seam

Invariants
----------
* EVIDENCE_PROJECTION_IMPLEMENTED=yes
* SECOND_EVIDENCE_STORE_CREATED=no
* EVIDENCE_BYTES_DUPLICATED=no
* EVIDENCE_PROJECTION_IS_AUTHORITY=no
* EVIDENCE_REF_IS_AUTHORITY=no
* PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT=no
* AUTHORIZED_EVIDENCE_WRITE_ACCESS=no
* WORKER_DEFAULT_AUTHORIZED_EVIDENCE_GRANT=no
* RESULT_REFS_USE_ORIGINAL_HYDRATION_AUTHORITY=yes
* AUTHORIZED_EVIDENCE_FILESYSTEM_ROOT_IS_PROJECT_SCOPED=yes (the bounded
  root binding itself lives in ``authorized_roots``; this module never
  resolves a path, never scans a directory and never reads bytes)

Actual authority still comes from the trusted root/result scope that owns
the referenced truth; a projection item or an evidence ref never authorizes
access on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind

EVIDENCE_PROJECTION_IMPLEMENTED = True
SECOND_EVIDENCE_STORE_CREATED = False
EVIDENCE_BYTES_DUPLICATED = False
EVIDENCE_PROJECTION_IS_AUTHORITY = False
EVIDENCE_REF_IS_AUTHORITY = False
PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT = False
AUTHORIZED_EVIDENCE_WRITE_ACCESS = False
WORKER_DEFAULT_AUTHORIZED_EVIDENCE_GRANT = False
RESULT_REFS_USE_ORIGINAL_HYDRATION_AUTHORITY = True
AUTHORIZED_EVIDENCE_PROJECTION_IS_DERIVED = True

AUTHORIZED_EVIDENCE_ROOT_REF = "authorized-evidence"
AUTHORIZED_EVIDENCE_ROOT_IS_TRUSTED_BINDING_ONLY = True
AUTHORIZED_EVIDENCE_ROOT_IS_PROJECT_SCOPED = True
AUTHORIZED_EVIDENCE_GLOBAL_BASE_REACHABLE = False

OWNER_KIND_EVIDENCE_TREE = "evidence_tree"
OWNER_KIND_DURABLE_PAYLOAD = "durable_payload"
OWNER_KIND_RESULT_STORE = "result_store"
OWNER_KIND_MATERIALIZATION_RECEIPT = "materialization_receipt"
OWNER_KIND_VALIDATION_RECEIPT = "validation_receipt"
OWNER_KINDS: frozenset[str] = frozenset(
    {
        OWNER_KIND_EVIDENCE_TREE,
        OWNER_KIND_DURABLE_PAYLOAD,
        OWNER_KIND_RESULT_STORE,
        OWNER_KIND_MATERIALIZATION_RECEIPT,
        OWNER_KIND_VALIDATION_RECEIPT,
    }
)

ACCESS_KIND_WORKSPACE_READ = "workspace_read"
ACCESS_KIND_WORKSPACE_SEARCH = "workspace_search"
ACCESS_KIND_RESULT_HYDRATE = "result_hydrate"
ACCESS_KIND_RECEIPT_READER = "receipt_reader"
ACCESS_KINDS: frozenset[str] = frozenset(
    {
        ACCESS_KIND_WORKSPACE_READ,
        ACCESS_KIND_WORKSPACE_SEARCH,
        ACCESS_KIND_RESULT_HYDRATE,
        ACCESS_KIND_RECEIPT_READER,
    }
)
WRITE_ACCESS_KINDS: frozenset[str] = frozenset({"workspace_write", "evidence_write"})

_ACCESS_MECHANISM: dict[str, str] = {
    ACCESS_KIND_WORKSPACE_READ: "workspace.read(root_ref=authorized-evidence)",
    ACCESS_KIND_WORKSPACE_SEARCH: "workspace.search(root_ref=authorized-evidence)",
    ACCESS_KIND_RESULT_HYDRATE: "result.hydrate (existing result/hydration authority)",
    ACCESS_KIND_RECEIPT_READER: "existing receipt reader seam",
}

MAX_REF_LENGTH = 512
MAX_SCOPE_LENGTH = 512
_MAX_REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._:/#@-]{0,511}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


class AuthorizedEvidenceProjectionError(ValueError):
    """Bounded fail-closed projection error (never authority)."""

    code = "AUTHORIZED_EVIDENCE_PROJECTION_INVALID"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class EvidenceOwner:
    """Classification of one existing evidence/result truth owner.

    ``owner_kind`` selects the projection ref scheme; ``owner`` names the
    physical owner, ``access_path`` the existing access mechanism, and
    ``projection_ref`` the typed ref W4 projects for it.
    """

    owner_kind: str
    owner: str
    access_path: str
    projection_ref: str
    authority_source: str
    write_owner: str


EVIDENCE_OWNERS: tuple[EvidenceOwner, ...] = (
    EvidenceOwner(
        owner_kind=OWNER_KIND_EVIDENCE_TREE,
        owner=".aota-evidence/<project_id>/ operator evidence tree",
        access_path="workspace.read / workspace.search with root_ref authorized-evidence",
        projection_ref="authorized-evidence:<project_id>/<relative_ref>",
        authority_source="trusted evidence base + canonical project identity binding",
        write_owner="existing evidence producers (task-main/steward runs)",
    ),
    EvidenceOwner(
        owner_kind=OWNER_KIND_DURABLE_PAYLOAD,
        owner="worktree .aota/durable_payloads file-backed payload store",
        access_path="result.hydrate",
        projection_ref="tool_output ref + digest",
        authority_source="existing ToolOutputRef + current WorktreeSandboxBoundary scope",
        write_owner="durable_result_store (FileBackedHydrationSource)",
    ),
    EvidenceOwner(
        owner_kind=OWNER_KIND_RESULT_STORE,
        owner="ExecutionStateStore result refs",
        access_path="existing result refs / result.hydrate",
        projection_ref="execution result ref",
        authority_source="ExecutionStateStore record scope",
        write_owner="ExecutionStateStore",
    ),
    EvidenceOwner(
        owner_kind=OWNER_KIND_MATERIALIZATION_RECEIPT,
        owner="MaterializationReceipt",
        access_path="existing receipt seam",
        projection_ref="receipt ref + digest",
        authority_source="receipt record + trusted finalizer facts",
        write_owner="Steward finalizer",
    ),
    EvidenceOwner(
        owner_kind=OWNER_KIND_VALIDATION_RECEIPT,
        owner="task return / milestone review receipts",
        access_path="existing receipt seam",
        projection_ref="receipt ref + digest",
        authority_source="receipt record + trusted review binding",
        write_owner="task-main / reviewer producers",
    ),
)

EVIDENCE_OWNERS_BY_KIND: dict[str, EvidenceOwner] = {
    owner.owner_kind: owner for owner in EVIDENCE_OWNERS
}


def _require_bounded_ref(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise AuthorizedEvidenceProjectionError(f"{label} must be str, got {type(value).__name__}")
    if value != value.strip() or "\x00" in value or "\\" in value:
        raise AuthorizedEvidenceProjectionError(f"{label} must be a bounded canonical reference")
    if not value:
        if allow_empty:
            return ""
        raise AuthorizedEvidenceProjectionError(f"{label} must be non-empty")
    if len(value) > MAX_REF_LENGTH or not _MAX_REF_RE.fullmatch(value):
        raise AuthorizedEvidenceProjectionError(f"{label} is not a bounded reference")
    if value.startswith(("/", "./", "../")) or "/../" in value or value.endswith("/.."):
        raise AuthorizedEvidenceProjectionError(f"{label} must not be path traversal shaped")
    return value


def _require_project_id(value: object, *, label: str = "project_id") -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise AuthorizedEvidenceProjectionError(f"{label} must be str, got {type(value).__name__}")
    if value != value.strip() or not PROJECT_ID_RE.fullmatch(value):
        raise AuthorizedEvidenceProjectionError(
            f"{label} must use the canonical project identity grammar"
        )
    return value


def _require_scope(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise AuthorizedEvidenceProjectionError("scope must be str")
    if value == "":
        return ""
    if value != value.strip() or "\x00" in value or "\\" in value or value.startswith("/"):
        raise AuthorizedEvidenceProjectionError("scope must be a canonical relative subtree")
    if len(value) > MAX_SCOPE_LENGTH:
        raise AuthorizedEvidenceProjectionError("scope exceeds the bounded scope length")
    for segment in value.split("/"):
        if not segment or segment in (".", "..") or not _SCOPE_SEGMENT_RE.fullmatch(segment):
            raise AuthorizedEvidenceProjectionError(
                "scope must not contain empty/traversal/oversized segments"
            )
    return value


def _require_access_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ACCESS_KINDS:
        raise AuthorizedEvidenceProjectionError(
            f"access_kind must be one of {sorted(ACCESS_KINDS)}"
        )
    return value


@dataclass(frozen=True)
class AuthorizedEvidenceItem:
    """One read-only projection over already-existing evidence/result truth."""

    evidence_ref: str
    owner_kind: str
    project_id: str
    access_kind: str
    plan_id: str = ""
    scope: str = ""
    digest: str = ""
    revision: str = ""
    complete: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_ref", _require_bounded_ref(self.evidence_ref, label="evidence_ref")
        )
        if self.owner_kind not in OWNER_KINDS:
            raise AuthorizedEvidenceProjectionError(
                f"owner_kind must be one of {sorted(OWNER_KINDS)}"
            )
        object.__setattr__(self, "project_id", _require_project_id(self.project_id))
        object.__setattr__(self, "access_kind", _require_access_kind(self.access_kind))
        if self.plan_id:
            if not is_plan_id(self.plan_id):
                raise AuthorizedEvidenceProjectionError(
                    "plan_id must be one canonical internal Plan ID when supplied"
                )
        object.__setattr__(self, "scope", _require_scope(self.scope))
        if self.digest:
            if not isinstance(self.digest, str) or not _DIGEST_RE.fullmatch(self.digest):
                raise AuthorizedEvidenceProjectionError("digest must be a SHA-256 hex digest")
        if self.revision:
            if not isinstance(self.revision, str) or not _MAX_REF_RE.fullmatch(self.revision):
                raise AuthorizedEvidenceProjectionError("revision must be a bounded revision ref")
        if type(self.complete) is not bool:
            raise AuthorizedEvidenceProjectionError("complete must be a bool")

    @property
    def access_mechanism(self) -> str:
        return _ACCESS_MECHANISM[self.access_kind]

    @property
    def is_authority(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evidence_ref": self.evidence_ref,
            "owner_kind": self.owner_kind,
            "project_id": self.project_id,
            "access_kind": self.access_kind,
            "plan_id": self.plan_id,
            "scope": self.scope,
            "digest": self.digest,
            "revision": self.revision,
            "complete": self.complete,
            "is_authority": False,
        }
        return payload

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class AuthorizedEvidenceProjection:
    """A bounded typed projection over existing evidence owners.

    The projection is derived metadata only: it duplicates no bytes, creates
    no store, and every item routes back to the original owner mechanism.
    """

    project_id: str
    items: tuple[AuthorizedEvidenceItem, ...]
    complete: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_project_id(self.project_id))
        if not isinstance(self.items, tuple):
            raise AuthorizedEvidenceProjectionError("items must be a tuple")
        for idx, item in enumerate(self.items):
            if not isinstance(item, AuthorizedEvidenceItem):
                raise AuthorizedEvidenceProjectionError(
                    f"items[{idx}] must be AuthorizedEvidenceItem"
                )
            if item.project_id != self.project_id:
                raise AuthorizedEvidenceProjectionError(
                    "projection items must belong to the projection project scope"
                )
        if type(self.complete) is not bool:
            raise AuthorizedEvidenceProjectionError("complete must be a bool")

    @property
    def is_authority(self) -> bool:
        return False

    def by_owner_kind(self) -> dict[str, tuple[AuthorizedEvidenceItem, ...]]:
        grouped: dict[str, list[AuthorizedEvidenceItem]] = {}
        for item in self.items:
            grouped.setdefault(item.owner_kind, []).append(item)
        return {kind: tuple(values) for kind, values in sorted(grouped.items())}

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "items": [item.to_dict() for item in self.items],
            "complete": self.complete,
            "is_authority": False,
            "second_evidence_store_created": False,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


def project_workspace_evidence_item(
    *,
    project_id: object,
    relative_ref: object,
    access_kind: object = ACCESS_KIND_WORKSPACE_READ,
    plan_id: object = "",
    digest: object = "",
    revision: object = "",
    complete: object = False,
) -> AuthorizedEvidenceItem:
    """Project one relative ref of the project-scoped evidence tree.

    The ref is logical only; the caller still needs the trusted
    ``authorized-evidence`` root binding in its AuthorizedRootSet for any
    actual ``workspace.read/search``.
    """
    pid = _require_project_id(project_id)
    ref = _require_bounded_ref(relative_ref, label="relative_ref")
    kind = _require_access_kind(access_kind)
    if kind not in (ACCESS_KIND_WORKSPACE_READ, ACCESS_KIND_WORKSPACE_SEARCH):
        raise AuthorizedEvidenceProjectionError(
            "evidence-tree items route only to workspace.read/search"
        )
    if "/" in ref and any(segment in (".", "..") for segment in ref.split("/")):
        raise AuthorizedEvidenceProjectionError("relative_ref must not contain traversal segments")
    scope = ref if "/" in ref else ""
    return AuthorizedEvidenceItem(
        evidence_ref=f"{AUTHORIZED_EVIDENCE_ROOT_REF}:{pid}/{ref}",
        owner_kind=OWNER_KIND_EVIDENCE_TREE,
        project_id=pid,
        access_kind=kind,
        plan_id=str(plan_id) if plan_id else "",
        scope=scope,
        digest=str(digest) if digest else "",
        revision=str(revision) if revision else "",
        complete=complete,
    )


def project_governed_evidence_item(
    governed_reference: GovernedReference,
    *,
    project_id: object,
    owner_kind: object,
    access_kind: object,
    plan_id: object = "",
    scope: object = "",
    revision: object = "",
    complete: object = False,
) -> AuthorizedEvidenceItem:
    """Project one existing governed evidence reference (receipts/results)."""
    if not isinstance(governed_reference, GovernedReference):
        raise AuthorizedEvidenceProjectionError("governed_reference must be a GovernedReference")
    if governed_reference.kind != GovernedReferenceKind.EVIDENCE:
        raise AuthorizedEvidenceProjectionError(
            "governed evidence projection requires an evidence-kind reference"
        )
    if owner_kind not in (
        OWNER_KIND_RESULT_STORE,
        OWNER_KIND_MATERIALIZATION_RECEIPT,
        OWNER_KIND_VALIDATION_RECEIPT,
    ):
        raise AuthorizedEvidenceProjectionError(
            "governed evidence items must name a result/receipt owner kind"
        )
    kind = _require_access_kind(access_kind)
    if kind not in (ACCESS_KIND_RESULT_HYDRATE, ACCESS_KIND_RECEIPT_READER):
        raise AuthorizedEvidenceProjectionError(
            "governed evidence items route to result.hydrate or the receipt reader only"
        )
    return AuthorizedEvidenceItem(
        evidence_ref=governed_reference.ref,
        owner_kind=owner_kind,
        project_id=_require_project_id(project_id),
        access_kind=kind,
        plan_id=str(plan_id) if plan_id else "",
        scope=_require_scope(scope),
        digest=governed_reference.digest or "",
        revision=str(revision) if revision else "",
        complete=complete,
    )


def project_result_payload_item(
    *,
    result_ref: object,
    project_id: object,
    digest: object = "",
    plan_id: object = "",
    revision: object = "",
    complete: object = True,
) -> AuthorizedEvidenceItem:
    """Project one durable result payload ref (result.hydrate authority)."""
    return AuthorizedEvidenceItem(
        evidence_ref=_require_bounded_ref(result_ref, label="result_ref"),
        owner_kind=OWNER_KIND_DURABLE_PAYLOAD,
        project_id=_require_project_id(project_id),
        access_kind=ACCESS_KIND_RESULT_HYDRATE,
        plan_id=str(plan_id) if plan_id else "",
        scope="",
        digest=str(digest) if digest else "",
        revision=str(revision) if revision else "",
        complete=complete,
    )


def build_authorized_evidence_projection(
    *,
    project_id: object,
    items: object,
    complete: object = False,
) -> AuthorizedEvidenceProjection:
    """Build the bounded projection; never reads bytes and never creates storage."""
    if items is None:
        items_tuple: tuple[AuthorizedEvidenceItem, ...] = ()
    elif isinstance(items, tuple):
        items_tuple = items
    elif isinstance(items, (list, set, frozenset)):
        items_tuple = tuple(items)
    else:
        raise AuthorizedEvidenceProjectionError("items must be an iterable of projection items")
    return AuthorizedEvidenceProjection(
        project_id=_require_project_id(project_id),
        items=items_tuple,
        complete=complete,
    )


__all__ = [
    "EVIDENCE_PROJECTION_IMPLEMENTED",
    "SECOND_EVIDENCE_STORE_CREATED",
    "EVIDENCE_BYTES_DUPLICATED",
    "EVIDENCE_PROJECTION_IS_AUTHORITY",
    "EVIDENCE_REF_IS_AUTHORITY",
    "PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT",
    "AUTHORIZED_EVIDENCE_WRITE_ACCESS",
    "WORKER_DEFAULT_AUTHORIZED_EVIDENCE_GRANT",
    "RESULT_REFS_USE_ORIGINAL_HYDRATION_AUTHORITY",
    "AUTHORIZED_EVIDENCE_PROJECTION_IS_DERIVED",
    "AUTHORIZED_EVIDENCE_ROOT_REF",
    "AUTHORIZED_EVIDENCE_ROOT_IS_TRUSTED_BINDING_ONLY",
    "AUTHORIZED_EVIDENCE_ROOT_IS_PROJECT_SCOPED",
    "AUTHORIZED_EVIDENCE_GLOBAL_BASE_REACHABLE",
    "OWNER_KIND_EVIDENCE_TREE",
    "OWNER_KIND_DURABLE_PAYLOAD",
    "OWNER_KIND_RESULT_STORE",
    "OWNER_KIND_MATERIALIZATION_RECEIPT",
    "OWNER_KIND_VALIDATION_RECEIPT",
    "OWNER_KINDS",
    "ACCESS_KIND_WORKSPACE_READ",
    "ACCESS_KIND_WORKSPACE_SEARCH",
    "ACCESS_KIND_RESULT_HYDRATE",
    "ACCESS_KIND_RECEIPT_READER",
    "ACCESS_KINDS",
    "WRITE_ACCESS_KINDS",
    "MAX_REF_LENGTH",
    "MAX_SCOPE_LENGTH",
    "AuthorizedEvidenceProjectionError",
    "EvidenceOwner",
    "EVIDENCE_OWNERS",
    "EVIDENCE_OWNERS_BY_KIND",
    "AuthorizedEvidenceItem",
    "AuthorizedEvidenceProjection",
    "project_workspace_evidence_item",
    "project_governed_evidence_item",
    "project_result_payload_item",
    "build_authorized_evidence_projection",
]
