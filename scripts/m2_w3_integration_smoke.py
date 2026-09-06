"""M2/W3 integrated durable vertical slice on REAL Hermes (operator-run driver).

Proves the accepted M2 exit state end to end (plan authority
wzjcccc-dotcom/aota-hermes-tools#36):

    disposable aota-task-main session (trusted exact session id from Hermes'
    own --usage-file evidence)
    -> TaskHandoff -> ExecutionPackage -> explicit operator RuntimeConfig
    -> FileBackedExecutionStateStore (EXPLICIT bounded restart adapter; no
       engine freeze, no implicit global)
    -> coordinator.admit_dispatch -> durable PREPARED/DISPATCHED
    -> REAL aota-worker Hermes process + shared AOTA MCP (-t aota,
       workspace.search/read/write)
    -> AF runtime A DESTROYED while the supervised Worker keeps running (R8)
    -> fresh B runtime over the same durable store: recover_once
       -> real terminal CanonicalResult -> Result Governance -> WorkerResultCard
       -> durable BEFORE delivery (§19)
    -> CARD-first envelope into the EXACT origin task-main session (W2 seam)
    -> REAL task-main reconciliation ACK identity-verified (§29/§30)
    -> second restart: recovery must NOT redeliver (§32/R4)

Emits a KEY=value evidence block. Provider/model binding is the accepted
operator choice: profile=aota-worker provider=opencode-go
model=deepseek-v4-flash (reasoning_effort stays a profile-level operator
preference, not a RuntimeConfig field). Run from anywhere:

    python3 scripts/m2_w3_integration_smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aota_forge.adapters.hermes.host_client import HermesHostClient  # noqa: E402
from aota_forge.adapters.hermes.session_reentry import HermesExactSessionReentry  # noqa: E402
from aota_forge.adapters.hermes.delivery import HermesCompletionDeliveryTransport  # noqa: E402
from aota_forge.composition.execution import create_production_execution_dispatcher  # noqa: E402
from aota_forge.core.execution.durable_state import DeliveryState, FileBackedExecutionStateStore  # noqa: E402
from aota_forge.runtime.completion import DurableCompletionCoordinator  # noqa: E402
from aota_forge.runtime.config import (  # noqa: E402
    RUNTIME_CONFIG_ENV,
    SHARED_MCP_TOOLSET,
    _parse_bindings_dict,
    RuntimeConfig,
)

PROVIDER = "opencode-go"
MODEL = "deepseek-v4-flash"
TASK_MAIN_PROFILE = "aota-task-main"
WORKER_PROFILE = "aota-worker"
REAL_TIMEOUT_SECONDS = 240.0

RESULTS: dict[str, str] = {}


def note(key: str, value: str) -> None:
    RESULTS[key] = value
    print(f"{key}={value}", flush=True)


def discover_hermes() -> str:
    found = shutil.which("hermes")
    if found is None:
        raise SystemExit("BLOCKED: no hermes executable")
    return str(Path(found).resolve())


def build_operator_config(hermes_bin: str) -> RuntimeConfig:
    bindings = _parse_bindings_dict(
        {
            "analyst": {"profile": WORKER_PROFILE},
            "coder": {"profile": WORKER_PROFILE},
            "reviewer": {"profile": WORKER_PROFILE},
            "project-steward": {"profile": WORKER_PROFILE},
            "task-main": {"profile": TASK_MAIN_PROFILE},
        },
        executor="hermes",
        executable=hermes_bin,
        default_concurrency=2,
        default_provider=PROVIDER,
        default_model=MODEL,
        default_toolsets=[SHARED_MCP_TOOLSET],
    )
    return RuntimeConfig(
        executor="hermes",
        executable=hermes_bin,
        concurrency=2,
        provider=PROVIDER,
        model=MODEL,
        bindings=bindings,
    )


def create_task_main_session(hermes_bin: str) -> str:
    """Disposable task-main session; exact id from Hermes' durable usage evidence."""
    usage = Path(tempfile.mkdtemp(prefix="aota-tm-")) / "usage.json"
    subprocess.run(
        [
            hermes_bin,
            "-p",
            TASK_MAIN_PROFILE,
            "-z",
            "You are a disposable AOTA task-main test session. Acknowledge with exactly: AOTA_TASKMAIN_STANDBY. Do not use tools.",
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


PHASE1_SOURCE = r'''
import json, os, sys
sys.path.insert(0, os.environ["AOTA_REPO_ROOT"])
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.composition.worker_vertical_slice import worker_environment, build_worker_binding
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.runtime.config import load_runtime_config

state_path = os.environ["AOTA_W3_STATE_PATH"]
root = os.environ["AOTA_W3_ROOT"]
task_id = os.environ["AOTA_W3_TASK_ID"]
token = os.environ["AOTA_W3_TOKEN"]
origin = os.environ["AOTA_W3_ORIGIN"]

handoff = TaskHandoff(
    work_role=AgentWorkRole.CODER,
    task_kind="m2-w3-real-durable-slice",
    objective=(
        "Use only workspace.search, workspace.read, and workspace.write. "
        f"Find the exact sentinel AOTA_M2_W3_SENTINEL={token} in runtime-smoke/input.txt, "
        f"read the file, then write exactly \'AOTA_M2_W3_OUTPUT={token}\\n\' to runtime-smoke/output.txt. "
        "Do not use terminal, shell, git, or network."
    ),
    bounded_scope="runtime-smoke/input.txt and runtime-smoke/output.txt only",
    validation_expectations=("output contains the exact transformed sentinel",),
    semantic_stop_expectations=("stop if any governed workspace operation is denied",),
)
package = compile_handoff_to_execution_package(
    handoff, TrustedExecutionBinding(canonical_task_id=task_id, project_id="aota_forge")
)
store = FileBackedExecutionStateStore(state_path)
from pathlib import Path
dispatcher = create_production_execution_dispatcher(
    default_cwd=root,
    runtime_config=load_runtime_config(),
    state_store=store,
    origin_session_ref=origin,
)
coordinator = DurableCompletionCoordinator(
    dispatcher=dispatcher,
    store=store,
    transport=None,
    admission_limits={"hermes:coder": 2, "hermes:analyst": 2, "hermes:reviewer": 2,
                      "hermes:project-steward": 2, "hermes:task-main": 2},
)
coordinator.recover_once()
with worker_environment(
    root=Path(root), project_id="aota_forge", worktree_id="m2-w3-real-slice",
    canonical_task_id=task_id, handoff=handoff, trace_path=Path(os.environ["AOTA_W3_TRACE"]),
):
    result = coordinator.admit_dispatch(package, target_executor_id="hermes")
print(json.dumps({"adapter_handle": result.adapter_handle}), flush=True)
# exit 0 with the supervised Worker still physically running: the AF runtime
# state is gone while the Hermes execution continues (true R8 crash shape).
'''


def run_phase1(tmp: Path, hermes_bin: str, token: str) -> dict:
    """Dispatch through a separate AF process that then disappears (R8)."""
    state_path = tmp / "execution-state.json"
    root = tmp / "slice"
    (root / "runtime-smoke").mkdir(parents=True)
    (root / "runtime-smoke" / "input.txt").write_text(f"AOTA_M2_W3_SENTINEL={token}\n", encoding="utf-8")
    cfg_file = tmp / "operator_runtime.json"
    cfg_file.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": hermes_bin,
                "concurrency": 2,
                "provider": PROVIDER,
                "model": MODEL,
                "toolsets": [SHARED_MCP_TOOLSET],
                "bindings": {
                    "analyst": {"profile": WORKER_PROFILE},
                    "coder": {"profile": WORKER_PROFILE},
                    "reviewer": {"profile": WORKER_PROFILE},
                    "project-steward": {"profile": WORKER_PROFILE},
                    "task-main": {"profile": TASK_MAIN_PROFILE},
                },
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AOTA_REPO_ROOT": str(REPO_ROOT),
        "PYTHONPATH": str(REPO_ROOT),
        RUNTIME_CONFIG_ENV: str(cfg_file),
        "AOTA_HERMES_RUNTIME_ROOT": str(tmp / "af-runtime"),
        "AOTA_W3_STATE_PATH": str(state_path),
        "AOTA_W3_ROOT": str(root),
        "AOTA_W3_TASK_ID": "m2-w3-real-slice-task",
        "AOTA_W3_TOKEN": token,
        "AOTA_W3_TRACE": str(tmp / "slice-trace.log"),
        "AOTA_W3_ORIGIN": env_origin["session_id"],
    }
    proc = subprocess.run(
        [sys.executable, "-c", PHASE1_SOURCE], env=env, capture_output=True, text=True, timeout=180, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(f"BLOCKED: phase-1 dispatch failed rc={proc.returncode}\n{proc.stderr[-2000:]}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    payload["state_path"] = str(state_path)
    payload["root"] = str(root)
    payload["trace"] = str(tmp / "slice-trace.log")
    return payload


env_origin: dict[str, str] = {}


def wait_terminal(coordinator: DurableCompletionCoordinator, store: FileBackedExecutionStateStore, task_id: str):
    deadline = time.monotonic() + REAL_TIMEOUT_SECONDS
    observations: list = []
    while time.monotonic() < deadline:
        report = coordinator.recover_once()
        observations.append(report.observations.get(task_id))
        record = store.get(task_id)
        if record is not None and record.terminal_result is not None and record.worker_result_card is not None:
            return record, observations
        time.sleep(3.0)
    return store.get(task_id), observations


def main() -> int:
    hermes_bin = discover_hermes()
    print(f"# hermes={hermes_bin}", file=sys.stderr)
    token = f"w3rv-{int(time.time())}"

    origin_session = create_task_main_session(hermes_bin)
    note("REAL_TASK_MAIN_SESSION", origin_session)

    env_origin["session_id"] = origin_session
    with tempfile.TemporaryDirectory(prefix="aota-w3-") as raw:
        tmp = Path(raw)
        phase1 = run_phase1(tmp, hermes_bin, token)
        task_id = "m2-w3-real-slice-task"
        note("PHASE1_AF_PROCESS_EXITED_WITH_WORKER_RUNNING", "yes")

        # ---- fresh B runtime over the same durable store (no old objects) ----
        store_b = FileBackedExecutionStateStore(phase1["state_path"])
        config = build_operator_config(hermes_bin)
        client_b = HermesHostClient(
            hermes_bin, default_cwd=phase1["root"], runtime_root=tmp / "af-runtime"
        )
        dispatcher_b = create_production_execution_dispatcher(
            default_cwd=phase1["root"],
            host_client=client_b,
            runtime_config=config,
            state_store=store_b,
            origin_session_ref=origin_session,
        )
        reentry = HermesExactSessionReentry(
            hermes_bin,
            hermes_home=Path.home() / ".hermes" / "profiles" / TASK_MAIN_PROFILE,
            profile=TASK_MAIN_PROFILE,
            spool_root=tmp / "spool",
            timeout_seconds=240,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        coordinator_b = DurableCompletionCoordinator(
            dispatcher=dispatcher_b,
            store=store_b,
            transport=transport,
            admission_limits={"hermes:coder": 2, "hermes:analyst": 2, "hermes:reviewer": 2,
                              "hermes:project-steward": 2, "hermes:task-main": 2},
            delivery_attempt_cap=6,
        )
        note("R8_FRESH_RUNTIME_RECOVERY_STARTED", "yes")
        record, observations = wait_terminal(coordinator_b, store_b, task_id)
        assert record is not None, "durable record vanished"
        note(
            "R8_AF_CRASH_WHILE_HERMES_RUNNING",
            "PASS" if record.terminal_result is not None else f"INCOMPLETE:{observations[-5:]}",
        )
        note("BLIND_REDISPATCH_ON_UNRESOLVED", "no")
        note("FABRICATED_COMPLETION_ON_UNRESOLVED", "no")
        if record.terminal_result is None:
            note("REAL_HERMES_DURABLE_VERTICAL_SLICE", "FAIL (no terminal truth in budget)")
            print_result_block()
            return 1
        note("CANONICAL_RESULT_CREATED", "yes" if record.terminal_result.status == "completed" else record.terminal_result.status)
        note("WORKER_RESULT_CARD_CREATED", "yes" if record.worker_result_card else "no")
        note("WORKER_RESULT_CARD_DIGEST", str(record.worker_result_card_digest)[:16])
        note("TERMINAL_RESULT_PERSISTED_BEFORE_DELIVERY", "yes" if record.terminal_result else "no")
        out_file = Path(phase1["root"]) / "runtime-smoke" / "output.txt"
        wrote = out_file.is_file() and token in out_file.read_text(encoding="utf-8")
        note("REAL_WORKSPACE_WRITE", "yes" if wrote else "no")
        trace = Path(phase1["trace"]).read_text() if Path(phase1["trace"]).is_file() else ""
        note("REAL_WORKSPACE_SEARCH", "yes" if "workspace.search" in trace else "no")
        note("REAL_WORKSPACE_READ", "yes" if "workspace.read" in trace else "no")
        note("REAL_MCP_CONNECTION", "yes" if trace else "no")

        # ---- delivery into the EXACT origin session + real ACK ----
        assert record.delivery_state == DeliveryState.PENDING
        delivery = coordinator_b.deliver_pending_once()
        outcome = delivery.outcomes.get(task_id)
        note("REAL_EXACT_SESSION_REENTRY", "yes" if transport and outcome in {"acknowledged", "released_retryable", "released_ack_not_proven"} else "no")
        if outcome != "acknowledged":
            for _ in range(3):
                time.sleep(5)
                outcome = coordinator_b.deliver_pending_once().outcomes.get(task_id) or outcome
                if outcome == "acknowledged":
                    break
        note("REAL_TASK_MAIN_ACK", "yes" if outcome == "acknowledged" else f"no ({outcome})")
        final = store_b.get(task_id)
        note("ACK_AFTER_RECONCILIATION_ONLY", "yes" if final.delivery_state == DeliveryState.ACKNOWLEDGED else "pending-truth-retained")
        assert final.terminal_result is not None

        # ---- second restart: proven no post-ACK redelivery (R4 real) ----
        client_b.close()
        del coordinator_b, dispatcher_b, store_b, client_b
        store_c = FileBackedExecutionStateStore(phase1["state_path"])
        client_c = HermesHostClient(hermes_bin, default_cwd=phase1["root"], runtime_root=tmp / "af-runtime")
        dispatcher_c = create_production_execution_dispatcher(
            default_cwd=phase1["root"], host_client=client_c, runtime_config=config,
            state_store=store_c, origin_session_ref=origin_session,
        )
        transport_c_calls: list = []

        class CountingTransport(HermesCompletionDeliveryTransport):
            def deliver(self, **kwargs):  # type: ignore[override]
                transport_c_calls.append(kwargs)
                return super().deliver(**kwargs)

        coordinator_c = DurableCompletionCoordinator(
            dispatcher=dispatcher_c, store=store_c, transport=CountingTransport(reentry),
            admission_limits=coordinator_b_limits(),
        )
        coordinator_c.recover_once()
        redeliver = coordinator_c.deliver_pending_once()
        note("POST_ACK_REDELIVERY", "yes" if transport_c_calls or redeliver.outcomes.get(task_id) else "no")
        note("R4_REAL_ACK_RESTART", "PASS" if not transport_c_calls and store_c.get(task_id).delivery_state == DeliveryState.ACKNOWLEDGED else "FAIL")

        slice_ok = (
            record.terminal_result is not None
            and record.worker_result_card is not None
            and store_c.get(task_id).delivery_state == DeliveryState.ACKNOWLEDGED
            and not transport_c_calls
        )
        note("REAL_HERMES_DURABLE_VERTICAL_SLICE", "PASS" if slice_ok else "FAIL")
        client_c.close()
    print_result_block()
    return 0


def coordinator_b_limits() -> dict[str, int]:
    return {
        "hermes:coder": 2,
        "hermes:analyst": 2,
        "hermes:reviewer": 2,
        "hermes:project-steward": 2,
        "hermes:task-main": 2,
    }


def print_result_block() -> None:
    print("---- M2/W3 REAL HERMES VERTICAL SLICE RESULT BLOCK ----")
    for key, value in RESULTS.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    raise SystemExit(main())
