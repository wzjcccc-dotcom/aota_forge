"""Tool Usage Observation Hook — S2 M4-W3.

Thin non-authoritative bounded observation projection for governed Tool invocations.

    governed Tool invocation/result
        ↓
    bounded non-authoritative usage observation
        ↓
    injected callback/hook

Invariants
----------
* TOOL_USAGE_OBSERVATION_IS_AUTHORITY=no
* OBSERVATION_IS_CANONICAL_RESULT=no
* OBSERVATION_IS_OPERATION_AUTHORITY=no
* OBSERVATION_DIGEST_IS_AUTHORITY=no
* TOOL_USAGE_OBSERVATION_BOUNDED=yes
* TOOL_USAGE_OBSERVATION_DETERMINISTIC=yes
* OBSERVATION_COUNT_PER_INVOCATION_BOUNDED=yes (exactly one per invoke, no recursion)
* OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT=yes
* SUCCESS_OBSERVATION_ONLY_AFTER_RESULT_KNOWN=yes
* SIDE_EFFECT_OBSERVATION_REUSES_EXISTING_RESULT_SEMANTICS=yes
* TOOL_FAILURE_OBSERVATION_TRUTHFUL=yes
* RAW_TOOL_INPUT_CAPTURED=no
* RAW_TOOL_OUTPUT_CAPTURED=no
* SECRET_ENV_CAPTURED=no
* TELEMETRY_STORE_CREATED=no
* ANALYTICS_CREATED=no
* EVENT_STORE_CREATED=no
* NEW_EXECUTION_EVENT_TYPE_CREATED=no
* S6_OWNERSHIP_PRESERVED=yes

S2-local value object — NOT a new ExecutionEventType.
Reuses S1 ExecutionEvent contract patterns (bounded immutable, deterministic) but does
not extend ExecutionEventType enum.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Public invariant flags
# ---------------------------------------------------------------------------

TOOL_USAGE_OBSERVATION_IS_AUTHORITY: bool = False
OBSERVATION_IS_CANONICAL_RESULT: bool = False
OBSERVATION_IS_OPERATION_AUTHORITY: bool = False
OBSERVATION_DIGEST_IS_AUTHORITY: bool = False
TOOL_USAGE_OBSERVATION_BOUNDED: bool = True
TOOL_USAGE_OBSERVATION_DETERMINISTIC: bool = True
OBSERVATION_COUNT_PER_INVOCATION_BOUNDED: bool = True
OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT: bool = True
SUCCESS_OBSERVATION_ONLY_AFTER_RESULT_KNOWN: bool = True
SIDE_EFFECT_OBSERVATION_REUSES_EXISTING_RESULT_SEMANTICS: bool = True
TOOL_FAILURE_OBSERVATION_TRUTHFUL: bool = True
RAW_TOOL_INPUT_CAPTURED: bool = False
RAW_TOOL_OUTPUT_CAPTURED: bool = False
SECRET_ENV_CAPTURED: bool = False
TELEMETRY_STORE_CREATED: bool = False
EVENT_STORE_CREATED: bool = False
ANALYTICS_CREATED: bool = False
METRICS_DATABASE_CREATED: bool = False
EXPORTER_CREATED: bool = False
S6_OWNERSHIP_PRESERVED: bool = True
NEW_EXECUTION_EVENT_TYPE_CREATED: bool = False
TOOL_PROVIDER_CONTRACT_CHANGED: bool = False
TOOL_RESPONSE_CONTRACT_CHANGED: bool = False
OPERATION_DESCRIPTOR_CONTRACT_CHANGED: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_OBSERVATION_ID_LENGTH: int = 128
MAX_OPERATION_NAME_LENGTH: int = 128
MAX_CONTRACT_HASH_LENGTH: int = 128
MAX_CORRELATION_LENGTH: int = 512
MAX_RESULT_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_PROJECT_ID_LENGTH: int = 96
MAX_WORKTREE_ID_LENGTH: int = 128
MAX_ERROR_CODE_LENGTH: int = 128
MAX_OUTCOME_CLASS_LENGTH: int = 64
MAX_SIDE_EFFECT_LENGTH: int = 64

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_ALLOWED_SIDE_EFFECTS: frozenset[str] = frozenset({
    "read",
    "write_mutation",
    "test_execution",
    "git_read",
    "shell_process",
    "unknown",
})

_ALLOWED_OUTCOME_CLASSES: frozenset[str] = frozenset({
    "success",
    "success_domain_failure",
    "failure_authority_denied",
    "failure_invalid_input",
    "failure_timeout",
    "failure_provider",
})

_SIDE_EFFECT_BY_OPERATION: dict[str, str] = {
    "workspace.read": "read",
    "workspace.search": "read",
    "workspace.write": "write_mutation",
    "test.run": "test_execution",
    "git.status": "git_read",
    "git.diff": "git_read",
    "restricted_shell.run": "shell_process",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{label} must be a non-empty string")
    if len(v) > max_len:
        raise ValueError(f"{label} length {len(v)} exceeds max {max_len}")
    if "\x00" in v:
        raise ValueError(f"{label} must not contain NUL")
    return v


def _validate_optional_bounded_str(value: object, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string or None, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{label} when provided must be non-empty")
    if len(v) > max_len:
        raise ValueError(f"{label} length {len(v)} exceeds max {max_len}")
    if "\x00" in v:
        raise ValueError(f"{label} must not contain NUL")
    return v


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ValueError("digest must be non-empty")
    if len(v) > MAX_DIGEST_LENGTH:
        raise ValueError(f"digest length {len(v)} exceeds {MAX_DIGEST_LENGTH}")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ValueError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _validate_optional_digest(value: object) -> str | None:
    if value is None:
        return None
    return _validate_digest(value)


def _resolve_side_effect(operation_name: str) -> str:
    return _SIDE_EFFECT_BY_OPERATION.get(operation_name, "unknown")


def _classify_failure(error_code: str) -> str:
    cu = error_code.upper()
    if "AUTHORITY" in cu or "DENIED" in cu or "CONTRACT_DRIFT" in cu or "OPERATION_MISMATCH" in cu:
        return "failure_authority_denied"
    if "TIMEOUT" in cu:
        return "failure_timeout"
    if "INVALID" in cu or "MISSING" in cu or "REJECTED" in cu or "UNKNOWN" in cu or "PATH" in cu or "ARGUMENT" in cu or "EXCEEDED" in cu:
        return "failure_invalid_input"
    return "failure_provider"


def _payload_bytes_for_digest(payload: object) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    try:
        j = canonical_json(canonicalize(payload, path="payload"))
        return j.encode("utf-8")
    except Exception:
        return str(payload).encode("utf-8")


_ALLOWED_OBSERVATION_FIELDS: frozenset[str] = frozenset({
    "observation_id",
    "operation_name",
    "contract_hash",
    "correlation_id",
    "work_role",
    "is_success",
    "outcome_class",
    "error_code",
    "result_digest",
    "result_byte_length",
    "result_ref",
    "side_effect",
    "project_id",
    "worktree_id",
})

# ---------------------------------------------------------------------------
# ToolUsageObservation — bounded immutable non-authoritative projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolUsageObservation:
    """Bounded immutable non-authoritative Tool usage observation.

    Captures governance/diagnostics identity only: operation identity,
    contract hash, correlation, work role, outcome class, digest/ref,
    side-effect classification. NOT raw payload, NOT authority.

    Fields bound, deterministic canonical serialization.
    """

    observation_id: str
    operation_name: str
    contract_hash: str
    is_success: bool
    outcome_class: str
    side_effect: str
    # optional bounded identities
    correlation_id: str | None = None
    work_role: AgentWorkRole | None = None
    error_code: str | None = None
    result_digest: str | None = None
    result_byte_length: int | None = None
    result_ref: str | None = None
    project_id: str | None = None
    worktree_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _validate_bounded_str(self.observation_id, "observation_id", MAX_OBSERVATION_ID_LENGTH))
        # operation_name bounded and charset
        op = _validate_bounded_str(self.operation_name, "operation_name", MAX_OPERATION_NAME_LENGTH)
        if not _OPERATION_NAME_RE.fullmatch(op):
            raise ValueError(f"operation_name invalid charset: {op!r}")
        object.__setattr__(self, "operation_name", op)
        # contract_hash is 64 hex (from descriptor)
        ch = _validate_bounded_str(self.contract_hash, "contract_hash", MAX_CONTRACT_HASH_LENGTH)
        if not _DIGEST_HEX_RE.fullmatch(ch.lower()):
            # allow non-hex? descriptor hash is hex; enforce
            raise ValueError(f"contract_hash must be 64 hex chars: {self.contract_hash!r}")
        object.__setattr__(self, "contract_hash", ch.lower())
        if type(self.is_success) is not bool:
            raise TypeError(f"is_success must be bool, got {type(self.is_success).__name__}")
        oc = _validate_bounded_str(self.outcome_class, "outcome_class", MAX_OUTCOME_CLASS_LENGTH)
        if oc not in _ALLOWED_OUTCOME_CLASSES:
            raise ValueError(f"outcome_class must be one of {sorted(_ALLOWED_OUTCOME_CLASSES)}, got {oc!r}")
        object.__setattr__(self, "outcome_class", oc)
        se = _validate_bounded_str(self.side_effect, "side_effect", MAX_SIDE_EFFECT_LENGTH)
        if se not in _ALLOWED_SIDE_EFFECTS:
            raise ValueError(f"side_effect must be one of {sorted(_ALLOWED_SIDE_EFFECTS)}, got {se!r}")
        object.__setattr__(self, "side_effect", se)

        # correlation optional
        if self.correlation_id is not None:
            object.__setattr__(self, "correlation_id", _validate_bounded_str(self.correlation_id, "correlation_id", MAX_CORRELATION_LENGTH))
        # work_role optional
        if self.work_role is not None:
            if isinstance(self.work_role, AgentWorkRole):
                pass
            elif isinstance(self.work_role, Enum):
                raise TypeError(f"work_role must be AgentWorkRole or None, got foreign Enum {type(self.work_role).__name__}")
            elif isinstance(self.work_role, str) and type(self.work_role) is str:
                object.__setattr__(self, "work_role", parse_agent_work_role(self.work_role))
            else:
                raise TypeError(f"work_role must be AgentWorkRole, string, or None, got {type(self.work_role).__name__}")
        # error_code optional bounded, only when failure
        if self.error_code is not None:
            ec = _validate_bounded_str(self.error_code, "error_code", MAX_ERROR_CODE_LENGTH)
            if "/" in ec or "\\" in ec:
                raise ValueError("error_code must not contain path separators")
            object.__setattr__(self, "error_code", ec)
        # consistency: success must not have error_code
        if self.is_success and self.error_code is not None:
            raise ValueError("success observation must have error_code=None")
        if not self.is_success and self.outcome_class == "success":
            raise ValueError("failure observation cannot have outcome_class success")
        if self.is_success and self.outcome_class not in ("success", "success_domain_failure"):
            raise ValueError("success observation outcome_class must be success or success_domain_failure")

        # result_digest optional 64 hex
        if self.result_digest is not None:
            object.__setattr__(self, "result_digest", _validate_digest(self.result_digest))
        # result_byte_length optional int
        if self.result_byte_length is not None:
            if type(self.result_byte_length) is not int:
                raise TypeError(f"result_byte_length must be int, got {type(self.result_byte_length).__name__}")
            if self.result_byte_length < 0:
                raise ValueError("result_byte_length must be >=0")
            if self.result_byte_length > 100 * 1024 * 1024:
                raise ValueError("result_byte_length exceeds bound")
        # result_ref optional bounded
        if self.result_ref is not None:
            object.__setattr__(self, "result_ref", _validate_bounded_str(self.result_ref, "result_ref", MAX_RESULT_REF_LENGTH))
        # project/worktree optional
        if self.project_id is not None:
            object.__setattr__(self, "project_id", _validate_bounded_str(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        if self.worktree_id is not None:
            object.__setattr__(self, "worktree_id", _validate_bounded_str(self.worktree_id, "worktree_id", MAX_WORKTREE_ID_LENGTH))

        # Additional invariants: digest vs byte_length coherence not strictly enforced but if one present without other, allow

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "is_success": self.is_success,
            "observation_id": self.observation_id,
            "operation_name": self.operation_name,
            "contract_hash": self.contract_hash,
            "outcome_class": self.outcome_class,
            "side_effect": self.side_effect,
        }
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.work_role is not None:
            d["work_role"] = self.work_role.value
        if self.error_code is not None:
            d["error_code"] = self.error_code
        if self.result_digest is not None:
            d["result_digest"] = self.result_digest
        if self.result_byte_length is not None:
            d["result_byte_length"] = self.result_byte_length
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.project_id is not None:
            d["project_id"] = self.project_id
        if self.worktree_id is not None:
            d["worktree_id"] = self.worktree_id
        return canonicalize(d, path="ToolUsageObservation")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "observation_id": self.observation_id,
            "operation_name": self.operation_name,
            "contract_hash": self.contract_hash,
            "is_success": self.is_success,
            "outcome_class": self.outcome_class,
            "side_effect": self.side_effect,
        }
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.work_role is not None:
            d["work_role"] = self.work_role.value
        if self.error_code is not None:
            d["error_code"] = self.error_code
        if self.result_digest is not None:
            d["result_digest"] = self.result_digest
        if self.result_byte_length is not None:
            d["result_byte_length"] = self.result_byte_length
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.project_id is not None:
            d["project_id"] = self.project_id
        if self.worktree_id is not None:
            d["worktree_id"] = self.worktree_id
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolUsageObservation":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_OBSERVATION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ToolUsageObservation: {sorted(extra)}")
        for req in ("observation_id", "operation_name", "contract_hash", "is_success", "outcome_class", "side_effect"):
            if req not in data:
                raise ValueError(f"Missing required field: {req!r}")
        wr = data.get("work_role")
        if wr is not None:
            wr = parse_agent_work_role(wr)
        return cls(
            observation_id=data["observation_id"],
            operation_name=data["operation_name"],
            contract_hash=data["contract_hash"],
            is_success=data["is_success"],
            outcome_class=data["outcome_class"],
            side_effect=data["side_effect"],
            correlation_id=data.get("correlation_id"),
            work_role=wr,
            error_code=data.get("error_code"),
            result_digest=data.get("result_digest"),
            result_byte_length=data.get("result_byte_length"),
            result_ref=data.get("result_ref"),
            project_id=data.get("project_id"),
            worktree_id=data.get("worktree_id"),
        )

    def authorize(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("ToolUsageObservation is not authority; cannot authorize")

# ---------------------------------------------------------------------------
# Hook / Emission seam — injected, no persistent singleton storage
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolUsageHook(Protocol):
    """Injected hook — caller-owned, no hidden singleton."""

    def __call__(self, observation: ToolUsageObservation) -> None: ...


class ToolUsageHookError(RuntimeError):
    """Typed wrapper for hook failures that propagate to caller."""

    def __init__(self, observation_id: str, cause: BaseException) -> None:
        super().__init__(f"tool usage hook failed for {observation_id}: {cause}")
        self.observation_id = observation_id
        self.cause = cause


def emit_tool_usage_observation(
    observation: ToolUsageObservation,
    hook: ToolUsageHook | Callable[[ToolUsageObservation], None] | None = None,
) -> None:
    """Emit observation via injected hook.

    * NO_HOOK → valid no-op, deterministic
    * hook present → call hook(observation) exactly once
    * hook failure → propagate typed ToolUsageHookError without mutating
      ToolResponse or triggering retry.
    * No persistent storage, no global singleton.
    """
    if not isinstance(observation, ToolUsageObservation):
        raise TypeError(f"observation must be ToolUsageObservation, got {type(observation).__name__}")
    if hook is None:
        return
    if not callable(hook):
        raise TypeError(f"hook must be callable or None, got {type(hook).__name__}")
    try:
        hook(observation)
    except ToolUsageHookError:
        raise
    except BaseException as exc:  # pragma: no cover
        raise ToolUsageHookError(observation.observation_id, exc) from exc


# ---------------------------------------------------------------------------
# Projection — deterministic bounded observation from ToolRequest/ToolResponse
# ---------------------------------------------------------------------------

def project_tool_usage_observation(
    request: ToolRequest,
    response: ToolResponse,
    *,
    observation_id: str | None = None,
    work_role: AgentWorkRole | str | None = None,
    project_id: str | None = None,
    worktree_id: str | None = None,
) -> ToolUsageObservation:
    """Project bounded non-authoritative observation after Tool result known.

    Deterministic: same request/response logical identity yields same observation.
    MUST be called only after Tool invocation completes (SUCCESS_OBSERVATION_ONLY_AFTER_RESULT_KNOWN).

    Does NOT capture raw inputs/outputs; uses digests/refs/counts.
    """
    if not isinstance(request, ToolRequest):
        raise TypeError(f"request must be ToolRequest, got {type(request).__name__}")
    if not isinstance(response, ToolResponse):
        raise TypeError(f"response must be ToolResponse, got {type(response).__name__}")

    operation_name = request.operation.name
    contract_hash = request.operation.contract_hash()
    correlation_id = request.correlation_id
    # bounded correlation already validated via ToolRequest, but re-validate length
    if correlation_id is not None and len(correlation_id) > MAX_CORRELATION_LENGTH:
        raise ValueError("correlation_id exceeds bound")

    # work_role optional parse
    wr: AgentWorkRole | None = None
    if work_role is not None:
        if isinstance(work_role, AgentWorkRole):
            wr = work_role
        elif isinstance(work_role, str) and type(work_role) is str:
            wr = parse_agent_work_role(work_role)
        else:
            raise TypeError(f"work_role must be AgentWorkRole or string, got {type(work_role).__name__}")

    # Optional project/worktree bounded if supplied
    pid = _validate_optional_bounded_str(project_id, "project_id", MAX_PROJECT_ID_LENGTH) if project_id is not None else None
    wid = _validate_optional_bounded_str(worktree_id, "worktree_id", MAX_WORKTREE_ID_LENGTH) if worktree_id is not None else None

    is_success = response.ok
    side_effect = _resolve_side_effect(operation_name)

    # outcome classification and digest
    if is_success:
        payload = response.payload if response.payload is not None else {}
        # domain outcome check for test/shell where payload indicates nonzero
        domain_failed = False
        if isinstance(payload, dict):
            if payload.get("tests_passed") is False:
                domain_failed = True
            elif "exit_code" in payload and isinstance(payload["exit_code"], int) and payload["exit_code"] != 0:
                # Only treat as domain failure if operation is test or shell; otherwise generic success
                if operation_name in ("test.run", "restricted_shell.run"):
                    domain_failed = True
        outcome_class = "success_domain_failure" if domain_failed else "success"
        error_code = None
        payload_bytes = _payload_bytes_for_digest(payload)
        # bound check not needed; we just digest, not capture raw
        byte_len = len(payload_bytes)
        digest = hashlib.sha256(payload_bytes).hexdigest()
        result_ref = f"tool_obs:{operation_name}:{digest[:16]}"
    else:
        err = response.error or {}
        if not isinstance(err, dict):
            raise TypeError("response error must be dict when ok=False")
        error_code_raw = str(err.get("code", "UNKNOWN"))
        # bound error_code
        ec = error_code_raw.strip()
        if not ec:
            ec = "UNKNOWN"
        if len(ec) > MAX_ERROR_CODE_LENGTH:
            ec = ec[:MAX_ERROR_CODE_LENGTH]
        if "\x00" in ec:
            ec = ec.replace("\x00", "")
        error_code = ec
        outcome_class = _classify_failure(error_code)
        err_bytes = _payload_bytes_for_digest(err)
        byte_len = len(err_bytes)
        digest = hashlib.sha256(err_bytes).hexdigest()
        result_ref = f"tool_obs:{operation_name}:{digest[:16]}"

    # observation_id deterministic if not supplied
    if observation_id is None:
        seed = f"{operation_name}:{contract_hash}:{correlation_id or ''}:{digest}:{is_success}"
        observation_id = "tuo-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    else:
        observation_id = _validate_bounded_str(observation_id, "observation_id", MAX_OBSERVATION_ID_LENGTH)

    return ToolUsageObservation(
        observation_id=observation_id,
        operation_name=operation_name,
        contract_hash=contract_hash,
        correlation_id=correlation_id,
        work_role=wr,
        is_success=is_success,
        outcome_class=outcome_class,
        error_code=error_code,
        result_digest=digest,
        result_byte_length=byte_len,
        result_ref=result_ref,
        side_effect=side_effect,
        project_id=pid,
        worktree_id=wid,
    )


# ---------------------------------------------------------------------------
# ObservedToolProvider — central composition seam wrapping any ToolProvider
# ---------------------------------------------------------------------------

class ObservedToolProvider:
    """Thin wrapper observing any ToolProvider via injected hook.

    - Delegates invoke to inner provider.
    - Projects bounded observation ONLY AFTER result known.
    - Emits via injected hook exactly once per invocation (bounded cardinality, no recursion).
    - Hook failure propagates typed ToolUsageHookError WITHOUT rewriting ToolResponse.

    Use as: observed = ObservedToolProvider(inner_provider, hook=my_hook)

    No persistent storage, no telemetry DB, no analytics.
    """

    def __init__(
        self,
        inner: ToolProvider,
        hook: ToolUsageHook | Callable[[ToolUsageObservation], None] | None = None,
        *,
        work_role: AgentWorkRole | str | None = None,
        project_id: str | None = None,
        worktree_id: str | None = None,
    ) -> None:
        if not hasattr(inner, "invoke") or not callable(getattr(inner, "invoke")):
            raise TypeError(f"inner must be ToolProvider with invoke, got {type(inner).__name__}")
        self._inner = inner
        self._hook = hook
        self._work_role = work_role
        self._project_id = project_id
        self._worktree_id = worktree_id
        # Ensure no persistent storage flag
        self._invocation_count = 0  # for testing cardinality bounds, in-memory only, not persistent

    @property
    def inner(self) -> ToolProvider:
        return self._inner

    @property
    def hook(self) -> ToolUsageHook | Callable[[ToolUsageObservation], None] | None:
        return self._hook

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            # Fail-closed but as ToolResponse to not break contract
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"request must be ToolRequest, got {type(request).__name__}"})
        # Invoke inner — trusted semantics known after this
        response = self._inner.invoke(request)
        if not isinstance(response, ToolResponse):
            # Provider violated contract — fail-closed
            return ToolResponse.failure({"code": "PROVIDER_CONTRACT_VIOLATION", "message": f"provider returned {type(response).__name__} not ToolResponse"})

        # Project observation only after result known
        try:
            observation = project_tool_usage_observation(
                request,
                response,
                work_role=self._work_role,
                project_id=self._project_id,
                worktree_id=self._worktree_id,
            )
        except Exception:
            # Projection failure must not rewrite ToolResponse; propagate as hook-like error?
            # For robustness, return original response if projection fails (observation is non-authority)
            # But to surface, we raise — still not rewriting response semantics
            raise

        # Emit via hook — bounded count, no recursion
        self._invocation_count += 1
        # Guard against recursive fan-out: hook must not call provider invoke recursively and generate unbounded observations
        # We emit exactly once; hook itself is responsible for not recursing (contract)
        try:
            emit_tool_usage_observation(observation, self._hook)
        except ToolUsageHookError:
            # Propagate without rewriting ToolResponse
            raise
        except BaseException as exc:
            raise ToolUsageHookError(observation.observation_id, exc) from exc

        return response

    def invoke_with_observation(self, request: ToolRequest) -> tuple[ToolResponse, ToolUsageObservation]:
        """Convenience: invoke and return both response and observation (without hook).

        Does not emit via hook; useful for tests needing direct observation capture.
        Still counts as one observation per invocation.
        """
        response = self._inner.invoke(request)
        observation = project_tool_usage_observation(
            request,
            response,
            work_role=self._work_role,
            project_id=self._project_id,
            worktree_id=self._worktree_id,
        )
        return response, observation


__all__ = [
    "ToolUsageObservation",
    "ToolUsageHook",
    "ToolUsageHookError",
    "emit_tool_usage_observation",
    "project_tool_usage_observation",
    "ObservedToolProvider",
    # flags
    "TOOL_USAGE_OBSERVATION_IS_AUTHORITY",
    "OBSERVATION_IS_CANONICAL_RESULT",
    "OBSERVATION_IS_OPERATION_AUTHORITY",
    "OBSERVATION_DIGEST_IS_AUTHORITY",
    "TOOL_USAGE_OBSERVATION_BOUNDED",
    "TOOL_USAGE_OBSERVATION_DETERMINISTIC",
    "OBSERVATION_COUNT_PER_INVOCATION_BOUNDED",
    "OBSERVATION_HOOK_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT",
    "SUCCESS_OBSERVATION_ONLY_AFTER_RESULT_KNOWN",
    "SIDE_EFFECT_OBSERVATION_REUSES_EXISTING_RESULT_SEMANTICS",
    "TOOL_FAILURE_OBSERVATION_TRUTHFUL",
    "RAW_TOOL_INPUT_CAPTURED",
    "RAW_TOOL_OUTPUT_CAPTURED",
    "SECRET_ENV_CAPTURED",
    "TELEMETRY_STORE_CREATED",
    "EVENT_STORE_CREATED",
    "ANALYTICS_CREATED",
    "METRICS_DATABASE_CREATED",
    "EXPORTER_CREATED",
    "S6_OWNERSHIP_PRESERVED",
    "NEW_EXECUTION_EVENT_TYPE_CREATED",
]
