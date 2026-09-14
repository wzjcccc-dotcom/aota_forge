"""Trusted project registration & reconciliation lifecycle (AF #55 M1/W2).

Control-Plane-owned mechanical lifecycle for the existing AF project registry
foundation.  This module does NOT create a second registry, a project
database, a project graph, a background daemon or a filesystem watcher.  It
evolves the existing ``core/project/*`` subsystem in place:

    operator-authorized Workspace Registry  (workspaces.json)
    + per-project .aota/project.yaml        (manifest schema v1)
    + deterministic discovery/resolution    (discovery.py / resolver.py)
    = trusted project resolution

Lifecycle:
    * ``register_project`` — trusted adoption/registration of a project
      checkout inside an operator-authorized workspace, with write-through
      authorization of a new trusted workspace root when the operator/runtime
      supplies one.
    * ``reconcile_project`` — on-demand mechanical verification (no daemon):
      workspace still resolvable, manifest still present/valid, project_id
      unchanged, registered path still safe, repository identity still
      matching.  Mismatch fails closed with a typed error; there is no
      auto-rebind by name, nearest path or Git repo name.
    * ``retire_workspace_authorization`` — bounded removal of one candidate
      root from a workspace entry (explicit operator intent only).

Write-through mechanics: schema validation before replacement, atomic
``os.replace`` on the same directory, read-back verification, no silent
partial registry corruption.  No transaction service is created.

Registration requires a trusted operation: candidate roots and workspace
roots are trusted Control-Plane/runtime inputs.  A model-supplied path is
never accepted as authority (``MODEL_SELF_GRANTS_PROJECT_AUTHORITY=no``).

Invariants:
    PROJECT_REGISTRY_HAS_LIFECYCLE_OWNER=yes
    LIFECYCLE_OWNER=Control Plane / AF project subsystem (mechanical)
    PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATION=yes
    MODEL_SELF_GRANTS_PROJECT_AUTHORITY=no
    MANUAL_REGISTRY_DRIFT_REQUIRED=no
    BACKGROUND_DAEMON_REQUIRED=no
    CONTINUOUS_FILESYSTEM_WATCHER=no
    AUTO_REBIND_BY_NAME=no
    AUTO_REBIND_BY_NEAREST_PATH=no
    AUTO_REBIND_BY_GIT_REPO_NAME=no
    PROJECT_REGISTRY_REIMPLEMENTED=no
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectManifestInvalidError,
    ProjectNotFoundError,
    ProjectReconciliationDriftError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.project.discovery import scan_projects
from aota_forge.core.project.manifest import PROJECT_ID_RE, load_project
from aota_forge.core.project.repository_identity import (
    RepositoryIdentityEvidence,
    verify_checkout_repository_identity,
)
from aota_forge.core.project.resolver import (
    MAX_REGISTRY_BYTES,
    load_workspace_registry,
    resolve_project_candidates,
    resolve_workspace,
    validate_workspace_registry_data,
)

PROJECT_REGISTRY_HAS_LIFECYCLE_OWNER = True
LIFECYCLE_OWNER = "Control Plane / AF project subsystem (mechanical)"
PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATION = True
MODEL_SELF_GRANTS_PROJECT_AUTHORITY = False
MANUAL_REGISTRY_DRIFT_REQUIRED = False
BACKGROUND_DAEMON_REQUIRED = False
CONTINUOUS_FILESYSTEM_WATCHER = False
AUTO_REBIND_BY_NAME = False
AUTO_REBIND_BY_NEAREST_PATH = False
AUTO_REBIND_BY_GIT_REPO_NAME = False
PROJECT_REGISTRY_REIMPLEMENTED = False
REGISTRY_WRITE_ATOMIC = True
REGISTRY_WRITE_SCHEMA_VALIDATED = True
REGISTRY_WRITE_READBACK_VERIFIED = True
REGISTRY_WRITE_TRANSACTION_SERVICE_CREATED = False
# Registration is a bounded Control-Plane/operator operation with
# trusted-input-only semantics.  It is deliberately NOT a Core ingress
# dispatch operation and NOT Agent-visible: the Core write ingress owns typed
# lifecycle-lease mutations (plan_init / plan_retirement) and inventing a
# lease/authority path for project registration is out of scope (no new
# authority engine).  ``project.reconcile`` (read) IS the canonical
# dispatchable Core operation.
PROJECT_REGISTRATION_OPERATION = "project.register"
PROJECT_REGISTRATION_OPERATION_KIND = "control_plane_operator_operation"
PROJECT_REGISTRATION_IS_CORE_INGRESS_OPERATION = False
PROJECT_REGISTRATION_IS_AGENT_VISIBLE_OPERATION = False
PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATOR_CHANNEL = True
PROJECT_RECONCILE_OPERATION = "project.reconcile"
PROJECT_RECONCILE_IS_CORE_INGRESS_OPERATION = True
PROJECT_RECONCILE_IS_AGENT_VISIBLE_OPERATION = False

MAX_WORKSPACE_ID_LENGTH = 128
MAX_PROJECT_ID_LENGTH = 96
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validate_workspace_id(workspace_id: object) -> str:
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ProjectRegistryInvalidError("workspace_id must be a non-empty string")
    value = workspace_id.strip()
    if len(value) > MAX_WORKSPACE_ID_LENGTH or not _WORKSPACE_ID_RE.fullmatch(value):
        raise ProjectRegistryInvalidError("workspace_id must be a bounded trusted identifier")
    return value


def _validate_project_id(project_id: object) -> str:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProjectNotFoundError("project_id must be a non-empty string")
    value = project_id.strip()
    if len(value) > MAX_PROJECT_ID_LENGTH or not PROJECT_ID_RE.fullmatch(value):
        raise ProjectNotFoundError("project_id must match manifest schema v1 project id")
    return value


def _validate_trusted_workspace_root(workspace_root: object) -> Path:
    """Validate an operator/runtime-supplied workspace authorization root.

    This is a trusted Control-Plane input (CLI/operator/bootstrap), never a
    model-supplied path: exists, real directory, not a symlink, canonical.
    """
    if not isinstance(workspace_root, (str, os.PathLike)):
        raise ProjectRegistryInvalidError("workspace_root must be a filesystem path")
    root = Path(workspace_root)
    try:
        if root.is_symlink():
            raise ProjectRegistryInvalidError(f"workspace_root must not be a symlink: {root}")
        if not root.exists():
            raise ProjectNotFoundError(f"workspace_root does not exist: {root}")
        if not root.is_dir():
            raise ProjectNotFoundError(f"workspace_root is not a directory: {root}")
        return root.resolve(strict=True)
    except ProjectRegistryInvalidError:
        raise
    except ProjectNotFoundError:
        raise
    except OSError as exc:
        raise ProjectRegistryInvalidError(f"workspace_root inaccessible: {exc}") from exc


def _atomic_write_workspace_registry(registry_path: Path, data: dict[str, Any]) -> None:
    """Atomic, schema-validated, read-back-verified registry replacement.

    No transaction service, no partial-write acceptance: a temporary sibling
    file is written, schema-validated, atomically replaced over the registry
    and then read back and compared.  Any failure leaves the previous registry
    bytes untouched (the temp file is removed on failure).
    """
    validate_workspace_registry_data(data)
    if registry_path.is_symlink():
        raise ProjectRegistryInvalidError("workspace registry path must not be a symlink")
    if not registry_path.parent.is_dir():
        raise ProjectRegistryInvalidError("workspace registry parent directory is missing")
    try:
        original_mode = registry_path.stat().st_mode & 0o777
    except OSError:
        original_mode = 0o600
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_REGISTRY_BYTES:
        raise ProjectRegistryInvalidError("workspace registry write would exceed the size bound")
    tmp = registry_path.with_name(f"{registry_path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp, original_mode)
        except OSError:
            pass
        os.replace(tmp, registry_path)
    except OSError as exc:
        raise ProjectRegistryInvalidError(f"workspace registry write failed: {exc}") from exc
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    readback = load_workspace_registry(registry_path)
    if readback != data:
        raise ProjectRegistryInvalidError("workspace registry read-back mismatch after write")


def _ensure_workspace_authorized(
    registry_path: Path,
    registry: dict[str, Any],
    workspace_id: str,
    workspace_root: Path,
) -> bool:
    """Write-through workspace authorization (idempotent, conflict fail-closed).

    * new workspace_id → add entry
    * existing entry already contains the canonical root → no write
    * existing entry has another live (existing, non-symlink) root → fail
      closed; explicit retirement is required (no silent rebind)
    * existing entry has only dead/non-existing candidates → append the root
    """
    new_root = str(workspace_root)
    entry = registry.get(workspace_id)
    if entry is None:
        updated = dict(registry)
        updated[workspace_id] = {"candidates": [new_root]}
        _atomic_write_workspace_registry(registry_path, updated)
        return True
    candidates = [str(c) for c in entry["candidates"]]
    if new_root in candidates:
        return False
    conflicting = []
    for candidate in candidates:
        path = Path(candidate)
        try:
            if path.is_symlink():
                continue
            if path.exists() and path.is_dir():
                conflicting.append(candidate)
        except OSError:
            continue
    if conflicting:
        raise ProjectAmbiguousError(
            f"workspace_id '{workspace_id}' already has a live authorized root "
            f"({conflicting[0]!r}); explicit retirement is required before registering {new_root!r}"
        )
    updated = dict(registry)
    updated[workspace_id] = {"candidates": sorted(set(candidates + [new_root]))}
    _atomic_write_workspace_registry(registry_path, updated)
    return True


def _single_candidate_in_workspace(workspace_root: Path, project_id: str) -> tuple[Path, dict[str, Any]]:
    """Find exactly one project checkout for ``project_id`` in an authorized root."""
    scanned = scan_projects(workspace_root, limit=None)
    matches = [item for item in scanned["projects"] if item["project_id"] == project_id]
    if not matches:
        raise ProjectNotFoundError(f"project not found in authorized workspace: {project_id}")
    if len(matches) > 1:
        raise ProjectAmbiguousError(
            f"project_id '{project_id}' has {len(matches)} manifests in the authorized workspace"
        )
    record = matches[0]
    checkout = workspace_root if record["root"] == "." else workspace_root / record["root"]
    return checkout, record


def _validate_candidate_checkout(checkout_root: Path, workspace_root: Path) -> dict[str, Any]:
    """Mechanical registration validation of the candidate project checkout."""
    try:
        canonical = checkout_root.resolve(strict=True)
    except OSError as exc:
        raise ProjectNotFoundError(f"candidate project root is missing: {exc}") from exc
    try:
        canonical.relative_to(workspace_root)
    except ValueError as exc:
        raise ProjectRegistryInvalidError(
            f"candidate project root {canonical} lies outside authorized workspace {workspace_root}"
        ) from exc
    if checkout_root.is_symlink():
        raise ProjectRegistryInvalidError(f"candidate project root must not be a symlink: {checkout_root}")
    if not canonical.is_dir():
        raise ProjectNotFoundError(f"candidate project root is not a directory: {canonical}")
    manifest = canonical / ".aota" / "project.yaml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ProjectManifestInvalidError("manifest_invalid", ".aota/project.yaml is missing or a symlink")
    root, data = load_project(manifest)
    if root.resolve() != canonical:
        raise ProjectManifestInvalidError("manifest_invalid", "manifest root does not match candidate checkout")
    return data


def _verify_repository_if_declared(
    checkout_root: Path, source_repository: str | None
) -> RepositoryIdentityEvidence | None:
    if source_repository is None or not str(source_repository).strip():
        return None
    return verify_checkout_repository_identity(
        project_root=checkout_root,
        declared_source_repository=str(source_repository).strip(),
        boundary=checkout_root,
    )


@dataclass(frozen=True)
class ProjectRegistrationEvidence:
    """Trusted registration result (mechanical identity only)."""

    workspace_id: str
    workspace_root: str
    project_id: str
    project_root: str
    manifest_path: str
    registry_path: str
    registry_fingerprint: str
    candidate_fingerprint: str
    registry_updated: bool
    source_repository: str = ""
    repository_identity: dict[str, Any] | None = None
    status: str = "REGISTERED"
    lifecycle_owner: str = LIFECYCLE_OWNER

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "manifest_path": self.manifest_path,
            "registry_path": self.registry_path,
            "registry_fingerprint": self.registry_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "registry_updated": self.registry_updated,
            "source_repository": self.source_repository,
            "repository_identity": self.repository_identity,
            "lifecycle_owner": self.lifecycle_owner,
        }


@dataclass(frozen=True)
class ProjectReconciliationEvidence:
    """Mechanical reconciliation result (no automatic rebinding)."""

    workspace_id: str
    workspace_root: str
    project_id: str
    project_root: str
    manifest_path: str
    registry_fingerprint: str
    candidate_fingerprint: str
    source_repository: str = ""
    repository_identity: dict[str, Any] | None = None
    status: str = "RECONCILED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "manifest_path": self.manifest_path,
            "registry_fingerprint": self.registry_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "source_repository": self.source_repository,
            "repository_identity": self.repository_identity,
        }


def register_project(
    *,
    workspace_id: str,
    registry_path: Path | str,
    project_id: str,
    source_repository: str | None = None,
    workspace_root: Path | str | None = None,
) -> ProjectRegistrationEvidence:
    """Register/adopt one project under trusted workspace authority.

    ``workspace_root`` is the trusted operator/runtime authorization input for
    first-time workspace authorization (write-through).  When omitted, the
    workspace must already be operator-authorized in the registry.  The
    candidate project is always discovered by exact ``project_id`` match
    inside the authorized root (never by folder/repo name similarity), and
    the checkout is mechanically validated before any registry write.
    """
    wid = _validate_workspace_id(workspace_id)
    pid = _validate_project_id(project_id)
    registry_path = Path(registry_path)
    registry = load_workspace_registry(registry_path)

    if workspace_root is not None:
        ws_root = _validate_trusted_workspace_root(workspace_root)
    else:
        if wid not in registry:
            raise ProjectNotFoundError(f"workspace_id is not operator-authorized: {wid}")
        ws_root = resolve_workspace(wid, registry_path)

    checkout, _record = _single_candidate_in_workspace(ws_root, pid)
    _validate_candidate_checkout(checkout, ws_root)
    canonical_checkout = checkout.resolve(strict=True)
    repository_identity = _verify_repository_if_declared(canonical_checkout, source_repository)

    registry_updated = False
    if workspace_root is not None:
        registry_updated = _ensure_workspace_authorized(registry_path, registry, wid, ws_root)

    evidence = resolve_project_candidates(wid, registry_path, pid)
    if evidence.status != "RESOLVED" or len(evidence.candidates) != 1:
        raise ProjectRegistryInvalidError(
            f"registration did not produce singular trusted evidence for {pid!r}: {evidence.status}"
        )
    candidate = evidence.candidates[0]
    return ProjectRegistrationEvidence(
        workspace_id=wid,
        workspace_root=str(ws_root),
        project_id=pid,
        project_root=candidate.project_root,
        manifest_path=candidate.manifest_path,
        registry_path=str(registry_path),
        registry_fingerprint=candidate.registry_fingerprint,
        candidate_fingerprint=candidate.candidate_fingerprint,
        registry_updated=registry_updated,
        source_repository=str(source_repository).strip() if source_repository else "",
        repository_identity=repository_identity.to_dict() if repository_identity is not None else None,
    )


def reconcile_project(
    *,
    workspace_id: str,
    registry_path: Path | str,
    project_id: str,
    source_repository: str | None = None,
    expected_project_root: str | None = None,
    expected_manifest_path: str | None = None,
) -> ProjectReconciliationEvidence:
    """On-demand mechanical reconciliation; drift fails closed.

    Verifies: workspace still resolvable, manifest still present and valid,
    project_id still matching, registered path still safe, Git repository
    identity still matching (when declared).  Never rebinds to a similarly
    named folder, nearest path or matching Git repo name.
    """
    wid = _validate_workspace_id(workspace_id)
    pid = _validate_project_id(project_id)
    registry_path = Path(registry_path)

    evidence = resolve_project_candidates(wid, registry_path, pid)
    if evidence.status == "PROJECT_NOT_FOUND":
        raise ProjectNotFoundError(f"registered project not found during reconciliation: {pid}")
    if evidence.status == "NEEDS_SEMANTIC_CHOICE":
        raise ProjectAmbiguousError(
            f"registered project_id '{pid}' is ambiguous during reconciliation: "
            f"{len(evidence.candidates)} manifests"
        )
    candidate = evidence.candidates[0]
    ws_root = Path(evidence.workspace_root)
    checkout = Path(candidate.project_root)
    try:
        canonical_checkout = checkout.resolve(strict=True)
    except OSError as exc:
        raise ProjectNotFoundError(f"registered project root missing: {exc}") from exc
    try:
        canonical_checkout.relative_to(ws_root)
    except ValueError as exc:
        raise ProjectRegistryInvalidError(
            f"registered project root escaped the authorized workspace: {checkout}"
        ) from exc
    if checkout.is_symlink():
        raise ProjectRegistryInvalidError(f"registered project root became a symlink: {checkout}")
    manifest = canonical_checkout / ".aota" / "project.yaml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ProjectManifestInvalidError("manifest_invalid", "registered .aota/project.yaml is missing")
    root, data = load_project(manifest)
    if root.resolve() != canonical_checkout or data["project"]["id"] != pid:
        raise ProjectReconciliationDriftError(
            f"manifest identity changed for registered project {pid!r} at {canonical_checkout}"
        )
    if expected_project_root is not None:
        expected = Path(str(expected_project_root))
        try:
            expected_canonical = expected.resolve(strict=False)
        except OSError:
            expected_canonical = expected
        if str(expected_canonical) != str(canonical_checkout):
            raise ProjectReconciliationDriftError(
                f"registered project root drifted: expected {expected_canonical}, found {canonical_checkout}"
            )
    if expected_manifest_path is not None and str(expected_manifest_path) != candidate.manifest_path:
        raise ProjectReconciliationDriftError(
            f"registered manifest path drifted: expected {expected_manifest_path!r}, "
            f"found {candidate.manifest_path!r}"
        )
    repository_identity = _verify_repository_if_declared(canonical_checkout, source_repository)
    return ProjectReconciliationEvidence(
        workspace_id=wid,
        workspace_root=str(ws_root),
        project_id=pid,
        project_root=str(canonical_checkout),
        manifest_path=candidate.manifest_path,
        registry_fingerprint=candidate.registry_fingerprint,
        candidate_fingerprint=candidate.candidate_fingerprint,
        source_repository=str(source_repository).strip() if source_repository else "",
        repository_identity=repository_identity.to_dict() if repository_identity is not None else None,
    )


def retire_workspace_authorization(
    *,
    workspace_id: str,
    registry_path: Path | str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """Remove one candidate root from a workspace entry (explicit retirement).

    Removes the workspace entry entirely when it becomes empty.  Atomic
    write-through + read-back; fail closed when the workspace or candidate
    root is not authorized.
    """
    wid = _validate_workspace_id(workspace_id)
    registry_path = Path(registry_path)
    registry = load_workspace_registry(registry_path)
    entry = registry.get(wid)
    if entry is None:
        raise ProjectNotFoundError(f"workspace_id is not operator-authorized: {wid}")
    root = Path(workspace_root)
    candidates = [str(c) for c in entry["candidates"]]
    target = str(root)
    if target not in candidates:
        resolved = None
        try:
            resolved = str(root.resolve(strict=False))
        except OSError:
            resolved = None
        matches = [c for c in candidates if c == resolved or str(Path(c).resolve(strict=False)) == resolved]
        if not matches:
            raise ProjectNotFoundError(f"candidate root is not authorized for workspace {wid!r}: {target}")
        target = matches[0]
    updated = dict(registry)
    remaining = [c for c in candidates if c != target]
    if remaining:
        updated[wid] = {"candidates": remaining}
    else:
        updated.pop(wid, None)
    _atomic_write_workspace_registry(registry_path, updated)
    return {
        "status": "RETIRED",
        "workspace_id": wid,
        "removed_candidate": target,
        "workspace_entry_removed": wid not in updated,
        "registry_path": str(registry_path),
    }


__all__ = [
    "register_project",
    "reconcile_project",
    "retire_workspace_authorization",
    "ProjectRegistrationEvidence",
    "ProjectReconciliationEvidence",
    "PROJECT_REGISTRY_HAS_LIFECYCLE_OWNER",
    "LIFECYCLE_OWNER",
    "PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATION",
    "MODEL_SELF_GRANTS_PROJECT_AUTHORITY",
    "MANUAL_REGISTRY_DRIFT_REQUIRED",
    "BACKGROUND_DAEMON_REQUIRED",
    "CONTINUOUS_FILESYSTEM_WATCHER",
    "AUTO_REBIND_BY_NAME",
    "AUTO_REBIND_BY_NEAREST_PATH",
    "AUTO_REBIND_BY_GIT_REPO_NAME",
    "PROJECT_REGISTRY_REIMPLEMENTED",
    "REGISTRY_WRITE_ATOMIC",
    "REGISTRY_WRITE_SCHEMA_VALIDATED",
    "REGISTRY_WRITE_READBACK_VERIFIED",
    "REGISTRY_WRITE_TRANSACTION_SERVICE_CREATED",
    "PROJECT_REGISTRATION_OPERATION",
    "PROJECT_REGISTRATION_OPERATION_KIND",
    "PROJECT_REGISTRATION_IS_CORE_INGRESS_OPERATION",
    "PROJECT_REGISTRATION_IS_AGENT_VISIBLE_OPERATION",
    "PROJECT_REGISTRATION_REQUIRES_TRUSTED_OPERATOR_CHANNEL",
    "PROJECT_RECONCILE_OPERATION",
    "PROJECT_RECONCILE_IS_CORE_INGRESS_OPERATION",
    "PROJECT_RECONCILE_IS_AGENT_VISIBLE_OPERATION",
]
