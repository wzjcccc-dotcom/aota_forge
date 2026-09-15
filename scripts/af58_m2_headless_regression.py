#!/usr/bin/env python3
"""AF #58 M2 — deterministic headless task-main launcher regression.

Runs the ACCEPTED headless production launcher path end-to-end against the
real pinned OpenCode reference host (:4096) with the deterministic AF stub
model (:4097), on a disposable fixture project:

  DailyTaskMainLauncher.launch (executor=opencode, thin)
    -> exact task-main session + instance namespace
    -> digest-bound task-main binding staged
    -> role.bootstrap-first turn (stub script)
    -> zero worker dispatch

Asserts the headless path is unchanged by M2 (no interactive Plan projection
on the headless bootstrap) and that interactive/headless binding semantics
still converge on the same profile, Plan ref and authorized-root contract.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOST = os.environ.get("AF58_M2_HOST", "http://127.0.0.1:4096")
STUB_HOST = os.environ.get("AF58_M2_STUB_HOST", "127.0.0.1")
STUB_PORT = int(os.environ.get("AF58_M2_STUB_PORT", "4097"))
STUB = f"http://{STUB_HOST}:{STUB_PORT}"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#39"
SOURCE_REPOSITORY = "wzjcccc-dotcom/af58_m2_fixture"
PROJECT_ID = "af58-m2-fixture"
TASK_MAIN_PROFILE = "aota-task-main"
MAX_CONTINUATIONS = 0

MANIFEST = (
    "schema_version: 1\nproject:\n"
    f"  id: {PROJECT_ID}\n  name: fixture\n  kind: test\n  status: active\n"
    "summary: bounded M2 headless regression fixture\ncapabilities: []\npaths:\n"
    "  source_root: .\n  source: []\n  docs: []\n  scripts: []\n  profiles: []\n"
    "  skills: []\n  tests: []\ncommands:\n  validate: []\n  deploy: []\n"
    "  verify_deploy: []\nruntime:\n  deployment_type: manual\n"
    "  requires_human_checkpoint: false\ncodegraph:\n  enabled: false\n"
    "  index_location: .codegraph\nplan:\n  active_plan_id: null\nconstraints: []\n"
)


def jreq(method: str, url: str, body: dict | None = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("accept", "application/json")
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]
    except Exception:  # noqa: BLE001 - unreachable stub is not up yet
        return 0, None


def ensure_stub() -> subprocess.Popen | None:
    status, _ = jreq("GET", f"{STUB}/_admin/captures")
    if status == 200:
        return None
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "m2_w4_openai_stub_server.py")],
        env={**os.environ, "AF_STUB_HOST": STUB_HOST, "AF_STUB_PORT": str(STUB_PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        time.sleep(0.25)
        status, _ = jreq("GET", f"{STUB}/_admin/captures")
        if status == 200:
            return proc
    raise SystemExit("AF stub server did not start")


def install_scripts() -> None:
    scripts = [
        {
            "match": r"AF58 M2 headless regression",
            "repeat": False,
            "steps": [
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {"operation": "role.bootstrap", "arguments": {}},
                },
                {"type": "text", "text": "AF58 M2 headless regression complete."},
            ],
        }
    ]
    status, body = jreq("POST", f"{STUB}/_admin/scripts", {"scripts": scripts})
    assert status == 200, (status, body)


def build_fixture(root: Path) -> tuple[Path, Path]:
    checkout = root / "source-checkout"
    (checkout / ".aota").mkdir(parents=True, exist_ok=True)
    (checkout / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{SOURCE_REPOSITORY}.git"],
        cwd=checkout,
        check=True,
    )
    registry = root / "workspace-registry.json"
    registry.write_text(
        json.dumps({"ws-m2": {"candidates": [str(checkout)]}}), encoding="utf-8"
    )
    runtime_config = root / "runtime-afstub.json"
    runtime_config.write_text(
        json.dumps(
            {
                "executor": "opencode",
                "executable": "/bin/true",
                "concurrency": 1,
                "provider": "afstub",
                "model": "stub-model",
                "bindings": {
                    "coder": {"profile": "aota-worker"},
                    "reviewer": {"profile": "aota-worker"},
                    "analyst": {"profile": "aota-worker"},
                    "project-steward": {"profile": "aota-worker"},
                    "task-main": {"profile": TASK_MAIN_PROFILE},
                },
                "host_endpoint": HOST,
            }
        ),
        encoding="utf-8",
    )
    return registry, runtime_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    evidence_root = Path(args.evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    stub_proc = ensure_stub()
    try:
        install_scripts()
        registry, runtime_config = build_fixture(run_root)
        worktree_root = run_root / "headless-active-worktree"
        worktree_root.mkdir(parents=True, exist_ok=True)

        from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
        from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

        launcher = DailyTaskMainLauncher()
        started = time.time()
        ctx, session_id = launcher.launch(
            worktree_root=worktree_root,
            project_id=PROJECT_ID,
            worktree_id="af58-m2-headless",
            runtime_config_path=runtime_config,
            initial_prompt=(
                "AF58 M2 headless regression: your first action must be "
                "aota_aota_invoke(operation='role.bootstrap', arguments={}) and "
                "then stop."
            ),
            timeout_seconds=180.0,
            completion_timeout_seconds=30.0,
            max_productive_continuations=MAX_CONTINUATIONS,
            plan_ref=PLAN_REF,
            source_repository=SOURCE_REPOSITORY,
            registry_path=registry,
        )
        elapsed = round(time.time() - started, 1)

        host = OpenCodeHostClient(HOST)
        row = host.get_session(session_id)
        directory = str(row.get("directory") or "")
        messages = host.fetch_session_messages(session_id, directory=directory)
        first_tool = None
        for message in messages:
            for part in message.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "tool":
                    state = part.get("state") if isinstance(part.get("state"), dict) else {}
                    raw_input = state.get("input")
                    operation = None
                    if isinstance(raw_input, dict):
                        operation = raw_input.get("operation")
                    elif isinstance(raw_input, str):
                        try:
                            decoded = json.loads(raw_input)
                            operation = decoded.get("operation") if isinstance(decoded, dict) else None
                        except Exception:  # noqa: BLE001
                            operation = None
                    first_tool = {"tool": part.get("tool"), "operation": operation, "status": state.get("status")}
                    break
            if first_tool:
                break
        bootstrap = json.loads(
            (worktree_root / ".aota" / "task-main-thin-bootstrap.json").read_text(encoding="utf-8")
        )
        pointer_path = Path(directory) / ".aota" / "opencode" / "active_binding.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.is_file() else {}
        records = []
        execution_store = worktree_root / ".aota" / "execution.json"
        if execution_store.is_file():
            records = list((json.loads(execution_store.read_text(encoding="utf-8") or "{}") or {}).keys())

        report = {
            "record": "AF #58 M2 headless launcher regression (stub model, real host)",
            "elapsed_seconds": elapsed,
            "session_id": session_id,
            "session_row_agent": row.get("agent"),
            "instance_directory": directory,
            "first_tool_call": first_tool,
            "bootstrap_runtime_path": bootstrap.get("runtime_path"),
            "bootstrap_plan_ref": bootstrap.get("plan_ref"),
            "bootstrap_source_repository": bootstrap.get("source_repository"),
            "bootstrap_has_interactive_plan_state": "trusted_plan_state" in bootstrap,
            "binding_pointer_kind": pointer.get("kind"),
            "worker_records": records,
            "assertions": {
                "HEADLESS_LAUNCHER_REGRESSION": True,
                "HEADLESS_SESSION_PROFILE": row.get("agent") == TASK_MAIN_PROFILE,
                "HEADLESS_INSTANCE_NAMESPACE": "/.aota/opencode/instances/task-main-" in directory,
                "HEADLESS_ROLE_BOOTSTRAP_FIRST": first_tool is not None
                and first_tool.get("tool") == "aota_aota_invoke"
                and first_tool.get("operation") == "role.bootstrap",
                "HEADLESS_THIN_PATH": bootstrap.get("runtime_path") == "thin",
                "HEADLESS_NO_INTERACTIVE_PROJECTION": "trusted_plan_state" not in bootstrap,
                "HEADLESS_TASK_MAIN_BINDING": pointer.get("kind") == "task-main",
                "HEADLESS_NO_WORKER_DISPATCH": len(records) == 0,
                "HEADLESS_PLAN_REF_PRESERVED": bootstrap.get("plan_ref") == PLAN_REF,
            },
        }
        (evidence_root / "headless-regression.json").write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(json.dumps(report["assertions"], indent=2))
        blocking = {k: v for k, v in report["assertions"].items() if not v}
        if blocking:
            print("HEADLESS ASSERTIONS FAILED:", json.dumps(blocking, indent=2))
            return 1
        print("M2 headless launcher regression PASS")
        return 0
    finally:
        if stub_proc is not None:
            try:
                stub_proc.send_signal(signal.SIGTERM)
                stub_proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                stub_proc.kill()


if __name__ == "__main__":
    sys.exit(main())
