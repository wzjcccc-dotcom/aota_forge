"""Thin durable launcher supervisor for Hermes one-shot workers (M2/W2).

The supervisor is the HERMES-ADAPTER-PRIVATE mechanical side of the durable
runtime boundary.  It exists for exactly one reason: the terminal evidence of
a Worker run (exit status, bounded stdout/stderr capture, completion marker)
must survive loss of the dispatching AF process, so a later AF process can
reconcile the same locator without the original Python objects.

It is deliberately thin:

- it is launched detached (new session) with the run's spec file as its only
  argument;
- it spawns the Hermes child, records child birth identity, drains both
  streams into BOUNDED files, enforces the absolute deadline, and writes one
  atomic terminal receipt;
- it stores no semantic state: process exit zero is mechanical evidence, never
  AF acceptance (``HERMES_PROCESS_EXIT_ZERO_IS_AF_ACCEPTANCE=no``);
- it never touches provider credentials, auth material, or profile env files;
  the child simply inherits the operator environment it was dispatched with.

Run as a module: ``python -m aota_forge.adapters.hermes.launcher <spec.json>``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import locator
from .locator import HermesLocatorError, HermesRunPaths

JOIN_GRACE_SECONDS = 1.0
TERMINATE_WAIT_SECONDS = 1.0
LIVENESS_POLL_SECONDS = 0.05


class _BoundedFileCapture:
    """Drain one pipe into a bounded file: at most ``limit`` bytes retained."""

    def __init__(self, path: Path, limit: int) -> None:
        self._path = path
        self._limit = limit
        self.written = 0
        self.truncated = False
        self.read_failure = False
        self._lock = threading.Lock()

    def drain(self, stream) -> None:
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as sink:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8", errors="replace")
                    with self._lock:
                        remaining = self._limit - self.written
                        if remaining > 0:
                            keep = chunk[:remaining]
                            sink.write(keep)
                            self.written += len(keep)
                        if len(chunk) > remaining:
                            self.truncated = True
                sink.flush()
                os.fsync(sink.fileno())
        except Exception:
            # A capture that died mid-stream can never be re-established as a
            # complete stable view; the receipt records read_failure and the
            # host projection refuses to publish "done".
            self.read_failure = True


def _install_cancel_flag(state: dict) -> None:
    def _handler(signum, frame) -> None:  # pragma: no cover - signal path
        state["cancel"] = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def supervise(spec_path: str | os.PathLike[str]) -> int:
    """Run one supervised Hermes execution; always attempt a terminal receipt.

    Returns the supervisor's own exit code (0 = receipt published).  The
    receipt is the durable evidence; a supervisor that dies before writing it
    is expected to surface as UNKNOWN to reconciling AF processes — it must
    never be back-filled with a fabricated terminal state.
    """
    spec = locator.validate_spec(locator.read_json_object(Path(spec_path)) or {})
    run_id = spec.get("run_id")
    if not isinstance(run_id, str):
        raise HermesLocatorError("launch spec is missing a run id")
    # spec_path is <root>/runs/<run_id>/spec.json -> root = parents[2]
    paths = HermesRunPaths(Path(spec_path).resolve().parents[2], run_id)

    limit = int(spec["output_limit_bytes"])
    out = _BoundedFileCapture(paths.stdout, limit)
    err = _BoundedFileCapture(paths.stderr, limit)
    state = {"cancel": False}
    _install_cancel_flag(state)

    started_at_wall = float(spec["started_at_wall"])
    deadline_wall = float(spec["deadline_wall"])
    exit_code: int | None = None
    status = "failed"
    process: subprocess.Popen | None = None
    reader_threads: list[threading.Thread] = []
    try:
        process = subprocess.Popen(
            list(spec["hermes_argv"]),
            cwd=spec["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        locator.write_child_record(paths, child_pid=process.pid, started_at_wall=time.time())
        for stream, capture in ((process.stdout, out), (process.stderr, err)):
            reader = threading.Thread(target=capture.drain, args=(stream,), daemon=True)
            reader.start()
            reader_threads.append(reader)

        while True:
            if state["cancel"]:
                _terminate(process)
                status = "cancelled"
                break
            remaining = deadline_wall - time.time()
            if remaining <= 0:
                _terminate(process)
                status = "timeout"
                break
            try:
                exit_code = process.wait(timeout=min(LIVENESS_POLL_SECONDS * 20, max(0.05, remaining)))
                break
            except subprocess.TimeoutExpired:
                continue

        if exit_code is None:
            observed = process.poll()
            if isinstance(observed, int):
                exit_code = observed
        if status != "cancelled" and status != "timeout":
            status = "done" if exit_code == 0 else "failed"
    except FileNotFoundError:
        status = "failed"
        exit_code = None
    except OSError:
        status = "failed"
        exit_code = None

    # W2 ordering invariant (inherited from the accepted M1 boundary): a
    # terminal receipt is published only after both captures have finished,
    # so a reconciling reader can never freeze a partially drained stream.
    join_deadline = time.monotonic() + JOIN_GRACE_SECONDS
    for reader in reader_threads:
        reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
    read_failure = out.read_failure or err.read_failure
    if status == "done" and any(reader.is_alive() for reader in reader_threads):
        # Streams never stabilized inside the bounded drain window: this is a
        # capture failure, not a completed success.
        read_failure = True

    completed_at_wall = time.time()
    locator.write_receipt(
        paths,
        status=status,
        exit_code=exit_code,
        started_at_wall=started_at_wall,
        completed_at_wall=completed_at_wall,
        stdout_bytes=out.written,
        stderr_bytes=err.written,
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        read_failure=read_failure,
    )
    return 0


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=TERMINATE_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=TERMINATE_WAIT_SECONDS)
        except Exception:
            pass
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        return 2
    try:
        return supervise(argv[0])
    except HermesLocatorError:
        return 3
    except Exception:
        # The supervisor must never leak a traceback into captured evidence.
        return 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
