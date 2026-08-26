"""Canonical vocabulary & identity boundaries — S1/M1/W1.

Minimal semantic declaration of five canonical concepts without building
a new runtime framework:

- Operation — semantic description of what should happen
- Task      — Control Plane managed unit of work (S1: identity/reference only)
- Execution — one executor attempt for a Task (S1: identity/reference only)
- Capability — what an executor/tool/provider declares it can perform
- Result    — governed outcome / observation of an Operation or Execution

Freezes (source-level constants for machine tests):

    TASK_FULL_MODEL_DEFERRED_TO_S2 = yes
    EXECUTION_FULL_MODEL_DEFERRED_TO_S2_S3 = yes
    OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION = yes
    TASK_IDENTITY_STABLE_ACROSS_EXECUTION_ATTEMPTS = yes
    TASK_IDENTITY_DISTINCT_FROM_EXECUTION_ATTEMPT_IDENTITY = yes
    CAPABILITY_FIRST_CLASS = yes, CAPABILITY_IS_PROFILE = no, CAPABILITY_IS_EXECUTOR = no
    RESULT_CONTRACT_IDENTITY_DEFINED = yes, RESULT_INSTANCE_DISTINCT = yes
    HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY = yes (Plan acceptance; implemented generically)

NewType semantics (corrected):

    NEWTYPE_RUNTIME_REPRESENTATION = str
    NEWTYPE_STATIC_DOMAIN_SEPARATION = yes
    NEWTYPE_RUNTIME_NOMINAL_SEPARATION = no

    Python typing.NewType provides static nominal typing distinction only.
    At runtime values remain plain strings.  ``isinstance(TaskRef(...), TaskRef)``
    does not distinguish domains and ``type(TaskRef("x")) is str``.  Domain
    separation is contractual/static-semantic, not encoded in runtime object type.

Design choices (reuse-first):

- Operation semantic identity = protocol family + operation name
- Contract revision fingerprint = protocol version + deterministic contract hash
- Task identity = canonical_task_id (opaque stable reference, reused from ExecutionPackage)
- Execution attempt identity = execution attempt reference (opaque, distinct type)
- Capability = ExecutorCapabilities (first-class, not profile, not executor)
- Result contract identity = OperationContractDescriptor.result_contract (opaque reference)

Runtime locator principle (generic):

    RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY = yes

    A runtime locator (process handle, worker handle, session handle, etc.)
    must never be required as canonical identity.  Canonical Core defines
    only the generic principle and does not enumerate executor-specific
    runtime systems.

No TaskDescriptor, ExecutionDescriptor, ID broker, dispatch, lifecycle,
or YAML catalog is created here.  Existing ExecutionPackage,
CanonicalResult, ExecutorCapabilities remain the executor-neutral evidence
and are reused without duplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.version import OPERATION_CONTRACT_PROTOCOL, PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Freeze constants — machine-testable semantic flags (W1 acceptance)
# ---------------------------------------------------------------------------

TASK_FULL_MODEL_DEFERRED_TO_S2: bool = True
EXECUTION_FULL_MODEL_DEFERRED_TO_S2_S3: bool = True

OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION: bool = True
CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT: bool = True

TASK_IDENTITY_STABLE_ACROSS_EXECUTION_ATTEMPTS: bool = True
TASK_IDENTITY_DISTINCT_FROM_EXECUTION_ATTEMPT_IDENTITY: bool = True

CAPABILITY_FIRST_CLASS: bool = True
CAPABILITY_IS_PROFILE: bool = False
CAPABILITY_IS_EXECUTOR: bool = False

RESULT_CONTRACT_IDENTITY_DEFINED: bool = True
RESULT_INSTANCE_DISTINCT: bool = True

# Plan acceptance statement — must remain true.  Implementation is proven
# via the generic principle below, not via Hermes-specific knowledge.
HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY: bool = True

# Generic architectural rule — runtime locator is not canonical identity.
RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY: bool = True
RUNTIME_LOCATOR_IS_NOT_CANONICAL_IDENTITY: bool = True

CANONICAL_VOCABULARY_DEFINED: bool = True
OPERATION_TASK_EXECUTION_DISTINCT: bool = True

# Negative creation guards (must remain false)
NEW_TASK_DESCRIPTOR_CREATED: bool = False
NEW_EXECUTION_DESCRIPTOR_CREATED: bool = False
NEW_ID_BROKER_CREATED: bool = False

W2_IMPLEMENTED: bool = False
W3_IMPLEMENTED: bool = False
M2_IMPLEMENTED: bool = False

# NewType runtime semantics — explicit machine-testable flags
NEWTYPE_RUNTIME_REPRESENTATION: str = "str"
NEWTYPE_STATIC_DOMAIN_SEPARATION: bool = True
NEWTYPE_RUNTIME_NOMINAL_SEPARATION: bool = False

# ---------------------------------------------------------------------------
# Vocabulary type aliases — distinct semantic domains (static only)
# ---------------------------------------------------------------------------

# Opaque canonical references — NewType provides static nominal typing
# distinction for type checkers only.  At runtime values are plain ``str``
# objects (NEWTYPE_RUNTIME_REPRESENTATION=str).  Runtime ``isinstance`` or
# ``type()`` checks cannot distinguish TaskRef from ExecutionAttemptRef;
# domain separation is contractual/static-semantic
# (NEWTYPE_STATIC_DOMAIN_SEPARATION=yes, NEWTYPE_RUNTIME_NOMINAL_SEPARATION=no).
TaskRef = NewType("TaskRef", str)
ExecutionAttemptRef = NewType("ExecutionAttemptRef", str)
ResultContractRef = NewType("ResultContractRef", str)

# Capability domain is represented by the existing ExecutorCapabilities class.
# Alias kept for vocabulary clarity without creating a new class.
CapabilityRef = NewType("CapabilityRef", str)


# ---------------------------------------------------------------------------
# Operation semantic identity vs contract revision fingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationSemanticIdentity:
    """Stable semantic identity: protocol family + operation name.

    Distinct from contract revision.  Changing only description, errors,
    or other descriptor fields changes the revision fingerprint but leaves
    semantic identity stable.  Changing operation name or protocol family
    changes semantic identity.
    """

    protocol_family: str
    operation_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_family, str) or not self.protocol_family.strip():
            raise ValueError("protocol_family must be a non-empty string")
        if not isinstance(self.operation_name, str) or not self.operation_name.strip():
            raise ValueError("operation_name must be a non-empty string")


@dataclass(frozen=True)
class ContractRevisionFingerprint:
    """Revision fingerprint: protocol version + deterministic contract hash.

    contract_hash is the SHA-256 of the canonical descriptor serialization
    (which already includes protocol_version).  It is NOT the operation
    semantic identity.
    """

    protocol_version: str
    contract_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_version, str) or not self.protocol_version.strip():
            raise ValueError("protocol_version must be a non-empty string")
        if not isinstance(self.contract_hash, str) or not self.contract_hash.strip():
            raise ValueError("contract_hash must be a non-empty string")


def operation_semantic_identity_of(descriptor: OperationContractDescriptor) -> OperationSemanticIdentity:
    """Derive stable semantic identity for an operation descriptor.

    Uses the canonical protocol family constant (not version) plus the
    operation name.  This value does not change when the descriptor's
    description or errors change (which would change contract_hash).
    """
    if not isinstance(descriptor, OperationContractDescriptor):
        raise TypeError(f"descriptor must be OperationContractDescriptor, got {type(descriptor).__name__}")
    return OperationSemanticIdentity(
        protocol_family=OPERATION_CONTRACT_PROTOCOL,
        operation_name=descriptor.name,
    )


def contract_revision_of(descriptor: OperationContractDescriptor) -> ContractRevisionFingerprint:
    """Derive revision fingerprint for an operation descriptor.

    Combines descriptor.protocol_version and descriptor.contract_hash().
    Stable across call sites (deterministic), changes when any semantic
    descriptor field changes.
    """
    if not isinstance(descriptor, OperationContractDescriptor):
        raise TypeError(f"descriptor must be OperationContractDescriptor, got {type(descriptor).__name__}")
    return ContractRevisionFingerprint(
        protocol_version=str(descriptor.protocol_version),
        contract_hash=descriptor.contract_hash(),
    )


def semantic_identity_distinct_from_revision(
    semantic: OperationSemanticIdentity,
    revision: ContractRevisionFingerprint,
) -> bool:
    """Return True iff semantic identity is structurally distinct from revision.

    They occupy different type domains and never alias.  This is a
    machine-testable guard for OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION.
    """
    if not isinstance(semantic, OperationSemanticIdentity):
        raise TypeError("semantic must be OperationSemanticIdentity")
    if not isinstance(revision, ContractRevisionFingerprint):
        raise TypeError("revision must be ContractRevisionFingerprint")
    # Type-domain separation is structural; value comparison is never equality.
    return type(semantic) is not type(revision)


# ---------------------------------------------------------------------------
# Task vs Execution identity boundary
# ---------------------------------------------------------------------------


def task_ref(value: str) -> TaskRef:
    """Create an opaque canonical Task reference (stable across attempts).

    Validates only generic structural requirements appropriate for an opaque
    canonical reference: must be ``str``, non-empty after stripping.  No
    string-prefix inference is performed — whether a runtime locator is
    incorrectly being used as a Task identity is caught at the
    integration/mapping boundary, not by hard-coded prefix parsing in
    canonical Core.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("task identity must be a non-empty string")
    return TaskRef(value.strip())


def execution_attempt_ref(value: str) -> ExecutionAttemptRef:
    """Create an opaque Execution-attempt reference (distinct from Task).

    Validates only generic structural requirements: non-empty string.
    Domain distinction from Task is static-semantic, not runtime type or
    string inequality.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("execution attempt identity must be a non-empty string")
    return ExecutionAttemptRef(value.strip())


def result_contract_ref(value: str | None) -> ResultContractRef | None:
    """Interpret OperationContractDescriptor.result_contract as opaque identity ref.

    W1 semantics: `result_contract: str` is an opaque canonical
    result-contract identity/reference, distinct from the runtime result
    payload instance (CanonicalResult / result_data).  None means no
    declared contract.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("result_contract must be a non-empty string or None")
    return ResultContractRef(value.strip())


# ---------------------------------------------------------------------------
# Runtime locator vs semantic identity isolation (generic)
# ---------------------------------------------------------------------------

CANONICAL_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "task_ref",
        "execution_attempt_ref",
        "operation_semantic_identity",
        "result_contract_ref",
    }
)

# Generic runtime locator field examples — intentionally generic categories,
# not an exhaustive executor-specific enumeration.  Canonical Core does not
# enumerate Hermes / OpenCode / Codex / other adapter names.
RUNTIME_LOCATOR_FIELDS: frozenset[str] = frozenset(
    {
        "process_id",
        "worker_handle",
        "session_handle",
        "package_id",
        "correlation_id_as_execution_identity",
    }
)


def is_runtime_locator(field_name: str) -> bool:
    return field_name in RUNTIME_LOCATOR_FIELDS


def is_canonical_identity_field(field_name: str) -> bool:
    return field_name in CANONICAL_IDENTITY_FIELDS


# ---------------------------------------------------------------------------
# Vocabulary domain enumeration (for A: domain non-alias tests)
# ---------------------------------------------------------------------------

VOCABULARY_DOMAINS: tuple[str, ...] = (
    "Operation",
    "Task",
    "Execution",
    "Capability",
    "Result",
)

VOCABULARY_DISTINCT: bool = True
