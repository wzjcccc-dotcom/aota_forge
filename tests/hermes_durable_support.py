"""Deterministic durable-supervisor emulator for W2 restart tests.

The production host client spawns the launcher supervisor with
``popen_factory``; this module replaces only that spawn seam.  The emulator
speaks the REAL file protocol (via the real locator primitives) and owns
REAL OS child processes so /proc identity checks behave exactly like
production — which is what lets tests prove:

    client A dispatches -> locator durable on disk
    -> A is destroyed (no in-memory state)
    -> client B resolves the same handle with no Python-object dependency.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from aota_forge.adapters.hermes import locator

_SLEEP_SOURCE = "import sys,time; time.sleep(float(sys.argv[1]))"


def launch_spec(args: list[str]) -> dict[str, Any]:
    """Extract the hermes launch spec from supervisor argv [exe, -c, bootstrap, spec]."""
    spec = locator.read_json_object(Path(args[-1]))
    assert spec is not None, "supervisor launch was handed a missing spec file"
    return locator.validate_spec(spec)


def hermes_argv_of(args: list[str]) -> list[str]:
    return list(launch_spec(args)["hermes_argv"])


class SupervisedLaunch:
    """One emulated supervised Hermes execution with real process identity."""

    def __init__(self, spec_path: Path, *, stdout: bytes, stderr: bytes, deadline: float, started: float) -> None:
        self.spec = locator.validate_spec(locator.read_json_object(spec_path) or {})
        self.paths = locator.HermesRunPaths(Path(spec_path).resolve().parents[2], self.spec["run_id"])
        self._stdout = stdout
        self._stderr = stderr
        self.terminate_calls = 0
        self._lock = threading.Lock()
        self._returncode: int | None = None
        self._finalizing = False
        self._child_rc: int | None = None
        self._finished = threading.Event()
        token = f"launcher={self._launcher()} run={self.spec['run_id']}"
        # Two real OS processes: one stands in for the supervisor (recorded in
        # the marker), one for the supervised Hermes child (child.json).
        self._supervisor = subprocess.Popen(
            [sys.executable, "-c", f"{_SLEEP_SOURCE} # {token}", "7200"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._child = subprocess.Popen(
            [sys.executable, "-c", f"{_SLEEP_SOURCE} # {token}", "7200"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        locator.write_child_record(self.paths, child_pid=self._child.pid, started_at_wall=started)
        self._deadline_watch = threading.Timer(
            max(0.0, deadline - time.time()), lambda: self._finalize("timeout", None)
        )
        self._deadline_watch.daemon = True
        self._deadline_watch.start()

    def _launcher(self) -> str:
        argv = self.spec["hermes_argv"]
        return str(argv[0])

    @property
    def pid(self) -> int:
        return int(self._supervisor.pid)

    def poll(self) -> int | None:
        with self._lock:
            return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._finished.wait(timeout if timeout is not None else 30.0):
            with self._lock:
                assert self._returncode is not None
                return self._returncode
        raise subprocess.TimeoutExpired("supervised-launch", timeout)

    def release(
        self,
        code: int | None = None,
        *,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
        read_failure: bool = False,
        stream_gate: threading.Event | list[threading.Event] | None = None,
    ) -> None:
        exit_code = code if code is not None else 0
        status = "done" if exit_code == 0 else "failed"
        gates = None
        if stream_gate is not None:
            gates = stream_gate if isinstance(stream_gate, list) else [stream_gate]
        if gates is not None:
            # Drain window: the supervised worker exited, but the supervisor
            # is still stabilizing its bounded captures — no terminal receipt
            # may exist until the gate opens.  Exactly the production
            # supervisor ordering (receipt renamed last).
            self._child_rc = exit_code
            self._kill(self._child)

            def _finish() -> None:
                for event in gates or []:
                    event.wait(timeout=20.0)
                self._finalize(
                    status,
                    exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    read_failure=read_failure,
                )

            threading.Thread(target=_finish, daemon=True).start()
            return
        self._finalize(
            status,
            exit_code,
            stdout=stdout,
            stderr=stderr,
            read_failure=read_failure,
        )

    def child_returncode(self) -> int | None:
        return self._child.poll() if self._child is not None else None

    def crash_without_receipt(self) -> None:
        """Simulate total runtime loss: both processes die, no terminal receipt.

        Reconciling readers must then return UNKNOWN — never a fabricated
        terminal observation.
        """
        self._deadline_watch.cancel()
        self._kill(self._child)
        self._kill(self._supervisor)
        with self._lock:
            self._returncode = -9
            self._finalizing = True
        self._finished.set()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._finalize("cancelled", None)

    def kill(self) -> None:
        self.terminate_calls += 1
        self._finalize("cancelled", None)

    def _finalize(
        self,
        status: str,
        exit_code: int | None,
        *,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
        read_failure: bool = False,
    ) -> None:
        with self._lock:
            if self._finalizing or self._returncode is not None:
                return
            self._finalizing = True
        self._deadline_watch.cancel()
        out = self._stdout if stdout is None else stdout
        err = self._stderr if stderr is None else stderr
        limit = int(self.spec["output_limit_bytes"])
        self._kill(self._child)
        self._kill(self._supervisor)
        _write_bounded(self.paths.stdout, out, limit)
        _write_bounded(self.paths.stderr, err, limit)
        locator.write_receipt(
            self.paths,
            status=status,
            exit_code=exit_code,
            started_at_wall=float(self.spec["started_at_wall"]),
            completed_at_wall=time.time(),
            stdout_bytes=min(len(out), limit),
            stderr_bytes=min(len(err), limit),
            stdout_truncated=len(out) > limit,
            stderr_truncated=len(err) > limit,
            read_failure=read_failure,
        )
        self._finished.set()

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            process.kill()
            process.wait(timeout=1.0)
        except Exception:
            pass


class DurableSupervisorPad:
    """popen_factory replacement emulating durable supervisor launches."""

    def __init__(
        self,
        *,
        stdout: bytes = b"controlled stdout",
        stderr: bytes = b"controlled stderr",
        release_on_spawn: int | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.release_on_spawn = release_on_spawn
        self.launches: list[SupervisedLaunch] = []
        self.calls: list[dict[str, Any]] = []
        self.raises: Exception | None = None

    def __call__(self, args: list[str], **kwargs: Any) -> SupervisedLaunch:
        if self.raises is not None:
            raise self.raises
        spec_path = Path(str(args[-1]))
        launch = SupervisedLaunch(
            spec_path,
            stdout=self.stdout,
            stderr=self.stderr,
            deadline=float(launch_spec(args)["deadline_wall"]),
            started=time.time(),
        )
        self.launches.append(launch)
        self.calls.append({"args": list(args), **kwargs})
        if self.release_on_spawn is not None:
            launch.release(self.release_on_spawn)
        return launch

    @property
    def last(self) -> SupervisedLaunch:
        assert self.launches, "no supervised launch happened"
        return self.launches[-1]

    def close(self) -> None:
        for launch in self.launches:
            launch.terminate()


def _write_bounded(path: Path, data: bytes, limit: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data[:limit])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def make_marker_handle(client: Any, adapter_handle: str) -> dict[str, Any]:
    run_id = locator.run_id_from_adapter_handle(adapter_handle)
    assert run_id is not None
    paths = locator.HermesRunPaths(Path(client.runtime_root), run_id)
    marker = locator.read_json_object(paths.marker)
    return json.loads(json.dumps(marker)) if marker else {}
