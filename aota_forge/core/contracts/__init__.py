"""Executor-neutral operation contracts.

Canonical contract foundation (M2-A):

* ``OperationContractDescriptor`` — portable declarative machine contract
* ``HandlerRegistry`` / ``DEFAULT_REGISTRY`` — runtime-local handler binding
* ``PROTOCOL_VERSION`` — explicit operation contract protocol version

The legacy coupled ``OperationContract`` shim lives in
``aota_forge.core.contracts.operations`` and remains importable unchanged
for M1 compatibility; it is NOT contract authority anymore.
"""

from aota_forge.core.contracts.descriptor import (
    OperationContractDescriptor,
    InputSpec,
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
    READ_ONLY,
    READ_WRITE,
    WRITE_ONLY,
)
from aota_forge.core.contracts.registry import (
    DuplicateOperationRegistrationError,
    HandlerRegistry,
    UnknownOperationError,
    DEFAULT_REGISTRY,
)
from aota_forge.core.contracts.version import (
    OPERATION_CONTRACT_PROTOCOL,
    PROTOCOL_VERSION,
    protocol_version,
)
from aota_forge.core.contracts.mutation import (
    AuthoritativeEffectConfirmation,
    CanonicalMutationResult,
    MutationEffect,
    MutationIntent,
    MutationPreconditions,
    MutationResult,
)
from aota_forge.core.contracts.errors import (
    ApprovalRequiredError,
    AuthorizationContractDriftError,
    AuthorizationMissingError,
    AuthorizationOperationMismatchError,
    AuthorizationScopeMismatchError,
    AuthorizationTargetMismatchError,
    AuthorityPreconditionStaleError,
    ERROR_CLASSES,
    ForgeError,
    LeaseConsumedError,
    LeaseExpiredError,
    LeaseIntentMismatchError,
    LeaseRevokedError,
    LeaseScopeMismatchError,
    LeaseTargetMismatchError,
    M4_2_AUTHORIZATION_ERROR_CODES,
    M4_4_LIFECYCLE_ERROR_CODES,
    MaterializedDecisionRequiredError,
    OutcomeUnknownRequiresReconciliationError,
    PlanInitAlreadyInitializedError,
    PlanInitInvalidPredecessorError,
    PlanInitStaleAuthorityPreconditionError,
    PlanInitStaleSubjectRevisionError,
    ProjectBindingRequiredError,
    RetirementNeedsSemanticChoiceError,
    RetirementNoCandidateError,
    RetirementRunningTaskProtectedError,
    RetirementSelfSuccessorError,
    RetirementStaleAuthorityPreconditionError,
    RetirementStaleSnapshotError,
    RetirementSuccessorInvalidError,
    RetirementSuccessorRequiredError,
    RetirementTargetProtectedError,
    SubjectRevisionStaleAuthorizationError,
    error_from_dict,
)

def __getattr__(name: str):
    # Lazy projection for lifecycle descriptors (YAML-backed).
    # This avoids circular import: catalog imports descriptor, descriptor package
    # __init__ would otherwise eagerly import catalog before catalog finishes.
    if name in ("PLAN_INIT_DESCRIPTOR", "PLAN_RETIREMENT_DESCRIPTOR", "LIFECYCLE_DESCRIPTORS"):
        from aota_forge.core.catalog import (
            LIFECYCLE_DESCRIPTORS as _LD,
            PLAN_INIT_DESCRIPTOR as _PID,
            PLAN_RETIREMENT_DESCRIPTOR as _PRD,
        )

        if name == "PLAN_INIT_DESCRIPTOR":
            return _PID
        if name == "PLAN_RETIREMENT_DESCRIPTOR":
            return _PRD
        return _LD
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "OperationContractDescriptor",
    "InputSpec",
    "PLAN_INIT_OPERATION",
    "PLAN_RETIREMENT_OPERATION",
    "PLAN_INIT_DESCRIPTOR",
    "PLAN_RETIREMENT_DESCRIPTOR",
    "LIFECYCLE_DESCRIPTORS",
    "READ_ONLY",
    "READ_WRITE",
    "WRITE_ONLY",
    "HandlerRegistry",
    "DEFAULT_REGISTRY",
    "DuplicateOperationRegistrationError",
    "UnknownOperationError",
    "OPERATION_CONTRACT_PROTOCOL",
    "PROTOCOL_VERSION",
    "protocol_version",
    "MutationIntent",
    "MutationPreconditions",
    "MutationEffect",
    "AuthoritativeEffectConfirmation",
    "MutationResult",
    "CanonicalMutationResult",
    "ForgeError",
    "ERROR_CLASSES",
    "error_from_dict",
    "M4_2_AUTHORIZATION_ERROR_CODES",
    "M4_4_LIFECYCLE_ERROR_CODES",
    "AuthorizationMissingError",
    "AuthorizationScopeMismatchError",
    "AuthorizationTargetMismatchError",
    "AuthorizationOperationMismatchError",
    "AuthorizationContractDriftError",
    "ApprovalRequiredError",
    "MaterializedDecisionRequiredError",
    "SubjectRevisionStaleAuthorizationError",
    "AuthorityPreconditionStaleError",
    "LeaseExpiredError",
    "LeaseRevokedError",
    "LeaseConsumedError",
    "LeaseIntentMismatchError",
    "LeaseTargetMismatchError",
    "LeaseScopeMismatchError",
    "OutcomeUnknownRequiresReconciliationError",
    "ProjectBindingRequiredError",
    "PlanInitInvalidPredecessorError",
    "PlanInitAlreadyInitializedError",
    "PlanInitStaleSubjectRevisionError",
    "PlanInitStaleAuthorityPreconditionError",
    "RetirementNoCandidateError",
    "RetirementNeedsSemanticChoiceError",
    "RetirementStaleSnapshotError",
    "RetirementTargetProtectedError",
    "RetirementRunningTaskProtectedError",
    "RetirementSuccessorRequiredError",
    "RetirementSuccessorInvalidError",
    "RetirementSelfSuccessorError",
    "RetirementStaleAuthorityPreconditionError",
]
