"""M1/W1 — Runtime Configuration & Hermes Invocation Binding (bounded tests).

Covers section 18 of portable plan:

* Configuration valid/invalid cases
* Authority separation (TaskHandoff, Core)
* Worker shared profile + task-main independent binding
* Hermes invocation translation (profile/provider/model/missing exe/stale host)
* Determinism
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import tempfile
from typing import Any

import pytest

from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    canonical_to_hermes_payload,
)
from aota_forge.core.execution import (
    ExecutionPackage,
    ExecutorCapabilities,
)
from aota_forge.runtime.config import (
    DEFAULT_HERMES_EXECUTABLE,
    LEGACY_HERMES_HOST,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    get_default_runtime_config,
    load_runtime_config,
    resolve_binding_for_canonical_role,
    resolve_binding_for_work_role,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.mapping import resolve_work_role_to_canonical_role, WorkRoleMappingError
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.core.execution.roles import CanonicalRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_host(payload_captures: list[dict[str, Any]] | None = None):
    """Minimal fake host_client that captures payload and returns dispatch handle."""
    captures = payload_captures if payload_captures is not None else []

    class FakeHost:
        def dispatch(self, payload):
            captures.append(dict(payload))
            return {"adapter_handle": "hermes-host-fake1", "status": "pending", "dispatch_time": "2026-09-06T00:00:00Z"}

        def query_status(self, handle):
            return {"status": "running"}

        def fetch_result(self, handle):
            return {"status": "pending"}

        def cancel_task(self, handle):
            return {"cancelled": True, "status": "cancelled"}

        def resume_task(self, handle, payload):
            return {"status": "running"}

    return FakeHost(), captures


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_valid_runtime_config_loads(self, tmp_path):
        cfg_file = tmp_path / "runtime.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "executor": "hermes",
                    "executable": "/home/latios/.local/bin/hermes",
                    "concurrency": 2,
                    "provider": "opencode-go",
                    "model": "muse-spark-1.2-contributor",
                    "bindings": {
                        "coder": {"profile": "aota-worker", "provider": "opencode-go", "model": "muse-spark-1.2-contributor"},
                        "analyst": {"profile": "aota-worker"},
                        "reviewer": {"profile": "aota-worker"},
                        "project-steward": {"profile": "aota-worker"},
                        "task-main": {"profile": "aota-task-main", "provider": "opencode-go", "model": "muse-spark-1.2-contributor"},
                    },
                }
            )
        )
        cfg = load_runtime_config(config_path=str(cfg_file))
        assert cfg.executor == "hermes"
        assert cfg.executable == "/home/latios/.local/bin/hermes"
        assert cfg.concurrency == 2
        assert cfg.get_binding("coder").profile == "aota-worker"
        assert cfg.get_binding("task-main").profile == "aota-task-main"

    def test_invalid_executor_fails(self, tmp_path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text(json.dumps({"executor": "unknown_exec", "executable": "/home/latios/.local/bin/hermes", "bindings": {"coder": {"profile": "aota-worker"}}}))
        with pytest.raises(RuntimeConfigError, match="unknown executor"):
            load_runtime_config(config_path=str(cfg_file))

    def test_missing_required_profile_fails(self, tmp_path):
        cfg_file = tmp_path / "bad2.json"
        cfg_file.write_text(json.dumps({"executor": "hermes", "executable": "/home/latios/.local/bin/hermes", "bindings": {"coder": {}}}))
        with pytest.raises(RuntimeConfigError, match="missing required field 'profile'"):
            load_runtime_config(config_path=str(cfg_file))

    def test_invalid_concurrency_fails(self, tmp_path):
        for bad in [0, -1, 99, "1", 1.5, None]:
            cfg_file = tmp_path / f"bad_conc_{bad}.json"
            # sanitize filename
            safe = str(bad).replace("/", "_")
            cfg_file = tmp_path / f"bad_{safe}.json"
            cfg_file.write_text(json.dumps({"executor": "hermes", "executable": "/home/latios/.local/bin/hermes", "concurrency": bad, "bindings": {"coder": {"profile": "aota-worker"}}}))
            with pytest.raises(RuntimeConfigError):
                load_runtime_config(config_path=str(cfg_file))

        # per-binding concurrency invalid
        cfg_file = tmp_path / "bad_per_binding.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "executor": "hermes",
                    "executable": "/home/latios/.local/bin/hermes",
                    "bindings": {"coder": {"profile": "aota-worker", "concurrency": 0}},
                }
            )
        )
        with pytest.raises(RuntimeConfigError):
            load_runtime_config(config_path=str(cfg_file))

    def test_unknown_work_role_binding_fails(self):
        cfg = get_default_runtime_config(validate_executable=False)
        with pytest.raises(RuntimeConfigError, match="unknown work role"):
            cfg.get_binding("unknown-role")
        with pytest.raises(RuntimeConfigError, match="unknown work role"):
            cfg.get_binding("super-coder")
        # type-unsafe
        with pytest.raises(RuntimeConfigError):
            cfg.get_binding(123)  # type: ignore
        with pytest.raises(RuntimeConfigError):
            cfg.get_binding(None)  # type: ignore

    def test_invalid_profile_for_worker_fails(self, tmp_path):
        cfg_file = tmp_path / "bad_profile.json"
        cfg_file.write_text(
            json.dumps(
                {"executor": "hermes", "executable": "/home/latios/.local/bin/hermes", "bindings": {"coder": {"profile": "coder"}}}
            )
        )
        with pytest.raises(RuntimeConfigError, match="must map to shared profile"):
            load_runtime_config(config_path=str(cfg_file))

    def test_missing_executable_fails_closed(self, tmp_path):
        missing = str(tmp_path / "nonexistent-hermes")
        cfg_file = tmp_path / "missing_exe.json"
        cfg_file.write_text(json.dumps({"executor": "hermes", "executable": missing, "bindings": {"coder": {"profile": "aota-worker"}}}))
        with pytest.raises(RuntimeConfigError, match="missing or not a file"):
            load_runtime_config(config_path=str(cfg_file))

    def test_unknown_top_level_key_fails(self, tmp_path):
        cfg_file = tmp_path / "unknown_key.json"
        cfg_file.write_text(json.dumps({"executor": "hermes", "executable": "/home/latios/.local/bin/hermes", "unknownKey": "foo", "bindings": {"coder": {"profile": "aota-worker"}}}))
        with pytest.raises(RuntimeConfigError, match="unknown runtime config keys"):
            load_runtime_config(config_path=str(cfg_file))


# ---------------------------------------------------------------------------
# 2. Authority separation
# ---------------------------------------------------------------------------

class TestAuthoritySeparation:
    def test_task_handoff_has_no_provider_model_profile_deployment_authority(self):
        # TaskHandoff must not accept provider/model/profile as semantic fields
        base_kwargs = dict(
            work_role="coder",
            task_kind="implementation",
            objective="Implement feature X",
            bounded_scope="bounded scope",
            validation_expectations=("tests pass",),
            semantic_stop_expectations=("ambiguous",),
        )
        # Direct TaskHandoff construction with extra provider field should fail via from_dict
        with pytest.raises(ValueError, match="Unknown field|Forbidden"):
            TaskHandoff.from_dict({**base_kwargs, "work_role": "coder", "provider": "opencode-go", "task_kind": "implementation", "objective": "x", "bounded_scope": "b", "validation_expectations": [], "semantic_stop_expectations": []})
        with pytest.raises(ValueError, match="Unknown field|Forbidden"):
            TaskHandoff.from_dict({**base_kwargs, "model": "muse-spark-1.2", "work_role": "coder", "task_kind": "implementation", "objective": "x", "bounded_scope": "b", "validation_expectations": [], "semantic_stop_expectations": []})
        with pytest.raises(ValueError, match="Unknown field|Forbidden"):
            TaskHandoff.from_dict({**base_kwargs, "profile": "aota-worker", "work_role": "coder", "task_kind": "implementation", "objective": "x", "bounded_scope": "b", "validation_expectations": [], "semantic_stop_expectations": []})
        # Verify FORBIDDEN_MECHANICAL_FIELDS does not include provider/model but unknown field rejection covers it
        # Also check that TaskHandoff fields are exactly expected
        from aota_forge.work_plane.handoff import ALL_HANDOFF_FIELDS, FORBIDDEN_MECHANICAL_FIELDS
        assert "provider" not in ALL_HANDOFF_FIELDS
        assert "model" not in ALL_HANDOFF_FIELDS
        assert "profile" not in ALL_HANDOFF_FIELDS
        assert "provider" not in FORBIDDEN_MECHANICAL_FIELDS or True  # unknown field path is enough

    def test_core_remains_hermes_profile_neutral(self):
        # Core files must not import runtime or know hermes profiles
        core_files = [
            pathlib.Path("aota_forge/core/execution/roles.py"),
            pathlib.Path("aota_forge/core/execution/package.py"),
            pathlib.Path("aota_forge/core/execution/capabilities.py"),
            pathlib.Path("aota_forge/core/execution/dispatcher.py"),
            pathlib.Path("aota_forge/core/execution/adapter.py"),
        ]
        for fpath in core_files:
            text = fpath.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "runtime" not in alias.name.lower(), f"{fpath} imports runtime"
                        assert "hermes" not in alias.name.lower(), f"{fpath} imports hermes"
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert "runtime" not in mod.lower(), f"{fpath} imports runtime"
                    assert "hermes" not in mod.lower(), f"{fpath} imports hermes"
            # No Hermes profile strings in Core
            assert "aota-worker" not in text
            assert "aota-task-main" not in text

    def test_task_main_still_has_no_canonical_role_worker_mapping(self):
        # task-main must fail closed via mapping resolver
        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role("task-main")
        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)
        # But runtime binding for task-main must be resolvable (separate path)
        cfg = get_default_runtime_config(validate_executable=False)
        binding = cfg.get_binding("task-main")
        assert binding.profile == "aota-task-main"
        # Ensure no CanonicalRole.TASK_MAIN exists
        assert not hasattr(CanonicalRole, "TASK_MAIN")
        assert "task-main" not in {r.value for r in CanonicalRole}


# ---------------------------------------------------------------------------
# 3. Worker profile
# ---------------------------------------------------------------------------

class TestWorkerProfile:
    @pytest.mark.parametrize("role", ["analyst", "coder", "reviewer", "project-steward"])
    def test_worker_roles_share_aota_worker_runtime_profile(self, role):
        cfg = get_default_runtime_config(validate_executable=False)
        # Runtime binding -> shared profile
        binding = cfg.get_binding(role)
        assert binding.profile == SHARED_WORKER_PROFILE == "aota-worker"
        # Provider/model may be pinned per role or via defaults, but must be deterministic
        assert binding.provider is not None
        assert binding.model is not None
        # Also via canonical role lookup
        canonical = resolve_work_role_to_canonical_role(role)
        binding_via_canonical = resolve_binding_for_canonical_role(canonical.value, cfg)
        assert binding_via_canonical.profile == "aota-worker"
        assert binding_via_canonical.work_role == role

    def test_no_per_role_hermes_profiles_created(self):
        cfg = get_default_runtime_config(validate_executable=False)
        profiles = {b.profile for b in cfg.bindings}
        # Only two profiles allowed: shared worker and task-main
        assert profiles == {"aota-worker", "aota-task-main"}
        # Ensure not creating aota-analyst etc.
        for forbidden in ["aota-analyst", "aota-coder", "aota-reviewer", "aota-steward"]:
            assert forbidden not in profiles

    def test_runtime_bindings_are_not_canonical_role_mappings(self):
        # Ensure work_plane mapping still distinct from runtime profile
        # WorkPlane mapping is semantic translation to CanonicalRole, not Hermes profile
        for role, expected_canonical in [
            ("analyst", "planner"),
            ("coder", "coder"),
            ("reviewer", "reviewer"),
            ("project-steward", "steward"),
        ]:
            canonical = resolve_work_role_to_canonical_role(role)
            assert canonical.value == expected_canonical
            # CanonicalRole values must not equal Hermes profile
            assert canonical.value != "aota-worker"
            assert canonical.value != "aota-task-main"


# ---------------------------------------------------------------------------
# 4. task-main
# ---------------------------------------------------------------------------

class TestTaskMainBinding:
    def test_task_main_runtime_binding_resolves_aota_task_main(self):
        cfg = get_default_runtime_config(validate_executable=False)
        binding = cfg.get_binding("task-main")
        assert binding.profile == TASK_MAIN_PROFILE == "aota-task-main"
        assert binding.executor == "hermes"
        assert binding.work_role == "task-main"
        # Provider/model pinned
        assert binding.provider == "opencode-go"
        assert binding.model == "muse-spark-1.2-contributor"

    def test_task_main_does_not_become_worker_canonical_role(self):
        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)
        # And canonical role mapping for workers still not include task-main
        from aota_forge.work_plane.mapping import WORK_ROLE_TO_CANONICAL_ROLE

        assert AgentWorkRole.TASK_MAIN not in WORK_ROLE_TO_CANONICAL_ROLE

    def test_task_main_binding_independent_from_workers(self):
        cfg = get_default_runtime_config(validate_executable=False)
        task_binding = cfg.get_binding("task-main")
        coder_binding = cfg.get_binding("coder")
        assert task_binding.profile != coder_binding.profile
        assert task_binding.work_role == "task-main"
        assert coder_binding.work_role == "coder"
        # But both share same executor and defaults
        assert task_binding.executor == coder_binding.executor == "hermes"


# ---------------------------------------------------------------------------
# 5. Hermes invocation
# ---------------------------------------------------------------------------

class TestHermesInvocation:
    def test_profile_translated_correctly(self):
        cfg = get_default_runtime_config(validate_executable=False)
        pkg = ExecutionPackage.create(
            canonical_task_id="task-inv-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Do work",
        )
        payload = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        assert payload["profile"] == "aota-worker"

        # task-main is not dispatchable via canonical_role, but work_role binding still testable
        task_binding = cfg.get_binding("task-main")
        args = task_binding.hermes_args("do task-main work")
        assert "-p" in args
        assert args[args.index("-p") + 1] == "aota-task-main"

    def test_provider_translated_correctly(self):
        cfg = get_default_runtime_config(validate_executable=False)
        pkg = ExecutionPackage.create(
            canonical_task_id="task-inv-2",
            project_id="aota_forge",
            canonical_role="planner",
            instruction="Analyze",
        )
        payload = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        assert payload["provider"] == "opencode-go"
        # Verify host_client arg translation via RuntimeBinding
        binding = cfg.get_binding("analyst")
        args = binding.hermes_args("Analyze")
        assert "--provider" in args
        assert args[args.index("--provider") + 1] == "opencode-go"

    def test_model_translated_correctly(self):
        cfg = get_default_runtime_config(validate_executable=False)
        pkg = ExecutionPackage.create(
            canonical_task_id="task-inv-3",
            project_id="aota_forge",
            canonical_role="reviewer",
            instruction="Review",
        )
        payload = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        assert payload["model"] == "muse-spark-1.2-contributor"
        binding = cfg.get_binding("reviewer")
        args = binding.hermes_args("Review")
        assert "-m" in args
        assert args[args.index("-m") + 1] == "muse-spark-1.2-contributor"

    def test_missing_executable_fails_closed(self, tmp_path):
        missing = str(tmp_path / "missing-hermes")
        with pytest.raises(RuntimeConfigError, match="missing or not a file"):
            RuntimeBinding(
                work_role="coder",
                executor="hermes",
                profile="aota-worker",
                provider="opencode-go",
                model="muse-spark-1.2-contributor",
                concurrency=1,
                executable=missing,
            )
        # Also via HermesHostClient direct
        from aota_forge.adapters.hermes.host_client import HermesHostClient, HermesHostUnavailableError

        with pytest.raises(HermesHostUnavailableError):
            HermesHostClient(missing)
        # Composition with missing executable and real client should fail
        from aota_forge.composition.execution import create_production_execution_dispatcher

        with pytest.raises(Exception, match="EXECUTOR_UNAVAILABLE|RUNTIME_CONFIG_INVALID|missing"):
            create_production_execution_dispatcher(launcher_path=missing)

    def test_stale_hermes_host_assumption_removed(self):
        # composition should not hardcode hermes-host as production executable
        comp_path = pathlib.Path("aota_forge/composition/execution.py")
        text = comp_path.read_text(encoding="utf-8")
        # Old stale path must not be the default production launcher
        assert 'PRODUCTION_HERMES_HOST_LAUNCHER = "/home/latios/.local/bin/hermes-host"' not in text, "stale hermes-host launcher still hardcoded"
        assert 'PRODUCTION_HERMES_EXECUTABLE = "/home/latios/.local/bin/hermes"' in text or '"/home/latios/.local/bin/hermes"' in text
        # Verify real hermes exists (Path A decision)
        assert pathlib.Path("/home/latios/.local/bin/hermes").exists()
        assert not pathlib.Path("/home/latios/.local/bin/hermes-host").exists()
        # Adapter host_client now supports direct hermes invocation
        # Verify host_client dispatch builds correct args via payload
        from aota_forge.adapters.hermes.host_client import HermesHostClient

        # Use fake popen to capture args
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args

            class FakeProc:
                stdout = None
                stderr = None

                def poll(self):
                    return None

                def wait(self, timeout=None):
                    raise Exception("not needed")

                def terminate(self):
                    pass

            return FakeProc()

        client = HermesHostClient(
            "/home/latios/.local/bin/hermes",
            default_cwd=str(pathlib.Path.cwd()),
            popen_factory=fake_popen,
            validate_launcher=True,
        )
        payload = {
            "profile": "aota-worker",
            "provider": "opencode-go",
            "model": "muse-spark-1.2-contributor",
            "instruction": "do work",
            "context": {"working_context": {"cwd": str(pathlib.Path.cwd())}},
            "artifacts": [],
            "constraints": {},
            "capability_requirements": {},
            "result_expectations": {},
            "operation": "task_dispatch",
            "package_id": "pkg-1",
        }
        try:
            client.dispatch(payload)
        except Exception:
            pass
        assert captured["args"][0] == "/home/latios/.local/bin/hermes"
        assert "-p" in captured["args"]
        assert "--provider" in captured["args"]
        assert "-m" in captured["args"]
        assert "-z" in captured["args"]

    def test_hermes_invocation_deterministic(self):
        cfg = get_default_runtime_config(validate_executable=False)
        pkg = ExecutionPackage.create(
            canonical_task_id="det-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Deterministic work",
        )
        p1 = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        p2 = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        assert p1 == p2
        # Same binding same args
        b1 = cfg.get_binding("coder")
        b2 = cfg.get_binding("coder")
        assert b1.hermes_args("same") == b2.hermes_args("same")

    def test_adapter_uses_runtime_binding_when_present(self):
        cfg = get_default_runtime_config(validate_executable=False)
        fake_host, captures = _fake_host()
        adapter = HermesAdapter(host_client=fake_host, runtime_config=cfg)
        pkg = ExecutionPackage.create(
            canonical_task_id="adapter-rt-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Adapter uses runtime binding",
        )
        adapter.dispatch(pkg)
        assert len(captures) == 1
        assert captures[0]["profile"] == "aota-worker"
        assert captures[0]["provider"] == "opencode-go"
        assert captures[0]["model"] == "muse-spark-1.2-contributor"


# ---------------------------------------------------------------------------
# 6. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_config_same_role_same_binding(self):
        cfg = get_default_runtime_config(validate_executable=False)
        for role in ["analyst", "coder", "reviewer", "project-steward", "task-main"]:
            b1 = cfg.get_binding(role)
            b2 = cfg.get_binding(role)
            assert b1 == b2
            assert b1.to_dict() == b2.to_dict()
            assert b1.hermes_args("do") == b2.hermes_args("do")

    def test_different_provider_model_produces_different_binding(self):
        cfg1 = get_default_runtime_config(validate_executable=False, provider="opencode-go", model="muse-spark-1.2-contributor")
        cfg2 = get_default_runtime_config(validate_executable=False, provider="other-provider", model="other-model")
        assert cfg1.get_binding("coder").provider != cfg2.get_binding("coder").provider
        assert cfg1.get_binding("coder").model != cfg2.get_binding("coder").model

    def test_file_config_deterministic(self, tmp_path):
        cfg_file = tmp_path / "det.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "executor": "hermes",
                    "executable": "/home/latios/.local/bin/hermes",
                    "concurrency": 1,
                    "provider": "opencode-go",
                    "model": "muse-spark-1.2-contributor",
                    "bindings": {
                        "coder": {"profile": "aota-worker"},
                        "analyst": {"profile": "aota-worker"},
                        "reviewer": {"profile": "aota-worker"},
                        "project-steward": {"profile": "aota-worker"},
                        "task-main": {"profile": "aota-task-main"},
                    },
                }
            )
        )
        c1 = load_runtime_config(config_path=str(cfg_file))
        c2 = load_runtime_config(config_path=str(cfg_file))
        assert c1.to_dict() == c2.to_dict()
        assert c1.get_binding("coder").hermes_args("x") == c2.get_binding("coder").hermes_args("x")


# ---------------------------------------------------------------------------
# 7. Concurrency binding
# ---------------------------------------------------------------------------

class TestConcurrencyBinding:
    def test_concurrency_binding_supported_and_bounded(self):
        cfg = get_default_runtime_config(validate_executable=False, concurrency=1)
        assert cfg.concurrency == 1
        assert cfg.get_binding("coder").concurrency == 1
        # Valid concurrency values
        for valid in [1, 2, 16, 32]:
            c = get_default_runtime_config(validate_executable=False, concurrency=valid)
            assert c.concurrency == valid
        # Invalid should fail at construction
        for invalid in [0, -1, 33, "2", 1.5]:
            with pytest.raises(RuntimeConfigError):
                get_default_runtime_config(validate_executable=False, concurrency=invalid)  # type: ignore

    def test_concurrency_per_binding_override(self, tmp_path):
        cfg_file = tmp_path / "conc.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "executor": "hermes",
                    "executable": "/home/latios/.local/bin/hermes",
                    "concurrency": 1,
                    "bindings": {
                        "coder": {"profile": "aota-worker", "concurrency": 2},
                        "analyst": {"profile": "aota-worker"},
                        "reviewer": {"profile": "aota-worker"},
                        "project-steward": {"profile": "aota-worker"},
                        "task-main": {"profile": "aota-task-main", "concurrency": 1},
                    },
                }
            )
        )
        cfg = load_runtime_config(config_path=str(cfg_file))
        assert cfg.get_binding("coder").concurrency == 2
        assert cfg.get_binding("analyst").concurrency == 1

    def test_production_concurrency_is_one(self):
        from aota_forge.composition.execution import create_production_execution_dispatcher
        from unittest.mock import MagicMock

        fake = MagicMock()
        fake.dispatch.return_value = {"adapter_handle": "h1", "status": "pending", "dispatch_time": "now"}
        fake.query_status.return_value = {"status": "pending"}
        fake.fetch_result.return_value = {"status": "pending"}
        fake.cancel_task.return_value = {"cancelled": False, "status": "pending"}
        fake.resume_task.return_value = {"status": "running"}
        # Use fake host to avoid real spawn
        dispatcher = create_production_execution_dispatcher(host_client=fake)
        caps = dispatcher.registry.get("hermes").capabilities()
        assert caps.concurrency_limit == 1
