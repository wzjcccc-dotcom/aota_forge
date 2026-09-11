"""Executor-neutral Portable Plan read models (M1-C / M2-D).

M1-C: PortablePlanSnapshot / MilestoneSnapshot / WorkItemSnapshot.

M2-D: PortablePlanDocument — the canonical normalized representation of the
authoritative Portable Plan (the governing Issue body).  It distinguishes
current authoritative state, Milestone definitions/status, known-good
checkpoint references, governance observations, and structured
appendix/provenance observations, and carries normalization diagnostics plus
source revision/digest.  It never carries executor-private IDs as schema
fields, and it grants no authority.

These read models describe observed Plan state without granting authority.
The Issue body remains Portable Plan authority.  Legacy plan.json state, when
read for comparison/migration evidence, is isolated behind an explicit
LegacyPlanStateReader (shadow/migration evidence only; no dual authority,
no Plan mutation in M1).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import ProjectManifestInvalidError

PLAN_ID_RE = re.compile(r"^plan_[a-zA-Z0-9]+(?:[_-][a-zA-Z0-9]+)*$")
MAX_PLAN_BYTES = 256 * 1024


@dataclass(frozen=True)
class WorkItemSnapshot:
    work_item_id: str
    status: str
    milestone_id: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "status": self.status,
            "milestone_id": self.milestone_id,
            "title": self.title,
        }


@dataclass(frozen=True)
class MilestoneSnapshot:
    milestone_id: str
    status: str
    work_items: list[WorkItemSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "status": self.status,
            "work_items": [w.to_dict() for w in self.work_items],
        }


@dataclass(frozen=True)
class PortablePlanSnapshot:
    plan_id: str
    revision: int
    sha256: str
    status: str
    current_milestone: str | None = None
    milestones: list[MilestoneSnapshot] = field(default_factory=list)
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "revision": self.revision,
            "sha256": self.sha256,
            "status": self.status,
            "current_milestone": self.current_milestone,
            "milestones": [m.to_dict() for m in self.milestones],
            "source": self.source,
        }


def snapshot_sha256(payload: dict[str, Any]) -> str:
    """Deterministic sha256 over canonical snapshot JSON."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PortablePlanDocument:
    """Canonical normalized Portable Plan document (M2-D / M3-RV2-B003).

    Built exclusively from the authoritative issue body by
    ``aota_forge.core.plan.normalize.normalize_portable_plan``.

    Executor-private IDs are NOT part of this schema; raw body KEY=VALUE
    observations are kept separately under ``current_fields`` for bounded
    diagnostics, never as typed schema fields.

    M3-B003 extensions (executor-neutral, typed):
      * ``milestone_work_items`` — work item identities per milestone
      * ``milestone_dependencies`` — dependency edges per milestone
      * ``milestone_approvals`` — explicit user approval per milestone (yes/no)
        Missing/ambiguous remains absent (unknown) instead of default True/False.
      * ``entry_base`` — canonical entry base from Plan authority (ENTRY_BASE)
      * ``milestone_dag_raw`` — raw DAG source for diagnostics
    M3/W1-R1 extensions (executor-neutral, typed):
      * ``milestone_section_prose`` — bounded milestone prose per milestone
        (free text minus KEY=VALUE/fences, <=4096 chars each). Source for
        generic per-Work semantic derivation; covered by plan digest.
    """

    plan_status: str | None = None
    current_milestone: str | None = None
    current_status: str | None = None
    current_blocker: str | None = None
    completed_milestones: tuple[str, ...] = ()
    milestone_status: dict[str, str] = field(default_factory=dict)
    milestone_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_next_action: str | None = None
    handoff_state: str | None = None
    project_context: dict[str, str] = field(default_factory=dict)
    known_good_checkpoints: tuple[str, ...] = ()
    governance: dict[str, str] = field(default_factory=dict)
    provenance_observations: dict[str, dict[str, str]] = field(default_factory=dict)
    current_fields: dict[str, str] = field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()
    source_revision: str | None = None
    source_digest: str = ""
    source_kind: str = "portable_plan_issue_body"
    # M3-B003 canonical extensions (executor-neutral)
    milestone_work_items: dict[str, tuple[str, ...]] = field(default_factory=dict)
    milestone_dependencies: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    milestone_approvals: dict[str, bool] = field(default_factory=dict)
    milestone_dag_raw: dict[str, str] = field(default_factory=dict)
    entry_base: str | None = None
    # M3/W1-R1 bounded milestone prose (executor-neutral, digest-covered)
    milestone_section_prose: dict[str, str] = field(default_factory=dict)
    # W4 bounded Work source slices (structural, digest-covered, faithful)
    work_source_slices: dict[str, tuple[Any, ...]] = field(default_factory=dict)

    def canonical_dict(self) -> dict[str, Any]:
        """Digest payload: everything except the derived digest itself."""
        # Serialize work_source_slices deterministically for digest
        slices_payload: dict[str, list[dict[str, Any]]] = {}
        for mid in sorted(self.work_source_slices.keys()):
            raw = self.work_source_slices.get(mid, ())
            items: list[dict[str, Any]] = []
            for s in raw:
                try:
                    items.append(s.to_dict() if hasattr(s, "to_dict") else dict(s))
                except Exception:
                    continue
            # Deterministic order by work_item_id
            items.sort(key=lambda d: d.get("work_item_id", ""))
            slices_payload[mid] = items
        return {
            "source_kind": self.source_kind,
            "source_revision": self.source_revision,
            "plan_status": self.plan_status,
            "current_milestone": self.current_milestone,
            "current_status": self.current_status,
            "current_blocker": self.current_blocker,
            "completed_milestones": list(self.completed_milestones),
            "milestone_status": self.milestone_status,
            "milestone_specs": self.milestone_specs,
            "current_next_action": self.current_next_action,
            "handoff_state": self.handoff_state,
            "project_context": self.project_context,
            "known_good_checkpoints": list(self.known_good_checkpoints),
            "governance": self.governance,
            "provenance_observations": self.provenance_observations,
            "current_fields": self.current_fields,
            "milestone_work_items": {k: list(v) for k, v in self.milestone_work_items.items()},
            "milestone_dependencies": {k: [list(e) for e in v] for k, v in self.milestone_dependencies.items()},
            "milestone_approvals": dict(self.milestone_approvals),
            "milestone_dag_raw": dict(self.milestone_dag_raw),
            "entry_base": self.entry_base,
            "milestone_section_prose": dict(self.milestone_section_prose),
            "work_source_slices": slices_payload,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["source_digest"] = self.source_digest
        payload["diagnostics"] = list(self.diagnostics)
        return payload


def portable_plan_digest(document: PortablePlanDocument) -> str:
    """Deterministic digest over the canonical Portable Plan payload."""
    return snapshot_sha256(document.canonical_dict())


# ---------------------------------------------------------------------------
# M3/W1-R1 bounded governed Work semantic view (F2).
#
# Projection of existing Plan authority (never new authority, never second
# Plan model, never coordinator truth). Carries only bounded Work-level
# semantics needed for task-main reasoning: Work identity + product
# objective + bounded semantic context. Full Plan body never enters model
# context. Missing usable semantics fails closed (no heuristic scope).
# ---------------------------------------------------------------------------

MAX_WORK_SEMANTIC_OBJECTIVE_LENGTH: int = 1024
MAX_WORK_SEMANTIC_CONTEXT_LENGTH: int = 2048

MAX_WORK_SOURCE_TEXT_LENGTH: int = 8192
MAX_WORK_SOURCE_TITLE_LENGTH: int = 512


@dataclass(frozen=True)
class GovernedWorkSemanticView:
    """Bounded governed Work semantic view (Plan authority projection)."""

    work_item_id: str
    milestone_id: str
    objective: str
    semantic_context: str

    def __post_init__(self) -> None:
        for label, val, bound in (
            ("work_item_id", self.work_item_id, 128),
            ("milestone_id", self.milestone_id, 128),
            ("objective", self.objective, MAX_WORK_SEMANTIC_OBJECTIVE_LENGTH),
            ("semantic_context", self.semantic_context, MAX_WORK_SEMANTIC_CONTEXT_LENGTH),
        ):
            if not isinstance(val, str) or type(val) is not str:
                raise TypeError(f"{label} must be a string")
            if not val.strip():
                raise ValueError(f"{label} must be non-empty")
            if len(val.strip()) > bound:
                raise ValueError(f"{label} exceeds maximum {bound}")
            if "\x00" in val:
                raise ValueError(f"{label} must not contain NUL")
        object.__setattr__(self, "work_item_id", self.work_item_id.strip())
        object.__setattr__(self, "milestone_id", self.milestone_id.strip())
        object.__setattr__(self, "objective", self.objective.strip())
        object.__setattr__(self, "semantic_context", self.semantic_context.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "milestone_id": self.milestone_id,
            "objective": self.objective,
            "semantic_context": self.semantic_context,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "GovernedWorkSemanticView":
        if not isinstance(data, dict):
            raise TypeError("GovernedWorkSemanticView data must be a dict")
        for req in ("work_item_id", "milestone_id", "objective", "semantic_context"):
            if req not in data:
                raise ValueError(f"missing required field: {req!r}")
        extra = set(data.keys()) - {"work_item_id", "milestone_id", "objective", "semantic_context"}
        if extra:
            raise ValueError(f"unknown field(s): {sorted(extra)}")
        return cls(
            work_item_id=data["work_item_id"],
            milestone_id=data["milestone_id"],
            objective=data["objective"],
            semantic_context=data["semantic_context"],
        )


# ---------------------------------------------------------------------------
# W4 structural Work source slice (bounded, faithful, digest-bound).
#
# Deterministic structural projection of the authoritative Plan source:
#   plan_ref / plan_digest + milestone_id + work_item_id + title + source_text
# No semantic rewriting: the slice is the original Work-local title, prose,
# lists and code blocks as bounded faithful source, truncated to
# MAX_WORK_SOURCE_TEXT_LENGTH. Sibling scope never leaks, digest binds the
# slice, and the Control Plane never synthesizes objective.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkSourceSlice:
    """Bounded faithful Work source slice (structural, not semantic)."""

    milestone_id: str
    work_item_id: str
    title: str
    source_text: str

    def __post_init__(self) -> None:
        for label, val, bound in (
            ("milestone_id", self.milestone_id, 128),
            ("work_item_id", self.work_item_id, 128),
            ("title", self.title, MAX_WORK_SOURCE_TITLE_LENGTH),
            ("source_text", self.source_text, MAX_WORK_SOURCE_TEXT_LENGTH),
        ):
            if not isinstance(val, str) or type(val) is not str:
                raise TypeError(f"{label} must be a string")
            if not val.strip():
                raise ValueError(f"{label} must be non-empty")
            if len(val.strip()) > bound:
                raise ValueError(f"{label} exceeds maximum {bound}")
            if "\x00" in val:
                raise ValueError(f"{label} must not contain NUL")
        object.__setattr__(self, "milestone_id", self.milestone_id.strip())
        object.__setattr__(self, "work_item_id", self.work_item_id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "source_text", self.source_text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "work_item_id": self.work_item_id,
            "title": self.title,
            "source_text": self.source_text,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "WorkSourceSlice":
        if not isinstance(data, dict):
            raise TypeError("WorkSourceSlice data must be a dict")
        for req in ("milestone_id", "work_item_id", "title", "source_text"):
            if req not in data:
                raise ValueError(f"missing required field: {req!r}")
        extra = set(data.keys()) - {"milestone_id", "work_item_id", "title", "source_text"}
        if extra:
            raise ValueError(f"unknown field(s): {sorted(extra)}")
        return cls(
            milestone_id=data["milestone_id"],
            work_item_id=data["work_item_id"],
            title=data["title"],
            source_text=data["source_text"],
        )


class PlanSource:
    """Port: the source of Portable Plan state (executor-neutral).

    M1 ships LegacyPlanStateReader as the only concrete read source, used
    strictly as shadow/migration evidence.
    """

    def load(self, project_root: Path, plan_id: str | None = None) -> PortablePlanSnapshot | None:
        raise NotImplementedError


class LegacyPlanStateReader(PlanSource):
    """Explicitly named legacy plan.json reader.

    Shadow/migration evidence ONLY.  It does not become Portable Plan
    authority and M1 performs no Plan mutation.
    """

    def load(self, project_root: Path, plan_id: str | None = None) -> PortablePlanSnapshot | None:
        plans_root = project_root / ".aota" / "forge" / "plans"
        if not plans_root.is_dir():
            return None
        candidates = sorted(plans_root.glob("plan_*")) if plan_id is None else [plans_root / plan_id]
        for plan_dir in candidates:
            if plan_id is None and not plan_dir.is_dir():
                continue
            plan_json = plan_dir / "plan.json"
            if plan_json.is_symlink() or not plan_json.is_file():
                continue
            try:
                if plan_json.stat().st_size > MAX_PLAN_BYTES:
                    continue
                data = json.loads(plan_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            resolved_id = str(data.get("plan_id") or plan_dir.name)
            if not PLAN_ID_RE.fullmatch(resolved_id):
                continue
            revision = int(data["revision"]) if isinstance(data.get("revision"), int) else 1
            status = str(data.get("status", "unknown"))
            snapshot = PortablePlanSnapshot(
                plan_id=resolved_id,
                revision=revision,
                sha256=snapshot_sha256({"plan_id": resolved_id, "revision": revision, "status": status}),
                status=status,
                current_milestone=str(data["current_milestone"]) if data.get("current_milestone") else None,
                milestones=_milestones_from(data.get("milestones")),
                source="legacy_shadow",
            )
            return snapshot
        return None


def _milestones_from(raw: Any) -> list[MilestoneSnapshot]:
    milestones: list[MilestoneSnapshot] = []
    if not isinstance(raw, dict):
        return milestones
    for milestone_id, value in raw.items():
        if not isinstance(milestone_id, str):
            continue
        status = "unknown"
        work_items: list[WorkItemSnapshot] = []
        if isinstance(value, dict):
            status = str(value.get("status", "unknown"))
            for item in value.get("work_items", []) if isinstance(value.get("work_items"), list) else []:
                if isinstance(item, dict) and isinstance(item.get("work_item_id"), str):
                    work_items.append(
                        WorkItemSnapshot(
                            work_item_id=item["work_item_id"],
                            status=str(item.get("status", "unknown")),
                            milestone_id=milestone_id,
                            title=str(item["title"]) if item.get("title") else None,
                        )
                    )
        milestones.append(MilestoneSnapshot(milestone_id=milestone_id, status=status, work_items=work_items))
    return sorted(milestones, key=lambda m: m.milestone_id)
