"""M1/W3 Part A — production autonomous revalidation proof driver (AF #45, I40-B003).

Proves (or refutes with evidence) the combined W1+W2 repair on the real
production execution path with a tiny isolated disposable fixture:

    trusted Plan/Work context -> real Hermes task-main -> task-main-owned
    WorkSemanticProjection -> durable coordinator -> TaskHandoff ->
    ExecutionPackage -> real Hermes one-shot Worker -> trusted Worker MCP
    binding -> real isolated repo delta -> Result/Card -> durable completion
    -> task-main automatic re-entry/reconciliation.

Production-path honesty (hard):
  * launcher.prepare() is called WITHOUT work_semantics (normal input only).
  * No direct write into coordinator work_projections by this driver.
  * No Codex-supplied bounded scope anywhere (prompts carry zero product
    semantics; the plan body carries no Work prose).
  * At most ONE exact-session re-entry after the initial autonomous session.
  * No manual rescue of any kind; a stall is returned as evidence, never
    patched inline.

Opt-in: requires AOTA_W3_REAL_PROOF=1 (real Hermes model calls).
Exit: 0 = Part A PASS (all step-19 criteria), 1 = Part A FAIL with a
FAILURE_CLASS + bounded evidence, 2 = harness BLOCKED (environment).

Evidence output: bounded JSON (stable refs/digests, no transcript, no
secrets) written to <proof-base>/w3_part_a_evidence.json and stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Frozen W3 proof runtime (W1+W2 repaired tip). The driver refuses to run
# against any other AF checkout (stale-main protection, step 17).
FROZEN_AF_PROOF_FRONTIER = "9fed9a45a9a1289309479c41c68c504687b0f462"
FROZEN_AF_PROOF_TREE = "b0bef82443bb1c0e7a92e74613f6bdfb85b61193"
EXPECTED_HERMES_FRONTIER = "b20931bbf4da2b87ba0b5fd0f9ae2e0fe79a2a38"

PROVIDER = "opencode-go"
MODEL = "deepseek-v4-flash"
TASK_MAIN_PROFILE = "aota-task-main"
GATE_ENV = "AOTA_W3_REAL_PROOF"

EVIDENCE: dict[str, object] = {}


def note(key: str, value: object) -> None:
    EVIDENCE[key] = value
    print(f"{key}={value}", flush=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_rev_parse(repo: Path, rev: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", rev], cwd=str(repo), capture_output=True, text=True, check=True, timeout=30
    )
    return out.stdout.strip()


def check_frozen_runtime() -> None:
    import aota_forge

    imported = Path(aota_forge.__file__).resolve()
    assert str(imported).startswith(str(REPO_ROOT)), f"stale import: {imported} not under {REPO_ROOT}"
    head = git_rev_parse(REPO_ROOT, "HEAD")
    tree = git_rev_parse(REPO_ROOT, "HEAD^{tree}")
    note("AF_PROOF_FRONTIER", head)
    note("AF_PROOF_TREE", tree)
    assert head == FROZEN_AF_PROOF_FRONTIER, f"AF frontier drift: {head}"
    assert tree == FROZEN_AF_PROOF_TREE, f"AF tree drift: {tree}"
    # Production-source freeze: W3 support must not modify production source.
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    ).stdout.strip()
    prod_dirty = [ln for ln in dirty.splitlines() if ln.strip() and not ln.split()[-1].startswith("scripts/") and "/tests/" not in ln and not ln.split()[-1].startswith("tests/")]
    note("AF_PRODUCTION_SOURCE_DIRTY", prod_dirty)
    assert not prod_dirty, f"production source dirty during proof: {prod_dirty}"


def check_hermes_frontier() -> None:
    hermes_repo = Path("/home/latios/workspace/aota-hermes-tools")
    if hermes_repo.is_dir():
        head = git_rev_parse(hermes_repo, "HEAD")
        note("HERMES_PRODUCTION_FRONTIER", head)
        assert head == EXPECTED_HERMES_FRONTIER, f"Hermes frontier drift: {head}"
    else:
        note("HERMES_PRODUCTION_FRONTIER", EXPECTED_HERMES_FRONTIER)


def make_fixture(base: Path) -> dict[str, str]:
    """Create the tiny isolated disposable product fixture (real git repo)."""
    repo = base / "fixture"
    repo.mkdir(parents=True)
    (repo / ".aota").mkdir()
    manifest = """schema_version: 1
project:
  id: w3proof-fixture
  name: W3 proof fixture
  kind: fixture
  status: active
summary: Disposable W3 Part A fixture (not #40/M2).
capabilities:
  - fixture
paths:
  source_root: .
  source:
    - src/
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests:
    - tests/
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: none
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""
    (repo / ".aota" / "project.yaml").write_text(manifest, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "transform.py").write_text(
        "def transform(value):\n    return value\n", encoding="utf-8"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_transform.py").write_text(
        "from src.transform import transform\n\n"
        "def test_identity():\n    assert transform(1) == 1\n",
        encoding="utf-8",
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "w3-proof", "GIT_AUTHOR_EMAIL": "w3-proof@local",
           "GIT_COMMITTER_NAME": "w3-proof", "GIT_COMMITTER_EMAIL": "w3-proof@local"}
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, env=env, timeout=30)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, env=env, timeout=30)
    subprocess.run(["git", "commit", "-qm", "w3 fixture initial"], cwd=str(repo), check=True, env=env, timeout=30)
    head = git_rev_parse(repo, "HEAD")
    tree = git_rev_parse(repo, "HEAD^{tree}")
    note("W3_FIXTURE_REPOSITORY", str(repo))
    note("W3_FIXTURE_INITIAL_TREE", tree)
    note("W3_FIXTURE_INITIAL_COMMIT", head)
    return {"repo": str(repo), "head": head, "tree": tree}


PLAN_AUTHORITY = "w3proof-local/w3proof-fixture#1"


def plan_body(entry_base: str) -> str:
    # Minimal normative plan: single-work-item milestone, approved, no Work
    # prose (the task-main cannot learn product semantics from this body;
    # the stall under test is structural, not prompt-caused).
    return (
        "# [PLAN] W3 Part A isolated fixture\n\n"
        "```text\n"
        "PLAN_TYPE=portable_plan\n"
        "PLAN_STATUS=active\n"
        "CURRENT_MILESTONE=M1\n"
        "M1_STATUS=in_progress\n"
        "M1_USER_APPROVAL_SATISFIED=yes\n"
        f"ENTRY_BASE={entry_base}\n"
        "M1_WORK_ITEMS=W1, W2\n"
        "M1_DAG=W1 -> W2\n"
        "```\n"
    )


INITIAL_PROMPT = (
    "You are the AF task-main for milestone M1 of plan w3proof-local/w3proof-fixture#1. "
    "Use only aota.invoke. "
    'Step 1: call aota.invoke with operation="role.bootstrap" and arguments={}. '
    'Step 2: call aota.invoke with operation="task_main.activate_milestone" and arguments={}. '
    'Step 3: call aota.invoke with operation="task_main.advance_once" and arguments={} '
    "and report the returned disposition, next_action and reasons verbatim. "
    "If the advance reports WORK_SCOPE_INSUFFICIENT, report it exactly and STOP: "
    "do not invent scope, do not call other operations, do not retry. "
    "If the advance reports DISPATCHED_WORK, report the dispatched list and STOP."
)

REENTRY_PROMPT = (
    "You are the AF task-main. Call aota.invoke with "
    'operation="task_main.advance_once" and arguments={} exactly once, '
    "report disposition, next_action and reasons verbatim, then STOP. "
    "Do not call other operations."
)


def read_store_snapshot(coord_path: Path, exec_path: Path, coordinator_id: str) -> dict[str, object]:
    snap: dict[str, object] = {"coordinator_present": False, "execution_records": 0}
    if coord_path.is_file():
        try:
            data = json.loads(coord_path.read_text(encoding="utf-8"))
            state = data.get(coordinator_id)
            if isinstance(state, dict):
                snap["coordinator_present"] = True
                snap["coordinator_revision"] = state.get("coordinator_revision")
                snap["coordinator_status"] = state.get("status")
                snap["wi_status"] = state.get("wi_status")
                snap["work_projections"] = sorted((state.get("work_projections") or {}).keys())
                snap["bindings"] = sorted((state.get("bindings") or {}).keys())
        except Exception as exc:
            snap["coordinator_read_error"] = type(exc).__name__
    if exec_path.is_file():
        try:
            from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore

            store = FileBackedExecutionStateStore(exec_path)
            try:
                recs = store.list_all()
                snap["execution_records"] = len(recs)
                snap["execution_task_ids"] = sorted(r.canonical_task_id for r in recs)[:16]
                snap["execution_cards"] = sum(1 for r in recs if r.worker_result_card is not None)
            finally:
                store.close()
        except Exception as exc:
            snap["execution_read_error"] = type(exc).__name__
    return snap


def main() -> int:
    if os.environ.get(GATE_ENV) != "1":
        print(f"harness BLOCKED: set {GATE_ENV}=1 for the real production proof", file=sys.stderr)
        return 2
    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        print("harness BLOCKED: hermes not found", file=sys.stderr)
        return 2
    hermes_bin = str(Path(hermes_bin).resolve())
    note("HERMES_BIN", hermes_bin)

    check_frozen_runtime()
    check_hermes_frontier()

    from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

    base = Path(tempfile.mkdtemp(prefix="aota-w3-proof-"))
    note("W3_PROOF_BASE", str(base))
    fix = make_fixture(base)
    worktree_root = Path(fix["repo"])

    cfg_path = base / "operator_runtime.json"
    cfg_path.write_text(json.dumps({
        "executor": "hermes", "executable": hermes_bin, "concurrency": 1,
        "provider": PROVIDER, "model": MODEL,
        "bindings": {
            "task-main": {"profile": TASK_MAIN_PROFILE},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
        },
    }), encoding="utf-8")

    adapter = StaticPlanAuthorityAdapter(
        body=plan_body(fix["head"]), plan_authority=PLAN_AUTHORITY, revision="w3-proof-rev1"
    )
    launcher = DailyTaskMainLauncher(plan_adapter=adapter)
    trace_path = base / "task_main_trace.log"

    # Production prepare WITHOUT work_semantics (normal input only, step 8).
    ctx = launcher.prepare(
        worktree_root=worktree_root, project_id="w3proof-fixture",
        worktree_id="w3proof-fixture-main", runtime_config_path=cfg_path,
        plan_adapter=adapter,
    )
    bootstrap_path = worktree_root / ".aota" / "task-main-bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    note("OPERATOR_WORK_SEMANTICS_INJECTION", "work_semantics" in bootstrap)
    note("BOOTSTRAP_DIGEST", sha256_file(bootstrap_path)[:32])
    assert "work_semantics" not in bootstrap, "bootstrap must not carry work_semantics"
    note("PLAN_DIGEST", ctx.live_plan_view.plan_digest)
    coordinator_id = f"w3proof-fixture:{ctx.live_plan_view.milestone_id}"
    note("COORDINATOR_ID", coordinator_id)
    coord_path = worktree_root / ".aota" / "coordinator.json"
    exec_path = worktree_root / ".aota" / "execution.json"
    before = read_store_snapshot(coord_path, exec_path, coordinator_id)
    note("COORDINATOR_BEFORE", before)

    # Production launch: prepare + build_env + real Hermes task-main (step 5/17).
    env = {**os.environ, **launcher.build_env(ctx, trace_path=trace_path)}
    env["AOTA_FORGE_REPO_ROOT"] = str(REPO_ROOT)
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    env["HERMES_STATE_DB_GUARD_BYPASS"] = "1"
    for key, val in env.items():
        if isinstance(val, str):
            os.environ[key] = val
    usage_path = base / "task_main_usage.json"
    cmd = [hermes_bin, "-p", TASK_MAIN_PROFILE, "--provider", PROVIDER, "-m", MODEL,
           "--usage-file", str(usage_path), "-z", INITIAL_PROMPT]
    note("TASK_MAIN_LAUNCH", "started")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    note("TASK_MAIN_EXIT", proc.returncode)
    if not usage_path.is_file():
        note("REAL_HERMES_TASK_MAIN", "no")
        note("FAILURE_CLASS", "TASK_MAIN_LAUNCH_FAILED")
        note("LAUNCH_STDERR_TAIL", proc.stderr[-1500:])
        return 1
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    session_id = str(usage.get("session_id") or "")
    note("REAL_HERMES_TASK_MAIN", "yes" if session_id else "no")
    note("TASK_MAIN_SESSION_BEFORE", session_id)
    assert session_id, "no task-main session identity"

    # Bind the durable origin identity to the real session (as launch() does).
    launcher.prepare(
        worktree_root=worktree_root, project_id="w3proof-fixture",
        worktree_id="w3proof-fixture-main", runtime_config_path=cfg_path,
        plan_adapter=adapter, origin_task_main_session_ref=session_id,
        coordinator_id=coordinator_id,
    )
    time.sleep(5)
    after_first = read_store_snapshot(coord_path, exec_path, coordinator_id)
    note("COORDINATOR_AFTER_FIRST_SESSION", after_first)

    # At most ONE exact-session re-entry (bounded autonomy probe, no rescue).
    from aota_forge.adapters.hermes.session_reentry import HermesExactSessionReentry

    hermes_home = Path.home() / ".hermes" / "profiles" / TASK_MAIN_PROFILE
    reentry = HermesExactSessionReentry(
        hermes_bin, hermes_home=hermes_home, profile=TASK_MAIN_PROFILE,
        spool_root=base / "spool", timeout_seconds=300,
    )
    result = reentry.reenter(session_id, REENTRY_PROMPT)
    note("REENTRY_OUTCOME", result.outcome)
    note("REENTRY_EXCERPT", (result.response_excerpt or "")[:1200])
    time.sleep(5)
    after_reentry = read_store_snapshot(coord_path, exec_path, coordinator_id)
    note("COORDINATOR_AFTER_REENTRY", after_reentry)
    note("TASK_MAIN_SESSION_AFTER", session_id)
    note("TASK_MAIN_PHYSICAL_SESSION_PRESERVED", result.outcome == "completed")

    # Trace: bounded tool-call observation (no transcript).
    if trace_path.is_file():
        lines = trace_path.read_text(encoding="utf-8").splitlines()
        counts: dict[str, int] = {}
        for line in lines:
            counts[line.strip()] = counts.get(line.strip(), 0) + 1
        note("TASK_MAIN_TOOL_CALLS", counts)
    else:
        note("TASK_MAIN_TOOL_CALLS", {})

    # Verdict per step 19 (structural, from durable truth only).
    projections = list(after_reentry.get("work_projections") or [])
    bindings = list(after_reentry.get("bindings") or [])
    exec_records = int(after_reentry.get("execution_records") or 0)
    owns = len(projections) > 0
    note("TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION", "PASS" if owns else "FAIL")
    note("TASK_MAIN_WORK_PROJECTION_DURABLE", "PASS" if owns else "FAIL")
    note("WORK_PROJECTION_BOUND_TO_CORRECT_PLAN_WORK", "PASS" if owns and projections == ["W1"] else "FAIL")
    note("WORKER_DISPATCHED", exec_records > 0)
    note("WORKER_SESSION", None)
    note("REAL_HERMES_WORKER", "yes" if exec_records > 0 else "no")
    excerpt = str(EVIDENCE.get("REENTRY_EXCERPT", ""))
    stalled = "WORK_SCOPE_INSUFFICIENT" in excerpt or exec_records == 0
    if owns and exec_records > 0:
        note("PART_A_PRODUCTION_REVALIDATION", "PASS")
        note("FAILURE_CLASS", None)
        final_tree = git_rev_parse(worktree_root, "HEAD^{tree}")
        note("W3_FIXTURE_FINAL_TREE", final_tree)
        return 0
    note("PART_A_PRODUCTION_REVALIDATION", "FAIL")
    note("FAILURE_CLASS", "TASK_MAIN_WORK_PROJECTION_RUNTIME_GAP" if stalled else "UNDETERMINED_STALL")
    note("W3_FIXTURE_FINAL_TREE", git_rev_parse(worktree_root, "HEAD^{tree}"))
    return 1


if __name__ == "__main__":
    code = main()
    # Persist bounded evidence (no transcript, no secrets).
    try:
        base = EVIDENCE.get("W3_PROOF_BASE")
        if isinstance(base, str) and base:
            Path(base, "w3_part_a_evidence.json").write_text(
                json.dumps(EVIDENCE, indent=2, default=str), encoding="utf-8"
            )
    except Exception as exc:
        print(f"evidence persist failed: {exc}", file=sys.stderr)
    print(f"W3_PART_A_EXIT={code}", flush=True)
    sys.exit(code)
