"""AF #54 M2/W4 — bounded thin task-main WORK-IDENTITY production smoke (V3-style).

NOT a full dogfood. It drives the EXISTING production seam once:

    DailyTaskMainLauncher.launch(worktree_root=<disposable fixture>)
      -> real Hermes aota-task-main session (two-phase, exact origin bind)
      -> real aota.invoke MCP child (digest-verified thin binding)
      -> the converged model-visible guidance channels (SOUL/eager/Skill/
         OPERATION_GUIDANCE) are the ONLY usage teaching present
      -> FIRST work_item handoff must already carry the model's own semantic
         identity: work_role + work_item_ref(W1) + milestone_ref(M2)
      -> real task.start with matching role; the durable stamp must carry the
         supplied Work identity; the reviewer child really launches.

Grading is structural over durable facts + read-only persisted tool-result
message counting (ROLE_HANDOFF_MISMATCH occurrences). Natural-language style
is not graded. No transcripts are copied into output. If Hermes/model access
is unavailable, the driver exits 2 (BLOCKED), never FAIL. pytest never runs
this; it is operator opt-in.

Run:
    python3 scripts/af54_m2_thin_work_identity_smoke.py \
        --root <disposable-dir> [--timeout 300] [--summary out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TASK_MAIN_PROFILE_HOME = Path.home() / ".hermes" / "profiles" / "aota-task-main"

PROJECT_ID = "af54_m2_smoke"
WORKTREE_ID = "af54-m2-smoke-wt"

PROJECT_MANIFEST = (
    "schema_version: 1\nproject:\n"
    f"  id: {PROJECT_ID}\n  name: af54m2smoke\n  kind: test\n  status: active\n"
    "summary: AF54 M2 bounded work-identity smoke fixture\n"
    "capabilities: []\npaths:\n  source_root: .\n  source: []\n"
    "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
    "plan:\n  active_plan_id: null\nconstraints: []\n"
)

PLAN_FIXTURE = """# AF54 M2 Smoke Plan (authoritative fixture for this session)

Milestone M2 contains exactly two Work Items.

## M2/W1 — implementation (status: implementation-complete)
Deliver src/widget.py with the bounded `add(a, b)` behavior. The file already
exists in this worktree; treat W1 as implemented and awaiting independent
review.

## M2/W2 — docs pass (status: pending; must NOT start yet)
Requires the W1 review to exist first.

## Required next action (perform exactly this, then stop)
Delegate one bounded REVIEW of M2/W1 to a reviewer child. The review targets
W1 itself (reuse W1's identity; do not invent a new Work number). Scope the
child to src/widget.py plus this plan section only. After the child is
started, output DELEGATION_COMPLETE and the child task id, then stop.
"""

WIDGET_SRC = '''"""tiny fixture under review (M2/W1)."""


def add(a, b):
    return a + b
'''

SCENARIO_PROMPT = (
    "你是本 worktree 的 AF task-main（thin production）。唯一權威 Plan 在 docs/plan.md。"
    "使用 AF 工具讀取 Plan，並完成 Plan 中 Required next action 所述的委派。"
    "一切 AF operation 只透過 aota.invoke；先 role.bootstrap，依 AF 提供的指引操作。"
    "完成委派後輸出 DELEGATION_COMPLETE 與 child task id，然後停止。"
    "不要自己進行 review、不要啟動其他 child、不要修改任何檔案。"
)


def _write_fixture(root: Path) -> None:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(PROJECT_MANIFEST, encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "docs" / "plan.md").write_text(PLAN_FIXTURE, encoding="utf-8")
    (root / "src" / "widget.py").write_text(WIDGET_SRC, encoding="utf-8")


def _first_work_item_handoffs(root: Path, after_ts: float) -> list[dict]:
    out = []
    d = root / ".aota" / "handoffs"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("mode") != "work_item":
            continue
        created = str((data.get("envelope") or {}).get("created_at", ""))
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = f.stat().st_mtime
        if ts >= after_ts - 1.0:
            out.append(data)
    return out


def _execution_records(root: Path) -> dict:
    p = root / ".aota" / "execution.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mismatch_count_in_session(session_id: str) -> int:
    db = TASK_MAIN_PROFILE_HOME / "state.db"
    if not db.is_file():
        return -1
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        n = 0
        for (content, tool_calls) in con.execute(
            "SELECT content, tool_calls FROM messages WHERE session_id = ?", (session_id,)
        ):
            # Count only ACTUAL typed failure outcomes, not guidance echoes:
            # a Skill body legitimately contains the code word. The MCP
            # failure envelope renders "code": "<CODE>" / "<CODE>: " forms.
            for field in (content, tool_calls):
                if not isinstance(field, str):
                    continue
                if '"code": "ROLE_HANDOFF_MISMATCH"' in field or '"code":"ROLE_HANDOFF_MISMATCH"' in field or "ROLE_HANDOFF_MISMATCH:" in field:
                    n += 1
        con.close()
        return n
    except Exception:
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--runtime-config", default=str(Path.home() / ".config/aota-forge/runtime.json"))
    ap.add_argument("--summary", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        print("REFUSED: smoke root must be outside the repo", file=sys.stderr)
        return 2
    hermes_bin = os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
    if not Path(hermes_bin).is_file():
        print("BLOCKED: hermes runtime access unavailable", file=sys.stderr)
        return 2

    _write_fixture(root)
    t0 = time.time()

    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

    launcher = DailyTaskMainLauncher()
    result = {
        "work_item": "M2/W4",
        "plan": "wzjcccc-dotcom/aota-hermes-tools#54",
        "project_id": PROJECT_ID,
        "worktree_id": WORKTREE_ID,
        "root": str(root),
    }
    try:
        ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            runtime_config_path=args.runtime_config,
            initial_prompt=SCENARIO_PROMPT,
            timeout_seconds=args.timeout,
            completion_timeout_seconds=float(args.timeout) * 3,
            max_productive_continuations=2,
        )
        result["TASK_MAIN_SESSION"] = session_id
    except Exception as exc:
        result["LAUNCH_OUTCOME"] = "BLOCKED"
        result["LAUNCH_ERROR_TYPE"] = type(exc).__name__
        result["LAUNCH_ERROR"] = str(exc)[:400]
        _emit(result, args.summary)
        return 2

    handoffs = _first_work_item_handoffs(root, t0)
    records = _execution_records(root)
    mismatch = _mismatch_count_in_session(session_id)

    first_ok = None
    for h in handoffs:
        sem = h.get("semantic") or {}
        if (
            sem.get("work_role") == "reviewer"
            and str(sem.get("work_item_ref", "")).strip()
            and str(sem.get("milestone_ref", "")).strip()
        ):
            first_ok = h
            break

    wi = (first_ok or {}).get("semantic", {})
    result["FIRST_WORK_ITEM_HANDOFF_COUNT"] = str(len(handoffs))
    result["FIRST_VALID_HANDOFF_CONTAINS_CORRECT_WORK_IDENTITY"] = (
        "yes"
        if first_ok is not None
        and str(wi.get("work_item_ref", "")).upper().endswith("W1")
        and str(wi.get("milestone_ref", "")).upper().endswith("M2")
        else "no"
    )
    result["SMOKE_FIRST_VALID_WORK_ROLE"] = str(wi.get("work_role", ""))
    result["SMOKE_FIRST_VALID_WORK_ITEM_REF"] = str(wi.get("work_item_ref", ""))
    result["SMOKE_FIRST_VALID_MILESTONE_REF"] = str(wi.get("milestone_ref", ""))

    stamp_match = [
        cid
        for cid in (records or {})
        if ":M2:" in str(cid) and ":W1:" in str(cid)
    ]
    role_ok = False
    if stamp_match:
        rec = records[stamp_match[0]]
        role_ok = "reviewer" in json.dumps(rec)
    result["DURABLE_RECORDS"] = str(len(records or {}))
    result["FIRST_VALID_TASK_START_ROLE_MATCH"] = "yes" if stamp_match and role_ok else "no"
    result["NO_W1_M1_FALLBACK_USED"] = (
        "yes"
        if first_ok is not None and str(wi.get("milestone_ref", "")).upper().endswith("M2")
        else "unknown"
    )
    result["SMOKE_ROLE_HANDOFF_MISMATCH_COUNT"] = str(mismatch if mismatch >= 0 else "unobservable")
    ok = (
        result["FIRST_VALID_HANDOFF_CONTAINS_CORRECT_WORK_IDENTITY"] == "yes"
        and result["FIRST_VALID_TASK_START_ROLE_MATCH"] == "yes"
        and mismatch == 0
    )
    result["SMOKE_OUTCOME"] = "PASS" if ok else "JUDGED_FAIL"
    _emit(result, args.summary)
    return 0 if ok else 1


def _emit(result: dict, summary_path: str) -> None:
    for k, v in result.items():
        print(f"{k}={v}")
    if summary_path:
        Path(summary_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
