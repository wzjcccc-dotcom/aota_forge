#!/usr/bin/env python3
"""Deterministic fixture suite for P3 chat governance guards (W3).

stdlib-only, no pytest, no network. Runs static guard and materialization linter
against synthetic positive/negative/false-positive fixtures.

Exit 0 = all fixtures PASS with expected codes, non-zero = FAIL.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_ROOT = ROOT / "chat_governance"
VERIFY_CHAT = ROOT / "scripts" / "verify_chat_governance.py"
LINT_SCRIPT = ROOT / "scripts" / "lint_plan_issue_materialization.py"

# Copy canonical files for fixtures to start from a known-good base
def load_canonical_text(name: str) -> str:
    return (GOVERNANCE_ROOT / name).read_text(encoding="utf-8")

CANONICAL_CORE_TEXT = load_canonical_text("aota-portable-plan-governance.md")
CANONICAL_INDEX_TEXT = load_canonical_text("GOVERNANCE_INDEX.md")
CANONICAL_CHAT_PLANNING = load_canonical_text("aota-chatgpt-project-planning.md")
CANONICAL_PARALLEL = load_canonical_text("aota-chatgpt-parallel-development.md")
CANONICAL_WORKTREE = load_canonical_text("aota-chatgpt-worktree-governance.md")
CANONICAL_GITHUB_ALIAS = load_canonical_text("aota-github-issue-planning.md")

# Helper to run static guard on synthetic governance root
def run_static_guard(gov_files: dict) -> tuple[int, str]:
    # gov_files: filename -> content, must include at least GOVERNANCE_INDEX.md etc
    with tempfile.TemporaryDirectory() as td:
        gov_root = Path(td) / "chat_governance"
        gov_root.mkdir(parents=True)
        for fname, content in gov_files.items():
            (gov_root / fname).write_text(content, encoding="utf-8")
        # Run via subprocess with --governance-root
        cmd = [sys.executable, str(VERIFY_CHAT), "--governance-root", str(gov_root)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        return proc.returncode, out

def make_good_governance() -> dict:
    return {
        "GOVERNANCE_INDEX.md": CANONICAL_INDEX_TEXT,
        "aota-portable-plan-governance.md": CANONICAL_CORE_TEXT,
        "aota-chatgpt-project-planning.md": CANONICAL_CHAT_PLANNING,
        "aota-chatgpt-parallel-development.md": CANONICAL_PARALLEL,
        "aota-chatgpt-worktree-governance.md": CANONICAL_WORKTREE,
        "aota-github-issue-planning.md": CANONICAL_GITHUB_ALIAS,
    }

# Materialization linter helper
def run_lint(body: str, comments: list, mode: str, extra_args: list = None) -> tuple[int, str]:
    extra_args = extra_args or []
    with tempfile.TemporaryDirectory() as td:
        body_f = Path(td) / "body.md"
        body_f.write_text(body, encoding="utf-8")
        comments_f = Path(td) / "comments.json"
        comments_f.write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(LINT_SCRIPT), "--body-file", str(body_f), "--comments-json", str(comments_f), "--mode", mode] + extra_args
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        return proc.returncode, out

# Generate managed comment body helper
def managed_comment(cid: int, role: str, cont: str = None, extra: str = "") -> dict:
    lines = [f"COMMENT_ROLE={role}"]
    if cont is not None:
        lines.append(f"CONTINUATION_OF={cont}")
    else:
        # For primary, optionally include CONTINUATION_OF=none if we want explicit
        pass
    body = "\n".join(lines) + ("\n" + extra if extra else "")
    # Wrap in text block to mimic real comment
    full = f"```text\n" + body + "\n```\n" + extra
    return {"id": cid, "body": full}

def five_primary_comments() -> list:
    return [
        managed_comment(101, "milestone_progress_index", None, "Current S2/M1 status: in-progress\nNext: W1\n"),
        managed_comment(102, "development_notes", None, "Frozen decision: use S/M/W hierarchy\n"),
        managed_comment(103, "defect_register", None, "I12-B001 open\n"),
        managed_comment(104, "plan_appendix", None, "Implementation notes: API foo\n"),
        managed_comment(105, "decision_change_log", None, "2026-08-25: adopted S/M/W\n"),
    ]

# ---------------------------------------------------------------------------
# Fixture definitions
# ---------------------------------------------------------------------------

def test_static_guard():
    results = []
    # P01 canonical current governance -> PASS
    def check(name, files, should_pass, expect_code=None):
        rc, out = run_static_guard(files)
        passed = (rc == 0) == should_pass
        # For fail fixtures, check stable code present
        code_ok = True
        if expect_code and not should_pass:
            code_ok = expect_code in out
            passed = passed and code_ok
        results.append((name, passed, rc, out.strip().splitlines()[:5]))
        if not passed:
            print(f"FAIL static {name} rc={rc} expect_pass={should_pass} code_ok={code_ok}")
            print(out[:800])
        return passed

    # P01
    check("P01 canonical -> PASS", make_good_governance(), True)
    # P02 legacy W=Workstream wording inside explicit legacy compatibility -> PASS
    # We inject legacy phrase inside legacy section, should be tolerated
    gov = make_good_governance()
    # Add legacy phrase inside plan_appendix? Actually core already has legacy section; we add an extra tolerated instance in a legacy-commented area
    # For test, inject into aota-portable-plan-governance.md a line inside legacy section that mentions W<n>=Workstream but with LEGACY marker
    # The core already tolerates LEGACY_W_AS_WORKSTREAM... So just ensure still passes with original (already covers P02). We'll explicitly test by adding a legacy compatibility block in an adapter
    gov2 = make_good_governance()
    gov2["aota-github-issue-planning.md"] = CANONICAL_GITHUB_ALIAS + "\n## Legacy compatibility\nLEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE=yes\nW<n> = Workstream historical reference\n"
    check("P02 legacy Workstream inside compatibility -> PASS", gov2, True)

    # P03 thin adapter references shared core -> PASS (already canonical)
    check("P03 thin adapter references shared core -> PASS", make_good_governance(), True)

    # P04 risk-scoped exception wording -> PASS
    # Add a risk-scoped extra gate in parallel adapter (should be allowed)
    gov3 = make_good_governance()
    gov3["aota-chatgpt-parallel-development.md"] = CANONICAL_PARALLEL + "\nEXTRA_GATE_ID=RISK-1\nIDENTIFIED_RISK=destructive migration\nJUSTIFICATION=irreversible\nSCOPE=DB migration\nEND_CONDITION=verified\nRISK_SCOPED_EXTRA_GATE_ALLOWED=yes\n"
    check("P04 risk-scoped exception wording -> PASS", gov3, True)

    # N01 adapter declares itself shared normative authority -> FAIL
    gov_n1 = make_good_governance()
    gov_n1["aota-chatgpt-project-planning.md"] = CANONICAL_CHAT_PLANNING + "\nThis is the single shared semantic governance core for Chat planning\nONE_SHARED_GOVERNANCE_AUTHORITY=yes\n"
    check("N01 adapter declares itself shared core -> FAIL", gov_n1, False, "CG003_DUPLICATE_NORMATIVE_AUTHORITY")

    # N02 current Event Log-only comment model -> FAIL
    gov_n2 = make_good_governance()
    gov_n2["aota-github-issue-planning.md"] = "```text\nComments are an append-only Event Log only\n```\n"
    # Need to keep required reference else also fail drift, but we want Event Log fail
    # Add required alias marker to still pass thin check but event log fail triggers
    gov_n2["aota-github-issue-planning.md"] += "\nLEGACY_COMPATIBILITY_ALIAS_ONLY=yes\n"
    check("N02 Event Log-only current -> FAIL", gov_n2, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N03 body + structured appendices current authority -> FAIL
    gov_n3 = make_good_governance()
    gov_n3["aota-portable-plan-governance.md"] = CANONICAL_CORE_TEXT + "\nGitHub Issue body + structured appendices = Portable Plan\n"
    check("N03 body+appendices current authority -> FAIL", gov_n3, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N04 W=Workstream as new/current hierarchy -> FAIL
    gov_n4 = make_good_governance()
    gov_n4["aota-chatgpt-project-planning.md"] = CANONICAL_CHAT_PLANNING + "\nFORMAL_GOVERNANCE_DEPTH=W_M\nW<n> = Workstream\n"
    check("N04 W=Workstream current hierarchy -> FAIL", gov_n4, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N05 per-W Steward default -> FAIL (inject forbidden marker)
    gov_n5 = make_good_governance()
    gov_n5["aota-portable-plan-governance.md"] = CANONICAL_CORE_TEXT + "\nNORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=yes\n"
    check("N05 per-W Steward default -> FAIL", gov_n5, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N06 per-W independent review default -> FAIL
    gov_n6 = make_good_governance()
    gov_n6["aota-chatgpt-parallel-development.md"] = CANONICAL_PARALLEL + "\nNORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=yes\n"
    check("N06 per-W independent review default -> FAIL", gov_n6, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N07 independent integration review universal default -> FAIL
    gov_n7 = make_good_governance()
    gov_n7["aota-portable-plan-governance.md"] = CANONICAL_CORE_TEXT + "\nINDEPENDENT_INTEGRATION_REVIEW=yes\n"
    check("N07 independent integration review -> FAIL", gov_n7, False, "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED")

    # N08 missing canonical target -> FAIL (remove file)
    gov_n8 = make_good_governance()
    del gov_n8["aota-chatgpt-worktree-governance.md"]
    # Also remove from index expectation? Actually index still points to missing file, so guard should catch missing target
    check("N08 missing canonical target -> FAIL", gov_n8, False, "CG001_CANONICAL_CORE_MISSING")

    return results

def test_materialization():
    results = []
    def check(name, body, comments, mode, extra, should_pass, expect_code=None):
        rc, out = run_lint(body, comments, mode, extra)
        passed = (rc == 0) == should_pass
        code_ok = True
        if expect_code and not should_pass:
            code_ok = expect_code in out
            passed = passed and code_ok
        results.append((name, passed, rc, out.strip().splitlines()[:5]))
        if not passed:
            print(f"FAIL material {name} rc={rc} expect_pass={should_pass} code_ok={code_ok}")
            print(out[:1000])
        return passed

    good_body = "# Plan\nObjective: build foo\nScope: bar\n"

    # M-P01 five primary roles -> PASS
    check("M-P01 five primary -> PASS", good_body, five_primary_comments(), "audit-current", [], True)

    # M-P02 approved same-role continuation with valid parent -> PASS
    cont_comments = five_primary_comments() + [managed_comment(106, "plan_appendix", "104", "continued appendix")]
    check("M-P02 approved continuation -> PASS", good_body, cont_comments, "audit-current", [], True)

    # M-P03 legacy extra unmarked comments tolerated -> PASS
    legacy_comments = five_primary_comments() + [{"id": 200, "body": "old historical comment without role"}, {"id": 201, "body": "another legacy comment"}]
    check("M-P03 legacy extra unmarked tolerated -> PASS", good_body, legacy_comments, "audit-current", [], True)

    # M-P04 body <=80KiB -> PASS
    check("M-P04 body <=80KiB -> PASS", good_body, five_primary_comments(), "audit-current", [], True)

    # M-P05 body >96KiB + semantic compaction -> PASS
    large_body = "x" * (98 * 1024)
    check("M-P05 >96KiB + semantic_compaction -> PASS", large_body, five_primary_comments(), "prewrite", ["--mutation-kind", "body_update", "--write-class", "semantic_compaction"], True)

    # M-P06 #4 technical appendix containing fixtures/review expectations/PASS words -> PASS (must not be naive keyword fail)
    appendix_with_pass = five_primary_comments()
    # replace #4 body with technical content containing PASS words
    for c in appendix_with_pass:
        if "plan_appendix" in c["body"]:
            c["body"] = c["body"] + "\nFixture 1: review expectations PASS criteria security schema migration RV1\n"
    check("M-P06 #4 with PASS words -> PASS", good_body, appendix_with_pass, "audit-current", [], True)

    # M-P07 material mid-Milestone #3 defect update -> PASS (comment_update)
    check("M-P07 material #3 defect update -> PASS", good_body, five_primary_comments(), "prewrite", ["--mutation-kind", "comment_update", "--target-comment-id", "103", "--target-role", "defect_register"], True)

    # M-P08 material mid-Milestone #2 engineering constraint update -> PASS
    check("M-P08 material #2 constraint update -> PASS", good_body, five_primary_comments(), "prewrite", ["--mutation-kind", "comment_update", "--target-comment-id", "102", "--target-role", "development_notes"], True)

    # M-N01 duplicate primary role -> FAIL
    dup = five_primary_comments() + [managed_comment(110, "milestone_progress_index", None, "duplicate")]
    check("M-N01 duplicate primary -> FAIL", good_body, dup, "audit-current", [], False, "PM001_DUPLICATE_PRIMARY_ROLE")

    # M-N02 continuation missing CONTINUATION_OF -> FAIL (duplicate primary without cont)
    # Actually this is same as duplicate primary; but we can test continuation chain missing cont by creating a second primary same role without CONTINUATION_OF
    # Already N01 covers. For N02 we test that creating continuation without CONTINUATION_OF fails in prewrite
    check("M-N02 continuation missing CONTINUATION_OF -> FAIL", good_body, five_primary_comments(), "prewrite", ["--mutation-kind", "comment_create", "--target-role", "plan_appendix"], False, "PM002_INVALID_CONTINUATION")

    # M-N03 continuation role mismatch -> FAIL
    mismatch_comments = five_primary_comments() + [managed_comment(106, "development_notes", "104", "mismatch parent is appendix")]
    check("M-N03 continuation role mismatch -> FAIL", good_body, mismatch_comments, "audit-current", [], False, "PM003_UNAPPROVED_CONTINUATION")

    # M-N04 unapproved continuation create -> FAIL
    check("M-N04 unapproved continuation create -> FAIL", good_body, five_primary_comments(), "prewrite", ["--mutation-kind", "comment_create", "--target-role", "plan_appendix", "--continuation-of", "104", "--user-approved-overflow", "no"], False, "PM003_UNAPPROVED_CONTINUATION")

    # M-N05 unknown new managed role -> FAIL
    unknown = five_primary_comments() + [managed_comment(106, "new_custom_role", None, "")]
    check("M-N05 unknown new managed role -> FAIL", good_body, unknown, "audit-current", [], False, "PM004_UNKNOWN_MANAGED_ROLE")

    # M-N06 arbitrary routine new GitHub comment -> FAIL (prewrite creates unmarked comment)
    check("M-N06 arbitrary routine new comment -> FAIL", good_body, five_primary_comments(), "prewrite", ["--mutation-kind", "comment_create"], False, "PM005_NORMAL_NEW_COMMENT_FORBIDDEN")

    # M-N07 normal additive body write >96KiB -> FAIL
    large_body2 = "y" * (98 * 1024)
    check("M-N07 normal additive >96KiB -> FAIL", large_body2, five_primary_comments(), "prewrite", ["--mutation-kind", "body_update", "--write-class", "normal_additive"], False, "PM006_BODY_ADDITIVE_HARD_STOP")

    # M-N08 high-confidence body execution-ledger dump -> FAIL
    ledger_body = good_body + "\nREAD_BEFORE_WRITE=PASS\nVERIFY_AFTER_WRITE=PASS\n_CONSTRUCTION_RESULT=PASS\n_REPAIR_RESULT=foo\nRV1 FAIL -> repair -> RV2\n"
    check("M-N08 high-confidence body ledger -> FAIL", ledger_body, five_primary_comments(), "audit-current", [], False, "PM007_BODY_LEDGER_CONTAMINATION")

    # M-N09 high-confidence #1 raw review/test ledger -> FAIL
    comments_with_ledger_1 = five_primary_comments()
    for c in comments_with_ledger_1:
        if "milestone_progress_index" in c["body"]:
            c["body"] = c["body"] + "\nREAD_BEFORE_WRITE=PASS\nVERIFY_AFTER_WRITE=PASS\n_CONSTRUCTION_RESULT=xyz\n" + "A"*6000
    check("M-N09 #1 raw ledger -> FAIL", good_body, comments_with_ledger_1, "audit-current", [], False, "PM007_BODY_LEDGER_CONTAMINATION")

    # M-N10 high-confidence #2 R1/R2 repair chronology dump -> FAIL
    comments_with_ledger_2 = five_primary_comments()
    for c in comments_with_ledger_2:
        if "development_notes" in c["body"]:
            c["body"] = c["body"] + "\nR1 -> repair -> R2 -> fresh review\n_CONSTRUCTION_RESULT=foo\n_SOURCE_REVIEW_RESULT=bar\n" + "B"*6000
    check("M-N10 #2 R1/R2 dump -> FAIL", good_body, comments_with_ledger_2, "audit-current", [], False, "PM007_BODY_LEDGER_CONTAMINATION")

    return results

def test_false_positives():
    results = []
    def check(name, body, comments, mode, extra):
        rc, out = run_lint(body, comments, mode, extra)
        passed = (rc == 0)
        results.append((name, passed, rc, out.strip().splitlines()[:5]))
        if not passed:
            print(f"FAIL false-positive {name} rc={rc}")
            print(out[:1000])
        return passed

    good_body = "# Plan\nObjective: test\nScope: foo\n"
    # Fixture 1 etc single words should not cause hard fail
    body_fp = good_body + " Fixture 1 review expectations PASS criteria security schema migration RV1 "
    comments = five_primary_comments()
    # Put single words in appendix - should pass
    for c in comments:
        if "plan_appendix" in c["body"]:
            c["body"] = c["body"] + "\nThis appendix describes Fixture 1 and review expectations for PASS criteria.\n"
    check("FP single words in legitimate prose -> PASS", body_fp, comments, "audit-current", [])

    # Also test security word alone not fail
    body2 = good_body + " security considerations and schema migration notes "
    comments2 = five_primary_comments()
    check("FP security/schema alone -> PASS", body2, comments2, "audit-current", [])

    # Test that body with one PASS word not flagged
    body3 = good_body + " This plan's PASS criteria is X "
    check("FP single PASS -> PASS", body3, five_primary_comments(), "audit-current", [])

    return results

def main():
    total = 0
    passed = 0
    failed = 0

    print("=== Static guard fixtures ===")
    res = test_static_guard()
    for name, ok, rc, out in res:
        total += 1
        if ok:
            passed += 1
            print(f"PASS static {name}")
        else:
            failed += 1
            print(f"FAIL static {name}")

    print("\n=== Materialization fixtures ===")
    res2 = test_materialization()
    for name, ok, rc, out in res2:
        total += 1
        if ok:
            passed += 1
            print(f"PASS material {name}")
        else:
            failed += 1
            print(f"FAIL material {name}")

    print("\n=== False-positive fixtures ===")
    res3 = test_false_positives()
    for name, ok, rc, out in res3:
        total += 1
        if ok:
            passed += 1
            print(f"PASS false-positive {name}")
        else:
            failed += 1
            print(f"FAIL false-positive {name}")

    print(f"\nFIXTURE_SUITE_RESULT total={total} pass={passed} fail={failed}")
    # Write a small fixture manifest for evidence
    manifest = {
        "positive_static": 4,
        "negative_static": 8,
        "positive_material": 8,
        "negative_material": 10,
        "false_positive": len(res3),
        "total": total,
        "passed": passed,
        "failed": failed,
    }
    # Ensure fixtures directory exists
    fixture_dir = ROOT / "tests" / "fixtures" / "chat_governance"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "fixture_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if failed == 0:
        print("CHAT_GOVERNANCE_FIXTURE_SUITE_PASS")
        return 0
    else:
        print("CHAT_GOVERNANCE_FIXTURE_SUITE_FAIL")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
