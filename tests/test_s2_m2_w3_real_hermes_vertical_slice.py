"""S2/M2/W3 proof: Joint First Real Hermes Vertical Slice (RENDEZVOUS_ID=S2S3-JRV1).

W4 synchronization (accepted M1 architecture): the production real-runtime
proof now belongs to the M1/W3 governed Worker vertical slice (shared
``aota-worker`` profile + shared AOTA MCP + result governance). This
predecessor JRV1 slice keeps its own chain as an OPT-IN bounded real Hermes
probe over the current ingress seam, because the repository's full suite must
stay deterministic and the production composition now requires an
operator-owned RuntimeConfig (fail-closed; no source-owned deployment
default). When opted in, the test constructs its own explicit config fixture
(a bounded temporary probe, not production deployment authority).

Chain proven when enabled:

    execution.task_start (canonical ingress)
    -> ExecutionPackage
    -> ExecutionDispatcher (accepted production composition)
    -> real HermesAdapter
    -> real HermesHostClient (no host_client injection)
    -> direct Hermes binary, bounded one-shot Worker on the shared profile
    -> execution.task_status polling
    -> execution.task_result
    -> Forge/Core terminal reconciliation

Offline seams (host protocol, projection) are exercised by the W1/W2/W3 M1
test files; the capability-vector preservation assertion below always runs
through an injected host (no process launch).

Durability stays D0 / process-local: no restart, no persistence, no resume,
no cancellation of the primary worker.
"""

from __future__ import annotations

import os
import shutil
import time
import unittest
import uuid
from pathlib import Path
from typing import Any

from aota_forge.adapters.hermes.executor import HERMES_EXECUTOR_ID, HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.composition.execution import (
    PRODUCTION_HERMES_DEFAULT_CWD,
    bind_production_execution_dispatcher,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution.results import FORBIDDEN_HERMES_RESULT_KEYS
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import execute, reset_execution_dispatcher
from aota_forge.runtime.config import SHARED_MCP_TOOLSET, RuntimeBinding, RuntimeConfig

REAL_MARKER = "AOTA_S2S3_JRV1_OK"
WORKER_INSTRUCTION = f"Return exactly: {REAL_MARKER}"

REAL_SMOKE_GATE = "AOTA_S2_M2_W3_REAL_HERMES_SMOKE"

# Bounded runtime envelope: a one-shot echo-style worker with a host-side
# deadline below the accepted production maximum (300s).
WORKER_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 2.0
POLL_COUNT_LIMIT = 75  # ~150s wall bound, larger than the host-side deadline

# Test-owned explicit operator pins (JRV1-era probe values, chosen by this
# fixture; never consulted by production source).
JRV1_TEST_PROVIDER = "opencode-go"
JRV1_TEST_MODEL = "deepseek-v4-flash"

INITIAL_NON_TERMINAL_STATES = {
    CanonicalTaskState.QUEUED.value,
    CanonicalTaskState.RUNNING.value,
    CanonicalTaskState.WAITING.value,
}

TERMINAL_STATE_VALUES = {state.value for state in CanonicalTaskState if state.is_terminal}


class _Jrv1FakeHost:
    """Offline recording host for the capability-vector seam (no process)."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def dispatch(self, payload):
        self.payloads.append(dict(payload))
        return {"adapter_handle": "jrv1-fake-1", "status": "pending"}

    def query_status(self, handle):
        return {"status": "running"}

    def fetch_result(self, handle):
        return {"status": "pending"}

    def cancel_task(self, handle):
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, handle, payload):
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


def _jrv1_runtime_config() -> RuntimeConfig | None:
    """Explicit opt-in operator config fixture over the discovered Hermes binary."""
    executable = shutil.which("hermes")
    if executable is None:
        return None
    exe = Path(executable)
    if exe.is_symlink() or not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor="hermes",
            profile="aota-worker" if role != "task-main" else "aota-task-main",
            provider=JRV1_TEST_PROVIDER,
            model=JRV1_TEST_MODEL,
            concurrency=1,
            executable=str(exe),
            toolsets=(SHARED_MCP_TOOLSET,) if role != "task-main" else None,
        )
        for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
    )
    return RuntimeConfig(
        executor="hermes",
        executable=str(exe),
        concurrency=1,
        provider=JRV1_TEST_PROVIDER,
        model=JRV1_TEST_MODEL,
        bindings=bindings,
    )


class S2M2W3RealHermesVerticalSliceTest(unittest.TestCase):
    """JRV1: one bounded real Hermes worker across the full canonical seam."""

    def test_production_capability_vector_is_frozen_for_jrv1(self) -> None:
        """The accepted production Hermes capability vector stays async/process/concurrency 1
        and covers exactly the four shared-profile Worker canonical roles."""
        reset_execution_dispatcher()
        try:
            fake = _Jrv1FakeHost()
            dispatcher = create_production_execution_dispatcher(host_client=fake)
            descriptor = dispatcher.registry.get_descriptor(HERMES_EXECUTOR_ID)
            self.assertEqual(descriptor.executor_id, HERMES_EXECUTOR_ID)
            self.assertEqual(tuple(descriptor.supported_execution_modes), ("async",))
            self.assertEqual(
                tuple(descriptor.supported_canonical_roles),
                ("coder", "planner", "reviewer", "steward"),
            )
            self.assertTrue(descriptor.supports_task_cancellation)
            self.assertFalse(descriptor.supports_task_resume)
            self.assertFalse(descriptor.supports_structured_result)
            self.assertFalse(descriptor.supports_artifact_transport)
            self.assertTrue(descriptor.supports_working_directory)
        finally:
            reset_execution_dispatcher()

    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.dispatcher = None
        self.client = None
        if os.environ.get(REAL_SMOKE_GATE) != "1":
            self.skipTest(
                "JRV1 real slice is opt-in (bounded real-runtime proof); the M1/W3 "
                "governed Worker slice is the current production real-runtime proof"
            )
        self.config = _jrv1_runtime_config()
        if self.config is None:
            self.skipTest("JRV1_NOT_SATISFIED_BY_SKIP: no trusted Hermes executable available (RUNTIME_ENVIRONMENT)")
        if not Path(PRODUCTION_HERMES_DEFAULT_CWD).is_dir():
            self.skipTest(
                "JRV1_NOT_SATISFIED_BY_SKIP: accepted production default working "
                f"directory is unavailable at {PRODUCTION_HERMES_DEFAULT_CWD} "
                "(RUNTIME_ENVIRONMENT)"
            )
        # No host_client argument: the accepted composition must build the real
        # HermesHostClient against the operator-configured executable.
        self.dispatcher = bind_production_execution_dispatcher(runtime_config=self.config)
        adapter = self.dispatcher.registry.get(HERMES_EXECUTOR_ID)
        self.assertIsInstance(adapter, HermesAdapter)
        client = getattr(adapter, "_host_client", None)
        self.assertIs(type(client), HermesHostClient)
        self.client = client

    def tearDown(self) -> None:
        try:
            if self.client is not None:
                self.client.close()
        finally:
            reset_execution_dispatcher()

    def _status_data(self, task_id: str) -> dict[str, Any]:
        envelope = execute(
            "execution.task_status", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID}
        )
        self.assertTrue(envelope["ok"], f"canonical task_status failed: {envelope}")
        self.assertEqual(envelope["data"]["canonical_task_id"], task_id)
        return envelope["data"]

    def test_s2s3_jrv1_real_hermes_vertical_slice(self) -> None:
        dispatcher = self.dispatcher
        task_id = f"s2s3-jrv1-{uuid.uuid4().hex[:12]}"

        # ---- 1. canonical task_start through the ingress -------------------
        start = execute(
            "execution.task_start",
            {
                "canonical_task_id": task_id,
                "executor": HERMES_EXECUTOR_ID,
                "role": "coder",
                "instruction": WORKER_INSTRUCTION,
                "project_id": "aota_forge",
                "timeout": WORKER_TIMEOUT_SECONDS,
            },
        )
        self.assertTrue(start["ok"], f"canonical task_start failed: {start}")
        self.assertEqual(start["audit"]["validation"], "ok")
        start_data = start["data"]
        self.assertEqual(start_data["canonical_task_id"], task_id)

        adapter_handle = start_data["adapter_handle"]
        self.assertIsInstance(adapter_handle, str)
        self.assertTrue(adapter_handle.strip())

        # async production semantics: task_start must not require sync completion
        self.assertIn(start_data["initial_state"], INITIAL_NON_TERMINAL_STATES)

        route = dispatcher.get_route(task_id)
        self.assertEqual(route.executor_id, HERMES_EXECUTOR_ID)
        self.assertEqual(route.adapter_handle, adapter_handle)

        # identity domains stay distinct end-to-end
        self.assertNotEqual(route.canonical_task_id, route.adapter_handle)
        self.assertNotEqual(route.package_id, route.adapter_handle)
        self.assertNotEqual(route.dispatch_attempt_id, route.adapter_handle)
        self.assertNotEqual(route.package_id, route.dispatch_attempt_id)
        self.assertNotEqual(route.canonical_task_id, route.package_id)
        self.assertNotEqual(route.canonical_task_id, route.dispatch_attempt_id)

        # ---- 2. bounded canonical status polling ---------------------------
        observed: list[str] = [start_data["initial_state"]]
        terminal: str | None = None
        active_probe: dict[str, Any] | None = None
        for _ in range(POLL_COUNT_LIMIT):
            state = self._status_data(task_id)["state"]
            if state != observed[-1]:
                observed.append(state)
            if state in TERMINAL_STATE_VALUES:
                terminal = state
                break
            if active_probe is None:
                # one-shot active-result real-runtime probe (§19)
                probe = execute(
                    "execution.task_result",
                    {"task_id": task_id, "executor": HERMES_EXECUTOR_ID},
                )
                active_probe = probe
                self.assertTrue(probe["ok"], f"active-result envelope malformed: {probe}")
                pdata = probe["data"]
                # safety invariants: identity coherence, never a fabricated
                # terminal truth while the host still reports non-terminal
                self.assertEqual(pdata["canonical_task_id"], task_id)
                self.assertEqual(pdata["executor_id"], HERMES_EXECUTOR_ID)
                route_now = dispatcher.get_route(task_id)
                self.assertEqual(route_now.adapter_handle, adapter_handle)
                self.assertEqual(route_now.dispatch_attempt_id, route.dispatch_attempt_id)
                self.assertFalse(
                    pdata["ok"] and route_now.last_known_state.value != pdata["canonical_task_state"]
                )
                if not pdata["ok"]:
                    # expected frozen S3/M1 projection observed at W2
                    self.assertEqual(pdata["canonical_task_state"], state)
                    self.assertEqual(pdata["status"], "unknown")
                    self.assertEqual(pdata["error"]["code"], "TASK_STILL_RUNNING")
            time.sleep(POLL_INTERVAL_SECONDS)

        self.assertIsNotNone(
            terminal,
            "real Hermes worker did not reach a terminal state inside the bounded "
            f"poll window; observed canonical sequence: {observed}. Classify as "
            "WORKER_FAILURE / LAUNCHER / RUNTIME_ENVIRONMENT from host evidence; "
            "do not patch production source here.",
        )
        self.assertEqual(
            terminal,
            CanonicalTaskState.COMPLETED.value,
            f"unexpected real terminal state {terminal!r}; observed {observed}",
        )

        # ---- 3. canonical terminal result projection -----------------------
        result = execute(
            "execution.task_result", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID}
        )
        self.assertTrue(result["ok"], f"canonical task_result failed: {result}")
        data = result["data"]
        self.assertTrue(data["ok"], f"terminal CanonicalResult not ok: {data}")
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["canonical_task_state"], CanonicalTaskState.COMPLETED.value)
        self.assertEqual(data["canonical_task_id"], task_id)
        self.assertEqual(data["executor_id"], HERMES_EXECUTOR_ID)
        self.assertEqual(data["exit_code"], 0)
        self.assertIsInstance(data["stdout_summary"], str)
        self.assertIn(
            REAL_MARKER,
            data["stdout_summary"],
            f"bounded canonical output missing exact marker: {data['stdout_summary']!r}",
        )

        # Hermes-private identity never escapes into canonical projections
        for container in (data["result_data"], data["execution_stats"]):
            self.assertIsInstance(container, dict)
            for key in container:
                lowered = str(key).lower()
                self.assertNotIn(lowered, FORBIDDEN_HERMES_RESULT_KEYS)
                self.assertFalse(lowered.startswith("hermes_"))

        # ---- 4. Forge reconciliation and terminal stickiness ---------------
        route_after = dispatcher.get_route(task_id)
        self.assertEqual(route_after.last_known_state, CanonicalTaskState.COMPLETED)
        self.assertEqual(route_after.executor_id, HERMES_EXECUTOR_ID)
        self.assertEqual(route_after.canonical_task_id, task_id)
        self.assertEqual(route_after.adapter_handle, adapter_handle)

        # post-terminal observations must not silently regress Core truth
        post = self._status_data(task_id)
        self.assertEqual(post["state"], CanonicalTaskState.COMPLETED.value)
        replay = execute(
            "execution.task_result", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID}
        )
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["data"]["ok"])
        self.assertEqual(replay["data"]["canonical_task_id"], task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.COMPLETED
        )


if __name__ == "__main__":
    unittest.main()
