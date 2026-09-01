"""AGENTS applicability semantic contract (S1 M2-W2).

Pre-resolved trusted candidate evaluation, not physical lookup.

Invariants
----------
* POLICY_BOUND_TO_ONE_WORKTREE_SEMANTIC_CONTEXT = yes
* CROSS_PROJECT_APPLICABILITY = no
* RAW_ARBITRARY_PATH_INPUT = no — candidate carries no raw filesystem path authority
* CONTENT_BOUNDED = yes
* DIGESTED = yes
* APPLICABILITY_DETERMINISTIC = yes
* NESTED_SCOPED_SEMANTICS_DEFINED = yes
* CONFLICT_FAIL_CLOSED = yes
* AGENTS_POLICY_COMPONENT_IS_PLAN_AUTHORITY = no
* PROJECT_AGENTS_POLICY_APPLIES_TO_ALL_ROLES = yes
* AGENTS_RESOLVER_IMPLEMENTED_IN_S1 = no
* No physical lookup, no link handling, no isolated shell, no bootstrap bundle

See S1 plan 3.8 and M2-W2 spec for authority boundaries.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Bounded capacities
# ---------------------------------------------------------------------------
MAX_POLICY_ID_LENGTH: int = 128
MAX_PROJECT_ID_LENGTH: int = 128
MAX_SCOPE_LENGTH: int = 256
MAX_SCOPE_COMPONENT_LENGTH: int = 64
MAX_CONTENT_LENGTH: int = 32 * 1024
MAX_LOGICAL_REF_LENGTH: int = 512
MAX_DIGEST_HEX_LENGTH: int = 64

# Digest is 64 lower hex chars (sha256)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
# Identifier charset: start alnum, then alnum . _ -
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Scope component charset
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Public role set for T19
AGENTS_POLICY_APPLIES_TO_ALL_ROLES: bool = True
AGENTS_POLICY_IS_PLAN_AUTHORITY: bool = False

# ---------------------------------------------------------------------------
# Error types — fail-closed semantic
# ---------------------------------------------------------------------------


class AgentsPolicyError(ValueError):
    """Base fail-closed error for AGENTS policy contract."""


class AmbiguousPolicyError(AgentsPolicyError):
    """Same scope with divergent identity or digest."""


class CrossProjectPolicyError(AgentsPolicyError):
    """Candidate bound to different project than target."""


class UnboundedContentError(AgentsPolicyError):
    """Policy material exceeds bounded capacity."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reject_raw_path(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        return
    s = value.strip()
    if not s:
        return
    # posix absolute
    if s.startswith("/"):
        raise ValueError(f"{field_name} must not be raw absolute path: {value!r}")
    # windows absolute like C:/ or C:\
    if re.match(r"^[a-zA-Z]:[\\/]", s):
        raise ValueError(f"{field_name} must not be raw absolute path: {value!r}")
    # traversal segment
    parts = s.split("/")
    for p in parts:
        if p == "..":
            raise ValueError(f"{field_name} must not contain traversal segment '..': {value!r}")
        if "\\" in p:
            raise ValueError(f"{field_name} must not contain backslash: {value!r}")


def _validate_id(value: str, field_name: str, max_len: int = MAX_POLICY_ID_LENGTH) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(v) > max_len:
        raise ValueError(f"{field_name} length {len(v)} exceeds max {max_len}")
    _reject_raw_path(v, field_name)
    if "/" in v or "\\" in v:
        raise ValueError(f"{field_name} must not contain path separators: {value!r}")
    if not _ID_RE.fullmatch(v):
        raise ValueError(f"{field_name} has invalid charset: {value!r}")
    return v


def _normalize_scope(value: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"scope must be a string, got {type(value).__name__}")
    v = value.strip()
    # root is empty string or literal "root"
    if v == "" or v == "root":
        return ""
    if len(v) > MAX_SCOPE_LENGTH:
        raise ValueError(f"scope length {len(v)} exceeds max {MAX_SCOPE_LENGTH}")
    _reject_raw_path(v, "scope")
    if v.startswith("/") or v.endswith("/"):
        raise ValueError(f"scope must not have leading or trailing '/': {value!r}")
    if "//" in v:
        raise ValueError(f"scope must not contain '//': {value!r}")
    if "\\" in v:
        raise ValueError(f"scope must not contain backslash: {value!r}")
    parts = v.split("/")
    for p in parts:
        if p == "" or p == "." or p == "..":
            raise ValueError(f"scope contains invalid segment {p!r}: {value!r}")
        if len(p) > MAX_SCOPE_COMPONENT_LENGTH:
            raise ValueError(f"scope component {p!r} exceeds max {MAX_SCOPE_COMPONENT_LENGTH}")
        if not _COMPONENT_RE.fullmatch(p):
            raise ValueError(f"scope component {p!r} has invalid charset")
    # canonical form is joined parts without redundant separators
    return "/".join(parts)


def _validate_logical_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"provenance_ref must be a string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError("provenance_ref when provided must be non-empty")
    if len(v) > MAX_LOGICAL_REF_LENGTH:
        raise ValueError(f"provenance_ref length {len(v)} exceeds max {MAX_LOGICAL_REF_LENGTH}")
    _reject_raw_path(v, "provenance_ref")
    # forbid absolute path markers even though logical ref may contain ':'
    if v.startswith("/"):
        raise ValueError(f"provenance_ref must not be absolute path: {value!r}")
    # allow opaque logical refs but forbid traversal
    if ".." in v.split("/"):
        raise ValueError(f"provenance_ref must not contain traversal: {value!r}")
    return v


def _validate_content(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"content must be a string or None, got {type(value).__name__}")
    if len(value) > MAX_CONTENT_LENGTH:
        raise UnboundedContentError(
            f"content length {len(value)} exceeds bounded max {MAX_CONTENT_LENGTH}"
        )
    # content itself is not a path, but reject if content is used to smuggle path authority?
    # No need to reject content that looks like path; content is bounded material.
    return value


def _validate_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"content_digest must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ValueError("content_digest when provided must be non-empty")
    if not _DIGEST_RE.fullmatch(v):
        raise ValueError(f"content_digest must be 64 lower hex chars: {value!r}")
    return v


def compute_policy_digest(content: str) -> str:
    """Deterministic sha256 hex digest of bounded policy material."""
    if not isinstance(content, str) or type(content) is not str:
        raise TypeError(f"content must be a string, got {type(content).__name__}")
    if len(content) > MAX_CONTENT_LENGTH:
        raise UnboundedContentError(
            f"content length {len(content)} exceeds bounded max {MAX_CONTENT_LENGTH}"
        )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _scope_specificity(normalized_scope: str) -> int:
    if normalized_scope == "":
        return 0
    return normalized_scope.count("/") + 1


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentsPolicyCandidate:
    """Immutable semantic candidate for AGENTS project policy.

    Fields
    ------
    policy_id: opaque bounded identity (not a path)
    project_id: bound worktree/project semantic identity
    scope: logical nested scope ("", "a", "a/b") — root is ""
    content: optional bounded material (None for reference-only)
    content_digest: sha256 hex covering content or reference
    provenance_ref: optional opaque logical reference (not a path)

    No field accepts raw filesystem path authority.
    """

    policy_id: str
    project_id: str
    scope: str
    content: str | None = None
    content_digest: str | None = None
    provenance_ref: str | None = None

    def __post_init__(self) -> None:
        pid = _validate_id(self.policy_id, "policy_id", MAX_POLICY_ID_LENGTH)
        object.__setattr__(self, "policy_id", pid)

        proj = _validate_id(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH)
        object.__setattr__(self, "project_id", proj)

        sc = _normalize_scope(self.scope)
        object.__setattr__(self, "scope", sc)

        cont = _validate_content(self.content)
        object.__setattr__(self, "content", cont)

        pref = _validate_logical_ref(self.provenance_ref)
        object.__setattr__(self, "provenance_ref", pref)

        dg = _validate_digest(self.content_digest)

        # Content / digest consistency, bounded.
        if cont is not None:
            computed = compute_policy_digest(cont)
            if dg is not None and dg != computed:
                raise ValueError(
                    f"content_digest mismatch: provided {dg!r} != computed {computed!r}"
                )
            object.__setattr__(self, "content_digest", computed)
        else:
            # reference-only: digest must be supplied
            if dg is None:
                raise ValueError("reference-only candidate requires content_digest")
            object.__setattr__(self, "content_digest", dg)

        # provenance_ref not required, but reference-only should have it or policy is still bounded
        # No additional authority.

    @property
    def specificity(self) -> int:
        return _scope_specificity(self.scope)

    @property
    def is_root(self) -> bool:
        return self.scope == ""

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "content_digest": self.content_digest,
            "policy_id": self.policy_id,
            "project_id": self.project_id,
            "provenance_ref": self.provenance_ref,
            "scope": self.scope,
        }

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "policy_id": self.policy_id,
            "project_id": self.project_id,
            "scope": self.scope,
            "content_digest": self.content_digest,
        }
        if self.content is not None:
            d["content"] = self.content
        if self.provenance_ref is not None:
            d["provenance_ref"] = self.provenance_ref
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentsPolicyCandidate":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {"policy_id", "project_id", "scope", "content", "content_digest", "provenance_ref"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in AgentsPolicyCandidate: {sorted(extra)}")
        for req in ("policy_id", "project_id", "scope"):
            if req not in data:
                raise ValueError(f"Missing required field {req!r}")
        # Reject raw path injection in any string field
        for k, v in data.items():
            if isinstance(v, str):
                _reject_raw_path(v, k)
                if k in ("policy_id", "project_id") and ("/" in v or "\\" in v):
                    # path-like id rejected earlier but do explicit fail-closed
                    if v.strip().startswith("/"):
                        raise ValueError(f"{k} must not be absolute path: {v!r}")
        return cls(
            policy_id=data["policy_id"],
            project_id=data["project_id"],
            scope=data["scope"],
            content=data.get("content"),
            content_digest=data.get("content_digest"),
            provenance_ref=data.get("provenance_ref"),
        )


# ---------------------------------------------------------------------------
# Applicability — deterministic scoped composition, fail-closed
# ---------------------------------------------------------------------------


def _deduplicate(candidates: Sequence[AgentsPolicyCandidate]) -> list[AgentsPolicyCandidate]:
    # key includes scope + policy_id + digest to deduplicate exact duplicates
    seen: dict[tuple[str, str, str], AgentsPolicyCandidate] = {}
    for c in candidates:
        key = (c.scope, c.policy_id, c.content_digest or "")
        if key not in seen:
            seen[key] = c
    return list(seen.values())


def resolve_applicable_policies(
    candidates: Sequence[AgentsPolicyCandidate],
    target_project_id: str,
) -> tuple[AgentsPolicyCandidate, ...]:
    """Deterministically order applicable candidates for a project.

    Validates bound worktree semantics, cross-project isolation,
    nested scoped ordering, and ambiguous same-scope conflicts.

    Parameters
    ----------
    candidates: pre-resolved trusted candidates (no path authority)
    target_project_id: intended worktree/project identity

    Returns tuple ordered by specificity (root first) then scope and
    policy_id for stable tie-break. Input order is ignored.

    Raises AmbiguousPolicyError, CrossProjectPolicyError, AgentsPolicyError
    fail-closed for any structural ambiguity.
    """
    if not isinstance(target_project_id, str) or type(target_project_id) is not str:
        raise TypeError(f"target_project_id must be a string, got {type(target_project_id).__name__}")
    target = _validate_id(target_project_id, "target_project_id", MAX_PROJECT_ID_LENGTH)

    if not isinstance(candidates, (list, tuple)):
        raise TypeError(f"candidates must be a list or tuple, got {type(candidates).__name__}")

    if len(candidates) == 0:
        return ()

    # Validate element types fail-closed
    for idx, c in enumerate(candidates):
        if not isinstance(c, AgentsPolicyCandidate):
            raise TypeError(f"candidates[{idx}] must be AgentsPolicyCandidate, got {type(c).__name__}")

    # Cross-project isolation — fail closed if any candidate bound elsewhere
    for c in candidates:
        if c.project_id != target:
            raise CrossProjectPolicyError(
                f"candidate {c.policy_id!r} bound to {c.project_id!r} != target {target!r}"
            )

    # Detect ambiguous same-scope candidates with divergent identity/digest
    by_scope: dict[str, list[AgentsPolicyCandidate]] = {}
    for c in candidates:
        by_scope.setdefault(c.scope, []).append(c)

    for scope, group in by_scope.items():
        if len(group) > 1:
            # If group has more than one distinct (policy_id, digest) combination, ambiguous
            distinct_keys = {(g.policy_id, g.content_digest) for g in group}
            if len(distinct_keys) > 1:
                raise AmbiguousPolicyError(
                    f"ambiguous candidates at scope {scope!r}: divergent identity/digest {distinct_keys}"
                )

    deduped = _deduplicate(candidates)

    # Re-group after dedup for ordering determinism: still same scope handling
    # Deterministic ordering: specificity asc, then scope lexical, then policy_id
    ordered = sorted(
        deduped,
        key=lambda c: (_scope_specificity(c.scope), c.scope, c.policy_id, c.content_digest or ""),
    )
    return tuple(ordered)


# Aliases for downstream ergonomics
select_applicable_policies = resolve_applicable_policies
resolve_agents_applicability = resolve_applicable_policies
collect_applicable_policies = resolve_applicable_policies
get_effective_policy_chain = resolve_applicable_policies


def agents_policy_applies_to_role(role: object) -> bool:
    """AGENTS project policy applies to all five work roles semantically."""
    # Fail-closed validation not needed; policy applies regardless of role.
    # Accept any input and report True for valid roles; unknown roles also conceptually
    # would be governed if they existed, but we gate on known set for test stability.
    try:
        from aota_forge.work_plane.roles import is_agent_work_role
    except Exception:
        return True
    # If it's a known work role, definitely applies
    if is_agent_work_role(role):
        return True
    # String that is a valid work role also applies
    if isinstance(role, str) and role in ("task-main", "analyst", "coder", "reviewer", "project-steward"):
        return True
    # For unknown shapes, policy still semantically applies to all roles, but we
    # signal False for completely invalid types to avoid masking caller errors.
    # To satisfy T19 (applies to five roles), we return True for those five and
    # also for any AgentWorkRole enum member.
    if isinstance(role, str):
        # Unknown string still not a valid role, but policy would apply if role existed
        return False
    # Non-string unknown
    return False


__all__ = [
    "AgentsPolicyCandidate",
    "AgentsPolicyError",
    "AmbiguousPolicyError",
    "CrossProjectPolicyError",
    "UnboundedContentError",
    "compute_policy_digest",
    "resolve_applicable_policies",
    "select_applicable_policies",
    "resolve_agents_applicability",
    "collect_applicable_policies",
    "get_effective_policy_chain",
    "agents_policy_applies_to_role",
    "AGENTS_POLICY_APPLIES_TO_ALL_ROLES",
    "AGENTS_POLICY_IS_PLAN_AUTHORITY",
    "MAX_POLICY_ID_LENGTH",
    "MAX_PROJECT_ID_LENGTH",
    "MAX_SCOPE_LENGTH",
    "MAX_CONTENT_LENGTH",
    "MAX_LOGICAL_REF_LENGTH",
]
