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
    inputs: tuple[InputSpec, ...] = field(default_factory=tuple)
    required_context: tuple[str, ...] = field(default_factory=tuple)
    optional_context: tuple[str, ...] = field(default_factory=tuple)
    internal_ids_required: tuple[str, ...] = field(default_factory=tuple)
    internal_ids_created: tuple[str, ...] = field(default_factory=tuple)
    read_write: str = READ_ONLY
    mutation_scope: str | None = None
    required_authority: str | None = None
    approval_required: bool = False
    valid_predecessor_state: str | None = None
    valid_successor_state: str | None = None
    idempotency: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)
    protocol_version: str = PROTOCOL_VERSION
    decision_required: bool = False
    subject_revision_precondition: bool = False
    external_authority_precondition: bool = False
    result_contract: str | None = None

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
        if not isinstance(self.protocol_version, str) or not self.protocol_version:
            raise ValueError(f"descriptor protocol_version must be a non-empty string: {self.name}")
        for spec in self.inputs:
            if not isinstance(spec, InputSpec) or not spec.name or not spec.type:
                raise ValueError(f"descriptor inputs must be InputSpec entries with name and type: {self.name}")
        ensure_unique_input_names(self.name, self.inputs)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OperationContractDescriptor":
        """Rebuild a descriptor from its plain dict form (JSON round-trip)."""
        descriptor = cls(
            name=raw["name"],
            description=raw["description"],
            inputs=tuple(InputSpec(name=item["name"], type=item["type"]) for item in raw.get("inputs", [])),
            required_context=tuple(raw.get("required_context", [])),
            optional_context=tuple(raw.get("optional_context", [])),
            internal_ids_required=tuple(raw.get("internal_ids_required", [])),
            internal_ids_created=tuple(raw.get("internal_ids_created", [])),
            read_write=raw.get("read_write", READ_ONLY),
            mutation_scope=raw.get("mutation_scope"),
            required_authority=raw.get("required_authority"),
            approval_required=bool(raw.get("approval_required", False)),
            decision_required=bool(raw.get("decision_required", False)),
            valid_predecessor_state=raw.get("valid_predecessor_state"),
            valid_successor_state=raw.get("valid_successor_state"),
            subject_revision_precondition=bool(raw.get("subject_revision_precondition", False)),
            external_authority_precondition=bool(raw.get("external_authority_precondition", False)),
            idempotency=raw.get("idempotency"),
            result_contract=raw.get("result_contract"),
            errors=tuple(raw.get("errors", [])),
            protocol_version=raw.get("protocol_version", PROTOCOL_VERSION),
        )
        descriptor.validate()
        return descriptor
