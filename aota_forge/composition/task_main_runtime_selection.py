"""AF #53 M3/W1 — Side-by-side production task-main runtime selection.

Bounded trusted operator/runtime selection between the two production
task-main composition paths that coexist in this source tree:

    legacy compatibility path   (existing task_main_host_bootstrap composition)
    thin production candidate   (accepted M2 thin trusted host composition)

Conceptually:

    trusted operator RuntimeConfig.runtime_path
            |
    production launcher
            |
    runtime-path selector  (this module)
           / \\
      legacy   thin
        |       |
    existing   M2 accepted
    bootstrap  thin composition

What this module is and is not:

* it is a deployment mechanic: trusted, bounded, explicit, documented and
  non-model-facing. The task-main model, a handoff payload, Plan prose or a
  startup prompt cannot select the path
  (``MODEL_CAN_SELECT_RUNTIME_PATH=no``,
  ``HANDOFF_CAN_SELECT_RUNTIME_PATH=no``,
  ``STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH=no``);
* it is NOT a workflow strategy seam: it decides only which production
  composition path runs, never the next Work Item, review timing/count,
  repair action, Milestone transition or child role
  (``RUNTIME_SELECTION_IS_WORKFLOW_STRATEGY=no``);
* M3/W3 cutover: the absent default is now ``thin``
  (``THIN_PATH_PRODUCTION_DEFAULT=yes``, ``PRODUCTION_DEFAULT_CUTOVER=yes``)
  after the M3/W2 fresh production dogfood PASS; explicit trusted
  ``legacy`` remains an operator override for the frozen compatibility path
  (``LEGACY_PATH_COMPATIBILITY_ONLY=yes``);
* it does not create a second RuntimeConfig, a second MCP tool plane, a new
  execution/authority/result/session/workflow engine or a generic plugin
  framework.

The thin bootstrap materialized here is operator-owned (0600, atomic write)
and deliberately carries NO MilestonePlanView, coordinator store,
TaskMainControlService, review state, READY calculation or
task_main.advance_once reference. The thin production binding is rebuilt
from this bootstrap through the accepted M2 thin composition
(``compose_thin_task_main_host``), so the thin executed path loads no legacy
workflow-brain module.
"""

from __future__ import annotations

import json
import re
from os import PathLike
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.config import (
    DEFAULT_TASK_MAIN_RUNTIME_PATH,
    SUPPORTED_TASK_MAIN_RUNTIME_PATHS,
    TASK_MAIN_RUNTIME_PATH_LEGACY,
    TASK_MAIN_RUNTIME_PATH_THIN,
    RuntimeConfig,
)
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    TrustedWorkerBinding,
)

# ---------------------------------------------------------------------------
# Ownership and architecture markers
# ---------------------------------------------------------------------------

TASK_MAIN_RUNTIME_PATH_SELECTION_OWNER = (
    "aota_forge/composition/task_main_runtime_selection.py"
)
TASK_MAIN_RUNTIME_PATH_SELECTION_KIND = "trusted_operator_deployment_mechanic"

MODEL_CAN_SELECT_RUNTIME_PATH = False
HANDOFF_CAN_SELECT_RUNTIME_PATH = False
STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH = False
PLAN_PROSE_CAN_SELECT_RUNTIME_PATH = False
AOTA_INVOKE_CAN_SELECT_RUNTIME_PATH = False

RUNTIME_SELECTION_IS_DEPLOYMENT_MECHANIC = True
RUNTIME_SELECTION_IS_WORKFLOW_STRATEGY = False

THIN_PATH_PRODUCTION_DEFAULT = True
PRODUCTION_DEFAULT_CUTOVER = True
M3_W1_THIN_IS_DEFAULT = False
M3_W3_THIN_IS_DEFAULT = True

LEGACY_PRODUCTION_PATH_PRESERVED = True
LEGACY_PATH_COMPATIBILITY_ONLY = True
LEGACY_DELETION_PERFORMED = False
NO_FURTHER_LEGACY_SEMANTIC_EXPANSION = True

SECOND_RUNTIME_CONFIG_CREATED = False
SECOND_MCP_TOOL_PLANE_CREATED = False
NEW_EXECUTION_ENGINE_CREATED = False
NEW_AUTHORITY_ENGINE_CREATED = False
NEW_RESULT_ONTOLOGY_CREATED = False
NEW_SESSION_ENGINE_CREATED = False
NEW_WORKFLOW_ENGINE_CREATED = False

# Thin operator-owned bootstrap file (distinct from the legacy bootstrap so
# no legacy builder can ever consume it, and vice versa).
THIN_BOOTSTRAP_RELPATH = ".aota/task-main-thin-bootstrap.json"
THIN_BOOTSTRAP_VERSION = 1

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_ORIGIN_LEN = 512


class TaskMainRuntimeSelectionError(ValueError):
    """Trusted runtime path selection is invalid; fail closed (no fallback)."""


# ---------------------------------------------------------------------------
# Trusted selection
# ---------------------------------------------------------------------------


def normalize_task_main_runtime_path(value: Any) -> str:
    """Validate an operator-supplied runtime path value, fail closed.

    Exact membership only. Unsupported values (aliases, different casing,
    surrounding whitespace) fail closed; there is no fuzzy alias matching and
    no silent fallback to ``legacy``.
    """
    if value is None:
        return DEFAULT_TASK_MAIN_RUNTIME_PATH
    if not isinstance(value, str) or type(value) is not str:
        raise TaskMainRuntimeSelectionError(
            f"task-main runtime path must be a string, got {type(value).__name__}"
        )
    if value not in SUPPORTED_TASK_MAIN_RUNTIME_PATHS:
        raise TaskMainRuntimeSelectionError(
            "task-main runtime path must be exactly one of "
            f"{list(SUPPORTED_TASK_MAIN_RUNTIME_PATHS)}, got {value!r} "
            "(no fuzzy alias, no silent fallback)"
        )
    return value


def select_task_main_runtime_path(runtime_config: Any) -> str:
    """Resolve the trusted production runtime path from operator config.

    The only input is the existing operator-owned RuntimeConfig authority
    (``runtime_path``). Missing/other values fail closed. No model, handoff,
    Plan prose, startup prompt or ``aota.invoke`` argument participates.
    """
    if isinstance(runtime_config, RuntimeConfig):
        value = runtime_config.runtime_path
    else:
        value = getattr(runtime_config, "runtime_path", None)
    return normalize_task_main_runtime_path(value)


def is_thin_task_main_runtime_path(value: Any) -> bool:
    return normalize_task_main_runtime_path(value) == TASK_MAIN_RUNTIME_PATH_THIN


# ---------------------------------------------------------------------------
# Operator-side thin bootstrap materialization (trusted, 0600, atomic)
# ---------------------------------------------------------------------------


def _validate_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskMainRuntimeSelectionError(
            f"thin bootstrap {label} must be a non-empty string"
        )
    candidate = value.strip()
    if not _SAFE_ID.fullmatch(candidate):
        raise TaskMainRuntimeSelectionError(
            f"thin bootstrap {label} must be a bounded trusted identifier"
        )
    return candidate


def _validate_origin_session(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskMainRuntimeSelectionError(
            "thin bootstrap origin_task_main_session_ref must be non-empty"
        )
    candidate = value.strip()
    if len(candidate) > _MAX_ORIGIN_LEN:
        raise TaskMainRuntimeSelectionError(
            "thin bootstrap origin_task_main_session_ref exceeds bound"
        )
    return candidate


def _validate_worktree_root(value: Any) -> Path:
    if not isinstance(value, (str, PathLike)):
        raise TaskMainRuntimeSelectionError(
            "thin bootstrap worktree_root must be a filesystem path"
        )
    root = Path(value)
    try:
        root = root.resolve()
    except OSError as exc:
        raise TaskMainRuntimeSelectionError(
            f"thin bootstrap worktree_root inaccessible: {exc}"
        ) from exc
    if not root.is_dir():
        raise TaskMainRuntimeSelectionError(
            f"thin bootstrap worktree_root must be a directory: {root!r}"
        )
    return root


def materialize_thin_task_main_bootstrap(
    *,
    worktree_root: str | PathLike[str],
    project_id: str,
    worktree_id: str,
    runtime_config_path: str | PathLike[str],
    origin_task_main_session_ref: str,
    execution_store_path: str | PathLike[str] | None = None,
    executor_id: str = "hermes",
    observation_evidence_path: str | None = None,
    observation_run_ref: str | None = None,
    git_integration_branch: str | None = None,
    git_remote: str | None = None,
    plan_ref: str | None = None,
    source_repository: str | None = None,
    registry_path: str | PathLike[str] | None = None,
) -> Path:
    """Write the operator-owned thin task-main bootstrap (0600, digest-bound later).

    Contains only trusted runtime identity: project/worktree, worktree root,
    operator RuntimeConfig locator, exact origin session, execution store and
    the explicit ``runtime_path=thin`` marker. It never contains
    MilestonePlanView, coordinator store, TaskMainControlService, review
    state or task_main.advance_once.

    AF #55 M1/W4: optional trusted Plan project identity —
    ``source_repository`` (Plan SOURCE_REPOSITORY assertion) and
    ``registry_path`` (operator-authorized workspace registry) — may ride the
    same operator-owned channel. Both are validated mechanically here and
    consumed by the canonical project binding; they never become model
    arguments.
    """
    root = _validate_worktree_root(worktree_root)
    pid = _validate_identifier(project_id, "project_id")
    wid = _validate_identifier(worktree_id, "worktree_id")
    origin = _validate_origin_session(origin_task_main_session_ref)

    config_path = Path(runtime_config_path)
    if not config_path.is_file():
        raise TaskMainRuntimeSelectionError(
            f"thin bootstrap runtime config missing: {config_path!r}"
        )
    config_path = config_path.resolve()

    exec_path = (
        Path(execution_store_path) if execution_store_path is not None
        else root / ".aota" / "execution.json"
    )
    exec_path = exec_path.resolve()
    try:
        exec_path.relative_to(root)
    except ValueError as exc:
        raise TaskMainRuntimeSelectionError(
            "thin bootstrap execution store must be inside worktree_root"
        ) from exc

    if not isinstance(executor_id, str) or not executor_id.strip():
        raise TaskMainRuntimeSelectionError("thin bootstrap executor_id invalid")

    dest = root / THIN_BOOTSTRAP_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "runtime_path": TASK_MAIN_RUNTIME_PATH_THIN,
        "bootstrap_version": THIN_BOOTSTRAP_VERSION,
        "project_id": pid,
        "worktree_id": wid,
        "worktree_root": str(root),
        "runtime_config_path": str(config_path),
        "execution_store_path": str(exec_path),
        "origin_task_main_session_ref": origin,
        "executor_id": executor_id.strip(),
    }
    # AF #54 M3/W2 optional bounded passive-observation configuration carried
    # through the trusted operator bootstrap (mechanical, non-authoritative;
    # absent unless the operator explicitly configured a per-run evidence
    # sink). Bounded identifiers only; never model input.
    if observation_evidence_path is not None and str(observation_evidence_path).strip():
        candidate = str(observation_evidence_path).strip()
        if len(candidate) > 1024 or "\x00" in candidate:
            raise TaskMainRuntimeSelectionError("thin bootstrap observation evidence path invalid")
        payload["observation_evidence_path"] = candidate
    if observation_run_ref is not None and str(observation_run_ref).strip():
        candidate_run = str(observation_run_ref).strip()
        if len(candidate_run) > 128 or not _SAFE_ID.fullmatch(candidate_run):
            raise TaskMainRuntimeSelectionError("thin bootstrap observation run_ref invalid")
        payload["observation_run_ref"] = candidate_run
    # AF #54 M5/W1 optional bounded trusted Git lifecycle configuration
    # (mechanical Control-Plane facts: which integration branch / which
    # remote. Absent => no lifecycle mutation authority; fail closed).
    if git_integration_branch is not None and str(git_integration_branch).strip():
        branch = str(git_integration_branch).strip()
        if len(branch) > 128 or not _SAFE_ID.fullmatch(branch):
            raise TaskMainRuntimeSelectionError("thin bootstrap git integration branch invalid")
        payload["git_integration_branch"] = branch
    if git_remote is not None and str(git_remote).strip():
        remote = str(git_remote).strip()
        if len(remote) > 64 or not _SAFE_ID.fullmatch(remote):
            raise TaskMainRuntimeSelectionError("thin bootstrap git remote invalid")
        payload["git_remote"] = remote
    # AF #54 M5/W2: trusted bound-Plan reference ("owner/repo#number").
    # Operator/runtime-supplied at launch; the model never re-states owner,
    # repo or issue per call — GitHub operations mechanically ground from it.
    if plan_ref is not None and str(plan_ref).strip():
        candidate_plan = str(plan_ref).strip()
        if len(candidate_plan) > 256:
            raise TaskMainRuntimeSelectionError("thin bootstrap plan_ref invalid")
        try:
            from aota_forge.work_plane.github_tools import parse_plan_ref

            parse_plan_ref(candidate_plan)
        except Exception as exc:
            raise TaskMainRuntimeSelectionError(f"thin bootstrap plan_ref invalid: {exc}") from exc
        payload["plan_ref"] = candidate_plan
    # AF #55 M1/W4: optional trusted Plan project identity. A malformed
    # declared SOURCE_REPOSITORY fails closed at materialization (before any
    # session launch); the registry path must be an existing trusted file.
    if source_repository is not None and str(source_repository).strip():
        declared = str(source_repository).strip()
        if len(declared) > 512 or "\x00" in declared:
            raise TaskMainRuntimeSelectionError("thin bootstrap source_repository invalid")
        from aota_forge.core.project.repository_identity import (
            normalize_repository_identity,
        )

        try:
            normalize_repository_identity(declared)
        except ValueError as exc:
            raise TaskMainRuntimeSelectionError(
                f"thin bootstrap source_repository not a repository identity: {exc}"
            ) from exc
        payload["source_repository"] = declared
    if registry_path is not None and str(registry_path).strip():
        registry = Path(str(registry_path)).resolve()
        if registry.is_symlink() or not registry.is_file():
            raise TaskMainRuntimeSelectionError(
                f"thin bootstrap registry_path must be an existing trusted file: {registry!r}"
            )
        payload["registry_path"] = str(registry)
    # AF #55 M2: the static authorized root_ref contract carried by the trusted
    # operator bootstrap. Ref names are fixed; the child re-materializes the
    # authorized root set from the resolved trusted sandbox and fails closed
    # if this declaration ever drifts.
    from aota_forge.work_plane.authorized_roots import (
        ROOT_REF_ACTIVE_WORKTREE,
        ROOT_REF_PROJECT_MAIN,
    )

    payload["authorized_root_refs"] = {
        "project": ROOT_REF_PROJECT_MAIN,
        "worktree": ROOT_REF_ACTIVE_WORKTREE,
    }
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(dest)
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest


def read_existing_thin_origin_session_ref(
    bootstrap_path: str | PathLike[str],
) -> str | None:
    """Read the previously bound thin origin session, if any (mechanical)."""
    try:
        data = json.loads(Path(bootstrap_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    candidate = str(data.get("origin_task_main_session_ref", "")).strip()
    if candidate and len(candidate) <= _MAX_ORIGIN_LEN:
        return candidate
    return None


# ---------------------------------------------------------------------------
# MCP-child-side thin binding construction (verified envelope payload only)
# ---------------------------------------------------------------------------

_THIN_BOOTSTRAP_REQUIRED_FIELDS: tuple[str, ...] = (
    "project_id",
    "worktree_id",
    "worktree_root",
    "runtime_config_path",
    "origin_task_main_session_ref",
)


def build_thin_task_main_binding_from_envelope_bootstrap(
    content: Mapping[str, Any],
    *,
    envelope_worktree_root: Any | None = None,
) -> TrustedWorkerBinding:
    """Build the thin task-main binding from a digest-verified bootstrap payload.

    Reuses the accepted M2 thin composition (``compose_thin_task_main_host``)
    so the production MCP child gets the same trusted thin binding that M2/W2
    proved: canonical project binding + worktree sandbox, canonical
    RuntimeConfig, trusted production ExecutionDispatcher, canonical single
    entry ``aota.invoke`` and ``trusted_task_main_context=None``. This is the
    ONLY thin production binding construction path.
    """
    if not isinstance(content, Mapping):
        raise TrustedBindingError("thin task-main bootstrap content must be a mapping")
    if content.get("runtime_path") != TASK_MAIN_RUNTIME_PATH_THIN:
        raise TrustedBindingError(
            "thin task-main bootstrap is missing the explicit runtime_path=thin marker"
        )
    missing = [
        field
        for field in _THIN_BOOTSTRAP_REQUIRED_FIELDS
        if not str(content.get(field, "")).strip()
    ]
    if missing:
        raise TrustedBindingError(
            f"thin task-main bootstrap missing trusted fields: {missing}"
        )

    root = _validate_worktree_root(content["worktree_root"])
    if envelope_worktree_root is not None:
        try:
            expected_root = Path(str(envelope_worktree_root)).resolve()
        except OSError as exc:
            raise TrustedBindingError(
                f"thin envelope worktree_root unreadable: {exc}"
            ) from exc
        if root != expected_root:
            raise TrustedBindingError(
                "thin bootstrap worktree_root does not match envelope worktree_root"
            )

    execution_store = None
    raw_exec = content.get("execution_store_path")
    if raw_exec is not None and str(raw_exec).strip():
        exec_path = Path(str(raw_exec)).resolve()
        try:
            exec_path.relative_to(root)
        except ValueError as exc:
            raise TrustedBindingError(
                "thin bootstrap execution store is outside worktree_root"
            ) from exc
        execution_store = FileBackedExecutionStateStore(exec_path)

    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

    source_repository = str(content.get("source_repository") or "").strip() or None
    registry_path = str(content.get("registry_path") or "").strip() or None
    # AF #55 M2: the bootstrap's static authorized root_ref declaration must
    # match the canonical contract exactly (fail closed on drift). The actual
    # authorized root set is re-materialized child-side from the resolved
    # trusted sandbox by ``compose_thin_task_main_host``.
    declared_root_refs = content.get("authorized_root_refs")
    if declared_root_refs is not None:
        from aota_forge.work_plane.authorized_roots import (
            ROOT_REF_ACTIVE_WORKTREE,
            ROOT_REF_PROJECT_MAIN,
        )

        if not isinstance(declared_root_refs, Mapping):
            raise TrustedBindingError("thin bootstrap authorized_root_refs must be a mapping")
        if (
            str(declared_root_refs.get("project", "")).strip() != ROOT_REF_PROJECT_MAIN
            or str(declared_root_refs.get("worktree", "")).strip() != ROOT_REF_ACTIVE_WORKTREE
        ):
            raise TrustedBindingError(
                "thin bootstrap authorized_root_refs declaration drifts from the trusted root_ref contract"
            )
    host = compose_thin_task_main_host(
        worktree_root=root,
        project_id=str(content["project_id"]),
        worktree_id=str(content["worktree_id"]),
        runtime_config_path=str(content["runtime_config_path"]),
        origin_task_main_session_ref=str(content["origin_task_main_session_ref"]),
        execution_store=execution_store,
        git_integration_branch=str(content.get("git_integration_branch") or "").strip() or None,
        git_remote=str(content.get("git_remote") or "").strip() or None,
        plan_ref=str(content.get("plan_ref") or "").strip() or None,
        source_repository=source_repository,
        registry_path=registry_path,
    )
    # AF #54 M3/W2: install the operator-opt-in passive observation sink from
    # the verified bootstrap (bounded, non-authoritative). Fail-isolated:
    # telemetry configuration never breaks binding construction.
    try:
        from aota_forge.work_plane.runtime_observation import configure_runtime_observation

        configure_runtime_observation(
            evidence_path=content.get("observation_evidence_path"),
            run_ref=content.get("observation_run_ref"),
            session_ref=str(content["origin_task_main_session_ref"]),
        )
    except BaseException:
        pass
    return host.trusted_binding


__all__ = [
    "TASK_MAIN_RUNTIME_PATH_SELECTION_OWNER",
    "TASK_MAIN_RUNTIME_PATH_SELECTION_KIND",
    "MODEL_CAN_SELECT_RUNTIME_PATH",
    "HANDOFF_CAN_SELECT_RUNTIME_PATH",
    "STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH",
    "PLAN_PROSE_CAN_SELECT_RUNTIME_PATH",
    "AOTA_INVOKE_CAN_SELECT_RUNTIME_PATH",
    "RUNTIME_SELECTION_IS_DEPLOYMENT_MECHANIC",
    "RUNTIME_SELECTION_IS_WORKFLOW_STRATEGY",
    "THIN_PATH_PRODUCTION_DEFAULT",
    "PRODUCTION_DEFAULT_CUTOVER",
    "M3_W1_THIN_IS_DEFAULT",
    "M3_W3_THIN_IS_DEFAULT",
    "LEGACY_PRODUCTION_PATH_PRESERVED",
    "LEGACY_PATH_COMPATIBILITY_ONLY",
    "LEGACY_DELETION_PERFORMED",
    "NO_FURTHER_LEGACY_SEMANTIC_EXPANSION",
    "SECOND_RUNTIME_CONFIG_CREATED",
    "SECOND_MCP_TOOL_PLANE_CREATED",
    "NEW_EXECUTION_ENGINE_CREATED",
    "NEW_AUTHORITY_ENGINE_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_SESSION_ENGINE_CREATED",
    "NEW_WORKFLOW_ENGINE_CREATED",
    "THIN_BOOTSTRAP_RELPATH",
    "THIN_BOOTSTRAP_VERSION",
    "DEFAULT_TASK_MAIN_RUNTIME_PATH",
    "SUPPORTED_TASK_MAIN_RUNTIME_PATHS",
    "TASK_MAIN_RUNTIME_PATH_LEGACY",
    "TASK_MAIN_RUNTIME_PATH_THIN",
    "TaskMainRuntimeSelectionError",
    "normalize_task_main_runtime_path",
    "select_task_main_runtime_path",
    "is_thin_task_main_runtime_path",
    "materialize_thin_task_main_bootstrap",
    "read_existing_thin_origin_session_ref",
    "build_thin_task_main_binding_from_envelope_bootstrap",
]
