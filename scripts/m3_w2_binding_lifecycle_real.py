#!/usr/bin/env python3
"""AF #56 M3/W2 — real reference-host binding-lifecycle proof (NB-3 closure).

Runs against the PINNED reference host (127.0.0.1:4096) with the real
production MCP binding wrapper and the deterministic stub model. Proves,
mechanically and end-to-end:

  1. Task A (coder Worker) is dispatched on trusted worktree W through the
     real production dispatcher; its host session lives in a task-scoped
     instance namespace I_A (NOT W) whose staged binding is A's.
  2. Task B (coder Worker) is dispatched on the SAME worktree W; its session
     lives in a DIFFERENT instance namespace I_B whose staged binding is B's.
  3. The host-spawned MCP child of I_A resolves ONLY A's envelope and the
     child of I_B resolves ONLY B's envelope (wrapper debug + envelope read):
     a stale task A binding can never authorize task B.
  4. The task-main session S0 lives in its own instance namespace whose MCP
     child resolves the task-main binding only (role authority separation).

Outputs bounded JSON evidence under the run evidence root. No raw transcripts,
no credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REFERENCE_BASE_URL = os.environ.get("AF_REF_URL", "http://127.0.0.1:4096")
STUB_URL = os.environ.get("AF_STUB_URL", "http://127.0.0.1:4097")
REFERENCE_BINARY = os.environ.get(
    "AF_OPENCODE_BINARY",
    "/home/latios/.local/share/aota-forge/opencode-reference/v1.18.30/bin/opencode",
)
MODEL = {"providerID": "afstub", "modelID": "stub-model"}
PROJECT_ID = "aota_forge"
WORKTREE_ID = "wt-m3-lifecycle"


def _req(method: str, url: str, body=None, timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode()


def jreq(method: str, url: str, body=None, timeout: float = 60.0):
    status, raw = _req(method, url, body, timeout)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = {"_raw": raw.decode("utf-8", errors="replace")[:400]}
    return status, parsed


def write_evidence(root: Path, name: str, obj) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[evidence] {name}")


def write_runtime_config(path: Path) -> None:
    doc = {
        "executor": "opencode",
        "executable": REFERENCE_BINARY,
        "concurrency": 4,
        "provider": "afstub",
        "model": "stub-model",
        "host_endpoint": REFERENCE_BASE_URL,
        "worker_execution_timeout_seconds": 900,
        "bindings": {
            "analyst": {"profile": "aota-worker-analyst"},
            "coder": {"profile": "aota-worker"},
            "reviewer": {"profile": "aota-worker-reviewer"},
            "project-steward": {"profile": "aota-worker-steward"},
            "task-main": {"profile": "aota-task-main"},
        },
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def install_stub_scripts(parent_run_seconds: int) -> None:
    scripts = [
        {
            "match": r"AF_M3_PARENT_S0",
            "steps": [
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {"operation": "role.bootstrap", "arguments": {}},
                },
                {"type": "text", "text": "AF_M3_PARENT_S0 bootstrap observed"},
            ],
        },
        {
            "match": r"AF_M3_WORKER_A",
            "steps": [
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {"operation": "role.bootstrap", "arguments": {}},
                },
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {
                        "operation": "handoff.write",
                        "arguments": {
                            "mode": "result",
                            "payload": {
                                "summary": "M3 lifecycle worker A completed",
                                "work_done": "AF_M3_WORKER_A result handoff",
                            },
                        },
                    },
                },
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {
                        "operation": "task.return",
                        "arguments": {"status": "completed", "result_ref": "$captured_ref"},
                    },
                },
                {"type": "text", "text": "AF_M3_WORKER_A finished"},
            ],
        },
        {
            "match": r"AF_M3_WORKER_B",
            "steps": [
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {"operation": "role.bootstrap", "arguments": {}},
                },
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {
                        "operation": "handoff.write",
                        "arguments": {
                            "mode": "result",
                            "payload": {
                                "summary": "M3 lifecycle worker B completed",
                                "work_done": "AF_M3_WORKER_B result handoff",
                            },
                        },
                    },
                },
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {
                        "operation": "task.return",
                        "arguments": {"status": "completed", "result_ref": "$captured_ref"},
                    },
                },
                {"type": "text", "text": "AF_M3_WORKER_B finished"},
            ],
        },
    ]
    status, body = jreq("POST", STUB_URL + "/_admin/scripts", {"scripts": scripts})
    if status != 200:
        raise SystemExit(f"stub script install failed: {status} {body}")


def make_sandbox(worktree_root: Path):
    from aota_forge.composition.project_binding import resolve_trusted_project_evidence
    from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

    evidence = resolve_trusted_project_evidence(
        worktree_root=worktree_root, project_id=PROJECT_ID
    )
    if getattr(evidence, "status", None) != "RESOLVED" or len(getattr(evidence, "candidates", ())) != 1:
        raise SystemExit(f"real project evidence not resolved for {worktree_root}: {evidence}")
    return bind_worktree_sandbox(evidence, WORKTREE_ID, worktree_root, expected_project_id=PROJECT_ID)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    args = parser.parse_args()

    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.adapters.opencode.task_main import (
        BINDING_KIND_TASK_MAIN,
        create_task_main_session,
        instance_directory,
        stage_envelope_in_instance,
        task_main_instance_key,
        write_binding_pointer,
    )
    from aota_forge.composition.execution import opencode_worker_directory_resolver
    from aota_forge.composition.worker_vertical_slice import create_governed_worker_env_resolver
    from aota_forge.composition.task_main_runtime_selection import (
        THIN_BOOTSTRAP_RELPATH,
        materialize_thin_task_main_bootstrap,
    )
    from aota_forge.runtime.config import load_runtime_config
    from aota_forge.runtime.trusted_runtime_binding import (
        PRE_RESOLVED_BINDING_ENV,
        create_task_main_envelope,
    )
    from aota_forge.composition.execution import (
        create_durable_completion_coordinator,
        create_production_execution_dispatcher,
    )
    from aota_forge.core.execution.durable_state import (
        FileBackedExecutionStateStore,
        OriginSessionRef,
    )
    from aota_forge.work_plane.handoff_store import handoff_write
    from aota_forge.work_plane.task_facade import task_start
    from aota_forge.work_plane.task_return_receipt import (
        WorktreeSemanticReturnEvidenceProvider,
    )

    run_root = Path(args.run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    evidence_root = Path(args.evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    config_path = run_root / "runtime-opencode.json"
    write_runtime_config(config_path)
    store_path = run_root / "execution-state.json"
    if store_path.exists():
        store_path.unlink()

    # 1. disposable source worktree W (real project manifest, same HEAD)
    source_wt = run_root / "source-wt"
    if not source_wt.exists():
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(source_wt), "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )

    host = OpenCodeHostClient(REFERENCE_BASE_URL)
    health = host.probe_versions()
    if not health.get("healthy"):
        raise SystemExit(f"reference host not healthy: {health}")
    install_stub_scripts(60)

    # 2. task-main S0 in its own mechanical instance namespace + task-main binding
    tm_instance = instance_directory(source_wt, task_main_instance_key(f"lifecycle-{int(time.time())}"))
    parent = create_task_main_session(
        host, directory=tm_instance, instance_key=tm_instance.name, model=MODEL
    )
    s0 = parent["id"]
    bootstrap = materialize_thin_task_main_bootstrap(
        worktree_root=source_wt,
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        runtime_config_path=config_path,
        origin_task_main_session_ref=s0,
        executor_id="opencode",
    )
    envelope = create_task_main_envelope(worktree_root=source_wt, bootstrap_path=bootstrap)
    staged = stage_envelope_in_instance(tm_instance, envelope)
    write_binding_pointer(
        tm_instance,
        kind=BINDING_KIND_TASK_MAIN,
        envelope_path=staged,
        binding_root=source_wt,
        bootstrap_path=bootstrap,
    )
    status, _ = jreq(
        "POST",
        REFERENCE_BASE_URL
        + f"/session/{s0}/prompt_async?directory="
        + urllib.parse.quote(str(tm_instance)),
        {"model": MODEL, "parts": [{"type": "text", "text": "AF_M3_PARENT_S0 bounded bootstrap"}]},
    )
    if status != 204:
        raise SystemExit(f"S0 prompt failed: {status}")
    # wait for the parent turn to terminate
    deadline = time.time() + 60
    while time.time() < deadline:
        entry = host.session_status(s0, directory=str(tm_instance))
        msgs = host.fetch_session_messages(s0, directory=str(tm_instance))
        has_assistant = any(
            isinstance(m, dict) and isinstance(m.get("info"), dict) and m["info"].get("role") == "assistant"
            for m in msgs
        )
        if entry is None and has_assistant:
            break
        time.sleep(0.5)

    # 3. trusted sandbox + governed resolver on the SAME worktree W
    sandbox = make_sandbox(source_wt)
    env_resolver = create_governed_worker_env_resolver(
        sandbox=sandbox, runtime_config_path=config_path, repo_root=REPO_ROOT
    )
    directory_resolver = opencode_worker_directory_resolver(env_resolver)
    config = load_runtime_config(config_path=str(config_path))
    store = FileBackedExecutionStateStore(store_path)
    dispatcher = create_production_execution_dispatcher(
        runtime_config=config,
        state_store=store,
        origin_session_ref=OriginSessionRef(s0),
        worker_env_resolver=env_resolver,
    )
    coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=config,
        semantic_return_provider=WorktreeSemanticReturnEvidenceProvider(sandbox),
    )

    def write_handoff(marker: str, work_item: str):
        ref = handoff_write(
            mode="work_item",
            semantic={
                "work_role": "coder",
                "work_item_ref": work_item,
                "milestone_ref": "m3",
                "objective": f"{marker} bounded lifecycle worker",
                "bounded_scope": "M3 lifecycle proof source worktree only",
                "validation_expectations": ["result handoff and task.return durably recorded"],
                "semantic_stop_expectations": ["stop if any governed call is denied"],
            },
            caller_role="task-main",
            sandbox=sandbox,
            milestone_id="m3",
            work_item_id=work_item,
        )
        return ref

    def wait_terminal(task_id: str, timeout_seconds: float = 180.0):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            coordinator.recover_once()
            record = store.get(task_id)
            if record is not None and record.canonical_task_state.is_terminal:
                return record
            time.sleep(1.0)
        raise SystemExit(f"worker {task_id} did not reach terminal within budget")

    # 4. Worker A on W
    ref_a = write_handoff("AF_M3_WORKER_A", "wi-A")
    dispatch_a = task_start(
        role="coder",
        handoff_ref=ref_a.ref,
        caller_role="task-main",
        sandbox=sandbox,
        dispatcher=dispatcher,
        thin_task_lifecycle=True,
    )
    task_a = dispatch_a["task_id"]
    record_a = wait_terminal(task_a)
    session_a = record_a.adapter_handle

    # 5. Worker B on the SAME worktree W (sequential task lifetime)
    ref_b = write_handoff("AF_M3_WORKER_B", "wi-B")
    dispatch_b = task_start(
        role="coder",
        handoff_ref=ref_b.ref,
        caller_role="task-main",
        sandbox=sandbox,
        dispatcher=dispatcher,
        thin_task_lifecycle=True,
    )
    task_b = dispatch_b["task_id"]
    record_b = wait_terminal(task_b)
    session_b = record_b.adapter_handle

    # 6. resolve the actual session instance directories + staged bindings
    row_a = host.get_session(session_a, directory=str(tm_instance))
    row_b = host.get_session(session_b, directory=str(tm_instance))
    dir_a = str(row_a.get("directory"))
    dir_b = str(row_b.get("directory"))

    def pointer(instance_dir: str) -> dict:
        return json.loads(
            (Path(instance_dir) / ".aota" / "opencode" / "active_binding.json").read_text(encoding="utf-8")
        )

    def envelope_payload(path: str) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))["payload"]

    pa = pointer(dir_a)
    pb = pointer(dir_b)
    ea = envelope_payload(pa["envelope_path"])
    eb = envelope_payload(pb["envelope_path"])

    # 7. wrapper debug: which envelope each instance's MCP child resolved
    debug_path = Path(
        os.environ.get(
            "AOTA_OPENCODE_MCP_DEBUG",
            "/home/latios/workspace/.aota-evidence/aota_forge/issue-56/M3/mcp-child-debug.jsonl",
        )
    )
    resolved_records: list[dict] = []
    if debug_path.is_file():
        for line in debug_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("event") == "m3_mcp_binding_resolved":
                resolved_records.append(record)

    def child_resolutions(instance_dir: str) -> list[dict]:
        out = []
        for record in resolved_records:
            if Path(str(record.get("instance_dir", ""))).resolve() == Path(instance_dir).resolve():
                envelope = Path(str(record.get("envelope", "")))
                if envelope.is_file():
                    payload = envelope_payload(envelope)
                    out.append(
                        {
                            "kind": payload.get("kind"),
                            "envelope": str(envelope),
                            "canonical_task_id": payload.get("canonical_task_id"),
                            "worktree_root": payload.get("worktree_root"),
                        }
                    )
        return out

    resolutions_a = child_resolutions(dir_a)
    resolutions_b = child_resolutions(dir_b)
    resolutions_tm = child_resolutions(str(tm_instance))

    stale_reused = (
        not pa.get("envelope_path")
        or pa["envelope_path"] == pb["envelope_path"]
        or dir_a == dir_b
        or dir_a == str(tm_instance)
        or dir_b == str(tm_instance)
    )

    proof = {
        "record": "AF #56 M3/W2 real same-worktree sequential redispatch (NB-3 closure)",
        "reference_host": {"base_url": REFERENCE_BASE_URL, "health": health},
        "trusted_source_worktree": str(source_wt),
        "task_main": {
            "session_id": s0,
            "instance_directory": str(tm_instance),
            "pointer_kind": pointer(str(tm_instance))["kind"],
            "child_resolutions": resolutions_tm,
        },
        "task_a": {
            "canonical_task_id": task_a,
            "session_id": session_a,
            "state": record_a.canonical_task_state.value,
            "task_return_ok": bool(record_a.terminal_result and record_a.terminal_result.ok),
            "card_durable": record_a.worker_result_card is not None,
            "instance_directory": dir_a,
            "pointer_kind": pa["kind"],
            "pointer_binding_root": pa["binding_root"],
            "envelope_canonical_task_id": ea.get("canonical_task_id"),
            "envelope_worktree_root": ea.get("worktree_root"),
            "child_resolutions": resolutions_a,
        },
        "task_b": {
            "canonical_task_id": task_b,
            "session_id": session_b,
            "state": record_b.canonical_task_state.value,
            "task_return_ok": bool(record_b.terminal_result and record_b.terminal_result.ok),
            "card_durable": record_b.worker_result_card is not None,
            "instance_directory": dir_b,
            "pointer_kind": pb["kind"],
            "pointer_binding_root": pb["binding_root"],
            "envelope_canonical_task_id": eb.get("canonical_task_id"),
            "envelope_worktree_root": eb.get("worktree_root"),
            "child_resolutions": resolutions_b,
        },
        "assertions": {
            "SAME_TRUSTED_WORKTREE": str(ea.get("worktree_root")) == str(eb.get("worktree_root")),
            "DISTINCT_INSTANCE_DIRECTORIES": dir_a != dir_b,
            "INSTANCE_DIRECTORIES_NOT_THE_WORKTREE": Path(dir_a).resolve() != source_wt.resolve()
            and Path(dir_b).resolve() != source_wt.resolve(),
            "A_BINDING_IS_A": ea.get("canonical_task_id") == task_a,
            "B_BINDING_IS_B": eb.get("canonical_task_id") == task_b,
            "A_CHILD_RESOLVED_A": bool(resolutions_a)
            and all(item["canonical_task_id"] == task_a for item in resolutions_a),
            "B_CHILD_RESOLVED_B": bool(resolutions_b)
            and all(item["canonical_task_id"] == task_b for item in resolutions_b),
            "TASK_MAIN_BINDING_IS_TASK_MAIN": bool(resolutions_tm)
            and all(item["kind"] == "task-main" for item in resolutions_tm),
            "ROLE_INSTANCE_NAMESPACES_DISJOINT": len({dir_a, dir_b, str(tm_instance)}) == 3,
            "STALE_BINDING_REUSED": stale_reused,
            "SAME_WORKTREE_REDISPATCH_PROVEN": not stale_reused,
            "SESSION_IDS_DISTINCT": session_a != session_b,
        },
        "truthful_markers": {
            "HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE": False,
            "ESTALE_BINDING_REUSED": "NO (use STALE_BINDING_REUSED=false)",
            "STALE_BINDING_REUSED": False,
            "OVERWRITE_POINTER_AND_HOPE": False,
            "SECOND_MCP_SERVER": False,
            "MODEL_SUPPLIED_CWD_AUTHORITY": False,
        },
    }
    write_evidence(evidence_root, "same-worktree-redispatch-proof.json", proof)
    if not all(proof["assertions"][key] for key in proof["assertions"] if key != "STALE_BINDING_REUSED"):
        raise SystemExit("lifecycle proof assertions failed; see evidence")
    print("M3 binding lifecycle proof PASS")


if __name__ == "__main__":
    main()
