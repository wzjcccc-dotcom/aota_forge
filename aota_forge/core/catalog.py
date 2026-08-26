"""Canonical operation catalog — YAML projection (S1/M2/W2).

This module is now a projection-only surface over ``.aota/contracts/operations.yaml``.

Responsibilities:
- load canonical descriptors via ``loader.load_operation_descriptors``
- register only the general/lifecycle subset into ``DEFAULT_REGISTRY`` (registry
  routing topology preserved; execution.* stays in ExecutionDispatcher)
- expose deterministic helpers and lifecycle projections for compatibility

No hard-coded ``OperationContract`` or ``OperationContractDescriptor``
instances remain.  Operation identity constants stay as routing helpers.

``DESCRIPTOR_MODULE_FILESYSTEM_IO=no`` is preserved because I/O lives in
``loader`` only; this module delegates there via explicit project root
discovery (walking parents from package location to ``.aota/project.yaml``,
not CWD/ENV/host-absolute).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
    OperationContractDescriptor,
)
from aota_forge.core.contracts.loader import (
    DeclarativeContractError,
    discover_canonical_project_root,
    load_operation_descriptor_map,
    load_operation_descriptors,
)
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

# Stable operation identity / routing grouping (allowed to remain)
CANONICAL_GENERAL_OPERATIONS: Final[tuple[str, ...]] = (
    "project.resolve",
    "git.inspect",
    "runtime.status",
    "host.status",
    "operations.list",
)

CANONICAL_LIFECYCLE_OPERATIONS: Final[tuple[str, ...]] = (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
)

# Registry topology: these are bound into DEFAULT_REGISTRY; execution stays separate
_CATALOG_REGISTRY_NAMES: Final[frozenset[str]] = frozenset(
    (*CANONICAL_GENERAL_OPERATIONS, *CANONICAL_LIFECYCLE_OPERATIONS)
)

_REGISTERED = False
_CACHED_DESCRIPTORS: tuple[OperationContractDescriptor, ...] | None = None
_CACHED_MAP: dict[str, OperationContractDescriptor] | None = None


def _canonical_project_root() -> Path:
    return discover_canonical_project_root()


def _load_all_descriptors() -> tuple[OperationContractDescriptor, ...]:
    global _CACHED_DESCRIPTORS
    if _CACHED_DESCRIPTORS is not None:
        return _CACHED_DESCRIPTORS
    root = _canonical_project_root()
    _CACHED_DESCRIPTORS = load_operation_descriptors(root)
    return _CACHED_DESCRIPTORS


def _load_descriptor_map() -> dict[str, OperationContractDescriptor]:
    global _CACHED_MAP
    if _CACHED_MAP is not None:
        return _CACHED_MAP
    descs = _load_all_descriptors()
    _CACHED_MAP = {d.name: d for d in descs}
    return _CACHED_MAP


def get_catalog_descriptor(name: str) -> OperationContractDescriptor | None:
    """YAML-backed lookup for any canonical operation (general/lifecycle/execution)."""
    return _load_descriptor_map().get(name)


def get_all_descriptors() -> tuple[OperationContractDescriptor, ...]:
    """Return all 13 canonical descriptors in YAML order (deterministic)."""
    return _load_all_descriptors()


def get_general_lifecycle_descriptors() -> tuple[OperationContractDescriptor, ...]:
    """Return the 7 descriptors that are bound into DEFAULT_REGISTRY."""
    m = _load_descriptor_map()
    return tuple(m[name] for name in (*CANONICAL_GENERAL_OPERATIONS, *CANONICAL_LIFECYCLE_OPERATIONS) if name in m)


# ---------------------------------------------------------------------------
# Compatibility projections for lifecycle descriptors (YAML-derived, not hard-coded)
# ---------------------------------------------------------------------------

def _require_descriptor(name: str) -> OperationContractDescriptor:
    desc = get_catalog_descriptor(name)
    if desc is None:
        raise DeclarativeContractError(
            "DECLARATIVE_CONTRACT_NOT_FOUND", f"canonical descriptor missing in YAML: {name}"
        )
    return desc


# These are projections, not hard-coded instances.  Importers that previously
# used ``from aota_forge.core.contracts.descriptor import PLAN_INIT_DESCRIPTOR``
# should migrate to ``from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR``.
# We keep them here for bounded compatibility during cutover.
PLAN_INIT_DESCRIPTOR: OperationContractDescriptor = _require_descriptor(PLAN_INIT_OPERATION)
PLAN_RETIREMENT_DESCRIPTOR: OperationContractDescriptor = _require_descriptor(PLAN_RETIREMENT_OPERATION)
LIFECYCLE_DESCRIPTORS: tuple[OperationContractDescriptor, ...] = (
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)

# Also expose execution descriptor helpers via catalog for callers that need full map
def get_execution_descriptors() -> dict[str, OperationContractDescriptor]:
    """Projection of execution.* descriptors (YAML-derived)."""
    from aota_forge.core.ingress import CANONICAL_EXECUTION_OPERATIONS  # local to avoid cycle at top

    m = _load_descriptor_map()
    return {name: m[name] for name in CANONICAL_EXECUTION_OPERATIONS if name in m}


def register_canonical_descriptors() -> None:
    """Register the canonical general/lifecycle descriptors into DEFAULT_REGISTRY once.

    Fail-closed if YAML is missing/invalid/duplicate — no Python fallback.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    for name in (*CANONICAL_GENERAL_OPERATIONS, *CANONICAL_LIFECYCLE_OPERATIONS):
        desc = _require_descriptor(name)
        if not DEFAULT_REGISTRY.has(desc.name):
            DEFAULT_REGISTRY.bind(desc)
    _REGISTERED = True


# Eager registration at import (fail-closed if YAML missing)
register_canonical_descriptors()
