"""OpenCode task-main operator path — thin host translation (AF #56 M3/W1/W2).

This module is the host-side mechanical translation for launching and
continuing an EXACT OpenCode task-main session on the pinned reference host.
It adds no workflow authority, no plan reader, no bootstrap materializer and no
second result model: every trusted input is produced by the existing AF
composition seams (``DailyTaskMainLauncher`` prepare/bootstrap + RuntimeConfig)
and every host call is mechanical.

Lifecycle rule (AF #56 M3/W2, NB-3 closure — option A, task/session-scoped
instance directory):

  OpenCode host instance directory
    = trusted task/session-scoped mechanical namespace
      (``<worktree_root>/.aota/opencode/instances/<instance_key>``)

  AF authorized worktree
    = trusted source authority carried INSIDE the AF binding envelope
      (``worktree_root`` payload field), never equated with the host instance
      directory.

The pinned host spawns one MCP child per directory instance for the instance
lifetime (M2 empirical fact). Because every task/session gets its own instance
namespace, a new task never inherits the previous task's cached MCP child:

- task-main S0 gets its own ``task-main-<run>`` namespace;
- Worker Task A gets ``worker-<task A>`` and Worker Task B (same worktree)
  gets ``worker-<task B>``;
- the binding pointer + digest-verified envelope live INSIDE each instance
  namespace (``<instance_dir>/.aota/...``), so the host-spawned MCP child can
  only ever resolve the binding staged for that exact instance.

Stale task A authority can therefore never authorize task B, and task-main
authority is never visible to a Worker instance (and vice versa).

Truthful markers:

  HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE=no
  STALE_BINDING_REUSED=no
  OVERWRITE_POINTER_AND_HOPE=no
  SECOND_MCP_SERVER=no
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.adapters.opencode.host_client import (
    OpenCodeHostClient,
    OpenCodeHostError,
    OpenCodeSessionNotFoundError,
    assistant_texts_since,
    message_role,
    turn_evidence,
)
from aota_forge.adapters.opencode.profiles import (
    require_exact_profile,
    task_main_profile_default,
)

# ---------------------------------------------------------------------------
# Mechanical namespace + binding pointer contract
# ---------------------------------------------------------------------------

INSTANCE_ROOT_RELPATH: tuple[str, ...] = (".aota", "opencode", "instances")
POINTER_RELPATH: tuple[str, ...] = (".aota", "opencode", "active_binding.json")
ENVELOPE_ROOT_RELPATH: tuple[str, ...] = (".aota", "pre-resolved-bindings")

BINDING_POINTER_SCHEMA_VERSION = "1"
BINDING_KIND_WORKER = "worker"
BINDING_KIND_TASK_MAIN = "task-main"
BINDING_KINDS: frozenset[str] = frozenset({BINDING_KIND_WORKER, BINDING_KIND_TASK_MAIN})

_INSTANCE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")

# Host instance directory is a mechanical namespace; it is NEVER the AF
# semantic authority (the binding envelope carries the authorized worktree).
HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE = False
HOST_INSTANCE_DIRECTORY_IS_MECHANICAL_NAMESPACE = True
STALE_BINDING_REUSED = False
OVERWRITE_POINTER_AND_HOPE = False
SECOND_MCP_SERVER = False

# Task-main session metadata (mechanical identity for host lineage/restart
# recovery observation ONLY; never AF authority).
TASK_MAIN_METADATA_ROLE_KEY = "aota_role"
TASK_MAIN_METADATA_ROLE_VALUE = "task-main"
TASK_MAIN_METADATA_SCHEMA_KEY = "aota_schema"
TASK_MAIN_METADATA_SCHEMA = "af56-m3-task-main-v1"
TASK_MAIN_METADATA_PLAN_REF_KEY = "aota_plan_ref"
TASK_MAIN_METADATA_INSTANCE_KEY = "aota_instance_key"

# Task-main first-semantic-action gate (M3 acceptance: ROLE_BOOTSTRAP_FIRST).
ROLE_BOOTSTRAP_OPERATION = "role.bootstrap"
AOTA_INVOKE_HOST_TOOL_NAME = "aota_aota_invoke"
ROLE_BOOTSTRAP_FIRST = True

DEFAULT_TASK_MAIN_TURN_TIMEOUT_SECONDS = 900.0
DEFAULT_TASK_MAIN_TURN_POLL_SECONDS = 1.0


class OpenCodeTaskMainError(Exception):
    """Bounded mechanical task-main host-path failure (never fabricated truth)."""

    def __init__(self, message: str, code: str = "TASK_MAIN_HOST_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OpenCodeTaskMainInstanceError(OpenCodeTaskMainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INSTANCE_NAMESPACE_INVALID")


class OpenCodeTaskMainBindingError(OpenCodeTaskMainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="TASK_MAIN_BINDING_UNAVAILABLE")


class OpenCodeTaskMainTurnError(OpenCodeTaskMainError):
    def __init__(self, message: str, code: str = "TASK_MAIN_TURN_FAILED") -> None:
        super().__init__(message, code=code)


class OpenCodeRoleBootstrapFirstViolation(OpenCodeTaskMainTurnError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="ROLE_BOOTSTRAP_NOT_FIRST")


# ---------------------------------------------------------------------------
# Instance namespace allocation + binding staging
# ---------------------------------------------------------------------------


def _validate_instance_key(instance_key: str) -> str:
    if not isinstance(instance_key, str) or not instance_key.strip():
        raise OpenCodeTaskMainInstanceError("instance_key must be a non-empty string")
    candidate = instance_key.strip()
    if candidate in (".", "..") or not _INSTANCE_KEY_RE.match(candidate):
        raise OpenCodeTaskMainInstanceError(
            f"instance_key must match [A-Za-z0-9._-]{{1,96}}, got {candidate!r}"
        )
    return candidate


def task_main_instance_key(run_token: str) -> str:
    """Mechanical per-run task-main instance key (never model supplied)."""
    token = _validate_instance_key(run_token)
    return f"task-main-{token}"


def worker_instance_key(canonical_task_id: str) -> str:
    """Mechanical per-task Worker instance key derived from the canonical task id."""
    if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
        raise OpenCodeTaskMainInstanceError("canonical_task_id must be a non-empty string")
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", canonical_task_id.strip())[:64]
    digest = hashlib.sha256(canonical_task_id.strip().encode("utf-8")).hexdigest()[:12]
    return _validate_instance_key(f"worker-{sanitized}-{digest}")


def instance_directory(worktree_root: str | Path, instance_key: str) -> Path:
    """Allocate (idempotently) the mechanical host instance directory.

    The directory lives under the trusted ``.aota/opencode/instances`` boundary
    of the worktree but is NOT the AF authorized worktree: the authorized
    worktree travels inside the binding envelope.
    """
    root = Path(worktree_root).resolve()
    if not root.is_dir():
        raise OpenCodeTaskMainInstanceError(f"worktree_root must be a directory: {root}")
    key = _validate_instance_key(instance_key)
    instance_root = root.joinpath(*INSTANCE_ROOT_RELPATH).resolve()
    try:
        instance_root.relative_to(root)
    except ValueError as exc:  # pragma: no cover - joinpath cannot escape, fail closed
        raise OpenCodeTaskMainInstanceError("instance root escapes the worktree boundary") from exc
    instance_dir = instance_root / key
    if instance_dir.is_symlink():
        raise OpenCodeTaskMainInstanceError(f"instance directory must not be a symlink: {instance_dir}")
    instance_dir.mkdir(parents=True, exist_ok=True)
    try:
        instance_dir.relative_to(instance_root)
    except ValueError as exc:  # pragma: no cover
        raise OpenCodeTaskMainInstanceError("instance directory escapes the instance root") from exc
    return instance_dir


def stage_envelope_in_instance(instance_dir: str | Path, envelope_path: str | Path) -> Path:
    """Copy a digest-bound envelope into the instance namespace (bytes-exact).

    The copy is the only envelope the host-spawned MCP child of this instance
    can resolve; the source envelope remains AF-owned. A byte-identical copy
    preserves the envelope digest exactly (representation, not authority).
    """
    instance = Path(instance_dir).resolve()
    envelope = Path(envelope_path)
    try:
        data = envelope.read_bytes()
    except OSError as exc:
        raise OpenCodeTaskMainBindingError(f"binding envelope unreadable: {exc}") from exc
    target_dir = instance.joinpath(*ENVELOPE_ROOT_RELPATH)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / envelope.name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(target)
    try:
        target.chmod(0o600)
    except Exception:
        pass
    return target


def write_binding_pointer(
    instance_dir: str | Path,
    *,
    kind: str,
    envelope_path: str | Path,
    binding_root: str | Path,
    bootstrap_path: str | Path | None = None,
) -> Path:
    """Atomically write the per-instance binding pointer (routing only).

    Containment is enforced: the staged envelope must live inside THIS
    instance's ``.aota`` boundary. A worker/task-main child of another instance
    namespace can never resolve this pointer.
    """
    instance = Path(instance_dir).resolve()
    if kind not in BINDING_KINDS:
        raise OpenCodeTaskMainBindingError(f"binding kind invalid: {kind!r}")
    envelope = Path(envelope_path)
    try:
        resolved_envelope = envelope.resolve(strict=True)
    except OSError as exc:
        raise OpenCodeTaskMainBindingError(f"binding envelope missing: {exc}") from exc
    aota_root = (instance / ".aota").resolve()
    try:
        resolved_envelope.relative_to(aota_root)
    except ValueError as exc:
        raise OpenCodeTaskMainBindingError(
            "binding envelope escapes the instance .aota boundary"
        ) from exc
    root = Path(binding_root).resolve()
    if not root.is_dir():
        raise OpenCodeTaskMainBindingError(f"binding_root must be a directory: {root}")
    try:
        instance.relative_to(root)
    except ValueError as exc:
        raise OpenCodeTaskMainBindingError(
            "instance directory must live inside the trusted binding_root"
        ) from exc

    payload: dict[str, Any] = {
        "schema_version": BINDING_POINTER_SCHEMA_VERSION,
        "kind": kind,
        "envelope_path": str(resolved_envelope),
        "binding_root": str(root),
        "instance_key": instance.name,
    }
    if kind == BINDING_KIND_TASK_MAIN:
        if bootstrap_path is None or not str(bootstrap_path).strip():
            raise OpenCodeTaskMainBindingError("task-main pointer requires bootstrap_path")
        bootstrap = Path(bootstrap_path)
        try:
            resolved_bootstrap = bootstrap.resolve(strict=True)
        except OSError as exc:
            raise OpenCodeTaskMainBindingError(f"task-main bootstrap missing: {exc}") from exc
        payload["bootstrap_path"] = str(resolved_bootstrap)
    elif bootstrap_path is not None:
        raise OpenCodeTaskMainBindingError("worker pointer must not carry bootstrap_path")

    pointer_dir = instance / ".aota" / "opencode"
    pointer_dir.mkdir(parents=True, exist_ok=True)
    pointer = pointer_dir / "active_binding.json"
    tmp = pointer.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(pointer)
    try:
        pointer.chmod(0o600)
    except Exception:
        pass
    return pointer


def read_binding_pointer(instance_dir: str | Path) -> dict[str, Any]:
    """Read + validate the per-instance binding pointer (fail closed)."""
    instance = Path(instance_dir).resolve()
    pointer = instance.joinpath(*POINTER_RELPATH)
    if not pointer.is_file():
        raise OpenCodeTaskMainBindingError(f"no trusted binding pointer at {pointer}")
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable pointer fails closed
        raise OpenCodeTaskMainBindingError(f"pointer unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != BINDING_POINTER_SCHEMA_VERSION:
        raise OpenCodeTaskMainBindingError("pointer schema mismatch")
    if data.get("kind") not in BINDING_KINDS:
        raise OpenCodeTaskMainBindingError("pointer kind invalid")
    envelope_path = data.get("envelope_path")
    if not isinstance(envelope_path, str) or not envelope_path.strip():
        raise OpenCodeTaskMainBindingError("pointer envelope_path missing")
    return data


# ---------------------------------------------------------------------------
# Task-main session mechanics (exact session only; no heuristics)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskMainTurnResult:
    """Mechanical outcome of one bounded task-main turn."""

    session_id: str
    directory: str
    completed: bool
    assistant_messages: int
    first_semantic_tool: str | None
    last_assistant_finish: str | None
    detail: str = ""


def _session_create_model(model: Mapping[str, str] | None) -> dict[str, str] | None:
    if model is None:
        return None
    provider = model.get("providerID")
    model_id = model.get("modelID")
    if not provider or not model_id:
        raise OpenCodeTaskMainError("prompt model requires {providerID, modelID}")
    # Pinned v1.18.30 session-create model shape (M2 empirical correction).
    return {"id": model_id, "providerID": provider}


def resolve_operator_prompt_model(runtime_config: Any) -> dict[str, str] | None:
    """Operator-owned prompt model for the task-main session (never model input)."""
    provider = getattr(runtime_config, "provider", None)
    model = getattr(runtime_config, "model", None)
    if provider is None and model is None:
        return None
    if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
        raise OpenCodeTaskMainError(
            "provider and model must both be operator-configured for the OpenCode task-main session",
            code="MODEL_BINDING_INCOMPLETE",
        )
    return {"providerID": provider.strip(), "modelID": model.strip()}


def create_task_main_session(
    host_client: OpenCodeHostClient,
    *,
    directory: str | Path,
    instance_key: str,
    plan_ref: str = "",
    model: Mapping[str, str] | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Create the EXACT task-main session in its mechanical instance namespace.

    AF #58 M1: the exact task-main host profile is persisted on the session row.
    When the caller does not supply the operator RuntimeConfig profile, the
    accepted AF contract constant applies; the session is never created
    profile-less (the pinned host would otherwise resolve the default agent at
    message time).
    """
    directory_str = str(Path(directory).resolve())
    profile = (
        task_main_profile_default()
        if agent is None
        else require_exact_profile(agent, label="task-main profile")
    )
    metadata: dict[str, Any] = {
        TASK_MAIN_METADATA_ROLE_KEY: TASK_MAIN_METADATA_ROLE_VALUE,
        TASK_MAIN_METADATA_SCHEMA_KEY: TASK_MAIN_METADATA_SCHEMA,
        TASK_MAIN_METADATA_INSTANCE_KEY: instance_key,
    }
    if plan_ref:
        metadata[TASK_MAIN_METADATA_PLAN_REF_KEY] = plan_ref
    return host_client.create_session(
        directory=directory_str,
        title=f"af-task-main:{instance_key}"[:120],
        agent=profile,
        model=_session_create_model(model),
        metadata=metadata,
    )


def submit_task_main_turn(
    host_client: OpenCodeHostClient,
    *,
    session_id: str,
    directory: str | Path,
    text: str,
    model: Mapping[str, str] | None = None,
    agent: str | None = None,
    timeout_seconds: float = DEFAULT_TASK_MAIN_TURN_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_TASK_MAIN_TURN_POLL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> TaskMainTurnResult:
    """Deliver one bounded task-main turn and wait for exact-session termination.

    prompt_async is the mechanical delivery path (accepted non-blocking; a busy
    session joins the in-flight run). Termination evidence is read back from the
    EXACT session row only: a new assistant message plus observed idle. HTTP
    acceptance is never semantic success.

    AF #58 M1: the pinned host resolves the message-time ``agent``
    authoritatively, so EVERY task-main turn carries the exact task-main host
    profile (never the session-row default and never the host default agent).
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise OpenCodeTaskMainTurnError("session_id must be a non-empty exact session id")
    if not isinstance(text, str) or not text.strip():
        raise OpenCodeTaskMainTurnError("turn text must be a non-empty string")
    profile = (
        task_main_profile_default()
        if agent is None
        else require_exact_profile(agent, label="task-main profile")
    )
    scope = str(Path(directory).resolve())

    baseline = host_client.fetch_session_messages(session_id, directory=scope)
    baseline_count = len(baseline)
    host_client.submit_prompt_async(
        session_id,
        directory=scope,
        parts=[{"type": "text", "text": text}],
        model=dict(model) if model is not None else None,
        agent=profile,
    )

    deadline = now_fn() + float(timeout_seconds)
    while True:
        sleep_fn(float(poll_interval_seconds))
        messages = host_client.fetch_session_messages(session_id, directory=scope)
        evidence = turn_evidence(messages)
        new_assistant = [
            message
            for message in messages[baseline_count:]
            if message_role(message) == "assistant"
        ]
        if evidence.terminal_error and new_assistant:
            raise OpenCodeTaskMainTurnError(
                f"task-main turn terminated with assistant error: {evidence.last_assistant_error}",
                code="TASK_MAIN_ASSISTANT_ERROR",
            )
        if new_assistant and evidence.terminal_success:
            return TaskMainTurnResult(
                session_id=session_id,
                directory=scope,
                completed=True,
                assistant_messages=len(new_assistant),
                first_semantic_tool=first_semantic_tool_name(messages),
                last_assistant_finish=evidence.last_assistant_finish,
            )
        if now_fn() >= deadline:
            raise OpenCodeTaskMainTurnError(
                f"task-main turn did not terminate within {timeout_seconds}s "
                f"(assistant messages={len(new_assistant)}, "
                f"finish={evidence.last_assistant_finish!r})",
                code="TASK_MAIN_TURN_TIMEOUT",
            )


def first_semantic_tool_name(messages: Sequence[Mapping[str, Any]]) -> str | None:
    """First tool part name in the exact session transcript (mechanical read)."""
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, Mapping) and part.get("type") == "tool":
                tool = part.get("tool")
                if isinstance(tool, str) and tool.strip():
                    return tool.strip()
    return None


def assert_role_bootstrap_first(messages: Sequence[Mapping[str, Any]]) -> None:
    """Mechanically enforce ROLE_BOOTSTRAP_FIRST on the exact session transcript.

    The first tool call of the task-main session must be the single AF MCP
    entry invoking ``role.bootstrap``. Anything else fails closed: the trusted
    role/bootstrap context must be established before any other semantic act.
    """
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, Mapping) or part.get("type") != "tool":
                continue
            tool = part.get("tool")
            if not isinstance(tool, str) or not tool.strip():
                continue
            if tool.strip() != AOTA_INVOKE_HOST_TOOL_NAME:
                raise OpenCodeRoleBootstrapFirstViolation(
                    f"first semantic tool call was {tool.strip()!r}, not the AF aota.invoke entry"
                )
            state = part.get("state")
            tool_input = state.get("input") if isinstance(state, Mapping) else None
            operation = _extract_invoke_operation(tool_input)
            if operation != ROLE_BOOTSTRAP_OPERATION:
                raise OpenCodeRoleBootstrapFirstViolation(
                    f"first aota.invoke operation was {operation!r}, not {ROLE_BOOTSTRAP_OPERATION!r}"
                )
            return
    raise OpenCodeRoleBootstrapFirstViolation(
        "no tool call was observed in the task-main transcript; role.bootstrap not proven first"
    )


def _extract_invoke_operation(tool_input: Any) -> str | None:
    """Mechanical extraction of the operation from the aota.invoke tool input.

    The pinned host records the tool call input either as a JSON string or as a
    mapping depending on the model/tool path; both shapes are read
    mechanically. No semantic interpretation is performed.
    """
    if isinstance(tool_input, Mapping):
        operation = tool_input.get("operation")
        return operation.strip() if isinstance(operation, str) else None
    if isinstance(tool_input, str):
        try:
            decoded = json.loads(tool_input)
        except Exception:
            return None
        if isinstance(decoded, Mapping):
            operation = decoded.get("operation")
            return operation.strip() if isinstance(operation, str) else None
    return None


def native_tool_parts(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bounded mechanical read of every non-AOTA tool part in a transcript.

    Used for negative proof (RAW_SHELL_CALLS=0 / NATIVE_EDIT_CALLS=0 / ...):
    any native host tool attempt appears here and is never executed (M1-proven
    deny mechanics). The AF entry ``aota_aota_invoke`` is not native.
    """
    found: list[dict[str, Any]] = []
    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, Mapping) or part.get("type") != "tool":
                continue
            tool = part.get("tool")
            if not isinstance(tool, str) or not tool.strip():
                continue
            name = tool.strip()
            if name in (AOTA_INVOKE_HOST_TOOL_NAME, "invalid"):
                # ``invalid`` is the pinned host's built-in answer for an
                # unavailable tool name; it is evidence of a blocked attempt,
                # never a successful native execution.
                continue
            state = part.get("state")
            status = state.get("status") if isinstance(state, Mapping) else None
            found.append({"tool": name, "status": status})
    return found


__all__ = [
    "AOTA_INVOKE_HOST_TOOL_NAME",
    "BINDING_KIND_TASK_MAIN",
    "BINDING_KIND_WORKER",
    "BINDING_KINDS",
    "BINDING_POINTER_SCHEMA_VERSION",
    "DEFAULT_TASK_MAIN_TURN_POLL_SECONDS",
    "DEFAULT_TASK_MAIN_TURN_TIMEOUT_SECONDS",
    "ENVELOPE_ROOT_RELPATH",
    "HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE",
    "HOST_INSTANCE_DIRECTORY_IS_MECHANICAL_NAMESPACE",
    "INSTANCE_ROOT_RELPATH",
    "OVERWRITE_POINTER_AND_HOPE",
    "POINTER_RELPATH",
    "ROLE_BOOTSTRAP_FIRST",
    "ROLE_BOOTSTRAP_OPERATION",
    "SECOND_MCP_SERVER",
    "STALE_BINDING_REUSED",
    "TASK_MAIN_METADATA_INSTANCE_KEY",
    "TASK_MAIN_METADATA_PLAN_REF_KEY",
    "TASK_MAIN_METADATA_ROLE_KEY",
    "TASK_MAIN_METADATA_ROLE_VALUE",
    "TASK_MAIN_METADATA_SCHEMA",
    "TASK_MAIN_METADATA_SCHEMA_KEY",
    "OpenCodeRoleBootstrapFirstViolation",
    "OpenCodeTaskMainBindingError",
    "OpenCodeTaskMainError",
    "OpenCodeTaskMainInstanceError",
    "OpenCodeTaskMainTurnError",
    "TaskMainTurnResult",
    "assert_role_bootstrap_first",
    "create_task_main_session",
    "first_semantic_tool_name",
    "instance_directory",
    "native_tool_parts",
    "read_binding_pointer",
    "resolve_operator_prompt_model",
    "stage_envelope_in_instance",
    "submit_task_main_turn",
    "task_main_instance_key",
    "worker_instance_key",
    "write_binding_pointer",
]
