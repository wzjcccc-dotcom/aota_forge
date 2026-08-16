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
    """Canonical normalized Portable Plan document (M2-D).

    Built exclusively from the authoritative issue body by
    ``aota_forge.core.plan.normalize.normalize_portable_plan``.

    Executor-private IDs are NOT part of this schema; raw body KEY=VALUE
    observations are kept separately under ``current_fields`` for bounded
    diagnostics, never as typed schema fields.
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

    def canonical_dict(self) -> dict[str, Any]:
        """Digest payload: everything except the derived digest itself."""
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
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["source_digest"] = self.source_digest
        payload["diagnostics"] = list(self.diagnostics)
        return payload


def portable_plan_digest(document: PortablePlanDocument) -> str:
    """Deterministic digest over the canonical Portable Plan payload."""
    return snapshot_sha256(document.canonical_dict())


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
