"""M3-B10 Frozen 16-Class Regression Matrix Inventory and Metadata (Issue #9, lane M3-B10).

Treats the 16-class successor regression matrix from
deploy/evidence/issues/9/m2-successor-regression-matrix.json as frozen semantic
input (REGRESSION_MATRIX_CLASS_COUNT=16, REGRESSION_MATRIX_MUTATED=no,
B10_REGRESSION_MATRIX_IS_FROZEN_INPUT=yes).

Provides the complete 16-class inventory mapping:
- frozen semantic purpose
- existing legacy evidence
- successor behavior under test
- primary M3 foundation owner(s)
- fixture requirements
- proof type
- whether existing source already has coverage
- whether new B10 proof is required
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


REGRESSION_MATRIX_CLASS_COUNT = 16
REGRESSION_MATRIX_MUTATED = False
B10_REGRESSION_MATRIX_IS_FROZEN_INPUT = True
ALL_16_CLASSES_MAPPED = True

REQUIRED_FAILURE_CLASSES: tuple[str, ...] = (
    "B011",
    "B013",
    "B014",
    "B014-F",
    "B014-F1",
    "ACTIVATE-R-current-binding",
    "ACTIVATE-R-host-inspection-escalation",
    "ACTIVATE-R-wrong-source-checkout",
    "WCTX-1",
    "BIND-1",
    "DRIFT-1",
    "RC2-1",
    "CLASSIFY-1",
    "RECOVERY-1",
    "RUNNER-1",
    "E2E-1",
)


@dataclass(frozen=True)
class RegressionClassInventoryEntry:
    """Complete inventory descriptor for one frozen regression class."""

    failure_class: str
    frozen_semantic_purpose: str
    legacy_evidence: str
    successor_behavior_under_test: str
    primary_m3_foundation_owners: tuple[str, ...]
    fixture_requirements: tuple[str, ...]
    proof_type: str
    has_existing_coverage: bool
    new_b10_proof_required: bool
    successor_owner_milestone: str
    expected_successor_disposition: str
    given: str
    when: str
    then: str


FROZEN_INVENTORY: tuple[RegressionClassInventoryEntry, ...] = (
    RegressionClassInventoryEntry(
        failure_class="B011",
        frozen_semantic_purpose="Prevent pre-binding PLAN_INIT bootstrap and lifecycle cycles; enforce monotonic transitions and revision CAS.",
        legacy_evidence="Issue #8 M7 failure class: pre-binding PLAN_INIT bootstrap/lifecycle cycle.",
        successor_behavior_under_test="Lifecycle transitions are monotonic and deterministic; PLAN_INIT cannot cycle once initialized; stale revision fails closed.",
        primary_m3_foundation_owners=("M3-B6", "M3-B7"),
        fixture_requirements=("in-memory transaction store", "initialized subject graph", "stale revision mutation request"),
        proof_type="TRANSITION_MONOTONICITY_AND_CAS",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A plan subject exists in the durable subject graph with an already-initialized lifecycle state.",
        when="A bootstrap or duplicate initialization transition is requested against that state.",
        then="The transition is rejected; the lifecycle state cannot cycle back; stale revision mutation fails closed.",
    ),
    RegressionClassInventoryEntry(
        failure_class="B013",
        frozen_semantic_purpose="Resolve stale Plan ambiguity and ensure historical plan blocks are treated as provenance observations only without dual authority.",
        legacy_evidence="Issue #8 M7 failure class: stale Plan ambiguity / missing bounded retirement lifecycle.",
        successor_behavior_under_test="Read normalization distinguishes current authoritative Plan state from historical provenance; projection rebuild preserves durable Subject truth.",
        primary_m3_foundation_owners=("M3-B8", "M3-B11"),
        fixture_requirements=("normalized portable plan fixture", "graph projection rebuild fixture with historical/superseded blocks"),
        proof_type="PLAN_NORMALIZATION_AND_PROJECTION_TRUTH",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A project with active plan state and historical superseded blocks or shadow plan records.",
        when="The normalized plan reader and projection rebuild service resolve current plan state.",
        then="Only authoritative state is current; superseded blocks are provenance observations only; no dual authority.",
    ),
    RegressionClassInventoryEntry(
        failure_class="B014",
        frozen_semantic_purpose="Prevent post-binding PLAN_INIT producer-consumer cycles; bound lifecycle subject cannot ping-pong lifecycle state.",
        legacy_evidence="Issue #8 M7 failure class: post-binding PLAN_INIT producer-consumer cycle.",
        successor_behavior_under_test="Post-binding transitions are validated against owning Subject revision and state; invalid re-entry into initial states is rejected.",
        primary_m3_foundation_owners=("M3-B6", "M3-B7"),
        fixture_requirements=("bound subject in transaction store", "post-binding transition request with producer/consumer conflict"),
        proof_type="POST_BINDING_TRANSITION_CAS",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A lifecycle subject that is already bound in the durable subject graph.",
        when="A transition is requested by producer or consumer attempting invalid lifecycle cycling.",
        then="The transition is rejected; stale revision fails closed; no producer-consumer cycle can form.",
    ),
    RegressionClassInventoryEntry(
        failure_class="B014-F",
        frozen_semantic_purpose="Ensure Materialization and canonical transition failures surface authoritatively without silent suppression.",
        legacy_evidence="Issue #8 M7 failure class: production Materialization authority gap.",
        successor_behavior_under_test="Any transition or Materialization stage failure triggers atomic rollback and surfaces bounded canonical error envelopes.",
        primary_m3_foundation_owners=("M3-B6", "M3-B7"),
        fixture_requirements=("transaction store with forced failure at staging boundaries", "rollback verification"),
        proof_type="ATOMIC_FAILURE_SURFACING",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A transition or Materialization operation that encounters failure after partial staging.",
        when="The operation fails and returns its canonical result envelope.",
        then="The canonical result reflects authoritative failure; no partial mutation or unbacked artifact remains usable.",
    ),
    RegressionClassInventoryEntry(
        failure_class="B014-F1",
        frozen_semantic_purpose="Prevent Finalizer lifecycle-recognition drift by deriving lifecycle state from canonical contract, not private adapter state.",
        legacy_evidence="Issue #8 M7 failure class: Finalizer lifecycle-recognition drift.",
        successor_behavior_under_test="Task state and lifecycle derivation derive from canonical result state; executor neutrality is preserved.",
        primary_m3_foundation_owners=("M3-B1", "M3-B7"),
        fixture_requirements=("canonical result envelope", "executor-neutral contract descriptors"),
        proof_type="EXECUTOR_NEUTRAL_LIFECYCLE_DERIVATION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M5",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="An execution completion or task result reported through canonical transition interfaces.",
        when="Lifecycle recognition is evaluated for the owning Subject.",
        then="Recognition derives strictly from canonical result state; private executor bookkeeping cannot drift from canonical state.",
    ),
    RegressionClassInventoryEntry(
        failure_class="ACTIVATE-R-current-binding",
        frozen_semantic_purpose="Enforce that current Subject projection derives deterministically from canonical graph truth and legacy current_* pointers possess zero binding authority.",
        legacy_evidence="Issue #8 M7 failure class: current decision/followup subject binding conflict.",
        successor_behavior_under_test="Durable Subject in graph binds deterministically; stale/missing projection does not hide/delete Subject; legacy current pointer cannot bind.",
        primary_m3_foundation_owners=("M3-B8", "M3-B9"),
        fixture_requirements=("graph repository with durable Subject", "stale/missing projection", "conflicting legacy current pointer"),
        proof_type="GRAPH_AUTHORITATIVE_BINDING_AND_PROJECTION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M3",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A durable subject graph containing subjects with conflicting or stale legacy current_* pointers.",
        when="Subject binding and projection resolution run for current decision or followup.",
        then="Binding and projection derive from graph truth; stale pointer cannot hide or substitute a valid subject; ambiguity returns NEEDS_SEMANTIC_CHOICE.",
    ),
    RegressionClassInventoryEntry(
        failure_class="ACTIVATE-R-host-inspection-escalation",
        frozen_semantic_purpose="Eliminate safe Host read-only inspection escalation into Plan/SPEC/Profile Task/approval by construction.",
        legacy_evidence="Issue #8 M7 failure class: safe Host read-only inspection unnecessarily escalated.",
        successor_behavior_under_test="Host inspection executes without Plan, Milestone, Work Item, SPEC, Profile Task, approval, or mutation authority.",
        primary_m3_foundation_owners=("M2", "M3-B1", "M3-B2"),
        fixture_requirements=("host.status contract descriptor", "ingress execution with empty plan context"),
        proof_type="READONLY_DIAGNOSTIC_PLANE_ISOLATION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="ELIMINATED_BY_CONSTRUCTION",
        given="A read-only Host inspection request through canonical ingress.",
        when="The host.status operation contract handles the request.",
        then="The operation completes with host evidence only; no Plan/SPEC/Profile Task or mutation authority is required or created.",
    ),
    RegressionClassInventoryEntry(
        failure_class="ACTIVATE-R-wrong-source-checkout",
        frozen_semantic_purpose="Eliminate git boundary escapes by bounding repository discovery to the resolved canonical project root.",
        legacy_evidence="Issue #8 M7 failure class: validation used wrong source checkout instead of reviewed activation source.",
        successor_behavior_under_test="Git discovery is strictly bounded to project root; boundary escapes fail closed with GitBoundaryViolationError.",
        primary_m3_foundation_owners=("M2", "M3-B2"),
        fixture_requirements=("isolated project repository with nested directory and outside path", "boundary violation assertion"),
        proof_type="PROJECT_GIT_BOUNDARY_FAIL_CLOSED",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="ELIMINATED_BY_CONSTRUCTION",
        given="A resolved project root and a git inspection path.",
        when="Git discovery runs inside and outside the project boundary.",
        then="Nested paths resolve within the root; escapes raise GitBoundaryViolationError; repositories above boundary are not followed.",
    ),
    RegressionClassInventoryEntry(
        failure_class="WCTX-1",
        frozen_semantic_purpose="Preserve distinct error taxonomy across workspace, project, plan, and work item contexts without collapsing into generic NOT_FOUND.",
        legacy_evidence="Issue #9 amendment: distinct lifecycle error semantics.",
        successor_behavior_under_test="Distinct root causes (PROJECT_BINDING_MISSING, PLAN_MISSING, PLAN_WORKSPACE_CONTEXT_MISSING, ACTIVE_WORK_ITEM_MISSING, etc.) produce pairwise distinct canonical error codes.",
        primary_m3_foundation_owners=("M3-B1", "M3-B7", "M3-B9"),
        fixture_requirements=("multi-root-cause error generation fixtures", "pairwise distinction assertions"),
        proof_type="DISTINCT_ERROR_TAXONOMY_PRESERVATION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="Operations experiencing distinct context root causes.",
        when="Each missing-prerequisite operation returns its canonical error envelope.",
        then="Canonical error codes are pairwise distinct and machine-readable; different root causes never collapse.",
    ),
    RegressionClassInventoryEntry(
        failure_class="BIND-1",
        frozen_semantic_purpose="Enforce Subject binding automation rule: 0 candidates fail-closed, 1 candidate binds deterministically after validity filtering, >1 candidates return NEEDS_SEMANTIC_CHOICE without heuristic reduction.",
        legacy_evidence="Issue #9 amendment: Project Decision vs Binding.",
        successor_behavior_under_test="Zero/one/many binding holds; candidate source is canonical graph; binding implies zero mutation authority; heuristics denied.",
        primary_m3_foundation_owners=("M3-B5", "M3-B9"),
        fixture_requirements=("graph repository with 0, 1 valid, 1 invalid, and >1 candidate subjects", "validity filter test cases"),
        proof_type="DETERMINISTIC_ZERO_ONE_MANY_BINDING",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A binding request evaluated against candidate subjects in the canonical graph.",
        when="Candidate filtering and classification execute.",
        then="Zero candidates return bounded fail-closed result; 1 valid candidate binds deterministically; multiple valid candidates return NEEDS_SEMANTIC_CHOICE.",
    ),
    RegressionClassInventoryEntry(
        failure_class="DRIFT-1",
        frozen_semantic_purpose="Eliminate contract and role surface drift by deriving adapter projections deterministically from one declarative registry with contract_hash drift detection.",
        legacy_evidence="Issue #9 amendment: contract / role surface drift.",
        successor_behavior_under_test="Single declarative contract registry; deterministic contract_hash / protocol_version drift detection; graph truth unaffected by projection drift.",
        primary_m3_foundation_owners=("M2", "M3-B1", "M3-B8"),
        fixture_requirements=("contract registry", "mismatched contract hash / protocol version request", "projection drift check"),
        proof_type="CONTRACT_AND_PROJECTION_DRIFT_DETECTION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="ELIMINATED_BY_CONSTRUCTION",
        given="Adapters and clients requesting canonical operations with declared or mismatched contract identities.",
        when="Contract drift detection evaluates the request.",
        then="Mismatched contract_hash or protocol_version is rejected before execution; matching identity executes deterministically; graph truth invariant under projection drift.",
    ),
    RegressionClassInventoryEntry(
        failure_class="RC2-1",
        frozen_semantic_purpose="Ensure Materialization and transaction failures report authoritative failure envelopes and prevent half-committed side channels.",
        legacy_evidence="Issue #9 amendment: silent / non-authoritative Materialization failure.",
        successor_behavior_under_test="Failed transaction does not consume lease or advance revision; error envelope reports authoritative failure; retry is idempotent.",
        primary_m3_foundation_owners=("M3-B6", "M3-B7"),
        fixture_requirements=("staged transaction with forced failure", "lease consumption check", "revision advance check"),
        proof_type="TRANSACTION_FAILURE_ATOMICITY_AND_SURFACING",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A mechanical transaction or Materialization step that fails during execution.",
        when="The transaction rolls back and returns.",
        then="The machine result reports authoritative failure with bounded error details; no lease is consumed; parent revision does not advance.",
    ),
    RegressionClassInventoryEntry(
        failure_class="CLASSIFY-1",
        frozen_semantic_purpose="Eliminate project classification ambiguity and disappearance beyond listing boundaries without project hint authority.",
        legacy_evidence="Issue #9 amendment: existing project candidate classification.",
        successor_behavior_under_test="Complete deterministic project scan (60+ projects); exact project ID matches itself; duplicates fail closed as PROJECT_AMBIGUOUS; no hint authority.",
        primary_m3_foundation_owners=("M2", "M3-B2"),
        fixture_requirements=("workspace with 60+ projects", "exact match test", "duplicate match test"),
        proof_type="DETERMINISTIC_PROJECT_CLASSIFICATION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M2",
        expected_successor_disposition="ELIMINATED_BY_CONSTRUCTION",
        given="A workspace with many registered projects (>50) and a project resolution request.",
        when="Deterministic candidate scan and project resolution execute.",
        then="The exact project resolves to itself; duplicate project ids fail closed as PROJECT_AMBIGUOUS; no hint parameter can alter authority.",
    ),
    RegressionClassInventoryEntry(
        failure_class="RECOVERY-1",
        frozen_semantic_purpose="Provide bounded deterministic post-binding recovery by reconciling durable state and classifying stale/missing projections without guessing or graph mutation.",
        legacy_evidence="Issue #9 amendment: post-binding deterministic recovery.",
        successor_behavior_under_test="Recovery reconciles durable graph state, classifies stale/missing projections, surfaces ambiguity explicitly, and performs zero canonical graph writes.",
        primary_m3_foundation_owners=("M3-B8", "M3-B9"),
        fixture_requirements=("graph with missing/stale projection", "binder recovery classification", "graph state stability check"),
        proof_type="BOUNDED_MECHANICAL_RECOVERY",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M4",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A valid binding whose prerequisite projection is missing or stale.",
        when="Mechanical recovery processes the case.",
        then="Result deterministically reconciles or surfaces NEEDS_SEMANTIC_CHOICE; zero graph writes; no guessing or Subject invention.",
    ),
    RegressionClassInventoryEntry(
        failure_class="RUNNER-1",
        frozen_semantic_purpose="Prevent selection of unsupported or Docker-era runner candidates; maintain host-only deployment contract and executor neutrality.",
        legacy_evidence="Issue #9 amendment: wrong runtime/runner selection.",
        successor_behavior_under_test="Runtime inspection is host-aligned, executor-neutral, and rejects non-canonical discoverable runtime candidates.",
        primary_m3_foundation_owners=("M2", "M3-B1", "M3-B2"),
        fixture_requirements=("runtime inspect contract", "host environment fixture"),
        proof_type="EXECUTOR_NEUTRAL_RUNTIME_INSPECTION",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M5",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A runtime inspection request in an environment with discoverable host-only runtime.",
        when="Runtime status and inspection execute.",
        then="The canonical host runtime is inspected; discoverable unsupported candidates are not selected; executor identity remains adapter-level.",
    ),
    RegressionClassInventoryEntry(
        failure_class="E2E-1",
        frozen_semantic_purpose="Prevent production-chain behavioral evidence gaps by establishing deterministic successor behavioral proof at the milestone where operations are introduced.",
        legacy_evidence="Issue #9 amendment: production-chain behavioral evidence gap.",
        successor_behavior_under_test="Deterministic behavioral proofs model real operation chains without requiring external networks, live GitHub, or deployment infrastructure.",
        primary_m3_foundation_owners=("M3-B10", "M3-B14"),
        fixture_requirements=("isolated chained operation fixture", "pre-cutover deterministic assertion"),
        proof_type="SUCCESSOR_BEHAVIORAL_CHAIN_PROOF",
        has_existing_coverage=True,
        new_b10_proof_required=True,
        successor_owner_milestone="M5",
        expected_successor_disposition="NOT_YET_IMPLEMENTED",
        given="A requirement for chained behavioral evidence across M3 canonical foundations.",
        when="Deterministic successor behavioral proofs run in isolated fixture environments.",
        then="Real chained behavior (authority -> lease -> CAS -> transition -> projection -> binding -> recovery) is proven end-to-end without network or production mutation.",
    ),
)


INVENTORY_BY_CLASS: Mapping[str, RegressionClassInventoryEntry] = {
    entry.failure_class: entry for entry in FROZEN_INVENTORY
}


def get_inventory_entry(failure_class: str) -> RegressionClassInventoryEntry:
    """Retrieve the frozen inventory entry for a failure class."""
    if failure_class not in INVENTORY_BY_CLASS:
        raise KeyError(f"Unknown regression failure class: {failure_class!r}")
    return INVENTORY_BY_CLASS[failure_class]


def list_inventory_classes() -> tuple[str, ...]:
    """Return the ordered list of all 16 frozen failure classes."""
    return tuple(entry.failure_class for entry in FROZEN_INVENTORY)


__all__ = [
    "ALL_16_CLASSES_MAPPED",
    "B10_REGRESSION_MATRIX_IS_FROZEN_INPUT",
    "FROZEN_INVENTORY",
    "INVENTORY_BY_CLASS",
    "REGRESSION_MATRIX_CLASS_COUNT",
    "REGRESSION_MATRIX_MUTATED",
    "REQUIRED_FAILURE_CLASSES",
    "RegressionClassInventoryEntry",
    "get_inventory_entry",
    "list_inventory_classes",
]
