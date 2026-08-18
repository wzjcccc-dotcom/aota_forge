"""M3-B11 legacy comparison evidence model.

Scope (Issue #9, Lane M3-B11):
- Compares legacy inputs, pointers, and evidence against materialized shadow state.
- Classifies comparison outcomes: MATCH, DRIFT, MISSING_LEGACY, MISSING_SHADOW, AMBIGUOUS.
- Invariants:
    LEGACY_COMPARISON_IMPLEMENTED = True
    SHADOW_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from aota_forge.core.graph import records
from aota_forge.core.idempotency import canonical_fingerprint

LEGACY_COMPARISON_IMPLEMENTED = True
SHADOW_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH = False


class ComparisonStatus:
    MATCH = "MATCH"
    DRIFT = "DRIFT"
    MISSING_LEGACY = "MISSING_LEGACY"
    MISSING_SHADOW = "MISSING_SHADOW"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class ComparisonItem:
    """Comparison evaluation between one legacy field/pointer and shadow state."""

    field_name: str
    legacy_value: Any
    shadow_value: Any
    status: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "legacy_value": self.legacy_value,
            "shadow_value": self.shadow_value,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ComparisonEvidence:
    """Aggregate comparison evidence for legacy vs shadow state."""

    target_ref: str
    items: tuple[ComparisonItem, ...] = ()

    @property
    def match_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComparisonStatus.MATCH)

    @property
    def drift_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComparisonStatus.DRIFT)

    @property
    def missing_count(self) -> int:
        return sum(
            1
            for i in self.items
            if i.status in (ComparisonStatus.MISSING_LEGACY, ComparisonStatus.MISSING_SHADOW)
        )

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComparisonStatus.AMBIGUOUS)

    def fingerprint(self) -> str:
        payload = {
            "target_ref": self.target_ref,
            "items": [i.to_dict() for i in self.items],
            "match_count": self.match_count,
            "drift_count": self.drift_count,
            "missing_count": self.missing_count,
            "ambiguous_count": self.ambiguous_count,
        }
        return canonical_fingerprint(payload)

    def to_dict(self) -> dict:
        return {
            "target_ref": self.target_ref,
            "comparison_fingerprint": self.fingerprint(),
            "match_count": self.match_count,
            "drift_count": self.drift_count,
            "missing_count": self.missing_count,
            "ambiguous_count": self.ambiguous_count,
            "items": [i.to_dict() for i in self.items],
            "drift_auto_rewrites_canonical_graph": False,
        }


def compare_legacy_and_shadow(
    legacy_payload: dict,
    shadow_subject: records.Subject,
    executions: list[records.Execution] | None = None,
    completions: list[records.Completion] | None = None,
    decisions: list[records.Decision] | None = None,
) -> ComparisonEvidence:
    """Construct deterministic ComparisonEvidence between legacy facts and shadow state."""
    items: list[ComparisonItem] = []
    target_ref = shadow_subject.subject_id.to_canonical()

    # 1. State / status comparison
    legacy_state = legacy_payload.get("current_status") or legacy_payload.get("state")
    shadow_state = shadow_subject.mechanical_state.get("state")
    if legacy_state is not None:
        if legacy_state == shadow_state:
            items.append(ComparisonItem("state", legacy_state, shadow_state, ComparisonStatus.MATCH))
        else:
            items.append(
                ComparisonItem(
                    "state",
                    legacy_state,
                    shadow_state,
                    ComparisonStatus.DRIFT,
                    f"Legacy state {legacy_state!r} != shadow state {shadow_state!r}",
                )
            )
    else:
        items.append(
            ComparisonItem("state", None, shadow_state, ComparisonStatus.MISSING_LEGACY)
        )

    # 2. Milestone comparison
    legacy_milestone = legacy_payload.get("milestone") or legacy_payload.get("current_milestone")
    shadow_milestone = shadow_subject.mechanical_state.get("milestone")
    if legacy_milestone is not None:
        if legacy_milestone == shadow_milestone:
            items.append(
                ComparisonItem("milestone", legacy_milestone, shadow_milestone, ComparisonStatus.MATCH)
            )
        else:
            items.append(
                ComparisonItem(
                    "milestone",
                    legacy_milestone,
                    shadow_milestone,
                    ComparisonStatus.DRIFT,
                    f"Legacy milestone {legacy_milestone!r} != shadow milestone {shadow_milestone!r}",
                )
            )

    # 3. Executions / Task count comparison
    legacy_tasks = legacy_payload.get("tasks") or []
    exec_count = len(executions or [])
    if isinstance(legacy_tasks, list):
        if len(legacy_tasks) == exec_count:
            items.append(ComparisonItem("task_count", len(legacy_tasks), exec_count, ComparisonStatus.MATCH))
        else:
            items.append(
                ComparisonItem(
                    "task_count",
                    len(legacy_tasks),
                    exec_count,
                    ComparisonStatus.DRIFT,
                    f"Legacy task count {len(legacy_tasks)} != shadow executions {exec_count}",
                )
            )

    # 4. Decisions comparison
    legacy_decisions = legacy_payload.get("decisions") or []
    dec_count = len(decisions or [])
    if isinstance(legacy_decisions, list):
        if len(legacy_decisions) == dec_count:
            items.append(
                ComparisonItem("decision_count", len(legacy_decisions), dec_count, ComparisonStatus.MATCH)
            )
        else:
            items.append(
                ComparisonItem(
                    "decision_count",
                    len(legacy_decisions),
                    dec_count,
                    ComparisonStatus.DRIFT,
                    f"Legacy decision count {len(legacy_decisions)} != shadow decisions {dec_count}",
                )
            )

    # 5. Check for legacy pointer ambiguity
    legacy_pointers = legacy_payload.get("current_pointers") or {}
    if isinstance(legacy_pointers, dict) and len(legacy_pointers.get("ambiguous_refs", [])) > 0:
        items.append(
            ComparisonItem(
                "current_pointers",
                legacy_pointers.get("ambiguous_refs"),
                None,
                ComparisonStatus.AMBIGUOUS,
                "Ambiguous legacy current pointers detected in evidence",
            )
        )

    return ComparisonEvidence(
        target_ref=target_ref,
        items=tuple(sorted(items, key=lambda i: i.field_name)),
    )


__all__ = [
    "LEGACY_COMPARISON_IMPLEMENTED",
    "SHADOW_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH",
    "ComparisonStatus",
    "ComparisonItem",
    "ComparisonEvidence",
    "compare_legacy_and_shadow",
]
