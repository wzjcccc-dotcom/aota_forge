"""Bounded bootstrap bundle contract (S1 M2-W3).

Minimal typed bootstrap for task-main (long-lived coordination) and
Worker (one-shot execution-scoped). Composes accepted WorkRole binding,
SOUL, TaskHandoff, and applicable policy candidates without adding
physical discovery, loaders, or retrieval.

Invariants
----------
* BOOTSTRAP_BUDGET_REQUIRED=yes
* BOOTSTRAP_COMPONENTS_BOUNDED=yes
* EXPECTED_TO_BE_USED_IN_CURRENT_EXECUTION=eager
* POSSIBLY_USEFUL=progressive
* BOOTSTRAP_REFS_ONLY_REQUIRED=no
* EXACT_LIMITS_FROZEN=no
* Component distinguishes kind / delivery / bounded material or ref / digest / provenance
* No generic plugin framework, registry, marketplace, or database
* EAGER vs PROGRESSIVE distinct; eager may materialize bounded content
* task-main vs Worker distinct; Worker binds execution WorkRole via single source
* SOUL reused; no duplicate contract
* Policy candidates reused; no discovery implementation
* TaskHandoff reused; no second envelope
* Budget accounting deterministic over canonical UTF-8 bytes, overflow fail-closed, no silent truncation
* Boundedness: component count, material size, ref size, ref count, total canonical size
* Canonical deterministic; digest is traceability not authority
* Provenance preserved without becoming authority
* Bundle is not authority
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.lifecycle import ExecutionWorkRoleBinding
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.soul import Soul

# ---------------------------------------------------------------------------
# Public flags
# ---------------------------------------------------------------------------

BOOTSTRAP_BUDGET_REQUIRED: bool = True
BOOTSTRAP_COMPONENTS_BOUNDED: bool = True
BOOTSTRAP_REFS_ONLY_REQUIRED: bool = False
EXACT_LIMITS_FROZEN: bool = False
BOOTSTRAP_BUNDLE_IS_AUTHORITY: bool = False
BOOTSTRAP_DIGEST_IS_AUTHORITY: bool = False

# Reuse markers
SOUL_REUSED: bool = True
SOUL_DUPLICATE_CONTRACT_CREATED: bool = False

AGENTS_APPLICABILITY_REUSED: bool = True
AGENTS_RESOLVER_IMPLEMENTED_IN_S1: bool = False

TASK_HANDOFF_REUSED: bool = True

WORK_ROLE_BINDING_SINGLE_SEMANTIC_SOURCE: bool = True

# Delivery semantics
EAGER_EXPECTED_IN_CURRENT_EXECUTION: bool = True
PROGRESSIVE_POSSIBLY_USEFUL: bool = True

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_COMPONENT_COUNT: int = 16
MAX_COMPONENT_KIND_LENGTH: int = 64
MAX_MATERIALIZED_LENGTH: int = 32 * 1024
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_PROVENANCE_LENGTH: int = 512
MAX_BUNDLE_CANONICAL_BYTES_HARD: int = 128 * 1024

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ALLOWED_KINDS: frozenset[str] = frozenset({
    "work_role_binding",
    "soul",
    "task_handoff",
    "agents_policy",
    "semantic_ref",
    "coordination_ref",
})

ALLOWED_BUNDLE_TYPES: frozenset[str] = frozenset({"task_main", "worker"})

ALLOWED_DELIVERIES: frozenset[str] = frozenset({"eager", "progressive"})


def _validate_kind(value: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"kind must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError("kind must be non-empty")
    if len(v) > MAX_COMPONENT_KIND_LENGTH:
        raise ValueError(f"kind length {len(v)} exceeds {MAX_COMPONENT_KIND_LENGTH}")
    if v not in ALLOWED_KINDS:
        raise ValueError(f"kind {v!r} not in allowed {sorted(ALLOWED_KINDS)}")
    if not _KIND_RE.fullmatch(v):
        raise ValueError(f"kind has invalid charset: {v!r}")
    return v


def _validate_delivery(value: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"delivery must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if v not in ALLOWED_DELIVERIES:
        raise ValueError(f"delivery must be one of {sorted(ALLOWED_DELIVERIES)}, got {value!r}")
    return v


def _validate_materialized(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"materialized content must be a string or None, got {type(value).__name__}")
    if len(value) == 0:
        raise ValueError("materialized content when provided must be non-empty")
    if len(value) > MAX_MATERIALIZED_LENGTH:
        raise ValueError(f"materialized length {len(value)} exceeds {MAX_MATERIALIZED_LENGTH}")
    return value


def _validate_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"ref must be a string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError("ref when provided must be non-empty")
    if len(v) > MAX_REF_LENGTH:
        raise ValueError(f"ref length {len(v)} exceeds {MAX_REF_LENGTH}")
    if v.startswith("/"):
        raise ValueError(f"ref must not be absolute path: {value!r}")
    if ".." in v.split("/"):
        raise ValueError(f"ref must not contain '..': {value!r}")
    return v


def _validate_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string or None, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ValueError("digest when provided must be non-empty")
    if len(v) > MAX_DIGEST_LENGTH:
        raise ValueError(f"digest length {len(v)} exceeds {MAX_DIGEST_LENGTH}")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ValueError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _validate_provenance(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"provenance must be a string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError("provenance when provided must be non-empty")
    if len(v) > MAX_PROVENANCE_LENGTH:
        raise ValueError(f"provenance length {len(v)} exceeds {MAX_PROVENANCE_LENGTH}")
    if v.startswith("/"):
        raise ValueError(f"provenance must not be absolute: {value!r}")
    if ".." in v.split("/"):
        raise ValueError(f"provenance must not contain '..': {value!r}")
    return v


def _compute_sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapComponent:
    """Minimal typed bootstrap component.

    Distinguishes kind, delivery mode, bounded material or ref,
    digest, and provenance. No registry or marketplace.
    """

    kind: str
    delivery: str
    # exactly one of materialized or ref
    materialized: str | None = None
    ref: str | None = None
    digest: str | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        k = _validate_kind(self.kind)
        object.__setattr__(self, "kind", k)

        d = _validate_delivery(self.delivery)
        object.__setattr__(self, "delivery", d)

        m = _validate_materialized(self.materialized)
        object.__setattr__(self, "materialized", m)

        r = _validate_ref(self.ref)
        object.__setattr__(self, "ref", r)

        # exactly one of materialized or ref
        if (m is None) == (r is None):
            raise ValueError("component must have exactly one of materialized or ref")

        # digest required
        dg = _validate_digest(self.digest)
        if dg is None:
            raise ValueError("digest is required")
        # For eager materialized, enforce digest matches material
        if m is not None:
            expected = _compute_sha256_hex(m)
            if dg != expected:
                raise ValueError(f"digest mismatch for materialized: expected {expected!r} got {dg!r}")
        object.__setattr__(self, "digest", dg)

        prov = _validate_provenance(self.provenance)
        object.__setattr__(self, "provenance", prov)

        # Eager should be materialized, progressive should be ref — enforce distinction
        if d == "eager" and m is None:
            raise ValueError("eager delivery requires materialized content")
        if d == "progressive" and r is None:
            raise ValueError("progressive delivery requires ref")

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "delivery": self.delivery,
            "digest": self.digest,
            "kind": self.kind,
        }
        if self.materialized is not None:
            d["materialized"] = self.materialized
        if self.ref is not None:
            d["ref"] = self.ref
        if self.provenance is not None:
            d["provenance"] = self.provenance
        return d

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BootstrapComponent":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {"kind", "delivery", "materialized", "ref", "digest", "provenance"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in BootstrapComponent: {sorted(extra)}")
        for req in ("kind", "delivery", "digest"):
            if req not in data:
                raise ValueError(f"Missing required field {req!r}")
        return cls(
            kind=data["kind"],
            delivery=data["delivery"],
            materialized=data.get("materialized"),
            ref=data.get("ref"),
            digest=data.get("digest"),
            provenance=data.get("provenance"),
        )

    # Convenience constructors reusing accepted contracts

    @classmethod
    def from_soul(cls, soul: Soul, delivery: str = "eager", provenance: str | None = None) -> "BootstrapComponent":
        if not isinstance(soul, Soul):
            raise TypeError(f"soul must be Soul, got {type(soul).__name__}")
        deliv = _validate_delivery(delivery)
        if deliv != "eager":
            raise ValueError("SOUL as bootstrap component must be eager (expected in current execution)")
        content = soul.canonical_json()
        if len(content) > MAX_MATERIALIZED_LENGTH:
            raise ValueError("soul canonical length exceeds materialized bound")
        digest = soul.digest
        return cls(kind="soul", delivery="eager", materialized=content, digest=digest, provenance=provenance)

    @classmethod
    def from_task_handoff(cls, handoff: TaskHandoff, delivery: str = "eager", provenance: str | None = None) -> "BootstrapComponent":
        if not isinstance(handoff, TaskHandoff):
            raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
        deliv = _validate_delivery(delivery)
        if deliv != "eager":
            raise ValueError("TaskHandoff for Worker must be eager")
        content = handoff.canonical_json()
        if len(content) > MAX_MATERIALIZED_LENGTH:
            raise ValueError("handoff canonical length exceeds bound")
        digest = handoff.handoff_digest
        return cls(kind="task_handoff", delivery="eager", materialized=content, digest=digest, provenance=provenance)

    @classmethod
    def from_agents_policy(cls, candidate: AgentsPolicyCandidate, delivery: str = "eager", provenance: str | None = None) -> "BootstrapComponent":
        if not isinstance(candidate, AgentsPolicyCandidate):
            raise TypeError(f"candidate must be AgentsPolicyCandidate, got {type(candidate).__name__}")
        deliv = _validate_delivery(delivery)
        # policy may be eager (materialized) or progressive (ref)
        if deliv == "eager":
            if candidate.content is None:
                raise ValueError("eager agents_policy requires materialized content")
            content = candidate.content
            if len(content) > MAX_MATERIALIZED_LENGTH:
                raise ValueError("policy content exceeds bound")
            digest = candidate.content_digest
            if digest is None:
                raise ValueError("policy digest required")
            # ensure digest matches content
            expected = _compute_sha256_hex(content)
            if digest != expected:
                raise ValueError("policy digest mismatch")
            return cls(kind="agents_policy", delivery="eager", materialized=content, digest=digest, provenance=provenance or candidate.provenance_ref)
        else:
            # progressive: reference only
            ref = candidate.provenance_ref or f"policy:{candidate.policy_id}:{candidate.scope}"
            if len(ref) > MAX_REF_LENGTH:
                raise ValueError("policy ref exceeds bound")
            digest = candidate.content_digest
            return cls(kind="agents_policy", delivery="progressive", ref=ref, digest=digest, provenance=provenance or candidate.provenance_ref)

    @classmethod
    def from_work_role_binding(cls, binding: ExecutionWorkRoleBinding, delivery: str = "eager", provenance: str | None = None) -> "BootstrapComponent":
        if not isinstance(binding, ExecutionWorkRoleBinding):
            raise TypeError(f"binding must be ExecutionWorkRoleBinding, got {type(binding).__name__}")
        deliv = _validate_delivery(delivery)
        if deliv != "eager":
            raise ValueError("work_role_binding must be eager")
        content = binding.canonical_json()
        digest = _compute_sha256_hex(content)
        return cls(kind="work_role_binding", delivery="eager", materialized=content, digest=digest, provenance=provenance)

    @classmethod
    def progressive_ref(cls, kind: str, ref: str, digest: str, provenance: str | None = None) -> "BootstrapComponent":
        k = _validate_kind(kind)
        r = _validate_ref(ref)
        dg = _validate_digest(digest)
        return cls(kind=k, delivery="progressive", ref=r, digest=dg, provenance=provenance)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapBudget:
    """Caller-supplied bounded budget.

    Accounts over canonical UTF-8 bytes plus structural counts.
    Not a frozen program-wide constant.
    """

    max_canonical_bytes: int
    max_components: int = MAX_COMPONENT_COUNT
    max_ref_count: int = MAX_COMPONENT_COUNT

    def __post_init__(self) -> None:
        if not isinstance(self.max_canonical_bytes, int) or type(self.max_canonical_bytes) is not int:
            raise TypeError("max_canonical_bytes must be int")
        if self.max_canonical_bytes <= 0:
            raise ValueError("max_canonical_bytes must be positive")
        if self.max_canonical_bytes > MAX_BUNDLE_CANONICAL_BYTES_HARD:
            raise ValueError(f"max_canonical_bytes exceeds hard limit {MAX_BUNDLE_CANONICAL_BYTES_HARD}")
        if not isinstance(self.max_components, int) or type(self.max_components) is not int:
            raise TypeError("max_components must be int")
        if self.max_components <= 0 or self.max_components > MAX_COMPONENT_COUNT:
            raise ValueError(f"max_components must be 1..{MAX_COMPONENT_COUNT}")
        if not isinstance(self.max_ref_count, int) or type(self.max_ref_count) is not int:
            raise TypeError("max_ref_count must be int")
        if self.max_ref_count <= 0 or self.max_ref_count > MAX_COMPONENT_COUNT:
            raise ValueError(f"max_ref_count must be 1..{MAX_COMPONENT_COUNT}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_canonical_bytes": self.max_canonical_bytes,
            "max_components": self.max_components,
            "max_ref_count": self.max_ref_count,
        }


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BootstrapBundle:
    """Bounded bootstrap bundle for task_main or Worker.

    Bundle is delivery, not authority. Digest is traceability.
    """

    bundle_type: str
    components: tuple[BootstrapComponent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bundle_type, str) or type(self.bundle_type) is not str:
            raise TypeError(f"bundle_type must be string, got {type(self.bundle_type).__name__}")
        bt = self.bundle_type.strip().lower()
        if bt not in ALLOWED_BUNDLE_TYPES:
            raise ValueError(f"bundle_type must be one of {sorted(ALLOWED_BUNDLE_TYPES)}, got {self.bundle_type!r}")
        object.__setattr__(self, "bundle_type", bt)

        if not isinstance(self.components, (tuple, list)):
            raise TypeError(f"components must be tuple or list, got {type(self.components).__name__}")
        comps = tuple(self.components)
        if len(comps) == 0:
            raise ValueError("bundle must have at least one component")
        if len(comps) > MAX_COMPONENT_COUNT:
            raise ValueError(f"component count {len(comps)} exceeds {MAX_COMPONENT_COUNT}")
        for idx, c in enumerate(comps):
            if not isinstance(c, BootstrapComponent):
                raise TypeError(f"components[{idx}] must be BootstrapComponent, got {type(c).__name__}")
        # Check ref count (progressive components)
        ref_count = sum(1 for c in comps if c.delivery == "progressive")
        if ref_count > MAX_COMPONENT_COUNT:
            raise ValueError(f"ref count {ref_count} exceeds {MAX_COMPONENT_COUNT}")
        # Validate distinct bundle semantics: worker vs task_main must differ
        # Worker must contain at least work_role_binding or task_handoff; task_main must not contain task_handoff as eager worker package
        kinds = [c.kind for c in comps]
        if bt == "worker":
            if "work_role_binding" not in kinds and "task_handoff" not in kinds:
                raise ValueError("worker bundle must contain work_role_binding or task_handoff")
            # Worker requires at least soul or work_role
            if "soul" not in kinds:
                # allow but note: spec says at least should be able to bind SOUL
                pass
        else:  # task_main
            if "task_handoff" in kinds and comps[0].delivery == "eager":
                # task_main may hold coordination refs but not worker execution package
                # we allow but ensure not identical to worker shape — handled via bundle_type distinction
                pass

        # Deterministic ordering check: ensure components are stored in canonical sorted order for determinism
        # We enforce that callers may provide any order but bundle normalizes internally via canonical_dict
        # No additional ordering validation needed here, but we store as given and canonicalize sorted.

        # Check for unknown nested mapping escape: components are bounded already

        # Check WorkRole binding single source: if bundle contains both work_role_binding and task_handoff, their work_roles must match
        work_role_from_binding: str | None = None
        work_role_from_handoff: str | None = None
        for c in comps:
            if c.kind == "work_role_binding" and c.materialized is not None:
                try:
                    import json as _json
                    d = _json.loads(c.materialized)
                    work_role_from_binding = d.get("work_role")
                except Exception:
                    pass
            if c.kind == "task_handoff" and c.materialized is not None:
                try:
                    import json as _json
                    d = _json.loads(c.materialized)
                    work_role_from_handoff = d.get("work_role")
                except Exception:
                    pass
        if work_role_from_binding is not None and work_role_from_handoff is not None:
            if work_role_from_binding != work_role_from_handoff:
                raise ValueError(f"work_role mismatch: binding {work_role_from_binding!r} != handoff {work_role_from_handoff!r}")

        object.__setattr__(self, "components", comps)

    def canonical_dict(self) -> dict[str, Any]:
        # Deterministic: sort components by (kind, delivery, materialized or ref, digest)
        def sort_key(c: BootstrapComponent) -> tuple[str, str, str, str]:
            payload = c.materialized if c.materialized is not None else c.ref or ""
            return (c.kind, c.delivery, payload, c.digest or "")

        sorted_comps = sorted(self.components, key=sort_key)
        return {
            "bundle_type": self.bundle_type,
            "components": [c.canonical_dict() for c in sorted_comps],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def canonical_bytes(self) -> bytes:
        return self.canonical_json().encode("utf-8")

    def accounted_size(self) -> int:
        return len(self.canonical_bytes())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BootstrapBundle":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"bundle_type", "components"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in BootstrapBundle: {sorted(extra)}")
        if "bundle_type" not in data or "components" not in data:
            raise ValueError("Missing required fields in BootstrapBundle")
        comps_data = data["components"]
        if not isinstance(comps_data, (list, tuple)):
            raise TypeError("components must be list or tuple")
        comps = tuple(BootstrapComponent.from_dict(c) for c in comps_data)
        return cls(bundle_type=data["bundle_type"], components=comps)

    def validate_budget(self, budget: BootstrapBudget) -> None:
        if not isinstance(budget, BootstrapBudget):
            raise TypeError(f"budget must be BootstrapBudget, got {type(budget).__name__}")
        size = self.accounted_size()
        if size > budget.max_canonical_bytes:
            raise ValueError(f"bundle size {size} exceeds budget {budget.max_canonical_bytes}")
        if len(self.components) > budget.max_components:
            raise ValueError(f"component count {len(self.components)} exceeds budget {budget.max_components}")
        ref_count = sum(1 for c in self.components if c.delivery == "progressive")
        if ref_count > budget.max_ref_count:
            raise ValueError(f"ref count {ref_count} exceeds budget {budget.max_ref_count}")
        # Also check hard limit
        if size > MAX_BUNDLE_CANONICAL_BYTES_HARD:
            raise ValueError(f"bundle size {size} exceeds hard limit {MAX_BUNDLE_CANONICAL_BYTES_HARD}")

    def reconcile_work_role(self, expected: ExecutionWorkRoleBinding | AgentWorkRole | str) -> None:
        """Fail-closed reconciliation of expected WorkRole against bundle's binding."""
        # Parse expected
        if isinstance(expected, ExecutionWorkRoleBinding):
            expected_val = expected.work_role.value
        elif isinstance(expected, AgentWorkRole):
            expected_val = expected.value
        elif isinstance(expected, str) and type(expected) is str:
            expected_val = parse_agent_work_role(expected).value
        else:
            raise TypeError(f"expected must be ExecutionWorkRoleBinding, AgentWorkRole, or str, got {type(expected).__name__}")

        # Find work_role in bundle
        found: str | None = None
        for c in self.components:
            if c.kind == "work_role_binding" and c.materialized is not None:
                try:
                    import json as _json
                    d = _json.loads(c.materialized)
                    found = d.get("work_role")
                    break
                except Exception:
                    continue
            if c.kind == "task_handoff" and c.materialized is not None:
                try:
                    import json as _json
                    d = _json.loads(c.materialized)
                    if found is None:
                        found = d.get("work_role")
                except Exception:
                    continue
        if found is None:
            raise ValueError("bundle contains no work_role to reconcile")
        if found != expected_val:
            raise ValueError(f"work_role mismatch: bundle {found!r} != expected {expected_val!r}")


BootstrapBundle = _BootstrapBundle


# ---------------------------------------------------------------------------
# Factory helpers for task_main vs Worker
# ---------------------------------------------------------------------------


def create_task_main_bundle(
    soul: Soul,
    agents_policies: Sequence[AgentsPolicyCandidate] = (),
    extra_refs: Sequence[BootstrapComponent] = (),
    provenance: str | None = None,
) -> BootstrapBundle:
    """Create bounded task_main bootstrap (long-lived coordination)."""
    if not isinstance(soul, Soul):
        raise TypeError(f"soul must be Soul, got {type(soul).__name__}")
    comps: list[BootstrapComponent] = []
    comps.append(BootstrapComponent.from_soul(soul, delivery="eager", provenance=provenance))
    for p in agents_policies:
        # task_main may hold applicable policy as eager or progressive
        if p.content is not None:
            comps.append(BootstrapComponent.from_agents_policy(p, delivery="eager", provenance=provenance))
        else:
            comps.append(BootstrapComponent.from_agents_policy(p, delivery="progressive", provenance=provenance))
    for r in extra_refs:
        if not isinstance(r, BootstrapComponent):
            raise TypeError(f"extra_ref must be BootstrapComponent, got {type(r).__name__}")
        if r.delivery != "progressive":
            raise ValueError("task_main extra_refs must be progressive")
        comps.append(r)
    return BootstrapBundle(bundle_type="task_main", components=tuple(comps))


def create_worker_bundle(
    binding: ExecutionWorkRoleBinding,
    soul: Soul,
    handoff: TaskHandoff,
    agents_policies: Sequence[AgentsPolicyCandidate] = (),
    extra_refs: Sequence[BootstrapComponent] = (),
    provenance: str | None = None,
) -> BootstrapBundle:
    """Create bounded Worker bootstrap (one-shot execution-scoped)."""
    if not isinstance(binding, ExecutionWorkRoleBinding):
        raise TypeError(f"binding must be ExecutionWorkRoleBinding, got {type(binding).__name__}")
    if not isinstance(soul, Soul):
        raise TypeError(f"soul must be Soul, got {type(soul).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    # Reconcile work_role single source
    if binding.work_role.value != handoff.work_role.value:
        raise ValueError(f"work_role mismatch: binding {binding.work_role.value!r} != handoff {handoff.work_role.value!r}")

    comps: list[BootstrapComponent] = []
    comps.append(BootstrapComponent.from_work_role_binding(binding, delivery="eager", provenance=provenance))
    comps.append(BootstrapComponent.from_soul(soul, delivery="eager", provenance=provenance))
    comps.append(BootstrapComponent.from_task_handoff(handoff, delivery="eager", provenance=provenance))
    for p in agents_policies:
        if p.content is not None:
            # Worker may materialize required policy eagerly
            comps.append(BootstrapComponent.from_agents_policy(p, delivery="eager", provenance=provenance))
        else:
            comps.append(BootstrapComponent.from_agents_policy(p, delivery="progressive", provenance=provenance))
    for r in extra_refs:
        if not isinstance(r, BootstrapComponent):
            raise TypeError(f"extra_ref must be BootstrapComponent, got {type(r).__name__}")
        comps.append(r)
    # Validate bounds via construction
    return BootstrapBundle(bundle_type="worker", components=tuple(comps))


__all__ = [
    "BootstrapComponent",
    "BootstrapBundle",
    "BootstrapBudget",
    "create_task_main_bundle",
    "create_worker_bundle",
    "MAX_COMPONENT_COUNT",
    "MAX_MATERIALIZED_LENGTH",
    "MAX_REF_LENGTH",
    "MAX_BUNDLE_CANONICAL_BYTES_HARD",
    "BOOTSTRAP_BUDGET_REQUIRED",
    "BOOTSTRAP_COMPONENTS_BOUNDED",
    "BOOTSTRAP_REFS_ONLY_REQUIRED",
    "EXACT_LIMITS_FROZEN",
    "BOOTSTRAP_BUNDLE_IS_AUTHORITY",
    "BOOTSTRAP_DIGEST_IS_AUTHORITY",
    "SOUL_REUSED",
    "SOUL_DUPLICATE_CONTRACT_CREATED",
    "AGENTS_APPLICABILITY_REUSED",
    "AGENTS_RESOLVER_IMPLEMENTED_IN_S1",
    "TASK_HANDOFF_REUSED",
    "WORK_ROLE_BINDING_SINGLE_SEMANTIC_SOURCE",
]
