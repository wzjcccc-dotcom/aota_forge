"""AF #58 M2 — trusted interactive session ingress service.

Two-phase trusted ingress for an OpenChamber ``:3002`` New Chat:

    Phase A (reserve)   mechanical seam, no authority
        mint preparation id + unique per-session instance namespace
        (under the operator-owned AF interactive workspace root)

    Phase B (bind)      server-side trusted authority
        verify exact OpenCode session S0 (id / directory / metadata /
        profile) against the pinned host, read the LIVE Plan through the
        accepted Plan-authority read path, resolve project + source +
        trusted root through the accepted #55 binding, materialize the
        EXISTING digest-bound task-main bootstrap/envelope/pointer into the
        exact instance namespace, and only then let the original operator
        message be dispatched.

Hard boundaries:

* ``USER_PLAN_REF_IS_INTENT=yes`` / ``USER_PLAN_REF_IS_AUTHORITY=no``:
  the message only proposes a canonical reference.
* ``PREPARATION_TOKEN_IS_AUTHORITY=no``: the preparation record is staging.
* ``SECOND_PLAN_PARSER_CREATED=no``: the live Plan is read through the
  accepted ``GitHubPlanAuthorityReadAdapter`` and normalized by
  ``normalize_portable_plan`` (the single Plan ontology).
* ``SECOND_BINDING_ONTOLOGY_CREATED=no``: the envelope, pointer and thin
  bootstrap are the accepted #56 task-main materialization, byte-for-byte
  the same format the headless launcher produces.
* ``SECOND_MCP_SERVER=no``: the host-spawned MCP child resolves the same
  per-instance pointer through the existing deployed wrapper.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from aota_forge.adapters.opencode.task_main import (
    BINDING_KIND_TASK_MAIN,
    instance_directory,
    read_binding_pointer,
    stage_envelope_in_instance,
    write_binding_pointer,
)
from aota_forge.composition.project_binding import resolve_trusted_project_binding
from aota_forge.composition.task_main_runtime_selection import (
    materialize_thin_task_main_bootstrap,
)
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PortablePlanDocument
from aota_forge.core.project.repository_identity import normalize_repository_identity
from aota_forge.interactive_ingress.contract import (
    DEFAULT_PREPARATION_TTL_SECONDS,
    INTERACTIVE_SCHEMA,
    PREPARATION_STATE_BOUND,
    PLAN_PROJECT_IDENTITY_MISSING,
    PLAN_REF_AMBIGUOUS,
    PLAN_UNREADABLE,
    PROFILE_MISMATCH,
    PROJECT_RESOLUTION_FAILED,
    PROJECT_ROOT_MISMATCH,
    SESSION_ALREADY_BOUND_DIFFERENT_PLAN,
    SESSION_DIRECTORY_MISMATCH,
    SESSION_METADATA_PREPARATION_KEY,
    SESSION_METADATA_SCHEMA_KEY,
    SESSION_NOT_AF_INTERACTIVE,
    STALE_PREPARATION,
    UNKNOWN_SESSION,
    WORKSPACE_ROOT_INVALID,
    CROSS_SESSION_BIND,
    BINDING_TAMPERED,
    MATERIALIZATION_FAILED,
    INVALID_INPUT,
    InteractiveIngressError,
    InteractivePreparation,
    derive_interactive_plan_id,
    extract_canonical_plan_refs,
    new_preparation,
    normalize_message,
    preparation_bind_receipt_path,
    preparation_record_path,
    require_bounded_profile,
    require_single_plan_ref,
    validate_trusted_plan_state,
)
from aota_forge.runtime.config import RuntimeConfig, load_runtime_config, task_main_host_profile
from aota_forge.runtime.trusted_runtime_binding import (
    create_task_main_envelope,
    verify_envelope,
)
from aota_forge.work_plane.github_tools import parse_plan_ref

# Truthful architecture markers (AF #58 M2).
PREPARATION_TOKEN_IS_AUTHORITY = False
SECOND_PLAN_PARSER_CREATED = False
SECOND_BINDING_ONTOLOGY_CREATED = False
MATERIALIZE_REUSES_EXISTING_TASK_MAIN_FORMAT = True
INTERACTIVE_INGRESS_IS_AUTHORITY = False

WORKSPACE_ROOT_ENV = "AF_INTERACTIVE_WORKSPACE_ROOT"
WORKSPACE_ROOT_DEFAULT = "/home/latios/workspace/.aota/interactive"


def resolve_workspace_root(explicit: str | Path | None = None) -> Path:
    """Operator-owned AF interactive workspace root (mechanical staging area)."""
    raw = explicit if explicit is not None else os.environ.get(WORKSPACE_ROOT_ENV)
    if raw is None or not str(raw).strip():
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID,
            f"AF interactive workspace root is not configured ({WORKSPACE_ROOT_ENV})",
        )
    candidate = Path(str(raw).strip())
    if not candidate.is_absolute():
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID, "AF interactive workspace root must be an absolute path"
        )
    if candidate.is_symlink():
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID, "AF interactive workspace root must not be a symlink"
        )
    if candidate.exists() and not candidate.is_dir():
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID, "AF interactive workspace root must be a directory"
        )
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def trusted_plan_state_from_document(
    document: PortablePlanDocument,
    *,
    plan_ref: str,
    plan_id: str,
    source_revision: str | None,
    source_digest: str | None,
    authority_source_kind: str = "github_issue",
) -> dict[str, Any]:
    """Bounded trusted Plan projection derived from the single Plan ontology.

    Mechanical projection only (current milestone + approval truth + revision
    identity).  It grants nothing; it exists so ``role.bootstrap`` can expose
    the already-read live Plan state instead of asking the model to guess.
    """
    milestone = str(getattr(document, "current_milestone", "") or "").strip()
    approval: bool | None
    approvals = getattr(document, "milestone_approvals", None) or {}
    if milestone and milestone in approvals:
        approval = bool(approvals[milestone])
    else:
        approval = None
    status = str((getattr(document, "milestone_status", None) or {}).get(milestone, "") or "")
    state = {
        "plan_ref": str(plan_ref),
        "plan_id": str(plan_id),
        # The bound authority source kind (the source-neutral PlanAuthorityBinding
        # vocabulary), not the transport-level document source marker.
        "source_kind": str(authority_source_kind),
        "source_revision": str(source_revision) if source_revision is not None else None,
        "source_digest": str(source_digest or getattr(document, "source_digest", "") or ""),
        "current_milestone": milestone,
        "milestone_status": status,
        "milestone_user_approval_satisfied": approval,
    }
    validate_trusted_plan_state(state)
    return state


def reserve_interactive_session(
    *,
    workspace_root: str | Path | None = None,
    ttl_seconds: float = DEFAULT_PREPARATION_TTL_SECONDS,
    now_fn: Callable[[], float] = time.time,
    runtime_config_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Phase A: mint the mechanical preparation + unique instance namespace.

    Grants no authority: no Plan is read, no project is resolved, no binding
    pointer is written.  The namespace exists so the pinned host's per
    directory MCP child can never be shared between chats.
    """
    root = resolve_workspace_root(workspace_root)
    preparation = new_preparation(
        workspace_root=root,
        ttl_seconds=ttl_seconds,
        now=now_fn(),
        runtime_config_path=str(runtime_config_path or ""),
        registry_path=str(registry_path or ""),
    )
    active_worktree = Path(preparation.worktree_root)
    if active_worktree.is_symlink():
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID, "preparation worktree must not be a symlink"
        )
    active_worktree.mkdir(parents=True, exist_ok=True)
    # Reuse the accepted per-instance namespace allocator (containment + key
    # grammar + symlink refusal owned by the #56 adapter).
    instance = instance_directory(active_worktree, preparation.instance_key)
    if str(instance) != preparation.instance_dir:
        raise InteractiveIngressError(
            WORKSPACE_ROOT_INVALID, "instance namespace allocation drifted from the record"
        )
    preparation.write(preparation_record_path(root, preparation.preparation_id))
    return {
        "ok": True,
        "preparation_id": preparation.preparation_id,
        "instance_key": preparation.instance_key,
        "instance_dir": str(instance),
        "worktree_root": preparation.worktree_root,
        "session_root": preparation.session_root,
        "expires_at": preparation.expires_at,
        "authority": False,
    }


def _load_trusted_runtime_config(runtime_config_path: str | Path | None) -> tuple[RuntimeConfig, Path]:
    raw = runtime_config_path or os.environ.get("AF_INTERACTIVE_RUNTIME_CONFIG") or os.environ.get(
        "AOTA_FORGE_RUNTIME_CONFIG"
    )
    if raw is None or not str(raw).strip():
        raise InteractiveIngressError(
            INVALID_INPUT, "interactive ingress requires the operator runtime config path"
        )
    path = Path(str(raw)).resolve()
    if path.is_symlink() or not path.is_file():
        raise InteractiveIngressError(
            INVALID_INPUT, f"interactive ingress runtime config missing: {path}"
        )
    return load_runtime_config(config_path=str(path)), path


def _load_registry_path(registry_path: str | Path | None) -> Path:
    raw = registry_path or os.environ.get("AF_INTERACTIVE_REGISTRY")
    if raw is None or not str(raw).strip():
        raise InteractiveIngressError(
            INVALID_INPUT, "interactive ingress requires the operator workspace registry path"
        )
    path = Path(str(raw)).resolve()
    if path.is_symlink() or not path.is_file():
        raise InteractiveIngressError(
            INVALID_INPUT, f"interactive ingress registry missing: {path}"
        )
    return path


def _envelope_digest(envelope_path: str | Path) -> str:
    try:
        data = json.loads(Path(envelope_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - digest is evidence only; verification already happened
        return ""
    return str(data.get("digest") or "") if isinstance(data, Mapping) else ""


def _default_plan_loader(plan_ref: str) -> Any:
    from aota_forge.adapters.plan_authority.github_read import GitHubPlanAuthorityReadAdapter

    repo, _owner, issue_number = parse_plan_ref(plan_ref)
    return GitHubPlanAuthorityReadAdapter(repo=repo, issue_number=issue_number).load()


def _plan_context_field(document: PortablePlanDocument, *keys: str) -> str:
    context = getattr(document, "project_context", None) or {}
    current = getattr(document, "current_fields", None) or {}
    for key in keys:
        for source in (context, current):
            value = source.get(key) if isinstance(source, Mapping) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _read_session_row(host_client: Any, session_id: str) -> dict[str, Any]:
    try:
        row = host_client.get_session(session_id)
    except Exception as exc:  # noqa: BLE001 - exact session lookup fails closed
        raise InteractiveIngressError(
            UNKNOWN_SESSION, f"OpenCode session {session_id!r} could not be resolved"
        ) from exc
    if not isinstance(row, Mapping) or str(row.get("id") or "") != session_id:
        raise InteractiveIngressError(UNKNOWN_SESSION, "OpenCode returned a non-exact session row")
    return dict(row)


def _bound_plan_ref(instance_dir: Path) -> str | None:
    """Plan ref of the already materialized binding, or None when unbound."""
    pointer_path = instance_dir / ".aota" / "opencode" / "active_binding.json"
    if not pointer_path.exists():
        return None
    try:
        pointer = read_binding_pointer(instance_dir)
    except Exception as exc:  # noqa: BLE001 - unreadable pointer fails closed
        raise InteractiveIngressError(BINDING_TAMPERED, f"binding pointer unreadable: {exc}") from exc
    if pointer.get("kind") != BINDING_KIND_TASK_MAIN:
        raise InteractiveIngressError(
            BINDING_TAMPERED, "binding pointer exists but is not a task-main binding"
        )
    envelope_path = pointer.get("envelope_path")
    try:
        kind, payload = verify_envelope(envelope_path)
    except Exception as exc:  # noqa: BLE001 - digest mismatch fails closed
        raise InteractiveIngressError(BINDING_TAMPERED, f"binding envelope invalid: {exc}") from exc
    if kind != BINDING_KIND_TASK_MAIN:
        raise InteractiveIngressError(BINDING_TAMPERED, "binding envelope kind is not task-main")
    content = payload.get("bootstrap_content") if isinstance(payload, Mapping) else None
    if not isinstance(content, Mapping):
        raise InteractiveIngressError(BINDING_TAMPERED, "binding bootstrap content missing")
    plan_ref = content.get("plan_ref")
    if not isinstance(plan_ref, str) or not plan_ref.strip():
        raise InteractiveIngressError(BINDING_TAMPERED, "binding bootstrap carries no plan_ref")
    return plan_ref.strip()


def read_bound_plan_ref(instance_dir: str | Path) -> str | None:
    """Public mechanical read of the bound Plan ref (None == unbound)."""
    return _bound_plan_ref(Path(instance_dir).resolve())


def _instance_facts(instance_dir: Path) -> dict[str, Any]:
    pointer = read_binding_pointer(instance_dir)
    kind, payload = verify_envelope(pointer.get("envelope_path"))
    content = dict(payload.get("bootstrap_content") or {})
    return {
        "instance_dir": str(instance_dir),
        "plan_ref": str(content.get("plan_ref") or ""),
        "plan_id": str(content.get("plan_id") or ""),
        "project_id": str(content.get("project_id") or ""),
        "worktree_id": str(content.get("worktree_id") or ""),
        "origin_task_main_session_ref": str(content.get("origin_task_main_session_ref") or ""),
        "bootstrap_digest": str(payload.get("bootstrap_digest") or ""),
        "envelope_digest": _envelope_digest(pointer.get("envelope_path") or ""),
        "bind_kind": kind,
    }


def bind_interactive_session(
    *,
    session_id: str,
    message_text: str,
    profile: str,
    workspace_root: str | Path | None = None,
    runtime_config_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    host_client: Any | None = None,
    plan_loader: Callable[[str], Any] | None = None,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Phase B: independently verify S0 and materialize the trusted binding.

    On success the exact already-created OpenCode session S0 owns the trusted
    task-main binding inside its own unique instance namespace.  On any
    failure the caller must NOT dispatch the operator message.
    """
    message = normalize_message(message_text)
    runtime_config, config_path = _load_trusted_runtime_config(runtime_config_path)
    expected_profile = require_bounded_profile(task_main_host_profile(runtime_config))
    requested_profile = require_bounded_profile(profile)
    if requested_profile != expected_profile:
        raise InteractiveIngressError(
            PROFILE_MISMATCH,
            f"interactive task-main ingress requires host profile {expected_profile!r}; "
            f"refusing {requested_profile!r}",
        )

    if not isinstance(session_id, str) or not session_id.startswith("ses_"):
        raise InteractiveIngressError(UNKNOWN_SESSION, "session id is not an exact OpenCode id")

    root = resolve_workspace_root(workspace_root)
    registry = _load_registry_path(registry_path)

    if host_client is None:
        from aota_forge.adapters.opencode.host_client import OpenCodeHostClient

        endpoint = getattr(runtime_config, "host_endpoint", None)
        if not endpoint:
            raise InteractiveIngressError(
                INVALID_INPUT, "operator runtime config carries no OpenCode host endpoint"
            )
        host_client = OpenCodeHostClient(endpoint)

    row = _read_session_row(host_client, session_id)
    session_directory = str(row.get("directory") or "").strip()
    if not session_directory:
        raise InteractiveIngressError(UNKNOWN_SESSION, "session row carries no directory")
    instance_dir = Path(session_directory).resolve()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    preparation_id = str(metadata.get(SESSION_METADATA_PREPARATION_KEY) or "").strip()
    if not preparation_id:
        raise InteractiveIngressError(
            SESSION_NOT_AF_INTERACTIVE,
            "session is not AF-interactive managed (missing preparation metadata)",
        )
    if str(metadata.get(SESSION_METADATA_SCHEMA_KEY) or "") != INTERACTIVE_SCHEMA:
        raise InteractiveIngressError(
            SESSION_NOT_AF_INTERACTIVE, "session preparation metadata schema mismatch"
        )
    if row.get("parentID"):
        raise InteractiveIngressError(
            SESSION_NOT_AF_INTERACTIVE, "child sessions cannot acquire a task-main binding"
        )
    row_agent = row.get("agent")
    if row_agent not in (None, "") and str(row_agent) != expected_profile:
        raise InteractiveIngressError(
            PROFILE_MISMATCH,
            f"session host row carries profile {row_agent!r}, not {expected_profile!r}",
        )

    preparation = InteractivePreparation.read(preparation_record_path(root, preparation_id))
    if preparation.instance_dir != str(instance_dir):
        raise InteractiveIngressError(
            SESSION_DIRECTORY_MISMATCH,
            "session directory does not match the prepared instance namespace",
        )
    if not str(instance_dir).startswith(str(Path(preparation.worktree_root)) + os.sep):
        raise InteractiveIngressError(
            SESSION_DIRECTORY_MISMATCH, "instance namespace escapes the prepared worktree"
        )
    if preparation.is_expired(now_fn()):
        raise InteractiveIngressError(STALE_PREPARATION, "preparation has expired")
    if preparation.bound_session_id and preparation.bound_session_id != session_id:
        raise InteractiveIngressError(
            CROSS_SESSION_BIND,
            "this preparation is already bound to another session; start a New Chat",
        )

    bound_ref = _bound_plan_ref(instance_dir)
    refs = extract_canonical_plan_refs(message)
    if bound_ref is not None:
        if preparation.bound_session_id and preparation.bound_session_id != session_id:
            raise InteractiveIngressError(
                CROSS_SESSION_BIND, "bound instance belongs to another session"
            )
        if not refs:
            return _bound_result(instance_dir, bound_ref, idempotent=True)
        if len(refs) > 1:
            raise InteractiveIngressError(
                PLAN_REF_AMBIGUOUS,
                "this chat is already bound to one Plan; mention at most that Plan",
            )
        if refs[0] != bound_ref:
            raise InteractiveIngressError(
                SESSION_ALREADY_BOUND_DIFFERENT_PLAN,
                f"this chat is already bound to {bound_ref}; start a New Chat for {refs[0]}",
            )
        return _bound_result(instance_dir, bound_ref, idempotent=True)

    plan_ref = require_single_plan_ref(message)
    plan_id = _require_plan_identity(plan_ref)
    loader = plan_loader or _default_plan_loader
    try:
        snapshot = loader(plan_ref)
    except Exception as exc:  # noqa: BLE001 - live Plan read fails closed
        raise InteractiveIngressError(
            PLAN_UNREADABLE, f"live Plan {plan_ref} could not be read"
        ) from exc
    body = getattr(snapshot, "body", None)
    revision = getattr(snapshot, "revision", None)
    snapshot_digest = getattr(snapshot, "digest", None)
    if not isinstance(body, str) or not body.strip():
        raise InteractiveIngressError(PLAN_UNREADABLE, "live Plan body is empty")
    try:
        document = normalize_portable_plan(body, source_revision=revision)
    except Exception as exc:  # noqa: BLE001 - normalization fails closed
        raise InteractiveIngressError(
            PLAN_UNREADABLE, f"live Plan {plan_ref} is not an AF-supported Portable Plan"
        ) from exc

    project_id = _plan_context_field(document, "CANONICAL_PROJECT_ID", "PROJECT_ID")
    source_repository = _plan_context_field(
        document, "CANONICAL_SOURCE_REPOSITORY", "SOURCE_REPOSITORY"
    )
    declared_root = _plan_context_field(document, "CANONICAL_PROJECT_ROOT", "KNOWN_SOURCE_ROOT")
    if not project_id or not source_repository:
        raise InteractiveIngressError(
            PLAN_PROJECT_IDENTITY_MISSING,
            "live Plan does not declare PROJECT_ID/SOURCE_REPOSITORY; refusing to guess",
        )
    try:
        normalize_repository_identity(source_repository)
    except Exception as exc:  # noqa: BLE001
        raise InteractiveIngressError(
            PLAN_PROJECT_IDENTITY_MISSING, f"Plan SOURCE_REPOSITORY is not a repository identity: {exc}"
        ) from exc
    try:
        binding_evidence = resolve_trusted_project_binding(
            project_id=project_id,
            source_repository=source_repository,
            registry_path=registry,
        )
    except Exception as exc:  # noqa: BLE001 - trusted project resolution fails closed
        raise InteractiveIngressError(
            PROJECT_RESOLUTION_FAILED, f"trusted project resolution failed for {project_id!r}"
        ) from exc
    candidates = getattr(getattr(binding_evidence, "resolution", None), "candidates", ()) or ()
    if getattr(binding_evidence, "status", "") != "RESOLVED" or len(candidates) != 1:
        raise InteractiveIngressError(
            PROJECT_RESOLUTION_FAILED, f"trusted project resolution for {project_id!r} is not singular"
        )
    repository_identity = getattr(binding_evidence, "repository_identity", None) or {}
    if not repository_identity.get("verified"):
        raise InteractiveIngressError(
            PROJECT_RESOLUTION_FAILED, "source repository identity was not mechanically verified"
        )
    canonical_project_root = str(candidates[0].project_root)
    if declared_root and Path(declared_root).resolve() != Path(canonical_project_root).resolve():
        raise InteractiveIngressError(
            PROJECT_ROOT_MISMATCH,
            f"Plan root {declared_root} does not match the trusted resolved root",
        )

    plan_state = trusted_plan_state_from_document(
        document,
        plan_ref=plan_ref,
        plan_id=plan_id,
        source_revision=revision,
        source_digest=snapshot_digest,
    )
    worktree_root = Path(preparation.worktree_root).resolve()
    worktree_id = f"{project_id}-interactive"

    try:
        bootstrap_path = materialize_thin_task_main_bootstrap(
            worktree_root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            runtime_config_path=config_path,
            origin_task_main_session_ref=session_id,
            executor_id=runtime_config.executor,
            plan_ref=plan_ref,
            plan_id=plan_id,
            source_repository=source_repository,
            registry_path=registry,
            trusted_plan_state=plan_state,
        )
        envelope = create_task_main_envelope(
            worktree_root=worktree_root,
            bootstrap_path=bootstrap_path,
            provenence={
                "host": "opencode",
                "ingress": "interactive",
                "session_id": session_id,
                "preparation_id": preparation.preparation_id,
            },
        )
        staged = stage_envelope_in_instance(instance_dir, envelope)
        pointer = write_binding_pointer(
            instance_dir,
            kind=BINDING_KIND_TASK_MAIN,
            envelope_path=staged,
            binding_root=worktree_root,
            bootstrap_path=bootstrap_path,
        )
    except InteractiveIngressError:
        raise
    except Exception as exc:  # noqa: BLE001 - materialization fails closed
        raise InteractiveIngressError(
            MATERIALIZATION_FAILED, f"trusted binding materialization failed: {exc}"
        ) from exc

    try:
        kind, payload = verify_envelope(staged)
        written_pointer = read_binding_pointer(instance_dir)
    except Exception as exc:  # noqa: BLE001
        raise InteractiveIngressError(
            MATERIALIZATION_FAILED, f"staged binding envelope failed verification: {exc}"
        ) from exc
    if kind != BINDING_KIND_TASK_MAIN:
        raise InteractiveIngressError(MATERIALIZATION_FAILED, "staged envelope kind mismatch")
    if str(written_pointer.get("envelope_path") or "") != str(staged):
        raise InteractiveIngressError(
            MATERIALIZATION_FAILED, "binding pointer does not route to the staged envelope"
        )

    receipt = {
        "record": "AF #58 M2 interactive trusted binding receipt",
        "preparation_id": preparation.preparation_id,
        "session_id": session_id,
        "instance_key": preparation.instance_key,
        "instance_dir": str(instance_dir),
        "worktree_root": str(worktree_root),
        "worktree_id": worktree_id,
        "bootstrap_path": str(bootstrap_path),
        "envelope_path": str(staged),
        "pointer_path": str(pointer),
        "envelope_digest": _envelope_digest(staged),
        "bootstrap_digest": str(payload.get("bootstrap_digest") or ""),
        "plan_ref": plan_ref,
        "plan_id": plan_id,
        "plan_source_kind": str(plan_state.get("source_kind") or ""),
        "plan_source_revision": revision,
        "plan_source_digest": str(plan_state.get("source_digest") or ""),
        "project_id": project_id,
        "source_repository": source_repository,
        "canonical_project_root": canonical_project_root,
        "repository_identity": dict(repository_identity),
        "current_milestone": str(plan_state.get("current_milestone") or ""),
        "milestone_status": str(plan_state.get("milestone_status") or ""),
        "milestone_user_approval_satisfied": plan_state.get(
            "milestone_user_approval_satisfied"
        ),
        "bound_at": float(now_fn()),
        "profile": expected_profile,
        "binding_format": "af56-m3-task-main-envelope-v1 (accepted; reused)",
        "role_bootstrap_expected_first": True,
    }
    receipt_path = preparation_bind_receipt_path(root, preparation.preparation_id)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = receipt_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=False), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(receipt_path)

    preparation.state = PREPARATION_STATE_BOUND
    preparation.bound_session_id = session_id
    preparation.bound_plan_ref = plan_ref
    preparation.write(preparation_record_path(root, preparation.preparation_id))

    return {
        "ok": True,
        "idempotent": False,
        "session_id": session_id,
        "instance_dir": str(instance_dir),
        "instance_key": preparation.instance_key,
        "plan_ref": plan_ref,
        "plan_id": plan_id,
        "plan_source_kind": str(plan_state.get("source_kind") or ""),
        "project_id": project_id,
        "source_repository": source_repository,
        "canonical_project_root": canonical_project_root,
        "current_milestone": str(plan_state.get("current_milestone") or ""),
        "milestone_user_approval_satisfied": plan_state.get(
            "milestone_user_approval_satisfied"
        ),
        "bootstrap_path": str(bootstrap_path),
        "envelope_path": str(staged),
        "envelope_digest": _envelope_digest(staged),
        "pointer_path": str(pointer),
        "binding_receipt_path": str(receipt_path),
        "role_bootstrap_expected_first": True,
    }


def _require_plan_identity(plan_ref: str) -> str:
    from aota_forge.interactive_ingress.contract import derive_interactive_plan_id

    return derive_interactive_plan_id(plan_ref)


def _bound_result(instance_dir: Path, plan_ref: str, *, idempotent: bool) -> dict[str, Any]:
    facts = _instance_facts(instance_dir)
    return {
        "ok": True,
        "idempotent": idempotent,
        "session_id": facts.get("origin_task_main_session_ref", ""),
        "instance_dir": str(instance_dir),
        "plan_ref": plan_ref,
        "plan_id": facts.get("plan_id", ""),
        "project_id": facts.get("project_id", ""),
        "envelope_digest": facts.get("envelope_digest", ""),
        "role_bootstrap_expected_first": True,
    }


def sweep_interactive_preparations(
    *,
    workspace_root: str | Path | None = None,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Remove expired, never-bound preparations (mechanical cleanup only)."""
    root = resolve_workspace_root(workspace_root)
    sessions_root = root / "sessions"
    removed: list[str] = []
    kept = 0
    if not sessions_root.is_dir():
        return {"ok": True, "removed": removed, "kept": kept}
    for entry in sorted(sessions_root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        record_path = entry / "preparation.json"
        if not record_path.is_file():
            continue
        try:
            preparation = InteractivePreparation.read(record_path)
        except Exception:  # noqa: BLE001 - unreadable records are left for inspection
            kept += 1
            continue
        pointer = Path(preparation.instance_dir) / ".aota" / "opencode" / "active_binding.json"
        if preparation.state != PREPARATION_STATE_BOUND and preparation.is_expired(now_fn()) and not pointer.exists():
            shutil.rmtree(entry)
            removed.append(preparation.preparation_id)
        else:
            kept += 1
    return {"ok": True, "removed": removed, "kept": kept}


__all__ = [
    "INTERACTIVE_INGRESS_IS_AUTHORITY",
    "MATERIALIZE_REUSES_EXISTING_TASK_MAIN_FORMAT",
    "PREPARATION_TOKEN_IS_AUTHORITY",
    "SECOND_BINDING_ONTOLOGY_CREATED",
    "SECOND_PLAN_PARSER_CREATED",
    "TRUSTED_PLAN_STATE_KEYS",
    "WORKSPACE_ROOT_DEFAULT",
    "WORKSPACE_ROOT_ENV",
    "bind_interactive_session",
    "read_bound_plan_ref",
    "reserve_interactive_session",
    "resolve_workspace_root",
    "sweep_interactive_preparations",
    "trusted_plan_state_from_document",
    "validate_trusted_plan_state",
]
