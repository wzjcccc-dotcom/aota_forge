"""Bounded, read-only Git inspection (M1-D).

Invariant: GIT_SEARCH_BOUNDARY = resolved project root.  Git discovery never
walks above the resolved project boundary (legacy ``_repo_status.py`` walked
toward filesystem root; that behavior is NOT carried over).

Mechanics: structured argv, shell=False, fixed commands, timeout, no
model-supplied Git arguments.  Machine result returns the FULL HEAD SHA;
short SHA is human display metadata only.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from aota_forge.core.contracts.errors import GitBoundaryViolationError, GitNotFoundError

GIT_TIMEOUT = 10
HARD_MAX_ENTRIES = 500

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "HOME": os.environ.get("HOME", "/tmp"),
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}


def _run_git(cwd: Path, args: list[str], timeout: int = GIT_TIMEOUT) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(_GIT_ENV),
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", "git command timed out", -1
    except Exception as exc:  # bounded guard
        return "", str(exc), -1


def find_git_root(start: Path, boundary: Path) -> Path:
    """Find the .git root by walking up from start, never above boundary.

    Raises:
        GitNotFoundError: no .git found inside the boundary.
        GitBoundaryViolationError: start lies outside the resolved boundary.
    """
    boundary_resolved = boundary.resolve()
    start_resolved = start.resolve()
    try:
        start_resolved.relative_to(boundary_resolved)
    except ValueError:
        raise GitBoundaryViolationError(
            f"inspection start {start_resolved} is outside project boundary {boundary_resolved}"
        )
    p = start_resolved
    while True:
        if (p / ".git").exists():
            return p
        if p == boundary_resolved or p.parent == p:
            break
        p = p.parent
    raise GitNotFoundError(
        f"no git repository found within boundary {boundary_resolved}"
    )


def _porcelain_split(porcelain: str) -> tuple[list[str], list[str], list[str]]:
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in porcelain.split("\n"):
        if not line:
            continue
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        path_part = line[3:]
        if " -> " in path_part and x in ("R", "C"):
            path_part = path_part.split(" -> ", 1)[1]
        if x == "?" and y == "?":
            untracked.append(path_part)
        else:
            if x not in (" ", "?"):
                staged.append(path_part)
            if y not in (" ", "?"):
                unstaged.append(path_part)
    return staged, unstaged, untracked


def inspect_git(project_root: Path, boundary: Path | None = None, max_entries: int = 100) -> dict[str, object]:
    """Inspect Git state within the resolved project boundary (read-only)."""
    if max_entries < 1:
        max_entries = 100
    max_entries = min(max_entries, HARD_MAX_ENTRIES)
    boundary = boundary or project_root
    git_root = find_git_root(project_root, boundary)

    branch, _, rc = _run_git(git_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        raise GitNotFoundError("git rev-parse --abbrev-ref failed")
    head_sha, _, rc = _run_git(git_root, ["git", "rev-parse", "HEAD"])
    if rc != 0:
        raise GitNotFoundError("git rev-parse HEAD failed")
    head_sha = head_sha.strip()
    if len(head_sha) != 40:
        raise GitNotFoundError("git HEAD is not a full 40-char SHA")

    porcelain, _, rc = _run_git(git_root, ["git", "status", "--porcelain=v1", "--untracked-files=normal"])
    if rc != 0:
        raise GitNotFoundError("git status failed")
    staged, unstaged, untracked = _porcelain_split(porcelain)

    truncated = lambda items, n=max_entries: len(items) > n
    staged_truncated = truncated(staged)
    unstaged_truncated = truncated(unstaged)
    untracked_truncated = truncated(untracked)
    staged = staged[:max_entries]
    unstaged = unstaged[:max_entries]
    untracked = untracked[:max_entries]

    relative = git_root.relative_to(boundary.resolve())
    repo_root = "." if relative == Path(".") else relative.as_posix()

    return {
        "available": True,
        "repo_root": repo_root,
        "branch": branch.strip() or "unknown",
        "head_sha": head_sha,
        "head_short": head_sha[:12],
        "is_dirty": bool(staged or unstaged or untracked),
        "staged": {"count": len(staged), "paths": staged, "truncated": staged_truncated},
        "unstaged": {"count": len(unstaged), "paths": unstaged, "truncated": unstaged_truncated},
        "untracked": {"count": len(untracked), "paths": untracked, "truncated": untracked_truncated},
        "boundary": str(boundary.resolve()),
    }
