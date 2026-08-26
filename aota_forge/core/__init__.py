"""Forge Core — executor-neutral read-only domain.

Dependency invariant (M1): Core imports stdlib, PyYAML (repo-accepted strict
parser dependency), and new executor-neutral contracts only.  It MUST NOT
import plugin/aota-tools legacy orchestration, Hermes packages, WebUI,
Docker, outbox, or parent-wake modules.

M2-I import isolation: importing this package registers the canonical
operation DESCRIPTORS only (declarative contract surface).  Executable
handler binding is attached lazily, deterministically and idempotently at
the first ingress execution (``core.bootstrap.ensure_handlers_bound``), so
importing declarative contract modules never executes handler registration
as a package-import side effect.
"""

from aota_forge.core import catalog  # noqa: F401  (registers canonical descriptors)
from aota_forge.core.authority import (
    ApprovalEvidence,
    AuthorityDecision,
    AuthorityEngine,
    AuthorityReason,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
)
from aota_forge.core.authorization import (
    AuthorizationErrorCode,
    AuthorizationFailure,
    AuthorizationResult,
    CapabilityLeaseIssuer,
)
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import Principal, TrustedContext, bind_trusted_context
from aota_forge.core.catalog import (
    LIFECYCLE_DESCRIPTORS,
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.ingress import execute
from aota_forge.core.transitions import (
    PlanInitRequest,
    PlanRetirementRequest,
    RetirementCandidateResolution,
    RetirementCandidateSnapshot,
    capture_retirement_snapshot,
    plan_init,
    resolve_retirement_candidates,
    retire_plan,
)

__all__ = [
    "execute",
    "Principal",
    "TrustedContext",
    "bind_trusted_context",
    "ApprovalEvidence",
    "AuthorityDecision",
    "AuthorityEngine",
    "AuthorityReason",
    "MaterializedDecisionEvidence",
    "TrustedMutationAuthorization",
    "AuthorizationErrorCode",
    "AuthorizationFailure",
    "AuthorizationResult",
    "CapabilityLeaseIssuer",
    "CapabilityLease",
    "LIFECYCLE_DESCRIPTORS",
    "PLAN_INIT_DESCRIPTOR",
    "PLAN_RETIREMENT_DESCRIPTOR",
    "PlanInitRequest",
    "PlanRetirementRequest",
    "RetirementCandidateResolution",
    "RetirementCandidateSnapshot",
    "capture_retirement_snapshot",
    "resolve_retirement_candidates",
    "plan_init",
    "retire_plan",
]
