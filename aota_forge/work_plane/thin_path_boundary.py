"""Thin Control Plane / LLM-owned workflow boundary contract — AF #53 M1/W1.

Frozen source-level contract for the approved `foundation_plus_plugins`
boundary convergence (Plan `wzjcccc-dotcom/aota-hermes-tools#53`):

* the task-main LLM owns workflow strategy: plan interpretation,
  decomposition, sequencing, delegation, review/repair strategy, semantic
  and Milestone judgment, next-best-action reasoning;
* the Control Plane owns trusted mechanics only: identity, trusted project
  binding, authorization, project/worktree boundaries, role/tool authority,
  runtime configuration, generic task execution lifecycle state, session
  identity, parent/child binding, durability, provenance, resource bounds
  and mechanical schema validation;
* the Control Plane enforces authority, it does not decide workflow
  strategy, does not validate Plan workflow position, and does not ask why
  an authorized task was started.

Core invariants:

    EXECUTION_LIFECYCLE_STATE_NE_PROJECT_WORKFLOW_STATE=yes
    CONTROL_PLANE_ENFORCES_AUTHORITY_NOT_WORKFLOW_STRATEGY=yes
    CONTROL_PLANE_IS_WORKFLOW_BRAIN=no

Architecture test this contract exists to support:

    If task-main changes workflow strategy,
    Control Plane source should normally not change.

This module freezes the W1 boundary declarations plus the reverse-coupling
map, the legacy freeze and the foundation reuse/adapt boundary. It creates
no execution engine, authority engine, result ontology, workflow engine,
registry, persistence subsystem or second control plane. It deliberately
stays a compact contract seam rather than a copy of the Plan body.

W2 drift guards and M2 thin composition verify against these declarations;
the legacy workflow path itself remains frozen and untouched here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aota_forge.work_plane.roles import WORK_ROLES

# ---------------------------------------------------------------------------
# Ownership split (AC1)
# ---------------------------------------------------------------------------

TASK_MAIN_LLM_CALLER_ROLE = "task-main"

TASK_MAIN_LLM_OWNS: tuple[str, ...] = (
    "plan_interpretation",
    "workflow_strategy",
    "decomposition",
    "sequencing",
    "delegation_strategy",
    "review_strategy",
    "review_timing",
    "repair_strategy",
    "semantic_judgment",
    "milestone_completion_judgment",
    "next_best_action_reasoning",
)

CONTROL_PLANE_OWNS: tuple[str, ...] = (
    "identity",
    "trusted_project_binding",
    "authorization",
    "project_boundary",
    "worktree_boundary",
    "role_authority",
    "tool_authority",
    "runtime_configuration",
    "generic_task_execution_state",
    "session_identity",
    "parent_child_binding",
    "durability",
    "provenance",
    "resource_bounds",
    "mechanical_schema_validation",
)

CONTROL_PLANE_IS_WORKFLOW_BRAIN = False
CONTROL_PLANE_IS_SEMANTIC_INTERPRETER = False
CONTROL_PLANE_REPLACES_LLM_REASONING = False
CONTROL_PLANE_ENFORCES_AUTHORITY_NOT_WORKFLOW_STRATEGY = True

CONTROL_PLANE_REVIEW_FREQUENCY_OPINION = False
CONTROL_PLANE_WORK_SEQUENCE_OPINION = False
CONTROL_PLANE_REPAIR_STRATEGY_OPINION = False

WORKFLOW_STRATEGY_CHANGE_REQUIRES_CONTROL_PLANE_SOURCE_CHANGE = False

# Generic child roles: every Agent Work Role except the task-main dispatch root.
GENERIC_CHILD_TASK_ROLES: tuple[str, ...] = tuple(
    role for role in WORK_ROLES if role != TASK_MAIN_LLM_CALLER_ROLE
)

# ---------------------------------------------------------------------------
# Execution lifecycle state vs project workflow state (AC2)
# ---------------------------------------------------------------------------

EXECUTION_LIFECYCLE_STATE_NE_PROJECT_WORKFLOW_STATE = True
CP_DOES_NOT_VALIDATE_PLAN_WORKFLOW_POSITION = True
CP_DOES_NOT_ASK_WHY_TASK_IS_STARTED = True

# ---------------------------------------------------------------------------
# task.start semantic surface + trusted mechanical enrichment (AC3/AC4)
# ---------------------------------------------------------------------------

TASK_START_OPERATION = "task.start"

# The only semantic inputs the LLM supplies.
TASK_START_LLM_SUPPLIED: tuple[str, ...] = ("role", "handoff_ref")

# Trusted runtime information mechanically derived server-side.
TASK_START_CONTROL_PLANE_ENRICHES: tuple[str, ...] = (
    "project_binding",
    "project_root",
    "worktree_root",
    "worktree_boundary",
    "canonical_task_id",
    "parent_task_identity",
    "origin_session_identity",
    "runtime_profile",
    "tool_surface",
    "role_authority",
    "timeout_resource_bounds",
    "execution_store",
    "completion_route",
    "handoff_provenance_digest",
    "dispatch_idempotency",
)

# Mechanical validation the Control Plane may perform.
TASK_START_CONTROL_PLANE_VALIDATES: tuple[str, ...] = (
    "caller_authority",
    "trusted_project_binding",
    "project_boundary",
    "handoff_existence_integrity",
    "worktree_containment",
    "role_authority",
    "tool_authority",
    "resource_bounds",
    "schema_input_validity",
    "requested_role_grounded_handoff_consistency",
)

# Workflow-strategy judgment the Control Plane must NOT perform.
TASK_START_CONTROL_PLANE_MUST_NOT_VALIDATE: tuple[str, ...] = (
    "plan_workflow_position",
    "work_item_readiness",
    "previous_work_item_completion",
    "plan_dag_position",
    "review_timing",
    "review_frequency",
    "reviewer_choice_reason",
    "repair_strategy",
    "milestone_closure_semantics",
    "next_milestone_progression_strategy",
)

MODEL_AUTHORED_TRUSTED_RUNTIME_BINDING = False

# ---------------------------------------------------------------------------
# Thin generic task lifecycle seam (AF #53 M2/W1)
# ---------------------------------------------------------------------------
#
# M2/W1 realizes the thin-path runtime seam the M1 contract anticipated: the
# canonical generic task lifecycle (``task.start`` / ``task.return``) runs on
# a thin trusted binding that carries no TrustedTaskMainRuntimeContext, and
# therefore requires no MilestonePlanView, TaskMainControlService, coordinator
# state, task_main.advance_once, READY calculation or review transition state.
# The implementation owners below are the real adapted seams.

THIN_TASK_LIFECYCLE_OPERATIONS: tuple[str, ...] = ("task.start", "task.return")
THIN_TASK_LIFECYCLE_IMPLEMENTATION_OWNERS: tuple[str, ...] = (
    "aota_forge/core_ingress/__init__.py",
    "aota_forge/work_plane/task_facade.py",
)
THIN_TASK_LIFECYCLE_REQUIRES_TRUSTED_TASK_MAIN_CONTEXT = False
THIN_NORMAL_TASK_LIFECYCLE_DEPENDS_ON_LEGACY_WORKFLOW_STATE = False

# F2 (accepted M1/RV1 carry-forward; owner M2/W1): the requested child role
# must equal the grounded durable handoff work_role, generic for every child
# role. Mismatch is mechanical integrity, fails closed before dispatch, and
# creates zero child execution.
REQUESTED_ROLE_MUST_EQUAL_GROUNDED_HANDOFF_ROLE = True
ROLE_HANDOFF_MISMATCH_CODE = "ROLE_HANDOFF_MISMATCH"
ROLE_HANDOFF_MISMATCH_FAILS_BEFORE_DISPATCH = True

# I53-B002 repair (M3/W2-R2): thin work-item role grounding is explicit. The
# task-main LLM owns the child-role choice and expresses it as
# payload.work_role at handoff.write; the thin runtime never silently selects
# a child role (missing/invalid role fails closed before dispatch). Only the
# non-thin legacy compatibility path keeps its historical coder default.
THIN_WORK_ITEM_ROLE_EXPLICIT = True
THIN_MISSING_WORK_ROLE_FAILS_CLOSED = True
CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH = False
LEGACY_MISSING_WORK_ROLE_CODER_DEFAULT_PRESERVED = True

# ---------------------------------------------------------------------------
# Thin task-main host composition seam (AF #53 M2/W2)
# ---------------------------------------------------------------------------
#
# M2/W2 realizes the side-by-side thin task-main host composition the M1
# contract anticipated: trusted project binding + existing canonical
# RuntimeConfig + trusted production dispatcher + canonical aota.invoke +
# thin task lifecycle + task-main Role/Skill/tool guidance + existing
# completion delivery/reentry foundation, and never the legacy workflow
# coordinator as the normal-path brain. Selection between legacy and thin is
# a trusted/internal runtime composition choice, not a model-facing argument.
# The legacy host path and production default remain untouched.

THIN_HOST_COMPOSITION_OWNER = "aota_forge/composition/thin_task_main_host.py"
THIN_HOST_COMPOSITION_KIND = "trusted_internal_composition"
THIN_HOST_PRODUCTION_DEFAULT = False
THIN_HOST_SIDE_BY_SIDE = True
MODEL_AUTHORED_THIN_HOST_BINDING = False
THIN_HOST_REQUIRES_LEGACY_WORKFLOW_BRAIN = False
THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE = False
THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW = False
THIN_HOST_REQUIRES_COORDINATOR = False
THIN_HOST_REQUIRES_ADVANCE_ONCE = False
THIN_HOST_WORKFLOW_SPECIAL_OPERATIONS: tuple[str, ...] = (
    "task.observe",
    "task.advance",
    "task.review",
    "task.repair",
    "workflow.next",
)
COMPLETION_DELIVERY_IS_FACTUAL_SIGNAL = True
COMPLETION_DELIVERY_IS_WORKFLOW_DECISION = False

# ---------------------------------------------------------------------------
# Handoff boundary (AC5)
# ---------------------------------------------------------------------------

HANDOFF_SEMANTIC_ARTIFACT = True
HANDOFF_IS_AUTHORITY = False

HANDOFF_SEMANTIC_CONTENT: tuple[str, ...] = (
    "objective",
    "bounded_scope",
    "validation_expectations",
    "semantic_stop_expectations",
)

HANDOFF_CANNOT_ELEVATE: tuple[str, ...] = (
    "project_authority",
    "write_authority",
    "tool_authority",
    "role_authority",
    "worktree_authority",
)

# ---------------------------------------------------------------------------
# Reviewer as a generic child task (AC6)
# ---------------------------------------------------------------------------

REVIEWER_ROLE = "reviewer"
REVIEWER_SPECIAL_WORKFLOW_STATE_REQUIRED = False
REVIEWER_USES_GENERIC_CHILD_TASK_LIFECYCLE = True

# ---------------------------------------------------------------------------
# Legacy workflow freeze (AC7)
# ---------------------------------------------------------------------------

LEGACY_WORKFLOW_PATH_FROZEN = True
LEGACY_REMOVAL_INITIAL_PLAN = False
NO_FURTHER_LEGACY_SEMANTIC_EXPANSION = True

LEGACY_FROZEN_FILES: tuple[str, ...] = (
    "aota_forge/runtime/task_main/runner.py",
    "aota_forge/runtime/task_main/coordinator.py",
    "aota_forge/runtime/task_main/coordinator_state.py",
    "aota_forge/runtime/task_main/coordinator_store.py",
    "aota_forge/runtime/task_main/reconciliation.py",
    "aota_forge/runtime/task_main/control.py",
)

LEGACY_FROZEN_MACHINERY: tuple[str, ...] = (
    "task_main.activate_milestone",
    "task_main.advance_once",
    "task_main.submit_work_projection",
    "MilestonePlanView",
    "READY work calculation",
    "progression-complete",
    "INTEGRATED_REVIEW_REQUIRED",
    "DISPATCHED_REVIEW",
    "REPAIR_REQUIRED",
    "MILESTONE_CLOSURE_READY",
    "NEXT_MILESTONE_USER_GATE",
)

# Actual source symbols realizing the frozen machinery. These must remain
# importable while the legacy compatibility path stays frozen.
LEGACY_FROZEN_SYMBOLS: tuple[str, ...] = (
    "aota_forge.runtime.task_main.coordinator.activate_milestone",
    "aota_forge.runtime.task_main.coordinator.MilestonePlanView",
    "aota_forge.runtime.task_main.coordinator.evaluate_ready_work_items",
    "aota_forge.runtime.task_main.control.TaskMainControlService.advance_once",
    "aota_forge.runtime.task_main.control.TaskMainControlService.submit_work_projection",
    "aota_forge.runtime.task_main.reconciliation.DISPOSITION_PROGRESSION_COMPLETE",
    "aota_forge.runtime.task_main.runner.DISPOSITION_INTEGRATED_REVIEW_REQUIRED",
    "aota_forge.runtime.task_main.runner.DISPOSITION_DISPATCHED_REVIEW",
    "aota_forge.runtime.task_main.runner.DISPOSITION_REPAIR_REQUIRED",
    "aota_forge.runtime.task_main.runner.DISPOSITION_MILESTONE_CLOSURE_READY",
    "aota_forge.runtime.task_main.runner.DISPOSITION_NEXT_MILESTONE_USER_GATE",
)

# ---------------------------------------------------------------------------
# Reverse-coupling map (AC7)
# ---------------------------------------------------------------------------
#
# Production modules currently import legacy runtime.task_main workflow state.
# These couplings are classified as legacy compatibility and are NOT required
# ownership for the thin path. M1/W1 does not remove them; M2 bypasses them for
# the thin normal path (v2 normal path must not depend on advance_once /
# MilestonePlanView / INTEGRATED_REVIEW_REQUIRED / DISPATCHED_REVIEW).

LEGACY_COMPATIBILITY_COUPLING = "legacy_compatibility_coupling"


@dataclass(frozen=True)
class LegacyCoupling:
    """One classified import coupling into the frozen legacy workflow path."""

    importer: str
    imported: tuple[str, ...]
    classification: str
    required_for_thin_path: bool
    symbols: tuple[str, ...] = ()
    note: str = ""


REVERSE_COUPLING_MAP: tuple[LegacyCoupling, ...] = (
    LegacyCoupling(
        "aota_forge.runtime.trusted_runtime_binding",
        ("aota_forge.runtime.task_main.control", "aota_forge.runtime.task_main.coordinator"),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        ("TaskMainControlService", "MilestonePlanView"),
        "TrustedTaskMainRuntimeContext carrier binds legacy workflow types",
    ),
    LegacyCoupling(
        "aota_forge.composition.task_main_host_bootstrap",
        (
            "aota_forge.runtime.task_main.control",
            "aota_forge.runtime.task_main.coordinator",
            "aota_forge.runtime.task_main.coordinator_store",
            "aota_forge.runtime.task_main.reconciliation",
        ),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        (
            "TaskMainControlService",
            "MilestonePlanView",
            "FileBackedTaskMainCoordinatorStore",
            "GovernedWorkItemEvidence",
            "GovernedReviewEvidence",
        ),
        "task-main host bootstrap composes coordinator/progression/review machinery",
    ),
    LegacyCoupling(
        "aota_forge.core_ingress",
        (
            "aota_forge.runtime.task_main.control",
            "aota_forge.runtime.task_main.coordinator",
            "aota_forge.runtime.task_main.coordinator_store",
        ),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        ("AF_TASK_MAIN_ROLE", "TaskMainControlAuthorityError", "build_projection_required_context"),
        "canonical dispatch maps legacy task_main.* operations incl. advance_once",
    ),
    LegacyCoupling(
        "aota_forge.mcp_transport",
        (
            "aota_forge.runtime.task_main.control",
            "aota_forge.runtime.task_main.coordinator",
            "aota_forge.runtime.task_main.coordinator_store",
        ),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        ("TaskMainControlAuthorityError",),
        "typed error identity mapping for legacy task_main failures",
    ),
    LegacyCoupling(
        "aota_forge.core.plan.projection",
        ("aota_forge.runtime.task_main.coordinator",),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        ("MilestonePlanView",),
        "structural Plan projection reuses MilestonePlanView today",
    ),
    LegacyCoupling(
        "aota_forge.work_plane.human_brake",
        ("aota_forge.runtime.task_main.coordinator_state",),
        LEGACY_COMPATIBILITY_COUPLING,
        False,
        ("HUMAN_BRAKE_SCOPES", "HUMAN_BRAKE_STATES"),
        "human brake vocabulary consumed from legacy coordinator state",
    ),
)

# ---------------------------------------------------------------------------
# Foundation reuse / adapt boundary (AC8)
# ---------------------------------------------------------------------------

REUSE_AS_IS = "reuse_as_is"
ADAPT_FOR_THIN_PATH = "adapt_for_thin_path"
FOUNDATION_DISPOSITIONS: tuple[str, ...] = (REUSE_AS_IS, ADAPT_FOR_THIN_PATH)


@dataclass(frozen=True)
class FoundationBoundary:
    """One existing foundation and its W1 disposition."""

    foundation: str
    disposition: str
    owners: tuple[str, ...]


# Owners are existing source paths. REUSE_AS_IS entries are never wrapped or
# cloned for naming consistency; ADAPT_FOR_THIN_PATH entries are the bounded
# candidate seams for the M2 thin path.
FOUNDATION_BOUNDARY: tuple[FoundationBoundary, ...] = (
    FoundationBoundary("project_resolution_and_binding", REUSE_AS_IS, ("aota_forge/core/project/resolver.py", "aota_forge/composition/project_binding.py")),
    FoundationBoundary("identity", REUSE_AS_IS, ("aota_forge/core/context.py",)),
    FoundationBoundary("worktree_sandbox_and_resources", REUSE_AS_IS, ("aota_forge/work_plane/worktree_sandbox.py", "aota_forge/work_plane/worktree_resources.py")),
    FoundationBoundary("workspace_search_read_write", REUSE_AS_IS, ("aota_forge/work_plane/workspace_tools.py", "aota_forge/work_plane/workspace_mutation.py")),
    FoundationBoundary("restricted_shell", REUSE_AS_IS, ("aota_forge/work_plane/restricted_shell.py",)),
    FoundationBoundary("test_execution", REUSE_AS_IS, ("aota_forge/work_plane/test_execution.py",)),
    FoundationBoundary("git_inspection", REUSE_AS_IS, ("aota_forge/work_plane/git_tools.py",)),
    FoundationBoundary("task_handoff_store", REUSE_AS_IS, ("aota_forge/work_plane/handoff.py", "aota_forge/work_plane/handoff_store.py", "aota_forge/work_plane/handoff_runtime.py")),
    FoundationBoundary("task_return_receipt", REUSE_AS_IS, ("aota_forge/work_plane/task_return_receipt.py",)),
    FoundationBoundary("canonical_result_and_result_governance", REUSE_AS_IS, ("aota_forge/core/execution/results.py",)),
    FoundationBoundary("worker_result_card", REUSE_AS_IS, ("aota_forge/work_plane/result_card.py",)),
    FoundationBoundary("durable_result_and_hydration", REUSE_AS_IS, ("aota_forge/work_plane/durable_result_store.py", "aota_forge/work_plane/result_hydrate.py")),
    FoundationBoundary("execution_dispatcher_registry_state", REUSE_AS_IS, ("aota_forge/core/execution/dispatcher.py", "aota_forge/core/execution/registry.py", "aota_forge/core/execution/state.py")),
    FoundationBoundary("durable_execution_delivery_state", REUSE_AS_IS, ("aota_forge/core/execution/durable_state.py",)),
    FoundationBoundary("hermes_executor_launcher_session_reentry", REUSE_AS_IS, ("aota_forge/adapters/hermes", "aota_forge/composition/execution.py")),
    FoundationBoundary("runtime_config", REUSE_AS_IS, ("aota_forge/runtime/config.py",)),
    FoundationBoundary("role_bootstrap_skill_tool_surface", REUSE_AS_IS, ("aota_forge/work_plane/role_bootstrap.py", "aota_forge/work_plane/af_roles.py", "aota_forge/work_plane/tool_surface.py")),
    FoundationBoundary("aota_invoke_transport", REUSE_AS_IS, ("aota_forge/mcp_transport.py",)),
    FoundationBoundary("completion_delivery", REUSE_AS_IS, ("aota_forge/runtime/completion.py",)),
    FoundationBoundary("task_start_task_return_facade", REUSE_AS_IS, ("aota_forge/work_plane/task_facade.py",)),
    FoundationBoundary("thin_trusted_ingress", ADAPT_FOR_THIN_PATH, ("aota_forge/core_ingress/__init__.py",)),
    FoundationBoundary("mcp_transport_exposure", ADAPT_FOR_THIN_PATH, ("aota_forge/mcp_transport.py",)),
    FoundationBoundary("task_main_host_composition", ADAPT_FOR_THIN_PATH, ("aota_forge/composition/task_main_host_bootstrap.py",)),
    FoundationBoundary("trusted_runtime_binding_carrier", ADAPT_FOR_THIN_PATH, ("aota_forge/runtime/trusted_runtime_binding.py",)),
    FoundationBoundary("task_facade_thin_adaptation", ADAPT_FOR_THIN_PATH, ("aota_forge/work_plane/task_facade.py",)),
    FoundationBoundary("thin_launcher_session_composition", ADAPT_FOR_THIN_PATH, ("aota_forge/composition/task_main_daily_launcher.py",)),
    FoundationBoundary("neutral_structural_plan_view", ADAPT_FOR_THIN_PATH, ("aota_forge/core/plan/projection.py",)),
    FoundationBoundary("task_main_descriptors", ADAPT_FOR_THIN_PATH, ("aota_forge/work_plane/task_main_descriptors.py",)),
    FoundationBoundary("af_roles_eager_guidance", ADAPT_FOR_THIN_PATH, ("aota_forge/work_plane/af_roles.py",)),
    FoundationBoundary("task_main_role_skill_guidance", ADAPT_FOR_THIN_PATH, ("aota_forge/roles/task-main.md",)),
)

FOUNDATION_DUPLICATION_INTRODUCED = False

# Negative markers: this contract introduces no new engine/ontology.
NEW_EXECUTION_ENGINE_CREATED = False
NEW_AUTHORITY_ENGINE_CREATED = False
NEW_RESULT_ONTOLOGY_CREATED = False
NEW_WORKFLOW_ENGINE_CREATED = False

# ---------------------------------------------------------------------------
# Deterministic path helpers (source-level freeze checks)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def repository_root() -> Path:
    """Repository root of the current AF checkout."""
    return _REPO_ROOT


def frozen_legacy_file_paths(root: Path | None = None) -> tuple[Path, ...]:
    """Resolved paths of the frozen legacy workflow files."""
    base = Path(root) if root is not None else _REPO_ROOT
    return tuple(base / rel for rel in LEGACY_FROZEN_FILES)


def frozen_legacy_files_present(root: Path | None = None) -> bool:
    """True iff every frozen legacy workflow file still exists."""
    return all(path.is_file() for path in frozen_legacy_file_paths(root))


__all__ = [
    # Ownership split
    "TASK_MAIN_LLM_CALLER_ROLE",
    "TASK_MAIN_LLM_OWNS",
    "CONTROL_PLANE_OWNS",
    "CONTROL_PLANE_IS_WORKFLOW_BRAIN",
    "CONTROL_PLANE_IS_SEMANTIC_INTERPRETER",
    "CONTROL_PLANE_REPLACES_LLM_REASONING",
    "CONTROL_PLANE_ENFORCES_AUTHORITY_NOT_WORKFLOW_STRATEGY",
    "CONTROL_PLANE_REVIEW_FREQUENCY_OPINION",
    "CONTROL_PLANE_WORK_SEQUENCE_OPINION",
    "CONTROL_PLANE_REPAIR_STRATEGY_OPINION",
    "WORKFLOW_STRATEGY_CHANGE_REQUIRES_CONTROL_PLANE_SOURCE_CHANGE",
    "GENERIC_CHILD_TASK_ROLES",
    # Lifecycle/state separation
    "EXECUTION_LIFECYCLE_STATE_NE_PROJECT_WORKFLOW_STATE",
    "CP_DOES_NOT_VALIDATE_PLAN_WORKFLOW_POSITION",
    "CP_DOES_NOT_ASK_WHY_TASK_IS_STARTED",
    # task.start
    "TASK_START_OPERATION",
    "TASK_START_LLM_SUPPLIED",
    "TASK_START_CONTROL_PLANE_ENRICHES",
    "TASK_START_CONTROL_PLANE_VALIDATES",
    "TASK_START_CONTROL_PLANE_MUST_NOT_VALIDATE",
    "MODEL_AUTHORED_TRUSTED_RUNTIME_BINDING",
    # Thin generic task lifecycle seam
    "THIN_TASK_LIFECYCLE_OPERATIONS",
    "THIN_TASK_LIFECYCLE_IMPLEMENTATION_OWNERS",
    "THIN_TASK_LIFECYCLE_REQUIRES_TRUSTED_TASK_MAIN_CONTEXT",
    "THIN_NORMAL_TASK_LIFECYCLE_DEPENDS_ON_LEGACY_WORKFLOW_STATE",
    "REQUESTED_ROLE_MUST_EQUAL_GROUNDED_HANDOFF_ROLE",
    "ROLE_HANDOFF_MISMATCH_CODE",
    "ROLE_HANDOFF_MISMATCH_FAILS_BEFORE_DISPATCH",
    "THIN_WORK_ITEM_ROLE_EXPLICIT",
    "THIN_MISSING_WORK_ROLE_FAILS_CLOSED",
    "CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH",
    "LEGACY_MISSING_WORK_ROLE_CODER_DEFAULT_PRESERVED",
    # Thin task-main host composition seam (M2/W2)
    "THIN_HOST_COMPOSITION_OWNER",
    "THIN_HOST_COMPOSITION_KIND",
    "THIN_HOST_PRODUCTION_DEFAULT",
    "THIN_HOST_SIDE_BY_SIDE",
    "MODEL_AUTHORED_THIN_HOST_BINDING",
    "THIN_HOST_REQUIRES_LEGACY_WORKFLOW_BRAIN",
    "THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE",
    "THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW",
    "THIN_HOST_REQUIRES_COORDINATOR",
    "THIN_HOST_REQUIRES_ADVANCE_ONCE",
    "THIN_HOST_WORKFLOW_SPECIAL_OPERATIONS",
    "COMPLETION_DELIVERY_IS_FACTUAL_SIGNAL",
    "COMPLETION_DELIVERY_IS_WORKFLOW_DECISION",
    # Handoff
    "HANDOFF_SEMANTIC_ARTIFACT",
    "HANDOFF_IS_AUTHORITY",
    "HANDOFF_SEMANTIC_CONTENT",
    "HANDOFF_CANNOT_ELEVATE",
    # Reviewer
    "REVIEWER_ROLE",
    "REVIEWER_SPECIAL_WORKFLOW_STATE_REQUIRED",
    "REVIEWER_USES_GENERIC_CHILD_TASK_LIFECYCLE",
    # Legacy freeze
    "LEGACY_WORKFLOW_PATH_FROZEN",
    "LEGACY_REMOVAL_INITIAL_PLAN",
    "NO_FURTHER_LEGACY_SEMANTIC_EXPANSION",
    "LEGACY_FROZEN_FILES",
    "LEGACY_FROZEN_MACHINERY",
    "LEGACY_FROZEN_SYMBOLS",
    # Reverse coupling
    "LEGACY_COMPATIBILITY_COUPLING",
    "LegacyCoupling",
    "REVERSE_COUPLING_MAP",
    # Foundation boundary
    "REUSE_AS_IS",
    "ADAPT_FOR_THIN_PATH",
    "FOUNDATION_DISPOSITIONS",
    "FoundationBoundary",
    "FOUNDATION_BOUNDARY",
    "FOUNDATION_DUPLICATION_INTRODUCED",
    "NEW_EXECUTION_ENGINE_CREATED",
    "NEW_AUTHORITY_ENGINE_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_WORKFLOW_ENGINE_CREATED",
    # Helpers
    "repository_root",
    "frozen_legacy_file_paths",
    "frozen_legacy_files_present",
]
