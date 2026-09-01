"""Handoff → ExecutionPackage compiler / materialization proof (S1 M1-W4).

Establishes deterministic semantic compilation from TaskHandoff to
existing ExecutionPackage without modifying frozen Core contracts.

Invariants
----------
* RETAIN: ExecutionPackage schema, CanonicalRole, RoleMapping,
  compute_intent_fingerprint, Dispatcher idempotency.
* Handoff does NOT own trusted execution identities:
  canonical_task_id and project_id come from TrustedExecutionBinding.
* WorkRole -> CanonicalRole via accepted W2 resolver (no duplicate table).
* task-main fails closed (no default CanonicalRole).
* Semantic intent preserved: objective, bounded_scope,
  validation_expectations, semantic_stop_expectations, refs, digest.
* Fingerprint coverage: ANY execution-relevant Handoff semantic change
  changes existing intent_fingerprint via handoff_digest bound into
  fingerprint-covered input_artifacts.
* Mechanical field ownership: package_id, correlation_id, idempotency_key
  are Forge-owned (existing ExecutionPackage mechanics).
* No executor selection, no Hermes dependency.
* Deterministic: identical Handoff + identical binding -> identical intent.

Dependency direction
--------------------
work_plane.compiler -> work_plane.roles/mapping/handoff -> core.execution
Core never imports work_plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.execution.package import (
    EXECUTION_CONTRACT_HASH,
    PROTOCOL_VERSION,
    ExecutionPackage,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.mapping import resolve_work_role_to_canonical_role


@dataclass(frozen=True)
class TrustedExecutionBinding:
    """Minimal trusted caller boundary for execution identities.

    Supplies existing execution values that Handoff must not self-authorize.
    Narrow seam, not a new authority registry/store.
    """

    canonical_task_id: str
    project_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or type(self.canonical_task_id) is not str:
            raise TypeError(f"canonical_task_id must be a string, got {type(self.canonical_task_id).__name__}")
        cid = self.canonical_task_id.strip()
        if not cid:
            raise ValueError("canonical_task_id must be a non-empty string")
        if cid != self.canonical_task_id:
            object.__setattr__(self, "canonical_task_id", cid)

        if not isinstance(self.project_id, str) or type(self.project_id) is not str:
            raise TypeError(f"project_id must be a string, got {type(self.project_id).__name__}")
        pid = self.project_id.strip()
        if not pid:
            raise ValueError("project_id must be a non-empty string")
        if pid != self.project_id:
            object.__setattr__(self, "project_id", pid)


def _build_input_artifacts(handoff: TaskHandoff) -> tuple[dict[str, Any], ...]:
    """Fingerprint-covered input projection carrying Handoff semantic identity.

    Small canonical metadata entry within existing fingerprint-covered
    input_artifacts. Encodes handoff digest + task kind + work role without
    embedding full Handoff.
    """
    # Use handoff_digest which covers all execution-relevant Handoff fields.
    artifact: dict[str, Any] = {
        "handoff_digest": handoff.handoff_digest,
        "handoff_kind": "task_handoff",
        "task_kind": handoff.task_kind,
        "work_role": handoff.work_role.value,
    }
    return (artifact,)


def _build_working_context(handoff: TaskHandoff) -> dict[str, Any]:
    """Execution-visible projection for bounded_scope and traceable refs.

    Not fingerprint-covered; visibility only. Fingerprint coverage is via
    input_artifacts digest.
    """
    # Preserve objective is via instruction; bounded_scope via working_context.
    ctx: dict[str, Any] = {
        "bounded_scope": handoff.bounded_scope,
        "handoff_digest": handoff.handoff_digest,
        "task_kind": handoff.task_kind,
        "work_role": handoff.work_role.value,
    }
    # Traceable refs — preserve without hydrating.
    refs: dict[str, Any] = {}
    if handoff.project_ref is not None:
        refs["project_ref"] = handoff.project_ref.to_dict()
    if handoff.plan_ref is not None:
        refs["plan_ref"] = handoff.plan_ref.to_dict()
    if handoff.milestone_ref is not None:
        refs["milestone_ref"] = handoff.milestone_ref.to_dict()
    if handoff.work_item_ref is not None:
        refs["work_item_ref"] = handoff.work_item_ref.to_dict()
    if handoff.policy_refs:
        refs["policy_refs"] = [r.to_dict() for r in handoff.policy_refs]
    if handoff.context_refs:
        refs["context_refs"] = [r.to_dict() for r in handoff.context_refs]
    if handoff.evidence_refs:
        refs["evidence_refs"] = [r.to_dict() for r in handoff.evidence_refs]
    if handoff.skill_refs:
        refs["skill_refs"] = [r.to_dict() for r in handoff.skill_refs]
    if handoff.process_depth_or_risk_projection_ref is not None:
        refs["process_depth_or_risk_projection_ref"] = handoff.process_depth_or_risk_projection_ref.to_dict()

    if refs:
        ctx["refs"] = refs
    return ctx


def _build_result_expectations(handoff: TaskHandoff) -> dict[str, Any]:
    """Projection of validation and semantic stop expectations."""
    return {
        "validation_expectations": list(handoff.validation_expectations),
        "semantic_stop_expectations": list(handoff.semantic_stop_expectations),
    }


def compile_handoff_to_execution_package(
    handoff: TaskHandoff,
    binding: TrustedExecutionBinding,
    *,
    package_id: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    operation: str = "task_dispatch",
) -> ExecutionPackage:
    """Deterministically compile TaskHandoff + trusted binding to ExecutionPackage.

    Parameters
    ----------
    handoff: TaskHandoff
        Semantic handoff (task-main -> Forge). Must be a validated TaskHandoff.
    binding: TrustedExecutionBinding
        Trusted execution identities. Handoff does NOT self-authorize these.
    package_id, idempotency_key, correlation_id:
        Optional trusted mechanical lifecycle seam. If None, Forge-owned
        generation via ExecutionPackage mechanics. Never derived from Handoff.

    Returns
    -------
    ExecutionPackage
        Existing Core type with deterministic semantic intent
        and Forge-owned mechanical identities.
    """
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be a TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(binding, TrustedExecutionBinding):
        raise TypeError(f"binding must be a TrustedExecutionBinding, got {type(binding).__name__}")

    # WorkRole -> CanonicalRole via accepted W2 resolver (no duplicate table).
    # This fails closed for task-main and invalid roles.
    canonical_role = resolve_work_role_to_canonical_role(handoff.work_role)

    # Semantic projections — deterministic.
    instruction = handoff.objective
    input_artifacts = _build_input_artifacts(handoff)
    working_context = _build_working_context(handoff)
    result_expectations = _build_result_expectations(handoff)
    # Bounded defaults — no speculative capability inference.
    capability_requirements: dict[str, Any] = {}
    constraints: dict[str, Any] = {}

    # Materialize via existing ExecutionPackage mechanics (Forge-owned).
    # protocol_version and contract_hash remain existing values (not from Handoff).
    pkg = ExecutionPackage.create(
        canonical_task_id=binding.canonical_task_id,
        project_id=binding.project_id,
        canonical_role=canonical_role.value,
        instruction=instruction,
        operation=operation,
        package_id=package_id,
        protocol_version=PROTOCOL_VERSION,
        contract_hash=EXECUTION_CONTRACT_HASH,
        input_artifacts=input_artifacts,
        working_context=working_context,
        capability_requirements=capability_requirements,
        constraints=constraints,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        result_expectations=result_expectations,
    )
    return pkg


# Alias for spec-preferred naming.
compile_task_handoff = compile_handoff_to_execution_package
