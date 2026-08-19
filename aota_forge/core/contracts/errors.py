"""Canonical machine-readable error contract (M1-G / M2-B).

Error codes are stable bounded strings.  Legacy executor-specific errors
(SPEC_NOT_APPROVED / PROFILE_FORBIDDEN / STALE_WORKER) are NOT part of Core;
an adapter may map them at an executor boundary later.

M2-B repair (I9-B005):

* every registered class exposes a class-level canonical ``code`` so the
  registry never collapses to a single FORGE_ERROR entry
* ``error_from_dict`` reconstructs heterogeneous subclasses through the
  canonical base payload (``from_payload``), never through a positional
  ``cls(message, retryable)`` assumption
* every registered error round-trips: instance -> to_dict -> error_from_dict
  preserves semantic code, message, retryable and details
* unknown future codes degrade deterministically to ``UnknownFutureError``
  carrying the original machine code; they never crash, never map to an
  unrelated subclass and never drop the original code.
"""

from __future__ import annotations

from typing import Any, Optional


class ForgeError(Exception):
    """Base error for executor-neutral Forge Core failures.

    The base class keeps the M1-compatible ``(code, message, retryable)``
    positional signature; registered subclasses use the canonical
    ``(message, retryable, details)`` payload signature together with a
    class-level ``code`` / ``default_message`` / ``default_retryable``.
    """

    code: str = "FORGE_ERROR"
    default_message: str = "forge error"
    default_retryable: bool = False

    def __init__(self, code: str, message: str, retryable: bool = False, details: object = None) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            payload["details"] = self.details
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ForgeError":
        """Rebuild an error from its canonical dict payload.

        Registered subclasses share the ``(message, retryable, details)``
        payload signature, so one canonical reconstruction works for the
        whole registry without per-class positional guessing.
        """
        message = payload.get("message")
        retryable = payload.get("retryable")
        details = payload.get("details")
        if cls is ForgeError:
            return cls(
                str(payload.get("code") or cls.code),
                str(message if message is not None else cls.default_message),
                retryable=bool(retryable) if retryable is not None else cls.default_retryable,
                details=details,
            )
        return cls(
            str(message if message is not None else cls.default_message),
            retryable=bool(retryable) if retryable is not None else cls.default_retryable,
            details=details,
        )


def _canonical_init(self: ForgeError, message: object, retryable: object, details: object) -> None:
    ForgeError.__init__(
        self,
        type(self).code,
        str(message) if message is not None else type(self).default_message,
        retryable=bool(retryable) if retryable is not None else type(self).default_retryable,
        details=details,
    )


class ProjectNotFoundError(ForgeError):
    code = "PROJECT_NOT_FOUND"
    default_message = "project not found"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ProjectAmbiguousError(ForgeError):
    code = "PROJECT_AMBIGUOUS"
    default_message = "project resolution is ambiguous"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ProjectManifestInvalidError(ForgeError):
    code = "PROJECT_MANIFEST_INVALID"
    default_message = "project manifest is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, detail: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        if detail is not None:
            message = f"{message}: {detail}"
        _canonical_init(self, message, retryable, details)


class ProjectRegistryInvalidError(ForgeError):
    code = "PROJECT_REGISTRY_INVALID"
    default_message = "project registry is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class GitNotFoundError(ForgeError):
    code = "GIT_NOT_FOUND"
    default_message = "git repository not found within boundary"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class GitBoundaryViolationError(ForgeError):
    code = "GIT_BOUNDARY_VIOLATION"
    default_message = "git search crossed the resolved project boundary"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class RuntimeNotRunningError(ForgeError):
    code = "RUNTIME_NOT_RUNNING"
    default_message = "runtime is not running"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class RuntimeIdentityUnavailableError(ForgeError):
    code = "RUNTIME_IDENTITY_UNAVAILABLE"
    default_message = "runtime identity is unavailable"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ReceiptInvalidError(ForgeError):
    code = "RECEIPT_INVALID"
    default_message = "receipt is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class SourceParityMismatchError(ForgeError):
    code = "SOURCE_PARITY_MISMATCH"
    default_message = "source/runtime parity mismatch"
    default_retryable = True

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class UnsupportedOperationError(ForgeError):
    code = "UNSUPPORTED_OPERATION"
    default_message = "unsupported operation"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ContractVersionMismatchError(ForgeError):
    code = "CONTRACT_VERSION_MISMATCH"
    default_message = "contract version mismatch"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class WorkspaceError(ForgeError):
    """Workspace registry-level failure, kept executor-neutral."""

    code = "WORKSPACE_ERROR"
    default_message = "workspace error"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class HostResourceDeniedError(ForgeError):
    """A model-facing logical host resource reference was denied by the
    trusted resource boundary (M2-E, canonical registration in M2-I)."""

    code = "HOST_RESOURCE_DENIED"
    default_message = "host resource denied"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


# ---------------------------------------------------------------------------
# M2-B stable semantic codes (distinct root causes must NOT collapse).
# ---------------------------------------------------------------------------


class ProjectBindingMissingError(ForgeError):
    """Caller explicitly required project/workspace-bound semantics but the
    binding context (registry/workspace/project identifiers) is incomplete."""

    code = "PROJECT_BINDING_MISSING"
    default_message = "project binding context is missing"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class PlanMissingError(ForgeError):
    code = "PLAN_MISSING"
    default_message = "plan is missing"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class PlanWorkspaceContextMissingError(ForgeError):
    code = "PLAN_WORKSPACE_CONTEXT_MISSING"
    default_message = "plan workspace context is missing"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ActiveWorkItemMissingError(ForgeError):
    code = "ACTIVE_WORK_ITEM_MISSING"
    default_message = "active work item is missing"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ProjectInitializationRequiredError(ForgeError):
    code = "PROJECT_INITIALIZATION_REQUIRED"
    default_message = "project initialization is required"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class NeedsSemanticChoiceError(ForgeError):
    code = "NEEDS_SEMANTIC_CHOICE"
    default_message = "semantic choice is required"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class MutationConflictError(ForgeError):
    code = "CONFLICT"
    default_message = "mutation intent or precondition conflicts"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class MutationBlockedError(ForgeError):
    code = "BLOCKED"
    default_message = "mutation prerequisite is not satisfied"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class OutcomeUnknownError(ForgeError):
    code = "OUTCOME_UNKNOWN"
    default_message = "authoritative mutation outcome is unknown"
    default_retryable = True

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class FailedNoEffectError(ForgeError):
    code = "FAILED_NO_EFFECT"
    default_message = "mutation failed with no authoritative effect"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class StaleSubjectError(ForgeError):
    code = "STALE_SUBJECT"
    default_message = "Subject revision precondition is stale"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class StaleAuthorityError(ForgeError):
    code = "STALE_AUTHORITY"
    default_message = "external authority precondition is stale"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class InvalidPredecessorError(ForgeError):
    code = "INVALID_PREDECESSOR"
    default_message = "mutation predecessor state is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class MaterializationFailedError(ForgeError):
    code = "MATERIALIZATION_FAILED"
    default_message = "authoritative mutation materialization failed"
    default_retryable = True

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class IdempotencyConflictError(ForgeError):
    code = "IDEMPOTENCY_CONFLICT"
    default_message = "idempotency identity is bound to a different intent"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class GovernanceProjectionDriftError(ForgeError):
    code = "GOVERNANCE_PROJECTION_DRIFT"
    default_message = "governance projection drift"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class ContextNotSupportedError(ForgeError):
    """Deterministic bounded outcome for context requirements M2 cannot yet
    satisfy; the resolver never fabricates future-milestone context."""

    code = "CONTEXT_NOT_SUPPORTED"
    default_message = "operation context is not supported in this milestone"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class UnknownInputError(ForgeError):
    code = "UNKNOWN_INPUT"
    default_message = "unknown input"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class MissingRequiredInputError(ForgeError):
    code = "REQUIRED_INPUT_MISSING"
    default_message = "required input is missing"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class InputTypeError(ForgeError):
    code = "INPUT_TYPE_INVALID"
    default_message = "input type is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class InputSizeError(ForgeError):
    code = "INPUT_SIZE_EXCEEDED"
    default_message = "input size bound exceeded"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class DuplicateOperationInputError(ForgeError):
    """Deterministic contract-definition failure (I9-B007): a descriptor
    declares the same semantic operation input name more than once.
    """

    code = "DUPLICATE_OPERATION_INPUT"
    default_message = "duplicate operation input declaration"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


class UnknownFutureError(ForgeError):
    """Deterministic degradation for error codes not registered yet.

    Never crashes, never maps to an unrelated subclass and never drops the
    original machine code: ``original_code`` preserves it in the dict form.
    """

    code = "UNKNOWN_FUTURE_ERROR"
    default_message = "unknown error code"
    default_retryable = False

    def __init__(self, original_code: object = None, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        self.original_code = original_code
        _canonical_init(
            self,
            message if message is not None else f"unknown error code: {original_code}",
            retryable,
            details,
        )

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        if self.original_code is not None:
            payload["original_code"] = self.original_code
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "UnknownFutureError":
        return cls(
            original_code=payload.get("original_code"),
            message=payload.get("message"),
            retryable=payload.get("retryable"),
            details=payload.get("details"),
        )


# M4 shared seams keep authorization and lifecycle failure codes distinct in
# the canonical registry without making the registry depend on either source
# implementation module.
class _M4ContractError(ForgeError):
    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        _canonical_init(self, message, retryable, details)


def _make_m4_contract_error(
    name: str,
    code: str,
    default_message: str,
    *,
    default_retryable: bool = False,
) -> type[ForgeError]:
    return type(
        name,
        (_M4ContractError,),
        {
            "__module__": __name__,
            "code": code,
            "default_message": default_message,
            "default_retryable": default_retryable,
        },
    )


M4_2_AUTHORIZATION_ERROR_CODES = (
    "AUTHORIZATION_MISSING",
    "AUTHORIZATION_SCOPE_MISMATCH",
    "AUTHORIZATION_TARGET_MISMATCH",
    "AUTHORIZATION_OPERATION_MISMATCH",
    "AUTHORIZATION_CONTRACT_DRIFT",
    "APPROVAL_REQUIRED",
    "MATERIALIZED_DECISION_REQUIRED",
    "SUBJECT_REVISION_STALE",
    "AUTHORITY_PRECONDITION_STALE",
    "LEASE_EXPIRED",
    "LEASE_REVOKED",
    "LEASE_CONSUMED",
    "LEASE_INTENT_MISMATCH",
    "LEASE_TARGET_MISMATCH",
    "LEASE_SCOPE_MISMATCH",
    "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
    "NEEDS_SEMANTIC_CHOICE",
)

M4_4_LIFECYCLE_ERROR_CODES = (
    "PROJECT_NOT_FOUND",
    "NEEDS_SEMANTIC_CHOICE",
    "PROJECT_BINDING_REQUIRED",
    "PLAN_INIT_INVALID_PREDECESSOR",
    "PLAN_INIT_ALREADY_INITIALIZED",
    "PLAN_INIT_STALE_SUBJECT_REVISION",
    "PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
    "RETIREMENT_NO_CANDIDATE",
    "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
    "RETIREMENT_STALE_SNAPSHOT",
    "RETIREMENT_TARGET_PROTECTED",
    "RETIREMENT_RUNNING_TASK_PROTECTED",
    "RETIREMENT_SUCCESSOR_REQUIRED",
    "RETIREMENT_SUCCESSOR_INVALID",
    "RETIREMENT_SELF_SUCCESSOR",
    "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
)

AuthorizationMissingError = _make_m4_contract_error(
    "AuthorizationMissingError", "AUTHORIZATION_MISSING", "authorization is missing"
)
AuthorizationScopeMismatchError = _make_m4_contract_error(
    "AuthorizationScopeMismatchError", "AUTHORIZATION_SCOPE_MISMATCH", "authorization scope differs"
)
AuthorizationTargetMismatchError = _make_m4_contract_error(
    "AuthorizationTargetMismatchError", "AUTHORIZATION_TARGET_MISMATCH", "authorization target differs"
)
AuthorizationOperationMismatchError = _make_m4_contract_error(
    "AuthorizationOperationMismatchError", "AUTHORIZATION_OPERATION_MISMATCH", "authorization operation differs"
)
AuthorizationContractDriftError = _make_m4_contract_error(
    "AuthorizationContractDriftError", "AUTHORIZATION_CONTRACT_DRIFT", "authorization contract differs"
)
ApprovalRequiredError = _make_m4_contract_error(
    "ApprovalRequiredError", "APPROVAL_REQUIRED", "exact approval evidence is required"
)
MaterializedDecisionRequiredError = _make_m4_contract_error(
    "MaterializedDecisionRequiredError", "MATERIALIZED_DECISION_REQUIRED", "exact materialized decision evidence is required"
)
SubjectRevisionStaleAuthorizationError = _make_m4_contract_error(
    "SubjectRevisionStaleAuthorizationError",
    "SUBJECT_REVISION_STALE",
    "Subject revision is stale",
    default_retryable=True,
)
AuthorityPreconditionStaleError = _make_m4_contract_error(
    "AuthorityPreconditionStaleError",
    "AUTHORITY_PRECONDITION_STALE",
    "authority precondition is stale",
    default_retryable=True,
)
LeaseExpiredError = _make_m4_contract_error("LeaseExpiredError", "LEASE_EXPIRED", "lease is expired")
LeaseRevokedError = _make_m4_contract_error("LeaseRevokedError", "LEASE_REVOKED", "lease is revoked")
LeaseConsumedError = _make_m4_contract_error("LeaseConsumedError", "LEASE_CONSUMED", "lease is consumed")
LeaseIntentMismatchError = _make_m4_contract_error(
    "LeaseIntentMismatchError", "LEASE_INTENT_MISMATCH", "lease intent differs"
)
LeaseTargetMismatchError = _make_m4_contract_error(
    "LeaseTargetMismatchError", "LEASE_TARGET_MISMATCH", "lease target differs"
)
LeaseScopeMismatchError = _make_m4_contract_error(
    "LeaseScopeMismatchError", "LEASE_SCOPE_MISMATCH", "lease scope differs"
)
OutcomeUnknownRequiresReconciliationError = _make_m4_contract_error(
    "OutcomeUnknownRequiresReconciliationError",
    "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
    "unknown outcome requires reconciliation",
)

ProjectBindingRequiredError = _make_m4_contract_error(
    "ProjectBindingRequiredError", "PROJECT_BINDING_REQUIRED", "exact Project Binding is required"
)
PlanInitInvalidPredecessorError = _make_m4_contract_error(
    "PlanInitInvalidPredecessorError", "PLAN_INIT_INVALID_PREDECESSOR", "PLAN_INIT predecessor is invalid"
)
PlanInitAlreadyInitializedError = _make_m4_contract_error(
    "PlanInitAlreadyInitializedError", "PLAN_INIT_ALREADY_INITIALIZED", "PLAN_INIT re-entry is denied"
)
PlanInitStaleSubjectRevisionError = _make_m4_contract_error(
    "PlanInitStaleSubjectRevisionError",
    "PLAN_INIT_STALE_SUBJECT_REVISION",
    "PLAN_INIT Subject revision is stale",
)
PlanInitStaleAuthorityPreconditionError = _make_m4_contract_error(
    "PlanInitStaleAuthorityPreconditionError",
    "PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
    "PLAN_INIT authority precondition is stale",
)
RetirementNoCandidateError = _make_m4_contract_error(
    "RetirementNoCandidateError", "RETIREMENT_NO_CANDIDATE", "no eligible retirement candidate exists"
)
RetirementNeedsSemanticChoiceError = _make_m4_contract_error(
    "RetirementNeedsSemanticChoiceError",
    "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
    "retirement candidate choice is required",
)
RetirementStaleSnapshotError = _make_m4_contract_error(
    "RetirementStaleSnapshotError", "RETIREMENT_STALE_SNAPSHOT", "retirement snapshot is stale"
)
RetirementTargetProtectedError = _make_m4_contract_error(
    "RetirementTargetProtectedError", "RETIREMENT_TARGET_PROTECTED", "retirement target is protected"
)
RetirementRunningTaskProtectedError = _make_m4_contract_error(
    "RetirementRunningTaskProtectedError",
    "RETIREMENT_RUNNING_TASK_PROTECTED",
    "retirement target has a running task",
)
RetirementSuccessorRequiredError = _make_m4_contract_error(
    "RetirementSuccessorRequiredError", "RETIREMENT_SUCCESSOR_REQUIRED", "an exact successor is required"
)
RetirementSuccessorInvalidError = _make_m4_contract_error(
    "RetirementSuccessorInvalidError", "RETIREMENT_SUCCESSOR_INVALID", "retirement successor is invalid"
)
RetirementSelfSuccessorError = _make_m4_contract_error(
    "RetirementSelfSuccessorError", "RETIREMENT_SELF_SUCCESSOR", "retirement successor cannot be the target"
)
RetirementStaleAuthorityPreconditionError = _make_m4_contract_error(
    "RetirementStaleAuthorityPreconditionError",
    "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
    "retirement authority precondition is stale",
)


ERROR_CLASSES: dict[str, type[ForgeError]] = {
    cls.code: cls
    for cls in (
        ForgeError,
        ProjectNotFoundError,
        ProjectAmbiguousError,
        ProjectManifestInvalidError,
        ProjectRegistryInvalidError,
        GitNotFoundError,
        GitBoundaryViolationError,
        RuntimeNotRunningError,
        RuntimeIdentityUnavailableError,
        ReceiptInvalidError,
        SourceParityMismatchError,
        UnsupportedOperationError,
        ContractVersionMismatchError,
        WorkspaceError,
        HostResourceDeniedError,
        ProjectBindingMissingError,
        PlanMissingError,
        PlanWorkspaceContextMissingError,
        ActiveWorkItemMissingError,
        ProjectInitializationRequiredError,
        NeedsSemanticChoiceError,
        MutationConflictError,
        MutationBlockedError,
        OutcomeUnknownError,
        FailedNoEffectError,
        StaleSubjectError,
        StaleAuthorityError,
        InvalidPredecessorError,
        MaterializationFailedError,
        IdempotencyConflictError,
        GovernanceProjectionDriftError,
        ContextNotSupportedError,
        UnknownInputError,
        MissingRequiredInputError,
        InputTypeError,
        InputSizeError,
        DuplicateOperationInputError,
        UnknownFutureError,
        AuthorizationMissingError,
        AuthorizationScopeMismatchError,
        AuthorizationTargetMismatchError,
        AuthorizationOperationMismatchError,
        AuthorizationContractDriftError,
        ApprovalRequiredError,
        MaterializedDecisionRequiredError,
        SubjectRevisionStaleAuthorizationError,
        AuthorityPreconditionStaleError,
        LeaseExpiredError,
        LeaseRevokedError,
        LeaseConsumedError,
        LeaseIntentMismatchError,
        LeaseTargetMismatchError,
        LeaseScopeMismatchError,
        OutcomeUnknownRequiresReconciliationError,
        ProjectBindingRequiredError,
        PlanInitInvalidPredecessorError,
        PlanInitAlreadyInitializedError,
        PlanInitStaleSubjectRevisionError,
        PlanInitStaleAuthorityPreconditionError,
        RetirementNoCandidateError,
        RetirementNeedsSemanticChoiceError,
        RetirementStaleSnapshotError,
        RetirementTargetProtectedError,
        RetirementRunningTaskProtectedError,
        RetirementSuccessorRequiredError,
        RetirementSuccessorInvalidError,
        RetirementSelfSuccessorError,
        RetirementStaleAuthorityPreconditionError,
    )
}


def forge_error_to_dict(exc: ForgeError) -> dict[str, object]:
    return exc.to_dict()


def error_from_dict(payload: Optional[dict[str, object]]) -> Optional[ForgeError]:
    """Reconstruct any registered error from its canonical dict payload.

    Unregistered codes degrade deterministically to ``UnknownFutureError``
    while preserving the original code and details.
    """
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        return None
    cls = ERROR_CLASSES.get(code)
    if cls is None:
        return UnknownFutureError(
            original_code=code,
            message=payload.get("message"),
            retryable=payload.get("retryable"),
            details=payload.get("details"),
        )
    return cls.from_payload(payload)


def serialize_error(exc: ForgeError) -> dict[str, Any]:
    """Canonical serialization helper for machine envelopes."""
    return exc.to_dict()
