"""Canonical task-main context contract for M1/W2.

This module owns only the inspect/bind contract and its trusted identity
boundary. Project and Plan resolution remain trusted composition concerns;
this operation consumes their typed binding and authorized-root result without
creating another resolver or state store.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.providers.tool import ToolResponse
from aota_forge.core.project.manifest import PROJECT_ID_RE

CONTEXT_OPERATION = "task_main.context"
CONTEXT_MODE_INSPECT = "inspect"
CONTEXT_MODE_BIND = "bind"
CONTEXT_MODE_SWITCH = "switch"
CONTEXT_MODES: tuple[str, ...] = (
    CONTEXT_MODE_INSPECT,
    CONTEXT_MODE_BIND,
    CONTEXT_MODE_SWITCH,
)

# Contract/security markers. They are descriptive and do not create a second
# authority or session store.
M1_CONTEXT_OPERATION_CONTRACT = True
SESSION_BINDING_IS_AUTHORITY = False
SESSION_ID_IS_AUTHORITY = False
PLAN_ID_IS_AUTHORITY = False
AUTHORITY_REF_IS_AUTHORITY = False
LOCATOR_IS_AUTHORITY = False
MODEL_MINTS_AUTHORITY = False
NEW_CONTEXT_STORE_CREATED = False
LIVE_CONTEXT_SWITCH_IMPLEMENTED = False
M1_DURABLE_BINDING_CREATED = False

_MAX_LOCATOR_LENGTH = 512
_SAFE_LOCATOR = re.compile(r"^[^\x00\r\n\t ]{1,512}$")


class TaskMainContextError(ValueError):
    """Typed fail-closed context contract error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _bounded_locator(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TaskMainContextError("INVALID_INPUT", f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > _MAX_LOCATOR_LENGTH or not _SAFE_LOCATOR.fullmatch(value):
        raise TaskMainContextError("INVALID_INPUT", f"{label} must be a bounded locator")
    return value


@dataclass(frozen=True)
class ContextLocator:
    """Model semantic locator; it is never an authority source."""

    project_ref: str | None = None
    plan_ref: str | None = None

    def __post_init__(self) -> None:
        for label in ("project_ref", "plan_ref"):
            value = getattr(self, label)
            if value is not None:
                object.__setattr__(self, label, _bounded_locator(value, label))

    @property
    def has_target(self) -> bool:
        return self.project_ref is not None or self.plan_ref is not None

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("project_ref", self.project_ref),
                ("plan_ref", self.plan_ref),
            )
            if value is not None
        }


@dataclass(frozen=True)
class TrustedProjectPlanIdentity:
    """Runtime-carried Project/Plan identity for one context operation.

    ``plan_binding`` identifies the currently bound source but does not become
    authority merely because it is present. The runtime that created it remains
    the authority owner.
    """

    project_id: str
    plan_binding: PlanAuthorityBinding

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise TaskMainContextError("PROJECT_CONTEXT_MISMATCH", "trusted project identity is invalid")
        if not isinstance(self.plan_binding, PlanAuthorityBinding):
            raise TaskMainContextError("AUTHORITY_DENIED", "trusted Plan authority binding is required")

    def authority_locator_values(self, legacy_plan_binding: object | None = None) -> frozenset[str]:
        values = {
            self.plan_binding.plan_id,
            self.plan_binding.authority_ref,
        }
        legacy_ref = getattr(legacy_plan_binding, "plan_ref", None)
        if isinstance(legacy_ref, str) and legacy_ref.strip():
            values.add(legacy_ref.strip())
        return frozenset(values)

    def to_dict(self) -> dict[str, Any]:
        binding = self.plan_binding
        return {
            "project_id": self.project_id,
            "plan_id": binding.plan_id,
            "authority_binding": {
                "source_kind": binding.source_kind,
                "authority_ref": binding.authority_ref,
                "source_revision": binding.source_revision,
                "source_digest": binding.source_digest,
            },
            "identity_is_authority": False,
            "locator_is_authority": False,
        }


def trusted_identity_from_binding(binding: object) -> TrustedProjectPlanIdentity | None:
    """Extract only an already-typed trusted Project/Plan binding.

    Model-visible values and plain mappings are never promoted to trusted
    identity here.
    """

    project_id = getattr(binding, "project_id", None)
    plan_binding = getattr(binding, "plan_authority_binding", None)
    if not isinstance(project_id, str) or not isinstance(plan_binding, PlanAuthorityBinding):
        return None
    try:
        return TrustedProjectPlanIdentity(project_id=project_id, plan_binding=plan_binding)
    except TaskMainContextError:
        return None


def _locator_from_arguments(arguments: Mapping[str, Any]) -> ContextLocator:
    return ContextLocator(
        project_ref=arguments.get("project_ref"),
        plan_ref=arguments.get("plan_ref"),
    )


def _match_locator(
    locator: ContextLocator,
    identity: TrustedProjectPlanIdentity,
    *,
    legacy_plan_binding: object | None = None,
) -> None:
    if locator.project_ref is not None and locator.project_ref != identity.project_id:
        raise TaskMainContextError(
            "PROJECT_CONTEXT_MISMATCH",
            "context locator does not match the trusted project identity",
        )
    if locator.plan_ref is not None and locator.plan_ref not in identity.authority_locator_values(legacy_plan_binding):
        raise TaskMainContextError(
            "PLAN_CONTEXT_MISMATCH",
            "context locator does not match the trusted Plan authority binding",
        )


def _snapshot(
    mode: str,
    identity: TrustedProjectPlanIdentity | None,
    *,
    binding_status: str,
    authorized_roots: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "state": binding_status,
        "session_binding_is_authority": False,
        "identity_is_authority": False,
        "locator_is_authority": False,
    }
    if identity is not None:
        payload["context"] = identity.to_dict()
        if authorized_roots is not None:
            payload["context"]["authorized_roots"] = authorized_roots.public_roots()
    else:
        payload["context"] = None
    return payload


def _trusted_authorized_roots(binding: object) -> Any | None:
    """Read and revalidate the runtime-derived root set, if one is present."""
    sandbox = getattr(binding, "sandbox", None)
    if sandbox is None:
        return None

    from aota_forge.work_plane.authorized_roots import (
        AuthorizedRootSet,
        authorized_roots_single_root,
        validate_root_set_against_sandbox,
    )

    try:
        roots = getattr(binding, "effective_authorized_roots", None)
        if callable(roots):
            roots = roots()
        if roots is None:
            roots = authorized_roots_single_root(sandbox)
    except TaskMainContextError:
        raise
    except Exception as exc:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "trusted authorized root set is invalid",
        ) from exc
    if not isinstance(roots, AuthorizedRootSet):
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "trusted authorized root set is invalid",
        )
    try:
        validate_root_set_against_sandbox(roots, sandbox)
    except Exception as exc:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "trusted authorized root set is not sandbox-derived",
        ) from exc
    return roots


def dispatch_task_main_context(
    arguments: Mapping[str, Any] | None,
    binding: object,
) -> ToolResponse:
    """Execute the bounded M1/W1 context contract.

    ``inspect`` is read-only. ``bind`` validates a model locator against the
    trusted runtime binding but does not resolve or persist anything. ``switch``
    is intentionally unavailable until the later switch work item.
    """

    if not isinstance(arguments, Mapping):
        return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "arguments must be an object"})
    try:
        mode = arguments.get("mode")
        if not isinstance(mode, str) or mode not in CONTEXT_MODES:
            raise TaskMainContextError("INVALID_MODE", "mode must be inspect, bind, or switch")
        locator = _locator_from_arguments(arguments)
        identity = trusted_identity_from_binding(binding)
        legacy_plan_binding = getattr(binding, "plan_binding", None)
        authorized_roots = _trusted_authorized_roots(binding)

        if mode == CONTEXT_MODE_INSPECT:
            if identity is None:
                return ToolResponse.success(_snapshot(mode, None, binding_status="unbound"))
            status = "bound" if identity.plan_binding.source_digest is not None else "bound_unverified"
            return ToolResponse.success(
                _snapshot(
                    mode,
                    identity,
                    binding_status=status,
                    authorized_roots=authorized_roots,
                )
            )

        if identity is None:
            raise TaskMainContextError(
                "CONTEXT_BINDING_REQUIRED",
                "bind requires a trusted Project/Plan runtime binding",
            )
        if not locator.has_target:
            raise TaskMainContextError(
                "CONTEXT_LOCATOR_REQUIRED",
                f"{mode} requires project_ref or plan_ref",
            )
        _match_locator(locator, identity, legacy_plan_binding=legacy_plan_binding)

        if mode == CONTEXT_MODE_BIND:
            if identity.plan_binding.source_digest is None:
                raise TaskMainContextError(
                    "STALE_CONTEXT",
                    "trusted Plan binding has no observed source digest",
                )
            return ToolResponse.success(
                _snapshot(
                    mode,
                    identity,
                    binding_status="bound",
                    authorized_roots=authorized_roots,
                )
            )

        # M1/W1 defines the switch contract but does not implement live
        # switching or mutate the current trusted runtime binding.
        return ToolResponse.failure(
            {
                "code": "CONTEXT_SWITCH_UNAVAILABLE",
                "message": "live context switching is not implemented in M1/W1",
            }
        )
    except TaskMainContextError as exc:
        return ToolResponse.failure({"code": exc.code, "message": str(exc)})
    except Exception:
        return ToolResponse.failure(
            {
                "code": "INTERNAL_MECHANICAL_ERROR",
                "message": "task-main context contract failed closed",
            }
        )


__all__ = [
    "AUTHORITY_REF_IS_AUTHORITY",
    "CONTEXT_MODE_BIND",
    "CONTEXT_MODE_INSPECT",
    "CONTEXT_MODE_SWITCH",
    "CONTEXT_MODES",
    "CONTEXT_OPERATION",
    "ContextLocator",
    "LIVE_CONTEXT_SWITCH_IMPLEMENTED",
    "LOCATOR_IS_AUTHORITY",
    "M1_CONTEXT_OPERATION_CONTRACT",
    "M1_DURABLE_BINDING_CREATED",
    "MODEL_MINTS_AUTHORITY",
    "NEW_CONTEXT_STORE_CREATED",
    "PLAN_ID_IS_AUTHORITY",
    "SESSION_BINDING_IS_AUTHORITY",
    "SESSION_ID_IS_AUTHORITY",
    "TaskMainContextError",
    "TrustedProjectPlanIdentity",
    "dispatch_task_main_context",
    "trusted_identity_from_binding",
]
