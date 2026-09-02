"""Bounded Role Tool Surface — S2 M2-W1.

Thin semantic projection:

    AgentWorkRole + existing canonical Tool capability identity + task/policy context
        ↓
    Tool visibility / exposure surface
        ↓
    eager Tool visibility + progressive specialized Tool references

Invariants
----------
* visibility != authority
* TOOL_IDENTITY_IS_AUTHORITY=no
* TOOL_EXPOSURE_IS_AUTHORITY=no
* ROLE_TOOL_SURFACE_IS_AUTHORITY=no
* WORK_ROLE_IS_TOOL_PERMISSION=no
* TOOL_METADATA_IS_OPERATION_AUTHORITY=no
* ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY=yes
* ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY=yes
* ROLE_EAGER_TOOL_SURFACE=yes
* SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE=yes
* DISCOVER_EVERY_TOOL_AT_BOOTSTRAP=no
* PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED=yes
* PROGRESSIVE_TOOL_REF_BOUNDED=yes
* PROGRESSIVE_TOOL_REF_IS_AUTHORITY=no
* ROLE_TOOL_SURFACE_DETERMINISTIC=yes
* ROLE_TOOL_SURFACE_BOUNDED=yes
* DUPLICATE_TOOL_SURFACE_ENTRY_DETERMINISTIC=yes
* CONFLICTING_TOOL_SURFACE_ENTRY_FAIL_CLOSED=yes
* UNKNOWN_WORK_ROLE_FAIL_CLOSED=yes
* UNKNOWN_TOOL_CAPABILITY_AUTO_CREATED=no
* TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY=no
* ROLE_DEFAULT_SURFACE_IS_SECURITY_ACL=no
* VISIBILITY_AND_AUTHORIZATION_BIDIRECTIONAL_EQUIVALENCE=no
* OPERATION_DESCRIPTOR_IS_AUTHORITY_DECLARATION=yes
* OPERATION_DESCRIPTOR_IS_RUNTIME_AUTHORITY_DECISION=no
* AGENTS_TEXT_AUTO_EXPOSES_TOOL=no
* AGENTS_TEXT_GRANTS_TOOL_AUTHORITY=no
* SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY=no

Reuse
-----
* Reuses AgentWorkRole (exactly five values, no new role)
* Reuses OperationContractDescriptor.name as canonical Tool capability identity
* Reuses ToolProvider / ToolRequest / ToolResponse contracts unchanged
* Does NOT create ToolRegistry / PermissionMatrix / ToolRuntime / PolicyEngine
* Does NOT implement workspace.read/search or Tool Result Governance
* Does NOT invoke ToolProvider

Determinism
-----------
Equivalent (AgentWorkRole + same capability inputs + same context) yields
deterministic exposure projection. No dependency on dict insertion order,
filesystem enumeration, cwd, object id, or mtime.

Boundedness
-----------
All public collections are explicitly bounded by construction. No unbounded
scan of all possible Tools. Progressive refs are bounded logical refs with
minimal display metadata and optional integrity digest.

Authority separation
--------------------
A role surface may determine eagerly visible common Tools and progressively
discoverable specialized Tool refs. It must NOT determine read/write
permission, mutation authority, approval, filesystem/shell/git authorization.
Visible != authorized; not visible yet != forbidden.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Public invariant flags (for tests / downstream seam)
# ---------------------------------------------------------------------------

TOOL_IDENTITY_IS_AUTHORITY: bool = False
TOOL_EXPOSURE_IS_AUTHORITY: bool = False
ROLE_TOOL_SURFACE_IS_AUTHORITY: bool = False
WORK_ROLE_IS_TOOL_PERMISSION: bool = False
TOOL_METADATA_IS_OPERATION_AUTHORITY: bool = False
ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY: bool = True

ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY: bool = True

ROLE_EAGER_TOOL_SURFACE: bool = True
SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE: bool = True
DISCOVER_EVERY_TOOL_AT_BOOTSTRAP: bool = False
PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED: bool = True
PROGRESSIVE_TOOL_REF_BOUNDED: bool = True
PROGRESSIVE_TOOL_REF_IS_AUTHORITY: bool = False

ROLE_TOOL_SURFACE_DETERMINISTIC: bool = True
ROLE_TOOL_SURFACE_BOUNDED: bool = True

DUPLICATE_TOOL_SURFACE_ENTRY_DETERMINISTIC: bool = True
CONFLICTING_TOOL_SURFACE_ENTRY_FAIL_CLOSED: bool = True
UNKNOWN_WORK_ROLE_FAIL_CLOSED: bool = True
UNKNOWN_TOOL_CAPABILITY_AUTO_CREATED: bool = False

TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY: bool = False
ROLE_DEFAULT_SURFACE_IS_SECURITY_ACL: bool = False
VISIBILITY_AND_AUTHORIZATION_BIDIRECTIONAL_EQUIVALENCE: bool = False

OPERATION_DESCRIPTOR_IS_AUTHORITY_DECLARATION: bool = True
OPERATION_DESCRIPTOR_IS_RUNTIME_AUTHORITY_DECISION: bool = False

AGENTS_TEXT_AUTO_EXPOSES_TOOL: bool = False
AGENTS_TEXT_GRANTS_TOOL_AUTHORITY: bool = False
SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY: bool = False

# Reuse markers
EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_REQUEST_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_OPERATION_DESCRIPTOR_REUSED: bool = True
DUPLICATE_TOOL_IDENTITY_NAMESPACE_CREATED: bool = False

# Negative scope markers (must remain false/zero)
NEW_TOOL_RUNTIME_CREATED: bool = False
NEW_PERMISSION_ENGINE_CREATED: bool = False
NEW_TOOL_REGISTRY_REQUIRED: bool = False
DYNAMIC_TOOL_PLUGIN_DISCOVERY: bool = False
PERSISTENT_TOOL_REGISTRY: bool = False
THIRD_RESULT_ONTOLOGY_CREATED: bool = False
NEW_TOOL_STATE_MACHINE_CREATED: bool = False
NEW_TOOL_JOURNAL_CREATED: bool = False
TOOL_TELEMETRY_STORE_CREATED: bool = False
WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1: bool = False
WORKSPACE_SEARCH_TOOL_IMPLEMENTED_IN_W1: bool = False
TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W1: bool = False
LIVE_TOOL_EXECUTION_IMPLEMENTED_IN_W1: bool = False
S3_IMPLEMENTATION_INTRODUCED: bool = False

# ---------------------------------------------------------------------------
# Bounds (local Milestone constants, not Child Plan authority)
# ---------------------------------------------------------------------------

MAX_CAPABILITY_NAME_LENGTH: int = 128
MAX_DISPLAY_NAME_LENGTH: int = 64
MAX_DESCRIPTION_LENGTH: int = 256
MAX_DIGEST_LENGTH: int = 128
MAX_EAGER_CAPABILITIES: int = 16
MAX_PROGRESSIVE_REFS: int = 32
MAX_TOTAL_CAPABILITIES: int = 32
MAX_TOOL_SURFACE_CANONICAL_BYTES: int = 16 * 1024

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# capability_name charset: mirror descriptor name permissively but bound and no path separators
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class ToolSurfaceError(ValueError):
    """Base fail-closed error for role tool surface."""


class UnknownWorkRoleError(ToolSurfaceError):
    """Unknown work role (fail-closed)."""


class ToolCapabilityError(ToolSurfaceError):
    """Malformed Tool capability identity."""


class ToolSurfaceConflictError(ToolSurfaceError):
    """Conflicting duplicate capability metadata (fail-closed)."""


class ToolSurfaceBoundError(ToolSurfaceError):
    """Oversized surface or metadata (fail-closed)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_capability_name(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ToolCapabilityError(f"capability_name must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ToolCapabilityError("capability_name must be non-empty")
    if v != value.strip():
        # if original had leading/trailing whitespace, we already stripped but original != stripped means input had whitespace border -> still accept normalized? Actually spec says fail-closed on blank/invalid bounded refs. We normalize stripped but if original had surrounding whitespace it's still valid after strip? We'll allow stripped canonical.
        pass
    if len(v) > MAX_CAPABILITY_NAME_LENGTH:
        raise ToolSurfaceBoundError(f"capability_name length {len(v)} exceeds {MAX_CAPABILITY_NAME_LENGTH}")
    if "\x00" in v:
        raise ToolCapabilityError("capability_name must not contain NUL")
    if "/" in v or "\\" in v:
        raise ToolCapabilityError(f"capability_name must not contain path separators: {v!r}")
    if v in (".", ".."):
        raise ToolCapabilityError(f"capability_name must not be '.' or '..': {v!r}")
    # also reject if blank after strip (already)
    # charset check — must match allowed pattern
    if not _CAPABILITY_RE.fullmatch(v):
        raise ToolCapabilityError(f"capability_name has invalid charset or format: {v!r}")
    return v


def _validate_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise ToolCapabilityError(f"display_name must be string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ToolCapabilityError("display_name when provided must be non-empty")
    if len(v) > MAX_DISPLAY_NAME_LENGTH:
        raise ToolSurfaceBoundError(f"display_name length {len(v)} exceeds {MAX_DISPLAY_NAME_LENGTH}")
    if "\x00" in v:
        raise ToolCapabilityError("display_name must not contain NUL")
    if "/" in v and v.startswith("/"):
        raise ToolCapabilityError("display_name must not be absolute path")
    return v


def _validate_description(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise ToolCapabilityError(f"description must be string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ToolCapabilityError("description when provided must be non-empty")
    if len(v) > MAX_DESCRIPTION_LENGTH:
        raise ToolSurfaceBoundError(f"description length {len(v)} exceeds {MAX_DESCRIPTION_LENGTH}")
    if "\x00" in v:
        raise ToolCapabilityError("description must not contain NUL")
    return v


def _validate_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise ToolCapabilityError(f"digest must be string or None, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ToolCapabilityError("digest when provided must be non-empty")
    if len(v) > MAX_DIGEST_LENGTH:
        raise ToolSurfaceBoundError(f"digest length {len(v)} exceeds {MAX_DIGEST_LENGTH}")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ToolCapabilityError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _validate_work_role(role: object) -> AgentWorkRole:
    # Reuse canonical validation — unknown fails closed, never maps to default
    try:
        return parse_agent_work_role(role)
    except (TypeError, ValueError) as exc:
        # Normalize to our domain errors for tests to assert fail-closed
        if isinstance(exc, TypeError):
            raise UnknownWorkRoleError(str(exc)) from exc
        raise UnknownWorkRoleError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCapabilityRef:
    """Bounded non-authoritative Tool capability reference.

    Points back to existing OperationContractDescriptor.name as stable
    canonical identity. Carries only bounded logical ref + minimal display
    metadata + optional integrity digest. Does NOT grant authority.
    """

    capability_name: str
    display_name: str | None = None
    description: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        cn = _validate_capability_name(self.capability_name)
        object.__setattr__(self, "capability_name", cn)
        dn = _validate_display_name(self.display_name)
        object.__setattr__(self, "display_name", dn)
        desc = _validate_description(self.description)
        object.__setattr__(self, "description", desc)
        dg = _validate_digest(self.digest)
        object.__setattr__(self, "digest", dg)

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"capability_name": self.capability_name}
        if self.display_name is not None:
            d["display_name"] = self.display_name
        if self.description is not None:
            d["description"] = self.description
        if self.digest is not None:
            d["digest"] = self.digest
        return d

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolCapabilityRef":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"capability_name", "display_name", "description", "digest"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ToolCapabilityError(f"Unknown field(s) in ToolCapabilityRef: {sorted(extra)}")
        if "capability_name" not in data:
            raise ToolCapabilityError("Missing capability_name")
        return cls(
            capability_name=data["capability_name"],
            display_name=data.get("display_name"),
            description=data.get("description"),
            digest=data.get("digest"),
        )

    @classmethod
    def from_descriptor(
        cls,
        descriptor: OperationContractDescriptor,
        *,
        display_name: str | None = None,
        description: str | None = None,
        digest: str | None = None,
    ) -> "ToolCapabilityRef":
        """Create a bounded ref reusing existing OperationContractDescriptor identity."""
        if not isinstance(descriptor, OperationContractDescriptor):
            raise ToolCapabilityError(f"descriptor must be OperationContractDescriptor, got {type(descriptor).__name__}")
        # Validate descriptor is well-formed
        descriptor.validate()
        # Use descriptor.name as canonical identity
        cn = descriptor.name
        # If digest not supplied, use contract_hash as integrity identity (optional)
        dg = digest if digest is not None else None
        # Allow caller to supply digest; if not supplied and descriptor has deterministic hash, caller may choose to include
        # We expose helper to compute digest from descriptor
        return cls(
            capability_name=cn,
            display_name=display_name if display_name is not None else None,
            description=description if description is not None else descriptor.description[:MAX_DESCRIPTION_LENGTH] if descriptor.description else None,
            digest=dg,
        )

    @classmethod
    def from_descriptor_with_digest(
        cls,
        descriptor: OperationContractDescriptor,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> "ToolCapabilityRef":
        if not isinstance(descriptor, OperationContractDescriptor):
            raise ToolCapabilityError(f"descriptor must be OperationContractDescriptor, got {type(descriptor).__name__}")
        descriptor.validate()
        dg = descriptor.contract_hash()
        return cls(
            capability_name=descriptor.name,
            display_name=display_name,
            description=description if description is not None else descriptor.description[:MAX_DESCRIPTION_LENGTH],
            digest=dg,
        )


@dataclass(frozen=True)
class ToolRoleSurface:
    """Bounded deterministic Tool visibility surface for one AgentWorkRole.

    Holds eagerly visible common Tools and progressively discoverable
    specialized Tool refs. Both collections are bounded, deterministically
    ordered, and carry no authority. Visible != authorized.

    Construction is fail-closed and deterministic: equivalent inputs yield
    identical canonical form regardless of input ordering.
    """

    work_role: AgentWorkRole
    eager: tuple[ToolCapabilityRef, ...]
    progressive: tuple[ToolCapabilityRef, ...]

    def __post_init__(self) -> None:
        # Validate work_role
        wr = _validate_work_role(self.work_role)
        object.__setattr__(self, "work_role", wr)

        # Normalize eager / progressive to tuples of ToolCapabilityRef
        if not isinstance(self.eager, (tuple, list)):
            raise TypeError(f"eager must be tuple or list, got {type(self.eager).__name__}")
        if not isinstance(self.progressive, (tuple, list)):
            raise TypeError(f"progressive must be tuple or list, got {type(self.progressive).__name__}")

        # Validate each entry is ToolCapabilityRef (or fail-closed)
        def _coerce_entry(entry: object, field: str, idx: int) -> ToolCapabilityRef:
            if isinstance(entry, ToolCapabilityRef):
                return entry
            if isinstance(entry, OperationContractDescriptor):
                # Convenience: auto-convert descriptor to ref
                return ToolCapabilityRef.from_descriptor(entry)
            if isinstance(entry, str) and type(entry) is str:
                # Bare string as capability_name
                return ToolCapabilityRef(capability_name=entry)
            if isinstance(entry, Mapping):
                return ToolCapabilityRef.from_dict(entry)  # type: ignore[arg-type]
            raise ToolCapabilityError(f"{field}[{idx}] must be ToolCapabilityRef, OperationContractDescriptor, str, or mapping, got {type(entry).__name__}")

        raw_eager = tuple(_coerce_entry(e, "eager", i) for i, e in enumerate(self.eager))
        raw_prog = tuple(_coerce_entry(e, "progressive", i) for i, e in enumerate(self.progressive))

        # Bound checks
        if len(raw_eager) > MAX_EAGER_CAPABILITIES:
            raise ToolSurfaceBoundError(f"eager count {len(raw_eager)} exceeds {MAX_EAGER_CAPABILITIES}")
        if len(raw_prog) > MAX_PROGRESSIVE_REFS:
            raise ToolSurfaceBoundError(f"progressive count {len(raw_prog)} exceeds {MAX_PROGRESSIVE_REFS}")
        if len(raw_eager) + len(raw_prog) > MAX_TOTAL_CAPABILITIES:
            raise ToolSurfaceBoundError(f"total capabilities {len(raw_eager)+len(raw_prog)} exceeds {MAX_TOTAL_CAPABILITIES}")

        # Deterministic deduplication within each set
        def _dedup_and_check(entries: tuple[ToolCapabilityRef, ...], field: str) -> tuple[ToolCapabilityRef, ...]:
            # Map capability_name -> canonical metadata tuple
            seen: dict[str, ToolCapabilityRef] = {}
            for ref in entries:
                key = ref.capability_name
                if key not in seen:
                    seen[key] = ref
                else:
                    existing = seen[key]
                    # identical duplicate: same metadata -> deterministic dedup (keep first, ignore order)
                    if existing == ref:
                        continue
                    # conflicting duplicate metadata -> fail closed (input order must not choose winner)
                    raise ToolSurfaceConflictError(
                        f"conflicting duplicate capability metadata in {field}: {key!r} existing {existing.canonical_dict()} != new {ref.canonical_dict()}"
                    )
            # Return deterministically sorted by capability_name then digest then display
            sorted_refs = sorted(seen.values(), key=lambda r: (r.capability_name, r.digest or "", r.display_name or "", r.description or ""))
            return tuple(sorted_refs)

        dedup_eager = _dedup_and_check(raw_eager, "eager")
        dedup_prog = _dedup_and_check(raw_prog, "progressive")

        # Cross-set distinctness: eager and progressive must remain distinct (no overlap)
        eager_names = {r.capability_name for r in dedup_eager}
        prog_names = {r.capability_name for r in dedup_prog}
        overlap = eager_names & prog_names
        if overlap:
            # Fail closed — sets must remain distinct. Even identical duplicates across sets are not allowed to silently duplicate visibility.
            raise ToolSurfaceConflictError(f"capability appears in both eager and progressive (must remain distinct): {sorted(overlap)}")

        # Final bounded canonical size check
        # Use deterministic canonical dict to compute bytes
        tmp_dict = {
            "work_role": wr.value,
            "eager": [r.canonical_dict() for r in dedup_eager],
            "progressive": [r.canonical_dict() for r in dedup_prog],
        }
        size = len(canonical_json(tmp_dict).encode("utf-8"))
        if size > MAX_TOOL_SURFACE_CANONICAL_BYTES:
            raise ToolSurfaceBoundError(f"surface canonical size {size} exceeds {MAX_TOOL_SURFACE_CANONICAL_BYTES}")

        object.__setattr__(self, "eager", dedup_eager)
        object.__setattr__(self, "progressive", dedup_prog)

    # -----------------------------------------------------------------------
    # Visibility helpers (non-authoritative)
    # -----------------------------------------------------------------------

    def is_visible(self, capability_name: str) -> bool:
        """Return True iff capability is either eager or progressive (visibility only)."""
        cn = _validate_capability_name(capability_name)
        return any(r.capability_name == cn for r in self.eager) or any(r.capability_name == cn for r in self.progressive)

    def is_eager(self, capability_name: str) -> bool:
        cn = _validate_capability_name(capability_name)
        return any(r.capability_name == cn for r in self.eager)

    def is_progressive(self, capability_name: str) -> bool:
        cn = _validate_capability_name(capability_name)
        return any(r.capability_name == cn for r in self.progressive)

    def all_capability_names(self) -> tuple[str, ...]:
        """Deterministic tuple of all capability names (eager + progressive)."""
        names = [r.capability_name for r in self.eager] + [r.capability_name for r in self.progressive]
        return tuple(sorted(names))

    # Authority is never granted by surface — explicit marker
    @property
    def is_authority(self) -> bool:
        return False

    def authorize(self, *args: Any, **kwargs: Any) -> None:
        """No authorization engine in W1. This method always fails closed to prove visibility != authority."""
        raise NotImplementedError("ToolRoleSurface does not grant authority; visibility != authority")

    # -----------------------------------------------------------------------
    # Canonical representation
    # -----------------------------------------------------------------------

    def canonical_dict(self) -> dict[str, Any]:
        # Already sorted deterministically
        return {
            "work_role": self.work_role.value,
            "eager": [r.canonical_dict() for r in self.eager],
            "progressive": [r.canonical_dict() for r in self.progressive],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def canonical_bytes(self) -> bytes:
        return self.canonical_json().encode("utf-8")

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolRoleSurface":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"work_role", "eager", "progressive"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ToolSurfaceError(f"Unknown field(s) in ToolRoleSurface: {sorted(extra)}")
        if "work_role" not in data or "eager" not in data or "progressive" not in data:
            raise ToolSurfaceError("Missing required field in ToolRoleSurface")
        work_role = data["work_role"]
        eager_data = data["eager"]
        prog_data = data["progressive"]
        if not isinstance(eager_data, (list, tuple)):
            raise TypeError("eager must be list or tuple")
        if not isinstance(prog_data, (list, tuple)):
            raise TypeError("progressive must be list or tuple")
        eager_refs = tuple(ToolCapabilityRef.from_dict(v) if isinstance(v, Mapping) else ToolCapabilityRef(capability_name=v) for v in eager_data)  # type: ignore
        prog_refs = tuple(ToolCapabilityRef.from_dict(v) if isinstance(v, Mapping) else ToolCapabilityRef(capability_name=v) for v in prog_data)  # type: ignore
        return cls(work_role=work_role, eager=eager_refs, progressive=prog_refs)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolRoleSurface):
            return NotImplemented
        return self.canonical_dict() == other.canonical_dict()

    def __hash__(self) -> int:
        return hash(self.canonical_json())


# ---------------------------------------------------------------------------
# Factory — deterministic bounded creation
# ---------------------------------------------------------------------------


def create_role_tool_surface(
    work_role: AgentWorkRole | str,
    *,
    eager: Sequence[ToolCapabilityRef | OperationContractDescriptor | str | Mapping[str, Any]] = (),
    progressive: Sequence[ToolCapabilityRef | OperationContractDescriptor | str | Mapping[str, Any]] = (),
) -> ToolRoleSurface:
    """Create a bounded deterministic role tool surface (visibility only).

    Parameters
    ----------
    work_role: AgentWorkRole | str
        Exactly one of the five canonical work roles. Unknown roles fail closed.
    eager: sequence
        Eagerly visible common Tools (bounded).
    progressive: sequence
        Progressively discoverable specialized Tool refs (bounded).

    Returns
    -------
    ToolRoleSurface
        Non-authoritative exposure projection.

    Fail-closed
    -----------
    Unknown work role, malformed capability identity, conflicting duplicate
    metadata, blank/invalid bounded refs, or oversized surface raise.
    """
    # Validate role before any other work
    wr = _validate_work_role(work_role)
    # Convert to refs handled inside ToolRoleSurface
    return ToolRoleSurface(work_role=wr, eager=tuple(eager), progressive=tuple(progressive))


def create_role_tool_surface_from_descriptors(
    work_role: AgentWorkRole | str,
    *,
    eager_descriptors: Sequence[OperationContractDescriptor] = (),
    progressive_descriptors: Sequence[OperationContractDescriptor] = (),
    include_digest: bool = False,
) -> ToolRoleSurface:
    """Convenience: build surface directly from existing OperationContractDescriptors."""
    wr = _validate_work_role(work_role)
    eager_refs: list[ToolCapabilityRef] = []
    for d in eager_descriptors:
        if not isinstance(d, OperationContractDescriptor):
            raise ToolCapabilityError(f"eager_descriptors must be OperationContractDescriptor, got {type(d).__name__}")
        if include_digest:
            eager_refs.append(ToolCapabilityRef.from_descriptor_with_digest(d))
        else:
            eager_refs.append(ToolCapabilityRef.from_descriptor(d))
    prog_refs: list[ToolCapabilityRef] = []
    for d in progressive_descriptors:
        if not isinstance(d, OperationContractDescriptor):
            raise ToolCapabilityError(f"progressive_descriptors must be OperationContractDescriptor, got {type(d).__name__}")
        if include_digest:
            prog_refs.append(ToolCapabilityRef.from_descriptor_with_digest(d))
        else:
            prog_refs.append(ToolCapabilityRef.from_descriptor(d))
    return ToolRoleSurface(work_role=wr, eager=tuple(eager_refs), progressive=tuple(prog_refs))


# ---------------------------------------------------------------------------
# Exposure helpers — visiblity only, never authority
# ---------------------------------------------------------------------------


def is_tool_visible(surface: ToolRoleSurface, capability_name: str) -> bool:
    if not isinstance(surface, ToolRoleSurface):
        raise TypeError(f"surface must be ToolRoleSurface, got {type(surface).__name__}")
    return surface.is_visible(capability_name)


def is_tool_eagerly_visible(surface: ToolRoleSurface, capability_name: str) -> bool:
    if not isinstance(surface, ToolRoleSurface):
        raise TypeError(f"surface must be ToolRoleSurface, got {type(surface).__name__}")
    return surface.is_eager(capability_name)


def is_tool_progressively_discoverable(surface: ToolRoleSurface, capability_name: str) -> bool:
    if not isinstance(surface, ToolRoleSurface):
        raise TypeError(f"surface must be ToolRoleSurface, got {type(surface).__name__}")
    return surface.is_progressive(capability_name)


__all__ = [
    "ToolCapabilityRef",
    "ToolRoleSurface",
    "create_role_tool_surface",
    "create_role_tool_surface_from_descriptors",
    "is_tool_visible",
    "is_tool_eagerly_visible",
    "is_tool_progressively_discoverable",
    "ToolSurfaceError",
    "UnknownWorkRoleError",
    "ToolCapabilityError",
    "ToolSurfaceConflictError",
    "ToolSurfaceBoundError",
    "MAX_CAPABILITY_NAME_LENGTH",
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_DIGEST_LENGTH",
    "MAX_EAGER_CAPABILITIES",
    "MAX_PROGRESSIVE_REFS",
    "MAX_TOTAL_CAPABILITIES",
    "MAX_TOOL_SURFACE_CANONICAL_BYTES",
    # flags
    "TOOL_IDENTITY_IS_AUTHORITY",
    "TOOL_EXPOSURE_IS_AUTHORITY",
    "ROLE_TOOL_SURFACE_IS_AUTHORITY",
    "WORK_ROLE_IS_TOOL_PERMISSION",
    "TOOL_METADATA_IS_OPERATION_AUTHORITY",
    "ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY",
    "ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY",
    "ROLE_EAGER_TOOL_SURFACE",
    "SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE",
    "DISCOVER_EVERY_TOOL_AT_BOOTSTRAP",
    "PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED",
    "PROGRESSIVE_TOOL_REF_BOUNDED",
    "PROGRESSIVE_TOOL_REF_IS_AUTHORITY",
    "ROLE_TOOL_SURFACE_DETERMINISTIC",
    "ROLE_TOOL_SURFACE_BOUNDED",
    "DUPLICATE_TOOL_SURFACE_ENTRY_DETERMINISTIC",
    "CONFLICTING_TOOL_SURFACE_ENTRY_FAIL_CLOSED",
    "UNKNOWN_WORK_ROLE_FAIL_CLOSED",
    "UNKNOWN_TOOL_CAPABILITY_AUTO_CREATED",
    "TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY",
    "ROLE_DEFAULT_SURFACE_IS_SECURITY_ACL",
    "VISIBILITY_AND_AUTHORIZATION_BIDIRECTIONAL_EQUIVALENCE",
    "OPERATION_DESCRIPTOR_IS_AUTHORITY_DECLARATION",
    "OPERATION_DESCRIPTOR_IS_RUNTIME_AUTHORITY_DECISION",
    "AGENTS_TEXT_AUTO_EXPOSES_TOOL",
    "AGENTS_TEXT_GRANTS_TOOL_AUTHORITY",
    "SANDBOX_BOUNDARY_IS_TOOL_AUTHORITY",
    "EXISTING_TOOL_PROVIDER_REUSED",
    "EXISTING_TOOL_REQUEST_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "EXISTING_OPERATION_DESCRIPTOR_REUSED",
    "DUPLICATE_TOOL_IDENTITY_NAMESPACE_CREATED",
    "NEW_TOOL_RUNTIME_CREATED",
    "NEW_PERMISSION_ENGINE_CREATED",
    "NEW_TOOL_REGISTRY_REQUIRED",
    "DYNAMIC_TOOL_PLUGIN_DISCOVERY",
    "PERSISTENT_TOOL_REGISTRY",
    "THIRD_RESULT_ONTOLOGY_CREATED",
    "NEW_TOOL_STATE_MACHINE_CREATED",
    "NEW_TOOL_JOURNAL_CREATED",
    "TOOL_TELEMETRY_STORE_CREATED",
    "WORKSPACE_READ_TOOL_IMPLEMENTED_IN_W1",
    "WORKSPACE_SEARCH_TOOL_IMPLEMENTED_IN_W1",
    "TOOL_RESULT_GOVERNANCE_IMPLEMENTED_IN_W1",
    "LIVE_TOOL_EXECUTION_IMPLEMENTED_IN_W1",
    "S3_IMPLEMENTATION_INTRODUCED",
]
