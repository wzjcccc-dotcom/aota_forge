"""M1/W1 (W4-repaired) — Runtime Configuration & Hermes Invocation Binding.

Authority repair (F-M1-RV1-01 / F-M1-RV1-02):

* RUNTIME_CONFIG_AUTHORITY=operator_owned: production runtime binding must
  originate from the explicit trusted operator channel; a missing operator
  config fails closed.
* No source-owned provider/model/profile/executable deployment fallback:
  DEFAULT_* names and get_default_runtime_config() do not exist; production
  modules never contain vendor deployment strings.
* No unittest.mock/test bypass inside production runtime config: validation
  is unconditional; offline tests build explicit configs over bounded
  temporary executable fixtures.
* Mechanical Worker restriction: every Worker binding pins the shared AOTA MCP
  toolset allowlist, translated into the Hermes invocation (`-t aota`).

Determinism and authority separation (TaskHandoff/Core) remain covered.
"""

from __future__ import annotations

import ast
import json
import pathlib
import stat
from typing import Any

import pytest

from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    canonical_to_hermes_payload,
)
from aota_forge.core.execution import ExecutionPackage
from aota_forge.runtime.config import (
    RUNTIME_CONFIG_ENV,
    SHARED_MCP_TOOLSET,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
    resolve_binding_for_canonical_role,
    resolve_binding_for_work_role,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.mapping import WorkRoleMappingError, resolve_work_role_to_canonical_role
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.core.execution.roles import CanonicalRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_executable(tmp_path: pathlib.Path) -> str:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _bindings(profile_by_role: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return profile_by_role


def _worker_binding(role: str) -> dict[str, Any]:
    return {"profile": "aota-worker", "toolsets": ["aota"]}


def _complete_bindings() -> dict[str, Any]:
    return {
        role: _worker_binding(role)
        for role in ("analyst", "coder", "reviewer", "project-steward")
    } | {"task-main": {"profile": "aota-task-main"}}


def _config_doc(tmp_path: pathlib.Path, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "executor": "hermes",
        "executable": _stub_executable(tmp_path),
        "concurrency": 1,
        "provider": "aota-test-provider",
        "model": "aota-test-model",
        "bindings": _complete_bindings(),
    }
    doc.update(overrides)
    return doc


def _write_config(tmp_path: pathlib.Path, doc: dict[str, Any], name: str = "runtime.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def _fake_host(payload_captures: list[dict[str, Any]] | None = None):
    captures = payload_captures if payload_captures is not None else []

    class FakeHost:
        def dispatch(self, payload):
            captures.append(dict(payload))
            return {"adapter_handle": "hermes-test-fake1", "status": "pending", "dispatch_time": "2026-09-06T00:00:00Z"}

        def query_status(self, handle):
            return {"status": "running"}

        def fetch_result(self, handle):
            return {"status": "pending"}

        def cancel_task(self, handle):
            return {"cancelled": True, "status": "cancelled"}

        def resume_task(self, handle, payload):
            return {"status": "running"}

    return FakeHost(), captures


def _loaded_config(tmp_path: pathlib.Path, **overrides: Any) -> RuntimeConfig:
    path = _write_config(tmp_path, _config_doc(tmp_path, **overrides))
    return load_runtime_config(config_path=path)


# ---------------------------------------------------------------------------
# 1. Operator authority: fail closed on missing/invalid config
# ---------------------------------------------------------------------------

class TestOperatorAuthorityFailClosed:
    def test_missing_operator_config_fails_closed(self, monkeypatch):
        # No env channel, no explicit path, no injection: deterministic fail closed.
        monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config()

    def test_empty_environ_fails_closed(self):
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config(environ={})

    def test_blank_env_value_fails_closed(self, tmp_path):
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config(environ={RUNTIME_CONFIG_ENV: "   "})

    def test_env_channel_points_to_missing_file_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(tmp_path / "absent.json"))
        with pytest.raises(RuntimeConfigError, match="missing or not a file"):
            load_runtime_config()

    def test_symlinked_config_rejected(self, tmp_path):
        real = tmp_path / "real.json"
        real.write_text(json.dumps(_config_doc(tmp_path)), encoding="utf-8")
        link = tmp_path / "link.json"
        link.symlink_to(real)
        with pytest.raises(RuntimeConfigError, match="symlink"):
            load_runtime_config(config_path=str(link))

    def test_explicit_valid_operator_config_succeeds(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        assert cfg.executor == "hermes"
        assert cfg.get_binding("coder").profile == "aota-worker"
        assert cfg.get_binding("task-main").profile == "aota-task-main"
        assert cfg.provider == "aota-test-provider"
        assert cfg.model == "aota-test-model"

    def test_incomplete_config_missing_fields_fails_closed(self, tmp_path):
        for missing in ("executor", "executable", "provider", "model", "bindings"):
            doc = _config_doc(tmp_path)
            del doc[missing]
            path = _write_config(tmp_path, doc, name=f"missing_{missing}.json")
            with pytest.raises(RuntimeConfigError, match="missing required fields"):
                load_runtime_config(config_path=path)

    def test_incomplete_bindings_missing_role_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        del doc["bindings"]["reviewer"]
        path = _write_config(tmp_path, doc, name="partial_roles.json")
        with pytest.raises(RuntimeConfigError, match="incomplete"):
            load_runtime_config(config_path=path)

    def test_unknown_top_level_key_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["unknownKey"] = "foo"
        path = _write_config(tmp_path, doc, name="unknown_key.json")
        with pytest.raises(RuntimeConfigError, match="unknown runtime config keys"):
            load_runtime_config(config_path=path)

    def test_unknown_binding_key_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["bindings"]["coder"]["maxTurns"] = 5
        path = _write_config(tmp_path, doc, name="unknown_binding_key.json")
        with pytest.raises(RuntimeConfigError, match="unknown keys in binding"):
            load_runtime_config(config_path=path)

    def test_unknown_work_role_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["bindings"]["super-coder"] = {"profile": "aota-worker", "toolsets": ["aota"]}
        path = _write_config(tmp_path, doc, name="unknown_role.json")
        with pytest.raises(RuntimeConfigError, match="unknown work role"):
            load_runtime_config(config_path=path)

        cfg = _loaded_config(tmp_path)
        with pytest.raises(RuntimeConfigError, match="unknown work role"):
            cfg.get_binding("unknown-role")
        with pytest.raises(RuntimeConfigError):
            cfg.get_binding(123)  # type: ignore[arg-type]
        with pytest.raises(RuntimeConfigError):
            cfg.get_binding(None)  # type: ignore[arg-type]

    def test_invalid_executor_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["executor"] = "unknown_exec"
        path = _write_config(tmp_path, doc, name="bad_executor.json")
        with pytest.raises(RuntimeConfigError, match="unknown executor"):
            load_runtime_config(config_path=path)

    def test_invalid_concurrency_fails_closed(self, tmp_path):
        for bad in (0, -1, 99, "1", 1.5, None):
            doc = _config_doc(tmp_path)
            doc["concurrency"] = bad
            path = _write_config(tmp_path, doc, name=f"bad_conc_{str(bad).replace('/', '_')}.json")
            with pytest.raises(RuntimeConfigError):
                load_runtime_config(config_path=path)

    def test_missing_binding_profile_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["bindings"]["coder"] = {}
        path = _write_config(tmp_path, doc, name="missing_profile.json")
        with pytest.raises(RuntimeConfigError, match="missing required field 'profile'"):
            load_runtime_config(config_path=path)

    def test_missing_executable_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["executable"] = str(tmp_path / "nonexistent-hermes")
        path = _write_config(tmp_path, doc, name="missing_exe.json")
        with pytest.raises(RuntimeConfigError, match="missing or not a file"):
            load_runtime_config(config_path=path)

        missing = str(tmp_path / "also-absent")
        with pytest.raises(RuntimeConfigError, match="missing or not a file"):
            RuntimeBinding(
                work_role="coder",
                executor="hermes",
                profile="aota-worker",
                provider=None,
                model=None,
                concurrency=1,
                executable=missing,
                toolsets=("aota",),
            )

    def test_non_executable_file_rejected(self, tmp_path):
        doc = _config_doc(tmp_path)
        not_exe = tmp_path / "plain-file"
        not_exe.write_text("data", encoding="utf-8")
        doc["executable"] = str(not_exe)
        path = _write_config(tmp_path, doc, name="not_exe.json")
        with pytest.raises(RuntimeConfigError, match="not executable"):
            load_runtime_config(config_path=path)


# ---------------------------------------------------------------------------
# 2. No source-owned deployment fallback / no test bypass in production config
# ---------------------------------------------------------------------------

class TestNoSourceOwnedFallback:
    def test_default_config_helpers_do_not_exist(self):
        import aota_forge.runtime.config as config_module

        for retired in (
            "get_default_runtime_config",
            "DEFAULT_PROVIDER",
            "DEFAULT_MODEL",
            "DEFAULT_HERMES_EXECUTABLE",
            "LEGACY_HERMES_HOST",
        ):
            assert not hasattr(config_module, retired), f"source-owned deployment fallback {retired} still present"

    def test_production_modules_contain_no_vendor_deployment_strings(self):
        # Provider/model/executable deployment pins belong to operator config,
        # never to production source.
        for rel in (
            "aota_forge/runtime/config.py",
            "aota_forge/composition/execution.py",
            "aota_forge/composition/worker_vertical_slice.py",
            "aota_forge/adapters/hermes/executor.py",
            "aota_forge/adapters/hermes/host_client.py",
        ):
            text = pathlib.Path(rel).read_text(encoding="utf-8")
            lowered = text.lower()
            for vendor in ("opencode-go", "muse-spark", "deepseek", "/home/latios"):
                assert vendor not in lowered, f"{rel} carries source-owned deployment string {vendor!r}"

    def test_no_unittest_mock_inside_production_runtime_config(self):
        # F-M1-RV1-02: production runtime config must never monkey-patch its
        # own validation. Tests use explicit configs + temporary executables.
        rel = pathlib.Path("aota_forge/runtime/config.py")
        tree = ast.parse(rel.read_text(encoding="utf-8"), filename=str(rel))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert "unittest" not in name.lower(), f"production runtime config imports {name}"

    def test_no_stale_hermes_host_rejection_comment(self):
        # F-M1-RV1-02: stale comment claiming legacy hermes-host rejection was
        # removed together with the code it described.
        rel = pathlib.Path("aota_forge/runtime/config.py")
        text = rel.read_text(encoding="utf-8")
        assert "hermes-host" not in text

    def test_explicit_null_provider_model_defers_to_profile_not_source(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["provider"] = None
        doc["model"] = None
        path = _write_config(tmp_path, doc, name="null_pins.json")
        cfg = load_runtime_config(config_path=path)
        assert cfg.provider is None
        assert cfg.model is None
        assert cfg.get_binding("coder").provider is None
        assert cfg.get_binding("coder").model is None


# ---------------------------------------------------------------------------
# 3. Worker binding + mechanical MCP toolset restriction
# ---------------------------------------------------------------------------

class TestWorkerBindingAndRestriction:
    @pytest.mark.parametrize("role", ["analyst", "coder", "reviewer", "project-steward"])
    def test_worker_roles_share_profile_and_pin_mcp_toolset(self, tmp_path, role):
        cfg = _loaded_config(tmp_path)
        binding = cfg.get_binding(role)
        assert binding.profile == SHARED_WORKER_PROFILE == "aota-worker"
        assert binding.toolsets == (SHARED_MCP_TOOLSET,) == ("aota",)
        canonical = resolve_work_role_to_canonical_role(role)
        via_canonical = resolve_binding_for_canonical_role(canonical.value, cfg)
        assert via_canonical == binding

    def test_worker_binding_without_toolset_pin_fails_closed(self, tmp_path):
        doc = _config_doc(tmp_path)
        del doc["bindings"]["coder"]["toolsets"]
        path = _write_config(tmp_path, doc, name="unpinned_worker.json")
        with pytest.raises(RuntimeConfigError, match="must pin toolsets"):
            load_runtime_config(config_path=path)

    def test_worker_binding_with_extra_or_native_toolsets_fails_closed(self, tmp_path):
        for bad in (["aota", "terminal"], ["terminal"], ["aota", "file"], []):
            doc = _config_doc(tmp_path)
            doc["bindings"]["coder"]["toolsets"] = bad
            path = _write_config(tmp_path, doc, name=f"bad_toolsets_{abs(hash(str(bad)))}.json")
            with pytest.raises(RuntimeConfigError, match="toolsets"):
                load_runtime_config(config_path=path)

    def test_task_main_profile_binding_independent(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        task_binding = cfg.get_binding("task-main")
        coder_binding = cfg.get_binding("coder")
        assert task_binding.profile == TASK_MAIN_PROFILE == "aota-task-main"
        assert task_binding.profile != coder_binding.profile
        assert task_binding.executor == coder_binding.executor == "hermes"
        # task-main is not a Worker CanonicalRole and never dispatches as one
        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role("task-main")
        assert not hasattr(CanonicalRole, "TASK_MAIN")
        assert "task-main" not in {r.value for r in CanonicalRole}

    def test_no_per_role_worker_profiles(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        profiles = {b.profile for b in cfg.bindings}
        assert profiles == {"aota-worker", "aota-task-main"}
        for forbidden in ("aota-analyst", "aota-coder", "aota-reviewer", "aota-steward"):
            assert forbidden not in profiles

    def test_hermes_args_carry_mechanical_toolset_restriction(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        args = cfg.get_binding("coder").hermes_args("do bounded work")
        assert args[0] == cfg.executable
        assert args[:3] == [cfg.executable, "-p", "aota-worker"]
        t_index = args.index("-t")
        assert args[t_index + 1] == "aota"
        # instruction travels as exactly one final argv element
        assert args[-2] == "-z"
        assert args[-1] == "do bounded work"

    def test_resolve_helpers_require_explicit_config(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        assert resolve_binding_for_work_role("coder", cfg).profile == "aota-worker"
        with pytest.raises(RuntimeConfigError):
            resolve_binding_for_work_role("coder", None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeConfigError):
            resolve_binding_for_canonical_role("coder", None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeConfigError, match="no work role mapping"):
            resolve_binding_for_canonical_role("executor", cfg)
        with pytest.raises(RuntimeConfigError, match="no work role mapping"):
            resolve_binding_for_canonical_role("task-main", cfg)


# ---------------------------------------------------------------------------
# 4. Authority separation (unchanged contracts)
# ---------------------------------------------------------------------------

class TestAuthoritySeparation:
    def test_task_handoff_has_no_deployment_authority(self):
        base_kwargs = dict(
            work_role="coder",
            task_kind="implementation",
            objective="Implement feature X",
            bounded_scope="bounded scope",
            validation_expectations=("tests pass",),
            semantic_stop_expectations=("ambiguous",),
        )
        for injected in ("provider", "model", "profile"):
            with pytest.raises(ValueError, match="Unknown field|Forbidden"):
                TaskHandoff.from_dict(
                    {
                        **base_kwargs,
                        "work_role": "coder",
                        "task_kind": "implementation",
                        "objective": "x",
                        "bounded_scope": "b",
                        "validation_expectations": [],
                        "semantic_stop_expectations": [],
                        injected: "operator-value",
                    }
                )
        from aota_forge.work_plane.handoff import ALL_HANDOFF_FIELDS, FORBIDDEN_MECHANICAL_FIELDS

        assert "provider" not in ALL_HANDOFF_FIELDS
        assert "model" not in ALL_HANDOFF_FIELDS
        assert "profile" not in ALL_HANDOFF_FIELDS
        assert isinstance(FORBIDDEN_MECHANICAL_FIELDS, (frozenset, set))

    def test_core_remains_hermes_profile_neutral(self):
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
            assert "aota-worker" not in text
            assert "aota-task-main" not in text

    def test_runtime_binding_is_not_canonical_role_mapping(self, tmp_path):
        for role, expected_canonical in [
            ("analyst", "planner"),
            ("coder", "coder"),
            ("reviewer", "reviewer"),
            ("project-steward", "steward"),
        ]:
            canonical = resolve_work_role_to_canonical_role(role)
            assert canonical.value == expected_canonical
            assert canonical.value != "aota-worker"
            assert canonical.value != "aota-task-main"


# ---------------------------------------------------------------------------
# 5. Hermes invocation translation
# ---------------------------------------------------------------------------

class TestHermesInvocation:
    def test_profile_provider_model_toolset_translation(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        for canonical_role, work_role in (
            ("coder", "coder"),
            ("planner", "analyst"),
            ("reviewer", "reviewer"),
            ("steward", "project-steward"),
        ):
            pkg = ExecutionPackage.create(
                canonical_task_id=f"task-inv-{canonical_role}",
                project_id="aota_forge",
                canonical_role=canonical_role,
                instruction="Do work",
            )
            payload = canonical_to_hermes_payload(pkg, runtime_config=cfg)
            binding = cfg.get_binding(work_role)
            assert payload["profile"] == "aota-worker"
            assert payload["provider"] == binding.provider
            assert payload["model"] == binding.model
            assert payload["toolsets"] == ["aota"]
            args = binding.hermes_args(payload["instruction"])
            assert "--provider" in args and args[args.index("--provider") + 1] == binding.provider
            assert "-m" in args and args[args.index("-m") + 1] == binding.model
            assert "-t" in args and args[args.index("-t") + 1] == "aota"

    def test_adapter_uses_operator_binding_only(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        fake_host, captures = _fake_host()
        adapter = HermesAdapter(host_client=fake_host, runtime_config=cfg)
        pkg = ExecutionPackage.create(
            canonical_task_id="adapter-rt-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Adapter uses operator binding",
        )
        adapter.dispatch(pkg)
        assert len(captures) == 1
        assert captures[0]["profile"] == "aota-worker"
        assert captures[0]["provider"] == "aota-test-provider"
        assert captures[0]["model"] == "aota-test-model"
        assert captures[0]["toolsets"] == ["aota"]

    def test_deterministic_same_config_same_binding(self, tmp_path):
        cfg = _loaded_config(tmp_path)
        pkg = ExecutionPackage.create(
            canonical_task_id="det-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Deterministic work",
        )
        p1 = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        p2 = canonical_to_hermes_payload(pkg, runtime_config=cfg)
        assert p1 == p2
        b1 = cfg.get_binding("coder")
        b2 = cfg.get_binding("coder")
        assert b1 == b2
        assert b1.to_dict() == b2.to_dict()
        assert b1.hermes_args("do") == b2.hermes_args("do")

    def test_file_config_deterministic(self, tmp_path):
        path = _write_config(tmp_path, _config_doc(tmp_path))
        c1 = load_runtime_config(config_path=path)
        c2 = load_runtime_config(config_path=path)
        assert c1.to_dict() == c2.to_dict()
        assert c1.get_binding("coder").hermes_args("x") == c2.get_binding("coder").hermes_args("x")


# ---------------------------------------------------------------------------
# 6. Composition authority boundary
# ---------------------------------------------------------------------------

class TestCompositionAuthority:
    def test_production_composition_without_operator_config_fails_closed(self, monkeypatch):
        monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
        from aota_forge.composition.execution import create_production_execution_dispatcher

        with pytest.raises(RuntimeConfigError):
            create_production_execution_dispatcher()

    def test_production_composition_missing_config_fails_closed_even_with_fake_host(self, monkeypatch):
        # Injecting a host client is a dependency seam, not an operator config
        # channel: it must not smuggle a source-owned default RuntimeConfig in.
        monkeypatch.delenv(RUNTIME_CONFIG_ENV, raising=False)
        from aota_forge.composition.execution import create_production_execution_dispatcher

        fake_host, _captures = _fake_host()
        with pytest.raises(RuntimeConfigError):
            create_production_execution_dispatcher(host_client=fake_host)

    def test_production_composition_with_explicit_operator_config_succeeds(self, tmp_path):
        from aota_forge.composition.execution import create_production_execution_dispatcher

        cfg = _loaded_config(tmp_path)
        fake_host, captures = _fake_host()
        dispatcher = create_production_execution_dispatcher(host_client=fake_host, runtime_config=cfg)
        caps = dispatcher.registry.get("hermes").capabilities()
        assert set(caps.supported_canonical_roles) == {"planner", "coder", "reviewer", "steward"}
        assert caps.concurrency_limit == 1
        pkg = ExecutionPackage.create(
            canonical_task_id="comp-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="bounded",
        )
        dispatcher.dispatch(pkg)
        assert captures[0]["profile"] == "aota-worker"
        assert captures[0]["toolsets"] == ["aota"]

    def test_production_composition_rejects_invalid_injection(self):
        from aota_forge.composition.execution import create_production_execution_dispatcher

        with pytest.raises(RuntimeConfigError):
            create_production_execution_dispatcher(runtime_config={"ad hoc": "dict"})

    def test_production_concurrency_is_one(self):
        from aota_forge.composition.execution import create_production_execution_dispatcher
        from unittest.mock import MagicMock

        fake = MagicMock()
        fake.dispatch.return_value = {"adapter_handle": "h1", "status": "pending", "dispatch_time": "now"}
        dispatcher = create_production_execution_dispatcher(host_client=fake)
        caps = dispatcher.registry.get("hermes").capabilities()
        assert caps.concurrency_limit == 1

    def test_environment_channel_supplies_config(self, tmp_path, monkeypatch):
        from aota_forge.composition.execution import create_production_execution_dispatcher

        path = _write_config(tmp_path, _config_doc(tmp_path), name="env.json")
        monkeypatch.setenv(RUNTIME_CONFIG_ENV, path)
        fake_host, captures = _fake_host()
        dispatcher = create_production_execution_dispatcher(host_client=fake_host)
        pkg = ExecutionPackage.create(
            canonical_task_id="env-task",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="via env channel",
        )
        dispatcher.dispatch(pkg)
        assert captures[0]["profile"] == "aota-worker"
        assert captures[0]["provider"] == "aota-test-provider"

    def test_concurrency_override_binding_supported(self, tmp_path):
        doc = _config_doc(tmp_path)
        doc["concurrency"] = 2
        doc["bindings"]["coder"]["concurrency"] = 3
        path = _write_config(tmp_path, doc, name="conc.json")
        cfg = load_runtime_config(config_path=path)
        assert cfg.concurrency == 2
        assert cfg.get_binding("coder").concurrency == 3
        assert cfg.get_binding("analyst").concurrency == 2
