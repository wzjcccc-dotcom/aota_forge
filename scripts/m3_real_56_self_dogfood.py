#!/usr/bin/env python3
"""AF #56 M3/W2 — real OpenCode task-main self-dogfood on Plan #56.

Runs the REAL pinned OpenCode reference host with the REAL operator-owned
provider/model through the PRODUCTION task-main operator path
(``DailyTaskMainLauncher`` executor=opencode) and verifies the full governed
lifecycle with bounded machine evidence:

  real task-main S0
    -> aota.invoke role.bootstrap (mechanically enforced first semantic action)
    -> github.issue.read #56 (Plan intake through governed operations)
    -> handoff.write(work_item) + task.start(coder)
    -> real coder Worker session S1 (workspace.write source mutation)
    -> task.return -> durable WorkerResultCard
    -> completion envelope into the EXACT S0 -> identity-bound ACK
    -> task-main dispatches reviewer -> real reviewer Worker session S2
    -> task.return -> CARD -> ACK in the SAME S0

The harness itself scripts NO semantic step: it supplies the operator Work
order, runs the production launcher, and then reads bounded evidence
(sessions, durable records, exact-session messages, git status).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REFERENCE_BASE_URL = os.environ.get("AF_REF_URL", "http://127.0.0.1:4096")
DEFAULT_RUNTIME_CONFIG = str(Path.home() / ".config" / "aota-forge" / "runtime-opencode-reference.json")
DEFAULT_PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#56"
DOGFOOD_DOC = "docs/af56-m3-nb3-binding-lifecycle.md"
GOVERNANCE_REPO = "wzjcccc-dotcom/aota-hermes-tools"
GH_CONFIG_DIR = os.environ.get("GH_CONFIG_DIR", str(Path.home() / ".config" / "gh"))

DOGFOOD_PROMPT = """【AF #56 M3 dogfood — bounded Work】

你是本次 run 的唯一 AF task-main（OpenCode reference host）。

第一個語意動作必須是：
aota_aota_invoke(operation="role.bootstrap", arguments={})

工作流程（依 AF execution context / OPERATION_GUIDANCE）：
A. 用 github.issue.read 讀取本次 bound Plan（#56）本體；可用
   github.issue.comments.read 讀取 managed comments 以確認 M3 範圍。
   不要修改 Issue 本體或任何 comment。
B. 讀完 Plan 後，必須在本回合內立即委派一個 coder Worker，產出新文件
   docs/af56-m3-nb3-binding-lifecycle.md，內容涵蓋：
   1) OpenCode host instance directory 是 task/session-scoped 機械命名空間；
   2) AF authorized worktree 由 binding envelope 攜帶，
      host instance directory != AF authorized worktree；
   3) 同一 trusted worktree 的連續 task 不得重用舊 binding（NB-3）；
   4) 每個 task lifetime 有獨立 instance namespace，stale binding 不可授權新 task；
   5) NB-4：reference runtime 不採用 synthetic project evidence。
   文件 < 8KB。
   使用 handoff.write(mode="work_item", payload={work_role:"coder",
   work_item_ref:"M3-W2-NB3-DOC", milestone_ref:"M3", objective:...,
   bounded_scope:"只在 docs/af56-m3-nb3-binding-lifecycle.md 建立新檔",
   validation_expectations:[...], semantic_stop_expectations:[...]})，
   然後 task.start(role="coder", handoff_ref=<上一步回傳的 ref>)。
C. task.start 後結束本回合，等待 completion delivery。
D. 收到 completion envelope 的回合：完全依照 envelope 指示，只輸出該單行 ACK。
   系統會在 ACK 後給你下一個回合。E. ACK coder completion 後的下一個回合：委派一位 reviewer Worker 獨立審查
   docs/af56-m3-nb3-binding-lifecycle.md（同樣 handoff.write + task.start，
   role="reviewer"）。reviewer 以 workspace.read 讀取該檔，驗證是否涵蓋 B 的
   五點；不得修改任何檔案。
F. 收到 reviewer completion 後同樣只輸出 ACK 行。
G. 最後一回合以一句話總結 bounded Work 結果。

限制：
- 你沒有 workspace.write authority；不得直接修改任何 source 檔案，
  所有檔案變更只能由 coder Worker 執行。
- 不得建立第二個 task-main session；不得修改 #56 governance。
- 所有 authority 以 AF trusted runtime 為準。
"""


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/home/latios/.local/bin/rtk", "gh", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GH_CONFIG_DIR": GH_CONFIG_DIR},
        timeout=60,
    )


def governance_snapshot(issue: int) -> dict:
    proc = _gh("api", f"repos/{GOVERNANCE_REPO}/issues/{issue}")
    if proc.returncode != 0:
        raise SystemExit(f"governance read failed: {proc.stderr[:300]}")
    data = json.loads(proc.stdout)
    body = data.get("body") or ""
    comments = _gh(
        "api", f"repos/{GOVERNANCE_REPO}/issues/{issue}/comments?per_page=100"
    )
    comment_count = len(json.loads(comments.stdout)) if comments.returncode == 0 else -1
    return {
        "updated_at": data.get("updated_at"),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "comment_count": comment_count,
    }


def _raw_json(method: str, url: str, body=None, timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def write_evidence(root: Path, name: str, obj) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[evidence] {name}")


def _tool_calls(messages) -> list[dict]:
    calls = []
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            raw_input = state.get("input")
            operation = None
            if isinstance(raw_input, dict):
                operation = raw_input.get("operation")
            elif isinstance(raw_input, str):
                try:
                    decoded = json.loads(raw_input)
                    operation = decoded.get("operation") if isinstance(decoded, dict) else None
                except Exception:
                    operation = None
            calls.append(
                {
                    "tool": part.get("tool"),
                    "operation": operation,
                    "status": state.get("status"),
                    "input_sha256": hashlib.sha256(
                        json.dumps(raw_input, sort_keys=True).encode("utf-8")
                    ).hexdigest()[:16]
                    if raw_input is not None
                    else None,
                }
            )
    return calls


def _native_attempts(calls: list[dict]) -> dict:
    successful = [c for c in calls if c["tool"] not in ("aota_aota_invoke", "invalid")]
    blocked = [c for c in calls if c["tool"] == "invalid"]
    return {
        "successful_native_tool_calls": len(successful),
        "blocked_native_attempt_parts": len(blocked),
        "successful_marker_calls": successful[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--runtime-config", default=DEFAULT_RUNTIME_CONFIG)
    parser.add_argument("--plan-ref", default=DEFAULT_PLAN_REF)
    parser.add_argument("--worktree", default=str(REPO_ROOT))
    parser.add_argument("--timeout-seconds", type=float, default=1500.0)
    parser.add_argument("--completion-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--max-productive-continuations", type=int, default=3)
    parser.add_argument("--max-operator-nudges", type=int, default=2)
    args = parser.parse_args()

    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore

    worktree = Path(args.worktree).resolve()
    run_root = Path(args.run_root).resolve()
    evidence_root = Path(args.evidence_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)

    pre_gov = governance_snapshot(56)
    pre_git = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    pre_instances = sorted(
        p.name for p in (worktree / ".aota" / "opencode" / "instances").glob("task-main-*")
    ) if (worktree / ".aota" / "opencode" / "instances").is_dir() else []

    launcher = DailyTaskMainLauncher()
    started = time.time()
    ctx, s0 = launcher.launch(
        worktree_root=worktree,
        project_id="aota_forge",
        worktree_id="aota_forge-m3-dogfood",
        runtime_config_path=Path(args.runtime_config),
        initial_prompt=DOGFOOD_PROMPT,
        timeout_seconds=args.timeout_seconds,
        completion_timeout_seconds=args.completion_timeout_seconds,
        max_productive_continuations=args.max_productive_continuations,
        plan_ref=args.plan_ref,
    )

    # Bounded operator nudge: if the task-main session ended its first turn
    # WITHOUT dispatching the bounded Work (model stalled after Plan intake),
    # the operator re-issues the SAME Work order through the production resume
    # seam (no semantic scripting: the model still owns every step). Recorded
    # as bounded evidence.
    nudge_prompt = (
        "【AF operator nudge】\n"
        "你上一回合尚未完成 bounded Work。請立即執行未完成步驟：\n"
        '1) handoff.write(mode="work_item", payload={work_role:"coder", '
        'work_item_ref:"M3-W2-NB3-DOC", milestone_ref:"M3", objective:"...", '
        'bounded_scope:"只在 docs/af56-m3-nb3-binding-lifecycle.md 建立新檔", '
        "validation_expectations:[...], semantic_stop_expectations:[...]})；\n"
        '2) task.start(role="coder", handoff_ref=<上一步 ref>)；\n'
        "3) 完成後結束回合等待 completion delivery。\n"
        "若 coder completion 已 ACK，則改為委派 reviewer 審查 "
        "docs/af56-m3-nb3-binding-lifecycle.md。不要停在只有讀取。"
    )
    nudges = 0
    if args.max_operator_nudges > 0:
        store_probe = FileBackedExecutionStateStore(worktree / ".aota" / "execution.json")
        while nudges < args.max_operator_nudges:
            live_records = [
                r for r in store_probe.list_all()
                if r.origin_session_ref is not None and r.origin_session_ref.value == s0
            ]
            if live_records:
                break
            nudges += 1
            launcher.resume(
                worktree_root=worktree,
                session_id=s0,
                payload=nudge_prompt,
                project_id="aota_forge",
                worktree_id="aota_forge-m3-dogfood",
                runtime_config_path=Path(args.runtime_config),
                timeout_seconds=args.timeout_seconds,
                plan_ref=args.plan_ref,
            )
            continuation = launcher._run_opencode_completion_continuation(
                ctx=ctx, session_id=s0, timeout_seconds=args.completion_timeout_seconds
            )
            launcher._run_opencode_productive_continuations(
                ctx=ctx,
                session_id=s0,
                continuation=continuation,
                startup_prompt=DOGFOOD_PROMPT,
                timeout_seconds=args.timeout_seconds,
                completion_timeout_seconds=args.completion_timeout_seconds,
                max_productive_continuations=args.max_productive_continuations,
            )
    elapsed = round(time.time() - started, 1)

    host = OpenCodeHostClient(REFERENCE_BASE_URL)
    tm_row = host.get_session(s0)
    tm_directory = str(tm_row.get("directory"))
    s0_messages = host.fetch_session_messages(s0, directory=tm_directory)
    s0_calls = _tool_calls(s0_messages)
    first_tool = next((c for c in s0_calls if c["tool"]), None)
    s0_ack_lines = [
        part.get("text", "")
        for message in s0_messages
        for part in (message.get("parts") or [])
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    ack_lines = sorted({line.strip() for text in s0_ack_lines for line in text.splitlines() if line.strip().startswith("AOTA_COMPLETION_ACK_V1")})
    envelope_messages = sum(
        1
        for text in s0_ack_lines
        if "AOTA_WORKER_COMPLETION_V1" in text
    )

    # sessions created in the task-main instance namespace (replacement check)
    status, listed = _raw_json(
        "GET", f"{REFERENCE_BASE_URL}/session?directory=" + urllib.parse.quote(tm_directory)
    )
    tm_sessions = []
    if isinstance(listed, list):
        for row in listed:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if metadata.get("aota_role") == "task-main":
                tm_sessions.append({"id": row.get("id"), "created": (row.get("time") or {}).get("created")})

    store = FileBackedExecutionStateStore(worktree / ".aota" / "execution.json")

    def _worker_surface(session_id: str | None) -> dict:
        if not session_id:
            return {"available": False}
        try:
            row = host.get_session(session_id)
            directory = str(row.get("directory"))
            messages = host.fetch_session_messages(session_id, directory=directory)
            calls = _tool_calls(messages)
            return {
                "available": True,
                "session_id": session_id,
                "directory": directory,
                "operations_completed": [
                    c["operation"]
                    for c in calls
                    if c["tool"] == "aota_aota_invoke" and c["status"] == "completed"
                ],
                "native": _native_attempts(calls),
            }
        except Exception as exc:  # noqa: BLE001 - evidence collection is bounded
            return {"available": False, "error": type(exc).__name__}

    records = []
    for record in store.list_all():
        handle = record.adapter_handle
        worker_session = None
        worker_role = None
        instance_dir = None
        if handle:
            try:
                row = host.get_session(handle)
                instance_dir = row.get("directory")
                pointer_path = Path(instance_dir) / ".aota" / "opencode" / "active_binding.json"
                if pointer_path.is_file():
                    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                    envelope = json.loads(Path(pointer["envelope_path"]).read_text(encoding="utf-8"))
                    worker_role = (envelope.get("payload", {}).get("handoff", {}) or {}).get("work_role")
            except Exception:
                worker_session = None
        records.append(
            {
                "canonical_task_id": record.canonical_task_id,
                "session_id": handle,
                "instance_directory": instance_dir,
                "worker_role": worker_role,
                "origin_session_ref": record.origin_session_ref.value if record.origin_session_ref else None,
                "state": record.canonical_task_state.value,
                "task_return_ok": bool(record.terminal_result and record.terminal_result.ok),
                "card_durable": record.worker_result_card is not None,
                "card_digest": record.worker_result_card_digest,
                "delivery_state": record.delivery_state.value,
            }
        )
    records.sort(key=lambda item: item["canonical_task_id"])

    coder_records = [r for r in records if r["worker_role"] == "coder"]
    reviewer_records = [r for r in records if r["worker_role"] == "reviewer"]

    coder: dict = {
        "count": len(coder_records),
        "sessions": [r["session_id"] for r in coder_records],
        "task_return_ok": all(r["task_return_ok"] for r in coder_records) and bool(coder_records),
        "cards_durable": all(r["card_durable"] for r in coder_records) and bool(coder_records),
        "instance_directories": [r["instance_directory"] for r in coder_records],
        "session_surfaces": [_worker_surface(r["session_id"]) for r in coder_records],
    }
    reviewer: dict = {
        "count": len(reviewer_records),
        "sessions": [r["session_id"] for r in reviewer_records],
        "task_return_ok": all(r["task_return_ok"] for r in reviewer_records) and bool(reviewer_records),
        "cards_durable": all(r["card_durable"] for r in reviewer_records) and bool(reviewer_records),
        "instance_directories": [r["instance_directory"] for r in reviewer_records],
        "session_surfaces": [_worker_surface(r["session_id"]) for r in reviewer_records],
    }
    worker_native_total = sum(
        surface.get("native", {}).get("successful_native_tool_calls", 0)
        for surface in (coder["session_surfaces"] + reviewer["session_surfaces"])
        if surface.get("available")
    )

    post_git = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    doc_path = worktree / DOGFOOD_DOC
    doc_exists = doc_path.is_file()
    doc_sha = hashlib.sha256(doc_path.read_bytes()).hexdigest() if doc_exists else None
    post_gov = governance_snapshot(56)
    post_instances = sorted(
        p.name for p in (worktree / ".aota" / "opencode" / "instances").glob("task-main-*")
    ) if (worktree / ".aota" / "opencode" / "instances").is_dir() else []

    s0_ops = [c["operation"] for c in s0_calls if c["tool"] == "aota_aota_invoke" and c["status"] == "completed"]
    s0_write_ops = [op for op in s0_ops if op == "workspace.write"]
    plan_read = any(op == "github.issue.read" for op in s0_ops)
    start_ops = [op for op in s0_ops if op == "task.start"]

    same_parent = all(r["origin_session_ref"] == s0 for r in records) and bool(records)
    acked = all(r["delivery_state"] == "acknowledged" for r in records) and bool(records)

    result = {
        "record": "AF #56 M3/W2 real OpenCode task-main self-dogfood",
        "plan_ref": args.plan_ref,
        "runtime_config": args.runtime_config,
        "reference_host": host.probe_versions(),
        "worktree": str(worktree),
        "elapsed_seconds": elapsed,
        "operator_nudges": nudges,
        "task_main": {
            "session_id": s0,
            "instance_directory": tm_directory,
            "session_row_metadata": tm_row.get("metadata"),
            "first_tool": first_tool,
            "operations_completed": s0_ops,
            "task_start_count": len(start_ops),
            "workspace_write_calls": s0_write_ops,
            "native": _native_attempts(s0_calls),
            "ack_lines": ack_lines,
            "completion_envelopes_seen": envelope_messages,
            "task_main_sessions_in_instance": tm_sessions,
        },
        "records": records,
        "coder": coder,
        "reviewer": reviewer,
        "source_mutation": {
            "doc_path": DOGFOOD_DOC,
            "doc_exists": doc_exists,
            "doc_sha256": doc_sha,
            "git_status_after": post_git.splitlines(),
        },
        "governance": {
            "pre": pre_gov,
            "post": post_gov,
            "body_unchanged": pre_gov["body_sha256"] == post_gov["body_sha256"],
            "comment_count_unchanged": pre_gov["comment_count"] == post_gov["comment_count"],
        },
        "task_main_instance_dirs": {
            "pre": pre_instances,
            "post": post_instances,
            "new_during_run": sorted(set(post_instances) - set(pre_instances)),
        },
        "assertions": {
            "REAL_56_TASK_MAIN_SESSION": bool(s0 and s0.startswith("ses_")),
            "ROLE_BOOTSTRAP_FIRST": bool(first_tool)
            and first_tool.get("tool") == "aota_aota_invoke"
            and first_tool.get("operation") == "role.bootstrap",
            "PLAN_INTAKE": plan_read,
            "REAL_CODER_WORKER": bool(coder_records),
            "REAL_REVIEWER_WORKER": bool(reviewer_records),
            "CODER_SOURCE_MUTATION": doc_exists,
            "TASK_MAIN_SOURCE_MUTATION_ABSENT": not s0_write_ops,
            "TASK_MAIN_AOTA_ONLY_SURFACE": _native_attempts(s0_calls)["successful_native_tool_calls"] == 0,
            "WORKER_AOTA_ONLY_SURFACE": worker_native_total == 0,
            "TASK_RETURN_VALID": all(r["task_return_ok"] for r in records) and bool(records),
            "CARDS_DURABLE": all(r["card_durable"] for r in records) and bool(records),
            "SAME_EXACT_PARENT_SESSION": same_parent
            and len(tm_sessions) == 1
            and len(post_instances) - len(pre_instances) == 1,
            "ACK_IDENTITY_BOUND": acked and bool(ack_lines),
            "GOVERNANCE_UNCHANGED": pre_gov["body_sha256"] == post_gov["body_sha256"]
            and pre_gov["comment_count"] == post_gov["comment_count"],
            "NO_SECOND_TASK_MAIN_SESSION": len(tm_sessions) == 1,
        },
    }
    write_evidence(evidence_root, "real-56-task-main-session.json", result["task_main"])
    write_evidence(evidence_root, "real-coder-dispatch-result.json", result["coder"])
    write_evidence(evidence_root, "real-reviewer-dispatch-result.json", result["reviewer"])
    write_evidence(evidence_root, "real-parent-reentry-proof.json", result)

    blocking = {k: v for k, v in result["assertions"].items() if not v}
    if blocking:
        print("ASSERTIONS FAILED:", json.dumps(blocking, indent=1))
        raise SystemExit(1)
    print("M3 real #56 self-dogfood PASS")


if __name__ == "__main__":
    main()
