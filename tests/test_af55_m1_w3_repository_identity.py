"""AF #55 M1/W3 — Source Repository Identity & Git-Origin Grounding.

Deterministic, local-only proofs (no GitHub network access; temporary local
Git repositories only):

* HTTPS GitHub URL normalization PASS
* SSH GitHub URL normalization PASS
* optional .git suffix normalization PASS
* matching origin → PASS
* different owner → mismatch
* different repo → mismatch
* folder name match but wrong origin → FAIL
* folder name mismatch but correct origin → PASS
* project_id mismatch remains independent from repo-name matching
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aota_forge.core.contracts.errors import (
    GitNotFoundError,
    ProjectSourceRepositoryMismatchError,
)
from aota_forge.core.project.repository_identity import (
    FOLDER_NAME_SIMILARITY_IS_IDENTITY,
    GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE,
    REPOSITORY_IDENTITY_MECHANISM,
    REPOSITORY_IDENTITY_PERSISTED_IN_MANIFEST,
    REPOSITORY_IDENTITY_SIDE_CAR_RECORD_CREATED,
    SOURCE_REPOSITORY_VALIDATED_MECHANICALLY,
    normalize_repository_identity,
    verify_checkout_repository_identity,
)

OWNER = "wzjcccc-dotcom"
REPO = "aota_reader_mcp"


def _git_checkout(root: Path, origin: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/wzjcccc-dotcom/aota_reader_mcp.git",
        "http://github.com/wzjcccc-dotcom/aota_reader_mcp",
        "git@github.com:wzjcccc-dotcom/aota_reader_mcp.git",
        "git@github.com:wzjcccc-dotcom/aota_reader_mcp",
        "ssh://git@github.com/wzjcccc-dotcom/aota_reader_mcp.git",
        "git://github.com/wzjcccc-dotcom/aota_reader_mcp",
        "wzjcccc-dotcom/aota_reader_mcp",
        "https://github.com/wzjcccc-dotcom/aota_reader_mcp.git/",
        "https://WWW.GitHub.com/WZJCCCC-Dotcom/AOTA_Reader_MCP.git",
    ],
)
def test_w3_github_forms_normalize_to_one_identity(raw: str) -> None:
    assert normalize_repository_identity(raw) == f"{OWNER}/{REPO}"


def test_w3_non_github_host_normalizes_with_host() -> None:
    assert (
        normalize_repository_identity("git@gitlab.example.com:group/project.git")
        == "gitlab.example.com/group/project"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "github.com",
        "https://github.com/onlyowner",
        "https://github.com/a/b/c",
        "https://github.com/a/",
        "/absolute/path/repo",
        "https://github.com/../etc/passwd",
    ],
)
def test_w3_unparseable_forms_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_repository_identity(raw)


def test_w3_matching_origin_pass(tmp_path: Path) -> None:
    checkout = _git_checkout(tmp_path / "checkout", f"https://github.com/{OWNER}/{REPO}.git")
    evidence = verify_checkout_repository_identity(
        project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
    )
    assert evidence.verified is True
    assert evidence.declared_normalized == f"{OWNER}/{REPO}"
    assert evidence.git_origin_normalized == f"{OWNER}/{REPO}"


def test_w3_different_owner_mismatch(tmp_path: Path) -> None:
    checkout = _git_checkout(tmp_path / "checkout", "https://github.com/other-owner/aota_reader_mcp.git")
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        verify_checkout_repository_identity(
            project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
        )


def test_w3_different_repo_mismatch(tmp_path: Path) -> None:
    checkout = _git_checkout(tmp_path / "checkout", f"https://github.com/{OWNER}/other_repo.git")
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        verify_checkout_repository_identity(
            project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
        )


def test_w3_folder_name_match_but_wrong_origin_fails(tmp_path: Path) -> None:
    checkout = _git_checkout(
        tmp_path / REPO, "https://github.com/completely-different/not_the_repo.git"
    )
    assert checkout.name == REPO
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        verify_checkout_repository_identity(
            project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
        )


def test_w3_folder_name_mismatch_but_correct_origin_passes(tmp_path: Path) -> None:
    checkout = _git_checkout(
        tmp_path / "chatgpt-hermes-mcp-poc", f"https://github.com/{OWNER}/{REPO}.git"
    )
    evidence = verify_checkout_repository_identity(
        project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
    )
    assert evidence.verified is True


def test_w3_repository_verification_independent_of_project_id(tmp_path: Path) -> None:
    # Folder name, declared project identity and repository name all differ;
    # only the exact normalized Git origin is compared.
    checkout = _git_checkout(
        tmp_path / "chatgpt-hermes-mcp-poc", f"git@github.com:{OWNER}/{REPO}.git"
    )
    evidence = verify_checkout_repository_identity(
        project_root=checkout,
        declared_source_repository=f"{OWNER}/{REPO}",
    )
    assert evidence.verified is True
    assert evidence.declared_normalized != checkout.name


def test_w3_malformed_declared_identity_fails_closed(tmp_path: Path) -> None:
    checkout = _git_checkout(tmp_path / "checkout", f"https://github.com/{OWNER}/{REPO}.git")
    with pytest.raises(ProjectSourceRepositoryMismatchError):
        verify_checkout_repository_identity(
            project_root=checkout, declared_source_repository="not a repo identity"
        )


def test_w3_missing_git_repository_fails_closed(tmp_path: Path) -> None:
    checkout = tmp_path / "nogit"
    checkout.mkdir()
    with pytest.raises(GitNotFoundError):
        verify_checkout_repository_identity(
            project_root=checkout, declared_source_repository=f"{OWNER}/{REPO}"
        )


def test_w3_identity_markers() -> None:
    assert SOURCE_REPOSITORY_VALIDATED_MECHANICALLY is True
    assert GIT_REMOTE_IDENTITY_IS_AUTHORITATIVE is True
    assert FOLDER_NAME_SIMILARITY_IS_IDENTITY is False
    assert REPOSITORY_IDENTITY_MECHANISM == "resolver_time_git_origin_verification"
    assert REPOSITORY_IDENTITY_PERSISTED_IN_MANIFEST is False
    assert REPOSITORY_IDENTITY_SIDE_CAR_RECORD_CREATED is False
