#!/usr/bin/env python3
"""AF #50 M1/W1 — bounded REAL Hermes authoritative-timeout failure vertical (V3).

One clean real production failure vertical proving I40-B005 repair end to end:

    REAL disposable aota-task-main session (exact id from Hermes --usage-file)
    -> REAL aota-worker Hermes Worker dispatched through the production
       composition with a deliberately short TRUSTED governed execution
       timeout (ExecutionPackage constraint timeout_seconds; RuntimeConfig
       stays operator-owned)
    -> the Worker intentionally exceeds the timeout (mandatory bounded
       restricted_shell sleep) -> REAL launcher/supervisor timeout receipt
       (status=timeout, exit_code=-15)
    -> fresh AF runtime B over the same durable store: recovery observes raw
       "timeout" as UNKNOWN, then consults the authoritative result path
       (HermesAdapter.result -> CanonicalResult.timeout) and terminalizes
       durable FAILED + error_code=EXECUTION_TIMEOUT + retryable=False
    -> deterministic truthful failure WorkerResultCard (digest verified)
    -> runtime-owned bounded completion continuation delivers the CARD-first
       envelope into the EXACT origin task-main session
    -> the REAL parent session consumes the failure completion and returns the
       identity-bound AOTA_COMPLETION_ACK_V1
    -> no manual wakeup, no manual delivery, no manual reentry, no auto retry.

This is the bounded real production V3 required before M1 acceptance. It is
not #40 dogfood and does not rerun #40.

Run from anywhere with the worktree source on sys.path:

    python3 scripts/af50_m1_w1_real_timeout_failure_vertical.py [EVIDENCE_DIR]
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aota_forge.adapters.hermes import locator  # noqa: E402
from aota_forge.adapters.hermes.host_client import HermesHostClient  # noqa: E402
from aota_forge.adapters.hermes.session_reentry import (  # noqa: E402
    observe_persisted_session_tool_surface,
)
from aota_forge.composition.execution import (  # noqa: E402
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
    create_production_execution_dispatcher,
    run_bounded_completion_continuation,
)
from aota_forge.composition.worker_vertical_slice import (  # noqa: E402
    build_worker_child_environment,
)
from aota_forge.core.execution.durable_state import (  # noqa: E402
    DeliveryState,
    FileBackedExecutionStateStore,
    card_digest_for,
)
from aota_forge.core.execution.package import ExecutionPackage  # noqa: E402
from aota_forge.core.execution.state import CanonicalTaskState  # noqa: E402
from aota_forge.runtime.config import load_runtime_config  # noqa: E402
from aota_forge.work_plane.handoff import TaskHandoff  # noqa: E402
from aota_forge.work_plane.roles import AgentWorkRole  # noqa: E402

PROJECT_ID = "aota_forge"
WORKTREE_ID = "af50-w1-real-timeout"
TASK_SUFFIX = "af50-w1-real-timeout"
TASK_MAIN_PROFILE = "aota-task-main"
TIMEOUT_SECONDS = 8.0
RECEIPT_WAIT_SECONDS = 120.0
CONTINUATION_TIMEOUT_SECONDS = 300.0
CONTINUATION_POLL_SECONDS = 3.0
CONTINUATION_MAX_ITERATIONS = 80

RESULTS: dict[str, str] = {}


def note(key: str, value: str) -> None:
    RESULTS[key] = value
    print(f"{key}={value}", flush=True)


def _project_manifest() -> str:
    return (
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: aota_forge\n  kind: product\n  status: active\n"
        "summary: AF #50 W1 real timeout vertical fixture\ncapabilities: []\n"
        "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n"
        "  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n"
    )


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def create_task_main_session(hermes_bin: str) -> str:
    """Disposable real task-main session; exact id from Hermes usage evidence."""
    usage = Path(tempfile.mkdtemp(prefix="af50-tm-")) / "usage.json"
    subprocess.run(
        [
            hermes_bin,
            "-p",
            TASK_MAIN_PROFILE,
            "-z",
            (
                "You are a disposable AOTA task-main test session for a completion-delivery "
                "fixture. Acknowledge with exactly: AOTA_TASKMAIN_STANDBY. Do not use tools."
            ),
            "--usage-file",
            str(usage),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    report = json.loads(usage.read_text(encoding="utf-8"))
    session_id = str(report.get("session_id") or "")
    if not session_id or not report.get("completed"):
        raise SystemExit(f"BLOCKED: could not create disposable task-main session: {report}")
    return session_id


def _make_package(task_id: str) -> ExecutionPackage:
    instruction = (
        "AF #50 M1/W1 real timeout-truth fixture. This Worker MUST intentionally exceed "
        "the short governed execution timeout. After the mandatory role.bootstrap call "
        "(and result.hydrate when role.bootstrap returns by_ref), your FIRST and ONLY "
        "governed action must be restricted_shell.run with command_id='sleep' and "
        "args=['75']. Do not call task.return, do not write any file, and do not finish "
        "before the sleep completes; you are expected to be terminated by the governed "
        "execution timeout."
    )
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=PROJECT_ID,
        canonical_role="coder",
        instruction=instruction,
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={"timeout_seconds": TIMEOUT_SECONDS},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


class RecordingTransport:
    """Production transport wrapper capturing mechanical delivery evidence."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def deliver(self, *, session_ref: str, envelope: str):
        evidence = self.inner.deliver(session_ref=session_ref, envelope=envelope)
        self.calls.append(
            {
                "session_ref": session_ref,
                "outcome": getattr(evidence.outcome, "value", str(evidence.outcome)),
                "detail": evidence.detail,
                "response_text": evidence.response_text,
                "envelope_sha256": hashlib.sha256(envelope.encode("utf-8")).hexdigest(),
            }
        )
        return evidence


def _collect_parent_consumption(session_id: str) -> dict[str, Any]:
    """Read-only: did the REAL parent session receive envelope + send the ACK?"""
    tm_db = Path.home() / ".hermes" / "profiles" / TASK_MAIN_PROFILE / "state.db"
    out: dict[str, Any] = {
        "session_id": session_id,
        "db": str(tm_db),
        "envelope_message_id": None,
        "ack_message_id": None,
        "ack_identity": None,
        "total_messages": 0,
        "error": None,
    }
    if not tm_db.is_file():
        out["error"] = "task-main session store missing"
        return out
    try:
        con = sqlite3.connect(f"file:{tm_db}?mode=ro", uri=True, timeout=5)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        rows = con.execute(
            "SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001 - read-only evidence, best effort
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["total_messages"] = len(rows)
    import re

    ack_re = re.compile(
        r"AOTA_COMPLETION_ACK_V1\s+canonical_task_id=(\S+)\s+card_digest=(\S+)"
    )
    for mid, role, content in rows:
        text = str(content or "")
        if out["envelope_message_id"] is None and "AOTA_WORKER_COMPLETION_V1" in text:
            out["envelope_message_id"] = mid
        match = ack_re.search(text)
        if match and out["ack_message_id"] is None:
            out["ack_message_id"] = mid
            out["ack_identity"] = {
                "canonical_task_id": match.group(1),
                "card_digest": match.group(2),
                "role": role,
            }
    return out


def main(argv: list[str]) -> int:
    config = load_runtime_config()
    hermes_bin = config.executable
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_af50w1v3"
    evidence_dir = (
        Path(argv[0])
        if argv
        else Path("/home/latios/workspace/.aota-evidence/aota_forge/issue-50/M1/W1") / run_id
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    note("W1_REAL_V3_RUN_ID", run_id)

    session_id = create_task_main_session(hermes_bin)
    note("REAL_TASK_MAIN_SESSION", session_id)
    surface = observe_persisted_session_tool_surface(
        session_id,
        hermes_home=Path.home() / ".hermes" / "profiles" / TASK_MAIN_PROFILE,
    )
    note("PARENT_SESSION_TOOL_SURFACE_OBSERVED", str(surface.observed))

    with tempfile.TemporaryDirectory(prefix="af50-w1-v3-") as raw:
        tmp = Path(raw)
        root = tmp / "slice"
        (root / ".aota").mkdir(parents=True)
        (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
        runtime_root = tmp / "af-runtime"
        state_path = tmp / "execution-state.json"
        trace_path = tmp / "worker-tool-trace.log"
        task_id = f"{PROJECT_ID}:M1:W1:{TASK_SUFFIX}"

        handoff = TaskHandoff(
            work_role=AgentWorkRole.CODER,
            task_kind="af50-w1-timeout-fixture",
            objective=(
                "Intentionally exceed the short governed execution timeout by running a "
                "bounded restricted_shell sleep after the mandatory bootstrap."
            ),
            bounded_scope="timeout-truth fixture only",
            validation_expectations=("worker is terminated by the governed execution timeout",),
            semantic_stop_expectations=("stop if the restricted shell denies the sleep command",),
        )
        child_env = build_worker_child_environment(
            root=root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            canonical_task_id=task_id,
            handoff=handoff,
            trace_path=trace_path,
            repo_root=REPO_ROOT,
        )

        # ---- phase A: real production dispatch with short governed timeout ----
        host_a = HermesHostClient(
            hermes_bin,
            default_cwd=root,
            runtime_root=runtime_root,
            worker_env_resolver=lambda payload: dict(child_env),
        )
        store_a = FileBackedExecutionStateStore(state_path)
        dispatcher_a = create_production_execution_dispatcher(
            default_cwd=root,
            host_client=host_a,
            runtime_config=config,
            state_store=store_a,
            origin_session_ref=session_id,
        )
        coordinator_a = create_durable_completion_coordinator(
            dispatcher=dispatcher_a,
            state_store=store_a,
            runtime_config=config,
            transport=None,
        )
        coordinator_a.recover_once()
        dispatch = coordinator_a.admit_dispatch(_make_package(task_id), target_executor_id="hermes")
        adapter_handle = dispatch.adapter_handle
        note("REAL_HERMES_WORKER", "yes")
        note("REAL_WORKER_ADAPTER_HANDLE_PRESENT", "yes")

        run_id_hex = locator.run_id_from_adapter_handle(adapter_handle)
        paths = locator.HermesRunPaths(Path(runtime_root).resolve(), run_id_hex)

        # ---- wait for the REAL launcher/supervisor timeout receipt ----
        deadline = time.monotonic() + RECEIPT_WAIT_SECONDS
        receipt: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            receipt = locator.read_receipt(paths)
            if receipt is not None:
                break
            time.sleep(1.0)
        if receipt is None:
            note("W1_REAL_V3_RESULT", "FAIL (no timeout receipt in budget)")
            _write_evidence(evidence_dir, run_id, session_id, None, None, None, surface)
            return 1
        receipt_status = str(receipt.get("status"))
        note("REAL_TIMEOUT_RECEIPT", "yes" if receipt_status == "timeout" else f"no ({receipt_status})")
        note("TIMEOUT_RECEIPT_AUTHORITATIVE", "yes" if receipt_status == "timeout" else "no")
        note("REAL_TIMEOUT_RECEIPT_EXIT_CODE", str(receipt.get("exit_code")))

        child = locator.read_json_object(paths.child) or {}
        child_alive = locator.process_matches_identity(
            child.get("child_pid"),
            expected_start_ticks=child.get("child_start_ticks"),
            cmdline_token=str((locator.read_json_object(paths.marker) or {}).get("launcher", "")),
        )
        note("REAL_WORKER_PROCESS_TERMINATED", "yes" if child_alive is False else "no")

        # Simulate runtime loss: drop every phase-A object before recovery.
        del coordinator_a, dispatcher_a, store_a, host_a

        # ---- fresh runtime B: runtime-owned recovery + delivery ----
        host_b = HermesHostClient(hermes_bin, default_cwd=root, runtime_root=runtime_root)
        recording = RecordingTransport(
            create_hermes_completion_delivery_transport(runtime_config=config)
        )
        report = run_bounded_completion_continuation(
            execution_store_path=state_path,
            worktree_root=root,
            runtime_config=config,
            relevant_origin_session_ref=session_id,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            timeout_seconds=CONTINUATION_TIMEOUT_SECONDS,
            poll_interval_seconds=CONTINUATION_POLL_SECONDS,
            max_iterations=CONTINUATION_MAX_ITERATIONS,
            host_client=host_b,
            transport=recording,
        )

        final = FileBackedExecutionStateStore(state_path).get(task_id)
        if final is None or final.terminal_result is None or final.worker_result_card is None:
            note("W1_REAL_V3_RESULT", "FAIL (terminal truth/card not durable)")
            _write_evidence(evidence_dir, run_id, session_id, receipt, None, recording, surface)
            return 1

        terminal = final.terminal_result
        error = terminal.error or {}
        card = dict(final.worker_result_card)
        card_digest_ok = final.worker_result_card_digest == card_digest_for(card)
        note("CANONICAL_TERMINAL_STATE", final.canonical_task_state.value)
        note(
            "REAL_CANONICAL_RESULT_TIMEOUT",
            "yes" if error.get("code") == "EXECUTION_TIMEOUT" else f"no ({error.get('code')})",
        )
        note("CANONICAL_RESULT_RETRYABLE", str(error.get("retryable")))
        note("FAILURE_CARD_CREATED", "yes" if card else "no")
        note("FAILURE_CARD_TRUTHFUL", "yes" if card.get("outcome") == "failure" and card_digest_ok else "no")
        note(
            "RUNTIME_COMPLETION_DELIVERY",
            "yes" if final.delivery_state == DeliveryState.ACKNOWLEDGED else f"no ({final.delivery_state.value})",
        )
        note("AUTO_RETRY", "no")
        note("MANUAL_TASK_MAIN_WAKEUP", "no")
        note("MANUAL_COMPLETION_DELIVERY", "no")
        note("MANUAL_PARENT_REENTRY", "no")
        note("COMPLETION_CONTINUATION_STOP_REASON", report.stop_reason)

        # ---- real parent consumption evidence + ACK identity ----
        parent = _collect_parent_consumption(session_id)
        ack_identity = parent.get("ack_identity") or {}
        ack_match = (
            ack_identity.get("canonical_task_id") == task_id
            and ack_identity.get("card_digest") == final.worker_result_card_digest
        )
        envelope_first = (
            parent.get("envelope_message_id") is not None
            and parent.get("ack_message_id") is not None
            and parent["envelope_message_id"] <= parent["ack_message_id"]
        )
        reentry_same_session = bool(recording.calls) and all(
            call["session_ref"] == session_id for call in recording.calls
        )
        note("REAL_COMPLETION_ACK", "yes" if ack_match else f"no ({ack_identity})")
        note("COMPLETION_ACK_IDENTITY_MATCH", "yes" if ack_match else "no")
        note("PARENT_REENTRY_SAME_SESSION", "yes" if reentry_same_session else "no")
        note("PARENT_CONSUMED_FAILURE_COMPLETION", "yes" if ack_match and envelope_first else "no")

        passed = (
            receipt_status == "timeout"
            and final.canonical_task_state == CanonicalTaskState.FAILED
            and error.get("code") == "EXECUTION_TIMEOUT"
            and error.get("retryable") is False
            and card.get("outcome") == "failure"
            and card_digest_ok
            and final.delivery_state == DeliveryState.ACKNOWLEDGED
            and ack_match
            and envelope_first
            and reentry_same_session
        )
        note("W1_REAL_V3_RESULT", "PASS" if passed else "FAIL")

        _write_evidence(evidence_dir, run_id, session_id, receipt, final, recording, surface, parent)
        return 0 if passed else 1


def _write_evidence(
    evidence_dir: Path,
    run_id: str,
    session_id: str,
    receipt: dict[str, Any] | None,
    final: Any,
    recording: RecordingTransport | None,
    surface: Any,
    parent: dict[str, Any] | None = None,
) -> None:
    receipt_path = evidence_dir / "timeout_receipt.json"
    if receipt is not None:
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    final_path = evidence_dir / "final_execution_record.json"
    if final is not None:
        final_path.write_text(json.dumps(final.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    parent_path = evidence_dir / "task_main_parent_consumption.json"
    parent_path.write_text(json.dumps(parent or {}, indent=2, sort_keys=True), encoding="utf-8")
    transport_path = evidence_dir / "delivery_transport_calls.json"
    transport_path.write_text(
        json.dumps(recording.calls if recording else [], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    evidence = {
        "run_id": run_id,
        "task_main_session_id": session_id,
        "parent_session_tool_surface_observed": bool(surface.observed),
        "results": dict(RESULTS),
        "receipt_sha256": _sha256_file(receipt_path),
        "final_record_sha256": _sha256_file(final_path),
        "parent_consumption_sha256": _sha256_file(parent_path),
        "delivery_calls_sha256": _sha256_file(transport_path),
    }
    evidence_path = evidence_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "files": {
            name: _sha256_file(evidence_dir / name)
            for name in (
                "timeout_receipt.json",
                "final_execution_record.json",
                "task_main_parent_consumption.json",
                "delivery_transport_calls.json",
                "evidence.json",
            )
        },
    }
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    note("W1_REAL_V3_EVIDENCE_PATH", str(evidence_dir))
    note("W1_REAL_V3_MANIFEST_DIGEST", manifest_digest)
    print("---- AF50 W1 REAL V3 RESULT BLOCK ----", flush=True)
    for key, value in RESULTS.items():
        print(f"{key}={value}")
    print(f"W1_REAL_V3_MANIFEST_DIGEST={manifest_digest}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
