"""Serializable deterministic operation contract descriptor (M2-A).

``OperationContractDescriptor`` is the portable declarative machine contract
for one canonical operation.  It carries only serializable semantics: name,
model-facing inputs, required/optional context, internal IDs, read/write
classification, mutation scope, authority/approval requirements, valid
predecessor/successor state, idempotency semantics, machine-readable error
codes and the protocol version.

A descriptor NEVER carries an executable handler: handler binding is a
runtime-local concern owned by ``HandlerRegistry``.  The descriptor is the
single source for canonical serialization and ``contract_hash``; both are
deterministic across processes, import orders and registration orders, and
never depend on object ids, callable reprs, memory addresses or incidental
dict ordering.

M3/M4 mutation semantics (mutation scope, authority, approval, predecessor/
successor state) are expressed declaratively (empty/none/not-applicable);
this lane does not implement any mutation runtime.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.contracts.canonical import canonicalize
from aota_forge.core.contracts.version import PROTOCOL_VERSION

from aota_forge.core.contracts.errors import DuplicateOperationInputError

READ_ONLY = "read"
WRITE_ONLY = "write"
READ_WRITE = "read-write"

READ_WRITE_CLASSIFICATIONS = frozenset({READ_ONLY, WRITE_ONLY, READ_WRITE})
_UNSET = object()


@dataclass(frozen=True)
class InputSpec:
    """One declarative model-facing input binding: name and executor-neutral type."""

    name: str
    type: str


def ensure_unique_input_names(operation: str, inputs: tuple[InputSpec, ...]) -> None:
    """Reject duplicate semantic input names (I9-B007).

    Each semantic operation input name must occur exactly once within
    ``OperationContractDescriptor.inputs``; identical and conflicting
    duplicate declarations are both invalid contract definitions.
    """
    seen: set[str] = set()
    for spec in inputs:
        if spec.name in seen:
            raise DuplicateOperationInputError(
                f"duplicate operation input declaration in contract '{operation}': {spec.name}",
                details={"operation": operation, "input": spec.name},
            )
        seen.add(spec.name)


@dataclass(frozen=True)
class OperationContractDescriptor:
    """Portable declarative machine contract for one canonical operation.

    All collection fields are tuples so the semantic field order is explicit;
    canonical serialization additionally sorts keys and canonicalizes named
    inputs by name, so any two descriptors with the same semantic fields
    serialize identically.
    """

    name: str
    description: str
    inputs: tuple[InputSpec, ...] | object = _UNSET
    required_context: tuple[str, ...] | object = _UNSET
    optional_context: tuple[str, ...] | object = _UNSET
    internal_ids_required: tuple[str, ...] = field(default_factory=tuple)
    internal_ids_created: tuple[str, ...] = field(default_factory=tuple)
    read_write: str = READ_ONLY
    mutation_scope: str | None | object = _UNSET
    required_authority: str | None | object = _UNSET
    approval_required: bool | object = _UNSET
    valid_predecessor_state: str | None | object = _UNSET
    valid_successor_state: str | None | object = _UNSET
    idempotency: str | None | object = _UNSET
    errors: tuple[str, ...] | object = _UNSET
    protocol_version: str | object = _UNSET
    decision_required: bool | object = _UNSET
    subject_revision_precondition: bool | object = _UNSET
    external_authority_precondition: bool | object = _UNSET
    result_contract: str | None | object = _UNSET

    def __post_init__(self) -> None:
        if self.read_write == READ_ONLY:
            defaults = {
                "inputs": (),
                "required_context": (),
                "optional_context": (),
                "mutation_scope": None,
                "required_authority": None,
                "approval_required": False,
                "valid_predecessor_state": None,
                "valid_successor_state": None,
                "idempotency": None,
                "errors": (),
                "protocol_version": PROTOCOL_VERSION,
                "decision_required": False,
                "subject_revision_precondition": False,
                "external_authority_precondition": False,
                "result_contract": None,
            }
            for name, default in defaults.items():
                if getattr(self, name) is _UNSET:
                    object.__setattr__(self, name, default)
        self.validate()

    def to_dict(self) -> dict[str, Any]:
        """Return a plain JSON-native dict (no callables, no descriptors).

        Inputs are canonicalized by name so declaration order never changes
        the canonical form: named model-facing inputs are order-insensitive
        semantics.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputs": [
                {"name": spec.name, "type": spec.type}
                for spec in sorted(self.inputs, key=lambda item: (item.name, item.type))
            ],
            "required_context": list(self.required_context),
            "optional_context": list(self.optional_context),
            "internal_ids_required": list(self.internal_ids_required),
            "internal_ids_created": list(self.internal_ids_created),
            "read_write": self.read_write,
            "mutation_scope": canonicalize(self.mutation_scope, path="mutation_scope"),
            "required_authority": canonicalize(self.required_authority, path="required_authority"),
            "approval_required": self.approval_required,
            "decision_required": self.decision_required,
            "valid_predecessor_state": canonicalize(self.valid_predecessor_state, path="valid_predecessor_state"),
            "valid_successor_state": canonicalize(self.valid_successor_state, path="valid_successor_state"),
            "subject_revision_precondition": self.subject_revision_precondition,
            "external_authority_precondition": self.external_authority_precondition,
            "idempotency": canonicalize(self.idempotency, path="idempotency"),
            "result_contract": canonicalize(self.result_contract, path="result_contract"),
            "errors": list(self.errors),
            "protocol_version": self.protocol_version,
        }

    def to_canonical_json(self) -> str:
        """Deterministic canonical serialization (sorted keys, no incidental ordering)."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def to_canonical_bytes(self) -> bytes:
        return self.to_canonical_json().encode("utf-8")

    def contract_hash(self) -> str:
        """Deterministic semantic hash: canonical serialization incl. protocol_version.

        Excludes handler callable identity, memory addresses and any
        runtime-local registry state by construction: the descriptor carries
        no such values.
        """
        self.validate()
        return hashlib.sha256(self.to_canonical_bytes()).hexdigest()

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("descriptor name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"descriptor description must be a non-empty string: {self.name}")
        if self.read_write not in READ_WRITE_CLASSIFICATIONS:
            raise ValueError(
                f"descriptor read_write must be one of {sorted(READ_WRITE_CLASSIFICATIONS)}: {self.name}"
            )
        if self.read_write != READ_ONLY:
            required = {
                "inputs": self.inputs,
                "required_context": self.required_context,
                "optional_context": self.optional_context,
                "mutation_scope": self.mutation_scope,
                "required_authority": self.required_authority,
                "approval_required": self.approval_required,
                "decision_required": self.decision_required,
                "valid_predecessor_state": self.valid_predecessor_state,
                "valid_successor_state": self.valid_successor_state,
                "subject_revision_precondition": self.subject_revision_precondition,
                "external_authority_precondition": self.external_authority_precondition,
                "idempotency": self.idempotency,
                "result_contract": self.result_contract,
                "errors": self.errors,
                "protocol_version": self.protocol_version,
            }
            missing = [name for name, value in required.items() if value is _UNSET]
            if missing:
                raise ValueError(
                    f"write descriptor is missing required declarations: {', '.join(missing)}: {self.name}"
                )
            for name in (
                "mutation_scope",
                "required_authority",
                "valid_predecessor_state",
                "valid_successor_state",
                "idempotency",
                "result_contract",
            ):
                value = getattr(self, name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"write descriptor {name} must be a non-empty string: {self.name}")
            for name in (
                "approval_required",
                "decision_required",
                "subject_revision_precondition",
                "external_authority_precondition",
            ):
                if not isinstance(getattr(self, name), bool):
                    raise ValueError(f"write descriptor {name} must be a boolean: {self.name}")
        if self.inputs is _UNSET or self.required_context is _UNSET or self.optional_context is _UNSET:
            raise ValueError(f"descriptor collection declarations are missing: {self.name}")
        if self.errors is _UNSET or self.protocol_version is _UNSET:
            raise ValueError(f"descriptor error/protocol declarations are missing: {self.name}")
        if not isinstance(self.inputs, tuple):
            raise ValueError(f"descriptor inputs must be a tuple: {self.name}")
        if not isinstance(self.protocol_version, str) or not self.protocol_version:
            raise ValueError(f"descriptor protocol_version must be a non-empty string: {self.name}")
        for spec in self.inputs:
            if not isinstance(spec, InputSpec) or not spec.name or not spec.type:
                raise ValueError(f"descriptor inputs must be InputSpec entries with name and type: {self.name}")
        ensure_unique_input_names(self.name, self.inputs)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OperationContractDescriptor":
        """Rebuild a descriptor from its plain dict form (JSON round-trip)."""
        raw_inputs = raw.get("inputs", _UNSET)
        descriptor = cls(
            name=raw["name"],
            description=raw["description"],
            inputs=(
                _UNSET
                if raw_inputs is _UNSET
                else tuple(InputSpec(name=item["name"], type=item["type"]) for item in raw_inputs)
            ),
            required_context=(
                _UNSET if "required_context" not in raw else tuple(raw["required_context"])
            ),
            optional_context=(
                _UNSET if "optional_context" not in raw else tuple(raw["optional_context"])
            ),
            internal_ids_required=tuple(raw.get("internal_ids_required", [])),
            internal_ids_created=tuple(raw.get("internal_ids_created", [])),
            read_write=raw.get("read_write", READ_ONLY),
            mutation_scope=raw.get("mutation_scope", _UNSET),
            required_authority=raw.get("required_authority", _UNSET),
            approval_required=raw.get("approval_required", _UNSET),
            decision_required=raw.get("decision_required", _UNSET),
            valid_predecessor_state=raw.get("valid_predecessor_state", _UNSET),
            valid_successor_state=raw.get("valid_successor_state", _UNSET),
            subject_revision_precondition=raw.get("subject_revision_precondition", _UNSET),
            external_authority_precondition=raw.get("external_authority_precondition", _UNSET),
            idempotency=raw.get("idempotency", _UNSET),
            result_contract=raw.get("result_contract", _UNSET),
            errors=_UNSET if "errors" not in raw else tuple(raw["errors"]),
            protocol_version=raw.get("protocol_version", _UNSET),
        )
        return descriptor


PLAN_INIT_OPERATION = "plan_init"
PLAN_RETIREMENT_OPERATION = "plan_retirement"

PLAN_INIT_DESCRIPTOR = OperationContractDescriptor(
    name=PLAN_INIT_OPERATION,
    description="Mechanically initialize one exact Plan Subject after semantic choice.",
    inputs=(
        InputSpec("plan_ref", "str"),
        InputSpec("project_binding", "dict"),
        InputSpec("semantic_inputs", "dict"),
        InputSpec("subject_expected_revision", "int"),
        InputSpec("authority_source_revision", "str"),
        InputSpec("authority_observed_raw_digest", "str"),
    ),
    required_context=("principal",),
    optional_context=("project_binding",),
    internal_ids_required=("subject",),
    internal_ids_created=(),
    read_write=WRITE_ONLY,
    mutation_scope="plan_subject",
    required_authority="semantic_authorization_and_operation_lease",
    approval_required=True,
    valid_predecessor_state="uninitialized",
    valid_successor_state="initialized",
    idempotency="same intent replays; changed intent conflicts",
    errors=(
        "PROJECT_NOT_FOUND",
        "NEEDS_SEMANTIC_CHOICE",
        "PROJECT_BINDING_REQUIRED",
        "PLAN_INIT_INVALID_PREDECESSOR",
        "PLAN_INIT_ALREADY_INITIALIZED",
        "PLAN_INIT_STALE_SUBJECT_REVISION",
        "PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
    ),
    protocol_version=PROTOCOL_VERSION,
    decision_required=True,
    subject_revision_precondition=True,
    external_authority_precondition=True,
    result_contract="canonical_mutation_result.v1",
)

PLAN_RETIREMENT_DESCRIPTOR = OperationContractDescriptor(
    name=PLAN_RETIREMENT_OPERATION,
    description="Mechanically retire one exact Plan selected from a bounded snapshot.",
    inputs=(
        InputSpec("plan_ref", "str"),
        InputSpec("retirement_kind", "str"),
        InputSpec("snapshot_identity", "str"),
        InputSpec("subject_expected_revision", "int"),
        InputSpec("authority_source_revision", "str"),
        InputSpec("authority_observed_raw_digest", "str"),
        InputSpec("successor_ref", "str?"),
    ),
    required_context=("principal",),
    optional_context=("project_binding",),
    internal_ids_required=("subject",),
    internal_ids_created=(),
    read_write=WRITE_ONLY,
    mutation_scope="plan_subject",
    required_authority="semantic_authorization_and_operation_lease",
    approval_required=True,
    valid_predecessor_state="initialized",
    valid_successor_state="cancelled|superseded",
    idempotency="same intent replays; changed intent conflicts",
    errors=(
        "RETIREMENT_NO_CANDIDATE",
        "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
        "RETIREMENT_STALE_SNAPSHOT",
        "RETIREMENT_TARGET_PROTECTED",
        "RETIREMENT_RUNNING_TASK_PROTECTED",
        "RETIREMENT_SUCCESSOR_REQUIRED",
        "RETIREMENT_SUCCESSOR_INVALID",
        "RETIREMENT_SELF_SUCCESSOR",
        "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
    ),
    protocol_version=PROTOCOL_VERSION,
    decision_required=True,
    subject_revision_precondition=True,
    external_authority_precondition=True,
    result_contract="canonical_mutation_result.v1",
)

LIFECYCLE_DESCRIPTORS = (PLAN_INIT_DESCRIPTOR, PLAN_RETIREMENT_DESCRIPTOR)
