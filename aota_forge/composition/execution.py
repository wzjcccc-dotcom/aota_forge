"""Production executor composition for the AF Hermes runtime (M1/W1, W4 repair).

Runtime binding authority: an operator-owned RuntimeConfig owns executor/
profile/provider/model/concurrency/toolsets. It arrives through an explicit
trusted operator channel only:

* ``AOTA_FORGE_RUNTIME_CONFIG`` pointing to a trusted runtime config file, or
* an explicit ``runtime_config=`` injection at this composition seam.

There is no source-owned deployment fallback: when no operator RuntimeConfig
is available, production composition construction fails closed
(MISSING_RUNTIME_CONFIG_FAILS_CLOSED=yes) and no Worker process can start.
The executable comes from the operator config; the adapter translates the
resolved binding mechanically (typed argv, list form, no shell). Missing or
invalid config surfaces as ``RuntimeConfigError``/adapter errors.

Core remains executor-neutral and TaskHandoff never carries deployment
authority. The one shared AOTA MCP toolset pin carried by Worker bindings is
the mechanical restriction of the Worker tool surface (M1 acceptance boundary;
see aota_forge.runtime.config).
"""

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
from aota_forge.runtime.config import (
    RuntimeConfig,
    load_runtime_config,
    worker_canonical_profile_mapping,
)

# Repository root is deterministic and independent of the process launch CWD.
# It is a working-directory default only, not deployment authority.
PRODUCTION_HERMES_DEFAULT_CWD = Path(__file__).resolve().parents[2]


def _production_capabilities(
    supported_canonical_roles: tuple[str, ...] = ("coder",),
) -> ExecutorCapabilities:
    # Production Hermes slice remains bounded: async, process, concurrency 1.
    # The supported role vector is derived from the operator config bindings;
    # the production slice does not add multi-Worker concurrency (M2+).
    return ExecutorCapabilities(
        executor_id="hermes",
        adapter_kind="hermes_host_adapter",
        supported_execution_modes=("async",),
        supports_streaming_events=False,
        supports_task_cancellation=True,
        supports_task_resume=False,
        supports_structured_result=False,
        supported_canonical_roles=supported_canonical_roles,
        supported_isolation_modes=("process",),
        supports_working_directory=True,
        supports_artifact_transport=False,
        max_timeout_seconds=300,
        concurrency_limit=1,
    )


def _resolve_operator_runtime_config(runtime_config: Any | None) -> RuntimeConfig:
    """Operator authority only: explicit injection, else the env config channel.

    No source-owned default exists; a missing/invalid operator config fails
    closed with RuntimeConfigError before any host client is constructed.
    """
    if runtime_config is None:
        runtime_config = load_runtime_config()
    if not isinstance(runtime_config, RuntimeConfig):
        from aota_forge.runtime.config import RuntimeConfigError

        raise RuntimeConfigError(
            f"production composition requires an operator-owned RuntimeConfig, got {type(runtime_config).__name__}"
        )
    return runtime_config


def create_production_execution_dispatcher(
    *,
    default_cwd: str | PathLike[str] | None = PRODUCTION_HERMES_DEFAULT_CWD,
    host_client: Any | None = None,
    host_client_factory: Callable[..., Any] = HermesHostClient,
    runtime_config: Any | None = None,
    launcher_path: str | PathLike[str] | None = None,
) -> ExecutionDispatcher:
    """Construct the real production graph without changing Core routing.

    ``runtime_config`` must be an operator-owned RuntimeConfig (explicit
    injection) or reachable through the ``AOTA_FORGE_RUNTIME_CONFIG`` env
    channel; otherwise construction fails closed. The Hermes executable is
    ``launcher_path`` when explicitly provided (bounded override seam), else
    the operator config's validated ``executable``.
    """
    config = _resolve_operator_runtime_config(runtime_config)

    effective_launcher = str(launcher_path) if launcher_path is not None else config.executable
    client = host_client if host_client is not None else host_client_factory(
        effective_launcher, default_cwd=default_cwd
    )

    # Role->profile mapping derives exclusively from operator config bindings.
    # Worker bindings carry the shared AOTA MCP toolset pin enforced by
    # RuntimeConfig validation; no static source-owned mapping exists.
    effective_mapping = worker_canonical_profile_mapping(config)
    role_mapping = RoleMapping.create("hermes", effective_mapping)
    caps = _production_capabilities(tuple(sorted(effective_mapping.keys())))

    adapter = HermesAdapter(
        host_client=client,
        capabilities=caps,
        role_mapping=role_mapping,
        runtime_config=config,
    )
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry)


def bind_production_execution_dispatcher(**kwargs: Any) -> ExecutionDispatcher:
    """Build and bind the production dispatcher at the application boundary."""
    dispatcher = create_production_execution_dispatcher(**kwargs)
    bind_execution_dispatcher(dispatcher)
    return dispatcher
