"""Validate completeness and scope of independent M4-0 review evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "deploy/evidence/issues/9/m4-0-review/m4-0-independent-review.json"
BASE = "573be8c2d01eedaf98edf589ab11c8a3bebcf55b"
TARGET = "7a3b9aeb797004ded7a714cbe9f607001ac89921"
failures: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        failures.append(name)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def main() -> int:
    try:
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"FAIL review-json:{type(exc).__name__}")
        return 1

    required_sections = {
        "scope_integrity",
        "capability_audit_review",
        "cross_authority_review",
        "journal_recovery_review",
        "lease_authorization_review",
        "revision_digest_review",
        "plan_init_review",
        "retirement_review",
        "control_comment_review",
        "m4_m5_boundary_review",
        "project_steward_git_review",
        "regression_review",
        "dag_review",
        "negative_guard_review",
        "blocking_findings",
        "non_blocking_findings",
        "verdict",
    }
    check("review object", isinstance(review, dict))
    check("identity", review.get("review_target_sha") == TARGET and review.get("review_base_sha") == BASE and review.get("governing_issue") == "wzjcccc-dotcom/aota-hermes-tools#9")
    check("required sections", required_sections <= set(review))
    check("scope target", review.get("scope_integrity", {}).get("target_commit_exists") is True)
    check("scope candidate integrity", review.get("scope_integrity", {}).get("candidate_artifacts_mutated_during_review") is False)
    check("negative count", review.get("negative_guard_review", {}).get("case_count") == 12)
    check("negative accounting", review.get("negative_guard_review", {}).get("reject_count") + review.get("negative_guard_review", {}).get("unexpected_accept_count") == 12)
    check("capability count", review.get("capability_audit_review", {}).get("entry_count") == 25)
    check("m4 closure false", review.get("regression_review", {}).get("M4_0_REGRESSION_BEHAVIORAL_CLOSURE_CLAIMED") == "no")
    check("unknown outcome retry", review.get("journal_recovery_review", {}).get("OUTCOME_UNKNOWN_BLIND_RETRY_ALLOWED") == "no")
    check("review verdict", review.get("verdict") == "FAIL")
    check("blocking findings", isinstance(review.get("blocking_findings"), list) and len(review["blocking_findings"]) > 0)

    allowed_review = {
        "deploy/evidence/issues/9/m4-0-review/m4-0-independent-review.json",
        "scripts/m4_0_independent_review_guard.py",
    }
    try:
        status_lines = git("status", "--short", "--untracked-files=all").splitlines()
        status_paths = {line[3:].strip().strip('"') for line in status_lines if len(line) >= 4}
        check("review-only worktree", status_paths <= allowed_review)
        check("no candidate mutation", not any(path.startswith("deploy/evidence/issues/9/m4-0/") for path in status_paths))
        check("no core mutation", not any(path == "aota_forge" or path.startswith("aota_forge/") for path in status_paths))
        check("diff check", subprocess.run(["git", "-C", str(ROOT), "diff", "--check", BASE], capture_output=True).returncode == 0)
    except (subprocess.CalledProcessError, OSError):
        failures.append("git scope validation")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS M4-0 independent review evidence guard")
    print(f"BLOCKING_FINDING_COUNT={len(review['blocking_findings'])}")
    print(f"NEGATIVE_CASE_COUNT={review['negative_guard_review']['case_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
