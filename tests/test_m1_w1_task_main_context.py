"""M1/W1 task-main context contract and trusted identity proof."""

from __future__ import annotations

from types import SimpleNamespace

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.task_main_context import dispatch_task_main_context
from aota_forge.core_ingress import (
    CanonicalDispatchBinding,
    dispatch_tool_operation,
    resolve_descriptor,
)
from aota_forge.mcp_transport import (
    CONTEXT_OPERATIONS,
    SUPPORTED_OPERATIONS,
    UnboundHostContext,
    _UnboundAotaAdapter,
    help_operation_projection,
)
from aota_forge.work_plane.task_main_exposure import canonical_task_main_operations


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_context_contract"
AUTHORITY_REF = "local-governance:aota_forge/plan_context_contract"
SOURCE_DIGEST = "a" * 64


def _plan_binding(*, digest: str | None = SOURCE_DIGEST) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=AUTHORITY_REF,
        source_revision=5,
        source_digest=digest,
    )


def _binding(*, digest: str | None = SOURCE_DIGEST) -> CanonicalDispatchBinding:
    return CanonicalDispatchBinding(
        project_id=PROJECT_ID,
        plan_authority_binding=_plan_binding(digest=digest),
    )


def test_context_descriptor_and_exposure_use_the_canonical_contract() -> None:
    descriptor = resolve_descriptor("task_main.context")

    assert descriptor.name == "task_main.context"
    assert {item.name for item in descriptor.inputs} == {"mode", "project_ref", "plan_ref"}
    assert descriptor.read_write == "read"
    assert descriptor.required_authority == "trusted_project_plan_binding"
    assert CONTEXT_OPERATIONS == ("task_main.context",)
    assert "task_main.context" in SUPPORTED_OPERATIONS
    assert "task_main.context" in canonical_task_main_operations()


def test_inspect_is_read_only_and_unbound_is_truthful() -> None:
    response = dispatch_tool_operation(
        "task_main.context",
        {"mode": "inspect"},
        CanonicalDispatchBinding(unbound_host_session=True),
    )

    assert response.ok is True
    assert response.payload == {
        "mode": "inspect",
        "state": "unbound",
        "session_binding_is_authority": False,
        "identity_is_authority": False,
        "locator_is_authority": False,
        "context": None,
    }


def test_unbound_mcp_adapter_uses_the_same_context_contract() -> None:
    projection = help_operation_projection("task_main.context")
    result = _UnboundAotaAdapter(UnboundHostContext(repo_root="/tmp")).invoke(
        "task_main.context",
        {"mode": "inspect"},
    )

    assert projection["operation"] == "task_main.context"
    assert projection["required_inputs"] == ["mode"]
    assert result["ok"] is True
    assert result["payload"]["state"] == "unbound"


def test_bind_uses_trusted_identity_and_locator_is_not_authority() -> None:
    response = dispatch_task_main_context(
        {"mode": "bind", "project_ref": PROJECT_ID, "plan_ref": PLAN_ID},
        _binding(),
    )

    assert response.ok is True
    assert response.payload is not None
    assert response.payload["state"] == "bound"
    assert response.payload["context"]["project_id"] == PROJECT_ID
    assert response.payload["context"]["plan_id"] == PLAN_ID
    assert response.payload["context"]["identity_is_authority"] is False
    assert response.payload["context"]["locator_is_authority"] is False


def test_cross_project_and_cross_plan_locators_fail_closed() -> None:
    foreign_project = dispatch_task_main_context(
        {"mode": "bind", "project_ref": "other_project"},
        _binding(),
    )
    foreign_plan = dispatch_task_main_context(
        {"mode": "bind", "plan_ref": "plan_other"},
        _binding(),
    )

    assert foreign_project.error is not None
    assert foreign_project.error["code"] == "PROJECT_CONTEXT_MISMATCH"
    assert foreign_plan.error is not None
    assert foreign_plan.error["code"] == "PLAN_CONTEXT_MISMATCH"


def test_stale_binding_and_live_switch_fail_closed_without_mutation() -> None:
    stale = dispatch_task_main_context(
        {"mode": "bind", "plan_ref": PLAN_ID},
        _binding(digest=None),
    )
    switch = dispatch_task_main_context(
        {"mode": "switch", "plan_ref": PLAN_ID},
        _binding(),
    )

    assert stale.error is not None
    assert stale.error["code"] == "STALE_CONTEXT"
    assert switch.error is not None
    assert switch.error["code"] == "CONTEXT_SWITCH_UNAVAILABLE"


def test_model_values_cannot_be_promoted_to_trusted_identity() -> None:
    plain = SimpleNamespace(
        project_id=PROJECT_ID,
        plan_authority_binding={
            "plan_id": PLAN_ID,
            "source_kind": "local_governance",
            "authority_ref": AUTHORITY_REF,
        },
    )
    response = dispatch_task_main_context(
        {"mode": "bind", "project_ref": PROJECT_ID, "plan_ref": PLAN_ID},
        plain,
    )

    assert response.error is not None
    assert response.error["code"] == "CONTEXT_BINDING_REQUIRED"
