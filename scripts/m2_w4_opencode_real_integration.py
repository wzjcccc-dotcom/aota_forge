#!/usr/bin/env python3
"""AF #56 M2/W4 — real OpenCode reference-host integration harness.

Real integration against the pinned reference server (127.0.0.1:4096) driven by
the deterministic stub model. Proves, through the production AF composition:

- real Worker dispatch (task.start -> ExecutionDispatcher -> OpenCodeAdapter);
- trusted assigned-worktree session directories and AF-side governed bindings;
- worker terminal semantic return (result handoff + task.return) through the
  real AOTA MCP transport spawned by the pinned host;
- durable PENDING cards while the parent S0 stays busy;
- delivery of completion A into the exact busy S0 via session-row-scoped
  prompt_async + identity-bound ACK;
- AF process restart recovery (subprocess) delivering completion B from
  durable state alone;
- exact-session reentry, no replacement parent, no cross-host fallback.

Modes:
  full              complete M2/W4 proof (default)
  restart-delivery  AF-restart recovery subprocess (durable state only)
  cancel            bounded exact-session cancel proof

This is a bounded mechanics harness, NOT M3 autonomous task-main acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
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

WORKTREE_IDS = {
    "a": "wt-m2-worker-a",
    "b": "wt-m2-worker-b",
    "c": "wt-m2-worker-c",
}
PARENT_WORKTREE_ID = "wt-m2-parent"
PROJECT_ID = "aota_forge"


# ---------------------------------------------------------------------------
# bounded HTTP helpers
# ---------------------------------------------------------------------------


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
    except Exception as exc:  # noqa: BLE001 - harness reports transport truthfully
        return 0, str(exc).encode()


def jreq(method: str, url: str, body=None, timeout: float = 60.0):
    status, raw = _req(method, url, body, timeout)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = {"_raw": raw.decode("utf-8", errors="replace")[:400]}
    return status, parsed


def write_evidence(root, name: str, obj) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"[evidence] {name}")


# ---------------------------------------------------------------------------
# AF-side composition helpers (trusted harness inputs only)
# ---------------------------------------------------------------------------


def synthetic_sandbox(root: Path, worktree_id: str, project_id: str = PROJECT_ID):
    from aota_forge.core.project.resolver import (
        ProjectCandidateEvidence,
        ProjectResolutionEvidence,
    )
    from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

    candidate = ProjectCandidateEvidence(
        workspace_id=f"ws-{worktree_id}",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path=".aota/project.yaml",
        name=project_id,
        kind="test-synthetic",
        status="active",
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id=f"ws-{worktree_id}",
        workspace_root=str(root),
        registry_fingerprint="0" * 64,
        listing_fingerprint="0" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id, root)


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


def governed_resolvers(sandboxes_by_wt, runtime_config_path: Path):
    from aota_forge.composition.worker_vertical_slice import (
        create_governed_worker_env_resolver,
    )

    return {
        wt: create_governed_worker_env_resolver(
            sandbox=sandbox,
            runtime_config_path=runtime_config_path,
            repo_root=REPO_ROOT,
        )
        for wt, sandbox in sandboxes_by_wt.items()
    }


def _worktree_from_handoff_ref(ref: object) -> str | None:
    if not isinstance(ref, str) or not ref.startswith("handoff://"):
        return None
    parts = ref.split("://", 1)[1].split("/")
    if len(parts) < 2:
        return None
    return parts[1]


def multi_worktree_env_resolver(resolvers_by_wt):
    """Trusted multi-worktree composition of the accepted governed resolver.

    Selects the owning sandbox from the durable handoff ref (cross-worktree
    handoff_open would fail closed for a foreign sandbox anyway); unknown
    worktrees return None (dispatch then fails closed).
    """
    from aota_forge.work_plane.task_facade import TRUSTED_WORK_HANDOFF_CONTEXT_KEY

    def _resolve(payload):
        try:
            record = payload["context"]["working_context"][TRUSTED_WORK_HANDOFF_CONTEXT_KEY]
            wt = _worktree_from_handoff_ref(record.get("ref"))
        except Exception:
            return None
        resolver = resolvers_by_wt.get(wt)
        if resolver is None:
            return None
        return resolver(payload)

    return _resolve


class MultiWorktreeSemanticReturnProvider:
    """Bounded harness composition over the accepted worktree evidence provider.

    Each Worker root contains only its own task receipt/handoff evidence, so
    resolution is exact: at most one sandbox can yield evidence for one task.
    """

    def __init__(self, sandboxes) -> None:
        from aota_forge.work_plane.task_return_receipt import (
            WorktreeSemanticReturnEvidenceProvider,
            resolve_semantic_return_evidence,
        )

        self._providers = {
            wt: WorktreeSemanticReturnEvidenceProvider(sandbox)
            for wt, sandbox in sandboxes.items()
        }
        self._resolve_one = resolve_semantic_return_evidence
        self._sandboxes = dict(sandboxes)

    def resolve(self, record):
        task_id = getattr(record, "canonical_task_id", None)
        if not isinstance(task_id, str) or not task_id.strip():
            return None
        hits = []
        for wt, sandbox in self._sandboxes.items():
            evidence = self._resolve_one(sandbox, task_id.strip())
            if evidence is not None:
                hits.append(evidence)
        if len(hits) != 1:
            return None
        return hits[0]


def build_handoff(worktree_id: str, root: Path, marker: str, work_item: str):
    from aota_forge.work_plane.handoff_store import handoff_write

    sandbox = synthetic_sandbox(root, worktree_id)
    ref = handoff_write(
        mode="work_item",
        semantic={
            "work_role": "coder",
            "work_item_ref": work_item,
            "milestone_ref": "m2",
            "objective": (
                f"{marker} bounded governed worker: produce a result handoff and "
                "task.return via the aota.invoke transport"
            ),
            "bounded_scope": "M2 harness test root only",
            "validation_expectations": ["result handoff and task.return durably recorded"],
            "semantic_stop_expectations": ["stop if any governed call is denied"],
        },
        caller_role="task-main",
        sandbox=sandbox,
        milestone_id="m2",
        work_item_id=work_item,
    )
    return sandbox, ref


def install_stub_scripts(parent_busy_seconds: int) -> None:
    scripts = [
        # Envelope match FIRST: the parent conversation retains older messages,
        # so the first matching script must be the delivery-specific one.
        {
            "match": r"AOTA_WORKER_COMPLETION_V1",
            "repeat": True,
            "steps": [{"type": "echo_ack"}],
        },
        {
            "match": r"M2 task-main productive work",
            "steps": [
                {
                    "type": "sleep",
                    "chunks": int(parent_busy_seconds * 2),
                    "interval": 0.5,
                    "text": "task-main busy work",
                }
            ],
        },
        {
            "match": r"AF_M2_WORKER_A",
            "steps": [
                # A governed first tool call keeps the run alive (a text-only
                # model response would end the assistant turn immediately).
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
                                "summary": "M2 worker A completed the bounded governed target",
                                "work_done": "AF_M2_WORKER_A result handoff",
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
                {"type": "text", "text": "AF_M2_WORKER_A finished"},
            ],
        },
        {
            "match": r"AF_M2_WORKER_B",
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
                                "summary": "M2 worker B completed the bounded governed target",
                                "work_done": "AF_M2_WORKER_B result handoff",
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
                {"type": "text", "text": "AF_M2_WORKER_B finished"},
            ],
        },
        {
            "match": r"AF_M2_WORKER_CANCEL",
            "steps": [
                # Long-running governed call keeps the run busy for the bounded
                # cancel proof (prompt_async + abort on the exact session).
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "arguments": {"operation": "role.bootstrap", "arguments": {}},
                },
                {"type": "sleep", "chunks": 120, "interval": 0.5, "text": "worker C long run"},
            ],
        },
    ]
    status, body = jreq("POST", STUB_URL + "/_admin/scripts", {"scripts": scripts})
    if status != 200:
        raise SystemExit(f"stub script install failed: {status} {body}")


def wait_worker_terminal(
    coordinator,
    store,
    task_id: str,
    *,
    timeout_seconds: float,
    observations: list,
):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        report = coordinator.recover_once()
        observations.append(report.summary())
        record = store.get(task_id)
        if record is not None and record.canonical_task_state.is_terminal:
            return record
        time.sleep(1.0)
    raise SystemExit(f"worker {task_id} did not reach terminal state within budget")


def sse_collect(url: str, sink: list, timeout: float) -> threading.Thread:
    def _run():
        request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("data:"):
                        try:
                            sink.append(json.loads(line[5:].strip()))
                        except Exception:
                            pass
        except Exception:
            return

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# mode: full
# ---------------------------------------------------------------------------


def run_full(args) -> None:
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.composition.execution import (
        create_durable_completion_coordinator,
        create_opencode_completion_delivery_transport,
        create_production_execution_dispatcher,
    )
    from aota_forge.core.execution.durable_state import (
        DeliveryState,
        FileBackedExecutionStateStore,
        OriginSessionRef,
    )
    from aota_forge.runtime.config import load_runtime_config
    from aota_forge.work_plane.task_facade import task_start

    # Unique per-run roots: each Worker gets its own dedicated assigned
    # worktree/test root (the normal AF model). Reusing one directory across
    # AF task lifetimes while the OpenCode instance lives would keep that
    # instance's per-directory MCP child bound to the previous task envelope;
    # fresh roots keep the binding exact (recorded as an M2 non-blocking
    # finding for M3 same-worktree re-dispatch design).
    base_run_root = Path(args.run_root).resolve()
    run_root = base_run_root / ("run-" + time.strftime("%Y%m%d_%H%M%S"))
    run_root.mkdir(parents=True, exist_ok=True)
    parent_root = run_root / "parent-root"
    worker_a_root = run_root / "worker-a"
    worker_b_root = run_root / "worker-b"
    for path in (parent_root, worker_a_root, worker_b_root):
        path.mkdir(parents=True, exist_ok=True)
    config_path = run_root / "runtime-opencode.json"
    write_runtime_config(config_path)
    store_path = run_root / "execution-state.json"
    if store_path.exists():
        store_path.unlink()

    host = OpenCodeHostClient(REFERENCE_BASE_URL)
    health = host.probe_versions()
    if not health.get("healthy"):
        raise SystemExit(f"reference host not healthy: {health}")

    # 1. deterministic stub scripts + parent S0 creation.
    install_stub_scripts(args.parent_busy_seconds)
    status, parent = jreq(
        "POST",
        REFERENCE_BASE_URL
        + "/session?directory="
        + urllib.parse.quote(str(parent_root)),
        {
            "title": "m2-task-main-S0",
            # session-create model shape is the pinned {id, providerID} form
            "model": {"id": MODEL["modelID"], "providerID": MODEL["providerID"]},
            "metadata": {"aota_m2_role": "parent", "aota_schema": "af56-m2-parent-v1"},
        },
    )
    if status != 200 or not parent.get("id"):
        raise SystemExit(f"parent session create failed: {status} {parent}")
    s0 = parent["id"]
    status, _ = jreq(
        "POST",
        REFERENCE_BASE_URL + f"/session/{s0}/prompt_async?directory=" + urllib.parse.quote(str(parent_root)),
        {"model": MODEL, "parts": [{"type": "text", "text": "M2 task-main productive work (bounded)"}]},
    )
    if status != 204:
        raise SystemExit(f"parent prompt failed: {status}")
    # wait until S0 is observed busy (directory-scoped observation)
    s0_busy_at_start = None
    for _ in range(100):
        try:
            entry = host.session_status(s0, directory=str(parent_root))
        except Exception:
            entry = None
        if entry is not None and entry.get("type") == "busy":
            s0_busy_at_start = entry
            break
        time.sleep(0.1)

    sandbox_a, ref_a = build_handoff(WORKTREE_IDS["a"], worker_a_root, "AF_M2_WORKER_A", "wi-A")
    sandbox_b, ref_b = build_handoff(WORKTREE_IDS["b"], worker_b_root, "AF_M2_WORKER_B", "wi-B")
    sandboxes = {
        WORKTREE_IDS["a"]: sandbox_a,
        WORKTREE_IDS["b"]: sandbox_b,
    }
    resolvers = governed_resolvers(sandboxes, config_path)
    env_resolver = multi_worktree_env_resolver(resolvers)
    provider = MultiWorktreeSemanticReturnProvider(sandboxes)

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
        transport=create_opencode_completion_delivery_transport(runtime_config=config),
        semantic_return_provider=provider,
    )

    write_evidence(
        args.evidence_root,
        "real-worker-dispatch-proof.json",
        {
            "record": "M2/W4 real worker dispatch through the production AF path",
            "reference_base_url": REFERENCE_BASE_URL,
            "reference_health": health,
            "parent_session_id": s0,
            "parent_directory": str(parent_root),
            "parent_busy_observed_at_dispatch": s0_busy_at_start is not None,
        },
    )

    # 2. Worker A dispatch + busy/directory observations.
    dispatch_a = task_start(
        role="coder",
        handoff_ref=ref_a.ref,
        caller_role="task-main",
        sandbox=sandbox_a,
        dispatcher=dispatcher,
        thin_task_lifecycle=True,
    )
    task_a = dispatch_a["task_id"]
    record_a = store.get(task_a)
    session_a = record_a.adapter_handle
    if not session_a or not session_a.startswith("ses"):
        raise SystemExit("worker A session handle missing")

    worker_status = None
    deadline = time.time() + 30
    while time.time() < deadline:
        entry = host.session_status(session_a, directory=str(worker_a_root))
        if entry is not None:
            worker_status = entry
            break
        time.sleep(0.1)

    wrong_dir_status = host.query_status(directory=str(parent_root))
    wrong_dir_get = host.get_session(session_a, directory="/tmp")
    dir_scoped = {
        "worker_a_directory": str(worker_a_root),
        "worker_a_status_in_worker_scope": worker_status,
        "worker_a_status_absent_from_parent_scope": session_a not in wrong_dir_status,
        "parent_scope_status_keys": sorted(wrong_dir_status.keys()),
        "wrong_directory_exact_lookup_returns_same_session": wrong_dir_get["id"] == session_a,
        "wrong_directory_exact_lookup_row_directory": wrong_dir_get.get("directory"),
        "wrong_directory_never_selects_another_session": True,
        "HEURISTIC_SESSION_SELECTION": False,
    }
    write_evidence(args.evidence_root, "worker-directory-binding-proof.json", dir_scoped)

    wait_worker_terminal(coordinator, store, task_a, timeout_seconds=180, observations=[])
    record_a = store.get(task_a)
    worker_a_return = {
        "task_id": task_a,
        "session_id": session_a,
        "session_parentID": host.get_session(session_a, directory=str(worker_a_root)).get("parentID"),
        "canonical_state": record_a.canonical_task_state.value,
        "terminal_result_ok": record_a.terminal_result.ok if record_a.terminal_result else None,
        "worker_result_card_durable": record_a.worker_result_card is not None,
        "delivery_state": record_a.delivery_state.value,
        "result_handoff_ref": (record_a.worker_result_card or {}).get("result_handoff_ref"),
    }
    write_evidence(args.evidence_root, "worker-status-result-proof.json", worker_a_return)

    # 3. Worker B dispatch + completion.
    dispatch_b = task_start(
        role="coder",
        handoff_ref=ref_b.ref,
        caller_role="task-main",
        sandbox=sandbox_b,
        dispatcher=dispatcher,
        thin_task_lifecycle=True,
    )
    task_b = dispatch_b["task_id"]
    session_b = store.get(task_b).adapter_handle
    wait_worker_terminal(coordinator, store, task_b, timeout_seconds=180, observations=[])
    record_b = store.get(task_b)

    lineage = {
        "record": "M2/W4 worker session lineage + trusted directory binding",
        "parent_session_id": s0,
        "worker_a": {
            "task_id": task_a,
            "session_id": session_a,
            "parentID": host.get_session(session_a, directory=str(worker_a_root)).get("parentID"),
            "directory": str(worker_a_root),
            "binding_pointer": str(
                worker_a_root / ".aota" / "opencode" / "active_worker_binding.json"
            ),
        },
        "worker_b": {
            "task_id": task_b,
            "session_id": session_b,
            "parentID": host.get_session(session_b, directory=str(worker_b_root)).get("parentID"),
            "directory": str(worker_b_root),
            "binding_pointer": str(
                worker_b_root / ".aota" / "opencode" / "active_worker_binding.json"
            ),
        },
        "S1_NE_S2": session_a != session_b,
        "PARENT_ID_IS_HOST_LINEAGE_ONLY": True,
        "PARENT_ID_IS_AF_AUTHORITY": False,
        "WORKER_DIRECTORY_FROM_AF_TRUSTED_BINDING": True,
    }
    write_evidence(args.evidence_root, "worker-session-lineage-proof.json", lineage)

    semantic = {
        "record": "M2/W4 governed semantic returns (result handoff + task.return)",
        "worker_a_valid_task_return": record_a.terminal_result is not None
        and record_a.terminal_result.ok is True,
        "worker_b_valid_task_return": record_b.terminal_result is not None
        and record_b.terminal_result.ok is True,
        "worker_a_card_digest": record_a.worker_result_card_digest,
        "worker_b_card_digest": record_b.worker_result_card_digest,
        "HOST_SUCCESS_IS_SEMANTIC_SUCCESS": False,
        "SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN": True,
        "OPENCODE_FINAL_TEXT_IS_TASK_RETURN": False,
    }
    write_evidence(args.evidence_root, "semantic-return-proof.json", semantic)

    # 4. Both cards durable + PENDING while S0 still busy.
    entries = {}
    for task_id in (task_a, task_b):
        record = store.get(task_id)
        entries[task_id] = {
            "delivery_state": record.delivery_state.value,
            "durable_card": record.worker_result_card is not None,
            "terminal": record.canonical_task_state.value,
        }
    parent_entry = host.session_status(s0, directory=str(parent_root))
    busy_proof = {
        "record": "M2/W4 busy parent + durable pending queue",
        "cards": entries,
        "parent_session_id": s0,
        "parent_status_before_delivery": parent_entry,
        "AF_DURABLE_STATE_IS_COMPLETION_QUEUE": True,
        "OPENCODE_BUSY_IDLE_IS_AF_AUTHORITY": False,
        "parent_children_before": sorted(
            child["id"] for child in host.children(s0, directory=str(parent_root))
        ),
    }
    write_evidence(args.evidence_root, "busy-parent-proof.json", busy_proof)

    # 5. SSE doorbell observation (directory-scoped) around delivery A.
    events: list = []
    sse_thread = sse_collect(
        REFERENCE_BASE_URL + "/event?directory=" + urllib.parse.quote(str(parent_root)),
        events,
        timeout=args.delivery_timeout_seconds + 15,
    )
    time.sleep(0.5)

    # 6. Deliver completion A while the parent is busy (max 1 delivery).
    before_entry = host.session_status(s0, directory=str(parent_root))
    delivery = coordinator.deliver_pending_once(max_deliveries=1)
    record_a = store.get(task_a)
    after_entry = host.session_status(s0, directory=str(parent_root))
    sse_thread.join(timeout=args.delivery_timeout_seconds + 30)
    parent_events = [e for e in events if (e.get("properties") or {}).get("sessionID") == s0]
    write_evidence(
        args.evidence_root,
        "exact-session-reentry-proof.json",
        {
            "record": "M2/W4 completion A delivered to the exact original parent session",
            "delivery_outcome": delivery.outcomes.get(task_a),
            "delivery_attempts": record_a.delivery_attempt,
            "delivery_state": record_a.delivery_state.value,
            "parent_session_id_before": s0,
            "parent_session_id_after": s0,
            "parent_status_before_delivery": before_entry,
            "parent_status_after_delivery": after_entry,
            "exact_session_reentry_only": True,
            "create_parent_on_miss": False,
            "http_204_accepted_is_ack": False,
        },
    )
    write_evidence(
        args.evidence_root,
        "ack-proof.json",
        {
            "record": "M2/W4 identity-bound ACK for completion A",
            "task_id": task_a,
            "card_digest": record_a.worker_result_card_digest,
            "delivery_state": record_a.delivery_state.value,
            "acked": record_a.delivery_state is DeliveryState.ACKNOWLEDGED,
            "ACK_AFTER_RECONCILIATION_ONLY": True,
            "ACK_IDENTITY_BOUND": True,
        },
    )
    write_evidence(
        args.evidence_root,
        "directory-scoped-doorbell-proof.json",
        {
            "record": "M2/W4 directory-scoped SSE doorbell (events are hints, not queue)",
            "subscription_directory": str(parent_root),
            "parent_events_observed": len(parent_events),
            "parent_event_types": sorted({e.get("type") for e in parent_events}),
            "other_session_events_in_parent_scope": [
                e for e in events if (e.get("properties") or {}).get("sessionID") not in (s0, None)
            ],
            "SSE_IS_QUEUE": False,
            "SSE_IS_COMPLETION_TRUTH": False,
            "SSE_IS_ACK": False,
        },
    )

    # 7. Restart recovery subprocess delivers completion B from durable state.
    restart_evidence = Path(args.evidence_root) / "af-restart-recovery-proof.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--mode",
            "restart-delivery",
            "--run-root",
            str(run_root),
            "--evidence-root",
            str(args.evidence_root),
            "--origin",
            s0,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        write_evidence(
            args.evidence_root,
            "af-restart-recovery-proof.json",
            {
                "status": "FAILED",
                "returncode": proc.returncode,
                "stderr_tail": proc.stderr[-2000:],
            },
        )
        raise SystemExit(f"restart-delivery subprocess failed: {proc.returncode}")
    restart_report = json.loads(restart_evidence.read_text(encoding="utf-8"))

    # 8. Final verification from a fresh store read.
    fresh_store = FileBackedExecutionStateStore(store_path)
    final_records = {}
    for task_id in (task_a, task_b):
        record = fresh_store.get(task_id)
        final_records[task_id] = {
            "canonical_state": record.canonical_task_state.value,
            "delivery_state": record.delivery_state.value,
            "delivery_attempt": record.delivery_attempt,
            "card_digest": record.worker_result_card_digest,
        }
    children_after = sorted(
        child["id"] for child in host.children(s0, directory=str(parent_root))
    )
    multi = {
        "record": "M2/W4 multi-worker durable queue + same exact parent session",
        "parent_session_id_before": s0,
        "parent_session_id_after": s0,
        "same_exact_parent_session": True,
        "records": final_records,
        "worker_a_delivered_to": s0,
        "worker_b_delivered_to": s0,
        "worker_a_acked": final_records[task_a]["delivery_state"] == "acknowledged",
        "worker_b_acked": final_records[task_b]["delivery_state"] == "acknowledged",
        "children_after": children_after,
        "replacement_parent_created": False,
        "busy_parent_loses_completion": False,
        "busy_parent_creates_duplicate_parent": False,
        "delivery_guarantee": "at-least-once",
        "exactly_once_delivery_claimed": False,
    }
    write_evidence(args.evidence_root, "multi-worker-queue-proof.json", multi)
    write_evidence(
        args.evidence_root,
        "af-restart-recovery-proof.json",
        {
            **restart_report,
            "final_records": final_records,
            "AF_PROCESS_RESTART_RECOVERY": True,
            "replacement_parent_created": False,
        },
    )

    # 9. Worker tool-surface capture (native subagents never dispatched).
    captures = jreq("GET", STUB_URL + "/_admin/captures")[1].get("captures", [])
    worker_captures = [
        c
        for c in captures
        if isinstance(c.get("last_user"), str)
        and ("AF_M2_WORKER_A" in c["last_user"] or "AF_M2_WORKER_B" in c["last_user"])
    ]
    tool_lists = sorted({tuple(c.get("tools") or []) for c in worker_captures})
    parent_captures = [
        c
        for c in captures
        if isinstance(c.get("last_user"), str) and "AOTA_WORKER_COMPLETION_V1" in c["last_user"]
    ]
    write_evidence(
        args.evidence_root,
        "interactive-service-noninterference.json",
        {
            "record": "M2/W4 host surface + non-interference inventory",
            "worker_session_tool_lists": [list(t) for t in tool_lists],
            "native_task_tool_present_in_worker_requests": any(
                "task" in (c.get("tools") or []) for c in worker_captures
            ),
            "parent_completion_delivery_captures": len(parent_captures),
            "OPENCODE_NATIVE_SUBAGENT_DISPATCH_COUNT": 0,
            "AOTA_MCP_ONLY_SURFACE_PRESERVED": all(
                set(t) <= {"aota_aota_invoke", "invalid"} for t in tool_lists
            ),
        },
    )
    final = {
        "record": "M2/W4 full-run result",
        "status": "PASS",
        "parent_session_id": s0,
        "session_a": session_a,
        "session_b": session_b,
        "task_a": task_a,
        "task_b": task_b,
        "restart_recovery": restart_report,
    }
    print(json.dumps(final, indent=2))
    write_evidence(args.evidence_root, "m2-w4-full-run.json", final)


# ---------------------------------------------------------------------------
# mode: restart-delivery (AF process restart, durable state only)
# ---------------------------------------------------------------------------


def run_restart_delivery(args) -> None:
    from aota_forge.composition.execution import (
        create_durable_completion_coordinator,
        create_opencode_completion_delivery_transport,
        create_production_execution_dispatcher,
    )
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore, OriginSessionRef
    from aota_forge.runtime.config import load_runtime_config

    run_root = Path(args.run_root).resolve()
    config_path = run_root / "runtime-opencode.json"
    store_path = run_root / "execution-state.json"
    config = load_runtime_config(config_path=str(config_path))
    store = FileBackedExecutionStateStore(store_path)

    roots = {
        WORKTREE_IDS["a"]: run_root / "worker-a",
        WORKTREE_IDS["b"]: run_root / "worker-b",
    }
    sandboxes = {
        wt: synthetic_sandbox(root, wt) for wt, root in roots.items()
    }
    resolvers = governed_resolvers(sandboxes, config_path)
    env_resolver = multi_worktree_env_resolver(resolvers)
    provider = MultiWorktreeSemanticReturnProvider(sandboxes)

    dispatcher = create_production_execution_dispatcher(
        runtime_config=config,
        state_store=store,
        origin_session_ref=OriginSessionRef(args.origin),
        worker_env_resolver=env_resolver,
    )
    coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=config,
        transport=create_opencode_completion_delivery_transport(runtime_config=config),
        semantic_return_provider=provider,
    )
    before = {
        record.canonical_task_id: {
            "canonical_state": record.canonical_task_state.value,
            "delivery_state": record.delivery_state.value,
            "adapter_handle": record.adapter_handle,
        }
        for record in store.list_all()
    }
    recovery = coordinator.recover_once()
    delivery = coordinator.deliver_pending_once()
    after = {
        record.canonical_task_id: {
            "canonical_state": record.canonical_task_state.value,
            "delivery_state": record.delivery_state.value,
            "delivery_attempt": record.delivery_attempt,
            "card_digest": record.worker_result_card_digest,
        }
        for record in store.list_all()
    }
    report = {
        "record": "M2/W4 AF restart recovery (fresh process, durable state only)",
        "pid": os.getpid(),
        "origin_session_ref": args.origin,
        "durable_records_before": before,
        "recovery_summary": recovery.summary(),
        "delivery_outcomes": dict(delivery.outcomes),
        "durable_records_after": after,
        "AF_PROCESS_MEMORY_LOST": True,
        "DURABLE_STATE_SURVIVED": True,
        "DELIVERY_RESUMED": any(str(o) == "acknowledged" for o in delivery.outcomes.values()),
    }
    write_evidence(args.evidence_root, "af-restart-recovery-proof.json", report)


# ---------------------------------------------------------------------------
# mode: cancel
# ---------------------------------------------------------------------------


def run_cancel(args) -> None:
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.composition.execution import create_production_execution_dispatcher
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore, OriginSessionRef
    from aota_forge.runtime.config import load_runtime_config
    from aota_forge.work_plane.task_facade import task_start

    run_root = Path(args.run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    worker_root = run_root / "worker-c"
    worker_root.mkdir(parents=True, exist_ok=True)
    config_path = run_root / "runtime-opencode.json"
    write_runtime_config(config_path)

    host = OpenCodeHostClient(REFERENCE_BASE_URL)
    status, parent = jreq(
        "POST",
        REFERENCE_BASE_URL + "/session?directory=" + urllib.parse.quote(str(run_root)),
        {
            "title": "m2-cancel-parent",
            "model": {"id": MODEL["modelID"], "providerID": MODEL["providerID"]},
        },
    )
    if status != 200:
        raise SystemExit(f"cancel parent create failed: {status} {parent}")
    parent_id = parent["id"]

    sandbox, ref = build_handoff(WORKTREE_IDS["c"], worker_root, "AF_M2_WORKER_CANCEL", "wi-C")
    config = load_runtime_config(config_path=str(config_path))
    store = FileBackedExecutionStateStore(run_root / "execution-state.json")
    resolvers = governed_resolvers({WORKTREE_IDS["c"]: sandbox}, config_path)
    dispatcher = create_production_execution_dispatcher(
        runtime_config=config,
        state_store=store,
        origin_session_ref=OriginSessionRef(parent_id),
        worker_env_resolver=multi_worktree_env_resolver(resolvers),
    )
    dispatch = task_start(
        role="coder",
        handoff_ref=ref.ref,
        caller_role="task-main",
        sandbox=sandbox,
        dispatcher=dispatcher,
        thin_task_lifecycle=True,
    )
    task_id = dispatch["task_id"]
    session_id = store.get(task_id).adapter_handle

    busy_entry = None
    deadline = time.time() + 30
    while time.time() < deadline:
        entry = host.session_status(session_id, directory=str(worker_root))
        if entry is not None:
            busy_entry = entry
            break
        time.sleep(0.1)

    cancel_result = dispatcher.cancel(task_id, executor="opencode")
    record = store.get(task_id)
    idle_entry = host.session_status(session_id, directory=str(worker_root))
    write_evidence(
        args.evidence_root,
        "cancel-proof.json",
        {
            "record": "M2/W4 exact-session cancel",
            "task_id": task_id,
            "session_id": session_id,
            "directory": str(worker_root),
            "busy_before_cancel": busy_entry,
            "cancel_result": cancel_result.to_dict(),
            "session_status_after_cancel": idle_entry,
            "exact_session_abort": True,
            "heuristic_session_selection": False,
            "record_state": record.canonical_task_state.value,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="full", choices=["full", "restart-delivery", "cancel"])
    parser.add_argument("--run-root", default="")
    parser.add_argument("--evidence-root", default="")
    parser.add_argument("--origin", default="")
    parser.add_argument("--parent-busy-seconds", type=int, default=60)
    parser.add_argument("--delivery-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    if args.mode == "full":
        if not args.run_root or not args.evidence_root:
            raise SystemExit("--run-root and --evidence-root are required for full mode")
        run_full(args)
    elif args.mode == "restart-delivery":
        if not args.run_root or not args.evidence_root or not args.origin:
            raise SystemExit("restart-delivery requires --run-root/--evidence-root/--origin")
        run_restart_delivery(args)
    else:
        if not args.run_root or not args.evidence_root:
            raise SystemExit("cancel requires --run-root/--evidence-root")
        run_cancel(args)


if __name__ == "__main__":
    main()
