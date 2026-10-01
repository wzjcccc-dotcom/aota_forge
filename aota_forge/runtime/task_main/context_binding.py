"""Durable trusted task-main context evidence over the existing coordinator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX
    fcntl = None  # type: ignore[assignment]

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import is_bound_origin_session_ref
from aota_forge.core.project.manifest import PROJECT_ID_RE

CONTEXT_BINDING_SCHEMA_VERSION = 1
DURABLE_CONTEXT_BINDING_IS_AUTHORITY = False
CONTEXT_BINDING_EVIDENCE_IS_AUTHORITY = False
CONTEXT_BINDING_PROJECTION_SCHEMA_VERSION = 1
CONTEXT_BINDING_PROJECTION_FILENAME = "task-main-context-bindings.json"
CONTEXT_BINDING_PROJECTION_IS_AUTHORITY = False
DURABLE_SOURCE_DIGEST_IS_AUTHORITY = False
DURABLE_SOURCE_REVISION_IS_AUTHORITY = False
ROOT_AUTHORITY_AUTO_EXPANSION = False
MAX_CONTEXT_BINDING_PROJECTION_RECORDS = 64

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 512


class ContextBindingError(ValueError):
    """Typed fail-closed durable context binding error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ContextBindingError("CONTEXT_BINDING_CORRUPT", f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > _MAX_TEXT_LENGTH or "\x00" in value:
        raise ContextBindingError("CONTEXT_BINDING_CORRUPT", f"{label} is not bounded text")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ContextBindingError("CONTEXT_BINDING_CORRUPT", f"{label} must be 64 lowercase hex")
    return value


@dataclass(frozen=True)
class DurableTaskMainContextBinding:
    """Immutable Project/Plan/session evidence retained by coordinator state.

    The record is evidence for recovery checks only. Project/Plan authority is
    re-resolved by the trusted runtime and the binding is never promoted to
    authority by being persisted.
    """

    project_id: str
    project_fingerprint: str
    plan_binding: PlanAuthorityBinding
    origin_task_main_session_ref: str
    authorized_roots_digest: str | None = None
    schema_version: int = CONTEXT_BINDING_SCHEMA_VERSION
    binding_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ContextBindingError(
                "PROJECT_CONTEXT_MISMATCH",
                "durable context binding project_id is not canonical",
            )
        object.__setattr__(self, "project_fingerprint", _digest(self.project_fingerprint, "project_fingerprint"))
        if not isinstance(self.plan_binding, PlanAuthorityBinding):
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "durable context binding requires PlanAuthorityBinding",
            )
        if self.plan_binding.source_digest is None:
            raise ContextBindingError(
                "STALE_CONTEXT",
                "durable context binding requires an observed Plan source digest",
            )
        session = _text(self.origin_task_main_session_ref, "origin_task_main_session_ref")
        if not is_bound_origin_session_ref(session):
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "durable context binding requires an exact non-placeholder session",
            )
        object.__setattr__(self, "origin_task_main_session_ref", session)
        if self.authorized_roots_digest is not None:
            object.__setattr__(
                self,
                "authorized_roots_digest",
                _digest(self.authorized_roots_digest, "authorized_roots_digest"),
            )
        if type(self.schema_version) is not int or self.schema_version != CONTEXT_BINDING_SCHEMA_VERSION:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                f"unsupported context binding schema_version: {self.schema_version!r}",
            )
        expected = self.compute_digest()
        if self.binding_digest:
            if self.binding_digest != expected:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CORRUPT",
                    "durable context binding digest mismatch",
                )
        else:
            object.__setattr__(self, "binding_digest", expected)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_fingerprint": self.project_fingerprint,
            "plan_binding": self.plan_binding.to_dict(),
            "origin_task_main_session_ref": self.origin_task_main_session_ref,
            "authorized_roots_digest": self.authorized_roots_digest,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.canonical_payload())
        payload["binding_digest"] = self.binding_digest
        payload["IS_AUTHORITY"] = False
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DurableTaskMainContextBinding":
        if not isinstance(data, Mapping):
            raise ContextBindingError("CONTEXT_BINDING_CORRUPT", "context binding must be a mapping")
        allowed = {
            "schema_version",
            "project_id",
            "project_fingerprint",
            "plan_binding",
            "origin_task_main_session_ref",
            "authorized_roots_digest",
            "binding_digest",
            "IS_AUTHORITY",
        }
        extra = set(data) - allowed
        if extra:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                f"unknown context binding field(s): {sorted(extra)}",
            )
        if "IS_AUTHORITY" in data and data["IS_AUTHORITY"] is not False:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "durable context binding cannot claim authority",
            )
        try:
            plan_binding = PlanAuthorityBinding.from_dict(data["plan_binding"])
            return cls(
                project_id=data["project_id"],
                project_fingerprint=data["project_fingerprint"],
                plan_binding=plan_binding,
                origin_task_main_session_ref=data["origin_task_main_session_ref"],
                authorized_roots_digest=data.get("authorized_roots_digest"),
                schema_version=data.get("schema_version", CONTEXT_BINDING_SCHEMA_VERSION),
                binding_digest=data.get("binding_digest", ""),
            )
        except ContextBindingError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                f"invalid durable context binding: {exc}",
            ) from exc

    def assert_compatible(self, current: "DurableTaskMainContextBinding") -> None:
        if not isinstance(current, DurableTaskMainContextBinding):
            raise ContextBindingError("CONTEXT_BINDING_CORRUPT", "current context binding is invalid")
        if self.project_id != current.project_id or self.project_fingerprint != current.project_fingerprint:
            raise ContextBindingError(
                "PROJECT_CONTEXT_MISMATCH",
                "durable context binding project evidence changed",
            )
        if self.origin_task_main_session_ref != current.origin_task_main_session_ref:
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "exact task-main session differs from durable context binding",
            )
        if (
            self.plan_binding.plan_id != current.plan_binding.plan_id
            or self.plan_binding.source_kind != current.plan_binding.source_kind
            or self.plan_binding.authority_ref != current.plan_binding.authority_ref
        ):
            raise ContextBindingError(
                "PLAN_CONTEXT_MISMATCH",
                "durable Plan identity differs from the current trusted binding",
            )
        if (
            (self.plan_binding.source_revision is None)
            != (current.plan_binding.source_revision is None)
            or (
                self.plan_binding.source_revision is not None
                and str(self.plan_binding.source_revision)
                != str(current.plan_binding.source_revision)
            )
            or self.plan_binding.source_digest != current.plan_binding.source_digest
        ):
            raise ContextBindingError(
                "STALE_CONTEXT",
                "durable Plan source evidence differs from the current trusted binding",
            )
        if self.authorized_roots_digest != current.authorized_roots_digest:
            raise ContextBindingError(
                "AUTHORITY_DENIED",
                "durable authorized-root evidence differs from the current trusted binding",
            )

    def assert_resume_identity_compatible(
        self, current: "DurableTaskMainContextBinding"
    ) -> None:
        """Compare only immutable identity for a trusted resume refresh.

        Source revision/digest are observations from an earlier authority read,
        not leases that prevent a later canonical Plan lifecycle advancement.
        Authorized roots remain a separate exact compatibility check.
        """
        if not isinstance(current, DurableTaskMainContextBinding):
            raise ContextBindingError("CONTEXT_BINDING_CORRUPT", "current context binding is invalid")
        if self.project_id != current.project_id or self.project_fingerprint != current.project_fingerprint:
            raise ContextBindingError(
                "PROJECT_CONTEXT_MISMATCH",
                "durable context binding project identity differs from the current trusted identity",
            )
        if self.origin_task_main_session_ref != current.origin_task_main_session_ref:
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "exact task-main session differs from durable context binding",
            )
        if (
            self.plan_binding.plan_id != current.plan_binding.plan_id
            or self.plan_binding.source_kind != current.plan_binding.source_kind
            or self.plan_binding.authority_ref != current.plan_binding.authority_ref
        ):
            raise ContextBindingError(
                "PLAN_CONTEXT_MISMATCH",
                "durable Plan identity differs from the current trusted binding",
            )

    def assert_live_plan(
        self,
        *,
        project_id: str,
        plan_authority: str,
        plan_digest: str,
        plan_source_revision: str | None,
        origin_task_main_session_ref: str,
    ) -> None:
        if self.project_id != project_id:
            raise ContextBindingError(
                "PROJECT_CONTEXT_MISMATCH",
                "live Project differs from durable context binding",
            )
        if self.origin_task_main_session_ref != origin_task_main_session_ref:
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "live exact session differs from durable context binding",
            )
        if self.plan_binding.authority_ref != plan_authority:
            raise ContextBindingError(
                "PLAN_CONTEXT_MISMATCH",
                "live Plan authority differs from durable context binding",
            )
        if self.plan_binding.source_digest != plan_digest:
            raise ContextBindingError(
                "STALE_CONTEXT",
                "live Plan digest differs from durable context binding",
            )
        if (self.plan_binding.source_revision is None) != (plan_source_revision is None) or (
            self.plan_binding.source_revision is not None
            and str(self.plan_binding.source_revision) != str(plan_source_revision)
        ):
            raise ContextBindingError(
                "STALE_CONTEXT",
                "live Plan source revision differs from durable context binding",
            )


class FileBackedTaskMainContextBindingProjection:
    """Small runtime-owned carrier for interactive context evidence.

    The existing coordinator store cannot represent a binding without also
    fabricating a milestone coordinator. This projection therefore carries
    only typed ``DurableTaskMainContextBinding`` evidence under the trusted
    Project's ``.aota`` directory. It is not an authority store, workflow
    state machine, or session database.
    """

    def __init__(self, path: str | Path) -> None:
        candidate = Path(path)
        if candidate.name != CONTEXT_BINDING_PROJECTION_FILENAME:
            raise ContextBindingError(
                "CONTEXT_BINDING_PROJECTION_INVALID",
                "context binding projection has an unexpected filename",
            )
        if candidate.parent.name != ".aota":
            raise ContextBindingError(
                "CONTEXT_BINDING_PROJECTION_INVALID",
                "context binding projection must be directly under .aota",
            )
        try:
            if candidate.parent.is_symlink():
                raise ContextBindingError(
                    "CONTEXT_BINDING_PROJECTION_INVALID",
                    "context binding projection parent must not be a symlink",
                )
            self._path = candidate.resolve(strict=False)
        except ContextBindingError:
            raise
        except OSError as exc:
            raise ContextBindingError(
                "CONTEXT_BINDING_PROJECTION_INVALID",
                "context binding projection path is inaccessible",
            ) from exc
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _path_lock(self):
        """Serialize projection read-modify-write across adapter instances."""
        lock_path = self._path.with_name(f".{self._path.name}.lock")
        with self._lock:
            if fcntl is None:
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "inter-process context binding locking is unavailable",
                )
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                except OSError as exc:
                    raise ContextBindingError(
                        "CONTEXT_BINDING_PERSISTENCE_FAILED",
                        "inter-process context binding lock could not be acquired",
                    ) from exc
                yield
            except OSError as exc:
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "inter-process context binding lock could not be opened",
                ) from exc
            finally:
                if descriptor is not None:
                    if fcntl is not None:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        except OSError:
                            pass
                    os.close(descriptor)

    def _read(self) -> list[DurableTaskMainContextBinding]:
        if not self._path.exists():
            return []
        try:
            if self._path.is_symlink() or not self._path.is_file():
                raise ContextBindingError(
                    "CONTEXT_BINDING_CORRUPT",
                    "context binding projection is not a regular file",
                )
            if self._path.stat().st_size > 256 * 1024:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CORRUPT",
                    "context binding projection exceeds its bounded size",
                )
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except ContextBindingError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "context binding projection could not be read",
            ) from exc
        if not isinstance(raw, Mapping):
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "context binding projection root must be an object",
            )
        allowed = {"schema_version", "records", "IS_AUTHORITY"}
        extra = set(raw) - allowed
        if extra:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                f"unknown context binding projection field(s): {sorted(extra)}",
            )
        if raw.get("schema_version") != CONTEXT_BINDING_PROJECTION_SCHEMA_VERSION:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "unsupported context binding projection schema_version",
            )
        if raw.get("IS_AUTHORITY") is not False:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "context binding projection cannot claim authority",
            )
        records = raw.get("records")
        if not isinstance(records, list) or len(records) > MAX_CONTEXT_BINDING_PROJECTION_RECORDS:
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "context binding projection records are invalid or oversized",
            )
        parsed: list[DurableTaskMainContextBinding] = []
        for record in records:
            try:
                parsed.append(DurableTaskMainContextBinding.from_dict(record))
            except ContextBindingError:
                raise
            except Exception as exc:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CORRUPT",
                    "context binding projection record is invalid",
                ) from exc
        return parsed

    def _persist(self, records: list[DurableTaskMainContextBinding]) -> None:
        if not self._path.parent.is_dir():
            raise ContextBindingError(
                "CONTEXT_BINDING_PERSISTENCE_FAILED",
                "trusted Project .aota directory is unavailable",
            )
        payload = {
            "schema_version": CONTEXT_BINDING_PROJECTION_SCHEMA_VERSION,
            "records": [record.to_dict() for record in records],
            "IS_AUTHORITY": False,
        }
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(self._path.parent),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, indent=2, ensure_ascii=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            temporary = None
            os.chmod(self._path, 0o600)
            try:
                directory_fd = os.open(self._path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except ContextBindingError:
            raise
        except OSError as exc:
            raise ContextBindingError(
                "CONTEXT_BINDING_PERSISTENCE_FAILED",
                "context binding projection atomic write failed",
            ) from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def get_for_session(self, origin_task_main_session_ref: str) -> DurableTaskMainContextBinding | None:
        session = _text(origin_task_main_session_ref, "origin_task_main_session_ref")
        if not is_bound_origin_session_ref(session):
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "context binding lookup requires an exact non-placeholder session",
            )
        with self._path_lock():
            matches = [
                record
                for record in self._read()
                if record.origin_task_main_session_ref == session
            ]
            if len(matches) > 1:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CONFLICT",
                    "multiple durable context bindings match the exact session",
                )
            return matches[0] if matches else None

    def persist(self, binding: DurableTaskMainContextBinding) -> DurableTaskMainContextBinding:
        if not isinstance(binding, DurableTaskMainContextBinding):
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "only a typed DurableTaskMainContextBinding may be persisted",
            )
        with self._path_lock():
            records = self._read()
            same_session = [
                record
                for record in records
                if record.origin_task_main_session_ref == binding.origin_task_main_session_ref
            ]
            if len(same_session) > 1:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CONFLICT",
                    "multiple durable context bindings already match the exact session",
                )
            if same_session:
                if same_session[0].binding_digest != binding.binding_digest:
                    raise ContextBindingError(
                        "CONTEXT_BINDING_CONFLICT",
                        "a different durable context binding already owns the exact session",
                    )
                return same_session[0]
            if len(records) >= MAX_CONTEXT_BINDING_PROJECTION_RECORDS:
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "context binding projection record limit reached",
                )
            self._persist([*records, binding])
            readback = next(
                (
                    record
                    for record in self._read()
                    if record.origin_task_main_session_ref == binding.origin_task_main_session_ref
                ),
                None,
            )
            if readback is None or readback.binding_digest != binding.binding_digest:
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "durable context binding readback did not match the committed evidence",
                )
            return readback

    def remove_for_session(
        self,
        *,
        origin_task_main_session_ref: str,
        expected_binding_digest: str,
    ) -> None:
        """Rollback one newly-created session record under the same projection lock."""
        session = _text(origin_task_main_session_ref, "origin_task_main_session_ref")
        if not is_bound_origin_session_ref(session):
            raise ContextBindingError(
                "SESSION_RECOVERY_REQUIRED",
                "context binding rollback requires an exact non-placeholder session",
            )
        expected_digest = _digest(expected_binding_digest, "expected_binding_digest")
        with self._path_lock():
            records = self._read()
            matches = [
                record
                for record in records
                if record.origin_task_main_session_ref == session
            ]
            if not matches:
                return
            if len(matches) != 1 or matches[0].binding_digest != expected_digest:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CONFLICT",
                    "durable context binding changed before rollback",
                )
            self._persist(
                [
                    record
                    for record in records
                    if record.origin_task_main_session_ref != session
                ]
            )
            if any(
                record.origin_task_main_session_ref == session
                for record in self._read()
            ):
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "durable context binding rollback readback still contains the record",
                )

    def refresh_for_session(
        self,
        *,
        expected_binding_digest: str,
        replacement: DurableTaskMainContextBinding,
    ) -> DurableTaskMainContextBinding:
        """CAS-replace historical evidence for one exact session.

        The current record must still be the record observed by the caller.
        Only source observations may advance; immutable identity and authorized
        roots must remain unchanged. The replacement is committed atomically
        and verified by a fresh readback.
        """
        expected_digest = _digest(expected_binding_digest, "expected_binding_digest")
        if not isinstance(replacement, DurableTaskMainContextBinding):
            raise ContextBindingError(
                "CONTEXT_BINDING_CORRUPT",
                "only a typed DurableTaskMainContextBinding may replace durable evidence",
            )
        with self._path_lock():
            records = self._read()
            matches = [
                record
                for record in records
                if record.origin_task_main_session_ref
                == replacement.origin_task_main_session_ref
            ]
            if len(matches) != 1:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CONFLICT",
                    "exact task-main session must have exactly one durable context binding",
                )
            current = matches[0]
            if current.binding_digest != expected_digest:
                raise ContextBindingError(
                    "CONTEXT_BINDING_CONFLICT",
                    "durable context binding changed before the CAS refresh",
                )
            current.assert_resume_identity_compatible(replacement)
            if current.authorized_roots_digest != replacement.authorized_roots_digest:
                raise ContextBindingError(
                    "AUTHORITY_DENIED",
                    "authorized-root evidence cannot change during a resume refresh",
                )
            replaced = [
                replacement if record is current else record
                for record in records
            ]
            self._persist(replaced)
            readback = next(
                (
                    record
                    for record in self._read()
                    if record.origin_task_main_session_ref == replacement.origin_task_main_session_ref
                ),
                None,
            )
            if readback is None or readback.binding_digest != replacement.binding_digest:
                raise ContextBindingError(
                    "CONTEXT_BINDING_PERSISTENCE_FAILED",
                    "durable context binding refresh readback did not match the replacement",
                )
            return readback


def context_binding_projection_path(project_root: str | Path) -> Path:
    """Return the fixed project-scoped projection path."""

    root = Path(project_root)
    try:
        if root.is_symlink() or not root.is_dir():
            raise ContextBindingError(
                "CONTEXT_BINDING_PROJECTION_INVALID",
                "trusted Project root is not a regular directory",
            )
        return root.resolve() / ".aota" / CONTEXT_BINDING_PROJECTION_FILENAME
    except ContextBindingError:
        raise
    except OSError as exc:
        raise ContextBindingError(
            "CONTEXT_BINDING_PROJECTION_INVALID",
            "trusted Project root is inaccessible",
        ) from exc


def locate_durable_context_binding(
    *,
    origin_task_main_session_ref: str,
    registry_path: str | Path | None = None,
    worktree_root: str | Path | None = None,
) -> tuple[DurableTaskMainContextBinding, Path] | None:
    """Locate one evidence record by exact session across trusted Projects."""

    session = _text(origin_task_main_session_ref, "origin_task_main_session_ref")
    if not is_bound_origin_session_ref(session):
        raise ContextBindingError(
            "SESSION_RECOVERY_REQUIRED",
            "durable context lookup requires an exact non-placeholder session",
        )
    roots: set[Path] = set()
    if worktree_root is not None and str(worktree_root).strip():
        candidate = Path(str(worktree_root)).resolve()
        if candidate.is_dir():
            roots.add(candidate)
    if registry_path is not None and str(registry_path).strip():
        try:
            from aota_forge.core.project.discovery import scan_projects
            from aota_forge.core.project.resolver import load_workspace_registry, resolve_workspace

            registry = Path(registry_path)
            for workspace_id in sorted(load_workspace_registry(registry)):
                workspace_root = resolve_workspace(workspace_id, registry)
                for record in scan_projects(workspace_root, limit=None)["projects"]:
                    project_root = workspace_root / record["root"] if record["root"] != "." else workspace_root
                    roots.add(project_root.resolve())
        except ContextBindingError:
            raise
        except Exception as exc:
            raise ContextBindingError(
                "CONTEXT_BINDING_LOOKUP_FAILED",
                "trusted Project registry could not be scanned for durable context",
            ) from exc

    matches: list[tuple[DurableTaskMainContextBinding, Path]] = []
    for root in sorted(roots, key=str):
        projection_path = context_binding_projection_path(root)
        if not projection_path.exists():
            continue
        projection = FileBackedTaskMainContextBindingProjection(projection_path)
        binding = projection.get_for_session(session)
        if binding is not None:
            matches.append((binding, projection_path))
    if len(matches) > 1:
        raise ContextBindingError(
            "CONTEXT_BINDING_CONFLICT",
            "multiple Project-scoped durable bindings match the exact session",
        )
    return matches[0] if matches else None


__all__ = [
    "CONTEXT_BINDING_EVIDENCE_IS_AUTHORITY",
    "CONTEXT_BINDING_PROJECTION_FILENAME",
    "CONTEXT_BINDING_PROJECTION_IS_AUTHORITY",
    "CONTEXT_BINDING_PROJECTION_SCHEMA_VERSION",
    "CONTEXT_BINDING_SCHEMA_VERSION",
    "DURABLE_CONTEXT_BINDING_IS_AUTHORITY",
    "DURABLE_SOURCE_DIGEST_IS_AUTHORITY",
    "DURABLE_SOURCE_REVISION_IS_AUTHORITY",
    "MAX_CONTEXT_BINDING_PROJECTION_RECORDS",
    "ROOT_AUTHORITY_AUTO_EXPANSION",
    "ContextBindingError",
    "DurableTaskMainContextBinding",
    "FileBackedTaskMainContextBindingProjection",
    "context_binding_projection_path",
    "locate_durable_context_binding",
]
