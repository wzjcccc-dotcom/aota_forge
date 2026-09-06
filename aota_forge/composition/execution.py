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

M2/W3 final production wiring: the composition now accepts an EXPLICIT
``ExecutionStateStore`` dependency (no implicit global store;
PRODUCTION_STORAGE_ENGINE_FROZEN=no — the file-backed store stays an explicitly
selected bounded single-host restart adapter, never a default) plus the trusted
runtime-side ``origin_session_ref`` (never TaskHandoff, never model-supplied).
The admission-scope resolver and the concurrency bounds map are derived ONLY
from the operator RuntimeConfig bindings, so a fresh process reconstructs the
same accounting after restart.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any, Callable

from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import ExecutionStateStore, OriginSessionRef
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.roles import RoleMapping
from aota_forge.core.ingress import bind_execution_dispatcher
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.config import (
    RuntimeConfig,
    load_runtime_config,
    resolve_binding_for_canonical_role,
    worker_canonical_profile_mapping,
)

# Repository root is deterministic and independent of the process launch CWD.
# It is a working-directory default only, not deployment authority.
PRODUCTION_HERMES_DEFAULT_CWD = Path(__file__).resolve().parents[2]


def _production_capabilities(
    supported_canonical_roles: tuple[str, ...] = ("coder",),
    *,
    concurrency_limit: int = 1,
) -> ExecutorCapabilities:
    # Production Hermes slice: async, process isolation. M2/W3 advertises the
    # operator-configured concurrency bound mechanically; admission ENFORCEMENT
    # is the W3 durable coordinator (RuntimeConfig -> bounds, ExecutionStateStore
    # -> durable active-set, W3 -> enforcement), not this capability vector.
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
        concurrency_limit=concurrency_limit,
    )


def admission_scope_for_package(config: RuntimeConfig) -> Callable[[ExecutionPackage], str | None]:
    """Server-side trusted resolver: canonical role -> "executor:work_role".

    Derived exclusively from the operator RuntimeConfig binding for the
    package's canonical role. The model and TaskHandoff never supply this
    value; it is a concurrency-accounting identity, not authority. An
    unmappable role resolves to None (the W3 coordinator then fails closed
    whenever configured bounds exist).
    """

    def _resolve(package: ExecutionPackage) -> str | None:
        if not isinstance(package, ExecutionPackage):
            raise TypeError("resolver requires an ExecutionPackage")
        try:
            binding = resolve_binding_for_canonical_role(package.canonical_role, config)
        except Exception:  # noqa: BLE001 - unmapped role has no accounting bucket
            return None
        return f"{binding.executor}:{binding.work_role}"

    return _resolve


def admission_limits_from_runtime_config(config: RuntimeConfig) -> dict[str, int]:
    """Bounded concurrency limits keyed by the same deterministic scopes."""
    return {f"{b.executor}:{b.work_role}": b.concurrency for b in config.bindings}


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
    state_store: ExecutionStateStore | None = None,
    origin_session_ref: OriginSessionRef | str | None = None,
) -> ExecutionDispatcher:
    """Construct the real production graph without changing Core routing.

    ``runtime_config`` must be an operator-owned RuntimeConfig (explicit
    injection) or reachable through the ``AOTA_FORGE_RUNTIME_CONFIG`` env
    channel; otherwise construction fails closed. The Hermes executable is
    ``launcher_path`` when explicitly provided (bounded override seam), else
    the operator config's validated ``executable``.

    M2/W3 durable wiring (explicit dependencies, no implicit globals):
    ``state_store`` connects the W1 canonical durable execution seam (pass a
    FileBackedExecutionStateStore for the bounded single-host restart
    deployment; leaving it None keeps the M1 process-local behavior and is
    NOT a hidden default engine), and ``origin_session_ref`` is the trusted
    runtime-side task-main binding recorded on every new durable execution
    record. There is still exactly ONE dispatcher in this graph.
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
    caps = _production_capabilities(
        tuple(sorted(effective_mapping.keys())),
        concurrency_limit=config.concurrency,
    )

    adapter = HermesAdapter(
        host_client=client,
        capabilities=caps,
        role_mapping=role_mapping,
        runtime_config=config,
    )
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(
        registry,
        state_store=state_store,
        origin_session_ref=origin_session_ref,
        admission_scope_resolver=admission_scope_for_package(config),
    )


def create_durable_completion_coordinator(
    *,
    dispatcher: ExecutionDispatcher,
    state_store: ExecutionStateStore,
    runtime_config: Any | None = None,
    transport: Any | None = None,
    **coordinator_kwargs: Any,
) -> DurableCompletionCoordinator:
    """Wire the narrow W3 coordinator over the SAME durable dispatcher graph.

    Concurrency bounds come only from the operator RuntimeConfig bindings
    (explicit injection or the trusted env channel; never a source-owned
    default). ``transport`` is the executor-neutral completion delivery seam
    (e.g. the Hermes exact-session transport); the coordinator runs no loop.
    """
    config = _resolve_operator_runtime_config(runtime_config)
    if dispatcher.state_store is not state_store:
        from aota_forge.runtime.completion import CompletionCoordinatorError

        raise CompletionCoordinatorError(
            "the coordinator requires the dispatcher wired to the same ExecutionStateStore"
        )
    return DurableCompletionCoordinator(
        dispatcher=dispatcher,
        store=state_store,
        transport=transport,
        admission_limits=admission_limits_from_runtime_config(config),
        **coordinator_kwargs,
    )


def bind_production_execution_dispatcher(**kwargs: Any) -> ExecutionDispatcher:
    """Build and bind the production dispatcher at the application boundary."""
    dispatcher = create_production_execution_dispatcher(**kwargs)
    bind_execution_dispatcher(dispatcher)
    return dispatcher
