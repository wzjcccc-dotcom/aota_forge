"""Deterministic guard for the bounded M4-0 architecture design candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = "573be8c2d01eedaf98edf589ab11c8a3bebcf55b"
ORIGINAL_CANDIDATE = "7a3b9aeb797004ded7a714cbe9f607001ac89921"
REL_AUDIT = Path("deploy/evidence/issues/9/m4-0/m4-0-handoff-capability-audit.json")
REL_FREEZE = Path("deploy/evidence/issues/9/m4-0/m4-0-architecture-freeze.json")
REL_REGRESSION = Path("deploy/evidence/issues/9/m4-0/m4-0-regression-ownership.json")
REL_MATRIX = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

failures: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        failures.append(name)


def load(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"json:{label}:{type(exc).__name__}")
        return {}
    check(f"object:{label}", isinstance(value, dict))
    return value if isinstance(value, dict) else {}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def path_from_status(line: str) -> str:
    value = line[3:] if line.startswith("?? ") else line[2:].lstrip() if len(line) >= 2 else line
    if " -> " in value:
        value = value.split(" -> ", 1)[1]
    return value.strip().strip('"')


def check_audit(audit: dict) -> None:
    required_capabilities = {
        "trusted principal/context", "OperationContractDescriptor", "HandlerRegistry", "Unified Ingress",
        "OperationContext / ContextResolver", "Durable Subject Graph", "ObjectRef / internal identity",
        "Subject binding", "Authority Engine", "Capability Lease", "Subject revision/CAS",
        "transaction mechanics", "idempotency mechanics", "canonical lifecycle transitions",
        "projection reconstruction", "binding/recovery", "cutover state machine",
        "postcommit verification/recovery semantics", "production M3/B14 evidence",
        "Portable Plan normalization", "Portable Plan read model", "PlanAuthorityReadAdapter",
        "CLI adapter", "Hermes schema/adapter boundary", "successor regression corpus",
    }
    capabilities = {item.get("capability") for item in audit.get("capabilities", []) if isinstance(item, dict)}
    required_fields = {"capability", "source_locations", "inherited_invariant", "source_primitive", "isolated_behavioral_proof", "production_runtime_proof", "m4_production_integration_required", "evidence", "limitations", "m4_owner_or_disposition"}
    check("audit categories", bool(audit.get("capabilities")) and all(required_fields <= set(item) for item in audit.get("capabilities", []) if isinstance(item, dict)))
    check("mandatory audited capabilities", required_capabilities <= capabilities)
    check("audit primitive/service distinction", all(item.get("reusable_production_service") in {"yes", "no"} and (item.get("source_primitive") != "yes" or item.get("reusable_production_service") == "no") for item in audit.get("capabilities", []) if isinstance(item, dict)))
    check("production proof does not imply service", all(item.get("production_runtime_proof") != "yes" or item.get("reusable_production_service") == "no" for item in audit.get("capabilities", []) if isinstance(item, dict)))


def check_d3(d3: dict) -> None:
    required_states = {"PREPARED", "APPLYING", "OUTCOME_UNKNOWN", "RECONCILING", "VERIFIED", "VERIFIED_RECOVERED", "FAILED_NO_EFFECT", "RETRYABLE_NO_EFFECT", "CONFLICT"}
    check("D3 states", required_states <= set(d3.get("states", [])))
    check("D3 write order", d3.get("required_write_order") == ["validate existing semantic authorization", "validate Subject precondition", "read external authority state", "validate raw external precondition", "construct exact candidate", "compute candidate_raw_digest", "persist PREPARED journal record durably", "persist APPLYING attempt-start state durably", "perform at most one external mutation attempt", "read authority store after mutation where result permits", "classify exact observed state", "persist terminal or reconciliation state"])
    check("D3 write-before-journal bans", d3.get("EXTERNAL_WRITE_BEFORE_PREPARED_DURABLE") == "no" and d3.get("EXTERNAL_WRITE_BEFORE_APPLYING_DURABLE") == "no" and d3.get("NO_EXTERNAL_WRITE_ALLOWED_IF_MANDATORY_JOURNAL_PERSISTENCE_FAILS") == "yes")
    cases = d3.get("crash_windows", [])
    check("D3 J1-J10 exactly once", len(cases) == 10 and {item.get("CASE_ID") for item in cases} == {f"J{i}" for i in range(1, 11)})
    required_case_fields = {"CASE_ID", "PRECONDITION", "FAILURE_WINDOW", "RECOVERY_ENTRY_STATE", "REQUIRED_AUTHORITY_READ", "CLASSIFICATION_RULE", "RESULT_STATE", "RETRY_ALLOWED", "SECOND_EXTERNAL_WRITE_ALLOWED", "SEMANTIC_CHOICE_REQUIRED", "NO_EFFECT_PROOF_RULE"}
    check("D3 crash fields", all(required_case_fields <= set(item) for item in cases if isinstance(item, dict)))
    by_id = {item.get("CASE_ID"): item for item in cases}
    check("D3 J3 recovery", "reconcil" in by_id.get("J3", {}).get("CLASSIFICATION_RULE", "").lower() and by_id.get("J3", {}).get("SECOND_EXTERNAL_WRITE_ALLOWED") == "no")
    check("D3 J5 fresh preconditions", all(term in by_id.get("J5", {}).get("CLASSIFICATION_RULE", "").lower() for term in ["authorization", "subject", "raw"]))
    check("D3 J8 raw precedence", "raw" in by_id.get("J8", {}).get("CLASSIFICATION_RULE", "").lower() and by_id.get("J8", {}).get("NO_EFFECT_PROOF_RULE") == "NORMALIZED_EQUALITY_PROVES_EXACT_EXTERNAL_EFFECT=no")
    check("D3 J9 fail closed", "JOURNAL_PERSISTENCE_FAILURE_BEFORE_EXTERNAL_WRITE" in by_id.get("J9", {}).get("CLASSIFICATION_RULE", "") and "forbidden" in by_id.get("J9", {}).get("NO_EFFECT_PROOF_RULE", ""))
    check("D3 J10 recovery", by_id.get("J10", {}).get("RESULT_STATE") == "VERIFIED_RECOVERED" and by_id.get("J10", {}).get("SECOND_EXTERNAL_WRITE_ALLOWED") == "no")
    check("D3 stranded APPLYING", d3.get("stranded_applying_after_process_restart") == "outcome_uncertain_until_reconciled" and d3.get("STRANDED_APPLYING_BLIND_RETRY_ALLOWED") == "no")
    check("D3 comparison precedence", d3.get("reconciliation_comparison_precedence") == ["exact candidate raw identity", "exact original raw identity", "third raw state"] and d3.get("NORMALIZED_EQUALITY_PROVES_EXACT_EXTERNAL_EFFECT") == "no")
    check("D3 unknown outcome controls", d3.get("OUTCOME_UNKNOWN_IS_FAILURE") == "no" and d3.get("OUTCOME_UNKNOWN_BLIND_RETRY_ALLOWED") == "no" and d3.get("SILENT_REBASE_ALLOWED") == "no" and d3.get("SILENT_ROLLBACK_ALLOWED") == "no")


def check_d7(d7: dict) -> None:
    rows = d7.get("transition_table", [])
    names = {row.get("transition") for row in rows}
    check("D7 transitions", {"PLAN_INIT", "PLAN_INIT pre-binding re-entry", "PLAN_INIT post-binding re-entry"} <= names)
    check("D7 PLAN_INIT re-entry must remain denied", d7.get("reentry_denied") == "yes")
    owner_fields = {"semantic_decision_owner", "project_truth_reconciliation_owner", "mechanical_transition_owner", "authority_store_mutation_owner"}
    contract_fields = {"TRANSITION", "PREDECESSOR_STATE", "SUCCESSOR_STATE"}
    required = owner_fields | {"semantic_authorization_required", "project_binding_requirement", "subject_expected_revision", "raw_authority_precondition", "idempotency_identity", "idempotency_replay_result", "idempotency_conflict_result", "stale_subject_result", "stale_authority_result", "invalid_predecessor_result", "no_effect_on_failure", "no_effect_on_semantic_choice", "no_effect_on_stale_precondition"}
    check("D7 ownership fields", all(owner_fields <= set(row) for row in rows) and all(row.get("semantic_decision_owner") != row.get("mechanical_transition_owner") for row in rows))
    check("D7 executable transition contract", all(required <= set(row) for row in rows) and all({"transition", "predecessor_state", "successor_state"} <= set(row) for row in rows))
    check("D7 no-effect paths", all(row.get(field) == "yes" for row in rows for field in ["no_effect_on_failure", "no_effect_on_semantic_choice", "no_effect_on_stale_precondition"]))
    check("D7 B011/B014", isinstance(d7.get("B011"), dict) and isinstance(d7.get("B014"), dict) and d7["B011"].get("invalid_reentry_result") != d7["B014"].get("invalid_reentry_result"))
    check("D7 boundary", d7.get("CORE_SELECTS_PROJECT_UNDER_AMBIGUITY") == "no" and d7.get("PROJECT_STEWARD_SELECTS_SEMANTIC_PROJECT") == "no")


def check_d8(d8: dict) -> None:
    stages = {item.get("name") for item in d8.get("stages", [])}
    kinds = {item.get("retirement_kind") for item in d8.get("transition_table", [])}
    check("D8 two stages", {"retirement candidate snapshot", "semantic selection + exact retirement mutation"} <= stages)
    check("D8 snapshot fields", {"snapshot_identity", "snapshot_source_revision_or_digest", "candidate_reference", "choice_reference", "candidate_plan_identity", "candidate_plan_state", "active_current_protection_state", "running_task_protection_state", "eligible_retirement_kinds", "successor_eligibility"} <= set(d8.get("snapshot_required_fields", [])))
    check("D8 zero/one/many", all(key in d8.get("selection_rules", {}) for key in ["zero", "one", "many"]) and "no mutation" in d8["selection_rules"]["zero"] and "NEEDS_SEMANTIC_CHOICE" in d8["selection_rules"]["many"])
    check("D8 transitions", kinds == {"abandoned", "superseded"} and d8.get("transition_table", [])[0].get("resulting_plan_state") == "cancelled" and d8.get("transition_table", [])[1].get("resulting_plan_state") == "superseded")
    protections = d8.get("protection_and_staleness", {})
    check("D8 protections", protections.get("ACTIVE_CURRENT_PLAN_RETIREMENT_PROTECTED") == "yes" and protections.get("RUNNING_TASK_PLAN_RETIREMENT_PROTECTED") == "yes" and protections.get("STALE_CANDIDATE_SNAPSHOT_FAILS_CLOSED") == "yes")
    check("D8 exact successor", d8.get("superseded_contract", {}).get("successor_ref_required") == "yes" and d8.get("superseded_contract", {}).get("heuristic_successor_selection") == "denied")
    check("D8 no resurrection", d8.get("RETIRED_PLAN_IMPLICIT_RESURRECTION_ALLOWED") == "no" and d8.get("B013_future_executable_contract", {}).get("then"))


def check_d9(d9: dict) -> None:
    required = set(d9.get("required_roles", []))
    resolution = d9.get("role_resolution", {}).get("required", {})
    check("D9 required roles", required == {"milestone_progress_index", "development_notes", "defect_register"} and all(key in resolution for key in ["zero", "one", "many"]))
    check("D9 duplicate fail closed", "fail closed" in resolution.get("many", "") and "no mutation" in resolution.get("many", ""))
    check("D9 no arbitrary selection", {"first", "newest", "latest"} <= set(d9.get("role_resolution", {}).get("selection_forbidden", [])))
    check("D9 no auto-create", d9.get("GENERIC_CONTROL_COMMENT_UPDATE_CREATES_MISSING_ROLE") == "no" and d9.get("DUPLICATE_CONTROL_ROLE_CREATION_ALLOWED") == "no")
    check("D9 authority", d9.get("ISSUE_BODY_REMAINS_PLAN_AUTHORITY") == "yes" and d9.get("CONTROL_COMMENTS_PLAN_AUTHORITY") == "no" and d9.get("EVENT_LOG_PLAN_AUTHORITY") == "no")


def check_d11(d11: dict) -> None:
    no_fields = ["CORE_AUTO_ACCEPTS_MILESTONE", "PROJECT_STEWARD_AUTO_ACCEPTS_MILESTONE", "CORE_AUTO_CLOSES_PLAN", "PROJECT_STEWARD_AUTO_CLOSES_PLAN", "CORE_INVENTS_PROJECT_BINDING", "PROJECT_STEWARD_INVENTS_PROJECT_BINDING", "RELATIONSHIP_RESOLVE_RESULT_IS_PROJECT_BINDING", "RELATIONSHIP_RESOLVE_RESULT_IS_SEMANTIC_PROJECT_DECISION", "CONTEXT_PREPARE_MAY_INVENT_PROJECT_BINDING"]
    check("D11 semantic prohibitions", all(d11.get(field) == "no" for field in no_fields))
    check("D11 binding basis", d11.get("PROJECT_BINDING_REQUIRES_REAL_CANONICAL_VALIDATED_PROJECT") == "yes" and d11.get("PROJECT_BINDING_REQUIRES_TRUSTED_SEMANTIC_DECISION_BASIS") == "yes")
    check("D11 sequence", len(d11.get("plan_init_sequence", [])) == 3 and "NEEDS_SEMANTIC_CHOICE" in d11.get("ambiguous_project_result", ""))


def check_regression(regression: dict, matrix: dict) -> None:
    entries = {item.get("FAILURE_CLASS"): item for item in regression.get("failure_classes", []) if isinstance(item, dict)}
    matrix_entries = {item.get("failure_class"): item for item in matrix.get("failure_classes", []) if isinstance(item, dict)}
    check("regression all frozen classes", set(entries) == set(matrix_entries) and len(entries) == len(matrix_entries))
    check("regression frozen owners", all(entries[name].get("FROZEN_SUCCESSOR_OWNER_MILESTONE") == matrix_entries[name].get("successor_owner_milestone") for name in matrix_entries if name in entries))
    check("regression acceptance not claimed", all(item.get("M4_0_BEHAVIORAL_ACCEPTANCE_CLAIMED") == "no" for item in entries.values()))
    check("regression false claims", regression.get("M4_0_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT") == 0 and regression.get("M4_0_REGRESSION_BEHAVIORAL_CLOSURE_CLAIMED") == "no")


def check_git_scope() -> None:
    allowed = {"deploy/evidence/issues/9/m4-0/m4-0-handoff-capability-audit.json", "deploy/evidence/issues/9/m4-0/m4-0-architecture-freeze.json", "deploy/evidence/issues/9/m4-0/m4-0-regression-ownership.json", "scripts/m4_0_architecture_freeze_guard.py"}
    try:
        changed = set(filter(None, git("diff", "--name-only", BASE).splitlines()))
        status = {path_from_status(line) for line in git("status", "--short").splitlines() if line}
        check("allowed path guard", (changed | status) <= allowed)
        check("functional Core unchanged", not any(path == "aota_forge" or path.startswith("aota_forge/") for path in changed | status))
        original_audit_changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", ORIGINAL_CANDIDATE, "--", REL_AUDIT.as_posix()]).returncode != 0
        check("capability audit unchanged", not original_audit_changed)
        check("M2 matrix unchanged", REL_MATRIX.as_posix() not in changed)
        check("diff check", subprocess.run(["git", "-C", str(ROOT), "diff", "--check", BASE], capture_output=True, text=True).returncode == 0)
        check("base is ancestor", subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE, "HEAD"]).returncode == 0)
    except (subprocess.CalledProcessError, OSError):
        failures.append("git:scope-check")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=ROOT, help="temporary artifact root for offline validation")
    args = parser.parse_args()
    artifact_root = args.artifact_root.resolve()
    audit = load(artifact_root / REL_AUDIT, REL_AUDIT.as_posix())
    freeze = load(artifact_root / REL_FREEZE, REL_FREEZE.as_posix())
    regression = load(artifact_root / REL_REGRESSION, REL_REGRESSION.as_posix())
    matrix = load(artifact_root / REL_MATRIX, REL_MATRIX.as_posix())

    for doc in (audit, freeze, regression):
        check("project/repository/issue identity", doc.get("project_id") == "aota_forge" and doc.get("repository") == "wzjcccc-dotcom/aota_forge" and doc.get("governing_plan_issue") == "wzjcccc-dotcom/aota-hermes-tools#9")
        check("exact expected base", doc.get("expected_base_sha", doc.get("frozen_matrix_sha")) == BASE)
    check("matrix path exists", (artifact_root / REL_MATRIX).is_file())
    check_audit(audit)
    decisions = {item.get("id"): item.get("freeze", {}) for item in freeze.get("architecture_decisions", []) if isinstance(item, dict)}
    check("D1-D12 present", set(decisions) == {f"D{i}" for i in range(1, 13)})
    check("no cross-authority transaction", decisions.get("D2", {}).get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE") == "no")
    check_d3(decisions.get("D3", {}))
    check("D4 candidate raw field", "candidate_raw_digest" in decisions.get("D4", {}).get("required_fields", []))
    check("D5 lease boundary", decisions.get("D5", {}).get("durable_semantic_authorization_differs_from_capability_lease") == "yes" and decisions.get("D5", {}).get("LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER") == "no" and decisions.get("D5", {}).get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE") == "no")
    check("D5 exact authorization binding", "exact mutation/candidate fingerprint" in decisions.get("D5", {}).get("exact_authorization_binding", []))
    check("D6 digest separation", decisions.get("D6", {}).get("CANDIDATE_RAW_DIGEST_BINDS_EXACT_INTENT") == "yes" and decisions.get("D6", {}).get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN") == "no")
    check_d7(decisions.get("D7", {}))
    check_d8(decisions.get("D8", {}))
    d9 = decisions.get("D9", {})
    check("D9 authority classes", d9.get("authority_classes", {}).get("Issue body") == "Portable Plan semantic authority" and d9.get("authority_classes", {}).get("control comments") == "mutable operational projections" and d9.get("authority_classes", {}).get("Event Log comments") == "append-only historical evidence")
    check_d9(d9)
    check("M4/M5 boundary", decisions.get("D10", {}).get("M4_HERMES_RUNTIME_MUTATION_ACTIVATION_REQUIRED") == "no" and decisions.get("D10", {}).get("M4_HERMES_EXECUTOR_E2E_REQUIRED") == "no")
    d11 = decisions.get("D11", {})
    check_d11(d11)
    check("D11 Git scope", d11.get("GENERIC_GIT_WRITE_API_ALLOWED") == "no" and d11.get("GENERIC_TERMINAL_EXPANSION_ALLOWED") == "no" and d11.get("PROJECT_SELECTION_UNDER_AMBIGUITY_CORE_ALLOWED") == "no")
    check("safety boundary", decisions.get("D12", {}).get("GITHUB_IS_FORGE_CORE_ONTOLOGY") == "no" and decisions.get("D12", {}).get("RAW_GH_OPERATION_IN_CORE") == "no" and decisions.get("D12", {}).get("MODEL_FACING_MEGA_TOOL") == "no")

    dag = freeze.get("dependency_dag", {})
    check("M4 DAG and authorization", dag.get("acyclic") == "yes" and dag.get("M4_6_M4_7_PARALLEL_SAFETY") == "pending_source_ownership_reconnaissance" and freeze.get("authorization_boundary", {}).get("M4_1_EXECUTION_AUTHORIZED") == "no" and freeze.get("authorization_boundary", {}).get("NEXT_READY_SET") == "independent_review_of_M4_0")
    check_regression(regression, matrix)
    check_git_scope()

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS M4-0 architecture freeze guard")
    print(f"CAPABILITY_AUDIT_ENTRY_COUNT={len(audit.get('capabilities', []))}")
    print(f"ARCHITECTURE_DECISION_COUNT={len(decisions)}")
    print(f"CRASH_WINDOW_CASE_COUNT={len(decisions.get('D3', {}).get('crash_windows', []))}")
    print("CRASH_WINDOW_CASES_DESIGN_CLASSIFIED=10")
    print(f"FROZEN_REGRESSION_CLASS_COUNT={len(matrix.get('failure_classes', []))}")
    print(f"REGRESSION_CLASS_TRACE_COUNT={len(regression.get('failure_classes', []))}")
    print("M4_0_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
