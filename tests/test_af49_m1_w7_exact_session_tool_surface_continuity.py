"""AF #49 M1/W7 — exact-session reentry tool-surface continuity repair (I49-B005).

Repairs:
  I49-B005: the real task-main session identity survived the W6 two-phase
            continuation, but the phase-2 exact-session turn lacked the
            required AF production tool surface, so ``aota.invoke`` was
            unreachable and the real vertical halted.

Confirmed root mechanics (real Hermes 0.21.1 resolution, read-only):
  * the W6 ``chat -Q --resume`` continuation applies
    ``agent.disabled_toolsets`` at tool granularity; the canonical task-main
    profile disables ``all``, which subtracts the whole AF MCP surface for the
    resumed turn (the phase-1 ``-z`` invocation does not pass that list);
  * Hermes' own session tool-name pin restore only runs on a matching stored
    prompt; a stale-runtime rebuild skips it and persists its own (empty)
    resolution over the phase-1 pin.

W7 repair:
  * exact-session continuation uses the supported oneshot-resume invocation
    (``hermes -z <payload> --resume <exact id> --usage-file <path>``), which
    keeps the production platform-toolset + MCP resolution for the SAME
    session identity;
  * the production launcher observes the durable session tool-name pin
    read-only and fails closed (``TASK_MAIN_TOOL_SURFACE_UNAVAILABLE``) before
    binding the trusted origin / entering productive phase-2 when the required
    AF surface is known to be unavailable.

Proof boundary (honest):
  PROVES=AF-side invocation/observation contract: exact-session-only argv
         construction for the oneshot transport, bounded usage evidence,
         read-only session surface observation, and the launcher gates that
         fail closed before origin bind and after exact resume.
  DOES_NOT_PROVE=real Hermes model turn behavior, real role.bootstrap through
         the model, Worker/product vertical, or M1_V3_RERUN_2. Those require
         the bounded real Hermes micro-probe and the M1 V3 rerun 2 gate.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.hermes.session_reentry import (
    MAX_ONESHOT_ARGV_PAYLOAD_BYTES,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_NOT_FOUND,
    OUTCOME_RETRYABLE,
    TRANSPORT_ONESHOT_RESUME,
    HermesExactSessionReentry,
    HermesReentryResult,
    HermesSessionReentryError,
    PersistedSessionToolSurface,
    build_exact_oneshot_resume_argv,
    build_exact_reentry_argv,
    observe_persisted_session_tool_surface,
    production_aota_tool_surface_present,
    validate_exact_session_id,
)
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition.task_main_daily_launcher import (
    AOTA_MCP_INVOKE_TOOL_NAME,
    PRODUCTION_EXACT_SESSION_TRANSPORT,
    TASK_MAIN_PHASE1_TOOL_SURFACE_GATE,
    TASK_MAIN_PHASE2_TOOL_SURFACE_GATE,
    TASK_MAIN_SESSION_DB_MUTATION,
    TASK_MAIN_TOOL_SURFACE_FAIL_CLOSED,
    AfMcpSurfaceProbe,
    DailyTaskMainLauncher,
    TaskMainSessionContinuationError,
    TaskMainToolSurfaceUnavailable,
)
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_RELPATH,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.execution.durable_state import (
    FileBackedExecutionStateStore,
    UnboundOriginSessionError,
    is_placeholder_origin_session_ref,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.mcp_transport import (
    HERMES_AGENT_FACING_AOTA_TOOL_COUNT,
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
)
from aota_forge.work_plane.af_roles import get_tool_surface_for_role

PROJECT_ID = "aota_forge_w7_fixture"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
ENTRY_BASE = "a" * 40
REAL_SESSION = "20260912_130000_w7real"
PRODUCTION_SURFACE = ("tool_search", "tool_describe", "tool_call")
TEST_SCOPE = "hermes:coder"

FAKE_ONESHOT = """#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
log = os.environ.get("FAKE_ONESHOT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\\n")

mode = os.environ.get("FAKE_ONESHOT_MODE", "ok")
resume = argv[argv.index("--resume") + 1] if "--resume" in argv else ""
usage = argv[argv.index("--usage-file") + 1] if "--usage-file" in argv else ""

if mode == "ok":
    if usage:
        with open(usage, "w", encoding="utf-8") as handle:
            json.dump({"session_id": os.environ.get("FAKE_ONESHOT_RESOLVED", resume), "completed": True}, handle)
    print("AF role.bootstrap probe complete")
    sys.exit(0)
if mode == "no_usage":
    print("turn answered without a usage report")
    sys.exit(0)
if mode == "notfound":
    sys.stderr.write("session not found: %s\\n" % resume)
    sys.exit(1)
if mode == "hardfail":
    sys.stderr.write("provider exploded\\n")
    sys.exit(1)
sys.exit(64)
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W7 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1

#### M1/W1 — Authoritative alpha slice
Create the bounded W7 alpha artifact.

#### M1/W2 — Authoritative beta slice
Create the bounded W7 beta artifact.
"""


def _live_view():
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af49w7")
    live, _next = project_milestone_views(
        doc, plan_authority=PLAN_AUTH, plan_digest="d" * 64, plan_source_revision="rev-af49w7"
    )
    return live


def _project_manifest() -> str:
    return (
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n"
    )


def _runtime_config_json() -> dict:
    return {
        "executor": "hermes",
        "executable": "/bin/false",
        "concurrency": 2,
        "provider": "opencode-go",
        "model": "m",
        "bindings": {
            "task-main": {"profile": "aota-task-main"},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
        },
    }


def _make_worktree(tmp_path: Path, *, origin: str, run_id: str = "w7") -> tuple[Path, Path]:
    root = tmp_path / f"wt_{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{run_id}.json"
    cfg_path.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
    coord_path = root / ".aota" / "coordinator.json"
    exec_path = root / ".aota" / "execution.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-w7",
        coordinator_store_path=coord_path,
        execution_store_path=exec_path,
        runtime_config_path=cfg_path,
        origin_task_main_session_ref=origin,
        live_plan_view=_live_view(),
        next_milestone_view=None,
    )
    return root, cfg_path


def _make_package(task_id: str = "w7-task-1") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=PROJECT_ID,
        canonical_role="coder",
        instruction=f"w7 tool-surface fixture child {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


def _make_home(tmp_path: Path, session_id: str, tool_names) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir(exist_ok=True)
    connection = sqlite3.connect(home / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, tool_names TEXT);
        CREATE TABLE session_turn_leases (
            conversation_id TEXT PRIMARY KEY, holder TEXT NOT NULL,
            acquired_at REAL, expires_at REAL
        );
        """
    )
    payload = None if tool_names is None else json.dumps(list(tool_names))
    connection.execute("INSERT INTO sessions VALUES (?, ?)", (session_id, payload))
    connection.commit()
    connection.close()
    return home


def _make_fake_hermes(tmp_path: Path, mode: str = "ok", *, resolved: str | None = None) -> dict[str, Path]:
    bin_path = tmp_path / "hermes-oneshot"
    bin_path.write_text(FAKE_ONESHOT)
    bin_path.chmod(0o755)
    log = tmp_path / "oneshot-argv.log"
    os.environ["FAKE_ONESHOT_LOG"] = str(log)
    os.environ["FAKE_ONESHOT_MODE"] = mode
    if resolved:
        os.environ["FAKE_ONESHOT_RESOLVED"] = resolved
    else:
        os.environ.pop("FAKE_ONESHOT_RESOLVED", None)
    return {"bin": bin_path, "log": log}


@pytest.fixture(autouse=True)
def _clean_fake_env():
    keys = ("FAKE_ONESHOT_LOG", "FAKE_ONESHOT_MODE", "FAKE_ONESHOT_RESOLVED", "HERMES_HOME", BOOTSTRAP_ENV_ROOT)
    for key in keys:
        os.environ.pop(key, None)
    yield
    for key in keys:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# 1. Transport argv construction — exact session only, tool-surface preserving
# ---------------------------------------------------------------------------


class TestOneshotResumeArgv:
    def test_argv_shape_is_exact_session_only(self) -> None:
        argv = build_exact_oneshot_resume_argv(
            "/usr/bin/hermes",
            REAL_SESSION,
            "probe payload",
            profile="aota-task-main",
            usage_file_path="/tmp/usage.json",
        )
        assert argv[:3] == ["/usr/bin/hermes", "-p", "aota-task-main"]
        assert argv[3] == "-z"
        assert argv[4] == "probe payload"
        assert argv[5:7] == ["--resume", REAL_SESSION]
        assert argv[7:] == ["--usage-file", "/tmp/usage.json"]
        joined = set(argv)
        assert joined.isdisjoint({"latest", "-c", "--continue", "--create-if-missing", "--query-file", "chat", "-Q"})
        assert argv[argv.index("--resume") + 1] == REAL_SESSION

    @pytest.mark.parametrize(
        "forbidden",
        ["latest", "-", "--", "@claude", "@codex", "", " ", "a b", "sess/../etc/passwd", "-" + "x" * 130, "\x00session"],
    )
    def test_soft_and_injected_session_targets_are_unproducible(self, forbidden: str) -> None:
        with pytest.raises(HermesSessionReentryError):
            build_exact_oneshot_resume_argv("/usr/bin/hermes", forbidden, "payload")
        with pytest.raises(HermesSessionReentryError):
            validate_exact_session_id(forbidden)

    def test_payload_bounds_fail_closed(self) -> None:
        with pytest.raises(HermesSessionReentryError):
            build_exact_oneshot_resume_argv("/usr/bin/hermes", REAL_SESSION, "")
        with pytest.raises(HermesSessionReentryError):
            build_exact_oneshot_resume_argv("/usr/bin/hermes", REAL_SESSION, "nul\x00payload")
        with pytest.raises(HermesSessionReentryError):
            build_exact_oneshot_resume_argv(
                "/usr/bin/hermes", REAL_SESSION, "x" * (MAX_ONESHOT_ARGV_PAYLOAD_BYTES + 1)
            )

    def test_w6_chat_transport_shape_is_unchanged(self) -> None:
        argv = build_exact_reentry_argv("/usr/bin/hermes", REAL_SESSION, "/tmp/q.txt", profile="aota-task-main")
        assert argv == [
            "/usr/bin/hermes",
            "-p",
            "aota-task-main",
            "chat",
            "-Q",
            "--resume",
            REAL_SESSION,
            "--query-file",
            "/tmp/q.txt",
        ]
        assert set(argv).isdisjoint({"-z", "--oneshot", "--usage-file"})


# ---------------------------------------------------------------------------
# 2. Read-only session tool-surface observation + capability markers
# ---------------------------------------------------------------------------


class TestSessionToolSurfaceObservation:
    def test_observation_reads_pin_without_mutating_store(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        db = home / "state.db"
        before = db.read_bytes()
        observation = observe_persisted_session_tool_surface(REAL_SESSION, hermes_home=home)
        assert observation == PersistedSessionToolSurface(True, PRODUCTION_SURFACE, None)
        assert db.read_bytes() == before
        assert not (tmp_path / "hermes-home" / "state.db-wal").exists()

    def test_missing_session_is_unobserved(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        observation = observe_persisted_session_tool_surface("20991231_235959_missing", hermes_home=home)
        assert observation.observed is False
        assert observation.tool_names is None

    def test_missing_store_is_unobserved(self, tmp_path: Path) -> None:
        observation = observe_persisted_session_tool_surface(REAL_SESSION, hermes_home=tmp_path / "absent")
        assert observation.observed is False

    def test_unpersisted_pin_is_unobserved(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, None)
        observation = observe_persisted_session_tool_surface(REAL_SESSION, hermes_home=home)
        assert observation.observed is False

    def test_empty_pin_is_observed_empty(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, [])
        observation = observe_persisted_session_tool_surface(REAL_SESSION, hermes_home=home)
        assert observation.observed is True
        assert observation.tool_names == ()
        assert production_aota_tool_surface_present(observation.tool_names) is False

    def test_production_surface_markers(self) -> None:
        assert production_aota_tool_surface_present(("tool_search",)) is True
        assert production_aota_tool_surface_present(("tool_call",)) is True
        assert production_aota_tool_surface_present(("mcp__aota__aota_invoke",)) is True
        assert production_aota_tool_surface_present(("bash", "read_file", "write_file")) is False
        assert production_aota_tool_surface_present(()) is False
        assert production_aota_tool_surface_present(None) is False


# ---------------------------------------------------------------------------
# 3. Oneshot transport mechanics — exact session, bounded usage evidence
# ---------------------------------------------------------------------------


class TestOneshotResumeTransport:
    def test_completed_reports_same_exact_session_from_usage(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        files = _make_fake_hermes(tmp_path, mode="ok")
        adapter = HermesExactSessionReentry(
            str(files["bin"]),
            state_db_path=home / "state.db",
            spool_root=tmp_path / "spool",
            timeout_seconds=8.0,
            transport=TRANSPORT_ONESHOT_RESUME,
        )
        result = adapter.reenter(REAL_SESSION, "probe payload")
        assert result.outcome == OUTCOME_COMPLETED
        assert result.resolved_session_id == REAL_SESSION
        argv = json.loads(files["log"].read_text().splitlines()[0])
        assert argv[argv.index("--resume") + 1] == REAL_SESSION
        assert argv[argv.index("-z") + 1] == "probe payload"

    def test_usage_reports_resolved_lineage_session(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        resolved = "20260912_130001_w7tip"
        files = _make_fake_hermes(tmp_path, mode="ok", resolved=resolved)
        adapter = HermesExactSessionReentry(
            str(files["bin"]),
            state_db_path=home / "state.db",
            spool_root=tmp_path / "spool",
            timeout_seconds=8.0,
            transport=TRANSPORT_ONESHOT_RESUME,
        )
        result = adapter.reenter(REAL_SESSION, "probe payload")
        assert result.outcome == OUTCOME_COMPLETED
        assert result.resolved_session_id == resolved

    def test_missing_usage_evidence_fails_closed(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        files = _make_fake_hermes(tmp_path, mode="no_usage")
        adapter = HermesExactSessionReentry(
            str(files["bin"]),
            state_db_path=home / "state.db",
            spool_root=tmp_path / "spool",
            timeout_seconds=8.0,
            transport=TRANSPORT_ONESHOT_RESUME,
        )
        result = adapter.reenter(REAL_SESSION, "probe payload")
        assert result.outcome == OUTCOME_FAILED
        assert result.turn_accepted is False

    def test_missing_exact_session_is_not_found_and_never_creates(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        files = _make_fake_hermes(tmp_path, mode="notfound")
        adapter = HermesExactSessionReentry(
            str(files["bin"]),
            state_db_path=home / "state.db",
            spool_root=tmp_path / "spool",
            timeout_seconds=8.0,
            transport=TRANSPORT_ONESHOT_RESUME,
        )
        result = adapter.reenter(REAL_SESSION, "probe payload")
        assert result.outcome == OUTCOME_NOT_FOUND
        assert result.exact_session_found is False
        # The only invocation was the exact resume; no soft/new-session fallback.
        argv = json.loads(files["log"].read_text().splitlines()[0])
        assert argv[argv.index("--resume") + 1] == REAL_SESSION

    def test_busy_lease_stays_retryable_without_invocation(self, tmp_path: Path) -> None:
        home = _make_home(tmp_path, REAL_SESSION, PRODUCTION_SURFACE)
        connection = sqlite3.connect(home / "state.db")
        connection.execute(
            "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
            (REAL_SESSION, f"pid={os.getpid()}:holder", 0.0, 4102444800.0),
        )
        connection.commit()
        connection.close()
        files = _make_fake_hermes(tmp_path, mode="ok")
        adapter = HermesExactSessionReentry(
            str(files["bin"]),
            state_db_path=home / "state.db",
            spool_root=tmp_path / "spool",
            timeout_seconds=8.0,
            transport=TRANSPORT_ONESHOT_RESUME,
        )
        result = adapter.reenter(REAL_SESSION, "probe payload")
        assert result.outcome == OUTCOME_RETRYABLE
        assert result.busy is True
        assert not files["log"].exists()


# ---------------------------------------------------------------------------
# 4. Launcher composition — phase-1 policy, exact same-session continuation
# ---------------------------------------------------------------------------


def _launcher(tmp_path: Path, *, run_id: str = "w7launch") -> tuple[object, Path, Path, object]:
    root, cfg = _make_worktree(tmp_path, origin=f"{UNBOUND_PREFIX}1700000000-7", run_id=run_id)
    adapter = StaticPlanAuthorityAdapter(body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af49w7")
    launcher = DailyTaskMainLauncher(plan_adapter=adapter, hermes_bin="/bin/false")
    return launcher, root, cfg, adapter


UNBOUND_PREFIX = "pending-"


class _RecordingReentry:
    def __init__(self, result: HermesReentryResult | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._result = result

    def reenter(self, session_id, payload):
        self.calls.append((session_id, payload))
        if self._result is not None:
            return self._result
        return HermesReentryResult(
            outcome=OUTCOME_COMPLETED,
            retryable=False,
            exact_session_found=True,
            turn_accepted=True,
            busy=False,
            exit_code=0,
            session_id=session_id,
            resolved_session_id=session_id,
            error_code=None,
            error_message=None,
            response_excerpt="continued",
            stderr_excerpt=None,
        )


def _record_phase1(monkeypatch, root: Path, *, session_id: str = REAL_SESSION):
    import subprocess as _subprocess

    import aota_forge.composition.task_main_daily_launcher as launcher_mod

    calls: list[list[str]] = []

    def fake_run(cmd, capture_output=False, text=True, timeout=None, env=None, **kwargs):
        calls.append(list(cmd))
        usage_path = Path(cmd[cmd.index("--usage-file") + 1])
        usage_path.write_text(json.dumps({"session_id": session_id, "completed": True}), encoding="utf-8")
        return _subprocess.CompletedProcess(cmd, 0, stdout="session ready", stderr="")

    monkeypatch.setattr(launcher_mod.subprocess, "run", fake_run)
    return calls


VALID_MCP_SURFACE = (
    AOTA_MCP_INVOKE_TOOL_NAME,
    "get_prompt",
    "list_prompts",
    "list_resources",
    "read_resource",
)


def _valid_probe() -> AfMcpSurfaceProbe:
    return AfMcpSurfaceProbe(True, VALID_MCP_SURFACE, None)


def _valid_observation() -> PersistedSessionToolSurface:
    return PersistedSessionToolSurface(True, PRODUCTION_SURFACE, None)


class TestLauncherToolSurfaceComposition:
    def test_phase1_launch_uses_production_profile_tool_policy(self, tmp_path: Path, monkeypatch) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="policy")
        calls = _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: _valid_observation(),
        )
        launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w7",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W7 PHASE2 PROBE",
            timeout_seconds=30,
        )
        argv = calls[0]
        assert argv[1:3] == ["-p", "aota-task-main"]
        assert argv[argv.index("-z") + 1].startswith("AF runtime phase-1 session bootstrap only")
        assert "--usage-file" in argv
        # The production profile tool policy is used as-is: no tool stripping,
        # no alternate profile, no config bypass.
        assert set(argv).isdisjoint({"-t", "--toolsets", "--safe-mode", "--ignore-user-config", "--ignore-rules"})

    def test_launch_continues_exact_same_session_with_tool_surface_gates(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="same-session")
        calls = _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        phases: list[str] = []

        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)

        def observe(*, session_id: str):
            phases.append("observe")
            return _valid_observation()

        def probe(*, ctx, env=None):
            phases.append("probe")
            return _valid_probe()

        monkeypatch.setattr(launcher, "_observe_exact_session_tool_surface", observe)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", probe)
        _ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w7",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W7 PHASE2 PROBE",
            timeout_seconds=30,
        )
        assert session_id == REAL_SESSION
        assert len(calls) == 1, "phase 1 must be the only session creation call"
        assert len(reentry.calls) == 1
        assert reentry.calls[0][0] == REAL_SESSION
        assert reentry.calls[0][1] == "W7 PHASE2 PROBE"
        assert phases.count("probe") == 2, "AF MCP surface probe must run for both phases"
        assert phases.count("observe") == 2, "session pin observation must run for both phases"
        bootstrap = json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert bootstrap["origin_task_main_session_ref"] == REAL_SESSION

    def test_production_reentry_builder_uses_oneshot_tool_surface_transport(self) -> None:
        launcher = DailyTaskMainLauncher(plan_adapter=None, hermes_bin="/bin/false")
        ctx = SimpleNamespace(hermes_bin="/bin/false")
        adapter = launcher._build_exact_session_reentry(ctx=ctx, timeout_seconds=5)
        assert isinstance(adapter, HermesExactSessionReentry)
        assert adapter._transport == TRANSPORT_ONESHOT_RESUME
        assert PRODUCTION_EXACT_SESSION_TRANSPORT == TRANSPORT_ONESHOT_RESUME

    def test_phase1_gate_fails_closed_before_origin_bind(self, tmp_path: Path, monkeypatch) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="empty-surface")
        calls = _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: PersistedSessionToolSurface(True, (), None),
        )
        with pytest.raises(TaskMainToolSurfaceUnavailable) as excinfo:
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert excinfo.value.code == "TASK_MAIN_TOOL_SURFACE_UNAVAILABLE"
        assert len(calls) == 1
        assert reentry.calls == []
        bootstrap = json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert is_placeholder_origin_session_ref(bootstrap["origin_task_main_session_ref"])

    def test_phase1_gate_fails_closed_without_af_capability(self, tmp_path: Path, monkeypatch) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="no-af")
        _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: PersistedSessionToolSurface(True, ("bash", "read_file"), None),
        )
        with pytest.raises(TaskMainToolSurfaceUnavailable):
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert reentry.calls == []

    def test_phase1_gate_fails_closed_when_af_mcp_probe_fails(self, tmp_path: Path, monkeypatch) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="probe-failed")
        calls = _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(
            launcher,
            "_probe_af_mcp_tool_surface",
            lambda *, ctx, env=None: AfMcpSurfaceProbe(False, (), "child startup failed"),
        )
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: _valid_observation(),
        )
        with pytest.raises(TaskMainToolSurfaceUnavailable) as excinfo:
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert excinfo.value.code == "TASK_MAIN_TOOL_SURFACE_UNAVAILABLE"
        assert len(calls) == 1
        assert reentry.calls == []
        bootstrap = json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert is_placeholder_origin_session_ref(bootstrap["origin_task_main_session_ref"])

    def test_phase1_gate_fails_closed_when_aota_invoke_absent_from_probe(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="probe-no-invoke")
        _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(
            launcher,
            "_probe_af_mcp_tool_surface",
            lambda *, ctx, env=None: AfMcpSurfaceProbe(True, ("get_prompt",), None),
        )
        with pytest.raises(TaskMainToolSurfaceUnavailable):
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert reentry.calls == []

    def test_gate_allows_unpinned_fresh_session_when_af_mcp_surface_probes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Hermes 0.21.1 does not persist a tool pin for a fresh single-turn
        # session (the row is created after the prompt/tool persist); a missing
        # pin is not evidence of an unusable surface.  The AF MCP probe and the
        # real continuation turn carry the capability guarantee.
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="unobservable")
        _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: PersistedSessionToolSurface(False, None, "session tool pin is not persisted"),
        )
        _ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w7",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W7 PHASE2 PROBE",
            timeout_seconds=30,
        )
        assert session_id == REAL_SESSION
        assert len(reentry.calls) == 1

    def test_phase2_gate_fails_closed_when_surface_lost(self, tmp_path: Path, monkeypatch) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="lost-surface")
        _record_phase1(monkeypatch, root)
        reentry = _RecordingReentry()
        results = [
            PersistedSessionToolSurface(True, PRODUCTION_SURFACE, None),
            PersistedSessionToolSurface(True, (), None),
        ]
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: results.pop(0),
        )
        with pytest.raises(TaskMainToolSurfaceUnavailable) as excinfo:
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert excinfo.value.code == "TASK_MAIN_TOOL_SURFACE_UNAVAILABLE"
        assert len(reentry.calls) == 1

    def test_continuation_failure_never_spawns_replacement_session(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        launcher, root, cfg, adapter = _launcher(tmp_path, run_id="no-replacement")
        calls = _record_phase1(monkeypatch, root)
        failed = HermesReentryResult(
            outcome=OUTCOME_NOT_FOUND,
            retryable=False,
            exact_session_found=False,
            turn_accepted=False,
            busy=False,
            exit_code=1,
            session_id=REAL_SESSION,
            resolved_session_id=None,
            error_code="HERMES_EXACT_SESSION_NOT_FOUND",
            error_message="exact session vanished",
            response_excerpt=None,
            stderr_excerpt=None,
        )
        reentry = _RecordingReentry(result=failed)
        monkeypatch.setattr(launcher, "_build_exact_session_reentry", lambda *, ctx, timeout_seconds: reentry)
        monkeypatch.setattr(launcher, "_probe_af_mcp_tool_surface", lambda *, ctx, env=None: _valid_probe())
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: _valid_observation(),
        )
        with pytest.raises(TaskMainSessionContinuationError) as excinfo:
            launcher.launch(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-w7",
                runtime_config_path=cfg,
                plan_adapter=adapter,
                initial_prompt="W7 PHASE2 PROBE",
                timeout_seconds=30,
            )
        assert "no replacement task-main session" in str(excinfo.value)
        assert len(calls) == 1
        assert reentry.calls[0][0] == REAL_SESSION


# ---------------------------------------------------------------------------
# 5. Targeted V2 — real subprocess + real SQLite observation continuity
# ---------------------------------------------------------------------------

FAKE_HERMES_PROCESS = """#!/usr/bin/env python3
import json, os, sqlite3, sys

argv = sys.argv[1:]
log = os.environ.get("FAKE_V2_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\\n")

resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
usage = argv[argv.index("--usage-file") + 1] if "--usage-file" in argv else ""
db_path = os.environ.get("FAKE_V2_STATE_DB", "")

if resume is None:
    sid = os.environ["FAKE_V2_PHASE1_SESSION"]
    if db_path:
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, tool_names TEXT)")
        connection.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?)",
            (sid, json.dumps(["tool_search", "tool_describe", "tool_call"])),
        )
        connection.commit()
        connection.close()
    if usage:
        with open(usage, "w", encoding="utf-8") as handle:
            json.dump({"session_id": sid, "completed": True}, handle)
    print("AF_TASK_MAIN_SESSION_READY")
    sys.exit(0)

payload = argv[argv.index("-z") + 1] if "-z" in argv else ""
payload_path = os.environ.get("FAKE_V2_PHASE2_PAYLOAD")
if payload_path:
    with open(payload_path, "w", encoding="utf-8") as handle:
        handle.write(payload)
if usage:
    with open(usage, "w", encoding="utf-8") as handle:
        json.dump({"session_id": resume, "completed": True}, handle)
print("AOTA_W7_V2_FAKE_CONTINUATION")
sys.exit(0)
"""


class TestTargetedV2RealProcessContinuity:
    def test_real_subprocess_same_session_tool_surface_continuity(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        session_id = "20260912_131500_w7v2"
        fake = tmp_path / "hermes-v2"
        fake.write_text(FAKE_HERMES_PROCESS)
        fake.chmod(0o755)
        log = tmp_path / "v2-argv.log"
        payload_out = tmp_path / "v2-phase2-payload.txt"
        hermes_home = tmp_path / "hermes-home-root"
        profile_dir = hermes_home / "profiles" / "aota-task-main"
        profile_dir.mkdir(parents=True)
        state_db = profile_dir / "state.db"

        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["FAKE_V2_LOG"] = str(log)
        os.environ["FAKE_V2_STATE_DB"] = str(state_db)
        os.environ["FAKE_V2_PHASE1_SESSION"] = session_id
        os.environ["FAKE_V2_PHASE2_PAYLOAD"] = str(payload_out)

        root, cfg = _make_worktree(tmp_path, origin="pending-1700000000-7", run_id="v2")
        adapter = StaticPlanAuthorityAdapter(body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af49w7")
        launcher = DailyTaskMainLauncher(plan_adapter=adapter, hermes_bin=str(fake))
        ctx, continued = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w7",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W7 V2 PHASE2 PAYLOAD",
            timeout_seconds=20,
        )
        assert continued == session_id
        invocations = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        assert len(invocations) == 2, "phase 1 + one exact-session continuation"
        phase1, phase2 = invocations
        assert "--resume" not in phase1
        assert phase1[phase1.index("-z") + 1].startswith("AF runtime phase-1 session bootstrap only")
        assert phase2[phase2.index("--resume") + 1] == session_id
        assert phase2[phase2.index("-z") + 1] == "W7 V2 PHASE2 PAYLOAD"
        assert phase2[phase2.index("--usage-file") + 1]
        assert payload_out.read_text(encoding="utf-8") == "W7 V2 PHASE2 PAYLOAD"
        observation = observe_persisted_session_tool_surface(session_id, hermes_home=profile_dir)
        assert observation.observed is True
        assert production_aota_tool_surface_present(observation.tool_names) is True
        assert FileBackedExecutionStateStore(root / ".aota" / "execution.json").list_all() == []
        bootstrap = json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert bootstrap["origin_task_main_session_ref"] == session_id


# ---------------------------------------------------------------------------
# 6. Preserved authority invariants (W5 single tool, W6 unbound gate, no DB write)
# ---------------------------------------------------------------------------


class TestPreservedInvariants:
    def test_unbound_origin_still_fails_closed_and_creates_no_durable_child(
        self, tmp_path: Path
    ) -> None:
        placeholder = f"{UNBOUND_PREFIX}1700000000-4242"
        root, _cfg = _make_worktree(tmp_path, origin=placeholder, run_id="unbound")
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        try:
            binding = try_build_task_main_binding()
        finally:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        assert binding is not None
        dispatcher = binding.trusted_task_main_context.control_service._dispatcher
        assert dispatcher.origin_session_is_placeholder is True
        with pytest.raises(UnboundOriginSessionError) as excinfo:
            dispatcher.dispatch(_make_package("w7-unbound-child"), target_executor_id="hermes")
        assert excinfo.value.code == "UNBOUND_ORIGIN_SESSION"
        assert FileBackedExecutionStateStore(root / ".aota" / "execution.json").list_all() == []

    def test_worker_surface_not_broadened_by_task_main_repair(self) -> None:
        for role in ("coder", "analyst", "reviewer", "project-steward"):
            surface = get_tool_surface_for_role(role)
            assert not any(op.startswith("task_main.") for op in surface.all_capability_names()), role

    def test_single_agent_facing_aota_mcp_tool_preserved(self) -> None:
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1
        assert HERMES_AGENT_FACING_AOTA_TOOL_COUNT == 1

    def test_w7_constants_declare_no_db_mutation_and_fail_closed_gates(self) -> None:
        assert TASK_MAIN_SESSION_DB_MUTATION is False
        assert TASK_MAIN_TOOL_SURFACE_FAIL_CLOSED is True
        assert TASK_MAIN_PHASE1_TOOL_SURFACE_GATE is True
        assert TASK_MAIN_PHASE2_TOOL_SURFACE_GATE is True
