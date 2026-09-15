"""Pure Plan validation / hash primitives (M1-C).

Deterministic, executor-neutral validation helpers; safe to extract.

AF #57 M1/W1: this module is the single canonical source-level owner of the
new-Plan ID grammar.  A canonical Plan ID is the internal stable Plan identity:
lowercase, path-safe, and stable within one project:

    ^plan_[a-z0-9]+(?:[_-][a-z0-9]+)*$

Legacy Plan IDs (for example the timestamped
``plan_20260729T075202_ff38a6a0`` shadow evidence) are NOT canonical.  They
are never silently normalized into canonical IDs; bounded read compatibility
for already accepted legacy artifacts lives only in
``aota_forge.core.plan.read_model`` (``LegacyPlanStateReader`` /
``LEGACY_PLAN_ID_RE``).

ISSUE_NUMBER_IS_PLAN_ID=no
FOLDER_NAME_IS_PLAN_ID_AUTHORITY=no
"""

from __future__ import annotations

import re
from typing import Any

# The one canonical new-Plan ID grammar.  Do not introduce a competing regex.
PLAN_ID_RE = re.compile(r"^plan_[a-z0-9]+(?:[_-][a-z0-9]+)*$")
MILESTONE_ID_RE = re.compile(r"^M[0-9]+$")
WORK_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


def is_plan_id(value: Any) -> bool:
    """True only for the one canonical new-Plan ID grammar."""
    return isinstance(value, str) and bool(PLAN_ID_RE.fullmatch(value))


def is_milestone_id(value: Any) -> bool:
    return isinstance(value, str) and bool(MILESTONE_ID_RE.fullmatch(value))


def is_work_item_id(value: Any) -> bool:
    return isinstance(value, str) and bool(WORK_ITEM_ID_RE.fullmatch(value))


def is_status_value(value: Any) -> bool:
    return isinstance(value, str) and value in {"planned", "in-progress", "completed", "blocked"}
