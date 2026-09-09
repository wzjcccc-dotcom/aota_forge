"""Typed immutable MaterializationReceipt (M3/W2).

Proves mechanical effects of the trusted Steward finalizer. It is evidence,
never authority:

    MATERIALIZATION_RECEIPT_IS_PLAN_AUTHORITY=no
    MATERIALIZATION_RECEIPT_IS_USER_APPROVAL_AUTHORITY=no

Digest-bound, idempotent, crash-recoverable. No secrets/tokens are stored.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

# ---------------------------------------------------------------------------
# Authority markers
# ---------------------------------------------------------------------------

MATERIALIZATION_RECEIPT_CREATED = True
MATERIALIZATION_RECEIPT_IS_DIGEST_BOUND = True
MATERIALIZATION_RECEIPT_IS_PLAN_AUTHORITY = False
MATERIALIZATION_RECEIPT_IS_USER_APPROVAL_AUTHORITY = False

# Receipt never claims distributed atomicity; per-operation CAS + durable
# receipt + restart reconciliation is the recovery model.
DISTRIBUTED_GLOBAL_ATOMICITY_CLAIMED = False

# Bounded capacities
MAX_OPERATIONS = 16
MAX_REF_LENGTH = 512
MAX_DIGEST_LENGTH = 128
MAX_SCOPE_KEYS = 16

OPERATION_STATUSES: frozenset[str] = frozenset(
    {"applied", "already_applied", "failed", "skipped"}
)
FINAL_STATUSES: frozenset[str] = frozenset(
    {"APPLIED", "ALREADY_APPLIED", "PARTIAL", "BLOCKED"}
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_[A-Za-z0-9]+"),
    re.compile(r"gho_[A-Za-z0-9]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"(?i)\bgithub_token\b"),
    re.compile(r"(?i)\baccess_token\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_bounded_str(value: Any, label: str, max_len: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace")
    if not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_len}")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _scan_no_secrets(value: Any, label: str) -> None:
    """Fail closed if any string field looks like a credential."""
    if isinstance(value, str):
        for pat in _SECRET_PATTERNS:
            if pat.search(value):
                raise ValueError(f"{label} must not contain secrets/credentials")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _scan_no_secrets(k, f"{label}.key")
            _scan_no_secrets(v, f"{label}.{k}")
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            _scan_no_secrets(item, f"{label}[{idx}]")


@dataclass(frozen=True)
class OperationReceipt:
    """Per-operation mechanical evidence (before/after, status)."""

    kind: str
    target: str
    before_ref: str | None = None
    before_digest: str | None = None
    after_ref: str | None = None
    after_digest: str | None = None
    status: str = "applied"
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_bounded_str(self.kind, "kind", 128))
        object.__setattr__(self, "target", _require_bounded_str(self.target, "target", MAX_REF_LENGTH))
        for label in ("before_ref", "after_ref"):
            val = getattr(self, label)
            if val is not None:
                object.__setattr__(self, label, _require_bounded_str(val, label, MAX_REF_LENGTH))
        for label in ("before_digest", "after_digest"):
            val = getattr(self, label)
            if val is not None:
                object.__setattr__(self, label, _require_bounded_str(val, label, MAX_DIGEST_LENGTH))
        if self.status not in OPERATION_STATUSES:
            raise ValueError(f"status must be one of {sorted(OPERATION_STATUSES)}, got {self.status!r}")
        if self.detail is not None:
            object.__setattr__(self, "detail", _require_bounded_str(self.detail, "detail", 512))
        _scan_no_secrets(self.kind, "kind")
        _scan_no_secrets(self.target, "target")
        _scan_no_secrets(self.detail or "", "detail")

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "target": self.target, "status": self.status}
        if self.before_ref is not None:
            d["before_ref"] = self.before_ref
        if self.before_digest is not None:
            d["before_digest"] = self.before_digest
        if self.after_ref is not None:
            d["after_ref"] = self.after_ref
        if self.after_digest is not None:
            d["after_digest"] = self.after_digest
        if self.detail is not None:
            d["detail"] = self.detail
        return d  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationReceipt:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        allowed = {"kind", "target", "before_ref", "before_digest", "after_ref", "after_digest", "status", "detail"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in OperationReceipt: {sorted(extra)}")
        return cls(
            kind=data["kind"],
            target=data["target"],
            before_ref=data.get("before_ref"),
            before_digest=data.get("before_digest"),
            after_ref=data.get("after_ref"),
            after_digest=data.get("after_digest"),
            status=data.get("status", "applied"),
            detail=data.get("detail"),
        )


_ALLOWED_RECEIPT_FIELDS: frozenset[str] = frozenset(
    {
        "receipt_id",
        "project_id",
        "plan_ref",
        "milestone_ref",
        "closure_phase",
        "steward_digest",
        "binding_digest",
        "materialization_scope",
        "operations",
        "git_final_refs",
        "git_final_tree",
        "github_evidence",
        "idempotency_key",
        "final_status",
        "reconciled_at",
        "receipt_digest",
    }
)


@dataclass(frozen=True)
class MaterializationReceipt:
    """Immutable digest-bound proof of mechanical effects."""

    receipt_id: str
    project_id: str
    plan_ref: str
    milestone_ref: str
    closure_phase: str
    steward_digest: str
    binding_digest: str
    materialization_scope: dict[str, Any]
    operations: tuple[OperationReceipt, ...] = ()
    git_final_refs: dict[str, Any] | None = None
    git_final_tree: str | None = None
    github_evidence: dict[str, Any] | None = None
    idempotency_key: str = ""
    final_status: str = "APPLIED"
    reconciled_at: str = ""
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _require_bounded_str(self.receipt_id, "receipt_id", 128))
        object.__setattr__(self, "project_id", _require_bounded_str(self.project_id, "project_id", 128))
        object.__setattr__(self, "plan_ref", _require_bounded_str(self.plan_ref, "plan_ref", MAX_REF_LENGTH))
        object.__setattr__(self, "milestone_ref", _require_bounded_str(self.milestone_ref, "milestone_ref", 128))
        object.__setattr__(self, "closure_phase", _require_bounded_str(self.closure_phase, "closure_phase", 32))
        if self.closure_phase not in ("REVIEWED_CLOSURE", "ACCEPTED_CLOSURE"):
            raise ValueError(f"closure_phase must be REVIEWED_CLOSURE/ACCEPTED_CLOSURE, got {self.closure_phase!r}")
        object.__setattr__(self, "steward_digest", _require_bounded_str(self.steward_digest, "steward_digest", MAX_DIGEST_LENGTH))
        object.__setattr__(self, "binding_digest", _require_bounded_str(self.binding_digest, "binding_digest", MAX_DIGEST_LENGTH))
        if not isinstance(self.materialization_scope, dict):
            raise TypeError("materialization_scope must be dict")
        if len(self.materialization_scope) > MAX_SCOPE_KEYS:
            raise ValueError(f"materialization_scope exceeds {MAX_SCOPE_KEYS} keys")
        # Scope must be JSON-native
        canonicalize(dict(self.materialization_scope), path="materialization_scope")
        object.__setattr__(self, "materialization_scope", dict(self.materialization_scope))
        if not isinstance(self.operations, (tuple, list)):
            raise TypeError("operations must be tuple/list")
        if len(self.operations) > MAX_OPERATIONS:
            raise ValueError(f"operations count exceeds {MAX_OPERATIONS}")
        norm_ops: list[OperationReceipt] = []
        for idx, op in enumerate(self.operations):
            if not isinstance(op, OperationReceipt):
                raise TypeError(f"operations[{idx}] must be OperationReceipt")
            norm_ops.append(op)
        object.__setattr__(self, "operations", tuple(norm_ops))
        if self.git_final_tree is not None:
            object.__setattr__(self, "git_final_tree", _require_bounded_str(self.git_final_tree, "git_final_tree", MAX_DIGEST_LENGTH))
        if self.git_final_refs is not None:
            if not isinstance(self.git_final_refs, dict):
                raise TypeError("git_final_refs must be dict or None")
            canonicalize(dict(self.git_final_refs), path="git_final_refs")
            object.__setattr__(self, "git_final_refs", dict(self.git_final_refs))
        if self.github_evidence is not None:
            if not isinstance(self.github_evidence, dict):
                raise TypeError("github_evidence must be dict or None")
            canonicalize(dict(self.github_evidence), path="github_evidence")
            object.__setattr__(self, "github_evidence", dict(self.github_evidence))
        object.__setattr__(self, "idempotency_key", _require_bounded_str(self.idempotency_key, "idempotency_key", 256))
        if self.final_status not in FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {sorted(FINAL_STATUSES)}, got {self.final_status!r}")
        object.__setattr__(self, "reconciled_at", _require_bounded_str(self.reconciled_at, "reconciled_at", 64))
        if not isinstance(self.receipt_digest, str) or type(self.receipt_digest) is not str:
            raise TypeError("receipt_digest must be str")
        # Secrets must never appear in receipt
        _scan_no_secrets(self.receipt_id, "receipt_id")
        _scan_no_secrets(self.materialization_scope, "materialization_scope")
        _scan_no_secrets(self.git_final_refs or {}, "git_final_refs")
        _scan_no_secrets(self.github_evidence or {}, "github_evidence")
        _scan_no_secrets(self.idempotency_key, "idempotency_key")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "closure_phase": self.closure_phase,
            "binding_digest": self.binding_digest,
            "final_status": self.final_status,
            "git_final_refs": self.git_final_refs,
            "git_final_tree": self.git_final_tree,
            "github_evidence": self.github_evidence,
            "idempotency_key": self.idempotency_key,
            "materialization_scope": dict(self.materialization_scope),
            "milestone_ref": self.milestone_ref,
            "operations": [op.canonical_dict() for op in self.operations],
            "plan_ref": self.plan_ref,
            "project_id": self.project_id,
            "receipt_id": self.receipt_id,
            "reconciled_at": self.reconciled_at,
            "steward_digest": self.steward_digest,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(
            canonical_json(canonicalize(self.canonical_dict(), path="MaterializationReceipt")).encode("utf-8")
        ).hexdigest()

    def with_digest(self) -> MaterializationReceipt:
        return replace(self, receipt_digest=self.compute_digest())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["receipt_digest"] = self.receipt_digest
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MaterializationReceipt:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_RECEIPT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in MaterializationReceipt: {sorted(extra)}")
        for req in (
            "receipt_id", "project_id", "plan_ref", "milestone_ref", "closure_phase",
            "steward_digest", "binding_digest", "materialization_scope",
            "idempotency_key", "final_status", "reconciled_at", "receipt_digest",
        ):
            if req not in data:
                raise ValueError(f"Missing required field in MaterializationReceipt: {req!r}")
        ops = tuple(OperationReceipt.from_dict(o) for o in (data.get("operations") or ()))
        receipt = cls(
            receipt_id=data["receipt_id"],
            project_id=data["project_id"],
            plan_ref=data["plan_ref"],
            milestone_ref=data["milestone_ref"],
            closure_phase=data["closure_phase"],
            steward_digest=data["steward_digest"],
            binding_digest=data["binding_digest"],
            materialization_scope=dict(data["materialization_scope"]),
            operations=ops,
            git_final_refs=dict(data["git_final_refs"]) if data.get("git_final_refs") is not None else None,
            git_final_tree=data.get("git_final_tree"),
            github_evidence=dict(data["github_evidence"]) if data.get("github_evidence") is not None else None,
            idempotency_key=data["idempotency_key"],
            final_status=data["final_status"],
            reconciled_at=data["reconciled_at"],
            receipt_digest=data["receipt_digest"],
        )
        if receipt.receipt_digest != receipt.compute_digest():
            raise ValueError("MaterializationReceipt digest mismatch: stored evidence fails closed")
        return receipt


__all__ = [
    "MATERIALIZATION_RECEIPT_CREATED",
    "MATERIALIZATION_RECEIPT_IS_DIGEST_BOUND",
    "MATERIALIZATION_RECEIPT_IS_PLAN_AUTHORITY",
    "MATERIALIZATION_RECEIPT_IS_USER_APPROVAL_AUTHORITY",
    "DISTRIBUTED_GLOBAL_ATOMICITY_CLAIMED",
    "OperationReceipt",
    "MaterializationReceipt",
]
