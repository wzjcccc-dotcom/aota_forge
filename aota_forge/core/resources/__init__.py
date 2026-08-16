"""Executor-neutral trusted read-only resource boundary (M2-E).

Model-facing Host/project operations must never take an arbitrary
filesystem path as a security boundary.  The model-facing contract is a
logical reference:

    resource kind + resource id
            ↓
    trusted resolver
            ↓
    bounded filesystem resource under a trusted configured root

Trusted roots are operator configuration, never model input.  Denial is
fail-closed: unknown kind, unsafe id (absolute path, path separators,
traversal, null bytes), missing/untrusted root, symlink escape, and
containment escape are all rejected with HostResourceDeniedError.

No generic filesystem reader exists; every kind has a deterministic
bounded layout, and the existing bounded readers (size limits, JSON
validation) remain the only read mechanics.
"""

from aota_forge.core.resources.resolver import (
    RESOURCE_ID_RE,
    RESOURCE_KINDS,
    HostResourceDeniedError,
    ResourceKind,
    TrustedResourceConfig,
    TrustedResourceResolver,
    resolve_host_resource,
)

__all__ = [
    "RESOURCE_ID_RE",
    "RESOURCE_KINDS",
    "HostResourceDeniedError",
    "ResourceKind",
    "TrustedResourceConfig",
    "TrustedResourceResolver",
    "resolve_host_resource",
]
