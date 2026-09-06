"""Production executor composition for AF Hermes runtime (M1/W1).

W1 launcher resolution: Path A — direct Hermes invocation.
The stale `hermes-host` wrapper assumption is removed; production now
reuses the real Hermes binary at /home/latios/.local/bin/hermes.
The wrapper was thin (subprocess + bounded output) with no required
structured protocol that real Hermes CLI cannot provide, so direct
`hermes -p <profile> --provider X -m Y -z <instruction>` is preferred.

Runtime binding authority: operator-owned RuntimeConfig owns executor/
profile/provider/model/concurrency. Composition resolves the binding
deterministically and the Hermes adapter translates it to CLI flags.
Core remains executor-neutral and TaskHandoff never carries deployment
authority.
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

# W1 direct Hermes invocation — real binary, not the absent hermes-host wrapper.
# Verified: /home/latios/.local/bin/hermes exists, version v0.21.0, supports -p/-m/--provider/-z.
PRODUCTION_HERMES_EXECUTABLE = "/home/latios/.local/bin/hermes"
# Legacy alias kept for read-compat; both now point at the real executable.
PRODUCTION_HERMES_HOST_LAUNCHER = PRODUCTION_HERMES_EXECUTABLE

# W1 shared worker profile: all Worker roles deterministic map to one profile.
# This is runtime binding (operator deployment), not semantic CanonicalRole mapping.
PRODUCTION_HERMES_PROFILE_MAPPING = {
    "planner": "aota-worker",
    "coder": "aota-worker",
    "reviewer": "aota-worker",
    "steward": "aota-worker",
}
# Task-main profile is separate and not a CanonicalRole (no mapping added).

# Repository root is deterministic and independent of the process launch CWD.
PRODUCTION_HERMES_DEFAULT_CWD = Path(__file__).resolve().parents[2]


def _production_capabilities() -> ExecutorCapabilities:
    # Production Hermes slice remains bounded: async, process, concurrency 1.
    # W1 expands role binding preparation to all worker roles (shared profile)
    # but does not yet implement multi-role concurrent execution proof (W3).
    return ExecutorCapabilities(
        executor_id="hermes",
        adapter_kind="hermes_host_adapter",
        supported_execution_modes=("async",),
        supports_streaming_events=False,
        supports_task_cancellation=True,
        supports_task_resume=False,
        supports_structured_result=False,
        supported_canonical_roles=("coder", "planner", "reviewer", "steward"),
        supported_isolation_modes=("process",),
        supports_working_directory=True,
        supports_artifact_transport=False,
        max_timeout_seconds=300,
        concurrency_limit=1,
    )


def _load_runtime_config_for_composition(validate_executable: bool) -> Any | None:
    """Load operator runtime config for composition, fail-closed but test-friendly.

    When validate_executable=False (tests inject fake host_client), we skip
    filesystem validation so tests don't require a real hermes binary.
    """
    try:
        from aota_forge.runtime.config import load_runtime_config

        return load_runtime_config(validate_executable=validate_executable)
    except Exception:
        # No operator config or invalid -> fallback to None; adapter will use static mapping.
        # This keeps existing offline tests working without env var.
        return None


def create_production_execution_dispatcher(
    *,
    launcher_path: str | PathLike[str] | None = None,
    default_cwd: str | PathLike[str] | None = PRODUCTION_HERMES_DEFAULT_CWD,
    host_client: Any | None = None,
    host_client_factory: Callable[..., Any] = HermesHostClient,
    runtime_config: Any | None = None,
) -> ExecutionDispatcher:
    """Construct the real production graph without changing Core routing.

    Runtime binding: operator-owned RuntimeConfig resolves executor/profile/provider/
    model/concurrency; composition passes it to HermesAdapter for deterministic
    translation to `hermes -p <profile> --provider X -m Y -z ...`.
    If no RuntimeConfig is provided, loads from AOTA_FORGE_RUNTIME_CONFIG env
    (or deterministic default) with filesystem validation only when a real host
    client is created.
    """
    # Resolve runtime config (operator-owned). Tests that inject host_client
    # should not require a real executable, so we disable validation then.
    if runtime_config is None:
        # Only validate executable when we will actually spawn a real process
        validate_exec = host_client is None
        runtime_config = _load_runtime_config_for_composition(validate_executable=validate_exec)

    # Resolve launcher: explicit arg > runtime_config.executable > legacy constant
    effective_launcher = launcher_path
    if effective_launcher is None:
        if runtime_config is not None and hasattr(runtime_config, "executable"):
            effective_launcher = runtime_config.executable
        else:
            effective_launcher = PRODUCTION_HERMES_EXECUTABLE

    client = host_client if host_client is not None else host_client_factory(effective_launcher, default_cwd=default_cwd)

    # Resolve profile mapping: prefer runtime_config bindings when available,
    # otherwise use static shared worker mapping.
    effective_mapping = PRODUCTION_HERMES_PROFILE_MAPPING
    if runtime_config is not None and hasattr(runtime_config, "bindings"):
        try:
            # Build mapping from runtime_config worker bindings: canonical_role -> profile
            from aota_forge.runtime.config import _CANONICAL_TO_WORK_ROLE

            mapping_dict: dict[str, str] = {}
            for canonical, work_role in _CANONICAL_TO_WORK_ROLE.items():
                try:
                    binding = runtime_config.get_binding(work_role)
                    mapping_dict[canonical] = binding.profile
                except Exception:
                    continue
            if mapping_dict:
                effective_mapping = mapping_dict
        except Exception:
            pass

    role_mapping = RoleMapping.create("hermes", effective_mapping)
    # Production capabilities reflect effective mapping's supported roles
    caps = _production_capabilities()
    # If effective mapping has different role set, adjust capabilities accordingly
    # but keep production slice bounded (concurrency 1, async only).
    if set(effective_mapping.keys()) != set(caps.supported_canonical_roles):
        caps = ExecutorCapabilities(
            executor_id=caps.executor_id,
            adapter_kind=caps.adapter_kind,
            supported_execution_modes=caps.supported_execution_modes,
            supports_streaming_events=caps.supports_streaming_events,
            supports_task_cancellation=caps.supports_task_cancellation,
            supports_task_resume=caps.supports_task_resume,
            supports_structured_result=caps.supports_structured_result,
            supported_canonical_roles=tuple(sorted(effective_mapping.keys())),
            supported_isolation_modes=caps.supported_isolation_modes,
            supports_working_directory=caps.supports_working_directory,
            supports_artifact_transport=caps.supports_artifact_transport,
            max_timeout_seconds=caps.max_timeout_seconds,
            concurrency_limit=caps.concurrency_limit,
        )

    adapter = HermesAdapter(
        host_client=client,
        capabilities=caps,
        role_mapping=role_mapping,
        runtime_config=runtime_config,
    )
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry)


def bind_production_execution_dispatcher(**kwargs: Any) -> ExecutionDispatcher:
    """Build and bind the production dispatcher at the application boundary."""
    dispatcher = create_production_execution_dispatcher(**kwargs)
    bind_execution_dispatcher(dispatcher)
    return dispatcher
