"""Canonical task-main context contract for M1/W2.

This module owns only the inspect/bind contract and its trusted identity
boundary. Project and Plan resolution remain trusted composition concerns;
this operation consumes their typed binding and authorized-root result without
creating another resolver or state store.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import is_bound_origin_session_ref
from aota_forge.core.providers.tool import ToolResponse
from aota_forge.core.project.manifest import PROJECT_ID_RE

CONTEXT_OPERATION = "task_main.context"
CONTEXT_MODE_INSPECT = "inspect"
CONTEXT_MODE_BIND = "bind"
CONTEXT_MODE_SWITCH = "switch"
CONTEXT_STATE_PROJECT_BOUND = "project_bound"
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
# The public switch path is implemented when the trusted W4 runtime carrier is
# composed; legacy bindings without that carrier remain fail-closed.
LIVE_CONTEXT_SWITCH_IMPLEMENTED = True
M1_DURABLE_BINDING_CREATED = False

_MAX_LOCATOR_LENGTH = 512
_SAFE_LOCATOR = re.compile(r"^[^\x00\r\n\t ]{1,512}$")
_NO_GOVERNANCE_CONTEXT_OVERRIDE = object()


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
    plan_binding: PlanAuthorityBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise TaskMainContextError("PROJECT_CONTEXT_MISMATCH", "trusted project identity is invalid")
        if self.plan_binding is not None and not isinstance(self.plan_binding, PlanAuthorityBinding):
            raise TaskMainContextError("AUTHORITY_DENIED", "trusted Plan authority binding is required")

    def authority_locator_values(self, legacy_plan_binding: object | None = None) -> frozenset[str]:
        if self.plan_binding is None:
            return frozenset()
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
        payload: dict[str, Any] = {
            "project_id": self.project_id,
            "plan_id": binding.plan_id if binding is not None else None,
            "authority_binding": (
                {
                    "source_kind": binding.source_kind,
                    "authority_ref": binding.authority_ref,
                    "source_revision": binding.source_revision,
                    "source_digest": binding.source_digest,
                }
                if binding is not None
                else None
            ),
            "identity_is_authority": False,
            "locator_is_authority": False,
        }
        return payload


def trusted_identity_from_binding(binding: object) -> TrustedProjectPlanIdentity | None:
    """Extract only an already-typed trusted Project/Plan binding.

    Model-visible values and plain mappings are never promoted to trusted
    identity here.
    """

    project_id = getattr(binding, "project_id", None)
    plan_binding = getattr(binding, "plan_authority_binding", None)
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        return None
    if plan_binding is not None and not isinstance(plan_binding, PlanAuthorityBinding):
        return None
    try:
        return TrustedProjectPlanIdentity(project_id=project_id, plan_binding=plan_binding)
    except TaskMainContextError:
        return None


def normalize_context_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize canonical selectors and legacy aliases at one boundary."""
    if not isinstance(arguments, Mapping):
        raise TaskMainContextError("INPUT_TYPE_INVALID", "arguments must be an object")
    normalized = dict(arguments)
    for canonical, legacy in (
        ("project_selector", "project_ref"),
        ("plan_selector", "plan_ref"),
    ):
        has_canonical = canonical in normalized
        has_legacy = legacy in normalized
        if has_canonical and has_legacy:
            canonical_value = normalized[canonical]
            legacy_value = normalized[legacy]
            if (
                canonical_value is not None
                and legacy_value is not None
                and canonical_value != legacy_value
            ):
                raise TaskMainContextError(
                    "INVALID_INPUT",
                    f"{canonical} conflicts with legacy {legacy}",
                )
        if has_canonical:
            normalized[legacy] = normalized[canonical]
            normalized.pop(canonical, None)
    return normalized


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
    governance_context: Mapping[str, Any] | None = None,
    origin_session_ref: str | None = None,
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
        if origin_session_ref is not None:
            payload["origin_session_ref"] = origin_session_ref
        if authorized_roots is not None:
            payload["context"]["authorized_roots"] = authorized_roots.public_roots()
        if governance_context is not None:
            payload["governance_context"] = dict(governance_context)
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


def _trusted_governance_context(
    binding: object,
    identity: TrustedProjectPlanIdentity,
    *,
    carrier_override: object = _NO_GOVERNANCE_CONTEXT_OVERRIDE,
) -> dict[str, Any] | None:
    """Return a bounded projection only when it agrees with trusted identity."""
    carrier = (
        getattr(binding, "governance_context", None)
        if carrier_override is _NO_GOVERNANCE_CONTEXT_OVERRIDE
        else carrier_override
    )
    if carrier is None:
        return None
    if not isinstance(carrier, Mapping):
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "trusted governance context must be a mapping",
        )
    if carrier.get("IS_AUTHORITY") is not False or carrier.get("GOVERNANCE_CONTEXT_IS_AUTHORITY") is not False:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context must declare non-authority markers",
        )
    route = carrier.get("CONTEXT_ROUTE")
    if not isinstance(route, Mapping) or route.get("is_authority") is not False:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context route must be a derived projection",
        )
    if route.get("CONTEXT_ROUTE_IS_AUTHORITY") is not False:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context route must declare non-authority",
        )
    if route.get("project_id") != identity.project_id:
        raise TaskMainContextError(
            "PROJECT_CONTEXT_MISMATCH",
            "governance context route does not match the trusted project identity",
        )
    entries = route.get("entries")
    if not isinstance(entries, list):
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context route entries must be a list",
        )
    plan_binding = identity.plan_binding
    if plan_binding is None:
        if any(
            route.get(field) is not None
            for field in (
                "plan_id",
                "authority_source_kind",
                "authority_ref",
                "authority_revision",
                "authority_digest",
            )
        ):
            raise TaskMainContextError(
                "PLAN_CONTEXT_MISMATCH",
                "project-only governance context must not carry Plan authority",
            )
        if any(
            isinstance(route_entry, Mapping) and route_entry.get("kind") == "plan_authority"
            for route_entry in entries
        ):
            raise TaskMainContextError(
                "PLAN_CONTEXT_MISMATCH",
                "project-only context route must not carry Plan authority",
            )
        cards = carrier.get("CARDS")
        if cards is not None:
            if not isinstance(cards, Mapping):
                raise TaskMainContextError(
                    "AUTHORITY_DENIED",
                    "governance context CARDS must be a mapping or None",
                )
            if cards.get("plans") not in ([], None) or cards.get("milestones") not in ([], None):
                raise TaskMainContextError(
                    "PLAN_CONTEXT_MISMATCH",
                    "project-only governance context must not carry Plan or Milestone cards",
                )
            project_card = cards.get("project")
            if isinstance(project_card, Mapping) and project_card.get("project_id") != identity.project_id:
                raise TaskMainContextError(
                    "PROJECT_CONTEXT_MISMATCH",
                    "governance project card does not match the trusted project identity",
                )
        try:
            normalized = json.loads(canonical_json(dict(carrier)))
        except (TypeError, ValueError) as exc:
            raise TaskMainContextError(
                "AUTHORITY_DENIED",
                "governance context must be bounded plain data",
            ) from exc
        from aota_forge.work_plane.role_bootstrap import MAX_GOVERNANCE_CONTEXT_CARRIER_BYTES

        if len(canonical_json(normalized).encode("utf-8")) > MAX_GOVERNANCE_CONTEXT_CARRIER_BYTES:
            raise TaskMainContextError(
                "AUTHORITY_DENIED",
                "governance context exceeds its bounded carrier size",
            )
        return normalized

    if route.get("plan_id") != plan_binding.plan_id:
        raise TaskMainContextError(
            "PLAN_CONTEXT_MISMATCH",
            "governance context route does not match the trusted Plan identity",
        )
    if route.get("authority_source_kind") != plan_binding.source_kind:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context route source does not match the trusted Plan binding",
        )

    authority_entries = [
        entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("kind") == "plan_authority"
    ]
    if len(authority_entries) != 1:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context route must contain exactly one Plan authority entry",
        )
    authority_entry = authority_entries[0]
    if authority_entry.get("ref") != plan_binding.authority_ref:
        raise TaskMainContextError(
            "PLAN_CONTEXT_MISMATCH",
            "governance context authority ref does not match the trusted Plan binding",
        )
    if authority_entry.get("source_kind") != plan_binding.source_kind:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context authority source does not match the trusted Plan binding",
        )
    expected_revision = (
        str(plan_binding.source_revision)
        if plan_binding.source_revision is not None
        else None
    )
    if authority_entry.get("revision") != expected_revision:
        raise TaskMainContextError(
            "STALE_CONTEXT",
            "governance context authority revision is not bound to the trusted source",
        )
    if authority_entry.get("digest") != plan_binding.source_digest:
        raise TaskMainContextError(
            "STALE_CONTEXT",
            "governance context authority digest is not bound to the trusted source",
        )

    if type(carrier.get("CARDS_PRESENT")) is not bool:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context CARDS_PRESENT must be a bool",
        )
    cards = carrier.get("CARDS")
    if cards is not None:
        if not isinstance(cards, Mapping):
            raise TaskMainContextError(
                "AUTHORITY_DENIED",
                "governance context CARDS must be a mapping or None",
            )
        project_card = cards.get("project")
        if isinstance(project_card, Mapping) and project_card.get("project_id") != identity.project_id:
            raise TaskMainContextError(
                "PROJECT_CONTEXT_MISMATCH",
                "governance project card does not match the trusted project identity",
            )
        plan_cards = cards.get("plans")
        if isinstance(plan_cards, list) and plan_cards:
            matching_plan_cards = [
                card
                for card in plan_cards
                if isinstance(card, Mapping) and card.get("plan_id") == plan_binding.plan_id
            ]
            if len(matching_plan_cards) != 1:
                raise TaskMainContextError(
                    "PLAN_CONTEXT_MISMATCH",
                    "governance Plan cards do not match the trusted Plan identity",
                )

    try:
        normalized = json.loads(canonical_json(dict(carrier)))
    except (TypeError, ValueError) as exc:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context must be bounded plain data",
        ) from exc
    from aota_forge.work_plane.role_bootstrap import MAX_GOVERNANCE_CONTEXT_CARRIER_BYTES

    if len(canonical_json(normalized).encode("utf-8")) > MAX_GOVERNANCE_CONTEXT_CARRIER_BYTES:
        raise TaskMainContextError(
            "AUTHORITY_DENIED",
            "governance context exceeds its bounded carrier size",
        )
    return normalized


def dispatch_task_main_context(
    arguments: Mapping[str, Any] | None,
    binding: object,
) -> ToolResponse:
    """Execute the bounded M1/W1 context contract.

    ``inspect`` is read-only. ``bind`` validates the already materialized
    trusted Project/optional Plan identity; unbound selector resolution and
    delegate installation remain composition/transport concerns. ``switch``
    delegates to the composed W4 runtime; bindings without that trusted carrier
    remain unavailable.
    """

    if not isinstance(arguments, Mapping):
        return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "arguments must be an object"})
    try:
        arguments = normalize_context_arguments(arguments)
        mode = arguments.get("mode")
        if not isinstance(mode, str) or mode not in CONTEXT_MODES:
            raise TaskMainContextError("INVALID_MODE", "mode must be inspect, bind, or switch")
        locator = _locator_from_arguments(arguments)
        switch_runtime = getattr(binding, "context_switch_runtime", None)
        runtime_governance_context: object = _NO_GOVERNANCE_CONTEXT_OVERRIDE
        if switch_runtime is not None:
            try:
                runtime_view = switch_runtime.current_context()
                identity = runtime_view.identity
                authorized_roots = runtime_view.target.authorized_roots
                runtime_governance_context = runtime_view.target.governance_context
            except Exception as exc:
                code = getattr(exc, "code", "CONTEXT_SWITCH_RECOVERY_REQUIRED")
                raise TaskMainContextError(
                    code if isinstance(code, str) else "CONTEXT_SWITCH_RECOVERY_REQUIRED",
                    str(exc)[:512] or "durable context rehydration failed",
                ) from exc
        else:
            identity = trusted_identity_from_binding(binding)
            authorized_roots = _trusted_authorized_roots(binding)
        legacy_plan_binding = getattr(binding, "plan_binding", None)

        if mode == CONTEXT_MODE_INSPECT:
            if identity is None:
                if getattr(binding, "governance_context", None) is not None:
                    raise TaskMainContextError(
                        "AUTHORITY_DENIED",
                        "governance context requires a trusted Project/Plan binding",
                    )
                return ToolResponse.success(_snapshot(mode, None, binding_status="unbound"))
            governance_context = _trusted_governance_context(
                binding,
                identity,
                carrier_override=runtime_governance_context,
            )
            if identity.plan_binding is None:
                status = CONTEXT_STATE_PROJECT_BOUND
            else:
                status = "bound" if identity.plan_binding.source_digest is not None else "bound_unverified"
            session_ref = getattr(binding, "session_ref", None)
            if not is_bound_origin_session_ref(session_ref):
                session_ref = None
            return ToolResponse.success(
                _snapshot(
                    mode,
                    identity,
                    binding_status=status,
                    authorized_roots=authorized_roots,
                    governance_context=governance_context,
                    origin_session_ref=session_ref,
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

        if mode == CONTEXT_MODE_SWITCH and switch_runtime is not None:
            try:
                return ToolResponse.success(switch_runtime.switch(locator))
            except Exception as exc:
                code = getattr(exc, "code", "CONTEXT_SWITCH_RECOVERY_REQUIRED")
                raise TaskMainContextError(
                    code if isinstance(code, str) else "CONTEXT_SWITCH_RECOVERY_REQUIRED",
                    str(exc)[:512] or "context switch failed closed",
                ) from exc

        _match_locator(locator, identity, legacy_plan_binding=legacy_plan_binding)
        governance_context = _trusted_governance_context(
            binding,
            identity,
            carrier_override=runtime_governance_context,
        )

        if mode == CONTEXT_MODE_BIND:
            if identity.plan_binding is not None and identity.plan_binding.source_digest is None:
                raise TaskMainContextError(
                    "STALE_CONTEXT",
                    "trusted Plan binding has no observed source digest",
                )
            status = (
                CONTEXT_STATE_PROJECT_BOUND
                if identity.plan_binding is None
                else "bound"
            )
            session_ref = getattr(binding, "session_ref", None)
            if not is_bound_origin_session_ref(session_ref):
                session_ref = None
            return ToolResponse.success(
                _snapshot(
                    mode,
                    identity,
                    binding_status=status,
                    authorized_roots=authorized_roots,
                    governance_context=governance_context,
                    origin_session_ref=session_ref,
                )
            )

        # Legacy bindings keep the original fail-closed behavior when the W4
        # trusted runtime carrier was not composed.
        return ToolResponse.failure(
            {
                "code": "CONTEXT_SWITCH_UNAVAILABLE",
                "message": "live context switching requires the trusted W4 runtime composition",
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
    "CONTEXT_STATE_PROJECT_BOUND",
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
    "normalize_context_arguments",
    "trusted_identity_from_binding",
]
