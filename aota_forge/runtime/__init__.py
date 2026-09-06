"""AF Hermes runtime configuration and invocation binding (M1/W1)."""

from .config import (
    RUNTIME_CONFIG_ENV,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    SHARED_MCP_TOOLSET,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    load_runtime_config,
    resolve_binding_for_canonical_role,
    resolve_binding_for_work_role,
)

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "RuntimeBinding",
    "RuntimeConfig",
    "RuntimeConfigError",
    "SHARED_MCP_TOOLSET",
    "SHARED_WORKER_PROFILE",
    "TASK_MAIN_PROFILE",
    "load_runtime_config",
    "resolve_binding_for_canonical_role",
    "resolve_binding_for_work_role",
]
