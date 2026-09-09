"""AF Role Bootstrap — exclusive Role/Soul/Tool/Skill convergence (M1 W2).

Defines the five canonical AF roles with concise SOUL content,
minimal per-role Tool surfaces, and minimal per-role Skill universes.

Reuses existing contracts only:
- AgentWorkRole / Soul / TaskHandoff / ToolRoleSurface
- SkillIdentity / StaticSkillRegistry / AllowedSkillUniverse
- SkillResolutionResult / SkillBootstrapProjection / open_skill
- compose_skill_bootstrap / BootstrapBudget / BootstrapBundle

Invariants:
- AF_ROLE_SOURCE=exclusive, AF_ROLE_SOUL_SOURCE=exclusive, AF_ROLE_TOOL_AUTHORITY=exclusive, AF_ROLE_SKILL_SOURCE=exclusive
- SOUL_IS_AUTHORITY=no, SKILL_IS_AUTHORITY=no, TOOL_VISIBILITY_IS_AUTHORITY=no
- Global bundle not created; per-role minimal universe only.
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
# Soul catalog — load from AF-owned markdown files (aota_forge/roles/<role>.md)
# ---------------------------------------------------------------------------

def _roles_dir() -> Path:
    # af_roles.py lives at aota_forge/work_plane/af_roles.py -> project packages at aota_forge/
    # roles are at aota_forge/roles/<role>.md
    return Path(__file__).resolve().parents[1] / "roles"

def _load_soul_content(role: str) -> str:
    p = _roles_dir() / f"{role}.md"
    if not p.is_file():
        raise FileNotFoundError(f"AF soul content missing for role {role!r} at {p}")
    txt = p.read_text(encoding="utf-8").strip()
    if not txt:
        raise ValueError(f"AF soul content empty for role {role!r}")
    # ensure bounded via Soul validation later
    return txt

# Cache souls
_SOUL_CACHE: dict[str, Soul] = {}
_SOUL_VERSION = "1.0.0"

def get_soul_for_role(role: AgentWorkRole | str) -> Soul:
    if isinstance(role, AgentWorkRole):
        key = role.value
    else:
        # validate via AgentWorkRole
        from aota_forge.work_plane.roles import parse_agent_work_role
        key = parse_agent_work_role(role).value
    if key in _SOUL_CACHE:
        return _SOUL_CACHE[key]
    content = _load_soul_content(key)
    soul = Soul(content=content, version=_SOUL_VERSION)
    _SOUL_CACHE[key] = soul
    return soul

# Eager export of five souls for deterministic tests
AF_SOULS: dict[str, Soul] = {r: get_soul_for_role(r) for r in WORK_ROLES}

# ---------------------------------------------------------------------------
# Tool surfaces — minimal per-role (visibility != authority)
# ---------------------------------------------------------------------------

# Shared constants for tool ops
WORKSPACE_OPS = ("workspace.search", "workspace.read", "workspace.write")
RESULT_HYDRATE = "result.hydrate"
RESTRICTED_SHELL = "restricted_shell.run"
TEST_RUN_OP = "test.run"
TASK_MAIN_OPS = ("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once")
ROLE_BOOTSTRAP_OP = "role.bootstrap"
SKILL_OPEN_OP = "skill.open"

# Per-role minimal tool surfaces
_TOOL_SURFACES: dict[str, ToolRoleSurface] = {}

def _build_tool_surfaces() -> None:
    if _TOOL_SURFACES:
        return
    # task-main: eager task_main control only, progressive result hydration
    _TOOL_SURFACES["task-main"] = create_role_tool_surface(
        "task-main",
        eager=list(TASK_MAIN_OPS),
        progressive=[RESULT_HYDRATE],
    )
    # analyst: eager workspace ops, progressive result hydrate + restricted shell
    _TOOL_SURFACES["analyst"] = create_role_tool_surface(
        "analyst",
        eager=list(WORKSPACE_OPS),
        progressive=[RESULT_HYDRATE, RESTRICTED_SHELL],
    )
    # coder: eager workspace ops, progressive hydrate + shell + test.run
    _TOOL_SURFACES["coder"] = create_role_tool_surface(
        "coder",
        eager=list(WORKSPACE_OPS),
        progressive=[RESULT_HYDRATE, RESTRICTED_SHELL, TEST_RUN_OP],
    )
    # reviewer: eager workspace ops + eager test.run visibility, progressive
    # hydrate only (no shell). test.run visibility is eager but server
    # authorization stays conditional (M2/W2 reviewer independent
    # validation: eager_visible+conditionally_authorized). Visibility !=
    # authority; binding authority is granted only when review evidence
    # requires it.
    _TOOL_SURFACES["reviewer"] = create_role_tool_surface(
        "reviewer",
        eager=list(WORKSPACE_OPS) + [TEST_RUN_OP],
        progressive=[RESULT_HYDRATE],
    )
    # project-steward: eager workspace ops, progressive hydrate only
    _TOOL_SURFACES["project-steward"] = create_role_tool_surface(
        "project-steward",
        eager=list(WORKSPACE_OPS),
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
# Skill catalog — minimal per-role skill universe (exclusive AF source)
# ---------------------------------------------------------------------------

# Skill metadata: skill_id -> (short_description, use_when)
_SKILL_META: dict[str, tuple[str, str]] = {
    "aota-task-main-control": (
        "Task-main milestone control via aota.invoke",
        "use when you need to activate, recover, or advance milestone as task-main",
    ),
    "aota-workspace-operations": (
        "Bounded workspace search/read/write via aota.invoke",
        "use when you need to search, read, or write files in the worktree",
    ),
    "aota-result-hydration": (
        "Durable result hydration via aota.invoke",
        "use when you need to hydrate a large governed result reference",
    ),
    "aota-restricted-shell": (
        "Residual restricted shell fallback via aota.invoke",
        "use only when specialized operations are insufficient, as residual fallback",
    ),
    "aota-pcf-project-steward": (
        "Project closure and stewardship checks",
        "use when you need to perform milestone closure or stewardship verification",
    ),
    "aota-multi-phase-doc-closure": (
        "Multi-phase documentation closure",
        "use when you need to close or reconcile documentation phases",
    ),
    "aota-evidence-first-debugging": (
        "Evidence-first debugging workflow",
        "use when you need bounded evidence gathering for debugging",
    ),
    "aota-implementation-review": (
        "Implementation review checks",
        "use when you need to review implementation against acceptance",
    ),
}

# Per-role eager vs progressive skill sets (minimal, not global bundle)
# Format: role -> (eager list, progressive list)
_ROLE_SKILL_DEFS: dict[str, tuple[list[str], list[str]]] = {
    "task-main": (
        ["aota-task-main-control"],
        ["aota-workspace-operations", "aota-result-hydration"],
    ),
    "analyst": (
        ["aota-workspace-operations"],
        ["aota-result-hydration", "aota-restricted-shell"],
    ),
    "coder": (
        ["aota-workspace-operations", "aota-result-hydration"],
        ["aota-restricted-shell"],
    ),
    "reviewer": (
        ["aota-workspace-operations"],
        ["aota-result-hydration"],
    ),
    "project-steward": (
        ["aota-workspace-operations"],
        ["aota-result-hydration"],
    ),
}

# Resolve project root for skill file reading (for digest)
def _project_root() -> Path:
    # af_roles.py at aota_forge/work_plane/ -> parents[2] = project root (contains .aota and skills)
    return Path(__file__).resolve().parents[2]

def _skill_file_path(skill_id: str) -> Path:
    return _project_root() / "skills" / skill_id / "SKILL.md"

def _skill_content_for(skill_id: str) -> str:
    p = _skill_file_path(skill_id)
    if not p.is_file():
        # fallback: minimal placeholder if file missing in some test worktree
        return f"# {skill_id}\nPlaceholder skill content for {skill_id}."
    return p.read_text(encoding="utf-8")

def _make_skill_identity(skill_id: str, version: str = "1.0.0") -> SkillIdentity:
    content = _skill_content_for(skill_id)
    digest = compute_skill_digest(content)
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance="aota_forge")

# Build registry entries: one entry per skill_id per role that needs it, namespace-scoped
_REGISTRY_ENTRIES: list[SkillRegistryEntry] = []
_ALLOWED_UNIVERSES: dict[str, AllowedSkillUniverse] = {}
_SKILL_VERSION = "1.0.0"

def _build_registry_and_universes() -> None:
    global _REGISTRY_ENTRIES, _ALLOWED_UNIVERSES
    if _REGISTRY_ENTRIES and _ALLOWED_UNIVERSES:
        return
    entries: list[SkillRegistryEntry] = []
    # collect unique (namespace, skill_id, version) keys
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
            # validate content_ref bounds via SkillRegistryEntry (will fail if invalid)
            entry = SkillRegistryEntry(namespace=role, identity=ident, content_ref=content_ref)
            entries.append(entry)
    registry = StaticSkillRegistry(entries)
    # Build AllowedSkillUniverse per role
    for role, (eager, prog) in _ROLE_SKILL_DEFS.items():
        allowed: list[AllowedSkill] = []
        for sid in list(eager) + list(prog):
            # opaque logical ref: skill_id@version
            ref = f"{sid}@{_SKILL_VERSION}"
            # validate namespace via AllowedSkill
            allowed.append(AllowedSkill(ref=ref, namespace=role, skill_id=sid, version=_SKILL_VERSION))
        # also ensure deterministic ordering via AllowedSkillUniverse
        universe = AllowedSkillUniverse(allowed)
        _ALLOWED_UNIVERSES[role] = universe
    # store
    _REGISTRY_ENTRIES = entries
    # expose global registry
    globals()["AF_SKILL_REGISTRY"] = registry

_build_registry_and_universes()

# Export after build
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

# Helper: get opaque logical ref for skill
def skill_ref(skill_id: str, version: str = _SKILL_VERSION) -> str:
    return f"{skill_id}@{version}"

# Progressive metadata helper
def progressive_skill_metadata(role: str) -> list[dict[str, str]]:
    _, prog = _ROLE_SKILL_DEFS[role]
    out = []
    for sid in prog:
        short_desc, use_when = _SKILL_META.get(sid, (sid, "use when needed"))
        ref = skill_ref(sid)
        # find entry to get digest
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

# Validation helpers for tests
EXACT_AF_ROLE_COUNT = 5
AF_ROLE_COUNT = len(WORK_ROLES)
