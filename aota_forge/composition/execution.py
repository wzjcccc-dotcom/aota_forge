"""Production executor composition for the first Hermes slice."""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any, Callable

from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.roles import RoleMapping
from aota_forge.core.ingress import bind_execution_dispatcher

PRODUCTION_HERMES_HOST_LAUNCHER = "/home/latios/.local/bin/hermes-host"
PRODUCTION_HERMES_PROFILE_MAPPING = {"coder": "coder"}
# Repository root is deterministic and independent of the process launch CWD.
PRODUCTION_HERMES_DEFAULT_CWD = Path(__file__).resolve().parents[2]


def _production_capabilities() -> ExecutorCapabilities:
    return ExecutorCapabilities(
        executor_id="hermes",
        adapter_kind="hermes_host_adapter",
        supported_execution_modes=("async",),
        supports_streaming_events=False,
        supports_task_cancellation=True,
        supports_task_resume=False,
        supports_structured_result=False,
        supported_canonical_roles=("coder",),
        supported_isolation_modes=("process",),
        supports_working_directory=True,
        supports_artifact_transport=False,
        max_timeout_seconds=300,
        concurrency_limit=1,
    )


def create_production_execution_dispatcher(
    *,
    launcher_path: str | PathLike[str] = PRODUCTION_HERMES_HOST_LAUNCHER,
    default_cwd: str | PathLike[str] | None = PRODUCTION_HERMES_DEFAULT_CWD,
    host_client: Any | None = None,
    host_client_factory: Callable[..., Any] = HermesHostClient,
) -> ExecutionDispatcher:
    """Construct the real production graph without changing Core routing."""
    client = host_client if host_client is not None else host_client_factory(launcher_path, default_cwd=default_cwd)
    role_mapping = RoleMapping.create("hermes", PRODUCTION_HERMES_PROFILE_MAPPING)
    adapter = HermesAdapter(
        host_client=client,
        capabilities=_production_capabilities(),
        role_mapping=role_mapping,
    )
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry)


def bind_production_execution_dispatcher(**kwargs: Any) -> ExecutionDispatcher:
    """Build and bind the production dispatcher at the application boundary."""
    dispatcher = create_production_execution_dispatcher(**kwargs)
    bind_execution_dispatcher(dispatcher)
    return dispatcher
