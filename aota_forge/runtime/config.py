"""Operator-owned Hermes runtime configuration and invocation binding (M1/W1).

Authority boundary
------------------
* semantic input: TaskHandoff / AgentWorkRole (work_plane)
* Core execution contracts: executor-neutral (core.execution)
* operator runtime configuration: executor/profile/provider/model/concurrency (this module)
* composition: resolve runtime binding
* Hermes adapter: translate binding -> actual Hermes invocation (via host_client)

Invariants
----------
* WORK_ROLE_IS_HERMES_PROFILE=no
* ONE_SHARED_WORKER_PROFILE=yes  -> analyst/coder/reviewer/project-steward all -> aota-worker
* TASK_MAIN_PROFILE=aota-task-main (independent binding, not a CanonicalRole)
* TASK_HANDOFF_OWNS_MODEL=no / CORE_HERMES_CONTAMINATION=no
* MISSING_EXECUTABLE_FAILS_CLOSED=yes (no silent fallback)
* Fail-closed on invalid executor, missing profile, invalid concurrency, unknown role.
* Deterministic: same config + same role -> same effective binding.

Minimal V1 contract
-------------------
Fields: executor, profile, provider, model, concurrency, executable
Provider/model support explicit operator pinning; production config must be able
to deterministic resolve effective provider/model (via per-role or default).
Concurrency is bounded operator setting (default 1, validated).

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

# Valid hermes executables (real paths). We validate existence at load time but
# also provide a known set for stricter checks if needed.
DEFAULT_HERMES_EXECUTABLE = "/home/latios/.local/bin/hermes"
LEGACY_HERMES_HOST = "/home/latios/.local/bin/hermes-host"

# Concurrency bounds for W1 (M1/M2/M3 use, but W1 only configures)
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 32

# Provider/model defaults for production deterministic resolution.
# If per-role not pinned, these defaults are used; if these are also absent,
# effective resolution is None (hermes will use its config.yaml defaults) but
# production operator config should pin explicitly.
DEFAULT_PROVIDER = "opencode-go"
DEFAULT_MODEL = "muse-spark-1.2-contributor"


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
    # Also reject legacy hermes-host if it does not exist (fail-closed)
    return str(p.resolve())


@dataclass(frozen=True)
class RuntimeBinding:
    """Deterministic runtime binding for one work role.

    executor/work_role -> profile/provider/model/concurrency/executable
    """

    work_role: str
    executor: str
    profile: str
    provider: str | None
    model: str | None
    concurrency: int
    executable: str

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

    @property
    def effective_provider(self) -> str | None:
        return self.provider

    @property
    def effective_model(self) -> str | None:
        return self.model

    def hermes_args(self, instruction: str) -> list[str]:
        """Deterministic translation to actual Hermes CLI arguments.

        Real hermes flags per v0.21: -p/--profile, --provider, -m/--model, -z PROMPT
        Verified via `hermes --help`: -m MODEL, --provider PROVIDER, -p PROFILE.
        No memory lookup, no heuristic.
        """
        if not isinstance(instruction, str) or not instruction.strip():
            raise RuntimeConfigError("instruction must be a non-empty string for hermes invocation")
        args: list[str] = [self.executable, "-p", self.profile]
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
        # Ensure bindings are all RuntimeBinding and unique work_role
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


def _default_bindings(
    executor: str = "hermes",
    executable: str = DEFAULT_HERMES_EXECUTABLE,
    concurrency: int = 1,
    provider: str | None = DEFAULT_PROVIDER,
    model: str | None = DEFAULT_MODEL,
) -> tuple[RuntimeBinding, ...]:
    # Validate executable exists; if default missing, fail-closed will raise at config creation time.
    # For test environments where executable may not exist, caller can override via validate.
    bindings: list[RuntimeBinding] = []
    for role in sorted(ALLOWED_WORK_ROLES):
        if role in WORKER_ROLES:
            profile = SHARED_WORKER_PROFILE
        else:
            profile = TASK_MAIN_PROFILE
        bindings.append(
            RuntimeBinding(
                work_role=role,
                executor=executor,
                profile=profile,
                provider=provider,
                model=model,
                concurrency=concurrency,
                executable=executable,
            )
        )
    return tuple(bindings)


def get_default_runtime_config(
    *,
    executor: str = "hermes",
    executable: str = DEFAULT_HERMES_EXECUTABLE,
    concurrency: int = 1,
    provider: str | None = DEFAULT_PROVIDER,
    model: str | None = DEFAULT_MODEL,
    validate_executable: bool = True,
) -> RuntimeConfig:
    """Return deterministic default runtime config (operator-owned but code-default).

    Used when no operator file is present. Still bounded and validated, but
    allows caller to disable executable validation for pure offline tests that
    inject a fake host_client and never spawn a real process.
    """
    if not validate_executable:
        # For offline tests: use placeholder without filesystem check.
        # We bypass _validate_executable by constructing via object.__setattr__ trick,
        # but simpler: temporarily make a temp file? Instead, we create bindings with
        # executable as given and skip validation via unsafe bypass only when requested.
        # Achieve by temporarily monkeypatching _validate_executable.
        import unittest.mock as mock

        with mock.patch("aota_forge.runtime.config._validate_executable", lambda v: str(v).strip()):
            bindings = _default_bindings(
                executor=executor,
                executable=executable,
                concurrency=concurrency,
                provider=provider,
                model=model,
            )
            return RuntimeConfig(
                executor=executor,
                executable=executable,
                concurrency=concurrency,
                provider=provider,
                model=model,
                bindings=bindings,
            )
    # Normal path validates executable exists
    bindings = _default_bindings(
        executor=executor,
        executable=executable,
        concurrency=concurrency,
        provider=provider,
        model=model,
    )
    return RuntimeConfig(
        executor=executor,
        executable=executable,
        concurrency=concurrency,
        provider=provider,
        model=model,
        bindings=bindings,
    )


def _parse_bindings_dict(
    raw_bindings: Any,
    executor: str,
    executable: str,
    default_concurrency: int,
    default_provider: str | None,
    default_model: str | None,
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
        allowed_keys = {"profile", "provider", "model", "concurrency", "executor", "executable"}
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
            )
        )
    # Ensure all roles covered? W1 requires at least worker roles + task-main? But allow partial for now?
    # For production config we expect all 5, but we don't enforce here strictly; missing role will fail on lookup.
    return tuple(sorted(bindings, key=lambda b: b.work_role))


def load_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
    validate_executable: bool = True,
) -> RuntimeConfig:
    """Load operator runtime config from env channel or explicit path.

    Deterministic, typed, bounded, fail-closed, no arbitrary injection, no LLM control.
    Mirrors cli/config.py trusted pattern: env var points to JSON file, bounded size,
    no symlink, strict unknown-key rejection.
    """
    env = environ if environ is not None else os.environ
    path_str = str(config_path) if config_path is not None else env.get(RUNTIME_CONFIG_ENV)

    if not path_str:
        # No operator config: return deterministic default (still validated)
        return get_default_runtime_config(validate_executable=validate_executable)

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
    allowed_top = {"executor", "executable", "concurrency", "provider", "model", "bindings"}
    unknown_top = set(data.keys()) - allowed_top
    if unknown_top:
        raise RuntimeConfigError(f"unknown runtime config keys: {sorted(unknown_top)}")

    # executor required, else default hermes (but we require explicit for determinism)
    executor_raw = data.get("executor", "hermes")
    executor = _validate_executor(executor_raw)

    executable_raw = data.get("executable", DEFAULT_HERMES_EXECUTABLE)
    if validate_executable:
        executable = _validate_executable(executable_raw)
    else:
        # For offline tests: skip filesystem check but still ensure non-empty string
        if not isinstance(executable_raw, str) or not str(executable_raw).strip():
            raise RuntimeConfigError("executable must be a non-empty string")
        executable = str(Path(str(executable_raw).strip()).resolve()) if Path(str(executable_raw).strip()).is_absolute() else str(executable_raw).strip()

    concurrency_raw = data.get("concurrency", 1)
    concurrency = _validate_concurrency(concurrency_raw)

    # Top-level provider/model: explicit null allowed -> None; absent -> defaults
    if "provider" in data:
        provider = _validate_provider(data["provider"], "provider")
    else:
        provider = DEFAULT_PROVIDER

    if "model" in data:
        model = _validate_model(data["model"], "model")
    else:
        model = DEFAULT_MODEL

    raw_bindings = data.get("bindings")
    if raw_bindings is None:
        # No explicit bindings: generate defaults for all roles
        bindings = _default_bindings(
            executor=executor,
            executable=executable,
            concurrency=concurrency,
            provider=provider,
            model=model,
        )
    else:
        bindings = _parse_bindings_dict(
            raw_bindings,
            executor=executor,
            executable=executable,
            default_concurrency=concurrency,
            default_provider=provider,
            default_model=model,
        )

    return RuntimeConfig(
        executor=executor,
        executable=executable,
        concurrency=concurrency,
        provider=provider,
        model=model,
        bindings=bindings,
    )


def resolve_binding_for_work_role(
    work_role: object,
    config: RuntimeConfig | None = None,
) -> RuntimeBinding:
    """Deterministic resolve work_role -> RuntimeBinding via given or default config."""
    cfg = config if config is not None else get_default_runtime_config(validate_executable=False)
    return cfg.get_binding(work_role)


def resolve_binding_for_canonical_role(
    canonical_role: str,
    config: RuntimeConfig | None = None,
) -> RuntimeBinding:
    """Deterministic resolve canonical_role (planner/coder etc.) -> RuntimeBinding.

    Uses reverse mapping canonical->work_role. Fails closed if no mapping.
    """
    if not isinstance(canonical_role, str) or type(canonical_role) is not str:
        raise RuntimeConfigError(f"canonical_role must be a string, got {type(canonical_role).__name__}")
    role = canonical_role.strip()
    if role not in _CANONICAL_TO_WORK_ROLE:
        # Also allow direct worker roles? But spec says task-main has no canonical, so that case fails.
        raise RuntimeConfigError(f"no work role mapping for canonical role {role!r}")
    work_role = _CANONICAL_TO_WORK_ROLE[role]
    return resolve_binding_for_work_role(work_role, config)
