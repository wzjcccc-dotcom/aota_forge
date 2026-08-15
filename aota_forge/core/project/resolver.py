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
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.project.discovery import fingerprint_registry, scan_projects

MAX_REGISTRY_BYTES = 512 * 1024


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
    if not isinstance(data, dict):
        raise ProjectRegistryInvalidError("workspace registry must be an object")
    for workspace_id, entry in data.items():
        if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 128:
            raise ProjectRegistryInvalidError("workspace registry contains invalid workspace_id")
        candidates = entry.get("candidates") if isinstance(entry, dict) else None
        if not candidates or not isinstance(candidates, list) or not all(isinstance(c, str) and c for c in candidates):
            raise ProjectRegistryInvalidError(f"workspace registry entry invalid: {workspace_id}")
    return data


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

    Duplicate project IDs are treated as ambiguity (fail closed, no
    automatic choice).
    """
    if not isinstance(project_id, str) or not project_id or len(project_id) > 96:
        raise ProjectNotFoundError("project_id is required")
    scanned = scan_projects(root)
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
    """Resolve workspace then project, returning canonical machine result."""
    root = resolve_workspace(workspace_id, registry_path)
    record = resolve_project(root, project_id)
    scanned = scan_projects(root)
    fingerprint = fingerprint_registry(
        workspace_id, scanned["projects"], scanned["invalid"]
    )
    return {
        "workspace_id": workspace_id,
        "workspace_root": str(root),
        "project_id": record["project_id"],
        "project_root": str(root / record["root"]) if record["root"] != "." else str(root),
        "manifest_path": record["manifest_path"],
        "name": record["name"],
        "kind": record["kind"],
        "status": record["status"],
        "registry_fingerprint": fingerprint,
        "project_count": scanned["project_count"],
    }
