"""Live GitHub Plan authority read adapter — thin production read seam (M3 RV1 B003).

Implements the existing PlanAuthorityReadAdapter boundary for the current
external authority mechanism (GitHub Issue wzjcccc-dotcom/aota-hermes-tools#37).

Core remains platform-neutral: it never sees GitHub issue numbers, gh CLI,
comment IDs, REST API pagination, or comment role markers. The adapter hides
all of that and returns a typed PlanAuthoritySnapshot (body, revision, digest,
control_projections). Transport is read-only.

This is the thinnest production concrete reader under the existing
aota_forge/adapters/plan_authority/ boundary. No new Plan authority system
is created; the existing boundary is reused.

Required by AOTA Forge Operational Plan #37 M3 RV1 B003 repair:
  EXISTING_PLAN_AUTHORITY_BOUNDARY_REUSED=yes
  NEW_PLAN_AUTHORITY_SYSTEM_CREATED=no
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from aota_forge.adapters.plan_authority import PlanAuthorityReadAdapter, PlanAuthoritySnapshot

# Current governing Plan authority (external). This is adapter-private
# knowledge. Core never imports it.
DEFAULT_GITHUB_REPO = "wzjcccc-dotcom/aota-hermes-tools"
DEFAULT_ISSUE_NUMBER = 37
GH_RTK_BIN = "rtk"
GH_CONFIG_ENV = "GH_CONFIG_DIR"
GH_CONFIG_DEFAULT = "/home/latios/.config/gh"

# Snapshot source for deterministic tests. When set, the adapter reads body
# from this file instead of calling gh. The file contains raw issue body.
SNAPSHOT_PATH_ENV = "AOTA_PLAN_AUTHORITY_SNAPSHOT_PATH"


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _gh_api_ok() -> bool:
    """Verify gh auth via the documented contract: GH_CONFIG_DIR + rtk gh api user."""
    gh_config = os.environ.get(GH_CONFIG_ENV) or GH_CONFIG_DEFAULT
    try:
        result = subprocess.run(
            [GH_RTK_BIN, "gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, GH_CONFIG_ENV: gh_config},
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


class GitHubPlanAuthorityReadAdapter(PlanAuthorityReadAdapter):
    """Thin live reader for the governing GitHub Issue body (read-only).

    Reads the current authoritative Issue body via the operator's gh
    credential (GH_CONFIG_DIR + rtk gh). Never mutates. Body is returned
    as-is; revision/digest are derived from the Issue's updatedAt and body
    sha256. Control projections are fetched as bounded snapshots of the
    managed comments (role markers), but the single Plan authority is always
    the Issue body.

    Fallback: if AOTA_PLAN_AUTHORITY_SNAPSHOT_PATH is set, read body from
    that file (deterministic fixture path for disposable tests).
    """

    def __init__(
        self,
        repo: str = DEFAULT_GITHUB_REPO,
        issue_number: int = DEFAULT_ISSUE_NUMBER,
        *,
        gh_bin: str = GH_RTK_BIN,
        gh_config_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(repo, str) or not repo.strip() or "/" not in repo:
            raise ValueError(f"repo must be 'owner/repo', got {repo!r}")
        if not isinstance(issue_number, int) or issue_number <= 0:
            raise ValueError(f"issue_number must be positive int, got {issue_number!r}")
        self._repo = repo.strip()
        self._issue = int(issue_number)
        self._gh_bin = gh_bin
        self._gh_config_dir = str(gh_config_dir) if gh_config_dir is not None else os.environ.get(GH_CONFIG_ENV, GH_CONFIG_DEFAULT)

    def load(self) -> PlanAuthoritySnapshot:
        # Deterministic fixture path for tests (no gh required)
        snapshot_path = os.environ.get(SNAPSHOT_PATH_ENV, "").strip()
        if snapshot_path:
            p = Path(snapshot_path)
            if not p.is_file():
                raise RuntimeError(f"snapshot file missing: {snapshot_path!r}")
            body = p.read_text(encoding="utf-8")
            if not body.strip():
                raise RuntimeError("snapshot body is empty")
            digest = _digest(body)
            # revision is file mtime as opaque string; not semantic
            try:
                revision = str(int(p.stat().st_mtime))
            except Exception:
                revision = None
            return PlanAuthoritySnapshot(body=body, revision=revision, digest=digest, control_projections={})

        # Live GitHub read via gh CLI
        gh_config = self._gh_config_dir
        env = {**os.environ, GH_CONFIG_ENV: gh_config}
        # Fetch issue body + updatedAt via gh issue view json
        # Use --json body,updatedAt,number to get bounded fields
        try:
            result = subprocess.run(
                [self._gh_bin, "gh", "issue", "view", str(self._issue), "--repo", self._repo, "--json", "body,updatedAt,number"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
        except Exception as exc:
            raise RuntimeError(f"GitHub Plan authority read failed: {type(exc).__name__}: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"GitHub Plan read failed (gh issue view): {result.stderr.strip()[:800] or result.stdout.strip()[:800]}")
        try:
            data = json.loads(result.stdout)
        except Exception as exc:
            raise RuntimeError(f"GitHub Plan body JSON parse failed: {exc}") from exc
        body = data.get("body") or ""
        if not isinstance(body, str) or not body.strip():
            raise RuntimeError("GitHub Plan issue body is empty")
        updated_at = data.get("updatedAt")
        revision = str(updated_at).strip() if updated_at else None
        digest = _digest(body)

        # Control projections: fetch managed comments with role markers
        # Bounded: fetch only first 100 comments' bodies via gh api? Use gh issue view with --json comments
        # For now we do a second call for comments if needed, but keep it bounded.
        control_projections: dict[str, dict[str, str]] = {}
        try:
            c_result = subprocess.run(
                [self._gh_bin, "gh", "issue", "view", str(self._issue), "--repo", self._repo, "--json", "comments"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            if c_result.returncode == 0:
                c_data = json.loads(c_result.stdout)
                comments = c_data.get("comments") or []
                # Extract role markers from comment bodies (bounded, last writer wins)
                # Each comment body may contain COMMENT_ROLE=... and managed content.
                # For the launcher we only need current fields for approval? But we provide projections for completeness.
                # We keep it simple: map role -> body snippet.
                if isinstance(comments, list):
                    for c in comments[:100]:
                        body_text = c.get("body") if isinstance(c, dict) else ""
                        if not isinstance(body_text, str):
                            continue
                        # Detect COMMENT_ROLE marker
                        m = re.search(r"COMMENT_ROLE=([a-z_]+)", body_text)
                        if m:
                            role = m.group(1).strip()
                            # Keep the latest occurrence for each role
                            control_projections[role] = {"body": body_text[:4096], "id": str(c.get("id", ""))[:64]}
        except Exception:
            # Control projections are best-effort; body remains authoritative
            pass

        return PlanAuthoritySnapshot(body=body, revision=revision, digest=digest, control_projections=control_projections)


__all__ = ["GitHubPlanAuthorityReadAdapter", "DEFAULT_GITHUB_REPO", "DEFAULT_ISSUE_NUMBER"]
