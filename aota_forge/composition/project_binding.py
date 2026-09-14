"""Generic trusted project binding via canonical resolver (M1/W1).

Thin shared production helper that derives ProjectResolutionEvidence from
trusted workspace root / registry + canonical .aota/project.yaml discovery +
exact trusted project_id.

No model-facing arguments, no cwd guessing, no hard-coded project root,
no project_id if/elif table, no synthetic fingerprints.

Responsibility is strictly:
    trusted inputs (workspace root / registry + project_id)
    → canonical project resolution evidence (via existing resolver mechanics)

Reuses canonical discovery/resolver:
    scan_projects
    fingerprint_registry
    ProjectCandidateEvidence / ProjectResolutionEvidence
    resolve_project_candidates (when registry is available)

Does not re-implement a second scanner, does not become orchestration
subsystem, does not decide handoff semantics.

Invariants
----------
* PROJECT_EVIDENCE_DERIVED_FROM_CANONICAL_PROJECT_RESOLUTION=yes
* PROJECT_ID_SPECIAL_CASE_ALLOWED=no
* DOGFOOD_PROJECT_LITERAL_IN_PRODUCTION_PATH_ALLOWED=no
* M3_FIXTURE_IS_PRODUCTION_AUTHORITY=no
* PROJECT_RESOLUTION_HEURISTIC_FALLBACK=no
* MODEL_SUPPLIED_PROJECT_AUTHORITY=no
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectBindingMissingError,
    ProjectNotFoundError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.project.discovery import fingerprint_registry, scan_projects
from aota_forge.core.project.repository_identity import verify_checkout_repository_identity
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
    resolve_project_across_workspaces,
    resolve_project_candidates,
)

MAX_WORKSPACE_ID_LENGTH = 128
MAX_PROJECT_ID_LENGTH = 96


def _candidate_fingerprint(record: dict[str, Any]) -> str:
    payload = {
        "project_id": record["project_id"],
        "name": record["name"],
        "kind": record["kind"],
        "status": record["status"],
        "root": record["root"],
        "manifest_path": record["manifest_path"],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_workspace_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise ValueError(f"workspace_root must be Path, got {type(root).__name__}")
    # Reject symlink at root itself
    try:
        if root.is_symlink():
            raise ProjectRegistryInvalidError(f"workspace_root must not be a symlink: {root}")
    except OSError as exc:
        raise ProjectRegistryInvalidError(f"workspace_root inaccessible: {exc}") from exc
    if not root.exists():
        raise ProjectNotFoundError(f"workspace_root does not exist: {root}")
    if not root.is_dir():
        raise ProjectNotFoundError(f"workspace_root is not a directory: {root}")
    # Canonical resolve for determinism
    try:
        canonical = root.resolve(strict=True)
    except OSError as exc:
        raise ProjectRegistryInvalidError(f"workspace_root resolve failed: {exc}") from exc
    return canonical


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or type(project_id) is not str:
        raise ProjectNotFoundError(f"project_id must be str, got {type(project_id).__name__}")
    v = project_id.strip()
    if not v:
        raise ProjectNotFoundError("project_id must be non-empty")
    if len(v) > MAX_PROJECT_ID_LENGTH:
        raise ProjectNotFoundError(f"project_id exceeds {MAX_PROJECT_ID_LENGTH}")
    return v


def derive_canonical_project_evidence(
    *,
    workspace_root: Path,
    project_id: str,
    workspace_id: str | None = None,
) -> ProjectResolutionEvidence:
    """Derive ProjectResolutionEvidence via canonical scan (no registry).

    Trusted inputs: workspace_root (trusted filesystem root) + project_id
    (trusted bootstrap). No model-supplied authority, no hard-coded root,
    no project_id branching.

    Uses scan_projects (existing canonical discovery) and
    fingerprint_registry (existing canonical fingerprint) to produce a
    deterministic ProjectResolutionEvidence.

    Fail-closed:
        0 candidates → PROJECT_NOT_FOUND
        1 candidate  → RESOLVED
        >1 candidates → NEEDS_SEMANTIC_CHOICE (ambiguous)

    No fallback to first/nearest/newest; no synthetic fingerprints.
    """
    ws_root = _validate_workspace_root(workspace_root)
    pid = _validate_project_id(project_id)

    # Workspace id: derived from workspace_root when not supplied, bounded
    if workspace_id is None:
        # Use workspace directory name as workspace_id when possible, else fixed
        candidate = ws_root.name.strip()
        if candidate and len(candidate) <= MAX_WORKSPACE_ID_LENGTH and candidate.replace("_", "").replace("-", "").isalnum():
            wid = candidate
        else:
            wid = "workspace"
    else:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ProjectRegistryInvalidError("workspace_id must be non-empty str")
        wid = workspace_id.strip()
        if len(wid) > MAX_WORKSPACE_ID_LENGTH:
            raise ProjectRegistryInvalidError(f"workspace_id exceeds {MAX_WORKSPACE_ID_LENGTH}")
        if "/" in wid or "\\" in wid:
            raise ProjectRegistryInvalidError("workspace_id must not contain path separators")

    scanned = scan_projects(ws_root, limit=None)
    registry_fingerprint = fingerprint_registry(wid, scanned["projects"], scanned["invalid"])
    listed = scanned["projects"][:50]
    listing_fingerprint = fingerprint_registry(wid, listed, scanned["invalid"][:50])

    matches = [item for item in scanned["projects"] if item["project_id"] == pid]
    candidates = tuple(
        ProjectCandidateEvidence(
            workspace_id=wid,
            workspace_root=str(ws_root),
            project_id=record["project_id"],
            project_root=str(ws_root / record["root"]) if record["root"] != "." else str(ws_root),
            manifest_path=record["manifest_path"],
            name=record["name"],
            kind=record["kind"],
            status=record["status"],
            registry_fingerprint=registry_fingerprint,
            candidate_fingerprint=_candidate_fingerprint(record),
        )
        for record in matches
    )

    # Also consider invalid manifests that might correspond to project_id?
    # scan_projects records invalid as {manifest_path, error_code} without project_id,
    # so we cannot map invalid to project_id directly. Fail-closed for invalid
    # is covered by the fact that a project with an invalid manifest will not
    # appear in scanned["projects"], hence PROJECT_NOT_FOUND, which is fail-closed.
    # If the workspace contains a manifest that is syntactically invalid, it is
    # recorded in scanned["invalid"] and contributes to fingerprint, but does not
    # create a candidate.

    status = (
        "PROJECT_NOT_FOUND"
        if not candidates
        else "RESOLVED"
        if len(candidates) == 1
        else "NEEDS_SEMANTIC_CHOICE"
    )
    return ProjectResolutionEvidence(
        status=status,
        workspace_id=wid,
        workspace_root=str(ws_root),
        registry_fingerprint=registry_fingerprint,
        listing_fingerprint=listing_fingerprint,
        candidates=candidates,
    )


def derive_project_evidence_via_registry(
    *,
    workspace_id: str,
    registry_path: Path,
    project_id: str,
) -> ProjectResolutionEvidence:
    """Derive evidence via existing resolve_project_candidates (registry path).

    This is the canonical registry-backed path and reuses
    resolve_project_candidates directly without re-implementation.
    Fail-closed on missing/ambiguous/symlink etc. is handled by that function
    (which raises on workspace mismatch). For evidence return path, we call
    it and return its evidence; it already produces RESOLVED / PROJECT_NOT_FOUND
    / NEEDS_SEMANTIC_CHOICE statuses without picking a heuristic.
    """
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ProjectRegistryInvalidError("workspace_id must be non-empty str")
    if not isinstance(registry_path, Path):
        registry_path = Path(registry_path)
    _validate_project_id(project_id)
    # This reuses canonical resolver; no synthetic logic
    return resolve_project_candidates(workspace_id, registry_path, project_id)


@dataclass(frozen=True)
class TrustedProjectBindingEvidence:
    """Trusted Project Binding: singular project resolution + repository identity.

    The resolution evidence is the existing canonical
    ``ProjectResolutionEvidence`` (complete fingerprints, no heuristic
    selection).  ``repository_identity`` is present only when a
    ``SOURCE_REPOSITORY`` was declared and mechanically grounded against the
    checkout Git origin (AF #55 M1/W3/W4).
    """

    resolution: ProjectResolutionEvidence
    project_id: str
    source_repository: str = ""
    repository_identity: dict[str, Any] | None = None
    status: str = "RESOLVED"

    @property
    def candidate(self) -> ProjectCandidateEvidence | None:
        if len(self.resolution.candidates) != 1:
            return None
        return self.resolution.candidates[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "source_repository": self.source_repository,
            "repository_identity": self.repository_identity,
            "resolution": self.resolution.to_dict(),
        }


def resolve_trusted_project_binding(
    *,
    project_id: str,
    source_repository: str | None = None,
    registry_path: Path | str | None = None,
    workspace_id: str | None = None,
    workspace_root: Path | str | None = None,
) -> TrustedProjectBindingEvidence:
    """Canonical trusted project binding for production composition.

    Chain (AF #55 M1/W4):

        trusted Plan context (PROJECT_ID, SOURCE_REPOSITORY)
            → trusted workspace registry (or a trusted workspace root)
            → .aota/project.yaml discovery
            → exact project_id resolution (0/1/>1 fail-closed)
            → Git origin verification against SOURCE_REPOSITORY
            → Trusted Project Binding evidence

    Fail-closed typed outcomes:
        * no match                → ProjectNotFoundError
        * >1 match                → ProjectAmbiguousError (no auto-selection)
        * no registry/root input  → ProjectRegistryInvalidError
        * wrong Git origin        → ProjectSourceRepositoryMismatchError
        * non-singular evidence   → ProjectBindingMissingError

    Never consults cwd, never infers from folder/repo name similarity, never
    accepts a model-supplied root as authority.
    """
    pid = _validate_project_id(project_id)
    src = str(source_repository).strip() if source_repository else ""

    if registry_path is not None:
        registry = Path(registry_path)
        if workspace_id is not None:
            evidence = derive_project_evidence_via_registry(
                workspace_id=workspace_id,
                registry_path=registry,
                project_id=pid,
            )
        else:
            evidence = resolve_project_across_workspaces(registry, pid)
    elif workspace_root is not None:
        evidence = derive_canonical_project_evidence(
            workspace_root=Path(workspace_root),
            project_id=pid,
            workspace_id=workspace_id,
        )
    else:
        raise ProjectRegistryInvalidError(
            "trusted project binding requires a trusted registry_path or workspace_root"
        )

    if evidence.status == "PROJECT_NOT_FOUND":
        raise ProjectNotFoundError(f"trusted project binding found no project: {pid}")
    if evidence.status == "NEEDS_SEMANTIC_CHOICE":
        raise ProjectAmbiguousError(
            f"trusted project binding is ambiguous for {pid!r}: {len(evidence.candidates)} candidates"
        )
    if evidence.status != "RESOLVED" or len(evidence.candidates) != 1:
        raise ProjectBindingMissingError(
            f"trusted project binding requires singular RESOLVED evidence for {pid!r} "
            f"(status={evidence.status!r}, candidates={len(evidence.candidates)})"
        )
    candidate = evidence.candidates[0]
    identity = None
    if src:
        identity = verify_checkout_repository_identity(
            project_root=Path(candidate.project_root),
            declared_source_repository=src,
            boundary=Path(candidate.project_root),
        )
    return TrustedProjectBindingEvidence(
        resolution=evidence,
        project_id=pid,
        source_repository=src,
        repository_identity=identity.to_dict() if identity is not None else None,
    )


def resolve_trusted_project_evidence(
    *,
    worktree_root: Path,
    project_id: str,
    workspace_root: Path | None = None,
    workspace_id: str | None = None,
    registry_path: Path | None = None,
) -> ProjectResolutionEvidence:
    """Unified trusted entry point used by composition seams.

    Preferred: when registry_path + workspace_id are available (trusted
    bootstrap provides them), use registry-backed resolver.

    Otherwise, use workspace_root scan path. workspace_root defaults to
    worktree_root itself when it directly contains the project manifest
    (the common production case where worktree_root == project_root), else
    the caller may supply an explicit trusted workspace_root (e.g., the
    parent workspace that contains multiple projects).

    No project_id branching, no M3 fixture fallback, no dogfood literal.

    The caller must supply worktree_root and project_id from trusted
    bootstrap (operator-controlled file, never model argument). This helper
    never consults cwd or model-supplied path.

    Fail-closed: if evidence is not RESOLVED, the caller (e.g.,
    WorktreeSandboxBoundary) will fail closed; this helper does not pick a
    candidate.
    """
    pid = _validate_project_id(project_id)
    w_root = Path(worktree_root)
    # Validate worktree_root is trusted physical checkout (exists, not symlink, dir)
    try:
        if w_root.is_symlink():
            raise ProjectRegistryInvalidError(f"worktree_root must not be symlink: {w_root}")
    except OSError as exc:
        raise ProjectRegistryInvalidError(f"worktree_root inaccessible: {exc}") from exc
    if not w_root.exists() or not w_root.is_dir():
        raise ProjectNotFoundError(f"worktree_root does not exist or not dir: {w_root}")

    # Registry path takes precedence when both workspace_id and registry are supplied
    if registry_path is not None and workspace_id is not None:
        return derive_project_evidence_via_registry(
            workspace_id=workspace_id,
            registry_path=Path(registry_path),
            project_id=pid,
        )
    if registry_path is not None or workspace_id is not None:
        # Partial registry inputs are ambiguous → fail closed
        raise ProjectRegistryInvalidError("both workspace_id and registry_path must be supplied together for registry path")

    # Scan path: determine effective workspace_root
    effective_ws = Path(workspace_root) if workspace_root is not None else w_root
    # If effective_ws is worktree itself and it contains a project at ".",
    # scan will find it. Otherwise, if worktree is a child of workspace and
    # workspace contains project, scanning worktree alone would not find a
    # sibling project; but for production where worktree == project root,
    # scanning worktree is correct. For a multi-project workspace where
    # worktree is the project dir under workspace, we could also scan the
    # parent workspace. To keep deterministic and avoid heuristic fallback,
    # we require the caller to supply workspace_root when worktree is not the
    # workspace. If not supplied, we use worktree_root itself. If that yields
    # PROJECT_NOT_FOUND but the parent workspace would have found it, the
    # evidence will be PROJECT_NOT_FOUND and the sandbox will fail closed,
    # which is the correct generic behavior (no silent parent guess). The
    # caller can explicitly pass workspace_root = worktree_root.parent when
    # that is the intended trusted workspace.
    return derive_canonical_project_evidence(
        workspace_root=effective_ws,
        project_id=pid,
        workspace_id=workspace_id,
    )


# Marker for tests / downstream seam checks
PROJECT_EVIDENCE_DERIVED_FROM_CANONICAL_PROJECT_RESOLUTION = True
PROJECT_ID_SPECIAL_CASE_ALLOWED = False
DOGFOOD_PROJECT_LITERAL_IN_PRODUCTION_PATH_ALLOWED = False
M3_FIXTURE_IS_PRODUCTION_AUTHORITY = False
PROJECT_RESOLUTION_HEURISTIC_FALLBACK = False
# AF #55 M1/W4: production binding grounds repository identity mechanically
# through the same canonical resolver path; registry search is deterministic
# across operator-authorized workspaces.
SOURCE_REPOSITORY_VALIDATED_MECHANICALLY = True
GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE = True
FOLDER_NAME_SIMILARITY_IS_IDENTITY = False
CWD_IS_PROJECT_AUTHORITY = False
MODEL_GUESSES_PROJECT_ROOT = False

__all__ = [
    "derive_canonical_project_evidence",
    "derive_project_evidence_via_registry",
    "resolve_trusted_project_binding",
    "resolve_trusted_project_evidence",
    "TrustedProjectBindingEvidence",
    "PROJECT_EVIDENCE_DERIVED_FROM_CANONICAL_PROJECT_RESOLUTION",
    "PROJECT_ID_SPECIAL_CASE_ALLOWED",
    "DOGFOOD_PROJECT_LITERAL_IN_PRODUCTION_PATH_ALLOWED",
    "M3_FIXTURE_IS_PRODUCTION_AUTHORITY",
    "PROJECT_RESOLUTION_HEURISTIC_FALLBACK",
    "SOURCE_REPOSITORY_VALIDATED_MECHANICALLY",
    "GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE",
    "FOLDER_NAME_SIMILARITY_IS_IDENTITY",
    "CWD_IS_PROJECT_AUTHORITY",
    "MODEL_GUESSES_PROJECT_ROOT",
]
