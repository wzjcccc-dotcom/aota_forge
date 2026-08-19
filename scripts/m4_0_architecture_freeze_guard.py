"""Deterministic guard for the M4-0 architecture design candidate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = "573be8c2d01eedaf98edf589ab11c8a3bebcf55b"
AUDIT_PATH = ROOT / "deploy/evidence/issues/9/m4-0/m4-0-handoff-capability-audit.json"
FREEZE_PATH = ROOT / "deploy/evidence/issues/9/m4-0/m4-0-architecture-freeze.json"
REGRESSION_PATH = ROOT / "deploy/evidence/issues/9/m4-0/m4-0-regression-ownership.json"
MATRIX_PATH = ROOT / "deploy/evidence/issues/9/m2-successor-regression-matrix.json"

failures: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        failures.append(name)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"json:{path.relative_to(ROOT)}:{type(exc).__name__}")
        return {}
    check(f"object:{path.relative_to(ROOT)}", isinstance(value, dict))
    return value if isinstance(value, dict) else {}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def path_from_status(line: str) -> str:
    value = line[3:] if len(line) >= 3 else line
    if " -> " in value:
        value = value.split(" -> ", 1)[1]
    return value.strip().strip('"')


def main() -> int:
    audit = load(AUDIT_PATH)
    freeze = load(FREEZE_PATH)
    regression = load(REGRESSION_PATH)

    for doc in (audit, freeze, regression):
        check("project/repository/issue identity", doc.get("project_id") == "aota_forge" and doc.get("repository") == "wzjcccc-dotcom/aota_forge" and doc.get("governing_plan_issue") == "wzjcccc-dotcom/aota-hermes-tools#9")
        check("exact expected base", doc.get("expected_base_sha", doc.get("frozen_matrix_sha")) == BASE)
    check("matrix path exists", MATRIX_PATH.is_file())
    try:
        parent = git("rev-list", "--parents", "-n", "1", "HEAD").split()
        check("exact base parent", git("rev-parse", BASE) == BASE and (git("rev-parse", "HEAD") == BASE or (len(parent) > 1 and parent[1] == BASE)))
    except (subprocess.CalledProcessError, OSError):
        failures.append("git:base-resolution")

    required_capabilities = {
        "trusted principal/context", "OperationContractDescriptor", "HandlerRegistry", "Unified Ingress", "OperationContext / ContextResolver", "Durable Subject Graph", "ObjectRef / internal identity", "Subject binding", "Authority Engine", "Capability Lease", "Subject revision/CAS", "transaction mechanics", "idempotency mechanics", "canonical lifecycle transitions", "projection reconstruction", "binding/recovery", "cutover state machine", "postcommit verification/recovery semantics", "production M3/B14 evidence", "Portable Plan normalization", "Portable Plan read model", "PlanAuthorityReadAdapter", "CLI adapter", "Hermes schema/adapter boundary", "successor regression corpus"
    }
    capabilities = {item.get("capability") for item in audit.get("capabilities", []) if isinstance(item, dict)}
    check("audit categories", bool(audit.get("capabilities")) and all(set(item) >= {"capability", "source_locations", "inherited_invariant", "source_primitive", "isolated_behavioral_proof", "production_runtime_proof", "m4_production_integration_required", "evidence", "limitations", "m4_owner_or_disposition"} for item in audit.get("capabilities", []) if isinstance(item, dict)))
    check("mandatory audited capabilities", required_capabilities <= capabilities)
    check("audit primitive/service distinction", all(item.get("reusable_production_service") in {"yes", "no"} and item.get("source_primitive") != "yes" or item.get("reusable_production_service") == "no" for item in audit.get("capabilities", []) if isinstance(item, dict)))
    check("production proof does not imply service", all(item.get("production_runtime_proof") != "yes" or item.get("reusable_production_service") == "no" for item in audit.get("capabilities", []) if isinstance(item, dict)))

    decisions = {item.get("id"): item.get("freeze", {}) for item in freeze.get("architecture_decisions", []) if isinstance(item, dict)}
    check("D1-D12 present", set(decisions) == {f"D{i}" for i in range(1, 13)})
    d1 = decisions.get("D1", {})
    check("M3 invariant/service distinction", d1.get("M3_ACCEPTED_INVARIANTS_REUSED") == "yes" and d1.get("M3_PROVEN_PRIMITIVES_REIMPLEMENTED_BY_DEFAULT") == "no" and d1.get("M3_SOURCE_PRIMITIVE_EQUALS_PRODUCTION_SERVICE") == "no")
    d2 = decisions.get("D2", {})
    check("no cross-authority transaction", d2.get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE") == "no")
    d3 = decisions.get("D3", {})
    states = set(d3.get("states", []))
    check("journal states", {"PREPARED", "APPLYING", "OUTCOME_UNKNOWN", "RECONCILING"} <= states and {"VERIFIED", "VERIFIED_RECOVERED", "FAILED_NO_EFFECT", "RETRYABLE_NO_EFFECT", "CONFLICT"} <= states)
    transitions = {(item.get("from"), item.get("to")) for item in d3.get("transitions", [])}
    check("journal transitions", {("PREPARED", "APPLYING"), ("APPLYING", "VERIFIED"), ("APPLYING", "FAILED_NO_EFFECT"), ("APPLYING", "OUTCOME_UNKNOWN"), ("OUTCOME_UNKNOWN", "RECONCILING"), ("RECONCILING", "VERIFIED_RECOVERED"), ("RECONCILING", "RETRYABLE_NO_EFFECT"), ("RECONCILING", "CONFLICT")} <= transitions)
    check("unknown outcome controls", d3.get("OUTCOME_UNKNOWN_BLIND_RETRY_ALLOWED") == "no" and d3.get("SILENT_REBASE_ALLOWED") == "no" and d3.get("SILENT_ROLLBACK_ALLOWED") == "no" and d3.get("RECOVERY_DIFFERS_FROM_ROLLBACK") == "yes")
    d4 = decisions.get("D4", {})
    check("journal record fields", {"operation", "principal", "typed_target", "correlation_id", "contract_hash", "idempotency_key", "intent_fingerprint", "subject_expected_revision", "raw_source_revision", "observed_raw_digest", "candidate_raw_digest", "normalized_plan_digest", "authorization_basis", "lease_or_attempt_reference", "journal_state"} <= set(d4.get("required_fields", [])))
    d5 = decisions.get("D5", {})
    check("authorization lease distinction", d5.get("durable_semantic_authorization_differs_from_capability_lease") == "yes" and d5.get("LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER") == "no" and d5.get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE") == "no")
    check("exact candidate binding", "exact mutation/candidate fingerprint" in d5.get("exact_authorization_binding", []))
    d6 = decisions.get("D6", {})
    domain_names = {item.get("name") for item in d6.get("domains", [])}
    check("revision digest domains", {"subject lifecycle concurrency", "external authority-store concurrency", "Portable Plan semantic identity"} <= domain_names and d6.get("candidate_raw_digest") and d6.get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN") == "no")
    d7 = decisions.get("D7", {})
    transition_names = {item.get("transition") for item in d7.get("transition_table", [])}
    check("PLAN_INIT lifecycle", {"PLAN_INIT", "PLAN_INIT pre-binding re-entry", "PLAN_INIT post-binding re-entry"} <= transition_names and d7.get("reentry_denied") == "yes" and d7.get("pre_binding_cycle_denied") == "yes" and d7.get("post_binding_cycle_denied") == "yes")
    d8 = decisions.get("D8", {})
    retirement_kinds = {item.get("retirement_kind") for item in d8.get("transition_table", [])}
    check("retirement lifecycle", {"abandoned", "superseded"} <= retirement_kinds and d8.get("SUCCESSOR_EQUALS_TARGET") == "denied" and d8.get("RETIRED_PLAN_IMPLICIT_RESURRECTION_ALLOWED") == "no" and d8.get("STALE_CANDIDATE_SNAPSHOT_FAILS_CLOSED") == "yes")
    d9 = decisions.get("D9", {})
    check("comment authority classes", d9.get("authority_classes", {}).get("Issue body") == "Portable Plan semantic authority" and d9.get("authority_classes", {}).get("control comments") == "mutable operational projections" and d9.get("authority_classes", {}).get("Event Log comments") == "append-only historical evidence")
    check("comment roles", set(d9.get("required_roles", [])) == {"milestone_progress_index", "development_notes", "defect_register"} and d9.get("DUPLICATE_CONTROL_ROLE") == "fail closed" and d9.get("CONTROL_COMMENT_DRIFT_CAN_REWRITE_ISSUE_BODY") == "no")
    d10 = decisions.get("D10", {})
    check("M4/M5 boundary", d10.get("M4_HERMES_RUNTIME_MUTATION_ACTIVATION_REQUIRED") == "no" and d10.get("M4_HERMES_EXECUTOR_E2E_REQUIRED") == "no")
    d11 = decisions.get("D11", {})
    classifications = {item.get("helper"): item.get("classification") for item in d11.get("helper_scope", [])}
    check("Project/Git scope", d11.get("PROJECT_SELECTION_UNDER_AMBIGUITY_CORE_ALLOWED") == "no" and d11.get("GENERIC_GIT_WRITE_API_ALLOWED") == "no" and d11.get("GENERIC_TERMINAL_EXPANSION_ALLOWED") == "no" and classifications.get("bounded Project Steward mechanical invocation") == "REQUIRED_FOR_M4")
    d12 = decisions.get("D12", {})
    check("safety boundary", d12.get("GITHUB_IS_FORGE_CORE_ONTOLOGY") == "no" and d12.get("RAW_GH_OPERATION_IN_CORE") == "no" and d12.get("MODEL_FACING_MEGA_TOOL") == "no" and d12.get("MODEL_FACING_ARBITRARY_FILESYSTEM_PATH") == "denied" and d12.get("RECOVERY_SEMANTIC_CHOICE_OWNER") == "external_LLM_or_user")

    dag = freeze.get("dependency_dag", {})
    edges = [tuple(edge) for edge in dag.get("edges", []) if isinstance(edge, list) and len(edge) == 2]
    nodes = set(dag.get("nodes", []))
    indegree = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for source, target in edges:
        check("DAG edge nodes", source in nodes and target in nodes)
        if source in nodes and target in nodes:
            indegree[target] += 1
            outgoing[source].append(target)
    queue = [node for node, count in indegree.items() if count == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for target in outgoing[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    check("M4 DAG acyclic", dag.get("acyclic") == "yes" and visited == len(nodes))
    check("M4-1 not automatically authorized", freeze.get("authorization_boundary", {}).get("M4_1_EXECUTION_AUTHORIZED") == "no")
    check("next ready set", freeze.get("authorization_boundary", {}).get("NEXT_READY_SET") == "independent_review_of_M4_0")
    check("parallel safety pending", dag.get("M4_6_M4_7_PARALLEL_SAFETY") == "pending_source_ownership_reconnaissance")

    required_regressions = {"B011", "B013", "B014", "B014-F", "RC2-1", "RECOVERY-1", "WCTX-1", "BIND-1", "DRIFT-1", "ACTIVATE-R-current-binding"}
    regression_entries = {item.get("FAILURE_CLASS"): item for item in regression.get("failure_classes", []) if isinstance(item, dict)}
    check("M4 regression ownership classes", required_regressions <= set(regression_entries))
    check("M4-0 no behavioral closure", regression.get("M4_0_REGRESSION_BEHAVIORAL_CLOSURE_CLAIMED") == "no")
    check("regression entries do not claim PASS", all(item.get("TARGET_DISPOSITION") != "PASS" for item in regression_entries.values()))

    allowed = {"deploy/evidence/issues/9/m4-0/m4-0-handoff-capability-audit.json", "deploy/evidence/issues/9/m4-0/m4-0-architecture-freeze.json", "deploy/evidence/issues/9/m4-0/m4-0-regression-ownership.json", "scripts/m4_0_architecture_freeze_guard.py"}
    try:
        changed = set(filter(None, git("diff", "--name-only", BASE).splitlines()))
        status = {path_from_status(line) for line in git("status", "--short").splitlines() if line}
        untracked = set(filter(None, git("ls-files", "--others", "--exclude-standard").splitlines()))
        status = {
            path
            for path in status
            if path in untracked
            or not any(candidate.startswith(path.rstrip("/") + "/") for candidate in untracked)
        } | untracked
        check("allowed path guard", (changed | status) <= allowed)
        check("functional Core unchanged", not any(path == "aota_forge" or path.startswith("aota_forge/") for path in changed | status))
        check("M3 accepted evidence unchanged", not any(path.startswith("deploy/evidence/issues/9/m3-a/") or path.startswith("deploy/evidence/issues/9/m3-b/") for path in changed | status))
        check("M2 matrix unchanged", "deploy/evidence/issues/9/m2-successor-regression-matrix.json" not in changed | status)
        check("diff check", subprocess.run(["git", "-C", str(ROOT), "diff", "--check", BASE], capture_output=True, text=True).returncode == 0)
    except (subprocess.CalledProcessError, OSError):
        failures.append("git:allowed-path-check")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS M4-0 architecture freeze guard")
    print(f"CAPABILITY_AUDIT_ENTRY_COUNT={len(audit.get('capabilities', []))}")
    print(f"ARCHITECTURE_DECISION_COUNT={len(decisions)}")
    print(f"REGRESSION_OWNERSHIP_ENTRY_COUNT={len(regression_entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
