"""Bounded Tool Result Governance — S2 M2-W3.

Thin non-authoritative projection:

    ToolProvider invocation outcome (ToolResponse)
        ↓
    existing ToolResponse / observation
        ↓
    bounded Tool-specific projection (ToolResultProjection)
        ↓
    inline bounded output
        OR
    digest-bound project/worktree-scoped ref (ToolOutputRef)
        ↓
    existing result-governance primitives (GovernedReference etc.)

Invariants
----------
* EXISTING_RESULT_GOVERNANCE_REUSED=yes — reuses GovernedReference / ResultGovernanceProjection / CanonicalResult via import
* EXISTING_TOOL_RESPONSE_REUSED=yes — ToolResponse remains provider-local carrier, unchanged
* TOOL_RESPONSE_SCHEMA_CHANGED=no
* THIRD_RESULT_ONTOLOGY_CREATED=no — no new canonical hierarchy
* DUAL_RESULT_AUTHORITY_CREATED=no
* TOOL_RESULT_PROJECTION_IS_AUTHORITY=no
* TOOL_RESULT_REF_IS_AUTHORITY=no
* TOOL_REF_DIGEST_IS_AUTHORITY=no
* TOOL_INLINE_OUTPUT_BOUNDED=yes — M2 constant 4096 bytes
* TOOL_OUTPUT_BY_REF_SUPPORTED=yes
* SILENT_TOOL_OUTPUT_TRUNCATION=no — mode flag truthfully indicates inline vs by_ref
* TOOL_REF_DIGEST_BOUND=yes
* TOOL_REF_PROJECT_WORKTREE_SCOPED=yes
* TOOL_HYDRATION_REAUTHORIZES_SCOPE=yes
* TOOL_REF_POSSESSION_GRANTS_HYDRATION_AUTHORITY=no
* CROSS_PROJECT_TOOL_REF_HYDRATION_FAIL_CLOSED=yes
* CROSS_WORKTREE_TOOL_REF_HYDRATION_FAIL_CLOSED=yes
* RAW_STDOUT_STDERR_DISTINCT_FROM_ARTIFACT=yes — never auto-promotes to artifact authority
* TOOL_FAILURE_IDENTITY_PRESERVED=yes
* RETRYABLE_FIELD_IS_RETRY_AUTHORITY=no
* TOOL_RESULT_PROJECTION_DETERMINISTIC=yes
* W1_TOOL_IDENTITY_REUSED=yes — capability identity via OperationContractDescriptor.name / ToolCapabilityRef

No persistent store, no journal, no state machine, no telemetry, no W2 dependency,
no artifact creation, no mutation Tool.

Storage seam:
    W3 is REF_CONTRACT_ONLY with injected/test-local resolver.  If existing
    durable evidence mechanism is suitable it is referenced via GovernedReference,
    but no new DB/object store is created.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError, error_from_dict
from aota_forge.core.providers.tool import ToolResponse
# Existing result governance reuse — import but do not modify
from aota_forge.core.result_governance import (
    GovernedReference,
    GovernedReferenceKind,
    ResultGovernanceProjection,
)
from aota_forge.core.execution.results import CanonicalResult
# W1 identity reuse
from aota_forge.work_plane.tool_surface import ToolCapabilityRef
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
# WorkerResultCard reused unchanged (import only, never modified)
from aota_forge.work_plane.result_card import WorkerResultCard  # noqa: F401

# ---------------------------------------------------------------------------
# Public invariant flags
# ---------------------------------------------------------------------------

EXISTING_RESULT_GOVERNANCE_REUSED: bool = True
EXISTING_TOOL_RESPONSE_REUSED: bool = True
TOOL_RESPONSE_SCHEMA_CHANGED: bool = False
# Must keep these as imported markers for tests to assert reuse
RESULT_GOVERNANCE_SEAM_USED: str = "GovernedReference+ResultGovernanceProjection+CanonicalResult"

THIRD_RESULT_ONTOLOGY_CREATED: bool = False
DUAL_RESULT_AUTHORITY_CREATED: bool = False

TOOL_RESULT_PROJECTION_IS_AUTHORITY: bool = False
TOOL_RESULT_REF_IS_AUTHORITY: bool = False
TOOL_REF_DIGEST_IS_AUTHORITY: bool = False

TOOL_INLINE_OUTPUT_BOUNDED: bool = True
TOOL_OUTPUT_BY_REF_SUPPORTED: bool = True
SILENT_TOOL_OUTPUT_TRUNCATION: bool = False

TOOL_REF_DIGEST_BOUND: bool = True
TOOL_REF_PROJECT_WORKTREE_SCOPED: bool = True

TOOL_HYDRATION_REAUTHORIZES_SCOPE: bool = True
TOOL_REF_POSSESSION_GRANTS_HYDRATION_AUTHORITY: bool = False

CROSS_PROJECT_TOOL_REF_HYDRATION_FAIL_CLOSED: bool = True
CROSS_WORKTREE_TOOL_REF_HYDRATION_FAIL_CLOSED: bool = True

RAW_STDOUT_STDERR_DISTINCT_FROM_ARTIFACT: bool = True

TOOL_FAILURE_IDENTITY_PRESERVED: bool = True
RETRYABLE_FIELD_IS_RETRY_AUTHORITY: bool = False

TOOL_RESULT_PROJECTION_DETERMINISTIC: bool = True

W1_TOOL_IDENTITY_REUSED: bool = True
DUPLICATE_RESULT_TOOL_ID_NAMESPACE_CREATED: bool = False

W2_DEPENDENCY_INTRODUCED_IN_W3: bool = False

WORKER_RESULT_CARD_REUSED_UNCHANGED: bool = True
TOOL_RESULT_CARD_CREATED: bool = False

NEW_PERSISTENT_RESULT_STORE_CREATED: bool = False
NEW_TOOL_RESULT_JOURNAL_CREATED: bool = False
NEW_TOOL_RESULT_STATE_MACHINE_CREATED: bool = False
TOOL_RESULT_TELEMETRY_STORE_CREATED: bool = False

# Implementation mode truthfully reported
REF_CONTRACT_ONLY: bool = True
REUSED_EXISTING_DURABLE_REF_SEAM: bool = False
REF_IMPLEMENTATION_MODE: str = "REF_CONTRACT_ONLY"

# No artifact promotion
TOOL_OUTPUT_IS_ARTIFACT: bool = False

# ---------------------------------------------------------------------------
# Bounds — empirical M2 constants (not Child Plan global authority)
# AF #46 M1/W2, D5 + §11: distinct bounds reflect distinct typed Core
# result classes owned here (AF_RESULT_GOVERNANCE), not adapter drift.
# - Tool outputs (workspace/shell/test/role/skill): 4096 inline, else by_ref.
# - Hydration whole-object (result.hydrate): 64 KiB inline (durable bound),
#   else by_ref/failure. A hydrate returns content, not another ref, so it
#   needs the larger whole-object bound; selective evidence/artifact slices
#   remain 4096 via selective_hydration.MAX_HYDRATED_BYTES.
# DO_MULTIPLE_BOUNDS_REFLECT_TYPED_CORE_SEMANTICS_OR_ADAPTER_LOCAL_DRIFT?
# -> TYPED_CORE_SEMANTICS (owned here; transports project, never decide).
# ---------------------------------------------------------------------------

TOOL_INLINE_OUTPUT_MAX_BYTES: int = 4096
TOOL_HYDRATE_INLINE_MAX_BYTES: int = 64 * 1024
TOOL_ERROR_INLINE_MAX_BYTES: int = 2048
TOOL_INLINE_OUTPUT_MAX_CHARS: int = 4096  # same bound for char len
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_PROJECT_ID_LENGTH: int = 96
MAX_WORKTREE_ID_LENGTH: int = 128
MAX_METADATA_BYTES: int = 1024
MAX_TOOL_PROJECTION_CANONICAL_BYTES: int = 16 * 1024

_DIGEST_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------

class ToolResultGovernanceError(ValueError):
    """Base fail-closed error for tool result governance."""


class ToolResultBoundError(ToolResultGovernanceError):
    """Oversized or unbounded content."""


class ToolResultHydrationError(ToolResultGovernanceError):
    """Hydration reauthorization failure (fail-closed)."""


class ToolRefTamperError(ToolResultHydrationError):
    """Tampered digest or ref metadata — fail-closed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_capability_name(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ToolResultGovernanceError(f"capability_name must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ToolResultGovernanceError("capability_name must be non-empty")
    if len(v) > 128:
        raise ToolResultBoundError(f"capability_name length {len(v)} exceeds 128")
    if "\x00" in v:
        raise ToolResultGovernanceError("capability_name must not contain NUL")
    if "/" in v or "\\" in v:
        raise ToolResultGovernanceError(f"capability_name must not contain path separators: {v!r}")
    if not _CAPABILITY_RE.fullmatch(v):
        raise ToolResultGovernanceError(f"capability_name invalid charset: {v!r}")
    return v


def _validate_bounded_str(label: str, value: object, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ToolResultGovernanceError(f"{label} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ToolResultGovernanceError(f"{label} must be non-empty")
    if len(v) > max_len:
        raise ToolResultBoundError(f"{label} length {len(v)} exceeds {max_len}")
    if "\x00" in v:
        raise ToolResultGovernanceError(f"{label} must not contain NUL")
    return v


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ToolResultGovernanceError(f"digest must be a string, got {type(value).__name__}")
    v = value.strip().lower()
    if not v:
        raise ToolResultGovernanceError("digest must be non-empty")
    if len(v) > MAX_DIGEST_LENGTH:
        raise ToolResultBoundError(f"digest length {len(v)} exceeds {MAX_DIGEST_LENGTH}")
    if not _DIGEST_HEX_RE.fullmatch(v):
        raise ToolResultGovernanceError(f"digest must be 64 lower hex chars: {value!r}")
    return v


def _validate_project_id(value: object) -> str:
    return _validate_bounded_str("project_id", value, MAX_PROJECT_ID_LENGTH)


def _validate_worktree_id(value: object) -> str:
    return _validate_bounded_str("worktree_id", value, MAX_WORKTREE_ID_LENGTH)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_output_bytes(payload: object) -> bytes:
    """Deterministic bytes for a given payload/raw output."""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    # dict/list etc -> canonical_json
    try:
        j = canonical_json(canonicalize(payload, path="output"))
        return j.encode("utf-8")
    except Exception:
        # fallback to str
        return str(payload).encode("utf-8")


def _expected_ref_str(capability_name: str, digest: str) -> str:
    # Deterministic logical ref: tool_output:<capability>:<first16>
    # Bounded and no path separators beyond colon
    return f"tool_output:{capability_name}:{digest[:16]}"


# ---------------------------------------------------------------------------
# ToolOutputRef — bounded non-authoritative digest-bound project/worktree ref
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolOutputRef:
    """Bounded non-authoritative reference to oversized Tool output.

    Carries:
        logical ref identity
        content digest (sha256 64 hex)
        project identity
        worktree identity
        bounded metadata (byte_length)

    Invariants:
        * TOOL_RESULT_REF_IS_AUTHORITY=no
        * TOOL_REF_DIGEST_IS_AUTHORITY=no
        * TOOL_REF_DIGEST_BOUND=yes
        * TOOL_REF_PROJECT_WORKTREE_SCOPED=yes
    """

    ref: str
    digest: str
    project_id: str
    worktree_id: str
    byte_length: int

    def __post_init__(self) -> None:
        r = _validate_bounded_str("ref", self.ref, MAX_REF_LENGTH)
        object.__setattr__(self, "ref", r)
        d = _validate_digest(self.digest)
        object.__setattr__(self, "digest", d)
        pid = _validate_project_id(self.project_id)
        object.__setattr__(self, "project_id", pid)
        wid = _validate_worktree_id(self.worktree_id)
        object.__setattr__(self, "worktree_id", wid)
        if type(self.byte_length) is not int:
            raise TypeError(f"byte_length must be int, got {type(self.byte_length).__name__}")
        if self.byte_length < 0:
            raise ValueError("byte_length must be >= 0")
        if self.byte_length > 100 * 1024 * 1024:
            raise ToolResultBoundError("byte_length exceeds absolute bound (100 MiB)")
        # No NUL etc already checked

    @property
    def is_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "byte_length": self.byte_length,
            "digest": self.digest,
            "project_id": self.project_id,
            "ref": self.ref,
            "worktree_id": self.worktree_id,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolOutputRef":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"ref", "digest", "project_id", "worktree_id", "byte_length"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ToolResultGovernanceError(f"Unknown field(s) in ToolOutputRef: {sorted(extra)}")
        for req in ("ref", "digest", "project_id", "worktree_id", "byte_length"):
            if req not in data:
                raise ToolResultGovernanceError(f"Missing required field in ToolOutputRef: {req!r}")
        return cls(
            ref=data["ref"],
            digest=data["digest"],
            project_id=data["project_id"],
            worktree_id=data["worktree_id"],
            byte_length=data["byte_length"],
        )

    def as_governed_evidence_ref(self) -> GovernedReference:
        """Bridge to existing result-governance primitive (reuse)."""
        return GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref=self.ref, digest=self.digest)


# ---------------------------------------------------------------------------
# ToolResultProjection — bounded non-authoritative Tool invocation projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolResultProjection:
    """Bounded non-authoritative projection of a ToolProvider invocation.

    Produced from ToolResponse + capability identity + trusted sandbox.
    Carries either inline bounded output or a digest-bound reference.
    Never silent truncation: output_mode truthfully indicates which.

    Fields bound and deterministic.
    """

    capability_name: str
    is_success: bool
    output_mode: str  # "inline" | "by_ref"
    inline_output: str | None
    output_ref: ToolOutputRef | None
    error: dict[str, Any] | None
    project_id: str
    worktree_id: str
    output_digest: str
    output_byte_length: int
    is_truncated: bool  # True iff by_ref (output not fully inline)

    def __post_init__(self) -> None:
        cn = _validate_capability_name(self.capability_name)
        object.__setattr__(self, "capability_name", cn)
        if type(self.is_success) is not bool:
            raise TypeError(f"is_success must be bool, got {type(self.is_success).__name__}")
        if self.output_mode not in ("inline", "by_ref"):
            raise ValueError(f"output_mode must be 'inline' or 'by_ref', got {self.output_mode!r}")
        pid = _validate_project_id(self.project_id)
        object.__setattr__(self, "project_id", pid)
        wid = _validate_worktree_id(self.worktree_id)
        object.__setattr__(self, "worktree_id", wid)
        od = _validate_digest(self.output_digest)
        object.__setattr__(self, "output_digest", od)
        if type(self.output_byte_length) is not int:
            raise TypeError(f"output_byte_length must be int, got {type(self.output_byte_length).__name__}")
        if self.output_byte_length < 0:
            raise ValueError("output_byte_length must be >=0")
        if type(self.is_truncated) is not bool:
            raise TypeError("is_truncated must be bool")
        # mode-specific checks
        if self.output_mode == "inline":
            if self.inline_output is None or not isinstance(self.inline_output, str):
                raise TypeError("inline mode requires inline_output str")
            # inline output bounded
            b = self.inline_output.encode("utf-8")
            if len(b) > TOOL_INLINE_OUTPUT_MAX_BYTES:
                raise ToolResultBoundError(f"inline_output bytes {len(b)} exceeds {TOOL_INLINE_OUTPUT_MAX_BYTES}")
            if self.output_ref is not None:
                raise ValueError("inline mode must have output_ref=None")
            if self.is_truncated is not False:
                raise ValueError("inline mode must have is_truncated=False")
            # failure vs success consistency
            if self.is_success is False and self.error is None:
                raise ValueError("failure projection requires error dict")
            if self.is_success is True and self.error is not None:
                raise ValueError("success projection must have error=None")
        else:  # by_ref
            if self.output_ref is None or not isinstance(self.output_ref, ToolOutputRef):
                raise TypeError("by_ref mode requires ToolOutputRef")
            if self.inline_output is not None:
                raise ValueError("by_ref mode must have inline_output=None")
            if self.is_truncated is not True:
                raise ValueError("by_ref mode must have is_truncated=True")
            # ref must be scoped same project/worktree as projection
            if self.output_ref.project_id != self.project_id:
                raise ToolResultGovernanceError("output_ref project_id must equal projection project_id")
            if self.output_ref.worktree_id != self.worktree_id:
                raise ToolResultGovernanceError("output_ref worktree_id must equal projection worktree_id")
            # digest agreement
            if self.output_ref.digest != self.output_digest:
                raise ToolResultGovernanceError("output_ref digest must equal projection output_digest")
            if self.output_ref.byte_length != self.output_byte_length:
                raise ToolResultGovernanceError("output_ref byte_length must equal projection byte_length")
        # error bounded
        if self.error is not None:
            if not isinstance(self.error, dict):
                raise TypeError("error must be dict when supplied")
            # must contain code/message
            if "code" not in self.error or "message" not in self.error:
                raise ValueError("error dict must contain code and message")
            # check bounded byte size
            try:
                eb = canonical_json(canonicalize(self.error, path="error")).encode("utf-8")
            except Exception as exc:
                raise ToolResultGovernanceError(f"error dict not canonicalizable: {exc}") from exc
            if len(eb) > TOOL_ERROR_INLINE_MAX_BYTES:
                raise ToolResultBoundError(f"error payload bytes {len(eb)} exceeds {TOOL_ERROR_INLINE_MAX_BYTES}")
        # completeness vs length consistency (digest already validates)
        # Canonical size bound
        size = len(self.canonical_json().encode("utf-8"))
        if size > MAX_TOOL_PROJECTION_CANONICAL_BYTES:
            raise ToolResultBoundError(f"projection canonical size {size} exceeds {MAX_TOOL_PROJECTION_CANONICAL_BYTES}")

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_artifact(self) -> bool:
        # RAW stdout/stderr distinct from artifact — never auto-promoted
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "capability_name": self.capability_name,
            "is_success": self.is_success,
            "is_truncated": self.is_truncated,
            "output_byte_length": self.output_byte_length,
            "output_digest": self.output_digest,
            "output_mode": self.output_mode,
            "project_id": self.project_id,
            "worktree_id": self.worktree_id,
        }
        if self.inline_output is not None:
            d["inline_output"] = self.inline_output
        if self.output_ref is not None:
            d["output_ref"] = self.output_ref.canonical_dict()
        if self.error is not None:
            d["error"] = canonicalize(self.error, path="error")
        return canonicalize(d, path="ToolResultProjection")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return _sha256_hex(self.canonical_json().encode("utf-8"))

    @property
    def projection_digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolResultProjection":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"capability_name", "is_success", "output_mode", "inline_output", "output_ref", "error", "project_id", "worktree_id", "output_digest", "output_byte_length", "is_truncated"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ToolResultGovernanceError(f"Unknown field(s) in ToolResultProjection: {sorted(extra)}")
        for req in ("capability_name", "is_success", "output_mode", "project_id", "worktree_id", "output_digest", "output_byte_length", "is_truncated"):
            if req not in data:
                raise ToolResultGovernanceError(f"Missing required field: {req!r}")
        ref_obj = None
        if data.get("output_ref") is not None:
            ref_obj = ToolOutputRef.from_dict(data["output_ref"])  # type: ignore[arg-type]
        return cls(
            capability_name=data["capability_name"],
            is_success=data["is_success"],
            output_mode=data["output_mode"],
            inline_output=data.get("inline_output"),
            output_ref=ref_obj,
            error=dict(data["error"]) if data.get("error") is not None else None,
            project_id=data["project_id"],
            worktree_id=data["worktree_id"],
            output_digest=data["output_digest"],
            output_byte_length=data["output_byte_length"],
            is_truncated=data["is_truncated"],
        )

    def as_governed_evidence_refs(self) -> tuple[GovernedReference, ...]:
        """Project into existing GovernedReference evidence (reuse)."""
        if self.output_mode == "by_ref" and self.output_ref is not None:
            return (self.output_ref.as_governed_evidence_ref(),)
        # inline case: synthesize bounded logical evidence ref
        syn_ref = f"tool_inline:{self.capability_name}:{self.output_digest[:16]}"
        # bound syn_ref length already
        return (GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref=syn_ref, digest=self.output_digest),)

    def authorize(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("ToolResultProjection is not authority; cannot authorize")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolResultProjection):
            return NotImplemented
        return self.canonical_dict() == other.canonical_dict()

    def __hash__(self) -> int:
        return hash(self.canonical_json())


# ---------------------------------------------------------------------------
# Projection function — deterministic
# ---------------------------------------------------------------------------

def _resolve_capability_name(capability: object) -> str:
    if isinstance(capability, ToolCapabilityRef):
        return _validate_capability_name(capability.capability_name)
    if isinstance(capability, OperationContractDescriptor):
        capability.validate()
        return _validate_capability_name(capability.name)
    if isinstance(capability, str) and type(capability) is str:
        return _validate_capability_name(capability)
    raise ToolResultGovernanceError(f"capability must be str, OperationContractDescriptor, or ToolCapabilityRef, got {type(capability).__name__}")


def _derive_full_output_bytes(response: ToolResponse, raw_output: object) -> bytes:
    if raw_output is not None:
        if isinstance(raw_output, bytes):
            return raw_output
        if isinstance(raw_output, str):
            return raw_output.encode("utf-8")
        # allow dict -> canonical
        return _canonical_output_bytes(raw_output)
    # No raw_output supplied: derive from response
    if response.ok:
        payload = response.payload or {}
        if not payload:
            return b""
        # payload dict -> canonical json bytes (deterministic)
        return canonical_json(canonicalize(payload, path="payload")).encode("utf-8")
    else:
        # failure has no output, treat as empty bytes for output semantics
        # error is separated
        return b""


def project_tool_result(
    response: ToolResponse,
    capability: str | OperationContractDescriptor | ToolCapabilityRef,
    sandbox: WorktreeSandboxBoundary,
    *,
    raw_output: str | bytes | dict[str, Any] | None = None,
) -> ToolResultProjection:
    """Project a bounded non-authoritative ToolResultProjection from ToolResponse.

    Deterministic: equivalent ToolResponse + same sandbox + same raw_output
    yields identical projection metadata/digest.

    Parameters
    ----------
    response: ToolResponse — existing provider-local carrier (reused, not mutated)
    capability: canonical Tool identity (W1 reuse)
    sandbox: trusted WorktreeSandboxBoundary (project/worktree scoped)
    raw_output: optional explicit output content (str/bytes/dict).  If None,
        success payload is serialized deterministically; failure payload is empty
        and error is projected separately.

    Returns
    -------
    ToolResultProjection — bounded inline or by_ref, never silent truncation.

    Fail-closed on oversized metadata/error, malformed identity, wrong types.
    """
    if not isinstance(response, ToolResponse):
        raise TypeError(f"response must be ToolResponse, got {type(response).__name__}")
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise TypeError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    capability_name = _resolve_capability_name(capability)

    # Validate error identity preserved — do not convert failure to success
    is_success = response.ok
    error_dict: dict[str, Any] | None = None
    if response.ok is False:
        # reuse ForgeError dict shape: must be dict with code/message
        if not isinstance(response.error, dict):
            raise TypeError("ToolResponse failure must carry error dict")
        # Validate via error_from_dict that it's a proper ForgeError projection
        # but keep original code identity
        # error_from_dict returns ForgeError or UnknownFutureError; we accept any valid
        # but ensure round-trip preserves code
        parsed = error_from_dict(response.error)  # type: ignore[arg-type]
        # parsed may be None only if code missing — but ToolResponse already validated has code/message
        if parsed is None:
            raise ToolResultGovernanceError("invalid error dict in ToolResponse")
        # Re-serialize canonical but preserve original retryable identity — no new authority
        err_canonical = canonicalize(response.error, path="error")
        # Check bound
        eb = canonical_json(err_canonical).encode("utf-8")
        if len(eb) > TOOL_ERROR_INLINE_MAX_BYTES:
            raise ToolResultBoundError(f"error payload exceeds bound {TOOL_ERROR_INLINE_MAX_BYTES}: {len(eb)}")
        error_dict = dict(err_canonical)  # type: ignore[assignment]
    else:
        if response.error is not None:
            raise ValueError("success ToolResponse must have error=None — invariant violation")
        error_dict = None

    full_bytes = _derive_full_output_bytes(response, raw_output)
    byte_len = len(full_bytes)
    digest = _sha256_hex(full_bytes)

    # Determine mode truthfully — never silently truncate.
    # Inline bound owned here (D5): 4096 for tool outputs.
    # result.hydrate whole-object inline (up to 64 KiB) is a distinct typed
    # result class owned by result_hydrate.project_hydrate_result_for_transport,
    # not by this function. Transports never choose bounds.
    if byte_len <= TOOL_INLINE_OUTPUT_MAX_BYTES:
        # inline complete
        inline_str = full_bytes.decode("utf-8", errors="replace") if full_bytes else ""
        # double-check char bound? bytes already bounded
        return ToolResultProjection(
            capability_name=capability_name,
            is_success=is_success,
            output_mode="inline",
            inline_output=inline_str,
            output_ref=None,
            error=error_dict,
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            output_digest=digest,
            output_byte_length=byte_len,
            is_truncated=False,
            )
    else:
        # by_ref — inline not returned, ref carries digest + scope
        ref_str = _expected_ref_str(capability_name, digest)
        # Ensure ref bounded
        if len(ref_str) > MAX_REF_LENGTH:
            raise ToolResultBoundError("generated ref exceeds max length")
        output_ref = ToolOutputRef(
            ref=ref_str,
            digest=digest,
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            byte_length=byte_len,
        )
        return ToolResultProjection(
            capability_name=capability_name,
            is_success=is_success,
            output_mode="by_ref",
            inline_output=None,
            output_ref=output_ref,
            error=error_dict,
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            output_digest=digest,
            output_byte_length=byte_len,
            is_truncated=True,
        )


# ---------------------------------------------------------------------------
# Hydration — reauthorization required
# ---------------------------------------------------------------------------

def hydrate_tool_output(
    projection: ToolResultProjection,
    *,
    current_sandbox: WorktreeSandboxBoundary,
    content_resolver: Callable[[ToolOutputRef], bytes | str] | Mapping[str, bytes | str] | Mapping[tuple[str, str], bytes | str] | None = None,
) -> str:
    """Hydrate Tool output, reauthorizing current trusted scope.

    Possessing a projection/ref alone does NOT grant hydration authority.
    Caller must supply current trusted WorktreeSandboxBoundary and a
    content_resolver seam.  Cross-project/worktree and tampered digests fail closed.

    For inline projections, returns inline_output directly after scope check.
    For by_ref projections, resolves content via resolver and validates digest/integrity.

    Parameters
    ----------
    projection: ToolResultProjection
    current_sandbox: WorktreeSandboxBoundary — current trusted scope (required)
    content_resolver: callable or mapping that returns content bytes/str for a given ToolOutputRef.
        Contract-only: injected test-local resolver, no persistent store.

    Returns
    -------
    str — hydrated content
    """
    if not isinstance(projection, ToolResultProjection):
        raise TypeError(f"projection must be ToolResultProjection, got {type(projection).__name__}")
    if not isinstance(current_sandbox, WorktreeSandboxBoundary):
        # mere possession cannot hydrate without current scope/binding
        raise ToolResultHydrationError("hydration requires current WorktreeSandboxBoundary — ref possession alone insufficient")
    # Revalidate project/worktree scope — cross-* fail closed
    if projection.project_id != current_sandbox.project_id:
        raise ToolResultHydrationError(
            f"cross-project hydration denied: projection project {projection.project_id!r} != current {current_sandbox.project_id!r}"
        )
    if projection.worktree_id != current_sandbox.worktree_id:
        raise ToolResultHydrationError(
            f"cross-worktree hydration denied: projection worktree {projection.worktree_id!r} != current {current_sandbox.worktree_id!r}"
        )
    if projection.output_mode == "inline":
        # No resolver needed, but still scope-checked
        assert projection.inline_output is not None
        # Verify digest integrity of returned content (defense in depth)
        computed = _sha256_hex(projection.inline_output.encode("utf-8"))
        if computed != projection.output_digest:
            raise ToolRefTamperError("inline digest mismatch — tampered content")
        return projection.inline_output

    # by_ref path — requires resolver and full revalidation
    if projection.output_ref is None:
        raise ToolResultGovernanceError("by_ref projection missing output_ref")
    return hydrate_by_ref(
        projection.output_ref,
        capability_name=projection.capability_name,
        expected_digest=projection.output_digest,
        expected_byte_length=projection.output_byte_length,
        current_sandbox=current_sandbox,
        content_resolver=content_resolver,
    )


def hydrate_by_ref(
    ref: ToolOutputRef,
    *,
    capability_name: str | None = None,
    expected_digest: str | None = None,
    expected_byte_length: int | None = None,
    current_sandbox: WorktreeSandboxBoundary,
    content_resolver: Callable[[ToolOutputRef], bytes | str] | Mapping[Any, bytes | str] | None = None,
) -> str:
    """Hydrate a single ToolOutputRef with full reauthorization.

    Validates project, worktree, digest integrity, byte_length, and ref
    logical identity.  Tampered content or metadata fails closed.
    """
    if not isinstance(ref, ToolOutputRef):
        raise TypeError(f"ref must be ToolOutputRef, got {type(ref).__name__}")
    if not isinstance(current_sandbox, WorktreeSandboxBoundary):
        raise ToolResultHydrationError("hydration requires current WorktreeSandboxBoundary")
    # Cross-scope checks
    if ref.project_id != current_sandbox.project_id:
        raise ToolResultHydrationError(f"cross-project ref hydration denied: {ref.project_id!r} != {current_sandbox.project_id!r}")
    if ref.worktree_id != current_sandbox.worktree_id:
        raise ToolResultHydrationError(f"cross-worktree ref hydration denied: {ref.worktree_id!r} != {current_sandbox.worktree_id!r}")
    # Capability consistency if supplied
    if capability_name is not None:
        cn = _validate_capability_name(capability_name)
        expected_ref = _expected_ref_str(cn, ref.digest)
        if ref.ref != expected_ref:
            raise ToolRefTamperError(f"ref logical identity tampered: expected {expected_ref!r}, got {ref.ref!r}")
    if expected_digest is not None:
        ed = _validate_digest(expected_digest)
        if ref.digest != ed:
            raise ToolRefTamperError(f"digest mismatch: ref {ref.digest!r} != expected {ed!r}")
    if expected_byte_length is not None:
        if type(expected_byte_length) is not int:
            raise TypeError("expected_byte_length must be int")
        if ref.byte_length != expected_byte_length:
            raise ToolRefTamperError(f"byte_length mismatch: ref {ref.byte_length} != expected {expected_byte_length}")

    if content_resolver is None:
        raise ToolResultHydrationError("hydration requires content_resolver (contract-only injected seam)")

    # Resolve content via injected seam
    raw: bytes | str | None = None
    try:
        if callable(content_resolver):
            raw = content_resolver(ref)  # type: ignore[call-arg]
        elif isinstance(content_resolver, Mapping):
            # Try lookup by ref object, then by digest, then by ref string
            if ref in content_resolver:  # type: ignore[operator]
                raw = content_resolver[ref]  # type: ignore[index]
            elif ref.digest in content_resolver:
                raw = content_resolver[ref.digest]  # type: ignore[index]
            elif ref.ref in content_resolver:
                raw = content_resolver[ref.ref]  # type: ignore[index]
            elif (ref.ref, ref.digest) in content_resolver:  # type: ignore[operator]
                raw = content_resolver[(ref.ref, ref.digest)]  # type: ignore[index]
            else:
                # also try tuple key with project/worktree?
                raise KeyError(f"resolver has no entry for ref {ref.ref!r}")
        else:
            raise TypeError("content_resolver must be callable or mapping")
    except ToolResultHydrationError:
        raise
    except Exception as exc:
        raise ToolResultHydrationError(f"resolver failure: {exc}") from exc

    if raw is None:
        raise ToolResultHydrationError("resolver returned None")

    if isinstance(raw, bytes):
        content_bytes = raw
        content_str = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        content_str = raw
        content_bytes = raw.encode("utf-8")
    else:
        raise ToolResultHydrationError(f"resolver returned unsupported type {type(raw).__name__}")

    # Integrity checks post-resolve
    computed_digest = _sha256_hex(content_bytes)
    if computed_digest != ref.digest:
        raise ToolRefTamperError(f"content digest mismatch: computed {computed_digest!r} != ref {ref.digest!r}")
    if len(content_bytes) != ref.byte_length:
        raise ToolRefTamperError(f"byte_length mismatch: content {len(content_bytes)} != ref {ref.byte_length}")

    # Also check overall bound for metadata tampering? ref already validated bounded
    return content_str


__all__ = [
    "ToolOutputRef",
    "ToolResultProjection",
    "project_tool_result",
    "hydrate_tool_output",
    "hydrate_by_ref",
    "ToolResultGovernanceError",
    "ToolResultBoundError",
    "ToolResultHydrationError",
    "ToolRefTamperError",
    "TOOL_INLINE_OUTPUT_MAX_BYTES",
    "TOOL_HYDRATE_INLINE_MAX_BYTES",
    "TOOL_ERROR_INLINE_MAX_BYTES",
    "MAX_REF_LENGTH",
    "MAX_DIGEST_LENGTH",
    "MAX_PROJECT_ID_LENGTH",
    "MAX_WORKTREE_ID_LENGTH",
    "MAX_METADATA_BYTES",
    "MAX_TOOL_PROJECTION_CANONICAL_BYTES",
    # flags
    "EXISTING_RESULT_GOVERNANCE_REUSED",
    "EXISTING_TOOL_RESPONSE_REUSED",
    "TOOL_RESPONSE_SCHEMA_CHANGED",
    "RESULT_GOVERNANCE_SEAM_USED",
    "THIRD_RESULT_ONTOLOGY_CREATED",
    "DUAL_RESULT_AUTHORITY_CREATED",
    "TOOL_RESULT_PROJECTION_IS_AUTHORITY",
    "TOOL_RESULT_REF_IS_AUTHORITY",
    "TOOL_REF_DIGEST_IS_AUTHORITY",
    "TOOL_INLINE_OUTPUT_BOUNDED",
    "TOOL_OUTPUT_BY_REF_SUPPORTED",
    "SILENT_TOOL_OUTPUT_TRUNCATION",
    "TOOL_REF_DIGEST_BOUND",
    "TOOL_REF_PROJECT_WORKTREE_SCOPED",
    "TOOL_HYDRATION_REAUTHORIZES_SCOPE",
    "TOOL_REF_POSSESSION_GRANTS_HYDRATION_AUTHORITY",
    "CROSS_PROJECT_TOOL_REF_HYDRATION_FAIL_CLOSED",
    "CROSS_WORKTREE_TOOL_REF_HYDRATION_FAIL_CLOSED",
    "RAW_STDOUT_STDERR_DISTINCT_FROM_ARTIFACT",
    "TOOL_FAILURE_IDENTITY_PRESERVED",
    "RETRYABLE_FIELD_IS_RETRY_AUTHORITY",
    "TOOL_RESULT_PROJECTION_DETERMINISTIC",
    "W1_TOOL_IDENTITY_REUSED",
    "DUPLICATE_RESULT_TOOL_ID_NAMESPACE_CREATED",
    "W2_DEPENDENCY_INTRODUCED_IN_W3",
    "WORKER_RESULT_CARD_REUSED_UNCHANGED",
    "TOOL_RESULT_CARD_CREATED",
    "NEW_PERSISTENT_RESULT_STORE_CREATED",
    "NEW_TOOL_RESULT_JOURNAL_CREATED",
    "NEW_TOOL_RESULT_STATE_MACHINE_CREATED",
    "TOOL_RESULT_TELEMETRY_STORE_CREATED",
    "REF_CONTRACT_ONLY",
    "REUSED_EXISTING_DURABLE_REF_SEAM",
    "REF_IMPLEMENTATION_MODE",
    "TOOL_OUTPUT_IS_ARTIFACT",
]
