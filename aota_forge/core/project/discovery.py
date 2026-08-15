"""Deterministic project/workspace discovery (M1-B).

EXTRACT of the read-only scan mechanics from legacy ``_project_registry.py`` /
``_project_discovery.py``: bounded walk, symlink rejection, skip dirs,
scan-excluded projection roots, duplicate project-ID detection, deterministic
sort, registry fingerprinting.

No writes.  No candidate ranking heuristics.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import ProjectManifestInvalidError
from aota_forge.core.project.manifest import load_project

MAX_RESULTS = 50
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".codegraph", ".pytest_cache", ".deploy-receipts", ".deploy-backups", "fixtures", "runtime", "deployment"}
PROJECT_SCAN_EXCLUDED_RELATIVE_ROOTS = (Path("deploy/runtime-projects"), Path(".aota-worktrees"))


def is_project_scan_excluded(workspace_root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(workspace_root)
    except ValueError:
        return True
    return any(
        relative == excluded_root or excluded_root in relative.parents
        for excluded_root in PROJECT_SCAN_EXCLUDED_RELATIVE_ROOTS
    )


def scan_projects(root: Path, limit: int = MAX_RESULTS) -> dict[str, Any]:
    """Scan workspace root for valid .aota/project.yaml manifests (read-only)."""
    if limit < 1 or limit > MAX_RESULTS:
        limit = MAX_RESULTS
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not (current / d).is_symlink()
        )
        if current.name != ".aota" or "project.yaml" not in filenames:
            continue
        manifest = current / "project.yaml"
        if manifest.is_symlink() or is_project_scan_excluded(root, manifest):
            continue
        try:
            project_root, data = load_project(manifest)
            project_id = data["project"]["id"]
            relative = "." if project_root == root else project_root.relative_to(root).as_posix()
            valid.append({
                "project_id": project_id,
                "name": data["project"]["name"],
                "kind": data["project"]["kind"],
                "status": data["project"]["status"],
                "summary": data["summary"][:300],
                "capabilities": list(data["capabilities"]),
                "root": relative,
                "manifest_path": manifest.relative_to(root).as_posix(),
            })
        except (ProjectManifestInvalidError, OSError) as exc:
            relative = "." if current == root else current.relative_to(root).as_posix()
            invalid.append({"manifest_path": relative, "error_code": getattr(exc, "code", "manifest_invalid")})
    valid.sort(key=lambda item: item["project_id"])
    invalid.sort(key=lambda item: (item["manifest_path"], item["error_code"]))
    return {
        "projects": valid[:limit],
        "project_count": len(valid),
        "invalid": invalid[:limit],
        "truncated": len(valid) > limit,
    }


def fingerprint_registry(workspace_id: str, records: list[dict[str, Any]], invalid: list[dict[str, str]]) -> str:
    """Deterministic registry fingerprint over bounded manifest identity."""
    source = {
        "workspace_id": workspace_id,
        "manifests": [
            {"project_id": item["project_id"], "manifest_path": item["manifest_path"], "root": item["root"]}
            for item in sorted(records, key=lambda r: (r["project_id"], r["manifest_path"]))
        ],
        "invalid_projects": invalid,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
