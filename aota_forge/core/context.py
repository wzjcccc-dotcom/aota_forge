"""Canonical operation context and trusted principal boundary (M1 / M2-B).

``OperationContext`` is the executor-neutral canonical context assembled by
the ingress.  It carries the operation contract identity (operation,
protocol_version, contract_hash), the correlation id, the typed principal
bound by a trusted runtime context, the validated semantic inputs, the
explicitly enumerated trusted adapter metadata, and known project/workspace
context where current read contracts allow it.

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

Free-form caller values are not principal identity.  ``TrustedContext`` is
the narrow injection carrier for runtime-bound principal metadata.  Its
private binding token keeps an ordinary model/user value from being promoted
to a trusted principal merely by matching the public dataclass shape.

Reserved future fields (``workflow``, ``subject``, ``internal_ids``,
``revision``, ``authority``, ``capability_lease``) exist only as ``None``
placeholders and are NOT authority in M2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ContextNotSupportedError
from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS

# Context keys the resolver can satisfy statically without any project,
# subject, plan, authority or lifecycle machinery.
SATISFIABLE_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"principal", "operation", "correlation_id", "protocol_version", "contract", "project_binding"}
)

_SAFE_CONTEXT_VALUE = re.compile(r"^[A-Za-z0-9._:@-]{1,256}$")
_TRUSTED_CONTEXT_TOKEN = object()


def _check_context_value(name: str, value: str | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not _SAFE_CONTEXT_VALUE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded safe identifier")


@dataclass(frozen=True)
class Principal:
    """Typed executor-neutral principal identity.

    The type is intentionally generic: no executor, vendor or session
    ontology is required by Core.  A ``Principal`` becomes trusted only when
    carried by a runtime-bound ``TrustedContext``.
    """

    principal_id: str
    principal_type: str
    provenance: str
    channel: str
    freshness: str | None = None

    def __post_init__(self) -> None:
        _check_context_value("principal_id", self.principal_id)
        _check_context_value("principal_type", self.principal_type)
        _check_context_value("provenance", self.provenance)
        _check_context_value("channel", self.channel)
        _check_context_value("freshness", self.freshness, optional=True)

    @property
    def id(self) -> str:
        """Stable identity alias used by bounded audit projections."""
        return self.principal_id

    @property
    def kind(self) -> str:
        """Executor-neutral type alias."""
        return self.principal_type

    def to_audit(self) -> dict[str, str | None]:
        """Return safe principal identity metadata, never credentials or paths."""
        return {
            "id": self.principal_id,
            "type": self.principal_type,
            "provenance": self.provenance,
            "channel": self.channel,
            "freshness": self.freshness,
        }


@dataclass(frozen=True)
class TrustedContext:
    """Runtime-supplied trusted context for one operation invocation."""

    principal: Principal | None = None
    channel: str = "runtime"
    provenance: str = "trusted_runtime"
    freshness: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    _binding_token: object = field(default=None, repr=False, compare=False)

    @property
    def is_bound(self) -> bool:
        return self._binding_token is _TRUSTED_CONTEXT_TOKEN


UNBOUND_PRINCIPAL = Principal(
    principal_id="unbound",
    principal_type="unbound",
    provenance="untrusted_input_ignored",
    channel="none",
)


@dataclass(frozen=True)
class PrincipalResolution:
    """Normalized principal and trust provenance for one invocation."""

    principal: Principal
    trust: str
    trusted_context: TrustedContext | None = None

    def to_audit(self) -> dict[str, Any]:
        result = dict(self.principal.to_audit())
        result["trust"] = self.trust
        return result


def bind_trusted_context(
    principal: Principal | None = None,
    *,
    principal_id: str | None = None,
    principal_type: str | None = None,
    provenance: str = "trusted_runtime",
    channel: str = "runtime",
    freshness: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> TrustedContext:
    """Create the runtime-bound context accepted by Core.

    A context without a principal is useful for trusted resource provenance
    on a safe read-only operation, but it never creates a privileged identity.
    """
    if principal is not None and (principal_id is not None or principal_type is not None):
        raise ValueError("provide principal or principal_id/principal_type, not both")
    if principal is None and (principal_id is not None or principal_type is not None):
        if principal_id is None or principal_type is None:
            raise ValueError("principal_id and principal_type must be provided together")
        principal = Principal(
            principal_id=principal_id,
            principal_type=principal_type,
            provenance=provenance,
            channel=channel,
            freshness=freshness,
        )
    _check_context_value("channel", channel)
    _check_context_value("provenance", provenance)
    _check_context_value("freshness", freshness, optional=True)
    bounded_metadata: dict[str, str] = {}
    for key, value in dict(metadata or {}).items():
        if key not in TRUSTED_ADAPTER_KEYS:
            raise ValueError(f"unsupported trusted context metadata: {key}")
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError(f"trusted context metadata must be bounded strings: {key}")
        bounded_metadata[key] = value
    return TrustedContext(
        principal=principal,
        channel=channel,
        provenance=provenance,
        freshness=freshness,
        metadata=bounded_metadata,
        _binding_token=_TRUSTED_CONTEXT_TOKEN,
    )


def resolve_principal_binding(
    principal: object = None,
    trusted_context: TrustedContext | None = None,
) -> PrincipalResolution:
    """Promote only a runtime-bound context to a trusted principal.

    ``principal=...`` is retained as a compatibility transport argument, but
    strings and plain ``Principal`` values are explicitly ignored as identity
    assertions.  Passing a ``TrustedContext`` in that slot is accepted only
    when its private binding token is valid.
    """
    if trusted_context is None and isinstance(principal, TrustedContext):
        trusted_context = principal
        principal = None
    if isinstance(trusted_context, TrustedContext) and trusted_context.is_bound:
        if trusted_context.principal is not None:
            return PrincipalResolution(trusted_context.principal, "trusted", trusted_context)
        return PrincipalResolution(
            Principal(
                principal_id="unbound",
                principal_type="unbound",
                provenance=trusted_context.provenance,
                channel=trusted_context.channel,
                freshness=trusted_context.freshness,
            ),
            "trusted_context_without_principal",
            trusted_context,
        )
    if principal is not None:
        return PrincipalResolution(UNBOUND_PRINCIPAL, "untrusted_input_ignored")
    return PrincipalResolution(UNBOUND_PRINCIPAL, "unbound")


PROJECT_BINDING_REQUIRES_SEMANTIC_DECISION_BASIS = True
CORE_SELECTS_PROJECT_UNDER_AMBIGUITY = False
PROJECT_STEWARD_SELECTS_SEMANTIC_PROJECT = False
RELATIONSHIP_RESOLVE_RESULT_IS_PROJECT_BINDING = False
PROJECT_STEWARD_AUTO_ACCEPTS_MILESTONE = False
PROJECT_STEWARD_AUTO_CLOSES_PLAN = False
PROJECT_STEWARD_INVENTS_PROJECT_BINDING = False


def _check_bounded_text(name: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 4096 or (not allow_empty and not value):
        raise ValueError(f"{name} must be a bounded string")
    return value


@dataclass(frozen=True)
class ProjectBinding:
    """An exact project fact carrying an external semantic decision basis.

    Relationship resolution supplies candidate evidence only.  A caller must
    provide this value after semantic choice; Core and Project Steward never
    construct it from a candidate.
    """

    workspace_id: str
    workspace_root: str
    project_id: str
    project_root: str
    manifest_path: str
    registry_fingerprint: str
    candidate_fingerprint: str
    semantic_decision_ref: str

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "workspace_root",
            "project_id",
            "project_root",
            "manifest_path",
            "semantic_decision_ref",
        ):
            _check_bounded_text(name, getattr(self, name))
        for name in ("registry_fingerprint", "candidate_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a SHA-256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "manifest_path": self.manifest_path,
            "registry_fingerprint": self.registry_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "semantic_decision_ref": self.semantic_decision_ref,
        }


def prepare_project_binding(evidence: object, binding: ProjectBinding) -> ProjectBinding:
    """Verify an already-decided binding against one unique evidence record."""
    if not isinstance(binding, ProjectBinding):
        raise ValueError("PROJECT_BINDING_REQUIRED")
    if evidence is None:
        raise ValueError("PROJECT_BINDING_REQUIRED")
    status = getattr(evidence, "status", None)
    if status == "PROJECT_NOT_FOUND":
        raise ValueError("PROJECT_NOT_FOUND")
    if status == "NEEDS_SEMANTIC_CHOICE":
        raise ValueError("NEEDS_SEMANTIC_CHOICE")
    candidates = tuple(getattr(evidence, "candidates", ()))
    if status != "RESOLVED" or len(candidates) != 1:
        raise ValueError("PROJECT_BINDING_REQUIRED")
    candidate = candidates[0]
    expected = {
        "workspace_id": getattr(candidate, "workspace_id", None),
        "workspace_root": getattr(candidate, "workspace_root", None),
        "project_id": getattr(candidate, "project_id", None),
        "project_root": getattr(candidate, "project_root", None),
        "manifest_path": getattr(candidate, "manifest_path", None),
        "registry_fingerprint": getattr(candidate, "registry_fingerprint", None),
        "candidate_fingerprint": getattr(candidate, "candidate_fingerprint", None),
    }
    actual = {
        key: getattr(binding, key)
        for key in expected
    }
    if actual != expected:
        raise ValueError("PROJECT_BINDING_REQUIRED")
    return binding


@dataclass(frozen=True)
class OperationContext:
    """Executor-neutral canonical operation context."""

    operation: str
    correlation_id: str
    principal: Principal
    protocol_version: str
    contract_hash: str
    principal_trust: str = "unbound"
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
    project_binding: ProjectBinding | None = None

    def to_dict(self) -> dict[str, Any]:
        """Bounded metadata view: never echoes params/trusted values."""
        return {
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "principal": self.principal.to_audit(),
            "principal_trust": self.principal_trust,
            "protocol_version": self.protocol_version,
            "contract_hash": self.contract_hash,
            "workspace": self.workspace,
            "project": self.project,
            "project_binding": self.project_binding.to_dict() if self.project_binding else None,
            "session": self.session,
        }


class ContextResolver:
    """Minimal executor-neutral read-only context resolver."""

    def resolve(
        self,
        descriptor: OperationContractDescriptor,
        validated_params: dict[str, Any],
        principal: object,
        correlation_id: str,
        trusted_context: TrustedContext | None = None,
    ) -> OperationContext:
        for key in descriptor.required_context:
            if key not in SATISFIABLE_CONTEXT_KEYS:
                raise ContextNotSupportedError(
                    f"operation context not supported in M2: {key}"
                )
        project_binding = validated_params.get("project_binding")
        if "project_binding" in descriptor.required_context and not isinstance(project_binding, ProjectBinding):
            raise ValueError("PROJECT_BINDING_REQUIRED")
        binding = resolve_principal_binding(principal, trusted_context)
        trusted = {
            key: validated_params[key]
            for key in TRUSTED_ADAPTER_KEYS
            if key in validated_params
        }
        if binding.trusted_context is not None:
            trusted.update(binding.trusted_context.metadata)
        semantic_inputs = {
            key: value
            for key, value in validated_params.items()
            if key not in TRUSTED_ADAPTER_KEYS and key != "project_binding"
        }
        params = dict(validated_params)
        params.update(trusted)
        return OperationContext(
            operation=descriptor.name,
            correlation_id=correlation_id,
            principal=binding.principal,
            principal_trust=binding.trust,
            protocol_version=descriptor.protocol_version,
            contract_hash=descriptor.contract_hash(),
            params=params,
            semantic_inputs=semantic_inputs,
            trusted=trusted,
            project_binding=project_binding if isinstance(project_binding, ProjectBinding) else None,
        )
