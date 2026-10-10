"""AF Role Bootstrap — exclusive Role/Soul/Tool/Skill convergence (M3 W1).

M3/W1 production convergence:
- Five Role Skill universes converged from accepted Role contracts
  (ROLE_CONTRACT_DERIVES_SKILL_UNIVERSE=yes, EXISTING_SKILL_IMPLIES_ASSIGNMENT=no).
- Orphan Skills reconciled deterministically (assign_eager / assign_progressive /
  remain_unassigned); orphan != defect automatically.
- Tool surfaces converged to accepted least-privilege matrix (visibility != authority).
- Eager Skill content is curated compact usable guidance (no semantic truncation),
  bounded inline (4096). Progressive refs are short opaque refs; skill.open returns
  usable content in one call; result.hydrate only for large refs/artifacts.
- Maintainer content excluded from normal LLM runtime (separate MAINTAINER.md).

Reuses existing contracts only:
- AgentWorkRole / Soul / TaskHandoff / ToolRoleSurface
- SkillIdentity / StaticSkillRegistry / AllowedSkillUniverse
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES
from aota_forge.work_plane.soul import Soul
from aota_forge.work_plane.tool_surface import create_role_tool_surface, ToolRoleSurface
from aota_forge.work_plane.skill import SkillIdentity, compute_skill_digest
from aota_forge.work_plane.skill_registry import SkillRegistryEntry, StaticSkillRegistry
from aota_forge.work_plane.skill_resolution import AllowedSkill, AllowedSkillUniverse
from aota_forge.work_plane.handoff import SemanticReference

# ---------------------------------------------------------------------------
# M3/W1 convergence markers (descriptive, not authority)
# ---------------------------------------------------------------------------

FIVE_ROLE_SKILL_UNIVERSES_CONVERGED = True
ROLE_CONTRACT_DERIVES_SKILL_UNIVERSE = True
EXISTING_SKILL_IMPLIES_ASSIGNMENT = False
ORPHAN_SKILLS_RECONCILED = True
ROLE_OPERATION_VISIBILITY_CONVERGED = True
SKILL_CONTROL_PLANE_DUPLICATION_MINIMIZED = True
MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT = False
EAGER_SKILL_CONTENT_IS_USABLE_GUIDANCE = True
SEMANTIC_TRUNCATION_FOR_BOOTSTRAP = False

# M1/W4 task-main consolidation markers. These describe the guidance
# ownership boundary; runtime authorization remains in the existing ingress.
TASK_MAIN_EAGER_BASE_SKILL_COUNT = 1
TASK_MAIN_BASE_SKILL_SUFFICIENT_FOR_NORMAL_HAPPY_PATH = True
COMPETING_TASK_MAIN_OPERATIONAL_CONTRACTS = 0
LEGACY_TASK_MAIN_SKILLS_IN_NORMAL_ROUTING = 0
NORMAL_TASK_MAIN_REQUIRES_MULTI_SKILL_PRELOAD = False
BASE_SKILL_OWNS_EXACT_OPERATION_SCHEMA = False
PROJECT_GOVERNANCE_SKILL_PROGRESSIVE = True
PLAN_GOVERNANCE_SKILL_PROGRESSIVE = True
GOVERNANCE_SKILL_GOV2_NORMAL = True
GOVERNANCE_SKILL_GOV1_COMPATIBILITY = True
GOV1_IS_NORMAL_NEW_PLAN_PATH = False
GUIDANCE_ADVERTISES_UNAVAILABLE_OPERATION = False
SOUL_OWNS_WORKFLOW = False
SOUL_OWNS_OPERATION_SCHEMA = False

# ---------------------------------------------------------------------------
# Soul catalog — load from AF-owned markdown files (aota_forge/roles/<role>.md)
# ---------------------------------------------------------------------------

def _roles_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "roles"

def _load_soul_content(role: str) -> str:
    p = _roles_dir() / f"{role}.md"
    if not p.is_file():
        raise FileNotFoundError(f"AF soul content missing for role {role!r} at {p}")
    txt = p.read_text(encoding="utf-8").strip()
    if not txt:
        raise ValueError(f"AF soul content empty for role {role!r}")
    return txt

_SOUL_CACHE: dict[str, Soul] = {}
_SOUL_VERSION = "1.0.0"

def get_soul_for_role(role: AgentWorkRole | str) -> Soul:
    if isinstance(role, AgentWorkRole):
        key = role.value
    else:
        from aota_forge.work_plane.roles import parse_agent_work_role
        key = parse_agent_work_role(role).value
    if key in _SOUL_CACHE:
        return _SOUL_CACHE[key]
    content = _load_soul_content(key)
    soul = Soul(content=content, version=_SOUL_VERSION)
    _SOUL_CACHE[key] = soul
    return soul

AF_SOULS: dict[str, Soul] = {r: get_soul_for_role(r) for r in WORK_ROLES}

# ---------------------------------------------------------------------------
# Tool surfaces — converged least-privilege visibility (visibility != authority)
#
# Actual-operation matrix (current operation names):
# task-main: the canonical #59 thin/ref-scoped exposure (role.bootstrap,
#            skill.open, help, introspection, Plan-bound GitHub/Git/workspace,
#            handoff/task lifecycle, result.hydrate); eager = read-classified,
#            progressive = mutation-classified. Legacy task_main.* workflow
#            operations are not part of the thin surface.
# analyst: search/read + handoff.open/handoff.write/task.return eager;
#          write+hydrate+shell progressive conditional; no test/task_main
# coder: search/read/write/edit/test + handoff.open/handoff.write/task.return eager,
#        hydrate+shell progressive conditional
# reviewer: search/read/test + handoff.open/handoff.write/task.return eager,
#           hydrate progressive; no write/shell
# steward: search/read + handoff.open/handoff.write/task.return eager,
#          hydrate progressive; no write/test/shell/git/github
#
# W5 (AF #49 M1/W5, I49-B001): the canonical normal-path handoff/task
# lifecycle operations are visible to the roles whose normal path uses them.
# Visibility is not authority: server-side role/mode validation still decides.
# ---------------------------------------------------------------------------

WORKSPACE_SEARCH = "workspace.search"
WORKSPACE_READ = "workspace.read"
WORKSPACE_WRITE = "workspace.write"
WORKSPACE_EDIT = "workspace.edit"
RESULT_HYDRATE = "result.hydrate"
RESTRICTED_SHELL = "restricted_shell.run"
TEST_RUN_OP = "test.run"
# AF #59 M2/W1-D: the legacy workflow-brain operations are no longer part of
# the task-main normal surface. Their legacy implementations stay present
# (not deleted in this Plan); they are simply not visible to the thin
# task-main role.
LEGACY_TASK_MAIN_WORKFLOW_OPS: tuple[str, ...] = (
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
    "task_main.submit_work_projection",
)
ROLE_BOOTSTRAP_OP = "role.bootstrap"
SKILL_OPEN_OP = "skill.open"
HELP_OP = "help"
HANDOFF_WRITE_OP = "handoff.write"
HANDOFF_OPEN_OP = "handoff.open"
TASK_START_OP = "task.start"
TASK_RETURN_OP = "task.return"
# Normal-path lifecycle visibility (exposure only; authority stays server-side)
TASK_MAIN_NORMAL_PATH_OPS = (HANDOFF_WRITE_OP, HANDOFF_OPEN_OP, TASK_START_OP)
WORKER_NORMAL_PATH_OPS = (HANDOFF_OPEN_OP, HANDOFF_WRITE_OP, TASK_RETURN_OP)

TASK_MAIN_NORMAL_PATH_OPERATIONS_VISIBLE = True
WORKER_LIFECYCLE_OPERATIONS_VISIBLE = True
# AF #59 M2/W1-D: the task-main role surface derives from the one canonical
# role-filtered actual exposure (never a parallel hand-maintained list).
LEGACY_TASK_MAIN_WORKFLOW_OPS_VISIBLE_TO_THIN_TASK_MAIN = False

_TOOL_SURFACES: dict[str, ToolRoleSurface] = {}


def task_main_thin_operations() -> tuple[str, ...]:
    """Canonical current #59 task-main operation exposure (derived).

    The single source is the light ``work_plane.task_main_exposure`` module
    (the same set ``role.bootstrap.AOTA_MCP`` and unbound ``operations.list``
    report). No second operation catalog is introduced.
    """
    from aota_forge.work_plane.task_main_exposure import (
        canonical_task_main_operations,
    )

    return canonical_task_main_operations()


# AF #59 M2/W1-D: accepted contract disposition — result.hydrate stays
# progressive for every role (large-reference ref remains optional; it is
# never an eager capability). This is a bounded disposition, not a second
# operation catalog.
_ALWAYS_PROGRESSIVE_ON_TASK_MAIN: frozenset[str] = frozenset({RESULT_HYDRATE})


def _task_main_surface_partition() -> tuple[list[str], list[str]]:
    """Deterministic eager/progressive split derived from the descriptors.

    Read-classified exposed operations are eager (except the accepted
    always-progressive hydration disposition); the remaining
    (mutation-classified) operations are progressive. Classification comes
    from the same canonical descriptor source used by the task-main
    descriptors, never a hand-maintained list.
    """
    from aota_forge.core.contracts.loader import (
        discover_canonical_project_root,
        load_operation_descriptor_map,
    )

    descriptors = load_operation_descriptor_map(discover_canonical_project_root())
    eager: list[str] = []
    progressive: list[str] = []
    for name in task_main_thin_operations():
        descriptor = descriptors.get(name)
        read_write = getattr(descriptor, "read_write", "write")
        if read_write == "read" and name not in _ALWAYS_PROGRESSIVE_ON_TASK_MAIN:
            eager.append(name)
        else:
            progressive.append(name)
    return eager, progressive


def _build_tool_surfaces() -> None:
    if _TOOL_SURFACES:
        return
    task_main_eager, task_main_progressive = _task_main_surface_partition()
    _TOOL_SURFACES["task-main"] = create_role_tool_surface(
        "task-main",
        eager=task_main_eager,
        progressive=task_main_progressive,
    )
    _TOOL_SURFACES["analyst"] = create_role_tool_surface(
        "analyst",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ] + list(WORKER_NORMAL_PATH_OPS),
        progressive=[WORKSPACE_WRITE, RESULT_HYDRATE, RESTRICTED_SHELL],
    )
    _TOOL_SURFACES["coder"] = create_role_tool_surface(
        "coder",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ, WORKSPACE_WRITE, WORKSPACE_EDIT, TEST_RUN_OP] + list(WORKER_NORMAL_PATH_OPS),
        progressive=[RESULT_HYDRATE, RESTRICTED_SHELL],
    )
    _TOOL_SURFACES["reviewer"] = create_role_tool_surface(
        "reviewer",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ, TEST_RUN_OP] + list(WORKER_NORMAL_PATH_OPS),
        progressive=[RESULT_HYDRATE],
    )
    _TOOL_SURFACES["project-steward"] = create_role_tool_surface(
        "project-steward",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ] + list(WORKER_NORMAL_PATH_OPS),
        progressive=[RESULT_HYDRATE],
    )

_build_tool_surfaces()

def get_tool_surface_for_role(role: AgentWorkRole | str) -> ToolRoleSurface:
    if isinstance(role, AgentWorkRole):
        key = role.value
    else:
        from aota_forge.work_plane.roles import parse_agent_work_role
        key = parse_agent_work_role(role).value
    return _TOOL_SURFACES[key]

AF_TOOL_SURFACES = dict(_TOOL_SURFACES)

# ---------------------------------------------------------------------------
# Skill catalog — converged five-Role universes (exclusive AF source)
#
# ROLE x SKILL matrix (ASSIGNMENT eager/progressive/not_assigned):
# task-main: task-main-control eager; workspace-operations/result-hydration/
#            task-main-governance/project-governance/plan-governance progressive
# analyst: workspace-operations + evidence-first-debugging eager;
#          result-hydration + restricted-shell progressive
# coder: workspace-operations + spec-driven-implementation eager;
#        result-hydration + restricted-shell progressive
# reviewer: workspace-operations + implementation-review eager;
#           result-hydration progressive
# steward: workspace-operations + pcf-project-steward eager;
#          result-hydration + multi-phase-doc-closure progressive
# All other existing Skills remain_unassigned (correct when not in normal path).
# ---------------------------------------------------------------------------

_SKILL_META: dict[str, tuple[str, str]] = {
    "aota-task-main-control": (
        "Canonical task-main normal path: local Governance 2.0 context, "
        "card-first reasoning, bounded child dispatch and typed stop behavior",
        "use as the single eager Base Skill for ordinary task-main work; "
        "open progressive guidance only for a specialized workflow",
    ),
    "aota-task-main-governance": (
        "Task-main governance workflow: Governance 2.0 local authority is normal; "
        "bounded Governance 1.x GitHub compatibility is explicit and legacy-only",
        "open before Project/Plan governance mutation, close, checkpoint, or "
        "an explicitly requested legacy GitHub reconciliation",
    ),
    "aota-project-governance": (
        "Governance 2.0 Project identity, trusted registration and generated projection intake",
        "open when creating, inspecting or reconciling a Project through governance.project",
    ),
    "aota-plan-governance": (
        "Governance 2.0 local Plan authority and bounded Plan lifecycle intake",
        "open when initializing, amending, inspecting or closing a local Plan through governance.plan",
    ),
    "aota-workspace-operations": (
        "Bounded workspace search/read/write plus test policy via aota.invoke",
        "use when you need to search, read, or write files, or know test policy",
    ),
    "aota-result-hydration": (
        "Selective hydration of prior by_ref results via aota.invoke",
        "use only when a prior aota.invoke returned by_ref and you need its content",
    ),
    "aota-restricted-shell": (
        "Residual restricted shell fallback via aota.invoke",
        "use only when specialized operations are insufficient, as residual fallback",
    ),
    "aota-pcf-project-steward": (
        "Steward Mode A ProjectState and Mode B governance closure",
        "use when you need ProjectState inspection or closure reconciliation as steward",
    ),
    "aota-multi-phase-doc-closure": (
        "Closure evidence and residual-risk reference",
        "use when you need closure evidence checklist or follow-up boundaries",
    ),
    "aota-evidence-first-debugging": (
        "Evidence-first diagnosis workflow",
        "use when you need bounded evidence gathering and hypothesis-driven diagnosis",
    ),
    "aota-implementation-review": (
        "Independent implementation review and verdicts",
        "use when you need to review implementation against acceptance independently",
    ),
    "aota-spec-driven-implementation": (
        "Spec-driven bounded implementation with validation integrity",
        "use when you need to implement bounded Work Item from frozen SPEC",
    ),
}

_ROLE_SKILL_DEFS: dict[str, tuple[list[str], list[str]]] = {
    "task-main": (
        ["aota-task-main-control"],
        [
            "aota-workspace-operations",
            "aota-result-hydration",
            "aota-task-main-governance",
            "aota-project-governance",
            "aota-plan-governance",
        ],
    ),
    "analyst": (
        ["aota-workspace-operations", "aota-evidence-first-debugging"],
        ["aota-result-hydration", "aota-restricted-shell"],
    ),
    "coder": (
        ["aota-workspace-operations", "aota-spec-driven-implementation"],
        ["aota-result-hydration", "aota-restricted-shell"],
    ),
    "reviewer": (
        ["aota-workspace-operations", "aota-implementation-review"],
        ["aota-result-hydration"],
    ),
    "project-steward": (
        ["aota-workspace-operations", "aota-pcf-project-steward"],
        ["aota-result-hydration", "aota-multi-phase-doc-closure"],
    ),
}

# Orphan disposition (deterministic, Role-contract rationale; orphan != defect).
# Known candidates + other existing Skills evaluated against accepted contracts.
ORPHAN_SKILL_DISPOSITION: dict[str, tuple[str, str]] = {
    "aota-pcf-project-steward": ("assign_eager", "steward compatibility execution vehicle for bounded Mode A/B residuals; rewritten to M1/M2 contract"),
    "aota-multi-phase-doc-closure": ("assign_progressive", "closure evidence reference for steward rare path; not eager"),
    "aota-evidence-first-debugging": ("assign_eager", "analyst bounded evidence/diagnosis is analyst normal path"),
    "aota-implementation-review": ("assign_eager", "independent review is reviewer normal path; verifies product effect"),
    "aota-spec-driven-implementation": ("assign_eager", "SPEC-driven workflow + validation integrity is coder normal path"),
    "aota-task-main-control": ("assign_eager", "single task-main Base Skill for the Governance 2.0 normal path"),
    "aota-task-main-governance": ("assign_progressive", "current task-main governance workflow; Governance 1.x is a bounded compatibility branch"),
    "aota-project-governance": ("assign_progressive", "Governance 2.0 Project intake is task-main-owned but on-demand; never eager"),
    "aota-plan-governance": ("assign_progressive", "Governance 2.0 local Plan intake is task-main-owned but on-demand; never eager"),
    "aota-workspace-operations": ("assign_eager", "search/read (+role-scoped write/test policy) is normal for analyst/coder/reviewer/steward"),
    "aota-restricted-shell": ("assign_progressive", "residual fallback only for analyst/coder conditional; never eager"),
    "aota-result-hydration": ("assign_progressive", "large-reference hydrate only; never normal Skill loading"),
    "aota-task-lifecycle": ("remain_unassigned", "bounded Profile Task runtime reference; not task-main normal lifecycle authority"),
    "aota-architecture-review": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-canonical-spec-contract": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-canonical-spec-pitfalls": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-plugin-tool-development": ("remain_unassigned", "plugin tool lifecycle not in accepted Role normal path"),
    "aota-profile-task-orchestration": ("remain_unassigned", "legacy Profile Task reference; not task-main normal orchestration or Plan authority"),
    "aota-skill-development": ("remain_unassigned", "skill authoring not in accepted Role normal path"),
    "aota-tool-failure-fallback": ("remain_unassigned", "generic fallback not in accepted Role normal path"),
    "aota-work-classify-and-plan-gate": ("remain_unassigned", "classification gate not in accepted Role normal path"),
    "aota-workspace-diagnostics": ("remain_unassigned", "diagnostics helper not in accepted Role normal path"),
    "aota-workspace-model": ("remain_unassigned", "workspace model helper not in accepted Role normal path"),
}

# ---------------------------------------------------------------------------
# Curated compact eager guidance (usable without skill.open, no truncation).
# Each entry is complete usable guidance for normal path (workflow, tool use,
# decision rules, stop/escalation, expected result). Bounded, no silent loss.
# ---------------------------------------------------------------------------

_CURATED_EAGER_GUIDANCE: dict[str, str] = {
    "aota-task-main-control": (
        "Task-main normal: understand intent, ensure local Project/Plan context through "
        "governance.project/governance.plan, read cards and exact refs only as needed, "
        "reason, then handoff.write(mode=work_item) and task.start(role, handoff_ref). "
        "Consume a compact result card and reason again; review frequency is semantic judgment, "
        "not a fixed runtime policy. submit_work_projection=compatibility only, not the normal path. "
        "Open aota-task-main-governance for the local Governance 2.0 workflow; its GitHub "
        "branch is compatibility-only. Keep the Human Brake, card-first retrieval, bounded scope, "
        "typed-error recovery and needs_input behavior. Dispatch workers via AF; workers start "
        "with role.bootstrap."
    ),
    "aota-workspace-operations": (
        "Via aota.invoke: bounded workspace.search and workspace.read. workspace.write creates or boundedly replaces whole files "
        "(create_only|replace_existing|create_or_replace). Coder-only workspace.edit makes one exact literal CAS edit; "
        "prefer it for small changes to existing large files. No shell/regex/generic patch/delete/move. "
        "test.run per Role Skill; lifecycle handoff.open → handoff.write(result) → task.return. "
        "Stop on AUTHORITY_DENIED/scope gaps; evidence != authority."
    ),
    "aota-evidence-first-debugging": (
        "Analyst diagnosis: state symptom (measurable), label fact/hypothesis/inference/confirmed, collect minimum evidence for leading hypothesis, prefer minimal reproduction, hypothesis-driven search only (no mass grep), "
        "classify source/config/runtime/live, need causal chain (suspicious code alone insufficient), confidence low/medium/high/confirmed. "
        "Read-only normal path (search/read); artifact-only writes when explicitly required by the Handoff. Output compact: symptom, evidence, cause+confidence, open questions, next action for coder. needs_input on missing inputs; never fix here."
    ),
    "aota-spec-driven-implementation": (
        "Coder workflow via aota.invoke: workspace.search/workspace.read; workspace.edit for exact CAS changes to existing files "
        "(prefer small edits to large files), workspace.write for new or bounded whole-file replacements. Stay in handoff scope; "
        "test.run all validation expectations; self-repair only with evidence. Finish handoff.write(result) → task.return "
        "(process exit is not completion). Stop/escalate on authority or scope gaps; open this Skill for procedure."
    ),
    "aota-implementation-review": (
        "Reviewer independent: acceptance is truth, coder result is evidence; verify product effect yourself. Keep the review bounded enough to finish "
        "inside the trusted Worker budget. Gather evidence with search/read; test.run only when the review handoff "
        "carries validation_expectations (server-decided). Map acceptance to change+evidence, emit a verdict "
        "(PASS|PASS_WITH_FINDINGS|NEEDS_FIX|BLOCKED|INCONCLUSIVE) with findings in the result payload, "
        "then task.return. Never write source or repair inside review. Full sizing/return contract: open this Skill."
    ),
    "aota-pcf-project-steward": (
        "Steward Mode A ProjectState: inspect identity/binding, existence, frontier, Plan/Milestone, defects, continuity, artifacts, freshness; never plans Work. "
        "Mode B: only after readiness (source-ready, reviewer, task-main done); StewardResult SYNCED/BLOCKED; accepted==reviewed; never set approval; finalizer scope never exceeded. "
        "Acceptance (reviewer evidence) != governance materialization (future finalizer Git/GitHub/CAS). Reviewer gives acceptance; task-main reconciles; user owns approval; steward reconciles governance; finalizer mutates (W2). "
        "Tools: search/read/hydrate. No write/shell/test/git. needs_input on gap; BLOCKED on defect."
    ),
}

def curated_eager_guidance(skill_id: str) -> str:
    try:
        return _CURATED_EAGER_GUIDANCE[skill_id]
    except KeyError:
        raise KeyError(f"no curated eager guidance for {skill_id!r}")

# ---------------------------------------------------------------------------
# AF #59 M2/W1-B/W1-C: thin task-main base guidance (routing table).
#
# ``role.bootstrap`` is the normal task-main startup guidance and delivers
# this text as the materialized eager content of ``aota-task-main-control``.
# It is guidance, never authority, never a Plan bind and never a session
# bind (SKILL_IS_AUTHORITY=no). It teaches the base workflow, the progressive
# Skill routing and the stop conditions; detailed procedures live in the
# progressive Skills.
# ---------------------------------------------------------------------------

THIN_TASK_MAIN_GUIDANCE_IS_ADVISORY = True
THIN_TASK_MAIN_GUIDANCE_IS_WORKFLOW_PRESCRIPTIVE = False
ROLE_BOOTSTRAP_IS_AUTHORITY = False
ROLE_BOOTSTRAP_BINDS_PLAN = False
ROLE_BOOTSTRAP_BINDS_SESSION = False
ROLE_BOOTSTRAP_MECHANICAL_GATE = False

# AF #59 M2 acceptance-polish (R1/R2): model-visible usage-contract
# disciplines. These are guidance flags only — no session/context engine, no
# cache and no new authority is introduced.
AMBIGUOUS_PLAN_NUMBER_IS_NOT_PLAN_REF = True
MISSING_PLAN_REF_FAST_STOP = True
SESSION_DIRECTORY_PROFILE_REPO_INFERENCE = False
UNNECESSARY_DISCOVERY_BEFORE_NEEDS_INPUT = False
KNOWN_CONTEXT_REUSE_FIRST = True

# Compact discovery-pointer bound for the thin materialized guidance. It stays
# well below the generic per-Skill bootstrap bound; a growing text means it is
# turning into a manual and must be moved into the base Skill instead.
THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS = 1600

THIN_TASK_MAIN_EAGER_GUIDANCE = (
    "AF task-main (unbound). aota-task-main-control eager; "
    "role.bootstrap/skill.open advisory, not authority. "
    "Session: role.bootstrap -> task_main.context(inspect) -> discuss unbound; "
    "governed execution -> task_main.context(bind, project_selector, optional "
    "plan_selector) -> same-session trusted bind; selectors locate, model mints "
    "no authority. "
    "Loop: intent -> Project/Plan -> cards/refs -> reason -> "
    "handoff.write(mode=work_item, payload.work_role+work_item_ref+milestone_ref) "
    "-> task.start(role=same, handoff_ref) -> result card -> reason; "
    "review frequency=semantic. Project/Plan -> governance.project / "
    "governance.plan; PLAN_ACCESS_VIA_WORKSPACE_READ=no. "
    "Context -> task_main.context(inspect|bind|switch); "
    "session/profile/model/locator never authority. "
    "WORKSPACE_ACCESS_VIA_ROOT_REF=yes: workspace.search/read use root_ref, never path. "
    "Governance -> open aota-task-main-governance@1.0.0; Gov2 normal; "
    "github.issue.read/comments.read legacy-only. "
    "by_ref -> result.hydrate; submit_work_projection=compatibility; "
    "args -> help(operation=...). "
    "A bare #N is not a plan_ref: ask for owner/repo#number "
    "(a GitHub Issue URL or owner/repo/issues/N path normalizes deterministically); "
    "plan_ref is an operation locator, never authority. never infer repo from "
    "host/runtime/workspace/cwd/profile/session/search. "
    "Reuse known context first: don't re-run role.bootstrap or reopen a Skill "
    "already in context (same ref/digest); candidate miss != search miss; "
    "continue with remaining refs; no repeat without freshness. "
    "AUTHORITY_DENIED -> stop; UNKNOWN_INPUT -> read Skill/help; "
    "missing approval/evidence -> needs_input."
)


def thin_task_main_eager_guidance() -> str:
    """Usable thin task-main base guidance (routing, not authority)."""
    if len(THIN_TASK_MAIN_EAGER_GUIDANCE) > THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS:
        raise ValueError(
            "thin task-main eager guidance exceeds "
            f"{THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS} chars; move detail to the base Skill"
        )
    return THIN_TASK_MAIN_EAGER_GUIDANCE


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _skill_file_path(skill_id: str) -> Path:
    return _project_root() / "skills" / skill_id / "SKILL.md"

def _skill_content_for(skill_id: str) -> str:
    p = _skill_file_path(skill_id)
    if not p.is_file():
        return f"# {skill_id}\nPlaceholder skill content for {skill_id}."
    return p.read_text(encoding="utf-8")

def _make_skill_identity(skill_id: str, version: str = "1.0.0") -> SkillIdentity:
    content = _skill_content_for(skill_id)
    digest = compute_skill_digest(content)
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance="aota_forge")

_REGISTRY_ENTRIES: list[SkillRegistryEntry] = []
_ALLOWED_UNIVERSES: dict[str, AllowedSkillUniverse] = {}
_SKILL_VERSION = "1.0.0"

def _build_registry_and_universes() -> None:
    global _REGISTRY_ENTRIES, _ALLOWED_UNIVERSES
    if _REGISTRY_ENTRIES and _ALLOWED_UNIVERSES:
        return
    entries: list[SkillRegistryEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for role, (eager, prog) in _ROLE_SKILL_DEFS.items():
        all_skills = list(eager) + list(prog)
        for sid in all_skills:
            key = (role, sid, _SKILL_VERSION)
            if key in seen:
                continue
            seen.add(key)
            ident = _make_skill_identity(sid, _SKILL_VERSION)
            content_ref = f"skills/{sid}/SKILL.md"
            entry = SkillRegistryEntry(namespace=role, identity=ident, content_ref=content_ref)
            entries.append(entry)
    registry = StaticSkillRegistry(entries)
    for role, (eager, prog) in _ROLE_SKILL_DEFS.items():
        allowed: list[AllowedSkill] = []
        for sid in list(eager) + list(prog):
            ref = f"{sid}@{_SKILL_VERSION}"
            allowed.append(AllowedSkill(ref=ref, namespace=role, skill_id=sid, version=_SKILL_VERSION))
        universe = AllowedSkillUniverse(allowed)
        _ALLOWED_UNIVERSES[role] = universe
    _REGISTRY_ENTRIES = entries
    globals()["AF_SKILL_REGISTRY"] = registry

_build_registry_and_universes()

AF_SKILL_REGISTRY: StaticSkillRegistry = globals()["AF_SKILL_REGISTRY"]
AF_ALLOWED_UNIVERSES: dict[str, AllowedSkillUniverse] = dict(_ALLOWED_UNIVERSES)

def get_allowed_universe_for_role(role: AgentWorkRole | str) -> AllowedSkillUniverse:
    if isinstance(role, AgentWorkRole):
        key = role.value
    else:
        from aota_forge.work_plane.roles import parse_agent_work_role
        key = parse_agent_work_role(role).value
    return AF_ALLOWED_UNIVERSES[key]

def get_skill_registry() -> StaticSkillRegistry:
    return AF_SKILL_REGISTRY

def skill_ref(skill_id: str, version: str = _SKILL_VERSION) -> str:
    return f"{skill_id}@{version}"

def progressive_skill_metadata(role: str) -> list[dict[str, str]]:
    _, prog = _ROLE_SKILL_DEFS[role]
    out = []
    for sid in prog:
        short_desc, use_when = _SKILL_META.get(sid, (sid, "use when needed"))
        ref = skill_ref(sid)
        entry = AF_SKILL_REGISTRY.get(role, sid, _SKILL_VERSION)
        digest = entry.identity.digest if entry else ""
        out.append({
            "skill_id": sid,
            "version": _SKILL_VERSION,
            "short_description": short_desc,
            "use_when": use_when,
            "ref": ref,
            "digest": digest,
            "provenance": "aota_forge",
        })
    return out

def eager_skill_refs_for_role(role: str) -> list[str]:
    eager, _ = _ROLE_SKILL_DEFS[role]
    return [skill_ref(s) for s in eager]

def progressive_skill_refs_for_role(role: str) -> list[str]:
    _, prog = _ROLE_SKILL_DEFS[role]
    return [skill_ref(s) for s in prog]

EXACT_AF_ROLE_COUNT = 5
AF_ROLE_COUNT = len(WORK_ROLES)
