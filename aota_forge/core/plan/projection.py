"""Canonical Plan → typed Milestone projection (M3-B003).

Executor-neutral, deterministic projection from a normalized
:class:`PortablePlanDocument` to typed values consumed by the daily launcher.

The launcher itself must NOT interpret Plan semantics (M3, W1, DAG syntax,
approval rules, entry base). It asks this canonical layer:

  What is current?
  What is approved?
  What is the Work Item graph?
  What Plan authority produced this truth?

All parsing, validation, and fail-closed decisions live here under
``core/plan``.  The launcher only consumes typed results.

This module reuses the existing PortablePlanDocument produced by
``normalize_portable_plan`` and the existing ``MilestoneWorkItemGraph``
contract.  No new Plan authority system is created.

Invariants:
  * MISSING_DAG_TRUTH_FAILS_CLOSED=yes
  * AMBIGUOUS_DAG_TRUTH_FAILS_CLOSED=yes
  * MISSING_APPROVAL_TRUTH_FAILS_CLOSED=yes
  * APPROVAL_CONTRADICTION_FAIL_CLOSED=yes
  * HISTORICAL_STATE_OVERRIDE=no (enforced by normalize)
  * CURRENT_STATE_CONTRADICTION_FAIL_CLOSED=yes (enforced by normalize)
"""

from __future__ import annotations

import re
from typing import Any

from aota_forge.core.plan.normalize import PlanNormalizationError
from aota_forge.core.plan.read_model import (
    MAX_WORK_SEMANTIC_CONTEXT_LENGTH,
    MAX_WORK_SEMANTIC_OBJECTIVE_LENGTH,
    GovernedWorkSemanticView,
    PortablePlanDocument,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


def get_milestone_approval(document: PortablePlanDocument, milestone_id: str) -> bool:
    """Return explicit approval for ``milestone_id`` (fail-closed).

    * ``M<n>_USER_APPROVAL_SATISFIED=yes`` => True
    * ``M<n>_USER_APPROVAL_SATISFIED=no``  => False
    * missing / malformed / ambiguous => raise PlanNormalizationError
    * contradictory (yes+no) already raised during normalize
    """
    if not isinstance(milestone_id, str) or not milestone_id.strip():
        raise PlanNormalizationError("MALFORMED_MILESTONE_ID", f"milestone_id {milestone_id!r} invalid")
    if milestone_id not in document.milestone_approvals:
        raise PlanNormalizationError(
            "MISSING_APPROVAL_TRUTH",
            f"approval for {milestone_id!r} missing: {milestone_id}_USER_APPROVAL_SATISFIED not in authoritative current state",
        )
    val = document.milestone_approvals[milestone_id]
    if not isinstance(val, bool):
        raise PlanNormalizationError("MALFORMED_APPROVAL_VALUE", f"approval for {milestone_id!r} must be bool")
    return val


def get_entry_base(document: PortablePlanDocument) -> str:
    """Return canonical entry base (fail-closed if missing)."""
    if document.entry_base is None or not str(document.entry_base).strip():
        raise PlanNormalizationError("MISSING_ENTRY_BASE", "ENTRY_BASE missing in authoritative current state")
    v = str(document.entry_base).strip()
    if len(v) < 7:
        raise PlanNormalizationError("MALFORMED_ENTRY_BASE", f"ENTRY_BASE {v!r} too short")
    return v


def get_milestone_graph(document: PortablePlanDocument, milestone_id: str) -> MilestoneWorkItemGraph:
    """Return MilestoneWorkItemGraph for ``milestone_id`` (fail-closed).

    Requires that the Plan contain an explicit DAG/work-items spec for that
    milestone.  Missing or ambiguous DAG fails closed; no fallback to
    single W1 or heading-order.
    """
    if not isinstance(milestone_id, str) or not milestone_id.strip():
        raise PlanNormalizationError("MALFORMED_MILESTONE_ID", f"milestone_id {milestone_id!r} invalid")
    work_items = document.milestone_work_items.get(milestone_id)
    dependencies = document.milestone_dependencies.get(milestone_id)
    dag_raw = document.milestone_dag_raw.get(milestone_id)

    if work_items is None:
        raise PlanNormalizationError(
            "MISSING_DAG_TRUTH",
            f"Work Items for {milestone_id!r} missing: no {milestone_id}_DAG or {milestone_id}_WORK_ITEMS in Plan",
        )
    if dependencies is None:
        # No dependencies entry but work items exist => ambiguous unless explicitly empty
        # If dag_raw is None but work_items exist via WORK_ITEMS, then dependencies is () (explicit independent)
        # That case is already handled: dependencies would be () not None. So None means truly missing.
        raise PlanNormalizationError("MISSING_DAG_TRUTH", f"Dependencies for {milestone_id!r} missing")

    # Validate non-empty work_items
    if len(work_items) == 0:
        raise PlanNormalizationError("MISSING_DAG_TRUTH", f"Work Items for {milestone_id!r} empty")

    # Ambiguous check: DAG string that produced no dependencies but work items exist? That's allowed for independent set?
    # For generic proof, empty dependencies is valid (independent). But ambiguous DAG that failed to parse already raised during normalize.
    # Here we just construct the graph; MilestoneWorkItemGraph will validate acyclic, unknown deps, etc.
    try:
        graph = MilestoneWorkItemGraph(
            milestone_ref=milestone_id,
            work_items=list(work_items),
            dependencies=[list(e) for e in dependencies],
        )
    except Exception as exc:
        raise PlanNormalizationError("AMBIGUOUS_DAG", f"DAG for {milestone_id!r} ambiguous/invalid: {exc}") from exc
    return graph


def get_current_milestone_id(document: PortablePlanDocument) -> str:
    """Return current milestone id (fail-closed if missing/malformed)."""
    if document.current_milestone is None or not str(document.current_milestone).strip():
        raise PlanNormalizationError("MISSING_CURRENT_MILESTONE", "CURRENT_MILESTONE missing in authoritative current state")
    return str(document.current_milestone).strip()


def get_next_milestone_id(document: PortablePlanDocument) -> str | None:
    """Derive next milestone id from Plan authority (not numeric increment).

    Uses sorted milestone_specs keys.  The next milestone after current is the
    successor in sorted order.  If no successor, returns None.  This satisfies
    NEXT_MILESTONE_FROM_PLAN_AUTHORITY=yes and avoids M3->M4 numeric assumption
    when no M4 is defined.
    """
    current = get_current_milestone_id(document)
    # Collect all milestone ids from specs and execution specs
    all_mids = set(document.milestone_specs.keys()) | set(document.milestone_work_items.keys())
    # Also include milestone_status keys and approvals
    all_mids |= set(document.milestone_status.keys())
    all_mids |= set(document.milestone_approvals.keys())
    # Sort by numeric value
    def _num(mid: str) -> int:
        m = re.fullmatch(r"M([0-9]+)", mid)
        return int(m.group(1)) if m else 10**9

    sorted_mids = sorted(all_mids, key=_num)
    if current not in sorted_mids:
        # Current not in spec list => no next (fail-closed? but for now return None)
        # Could also try to infer via DAG milestone graph, but keep simple
        return None
    idx = sorted_mids.index(current)
    if idx + 1 < len(sorted_mids):
        return sorted_mids[idx + 1]
    return None


def _work_line_matches(line: str, milestone_id: str, work_item_id: str) -> bool:
    """Generic Work-mention test (no product literals).

    Matches when the line contains the governed Work identity as:
    - {MID}/{WID} (e.g. M3/W1), {MID}_{WID}, {MID}-{WID}, or
    - WID as an exact token (split on non [A-Za-z0-9._-]).
    W1 does not match W10 (exact token, not substring).
    """
    if not isinstance(line, str) or not line:
        return False
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        return False
    wid = work_item_id.strip()
    mid = (milestone_id or "").strip()
    if mid and (f"{mid}/{wid}" in line or f"{mid}_{wid}" in line or f"{mid}-{wid}" in line):
        return True
    try:
        tokens = re.split(r"[^A-Za-z0-9._-]+", line)
    except Exception:
        return wid in line
    return wid in tokens


def get_governed_work_semantic_view(
    document: PortablePlanDocument, milestone_id: str, work_item_id: str
) -> GovernedWorkSemanticView:
    """Bounded governed Work view from canonical Plan authority (F2).

    Sources (generic, no dogfood literals):
    - milestone_section_prose[mid] (title + per-Work KV fragments + free prose)
    Work-specific lines are those mentioning the governed Work ID (see
    _work_line_matches). Objective is the first such line; context is title
    plus Work lines, bounded. Missing usable semantics fails closed with
    MISSING_WORK_SEMANTICS (no heuristic "Implement W1").
    """
    if not isinstance(milestone_id, str) or not milestone_id.strip():
        raise PlanNormalizationError("MALFORMED_MILESTONE_ID", "milestone_id invalid")
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        raise PlanNormalizationError("MALFORMED_WORK_ITEM_ID", "work_item_id invalid")
    mid = milestone_id.strip()
    wid = work_item_id.strip()
    work_items = document.milestone_work_items.get(mid)
    if work_items is None or wid not in set(work_items):
        raise PlanNormalizationError(
            "UNKNOWN_WORK_ITEM",
            f"Work Item {wid!r} is not a governed Work Item of Milestone {mid!r}",
        )
    prose = ""
    try:
        prose = (document.milestone_section_prose or {}).get(mid, "") or ""
    except Exception:
        prose = ""
    if not isinstance(prose, str) or not prose.strip():
        raise PlanNormalizationError(
            "MISSING_WORK_SEMANTICS",
            f"No governed Work semantics for {mid}/{wid}: milestone prose absent; refusing heuristic scope",
        )
    lines = [ln.strip() for ln in prose.splitlines() if ln.strip()]
    if not lines:
        raise PlanNormalizationError(
            "MISSING_WORK_SEMANTICS",
            f"No governed Work semantics for {mid}/{wid}: empty prose; refusing heuristic scope",
        )
    title = lines[0] if lines else mid
    work_lines = [ln for ln in lines if _work_line_matches(ln, mid, wid)]
    if not work_lines:
        raise PlanNormalizationError(
            "MISSING_WORK_SEMANTICS",
            f"No governed Work semantics for {mid}/{wid}: no Work-specific authority; refusing heuristic scope",
        )
    objective = work_lines[0].strip()[:MAX_WORK_SEMANTIC_OBJECTIVE_LENGTH].strip()
    if not objective:
        raise PlanNormalizationError(
            "MISSING_WORK_SEMANTICS", f"Empty objective for {mid}/{wid}; refusing heuristic scope"
        )
    ctx_parts = [title] + work_lines
    context = "\n".join(ctx_parts).strip()
    if len(context) > MAX_WORK_SEMANTIC_CONTEXT_LENGTH:
        context = context[:MAX_WORK_SEMANTIC_CONTEXT_LENGTH].strip()
    if not context:
        raise PlanNormalizationError(
            "MISSING_WORK_SEMANTICS", f"Empty context for {mid}/{wid}; refusing heuristic scope"
        )
    try:
        return GovernedWorkSemanticView(
            work_item_id=wid, milestone_id=mid, objective=objective, semantic_context=context
        )
    except Exception as exc:
        raise PlanNormalizationError("MISSING_WORK_SEMANTICS", f"Invalid view for {mid}/{wid}: {exc}") from exc


def get_milestone_work_semantic_views(
    document: PortablePlanDocument, milestone_id: str
) -> tuple[GovernedWorkSemanticView, ...]:
    """Best-effort views for all governed Works (skip missing, no fail).

    Used by project_milestone_views to populate MilestonePlanView. Missing
    per-Work semantics are skipped here; the control projector reports
    per-ready-Work missing as typed fail-closed (no heuristic).
    """
    try:
        work_items = document.milestone_work_items.get(milestone_id.strip(), ())
    except Exception:
        return ()
    if not work_items:
        return ()
    out: list[GovernedWorkSemanticView] = []
    for wid in work_items:
        try:
            out.append(get_governed_work_semantic_view(document, milestone_id, wid))
        except PlanNormalizationError:
            continue
        except Exception:
            continue
    return tuple(out)


def project_milestone_views(
    document: PortablePlanDocument,
    *,
    plan_authority: str,
    plan_digest: str | None = None,
    plan_source_revision: str | None = None,
) -> tuple[Any, Any | None]:
    """Project live and next MilestonePlanViews from canonical document.

    This is the sole authority for DAG, approval, entry base, and next milestone.
    The launcher must call this and not re-parse Plan text.

    Returns (live_view, next_view) where next_view may be None.
    Raises PlanNormalizationError on missing/ambiguous truth (fail-closed).
    """
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView

    current_id = get_current_milestone_id(document)
    # Approval, graph, entry_base are all fail-closed
    approved = get_milestone_approval(document, current_id)
    graph = get_milestone_graph(document, current_id)
    entry_base = get_entry_base(document)

    # Resolve plan_digest / source_revision if not supplied
    if plan_digest is None:
        from aota_forge.core.plan.read_model import portable_plan_digest

        plan_digest = portable_plan_digest(document)
    if plan_source_revision is None:
        plan_source_revision = document.source_revision or document.source_digest

    if not isinstance(plan_authority, str) or not plan_authority.strip():
        raise PlanNormalizationError("MISSING_PLAN_AUTHORITY", "plan_authority must be non-empty")
    if len(plan_authority) > 512:
        raise PlanNormalizationError("MALFORMED_PLAN_AUTHORITY", "plan_authority too long")

    live_view = MilestonePlanView(
        plan_authority=plan_authority.strip(),
        plan_digest=plan_digest,
        plan_source_revision=plan_source_revision,
        milestone_id=current_id,
        entry_base=entry_base,
        graph=graph,
        milestone_user_approval_satisfied=approved,
        plan_amendment_required=False,
        work_semantics=get_milestone_work_semantic_views(document, current_id),
    )

    next_id = get_next_milestone_id(document)
    next_view = None
    if next_id is not None:
        # Next approval: fail-closed? For next milestone, we treat missing approval as not approved (USER_GATE_REQUIRED)
        # But spec says approval must be explicit for current milestone; for next, we should also require explicit?
        # Here we check if next has explicit approval; if missing, we treat as not approved (gate), not fail-closed.
        # However, if next has contradictory approval, it would have already failed during normalize.
        next_approved = document.milestone_approvals.get(next_id, False)
        # Need graph for next milestone: if missing, next_view is None (truthfully no next)
        try:
            next_graph = get_milestone_graph(document, next_id)
        except PlanNormalizationError:
            # No execution spec for next milestone => next_view None (not an error, just no next)
            # But if next milestone exists in specs but has no graph, we treat as missing => None
            next_view = None
            return live_view, next_view
        next_view = MilestonePlanView(
            plan_authority=plan_authority.strip(),
            plan_digest=plan_digest,
            plan_source_revision=plan_source_revision,
            milestone_id=next_id,
            entry_base=entry_base,
            graph=next_graph,
            milestone_user_approval_satisfied=bool(next_approved),
            plan_amendment_required=False,
            work_semantics=get_milestone_work_semantic_views(document, next_id),
        )
    return live_view, next_view
