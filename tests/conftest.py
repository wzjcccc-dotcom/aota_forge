"""Session-wide test harness for the operator-owned runtime config channel.

M1/W4 invariant: production composition has NO source-owned deployment
fallback. Tests therefore supply an explicit trusted operator config through
the same channel an operator would (``AOTA_FORGE_RUNTIME_CONFIG`` -> bounded
JSON file), constructed here with test-owned values:

* executable: a bounded temporary executable file (never launched by tests
  that inject a host client / fake process seam);
* provider/model: deterministic test pins, clearly not vendor deployment
  authority;
* worker bindings: shared ``aota-worker`` profile with the mandatory
  ``aota`` MCP toolset pin; task-main: ``aota-task-main``.

Tests that need different shapes build their own RuntimeConfig explicitly.
The fail-closed-on-missing-config behaviour is covered by unit tests that
clear the env channel and by the isolated production-composition challenge
driver (scripts/m1_w4_runtime_config_challenge.py), which must run WITHOUT
this fixture.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

TEST_EXECUTABLE_NAME = "aota-test-hermes-stub"
TEST_PROVIDER = "aota-test-provider"
TEST_MODEL = "aota-test-model"


def worker_bindings() -> dict[str, Any]:
    return {
        "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
        "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
        "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
        "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
        "task-main": {"profile": "aota-task-main"},
    }


def runtime_config_document(executable: str) -> dict[str, Any]:
    return {
        "executor": "hermes",
        "executable": executable,
        "concurrency": 1,
        "provider": TEST_PROVIDER,
        "model": TEST_MODEL,
        "bindings": worker_bindings(),
    }


@pytest.fixture(scope="session")
def test_hermes_executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Bounded temporary executable fixture used as the operator-pinned path."""
    exe = tmp_path_factory.mktemp("operator-bin") / TEST_EXECUTABLE_NAME
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


@pytest.fixture(scope="session")
def operator_config_file(test_hermes_executable: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    cfg_dir = tmp_path_factory.mktemp("operator-config")
    cfg_file = cfg_dir / "runtime.json"
    cfg_file.write_text(
        json.dumps(runtime_config_document(str(test_hermes_executable)), sort_keys=True),
        encoding="utf-8",
    )
    return cfg_file


@pytest.fixture(scope="session", autouse=True)
def _operator_runtime_config_env(operator_config_file: Path) -> Any:
    """Publish the session operator config through the trusted env channel."""
    previous = os.environ.get("AOTA_FORGE_RUNTIME_CONFIG")
    os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = str(operator_config_file)
    yield
    if previous is None:
        os.environ.pop("AOTA_FORGE_RUNTIME_CONFIG", None)
    else:
        os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = previous


@pytest.fixture(autouse=True)
def _synthetic_project_evidence_for_tests(monkeypatch) -> Any:
    """Explicit test-only seam for synthetic project evidence (W2).

    Production path must fail closed without canonical evidence
    (SYNTHETIC_PROJECT_AUTHORITY_PRODUCTION_PATH=no). Tests that use
    tmp_path without a real manifest may explicitly allow synthetic via this
    seam. This fixture provides the seam for legacy tests that have not yet
    migrated to valid manifests; tests that verify fail-closed behavior can
    clear the env var via monkeypatch.delenv.
    """
    monkeypatch.setenv("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", "1")
    yield
    # monkeypatch automatically undoes
