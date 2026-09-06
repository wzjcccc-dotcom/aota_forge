"""Operator-owned Hermes runtime configuration and invocation binding (M1/W1, W4 repair).

Authority boundary
------------------
* semantic input: TaskHandoff / AgentWorkRole (work_plane)
* Core execution contracts: executor-neutral (core.execution)
* operator runtime configuration: executor/profile/provider/model/concurrency/
  toolsets (this module)
* composition: resolve runtime binding
* Hermes adapter: translate binding -> actual Hermes invocation (via host_client)

Invariants
----------
* RUNTIME_CONFIG_AUTHORITY=operator_owned: every production RuntimeConfig must
  originate from an explicit trusted operator channel
  (AOTA_FORGE_RUNTIME_CONFIG file, explicit RuntimeConfig injection, or an
  approved equivalent operator config seam).
* Missing operator config fails closed. There is no source-owned deployment
  fallback: this module never infers a provider, model, profile, or executable
  as production deployment authority.
* WORK_ROLE_IS_HERMES_PROFILE=no
* ONE_SHARED_WORKER_PROFILE=yes  -> analyst/coder/reviewer/project-steward all -> aota-worker
* TASK_MAIN_PROFILE=aota-task-main (independent binding, not a CanonicalRole)
* Worker bindings mechanically pin the shared AOTA MCP toolset allowlist
  (SHARED_MCP_TOOLSET) so a dispatched Worker exposes no raw terminal/shell or
  unrestricted native filesystem surface (M1 acceptance boundary; enforcement is
  translation into the Hermes invocation, see RuntimeBinding.hermes_args).
* TASK_HANDOFF_OWNS_MODEL=no / CORE_HERMES_CONTAMINATION=no
* MISSING_EXECUTABLE_FAILS_CLOSED=yes; MISSING_RUNTIME_CONFIG_FAILS_CLOSED=yes
* Fail-closed on invalid executor, missing profile, invalid concurrency,
  unknown role, unknown key, incomplete bindings, missing worker toolset pin.
* Deterministic: same config + same role -> same effective binding.

Minimal V1 contract
-------------------
Fields: executor, executable, concurrency, provider, model, toolsets, bindings.
Provider/model are explicit operator pins (a key may carry JSON null to defer
to the Hermes profile's own configuration); they are never defaulted here.
Concurrency is bounded operator setting (default 1, validated).
Offline unit tests build their own explicit RuntimeConfig fixtures (for example
over a bounded temporary executable); no test branch exists in this module.

This module is operator configuration, not Core. Core never imports it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.contracts.errors import ForgeError

RUNTIME_CONFIG_ENV = "AOTA_FORGE_RUNTIME_CONFIG"
MAX_RUNTIME_CONFIG_BYTES = 64 * 1024

TASK_MAIN_PROFILE = "aota-task-main"
SHARED_WORKER_PROFILE = "aota-worker"

# One shared restricted AOTA MCP server (W2 contract): the only tool surface a
# dispatched Worker may see. The value is the Hermes-side MCP server name under
# which the shared AOTA server is configured, i.e. a toolset selector passed to
# Hermes as `-t aota`; it is a runtime/deployment constant, not deployment
# provider/model/executable authority.
SHARED_MCP_TOOLSET = "aota"

ALLOWED_EXECUTORS: frozenset[str] = frozenset({"hermes"})
ALLOWED_WORK_ROLES: frozenset[str] = frozenset(
    {"task-main", "analyst", "coder", "reviewer", "project-steward"}
)
WORKER_ROLES: frozenset[str] = frozenset(
    {"analyst", "coder", "reviewer", "project-steward"}
)

# Reverse mapping for canonical_role -> work_role (worker only, task-main has no canonical)
_CANONICAL_TO_WORK_ROLE: dict[str, str] = {
    "planner": "analyst",
    "coder": "coder",
    "reviewer": "reviewer",
    "steward": "project-steward",
}

# Concurrency bounds for M1 (deployment concurrency, operator-bounded)
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 32


class RuntimeConfigError(ForgeError):
    """Operator runtime configuration is invalid, unsafe or unreadable."""

    code = "RUNTIME_CONFIG_INVALID"
    default_message = "runtime configuration is invalid"
    default_retryable = False

    def __init__(self, message: str | None = None, retryable: bool | None = None, details: object = None) -> None:
        from aota_forge.core.contracts.errors import _canonical_init

        _canonical_init(self, message, retryable, details)


def _validate_executor(value: Any) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise RuntimeConfigError(f"executor must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RuntimeConfigError("executor must be a non-empty string")
    if v not in ALLOWED_EXECUTORS:
        raise RuntimeConfigError(f"unknown executor {v!r}, allowed: {sorted(ALLOWED_EXECUTORS)}")
    return v


def _validate_profile(value: Any, work_role: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise RuntimeConfigError(f"profile for {work_role!r} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RuntimeConfigError(f"profile for {work_role!r} must be a non-empty string")
    if any(ch.isspace() for ch in v):
        raise RuntimeConfigError(f"profile for {work_role!r} must not contain whitespace: {v!r}")
    # Enforce shared worker profile invariant and task-main profile
    if work_role in WORKER_ROLES and v != SHARED_WORKER_PROFILE:
        raise RuntimeConfigError(
            f"worker role {work_role!r} must map to shared profile {SHARED_WORKER_PROFILE!r}, got {v!r}"
        )
    if work_role == "task-main" and v != TASK_MAIN_PROFILE:
        raise RuntimeConfigError(
            f"task-main must map to profile {TASK_MAIN_PROFILE!r}, got {v!r}"
        )
    return v


def _validate_provider(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise RuntimeConfigError(f"{field} must be a string or null, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RuntimeConfigError(f"{field} must be a non-empty string when provided")
    if any(ch.isspace() for ch in v):
        # provider names should not contain whitespace
        raise RuntimeConfigError(f"{field} must not contain whitespace: {v!r}")
    return v


def _validate_model(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise RuntimeConfigError(f"{field} must be a string or null, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RuntimeConfigError(f"{field} must be a non-empty string when provided")
    return v


def _validate_concurrency(value: Any, field: str = "concurrency") -> int:
    if type(value) is not int:
        raise RuntimeConfigError(f"{field} must be an integer, got {type(value).__name__}")
    if value < MIN_CONCURRENCY or value > MAX_CONCURRENCY:
        raise RuntimeConfigError(f"{field} must be between {MIN_CONCURRENCY} and {MAX_CONCURRENCY}, got {value!r}")
    return value


def _validate_executable(value: Any) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise RuntimeConfigError(f"executable must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RuntimeConfigError("executable must be a non-empty string")
    # Fail-closed: must exist, not symlink, file, executable. No fallback to PATH.
    p = Path(v)
    if p.is_symlink():
        raise RuntimeConfigError(f"executable is a symlink (rejected fail-closed): {v!r}")
    if not p.is_file():
        raise RuntimeConfigError(f"executable is missing or not a file: {v!r}")
    if not os.access(p, os.X_OK):
        raise RuntimeConfigError(f"executable is not executable: {v!r}")
    return str(p.resolve())


def _validate_toolsets(value: Any, work_role: str) -> tuple[str, ...] | None:
    """Bounded toolset allowlist validation (deployment/runtime policy).

    Worker roles must pin exactly the shared AOTA MCP toolset: the accepted M1
    boundary requires the shared MCP to be the Worker work interface and raw
    terminal/shell/native filesystem toolsets to be mechanically unavailable.
    task-main may carry any bounded allowlist or none (it is not dispatched as
    a Worker in M1).
    """
    if value is None:
        if work_role in WORKER_ROLES:
            raise RuntimeConfigError(
                f"worker role {work_role!r} must pin toolsets to the shared AOTA MCP allowlist "
                f"({SHARED_MCP_TOOLSET!r}); an unrestricted Worker surface fails closed"
            )
        return None
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise RuntimeConfigError(f"toolsets for {work_role!r} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or type(item) is not str:
            raise RuntimeConfigError(f"toolsets for {work_role!r} must contain only strings")
        v = item.strip()
        if not v:
            raise RuntimeConfigError(f"toolsets for {work_role!r} must contain only non-empty strings")
        if any(ch.isspace() for ch in v):
            raise RuntimeConfigError(f"toolsets for {work_role!r} must not contain whitespace: {v!r}")
        if v in items:
            raise RuntimeConfigError(f"toolsets for {work_role!r} must not contain duplicates: {v!r}")
        items.append(v)
    resolved = tuple(items)
    if work_role in WORKER_ROLES and resolved != (SHARED_MCP_TOOLSET,):
        raise RuntimeConfigError(
            f"worker role {work_role!r} toolsets must be exactly [{SHARED_MCP_TOOLSET!r}] "
            f"(shared AOTA MCP only), got {list(resolved)!r}"
        )
    return resolved


@dataclass(frozen=True)
class RuntimeBinding:
    """Deterministic runtime binding for one work role.

    executor/work_role -> profile/provider/model/concurrency/executable/toolsets
    """

    work_role: str
    executor: str
    profile: str
    provider: str | None
    model: str | None
    concurrency: int
    executable: str
    toolsets: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        # Validate via helpers to ensure frozen correctness even when constructed directly
        validate_role = self.work_role
        if validate_role not in ALLOWED_WORK_ROLES:
            raise RuntimeConfigError(f"unknown work role {validate_role!r}")
        _validate_executor(self.executor)
        _validate_profile(self.profile, self.work_role)
        if self.provider is not None:
            _validate_provider(self.provider, "provider")
        if self.model is not None:
            _validate_model(self.model, "model")
        _validate_concurrency(self.concurrency)
        _validate_executable(self.executable)
        object.__setattr__(
            self, "toolsets", _validate_toolsets(self.toolsets, self.work_role)
        )

    @property
    def effective_provider(self) -> str | None:
        return self.provider

    @property
    def effective_model(self) -> str | None:
        return self.model

    def hermes_args(self, instruction: str) -> list[str]:
        """Deterministic translation to actual Hermes CLI arguments.

        Real hermes flags per v0.21 (verified via `hermes --help` and
        `hermes_cli/oneshot.py`): -p PROFILE, -t TOOLSETS (comma-separated
        invocation-level toolset allowlist; MCP server names are valid),
        --provider PROVIDER, -m MODEL, -z PROMPT.
        No memory lookup, no heuristic.
        """
        if not isinstance(instruction, str) or not instruction.strip():
            raise RuntimeConfigError("instruction must be a non-empty string for hermes invocation")
        args: list[str] = [self.executable, "-p", self.profile]
        if self.toolsets:
            args.extend(["-t", ",".join(self.toolsets)])
        if self.provider is not None:
            args.extend(["--provider", self.provider])
        if self.model is not None:
            args.extend(["-m", self.model])
        args.extend(["-z", instruction])
        return args

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_role": self.work_role,
            "executor": self.executor,
            "profile": self.profile,
            "provider": self.provider,
            "model": self.model,
            "concurrency": self.concurrency,
            "executable": self.executable,
            "toolsets": list(self.toolsets) if self.toolsets is not None else None,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    """Operator-owned runtime configuration (typed, bounded, fail-closed)."""

    executor: str
    executable: str
    concurrency: int
    provider: str | None
    model: str | None
    bindings: tuple[RuntimeBinding, ...]

    def __post_init__(self) -> None:
        _validate_executor(self.executor)
        _validate_executable(self.executable)
        _validate_concurrency(self.concurrency)
        if self.provider is not None:
            _validate_provider(self.provider, "provider")
        if self.model is not None:
            _validate_model(self.model, "model")
        if not isinstance(self.bindings, (tuple, list)) or len(self.bindings) == 0:
            raise RuntimeConfigError("bindings must be a non-empty sequence")
        seen: set[str] = set()
        for b in self.bindings:
            if not isinstance(b, RuntimeBinding):
                raise RuntimeConfigError(f"binding must be RuntimeBinding, got {type(b).__name__}")
            if b.work_role in seen:
                raise RuntimeConfigError(f"duplicate binding for work role {b.work_role!r}")
            seen.add(b.work_role)
            if b.executor != self.executor:
                raise RuntimeConfigError(
                    f"binding executor {b.executor!r} must match config executor {self.executor!r}"
                )
            if b.executable != self.executable:
                raise RuntimeConfigError(
                    f"binding executable {b.executable!r} must match config executable {self.executable!r}"
                )
        missing = sorted(ALLOWED_WORK_ROLES - seen)
        if missing:
            raise RuntimeConfigError(
                f"operator runtime config is incomplete: bindings missing work roles {missing}"
            )

    def get_binding(self, work_role: object) -> RuntimeBinding:
        """Deterministic resolve work_role -> RuntimeBinding, fail-closed."""
        # Accept AgentWorkRole enum member or str, reject others fail-closed
        from enum import Enum

        if isinstance(work_role, Enum):
            # Only allow AgentWorkRole; reject CanonicalRole etc.
            # Check via value string
            try:
                from aota_forge.work_plane.roles import AgentWorkRole

                if isinstance(work_role, AgentWorkRole):
                    role_str = work_role.value
                else:
                    raise RuntimeConfigError(
                        f"work_role must be a string or AgentWorkRole, got {type(work_role).__name__}"
                    )
            except ImportError:
                raise RuntimeConfigError(f"work_role must be a string or AgentWorkRole, got {type(work_role).__name__}")
        elif isinstance(work_role, str) and type(work_role) is str:
            role_str = work_role.strip()
            if not role_str:
                raise RuntimeConfigError("work_role must be a non-empty string")
        else:
            raise RuntimeConfigError(f"work_role must be a string or AgentWorkRole, got {type(work_role).__name__}")

        if role_str not in ALLOWED_WORK_ROLES:
            raise RuntimeConfigError(f"unknown work role {role_str!r}")

        for b in self.bindings:
            if b.work_role == role_str:
                return b
        raise RuntimeConfigError(f"no runtime binding for work role {role_str!r}")

    def effective_provider_for(self, work_role: object) -> str | None:
        b = self.get_binding(work_role)
        if b.provider is not None:
            return b.provider
        return self.provider

    def effective_model_for(self, work_role: object) -> str | None:
        b = self.get_binding(work_role)
        if b.model is not None:
            return b.model
        return self.model

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "executable": self.executable,
            "concurrency": self.concurrency,
            "provider": self.provider,
            "model": self.model,
            "bindings": [b.to_dict() for b in sorted(self.bindings, key=lambda x: x.work_role)],
        }


def _parse_bindings_dict(
    raw_bindings: Any,
    executor: str,
    executable: str,
    default_concurrency: int,
    default_provider: str | None,
    default_model: str | None,
    default_toolsets: Any,
) -> tuple[RuntimeBinding, ...]:
    if not isinstance(raw_bindings, Mapping):
        raise RuntimeConfigError("bindings must be a mapping from work_role to binding object")
    if len(raw_bindings) == 0:
        raise RuntimeConfigError("bindings must be non-empty")
    bindings: list[RuntimeBinding] = []
    for role_key, binding_raw in raw_bindings.items():
        if not isinstance(role_key, str) or type(role_key) is not str:
            raise RuntimeConfigError(f"binding key must be a string, got {type(role_key).__name__}")
        role = role_key.strip()
        if role not in ALLOWED_WORK_ROLES:
            raise RuntimeConfigError(f"unknown work role in bindings: {role!r}")
        if not isinstance(binding_raw, Mapping):
            raise RuntimeConfigError(f"binding for {role!r} must be a mapping, got {type(binding_raw).__name__}")

        # Fail-closed on unknown keys in binding
        allowed_keys = {"profile", "provider", "model", "concurrency", "executor", "executable", "toolsets"}
        unknown = set(binding_raw.keys()) - allowed_keys
        if unknown:
            raise RuntimeConfigError(f"unknown keys in binding for {role!r}: {sorted(unknown)}")

        # Profile is required
        if "profile" not in binding_raw:
            raise RuntimeConfigError(f"binding for {role!r} missing required field 'profile'")
        profile = _validate_profile(binding_raw["profile"], role)

        if "provider" not in binding_raw:
            provider = default_provider
        else:
            provider = _validate_provider(binding_raw["provider"], f"provider for {role!r}")

        if "model" not in binding_raw:
            model = default_model
        else:
            model = _validate_model(binding_raw["model"], f"model for {role!r}")

        concurrency = binding_raw.get("concurrency", default_concurrency)
        concurrency = _validate_concurrency(concurrency, f"concurrency for {role!r}")

        if "toolsets" in binding_raw:
            toolsets = _validate_toolsets(binding_raw["toolsets"], role)
        elif role in WORKER_ROLES:
            toolsets = _validate_toolsets(default_toolsets, role)
        else:
            toolsets = None

        # executor/executable per binding may override but must match global if present
        binding_executor = binding_raw.get("executor", executor)
        binding_executable = binding_raw.get("executable", executable)
        # Validate they match globals if explicitly provided
        if "executor" in binding_raw:
            _validate_executor(binding_executor)
            if binding_executor != executor:
                raise RuntimeConfigError(
                    f"binding executor {binding_executor!r} for {role!r} must match global executor {executor!r}"
                )
        if "executable" in binding_raw:
            _validate_executable(binding_executable)
            if str(Path(binding_executable).resolve()) != str(Path(executable).resolve()):
                raise RuntimeConfigError(
                    f"binding executable {binding_executable!r} for {role!r} must match global executable {executable!r}"
                )

        bindings.append(
            RuntimeBinding(
                work_role=role,
                executor=executor,
                profile=profile,
                provider=provider,
                model=model,
                concurrency=concurrency,
                executable=str(Path(executable).resolve()),
                toolsets=toolsets,
            )
        )
    return tuple(sorted(bindings, key=lambda b: b.work_role))


def load_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> RuntimeConfig:
    """Load operator runtime config from the env channel or an explicit path.

    Deterministic, typed, bounded, fail-closed, no arbitrary injection, no LLM
    control. Mirrors cli/config.py trusted pattern: env var points to JSON file,
    bounded size, no symlink, strict unknown-key rejection.

    MISSING_RUNTIME_CONFIG_FAILS_CLOSED=yes: when neither an explicit
    ``config_path`` nor ``AOTA_FORGE_RUNTIME_CONFIG`` provides an operator
    config file, this raises; there is no source-owned default RuntimeConfig.
    """
    env = environ if environ is not None else os.environ
    path_str = str(config_path) if config_path is not None else env.get(RUNTIME_CONFIG_ENV)

    if not path_str or not str(path_str).strip():
        raise RuntimeConfigError(
            "no operator runtime configuration provided: set "
            f"{RUNTIME_CONFIG_ENV} to a trusted runtime config file or pass an "
            "explicit RuntimeConfig through the operator seam (fail-closed, "
            "no source-owned default deployment exists)"
        )

    p = Path(path_str)
    # Fail-closed file checks
    if p.is_symlink():
        raise RuntimeConfigError(f"runtime config file is a symlink (rejected): {path_str!r}")
    if not p.is_file():
        raise RuntimeConfigError(f"runtime config file is missing or not a file: {path_str!r}")
    try:
        if p.stat().st_size > MAX_RUNTIME_CONFIG_BYTES:
            raise RuntimeConfigError("runtime config file too large")
        data = json.loads(p.read_text(encoding="utf-8"))
    except RuntimeConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"runtime config unreadable: {type(exc).__name__}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeConfigError("runtime config must be a JSON object")

    # Fail-closed on unknown top-level keys
    allowed_top = {"executor", "executable", "concurrency", "provider", "model", "toolsets", "bindings"}
    unknown_top = set(data.keys()) - allowed_top
    if unknown_top:
        raise RuntimeConfigError(f"unknown runtime config keys: {sorted(unknown_top)}")

    # Fail-closed on missing required top-level fields: executor, executable,
    # provider, model and bindings must be explicitly operator-owned.
    required_top = {"executor", "executable", "provider", "model", "bindings"}
    missing_top = sorted(required_top - set(data.keys()))
    if missing_top:
        raise RuntimeConfigError(f"runtime config missing required fields: {missing_top}")

    executor = _validate_executor(data["executor"])
    executable = _validate_executable(data["executable"])
    concurrency = _validate_concurrency(data.get("concurrency", 1))
    provider = _validate_provider(data["provider"], "provider")
    model = _validate_model(data["model"], "model")
    default_toolsets = data.get("toolsets")
    if default_toolsets is not None and not isinstance(default_toolsets, (list, tuple)):
        raise RuntimeConfigError("toolsets must be a list of strings or absent")

    bindings = _parse_bindings_dict(
        data["bindings"],
        executor=executor,
        executable=executable,
        default_concurrency=concurrency,
        default_provider=provider,
        default_model=model,
        default_toolsets=default_toolsets,
    )

    return RuntimeConfig(
        executor=executor,
        executable=executable,
        concurrency=concurrency,
        provider=provider,
        model=model,
        bindings=bindings,
    )


def worker_canonical_profile_mapping(config: RuntimeConfig) -> dict[str, str]:
    """canonical_role -> Hermes profile for Worker roles, derived only from the
    operator-owned config. task-main is not a CanonicalRole and never appears.
    """
    if not isinstance(config, RuntimeConfig):
        raise RuntimeConfigError(
            f"an operator-owned RuntimeConfig is required, got {type(config).__name__}"
        )
    return {
        canonical: config.get_binding(work_role).profile
        for canonical, work_role in sorted(_CANONICAL_TO_WORK_ROLE.items())
    }


def resolve_binding_for_work_role(
    work_role: object,
    config: RuntimeConfig,
) -> RuntimeBinding:
    """Deterministic resolve work_role -> RuntimeBinding via the operator config.

    The config is required: there is no source-owned default fallback.
    """
    if not isinstance(config, RuntimeConfig):
        raise RuntimeConfigError(
            f"an operator-owned RuntimeConfig is required, got {type(config).__name__}"
        )
    return config.get_binding(work_role)


def resolve_binding_for_canonical_role(
    canonical_role: str,
    config: RuntimeConfig,
) -> RuntimeBinding:
    """Deterministic resolve canonical_role (planner/coder etc.) -> RuntimeBinding.

    Uses reverse mapping canonical->work_role. Fails closed if no mapping or no
    operator config is supplied.
    """
    if not isinstance(canonical_role, str) or type(canonical_role) is not str:
        raise RuntimeConfigError(f"canonical_role must be a string, got {type(canonical_role).__name__}")
    role = canonical_role.strip()
    if role not in _CANONICAL_TO_WORK_ROLE:
        # task-main has no canonical role, so that case fails; unknown or
        # unmapped canonical roles (e.g. executor) fail closed too.
        raise RuntimeConfigError(f"no work role mapping for canonical role {role!r}")
    work_role = _CANONICAL_TO_WORK_ROLE[role]
    return resolve_binding_for_work_role(work_role, config)
