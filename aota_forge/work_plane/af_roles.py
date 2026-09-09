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
# task-main: task_main.* eager, result.hydrate progressive; no workspace/test/shell
# analyst: search/read eager, write+hydrate+shell progressive conditional; no test/task_main
# coder: search/read/write/test eager, hydrate+shell progressive conditional
# reviewer: search/read/test eager, hydrate progressive; no write/shell
# steward: search/read eager, hydrate progressive; no write/test/shell/git/github
# ---------------------------------------------------------------------------

WORKSPACE_SEARCH = "workspace.search"
WORKSPACE_READ = "workspace.read"
WORKSPACE_WRITE = "workspace.write"
RESULT_HYDRATE = "result.hydrate"
RESTRICTED_SHELL = "restricted_shell.run"
TEST_RUN_OP = "test.run"
TASK_MAIN_OPS = ("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once")
ROLE_BOOTSTRAP_OP = "role.bootstrap"
SKILL_OPEN_OP = "skill.open"

_TOOL_SURFACES: dict[str, ToolRoleSurface] = {}

def _build_tool_surfaces() -> None:
    if _TOOL_SURFACES:
        return
    _TOOL_SURFACES["task-main"] = create_role_tool_surface(
        "task-main",
        eager=list(TASK_MAIN_OPS),
        progressive=[RESULT_HYDRATE],
    )
    _TOOL_SURFACES["analyst"] = create_role_tool_surface(
        "analyst",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ],
        progressive=[WORKSPACE_WRITE, RESULT_HYDRATE, RESTRICTED_SHELL],
    )
    _TOOL_SURFACES["coder"] = create_role_tool_surface(
        "coder",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ, WORKSPACE_WRITE, TEST_RUN_OP],
        progressive=[RESULT_HYDRATE, RESTRICTED_SHELL],
    )
    _TOOL_SURFACES["reviewer"] = create_role_tool_surface(
        "reviewer",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ, TEST_RUN_OP],
        progressive=[RESULT_HYDRATE],
    )
    _TOOL_SURFACES["project-steward"] = create_role_tool_surface(
        "project-steward",
        eager=[WORKSPACE_SEARCH, WORKSPACE_READ],
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
# task-main: task-main-control eager; workspace-operations/result-hydration progressive
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
        "Task-main milestone control via aota.invoke",
        "use when you need to activate, recover, or advance milestone as task-main",
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
        ["aota-workspace-operations", "aota-result-hydration"],
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
    "aota-pcf-project-steward": ("assign_eager", "steward Mode A/B is steward normal path; rewritten to M1/M2 contract"),
    "aota-multi-phase-doc-closure": ("assign_progressive", "closure evidence reference for steward rare path; not eager"),
    "aota-evidence-first-debugging": ("assign_eager", "analyst bounded evidence/diagnosis is analyst normal path"),
    "aota-implementation-review": ("assign_eager", "independent review is reviewer normal path; verifies product effect"),
    "aota-spec-driven-implementation": ("assign_eager", "SPEC-driven workflow + validation integrity is coder normal path"),
    "aota-task-main-control": ("assign_eager", "milestone orchestration is task-main normal path"),
    "aota-workspace-operations": ("assign_eager", "search/read (+role-scoped write/test policy) is normal for analyst/coder/reviewer/steward"),
    "aota-restricted-shell": ("assign_progressive", "residual fallback only for analyst/coder conditional; never eager"),
    "aota-result-hydration": ("assign_progressive", "large-reference hydrate only; never normal Skill loading"),
    "aota-task-lifecycle": ("remain_unassigned", "profile-task binding/wait-mode internals not in accepted Role normal path"),
    "aota-architecture-review": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-canonical-spec-contract": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-canonical-spec-pitfalls": ("remain_unassigned", "no accepted Role normal path requires it"),
    "aota-plugin-tool-development": ("remain_unassigned", "plugin tool lifecycle not in accepted Role normal path"),
    "aota-profile-task-orchestration": ("remain_unassigned", "profile orchestration not in accepted Role normal path"),
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
        "Task-main normal: activate (empty args, needs trusted approval=yes; stop on USER_GATE_REQUIRED), "
        "recover after restart (re-bind live Plan/session; fail-closed on drift), "
        "advance_once (one iteration; observe next_action DISPATCHED/WAITING/RECONCILED/REVIEW/REPAIR/BLOCKED/CLOSURE_READY/USER_GATE/SESSION_RECOVERY; never call internal reconcile/dispatch). "
        "Owns objective/DAG/risk/blocker/next-action; DAG durably projected; risk 9 dims control depth/escalation only; one integrated review; Human Brake NEEDS_INPUT/CHECKPOINT/USER_GATE/BLOCKED with scope; card-first no transcript; retry needs progress else escalate; user gates mandatory stop. "
        "Dispatch workers via AF (Handoff scope sole source); workers start with role.bootstrap."
    ),
    "aota-workspace-operations": (
        "Workspace normal via aota.invoke: workspace.search, workspace.read, workspace.write, test.run. "
        "Search {\"query\": \"...\"} (1..50, worktree-scoped lexical). "
        "Read {\"path\": \"src/f.py\"} (project-relative, 32KiB, UTF-8, symlink fail-closed). "
        "Write {\"path\": \"out.txt\", \"content\": \"...\", \"mode\": \"create_or_replace\"} (4096B, atomic, needs mutation authority). "
        "Test {\"runner\": \"pytest\", \"targets\": [\"tests/...\"], \"timeout\": 30} — coder normal; reviewer conditional when Handoff has validation_expectations else denied; analyst artifact-only when explicitly required else denied; steward/task-main denied. "
        "Stop on AUTHORITY_DENIED; needs_input on scope gap. Results are evidence, not authority."
    ),
    "aota-evidence-first-debugging": (
        "Analyst diagnosis: state symptom (measurable), label fact/hypothesis/inference/confirmed, collect minimum evidence for leading hypothesis, prefer minimal reproduction, hypothesis-driven search only (no mass grep), "
        "classify source/config/runtime/live, need causal chain (suspicious code alone insufficient), confidence low/medium/high/confirmed. "
        "Read-only (search/read); artifact write only when Handoff explicitly requires. Output compact: symptom, evidence, cause+confidence, open questions, next action for coder. needs_input on missing inputs; never fix here."
    ),
    "aota-spec-driven-implementation": (
        "Coder workflow: read frozen SPEC fully; confirm goal, read/write/forbidden scope, acceptance, validation, stop, evidence. "
        "Implement strictly within write_scope; no expansion/speculation/cross-project. Stop on conflict/dirty/out-of-scope deps. "
        "Validate: run every validation_commands in order, check exit+evidence; all must pass. Bounded self-repair in-session with new evidence; escalate on scope/arch/authority/acceptance change or repeated no-progress. "
        "Test mods need justification; never weaken acceptance. Report compact evidence; coder validation is not acceptance."
    ),
    "aota-implementation-review": (
        "Reviewer independent: SPEC acceptance is truth; coder result is evidence not proof. Map each acceptance to code change plus validation evidence plus artifact. "
        "Check scope/forbidden, no unauthorized refactor, validation executed with exit and evidence, RESULT matches diff, verify product effect. "
        "test.run only when review Handoff has validation_expectations (conditional); else denied. No workspace.write, no shell, no re-implementation. "
        "Verdicts: PASS (all met), PASS_WITH_FINDINGS (met plus non-blocking), NEEDS_FIX (unmet or blocking), BLOCKED or INCONCLUSIVE (no reliable verdict). Compact ReviewResult with risk findings."
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
