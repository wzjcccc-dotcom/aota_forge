#!/usr/bin/env python3
"""AF #56 M3 / AF #59 M1 — AOTA MCP wrapper for the OpenCode reference host.

This is the SINGLE production host-side wrapper for the pinned OpenCode
reference server. The host spawns local MCP servers per directory instance
with cwd = the instance directory. Two modes exist:

1. Bound instance (AF-staged task/worker instance directory)

   <instance_dir>/.aota/opencode/active_binding.json   # AF-written pointer
     -> kind=worker    : envelope staged in THIS instance namespace
     -> kind=task-main : envelope + bootstrap path (shared trusted bootstrap)

   The wrapper resolves the AF-staged binding for THAT exact instance
   namespace and execs the EXISTING production AOTA MCP entry
   (``aota_forge.composition.worker_vertical_slice --mcp-server``), which
   verifies the digest-bound pre-resolved envelope before any semantic
   dispatch.

2. Ordinary workspace (AF #59 M1 — no pointer, no binding)

   The wrapper starts the SAME single production MCP entry in unbound host
   mode (``AOTA_GLOBAL_MCP=1``). The single entry ``aota.invoke`` is always
   available; MCP availability is NOT authority. Side-effect operations
   resolve their authority from canonical refs at the operation boundary
   (Plan approval is the current server-side Plan fact). No session reserve,
   bind, preparation record or special instance directory is involved.

Boundaries:

- The wrapper adds no AF semantics, no second MCP server and no second binding
  protocol: it mechanically translates (a) the host instance directory into
  the existing ``AOTA_PRE_RESOLVED_BINDING`` (and ``AOTA_TASK_MAIN_BOOTSTRAP``
  for task-main) channels, or (b) an ordinary directory into the unbound host
  channel.
- Missing/ambiguous/foreign/mis-staged pointer material fails closed: the MCP
  server does not start with a bogus binding.
- The envelope must live inside THIS instance's ``.aota`` boundary AND its
  payload ``worktree_root`` must agree with the pointer's trusted
  ``binding_root`` (mechanical consistency; the envelope digest is verified
  downstream by the production entry).
- NB-4 closure: this wrapper NEVER provides the synthetic project-evidence
  seam. Real production/reference paths require real project evidence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

POINTER_RELATIVE_PATH = Path(".aota") / "opencode" / "active_binding.json"
PRE_RESOLVED_BINDING_ENV = "AOTA_PRE_RESOLVED_BINDING"
TASK_MAIN_BOOTSTRAP_ENV = "AOTA_TASK_MAIN_BOOTSTRAP"
REPO_ROOT_ENV = "AOTA_FORGE_REPO_ROOT"
RUNTIME_CONFIG_ENV = "AOTA_FORGE_RUNTIME_CONFIG"
REGISTRY_ENV = "AOTA_FORGE_REGISTRY"
GLOBAL_MCP_ENV = "AOTA_GLOBAL_MCP"
DEBUG_ENV = "AOTA_OPENCODE_MCP_DEBUG"
POINTER_SCHEMA_VERSION = "1"
BINDING_KIND_WORKER = "worker"
BINDING_KIND_TASK_MAIN = "task-main"
BINDING_KINDS = frozenset({BINDING_KIND_WORKER, BINDING_KIND_TASK_MAIN})

# NB-4: never defaulted here.
SYNTHETIC_PROJECT_EVIDENCE_PROVIDED = False
# AF #59 M1: ordinary workspace mode (no binding pointer required).
UNBOUND_HOST_MODE_AVAILABLE = True


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
    _debug({"event": "m3_mcp_binding_failure", "reason": reason, "cwd": os.getcwd()})
    print(f"AF M3 MCP binding unavailable: {reason}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def _repo_root_or_fail() -> Path:
    repo_root = os.environ.get(REPO_ROOT_ENV, "").strip()
    if not repo_root:
        _fail(f"{REPO_ROOT_ENV} is not configured")
    repo_path = Path(repo_root).resolve()
    if not (repo_path / "aota_forge").is_dir():
        _fail("configured repo root does not contain the aota_forge package")
    return repo_path


def _base_child_env(repo_path: Path) -> dict:
    child_env = os.environ.copy()
    child_env[REPO_ROOT_ENV] = str(repo_path)
    child_env["PYTHONPATH"] = str(repo_path) + (
        os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else ""
    )
    # NB-4 closure: the production/reference wrapper never defaults the
    # synthetic project-evidence test seam.
    child_env.pop("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", None)
    return child_env


def _exec_mcp(child_env: dict) -> "None":
    os.execve(
        sys.executable,
        [sys.executable, "-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"],
        child_env,
    )


def _run_unbound(instance_dir: Path) -> "None":
    """Ordinary workspace mode: always-available AOTA MCP, no binding."""
    repo_path = _repo_root_or_fail()
    child_env = _base_child_env(repo_path)
    child_env.pop(PRE_RESOLVED_BINDING_ENV, None)
    child_env.pop(TASK_MAIN_BOOTSTRAP_ENV, None)
    child_env[GLOBAL_MCP_ENV] = "1"
    _debug(
        {
            "event": "af59_unbound_mcp_mode",
            "instance_dir": str(instance_dir),
            "repo_root": str(repo_path),
            "registry": child_env.get(REGISTRY_ENV, ""),
            "runtime_config": child_env.get(RUNTIME_CONFIG_ENV, ""),
        }
    )
    _exec_mcp(child_env)


def _read_pointer(instance_dir: Path) -> dict:
    pointer_path = instance_dir / POINTER_RELATIVE_PATH
    if not pointer_path.is_file():
        _fail(f"no trusted binding pointer at {pointer_path}")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable pointer fails closed
        _fail(f"pointer unreadable: {type(exc).__name__}")
    if not isinstance(pointer, dict) or pointer.get("schema_version") != POINTER_SCHEMA_VERSION:
        _fail("pointer schema mismatch")
    if pointer.get("kind") not in BINDING_KINDS:
        _fail("pointer kind invalid")
    return pointer


def _resolve_envelope(instance_dir: Path, pointer: dict) -> Path:
    envelope_path = pointer.get("envelope_path")
    if not isinstance(envelope_path, str) or not envelope_path.strip():
        _fail("pointer envelope_path missing")
    envelope = Path(envelope_path)
    try:
        resolved_envelope = envelope.resolve(strict=True)
    except OSError as exc:
        _fail(f"envelope missing: {type(exc).__name__}")
    # Containment: the referenced envelope must live inside THIS instance's
    # trusted .aota boundary.
    try:
        resolved_envelope.relative_to(instance_dir / ".aota")
    except ValueError:
        _fail("envelope escapes the instance .aota boundary")
    # Mechanical consistency: the envelope payload worktree_root must agree
    # with the pointer's trusted binding_root.
    binding_root = pointer.get("binding_root")
    if not isinstance(binding_root, str) or not binding_root.strip():
        _fail("pointer binding_root missing")
    try:
        envelope_data = json.loads(resolved_envelope.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _fail(f"envelope unreadable: {type(exc).__name__}")
    payload = envelope_data.get("payload") if isinstance(envelope_data, dict) else None
    if not isinstance(payload, dict):
        _fail("envelope payload missing")
    envelope_root = str(payload.get("worktree_root") or "").strip()
    if not envelope_root:
        _fail("envelope worktree_root missing")
    try:
        envelope_root_resolved = str(Path(envelope_root).resolve())
        binding_root_resolved = str(Path(binding_root).resolve())
    except OSError as exc:  # noqa: BLE001
        _fail(f"binding root unreadable: {type(exc).__name__}")
    if envelope_root_resolved != binding_root_resolved:
        _fail("envelope worktree_root contradicts the pointer binding_root")
    try:
        instance_dir.relative_to(Path(binding_root_resolved))
    except ValueError:
        _fail("instance directory escapes the pointer binding_root")
    return resolved_envelope


def _resolve_bootstrap(instance_dir: Path, pointer: dict, binding_root: Path) -> Path:
    bootstrap_path = pointer.get("bootstrap_path")
    if not isinstance(bootstrap_path, str) or not bootstrap_path.strip():
        _fail("task-main pointer bootstrap_path missing")
    try:
        resolved = Path(bootstrap_path).resolve(strict=True)
    except OSError as exc:
        _fail(f"task-main bootstrap missing: {type(exc).__name__}")
    try:
        resolved.relative_to(binding_root / ".aota")
    except ValueError:
        _fail("task-main bootstrap escapes the binding .aota boundary")
    return resolved


def main() -> None:
    instance_dir = Path(os.environ.get("AOTA_OPENCODE_MCP_INSTANCE_DIR", os.getcwd())).resolve()
    # AF #59 M1: no pointer => ordinary workspace session. The single AOTA MCP
    # starts normally and serves the unbound host transport; authority is
    # resolved per operation from canonical refs at the operation boundary.
    if not (instance_dir / POINTER_RELATIVE_PATH).is_file():
        _run_unbound(instance_dir)
        return
    pointer = _read_pointer(instance_dir)
    kind = pointer["kind"]
    envelope = _resolve_envelope(instance_dir, pointer)

    repo_path = _repo_root_or_fail()
    child_env = _base_child_env(repo_path)
    child_env[PRE_RESOLVED_BINDING_ENV] = str(envelope)
    resolved_bootstrap = None
    if kind == BINDING_KIND_TASK_MAIN:
        resolved_bootstrap = _resolve_bootstrap(
            instance_dir, pointer, Path(pointer["binding_root"]).resolve()
        )
        child_env[TASK_MAIN_BOOTSTRAP_ENV] = str(resolved_bootstrap)
    else:
        child_env.pop(TASK_MAIN_BOOTSTRAP_ENV, None)

    _debug(
        {
            "event": "m3_mcp_binding_resolved",
            "instance_dir": str(instance_dir),
            "kind": kind,
            "envelope": str(envelope),
            "bootstrap": str(resolved_bootstrap) if resolved_bootstrap is not None else None,
            "runtime_config": child_env.get(RUNTIME_CONFIG_ENV, ""),
        }
    )
    _exec_mcp(child_env)


if __name__ == "__main__":
    main()
