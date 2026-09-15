#!/usr/bin/env python3
"""AF #56 M3/W3 — real #39 cross-project Plan intake & live approval-gate stop.

Runs a FRESH real OpenCode task-main session (no #56 semantic context reuse)
through the PRODUCTION operator path against Plan
``wzjcccc-dotcom/aota-hermes-tools#39`` with the trusted registry-backed
project binding:

  project_id=aota-reader
  SOURCE_REPOSITORY=wzjcccc-dotcom/aota_reader_mcp
  registry=/home/latios/.config/aota-forge/workspace-registry.json
  active worktree=a disposable operator directory (never the source repo)

The expected live state at M3 entry is M1_USER_APPROVAL_SATISFIED=no, so the
correct success is a bounded intake and a full stop at the user approval gate
with ZERO mutation of #39 source/governance and ZERO worker dispatch.
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
DEFAULT_RUNTIME_CONFIG = str(Path.home() / "config" / "aota-forge" / "runtime-opencode-reference.json")
_DEFAULT_RUNTIME = Path.home() / ".config" / "aota-forge" / "runtime-opencode-reference.json"
DEFAULT_REGISTRY = str(Path.home() / ".config" / "aota-forge" / "workspace-registry.json")
GOVERNANCE_REPO = "wzjcccc-dotcom/aota-hermes-tools"
PLAN_ISSUE = 39
SOURCE_REPO_LOCAL = Path("/home/latios/workspace/chatgpt-hermes-mcp-poc")
GH_CONFIG_DIR = os.environ.get("GH_CONFIG_DIR", str(Path.home() / ".config" / "gh"))

INTAKE_PROMPT = """【AF #39 cross-project intake — bounded】

你是本次 run 的唯一 AF task-main（OpenCode reference host）。

第一個語意動作必須是：
aota_aota_invoke(operation="role.bootstrap", arguments={})

本回合只做 intake，不要執行任何建構：
1. 用 github.issue.read 讀取本次 bound Plan（#39）本體；可用
   github.issue.comments.read 讀取 managed comments。
2. 從 AF trusted context（TRUSTED_PROJECT_CONTEXT）確認：
   - PROJECT_ID
   - 治理 Plan 的 repository（只提供 Plan，不是 source repository）
   - SOURCE_REPOSITORY
   - canonical project root（由 AF trusted project binding / authorized roots
     提供，root_ref=project-main）
   - authorized roots
3. 檢查 #39 目前的 milestone user approval 狀態。
4. 若 approval 尚未滿足（M1_USER_APPROVAL_SATISFIED=no），
   必須在 user approval gate 停下：
   - 不得 task.start、不得 dispatch 任何 worker；
   - 不得修改任何檔案（source 與 governance 都不可以）；
   - 不得修改 #39 本體或任何 comment。
5. 最後用 bounded 文字總結：
   PROJECT_ID / PLAN_REPOSITORY / SOURCE_REPOSITORY / CANONICAL_PROJECT_ROOT /
   authorized roots / M1_USER_APPROVAL_SATISFIED / gate 決策（STOP），然後結束回合。

不要執行任何其他動作。
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
        raise SystemExit(f"#39 governance read failed: {proc.stderr[:300]}")
    data = json.loads(proc.stdout)
    body = data.get("body") or ""
    comments = _gh("api", f"repos/{GOVERNANCE_REPO}/issues/{issue}/comments?per_page=100")
    comment_count = len(json.loads(comments.stdout)) if comments.returncode == 0 else -1
    flags = {}
    for line in body.splitlines():
        stripped = line.strip()
        for key in ("PROJECT_ID", "SOURCE_REPOSITORY", "CURRENT_MILESTONE", "CURRENT_STATUS",
                    "M1_STATUS", "M1_USER_APPROVAL_SATISFIED", "M1_SOURCE_CONSTRUCTION_STARTED"):
            if stripped.startswith(key + "="):
                flags[key] = stripped.split("=", 1)[1].strip()
    return {
        "updated_at": data.get("updated_at"),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "comment_count": comment_count,
        "flags": flags,
    }


def source_repo_snapshot() -> dict:
    head = subprocess.run(
        ["git", "-C", str(SOURCE_REPO_LOCAL), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(SOURCE_REPO_LOCAL), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return {
        "head": head,
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "status_count": len(status.splitlines()),
    }


def write_evidence(root: Path, name: str, obj) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[evidence] {name}")


def _tool_calls(messages) -> list[dict]:
    calls = []
    for message in messages:
        for part in message.get("parts") or []:
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
            calls.append({"tool": part.get("tool"), "operation": operation, "status": state.get("status")})
    return calls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument(
        "--runtime-config",
        default=str(_DEFAULT_RUNTIME) if _DEFAULT_RUNTIME.is_file() else DEFAULT_RUNTIME_CONFIG,
    )
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--plan-ref", default=f"{GOVERNANCE_REPO}#{PLAN_ISSUE}")
    parser.add_argument("--source-repository", default="wzjcccc-dotcom/aota_reader_mcp")
    parser.add_argument("--project-id", default="aota-reader")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()

    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore

    run_root = Path(args.run_root).resolve()
    evidence_root = Path(args.evidence_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    active_worktree = run_root / "active-worktree"
    active_worktree.mkdir(parents=True, exist_ok=True)

    pre_gov = governance_snapshot(PLAN_ISSUE)
    pre_src = source_repo_snapshot()
    registry_sha = hashlib.sha256(Path(args.registry).read_bytes()).hexdigest()

    launcher = DailyTaskMainLauncher()
    started = time.time()
    ctx, s0 = launcher.launch(
        worktree_root=active_worktree,
        project_id=args.project_id,
        worktree_id="aota-reader-m1-intake",
        runtime_config_path=Path(args.runtime_config),
        initial_prompt=INTAKE_PROMPT,
        timeout_seconds=args.timeout_seconds,
        completion_timeout_seconds=60.0,
        max_productive_continuations=0,
        plan_ref=args.plan_ref,
        source_repository=args.source_repository,
        registry_path=args.registry,
    )
    elapsed = round(time.time() - started, 1)

    host = OpenCodeHostClient(REFERENCE_BASE_URL)
    tm_row = host.get_session(s0)
    tm_directory = str(tm_row.get("directory"))
    messages = host.fetch_session_messages(s0, directory=tm_directory)
    calls = _tool_calls(messages)
    successful_ops = [c["operation"] for c in calls if c["tool"] == "aota_aota_invoke" and c["status"] == "completed"]
    native = [c for c in calls if c["tool"] not in ("aota_aota_invoke", "invalid")]
    final_texts = [
        " ".join(p.get("text", "") for p in (m.get("parts") or []) if isinstance(p, dict) and p.get("type") == "text")
        for m in messages
        if (m.get("info") or {}).get("role") == "assistant"
    ]
    final_text = "\n".join(text for text in final_texts if text.strip())

    store = FileBackedExecutionStateStore(active_worktree / ".aota" / "execution.json")
    records = [
        {
            "canonical_task_id": record.canonical_task_id,
            "origin_session_ref": record.origin_session_ref.value if record.origin_session_ref else None,
            "state": record.canonical_task_state.value,
        }
        for record in store.list_all()
    ]

    # AF-side trusted binding projection (same trusted operator inputs as the
    # MCP child composition; deterministic registry-backed resolution).
    from aota_forge.composition.project_binding import resolve_trusted_project_binding

    binding = resolve_trusted_project_binding(
        project_id=args.project_id,
        source_repository=args.source_repository,
        registry_path=args.registry,
    )
    canonical_project_root = binding.resolution.candidates[0].project_root

    post_gov = governance_snapshot(PLAN_ISSUE)
    post_src = source_repo_snapshot()
    registry_sha_after = hashlib.sha256(Path(args.registry).read_bytes()).hexdigest()
    live_approval = post_gov["flags"].get("M1_USER_APPROVAL_SATISFIED", "")

    intake_report = {
        "record": "AF #56 M3/W3 real #39 Plan intake & approval-gate stop",
        "plan_ref": args.plan_ref,
        "reference_host": host.probe_versions(),
        "elapsed_seconds": elapsed,
        "task_main": {
            "session_id": s0,
            "instance_directory": tm_directory,
            "session_row_metadata": tm_row.get("metadata"),
            "operations_completed": successful_ops,
            "native_tool_calls": len(native),
            "final_text_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
            "final_text_excerpt": final_text[:1200],
        },
        "project_binding": {
            "project_id": binding.project_id,
            "source_repository": binding.source_repository,
            "canonical_project_root": canonical_project_root,
            "repository_identity": binding.repository_identity,
        },
        "governance": {
            "pre": pre_gov,
            "post": post_gov,
            "live_m1_user_approval_satisfied": live_approval,
            "body_unchanged": pre_gov["body_sha256"] == post_gov["body_sha256"],
            "comment_count_unchanged": pre_gov["comment_count"] == post_gov["comment_count"],
        },
        "source_repo": {
            "pre": pre_src,
            "post": post_src,
            "unchanged": pre_src["head"] == post_src["head"]
            and pre_src["status_sha256"] == post_src["status_sha256"],
        },
        "worker_records": records,
        "assertions": {
            "REAL_39_PLAN_INTAKE": any(op == "github.issue.read" for op in successful_ops),
            "REAL_PROJECT_ID": binding.project_id == "aota-reader",
            "REAL_SOURCE_REPOSITORY_GROUNDED": bool(binding.repository_identity)
            and binding.repository_identity.get("verified") is True,
            "CANONICAL_PROJECT_ROOT_RESOLVED": canonical_project_root == str(SOURCE_REPO_LOCAL),
            "NO_WORKER_DISPATCH": len(records) == 0,
            "NO_TASK_START": "task.start" not in successful_ops,
            "NO_SOURCE_MUTATION": pre_src["head"] == post_src["head"]
            and pre_src["status_sha256"] == post_src["status_sha256"],
            "NO_GOVERNANCE_MUTATION": pre_gov["body_sha256"] == post_gov["body_sha256"]
            and pre_gov["comment_count"] == post_gov["comment_count"],
            "REGISTRY_UNCHANGED": registry_sha == registry_sha_after,
            "AOTA_ONLY_SURFACE": len(native) == 0,
            "STOPPED_AT_APPROVAL_GATE": live_approval == "no",
        },
    }
    write_evidence(evidence_root, "real-39-plan-intake.json", intake_report["task_main"])
    write_evidence(evidence_root, "real-39-project-binding.json", intake_report["project_binding"])
    write_evidence(evidence_root, "real-39-approval-gate-proof.json", intake_report)
    if live_approval != "no":
        print(f"LIVE APPROVAL STATE CHANGED: M1_USER_APPROVAL_SATISFIED={live_approval!r}; review live truth")
    blocking = {k: v for k, v in intake_report["assertions"].items() if not v}
    if blocking:
        print("ASSERTIONS FAILED:", json.dumps(blocking, indent=1))
        raise SystemExit(1)
    print("M3 real #39 cross-project intake PASS (stopped at approval gate)")


if __name__ == "__main__":
    main()
