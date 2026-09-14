"""AF #54 M3/W2 — Passive runtime observation sink for production seams.

Bounded, fail-isolated delivery of existing S2/S3 observations
(``ToolUsageObservation`` / ``SkillUsageObservation``) from the canonical
production Tool/Skill lifecycle seams.

    dispatch_tool_operation / role.bootstrap / skill.open
        ↓
    passive emission (this module; failure swallowed, counted)
        ↓
    runtime observation sink (in-memory or bounded per-run evidence file)
        ↓
    existing S6 observation/projection semantics + bounded effectiveness card

Invariants
----------
* SINK_IS_TELEMETRY_AUTHORITY=no
* OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_EXECUTION_TRUTH=yes
* OBSERVATION_COUNT_PER_INVOCATION_BOUNDED=yes
* BOUNDED_SINK_RECORDS=yes (hard cap; overflow marks truncation, never grows)
* RAW_TOOL_INPUT_CAPTURED=no, RAW_TOOL_OUTPUT_CAPTURED=no,
  RAW_PROMPT_CAPTURED=no, SECRET_ENV_CAPTURED=no
* NEW_TELEMETRY_STORE_CREATED=no, NEW_EVENT_BUS_CREATED=no,
  BACKGROUND_DAEMON_CREATED=no, NEW_ANALYTICS_RUNTIME_CREATED=no
* PER_RUN_EVIDENCE_FILE_REUSES_EXISTING_OBSERVATION_SEMANTICS=yes
* RECURSIVE_OBSERVATION_CREATED=no (emission never dispatches operations)
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.observation_correlation import (
    RuntimeObservationScope,
    ToolObservationContext,
    context_from_binding,
    install_runtime_observation_scope,
    runtime_observation_scope_installed,
)
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation

# ---------------------------------------------------------------------------
# Contract identity / environment configuration
# ---------------------------------------------------------------------------

OBSERVATION_EVIDENCE_RECORD_VERSION: str = "s6-af54-m3-v1"
OBSERVATION_SINK_ENV: str = "AOTA_RUNTIME_OBSERVATION_SINK"
OBSERVATION_RUN_REF_ENV: str = "AOTA_RUNTIME_OBSERVATION_RUN_REF"
OBSERVATION_SESSION_REF_ENV: str = "AOTA_RUNTIME_OBSERVATION_SESSION_REF"

DEFAULT_MAX_SINK_RECORDS: int = 4096
DEFAULT_FILE_MAX_RECORDS: int = 8192
DEFAULT_FILE_MAX_BYTES: int = 4 * 1024 * 1024
MAX_EVIDENCE_LINE_BYTES: int = 64 * 1024

RECORD_KIND_TOOL: str = "tool_observation"
RECORD_KIND_SKILL: str = "skill_observation"
RECORD_KIND_TRUNCATED: str = "sink_truncated"

# ---------------------------------------------------------------------------
# Public invariant flags
# ---------------------------------------------------------------------------

SINK_IS_TELEMETRY_AUTHORITY: bool = False
OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_EXECUTION_TRUTH: bool = True
OBSERVATION_COUNT_PER_INVOCATION_BOUNDED: bool = True
BOUNDED_SINK_RECORDS: bool = True
RAW_TOOL_INPUT_CAPTURED: bool = False
RAW_TOOL_OUTPUT_CAPTURED: bool = False
RAW_PROMPT_CAPTURED: bool = False
SECRET_ENV_CAPTURED: bool = False
NEW_TELEMETRY_STORE_CREATED: bool = False
NEW_EVENT_BUS_CREATED: bool = False
BACKGROUND_DAEMON_CREATED: bool = False
NEW_ANALYTICS_RUNTIME_CREATED: bool = False
PER_RUN_EVIDENCE_FILE_REUSES_EXISTING_OBSERVATION_SEMANTICS: bool = True
RECURSIVE_OBSERVATION_CREATED: bool = False
SKILL_DELIVERED_IS_OBSERVED_USED: bool = False


class RuntimeObservationSinkError(RuntimeError):
    """Typed sink-side failure (never propagates into execution truth)."""


# ---------------------------------------------------------------------------
# Sink protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class RuntimeObservationSink(Protocol):
    """Bounded observation sink. Implementations must be non-authoritative."""

    def record_tool(self, observation: ToolUsageObservation) -> None:
        ...

    def record_skill(
        self,
        observation: SkillUsageObservation,
        correlation: Mapping[str, str] | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class ObservedSkill:
    """Existing S3 observation plus bounded correlation facts (evidence only)."""

    observation: SkillUsageObservation
    correlation: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"observation": self.observation.to_dict()}
        if self.correlation:
            d["correlation"] = dict(sorted(self.correlation.items()))
        return d


def _bounded_correlation_map(correlation: ToolObservationContext | Mapping[str, Any] | None) -> dict[str, str]:
    if correlation is None:
        return {}
    if isinstance(correlation, ToolObservationContext):
        raw = {
            "project_id": correlation.project_id,
            "worktree_id": correlation.worktree_id,
            "session_ref": correlation.session_ref,
            "parent_session_ref": correlation.parent_session_ref,
            "canonical_task_id": correlation.canonical_task_id,
            "milestone_ref": correlation.milestone_ref,
            "work_item_ref": correlation.work_item_ref,
            "run_ref": correlation.run_ref,
            "work_role": correlation.work_role.value if correlation.work_role is not None else None,
        }
    elif isinstance(correlation, Mapping):
        raw = dict(correlation)
    else:
        raise TypeError(
            f"correlation must be ToolObservationContext, mapping or None, got {type(correlation).__name__}"
        )
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        k = key.strip()
        v = value.strip()
        if not k or not v or len(k) > 64 or len(v) > 512 or "\x00" in v:
            continue
        out[k] = v
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# In-memory sink
# ---------------------------------------------------------------------------

class InMemoryRuntimeObservationSink:
    """Bounded in-memory sink for component/integration proofs.

    Overflow marks ``truncated`` and stops recording; it never grows past the
    configured bound and never raises to the caller.
    """

    def __init__(self, *, max_records: int = DEFAULT_MAX_SINK_RECORDS) -> None:
        if type(max_records) is not int or max_records <= 0 or max_records > 1_000_000:
            raise ValueError("max_records must be a bounded positive int")
        self._max_records = max_records
        self._lock = threading.Lock()
        self._tool: list[ToolUsageObservation] = []
        self._skill: list[ObservedSkill] = []
        self._truncated = False

    @property
    def max_records(self) -> int:
        return self._max_records

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    def record_tool(self, observation: ToolUsageObservation) -> None:
        if not isinstance(observation, ToolUsageObservation):
            raise TypeError(f"observation must be ToolUsageObservation, got {type(observation).__name__}")
        with self._lock:
            if len(self._tool) + len(self._skill) >= self._max_records:
                self._truncated = True
                return
            self._tool.append(observation)

    def record_skill(
        self,
        observation: SkillUsageObservation,
        correlation: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(observation, SkillUsageObservation):
            raise TypeError(f"observation must be SkillUsageObservation, got {type(observation).__name__}")
        bounded = _bounded_correlation_map(correlation)
        with self._lock:
            if len(self._tool) + len(self._skill) >= self._max_records:
                self._truncated = True
                return
            self._skill.append(ObservedSkill(observation=observation, correlation=bounded))

    def tool_observations(self) -> tuple[ToolUsageObservation, ...]:
        with self._lock:
            return tuple(self._tool)

    def skill_observations(self) -> tuple[ObservedSkill, ...]:
        with self._lock:
            return tuple(self._skill)

    def clear(self) -> None:
        with self._lock:
            self._tool.clear()
            self._skill.clear()
            self._truncated = False


# ---------------------------------------------------------------------------
# Bounded per-run evidence file sink
# ---------------------------------------------------------------------------

class FileRuntimeObservationSink:
    """Append-only bounded per-run observation evidence file.

    One canonical JSON line per existing observation plus bounded correlation
    facts. Records, bytes and individual line size are hard-capped; overflow
    writes a single truncation marker and stops. No raw inputs/outputs.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_records: int = DEFAULT_FILE_MAX_RECORDS,
        max_bytes: int = DEFAULT_FILE_MAX_BYTES,
    ) -> None:
        if type(max_records) is not int or max_records <= 0 or max_records > 1_000_000:
            raise ValueError("max_records must be a bounded positive int")
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > 256 * 1024 * 1024:
            raise ValueError("max_bytes must be a bounded positive int")
        self._path = Path(path)
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._records_written = 0
        self._truncated = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._records_written = self._count_existing_lines()
            if self._path.stat().st_size >= self._max_bytes:
                self._truncated = True

    @property
    def path(self) -> Path:
        return self._path

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    @property
    def records_written(self) -> int:
        with self._lock:
            return self._records_written

    def _count_existing_lines(self) -> int:
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                return sum(1 for _ in handle)
        except Exception:
            return 0

    def _append_record(self, record: Mapping[str, Any]) -> None:
        line = canonical_json(record) + "\n"
        encoded = line.encode("utf-8")
        with self._lock:
            if self._truncated:
                return
            if len(encoded) > MAX_EVIDENCE_LINE_BYTES:
                raise RuntimeObservationSinkError("observation evidence line exceeds bound")
            current_size = self._path.stat().st_size if self._path.exists() else 0
            if self._records_written + 1 > self._max_records or current_size + len(encoded) > self._max_bytes:
                self._truncated = True
                marker = canonical_json(
                    {
                        "record_version": OBSERVATION_EVIDENCE_RECORD_VERSION,
                        "record_kind": RECORD_KIND_TRUNCATED,
                        "max_records": self._max_records,
                        "max_bytes": self._max_bytes,
                        "reason": "sink_bound_reached",
                    }
                ) + "\n"
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(marker)
                return
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._records_written += 1

    def record_tool(self, observation: ToolUsageObservation) -> None:
        if not isinstance(observation, ToolUsageObservation):
            raise TypeError(f"observation must be ToolUsageObservation, got {type(observation).__name__}")
        self._append_record(
            {
                "record_version": OBSERVATION_EVIDENCE_RECORD_VERSION,
                "record_kind": RECORD_KIND_TOOL,
                "observation": observation.to_dict(),
            }
        )

    def record_skill(
        self,
        observation: SkillUsageObservation,
        correlation: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(observation, SkillUsageObservation):
            raise TypeError(f"observation must be SkillUsageObservation, got {type(observation).__name__}")
        record: dict[str, Any] = {
            "record_version": OBSERVATION_EVIDENCE_RECORD_VERSION,
            "record_kind": RECORD_KIND_SKILL,
            "observation": observation.to_dict(),
        }
        bounded = _bounded_correlation_map(correlation)
        if bounded:
            record["correlation"] = bounded
        self._append_record(record)


# ---------------------------------------------------------------------------
# Evidence reader (bounded, fail-closed per line)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeObservationEvidence:
    tool_observations: tuple[ToolUsageObservation, ...] = ()
    skill_observations: tuple[ObservedSkill, ...] = ()
    truncated: bool = False
    invalid_line_count: int = 0
    record_versions: tuple[str, ...] = ()


def load_runtime_observation_evidence(
    path: str | os.PathLike[str],
    *,
    max_records: int = DEFAULT_FILE_MAX_RECORDS,
) -> RuntimeObservationEvidence:
    """Read a bounded per-run observation evidence file.

    Invalid or foreign records are skipped and counted; never raise. Raw
    content cannot appear because the writer only serializes bounded
    observations.
    """
    p = Path(path)
    if not p.is_file():
        return RuntimeObservationEvidence()
    tools: list[ToolUsageObservation] = []
    skills: list[ObservedSkill] = []
    invalid = 0
    truncated = False
    versions: set[str] = set()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return RuntimeObservationEvidence()
    for line in lines[:max_records]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            invalid += 1
            continue
        if not isinstance(record, dict):
            invalid += 1
            continue
        version = record.get("record_version")
        if isinstance(version, str):
            versions.add(version)
        kind = record.get("record_kind")
        if kind == RECORD_KIND_TRUNCATED:
            truncated = True
            continue
        obs_raw = record.get("observation")
        if not isinstance(obs_raw, dict):
            invalid += 1
            continue
        try:
            if kind == RECORD_KIND_TOOL:
                tools.append(ToolUsageObservation.from_dict(obs_raw))
            elif kind == RECORD_KIND_SKILL:
                obs = SkillUsageObservation(**obs_raw)
                corr_raw = record.get("correlation")
                corr = (
                    {str(k): str(v) for k, v in corr_raw.items() if isinstance(k, str) and isinstance(v, str)}
                    if isinstance(corr_raw, dict)
                    else {}
                )
                skills.append(ObservedSkill(observation=obs, correlation=corr))
            else:
                invalid += 1
        except Exception:
            invalid += 1
    return RuntimeObservationEvidence(
        tool_observations=tuple(tools),
        skill_observations=tuple(skills),
        truncated=truncated,
        invalid_line_count=invalid,
        record_versions=tuple(sorted(versions)),
    )


# ---------------------------------------------------------------------------
# Process-level sink management + passive emission
# ---------------------------------------------------------------------------

_SINK_LOCK = threading.Lock()
_SINK: RuntimeObservationSink | None = None
_SINK_INSTALLED: bool = False
_ENV_CONFIGURED: bool = False
_HEALTH: dict[str, Any] = {
    "tool_emitted": 0,
    "skill_emitted": 0,
    "failure_count": 0,
    "last_failure": None,
}


def install_runtime_observation_sink(sink: RuntimeObservationSink | None) -> None:
    global _SINK, _SINK_INSTALLED
    if sink is not None:
        for attr in ("record_tool", "record_skill"):
            if not callable(getattr(sink, attr, None)):
                raise TypeError(f"sink must implement {attr}(), got {type(sink).__name__}")
    with _SINK_LOCK:
        _SINK = sink
        _SINK_INSTALLED = sink is not None


def get_runtime_observation_sink() -> RuntimeObservationSink | None:
    with _SINK_LOCK:
        return _SINK


def runtime_observation_sink_installed() -> bool:
    with _SINK_LOCK:
        return _SINK_INSTALLED


def clear_runtime_observation_sink() -> None:
    global _SINK, _SINK_INSTALLED, _ENV_CONFIGURED
    with _SINK_LOCK:
        _SINK = None
        _SINK_INSTALLED = False
        _ENV_CONFIGURED = False


def runtime_observation_health() -> dict[str, Any]:
    with _SINK_LOCK:
        return dict(_HEALTH)


def reset_runtime_observation_health() -> None:
    with _SINK_LOCK:
        _HEALTH.update({"tool_emitted": 0, "skill_emitted": 0, "failure_count": 0, "last_failure": None})


def _record_failure(component: str) -> None:
    with _SINK_LOCK:
        _HEALTH["failure_count"] = int(_HEALTH["failure_count"]) + 1
        _HEALTH["last_failure"] = component


def _ensure_sink_from_env() -> None:
    """Lazily install the operator-configured per-run evidence sink.

    Operator opt-in only (``AOTA_RUNTIME_OBSERVATION_SINK``). This is a
    bounded evidence-file sink, not a daemon, event bus or store.
    """
    global _SINK, _SINK_INSTALLED, _ENV_CONFIGURED
    with _SINK_LOCK:
        if _SINK_INSTALLED or _ENV_CONFIGURED:
            return
        _ENV_CONFIGURED = True
        raw_path = os.environ.get(OBSERVATION_SINK_ENV, "").strip()
        if not raw_path:
            return
        try:
            _SINK = FileRuntimeObservationSink(raw_path)
            _SINK_INSTALLED = True
        except Exception as exc:
            _HEALTH["failure_count"] = int(_HEALTH["failure_count"]) + 1
            _HEALTH["last_failure"] = f"sink_init:{type(exc).__name__}"


def ensure_runtime_observation_ready() -> None:
    """Idempotent process preparation: sink + correlation scope from env."""
    _ensure_sink_from_env()
    if not runtime_observation_scope_installed():
        run_ref = os.environ.get(OBSERVATION_RUN_REF_ENV, "").strip() or None
        session_ref = os.environ.get(OBSERVATION_SESSION_REF_ENV, "").strip() or None
        if run_ref or session_ref:
            try:
                install_runtime_observation_scope(
                    RuntimeObservationScope(run_ref=run_ref, session_ref=session_ref)
                )
            except Exception:
                pass


def emit_tool_observation_passive(observation: ToolUsageObservation) -> bool:
    """Emit one Tool observation; any sink failure is swallowed and counted."""
    if not isinstance(observation, ToolUsageObservation):
        return False
    ensure_runtime_observation_ready()
    sink = get_runtime_observation_sink()
    if sink is None:
        return False
    try:
        sink.record_tool(observation)
    except BaseException as exc:  # telemetry must never surface into execution
        _record_failure(f"record_tool:{type(exc).__name__}")
        return False
    with _SINK_LOCK:
        _HEALTH["tool_emitted"] = int(_HEALTH["tool_emitted"]) + 1
    return True


def emit_skill_observation_passive(
    observation: SkillUsageObservation,
    correlation: ToolObservationContext | Mapping[str, Any] | None = None,
) -> bool:
    """Emit one Skill observation; any sink failure is swallowed and counted."""
    if not isinstance(observation, SkillUsageObservation):
        return False
    ensure_runtime_observation_ready()
    sink = get_runtime_observation_sink()
    if sink is None:
        return False
    try:
        sink.record_skill(observation, correlation)
    except BaseException as exc:
        _record_failure(f"record_skill:{type(exc).__name__}")
        return False
    with _SINK_LOCK:
        _HEALTH["skill_emitted"] = int(_HEALTH["skill_emitted"]) + 1
    return True


# ---------------------------------------------------------------------------
# Production Skill lifecycle projectors (S3 observations at real seams)
# ---------------------------------------------------------------------------

def _bootstrap_anchor_id(binding: Any, role_str: str) -> str:
    canonical_task_id = str(getattr(binding, "canonical_task_id", "") or "")
    project_id = str(getattr(binding, "project_id", "") or "")
    worktree_id = str(getattr(binding, "worktree_id", "") or "")
    seed = f"role.bootstrap:{role_str}:{project_id}:{worktree_id}:{canonical_task_id}"
    return "skillboot-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _skill_open_anchor_id(binding: Any, role_str: str, ref: str) -> str:
    canonical_task_id = str(getattr(binding, "canonical_task_id", "") or "")
    seed = f"skill.open:{role_str}:{ref}:{canonical_task_id}"
    return "skillopen-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def emit_role_bootstrap_skill_observations(
    *,
    binding: Any,
    resolution: Any,
    projection: Any,
    allowed_universe: Any | None = None,
    pinned_refs: Any = (),
    required_refs: Any = (),
    role_default_refs: Any = (),
    recommended_refs: Any = (),
) -> int:
    """Project existing S3 observations for the real role.bootstrap lifecycle.

    Reuses ``project_skill_usage`` (S3) with an existing ``ExecutionEvent``
    anchor; selection/delivery truth is derived from the actual bootstrap
    projection. Returns the emitted observation count (0 when unavailable).
    Never raises into the caller.
    """
    from aota_forge.work_plane.events import ExecutionEvent, ExecutionEventType
    from aota_forge.work_plane.skill_usage import project_skill_usage

    try:
        role = getattr(resolution, "target_namespace", None)
        role_str = role.value if hasattr(role, "value") else str(role)
        logical_ref_map: dict[Any, str] = {}
        for entry in getattr(resolution, "selected", ()) or ():
            try:
                identity = entry.identity
                logical_ref_map[entry.composite_key] = f"{identity.skill_id}@{identity.version}"
            except Exception:
                continue
        event = ExecutionEvent(
            event_id=_bootstrap_anchor_id(binding, role_str),
            event_type=ExecutionEventType.HANDOFF_PREPARED,
            work_role=role.strip() if isinstance(role, str) else role,
            canonical_task_id=str(getattr(binding, "canonical_task_id", "") or "") or None,
        )
        observations = project_skill_usage(
            event,
            resolution,
            projection,
            allowed_universe=allowed_universe,
            logical_ref_map=logical_ref_map or None,
            pinned_refs=pinned_refs,
            required_refs=required_refs,
            role_default_refs=role_default_refs,
            recommended_refs=recommended_refs,
        )
        correlation = context_from_binding(binding)
        emitted = 0
        for observation in observations:
            if emit_skill_observation_passive(observation, correlation):
                emitted += 1
        return emitted
    except BaseException as exc:
        _record_failure(f"role_bootstrap_projection:{type(exc).__name__}")
        return 0


def emit_skill_open_observation(
    *,
    binding: Any,
    ref: str,
    entry: Any,
    role_str: str,
) -> bool:
    """Emit the existing S3 observation for a real progressive skill.open.

    The observation records the loaded (delivered) Skill identity; it NEVER
    claims observed use (``SKILL_DELIVERED_IS_OBSERVED_USED=no``).
    """
    from aota_forge.work_plane.events import ExecutionEventType

    try:
        identity = entry.identity
        observation = SkillUsageObservation(
            event_id=_skill_open_anchor_id(binding, role_str, ref),
            event_type=ExecutionEventType.EXECUTION_MATERIALIZED.value,
            namespace=role_str,
            skill_id=identity.skill_id,
            version=identity.version,
            digest=identity.digest,
            delivery="progressive",
            selection_source=None,
            provenance=identity.provenance,
            ref=ref,
        )
        return emit_skill_observation_passive(observation, context_from_binding(binding))
    except BaseException as exc:
        _record_failure(f"skill_open_projection:{type(exc).__name__}")
        return False


__all__ = [
    "OBSERVATION_EVIDENCE_RECORD_VERSION",
    "OBSERVATION_SINK_ENV",
    "OBSERVATION_RUN_REF_ENV",
    "OBSERVATION_SESSION_REF_ENV",
    "DEFAULT_MAX_SINK_RECORDS",
    "DEFAULT_FILE_MAX_RECORDS",
    "DEFAULT_FILE_MAX_BYTES",
    "RECORD_KIND_TOOL",
    "RECORD_KIND_SKILL",
    "RECORD_KIND_TRUNCATED",
    "SINK_IS_TELEMETRY_AUTHORITY",
    "OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_EXECUTION_TRUTH",
    "OBSERVATION_COUNT_PER_INVOCATION_BOUNDED",
    "BOUNDED_SINK_RECORDS",
    "RAW_TOOL_INPUT_CAPTURED",
    "RAW_TOOL_OUTPUT_CAPTURED",
    "RAW_PROMPT_CAPTURED",
    "SECRET_ENV_CAPTURED",
    "NEW_TELEMETRY_STORE_CREATED",
    "NEW_EVENT_BUS_CREATED",
    "BACKGROUND_DAEMON_CREATED",
    "NEW_ANALYTICS_RUNTIME_CREATED",
    "PER_RUN_EVIDENCE_FILE_REUSES_EXISTING_OBSERVATION_SEMANTICS",
    "RECURSIVE_OBSERVATION_CREATED",
    "SKILL_DELIVERED_IS_OBSERVED_USED",
    "RuntimeObservationSinkError",
    "RuntimeObservationSink",
    "ObservedSkill",
    "InMemoryRuntimeObservationSink",
    "FileRuntimeObservationSink",
    "RuntimeObservationEvidence",
    "load_runtime_observation_evidence",
    "install_runtime_observation_sink",
    "get_runtime_observation_sink",
    "runtime_observation_sink_installed",
    "clear_runtime_observation_sink",
    "runtime_observation_health",
    "reset_runtime_observation_health",
    "ensure_runtime_observation_ready",
    "emit_tool_observation_passive",
    "emit_skill_observation_passive",
    "emit_role_bootstrap_skill_observations",
    "emit_skill_open_observation",
]
