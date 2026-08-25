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
