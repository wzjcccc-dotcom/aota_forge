"""Production task-main daily launcher — operator-facing composition seam (M3 RV2 B003).

This is the thinnest production operator composition entry that
productionizes the already-proven W2/W3 seams. It does NOT become:

  - a fourth task_main.* operation
  - a model-facing control
  - a workflow engine
  - a second authority engine
  - a deployment subsystem

Agent-facing controls remain exactly 3:

  task_main.activate_milestone
  task_main.recover_coordinator
  task_main.advance_once

Responsibility (orchestration/composition only):

  trusted operator input (explicit Plan authority config + worktree identity)
  ↓
  resolve project/worktree
  ↓
  read authoritative live Plan via selected PlanAuthorityReadAdapter
  ↓
  normalize via canonical Plan layer (normalize_portable_plan)
  ↓
  project typed Milestone views via canonical executor-neutral projection
  ↓
  verify current user approval truth (from canonical projection, not launcher inference)
  ↓
  resolve RuntimeConfig (operator authority)
  ↓
  open durable task-main/execution stores
  ↓
  materialize trusted bootstrap (operator-controlled, 0600, digest-bound)
  ↓
  startup prompt resolution (operator-owned ~/.config/aota-forge/task-main-startup.md, bounded, non-authoritative; missing => fail closed, never silent seed fallback)
  ↓
  construct MCP child environment (production path, no pre-exported test env)
  ↓
  launch or resume Hermes profile=aota-task-main

It must NOT decide task-main semantic next actions, interpret Plan text,
or infer DAG/approval/entry-base. After Hermes starts:

  Hermes Agent → aota.invoke → AF runner

still owns semantic progression.

Required invariants enforced here:

  PYTHON_OPERATOR_SEMANTIC_CONTROL_CALLS=0
  EXISTING_PLAN_AUTHORITY_BOUNDARY_REUSED=yes
  NEW_PLAN_AUTHORITY_SYSTEM_CREATED=no
  LIVE_PLAN_TRUTH_REVALIDATED=yes
  USER_APPROVAL_FRESHNESS_REVALIDATED=yes
  STALE_BOOTSTRAP_CANNOT_OVERRIDE_CURRENT_APPROVAL=yes
  PRODUCTION_TASK_MAIN_BOOTSTRAP_MATERIALIZER=write_bootstrap_file
  BOOTSTRAP_CREATION_DEPENDS_ON_TEST_HARNESS=no
  MODEL_SUPPLIES_TRUSTED_BOOTSTRAP_INPUT=no
  TASK_MAIN_REQUIRED_ENV_SUPPLIED_BY_PRODUCTION_PATH=yes
  NORMAL_DAILY_LAUNCH_DOES_NOT_REQUIRE_PREEXPORTED_TEST_ENV=yes
  RUNTIME_CONFIG_AUTHORITY=operator_owned
  OPERATOR_MANUAL_INTERNAL_OBJECT_ASSEMBLY_REQUIRED=no
  OPERATOR_MANUAL_SESSION_BOOTSTRAP_REQUIRED=no
  PLAN_AUTHORITY_OPERATOR_SELECTABLE=yes
  PLAN_AUTHORITY_HARDCODED_TO_ISSUE_37=no
  ENTRY_BASE_FROM_PLAN_AUTHORITY=yes
  ENTRY_BASE_HARDCODED_TO_ISSUE37=no
  MILESTONE_GRAPH_FROM_PLAN_AUTHORITY=yes
  HARDCODED_M3_GRAPH_IS_AUTHORITY=no
  DAILY_LAUNCHER_CREATES_SECOND_PLAN_SEMANTICS=no
  LAUNCHER_PLAN_INTERPRETATION_LOGIC=none

Reuse:
  PlanAuthorityReadAdapter / PlanAuthoritySnapshot
  normalize_portable_plan / portable_plan_digest
  project_milestone_views (canonical Plan projection)
  RuntimeConfig / load_runtime_config
  FileBackedTaskMainCoordinatorStore / FileBackedExecutionStateStore
  write_bootstrap_file / try_build_task_main_binding
  HermesExactSessionReentry

This seam is operator-facing, not Agent-facing. It may be invoked via
python -m aota_forge.composition.task_main_daily_launcher or via the
composition API below.  Plan authority selection is trusted operator
configuration, never model input.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aota_forge.adapters.plan_authority import PlanAuthorityReadAdapter, PlanAuthoritySnapshot
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    BOOTSTRAP_RELPATH,
    write_bootstrap_file,
)
from aota_forge.core.execution.durable_state import (
    UNBOUND_ORIGIN_SESSION_REF_PREFIX,
    is_bound_origin_session_ref,
)
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.runtime.config import RuntimeConfig, load_runtime_config
from aota_forge.runtime.task_main.coordinator import MilestonePlanView

# ---------------------------------------------------------------------------
# Task-main startup prompt surface (M1/W1 — Operator RuntimeConfig & User Startup Surface)
# ---------------------------------------------------------------------------
# AF source seed (SEED_ONLY, never runtime authority):
#   prompts/task-main-startup.default.md   (repo root)
# Effective operator surface (OPERATOR_OWNED, operator-editable):
#   ~/.config/aota-forge/task-main-startup.md
#
# Invariants:
#   TASK_MAIN_STARTUP_PROMPT_OPERATOR_OWNED=yes
#   TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY=no
#   AF_ROLE_AUTHORITY_FROM_STARTUP_PROMPT=no
#   PLAN_AUTHORITY_FROM_STARTUP_PROMPT=no
#   SOURCE_SEED_SILENT_RUNTIME_FALLBACK=no
#   TASK_MAIN_STARTUP_PROMPT_BOUNDED=yes (≤8 KiB)
#
# Production launcher relationship:
#   AF source seed --(operator materialization)--> ~/.config/aota-forge/task-main-startup.md --(DailyTaskMainLauncher)--> Hermes task-main
# Effective operator file missing => fail closed / preflight not ready, never silent fallback to seed.


OPERATOR_STARTUP_PROMPT_PATH: Path = Path.home() / ".config" / "aota-forge" / "task-main-startup.md"
SEED_STARTUP_PROMPT_PATH: Path = Path(__file__).resolve().parents[2] / "prompts" / "task-main-startup.default.md"
MAX_STARTUP_PROMPT_BYTES = 8 * 1024
MAX_STARTUP_PROMPT_CHARS = 8 * 1024

TASK_MAIN_STARTUP_PROMPT_OPERATOR_OWNED = "yes"
TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY = "no"
AF_ROLE_AUTHORITY_FROM_STARTUP_PROMPT = "no"
PLAN_AUTHORITY_FROM_STARTUP_PROMPT = "no"
SOURCE_SEED_SILENT_RUNTIME_FALLBACK = "no"
TASK_MAIN_STARTUP_PROMPT_BOUNDED = "yes"

# ---------------------------------------------------------------------------
# Env classification — task-main authority only (M1/W2-R1 I45-B001 repair)
# ---------------------------------------------------------------------------

# Each required env is classified here. The launcher itself is the PRODUCER
# for TASK_MAIN_REQUIRED vars, so the operator never pre-exports them.
#
# Authority channel separation (TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION=yes):
#   task-main process env = task-main authority channels only
#   Worker process env    = Worker authority channels only
#
# task-main authority channels (trusted task-main path only):
#   task-main bootstrap (AOTA_TASK_MAIN_BOOTSTRAP)
#   trusted runtime config (AOTA_FORGE_RUNTIME_CONFIG)
#   repo/python mechanics (PYTHONPATH, AOTA_FORGE_REPO_ROOT)
#   task-main trace (AOTA_TASK_MAIN_TRACE, optional, never authority)
#
# Worker authority channels (NEVER present in task-main launch env):
#   serialized TaskHandoff (AOTA_W3_HANDOFF_JSON)
#   canonical Worker task id (AOTA_W3_TASK_ID)
#   Worker worktree/project binding (AOTA_W3_MCP_ROOT/PROJECT_ID/WORKTREE_ID)
#   Worker context-kind assertion (AOTA_W3_CONTEXT_KIND)
#
# ENV_NAME | PRODUCER | SOURCE_OF_TRUTH | TASK_MAIN_REQUIRED | WORKER_REQUIRED
# ---------------------------------------------------------------------------
# PYTHONPATH                       | launcher | repo root (aota_forge parent)             | yes | yes (mechanical copy)
# AOTA_FORGE_REPO_ROOT             | launcher | repo root                                | yes | yes (mechanical copy)
# AOTA_FORGE_RUNTIME_CONFIG        | operator/launcher | runtime_config_path (operator)  | yes | yes (mechanical copy)
# AOTA_TASK_MAIN_BOOTSTRAP         | launcher | worktree_root/.aota/task-main-bootstrap.json | yes | no (must-clear)
# AOTA_TASK_MAIN_TRACE             | launcher | trace path (optional)                     | no | no
# AOTA_W3_MCP_ROOT                 | NEVER in task-main env | Worker binding only         | no | yes
# AOTA_W3_PROJECT_ID               | NEVER in task-main env | Worker binding only         | no | yes
# AOTA_W3_WORKTREE_ID              | NEVER in task-main env | Worker binding only         | no | yes
# AOTA_W3_TASK_ID                  | NEVER in task-main env | Worker binding only         | no | yes
# AOTA_W3_HANDOFF_JSON             | NEVER in task-main env | Worker binding only         | no | yes
# AOTA_W3_TOOL_TRACE               | NEVER in task-main env | Worker trace only           | no | no
# AOTA_W3_CONTEXT_KIND             | NEVER in task-main env | Worker assertion only       | no | yes
#
# TASK_MAIN_REQUIRED_ENV_SUPPLIED_BY_PRODUCTION_PATH=yes
# NORMAL_DAILY_LAUNCH_DOES_NOT_REQUIRE_PREEXPORTED_TEST_ENV=yes
# TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_HANDOFF=no
# TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_AUTHORITY_KEYS=no
# TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF=no

TASK_MAIN_REQUIRED_ENV: tuple[str, ...] = (
    "PYTHONPATH",
    "AOTA_FORGE_REPO_ROOT",
    "AOTA_FORGE_RUNTIME_CONFIG",
    "AOTA_TASK_MAIN_BOOTSTRAP",
)

# Marker for §45 acceptance
TASK_MAIN_REQUIRED_ENV_PRODUCER = "aota_forge/composition/task_main_daily_launcher.py:DailyTaskMainLauncher"
PRODUCTION_TASK_MAIN_BOOTSTRAP_MATERIALIZER = "aota_forge/composition/task_main_host_bootstrap.py:write_bootstrap_file"
BOOTSTRAP_CREATION_DEPENDS_ON_TEST_HARNESS = "no"
EXISTING_PLAN_AUTHORITY_BOUNDARY_REUSED = "yes"
NEW_PLAN_AUTHORITY_SYSTEM_CREATED = "no"

# M1/W2-R1 task-main launcher context separation (I45-B001 repair).
# The task-main launcher must stop emitting Worker-binding data merely as a
# placeholder. Task-main identity comes from the already-established trusted
# task-main path (TrustedTaskMainRuntimeContext via task-main bootstrap +
# trusted project/worktree/Plan/runtime binding), not from a pseudo-Worker
# handoff. Production normal path must not emit it.
TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_HANDOFF = False
TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_AUTHORITY_KEYS = False
TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF = False
TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION = True
# No variable masquerades as both unless explicitly proven
# mechanical/non-authoritative (PYTHONPATH/AOTA_FORGE_REPO_ROOT/
# AOTA_FORGE_RUNTIME_CONFIG/PATH are mechanics, never role authority).
TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED = False

# M1/W1-R1 task-main-owned Work projection authority (AF #45 repair).
# The launcher/control plane supplies only trusted mechanical inputs
# (project binding, plan identity/source, user-gate, runtime config, trusted
# source locator / Plan snapshot reference). It must NOT normally supply the
# already-interpreted WorkSemanticProjection on task-main's behalf.
# prepare(work_semantics=...) is retained ONLY as test/bootstrap compatibility;
# production normal operation succeeds without it because task-main commits its
# bounded projection to the existing durable coordinator store.
PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED = False
OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
CODEX_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
MANUAL_PER_WORK_SCOPE_INJECTION_REQUIRED = False
OPERATOR_REFRESH_REQUIRED_BETWEEN_WORK_ITEMS = False
TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION = True
TASK_MAIN_SEMANTIC_LAYER_PRODUCES_WORK_PROJECTION = True
WORK_PROJECTION_DURABLE = True
TASK_MAIN_RESTART_REQUIRES_OPERATOR_WORK_SEMANTICS_REINJECTION = False

# No Python semantic control calls — the launcher never calls these:
#   activate_milestone(), advance_milestone_once(), runner.advance_once(),
#   reconcile_worker_completion(), etc.
# The real Agent still calls aota.invoke.
PRODUCTION_LAUNCHER_SEMANTIC_CONTROL_CALLS = 0

# Canonical Plan authority is operator-selectable, not hardcoded.
PLAN_AUTHORITY_OPERATOR_SELECTABLE = "yes"
PLAN_AUTHORITY_HARDCODED_TO_ISSUE_37 = "no"
GITHUB_PLATFORM_KNOWLEDGE_ADAPTER_PRIVATE = "yes"
MODEL_CAN_SELECT_PLAN_AUTHORITY = "no"


# ---------------------------------------------------------------------------
# Startup prompt helpers — operator-owned, bounded, non-authoritative
# ---------------------------------------------------------------------------

def _read_operator_startup_prompt(operator_path: Path | None = None) -> str:
    """Read effective operator task-main startup prompt, fail closed if absent.

    Production launcher relationship:
      AF source seed --(operator materialization)--> OPERATOR_STARTUP_PROMPT_PATH --(launcher)--> Hermes task-main

    This helper NEVER falls back to SEED_STARTUP_PROMPT_PATH silently.
    If the effective operator file is missing, it raises (fail-closed / preflight not ready).
    The seed is only for explicit materialization via ``materialize_operator_startup_from_seed()``
    or install/setup guidance and tests.

    Bounded: ≤ MAX_STARTUP_PROMPT_BYTES / chars, non-empty, must contain
    the minimal AF bootstrap reference (aota.invoke + role.bootstrap).
    """
    raw = Path(operator_path) if operator_path is not None else OPERATOR_STARTUP_PROMPT_PATH
    if raw.is_symlink():
        raise RuntimeError(f"operator startup prompt is a symlink (rejected fail-closed): {raw}")
    try:
        path = raw.resolve()
    except Exception:
        path = Path(str(raw))
    if not path.is_file():
        raise RuntimeError(
            f"operator task-main startup prompt missing: {path} (fail-closed, no silent fallback to seed {SEED_STARTUP_PROMPT_PATH}; "
            f"operator must materialize effective file via explicit seed copy: cp {SEED_STARTUP_PROMPT_PATH} {path})"
        )
    try:
        size = path.stat().st_size
        if size > MAX_STARTUP_PROMPT_BYTES:
            raise RuntimeError(f"operator startup prompt too large: {path} ({size} > {MAX_STARTUP_PROMPT_BYTES} bytes)")
        text = path.read_text(encoding="utf-8")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"operator startup prompt unreadable: {type(exc).__name__}: {exc}") from exc
    if not text.strip():
        raise RuntimeError(f"operator startup prompt is empty: {path}")
    if len(text) > MAX_STARTUP_PROMPT_CHARS:
        raise RuntimeError(f"operator startup prompt exceeds char bound: {len(text)} > {MAX_STARTUP_PROMPT_CHARS}")
    if len(text.encode("utf-8")) > MAX_STARTUP_PROMPT_BYTES:
        raise RuntimeError(f"operator startup prompt exceeds byte bound: {path}")
    lowered = text.lower()
    if "aota.invoke" not in lowered or "role.bootstrap" not in lowered:
        raise RuntimeError(
            f"operator startup prompt does not contain required AF bootstrap reference "
            f"(must mention aota.invoke and role.bootstrap): {path}"
        )
    # Guard against prompt becoming authority: it must remain minimal and not embed
    # full Plan body, operation catalog, or other control-plane material.
    # We keep the check bounded: prompt must not be excessively large (already bounded)
    # and must not contain obvious authority markers as literal strings.
    # This is a cheap bounded heuristic, not a full semantic review.
    forbidden_markers = [
        "PLAN_TYPE=portable_plan",
        "OPERATION_CATALOG",
        "FULL_PLAN_BODY",
    ]
    for marker in forbidden_markers:
        if marker in text:
            raise RuntimeError(f"operator startup prompt must not contain authority marker {marker!r}: {path}")
    return text


def _resolve_task_main_startup_prompt(initial_prompt: str | None) -> str:
    """Resolve task-main initial prompt for Hermes launch.

    If ``initial_prompt`` is explicitly supplied (non-empty), use it directly
    (tests / operator override path) but still enforce bounded size.

    Otherwise, load the effective operator file via ``_read_operator_startup_prompt()``
    — fail closed if missing, never silently use the repository seed.
    """
    if initial_prompt is not None:
        cleaned = initial_prompt.strip()
        if cleaned:
            if len(cleaned) > MAX_STARTUP_PROMPT_CHARS:
                raise ValueError(f"initial_prompt exceeds char bound: {len(cleaned)} > {MAX_STARTUP_PROMPT_CHARS}")
            if len(cleaned.encode("utf-8")) > MAX_STARTUP_PROMPT_BYTES:
                raise ValueError(f"initial_prompt exceeds byte bound")
            return cleaned
    return _read_operator_startup_prompt()


def materialize_operator_startup_from_seed(
    operator_path: Path | None = None,
    seed_path: Path | None = None,
) -> Path:
    """Explicitly materialize the operator startup file from the AF source seed.

    This is the ONLY sanctioned path for the seed to become the effective
    operator prompt: an explicit operator action (install/setup or test helper).
    Production ``_read_operator_startup_prompt`` / ``_resolve_task_main_startup_prompt``
    never calls this automatically.
    """
    op = (operator_path or OPERATOR_STARTUP_PROMPT_PATH)
    seed = (seed_path or SEED_STARTUP_PROMPT_PATH)
    # Resolve without following symlink for seed check (fail closed on symlink)
    if seed.is_symlink():
        raise RuntimeError(f"seed startup prompt is a symlink (rejected): {seed}")
    if not seed.is_file():
        raise RuntimeError(f"seed startup prompt missing: {seed}")
    try:
        if seed.stat().st_size > MAX_STARTUP_PROMPT_BYTES:
            raise RuntimeError(f"seed startup prompt too large: {seed}")
        text = seed.read_text(encoding="utf-8")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"seed startup prompt unreadable: {exc}") from exc
    if not text.strip():
        raise RuntimeError(f"seed startup prompt is empty: {seed}")
    if len(text.encode("utf-8")) > MAX_STARTUP_PROMPT_BYTES:
        raise RuntimeError(f"seed startup prompt exceeds byte bound: {seed}")
    op = Path(op)
    op.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp then replace for durability
    tmp = op.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(op)
    try:
        op.chmod(0o600)
    except Exception:
        pass
    return op.resolve()


# ---------------------------------------------------------------------------
# AF #49 M1/W6 — two-phase task-main session binding (I49-B002)
# ---------------------------------------------------------------------------
# PHASE 1 mints an explicit unbound placeholder and establishes the real exact
# Hermes task-main session identity with a bounded mechanical bootstrap prompt
# only. PHASE 2 rewrites the trusted bootstrap with the real exact identity and
# continues the SAME session through the accepted exact-session reentry seam
# with the operator-owned startup prompt. Child dispatch is mechanically
# refused while the origin is unbound (ExecutionDispatcher + coordinator
# activation gates), so the placeholder can never become durable truth.

PHASE1_SESSION_BOOTSTRAP_PROMPT = (
    "AF runtime phase-1 session bootstrap only. Reply with exactly "
    "AF_TASK_MAIN_SESSION_READY. Do not call any tools. Do not execute work."
)
TWO_PHASE_SESSION_BINDING = True
SESSION_BOUND_BEFORE_CHILD_DISPATCH = True
SESSION_BEFORE_BIND_IS_REAL_EXACT_ID = True
EXACT_SESSION_CONTINUATION_REUSED = True
MODEL_SUPPLIES_ORIGIN_SESSION = False
WORKER_SUPPLIES_ORIGIN_SESSION = False
MODEL_CAN_OVERRIDE_ORIGIN_SESSION = False
WORKER_CAN_OVERRIDE_ORIGIN_SESSION = False
MODEL_POLLING_REQUIRED = False
OPERATOR_POLLING_REQUIRED = False
MANUAL_ADVANCE_ONCE_REQUIRED_FOR_COMPLETION = False
TWO_PHASE_LAUNCH_PATH = (
    "aota_forge/composition/task_main_daily_launcher.py:DailyTaskMainLauncher.launch"
)
REAL_SESSION_BINDING_PATH = (
    "aota_forge/composition/task_main_daily_launcher.py:DailyTaskMainLauncher.prepare"
    "(origin_task_main_session_ref=real_session_id)"
)
EXACT_SESSION_CONTINUATION_PATH = (
    "aota_forge/composition/task_main_daily_launcher.py:DailyTaskMainLauncher._continue_exact_session"
)
AUTONOMOUS_COMPLETION_OWNER_PATH = (
    "aota_forge/composition/task_main_daily_launcher.py:DailyTaskMainLauncher.launch"
    " -> aota_forge/composition/execution.py:run_bounded_completion_continuation"
)

# ---------------------------------------------------------------------------
# AF #49 M1/W7 — production tool-surface continuity (I49-B005)
# ---------------------------------------------------------------------------
# The production task-main session must keep the AF tool-surface path across
# the exact-session continuation.  The W7 repair:
#
#   - continues the exact session through the oneshot-resume invocation
#     (``hermes -z <payload> --resume <exact id>``), which resolves the
#     production platform toolsets + MCP servers without the chat path's
#     ``agent.disabled_toolsets`` subtraction (canonical task-main profile
#     disables ``all`` for the chat surface);
#   - mechanically probes the production AF MCP child (same trusted binding
#     envelope) and fails closed BEFORE binding the trusted origin /
#     productive phase-2 when the required AF surface is unavailable;
#   - observes the durable session tool-name pin read-only and fails closed
#     only when Hermes has persisted a definitively unusable pin (a missing
#     pin is the normal fresh-session persistence behavior).
#
# This is invocation/observation only: no Hermes profile/source mutation, no
# session-database write, no replacement session, no new session identity.

PRODUCTION_EXACT_SESSION_TRANSPORT = "oneshot_resume"
TASK_MAIN_TOOL_SURFACE_OBSERVATION_PATH = (
    "aota_forge/adapters/hermes/session_reentry.py:observe_persisted_session_tool_surface"
)
TASK_MAIN_PHASE1_TOOL_SURFACE_GATE = True
TASK_MAIN_PHASE2_TOOL_SURFACE_GATE = True
TASK_MAIN_TOOL_SURFACE_FAIL_CLOSED = True
TASK_MAIN_SESSION_DB_MUTATION = False
TASK_MAIN_PROFILE_HAND_EDIT_REQUIRED = False

# Deterministic AF-side production surface probe.  The phase guard spawns the
# real production aota MCP child with the exact trusted binding envelope the
# Hermes session uses and mechanically lists its tools over stdio.  This is
# "AF-side fail-closed verification that required production tools exist":
# it does not depend on Hermes persisting a session tool pin (Hermes 0.21.1
# does not pin a fresh single-turn session), and it never writes session state.
AOTA_MCP_INVOKE_TOOL_NAME = "aota.invoke"
AF_MCP_SURFACE_PROBE_TIMEOUT_SECONDS = 30.0
AF_MCP_SURFACE_PROBE_MODULE = "aota_forge.composition.worker_vertical_slice"


@dataclass(frozen=True)
class AfMcpSurfaceProbe:
    """Mechanical result of the bounded AF MCP child surface listing."""

    ok: bool
    tool_names: tuple[str, ...]
    detail: str | None


def probe_production_af_mcp_surface(
    *,
    env: Mapping[str, str],
    timeout_seconds: float = AF_MCP_SURFACE_PROBE_TIMEOUT_SECONDS,
) -> AfMcpSurfaceProbe:
    """Spawn the production AF MCP child and list its exposed tools (bounded).

    Uses the exact trusted binding environment supplied by ``build_env`` (the
    pre-resolved envelope + bootstrap locators).  Read-only with respect to
    durable state: the child only constructs the trusted binding and lists
    tools.  Any failure (missing client, child startup, timeout) is reported
    as ``ok=False`` so the caller can fail closed.
    """
    try:
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except Exception as exc:  # noqa: BLE001 - optional transport dependency
        return AfMcpSurfaceProbe(False, (), f"mcp client unavailable: {type(exc).__name__}")

    child_env = dict(os.environ)
    for key, value in env.items():
        if isinstance(key, str) and isinstance(value, str):
            child_env[key] = value
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", AF_MCP_SURFACE_PROBE_MODULE, "--mcp-server"],
        env=child_env,
    )

    async def _list_tools() -> tuple[str, ...]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                return tuple(tool.name for tool in listing.tools)

    try:
        names = asyncio.run(asyncio.wait_for(_list_tools(), timeout=timeout_seconds))
    except Exception as exc:  # noqa: BLE001 - bounded probe, fail closed
        return AfMcpSurfaceProbe(False, (), f"AF MCP child probe failed: {type(exc).__name__}: {exc}")
    return AfMcpSurfaceProbe(True, tuple(names), None)


def _resolve_task_main_hermes_home() -> Path:
    """Resolve the Hermes task-main profile home (same inputs as the W6 seam).

    ``HERMES_HOME`` (an explicit Hermes home) and ``AOTA_HERMES_HOME_HOST``
    are operator/runtime-owned environment inputs; the default remains
    ``~/.hermes/profiles/aota-task-main``.
    """
    hermes_home_env = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home_env:
        return Path(hermes_home_env) / "profiles" / "aota-task-main"
    alt = os.environ.get("AOTA_HERMES_HOME_HOST", "").strip()
    if alt:
        return Path(alt) / "profiles" / "aota-task-main"
    return Path.home() / ".hermes" / "profiles" / "aota-task-main"



def _new_unbound_origin_session_ref() -> str:
    """Mint the explicit pre-session placeholder for the phase-1 window.

    Canonical mechanical representation only (single prefix authority); this
    value is never durable completion authority and never eligible to enter a
    durable child execution record or coordinator activation.
    """
    return f"{UNBOUND_ORIGIN_SESSION_REF_PREFIX}{int(time.time())}-{os.getpid()}"


class TaskMainSessionContinuationError(RuntimeError):
    """Typed fail-closed error: exact task-main session continuation failed."""

    code = "TASK_MAIN_SESSION_CONTINUATION_FAILED"


class TaskMainToolSurfaceUnavailable(RuntimeError):
    """Typed fail-closed error: required production AF tool surface unavailable.

    Raised by the W7 phase gates when the durable task-main session metadata
    shows that the required production AF tool surface is absent/empty, or
    when that surface cannot be established mechanically.  The launch stops
    before the trusted origin bind / productive phase-2 continuation; the
    placeholder origin can therefore never become durable truth.
    """

    code = "TASK_MAIN_TOOL_SURFACE_UNAVAILABLE"


@dataclass(frozen=True)
class PlanAuthorityConfig:
    """Trusted operator configuration for Plan authority selection.

    This is the sole source of Plan authority identity.  It is never
    supplied by model arguments and never hardcoded to a single issue.
    """

    repo: str
    issue_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.repo, str) or not self.repo.strip() or "/" not in self.repo:
            raise ValueError(f"repo must be 'owner/repo', got {self.repo!r}")
        if not isinstance(self.issue_number, int) or self.issue_number <= 0:
            raise ValueError(f"issue_number must be positive int, got {self.issue_number!r}")
        object.__setattr__(self, "repo", self.repo.strip())
        object.__setattr__(self, "issue_number", int(self.issue_number))

    @property
    def authority_ref(self) -> str:
        return f"{self.repo}#{self.issue_number}"


@dataclass(frozen=True)
class DailyTaskMainLaunchConfig:
    """Minimal typed operator configuration for daily launch.

    All fields are operator-owned and validated.  No model-supplied
    authority is honored.
    """

    worktree_root: Path
    project_id: str
    worktree_id: str
    runtime_config_path: Path
    plan_authority: PlanAuthorityConfig

    def __post_init__(self) -> None:
        object.__setattr__(self, "worktree_root", Path(self.worktree_root).resolve())
        if not self.worktree_root.is_dir():
            raise ValueError(f"worktree_root must be a directory: {self.worktree_root!r}")
        if not self.project_id or not self.project_id.strip() or len(self.project_id) > 512:
            raise ValueError(f"project_id invalid: {self.project_id!r}")
        if not self.worktree_id or not self.worktree_id.strip() or len(self.worktree_id) > 512:
            raise ValueError(f"worktree_id invalid: {self.worktree_id!r}")
        object.__setattr__(self, "runtime_config_path", Path(self.runtime_config_path).resolve())
        if not self.runtime_config_path.is_file():
            raise ValueError(f"runtime_config_path missing: {self.runtime_config_path!r}")


@dataclass(frozen=True)
class DailyLaunchContext:
    """Trusted operator input + derived live Plan views (no internal assembly required)."""

    worktree_root: Path
    project_id: str
    worktree_id: str
    runtime_config_path: Path
    coordinator_store_path: Path
    execution_store_path: Path
    live_plan_view: MilestonePlanView
    next_milestone_view: MilestonePlanView | None
    plan_snapshot: PlanAuthoritySnapshot
    runtime_config: RuntimeConfig
    hermes_bin: str


class DailyTaskMainLauncher:
    """Operator-facing production task-main daily launcher (M3 RV2 B003).

    The operator supplies only bounded logical/runtime identity:

      - worktree_root      (where .aota/task-main-bootstrap.json lives)
      - project_id         (e.g. aota_forge)
      - worktree_id        (e.g. aota_forge-main or disposable slice id)
      - runtime_config_path (operator-owned RuntimeConfig JSON)
      - plan_adapter       (operator-selected Plan authority read adapter)

    The launcher never requires the operator to construct
    MilestonePlanView, TaskMainControlService, FileBackedTaskMainCoordinatorStore,
    FileBackedExecutionStateStore, or TrustedTaskMainRuntimeContext by hand.

      OPERATOR_MANUAL_INTERNAL_OBJECT_ASSEMBLY_REQUIRED=no
      OPERATOR_MANUAL_SESSION_BOOTSTRAP_REQUIRED=no

    Plan authority selection is trusted operator configuration; the launcher
    does not hardcode a universal issue and never trusts model-supplied
    authority.
    """

    def __init__(
        self,
        *,
        plan_adapter: PlanAuthorityReadAdapter | None = None,
        hermes_bin: str | None = None,
    ) -> None:
        # No universal default is encoded. If no adapter is supplied, the
        # caller must supply one per invocation (operator config). This
        # ensures PLAN_AUTHORITY_OPERATOR_SELECTABLE and avoids silent
        # #37 default.
        self._plan_adapter = plan_adapter
        self._hermes_bin_override = hermes_bin

    def _resolve_hermes_bin(self, runtime_config: RuntimeConfig) -> str:
        if self._hermes_bin_override and Path(self._hermes_bin_override).is_file():
            return str(Path(self._hermes_bin_override).resolve())
        exe = runtime_config.executable
        p = Path(exe)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        found = shutil.which("hermes")
        if found:
            return str(Path(found).resolve())
        raise RuntimeError("hermes binary not found: no operator RuntimeConfig executable and not on PATH")

    def _load_live_plan_views(
        self,
        *,
        plan_adapter: PlanAuthorityReadAdapter,
    ) -> tuple[PlanAuthoritySnapshot, Any, MilestonePlanView, MilestonePlanView | None]:
        """Read live Plan truth and project canonical Milestone views.

        This is the sole place where Plan text is interpreted, and it
        delegates entirely to the canonical Plan layer:

          snapshot -> normalize_portable_plan -> project_milestone_views

        The launcher itself performs no Plan semantic interpretation.

        Revalidates before every launch/recovery/continuation:

          LIVE_PLAN_TRUTH_REVALIDATED=yes
          USER_APPROVAL_FRESHNESS_REVALIDATED=yes
        """
        from aota_forge.core.plan.projection import project_milestone_views

        snapshot = plan_adapter.load()
        if not isinstance(snapshot.body, str) or not snapshot.body.strip():
            raise RuntimeError("live Plan body is empty")
        doc = normalize_portable_plan(snapshot.body, source_revision=snapshot.revision)

        # Derive plan authority from the selected trusted adapter (operator binding)
        plan_authority: str | None = None
        # Adapter-private knowledge: GitHub adapter exposes plan_authority, Static adapter may expose it
        if hasattr(plan_adapter, "plan_authority"):
            try:
                pa = getattr(plan_adapter, "plan_authority")
                if callable(pa):
                    pa = pa()
                if isinstance(pa, str) and pa.strip():
                    plan_authority = pa.strip()
                elif hasattr(plan_adapter, "_repo") and hasattr(plan_adapter, "_issue"):
                    # Fallback for GitHub adapter without property
                    plan_authority = f"{getattr(plan_adapter, '_repo')}#{getattr(plan_adapter, '_issue')}"
            except Exception:
                plan_authority = None
        # Fallback: try to infer from adapter's private fields (for GitHub)
        if plan_authority is None and hasattr(plan_adapter, "_repo") and hasattr(plan_adapter, "_issue"):
            try:
                plan_authority = f"{getattr(plan_adapter, '_repo')}#{getattr(plan_adapter, '_issue')}"
            except Exception:
                plan_authority = None
        if plan_authority is None:
            # For disposable adapters without explicit authority, require that
            # the body contain a GENERIC authority marker? For tests, the
            # adapter may have been constructed with plan_authority, otherwise
            # we use a synthetic authority derived from digest (not hardcoded #37)
            # But to satisfy MILESTONE_PLAN_AUTHORITY_FROM_OPERATOR_BINDING, we
            # must not invent a silent default. If no authority can be derived,
            # fail closed.
            raise RuntimeError(
                "Plan authority cannot be determined: adapter must expose operator-selected plan_authority "
                "(e.g., 'owner/repo#issue' or 'example-owner/example-governance#123')"
            )

        # Use canonical projection (no launcher-side interpretation)
        # This will fail closed on missing/ambiguous DAG or approval
        plan_digest = snapshot.digest or portable_plan_digest(doc)
        live_view, next_view = project_milestone_views(
            doc,
            plan_authority=plan_authority,
            plan_digest=plan_digest,
            plan_source_revision=snapshot.revision or doc.source_revision or doc.source_digest,
        )
        return snapshot, doc, live_view, next_view

    def prepare(
        self,
        *,
        worktree_root: Path,
        project_id: str = "aota_forge",
        worktree_id: str = "aota_forge-main",
        runtime_config_path: Path | str | None = None,
        plan_adapter: PlanAuthorityReadAdapter | None = None,
        origin_task_main_session_ref: str | None = None,
        coordinator_id: str | None = None,
        work_semantics: Mapping[str, Any] | None = None,
    ) -> DailyLaunchContext:
        """Trusted bootstrap materialization (create or refresh).

        This is the production non-test caller of write_bootstrap_file:

          PRODUCTION_TASK_MAIN_BOOTSTRAP_MATERIALIZER=write_bootstrap_file
          BOOTSTRAP_CREATION_DEPENDS_ON_TEST_HARNESS=no

        It re-reads live Plan truth every call, so stale bootstrap cannot
        override current approval:

          STALE_BOOTSTRAP_CANNOT_OVERRIDE_CURRENT_APPROVAL=yes

        M1/W1-R1: work_semantics is test/bootstrap compatibility ONLY, never
        production authority and never the normal path
        (PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED=no). Production task-main
        commits its bounded WorkSemanticProjection to the existing durable
        coordinator store (see runtime.task_main.coordinator
        .commit_task_main_work_projection); the production handoff_resolver
        transports that durable projection. Normal startup therefore needs
        only project binding + plan identity/source + user gate / runtime
        config, without work_semantics.
        """
        worktree_root = Path(worktree_root).resolve()
        if not worktree_root.is_dir():
            raise ValueError(f"worktree_root must be a directory: {worktree_root!r}")
        if not project_id or not project_id.strip() or len(project_id) > 512:
            raise ValueError(f"project_id invalid: {project_id!r}")
        if not worktree_id or not worktree_id.strip() or len(worktree_id) > 512:
            raise ValueError(f"worktree_id invalid: {worktree_id!r}")

        if runtime_config_path is None:
            runtime_config_path = os.environ.get("AOTA_FORGE_RUNTIME_CONFIG", "").strip()
            if not runtime_config_path:
                raise RuntimeError("runtime_config_path is required: set AOTA_FORGE_RUNTIME_CONFIG or pass explicitly")
        runtime_config_path = Path(runtime_config_path).resolve()
        if not runtime_config_path.is_file():
            raise RuntimeError(f"runtime config missing: {runtime_config_path}")
        runtime_config = load_runtime_config(config_path=str(runtime_config_path))

        adapter = plan_adapter or self._plan_adapter
        if adapter is None:
            raise RuntimeError(
                "plan_adapter is required: Plan authority is operator-selectable and must be explicitly supplied "
                "(e.g., GitHubPlanAuthorityReadAdapter(repo='owner/repo', issue_number=123) or "
                "StaticPlanAuthorityAdapter(body=..., plan_authority='owner/repo#123'))"
            )
        snapshot, doc, live_view, next_view = self._load_live_plan_views(plan_adapter=adapter)

        hermes_bin = self._resolve_hermes_bin(runtime_config)

        coordinator_store_path = worktree_root / ".aota" / "coordinator.json"
        execution_store_path = worktree_root / ".aota" / "execution.json"
        coordinator_store_path.parent.mkdir(parents=True, exist_ok=True)
        if not coordinator_store_path.exists():
            coordinator_store_path.write_text("{}", encoding="utf-8")
            try:
                coordinator_store_path.chmod(0o600)
            except Exception:
                pass
        if not execution_store_path.exists():
            execution_store_path.write_text("{}", encoding="utf-8")
            try:
                execution_store_path.chmod(0o600)
            except Exception:
                pass

        if origin_task_main_session_ref is None or not origin_task_main_session_ref.strip():
            existing_bootstrap = worktree_root / BOOTSTRAP_RELPATH
            if existing_bootstrap.is_file():
                try:
                    data = json.loads(existing_bootstrap.read_text(encoding="utf-8"))
                    cand = str(data.get("origin_task_main_session_ref", "")).strip()
                    if cand and len(cand) <= 512:
                        origin_task_main_session_ref = cand
                    else:
                        origin_task_main_session_ref = f"pending-{int(time.time())}-{os.getpid()}"
                except Exception:
                    origin_task_main_session_ref = f"pending-{int(time.time())}-{os.getpid()}"
            else:
                origin_task_main_session_ref = f"pending-{int(time.time())}-{os.getpid()}"

        if len(origin_task_main_session_ref) > 512 or not origin_task_main_session_ref.strip():
            raise ValueError("origin_task_main_session_ref invalid")
        write_bootstrap_file(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            coordinator_store_path=coordinator_store_path,
            execution_store_path=execution_store_path,
            runtime_config_path=runtime_config_path,
            origin_task_main_session_ref=origin_task_main_session_ref,
            live_plan_view=live_view,
            next_milestone_view=next_view,
            coordinator_id=coordinator_id,
            work_semantics=work_semantics,
        )

        bootstrap_path = worktree_root / BOOTSTRAP_RELPATH
        try:
            mode = bootstrap_path.stat().st_mode
            if mode & 0o777 != 0o600:
                bootstrap_path.chmod(0o600)
        except Exception:
            pass

        return DailyLaunchContext(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=runtime_config_path,
            coordinator_store_path=coordinator_store_path,
            execution_store_path=execution_store_path,
            live_plan_view=live_view,
            next_milestone_view=next_view,
            plan_snapshot=snapshot,
            runtime_config=runtime_config,
            hermes_bin=hermes_bin,
        )

    def refresh(
        self,
        *,
        worktree_root: Path,
        project_id: str = "aota_forge",
        worktree_id: str = "aota_forge-main",
        runtime_config_path: Path | str | None = None,
        plan_adapter: PlanAuthorityReadAdapter | None = None,
        origin_task_main_session_ref: str | None = None,
        work_semantics: Mapping[str, Any] | None = None,
    ) -> DailyLaunchContext:
        """Refresh bootstrap from current live Plan truth (post-user-gate, recovery)."""
        return self.prepare(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=runtime_config_path,
            plan_adapter=plan_adapter,
            origin_task_main_session_ref=origin_task_main_session_ref,
            work_semantics=work_semantics,
        )

    def build_env(self, ctx: DailyLaunchContext, *, trace_path: Path | None = None) -> dict[str, str]:
        """Construct required task-main MCP child environment (production path).

        M2/W1 pre-resolved: AF runtime composition constructs the trusted
        task-main binding BEFORE MCP and hands a verified envelope via the
        opaque locator AOTA_PRE_RESOLVED_BINDING (mechanical, digest-bound).
        Host env / profile literals are not authority. The envelope digest
        binds bootstrap + provenance; tamper fails closed. Old Worker-channel
        material is never emitted.

        M1/W2-R1 (I45-B001): task-main authority channels only.
        """
        from aota_forge.runtime.trusted_runtime_binding import create_task_main_envelope, PRE_RESOLVED_BINDING_ENV

        repo_root = str(Path(__file__).resolve().parents[2])
        bootstrap_path = ctx.worktree_root / BOOTSTRAP_RELPATH
        trace_str = str(trace_path.resolve()) if trace_path is not None else ""

        # Pre-resolved envelope: AF composition builds envelope before MCP
        try:
            envelope_path = create_task_main_envelope(
                worktree_root=ctx.worktree_root,
                bootstrap_path=bootstrap_path,
            )
            envelope_locator = str(envelope_path)
        except Exception:
            # If envelope creation fails, fall back to bootstrap path alone
            # (fail-closed at MCP will still require envelope, so this path
            # will be rejected there; we preserve bootstrap for 0600 audit)
            envelope_locator = str(bootstrap_path.resolve())

        env = {
            "PYTHONPATH": repo_root + (os.pathsep + os.environ.get("PYTHONPATH", "") if os.environ.get("PYTHONPATH") else ""),
            "AOTA_FORGE_REPO_ROOT": repo_root,
            "AOTA_FORGE_RUNTIME_CONFIG": str(ctx.runtime_config_path.resolve()),
            "AOTA_TASK_MAIN_BOOTSTRAP": str(bootstrap_path.resolve()),
            "AOTA_TASK_MAIN_TRACE": trace_str,
            PRE_RESOLVED_BINDING_ENV: envelope_locator,
        }
        return env

    def launch(
        self,
        *,
        worktree_root: Path,
        project_id: str = "aota_forge",
        worktree_id: str = "aota_forge-main",
        runtime_config_path: Path | str | None = None,
        plan_adapter: PlanAuthorityReadAdapter | None = None,
        initial_prompt: str | None = None,
        timeout_seconds: int = 120,
        trace_path: Path | None = None,
        completion_timeout_seconds: float | None = None,
    ) -> tuple[DailyLaunchContext, str]:
        """Two-phase production task-main launch (AF #49 M1/W6).

        PHASE 1 — session identity establishment only:
          materialize the trusted bootstrap with an explicit UNBOUND origin
          (pre-session placeholder), launch Hermes ``aota-task-main`` with the
          bounded mechanical bootstrap prompt, and obtain the real exact
          Hermes task-main session identity. While the placeholder is bound,
          the runtime fails closed for child dispatch / coordinator activation.

        PHASE 2 — trusted binding + exact-session continuation:
          rewrite the trusted bootstrap with the real exact session identity,
          then continue the SAME session through the existing
          ``HermesExactSessionReentry`` seam with the operator-owned startup
          prompt. Dispatch is now allowed and every durable child execution
          record carries the exact real origin.

        COMPLETION CONTINUATION — runtime-owned bounded lifecycle continuation:
          after the parent turn ends, the launcher (production lifecycle
          owner) performs the bounded receipt observation → parent-side
          reconciliation → deterministic CARD → exact-session delivery
          continuation for the currently relevant execution(s). No model call,
          no operator polling, no manual ``advance_once``.

        Returns (ctx, session_id). No replacement task-main session is ever
        spawned as the normal repair:

          OPERATOR_MANUAL_SESSION_BOOTSTRAP_REQUIRED=no
        """
        # Operator startup surface resolution happens before any launch: the
        # effective file is required (fail closed, no silent seed fallback) and
        # is delivered in PHASE 2, after the real origin binding exists.
        startup_prompt = _resolve_task_main_startup_prompt(initial_prompt)

        # ---- PHASE 1: unbound bootstrap + session identity establishment ----
        ctx_pending = self.prepare(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=runtime_config_path,
            plan_adapter=plan_adapter,
            origin_task_main_session_ref=_new_unbound_origin_session_ref(),
        )
        env = self.build_env(ctx_pending, trace_path=trace_path)
        hermes_env = {**os.environ, **env}
        hermes_bin = ctx_pending.hermes_bin

        with tempfile.TemporaryDirectory(prefix="aota-task-main-launch-") as td:
            usage_path = Path(td) / "usage.json"
            cmd = [hermes_bin, "-p", "aota-task-main"]
            if ctx_pending.runtime_config.provider:
                cmd.extend(["--provider", ctx_pending.runtime_config.provider])
            if ctx_pending.runtime_config.model:
                cmd.extend(["-m", ctx_pending.runtime_config.model])
            cmd.extend(["--usage-file", str(usage_path), "-z", PHASE1_SESSION_BOOTSTRAP_PROMPT])

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, env=hermes_env)
            if not usage_path.is_file():
                raise RuntimeError(f"hermes session creation failed: usage missing exit={proc.returncode} stdout={proc.stdout[:500]} stderr={proc.stderr[:500]}")
            data = json.loads(usage_path.read_text(encoding="utf-8"))
            session_id = str(data.get("session_id") or "").strip()
            if not session_id or not data.get("completed"):
                raise RuntimeError(f"hermes session not completed: {data}")

        if not is_bound_origin_session_ref(session_id):
            raise TaskMainSessionContinuationError(
                f"hermes returned a non-exact task-main session identity ({session_id!r}); "
                "refusing to bind a placeholder/foreign identity as the trusted origin"
            )

        # ---- W7 PHASE-1 GATE: production AF tool surface must be observable ----
        # Runs BEFORE the trusted origin bind: an unobservable/unusable surface
        # stops the launch while the origin is still the unbound placeholder.
        self._require_task_main_tool_surface(ctx=ctx_pending, session_id=session_id, phase="phase1", env=env)

        # ---- PHASE 2: bind real origin + continue the exact same session ----
        ctx = self.prepare(
            worktree_root=Path(worktree_root).resolve(),
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=runtime_config_path,
            plan_adapter=plan_adapter,
            origin_task_main_session_ref=session_id,
            coordinator_id=ctx_pending.live_plan_view.milestone_id and f"{project_id}:{ctx_pending.live_plan_view.milestone_id}" or None,
        )
        continuation = self._continue_exact_session(
            ctx=ctx,
            session_id=session_id,
            payload=startup_prompt,
            timeout_seconds=timeout_seconds,
            trace_path=trace_path,
        )
        from aota_forge.adapters.hermes.session_reentry import OUTCOME_COMPLETED

        if continuation.outcome != OUTCOME_COMPLETED:
            raise TaskMainSessionContinuationError(
                f"exact-session continuation into {session_id!r} did not complete "
                f"(outcome={continuation.outcome!r}, "
                f"error={continuation.error_code or continuation.error_message!r}); "
                "no replacement task-main session was spawned"
            )

        # ---- W7 PHASE-2 GATE: exact resume kept the production AF surface ----
        self._require_task_main_tool_surface(ctx=ctx, session_id=session_id, phase="phase2")

        # ---- runtime-owned bounded completion continuation (I49-B004) ----
        self._run_autonomous_completion_continuation(
            ctx=ctx,
            session_id=session_id,
            timeout_seconds=completion_timeout_seconds,
            trace_path=trace_path,
        )
        return ctx, session_id

    def _build_exact_session_reentry(self, *, ctx: DailyLaunchContext, timeout_seconds: float):
        """Trusted exact-session reentry builder (single production seam).

        W7 uses the tool-surface-preserving oneshot-resume transport so the
        resumed exact session resolves the production AF surface (the W6
        chat-quiet path applies the canonical profile's ``disabled_toolsets``
        subtraction, which zeroes the AF MCP surface for this profile).
        Tests/component proofs may override this method to inject a scripted
        exact-session seam; the production construction is unchanged.
        """
        from aota_forge.adapters.hermes.session_reentry import (
            TRANSPORT_ONESHOT_RESUME,
            HermesExactSessionReentry,
        )

        hermes_bin = ctx.hermes_bin
        hermes_home = _resolve_task_main_hermes_home()
        return HermesExactSessionReentry(
            hermes_bin,
            hermes_home=hermes_home,
            profile="aota-task-main",
            spool_root=Path(tempfile.gettempdir()) / "aota-task-main-launch-spool",
            timeout_seconds=timeout_seconds,
            transport=TRANSPORT_ONESHOT_RESUME,
        )

    def _observe_exact_session_tool_surface(self, *, session_id: str):
        """Read-only observation of the durable session tool-name pin.

        Supported session-metadata observation only: no session-database
        mutation, no session-row fabrication, no profile hand-edit.
        """
        from aota_forge.adapters.hermes.session_reentry import (
            observe_persisted_session_tool_surface,
        )

        return observe_persisted_session_tool_surface(
            session_id,
            hermes_home=_resolve_task_main_hermes_home(),
        )

    def _probe_af_mcp_tool_surface(
        self,
        *,
        ctx: DailyLaunchContext,
        env: Mapping[str, str] | None = None,
    ) -> AfMcpSurfaceProbe:
        """Spawn the production AF MCP child and list its exposed tools.

        The child receives the same trusted binding environment the Hermes
        session uses (pre-resolved envelope + bootstrap locators).  Read-only.
        """
        child_env = dict(env) if env is not None else self.build_env(ctx)
        return probe_production_af_mcp_surface(env=child_env)

    def _require_task_main_tool_surface(
        self,
        *,
        ctx: DailyLaunchContext,
        session_id: str,
        phase: str,
        env: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """Fail closed unless the required production AF tool surface is available.

        Two mechanical checks, in order:

        1. deterministic AF MCP surface probe — spawn the production aota MCP
           child with the trusted binding env and list its tools; the single
           ``aota.invoke`` entry must be present.  This is the authoritative
           capability check and does not depend on Hermes pin persistence.
        2. read-only session pin observation — when Hermes *has* persisted a
           tool pin for the exact session, it must be non-empty and carry the
           AOTA invoke capability.  A missing pin is the normal Hermes 0.21.1
           fresh-first-turn state (the row is created after the prompt/tool
           persist), so absence is not evidence of an unusable surface; the
           AF MCP probe and the real continuation turn carry that guarantee.

        Never a model-semantics judgement; never a session-store write.
        """
        from aota_forge.adapters.hermes.session_reentry import (
            production_aota_tool_surface_present,
        )

        probe = self._probe_af_mcp_tool_surface(ctx=ctx, env=env)
        if not probe.ok:
            raise TaskMainToolSurfaceUnavailable(
                f"{phase}: required task-main production AF tool surface probe failed: "
                f"{probe.detail}; refusing productive continuation"
            )
        if AOTA_MCP_INVOKE_TOOL_NAME not in probe.tool_names:
            raise TaskMainToolSurfaceUnavailable(
                f"{phase}: AF MCP production surface lacks {AOTA_MCP_INVOKE_TOOL_NAME!r}: "
                f"{list(probe.tool_names)}; refusing productive continuation"
            )
        observation = self._observe_exact_session_tool_surface(session_id=session_id)
        if observation.observed:
            names = tuple(observation.tool_names or ())
            if not names:
                raise TaskMainToolSurfaceUnavailable(
                    f"{phase}: task-main session {session_id!r} persisted an empty tool surface; "
                    "refusing productive continuation"
                )
            if not production_aota_tool_surface_present(names):
                raise TaskMainToolSurfaceUnavailable(
                    f"{phase}: task-main session {session_id!r} tool surface lacks the AOTA invoke "
                    f"capability: {list(names)}; refusing productive continuation"
                )
        return probe.tool_names

    def _continue_exact_session(
        self,
        *,
        ctx: DailyLaunchContext,
        session_id: str,
        payload: str,
        timeout_seconds: float = 120,
        trace_path: Path | None = None,
    ):
        """Continue the EXACT task-main session with trusted binding present.

        Reuses ``HermesExactSessionReentry`` exactly as accepted; the payload is
        the operator-owned startup prompt (or explicit operator override). The
        pre-resolved binding envelope for the updated bootstrap is exported for
        the duration of the turn so the resumed session's MCP child rebuilds
        the SAME trusted binding (real origin). Never spawns a replacement.
        """
        env = self.build_env(ctx, trace_path=trace_path)
        old_env: dict[str, str | None] = {}
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            reentry = self._build_exact_session_reentry(ctx=ctx, timeout_seconds=timeout_seconds)
            return reentry.reenter(session_id, payload)
        finally:
            for k, old in old_env.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    def _run_autonomous_completion_continuation(
        self,
        *,
        ctx: DailyLaunchContext,
        session_id: str,
        timeout_seconds: float | None = None,
        trace_path: Path | None = None,
    ):
        """Runtime-owned bounded completion continuation (AF #49 M1/W6).

        The launcher is the production lifecycle owner for the currently-active
        execution(s) of the exact session it launched: after the parent turn
        ends it performs the bounded receipt observation → parent-side
        reconciliation → deterministic CARD → exact-session delivery
        continuation over the durable stores. A fresh process reconstructs the
        same truth; no model call and no operator action participates. When no
        relevant active/pending execution exists, this is a bounded no-op.
        """
        from aota_forge.composition.execution import (
            DEFAULT_COMPLETION_CONTINUATION_TIMEOUT_SECONDS,
            run_bounded_completion_continuation,
        )

        effective_timeout = (
            DEFAULT_COMPLETION_CONTINUATION_TIMEOUT_SECONDS
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        env = self.build_env(ctx, trace_path=trace_path)
        old_env: dict[str, str | None] = {}
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            return run_bounded_completion_continuation(
                execution_store_path=ctx.execution_store_path,
                worktree_root=ctx.worktree_root,
                runtime_config=ctx.runtime_config,
                relevant_origin_session_ref=session_id,
                # AF #49 M1/W9: trusted lifecycle identity enables the governed
                # semantic-return gate (process exit 0 is not semantic success).
                project_id=ctx.project_id,
                worktree_id=ctx.worktree_id,
                timeout_seconds=effective_timeout,
            )
        finally:
            for k, old in old_env.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    def resume(
        self,
        *,
        worktree_root: Path,
        session_id: str,
        payload: str,
        project_id: str = "aota_forge",
        worktree_id: str = "aota_forge-main",
        runtime_config_path: Path | str | None = None,
        plan_adapter: PlanAuthorityReadAdapter | None = None,
        timeout_seconds: int = 120,
        trace_path: Path | None = None,
    ) -> Any:
        """Re-read live Plan, refresh bootstrap, and re-enter the exact task-main session."""
        ctx = self.refresh(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=runtime_config_path,
            plan_adapter=plan_adapter,
            origin_task_main_session_ref=session_id,
        )
        return self._continue_exact_session(
            ctx=ctx,
            session_id=session_id,
            payload=payload,
            timeout_seconds=timeout_seconds,
            trace_path=trace_path,
        )


# ---------------------------------------------------------------------------
# Convenience: simple operator entry point (no internal assembly required)
# ---------------------------------------------------------------------------

def launch_daily_task_main(
    worktree_root: str | os.PathLike[str],
    *,
    project_id: str = "aota_forge",
    worktree_id: str = "aota_forge-main",
    runtime_config_path: str | os.PathLike[str] | None = None,
    plan_adapter: PlanAuthorityReadAdapter | None = None,
    hermes_bin: str | None = None,
    initial_prompt: str | None = None,
) -> tuple[DailyLaunchContext, str]:
    """Operator-simple daily launcher entrypoint.

    Plan authority is operator-selectable via ``plan_adapter``.  No universal
    default is encoded.
    """
    if plan_adapter is None:
        raise RuntimeError(
            "plan_adapter is required: Plan authority is operator-selectable "
            "(e.g., GitHubPlanAuthorityReadAdapter(repo='owner/repo', issue_number=123) "
            "or StaticPlanAuthorityAdapter(body=..., plan_authority='owner/repo#123'))"
        )
    launcher = DailyTaskMainLauncher(plan_adapter=plan_adapter, hermes_bin=hermes_bin)
    return launcher.launch(
        worktree_root=Path(worktree_root),
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=Path(runtime_config_path) if runtime_config_path else None,
        plan_adapter=plan_adapter,
        initial_prompt=initial_prompt,
    )


__all__ = [
    "DailyTaskMainLauncher",
    "DailyLaunchContext",
    "launch_daily_task_main",
    "PlanAuthorityConfig",
    "DailyTaskMainLaunchConfig",
    "TASK_MAIN_REQUIRED_ENV",
    "TASK_MAIN_REQUIRED_ENV_PRODUCER",
    "PRODUCTION_TASK_MAIN_BOOTSTRAP_MATERIALIZER",
    "BOOTSTRAP_CREATION_DEPENDS_ON_TEST_HARNESS",
    "EXISTING_PLAN_AUTHORITY_BOUNDARY_REUSED",
    "NEW_PLAN_AUTHORITY_SYSTEM_CREATED",
    "PRODUCTION_LAUNCHER_SEMANTIC_CONTROL_CALLS",
    "PLAN_AUTHORITY_OPERATOR_SELECTABLE",
    "PLAN_AUTHORITY_HARDCODED_TO_ISSUE_37",
    "TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_HANDOFF",
    "TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_AUTHORITY_KEYS",
    "TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF",
    "TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION",
    "TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED",
    "OPERATOR_STARTUP_PROMPT_PATH",
    "SEED_STARTUP_PROMPT_PATH",
    "MAX_STARTUP_PROMPT_BYTES",
    "TASK_MAIN_STARTUP_PROMPT_OPERATOR_OWNED",
    "TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY",
    "AF_ROLE_AUTHORITY_FROM_STARTUP_PROMPT",
    "PLAN_AUTHORITY_FROM_STARTUP_PROMPT",
    "SOURCE_SEED_SILENT_RUNTIME_FALLBACK",
    "PHASE1_SESSION_BOOTSTRAP_PROMPT",
    "TWO_PHASE_SESSION_BINDING",
    "SESSION_BOUND_BEFORE_CHILD_DISPATCH",
    "SESSION_BEFORE_BIND_IS_REAL_EXACT_ID",
    "EXACT_SESSION_CONTINUATION_REUSED",
    "MODEL_SUPPLIES_ORIGIN_SESSION",
    "WORKER_SUPPLIES_ORIGIN_SESSION",
    "MODEL_CAN_OVERRIDE_ORIGIN_SESSION",
    "WORKER_CAN_OVERRIDE_ORIGIN_SESSION",
    "MODEL_POLLING_REQUIRED",
    "OPERATOR_POLLING_REQUIRED",
    "MANUAL_ADVANCE_ONCE_REQUIRED_FOR_COMPLETION",
    "TWO_PHASE_LAUNCH_PATH",
    "REAL_SESSION_BINDING_PATH",
    "EXACT_SESSION_CONTINUATION_PATH",
    "AUTONOMOUS_COMPLETION_OWNER_PATH",
    "PRODUCTION_EXACT_SESSION_TRANSPORT",
    "TASK_MAIN_TOOL_SURFACE_OBSERVATION_PATH",
    "TASK_MAIN_PHASE1_TOOL_SURFACE_GATE",
    "TASK_MAIN_PHASE2_TOOL_SURFACE_GATE",
    "TASK_MAIN_TOOL_SURFACE_FAIL_CLOSED",
    "TASK_MAIN_SESSION_DB_MUTATION",
    "TASK_MAIN_PROFILE_HAND_EDIT_REQUIRED",
    "AOTA_MCP_INVOKE_TOOL_NAME",
    "AF_MCP_SURFACE_PROBE_TIMEOUT_SECONDS",
    "AfMcpSurfaceProbe",
    "probe_production_af_mcp_surface",
    "TaskMainSessionContinuationError",
    "TaskMainToolSurfaceUnavailable",
    "_new_unbound_origin_session_ref",
    "_resolve_task_main_hermes_home",
    "_read_operator_startup_prompt",
    "_resolve_task_main_startup_prompt",
    "materialize_operator_startup_from_seed",
]
