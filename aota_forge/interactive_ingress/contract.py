"""AF #58 M2 — interactive ingress mechanical contract.

Pure mechanical contract helpers: canonical Plan-ref intent extraction,
interactive Plan identity derivation, bounded message/profile validation and
the non-authoritative preparation record.  No Plan read, no project
resolution, no host call, no authority.

Trust boundary:

* the extracted ``owner/repo#number`` token is user intent only;
* the derived internal Plan ID is a deterministic mechanical identity for the
  already accepted source-neutral ``PlanAuthorityBinding`` (it is never the
  authority: the bound authority source is);
* the preparation record is staging data only and cannot grant anything.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.plan.validation import is_plan_id
from aota_forge.work_plane.github_tools import parse_plan_ref

CONTRACT_OWNER = "aota_forge/interactive_ingress/contract.py"

INTERACTIVE_SCHEMA = "af58-m2-interactive-v1"
PREPARATION_RECORD_VERSION = 1
PREPARATION_ID_PREFIX = "prep-"
PREPARATION_ID_RE = re.compile(r"^prep-[0-9a-f]{32}$")

PREPARATION_STATE_PREPARED = "prepared"
PREPARATION_STATE_BOUND = "bound"
PREPARATION_STATES = frozenset({PREPARATION_STATE_PREPARED, PREPARATION_STATE_BOUND})

SESSION_METADATA_PREPARATION_KEY = "af_interactive_preparation"
SESSION_METADATA_SCHEMA_KEY = "af_interactive_schema"

# Bounded operator message accepted for intent extraction (bytes).
MAX_MESSAGE_BYTES = 65536
# Default lifetime of a prepared (unbound) instance namespace.
DEFAULT_PREPARATION_TTL_SECONDS = 43200.0

# Canonical Plan reference token: exactly ``owner/repo#number`` with the
# accepted AF Plan-ref grammar (``parse_plan_ref`` is the single grammar
# owner; this regex only locates candidate tokens inside free text).
PLAN_REF_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+#[1-9][0-9]{0,9}")

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PLAN_ID_SANITIZE_RE = re.compile(r"[^a-z0-9]+")

# Bounded trusted Plan projection carried by an interactive task-main binding.
# It is a mechanical projection of the already-read live Plan (identity,
# revision, current milestone, approval truth) and grants nothing.
TRUSTED_PLAN_STATE_KEYS = frozenset(
    {
        "plan_ref",
        "plan_id",
        "source_kind",
        "source_revision",
        "source_digest",
        "current_milestone",
        "milestone_status",
        "milestone_user_approval_satisfied",
    }
)

# Fail-closed error codes (bounded, UI-displayable).
PLAN_REF_MISSING = "PLAN_REF_MISSING"
PLAN_REF_AMBIGUOUS = "PLAN_REF_AMBIGUOUS"
PLAN_REF_INVALID = "PLAN_REF_INVALID"
PLAN_UNREADABLE = "PLAN_UNREADABLE"
PLAN_PROJECT_IDENTITY_MISSING = "PLAN_PROJECT_IDENTITY_MISSING"
PROJECT_RESOLUTION_FAILED = "PROJECT_RESOLUTION_FAILED"
PROJECT_ROOT_MISMATCH = "PROJECT_ROOT_MISMATCH"
PROFILE_MISMATCH = "PROFILE_MISMATCH"
UNKNOWN_SESSION = "UNKNOWN_SESSION"
SESSION_DIRECTORY_MISMATCH = "SESSION_DIRECTORY_MISMATCH"
SESSION_NOT_AF_INTERACTIVE = "SESSION_NOT_AF_INTERACTIVE"
STALE_PREPARATION = "STALE_PREPARATION"
CROSS_SESSION_BIND = "CROSS_SESSION_BIND"
SESSION_ALREADY_BOUND_DIFFERENT_PLAN = "SESSION_ALREADY_BOUND_DIFFERENT_PLAN"
BINDING_TAMPERED = "BINDING_TAMPERED"
INVALID_INPUT = "INVALID_INPUT"
WORKSPACE_ROOT_INVALID = "WORKSPACE_ROOT_INVALID"
MATERIALIZATION_FAILED = "MATERIALIZATION_FAILED"


class InteractiveIngressError(Exception):
    """Bounded fail-closed interactive ingress error (never fabricated truth)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def ensure_error(exc: Exception, code: str) -> InteractiveIngressError:
    """Normalize any failure into a typed bounded ingress error."""
    if isinstance(exc, InteractiveIngressError):
        return exc
    return InteractiveIngressError(code, str(exc) or type(exc).__name__)


def normalize_message(text: Any) -> str:
    """Bounded operator message text (mechanical; never authority)."""
    if not isinstance(text, str) or not text.strip():
        raise InteractiveIngressError(INVALID_INPUT, "operator message must be a non-empty string")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise InteractiveIngressError(
            INVALID_INPUT, f"operator message exceeds {MAX_MESSAGE_BYTES} bytes"
        )
    return text


def extract_canonical_plan_refs(text: Any) -> tuple[str, ...]:
    """Unique canonical Plan references present in the operator message.

    Zero or many distinct canonical references are both returned as-is; the
    caller fails closed (``PLAN_REF_MISSING`` / ``PLAN_REF_AMBIGUOUS``).  Any
    candidate token that does not satisfy the accepted Plan-ref grammar is
    ignored (it is not a canonical reference), so ``#39``, folder names and
    prose never become Plan authority.
    """
    message = normalize_message(text)
    seen: dict[str, None] = {}
    for candidate in PLAN_REF_TOKEN_RE.findall(message):
        try:
            parse_plan_ref(candidate)
        except Exception:
            continue
        seen.setdefault(candidate, None)
    return tuple(seen)


def validate_trusted_plan_state(value: Any) -> dict[str, Any]:
    """Fail-closed bounded validation of a trusted Plan state projection."""
    if not isinstance(value, Mapping):
        raise InteractiveIngressError(INVALID_INPUT, "trusted Plan state must be a mapping")
    unknown = set(value) - TRUSTED_PLAN_STATE_KEYS
    if unknown:
        raise InteractiveIngressError(
            INVALID_INPUT, f"trusted Plan state has unknown keys: {sorted(unknown)!r}"
        )
    required = {"plan_ref", "plan_id", "source_kind"}
    if not required.issubset(value.keys()):
        raise InteractiveIngressError(
            INVALID_INPUT, "trusted Plan state is missing required identity facts"
        )
    for key in ("plan_ref", "plan_id", "source_kind", "current_milestone", "milestone_status"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or len(item) > 512):
            raise InteractiveIngressError(
                INVALID_INPUT, f"trusted Plan state field {key!r} is not a bounded string"
            )
    revision = value.get("source_revision")
    if revision is not None and (not isinstance(revision, str) or len(revision) > 512):
        raise InteractiveIngressError(
            INVALID_INPUT, "trusted Plan state source_revision is not a bounded string"
        )
    digest = value.get("source_digest")
    if digest is not None and (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest.lower())
    ):
        raise InteractiveIngressError(
            INVALID_INPUT, "trusted Plan state source_digest is not a sha256 hex string"
        )
    approval = value.get("milestone_user_approval_satisfied")
    if approval is not None and not isinstance(approval, bool):
        raise InteractiveIngressError(
            INVALID_INPUT, "trusted Plan state approval must be boolean or null"
        )
    return dict(value)


def require_single_plan_ref(text: Any) -> str:
    """Exactly one canonical Plan reference, or fail closed."""
    refs = extract_canonical_plan_refs(text)
    if not refs:
        raise InteractiveIngressError(
            PLAN_REF_MISSING,
            "the first message must contain exactly one canonical Plan reference "
            "(owner/repo#number)",
        )
    if len(refs) > 1:
        raise InteractiveIngressError(
            PLAN_REF_AMBIGUOUS,
            "the first message contains more than one canonical Plan reference; "
            "state exactly one Plan and start a New Chat for another",
        )
    return refs[0]


def derive_interactive_plan_id(plan_ref: str) -> str:
    """Deterministic canonical internal Plan ID for an already-validated Plan ref.

    The value reuses the accepted canonical Plan ID grammar (one owner:
    ``core.plan.validation.is_plan_id``).  It is a mechanical identity for the
    source-neutral ``PlanAuthorityBinding``; the bound authority source
    remains the GitHub Issue reference, never this string.
    """
    try:
        repo, _owner, issue_number = parse_plan_ref(plan_ref)
    except Exception as exc:  # noqa: BLE001 - invalid references fail closed
        raise InteractiveIngressError(
            PLAN_REF_INVALID, f"not a canonical Plan reference: {plan_ref!r}"
        ) from exc
    token = _PLAN_ID_SANITIZE_RE.sub("_", f"{repo}_{issue_number}".lower()).strip("_")
    plan_id = f"plan_{token}"
    if not is_plan_id(plan_id):
        raise InteractiveIngressError(
            PLAN_REF_INVALID,
            f"cannot derive a canonical Plan ID from reference {plan_ref!r}",
        )
    return plan_id


def require_bounded_profile(value: Any) -> str:
    """Exact host-profile string (no whitespace, bounded); fail closed."""
    if not isinstance(value, str) or type(value) is not str:
        raise InteractiveIngressError(PROFILE_MISMATCH, "requested host profile must be a string")
    profile = value.strip()
    if not profile or any(ch.isspace() for ch in profile) or len(profile) > 128:
        raise InteractiveIngressError(
            PROFILE_MISMATCH, f"requested host profile is not an exact bounded value: {profile!r}"
        )
    return profile


def new_preparation_id() -> str:
    """Opaque mechanical preparation identity (never authority)."""
    return f"{PREPARATION_ID_PREFIX}{secrets.token_hex(16)}"


def preparation_instance_key(preparation_id: str) -> str:
    """Mechanical per-session instance key derived from the preparation id."""
    if not PREPARATION_ID_RE.fullmatch(preparation_id or ""):
        raise InteractiveIngressError(INVALID_INPUT, "preparation id is not a canonical prep id")
    return f"task-main-i{preparation_id[len(PREPARATION_ID_PREFIX):]}"


def preparation_session_root(workspace_root: Path, preparation_id: str) -> Path:
    return Path(workspace_root).resolve() / "sessions" / preparation_id


def preparation_record_path(workspace_root: Path, preparation_id: str) -> Path:
    return preparation_session_root(workspace_root, preparation_id) / "preparation.json"


def preparation_bind_receipt_path(workspace_root: Path, preparation_id: str) -> Path:
    return preparation_session_root(workspace_root, preparation_id) / "bind-receipt.json"


def preparation_active_worktree(workspace_root: Path, preparation_id: str) -> Path:
    return preparation_session_root(workspace_root, preparation_id) / "active-worktree"


@dataclass
class InteractivePreparation:
    """Non-authoritative staging record for one interactive New Chat.

    ``PREPARATION_TOKEN_IS_AUTHORITY=no``: nothing in this record grants a
    task-main role, project/root authority, write authority or approval.  The
    trusted binding is materialized separately by ``bind_interactive_session``
    from live Plan truth and trusted resolution.
    """

    preparation_id: str
    instance_key: str
    instance_dir: str
    worktree_root: str
    session_root: str
    created_at: float
    expires_at: float
    state: str = PREPARATION_STATE_PREPARED
    bound_session_id: str = ""
    bound_plan_ref: str = ""
    runtime_config_path: str = ""
    registry_path: str = ""
    record_version: int = PREPARATION_RECORD_VERSION
    interactive_schema: str = INTERACTIVE_SCHEMA
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not PREPARATION_ID_RE.fullmatch(self.preparation_id or ""):
            raise InteractiveIngressError(INVALID_INPUT, "preparation record has an invalid id")
        if not _SAFE_IDENTIFIER_RE.fullmatch(self.instance_key or ""):
            raise InteractiveIngressError(INVALID_INPUT, "preparation record instance_key invalid")
        for label, value in (
            ("instance_dir", self.instance_dir),
            ("worktree_root", self.worktree_root),
            ("session_root", self.session_root),
        ):
            if not isinstance(value, str) or not value.startswith("/"):
                raise InteractiveIngressError(
                    INVALID_INPUT, f"preparation record {label} must be an absolute path"
                )
        if self.state not in PREPARATION_STATES:
            raise InteractiveIngressError(INVALID_INPUT, "preparation record state invalid")
        if not isinstance(self.created_at, (int, float)) or not isinstance(
            self.expires_at, (int, float)
        ):
            raise InteractiveIngressError(INVALID_INPUT, "preparation record timestamps invalid")

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "record_version": self.record_version,
            "interactive_schema": self.interactive_schema,
            "preparation_id": self.preparation_id,
            "instance_key": self.instance_key,
            "instance_dir": self.instance_dir,
            "worktree_root": self.worktree_root,
            "session_root": self.session_root,
            "created_at": float(self.created_at),
            "expires_at": float(self.expires_at),
            "state": self.state,
            "bound_session_id": self.bound_session_id,
            "bound_plan_ref": self.bound_plan_ref,
            "runtime_config_path": self.runtime_config_path,
            "registry_path": self.registry_path,
        }
        if self.extra:
            record["extra"] = dict(self.extra)
        return record

    @classmethod
    def from_record(cls, data: Mapping[str, Any]) -> "InteractivePreparation":
        if not isinstance(data, Mapping):
            raise InteractiveIngressError(INVALID_INPUT, "preparation record must be a mapping")
        if data.get("record_version") != PREPARATION_RECORD_VERSION:
            raise InteractiveIngressError(INVALID_INPUT, "preparation record version mismatch")
        if data.get("interactive_schema") != INTERACTIVE_SCHEMA:
            raise InteractiveIngressError(INVALID_INPUT, "preparation record schema mismatch")
        known = {
            "record_version",
            "interactive_schema",
            "preparation_id",
            "instance_key",
            "instance_dir",
            "worktree_root",
            "session_root",
            "created_at",
            "expires_at",
            "state",
            "bound_session_id",
            "bound_plan_ref",
            "runtime_config_path",
            "registry_path",
            "extra",
        }
        unknown = set(data) - known
        if unknown:
            raise InteractiveIngressError(
                INVALID_INPUT, f"preparation record has unknown keys: {sorted(unknown)!r}"
            )
        extra = data.get("extra") or {}
        if not isinstance(extra, Mapping):
            raise InteractiveIngressError(INVALID_INPUT, "preparation record extra invalid")
        return cls(
            preparation_id=str(data.get("preparation_id") or ""),
            instance_key=str(data.get("instance_key") or ""),
            instance_dir=str(data.get("instance_dir") or ""),
            worktree_root=str(data.get("worktree_root") or ""),
            session_root=str(data.get("session_root") or ""),
            created_at=data.get("created_at"),
            expires_at=data.get("expires_at"),
            state=str(data.get("state") or ""),
            bound_session_id=str(data.get("bound_session_id") or ""),
            bound_plan_ref=str(data.get("bound_plan_ref") or ""),
            runtime_config_path=str(data.get("runtime_config_path") or ""),
            registry_path=str(data.get("registry_path") or ""),
            extra=dict(extra),
        )

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            tmp.chmod(0o600)
        except Exception:
            pass
        tmp.replace(target)
        try:
            target.chmod(0o600)
        except Exception:
            pass
        return target

    @classmethod
    def read(cls, path: str | Path) -> "InteractivePreparation":
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise InteractiveIngressError(INVALID_INPUT, f"preparation record missing: {target}")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - unreadable record fails closed
            raise InteractiveIngressError(
                INVALID_INPUT, f"preparation record unreadable: {type(exc).__name__}"
            ) from exc
        return cls.from_record(data)

    def is_expired(self, now: float) -> bool:
        return float(now) > float(self.expires_at)


def new_preparation(
    *,
    workspace_root: Path,
    ttl_seconds: float = DEFAULT_PREPARATION_TTL_SECONDS,
    now: float | None = None,
    runtime_config_path: str = "",
    registry_path: str = "",
) -> InteractivePreparation:
    """Mint one mechanical preparation record (no Plan, no authority)."""
    now_value = time.time() if now is None else float(now)
    if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
        raise InteractiveIngressError(INVALID_INPUT, "preparation ttl must be a positive number")
    preparation_id = new_preparation_id()
    instance_key = preparation_instance_key(preparation_id)
    active_worktree = preparation_active_worktree(workspace_root, preparation_id)
    instance_dir = active_worktree / ".aota" / "opencode" / "instances" / instance_key
    return InteractivePreparation(
        preparation_id=preparation_id,
        instance_key=instance_key,
        instance_dir=str(instance_dir),
        worktree_root=str(active_worktree),
        session_root=str(preparation_session_root(workspace_root, preparation_id)),
        created_at=now_value,
        expires_at=now_value + float(ttl_seconds),
        runtime_config_path=runtime_config_path,
        registry_path=registry_path,
    )


__all__ = [
    "CONTRACT_OWNER",
    "DEFAULT_PREPARATION_TTL_SECONDS",
    "INTERACTIVE_SCHEMA",
    "MAX_MESSAGE_BYTES",
    "PLAN_REF_TOKEN_RE",
    "PREPARATION_RECORD_VERSION",
    "PREPARATION_STATE_BOUND",
    "PREPARATION_STATE_PREPARED",
    "PREPARATION_STATES",
    "SESSION_METADATA_PREPARATION_KEY",
    "SESSION_METADATA_SCHEMA_KEY",
    "InteractiveIngressError",
    "InteractivePreparation",
    "derive_interactive_plan_id",
    "ensure_error",
    "extract_canonical_plan_refs",
    "new_preparation",
    "new_preparation_id",
    "normalize_message",
    "preparation_active_worktree",
    "preparation_bind_receipt_path",
    "preparation_instance_key",
    "preparation_record_path",
    "preparation_session_root",
    "require_bounded_profile",
    "require_single_plan_ref",
    "TRUSTED_PLAN_STATE_KEYS",
    "validate_trusted_plan_state",
]
