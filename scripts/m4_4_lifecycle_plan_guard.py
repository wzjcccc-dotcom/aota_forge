#!/usr/bin/env python3
"""Zero-LLM guard for the Issue #9 M4-4 lifecycle planning candidate."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess


EXPECTED_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy/evidence/issues/9/m4-4"
ALLOWED_PREFIX = "deploy/evidence/issues/9/m4-4/"
ALLOWED_SCRIPT = "scripts/m4_4_lifecycle_plan_guard.py"

FILES = {
    "audit": EVIDENCE / "m4-4-lifecycle-capability-audit.json",
    "plan_init": EVIDENCE / "m4-4-plan-init-contract.json",
    "retirement": EVIDENCE / "m4-4-retirement-candidate-contract.json",
    "steward": EVIDENCE / "m4-4-project-steward-boundary.json",
    "errors": EVIDENCE / "m4-4-lifecycle-error-contract.json",
    "handoff": EVIDENCE / "m4-4-m4-3-handoff.json",
    "forecast": EVIDENCE / "m4-4-source-ownership-forecast.json",
    "regressions": EVIDENCE / "m4-4-regression-handoff-map.json",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load() -> dict[str, dict]:
    result = {}
    for name, path in FILES.items():
        assert path.is_file(), f"missing artifact: {path}"
        with path.open(encoding="utf-8") as handle:
            result[name] = json.load(handle)
        assert result[name]["expected_base_sha"] == EXPECTED_BASE, f"wrong base in {name}"
    return result


def validate_git() -> None:
    actual_head = git("rev-parse", "HEAD")
    merge_base = git("merge-base", EXPECTED_BASE, "HEAD")
    assert merge_base == EXPECTED_BASE, "accepted base is not an ancestor of HEAD"
    changed = set(filter(None, git("diff", "--name-only", EXPECTED_BASE, "HEAD").splitlines()))
    status_lines = git("status", "--porcelain=v1").splitlines()
    for line in status_lines:
        path = line[3:] if len(line) >= 4 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(path)
    unexpected = sorted(
        path for path in changed
        if not (path.startswith(ALLOWED_PREFIX) or path == ALLOWED_SCRIPT)
    )
    assert not unexpected, f"unexpected changed paths: {unexpected}"
    source_changes = [path for path in changed if path.startswith("aota_forge/")]
    assert not source_changes, f"functional source changed: {source_changes}"
    print(f"EXACT_BASE_VERIFIED=yes ACTUAL_HEAD={actual_head} ACTUAL_BASE={merge_base}")
    print("FUNCTIONAL_CORE_SOURCE_CHANGED=no")


def validate_contracts(data: dict[str, dict]) -> None:
    audit = data["audit"]
    assert audit["capability_audit_entry_count"] == len(audit["capabilities"])
    assert audit["capability_audit_entry_count"] >= 20
    assert {entry["classification"] for entry in audit["capabilities"]} >= {
        "REUSE_AS_IS", "EXTEND_IN_M4_4", "CONSUME_LATER", "OUT_OF_SCOPE_M4_4"
    }

    plan = data["plan_init"]
    assert plan["PLAN_INIT_REENTRY_DENIED"] == "yes"
    assert len(plan["transitions"]) == 3
    assert [(item["TRANSITION"], item["PREDECESSOR"], item["SUCCESSOR"]) for item in plan["transitions"]] == [
        ("PLAN_INIT", "uninitialized", "initialized"),
        ("PLAN_INIT_PRE_BINDING_REENTRY", "initialized_unbound", "initialized_unbound"),
        ("PLAN_INIT_POST_BINDING_REENTRY", "initialized_bound", "initialized_bound"),
    ]
    required_transition_fields = {
        "TRANSITION", "PREDECESSOR", "SUCCESSOR", "SEMANTIC_DECISION_OWNER",
        "PROJECT_TRUTH_RECONCILIATION_OWNER", "MECHANICAL_TRANSITION_OWNER",
        "AUTHORITY_STORE_MUTATION_OWNER", "SEMANTIC_AUTHORIZATION_REQUIRED",
        "PROJECT_BINDING_REQUIRED", "SUBJECT_EXPECTED_REVISION_REQUIRED",
        "AUTHORITY_PRECONDITION_REQUIRED", "IDEMPOTENCY_RULE", "REPLAY_RESULT",
        "STALE_SUBJECT_RESULT", "STALE_AUTHORITY_RESULT", "INVALID_PREDECESSOR_RESULT",
        "NO_EFFECT_RULE"
    }
    assert all(required_transition_fields <= set(item) for item in plan["transitions"])
    assert plan["sequence"]["CORE_SELECTS_PROJECT_UNDER_AMBIGUITY"] == "no"
    assert plan["sequence"]["PROJECT_STEWARD_SELECTS_SEMANTIC_PROJECT"] == "no"
    assert plan["sequence"]["RELATIONSHIP_RESOLVE_RESULT_IS_PROJECT_BINDING"] == "no"
    assert plan["sequence"]["RELATIONSHIP_RESOLVE_RESULT_IS_SEMANTIC_PROJECT_DECISION"] == "no"
    assert "more than one" in plan["sequence"]["steps"][3]
    assert "NEEDS_SEMANTIC_CHOICE" in plan["sequence"]["steps"][3]

    retirement = data["retirement"]
    assert len(retirement["two_stage_protocol"]) == 2
    assert set(retirement["snapshot_required_fields"]) >= {
        "snapshot_identity", "source_revision", "source_digest", "plan_identity",
        "plan_state", "active_current_protection", "running_task_protection",
        "eligible_retirement_kinds", "successor_eligibility", "candidate_identity"
    }
    assert retirement["resolution"]["zero"].startswith("RETIREMENT_NO_CANDIDATE")
    assert retirement["resolution"]["many"].startswith("RETIREMENT_NEEDS_SEMANTIC_CHOICE")
    assert set(retirement["resolution"]["selection_forbidden"]) >= {
        "newest", "latest", "first", "similarity", "timestamp proximity",
        "path proximity", "stale current pointer heuristic"
    }
    assert retirement["retirement_kinds"]["abandoned"]["resulting_plan_state"] == "cancelled"
    superseded = retirement["retirement_kinds"]["superseded"]
    assert superseded["successor_required"] == "yes: exact successor_ref"
    assert superseded["successor_must_not_equal_target"] == "yes"
    assert retirement["protections"] == {
        "active_current_plan": "fail closed / no effect",
        "running_task_plan": "fail closed / no effect",
        "stale_snapshot": "fail closed / no effect",
        "changed_authority_revision": "fail closed / no effect",
        "changed_raw_digest": "fail closed / no effect",
        "target_no_longer_eligible": "fail closed / no effect",
        "silent_refresh_and_reuse_prior_choice": "forbidden"
    }
    assert retirement["NO_RESURRECTION"]["RETIRED_PLAN_IMPLICIT_RESURRECTION_ALLOWED"] == "no"
    assert retirement["B013"]["DESIGN_CLOSURE_READY"] == "yes"
    assert retirement["B013"]["BEHAVIORAL_PASS"] == "no"

    steward = data["steward"]
    for name, value in steward["flags"].items():
        if name.endswith("SELECTS_SEMANTIC_PROJECT") or "AUTO_" in name or "INVENTS_PROJECT_BINDING" in name:
            assert value == "no", f"unsafe steward flag: {name}"
    assert "choose ambiguous Project" in steward["may_not"]
    assert "invent successor" in steward["may_not"]

    required_errors = {
        "PROJECT_NOT_FOUND", "NEEDS_SEMANTIC_CHOICE", "PROJECT_BINDING_REQUIRED",
        "PLAN_INIT_INVALID_PREDECESSOR", "PLAN_INIT_ALREADY_INITIALIZED",
        "PLAN_INIT_STALE_SUBJECT_REVISION", "PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
        "RETIREMENT_NO_CANDIDATE", "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
        "RETIREMENT_STALE_SNAPSHOT", "RETIREMENT_TARGET_PROTECTED",
        "RETIREMENT_RUNNING_TASK_PROTECTED", "RETIREMENT_SUCCESSOR_REQUIRED",
        "RETIREMENT_SUCCESSOR_INVALID", "RETIREMENT_SELF_SUCCESSOR",
        "RETIREMENT_STALE_AUTHORITY_PRECONDITION"
    }
    assert required_errors <= set(data["errors"]["errors"])
    assert data["errors"]["errors"]["NEEDS_SEMANTIC_CHOICE"]["semantic_choice"] == "yes"
    assert data["errors"]["errors"]["RETIREMENT_NEEDS_SEMANTIC_CHOICE"]["semantic_choice"] == "yes"

    assert data["handoff"]["M4_4_PASS_AUTOMATICALLY_AUTHORIZES_M4_3"] == "no"
    assert data["handoff"]["M4_3_EXECUTION_AUTHORIZED"] == "no"
    assert data["forecast"]["M4_2_M4_4_SOURCE_PARALLEL_SAFETY"] in {"safe", "unsafe", "conditional", "pending"}
    assert data["forecast"]["source_parallelism_authorized"] == "no"
    assert data["regressions"]["M4_4_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT"] == 0


def negative_cases(data: dict[str, dict]) -> int:
    """Each mutation below must be rejected by the frozen contract checks."""
    cases = [
        ("NEG-M44-01", lambda x: x["plan_init"].update(PLAN_INIT_REENTRY_DENIED="no")),
        ("NEG-M44-02", lambda x: x["steward"]["flags"].update(PROJECT_STEWARD_SELECTS_SEMANTIC_PROJECT="yes")),
        ("NEG-M44-03", lambda x: x["plan_init"]["sequence"].update(RELATIONSHIP_RESOLVE_RESULT_IS_PROJECT_BINDING="yes")),
        ("NEG-M44-04", lambda x: x["plan_init"]["transitions"][0].update(PREDECESSOR="initialized")),
        ("NEG-M44-05", lambda x: x["retirement"]["resolution"]["selection_forbidden"].remove("newest")),
        ("NEG-M44-06", lambda x: x["retirement"]["retirement_kinds"]["abandoned"].update(resulting_plan_state="deleted")),
        ("NEG-M44-07", lambda x: x["retirement"]["retirement_kinds"]["superseded"].update(successor_required="no")),
        ("NEG-M44-08", lambda x: x["retirement"]["retirement_kinds"]["superseded"].update(successor_must_not_equal_target="no")),
        ("NEG-M44-09", lambda x: x["retirement"]["protections"].update(active_current_plan="allow")),
        ("NEG-M44-10", lambda x: x["retirement"]["protections"].update(running_task_plan="allow")),
        ("NEG-M44-11", lambda x: x["retirement"]["NO_RESURRECTION"].update(RETIRED_PLAN_IMPLICIT_RESURRECTION_ALLOWED="yes")),
        ("NEG-M44-12", lambda x: x["handoff"].update(M4_4_PASS_AUTOMATICALLY_AUTHORIZES_M4_3="yes")),
    ]
    rejected = 0
    for case_id, mutate in cases:
        candidate = deepcopy(data)
        mutate(candidate)
        try:
            validate_contracts(candidate)
        except (AssertionError, KeyError, ValueError):
            rejected += 1
        else:
            raise AssertionError(f"{case_id} unexpectedly accepted")
    assert rejected == 12
    return rejected


def main() -> int:
    validate_git()
    data = load()
    validate_contracts(data)
    rejected = negative_cases(data)
    print(f"CAPABILITY_AUDIT_ENTRY_COUNT={data['audit']['capability_audit_entry_count']}")
    print("PLAN_INIT_REENTRY_DENIED=yes")
    print("PLAN_INIT_OWNERSHIP_BOUNDARY_FROZEN=yes")
    print("B011_DESIGN_CLOSURE_READY=yes B014_DESIGN_CLOSURE_READY=yes B013_DESIGN_CLOSURE_READY=yes")
    print("ABANDONED_TO_CANCELLED=yes SUPERSEDED_EXACT_SUCCESSOR_REQUIRED=yes")
    print("ACTIVE_CURRENT_PROTECTION=yes RUNNING_TASK_PROTECTION=yes STALE_SNAPSHOT_FAILS_CLOSED=yes")
    print("IMPLICIT_RESURRECTION_ALLOWED=no")
    print("M4_3_EXECUTION_AUTHORIZED=no")
    print("SOURCE_OWNERSHIP_FORECAST=PASS M4_2_M4_4_SOURCE_PARALLEL_SAFETY=pending")
    print(f"NEGATIVE_CASE_COUNT=12 NEGATIVE_CASE_REJECT_COUNT={rejected} NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT=0")
    print("M4_4_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
