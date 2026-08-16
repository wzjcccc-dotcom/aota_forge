"""Canonical OperationContext and read-only ContextResolver (M1 / M2-B).

``OperationContext`` is the executor-neutral canonical context assembled by
the ingress.  It carries the operation contract identity (operation,
protocol_version, contract_hash), the correlation id, the principal, the
validated semantic inputs, the explicitly enumerated trusted adapter
metadata, and known project/workspace context where current read contracts
allow it.

``ContextResolver`` is a minimal executor-neutral resolver.  It only does
deterministic context assembly, trusted adapter context interpretation and
operation contract context requirement evaluation.  It MUST NOT:

* search current_* pointers or choose a current subject
* create durable subjects, mint authority or mint capability leases
* bind followups, select Work Items or repair lifecycle state
* perform semantic project selection

Context requirements M2 cannot satisfy yield a deterministic
``CONTEXT_NOT_SUPPORTED`` error; future-milestone context is never
fabricated here.

Reserved future fields (``workflow``, ``subject``, ``internal_ids``,
``revision``, ``authority``, ``capability_lease``) exist only as ``None``
placeholders and are NOT authority in M2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ContextNotSupportedError
from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS

# Context keys the resolver can satisfy statically without any project,
# subject, plan, authority or lifecycle machinery.
SATISFIABLE_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"principal", "operation", "correlation_id", "protocol_version", "contract"}
)


@dataclass(frozen=True)
class OperationContext:
    """Executor-neutral canonical operation context."""

    operation: str
    correlation_id: str
    principal: str
    protocol_version: str
    contract_hash: str
    params: dict[str, Any] = field(default_factory=dict)
    semantic_inputs: dict[str, Any] = field(default_factory=dict)
    trusted: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] | None = None
    workspace: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    # Reserved future-milestone placeholders; MUST remain None in M2 and
    # must never act as authority.
    workflow: None = None
    subject: None = None
    internal_ids: None = None
    revision: None = None
    authority: None = None
    capability_lease: None = None

    def to_dict(self) -> dict[str, Any]:
        """Bounded metadata view: never echoes params/trusted values."""
        return {
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "principal": self.principal,
            "protocol_version": self.protocol_version,
            "contract_hash": self.contract_hash,
            "workspace": self.workspace,
            "project": self.project,
            "session": self.session,
        }


class ContextResolver:
    """Minimal executor-neutral read-only context resolver."""

    def resolve(
        self,
        descriptor: OperationContractDescriptor,
        validated_params: dict[str, Any],
        principal: str,
        correlation_id: str,
    ) -> OperationContext:
        for key in descriptor.required_context:
            if key not in SATISFIABLE_CONTEXT_KEYS:
                raise ContextNotSupportedError(
                    f"operation context not supported in M2: {key}"
                )
        trusted = {
            key: validated_params[key]
            for key in TRUSTED_ADAPTER_KEYS
            if key in validated_params
        }
        semantic_inputs = {
            key: value
            for key, value in validated_params.items()
            if key not in TRUSTED_ADAPTER_KEYS
        }
        return OperationContext(
            operation=descriptor.name,
            correlation_id=correlation_id,
            principal=principal,
            protocol_version=descriptor.protocol_version,
            contract_hash=descriptor.contract_hash(),
            params=validated_params,
            semantic_inputs=semantic_inputs,
            trusted=trusted,
        )
