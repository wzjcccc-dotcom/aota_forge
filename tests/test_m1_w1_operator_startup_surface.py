"""M1/W1 — Operator RuntimeConfig & User Startup Surface (focused validation).

Covers W1 invariants per spec section 13:

  RUNTIME_CONFIG_OPERATOR_OWNED
  MISSING_RUNTIME_CONFIG_FAIL_CLOSED
  WORKER_PROFILE_MAPPING
  WORKER_TOOLSETS_EXACTLY_AOTA
  SOURCE_PROVIDER_DEFAULT_ABSENT
  SOURCE_MODEL_DEFAULT_ABSENT
  TASK_MAIN_STARTUP_SEED_FOUND
  TASK_MAIN_OPERATOR_PROMPT_PATH
  OPERATOR_PROMPT_EFFECTIVE_FILE_REQUIRED
  SOURCE_SEED_SILENT_RUNTIME_FALLBACK=no
  TASK_MAIN_STARTUP_PROMPT_BOUNDED
  TASK_MAIN_STARTUP_PROMPT_NOT_AUTHORITY
  DAILY_LAUNCHER_EXISTING_AUTHORITY_CHAIN_PRESERVED
"""

from __future__ import annotations

import json
import pathlib
import re
import stat
from pathlib import Path

import pytest

from aota_forge.runtime.config import (
    RuntimeConfigError,
    load_runtime_config,
    worker_canonical_profile_mapping,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PROMPT_PATH = REPO_ROOT / "prompts" / "task-main-startup.default.md"
OPERATOR_PROMPT_PATH = Path.home() / ".config" / "aota-forge" / "task-main-startup.md"
OPERATOR_RUNTIME_PATH = Path.home() / ".config" / "aota-forge" / "runtime.json"


def _stub_executable(tmp_path: Path) -> str:
    exe = tmp_path / "hermes-stub-w1"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


# ---------------------------------------------------------------------------
# 1. RuntimeConfig operator-owned, fail-closed, worker mapping
# ---------------------------------------------------------------------------


class TestRuntimeConfigOperatorOwned:
    def test_runtime_config_operator_owned_and_valid(self):
        # Production operator config must exist at canonical location and be valid
        assert OPERATOR_RUNTIME_PATH.is_file(), f"production runtime config missing: {OPERATOR_RUNTIME_PATH}"
        assert not OPERATOR_RUNTIME_PATH.is_symlink(), "runtime config must not be symlink"
        cfg = load_runtime_config(config_path=str(OPERATOR_RUNTIME_PATH))
        assert cfg.executor == "hermes"
        assert cfg.provider == "opencode-go"
        assert cfg.model == "deepseek-v4-flash"
        # Worker profile mapping
        mapping = worker_canonical_profile_mapping(cfg)
        assert mapping["coder"] == "aota-worker"
        assert mapping["planner"] == "aota-worker"
        assert mapping["reviewer"] == "aota-worker"
        assert mapping["steward"] == "aota-worker"
        for role in ("analyst", "coder", "reviewer", "project-steward"):
            b = cfg.get_binding(role)
            assert b.profile == "aota-worker"
            assert b.toolsets == ("aota",)

    def test_missing_runtime_config_fail_closed(self, monkeypatch):
        monkeypatch.delenv("AOTA_FORGE_RUNTIME_CONFIG", raising=False)
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config()
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config(environ={})

    def test_worker_toolsets_exactly_aota(self):
        cfg = load_runtime_config(config_path=str(OPERATOR_RUNTIME_PATH))
        for role in ("analyst", "coder", "reviewer", "project-steward"):
            assert cfg.get_binding(role).toolsets == ("aota",)
        assert cfg.get_binding("task-main").profile == "aota-task-main"

    def test_source_provider_model_default_absent(self):
        # Production modules must not contain source-owned provider/model defaults
        for rel in (
            "aota_forge/runtime/config.py",
            "aota_forge/composition/task_main_daily_launcher.py",
        ):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            # Forbidden source-owned defaults
            assert 'DEFAULT_PROVIDER' not in text, f"{rel} contains DEFAULT_PROVIDER"
            assert 'DEFAULT_MODEL' not in text, f"{rel} contains DEFAULT_MODEL"
            assert 'DEFAULT_PROVIDER="opencode-go"' not in text
            assert 'DEFAULT_MODEL="deepseek-v4-flash"' not in text
            # Ensure no hardcoded deployment default that would masquerade as operator authority
            # We allow the operator file itself to contain the values, but not source code
            lowered = text.lower()
            # Source code must not hardcode the observed runtime values as defaults
            # Check for literal default assignment pattern
            assert 'provider = "opencode-go"' not in text
            assert "provider = 'opencode-go'" not in text
            assert 'model = "deepseek-v4-flash"' not in text

    def test_production_runtime_config_found_and_valid(self):
        cfg = load_runtime_config(config_path=str(OPERATOR_RUNTIME_PATH))
        # Validate via existing seam
        assert cfg.get_binding("coder").provider == "opencode-go"
        assert cfg.get_binding("coder").model == "deepseek-v4-flash"
        # Executable must exist, not symlink, executable
        exe = Path(cfg.executable)
        assert not exe.is_symlink()
        assert exe.is_file()
        import os

        assert os.access(exe, os.X_OK)


# ---------------------------------------------------------------------------
# 2. Startup prompt seed / operator surface
# ---------------------------------------------------------------------------


class TestStartupPromptSurface:
    def test_task_main_startup_seed_found(self):
        assert SEED_PROMPT_PATH.is_file(), f"seed not found: {SEED_PROMPT_PATH}"
        assert not SEED_PROMPT_PATH.is_symlink()
        text = SEED_PROMPT_PATH.read_text(encoding="utf-8")
        assert len(text.strip()) > 0
        assert len(text.encode("utf-8")) <= 8 * 1024
        lowered = text.lower()
        assert "aota.invoke" in lowered
        assert "role.bootstrap" in lowered
        # Seed must not be authority
        assert "PLAN_TYPE=portable_plan" not in text
        assert "OPERATION_CATALOG" not in text

    def test_task_main_operator_prompt_path(self):
        # Effective operator path must be exactly ~/.config/aota-forge/task-main-startup.md
        expected = Path.home() / ".config" / "aota-forge" / "task-main-startup.md"
        assert OPERATOR_PROMPT_PATH == expected
        # Check launcher constant matches
        from aota_forge.composition.task_main_daily_launcher import OPERATOR_STARTUP_PROMPT_PATH

        assert OPERATOR_STARTUP_PROMPT_PATH == expected
        assert OPERATOR_PROMPT_PATH.is_file(), f"operator prompt missing: {OPERATOR_PROMPT_PATH}"
        assert not OPERATOR_PROMPT_PATH.is_symlink()

    def test_operator_prompt_effective_file_required(self):
        from aota_forge.composition.task_main_daily_launcher import _read_operator_startup_prompt

        # Present file should load
        text = _read_operator_startup_prompt()
        assert text.strip()
        assert "aota.invoke" in text.lower()
        assert "role.bootstrap" in text.lower()

        # Missing file must fail closed
        import tempfile

        missing = Path(tempfile.mktemp())
        # Ensure missing
        if missing.exists():
            missing.unlink()
        with pytest.raises(RuntimeError, match="operator task-main startup prompt missing"):
            _read_operator_startup_prompt(operator_path=missing)
        # Symlink must be rejected
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real.md"
            real.write_text("you are af task-main aota.invoke role.bootstrap", encoding="utf-8")
            link = Path(td) / "link.md"
            link.symlink_to(real)
            with pytest.raises(RuntimeError, match="symlink"):
                _read_operator_startup_prompt(operator_path=link)

    def test_source_seed_silent_runtime_fallback_no(self):
        from aota_forge.composition.task_main_daily_launcher import _resolve_task_main_startup_prompt
        import tempfile

        # Even though seed exists, missing operator must not silently use seed
        assert SEED_PROMPT_PATH.is_file()
        missing = Path(tempfile.mktemp())
        import aota_forge.composition.task_main_daily_launcher as mod

        orig = mod.OPERATOR_STARTUP_PROMPT_PATH
        try:
            mod.OPERATOR_STARTUP_PROMPT_PATH = missing
            with pytest.raises(RuntimeError, match="no silent fallback to seed"):
                _resolve_task_main_startup_prompt(None)
        finally:
            mod.OPERATOR_STARTUP_PROMPT_PATH = orig

    def test_task_main_startup_prompt_bounded(self):
        text = OPERATOR_PROMPT_PATH.read_text(encoding="utf-8")
        assert len(text) > 0
        assert len(text) <= 8 * 1024
        assert len(text.encode("utf-8")) <= 8 * 1024
        # Launcher helper also enforces bound
        from aota_forge.composition.task_main_daily_launcher import MAX_STARTUP_PROMPT_BYTES

        assert MAX_STARTUP_PROMPT_BYTES == 8 * 1024

    def test_task_main_startup_prompt_not_authority(self):
        text = OPERATOR_PROMPT_PATH.read_text(encoding="utf-8")
        lowered = text.lower()
        # Must contain minimal AF bootstrap reference
        assert "aota.invoke" in lowered
        assert "role.bootstrap" in lowered
        # Must not embed full Plan body, Tool manual, operation catalog, etc.
        assert "PLAN_TYPE=portable_plan" not in text
        assert "OPERATION_CATALOG" not in text
        assert "FULL_PLAN_BODY" not in text
        # Must not be overly verbose (bounded already ensures minimal)
        assert len(text) < 4096  # minimal prompt is small
        # Check launcher constant says not authority
        from aota_forge.composition.task_main_daily_launcher import (
            TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY,
            AF_ROLE_AUTHORITY_FROM_STARTUP_PROMPT,
            PLAN_AUTHORITY_FROM_STARTUP_PROMPT,
        )

        assert TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY == "no"
        assert AF_ROLE_AUTHORITY_FROM_STARTUP_PROMPT == "no"
        assert PLAN_AUTHORITY_FROM_STARTUP_PROMPT == "no"

    def test_seed_does_not_contain_authority(self):
        text = SEED_PROMPT_PATH.read_text(encoding="utf-8")
        # Seed must be minimal too, not full Plan
        assert "PLAN_TYPE=portable_plan" not in text
        assert "M1_DAG" not in text
        # Seed should be SEED_ONLY, not authority
        assert "RUNTIME_AUTHORITY=no" in text or "SEED_ONLY=yes" in text


# ---------------------------------------------------------------------------
# 3. DailyTaskMainLauncher wiring — authority chain preserved
# ---------------------------------------------------------------------------


class TestDailyLauncherWiring:
    def test_daily_launcher_existing_authority_chain_preserved(self):
        src = (REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py").read_text(
            encoding="utf-8"
        )
        # Must still contain the canonical authority chain in order
        assert "read authoritative live Plan via selected PlanAuthorityReadAdapter" in src
        assert "normalize via canonical Plan layer" in src
        assert "project typed Milestone views" in src
        assert "verify current user approval truth" in src
        assert "resolve RuntimeConfig (operator authority)" in src
        assert "materialize trusted bootstrap" in src
        assert "startup prompt resolution" in src
        assert "construct MCP child environment" in src
        assert "launch or resume Hermes profile=aota-task-main" in src
        # Must not bypass trusted bootstrap
        assert "write_bootstrap_file" in src
        assert "STARTUP_PROMPT_IS_AUTHORITY" not in src or "TASK_MAIN_STARTUP_PROMPT_IS_AUTHORITY" in src

    def test_launcher_does_not_bypass_bootstrap(self):
        src = (REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py").read_text(
            encoding="utf-8"
        )
        # Ensure bootstrap is still materialized before prompt resolution in the
        # documented responsibility chain (top docstring) and in launch flow.
        # Docstring chain must be in order: trusted bootstrap -> startup prompt -> MCP env -> launch
        doc_start = src.find("Responsibility (orchestration/composition only)")
        chain = src[doc_start : doc_start + 2000] if doc_start != -1 else src
        boot_doc = chain.find("materialize trusted bootstrap")
        prompt_doc = chain.find("startup prompt resolution")
        mcp_doc = chain.find("construct MCP child environment")
        launch_doc = chain.find("launch or resume Hermes")
        assert boot_doc != -1 and prompt_doc != -1 and mcp_doc != -1 and launch_doc != -1
        assert boot_doc < prompt_doc < mcp_doc < launch_doc, "docstring chain must be bootstrap -> prompt -> MCP -> launch"
        # Launch method must call prompt resolver after prepare (which does bootstrap)
        launch_section = src[src.find("def launch") :]
        assert "_resolve_task_main_startup_prompt" in launch_section
        assert "write_bootstrap_file" in src  # still present in prepare

    def test_launcher_startup_prompt_after_bootstrap_before_launch(self):
        src = (REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py").read_text(
            encoding="utf-8"
        )
        # In launch(), prompt resolution must happen after prepare() (which does bootstrap)
        launch_section = src[src.find("def launch") :]
        # Must call helper, not silent seed fallback
        assert "_resolve_task_main_startup_prompt" in launch_section
        assert "OPERATOR_STARTUP_PROMPT_PATH" in src
        assert "SEED_STARTUP_PROMPT_PATH" in src
        # Must not contain fallback logic like "if operator_file_missing: read seed"
        # Check that the helper _read_operator_startup_prompt raises and mentions no silent fallback
        assert "no silent fallback to seed" in launch_section or "no silent fallback to seed" in src

    def test_launcher_preserves_plan_authority_boundary(self):
        src = (REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py").read_text(
            encoding="utf-8"
        )
        assert "EXISTING_PLAN_AUTHORITY_BOUNDARY_REUSED" in src
        assert "PLAN_AUTHORITY_OPERATOR_SELECTABLE" in src
        assert 'PLAN_AUTHORITY_HARDCODED_TO_ISSUE_37 = "no"' in src
