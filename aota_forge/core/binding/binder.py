"""M3-B9 deterministic subject binding / recovery resolver (Issue #9, lane M3-B9).

Implements the B4 ``SemanticRefResolver`` protocol concretely over the B3 graph
repository candidate-query primitives and the frozen M3-A5 binding model:

* deterministic candidate enumeration from the canonical graph
  (``CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH=yes``)
* validity filtering OUTSIDE any semantic choice (``CANDIDATE_COUNT_AFTER_
  VALIDITY_FILTER=yes``)
* 0 / 1 / many classification:
    - 0 valid candidates  -> bounded NOT_FOUND / recovery classification
    - 1 valid candidate   -> deterministic BOUND only when all validity + A5
                             precondition conditions pass
    - >1 valid candidates -> ``NEEDS_SEMANTIC_CHOICE`` with deterministic
                             bounded candidate facts and NO recommendation
* bounded read-only recovery classification (projection stale, missing
  committed Decision basis, missing prerequisite)

Hard boundaries:

* NO heuristic Subject selection (``HEURISTIC_SUBJECT_SELECTION_ALLOWED=no``):
  no recency, no current pointer, no title/LLM similarity, no hidden many->one
  reduction (``NO_HIDDEN_REDUCTION_MANY_TO_ONE=yes``)
* binding NEVER invents or mints a Subject (``BINDING_FAILURE_AUTO_MINTS_SUBJECT=no``,
  ``ZERO_CANDIDATE_AUTO_CREATES_SUBJECT=no``)
* binding implies NO mutation authority (``SUBJECT_BINDING_IMPLIES_MUTATION_
  AUTHORITY=no``, ``B9_PERFORMS_AUTHORITY_DECISION=no``); result.authority is
  always ``False``
* a trusted internal ``ObjectRef`` fast-path is handled separately
  (``TRUSTED_OBJECT_REF_IMPLIES_MUTATION_AUTHORITY=no``)
* read-only with respect to the lifecycle graph (``BINDING_GRAPH_WRITE_COUNT=0``);
  this module never calls ``repo.store``, never opens a transaction and never
  consumes a capability lease
* B9 depends on B7 source only as an accepted ObjectRef carrier; it does not
  mutate the transition layer (``B9_NEEDS_B7_SOURCE_MUTATION=no``) and does not
  depend on the B8 projection implementation (``B9_DEPENDS_ON_B8=no``)
"""

from __future__ import annotations

from aota_forge.core.graph.repository import (
    GraphRepository,
    GraphNotFoundError,
    assert_object_ref_kind,
)
from aota_forge.core.identity.boundary import classify_ref, RefCategory
from aota_forge.core.identity.errors import IdentityError, SemanticRefRejectedError
from aota_forge.core.identity.ids import InternalId
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import (
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.revision import revision_number_of

from aota_forge.core.binding.request import BindingRequest
from aota_forge.core.binding.result import (
    BindingCandidate,
    BindingResult,
    BindingStatus,
    candidates_only,
    plain,
    success_bound,
)


# ---------------------------------------------------------------------------
# Frozen required end state for B9 (module-level flags the validator asserts).
# ---------------------------------------------------------------------------

HEURISTIC_SUBJECT_SELECTION_ALLOWED = False
ZERO_CANDIDATE_HEURISTIC_SELECTION = False
ZERO_CANDIDATE_HEURISTIC_FALLBACK = False
MULTIPLE_CANDIDATE_HEURISTIC_SELECTION = False
CURRENT_POINTER_SUBJECT_AUTHORITY = False
B9_MAY_SELECT_USING_CURRENT_POINTER = False
SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY = False
B9_PERFORMS_AUTHORITY_DECISION = False
NEEDS_SEMANTIC_CHOICE_IS_AUTHORITY_DECISION = False
OBJECT_REF_IS_AUTHORITY = False
BINDING_FAILURE_AUTO_MINTS_SUBJECT = False
ZERO_CANDIDATE_AUTO_CREATES_SUBJECT = False
BINDING_GRAPH_WRITE_COUNT = 0
BINDING_TRANSACTION_REQUIRED = False
BINDING_CAPABILITY_LEASE_REQUIRED = False
ONE_CANDIDATE_BINDING_DETERMINISTIC = True
UNIQUE_CANDIDATE_BYPASSES_VALIDITY_FILTER = False
NO_HIDDEN_REDUCTION_MANY_TO_ONE = True
MULTIPLE_CANDIDATE_RESULT = "NEEDS_SEMANTIC_CHOICE"
CANDIDATE_COUNT_AFTER_VALIDITY_FILTER = True
CANDIDATE_LIST_ORDER_DETERMINISTIC = True
MODEL_INTERNAL_IDS_NORMAL_INPUT = False
RAW_INTERNAL_ID_SELF_BINDING_ALLOWED = False
TRUSTED_OBJECT_REF_IMPLIES_MUTATION_AUTHORITY = False
B9_DEPENDS_ON_B8 = False
B9_NEEDS_B7_SOURCE_MUTATION = False
B9_CONTEXT_DIRECT_MUTATION_ALLOWED = False
B9_SHARED_ERRORS_WRITE_ALLOWED = False
B9_SHARED_RESULTS_WRITE_ALLOWED = False
B9_CATALOG_WRITE_ALLOWED = False
B9_CORE_INIT_WRITE_ALLOWED = False
PROJECTION_RECONSTRUCTION_IMPLEMENTED_BY_B9 = False
BINDING_RECOVERY_IMPLEMENTED = True
BINDING_SOURCE_IMPLEMENTED = True
BINDING_RUNTIME_PRODUCTION_ACTIVE = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
M3_B_SHADOW_MATERIALIZATION_AUTHORIZED = False
CUTOVER_AUTHORIZED = False


def _derive_deterministic_value(request: BindingRequest) -> str:
    """Stable deterministic identity value for workspace/project/plan kinds.

    Uses B4 canonical derivation only (stable semantic identity + owning
    subject namespace).  Raises ``ValueError`` when the required owning
    context is absent, so the caller can map that to a bounded NOT_FOUND
    (missing prerequisite) rather than inventing an identity.
    """
    sub_kind = request.subject_sub_kind
    if sub_kind == SubjectKind.WORKSPACE:
        workspace_id = request.workspace_id or request.semantic_ref
        if not workspace_id:
            raise ValueError("workspace semantic identity is required")
        return workspace_subject(workspace_id).value
    if sub_kind == SubjectKind.PROJECT:
        project_id = request.project_id or request.semantic_ref
        workspace_id = request.workspace_id
        if not project_id or not workspace_id:
            raise ValueError("project and owning workspace semantic identity are required")
        owning_workspace = workspace_subject(workspace_id)
        return project_subject(project_id, owning_workspace).value
    if sub_kind == SubjectKind.PLAN:
        plan_key = request.plan_id or request.semantic_ref
        project_id = request.project_id
        workspace_id = request.workspace_id
        if not plan_key or not project_id or not workspace_id:
            raise ValueError("plan, owning project and owning workspace semantic identity are required")
        owning_workspace = workspace_subject(workspace_id)
        owning_project = project_subject(project_id, owning_workspace)
        return plan_subject(plan_key, owning_project).value
    raise ValueError(f"subject kind is not deterministic: {sub_kind!r}")


def _candidate_from_subject(subject) -> BindingCandidate:
    sub_kind = subject.subject_id.sub_kind
    ref = make_object_ref(IdKind.SUBJECT, subject.subject_id)
    workflow = subject.workflow_ref.to_canonical() if subject.workflow_ref is not None else None
    return BindingCandidate(
        subject_ref=ref,
        sub_kind=sub_kind,
        id_derivation=subject.id_derivation or "unknown",
        owning_workflow_ref=workflow,
    )


def _candidate_from_edge(child_subject, edge) -> BindingCandidate:
    sub_kind = child_subject.subject_id.sub_kind
    ref = make_object_ref(IdKind.SUBJECT, child_subject.subject_id)
    workflow = child_subject.workflow_ref.to_canonical() if child_subject.workflow_ref is not None else None
    return BindingCandidate(
        subject_ref=ref,
        sub_kind=sub_kind,
        id_derivation=child_subject.id_derivation or "unknown",
        owning_workflow_ref=workflow,
        source_decision_ref=edge.source_decision_ref.to_canonical(),
    )


class SubjectBindingResolver:
    """Deterministic executor-neutral binder over a canonical graph repository.

    The binder is read-only: it consumes candidate-query primitives and never
    writes to the lifecycle graph (``BINDING_GRAPH_WRITE_COUNT=0``), never
    requires a transaction or a capability lease, and never makes an authority
    or semantic decision.
    """

    HEURISTIC_SUBJECT_SELECTION_ALLOWED = False
    SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY = False
    B9_PERFORMS_AUTHORITY_DECISION = False
    BINDING_GRAPH_WRITE_COUNT = 0
    BINDING_RECOVERY_IMPLEMENTED = True

    def __init__(self, repo: GraphRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def semantic_ref_to_object_ref(self, semantic_ref: str) -> ObjectRef:
        """Root B4 protocol entry point.

        Raises ``SemanticRefRejectedError`` for any non-BOUND outcome, so the
        concrete resolver never returns a half-bound subject.  The subject kind
        is inferred as deterministic bindable kinds in order; callers needing a
        specific kind should use ``bind`` directly.
        """
        result = self.bind(BindingRequest(semantic_ref=semantic_ref))
        if result.status is BindingStatus.BOUND and result.subject_ref is not None:
            return result.subject_ref
        raise SemanticRefRejectedError(
            f"semantic reference did not deterministically resolve to one subject: {result.status.value}",
            details={"status": result.status.value},
        )

    def bind(self, request: BindingRequest) -> BindingResult:
        """Run one deterministic binding / recovery operation."""
        if request.uses_trusted_fast_path and request.trusted_object_ref is not None:
            return self._bind_trusted_object_ref(request.trusted_object_ref, request)
        if not request.semantic_ref:
            return plain(BindingStatus.INVALID_REFERENCE, detail=(("reason", "missing semantic_ref"),))
        category = classify_ref(request.semantic_ref)
        if category is RefCategory.RAW_INTERNAL_ID:
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "raw internal id is not normal model input"),),
            )
        if category is RefCategory.LEGACY:
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "legacy / current_* pointer is not an authoritative semantic ref"),),
            )
        if category is not RefCategory.SEMANTIC:
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", f"reference classified as {category.value}"),),
            )
        if not request.subject_sub_kind:
            return plain(
                BindingStatus.NOT_FOUND,
                detail=(("reason", "subject kind required for deterministic classification"),),
            )
        return self._bind_semantic(request)

    # ------------------------------------------------------------------
    # Trusted ObjectRef fast-path
    # ------------------------------------------------------------------

    def _bind_trusted_object_ref(self, ref: ObjectRef, request: BindingRequest) -> BindingResult:
        if not isinstance(ref, ObjectRef):
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "trusted ObjectRef fast-path requires a typed ObjectRef"),),
            )
        try:
            assert_object_ref_kind(ref, IdKind.SUBJECT, "trusted subject ref")
        except Exception:  # noqa: BLE001 - fail closed on wrong kind
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "trusted ObjectRef has wrong kind"),),
            )
        try:
            subject = self._repo.subject(ref)
        except GraphNotFoundError:
            return plain(BindingStatus.NOT_FOUND, detail=(("ref", ref.serialize()),))
        except (IdentityError, TypeError, ValueError):
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "trusted ObjectRef did not resolve mechanically"),),
            )
        if request.subject_sub_kind is not None and subject.subject_id.sub_kind != request.subject_sub_kind:
            return plain(
                BindingStatus.INVALID_REFERENCE,
                detail=(("reason", "trusted ObjectRef kind does not match requested subject kind"),),
            )
        # Classify A5 preconditions supplied by the caller (never decided here).
        precondition = self._classify_preconditions(subject, request, ref)
        if precondition is not None:
            return precondition
        return success_bound(ref, detail=(("path", "trusted_fast_path"),))

    # ------------------------------------------------------------------
    # Semantic binding path
    # ------------------------------------------------------------------

    def _bind_semantic(self, request: BindingRequest) -> BindingResult:
        try:
            request.require_subject_kind()
        except (TypeError, ValueError) as exc:
            return plain(BindingStatus.INVALID_REFERENCE, detail=(("reason", str(exc)),))

        candidates = self._enumerate_candidates(request)
        valid, _invalid = self._split_valid(candidates, request)

        # Candidate count AFTER validity filter (N23).
        if not valid:
            return self._zero_case(request)
        if len(valid) == 1:
            return self._one_case(valid, request)
        # >1 valid candidates: bounded ambiguity, never reduced heuristically.
        ordered = self._order(valid)
        return candidates_only(BindingStatus.NEEDS_SEMANTIC_CHOICE, tuple(ordered))

    def _enumerate_candidates(self, request: BindingRequest) -> list[BindingCandidate]:
        sub_kind = request.subject_sub_kind
        workflow_ref = request.workflow_ref.internal_id if request.workflow_ref is not None else None

        # Followup / minted lineage path derives candidates from the canonical
        # Decision-backed FollowupEdge graph (never from guesswork).
        if request.lineage_only or sub_kind in (SubjectKind.WORK,) or request.parent_ref is not None:
            if request.parent_ref is None:
                return []
            try:
                edges = self._repo.followup_children_of(
                    request.parent_ref,
                    decision_ref=request.source_decision_ref,
                    candidate_sub_kind=sub_kind,
                )
            except Exception:  # noqa: BLE001 - fail closed on malformed lineage
                return []
            candidates: list[BindingCandidate] = []
            for edge in edges:
                child_ref = make_object_ref(IdKind.SUBJECT, edge.child_subject_ref)
                try:
                    child = self._repo.subject(child_ref)
                except GraphNotFoundError:
                    continue  # orphan lineage is not a candidate (fail closed)
                candidates.append(_candidate_from_edge(child, edge))
            return self._order(candidates)

        # Deterministic stable semantic identity path (workspace/project/plan).
        try:
            derived = _derive_deterministic_value(request)
        except (TypeError, ValueError, IdentityError):
            # Owning/namespace context missing: bounded scope enumeration only
            # (deterministic, from the canonical graph; never heuristic).
            return self._order(self._scope_candidates(request, sub_kind, workflow_ref))
        return self._order(self._exact_identity_candidates(request, sub_kind, derived))

    def _exact_identity_candidates(
        self,
        request: BindingRequest,
        sub_kind: str,
        derived: str,
    ) -> list[BindingCandidate]:
        subjects = self._repo.subjects_matching_semantic_ref(
            request.semantic_ref, sub_kind=sub_kind, identity_values=(derived,)
        )
        return [_candidate_from_subject(s) for s in subjects]

    def _scope_candidates(self, request: BindingRequest, sub_kind: str, workflow_ref: InternalId | None) -> list[BindingCandidate]:
        subjects = self._repo.subjects_by_scope(workflow_ref=workflow_ref, sub_kind=sub_kind)
        return [_candidate_from_subject(s) for s in subjects]

    def _split_valid(self, candidates, request: BindingRequest) -> tuple[list[BindingCandidate], list[BindingCandidate]]:
        valid: list[BindingCandidate] = []
        invalid: list[BindingCandidate] = []
        for candidate in candidates:
            if self._is_valid(candidate, request):
                valid.append(candidate)
            else:
                invalid.append(candidate)
        return valid, invalid

    def _is_valid(self, candidate: BindingCandidate, request: BindingRequest) -> bool:
        if request.subject_sub_kind is not None and candidate.sub_kind != request.subject_sub_kind:
            return False
        if request.workflow_ref is not None and candidate.owning_workflow_ref is not None:
            if candidate.owning_workflow_ref != request.workflow_ref.internal_id.to_canonical():
                return False
        if request.need_decision_basis or request.lineage_only or request.parent_ref is not None:
            if candidate.source_decision_ref is None:
                return False
            decision_ref = candidate.source_decision_ref
            try:
                decision_ref_obj = make_object_ref(IdKind.DECISION, InternalId_from_canonical(decision_ref))
                decision = self._repo.decision(decision_ref_obj)
                if request.parent_ref is not None and decision.subject_ref != request.parent_ref.internal_id:
                    return False
            except GraphNotFoundError:
                return False
            except (IdentityError, TypeError, ValueError):
                return False
        return True

    # ------------------------------------------------------------------
    # 0 / 1 classifications
    # ------------------------------------------------------------------

    def _zero_case(self, request: BindingRequest) -> BindingResult:
        # Missing committed Decision Basis prerequisite.
        if request.need_decision_basis or request.parent_ref is not None:
            if not self._decision_basis_present(request):
                return plain(
                    BindingStatus.MATERIALIZED_DECISION_MISSING,
                    detail=(("reason", "decision-backed binding has no committed Decision basis"),),
                )
        # A durable canonical Subject with the exact derived identity may exist
        # even though the surface lookup / scope is stale: recoverable projection.
        stale = self._stale_projection_evidence(request)
        if stale is not None:
            return candidates_only(BindingStatus.PROJECTION_STALE_RECONCILABLE, (stale,))
        return plain(BindingStatus.NOT_FOUND, detail=(("candidates", "zero"),))

    def _one_case(self, candidates, request: BindingRequest) -> BindingResult:
        candidate = candidates[0]
        precondition = self._classify_preconditions_candidate(candidate, request)
        if precondition is not None:
            return precondition
        return success_bound(
            candidate.subject_ref,
            detail=(("path", "semantic"), ("candidates", "one_valid")),
        )

    def _decision_basis_present(self, request: BindingRequest) -> bool:
        if request.source_decision_ref is None:
            return False
        try:
            self._repo.decision(request.source_decision_ref)
            return True
        except (GraphNotFoundError, IdentityError, TypeError, ValueError):
            return False

    def _stale_projection_evidence(self, request: BindingRequest) -> BindingCandidate | None:
        sub_kind = request.subject_sub_kind
        try:
            derived = _derive_deterministic_value(request)
        except (TypeError, ValueError, IdentityError):
            return None
        matches = self._repo.subjects_matching_semantic_ref(
            request.semantic_ref, sub_kind=sub_kind, identity_values=(derived,)
        )
        if matches:
            return _candidate_from_subject(matches[0])
        return None

    # ------------------------------------------------------------------
    # A5 precondition classification (never an authority decision)
    # ------------------------------------------------------------------

    def _classify_preconditions(self, subject, request: BindingRequest, ref: ObjectRef) -> BindingResult | None:
        current = revision_number_of(subject)
        if request.expected_revision is not None and request.expected_revision != current:
            return plain(
                BindingStatus.SUBJECT_BINDING_REVISION_CONFLICT,
                detail=(("expected", str(request.expected_revision)), ("current", str(current))),
            )
        if request.authority_eligible is False:
            return plain(
                BindingStatus.SUBJECT_BINDING_AUTHORITY_DENIED,
                detail=(("reason", "supplied authority-eligibility precondition is negative"),),
            )
        return None

    def _classify_preconditions_candidate(self, candidate: BindingCandidate, request: BindingRequest) -> BindingResult | None:
        try:
            subject = self._repo.subject(candidate.subject_ref)
        except GraphNotFoundError:
            return candidates_only(BindingStatus.NOT_FOUND, (candidate,))
        return self._classify_preconditions(subject, request, candidate.subject_ref)

    # ------------------------------------------------------------------
    # Deterministic ordering
    # ------------------------------------------------------------------

    @staticmethod
    def _order(candidates: list[BindingCandidate]) -> list[BindingCandidate]:
        return sorted(candidates, key=lambda c: c.subject_ref.serialize())


def InternalId_from_canonical(text: str) -> InternalId:
    """Parse a canonical internal ID string into a typed ``InternalId``."""
    from aota_forge.core.identity.ids import parse_internal_id

    return parse_internal_id(text)


__all__ = [
    "B9_DEPENDS_ON_B8",
    "B9_NEEDS_B7_SOURCE_MUTATION",
    "HEURISTIC_SUBJECT_SELECTION_ALLOWED",
    "MODEL_INTERNAL_IDS_NORMAL_INPUT",
    "SubjectBindingResolver",
    "derive_deterministic_value_from_request",
]


def derive_deterministic_value_from_request(request: BindingRequest) -> str:
    return _derive_deterministic_value(request)