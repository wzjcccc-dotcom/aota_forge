#!/usr/bin/env python3
"""AF #56 M2 — per-worktree AOTA MCP binding wrapper for the OpenCode reference host.

OpenCode spawns local MCP servers per directory instance with cwd = the
instance directory (pinned v1.18.30: ``MCP.connectLocal`` uses
``InstanceState.directory`` as the child cwd). AF dispatches every Worker
session into its own trusted assigned worktree and writes a mechanical
per-directory binding pointer there; this wrapper resolves that pointer and
execs the EXISTING production Worker MCP entry
(``aota_forge.composition.worker_vertical_slice --mcp-server``), which verifies
the digest-bound pre-resolved envelope before any semantic dispatch.

Boundaries:

- The wrapper adds no AF semantics, no second MCP server, and no second
  binding protocol: it only translates the host's per-directory cwd into the
  existing ``AOTA_PRE_RESOLVED_BINDING`` channel.
- Missing/ambiguous/foreign pointer material fails closed: the MCP server does
  not start, so a Worker can never gain a binding it was not dispatched with.
- The pointer lives under the trusted worktree ``.aota`` boundary and must
  resolve inside it; a pointer escaping the directory is rejected.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

POINTER_RELATIVE_PATH = Path(".aota") / "opencode" / "active_worker_binding.json"
PRE_RESOLVED_BINDING_ENV = "AOTA_PRE_RESOLVED_BINDING"
REPO_ROOT_ENV = "AOTA_FORGE_REPO_ROOT"
DEBUG_ENV = "AOTA_OPENCODE_MCP_DEBUG"
POINTER_SCHEMA_VERSION = "1"


def _debug(record: dict) -> None:
    target = os.environ.get(DEBUG_ENV)
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def _fail(reason: str) -> "None":
    _debug({"event": "m2_mcp_binding_failure", "reason": reason, "cwd": os.getcwd()})
    print(f"AF M2 MCP binding unavailable: {reason}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def main() -> None:
    instance_dir = Path(os.environ.get("AOTA_OPENCODE_MCP_INSTANCE_DIR", os.getcwd())).resolve()
    pointer_path = instance_dir / POINTER_RELATIVE_PATH
    if not pointer_path.is_file():
        _fail(f"no trusted worker binding pointer at {pointer_path}")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable pointer fails closed
        _fail(f"pointer unreadable: {type(exc).__name__}")
    if not isinstance(pointer, dict) or pointer.get("schema_version") != POINTER_SCHEMA_VERSION:
        _fail("pointer schema mismatch")
    envelope_path = pointer.get("envelope_path")
    if not isinstance(envelope_path, str) or not envelope_path.strip():
        _fail("pointer envelope_path missing")
    envelope = Path(envelope_path)
    try:
        resolved_envelope = envelope.resolve(strict=True)
    except OSError as exc:
        _fail(f"envelope missing: {type(exc).__name__}")
    # Containment: the referenced envelope must live inside the trusted
    # worktree .aota boundary of THIS directory instance.
    try:
        resolved_envelope.relative_to(instance_dir / ".aota")
    except ValueError:
        _fail("envelope escapes the instance .aota boundary")

    repo_root = os.environ.get(REPO_ROOT_ENV, "").strip()
    if not repo_root:
        _fail(f"{REPO_ROOT_ENV} is not configured")
    repo_path = Path(repo_root).resolve()
    if not (repo_path / "aota_forge").is_dir():
        _fail("configured repo root does not contain the aota_forge package")

    _debug(
        {
            "event": "m2_mcp_binding_resolved",
            "instance_dir": str(instance_dir),
            "envelope": str(resolved_envelope),
        }
    )
    child_env = os.environ.copy()
    child_env[PRE_RESOLVED_BINDING_ENV] = str(resolved_envelope)
    child_env[REPO_ROOT_ENV] = str(repo_path)
    child_env["AOTA_FORGE_REPO_ROOT"] = str(repo_path)
    child_env["PYTHONPATH"] = str(repo_path) + (
        os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else ""
    )
    # Bounded integration-harness seam: the M2 real run uses synthetic project
    # evidence test roots (same accepted seam build_worker_binding documents);
    # production binding would resolve canonical project evidence instead.
    child_env.setdefault("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", "1")
    os.execve(
        sys.executable,
        [sys.executable, "-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"],
        child_env,
    )


if __name__ == "__main__":
    main()
