"""Handoff → ExecutionPackage compiler / materialization proof (S1 M1-W4).

Establishes deterministic semantic compilation from TaskHandoff to
existing ExecutionPackage without modifying frozen Core contracts.

M1/W1 bounded Worker scope contract (AF repair #45, defect I40-B003/F1):

* OBJECTIVE_ONLY_WORKER_INSTRUCTION=no. The Hermes dispatch envelope
  carries the package instruction verbatim as the Worker's model-facing
  initial prompt (``-z <instruction>``); working_context and
  result_expectations travel as validated metadata the host client does not
  forward to the model. A Worker that never reaches role.bootstrap (the
  observed stall: 0 tool calls, stop clause obeyed) therefore sees only the
  instruction. The instruction is a compact deterministic rendering of ALL
  validated TaskHandoff execution fields (objective + bounded_scope +
  validation/stop expectations + stable ref identities + handoff digest),
  so the model itself can see usable bounded scope without a GitHub Plan
  fetch and without a full Plan dump.
* Fingerprint coverage: ANY execution-relevant Handoff semantic change
  changes existing intent_fingerprint twice: via the handoff_digest bound
  into fingerprint-covered input_artifacts, and via the instruction itself
  (intent_fingerprint covers instruction verbatim).

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

# M1/W1 marker: the model-facing instruction is never the objective alone.
OBJECTIVE_ONLY_WORKER_INSTRUCTION = False


class PackageIntegrityError(ValueError):
    """Execution-visible package diverges from its validated TaskHandoff."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"PACKAGE_HANDOFF_MISMATCH: {detail}")
        self.code = "PACKAGE_HANDOFF_MISMATCH"


def build_worker_instruction(handoff: TaskHandoff) -> str:
    """Render the compact model-facing Worker instruction (deterministic).

    The Hermes dispatch envelope forwards the package instruction verbatim
    as the Worker's initial prompt (``-z``); structured working_context and
    result_expectations do not reach the model on that path. This rendering
    therefore carries every execution-relevant handoff field in compact
    labeled sections — short goal first, then bounded scope, validation and
    stop expectations, stable ref identities, and the handoff digest — so a
    Worker can act without fetching the GitHub Plan and without receiving a
    full Plan dump. No truncation: all handoff fields are already bounded by
    the TaskHandoff contract, and every one of them is required semantics.

    Deterministic: identical TaskHandoff -> identical instruction, so the
    existing intent_fingerprint (which covers instruction verbatim) binds
    scope a second time alongside the handoff_digest input artifact.
    """
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be a TaskHandoff, got {type(handoff).__name__}")
    lines: list[str] = [handoff.objective, "", "Bounded scope:", handoff.bounded_scope, ""]
    lines.append("Validation expectations:")
    for item in handoff.validation_expectations:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Stop or escalate when:")
    for item in handoff.semantic_stop_expectations:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("References:")
    refs: list[tuple[str, str]] = []
    for attr in (
        "project_ref",
        "plan_ref",
        "milestone_ref",
        "work_item_ref",
        "process_depth_or_risk_projection_ref",
    ):
        val = getattr(handoff, attr)
        if val is not None:
            digest = f"#{val.digest}" if val.digest else ""
            refs.append((attr, f"{val.ref}{digest}"))
    for attr in ("policy_refs", "context_refs", "evidence_refs", "skill_refs"):
        vals = getattr(handoff, attr) or ()
        if vals:
            joined = ", ".join(
                (f"{v.ref}#{v.digest}" if v.digest else v.ref) for v in vals
            )
            refs.append((attr, joined))
    for key, rendered in refs:
        lines.append(f"{key}={rendered}")
    lines.append(f"handoff_digest={handoff.handoff_digest}")
    return "\n".join(lines).strip() + "\n"


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

    # Semantic projections — deterministic. The model-facing instruction is
    # the compact rendering of ALL validated handoff execution fields, never
    # the objective alone (M1/W1: OBJECTIVE_ONLY_WORKER_INSTRUCTION=no).
    instruction = build_worker_instruction(handoff)
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


def verify_execution_package_integrity(package: ExecutionPackage, handoff: TaskHandoff) -> None:
    """Fail closed when the execution-visible package diverges from its handoff.

    Checks the exact binding a scope-tampering adversary would attack:

    * fingerprint-covered input artifact carries this handoff's digest;
    * visible working_context.bounded_scope equals the handoff scope;
    * visible result_expectations equal the handoff expectations;
    * model-facing instruction equals the deterministic rendering of this
      handoff (so Worker-visible instructions cannot change independently
      of the digest-covered handoff).

    A digest that corresponds to scope A with a Worker-visible scope B (or
    any other field substitution) raises PackageIntegrityError.
    """
    if not isinstance(package, ExecutionPackage):
        raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be a TaskHandoff, got {type(handoff).__name__}")
    artifact_digest: str | None = None
    for artifact in package.input_artifacts:
        if isinstance(artifact, Mapping) and artifact.get("handoff_kind") == "task_handoff":
            artifact_digest = artifact.get("handoff_digest")
            break
    if artifact_digest != handoff.handoff_digest:
        raise PackageIntegrityError(
            "input artifact handoff_digest does not match the validated handoff digest"
        )
    if not isinstance(package.working_context, Mapping):
        raise PackageIntegrityError("working_context must be a mapping")
    if package.working_context.get("bounded_scope") != handoff.bounded_scope:
        raise PackageIntegrityError("working_context.bounded_scope diverges from handoff bounded_scope")
    if package.working_context.get("handoff_digest") != handoff.handoff_digest:
        raise PackageIntegrityError("working_context.handoff_digest diverges from handoff digest")
    expectations = package.result_expectations
    if not isinstance(expectations, Mapping):
        raise PackageIntegrityError("result_expectations must be a mapping")
    if list(expectations.get("validation_expectations") or []) != list(handoff.validation_expectations):
        raise PackageIntegrityError("result validation_expectations diverge from handoff")
    if list(expectations.get("semantic_stop_expectations") or []) != list(handoff.semantic_stop_expectations):
        raise PackageIntegrityError("result semantic_stop_expectations diverge from handoff")
    if package.instruction != build_worker_instruction(handoff):
        raise PackageIntegrityError("model-facing instruction diverges from the validated handoff rendering")
