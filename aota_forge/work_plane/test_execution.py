"""Governed Test Execution — S2 M3-W2.

Structured test operation:

    trusted sandbox + task scope + policy + operation authority
        ↓
    bounded test.run Tool invocation (argv-style, no shell)
        ↓
    existing ToolResponse
        ↓
    existing M2 Tool Result Governance projection

Invariants
----------
* TEST_EXECUTION_IS_RESTRICTED_SHELL=no
* RAW_SHELL_COMMAND_ACCEPTED=no
* SHELL_TRUE_USED=no
* ARBITRARY_EXECUTABLE_SELECTION_ALLOWED=no
* TEST_EXECUTION_AUTHORITY_REQUIRED=yes
* READ_AUTHORITY_IS_TEST_EXECUTION_AUTHORITY=no
* TOOL_EXPOSURE_IS_TEST_EXECUTION_AUTHORITY=no
* WORKTREE_BOUND_CWD=yes
* CALLER_SUPPLIED_ARBITRARY_CWD=no
* CROSS_PROJECT_CWD_FAIL_CLOSED=yes
* TEST_TARGET_PROJECT_WORKTREE_BOUND=yes
* ABSOLUTE_FOREIGN_TEST_TARGET_ALLOWED=no
* PATH_TRAVERSAL_TEST_TARGET_FAIL_CLOSED=yes
* UNBOUNDED_HOST_ENV_INHERITANCE=no
* TIMEOUT_REQUIRED=yes
* UNBOUNDED_TEST_PROCESS_ALLOWED=no
* PROCESS_TREE_TERMINATION_REQUIRED=yes
* STDOUT_BOUNDED=yes
* STDERR_BOUNDED=yes
* TOTAL_PROCESS_OUTPUT_BOUNDED=yes
* EXISTING_TOOL_RESPONSE_REUSED=yes
* EXISTING_TOOL_RESULT_GOVERNANCE_REUSED=yes
* TEST_EXECUTION_IS_READ_ONLY_OPERATION=no (tests may write temp/cache inside worktree)
* GENERAL_WORKSPACE_MUTATION_PROVIDER_IMPLEMENTED_IN_W2=no
* GIT_OPERATION_IMPLEMENTED_IN_W2=no
* NETWORK_SUBSYSTEM_CREATED=no
* SHELL_EXPANSION_USED=no
* PROCESS_ISOLATION_IMPLEMENTED=trusted_cwd+bounded_env+new_process_group+timeout+tree_termination

No generic shell, no interactive shell, no new state machine/journal.

Reuse
-----
* WorktreeSandboxBoundary (M1 W1)
* ProjectBoundResourceResolver / resolve_worktree_resource (M1 W2)
* OperationContractDescriptor (read-only -> but test.run is not read-only)
* ToolProvider / ToolRequest / ToolResponse unchanged
* AgentsPolicyCandidate + resolve_applicable_policies (S1 M2 W2) as policy context input
* TaskHandoff as scope input
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate, resolve_applicable_policies
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_resources import resolve_worktree_resource
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Public invariant flags (for tests / downstream seam)
# ---------------------------------------------------------------------------

TEST_EXECUTION_IS_RESTRICTED_SHELL: bool = False
RAW_SHELL_COMMAND_ACCEPTED: bool = False
SHELL_TRUE_USED: bool = False
ARBITRARY_EXECUTABLE_SELECTION_ALLOWED: bool = False

TEST_EXECUTION_AUTHORITY_REQUIRED: bool = True
READ_AUTHORITY_IS_TEST_EXECUTION_AUTHORITY: bool = False
TOOL_EXPOSURE_IS_TEST_EXECUTION_AUTHORITY: bool = False

WORKTREE_BOUND_CWD: bool = True
CALLER_SUPPLIED_ARBITRARY_CWD: bool = False
CROSS_PROJECT_CWD_FAIL_CLOSED: bool = True

TEST_TARGET_PROJECT_WORKTREE_BOUND: bool = True
ABSOLUTE_FOREIGN_TEST_TARGET_ALLOWED: bool = False
PATH_TRAVERSAL_TEST_TARGET_FAIL_CLOSED: bool = True

UNBOUNDED_HOST_ENV_INHERITANCE: bool = False
# Explicit bounded env policy truthfully reported
TEST_ENV_POLICY: str = "bounded_allowlist_no_unbounded_host_inheritance"
# Details for TEST_ENV_POLICY output
PROCESS_ISOLATION_IMPLEMENTED: str = "trusted_cwd+bounded_env+new_process_group+timeout+tree_termination"
RESTRICTED_SHELL_IMPLEMENTED_IN_W2: bool = False
GENERAL_WORKSPACE_MUTATION_PROVIDER_IMPLEMENTED_IN_W2: bool = False
GIT_OPERATION_IMPLEMENTED_IN_W2: bool = False
NETWORK_SUBSYSTEM_CREATED: bool = False
TEST_EXECUTION_IS_READ_ONLY_OPERATION: bool = False

TIMEOUT_REQUIRED: bool = True
UNBOUNDED_TEST_PROCESS_ALLOWED: bool = False
PROCESS_TREE_TERMINATION_REQUIRED: bool = True

STDOUT_BOUNDED: bool = True
STDERR_BOUNDED: bool = True
TOTAL_PROCESS_OUTPUT_BOUNDED: bool = True

EXISTING_TOOL_PROVIDER_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
EXISTING_TOOL_RESULT_GOVERNANCE_REUSED: bool = True

SHELL_EXPANSION_USED: bool = False
COMMAND_SUBSTITUTION_POSSIBLE: bool = False
PIPE_REDIRECTION_STRING_PARSING: bool = False

# Test failure mapping
TEST_FAILURE_MAPPING: str = "nonzero_exit_is_success_payload_with_failed_tests_not_provider_failure"

# Timeout failure distinguishable
TIMEOUT_DISTINGUISHABLE: bool = True

# Reuse markers
NEW_PROCESS_STATE_MACHINE_CREATED: bool = False
NEW_PROCESS_JOURNAL_CREATED: bool = False
NEW_TOOL_RESULT_CARD_CREATED: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_RUNNER_NAME_LENGTH: int = 64
MAX_TEST_TARGETS: int = 16
MAX_TARGET_LENGTH: int = 512
MAX_EXTRA_ARGS: int = 32
MAX_ARG_LENGTH: int = 256
MIN_TIMEOUT_SECONDS: int = 1
MAX_TIMEOUT_SECONDS: int = 300
DEFAULT_TIMEOUT_SECONDS: int = 30
MAX_STDOUT_BYTES: int = 32 * 1024
MAX_STDERR_BYTES: int = 32 * 1024
MAX_TOTAL_OUTPUT_BYTES: int = 64 * 1024

# Runner catalog — source-backed trusted executable identity
# Only runners in this catalog are allowed. No arbitrary path.
TRUSTED_RUNNER_CATALOG: dict[str, list[str]] = {
    "pytest": [sys.executable, "-m", "pytest"],
}

# Bounded env allowlist — host env not blindly inherited
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

# Dangerous keys that must never be caller-supplied even if in allowlist override
DANGEROUS_ENV_KEYS: frozenset[str] = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "PYTHONINSPECT",
    "PYTHONSTARTUP",
})

TEST_ENV_KEYS_BOUNDED: bool = True
DANGEROUS_CALLER_OVERRIDES_REJECTED: bool = True

# ---------------------------------------------------------------------------
# Descriptor — canonical YAML projection (thin compatibility reference)
# ---------------------------------------------------------------------------
# Single semantic authority is .aota/contracts/operations.yaml via loader.
# TOOL_SCHEMA_SECOND_AUTHORITY=no

def _load_canonical_descriptor(name: str) -> OperationContractDescriptor:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map

    root = discover_canonical_project_root()
    return load_operation_descriptor_map(root)[name]


TEST_RUN_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("test.run")

# Validate descriptor at import time: note read_write default is read, but test.run is not read-only.
# For test execution we explicitly set read_write to read to keep descriptor simple; side-effect semantics
# are documented via is_read_only=false flag, not via descriptor classification yet (no mutation runtime).
# The descriptor itself is still used as operation identity; isolation is via authority evidence.
# Ensure it's valid.
TEST_RUN_DESCRIPTOR.validate()

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestExecutionAuthorityError(ValueError):
    """Missing or invalid trusted test execution authority (fail-closed)."""


class TestExecutionValidationError(ValueError):
    """Invalid test execution input (fail-closed)."""


class TestExecutionTimeoutError(ValueError):
    """Timeout as distinguishable failure."""


# ---------------------------------------------------------------------------
# Authority evidence — bounded value object, not decision engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestExecutionAuthorityEvidence:
    """Trusted operation-authority evidence for one test.run invocation."""

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    applicable_policies: tuple[AgentsPolicyCandidate, ...]
    operation: OperationContractDescriptor
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise TestExecutionAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise TestExecutionAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.applicable_policies, (tuple, list)):
            raise TestExecutionAuthorityError("applicable_policies must be tuple or list")
        for idx, p in enumerate(self.applicable_policies):
            if not isinstance(p, AgentsPolicyCandidate):
                raise TestExecutionAuthorityError(f"applicable_policies[{idx}] must be AgentsPolicyCandidate")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise TestExecutionAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        if self.operation.name != "test.run":
            raise TestExecutionAuthorityError(f"operation name must be test.run, got {self.operation.name!r}")
        self.operation.validate()
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise TestExecutionAuthorityError("evidence_id must be non-empty string")

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


def create_test_execution_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    applicable_policies: Sequence[AgentsPolicyCandidate],
    operation: OperationContractDescriptor,
    *,
    evidence_id: str | None = None,
) -> TestExecutionAuthorityEvidence:
    """Create trusted test execution authority evidence."""
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise TestExecutionAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise TestExecutionAuthorityError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if not isinstance(operation, OperationContractDescriptor):
        raise TestExecutionAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    operation.validate()
    if operation.name != "test.run":
        raise TestExecutionAuthorityError(f"operation must be test.run, got {operation.name!r}")
    policies_tuple = tuple(applicable_policies) if applicable_policies is not None else ()
    for p in policies_tuple:
        if not isinstance(p, AgentsPolicyCandidate):
            raise TestExecutionAuthorityError(f"policy must be AgentsPolicyCandidate, got {type(p).__name__}")
    if len(policies_tuple) > 0:
        # Reuse S1 deterministic applicability to ensure policy context is valid
        resolved = resolve_applicable_policies(policies_tuple, sandbox.project_id)
        policies_tuple = resolved
    if evidence_id is None:
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}"
        evidence_id = "te-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise TestExecutionAuthorityError("evidence_id must be non-empty string")
        evidence_id = evidence_id.strip()
        if len(evidence_id) > 128:
            raise TestExecutionAuthorityError("evidence_id exceeds bound")
        if "/" in evidence_id or "\\" in evidence_id:
            raise TestExecutionAuthorityError("evidence_id must not contain path separators")
    return TestExecutionAuthorityEvidence(
        sandbox=sandbox,
        handoff=handoff,
        applicable_policies=policies_tuple,
        operation=operation,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------------------
# Helpers — validation
# ---------------------------------------------------------------------------


def _validate_runner(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TestExecutionValidationError(f"runner must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise TestExecutionValidationError("runner must be non-empty")
    if len(v) > MAX_RUNNER_NAME_LENGTH:
        raise TestExecutionValidationError(f"runner length {len(v)} exceeds {MAX_RUNNER_NAME_LENGTH}")
    if "\x00" in v:
        raise TestExecutionValidationError("runner must not contain NUL")
    if "/" in v or "\\" in v:
        raise TestExecutionValidationError("runner must not contain path separators (use catalog name)")
    if v not in TRUSTED_RUNNER_CATALOG:
        raise TestExecutionValidationError(f"runner {v!r} not in trusted catalog {sorted(TRUSTED_RUNNER_CATALOG.keys())}")
    return v


def _validate_targets(value: object, sandbox: WorktreeSandboxBoundary) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise TestExecutionValidationError(f"targets must be a list, got {type(value).__name__}")
    if len(value) == 0:
        raise TestExecutionValidationError("targets must be non-empty")
    if len(value) > MAX_TEST_TARGETS:
        raise TestExecutionValidationError(f"targets count {len(value)} exceeds max {MAX_TEST_TARGETS}")
    validated: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(value):
        if not isinstance(item, str) or type(item) is not str:
            raise TestExecutionValidationError(f"targets[{idx}] must be a string, got {type(item).__name__}")
        if "\x00" in item:
            raise TestExecutionValidationError(f"targets[{idx}] must not contain NUL")
        if item.strip() != item:
            raise TestExecutionValidationError(f"targets[{idx}] must not have leading/trailing whitespace")
        if not item:
            raise TestExecutionValidationError(f"targets[{idx}] must be non-empty")
        if len(item) > MAX_TARGET_LENGTH:
            raise TestExecutionValidationError(f"targets[{idx}] length exceeds {MAX_TARGET_LENGTH}")
        if item.startswith("/"):
            raise TestExecutionValidationError(f"targets[{idx}] must not be absolute: {item!r}")
        if "\\" in item:
            raise TestExecutionValidationError(f"targets[{idx}] must not contain backslash")
        if ".." in item.split("/"):
            raise TestExecutionValidationError(f"targets[{idx}] must not contain '..' (traversal)")
        if "//" in item:
            raise TestExecutionValidationError(f"targets[{idx}] must not contain '//'")
        # Reject foreign absolute after resolver, but also do containment via resolver
        try:
            evidence = resolve_worktree_resource(sandbox, item)
        except Exception as exc:
            raise TestExecutionValidationError(f"targets[{idx}] rejected: {exc}") from exc
        if evidence.project_id != sandbox.project_id:
            raise TestExecutionValidationError(f"targets[{idx}] cross-project rejected")
        if evidence.worktree_id != sandbox.worktree_id:
            raise TestExecutionValidationError(f"targets[{idx}] cross-worktree rejected")
        # canonical_path containment already ensured via resolver's symlink + canonical checks
        if item in seen:
            continue
        seen.add(item)
        validated.append(item)
    return validated


def _validate_extra_args(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise TestExecutionValidationError(f"extra_args must be a list, got {type(value).__name__}")
    if len(value) > MAX_EXTRA_ARGS:
        raise TestExecutionValidationError(f"extra_args count {len(value)} exceeds {MAX_EXTRA_ARGS}")
    validated: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or type(item) is not str:
            raise TestExecutionValidationError(f"extra_args[{idx}] must be a string, got {type(item).__name__}")
        if "\x00" in item:
            raise TestExecutionValidationError(f"extra_args[{idx}] must not contain NUL")
        if len(item) > MAX_ARG_LENGTH:
            raise TestExecutionValidationError(f"extra_args[{idx}] length exceeds {MAX_ARG_LENGTH}")
        # Shell metachars remain ordinary argv content — we accept them but never interpret as shell
        # So we do NOT reject ";" "&" "|" "$" "`" etc. They are just string values.
        # But we reject leading/trailing whitespace border to avoid confusion
        # Allow but keep bounded
        validated.append(item)
    return validated


def _validate_timeout(value: object) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise TestExecutionValidationError(f"timeout must be an int, got {type(value).__name__}")
    if value < MIN_TIMEOUT_SECONDS:
        raise TestExecutionValidationError(f"timeout {value} below minimum {MIN_TIMEOUT_SECONDS}")
    if value > MAX_TIMEOUT_SECONDS:
        raise TestExecutionValidationError(f"timeout {value} exceeds maximum {MAX_TIMEOUT_SECONDS}")
    if value <= 0:
        raise TestExecutionValidationError("timeout must be positive")
    return value


def _build_bounded_env() -> dict[str, str]:
    """Construct bounded environment from allowlist, not unbounded host inheritance."""
    env: dict[str, str] = {}
    for key in ALLOWED_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            # bound key length and value length
            if len(key) > 128 or len(val) > 4096:
                continue
            if "\x00" in val:
                continue
            env[key] = val
    # Ensure minimal required
    if "PATH" not in env:
        env["PATH"] = "/usr/bin:/bin"
    if "PYTHONUNBUFFERED" not in env:
        env["PYTHONUNBUFFERED"] = "1"
    if "PYTHONIOENCODING" not in env:
        env["PYTHONIOENCODING"] = "utf-8"
    # Truthfully report we do not inherit arbitrary host env
    return env


def _bounded_truncate(data: bytes, limit: int) -> tuple[bytes, bool]:
    if len(data) <= limit:
        return data, False
    return data[:limit], True

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class BoundedTestExecutionToolProvider:
    """Bounded test execution provider handling test.run.

    Reuses existing ToolProvider seam, consumes trusted TestExecutionAuthorityEvidence.
    Implements sandboxed process execution with:
      - worktree-bound cwd (never caller-supplied)
      - trusted runner catalog (no arbitrary executable)
      - argv-style launch (shell=False)
      - bounded env
      - timeout + process group tree termination
      - bounded stdout/stderr/total
    """

    TEST_EXECUTION_IS_RESTRICTED_SHELL: bool = False

    def __init__(self, authority: TestExecutionAuthorityEvidence) -> None:
        if not isinstance(authority, TestExecutionAuthorityEvidence):
            raise TestExecutionAuthorityError(
                f"authority must be TestExecutionAuthorityEvidence, got {type(authority).__name__}"
            )
        sb = authority.sandbox
        root = Path(sb.worktree_root)
        try:
            if root.is_symlink():
                raise TestExecutionAuthorityError(f"sandbox root is symlink: {root}")
            if not root.exists():
                raise TestExecutionAuthorityError(f"sandbox root does not exist: {root}")
            if not root.is_dir():
                raise TestExecutionAuthorityError(f"sandbox root is not a directory: {root}")
        except OSError as exc:
            raise TestExecutionAuthorityError(f"sandbox root inaccessible: {exc}") from exc
        try:
            self._root = Path(sb.worktree_root).resolve(strict=True)
        except OSError as exc:
            raise TestExecutionAuthorityError(f"sandbox root resolve failed: {exc}") from exc
        self._authority = authority
        self._sandbox = sb
        self._handoff = authority.handoff
        self._policies = authority.applicable_policies
        self._operation = authority.operation

    @property
    def authority(self) -> TestExecutionAuthorityEvidence:
        return self._authority

    @property
    def sandbox(self) -> WorktreeSandboxBoundary:
        return self._sandbox

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        try:
            if self._authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_MISSING", "message": "missing test execution authority"})
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure(
                    {"code": "OPERATION_MISMATCH", "message": f"request operation {request.operation.name!r} != authority {self._authority.operation.name!r}"}
                )
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            # Revalidate sandbox root
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
            if request.operation.name == "test.run":
                return self._handle_test_run(request)
            else:
                return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported: {request.operation.name!r}"})
        except Exception as exc:
            if isinstance(exc, (TestExecutionValidationError, TestExecutionAuthorityError, TestExecutionTimeoutError)):
                return ToolResponse.failure({"code": "TEST_EXECUTION_ERROR", "message": str(exc)})
            return ToolResponse.failure({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})

    def _handle_test_run(self, request: ToolRequest) -> ToolResponse:
        inputs = request.inputs
        # Required inputs validated via descriptor but re-validate defensively
        runner_val = inputs.get("runner")
        targets_val = inputs.get("targets")
        extra_args_val = inputs.get("extra_args")
        timeout_val = inputs.get("timeout")

        # Reject raw shell command strings
        if "command" in inputs or "shell_command" in inputs or "cmd" in inputs:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "raw shell command not accepted"})

        try:
            runner = _validate_runner(runner_val)
        except TestExecutionValidationError as exc:
            return ToolResponse.failure({"code": "INVALID_RUNNER", "message": str(exc)})
        try:
            targets = _validate_targets(targets_val, self._sandbox)
        except TestExecutionValidationError as exc:
            # Distinguish traversal vs foreign vs generic
            msg = str(exc)
            if "traversal" in msg or ".." in msg:
                return ToolResponse.failure({"code": "PATH_TRAVERSAL_REJECTED", "message": msg})
            if "cross-project" in msg or "cross-worktree" in msg or "foreign" in msg:
                return ToolResponse.failure({"code": "CROSS_PROJECT_TARGET_REJECTED", "message": msg})
            if "absolute" in msg:
                return ToolResponse.failure({"code": "ABSOLUTE_TARGET_REJECTED", "message": msg})
            return ToolResponse.failure({"code": "INVALID_TARGET", "message": msg})
        try:
            extra_args = _validate_extra_args(extra_args_val)
        except TestExecutionValidationError as exc:
            return ToolResponse.failure({"code": "INVALID_EXTRA_ARGS", "message": str(exc)})
        try:
            timeout = _validate_timeout(timeout_val)
        except TestExecutionValidationError as exc:
            return ToolResponse.failure({"code": "INVALID_TIMEOUT", "message": str(exc)})

        # Build argv from trusted catalog — never caller-supplied arbitrary executable
        runner_argv = list(TRUSTED_RUNNER_CATALOG[runner])
        # Append targets — each is worktree-relative path, but we pass as is; pytest will resolve relative to cwd=worktree root
        # Ensure targets exist as files/dirs? They were validated via resolver existence may be missing but resolver tells existence.
        # For test execution we require they be resolvable but allow missing? Better to require existence for determinism.
        # Check that each target was validated via resolver which already ensures not escaping but does not require existence?
        # We already validated via resolver; if missing, we still would have evidence.kind=="missing" but resolver doesn't fail.
        # We should check existence: if missing, return failure
        for t in targets:
            try:
                ev = resolve_worktree_resource(self._sandbox, t)
                if not ev.exists:
                    return ToolResponse.failure({"code": "TARGET_NOT_FOUND", "message": f"target not found: {t!r}"})
            except Exception as exc:
                return ToolResponse.failure({"code": "INVALID_TARGET", "message": str(exc)})

        argv = runner_argv + targets + extra_args
        # Final argv bound check
        total_arg_len = sum(len(a) for a in argv)
        if total_arg_len > 4096:
            return ToolResponse.failure({"code": "ARGV_BOUNDED_EXCEEDED", "message": "argv total exceeds bound"})
        # Build bounded env
        env = _build_bounded_env()
        # Reject dangerous overrides if any were supplied (we don't accept env input, but be explicit)
        for dk in DANGEROUS_ENV_KEYS:
            if dk in env and dk not in ALLOWED_ENV_KEYS:
                env.pop(dk, None)

        # Launch with process group isolation, bounded output, timeout, tree termination
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
            # Use start_new_session for process group (POSIX). shell=False by construction.
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
            return ToolResponse.failure({"code": "RUNNER_NOT_FOUND", "message": f"runner executable not found: {exc}"})
        except Exception as exc:
            return ToolResponse.failure({"code": "PROCESS_SPAWN_FAILED", "message": str(exc)})

        try:
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                # Terminate entire process tree via process group
                try:
                    # Try killpg if supported
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
                # Give brief grace then kill
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
                # Ensure exit_code is set to indicate timeout
                if exit_code is None:
                    exit_code = -1
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            # Ensure process terminated
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception:
                pass

        # stdout_bytes / stderr_bytes may be None if not captured
        if stdout_bytes is None:
            stdout_bytes = b""
        if stderr_bytes is None:
            stderr_bytes = b""

        # Bounded output — truncate truthfully, never hold unbounded
        stdout_bytes, stdout_truncated = _bounded_truncate(stdout_bytes, MAX_STDOUT_BYTES)
        stderr_bytes, stderr_truncated = _bounded_truncate(stderr_bytes, MAX_STDERR_BYTES)
        # Combined bound check
        total_len = len(stdout_bytes) + len(stderr_bytes)
        if total_len > MAX_TOTAL_OUTPUT_BYTES:
            # Need to truncate further to fit total bound — truncate larger side first
            # Simple: truncate both proportionally to fit
            excess = total_len - MAX_TOTAL_OUTPUT_BYTES
            # Trim stdout first if larger
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

        # Decode with replace for payload, but keep bytes bound
        try:
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        except Exception:
            stdout_text = ""
        try:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        except Exception:
            stderr_text = ""

        if timed_out:
            # Distinguishable timeout failure — not tests failed, not authority denied, not invalid input
            return ToolResponse.failure(
                {
                    "code": "TEST_TIMEOUT",
                    "message": f"test execution timed out after {timeout}s",
                    "details": {
                        "runner": runner,
                        "targets": targets,
                        "timeout": timeout,
                        "duration_ms": duration_ms,
                        "exit_code": exit_code,
                        "stdout_truncated": stdout_truncated,
                        "stderr_truncated": stderr_truncated,
                        "total_truncated": total_truncated,
                    },
                }
            )

        # Non-timeout: distinguish provider failure vs test suite failure
        # If exit_code !=0 and process launched successfully, it's test failure payload not provider failure
        # Provider failure is only non-launch or timeout cases already handled
        payload = {
            "runner": runner,
            "targets": targets,
            "extra_args": extra_args,
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
            "tests_passed": exit_code == 0,
            "project_id": self._sandbox.project_id,
            "worktree_id": self._sandbox.worktree_id,
        }
        # Enforce final payload bound via canonical_json size
        try:
            pj_len = len(canonical_json(payload).encode("utf-8"))
            if pj_len > MAX_TOTAL_OUTPUT_BYTES + 4096:
                # Should not happen due to truncation, but fail-closed
                return ToolResponse.failure({"code": "OUTPUT_BOUNDED_EXCEEDED", "message": "payload exceeds bound after truncation"})
        except Exception:
            pass
        return ToolResponse.success(payload)


__all__ = [
    "TEST_RUN_DESCRIPTOR",
    "TestExecutionAuthorityEvidence",
    "create_test_execution_authority",
    "BoundedTestExecutionToolProvider",
    "TRUSTED_RUNNER_CATALOG",
    "TEST_ENV_POLICY",
    "PROCESS_ISOLATION_IMPLEMENTED",
    "TEST_FAILURE_MAPPING",
    "MAX_STDOUT_BYTES",
    "MAX_STDERR_BYTES",
    "MAX_TOTAL_OUTPUT_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "MIN_TIMEOUT_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ALLOWED_ENV_KEYS",
    "DANGEROUS_ENV_KEYS",
    # flags
    "TEST_EXECUTION_IS_RESTRICTED_SHELL",
    "RAW_SHELL_COMMAND_ACCEPTED",
    "SHELL_TRUE_USED",
    "ARBITRARY_EXECUTABLE_SELECTION_ALLOWED",
    "TEST_EXECUTION_AUTHORITY_REQUIRED",
    "READ_AUTHORITY_IS_TEST_EXECUTION_AUTHORITY",
    "TOOL_EXPOSURE_IS_TEST_EXECUTION_AUTHORITY",
    "WORKTREE_BOUND_CWD",
    "CALLER_SUPPLIED_ARBITRARY_CWD",
    "CROSS_PROJECT_CWD_FAIL_CLOSED",
    "TEST_TARGET_PROJECT_WORKTREE_BOUND",
    "ABSOLUTE_FOREIGN_TEST_TARGET_ALLOWED",
    "PATH_TRAVERSAL_TEST_TARGET_FAIL_CLOSED",
    "UNBOUNDED_HOST_ENV_INHERITANCE",
    "TIMEOUT_REQUIRED",
    "UNBOUNDED_TEST_PROCESS_ALLOWED",
    "PROCESS_TREE_TERMINATION_REQUIRED",
    "STDOUT_BOUNDED",
    "STDERR_BOUNDED",
    "TOTAL_PROCESS_OUTPUT_BOUNDED",
    "EXISTING_TOOL_PROVIDER_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "EXISTING_TOOL_RESULT_GOVERNANCE_REUSED",
    "SHELL_EXPANSION_USED",
    "COMMAND_SUBSTITUTION_POSSIBLE",
    "PIPE_REDIRECTION_STRING_PARSING",
    "TEST_EXECUTION_IS_READ_ONLY_OPERATION",
    "RESTRICTED_SHELL_IMPLEMENTED_IN_W2",
    "GENERAL_WORKSPACE_MUTATION_PROVIDER_IMPLEMENTED_IN_W2",
    "GIT_OPERATION_IMPLEMENTED_IN_W2",
    "NETWORK_SUBSYSTEM_CREATED",
]
