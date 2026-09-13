"""AF #51 M1/W2 — Operator-owned bounded Worker execution budget (bounded V1/V2).

Repairs I40-B008. The production Worker total execution lifetime used to be a
fixed 300s source constant; a valid real Worker needed ~370s. The trusted
operator ``RuntimeConfig`` now carries one bounded
``worker_execution_timeout_seconds`` value that drives both the production
``HermesHostClient(timeout_seconds=...)`` lifetime and the advertised
``ExecutorCapabilities.max_timeout_seconds``:

    HOST_TIMEOUT_SECONDS == EXECUTOR_MAX_TIMEOUT_SECONDS
                         == RUNTIME_CONFIG.worker_execution_timeout_seconds

The value is operator-owned, positive, bounded, deterministic, never
model-owned, never TaskHandoff-owned, and never a per-dispatch semantic
override. B005 timeout terminal semantics remain untouched.

Proof boundary (honest):
  PROVES=config contract parsing/validation and the single-source projection
         through the real production composition (host client + capabilities).
  DOES_NOT_PROVE=a real >300s Hermes Worker run (M1/W3 owns the real
         production vertical proof).
"""

from __future__ import annotations

import inspect
import json
import pathlib
import stat
from typing import Any

import pytest

import aota_forge.composition.execution as composition_execution
import aota_forge.runtime.completion as completion_module
from aota_forge.adapters.hermes.executor import HERMES_STATUS_MAP
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.runtime.config import (
    DEFAULT_WORKER_EXECUTION_TIMEOUT_SECONDS,
    MAX_WORKER_EXECUTION_TIMEOUT_SECONDS,
    MIN_WORKER_EXECUTION_TIMEOUT_SECONDS,
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
)

OLD_HARDCODED_PRODUCTION_TIMEOUT_SECONDS = 300
REAL_WORKER_RUNTIME_MS = 369996.9


def _stub_executable(tmp_path: pathlib.Path) -> str:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _complete_bindings() -> dict[str, Any]:
    bindings: dict[str, Any] = {
        role: {"profile": "aota-worker", "toolsets": ["aota"]}
        for role in ("analyst", "coder", "reviewer", "project-steward")
    }
    bindings["task-main"] = {"profile": "aota-task-main"}
    return bindings


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
    path = tmp_path / f"{name}"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def _loaded_config(tmp_path: pathlib.Path, **overrides: Any) -> RuntimeConfig:
    path = _write_config(tmp_path, _config_doc(tmp_path, **overrides))
    return load_runtime_config(config_path=path)


def _package_with_timeout(task_id: str, timeout_seconds: float) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=f"af51 w2 timeout projection {task_id}",
        capability_requirements={
            "execution_mode": "async",
            "isolation_mode": "process",
            "timeout_seconds": timeout_seconds,
        },
        constraints={},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


# ---------------------------------------------------------------------------
# 1. V1: RuntimeConfig contract — bounded, deterministic, fail-closed
# ---------------------------------------------------------------------------


class TestWorkerTimeoutConfigContract:
    def test_valid_bounded_value_accepted(self, tmp_path: pathlib.Path) -> None:
        for value in (1, 371, 600, DEFAULT_WORKER_EXECUTION_TIMEOUT_SECONDS, MAX_WORKER_EXECUTION_TIMEOUT_SECONDS):
            cfg = _loaded_config(tmp_path, worker_execution_timeout_seconds=value)
            assert cfg.worker_execution_timeout_seconds == value

    def test_default_is_explicit_bounded_and_permits_real_workload(
        self, tmp_path: pathlib.Path
    ) -> None:
        cfg = _loaded_config(tmp_path)
        assert cfg.worker_execution_timeout_seconds == DEFAULT_WORKER_EXECUTION_TIMEOUT_SECONDS
        assert cfg.worker_execution_timeout_seconds > OLD_HARDCODED_PRODUCTION_TIMEOUT_SECONDS
        assert cfg.worker_execution_timeout_seconds * 1000 > REAL_WORKER_RUNTIME_MS
        assert MIN_WORKER_EXECUTION_TIMEOUT_SECONDS <= cfg.worker_execution_timeout_seconds
        assert cfg.worker_execution_timeout_seconds <= MAX_WORKER_EXECUTION_TIMEOUT_SECONDS

    def test_max_bound_permits_real_workload(self) -> None:
        assert MAX_WORKER_EXECUTION_TIMEOUT_SECONDS > OLD_HARDCODED_PRODUCTION_TIMEOUT_SECONDS
        assert MAX_WORKER_EXECUTION_TIMEOUT_SECONDS * 1000 > REAL_WORKER_RUNTIME_MS

    @pytest.mark.parametrize("value", [0, -1, MAX_WORKER_EXECUTION_TIMEOUT_SECONDS + 1])
    def test_out_of_bounds_rejected(self, tmp_path: pathlib.Path, value: int) -> None:
        with pytest.raises(RuntimeConfigError, match="worker_execution_timeout_seconds must be between"):
            _loaded_config(tmp_path, worker_execution_timeout_seconds=value)

    @pytest.mark.parametrize("value", [True, False, 1.5, "900", None, [900]])
    def test_non_int_or_bool_rejected(self, tmp_path: pathlib.Path, value: Any) -> None:
        with pytest.raises(RuntimeConfigError, match="worker_execution_timeout_seconds must be an integer"):
            _loaded_config(tmp_path, worker_execution_timeout_seconds=value)

    def test_direct_constructor_validates_the_field(self, tmp_path: pathlib.Path) -> None:
        cfg = _loaded_config(tmp_path, worker_execution_timeout_seconds=600)
        rebuilt = RuntimeConfig(
            executor=cfg.executor,
            executable=cfg.executable,
            concurrency=cfg.concurrency,
            provider=cfg.provider,
            model=cfg.model,
            bindings=cfg.bindings,
            worker_execution_timeout_seconds=cfg.worker_execution_timeout_seconds,
        )
        assert rebuilt.worker_execution_timeout_seconds == 600
        with pytest.raises(RuntimeConfigError, match="worker_execution_timeout_seconds must be an integer"):
            RuntimeConfig(
                executor=cfg.executor,
                executable=cfg.executable,
                concurrency=cfg.concurrency,
                provider=cfg.provider,
                model=cfg.model,
                bindings=cfg.bindings,
                worker_execution_timeout_seconds=True,  # type: ignore[arg-type]
            )

    def test_unknown_top_level_key_still_fails_closed(self, tmp_path: pathlib.Path) -> None:
        doc = _config_doc(tmp_path)
        doc["worker_execution_timeout_seconds"] = 900
        doc["worker_timeout"] = 600
        path = _write_config(tmp_path, doc, name="unknown_timeout_key.json")
        with pytest.raises(RuntimeConfigError, match="unknown runtime config keys"):
            load_runtime_config(config_path=path)

    def test_unknown_binding_key_still_fails_closed(self, tmp_path: pathlib.Path) -> None:
        doc = _config_doc(tmp_path)
        doc["bindings"]["coder"]["worker_execution_timeout_seconds"] = 900
        path = _write_config(tmp_path, doc, name="binding_timeout_key.json")
        with pytest.raises(RuntimeConfigError, match="unknown keys in binding"):
            load_runtime_config(config_path=path)

    def test_effective_value_participates_in_config_truth(self, tmp_path: pathlib.Path) -> None:
        cfg = _loaded_config(tmp_path, worker_execution_timeout_seconds=750)
        serialized = cfg.to_dict()
        assert serialized["worker_execution_timeout_seconds"] == 750
        # Deterministic canonical form (no incidental variance).
        assert cfg.to_dict() == cfg.to_dict()

    def test_bool_is_not_an_int_acceptance_path(self, tmp_path: pathlib.Path) -> None:
        # type(value) is int is the only accepted type; True must not count as 1.
        with pytest.raises(RuntimeConfigError):
            _loaded_config(tmp_path, worker_execution_timeout_seconds=True)


# ---------------------------------------------------------------------------
# 2. V2: single trusted source projects to host timeout AND capability bound
# ---------------------------------------------------------------------------


class TestSingleSourceTimeoutProjection:
    def _dispatch_with_config(self, tmp_path: pathlib.Path, value: int):
        cfg = _loaded_config(tmp_path, worker_execution_timeout_seconds=value)
        dispatcher = create_production_execution_dispatcher(runtime_config=cfg)
        adapter = dispatcher.registry.get("hermes")
        return cfg, dispatcher, adapter

    def test_production_host_timeout_equals_configured_value(self, tmp_path: pathlib.Path) -> None:
        cfg, _dispatcher, adapter = self._dispatch_with_config(tmp_path, 900)
        client = getattr(adapter, "_host_client", None)
        assert client is not None
        assert client.timeout_seconds == float(cfg.worker_execution_timeout_seconds) == 900.0

    def test_executor_capability_timeout_equals_configured_value(self, tmp_path: pathlib.Path) -> None:
        cfg, _dispatcher, adapter = self._dispatch_with_config(tmp_path, 900)
        caps = adapter.capabilities()
        assert caps.max_timeout_seconds == cfg.worker_execution_timeout_seconds == 900

    def test_host_and_capability_exact_equality_single_source(self, tmp_path: pathlib.Path) -> None:
        for value in (371, 600, 900, 1800):
            cfg, _dispatcher, adapter = self._dispatch_with_config(tmp_path, value)
            client = getattr(adapter, "_host_client", None)
            caps = adapter.capabilities()
            assert client is not None
            assert (
                client.timeout_seconds
                == float(caps.max_timeout_seconds)
                == float(cfg.worker_execution_timeout_seconds)
                == float(value)
            )

    def test_capability_bound_enforced_for_package_constraints(self, tmp_path: pathlib.Path) -> None:
        _cfg, _dispatcher, adapter = self._dispatch_with_config(tmp_path, 900)
        accepted = adapter.validate_package(_package_with_timeout("af51-w2-600", 600))
        assert accepted.valid, accepted.errors
        rejected = adapter.validate_package(_package_with_timeout("af51-w2-1000", 1000))
        assert not rejected.valid
        assert any("CAPABILITY_MISMATCH" in e for e in rejected.errors)

    def test_old_hardcoded_300_production_source_removed(self) -> None:
        source = inspect.getsource(composition_execution)
        assert "max_timeout_seconds=300" not in source
        assert "timeout_seconds=300" not in source
        assert "config.worker_execution_timeout_seconds" in source

    def test_explicit_host_client_injection_keeps_capability_projection(self, tmp_path: pathlib.Path) -> None:
        cfg = _loaded_config(tmp_path, worker_execution_timeout_seconds=750)

        class FakeHost:
            def dispatch(self, payload):
                return {"adapter_handle": "fake", "status": "pending", "dispatch_time": "2026-09-13T00:00:00Z"}

        dispatcher = create_production_execution_dispatcher(runtime_config=cfg, host_client=FakeHost())
        caps = dispatcher.registry.get("hermes").capabilities()
        assert caps.max_timeout_seconds == cfg.worker_execution_timeout_seconds == 750


# ---------------------------------------------------------------------------
# 3. B005 preservation (cheap regression markers)
# ---------------------------------------------------------------------------


class TestB005Preserved:
    def test_timeout_terminal_semantics_untouched(self) -> None:
        assert completion_module.UNTRUSTWORTHY_OBSERVATION_TIMEOUT_IS_UNKNOWN is True
        assert completion_module.AUTHORITATIVE_EXECUTION_TIMEOUT_IS_TERMINAL is True
        assert completion_module.HERMES_STATUS_MAP_GLOBAL_FLIP is False
        assert completion_module.RUNTIME_AUTO_RETRY_ON_TIMEOUT is False
        assert completion_module.COMPLETION_CONTINUATION_BUDGET_IS_ROOT_CAUSE is False

    def test_raw_timeout_status_remains_unknown_and_authoritative_result_terminal(self) -> None:
        assert HERMES_STATUS_MAP.get("timeout") == CanonicalTaskState.UNKNOWN
        result = CanonicalResult.timeout(
            canonical_task_id="af51-w2-timeout",
            executor_id="hermes",
            correlation_id="corr-af51-w2-timeout",
        )
        assert result.canonical_task_state == CanonicalTaskState.FAILED
        assert result.error is not None
        assert result.error["code"] == "EXECUTION_TIMEOUT"
        assert result.error["retryable"] is False

    def test_no_auto_retry_introduced_by_w2(self) -> None:
        assert completion_module.RUNTIME_AUTO_RETRY_ON_TIMEOUT is False
