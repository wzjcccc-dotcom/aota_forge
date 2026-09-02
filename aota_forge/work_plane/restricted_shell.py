"""Restricted Shell v0 Fallback — S2 M3-W4.

Residual fallback only: structured command_id + bounded argv -> one-shot
bounded subprocess via existing ToolResponse -> existing Tool Result Governance.

Invariants
----------
* RESTRICTED_SHELL_IS_RESIDUAL_FALLBACK=yes
* RESTRICTED_SHELL_PRIMARY_INTERFACE=no
* RAW_SHELL_COMMAND_STRING_ACCEPTED=no
* SHELL_TRUE_USED=no
* ARGV_STYLE_EXECUTION=yes
* TRUSTED_COMMAND_CATALOG=yes
* COMMAND_CATALOG_BOUNDED=yes
* ARBITRARY_EXECUTABLE_SELECTION_ALLOWED=no
* CALLER_SUPPLIED_EXECUTABLE_PATH_ALLOWED=no
* UNKNOWN_COMMAND_ID_FAIL_CLOSED=yes
* SHELL_EXPANSION_USED=no
* COMMAND_SUBSTITUTION_SUPPORTED=no
* PIPE_OPERATOR_SUPPORTED=no
* REDIRECTION_OPERATOR_SUPPORTED=no
* COMMAND_CHAINING_SUPPORTED=no
* NESTED_SHELL_EXPOSED=no
* GENERAL_INTERPRETER_ESCAPE_EXPOSED=no
* SPECIALIZED_TOOL_BYPASS_VIA_SHELL=no
* GIT_BYPASS_VIA_RESTRICTED_SHELL=no
* TEST_RUNNER_BYPASS_VIA_RESTRICTED_SHELL=no
* WORKSPACE_MUTATION_BYPASS_VIA_RESTRICTED_SHELL=no
* WORKTREE_BOUND_CWD=yes
* CALLER_SUPPLIED_ARBITRARY_CWD=no
* PATH_CAPABLE_COMMAND_ARGS_VALIDATED=yes
* ARG_COUNT_BOUNDED=yes
* ARG_LENGTH_BOUNDED=yes
* ENV_DENY_BY_DEFAULT=yes
* UNBOUNDED_HOST_ENV_INHERITANCE=no
* CALLER_CONTROLLED_PATH_LOOKUP=no
* TIMEOUT_REQUIRED=yes
* UNBOUNDED_COMMAND_EXECUTION_ALLOWED=no
* PROCESS_TREE_TERMINATION_REQUIRED=yes
* STDOUT_BOUNDED=yes
* STDERR_BOUNDED=yes
* TOTAL_PROCESS_OUTPUT_BOUNDED=yes
* SILENT_OUTPUT_TRUNCATION=no
* INTERACTIVE_SHELL=no
* PTY_ALLOCATED=no
* SHELL_INVOCATION_ONE_SHOT=yes
* PERSISTENT_SHELL_SESSION_CREATED=no
* EXISTING_TOOL_PROVIDER_REUSED=yes
* EXISTING_TOOL_RESPONSE_REUSED=yes
* EXISTING_TOOL_RESULT_GOVERNANCE_REUSED=yes
* NEW_SHELL_RESULT_ONTOLOGY_CREATED=no
* SHELL_RESULT_CARD_CREATED=no
* SHELL_RESULT_IS_AUTHORITY=no
* RESTRICTED_SHELL_DEFAULT_EAGER=no
* RESTRICTED_SHELL_PROGRESSIVE_FALLBACK=yes
* NEW_NETWORK_SUBSYSTEM_CREATED=no
* NETWORK_ISOLATION_ENFORCED=no (truthfully reported, not claimed)

Reuse
-----
* WorktreeSandboxBoundary (M1 W1)
* ProjectBoundResourceResolver / resolve_worktree_resource (M1 W2) for path args
* OperationContractDescriptor
* ToolProvider / ToolRequest / ToolResponse unchanged
* AgentsPolicyCandidate + resolve_applicable_policies
* TaskHandoff

No generic process runtime, no shell parser, no network subsystem.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate, resolve_applicable_policies
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_resources import resolve_worktree_resource
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Public invariant flags (for tests / downstream seam)
# ---------------------------------------------------------------------------

RESTRICTED_SHELL_IS_RESIDUAL_FALLBACK: bool = True
RESTRICTED_SHELL_PRIMARY_INTERFACE: bool = False
RAW_SHELL_COMMAND_STRING_ACCEPTED: bool = False
SHELL_TRUE_USED: bool = False
ARGV_STYLE_EXECUTION: bool = True
SHELL_EXPANSION_USED: bool = False
COMMAND_SUBSTITUTION_SUPPORTED: bool = False
PIPE_OPERATOR_SUPPORTED: bool = False
REDIRECTION_OPERATOR_SUPPORTED: bool = False
COMMAND_CHAINING_SUPPORTED: bool = False

TRUSTED_COMMAND_CATALOG_EXISTS: bool = True
TRUSTED_COMMAND_CATALOG: bool = True  # alias
COMMAND_CATALOG_BOUNDED: bool = True
ARBITRARY_EXECUTABLE_SELECTION_ALLOWED: bool = False
CALLER_SUPPLIED_EXECUTABLE_PATH_ALLOWED: bool = False
UNKNOWN_COMMAND_ID_FAIL_CLOSED: bool = True
DISCOVER_ARBITRARY_HOST_EXECUTABLES: bool = False
PATH_BASED_EXECUTABLE_DISCOVERY: bool = False
DYNAMIC_COMMAND_PLUGIN_DISCOVERY: bool = False

NESTED_SHELL_EXPOSED: bool = False
GENERAL_INTERPRETER_ESCAPE_EXPOSED: bool = False

SPECIALIZED_TOOL_BYPASS_VIA_SHELL: bool = False
GIT_BYPASS_VIA_RESTRICTED_SHELL: bool = False
TEST_RUNNER_BYPASS_VIA_RESTRICTED_SHELL: bool = False
WORKSPACE_MUTATION_BYPASS_VIA_RESTRICTED_SHELL: bool = False

WORKTREE_BOUND_CWD: bool = True
CALLER_SUPPLIED_ARBITRARY_CWD: bool = False
CROSS_PROJECT_CWD_FAIL_CLOSED: bool = True
CROSS_WORKTREE_CWD_FAIL_CLOSED: bool = True

PATH_CAPABLE_COMMAND_ARGS_VALIDATED: bool = True

ARG_COUNT_BOUNDED: bool = True
ARG_LENGTH_BOUNDED: bool = True
FREE_FORM_ARGUMENT_LANGUAGE_ALLOWED: bool = False

ENV_DENY_BY_DEFAULT: bool = True
UNBOUNDED_HOST_ENV_INHERITANCE: bool = False
CALLER_CONTROLLED_PATH_LOOKUP: bool = False
RESTRICTED_SHELL_ENV_POLICY: str = "bounded_allowlist_no_unbounded_host_inheritance"
CALLER_SUPPLIED_SECRET_ENV_ALLOWED: bool = False

TIMEOUT_REQUIRED: bool = True
UNBOUNDED_COMMAND_EXECUTION_ALLOWED: bool = False
PROCESS_TREE_TERMINATION_REQUIRED: bool = True

STDOUT_BOUNDED: bool = True
STDERR_BOUNDED: bool = True
TOTAL_PROCESS_OUTPUT_BOUNDED: bool = True
SILENT_OUTPUT_TRUNCATION: bool = False

INTERACTIVE_SHELL: bool = False
PTY_ALLOCATED: bool = False
STDIN_INTERACTIVE_FORWARDING: bool = False
JOB_CONTROL: bool = False

SHELL_INVOCATION_ONE_SHOT: bool = True
PERSISTENT_SHELL_SESSION_CREATED: bool = False

EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_TOOL_RESULT_GOVERNANCE_REUSED: bool = True
NEW_SHELL_RESULT_ONTOLOGY_CREATED: bool = False
SHELL_RESULT_CARD_CREATED: bool = False
SHELL_RESULT_IS_AUTHORITY: bool = False
COMMAND_EXIT_CODE_IS_AUTHORITY: bool = False
OUTPUT_REF_IS_AUTHORITY: bool = False

RESTRICTED_SHELL_DEFAULT_EAGER: bool = False
RESTRICTED_SHELL_PROGRESSIVE_FALLBACK: bool = True

NEW_NETWORK_SUBSYSTEM_CREATED: bool = False
NETWORK_ISOLATION_ENFORCED: bool = False
CALLER_CAN_ENABLE_NETWORK: bool = False
KNOWN_NETWORK_COMMAND_CAPABILITY_EXPOSED: bool = False
NETWORK_POLICY_MODE: str = "fail_closed_no_network_commands_exposed_no_enforcement_claim"

# Authority flags
TOOL_EXPOSURE_IS_SHELL_AUTHORITY: bool = False
WORK_ROLE_IS_SHELL_AUTHORITY: bool = False
SANDBOX_IS_SHELL_AUTHORITY: bool = False
READ_AUTHORITY_IS_SHELL_AUTHORITY: bool = False
WORKSPACE_MUTATION_AUTHORITY_IS_SHELL_AUTHORITY: bool = False
TEST_EXECUTION_AUTHORITY_IS_SHELL_AUTHORITY: bool = False
GIT_AUTHORITY_IS_SHELL_AUTHORITY: bool = False
SHELL_AUTHORITY_OPERATION_BOUND: bool = True
CROSS_OPERATION_AUTHORITY_SUBSTITUTION_FAIL_CLOSED: bool = True
SHELL_AUTHORITY_FACTORY_IS_AUTHORITY_LAUNDERING: bool = False
CALLER_CAN_SELF_MINT_SHELL_AUTHORITY: bool = False
EVIDENCE_IS_AUTHORITY_DECISION: bool = False

# Reuse markers
REUSE_EXISTING_BOUNDED_PROCESS_SEAM: bool = False
# We use bounded shell-specific implementation (mirrors W2 mechanics) without generic runtime
PROCESS_MECHANICS_REUSE_MODE: str = "BOUNDED_SHELL_SPECIFIC_IMPLEMENTATION"
TEST_EXECUTION_PROVIDER_REMAINS_TEST_SPECIFIC: bool = True
NEW_PUBLIC_GENERIC_PROCESS_RUNTIME_CREATED: bool = False
NEW_AUTHORITY_ENGINE_CREATED: bool = False
NEW_SHELL_SESSION_RUNTIME_CREATED: bool = False

# Command exit mapping
COMMAND_NONZERO_EXIT_MAPPING: str = "nonzero_exit_is_success_payload_with_command_nonzero_not_provider_failure"

# Bounds
MAX_COMMAND_ID_LENGTH: int = 64
MAX_ARGS: int = 16
MAX_ARG_LENGTH: int = 256
MAX_TOTAL_ARGV_BYTES: int = 4096
MIN_TIMEOUT_SECONDS: int = 1
MAX_TIMEOUT_SECONDS: int = 30
DEFAULT_TIMEOUT_SECONDS: int = 5
MAX_STDOUT_BYTES: int = 32 * 1024
MAX_STDERR_BYTES: int = 32 * 1024
MAX_TOTAL_OUTPUT_BYTES: int = 64 * 1024

# Allowed env - deny by default, explicit allowlist
ALLOWED_ENV_KEYS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "VIRTUAL_ENV",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "PWD",
})

DANGEROUS_ENV_KEYS: frozenset[str] = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "PYTHONINSPECT",
    "PYTHONSTARTUP",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN",
    "GH_TOKEN",
})

# ---------------------------------------------------------------------------
# Trusted command catalog — bounded, immutable, no PATH discovery
# ---------------------------------------------------------------------------
# Each entry: command_id -> {executable: list[str], max_args, is_path_capable, allowed_options}
# Executables are resolved trusted paths, not caller-supplied.
# No git, no pytest, no rm/cp/mv/sed, no sh/bash/python/node

@dataclass(frozen=True)
class _CatalogEntry:
    executable: tuple[str, ...]
    max_args: int
    is_path_capable: bool
    allowed_options: frozenset[str]
    description: str

_CATALOG: dict[str, _CatalogEntry] = {
    "echo": _CatalogEntry(
        executable=("/bin/echo",),
        max_args=8,
        is_path_capable=False,
        allowed_options=frozenset({"-n"}),
        description="bounded echo residual — no path, no side effect beyond stdout",
    ),
    "ls": _CatalogEntry(
        executable=("/bin/ls",),
        max_args=1,
        is_path_capable=True,
        allowed_options=frozenset(),
        description="bounded ls residual — worktree-scoped listing",
    ),
    "sleep": _CatalogEntry(
        executable=("/bin/sleep",),
        max_args=1,
        is_path_capable=False,
        allowed_options=frozenset(),
        description="bounded sleep for timeout proof — residual",
    ),
}

EXPOSED_COMMAND_IDS: tuple[str, ...] = tuple(sorted(_CATALOG.keys()))
COMMAND_CATALOG_BOUNDED_COUNT: int = len(_CATALOG)

# ---------------------------------------------------------------------------
# Descriptor — structured inputs, no raw shell string
# ---------------------------------------------------------------------------

RESTRICTED_SHELL_DESCRIPTOR: OperationContractDescriptor = OperationContractDescriptor(
    name="restricted_shell.run",
    description="Governed restricted shell fallback (worktree-scoped, bounded argv, no shell)",
    inputs=(
        InputSpec(name="command_id", type="str"),
        InputSpec(name="args", type="list?"),
        InputSpec(name="timeout", type="int?"),
    ),
)
RESTRICTED_SHELL_DESCRIPTOR.validate()

# For convenience, alias
RESTRICTED_SHELL_OPERATION_NAME: str = "restricted_shell.run"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RestrictedShellAuthorityError(ValueError):
    """Missing or invalid trusted restricted-shell authority (fail-closed)."""


class RestrictedShellValidationError(ValueError):
    """Invalid restricted shell input (fail-closed)."""


class RestrictedShellTimeoutError(ValueError):
    """Timeout distinguishable failure."""


# ---------------------------------------------------------------------------
# Authority evidence — bounded value object, not decision engine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RestrictedShellAuthorityEvidence:
    """Trusted operation-authority evidence for one restricted_shell.run invocation."""

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    applicable_policies: tuple[AgentsPolicyCandidate, ...]
    operation: OperationContractDescriptor
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise RestrictedShellAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise RestrictedShellAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.applicable_policies, (tuple, list)):
            raise RestrictedShellAuthorityError("applicable_policies must be tuple or list")
        for idx, p in enumerate(self.applicable_policies):
            if not isinstance(p, AgentsPolicyCandidate):
                raise RestrictedShellAuthorityError(f"applicable_policies[{idx}] must be AgentsPolicyCandidate")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise RestrictedShellAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        if self.operation.name != "restricted_shell.run":
            raise RestrictedShellAuthorityError(f"operation name must be restricted_shell.run, got {self.operation.name!r}")
        self.operation.validate()
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise RestrictedShellAuthorityError("evidence_id must be non-empty string")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "operation": self.operation.name,
            "contract_hash": self.operation.contract_hash(),
            "sandbox_digest": self.sandbox.compute_digest(),
            "handoff_digest": self.handoff.handoff_digest,
            "policy_count": len(self.applicable_policies),
            "policy_digests": sorted([p.content_digest or "" for p in self.applicable_policies]),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def evidence_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def create_restricted_shell_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    applicable_policies: Sequence[AgentsPolicyCandidate],
    operation: OperationContractDescriptor,
    *,
    evidence_id: str | None = None,
) -> RestrictedShellAuthorityEvidence:
    """Create trusted restricted-shell authority evidence.

    Reconciles already-trusted prerequisites mechanically; does not mint
    permission from caller-controlled values.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise RestrictedShellAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise RestrictedShellAuthorityError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(operation, OperationContractDescriptor):
        raise RestrictedShellAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    operation.validate()
    if operation.name != "restricted_shell.run":
        raise RestrictedShellAuthorityError(f"operation must be restricted_shell.run, got {operation.name!r}")
    policies_tuple = tuple(applicable_policies) if applicable_policies is not None else ()
    for p in policies_tuple:
        if not isinstance(p, AgentsPolicyCandidate):
            raise RestrictedShellAuthorityError(f"policy must be AgentsPolicyCandidate, got {type(p).__name__}")
    if len(policies_tuple) > 0:
        resolved = resolve_applicable_policies(policies_tuple, sandbox.project_id)
        policies_tuple = resolved
    if evidence_id is None:
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}"
        evidence_id = "rse-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise RestrictedShellAuthorityError("evidence_id must be non-empty string")
        evidence_id = evidence_id.strip()
        if len(evidence_id) > 128:
            raise RestrictedShellAuthorityError("evidence_id exceeds bound")
        if "/" in evidence_id or "\\" in evidence_id:
            raise RestrictedShellAuthorityError("evidence_id must not contain path separators")
    return RestrictedShellAuthorityEvidence(
        sandbox=sandbox,
        handoff=handoff,
        applicable_policies=policies_tuple,
        operation=operation,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------------------
# Helpers — validation
# ---------------------------------------------------------------------------

def _validate_command_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise RestrictedShellValidationError(f"command_id must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise RestrictedShellValidationError("command_id must be non-empty")
    if len(v) > MAX_COMMAND_ID_LENGTH:
        raise RestrictedShellValidationError(f"command_id length {len(v)} exceeds {MAX_COMMAND_ID_LENGTH}")
    if "\x00" in v:
        raise RestrictedShellValidationError("command_id must not contain NUL")
    if "/" in v or "\\" in v:
        raise RestrictedShellValidationError("command_id must not contain path separators")
    if v not in _CATALOG:
        raise RestrictedShellValidationError(f"command_id {v!r} not in trusted catalog {sorted(_CATALOG.keys())}")
    return v


def _validate_args(command_id: str, value: object, sandbox: WorktreeSandboxBoundary) -> list[str]:
    entry = _CATALOG[command_id]
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise RestrictedShellValidationError(f"args must be a list, got {type(value).__name__}")
    if len(value) > entry.max_args:
        raise RestrictedShellValidationError(f"args count {len(value)} exceeds max {entry.max_args} for {command_id}")
    if len(value) > MAX_ARGS:
        raise RestrictedShellValidationError(f"args count {len(value)} exceeds global max {MAX_ARGS}")
    validated: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or type(item) is not str:
            raise RestrictedShellValidationError(f"args[{idx}] must be a string, got {type(item).__name__}")
        if "\x00" in item:
            raise RestrictedShellValidationError(f"args[{idx}] must not contain NUL")
        if len(item) > MAX_ARG_LENGTH:
            raise RestrictedShellValidationError(f"args[{idx}] length {len(item)} exceeds {MAX_ARG_LENGTH}")
        # No shell expansion: metacharacters are literal argv data, we accept them
        # But we must ensure no caller can inject executable path via arg
        # For path-capable commands, validate path containment
        if item.strip() == "" and item != "":
            raise RestrictedShellValidationError(f"args[{idx}] must not be whitespace-only")
        # For echo/ls/sleep: check option allowlist
        if entry.is_path_capable:
            # For ls: args are paths, if empty no path, else validate as resource
            # For ls we allow 0 or 1 path; if provided, validate containment
            if len(item) > 0 and item.startswith("-"):
                # ls has no allowed options — reject any option
                if item not in entry.allowed_options:
                    raise RestrictedShellValidationError(f"args[{idx}] option {item!r} not allowed for {command_id}")
            else:
                # path arg: validate via resolver if not empty
                # Empty path not allowed for ls (would be argv with empty)
                if not item:
                    raise RestrictedShellValidationError(f"args[{idx}] must be non-empty path")
                # Validate path via resolver (relative, no traversal, no absolute)
                if item.startswith("/"):
                    raise RestrictedShellValidationError(f"args[{idx}] must not be absolute: {item!r}")
                if "\\" in item:
                    raise RestrictedShellValidationError(f"args[{idx}] must not contain backslash")
                if ".." in item.split("/"):
                    raise RestrictedShellValidationError(f"args[{idx}] must not contain '..' (traversal)")
                try:
                    evidence = resolve_worktree_resource(sandbox, item)
                except Exception as exc:
                    raise RestrictedShellValidationError(f"args[{idx}] rejected: {exc}") from exc
                if evidence.project_id != sandbox.project_id:
                    raise RestrictedShellValidationError(f"args[{idx}] cross-project rejected")
                if evidence.worktree_id != sandbox.worktree_id:
                    raise RestrictedShellValidationError(f"args[{idx}] cross-worktree rejected")
                # Symlink escape already checked in resolver; additional check: path must not be symlink
                # Resolver already rejects symlink path
        else:
            # Non-path commands: echo, sleep
            if command_id == "echo":
                if item.startswith("-") and item not in entry.allowed_options:
                    # reject prohibited option
                    raise RestrictedShellValidationError(f"args[{idx}] option {item!r} not allowed for {command_id}")
                # echo args are literal, even if they contain shell metachars like ";", "|", "$", "`" — keep literal
                # No further validation
                pass
            elif command_id == "sleep":
                # sleep arg must be numeric duration
                # Reject shell metachars, but they would be literal strings; we still validate numeric
                if item.startswith("-"):
                    raise RestrictedShellValidationError(f"args[{idx}] option not allowed for sleep")
                # Must be numeric (int or float) string within bound
                try:
                    # Allow "0", "1", "2.5" etc but bound to 0..30
                    val = float(item)
                except ValueError:
                    raise RestrictedShellValidationError(f"args[{idx}] must be numeric duration for sleep, got {item!r}")
                if val < 0 or val > 30:
                    raise RestrictedShellValidationError(f"args[{idx}] duration {val} out of bound 0..30")
                # Also ensure no extra chars like "1; rm"
                # Already validated via float conversion; but "1; echo" would fail float parse -> rejected
                pass
            else:
                # generic: reject options not in allowlist if starts with -
                if item.startswith("-") and item not in entry.allowed_options:
                    raise RestrictedShellValidationError(f"args[{idx}] option {item!r} not allowed for {command_id}")
        validated.append(item)
    # Also validate total argv bytes bound
    total = sum(len(a) for a in validated) + sum(len(p) for p in entry.executable)
    if total > MAX_TOTAL_ARGV_BYTES:
        raise RestrictedShellValidationError(f"total argv bytes {total} exceeds bound {MAX_TOTAL_ARGV_BYTES}")
    return validated


def _validate_timeout(value: object) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise RestrictedShellValidationError(f"timeout must be an int, got {type(value).__name__}")
    if value < MIN_TIMEOUT_SECONDS:
        raise RestrictedShellValidationError(f"timeout {value} below minimum {MIN_TIMEOUT_SECONDS}")
    if value > MAX_TIMEOUT_SECONDS:
        raise RestrictedShellValidationError(f"timeout {value} exceeds maximum {MAX_TIMEOUT_SECONDS}")
    return value


def _build_bounded_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ALLOWED_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            if len(key) > 128 or len(val) > 4096:
                continue
            if "\x00" in val:
                continue
            env[key] = val
    if "PATH" not in env:
        env["PATH"] = "/usr/bin:/bin"
    if "PYTHONUNBUFFERED" not in env:
        env["PYTHONUNBUFFERED"] = "1"
    if "PYTHONIOENCODING" not in env:
        env["PYTHONIOENCODING"] = "utf-8"
    # Remove dangerous keys even if in allowlist? They are not in allowlist, so not included.
    for dk in DANGEROUS_ENV_KEYS:
        env.pop(dk, None)
    return env


def _bounded_truncate(data: bytes, limit: int) -> tuple[bytes, bool]:
    if len(data) <= limit:
        return data, False
    return data[:limit], True


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class BoundedRestrictedShellProvider:
    """Bounded restricted shell provider handling restricted_shell.run.

    Reuses existing ToolProvider seam, consumes trusted
    RestrictedShellAuthorityEvidence. Implements:
      - worktree-bound cwd (never caller-supplied)
      - trusted command catalog (no arbitrary executable, no PATH discovery)
      - argv-style launch (shell=False)
      - bounded env (deny-by-default)
      - timeout + process group tree termination
      - bounded stdout/stderr/total with truthful truncation metadata
    """

    def __init__(self, authority: RestrictedShellAuthorityEvidence) -> None:
        if not isinstance(authority, RestrictedShellAuthorityEvidence):
            raise RestrictedShellAuthorityError(
                f"authority must be RestrictedShellAuthorityEvidence, got {type(authority).__name__}"
            )
        sb = authority.sandbox
        root = Path(sb.worktree_root)
        try:
            if root.is_symlink():
                raise RestrictedShellAuthorityError(f"sandbox root is symlink: {root}")
            if not root.exists():
                raise RestrictedShellAuthorityError(f"sandbox root does not exist: {root}")
            if not root.is_dir():
                raise RestrictedShellAuthorityError(f"sandbox root is not a directory: {root}")
        except OSError as exc:
            raise RestrictedShellAuthorityError(f"sandbox root inaccessible: {exc}") from exc
        try:
            self._root = Path(sb.worktree_root).resolve(strict=True)
        except OSError as exc:
            raise RestrictedShellAuthorityError(f"sandbox root resolve failed: {exc}") from exc
        self._authority = authority
        self._sandbox = sb
        self._handoff = authority.handoff
        self._policies = authority.applicable_policies
        self._operation = authority.operation

    @property
    def authority(self) -> RestrictedShellAuthorityEvidence:
        return self._authority

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self._sandbox

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        try:
            if self._authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_MISSING", "message": "missing restricted shell authority"})
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure(
                    {"code": "OPERATION_MISMATCH", "message": f"request operation {request.operation.name!r} != authority {self._authority.operation.name!r}"}
                )
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            root = Path(self._sandbox.worktree_root)
            try:
                if root.is_symlink() or not root.exists() or not root.is_dir():
                    return ToolResponse.failure({"code": "SANDBOX_STALE", "message": "sandbox root stale or missing"})
                root_canonical = root.resolve(strict=True)
                if root_canonical != self._root:
                    return ToolResponse.failure({"code": "SANDBOX_MISMATCH", "message": "sandbox canonical root mismatch"})
            except OSError as exc:
                return ToolResponse.failure({"code": "SANDBOX_ERROR", "message": f"sandbox revalidation failed: {exc}"})
            if len(self._policies) > 0:
                try:
                    resolve_applicable_policies(self._policies, self._sandbox.project_id)
                except Exception as exc:
                    return ToolResponse.failure({"code": "POLICY_CONTEXT_INVALID", "message": f"policy context invalid: {exc}"})
            if not self._handoff.bounded_scope or not self._handoff.bounded_scope.strip():
                return ToolResponse.failure({"code": "TASK_SCOPE_MISSING", "message": "task scope missing"})
            if request.operation.name == "restricted_shell.run":
                return self._handle_shell(request)
            else:
                return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported: {request.operation.name!r}"})
        except Exception as exc:
            if isinstance(exc, (RestrictedShellValidationError, RestrictedShellAuthorityError, RestrictedShellTimeoutError)):
                return ToolResponse.failure({"code": "RESTRICTED_SHELL_ERROR", "message": str(exc)})
            return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})

    def _handle_shell(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        # Reject raw shell command strings, arbitrary executable path, caller env/cwd
        if "command" in inputs or "shell_command" in inputs or "cmd" in inputs or "executable" in inputs:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "raw shell command not accepted"})
        if "env" in inputs or "environment" in inputs:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "caller supplied env rejected"})
        if "cwd" in inputs or "workdir" in inputs or "path" in inputs:
            # path as free form is rejected, but args may contain path for ls; command_id+args is correct shape
            # However single "path" input not allowed for this operation
            # Check descriptor: we only have command_id, args, timeout
            pass
        # Also reject shell/path injection via extra keys (ToolRequest already rejected unknown inputs, but defensive)
        for forbidden in ("shell", "executable_path", "cwd", "env", "PATH"):
            if forbidden in inputs and forbidden not in ("command_id", "args", "timeout"):
                # ToolRequest.validate_inputs would have already rejected unknown, but be explicit
                if forbidden in ("shell", "executable_path"):
                    return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"forbidden input {forbidden!r}"})
        command_id_val = inputs.get("command_id")
        args_val = inputs.get("args")
        timeout_val = inputs.get("timeout")
        # command_id validation includes catalog check
        try:
            command_id = _validate_command_id(command_id_val)
        except RestrictedShellValidationError as exc:
            msg = str(exc)
            if "not in trusted catalog" in msg:
                return ToolResponse.failure({"code": "UNKNOWN_COMMAND", "message": msg})
            return ToolResponse.failure({"code": "INVALID_COMMAND_ID", "message": msg})
        try:
            args = _validate_args(command_id, args_val, self._sandbox)
        except RestrictedShellValidationError as exc:
            msg = str(exc)
            if "option" in msg.lower():
                return ToolResponse.failure({"code": "ARGUMENT_POLICY_DENIED", "message": msg})
            if "traversal" in msg.lower() or ".." in msg:
                return ToolResponse.failure({"code": "PATH_TRAVERSAL_REJECTED", "message": msg})
            if "absolute" in msg.lower():
                return ToolResponse.failure({"code": "ABSOLUTE_PATH_REJECTED", "message": msg})
            if "cross-project" in msg.lower() or "cross-worktree" in msg.lower():
                return ToolResponse.failure({"code": "CROSS_PROJECT_PATH_REJECTED", "message": msg})
            if "count" in msg.lower() or "exceeds max" in msg.lower():
                return ToolResponse.failure({"code": "ARG_COUNT_BOUNDED_EXCEEDED" if "count" in msg.lower() else "ARG_LENGTH_BOUNDED_EXCEEDED", "message": msg})
            if "length" in msg.lower():
                return ToolResponse.failure({"code": "ARG_LENGTH_BOUNDED_EXCEEDED", "message": msg})
            return ToolResponse.failure({"code": "INVALID_ARGS", "message": msg})
        try:
            timeout = _validate_timeout(timeout_val)
        except RestrictedShellValidationError as exc:
            if timeout_val is None:
                return ToolResponse.failure({"code": "TIMEOUT_REQUIRED", "message": str(exc)})
            return ToolResponse.failure({"code": "INVALID_TIMEOUT", "message": str(exc)})
        # Reject if timeout missing? Descriptor allows optional, but we enforce default so never unbounded
        # Check for missing timeout explicitly? Default already provides value, so not needed

        entry = _CATALOG[command_id]
        argv = list(entry.executable) + args
        # Final check: ensure executable is trusted catalog entry, not caller-supplied
        # Already guaranteed
        env = _build_bounded_env()
        # Caller PATH override rejected — we use bounded env PATH, not caller supplied
        # Ensure no caller env injection (we already rejected env input)

        start = time.monotonic()
        stdout_bytes = b""
        stderr_bytes = b""
        exit_code: int | None = None
        timed_out = False
        duration_ms = 0
        stdout_truncated = False
        stderr_truncated = False
        total_truncated = False

        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(self._root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return ToolResponse.failure({"code": "PROCESS_SPAWN_FAILED", "message": f"executable not found: {exc}"})
        except Exception as exc:
            return ToolResponse.failure({"code": "PROCESS_SPAWN_FAILED", "message": str(exc)})

        try:
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    pgid = os.getpgid(proc.pid)
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except Exception:
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except Exception:
                            pass
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=2)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    try:
                        pgid = os.getpgid(proc.pid)
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except Exception:
                            pass
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    try:
                        stdout_bytes, stderr_bytes = proc.communicate(timeout=2)
                        exit_code = proc.returncode
                    except Exception:
                        stdout_bytes = stdout_bytes or b""
                        stderr_bytes = stderr_bytes or b""
                        exit_code = None
                if exit_code is None:
                    exit_code = -1
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception:
                pass

        if stdout_bytes is None:
            stdout_bytes = b""
        if stderr_bytes is None:
            stderr_bytes = b""

        stdout_bytes, stdout_truncated = _bounded_truncate(stdout_bytes, MAX_STDOUT_BYTES)
        stderr_bytes, stderr_truncated = _bounded_truncate(stderr_bytes, MAX_STDERR_BYTES)
        total_len = len(stdout_bytes) + len(stderr_bytes)
        if total_len > MAX_TOTAL_OUTPUT_BYTES:
            excess = total_len - MAX_TOTAL_OUTPUT_BYTES
            if len(stdout_bytes) >= len(stderr_bytes):
                trim = min(excess, len(stdout_bytes))
                stdout_bytes = stdout_bytes[: len(stdout_bytes) - trim]
                stdout_truncated = True
            else:
                trim = min(excess, len(stderr_bytes))
                stderr_bytes = stderr_bytes[: len(stderr_bytes) - trim]
                stderr_truncated = True
            total_truncated = True
        else:
            total_truncated = stdout_truncated or stderr_truncated

        try:
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        except Exception:
            stdout_text = ""
        try:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        except Exception:
            stderr_text = ""

        if timed_out:
            return ToolResponse.failure(
                {
                    "code": "SHELL_TIMEOUT",
                    "message": f"restricted shell command timed out after {timeout}s",
                    "details": {
                        "command_id": command_id,
                        "args": args,
                        "timeout": timeout,
                        "duration_ms": duration_ms,
                        "exit_code": exit_code,
                        "stdout_truncated": stdout_truncated,
                        "stderr_truncated": stderr_truncated,
                        "total_truncated": total_truncated,
                    },
                }
            )

        payload = {
            "command_id": command_id,
            "args": args,
            "timeout": timeout,
            "cwd": str(self._root),
            "exit_code": exit_code if exit_code is not None else -1,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_ms": duration_ms,
            "stdout_length": len(stdout_bytes),
            "stderr_length": len(stderr_bytes),
            "total_output_length": len(stdout_bytes) + len(stderr_bytes),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "total_truncated": total_truncated,
            "is_timeout": False,
            "project_id": self._sandbox.project_id,
            "worktree_id": self._sandbox.worktree_id,
        }
        try:
            pj_len = len(canonical_json(payload).encode("utf-8"))
            if pj_len > MAX_TOTAL_OUTPUT_BYTES + 4096:
                return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": "payload exceeds bound after truncation"})
        except Exception:
            pass
        return ToolResponse.success(payload)


__all__ = [
    "RESTRICTED_SHELL_DESCRIPTOR",
    "RestrictedShellAuthorityEvidence",
    "create_restricted_shell_authority",
    "BoundedRestrictedShellProvider",
    "EXPOSED_COMMAND_IDS",
    # flags
    "RESTRICTED_SHELL_IS_RESIDUAL_FALLBACK",
    "RESTRICTED_SHELL_PRIMARY_INTERFACE",
    "RAW_SHELL_COMMAND_STRING_ACCEPTED",
    "SHELL_TRUE_USED",
    "ARGV_STYLE_EXECUTION",
    "SHELL_EXPANSION_USED",
    "COMMAND_SUBSTITUTION_SUPPORTED",
    "PIPE_OPERATOR_SUPPORTED",
    "REDIRECTION_OPERATOR_SUPPORTED",
    "COMMAND_CHAINING_SUPPORTED",
    "TRUSTED_COMMAND_CATALOG",
    "COMMAND_CATALOG_BOUNDED",
    "ARBITRARY_EXECUTABLE_SELECTION_ALLOWED",
    "CALLER_SUPPLIED_EXECUTABLE_PATH_ALLOWED",
    "UNKNOWN_COMMAND_ID_FAIL_CLOSED",
    "DISCOVER_ARBITRARY_HOST_EXECUTABLES",
    "PATH_BASED_EXECUTABLE_DISCOVERY",
    "DYNAMIC_COMMAND_PLUGIN_DISCOVERY",
    "NESTED_SHELL_EXPOSED",
    "GENERAL_INTERPRETER_ESCAPE_EXPOSED",
    "SPECIALIZED_TOOL_BYPASS_VIA_SHELL",
    "GIT_BYPASS_VIA_RESTRICTED_SHELL",
    "TEST_RUNNER_BYPASS_VIA_RESTRICTED_SHELL",
    "WORKSPACE_MUTATION_BYPASS_VIA_RESTRICTED_SHELL",
    "WORKTREE_BOUND_CWD",
    "CALLER_SUPPLIED_ARBITRARY_CWD",
    "CROSS_PROJECT_CWD_FAIL_CLOSED",
    "CROSS_WORKTREE_CWD_FAIL_CLOSED",
    "PATH_CAPABLE_COMMAND_ARGS_VALIDATED",
    "ARG_COUNT_BOUNDED",
    "ARG_LENGTH_BOUNDED",
    "FREE_FORM_ARGUMENT_LANGUAGE_ALLOWED",
    "ENV_DENY_BY_DEFAULT",
    "RESTRICTED_SHELL_ENV_POLICY",
    "UNBOUNDED_HOST_ENV_INHERITANCE",
    "CALLER_CONTROLLED_PATH_LOOKUP",
    "CALLER_SUPPLIED_SECRET_ENV_ALLOWED",
    "TIMEOUT_REQUIRED",
    "UNBOUNDED_COMMAND_EXECUTION_ALLOWED",
    "PROCESS_TREE_TERMINATION_REQUIRED",
    "STDOUT_BOUNDED",
    "STDERR_BOUNDED",
    "TOTAL_PROCESS_OUTPUT_BOUNDED",
    "SILENT_OUTPUT_TRUNCATION",
    "INTERACTIVE_SHELL",
    "PTY_ALLOCATED",
    "STDIN_INTERACTIVE_FORWARDING",
    "JOB_CONTROL",
    "SHELL_INVOCATION_ONE_SHOT",
    "PERSISTENT_SHELL_SESSION_CREATED",
    "EXISTING_TOOL_PROVIDER_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "EXISTING_TOOL_RESULT_GOVERNANCE_REUSED",
    "NEW_SHELL_RESULT_ONTOLOGY_CREATED",
    "SHELL_RESULT_CARD_CREATED",
    "SHELL_RESULT_IS_AUTHORITY",
    "COMMAND_EXIT_CODE_IS_AUTHORITY",
    "OUTPUT_REF_IS_AUTHORITY",
    "RESTRICTED_SHELL_DEFAULT_EAGER",
    "RESTRICTED_SHELL_PROGRESSIVE_FALLBACK",
    "NEW_NETWORK_SUBSYSTEM_CREATED",
    "NETWORK_ISOLATION_ENFORCED",
    "CALLER_CAN_ENABLE_NETWORK",
    "KNOWN_NETWORK_COMMAND_CAPABILITY_EXPOSED",
    "NETWORK_POLICY_MODE",
    "TOOL_EXPOSURE_IS_SHELL_AUTHORITY",
    "WORK_ROLE_IS_SHELL_AUTHORITY",
    "SANDBOX_IS_SHELL_AUTHORITY",
    "READ_AUTHORITY_IS_SHELL_AUTHORITY",
    "WORKSPACE_MUTATION_AUTHORITY_IS_SHELL_AUTHORITY",
    "TEST_EXECUTION_AUTHORITY_IS_SHELL_AUTHORITY",
    "GIT_AUTHORITY_IS_SHELL_AUTHORITY",
    "SHELL_AUTHORITY_OPERATION_BOUND",
    "CROSS_OPERATION_AUTHORITY_SUBSTITUTION_FAIL_CLOSED",
    "SHELL_AUTHORITY_FACTORY_IS_AUTHORITY_LAUNDERING",
    "CALLER_CAN_SELF_MINT_SHELL_AUTHORITY",
    "EVIDENCE_IS_AUTHORITY_DECISION",
    "PROCESS_MECHANICS_REUSE_MODE",
    "TEST_EXECUTION_PROVIDER_REMAINS_TEST_SPECIFIC",
    "NEW_PUBLIC_GENERIC_PROCESS_RUNTIME_CREATED",
    "COMMAND_NONZERO_EXIT_MAPPING",
    "MAX_STDOUT_BYTES",
    "MAX_STDERR_BYTES",
    "MAX_TOTAL_OUTPUT_BYTES",
    "_CATALOG",
]
