"""AF #56 M1/W3 — bounded RuntimeConfig host-selection contract.

Scope (M1): configuration authority only. ``executor`` is exactly one of
``hermes`` or ``opencode`` in the ONE operator-owned RuntimeConfig authority.
Hermes keeps its exact previous semantics; OpenCode is representable as a
dedicated persistent local HTTP/SSE host with an operator-owned loopback
``host_endpoint``. No OpenCode execution adapter exists in M1: production
execution construction must fail closed for ``executor=opencode`` and must
never silently fall back to Hermes.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import stat
from typing import Any

import pytest

from aota_forge.composition.execution import (
    create_hermes_completion_delivery_transport,
    create_production_execution_dispatcher,
)
from aota_forge.runtime.config import (
    EXECUTOR_HERMES,
    EXECUTOR_OPENCODE,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
)
from aota_forge.work_plane.handoff import TaskHandoff

WORKER_ROLES = ("analyst", "coder", "reviewer", "project-steward")


def _stub_executable(tmp_path: pathlib.Path) -> str:
    exe = tmp_path / "host-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _hermes_bindings() -> dict[str, Any]:
    doc = {role: {"profile": SHARED_WORKER_PROFILE, "toolsets": ["aota"]} for role in WORKER_ROLES}
    doc["task-main"] = {"profile": TASK_MAIN_PROFILE}
    return doc


def _opencode_bindings() -> dict[str, Any]:
    doc = {role: {"profile": SHARED_WORKER_PROFILE} for role in WORKER_ROLES}
    doc["task-main"] = {"profile": TASK_MAIN_PROFILE}
    return doc


def _config_doc(tmp_path: pathlib.Path, executor: str = EXECUTOR_HERMES, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "executor": executor,
        "executable": _stub_executable(tmp_path),
        "concurrency": 1,
        "provider": "aota-test-provider",
        "model": "aota-test-model",
        "bindings": _hermes_bindings() if executor == EXECUTOR_HERMES else _opencode_bindings(),
    }
    if executor == EXECUTOR_OPENCODE:
        doc["host_endpoint"] = "http://127.0.0.1:4096"
    doc.update(overrides)
    return doc


def _load(tmp_path: pathlib.Path, doc: dict[str, Any]) -> RuntimeConfig:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return load_runtime_config(config_path=str(path))


class _FakeHost:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, payload):
        self.calls += 1
        return {"adapter_handle": "fake1", "status": "pending", "dispatch_time": "2026-09-15T00:00:00Z"}

    def query_status(self, handle):
        return {"status": "running"}

    def fetch_result(self, handle):
        return {"status": "pending"}

    def cancel_task(self, handle):
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, handle, payload):
        return {"status": "running"}


# ---------------------------------------------------------------------------
# 1. Hermes backward compatibility
# ---------------------------------------------------------------------------

class TestHermesBackwardCompatibility:
    def test_hermes_config_still_valid(self, tmp_path):
        config = _load(tmp_path, _config_doc(tmp_path, EXECUTOR_HERMES))
        assert config.executor == "hermes"
        assert config.host_endpoint is None
        assert config.get_binding("coder").toolsets == ("aota",)
        binding = config.get_binding("task-main")
        args = binding.hermes_args("do the thing")
        assert args[0] == config.executable
        assert "-p" in args and "aota-task-main" in args

    def test_hermes_production_dispatcher_still_constructs(self, tmp_path):
        config = _load(tmp_path, _config_doc(tmp_path, EXECUTOR_HERMES))
        host = _FakeHost()
        dispatcher = create_production_execution_dispatcher(runtime_config=config, host_client=host)
        assert dispatcher is not None
        assert host.calls == 0

    def test_hermes_only_worker_profile_invariant_preserved(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_HERMES)
        doc["bindings"]["coder"]["profile"] = "some-other-profile"
        with pytest.raises(RuntimeConfigError, match="must map to shared profile"):
            _load(tmp_path, doc)


# ---------------------------------------------------------------------------
# 2. OpenCode representable only under the bounded operator contract
# ---------------------------------------------------------------------------

class TestOpenCodeRepresentable:
    def test_opencode_config_loads_with_operator_endpoint(self, tmp_path):
        config = _load(tmp_path, _config_doc(tmp_path, EXECUTOR_OPENCODE))
        assert config.executor == EXECUTOR_OPENCODE
        assert config.host_endpoint == "http://127.0.0.1:4096"
        assert config.to_dict()["host_endpoint"] == "http://127.0.0.1:4096"
        assert config.get_binding("coder").toolsets is None

    def test_opencode_mechanical_profile_binding_allowed(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE)
        doc["bindings"]["coder"]["profile"] = "aota-opencode-coder"
        config = _load(tmp_path, doc)
        assert config.get_binding("coder").profile == "aota-opencode-coder"

    def test_host_endpoint_normalized(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE, host_endpoint="http://localhost:4096/")
        config = _load(tmp_path, doc)
        assert config.host_endpoint == "http://localhost:4096"

    def test_unknown_executor_rejected(self, tmp_path):
        doc = _config_doc(tmp_path, "codex")
        with pytest.raises(RuntimeConfigError, match="unknown executor"):
            _load(tmp_path, doc)

    def test_missing_endpoint_rejected_at_load(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE)
        del doc["host_endpoint"]
        with pytest.raises(RuntimeConfigError, match="requires the operator-owned 'host_endpoint'"):
            _load(tmp_path, doc)

    def test_missing_endpoint_rejected_at_construction(self, tmp_path):
        with pytest.raises(RuntimeConfigError, match="requires an operator-owned host_endpoint"):
            RuntimeConfig(
                executor=EXECUTOR_OPENCODE,
                executable=_stub_executable(tmp_path),
                concurrency=1,
                provider="p",
                model="m",
                bindings=tuple(
                    RuntimeBinding(
                        work_role=role,
                        executor=EXECUTOR_OPENCODE,
                        profile=SHARED_WORKER_PROFILE,
                        provider=None,
                        model=None,
                        concurrency=1,
                        executable=_stub_executable(tmp_path),
                        toolsets=None,
                    )
                    for role in WORKER_ROLES + ("task-main",)
                ),
            )

    @pytest.mark.parametrize(
        "endpoint",
        [
            "127.0.0.1:4096",
            "ftp://127.0.0.1:4096",
            "http://127.0.0.1",
            "http://0.0.0.0:4096",
            "http://example.com:4096",
            "http://user:pass@127.0.0.1:4096",
            "http://127.0.0.1:4096/api",
            "http://127.0.0.1:4096?x=1",
            "http://127.0.0.1:99999",
            "http:// 127.0.0.1:4096",
            "",
        ],
    )
    def test_malformed_endpoint_rejected(self, tmp_path, endpoint):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE, host_endpoint=endpoint)
        with pytest.raises(RuntimeConfigError):
            _load(tmp_path, doc)

    def test_non_string_endpoint_rejected(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE, host_endpoint=4096)
        with pytest.raises(RuntimeConfigError, match="host_endpoint string"):
            _load(tmp_path, doc)


# ---------------------------------------------------------------------------
# 3. No cross-meaning between host-specific fields
# ---------------------------------------------------------------------------

class TestNoCrossMeaning:
    def test_hermes_rejects_opencode_only_field(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_HERMES, host_endpoint="http://127.0.0.1:4096")
        with pytest.raises(RuntimeConfigError, match="opencode-only"):
            _load(tmp_path, doc)

    def test_opencode_rejects_hermes_only_toolsets(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE)
        doc["bindings"]["coder"]["toolsets"] = ["aota"]
        with pytest.raises(RuntimeConfigError, match="hermes-only"):
            _load(tmp_path, doc)

    def test_opencode_top_level_toolsets_rejected(self, tmp_path):
        doc = _config_doc(tmp_path, EXECUTOR_OPENCODE)
        doc["toolsets"] = ["aota"]
        doc["bindings"]["coder"]["toolsets"] = ["aota"]
        with pytest.raises(RuntimeConfigError, match="hermes-only"):
            _load(tmp_path, doc)

    def test_hermes_args_fails_closed_for_opencode_binding(self, tmp_path):
        binding = RuntimeBinding(
            work_role="coder",
            executor=EXECUTOR_OPENCODE,
            profile=SHARED_WORKER_PROFILE,
            provider=None,
            model=None,
            concurrency=1,
            executable=_stub_executable(tmp_path),
            toolsets=None,
        )
        with pytest.raises(RuntimeConfigError, match="hermes_args is Hermes-only"):
            binding.hermes_args("x")


# ---------------------------------------------------------------------------
# 4. Pre-M2 dispatch fails closed (no Hermes fallback)
# ---------------------------------------------------------------------------

class TestPreM2FailClosed:
    def test_dispatcher_refuses_opencode_without_adapter(self, tmp_path):
        config = _load(tmp_path, _config_doc(tmp_path, EXECUTOR_OPENCODE))
        host = _FakeHost()
        with pytest.raises(RuntimeConfigError, match="no registered AF execution adapter"):
            create_production_execution_dispatcher(runtime_config=config, host_client=host)
        assert host.calls == 0

    def test_hermes_transport_refuses_opencode(self, tmp_path):
        config = _load(tmp_path, _config_doc(tmp_path, EXECUTOR_OPENCODE))
        with pytest.raises(RuntimeConfigError, match="no registered AF execution adapter"):
            create_hermes_completion_delivery_transport(runtime_config=config)


# ---------------------------------------------------------------------------
# 5. Single operator-owned authority / no model-supplied host authority
# ---------------------------------------------------------------------------

class TestOperatorAuthority:
    def test_task_handoff_has_no_host_authority_fields(self):
        names = {f.name for f in dataclasses.fields(TaskHandoff)}
        assert "host_endpoint" not in names
        assert "executor" not in names
        assert "executable" not in names

    def test_runtime_config_module_does_not_import_task_handoff_or_model(self):
        source = pathlib.Path("aota_forge/runtime/config.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert "aota_forge.work_plane.handoff" not in imported
        assert not any("llm" in module or "model" in module for module in imported)

    def test_host_endpoint_only_enters_through_operator_config_channel(self, monkeypatch, tmp_path):
        # env channel is the single operator seam; no default endpoint exists
        monkeypatch.delenv("AOTA_FORGE_RUNTIME_CONFIG", raising=False)
        with pytest.raises(RuntimeConfigError, match="no operator runtime configuration"):
            load_runtime_config()

    def test_opencode_endpoint_is_not_source_hardcoded(self):
        source = pathlib.Path("aota_forge/runtime/config.py").read_text(encoding="utf-8")
        assert "127.0.0.1:4096" not in source
