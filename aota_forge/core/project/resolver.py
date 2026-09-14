"""Executor-neutral workspace / project resolver (M1-B).

Deterministic canonical rule:

    0 candidates → PROJECT_NOT_FOUND / unresolved
    1 candidate  → resolved
    >1 candidates → PROJECT_AMBIGUOUS, bounded candidates returned

No newest/first/closest-folder/title heuristics.  No project hint authority.
Workspace resolution uses the registered workspaces.json registry only; the
registry path is injected (no hard-coded host path in Core).
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.project.discovery import MAX_RESULTS, fingerprint_registry, scan_projects

MAX_REGISTRY_BYTES = 512 * 1024


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


@dataclass(frozen=True)
class ProjectCandidateEvidence:
    """One deterministic relationship candidate, never a semantic binding."""

    workspace_id: str
    workspace_root: str
    project_id: str
    project_root: str
    manifest_path: str
    name: str
    kind: str
    status: str
    registry_fingerprint: str
    candidate_fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "manifest_path": self.manifest_path,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "registry_fingerprint": self.registry_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
        }


@dataclass(frozen=True)
class ProjectResolutionEvidence:
    """Complete deterministic candidate evidence for Project Binding choice."""

    status: str
    workspace_id: str
    workspace_root: str
    registry_fingerprint: str
    listing_fingerprint: str
    candidates: tuple[ProjectCandidateEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "registry_fingerprint": self.registry_fingerprint,
            "listing_fingerprint": self.listing_fingerprint,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def resolve_project_candidates(
    workspace_id: str,
    registry_path: Path,
    project_id: str,
) -> ProjectResolutionEvidence:
    """Return complete relationship evidence without selecting a project."""
    root = resolve_workspace(workspace_id, registry_path)
    complete = scan_projects(root, limit=None)
    registry_fingerprint = fingerprint_registry(workspace_id, complete["projects"], complete["invalid"])
    listed = complete["projects"][:MAX_RESULTS]
    listing_fingerprint = fingerprint_registry(
        workspace_id,
        listed,
        complete["invalid"][:MAX_RESULTS],
    )
    matches = [item for item in complete["projects"] if item["project_id"] == project_id]
    candidates = tuple(
        ProjectCandidateEvidence(
            workspace_id=workspace_id,
            workspace_root=str(root),
            project_id=record["project_id"],
            project_root=str(root / record["root"]) if record["root"] != "." else str(root),
            manifest_path=record["manifest_path"],
            name=record["name"],
            kind=record["kind"],
            status=record["status"],
            registry_fingerprint=registry_fingerprint,
            candidate_fingerprint=_candidate_fingerprint(record),
        )
        for record in matches
    )
    status = (
        "PROJECT_NOT_FOUND"
        if not candidates
        else "RESOLVED"
        if len(candidates) == 1
        else "NEEDS_SEMANTIC_CHOICE"
    )
    return ProjectResolutionEvidence(
        status=status,
        workspace_id=workspace_id,
        workspace_root=str(root),
        registry_fingerprint=registry_fingerprint,
        listing_fingerprint=listing_fingerprint,
        candidates=candidates,
    )


def validate_workspace_registry_data(data: object) -> dict[str, Any]:
    """Validate the workspace registry schema (shared by load and write-through).

    Schema: {workspace_id: {"candidates": [non-empty path string, ...]}}.
    Fail closed; never repairs or normalizes invalid data silently.
    """
    if not isinstance(data, dict):
        raise ProjectRegistryInvalidError("workspace registry must be an object")
    for workspace_id, entry in data.items():
        if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 128:
            raise ProjectRegistryInvalidError("workspace registry contains invalid workspace_id")
        candidates = entry.get("candidates") if isinstance(entry, dict) else None
        if not candidates or not isinstance(candidates, list) or not all(isinstance(c, str) and c for c in candidates):
            raise ProjectRegistryInvalidError(f"workspace registry entry invalid: {workspace_id}")
    return data


def _combined_registry_fingerprint(per_workspace: list[tuple[str, str]]) -> str:
    source = {
        "workspaces": [
            {"workspace_id": workspace_id, "fingerprint": fingerprint}
            for workspace_id, fingerprint in sorted(per_workspace)
        ]
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_project_across_workspaces(
    registry_path: Path,
    project_id: str,
) -> ProjectResolutionEvidence:
    """Resolve ``project_id`` across ALL operator-authorized workspaces.

    Deterministic and fail-closed over the complete authorized workspace set:

    * every registered workspace root is resolved with the existing
      ``resolve_workspace`` mechanics (a workspace entry whose root cannot
      resolve fails the whole resolution closed — no silent skip);
    * every workspace is scanned completely (``scan_projects(limit=None)``);
    * matches for ``project_id`` across workspaces are gathered in
      deterministic order (workspace_id, project_root);
    * 0 → PROJECT_NOT_FOUND, 1 → RESOLVED, >1 → NEEDS_SEMANTIC_CHOICE.

    This is the canonical Plan-driven entry point: a Plan declares
    ``PROJECT_ID`` (and optionally ``SOURCE_REPOSITORY``); it never declares a
    filesystem path, and this resolver never consults cwd or workspace/folder
    name similarity.
    """
    if not isinstance(project_id, str) or not project_id or len(project_id) > 96:
        raise ProjectNotFoundError("project_id is required")
    registry = load_workspace_registry(registry_path)
    per_workspace: list[tuple[str, str]] = []
    listing_per_workspace: list[tuple[str, str]] = []
    combined: list[tuple[str, Path, dict[str, Any]]] = []
    for workspace_id in sorted(registry):
        root = resolve_workspace(workspace_id, registry_path)
        complete = scan_projects(root, limit=None)
        per_workspace.append(
            (workspace_id, fingerprint_registry(workspace_id, complete["projects"], complete["invalid"]))
        )
        listing_per_workspace.append(
            (
                workspace_id,
                fingerprint_registry(
                    workspace_id,
                    complete["projects"][:MAX_RESULTS],
                    complete["invalid"][:MAX_RESULTS],
                ),
            )
        )
        for record in complete["projects"]:
            if record["project_id"] == project_id:
                combined.append((workspace_id, root, record))
    combined.sort(key=lambda item: (item[0], str(item[1] / item[2]["root"])))
    candidates = tuple(
        ProjectCandidateEvidence(
            workspace_id=workspace_id,
            workspace_root=str(root),
            project_id=record["project_id"],
            project_root=str(root / record["root"]) if record["root"] != "." else str(root),
            manifest_path=record["manifest_path"],
            name=record["name"],
            kind=record["kind"],
            status=record["status"],
            registry_fingerprint=dict(per_workspace).get(workspace_id, ""),
            candidate_fingerprint=_candidate_fingerprint(record),
        )
        for workspace_id, root, record in combined
    )
    status = (
        "PROJECT_NOT_FOUND"
        if not candidates
        else "RESOLVED"
        if len(candidates) == 1
        else "NEEDS_SEMANTIC_CHOICE"
    )
    resolved = candidates[0] if status == "RESOLVED" else None
    return ProjectResolutionEvidence(
        status=status,
        workspace_id=resolved.workspace_id if resolved is not None else "",
        workspace_root=resolved.workspace_root if resolved is not None else "",
        registry_fingerprint=_combined_registry_fingerprint(per_workspace),
        listing_fingerprint=_combined_registry_fingerprint(listing_per_workspace),
        candidates=candidates,
    )


def load_workspace_registry(registry_path: Path) -> dict[str, Any]:
    """Load the workspace registry with bounded parsing.

    Returns {workspace_id: {"candidates": [absolute path, ...]}}.
    Fail closed on missing/malformed registry.
    """
    try:
        if registry_path.is_symlink() or not registry_path.is_file() or registry_path.stat().st_size > MAX_REGISTRY_BYTES:
            raise ProjectRegistryInvalidError("workspace registry is unsafe or too large")
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except ProjectRegistryInvalidError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectRegistryInvalidError(f"workspace registry unreadable: {type(exc).__name__}") from exc
    return validate_workspace_registry_data(data)


def resolve_workspace(workspace_id: str, registry_path: Path) -> Path:
    """Resolve a workspace_id to exactly one existing directory root."""
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ProjectNotFoundError("workspace_id is required")
    registry = load_workspace_registry(registry_path)
    entry = registry.get(workspace_id)
    if entry is None:
        raise ProjectNotFoundError(f"unknown workspace_id: {workspace_id}")
    candidates = entry.get("candidates") if isinstance(entry, dict) else []
    resolved: list[Path] = []
    for c in candidates:
        p = Path(c)
        if p.is_symlink():
            raise ProjectRegistryInvalidError(f"workspace_id '{workspace_id}': candidate root must not be a symlink")
        if p.exists() and p.is_dir():
            resolved.append(p.resolve())
    unique = sorted({str(p) for p in resolved})
    if len(unique) == 0:
        raise ProjectNotFoundError(f"workspace_id '{workspace_id}': no candidate path exists")
    if len(unique) > 1:
        raise ProjectAmbiguousError(
            f"workspace_id '{workspace_id}': multiple distinct candidate paths exist: {unique}"
        )
    return Path(unique[0])


def resolve_project(root: Path, project_id: str) -> dict[str, Any]:
    """Resolve a project_id within a workspace to exactly one manifest record.

    Resolution uses the COMPLETE semantic candidate set
    (``scan_projects(root, limit=None)``): a bounded display limit can
    never turn a project that exists beyond the listing boundary into a
    false PROJECT_NOT_FOUND, and a duplicate target beyond the listing
    boundary is never hidden from ambiguity detection.

    Duplicate project IDs are treated as ambiguity (fail closed, no
    automatic choice).
    """
    if not isinstance(project_id, str) or not project_id or len(project_id) > 96:
        raise ProjectNotFoundError("project_id is required")
    scanned = scan_projects(root, limit=None)
    matches = [item for item in scanned["projects"] if item["project_id"] == project_id]
    if len(matches) == 0:
        raise ProjectNotFoundError(f"project not found: {project_id}")
    if len(matches) > 1:
        raise ProjectAmbiguousError(
            f"project_id '{project_id}' is ambiguous: {len(matches)} manifest records",
        )
    return matches[0]


def resolve_project_with_fingerprint(
    workspace_id: str, registry_path: Path, project_id: str
) -> dict[str, Any]:
    """Resolve workspace then project, returning canonical machine result.

    Fingerprints are explicitly split:

    * ``registry_fingerprint`` — semantic-resolution fingerprint over the
      COMPLETE project set (resolution/context evidence; complete by
      construction).
    * ``listing_fingerprint`` — bounded display fingerprint over the
      first-50 listing only (never presented as complete evidence;
      ``listing_truncated`` flags truncation).
    """
    root = resolve_workspace(workspace_id, registry_path)
    complete = scan_projects(root, limit=None)
    matches = [item for item in complete["projects"] if item["project_id"] == project_id]
    if len(matches) == 0:
        raise ProjectNotFoundError(f"project not found: {project_id}")
    if len(matches) > 1:
        raise ProjectAmbiguousError(
            f"project_id '{project_id}' is ambiguous: {len(matches)} manifest records",
        )
    record = matches[0]
    listed = complete["projects"][:MAX_RESULTS]
    listing_truncated = len(complete["projects"]) > MAX_RESULTS
    return {
        "workspace_id": workspace_id,
        "workspace_root": str(root),
        "project_id": record["project_id"],
        "project_root": str(root / record["root"]) if record["root"] != "." else str(root),
        "manifest_path": record["manifest_path"],
        "name": record["name"],
        "kind": record["kind"],
        "status": record["status"],
        "registry_fingerprint": fingerprint_registry(
            workspace_id, complete["projects"], complete["invalid"]
        ),
        "listing_fingerprint": fingerprint_registry(
            workspace_id, listed, complete["invalid"][:MAX_RESULTS]
        ),
        "listing_truncated": listing_truncated,
        "project_count": complete["project_count"],
        "listed_project_count": len(listed),
    }
