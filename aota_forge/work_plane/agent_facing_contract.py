"""Agent-facing Contract Freeze — AF #48 M1/W1.

Freeze exact Agent-facing responsibility model for the seven tool/lifecycle
families:

    search, read, write, terminal, handoff, task.start, task.return

Target architecture (normative):

    Role + Base Skill
           │
           ▼
         Agent
           │
     ┌─────┼─────────────────────────────────┐
     ▼     ▼       ▼        ▼        ▼       │
    search read   write   terminal  handoff │
                                             │
    task-main only ─────────────── task.start
    one-shot roles ─────────────── task.return

Common roles (exactly five, no sixth):

    task-main, analyst, coder, reviewer, project-steward

``AgentWorkRole`` PROJECT_STEWARD = "project-steward" is the Agent-facing
correct name. ``CanonicalRole`` STEWARD = "steward" in
``core.execution.roles`` remains as internal execution/compatibility mapping
and must NOT be globally rewritten. Only Agent-facing / AgentWorkRole
contexts use ``project-steward``.

This module is W1 **contract freeze only**. It declares normative
responsibility boundaries, validates them deterministically, and references
existing internal seams for reuse. It creates no new execution engine,
authority engine, result ontology, coordinator, second control plane, or
generic policy/YAML framework. W2/W3/W4 own thin façade convergence.

Each family is frozen with dimensions:

    NAME, CALLER/APPLICABLE ROLES, LLM-SUPPLIED INPUT,
    CONTROL-PLANE-SUPPLIED INPUT, CONTROL-PLANE VALIDATION,
    OUTPUT, EXPLICIT NON-RESPONSIBILITIES, INTERNAL REUSED SEAM,
    AUTHORITY SOURCE

Implementation representation is minimal typed constants + dataclass
instances (smallest existing seam). No generic metadata framework is
created to encode the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aota_forge.work_plane.roles import AgentWorkRole

# ---------------------------------------------------------------------------
# Architecture guardrails (normative, §5)
# ---------------------------------------------------------------------------

CONTROL_PLANE_ROLE: str = "assist_and_guard"
CONTROL_PLANE_IS_WORKFLOW_BRAIN: bool = False
CONTROL_PLANE_IS_SEMANTIC_INTERPRETER: bool = False
CONTROL_PLANE_REPLACES_LLM_REASONING: bool = False

LLM_OWNS: tuple[str, ...] = (
    "semantic_interpretation",
    "reasoning",
    "decomposition",
    "implementation_strategy",
    "evidence_judgment",
    "handoff_semantic_content",
)

CONTROL_PLANE_OWNS: tuple[str, ...] = (
    "identity",
    "trusted_binding",
    "project_boundary",
    "worktree_boundary",
    "role_authority",
    "tool_authority",
    "user_gates",
    "lifecycle_state",
    "durability",
    "provenance",
    "mechanical_schema_validation",
    "resource_bounds",
)

ONE_CONTROL_PLANE: bool = True
AF_CONTROL_PLANE_COUNT: int = 1
ONE_AGENT_FACING_AOTA_MCP_TOOL: bool = True
AGENT_FACING_AOTA_TOOL: str = "aota.invoke"
SKILL_IS_AUTHORITY: bool = False
TOOL_VISIBILITY_IS_AUTHORITY: bool = False
ROLE_TOOL_SURFACE_IS_AUTHORITY: bool = False
SERVER_SIDE_AUTHORITY_REQUIRED: bool = True

# Foundation reuse markers
FOUNDATION_REUSE_EXPECTED: bool = True
EXPECTED_ARCHITECTURE_CHANGE: str = "boundary_convergence"
EXPECTED_SOURCE_CHANGE_CLASS: str = "bounded_medium_small"
EXPECTED_CORE_REWRITE: bool = False
EXPECTED_RUNTIME_REWRITE: bool = False
EXPECTED_HERMES_REWRITE: bool = False
EXPECTED_EXECUTION_ENGINE_REWRITE: bool = False
LARGE_SCALE_CORE_REWRITE_REQUIRED: bool = False

# Negative markers — must remain False / no new subsystem
NEW_EXECUTION_ENGINE_CREATED: bool = False
NEW_AUTHORITY_ENGINE_CREATED: bool = False
NEW_RESULT_ONTOLOGY_CREATED: bool = False
NEW_COORDINATOR_CREATED: bool = False
SECOND_CONTROL_PLANE_CREATED: bool = False
POLICY_YAML_EXTERNALIZATION_PERFORMED: bool = False
NEW_TASK_STATE_MACHINE_CREATED: bool = False
NEW_GENERIC_POLICY_ENGINE_CREATED: bool = False

# ---------------------------------------------------------------------------
# Common surface (§4)
# ---------------------------------------------------------------------------

COMMON_AGENT_TOOL_FAMILIES: tuple[str, ...] = ("search", "read", "write", "terminal", "handoff")
TASK_MAIN_EXTRA: str = "task.start"
ONE_SHOT_ROLE_EXTRA: str = "task.return"

# Exactly five roles, no sixth
AGENT_WORK_ROLE_COUNT: int = 5
COMMON_ROLES: tuple[str, ...] = ("task-main", "analyst", "coder", "reviewer", "project-steward")
NO_SIXTH_WORK_ROLE: bool = True

# One-shot roles that may call task.return (coder, analyst, reviewer, project-steward)
ONE_SHOT_ROLES: frozenset[str] = frozenset({"coder", "analyst", "reviewer", "project-steward"})
TASK_START_CALLER: str = "task-main"
TASK_RETURN_CALLERS: frozenset[str] = ONE_SHOT_ROLES

# Verify steward naming invariant: Agent-facing uses project-steward, not steward
AGENT_WORK_ROLE_STEWARD_NAME: str = AgentWorkRole.PROJECT_STEWARD.value
AGENT_FACING_CONTRACT_USES_STEWARD_AS_AGENT_WORK_ROLE: bool = False  # must be False
CANONICAL_ROLE_STEWARD_COMPATIBILITY_PRESERVED: bool = True  # core.execution.roles.CanonicalRole.STEWARD remains "steward"

# ---------------------------------------------------------------------------
# Search contract (§7.1)
# ---------------------------------------------------------------------------

SEARCH_NAME: str = "search"
SEARCH_CALLER_APPLICABLE_ROLES: tuple[str, ...] = COMMON_ROLES  # all roles may search within authorized project/worktree
SEARCH_AGENT_INTENT: str = "find relevant project content"
SEARCH_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("query", "search intent")
SEARCH_LLM_OWNS: tuple[str, ...] = ("query / search intent", "semantic judgment of results")
SEARCH_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = (
    "role identity",
    "project binding",
    "worktree binding",
    "authorized boundary",
    "resource/output bounds",
)
SEARCH_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "role identity matches bound WorkRole",
    "project/worktree binding trusted",
    "query bounded length and resource bounds",
    "cross-project fail-closed",
    "output bounded",
)
SEARCH_OUTPUT: tuple[str, ...] = ("bounded lexical matches with snippets", "project_id/worktree_id evidence")
SEARCH_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "does not grant read authority",
    "search result is not Plan authority",
    "does not require Analyst delegation by threshold",
)
SEARCH_INTERNAL_REUSED_SEAM: str = "BoundedWorkspaceToolProvider + WorktreeSandboxBoundary + resolve_worktree_resource"
SEARCH_AUTHORITY_SOURCE: str = "AF_CORE server-side trusted binding (sandbox + handoff + policy + descriptor)"

AUTHORIZED_PROJECT_SEARCH: str = "broad"
CROSS_PROJECT_SEARCH_FAIL_CLOSED: bool = True
SEARCH_RESULT_IS_AUTHORITY: bool = False
ANALYST_IS_READ_PERMISSION_PROXY: bool = False  # Analyst is not a proxy
SEARCH_THRESHOLD_GATE_REQUIRED: bool = False  # no search_count > N gates

# ---------------------------------------------------------------------------
# Read contract (§7.2)
# ---------------------------------------------------------------------------

READ_NAME: str = "read"
READ_CALLER_APPLICABLE_ROLES: tuple[str, ...] = COMMON_ROLES
READ_AGENT_INTENT: str = "read project implementation/evidence"
READ_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("path", "offset/max_bytes")
READ_LLM_OWNS: tuple[str, ...] = ("read target selection", "evidence judgment")
READ_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = SEARCH_CONTROL_PLANE_SUPPLIED_INPUT
READ_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "role identity",
    "project/worktree binding",
    "worktree-bound path validation",
    "symlink/revalidation safety",
    "resource containment and bounds",
    "cross-project fail-closed",
)
READ_OUTPUT: tuple[str, ...] = ("bounded file content with truncation metadata",)
READ_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "read result is not Plan authority",
    "does not invent filesystem ACL from TaskHandoff scope or AGENTS applicability",
    "preserves resource containment / symlink / revalidation safety",
)
READ_INTERNAL_REUSED_SEAM: str = "BoundedWorkspaceToolProvider + WorktreeSandboxBoundary + ProjectBoundResourceResolver"
READ_AUTHORITY_SOURCE: str = SEARCH_AUTHORITY_SOURCE

AUTHORIZED_PROJECT_READ: str = "broad"
CROSS_PROJECT_READ_FAIL_CLOSED: bool = True
READ_RESULT_IS_PLAN_AUTHORITY: bool = False

# ---------------------------------------------------------------------------
# Write contract (§7.3) — conceptual policy only, W3 owns rebalance implementation
# ---------------------------------------------------------------------------

WRITE_NAME: str = "write"
WRITE_CALLER_APPLICABLE_ROLES: tuple[str, ...] = COMMON_ROLES  # conceptual; enforcement is role_and_scope_bounded
WRITE_AGENT_INTENT: str = "mutate assigned project worktree product content"
WRITE_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("path", "content", "mode")
WRITE_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = SEARCH_CONTROL_PLANE_SUPPLIED_INPUT
WRITE_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "role_and_scope bounded check",
    "mutation authority distinct from read authority",
    "worktree-bound path validation and revalidation at use",
    "symlink escape fail-closed",
    "atomic replace",
)
WRITE_OUTPUT: tuple[str, ...] = ("bounded artifact reference with digest",)
WRITE_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "W1 does not implement full W3 authority rebalance",
    "broad read does not imply broad write",
    "Control-plane artifact/state write distinct from product source write",
)
WRITE_INTERNAL_REUSED_SEAM: str = "BoundedWorkspaceMutationProvider + WorktreeSandboxBoundary"
WRITE_AUTHORITY_SOURCE: str = "AF_CORE server-side mutation authority (sandbox + handoff + policy + workspace.write descriptor)"

AUTHORIZED_PROJECT_WRITE: str = "role_and_scope_bounded"
BROAD_READ: bool = True
NARROW_WRITE: bool = True

# Conceptual product-write policy (frozen, W3 implements)
TASK_MAIN_PRODUCT_SOURCE_WRITE_ALLOWED: bool = False
ANALYST_PRODUCT_SOURCE_WRITE_ALLOWED: bool = False
REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED: bool = False
CODER_ASSIGNED_PROJECT_WORKTREE_PRODUCT_WRITE_ALLOWED: bool = True
PROJECT_STEWARD_GOVERNED_MUTATION_ONLY: bool = True

# ---------------------------------------------------------------------------
# Terminal contract (§7.4)
# ---------------------------------------------------------------------------

TERMINAL_NAME: str = "terminal"
TERMINAL_AGENT_INTENT: str = "bounded_restricted_terminal"
TERMINAL_IS_RAW_BASH: bool = False
TERMINAL_KIND: str = "bounded_restricted_terminal"
TERMINAL_CALLER_APPLICABLE_ROLES: tuple[str, ...] = COMMON_ROLES  # visibility; actual allow/deny is role×command-family in W3
TERMINAL_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("command_id", "args", "timeout")
TERMINAL_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = SEARCH_CONTROL_PLANE_SUPPLIED_INPUT
TERMINAL_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "argv-style, no shell=True",
    "trusted executable catalog (no arbitrary path)",
    "project/worktree-bound cwd (never caller arbitrary)",
    "argument/path validation via resolve_worktree_resource",
    "bounded env (deny-by-default)",
    "timeout required + process-tree termination",
    "bounded stdout/stderr/total with truthful truncation",
    "no nested shell / no interpreter escape",
)
TERMINAL_OUTPUT: tuple[str, ...] = ("exit_code, bounded stdout/stderr, duration, truncation metadata",)
TERMINAL_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "W1 freezes responsibility; W3 implements rebalance",
    "does not expose raw bash",
    "does not externalize policy to YAML in W1",
)
TERMINAL_INTERNAL_REUSED_SEAM: str = "BoundedRestrictedShellProvider + WorktreeSandboxBoundary"
TERMINAL_AUTHORITY_SOURCE: str = "AF_CORE restricted-shell authority (role×command-family×trusted binding×argument/path policy in W3; W1 only freezes kind)"

# Mechanics preserved from existing RestrictedShell
TERMINAL_ARGV_STYLE: bool = True
TERMINAL_NO_SHELL_TRUE: bool = True
TERMINAL_TRUSTED_CATALOG: bool = True
TERMINAL_NO_ARBITRARY_EXECUTABLE_PATH: bool = True
TERMINAL_PROJECT_WORKTREE_BOUND_CWD: bool = True
TERMINAL_BOUNDED_ENV: bool = True
TERMINAL_TIMEOUT_REQUIRED: bool = True
TERMINAL_PROCESS_TREE_TERMINATION: bool = True
TERMINAL_BOUNDED_STDOUT_STDERR: bool = True
TERMINAL_NO_NESTED_SHELL: bool = True
TERMINAL_ROLE_COMMAND_POLICY: bool = True
NO_POLICY_YAML_EXTERNALIZATION: bool = True

# ---------------------------------------------------------------------------
# Handoff contract (§8)
# ---------------------------------------------------------------------------

HANDOFF_NAME: str = "handoff"
HANDOFF_CALLER_APPLICABLE_ROLES: tuple[str, ...] = COMMON_ROLES
HANDOFF_AGENT_INTENT: str = "structured semantic artifact I/O"
HANDOFF_LLM_SUPPLIED_INPUT: dict[str, tuple[str, ...]] = {
    "write": ("mode=milestone|work_item|result", "semantic fields per mode"),
    "open": ("handoff_ref", "view=card|full"),
}
HANDOFF_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = (
    "artifact_id",
    "project_id",
    "plan_ref",
    "milestone_id",
    "work_item_id",
    "source_role",
    "target_role",
    "task_id",
    "attempt_id",
    "created_at",
    "schema_version",
    "binding/provenance metadata",
    "digest",
)
HANDOFF_SEMANTIC_PAYLOAD_FIELDS: tuple[str, ...] = (
    "objective",
    "scope",
    "context",
    "acceptance",
    "stop_conditions",
    "summary",
    "work_done",
    "validation",
    "findings",
    "blockers",
    "recommendation",
    "useful_refs",
)
HANDOFF_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "schema/bounds validation",
    "digest/provenance generation",
    "role determines which modes allowed",
    "LLM cannot mutate control fields",
    "Control Plane does not rewrite LLM semantics",
)
HANDOFF_OUTPUT: tuple[str, ...] = ("durable handoff ref with digest", "open returns card or full view")
HANDOFF_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "handoff does not dispatch Worker",
    "handoff does not terminate task",
    "control envelope != TaskHandoff semantic payload (TaskHandoff has deliberately semantic contract)",
)
HANDOFF_INTERNAL_REUSED_SEAM: str = "TaskHandoff (semantic) + handoff_runtime durable store + result governance"
HANDOFF_AUTHORITY_SOURCE: str = "AF_CORE handoff governance (control envelope owned by Control Plane, semantic payload owned by LLM)"

HANDOFF_ACTIONS: tuple[str, ...] = ("write", "open")
HANDOFF_WRITE_MODES: tuple[str, ...] = ("milestone", "work_item", "result")
HANDOFF_OPEN_VIEWS: tuple[str, ...] = ("card", "full")
HANDOFF_DISPATCHES_WORKER: bool = False
HANDOFF_TERMINATES_TASK: bool = False
CONTROL_PLANE_REWRITES_LLM_SEMANTICS: bool = False
LLM_MUTATES_CONTROL_FIELDS: bool = False
HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD: bool = True
CONTROL_METADATA_MUST_NOT_BE_FORCED_INTO_TASK_HANDOFF_SEMANTIC_FIELDS: bool = True

# Critical boundary — control envelope distinct from TaskHandoff semantic payload
HANDOFF_CONTROL_ENVELOPE_IS_TASK_HANDOFF_SEMANTIC_PAYLOAD: bool = False
TASK_HANDOFF_SEMANTIC_BOUNDARY_PRESERVED: bool = True
HANDOFF_CONTROL_ENVELOPE_SEPARATE: bool = True

# ---------------------------------------------------------------------------
# task.start contract (§9)
# ---------------------------------------------------------------------------

TASK_START_NAME: str = "task.start"
TASK_START_CALLER: str = "task-main"  # normal caller task-main only
TASK_START_AGENT_FACADE: str = "task.start(role=<target AgentWorkRole>, handoff_ref=<durable work_item handoff>)"
TASK_START_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("role (target AgentWorkRole)", "handoff_ref (durable Work Item handoff)")
TASK_START_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = (
    "caller dispatch authority",
    "project/worktree/task binding",
    "trusted resolution of handoff",
)
TASK_START_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "validate caller dispatch authority (task-main only)",
    "validate target AgentWorkRole in {coder, analyst, reviewer, project-steward}",
    "validate handoff existence",
    "validate handoff digest",
    "validate project/worktree/task binding",
    "start/reuse existing execution machinery",
    "return task identity/status",
)
TASK_START_OUTPUT: tuple[str, ...] = ("task_id/task identity", "status")
TASK_START_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "does not invent objective",
    "does not rewrite scope",
    "does not construct semantic Work meaning",
    "does not perform Worker work",
    "does not create Result",
)
TASK_START_INTERNAL_REUSED_SEAM: str = "execution.task_start (internal/core execution operation) via trusted resolution/compilation"
TASK_START_AUTHORITY_SOURCE: str = "AF_CORE task-main dispatch authority"
TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM: bool = True
EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED: bool = False
TASK_START_FAÇADE_MAPS_TO_EXISTING_EXECUTION_SEAM: bool = True
TASK_START_IS_NOT_SEMANTIC_RESULT_CREATION: bool = True

# ---------------------------------------------------------------------------
# task.return contract (§10)
# ---------------------------------------------------------------------------

TASK_RETURN_NAME: str = "task.return"
TASK_RETURN_CALLERS: frozenset[str] = ONE_SHOT_ROLES
TASK_RETURN_AGENT_FACADE: str = "task.return(status=completed|blocked|failed, result_ref=<durable full Result handoff ref>)"
TASK_RETURN_LLM_SUPPLIED_INPUT: tuple[str, ...] = ("status (completed|blocked|failed)", "result_ref (durable full Result handoff ref)")
TASK_RETURN_CONTROL_PLANE_SUPPLIED_INPUT: tuple[str, ...] = (
    "caller one-shot role identity",
    "trusted result binding",
    "lifecycle state",
)
TASK_RETURN_CONTROL_PLANE_VALIDATION: tuple[str, ...] = (
    "validate caller is one-shot role",
    "validate result ref exists and is full Result",
    "reuse completion/finalization/wakeup seams",
    "mark terminal lifecycle return",
)
TASK_RETURN_OUTPUT: tuple[str, ...] = ("completion delivery to parent task-main (card + full ref)",)
TASK_RETURN_EXPLICIT_NON_RESPONSIBILITIES: tuple[str, ...] = (
    "does not create Work semantics",
    "is not handoff.write",
    "is not execution.task_result (fetch concept)",
    "handoff.write(mode=result) without task.return does NOT terminate",
)
TASK_RETURN_INTERNAL_REUSED_SEAM: str = "completion / finalization / durable result state / WorkerResultCard / wakeup/re-entry"
TASK_RETURN_AUTHORITY_SOURCE: str = "AF_CORE completion governance (child→parent terminal return)"

TASK_RETURN_CREATES_WORK_SEMANTICS: bool = False
TASK_RETURN_IS_NOT_HANDOFF_WRITE: bool = True
TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT: bool = True
TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS: bool = True
TASK_RETURN_IS_CHILD_TO_PARENT_TERMINAL_RETURN: bool = True
# handoff.write(mode=result) is separate from termination
WORKER_MAY_HANDOFF_WRITE_WITHOUT_TERMINATING: bool = True
ONLY_TASK_RETURN_MARKS_TERMINAL_RETURN: bool = True

# ---------------------------------------------------------------------------
# Result/Card contract (§11)
# ---------------------------------------------------------------------------

RESULT_NAME: str = "result"
WORKER_RESULT_FULL_WRITE_COUNT_NORMAL: int = 1
WORKER_AUTHORS_RESULT_CARD: bool = False
RESULT_CARD_GENERATION: str = "deterministic_from_full_result"
RESULT_FULL_WRITE_COUNT_NORMAL: int = 1
RESULT_CARD_IS_DETERMINISTIC_FROM_FULL: bool = True
RESULT_CARD_IS_AUTHORITY: bool = False

RESULT_INTERNAL_REUSED_SEAM: str = "CanonicalResult + ResultGovernanceProjection + WorkerResultCard + ResultHandoffRef"
RESULT_AUTHORITY_SOURCE: str = "AF_CORE result governance (deterministic compact Card from accepted full Result)"

RESULT_DELIVERY: str = "full Result → existing result governance → deterministic compact WorkerResultCard → completion event → task-main"
RESULT_COMPLETION_EVENT_FIELDS: tuple[str, ...] = ("task_id", "status", "compact card", "full_result_ref")
RESULT_NORMAL_TASK_MAIN_CARD_EXTRA_READ_CALL: int = 0

# ---------------------------------------------------------------------------
# WorkSemanticProjection boundary (§12)
# ---------------------------------------------------------------------------

WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED: bool = False
WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED: bool = True
WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_SEAMS_REUSED: bool = True
INTERNAL_COMPATIBILITY_SEAMS_REUSED: bool = True

# Normal semantic path: authoritative bounded Plan/Work source → task-main LLM reasoning → handoff.write(mode=work_item)
WORK_SEMANTIC_NORMAL_PATH: str = "authoritative bounded Plan/Work source → task-main LLM reasoning → handoff.write(mode=work_item)"
# Internal compatibility allowed: handoff semantic → WorkSemanticProjection compatibility representation
WORK_SEMANTIC_INTERNAL_COMPATIBILITY_PATH: str = "handoff semantic content → WorkSemanticProjection compatibility representation (allowed)"

# Must NOT require Plan → deterministic semantic compiler → WorkSemanticProjection → LLM
PLAN_TO_WORK_SEMANTIC_PROJECTION_REQUIRED_AS_AGENT_PRESTEP: bool = False

# ---------------------------------------------------------------------------
# Existing task_main.* operations classification (§13)
# ---------------------------------------------------------------------------

# Inspect, do not prematurely delete. Classify each as:
#   Agent-facing required / internal runtime/coordinator seam / compatibility seam / candidate for later retirement
# Expected: submit_work_projection no longer required as Agent-facing semantic compiler/prestep under #48,
# but internal compatibility use may remain. Destructive removal belongs to later only if proven necessary.

TASK_MAIN_OPERATION_CLASSIFICATION: dict[str, str] = {
    "task_main.activate_milestone": "internal runtime/coordinator seam (task-main lifecycle; Agent-facing via bootstrap/wakeup, not direct model dispatch)",
    "task_main.recover_coordinator": "internal runtime/coordinator seam (session recovery; not Agent-facing direct)",
    "task_main.advance_once": "internal runtime/coordinator seam (coordinator advance; not Agent-facing direct)",
    "task_main.submit_work_projection": "compatibility seam (internal compatibility allowed; Agent-facing required = no under #48 — LLM writes handoff.write(mode=work_item) instead)",
}

TASK_MAIN_SUBMIT_WORK_PROJECTION_AGENT_FACING_REQUIRED: bool = False
TASK_MAIN_SUBMIT_WORK_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED: bool = True

# ---------------------------------------------------------------------------
# Operation descriptor / canonical ingress rules (§14)
# ---------------------------------------------------------------------------

ONE_AGENT_FACING_AOTA_MCP_TOOL_REQUIRED: bool = True
CANONICAL_OPERATION_DISPATCH_PLANE_COUNT: int = 1
OPERATION_DESCRIPTOR_AUTHORITY_COUNT: int = 1
ONE_AGENT_FACING_AOTA_MCP_TOOL_ACTUAL: bool = ONE_AGENT_FACING_AOTA_MCP_TOOL
AGENT_FACING_AOTA_MCP_TOOL_NAME: str = AGENT_FACING_AOTA_TOOL
CANONICAL_DESCRIPTOR_AUTHORITY: str = ".aota/contracts/operations.yaml"

# Do not register Agent-facing live operation merely as documentation if no valid provider/dispatch exists yet.
DO_NOT_REGISTER_AGENT_FACING_LIVE_OPERATION_WITHOUT_PROVIDER: bool = True
W1_IS_CONTRACT_FREEZE_NOT_LIVE_REGISTRATION: bool = True
W1_IMPLEMENTATION_CONVERGENCE_OWNERS: tuple[str, ...] = ("W2", "W3", "W4")

# ---------------------------------------------------------------------------
# Façade/internal boundary acceptance (approved 2026-09-11, §3.1B)
# ---------------------------------------------------------------------------

AGENT_FACING_CONTRACT_DOES_NOT_REQUIRE_INTERNAL_TYPE_REWRITE: bool = True
# HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD already defined above as True
# CONTROL_METADATA_MUST_NOT_BE_FORCED_INTO_TASK_HANDOFF_SEMANTIC_FIELDS already True
# TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM already True
# EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED already False
TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT_ALIAS: bool = TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT
# TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS already True
# WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED already False
# WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED already True
# INTERNAL_COMPATIBILITY_SEAMS_REUSED already True

# ---------------------------------------------------------------------------
# Typed contract helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentToolContract:
    """Minimal typed contract for one Agent-facing family.

    Mirrors the required table dimensions without creating a generic metadata
    framework. Each instance is a frozen value object; authority remains with
    server-side binding, not this declaration.
    """

    name: str
    caller_applicable_roles: tuple[str, ...]
    llm_supplied_input: tuple[str, ...]
    control_plane_supplied_input: tuple[str, ...]
    control_plane_validation: tuple[str, ...]
    output: tuple[str, ...]
    explicit_non_responsibilities: tuple[str, ...]
    internal_reused_seam: str
    authority_source: str


# Frozen contract instances for the seven families

SEARCH_CONTRACT: AgentToolContract = AgentToolContract(
    name=SEARCH_NAME,
    caller_applicable_roles=SEARCH_CALLER_APPLICABLE_ROLES,
    llm_supplied_input=SEARCH_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=SEARCH_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=SEARCH_CONTROL_PLANE_VALIDATION,
    output=SEARCH_OUTPUT,
    explicit_non_responsibilities=SEARCH_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=SEARCH_INTERNAL_REUSED_SEAM,
    authority_source=SEARCH_AUTHORITY_SOURCE,
)

READ_CONTRACT: AgentToolContract = AgentToolContract(
    name=READ_NAME,
    caller_applicable_roles=READ_CALLER_APPLICABLE_ROLES,
    llm_supplied_input=READ_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=READ_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=READ_CONTROL_PLANE_VALIDATION,
    output=READ_OUTPUT,
    explicit_non_responsibilities=READ_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=READ_INTERNAL_REUSED_SEAM,
    authority_source=READ_AUTHORITY_SOURCE,
)

WRITE_CONTRACT: AgentToolContract = AgentToolContract(
    name=WRITE_NAME,
    caller_applicable_roles=WRITE_CALLER_APPLICABLE_ROLES,
    llm_supplied_input=WRITE_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=WRITE_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=WRITE_CONTROL_PLANE_VALIDATION,
    output=WRITE_OUTPUT,
    explicit_non_responsibilities=WRITE_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=WRITE_INTERNAL_REUSED_SEAM,
    authority_source=WRITE_AUTHORITY_SOURCE,
)

TERMINAL_CONTRACT: AgentToolContract = AgentToolContract(
    name=TERMINAL_NAME,
    caller_applicable_roles=TERMINAL_CALLER_APPLICABLE_ROLES,
    llm_supplied_input=TERMINAL_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=TERMINAL_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=TERMINAL_CONTROL_PLANE_VALIDATION,
    output=TERMINAL_OUTPUT,
    explicit_non_responsibilities=TERMINAL_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=TERMINAL_INTERNAL_REUSED_SEAM,
    authority_source=TERMINAL_AUTHORITY_SOURCE,
)

HANDOFF_CONTRACT: AgentToolContract = AgentToolContract(
    name=HANDOFF_NAME,
    caller_applicable_roles=HANDOFF_CALLER_APPLICABLE_ROLES,
    llm_supplied_input=("mode and semantic fields per mode", "handoff_ref + view for open"),
    control_plane_supplied_input=HANDOFF_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=HANDOFF_CONTROL_PLANE_VALIDATION,
    output=HANDOFF_OUTPUT,
    explicit_non_responsibilities=HANDOFF_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=HANDOFF_INTERNAL_REUSED_SEAM,
    authority_source=HANDOFF_AUTHORITY_SOURCE,
)

TASK_START_CONTRACT: AgentToolContract = AgentToolContract(
    name=TASK_START_NAME,
    caller_applicable_roles=(TASK_START_CALLER,),
    llm_supplied_input=TASK_START_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=TASK_START_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=TASK_START_CONTROL_PLANE_VALIDATION,
    output=TASK_START_OUTPUT,
    explicit_non_responsibilities=TASK_START_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=TASK_START_INTERNAL_REUSED_SEAM,
    authority_source=TASK_START_AUTHORITY_SOURCE,
)

TASK_RETURN_CONTRACT: AgentToolContract = AgentToolContract(
    name=TASK_RETURN_NAME,
    caller_applicable_roles=tuple(sorted(TASK_RETURN_CALLERS)),
    llm_supplied_input=TASK_RETURN_LLM_SUPPLIED_INPUT,
    control_plane_supplied_input=TASK_RETURN_CONTROL_PLANE_SUPPLIED_INPUT,
    control_plane_validation=TASK_RETURN_CONTROL_PLANE_VALIDATION,
    output=TASK_RETURN_OUTPUT,
    explicit_non_responsibilities=TASK_RETURN_EXPLICIT_NON_RESPONSIBILITIES,
    internal_reused_seam=TASK_RETURN_INTERNAL_REUSED_SEAM,
    authority_source=TASK_RETURN_AUTHORITY_SOURCE,
)

ALL_AGENT_CONTRACTS: tuple[AgentToolContract, ...] = (
    SEARCH_CONTRACT,
    READ_CONTRACT,
    WRITE_CONTRACT,
    TERMINAL_CONTRACT,
    HANDOFF_CONTRACT,
    TASK_START_CONTRACT,
    TASK_RETURN_CONTRACT,
)

ALL_AGENT_CONTRACT_NAMES: frozenset[str] = frozenset(c.name for c in ALL_AGENT_CONTRACTS)


def get_agent_contract(name: str) -> AgentToolContract:
    """Fail-closed lookup for Agent-facing contract by name."""
    if not isinstance(name, str) or type(name) is not str:
        raise TypeError(f"name must be str, got {type(name).__name__}")
    for c in ALL_AGENT_CONTRACTS:
        if c.name == name:
            return c
    raise ValueError(f"Unknown Agent contract: {name!r}. Known: {sorted(ALL_AGENT_CONTRACT_NAMES)}")


def is_task_start_caller(role: str | AgentWorkRole) -> bool:
    """Return True iff *role* is allowed to call task.start (task-main only)."""
    if isinstance(role, AgentWorkRole):
        return role.value == TASK_START_CALLER
    if isinstance(role, str) and type(role) is str:
        return role == TASK_START_CALLER
    return False


def is_task_return_caller(role: str | AgentWorkRole) -> bool:
    """Return True iff *role* is allowed to call task.return (one-shot roles)."""
    if isinstance(role, AgentWorkRole):
        return role.value in TASK_RETURN_CALLERS
    if isinstance(role, str) and type(role) is str:
        return role in TASK_RETURN_CALLERS
    return False


def validate_task_start_caller(role: object) -> str:
    """Validate caller for task.start, fail-closed."""
    if isinstance(role, AgentWorkRole):
        if role.value != TASK_START_CALLER:
            raise ValueError(f"task.start caller must be {TASK_START_CALLER!r}, got {role.value!r}")
        return role.value
    if not isinstance(role, str) or type(role) is not str:
        raise TypeError(f"task.start caller must be str or AgentWorkRole, got {type(role).__name__}")
    if role != TASK_START_CALLER:
        raise ValueError(f"task.start caller must be {TASK_START_CALLER!r}, got {role!r}")
    return role


def validate_task_return_caller(role: object) -> str:
    """Validate caller for task.return, fail-closed."""
    if isinstance(role, AgentWorkRole):
        if role.value not in TASK_RETURN_CALLERS:
            raise ValueError(f"task.return caller must be one of {sorted(TASK_RETURN_CALLERS)}, got {role.value!r}")
        return role.value
    if not isinstance(role, str) or type(role) is not str:
        raise TypeError(f"task.return caller must be str or AgentWorkRole, got {type(role).__name__}")
    if role not in TASK_RETURN_CALLERS:
        raise ValueError(f"task.return caller must be one of {sorted(TASK_RETURN_CALLERS)}, got {role!r}")
    return role


def contract_maps_to_existing_seam(contract: AgentToolContract) -> bool:
    """All W1 contracts must explicitly map toward an existing internal seam."""
    return bool(contract.internal_reused_seam and contract.internal_reused_seam.strip())


__all__ = [
    "CONTROL_PLANE_ROLE",
    "CONTROL_PLANE_IS_WORKFLOW_BRAIN",
    "CONTROL_PLANE_IS_SEMANTIC_INTERPRETER",
    "CONTROL_PLANE_REPLACES_LLM_REASONING",
    "LLM_OWNS",
    "CONTROL_PLANE_OWNS",
    "ONE_CONTROL_PLANE",
    "AF_CONTROL_PLANE_COUNT",
    "ONE_AGENT_FACING_AOTA_MCP_TOOL",
    "AGENT_FACING_AOTA_TOOL",
    "SKILL_IS_AUTHORITY",
    "TOOL_VISIBILITY_IS_AUTHORITY",
    "ROLE_TOOL_SURFACE_IS_AUTHORITY",
    "SERVER_SIDE_AUTHORITY_REQUIRED",
    "FOUNDATION_REUSE_EXPECTED",
    "EXPECTED_ARCHITECTURE_CHANGE",
    "EXPECTED_SOURCE_CHANGE_CLASS",
    "NEW_EXECUTION_ENGINE_CREATED",
    "NEW_AUTHORITY_ENGINE_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_COORDINATOR_CREATED",
    "SECOND_CONTROL_PLANE_CREATED",
    "POLICY_YAML_EXTERNALIZATION_PERFORMED",
    "COMMON_AGENT_TOOL_FAMILIES",
    "TASK_MAIN_EXTRA",
    "ONE_SHOT_ROLE_EXTRA",
    "AGENT_WORK_ROLE_COUNT",
    "COMMON_ROLES",
    "NO_SIXTH_WORK_ROLE",
    "ONE_SHOT_ROLES",
    "TASK_START_CALLER",
    "TASK_RETURN_CALLERS",
    "AGENT_WORK_ROLE_STEWARD_NAME",
    "AGENT_FACING_CONTRACT_USES_STEWARD_AS_AGENT_WORK_ROLE",
    "CANONICAL_ROLE_STEWARD_COMPATIBILITY_PRESERVED",
    "AUTHORIZED_PROJECT_SEARCH",
    "CROSS_PROJECT_SEARCH_FAIL_CLOSED",
    "SEARCH_RESULT_IS_AUTHORITY",
    "ANALYST_IS_READ_PERMISSION_PROXY",
    "SEARCH_THRESHOLD_GATE_REQUIRED",
    "AUTHORIZED_PROJECT_READ",
    "CROSS_PROJECT_READ_FAIL_CLOSED",
    "READ_RESULT_IS_PLAN_AUTHORITY",
    "AUTHORIZED_PROJECT_WRITE",
    "BROAD_READ",
    "NARROW_WRITE",
    "TASK_MAIN_PRODUCT_SOURCE_WRITE_ALLOWED",
    "ANALYST_PRODUCT_SOURCE_WRITE_ALLOWED",
    "REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED",
    "CODER_ASSIGNED_PROJECT_WORKTREE_PRODUCT_WRITE_ALLOWED",
    "PROJECT_STEWARD_GOVERNED_MUTATION_ONLY",
    "TERMINAL_KIND",
    "TERMINAL_IS_RAW_BASH",
    "TERMINAL_ARGV_STYLE",
    "TERMINAL_NO_SHELL_TRUE",
    "TERMINAL_TRUSTED_CATALOG",
    "TERMINAL_NO_ARBITRARY_EXECUTABLE_PATH",
    "TERMINAL_PROJECT_WORKTREE_BOUND_CWD",
    "TERMINAL_BOUNDED_ENV",
    "TERMINAL_TIMEOUT_REQUIRED",
    "TERMINAL_PROCESS_TREE_TERMINATION",
    "TERMINAL_BOUNDED_STDOUT_STDERR",
    "TERMINAL_NO_NESTED_SHELL",
    "TERMINAL_ROLE_COMMAND_POLICY",
    "NO_POLICY_YAML_EXTERNALIZATION",
    "HANDOFF_ACTIONS",
    "HANDOFF_WRITE_MODES",
    "HANDOFF_OPEN_VIEWS",
    "HANDOFF_DISPATCHES_WORKER",
    "HANDOFF_TERMINATES_TASK",
    "CONTROL_PLANE_REWRITES_LLM_SEMANTICS",
    "LLM_MUTATES_CONTROL_FIELDS",
    "HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD",
    "CONTROL_METADATA_MUST_NOT_BE_FORCED_INTO_TASK_HANDOFF_SEMANTIC_FIELDS",
    "HANDOFF_CONTROL_ENVELOPE_IS_TASK_HANDOFF_SEMANTIC_PAYLOAD",
    "TASK_HANDOFF_SEMANTIC_BOUNDARY_PRESERVED",
    "HANDOFF_CONTROL_ENVELOPE_SEPARATE",
    "TASK_START_NAME",
    "TASK_START_AGENT_FACADE",
    "TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM",
    "EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED",
    "TASK_START_FAÇADE_MAPS_TO_EXISTING_EXECUTION_SEAM",
    "TASK_RETURN_NAME",
    "TASK_RETURN_IS_NOT_HANDOFF_WRITE",
    "TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT",
    "TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS",
    "WORKER_RESULT_FULL_WRITE_COUNT_NORMAL",
    "WORKER_AUTHORS_RESULT_CARD",
    "RESULT_CARD_GENERATION",
    "WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED",
    "WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED",
    "INTERNAL_COMPATIBILITY_SEAMS_REUSED",
    "TASK_MAIN_OPERATION_CLASSIFICATION",
    "TASK_MAIN_SUBMIT_WORK_PROJECTION_AGENT_FACING_REQUIRED",
    "ONE_AGENT_FACING_AOTA_MCP_TOOL_REQUIRED",
    "CANONICAL_OPERATION_DISPATCH_PLANE_COUNT",
    "OPERATION_DESCRIPTOR_AUTHORITY_COUNT",
    "AGENT_FACING_CONTRACT_DOES_NOT_REQUIRE_INTERNAL_TYPE_REWRITE",
    "TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT_ALIAS",
    "AgentToolContract",
    "SEARCH_CONTRACT",
    "READ_CONTRACT",
    "WRITE_CONTRACT",
    "TERMINAL_CONTRACT",
    "HANDOFF_CONTRACT",
    "TASK_START_CONTRACT",
    "TASK_RETURN_CONTRACT",
    "ALL_AGENT_CONTRACTS",
    "ALL_AGENT_CONTRACT_NAMES",
    "get_agent_contract",
    "is_task_start_caller",
    "is_task_return_caller",
    "validate_task_start_caller",
    "validate_task_return_caller",
    "contract_maps_to_existing_seam",
]
