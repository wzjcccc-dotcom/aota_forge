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

AF #49 M1/W3: ``create_hermes_completion_delivery_transport`` wires the
accepted W2 exact-session seam as the production completion transport; the
target session identity is per-delivery durable ``origin_session_ref`` truth,
never a construction/factory input and never model/Worker supplied.
"""

from __future__ import annotations

import os
from os import PathLike
from pathlib import Path
from typing import Any, Callable

from aota_forge.adapters.hermes.delivery import HermesCompletionDeliveryTransport
from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.adapters.hermes.session_reentry import (
    DEFAULT_REENTRY_TIMEOUT_SECONDS,
    HermesExactSessionReentry,
)
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


# AF #49 M1/W3 — production completion delivery transport wiring.
# The task-main profile/executable identity comes only from the operator-owned
# RuntimeConfig; the durable session identity is supplied per delivery from the
# trusted execution record's origin_session_ref, never from this factory and
# never from model/Worker input.
TASK_MAIN_COMPLETION_DELIVERY_ROLE = "task-main"


def _resolve_task_main_hermes_home(profile: str) -> Path | None:
    """Deterministic trusted Hermes home for exact-session re-entry (read-only).

    Bounded candidate order over operator/runtime-controlled locations already
    used by the accepted launcher/re-entry seams (``HERMES_HOME``,
    ``AOTA_HERMES_HOME_HOST``, the per-profile home). Never CWD-derived, never
    model/Worker supplied. Returns the first candidate with a durable
    ``state.db``; the deterministic per-profile default otherwise (the W2
    adapter then fails closed as unknown/not-found rather than guessing a new
    session).
    """
    candidates: list[Path] = []
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home))
    alt_home = os.environ.get("AOTA_HERMES_HOME_HOST", "").strip()
    if alt_home:
        candidates.append(Path(alt_home) / "profiles" / profile)
    if env_home:
        candidates.append(Path(env_home) / "profiles" / profile)
    candidates.append(Path.home() / ".hermes" / "profiles" / profile)
    candidates.append(Path.home() / ".hermes")
    for candidate in candidates:
        try:
            if (candidate / "state.db").is_file():
                return candidate
        except OSError:
            continue
    return Path.home() / ".hermes" / "profiles" / profile


def create_hermes_completion_delivery_transport(
    *,
    runtime_config: Any | None = None,
    hermes_home: str | PathLike[str] | None = None,
    state_db_path: str | PathLike[str] | None = None,
    spool_root: str | PathLike[str] | None = None,
    timeout_seconds: float | None = None,
) -> HermesCompletionDeliveryTransport:
    """Production Hermes completion transport over the accepted W2 exact-session seam.

    Reuses ``HermesExactSessionReentry`` + ``HermesCompletionDeliveryTransport``
    exactly as accepted (M2/W2/W3). The task-main profile comes from the
    operator RuntimeConfig binding; the exact target session arrives per
    delivery from the durable execution record's trusted
    ``origin_session_ref``. No session identity is supplied here and none can
    be supplied by the model or the Worker.
    """
    config = _resolve_operator_runtime_config(runtime_config)
    task_main_profile = config.get_binding(TASK_MAIN_COMPLETION_DELIVERY_ROLE).profile
    effective_home: Path | None = None
    if hermes_home is not None:
        effective_home = Path(hermes_home)
    else:
        effective_home = _resolve_task_main_hermes_home(task_main_profile)
    reentry = HermesExactSessionReentry(
        config.executable,
        hermes_home=effective_home,
        profile=task_main_profile,
        state_db_path=Path(state_db_path) if state_db_path is not None else None,
        spool_root=Path(spool_root) if spool_root is not None else None,
        timeout_seconds=(
            DEFAULT_REENTRY_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
        ),
    )
    return HermesCompletionDeliveryTransport(reentry)


def prune_reconciled_hermes_receipts(
    *,
    coordinator: DurableCompletionCoordinator,
    host_client: Any,
    now_wall: float | None = None,
) -> int:
    """One bounded explicit maintenance pass for canonically safe receipts.

    M2/W4 RV1 F01 repair wiring: the trusted runtime layer (this composition
    seam, the only place in the Hermes runtime graph that already imports both
    sides) asks the W3 coordinator which adapter handles have durable terminal
    canonical results plus digest-verified result-CARD truth, and hands
    that EXPLICIT set to the executor-private client maintenance API. Ownership
    stays separated:

    - the Hermes locator/client still performs only mechanical checks
      (terminal receipt validity, bounded retention age) and never imports the
      AF canonical store;
    - the AF coordinator never touches executor files;
    - no new dispatch can prune an uncanonicalized receipt (§11), and GC
      eligibility exists only AFTER canonical persist + verify (§13).

    No generic GC framework: this is the one bounded maintenance seam.
    """
    prune = getattr(host_client, "prune_canonicalized_receipts", None)
    if not callable(prune):
        raise TypeError(
            "host_client must expose prune_canonicalized_receipts(eligible_handles=...); "
            "implicit dispatch-side pruning was removed with the W4 receipt-retention repair"
        )
    eligible = coordinator.canonicalized_terminal_handles()
    if not eligible:
        return 0
    return prune(eligible_handles=eligible, now_wall=now_wall)


def bind_production_execution_dispatcher(**kwargs: Any) -> ExecutionDispatcher:
    """Build and bind the production dispatcher at the application boundary."""
    dispatcher = create_production_execution_dispatcher(**kwargs)
    bind_execution_dispatcher(dispatcher)
    return dispatcher
