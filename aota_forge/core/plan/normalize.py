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
MILESTONE_DAG_KEY_RE = re.compile(r"^(M[0-9]+)_DAG$")
MILESTONE_WORK_ITEMS_KEY_RE = re.compile(r"^(M[0-9]+)_WORK_ITEMS$")
MILESTONE_APPROVAL_KEY_RE = re.compile(r"^(M[0-9]+)_USER_APPROVAL_SATISFIED$")
ENTRY_BASE_KEY = "ENTRY_BASE"

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
        ENTRY_BASE_KEY,
        "PLAN_ACCEPTED_FRONTIER",
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
                if (
                    key in CANONICAL_STATE_KEYS
                    or key.startswith("CURRENT_")
                    or MILESTONE_STATUS_KEY_RE.fullmatch(key)
                    or MILESTONE_APPROVAL_KEY_RE.fullmatch(key)
                    or MILESTONE_DAG_KEY_RE.fullmatch(key)
                    or MILESTONE_WORK_ITEMS_KEY_RE.fullmatch(key)
                    or key == ENTRY_BASE_KEY
                ):
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
            if (
                key in CANONICAL_STATE_KEYS
                or MILESTONE_STATUS_KEY_RE.fullmatch(key)
                or MILESTONE_APPROVAL_KEY_RE.fullmatch(key)
                or MILESTONE_DAG_KEY_RE.fullmatch(key)
                or MILESTONE_WORK_ITEMS_KEY_RE.fullmatch(key)
                or key == ENTRY_BASE_KEY
            ):
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


# ---------------------------------------------------------------------------
# DAG / Work Item / Approval parsing (M3-B003 canonical)
# ---------------------------------------------------------------------------

DAG_ARROW_RE = re.compile(r"\s*(?:→|->|—>|-->)\s*")
# Work item id pattern is permissive: alphanum + dash/underscore, but must be bounded
_WORK_ITEM_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _normalize_dag_string(raw: str) -> str:
    """Normalize unicode arrows and whitespace for DAG parsing."""
    s = raw.replace("→", "->").replace("—>", "->").replace("-->", "->").replace("—", "->")
    return s


def _parse_work_items_value(value: str) -> tuple[str, ...]:
    """Parse a work-items list value like 'A, B, C' or 'DISCOVER BUILD'."""
    if not isinstance(value, str) or not value.strip():
        return ()
    # Split on commas, semicolons, pipes, whitespace
    tokens = re.split(r"[\s,;|]+", value.strip())
    items: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        tok = tok.strip().strip("()[]{}")
        if not tok:
            continue
        # Remove possible arrows inside? Should not be here
        if "->" in tok or "→" in tok:
            continue
        if tok in ("||", "->", "→"):
            continue
        if tok.startswith("RV"):
            continue
        if not _WORK_ITEM_TOKEN_RE.fullmatch(tok):
            # Allow any bounded token for generic fixtures (e.g., DISCOVER)
            if not re.fullmatch(r"^[A-Za-z0-9]+$", tok):
                continue
        if tok not in seen:
            seen.add(tok)
            items.append(tok)
    return tuple(sorted(items))


def _parse_dag_string(raw: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Parse a DAG string into (work_items, dependencies).

    Supports:
      * single edge: 'A -> C'
      * comma list: 'A -> C, B -> C'
      * chain: 'DISCOVER -> BUILD -> VERIFY -> SHIP' => 3 edges
      * staged parallel: 'W0 -> (W1 || W2) -> W3' => 4 edges
    Returns sorted canonical tuples. Raises PlanNormalizationError on malformed/ambiguous.
    """
    s = _normalize_dag_string(raw).strip()
    if not s:
        return (), ()
    # Detect staged parallel syntax: contains '->' and '||' or parentheses
    if "||" in s or "(" in s or ")" in s:
        # Staged parsing: split by '->' into stages, each stage may have parallel nodes
        # Remove parentheses first
        s_clean = s.replace("(", " ").replace(")", " ")
        # Split by '->'
        stages_raw = re.split(r"\s*->\s*", s_clean)
        stages: list[list[str]] = []
        for stage_raw in stages_raw:
            # Split stage by '||' or ',' or ';'
            nodes = re.split(r"\s*(?:\|\||[,;])\s*", stage_raw.strip())
            clean_nodes: list[str] = []
            for n in nodes:
                n = n.strip()
                if not n:
                    continue
                # Further split by whitespace if still multiple tokens
                for sub in re.split(r"\s+", n):
                    sub = sub.strip()
                    if not sub or sub.startswith("RV"):
                        continue
                    if _WORK_ITEM_TOKEN_RE.fullmatch(sub):
                        clean_nodes.append(sub)
            if clean_nodes:
                # Deduplicate within stage
                uniq = []
                seen_stage: set[str] = set()
                for cn in clean_nodes:
                    if cn not in seen_stage:
                        seen_stage.add(cn)
                        uniq.append(cn)
                stages.append(uniq)
        if len(stages) < 2:
            # Fallback to simple edge parsing
            pass
        else:
            work_set: set[str] = set()
            deps: list[tuple[str, str]] = []
            for idx in range(len(stages) - 1):
                src_stage = stages[idx]
                dst_stage = stages[idx + 1]
                for src in src_stage:
                    work_set.add(src)
                    for dst in dst_stage:
                        work_set.add(dst)
                        deps.append((src, dst))
            # Filter RV nodes already, sort
            work_items = tuple(sorted(work_set))
            dependencies = tuple(sorted(set(deps)))
            return work_items, dependencies

    # Simple edge-list parsing: split by commas, semicolons, newlines
    edge_tokens = re.split(r"[\n,;]+", s)
    work_set2: set[str] = set()
    deps2: list[tuple[str, str]] = []
    for token in edge_tokens:
        token = token.strip()
        if not token:
            continue
        if "->" not in token:
            # Might be a chain without commas: e.g., 'DISCOVER -> BUILD -> VERIFY -> SHIP'
            # Already handled via split by '->' above? But this token is the whole string if no commas
            # So handle chain
            if token.count("->") >= 1:
                parts = [p.strip() for p in token.split("->") if p.strip()]
                # Clean each part: remove parentheses and || handling? Already done?
                # For simple chain, each part is single node
                for i in range(len(parts) - 1):
                    src = parts[i].strip().strip("()")
                    dst = parts[i + 1].strip().strip("()")
                    if not src or not dst or src.startswith("RV") or dst.startswith("RV"):
                        continue
                    if not _WORK_ITEM_TOKEN_RE.fullmatch(src) or not _WORK_ITEM_TOKEN_RE.fullmatch(dst):
                        continue
                    work_set2.add(src)
                    work_set2.add(dst)
                    deps2.append((src, dst))
            continue
        # Token contains '->' and may be chain
        parts = [p.strip() for p in token.split("->") if p.strip()]
        for i in range(len(parts) - 1):
            src = parts[i].strip().strip("()")
            dst = parts[i + 1].strip().strip("()")
            if not src or not dst or src.startswith("RV") or dst.startswith("RV"):
                continue
            # Handle parallel inside src/dst: e.g., 'W1 || W2'
            src_nodes = [x.strip() for x in re.split(r"\s*\|\|\s*", src) if x.strip()]
            dst_nodes = [x.strip() for x in re.split(r"\s*\|\|\s*", dst) if x.strip()]
            for s_node in src_nodes:
                for d_node in dst_nodes:
                    if not _WORK_ITEM_TOKEN_RE.fullmatch(s_node) or not _WORK_ITEM_TOKEN_RE.fullmatch(d_node):
                        continue
                    work_set2.add(s_node)
                    work_set2.add(d_node)
                    deps2.append((s_node, d_node))
    if work_set2 or deps2:
        return tuple(sorted(work_set2)), tuple(sorted(set(deps2)))
    # If no edges parsed but string non-empty and looks like work items, treat as error (ambiguous)
    # Caller will handle missing
    return (), ()


def _validate_approval_value(value: str) -> bool:
    v = value.strip().lower()
    if v in ("yes", "true", "1"):
        return True
    if v in ("no", "false", "0"):
        return False
    raise PlanNormalizationError(
        "MALFORMED_APPROVAL_VALUE",
        f"M*_USER_APPROVAL_SATISFIED value {value!r} must be 'yes' or 'no'",
    )


def _collect_milestone_execution(
    sections: list[BodySection],
    current_fields: dict[str, str],
    diagnostics: list[dict[str, Any]],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[tuple[str, str], ...]], dict[str, str], dict[str, bool]]:
    """Collect work items, dependencies, dag raw, and approvals from all sections."""
    dag_raw: dict[str, str] = {}
    work_items_raw: dict[str, str] = {}
    approvals_raw: dict[str, str] = {}

    # Scan all sections (including milestone_spec) for DAG/WORK_ITEMS/APPROVAL keys
    for section in sections:
        for key, value in section.key_values.items():
            if MILESTONE_DAG_KEY_RE.fullmatch(key):
                mid = MILESTONE_DAG_KEY_RE.fullmatch(key).group(1)  # type: ignore
                if mid in dag_raw and dag_raw[mid] != value:
                    raise PlanNormalizationError(
                        "DAG_CONTRADICTION",
                        f"DAG for {mid!r} contradicts across sections: {dag_raw[mid]!r} vs {value!r} (section {section.title!r})",
                    )
                # Also check duplicate within same section already handled, but cross-section duplicate same value is info
                if mid in dag_raw and dag_raw[mid] == value:
                    diagnostics.append({"code": "DUPLICATE_DAG_SAME_VALUE", "severity": "info", "milestone": mid})
                dag_raw[mid] = value
            elif MILESTONE_WORK_ITEMS_KEY_RE.fullmatch(key):
                mid = MILESTONE_WORK_ITEMS_KEY_RE.fullmatch(key).group(1)  # type: ignore
                if mid in work_items_raw and work_items_raw[mid] != value:
                    raise PlanNormalizationError(
                        "WORK_ITEMS_CONTRADICTION",
                        f"WORK_ITEMS for {mid!r} contradicts: {work_items_raw[mid]!r} vs {value!r}",
                    )
                work_items_raw[mid] = value
            elif MILESTONE_APPROVAL_KEY_RE.fullmatch(key):
                mid = MILESTONE_APPROVAL_KEY_RE.fullmatch(key).group(1)  # type: ignore
                # Only current sections are authoritative for approval, but we still collect to detect outside
                # If this section is not current, it's not authoritative; we don't collect, just diagnostic already
                if section.kind != KIND_CURRENT:
                    continue
                if mid in approvals_raw and approvals_raw[mid].strip().lower() != value.strip().lower():
                    raise PlanNormalizationError(
                        "APPROVAL_CONTRADICTION",
                        f"approval for {mid!r} contradicts: {approvals_raw[mid]!r} vs {value!r}",
                    )
                if mid in approvals_raw and approvals_raw[mid].strip().lower() == value.strip().lower():
                    diagnostics.append({"code": "DUPLICATE_APPROVAL_SAME_VALUE", "severity": "info", "milestone": mid})
                else:
                    approvals_raw[mid] = value

    # Also ensure current_fields approvals are captured (they are from current sections)
    # The above loop already captures them via sections scan, but we double-check
    for key, value in current_fields.items():
        if MILESTONE_APPROVAL_KEY_RE.fullmatch(key):
            mid = MILESTONE_APPROVAL_KEY_RE.fullmatch(key).group(1)  # type: ignore
            if mid not in approvals_raw:
                approvals_raw[mid] = value

    milestone_work_items: dict[str, tuple[str, ...]] = {}
    milestone_dependencies: dict[str, tuple[tuple[str, str], ...]] = {}
    milestone_approvals: dict[str, bool] = {}

    # Parse approvals
    for mid, raw in approvals_raw.items():
        try:
            milestone_approvals[mid] = _validate_approval_value(raw)
        except PlanNormalizationError:
            raise
        except Exception as exc:
            raise PlanNormalizationError("MALFORMED_APPROVAL_VALUE", str(exc)) from exc

    # Parse execution specs for each milestone that has DAG or WORK_ITEMS
    all_mids = set(dag_raw.keys()) | set(work_items_raw.keys())
    for mid in all_mids:
        dag_val = dag_raw.get(mid)
        wi_val = work_items_raw.get(mid)
        dag_work_items: tuple[str, ...] = ()
        dag_deps: tuple[tuple[str, str], ...] = ()
        if dag_val is not None:
            try:
                dag_work_items, dag_deps = _parse_dag_string(dag_val)
            except PlanNormalizationError:
                raise
            except Exception as exc:
                raise PlanNormalizationError("MALFORMED_DAG_VALUE", f"DAG for {mid!r} malformed: {exc}") from exc
            if not dag_work_items and dag_val.strip():
                # Non-empty DAG but no work items parsed => ambiguous
                raise PlanNormalizationError("AMBIGUOUS_DAG", f"DAG for {mid!r} is ambiguous or empty: {dag_val!r}")
        wi_items: tuple[str, ...] = ()
        if wi_val is not None:
            wi_items = _parse_work_items_value(wi_val)
            if not wi_items and wi_val.strip():
                raise PlanNormalizationError("MALFORMED_WORK_ITEMS", f"WORK_ITEMS for {mid!r} malformed: {wi_val!r}")

        # Reconcile: if both present, they must be compatible
        if dag_val is not None and wi_val is not None:
            # Explicit empty DAG (dag_val == "") with work items is valid independent (no deps)
            if dag_val.strip() == "":
                final_wi = wi_items
                final_deps = ()
            elif set(dag_work_items) != set(wi_items):
                if dag_work_items:
                    raise PlanNormalizationError(
                        "WORK_ITEMS_DAG_MISMATCH",
                        f"WORK_ITEMS {wi_items!r} does not match DAG nodes {dag_work_items!r} for {mid!r}",
                    )
                final_wi = wi_items
                final_deps = dag_deps
            else:
                final_wi = wi_items
                final_deps = dag_deps
        elif dag_val is not None:
            # DAG present (may be empty string for independent with no explicit work items)
            if dag_val.strip() == "" and not dag_work_items:
                # Empty DAG with no work items: treat as missing (fail later)
                raise PlanNormalizationError("MISSING_DAG_TRUTH", f"DAG for {mid!r} is empty and no WORK_ITEMS")
            final_wi = dag_work_items
            final_deps = dag_deps
        else:
            # DAG missing but WORK_ITEMS present => missing DAG truth (fail closed)
            raise PlanNormalizationError("MISSING_DAG_TRUTH", f"DAG for {mid!r} missing: {mid}_DAG not in Plan")

        # Validate dependencies reference known work items
        if final_deps:
            wi_set = set(final_wi)
            for src, dst in final_deps:
                if src not in wi_set or dst not in wi_set:
                    raise PlanNormalizationError(
                        "DAG_UNKNOWN_WORK_ITEM",
                        f"DAG for {mid!r} references unknown work item: {src!r}->{dst!r} not in {final_wi!r}",
                    )
            # Check for self-cycle etc. will be handled by MilestoneWorkItemGraph validation, but we can also check here
        if not final_wi and final_deps:
            raise PlanNormalizationError("DAG_WITHOUT_WORK_ITEMS", f"DAG for {mid!r} has dependencies but no work items")

        if final_wi:
            milestone_work_items[mid] = final_wi
            milestone_dependencies[mid] = final_deps
            if dag_val is not None:
                milestone_work_items[mid] = final_wi  # already
                # keep dag_raw for diagnostics
    # Also need to handle milestones that have no DAG/WORK_ITEMS but are current: they will be missing and later fail closed when requested

    return milestone_work_items, milestone_dependencies, dag_raw, milestone_approvals


def _extract_entry_base(current_fields: dict[str, str], diagnostics: list[dict[str, Any]]) -> str | None:
    val = current_fields.get(ENTRY_BASE_KEY)
    if val is None:
        # Also check PLAN_ACCEPTED_FRONTIER as fallback? But spec says ENTRY_BASE must come from Plan authority, not fallback to hardcoded
        # We treat missing as None, caller will fail closed if required
        return None
    v = val.strip()
    if len(v) < 7:
        raise PlanNormalizationError("MALFORMED_ENTRY_BASE", f"ENTRY_BASE {v!r} too short")
    # Allow hex-like but not strictly enforce; at least check hex chars for first 7
    if not re.fullmatch(r"[0-9a-fA-F]+", v):
        # Allow non-hex but still fail? For generic fixtures, entry base may be like 'abc123'
        # We keep permissive but warn
        diagnostics.append({"code": "ENTRY_BASE_NON_HEX", "severity": "info", "value": v})
    return v


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

    # M3-B003: collect canonical execution specs and approvals (fail-closed on contradiction/ambiguous)
    milestone_work_items, milestone_dependencies, milestone_dag_raw, milestone_approvals = _collect_milestone_execution(
        sections, current_fields, diagnostics
    )
    entry_base = _extract_entry_base(current_fields, diagnostics)

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
        milestone_work_items=milestone_work_items,
        milestone_dependencies=milestone_dependencies,
        milestone_approvals=milestone_approvals,
        milestone_dag_raw=milestone_dag_raw,
        entry_base=entry_base,
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
