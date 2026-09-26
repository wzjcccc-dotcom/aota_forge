"""Trusted operator-side RV1/RV2 lifecycle over the generic thin task seam.

This composition owns the small amount of review-only orchestration that a
thin task-main host intentionally does not own.  It reuses the existing
coordinator adoption marker, handoff store, canonical ``task.start`` facade,
execution store, CARD projection, and review reconciliation.  It creates no
new operation, receipt, store, or workflow engine.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from aota_forge.composition.completion_evidence import derive_governed_review_evidence
from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import (
    GovernedReviewEvidence,
    ReconciliationError,
    ReconciliationOutcome,
    reconcile_review_completion,
)
from aota_forge.runtime.task_main.review_adoption import (
    AcceptedFrontierAuthorization,
    ReviewOnlyAdoption,
    TrustedPlanAuthority,
    adopt_accepted_frontier_for_review,
    build_review_dispatch_binding,
    record_review_dispatch_binding,
    review_only_adoption_from_state,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_store import (
    find_handoffs_by_provenance,
    handoff_open,
    handoff_write,
)
from aota_forge.work_plane.milestone_review import ReviewCycle
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.steward_finalizer import GitPort
from aota_forge.work_plane.task_facade import load_trusted_work_item_task_handoff
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


THIN_REVIEW_LIFECYCLE_OWNER = "aota_forge/composition/thin_review_lifecycle.py"
THIN_REVIEW_LIFECYCLE_KIND = "trusted_operator_side_composition"
THIN_REVIEW_LIFECYCLE_USES_CANONICAL_TASK_START = True
THIN_REVIEW_LIFECYCLE_USES_LEGACY_WORKFLOW_BRAIN = False
THIN_REVIEW_LIFECYCLE_CREATES_NEW_OPERATION = False
THIN_REVIEW_LIFECYCLE_CREATES_NEW_STORE = False


class ThinReviewLifecycleError(RuntimeError):
    """Fail-closed error from the trusted operator-side RV1 composition."""


def _ref_values(values: Any) -> list[str]:
    result: list[str] = []
    for value in tuple(values or ()):
        if isinstance(value, SemanticReference):
            result.append(value.ref)
    return result


def _materialization_id(
    *,
    state: Any,
    adoption: ReviewOnlyAdoption,
    handoff: TaskHandoff,
    parent_task_identity: str,
    plan_id: str | None,
) -> str:
    payload = {
        "frontier_ref": adoption.frontier_ref,
        "handoff_digest": handoff.compute_handoff_digest(),
        "milestone_id": state.milestone_id,
        "parent_task_identity": parent_task_identity,
        "plan_digest": state.plan_digest,
        "plan_id": plan_id or "",
        "plan_source_revision": state.plan_source_revision or "",
        "project_id": state.project_id,
        "reviewer_task_id": adoption.reviewer_canonical_task_id,
    }
    return hashlib.sha256(
        canonical_json(canonicalize(payload, path="ThinReviewMaterialization")).encode("utf-8")
    ).hexdigest()


def _semantic_from_handoff(
    handoff: TaskHandoff,
    *,
    project_id: str,
    milestone_id: str,
    review_cycle: ReviewCycle,
) -> dict[str, Any]:
    if handoff.work_role != AgentWorkRole.REVIEWER:
        raise ThinReviewLifecycleError("reviewer handoff must carry the reviewer role")
    if handoff.project_ref is not None and handoff.project_ref.ref != project_id:
        raise ThinReviewLifecycleError("reviewer handoff project identity contradicts coordinator")
    if handoff.milestone_ref is not None and handoff.milestone_ref.ref != milestone_id:
        raise ThinReviewLifecycleError("reviewer handoff Milestone contradicts coordinator")
    return {
        "work_role": handoff.work_role.value,
        "task_kind": handoff.task_kind,
        "objective": handoff.objective,
        "bounded_scope": handoff.bounded_scope,
        "validation_expectations": list(handoff.validation_expectations),
        "semantic_stop_expectations": list(handoff.semantic_stop_expectations),
        "context_refs": _ref_values(handoff.context_refs),
        "evidence_refs": _ref_values(handoff.evidence_refs),
        "skill_refs": _ref_values(handoff.skill_refs),
        "milestone_ref": milestone_id,
        "work_item_ref": review_cycle.value,
        "project_ref": project_id,
    }


def _open_materialized_handoff(
    *,
    ref: str,
    digest: str,
    sandbox: WorktreeSandboxBoundary,
    reviewer_task_id: str,
    state: Any,
    frontier_ref: str,
) -> TaskHandoff:
    try:
        opened = handoff_open(ref, "full", sandbox=sandbox)
        envelope = opened.get("envelope")
        provenance = envelope.get("provenance") if isinstance(envelope, Mapping) else None
        if (
            opened.get("mode") != "work_item"
            or opened.get("ref") != ref
            or opened.get("digest") != digest
            or not isinstance(envelope, Mapping)
            or envelope.get("task_id") != reviewer_task_id
            or envelope.get("target_role") != AgentWorkRole.REVIEWER.value
            or envelope.get("plan_ref") != state.plan_authority
            or envelope.get("milestone_id") != state.milestone_id
            or not isinstance(provenance, Mapping)
            or provenance.get("frontier_ref") != frontier_ref
        ):
            raise ValueError("durable reviewer handoff identity/provenance mismatch")
        return load_trusted_work_item_task_handoff(
            opened=opened,
            sandbox=sandbox,
            require_explicit_work_role=True,
            require_explicit_work_identity=True,
        )
    except Exception as exc:
        raise ThinReviewLifecycleError(f"durable reviewer handoff reopen failed: {exc}") from exc


def _materialize_reviewer_handoff(
    *,
    base_handoff: TaskHandoff,
    sandbox: WorktreeSandboxBoundary,
    state: Any,
    adoption: ReviewOnlyAdoption,
    parent_task_identity: str,
    plan_id: str | None,
) -> dict[str, Any]:
    """Write/reopen one canonical durable reviewer work-item handoff."""
    materialization_id = _materialization_id(
        state=state,
        adoption=adoption,
        handoff=base_handoff,
        parent_task_identity=parent_task_identity,
        plan_id=plan_id,
    )
    reviewer_task_id = adoption.reviewer_canonical_task_id
    existing_marker = state.integrated_review.get("reviewer_binding") if isinstance(
        state.integrated_review, Mapping
    ) else None
    candidates: list[tuple[str, str]] = []
    if isinstance(existing_marker, Mapping):
        existing_ref = existing_marker.get("handoff_ref")
        existing_digest = existing_marker.get("handoff_digest")
        if isinstance(existing_ref, str) and isinstance(existing_digest, str):
            candidates.append((existing_ref, existing_digest.strip().lower()))
    if not candidates:
        matches = find_handoffs_by_provenance(
            mode="work_item",
            provenance_key="materialization_id",
            provenance_value=materialization_id,
            sandbox=sandbox,
            max_matches=2,
        )
        if len(matches) > 1:
            raise ThinReviewLifecycleError(
                "multiple durable reviewer handoffs match the same materialization identity"
            )
        if matches:
            candidates.append((matches[0].ref, matches[0].digest))

    for ref, digest in candidates:
        handoff = _open_materialized_handoff(
            ref=ref,
            digest=digest,
            sandbox=sandbox,
            reviewer_task_id=reviewer_task_id,
            state=state,
            frontier_ref=adoption.frontier_ref,
        )
        return {
            "handoff": handoff,
            "handoff_ref": ref,
            "handoff_digest": digest,
            "canonical_task_id": reviewer_task_id,
            "frontier_ref": adoption.frontier_ref,
            "handoff_sandbox": sandbox,
        }

    semantic = _semantic_from_handoff(
        base_handoff,
        project_id=state.project_id,
        milestone_id=state.milestone_id,
        review_cycle=adoption.review_cycle,
    )
    ref = handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=sandbox,
        plan_ref=state.plan_authority,
        milestone_id=state.milestone_id,
        work_item_id=adoption.review_cycle.value,
        target_role=AgentWorkRole.REVIEWER.value,
        task_id=reviewer_task_id,
        provenance={
            "materialization_id": materialization_id,
            "plan_digest": state.plan_digest,
            "plan_source_revision": state.plan_source_revision or "",
            "frontier_ref": adoption.frontier_ref,
            "review_dispatch_mode": "runtime_resolved",
        },
    )
    handoff = _open_materialized_handoff(
        ref=ref.ref,
        digest=ref.digest,
        sandbox=sandbox,
        reviewer_task_id=reviewer_task_id,
        state=state,
        frontier_ref=adoption.frontier_ref,
    )
    return {
        "handoff": handoff,
        "handoff_ref": ref.ref,
        "handoff_digest": ref.digest,
        "canonical_task_id": reviewer_task_id,
        "frontier_ref": adoption.frontier_ref,
        "handoff_sandbox": sandbox,
    }


@dataclass(frozen=True)
class ThinReviewLifecycle:
    """Operator-side bounded RV1/RV2 lifecycle using a thin task-main host."""

    host: Any
    coordinator_store: TaskMainCoordinatorStore
    coordinator_id: str
    live_plan_view: MilestonePlanView
    authorization: AcceptedFrontierAuthorization
    trusted_plan_authority: TrustedPlanAuthority
    reviewer_handoff_resolver: Callable[[], TaskHandoff]
    parent_task_identity: str = ""
    plan_id: str | None = None
    review_adoption_git: GitPort | None = None
    review_cycle: ReviewCycle = ReviewCycle.RV1

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator_store, TaskMainCoordinatorStore):
            raise TypeError("coordinator_store must be TaskMainCoordinatorStore")
        if not isinstance(self.live_plan_view, MilestonePlanView):
            raise TypeError("live_plan_view must be MilestonePlanView")
        if not isinstance(self.authorization, AcceptedFrontierAuthorization):
            raise TypeError("authorization must be AcceptedFrontierAuthorization")
        if not isinstance(self.trusted_plan_authority, TrustedPlanAuthority):
            raise TypeError("trusted_plan_authority must be TrustedPlanAuthority")
        try:
            object.__setattr__(self, "review_cycle", ReviewCycle(self.review_cycle))
        except (TypeError, ValueError) as exc:
            raise TypeError("review_cycle must be ReviewCycle.RV1 or ReviewCycle.RV2") from exc
        if self.authorization.review_cycle != self.review_cycle:
            raise ValueError("review_cycle does not match the trusted authorization")
        if not isinstance(self.coordinator_id, str) or not self.coordinator_id.strip():
            raise ValueError("coordinator_id must be non-empty")
        if not callable(getattr(self.host, "invoke", None)):
            raise TypeError("host must expose the canonical invoke callable")
        sandbox = getattr(self.host, "sandbox", None)
        if not isinstance(sandbox, WorktreeSandboxBoundary):
            raise TypeError("host must carry the trusted WorktreeSandboxBoundary")
        if not callable(self.reviewer_handoff_resolver):
            raise TypeError("reviewer_handoff_resolver must be callable")

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self.host.sandbox

    def _state_and_adoption(self) -> tuple[Any, ReviewOnlyAdoption]:
        state = self.coordinator_store.get(self.coordinator_id)
        if state is None:
            raise ThinReviewLifecycleError(
                f"coordinator {self.coordinator_id!r} does not exist"
            )
        adoption = review_only_adoption_from_state(
            state,
            trusted_authority=self.trusted_plan_authority,
        )
        if adoption is None:
            raise ThinReviewLifecycleError("review-only adoption is not durable")
        return state, adoption

    def adopt_frontier(self) -> Any:
        """CAS-adopt the exact trusted frontier before reviewer materialization."""
        return adopt_accepted_frontier_for_review(
            store=self.coordinator_store,
            execution_store=self.host.execution_store,
            coordinator_id=self.coordinator_id,
            live_plan_view=self.live_plan_view,
            authorization=self.authorization,
            worktree_root=self.sandbox,
            git=self.review_adoption_git,
            trusted_authority=self.trusted_plan_authority,
        )

    def dispatch_reviewer(self) -> dict[str, Any]:
        """Adopt, durably bind, then dispatch through canonical thin task.start."""
        self.adopt_frontier()
        state, adoption = self._state_and_adoption()
        base_handoff = self.reviewer_handoff_resolver()
        if not isinstance(base_handoff, TaskHandoff):
            raise ThinReviewLifecycleError("reviewer_handoff_resolver must return TaskHandoff")
        record = _materialize_reviewer_handoff(
            base_handoff=base_handoff,
            sandbox=self.sandbox,
            state=state,
            adoption=adoption,
            parent_task_identity=self.parent_task_identity,
            plan_id=self.plan_id,
        )
        binding = build_review_dispatch_binding(
            state=state,
            adoption=adoption,
            handoff=record["handoff"],
            handoff_ref=record["handoff_ref"],
            handoff_digest=record["handoff_digest"],
        )
        record_review_dispatch_binding(
            store=self.coordinator_store,
            coordinator_id=self.coordinator_id,
            binding=binding,
            trusted_authority=self.trusted_plan_authority,
        )
        existing_execution = self.host.execution_store.get(adoption.reviewer_canonical_task_id)
        if existing_execution is not None:
            return {
                **record,
                "task_start": {
                    "task_id": adoption.reviewer_canonical_task_id,
                    "status": existing_execution.canonical_task_state.value,
                    "reused": True,
                },
                "coordinator_id": self.coordinator_id,
            }
        trusted_binding = getattr(self.host, "trusted_binding", None)
        if trusted_binding is not None and getattr(trusted_binding, "trusted_task_main_context", None) is not None:
            raise ThinReviewLifecycleError(
                "reviewer dispatch requires the generic thin task-main binding"
            )
        response = self.host.invoke(
            "task.start",
            {"role": AgentWorkRole.REVIEWER.value, "handoff_ref": record["handoff_ref"]},
        )
        if not isinstance(response, Mapping) or response.get("ok") is not True:
            error = response.get("error") if isinstance(response, Mapping) else None
            raise ThinReviewLifecycleError(
                f"canonical thin reviewer task.start failed: {error!r}"
            )
        payload = response.get("payload")
        if not isinstance(payload, Mapping) or payload.get("task_id") != adoption.reviewer_canonical_task_id:
            raise ThinReviewLifecycleError(
                "canonical thin task.start returned a different reviewer task identity"
            )
        return {
            **record,
            "task_start": dict(payload),
            "coordinator_id": self.coordinator_id,
        }

    def reconcile_reviewer(self, card_digest: str) -> ReconciliationOutcome:
        """Derive exact-frontier review evidence and reuse existing reconciliation."""
        state, adoption = self._state_and_adoption()
        if not isinstance(card_digest, str) or not card_digest.strip():
            raise ReconciliationError("card_digest must be non-empty")
        binding = state.integrated_review.get("reviewer_binding") if isinstance(
            state.integrated_review, Mapping
        ) else None
        if not isinstance(binding, Mapping):
            raise ReconciliationError("review-only adoption has no durable reviewer binding")
        handoff_ref = binding.get("handoff_ref")
        handoff_digest = binding.get("handoff_digest")
        if not isinstance(handoff_ref, str) or not isinstance(handoff_digest, str):
            raise ReconciliationError("durable reviewer binding has no handoff identity")

        def handoff_resolver() -> TaskHandoff:
            return _open_materialized_handoff(
                ref=handoff_ref,
                digest=handoff_digest,
                sandbox=self.sandbox,
                reviewer_task_id=adoption.reviewer_canonical_task_id,
                state=state,
                frontier_ref=adoption.frontier_ref,
            )

        governed = derive_governed_review_evidence(
            reviewer_canonical_task_id=adoption.reviewer_canonical_task_id,
            card_digest=card_digest,
            execution_store=self.host.execution_store,
            live_plan_view=self.live_plan_view,
            reviewed_frontier_ref=adoption.frontier_ref,
            reviewer_handoff_resolver=handoff_resolver,
            review_cycle=self.review_cycle,
        )
        governed = replace(
            governed,
            review_handoff_ref=handoff_ref,
            review_handoff_digest=handoff_digest,
        )
        return reconcile_review_completion(
            store=self.coordinator_store,
            execution_store=self.host.execution_store,
            coordinator_id=self.coordinator_id,
            reviewer_canonical_task_id=adoption.reviewer_canonical_task_id,
            card_digest=card_digest,
            live_plan_view=self.live_plan_view,
            governed_review=governed,
            review_adoption_worktree_root=self.sandbox,
            review_adoption_git=self.review_adoption_git,
            trusted_plan_authority=self.trusted_plan_authority,
        )


def create_thin_review_lifecycle(**kwargs: Any) -> ThinReviewLifecycle:
    """Trusted composition factory; no model-facing operation is added."""
    return ThinReviewLifecycle(**kwargs)


__all__ = [
    "THIN_REVIEW_LIFECYCLE_OWNER",
    "THIN_REVIEW_LIFECYCLE_KIND",
    "THIN_REVIEW_LIFECYCLE_USES_CANONICAL_TASK_START",
    "THIN_REVIEW_LIFECYCLE_USES_LEGACY_WORKFLOW_BRAIN",
    "THIN_REVIEW_LIFECYCLE_CREATES_NEW_OPERATION",
    "THIN_REVIEW_LIFECYCLE_CREATES_NEW_STORE",
    "ThinReviewLifecycle",
    "ThinReviewLifecycleError",
    "create_thin_review_lifecycle",
]
