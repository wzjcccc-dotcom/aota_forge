"""Pure Plan validation / hash primitives (M1-C).

Deterministic, executor-neutral validation helpers; safe to extract.
"""

from __future__ import annotations

import re
from typing import Any

PLAN_ID_RE = re.compile(r"^plan_[a-zA-Z0-9]+(?:[_-][a-zA-Z0-9]+)*$")
MILESTONE_ID_RE = re.compile(r"^M[0-9]+$")
WORK_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


def is_plan_id(value: Any) -> bool:
    return isinstance(value, str) and bool(PLAN_ID_RE.fullmatch(value))


def is_milestone_id(value: Any) -> bool:
    return isinstance(value, str) and bool(MILESTONE_ID_RE.fullmatch(value))


def is_work_item_id(value: Any) -> bool:
    return isinstance(value, str) and bool(WORK_ITEM_ID_RE.fullmatch(value))


def is_status_value(value: Any) -> bool:
    return isinstance(value, str) and value in {"planned", "in-progress", "completed", "blocked"}
