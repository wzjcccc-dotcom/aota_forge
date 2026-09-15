"""AF #57 M1/W4 — trusted launch-time Plan Authority binding composition.

Source-neutral W1 ``PlanAuthorityBinding`` for the live thin task-main
composition path.  One internal Plan identity is bound to exactly one current
authority source:

    internal plan_id
         |
         v
    PlanAuthorityBinding
         |-- github_issue      existing Governance 1.x GitHub plan_ref
         |-- local_governance  AF #57 M1/W2 local governance authority ref

Exactly one source per launch:

* no ``plan_id`` -> no binding (existing launch unchanged; the accepted #55
  root set and the Governance 1.x GitHub path stay functional without any
  local-governance configuration);
* ``plan_id`` + exactly one available trusted source -> one binding;
* ``plan_id`` + zero or two available sources -> fail closed.  Silent dual
  authority is never materialized; the operator must bind exactly one source.

The local authority reference is derived by the W2 owner
(``local_plan_authority_reference``) from the canonical project identity and
the canonical internal Plan ID: the physical host path is never the authority
identity.  GitHub Issue numbers are never Core Plan identity: the internal
``plan_id`` is supplied explicitly by the trusted runtime and the Issue ref is
only the bound ``authority_ref`` of the Governance 1.x source.

Composition only: this module performs no Plan read, no Plan mutation, no
filesystem effect and no GitHub call, and it creates no second Plan authority
ontology (``PlanAuthorityMutationPort`` stays the only Plan mutation
protocol; the Project Governance Store stays the only Plan lifecycle store).
"""

from __future__ import annotations

from typing import Any

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.core.plan.validation import is_plan_id

PLAN_AUTHORITY_COMPOSITION_INVALID = "PLAN_AUTHORITY_COMPOSITION_INVALID"
PLAN_AUTHORITY_SOURCE_AMBIGUOUS = "PLAN_AUTHORITY_SOURCE_AMBIGUOUS"
PLAN_AUTHORITY_SOURCE_UNAVAILABLE = "PLAN_AUTHORITY_SOURCE_UNAVAILABLE"

LAUNCH_TIME_PLAN_AUTHORITY_BINDING_IS_AUTHORITY = False
ONE_LAUNCH_BINDS_EXACTLY_ONE_PLAN_AUTHORITY_SOURCE = True
LOCAL_GOVERNANCE_PATH_IS_AUTHORITY = False


class PlanAuthorityCompositionError(ValueError):
    """Fail-closed trusted launch-time Plan Authority composition error."""

    code = PLAN_AUTHORITY_COMPOSITION_INVALID

    def __init__(self, message: str, *, code: str = PLAN_AUTHORITY_COMPOSITION_INVALID) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def compose_plan_authority_binding(
    *,
    project_id: Any,
    plan_id: Any,
    github_plan_ref: Any = None,
    local_governance_enabled: bool = False,
) -> PlanAuthorityBinding | None:
    """One source-neutral PlanAuthorityBinding, or ``None`` without a Plan identity.

    ``github_plan_ref`` is the existing trusted GitHub Plan reference
    (``owner/repo#number`` already grounded by the runtime).
    ``local_governance_enabled`` states that the trusted operator governance
    base is active for exactly this project; the W2 authority reference is
    then derived here, never supplied as a physical path.
    """
    normalized_plan_id = _clean(plan_id)
    if normalized_plan_id is None:
        # A launch without an explicit internal Plan identity carries no Plan
        # authority binding at all (the existing Governance 1.x GitHub
        # plan_ref remains the runtime Plan authority; no second binding is
        # invented and no local governance is required).
        return None
    if not is_plan_id(normalized_plan_id):
        raise PlanAuthorityCompositionError(
            "plan_id must be one canonical internal Plan ID; an Issue number, "
            "worktree, branch, repository or title is never a Plan identity"
        )

    github_ref = _clean(github_plan_ref)
    sources: list[str] = []
    if github_ref is not None:
        sources.append(PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE)
    if local_governance_enabled:
        sources.append(PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE)

    if len(sources) > 1:
        raise PlanAuthorityCompositionError(
            "both a GitHub Plan reference and an active local-governance root "
            "are present for one Plan identity; refusing to materialize silent "
            "dual authority (bind exactly one source)",
            code=PLAN_AUTHORITY_SOURCE_AMBIGUOUS,
        )
    if not sources:
        raise PlanAuthorityCompositionError(
            "a bound Plan identity requires exactly one authority source "
            "(github_issue plan_ref or an active local-governance root); "
            "none is available",
            code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
        )

    if sources[0] == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE:
        return PlanAuthorityBinding(
            plan_id=normalized_plan_id,
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref=github_ref,
        )

    from aota_forge.adapters.plan_authority.local_governance import (
        local_plan_authority_reference,
    )

    project = _clean(project_id)
    if project is None:
        raise PlanAuthorityCompositionError(
            "local_governance binding requires the canonical project identity"
        )
    return PlanAuthorityBinding(
        plan_id=normalized_plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(project, normalized_plan_id),
    )


__all__ = [
    "PLAN_AUTHORITY_COMPOSITION_INVALID",
    "PLAN_AUTHORITY_SOURCE_AMBIGUOUS",
    "PLAN_AUTHORITY_SOURCE_UNAVAILABLE",
    "LAUNCH_TIME_PLAN_AUTHORITY_BINDING_IS_AUTHORITY",
    "ONE_LAUNCH_BINDS_EXACTLY_ONE_PLAN_AUTHORITY_SOURCE",
    "LOCAL_GOVERNANCE_PATH_IS_AUTHORITY",
    "PlanAuthorityCompositionError",
    "compose_plan_authority_binding",
]
