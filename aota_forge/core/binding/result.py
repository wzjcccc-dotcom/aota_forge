"""M3-B9 local typed binding / recovery result model (Issue #9, lane M3-B9).

Shared ``core/contracts/errors.py`` / ``core/contracts/results.py`` are
integration-only hot files and are NOT modified (``B9_SHARED_ERRORS_WRITE_ALLOWED
=no``, ``B9_SHARED_RESULTS_WRITE_ALLOWED=no``).  B9 therefore owns a local typed
result model carrying stable machine-readable result codes plus deterministic
bounded candidate facts suitable for a later external semantic orchestrator.

Result codes (frozen A5 conflict taxonomy, executor-neutral):

* ``BOUND``                            — exactly one valid candidate, all
                                        validity conditions satisfied
* ``NOT_FOUND``                        — zero valid candidates; the durable
                                        canonical Subject is not discoverable
* ``NEEDS_SEMANTIC_CHOICE``            — more than one valid candidate; no
                                        automatic selection
* ``INVALID_REFERENCE``                — raw internal ID / legacy ID / current_*
                                        pointer supplied as a semantic ref
* ``PROJECTION_STALE_RECONCILABLE``    — a durable canonical Subject exists but
                                        a derived projection/index/pointer is
                                        stale or missing (read-only diagnosis)
* ``SUBJECT_BINDING_PREDECESSOR_INVALID`` — candidate passes enumerated filters
                                        but its predecessor state is illegal
* ``SUBJECT_BINDING_REVISION_CONFLICT``    — expected revision != current at the
                                        A5 CAS precondition classification
* ``SUBJECT_BINDING_AUTHORITY_DENIED``     — supplied frozen authority-eligibility
                                        fact is negative (classification only;
                                        B9 never decides authority by itself)
* ``MATERIALIZED_DECISION_MISSING``       — decision-backed binding requested
                                        without a durable committed Decision basis

A binding result NEVER implies mutation authority
(``SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY=no``,
``OBJECT_REF_IS_AUTHORITY=no``): the result carries the selected canonical
ObjectRef but no ALLOW decision, no lease, and no CAS entitlement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.identity.refs import ObjectRef


class BindingStatus(str, Enum):
    """Bounded deterministic outcome of one B9 binding operation."""

    BOUND = "BOUND"
    NOT_FOUND = "NOT_FOUND"
    NEEDS_SEMANTIC_CHOICE = "NEEDS_SEMANTIC_CHOICE"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    PROJECTION_STALE_RECONCILABLE = "PROJECTION_STALE_RECONCILABLE"
    SUBJECT_BINDING_PREDECESSOR_INVALID = "SUBJECT_BINDING_PREDECESSOR_INVALID"
    SUBJECT_BINDING_REVISION_CONFLICT = "SUBJECT_BINDING_REVISION_CONFLICT"
    SUBJECT_BINDING_AUTHORITY_DENIED = "SUBJECT_BINDING_AUTHORITY_DENIED"
    MATERIALIZED_DECISION_MISSING = "MATERIALIZED_DECISION_MISSING"


@dataclass(frozen=True)
class BindingCandidate:
    """Deterministic bounded candidate fact exposed to a semantic orchestrator.

    Only safe mechanical facts are exposed: canonical ObjectRef, subject kind,
    owning workflow/Decision refs, and bounded lineage facts.  It never
    contains host paths, secrets, executor-private state, or a recommendation.
    """

    subject_ref: ObjectRef
    sub_kind: str
    id_derivation: str
    owning_workflow_ref: str | None = None
    source_decision_ref: str | None = None

    def to_choice(self) -> dict[str, Any]:
        """Deterministic fact payload for ``semantic_choices`` (no recommendation)."""
        payload: dict[str, Any] = {
            "object_ref": self.subject_ref.serialize(),
            "subject_kind": self.sub_kind,
            "id_derivation": self.id_derivation,
        }
        if self.owning_workflow_ref is not None:
            payload["owning_workflow_ref"] = self.owning_workflow_ref
        if self.source_decision_ref is not None:
            payload["source_decision_ref"] = self.source_decision_ref
        return payload


@dataclass(frozen=True)
class BindingResult:
    """Bounded executor-neutral result of one B9 binding / recovery operation.

    ``semantic_choices`` is a deterministic tuple of ``BindingCandidate.to_choice()``
    dicts emitted ONLY for ``NEEDS_SEMANTIC_CHOICE`` (and as diagnostic facts on
    other bounded outcomes).  It never contains a recommended candidate.
    """

    status: BindingStatus
    subject_ref: ObjectRef | None = None
    candidates: tuple[BindingCandidate, ...] = ()
    semantic_choices: tuple[Mapping[str, Any], ...] = ()
    detail: tuple[tuple[str, str], ...] = ()
    authority: bool = False  # ALWAYS False: binding implies no mutation authority.

    def to_audit(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "subject_ref": self.subject_ref.serialize() if self.subject_ref else None,
            "candidates": [c.subject_ref.serialize() for c in self.candidates],
            "semantic_choices": [dict(c) for c in self.semantic_choices],
            "detail": {k: v for k, v in self.detail},
            "authority": False,
        }


def success_bound(subject_ref: ObjectRef, *, detail: tuple[tuple[str, str], ...] = ()) -> BindingResult:
    return BindingResult(
        status=BindingStatus.BOUND,
        subject_ref=subject_ref,
        candidates=(),
        semantic_choices=(),
        detail=detail,
        authority=False,
    )


def candidates_only(
    status: BindingStatus,
    candidates: tuple[BindingCandidate, ...],
    *,
    detail: tuple[tuple[str, str], ...] = (),
) -> BindingResult:
    """Bounded outcome carrying deterministic candidate facts (no choice)."""
    choices = tuple(candidate.to_choice() for candidate in candidates) if candidates else ()
    return BindingResult(
        status=status,
        subject_ref=None,
        candidates=candidates,
        semantic_choices=choices,
        detail=detail,
        authority=False,
    )


def plain(status: BindingStatus, *, detail: tuple[tuple[str, str], ...] = ()) -> BindingResult:
    return BindingResult(
        status=status,
        subject_ref=None,
        candidates=(),
        semantic_choices=(),
        detail=detail,
        authority=False,
    )


__all__ = [
    "BindingCandidate",
    "BindingResult",
    "BindingStatus",
    "candidates_only",
    "plain",
    "success_bound",
]