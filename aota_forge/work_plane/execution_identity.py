"""AF #57 M1/W4 — canonical Plan-aware execution identity.

The frozen logical identity chain (Governance 2.0 baseline) is:

    PROJECT_ID -> PLAN_ID -> MILESTONE_ID -> WORK_ITEM_ID -> EXECUTION_ATTEMPT_ID

Canonical runtime task identity for new work embeds the internal Plan
identity at an explicit position (index 1, immediately after the project
segment):

    {project_id}:{plan_id}:{milestone_id}:{work_item_id}:{tail...}

``tail`` is producer-specific and is carried opaquely:

    <artifact8>:<uuid8>   task_facade worker dispatch identity
    attempt-<n>           deterministic dispatch identity

The Plan segment is the canonical internal Plan ID (``plan_...`` grammar is
owned by ``aota_forge.core.plan.validation``).  A GitHub Issue number, a
folder name, a branch name, a repository name or a display title is never a
Plan identity:

    CANONICAL_TASK_ID_PLAN_POSITION_EXPLICIT=yes
    PLAN_ID_POSITION=1
    PLAN_ID_SILENT_INFERENCE=no

Legacy (Governance 1.x) task identities minted before W4 carry no Plan
segment, for example:

    {project}:{milestone}:{work}:attempt-{n}
    {project}:{milestone}:{work}:{artifact8}:{uuid8}

They stay readable through ``parse_task_identity_bounded`` and are explicitly
classified ``legacy_plan_less``; no fake Plan ID is injected and no legacy
identity is silently upgraded:

    LEGACY_TASK_IDENTITY_READ_COMPATIBLE=yes
    LEGACY_TASK_IDENTITY_SILENT_PLAN_INJECTION=no

Structural discrimination is deterministic, not heuristic: the segment at
``PLAN_ID_POSITION`` is a Plan segment exactly when it matches the canonical
internal Plan ID grammar.  A legacy identity carries its Milestone at that
position and the frozen Milestone identity (``M<n>``) can never match the
Plan ID grammar.

New-work worktree / branch identity (applies prospectively after W4
acceptance; existing worktrees and branches are never renamed):

    <workspace>/.aota-worktrees/<project-id>/<plan-id>/<milestone-id>/<work-item-or-lane>/
    aota/<plan-id>/<milestone-id>/<work-item-or-lane>

The physical worktree/branch layout remains operator/governance convention:
this module owns the deterministic identity segments only, performs no
filesystem or Git effect, and decides no policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.plan.validation import is_milestone_id, is_plan_id

TASK_IDENTITY_SEPARATOR = ":"
PLAN_ID_POSITION = 1
PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS = 5
LEGACY_TASK_IDENTITY_MIN_SEGMENTS = 4
MAX_TASK_IDENTITY_SEGMENT_LENGTH = 256

TASK_IDENTITY_INVALID = "TASK_IDENTITY_INVALID"
TASK_IDENTITY_MALFORMED = "TASK_IDENTITY_MALFORMED"
TASK_IDENTITY_PLAN_MISMATCH = "TASK_IDENTITY_PLAN_MISMATCH"
TASK_IDENTITY_PLAN_POSITION_NOT_EXPLICIT = "TASK_IDENTITY_PLAN_POSITION_NOT_EXPLICIT"

# The canonical task identity always names the Plan at this explicit position.
CANONICAL_TASK_ID_PLAN_POSITION_EXPLICIT = True
# A missing Plan segment is never inferred/defaulted; legacy identities stay
# explicitly plan-less instead of receiving a fabricated Plan identity.
PLAN_ID_SILENT_INFERENCE = False

# New-work identity conventions (prospective; operator/governance convention).
NEW_WORKTREE_ROOT_SEGMENT = ".aota-worktrees"
NEW_WORKTREE_LAYOUT = (
    "<workspace>/.aota-worktrees/<project-id>/<plan-id>/"
    "<milestone-id>/<work-item-or-lane>/"
)
NEW_BRANCH_LAYOUT = "aota/<plan-id>/<milestone-id>/<work-item-or-lane>"
NEW_WORKTREE_IDENTITY_INCLUDES_PLAN = True
NEW_BRANCH_IDENTITY_INCLUDES_PLAN = True
LEGACY_WORKTREE_MASS_MIGRATION = False
LEGACY_WORKTREE_READ_COMPATIBLE = True
LEGACY_BRANCH_RENAME_REQUIRED = False

_WORK_LANE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TaskIdentityError(ValueError):
    """Bounded canonical execution identity error (fail closed)."""

    code = TASK_IDENTITY_INVALID


class MalformedTaskIdentityError(TaskIdentityError):
    """The canonical task identity text is structurally malformed."""

    code = TASK_IDENTITY_MALFORMED


class TaskIdentityPlanMismatchError(TaskIdentityError):
    """The task identity does not carry the expected Plan-bound identity."""

    code = TASK_IDENTITY_PLAN_MISMATCH


def _require_bounded_segment(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedTaskIdentityError(f"{label} must be a non-empty string")
    segment = value.strip()
    if len(segment) > MAX_TASK_IDENTITY_SEGMENT_LENGTH:
        raise MalformedTaskIdentityError(f"{label} exceeds the bounded segment length")
    if "\x00" in segment or TASK_IDENTITY_SEPARATOR in segment:
        raise MalformedTaskIdentityError(f"{label} must be a bounded identity segment")
    return segment


def _require_milestone_segment(value: Any) -> str:
    segment = _require_bounded_segment(value, "milestone_id")
    if not is_milestone_id(segment):
        raise MalformedTaskIdentityError(
            f"milestone_id must be one canonical Milestone identity M<n>, got {segment!r}"
        )
    return segment


@dataclass(frozen=True)
class CanonicalTaskIdentity:
    """One canonical Plan-aware execution identity (new work)."""

    project_id: str
    plan_id: str
    milestone_id: str
    work_item_id: str
    tail: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_bounded_segment(self.project_id, "project_id"))
        if not is_plan_id(self.plan_id):
            raise MalformedTaskIdentityError(
                "plan_id must be one canonical internal Plan ID at the explicit Plan position"
            )
        object.__setattr__(self, "milestone_id", _require_bounded_segment(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "work_item_id", _require_bounded_segment(self.work_item_id, "work_item_id"))
        cleaned: list[str] = []
        for index, token in enumerate(self.tail):
            cleaned.append(_require_bounded_segment(token, f"tail[{index}]"))
        object.__setattr__(self, "tail", tuple(cleaned))

    @property
    def plan_position(self) -> int:
        return PLAN_ID_POSITION

    def to_canonical(self) -> str:
        return TASK_IDENTITY_SEPARATOR.join(
            (self.project_id, self.plan_id, self.milestone_id, self.work_item_id, *self.tail)
        )


@dataclass(frozen=True)
class BoundedTaskIdentity:
    """One structurally bounded task identity, plan-aware or explicitly legacy.

    ``plan_id`` is ``None`` exactly for the bounded legacy plan-less form; it
    is never synthesized.
    """

    project_id: str
    milestone_id: str
    work_item_id: str
    tail: tuple[str, ...] = field(default_factory=tuple)
    plan_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_bounded_segment(self.project_id, "project_id"))
        if self.plan_id is not None and not is_plan_id(self.plan_id):
            raise MalformedTaskIdentityError("plan_id must be one canonical internal Plan ID when present")
        object.__setattr__(self, "milestone_id", _require_bounded_segment(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "work_item_id", _require_bounded_segment(self.work_item_id, "work_item_id"))
        cleaned: list[str] = []
        for index, token in enumerate(self.tail):
            cleaned.append(_require_bounded_segment(token, f"tail[{index}]"))
        object.__setattr__(self, "tail", tuple(cleaned))

    @property
    def plan_aware(self) -> bool:
        return self.plan_id is not None

    @property
    def legacy_plan_less(self) -> bool:
        return self.plan_id is None

    def to_canonical(self) -> str:
        if self.plan_id is None:
            return TASK_IDENTITY_SEPARATOR.join(
                (self.project_id, self.milestone_id, self.work_item_id, *self.tail)
            )
        return TASK_IDENTITY_SEPARATOR.join(
            (self.project_id, self.plan_id, self.milestone_id, self.work_item_id, *self.tail)
        )


def format_plan_aware_task_id(
    *,
    project_id: Any,
    plan_id: Any,
    milestone_id: Any,
    work_item_id: Any,
    tail: tuple[Any, ...] = (),
) -> str:
    """Build the canonical Plan-aware task identity string (new work)."""
    return CanonicalTaskIdentity(
        project_id=project_id,
        plan_id=plan_id,
        milestone_id=milestone_id,
        work_item_id=work_item_id,
        tail=tuple(tail),
    ).to_canonical()


def parse_plan_aware_task_identity(text: Any) -> CanonicalTaskIdentity:
    """Strictly parse a canonical Plan-aware task identity (fail closed).

    A missing Plan segment, a non-canonical Plan segment, an empty segment or
    too few segments all fail closed; a legacy plan-less identity is never
    silently accepted here.
    """
    if not isinstance(text, str) or not text:
        raise MalformedTaskIdentityError("task identity must be a non-empty string")
    parts = text.split(TASK_IDENTITY_SEPARATOR)
    if len(parts) < PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS:
        raise MalformedTaskIdentityError(
            "canonical task identity requires at least "
            f"{PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS} segments "
            f"(project:plan:milestone:work:tail), got {len(parts)}"
        )
    if not is_plan_id(parts[PLAN_ID_POSITION]):
        raise MalformedTaskIdentityError(
            f"canonical task identity Plan segment at position {PLAN_ID_POSITION} "
            f"must be one canonical internal Plan ID, got {parts[PLAN_ID_POSITION]!r}"
        )
    return CanonicalTaskIdentity(
        project_id=parts[0],
        plan_id=parts[PLAN_ID_POSITION],
        milestone_id=parts[PLAN_ID_POSITION + 1],
        work_item_id=parts[PLAN_ID_POSITION + 2],
        tail=tuple(parts[PLAN_ID_POSITION + 3 :]),
    )


def parse_task_identity_bounded(text: Any) -> BoundedTaskIdentity:
    """Parse a task identity with explicit bounded legacy compatibility.

    The Plan position is explicit: a segment at ``PLAN_ID_POSITION`` is a Plan
    segment exactly when it matches the canonical internal Plan ID grammar.
    Otherwise the identity is the bounded legacy plan-less form (the segment
    is the Milestone).  This never fabricates a Plan identity.
    """
    if not isinstance(text, str) or not text:
        raise MalformedTaskIdentityError("task identity must be a non-empty string")
    parts = text.split(TASK_IDENTITY_SEPARATOR)
    if len(parts) < LEGACY_TASK_IDENTITY_MIN_SEGMENTS:
        raise MalformedTaskIdentityError(
            "bounded task identity requires at least "
            f"{LEGACY_TASK_IDENTITY_MIN_SEGMENTS} segments "
            f"(project:milestone:work:tail), got {len(parts)}"
        )
    plan_id = parts[PLAN_ID_POSITION] if is_plan_id(parts[PLAN_ID_POSITION]) else None
    if plan_id is not None and len(parts) < PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS:
        raise MalformedTaskIdentityError(
            "Plan-aware task identity requires at least "
            f"{PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS} segments "
            f"(project:plan:milestone:work:tail), got {len(parts)}"
        )
    milestone_index = PLAN_ID_POSITION + 1 if plan_id is not None else PLAN_ID_POSITION
    if len(parts) < milestone_index + 2:
        raise MalformedTaskIdentityError(
            "bounded task identity does not carry milestone/work segments"
        )
    return BoundedTaskIdentity(
        project_id=parts[0],
        plan_id=plan_id,
        milestone_id=parts[milestone_index],
        work_item_id=parts[milestone_index + 1],
        tail=tuple(parts[milestone_index + 2 :]),
    )


def is_plan_aware_task_identity(text: Any) -> bool:
    """True only for a strictly valid canonical Plan-aware task identity."""
    try:
        parse_plan_aware_task_identity(text)
        return True
    except TaskIdentityError:
        return False


def require_task_identity_matches(
    text: Any,
    *,
    plan_id: Any,
    milestone_id: Any = None,
    work_item_id: Any = None,
    project_id: Any = None,
) -> CanonicalTaskIdentity:
    """Fail closed unless ``text`` carries the expected Plan-bound identity."""
    identity = parse_plan_aware_task_identity(text)
    if not is_plan_id(plan_id) or identity.plan_id != plan_id:
        raise TaskIdentityPlanMismatchError(
            f"task identity Plan segment {identity.plan_id!r} does not match the "
            f"expected Plan identity {plan_id!r}"
        )
    if project_id is not None and identity.project_id != str(project_id):
        raise TaskIdentityPlanMismatchError(
            f"task identity project {identity.project_id!r} does not match the "
            f"expected project {project_id!r}"
        )
    if milestone_id is not None and identity.milestone_id != str(milestone_id):
        raise TaskIdentityPlanMismatchError(
            f"task identity Milestone {identity.milestone_id!r} does not match the "
            f"expected Milestone {milestone_id!r}"
        )
    if work_item_id is not None and identity.work_item_id != str(work_item_id):
        raise TaskIdentityPlanMismatchError(
            f"task identity Work Item {identity.work_item_id!r} does not match the "
            f"expected Work Item {work_item_id!r}"
        )
    return identity


# ---------------------------------------------------------------------------
# New-work worktree / branch identity segments (operator/governance convention)
# ---------------------------------------------------------------------------


def _require_work_lane(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskIdentityError("work-item-or-lane must be a non-empty string")
    lane = value.strip()
    if not _WORK_LANE_RE.fullmatch(lane) or lane in (".", "..") or ".." in lane:
        raise TaskIdentityError(
            "work-item-or-lane must be a bounded path-safe identity segment"
        )
    return lane


def new_work_worktree_segments(
    *,
    project_id: Any,
    plan_id: Any,
    milestone_id: Any,
    work_item_or_lane: Any,
) -> tuple[str, ...]:
    """Canonical new-work worktree identity segments (identity only)."""
    project = _require_bounded_segment(project_id, "project_id")
    if not is_plan_id(plan_id):
        raise TaskIdentityError("plan_id must be one canonical internal Plan ID")
    milestone = _require_milestone_segment(milestone_id)
    lane = _require_work_lane(work_item_or_lane)
    return (project, plan_id, milestone, lane)


def new_work_worktree_relpath(
    *,
    project_id: Any,
    plan_id: Any,
    milestone_id: Any,
    work_item_or_lane: Any,
) -> str:
    """Canonical new-work worktree path relative to the workspace root."""
    segments = new_work_worktree_segments(
        project_id=project_id,
        plan_id=plan_id,
        milestone_id=milestone_id,
        work_item_or_lane=work_item_or_lane,
    )
    return "/".join((NEW_WORKTREE_ROOT_SEGMENT, *segments))


def new_work_branch_name(
    *,
    plan_id: Any,
    milestone_id: Any,
    work_item_or_lane: Any,
) -> str:
    """Canonical new-work branch name (a Git branch already belongs to one repo)."""
    if not is_plan_id(plan_id):
        raise TaskIdentityError("plan_id must be one canonical internal Plan ID")
    milestone = _require_milestone_segment(milestone_id)
    lane = _require_work_lane(work_item_or_lane)
    return f"aota/{plan_id}/{milestone}/{lane}"


__all__ = [
    "TASK_IDENTITY_SEPARATOR",
    "PLAN_ID_POSITION",
    "PLAN_AWARE_TASK_IDENTITY_MIN_SEGMENTS",
    "LEGACY_TASK_IDENTITY_MIN_SEGMENTS",
    "MAX_TASK_IDENTITY_SEGMENT_LENGTH",
    "TASK_IDENTITY_INVALID",
    "TASK_IDENTITY_MALFORMED",
    "TASK_IDENTITY_PLAN_MISMATCH",
    "TASK_IDENTITY_PLAN_POSITION_NOT_EXPLICIT",
    "CANONICAL_TASK_ID_PLAN_POSITION_EXPLICIT",
    "PLAN_ID_SILENT_INFERENCE",
    "NEW_WORKTREE_ROOT_SEGMENT",
    "NEW_WORKTREE_LAYOUT",
    "NEW_BRANCH_LAYOUT",
    "NEW_WORKTREE_IDENTITY_INCLUDES_PLAN",
    "NEW_BRANCH_IDENTITY_INCLUDES_PLAN",
    "LEGACY_WORKTREE_MASS_MIGRATION",
    "LEGACY_WORKTREE_READ_COMPATIBLE",
    "LEGACY_BRANCH_RENAME_REQUIRED",
    "TaskIdentityError",
    "MalformedTaskIdentityError",
    "TaskIdentityPlanMismatchError",
    "CanonicalTaskIdentity",
    "BoundedTaskIdentity",
    "format_plan_aware_task_id",
    "parse_plan_aware_task_identity",
    "parse_task_identity_bounded",
    "is_plan_aware_task_identity",
    "require_task_identity_matches",
    "new_work_worktree_segments",
    "new_work_worktree_relpath",
    "new_work_branch_name",
]
