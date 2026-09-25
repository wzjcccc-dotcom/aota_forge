"""Durable trusted task-main context evidence over the existing coordinator."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import is_bound_origin_session_ref
from aota_forge.core.project.manifest import PROJECT_ID_RE

CONTEXT_BINDING_SCHEMA_VERSION = 1
DURABLE_CONTEXT_BINDING_IS_AUTHORITY = False
CONTEXT_BINDING_EVIDENCE_IS_AUTHORITY = False

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


__all__ = [
    "CONTEXT_BINDING_EVIDENCE_IS_AUTHORITY",
    "CONTEXT_BINDING_SCHEMA_VERSION",
    "DURABLE_CONTEXT_BINDING_IS_AUTHORITY",
    "ContextBindingError",
    "DurableTaskMainContextBinding",
]
