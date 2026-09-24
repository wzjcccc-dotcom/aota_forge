"""Real Git observation coverage for ignored runtime artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from aota_forge.core.git.inspect import inspect_git
from aota_forge.runtime.task_main.review_adoption import _trusted_source_is_clean


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(root: Path) -> None:
    _git(root, "init", "--quiet")
    (root / ".gitignore").write_text(
        ".aota/handoffs/\n.aota/opencode/\n*.pyc\n.pytest_cache/\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("clean\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "README.md")
    _git(
        root,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )


def test_ignored_nested_unknown_artifact_is_observed_and_rejected(tmp_path: Path) -> None:
    _repo(tmp_path)
    artifact = tmp_path / ".aota" / "handoffs" / "nested" / "unknown.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("unknown\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert ".aota/handoffs/nested/unknown.json" in observation["ignored"]["paths"]
    assert ".aota/handoffs/nested/unknown.json" not in observation["untracked"]["paths"]
    assert observation["is_dirty"] is False
    assert _trusted_source_is_clean(observation) is False


def test_ignored_known_runtime_artifact_remains_accepted(tmp_path: Path) -> None:
    _repo(tmp_path)
    artifact = tmp_path / ".aota" / "handoffs" / ("a" * 64 + ".json")
    artifact.parent.mkdir(parents=True)
    artifact.write_text("runtime\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert observation["ignored"]["paths"] == [
        ".aota/handoffs/" + "a" * 64 + ".json"
    ]
    assert _trusted_source_is_clean(observation) is True


def test_ignored_observation_truncation_fails_closed(tmp_path: Path) -> None:
    _repo(tmp_path)
    handoffs = tmp_path / ".aota" / "handoffs"
    handoffs.mkdir(parents=True)
    for name in ("one.json", "two.json"):
        (handoffs / name).write_text("runtime\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path, max_entries=1)

    assert observation["ignored"]["truncated"] is True
    assert _trusted_source_is_clean(observation) is False


def test_ignored_opencode_binding_artifacts_remain_accepted(tmp_path: Path) -> None:
    _repo(tmp_path)
    instance = tmp_path / ".aota" / "opencode" / "instances" / "task-main-run-1"
    pointer = instance / ".aota" / "opencode" / "active_binding.json"
    envelope = instance / ".aota" / "pre-resolved-bindings" / "task-main-abcd1234-efgh5678.json"
    pointer.parent.mkdir(parents=True)
    envelope.parent.mkdir(parents=True)
    pointer.write_text("runtime\n", encoding="utf-8")
    envelope.write_text("runtime\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert str(pointer.relative_to(tmp_path)) in observation["ignored"]["paths"]
    assert str(envelope.relative_to(tmp_path)) in observation["ignored"]["paths"]
    assert _trusted_source_is_clean(observation) is True


def test_unknown_ignored_opencode_artifact_is_rejected(tmp_path: Path) -> None:
    _repo(tmp_path)
    artifact = (
        tmp_path
        / ".aota"
        / "opencode"
        / "instances"
        / "task-main-run-1"
        / "unexpected.txt"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("unknown\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert str(artifact.relative_to(tmp_path)) in observation["ignored"]["paths"]
    assert _trusted_source_is_clean(observation) is False


def test_ignored_python_bytecode_remains_accepted(tmp_path: Path) -> None:
    _repo(tmp_path)
    bytecode = tmp_path / "aota_forge" / "runtime" / "__pycache__" / "module.cpython-312.pyc"
    bytecode.parent.mkdir(parents=True)
    bytecode.write_bytes(b"runtime\n")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert str(bytecode.relative_to(tmp_path)) in observation["ignored"]["paths"]
    assert _trusted_source_is_clean(observation) is True


def test_ignored_pytest_cache_remains_accepted(tmp_path: Path) -> None:
    _repo(tmp_path)
    cache = tmp_path / ".pytest_cache" / "v" / "cache" / "nodeids"
    cache.parent.mkdir(parents=True)
    cache.write_text("[]\n", encoding="utf-8")

    observation = inspect_git(tmp_path, boundary=tmp_path)

    assert ".pytest_cache/v/cache/nodeids" in observation["ignored"]["paths"]
    assert _trusted_source_is_clean(observation) is True
