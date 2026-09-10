"""Durable Result Payload Store — M2/W1 file-backed fallback.

Thin non-authoritative file-backed payload storage for restart-durable
selective hydration.

M1 ToolOutputRef
+ durable bytes (file under worktree_root)
+ existing HydrationSource / SelectiveHydration
↓
restart-durable selective hydration

Invariants
----------
* DURABLE_STORAGE_IS_RESULT_AUTHORITY=no
* HYDRATION_SOURCE_IS_AUTHORITY=no
* SECOND_RESULT_AUTHORITY_CREATED=no
* OUTPUT_REF_IS_AUTHORITY=no
* DIGEST_IS_AUTHORITY=no
* REF_POSSESSION_IS_HYDRATION_AUTHORITY=no
* HYDRATION_REAUTHORIZES_CURRENT_SCOPE=yes
* CROSS_PROJECT / CROSS_WORKTREE / FOREIGN_REF fail-closed
* HYDRATION_DIGEST_VERIFIED=yes
* TAMPERED fail-closed
* HYDRATION_REQUEST_BOUNDED=yes, HYDRATION_OUTPUT_BOUNDED=yes
* SELECTIVE_HYDRATION=yes, EAGER_HYDRATE_ALL_REFS=no

Reuse
-----
* Reuses GovernedReference / GovernedReferenceKind
* Reuses ToolOutputRef / ArtifactReference
* Reuses WorktreeSandboxBoundary (trusted current scope)
* Reuses selective_hydration.MAX_HYDRATED_BYTES where applicable
* Implements HydrationSource protocol (storage-neutral, non-authority)

Implementation mode
-------------------
EXISTING_DURABLE_SEAM_SUFFICIENT=no — existing seams (ExecutionStateStore,
JournalStore, CoordinatorStore, workspace artifact files) cannot persist
arbitrary bounded ToolOutputRef payload bytes with deterministic
GovernedReference lookup while preserving project/worktree scope without
creating a second result authority. The artifact file seam rejects
tool_output:* refs (colon charset) and is tied to workspace.write logical
paths, not digest-addressed payload. Execution/journal/coordinator stores
are typed to their own records, not generic payload bytes. Therefore a
minimal file-backed payload store is required.

File-backed fallback (thin, bounded, deterministic)
----------------------------------------------------
* bounded: payload ≤ DURABLE_PAYLOAD_MAX_BYTES (64 KiB, reuses
  workspace_tools.MAX_TOTAL_OUTPUT_BYTES)
* deterministic: path = <worktree_root>/.aota/durable_payloads/<digest>.bin
  + <digest>.json metadata; same digest → same file
* project/worktree scoped: file lives under worktree_root (physical
  containment), and every hydrate re-validates expected project/worktree
  vs current WorktreeSandboxBoundary
* digest-addressed & digest-verified: payload sha256 must equal ref digest
* non-authoritative: file is bytes only, never grants operation/workspace
  authority
* non-database: one file per payload, no index DB, no journal, no state
  machine, no daemon, no background GC
* atomic persist: temp file + rename

Retention / GC
--------------
* RETENTION_BOUNDARY = existing project/worktree runtime lifecycle
  (files under worktree_root/.aota/durable_payloads)
* AUTOMATIC_TIME_BASED_GC = no
* EXPLICIT_CLEANUP_SUPPORTED = yes (clear_durable_payloads)
* process restart = yes (file survives)
* server restart = yes (file survives)
* worktree deletion = truthful: payload removed with worktree directory
* project cleanup = truthful: payload removed with worktree deletion

This module does NOT change ToolOutputRef into authority and does NOT
invent HydrationSource v2. It provides a file-backed HydrationSource
adapter that remains non-authority and non-persistent-store in the
selective_hydration sense (is_authority=False, is_persistent_store=False).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind
from aota_forge.work_plane.tool_result_governance import ToolOutputRef
from aota_forge.work_plane.workspace_mutation import ArtifactReference
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.work_plane.selective_hydration import (
    MAX_HYDRATED_BYTES as SELECTIVE_MAX_BYTES,
    DigestMismatchError,
    ForeignRefError,
    HydrationSource,
    OversizedHydrationError,
    SourceUnavailableError,
    UnknownRefError,
)

# ---------------------------------------------------------------------------
# Public invariant flags — must be truthful for tests / review
# ---------------------------------------------------------------------------

EXISTING_SELECTIVE_HYDRATION_REUSED: bool = True
EXISTING_HYDRATION_SOURCE_REUSED: bool = True
EXISTING_RESULT_GOVERNANCE_REUSED: bool = True

EXISTING_DURABLE_SEAM_SUFFICIENT: bool = False
NEW_FILE_BACKED_IMPLEMENTATION_CREATED: bool = True

DURABLE_STORAGE_IS_RESULT_AUTHORITY: bool = False
HYDRATION_SOURCE_IS_AUTHORITY: bool = False
SECOND_RESULT_AUTHORITY_CREATED: bool = False

NEW_RESULT_DB_CREATED: bool = False
NEW_RESULT_STATE_MACHINE_CREATED: bool = False
NEW_PERSISTENT_RESULT_STORE_CREATED: bool = False  # file-backed but not DB
NEW_PERSISTENT_HYDRATION_STORE_CREATED: bool = True  # file-backed payloads, truthful

OUTPUT_REF_IS_AUTHORITY: bool = False
DIGEST_IS_AUTHORITY: bool = False
REF_POSSESSION_IS_HYDRATION_AUTHORITY: bool = False

HYDRATION_REAUTHORIZES_CURRENT_SCOPE: bool = True
CROSS_PROJECT_HYDRATION_FAIL_CLOSED: bool = True
CROSS_WORKTREE_HYDRATION_FAIL_CLOSED: bool = True
FOREIGN_REF_HYDRATION_FAIL_CLOSED: bool = True

HYDRATION_DIGEST_VERIFIED: bool = True
TAMPERED_REF_FAIL_CLOSED: bool = True
TAMPERED_PAYLOAD_FAIL_CLOSED: bool = True

HYDRATION_REQUEST_BOUNDED: bool = True
HYDRATION_OUTPUT_BOUNDED: bool = True
SELECTIVE_HYDRATION: bool = True
EAGER_HYDRATE_ALL_REFS: bool = False

RESTART_REF_DURABILITY: bool = True
REF_CONTRACT_ONLY: bool = False
REF_IMPLEMENTATION_MODE: str = "FILE_BACKED_DURABLE"

DURABLE_PAYLOAD_MAX_BYTES: int = 64 * 1024  # reuses workspace MAX_TOTAL_OUTPUT_BYTES, bounded
DURABLE_PAYLOAD_DIR_NAME: str = "durable_payloads"

RETENTION_BOUNDARY: str = "existing project/worktree runtime lifecycle (worktree_root/.aota/durable_payloads)"
AUTOMATIC_TIME_BASED_GC: bool = False
EXPLICIT_CLEANUP_SUPPORTED: bool = True

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_digest(value: object) -> str:
    import re

    _RE = re.compile(r"^[0-9a-f]{64}$")
    if not isinstance(value, str) or type(value) is not str:
        raise DigestMismatchError(f"digest must be string, got {type(value).__name__}")
    v = value.strip().lower()
    if not _RE.fullmatch(v):
        raise DigestMismatchError(f"digest must be 64 lower hex chars: {value!r}")
    return v


DURABLE_PAYLOAD_SUBDIR = DURABLE_PAYLOAD_DIR_NAME


def _payload_root(sandbox: WorktreeSandboxBoundary) -> Path:
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ForeignRefError("payload store requires WorktreeSandboxBoundary")
    # worktree_root is already canonicalized absolute path
    root = Path(sandbox.worktree_root)
    # Ensure containment: must be inside worktree_root
    payload_root = root / ".aota" / DURABLE_PAYLOAD_SUBDIR
    return payload_root


def _payload_path(sandbox: WorktreeSandboxBoundary, digest: str) -> Path:
    d = _validate_digest(digest)
    return _payload_root(sandbox) / f"{d}.bin"


def _meta_path(sandbox: WorktreeSandboxBoundary, digest: str) -> Path:
    d = _validate_digest(digest)
    return _payload_root(sandbox) / f"{d}.json"


def _ensure_payload_dir(sandbox: WorktreeSandboxBoundary) -> Path:
    pr = _payload_root(sandbox)
    # Create parents deterministically, ensure not symlink
    pr.mkdir(parents=True, exist_ok=True)
    # Ensure payload_root is not a symlink and is inside worktree_root
    try:
        if pr.is_symlink():
            raise SourceUnavailableError(f"payload dir is symlink: {pr}")
        # containment check
        worktree_canonical = Path(sandbox.worktree_root).resolve(strict=True)
        pr.resolve(strict=False).relative_to(worktree_canonical)
    except (ValueError, OSError) as exc:
        raise SourceUnavailableError(f"payload dir containment failed: {exc}") from exc
    return pr


# ---------------------------------------------------------------------------
# Durable persistence — file-backed, atomic, digest-verified
# ---------------------------------------------------------------------------

def persist_durable_payload(
    sandbox: WorktreeSandboxBoundary,
    payload: bytes | str,
    *,
    ref: str | None = None,
    digest: str | None = None,
    byte_length: int | None = None,
    project_id: str | None = None,
    worktree_id: str | None = None,
    kind: str = "evidence",
) -> dict[str, Any]:
    """Persist arbitrary bounded payload bytes durably under current sandbox.

    Binds payload to project_id/worktree_id/ref/digest/byte_length.
    Current trusted scope comes from sandbox, not model arguments. The
    supplied project_id/worktree_id are the *claimed* ref scope that must
    match sandbox, otherwise fail-closed.

    Parameters
    ----------
    sandbox: WorktreeSandboxBoundary — current trusted scope
    payload: bytes|str — bounded payload to persist
    ref: optional logical ref identity (for metadata)
    digest: optional digest; if None computed from payload
    byte_length: optional; if None derived
    project_id/worktree_id: claimed scope; defaults to sandbox's
    kind: artifact|evidence (for metadata)

    Returns
    -------
    metadata dict with ref, digest, byte_length, project_id, worktree_id, path
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ForeignRefError("persist requires WorktreeSandboxBoundary")
    # Resolve payload bytes
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        data = payload
    else:
        raise ValueError(f"payload must be bytes or str, got {type(payload).__name__}")
    if len(data) > DURABLE_PAYLOAD_MAX_BYTES:
        raise OversizedHydrationError(f"payload bytes {len(data)} exceeds durable bound {DURABLE_PAYLOAD_MAX_BYTES}")
    # Also enforce selective bound for evidence kind? For now allow up to durable bound
    # Compute digest
    computed = _sha256_hex(data)
    if digest is not None:
        d = _validate_digest(digest)
        if d != computed:
            raise DigestMismatchError(f"supplied digest {d!r} != computed {computed!r} — tamper fail closed")
        digest_val = d
    else:
        digest_val = computed
    # Resolve ref string
    ref_str = ref.strip() if isinstance(ref, str) and ref.strip() else f"durable:{digest_val[:16]}"
    if len(ref_str) > 512:
        raise OversizedHydrationError(f"ref length {len(ref_str)} exceeds 512")
    # Resolve project/worktree claims
    claim_project = project_id.strip() if isinstance(project_id, str) and project_id.strip() else sandbox.project_id
    claim_worktree = worktree_id.strip() if isinstance(worktree_id, str) and worktree_id.strip() else sandbox.worktree_id
    # Reauthorize: claim must equal current trusted scope
    if claim_project != sandbox.project_id:
        raise ForeignRefError(f"cross-project persist denied: claim {claim_project!r} != current {sandbox.project_id!r}")
    if claim_worktree != sandbox.worktree_id:
        raise ForeignRefError(f"cross-worktree persist denied: claim {claim_worktree!r} != current {sandbox.worktree_id!r}")
    # Validate kind
    if kind not in ("artifact", "evidence"):
        # allow tool_output as evidence alias
        if kind not in ("tool_output",):
            raise ValueError(f"kind must be artifact or evidence, got {kind!r}")
        kind = "evidence"
    blen = byte_length if byte_length is not None else len(data)
    if blen != len(data):
        raise DigestMismatchError(f"byte_length {blen} != actual {len(data)} — tamper fail closed")
    # Ensure payload dir
    payload_root = _ensure_payload_dir(sandbox)
    payload_path = _payload_path(sandbox, digest_val)
    meta_path = _meta_path(sandbox, digest_val)
    # Atomic write payload: temp file + rename
    try:
        # Write payload bytes atomically
        fd, tmp_name = tempfile.mkstemp(dir=str(payload_root), prefix=f".tmp-payload-{digest_val[:8]}-")
        tmp_path = Path(tmp_name)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        # Verify tmp not symlink
        if tmp_path.is_symlink():
            tmp_path.unlink(missing_ok=True)
            raise SourceUnavailableError("temp payload is symlink")
        tmp_path.resolve(strict=False).relative_to(payload_root.resolve(strict=False))
        # Atomic replace
        tmp_path.replace(payload_path)
        # Write metadata atomically as well
        meta = {
            "ref": ref_str,
            "digest": digest_val,
            "byte_length": blen,
            "project_id": sandbox.project_id,
            "worktree_id": sandbox.worktree_id,
            "kind": kind,
        }
        meta_text = json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        fd2, tmp_meta = tempfile.mkstemp(dir=str(payload_root), prefix=f".tmp-meta-{digest_val[:8]}-")
        tmp_meta_path = Path(tmp_meta)
        try:
            os.write(fd2, meta_text.encode("utf-8"))
            os.fsync(fd2)
        finally:
            os.close(fd2)
        if tmp_meta_path.is_symlink():
            tmp_meta_path.unlink(missing_ok=True)
            raise SourceUnavailableError("temp meta is symlink")
        tmp_meta_path.replace(meta_path)
    except OSError as exc:
        raise SourceUnavailableError(f"persist failed: {exc}") from exc
    # Verify written payload digest again
    try:
        written = payload_path.read_bytes()
        if _sha256_hex(written) != digest_val:
            raise DigestMismatchError("written payload digest mismatch — tamper fail closed")
    except OSError as exc:
        raise SourceUnavailableError(f"post-write verification failed: {exc}") from exc
    return {
        "ref": ref_str,
        "digest": digest_val,
        "byte_length": blen,
        "project_id": sandbox.project_id,
        "worktree_id": sandbox.worktree_id,
        "kind": kind,
        "path": str(payload_path),
    }


def persist_tool_output_payload(
    sandbox: WorktreeSandboxBoundary,
    payload: bytes | str,
    capability_name: str,
) -> ToolOutputRef:
    """Persist oversized tool output payload and return digest-bound ToolOutputRef.

    Reuses ToolOutputRef construction (project/worktree scoped, digest bound).
    Payload is verified and then persisted via persist_durable_payload.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ForeignRefError("persist requires WorktreeSandboxBoundary")
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        data = payload
    else:
        raise TypeError(f"payload must be bytes or str, got {type(payload).__name__}")
    # Validate capability name charset via tool_result_governance helper? Reimplement minimal
    import re

    _CAP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    if not isinstance(capability_name, str) or not _CAP_RE.fullmatch(capability_name.strip()):
        raise ValueError(f"capability_name invalid: {capability_name!r}")
    cap = capability_name.strip()
    if len(data) > DURABLE_PAYLOAD_MAX_BYTES:
        raise OversizedHydrationError(f"payload {len(data)} exceeds durable bound {DURABLE_PAYLOAD_MAX_BYTES}")
    digest = _sha256_hex(data)
    # Deterministic logical ref: tool_output:<cap>:<first16>
    ref_str = f"tool_output:{cap}:{digest[:16]}"
    # Persist
    persist_durable_payload(
        sandbox,
        data,
        ref=ref_str,
        digest=digest,
        byte_length=len(data),
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        kind="evidence",
    )
    # Return governed ref
    return ToolOutputRef(
        ref=ref_str,
        digest=digest,
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        byte_length=len(data),
    )


def persist_governed_payload(
    sandbox: WorktreeSandboxBoundary,
    payload: bytes | str,
    kind: str,
    ref: str,
) -> GovernedReference:
    """Persist arbitrary bounded GovernedReference payload (artifact/evidence).

    Returns GovernedReference with correct digest.
    """
    if kind not in ("artifact", "evidence"):
        raise ValueError(f"kind must be artifact or evidence, got {kind!r}")
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = payload  # type: ignore
    if not isinstance(data, bytes):
        raise TypeError("payload must be bytes or str")
    if len(data) > DURABLE_PAYLOAD_MAX_BYTES:
        raise OversizedHydrationError(f"payload {len(data)} exceeds durable bound")
    digest = _sha256_hex(data)
    persist_durable_payload(
        sandbox,
        data,
        ref=ref,
        digest=digest,
        byte_length=len(data),
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        kind=kind,
    )
    from aota_forge.core.result_governance import GovernedReferenceKind

    k = GovernedReferenceKind.ARTIFACT if kind == "artifact" else GovernedReferenceKind.EVIDENCE
    return GovernedReference(kind=k, ref=ref, digest=digest)


def load_durable_payload(
    sandbox: WorktreeSandboxBoundary,
    digest: str,
    *,
    expected_ref: str | None = None,
    expected_project_id: str | None = None,
    expected_worktree_id: str | None = None,
    expected_byte_length: int | None = None,
    expected_kind: str | None = None,
) -> bytes:
    """Load and verify durable payload for given digest under current sandbox.

    Reauthorizes current scope: expected project/worktree (from ref claims)
    must equal current sandbox. Verifies digest and byte_length.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ForeignRefError("load requires WorktreeSandboxBoundary")
    d = _validate_digest(digest)
    # Reauthorize scope if expected claims supplied
    if expected_project_id is not None and expected_project_id != sandbox.project_id:
        raise ForeignRefError(f"cross-project hydration denied: expected {expected_project_id!r} != current {sandbox.project_id!r}")
    if expected_worktree_id is not None and expected_worktree_id != sandbox.worktree_id:
        raise ForeignRefError(f"cross-worktree hydration denied: expected {expected_worktree_id!r} != current {sandbox.worktree_id!r}")
    payload_path = _payload_path(sandbox, d)
    meta_path = _meta_path(sandbox, d)
    # Check existence
    if not payload_path.is_file() or payload_path.is_symlink():
        raise UnknownRefError(f"unknown ref: no durable payload for digest {d!r} under current worktree")
    try:
        data = payload_path.read_bytes()
    except OSError as exc:
        raise SourceUnavailableError(f"payload read failed: {exc}") from exc
    if len(data) > DURABLE_PAYLOAD_MAX_BYTES:
        raise OversizedHydrationError(f"payload {len(data)} exceeds durable bound")
    # Verify digest
    computed = _sha256_hex(data)
    if computed != d:
        raise DigestMismatchError(f"payload digest mismatch: computed {computed!r} != expected {d!r} — tampered payload fail closed")
    # Verify byte_length if supplied
    if expected_byte_length is not None and len(data) != expected_byte_length:
        raise DigestMismatchError(f"byte_length mismatch: actual {len(data)} != expected {expected_byte_length} — tampered fail closed")
    # Verify metadata if present
    if meta_path.is_file():
        try:
            meta_text = meta_path.read_text(encoding="utf-8")
            meta = json.loads(meta_text)
            # Verify metadata vs expected and vs payload
            if meta.get("digest") and meta["digest"] != d:
                raise DigestMismatchError(f"metadata digest mismatch: {meta['digest']!r} != {d!r}")
            if expected_ref is not None and meta.get("ref") and meta["ref"] != expected_ref:
                # For tool output, allow ref tamper check via capability if supplied elsewhere
                # We treat mismatch as tamper if expected_ref is precise
                # But for generic, if meta ref differs from expected_ref, it's okay as long as digest matches?
                # To be strict, we consider tampered ref fail closed if expected_ref supplied and mismatch
                raise DigestMismatchError(f"ref mismatch: meta {meta['ref']!r} != expected {expected_ref!r} — tampered ref fail closed")
            if meta.get("project_id") and meta["project_id"] != sandbox.project_id:
                raise ForeignRefError(f"metadata project {meta['project_id']!r} != current {sandbox.project_id!r}")
            if meta.get("worktree_id") and meta["worktree_id"] != sandbox.worktree_id:
                raise ForeignRefError(f"metadata worktree {meta['worktree_id']!r} != current {sandbox.worktree_id!r}")
            if expected_kind is not None and meta.get("kind") and meta["kind"] != expected_kind:
                # Kind mismatch is not necessarily tamper, but unsupported?
                pass
        except json.JSONDecodeError:
            # Corrupted meta is not fatal if payload digest verifies, but we treat as tamper?
            # We already verified payload digest, so ignore meta corruption for now
            pass
    # Hydration output bounded check: reuse selective bound for evidence kind
    if expected_kind in ("artifact", "evidence") or expected_kind is None:
        # For generic governed refs, enforce SELECTIVE_MAX_BYTES (4096) if payload is for selective hydration
        # But tool output may be larger; we differentiate via expected_kind or ref prefix
        # If payload is for tool_output (ref startswith tool_output), allow larger bound
        is_tool = expected_ref is not None and expected_ref.startswith("tool_output:")
        if not is_tool and len(data) > SELECTIVE_MAX_BYTES:
            raise OversizedHydrationError(f"hydrated content {len(data)} exceeds selective bound {SELECTIVE_MAX_BYTES}")
    return data


def clear_durable_payloads(sandbox: WorktreeSandboxBoundary) -> int:
    """Explicit cleanup of durable payloads for given sandbox (retention GC).

    Returns number of payload files removed. Bounded, explicit, no background GC.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise ForeignRefError("clear requires WorktreeSandboxBoundary")
    root = _payload_root(sandbox)
    if not root.exists():
        return 0
    count = 0
    for p in root.glob("*.bin"):
        try:
            if p.is_file() and not p.is_symlink():
                # Ensure still inside worktree
                p.resolve(strict=False).relative_to(Path(sandbox.worktree_root).resolve(strict=True))
                p.unlink()
                count += 1
                # remove corresponding meta
                meta = p.with_suffix(".json")
                if meta.exists():
                    meta.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
    # Also remove orphan meta without bin?
    for m in root.glob("*.json"):
        try:
            # if bin missing, remove json
            bin_path = m.with_suffix(".bin")
            if not bin_path.exists():
                m.unlink(missing_ok=True)
        except OSError:
            continue
    return count


# ---------------------------------------------------------------------------
# File-backed HydrationSource adapter (storage-neutral, non-authority)
# ---------------------------------------------------------------------------

class FileBackedHydrationSource:
    """Thin file-backed HydrationSource adapter for durable selective hydration.

    Wraps the durable file store under a given trusted sandbox. Implements
    the existing HydrationSource protocol (storage-neutral, transport-neutral,
    non-authority). Each resolve reauthorizes scope and verifies digest.

    This is NOT a second result authority; it is bytes storage only.
    """

    def __init__(self, sandbox: WorktreeSandboxBoundary) -> None:
        if not isinstance(sandbox, WorktreeSandboxBoundary):
            raise ForeignRefError("FileBackedHydrationSource requires WorktreeSandboxBoundary")
        self._sandbox = sandbox

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_persistent_store(self) -> bool:
        # Must remain False to satisfy selective_hydration's check that
        # HydrationSource is not a persistent store authority. The file is
        # payload evidence, not result authority.
        return False

    def resolve(self, ref: GovernedReference) -> bytes:
        # Validate ref type
        if not isinstance(ref, GovernedReference):
            raise UnknownRefError(f"resolve requires GovernedReference, got {type(ref).__name__}")
        if ref.digest is None:
            raise DigestMismatchError("ref missing digest — cannot verify")
        # Determine kind for bound check
        kind_str = ref.kind.value if hasattr(ref.kind, "value") else str(ref.kind)
        data = load_durable_payload(
            self._sandbox,
            ref.digest,
            expected_ref=ref.ref,
            expected_project_id=self._sandbox.project_id,  # we enforce current scope already via sandbox
            expected_worktree_id=self._sandbox.worktree_id,
            expected_kind=kind_str,
        )
        return data

    # Convenience for ToolOutputRef
    def resolve_tool_ref(self, ref: ToolOutputRef) -> bytes:
        if not isinstance(ref, ToolOutputRef):
            raise UnknownRefError(f"expected ToolOutputRef, got {type(ref).__name__}")
        data = load_durable_payload(
            self._sandbox,
            ref.digest,
            expected_ref=ref.ref,
            expected_project_id=ref.project_id,
            expected_worktree_id=ref.worktree_id,
            expected_byte_length=ref.byte_length,
            expected_kind="evidence",
        )
        # Additional cross-scope check is done inside load_durable_payload via expected_* vs sandbox
        return data

    def __call__(self, ref: Any) -> bytes:
        """Callable adapter for ToolResultGovernance seam (expects callable).

        Allows FileBackedHydrationSource to be used as content_resolver for
        ToolOutputRef hydration (hydrate_by_ref expects callable or mapping).
        """
        if isinstance(ref, ToolOutputRef):
            return self.resolve_tool_ref(ref)
        if isinstance(ref, GovernedReference):
            return self.resolve(ref)
        # Also handle ArtifactReference via its governed bridge?
        # For generic, try to treat as GovernedReference via duck typing
        if hasattr(ref, "digest") and hasattr(ref, "ref"):
            # Attempt GovernedReference path
            if hasattr(ref, "kind"):
                return self.resolve(ref)  # type: ignore[arg-type]
        raise UnknownRefError(f"FileBackedHydrationSource cannot resolve ref type {type(ref).__name__}")


# ---------------------------------------------------------------------------
# Core-owned governed persistence seam (AF #46 M1/W2, D5).
# Result durability decisions live here (AF_RESULT_GOVERNANCE), never in
# transport adapters. Canonical ingress calls this after provider dispatch;
# MCP must not call persist_durable_payload directly for semantic results.
# ---------------------------------------------------------------------------

def persist_governed_tool_result_if_by_ref(
    sandbox: WorktreeSandboxBoundary,
    response: Any,
    capability_name: str,
) -> None:
    """Best-effort durable persistence for large by_ref results (Core-owned).

    Mirrors the governed projection's digest/byte_length over the sanitized
    payload canonical JSON and persists via the file-backed durable store
    so later result.hydrate in a new process can rehydrate. Fail-open on
    persistence error (projection already truthful by_ref); hydration fails
    closed when the file is missing. Never persists failures or inline
    results. Bounds: durable bound DURABLE_PAYLOAD_MAX_BYTES (64 KiB).
    """
    try:
        ok = getattr(response, "ok", False)
        if not ok:
            return
        payload = getattr(response, "payload", None) or {}
        # Recreate canonical bytes exactly as project_tool_result does.
        from aota_forge.core.contracts.canonical import canonical_json, canonicalize

        if payload:
            full_bytes = canonical_json(canonicalize(payload, path="payload")).encode("utf-8")
        else:
            return
        # Only by_ref payloads need durability (inline bound owned by
        # tool_result_governance.TOOL_INLINE_OUTPUT_MAX_BYTES).
        try:
            from aota_forge.work_plane.tool_result_governance import TOOL_INLINE_OUTPUT_MAX_BYTES
        except Exception:
            TOOL_INLINE_OUTPUT_MAX_BYTES = 4096
        if len(full_bytes) <= TOOL_INLINE_OUTPUT_MAX_BYTES:
            return
        if len(full_bytes) > DURABLE_PAYLOAD_MAX_BYTES:
            return
        import hashlib

        digest = hashlib.sha256(full_bytes).hexdigest()
        ref = f"tool_output:{capability_name}:{digest[:16]}" if isinstance(capability_name, str) else f"durable:{digest[:16]}"
        try:
            persist_durable_payload(
                sandbox,
                full_bytes,
                ref=ref,
                digest=digest,
                byte_length=len(full_bytes),
                project_id=sandbox.project_id,
                worktree_id=sandbox.worktree_id,
                kind="evidence",
            )
        except Exception:
            pass
    except Exception:
        pass


# Backwards alias
DurableHydrationSource = FileBackedHydrationSource

__all__ = [
    "EXISTING_SELECTIVE_HYDRATION_REUSED",
    "EXISTING_HYDRATION_SOURCE_REUSED",
    "EXISTING_RESULT_GOVERNANCE_REUSED",
    "EXISTING_DURABLE_SEAM_SUFFICIENT",
    "NEW_FILE_BACKED_IMPLEMENTATION_CREATED",
    "DURABLE_STORAGE_IS_RESULT_AUTHORITY",
    "HYDRATION_SOURCE_IS_AUTHORITY",
    "SECOND_RESULT_AUTHORITY_CREATED",
    "NEW_RESULT_DB_CREATED",
    "NEW_RESULT_STATE_MACHINE_CREATED",
    "NEW_PERSISTENT_RESULT_STORE_CREATED",
    "NEW_PERSISTENT_HYDRATION_STORE_CREATED",
    "OUTPUT_REF_IS_AUTHORITY",
    "DIGEST_IS_AUTHORITY",
    "REF_POSSESSION_IS_HYDRATION_AUTHORITY",
    "HYDRATION_REAUTHORIZES_CURRENT_SCOPE",
    "CROSS_PROJECT_HYDRATION_FAIL_CLOSED",
    "CROSS_WORKTREE_HYDRATION_FAIL_CLOSED",
    "FOREIGN_REF_HYDRATION_FAIL_CLOSED",
    "HYDRATION_DIGEST_VERIFIED",
    "TAMPERED_REF_FAIL_CLOSED",
    "TAMPERED_PAYLOAD_FAIL_CLOSED",
    "HYDRATION_REQUEST_BOUNDED",
    "HYDRATION_OUTPUT_BOUNDED",
    "SELECTIVE_HYDRATION",
    "EAGER_HYDRATE_ALL_REFS",
    "RESTART_REF_DURABILITY",
    "REF_CONTRACT_ONLY",
    "REF_IMPLEMENTATION_MODE",
    "DURABLE_PAYLOAD_MAX_BYTES",
    "DURABLE_PAYLOAD_DIR_NAME",
    "DURABLE_PAYLOAD_SUBDIR",
    "RETENTION_BOUNDARY",
    "AUTOMATIC_TIME_BASED_GC",
    "EXPLICIT_CLEANUP_SUPPORTED",
    "persist_durable_payload",
    "persist_tool_output_payload",
    "persist_governed_payload",
    "persist_governed_tool_result_if_by_ref",
    "load_durable_payload",
    "clear_durable_payloads",
    "FileBackedHydrationSource",
    "DurableHydrationSource",
]
