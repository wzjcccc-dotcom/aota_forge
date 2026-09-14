"""Mechanical repository identity grounding (AF #55 M1/W3).

Grounds a Plan-declared ``SOURCE_REPOSITORY`` against the checkout's actual
Git ``origin`` remote, mechanically and deterministically:

    Plan declares:      SOURCE_REPOSITORY=owner/repo
    Checkout carries:   git remote get-url origin = <any acceptable form>
    Control Plane:      normalize both → compare exact canonical identity

Acceptable forms normalize to one canonical identity through exact structural
parsing (never substring / suffix / name similarity):

    https://github.com/owner/repo.git
    http://github.com/owner/repo
    ssh://git@github.com/owner/repo.git
    git@github.com:owner/repo.git
    git://github.com/owner/repo.git
    owner/repo

Git remote identity is authoritative for "which source repository does this
checkout represent".  It is NOT semantic Plan authority: it never decides
which project the user intended, which Milestone runs or whether a Plan is
approved.

FOLDER_NAME_SIMILARITY_IS_IDENTITY=no
SOURCE_REPOSITORY_VALIDATED_MECHANICALLY=yes
GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE=yes
CONTROL_PLANE_GROUNDS_REPOSITORY_IDENTITY=yes
CONTROL_PLANE_INTERPRETS_PROJECT_SEMANTICS=no
NEW_GIT_EXECUTOR_CREATED=no (reuses core/git bounded mechanics)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aota_forge.core.contracts.errors import (
    GitNotFoundError,
    ProjectSourceRepositoryMismatchError,
)
from aota_forge.core.git.inspect import read_git_origin

MAX_REPOSITORY_IDENTITY_LENGTH = 512
MAX_SEGMENT_LENGTH = 100

_SCOPED_RE = re.compile(r"^(?P<user>[^@/\\:]+)@(?P<host>[^/\\:]+):(?P<path>.+)$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

SOURCE_REPOSITORY_VALIDATED_MECHANICALLY = True
GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE = True
FOLDER_NAME_SIMILARITY_IS_IDENTITY = False
GITHUB_REPOSITORY_NAME_IS_LOCAL_DIRECTORY_NAME = False
CONTROL_PLANE_GROUNDS_REPOSITORY_IDENTITY = True
CONTROL_PLANE_INTERPRETS_PROJECT_SEMANTICS = False
REPOSITORY_IDENTITY_MECHANISM = "resolver_time_git_origin_verification"
REPOSITORY_IDENTITY_PERSISTED_IN_MANIFEST = False
REPOSITORY_IDENTITY_SIDE_CAR_RECORD_CREATED = False
NEW_GIT_EXECUTOR_CREATED = False


def _clean(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("repository identity must be a string")
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_REPOSITORY_IDENTITY_LENGTH or "\x00" in candidate:
        raise ValueError("repository identity must be a bounded non-empty string")
    return candidate


def _split_path_segments(path: str) -> list[str]:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    return segments


def _validate_segment(segment: str) -> str:
    if not _SEGMENT_RE.fullmatch(segment) or len(segment) > MAX_SEGMENT_LENGTH:
        raise ValueError(f"repository identity segment is invalid: {segment!r}")
    if segment in (".", ".."):
        raise ValueError("repository identity segment must not traverse")
    return segment


def _normalize_host(host: str) -> str:
    lowered = host.lower()
    if lowered.startswith("www."):
        lowered = lowered[4:]
    return lowered


def normalize_repository_identity(value: str) -> str:
    """Normalize an acceptable repository form to one canonical identity.

    * GitHub hosts normalize to ``owner/repo`` (lowercased; GitHub
      owner/repo identity is case-insensitive).
    * Other SSH/HTTPS hosts normalize to ``host/owner/repo`` (host
      lowercased, path case preserved).
    * A bare ``owner/repo`` (no host) normalizes to ``owner/repo``.

    Raises ``ValueError`` on any form that cannot be structurally mapped to
    exactly ``host`` + two path segments.  No substring or similarity logic
    is used anywhere.
    """
    candidate = _clean(value)
    host = ""
    path = ""

    if "://" in candidate:
        parts = urlsplit(candidate)
        host = _normalize_host(parts.hostname or "")
        path = parts.path
    elif _SCOPED_RE.match(candidate):
        match = _SCOPED_RE.match(candidate)
        assert match is not None
        host = _normalize_host(match.group("host"))
        path = match.group("path")
    elif "/" in candidate:
        host = ""
        path = candidate
    else:
        raise ValueError(f"repository identity form is not recognized: {candidate!r}")

    segments = _split_path_segments(path)
    if segments and segments[-1].lower().endswith(".git"):
        segments[-1] = segments[-1][: -len(".git")]
    if len(segments) != 2:
        raise ValueError(
            f"repository identity must be exactly owner/repo, got {len(segments)} path segments"
        )
    owner = _validate_segment(segments[0])
    repo = _validate_segment(segments[1])

    if not host:
        return f"{owner.lower()}/{repo.lower()}"
    if host == "github.com":
        return f"{owner.lower()}/{repo.lower()}"
    return f"{host}/{owner}/{repo}"


@dataclass(frozen=True)
class RepositoryIdentityEvidence:
    """Mechanical repository identity proof (never project semantic authority)."""

    project_root: str
    declared_source_repository: str
    declared_normalized: str
    git_origin: str
    git_origin_normalized: str
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "declared_source_repository": self.declared_source_repository,
            "declared_normalized": self.declared_normalized,
            "git_origin": self.git_origin,
            "git_origin_normalized": self.git_origin_normalized,
            "verified": self.verified,
        }


def verify_checkout_repository_identity(
    *,
    project_root: Path,
    declared_source_repository: str,
    boundary: Path | None = None,
) -> RepositoryIdentityEvidence:
    """Verify a checkout's Git origin against the declared SOURCE_REPOSITORY.

    Fail-closed:
        * unreadable / absent origin → GitNotFoundError
        * unparseable or non-matching identity → ProjectSourceRepositoryMismatchError

    The comparison is exact canonical identity equality.  Folder names,
    project_id, GitHub repo names and local paths are never compared.
    """
    root = Path(project_root)
    try:
        if root.is_symlink():
            raise GitNotFoundError(f"project root must not be a symlink: {root}")
    except OSError as exc:
        raise GitNotFoundError(f"project root inaccessible: {exc}") from exc
    if not root.exists() or not root.is_dir():
        raise GitNotFoundError(f"project root does not exist or is not a directory: {root}")

    origin = read_git_origin(root, boundary=boundary or root)
    try:
        declared_normalized = normalize_repository_identity(declared_source_repository)
    except ValueError as exc:
        raise ProjectSourceRepositoryMismatchError(
            f"declared SOURCE_REPOSITORY is not a valid repository identity: {exc}"
        ) from exc
    try:
        origin_normalized = normalize_repository_identity(origin)
    except ValueError as exc:
        raise ProjectSourceRepositoryMismatchError(
            f"checkout Git origin cannot be grounded to a repository identity: {exc}"
        ) from exc

    if declared_normalized != origin_normalized:
        raise ProjectSourceRepositoryMismatchError(
            f"PROJECT_SOURCE_REPOSITORY_MISMATCH: declared {declared_normalized!r} "
            f"does not match checkout origin {origin_normalized!r}"
        )
    return RepositoryIdentityEvidence(
        project_root=str(root),
        declared_source_repository=declared_source_repository,
        declared_normalized=declared_normalized,
        git_origin=origin,
        git_origin_normalized=origin_normalized,
        verified=True,
    )


__all__ = [
    "MAX_REPOSITORY_IDENTITY_LENGTH",
    "normalize_repository_identity",
    "RepositoryIdentityEvidence",
    "verify_checkout_repository_identity",
    "SOURCE_REPOSITORY_VALIDATED_MECHANICALLY",
    "GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE",
    "FOLDER_NAME_SIMILARITY_IS_IDENTITY",
    "GITHUB_REPOSITORY_NAME_IS_LOCAL_DIRECTORY_NAME",
    "CONTROL_PLANE_GROUNDS_REPOSITORY_IDENTITY",
    "CONTROL_PLANE_INTERPRETS_PROJECT_SEMANTICS",
    "REPOSITORY_IDENTITY_MECHANISM",
    "REPOSITORY_IDENTITY_PERSISTED_IN_MANIFEST",
    "REPOSITORY_IDENTITY_SIDE_CAR_RECORD_CREATED",
    "NEW_GIT_EXECUTOR_CREATED",
]
