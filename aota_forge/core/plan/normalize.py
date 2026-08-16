"""Portable Plan normalization and governance drift observation (M2-D).

``normalize_portable_plan`` turns the authoritative issue body into a
canonical :class:`PortablePlanDocument`, distinguishing:

    current authoritative Plan state
    historical / superseded evidence blocks
    Milestone definitions
    governance observations
    structured appendices / provenance

Current-state fields come ONLY from ``current`` sections.  Historical blocks
are provenance observations and can never override current state.

``observe_governance_projection_drift`` derives the expected operational
projection from the normalized document and compares it against canonical
control-comment snapshots.  On mismatch it returns bounded diff evidence
with ``GOVERNANCE_PROJECTION_DRIFT=yes``; the issue body remains the single
Plan authority and no automatic repair is performed.

Genuine semantic contradictions within the authoritative current state
produce a deterministic :class:`PlanNormalizationError` — never a vote,
latest-wins guess, or control-comment fallback.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.plan.read_model import PortablePlanDocument, portable_plan_digest
from aota_forge.core.plan.sections import (
    KIND_APPENDIX,
    KIND_CURRENT,
    KIND_GOVERNANCE,
    KIND_HISTORICAL,
    KIND_MILESTONE,
    MILESTONE_PREFIX_RE,
    BodySection,
    parse_body_sections,
    section_diagnostics,
)
from aota_forge.core.plan.validation import MILESTONE_ID_RE

MILESTONE_STATUS_KEY_RE = re.compile(r"^(M[0-9]+)_STATUS$")

PROJECT_CONTEXT_KEYS = frozenset(
    {
        "CANONICAL_PROJECT_ID",
        "CANONICAL_SOURCE_REPOSITORY",
        "CANONICAL_PROJECT_ROOT",
    }
)
KNOWN_GOOD_CHECKPOINT_KEYS = (
    "ACCEPTED_WRITABLE_BASE",
    "M1_SOURCE_CANDIDATE",
    "M1_KNOWN_GOOD_CHECKPOINT",
    "LEGACY_ACCEPTED_WRITABLE_BASE",
    "LEGACY_PREDECESSOR_KNOWN_GOOD_CHECKPOINT",
)
PROJECTION_FIELDS = ("CURRENT_MILESTONE", "PLAN_STATUS", "CURRENT_BLOCKER", "HANDOFF_STATE")
FIELD_TO_ATTR = {
    "CURRENT_MILESTONE": "current_milestone",
    "PLAN_STATUS": "plan_status",
    "CURRENT_BLOCKER": "current_blocker",
    "HANDOFF_STATE": "handoff_state",
}
MAX_DIAGNOSTICS = 100

CANONICAL_STATE_KEYS = frozenset(
    {
        "PLAN_STATUS",
        "CURRENT_MILESTONE",
        "CURRENT_STATUS",
        "CURRENT_BLOCKER",
        "COMPLETED_MILESTONES",
        "HANDOFF_STATE",
    }
) | PROJECT_CONTEXT_KEYS | set(KNOWN_GOOD_CHECKPOINT_KEYS)


class PlanNormalizationError(ForgeError):
    """Deterministic Portable Plan normalization failure (M2-D).

    Raised only for genuine contradictions or malformed authoritative
    current state; never resolved by guessing.
    """

    def __init__(self, diagnostic_code: str, message: str) -> None:
        super().__init__("PLAN_NORMALIZATION_ERROR", f"[{diagnostic_code}] {message}", retryable=False)
        self.diagnostic_code = diagnostic_code


def _merge_current_state(sections: list[BodySection]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    current_fields: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    for section in sections:
        if section.kind != KIND_CURRENT:
            for key in section.key_values:
                if key in CANONICAL_STATE_KEYS or key.startswith("CURRENT_"):
                    diagnostics.append(
                        {
                            "code": "CURRENT_KEY_OUTSIDE_CURRENT_SECTION",
                            "severity": "info",
                            "section": section.title,
                            "key": key,
                            "value": section.key_values[key],
                        }
                    )
            continue
        for key, value in section.key_values.items():
            if key not in current_fields:
                current_fields[key] = value
                continue
            if current_fields[key] == value:
                diagnostics.append(
                    {
                        "code": "DUPLICATE_CURRENT_KEY_SAME_VALUE",
                        "severity": "info",
                        "section": section.title,
                        "key": key,
                    }
                )
                continue
            if key == "CURRENT_NEXT_ACTION":
                diagnostics.append(
                    {
                        "code": "DUPLICATE_CURRENT_NEXT_ACTION",
                        "severity": "warning",
                        "section": section.title,
                        "key": key,
                        "first_value": current_fields[key],
                        "value": value,
                    }
                )
                continue
            if key in CANONICAL_STATE_KEYS or MILESTONE_STATUS_KEY_RE.fullmatch(key):
                raise PlanNormalizationError(
                    "CURRENT_STATE_CONTRADICTION",
                    f"current-state key {key!r} contradicts across current sections: "
                    f"{current_fields[key]!r} vs {value!r} (section {section.title!r})",
                )
            diagnostics.append(
                {
                    "code": "DUPLICATE_CURRENT_KEY_DIFFERENT_VALUE",
                    "severity": "info",
                    "section": section.title,
                    "key": key,
                    "first_value": current_fields[key],
                    "value": value,
                }
            )
    return current_fields, diagnostics


def _milestone_id_from_title(title: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    match = MILESTONE_PREFIX_RE.match(normalized)
    if match:
        return f"M{match.group(1)}"
    return None


def _build_milestone_specs(sections: list[BodySection], diagnostics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for section in sections:
        if section.kind != KIND_MILESTONE:
            continue
        milestone_id = _milestone_id_from_title(section.title)
        status: str | None = None
        if milestone_id is not None:
            status = section.key_values.get(f"{milestone_id}_STATUS")
        else:
            for key, value in section.key_values.items():
                status_match = MILESTONE_STATUS_KEY_RE.fullmatch(key)
                if status_match:
                    milestone_id = status_match.group(1)
                    status = value
                    break
        if milestone_id is None:
            diagnostics.append(
                {"code": "MILESTONE_SECTION_WITHOUT_ID", "severity": "warning", "section": section.title}
            )
            continue
        if milestone_id in specs:
            diagnostics.append(
                {"code": "MILESTONE_SPEC_DUPLICATE", "severity": "info", "section": section.title, "milestone_id": milestone_id}
            )
            continue
        specs[milestone_id] = {
            "title": section.title,
            "status": status,
            "key_count": len(section.key_values),
            "kind": "definition",
        }
    return specs


def normalize_portable_plan(body: str, *, source_revision: str | None = None) -> PortablePlanDocument:
    """Normalize the authoritative issue body into a PortablePlanDocument.

    Raises :class:`PlanNormalizationError` on genuine current-state
    contradictions or malformed authoritative values.  Returns a canonical
    read-only document otherwise.
    """
    if not isinstance(body, str) or not body.strip():
        raise PlanNormalizationError("EMPTY_PLAN_BODY", "authoritative issue body is empty")

    sections = parse_body_sections(body)
    diagnostics: list[dict[str, Any]] = []
    for section in sections:
        diagnostics.extend(section_diagnostics(section))
        if section.kind == KIND_CURRENT:
            for key, values in section.duplicates:
                if len(set(values)) > 1:
                    raise PlanNormalizationError(
                        "CURRENT_STATE_CONTRADICTION",
                        f"current-state key {key!r} contradicts within section {section.title!r}: {values!r}",
                    )

    current_fields, merge_diagnostics = _merge_current_state(sections)
    diagnostics.extend(merge_diagnostics)

    current_milestone: str | None = None
    raw_milestone = current_fields.get("CURRENT_MILESTONE")
    if raw_milestone is not None:
        raw_milestone = raw_milestone.strip()
        if not MILESTONE_ID_RE.fullmatch(raw_milestone):
            raise PlanNormalizationError(
                "MALFORMED_CURRENT_MILESTONE",
                f"CURRENT_MILESTONE value {raw_milestone!r} is not a valid milestone id (expected M<digits>)",
            )
        current_milestone = raw_milestone

    completed_milestones: tuple[str, ...] = ()
    raw_completed = current_fields.get("COMPLETED_MILESTONES")
    if raw_completed is not None:
        entries = [entry.strip() for entry in re.split(r"[\s,;|]+", raw_completed.strip()) if entry.strip()]
        for entry in entries:
            if not MILESTONE_ID_RE.fullmatch(entry):
                raise PlanNormalizationError(
                    "MALFORMED_COMPLETED_MILESTONES",
                    f"COMPLETED_MILESTONES entry {entry!r} is not a valid milestone id (expected M<digits>)",
                )
        completed_milestones = tuple(entries)

    milestone_status: dict[str, str] = {}
    for key, value in current_fields.items():
        match = MILESTONE_STATUS_KEY_RE.fullmatch(key)
        if match:
            milestone_status[match.group(1)] = value

    milestone_specs = _build_milestone_specs(sections, diagnostics)

    project_context = {key: current_fields[key] for key in PROJECT_CONTEXT_KEYS if key in current_fields}
    known_good_checkpoints = tuple(
        current_fields[key] for key in KNOWN_GOOD_CHECKPOINT_KEYS if key in current_fields
    )

    governance: dict[str, str] = {}
    provenance_observations: dict[str, dict[str, str]] = {}
    for section in sections:
        if section.kind == KIND_GOVERNANCE:
            for key, value in section.key_values.items():
                if key in governance:
                    diagnostics.append(
                        {
                            "code": "DUPLICATE_GOVERNANCE_KEY",
                            "severity": "info",
                            "section": section.title,
                            "key": key,
                        }
                    )
                governance[key] = value
        elif section.kind in (KIND_HISTORICAL, KIND_APPENDIX):
            if section.key_values:
                provenance_observations[section.title] = dict(section.key_values)

    document = PortablePlanDocument(
        plan_status=current_fields.get("PLAN_STATUS"),
        current_milestone=current_milestone,
        current_status=current_fields.get("CURRENT_STATUS"),
        current_blocker=current_fields.get("CURRENT_BLOCKER"),
        completed_milestones=completed_milestones,
        milestone_status=milestone_status,
        milestone_specs=milestone_specs,
        current_next_action=current_fields.get("CURRENT_NEXT_ACTION"),
        handoff_state=current_fields.get("HANDOFF_STATE"),
        project_context=project_context,
        known_good_checkpoints=known_good_checkpoints,
        governance=governance,
        provenance_observations=provenance_observations,
        current_fields=current_fields,
        diagnostics=_bounded_diagnostics(diagnostics),
        source_revision=source_revision,
        source_kind="portable_plan_issue_body",
    )
    digest = portable_plan_digest(document)
    return replace(document, source_digest=digest)


def _bounded_diagnostics(diagnostics: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Cap diagnostics at MAX_DIAGNOSTICS, keeping warnings before info."""
    severity_order = {"error": 0, "warning": 1, "info": 2}
    ordered = sorted(diagnostics, key=lambda item: severity_order.get(item.get("severity", "info"), 2))
    bounded = ordered[:MAX_DIAGNOSTICS]
    if len(ordered) > MAX_DIAGNOSTICS:
        bounded.append(
            {
                "code": "DIAGNOSTICS_TRUNCATED",
                "severity": "info",
                "total": len(ordered),
                "kept": MAX_DIAGNOSTICS,
            }
        )
    return tuple(bounded)


def derive_expected_projection(document: PortablePlanDocument) -> dict[str, str]:
    """Expected operational projection derived from the normalized document."""
    return {
        field: getattr(document, FIELD_TO_ATTR[field]) or "" for field in PROJECTION_FIELDS
    }


def observe_governance_projection_drift(
    document: PortablePlanDocument,
    control_projections: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Compare canonical control-comment snapshots against the expected projection.

    Read-only observation: returns bounded diff evidence and
    ``GOVERNANCE_PROJECTION_DRIFT=yes|no``.  The issue body always wins;
    there is no arbitration and no automatic repair.
    """
    expected = derive_expected_projection(document)
    provided: dict[str, dict[str, str]] = {}
    differences: list[dict[str, Any]] = []
    for label, projection in control_projections.items():
        included: dict[str, str] = {}
        for field in PROJECTION_FIELDS:
            if field in projection:
                included[field] = projection[field]
                if projection[field] != expected[field]:
                    differences.append(
                        {
                            "control_label": label,
                            "field": field,
                            "expected": expected[field],
                            "provided": projection[field],
                        }
                    )
        if included:
            provided[label] = included
    drift = bool(differences)
    return {
        "GOVERNANCE_PROJECTION_DRIFT": "yes" if drift else "no",
        "expected_projection": expected,
        "provided_projections": provided,
        "differences": differences[:50],
        "BODY_CONTROL_COMMENT_CONFLICT_ARBITRATION_REQUIRED": "no",
        "authority": "issue_body",
        "control_comment_authority": "projection_only",
        "automatic_repair": "not_implemented",
    }
