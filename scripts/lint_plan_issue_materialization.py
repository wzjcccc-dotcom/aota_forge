#!/usr/bin/env python3
"""Issue materialization linter (P3 W2).

Validates Issue body + managed comments snapshot against canonical governance.
stdlib-only, deterministic, no network.

Modes:
  audit-current  - audit existing snapshot (tolerate legacy unmarked comments)
  audit-legacy   - legacy historical audit (more permissive)
  prewrite       - validate a proposed mutation (requires mutation metadata)

Input:
  --body-file PATH
  --comments-json PATH  (JSON array of {id, body})
  --mode audit-current|audit-legacy|prewrite
  --mutation-kind body_update|comment_update|comment_create  (prewrite only)
  --target-comment-id INT (prewrite only, if applicable)
  --target-role ROLE (prewrite only)
  --continuation-of INT (prewrite continuation parent)
  --user-approved-overflow yes|no (prewrite)
  --write-class normal_additive|semantic_compaction|authority_repair|plan_boundary_split|closure_critical_correction
  --body-size-warning 80 (optional override, KiB)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MANAGED_ROLES = [
    "milestone_progress_index",
    "development_notes",
    "defect_register",
    "plan_appendix",
    "decision_change_log",
]

KNOWN_ROLES_SET = set(MANAGED_ROLES)

# Thresholds
BODY_HEALTHY_TARGET = 80 * 1024
BODY_WARNING_THRESHOLD = 80 * 1024
BODY_HARD_STOP = 96 * 1024

# Ledger detection signals
LEDGER_SIGNALS = [
    re.compile(r"READ_BEFORE_WRITE\s*=\s*PASS"),
    re.compile(r"VERIFY_AFTER_WRITE\s*=\s*PASS"),
    re.compile(r"CONTROL_COMMENT_READ_BEFORE_WRITE"),
    re.compile(r"CONTROL_COMMENT_VERIFY_AFTER_WRITE"),
    re.compile(r"_CONSTRUCTION_RESULT"),
    re.compile(r"_SOURCE_REVIEW_RESULT"),
    re.compile(r"_REPAIR_RESULT"),
    re.compile(r"_REVIEW_RESULT"),
    re.compile(r"RV1.*RV2|R1.*R2.*repair", re.IGNORECASE | re.DOTALL),
    re.compile(r"disposable.*database|aota-worktrees/.*M\d+/W\d+", re.IGNORECASE),
    re.compile(r"```.*\n.*(PASS|FAIL).*\n.*```", re.DOTALL),
]

ROLE_LEDGER_SIGNALS = {
    "milestone_progress_index": [
        re.compile(r"REVIEW.*PASS|PASS.*REVIEW", re.IGNORECASE),
        re.compile(r"test matrix|command output|worktree history", re.IGNORECASE),
        re.compile(r"_CONSTRUCTION_RESULT"),
        re.compile(r"commit [0-9a-f]{7,40}"),
    ],
    "development_notes": [
        re.compile(r"R1\s*->\s*repair\s*->\s*R2", re.IGNORECASE),
        re.compile(r"source review result|validation output", re.IGNORECASE),
        re.compile(r"lane commit history|debug logs", re.IGNORECASE),
        re.compile(r"_REPAIR_RESULT"),
    ],
    "defect_register": [
        re.compile(r"command.*log|review log", re.IGNORECASE),
        re.compile(r"_REPAIR_RESULT"),
    ],
}

# ---------------------------------------------------------------------------
# P3 Guard Hardening — deterministic Body governance-state validator
# Separate from generic execution-ledger heuristic detection.
# Categories are stable and ordered for deterministic output.
# ---------------------------------------------------------------------------
CATEGORY_ORDER = [
    "MILESTONE_STATUS",
    "MILESTONE_ACCEPTANCE_RESULT",
    "MILESTONE_CLOSURE_RESULT",
    "MILESTONE_KNOWN_GOOD",
    "MILESTONE_INTEGRATION_RESULT",
    "WORK_ITEM_OPERATIONAL_RESULT",
    "MILESTONE_DAG_STATUS_DECORATION",
    "CURRENT_WORKSTREAM_IDENTITY",
]

# Operational state values for M<n>_STATUS (bounded, deterministic)
OPERATIONAL_STATUS_VALUES = {
    "completed",
    "planned",
    "in-progress",
    "in_progress",
    "ready",
    "blocked",
    "review",
    "cancelled",
    "ready_for_construction",
}

# Dedicated structural patterns — not one giant regex
_MILESTONE_STATUS_RE = re.compile(r"\bM(\d+)_STATUS\s*=\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)
_MILESTONE_ACCEPTANCE_RE = re.compile(
    r"\bM(\d+)_(?:FINAL_ACCEPTANCE|COMPLETION_ACCEPTANCE|ACCEPTANCE)\s*=\s*\S+", re.IGNORECASE
)
_MILESTONE_CLOSURE_RE = re.compile(r"\bM(\d+)_CLOSURE_MATERIALIZED\s*=\s*\S+", re.IGNORECASE)
_MILESTONE_KNOWN_GOOD_RE = re.compile(
    r"\bM(\d+)_KNOWN_GOOD_[A-Z0-9_]+\s*=\s*\S+", re.IGNORECASE
)
_MILESTONE_INTEGRATION_RE = re.compile(
    r"\bM(\d+)_INTEGRATION_(?:COMMIT|TREE)\s*=\s*\S+", re.IGNORECASE
)
# Work Item operational result: legacy letter M3_A_* or normalized M3_W1_* with operational suffix
_WORK_ITEM_OPERATIONAL_RE = re.compile(
    r"\bM(\d+)_(?:[A-Z]|W\d+)_(?:STATUS|FINAL_ACCEPTANCE|COMPLETION_ACCEPTANCE|ACCEPTANCE|CLOSURE_MATERIALIZED|KNOWN_GOOD_[A-Z0-9_]+|INTEGRATION_COMMIT|INTEGRATION_TREE)\s*=\s*\S+",
    re.IGNORECASE,
)
# Current Workstream identity (active, not legacy) — filtered for LEGACY_ prefix post-match
_WORKSTREAM_IDENTITY_RE = re.compile(
    r"\b(WORKSTREAM|WORKSTREAM_NAME|WORKSTREAM_ID|PARENT_MILESTONE)\s*=\s*\S+"
)
# DAG status decoration — per-line structural detection
_DAG_STATE_TOKEN_RE = re.compile(r"\[\s*(completed|planned|in-progress|in_progress|ready|blocked|review|cancelled)\s*\]", re.IGNORECASE)
_DAG_LINE_ANCHOR_RE = re.compile(r"^\s*[-*]?\s*M\d+\b")
# Retired/historical context phrases that tolerate DAG bracket discussion
_DAG_TOLERATED_CONTEXT_RE = re.compile(r"retired|historical notation|legacy", re.IGNORECASE)


def _is_legacy_prefixed_workstream(text: str, match_start: int) -> bool:
    """Return True if the WORKSTREAM match is part of LEGACY_ compatibility form."""
    prefix_window = text[max(0, match_start - 30):match_start]
    # Check for LEGACY_ or LEGACY_PARENT_ directly preceding
    # e.g., LEGACY_PARENT_MILESTONE=M2 -> match PARENT_MILESTONE preceded by LEGACY_
    # LEGACY_PARENT_WORKSTREAM=W2 -> match WORKSTREAM preceded by LEGACY_PARENT_
    # LEGACY_PARENT_WORKSTREAM_NAME -> match WORKSTREAM_NAME preceded similarly
    # Also LEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE contains WORKSTREAM but not as assignment
    # We check if prefix_window ends with LEGACY_ or LEGACY_PARENT_
    if "LEGACY_" in prefix_window:
        # Find last occurrence of LEGACY_ before match
        # If the suffix between LEGACY_ and match_start is only letters/underscores (e.g., PARENT_), treat as legacy
        last_legacy = prefix_window.rfind("LEGACY_")
        between = prefix_window[last_legacy + len("LEGACY_"):].strip()
        # between may be "PARENT_" or "" ; if non-empty and only A-Z_/-
        if between == "" or re.fullmatch(r"[A-Z_]*", between):
            # Ensure the immediate character before match is _ or directly LEGACY_ prefix
            # For safety, treat any LEGACY_ within 20 chars as legacy-tolerated
            return True
    return False


def detect_body_operational_state(body: str) -> list[dict]:
    """Deterministic Body governance-state validator.

    Returns list of findings: {category, marker, excerpt}
    Distinct from generic ledger heuristic. Each finding is high-confidence
    structural operational-state violation scoped to M<n> prefixes or
    current Workstream identity or DAG decoration.

    Stable ordering by CATEGORY_ORDER then appearance.
    """
    findings: list[dict] = []
    if not body:
        return findings

    # MILESTONE_STATUS — check value is operational-ish; any STATUS assignment is suspect,
    # but we enforce bounded operational value set for stricter determinism.
    for m in _MILESTONE_STATUS_RE.finditer(body):
        raw_value = m.group(2).strip().strip('"').strip("'").rstrip(",;")
        low = raw_value.lower()
        # Normalize in_progress variations: treat yes/no as not operational status
        # Only flag when value looks like operational state (known set) OR any word that is clearly status-like
        # For determinism, flag if low in OPERATIONAL_STATUS_VALUES OR matches main forms.
        # We flag any STATUS assignment where value is operational OR where pattern suggests status line.
        # To avoid over-flagging arbitrary strings like "foo", require low in set OR low in common statuses.
        # However spec says M1_STATUS=completed must fail; we also want to catch any clear status value.
        # Implement: flag if low in set, else if low matches [a-z_\-]+ and not generic nonsense, still flag?
        # Spec says "Do not rely only on those exact values if existing grammar makes other clearly operational state values deterministic."
        # So we treat any M<n>_STATUS= as violation, but to preserve determinism we check against a slightly wider set including ready_for_construction.
        # For safety, flag all M<n>_STATUS occurrences — any STATUS in Body is operational residue.
        # The value-check guards against false positive like M1_STATUS=unknown where unknown not operational? But we still flag as it is still status marker.
        # Decision: flag every M<n>_STATUS assignment (high confidence structural).
        marker = m.group(0).strip()[:120]
        findings.append({"category": "MILESTONE_STATUS", "marker": marker, "pos": m.start()})

    for m in _MILESTONE_ACCEPTANCE_RE.finditer(body):
        # Already excludes MEMORY_* because prefix is M\d+
        marker = m.group(0).strip()[:120]
        findings.append({"category": "MILESTONE_ACCEPTANCE_RESULT", "marker": marker, "pos": m.start()})

    for m in _MILESTONE_CLOSURE_RE.finditer(body):
        marker = m.group(0).strip()[:120]
        findings.append({"category": "MILESTONE_CLOSURE_RESULT", "marker": marker, "pos": m.start()})

    for m in _MILESTONE_KNOWN_GOOD_RE.finditer(body):
        marker = m.group(0).strip()[:120]
        findings.append({"category": "MILESTONE_KNOWN_GOOD", "marker": marker, "pos": m.start()})

    for m in _MILESTONE_INTEGRATION_RE.finditer(body):
        # Avoid double-counting M<n>_KNOWN_GOOD cases already captured (different prefix)
        marker = m.group(0).strip()[:120]
        findings.append({"category": "MILESTONE_INTEGRATION_RESULT", "marker": marker, "pos": m.start()})

    # Work Item operational result — ensure not double-counting milestone categories above
    for m in _WORK_ITEM_OPERATIONAL_RE.finditer(body):
        raw = m.group(0).strip()[:120]
        # Filter out normative-but-similar: e.g., M3_E_REVISION_CAS is not in pattern, so not matched
        # Pattern already restricts to operational suffixes, so any match is high confidence.
        findings.append({"category": "WORK_ITEM_OPERATIONAL_RESULT", "marker": raw, "pos": m.start()})

    # DAG status decoration — line-structured
    for idx, line in enumerate(body.splitlines()):
        if _DAG_LINE_ANCHOR_RE.search(line) and _DAG_STATE_TOKEN_RE.search(line):
            if _DAG_TOLERATED_CONTEXT_RE.search(line):
                continue
            # Only flag if bracket token is trailing/associated with DAG entry
            # Bounded: line starts with M<n> and contains state bracket
            marker = line.strip()[:120]
            # Use line start as position approximation
            pos = body.find(line) if line.strip() else idx * 1000
            findings.append({"category": "MILESTONE_DAG_STATUS_DECORATION", "marker": marker, "pos": pos})

    # Current Workstream identity — active metadata
    for m in _WORKSTREAM_IDENTITY_RE.finditer(body):
        start = m.start(1)  # start of key
        if _is_legacy_prefixed_workstream(body, start):
            continue
        # Also exclude LEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE pattern which contains WORKSTREAM but not as assignment
        # Our regex requires "=" after key, so that case already not matched unless it has "="
        # Check that match is not part of LEGACY_ line; already handled
        marker = m.group(0).strip()[:120]
        # Additional guard: ensure not "LEGACY_PARENT_WORKSTREAM_NAME" already excluded
        findings.append({"category": "CURRENT_WORKSTREAM_IDENTITY", "marker": marker, "pos": m.start()})

    # Deterministic ordering: sort by CATEGORY_ORDER index then pos
    order_index = {cat: i for i, cat in enumerate(CATEGORY_ORDER)}
    findings.sort(key=lambda f: (order_index.get(f["category"], 999), f["pos"]))
    # Strip pos for external use, keep category+marker
    for f in findings:
        f.pop("pos", None)
    return findings


def detect_current_workstream_identity(text: str) -> list[dict]:
    """Thin wrapper for workstream identity subset (used for comment surfaces)."""
    all_findings = detect_body_operational_state(text)
    return [f for f in all_findings if f["category"] == "CURRENT_WORKSTREAM_IDENTITY"]

def parse_comment_role(body: str):
    """Extract COMMENT_ROLE and CONTINUATION_OF from comment body."""
    # Look for COMMENT_ROLE=...
    role_match = re.search(r"COMMENT_ROLE\s*=\s*([a-z_]+)", body)
    cont_match = re.search(r"CONTINUATION_OF\s*=\s*([0-9A-Za-z_\-]+|none)", body)
    # Also support PARENT_COMMENT_ID etc but we use CONTINUATION_OF
    role = role_match.group(1) if role_match else None
    cont = cont_match.group(1) if cont_match else None
    # Normalize cont "none" -> None
    if cont in ("none", "None", ""):
        cont = None
    return role, cont

def detect_ledger(body: str):
    hits = []
    for pat in LEDGER_SIGNALS:
        if pat.search(body):
            hits.append(pat.pattern[:50])
    return hits

def detect_role_ledger(role: str, body: str):
    pats = ROLE_LEDGER_SIGNALS.get(role, [])
    hits = []
    for pat in pats:
        if pat.search(body):
            hits.append(pat.pattern[:50])
    # Also count generic ledger signals
    generic_hits = detect_ledger(body)
    return hits, generic_hits

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--comments-json", required=True)
    parser.add_argument("--mode", choices=["audit-current", "audit-legacy", "prewrite"], required=True)
    parser.add_argument("--mutation-kind", choices=["body_update", "comment_update", "comment_create"])
    parser.add_argument("--target-comment-id", type=int)
    parser.add_argument("--target-role")
    parser.add_argument("--continuation-of")
    parser.add_argument("--user-approved-overflow", choices=["yes", "no"])
    parser.add_argument("--write-class", choices=["normal_additive", "semantic_compaction", "authority_repair", "plan_boundary_split", "closure_critical_correction"], default="normal_additive")
    parser.add_argument("--output-json", help="optional json output path")
    args = parser.parse_args()

    errors = []
    warnings = []

    body_path = Path(args.body_file)
    if not body_path.is_file():
        print("PLAN_ISSUE_MATERIALIZATION_FAIL")
        print("CODE=PM007_BODY_LEDGER_CONTAMINATION")
        print(f"DETAIL=body file missing: {body_path}")
        return 1
    body_text = body_path.read_text(encoding="utf-8")
    body_bytes = body_text.encode("utf-8")
    body_len = len(body_bytes)

    # Body size guard
    body_over_hard = body_len > BODY_HARD_STOP
    body_over_warning = body_len > BODY_WARNING_THRESHOLD
    if args.mode in ("audit-current", "audit-legacy"):
        if body_over_warning and not body_over_hard:
            warnings.append(f"WARNING_CODE=BODY_MATERIALIZATION_AUDIT_REQUIRED body={body_len} threshold=81920")
        elif body_over_hard:
            # In audit, don't fail hard, just warn that additive writes blocked
            warnings.append(f"WARNING_CODE=BODY_OVER_HARD_STOP body={body_len} hard=98304 NORMAL_ADDITIVE_WRITE_ALLOWED=no")
            # Also note BODY_OVER_HARD_STOP=yes
            warnings.append(f"BODY_OVER_HARD_STOP=yes")
    elif args.mode == "prewrite":
        if body_over_hard:
            # Check write class
            allowed = {"semantic_compaction", "authority_repair", "plan_boundary_split", "closure_critical_correction"}
            if args.write_class == "normal_additive":
                errors.append(("PM006_BODY_ADDITIVE_HARD_STOP", f"body {body_len} >96KiB normal_additive forbidden (write_class={args.write_class})"))
            elif args.write_class not in allowed:
                errors.append(("PM006_BODY_ADDITIVE_HARD_STOP", f"body {body_len} >96KiB write_class {args.write_class} not allowed"))
            else:
                warnings.append(f"WARNING_CODE=BODY_OVER_HARD_STOP_BUT_ALLOWED_WRITE_CLASS body={body_len} write_class={args.write_class}")

    # Body ledger guard - high confidence only
    ledger_hits = detect_ledger(body_text)
    # Require multi-signal for hard fail
    if len(ledger_hits) >= 3:
        errors.append(("PM007_BODY_LEDGER_CONTAMINATION", f"high-confidence execution ledger in body hits={ledger_hits}"))
    elif len(ledger_hits) >= 2:
        warnings.append(f"WARNING_CODE=BODY_LEDGER_RISK hits={ledger_hits}")

    # Parse comments
    comments_path = Path(args.comments_json)
    if not comments_path.is_file():
        print("PLAN_ISSUE_MATERIALIZATION_FAIL")
        print("CODE=PM001_DUPLICATE_PRIMARY_ROLE")
        print(f"DETAIL=comments json missing: {comments_path}")
        return 1
    try:
        comments_data = json.loads(comments_path.read_text(encoding="utf-8"))
    except Exception as e:
        print("PLAN_ISSUE_MATERIALIZATION_FAIL")
        print("CODE=PM001_DUPLICATE_PRIMARY_ROLE")
        print(f"DETAIL=comments json parse error: {e}")
        return 1
    if not isinstance(comments_data, list):
        print("PLAN_ISSUE_MATERIALIZATION_FAIL")
        print("CODE=PM001_DUPLICATE_PRIMARY_ROLE")
        print("DETAIL=comments json must be array")
        return 1

    # Validate comment structure
    seen_ids = set()
    primary_by_role = {}
    continuation_targets = {}
    # Map id -> role for parent resolution
    id_to_role = {}
    id_to_is_primary = {}

    for c in comments_data:
        cid = c.get("id")
        cbody = c.get("body", "")
        if cid is None:
            errors.append(("PM001_DUPLICATE_PRIMARY_ROLE", "comment missing id"))
            continue
        if cid in seen_ids:
            errors.append(("PM001_DUPLICATE_PRIMARY_ROLE", f"duplicate comment id {cid}"))
        seen_ids.add(cid)
        role, cont = parse_comment_role(cbody)
        if role is not None:
            if role not in KNOWN_ROLES_SET:
                errors.append(("PM004_UNKNOWN_MANAGED_ROLE", f"unknown managed role {role} id={cid}"))
            else:
                # Determine primary vs continuation
                is_primary = cont is None
                # Also need to check CONTINUATION_OF required for continuation
                if not is_primary:
                    # continuation must have valid parent role matching
                    continuation_targets[cid] = cont
                    # Will validate later
                    pass
                else:
                    # primary
                    if role in primary_by_role:
                        errors.append(("PM001_DUPLICATE_PRIMARY_ROLE", f"duplicate primary role {role} ids {primary_by_role[role]} and {cid}"))
                    else:
                        primary_by_role[role] = cid
                id_to_role[cid] = role
                id_to_is_primary[cid] = is_primary
                # Malformed marker check: if role found but no CONTINUATION_OF line and body suggests continuation? Already handled
                if cont is not None:
                    # cont must be numeric or id string
                    if cont == "":
                        errors.append(("PM002_INVALID_CONTINUATION", f"continuation missing CONTINUATION_OF value id={cid}"))
        else:
            # No COMMENT_ROLE -> legacy historical comment, tolerate physical count >5
            # Not counted as managed role
            # But check if body pretends to be managed without marker? That's okay as legacy
            pass

    # Validate continuation chains - normalize cont to int if possible for id comparison
    for cid, cont_raw in continuation_targets.items():
        cont = cont_raw
        # Try to interpret cont as int id
        try:
            cont_int = int(str(cont_raw))
            if cont_int in seen_ids:
                cont = cont_int
            elif str(cont_int) in {str(s) for s in seen_ids}:
                cont = str(cont_int)
        except:
            pass
        if cont not in seen_ids:
            # Also try string vs int coercion
            alt = None
            try:
                alt = int(cont)
                if alt in seen_ids:
                    cont = alt
            except:
                pass
            if cont not in seen_ids:
                errors.append(("PM002_INVALID_CONTINUATION", f"continuation {cid} points to nonexistent {cont_raw}"))
                continue
        parent_role = id_to_role.get(cont)
        # If not found, try alternative int/string
        if parent_role is None:
            # Try coercion
            try:
                alt = int(cont)
                parent_role = id_to_role.get(alt)
                if parent_role is not None:
                    cont = alt
            except:
                pass
        child_role = id_to_role.get(cid)
        if parent_role is None:
            errors.append(("PM002_INVALID_CONTINUATION", f"continuation {cid} parent {cont} has no managed role (legacy parent)"))
            continue
        if parent_role != child_role:
            errors.append(("PM003_UNAPPROVED_CONTINUATION", f"continuation role mismatch {cid} ({child_role}) vs parent {cont} ({parent_role})"))
        # Also ensure parent is not itself unknown? Already checked

    # Check expected 5 primary roles in audit-current (but allow missing in early init)
    if args.mode == "audit-current":
        missing = [r for r in MANAGED_ROLES if r not in primary_by_role]
        if missing:
            # If prewrite create for missing role, it's allowed elsewhere; but for audit, we report as error only if we are in steady-state?
            # For P3, audit-current should require exactly 5 primaries, but legacy extra unmarked comments tolerated.
            # If body is legacy and missing roles, we warn not fail? Let's enforce but allow legacy mode to pass with missing.
            # For now, require exactly 5 primaries for PASS fixtures, but for historical issues we should not break repo.
            # We'll make it a warning if count <5 but not fail hard unless strict? However spec says must have exactly 5 PRIMARY managed roles.
            # For synthetic tests we want FAIL when missing, so make it error.
            # But for real historical Issue #13 maybe missing -> we should allow audit-legacy to tolerate.
            errors.append(("PM001_DUPLICATE_PRIMARY_ROLE", f"missing primary managed roles: {missing}"))
        # Also check duplicate handled above
    elif args.mode == "audit-legacy":
        # Tolerate missing roles and extra legacy comments
        pass

    # Normal new comment guard (prewrite)
    if args.mode == "prewrite":
        # If mutation is comment_create, check if it's allowed
        if args.mutation_kind == "comment_create":
            target_role = args.target_role
            if target_role is None:
                errors.append(("PM005_NORMAL_NEW_COMMENT_FORBIDDEN", "comment_create missing target_role"))
            else:
                if target_role not in KNOWN_ROLES_SET:
                    errors.append(("PM004_UNKNOWN_MANAGED_ROLE", f"unknown target_role {target_role}"))
                else:
                    # Is this creating a missing primary?
                    if target_role not in primary_by_role:
                        # Creating initialization missing primary -> allowed
                        pass
                    else:
                        # Creating continuation requires approval and CONTINUATION_OF
                        if args.continuation_of is None:
                            errors.append(("PM002_INVALID_CONTINUATION", f"continuation missing CONTINUATION_OF for role {target_role}"))
                        else:
                            if args.user_approved_overflow != "yes":
                                errors.append(("PM003_UNAPPROVED_CONTINUATION", f"unapproved continuation create for {target_role} requires USER_APPROVED_OVERFLOW_CONTINUATION=yes"))
                            # Also check continuation_of points to valid parent of same role
                            cont_id = args.continuation_of
                            # Try to convert to int if possible
                            try:
                                cont_int = int(cont_id)
                                cont_id = cont_int
                            except:
                                pass
                            if cont_id not in seen_ids:
                                errors.append(("PM002_INVALID_CONTINUATION", f"continuation_of {cont_id} not found"))
                            else:
                                parent_role = id_to_role.get(cont_id)
                                if parent_role != target_role:
                                    errors.append(("PM003_UNAPPROVED_CONTINUATION", f"continuation role mismatch target {target_role} vs parent {parent_role}"))
        elif args.mutation_kind == "comment_update":
            # Updating existing managed comment is allowed (mutable projection)
            pass
        elif args.mutation_kind == "body_update":
            # Body update already handled size/ledger
            pass
        else:
            # If no mutation_kind, then maybe generic new comment creation not allowed
            # Detect if comments_data has extra unmarked new comment not in allowed categories -> handled via prewrite create check above
            pass

        # Also for prewrite, check arbitrary routine new GitHub comment (non-managed)
        # If caller is trying to create a comment without COMMENT_ROLE and not a continuation/primary, it's forbidden
        # This is covered by requiring target_role to be known; if they create unmarked comment, it would be without role -> fail
        # For our CLI, we already enforce that comment_create must have target_role known; if they omit it, we already error

        # Legacy extra unmarked comments are tolerated in audit, but in prewrite they should not be created anew
        # So if prewrite tries to create unmarked comment with arbitrary content, we reject
        if args.mutation_kind == "comment_create" and args.target_role is None:
            errors.append(("PM005_NORMAL_NEW_COMMENT_FORBIDDEN", "arbitrary routine new GitHub comment forbidden (no managed role)"))

    # ------------------------------------------------------------------
    # P3 hardening: structural Body governance-state validator
    # ------------------------------------------------------------------
    body_struct_findings = detect_body_operational_state(body_text)
    # Workstream identity also checked in comments (materialization-wide)
    comment_workstream_findings: list[dict] = []
    for _c in comments_data:
        _cb = _c.get("body", "")
        for _f in detect_current_workstream_identity(_cb):
            # Annotate with comment id for stable detail
            comment_workstream_findings.append({**_f, "comment_id": _c.get("id")})

    # Map category to stable error code
    def _code_for_category(cat: str) -> str:
        if cat == "CURRENT_WORKSTREAM_IDENTITY":
            return "PM009_CURRENT_WORKSTREAM_IDENTITY"
        # DAG and all milestone/work-item operational map to PM008
        return "PM008_BODY_OPERATIONAL_STATE"

    # Collect structural findings to report
    structural_findings = []
    # Body findings (all categories)
    for _f in body_struct_findings:
        structural_findings.append({"code": _code_for_category(_f["category"]), **_f, "surface": "body"})
    # Comment workstream findings (only workstream identity, to avoid double Body-only milestone checks on comments)
    for _f in comment_workstream_findings:
        # Avoid duplicating a body finding already counted? They are distinct surfaces, keep both
        structural_findings.append({"code": _code_for_category(_f["category"]), **_f, "surface": "comment"})

    # Mode semantics: audit-current / prewrite = hard FAIL, audit-legacy = tolerable warning
    if args.mode in ("audit-current", "prewrite"):
        for _sf in structural_findings:
            cat = _sf["category"]
            marker = _sf["marker"][:80]
            surface = _sf["surface"]
            cid = _sf.get("comment_id")
            extra = f" comment_id={cid}" if surface == "comment" and cid is not None else ""
            detail = f"category={cat} marker={marker} surface={surface}{extra}"
            errors.append((_sf["code"], detail))
    elif args.mode == "audit-legacy":
        for _sf in structural_findings:
            cat = _sf["category"]
            marker = _sf["marker"][:80]
            surface = _sf["surface"]
            warnings.append(f"WARNING_CODE={_sf['code']} category={cat} marker={marker} surface={surface}")

    # Managed-role content lint (role-aware)
    for c in comments_data:
        cbody = c.get("body", "")
        cid = c.get("id")
        role, _ = parse_comment_role(cbody)
        if role in ROLE_LEDGER_SIGNALS:
            per_role_hits, generic_hits = detect_role_ledger(role, cbody)
            # Threshold: for #1, need high confidence: at least 2 signals or large content?
            # For defect_register, #1 etc we use deterministic thresholds
            total_hits = len(per_role_hits) + len(generic_hits)
            # Also check for large ledger dumps: body length > 20k and containing PASS/FAIL etc
            if total_hits >= 2 and len(cbody) > 5000:
                # Strong signal -> fail
                if role == "milestone_progress_index":
                    errors.append(("PM007_BODY_LEDGER_CONTAMINATION", f"high-confidence #1 raw review/test ledger role={role} id={cid} hits={per_role_hits + generic_hits}"))
                elif role == "development_notes":
                    errors.append(("PM007_BODY_LEDGER_CONTAMINATION", f"high-confidence #2 R1/R2 repair chronology dump role={role} id={cid} hits={per_role_hits + generic_hits}"))
                else:
                    warnings.append(f"WARNING_CODE=ROLE_LEDGER_RISK role={role} id={cid} hits={per_role_hits + generic_hits}")
            elif total_hits >= 3:
                errors.append(("PM007_BODY_LEDGER_CONTAMINATION", f"high-confidence ledger contamination role={role} id={cid} hits={per_role_hits + generic_hits}"))
            elif total_hits >= 1 and len(cbody) > 10000:
                warnings.append(f"WARNING_CODE=ROLE_LEDGER_RISK role={role} id={cid} hits={per_role_hits + generic_hits}")

    # Special check for #4 plan_appendix authority
    for c in comments_data:
        cbody = c.get("body", "")
        role, _ = parse_comment_role(cbody)
        if role == "plan_appendix":
            if "COMMENT_PLAN_AUTHORITY=yes" in cbody or "EFFECTIVE_PLAN=yes" in cbody:
                errors.append(("PM004_UNKNOWN_MANAGED_ROLE", f"#4 plan_appendix must not claim authority id={c.get('id')}"))

    # Output
    if errors:
        print("PLAN_ISSUE_MATERIALIZATION_FAIL")
        for code, detail in errors:
            print(f"CODE={code}")
            # Try to extract ROLE if detail contains role=
            # Print ROLE line if possible
            m = re.search(r"role=([a-z_]+)", detail)
            if m:
                print(f"ROLE={m.group(1)}")
            print(f"DETAIL={detail}")
        for w in warnings:
            print(w)
        return 1
    else:
        print("PLAN_ISSUE_MATERIALIZATION_PASS")
        for w in warnings:
            print(w)
        # Also report body size
        print(f"BODY_SIZE={body_len}")
        if body_over_hard:
            print("BODY_OVER_HARD_STOP=yes")
            print("NORMAL_ADDITIVE_WRITE_ALLOWED=no")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
