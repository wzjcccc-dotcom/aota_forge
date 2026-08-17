"""M3-B9 typed binding request model (Issue #9, lane M3-B9).

Canonical mechanical inputs for one deterministic subject binding / recovery
operation.  Normal model-facing input is the semantic public reference plus
workspace / project / Plan identity context and Subject kind
(``MODEL_INTERNAL_IDS_NORMAL_INPUT=no``).  Raw internal IDs are never a normal
input (``RAW_INTERNAL_ID_SELF_BINDING_ALLOWED=no``).

An explicitly trusted internal ``ObjectRef`` is supported ONLY through the
separate ``trusted_object_ref`` fast-path (``TRUSTED_OBJECT_REF_IMPLIES_MUTATION
_AUTHORITY=no``): it bypasses semantic lookup but still requires a valid typed
ref that resolves mechanically and still grants no authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.identity.kinds import (
    is_known_subject_kind,
    require_subject_kind,
)
from aota_forge.core.identity.refs import ObjectRef


@dataclass(frozen=True)
class BindingRequest:
    """Deterministic mechanical input for a B9 binding / recovery operation.

    Fields:
      semantic_ref       — the semantic public reference (plan_id / project_id /
                           workspace_id / followup semantic target).  Must NOT be
                           a raw internal ID, legacy ID, or current_* pointer.
      subject_sub_kind   — the stable Subject-kind discriminator being bound
                           (workspace / project / plan / work).
      workspace_id       — canonical workspace semantic identity context.
      project_id         — canonical project semantic identity context.
      plan_id            — canonical Plan semantic identity context.
      workflow_ref       — canonical workflow scope (typed ObjectRef or raw
                           canonical string) constraining candidates.
      parent_ref         — for followup child binding: canonical parent Subject
                           ObjectRef (or its canonical string).
      source_decision_ref— for decision-backed followup binding: the committed
                           Decision ObjectRef that must back the lineage.
      trusted_object_ref — OPTIONAL explicitly trusted internal ObjectRef fast
                           path; bypasses semantic lookup, never grants authority.
      expected_revision  — optional frozen A5 CAS precondition fact used only for
                           revision-conflict classification (never enforced).
      authority_eligible — optional frozen A2 authority-eligibility fact used
                           only to CLASSIFY SUBJECT_BINDING_AUTHORITY_DENIED;
                           B9 never decides authority itself.
      need_decision_basis — decision-backed binding requires the
                           committed Decision basis to exist in the canonical graph.
      lineage_only       — restrict child enumeration to canonical Decision-backed
                           FollowupEdge lineage (``NO_HIDDEN_REDUCTION_MANY_TO_ONE=yes``).
    """

    semantic_ref: str | None = None
    subject_sub_kind: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    plan_id: str | None = None
    workflow_ref: ObjectRef | None = None
    parent_ref: ObjectRef | None = None
    source_decision_ref: ObjectRef | None = None
    trusted_object_ref: ObjectRef | None = None
    expected_revision: int | None = None
    authority_eligible: bool | None = None
    need_decision_basis: bool = False
    lineage_only: bool = False
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.subject_sub_kind is not None and not is_known_subject_kind(self.subject_sub_kind):
            raise ValueError(f"unknown subject kind: {self.subject_sub_kind!r}")
        for ref, label in (
            (self.workflow_ref, "workflow_ref"),
            (self.parent_ref, "parent_ref"),
            (self.source_decision_ref, "source_decision_ref"),
            (self.trusted_object_ref, "trusted_object_ref"),
        ):
            if ref is not None and not isinstance(ref, ObjectRef):
                raise ValueError(f"{label} must be a typed ObjectRef")

    @property
    def subject_kind(self) -> str | None:
        return self.subject_sub_kind

    @property
    def uses_trusted_fast_path(self) -> bool:
        return self.trusted_object_ref is not None

    def require_subject_kind(self) -> str:
        if not self.subject_sub_kind:
            raise ValueError("subject_sub_kind is required for binding")
        return require_subject_kind(self.subject_sub_kind)


def complete_request(**kwargs) -> BindingRequest:
    """Build a validated BindingRequest (raises early on malformed inputs)."""
    request = BindingRequest(**kwargs)
    if not request.uses_trusted_fast_path:
        if not request.semantic_ref:
            raise ValueError("semantic_ref is required when no trusted ObjectRef is supplied")
    return request


__all__ = ["BindingRequest", "complete_request"]