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
import hashlib
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
TRANSPORT_SUCCESS_ALONE_SUFFICIENT = False
AOTA_INVOKE_LOGICAL_OK_REQUIRED_FOR_SUCCESS = True

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


def _logical_aota_result(state: dict) -> dict | None:
    """Resolve only the bounded canonical result representation from a tool part."""
    for key in ("structuredContent", "structured_content"):
        candidate = state.get(key)
        if isinstance(candidate, dict):
            return candidate
    output = state.get("output")
    if not isinstance(output, str) or len(output.encode("utf-8")) > 64 * 1024:
        return None
    try:
        candidate = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _aota_invoke_result_is_valid(operation: object, result: object) -> bool:
    """Validate the bounded canonical result or its by_ref transport summary."""
    if not isinstance(result, dict):
        return False
    if not isinstance(operation, str) or result.get("operation") != operation:
        return False
    logical_ok = result.get("ok")
    if type(logical_ok) is not bool:
        return False
    expected_outcome = "success" if logical_ok else "failure"
    if result.get("outcome") != expected_outcome:
        return False
    if "is_success" in result and result.get("is_success") is not logical_ok:
        return False
    output_mode = result.get("output_mode")
    if not isinstance(output_mode, str) or output_mode not in {"inline", "by_ref"}:
        return False
    digest = result.get("output_digest", result.get("digest"))
    byte_length = result.get("output_byte_length", result.get("byte_length"))
    if not _is_lower_sha256(digest):
        return False
    if type(byte_length) is not int or byte_length < 0:
        return False

    if output_mode == "inline":
        if logical_ok and not isinstance(result.get("payload"), dict):
            return False
        if not logical_ok:
            error = result.get("error")
            error_code = result.get("error_code")
            if isinstance(error, dict):
                error_code = error.get("code")
            if not isinstance(error_code, str) or not error_code:
                return False
        if "is_truncated" in result and type(result.get("is_truncated")) is not bool:
            return False
        if "complete" in result and type(result.get("complete")) is not bool:
            return False
        return True

    if not logical_ok:
        return False

    output_ref = result.get("output_ref")
    hydration = result.get("hydration")
    if not isinstance(output_ref, dict) or not isinstance(hydration, dict):
        return False
    required_ref = ("ref", "digest", "project_id", "worktree_id", "byte_length")
    if not all(output_ref.get(key) is not None for key in required_ref):
        return False
    if not isinstance(output_ref.get("ref"), str) or not output_ref["ref"]:
        return False
    if not _is_lower_sha256(output_ref.get("digest")):
        return False
    if not isinstance(output_ref.get("project_id"), str) or not output_ref["project_id"]:
        return False
    if not isinstance(output_ref.get("worktree_id"), str) or not output_ref["worktree_id"]:
        return False
    ref_length = output_ref.get("byte_length")
    if type(ref_length) is not int or ref_length < 0:
        return False
    claims = hydration.get("arguments")
    if hydration.get("operation") != "result.hydrate" or not isinstance(claims, dict):
        return False
    if any(claims.get(key) != output_ref.get(key) for key in required_ref):
        return False
    return True


def _aota_invoke_result_succeeded(
    transport_status: object,
    operation: object,
    result: object,
) -> bool:
    """Accept transport completion only with a valid logical AOTA success."""
    return (
        transport_status == "completed"
        and _aota_invoke_result_is_valid(operation, result)
        and isinstance(result, dict)
        and result.get("ok") is True
    )


def _safe_result_evidence(
    transport_status: object,
    operation: object,
    result: dict | None,
) -> dict:
    valid = _aota_invoke_result_is_valid(operation, result)
    digest = None
    if isinstance(result, dict):
        digest = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    error = result.get("error") if isinstance(result, dict) else None
    error_code = result.get("error_code") if isinstance(result, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        error_code = error["code"]
    return {
        "structured_result_present": isinstance(result, dict),
        "canonical_result_valid": valid,
        "logical_ok": result.get("ok") if isinstance(result, dict) and type(result.get("ok")) is bool else None,
        "accepted_success": _aota_invoke_result_succeeded(
            transport_status, operation, result
        ),
        "outcome": result.get("outcome") if isinstance(result, dict) and isinstance(result.get("outcome"), str) else None,
        "output_mode": result.get("output_mode") if isinstance(result, dict) and isinstance(result.get("output_mode"), str) else None,
        "error_code": error_code if isinstance(error_code, str) else None,
        "result_sha256": digest,
    }


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
                    transport_status = state.get("status")
                    logical_result = _logical_aota_result(state)
                    first_tool = {
                        "tool": part.get("tool"),
                        "operation": operation,
                        "status": transport_status,
                        "logical_result": _safe_result_evidence(
                            transport_status, operation, logical_result
                        ),
                    }
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
            "session_id_sha256": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
            "session_row_agent": row.get("agent"),
            "headless_instance_namespace_verified": "/.aota/opencode/instances/task-main-" in directory,
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
                and first_tool.get("operation") == "role.bootstrap"
                and first_tool.get("status") == "completed",
                "AOTA_INVOKE_LOGICAL_OK_REQUIRED_FOR_SUCCESS": AOTA_INVOKE_LOGICAL_OK_REQUIRED_FOR_SUCCESS,
                "HEADLESS_ROLE_BOOTSTRAP_LOGICAL_SUCCESS": first_tool is not None
                and first_tool.get("logical_result", {}).get("canonical_result_valid") is True,
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
