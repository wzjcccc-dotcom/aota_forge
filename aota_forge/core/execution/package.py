"""ExecutionPackage contract definition and deterministic fingerprinting (M5-1).

Executor-neutral envelope containing task instructions, constraints, and requirements.
Distinct identity domains: package_id, canonical_task_id, idempotency_key, correlation_id,
and intent_fingerprint. Rejects Hermes-private fields and arbitrary command surface.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.roles import validate_canonical_role

PROTOCOL_VERSION: str = "1.0.0"
ALLOWED_OPERATIONS: frozenset[str] = frozenset({"task_dispatch", "task_resume"})

FORBIDDEN_HERMES_FIELDS: frozenset[str] = frozenset({
    "hermes_profile_object",
    "hermes_worker_object",
    "hermes_session_object",
    "hermes_task_object",
    "hermes_toolset_object",
    "hermes_home_path",
})

EXECUTION_CONTRACT_HASH: str = hashlib.sha256(
    b"aota_forge.core.execution.ExecutionPackage.v1.0.0"
).hexdigest()


def compute_intent_fingerprint(
    canonical_role: str,
    instruction: str,
    input_artifacts: tuple[dict, ...] | list[dict],
    project_id: str,
) -> str:
    """Deterministic SHA-256 fingerprint of semantic intent."""
    validate_canonical_role(canonical_role)
    payload = {
        "canonical_role": canonical_role,
        "input_artifacts": canonicalize(input_artifacts, path="input_artifacts"),
        "instruction": str(instruction),
        "project_id": str(project_id),
    }
    encoded = canonical_json(payload)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compute_package_fingerprint(package_or_dict: ExecutionPackage | Mapping[str, Any]) -> str:
    """Deterministic SHA-256 fingerprint of the full execution package payload."""
    if isinstance(package_or_dict, ExecutionPackage):
        payload = package_or_dict.to_dict()
    elif isinstance(package_or_dict, Mapping):
        payload = dict(package_or_dict)
    else:
        raise TypeError(f"Expected ExecutionPackage or Mapping, got {type(package_or_dict).__name__}")
    encoded = canonical_json(payload)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionPackage:
    """Frozen canonical execution package dispatched to an executor adapter."""

    package_id: str
    protocol_version: str
    contract_hash: str
    operation: str
    canonical_task_id: str
    subject_ref: str | None
    project_id: str
    canonical_role: str
    instruction: str
    input_artifacts: tuple[dict, ...]
    working_context: dict
    capability_requirements: dict
    constraints: dict
    idempotency_key: str
    intent_fingerprint: str
    correlation_id: str
    result_expectations: dict

    def __post_init__(self) -> None:
        if not isinstance(self.package_id, str) or not self.package_id.strip():
            raise ValueError("package_id must be a non-empty string")
        if not isinstance(self.protocol_version, str) or not self.protocol_version.strip():
            raise ValueError("protocol_version must be a non-empty string")
        if not isinstance(self.contract_hash, str) or not self.contract_hash.strip():
            raise ValueError("contract_hash must be a non-empty string")

        if self.operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"operation must be one of {sorted(ALLOWED_OPERATIONS)}, got {self.operation!r}")

        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")

        if self.subject_ref is not None:
            if not isinstance(self.subject_ref, str) or not self.subject_ref.strip():
                raise ValueError("subject_ref must be None or a non-empty string")

        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id must be a non-empty string")

        validate_canonical_role(self.canonical_role)

        if not isinstance(self.instruction, str):
            raise TypeError(f"instruction must be a string, got {type(self.instruction).__name__}")
        if not self.instruction.strip():
            raise ValueError("instruction must be a non-empty string")

        # input_artifacts must be a tuple/list of dicts
        if not isinstance(self.input_artifacts, (tuple, list)):
            raise TypeError(f"input_artifacts must be a tuple or list, got {type(self.input_artifacts).__name__}")
        for idx, item in enumerate(self.input_artifacts):
            if not isinstance(item, Mapping):
                raise TypeError(f"input_artifacts[{idx}] must be a dict, got {type(item).__name__}")
        normalized_artifacts = tuple(canonicalize(item, path=f"input_artifacts[{i}]") for i, item in enumerate(self.input_artifacts))
        object.__setattr__(self, "input_artifacts", normalized_artifacts)

        # mappings
        if not isinstance(self.working_context, Mapping):
            raise TypeError(f"working_context must be a dict/mapping, got {type(self.working_context).__name__}")
        object.__setattr__(self, "working_context", canonicalize(self.working_context, path="working_context"))

        if not isinstance(self.capability_requirements, Mapping):
            raise TypeError(f"capability_requirements must be a dict/mapping, got {type(self.capability_requirements).__name__}")
        object.__setattr__(self, "capability_requirements", canonicalize(self.capability_requirements, path="capability_requirements"))

        if not isinstance(self.constraints, Mapping):
            raise TypeError(f"constraints must be a dict/mapping, got {type(self.constraints).__name__}")
        object.__setattr__(self, "constraints", canonicalize(self.constraints, path="constraints"))

        if not isinstance(self.result_expectations, Mapping):
            raise TypeError(f"result_expectations must be a dict/mapping, got {type(self.result_expectations).__name__}")
        object.__setattr__(self, "result_expectations", canonicalize(self.result_expectations, path="result_expectations"))

        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError("correlation_id must be a non-empty string")

        # Validate intent fingerprint
        expected_intent_fp = compute_intent_fingerprint(
            canonical_role=self.canonical_role,
            instruction=self.instruction,
            input_artifacts=self.input_artifacts,
            project_id=self.project_id,
        )
        if self.intent_fingerprint != expected_intent_fp:
            raise ValueError(
                f"intent_fingerprint mismatch: got {self.intent_fingerprint!r}, expected {expected_intent_fp!r}"
            )

    def package_fingerprint(self) -> str:
        return compute_package_fingerprint(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "protocol_version": self.protocol_version,
            "contract_hash": self.contract_hash,
            "operation": self.operation,
            "canonical_task_id": self.canonical_task_id,
            "subject_ref": self.subject_ref,
            "project_id": self.project_id,
            "canonical_role": self.canonical_role,
            "instruction": self.instruction,
            "input_artifacts": list(self.input_artifacts),
            "working_context": self.working_context,
            "capability_requirements": self.capability_requirements,
            "constraints": self.constraints,
            "idempotency_key": self.idempotency_key,
            "intent_fingerprint": self.intent_fingerprint,
            "correlation_id": self.correlation_id,
            "result_expectations": self.result_expectations,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionPackage:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")

        # Check for forbidden Hermes fields
        for field in FORBIDDEN_HERMES_FIELDS:
            if field in data:
                raise ValueError(f"Forbidden Hermes-private field rejected: {field!r}")

        required_fields = (
            "package_id",
            "protocol_version",
            "contract_hash",
            "operation",
            "canonical_task_id",
            "project_id",
            "canonical_role",
            "instruction",
            "input_artifacts",
            "working_context",
            "capability_requirements",
            "constraints",
            "idempotency_key",
            "intent_fingerprint",
            "correlation_id",
            "result_expectations",
        )
        for req in required_fields:
            if req not in data:
                raise ValueError(f"Missing required field in ExecutionPackage: {req!r}")

        return cls(
            package_id=str(data["package_id"]),
            protocol_version=str(data["protocol_version"]),
            contract_hash=str(data["contract_hash"]),
            operation=str(data["operation"]),
            canonical_task_id=str(data["canonical_task_id"]),
            subject_ref=str(data["subject_ref"]) if data.get("subject_ref") is not None else None,
            project_id=str(data["project_id"]),
            canonical_role=str(data["canonical_role"]),
            instruction=str(data["instruction"]),
            input_artifacts=tuple(data["input_artifacts"]),
            working_context=dict(data["working_context"]),
            capability_requirements=dict(data["capability_requirements"]),
            constraints=dict(data["constraints"]),
            idempotency_key=str(data["idempotency_key"]),
            intent_fingerprint=str(data["intent_fingerprint"]),
            correlation_id=str(data["correlation_id"]),
            result_expectations=dict(data["result_expectations"]),
        )

    @classmethod
    def create(
        cls,
        canonical_task_id: str,
        project_id: str,
        canonical_role: str,
        instruction: str,
        operation: str = "task_dispatch",
        package_id: str | None = None,
        protocol_version: str = PROTOCOL_VERSION,
        contract_hash: str = EXECUTION_CONTRACT_HASH,
        subject_ref: str | None = None,
        input_artifacts: tuple[dict, ...] | list[dict] = (),
        working_context: dict[str, Any] | None = None,
        capability_requirements: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        result_expectations: dict[str, Any] | None = None,
    ) -> ExecutionPackage:
        norm_artifacts = tuple(input_artifacts) if isinstance(input_artifacts, (tuple, list)) else ()
        intent_fp = compute_intent_fingerprint(
            canonical_role=canonical_role,
            instruction=instruction,
            input_artifacts=norm_artifacts,
            project_id=project_id,
        )
        return cls(
            package_id=package_id or str(uuid.uuid4()),
            protocol_version=protocol_version,
            contract_hash=contract_hash,
            operation=operation,
            canonical_task_id=canonical_task_id,
            subject_ref=subject_ref,
            project_id=project_id,
            canonical_role=canonical_role,
            instruction=instruction,
            input_artifacts=norm_artifacts,
            working_context=working_context if working_context is not None else {},
            capability_requirements=capability_requirements if capability_requirements is not None else {},
            constraints=constraints if constraints is not None else {},
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            intent_fingerprint=intent_fp,
            correlation_id=correlation_id or str(uuid.uuid4()),
            result_expectations=result_expectations if result_expectations is not None else {},
        )
