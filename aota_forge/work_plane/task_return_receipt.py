"""Bounded durable task.return receipt + semantic return evidence (AF #49 M1/W9).

Repairs I49-B007: process exit 0 was accepted as semantic success without a
valid governed ``task.return``. Source inspection proved the existing durable
seams could NOT distinguish:

* ``handoff.write(mode=result)`` written only (explicitly non-terminal by the
  accepted agent-facing contract), from
* a valid ``task.return`` for the exact active execution,

because ``task.return`` deliberately performs no parent-store mutation
(``TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE=no``) and produced no
durable marker of its own. This module therefore adds exactly ONE bounded
durable receipt, written ONLY by the trusted ``task_facade.task_return`` path.

Hard boundaries (AF #49 M1/W9 §15/§45):

* the receipt is mechanical governance evidence, NOT a new result ontology,
  NOT a new task state, NOT a duplicate of semantic result content;
* it is bound to the exact ``canonical_task_id`` and the exact durable result
  handoff ref/digest;
* it is durable across process restart (atomic file under the trusted worktree
  ``.aota`` boundary, same storage idiom as the existing handoff store);
* it is non-authoritative by possession: the parent-side semantic-return
  resolver re-opens and digest-verifies the referenced result handoff and
  re-checks the exact task binding before any evidence claim is made.

The resolver reuses the EXISTING governed handoff store (digest recomputation,
cross-project/cross-worktree fail-closed, tamper fail-closed). No second
result store, no hydration protocol change, no new authority engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

TASK_RETURN_RECEIPT_SCHEMA_VERSION = "1"
TASK_RETURN_RECEIPT_DIR = "task_return_receipts"
TASK_RETURN_RECEIPT_STATUSES = ("completed", "blocked", "failed")
TASK_RETURN_RECEIPT_SOURCE_ROLES = ("coder", "analyst", "reviewer", "project-steward")
MAX_TASK_RETURN_RECEIPT_BYTES = 4096
_MAX_REF_LENGTH = 512

# Truthful governance markers (AF #49 M1/W9).
TASK_RETURN_RECEIPT_WRITTEN_ONLY_BY_TRUSTED_TASK_RETURN = True
NEW_RESULT_ONTOLOGY_CREATED = False
NEW_TASK_STATE_CREATED = False
TASK_RETURN_RECEIPT_IS_AUTHORITY = False
PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_digest(value: object, label: str = "result_digest") -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ValueError(f"{label} must be a string, got {type(value).__name__}")
    d = value.strip().lower()
    if len(d) != 64 or not all(c in "0123456789abcdef" for c in d):
        raise ValueError(f"{label} must be 64 lower hex chars")
    return d


def _validate_bounded_ref(value: object, label: str = "result_ref") -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ValueError(f"{label} must be a string, got {type(value).__name__}")
    ref = value.strip()
    if not ref:
        raise ValueError(f"{label} must be non-empty")
    if len(ref) > _MAX_REF_LENGTH:
        raise ValueError(f"{label} length exceeds {_MAX_REF_LENGTH}")
    return ref


def _validate_task_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ValueError(f"canonical_task_id must be a string, got {type(value).__name__}")
    tid = value.strip()
    if not tid:
        raise ValueError("canonical_task_id must be non-empty")
    if len(tid) > 256:
        raise ValueError("canonical_task_id length exceeds 256")
    return tid


def _validate_status(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ValueError(f"status must be a string, got {type(value).__name__}")
    status = value.strip()
    if status not in TASK_RETURN_RECEIPT_STATUSES:
        raise ValueError(
            f"status must be one of {TASK_RETURN_RECEIPT_STATUSES}, got {status!r}"
        )
    return status


def _receipt_dir(sandbox: WorktreeSandboxBoundary) -> Path:
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ValueError("task.return receipt store requires WorktreeSandboxBoundary")
    return Path(sandbox.worktree_root) / ".aota" / TASK_RETURN_RECEIPT_DIR


def _receipt_path(sandbox: WorktreeSandboxBoundary, canonical_task_id: str) -> Path:
    return _receipt_dir(sandbox) / f"{_digest_text(canonical_task_id)}.json"


def _ensure_receipt_dir(sandbox: WorktreeSandboxBoundary) -> Path:
    d = _receipt_dir(sandbox)
    d.mkdir(parents=True, exist_ok=True)
    try:
        if d.is_symlink():
            raise ValueError(f"task.return receipt dir is symlink: {d}")
        worktree_canonical = Path(sandbox.worktree_root).resolve(strict=True)
        d.resolve(strict=False).relative_to(worktree_canonical)
    except (ValueError, OSError) as exc:
        raise ValueError(f"task.return receipt dir containment failed: {exc}") from exc
    return d


def _receipt_digest(payload: Mapping[str, Any]) -> str:
    return _digest_text(canonical_json(canonicalize(dict(payload), path="task_return_receipt")))


@dataclass(frozen=True)
class TaskReturnReceipt:
    """Bounded immutable durable ``task.return`` receipt (mechanical evidence)."""

    canonical_task_id: str
    result_ref: str
    result_digest: str
    status: str
    created_at: str
    receipt_digest: str
    schema_version: str = TASK_RETURN_RECEIPT_SCHEMA_VERSION

    def _content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canonical_task_id": self.canonical_task_id,
            "result_ref": self.result_ref,
            "result_digest": self.result_digest,
            "status": self.status,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._content()
        payload["receipt_digest"] = self.receipt_digest
        return canonicalize(payload, path="TaskReturnReceipt")  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskReturnReceipt":
        if not isinstance(data, Mapping):
            raise TypeError(f"receipt must be a mapping, got {type(data).__name__}")
        allowed = {
            "schema_version",
            "canonical_task_id",
            "result_ref",
            "result_digest",
            "status",
            "created_at",
            "receipt_digest",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"unknown field(s) in task.return receipt: {sorted(extra)}")
        missing = allowed - set(data.keys())
        if missing:
            raise ValueError(f"missing field(s) in task.return receipt: {sorted(missing)}")
        if data.get("schema_version") != TASK_RETURN_RECEIPT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported task.return receipt schema_version "
                f"{data.get('schema_version')!r}; fail closed"
            )
        canonical_task_id = _validate_task_id(data.get("canonical_task_id"))
        result_ref = _validate_bounded_ref(data.get("result_ref"))
        result_digest = _validate_digest(data.get("result_digest"))
        status = _validate_status(data.get("status"))
        created_at = data.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("created_at must be a non-empty string")
        receipt_digest = data.get("receipt_digest")
        if not isinstance(receipt_digest, str) or not receipt_digest.strip():
            raise ValueError("receipt_digest must be a non-empty string")
        receipt = cls(
            canonical_task_id=canonical_task_id,
            result_ref=result_ref,
            result_digest=result_digest,
            status=status,
            created_at=created_at.strip(),
            receipt_digest=receipt_digest.strip().lower(),
        )
        if _receipt_digest(receipt._content()) != receipt.receipt_digest:
            raise ValueError("task.return receipt digest mismatch — tamper fail closed")
        return receipt


def write_task_return_receipt(
    sandbox: WorktreeSandboxBoundary,
    *,
    canonical_task_id: str,
    result_ref: str,
    result_digest: str,
    status: str,
) -> TaskReturnReceipt:
    """Persist one bounded durable ``task.return`` receipt (trusted path only).

    Atomic, digest-verified, restart-readable. Identical re-write is an
    idempotent no-op; a contradicting receipt for the same exact canonical
    task fails closed. The receipt carries no semantic result content.
    """
    tid = _validate_task_id(canonical_task_id)
    ref = _validate_bounded_ref(result_ref)
    digest = _validate_digest(result_digest)
    status_norm = _validate_status(status)
    content = {
        "schema_version": TASK_RETURN_RECEIPT_SCHEMA_VERSION,
        "canonical_task_id": tid,
        "result_ref": ref,
        "result_digest": digest,
        "status": status_norm,
        "created_at": _now_iso(),
    }
    receipt = TaskReturnReceipt(
        canonical_task_id=tid,
        result_ref=ref,
        result_digest=digest,
        status=status_norm,
        created_at=content["created_at"],
        receipt_digest=_receipt_digest(content),
    )
    existing = read_task_return_receipt(sandbox, tid)
    if existing is not None:
        if (
            existing.result_ref == receipt.result_ref
            and existing.result_digest == receipt.result_digest
            and existing.status == receipt.status
        ):
            return existing
        raise ValueError(
            "task.return receipt already durable for this exact canonical task and "
            "contradicts the proposed return — fail closed"
        )
    receipt_dir = _ensure_receipt_dir(sandbox)
    path = _receipt_path(sandbox, tid)
    encoded = canonical_json(receipt.to_dict()).encode("utf-8")
    if len(encoded) > MAX_TASK_RETURN_RECEIPT_BYTES:
        raise ValueError("task.return receipt exceeds the bounded size")
    fd, tmp_name = tempfile.mkstemp(dir=str(receipt_dir), prefix=f".tmp-return-{digest[:8]}-")
    tmp_path = Path(tmp_name)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if tmp_path.is_symlink():
            tmp_path.unlink(missing_ok=True)
            raise ValueError("temp task.return receipt is symlink")
        tmp_path.resolve(strict=False).relative_to(receipt_dir.resolve(strict=False))
        tmp_path.replace(path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ValueError(f"task.return receipt persist failed: {exc}") from exc
    written = read_task_return_receipt(sandbox, tid)
    if written is None or written.to_dict() != receipt.to_dict():
        raise ValueError("task.return receipt read-back mismatch — fail closed")
    return written


def read_task_return_receipt(
    sandbox: WorktreeSandboxBoundary, canonical_task_id: str
) -> TaskReturnReceipt | None:
    """Read the exact durable receipt for ``canonical_task_id`` (None if absent).

    A malformed/tampered/foreign receipt fails closed (raises).
    """
    tid = _validate_task_id(canonical_task_id)
    path = _receipt_path(sandbox, tid)
    if not path.exists():
        return None
    try:
        receipt_dir = _receipt_dir(sandbox)
        path.resolve(strict=False).relative_to(receipt_dir.resolve(strict=False))
    except Exception as exc:
        raise ValueError(f"task.return receipt path containment failed: {exc}") from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"task.return receipt read failed: {exc}") from exc
    if len(text.encode("utf-8")) > MAX_TASK_RETURN_RECEIPT_BYTES:
        raise ValueError("task.return receipt exceeds the bounded size")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"task.return receipt load failed: {exc}") from exc
    receipt = TaskReturnReceipt.from_dict(data)
    if receipt.canonical_task_id != tid:
        raise ValueError(
            "task.return receipt canonical_task_id does not match the requested "
            "exact canonical task — fail closed"
        )
    return receipt


@dataclass(frozen=True)
class SemanticReturnEvidence:
    """Parent-side proven semantic return evidence for one exact execution.

    Exists ONLY when all of the following hold for the exact
    ``canonical_task_id``:

    * a durable ``task.return`` receipt written by the trusted task.return path;
    * the referenced durable result handoff re-opens, digest-verifies, and is
      mode=``result``;
    * the result handoff envelope belongs to the exact canonical task.

    It carries no authority by possession; the terminal-truth owner is the
    parent-side completion coordinator.
    """

    canonical_task_id: str
    result_ref: str
    result_digest: str
    status: str
    summary: str | None = None

    @property
    def permits_success(self) -> bool:
        """Only a valid governed ``task.return(status=completed)`` permits success."""
        return self.status == "completed"


class SemanticReturnEvidenceProvider(Protocol):
    """Executor-neutral semantic-return evidence seam consumed by the coordinator."""

    def resolve(self, record: Any) -> SemanticReturnEvidence | None: ...


def _bounded_summary(semantic: Mapping[str, Any]) -> str | None:
    for key in ("summary", "work_done", "objective"):
        value = semantic.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:512]
    blockers = semantic.get("blockers")
    if isinstance(blockers, list):
        joined = "; ".join(str(item) for item in blockers[:3]).strip()
        if joined:
            return joined[:512]
    if isinstance(blockers, str) and blockers.strip():
        return blockers.strip()[:512]
    return None


def resolve_semantic_return_evidence(
    sandbox: WorktreeSandboxBoundary, canonical_task_id: str
) -> SemanticReturnEvidence | None:
    """Resolve proven semantic return evidence for the exact canonical task.

    Fail closed: any missing/unresolvable/foreign/tampered piece yields None
    (never a partial success claim). Reuses the existing governed handoff store
    for result identity/digest validation.
    """
    try:
        receipt = read_task_return_receipt(sandbox, canonical_task_id)
    except Exception:
        return None
    if receipt is None or receipt.canonical_task_id != canonical_task_id:
        return None
    try:
        from aota_forge.work_plane.handoff_store import handoff_open

        opened = handoff_open(receipt.result_ref, "full", sandbox=sandbox)
    except Exception:
        return None
    if opened.get("mode") != "result":
        return None
    opened_digest = opened.get("digest")
    if not isinstance(opened_digest, str) or opened_digest.strip().lower() != receipt.result_digest:
        return None
    envelope = opened.get("envelope")
    if not isinstance(envelope, Mapping):
        return None
    if envelope.get("task_id") != canonical_task_id:
        return None
    source_role = envelope.get("source_role")
    if source_role not in TASK_RETURN_RECEIPT_SOURCE_ROLES:
        return None
    semantic = opened.get("semantic")
    summary = _bounded_summary(semantic) if isinstance(semantic, Mapping) else None
    return SemanticReturnEvidence(
        canonical_task_id=canonical_task_id,
        result_ref=receipt.result_ref,
        result_digest=receipt.result_digest,
        status=receipt.status,
        summary=summary,
    )


class WorktreeSemanticReturnEvidenceProvider:
    """Evidence provider over one trusted worktree sandbox (production adapter).

    The sandbox may be resolved lazily (composition-time closure) so the
    provider can be constructed before the trusted sandbox object exists;
    resolution failure is a fail-closed ``None`` (never a false success). The
    provider is a protocol implementation only: it reads durable evidence and
    decides nothing.
    """

    def __init__(
        self,
        sandbox: WorktreeSandboxBoundary | None = None,
        *,
        sandbox_resolver: Callable[[], WorktreeSandboxBoundary | None] | None = None,
    ) -> None:
        if sandbox is None and sandbox_resolver is None:
            raise ValueError("provider requires a sandbox or a sandbox_resolver")
        self._sandbox = sandbox
        self._sandbox_resolver = sandbox_resolver

    def _resolve_sandbox(self) -> WorktreeSandboxBoundary | None:
        if self._sandbox is not None:
            return self._sandbox
        assert self._sandbox_resolver is not None
        try:
            return self._sandbox_resolver()
        except Exception:
            return None

    def resolve(self, record: Any) -> SemanticReturnEvidence | None:
        sandbox = self._resolve_sandbox()
        if sandbox is None:
            return None
        task_id = getattr(record, "canonical_task_id", None)
        if not isinstance(task_id, str) or not task_id.strip():
            return None
        return resolve_semantic_return_evidence(sandbox, task_id.strip())


__all__ = [
    "MAX_TASK_RETURN_RECEIPT_BYTES",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_TASK_STATE_CREATED",
    "PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS",
    "SemanticReturnEvidence",
    "SemanticReturnEvidenceProvider",
    "TASK_RETURN_RECEIPT_DIR",
    "TASK_RETURN_RECEIPT_IS_AUTHORITY",
    "TASK_RETURN_RECEIPT_SCHEMA_VERSION",
    "TASK_RETURN_RECEIPT_SOURCE_ROLES",
    "TASK_RETURN_RECEIPT_STATUSES",
    "TASK_RETURN_RECEIPT_WRITTEN_ONLY_BY_TRUSTED_TASK_RETURN",
    "TaskReturnReceipt",
    "WorktreeSemanticReturnEvidenceProvider",
    "read_task_return_receipt",
    "resolve_semantic_return_evidence",
    "write_task_return_receipt",
]
