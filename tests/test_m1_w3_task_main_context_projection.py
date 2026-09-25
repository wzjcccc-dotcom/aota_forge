"""M1/W3 bounded Cards/ContextRoute projection through task_main.context."""

from __future__ import annotations

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.core.task_main_context import dispatch_task_main_context
from aota_forge.core_ingress import CanonicalDispatchBinding


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_context_projection"
AUTHORITY_REF = f"local-governance:{PROJECT_ID}/{PLAN_ID}"
SOURCE_DIGEST = "b" * 64


def _binding(*, governance_context=None, digest: str | None = SOURCE_DIGEST):
    return CanonicalDispatchBinding(
        project_id=PROJECT_ID,
        plan_authority_binding=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind="local_governance",
            authority_ref=AUTHORITY_REF,
            source_revision=7,
            source_digest=digest,
        ),
        governance_context=governance_context,
    )


def _governance_context(
    *,
    project_id: str = PROJECT_ID,
    plan_id: str = PLAN_ID,
    authority_ref: str = AUTHORITY_REF,
    digest: str | None = SOURCE_DIGEST,
) -> dict:
    return {
        "CONTEXT_ROUTE": {
            "project_id": project_id,
            "plan_id": plan_id,
            "authority_source_kind": "local_governance",
            "entries": [
                {
                    "kind": "plan_authority",
                    "ref": authority_ref,
                    "source_kind": "local_governance",
                    "revision": "7",
                    "digest": digest,
                    "complete": digest is not None,
                    "IS_AUTHORITY": False,
                }
            ],
            "is_authority": False,
            "CONTEXT_ROUTE_IS_AUTHORITY": False,
        },
        "CARDS": {
            "project": {"project_id": project_id},
            "plans": [{"project_id": project_id, "plan_id": plan_id}],
        },
        "CARDS_PRESENT": True,
        "IS_AUTHORITY": False,
        "GOVERNANCE_CONTEXT_IS_AUTHORITY": False,
    }


def test_context_projection_is_exposed_from_the_trusted_carrier() -> None:
    carrier = _governance_context()

    inspected = dispatch_task_main_context(
        {"mode": "inspect"},
        _binding(governance_context=carrier),
    )
    bound = dispatch_task_main_context(
        {"mode": "bind", "project_ref": PROJECT_ID, "plan_ref": PLAN_ID},
        _binding(governance_context=carrier),
    )

    assert inspected.ok is True, inspected.error
    assert bound.ok is True, bound.error
    assert inspected.payload["governance_context"] == carrier
    assert bound.payload["governance_context"] == carrier
    assert inspected.payload["governance_context"]["IS_AUTHORITY"] is False


def test_context_projection_rejects_cross_project_and_cross_plan_carriers() -> None:
    foreign_project = dispatch_task_main_context(
        {"mode": "inspect"},
        _binding(governance_context=_governance_context(project_id="other_project")),
    )
    foreign_plan = dispatch_task_main_context(
        {"mode": "inspect"},
        _binding(governance_context=_governance_context(plan_id="plan_other")),
    )

    assert foreign_project.ok is False
    assert foreign_project.error["code"] == "PROJECT_CONTEXT_MISMATCH"
    assert foreign_plan.ok is False
    assert foreign_plan.error["code"] == "PLAN_CONTEXT_MISMATCH"


def test_context_projection_rejects_stale_authority_evidence() -> None:
    stale = dispatch_task_main_context(
        {"mode": "inspect"},
        _binding(governance_context=_governance_context(digest="c" * 64)),
    )

    assert stale.ok is False
    assert stale.error["code"] == "STALE_CONTEXT"


def test_context_projection_cannot_attach_to_an_unbound_binding() -> None:
    response = dispatch_task_main_context(
        {"mode": "inspect"},
        CanonicalDispatchBinding(governance_context=_governance_context()),
    )

    assert response.ok is False
    assert response.error["code"] == "AUTHORITY_DENIED"
